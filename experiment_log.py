#!/usr/bin/env python
"""Experiment log — the data-capture foundation for run analytics (T10, slice 1: capture only).

Appends ONE tidy row per finished run to runs/experiments.jsonl: machine context + every dial
(independent variables) + measured outcomes (runtime, per-phase + per-shot timing, peak VRAM) +
null rating slots (the subjective DVs, filled later by the RATE UI). Decoupled from the studio UI
on purpose. load_runs() / to_dataframe() feed pandas / R / SPSS. The schema is VERSIONED so it can
grow without breaking old rows — add fields freely; bump SCHEMA on a breaking change.
"""
import os
import json
import socket

SCHEMA = 1
REPO = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(REPO, "runs", "experiments.jsonl")

# Machine context so rows stay comparable if the hardware ever changes (the "(b) on my machine" axis).
# Override the GPU label with $LTX_GPU; everything else is cheap + nvidia-smi-free.
_MACHINE = {
    "host": socket.gethostname(),
    "os": "WSL2",
    "gpu": os.environ.get("LTX_GPU", "RTX 5070 Laptop 8GB (Blackwell sm_120)"),
    "vram_gb": 8,
}

# The subjective quality axes you rate post-run (1-10). seam_quality only meaningful for chained/director.
RATING_AXES = ["prompt_adherence", "image_quality", "seam_quality",
               "motion_quality", "temporal_consistency", "overall"]


def _num(x):
    """Coerce a stringy dial to a number for analysis; pass None/non-numeric through untouched."""
    if x is None or x == "":
        return None
    try:
        f = float(x)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return x


def build_record(job):
    """Build one tidy experiment row from a finished Job. Pure (no I/O) so it's easy to test."""
    p = job.params or {}
    enhance = job.kind == "enhance"
    try:
        runtime_s = int(job.elapsed())
    except Exception:
        runtime_s = int(job.finished - job.started) if (job.finished and job.started) else None
    return {
        "schema": SCHEMA,
        "run_id": job.id,
        "ts_created": job.created,
        "ts_finished": job.finished,
        "parent_id": p.get("source_id"),            # lineage: enhance->source (re-roll/clone lineage TBD)
        "status": job.status,
        "machine": _MACHINE,
        # ---- independent variables (the dials) ----
        "kind": job.kind,
        "mode": p.get("mode") or job.kind,
        "backend": p.get("backend"),
        "steps": _num(p.get("steps")),
        "cfg": _num(p.get("cfg")),
        "seed": _num(p.get("seed")),
        "width": _num(p.get("width")),
        "height": _num(p.get("height")),
        "res": p.get("res"),
        "fps": _num(p.get("fps")),
        "seconds": _num(p.get("seconds")),
        "seg_sec": _num(p.get("seg_sec")),
        "nseg": _num(p.get("nseg")),
        "seg_frames": _num(p.get("seg_frames")),
        "total_frames": _num(p.get("total_frames")),
        "steadiness": p.get("steadiness") or None,
        "cond_strength": _num(p.get("cond_strength")),
        "has_image": bool(p.get("image")),
        "prompt": p.get("prompt"),
        "directive": p.get("directive") or None,
        "anchors": p.get("anchors") or None,
        "n_prompt": p.get("n_prompt"),
        # enhance-only IVs (None for render runs)
        "enh_interp": p.get("enh_interp") if enhance else None,
        "enh_interpeng": p.get("enh_interpeng") if enhance else None,
        "enh_upscale": p.get("enh_upscale") if enhance else None,
        "enh_upmodel": p.get("enh_upmodel") if enhance else None,
        "enh_face": p.get("enh_face") if enhance else None,
        "enh_deflicker": p.get("enh_deflicker") if enhance else None,
        # ---- dependent variables: measured (objective) ----
        "runtime_s": runtime_s,
        "phase_secs": dict(getattr(job, "phase_secs", {}) or {}),   # {load,warmup,generating,decoding,...}
        "seg_secs": list(getattr(job, "seg_secs", []) or []),       # seconds per shot
        "dir_ms": {str(k): v for k, v in (getattr(job, "dir_ms", {}) or {}).items()},  # per-seam director cost
        "peak_vram_mb": getattr(job, "peak_vram", None),
        "cpu_secs": None,                            # TODO (next increment): process CPU-time sampling
        "error": job.error or "",
        # ---- dependent variables: subjective (you rate later via the RATE UI) ----
        "rating": dict({a: None for a in RATING_AXES}, notes=""),
    }


def log_run(job):
    """Append one experiment row for a finished job. Best-effort — never raises into the runner."""
    try:
        rec = build_record(job)
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec
    except Exception:
        return None


def load_runs():
    """Read every experiment row (for analysis). Tolerant of partial/corrupt lines."""
    rows = []
    if not os.path.exists(LOG_PATH):
        return rows
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def to_dataframe():
    """Convenience for analysis: a flat pandas DataFrame (rating.* flattened). Requires pandas."""
    import pandas as pd
    rows = load_runs()
    for r in rows:
        for k, v in (r.pop("rating", None) or {}).items():
            r["rating_" + k] = v
        r["gpu"] = (r.get("machine") or {}).get("gpu")
    return pd.json_normalize(rows)
