"""GPU-worker regressions (director.py / run_ltx.py / vlm_planner.py / vlm_director7b.py), CPU only:
no model is ever loaded. director.main() is driven end-to-end with a FAKE backend whose frames carry
their timeline index in pixel (0,0) R/G (dup/drop/reorder shows) and the producing shot in B (so a seam
crossfaded twice shows). Covers: crossfade color-match reference (drift held), crash-safe checkpoints,
numeric frame order past 10000, CPU consult never caps VRAM, HOLD skip + palette pool survive resume,
early SIGUSR1, fps -> LTX frame_rate, 9-frame floor, latent_fuse seam alignment, atomic mp4 export,
markdown-bold PLAN/PROMPT labels. Plus default-path invariants (frame counts/order, resume equivalence).

WORKER_SRC=<dir> runs the same checks against another copy of the worker scripts (e.g. HEAD's)."""
import os, sys, io, json, glob, shutil, tempfile, types, runpy, contextlib, subprocess, time
os.environ["STUDIO_MUTE"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = ""          # never touch a GPU, even if one exists
os.environ["HF_HUB_OFFLINE"] = "1"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.abspath(os.environ.get("WORKER_SRC") or REPO)
sys.path.insert(0, REPO)
sys.path.insert(0, SRC)
import numpy as np
import torch
from PIL import Image
import director

ok = True
T0 = time.time()


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))


def section(fn):
    """Run one test group; an exception is a FAIL of that group, never a crash of the suite."""
    try:
        fn()
    except BaseException as e:          # noqa: BLE001 -- KillSim etc. must not escape either
        if isinstance(e, KeyboardInterrupt) and not isinstance(e, KillSim):
            raise
        check(fn.__name__ + " (raised)", False, "%s: %s" % (type(e).__name__, e))
    return fn


class KillSim(KeyboardInterrupt):
    """Simulated SIGKILL: a BaseException, so no `except Exception` in the worker swallows it."""


TMP = tempfile.mkdtemp(prefix="test_worker_")

# ------------------------------------------------------------------ fake backend harness
REAL_COLOR_MATCH = director.color_match
H = types.SimpleNamespace(calls=[], hook=None, exported=None, gen=None)


def fr(idx, tag=0, W=64, H_=32):
    a = np.zeros((H_, W, 3), np.uint8)
    a[..., 0] = idx % 256
    a[..., 1] = idx // 256
    a[..., 2] = tag
    return Image.fromarray(a)


def idx_of(im):
    a = np.asarray(im.convert("RGB"))
    return int(a[0, 0, 0]) + 256 * int(a[0, 0, 1])


def _index_gen(self, cond, seed, prompt, neg, step_cb, is_cont):
    """Head reproduces the conditioning tail's indices; body continues them. B = per-shot tag, so the
    seam crossfade changes B only (R/G of tail and head are equal)."""
    n, tag = self.seg_frames, (seed * 40 + 20) % 256
    if cond[0] == "first":
        return [fr(i, tag) for i in range(n)]
    tail = cond[1]
    start = idx_of(tail[-1]) + 1
    out = [fr(idx_of(t), tag) for t in tail] + [fr(start + i, tag) for i in range(n - len(tail))]
    return out[len(tail):] if self.strips_cond_head else out


class FakeMixin:
    def load(self):
        self.pipe = None
        self._ref_anchor = None

    def first_cond(self, image_path):
        return ("first",)

    def tail_cond(self, tail, strength):
        return ("tail", list(tail))

    def gen(self, cond, seed, prompt, neg, step_cb, is_cont):
        H.calls.append((seed, prompt, is_cont))
        if H.hook:
            H.hook(len(H.calls))
        return (H.gen or _index_gen)(self, cond, seed, prompt, neg, step_cb, is_cont)


class FakeLTX(FakeMixin, director.LTXBackend):
    pass


class FakeWan(FakeMixin, director.WanBackend):
    pass


def fake_make_backend(args):
    b = FakeWan(args) if args.backend in ("wan", "wan-turbo") else FakeLTX(args)
    b.turbo = args.backend == "wan-turbo"
    return b


EXPORT = types.SimpleNamespace(fail=False, paths=[])


