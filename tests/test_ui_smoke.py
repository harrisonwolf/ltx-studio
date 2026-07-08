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
    load_msg="", preview=None, tail=[], error=None, ckpt_dir=None, resumes=0,
    created=now - 1300, started=now - 1200, finished=now - 100, favorite=False,
    seam_mse=[], drift=[], tok_counts=[], peak_vram=5400)
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
    def enhance_children(self, jid): return []
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
        # theme change must re-tint the SHOWN schematic in place (it used to keep the old-palette markup
        # until a dial was refocused). Assert the rendered markup actually changes color across themes.
        _sch_a = repr(fv.render())                  # repr() carries the color spans; str() is plain text
        app.theme = "pipboy-usa"; await pilot.pause()
        _sch_b = repr(fv.render())
        check("theme change re-renders the shown schematic", _sch_a != _sch_b, "schematic did not re-tint")
        app.theme = "pipboy"; await pilot.pause()
        app.query_one("#name").focus(); await pilot.pause()
        check("sticky schematic keeps its label on visual-less focus", "GUIDANCE" in str(fv.border_title))
        check("rail: boxes unmoving across focus", before == (fv.region, ro.region, ip.region))
        # readout is FIXED 15 rows = all six gauges always visible (the old 1fr box clipped the
        # tail gauges on short screens); SHOTS chain gauge replaced the TIME bar
        check("readout: fixed 15-row box", ro.region.height == 15, ro.region.height)
        _rr = str(ro.render())                     # Textual 8 Static: update() lands in .content, not .renderable
        check("readout: SHOTS chain gauge rendered", "SHOTS" in _rr and "[██]" in _rr, _rr[:120])
        check("readout: TIME bar retired", "TIME " not in _rr)
        check("readout: tail gauge DRIFT present", "DRIFT" in _rr)
        # mode gray-out
        for wid in ("directive", "steadiness", "seg"):
            check("single: #%s grayed" % wid, app.query_one("#" + wid).disabled is True)
        app.query_one("#mode", Select).value = "director"; await pilot.pause()
        check("director: #seg enabled", app.query_one("#seg").disabled is False)
        app.query_one("#mode", Select).value = "single"; await pilot.pause()
        # backend gray-out: schedule is Wan-only, identity anchor Wan/turbo-only
        check("ltx: schedule + anchor grayed", app.query_one("#cfg_interval").disabled is True
              and app.query_one("#wan_ref_anchor").disabled is True)
        app.query_one("#backend", Select).value = "wan"; await pilot.pause()
        check("wan: schedule + anchor enabled", app.query_one("#cfg_interval").disabled is False
              and app.query_one("#wan_ref_anchor").disabled is False)
        app.query_one("#backend", Select).value = "wan-turbo"; await pilot.pause()
        check("turbo: schedule grayed, anchor enabled", app.query_one("#cfg_interval").disabled is True
              and app.query_one("#wan_ref_anchor").disabled is False)
        app.query_one("#backend", Select).value = "ltx"; await pilot.pause()
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
        # ARCHIVE INSPECT renders a real job end-to-end (_fmt_inspect NameError class: the
        # _status_glyph/_run_kind strays only exploded when this exact path was clicked)
        arch = active
        arch.status = "done"
        app.mgr.jobs["jS"] = arch
        app._insp_jid, app._insp_view = "jS", "inspect"
        app._render_inspect()
        await pilot.pause()
        info_txt = str(app.query_one("#inspectinfo", Static).render())
        check("archive INSPECT renders (id + status present)", "jS" in info_txt and "DONE" in info_txt,
              info_txt[:80])
        app._insp_view = "timing"
        app._render_inspect()
        check("archive TIMING view renders", True)
        # every curated theme applies (stylesheet-crash guard) — both tiers
        for t in tuple(studio.EXTRA_THEMES) + tuple(studio.ULTRA_THEMES):
            try:
                app.theme = t.name; await pilot.pause()
                good = True
            except Exception:
                good = False
            check("theme applies: %s" % t.name, good)
        # ultra tier: an ultra theme lights the decoration box; a normal theme hides it (zero footprint).
        # The art only paints on the NEW RUN tab (where the box lives), so activate it first.
        app.query_one(TabbedContent).active = "tab-new"; await pilot.pause()
        app.theme = "ultra-synthwave"; await pilot.pause()
        dec = app.query_one("#ultradecor")
        check("ultra theme lights the decoration", dec.has_class("-on"))
        check("ultra decoration renders art", bool(str(dec.render()).strip()))
        # breakout effects: borders breathe + INFO text + topbar run an electron/wave. Ultra animation
        # is driven by the continuous _ultra_t clock (its own ~15fps timer); drive two KNOWN clock values
        # (reset _ultra_phase so the sprite-gated paint runs the border) -> deterministic, not flaky.
        ipanel = app.query_one("#infopanel")
        def _bord():
            b = ipanel.styles.border
            return str(b.top[1].hex) if b and b.top else None
        app._ultra_t = 0.0; app._ultra_phase = None; app._paint_ultra(); app._animate_ultra_topbar()
        _b0 = _bord()
        _t0 = repr(app.query_one("#topbartitle").render())
        app._ultra_t = 6.0; app._ultra_phase = None; app._paint_ultra(); app._animate_ultra_topbar()
        check("ultra: info-panel border breathes across ticks", _b0 != _bord(), (_b0, _bord()))
        check("ultra: topbar ambient wave moves across ticks", _t0 != repr(app.query_one("#topbartitle").render()))
        # INFO now fires DISCRETE electrons on a random schedule; inject one and confirm its comet TOURS
        app._electron_starts = [6.0]
        app._ultra_t = 6.1; app._animate_ultra_info(); _e0 = repr(app.query_one("#newinfo").render())
        app._ultra_t = 6.9; app._animate_ultra_info(); _e1 = repr(app.query_one("#newinfo").render())
        check("ultra: INFO electron comet tours the text", _e0 != _e1)
        # scheduler invariants over ~60s of frames: gaps within [MIN, 25s], concurrency stays bounded
        app._electron_starts, app._electron_next, app._ultra_t = [], 0.0, 0.0
        _fires, _maxc = [], 0
        for _ in range(60 * app._ULTRA_FPS):
            app._ultra_t += 2.0 / app._ULTRA_FPS
            _n = len(app._electron_starts); app._step_electrons()
            if len(app._electron_starts) > _n: _fires.append(app._ultra_t)
            _maxc = max(_maxc, len(app._electron_starts))
        _gaps = [(_fires[i + 1] - _fires[i]) / 2.0 for i in range(len(_fires) - 1)]
        check("ultra: electron gaps within [MIN, 25s]",
              all(app._ELECTRON_MIN_GAP_S - 0.3 <= g <= app._ELECTRON_MAX_GAP_S + 0.3 for g in _gaps), _gaps)
        check("ultra: electron tours prune (bounded concurrency)", 1 <= _maxc <= 4, _maxc)
        # smoothness: a SMALL clock step (sub-beat) already changes the border (continuous glow, not
        # a 5-color snap) -> proves the interpolation the 15fps timer relies on
        app._ultra_t = 6.0; app._ultra_phase = None; app._paint_ultra(); _bsmall = _bord()
        app._ultra_t = 6.2; app._ultra_phase = None; app._paint_ultra()
        check("ultra: glow interpolates continuously (smooth)", _bsmall != _bord(), (_bsmall, _bord()))
        check("ultra: whole rail breathes (all 4 borders)",
              all(app.query_one("#" + w).styles.border and app.query_one("#" + w).styles.border.top
                  for w, _ in app._ULTRA_BORDERS))
        app.theme = "pipboy"; await pilot.pause()
        check("normal theme hides the decoration", not dec.has_class("-on"))
        check("ultra->normal restores a non-glow border", _bord() not in ("#FF2D95", "#FF6AB0", "#C42678"), _bord())
        check("ultra->normal restores the plain topbar title",
              str(app.query_one("#topbartitle").render()).strip() == studio.Studio.TOPBAR_TITLE.strip())
        # theme overhaul: $selection resolves for CSS (pipboy defines it; builtins fall back to panel),
        # and the selected queue card re-lights its OWN frame in heavy box-art vs rounded when not
        cssv = app.get_css_variables()
        check("get_css_variables resolves $selection", "selection" in cssv, sorted(cssv)[:0])
        sel_card = app._queue_card(active, "QUEUED · #1", "#6dffab", 60, selected=True)
        dim_card = app._queue_card(active, "QUEUED · #1", "#6dffab", 60, selected=False)
        check("selected card = heavy ignited frame", "┏" in sel_card and "┃" in sel_card)
        check("unselected card = quiet rounded frame", "╭" in dim_card and "┏" not in dim_card)
        check("cut theme migrates (ice->vfd)", studio.THEME_MIGRATE.get("pipboy-ice") == "pipboy-vfd")
        check("restored theme not migrated (radium kept)", "pipboy-radium" not in studio.THEME_MIGRATE
              and any(t.name == "pipboy-radium" for t in studio.EXTRA_THEMES))
        # picking a sound persists but NEVER auditions (no unexpected audio — user rule)
        sound_calls.clear()
        app.query_one("#snd_done", Select).value = "bell.wav"
        await pilot.pause()
        check("sound pick does NOT audition", sound_calls == [], sound_calls)
        # picker: two curated sections (pipboy family, then a non-selectable ULTRA-THEMES header +
        # the ultra tier). Skip the disabled/id-less header when checking curation.
        scr = studio.ThemePickerScreen()
        app.push_screen(scr); await pilot.pause()
        ol = scr.query_one("#thlist", OptionList)
        opts = [ol.get_option_at_index(i) for i in range(ol.option_count)]
        real = [o.id for o in opts if o.id and not o.disabled]
        check("picker curated to pipboy/ultra family",
              bool(real) and all(n.startswith(("pipboy", "ultra")) for n in real))
        check("picker ids are all registered themes", all(n in app.available_themes for n in real))
        check("picker has a non-selectable ultra section header",
              any(o.disabled and o.id is None for o in opts))
        check("picker lists the ultra tier", any(n.startswith("ultra") for n in real))
        await pilot.press("escape"); await pilot.pause()

