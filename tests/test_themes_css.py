"""Theme system: curated set, full variable shape, CSS resolves + parses under EVERY theme
(the failure mode that crashes at app launch, invisible to py_compile), terminal-black wash guard."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio
from textual.css.stylesheet import Stylesheet

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

themes = studio.EXTRA_THEMES
check("14 curated themes, unique names", len(themes) == 14 and len({t.name for t in themes}) == 14)
CUSTOM = ("border", "border-strong", "surface-deep", "text-bright", "accent-2", "tertiary",
          "block-cursor-foreground", "block-cursor-background")
for t in themes:
    missing = [v for v in CUSTOM if not (t.variables or {}).get(v)]
    check("%s: full variable shape" % t.name, not missing, missing)

refs = sorted(set(re.findall(r"\$([a-z][a-z0-9-]*)", studio.Studio.CSS)))
for t in themes:
    merged = {**t.to_color_system().generate(), **(t.variables or {})}
    unresolved = [r for r in refs if r not in merged]
    check("%s: CSS vars resolve" % t.name, not unresolved, unresolved)
    try:
        ss = Stylesheet(variables=merged)
        try:
            ss.add_source(studio.Studio.CSS, path="t")
        except TypeError:
            ss.add_source(studio.Studio.CSS)
        ss.parse()
        check("%s: CSS parses" % t.name, True)
    except Exception as e:
        check("%s: CSS parses" % t.name, False, str(e)[:100])

check("no hardcoded hex in shell CSS", not re.findall(r"#[0-9a-fA-F]{6}", studio.Studio.CSS))

# terminal-black rule: canvas near-black; identity in fg/accents. gameboy = allowlisted exception.
def maxch(h):
    h = h.lstrip("#")
    return max(int(h[i:i + 2], 16) for i in (0, 2, 4))
for t in themes:
    if t.name == "pipboy-gameboy":
        continue
    bad = []
    if maxch(t.background) > 24: bad.append("bg")
    if maxch(t.surface) > 32: bad.append("surface")
    if maxch(t.panel) > 44: bad.append("panel")
    v = (t.variables or {}).get("surface-deep")
    if v and maxch(v) > 24: bad.append("surface-deep")
    check("%s: terminal-black canvas" % t.name, not bad, bad)

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
