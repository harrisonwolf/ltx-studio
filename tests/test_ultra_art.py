"""ultra_art: the animated decorations for the ultra-themes tier. FULLY DATA-DRIVEN — every check
iterates the registry (studio_themes.ULTRA_THEMES / ultra_art.THEMES), so adding a theme needs NO
edit here. Guards the properties the studio relies on: purity (deterministic frames), real motion,
the STUDIO_NO_ANIM freeze, width-fit + balanced markup at every size/beat, the additive canvas breath
(base==theme bg, peak near-black), and the border glow."""
import sys, os, re, math
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
            if art.count("[") != art.count("]"):
                balanced = False
            for ln in art.split("\n"):
                worst = max(worst, len(plain(ln)))
    check("%s: never raises" % n, not raised)
    check("%s: fits 48-col budget (max=%d)" % (n, worst), worst <= 48)
    check("%s: balanced markup" % n, balanced)

# ADDITIVE full-page canvas breath: EVERY ultra theme opts in, in its own hue; base==theme bg;
# peak near-black (<=24/channel). Non-ultra -> None. (Borders are independent; not asserted here.)
for t in studio_themes.ULTRA_THEMES:
    eff = ultra_art.EFFECTS.get(t.name) or {}
    check("%s: opts into the canvas breath" % t.name, "bgpulse" in eff)
    if "bgpulse" in eff:
        base, peak = eff["bgpulse"]
        check("%s: canvas base == theme bg" % t.name, base.lower() == t.background.lower())
        check("%s: canvas peak near-black (<=24/ch)" % t.name, maxch(peak) <= 24)
check("bg_pulse: non-ultra theme -> None", ultra_art.bg_pulse("pipboy", 3) is None)
_bp = [ultra_art.bg_pulse(NAMES[0], b * 0.5) for b in range(0, 40)]
check("bg_pulse: moves", all(_bp) and len(set(_bp)) > 3)

# EFFECTS covers exactly the ultra tier; border glow is valid + breathes, per theme
check("EFFECTS covers exactly the ultra tier", set(ultra_art.EFFECTS) == set(studio_themes.ULTRA_NAMES))
check("glow: non-ultra -> None", ultra_art.glow("pipboy", 0) is None)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
for n in NAMES:
    cols = [ultra_art.glow(n, b) for b in range(0, 14)]
    check("%s: glow valid hex + breathes" % n, all(_HEX.match(c or "") for c in cols) and len(set(cols)) > 1)

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

# freeze switch: STUDIO_NO_ANIM pins everything to frame 0 (headless / reduce-motion)
os.environ["STUDIO_NO_ANIM"] = "1"
try:
    for n in NAMES:
        check("%s: STUDIO_NO_ANIM freezes to frame 0" % n, ultra_art.render(n, 9) == ultra_art.render(n, 0))
    check("glow + bg_pulse frozen under STUDIO_NO_ANIM",
          ultra_art.glow(NAMES[0], 9) == ultra_art.glow(NAMES[0], 0)
          and ultra_art.bg_pulse(NAMES[0], 9) == ultra_art.bg_pulse(NAMES[0], 0))
finally:
    del os.environ["STUDIO_NO_ANIM"]

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