def fake_export(video, out, fps=None):
    EXPORT.paths.append(out)
    with open(out, "wb") as fh:          # a real writer creates the file -> so does the fake
        fh.write(b"partial" if EXPORT.fail else b"MP4:%d" % len(video))
    if EXPORT.fail:
        raise KillSim("killed mid-encode")
    H.exported = list(video)
    return out


director.make_backend = fake_make_backend
director.export_to_video = fake_export


def run(argv, identity_cm=True, hook=None, gen=None):
    """-> (rc, stdout). identity_cm stubs color_match so pixel (0,0) keeps the frame index."""
    H.calls.clear()
    H.hook, H.gen, H.exported = hook, gen, None
    director._SUSPEND = False
    director.color_match = (lambda frames, ref: list(frames)) if identity_cm else REAL_COLOR_MATCH
    out = os.path.join(TMP, "out", "x.mp4")
    sys.argv = ["director.py", "--width", "64", "--height", "32", "--out", out] + list(argv)
    buf, rc = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(buf):
            try:
                director.main()
            except SystemExit as e:
                rc = e.code
    finally:
        H.hook = H.gen = None
        director.color_match = REAL_COLOR_MATCH
    return rc, buf.getvalue()


def arrs(video):
    return [np.asarray(f.convert("RGB")).copy() for f in video]


def same(a, b):
    return len(a) == len(b) and all(np.array_equal(x, y) for x, y in zip(a, b))


def suspend_at(k):
    def hook(ncall):
        if ncall == k:
            director._SUSPEND = True
    return hook


def resume_equiv(base, k, gen=None, identity_cm=True):
    """Fresh run vs (suspend before gen call k -> resume). -> (fresh_frames, resumed_frames, calls_ok)."""
    run(base, identity_cm, gen=gen)
    ref, ref_calls = arrs(H.exported), list(H.calls)
    ck = tempfile.mkdtemp(dir=TMP)
    rc1, _ = run(base + ["--ckpt_dir", ck], identity_cm, hook=suspend_at(k), gen=gen)
    c1 = list(H.calls)
    rc2, _ = run(base + ["--ckpt_dir", ck, "--resume", ck], identity_cm, gen=gen)
    got = arrs(H.exported)
    shutil.rmtree(ck, ignore_errors=True)
    return ref, got, (rc1 == 99 and c1 + H.calls == ref_calls)


# ------------------------------------------------------------------ default-path invariants
@section
def invariants_counts_order():
    bad = []
    for backend in ("ltx", "wan"):
        for total, seg in ((1, 1), (3, 2.04), (7, 2.5), (12, 3), (20, 3)):
            for nocf in (False, True):
                argv = ["--prompt", "x", "--total", str(total), "--seg", str(seg), "--backend", backend]
                rc, out = run(argv + (["--no_crossfade"] if nocf else []))
                idxs = [idx_of(f) for f in H.exported]
                tgt = int(out.split("target=")[1].split("f")[0])
                if idxs != list(range(len(idxs))) or len(idxs) != tgt:
                    bad.append((backend, total, seg, nocf, len(idxs), tgt))
    check("frame counts == target and timeline order intact (ltx/wan, crossfade on/off)", not bad, bad[:3])


@section
def invariants_resume_equivalence():
    bad = []
    for backend in ("ltx", "wan"):
        for nocf in (False, True):
            base = ["--prompt", "x", "--total", "12", "--seg", "3", "--backend", backend] + \
                   (["--no_crossfade"] if nocf else [])
            for k in (2, 4):
                ref, got, calls_ok = resume_equiv(base, k)
                if not (same(ref, got) and calls_ok):
                    bad.append((backend, nocf, k))
    check("suspend/resume output byte-identical to a straight run (incl. seam tags)", not bad, bad)


# ------------------------------------------------------------------ 1: crossfade color_match reference
DRIFT_TEX = np.random.default_rng(0).normal(0, 25, (32, 64, 3))


def _lvl(level):
    return Image.fromarray(np.clip(DRIFT_TEX + level, 0, 255).astype(np.uint8))


def _mean(im):
    return float(np.asarray(im, np.float32).mean())


