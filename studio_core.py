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
_DIRX = re.compile(r"\[\[DIRECT\s+(.*?)\]\]")
_PHASE = re.compile(r"\[\[PHASE\s+(\w+)\]\]")
_LOAD = re.compile(r"\[\[LOAD\s+(\d+)\s+(\d+)\s+(.*?)\]\]")
_CKPT = re.compile(r"\[\[CKPT\s+(\d+)\s+(\d+)\]\]")
_SUSP = re.compile(r"\[\[SUSPENDED\s+(.*?)\]\]")
_PLAN = re.compile(r"\[\[PLAN\s+(\d+)\s+(.*?)\]\]")
_DMS = re.compile(r"\[\[DIRECT_MS\s+(\d+)\s+(\d+)\s+(\d+)\]\]")   # space-form: seg load_ms infer_ms
_VRAM = re.compile(r"\[\[VRAM\s+(\d+)\]\]")                       # per-shot peak CUDA MB (experiment_log DV)
_SEAMMSE = re.compile(r"\[\[SEAMMSE\s+(\d+)\s+(-?\d+)\]\]")       # seam continuity: [seg, mse*100] (Q3)
_DRIFT = re.compile(r"\[\[DRIFT\s+(\d+)\s+(-?\d+)\s+(-?\d+)\]\]") # drift vs anchor: [seg, pre*100, post*100] (Q3)
_TOKENS = re.compile(r"\[\[TOKENS\s+(\d+)\s+(\d+)\]\]")           # prompt token count: [seg, n] (Q3)
_FIELDS = ["id", "title", "kind", "cmd", "params", "status", "seg", "nseg", "step", "nstep",
           "out", "error", "director", "created", "started", "finished",
           "phase", "load_step", "load_total", "load_msg", "saw_step", "first_step_ts", "first_step_seg",
           "ckpt_dir", "resumes", "last_ckpt_seg", "preview", "plans", "dir_ms",
           "phase_started", "phase_secs", "seg_started", "seg_secs", "peak_vram",
           "seam_mse", "drift", "tok_counts"]
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
        self.started = self.finished = None
        self.tail = []              # last log lines, in-memory
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
        # ---- telemetry: phase + per-shot timing ----
        self.phase_started = None   # wall-clock the current phase began
        self.phase_secs = {}        # {phase: cumulative seconds}
        self.seg_started = None     # wall-clock the current shot began
        self.seg_secs = []          # [seconds per completed shot]
        self.peak_vram = None       # max [[VRAM mb]] seen (experiment_log measured DV)
        # ---- Q3: measurement floor (seam/drift/token telemetry) ----
        self.seam_mse = []          # [[seg, mse*100], ...] seam continuity per continuation
        self.drift = []             # [[seg, pre*100, post*100], ...] drift vs the shot-1 anchor
        self.tok_counts = []        # [[seg, n_tokens], ...] prompt length per shot

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
            j.status = "suspended"
            j.ckpt_dir = ckpt
        elif j.status == "suspending":
            j.status = "interrupted"
            j.finished = j.finished or (
                os.path.getmtime(j.logpath()) if os.path.exists(j.logpath()) else j.started)
        if os.path.exists(j.logpath()):
            try:
                j.tail = open(j.logpath()).read().splitlines()[-300:]
            except Exception:
                pass
        return j

    def elapsed(self):
        a = self.started or self.created
        b = self.finished or time.time()
        return int(b - a)

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
        """A checkpoint is valid IFF state.json exists AND n_frames == #PNGs in frames/."""
        try:
            sp = os.path.join(ckpt, "state.json")
            if not os.path.exists(sp):
                return False
            st = json.load(open(sp))
            npng = len(glob.glob(os.path.join(ckpt, "frames", "*.png")))
            return int(st.get("n_frames", -1)) == npng
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

    def queued(self):
        return [j for j in self._sorted() if j.status == "queued"]

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
        return [j for j in self._sorted() if j.status == "suspended"]

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
        j = self.jobs.get(jid)
        if j and j.status == "queued":
            self.jobs.pop(jid, None)
            try:
                os.remove(j.jpath())
            except OSError:
                pass

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

    def pause(self):
        if self.proc and self.current and not self.paused:
            try:
                self.proc.send_signal(signal.SIGSTOP)
                self.paused = True
                self.jobs[self.current].status = "paused"; self.jobs[self.current].save()
            except Exception:
                pass

    def resume(self):
        if self.proc and self.current and self.paused:
            try:
                self.proc.send_signal(signal.SIGCONT)
                self.paused = False
                self.jobs[self.current].status = "running"; self.jobs[self.current].save()
            except Exception:
                pass

    def cancel(self):
        if self.proc and self.current:
            try:
                if self.paused:
                    self.proc.send_signal(signal.SIGCONT)
                try:
                    # kill the whole process GROUP: director.py's VLM sidecar/daemon children must die
                    # too, or an orphan keeps holding GPU/RAM and OOMs the next queued run.
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    self.proc.kill()
            except Exception:
                pass

    def shutdown(self):
        """Clean app-exit (T9): stop the runner + kill the running subprocess so it can't orphan onto
        the GPU into the next session. Queued jobs persist on disk and resume next launch; a killed
        multi-segment run recovers to 'suspended' from its last checkpoint (only the in-flight shot lost)."""
        self._stop = True
        self.cancel()

    def suspend(self):
        """Clean checkpointed suspend (multi-segment jobs only) via SIGUSR1. Works even if paused."""
        job = self.jobs.get(self.current)
        if self.proc and self.current and job and int(job.nseg or 1) > 1:
            try:
                self._suspend_req = True
                if self.paused:                  # a SIGSTOP'd process must be continued to run its handler
                    self.proc.send_signal(signal.SIGCONT)
                    self.paused = False
                self.proc.send_signal(signal.SIGUSR1)
                job.status = "suspending"; job.save()
            except Exception:
                pass

    def resume_suspended(self, jid):
        job = self.jobs.get(jid)
        if job and job.status == "suspended":
            if "--resume" not in job.cmd:
                job.cmd = list(job.cmd) + ["--resume", job.ckpt_dir]
            job.status = "queued"
            job.resumes += 1
            job.started = job.finished = None
            job.save()

    def promote(self, jid):
        job = self.jobs.get(jid)
        if job and job.status in ("queued", "suspended"):
            job.created = min(j.created for j in self.jobs.values()) - 1
            job.save()

    # ---- runner ----
    def _loop(self):
        while not self._stop:
            nxt = next((j for j in self._sorted() if j.status == "queued"), None)
            if nxt is None:
                time.sleep(0.4); continue
            self._run(nxt)

    def _run(self, job):
        env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
        # T14: VRAM headroom reserved for the Windows desktop, as a fraction of the 8GB card.
        # gpu_budget.cap_vram() reads this env var in the subprocess (STUDIO_VRAM_HEADROOM=0.12 default).
        try:
            env["STUDIO_VRAM_HEADROOM"] = str(max(0.0, float(self.vram_reserve_gb)) / 8.0)
        except Exception:
            pass
        job.status, job.started, job.seg, job.step = "running", time.time(), 0, 0
        # reset load/phase tracking on every (re)start
        job.phase, job.load_step, job.load_total, job.load_msg = "", 0, 0, ""
        job.saw_step, job.first_step_ts, job.first_step_seg = False, None, 1
        job.phase_started, job.phase_secs = None, {}
        job.seg_started, job.seg_secs = None, []
        job.dir_ms = {}
        job.seam_mse, job.drift, job.tok_counts = [], [], []
        job.save()
        self.current, self.paused = job.id, False
        self._suspend_req = False
        suspended_ckpt = None
        last_save = time.time()
        cwd = job.params.get("cwd") or REPO
        with open(job.logpath(), "w") as lf:
            try:
                self.proc = subprocess.Popen(job.cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                                             start_new_session=True)   # own process group -> cancel can killpg children
                for line in self.proc.stdout:
                    line = line.rstrip()
                    lf.write(line + "\n"); lf.flush()
                    transition = False
                    m = _PROG.search(line)
                    if m:
                        a, b = int(m.group(2)), int(m.group(3))
                        if m.group(1) == "SEG":
                            _now = time.time()
                            if job.seg_started is not None and a > job.seg:
                                job.seg_secs.append(int(_now - job.seg_started))
                            job.seg, job.nseg, job.step = a, b, 0
                            job.seg_started = _now
                            transition = True
                        else:
                            job.step, job.nstep = a, b
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
                    d = _DIRX.search(line)
                    if d:
                        job.director = d.group(1)
                        if job.plans and not job.plans[-1][2]:   # back-fill prompt onto the open note
                            job.plans[-1][2] = d.group(1)
                        transition = True
                    ph = _PHASE.search(line)
                    if ph:
                        _now = time.time()
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
                        transition = True
                    su = _SUSP.search(line)
                    if su:
                        suspended_ckpt = su.group(1)
                        transition = True
                    if line and "vision_model" not in line and not line.startswith("[["):
                        job.tail.append(line); job.tail = job.tail[-300:]
                    now = time.time()
                    if transition or (now - last_save) >= 3:
                        job.save(); last_save = now
                rc = self.proc.wait()
                if suspended_ckpt or rc == 99:
                    job.status = "suspended"
                    job.ckpt_dir = os.path.join(RUNS_DIR, f"{job.id}_ckpt")
                elif rc == 0:
                    job.status = "done"
                elif rc < 0:
                    # killed: a user CANCEL -> 'cancelled' (artifacts cleaned below); an app-exit
                    # shutdown() -> 'interrupted' so the checkpoint SURVIVES and Job.load promotes
                    # it back to 'suspended' at the next launch (T9's whole point).
                    job.status = "interrupted" if self._stop else "cancelled"
                else:
                    job.status, job.error = "failed", f"exit {rc}"
            except Exception as e:
                job.status, job.error = "failed", str(e)[:200]
            finally:
                job.finished = time.time()
                self.proc, self.current, self.paused = None, None, False
                self._suspend_req = False
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
