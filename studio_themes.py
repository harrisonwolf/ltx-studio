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
Curated 13 (11 emissive/reference themes + concrete named: usa, tron).
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
# --- 1940s radium watch-dial lume — phosphorescent yellow-green glow on a near-black dial with a
#     warm brass casing. Distinct from gameboy: this is GLOW-ON-BLACK (a real near-black canvas, wash
#     compliant), not gameboy's flat lit LCD glass. Restored by request (user's favorite). ---
PIPBOY_RADIUM = Theme(
    name="pipboy-radium",
    primary="#9ab83c", secondary="#7d9a2e", accent="#eeff9a",
    foreground="#c9d96f", background="#0c0d04", surface="#121306", panel="#171908",
    success="#e3f2b8", warning="#ffb347", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#0c0d04", "block-cursor-background": "#eeff9a",
        "footer-key-foreground": "#e3f2b8", "footer-description-foreground": "#c9d96f",
        "border": "#5f6f22", "border-strong": "#3d4a14",
        "surface-deep": "#080902", "text-bright": "#dcea8a",
        "accent-2": "#ffb347", "tertiary": "#a3b855",    # brass casing · olive lume
        "selection": "#2a2c15",                          # olive-graphite lift (glow, wash-compliant)
    },
)
# --- CONCRETE / NAMED themes (2026-07-07, user asked for recognizable subjects, not abstractions):
#     each is a real, nameable thing rendered in the terminal-black language (identity in the
#     accents; canvas stays near-black). ---
# Old Glory at night — a flag lit in the dark: navy field (structure), red stripe (the hero/focus),
# white stars (bright text + counterpoint).
PIPBOY_USA = Theme(
    name="pipboy-usa",
    primary="#3a63c8", secondary="#2b4a9a", accent="#ef3e46",
    foreground="#c3ccea", background="#04050c", surface="#080a14", panel="#0c0f1e",
    success="#eaf0ff", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#04050c", "block-cursor-background": "#ef3e46",
        "footer-key-foreground": "#eaf0ff", "footer-description-foreground": "#c3ccea",
        "border": "#2b4a9a", "border-strong": "#1a2f66",
        "surface-deep": "#030409", "text-bright": "#eef1ff",
        "accent-2": "#eef1ff", "tertiary": "#7f8fc8",    # white stars · steel-blue field
        "selection": "#131a33",                          # navy-graphite lift
    },
)
# TRON — the Grid: electric cyan circuitry (structure + focus) with neon-orange programs (Clu) as
# the one counterpoint, on black glass. The cyan/orange pairing is what makes it read as TRON, not VFD.
PIPBOY_TRON = Theme(
    name="pipboy-tron",
    primary="#1fb6d4", secondary="#158a9a", accent="#7df0ff",
    foreground="#8fd8e6", background="#03080c", surface="#061318", panel="#08191f",
    success="#b8fff2", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#03080c", "block-cursor-background": "#7df0ff",
        "footer-key-foreground": "#b8fff2", "footer-description-foreground": "#8fd8e6",
        "border": "#157a8a", "border-strong": "#0d4a56",
        "surface-deep": "#020a0d", "text-bright": "#a8f0ff",
        "accent-2": "#ff9a3d", "tertiary": "#4fb0c0",    # neon-orange programs (Clu) · grid teal
        "selection": "#102a33",                          # cyan-graphite lift
    },
)
# The Matrix — falling code: bright spring-green rain over pure black, brighter/higher-contrast
# than the flagship phosphor so it reads as a data stream, not a CRT.
PIPBOY_MATRIX = Theme(
    name="pipboy-matrix",
    primary="#1f8a3a", secondary="#157a2e", accent="#4dff6a",
    foreground="#6ee87f", background="#030803", surface="#061206", panel="#081a08",
    success="#b8ffc4", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#030803", "block-cursor-background": "#4dff6a",
        "footer-key-foreground": "#b8ffc4", "footer-description-foreground": "#6ee87f",
        "border": "#176b2a", "border-strong": "#0d4419",
        "surface-deep": "#020802", "text-bright": "#b8ffc4",
        "accent-2": "#a0ff5a", "tertiary": "#4a9a5a",    # lime code glint · dim trailing green
        "selection": "#103018",                          # rain-graphite lift
    },
)
# Skynet HK combat HUD — chrome-steel structure with a combat-red targeting reticle (the plain,
# colors-only sibling of ultra-skynet).
PIPBOY_SKYNET = Theme(
    name="pipboy-skynet",
    primary="#7a828a", secondary="#5a636a", accent="#ff2a2a",
    foreground="#b8c0c8", background="#0a0708", surface="#140f10", panel="#1c1416",
    success="#86d98f", warning="#ffb03a", error="#ff3b3b", dark=True,
    variables={
        "block-cursor-foreground": "#0a0708", "block-cursor-background": "#ff2a2a",
        "footer-key-foreground": "#ffb03a", "footer-description-foreground": "#b8c0c8",
        "border": "#4a4f55", "border-strong": "#2e3237",
        "surface-deep": "#070506", "text-bright": "#dfe6ec",
        "accent-2": "#ffb03a", "tertiary": "#8a929a",    # amber scan · dim steel
        "selection": "#241a1c",                          # red-graphite lift
    },
)
# Miami / synthwave sunset (static color theme; ultra-synthwave is its animated cousin) — hot
# magenta + cyan on a deep indigo-black, sunset-orange counterpoint.
PIPBOY_MIAMI = Theme(
    name="pipboy-miami",
    primary="#ff3ca6", secondary="#c42e80", accent="#4de0ff",
    foreground="#f0a8d0", background="#0a0410", surface="#12081c", panel="#180b26",
    success="#6dffd8", warning="#ffcf5c", error="#ff5c8a", dark=True,
    variables={
        "block-cursor-foreground": "#0a0410", "block-cursor-background": "#4de0ff",
        "footer-key-foreground": "#6dffd8", "footer-description-foreground": "#f0a8d0",
        "border": "#6e2a78", "border-strong": "#431a4a",
        "surface-deep": "#070310", "text-bright": "#ffb8e0",
        "accent-2": "#ff9a3d", "tertiary": "#b86ba8",    # sunset orange · mid orchid
        "selection": "#1e1230",                          # indigo lift
    },
)
# Radiation hazard — hi-vis yellow + caution amber on black, trefoil red for stop/error.
PIPBOY_HAZMAT = Theme(
    name="pipboy-hazmat",
    primary="#c8a01a", secondary="#9a7a12", accent="#f2ff1f",
    foreground="#d9c96a", background="#0a0902", surface="#131005", panel="#1a1607",
    success="#d4ff6a", warning="#ff9a2a", error="#ff4d3a", dark=True,
    variables={
        "block-cursor-foreground": "#0a0902", "block-cursor-background": "#f2ff1f",
        "footer-key-foreground": "#d4ff6a", "footer-description-foreground": "#d9c96a",
        "border": "#6f5a14", "border-strong": "#45380c",
        "surface-deep": "#080702", "text-bright": "#f2e88a",
        "accent-2": "#ff7a1f", "tertiary": "#b8a04a",    # hazard-stripe orange · dust amber
        "selection": "#2a2410",                          # amber-graphite lift
    },
)
# Cyberpunk glitch — electric cyan structure, hi-vis yellow hero, glitch-red error/counterpoint.
PIPBOY_CYBERPUNK = Theme(
    name="pipboy-cyberpunk",
    primary="#22d3e0", secondary="#1a9aa4", accent="#f9f002",
    foreground="#7ae0e8", background="#05080a", surface="#081320", panel="#0b1a2a",
    success="#6dffb8", warning="#ffb347", error="#ff003c", dark=True,
    variables={
        "block-cursor-foreground": "#05080a", "block-cursor-background": "#f9f002",
        "footer-key-foreground": "#f9f002", "footer-description-foreground": "#7ae0e8",
        "border": "#157a84", "border-strong": "#0d4a52",
        "surface-deep": "#04080c", "text-bright": "#c4faff",
        "accent-2": "#ff003c", "tertiary": "#4faab0",    # glitch red · dim cyan
        "selection": "#10262e",                          # cyan-graphite lift
    },
)
# Christmas — evergreen structure, cranberry-red hero, gold ornaments.
PIPBOY_XMAS = Theme(
    name="pipboy-xmas",
    primary="#2f8f4f", secondary="#23723d", accent="#ff4d4d",
    foreground="#a8d9b4", background="#050a06", surface="#0a140c", panel="#0e1a10",
    success="#b8ffc9", warning="#ffd24a", error="#ff5c5c", dark=True,
    variables={
        "block-cursor-foreground": "#050a06", "block-cursor-background": "#ff4d4d",
        "footer-key-foreground": "#ffd24a", "footer-description-foreground": "#a8d9b4",
        "border": "#1f6f3a", "border-strong": "#134523",
        "surface-deep": "#030803", "text-bright": "#d9ffe2",
        "accent-2": "#ffd24a", "tertiary": "#6aa87d",    # gold ornaments · frost green
        "selection": "#12241a",                          # evergreen lift
    },
)
# Film noir — a rain-slicked 1940s detective picture: cool silver light in deep shadow, with ONE
# blood-red neon note (the accent) and a lone streetlamp amber. Cool grayscale, high contrast.
PIPBOY_NOIR = Theme(
    name="pipboy-noir",
    primary="#8a9098", secondary="#565c64", accent="#e5384a",
    foreground="#c4cad2", background="#060708", surface="#0c0e12", panel="#13161c",
    success="#a8c8d8", warning="#d9b96a", error="#ff4356", dark=True,
    variables={
        "block-cursor-foreground": "#060708", "block-cursor-background": "#e5384a",
        "footer-key-foreground": "#eef2f6", "footer-description-foreground": "#c4cad2",
        "border": "#383e46", "border-strong": "#22262c",
        "surface-deep": "#040506", "text-bright": "#eef2f6",
        "accent-2": "#d9b96a", "tertiary": "#767c84",    # lone streetlamp amber · mid steel
        "selection": "#191d23",                          # cool graphite lift
    },
)
# Cherry blossom at night — soft rose petals and cream against near-black branches, with a young
# moss-leaf green as the one cool counterpoint. Gentle and pretty (the calm one).
PIPBOY_SAKURA = Theme(
    name="pipboy-sakura",
    primary="#c86f8a", secondary="#96506a", accent="#ffb3d0",
    foreground="#e6bccb", background="#0a0608", surface="#150a10", panel="#1d0e16",
    success="#a6cf86", warning="#ffcf5c", error="#ff6d8a", dark=True,
    variables={
        "block-cursor-foreground": "#0a0608", "block-cursor-background": "#ffb3d0",
        "footer-key-foreground": "#fff0f5", "footer-description-foreground": "#e6bccb",
        "border": "#6a3a4e", "border-strong": "#3f222e",
        "surface-deep": "#080406", "text-bright": "#fff0f5",
        "accent-2": "#9fcf7a", "tertiary": "#b87f96",    # young leaf green · mid rose
        "selection": "#241420",                          # rose-graphite lift
    },
)
EXTRA_THEMES = (PIPBOY, PIPBOY_AMBER, PIPBOY_VFD, PIPBOY_VAULT, PIPBOY_VIOLET,
                PIPBOY_NIXIE, PIPBOY_NUKA, PIPBOY_GAMEBOY, PIPBOY_TUBE, PIPBOY_THERMAL,
                PIPBOY_RADIUM, PIPBOY_USA, PIPBOY_TRON,
                PIPBOY_MATRIX, PIPBOY_SKYNET, PIPBOY_MIAMI, PIPBOY_HAZMAT,
                PIPBOY_CYBERPUNK, PIPBOY_XMAS,
                PIPBOY_NOIR, PIPBOY_SAKURA)

