"""ultra_art: the animated pixel-art decorations for the ultra-themes tier. Guards the properties the
studio relies on — purity (deterministic frames), real motion, the STUDIO_NO_ANIM freeze, width-fit +
balanced markup at every size/beat, and that the module's THEMES match studio_themes.ULTRA_NAMES."""
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

NAMES = list(ultra_art.THEMES)
check("four ultra decorations", len(NAMES) == 4)
check("THEMES == ULTRA_NAMES", set(NAMES) == set(studio_themes.ULTRA_NAMES))
check("every ultra theme has a decoration + title",
      all(ultra_art.is_ultra(n) and n in ultra_art.TITLES for n in studio_themes.ULTRA_NAMES))
check("non-ultra -> None / False",
      ultra_art.render("pipboy", 0) is None and not ultra_art.is_ultra("pipboy"))

# purity: same (name, beat) -> identical output
for n in NAMES:
    check("%s: pure (beat 3 reproducible)" % n, ultra_art.render(n, 3) == ultra_art.render(n, 3))

# motion: the frame at beat 0 differs from a later beat (color sweep counts)
for n in NAMES:
    frames = {ultra_art.render(n, b) for b in range(0, 12)}
    check("%s: animates (distinct frames)" % n, len(frames) > 1, frames)

# theme-specific atmosphere: sprite/scene themes each have their OWN glyph SET (embers / HUD reticles
# / stars); matrix's whole decoration is procedural code rain, checked separately below.
_SPARKLE = {"ultra-dragon": "✸✦⋆", "ultra-skynet": "+⌖⊹", "ultra-synthwave": "✧✩·"}
check("each atmosphere glyph set is UNIQUE", len(set(_SPARKLE.values())) == len(_SPARKLE))
for n, gset in _SPARKLE.items():
    hits = [any(g in (ultra_art.render(n, b * 0.3) or "") for g in gset) for b in range(0, 30)]
    check("%s: has twinkling themed atmosphere" % n, any(hits))
    check("%s: atmosphere twinkles (frames vary)" % n,
          len({ultra_art.render(n, b * 0.3) for b in range(0, 30)}) > 1)
# matrix: procedural falling code -> its glyphs appear and the rain FALLS (frames vary across time)
_mtx = [ultra_art.render("ultra-matrix", b * 0.3, width=30) or "" for b in range(0, 20)]
check("matrix: renders code glyphs", any(any(c in f for c in ultra_art._MTX_CHARS) for f in _mtx))
check("matrix: rain falls (frames vary)", len(set(_mtx)) > 1)

# safety: no raise, width-fit (<=48 budget), balanced markup, at every size/beat
for n in NAMES:
    worst, balanced, raised = 0, True, False
    for w in (8, 16, 24, 30, None):
        for b in range(0, 9):
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

# bg_pulse: the strictly-ADDITIVE full-page touch — EVERY ultra theme breathes its whole canvas in its
# own hue; base == theme bg; peak stays near-black (<=24/channel); non-ultra -> None; frozen under NO_ANIM.
# (Borders are NOT involved — they keep their own independent triangle breathe; nothing existing changed.)
def _maxch(h):
    h = h.lstrip("#"); return max(int(h[i:i + 2], 16) for i in (0, 2, 4))
_TB = {"ultra-dragon": studio_themes.ULTRA_DRAGON, "ultra-skynet": studio_themes.ULTRA_SKYNET,
       "ultra-synthwave": studio_themes.ULTRA_SYNTHWAVE, "ultra-matrix": studio_themes.ULTRA_MATRIX}
for n in NAMES:
    eff = ultra_art.EFFECTS[n]
    check("%s: opts into the canvas breath" % n, "bgpulse" in eff)
    check("%s: canvas base == theme bg" % n, eff["bgpulse"][0].lower() == _TB[n].background.lower())
    check("%s: canvas peak near-black (<=24/ch)" % n, _maxch(eff["bgpulse"][1]) <= 24)
