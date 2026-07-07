"""One headless pilot smoke over the whole TUI: boot, fixed right rail, mode gray-out, empty
states, a full LIVE tick with an active fake job, every curated theme applied, picker filter."""
import sys, os, time, types, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio
from textual.widgets import Select, Static, TabbedContent, OptionList
from textual.containers import VerticalScroll

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

now = time.time()
active = types.SimpleNamespace(
    id="jS", title="smoke", kind="chained", status="running",
    params={"prompt": "p", "res": "704 x 480  balanced", "steps": "25", "seed": "1",
            "backend": "wan", "mode": "single", "seg_frames": 53, "total_frames": 93, "nseg": 2,
            "seconds": "6", "width": 704, "height": 480, "cfg": "5.5", "fps": "16",
            "out": "outputs/x.mp4"},
    seg=2, nseg=2, step=7, nstep=25, out="outputs/x.mp4", director="", plans=[], dir_ms={}, dcfg={},
    phase="generating", phase_started=now - 60, phase_secs={}, seg_started=now - 300, seg_secs=[859],
    saw_step=True, first_step_ts=now - 900, first_step_seg=1, load_step=5, load_total=5,
    load_msg="", preview=None, tail=[], error=None, ckpt_dir=None, resumes=0)
active.elapsed = lambda: 1100
active.is_loading = lambda: False
active.pct = lambda: 50

state = {"active": None}
class FakeMgr:
    def __init__(self): self.jobs, self.paused, self.vram_reserve_gb = {}, False, 1.0
    def queued(self): return []
    def suspended(self): return []
    def archived(self): return []
    def active(self): return state["active"]
    def counts(self): return (0, 1 if state["active"] else 0, 0, 0)
studio.JobManager = FakeMgr
studio.save_studio_config = lambda cfg: None
studio.load_studio_config = lambda: {}
sound_calls = []
studio.sounds = types.SimpleNamespace(play=lambda *a: sound_calls.append(("play", a)),
                                      preview=lambda *a: sound_calls.append(("preview", a)) or "ok")

async def main():
    global ok
    app = studio.Studio()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause()
        # fixed rail: stacked same-width schematic/readout, scrollable info to the right, unmoving
        fv, ro, ip = app.query_one("#fieldvisual"), app.query_one("#readout"), app.query_one("#infopanel")
        check("rail: info is a VerticalScroll", isinstance(ip, VerticalScroll))
        check("rail: schematic+readout stacked same width",
              fv.region.width == ro.region.width and ro.region.y > fv.region.y)
        check("rail: info panel right + taller", ip.region.x > fv.region.x and ip.region.height > fv.region.height)
        before = (fv.region, ro.region, ip.region)
        app.query_one("#cfg").focus(); await pilot.pause()
        check("schematic title names the shown dial", "GUIDANCE" in str(fv.border_title), fv.border_title)
        app.query_one("#name").focus(); await pilot.pause()
        check("sticky schematic keeps its label on visual-less focus", "GUIDANCE" in str(fv.border_title))
        check("rail: boxes unmoving across focus", before == (fv.region, ro.region, ip.region))
        # mode gray-out
        for wid in ("directive", "steadiness", "seg"):
            check("single: #%s grayed" % wid, app.query_one("#" + wid).disabled is True)
        app.query_one("#mode", Select).value = "director"; await pilot.pause()
        check("director: #seg enabled", app.query_one("#seg").disabled is False)
        app.query_one("#mode", Select).value = "single"; await pilot.pause()
        # empty states
        check("queue empty-state row", app.query_one("#qtable").row_count == 1)
        check("archive empty-state row", app.query_one("#atable").row_count == 1)
        # LIVE tick with an active job: full live branch executes, real ETA, no crash
        state["active"] = active
        app.query_one(TabbedContent).active = "tab-live"; await pilot.pause()
        app.tick(); await pilot.pause()
        bar = str(app.query_one("#livebar", Static).render())
        check("live tick: real ETA in the livebar", "left" in bar and "~" in bar, bar[:80])
        state["active"] = None
        # every curated theme applies (stylesheet-crash guard)
        for t in studio.EXTRA_THEMES:
            try:
                app.theme = t.name; await pilot.pause()
                good = True
            except Exception:
                good = False
            check("theme applies: %s" % t.name, good)
        # picking a sound persists but NEVER auditions (no unexpected audio — user rule)
        sound_calls.clear()
        app.query_one("#snd_done", Select).value = "bell.wav"
        await pilot.pause()
        check("sound pick does NOT audition", sound_calls == [], sound_calls)
        # picker lists only the curated family
        scr = studio.ThemePickerScreen()
        app.push_screen(scr); await pilot.pause()
        names = [scr.query_one("#thlist", OptionList).get_option_at_index(i).id
                 for i in range(scr.query_one("#thlist", OptionList).option_count)]
        check("picker curated to pipboy family", names and all(n.startswith("pipboy") for n in names))
        await pilot.press("escape"); await pilot.pause()

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
