# LTX-Video on this machine (8 GB RTX 5070, WSL)

Modern, realistic text/image-to-video that actually runs well on 8 GB. Chosen over the
FramePack GUI because FramePack's 13B HunyuanVideo is too slow on 8 GB (3–15 min/step);
LTX's 2B transformer fits in VRAM, so sampling is fast (~1–3 s/step).

Shares the FramePack venv (diffusers 0.33.1 / torch cu128). No compilation, no fp8 needed.

## Run
```bash
cd ~/video_gen/FramePack
# text-to-video
./ltx.sh --prompt "a fox trotting through snow, cinematic, highly detailed" --seconds 5 --steps 40
# image-to-video (animate a start frame)
./ltx.sh --image input/start.png --prompt "gentle waves, drifting clouds" --seconds 5 --steps 40
```
Output MP4 lands in `outputs/`.

## Dials
- `--prompt` / `--n_prompt`   what to make / avoid
- `--image PATH`              start frame -> image-to-video (omit = text-to-video)
- `--seconds` `--fps`         clip length (frames = seconds*fps, auto-rounded to 8k+1)
- `--width` `--height`        auto-rounded to /32 (try 704x480, 512x320 for speed/VRAM)
- `--steps`                   ~30–50 for quality
- `--cfg`                     guidance (default 3.0)
- `--seed`                    reproducibility
- `--fast_offload`            faster, but needs ~10 GB free VRAM (don't use on 8 GB)
- `--frames_dir DIR`          also dump PNG frames (to feed the enhance suite)

## Speed (measured, 8 GB)
~1.2 s/step at 512x320, ~3.1 s/step at 704x480 (+ ~2.5 min one-time model load).
A 3 s 704x480 clip ≈ 5 min total. Time scales with resolution x frames x steps.

## Enhance with the AnimateDiff suite (RIFE / upscale / face)
LTX's enhance suite lives in the AnimateDiff repo. Dump frames, then run it:
```bash
./ltx.sh --image input/x.png --prompt "..." --seconds 5 --frames_dir outputs/ltx_frames --out outputs/raw.mp4
cd ~/video_gen/AnimateDiff
./venv/bin/python -m scripts.enhance --frames ~/video_gen/FramePack/outputs/ltx_frames \
    --interp 2 --upscale --upscaler ultrasharp --face --out ~/video_gen/FramePack/outputs/final.mp4 --fps 48
```