def _drift_gen(self, cond, seed, prompt, neg, step_cb, is_cont):
    """Every emitted frame (head included, like a VAE round-trip) is +10 brighter than its condition."""
    n = self.seg_frames
    if cond[0] == "first":
        return [_lvl(100.0) for _ in range(n)]
    tail = cond[1]
    return [_lvl(_mean(t) + 10.0) for t in tail] + [_lvl(_mean(tail[-1]) + 10.0) for _ in range(n - len(tail))]


@section
def bug1_crossfade_color_match():
    base = ["--prompt", "x", "--total", "20", "--seg", "3", "--backend", "ltx"]
    run(base, identity_cm=False, gen=_drift_gen)
    v = H.exported
    cf = _mean(v[-1]) - _mean(v[0])
    run(base + ["--no_crossfade"], identity_cm=False, gen=_drift_gen)
    ncf = _mean(H.exported[-1]) - _mean(H.exported[0])
    check("#1 default crossfade path holds brightness drift (end-start %.1f; was ~+58)" % cf, abs(cf) < 6, cf)
    check("#1 crossfade drift matches --no_crossfade (%.1f vs %.1f)" % (cf, ncf), abs(cf - ncf) < 3, (cf, ncf))
    seam_jumps = [abs(_mean(v[i + 1]) - _mean(v[i])) for i in range(len(v) - 1)]
    check("#1 no brightness step at any seam (max frame-to-frame %.2f)" % max(seam_jumps), max(seam_jumps) < 3,
          max(seam_jumps))


# ------------------------------------------------------------------ 2: crash-safe checkpoint
def _ckpt_valid_rule(ck):
    """What studio_core.Job._ckpt_valid must accept: state.json + at least n_frames *.png."""
    st = json.load(open(os.path.join(ck, "state.json")))
    return len(glob.glob(os.path.join(ck, "frames", "*.png"))) >= int(st["n_frames"])


@section
def bug2_crash_safe_checkpoint():
    base = ["--prompt", "x", "--total", "12", "--seg", "3", "--backend", "ltx"]
    run(base)
    ref = arrs(H.exported)
    # a clean run leaves exactly n_frames PNGs and no staging files (old strict count check still holds)
    ck = tempfile.mkdtemp(dir=TMP)
    run(base + ["--ckpt_dir", ck])
    st = json.load(open(os.path.join(ck, "state.json")))
    fr_dir = os.path.join(ck, "frames")
    check("#2 clean run: #PNG == n_frames, no *.new/*.tmp left",
          len(glob.glob(fr_dir + "/*.png")) == st["n_frames"] and not glob.glob(fr_dir + "/*.new")
          and not glob.glob(fr_dir + "/*.tmp"), sorted(os.listdir(fr_dir))[-3:])
    shutil.rmtree(ck)

    orig_wc = director.write_checkpoint
    real_save, real_replace = Image.Image.save, os.replace
    bad = []
    # (kind, n): kill after n PNG saves inside shot 3's checkpoint, or after n seam renames (post-commit)
    for kind, n in [("save", 0), ("save", 5), ("save", 18), ("save", 19), ("save", 25), ("save", 82),
                    ("rename", 0), ("rename", 1), ("rename", 10), ("rename", 18)]:
        ck = tempfile.mkdtemp(dir=TMP)
        cnt = {"s": 0, "r": 0}

        def save(self, fp, *a, **k):
            cnt["s"] += 1
            if kind == "save" and cnt["s"] > n:
                raise KillSim()
            return real_save(self, fp, *a, **k)

        def replace(src, dst, *a, **k):
            if str(src).endswith(".new"):
                cnt["r"] += 1
                if kind == "rename" and cnt["r"] > n:
                    raise KillSim()
            return real_replace(src, dst, *a, **k)

        def wc(ckpt_dir, seg_idx, video, *a, **k):
            if seg_idx != 3:
                return orig_wc(ckpt_dir, seg_idx, video, *a, **k)
            Image.Image.save, os.replace = save, replace
            try:
                return orig_wc(ckpt_dir, seg_idx, video, *a, **k)
            finally:
                Image.Image.save, os.replace = real_save, real_replace

        director.write_checkpoint = wc
        killed = False
        try:
            run(base + ["--ckpt_dir", ck])
        except KillSim:
            killed = True
        finally:
            director.write_checkpoint = orig_wc
        why = None
        if not killed:
            why = "not killed"
        elif not _ckpt_valid_rule(ck):
            why = "fewer PNGs than n_frames"
        else:
            try:
                rc, _ = run(base + ["--ckpt_dir", ck, "--resume", ck])
                if not same(arrs(H.exported), ref):
                    why = "resumed output differs (double crossfade / wrong frames)"
            except Exception as e:
                why = "resume raised %s: %s" % (type(e).__name__, str(e)[:90])
        if why:
            bad.append((kind, n, why))
        shutil.rmtree(ck, ignore_errors=True)
    check("#2 kill anywhere in a checkpoint write -> ckpt valid, resume byte-identical to a straight run",
          not bad, bad)


