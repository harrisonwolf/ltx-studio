# 2026-07-04 — T22: Global READOUT meters (NEW RUN right column)

Scope: add a persistent "▌ READOUT" strip at the bottom of the NEW RUN tab's right-hand
column showing FIVE live gauges — est. peak VRAM, RAM/swap pressure, generation time,
quality outlook, drift/seam risk — that re-render on any dial change. The dials of the
form all feed these few global outcomes; today only *time* is surfaced (`#runest` one-liner)
and nothing estimates VRAM/RAM per-config at all. Ships with hand formulas PLUS an
auto-refit that re-derives VRAM/time constants from the real run history in
`runs/experiments.jsonl` as it grows. Display-only: no run behavior changes.

This is a single-phase plan; one agent executes all three items sequentially in one session.

## Decisions (LOCKED by the user — do not relitigate)

1. Meters: all five (VRAM, RAM/swap, gen-time, quality, drift/seam) — quality and drift are TWO separate gauges.
2. Quality gauge = honest hand-tuned heuristic, visibly labeled "rough guide".
3. Calibration = AUTO-REFITTING: hand-formula fallback + a refit pass over `runs/experiments.jsonl` whenever it has grown, persisted to a fit cache.
4. Panel + autofit are exposed configs (`runs/studio_config.json` keys), defaulting ON.

## Hard environment rules (violating these breaks the user's machine or trust)

- **NEVER run `nvidia-smi` or any NVML call** — it crashes this WSL2/Blackwell box (reproduced; restarts the whole VM). No exceptions, not even "quick checks".
- **Never launch CUDA / load a model.** Verification is `py_compile` + torch-free CPU tests only.
- **Do not `git commit` / `git add`.** Leave all changes in the working tree.
- The executing session's Bash tool is **Git Bash, not WSL**: run repo commands as
  `wsl.exe -- bash -c 'cd /home/wolve/video_gen/FramePack && <cmd>'` (always `cd` first;
  write multi-line scripts to files with the Write tool — heredocs/backticks mangle through wsl.exe;
  avoid nested `$()` and escaped brackets in one-liners, they mangle too).
- **Do NOT touch `field_visuals.py`** — a concurrent session may be editing it. Build a separate module.
- Do not modify `gpu_budget.py`, `experiment_log.py`, `director.py`, `run_ltx.py`, `studio_core.py`.

## Item index

| # | Title | Files (new unless noted) | Est. size |
|---|---|---|---|
| 1 | `readout.py` — estimators + auto-refit + gauge renderers | `readout.py` | ~350-450 lines |
| 2 | CPU tests | `_t22tests/test_readout.py` | ~150 lines |
| 3 | Studio wiring | `studio.py` (modify, 4 small regions) | ~40 lines |

---

## Item 1 — `readout.py` (new module, pure stdlib)

**Goal:** all estimator math, the auto-refit, and the five gauge renderers live in one
import-light module so the `studio.py` wiring stays thin (same philosophy as
`field_visuals.py` — read it for the house rendering style, but do not modify it).

**Public contract** (copy verbatim; keep pure-stdlib — `os, json, math, statistics, time` only):

```python
# readout.py — T22 global readout meters: estimators, auto-refit, gauge renderers.
# Pure stdlib. No torch, no textual, no studio imports. All colors via Rich markup tags.

FIT_CACHE = "runs/readout_fit.json"       # relative to the FramePack repo root
EXPERIMENTS = "runs/experiments.jsonl"

def vram_est(cfg: dict, fit: dict | None = None) -> tuple[float, float, float]:
    """-> (est_gb, cap_gb, reserve_gb). cfg keys used: backend, W, H, seg_frames, steps,
    ltx_variant, reserve_gb. cap_gb is the 8.0 physical card; the red zone starts at
    cap_gb - reserve_gb. Uses fit['vram'][backend] = {'base_gb': b, 'k_gb_per_mpxf': k}
    when present AND fitted from >=3 rows, else the hand constants."""

def ram_est(cfg: dict) -> tuple[float, float]:
    """-> (est_gb, cap_gb=26.0). cfg keys: backend, mode ('director' or not), consult
    (bool, best-effort), total_frames, W, H, chain. Static component table, no fit."""

def quality_score(cfg: dict) -> tuple[int, str]:
    """-> (0-100, one-line note). Hand heuristic; the RENDERED gauge must carry the
    literal label 'rough guide'."""

def drift_risk(cfg: dict) -> tuple[int, str]:
    """-> (0-100, one-line note). Mechanical: seams dominate; anchors reduce."""

def load_fit(repo: str) -> dict | None
def maybe_refit(repo: str, min_new_rows: int = 5) -> dict | None:
    """Cheap gate: if experiments.jsonl mtime <= cache mtime, return cached fit.
    Else parse the jsonl (skip corrupt lines), and if it has >= min_new_rows more rows
    than the cache's recorded row_count, refit + atomically rewrite the cache
    (tmp + os.replace, mirroring studio.save_studio_config). Runs inline — 44-row
    history is sub-millisecond math; no threads."""

def render_readout(cfg: dict, secs: float | None, fit: dict | None) -> str:
    """The full five-gauge strip as ONE Rich-markup string. Every line <= 48 columns
    (the panel wraps past ~50). Order: VRAM, RAM, TIME, QUALITY, DRIFT."""
```

