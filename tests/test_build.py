"""build() command matrix — the highest-value contract in the studio: form snapshot -> engine argv.
Uses the headless pilot with a stubbed JobManager and full `over` snapshots (no widget mutation)."""
import sys, os, types, asyncio
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

async def main():
    global ok
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
        # 4. guidance schedule variants reach the command verbatim
        _t, _k, cmd4, _p = app.build(dict(BASE, backend="wan", cfg="5.5", cfg_interval="2"))
        check("every-2nd schedule ships", arg(cmd4, "--cfg_interval") == "2")
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

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
