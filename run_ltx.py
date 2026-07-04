#!/usr/bin/env python
"""LTX-Video CLI (diffusers). Text-to-video, or image-to-video if --image is given.
8GB-tuned: 2B transformer stays resident on GPU; T5 text encoder offloads via
enable_model_cpu_offload; VAE tiling on. No compilation / fp8 needed.

  python run_ltx.py --prompt "..." --seconds 5
  python run_ltx.py --image start.png --prompt "..." --seconds 5
"""
import argparse, os, time
print("[[PHASE importing]]", flush=True)
print("[[LOAD 1 5 initializing torch + diffusers]]", flush=True)
import torch
from gpu_budget import cap_vram; cap_vram()   # leave ~12% VRAM free for the OS (clean OOM, never a choke)

ap = argparse.ArgumentParser()
ap.add_argument("--prompt", required=True)
ap.add_argument("--image", default=None, help="start image -> image-to-video; omit for text-to-video")
ap.add_argument("--n_prompt", default="worst quality, blurry, distorted, jittery, low detail, "
                "deformed, malformed anatomy, missing or extra limbs, mutated, fused body, headless")
ap.add_argument("--seconds", type=float, default=5.0)
ap.add_argument("--fps", type=int, default=24)
ap.add_argument("--width", type=int, default=704)
ap.add_argument("--height", type=int, default=480)
ap.add_argument("--steps", type=int, default=40)
ap.add_argument("--cfg", type=float, default=3.0)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--fast_offload", action="store_true", help="model-level offload (needs more free VRAM); default is sequential (8GB-safe)")
ap.add_argument("--frames_dir", default=None, help="also dump PNG frames here (for the AnimateDiff enhance suite)")
ap.add_argument("--out", default="outputs/ltx.mp4")
ap.add_argument("--preview", default=None, help="path to write a small live-preview PNG of the final frame")
ap.add_argument("--ltx_repo", default="Lightricks/LTX-Video-0.9.5",
                help="LTX diffusers checkpoint repo (escape hatch: Lightricks/LTX-Video = 0.9.0)")
ap.add_argument("--ltx_variant", default=None, choices=["distilled"],
                help="LTX transformer variant (distilled = 0.9.8-distilled 2B, forces few-step/cfg=1.0). "
                     "Omit -> byte-identical 0.9.5 run. Mirrors director.py.")
args = ap.parse_args()
# --ltx_variant distilled: swap ONLY the transformer for the 0.9.8-distilled 2B single-file checkpoint,
# keeping 0.9.5's T5/tokenizer/VAE/scheduler (same as director.py's distilled path). The distill is
# CFG-distilled + few-step, so clamp steps<=8 and force cfg=1.0 HERE -- so the [[STEP]] totals, the
# per-step preview gates, and the decode-phase flip (all driven by args.steps) agree with what runs.
_distilled = getattr(args, "ltx_variant", None) == "distilled"
if _distilled:
    args.steps = min(args.steps, 8)
    args.cfg = 1.0

