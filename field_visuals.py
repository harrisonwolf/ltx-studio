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
"""

import re

# ---- palette handles (keep the hex in ONE place so a Sonnet-authored visual stays on-brand) ----
ACCENT = "#6dffab"   # bright — highlights, current-value markers
CLEAN  = "#9dffce"   # the "good / clean" end of a scale
MID    = "#34d977"   # neutral mid green
DIM    = "#1f9a52"   # muted / low end
WARN   = "#ffcf5c"   # caution — getting too far
BAD    = "#ff6d6d"   # the "fried / broken" end (over-cooked, too high, too few)


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


def _marker_row(pos, width, color=ACCENT, glyph="▲"):
    """A row with a single marker glyph at column `pos` (clamped into 0..width-1)."""
    pos = 0 if pos < 0 else (width - 1 if pos > width - 1 else pos)
    return " " * pos + _c(color, glyph)


# =====================================================================================
# EXEMPLAR 1 — GUIDANCE (cfg): wash -> good -> punchy -> fried, scale 1..8
# =====================================================================================
def _cfg(app):
    # zoned gradient bar across cfg 1..8. Each zone is a run of a block glyph in a palette color.
    #   1..2  wash  (░, too low -> flat / vague)
    #   3..4  good  (▒)
    #   5..6  punchy(▓ -> █)
    #   7..8  fried (▉, too high -> neon / over-sharp / artifacts)
    LO, HI, WIDTH = 1.0, 8.0, 42          # cfg range mapped onto WIDTH columns
    # build the bar column-by-column so the value marker lines up exactly under the bar
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
    bar_str = "".join(bar)

    # zone labels under the bar (roughly aligned to each zone's centre)
    labels = (
        _c(DIM, "wash") + "      " + _c(MID, "good") + "    "
        + _c(CLEAN, "punchy") + "    " + _c(BAD, "fried")
    )
    scale = _c("dim", "1        3        5        7    8   (CFG)")

    lines = ["  " + bar_str, "  " + labels, "  " + scale]

    # value-aware marker: read live cfg, drop a ▲ under it
    cfg = _num(app, "cfg")
    if cfg is not None:
        frac = (cfg - LO) / (HI - LO)
        pos = int(round(frac * (WIDTH - 1)))
        lines.insert(1, "  " + _marker_row(pos, WIDTH, ACCENT, "▲"))
        cap_val = _c(ACCENT, "now %.3g" % cfg)
        lines.append(_c("dim", "  ↑ ") + cap_val
                     + _c("dim", "  ·  too low = vague, too high = ") + _c(BAD, "fried"))
    else:
        lines.append(_c("dim", "  low = loose/vague  ·  high = ") + _c(BAD, "over-cooked, saturated"))

    return "\n".join(lines)


# =====================================================================================
# EXEMPLAR 2 — STEPS: few (noisy/blocky) -> many (clean/detailed), diminishing past ~30
# =====================================================================================
def _steps(app):
    # a quality ramp: left tiles are coarse/noisy, right tiles resolve to clean detail.
    # Each "tile" stands for a step budget; the glyph gets finer left->right.
    ramp = [
        (8,  "░", BAD,   "noisy"),
        (16, "▒", WARN,  ""),
        (24, "▓", MID,   ""),
        (30, "█", CLEAN, "clean"),
        (40, "█", CLEAN, ""),
        (50, "█", CLEAN, "best"),
    ]
    WIDTH = 42
    per = WIDTH // len(ramp)
    bar = []
    for _n, glyph, color, _lab in ramp:
        bar.append(_c(color, glyph * per))
    bar_str = "".join(bar)

    # scale ticks under the tiles (step counts)
    scale = _c("dim", "8    16   24   30   40   50   (steps)")
    note = (_c("dim", "few = ") + _c(BAD, "blocky/noisy")
            + _c("dim", "  →  many = ") + _c(CLEAN, "clean/detailed"))
    dimin = _c("dim", "past ~30 = ") + _c(WARN, "diminishing returns, just slower")

    lines = ["  " + bar_str]

    steps = _num(app, "steps")
    if steps is not None:
        # map 0..~60 steps onto the bar for the marker (clamp beyond)
        SMAX = 60.0
        frac = max(0.0, min(1.0, steps / SMAX))
        pos = int(round(frac * (WIDTH - 1)))
        lines.append("  " + _marker_row(pos, WIDTH, ACCENT, "▲"))
        lines.append(_c("dim", "  ↑ ") + _c(ACCENT, "now %g" % steps))
    lines.append("  " + scale)
    lines.append("  " + note)
    lines.append("  " + dimin)
    return "\n".join(lines)


# =====================================================================================
# RES — the frame box: bigger = sharper but tighter on 8GB VRAM + shorter max clip
# =====================================================================================
def _res(app):
    # three fixed tiers (matches studio.py RES dict) drawn as boxes of increasing size, small -> big.
    tiers = [
        ("512 x 320", "fast",     4, DIM),
        ("704 x 480", "balanced", 6, MID),
        ("768 x 512", "sharp",    8, BAD),
    ]
    boxes = []
    for _label, _tag, w, color in tiers:
        boxes.append([
            _c(color, "┌" + "─" * w + "┐"),
            _c(color, "│" + " " * w + "│"),
            _c(color, "└" + "─" * w + "┘"),
        ])

    line1 = "  " + "  ".join(b[0] for b in boxes)
    line2 = "  " + "  ".join(b[1] for b in boxes)
    line3 = "  " + "  ".join(b[2] for b in boxes)
    labels = "  " + "  ".join(_c(c, "%-8s" % t) + " " * max(0, w - len(t) - 5)
                              for (_l, t, w, c) in tiers)

    lines = [line1, line2, line3, labels]

    res = None
    try:
        res = app.v("res")
    except Exception:
        res = None
    if res:
        s = str(res)
        col = 2
        for (label, _tag, w, _color) in tiers:
            box_width = w + 2  # incl the two border chars
            if s.startswith(label):
                lines.append(_c("dim", "  ↑ now ") + _c(ACCENT, s))
                break
            col += box_width + 2
    lines.append(_c("dim", "  bigger box = ") + _c(BAD, "more VRAM + shorter max clip"))
    return "\n".join(lines)


# =====================================================================================
# BACKEND — speed <-> quality axis: LTX (fast) . Wan (slower, nicer) . wan-turbo (fast, distilled)
# =====================================================================================
def _backend(app):
    WIDTH = 42
    # a speed<->quality axis, fast on the left, nicer/slower on the right
    bar = _c(DIM, "▓" * 10) + _c(MID, "▓" * 12) + _c(CLEAN, "▓" * 12) + _c(WARN, "▓" * 8)
    ruler = _c("dim", "fast" + " " * 15 + "slower" + " " * 13 + "nicer")

    opts = [
        ("ltx",       "LTX-2B",      "fast draft",         5,  DIM),
        ("wan-turbo", "Wan-turbo",   "fast 4-step distill", 20, MID),
        ("wan",       "Wan-VACE",    "slower, nicer",       36, CLEAN),
    ]

    backend = None
    try:
        backend = app.v("backend")
    except Exception:
        backend = None
    backend = (backend or "ltx").strip().lower()

    lines = ["  " + bar]
    marker = [" "] * WIDTH
    for key, _name, _tag, pos, color in opts:
        marker[pos] = _c(ACCENT, "▲") if key == backend else _c(color, "·")
    lines.append("  " + "".join(marker))
    lines.append("  " + ruler)

    legend = []
    for key, name, tag, _pos, color in opts:
        tick = _c(ACCENT, "● ") if key == backend else "  "
        legend.append(tick + _c(color, name) + _c("dim", " (%s)" % tag))
    for lg in legend:                       # A19: one item per line -> fits narrow panels
        lines.append("  " + lg)
    return "\n".join(lines)


# =====================================================================================
# SECONDS — shot-chaining timeline: longer = more chained shots = more TIME, not more VRAM
# =====================================================================================
def _seconds(app):
    seconds = _num(app, "seconds")
    seg = _num(app, "seg", 2.5) or 2.5
    n_shots = 1
    if seconds is not None and seg > 0:
        n_shots = max(1, int(seconds / seg + 0.9999))

    MAXSHOTS = 6                            # A19: keep the chain row within a narrow panel
    shown = min(n_shots, MAXSHOTS)
    shots = []
    for i in range(shown):
        shots.append(_c(CLEAN if i == 0 else MID, "[███]"))
    row = "  " + "─".join(shots)
    if n_shots > MAXSHOTS:
        row += _c("dim", " …+%d" % (n_shots - MAXSHOTS))

    lines = [row]
    if seconds is not None:
        lines.append(_c("dim", "  ↑ now ") + _c(ACCENT, "%.3gs" % seconds)
                      + _c("dim", "  ≈ %d shot%s chained" % (n_shots, "" if n_shots == 1 else "s")))
    lines.append(_c("dim", "  each shot = one SEGMENT pass, glued at the tails"))
    lines.append(_c("dim", "  longer = more shots = more ") + _c(WARN, "TIME") + _c("dim", ", not more VRAM"))
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
    bar_str = "".join(bar)
    labels = _c(BAD, "choppy") + "        " + _c(MID, "24 cinematic") + "      " + _c(CLEAN, "30+ smooth")
    scale = _c("dim", "12       18       24       30      36  (fps)")

    lines = ["  " + bar_str]
    fps = _num(app, "fps")
    if fps is not None:
        frac = max(0.0, min(1.0, (fps - LO) / (HI - LO)))
        pos = int(round(frac * (WIDTH - 1)))
        lines.append("  " + _marker_row(pos, WIDTH, ACCENT, "▲"))
        lines.append(_c("dim", "  ↑ ") + _c(ACCENT, "now %g" % fps))
    lines.append("  " + labels)
    lines.append("  " + scale)
    lines.append(_c("dim", "  note: Wan always renders its native 16fps regardless of this"))
    return "\n".join(lines)


# =====================================================================================
# SEG — how the clip is chunked into shots (per-shot length)
# =====================================================================================
def _seg(app):
    LO, HI, WIDTH = 1.5, 3.0, 42
    segs = [
        (1.75, "▒", DIM),
        (2.5,  "▓", CLEAN),
        (3.0,  "█", WARN),
    ]
    bar = []
    for col in range(WIDTH):
        val = LO + (HI - LO) * col / (WIDTH - 1)
        for ub, glyph, color in segs:
            if val <= ub + 1e-9:
                bar.append(_c(color, glyph))
                break
        else:
            bar.append(_c(WARN, "█"))
    bar_str = "".join(bar)
    labels = _c(DIM, "more seams") + "     " + _c(CLEAN, "sweet spot") + "      " + _c(WARN, "heavier/shot")
    scale = _c("dim", "1.5      2.0      2.5      3.0  (SEGMENT s)")

    lines = ["  " + bar_str]
    seg = _num(app, "seg")
    if seg is not None:
        frac = max(0.0, min(1.0, (seg - LO) / (HI - LO)))
        pos = int(round(frac * (WIDTH - 1)))
        lines.append("  " + _marker_row(pos, WIDTH, ACCENT, "▲"))
        lines.append(_c("dim", "  ↑ ") + _c(ACCENT, "now %.3gs / shot" % seg))
    lines.append("  " + labels)
    lines.append("  " + scale)
    lines.append(_c("dim", "  shorter = less VRAM, more seam/drift risk  ·  longer = fewer seams"))
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
    bar_str = "".join(bar)
    labels = _c(BAD, "drifts freely") + "   " + _c(CLEAN, "sticks tight (can stall)")
    scale = _c("dim", "0.0      0.4      0.6      0.8     1.0  (0-1)")

    lines = ["  " + bar_str]
    val = _num(app, "cond_strength")
    if val is not None:
        frac = max(0.0, min(1.0, (val - LO) / (HI - LO)))
        pos = int(round(frac * (WIDTH - 1)))
        lines.append("  " + _marker_row(pos, WIDTH, ACCENT, "▲"))
        lines.append(_c("dim", "  ↑ ") + _c(ACCENT, "now %.3g" % val))
    lines.append("  " + labels)
    lines.append("  " + scale)
    lines.append(_c("dim", "  raise if subject drifts/morphs · lower if a clip feels 'stuck'"))
    return "\n".join(lines)


# =====================================================================================
# STEADINESS — camera energy: lots of motion/redirects <-> locked-off / hold
# =====================================================================================
def _steadiness(app):
    WIDTH = 42
    opts = [
        ("evolve",   "Evolve",   "journey / transform", 4,  BAD),
        ("balanced", "Balanced", "gentle variation",     19, MID),
        ("hold",     "Hold",     "faithful / locked-off", 34, CLEAN),
    ]
    bar = _c(BAD, "▓" * 12) + _c(MID, "▓" * 14) + _c(CLEAN, "▓" * 16)

    steadiness = None
    try:
        steadiness = app.v("steadiness")
    except Exception:
        steadiness = None
    steadiness = (steadiness or "hold").strip().lower()

    marker = [" "] * WIDTH
    for key, _name, _tag, pos, color in opts:
        marker[pos] = _c(ACCENT, "▲") if key == steadiness else _c(color, "·")

    lines = ["  " + bar, "  " + "".join(marker)]
    ruler = _c("dim", "lots of motion" + " " * 5 + "gentle" + " " * 8 + "locked-off")
    lines.append("  " + ruler)

    legend = []
    for key, name, tag, _pos, color in opts:
        tick = _c(ACCENT, "● ") if key == steadiness else "  "
        legend.append(tick + _c(color, name) + _c("dim", " (%s)" % tag))
    for lg in legend:                       # A19: one item per line -> fits narrow panels
        lines.append("  " + lg)
    return "\n".join(lines)


# =====================================================================================
# CFG_RESCALE — the fry-fixer: over-saturated/fried -> corrected
# =====================================================================================
def _cfg_rescale(app):
    WIDTH = 42
    fried = _c(BAD, "▉▉▉▉▉▉▉▉") + _c(WARN, "▓▓▓▓▓▓▓▓") + _c(CLEAN, "▒▒▒▒▒▒▒▒") + _c(DIM, "░░░░░░░░")
    arrow = _c("dim", "  over-cooked ─────────────▶ corrected")

    val = None
    try:
        val = app.v("cfg_rescale")
    except Exception:
        val = None
    val = (val or "off").strip().lower()

    lines = ["  " + fried, arrow]
    tags = [("off", "off"), ("0.5", "mild"), ("0.7", "stronger")]
    legend = []
    for key, tag in tags:
        tick = _c(ACCENT, "●") if key == val else _c("dim", "○")
        legend.append(tick + " " + _c(ACCENT if key == val else MID, key) + _c("dim", " (%s)" % tag))
    lines.append("  " + "   ".join(legend))
    lines.append(_c("dim", "  fixes over-saturated/fried output when GUIDANCE > ~3"))
    lines.append(_c("dim", "  LTX-2B + Wan only · no-op at GUIDANCE ≤ 1"))
    return "\n".join(lines)


# =====================================================================================
# CFG_INTERVAL — step timeline: CFG on early / off late = faster
# =====================================================================================
def _cfg_interval(app):
    WIDTH = 42

    val = None
    try:
        val = app.v("cfg_interval")
    except Exception:
        val = None
    on = str(val or "off").strip().lower() not in ("off", "")

    if on:
        bar = _c(ACCENT, "█" * 21) + _c(DIM, "░" * 21)
        cap = _c(ACCENT, "on") + _c("dim", " (0.0:0.5) — guidance only over the first half of denoising")
    else:
        bar = _c(MID, "█" * 42)
        cap = _c("dim", "off — guidance runs every step (current default)")

    lines = ["  " + bar]
    lines.append(_c("dim", "  step 0") + " " * 28 + _c("dim", "step N"))
    lines.append("  " + cap)
    lines.append(_c(ACCENT, "  on") + _c("dim", " skips the uncond forward pass late = faster, fewer artifacts"))
    lines.append(_c("dim", "  LTX-2B + Wan only · no-op at GUIDANCE ≤ 1 or on wan-turbo"))
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
        chain = (_c(CLEAN, "[◆]") + _c(DIM, "──") + _c(CLEAN, "[◆]")
                 + _c(DIM, "──") + _c(CLEAN, "[◆]") + _c(DIM, "──") + _c(CLEAN, "[◆]"))
        anchors = "  " + " " * 0 + _c("dim", "│") + " " * 4 + _c("dim", "└─────anchored to shot 1─────┘")
        cap = _c(ACCENT, "on") + _c("dim", " — every shot pinned back to shot 1's opening frame")
    else:
        chain = (_c(CLEAN, "[◆]") + _c(DIM, "──") + _c(WARN, "[◇]")
                 + _c(DIM, "──") + _c(BAD, "[◈]") + _c(DIM, "──") + _c(BAD, "[?]"))
        anchors = _c("dim", "  shot 1        drifting        drifting        ") + _c(BAD, "morphed")
        cap = _c("dim", "off — each shot only conditions on the ") + _c(WARN, "previous") + _c("dim", " one")

    lines = ["  " + chain, anchors, "  " + cap]
    lines.append(_c("dim", "  Wan / Wan-turbo only, chained (multi-shot) runs"))
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
    seed_s = str(seed).strip() if seed not in (None, "") else "0"

    row1 = "  " + _c(CLEAN, "┌─────────┐") + "   " + _c("dim", "same settings") + "   " + _c(CLEAN, "┌─────────┐")
    row2 = ("  " + _c(CLEAN, "│ seed %-3s│" % seed_s[:3]) + " ──────────────▶ "
            + _c(CLEAN, "│ identical│"))
    row3 = "  " + _c(CLEAN, "└─────────┘") + "                 " + _c(CLEAN, "└─────────┘")
    row4 = "  " + _c(WARN, "┌─────────┐") + "   " + _c("dim", "any other seed") + "   " + _c(WARN, "┌─────────┐")
    row5 = ("  " + _c(WARN, "│ seed ??? │") + " ──────────────▶ "
            + _c(WARN, "│ new take │"))
    row6 = "  " + _c(WARN, "└─────────┘") + "                 " + _c(WARN, "└─────────┘")

    lines = [row1, row2, row3, "", row4, row5, row6]
    lines.append(_c("dim", "  ↑ now ") + _c(ACCENT, "seed=%s" % seed_s))
    lines.append(_c("dim", "  fix it to change ONE thing at a time and compare fairly"))
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