# ------------------------------------------------------------------ 3: numeric frame order past 10000
@section
def bug3_numeric_order():
    ck = tempfile.mkdtemp(dir=TMP)
    fd = os.path.join(ck, "frames")
    os.makedirs(fd)
    n = 10003
    for i in range(n + 1):                         # +1: an uncommitted extra PNG past n_frames
        fr(i, 0, 4, 4).save(os.path.join(fd, f"{i:04d}.png"))
    open(os.path.join(fd, "0005.png.99.new"), "wb").write(b"torn")   # uncommitted staging leftover
    json.dump({"schema": 1, "seg_idx": 7, "n_frames": n, "current_prompt": "p", "args": {"backend": "ltx"}},
              open(os.path.join(ck, "state.json"), "w"))
    try:
        video, seg_idx, prompt = director.load_checkpoint(ck, "ltx")
        idxs = [idx_of(f) for f in video]
        check("#3 load_checkpoint keeps timeline order past 10000 frames (and tolerates extras)",
              idxs == list(range(n)), [(i, v) for i, v in enumerate(idxs) if i != v][:3] or len(idxs))
    except Exception as e:
        check("#3 load_checkpoint keeps timeline order past 10000 frames (and tolerates extras)", False, e)
    shutil.rmtree(ck, ignore_errors=True)


# ------------------------------------------------------------------ 4: CPU consult never caps VRAM
class _Stop(Exception):
    pass


@contextlib.contextmanager
def stub_modules(**mods):
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _stop_module(name):
    m = types.ModuleType(name)

    def __getattr__(attr):
        raise _Stop(attr)
    m.__getattr__ = __getattr__
    return m


@section
def bug4_planner_cpu_no_cap():
    res = {}
    for dev in ("cpu", None):
        calls = []
        gb = types.ModuleType("gpu_budget")
        gb.cap_vram = lambda *a, **k: calls.append(1)
        old = os.environ.pop("CONSULT_DEVICE", None)
        if dev:
            os.environ["CONSULT_DEVICE"] = dev
        try:
            with stub_modules(gpu_budget=gb, transformers=_stop_module("transformers")):
                try:
                    runpy.run_path(os.path.join(SRC, "vlm_planner.py"), run_name="planner_under_test")
                except _Stop:
                    pass
        finally:
            os.environ.pop("CONSULT_DEVICE", None)
            if old is not None:
                os.environ["CONSULT_DEVICE"] = old
        res[dev] = len(calls)
    check("#4 CONSULT_DEVICE=cpu -> planner never calls cap_vram (no CUDA context)", res["cpu"] == 0, res)
    check("#4 GPU consult still caps VRAM once", res[None] == 1, res)


