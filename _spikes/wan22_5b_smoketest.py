#!/usr/bin/env python
# ==============================================================================================
#  Wan 2.2 TI2V-5B  --  image-to-video SMOKE TEST  (single image -> one short clip)
# ==============================================================================================
#  DO NOT RUN until ALL THREE are true:
#     (1) a GPU window is FREE   -- this launches CUDA and will contend for the shared 8GB card;
#     (2) the FP8/bf16 5B WEIGHTS are downloaded into the HF cache (see WEIGHTS below);
#     (3) the USER has explicitly APPROVED this run.
#  This file is written correct-by-reading (director.py WanBackend + diffusers 0.38 wan docs) and
#  has NEVER been executed. It touches .cuda / loads a multi-GB model -- treat it as live ordnance.
#
#  It is a standalone spike: it imports NOTHING from studio/core/director and speaks no [[MARKER]]
#  protocol. Purpose = answer "does the 5B i2v pipeline load + produce a clip on THIS 8GB box, and
#  how slow is it, really" -- nothing more. Wiring it into director.py is a SEPARATE follow-up.
#
#  HARD-BOX REMINDERS (from the repo constraints):
#     - NEVER run nvidia-smi / any NVML call from here -- it restarts the whole WSL2 VM.
#     - Expect heavy CPU<->GPU paging: sequential offload keeps only the active layer resident, so
#       a "success" here means "it finished", NOT "it was fast". Wall-clock is printed at the end so
#       you can separate "loads" from "usable".
# ==============================================================================================
#
#  WHY 0.38.0 IS ENOUGH (no diffusers bump):
#     Wan 2.2 (T2V-A14B / I2V-A14B / TI2V-5B) was integrated into diffusers 0.36.0 (Jul 2025). The
#     installed 0.38.0 already exposes WanImageToVideoPipeline, AutoencoderKLWan, Wan22* modular
#     blocks, and WanTransformer3DModel.from_single_file (all verified via dir(diffusers) on this
#     venv). The TI2V-5B model card's "needs the main branch" note is STALE -- it predates the 0.36
#     release. So this spike pins to the SAME diffusers the LTX + Wan2.1-VACE paths already use.
#
#  MODEL NOTES:
#     - TI2V-5B is a SINGLE dense transformer (unlike the A14B dual high-noise/low-noise experts), so
#       guidance_scale_2 / the boundary split does NOT apply here -- one guidance_scale only.
#     - Wan2.2-VAE is high-compression 4x16x16 (patchified to 4x32x32, ~64x overall) -- much lighter
#       per-frame than Wan2.1's 4x8x8 VAE, which is the whole reason 5B is 8GB-plausible.
#     - Native res is 704x1280 / 24fps / 121f (720p). We DELIBERATELY run 480p / 16fps-ish / short
#       here to give the shared 8GB card its best shot at finishing. flow_shift=3.0 is the docs'
#       480p value (5.0 is for 720p).
#     - Known issue diffusers#12034: 5B i2v in diffusers can look softer than the official HF Space.
#       That's a QUALITY caveat for adoption, not a blocker for a "does it run" smoke test.
# ==============================================================================================
#
#  WEIGHTS (pick ONE route; neither is downloaded by this script):
#
#   ROUTE A -- diffusers-native bf16 (SIMPLEST, most robust; ~10-11GB on disk, paged via offload):
#       huggingface-cli download Wan-AI/Wan2.2-TI2V-5B-Diffusers
#     Then set WEIGHTS_ROUTE = "bf16". This is the path this script defaults to because it needs no
#     key-remapping and matches the WanBackend.load() from_pretrained pattern already in director.py.
#
#   ROUTE B -- Kijai FP8 single-file transformer (smaller resident footprint, ComfyUI-format):
#       Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors  (or the e5m2 variant)
#       from  https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/tree/main/TI2V
#     Loaded via WanTransformer3DModel.from_single_file(...) as a transformer= override on the bf16
#     repo (SAME trick the LTX 0.9.8-distilled path uses for ltxv-2b-...safetensors in director.py).
#     CAVEAT: Kijai's file is a ComfyUI-layout checkpoint; diffusers from_single_file MAY need a
#     conversion/rename pass. If it errors on unexpected keys, FALL BACK to Route A -- do not fight it
#     during a smoke test. FP8 mainly shrinks the resident/paged transformer; with sequential offload
#     already streaming layers, the bf16 route's SPEED is similar -- FP8's win is headroom, not magic.
# ==============================================================================================

import argparse
import time
import torch
from PIL import Image
from diffusers import WanImageToVideoPipeline, AutoencoderKLWan
from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
from diffusers.utils import export_to_video, load_image

MODEL = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"

# Kijai FP8 single-file (Route B). Download it yourself; point --fp8_file at the local path.
FP8_HINT = ("Kijai/WanVideo_comfy_fp8_scaled :: TI2V/Wan2_2-TI2V-5B_fp8_e4m3fn_scaled_KJ.safetensors")


