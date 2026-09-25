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
        # a pause: the MANAGER shifts the job's shot/phase baselines (before SIGCONT, race-free); the UI's
        # later tick must not shift them again -- only its own intra-step latch
        j2 = mkjob(3); j2.phase, j2.seg, j2.step = "generating", 2, 10
        t0 = time.time()
        j2.seg_started = t0 - 100; j2.phase_started = t0 - 50; j2.first_step_ts = t0 - 400
        ACTIVE["job"] = j2
        app.tick()
        ACTIVE["paused"] = True; app.tick()
        app._paused_since = (j2.id, app._paused_since[1] - 600)   # pretend the pause lasted 10 minutes
        wall0 = app._smart_step_wall = time.time() - 700
        ACTIVE["paused"] = False; app.tick()
        check("the UI leaves the job's timing to the manager on resume", abs(j2.seg_started - (t0 - 100)) < 1, j2.seg_started - (t0 - 100))
        check("the UI shifts its own step latch past the pause", app._smart_step_wall is None or app._smart_step_wall - wall0 >= 590,
              app._smart_step_wall and app._smart_step_wall - wall0)
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
        # a standby DURING a pause: the manager's resume shift spans the whole pause, so the UI's wake
        # handling must not shift the job clocks too (that double shift wrote negative times)
        js = mkjob(3); js.phase, js.seg, js.step = "generating", 2, 4
        t0 = time.time(); js.seg_started = t0 - 30; js.phase_started = t0 - 10
        ACTIVE["job"], ACTIVE["paused"] = js, True
        app.tick()
        app._last_tick_wall = time.time() - 300          # the VM slept 5 min while paused
        app.tick()
        check("standby while paused leaves the job clocks to the resume shift", abs(js.seg_started - (t0 - 30)) < 1,
              js.seg_started - (t0 - 30))
        check("...but still tallies the standby", getattr(js, "slept", 0) >= 290, getattr(js, "slept", 0))
        ACTIVE["paused"] = False; ACTIVE["job"] = None; app.tick()
        # a RESUMED leg reloading its model (job.seg = checkpointed shot 2 of 5, no [[SEG]] yet this leg)
        jr = mkjob(5); jr.phase, jr.seg, jr.step, jr.seg_started, jr.saw_step = "loading", 2, 0, None, False
        jr.phase_started = time.time(); jr.load_step, jr.load_total, jr.load_msg = 2, 5, "loading"
        check("resumed leg's reload keeps its done shots in the bar", app._smart_pct(jr) >= 30, app._smart_pct(jr))
        jr.phase = ""
        check("resumed leg says the NEXT shot is starting", "Starting shot 3 of 5" in app._phase_text(jr), app._phase_text(jr))
        jr.phase = "loading"; app._smart_max_id = None
        ACTIVE["job"] = jr; app.tick(); await pilot.pause(0.05)
        pt = str(app.query_one("#progtext").render())
        check("resumed leg shows the load bar during its reload", "2/5" in pt, pt)
        ACTIVE["job"] = None; app.tick()
        # blind STEADINESS: evolve narrates a redirect every seam, hold every 3rd -> neutral text
        jb = mkjob(4); jb.phase, jb.seg = "redirecting", 2
        jb.params.update(pair_blind=True, pair_revealed=False, pair_varied_dial="steadiness")
        check("blind steadiness redirect narration is neutral", "plan shot" not in app._phase_text(jb), app._phase_text(jb))
        # ...and EVERY LIVE widget shows it exactly like the decode before it (phase line, progress
        # suffix, phase timeline, % bar) -- any one difference makes the redirects countable
        def live_view(ph, into):
            jb.phase, jb.phase_started, jb.status = ph, time.time() - into, "running"
            jb.phase_secs = {"decoding": 5.0} if ph == "decoding" else {"decoding": 5.0 + 0.0}
            ACTIVE["job"] = jb; app._smart_max_id = None; app.tick()
            return (app._phase_text(jb), str(app.query_one("#progtext").render()),
                    str(app.query_one("#ph_timeline").render()), app._smart_pct(jb))
        jb.step = jb.nstep = 25; jb.saw_step = True; jb.seg_started = time.time() - 60
        a_ = live_view("decoding", 10.0)
        b_ = live_view("redirecting", 10.0)
        check("blind steadiness: a redirect looks exactly like decoding in every LIVE widget", a_ == b_, (a_, b_))
        ACTIVE["job"] = None; app.tick()
        # the ~ms window after [[SEG N+1]] but before [[PHASE warmup]] reads as shot N+1's warmup
        jw = mkjob(5); jw.phase, jw.seg, jw.step, jw.nstep = "redirecting", 3, 25, 25
        jw.phase_started = time.time() - 30; jw.seg_started = time.time() - 0.001
        jw.params.update(pair_blind=True, pair_revealed=False, pair_varied_dial="steadiness")
        check("post-SEG redirect window is the next shot's warmup", studio._live_phase(jw) == "warmup", studio._live_phase(jw))
        check("...so the bar doesn't latch a whole shot ahead", app._smart_pct(jw) < 50, app._smart_pct(jw))
        # the same window after a DECODE (every seam without a redirect), timed from the SEG, not the decode
        jd = mkjob(5); jd.phase, jd.seg, jd.step, jd.nstep = "decoding", 3, 25, 25
        jd.phase_started = time.time() - 30; jd.seg_started = time.time() - 0.001
        fresh = mkjob(5); fresh.phase, fresh.seg, fresh.step, fresh.nstep = "warmup", 3, 0, 25
        fresh.phase_started = fresh.seg_started = time.time()
        check("post-SEG decode window = the next shot's warmup, just begun",
              studio._live_phase(jd) == "warmup" and abs(app._smart_pct(jd) - app._smart_pct(fresh)) <= 1,
              (studio._live_phase(jd), app._smart_pct(jd), app._smart_pct(fresh)))
        # blind steadiness: both variants are budgeted alike (hold's fewer redirects gave the ETA away)
        jh, je = mkjob(6), mkjob(6)
        for jj, st in ((jh, "hold"), (je, "evolve")):
            jj.params.update(mode="director", steadiness=st, directive="a storm builds",
                             pair_blind=True, pair_revealed=False, pair_varied_dial="steadiness")
        check("blind hold/evolve get the same time budget", app._run_budget(jh) == app._run_budget(je),
              (app._run_budget(jh)["gen"], app._run_budget(je)["gen"]))
        jh.params["pair_blind"] = je.params["pair_blind"] = False
        check("non-blind hold still budgets fewer redirects", app._run_budget(jh)["gen"] < app._run_budget(je)["gen"])
        # TIMING lists the director's redirect time (it was in the total but not a row); blind folds it
        jt = mkjob(3); jt.status, jt.started, jt.finished = "done", 1.0, 100.0
        jt.phase_secs = {"generating": 50.0, "decoding": 10.0, "redirecting": 30.0}
        prov = app._fmt_provenance(jt)
        check("TIMING lists director redirect time", "director" in prov and "33%" in prov, prov[-400:])
        jt.params.update(pair_blind=True, pair_revealed=False, pair_varied_dial="steadiness")
        prov = app._fmt_provenance(jt)
        check("blind-steadiness TIMING folds redirects into decode", "director " not in prov and "44%" in prov,
              [l for l in prov.splitlines() if "%" in l])
        jt.params["pair_blind"] = False
        jt.phase_secs = {"importing": 3.0, "loading": 20.0, "offload": 2.0, "generating": 30.0, "decoding": 10.0,
                         "redirecting": 5.0, "saving": 1.0}
        pc = [int(x) for x in __import__("re").findall(r"(\d+)%", app._fmt_provenance(jt))]
        check("TIMING percentages add up to ~100", 98 <= sum(pc) <= 102, pc)
        # stall sentry: a director redirect (a VLM call, legitimately minutes) gets the long fuse like a
        # decode -- 250 s into one must not raise STALL (the short 240 s fuse is for step-marked gen)
        jv = mkjob(5); jv.phase, jv.seg, jv.step = "redirecting", 2, 25
        ACTIVE["job"] = jv; app._stall_note = ""
        app._stall_state = {"sig": (jv.id, jv.phase, jv.seg, jv.step, 0), "pmt": 0.0,
                            "since": time.monotonic() - 250, "fired": False, "susp": False, "killed": False}
        app._alerts()
        check("250 s into a director redirect is not a STALL", "STALL" not in (app._stall_note or ""), app._stall_note)
        # ...nor is 1250 s, and a redirect is never auto-suspended / killed (director.py times out and
        # recovers its own waits: daemon load 900 s + request 600 s)
        calls = []
        app.mgr.suspend = lambda: calls.append("suspend"); app.mgr.hard_interrupt = lambda: calls.append("kill")
        for idle in (1250, 1600, 2500):
            app._stall_state = {"sig": (jv.id, jv.phase, jv.seg, jv.step, 0), "pmt": 0.0,
                                "since": time.monotonic() - idle, "fired": False, "susp": False, "killed": False}
            app._stall_note = ""
            app._alerts()
            if idle == 1250:
                check("1250 s into a redirect: no STALL banner yet", "STALL" not in (app._stall_note or ""), app._stall_note)
        check("a redirect is never auto-suspended or killed by the stall fuse", not calls, calls)
        check("...but a redirect wedged past 30 min is flagged", "STALL" in (app._stall_note or ""), app._stall_note)
        ACTIVE["job"] = None; app.tick()
        # suspending during a resumed leg's reload: no shot is finished first
        jr.status, jr.phase = "suspending", "loading"
        check("suspend during a resumed reload names no shot", "finishing shot" not in app._phase_text(jr), app._phase_text(jr))
        j0 = mkjob(5); j0.status, j0.phase, j0.seg, j0.seg_started = "suspending", "loading", 0, None
        check("suspend during a first load names shot 1 (not 0)", "shot 1 of 5" in app._phase_text(j0), app._phase_text(j0))
        jr.status = "running"
        # ETA during a resumed leg's reload: no phantom in-flight shot
        jr.seg_secs = [100, 100]
        check("resumed reload ETA = the remaining shots only", app._time_left(jr) == 300, app._time_left(jr))
        # a resumed leg's import window (no marker yet) already shows the load bar
        jr.phase, jr.load_step, jr.load_total = "", 0, 0
        ACTIVE["job"] = jr; app._smart_max_id = None; app.tick(); await pilot.pause(0.05)
        pt = str(app.query_one("#progtext").render())
        check("resumed leg's import window shows the load bar, not 'shot 2 of 5'", "shot 2 of 5" not in pt, pt)
        ACTIVE["job"] = None; app.tick()
        # resuming from a final-shot checkpoint never says "shot 6 of 5"
        jf = mkjob(5); jf.phase, jf.seg, jf.seg_started, jf.saw_step = "", 5, None, False
        check("final-checkpoint resume doesn't claim to start a shot", "Starting shot" not in app._phase_text(jf), app._phase_text(jf))
        jf.phase, jf.seg, jf.seg_started, jf.step = "warmup", 6, time.time(), 0     # after [[SEG 6 5]]
        ACTIVE["job"] = jf; app._smart_max_id = None; app.tick(); await pilot.pause(0.05)
        pt = str(app.query_one("#progtext").render())
        check("final-checkpoint resume never shows shot 6 of 5", "6 of 5" not in pt, pt)
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