# ------------------------------------------------------------------ 5: HOLD static-skip survives resume
@section
def bug5_hold_skip_resume():
    sc = os.path.join(TMP, "fake_sidecar.sh")
    with open(sc, "w") as fh:
        fh.write('#!/bin/sh\nprev=""; seg=""\nwhile [ $# -gt 0 ]; do case "$1" in --prev) prev="$2"; shift;; '
                 '--seg) seg="$2"; shift;; esac; shift; done\necho "[[PLAN plan$seg]]"\necho "P$seg<-[$prev]"\n')
    os.chmod(sc, 0o755)
    env0 = {k: os.environ.get(k) for k in ("LTX_DIRECTOR_PY", "HF_HOME")}
    os.environ["LTX_DIRECTOR_PY"], os.environ["HF_HOME"] = sc, os.path.join(TMP, "no_hf")   # no daemon

    def static_gen(self, cond, seed, prompt, neg, step_cb, is_cont):     # a scene that holds perfectly
        n = self.seg_frames
        k = 0 if cond[0] == "first" else len(cond[1])
        return [Image.new("RGB", (64, 32), (100, 110, 120)) for _ in range(n)][(k if self.strips_cond_head else 0):]
    try:
        base = ["--prompt", "x", "--total", "24", "--seg", "3", "--backend", "ltx", "--vlm",
                "--steadiness", "hold", "--directive", "a distinct directive"]
        _, out0 = run(base, gen=static_gen)
        fresh = [c[1] for c in H.calls]
        ck = tempfile.mkdtemp(dir=TMP)
        _, out1 = run(base + ["--ckpt_dir", ck], hook=suspend_at(5), gen=static_gen)
        c1 = [c[1] for c in H.calls]
        _, out2 = run(base + ["--ckpt_dir", ck, "--resume", ck], gen=static_gen)
        resumed = c1 + [c[1] for c in H.calls]
        skipped = "(skipped - scene holding steady" in out0
        check("#5 fresh HOLD run skips the director on a static seam (precondition)", skipped, out0[-300:])
        check("#5 resumed HOLD run keeps the static-skip (same prompts as a straight run)", resumed == fresh,
              (fresh, resumed))
        shutil.rmtree(ck, ignore_errors=True)
    finally:
        for k, v in env0.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ------------------------------------------------------------------ 6: palette pool survives resume
def _noise_gen(self, cond, seed, prompt, neg, step_cb, is_cont):
    """Content keyed on the shot seed only (palette_lock rewrites pixels, so no index-in-pixel here)."""
    n = self.seg_frames
    frames = [Image.fromarray(np.random.default_rng(seed * 1000 + i).integers(0, 256, (32, 64, 3), dtype=np.uint8))
              for i in range(n)]
    k = len(cond[1]) if cond[0] == "tail" and self.strips_cond_head else 0
    return frames[k:]


@section
def bug6_palette_pool_resume():
    bad = []
    for backend in ("wan", "ltx"):
        base = ["--prompt", "x", "--total", "12", "--seg", "3", "--backend", backend, "--palette_lock", "1.0"]
        ref, got, calls_ok = resume_equiv(base, 3, gen=_noise_gen, identity_cm=False)
        if not (same(ref, got) and calls_ok):
            nd = sum(not np.array_equal(a, b) for a, b in zip(ref, got))
            bad.append((backend, "differing frames", nd, "calls_ok", calls_ok))
    check("#6 --palette_lock resume byte-identical to a straight run (pool + refreshes restored)", not bad, bad)


# ------------------------------------------------------------------ 7: early SIGUSR1
@section
def bug7_early_sigusr1():
    # Deliver SIGUSR1 at the moment director.py starts importing torch (deterministic "first seconds"),
    # then stop the process once the module's imports are done and report the recorded request.
    code = r'''
import os, sys, signal, runpy
class Hook:
    fired = False
    def find_spec(self, name, path=None, target=None):
        if name == "torch" and not Hook.fired:
            Hook.fired = True
            os.kill(os.getpid(), signal.SIGUSR1)
        if name == "style_presets":
            g = sys.modules["__main__"].__dict__
            print("EARLY_FLAG=%r" % (g.get("_SUSPEND"),), flush=True)
            os._exit(0)
        return None
sys.meta_path.insert(0, Hook())
sys.argv = ["director.py", "--prompt", "x"]
sys.path.insert(0, SRC_DIR)
runpy.run_path(DIRECTOR_PY, run_name="__main__")
'''.replace("SRC_DIR", repr(SRC)).replace("DIRECTOR_PY", repr(os.path.join(SRC, "director.py")))
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1")
    p = subprocess.run([sys.executable, "-c", code], cwd=SRC, env=env, capture_output=True, text=True, timeout=120)
    check("#7 SIGUSR1 during the heavy imports does not kill the worker", p.returncode == 0,
          "rc=%s %s" % (p.returncode, (p.stderr or "")[-200:]))
    check("#7 the early suspend request is recorded (not reset by module init)", "EARLY_FLAG=True" in p.stdout,
          p.stdout[-200:])
    # ...and honored once the loop starts: shot 1 renders + checkpoints, then a clean suspend (exit 99)
    ck = tempfile.mkdtemp(dir=TMP)
    H.calls.clear()
    director._SUSPEND = True                     # as if the signal landed before main() ran
    buf, rc = io.StringIO(), 0
    sys.argv = ["director.py", "--width", "64", "--height", "32", "--out", os.path.join(TMP, "out", "e.mp4"),
                "--prompt", "x", "--total", "12", "--seg", "3", "--backend", "ltx", "--ckpt_dir", ck]
    try:
        with contextlib.redirect_stdout(buf):
            try:
                director.main()
            except SystemExit as e:
                rc = e.code
    finally:
        director._SUSPEND = False
    st = json.load(open(os.path.join(ck, "state.json")))
    check("#7 an early request is honored at the first seam (shot 1 checkpointed, exit 99)",
          rc == 99 and len(H.calls) == 1 and st["seg_idx"] == 1 and "[[SUSPENDED" in buf.getvalue(),
          (rc, len(H.calls), st.get("seg_idx")))
    shutil.rmtree(ck, ignore_errors=True)


