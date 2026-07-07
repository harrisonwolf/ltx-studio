# readout.py — T22 global readout meters: estimators, auto-refit, gauge renderers.
# Pure stdlib. No torch, no textual, no studio imports. All colors via Rich markup tags.
#
# Six live gauges for the NEW RUN right column: est. peak VRAM, max clip/shot, RAM/swap
# pressure, generation time, quality outlook, drift/seam risk. Display-only — nothing here changes
# run behaviour. Hand formulas ship as the v0 fallback; maybe_refit() re-derives the
# VRAM (k) and time (COEF/WARM/DECODE) constants from runs/experiments.jsonl as it grows
# and caches them, self-gating on file mtime so it is cheap to call on every keystroke.
#
# Style mirrors field_visuals.py (green-phosphor palette + block-art bars) but imports
# NOTHING from it — the tiny helpers below are re-implemented locally on purpose so the
# two modules never step on each other.

import os
import json
import statistics
import time

FIT_CACHE = "runs/readout_fit.json"       # relative to the FramePack repo root
EXPERIMENTS = "runs/experiments.jsonl"

# ---- palette (green-phosphor, matches studio.py + field_visuals.py Rich markup tags) ----
# Defaults = pipboy. Theme switching rebinds these via set_palette(); render functions read
# them at call time, so a rebind takes effect on the next render. (The inline "dim" markup
# tag elsewhere in this file is Rich's built-in dim style, not a palette color.)
ACCENT = "#6dffab"   # bright accent / headers
CLEAN = "#9dffce"    # the good / clean end
MID = "#34d977"      # neutral mid green
DIM = "#1f9a52"      # muted / low
WARN = "#ffcf5c"     # caution
BAD = "#ff6d6d"      # danger / red zone

# constant -> semantic palette key (the dict studio.py hands to set_palette on theme change)
_PALETTE_KEYS = {
    "ACCENT": "accent",
    "CLEAN": "success",
    "MID": "foreground",
    "DIM": "secondary",
    "WARN": "warning",
    "BAD": "error",
}


def set_palette(colors):
    """Rebind the module color constants from a semantic palette dict. Missing keys keep the
    current value. Called by studio.py on theme change; defaults = pipboy (unchanged)."""
    g = globals()
    for const, key in _PALETTE_KEYS.items():
        val = (colors or {}).get(key)
        if val:
            g[const] = val

W_BAR = 16           # bar cell count — keeps every line well under the ~48-col panel wrap

# ---- hand-constant anchors (v0 fallback; the refit overrides VRAM k + time COEF/WARM/DECODE) ----
# VRAM model: est_gb = base_gb + k * mpxf,  mpxf = W*H*seg_frames / 1e6.
# LTX base/k are the plan's stated anchors (704x480x49 -> ~4.95 GB). Wan/turbo pass through
# the measured ~4.2 GB @480p; wan sits ~0.3 GB above turbo (same k, higher floor).
HAND_VRAM = {
    "ltx": (2.3, 0.16),
    "wan": (3.7, 0.04),
    "wan-turbo": (3.4, 0.04),
}

# Time hand constants mirror studio.update_est()'s two-branch table (studio.py:2390-2392).
# LOAD / SEAM / SEG_REF stay hand even after a refit; only COEF/WARM/DECODE are re-derived.
HAND_TIME = {
    "ltx": {"COEF": 1.5, "WARM": 70.0, "DECODE": 20.0, "LOAD": 150.0, "SEAM": 90.0, "SEG_REF": 49.0},
    "wan": {"COEF": 4.8, "WARM": 220.0, "DECODE": 38.0, "LOAD": 40.0, "SEAM": 90.0, "SEG_REF": 29.0},
    "wan-turbo": {"COEF": 4.8, "WARM": 220.0, "DECODE": 38.0, "LOAD": 40.0, "SEAM": 90.0, "SEG_REF": 29.0},
}


# ============================================================================================
# tiny coercion + markup helpers (local re-impl of the field_visuals idiom; do NOT import it)
# ============================================================================================
def _f(x, default=0.0):
    """Coerce a stringy/None dial to float; bools and junk -> `default`."""
    if x is None or isinstance(x, bool):
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _s(x, default=""):
    """Coerce to a stripped lower-case-safe string; None -> `default`."""
    if x is None:
        return default
    try:
        return str(x)
    except Exception:
        return default


