#!/usr/bin/env python
"""ultra_art.py — animated 8-bit pixel-art decorations for the ULTRA-THEMES tier.

Sibling of field_visuals.py / preview_art.py: stdlib-only, torch/textual/studio-free, and it
NEVER raises (every entry point is wrapped -> returns "" or None on any error). The studio wires
it defensively (import -> None on failure) and paints its output the usual way: a Rich-markup
STRING -> Text.from_markup(no_wrap=True) -> Static.update, on the existing 0.5s tick/_beat cadence.

Design rules (see plan agile-tumbling-valley):
- Sprites are defined IN CODE (palette + pixel rows), not shipped as PNGs, so they are recolorable,
  diff-able, and can ANIMATE (the gold shimmer swaps palette entries per beat). Half-block glyphs
  (U+2580 / U+2584) pack two pixel-rows into one text row -> compact and crisp.
- All animation is a PURE function of the integer `beat` (so headless tests pass explicit beats and
  get deterministic output). STUDIO_NO_ANIM freezes to frame 0 (mirrors sounds.py's STUDIO_MUTE
  discipline) for stable tests/screenshots and as a user "reduce motion" switch.
- The base UI stays minimal: this only paints when an ultra theme is active AND on an opted-in
  surface. Shimmer lives inside the sprite markup only, never on CSS borders.
"""
import os

SHIMMER_PERIOD = 2          # advance the shimmer/scroll one step every 2nd beat (~1.0s at 0.5s tick)

# ordered emission ramps: deep -> hot. sweep/pulse index into these.
RAMPS = {
    "GOLD":  ["#b8860b", "#d9a520", "#ffd24a", "#ffe89a", "#fff3c4"],   # dragon gold
    "RED":   ["#7a1010", "#b01818", "#e02020", "#ff3030", "#ff6a4a"],   # T-800 eyes
    "SYNTH": ["#ffd24a", "#ff9a3d", "#ff6a5a", "#ff3d7a", "#ff2d95"],   # synthwave sun (top->bottom)
}

_PAL = {}                   # optional theme palette (set by set_palette); reserved for future re-tint


def set_palette(pal):
    """Optional hook mirrored from field_visuals — rebinds an active-theme palette the renderers MAY
    consult. Best-effort; the sprites carry their own fixed palettes today, so this is a no-op-safe
    stash for future themed recoloring."""
    try:
        _PAL.clear()
        _PAL.update(pal or {})
    except Exception:
        pass


def _frozen():
    """Freeze animation to frame 0 (headless tests / stable screenshots / user reduce-motion)."""
    return bool(os.environ.get("STUDIO_NO_ANIM"))


# ---------------------------------------------------------------- pixel-sprite renderer -----------
def _cell(top, bot):
    """One text cell = two vertically-stacked pixels via a half-block. top/bot are hex or None
    (None = transparent -> the host Static's $surface shows through, so we emit no bg clause)."""
    if top is None and bot is None:
        return " "
    if bot is None:
        return "[%s]▀[/%s]" % (top, top)          # upper half only
    if top is None:
        return "[%s]▄[/%s]" % (bot, bot)          # lower half only
    if top == bot:
        return "[%s]█[/%s]" % (top, top)          # full block
    return "[%s on %s]▀[/]" % (top, bot)          # both opaque: fg=top, bg=bot


def _glow_color(x, beat, glow, span):
    """The animated color for a glow pixel at column x. `sweep` = a bright crest travels across the
    row (dragon gold shines); `pulse` = all glow pixels breathe in unison (T-800 eyes). Pure fn of
    the integer beat via step = beat // period -> no sub-beat strobing."""
    ramp = RAMPS.get(glow.get("ramp"), RAMPS["GOLD"])
    n = len(ramp)
    step = beat // max(1, glow.get("period", SHIMMER_PERIOD))
    if glow.get("mode") == "pulse":
        m = 2 * (n - 1) or 1
        t = step % m
        idx = t if t < n else m - t                    # triangle wave 0..n-1..0
    else:                                              # sweep
        crest = step % (span + n)
        idx = (n - 1) - abs(x - crest)
    return ramp[max(0, min(n - 1, idx))]


