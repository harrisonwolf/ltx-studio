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
import math
import os

SHIMMER_PERIOD = 2          # sprite shimmer/scroll cadence (pixel art — stays chunky)
GLOW_PERIOD = 5             # border-breathe divisor: bigger = slower, more gradual swell (~20s full cycle)

# ordered emission ramps: deep -> hot. sweep/pulse index into these.
RAMPS = {
    "GOLD":  ["#b8860b", "#d9a520", "#ffd24a", "#ffe89a", "#fff3c4"],   # dragon gold
    "RED":   ["#7a1010", "#b01818", "#e02020", "#ff3030", "#ff6a4a"],   # T-800 eyes
    "SYNTH": ["#ffd24a", "#ff9a3d", "#ff6a5a", "#ff3d7a", "#ff2d95"],   # synthwave sun (top->bottom)
    "SYNTHB": ["#7a2a7e", "#c42678", "#ff2d95", "#ff6ab0", "#5cffe0"],  # synthwave border breathe (magenta->cyan)
    "GREEN":  ["#0c3a16", "#177a2a", "#2eae3f", "#5bff77", "#b8ffc4"],  # matrix rain (deep -> bright green)
    "TVB":    ["#2a323a", "#4a5560", "#7a8894", "#b8c4d0", "#f0f4f8"],  # broadcast steel -> white
    "CHROME": ["#3a2e14", "#7a5a1a", "#c89a2a", "#e6c86a", "#fff0c0"],  # cassette chrome/tape-gold
    "CYAN":   ["#0a3a44", "#127a8a", "#1fb6d4", "#5ce0f0", "#c4faff"],  # sonar / scope cyan
    "ATOMIC": ["#0a2a44", "#154a7a", "#2a8ad0", "#6ac0ff", "#d0ecff"],  # kaiju atomic-blue
    "AURORA": ["#0a3a2a", "#177a4a", "#2ec06a", "#6affaa", "#b8ffd8"],  # aurora green->mint
    "VHSB":   ["#1a2a4a", "#2a4a8a", "#3f6ac8", "#7a9ae0", "#d0e0ff"],  # vhs blue
    "AMBER2": ["#3a2606", "#7a4e12", "#c8901f", "#ffbf5c", "#ffe6b0"],  # blade-runner amber
    "ORCHID": ["#3a1a44", "#7a3a8a", "#c060c8", "#ff9ad8", "#ffd0ec"],  # vaporwave pink/orchid
}

