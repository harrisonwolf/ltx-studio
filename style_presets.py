"""STYLE PRESETS — named bundles of ANCHOR words for the NEW RUN form.

Picking a preset in the STYLE dropdown APPENDS its words to the ANCHORS field (deduped,
comma-separated), so you can stack e.g. Cinematic + Golden Hour. The curated BUILTIN set below
is merged with any user presets from runs/style_presets.json (or the "style_presets" key of
runs/studio_config.json) — so you can add/override your own without touching code.

PURE stdlib (no torch / textual / studio imports).
"""
import os
import json

# Curated built-in presets: distinct vibes, tuned for the LTX/Wan look. Edit freely.
BUILTIN = {
    "Cinematic":        ["cinematic", "35mm", "shallow depth of field", "film grain", "dramatic lighting"],
    "Documentary":      ["handheld", "natural light", "candid", "photorealistic", "24mm"],
    "Dreamy":           ["soft focus", "pastel", "ethereal", "hazy", "glowing", "shifting"],
    "Noir":             ["black and white", "high contrast", "chiaroscuro", "deep shadows", "moody"],
    "Golden Hour":      ["golden hour", "warm sunlight", "backlit", "lens flare", "soft glow"],
    "Neon / Cyberpunk": ["neon", "cyberpunk", "rain-slick streets", "teal and orange", "night"],
    "Anime":            ["anime", "cel-shaded", "vibrant", "clean lines", "2D"],
    "Oil Painting":     ["oil painting", "painterly", "textured brushstrokes", "rich color"],
    "Vintage Film":     ["vintage", "faded color", "super-8", "light leaks", "grain", "nostalgic"],
    "Epic Landscape":   ["epic wide shot", "sweeping vista", "volumetric light", "atmospheric", "grand scale"],
    "Macro":            ["macro", "extreme close-up", "crisp detail", "bokeh", "shallow depth"],
    "Minimal":          ["minimalist", "clean", "soft studio light", "muted palette"],
}


def _user_presets(repo):
    """Read user-added presets defensively from two optional sources (merged; user wins):
       runs/style_presets.json                    == {"Name": ["word", ...] | "word, word"}
       runs/studio_config.json ["style_presets"]  == same shape
    Any missing/corrupt file is simply skipped."""
    out = {}
    for path, key in ((os.path.join(repo, "runs", "style_presets.json"), None),
                      (os.path.join(repo, "runs", "studio_config.json"), "style_presets")):
        try:
            with open(path) as fh:
                data = json.load(fh)
            if key:
                data = data.get(key, {})
            if isinstance(data, dict):
                for name, words in data.items():
                    if isinstance(words, str):
                        words = [w.strip() for w in words.split(",") if w.strip()]
                    if isinstance(words, list) and words:
                        out[str(name)] = [str(w).strip() for w in words if str(w).strip()]
        except Exception:
            continue
    return out


def load_presets(repo):
    """BUILTIN merged with user presets (a user preset with the same name overrides the built-in)."""
    merged = dict(BUILTIN)
    try:
        merged.update(_user_presets(repo))
    except Exception:
        pass
    return merged


def apply_preset(existing_anchors, preset_words):
    """APPEND preset_words to the current ANCHORS text, deduped (case-insensitive), comma-joined.
    Existing words keep their order; only genuinely-new words are appended. Never raises."""
    try:
        existing = [w.strip() for w in (existing_anchors or "").split(",") if w.strip()]
        seen = {w.lower() for w in existing}
        for w in (preset_words or []):
            ww = str(w).strip()
            if ww and ww.lower() not in seen:
                existing.append(ww)
                seen.add(ww.lower())
        return ", ".join(existing)
    except Exception:
        return existing_anchors or ""
