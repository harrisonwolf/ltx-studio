#!/usr/bin/env bash
# LTX-Video launcher. Examples:
#   ./ltx.sh --prompt "a fox trotting through snow, cinematic" --seconds 5
#   ./ltx.sh --image start.png --prompt "gentle waves, drifting clouds" --seconds 5 --steps 40
# Notes: width/height auto-round to /32; 8GB uses sequential offload (default).
# Source-only repo: create a venv + `pip install -r requirements.txt`, or set LTX_PYTHON.
cd "$(dirname "$0")" || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Resolve a Python interpreter: LTX_PYTHON override -> activated venv -> ./venv -> python3.
if   [ -n "$LTX_PYTHON" ]   && [ -x "$LTX_PYTHON" ];             then PY="$LTX_PYTHON"
elif [ -n "$VIRTUAL_ENV" ]  && [ -x "$VIRTUAL_ENV/bin/python" ]; then PY="$VIRTUAL_ENV/bin/python"
elif [ -x "./venv/bin/python" ];                                then PY="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1;                        then PY="python3"
else echo "ltx.sh: no Python found. Create a venv + 'pip install -r requirements.txt' (see README), or set LTX_PYTHON." >&2; exit 1
fi

exec "$PY" run_ltx.py "$@"
