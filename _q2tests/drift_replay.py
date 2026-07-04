#!/usr/bin/env python
"""Q2 offline drift replay (CPU, no GPU). Simulates chaining two saved runs' frames and compares
drift-vs-anchor under (a) the current color_match-to-PREVIOUS-tail (compounding) against
(b) palette_lock-to-SHOT-1-anchor at strength 0.5 and 1.0. Renders a per-channel mean-drift chart
with PIL (matplotlib is not installed in this venv).

  venv/bin/python _q2tests/drift_replay.py [dirA_frames dirB_frames]

Writes outputs/q2_drift_replay.png and prints the path + a numeric table.
"""
import os, sys, glob
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import director

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NSHOTS = 8
PER_DIR = 80        # sampled frames per source run
LOADW = 256         # resize width on load (drift = mean color, scale-robust; keeps the replay fast)


def pick_dirs():
    """Default: the two largest PLAIN render frame dirs (skip *_enh* post-process dirs)."""
    ds = [d for d in glob.glob(os.path.join(REPO, "outputs", "*_frames")) if "_enh" not in os.path.basename(d)]
    ds = [(len(glob.glob(os.path.join(d, "*.png"))), d) for d in ds]
    ds = sorted([x for x in ds if x[0] >= NSHOTS], reverse=True)
    if len(ds) < 2:
        print("need >=2 plain render frame dirs in outputs/*_frames"); sys.exit(2)
    return ds[0][1], ds[1][1]


def load_sampled(d, k):
    paths = sorted(glob.glob(os.path.join(d, "*.png")))
    if not paths:
        return []
    idx = np.linspace(0, len(paths) - 1, min(k, len(paths))).round().astype(int)
    out = []
    for i in idx:
        im = Image.open(paths[i]).convert("RGB")
        w, h = im.size
        out.append(im.resize((LOADW, max(1, round(h * LOADW / w)))))
    return out


def chan_mean(frames):
    """Per-channel mean color over a list of frames -> (3,)."""
    return np.stack([np.asarray(f, np.float32).reshape(-1, 3).mean(0) for f in frames]).mean(0)


def drift_curves(dirA, dirB):
    chain = load_sampled(dirA, PER_DIR) + load_sampled(dirB, PER_DIR)
    ss = len(chain) // NSHOTS
    shots = [chain[i * ss:(i + 1) * ss] for i in range(NSHOTS)]
    anchor_mean = chan_mean(shots[0])                       # shot-1 per-channel mean = the target
    pool = director.build_palette_pool(shots[0], seed=0)    # shot-1 pixel pool (static; no decay in the replay)

    def per_chan_drift(frames):
        return np.abs(chan_mean(frames) - anchor_mean)      # (3,)

    curves = {"raw (no correction)": [], "color_match->prev (current)": [],
              "palette_lock->anchor 0.5": [], "palette_lock->anchor 1.0": []}
    perchan = {k: [] for k in curves}
    prev_ref = shots[0][-1]                                 # color_match chains off the PREVIOUS corrected tail
    for i, shot in enumerate(shots):
        raw_d = per_chan_drift(shot)
        if i == 0:
            for k in curves:
                curves[k].append(0.0); perchan[k].append(np.zeros(3))
            continue
        cm = director.color_match(shot, prev_ref)
        cm_d = per_chan_drift(cm)
        prev_ref = cm[-1]                                   # compounding random walk (chases the tail)
        p5 = per_chan_drift(director.palette_lock(shot, pool, 0.5))
        p10 = per_chan_drift(director.palette_lock(shot, pool, 1.0))
        for k, d in (("raw (no correction)", raw_d), ("color_match->prev (current)", cm_d),
                     ("palette_lock->anchor 0.5", p5), ("palette_lock->anchor 1.0", p10)):
            curves[k].append(float(d.mean())); perchan[k].append(d)
    return curves, perchan


def render_png(curves, path):
    W, H = 920, 560
    ML, MR, MT, MB = 70, 250, 56, 60
    img = Image.new("RGB", (W, H), (250, 250, 250))
    dr = ImageDraw.Draw(img)
    x0, y0, x1, y1 = ML, MT, W - MR, H - MB
    dr.rectangle([x0, y0, x1, y1], outline=(60, 60, 60), fill=(255, 255, 255))
    n = len(next(iter(curves.values())))
    ymax = max(1e-6, max(max(v) for v in curves.values())) * 1.1
    def px(i): return x0 + (x1 - x0) * (i / max(1, n - 1))
    def py(v): return y1 - (y1 - y0) * (v / ymax)
    # gridlines + y labels
    for g in range(5):
        yv = ymax * g / 4
        yy = py(yv)
        dr.line([x0, yy, x1, yy], fill=(230, 230, 230))
        dr.text((8, yy - 6), f"{yv:5.1f}", fill=(80, 80, 80))
    for i in range(n):
        dr.text((px(i) - 3, y1 + 8), str(i + 1), fill=(80, 80, 80))
    dr.text((x0, H - 24), "shot index", fill=(40, 40, 40))
    dr.text((10, 6), "Q2 drift replay: mean per-channel color drift vs shot 1 (lower = less drift)", fill=(20, 20, 20))
    colors = {"raw (no correction)": (150, 150, 150), "color_match->prev (current)": (210, 70, 60),
              "palette_lock->anchor 0.5": (60, 130, 210), "palette_lock->anchor 1.0": (30, 170, 90)}
    ly = MT + 6
    for name, vals in curves.items():
        col = colors.get(name, (0, 0, 0))
        pts = [(px(i), py(v)) for i, v in enumerate(vals)]
        for a, b in zip(pts, pts[1:]):
            dr.line([a, b], fill=col, width=3)
        for p in pts:
            dr.ellipse([p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3], fill=col)
        dr.line([x1 + 14, ly + 6, x1 + 40, ly + 6], fill=col, width=3)
        dr.text((x1 + 46, ly), name, fill=(30, 30, 30))
        dr.text((x1 + 46, ly + 14), f"final={vals[-1]:.2f}", fill=(90, 90, 90))
        ly += 40
    img.save(path)


def main():
    a, b = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else pick_dirs()
    print(f"chain: A={os.path.basename(a)}  B={os.path.basename(b)}  ({NSHOTS} shots)")
    curves, perchan = drift_curves(a, b)
    print("\nmean drift per shot (aggregate over RGB):")
    print("shot  " + "  ".join(f"{k[:22]:>22}" for k in curves))
    for i in range(len(next(iter(curves.values())))):
        print(f"{i+1:>4}  " + "  ".join(f"{curves[k][i]:>22.3f}" for k in curves))
    print("\nfinal-shot per-channel drift (R,G,B):")
    for k in curves:
        print(f"  {k:<30} {np.round(perchan[k][-1], 2)}")
    out = os.path.join(REPO, "outputs", "q2_drift_replay.png")
    render_png(curves, out)
    print(f"\nPNG -> {out}")
    # quick verdict for the summary
    cm_final = curves["color_match->prev (current)"][-1]
    pl_final = curves["palette_lock->anchor 1.0"][-1]
    print(f"verdict: palette_lock->anchor final drift {pl_final:.2f} vs color_match->prev {cm_final:.2f} "
          f"({'LOWER (anchoring wins)' if pl_final < cm_final else 'not lower on this pair'})")


if __name__ == "__main__":
    main()
