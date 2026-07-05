#!/usr/bin/env bash
# LTX-Video Studio - Pip-Boy themed TUI. Open in a real terminal (not headless).
# Source-only repo: create a venv + `pip install -r requirements.txt`, or set LTX_PYTHON.
cd "$(dirname "$0")" || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Resolve a Python interpreter: LTX_PYTHON override -> activated venv -> ./venv -> python3.
if   [ -n "$LTX_PYTHON" ]   && [ -x "$LTX_PYTHON" ];             then PY="$LTX_PYTHON"
elif [ -n "$VIRTUAL_ENV" ]  && [ -x "$VIRTUAL_ENV/bin/python" ]; then PY="$VIRTUAL_ENV/bin/python"
elif [ -x "./venv/bin/python" ];                                then PY="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1;                        then PY="python3"
else echo "ltx-studio.sh: no Python found. Create a venv + 'pip install -r requirements.txt' (see README), or set LTX_PYTHON." >&2; exit 1
fi

exec "$PY" studio.py "$@"
