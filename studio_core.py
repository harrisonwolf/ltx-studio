#!/usr/bin/env python
"""Job-manager backend for the LTX studio dashboard.
Persistent runs (runs/<id>.json + .log), a queue, a single-GPU runner thread,
live progress parsing, and pause(SIGSTOP)/resume(SIGCONT)/cancel. The TUI polls this.
"""
import os, json, time, signal, subprocess, threading, re, glob, itertools, shutil

REPO = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(REPO, "runs")
os.makedirs(RUNS_DIR, exist_ok=True)

_PROG = re.compile(r"\[\[(SEG|STEP)\s+(\d+)\s+(\d+)\]\]")
# PLAN/DIRECT text may END in "]" ("... [left]]]"): the lazy body must stop at the LAST "]]" of the run,
# i.e. a "]]" not followed by another "]". director.py's _ascii1 strips every "]]" from the text itself.
_DIRX = re.compile(r"\[\[DIRECT\s+(.*?)\]\](?!\])")
_PHASE = re.compile(r"\[\[PHASE\s+(\w+)\]\]")
_LOAD = re.compile(r"\[\[LOAD\s+(\d+)\s+(\d+)\s+(.*?)\]\]")
_CKPT = re.compile(r"\[\[CKPT\s+(\d+)\s+(\d+)\]\]")
_SUSP = re.compile(r"\[\[SUSPENDED\s+(.*?)\]\]")
_PLAN = re.compile(r"\[\[PLAN\s+(\d+)\s+(.*?)\]\](?!\])")
_DMS = re.compile(r"\[\[DIRECT_MS\s+(\d+)\s+(\d+)\s+(\d+)\]\]")   # space-form: seg load_ms infer_ms
_VRAM = re.compile(r"\[\[VRAM\s+(\d+)\]\]")                       # per-shot peak CUDA MB (experiment_log DV)
_SEAMMSE = re.compile(r"\[\[SEAMMSE\s+(\d+)\s+(-?\d+)\]\]")       # seam continuity: [seg, mse*100] (Q3)
_DRIFT = re.compile(r"\[\[DRIFT\s+(\d+)\s+(-?\d+)\s+(-?\d+)\]\]") # drift vs anchor: [seg, pre*100, post*100] (Q3)
_TOKENS = re.compile(r"\[\[TOKENS\s+(\d+)\s+(\d+)\]\]")           # prompt token count: [seg, n] (Q3)
_DCFG = re.compile(r"\[\[DCFG\s+(.*?)\]\]")                       # director as-RUN config "k=v k=v" (audit #8)
_FIELDS = ["id", "title", "kind", "cmd", "params", "status", "seg", "nseg", "step", "nstep",
           "out", "error", "director", "created", "started", "finished",
           "phase", "load_step", "load_total", "load_msg", "saw_step", "first_step_ts", "first_step_seg",
           "ckpt_dir", "resumes", "last_ckpt_seg", "preview", "plans", "dir_ms",
           "phase_started", "phase_secs", "seg_started", "seg_secs", "peak_vram",
           "seam_mse", "drift", "tok_counts", "dcfg", "qorder", "prior_secs", "pre_resume", "phase_ckpt"]
# Blind A/B pair state (pair_id, pair_variant, pair_blind, pair_varied_dial, pair_revealed) lives INSIDE
# each job's `params` dict, which is itself in _FIELDS above and round-trips through save()/load() -- so
# pair_revealed already survives an app restart with no extra top-level field needed.
ACTIVE = ("running", "paused")
ARCHIVED = ("done", "failed", "cancelled", "interrupted")
_counter = itertools.count(1)


