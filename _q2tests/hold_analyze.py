#!/usr/bin/env python
"""Attribute the hold-stress regression. Three curves of drift-vs-shot-1 (luminance MSE):
  (1) hold_base       -- real run, color_match-to-prev (baseline)
  (2) hold_anchored   -- real run, adain 0.7 + palette 1.0 (combined)
  (3) palette-only*    -- OFFLINE: palette_lock applied to hold_base's own frames.
palette_lock is a pixel post-process that never feeds the latent carry, so a live --palette_lock-only
run generates the SAME frames as baseline; applying it to the saved baseline frames reproduces that
run's drift without a GPU. If (3) tracks (1) while (2) explodes, AdaIN (which alters the generative
chain) is the culprit, not palette. Renders outputs/q2_hold_attribution.png.
"""
import os, sys, re, glob
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import director

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEG_FRAMES, OVERLAP = 49, 9          # total 13 / seg 2 @24fps -> 49; default overlap 9
STRIDE = SEG_FRAMES - OVERLAP        # 40 new frames per continuation


def drift_post(logname):
    """Parse [[DRIFT seg pre post]] -> {seg: post/100.0} from a run log."""
    out = {}
    with open(os.path.join(REPO, "outputs", f"{logname}.log")) as fh:
        for ln in fh:
            m = re.search(r"\[\[DRIFT (\d+) (\d+) (\d+)\]\]", ln)
            if m:
                out[int(m.group(1))] = int(m.group(3)) / 100.0
    return out


def offline_palette_only(frames_dir):
    """Reproduce a --palette_lock 1.0-only run's drift from baseline frames (palette is per-frame,
    content-preserving). Returns {seg: drift} for shots whose last frame survives the target trim."""
    paths = sorted(glob.glob(os.path.join(REPO, frames_dir, "*.png")))
    frames = [Image.open(p).convert("RGB") for p in paths]
    n = len(frames)
    anchor = frames[SEG_FRAMES - 1]                       # shot-1 last frame (index 48)
    pool = director.build_palette_pool(frames[:SEG_FRAMES], seed=42)
    out = {}
    seg = 2
    while True:
        idx = (SEG_FRAMES - 1) + (seg - 1) * STRIDE       # shot `seg` last-frame index in the assembled video
        if idx >= n:
            break
        pal_last = director.palette_lock([frames[idx]], pool, 1.0)[0]
        out[seg] = director._seam_mse(pal_last, anchor)
        seg += 1
    return out


def render(curves, path):
    W, H = 940, 560
    ML, MR, MT, MB = 78, 250, 56, 60
    img = Image.new("RGB", (W, H), (250, 250, 250)); dr = ImageDraw.Draw(img)
    x0, y0, x1, y1 = ML, MT, W - MR, H - MB
    dr.rectangle([x0, y0, x1, y1], outline=(60, 60, 60), fill=(255, 255, 255))
    segs = sorted(set().union(*[set(c["data"].keys()) for c in curves]))
    xmin, xmax = min(segs), max(segs)
    ymax = max(1e-6, max(max(c["data"].values()) for c in curves)) * 1.08
    def px(s): return x0 + (x1 - x0) * ((s - xmin) / max(1, xmax - xmin))
    def py(v): return y1 - (y1 - y0) * (v / ymax)
    for g in range(5):
        yv = ymax * g / 4; yy = py(yv)
        dr.line([x0, yy, x1, yy], fill=(232, 232, 232)); dr.text((8, yy - 6), f"{yv:6.0f}", fill=(90, 90, 90))
    for s in segs:
        dr.text((px(s) - 3, y1 + 8), str(s), fill=(90, 90, 90))
    dr.text((10, 6), "Q2 hold-scene drift attribution: luminance MSE vs shot 1 (lower = less drift)", fill=(20, 20, 20))
    dr.text((x0, H - 24), "shot index", fill=(40, 40, 40))
    ly = MT + 6
    for c in curves:
        pts = [(px(s), py(c["data"][s])) for s in sorted(c["data"])]
        for a, b in zip(pts, pts[1:]):
            dr.line([a, b], fill=c["color"], width=3)
        for p in pts:
            dr.ellipse([p[0]-3, p[1]-3, p[0]+3, p[1]+3], fill=c["color"])
        dr.line([x1 + 14, ly + 6, x1 + 40, ly + 6], fill=c["color"], width=3)
        dr.text((x1 + 46, ly), c["name"], fill=(30, 30, 30))
        fin = c["data"][max(c["data"])]
        dr.text((x1 + 46, ly + 14), f"shot{max(c['data'])}={fin:.0f}", fill=(90, 90, 90))
        ly += 42
    img.save(path)


def main():
    base = drift_post("hold_base")
    anch = drift_post("hold_anchored")
    palo = offline_palette_only("outputs/hold_base_frames")
    print("shot |  hold_base | palette-only* |  hold_anchored")
    for s in sorted(base):
        b = base.get(s); p = palo.get(s); a = anch.get(s)
        row = f"{s:>4} | {b:>10.1f} | " + (f"{p:>13.1f}" if p is not None else f"{'(trimmed)':>13}") + f" | {a:>13.1f}"
        print(row)
    curves = [
        {"name": "hold_base (color_match)", "color": (210, 70, 60), "data": base},
        {"name": "palette-only* (offline)", "color": (60, 130, 210), "data": palo},
        {"name": "hold_anchored (adain+pal)", "color": (150, 60, 190), "data": anch},
    ]
    out = os.path.join(REPO, "outputs", "q2_hold_attribution.png")
    render(curves, out)
    print(f"\nPNG -> {out}")
    # verdict
    s_common = sorted(set(base) & set(palo))
    palette_delta = np.mean([palo[s] - base[s] for s in s_common])
    print(f"palette-only vs baseline (mean drift delta over shots {s_common[0]}-{s_common[-1]}): "
          f"{palette_delta:+.1f}  ({'palette ~neutral/helpful' if palette_delta <= base[s_common[-1]]*0.25 else 'palette also hurts'})")
    fin_b, fin_a = base[max(base)], anch[max(anch)]
    print(f"combined anchored final drift {fin_a:.0f} vs baseline {fin_b:.0f} -> "
          f"{fin_a/fin_b:.1f}x {'WORSE' if fin_a > fin_b else 'better'}")


if __name__ == "__main__":
    main()
