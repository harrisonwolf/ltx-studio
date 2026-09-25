"""Blind A/B + archive regressions: a pair is queued whole or not at all, variants whose worker commands
are identical are refused (for blind A/B and PAIR), BALANCED/EVOLVE need a real directive, the inspect
view and archive table hide the varied value until REVEAL, back-to-back enhances of one run get
distinct outputs, "shots done" is honest, and restoring saved sound prefs at launch keeps the INFO intro."""
import sys, os, asyncio, tempfile
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
    def suspended(self): return [j for j in jobs.values() if j.status == "suspended"]
    def archived(self): return [j for j in jobs.values() if j.status == "done"]
    def active(self): return None
    def counts(self): return (len(self.queued()), 0, len(self.archived()), 0)
    def enhance_children(self, jid): return []
    def enqueue(self, title, kind, cmd, params):
        j = studio_core.Job(f"q{len(jobs)}", title, kind, cmd, params)   # real Job, never saved to disk
        j.params_at_enqueue = dict(params)
        j.save = lambda: None
        jobs[j.id] = j; return j
    def remove(self, jid):
        removed.append(jid); jobs.pop(jid, None); return "removed"
    def shutdown(self): pass
studio.REPO = tempfile.mkdtemp(prefix="blindrepo_")   # blind A/B writes runs/pair_blinds.jsonl: never the real one
studio.JobManager = FakeMgr
SAVES = []
studio.save_studio_config = lambda cfg: SAVES.append(cfg)
studio.load_studio_config = lambda: {"sounds": {"enabled": False}}
studio.gpu_budget.budget_ok = lambda *a, **k: (True, 99999)