# LTX requires width/height divisible by 32 and num_frames % 8 == 1
W = (args.width // 32) * 32
H = (args.height // 32) * 32
num_frames = int(args.seconds * args.fps)
num_frames = (num_frames // 8) * 8 + 1

from diffusers import LTXPipeline, LTXImageToVideoPipeline
from diffusers.utils import export_to_video, load_image
import ltx_preview

# live-preview cadence: skip the pure-noise phase, then every K steps, always the last step
PREVIEW_EVERY = 3
PREVIEW_START = max(1, int(0.3 * args.steps))
# T18: wall-clock floor (dial via STUDIO_PREVIEW_SEC, default 15s) so a slow/long step still refreshes the
# LIVE pane within ~PREVIEW_SEC instead of only every PREVIEW_EVERY steps. Never widens the cadence.
try:
    PREVIEW_SEC = max(1.0, float(os.environ.get("STUDIO_PREVIEW_SEC", "15.0")))
except Exception:
    PREVIEW_SEC = 15.0
_last_preview = 0.0
_LF, _LH, _LW = ltx_preview.latent_grid_dims(W, H, num_frames)

print("[[PHASE loading]]", flush=True)
print("[[LOAD 2 5 loading LTX checkpoint (largest part)]]", flush=True)
# Match director.py's Q1 load pattern so single clips and chained runs use the SAME checkpoint (0.9.5
# default) and the SAME escape hatch. Reuse the already-cached T5 -- 0.9.5 ships its own ~9GB
# text_encoder shards; loading them naively re-downloads weights we already have.
base = "Lightricks/LTX-Video"                         # T5 + tokenizer already in the HF cache
repo = getattr(args, "ltx_repo", None) or "Lightricks/LTX-Video-0.9.5"
PipeCls = LTXImageToVideoPipeline if args.image else LTXPipeline
extra = {}
if _distilled:
    # 0.9.8-distilled 2B: single-file checkpoint cached in the 0.9.0 base repo
    # (ltxv-2b-0.9.8-distilled.safetensors, ~6.3GB bf16). Pass it in as the transformer= override so the
    # pipeline never downloads/builds the repo's own default transformer -- mirrors director.py:365-374.
    from diffusers import LTXVideoTransformer3DModel
    from huggingface_hub import hf_hub_url
    url = hf_hub_url(base, "ltxv-2b-0.9.8-distilled.safetensors")
    extra["transformer"] = LTXVideoTransformer3DModel.from_single_file(url, torch_dtype=torch.bfloat16)
if repo == base:
    pipe = PipeCls.from_pretrained(base, torch_dtype=torch.bfloat16, **extra)
else:
    # slow T5Tokenizer (NOT T5TokenizerFast): model_index.json specifies the slow class; the fast class
    # would trigger a slow->fast conversion needing protobuf (not installed here) -- mirrors director.py.
    from transformers import T5EncoderModel, T5Tokenizer
    te = T5EncoderModel.from_pretrained(base, subfolder="text_encoder", torch_dtype=torch.bfloat16)
    tok = T5Tokenizer.from_pretrained(base, subfolder="tokenizer")
    pipe = PipeCls.from_pretrained(repo, text_encoder=te, tokenizer=tok, torch_dtype=torch.bfloat16, **extra)
print(f"LTX checkpoint: {repo}" + (" (variant: 0.9.8-distilled 2B)" if _distilled else ""), flush=True)
print("[[LOAD 3 5 building pipeline]]", flush=True)

print("[[PHASE offload]]", flush=True)
print("[[LOAD 4 5 wiring CPU<->GPU offload (8GB mode)]]", flush=True)
if args.fast_offload:
    pipe.enable_model_cpu_offload()      # whole-model swaps; faster but needs ~10GB free VRAM for the T5
else:
    pipe.enable_sequential_cpu_offload()  # layer-by-layer; fits 8GB (T5 won't fit whole), slower
pipe.vae.enable_tiling()

print(f"mode={'i2v' if args.image else 't2v'}  {W}x{H}  frames={num_frames}  steps={args.steps}")
gen = torch.Generator("cpu").manual_seed(args.seed)
kw = dict(prompt=args.prompt, negative_prompt=args.n_prompt, width=W, height=H,
          num_frames=num_frames, num_inference_steps=args.steps, guidance_scale=args.cfg, generator=gen)
if repo != base:      # 0.9.5's VAE is timestep-conditioned -- pass the decode kwargs only then (0.9.0 omits)
    kw["decode_timestep"] = 0.05
    kw["decode_noise_scale"] = 0.025
if args.image:
    kw["image"] = load_image(args.image)

def _cb(pp, i, t, cbk):
    global _last_preview
    if i == 0:
        print("[[PHASE generating]]", flush=True)
    print(f"[[STEP {i + 1} {args.steps}]]", flush=True)
    if i + 1 == args.steps:
        print("[[PHASE decoding]]", flush=True)
    # best-effort live preview from the in-flight latent (never crashes the run). T18: fire on the per-step
    # stride OR when >=PREVIEW_SEC of wall-clock has elapsed since the last write (floor for slow steps).
    due = (i + 1 == args.steps
           or (i >= PREVIEW_START and i % PREVIEW_EVERY == 0)
           or (i >= PREVIEW_START and time.monotonic() - _last_preview >= PREVIEW_SEC))
    if args.preview and due:
        try:
            if ltx_preview.write_preview_from_latents(cbk.get("latents"), args, _LF, _LH, _LW):
                _last_preview = time.monotonic()
                print(f"[[PREVIEW {i + 1}]]", flush=True)
        except Exception as e:
            print("[[PREVIEW-ERR]]", e, flush=True)
    return cbk

kw["callback_on_step_end"] = _cb
print(f"[[SEG 1 1]]", flush=True)
print("[[PHASE warmup]]", flush=True)
print("[[LOAD 5 5 warming up CUDA (first step is slow)]]", flush=True)
frames = pipe(**kw).frames[0]
if args.preview and frames:
    try:
        _pim = frames[-1]; _pw, _ph = _pim.size
        _pim = _pim.resize((160, max(1, round(160 * _ph / _pw))))
        ltx_preview.atomic_save_png(_pim, args.preview)
    except Exception:
        pass
print("[[PHASE saving]]", flush=True)
os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
export_to_video(frames, args.out, fps=args.fps)
if args.frames_dir:
    os.makedirs(args.frames_dir, exist_ok=True)
    for i, im in enumerate(frames):
        im.save(os.path.join(args.frames_dir, f"{i:04d}.png"))
    print("frames ->", args.frames_dir)
print("LTX_DONE", args.out)