# Per-ultra-theme "breakout" effects (the flourish that escapes the decoration box, opt-in per theme):
#   border  = which RAMP the panel borders breathe through (triangle wave — independent; NOT touched here)
#   mode/base/hot = the INFO-panel running-light: "electron" (a bright comet + tail travels the text)
#     or "wave" (a smooth traveling brightness wave). base = readable resting color, hot = the crest.
#   bgpulse = (base, peak) for the ever-so-subtle WHOLE-CANVAS breath — the ADDITIVE full-page touch.
#     Every ultra theme opts in, each toward its OWN hue; base MUST equal the theme background, and the
#     peak stays <=24/channel so even the swell is near-black. This is the ONLY strictly-additive full-
#     page surface (canvas bg) — it changes nothing else (borders/decoration/topbar/electrons untouched).
EFFECTS = {
    "ultra-dragon":    {"border": "GOLD",   "mode": "electron", "base": "#c9a24a", "hot": "#fff3c4",
                        "bgpulse": ("#0c0404", "#180a06")},   # black -> faint crimson-warm swell
    "ultra-skynet":    {"border": "RED",    "mode": "electron", "base": "#b0b8c0", "hot": "#ff6a5a",
                        "bgpulse": ("#060708", "#0e1218")},   # black -> faint cold steel-blue swell
    "ultra-synthwave": {"border": "SYNTHB", "mode": "wave", "base": "#e0a0d0", "hot": "#5cffe0",
                        "bgpulse": ("#08040f", "#0c0618")},   # black -> faint indigo swell (unchanged)
    "ultra-matrix":    {"border": "GREEN",  "mode": "electron", "base": "#5ab86a", "hot": "#d8ffe0",
                        "bgpulse": ("#020402", "#06180a")},   # black -> faint green swell
    "ultra-arcade":    {"border": "GREEN",  "mode": "electron", "base": "#5ab86a", "hot": "#d8ffe0",
                        "bgpulse": ("#030a04", "#06180a")},   # black -> faint green swell
    "ultra-tv":        {"border": "TVB",    "mode": "wave",     "base": "#9aa4ae", "hot": "#f0f4f8",
                        "bgpulse": ("#05070a", "#0a0e18")},   # black -> faint cool broadcast swell
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
    """Render sprite `spec` at frame `beat` to a Rich-markup string. PURE fn of (spec, beat, palette).
    The pixels + glow-sweep index off int(beat) (8-bit, chunky); an optional `sparkle` field scatters
    a few TWINKLING atmosphere glyphs (theme embers / specks) across the transparent cells, animated
    smoothly off the float clock. `cols` centers the sprite. Never raises."""
    try:
        pal = dict(spec.get("pal", {}))
        if palette:
            pal.update(palette)
        glow = spec.get("glow") or {}
        gchars = set(glow.get("chars", ""))
        rows = list(spec.get("rows", []))
        span = max((len(r) for r in rows), default=0)
        bi = int(beat)                                  # pixels + glow sweep (chunky)
        bf = float(beat)                                # sparkle twinkle (smooth)
        spark = spec.get("sparkle")

        def color(ch, x):
            if ch in (".", " ", ""):
                return None
            if ch in gchars:
                return _glow_color(x, bi, glow, span)
            return pal.get(ch)                          # unknown -> None (transparent) = safe

        sglyphs = (spark or {}).get("glyphs") or "·"    # per-theme atmosphere glyph SET (varied)

        def sparkle(x, orow):                           # a twinkling atmosphere glyph in a transparent cell
            # scrambled hash (not a linear modulus) -> an organic scatter, not a grid. ~1/26 cells (sparse
            # -> calm, not busy); each glyph pulses SLOWLY (~10s) and staggered -> soothing, not blinking.
            if not spark or ((x * 2246822519 + orow * 3266489917) & 0x7fffffff) % 26:
                return " "
            tw = 0.5 + 0.5 * math.sin(bf * 0.3 + x * 1.7 + orow * 2.3)
            if tw < 0.5:
                return " "
            g = sglyphs[(x * 3 + orow * 2) % len(sglyphs)]   # a given spot keeps its glyph; only brightness twinkles
            c = _lerp(spark.get("dim", "#333333"), spark.get("hot", "#ffffff"), (tw - 0.5) / 0.5)
            return "[%s]%s[/%s]" % (c, g, c)

        if len(rows) % 2:
            rows.append("")
        wide = cols if (cols and cols > span) else span   # fill the whole box so sparkles drift AROUND the sprite
        pad = (wide - span) // 2                           # centered sprite; the surrounding void gets atmosphere
        out = []
        for i in range(0, len(rows), 2):
            orow = i // 2
            top, bot = rows[i], rows[i + 1]
            cells = []
            for X in range(wide):
                x = X - pad
                if 0 <= x < span:
                    tc = color(top[x] if x < len(top) else " ", x)
                    bc = color(bot[x] if x < len(bot) else " ", x)
                    cells.append(sparkle(X, orow) if (tc is None and bc is None) else _cell(tc, bc))
                else:
                    cells.append(sparkle(X, orow))          # the space around the sprite -> drifting sparkles
            out.append("".join(cells))
        return "\n".join(out)
    except Exception:
        return ""


# ---------------------------------------------------------------- breakout effects ----------------
def _rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _lerp(a, b, f):
    """Linear RGB blend a->b at fraction f (clamped)."""
    f = max(0.0, min(1.0, f))
    ca, cb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(round(ca[i] + (cb[i] - ca[i]) * f)) for i in range(3))


