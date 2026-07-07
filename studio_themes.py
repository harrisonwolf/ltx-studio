#!/usr/bin/env python
"""LTX Studio themes: the curated pipboy family + the SPAL inline-markup palette.

Split out of studio.py (2026-07-06 light restructuring): pure code motion, no behavior
change — imports are the only wiring. See tests/ for the regression net."""

from textual.theme import Theme

PIPBOY = Theme(
    name="pipboy",
    primary="#2fae5f",
    secondary="#1f9a52",
    accent="#6dffab",
    foreground="#34d977",
    background="#06120b",
    surface="#08160d",
    panel="#0a1c10",
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
        "border-strong": "#134a2a",     # focus/hover/cursor fill (the CSS references these four
        "surface-deep": "#050d08",      # custom vars, so every theme must define them)
        "text-bright": "#7dffb8",
        # extended palette slots (inline markup via SPAL; not referenced by CSS):
        "accent-2": "#7fd0ff",          # counterpoint accent — enhance badge, secondary signals
        "tertiary": "#5bbf83",          # supporting text — card params line etc.
    },
)

# CURATED THEME SET (quality over quantity — 2026-07-05). Every theme is built around a real
# REFERENCE OBJECT, carries the full variable shape (4 CSS custom vars + the extended accent-2/
# tertiary inline slots), and is hand-tuned, not hue-rotated. The Ctrl+K picker lists ONLY this
# family; builtin textual themes are cut (get_css_variables still guards them if forced).
PIPBOY_AMBER = Theme(
    name="pipboy-amber",
    primary="#c98a1a", secondary="#a8720f", accent="#ffcf5c",
    foreground="#e0a83e", background="#140d02", surface="#1a1004", panel="#1f1305",
    success="#ffe29a", warning="#ffb347", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#140d02", "block-cursor-background": "#ffcf5c",
        "footer-key-foreground": "#ffe29a", "footer-description-foreground": "#e0a83e",
        "border": "#7a5310", "border-strong": "#4a3208",
        "surface-deep": "#0f0a02", "text-bright": "#f2c569",
        "accent-2": "#ff8c42", "tertiary": "#b8923a",    # ember orange · aged brass
    },
)
# (pipboy-blue and pipboy-red were CUT in the 2026-07-05 curation pass: blue was superseded by
# vfd + ice, red read as harsh novelty. Recover them from git history if ever wanted.)
PIPBOY_WHITE = Theme(
    name="pipboy-white",
    primary="#9fb8ac", secondary="#7d968a", accent="#ffffff",
    foreground="#d6e8dc", background="#0b0d0c", surface="#101413", panel="#161a19",
    success="#eafff4", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#0b0d0c", "block-cursor-background": "#ffffff",
        "footer-key-foreground": "#eafff4", "footer-description-foreground": "#d6e8dc",
        "border": "#55695f", "border-strong": "#333f39",
        "surface-deep": "#070908", "text-bright": "#e8f8ee",
        "accent-2": "#9fd4ff", "tertiary": "#a8c2b5",    # cool CRT-signal blue · silver sage
    },
)
PIPBOY_PLASMA = Theme(
    name="pipboy-plasma",
    primary="#e06a1f", secondary="#b85417", accent="#ffb37a",
    foreground="#ff9a5c", background="#170803", surface="#1e0b04", panel="#251006",
    success="#ffd9b8", warning="#ffe29a", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#170803", "block-cursor-background": "#ffb37a",
        "footer-key-foreground": "#ffd9b8", "footer-description-foreground": "#ff9a5c",
        "border": "#7a3d10", "border-strong": "#46220a",
        "surface-deep": "#100502", "text-bright": "#ffb98a",
        "accent-2": "#ffd23f", "tertiary": "#d97b3c",    # spark yellow · burnt copper
    },
)
PIPBOY_VFD = Theme(
    name="pipboy-vfd",
    primary="#17ae94", secondary="#0f9a82", accent="#6dffe8",
    foreground="#3fe0c8", background="#03110f", surface="#051814", panel="#071e19",
    success="#b8fff2", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#03110f", "block-cursor-background": "#6dffe8",
        "footer-key-foreground": "#b8fff2", "footer-description-foreground": "#3fe0c8",
        "border": "#0f7a66", "border-strong": "#0a4a3e",
        "surface-deep": "#020b0a", "text-bright": "#7df0dc",
        "accent-2": "#7dc4ff", "tertiary": "#2fae94",    # cold segment blue · deep teal
    },
)
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
        "accent-2": "#e8f2a0", "tertiary": "#7d9a2e",    # LCD glare · mid pea (true DMG shades)
    },
)
PIPBOY_MIDNIGHT = Theme(
    name="pipboy-midnight",
    primary="#17693a", secondary="#115530", accent="#2fae5f",
    foreground="#1f8a4c", background="#010503", surface="#020a06", panel="#041009",
    success="#4cc07a", warning="#a8873a", error="#a84848", dark=True,
    variables={
        "block-cursor-foreground": "#010503", "block-cursor-background": "#2fae5f",
        "footer-key-foreground": "#4cc07a", "footer-description-foreground": "#1f8a4c",
        "border": "#0d3d22", "border-strong": "#082816",
        "surface-deep": "#010302", "text-bright": "#27a35c",
        "accent-2": "#2a6a7f", "tertiary": "#145c36",    # faint cyan ghost · deep moss (stays dim)
    },
)
# Round 2 — themes with a REFERENCE OBJECT, not a hue swap:
# vault = Vault-Tec suit (vault yellow accents on utility navy); wasteland = sun-bleached field
# manual (dust sepia, burnt-orange warnings); ice = arctic glass HUD (near-white cyan, pale + high
# contrast, unlike blue/vfd); violet = blacklight UV lab phosphor; radium = 1940s radium watch
# dial (warm yellow-green lume on almost-black).
PIPBOY_VAULT = Theme(
    name="pipboy-vault",
    # TERMINAL-BLACK RULE (2026-07-05, user): the CANVAS stays near-black like a real terminal —
    # identity lives in text/borders/accents, never in background fills. v1 of this theme washed
    # the whole screen navy (bg #071120 / panel #0d1f3c); the wash-guard test now enforces the rule.
    primary="#2b6cb0", secondary="#1f4f8a", accent="#ffd24a",
    foreground="#8fb3dd", background="#05080f", surface="#070b14", panel="#0a101c",
    success="#a8d1ff", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#05080f", "block-cursor-background": "#ffd24a",
        "footer-key-foreground": "#ffd24a", "footer-description-foreground": "#8fb3dd",
        "border": "#1c477a", "border-strong": "#122a48",
        "surface-deep": "#030509", "text-bright": "#a9cdf6",
        "accent-2": "#ff8c42", "tertiary": "#5a86b8",    # hazard orange · suit-steel blue
    },
)
PIPBOY_WASTELAND = Theme(
    name="pipboy-wasteland",
    primary="#a8863c", secondary="#8a6f2f", accent="#ffe8b0",
    foreground="#d9c084", background="#161004", surface="#1d1606", panel="#241b08",
    success="#f2ddad", warning="#ff9a3c", error="#e05252", dark=True,
    variables={
        "block-cursor-foreground": "#161004", "block-cursor-background": "#ffe8b0",
        "footer-key-foreground": "#f2ddad", "footer-description-foreground": "#d9c084",
        "border": "#6f5a26", "border-strong": "#453816",
        "surface-deep": "#100b03", "text-bright": "#ecd9a0",
        "accent-2": "#8fb85a", "tertiary": "#b89a5f",    # cactus green · worn leather
    },
)
PIPBOY_ICE = Theme(
    name="pipboy-ice",
    primary="#4aa3c0", secondary="#33809a", accent="#e8faff",
    foreground="#a8d8e8", background="#060e14", surface="#0a151d", panel="#0e1c26",
    success="#c9f2ff", warning="#ffd280", error="#ff7d7d", dark=True,
    variables={
        "block-cursor-foreground": "#060e14", "block-cursor-background": "#e8faff",
        "footer-key-foreground": "#c9f2ff", "footer-description-foreground": "#a8d8e8",
        "border": "#2a607a", "border-strong": "#1a3f52",
        "surface-deep": "#04090e", "text-bright": "#d1f0fa",
        "accent-2": "#ffd280", "tertiary": "#6fb8d1",    # sun-glint amber · glacial mid-blue
    },
)
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
        "accent-2": "#7dffb8", "tertiary": "#9a6fd0",    # blacklight-green glow · mid violet
    },
)
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
    },
)
# Round 3 additions: nuka = Nuka-Cola machine (cola red + cream label + bottle-cap silver on
# deep maroon); tube = a warm 1950s B&W television — near-grayscale with EXACTLY ONE color note,
# the little green power LED (accent-2). Restraint as the design statement.
PIPBOY_NUKA = Theme(
    name="pipboy-nuka",
    primary="#c23b2e", secondary="#8f2a20", accent="#ff6a55",
    foreground="#e8c9b0", background="#140506", surface="#1c080a", panel="#240a0c",
    success="#ffd9c2", warning="#ffcf5c", error="#ff6d6d", dark=True,
    variables={
        "block-cursor-foreground": "#140506", "block-cursor-background": "#ff6a55",
        "footer-key-foreground": "#ffd9c2", "footer-description-foreground": "#e8c9b0",
        "border": "#6f2018", "border-strong": "#451410",
        "surface-deep": "#0e0304", "text-bright": "#f2d9c2",
        "accent-2": "#d9d9d9", "tertiary": "#b8735c",    # bottle-cap silver · rusted chrome
    },
)
PIPBOY_TUBE = Theme(
    name="pipboy-tube",
    primary="#8a857a", secondary="#6f6a60", accent="#f2ede2",
    foreground="#cfc9bd", background="#0d0c0a", surface="#12110e", panel="#171512",
    success="#e8e2d4", warning="#d9b96a", error="#d97a6a", dark=True,
    variables={
        "block-cursor-foreground": "#0d0c0a", "block-cursor-background": "#f2ede2",
        "footer-key-foreground": "#e8e2d4", "footer-description-foreground": "#cfc9bd",
        "border": "#4f4b42", "border-strong": "#33302a",
        "surface-deep": "#090807", "text-bright": "#e0dacc",
        "accent-2": "#7fd0a8", "tertiary": "#a8a296",    # THE power LED · warm gray
    },
)
EXTRA_THEMES = (PIPBOY, PIPBOY_AMBER, PIPBOY_WHITE, PIPBOY_PLASMA, PIPBOY_VFD,
                PIPBOY_GAMEBOY, PIPBOY_MIDNIGHT, PIPBOY_VAULT, PIPBOY_WASTELAND,
                PIPBOY_ICE, PIPBOY_VIOLET, PIPBOY_RADIUM, PIPBOY_NUKA, PIPBOY_TUBE)

# T20: studio-side semantic palette for INLINE Rich markup in dynamic content (queue cards, the
# status meter, plan line, stall banner). CSS covers the shell via $vars; these cover the strings.
# Rebound by _on_theme_changed; defaults = pipboy, so the default look is unchanged.
SPAL = {"accent": "#6dffab", "success": "#9dffce", "foreground": "#34d977", "secondary": "#1f9a52",
        "primary": "#2fae5f", "warning": "#ffcf5c", "error": "#ff6d6d", "text_bright": "#7dffb8",
        "border": "#1c7a42", "title": "#d7ffe8", "muted": "#4a9d6e", "soft": "#5bbf83",
        "accent2": "#7fd0ff"}   # extended slots: soft <- theme "tertiary", accent2 <- "accent-2"


def tmark(key, text):
    """Wrap `text` in the CURRENT theme's color for semantic `key` (balanced Rich tag)."""
    c = SPAL.get(key) or "#34d977"
    return "[%s]%s[/%s]" % (c, text, c)


