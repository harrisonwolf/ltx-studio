"""JobManager lifecycle contract (studio_core + experiment_log) against a FAKE worker that prints the
real [[MARKER]] protocol, handles SIGUSR1 like director.py, writes checkpoints, and can emit invalid
UTF-8 / spawn a grandchild / spam log lines. Everything runs in a temp dir: RUNS_DIR, the experiment
log and every job's cwd are redirected, so the repo's real runs/ is never touched. No GPU, no audio.
Set CORE_SRC=<dir> to run these checks against another copy of studio_core.py/experiment_log.py."""
import sys, os, json, time, glob, shutil, signal, tempfile, threading, linecache
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("CORE_SRC") or REPO)
import studio_core as sc
import experiment_log as el

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

TMP = tempfile.mkdtemp(prefix="ltx_core_test_")
sc.RUNS_DIR = os.path.join(TMP, "runs"); os.makedirs(sc.RUNS_DIR)
el.LOG_PATH = os.path.join(sc.RUNS_DIR, "experiments.jsonl")

FAKE = os.path.join(TMP, "fake_worker.py")
open(FAKE, "w").write(r'''
import argparse, json, os, signal, subprocess, sys, time
ap = argparse.ArgumentParser()
ap.add_argument("--nseg", type=int, default=3); ap.add_argument("--steps", type=int, default=2)
ap.add_argument("--step_sleep", type=float, default=0.02); ap.add_argument("--import_delay", type=float, default=0.0)
ap.add_argument("--ckpt_dir"); ap.add_argument("--resume"); ap.add_argument("--preview")
ap.add_argument("--badutf8", action="store_true"); ap.add_argument("--spam", type=int, default=0)
ap.add_argument("--grandchild"); ap.add_argument("--save_sleep", type=float, default=0.0)
ap.add_argument("--hang_seg", type=int, default=0); ap.add_argument("--hang", type=float, default=0.0)
ap.add_argument("--numbered", type=int, default=0); ap.add_argument("--line_sleep", type=float, default=0.0)
args = ap.parse_args()
SUSP = [False]
print("[[PHASE importing]]", flush=True)
time.sleep(args.import_delay)                     # like director.py's heavy imports: no handler yet
signal.signal(signal.SIGUSR1, lambda s, f: SUSP.__setitem__(0, True))
print("[[LOAD 1 5 initializing]]", flush=True)
print("[[PHASE loading]]", flush=True)
if args.grandchild:
    subprocess.Popen([sys.executable, "-c",
        "import time,sys\nwhile True:\n open(sys.argv[1],'a').write('t\\n'); time.sleep(0.05)", args.grandchild],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(args.spam):
    print("warning: same", flush=True)
for i in range(args.numbered):                    # numbered log lines: line i is the i-th tail line
    print("n %d" % (i + 1), flush=True); time.sleep(args.line_sleep)
if args.badutf8:
    sys.stdout.flush(); sys.stdout.buffer.write(b"lib says \xff\xfe garbage\n"); sys.stdout.buffer.flush()
def ckpt(seg):
    if args.ckpt_dir:
        fd = os.path.join(args.ckpt_dir, "frames"); os.makedirs(fd, exist_ok=True)
        for i in range(seg * 2):
            open(os.path.join(fd, "%04d.png" % i), "w").close()
        json.dump({"n_frames": seg * 2, "seg_idx": seg}, open(os.path.join(args.ckpt_dir, "state.json"), "w"))
        print("[[CKPT %d %d]]" % (seg, seg * 2), flush=True)
def shot(seg):
    print("[[TOKENS %d %d]]" % (seg, 40 + seg), flush=True)
    for i in range(args.steps):
        if i == 0: print("[[PHASE generating]]", flush=True)
        print("[[STEP %d %d]]" % (i + 1, args.steps), flush=True)
        if i == 0 and seg == args.hang_seg and not args.resume:
            time.sleep(args.hang)                  # a long in-flight shot (to be hard-killed)
        if i + 1 == args.steps: print("[[PHASE decoding]]", flush=True)
        time.sleep(args.step_sleep)
if args.resume:
    seg = json.load(open(os.path.join(args.resume, "state.json")))["seg_idx"]
    print("[[SEG %d %d]]" % (seg + 1, args.nseg), flush=True); print("[[PHASE warmup]]", flush=True)
else:
    print("[[SEG 1 %d]]" % args.nseg, flush=True); print("[[PHASE warmup]]", flush=True)
    shot(1); seg = 1
    print("[[DRIFT 1 0 0]]", flush=True); print("[[PHASE redirecting]]", flush=True)
    print("[[PLAN 1 pan to the [left]]]", flush=True); print("[[DIRECT_MS 1 0 1234]]", flush=True)
    print("[[DIRECT a cat looks [left]]]", flush=True)
    ckpt(1)
while seg < args.nseg:
    if SUSP[0] and args.ckpt_dir:
        print("[[SUSPENDED %s]]" % args.ckpt_dir, flush=True); sys.exit(99)
    seg += 1
    print("[[SEG %d %d]]" % (seg, args.nseg), flush=True); print("[[PHASE warmup]]", flush=True)
    shot(seg)
    print("[[SEAMMSE %d 12]]" % seg, flush=True); print("[[DRIFT %d 30 20]]" % seg, flush=True)
    ckpt(seg)
print("[[PHASE saving]]", flush=True)
time.sleep(args.save_sleep)
print("DIRECTOR_DONE", flush=True)
''')

