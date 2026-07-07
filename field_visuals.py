"""VISUAL TOOLTIPS for the NEW RUN form — BF6-settings-style schematics.

When a form field is focused (or its ⓘ button is clicked), studio.py asks this module for a
small block-art SCHEMATIC illustrating what the setting does. It renders ABOVE the existing
text help (#newinfo), never instead of it.

CONTRACT (this is the surface Sonnet fills for the remaining fields):
  VISUALS = { field_id: render_fn }
  render_fn(app) -> Rich-markup STRING  (colored Unicode block-art, ~40-48 cols, a few rows,
                                          plus a one-line caption).
  render(field_id, app) -> str | None   (None when the field has no visual — the form then
                                          behaves exactly as before: text help only).

A render_fn may read live form state via `app.v("<field_id>")` (the studio's value getter) to
draw a value-aware marker, but MUST be defensive: any read can raise or return junk, so wrap it
and fall back to a static schematic. render() already swallows exceptions per-field, so a broken
visual degrades to "no visual" (text-only) rather than crashing the form.

PURE + import-light: stdlib only (no torch, no textual, no studio imports) so it stays trivial to
extend and safe to import from either venv.

PALETTE (green-phosphor, matches studio.py's Rich markup tags):
  [#6dffab] bright accent   [#9dffce] success/clean   [#34d977] mid green   [#1f9a52] dim green
  [#ffcf5c] warning/amber    [#ff6d6d] bad/fried/red   [dim] muted caption

LAYOUT IDIOM (every bar schematic, top to bottom — keep new visuals on this grid):
  bar row  ->  ▲ marker row  ->  zone-label row  ->  scale-tick row  ->
  '  ↑ now X  ·  hint' caption  ->  dim hint line(s).
  Uniform 2-space left margin; no line wider than 46 visible cols (2 margin + 42 bar + slack).
"""

import re

# ---- palette handles (keep the hex in ONE place so a Sonnet-authored visual stays on-brand) ----
ACCENT = "#6dffab"   # bright — highlights, current-value markers
CLEAN  = "#9dffce"   # the "good / clean" end of a scale
MID    = "#34d977"   # neutral mid green
DIM    = "#1f9a52"   # muted / low end
WARN   = "#ffcf5c"   # caution — getting too far
BAD    = "#ff6d6d"   # the "fried / broken" end (over-cooked, too high, too few)

# ---- theme hook -------------------------------------------------------------------------------
# semantic key (studio theme dict) -> module color global it drives
_SEMANTIC_TO_CONST = {
    "accent":     "ACCENT",   # #6dffab bright highlight / current-value markers
    "success":    "CLEAN",    # #9dffce the "good / clean" end of a scale
    "foreground": "MID",      # #34d977 neutral mid green
    "secondary":  "DIM",      # #1f9a52 muted / low end
    "warning":    "WARN",     # #ffcf5c caution
    "error":      "BAD",      # #ff6d6d fried / broken end
}

# the built-in pipboy palette, keyed semantically (pass to set_palette() to reset)
DEFAULT_PALETTE = {
    "accent":     "#6dffab",
    "success":    "#9dffce",
    "foreground": "#34d977",
    "secondary":  "#1f9a52",
    "warning":    "#ffcf5c",
    "error":      "#ff6d6d",
}


def set_palette(colors=None):
    """Rebind the module color globals from a semantic theme dict.

    `colors` maps semantic keys ("accent", "success", "foreground", "secondary", "warning",
    "error") to "#rrggbb" strings. Missing/blank keys keep their CURRENT values; unknown keys
    (e.g. "primary", "text_bright", "border" — no constant here uses them) are ignored.
    Defaults stay pipboy until the studio calls this; set_palette(DEFAULT_PALETTE) resets.
    """
    if not colors:
        return
    g = globals()
    for sem, const in _SEMANTIC_TO_CONST.items():
        val = colors.get(sem)
        if val:
            g[const] = str(val)


def _c(color, text):
    """Wrap `text` in a Rich color tag."""
    return "[%s]%s[/%s]" % (color, text, color)


def _num(app, field_id, default=None):
    """Read a numeric form value defensively -> float, or `default` if unreadable/blank/NaN."""
    try:
        raw = app.v(field_id)
    except Exception:
        return default
    try:
        if raw is None:
            return default
        s = str(raw).strip()
        if not s:
            return default
        return float(s)
    except Exception:
        return default


