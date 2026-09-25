"""Every field schematic renders inside its width budget with VALID markup, for a benign app, a
hostile app (every read raises), a mid-edit app (_plan raises) and hostile field VALUES, at every
panel width the studio can pass. Plus the SEG meter's modes.

Markup is checked the way the studio consumes it: rich's Text.from_markup (a MarkupError = FAIL),
widths are terminal CELLS of the rendered plain text (rich cell_len), and a None visual is a
failure (every VISUALS key has a visual; None means its render_fn raised)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import field_visuals as fv
from rich.text import Text
from rich.cells import cell_len

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

# render() floors unknown/tiny widths (<24) to the 48-col design width; 24 is the narrowest panel
WIDTHS = [None] + list(range(24, 90)) + [120, 200]


def budget(width):
    return width if (width and width >= 24) else 48


class Benign:
    SAFE_PX = 20_000_000
    vals = {"cfg": "3.0", "steps": "40", "seconds": "4", "seg": "3", "res": "704 x 480  balanced",
            "backend": "ltx", "fps": "24", "seed": "7", "cond_strength": "1.0", "mode": "single",
            "steadiness": "hold", "cfg_rescale": "0.5", "cfg_interval": "0.0:0.5", "wan_ref_anchor": "off"}
    plan = (704, 480, 24, 97, 49, 2, True)
    def v(self, k): return self.vals.get(k, "")
    def _plan(self): return self.plan

class Hostile:
    def v(self, k): raise RuntimeError("no")
    def _plan(self): raise RuntimeError("no")
    def __getattr__(self, k): raise RuntimeError("no")

class MidEdit(Benign):
    def _plan(self): raise RuntimeError("mid-edit")


def stub(cls=Benign, plan=None, **over):
    s = cls()
    s.vals = dict(Benign.vals, **over)
    if plan is not None:
        s.plan = plan
    return s


STUBS = [
    (Benign(), "benign"), (Hostile(), "hostile"), (MidEdit(), "mid-edit"),
    # long chain (seconds: 6-box row + '…+N' tail; literal [███] boxes must count as visible cols)
    (stub(seconds="250.25", mode="director", plan=(832, 480, 16, 4001, 45, 112, True)), "long-chain"),
    (stub(backend="wan", steadiness="evolve", wan_ref_anchor="on", cfg_rescale="on"), "wan/evolve"),
    (stub(backend="wan-turbo", steadiness="balanced", cfg_interval="off"), "turbo/balanced"),
    (stub(cfg="nan", steps="x", cond_strength="inf", seed="[/]"), "junk-values"),
    (stub(seed="[bold red]\\x[/]"), "markup-seed"),
    (stub(seed="種子値テスト"), "cjk-seed"),
    (stub(seed="a\\"), "backslash-seed"),
]

for app, label in STUBS:
    for key in sorted(fv.VISUALS):
        problems = []
        for width in WIDTHS:
            try:
                art = fv.render(key, app, width=width)
            except Exception as e:
                problems.append((width, "raised %r" % e)); continue
            if art is None:
                problems.append((width, "None (render_fn raised)")); continue
            try:
                plain = Text.from_markup(art).plain
            except Exception as e:
                problems.append((width, "markup: %s" % e)); continue
            wide = [ln for ln in plain.split("\n") if cell_len(ln) > budget(width)]
            if wide:
                problems.append((width, "%d cells > %d: %r" % (cell_len(wide[0]), budget(width), wide[0])))
        check("%s/%s: renders, valid markup, fits every width" % (key, label), not problems, problems[:2])

# A wrapped PROSE line keeps all of its text at every width (continuation lines were being clipped away).
for app, label in STUBS[:1] + STUBS[3:6]:
    for key in sorted(fv.VISUALS):
        lost = []
        for width in WIDTHS:
            fv._AVAIL = budget(width)
            try:
                raw = fv.VISUALS[key](app)
            except Exception:
                continue
            art = fv.render(key, app, width=width)
            if art is None:
                continue
            shown = "".join(Text.from_markup(art).plain.split())        # all visible non-space chars
            for ln in raw.split("\n"):
                if fv._is_prose(ln):
                    want = "".join(Text.from_markup(ln).plain.split())
                    if want not in shown:                                  # wrapping may move breaks,
                        lost.append((width, want[-20:]))                   # never drop text
        check("%s/%s: wrapped prose keeps all its text" % (key, label), not lost, lost[:2])

# BACKEND / STEADINESS: the selected option's ▲ marker is ON the (narrow) bar at every width
for key, field, choices in (("backend", "backend", ("ltx", "wan-turbo", "wan")),
                            ("steadiness", "steadiness", ("evolve", "balanced", "hold"))):
    for choice in choices:
        miss = [w for w in WIDTHS
                if "▲" not in Text.from_markup(fv.render(key, stub(**{field: choice}), width=w) or "").plain]
        check("%s=%s: ▲ marker visible at every width" % (key, choice), not miss, miss[:5])

# SEED: user text can't inject markup, and the card keeps its exact width in CELLS
for seed in ("[/]", "[b]bold?[/b]", "\\", "x\\", "[#ff0000]r", "種子値テスト", "漢a", "😀😀", "é́x",
             "nl\nx", "tab\tx", "12345678901234567890"):
    art = fv.render("seed", stub(seed=seed), width=48)
    try:
        t = Text.from_markup(art or "")
    except Exception as e:
        check("seed %r: valid markup" % seed, False, e); continue
    check("seed %r: renders" % seed, art is not None)
    check("seed %r: no injected styles (no bold / red)" % seed,
          not any("bold" in str(sp.style) or "ff0000" in str(sp.style) for sp in t.spans),
          [str(sp.style) for sp in t.spans if "bold" in str(sp.style)])
    rows = t.plain.split("\n")
    card = [cell_len(r) for r in rows[:3]] + [cell_len(r) for r in rows[4:7]]
    check("seed %r: card rows all the same cell width" % seed, len(set(card)) == 1, card)

# SEG meter modes: single = AUTO cap; director honors + caps
d1 = stub(mode="single")
a = fv.render("seg", d1, width=48) or ""
check("seg single mode says AUTO", "AUTO" in a.upper(), a[:80])
d2 = stub(mode="director", seg="1.5")
check("seg director mode renders", bool(fv.render("seg", d2, width=48)))
d3 = stub(mode="director", seg="9")
check("seg over-cap flags capped", "cap" in (fv.render("seg", d3, width=48) or "").lower())

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
