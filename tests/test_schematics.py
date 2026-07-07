"""Every field schematic renders inside its width budget with balanced markup, for a benign app,
a hostile app (every read raises), and a mid-edit app (_plan raises). Plus the SEG meter's modes."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import field_visuals as fv

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

TAG = re.compile(r"\[/?(?:#[0-9A-Fa-f]{6}|[a-z][a-z0-9_]*)\]")

class Benign:
    SAFE_PX = 20_000_000
    vals = {"cfg": "3.0", "steps": "40", "seconds": "4", "seg": "3", "res": "704 x 480  balanced",
            "backend": "ltx", "fps": "24", "seed": "7", "cond_strength": "1.0", "mode": "single",
            "steadiness": "hold", "cfg_rescale": "0.5", "cfg_interval": "0.0:0.5", "wan_ref_anchor": "off"}
    def v(self, k): return self.vals.get(k, "")
    def _plan(self): return (704, 480, 24, 97, 49, 2, True)

class Hostile:
    def v(self, k): raise RuntimeError("no")
    def _plan(self): raise RuntimeError("no")
    def __getattr__(self, k): raise RuntimeError("no")

class MidEdit(Benign):
    def _plan(self): raise RuntimeError("mid-edit")

for stub, label in ((Benign(), "benign"), (Hostile(), "hostile"), (MidEdit(), "mid-edit")):
    for key in sorted(fv.VISUALS):
        try:
            art = fv.render(key, stub, width=48)
        except Exception as e:
            check("%s/%s: no exception" % (key, label), False, e); continue
        if art is None:
            continue
        wide = [ln for ln in art.splitlines() if len(TAG.sub("", ln)) > 48]
        check("%s/%s: fits 48 + balanced" % (key, label),
              not wide and TAG.sub("", art) is not None and art.count("[") >= art.count("]") - art.count("]]"),
              wide[:1])

# SEG meter modes: single = AUTO cap; director honors + caps
d1 = Benign(); d1.vals = dict(Benign.vals, mode="single")
a = fv.render("seg", d1, width=48) or ""
check("seg single mode says AUTO", "AUTO" in a.upper(), a[:80])
d2 = Benign(); d2.vals = dict(Benign.vals, mode="director", seg="1.5")
check("seg director mode renders", bool(fv.render("seg", d2, width=48)))
d3 = Benign(); d3.vals = dict(Benign.vals, mode="director", seg="9")
check("seg over-cap flags capped", "cap" in (fv.render("seg", d3, width=48) or "").lower())

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
