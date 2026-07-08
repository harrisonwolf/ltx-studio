"""ultra_art: the animated pixel-art decorations for the ultra-themes tier. Guards the properties the
studio relies on — purity (deterministic frames), real motion, the STUDIO_NO_ANIM freeze, width-fit +
balanced markup at every size/beat, and that the module's THEMES match studio_themes.ULTRA_NAMES."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ultra_art
import studio_themes

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

TAG = re.compile(r"\[/?[^\]]*\]")
def plain(s): return TAG.sub("", s)

NAMES = list(ultra_art.THEMES)
check("three ultra decorations", len(NAMES) == 3)
check("THEMES == ULTRA_NAMES", set(NAMES) == set(studio_themes.ULTRA_NAMES))
check("every ultra theme has a decoration + title",
      all(ultra_art.is_ultra(n) and n in ultra_art.TITLES for n in studio_themes.ULTRA_NAMES))
check("non-ultra -> None / False",
      ultra_art.render("pipboy", 0) is None and not ultra_art.is_ultra("pipboy"))

# purity: same (name, beat) -> identical output
for n in NAMES:
    check("%s: pure (beat 3 reproducible)" % n, ultra_art.render(n, 3) == ultra_art.render(n, 3))

# motion: the frame at beat 0 differs from a later beat (color sweep counts)
for n in NAMES:
    frames = {ultra_art.render(n, b) for b in range(0, 12)}
    check("%s: animates (distinct frames)" % n, len(frames) > 1, frames)

# safety: no raise, width-fit (<=48 budget), balanced markup, at every size/beat
for n in NAMES:
    worst, balanced, raised = 0, True, False
    for w in (8, 16, 24, 30, None):
        for b in range(0, 9):
            try:
                art = ultra_art.render(n, b, width=w)
            except Exception:
                raised = True; art = ""
            art = art or ""
            if art.count("[") != art.count("]"):
                balanced = False
            for ln in art.split("\n"):
                worst = max(worst, len(plain(ln)))
    check("%s: never raises" % n, not raised)
    check("%s: fits 48-col budget (max=%d)" % (n, worst), worst <= 48)
    check("%s: balanced markup" % n, balanced)

# freeze switch: STUDIO_NO_ANIM pins every theme to frame 0 (headless/reduce-motion)
os.environ["STUDIO_NO_ANIM"] = "1"
try:
    for n in NAMES:
        check("%s: STUDIO_NO_ANIM freezes to frame 0" % n,
              ultra_art.render(n, 9) == ultra_art.render(n, 0))
finally:
    del os.environ["STUDIO_NO_ANIM"]

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