**cfg dict** is assembled by studio (item 3) as:
`{backend, mode, steadiness, W, H, fps, seg_frames, total_frames, nseg, chain, steps,
cfg, cond_strength, ltx_variant, wan_ref_anchor, latent_adain, reserve_gb, consult}` —
strings as the form holds them; coerce defensively (`_num`-style helpers like
`field_visuals.py:41`).

**Hand-constant anchors (v0 fallback — refit overrides VRAM/time):**
- VRAM (per backend, model `est_gb = base_gb + k * mpxf` where `mpxf = W*H*seg_frames/1e6`):
  anchor points from real runs — LTX 2B @704x480x49f ≈ 4.5-5.0 GB; wan-turbo @480p measured
  peak 4.20 GB; wan similar to turbo +~0.3. Set base/k per backend to pass through those
  anchors (e.g. LTX: base≈2.3, k≈0.16). Distilled variant ≈ ltx.
- RAM components (GB, sum then min against per-run reality): base studio+misc 2.0;
  backend offload: ltx 14.0 (T5+transformer CPU offload), wan/wan-turbo 6.0;
  `mode=='director'` +8.0 (resident CPU fp16 director); `consult` +8.0;
  single-clip (not chain) adds `total_frames*W*H*3*~2 / 1e9` (decoded-frames working set —
  the old SAFE_PX swap-thrash lesson). Cap 26.0; red zone >= 24.0 ("swap-thrash").
  Director+LTX should land ≈ 22 GB — sanity-check against that observed number.
- Time: the gauge does NOT recompute; studio passes `secs` from `update_est()` (item 3).
  Render bins on a log-ish scale with ticks 1m / 5m / 15m / 1h / 3h.
- Quality (0-100, "rough guide"): backend base (wan 62, wan-turbo 55, ltx 48);
  steps curve peaking ~30-40 (LTX) / ~25-50 (Wan), distilled variant treated as its own
  fixed few-step (≈ ltx base, no steps bonus); + res term (long side 512→0, 704→+6, 768→+8);
  + cfg-in-sweet-spot term (ltx 2.5-4, wan 4-6, turbo fixed 1.0 → no term);
  + small bonuses when chained: wan_ref_anchor on Wan (+4), latent_adain>0 on LTX (+3).
  Clamp 5..95 — never show certainty at the ends.
- Drift risk (0-100): `nseam = max(0, nseg-1)`; base = min(90, nseam*12);
  modifiers: cond_strength < 0.5 → +10; anchors on (per-backend as above) → -20%;
  steadiness == 'hold' → -10 (static scenes drift less visibly); single clip → literally 0
  ("no seams — n/a").

**Refit math (`maybe_refit`)** — median-ratio, not least squares (few rows, known outliers:
one 21646s decode row exists in history):
- Row keys available (verified in `experiment_log.py`: backend :68, steps :71, nseg :80,
  seg_frames :81, phase_secs :99 `{load,warmup,generating,decoding,...}`, peak_vram_mb :105;
  also w/h — VERIFY the exact key names for width/height by reading
  `experiment_log.build_record` before coding, and skip rows missing them).