def _marker_row(pos, width, color=None, glyph="▲"):
    """A row with a single marker glyph at column `pos` (clamped into 0..width-1)."""
    if color is None:
        color = ACCENT          # late-bound so set_palette() retints the default marker too
    pos = 0 if pos < 0 else (width - 1 if pos > width - 1 else pos)
    return " " * pos + _c(color, glyph)


def _tick(v, lo, hi, width):
    """Bar column for value `v` on the lo..hi axis — the SAME mapping the ▲ marker uses, so
    scale ticks and markers can never drift apart."""
    if hi <= lo:
        return 0
    frac = (v - lo) / float(hi - lo)
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return int(round(frac * (width - 1)))


def _pin_row(width, pins):
    """One row of labels pinned to exact bar columns: pins = [(col, text, color), ...] where
    `col` is the column the label is CENTERED on (same column space as the ▲ marker). Labels
    are clamped into 0..width-1; a label that would touch an already-placed one is dropped
    (earlier pins win). Returns a Rich-markup string, right-trimmed."""
    cells = [(" ", None)] * width
    for col, text, color in pins:
        if not text or len(text) > width:
            continue
        start = int(col) - (len(text) - 1) // 2
        start = max(0, min(start, width - len(text)))
        lo, hi = max(0, start - 1), min(width, start + len(text) + 1)
        if any(ch != " " for ch, _cl in cells[lo:hi]):
            continue
        for i, ch in enumerate(text):
            cells[start + i] = (ch, color)
    end = width
    while end and cells[end - 1][0] == " ":
        end -= 1
    parts, i = [], 0
    while i < end:
        color = cells[i][1]
        j = i
        while j < end and cells[j][1] == color:
            j += 1
        run = "".join(ch for ch, _cl in cells[i:j])
        parts.append(_c(color, run) if color else run)
        i = j
    return "".join(parts)


def _caption(value, hint=None):
    """The standard caption line under a marker row: '  ↑ now <value>  ·  <hint>'.
    `value`/`hint` are pre-colored markup fragments; the scaffolding is dim. Keep the whole
    line <= 46 visible cols — the ↑ makes it alignment-locked art, so it is never wrapped."""
    s = _c("dim", "  ↑ now ") + value
    if hint:
        s += _c("dim", "  ·  ") + hint
    return s


# =====================================================================================
# EXEMPLAR 1 — GUIDANCE (cfg): wash -> good -> punchy -> fried, scale 1..8
# =====================================================================================
def _cfg(app):
    # zoned gradient bar across cfg 1..8. Each zone is a run of a block glyph in a palette color.
    #   1..2  wash  (░, too low -> flat / vague)
    #   3..4  good  (▒)
    #   5..6  punchy(█)
    #   7..8  fried (▉, too high -> neon / over-sharp / artifacts)
    LO, HI, WIDTH = 1.0, 8.0, 42          # cfg range mapped onto WIDTH columns
    segs = [                              # (upper_bound_inclusive, glyph, color)
        (2.0, "░", DIM),
        (4.0, "▒", MID),
        (6.0, "█", CLEAN),
        (8.0, "▉", BAD),
    ]
    bar = []
    for col in range(WIDTH):
        val = LO + (HI - LO) * col / (WIDTH - 1)
        for ub, glyph, color in segs:
            if val <= ub + 1e-9:
                bar.append(_c(color, glyph))
                break
        else:
            bar.append(_c(BAD, "▉"))
    lines = ["  " + "".join(bar)]

    cfg = _num(app, "cfg")
    if cfg is not None:
        lines.append("  " + _marker_row(_tick(cfg, LO, HI, WIDTH), WIDTH))
    # zone labels + scale ticks pinned to the SAME columns the ▲ marker maps to
    lines.append("  " + _pin_row(WIDTH, [(2, "wash", DIM), (11, "good", MID),
                                         (23, "punchy", CLEAN), (35, "fried", BAD)]))
    lines.append("  " + _pin_row(WIDTH, [(_tick(v, LO, HI, WIDTH), "%g" % v, "dim")
                                         for v in (1, 3, 5, 7)]
                                        + [(WIDTH - 1, "(CFG)", "dim")]))
    if cfg is not None:
        lines.append(_caption(_c(ACCENT, "%.3g" % cfg),
                              _c("dim", "low = vague, high = ") + _c(BAD, "fried")))
    else:
        lines.append(_c("dim", "  low = loose/vague · high = ") + _c(BAD, "fried, saturated"))
    return "\n".join(lines)


