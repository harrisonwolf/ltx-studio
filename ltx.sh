#!/usr/bin/env bash
# LTX-Video launcher. Examples:
#   ./ltx.sh --prompt "a fox trotting through snow, cinematic" --seconds 5
#   ./ltx.sh --image start.png --prompt "gentle waves, drifting clouds" --seconds 5 --steps 40
# Notes: width/height auto-round to /32; 8GB uses sequential offload (default).
cd "$(dirname "$0")" || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
exec ./venv/bin/python run_ltx.py "$@"
