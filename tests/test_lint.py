"""Undefined-name lint over every studio-owned module (pyflakes). This is the systematic guard
for the refactor's failure class: a helper moved between modules leaves a NameError that
py_compile can't see and only explodes when the user clicks the one button that reaches it
(_run_kind, then _status_glyph). Undefined name = suite failure. Other pyflakes chatter
(unused imports etc.) is reported but not fatal — light passes."""
import subprocess, sys, os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "venv", "bin", "python")
MODULES = ["studio.py", "studio_core.py", "studio_modals.py", "studio_themes.py",
           "studio_config.py", "preview_art.py", "sounds.py", "field_visuals.py",
           "readout.py", "dials_help.py", "style_presets.py", "experiment_log.py",
           "gpu_budget.py", "ltx_preview.py"]

r = subprocess.run([PY, "-m", "pyflakes"] + [os.path.join(REPO, m) for m in MODULES],
                   capture_output=True, text=True)
fatal, info = [], []
for line in (r.stdout + r.stderr).splitlines():
    (fatal if ("undefined name" in line or "used; unable to detect" in line) else info).append(line)

for line in info[:20]:
    print("  note:", line)
if fatal:
    print("UNDEFINED NAMES:")
    for line in fatal:
        print("  FAIL:", line)
print("RESULT:", "FAIL" if fatal else "PASS")
sys.exit(1 if fatal else 0)
