#!/usr/bin/env python
"""LTX Studio themes: the curated pipboy family + the SPAL inline-markup palette.

Split out of studio.py (2026-07-06 light restructuring): pure code motion, no behavior
change — imports are the only wiring. See tests/ for the regression net.

2026-07-07 — BLOOM-ON-BLACK overhaul (design workflow: 6 visions, judge panel, synthesis).
Every theme is ONE real light-emitting instrument, bound by four rules so ten machines read as
one system:
  (1) BLOOM-ON-BLACK   — background/surface/panel/surface-deep are the faintest tint of the
                          device's OWN emission, never neutral grey (still under the wash guard).
  (2) ONE HERO EMISSION — `accent` is the device's true emission peak, held in reserve; the eye
                          finds it because nothing else competes. `text-bright` is its halation.
  (3) ONE PHYSICAL COUNTERPOINT — `accent-2` is the real SECOND color the device shows (a nixie's
                          cold cathode-blue, a Nuka machine's cap-silver, a UV lab's fluoro-green,
                          a thermal scope's cold-pole cyan, a B&W TV's lone green power LED).
  (4) SELECTION IS THE ONE CHROMATIC EVENT — the new `selection` slot is a bounded low-chroma
                          DEPTH lift (one value-step above panel, NOT border-strong, NOT the hero)
                          used by the DataTable row cursor; the highlighted queue card re-lights
                          its OWN frame in the hero accent. Two channels, never a muddy flood.
Curated 10 (cut white/plasma/midnight/wasteland/ice/radium as duplicates; added nixie + thermal).
"""

from textual.theme import Theme

# --- green phosphor CRT — the canonical Fallout Pip-Boy tube (the namesake / default) ---
PIPBOY = Theme(
    name="pipboy",
    primary="#2fae5f",
    secondary="#1f9a52",
    accent="#6dffab",
    foreground="#34d977",
    background="#06120b",
    surface="#08170d",
    panel="#0b2011",
    success="#9dffce",
    warning="#ffcf5c",
    error="#ff6d6d",
    dark=True,
    variables={
        "block-cursor-foreground": "#06120b",
        "block-cursor-background": "#6dffab",
        "footer-key-foreground": "#9dffce",
        "footer-description-foreground": "#34d977",
        "border": "#1c7a42",
        "border-strong": "#134a2a",     # unfocused-input / hover / active-button fill (NOT the cursor now)
        "surface-deep": "#040d08",
        "text-bright": "#7dffb8",
        "accent-2": "#e8c26a",          # amber persistence-burn a real green CRT leaves behind
        "tertiary": "#5bbf83",          # supporting text — card params line etc.
        "selection": "#1f2c25",         # DataTable row-cursor DEPTH lift (green-graphite, > panel)
    },
)

# CURATED SET — every theme is a REFERENCE OBJECT (a real emissive display / machine), never a hue
# rotation, and carries the full variable shape (border/border-strong/surface-deep/text-bright +
# accent-2/tertiary inline slots + the selection cursor lift). The Ctrl+K picker lists ONLY this
# family; builtin textual themes are cut (get_css_variables still guards them if forced).

