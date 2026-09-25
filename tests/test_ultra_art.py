"""ultra_art: the animated decorations for the ultra-themes tier. FULLY DATA-DRIVEN — every check
iterates the registry (studio_themes.ULTRA_THEMES / ultra_art.THEMES), so adding a theme needs NO
edit here. Guards the properties the studio relies on: purity (deterministic frames), real motion,
the STUDIO_NO_ANIM freeze, width-fit + balanced markup at every size/beat, and the INFO running-light.
(Panel borders are static now — the border-breathe was removed 2026-07-08, so there is no glow.)"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ultra_art
import studio_themes
from rich.text import Text as _RT      # authoritative markup->plain (handles \[ escapes correctly)

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

TAG = re.compile(r"\[/?[^\]]*\]")
def plain(s): return TAG.sub("", s)
def maxch(h):
    h = h.lstrip("#"); return max(int(h[i:i + 2], 16) for i in (0, 2, 4))

NAMES = list(ultra_art.THEMES)
# registry consistency (magic count lives in test_themes_css; here we assert the maps agree)
check("decorations match the ultra registry",
      len(NAMES) == len(studio_themes.ULTRA_THEMES) and set(NAMES) == set(studio_themes.ULTRA_NAMES))
check("every ultra theme has a decoration + title",
      all(ultra_art.is_ultra(n) and n in ultra_art.TITLES for n in studio_themes.ULTRA_NAMES))
check("non-ultra -> None / False",
      ultra_art.render("pipboy", 0) is None and not ultra_art.is_ultra("pipboy"))

# purity + motion, per theme
for n in NAMES:
    check("%s: pure (beat 3 reproducible)" % n, ultra_art.render(n, 3) == ultra_art.render(n, 3))
    frames = {ultra_art.render(n, b) for b in range(0, 16)}
    check("%s: animates (distinct frames)" % n, len(frames) > 1)

# safety: no raise, width-fit (<=48 budget), balanced markup, at every size/beat
for n in NAMES:
    worst, balanced, raised = 0, True, False
    for w in (8, 16, 24, 30, 44, None):
        for b in range(0, 12):
            try:
                art = ultra_art.render(n, b, width=w)
            except Exception:
                raised = True; art = ""
            art = art or ""
            try:
                _RT.from_markup(art)          # the authoritative check: what the studio parses
            except Exception:
                balanced = False
            for ln in art.split("\n"):
                worst = max(worst, len(plain(ln)))
    check("%s: never raises" % n, not raised)
    check("%s: fits 48-col budget (max=%d)" % (n, worst), worst <= 48)
    check("%s: balanced markup" % n, balanced)

# (The whole-canvas "full-page touch" was removed — being redesigned; awaiting approval. No bg_pulse.)

# EFFECTS covers exactly the ultra tier (INFO running-light config; panel borders are static — no glow)
check("EFFECTS covers exactly the ultra tier", set(ultra_art.EFFECTS) == set(studio_themes.ULTRA_NAMES))
check("border-breathe removed: no glow() on ultra_art", not hasattr(ultra_art, "glow"))

# electron_text (topbar wave / info comet) — smooth float clock, bracket-safe, balanced
_ITXT = "PIP-OS v8 :: JOB CONTROL\nConfigure a run and QUEUE it. cfg [1..7]"
_wave = [ultra_art.electron_text(_ITXT, b * 0.13, "#e0a0d0", "#5cffe0", mode="wave") for b in range(0, 12)]
check("electron_text moves + bracket-safe + balanced",
      len({repr(f) for f in _wave}) > 1
      and all(_RT.from_markup(f).plain == _ITXT for f in _wave)
      and all(f.count("[") == f.count("]") for f in _wave))
# render_electrons (INFO touring comets) — PURE fn of (text, heads)
_ea = ultra_art.render_electrons(_ITXT, [5.0], "#c9a24a", "#fff3c4")
check("render_electrons: pure + moves + bracket-safe",
      _ea == ultra_art.render_electrons(_ITXT, [5.0], "#c9a24a", "#fff3c4")
      and _ea != ultra_art.render_electrons(_ITXT, [15.0], "#c9a24a", "#fff3c4")
      and _RT.from_markup(_ea).plain == _ITXT)

# backslashes in the text must not escape a cell's closing tag (a trailing "\\" used to swallow the
# next "[/#..]" -> MarkupError / garbled text). Plain text AND per-char colors must round-trip.
def _chars(markup):
    t = _RT.from_markup(markup)
    st = [None] * len(t.plain)
    for sp in t.spans:
        for i in range(sp.start, sp.end):
            st[i] = str(sp.style)
    return t.plain, st
_BS = ["\\", "a\\", "\\a", "C:\\path\\to\\x", "\\[x]", "[\\]", "\\\\", "a\\ b",
       "x\\\ny\\", "cfg \\[1..7] \\", "[/#e0a0d0]\\[#e0a0d0]"]
_bs_ok, _bs_bad = True, []
for _t in _BS:
    for _b in (0.0, 0.7, 3.3, 9.9):
        for _f in (lambda t, b: ultra_art.electron_text(t, b, "#e0a0d0", "#5cffe0", mode="wave"),
                   lambda t, b: ultra_art.electron_text(t, b, "#c9a24a", "#c9a24a", mode="electron"),
                   lambda t, b: ultra_art.render_electrons(t, [b, b * 3], "#c9a24a", "#fff3c4")):
            _m = _f(_t, _b)
            try:
                _pl, _st = _chars(_m or "")
                _unmerged = ultra_art._merge_runs
                ultra_art._merge_runs = lambda m: m          # merged vs per-char markup: same styles
                try:
                    _ref = _chars(_f(_t, _b) or "")
                finally:
                    ultra_art._merge_runs = _unmerged
                if _pl != _t or (_pl, _st) != _ref:
                    _bs_ok = False; _bs_bad.append((_t, _pl))
            except Exception as _e:
                _bs_ok = False; _bs_bad.append((_t, str(_e)[:50]))
check("electron_text/render_electrons: backslash-safe (text + colors round-trip)", _bs_ok, _bs_bad[:3])

# render() "never raises" -- even for a junk beat (float() used to sit outside the try)
_raised = []
for _b in ("x", None, [], object(), float("nan"), float("inf")):
    try:
        ultra_art.render(NAMES[0], _b)
    except Exception as _e:
        _raised.append((repr(_b), type(_e).__name__))
check("render: junk beat never raises", not _raised, _raised)

# freeze switch: STUDIO_NO_ANIM pins everything to frame 0 (headless / reduce-motion)
os.environ["STUDIO_NO_ANIM"] = "1"
try:
    for n in NAMES:
        check("%s: STUDIO_NO_ANIM freezes to frame 0" % n, ultra_art.render(n, 9) == ultra_art.render(n, 0))
finally:
    del os.environ["STUDIO_NO_ANIM"]

# ---- signature-moment registry + the live current (the whole-screen final layer) ----
check("MOMENTS covers every ultra theme", set(ultra_art.MOMENTS) == set(NAMES))
check("moment: pure + bounded",
      all(ultra_art.moment(n, 12.3) == ultra_art.moment(n, 12.3)
          and 0.0 <= ultra_art.moment(n, b)[0] <= 1.0 and 0.0 <= ultra_art.moment(n, b)[1] <= 1.0
          for n in NAMES for b in (0.0, 7.7, 44.4, 123.4)))
check("moment: unknown theme -> (0, 0)", ultra_art.moment("pipboy", 5.0) == (0.0, 0.0))
_fires = [n for n in NAMES if any(ultra_art.moment(n, x * 0.5)[0] > 0 for x in range(2000))]
check("moment: every theme fires within ~8 min", set(_fires) == set(NAMES), sorted(set(NAMES) - set(_fires)))

_seen, _ok = set(), True
for _x in range(0, 320):
    _glows = ultra_art.current_frame(_x * 0.25, 4)
    _ok &= len(_glows) <= 1 and all(0.0 <= g <= 1.0 for g in _glows.values())
    _seen.update(_glows)
check("current: a single bounded spark (whole panel at a time)", _ok)
check("current: visits every panel (4/4)", _seen == {0, 1, 2, 3}, _seen)
check("current: head matches the lit panel",
      all((lambda f: not f or ultra_art.current_head(b, 4) in f)(ultra_art.current_frame(b, 4))
          for b in (1.0, 10.0, 21.7, 33.3)))
check("current: storm shimmers every panel", len(ultra_art.current_frame(3.3, 3, storm=1.0)) == 3)
check("current: storm glow bounded", all(0.0 <= g <= 1.0 for g in ultra_art.current_frame(3.3, 3, storm=1.0).values()))
check("current: no panels -> {}", ultra_art.current_frame(5.0, 0) == {})


print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