class Job:
    def __init__(self, jid, title, kind, cmd, params):
        self.id, self.title, self.kind, self.cmd, self.params = jid, title, kind, cmd, params
        self.status = "queued"
        self.seg, self.nseg = 0, int(params.get("nseg", 1))
        self.step, self.nstep = 0, int(params.get("steps", 0) or 0)
        self.out = params.get("out")
        self.error = ""
        self.director = ""          # latest director prompt (director mode)
        self.created = time.time()
        self.qorder = self.created   # queue position (PROMOTE lowers it); `created` stays the true creation time
        self.started = self.finished = None
        self.prior_secs = 0         # wall seconds of EARLIER legs of a suspended+resumed run (run_secs())
        self.pre_resume = None      # [started, finished, prior_secs] before RESUME, so REMOVE can undo it
        self._tail_snap = ([], 0)   # (last log lines, lines ever appended) -- ONE tuple, see `tail` below
        # ---- load/phase tracking + checkpoint/suspend (per integration contract) ----
        self.phase = ""
        self.load_step, self.load_total, self.load_msg = 0, 0, ""
        self.saw_step = False
        self.first_step_ts = None
        self.first_step_seg = 1
        self.ckpt_dir = None
        self.resumes = 0
        self.last_ckpt_seg = 0
        self.preview = os.path.join(RUNS_DIR, f"{self.id}_preview.png")  # live frame preview
        self.plans = []             # [[seg, plan, prompt], ...] director reasoning history
        self.dir_ms = {}            # {seg:int -> [load_ms, infer_ms]} per-seam director cost
        self.dcfg = {}              # director as-RUN config from [[DCFG]] (steadiness may be downgraded)
        # ---- telemetry: phase + per-shot timing ----
        self.phase_started = None   # wall-clock the current phase began
        self.phase_secs = {}        # {phase: cumulative seconds}
        self.seg_started = None     # wall-clock the current shot began
        self.seg_secs = []          # [seconds per completed shot]
        self.phase_ckpt = {}        # {str(seg): phase_secs as of that [[CKPT]]} -> resume drops the lost shot
        self.peak_vram = None       # max [[VRAM mb]] seen (experiment_log measured DV)
        # ---- Q3: measurement floor (seam/drift/token telemetry) ----
        self.seam_mse = []          # [[seg, mse*100], ...] seam continuity per continuation
        self.drift = []             # [[seg, pre*100, post*100], ...] drift vs the shot-1 anchor
        self.tok_counts = []        # [[seg, n_tokens], ...] prompt length per shot

    # ---- LIVE log ring. The runner thread appends while the UI thread reads. The (lines, count) pair is
    # published as ONE immutable tuple (a new list per line, never mutated in place): a reader that needs
    # both must take them together via tail_snapshot() -- two separate attribute reads can straddle an
    # append, and no ordering of two stores fixes that.
    @property
    def tail(self):
        return self._tail_snap[0]

    @tail.setter
    def tail(self, lines):
        self._tail_snap = (list(lines), self._tail_snap[1])

    @property
    def tail_count(self):
        """Total lines ever appended (monotonic, not persisted). Use tail_snapshot() to pair it with tail."""
        return self._tail_snap[1]

    @tail_count.setter
    def tail_count(self, n):
        self._tail_snap = (self._tail_snap[0], int(n))

    def tail_snapshot(self):
        """(lines, tail_count) as one consistent pair."""
        return self._tail_snap

    def _tail_push(self, line):
        lines, n = self._tail_snap
        self._tail_snap = (lines[-299:] + [line], n + 1)

    def jpath(self):
        return os.path.join(RUNS_DIR, f"{self.id}.json")

    def logpath(self):
        return os.path.join(RUNS_DIR, f"{self.id}.log")

    _SAVE_LOCK = threading.Lock()

    def save(self):
        """Atomic + serialized. The runner thread and the UI thread (promote/rename/resume) both call
        this; a torn or interleaved write leaves invalid JSON and Job.load silently DROPS the run at
        the next launch. tmp + os.replace makes every write all-or-nothing."""
        with Job._SAVE_LOCK:
            tmp = self.jpath() + ".tmp"
            with open(tmp, "w") as f:
                json.dump({k: getattr(self, k) for k in _FIELDS}, f)
            os.replace(tmp, self.jpath())

    @classmethod
    def load(cls, path):
        d = json.load(open(path))
        j = cls(d["id"], d["title"], d.get("kind", "single"), d["cmd"], d["params"])
        for k in _FIELDS:
            if k in d:
                setattr(j, k, d[k])
        if "qorder" not in d:        # pre-qorder JSON: queue position = creation time (the old sort key)
            j.qorder = j.created
        j.dir_ms = {int(k): v for k, v in (getattr(j, "dir_ms", {}) or {}).items()}   # JSON str keys -> int
        if j.status in ACTIVE:       # was running when app died
            j.status = "interrupted"
            # freeze elapsed() at last log activity (or start) instead of "now"
            j.finished = j.finished or (
                os.path.getmtime(j.logpath()) if os.path.exists(j.logpath()) else j.started)
        # 'suspended' SURVIVES app restart -- never auto-flip it to 'interrupted'.
        # If a valid checkpoint exists, PREFER 'suspended'. A job killed mid-suspend
        # (transient 'suspending') recovers to 'suspended' when its checkpoint is valid,
        # else demotes to 'interrupted' so it can never become an unrecoverable zombie.
        ckpt = os.path.join(RUNS_DIR, f"{j.id}_ckpt")
        if j.status in ("interrupted", "suspended", "suspending") and j._ckpt_valid(ckpt):
            if j.status == "suspending":     # the app died before the leg ended: freeze it at the
                j.finished = j.finished or (  # last log activity so RESUME folds it into prior_secs
                    os.path.getmtime(j.logpath()) if os.path.exists(j.logpath()) else j.started)
            j.status = "suspended"
            j.ckpt_dir = ckpt
        elif j.status == "suspending":
            j.status = "interrupted"
            j.finished = j.finished or (
                os.path.getmtime(j.logpath()) if os.path.exists(j.logpath()) else j.started)
        tail = []
        if os.path.exists(j.logpath()):
            try:
                tail = open(j.logpath()).read().splitlines()[-300:]
            except Exception:
                pass
        j._tail_snap = (tail, len(tail))
        return j

    def elapsed(self):
        """Wall seconds of the CURRENT (or last) leg."""
        a = self.started or self.created
        b = self.finished or time.time()
        return int(b - a)

    def run_secs(self):
        """Wall seconds of the whole run: every earlier leg of a suspended+resumed run + this leg."""
        a = self.started or self.created
        b = self.finished or time.time()
        return int((self.prior_secs or 0) + (b - a))

    def pct(self):
        if self.nstep:
            segs = max(self.nseg, self.seg)   # self-heal if actual segments exceed est
            done = (self.seg - 1) * self.nstep + self.step if segs > 1 else self.step
            tot = segs * self.nstep
            return max(0, min(100, int(100 * done / max(1, tot))))
        return 0

    def is_loading(self):
        return self.phase in ("importing", "loading", "offload", "loading_vlm", "warmup")

    def load_pct(self):
        return int(100 * self.load_step / max(1, self.load_total))

    @staticmethod
    def _ckpt_valid(ckpt):
        """A checkpoint is valid IFF state.json exists AND frames/ holds at least n_frames PNGs. Extra
        *.png (an interrupted crash-safe write) are fine -- the loader reads only the first n_frames;
        pending *.png.new / *.tmp files never match the *.png glob."""
        try:
            sp = os.path.join(ckpt, "state.json")
            if not os.path.exists(sp):
                return False
            with open(sp) as f:
                n = int(json.load(f).get("n_frames", -1))
            # mirror director.load_checkpoint exactly: it reads frames/0000.png .. frames/{n-1}.png
            return n >= 1 and all(os.path.exists(os.path.join(ckpt, "frames", f"{i:04d}.png")) for i in range(n))
        except Exception:
            return False


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.proc = None
        self.current = None
        self.paused = False
        self._stop = False
        self._suspend_req = False
        self._interrupt_req = None    # job id; stall-sentry hard-kill -> land 'interrupted' (ckpt kept), not 'cancelled'
        self._cancel_req = None       # job id, set ONLY by an explicit user cancel() of THAT job
        # Guards every status transition shared by the UI thread (pause/resume/suspend/remove) and the
        # runner's start/final-status change, so a UI action can never overwrite a finished job's status.
        self._lock = threading.RLock()
        self._usr1_ready = False      # worker printed its first [[marker]] -> its SIGUSR1 handler is installed
        self._suspend_pending = None  # job id whose suspend() arrived before that; sent once it's ready
        self._wake_proc = None       # Windows-side Modern-Standby wake lock (held while rendering)
        self.vram_reserve_gb = 1.0   # T14: GB of the 8GB card to leave for the desktop; studio.py loads/persists this
        for p in glob.glob(os.path.join(RUNS_DIR, "*.json")):
            try:
                j = Job.load(p); self.jobs[j.id] = j
            except Exception:
                pass
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- queries (TUI polls these) ----
    def _sorted(self):
        return sorted(self.jobs.values(), key=lambda j: j.created)

    def _by_qorder(self):
        """Queue order (PROMOTE-aware). Archive order stays by true creation time (_sorted)."""
        return sorted(self.jobs.values(), key=lambda j: (getattr(j, "qorder", j.created), j.created))

    def queued(self):
        return [j for j in self._by_qorder() if j.status == "queued"]

    def archived(self):
        return list(reversed([j for j in self._sorted() if j.status in ARCHIVED]))

    def active(self):
        return self.jobs.get(self.current)

    def counts(self):
        q = sum(1 for j in self.jobs.values() if j.status == "queued")
        a = 1 if self.current else 0
        d = sum(1 for j in self.jobs.values() if j.status == "done")
        s = sum(1 for j in self.jobs.values() if j.status == "suspended")
        return q, a, d, s

    def suspended(self):
        return [j for j in self._by_qorder() if j.status == "suspended"]

    # ---- mutations ----
    def enqueue(self, title, kind, cmd, params):
        jid = time.strftime("%y%m%d-%H%M%S") + f"-{next(_counter):02d}"
        j = Job(jid, title, kind, cmd, params)
        self.jobs[jid] = j
        if kind in ("director", "chained") and int(params.get("nseg", 1)) > 1:
            ckpt = os.path.join("runs", f"{jid}_ckpt")
            j.cmd = list(j.cmd) + ["--ckpt_dir", ckpt]
            j.ckpt_dir = os.path.join(RUNS_DIR, f"{jid}_ckpt")
        if kind != "enhance":           # live frame preview for every generation run
            j.cmd = list(j.cmd) + ["--preview", j.preview]
        j.save()
        return j

    def remove(self, jid):
        """Take a queued run off the queue. A RESUMED run (queued with --resume on a valid checkpoint)
        goes back to 'suspended' -- REMOVE undoes the RESUME instead of silently destroying a
        resumable render (suspended runs aren't removable either). Returns "removed"/"suspended"/None."""
        with self._lock:
            j = self.jobs.get(jid)
            if not (j and j.status == "queued"):
                return None
            ck = os.path.join(RUNS_DIR, f"{j.id}_ckpt")
            if "--resume" in j.cmd and j._ckpt_valid(ck):
                j.status, j.ckpt_dir = "suspended", ck
                j.resumes = max(0, int(j.resumes or 0) - 1)
                if j.pre_resume:                           # restore the leg timing RESUME cleared/folded
                    j.started, j.finished, j.prior_secs = j.pre_resume
                    j.pre_resume = None
                j.finished = j.finished or time.time()     # (pre-fix JSON: no stash) freeze elapsed()
                j.save()
                return "suspended"
            self.jobs.pop(jid, None)
        paths = [j.jpath()]
        if "--resume" in j.cmd:          # a resumed run whose checkpoint is gone: don't orphan its leftovers
            shutil.rmtree(ck, ignore_errors=True)
            paths += [j.logpath(), getattr(j, "preview", None)]
        for p in paths:
            try:
                if p:
                    os.remove(p)
            except OSError:
                pass
        return "removed"

    def deletable(self, jid):
        """Only finished runs shown in ARCHIVE (never the active job) can be hard-deleted."""
        j = self.jobs.get(jid)
        return bool(j) and jid != self.current and j.status in ARCHIVED

    def enhance_children(self, jid):
        """Enhance jobs made FROM this run (kept, not cascaded, when the source is deleted)."""
        return [j for j in self.jobs.values()
                if j.kind == "enhance" and j.params.get("source_id") == jid]

    def delete(self, jid):
        """Hard-delete a finished run + all its artifacts (mp4, frames, ckpt, preview, log, json).
        Refuses the active/queued/suspended job. Does NOT cascade to enhance children."""
        if not self.deletable(jid):
            return False
        j = self.jobs.get(jid)

        def _rm(p):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

        out_abs = (j.out if os.path.isabs(j.out) else os.path.join(REPO, j.out)) if j.out else None
        _rm(out_abs)
        fd = j.params.get("frames_dir")
        if fd:
            shutil.rmtree(fd if os.path.isabs(fd) else os.path.join(REPO, fd), ignore_errors=True)
        shutil.rmtree(os.path.join(RUNS_DIR, f"{j.id}_ckpt"), ignore_errors=True)
        if getattr(j, "ckpt_dir", None):
            shutil.rmtree(j.ckpt_dir if os.path.isabs(j.ckpt_dir) else os.path.join(REPO, j.ckpt_dir),
                          ignore_errors=True)
        _rm(getattr(j, "preview", None))
        _rm(j.logpath())
        _rm(j.jpath())
        self.jobs.pop(jid, None)
        return True

    def rename(self, jid, new_name):
        """Rename a finished run's name AND its on-disk mp4 + frames dir, collision-safe.
        Refuses running/queued/resumable jobs. Returns (ok, message)."""
        j = self.jobs.get(jid)
        if not j:
            return False, "run not found"
        if jid == self.current or j.status in ("queued", "suspended", "interrupted"):
            return False, "can't rename a running, queued or resumable run"
        from studio import slugify   # lazy: avoid the studio <-> studio_core import cycle
        slug = slugify(new_name)
        if not slug:
            return False, "name is empty after slugifying"
        cur = os.path.splitext(os.path.basename(j.out or ""))[0]
        if slug == cur:
            return False, "name unchanged"
        base, n, cand = slug, 2, slug
        while (os.path.exists(os.path.join(REPO, f"outputs/{cand}.mp4"))
               or os.path.exists(os.path.join(REPO, f"outputs/{cand}_frames"))):
            cand = f"{base}-{n}"; n += 1
        slug = cand
        new_out, new_fdir = f"outputs/{slug}.mp4", f"outputs/{slug}_frames"
        old_out_abs = (j.out if os.path.isabs(j.out) else os.path.join(REPO, j.out)) if j.out else None
        if old_out_abs and os.path.exists(old_out_abs):
            try:
                os.rename(old_out_abs, os.path.join(REPO, new_out))
            except OSError as ex:
                return False, f"mp4 rename failed: {ex}"
        j.out = new_out
        fd = j.params.get("frames_dir")
        if fd:
            fd_abs = fd if os.path.isabs(fd) else os.path.join(REPO, fd)
            if os.path.exists(fd_abs):
                try:
                    os.rename(fd_abs, os.path.join(REPO, new_fdir))
                except OSError:
                    pass
            j.params["frames_dir"] = new_fdir
        j.params["name"] = slug
        j.title = new_name.strip() or slug
        j.save()
        return True, slug

    @staticmethod
    def _signal_group(proc, sig):
        """Signal the worker's whole process GROUP (start_new_session=True makes it the leader), so
        director.py's VLM sidecar / enhance children stop and continue WITH it; fall back to the leader.
        Never after the worker has been reaped (returncode set): its pid/pgid may already be reused."""
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except Exception:
            proc.send_signal(sig)

    def pause(self):
        with self._lock:
            proc, cur = self.proc, self.current
            job = self.jobs.get(cur) if cur else None
            if proc and job and not self.paused and job.status in ("running", "suspending"):
                try:
                    self._signal_group(proc, signal.SIGSTOP)
                    self.paused = True
                    job.status = "paused"; job.save()
                except Exception:
                    pass

    def resume(self):
        with self._lock:
            proc, cur = self.proc, self.current
            job = self.jobs.get(cur) if cur else None
            if proc and job and self.paused and job.status == "paused":
                try:
                    self._signal_group(proc, signal.SIGCONT)
                    self.paused = False
                    job.status = "running"; job.save()
                except Exception:
                    pass

    def cancel(self, interrupt=False):
        # Capture proc/current ONCE: re-reading self.proc after the check could kill the NEXT job if
        # this one finished in between. The cancel request names THIS job, so it can't leak onto the next.
        with self._lock:
            proc, cur = self.proc, self.current
            if not cur:
                return
            if interrupt:
                self._interrupt_req = cur   # stall-sentry kill -> 'interrupted' (ckpt kept)
            else:
                self._cancel_req = cur      # explicit user cancel -> 'cancelled' (vs kernel OOM-kill -> 'interrupted')
            if proc is None:                # claimed but not spawned yet: _run kills it right after Popen
                return
            paused = self.paused
        try:
            if paused:
                self._signal_group(proc, signal.SIGCONT)
            if proc.returncode is None:
                try:
                    # kill the whole process GROUP: director.py's VLM sidecar/daemon children must die
                    # too, or an orphan keeps holding GPU/RAM and OOMs the next queued run.
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
        except Exception:
            pass

    def shutdown(self):
        """Clean app-exit (T9): stop the runner + kill the running subprocess so it can't orphan onto
        the GPU into the next session. Queued jobs persist on disk and resume next launch; a killed
        multi-segment run recovers to 'suspended' from its last checkpoint (only the in-flight shot lost)."""
        self._stop = True
        self.cancel()
        self._release_wakelock()

    def hard_interrupt(self):
        """Stall-sentry escalation: kill the active run but land it 'interrupted' (checkpoint KEPT)
        instead of 'cancelled' (checkpoint deleted). The exit path then promotes it straight to
        'suspended' when a valid checkpoint exists, so it is resumable without an app restart.
        The runner loop moves on to the next queued job either way."""
        self.cancel(interrupt=True)

    def suspend(self):
        """Clean checkpointed suspend (multi-segment jobs only) via SIGUSR1. Works even if paused.
        Returns (ok, message) for the UI. A worker that hasn't printed its first real marker yet may
        not have installed its SIGUSR1 handler (the default action KILLS it), so the signal is held
        and sent the moment it is listening -- the engine only acts on it at a shot boundary anyway."""
        with self._lock:
            proc, cur = self.proc, self.current
            job = self.jobs.get(cur) if cur else None
            if not job:
                return False, "no active run to suspend"
            if int(job.nseg or 1) <= 1:
                return False, "single-shot run: nothing to checkpoint (use CANCEL)"
            if job.status not in ("running", "paused", "suspending"):
                return False, f"run is already {job.status}"
            try:
                self._suspend_req = True
                if self.paused:                  # a SIGSTOP'd process must be continued to run its handler
                    self._signal_group(proc, signal.SIGCONT)
                    self.paused = False
                if self._usr1_ready and proc is not None:
                    proc.send_signal(signal.SIGUSR1)   # leader ONLY: children keep SIGUSR1's default (die)
                    msg = "suspending: checkpoints at the next shot boundary"
                else:
                    self._suspend_pending = cur
                    msg = "suspend queued: sent as soon as the engine finishes starting up"
                job.status = "suspending"; job.save()
                return True, msg
            except Exception as e:
                return False, f"suspend failed: {e}"

    def resume_suspended(self, jid):
        with self._lock:                  # the runner claims queued jobs under this lock: finish every
            job = self.jobs.get(jid)      # field before the job becomes 'queued' (claimable)
            if job and job.status == "suspended":
                if "--resume" not in job.cmd:
                    job.cmd = list(job.cmd) + ["--resume", job.ckpt_dir]
                job.resumes += 1
                # fold the leg that just ended into prior_secs (run_secs() spans every leg); stash the
                # pre-resume timing so REMOVE can undo this RESUME exactly
                job.pre_resume = [job.started, job.finished, job.prior_secs or 0]
                if job.started and job.finished:
                    job.prior_secs = (job.prior_secs or 0) + max(0.0, job.finished - job.started)
                job.started = job.finished = None
                job.status = "queued"
                job.save()

    def promote(self, jid):
        """Move a queued/suspended run to the FRONT of the queue. Only the persisted queue-order key
        changes -- `created` (experiment ts_created, archive order, queued-wait) stays the truth."""
        job = self.jobs.get(jid)
        if job and job.status in ("queued", "suspended"):
            job.qorder = min(getattr(j, "qorder", j.created) for j in list(self.jobs.values())
                             if j.status in ("queued", "suspended")) - 1
            job.save()

    # ---- runner ----
    # ---- Modern-Standby wake lock (Windows/WSL) -------------------------------------------------
    # 2026-07-06 root cause of the frozen overnight batch: on S0 (Modern Standby) laptops the
    # display going dark IS standby entry — Kernel-Power logged "entering Modern Standby" 3 min
    # after the user pressed screen-off, and Windows froze the whole WSL VM for 8.5 hours. Idle
    # timeouts don't govern this. The documented fix: hold an ES_CONTINUOUS|ES_SYSTEM_REQUIRED
    # execution request while work is running — background compute stays alive, screen stays dark.
    # The request lives in a tiny Windows-side powershell that we kill when the queue drains, so
    # an idle studio never blocks the laptop from sleeping. No-op outside Windows/WSL.
    def _ensure_wakelock(self):
        if self._wake_proc is not None and self._wake_proc.poll() is None:
            return
        exe = shutil.which("powershell.exe")
        if not exe:
            return
        ps = ("Add-Type -Name P -Namespace W -MemberDefinition "
              "'[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f);';"
              "[W.P]::SetThreadExecutionState(2147483649) | Out-Null;"    # 0x80000001 = CONTINUOUS|SYSTEM_REQUIRED
              "while ($true) { Start-Sleep 60 }")
        try:
            self._wake_proc = subprocess.Popen([exe, "-NoProfile", "-Command", ps],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               stdin=subprocess.DEVNULL)
        except Exception:
            self._wake_proc = None

    def _release_wakelock(self):
        p, self._wake_proc = self._wake_proc, None
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _loop(self):
        # The runner THREAD must survive anything — if it dies, the TUI keeps ticking but the
        # queue silently freezes forever (worst possible overnight failure).
        while not self._stop:
            try:
                nxt = next((j for j in self._by_qorder() if j.status == "queued"), None)
            except RuntimeError:      # jobs dict mutated by the UI thread mid-iteration -> just retry
                time.sleep(0.05); continue
            if nxt is None:
                self._release_wakelock()      # idle queue must never block the laptop from sleeping
                time.sleep(0.4); continue
            self._ensure_wakelock()
            try:
                self._run(nxt)
            except Exception as e:    # ENOSPC / FS hiccup in the pre-Popen setup etc: fail THIS job, move on
                try:
                    nxt.status, nxt.error, nxt.finished = "failed", str(e)[:200], time.time()
                    nxt.save()
                except Exception:
                    pass
                self.proc, self.current, self.paused = None, None, False
                time.sleep(1.0)

    @staticmethod
    def _resume_seg(job):
        """Last shot held by the checkpoint a --resume run starts from (its state.json seg_idx);
        falls back to the last [[CKPT]] seen, 0 if neither is known."""
        try:
            ck = job.cmd[job.cmd.index("--resume") + 1]
            if not os.path.isabs(ck):
                ck = os.path.join(job.params.get("cwd") or REPO, ck)
            with open(os.path.join(ck, "state.json")) as f:
                return int(json.load(f)["seg_idx"])
        except Exception:
            return int(getattr(job, "last_ckpt_seg", 0) or 0)

    def _run(self, job):
        env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
        # T14: VRAM headroom reserved for the Windows desktop, as a fraction of the 8GB card.
        # gpu_budget.cap_vram() reads this env var in the subprocess (STUDIO_VRAM_HEADROOM=0.12 default).
        try:
            env["STUDIO_VRAM_HEADROOM"] = str(max(0.0, float(self.vram_reserve_gb)) / 8.0)
        except Exception:
            pass
        resumed = "--resume" in job.cmd
        with self._lock:
            if job.status != "queued" or self.jobs.get(job.id) is not job:
                return            # REMOVEd (or put back to 'suspended') after _loop picked it
            job.status, job.started, job.seg, job.step = "running", time.time(), 0, 0
            job.pre_resume = None                 # started: REMOVE can no longer undo the RESUME
            self.current, self.paused = job.id, False
            self._usr1_ready, self._suspend_pending = False, None
            self._suspend_req = False
        # reset load/phase tracking on every (re)start
        job.phase, job.load_step, job.load_total, job.load_msg = "", 0, 0, ""
        job.saw_step, job.first_step_ts, job.first_step_seg = False, None, 1
        job.phase_started, job.seg_started = None, None
        if resumed:
            # A resumed leg KEEPS the earlier legs' telemetry (the experiment row must cover the whole
            # run). Drop only what lies past the checkpoint we resume from: a hard-killed leg's
            # in-flight shot is re-rendered and would otherwise be counted twice.
            c = self._resume_seg(job)
            job.phase_secs = dict(job.phase_secs or {})
            job.seg_secs = list(job.seg_secs or [])
            job.dir_ms = dict(job.dir_ms or {})
            job.seam_mse, job.drift, job.tok_counts = (list(x or []) for x in (job.seam_mse, job.drift, job.tok_counts))
            job.phase_ckpt = {k: v for k, v in (job.phase_ckpt or {}).items() if c > 0 and int(k) <= c}
            if str(c) in job.phase_ckpt:      # the killed leg's in-flight shot is re-rendered: drop its phases
                job.phase_secs = dict(job.phase_ckpt[str(c)])
            if c > 0:
                job.seg_secs = job.seg_secs[:c]
                job.dir_ms = {k: v for k, v in job.dir_ms.items() if int(k) <= c}
                job.seam_mse, job.drift, job.tok_counts = (
                    [e for e in x if e and int(e[0]) <= c] for x in (job.seam_mse, job.drift, job.tok_counts))
        else:
            job.phase_secs, job.seg_secs = {}, []
            job.phase_ckpt, job.prior_secs = {}, 0
            job.dir_ms = {}
            job.dcfg = {}
            job.seam_mse, job.drift, job.tok_counts = [], [], []
        job.save()
        suspended_ckpt = None
        seg_closed = False        # the current shot's time is already in seg_secs (closed on saving/suspend)
        seg_stepped = False       # a [[STEP]] ran since the last [[SEG]]: only then is it a real shot (a
                                  # resume from the FINAL checkpoint prints [[SEG n+1]] and renders nothing)
        last_save = time.time()
        cwd = job.params.get("cwd") or REPO

        def _close_shot():
            nonlocal seg_closed
            if job.seg_started is not None and not seg_closed and seg_stepped:
                job.seg_secs.append(int(time.time() - job.seg_started))
                seg_closed = True

        with open(job.logpath(), "a" if resumed else "w") as lf:   # a resumed leg APPENDS to the run's log
            try:
                # errors="replace": ONE non-UTF-8 byte from some library must not abort the read loop
                proc = subprocess.Popen(job.cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
                                        start_new_session=True)   # own process group -> cancel can killpg children
                with self._lock:
                    self.proc = proc
                    # a CANCEL that landed between the claim and here (proc was still None) is only
                    # recorded -> act on it now. A held SUSPEND stays in _suspend_pending (sent below).
                    if job.id in (self._cancel_req, self._interrupt_req):
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except Exception:
                            proc.kill()
                for line in self.proc.stdout:
                    line = line.rstrip()
                    lf.write(line + "\n"); lf.flush()
                    if not self._usr1_ready and line.startswith("[[") and line != "[[PHASE importing]]":
                        # the worker is past its imports -> director.py's SIGUSR1 handler is installed
                        with self._lock:
                            self._usr1_ready = True
                            if self._suspend_pending == job.id:
                                self._suspend_pending = None
                                try:
                                    self.proc.send_signal(signal.SIGUSR1)
                                except Exception:
                                    pass
                    transition = False
                    m = _PROG.search(line)
                    if m:
                        a, b = int(m.group(2)), int(m.group(3))
                        if m.group(1) == "SEG":
                            _now = time.time()
                            if job.seg_started is not None and a > job.seg and not seg_closed and seg_stepped:
                                job.seg_secs.append(int(_now - job.seg_started))
                            seg_closed = seg_stepped = False
                            job.seg, job.nseg, job.step = a, b, 0
                            job.seg_started = _now
                            transition = True
                        else:
                            job.step, job.nstep = a, b
                            seg_stepped = True
                            if not job.saw_step:
                                job.saw_step, job.first_step_ts = True, time.time()
                                job.first_step_seg = job.seg
                    pl = _PLAN.search(line)
                    if pl:
                        job.plans.append([int(pl.group(1)), pl.group(2), ""])
                        transition = True
                    dm = _DMS.search(line)
                    if dm:
                        job.dir_ms[int(dm.group(1))] = [int(dm.group(2)), int(dm.group(3))]
                        transition = True
                    vm = _VRAM.search(line)
                    if vm:
                        job.peak_vram = max(int(vm.group(1)), int(getattr(job, "peak_vram", 0) or 0))
                    sm = _SEAMMSE.search(line)
                    if sm:
                        job.seam_mse.append([int(sm.group(1)), int(sm.group(2))])
                        transition = True
                    dr = _DRIFT.search(line)
                    if dr:
                        job.drift.append([int(dr.group(1)), int(dr.group(2)), int(dr.group(3))])
                        transition = True
                    tk = _TOKENS.search(line)
                    if tk:
                        job.tok_counts.append([int(tk.group(1)), int(tk.group(2))])   # noise -> no transition
                    dc = _DCFG.search(line)
                    if dc:   # director as-RUN provenance: steadiness may have been DOWNGRADED by the engine
                        job.dcfg = dict(kv.split("=", 1) for kv in dc.group(1).split() if "=" in kv)
                        transition = True
                    d = _DIRX.search(line)
                    if d:
                        job.director = d.group(1)
                        if job.plans and not job.plans[-1][2]:   # back-fill prompt onto the open note
                            job.plans[-1][2] = d.group(1)
                        transition = True
                    ph = _PHASE.search(line)
                    if ph:
                        _now = time.time()
                        if ph.group(1) == "saving":
                            _close_shot()       # the LAST shot has no next [[SEG]] to close it
                        if job.phase and job.phase_started is not None:
                            job.phase_secs[job.phase] = job.phase_secs.get(job.phase, 0) + (_now - job.phase_started)
                        job.phase = ph.group(1)
                        job.phase_started = _now
                        if job.phase in ("generating", "decoding", "saving", "redirecting"):
                            job.load_step = job.load_total
                        transition = True
                    ld = _LOAD.search(line)
                    if ld:
                        job.load_step, job.load_total, job.load_msg = \
                            int(ld.group(1)), int(ld.group(2)), ld.group(3)
                    ck = _CKPT.search(line)
                    if ck:
                        job.last_ckpt_seg = int(ck.group(1))
                        snap = dict(job.phase_secs)     # phases as of this commit (open phase up to now)
                        if job.phase and job.phase_started is not None:
                            snap[job.phase] = snap.get(job.phase, 0) + (time.time() - job.phase_started)
                        job.phase_ckpt[ck.group(1)] = snap
                        transition = True
                    su = _SUSP.search(line)
                    if su:
                        suspended_ckpt = su.group(1)
                        _close_shot()           # printed where the next [[SEG]] would be: last shot of this leg
                        transition = True
                    if line and "vision_model" not in line and not line.startswith("[["):
                        job._tail_push(line)    # list + monotonic count published together (LIVE re-anchor)
                    now = time.time()
                    if transition or (now - last_save) >= 3:
                        job.save(); last_save = now
                rc = self.proc.wait()
                with self._lock:     # vs pause/resume/suspend: the final status can't be overwritten
                    if suspended_ckpt or rc == 99:
                        job.status = "suspended"
                        job.ckpt_dir = os.path.join(RUNS_DIR, f"{job.id}_ckpt")
                    elif rc == 0:
                        job.status = "done"
                    elif rc < 0:
                        # killed: ONLY an explicit user CANCEL -> 'cancelled' (artifacts cleaned below).
                        # App-exit shutdown(), a stall-sentry hard_interrupt(), or an UNEXPLAINED SIGKILL
                        # (kernel OOM killer under overnight swap thrash!) -> 'interrupted', so the
                        # checkpoint SURVIVES and the job stays resumable.
                        _user_cancel = self._cancel_req == job.id and not (
                            self._stop or self._interrupt_req == job.id)
                        job.status = "cancelled" if _user_cancel else "interrupted"
                    else:
                        job.status, job.error = "failed", f"exit {rc}"
            except Exception as e:
                with self._lock:
                    job.status, job.error = "failed", str(e)[:200]
                # the worker may still be rendering: kill its whole group + reap it BEFORE the runner
                # moves on, or the next job starts beside it on the same 8GB GPU (OOM)
                p = self.proc
                if p is not None and p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
                    try:
                        p.wait(timeout=30)
                    except Exception:
                        pass
            finally:
                if job.phase and job.phase_started is not None:   # the phase still open at exit
                    job.phase_secs[job.phase] = job.phase_secs.get(job.phase, 0) + (time.time() - job.phase_started)
                    job.phase_started = None
                with self._lock:
                    job.finished = time.time()
                    self.proc, self.current, self.paused = None, None, False
                    self._suspend_req = False
                    self._usr1_ready, self._suspend_pending = False, None
                    # stall-kill: promote LIVE to 'suspended' when the checkpoint is valid (same rule
                    # Job.load applies at app start) so the run is resumable from the QUEUE right away.
                    if job.status == "interrupted" and not self._stop:
                        _ck = os.path.join(RUNS_DIR, f"{job.id}_ckpt")
                        if job._ckpt_valid(_ck):
                            job.status, job.ckpt_dir = "suspended", _ck
                    self._interrupt_req = None
                    self._cancel_req = None
                if job.status in ("done", "cancelled"):
                    ck = os.path.join(RUNS_DIR, f"{job.id}_ckpt")
                    try:                 # keep the director's notes readable after cleanup (DIR RAW view)
                        dj = os.path.join(ck, "director.jsonl")
                        if os.path.exists(dj):
                            os.replace(dj, os.path.join(RUNS_DIR, f"{job.id}_director.jsonl"))
                    except Exception:
                        pass
                    shutil.rmtree(ck, ignore_errors=True)
                job.save()
                if job.status in ("done", "failed"):    # T10: capture a naturally-finished run as one experiment row
                    try:
                        import experiment_log
                        experiment_log.log_run(job)
                    except Exception:
                        pass