# --- P3 amber-phosphor monochrome monitor (DEC/IBM terminal glass) ---
PIPBOY_AMBER = Theme(
    name="pipboy-amber",
    primary="#c98a1a", secondary="#a8720f", accent="#ffcf5c",
    foreground="#e0a83e", background="#140d02", surface="#1c1204", panel="#281905",
    success="#ffe29a", warning="#f2953a", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#140d02", "block-cursor-background": "#ffcf5c",
        "footer-key-foreground": "#ffe29a", "footer-description-foreground": "#e0a83e",
        "border": "#7a5310", "border-strong": "#4a3208",
        "surface-deep": "#0f0a02", "text-bright": "#f2c569",
        "accent-2": "#ff8c42", "tertiary": "#b8923a",    # hotter ember strike · aged brass
        "selection": "#302a20",                          # warm-graphite lift
    },
)
# --- vacuum-fluorescent display (hi-fi / VCR segment tube) behind indigo filter glass ---
PIPBOY_VFD = Theme(
    name="pipboy-vfd",
    primary="#17ae94", secondary="#0f9a82", accent="#6dffe8",
    foreground="#3fe0c8", background="#03110f", surface="#051a16", panel="#072420",
    success="#b8fff2", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#03110f", "block-cursor-background": "#6dffe8",
        "footer-key-foreground": "#b8fff2", "footer-description-foreground": "#3fe0c8",
        "border": "#0f7a66", "border-strong": "#0a4a3e",
        "surface-deep": "#020b0a", "text-bright": "#7df0dc",
        "accent-2": "#8f7dff", "tertiary": "#2fae94",    # deep indigo of the filter glass · deep teal
        "selection": "#1c2e2a",                          # teal-graphite lift
    },
)
# --- Vault-Tec vault suit — utility navy with vault-gold trim (the one intentional two-hue theme) ---
PIPBOY_VAULT = Theme(
    name="pipboy-vault",
    # TERMINAL-BLACK RULE: the CANVAS stays near-black; identity is the cool navy structure + the
    # warm vault-gold accent detonating as pure signal. v1 washed the whole screen navy — cut.
    primary="#2b6cb0", secondary="#1f4f8a", accent="#ffd24a",
    foreground="#8fb3dd", background="#05080f", surface="#070c17", panel="#0a1222",
    success="#a8d1ff", warning="#f2953a", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#05080f", "block-cursor-background": "#ffd24a",
        "footer-key-foreground": "#a8d1ff", "footer-description-foreground": "#8fb3dd",
        "border": "#1c477a", "border-strong": "#122a48",
        "surface-deep": "#030509", "text-bright": "#a9cdf6",
        "accent-2": "#ff8c42", "tertiary": "#5a86b8",    # suit caution-stripe orange · suit-steel blue
        "selection": "#1c2230",                          # navy-graphite lift
    },
)
# --- blacklight UV lab phosphor under 365nm excitation (the cold corner no other theme touches) ---
PIPBOY_VIOLET = Theme(
    name="pipboy-violet",
    primary="#8a4fc0", secondary="#6f3da0", accent="#e3c3ff",
    foreground="#b98ae0", background="#0e0616", surface="#140a1e", panel="#1a0e28",
    success="#d9b8ff", warning="#ffcf5c", error="#ff6d8a", dark=True,
    variables={
        "block-cursor-foreground": "#0e0616", "block-cursor-background": "#e3c3ff",
        "footer-key-foreground": "#d9b8ff", "footer-description-foreground": "#b98ae0",
        "border": "#5a3380", "border-strong": "#3a2054",
        "surface-deep": "#090310", "text-bright": "#d0a8f0",
        "accent-2": "#7dffb8", "tertiary": "#9a6fd0",    # blacklight-green fluorescence · mid violet
        "selection": "#262032",                          # violet-graphite lift
    },
)
# --- IN-14 nixie tube — neon-orange cathode glow in a glass envelope (NEW warm newcomer) ---
PIPBOY_NIXIE = Theme(
    name="pipboy-nixie",
    primary="#d9631f", secondary="#b34e17", accent="#ff8a3d",
    foreground="#ff9a52", background="#150803", surface="#1c0b05", panel="#241006",
    success="#ffd0a0", warning="#ffc46a", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#150803", "block-cursor-background": "#ff8a3d",
        "footer-key-foreground": "#ffd0a0", "footer-description-foreground": "#ff9a52",
        "border": "#7a3d12", "border-strong": "#48240b",
        "surface-deep": "#0e0502", "text-bright": "#ffb07a",
        "accent-2": "#5aa8ff", "tertiary": "#d98a5c",    # cold cathode-poisoning blue · dim ember
        "selection": "#302318",                          # ember-graphite lift
    },
)
# --- Nuka-Cola vending machine — cola-red enamel with bottle-cap silver ---
PIPBOY_NUKA = Theme(
    name="pipboy-nuka",
    primary="#c23b2e", secondary="#8f2a20", accent="#ff6a55",
    foreground="#e8c9b0", background="#140506", surface="#1c080a", panel="#260a0d",
    success="#ffd9c2", warning="#ffcf5c", error="#ef3b4e", dark=True,
    variables={
        "block-cursor-foreground": "#140506", "block-cursor-background": "#ff6a55",
        "footer-key-foreground": "#ffd9c2", "footer-description-foreground": "#e8c9b0",
        "border": "#6f2018", "border-strong": "#451410",
        "surface-deep": "#0e0304", "text-bright": "#f2d9c2",
        "accent-2": "#d9d9d9", "tertiary": "#b8735c",    # literal bottle-cap silver · rusted chrome
        "selection": "#302422",                          # maroon-graphite lift
    },
)
# --- Nintendo DMG pea-green LCD glass — the SINGLE allowlisted non-black canvas (lit reflective
#     glass; the one display whose backdrop is SUPPOSED to glow). No new exceptions added. ---
PIPBOY_GAMEBOY = Theme(
    name="pipboy-gameboy",
    primary="#6f9a1f", secondary="#306230", accent="#9bbc0f",
    foreground="#8bac0f", background="#0f2410", surface="#132c14", panel="#173418",
    success="#cadd6f", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#0f2410", "block-cursor-background": "#9bbc0f",
        "footer-key-foreground": "#cadd6f", "footer-description-foreground": "#8bac0f",
        "border": "#306230", "border-strong": "#1d421d",
        "surface-deep": "#0a1c0b", "text-bright": "#a3c322",
        "accent-2": "#e0503d", "tertiary": "#7d9a2e",    # the real red power-LED · mid pea
        "selection": "#24421f",                          # pea-green lift (glass, exempt from wash)
    },
)
# --- 1950s black-and-white television — bone-grey glass with a single green power LED (the thesis:
#     the ONLY chromatic pixel anywhere is that lone LED) ---
PIPBOY_TUBE = Theme(
    name="pipboy-tube",
    primary="#8a857a", secondary="#6f6a60", accent="#f2ede2",
    foreground="#cfc9bd", background="#0d0c0a", surface="#131210", panel="#1c1a16",
    success="#e8e2d4", warning="#d9b96a", error="#d97a6a", dark=True,
    variables={
        "block-cursor-foreground": "#0d0c0a", "block-cursor-background": "#f2ede2",
        "footer-key-foreground": "#e8e2d4", "footer-description-foreground": "#cfc9bd",
        "border": "#4f4b42", "border-strong": "#33302a",
        "surface-deep": "#090807", "text-bright": "#e0dacc",
        "accent-2": "#6fe0a0", "tertiary": "#a8a296",    # THE lone green power LED · warm gray
        "selection": "#2b2924",                          # achromatic lift (selection stays colorless)
    },
)
# --- FLIR thermal imager (grey palette) — iron-grey scene with a magenta hotspot (NEW cool
#     newcomer, the only magenta; hero = hot, accent-2 = cold-pole cyan) ---
PIPBOY_THERMAL = Theme(
    name="pipboy-thermal",
    primary="#8a828a", secondary="#6a636a", accent="#ff3d97",
    foreground="#cbc2ca", background="#100a0e", surface="#18121a", panel="#201824",
    success="#ffc4e2", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#100a0e", "block-cursor-background": "#ff3d97",
        "footer-key-foreground": "#ffc4e2", "footer-description-foreground": "#cbc2ca",
        "border": "#4a444a", "border-strong": "#2e2a2e",
        "surface-deep": "#0a070b", "text-bright": "#ece6ea",
        "accent-2": "#35d0e0", "tertiary": "#9a929a",    # cold-pole cyan (thermal scale) · iron grey
        "selection": "#2a2430",                          # neutral iron lift
    },
)
EXTRA_THEMES = (PIPBOY, PIPBOY_AMBER, PIPBOY_VFD, PIPBOY_VAULT, PIPBOY_VIOLET,
                PIPBOY_NIXIE, PIPBOY_NUKA, PIPBOY_GAMEBOY, PIPBOY_TUBE, PIPBOY_THERMAL)