def _c(color, text):
    """Wrap `text` in a balanced Rich color tag (balanced -> can never crash the Static)."""
    return "[%s]%s[/%s]" % (color, text, color)


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# ============================================================================================
# estimators
# ============================================================================================
def vram_est(cfg, fit=None):
    """-> (est_gb, cap_gb, reserve_gb). cfg keys used: backend, W, H, seg_frames, steps,
    ltx_variant, reserve_gb. cap_gb is the 8.0 physical card; the red zone starts at
    cap_gb - reserve_gb. Uses fit['vram'][backend] = {'base_gb': b, 'k_gb_per_mpxf': k}
    when present AND fitted from >=3 rows, else the hand constants."""
    be = _s(cfg.get("backend"), "ltx").strip().lower() or "ltx"
    bkey = be if be in HAND_VRAM else "ltx"      # distilled variant -> treated as ltx
    base, k = HAND_VRAM[bkey]
    if fit and isinstance(fit.get("vram"), dict):
        fv = fit["vram"].get(bkey)
        if isinstance(fv, dict) and _f(fv.get("rows")) >= 3:
            base = _f(fv.get("base_gb"), base)
            k = _f(fv.get("k_gb_per_mpxf"), k)
    W, H, sf = _f(cfg.get("W")), _f(cfg.get("H")), _f(cfg.get("seg_frames"))
    mpxf = (W * H * sf) / 1e6 if (W > 0 and H > 0 and sf > 0) else 0.0
    est = base + k * mpxf
    cap = 8.0
    reserve = _clamp(_f(cfg.get("reserve_gb"), 1.0), 0.0, cap)
    return est, cap, reserve


def ram_est(cfg):
    """-> (est_gb, cap_gb=26.0). cfg keys: backend, mode ('director' or not), consult
    (bool, best-effort), total_frames, W, H, chain. Static component table, no fit."""
    be = _s(cfg.get("backend"), "ltx").strip().lower()
    base = 2.0                                                    # studio + misc
    offload = {"ltx": 14.0, "wan": 6.0, "wan-turbo": 6.0}.get(be, 14.0)
    director = 8.0 if _s(cfg.get("mode")).strip().lower() == "director" else 0.0
    consult = 8.0 if cfg.get("consult") else 0.0
    total = base + offload + director + consult
    if not cfg.get("chain"):                                     # single clip -> decoded-frames working set
        W, H, tf = _f(cfg.get("W")), _f(cfg.get("H")), _f(cfg.get("total_frames"))
        if W > 0 and H > 0 and tf > 0:
            total += tf * W * H * 3 * 2 / 1e9
    cap = 26.0
    return min(total, cap), cap


def quality_score(cfg):
    """-> (0-100, one-line note). Hand heuristic; the RENDERED gauge carries the literal
    label 'rough guide'. The note stays qualitative (never claims an absolute)."""
    be = _s(cfg.get("backend"), "ltx").strip().lower()
    if be not in ("ltx", "wan", "wan-turbo"):
        be = "ltx"
    distilled = _s(cfg.get("ltx_variant")).strip().lower() == "distilled"
    score = float({"wan": 62, "wan-turbo": 55, "ltx": 48}.get(be, 48))
    steps = _f(cfg.get("steps"))
    if be == "wan-turbo":
        steps = min(steps, 8.0)
    score += _steps_bonus(be, steps, distilled)
    longside = max(_f(cfg.get("W")), _f(cfg.get("H")))           # res term: 512->0, 704->+6, 768->+8
    if longside >= 768:
        score += 8
    elif longside >= 704:
        score += 6
    elif longside >= 640:
        score += 3
    cfgv = _f(cfg.get("cfg"))                                    # guidance-in-sweet-spot term
    if be == "ltx" and 2.5 <= cfgv <= 4.0:
        score += 5
    elif be == "wan" and 4.0 <= cfgv <= 6.0:
        score += 5
    # (wan-turbo is CFG-distilled at a fixed 1.0 -> no sweet-spot term)
    if cfg.get("chain"):                                         # small bonuses only when chained
        if be in ("wan", "wan-turbo") and _s(cfg.get("wan_ref_anchor")).strip().lower() == "on":
            score += 4
        if be == "ltx" and _f(cfg.get("latent_adain")) > 0:
            score += 3
    q = int(round(_clamp(score, 5.0, 95.0)))                     # never certainty at the ends
    if q >= 75:
        note = "looks strong"
    elif q >= 60:
        note = "decent"
    elif q >= 45:
        note = "middling"
    else:
        note = "thin: raise steps/res"
    return q, note