# =====================================================================================
# EXEMPLAR 2 — STEPS: few (noisy/blocky) -> many (clean/detailed), diminishing past ~30
# =====================================================================================
def _steps(app):
    # a quality ramp: left tiles are coarse/noisy, right tiles resolve to clean detail.
    # Each "tile" stands for a step budget; the glyph gets finer left->right.
    ramp = [                              # (upper_bound_steps, glyph, color)
        (8,  "░", BAD),
        (16, "▒", WARN),
        (24, "▓", MID),
        (30, "█", CLEAN),
        (40, "█", CLEAN),
        (50, "█", CLEAN),
    ]
    WIDTH = 42
    per = WIDTH // len(ramp)
    lines = ["  " + "".join(_c(color, glyph * per) for _n, glyph, color in ramp)]

    steps = _num(app, "steps")
    if steps is not None:
        # piecewise map the step count onto the TILE axis so the ▲ agrees with the tick row
        bounds = [0] + [n for n, _g, _cl in ramp]
        pos = WIDTH - 1
        for i in range(len(ramp)):
            if steps <= bounds[i + 1]:
                frac = max(0.0, (steps - bounds[i]) / float(bounds[i + 1] - bounds[i]))
                pos = int(round((i + frac) * per))
                break
        lines.append("  " + _marker_row(min(WIDTH - 1, pos), WIDTH))
    lines.append("  " + _pin_row(WIDTH, [(3, "noisy", BAD), (24, "clean", CLEAN),
                                         (38, "best", CLEAN)]))
    lines.append("  " + _pin_row(WIDTH, [((i + 1) * per, "%d" % n, "dim")
                                         for i, (n, _g, _cl) in enumerate(ramp)]))
    if steps is not None:
        lines.append(_caption(_c(ACCENT, "%g" % steps)))
    lines.append(_c("dim", "  few = ") + _c(BAD, "blocky/noisy")
                 + _c("dim", " → many = ") + _c(CLEAN, "clean/detailed"))
    lines.append(_c("dim", "  past ~30 = ") + _c(WARN, "diminishing returns, just slower"))
    return "\n".join(lines)