def glow(theme_name, beat):
    """Breathing border color for an ultra theme at `beat` — a triangle wave up and down its border
    RAMP, INTERPOLATED continuously between ramp stops so a fast (float) clock breathes smoothly
    instead of snapping through 5 colors. Returns a hex string, or None if not an ultra theme. Pure
    fn of a float beat (STUDIO_NO_ANIM -> frame 0)."""
    eff = EFFECTS.get(theme_name)
    if not eff:
        return None
    b = 0.0 if _frozen() else float(beat)
    ramp = RAMPS.get(eff["border"], RAMPS["GOLD"])
    n = len(ramp)
    if n == 1:
        return ramp[0]
    u = b / max(1, GLOW_PERIOD)                        # continuous phase (GLOW_PERIOD slows the swell)
    m = 2 * (n - 1)                                    # triangle period
    t = u % m
    pos = t if t <= (n - 1) else (m - t)              # continuous triangle 0..n-1..0
    lo = int(pos)
    hi = min(n - 1, lo + 1)
    return _lerp(ramp[lo], ramp[hi], pos - lo)


def bg_pulse(theme_name, beat):
    """An ever-so-subtle breathing of the whole canvas background for themes that opt in
    (EFFECTS[name]['bgpulse'] = (base_hex, peak_hex)). A slow, small sine swell — a hazy dim glow that
    never leaves near-black. Returns a hex, or None if the theme doesn't opt in. Pure fn; frozen->base."""
    eff = EFFECTS.get(theme_name)
    if not eff or "bgpulse" not in eff:
        return None
    base, peak = eff["bgpulse"]
    b = 0.0 if _frozen() else float(beat)
    f = 0.5 + 0.5 * math.sin(b * 0.2)                  # ~16s swell (b is 2 units/sec)
    return _lerp(base, peak, f)


def electron_text(text, beat, base, hot, mode="electron", speed=2, tail=6, wavelen=7, amp=1.0):
    """Overlay a moving running-light on plain `text` and return Rich markup. Two modes:
      'electron' — a bright crest + fading tail travels through the (non-space) characters.
      'wave'     — a smooth traveling brightness wave (several soft crests).
    PURE fn of a FLOAT beat (STUDIO_NO_ANIM -> frame 0) — a fine-grained clock makes the wave/comet
    travel smoothly. Spaces/newlines pass through uncolored; '[' is escaped so arbitrary help text
    can't break Rich markup. Never raises (returns None)."""
    try:
        b = 0.0 if _frozen() else float(beat)
        lines = str(text).split("\n")
        positions = [(li, ci) for li, l in enumerate(lines) for ci, ch in enumerate(l) if ch != " "]
        N = len(positions) or 1
        gcol = {}
        if mode == "wave":
            phase = b * 0.6 * max(1, speed)
            for gi, pos in enumerate(positions):
                f = 0.5 + 0.5 * math.sin(gi / float(max(1, wavelen)) - phase)
                gcol[pos] = _lerp(base, hot, f * f * amp)  # square sharpens crests; amp softens the swing
        else:                                             # electron comet
            crest = (b * max(1, speed)) % N
            for gi, pos in enumerate(positions):
                d = (crest - gi) % N
                gcol[pos] = _lerp(base, hot, 1.0 - d / float(max(1, tail))) if d < tail else base
        out = []
        for li, l in enumerate(lines):
            buf = []
            for ci, ch in enumerate(l):
                if ch == " ":
                    buf.append(" ")
                    continue
                c = gcol.get((li, ci), base)
                buf.append("[%s]%s[/%s]" % (c, ("\\[" if ch == "[" else ch), c))
            out.append("".join(buf))
        return "\n".join(out)
    except Exception:
        return None


