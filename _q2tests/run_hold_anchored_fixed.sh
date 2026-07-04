#!/usr/bin/env bash
# One-shot launcher for the fixed-build re-run of the hold-stress anchored arm.
cd /home/wolve/video_gen/FramePack || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
HOLD="a still life of a ceramic vase with white roses on an oak table, soft morning window light, locked-off camera, no motion, photoreal"
exec venv/bin/python director.py --prompt "$HOLD" --total 13 --seg 2 --steps 30 --cfg 3 --seed 42 \
    --backend ltx --latent_chain --latent_adain 0.7 --palette_lock 1.0 \
    --out outputs/hold_anchored.mp4 --frames_dir outputs/hold_anchored_frames