- VRAM per backend: with `mpxf` as above, fit `k = median((peak_gb - base_gb)/mpxf)` holding
  the hand `base_gb` fixed; require >= 3 rows with `peak_vram_mb`, else keep hand k. Store
  `{'vram': {backend: {'base_gb':…, 'k_gb_per_mpxf':…, 'rows': n}}, 'row_count': N, 'ts': …}`.
- Time per backend (only rows with non-empty phase_secs): `COEF = median(generating /
  (steps*px*ff*nseg))`, `WARM = median(warmup/nseg)`, `DECODE = median(decoding/(ff*nseg))`
  with `px = W*H/(512*320)`, `ff = seg_frames/SEG_REF` (SEG_REF 49 ltx / 29 wan — mirror
  `studio.py:2390-2392`). Store under `fit['time'][backend]`; >= 3 rows or keep hand values.
  NOTE: item 3 does NOT rewire `update_est()` to use the fit in v1 — the time fit is stored
  and rendered as a "(fit: ±Ns vs formula)" annotation only. Do not change update_est math.
- Corrupt/missing jsonl → return `load_fit()` result or None; NEVER raise out of `maybe_refit`.

**Rendering:** mirror `field_visuals.py`'s style (palette constants at its :22-36, bar +
marker helpers :36-66 — reimplement locally, do not import from it). Each gauge = one bar
line + one caption line, ~48 cols. VRAM/RAM bars show a red zone; VRAM draws the reserve
line at `cap - reserve_gb`. Quality gauge caption MUST contain "rough guide". When
`secs is None` (estimator failed), the TIME gauge shows `[dim]enter numbers[/dim]`.

**What NOT to change:** `field_visuals.py`, `gpu_budget.py`, `experiment_log.py` (read-only
references). Do not import torch/textual/studio into `readout.py`.

**Pitfalls:** experiments.jsonl rows are schema v1 AND v2 (fields added over time) — use
`.get` everywhere. `phase_secs` values may be strings; coerce. wan-turbo rows have
steps clamped ≤8 — the COEF fit must use the row's own recorded steps, never the form value.
Rich markup: unbalanced `[tag]` crashes the Static — reuse a `_c()` helper and test-render.

---

## Item 2 — `_t22tests/test_readout.py` (new, torch-free)

**Goal:** prove the module renders and the math behaves, without any GPU or textual.

Cases (plain `assert`s, runnable via `venv/bin/python _t22tests/test_readout.py`):
1. `render_readout(cfg, secs, None)` returns a non-empty string containing all five gauge
   titles, for cfg variants: ltx single-clip, ltx chained director, wan chained, wan-turbo.
2. Every rendered line ≤ 48 visible columns after stripping `[..]` markup tags.
3. `vram_est`: monotonic in W*H and in seg_frames; ltx@704x480x49 lands in 4.0-5.5 GB.
4. `ram_est`: director+ltx+consult > plain ltx > plain wan; director+ltx ≈ 20-24 GB.
5. `drift_risk`: single clip == 0; 8-shot > 2-shot; anchors reduce the 8-shot score.
6. `quality_score` ∈ [5,95] on all variants; caption/note mentions nothing absolute.
7. `maybe_refit` on a synthetic jsonl (write ~10 rows with planted k/COEF + 1 corrupt line
   + 1 outlier row) recovers the planted constants within 25% (median survives the outlier),
   writes the cache, and a second call with unchanged mtime returns the cache without re-parsing.
8. Sparse history (2 rows) → fit keeps hand constants (`rows` < 3 ⇒ fallback flagged).
9. Missing/corrupt experiments.jsonl → `maybe_refit` returns None/cache, no exception.

**Acceptance:** script exits 0 printing `ALL_T22_CHECKS_PASS`.

---

## Item 3 — `studio.py` wiring (4 small regions; ~40 lines total)

Current anchors (verified 2026-07-04 — re-verify before editing, the file is hot):

