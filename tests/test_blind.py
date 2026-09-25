"""Blind A/B + archive regressions: a pair is queued whole or not at all, variants whose worker commands
are identical are refused (for blind A/B and PAIR), BALANCED/EVOLVE need a real directive, the inspect
view and archive table hide the varied value until REVEAL, back-to-back enhances of one run get
distinct outputs, "shots done" is honest, and restoring saved sound prefs at launch keeps the INFO intro."""
import sys, os, asyncio, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STUDIO_NO_ANIM"] = "1"
import studio, studio_core
from textual.widgets import Input, Select, TextArea

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

jobs = {}
removed = []
class FakeMgr:
    def __init__(self): self.jobs, self.paused, self.vram_reserve_gb = jobs, False, 1.0
    def queued(self): return [j for j in jobs.values() if j.status == "queued"]
    def suspended(self): return []
    def archived(self): return [j for j in jobs.values() if j.status == "done"]
    def active(self): return None
    def counts(self): return (len(self.queued()), 0, len(self.archived()), 0)
    def enhance_children(self, jid): return []
    def enqueue(self, title, kind, cmd, params):
        j = studio_core.Job(f"q{len(jobs)}", title, kind, cmd, params)   # real Job, never saved to disk
        j.save = lambda: None
        jobs[j.id] = j; return j
    def remove(self, jid):
        removed.append(jid); jobs.pop(jid, None); return "removed"
    def shutdown(self): pass
studio.JobManager = FakeMgr
studio.save_studio_config = lambda cfg: None
studio.load_studio_config = lambda: {"sounds": {"enabled": False}}
studio.gpu_budget.budget_ok = lambda *a, **k: (True, 99999)

async def main():
    app = studio.Studio()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause(0.4)
        # 0. launch restore of a saved "sound off" must not replace the INFO intro
        info = str(app.query_one("#newinfo").render())
        check("saved sound prefs don't clobber the INFO intro", "event sounds" not in info and "♪" not in info, info[:60])
        app.query_one("#prompt", TextArea).text = "a lighthouse at dusk"
        app.query_one("#seconds", Input).value = "2"      # one clip -> run_ltx.py
        await pilot.pause(0.2)
        # 1. a dial build() drops for this setup -> refused, nothing queued
        n = len(jobs)
        app._run_blind("seg", "2", "3")              # single mode: SEG is ignored
        check("blind A/B on an ignored dial is refused", len(jobs) == n, [j.cmd for j in jobs.values()])
        app._run_blind("cond_strength", "1.0", "0.5")   # 2 s single clip: cond strength unused
        check("blind A/B on cond_strength of a single clip is refused", len(jobs) == n)
        # 2. BALANCED with a blank directive runs as HOLD -> gate refuses, nothing queued
        app.query_one("#mode", Select).value = "director"; await pilot.pause(0.2)
        app.query_one("#seconds", Input).value = "8"
        app.query_one("#directive", TextArea).text = ""
        await pilot.pause(0.2)
        app._run_blind("steadiness", "hold", "balanced")
        check("hold-vs-balanced with blank directive is refused (no half pair)", len(jobs) == n, len(jobs) - n)
        # 3. a real pair queues both halves
        app.query_one("#mode", Select).value = "single"; app.query_one("#seconds", Input).value = "2"
        await pilot.pause(0.2)
        app._run_blind("steps", "30", "40")
        pair = [j for j in jobs.values() if j.params.get("pair_blind")]
        check("a real blind pair queues both runs", len(pair) == 2, len(pair))
        # 4. if the 2nd variant is refused after the 1st queued, the 1st is taken back
        real_q = app._queue_blind_variant
        calls = []
        def flaky(*a, **k):
            calls.append(1)
            return real_q(*a, **k) if len(calls) == 1 else None
        app._queue_blind_variant = flaky
        before = len(jobs)
        app._run_blind("steps", "20", "25")
        app._queue_blind_variant = real_q
        check("half-queued pair is rolled back", len(jobs) == before and len(removed) == 1, (len(jobs) - before, removed))
        # 5. inspect + archive table hide the varied value until REVEAL
        a = pair[0]
        a.status, a.finished, a.started = "done", 2.0, 1.0
        a.params.update(pair_varied_dial="seed", seed="4242")
        txt = app._fmt_inspect(a)
        check("blind inspect hides a varied seed", "4242" not in txt, [l for l in txt.splitlines() if "seed" in l])
        a.params.update(pair_varied_dial="prompt")
        txt = app._fmt_inspect(a)
        check("blind inspect hides a varied prompt", "lighthouse" not in txt)
        app.tick(); await pilot.pause(0.2)
        from textual.widgets import DataTable
        t = app.query_one("#atable", DataTable)
        row = t.get_row(a.id)
        check("archive title hides a blind-varied prompt", "lighthouse" not in str(row), row)
        a.params["pair_revealed"] = True
        app.tick(); await pilot.pause(0.2)
        check("revealed run shows its prompt again", "lighthouse" in str(t.get_row(a.id)), t.get_row(a.id))
        # 6. "shots done" is honest for a run that failed before its first shot completed
        f = studio_core.Job("failrun", "t", "chained", [], dict(a.params, pair_blind=False, nseg=3))
        f.status, f.seg = "failed", 0
        check("failed-before-shot-1 shows 0 of N done", "0 of 3 done" in app._fmt_inspect(f))
        # 7. two enhances of the same run queued back to back get distinct outputs
        src = studio_core.Job("srcrun", "src", "single", [], dict(a.params, pair_blind=False))
        src.status, src.started, src.finished, src.save = "done", 1.0, 2.0, (lambda: None)
        fd = tempfile.mkdtemp(prefix="enhsrc_")
        from PIL import Image
        Image.new("RGB", (64, 48)).save(os.path.join(fd, "0000.png"))
        src.params["frames_dir"] = fd; src.out = "outputs/srcrun.mp4"
        jobs[src.id] = src
        app.query_one(studio.TabbedContent).active = "tab-arch"; app.tick(); await pilot.pause(0.2)
        outs = []
        for _ in range(2):
            t.move_cursor(row=list(t.rows).index(next(k for k in t.rows if k.value == "srcrun")))
            await pilot.pause(0.1)
            app.query_one("#enhancebtn").press(); await pilot.pause(0.3)
            scr = app.screen
            scr.dismiss(dict(interp="2", interp_engine="rife", upscale="0", upmodel="realesrgan", face="0",
                             deflicker="0", restore="none", tile_feather="0", interp_skip="0"))
            await pilot.pause(0.3)
            outs.append([j for j in jobs.values() if j.kind == "enhance"][-1].params.get("out"))
        check("back-to-back enhances get distinct outputs", len(set(outs)) == 2, outs)
        ej = [j for j in jobs.values() if j.kind == "enhance"][-1]
        check("enhance job carries cwd from enqueue", ej.params.get("cwd") == studio.AD_REPO)

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
