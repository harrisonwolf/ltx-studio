#!/usr/bin/env python
"""Find the max LTX clip length that fits 8GB at each resolution (sequential offload).
Loads the pipeline once, then tries 2-step generations at increasing lengths."""
import gc, torch
from diffusers import LTXPipeline

pipe = LTXPipeline.from_pretrained("Lightricks/LTX-Video", torch_dtype=torch.bfloat16)
pipe.enable_sequential_cpu_offload()
pipe.vae.enable_tiling()


def fits(W, H, frames):
    try:
        g = torch.Generator("cpu").manual_seed(0)
        pipe(prompt="a test scene", width=W, height=H, num_frames=frames,
             num_inference_steps=2, guidance_scale=3.0, generator=g)
        ok = True
    except RuntimeError as e:
        ok = not ("out of memory" in str(e).lower())
        if "out of memory" not in str(e).lower():
            print("   ERR:", str(e)[:60], flush=True)
    torch.cuda.empty_cache(); gc.collect()
    return ok


for W, H, secs in [(512, 320, [4, 8, 12, 16]), (704, 480, [3, 5, 7, 9])]:
    best = 0
    for s in secs:
        frames = (int(s * 24) // 8) * 8 + 1
        ok = fits(W, H, frames)
        print(f"{W}x{H}  {s}s ({frames}f): {'OK' if ok else 'OOM'}", flush=True)
        if not ok:
            break
        best = s
    print(f"  ==> {W}x{H} max ~{best}s\n", flush=True)
print("CALIB_DONE")