def _steps_bonus(backend, steps, distilled):
    """Concave step-count bonus peaking ~35 (LTX) / ~37 (Wan). Distilled + wan-turbo are
    fixed few-step -> no bonus (their base already reflects the few-step regime)."""
    if backend == "wan-turbo" or distilled:
        return 0.0
    if backend == "wan":
        return _hump(steps, 37.0, 14.0, 40.0)
    return _hump(steps, 35.0, 12.0, 32.0)


def _hump(x, peak, amp, half):
    t = (x - peak) / half
    v = amp * (1.0 - t * t)
    return v if v > 0 else 0.0


def drift_risk(cfg):
    """-> (0-100, one-line note). Mechanical: seams dominate; anchors reduce. Single clip
    is literally 0 ('no seams — n/a')."""
    nseg = int(_f(cfg.get("nseg"), 1.0)) or 1
    if not cfg.get("chain") or nseg <= 1:
        return 0, "no seams — n/a"
    nseam = max(0, nseg - 1)
    base = min(90.0, nseam * 12.0)
    if _f(cfg.get("cond_strength"), 1.0) < 0.5:                  # loose continuity -> more drift
        base += 10.0
    be = _s(cfg.get("backend"), "ltx").strip().lower()
    anchors = ((be in ("wan", "wan-turbo") and _s(cfg.get("wan_ref_anchor")).strip().lower() == "on")
               or (be == "ltx" and _f(cfg.get("latent_adain")) > 0))
    if anchors:
        base *= 0.8
    if _s(cfg.get("steadiness")).strip().lower() == "hold":      # static scenes drift less visibly
        base -= 10.0
    d = int(round(_clamp(base, 0.0, 100.0)))
    if d >= 60:
        band = "high risk"
    elif d >= 35:
        band = "watch seams"
    elif d >= 15:
        band = "some drift"
    else:
        band = "low risk"
    note = "%d seam%s · %s" % (nseam, "" if nseam == 1 else "s", band)
    return d, note


# ============================================================================================
# auto-refit over runs/experiments.jsonl  (median-ratio, outlier-robust)
# ============================================================================================
def _read_experiments(path):
    """Parse the jsonl into a list of dicts, skipping corrupt lines. Missing file -> []."""
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        return []
    return rows


def _refit(rows):
    """Re-derive per-backend VRAM k and time COEF/WARM/DECODE by median ratio (robust to the
    known outliers, e.g. the 21646s decode row). Keeps hand constants for any backend with
    < 3 usable rows, flagging that via the stored 'rows' count."""
    fit = {"vram": {}, "time": {}}
    for be in HAND_VRAM:
        base_hand, k_hand = HAND_VRAM[be]
        ks = []
        for r in rows:
            if r.get("backend") != be:
                continue
            peak = _f(r.get("peak_vram_mb"))
            W, H, sf = _f(r.get("width")), _f(r.get("height")), _f(r.get("seg_frames"))
            if not (peak and W and H and sf):
                continue
            mpxf = W * H * sf / 1e6
            if mpxf <= 0:
                continue
            ks.append((peak / 1024.0 - base_hand) / mpxf)        # MiB -> GiB, hold hand base
        if len(ks) >= 3:
            fit["vram"][be] = {"base_gb": base_hand, "k_gb_per_mpxf": statistics.median(ks), "rows": len(ks)}
        else:
            fit["vram"][be] = {"base_gb": base_hand, "k_gb_per_mpxf": k_hand, "rows": len(ks)}

        ht = HAND_TIME[be]
        segref = ht["SEG_REF"]
        coefs, warms, decs = [], [], []
        for r in rows:
            if r.get("backend") != be:
                continue
            ps = r.get("phase_secs")
            if not isinstance(ps, dict) or not ps:
                continue
            W, H, sf = _f(r.get("width")), _f(r.get("height")), _f(r.get("seg_frames"))
            steps, nseg = _f(r.get("steps")), (_f(r.get("nseg")) or 1.0)
            if not (W and H and sf and steps and nseg):
                continue
            px = W * H / (512 * 320)
            ff = sf / segref
            gen, warm, dec = _f(ps.get("generating")), _f(ps.get("warmup")), _f(ps.get("decoding"))
            denom = steps * px * ff * nseg
            if gen and denom > 0:
                coefs.append(gen / denom)
            if warm and nseg > 0:
                warms.append(warm / nseg)
            if dec and ff > 0 and nseg > 0:
                decs.append(dec / (ff * nseg))
        tf = {"LOAD": ht["LOAD"], "SEAM": ht["SEAM"], "SEG_REF": segref}
        if len(coefs) >= 3:
            tf["COEF"] = statistics.median(coefs)
            tf["WARM"] = statistics.median(warms) if warms else ht["WARM"]
            tf["DECODE"] = statistics.median(decs) if decs else ht["DECODE"]
            tf["rows"] = len(coefs)
        else:
            tf["COEF"], tf["WARM"], tf["DECODE"], tf["rows"] = ht["COEF"], ht["WARM"], ht["DECODE"], len(coefs)
        fit["time"][be] = tf
    return fit