def render_electrons(text, heads, base, hot, tail=6):
    """Render `text` in the dim resting `base` color with one or more bright ELECTRON comets at the
    given `heads` (each a float position over the non-space character index; a comet's crest is
    brightest and fades to `base` over `tail` chars BEHIND it). `heads` may be empty (all base — the
    calm between fires). PURE fn (no clock, no random — the studio owns the stochastic scheduling, so
    this stays deterministic/testable). Brackets escaped; spaces/newlines uncolored. Never raises."""
    try:
        lines = str(text).split("\n")
        positions = [(li, ci) for li, l in enumerate(lines) for ci, ch in enumerate(l) if ch != " "]
        bright = [0.0] * len(positions)
        for h in (heads or ()):
            for gi in range(len(positions)):
                d = h - gi                                # >0 = this char is behind the comet head
                if 0.0 <= d < tail:
                    f = 1.0 - d / float(max(1, tail))
                    if f > bright[gi]:
                        bright[gi] = f
        gcol = {pos: (_lerp(base, hot, bright[gi]) if bright[gi] > 0 else base)
                for gi, pos in enumerate(positions)}
        out = []
        for li, l in enumerate(lines):
            buf = []
            for ci, ch in enumerate(l):
                if ch == " ":
                    buf.append(" ")
                    continue
                c = gcol.get((li, ci), base)
                buf.append("[%s]%s[/%s]" % (c, ("\\[" if ch == "[" else ch), c))
            out.append("".join(buf))
        return "\n".join(out)
    except Exception:
        return None


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
    # gold embers/sparks spat off the dragon — a heavy firework spark, a four-point ember, a small mote
    "sparkle": {"glyphs": "✸✦⋆", "hot": "#ffd24a", "dim": "#5a3a08"},
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
    # red targeting-HUD marks scanning around the skull — a crosshair, a position reticle, a tick
    "sparkle": {"glyphs": "+⌖⊹", "hot": "#ff5a3a", "dim": "#3a1210"},
}


# ---------------------------------------------------------------- synthwave (procedural showpiece) -
def _synthwave(beat, width=None):
    """A neon sunset: a banded sun (yellow->magenta) above a perspective grid whose horizontal rules
    scroll downward on the beat, with a bright scanning band. Fills the box width. Pure fn of beat."""
    try:
        W = min(max(int(width or 32), 8), 44)
        H = 9
        b = int(beat)                                   # grid scroll (chunky)
        bf = float(beat)                                # star twinkle (smooth)
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
                for x in range(W):                      # a few slow, soothing stars in the empty night sky
                    if row[x] == " " and (x * 5 + y * 11) % 21 == 0:
                        tw = 0.5 + 0.5 * math.sin(bf * 0.3 + x * 1.3 + y * 2.1)
                        if tw > 0.55:
                            g = "✧✩·"[(x + y) % 3]      # bright four-point, open five-point, distant speck
                            c = _lerp("#243a66", "#bfe8ff", (tw - 0.55) / 0.45)
                            row[x] = "[%s]%s[/%s]" % (c, g, c)
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


# ---------------------------------------------------------------- matrix (procedural code rain) ---
_MTX_CHARS = "ｱｶｻﾀﾅﾊﾏﾔﾗﾜ0123456789ﾂｸｼﾈﾘ:=*+"   # half-width katakana + digits (all single-cell)