# Cut themes -> nearest kept sibling, so a persisted stale choice migrates to a deliberate
# near-equivalent instead of a hard reset to the default green. Applied in Studio.on_mount.
THEME_MIGRATE = {
    "pipboy-white": "pipboy-tube",        # neutral bright  -> bone-grey TV
    "pipboy-plasma": "pipboy-nixie",      # cutter orange   -> nixie neon-orange
    "pipboy-midnight": "pipboy",          # ultra-dim green -> flagship green
    "pipboy-wasteland": "pipboy-amber",   # sepia/tan       -> amber gold
    "pipboy-ice": "pipboy-vfd",           # pale cyan       -> teal VFD
    "pipboy-radium": "pipboy-gameboy",    # yellow-green    -> lime DMG (truest hue match)
}

# T20: studio-side semantic palette for INLINE Rich markup in dynamic content (queue cards, the
# status meter, plan line, stall banner). CSS covers the shell via $vars; these cover the strings.
# Rebound by _on_theme_changed; defaults = pipboy, so the default look is unchanged.
SPAL = {"accent": "#6dffab", "success": "#9dffce", "foreground": "#34d977", "secondary": "#1f9a52",
        "primary": "#2fae5f", "warning": "#ffcf5c", "error": "#ff6d6d", "text_bright": "#7dffb8",
        "border": "#1c7a42", "title": "#d7ffe8", "muted": "#4a9d6e", "soft": "#5bbf83",
        "accent2": "#e8c26a"}   # extended slots: soft <- theme "tertiary", accent2 <- "accent-2"


def tmark(key, text):
    """Wrap `text` in the CURRENT theme's color for semantic `key` (balanced Rich tag)."""
    c = SPAL.get(key) or "#34d977"
    return "[%s]%s[/%s]" % (c, text, c)
