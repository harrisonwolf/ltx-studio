#!/usr/bin/env python
"""Q2 + preamble CPU unit tests (no GPU). Run: venv/bin/python _q2tests/test_units.py
Covers: palette_lock OFF-identity + convergence, adain_normalize_latents identity/match,
linear_overlap_fuse frame-count preservation, and experiment_log provenance export (P2)."""
import os, sys
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import director
import experiment_log

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


# ---------- helpers ----------
def rand_frames(n, w=64, h=48, lo=0, hi=255, seed=0):
    rng = np.random.default_rng(seed)
    return [Image.fromarray(rng.integers(lo, hi, (h, w, 3), dtype=np.uint8)) for _ in range(n)]

def cdf_dist(frames, pool):
    """Wasserstein-1 (sum |CDF_a - CDF_b|) between the frames' and pool's per-channel 256-bin CDFs,
    averaged over channels. Smaller = closer distributions."""
    fpix = np.concatenate([np.asarray(f).reshape(-1, 3) for f in frames], 0)
    d = 0.0
    for c in range(3):
        ha, _ = np.histogram(fpix[:, c], bins=256, range=(0, 255), density=True)
        hb, _ = np.histogram(pool[:, c], bins=256, range=(0, 255), density=True)
        d += np.abs(np.cumsum(ha) - np.cumsum(hb)).sum()
    return d / 3.0


# ---------- palette_lock: OFF-path identity ----------
frames = rand_frames(6, seed=1)
pool = director.build_palette_pool(rand_frames(4, lo=150, hi=255, seed=2), seed=0)

off = director.palette_lock(frames, pool, 0.0)
check("palette_lock(strength=0) returns the SAME list object", off is frames)
check("palette_lock(strength=0) is pixel-identical",
      all(np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(off, frames)))

# negative / None-pool also identity
check("palette_lock(strength<0) identity", director.palette_lock(frames, pool, -0.5) is frames)
check("palette_lock(pool=None) identity", director.palette_lock(frames, None, 0.9) is frames)

# ---------- palette_lock: convergence at strength 1.0 ----------
dark = rand_frames(6, lo=0, hi=80, seed=3)                       # distribution far from the bright pool
d0 = cdf_dist(dark, pool)
matched = director.palette_lock(dark, pool, 1.0)
d1 = cdf_dist(matched, pool)
check("palette_lock(strength=1) shrinks CDF distance to the pool by >5x",
      d1 * 5.0 < d0, f"d0={d0:.3f} d1={d1:.3f} ratio={d0/max(d1,1e-9):.1f}x")
check("palette_lock preserves frame COUNT", len(matched) == len(dark))
check("palette_lock preserves frame SIZE",
      all(m.size == d.size for m, d in zip(matched, dark)))
# partial strength lands between identity and full
half = director.palette_lock(dark, pool, 0.5)
dh = cdf_dist(half, pool)
check("palette_lock(0.5) is between identity and full", d1 <= dh <= d0, f"d1={d1:.3f} dh={dh:.3f} d0={d0:.3f}")


# ---------- adain_normalize_latents ----------
rng = torch.Generator().manual_seed(7)
curr = torch.randn(1, 4, 5, 8, 8, generator=rng) * 2.0 + 1.0
anchor = torch.randn(1, 4, 3, 8, 8, generator=rng) * 0.5 - 3.0    # different T, different per-channel stats

id0 = director.adain_normalize_latents(curr, anchor, 0.0)
check("adain factor=0 is exact identity", torch.equal(id0, curr))

full = director.adain_normalize_latents(curr, anchor, 1.0)
mu_out = full.mean(dim=(2, 3, 4))
sd_out = full.std(dim=(2, 3, 4))
mu_ref = anchor.mean(dim=(2, 3, 4))
sd_ref = anchor.std(dim=(2, 3, 4))
check("adain factor=1 matches anchor per-channel MEAN <1e-3",
      torch.allclose(mu_out, mu_ref, atol=1e-3), f"max|dmu|={(mu_out-mu_ref).abs().max():.2e}")
check("adain factor=1 matches anchor per-channel STD <1e-3",
      torch.allclose(sd_out, sd_ref, atol=1e-3), f"max|dsd|={(sd_out-sd_ref).abs().max():.2e}")
check("adain(ref=None) identity", torch.equal(director.adain_normalize_latents(curr, None, 1.0), curr))
check("adain preserves shape", full.shape == curr.shape)


