"""build() command matrix — the highest-value contract in the studio: form snapshot -> engine argv.
Uses the headless pilot with a stubbed JobManager and full `over` snapshots (no widget mutation)."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

class FakeMgr:
    def __init__(self): self.jobs, self.paused, self.vram_reserve_gb = {}, False, 1.0
    def queued(self): return []
    def suspended(self): return []
    def archived(self): return []
    def active(self): return None
    def counts(self): return (0, 0, 0, 0)
    def enqueue(self, *a): raise AssertionError("a bad form must never reach enqueue")
studio.JobManager = FakeMgr
studio.save_studio_config = lambda cfg: None
studio.load_studio_config = lambda: {}

BASE = {"mode": "single", "prompt": "a test scene", "directive": "", "anchors": "", "image": "",
        "seconds": "2", "seg": "3", "steps": "25", "cfg": "5.0", "seed": "123", "fps": "24",
        "n_prompt": "bad", "backend": "ltx", "cond_strength": "1.0", "cfg_rescale": "off",
        "cfg_interval": "off", "wan_ref_anchor": "off", "steadiness": "hold",
        "res": "704 x 480  balanced", "name": ""}

def arg(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None

import re
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def accepted_flags(script):
    """--flags the worker script's argparse declares (static read: the scripts import torch)."""
    return set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', open(os.path.join(REPO, script)).read()))
def unknown_flags(cmd):
    return sorted(f for f in cmd[2:] if f.startswith("--") and f not in accepted_flags(cmd[1]))

async def main():
    app = studio.Studio()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause()
        # 1. short LTX single -> run_ltx.py, seed passthrough, no CFG-surgery flags
        _t, kind, cmd, p = app.build(dict(BASE))
        check("short ltx single routes to run_ltx.py", "run_ltx.py" in cmd[1], cmd[1])
        check("fixed seed passes through", arg(cmd, "--seed") == "123")
        check("off omits cfg surgery", "--cfg_interval" not in cmd and "--cfg_rescale" not in cmd)
        # 2. blank seed -> concrete random seed, recorded
        _t, _k, cmd2, p2 = app.build(dict(BASE, seed=""))
        s = arg(cmd2, "--seed")
        check("blank seed -> concrete random int", s and s.isdigit() and s == str(p2.get("seed", s)), s)
        # 3. wan routes to director.py, forces 16 fps, upscales sub-480p dims
        _t, _k, cmd3, _p = app.build(dict(BASE, backend="wan", res="512 x 320  fast"))
        check("wan routes to director.py", "director.py" in cmd3[1])
        check("wan forces 16 fps", arg(cmd3, "--fps") == "16")
        check("wan upscales short side to >=480", int(arg(cmd3, "--height")) >= 480, cmd3)
        # 4. guidance schedule variants reach the command verbatim — WAN ONLY (LTX's batched CFG
        #    forward is incompatible with per-step gating; it crashed two real runs on 2026-07-06)
        _t, _k, cmd4, _p = app.build(dict(BASE, backend="wan", cfg="5.5", cfg_interval="2"))
        check("every-2nd schedule ships on wan", arg(cmd4, "--cfg_interval") == "2")
        _t, _k, cmd4b, _p = app.build(dict(BASE, backend="ltx", cfg="5.5", cfg_interval="0.0:0.5"))
        check("ltx OMITS the schedule flag even when set", "--cfg_interval" not in cmd4b)
        _t, _k, cmd5, _p = app.build(dict(BASE, backend="wan", cfg="5.5", cfg_interval="0.3:1.0"))
        check("late-range schedule ships", arg(cmd5, "--cfg_interval") == "0.3:1.0")
        # 5. wan-turbo clamps steps at the source
        _t, _k, cmd6, _p = app.build(dict(BASE, backend="wan-turbo", steps="40"))
        check("wan-turbo clamps steps <= 8", int(arg(cmd6, "--steps")) <= 8, arg(cmd6, "--steps"))
        # 6. director mode ships the VLM trio; single ships none
        long = dict(BASE, mode="director", seconds="8", directive="a storm builds", steadiness="evolve")
        _t, _k, cmd7, _p = app.build(long)
        check("director ships --vlm/--directive/--steadiness",
              "--vlm" in cmd7 and arg(cmd7, "--directive") == "a storm builds"
              and arg(cmd7, "--steadiness") == "evolve")
        check("single ships no VLM flags", "--vlm" not in cmd and "--steadiness" not in cmd)
        # 7. same-second collision safety: distinct outputs for identical blank names
        o1, o2 = arg(cmd, "--out"), arg(cmd2, "--out")
        check("unique output slugs for same-second builds", o1 != o2, (o1, o2))
        # 8. ANCHORS reach the worker on EVERY path (single clips used to drop them silently)
        _t, _k, cmd8, p8 = app.build(dict(BASE, anchors="watercolor, muted palette"))
        check("single ltx ships --anchors", "run_ltx.py" in cmd8[1] and arg(cmd8, "--anchors") == "watercolor, muted palette", cmd8)
        check("blank anchors ship no flag (byte-identical argv)", "--anchors" not in cmd)
        _t, _k, cmd9, _p = app.build(dict(BASE, seconds="8", anchors="noir"))
        check("chained ltx ships --anchors", "director.py" in cmd9[1] and arg(cmd9, "--anchors") == "noir", cmd9)
        # 10. STEPS / GUIDANCE typos fail in build() (the caller shows a message) instead of crashing
        #     Job() (int('30.0')) and with it the whole app, or exiting the worker in argparse
        check("steps '30.0' normalizes to 30", arg(app.build(dict(BASE, steps="30.0"))[2], "--steps") == "30")
        for bad in (dict(steps="30.5"), dict(steps="abc"), dict(steps="0"), dict(cfg="x"), dict(cond_strength="y")):
            try:
                app.build(dict(BASE, **bad)); raised = False
            except ValueError:
                raised = True
            check(f"build rejects {bad}", raised)
        app._queue_current_run(dict(BASE, steps="30.5")); await pilot.pause()
        check("QUEUE with bad steps leaves the app running", app.is_running)
        # 9. every flag build() emits is one the target script's argparse accepts (else: instant crash)
        for name, c in (("single", cmd8), ("chained", cmd9), ("wan", cmd3), ("wan cfg", cmd4),
                        ("turbo", cmd6), ("director", cmd7),
                        ("single distilled", app.build(dict(BASE, _ltx_variant="distilled", anchors="x"))[2]),
                        ("single i2v", app.build(dict(BASE, image="in.png"))[2]),
                        ("single rescale", app.build(dict(BASE, cfg="5.0", cfg_rescale="0.7"))[2]),
                        ("chained rescale", app.build(dict(BASE, seconds="8", cfg="5.0", cfg_rescale="0.7"))[2])):
            check(f"{name}: argv flags all accepted by {os.path.basename(c[1])}", not unknown_flags(c), unknown_flags(c))

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
