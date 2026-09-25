"""CLONE / RE-ROLL / PAIR / ×N regressions: cloned GUIDANCE/STEPS survive the backend-change handler,
a wan-turbo detour doesn't leave STEPS at 6, re-rolls keep the distilled checkpoint and leave the
NEW RUN form alone, PAIR refuses an already-paired run, and CLONE blanks NAME as it promises."""
import sys, os, asyncio, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STUDIO_NO_ANIM"] = "1"
import studio
from textual.widgets import Input, Select, TextArea, DataTable

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

def mkjob(jid, **params):
    base = {"prompt": "a lighthouse at dusk", "steps": "40", "cfg": "3.0", "seed": "777", "fps": "24",
            "seconds": "2", "res": "704 x 480  balanced", "backend": "ltx", "cond_strength": "1.0",
            "cfg_rescale": "off", "cfg_interval": "off", "wan_ref_anchor": "off", "n_prompt": "bad",
            "anchors": "", "directive": "", "image": "", "mode": "single"}
    base.update(params)
    j = types.SimpleNamespace(id=jid, title=jid, kind=base["mode"], status="done", params=base,
                              created=1.0, started=1.0, finished=2.0, out=f"outputs/{jid}.mp4")
    j.elapsed = lambda: 1
    j.save = lambda: None
    return j

ARCH = [mkjob("distilled_run", ltx_variant="distilled", steps="8", cfg="1.0"),
        mkjob("paired_run", pair_id="blind-x", pair_variant="A")]
enqueued = []
class FakeMgr:
    def __init__(self): self.jobs, self.paused, self.vram_reserve_gb = {j.id: j for j in ARCH}, False, 1.0
    def queued(self): return []
    def suspended(self): return []
    def archived(self): return list(ARCH)
    def active(self): return None
    def counts(self): return (0, 0, len(ARCH), 0)
    def enhance_children(self, jid): return []
    def enqueue(self, title, kind, cmd, params):
        j = types.SimpleNamespace(id=f"q{len(enqueued)}", params=params, cmd=cmd, save=lambda: None)
        enqueued.append(j); return j
    def shutdown(self): pass
studio.JobManager = FakeMgr
studio.save_studio_config = lambda cfg: None
studio.load_studio_config = lambda: {}
studio.gpu_budget.budget_ok = lambda *a, **k: (True, 99999)

async def main():
    app = studio.Studio()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause()
        F = lambda wid: app.query_one("#" + wid).value
        # 1. clone an LTX cfg-5.0 run while the form sits on wan -> cfg stays 5.0 after events settle
        app.query_one("#backend", Select).value = "wan"; await pilot.pause(0.3)
        app._apply_config({"backend": "ltx", "cfg": "5.0", "steps": "40", "prompt": "x"}); await pilot.pause(0.3)
        check("clone keeps GUIDANCE across a backend change", (F("backend"), F("cfg")) == ("ltx", "5.0"), (F("backend"), F("cfg")))
        # 2. clone a wan-turbo run with 20 steps onto an ltx form -> 1.0 / 20 kept
        app._apply_config({"backend": "wan-turbo", "cfg": "1.0", "steps": "20"}); await pilot.pause(0.3)
        check("clone keeps turbo STEPS", (F("cfg"), F("steps")) == ("1.0", "20"), (F("cfg"), F("steps")))
        # 3. a manual ltx -> turbo -> ltx detour restores STEPS (turbo's 6 must not stick)
        app._apply_config({"backend": "ltx", "cfg": "3.0", "steps": "40"}); await pilot.pause(0.3)
        app.query_one("#backend", Select).value = "wan-turbo"; await pilot.pause(0.3)
        check("turbo nudges STEPS to 6", F("steps") == "6", F("steps"))
        app.query_one("#backend", Select).value = "ltx"; await pilot.pause(0.3)
        check("leaving turbo restores STEPS", (F("steps"), F("cfg")) == ("40", "3.0"), (F("steps"), F("cfg")))
        # 4. CLONE blanks a stale NAME
        app.query_one("#name", Input).value = "old_name"
        cfg = app._clone_config(ARCH[0]); cfg["name"] = ""
        app._apply_config(cfg); await pilot.pause(0.2)
        check("clone blanks NAME", F("name") == "", F("name"))
        # set a recognizable form state that archive actions must NOT touch
        app._apply_config({"backend": "ltx", "cfg": "3.0", "steps": "40", "seed": "", "prompt": "form prompt"})
        await pilot.pause(0.3)
        form_before = {w: (app.query_one("#" + w).text if isinstance(app.query_one("#" + w), TextArea) else app.query_one("#" + w).value)
                       for w in ("prompt", "seed", "steps", "cfg", "backend", "name")}
        app.query_one(studio.TabbedContent).active = "tab-arch"; app.tick(); await pilot.pause(0.3)
        t = app.query_one("#atable", DataTable)
        def select(jid):
            for i, rk in enumerate(t.rows):
                if rk.value == jid:
                    t.move_cursor(row=i)
        async def press(btn, result):
            app.query_one("#" + btn).press(); await pilot.pause(0.3)
            if result is not None:
                app.screen.dismiss(result); await pilot.pause(0.4)
        # 5. RE-ROLL keeps the distilled checkpoint and uses the run's own config
        select("distilled_run"); await pilot.pause(0.1)
        await press("rerollbtn", {"seed": "4242"})
        c = enqueued[-1].cmd if enqueued else []
        check("re-roll queues one run", len(enqueued) == 1, len(enqueued))
        check("re-roll keeps --ltx_variant distilled", "--ltx_variant" in c and c[c.index("--ltx_variant") + 1] == "distilled", c)
        check("re-roll uses the new seed + the run's prompt",   # (--seed's value: the out path is a timestamp)
              "--seed" in c and c[c.index("--seed") + 1] == "4242" and "a lighthouse at dusk" in c, c)
        # 6. ×N builds from the run too
        await press("replbtn", {"n": "2"})
        check("×N queues N runs", len(enqueued) == 3, len(enqueued))
        check("×N runs keep the checkpoint", all("--ltx_variant" in j.cmd for j in enqueued[1:]))
        # 7. PAIR on an already-paired run is refused (a run belongs to one pair)
        n = len(enqueued)
        select("paired_run"); await pilot.pause(0.1)
        await press("pairbtn", None)
        check("PAIR refuses an already-paired run", len(enqueued) == n and not isinstance(app.screen, studio.PairScreen),
              type(app.screen).__name__)
        # 8. PAIR a fresh run: partner gets the source's id, source back-annotated
        select("distilled_run"); await pilot.pause(0.1)
        await press("pairbtn", {"dial": "steps", "value": "6"})
        j = enqueued[-1]
        check("PAIR queues the B side", len(enqueued) == n + 1 and j.params.get("pair_id") == "distilled_run"
              and ARCH[0].params.get("pair_id") == "distilled_run", (len(enqueued) - n, j.params.get("pair_id")))
        # 9. none of the archive actions touched the NEW RUN form
        await pilot.pause(0.3)
        form_after = {w: (app.query_one("#" + w).text if isinstance(app.query_one("#" + w), TextArea) else app.query_one("#" + w).value)
                      for w in form_before}
        check("archive actions leave the NEW RUN form alone", form_before == form_after, (form_before, form_after))

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
