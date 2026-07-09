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

# ordered emission ramps: deep -> hot. The sprite glow-sweep/pulse (dragon, T-800, synth sun) index
# into these.
RAMPS = {
    "GOLD":  ["#b8860b", "#d9a520", "#ffd24a", "#ffe89a", "#fff3c4"],   # dragon gold
    "RED":   ["#7a1010", "#b01818", "#e02020", "#ff3030", "#ff6a4a"],   # T-800 eyes
    "SYNTH": ["#ffd24a", "#ff9a3d", "#ff6a5a", "#ff3d7a", "#ff2d95"],   # synthwave sun (top->bottom)
}

# Per-ultra-theme INFO-panel running-light (the flourish that escapes the decoration box, opt-in):
#   mode/base/hot = "electron" (a bright comet + tail travels the text) or "wave" (a smooth traveling
#   brightness wave). base = readable resting color, hot = the crest.
# (Panel borders are STATIC — the border-breathe was removed 2026-07-08 at the user's request.
#  The whole-canvas "full-page touch" was removed 2026-07-07 — being redesigned; awaiting approval.)
EFFECTS = {
    "ultra-dragon":    {"mode": "electron", "base": "#c9a24a", "hot": "#fff3c4"},
    "ultra-skynet":    {"mode": "electron", "base": "#b0b8c0", "hot": "#ff6a5a"},
    "ultra-synthwave": {"mode": "wave",     "base": "#e0a0d0", "hot": "#5cffe0"},
    "ultra-matrix":    {"mode": "electron", "base": "#5ab86a", "hot": "#d8ffe0"},
    "ultra-tv":        {"mode": "wave",     "base": "#9aa4ae", "hot": "#f0f4f8"},
    "ultra-sonar":     {"mode": "wave",     "base": "#4ac0d0", "hot": "#c4faff"},
    "ultra-kaiju":     {"mode": "electron", "base": "#6a8aa8", "hot": "#eaf6ff"},
    "ultra-aurora":    {"mode": "wave",     "base": "#4ac080", "hot": "#b8ffd8"},
    "ultra-vhs":       {"mode": "electron", "base": "#7a8ab8", "hot": "#eef2f8"},
    "ultra-vaporwave": {"mode": "wave",     "base": "#d888c8", "hot": "#ffd0ec"},
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


# ---------------------------------------------------------------- signature moments ---------------
# The tier-wide spark: every ultra theme carries one rare, understated PAYOFF — a shooting star
# over the synthwave grid, a second sonar contact, a tracking glitch on the VHS, the dead channel
# cutting to colour... One shared scheduler drives them all, so the whole tier breathes on the
# same irregular pulse. (Kaiju's charge-and-fire breath cycle predates this and IS its moment.)
# Deterministic — a pure hash of the beat window, no random state — so headless tests reproduce.
# Dials (beat units; the ultra clock advances 2 units per real second, so 44.0 units = 22 s):
MOMENT_WIN = 44.0           # scheduling window: at most one moment per window (~22 s)
MOMENT_SKIP = 3             # ~1-in-N windows stay quiet -> gaps drift irregularly (~20-80 s)
MOMENT_RAMP = 0.8           # fade-in/out span (units) — moments GLIDE in and out, never pop


def _moment(beat, salt, win=None, dur=(2.0, 5.0), skip=None, ramp=None):
    """The shared signature-moment scheduler. Returns (intensity, phase) at `beat` for the stream
    `salt`: intensity is a smooth 0..1 trapezoid envelope (ramp in, hold, ramp out) and phase runs
    0..1 across the whole moment (for travelling payloads like a meteor). Each `win`-unit window
    holds at most one moment, at a hashed offset with a hashed duration drawn from `dur`, and
    ~1-in-`skip` windows skip entirely, so the rhythm never turns metronomic. PURE fn of beat ->
    deterministic/testable; never raises (worst case (0.0, 0.0))."""
    try:
        b = float(beat)
        win = float(win if win is not None else MOMENT_WIN)
        skip = MOMENT_SKIP if skip is None else skip
        ramp = MOMENT_RAMP if ramp is None else ramp
        w = int(b // win)
        hh = ((w * 2654435761) ^ ((salt + 1) * 97531)) & 0xffffffff
        hh = ((hh ^ (hh >> 13)) * 1274126177) & 0xffffffff
        hh ^= hh >> 16
        if skip and (hh % skip) == 0:                     # a quiet window keeps the gaps organic
            return (0.0, 0.0)
        d = dur[0] + ((hh >> 8) % 997) / 996.0 * (dur[1] - dur[0])
        lead = max(0.0, win - d - 1.0)
        start = w * win + 0.5 + ((hh >> 18) % 997) / 996.0 * lead
        t = b - start
        if t < 0.0 or t > d:
            return (0.0, 0.0)
        r = max(0.05, min(ramp, d / 3.0))
        return (min(1.0, t / r, (d - t) / r), t / max(0.05, d))
    except Exception:
        return (0.0, 0.0)



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


def render_sprite(spec, beat, palette=None, cols=None, surge=0.0):
    """Render sprite `spec` at frame `beat` to a Rich-markup string. PURE fn of (spec, beat, palette).
    The pixels + glow-sweep index off int(beat) (8-bit, chunky); an optional `sparkle` field scatters
    a few TWINKLING atmosphere glyphs (theme embers / specks) across the transparent cells, animated
    smoothly off the float clock. `cols` centers the sprite. `surge` (0..1, the signature-moment
    envelope) swells the atmosphere: a second, denser sparkle set fades in and twinkles faster,
    then melts away — the dragon spits an ember flurry, the T-800's targeting HUD lights up. Never
    raises."""
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
            # During a signature-moment surge a SECOND ~1/7 set fades in (brightness scaled by the
            # envelope, so the flurry glides in and melts away — never pops).
            h = (x * 2246822519 + orow * 3266489917) & 0x7fffffff
            lit = (h % 26) == 0
            extra = surge > 0.0 and (h % 7) == 3
            if not spark or not (lit or extra):
                return " "
            tw = 0.5 + 0.5 * math.sin(bf * (0.3 if lit else 0.9) + x * 1.7 + orow * 2.3)
            gate = 0.5 if lit else (1.0 - 0.45 * surge)      # the extra set opens with the envelope
            if tw < gate:
                return " "
            f = (tw - gate) / max(0.05, 1.0 - gate)
            if not lit:
                f *= surge                                   # never brighter than the moment allows
            g = sglyphs[(x * 3 + orow * 2) % len(sglyphs)]   # a given spot keeps its glyph; only brightness twinkles
            c = _lerp(spark.get("dim", "#333333"), spark.get("hot", "#ffffff"), f)
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
        LEAD = 1.5                                        # smooth lead-in ahead of the head so the crest
        #                                                  glides across sub-char positions (no stepping)
        for h in (heads or ()):
            for gi in range(len(positions)):
                d = h - gi                                # >0 behind the head (tail); <0 ahead (lead-in)
                if 0.0 <= d < tail:
                    f = 1.0 - d / float(max(1, tail))     # trailing comet tail
                elif -LEAD < d < 0.0:
                    f = 1.0 + d / LEAD                     # leading edge fades IN as the head nears it
                else:
                    continue
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
        rows_l = []
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
            rows_l.append(row)
        m, ph = _moment(bf, 3, dur=(2.2, 3.2))          # signature moment: a shooting star
        if m > 0.0:
            fx = ph * (W + 8.0) - 4.0                   # float path: upper-left -> lower-right
            fy = 0.4 + ph * 1.8
            for k in range(4):                          # bright head + fading tail
                px, py = int(round(fx - k * 1.6)), int(round(fy - k * 0.35))
                if 0 <= px < W and 0 <= py < hz - 1 and rows_l[py][px] == " ":
                    c = _lerp("#243a66", "#eaf6ff", m * (1.0 - k / 4.0))
                    rows_l[py][px] = "[%s]%s[/%s]" % (c, "✦" if k == 0 else "·", c)
        return "\n".join("".join(r) for r in rows_l)
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
        m, _ph = _moment(b, 4, dur=(4.0, 7.0))          # signature moment: one column burns white-hot
        cxm = (int(b // MOMENT_WIN) * 7919) % W
        for x in range(W):
            speed = 0.7 + ((x * 37) % 5) * 0.16         # 0.70 .. 1.34 cells/unit
            trail = 2 + ((x * 13) % 3)                  # 2 .. 4
            period = H + trail + 3
            head = (b * speed + (x * 11) % period) % period
            for k in range(trail + 1):
                y = int(head) - k
                if 0 <= y < H:
                    col = "#d8ffe0" if k == 0 else _lerp("#0c3a16", "#39ff58", 1.0 - k / float(trail + 1))
                    if m > 0.0 and x == cxm:
                        col = _lerp(col, "#eaffee", 0.85 * m)
                    g = _MTX_CHARS[(x * 13 + y * 7 + bi) % nch]     # occasional in-place glyph flicker
                    grid[y][x] = "[%s]%s[/%s]" % (col, g, col)
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- tv (dead channel) ---------------
def _tv(beat, width=None):
    """A dead broadcast: soft greyscale LUMINANCE BANDS roll slowly up the screen (a failing
    vertical hold) over a static scanline weave, a thin tracking line riding the roll. At
    irregular moments the colour SMPTE card CUTS IN — NO SIGNAL — for ~0.5-1.5 s, flanked by a
    breath of static, then the channel goes dead again. All motion runs on float clocks with
    colour-lerp interpolation (the bands GLIDE; nothing steps). Pure fn of beat (2 units = 1 s)."""
    try:
        W = min(max(int(width or 32), 14), 44); H = 8
        b = float(beat); bi = int(b); nb = 7
        grid = [[" "] * W for _ in range(H)]
        # the cut: 0.5-1.5 s of colour every ~2-38 s (hashed offset in a 40-unit window, no skips)
        m, _ph = _moment(b, 9, win=40.0, dur=(1.0, 3.0), skip=0)
        if m >= 0.55:                                     # ---- the colour card (NO SIGNAL) ----
            bars = ["#dcdcdc", "#c8c81a", "#1ac8c8", "#1ac01a", "#c81ac8", "#c81a1a", "#1a1ad0"]
            low  = ["#1a1ad0", "#0b0b0b", "#c81ac8", "#0b0b0b", "#1ac8c8", "#0b0b0b", "#dcdcdc"]
            for y in range(H):
                pal = bars if y < H - 2 else low
                for x in range(W):
                    c = pal[min(nb - 1, x * nb // W)]
                    grid[y][x] = "[%s]█[/%s]" % (c, c)
            msg = " NO SIGNAL "; my = H // 2; mx = max(0, (W - len(msg)) // 2)
            for i, ch in enumerate(msg):
                if 0 <= mx + i < W:
                    grid[my][mx + i] = "[#ff6a6a on #0b0b0b]%s[/]" % ch
        elif m > 0.0:                                     # ---- a breath of static flanks the cut ----
            for y in range(H):
                for x in range(W):
                    h = (x * 92821 + y * 68917 + bi * 40503) & 0xff
                    v = int(0x28 + (h % 0x90) * (0.4 + 0.6 * m))
                    c = "#%02x%02x%02x" % (v, v, v)
                    grid[y][x] = "[%s]%s[/%s]" % (c, "▒░▓"[(h >> 3) % 3], c)
        else:                                             # ---- the dead channel (bands rolling up) ----
            roll = b * 0.22                               # slow vertical-hold drift (~16 s per cycle)
            tp = (H + 2.0) - ((b * 0.11) % (H + 4.0))     # tracking line: float row, rolls up slower
            for y in range(H):
                # two soft harmonics -> organic rolling luminance; colour-lerped so it GLIDES
                s = 0.5 + 0.35 * math.sin((y + roll) * 0.9) + 0.15 * math.sin((y + roll) * 2.3 + 1.7)
                tl = max(0.0, 1.0 - abs(y - tp))          # sub-row soft envelope for the tracking line
                for x in range(W):
                    e = 0.06 * math.sin(x * 0.35 + roll * 0.7)    # faint diagonal shear
                    v = max(0.0, min(1.0, s + e + 0.38 * tl))
                    c = _lerp("#14181d", "#9aa3ac", v)
                    grid[y][x] = "[%s]%s[/%s]" % (c, "█" if y % 2 else "▓", c)
        return chr(10).join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- sonar (submarine scope) ---------
def _sonar(beat, width=None):
    """A sonar scope: a radial sweep line rotating around the center with a FADING afterglow trail,
    faint range rings, and a target blip that PINGS (brightens) as the sweep passes it. Pure fn."""
    try:
        W = min(max(int(width or 32), 16), 44); H = 8
        b = float(beat)
        cx, cy = (W - 1) / 2.0, (H - 1) / 2.0
        R = min(cx, cy * 2.0)
        sweep = (b * 0.45) % (2 * math.pi)
        arc = 1.5
        dimc, ring, hot = "#0e4450", "#1aa0b8", "#c4faff"
        ba, br = 0.9, R * 0.62                           # target blip (fixed polar position)
        grid = [[" "] * W for _ in range(H)]
        for y in range(H):
            for x in range(W):
                dx, dy = x - cx, (y - cy) * 2.0
                r = math.hypot(dx, dy)
                if r > R + 0.5:
                    continue
                cell = None
                if abs(r - R) < 0.6 or abs(r - R * 0.55) < 0.6:
                    cell = (ring, "·")                   # range rings
                d = (sweep - math.atan2(dy, dx)) % (2 * math.pi)
                if d < arc:                              # sweep + afterglow
                    f = 1.0 - d / arc
                    cell = (_lerp(dimc, hot, f), "█" if d < 0.2 else ("▓" if f > 0.5 else "▒"))
                if cell:
                    grid[y][x] = "[%s]%s[/%s]" % (cell[0], cell[1], cell[0])
        bx = int(round(cx + br * math.cos(ba))); by = int(round(cy + br * math.sin(ba) / 2.0))
        pf = 1.0 - min(1.0, ((sweep - ba) % (2 * math.pi)) / 1.2)
        bc = _lerp("#1a6a54", "#7cffc8", pf)             # blip: faint always, bright on ping
        if 0 <= bx < W and 0 <= by < H:
            grid[by][bx] = "[%s]◉[/%s]" % (bc, bc)
        m, _ph = _moment(b, 5, dur=(8.0, 14.0))          # signature moment: a second contact
        if m > 0.05:
            ba2, br2 = 3.8, R * 0.38
            b2x = int(round(cx + br2 * math.cos(ba2))); b2y = int(round(cy + br2 * math.sin(ba2) / 2.0))
            pf2 = 1.0 - min(1.0, ((sweep - ba2) % (2 * math.pi)) / 1.2)
            c2 = _lerp("#0e4450", _lerp("#1a6a54", "#7cffc8", pf2), m)   # surfaces + sinks with the envelope
            if 0 <= b2x < W and 0 <= b2y < H:
                grid[b2y][b2x] = "[%s]○[/%s]" % (c2, c2)
        grid[int(round(cy))][int(round(cx))] = "[%s]+[/%s]" % (ring, ring)
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- kaiju (monster movie) -----------
def _kaiju(beat, width=None):
    """A Showa monster picture: a city skyline (flickering windows), a kaiju silhouette whose dorsal
    spines CHARGE blue over ~7s then FIRE an atomic-breath beam across the sky. Pure fn of beat."""
    try:
        W = min(max(int(width or 32), 20), 44); H = 8
        b = float(beat); bi = int(b)
        dark, window, spine_dim = "#1a1e26", "#c8a83a", "#164a6a"
        atomic, hot, red = "#8ad0ff", "#eaf6ff", "#e03a3a"
        grid = [[" "] * W for _ in range(H)]
        def put(y, x, ch, c):
            if 0 <= x < W and 0 <= y < H:
                grid[y][x] = "[%s]%s[/%s]" % (c, ch, c)
        for x in range(W):                              # skyline
            for k in range(1 + ((x * 37 + 7) % 3)):
                put(H - 1 - k, x, "█", dark)
            if (x * 13 + bi) % 9 == 0:
                put(H - 2, x, "▪", window)              # windows flicker
        cyc = (b * 0.5) % 10.0
        charge = min(1.0, cyc / 7.0)
        firing = 7.0 <= cyc < 8.2
        kx = W // 2 - 3
        for s, ry in (("  ██  ", 2), (" ████ ", 3), ("██████", 4), ("█ ██ █", 5)):
            for i, ch in enumerate(s):
                if ch == "█":
                    put(ry, kx + i, "█", dark)
        put(3, kx + 1, "▪", hot if firing else red); put(3, kx + 4, "▪", hot if firing else red)
        for sx in (kx - 1, kx + 1, kx + 3, kx + 5):     # dorsal spines charge blue
            put(1, sx, "▲", _lerp(spine_dim, hot if firing else atomic, charge))
        if firing:                                      # atomic-breath beam
            for x in range(0, kx):
                put(4, x, "═", hot if x % 2 else atomic)
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- aurora (northern lights) --------
def _aurora(beat, width=None):
    """Northern lights: shimmering vertical curtains (green at the horizon -> teal -> violet up top)
    that undulate and drift, twinkling stars above, a dark pine treeline below. Pure fn of beat."""
    try:
        W = min(max(int(width or 32), 16), 44); H = 8
        b = float(beat)
        grid = [[" "] * W for _ in range(H)]
        def put(y, x, ch, c):
            if 0 <= x < W and 0 <= y < H:
                grid[y][x] = "[%s]%s[/%s]" % (c, ch, c)
        horizon = H - 2
        for x in range(W):
            s = 0.5 + 0.5 * math.sin(x * 0.4 + b * 0.3)         # curtain brightness drifts
            htop = 1 + int(round(2.4 * (0.5 + 0.5 * math.sin(x * 0.22 - b * 0.2))))
            span = max(1, horizon - htop)
            for y in range(htop, horizon + 1):
                depth = (y - htop) / span                        # 0 top .. 1 horizon
                c = (_lerp("#7a3ad0", "#1fb6a0", depth * 2) if depth < 0.5
                     else _lerp("#1fb6a0", "#39ff8a", (depth - 0.5) * 2))
                inten = s * (0.4 + 0.6 * depth)
                if inten < 0.28:
                    continue
                ch = "▓" if inten > 0.7 else ("▒" if inten > 0.45 else "░")
                put(y, x, ch, _lerp("#0a1a20", c, inten))
        for y in range(0, 2):                                    # stars in the upper sky
            for x in range(W):
                if grid[y][x] == " " and (x * 7 + y * 13) % 17 == 0:
                    tw = 0.5 + 0.5 * math.sin(b * 0.3 + x * 1.1 + y * 2.0)
                    if tw > 0.6:
                        put(y, x, "·", _lerp("#24406a", "#cfeaff", (tw - 0.6) / 0.4))
        m, ph = _moment(b, 6, dur=(1.6, 2.6))                    # signature moment: a meteor
        if m > 0.0:
            fx = (1.0 - ph) * (W + 6.0) - 3.0                    # right -> left, shallow fall
            fy = 0.2 + ph * 1.6
            for k in range(4):
                px, py = int(round(fx + k * 1.5)), int(round(fy - k * 0.3))
                if 0 <= px < W and 0 <= py < 2 and grid[py][px] == " ":
                    put(py, px, "✦" if k == 0 else "·", _lerp("#24406a", "#e8f4ff", m * (1.0 - k / 4.0)))
        for x in range(W):                                       # pine treeline
            put(horizon + 1, x, "▲", "#0e3a1e")
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- vhs (camcorder viewfinder) ------
def _vhs(beat, width=None):
    """A camcorder viewfinder: a slow-blinking ● REC, an ACCURATE recording clock (SP m:ss counting
    up in real seconds), a ▶ PLAY tag, and a band of bluish TRACKING NOISE drifting up the frame at
    half speed. The band uses a float position + a soft-edged coverage envelope, so it glides
    smoothly (sub-row interpolated) rather than stepping. Pure fn of beat (the 15 fps ultra clock)."""
    try:
        W = min(max(int(width or 32), 18), 44); H = 8
        b = float(beat); bi = int(b)
        red, white, blue = "#ff3a3a", "#eef2f8", "#3f6ac8"
        grid = [[" "] * W for _ in range(H)]
        def put(y, x, ch, c):
            if 0 <= x < W and 0 <= y < H:
                grid[y][x] = "[%s]%s[/%s]" % (c, ch, c)
        if bi % 2 == 0:                           # ● REC blinks ~1Hz (was ~7.5Hz)
            put(0, 1, "●", red)
        for i, ch in enumerate("REC"):
            put(0, 3 + i, ch, white)
        t = int(b / 2.0)                                     # REAL elapsed seconds (_ultra_t advances 2 units/sec, so /2)
        ts = "SP %d:%02d" % (t // 60, t % 60)            # accurate recording clock (top-right)
        for i, ch in enumerate(ts):
            put(0, W - len(ts) - 1 + i, ch, white)
        for i, ch in enumerate("▶ PLAY"):
            put(H - 1, 1 + i, ch, blue)
        # tracking-noise band drifts up at HALF speed, sub-row interpolated: a float position and a
        # soft-edged vertical envelope (edge rows fade by coverage) so it glides instead of stepping.
        pos = (b * 0.5) % (H + 6)                         # 0.5 rows/frame -> ~half the old speed
        band_top = (H - 2) - pos                          # float; band occupies [band_top, band_top+2)
        for y in range(1, H - 1):
            cov = min(y + 1.0, band_top + 2.0) - max(float(y), band_top)
            if cov <= 0.02:
                continue
            fade = 0.4 + 0.6 * min(1.0, cov)
            for x in range(W):
                h = (x * 92821 + y * 68917 + bi * 40503) & 0xff
                if h % 2 == 0:
                    v = int((0x50 + (h % 0x80)) * fade)
                    c = "#%02x%02x%02x" % (v, v, min(255, v + 0x20))
                    put(y, x, "▒░▓"[(h >> 3) % 3], c)
        m, _ph = _moment(b, 7, dur=(1.2, 2.4))            # signature moment: a tracking glitch
        if m > 0.05:
            gy = 2 + (int(b // MOMENT_WIN) % 4)           # one fixed mid-frame row per moment
            for x in range(W):
                h = (x * 40503 + bi * 92821) & 0xff
                if h % 3 < 2:
                    v = int((0x70 + (h % 0x70)) * (0.3 + 0.7 * m))
                    put(gy, x, "▬▒▔"[(h >> 4) % 3], "#%02x%02x%02x" % (v, v, v))
        return chr(10).join("".join(r) for r in grid)
    except Exception:
        return ""

# ---------------------------------------------------------------- vaporwave (aesthetic) -----------
def _vaporwave(beat, width=None):
    """A E S T H E T I C: a classical marble bust silhouette over a slowly-scrolling pastel
    checkerboard floor, with a few dreamy sparkles drifting. Calm, slow. Pure fn of beat."""
    try:
        W = min(max(int(width or 32), 18), 44); H = 8
        b = float(beat); bi = int(b)
        pink, cyan, mag, marble = "#ff9ad8", "#7af0ff", "#c060c8", "#f0d8f0"
        grid = [[" "] * W for _ in range(H)]
        def put(y, x, ch, c):
            if 0 <= x < W and 0 <= y < H:
                grid[y][x] = "[%s]%s[/%s]" % (c, ch, c)
        off = bi // 2                                    # checkerboard floor scrolls slowly
        for y in (H - 2, H - 1):
            for x in range(W):
                if (((x + off) // 2) + y) % 2 == 0:
                    put(y, x, "█", _lerp(mag, cyan, x / float(W)))
        m, _ph = _moment(b, 8, dur=(2.5, 4.0))           # signature moment: the marble catches the light
        marble = _lerp(marble, "#fff6ff", 0.7 * m); pink = _lerp(pink, "#ffe2f2", 0.5 * m)
        bx = W // 2 - 3                                  # classical bust
        for s, ry in (("  ▄▄▄  ", 1), (" ▟███▙ ", 2), (" █████ ", 3), (" █████ ", 4), ("▟█████▙", 5)):
            for i, ch in enumerate(s):
                if ch != " ":
                    put(ry, bx + i, ch, marble if ry < 3 else pink)
        for i in range(3):                               # dreamy drifting sparkles
            sx = (i * 11 + bi // 3) % W
            sy = i % 3
            tw = 0.5 + 0.5 * math.sin(b * 0.3 + i * 2.0)
            if tw > 0.55 and grid[sy][sx] == " ":
                put(sy, sx, "✧", _lerp("#3a2a44", "#ffd0ec", (tw - 0.55) / 0.45))
        return "\n".join("".join(r) for r in grid)
    except Exception:
        return ""


# ---------------------------------------------------------------- public API ----------------------
THEMES = {
    "ultra-dragon": lambda b, w=None: render_sprite(DRAGON, b, cols=w, surge=_moment(b, 1)[0]),
    "ultra-skynet": lambda b, w=None: render_sprite(T800, b, cols=w, surge=_moment(b, 2)[0]),
    "ultra-synthwave": lambda b, w=None: _synthwave(b, width=w),
    "ultra-matrix": lambda b, w=None: _matrix_rain(b, width=w),
    "ultra-tv": lambda b, w=None: _tv(b, width=w),
    "ultra-sonar": lambda b, w=None: _sonar(b, width=w),
    "ultra-kaiju": lambda b, w=None: _kaiju(b, width=w),
    "ultra-aurora": lambda b, w=None: _aurora(b, width=w),
    "ultra-vhs": lambda b, w=None: _vhs(b, width=w),
    "ultra-vaporwave": lambda b, w=None: _vaporwave(b, width=w),
}
TITLES = {
    "ultra-dragon": "「 年 · YEAR OF THE DRAGON 」",
    "ultra-skynet": "「 SKYNET · T-800 」",
    "ultra-synthwave": "「 OUTRUN · SYNTHWAVE 」",
    "ultra-matrix": "「 THE MATRIX · 電 」",
    "ultra-tv": "「 TV · PLEASE STAND BY 」",
    "ultra-sonar": "「 SONAR · DEPTH 340 」",
    "ultra-kaiju": "「 怪獣 · TOKYO ALERT 」",
    "ultra-aurora": "「 AURORA · 69°N 」",
    "ultra-vhs": "「 ● REC · SP 」",
    "ultra-vaporwave": "「 ＶＡＰＯＲ · 純粋 」",
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