def _matrix_rain(beat, width=None):
    """Falling green code: every column runs a drop at a varied speed — a white-green LEADING glyph
    with a fading green trail behind it; glyphs flicker on a slow (int) clock while the drops fall
    smoothly (float clock). Fills the box width. PURE fn of beat (STUDIO_NO_ANIM -> frame 0)."""
    try:
        W = min(max(int(width or 32), 8), 44)
        H = 8
        b = float(beat)
        bi = int(b)
        nch = len(_MTX_CHARS)
        grid = [[" "] * W for _ in range(H)]
        for x in range(W):
            speed = 0.7 + ((x * 37) % 5) * 0.16         # 0.70 .. 1.34 cells/unit
            trail = 2 + ((x * 13) % 3)                  # 2 .. 4
            period = H + trail + 3
            head = (b * speed + (x * 11) % period) % period
            for k in range(trail + 1):
                y = int(head) - k
                if 0 <= y < H:
                    col = "#d8ffe0" if k == 0 else _lerp("#0c3a16", "#39ff58", 1.0 - k / float(trail + 1))
                    g = _MTX_CHARS[(x * 13 + y * 7 + bi) % nch]     # occasional in-place glyph flicker
                    grid[y][x] = "[%s]%s[/%s]" % (col, g, col)
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- arcade (space invaders) ---------
def _arcade(beat, width=None):
    """Space Invaders: 3 rows of little green invaders marching side-to-side (2-frame leg wiggle), a
    magenta UFO streaking across the top now and then, a white cannon firing the odd bullet. Pure fn."""
    try:
        W = min(max(int(width or 32), 10), 44)
        H = 8
        b = float(beat); bi = int(b)
        green, mag, white = "#39ff5a", "#ff2d95", "#eafff0"
        grid = [[" "] * W for _ in range(H)]
        def put(y, x, ch, c):
            if 0 <= x < W and 0 <= y < H:
                grid[y][x] = "[%s]%s[/%s]" % (c, ch, c)
        m = bi % 8
        dx = m if m <= 4 else 8 - m                     # triangle march 0..4..0
        legs = "▙▟" if (bi % 2) else "▛▜"               # 2-frame leg wiggle
        for ry in (1, 2, 3):
            x = 2 + dx
            while x + 1 < W - 1:
                put(ry, x, legs[0], green); put(ry, x + 1, legs[1], green)
                x += 4
        ut = b % (W + 26)                               # UFO passes, with a long gap between
        if ut < W + 2:
            for i, ch in enumerate("◖▬◗"):
                put(0, int(ut) - 2 + i, ch, mag)
        cx = W // 2
        for i, ch in enumerate("▟█▙"):
            put(H - 1, cx - 1 + i, ch, white)
        by = (H - 2) - (bi % (H + 4))                   # a lone bullet rising from the cannon
        put(by, cx, "│", white)
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- tv (SMPTE test card) ------------
def _tv(beat, width=None):
    """SMPTE color bars (7 bars + a lower pluge), a faint tracking line rolling up, and a brief STATIC
    SNOW burst every ~15s. The bars are the flair; the theme canvas stays near-black. Pure fn of beat."""
    try:
        W = min(max(int(width or 32), 14), 44); H = 8
        b = float(beat); bi = int(b)
        grid = [[" "] * W for _ in range(H)]
        if (bi % 30) < 2:                                # brief static burst
            for y in range(H):
                for x in range(W):
                    h = (x * 92821 + y * 68917 + bi * 40503) & 0x1ff
                    if h % 3 == 0:
                        v = 0x38 + (h % 0x90)
                        c = "#%02x%02x%02x" % (v, v, v)
                        grid[y][x] = "[%s]%s[/%s]" % (c, "░▒▓"[(h >> 3) % 3], c)
        else:
            bars = ["#dcdcdc", "#c8c81a", "#1ac8c8", "#1ac01a", "#c81ac8", "#c81a1a", "#1a1ad0"]
            low = ["#1a1ad0", "#0b0b0b", "#c81ac8", "#0b0b0b", "#1ac8c8", "#0b0b0b", "#dcdcdc"]
            nb = len(bars)
            for y in range(H):
                pal = bars if y < H - 2 else low
                for x in range(W):
                    c = pal[min(nb - 1, x * nb // W)]
                    grid[y][x] = "[%s]█[/%s]" % (c, c)
            ty = bi % (H * 3)
            if ty < H:
                for x in range(W):
                    if (x + bi) % 6 < 2:
                        grid[ty][x] = "[#f0f4f8]▁[/#f0f4f8]"
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- public API ----------------------
THEMES = {
    "ultra-dragon": lambda b, w=None: render_sprite(DRAGON, b, cols=w),
    "ultra-skynet": lambda b, w=None: render_sprite(T800, b, cols=w),
    "ultra-synthwave": lambda b, w=None: _synthwave(b, width=w),
    "ultra-matrix": lambda b, w=None: _matrix_rain(b, width=w),
    "ultra-arcade": lambda b, w=None: _arcade(b, width=w),
    "ultra-tv": lambda b, w=None: _tv(b, width=w),
}
TITLES = {
    "ultra-dragon": "「 年 · YEAR OF THE DRAGON 」",
    "ultra-skynet": "「 SKYNET · T-800 」",
    "ultra-synthwave": "「 OUTRUN · SYNTHWAVE 」",
    "ultra-matrix": "「 THE MATRIX · 電 」",
    "ultra-arcade": "「 SPACE INVADERS · INSERT COIN 」",
    "ultra-tv": "「 SMPTE · PLEASE STAND BY 」",
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
    b = 0.0 if _frozen() else float(beat)             # float clock -> smooth sparkle/star twinkle
    try:
        return fn(b, width)
    except Exception:
        return None