def _save_fit(repo, fit):
    """Atomic write of the fit cache (tmp + os.replace, mirroring studio.save_studio_config)."""
    path = os.path.join(repo, FIT_CACHE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fit, f)
        os.replace(tmp, path)
    except Exception:
        pass


def load_fit(repo):
    """Read the cached fit dict, or None if absent/corrupt."""
    try:
        with open(os.path.join(repo, FIT_CACHE)) as f:
            return json.load(f) or None
    except Exception:
        return None


def maybe_refit(repo, min_new_rows=5):
    """Cheap gate: if experiments.jsonl mtime <= cache mtime, return cached fit. Else parse the
    jsonl (skip corrupt lines), and if it has >= min_new_rows more rows than the cache's recorded
    row_count, refit + atomically rewrite the cache. First call (no cache) always builds a fit.
    Runs inline — sub-millisecond on this history; no threads. Never raises."""
    try:
        cache_path = os.path.join(repo, FIT_CACHE)
        exp_path = os.path.join(repo, EXPERIMENTS)
        cached = load_fit(repo)
        try:
            exp_mtime = os.path.getmtime(exp_path)
        except OSError:
            return cached                                        # no experiments file -> whatever we cached
        if cached is not None:
            try:
                cache_mtime = os.path.getmtime(cache_path)
            except OSError:
                cache_mtime = -1.0
            if exp_mtime <= cache_mtime:
                return cached                                    # nothing changed -> no parse
        rows = _read_experiments(exp_path)
        prev = _f((cached or {}).get("row_count")) if cached else 0.0
        if cached is not None and (len(rows) - prev) < min_new_rows:
            return cached                                        # too few new rows to bother re-fitting
        fit = _refit(rows)
        fit["row_count"] = len(rows)
        fit["ts"] = time.time()
        _save_fit(repo, fit)
        return fit
    except Exception:
        try:
            return load_fit(repo)
        except Exception:
            return None