async def main():
    app = studio.Studio()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause(0.4)
        # 0. launch restore of a saved "sound off" must not replace the INFO intro
        info = str(app.query_one("#newinfo").render())
        check("saved sound prefs don't clobber the INFO intro", "event sounds" not in info and "♪" not in info, info[:60])
        check("launch never saves a sound pick the user didn't make", all(c.get("sounds", {"enabled": False}) == {"enabled": False} for c in SAVES), SAVES)
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
        # 3b. a SEED pair is a real pair (the seed is the variable; only it differs)
        before, handed = len(jobs), len(getattr(app, "_handed_slugs", ()) or ())
        app._run_blind("seed", "111", "222")
        check("blind A/B on SEED queues both runs", len(jobs) == before + 2, len(jobs) - before)
        # dry comparison builds don't burn output names: exactly the two queued runs reserved one each
        check("pre-flight builds don't consume output names", len(app._handed_slugs) - handed == 2,
              len(app._handed_slugs) - handed)
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
        # 5b. a LENGTH pair: single-vs-chained and the shot count would give the length away
        a.params.update(pair_varied_dial="seconds", pair_revealed=False)
        a.kind, a.nseg = "chained", 4
        a.params.update(seg_frames=49, total_frames=193)
        a.seg_secs = [10, 10, 10, 10]
        txt = app._fmt_inspect(a)
        check("length-blind inspect hides kind and shot count", "chained" not in txt and "of 4 done" not in txt and "4  ×" not in txt,
              [l for l in txt.splitlines() if "chained" in l or "of 4" in l or "4  ×" in l])
        prov = app._fmt_provenance(a)
        check("length-blind TIMING hides kind / frames", "chained" not in prov and "frames" in prov and "193" not in prov
              and "hidden" in prov, [l for l in prov.splitlines() if "chained" in l or "frames" in l])
        a.params.update(pair_varied_dial="seed", seed="4242")
        card = app._queue_card(a, "QUEUED · #1", "#ffffff", 70)
        check("blind queue card hides the varied seed", "4242" not in card, card)
        # the masking table: a varied dial also hides what it changes (backend -> res/steps/fps/...)
        a.params.update(pair_blind=True, pair_revealed=False, pair_varied_dial="backend", steps="40", width=704, height=480)
        card = app._queue_card(a, "QUEUED · #1", "#ffffff", 70)
        check("backend-blind queue card hides steps and res too", "40st" not in card and "704×480" not in card, card)
        a.params.update(pair_varied_dial="seconds"); a.status, a.seg = "suspended", 1
        app._sync_queue_cards(); await pilot.pause(0.1)
        qt = app.query_one("#qtable", DataTable)
        cards = " ".join(str(qt.get_row(k)) for k in qt.rows)
        check("length-blind SUSPENDED card hides the shot count", "shot 1/4" not in cards, cards[:300])
        a.status = "done"
        a.params.update(pair_varied_dial="prompt"); a.kind = "director"
        a.plans = [[0, "a whale breaching at dawn", "a whale breaching at dawn, cinematic"]]
        a.director = "a whale breaching, cinematic"
        txt = app._fmt_inspect(a)
        check("prompt-blind inspect hides the director's plans / rewrites", "whale" not in txt,
              [l for l in txt.splitlines() if "whale" in l])
        # a STEADINESS pair: hold's "(skipped …)" plans vs evolve's rewrites give it away -> hidden
        a.params.update(pair_varied_dial="steadiness")
        a.plans = [[1, "(skipped - scene holding steady; prompt unchanged)", ""]]
        txt = app._fmt_inspect(a)
        check("steadiness-blind inspect hides the director's notes", "holding steady" not in txt,
              [l for l in txt.splitlines() if "steady" in l])
        # a LENGTH pair in director mode: the number of noted shots / seams gives the length away
        a.params.update(pair_varied_dial="seconds")
        a.plans = [[i, f"plan {i}", ""] for i in range(1, 8)]
        a.dir_ms = {i: [1000, 2000] for i in range(1, 8)}
        txt = app._fmt_inspect(a)
        check("length-blind inspect hides the per-shot notes and seam count", "7 seams" not in txt and "shot 8" not in txt,
              [l for l in txt.splitlines() if "seams" in l or "shot 8" in l])
        a.kind, a.plans, a.director, a.dir_ms = "chained", [], "", {}
        a.params["pair_blind"] = False
        # derived runs (clone / re-roll / ×N / enhance) of an unrevealed blind run are refused
        br = studio_core.Job("blindsrc", "t", "single", [], dict(a.params, pair_blind=True, pair_revealed=False,
                                                                   pair_varied_dial="seed", pair_id="blind-z"))
        br.status, br.started, br.finished, br.save = "done", 1.0, 2.0, (lambda: None)
        jobs[br.id] = br
        app.query_one(studio.TabbedContent).active = "tab-arch"; app.tick(); await pilot.pause(0.2)
        n_before = len(jobs)
        for btn in ("clonebtn", "rerollbtn", "replbtn", "enhancebtn"):
            t.move_cursor(row=list(t.rows).index(next(k for k in t.rows if k.value == "blindsrc")))
            await pilot.pause(0.05)
            app.query_one("#" + btn).press(); await pilot.pause(0.2)
            info = str(app.query_one("#inspectinfo").render())
            check(f"{btn} on an unrevealed blind run is refused", "REVEAL" in info and app.screen is app.screen_stack[0], info[:80])
        check("no derived run queued from a blind run", len(jobs) == n_before)
        jobs.pop("blindsrc")
        # a hard-killed suspended run's card counts its CHECKPOINTED shots (job.seg is the lost one)
        hk = studio_core.Job("hardkilled", "t", "chained", [], dict(a.params, pair_blind=False, nseg=6))
        hk.status, hk.seg, hk.last_ckpt_seg, hk.save = "suspended", 3, 2, (lambda: None)
        jobs[hk.id] = hk
        app._sync_queue_cards(); await pilot.pause(0.1)
        qt = app.query_one("#qtable", DataTable)
        cards = " ".join(str(qt.get_row(k)) for k in qt.rows)
        check("hard-killed SUSPENDED card counts checkpointed shots", "shot 2/6" in cards and "shot 3/6" not in cards, cards[:200])
        jobs.pop("hardkilled")
        # 6. "shots done" is honest for a run that failed before its first shot completed
        f = studio_core.Job("failrun", "t", "chained", [], dict(a.params, pair_blind=False, nseg=3))
        f.status, f.seg = "failed", 0
        check("failed-before-shot-1 shows 0 of N done", "0 of 3 done" in app._fmt_inspect(f))
        f.status, f.seg, f.last_ckpt_seg, f.nseg = "suspended", 3, 3, 5      # suspended after shot 3
        rj = studio_core.Job("resumedrun", "t", "chained", [], dict(a.params, pair_blind=False, nseg=3))
        rj.status, rj.started, rj.finished, rj.prior_secs = "done", 1000.0, 1050.0, 100
        check("inspect runtime spans every resumed leg", "runtime     " + studio.fmt(150) in app._fmt_inspect(rj),
              [l for l in app._fmt_inspect(rj).splitlines() if "runtime" in l])
        check("suspended run counts its checkpointed shots", "3 of 5 done" in app._fmt_inspect(f), app._fmt_inspect(f)[-400:])
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
        check("enhance job carries cwd from enqueue", ej.params_at_enqueue.get("cwd") == studio.AD_REPO)

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
