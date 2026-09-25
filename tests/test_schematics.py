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

# ...and it lands on the SAME zone of the (rescaled) bar as at the 48-col design width, the three
# option glyphs keep their left-to-right order, and the zone labels stay whole + in order
# (a colliding label slides right instead of vanishing: STEADINESS kept only 'lots of motion'
# at 24-28 and lost 'gentle' at 37-40; BACKEND clipped 'nicer (slowe' at 24-25).
def _zone_at(bar, col):
    return [str(sp.style) for sp in bar.spans if sp.start <= col < sp.end]

# label -> the narrowest panel width at which it must be shown (whole); >= 2 labels always
for key, choices, labels, need in (
        ("backend", ("ltx", "wan-turbo", "wan"), ("fast", "nicer (slower)"), (24, 24)),
        ("steadiness", ("evolve", "balanced", "hold"), ("lots of motion", "gentle", "locked-off"),
         (24, 37, 29))):
    for choice in choices:
        ref = [Text.from_markup(ln) for ln in fv.render(key, stub(**{key: choice}), width=48).split("\n")]
        want = _zone_at(ref[0], ref[1].plain.index("▲"))
        bad = []
        for w in range(24, 201):
            t = [Text.from_markup(ln) for ln in fv.render(key, stub(**{key: choice}), width=w).split("\n")]
            mk, pins = t[1].plain, t[2].plain
            col = mk.index("▲") if "▲" in mk else -1
            if col < 0 or col >= w or _zone_at(t[0], col) != want:
                bad.append((w, "▲ zone", col, _zone_at(t[0], col), want))
            glyphs = [i for i, ch in enumerate(mk) if ch in "▲·"]
            if len(glyphs) != 3 or mk[glyphs[choices.index(choice)]] != "▲":
                bad.append((w, "option order", mk))
            pos = [pins.find(lb) for lb in labels]
            shown = [p for p in pos if p >= 0]
            if shown != sorted(shown) or len(shown) < 2 or any(p < 0 <= w - n for p, n in zip(pos, need)):
                bad.append((w, "labels", pins))
        check("%s=%s: ▲ on its design zone, options + labels in order, 24..200" % (key, choice),
              not bad, bad[:3])

# the exact zone-label rows (narrow panels + the 48-col design width, which must never change)
for key, w, want in (
        ("backend", 24, "    fast  nicer (slower)"), ("backend", 25, "    fast   nicer (slower)"),
        ("backend", 32, "    fast        nicer (slower)"),
        ("backend", 48, "      fast                    nicer (slower)"),
        ("steadiness", 24, "  lots of motion gentle"), ("steadiness", 32, "  lots of motion    locked-off"),
        ("steadiness", 40, "  lots of motion gentle   locked-off"),
        ("steadiness", 48, "  lots of motion   gentle      locked-off")):
    got = Text.from_markup(fv.render(key, stub(), width=w)).plain.split("\n")[2]
    check("%s @%d: zone labels %r" % (key, w, want), got == want, repr(got))

# SEED: user text can't inject markup, and the card keeps its exact width in CELLS
for seed in ("[/]", "[b]bold?[/b]", "\\", "x\\", "[#ff0000]r", "種子値テスト", "漢a", "😀😀", "é́x",
             "nl\nx", "tab\tx", "12345678901234567890",
             # rich measures these differently from unicodedata's East-Asian width: skin-tone
             # modifier (0 cells), trigrams / monograms / hexagrams / Tai Xuan Jing (2), Hangul fillers (0)
             "👍🏽", "👍🏽👍🏽", "☰☰☰☰", "⚊⚋⚌⚍", "䷀䷁", "𝌆𝌇", "\u3164\u3164", "ᅠᅡ", "\uffa0x"):
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
    zw = [ch for ch in rows[1] if cell_len(ch) == 0]     # a 0-cell char (skin-tone modifier, Hangul
    check("seed %r: no zero-width chars reach the card" % seed, not zw, zw)   # filler) -> '?'
    over = [(w, ln) for w in WIDTHS[1:] for ln in Text.from_markup(
            fv.render("seed", stub(seed=seed), width=w) or "").plain.split("\n") if cell_len(ln) > w]
    check("seed %r: every line fits every width (clip measured like rich)" % seed, not over, over[:2])

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
