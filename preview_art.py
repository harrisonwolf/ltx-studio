#!/usr/bin/env python
"""Terminal frame-art renderer: PNG -> truecolor sub-cell ANSI (sextant/quadrant/half).

Split out of studio.py (2026-07-06 light restructuring): pure code motion, no behavior
change — imports are the only wiring. See tests/ for the regression net."""

import os
from collections import OrderedDict

from PIL import Image
from rich.color import Color
from rich.style import Style
from rich.text import Text

# ---- dense sub-cell preview renderer (torch-free, PIL + Rich only) -----------
# Modes: "sextant" (2x3 px/cell, ~3x sharper), "quadrant" (2x2, universally font-safe),
# "half" (1x2, the original). Cell grid stays cols x rows in every mode, so the panel
# width math (cols+2) never changes. Sextants (U+1FB00..) need Cascadia 2404+, which
# Windows Terminal ships; the default is sextant everywhere. PREVIEW_MODE env or Ctrl+P
# (in-app) switches to quadrant/half if the glyphs render as tofu.
PREVIEW_MODE = os.environ.get("PREVIEW_MODE", "").strip().lower()
if PREVIEW_MODE not in ("sextant", "quadrant", "half"):
    PREVIEW_MODE = "sextant"   # sharpest; if glyphs show as boxes (old Cascadia) press Ctrl+P -> quadrant

# quadrant mask (TL=1,TR=2,BL=4,BR=8) -> glyph
_QUAD = {0: " ", 1: "▘", 2: "▝", 3: "▀", 4: "▖", 5: "▌", 6: "▞", 7: "▛",
         8: "▗", 9: "▚", 10: "▐", 11: "▜", 12: "▄", 13: "▙", 14: "▟", 15: "█"}


def _sextant_char(m):
    """6-bit mask (pos1=top-left .. pos6=bottom-right, bit=2^(pos-1)) -> glyph.
    Masks 0/21/42/63 have dedicated codepoints outside the U+1FB00 run."""
    if m == 0:  return " "
    if m == 21: return "▌"   # U+258C left half  (positions 1,3,5)
    if m == 42: return "▐"   # U+2590 right half (positions 2,4,6)
    if m == 63: return "█"   # U+2588 full
    off = m - 1
    if m > 21: off -= 1
    if m > 42: off -= 1
    return chr(0x1FB00 + off)


def _two_color_cell(sub):
    """sub = list of (r,g,b) sub-pixels in row-major order. Returns (mask, fg, bg) via
    luminance-vs-mean 2-clustering (chafa-style mean colors). mask=-1 -> solid fg, 0 -> solid bg."""
    lum = [0.299 * r + 0.587 * g + 0.114 * b for (r, g, b) in sub]
    thr = sum(lum) / len(lum)
    mask = 0
    fr = fg_ = fb = br = bg_ = bb = nf = nb = 0
    for i, (r, g, b) in enumerate(sub):
        if lum[i] >= thr:
            mask |= (1 << i); fr += r; fg_ += g; fb += b; nf += 1
        else:
            br += r; bg_ += g; bb += b; nb += 1
    n = len(sub)
    if nf == 0:                       # genuinely flat -> solid bg
        bg = (sum(r for r, _, _ in sub) // n, sum(g for _, g, _ in sub) // n, sum(b for _, _, b in sub) // n)
        return 0, bg, bg
    if nb == 0:                       # genuinely flat -> solid fg
        fg = (fr // nf, fg_ // nf, fb // nf)
        return -1, fg, fg
    return mask, (fr // nf, fg_ // nf, fb // nf), (br // nb, bg_ // nb, bb // nb)


_RENDER_CACHE = OrderedDict()   # (path, mtime_ns, size, cols, mode) -> Text; the frame viewer steps back and forth
_RENDER_CACHE_MAX = 32


def render_preview(path, cols=48):
    """Render a preview PNG as truecolor sub-cell ANSI art. Cell grid is cols x rows; each cell
    packs 2x3 (sextant), 2x2 (quadrant) or 1x2 (half) sub-pixels with one fg + one bg color,
    chosen per-cell by luminance clustering (chafa algorithm). Torch-free (PIL + Rich), never
    raises -> Text() on error. Returns a rich.text.Text, drop-in for the original half-block one.
    Memoized on the file's (mtime, size) + cols + mode; callers get a copy they may mutate."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size, cols, PREVIEW_MODE)
    except OSError:
        return Text()
    hit = _RENDER_CACHE.get(key)
    if hit is None:
        hit = _render_preview(path, cols)
        if not hit.plain:
            return hit                                   # don't pin a failed decode (file mid-write)
        _RENDER_CACHE[key] = hit
        while len(_RENDER_CACHE) > _RENDER_CACHE_MAX:
            _RENDER_CACHE.popitem(last=False)
    else:
        _RENDER_CACHE.move_to_end(key)
    return hit.copy()


def _rgb_color(c):
    return Color.from_rgb(*c)   # same Color as parsing "#rrggbb", without formatting + re-parsing a string


def _render_preview(path, cols):
    try:
        mode = PREVIEW_MODE
        sx, sy = {"sextant": (2, 3), "quadrant": (2, 2), "half": (1, 2)}.get(mode, (2, 3))
        im = Image.open(path).convert("RGB")
        w, h = im.size
        # 0.5 = terminal cell width/height; sets the on-screen aspect (same as the original
        # half-block path). Sub-pixel density (sx,sy) is independent of this — it only raises
        # resolution WITHIN each cell, so the cell grid (cols x rows) stays aspect-correct.
        rows = max(1, round(cols * (h / w) * 0.5))
        im = im.resize((cols * sx, rows * sy), Image.Resampling.LANCZOS)
        px = im.load()
        t = Text()
        for ry in range(rows):
            y0 = ry * sy
            for cx in range(cols):
                x0 = cx * sx
                if mode == "half":
                    t.append("▀", Style(color=_rgb_color(px[x0, y0]), bgcolor=_rgb_color(px[x0, y0 + 1])))
                    continue
                sub = [px[x0 + dx, y0 + dy] for dy in range(sy) for dx in range(sx)]
                m, fg, bg = _two_color_cell(sub)
                if m == 0:
                    t.append(" ", Style(bgcolor=_rgb_color(bg)))
                elif m == -1:
                    t.append("█", Style(color=_rgb_color(fg)))
                else:
                    glyph = _sextant_char(m) if mode == "sextant" else _QUAD[m]
                    t.append(glyph, Style(color=_rgb_color(fg), bgcolor=_rgb_color(bg)))
            if ry != rows - 1:
                t.append("\n")
        return t
    except Exception:
        return Text()