# ---- runner-thread tracer (installed before the runner starts): lets a check freeze the runner at
#      the exact point its job has a FINAL status but self.proc is still set, and run a "UI" call there.
RACE = {"fn": None, "fired": False}
def _runner_tr(frame, event, arg):
    if frame.f_code.co_name != "_run":
        return None
    def local(fr, ev, a):
        fn = RACE["fn"]
        if ev == "line" and fn and not RACE["fired"]:
            job = fr.f_locals.get("job")
            if job is not None and job.status in sc.ARCHIVED and m.proc is not None:
                RACE["fired"] = True
                t = threading.Thread(target=fn, daemon=True); t.start(); t.join(1.0)
        return local
    return local
threading.settrace(_runner_tr)
m = sc.JobManager()
threading.settrace(None)
m._ensure_wakelock = lambda: None           # never spawn the Windows wake-lock helper from a test

PY = sys.executable
def W(*a):
    return [PY, FAKE] + [str(x) for x in a]
def P(nseg, **kw):
    return dict({"nseg": nseg, "steps": 2, "cwd": TMP}, **kw)
def wait(cond, t=15.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False
def enq(title, kind, nseg, *a, **kw):
    return m.enqueue(title, kind, W("--nseg", nseg, *a), P(nseg, **kw))
def rows(j):
    return [r for r in el.load_runs() if r["run_id"] == j.id]
def settled(j):
    # the runner publishes the final status first and appends the experiment row just after (outside
    # the lock), so a done/failed run is settled only once its row is on disk -- reading rows earlier
    # raced the runner (intermittent empty/stale rows under load)
    return wait(lambda: j.status not in ("queued", "running", "paused", "suspending") and m.current != j.id
                and (j.status not in ("done", "failed") or bool(rows(j))))
def lines_in(path):
    try:
        return len(open(path).read().split())
    except OSError:
        return 0

# ======================= #10 PLAN/DIRECT text ending in "]" =======================
pl = sc._PLAN.search("[[PLAN 2 pan to the [left]]]")
dx = sc._DIRX.search("[[DIRECT a cat looks [left]]]")
dx2 = sc._DIRX.search("[[DIRECT plain prompt]] trailing junk")
check("#10 PLAN/DIRECT keep a trailing ']' (and still stop at the marker end)",
      pl and pl.group(2) == "pan to the [left]" and dx and dx.group(1) == "a cat looks [left]"
      and dx2 and dx2.group(1) == "plain prompt", (pl and pl.group(2), dx and dx.group(1)))

# ======================= checkpoint validity (crash-safe writer leaves extras) =======================
ck = os.path.join(TMP, "ckv"); os.makedirs(os.path.join(ck, "frames"))
for i in range(5):
    open(os.path.join(ck, "frames", "%04d.png" % i), "w").close()
open(os.path.join(ck, "frames", "0005.png.new"), "w").close()
open(os.path.join(ck, "frames", "0006.tmp"), "w").close()
json.dump({"n_frames": 4}, open(os.path.join(ck, "state.json"), "w"))
v_more = sc.Job._ckpt_valid(ck)
json.dump({"n_frames": 6}, open(os.path.join(ck, "state.json"), "w"))
v_short = sc.Job._ckpt_valid(ck)                  # 5 real PNGs < 6 (the .new/.tmp don't count)
json.dump({}, open(os.path.join(ck, "state.json"), "w"))
v_nokey = sc.Job._ckpt_valid(ck)
check("ckpt valid with >= n_frames PNGs; .png.new/.tmp ignored; too few / no n_frames invalid",
      v_more and not v_short and not v_nokey, (v_more, v_short, v_nokey))

# ======================= #1 invalid UTF-8 no longer kills the read loop; tail_count =======================
j = enq("utf8", "single", 1, "--badutf8", "--spam", 350)
settled(j)
log = open(j.logpath(), encoding="utf-8", errors="replace").read()
check("#1 one non-UTF-8 byte: run still finishes 'done' (bytes replaced in the log)",
      j.status == "done" and "�" in log, (j.status, j.error))
check("tail_count keeps rising past the 300-line ring",
      getattr(j, "tail_count", 0) >= 350 and len(j.tail) == 300, (getattr(j, "tail_count", None), len(j.tail)))

# ======================= #1 exception in the read loop kills + reaps the worker =======================
a = enq("boom", "director", 3, "--steps", 10, "--step_sleep", 0.2, "--import_delay", 0.3)
_orig_save, _boom = a.save, {"n": 0}
def _bad_save():
    if m.current == a.id and a.step >= 1 and not _boom["n"]:
        _boom["n"] = 1
        raise OSError(28, "No space left on device")
    return _orig_save()
a.save = _bad_save
wait(lambda: m.current == a.id and m.proc is not None)
a_proc = m.proc
settled(a)
alive = a_proc.poll() is None
check("#1 ENOSPC mid-run: job failed AND its worker was killed + reaped before the queue moved on",
      a.status == "failed" and "No space" in a.error and not alive, (a.status, a.error, "alive" if alive else "dead"))
if alive:                                          # old code: don't leave the orphan rendering
    try:
        os.killpg(a_proc.pid, signal.SIGKILL)
    except Exception:
        pass
del a.save

# ======================= #3 last shot + last phase are timed =======================
s1 = enq("single", "single", 1, "--save_sleep", 0.2)
s3 = enq("three", "director", 3, "--save_sleep", 0.2)
settled(s1); settled(s3)
r3 = rows(s3)
check("#3 every shot timed (1-shot -> 1, 3-shot -> 3) and the final 'saving' phase is in phase_secs",
      len(s1.seg_secs) == 1 and len(s3.seg_secs) == 3 and s3.phase_secs.get("saving", 0) >= 0.15
      and "saving" in s1.phase_secs and r3 and len(r3[-1]["seg_secs"]) == 3,
      (s1.seg_secs, s3.seg_secs, s3.phase_secs))

# ======================= #7 suspend before the SIGUSR1 handler exists =======================
sj = enq("early-suspend", "director", 3, "--import_delay", 1.0)
wait(lambda: m.current == sj.id and m.proc is not None)
early_phase = sj.phase
res = m.suspend()
settled(sj)
check("#7 suspend during startup is held until the worker listens -> lands 'suspended', not killed",
      sj.status == "suspended" and sc.Job._ckpt_valid(sj.ckpt_dir) and isinstance(res, tuple) and res[0],
      (early_phase, res, sj.status))
res_idle = m.suspend()
check("#7 suspend() reports why it did nothing", isinstance(res_idle, tuple) and res_idle[0] is False, res_idle)

# ======================= #5 REMOVE on a resumed (queued) run =======================
sj = enq("to-resume", "director", 3, "--step_sleep", 0.1)
wait(lambda: sj.seg == 2)                           # well past startup: a plain checkpointed suspend
m.suspend()
settled(sj)
blk = enq("blocker", "single", 1, "--steps", 40, "--step_sleep", 0.1)
wait(lambda: m.current == blk.id)
r0 = sj.resumes
t0 = (sj.started, sj.finished, getattr(sj, "prior_secs", 0), sj.elapsed())
m.resume_suspended(sj.id)
q_ok = sj.status == "queued" and "--resume" in sj.cmd
out5 = m.remove(sj.id)
check("#5 REMOVE on a resumed run puts it back to 'suspended' (json + checkpoint kept)",
      q_ok and sj.status == "suspended" and sj.id in m.jobs and os.path.exists(sj.jpath())
      and sc.Job._ckpt_valid(sj.ckpt_dir) and sj.resumes == r0 and sj in m.suspended(),
      (out5, sj.status, os.path.exists(sj.jpath())))
t1 = (sj.started, sj.finished, getattr(sj, "prior_secs", 0), sj.elapsed())
disk = json.load(open(sj.jpath()))
check("R4 REMOVE's undo-resume restores started/finished/prior_secs (elapsed() unchanged, persisted)",
      t1 == t0 and t0[0] and disk.get("started") == t0[0] and disk.get("finished") == t0[1], (t0, t1))
m.resume_suspended(sj.id)
try:
    os.remove(os.path.join(sj.ckpt_dir, "state.json"))      # checkpoint no longer valid
except OSError:
    pass
open(sj.preview, "w").close()
m.remove(sj.id)
left = sorted(os.path.basename(p) for p in glob.glob(os.path.join(sc.RUNS_DIR, sj.id + "*")))
check("#5 REMOVE on a resumed run with a dead checkpoint leaves no orphans (json/log/ckpt/preview)",
      sj.id not in m.jobs and left == [], left)

# ======================= #4 PROMOTE doesn't rewrite `created` =======================
old = sc.Job("250101-000000-01", "ancient", "single", ["true"], {"nseg": 1})
old.created = time.time() - 30 * 86400; old.status = "done"; old.save(); m.jobs[old.id] = old
q1 = enq("q1", "single", 1)
q2 = enq("q2", "single", 1)
c2 = q2.created
m.promote(q2.id)
order = [x.id for x in m.queued()]
check("#4 PROMOTE moves the run to the front without touching its created timestamp",
      order[:2] == [q2.id, q1.id] and q2.created == c2, (order, c2 - q2.created))
k = sc.Job.load(q2.jpath())
d = json.load(open(q1.jpath())); d.pop("qorder", None)
oldp = os.path.join(TMP, "legacy.json"); json.dump(d, open(oldp, "w"))
lg = sc.Job.load(oldp)
check("#4 queue order round-trips through JSON; pre-fix JSON defaults to created",
      getattr(k, "qorder", None) == getattr(q2, "qorder", 0) and k.created == c2
      and getattr(lg, "qorder", None) == lg.created, (getattr(k, "qorder", None), getattr(lg, "qorder", None)))
m.cancel()                                          # kill the blocker -> q2 then q1 run
settled(blk); settled(q1); settled(q2)
rq = rows(q2)
check("#4 promoted run's experiment row / archive keep the real creation time",
      rq and rq[-1]["ts_created"] == c2 and q2.started >= q2.created
      and [x.id for x in m.archived()].index(q2.id) < [x.id for x in m.archived()].index(old.id),
      rq and rq[-1]["ts_created"] - c2)

# ======================= #6 pause/resume/suspend reach the whole process group =======================
gc = os.path.join(TMP, "gc.txt")
g = enq("group", "director", 3, "--steps", 12, "--step_sleep", 0.1, "--grandchild", gc)
wait(lambda: g.step >= 2 and lines_in(gc) > 0)
m.pause(); time.sleep(0.25)
n1 = lines_in(gc); time.sleep(0.6); n2 = lines_in(gc)
m.resume(); time.sleep(0.4); n3 = lines_in(gc)
m.pause(); time.sleep(0.2)
m.suspend(); time.sleep(0.4); n4 = lines_in(gc)
settled(g)
check("#6 pause freezes the worker's children too; resume / suspend-from-paused continue them",
      n2 == n1 and n3 > n2 and n4 > n3 and g.status == "suspended", (n1, n2, n3, n4, g.status))
for p in glob.glob("/proc/[0-9]*/cmdline"):        # reap the fake grandchild (it outlives its parent)
    try:
        if gc.encode() in open(p, "rb").read():
            os.kill(int(p.split("/")[2]), signal.SIGKILL)
    except Exception:
        pass

# ======================= #2 resume keeps earlier legs' telemetry =======================
rj = enq("resume", "director", 4, "--step_sleep", 0.1)
wait(lambda: rj.seg == 2)
m.suspend()
settled(rj)
leg1 = rj.status
m.resume_suspended(rj.id)
settled(rj)
rr = rows(rj)
logtxt = open(rj.logpath()).read()
segs = lambda xs: [x[0] for x in xs]
check("#2 resumed run keeps every leg: seg_secs/tokens/drift/seam/dir_ms + the log",
      leg1 == "suspended" and rj.status == "done" and len(rj.seg_secs) == 4
      and segs(rj.tok_counts) == [1, 2, 3, 4] and segs(rj.drift) == [1, 2, 3, 4]
      and segs(rj.seam_mse) == [2, 3, 4] and 1 in rj.dir_ms
      and "[[SEG 1 4]]" in logtxt and "[[SEG 4 4]]" in logtxt,
      (leg1, rj.seg_secs, rj.tok_counts, rj.drift, list(rj.dir_ms)))
check("#2 experiment row says it was resumed and spans both legs",
      rr and rr[-1].get("resumes") == 1 and segs(rr[-1]["tok_counts"]) == [1, 2, 3, 4], rr and rr[-1].get("resumes"))

hk = enq("hardkill", "director", 4, "--step_sleep", 0.3)
wait(lambda: hk.seg == 3 and hk.step >= 1)
m.hard_interrupt()
settled(hk)
leg1 = hk.status
m.resume_suspended(hk.id)
settled(hk)
check("#2 resume after a hard kill: the re-rendered shot isn't double counted",
      leg1 == "suspended" and hk.status == "done" and segs(hk.tok_counts) == [1, 2, 3, 4]
      and len(hk.seg_secs) == 4 and segs(hk.drift) == [1, 2, 3, 4], (leg1, hk.tok_counts, hk.seg_secs))

# ======================= R1 runtime_s / phase_secs across legs =======================
rt = enq("runtime", "director", 3, "--step_sleep", 0.3)
wait(lambda: rt.seg == 2)
m.suspend()
settled(rt)
leg1_st, leg1 = rt.status, rt.finished - rt.started
m.resume_suspended(rt.id)
settled(rt)
leg2 = rt.finished - rt.started
rr = rows(rt)
disk = json.load(open(rt.jpath()))
check("R1 resumed run: runtime_s spans every leg (prior_secs persisted); elapsed() stays the last leg",
      leg1_st == "suspended" and rt.status == "done" and leg1 >= 1.0 and rr
      and int(leg1 + leg2) <= rr[-1]["runtime_s"] <= int(leg1 + leg2) + 1
      and abs(disk.get("prior_secs", 0) - leg1) < 0.01 and rt.elapsed() == int(leg2),
      (leg1_st, leg1, leg2, rr and rr[-1]["runtime_s"], disk.get("prior_secs")))
d = json.load(open(rt.jpath()))
for k in ("prior_secs", "pre_resume", "phase_ckpt"):
    d.pop(k, None)
oldp = os.path.join(TMP, "legacy_rt.json"); json.dump(d, open(oldp, "w"))
try:
    lj = sc.Job.load(oldp)
    legacy_ok = lj.prior_secs == 0 and lj.run_secs() == lj.elapsed() and el.build_record(lj)["runtime_s"] == lj.elapsed()
except Exception as e:
    legacy_ok = e
check("R1 pre-fix JSON (no prior_secs) loads with prior_secs=0; runtime_s == elapsed()", legacy_ok is True, legacy_ok)

hp = enq("hardkill-phases", "director", 3, "--hang_seg", 3, "--hang", 5.0)
wait(lambda: hp.seg == 3 and hp.step >= 1)
time.sleep(1.2)                                     # >=1.2s of in-flight 'generating' that will be lost
m.hard_interrupt()
settled(hp)
leg1_st, gen1 = hp.status, hp.phase_secs.get("generating", 0)
m.resume_suspended(hp.id)
settled(hp)
gen = hp.phase_secs.get("generating", 0)
rg = rows(hp)
check("R1 hard kill -> resume: the discarded in-flight shot's phase time is dropped from phase_secs",
      leg1_st == "suspended" and hp.status == "done" and gen1 >= 1.0 and gen < 0.8 and len(hp.seg_secs) == 3
      and rg and rg[-1]["phase_secs"].get("generating", 0) == gen, (leg1_st, gen1, gen, hp.seg_secs))

# ======================= R2 resume from the FINAL checkpoint: no phantom shot =======================
fk = enq("final-ckpt", "director", 3, "--save_sleep", 1.5)
wait(lambda: fk.phase == "saving")
m.hard_interrupt()                                   # killed while saving: the final checkpoint is valid
settled(fk)
leg1_st, n1 = fk.status, list(fk.seg_secs)
m.resume_suspended(fk.id)                            # resumes with every shot done -> renders nothing
settled(fk)
rf = rows(fk)
check("R2 resume from the final checkpoint doesn't append a phantom shot to seg_secs",
      leg1_st == "suspended" and fk.status == "done" and len(n1) == 3 and len(fk.seg_secs) == 3
      and rf and len(rf[-1]["seg_secs"]) == 3, (leg1_st, n1, fk.seg_secs))

# ======================= R3 CANCEL / SUSPEND between the runner's claim and Popen =======================
_cls_save = sc.Job.save
GATE = {"title": None, "in": threading.Event(), "go": threading.Event()}
def _gated_save(self):
    if (self.title == GATE["title"] and threading.current_thread() is not threading.main_thread()
            and m.current == self.id and m.proc is None and not GATE["in"].is_set()):
        GATE["in"].set(); GATE["go"].wait(10)       # hold the runner inside the claim -> Popen window
    return _cls_save(self)
sc.Job.save = _gated_save
try:
    GATE.update(title="win-cancel"); GATE["in"].clear(); GATE["go"].clear()
    wc = enq("win-cancel", "single", 1, "--steps", 40, "--step_sleep", 0.1)
    held = GATE["in"].wait(10)
    m.cancel(); GATE["go"].set(); t0 = time.time()
    settled(wc)
    check("R3 CANCEL in the claim->Popen window is honored (not silently dropped)",
          held and wc.status == "cancelled" and time.time() - t0 < 3, (held, wc.status, time.time() - t0))
    GATE.update(title="win-susp"); GATE["in"].clear(); GATE["go"].clear()
    ws = enq("win-susp", "director", 3, "--step_sleep", 0.05)
    held = GATE["in"].wait(10)
    res = m.suspend(); GATE["go"].set()
    settled(ws)
    check("R3 SUSPEND in the claim->Popen window is held and lands 'suspended'",
          held and isinstance(res, tuple) and res[0] and ws.status == "suspended" and sc.Job._ckpt_valid(ws.ckpt_dir)
          and len(ws.seg_secs) < 3, (held, res, ws.status, ws.seg_secs))
finally:
    GATE["go"].set()
    sc.Job.save = _cls_save

# ======================= R6 LIVE log: tail + tail_count read as a consistent pair =======================
nj = enq("numbered", "single", 1, "--numbered", 600, "--line_sleep", 0.003)
wait(lambda: len(nj.tail) > 5)
bad, seen = [], 0
while nj.status == "running" and seen < 60:
    t, n = nj.tail_snapshot()                        # exactly how studio.py's LIVE view reads it
    last = t[-1] if t else None
    time.sleep(0.005)                                # the runner keeps appending meanwhile...
    if t and last.startswith("n "):
        seen += 1
        if t[-1] != last or last != "n %d" % n:      # ...but the snapshot never changes under us
            bad.append((last, t[-1], n))
settled(nj)
check("R6 a reader's tail/tail_count pair always matches (count never ahead of / behind the list)",
      seen >= 20 and not bad, (seen, bad[:5]))

# ======================= #8 UI action racing the runner's final status =======================
for label, fn in (("suspend", lambda: m.suspend()), ("pause", lambda: m.pause())):
    RACE.update(fn=fn, fired=False)
    x = enq("race-" + label, "director", 2)
    settled(x)
    time.sleep(0.1)
    RACE["fn"] = None
    disk = json.load(open(x.jpath()))["status"]
    check("#8 %s() in the finish window can't overwrite the final status" % label,
          RACE["fired"] and x.status == "done" and disk == "done" and len(rows(x)) == 1 and not m.paused,
          (RACE["fired"], x.status, disk))

# ======================= #9 cancel() can't hit the NEXT job =======================
A = enq("A", "single", 1, "--steps", 3, "--step_sleep", 0.1)
B = enq("B", "single", 1, "--steps", 8, "--step_sleep", 0.1)
wait(lambda: m.current == A.id and m.proc is not None)
_hit = {"n": 0}
def _cancel_tr(frame, event, arg):
    if frame.f_code.co_name != "cancel":
        return None
    def local(fr, ev, a):
        if ev == "line" and not _hit["n"]:
            src = linecache.getline(fr.f_code.co_filename, fr.f_lineno).strip()
            if src.startswith("if") and "paused" in src:       # past the proc/current check, lock released
                _hit["n"] = 1
                wait(lambda: m.current == B.id and m.proc is not None and B.status == "running")
                time.sleep(0.2)
        return local
    return local
sys.settrace(_cancel_tr); m.cancel(); sys.settrace(None)
settled(A); settled(B)
check("#9 cancel racing A's natural finish never kills B", _hit["n"] and A.status == "done" and B.status == "done",
      (A.status, B.status))

# ======================= R7 app died mid-SUSPEND: the leg's end time survives the reload =======================
sj = sc.Job("susp_crash", "t", "chained", W("--nseg", 3), P(3))
sj.status, sj.started, sj.finished = "suspending", time.time() - 600, None
sj.save()
ck = os.path.join(sc.RUNS_DIR, "susp_crash_ckpt")
os.makedirs(os.path.join(ck, "frames"), exist_ok=True)
json.dump({"n_frames": 1}, open(os.path.join(ck, "state.json"), "w"))
open(os.path.join(ck, "frames", "0000.png"), "w").close()
open(sj.logpath(), "w").write("x\n")
os.utime(sj.logpath(), (sj.started + 300, sj.started + 300))
lj = sc.Job.load(sj.jpath())
check("R7 'suspending' at app death -> suspended with the leg's end time (from the log)",
      lj.status == "suspended" and lj.finished and abs(lj.finished - (sj.started + 300)) < 2, (lj.status, lj.finished))
m.jobs[lj.id] = lj
m.resume_suspended(lj.id)
check("R7 ...so RESUME folds that leg into prior_secs", abs((lj.prior_secs or 0) - 300) < 2, lj.prior_secs)
m.remove(lj.id)
# a leg killed before it logged anything: the log's mtime is the PREVIOUS leg's -> never end before start
sj2 = sc.Job("susp_early", "t", "chained", W("--nseg", 3), P(3))
sj2.status, sj2.started, sj2.finished = "suspending", time.time() - 100, None
sj2.save()
ck2 = os.path.join(sc.RUNS_DIR, "susp_early_ckpt")
os.makedirs(os.path.join(ck2, "frames"), exist_ok=True)
json.dump({"n_frames": 1}, open(os.path.join(ck2, "state.json"), "w"))
open(os.path.join(ck2, "frames", "0000.png"), "w").close()
open(sj2.logpath(), "w").write("x\n")
os.utime(sj2.logpath(), (sj2.started - 400, sj2.started - 400))
lj2 = sc.Job.load(sj2.jpath())
check("R7 a leg's reloaded end is never before its start (no negative runtime)",
      lj2.finished is not None and lj2.finished >= lj2.started and lj2.run_secs() >= 0, (lj2.started, lj2.finished))
# ======================= R8 resume_suspended takes the manager lock (runner claims under it) =======================
rj = sc.Job("resume_lock", "t", "chained", W("--nseg", 3), P(3))
rj.status, rj.started, rj.finished, rj.ckpt_dir = "suspended", time.time() - 100, time.time() - 50, ck
m.jobs[rj.id] = rj
held = threading.Event()
def _hold():
    with m._lock:
        held.set(); time.sleep(0.4)
threading.Thread(target=_hold, daemon=True).start(); held.wait(2)
rt = threading.Thread(target=m.resume_suspended, args=(rj.id,), daemon=True); rt.start()
time.sleep(0.15)
mid = rj.status
rt.join(2)
check("R8 resume_suspended waits for the manager lock before publishing 'queued'", mid == "suspended", mid)
m.remove(rj.id)

m._stop = True
time.sleep(0.5)
shutil.rmtree(TMP, ignore_errors=True)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