def round_frames(n):
    # Wan VAE temporal factor 4: (num_frames - 1) % 4 == 0  (same rule as director.py WanBackend).
    n = max(5, int(n))
    return ((n - 1) // 4) * 4 + 1


def round_dim(x):
    # Wan spatial constraint: multiples of 32 (matches WanBackend.dims()).
    return max(32, round(x / 32) * 32)


def main():
    ap = argparse.ArgumentParser(description="Wan 2.2 TI2V-5B i2v smoke test (DO NOT RUN casually).")
    ap.add_argument("--image", required=True, help="path to the single conditioning image")
    ap.add_argument("--prompt", default="cinematic, smooth natural motion, gentle camera drift")
    ap.add_argument("--neg", default=(
        "static, blurred details, low quality, jpeg artifacts, worst quality, deformed, "
        "extra limbs, watermark, subtitles, overexposed, flicker"))
    ap.add_argument("--out", default="_spikes/wan22_5b_smoke.mp4")
    # 480p-ish landscape by default: short side ~480, /32-rounded. Keep it SMALL for the first run.
    ap.add_argument("--width", type=int, default=832)    # 832x480 is Wan's canonical 480p box
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--frames", type=int, default=49)    # ~2s @ ~24fps; 49 = 4k+1. Bump later if it survives.
    ap.add_argument("--steps", type=int, default=30)     # 50 is the docs default; 30 is a faster smoke value.
    ap.add_argument("--cfg", type=float, default=5.0)    # guidance_scale (single transformer -> no cfg_2).
    ap.add_argument("--flow_shift", type=float, default=3.0)   # 3.0 for 480p, 5.0 for 720p (docs).
    ap.add_argument("--fps", type=int, default=24)       # 5B is a 24fps model.
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fp8_file", default=None,
                    help="Route B: local path to Kijai FP8 single-file transformer (see FP8_HINT).")
    args = ap.parse_args()

    W, H = round_dim(args.width), round_dim(args.height)
    F = round_frames(args.frames)
    print(f"[smoke] model={MODEL}")
    print(f"[smoke] {W}x{H}  frames={F}  steps={args.steps}  cfg={args.cfg}  "
          f"flow_shift={args.flow_shift}  fps={args.fps}  seed={args.seed}")
    if args.fp8_file:
        print(f"[smoke] ROUTE B (FP8 single-file): {args.fp8_file}")
    else:
        print(f"[smoke] ROUTE A (bf16 diffusers repo). FP8 alt: {FP8_HINT}")

    t0 = time.time()

    # VAE in fp32 for decode quality (docs recommendation; matches director.py's note that the Wan VAE
    # dtype matters at decode). If the fp32 VAE OOMs the decode on 8GB, switch this to torch.bfloat16 --
    # WanBackend in director.py runs the VAE in bf16 for exactly that reason.
    vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae", torch_dtype=torch.float32)

    if args.fp8_file:
        # Route B: override the transformer with the FP8 single-file (LTX-distilled pattern).
        from diffusers import WanTransformer3DModel
        transformer = WanTransformer3DModel.from_single_file(args.fp8_file, torch_dtype=torch.bfloat16)
        pipe = WanImageToVideoPipeline.from_pretrained(
            MODEL, vae=vae, transformer=transformer, torch_dtype=torch.bfloat16)
    else:
        # Route A: plain bf16 repo load (mirrors WanBackend.load()).
        pipe = WanImageToVideoPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16)

    # 480p flow_shift on the UniPC flow scheduler (same scheduler family WanBackend configures).
    pipe.scheduler = UniPCMultistepScheduler.from_config(
        pipe.scheduler.config, prediction_type="flow_prediction",
        use_flow_sigmas=True, flow_shift=args.flow_shift)

    # --- 8GB survival recipe (identical strategy to director.py WanBackend.load) ---
    # Sequential offload: only the active submodule sits on the GPU; everything else lives in system
    # RAM and is streamed per forward. This is what makes 5B *fit*; it is ALSO what makes it slow.
    pipe.enable_sequential_cpu_offload()
    # VAE tiling + smaller tiles so the decode-moment peak doesn't OOM on the shared card (the exact
    # death point WanBackend documents for Wan2.1 -- the 5B's 64x VAE helps but tile anyway).
    pipe.vae.enable_tiling(tile_sample_min_height=160, tile_sample_min_width=160,
                           tile_sample_stride_height=128, tile_sample_stride_width=128)
    try:
        pipe.vae.enable_slicing()
    except Exception:
        pass

    t_load = time.time() - t0
    print(f"[smoke] pipeline ready in {t_load:.1f}s -- starting denoise (expect this to be the SLOW part)")

    image = load_image(args.image).convert("RGB").resize((W, H))
    g = torch.Generator("cpu").manual_seed(args.seed)

    def _cb(pipe_self, step, timestep, cbk):
        # Lightweight progress print. NO nvidia-smi / NVML anywhere (hard box rule).
        print(f"[smoke] step {step + 1}/{args.steps}", flush=True)
        return cbk

    t1 = time.time()
    result = pipe(
        image=image,
        prompt=args.prompt,
        negative_prompt=args.neg,
        height=H, width=W, num_frames=F,
        num_inference_steps=args.steps,
        guidance_scale=args.cfg,
        generator=g,
        output_type="pil",
        callback_on_step_end=_cb,
        callback_on_step_end_tensor_inputs=["latents"],
    )
    frames = result.frames[0]
    t_gen = time.time() - t1

    export_to_video(frames, args.out, fps=args.fps)
    total = time.time() - t0
    print("=" * 70)
    print(f"[smoke] DONE  out={args.out}  frames={len(frames)}")
    print(f"[smoke] load={t_load:.1f}s  denoise+decode={t_gen:.1f}s  total={total:.1f}s")
    print(f"[smoke] ~{t_gen / max(1, F):.1f}s/frame  ->  usable? judge by wall-clock, not by 'it ran'")
    print("=" * 70)


if __name__ == "__main__":
    main()