def _fitted_time(cfg, fit):
    """Recompute the ETA from the fitted time constants (LOAD/SEAM/SEG_REF stay hand), mirroring
    studio.update_est()'s formula. -> seconds, or None if the backend lacks >=3 fitted rows.
    Used ONLY for the '(fit ...)' annotation; update_est()'s own math is never touched."""
    try:
        if not fit or not isinstance(fit.get("time"), dict):
            return None
        be = _s(cfg.get("backend"), "ltx").strip().lower()
        bkey = be if be in HAND_TIME else "ltx"
        tf = fit["time"].get(bkey)
        if not isinstance(tf, dict) or _f(tf.get("rows")) < 3:
            return None
        W, H, sf = _f(cfg.get("W")), _f(cfg.get("H")), _f(cfg.get("seg_frames"))
        steps = _f(cfg.get("steps"))
        if bkey == "wan-turbo":
            steps = min(steps, 8.0)
        nseg = int(_f(cfg.get("nseg"), 1.0)) or 1
        if not (W and H and sf and steps):
            return None
        px = W * H / (512 * 320)
        segref = _f(tf.get("SEG_REF"), 49.0) or 49.0
        ff = sf / segref
        director = _s(cfg.get("mode")).strip().lower() == "director"
        nseam = max(0, nseg - 1)
        if director and _s(cfg.get("steadiness"), "hold").strip().lower() != "evolve":
            nseam = -(-nseam // 3)                               # ceil div — director.py redirect cadence
        secs = (tf.get("LOAD", 0.0)
                + nseg * (steps * tf.get("COEF", 0.0) * px * ff + tf.get("WARM", 0.0) + tf.get("DECODE", 0.0) * ff)
                + (tf.get("SEAM", 0.0) * nseam if director else 0.0))
        return secs
    except Exception:
        return None


# ============================================================================================
# rendering — five bars, each = one bar line + one caption line, every line <= 48 cols
# ============================================================================================
def _zone_bar(value, vmax, width, red_start=None, reserve_at=None):
    """A fill bar (low = good). Filled cells green, ramping WARN->BAD across the red zone;
    empty red-zone cells are BAD-tinted; a reserve line ('┃', amber) is drawn at reserve_at."""
    if vmax <= 0:
        vmax = 1.0
    filled = int(round(_clamp(value / vmax, 0.0, 1.0) * width))
    rcol = None if reserve_at is None else int(round((reserve_at / vmax) * width))
    out = []
    for i in range(width):
        if rcol is not None and i == rcol:
            out.append(_c(WARN, "┃"))
            continue
        center = (i + 0.5) / width * vmax
        red = red_start is not None and center >= red_start
        if i < filled:
            if red:
                col = BAD
            elif red_start is not None and center >= red_start * 0.85:
                col = WARN
            else:
                col = MID
            out.append(_c(col, "█"))
        else:
            out.append(_c(BAD if red else DIM, "░"))
    return "".join(out)


def _score_bar(pct, width, fill_color):
    """A simple 0-100 fill bar in a single colour (used for time/quality/drift)."""
    filled = int(round(_clamp(pct, 0.0, 100.0) / 100.0 * width))
    return "".join(_c(fill_color, "█") for _ in range(filled)) + "".join(_c(DIM, "░") for _ in range(width - filled))


def _chain_boxes(ns, width, col):
    """[██]─[██]─[██] — one box per shot in the chain, joiners dim. Padded to `width` cells so the
    value tag stays column-aligned with the bar gauges. Overflow shows as a dim '┄+N' tail.
    NB the opening bracket is markup-escaped ('\\[') or Rich eats the box as a style tag."""
    ns = max(1, int(ns))
    shown = ns if ns * 5 - 1 <= width else max(1, (width - 3 + 1) // 5)   # tail '┄+N' needs ~3 cells
    parts = []
    for i in range(shown):
        if i:
            parts.append(_c(DIM, "─"))
        parts.append(_c(col, "\\[██]"))
    used = shown * 5 - 1
    if ns > shown:
        tail = "┄+%d" % (ns - shown)
        parts.append(_c(DIM, tail))
        used += len(tail)
    if used < width:
        parts.append(" " * (width - used))
    return "".join(parts)


def _fmt_secs(s):
    s = max(0.0, s)
    if s < 90:
        return "%ds" % int(round(s))
    m = s / 60.0
    if m < 90:
        return "~%dm" % int(round(m))
    return "~%.1fh" % (m / 60.0)


def _fmt_delta(d):
    sign = "+" if d >= 0 else "-"
    a = abs(d)
    if a < 90:
        return "%s%ds" % (sign, int(round(a)))
    if a < 5400:
        return "%s%dm" % (sign, int(round(a / 60.0)))
    return "%s%.1fh" % (sign, a / 3600.0)


def _row(title, bar, label):
    return title + " " + bar + " " + label


def render_readout(cfg, secs, fit, width=None):
    """The full six-gauge strip as ONE Rich-markup string. Order: VRAM, CLIP, RAM, SHOTS, QUALITY,
    DRIFT. Defensive: a single gauge failing degrades to a dim placeholder rather than crashing
    the panel. `width` = the panel's CONTENT width in cells (studio measures it and re-renders
    on window resize); bars are sized to fit so nothing wraps on narrow/snapped windows."""
    cfg = cfg or {}
    # per-row overhead: 5-char title + 2 separating spaces + value tag up to ~10 chars
    w_bar = max(12, min(34, int(width) - 18)) if width else W_BAR
    narrow = bool(width) and int(width) < 40
    lines = [_c(ACCENT, "▌ READOUT")]

    # ---- VRAM ----
    try:
        est, cap, reserve = vram_est(cfg, fit)
        red_start = cap - reserve
        bar = _zone_bar(est, cap, w_bar, red_start=red_start, reserve_at=red_start)
        col = BAD if est >= red_start else (WARN if est >= red_start * 0.9 else CLEAN)
        lines.append(_row("VRAM ", bar, _c(col, "%.1f/%.1fG" % (est, cap))))
        lines.append("      " + _c("dim", "peak · red≥%.1fG rsv%.1f" % (red_start, reserve)))
    except Exception:
        lines += ["VRAM " + _c("dim", "n/a"), ""]

    # ---- CLIP (max frames per shot: the lever RES actually trades. Peak VRAM is capped, so a
    #      higher res SHRINKS the segment instead of using more memory -> this is what visibly
    #      moves when you change RES, where the VRAM bar stays ~flat) ----
    try:
        sf = int(_f(cfg.get("seg_frames")))
        ns = int(_f(cfg.get("nseg"))) or 1
        fpsv = _f(cfg.get("fps")) or 0.0
        if sf > 0:
            frac = _clamp(sf / 130.0, 0.0, 1.0) * 100.0     # ~130fr ~ a long single segment; bar shrinks as res climbs
            col = CLEAN if ns <= 1 else (MID if ns <= 3 else WARN)
            val = ("%dfr x%d" % (sf, ns)) if ns > 1 else ("%dfr" % sf)
            lines.append(_row("CLIP ", _score_bar(frac, w_bar, col), _c(col, val)))
            sper = ("%.1fs/shot " % (sf / fpsv)) if fpsv > 0 else ""
            lines.append("      " + _c("dim", sper + ". res up=shorter"))
        else:
            lines += ["CLIP " + _c("dim", "n/a"), ""]
    except Exception:
        lines += ["CLIP " + _c("dim", "n/a"), ""]

    # ---- RAM ----
    try:
        est, cap = ram_est(cfg)
        bar = _zone_bar(est, cap, w_bar, red_start=24.0)
        col = BAD if est >= 24.0 else (WARN if est >= 21.0 else CLEAN)
        lines.append(_row("RAM  ", bar, _c(col, "%d/%dG" % (int(round(est)), int(round(cap))))))
        lines.append("      " + _c("dim", "+offload+decode · red≥24G"))
    except Exception:
        lines += ["RAM  " + _c("dim", "n/a"), ""]

    # ---- SHOTS (the chain itself: one box per SEGMENT pass, glued in order. Replaced the old
    #      TIME log-bar; the render ETA lives on in the caption so no information was lost) ----
    try:
        sf = int(_f(cfg.get("seg_frames")))
        ns = int(_f(cfg.get("nseg"))) or 1
        fpsv = _f(cfg.get("fps")) or 0.0
        if sf > 0 and fpsv > 0:
            col = CLEAN if ns <= 1 else (MID if ns <= 3 else WARN)
            tf = _f(cfg.get("total_frames"))
            actual = (tf if tf > 0 else float(sf * ns)) / fpsv
            asked = _f(cfg.get("seconds"))
            val = ("%gs→%.1fs" % (asked, actual)) if (asked > 0 and abs(asked - actual) > 0.05) \
                else ("%.1fs" % actual)
            lines.append(_row("SHOTS", _chain_boxes(ns, w_bar, col), _c(col, val)))
            cap = "%d × %.1fs" % (ns, sf / fpsv)
            if secs is not None:
                cap += " · " + _fmt_secs(secs) + " render"
                ft = _fitted_time(cfg, fit)
                if ft is not None and not narrow:  # the fit annot is the first thing to wrap when snapped
                    cap += "  (fit %s)" % _fmt_delta(ft - secs)
            lines.append("      " + _c("dim", cap))
        else:
            lines.append("SHOTS " + _c("dim", "enter numbers"))
            lines.append("      " + _c("dim", "one box per glued segment"))
    except Exception:
        lines += ["SHOTS " + _c("dim", "n/a"), ""]

    # ---- QUALITY (rough guide) ----
    try:
        q, note = quality_score(cfg)
        col = CLEAN if q >= 70 else (MID if q >= 50 else (WARN if q >= 30 else BAD))
        lines.append(_row("QUAL ", _score_bar(q, w_bar, col), _c(col, "%d/100" % q)))
        lines.append("      " + _c("dim", "rough guide · " + note))
    except Exception:
        lines += ["QUAL " + _c("dim", "rough guide"), ""]

    # ---- DRIFT / seam risk ----
    try:
        d, note = drift_risk(cfg)
        col = BAD if d >= 60 else (WARN if d >= 35 else (MID if d >= 15 else CLEAN))
        lines.append(_row("DRIFT", _score_bar(d, w_bar, col), _c(col, "%d/100" % d)))
        lines.append("      " + _c("dim", note))
    except Exception:
        lines += ["DRIFT" + _c("dim", " n/a"), ""]

    return "\n".join(lines)
