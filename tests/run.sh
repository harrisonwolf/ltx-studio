#!/usr/bin/env bash
# Minimal test runner: every tests/test_*.py plus the pre-existing module suites.
# Each test is a standalone script that exits nonzero on failure — no framework, by design.
cd "$(dirname "$0")/.." || exit 1
PY=${LTX_PYTHON:-./venv/bin/python}
fails=0; total=0
run() {
    total=$((total + 1))
    out=$("$PY" "$1" 2>&1); rc=$?
    if [ $rc -eq 0 ]; then
        printf '  \033[32mPASS\033[0m  %s\n' "$1"
    else
        fails=$((fails + 1))
        printf '  \033[31mFAIL\033[0m  %s\n' "$1"
        echo "$out" | tail -15 | sed 's/^/        /'
    fi
}
echo "== LTX Studio test suite =="
for f in tests/test_*.py; do run "$f"; done
run _t22tests/test_readout.py
run _q2tests/test_units.py
echo "== $((total - fails))/$total passed =="
exit $((fails > 0))
