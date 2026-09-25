# LTX-Video CLI (`ltx.sh` / `run_ltx.py`)

A headless single-clip text/image-to-video runner that works well on an 8 GB GPU. The studio uses the same script for short single-shot LTX runs; this page covers using it directly.

LTX was chosen over the FramePack GUI because FramePack's 13B HunyuanVideo is too slow on 8 GB (3–15 min/step). LTX's 2B transformer samples at ~1–3 s/step even with the default sequential CPU offload (weights stream to the GPU layer by layer, so it stays 8 GB-safe).

Use a Python 3.10+ env with this repo's `requirements.txt` (diffusers >=0.34, torch cu128 on RTX 50-series). No compilation, no fp8 needed.

## Run
From the repo root:
```bash
# text-to-video
./ltx.sh --prompt "a fox trotting through snow, cinematic, highly detailed" --seconds 5 --steps 40
# image-to-video (animate a start frame)
./ltx.sh --image input/start.png --prompt "gentle waves, drifting clouds" --seconds 5 --steps 40
```
Output goes to `outputs/ltx.mp4` by default. Each run overwrites it, so pass `--out` to keep clips.

## Dials
| Flag | Default | Notes |
|------|---------|-------|
| `--prompt` / `--n_prompt` | (required) / artifact list | What to make / avoid |
| `--image PATH` | none | Start frame → image-to-video (omit = text-to-video) |
| `--anchors` | none | Style/subject tokens appended to the prompt (the studio's ANCHORS field) |
| `--seconds` `--fps` | 5, 24 | Frames = seconds × fps, rounded **down** to 8k+1 |
| `--width` `--height` | 704, 480 | Rounded **down** to a multiple of 32 (try 512x320 for speed/VRAM) |
| `--steps` | 40 | ~30–50 for quality |
| `--cfg` | 3.0 | Guidance |
| `--seed` | 0 | Reproducibility |
| `--out` | `outputs/ltx.mp4` | Output path |
| `--frames_dir DIR` | none | Also dump PNG frames (to feed an enhance suite) |
| `--preview PATH` | none | Write a small live-preview PNG during sampling |
| `--ltx_repo` | `Lightricks/LTX-Video-0.9.5` | Checkpoint repo (`Lightricks/LTX-Video` = the old 0.9.0) |
| `--ltx_variant distilled` | off | 0.9.8-distilled 2B transformer; forces steps ≤ 8 and cfg 1.0 |
| `--fast_offload` | off | Model-level offload: faster, but needs ~10 GB free VRAM (don't use on 8 GB) |

## Speed (measured, 8 GB)
~1.2 s/step at 512x320, ~3.1 s/step at 704x480 (+ ~2.5 min one-time model load).
A 3 s 704x480 clip ≈ 5 min total. Time scales with resolution × frames × steps.

## Enhance (RIFE / upscale / face)
In the studio, ▲ ENHANCE runs these passes for you on a finished run. It needs an external AnimateDiff checkout that provides `scripts.enhance`; point `LTX_ANIMATEDIFF_REPO` at it (see the main README). To do it by hand, dump frames and run the suite from that repo:
```bash
./ltx.sh --image input/x.png --prompt "..." --seconds 5 --frames_dir outputs/ltx_frames --out outputs/raw.mp4
cd "$LTX_ANIMATEDIFF_REPO"
./venv/bin/python -m scripts.enhance --frames /path/to/ltx-studio/outputs/ltx_frames \
    --interp 2 --upscale --upscaler ultrasharp --face --out /path/to/ltx-studio/outputs/final.mp4 --fps 48
```
