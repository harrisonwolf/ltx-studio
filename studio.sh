#!/usr/bin/env bash
# LTX Studio dashboard (Pip-Boy job runner). Open in a real terminal.
cd "$(dirname "$0")" || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# Mirror stderr to studio.err (fresh each launch) so a crash leaves a traceback behind
# even if the terminal is left in a mess. (An OOM-kill is SIGKILL -> no traceback; see dmesg.)
exec ./venv/bin/python studio.py "$@" 2> >(tee studio.err >&2)