async def short_live():
    """Portable-monitor regime on the LIVE tab: -short (height<38) sheds PACE+STEERING but KEEPS
    the PHASES stopwatch; -tiny (height<30) sheds PHASES too. Controls stay reachable at both."""
    state["active"] = active
    def reachable(app):
        pb = app.query_one("#pausebtn"); return pb.region.y + pb.region.height <= app.size.height
    def shown(app, sel):
        w = app.query_one(sel); return bool(w.display) and w.region.height > 0
    app = studio.Studio()
    async with app.run_test(size=(120, 34)) as pilot:      # short, not tiny
        await pilot.pause()
        app.query_one(TabbedContent).active = "tab-live"; await pilot.pause()
        app.tick(); await pilot.pause()
        check("short LIVE: -short set", app.screen.has_class("-short") and not app.screen.has_class("-tiny"))
        check("short LIVE: PHASES stopwatch KEPT", shown(app, "#phasestrip"))
        check("short LIVE: PACE/STEERING shed", not shown(app, "#pacestrip") and not shown(app, "#steerstrip"))
        check("short LIVE: controls reachable", reachable(app))
    app2 = studio.Studio()
    async with app2.run_test(size=(120, 28)) as pilot:     # tiny
        await pilot.pause()
        app2.query_one(TabbedContent).active = "tab-live"; await pilot.pause()
        app2.tick(); await pilot.pause()
        check("tiny LIVE: -tiny set", app2.screen.has_class("-tiny"))
        check("tiny LIVE: PHASES shed too", not shown(app2, "#phasestrip"))
        check("tiny LIVE: controls still reachable", reachable(app2))
        # theme picker must FIT a short second monitor (auto height used to clip the bottom off-screen)
        app2.push_screen(studio.ThemePickerScreen()); await pilot.pause(); await pilot.pause()
        pbox = app2.screen.query_one("#thbox")
        check("picker fits short screen (no bottom cutoff)",
              pbox.region.y >= 0 and pbox.region.y + pbox.region.height <= 28,
              (pbox.region.y, pbox.region.height))
        plist = app2.screen.query_one("#thlist")
        _last = next((plist.get_option_at_index(i).id for i in range(plist.option_count - 1, -1, -1)
                      if plist.get_option_at_index(i).id), None)
        check("picker still lists the ultra tier (scrollable to synthwave)", _last == "ultra-synthwave", _last)
    state["active"] = None

asyncio.run(main())
asyncio.run(short_live())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