# =====================================================================================
# RES — the frame box: bigger = sharper. Peak VRAM is bounded (SAFE_PX working-set cap),
#       so higher res buys detail by SHRINKING the max clip/segment (more seams + time),
#       NOT by using more peak memory -> the VRAM meter stays ~flat as res changes.
# =====================================================================================
def _res(app):
    # three fixed tiers (matches studio.py RES dict) drawn as boxes of increasing size, small -> big.
    tiers = [
        ("512 x 320", "fast",     4, DIM),
        ("704 x 480", "balanced", 6, MID),
        ("768 x 512", "sharp",    8, BAD),
    ]
    rows = [[], [], []]
    for _label, _tag, w, color in tiers:
        rows[0].append(_c(color, "┌" + "─" * w + "┐"))
        rows[1].append(_c(color, "│" + " " * w + "│"))
        rows[2].append(_c(color, "└" + "─" * w + "┘"))
    lines = ["  " + "  ".join(r) for r in rows]
    # tag labels in cells as wide as their box (same 2-space gutters) -> columns line up
    lines.append("  " + "  ".join(_c(color, "%-*s" % (w + 2, tag))
                                  for (_label, tag, w, color) in tiers))

    res = None
    try:
        res = app.v("res")
    except Exception:
        res = None
    if res:
        s = str(res)
        col, total = 0, sum(t[2] + 2 for t in tiers) + 2 * (len(tiers) - 1)
        for label, _tag, w, _color in tiers:
            if s.startswith(label):
                lines.insert(3, "  " + _marker_row(col + (w + 2) // 2, total))
                lines.append(_caption(_c(ACCENT, s)))
                break
            col += w + 2 + 2
    lines.append(_c("dim", "  bigger box = ") + _c(MID, "sharper") + _c("dim", " + ")
                 + _c(BAD, "shorter max clip"))
    lines.append(_c("dim", "  peak VRAM ~flat — res trades ") + _c(WARN, "max clip len"))
    return "\n".join(lines)


# =====================================================================================
# BACKEND — speed <-> quality axis: LTX (fast) . wan-turbo (fast, distilled) . Wan (nicer)
# =====================================================================================
def _backend(app):
    WIDTH = 42
    # a speed<->quality axis, fast on the left, nicer/slower on the right
    bar = _c(DIM, "▓" * 10) + _c(MID, "▓" * 12) + _c(CLEAN, "▓" * 12) + _c(WARN, "▓" * 8)
    opts = [
        ("ltx",       "LTX-2B",    "fast draft",          5,  DIM),
        ("wan-turbo", "Wan-turbo", "fast 4-step distill", 20, MID),
        ("wan",       "Wan-VACE",  "slower, nicer",       36, CLEAN),
    ]

    backend = None
    try:
        backend = app.v("backend")
    except Exception:
        backend = None
    backend = str(backend or "ltx").strip().lower()

    marker = [" "] * WIDTH
    for key, _name, _tag, pos, color in opts:
        marker[pos] = _c(ACCENT, "▲") if key == backend else _c(color, "·")
    lines = ["  " + bar, "  " + "".join(marker)]
    lines.append("  " + _pin_row(WIDTH, [(5, "fast", "dim"), (36, "nicer (slower)", "dim")]))

    for key, name, tag, _pos, color in opts:    # A19: one item per line -> fits narrow panels
        tick = _c(ACCENT, "● ") if key == backend else "  "
        lines.append("  " + tick + _c(color, name) + _c("dim", " (%s)" % tag))
    return "\n".join(lines)


# =====================================================================================
# SECONDS — shot-chaining timeline: longer = more chained shots = more TIME, not more VRAM
# =====================================================================================
def _seconds(app):
    seconds = _num(app, "seconds")
    # Use the REAL plan (same nseg + frame-grid quantization as the QUEUE RUN plan line and the CLIP
    # gauge) so this schematic never disagrees with them. In single/auto-chain mode _plan ignores the
    # SEG field (segments come from the SAFE_PX cap), which is exactly why the old ceil(seconds/seg)
    # here diverged. Fall back to the rough estimate only if _plan is unavailable (fields mid-edit).
    n_shots, actual_s = 1, seconds
    try:
        _W, _H, fps, total_frames, _sf, nseg, _chain = app._plan()
        n_shots = max(1, int(nseg))
        if fps:
            actual_s = total_frames / float(fps)
    except Exception:
        seg = _num(app, "seg", 2.5) or 2.5
        if seconds is not None and seg > 0:
            n_shots = max(1, int(seconds / seg + 0.9999))

    MAXSHOTS = 6                            # A19: keep the chain row within a narrow panel
    shown = min(n_shots, MAXSHOTS)
    row = "  " + "─".join(_c(CLEAN if i == 0 else MID, "[███]") for i in range(shown))
    if n_shots > MAXSHOTS:
        row += _c("dim", " …+%d" % (n_shots - MAXSHOTS))

    lines = [row]
    if seconds is not None:
        # round EXACTLY like the QUEUE RUN plan line (round(_,1)) so the two never disagree by a digit
        req_r = round(float(seconds), 1)
        val = _c(ACCENT, "%gs" % req_r)
        if actual_s is not None:
            act_r = round(float(actual_s), 1)
            if abs(act_r - req_r) >= 0.05:       # frame-grid rounded the request -> show req → actual
                val += _c("dim", " → ") + _c(ACCENT, "%gs" % act_r)
        lines.append(_caption(val, _c("dim", "%d shot%s" % (n_shots, "" if n_shots == 1 else "s"))))
    lines.append(_c("dim", "  each shot = one SEGMENT pass, glued at tails"))
    lines.append(_c("dim", "  longer = more shots = more ") + _c(WARN, "TIME")
                 + _c("dim", ", not VRAM"))
    return "\n".join(lines)


# =====================================================================================
# FPS — choppy -> smooth motion ramp
# =====================================================================================
def _fps(app):
    LO, HI, WIDTH = 12.0, 36.0, 42
    segs = [
        (16.0, "░", BAD),
        (24.0, "▒", MID),
        (30.0, "▓", CLEAN),
        (36.0, "█", CLEAN),
    ]
    bar = []
    for col in range(WIDTH):
        val = LO + (HI - LO) * col / (WIDTH - 1)
        for ub, glyph, color in segs:
            if val <= ub + 1e-9:
                bar.append(_c(color, glyph))
                break
        else:
            bar.append(_c(CLEAN, "█"))
    lines = ["  " + "".join(bar)]

    fps = _num(app, "fps")
    if fps is not None:
        lines.append("  " + _marker_row(_tick(fps, LO, HI, WIDTH), WIDTH))
    lines.append("  " + _pin_row(WIDTH, [(3, "choppy", BAD), (14, "24 cinematic", MID),
                                         (31, "30+ smooth", CLEAN)]))
    lines.append("  " + _pin_row(WIDTH, [(_tick(v, LO, HI, WIDTH), "%g" % v, "dim")
                                         for v in (12, 18, 24, 30)]
                                        + [(WIDTH - 1, "(fps)", "dim")]))
    if fps is not None:
        lines.append(_caption(_c(ACCENT, "%g" % fps)))
    lines.append(_c("dim", "  note: Wan always renders native 16fps"))
    return "\n".join(lines)


# =====================================================================================
# SEG — per-shot length METER: the effective shot vs the SAFE_PX VRAM cap. The SEG field
#       is DIRECTOR-mode input only — in single/auto-chain the engine ignores it and every
#       shot is the cap itself. Numbers come from app._plan() (same source as QUEUE RUN's
#       plan line), so this meter can never disagree with it.
# =====================================================================================
def _seg(app):
    WIDTH = 42
    plan = None
    try:
        plan = app._plan()
    except Exception:
        plan = None
    if plan is None:
        # static fallback: form mid-edit — show the concept, no live numbers
        return "\n".join([
            "  " + _c(CLEAN, "█" * 26) + _c(DIM, "░" * 16),
            "  " + _pin_row(WIDTH, [(0, "0s", "dim"), (WIDTH - 1, "VRAM cap", "dim")]),
            _c("dim", "  per shot = min(SEG s, VRAM cap at this res)"),
            _c("dim", "  live numbers return once the form parses"),
        ])
    W, H, fps, _total, seg_frames, nseg, _chain = plan

    director = False
    try:
        director = str(app.v("mode") or "").strip().lower() == "director"
    except Exception:
        director = False
    wan = False
    try:
        wan = str(app.v("backend") or "ltx").strip().lower() in ("wan", "wan-turbo")
    except Exception:
        wan = False
    # the SAFE_PX working-set cap for the CURRENT res/backend — same math as app._plan()
    q = (lambda n: ((max(1, n) - 1) // 4) * 4 + 1) if wan else (lambda n: (max(1, n) // 8) * 8 + 1)
    cap = max(9, q(int(getattr(app, "SAFE_PX", 20_000_000) / float(W * H))))

    req_frames, seg_req = None, _num(app, "seg")
    if director and seg_req is not None and seg_req > 0:
        req_frames = q(int(round(seg_req * fps) if wan else int(seg_req * fps)))

    span = float(max(cap, req_frames or 0, seg_frames, 1))
    fill = max(1, min(WIDTH, int(round(WIDTH * seg_frames / span))))
    capcol = min(WIDTH, int(round(WIDTH * cap / span)))
    bar = []
    for col in range(WIDTH):
        if col < fill:
            bar.append(_c(CLEAN, "█"))       # the effective per-shot length
        elif col < capcol:
            bar.append(_c(DIM, "░"))         # headroom up to the VRAM cap
        else:
            bar.append(_c(BAD, "░"))         # past the cap — unreachable territory
    lines = ["  " + "".join(bar)]

    eff_s = round(seg_frames / float(fps), 1) if fps else 0.0
    cap_s = round(cap / float(fps), 1) if fps else 0.0
    shots = _c("dim", "%d shot%s" % (nseg, "" if nseg == 1 else "s"))
    scale = _pin_row(WIDTH, [(0, "0s", "dim"), (capcol - 1, "cap %gs" % cap_s, "dim")])

    if director and req_frames is not None:
        over = req_frames > cap
        lines.append("  " + _marker_row(int(round((req_frames / span) * (WIDTH - 1))),
                                        WIDTH, WARN if over else ACCENT))
        lines.append("  " + scale)
        if over:
            val = (_c(WARN, "%gs" % seg_req) + _c("dim", " → capped ")
                   + _c(ACCENT, "%df ≈ %gs" % (seg_frames, eff_s)))
        else:
            val = _c(ACCENT, "%gs → %df ≈ %gs" % (seg_req, seg_frames, eff_s))
        lines.append(_caption(val, shots))
        lines.append(_c("dim", "  shorter shots = more seams to hide"))
    else:
        lines.append("  " + scale)
        lines.append(_c("dim", "  single/auto-chain: SEG is AUTO = VRAM cap"))
        lines.append(_c("dim", "  (field inactive — used in DIRECTOR mode)"))
        lines.append(_c("dim", "  auto shot = %df ≈ %gs @ %gfps  ·  " % (seg_frames, eff_s, fps))
                     + shots)
    lines.append(_c("dim", "  cap = what fits VRAM at this res/backend"))
    return "\n".join(lines)


# =====================================================================================
# COND_STRENGTH — how tightly each shot holds the previous frame
# =====================================================================================
def _cond_strength(app):
    LO, HI, WIDTH = 0.0, 1.0, 42
    segs = [
        (0.4, "░", BAD),
        (0.6, "▒", WARN),
        (0.8, "▓", MID),
        (1.0, "█", CLEAN),
    ]
    bar = []
    for col in range(WIDTH):
        val = LO + (HI - LO) * col / (WIDTH - 1)
        for ub, glyph, color in segs:
            if val <= ub + 1e-9:
                bar.append(_c(color, glyph))
                break
        else:
            bar.append(_c(CLEAN, "█"))
    lines = ["  " + "".join(bar)]

    val = _num(app, "cond_strength")
    if val is not None:
        lines.append("  " + _marker_row(_tick(val, LO, HI, WIDTH), WIDTH))
    lines.append("  " + _pin_row(WIDTH, [(8, "drifts freely", BAD),
                                         (37, "sticks tight", CLEAN)]))
    lines.append("  " + _pin_row(WIDTH, [(_tick(v, LO, HI, WIDTH), "%.1f" % v, "dim")
                                         for v in (0.0, 0.4, 0.6, 0.8, 1.0)]))
    if val is not None:
        lines.append(_caption(_c(ACCENT, "%.3g" % val)))
    lines.append(_c("dim", "  raise if subject drifts · lower if stuck"))
    return "\n".join(lines)


# =====================================================================================
# STEADINESS — camera energy: lots of motion/redirects <-> locked-off / hold
# =====================================================================================
def _steadiness(app):
    WIDTH = 42
    opts = [
        ("evolve",   "Evolve",   "journey / transform",   4,  BAD),
        ("balanced", "Balanced", "gentle variation",      19, MID),
        ("hold",     "Hold",     "faithful / locked-off", 34, CLEAN),
    ]
    bar = _c(BAD, "▓" * 12) + _c(MID, "▓" * 14) + _c(CLEAN, "▓" * 16)

    steadiness = None
    try:
        steadiness = app.v("steadiness")
    except Exception:
        steadiness = None
    steadiness = str(steadiness or "hold").strip().lower()

    marker = [" "] * WIDTH
    for key, _name, _tag, pos, color in opts:
        marker[pos] = _c(ACCENT, "▲") if key == steadiness else _c(color, "·")
    lines = ["  " + bar, "  " + "".join(marker)]
    lines.append("  " + _pin_row(WIDTH, [(6, "lots of motion", "dim"), (19, "gentle", "dim"),
                                         (33, "locked-off", "dim")]))

    for key, name, tag, _pos, color in opts:    # A19: one item per line -> fits narrow panels
        tick = _c(ACCENT, "● ") if key == steadiness else "  "
        lines.append("  " + tick + _c(color, name) + _c("dim", " (%s)" % tag))
    return "\n".join(lines)


# =====================================================================================
# CFG_RESCALE — the fry-fixer: over-saturated/fried -> corrected
# =====================================================================================
def _cfg_rescale(app):
    fried = (_c(BAD, "▉" * 10) + _c(WARN, "▓" * 10) + _c(CLEAN, "▒" * 10) + _c(DIM, "░" * 10))
    arrow = _c("dim", "  over-cooked ─────────────────▶ corrected")

    val = None
    try:
        val = app.v("cfg_rescale")
    except Exception:
        val = None
    val = str(val or "off").strip().lower()

    lines = ["  " + fried, arrow]
    tags = [("off", "as-is"), ("0.5", "mild"), ("0.7", "strong")]
    legend = []
    for key, tag in tags:
        tick = _c(ACCENT, "●") if key == val else _c("dim", "○")
        legend.append(tick + " " + _c(ACCENT if key == val else MID, key) + _c("dim", " (%s)" % tag))
    lines.append("  " + "   ".join(legend))
    lines.append(_c("dim", "  fixes fried output when GUIDANCE > ~3"))
    lines.append(_c("dim", "  LTX-2B + Wan only · no-op at GUIDANCE ≤ 1"))
    return "\n".join(lines)


# =====================================================================================
# CFG_INTERVAL — step timeline: WHICH denoising steps run guidance, for the CURRENT value.
#   off      -> every step            a:b   -> the a..b fraction of the step axis
#   "2"/"3"  -> every-Nth-step comb   other -> generic "on" (defensive)
# =====================================================================================
def _cfg_interval(app):
    WIDTH = 42

    raw = None
    try:
        raw = app.v("cfg_interval")
    except Exception:
        raw = None
    val = str(raw if raw is not None else "off").strip().lower()

    if val in ("", "off"):
        bar = _c(MID, "█" * WIDTH)
        cap = _c("dim", "off — guidance runs every step (default)")
    elif val.isdigit() and int(val) >= 2:
        n = int(val)                         # every-Nth-step comb: █ on, ░ skipped
        bar = "".join(_c(ACCENT, "█") if col % n == 0 else _c(DIM, "░")
                      for col in range(WIDTH))
        cap = (_c(ACCENT, "every %d%s step" % (n, {2: "nd", 3: "rd"}.get(n, "th")))
               + _c("dim", " — CFG on 1 step in %d" % n))
    else:
        m = re.match(r"^(\d*\.?\d+)\s*:\s*(\d*\.?\d+)$", val)
        if m:
            a = max(0.0, min(1.0, float(m.group(1))))
            b = max(a, min(1.0, float(m.group(2))))
            bar = "".join(_c(ACCENT, "█") if a <= (col + 0.5) / WIDTH <= b else _c(DIM, "░")
                          for col in range(WIDTH))
            cap = (_c(ACCENT, "on %s" % val)
                   + _c("dim", " — CFG on %d–%d%% of the steps"
                        % (int(round(a * 100)), int(round(b * 100)))))
        else:                                # unknown token -> generic on (defensive)
            bar = _c(ACCENT, "█" * WIDTH)
            cap = _c(ACCENT, "on (%s)" % val[:12]) + _c("dim", " — custom schedule")

    lines = ["  " + bar]
    lines.append("  " + _pin_row(WIDTH, [(0, "step 0", "dim"), (WIDTH - 1, "step N", "dim")]))
    lines.append("  " + cap)
    lines.append("  " + _c(ACCENT, "█") + _c("dim", " = CFG on   ")
                 + _c(DIM, "░") + _c("dim", " = uncond pass skipped = faster"))
    lines.append(_c("dim", "  LTX-2B + Wan only · no-op: CFG ≤ 1, wan-turbo"))
    return "\n".join(lines)


# =====================================================================================
# WAN_REF_ANCHOR — shot-1 identity held across shots vs drifting/morphing
# =====================================================================================
def _wan_ref_anchor(app):
    val = None
    try:
        val = app.v("wan_ref_anchor")
    except Exception:
        val = None
    on = str(val or "off").strip().lower() == "on"

    if on:
        chain = (_c(CLEAN, "[◆]") + _c(DIM, "──")) * 3 + _c(CLEAN, "[◆]")
        lines = [
            "  " + chain,
            "   " + _c(DIM, "│") + "    " + _c(ACCENT, "↑    ↑    ↑"),
            "   " + _c(DIM, "└────┴────┴────┘") + _c("dim", "  anchored to shot 1"),
            "  " + _c(ACCENT, "on") + _c("dim", " — every shot pinned back to shot 1"),
        ]
    else:
        chain = (_c(CLEAN, "[◆]") + _c(DIM, "──") + _c(WARN, "[◇]") + _c(DIM, "──")
                 + _c(BAD, "[◈]") + _c(DIM, "──") + _c(BAD, "[?]"))
        lines = [
            "  " + chain,
            "  " + _pin_row(20, [(1, "shot 1", CLEAN), (16, "morphed", BAD)]),
            "  " + _c("dim", "off — each shot only sees the previous shot"),
        ]
    lines.append(_c("dim", "  Wan backends only · chained (multi-shot) runs"))
    return "\n".join(lines)


# =====================================================================================
# SEED — reproducibility card: same seed -> same clip, change -> new take
# =====================================================================================
def _seed(app):
    seed = None
    try:
        seed = app.v("seed")
    except Exception:
        seed = None
    seed_s = str(seed).strip() if seed not in (None, "") else ""

    top = "┌" + "─" * 10 + "┐"              # all four boxes share one 12-col footprint
    bot = "└" + "─" * 10 + "┘"
    arrow = " " + "─" * 14 + "▶ "           # 17-col connector, same width as the gap labels
    shown = (seed_s[:4] or "?")

    lines = [
        "  " + _c(CLEAN, top) + _c("dim", "  same settings  ") + _c(CLEAN, top),
        "  " + _c(CLEAN, "│ seed %-4s│" % shown) + _c("dim", arrow) + _c(CLEAN, "│ identical│"),
        "  " + _c(CLEAN, bot) + " " * 17 + _c(CLEAN, bot),
        "",
        "  " + _c(WARN, top) + _c("dim", " any other seed  ") + _c(WARN, top),
        "  " + _c(WARN, "│ seed ??? │") + _c("dim", arrow) + _c(WARN, "│ new take │"),
        "  " + _c(WARN, bot) + " " * 17 + _c(WARN, bot),
    ]
    if seed_s:
        lines.append(_caption(_c(ACCENT, "seed=%s" % seed_s[:10])))
    else:
        lines.append(_caption(_c(WARN, "blank"), _c("dim", "random seed every run")))
    lines.append(_c("dim", "  fix the seed to change ONE thing at a time"))
    return "\n".join(lines)


# ---- registry: field_id -> render_fn. Sonnet extends THIS dict for the other fields. ----
VISUALS = {
    "cfg": _cfg,
    "steps": _steps,
    "res": _res,
    "backend": _backend,
    "seconds": _seconds,
    "fps": _fps,
    "seg": _seg,
    "cond_strength": _cond_strength,
    "steadiness": _steadiness,
    "cfg_rescale": _cfg_rescale,
    "cfg_interval": _cfg_interval,
    "wan_ref_anchor": _wan_ref_anchor,
    "seed": _seed,
}


# =====================================================================================
# A19: markup-safe width fitting — word-wrap long PROSE caption lines so a schematic never
# overflows its panel. Column-aligned art (bars / scale ticks / rulers / legends, which use
# runs of >=3 spaces to line up) is left UNTOUCHED so wrapping can't break the alignment.
# =====================================================================================
# Match ONLY real color tags ([#rrggbb] / [dim] / their /closers). Literal brackets in the art
# (the "[███]" shot boxes, "[◆]" chain nodes, "[?]") are NOT tags -> left as plain text.
_TAGRE = re.compile(r"(\[/?(?:#[0-9A-Fa-f]{6}|[a-z][a-z0-9_]*)\])")
# glyphs that mean "this line is column-aligned art" (bars, boxes, markers, chains) -> never wrap
_ART = set("░▒▓█▉│└┌┐┘─▲▶◀◆◇◈○●↑[]")


def _vis_runs(s):
    """Resolve a Rich-markup string into visible (color|None, text) runs (color = active tag)."""
    stack, out = [], []
    for tok in _TAGRE.split(s):
        if not tok:
            continue
        if tok[0] == "[" and tok[-1] == "]":
            if tok[1:2] == "/":
                if stack:
                    stack.pop()
            else:
                stack.append(tok[1:-1])
        else:
            out.append((stack[-1] if stack else None, tok))
    return out


def _vis_len(s):
    return sum(len(t) for _, t in _vis_runs(s))


def _is_prose(s):
    """Flowing prose (wrap-safe) vs column-aligned art. A run of 3+ spaces (scale ticks, rulers,
    legends) OR any bar/box/marker glyph means it's alignment-locked art -> do not touch."""
    plain = "".join(t for _, t in _vis_runs(s))
    if "   " in plain:
        return False
    return not any(ch in _ART for ch in plain)


def _fit_line(s, width):
    """Word-wrap ONE prose line to <= `width` visible cols, markup-safe (color tags preserved,
    reopened on each wrapped continuation which is indented 2). Aligned / short lines pass through."""
    try:
        if _vis_len(s) <= width or width < 12 or not _is_prose(s):
            return s
        toks = []
        for color, text in _vis_runs(s):
            for piece in re.split(r"(\s+)", text):
                if piece:
                    toks.append((color, piece))
        out, cur, curlen = [], [], 0
        for color, piece in toks:
            if curlen == 0 and piece.isspace():
                continue                                     # no leading space on a fresh line
            if curlen + len(piece) > width and cur:
                out.append("".join(_c(c, t) if c else t for c, t in cur))
                cur, curlen = [(None, "  ")], 2              # 2-space continuation indent
                if piece.isspace():
                    continue
            cur.append((color, piece))
            curlen += len(piece)
        if cur:
            out.append("".join(_c(c, t) if c else t for c, t in cur))
        return "\n".join(out)
    except Exception:
        return s


def render(field_id, app, width=None):
    """Return the schematic STRING for `field_id`, or None if there is no visual (or it failed).

    `width` = the panel's content width in cols (studio may pass its measurement); long prose
    caption lines are word-wrapped to fit so they never overflow the panel. Defaults to 48 (the
    module's documented target) when unknown. Defensive by contract: a render_fn that raises
    degrades to None (text-only help), so a bad visual can never crash the NEW RUN form.
    """
    fn = VISUALS.get(field_id)
    if fn is None:
        return None
    try:
        out = fn(app)
    except Exception:
        return None
    if not out or not str(out).strip():
        return None
    try:
        w = int(width) if (width and int(width) >= 24) else 48
        out = "\n".join(_fit_line(ln, w) for ln in out.split("\n"))
    except Exception:
        pass
    return out