a) **Mount the panel** — `studio.py:1613-1615` currently:
```python
                    yield Static("", id="fieldvisual")
                    yield Static(INFO, id="newinfo")
                    with Vertical(id="blindpanel"):
```
Insert between `#newinfo` and `#blindpanel`:
```python
                    yield Static("", id="readout")      # T22: global readout meters
```
b) **CSS** — next to `studio.py:1489-1492` add (mirroring `#fieldvisual`, distinct border):
```css
    #readout { width: 1fr; border: round #1c7a42; background: #08160d;
               padding: 0 1; margin: 1 1 0 2; height: auto; display: none; }
    #readout.-active { display: block; }
```
c) **Refresh hook** — `update_est()` (`studio.py:2367`) already fires on every
`on_input_changed` (:1791), `on_select_changed` (:1810), `on_input_submitted` (:1824).
At the END of its `try` block (after `est.update(...)` :2401) add
`self._update_readout(secs, (W, H, fps, total_frames, seg_frames, nseg, chain))`, and in
the `except` branch (:2403) add `self._update_readout(None, None)`. Then add ONE new method
`_update_readout(self, secs, plan)` that: no-ops when the readout config is off or
`#blindpanel` has class `-active` (pattern: `_show_field_visual` :1779); assembles the cfg
dict (reads via `self.v(...)`, `reserve_gb` from the `#vram_reserve` select / config,
`consult` best-effort from the consult daemon's liveness if trivially readable else False);
calls `readout.maybe_refit(REPO)` (it self-gates on mtime — cheap) then
`readout.render_readout(cfg, secs, fit)`; updates `#readout` + toggles `-active` exactly
like `_show_field_visual` does (:1783-1788). Import `readout` defensively at top next to the
`field_visuals` try/except import (`readout = None` on failure ⇒ panel never shows).
d) **Blind-builder coexistence** — `_open_blind_panel` (`studio.py:2767`, it already hides
`#newinfo` and `#fieldvisual` around :2770) also hide `#readout`; `_close_blind_panel`
(:2785) restore it (call `self._update_readout` via a plain `self.update_est()`).
e) **Config keys** — in `on_mount` (:1692, near the :1709 `vram_reserve_gb` read) read
`readout_enabled` (default True) and `readout_autofit` (default True) from
`load_studio_config()`; `_update_readout` respects both (autofit False ⇒ skip `maybe_refit`,
use `load_fit`). No new UI control in v1 — the JSON keys satisfy the exposed-configs rule.

**What NOT to change:** `update_est()`'s math and its `#runest` line (append the one call
only); `_plan()`; `build()`; the blind A/B logic beyond the two hide/show lines;
`on_select_changed`'s `blind_var`/`backend`/`vram_reserve` branches; `field_visuals.py`.

**Pitfalls:** `update_est` runs on EVERY keystroke (`on_input_changed`) — `maybe_refit`'s
mtime gate makes that cheap, but do NOT parse the jsonl unconditionally per keystroke.
`#blindpanel.has_class('-active')` query can raise during compose — wrap in try/except like
`_show_field_visual`. The right column already stacks `#fieldvisual` (transient) above
`#newinfo`; `#readout` sits below `#newinfo` and is expected to be ~12-14 lines tall.

**Tests/acceptance:** `py_compile` green on `studio.py` + `readout.py`; item-2 script passes;
`grep -n "readout" studio.py` shows exactly the regions above (no strays).

## Execution order

Single agent, sequential: Item 1 → Item 2 (iterate until green) → Item 3 → final compile +
test run. No parallel fan-out — items 1/2 feed 3 and share one author cheaply.

## Verification (close-out)

1. `wsl.exe -- bash -c 'cd /home/wolve/video_gen/FramePack && venv/bin/python -m py_compile readout.py studio.py && echo COMPILE_OK'`
2. `wsl.exe -- bash -c 'cd /home/wolve/video_gen/FramePack && venv/bin/python _t22tests/test_readout.py'` → `ALL_T22_CHECKS_PASS`
3. Report what the REAL-GPU follow-ups are (cannot be done in-session): does the VRAM gauge
   track actual `[[VRAM]]` peaks on the next runs; does the RAM gauge's red zone line up with
   felt desktop lag; is the refit's k sane once ≥3 fresh rows per backend exist.
4. NO git commit. Leave everything in the working tree and summarize files touched.

## Out of scope (deferred — do not build)

- Any behavior change driven by the meters (warnings that block queueing, auto-tuning dials).
- Rewiring `update_est()` to use the fitted time constants (v2, after the fit is validated).
- A UI toggle widget for the panel (JSON config keys only in v1).
- T21 queue-card redesign; T20 themes; per-meter tooltips.
- `field_visuals.py` changes of any kind (concurrent session owns it).