_bp = [ultra_art.bg_pulse("ultra-synthwave", b * 0.5) for b in range(0, 40)]
check("bg_pulse: moves", all(_bp) and len(set(_bp)) > 3)
check("bg_pulse: non-ultra theme -> None", ultra_art.bg_pulse("pipboy", 3) is None)
os.environ["STUDIO_NO_ANIM"] = "1"
try:
    check("bg_pulse: frozen under STUDIO_NO_ANIM",
          ultra_art.bg_pulse("ultra-synthwave", 9) == ultra_art.bg_pulse("ultra-synthwave", 0))
finally:
    del os.environ["STUDIO_NO_ANIM"]

# ---- breakout effects: continuous border-breathe glow() + topbar wave electron_text() ----
check("EFFECTS covers exactly the ultra tier", set(ultra_art.EFFECTS) == set(studio_themes.ULTRA_NAMES))
check("glow: non-ultra -> None", ultra_art.glow("pipboy", 0) is None)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
for n in NAMES:
    cols = [ultra_art.glow(n, b) for b in range(0, 12)]
    check("%s: glow valid hex" % n, all(_HEX.match(c or "") for c in cols), cols)
    check("%s: glow breathes (distinct)" % n, len(set(cols)) > 1)
# glow is now CONTINUOUS (float clock) -> a fine sweep yields many shades, not a 5-color snap
_fine = {ultra_art.glow("ultra-synthwave", b * 0.1) for b in range(0, 90)}
check("glow interpolates continuously (>>5 shades over a cycle)", len(_fine) > 20, len(_fine))

_ITXT = "PIP-OS v8 :: JOB CONTROL\nConfigure a run and QUEUE it. cfg [1..7]"
# electron_text (the TOPBAR wave) — smooth float clock, bracket-safe, balanced
_wave = [ultra_art.electron_text(_ITXT, b * 0.13, "#e0a0d0", "#5cffe0", mode="wave") for b in range(0, 12)]
check("topbar wave never None", all(f is not None for f in _wave))
check("topbar wave moves (float clock)", len({repr(f) for f in _wave}) > 1)
check("topbar wave preserves plain text (bracket-safe)", all(_RT.from_markup(f).plain == _ITXT for f in _wave))
check("topbar wave balanced markup", all(f.count("[") == f.count("]") for f in _wave))

# render_electrons (the INFO touring electrons) — PURE fn of (text, heads)
check("electrons: empty heads -> all base (one color)",
      len(set(re.findall(r"#[0-9a-f]{6}", ultra_art.render_electrons(_ITXT, [], "#c9a24a", "#fff3c4")))) == 1)
_ea = ultra_art.render_electrons(_ITXT, [5.0], "#c9a24a", "#fff3c4")
_eb = ultra_art.render_electrons(_ITXT, [15.0], "#c9a24a", "#fff3c4")
check("electrons: comet moves with head", _ea != _eb)
check("electrons: pure (same heads reproducible)", _ea == ultra_art.render_electrons(_ITXT, [5.0], "#c9a24a", "#fff3c4"))
check("electrons: preserves plain text (bracket-safe)", _RT.from_markup(_ea).plain == _ITXT)
check("electrons: balanced markup", _ea.count("[") == _eb.count("]") or _ea.count("[") == _ea.count("]"))
check("electrons: multiple comets render (overlap)", ultra_art.render_electrons(_ITXT, [5.0, 20.0], "#c9a24a", "#fff3c4") is not None)

# freeze switch: STUDIO_NO_ANIM pins every theme to frame 0 (headless/reduce-motion)
os.environ["STUDIO_NO_ANIM"] = "1"
try:
    for n in NAMES:
        check("%s: STUDIO_NO_ANIM freezes to frame 0" % n,
              ultra_art.render(n, 9) == ultra_art.render(n, 0))
    check("glow frozen under STUDIO_NO_ANIM", ultra_art.glow("ultra-dragon", 9) == ultra_art.glow("ultra-dragon", 0))
    check("electron frozen under STUDIO_NO_ANIM",
          ultra_art.electron_text(_ITXT, 9, "#886644", "#ffeeaa") == ultra_art.electron_text(_ITXT, 0, "#886644", "#ffeeaa"))
finally:
    del os.environ["STUDIO_NO_ANIM"]

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
