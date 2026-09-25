"""Progress / ETA regressions: the smart % bar advances across a multi-shot run instead of jumping to
~89% at shot 1's decode (warm/gen/decode repeat per shot), and a PAUSE is taken out of the ETA."""
import sys, os, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STUDIO_NO_ANIM"] = "1"
import studio, studio_core

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

ACTIVE = {"job": None, "paused": False}
class FakeMgr:
    def __init__(self): self.jobs, self.vram_reserve_gb = {}, 1.0
    paused = property(lambda self: ACTIVE["paused"])
    def queued(self): return []
    def suspended(self): return []
    def archived(self): return []
    def active(self): return ACTIVE["job"]
    def counts(self): return (0, 1 if ACTIVE["job"] else 0, 0, 0)
    def enhance_children(self, jid): return []
    def shutdown(self): pass
studio.JobManager = FakeMgr
studio.save_studio_config = lambda cfg: None
studio.load_studio_config = lambda: {}

def mkjob(nseg):
    p = {"backend": "wan", "res": "704 x 480  balanced", "steps": "25", "nseg": nseg, "seg_frames": 53,
         "mode": "chained", "prompt": "p"}
    j = studio_core.Job("j1", "t", "chained", [], p)
    j.status, j.started = "running", time.time()
    return j

async def main():
    app = studio.Studio()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause()
        j = mkjob(3)
        seq = []
        now = time.time()
        def at(phase, seg, step=0, into=0.0):
            j.phase, j.seg, j.step = phase, seg, step
            j.phase_started = time.time() - into
            j.saw_step = step > 0 or seg > 1
            app._smart_step_wall = time.time()
            seq.append((phase, seg, step, app._smart_pct(j)))
        at("loading", 0, into=10)
        for shot in (1, 2, 3):
            at("warmup", shot, into=30)
            for st in (1, 12, 24):
                at("generating", shot, st)
            at("decoding", shot, 25, into=5)
        at("saving", 3, 25, into=1)
        pcts = [x[-1] for x in seq]
        check("smart % never goes backwards across shots", all(b >= a for a, b in zip(pcts, pcts[1:])), seq)
        dec1 = next(x[-1] for x in seq if x[0] == "decoding" and x[1] == 1)
        check("shot 1 of 3 decode is ~a third, not ~89%", 20 <= dec1 <= 45, dec1)
        check("final save < 100 until done", pcts[-1] < 100 and pcts[-1] >= 85, pcts[-1])
        # single-shot runs keep the old whole-run behavior
        j1 = mkjob(1); j1.phase, j1.seg, j1.step, j1.phase_started = "decoding", 1, 25, time.time()
        check("single-shot decode still late in the bar", app._smart_pct(j1) >= 60, app._smart_pct(j1))
        # a pause shifts the time-left baselines at resume
        j2 = mkjob(3); j2.phase, j2.seg, j2.step = "generating", 2, 10
        t0 = time.time()
        j2.seg_started = t0 - 100; j2.phase_started = t0 - 50; j2.first_step_ts = t0 - 400
        ACTIVE["job"] = j2
        app.tick()
        ACTIVE["paused"] = True; app.tick()
        app._paused_since = (j2.id, app._paused_since[1] - 600)   # pretend the pause lasted 10 minutes
        ACTIVE["paused"] = False; app.tick()
        check("resume shifts seg_started by the paused span", abs((j2.seg_started - (t0 - 100)) - 600) < 5, j2.seg_started - (t0 - 100))
        check("a pause is not tallied as standby", not getattr(j2, "slept", 0.0), getattr(j2, "slept", None))
        ACTIVE["job"] = None
        # LIVE log: once the 300-line tail ring is full, a repeated identical line still shows up
        from textual.widgets import RichLog
        jl = mkjob(1); jl.phase, jl.seg = "generating", 1
        jl.tail = [f"line {i}" for i in range(300)]; jl.tail_count = 300
        log = app.query_one("#livelog", RichLog)
        written = []
        real_write = log.write
        log.write = lambda content, *a, **k: (written.append(str(content)), real_write(content, *a, **k))[1]
        ACTIVE["job"] = jl; app.tick(); await pilot.pause(0.1)
        n0 = len(written)
        for ln in ("warning: same", "warning: same", "after"):   # one tick per line: the 2nd line equals
            jl.tail = (jl.tail + [ln])[-300:]; jl.tail_count += 1   # the last one written (the old anchor)
            app.tick(); await pilot.pause(0.05)
        check("LIVE log keeps repeated lines past a full ring", written[n0:] == ["warning: same", "warning: same", "after"], written[n0:])
        log.write = real_write
        ACTIVE["job"] = None; app.tick()
        # director redirect cadence in the budget matches director.py (every 3rd seam): 3 shots -> 0
        jd = mkjob(3); jd.params.update(mode="director", steadiness="hold", directive="storm")
        jc = mkjob(3)
        check("3-shot hold director budgets 0 redirects", app._run_budget(jd)["gen"] == app._run_budget(jc)["gen"])
        # enhance jobs are costed as enhance passes, not a 20-step diffusion run
        je = studio_core.Job("e", "e", "enhance", [], dict(mode="enhance", steps=0, nseg=1, seconds="20", fps="24",
                                                           enh_interp="2", enh_upscale="4", enh_face="gfpgan"))
        check("enhance queue budget uses the enhance calibration", abs(app._run_budget(je)["gen"] - 720) < 1, app._run_budget(je))
        # a RESTORE-only enhance plan is not "no passes selected"
        d = dict(interp="1", interp_engine="rife", upscale="0", upmodel="realesrgan", face="0",
                 deflicker="0", restore="seedvr2-3b", tile_feather="0", interp_skip="0")
        app.push_screen(studio.EnhanceOptsScreen(704, 480, 100, d)); await pilot.pause(0.3)
        plan = str(app.screen.query_one("#eopt_eta").render())
        check("restore-only enhance plan names the pass", "restore" in plan and "no passes" not in plan, plan)
        app.screen.dismiss(None); await pilot.pause(0.2)
        # the readout gets the steadiness the ENGINE runs (evolve + blank directive -> hold)
        from textual.widgets import Select, TextArea, Input
        seen = {}
        real_rr = studio.readout.render_readout
        studio.readout.render_readout = lambda cfg, *a, **k: (seen.update(cfg), real_rr(cfg, *a, **k))[1]
        try:
            app.query_one("#mode", Select).value = "director"; app.query_one("#seconds", Input).value = "12"
            await pilot.pause(0.2)
            app.query_one("#steadiness", Select).value = "evolve"
            app.query_one("#directive", TextArea).text = ""
            await pilot.pause(0.2); app.update_est()
            check("readout sees evolve+blank directive as hold", seen.get("steadiness") == "hold", seen.get("steadiness"))
        finally:
            studio.readout.render_readout = real_rr
        # a malformed schematic can't take the app down (the markup parse is guarded)
        real = studio.field_visuals.render
        studio.field_visuals.render = lambda *a, **k: "seed [/] oops"
        try:
            app._show_field_visual("seed"); await pilot.pause(0.1)
            check("bad schematic markup doesn't crash", app.is_running)
        finally:
            studio.field_visuals.render = real

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
