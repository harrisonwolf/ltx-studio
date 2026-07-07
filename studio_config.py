#!/usr/bin/env python
"""LTX Studio persisted UI prefs (runs/studio_config.json): tiny load/save helpers.

Split out of studio.py (2026-07-06 light restructuring): pure code motion, no behavior
change — imports are the only wiring. See tests/ for the regression net."""

import os
import json

from studio_core import REPO

STUDIO_CONFIG_PATH = os.path.join(REPO, "runs", "studio_config.json")   # T14: small persisted UI prefs


def load_studio_config():
    """Read runs/studio_config.json; missing/corrupt -> {} so every caller just applies its own defaults."""
    try:
        with open(STUDIO_CONFIG_PATH) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_studio_config(cfg):
    """Atomic write of runs/studio_config.json (mirrors Job.save()'s tmp+replace pattern)."""
    try:
        os.makedirs(os.path.dirname(STUDIO_CONFIG_PATH), exist_ok=True)
        tmp = STUDIO_CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f)
        os.replace(tmp, STUDIO_CONFIG_PATH)
    except Exception:
        pass


