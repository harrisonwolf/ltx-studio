#!/usr/bin/env bash
# LTX Studio dashboard (Pip-Boy job runner). Open in a real terminal.
# This repo ships SOURCE ONLY. Create a venv and `pip install -r requirements.txt`
# (see README), or point LTX_PYTHON at an existing interpreter, e.g.:
#   LTX_PYTHON=/path/to/venv/bin/python ./studio.sh
cd "$(dirname "$0")" || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Resolve a Python interpreter: LTX_PYTHON override -> activated venv -> ./venv -> python3.
if   [ -n "$LTX_PYTHON" ]   && [ -x "$LTX_PYTHON" ];             then PY="$LTX_PYTHON"
elif [ -n "$VIRTUAL_ENV" ]  && [ -x "$VIRTUAL_ENV/bin/python" ]; then PY="$VIRTUAL_ENV/bin/python"
elif [ -x "./venv/bin/python" ];                                then PY="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1;                        then PY="python3"
else echo "studio.sh: no Python found. Create a venv + 'pip install -r requirements.txt' (see README), or set LTX_PYTHON." >&2; exit 1
fi

# Mirror stderr to studio.err (fresh each launch) so a crash leaves a traceback behind
# even if the terminal is left in a mess. (An OOM-kill is SIGKILL -> no traceback; see dmesg.)
exec "$PY" studio.py "$@" 2> >(tee studio.err >&2)