# ------------------------------------------------------------------ 8/9/11: run_ltx.py (stubbed diffusers)
def run_run_ltx(argv, fail_export=False):
    """Execute run_ltx.py with a fake diffusers -> (pipe kwargs, export paths, out path, rc/exc)."""
    rec = types.SimpleNamespace(kw=None, exports=[])

    class FakePipe:
        vae = types.SimpleNamespace(enable_tiling=lambda: None)

        @classmethod
        def from_pretrained(cls, *a, **k):
            return cls()

        def enable_sequential_cpu_offload(self):
            pass

        def enable_model_cpu_offload(self):
            pass

        def __call__(self, **kw):
            rec.kw = kw
            return types.SimpleNamespace(frames=[[Image.new("RGB", (kw["width"], kw["height"]))] * kw["num_frames"]])

    def export(frames, path, fps=None):
        rec.exports.append(path)
        with open(path, "wb") as fh:
            fh.write(b"partial" if fail_export else b"MP4")
        if fail_export:
            raise KillSim()
        return path
    dif = types.ModuleType("diffusers")
    dif.LTXPipeline = dif.LTXImageToVideoPipeline = FakePipe
    du = types.ModuleType("diffusers.utils")
    du.export_to_video, du.load_image = export, (lambda p: Image.new("RGB", (8, 8)))
    dif.utils = du
    out = os.path.join(TMP, "rl", "clip.mp4")
    sys.argv = ["run_ltx.py", "--prompt", "p", "--ltx_repo", "Lightricks/LTX-Video", "--width", "64",
                "--height", "32", "--steps", "2", "--out", out] + list(argv)
    err = None
    with stub_modules(diffusers=dif, **{"diffusers.utils": du}), contextlib.redirect_stdout(io.StringIO()):
        try:
            runpy.run_path(os.path.join(SRC, "run_ltx.py"), run_name="__main__")
        except BaseException as e:            # noqa: BLE001
            err = e
    return rec, out, err


@section
def bug8_frame_rate():
    rec, _, err = run_run_ltx([])
    check("#8 run_ltx default fps 24 -> no frame_rate kwarg (default output unchanged)",
          err is None and rec.kw is not None and "frame_rate" not in rec.kw, (err, rec.kw and sorted(rec.kw)))
    rec, _, err = run_run_ltx(["--fps", "30"])
    check("#8 run_ltx --fps 30 reaches the pipeline as frame_rate=30",
          err is None and rec.kw is not None and rec.kw.get("frame_rate") == 30, (err, rec.kw and rec.kw.get("frame_rate")))
    # director's LTX backend: call gen() against a pipe that records its kwargs
    for fps, want in ((24, None), (30, 30), (12, 12)):
        seen = {}
        a = types.SimpleNamespace(latent_chain=False, cfg_rescale=0.0, steps=2, cfg=3.0, fps=fps)
        b = director.LTXBackend(a)
        b.W, b.H, b.seg_frames, b._decode_kwargs = 64, 32, 9, {}
        b.pipe = lambda **kw: (seen.update(kw), types.SimpleNamespace(frames=[[None]]))[1]
        b.gen(None, 0, "p", "n", None, False)
        check("#8 director LTX --fps %d -> frame_rate %s" % (fps, want), seen.get("frame_rate") == want,
              seen.get("frame_rate"))