# ---- ULTRA-THEMES tier (2026-07-07): opt-in, DECORATED/ANIMATED themes shown in a separate picker
#      section. The Theme objects below carry only colors (near-black canvas, wash-compliant, like any
#      theme); the animated 8-bit pixel-art flair lives in ultra_art.py, keyed by theme name. Kept in a
#      SEPARATE tuple so ULTRA_NAMES is the single source of truth for "is this the ultra tier". ----
# Year of the Dragon — gold on black-crimson lacquer (ultra_art draws a glimmering dragon head).
ULTRA_DRAGON = Theme(
    name="ultra-dragon",
    primary="#b8860b", secondary="#8a6410", accent="#ffd24a",
    foreground="#e8c96a", background="#0c0404", surface="#180808", panel="#200a0a",
    success="#ffe9a0", warning="#ff9a3d", error="#ff5c5c", dark=True,
    variables={
        "block-cursor-foreground": "#0c0404", "block-cursor-background": "#ffd24a",
        "footer-key-foreground": "#ffe9a0", "footer-description-foreground": "#e8c96a",
        "border": "#7a2418", "border-strong": "#4a1810",
        "surface-deep": "#080202", "text-bright": "#fff3c4",
        "accent-2": "#c81e2a", "tertiary": "#b8935a",    # imperial crimson · aged gold
        "selection": "#2a1810",                          # crimson-gold lift
    },
)
# Skynet / T-800 — chrome steel + glowing red (ultra_art draws a skull with pulsing red eyes).
ULTRA_SKYNET = Theme(
    name="ultra-skynet",
    primary="#8a929a", secondary="#6a727a", accent="#ff2a2a",
    foreground="#c4ccd4", background="#060708", surface="#0e1114", panel="#14181c",
    success="#86d98f", warning="#ffb03a", error="#ff3b3b", dark=True,
    variables={
        "block-cursor-foreground": "#060708", "block-cursor-background": "#ff2a2a",
        "footer-key-foreground": "#ff5a3a", "footer-description-foreground": "#c4ccd4",
        "border": "#3e444a", "border-strong": "#262a2e",
        "surface-deep": "#040506", "text-bright": "#e8eef2",
        "accent-2": "#ff5a3a", "tertiary": "#9aa2aa",    # hot ember · dim steel
        "selection": "#201618",                          # steel-red lift
    },
)
# Synthwave (the showpiece) — deep indigo-black with a magenta/cyan/orange neon sunset (ultra_art
# draws an animated sun + scrolling perspective grid).
ULTRA_SYNTHWAVE = Theme(
    name="ultra-synthwave",
    primary="#ff2d95", secondary="#c42678", accent="#2de0ff",
    foreground="#f0a0d8", background="#08040f", surface="#100820", panel="#150b28",
    success="#5cffe0", warning="#ffcf5c", error="#ff4d8a", dark=True,
    variables={
        "block-cursor-foreground": "#08040f", "block-cursor-background": "#2de0ff",
        "footer-key-foreground": "#5cffe0", "footer-description-foreground": "#f0a0d8",
        "border": "#7a2a7e", "border-strong": "#481a4e",
        "surface-deep": "#060310", "text-bright": "#ffb0e4",
        "accent-2": "#ff8a3d", "tertiary": "#b060b8",    # sunset orange · mid orchid
        "selection": "#1c1236",                          # indigo lift
    },
)
# The Matrix — digital rain: bright spring-green code falling on pure black (ultra_art draws the
# classic falling-code columns — a bright/white leading glyph with a fading green trail).
ULTRA_MATRIX = Theme(
    name="ultra-matrix",
    primary="#2e9a3f", secondary="#1c6a2c", accent="#5bff77",
    foreground="#6ee87f", background="#020402", surface="#061006", panel="#081a08",
    success="#b8ffc4", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#020402", "block-cursor-background": "#5bff77",
        "footer-key-foreground": "#b8ffc4", "footer-description-foreground": "#6ee87f",
        "border": "#156b26", "border-strong": "#0c4418",
        "surface-deep": "#010301", "text-bright": "#b8ffc4",
        "accent-2": "#d8ffe0", "tertiary": "#3a9a4a",    # white-green leading glyph · dim trailing green
        "selection": "#0e2c14",                          # rain-graphite lift
    },
)
# Space Invaders cabinet — phosphor-green invaders marching on black, a magenta UFO, INSERT COIN.
ULTRA_ARCADE = Theme(
    name="ultra-arcade",
    primary="#33aa44", secondary="#237a30", accent="#ff2d95",
    foreground="#7ae88a", background="#030a04", surface="#08160a", panel="#0c1e0f",
    success="#b8ffc4", warning="#ffcf5c", error="#ff5c5c", dark=True,
    variables={
        "block-cursor-foreground": "#030a04", "block-cursor-background": "#ff2d95",
        "footer-key-foreground": "#b8ffc4", "footer-description-foreground": "#7ae88a",
        "border": "#1f7a2e", "border-strong": "#124a1c",
        "surface-deep": "#020702", "text-bright": "#d8ffe0",
        "accent-2": "#46e8ff", "tertiary": "#4a9a5a",    # magenta UFO hero · electric-cyan · dim invader green
        "selection": "#0e2c14",
    },
)
# SMPTE test card — broadcast steel-blue chrome, the white/red/cyan of the bars, "PLEASE STAND BY".
ULTRA_TV = Theme(
    name="ultra-tv",
    primary="#6a7a8a", secondary="#4a5560", accent="#e6e6e6",
    foreground="#b8c0c8", background="#05070a", surface="#0c1015", panel="#12171e",
    success="#a8d8c0", warning="#d8c81a", error="#d02020", dark=True,
    variables={
        "block-cursor-foreground": "#05070a", "block-cursor-background": "#e6e6e6",
        "footer-key-foreground": "#f0f4f8", "footer-description-foreground": "#b8c0c8",
        "border": "#3a4652", "border-strong": "#232a32",
        "surface-deep": "#030507", "text-bright": "#f0f4f8",
        "accent-2": "#1ac8c8", "tertiary": "#7a828a",    # test-card cyan · broadcast grey
        "selection": "#131820",
    },
)
# Walkman mixtape — chrome shell + tape-gold, green/red VU, spinning reels. SIDE A.
ULTRA_CASSETTE = Theme(
    name="ultra-cassette",
    primary="#b09a5a", secondary="#7a684a", accent="#ffe08a",
    foreground="#d8c89a", background="#0a0803", surface="#14100a", panel="#1c160c",
    success="#5ade7a", warning="#ffcf5c", error="#ff7a4a", dark=True,
    variables={
        "block-cursor-foreground": "#0a0803", "block-cursor-background": "#ffe08a",
        "footer-key-foreground": "#fff0c0", "footer-description-foreground": "#d8c89a",
        "border": "#6a5628", "border-strong": "#40340f",
        "surface-deep": "#080602", "text-bright": "#fff0c0",
        "accent-2": "#ff7a4a", "tertiary": "#a89468",    # VU-red tape warmth · aged chrome
        "selection": "#241c0e",
    },
)
# Submarine sonar — abyssal cyan-green scope, a sweeping line, a pinging contact. DEPTH 340.
ULTRA_SONAR = Theme(
    name="ultra-sonar",
    primary="#1aa0b8", secondary="#12707f", accent="#5cffe0",
    foreground="#7ad0d8", background="#020a0c", surface="#06171a", panel="#0a2226",
    success="#7cffc8", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#020a0c", "block-cursor-background": "#5cffe0",
        "footer-key-foreground": "#c4faff", "footer-description-foreground": "#7ad0d8",
        "border": "#147a8a", "border-strong": "#0c4a54",
        "surface-deep": "#010708", "text-bright": "#c4faff",
        "accent-2": "#7cffb0", "tertiary": "#4a9aa0",    # contact-blip green · deep teal
        "selection": "#0c2a30",
    },
)
# Oscilloscope — phosphor green-cyan trace on a graticule; analog-synth bench vibe. CH1.
ULTRA_SCOPE = Theme(
    name="ultra-scope",
    primary="#1fb87a", secondary="#147a52", accent="#5cffd0",
    foreground="#7ad8b8", background="#020c08", surface="#08180f", panel="#0a2216",
    success="#7cffc8", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#020c08", "block-cursor-background": "#5cffd0",
        "footer-key-foreground": "#c4ffe8", "footer-description-foreground": "#7ad8b8",
        "border": "#148a5a", "border-strong": "#0c4a34",
        "surface-deep": "#010805", "text-bright": "#c4ffe8",
        "accent-2": "#c4faff", "tertiary": "#4a9a78",    # bright cyan flash · dim scope green
        "selection": "#0c2a1c",
    },
)
ULTRA_THEMES = (ULTRA_DRAGON, ULTRA_SKYNET, ULTRA_SYNTHWAVE, ULTRA_MATRIX, ULTRA_ARCADE, ULTRA_TV,
                ULTRA_CASSETTE, ULTRA_SONAR, ULTRA_SCOPE)
# Single source of truth for "is this the animated tier" — used by the picker (sectioning) and by
# studio._on_theme_changed (show/hide the decoration). ultra_art.THEMES maps the same names to art.
ULTRA_NAMES = frozenset(t.name for t in ULTRA_THEMES)

# Cut themes -> nearest kept sibling, so a persisted stale choice migrates to a deliberate
# near-equivalent instead of a hard reset to the default green. Applied in Studio.on_mount.
THEME_MIGRATE = {
    "pipboy-white": "pipboy-tube",        # neutral bright  -> bone-grey TV
    "pipboy-plasma": "pipboy-nixie",      # cutter orange   -> nixie neon-orange
    "pipboy-midnight": "pipboy",          # ultra-dim green -> flagship green
    "pipboy-wasteland": "pipboy-amber",   # sepia/tan       -> amber gold
    "pipboy-ice": "pipboy-vfd",           # pale cyan       -> teal VFD
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