def render_sprite(spec, beat, palette=None, cols=None):
    """Render sprite `spec` at animation frame `beat` to a Rich-markup string. PURE fn of
    (spec, beat, palette). `palette` overrides spec['pal'] (theme recolor); `cols` centers the sprite
    in a box that wide. Unknown/transparent chars ('.'/' ') -> transparent. Never raises."""
    try:
        pal = dict(spec.get("pal", {}))
        if palette:
            pal.update(palette)
        glow = spec.get("glow") or {}
        gchars = set(glow.get("chars", ""))
        rows = list(spec.get("rows", []))
        span = max((len(r) for r in rows), default=0)
        b = int(beat)

        def color(ch, x):
            if ch in (".", " ", ""):
                return None
            if ch in gchars:
                return _glow_color(x, b, glow, span)
            return pal.get(ch)                          # unknown -> None (transparent) = safe

        if len(rows) % 2:
            rows.append("")
        out = []
        for i in range(0, len(rows), 2):
            top, bot = rows[i], rows[i + 1]
            cells = []
            for x in range(span):
                tc = color(top[x] if x < len(top) else " ", x)
                bc = color(bot[x] if x < len(bot) else " ", x)
                cells.append(_cell(tc, bc))
            line = "".join(cells)
            if cols and cols > span:
                line = " " * ((cols - span) // 2) + line
            out.append(line)
        return "\n".join(out)
    except Exception:
        return ""


# ---------------------------------------------------------------- sprite specs --------------------
# Dragon head (side profile, facing right) — gold body with a crimson eye/whiskers; the gold "G"
# pixels shimmer as a bright crest sweeps across them.
DRAGON = {
    "pal": {
        "k": "#1a0a06",   # dark outline
        "o": "#7a4a10",   # shadow gold
        "w": "#fff3c4",   # hot glint (teeth)
        "r": "#c81e2a",   # crimson whisker/frill
        "R": "#ff3b3b",   # red eye
    },
    "rows": [             # 15 cols x 10 px -> 5 text rows ; G = animated gold (see glow)
        "....kkk........",
        "...kGGGk.......",
        "..kGGGGGkk.....",
        ".kGGGGGGGGkk...",
        ".kGRGGGGGGGGk..",
        "kGGGGGGGGGGGGGk",
        ".kGGGGGGGGwwwk.",
        "..kkGGGGkkk....",
        "....krrk.......",
        ".....rr........",
    ],
    "glow": {"chars": "G", "ramp": "GOLD", "mode": "sweep"},
}

# T-800 skull (front view) — chrome steel with two red eye clusters that pulse in unison.
T800 = {
    "pal": {
        "k": "#0a0d10",   # outline / sockets
        "s": "#5a636a",   # shadow steel
        "S": "#aeb6be",   # steel
        "H": "#e8eef2",   # highlight steel
    },
    "rows": [             # 14 cols x 12 px -> 6 text rows ; R = animated red eyes
        "..ssSSSSSss...",
        ".sSSSHHHSSSs..",
        ".sSSSSSSSSSSs.",
        "sSSkkkSSkkkSSs",
        "sSkRRkSSkRRkSs",
        "sSkRRkSSkRRkSs",
        "sSSkkSSSSkkSSs",
        ".sSSSkkkSSSSs.",
        ".sSSkSkSkSSs..",
        "..sSkSkSkSs...",
        "...kSkSkSk....",
        "....kSkSk.....",
    ],
    "glow": {"chars": "R", "ramp": "RED", "mode": "pulse"},
}


# ---------------------------------------------------------------- synthwave (procedural showpiece) -
def _synthwave(beat, width=None):
    """A neon sunset: a banded sun (yellow->magenta) above a perspective grid whose horizontal rules
    scroll downward on the beat, with a bright scanning band. Fills the box width. Pure fn of beat."""
    try:
        W = min(max(int(width or 32), 8), 44)
        H = 9
        b = int(beat)
        sun = RAMPS["SYNTH"]
        ns = len(sun)
        cyan, dim = "#2de0ff", "#1f6f88"
        cx = W // 2
        hz = 5                                          # horizon row -> 5 sun rows, 4 grid rows
        r = max(3, min(cx - 1, 4))                      # sun radius
        scroll = b // max(1, SHIMMER_PERIOD)
        out = []
        for y in range(H):
            row = [" "] * W
            if y < hz:                                  # ---- SUN (solid gradient disc) ----
                dy = hz - y                             # 5..1 above horizon
                gi = (ns - 1) - min(ns - 1, int(((dy - 1) / max(1, r)) * (ns - 1)))
                col = sun[max(0, min(ns - 1, gi))]      # top = yellow, bottom = magenta
                for x in range(W):
                    dx = x - cx
                    if dx * dx + dy * dy * 3 <= (r * r + r) * 3:
                        row[x] = "[%s]█[/%s]" % (col, col)
            else:                                       # ---- PERSPECTIVE GRID ----
                d = y - hz + 1                          # depth 1..4
                bright = ((d + scroll) % 3 == 0)        # one scrolling bright rule
                gcol = cyan if bright else dim
                glyph = "━" if bright else "─"
                for x in range(W):
                    row[x] = "[%s]%s[/%s]" % (gcol, glyph, gcol)
                for m in range(1, 5):                   # a clean 4-wide fan of converging verticals
                    off = m * d * 2
                    for xx in (cx - off, cx + off):
                        if 0 <= xx < W:
                            row[xx] = "[%s]│[/%s]" % (gcol, gcol)
            out.append("".join(row))
        return "\n".join(out)
    except Exception:
        return ""


# ---------------------------------------------------------------- public API ----------------------
THEMES = {
    "ultra-dragon": lambda b, w=None: render_sprite(DRAGON, b, cols=w),
    "ultra-skynet": lambda b, w=None: render_sprite(T800, b, cols=w),
    "ultra-synthwave": lambda b, w=None: _synthwave(b, width=w),
}
TITLES = {
    "ultra-dragon": "「 年 · YEAR OF THE DRAGON 」",
    "ultra-skynet": "「 SKYNET · T-800 」",
    "ultra-synthwave": "「 OUTRUN · SYNTHWAVE 」",
}


def is_ultra(theme_name):
    """True iff `theme_name` is an ultra theme with a registered decoration."""
    return theme_name in THEMES


def render(theme_name, beat, width=None):
    """Render an ultra theme's decoration at `beat`, or None if it isn't an ultra theme. Honors
    STUDIO_NO_ANIM (freeze to frame 0). Never raises."""
    fn = THEMES.get(theme_name)
    if fn is None:
        return None
    b = 0 if _frozen() else int(beat)
    try:
        return fn(b, width)
    except Exception:
        return None