@section
def bug9_nine_frame_floor():
    rec, _, err = run_run_ltx(["--seconds", "0.3"])
    check("#9 run_ltx 0.3s@24fps -> 9 frames (was 1)", err is None and rec.kw and rec.kw["num_frames"] == 9,
          (err, rec.kw and rec.kw["num_frames"]))
    rec, _, err = run_run_ltx(["--seconds", "5"])
    check("#9 run_ltx 5s@24fps unchanged (121 frames)", err is None and rec.kw and rec.kw["num_frames"] == 121,
          rec.kw and rec.kw["num_frames"])
    for backend in ("ltx", "wan"):
        run(["--prompt", "x", "--total", "0.3", "--seg", "3", "--backend", backend])
        n = len(H.exported)
        check("#9 director %s --total 0.3 -> 9 frames (was %s)" % (backend, "1" if backend == "ltx" else "5"),
              n == 9 and [idx_of(f) for f in H.exported] == list(range(9)), n)


@section
def bug11_atomic_export():
    rec, out, err = run_run_ltx([])
    p = rec.exports[0] if rec.exports else ""
    check("#11 run_ltx encodes to a temp .mp4 in the out dir, then renames",
          err is None and p != out and os.path.dirname(p) == os.path.dirname(out) and p.endswith(".mp4")
          and open(out, "rb").read() == b"MP4" and not os.path.exists(p), (err, p))
    os.remove(out)
    rec, out, err = run_run_ltx([], fail_export=True)
    check("#11 run_ltx killed mid-encode -> no truncated mp4 at --out",
          isinstance(err, KillSim) and not os.path.exists(out), (err, os.path.exists(out)))
    EXPORT.paths.clear()
    run(["--prompt", "x", "--total", "3", "--seg", "3", "--backend", "ltx"])
    out = os.path.join(TMP, "out", "x.mp4")
    p = EXPORT.paths[-1] if EXPORT.paths else ""
    check("#11 director encodes to a temp .mp4 in the out dir, then renames",
          p != out and os.path.dirname(p) == os.path.dirname(out) and p.endswith(".mp4")
          and open(out, "rb").read().startswith(b"MP4:") and not os.path.exists(p), p)
    with open(out, "wb") as fh:
        fh.write(b"PREVIOUS")
    EXPORT.fail = True
    try:
        run(["--prompt", "x", "--total", "3", "--seg", "3", "--backend", "ltx"])
        killed = False
    except KillSim:
        killed = True
    finally:
        EXPORT.fail = False
    leftovers = [f for f in os.listdir(os.path.dirname(out)) if f != "x.mp4"]
    check("#11 director killed mid-encode -> --out untouched, temp removed",
          killed and open(out, "rb").read() == b"PREVIOUS" and not leftovers, (killed, leftovers))


