#!/usr/bin/env bash
# LTX-Video Studio - Pip-Boy themed TUI. Open in a real terminal (not headless).
cd "$(dirname "$0")" || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
exec ./venv/bin/python studio.py "$@"