# ---------- linear_overlap_fuse (frame-count preservation, the director use-pattern) ----------
T, K = 10, 3
carry = torch.randn(1, 4, 6, 8, 8, generator=rng)   # previous shot's full latents
lat = torch.randn(1, 4, T, 8, 8, generator=rng)     # current shot's latents (must stay T frames)
fused = director.linear_overlap_fuse(carry[:, :, -K:], lat, K)
check("fuse preserves the current shot's latent frame COUNT", fused.shape[2] == T,
      f"got {fused.shape[2]}, want {T}")
check("fuse leaves the trailing (non-overlap) latent frames untouched",
      torch.equal(fused[:, :, K:], lat[:, :, K:]))
check("fuse blends the leading K frames (not equal to raw)",
      not torch.equal(fused[:, :, :K], lat[:, :, :K]))
# overlap<=1 -> concat semantics (the guarded path we never hit, but verify the math)
cc = director.linear_overlap_fuse(carry[:, :, -1:], lat, 1)
check("fuse(overlap=1) concatenates", cc.shape[2] == 1 + T)


# ---------- _denorm_stash invariant: RAW enters the stash/carry, corrections are decode-only ----------
# Mirrors director.LTXBackend._denorm_stash's op order (raw -> adain -> fuse -> decode-only output)
# without instantiating the full LTX pipeline. Falsified 2026-07-03: the original design carried the
# CORRECTED latents forward, causing a positive-feedback drift loop (anchored drift ~5x baseline by
# shot 3 in an 8-shot hold-stress test). This guards the fix: nothing touched by adain/fuse may reach
# the stash/carry, even with both switched on.
rng3 = torch.Generator().manual_seed(13)
raw = torch.randn(1, 4, 6, 8, 8, generator=rng3) * 3.0 + 2.0
raw_ref = raw.clone()
anchor3 = torch.randn(1, 4, 4, 8, 8, generator=rng3) * 0.3 - 1.0
prev_carry = torch.randn(1, 4, 5, 8, 8, generator=rng3)

# simulate the hook with adain>0 AND fuse on (the ON path that caused the regression)
out = raw
out = director.adain_normalize_latents(out, anchor3, factor=0.7)   # simulate --latent_adain 0.7
K = 3
out = director.linear_overlap_fuse(prev_carry[:, :, -K:], out, K)  # simulate --latent_fuse on
stashed = raw.detach()   # what _denorm_stash puts in _lat_stash["lat"] -> becomes _carry in gen()

check("simulated shot (adain>0 + fuse ON): stash/carry stays bit-identical to the RAW input",
      torch.equal(stashed, raw_ref))
check("simulated shot (adain>0 + fuse ON): the raw tensor itself is never mutated in place",
      torch.equal(raw, raw_ref))
check("simulated shot (adain>0 + fuse ON): decode-path output DOES differ from raw (corrections applied)",
      not torch.equal(out, raw_ref))

# OFF path (adain=0, fuse off) must still be bit-identical end to end, decode included
out_off = raw
check("simulated shot (OFF path): decode-path output is RAW, unchanged (bit-identical)",
      torch.equal(out_off, raw_ref))


# ---------- experiment_log provenance (P2) ----------
class _Job:
    id = "unit"; created = 1000; finished = 1002; started = 1000
    status = "done"; kind = "chained"; error = ""
    seam_mse = [[2, 340]]; drift = [[2, 120, 45]]; tok_counts = [[2, 96]]
    peak_vram = 6100; phase_secs = {"load": 5}; seg_secs = [3.0]; dir_ms = {}
    params = {"backend": "ltx", "ltx_repo": "Lightricks/LTX-Video-0.9.5",
              "ltx_variant": "distilled", "steps": "30", "pair_id": "unit", "pair_variant": "A"}
    def elapsed(self): return 2

rec = experiment_log.build_record(_Job())
check("build_record exports ltx_repo", rec.get("ltx_repo") == "Lightricks/LTX-Video-0.9.5")
check("build_record exports ltx_variant", rec.get("ltx_variant") == "distilled")
check("build_record still exports Q3 arrays",
      rec.get("seam_mse") == [[2, 340]] and rec.get("drift") == [[2, 120, 45]] and rec.get("tok_counts") == [[2, 96]])
check("SCHEMA not bumped (still 1)", rec.get("schema") == 1 and experiment_log.SCHEMA == 1)
# provenance fields default to None when absent (additive, tolerant)
class _Bare(_Job):
    params = {"backend": "wan"}
bare = experiment_log.build_record(_Bare())
check("ltx_repo/ltx_variant default None when unset",
      bare.get("ltx_repo") is None and bare.get("ltx_variant") is None)


print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL UNIT TESTS PASSED")