# ------------------------------------------------------------------ 10: latent_fuse seam alignment
@section
def bug10_latent_fuse_alignment():
    import diffusers.pipelines.ltx.pipeline_ltx_condition as ltxmod
    C = 4

    class FakeCondPipe:
        vae_temporal_compression_ratio, vae_spatial_compression_ratio = 8, 32

        def __init__(self):
            self.vae = types.SimpleNamespace(enable_tiling=lambda: None,
                                             latents_mean=torch.zeros(C), latents_std=torch.ones(C))

        @classmethod
        def from_pretrained(cls, *a, **k):
            return cls()

        def enable_sequential_cpu_offload(self):
            pass

        @staticmethod
        def _denormalize_latents(latents, latents_mean, latents_std, scaling_factor=1.0):
            return latents

    a = types.SimpleNamespace(ltx_repo="Lightricks/LTX-Video", ltx_variant=None, ltx_no_mush_patch=True,
                              latent_chain=True, latent_adain=0.0, latent_fuse=True, steps=4, cfg=3.0,
                              cfg_rescale=0.0, _cfg_interval=None, fps=24)
    orig_pipe_cls, orig_retrieve = director.LTXConditionPipeline, ltxmod.retrieve_latents
    director.LTXConditionPipeline = FakeCondPipe
    try:
        b = director.LTXBackend(a)
        b.configure(64, 32, 73, 17)                    # overlap 17 px -> 3 injected latent frames, fuse K=2
        with contextlib.redirect_stdout(io.StringIO()):
            b.load()
        F = 10
        carry = torch.arange(F, dtype=torch.float32).view(1, 1, F, 1, 1).expand(1, C, F, 1, 1).clone() + 100
        b._carry, b._lat_inj["lat"] = carry, carry
        Kc = (17 - 1) // 8 + 1
        enc = types.SimpleNamespace(latent_dist=types.SimpleNamespace(mode=lambda: torch.zeros(1, C, Kc, 1, 1)))
        inj = ltxmod.retrieve_latents(enc)             # the continuation's conditioning = carry[-Kc:]
        check("#10 precondition: injected conditioning is carry[-3:]", torch.equal(inj, carry[:, :, -Kc:]),
              inj[0, 0, :, 0, 0].tolist())
        out = torch.zeros(1, C, 10, 1, 1)
        out[:, :, :Kc] = carry[:, :, -Kc:]            # the shot's head reproduces the injected tail ...
        out[:, :, :Kc] += 0.5                           # ... approximately
        fused = b.pipe._denormalize_latents(out.clone(), None, None)
        K = 2
        want = director.linear_overlap_fuse(carry[:, :, F - Kc:F - Kc + K], out, K)
        check("#10 latent_fuse blends each leading frame with the carry frame it continues (not its successor)",
              torch.allclose(fused, want), (fused[0, 0, :4, 0, 0].tolist(), want[0, 0, :4, 0, 0].tolist()))
        check("#10 fuse keeps the latent frame count", fused.shape == out.shape, fused.shape)
    finally:
        director.LTXConditionPipeline = orig_pipe_cls
        ltxmod.retrieve_latents = orig_retrieve


# ------------------------------------------------------------------ 12: markdown-bold labels
@section
def bug12_markdown_labels():
    tr = types.ModuleType("transformers")
    tr.Qwen3VLForConditionalGeneration = tr.AutoProcessor = object
    qv = types.ModuleType("qwen_vl_utils")
    qv.process_vision_info = lambda m: (None, None)
    gb = types.ModuleType("gpu_budget")
    gb.cap_vram = lambda *a, **k: None
    sys.argv = ["vlm_director7b.py"]
    with stub_modules(transformers=tr, qwen_vl_utils=qv, gpu_budget=gb):
        g = runpy.run_path(os.path.join(SRC, "vlm_director7b.py"), run_name="director7b_under_test")
    ep = g["extract_prompt"]
    cases = [
        ("PLAN: keep it.\nPROMPT: A red fox in snow, oil painting", ("A red fox in snow, oil painting", "keep it.")),
        ("**PLAN:** keep it.\n**PROMPT:** A red fox in snow, oil painting",
         ("A red fox in snow, oil painting", "keep it.")),
        ("**PLAN**: keep it.\n**PROMPT**: A red fox", ("A red fox", "keep it.")),
        ("**PLAN:** keep it. **PROMPT:** A red fox", ("A red fox", "keep it.")),
        ("PLAN: x\nPROMPT: \"A red fox\"", ("A red fox", "x")),
        ("A red fox walking in snow", ("A red fox walking in snow", "A red fox walking in snow")),
        ("PLAN: fox walks\n\nPROMPT:\nA red fox walks", ("A red fox walks", "fox walks")),
    ]
    bad = [(c, ep(c), want) for c, want in cases if ep(c) != want]
    check("#12 PLAN/PROMPT parsed with or without markdown bold (plain responses unchanged)", not bad, bad)
    keep = g.get("is_keep")
    kc = ["KEEP", "**KEEP**", "PLAN: fits\nPROMPT: KEEP", "**PLAN:** fits\n**PROMPT:** KEEP", "PROMPT: **KEEP**"]
    nk = ["PLAN: fox\nPROMPT: A fox keeps running", "**PROMPT:** A red fox"]
    check("#12 KEEP detected with markdown bold, not in ordinary prompts",
          keep is not None and all(keep(c) for c in kc) and not any(keep(c) for c in nk),
          keep and [(c, keep(c)) for c in kc + nk])


print("RESULT:", "PASS" if ok else "FAIL", "(%.1fs)" % (time.time() - T0))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
