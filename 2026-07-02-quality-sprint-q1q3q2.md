# 2026-07-02 — Quality Sprint: Q1 checkpoint uplift → Q3 measurement floor → Q2 drift-kill anchors

Scope: Three-phase quality sprint for the LTX Studio video generator in THIS directory
(`/home/wolve/video_gen/FramePack`). Q1 moves the LTX backend off the obsolete 0.9.0 2B
checkpoint to 0.9.5 (same architecture + pipeline class). Q3 turns the drift/seam signals the
code already computes into logged telemetry + a paired A/B harness so quality changes can be
PROVEN. Q2 arrests identity/color drift by anchoring to SHOT 1 (latent AdaIN + latent seam
fuse + quantile palette lock) instead of chasing the previous shot's tail (a compounding random
walk). Source analysis: `/home/wolve/video_gen/quality-roadmap.md` (§1 items #1, #3, #2).
The phases are STRICTLY SEQUENTIAL (all touch `director.py`) and each must leave the studio
fully working with every new behavior OFF by default or value-identical unless stated.

**Project invariants (violating these = rejected work):**
1. Local/offline, open-weights only. No runtime API calls.
2. The studio's architecture stays: Textual TUI (`studio.py`) → `studio_core.py` JobManager →
   `director.py` subprocess speaking `[[MARKER]]` lines on stdout. New behavior = optional
   flags/toggles; defaults preserve current output unless the plan says otherwise.
3. NEVER run `nvidia-smi` or NVML (it crashes this WSL2/Blackwell box — reproduced).
4. GPU is 8GB, shared with Windows, and MAY BE BUSY with the user's renders. Do not launch any
   CUDA process without the user's explicit go-ahead in-session. CPU tests + `py_compile` are
   always safe.

## Environment (read first — this box is unusual)

- Files live in WSL. From Claude Code on Windows, use UNC paths for Read/Edit/Write:
  `\\wsl.localhost\ubuntu\home\wolve\video_gen\FramePack\<file>`.
- Shell commands run via: `wsl.exe -- bash -c 'cd /home/wolve/video_gen/FramePack && <cmd>'`.
  ALWAYS `cd` first (a leading `/path` argument gets mangled by Git Bash path conversion).
  Heredocs/backticks get mangled through wsl.exe — write scripts/test files with the Write tool
  to the UNC path, then execute them.
- Python: `venv/bin/python` (torch 2.11.0+cu128, diffusers 0.38.0). Compile check:
  `wsl.exe -- bash -c 'cd /home/wolve/video_gen/FramePack && venv/bin/python -m py_compile director.py studio.py studio_core.py experiment_log.py && echo OK'`
- `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` is set by callers; keep it.
- This directory IS a git repo. Do NOT commit; leave changes in the working tree (the user
  reviews `git diff` and commits per phase).
- The studio only picks up changes on relaunch (`./studio.sh`); an in-flight render subprocess
  is unaffected by edits.

## Decisions to confirm (locked at dispatch; defaults apply if unstated)

1. **0.9.5 becomes the DEFAULT LTX checkpoint** (escape hatch `--ltx_repo Lightricks/LTX-Video`).
   Default: yes.
2. **0.9.8-distilled 2B stretch goal**: only if a 2B distilled artifact verifiably exists
   (verify via `huggingface_hub.list_repo_files`); otherwise document absence and skip.
   Default: verify-then-skip-if-absent. Do NOT substitute any 13B checkpoint (48GB, won't serve
   the fast role on 8GB).
3. **Q2 defaults**: `--latent_adain 0.0` (off), `--latent_fuse` off, `--palette_lock 0.0` (off).
   Ship OFF; the user turns them on after Q3 telemetry proves them. Default: all off.
4. **Pair/blind ratings storage**: `runs/pair_ratings.jsonl` (separate from experiments.jsonl).
   Default: yes.

## Item index

| # | Title | Files (modified unless noted) | Suggested model |
|---|---|---|---|
| Q1 | LTX checkpoint uplift 0.9.0→0.9.5 (+distilled probe) | director.py | Sonnet 5 |
| Q3 | Measurement floor: DRIFT/SEAMMSE/TOKENS markers + pair harness + noise-floor | director.py, studio_core.py, experiment_log.py, studio.py | Sonnet 5 |
| Q2 | Drift-kill anchor pack: latent AdaIN + seam fuse + palette lock | director.py (+ new `palette.py` optional) | Opus 4.8 |

---

## Q1 — LTX checkpoint uplift (0.9.0 → 0.9.5)

**Goal:** The LTX backend loads `Lightricks/LTX-Video-0.9.5` (verified: proper diffusers-layout
repo) instead of the original `Lightricks/LTX-Video` (= 0.9.0 weights), reusing the already-
cached T5 to avoid a ~9GB re-download, with the 0.9.5 timestep-conditioned VAE decode kwargs
wired, and the existing scheduler monkeypatch re-validated.

**Fix shape** — all in `director.py`:

1. `director.py:197` currently:
   ```python
   pipe = LTXConditionPipeline.from_pretrained("Lightricks/LTX-Video", torch_dtype=torch.bfloat16)
   ```
   Replace with a repo-arg load that REUSES the cached T5 (0.9.5 ships its own text_encoder
   shards; loading them naively re-downloads ~9GB you already have):
   ```python
   base = "Lightricks/LTX-Video"                        # T5 + tokenizer already in the HF cache
   repo = getattr(args, "ltx_repo", None) or "Lightricks/LTX-Video-0.9.5"
   if repo == base:
       pipe = LTXConditionPipeline.from_pretrained(base, torch_dtype=torch.bfloat16)
   else:
       from transformers import T5EncoderModel, T5TokenizerFast
       te = T5EncoderModel.from_pretrained(base, subfolder="text_encoder", torch_dtype=torch.bfloat16)
       tok = T5TokenizerFast.from_pretrained(base, subfolder="tokenizer")
       pipe = LTXConditionPipeline.from_pretrained(repo, text_encoder=te, tokenizer=tok,
                                                   torch_dtype=torch.bfloat16)
   print(f"LTX checkpoint: {repo}", flush=True)
   ```
2. New argparse flags (add next to `--latent_chain`, `director.py:464`):
   `--ltx_repo` (default `"Lightricks/LTX-Video-0.9.5"`), `--ltx_no_mush_patch`
   (action="store_true").
3. Decode kwargs: 0.9.5's VAE is timestep-conditioned. FIRST verify the installed pipeline
   accepts them — grep `venv/lib/python3.10/site-packages/diffusers/pipelines/ltx/pipeline_ltx_condition.py`
   for `decode_timestep`. If present, add `decode_timestep=0.05, decode_noise_scale=0.025` to
   the LTXBackend `pipe(...)` generation call. If a config check shows the loaded VAE is NOT
   timestep-conditioned (0.9.0 fallback), omit them (gate on `repo != base`).
4. The scheduler "color-mush" monkeypatch (in `LTXBackend.load`, the timestep/mu fix around
   `director.py:198-231` — read it first): keep it applied by default, but wrap in
   `if not args.ltx_no_mush_patch:`. 0.9.5 may ship a fixed scheduler config; the flag lets the
   user A/B without a code change.
5. Distilled probe (stretch, decision #2): with `venv/bin/python` + `huggingface_hub`, list
   `Lightricks/LTX-Video` repo files and any `LTX-Video-0.9.8*` repos for a **2B** distilled
   safetensors (e.g. `ltxv-2b-0.9.8-distilled*.safetensors`). If one exists: add
   `--ltx_variant distilled` that loads it via
   `LTXVideoTransformer3DModel.from_single_file(<url-or-path>, torch_dtype=torch.bfloat16)`
   passed as `transformer=` into the 0.9.5 pipeline, forces `guidance_scale=1.0` and steps<=8,
   and DISABLES the mush patch for that variant. If none exists: write your findings into the
   thread summary and skip — do not improvise.

**What NOT to change:** Wan/WanBackend paths; the `[[MARKER]]` protocol; studio.py (no UI for
this — the default change is under the hood); the latent-chain hooks (`director.py:232-288`);
anything in enhance.py.

**Pitfalls:**
- `LTXConditionPipeline.from_pretrained(..., text_encoder=..., tokenizer=...)` with explicit
  components skips those subfolders — that is the point; do not also pass `variant=`.
- The 0.9.5 repo has no single-file at top level; it is a normal diffusers layout (verified).
- Disk: transformer+vae ≈ 6-8GB download. Check free space first (`df -h /home/wolve`).
- Sequential CPU offload (`enable_sequential_cpu_offload`) must remain AFTER any component swap.
- Do not touch `dims()/to_frames()` — frame math is contract-locked with studio.py `_plan()`.

**Tests / acceptance:**
- `py_compile` all touched files.
- CPU-only: `venv/bin/python -c "import director"`-style import must not regress (director.py
  guards heavy work behind `main()`; verify).
- GPU (ONLY with the user's go-ahead): one fixed-seed 2-shot chained run at 704x480 via
  `director.py --prompt "a red fox trotting in fresh snow, photoreal" --total 4 --seg 2 --steps 30 --cfg 3 --seed 42 --backend ltx --latent_chain --out outputs/q1_095.mp4 --frames_dir outputs/q1_095_frames`
  once with `--ltx_repo Lightricks/LTX-Video` (baseline) and once with the 0.9.5 default.
  Acceptance: both complete; no color-mush (frames are not a shifting color field); the 0.9.5
  run's mp4 + frames saved for the user's visual A/B. Report VRAM peaks from the `[[VRAM]]`
  marker lines.

---

## Q3 — Measurement floor (telemetry markers + pair harness + noise-floor probe)

**Goal:** The drift/seam/token signals the pipeline already computes (or can compute in O(1))
are emitted as stdout markers, parsed into the Job, persisted, and exported per-run to
`experiments.jsonl`; the studio gains a PAIR A/B action (clone-with-one-dial-changed, shared
seed), a REPLICATE ×N action (seed noise floor), and a minimal blind pair-rating modal.

**Fix shape:**

1. `director.py` — markers:
   - Refactor `_seam_static` (`director.py:428-436`) into `_seam_mse(a, b) -> float` returning
     the MSE; `_seam_static(a,b)` becomes `return _seam_mse(a,b) < SEAM_STATIC_MSE` (call sites
     unchanged).
   - After EVERY completed shot (both the first-segment block ending near `director.py:668` and
     the continuation block near `director.py:713` — anchor on the `write_checkpoint` calls),
     emit:
     - `[[SEAMMSE <seg> <int(mse*100)>]]` — `_seam_mse(new_first_frame, prev_last_frame)` across
       the seam just created (continuations only).
     - `[[DRIFT <seg> <int(pre*100)> <int(post*100)>]]` — luminance-MSE (reuse `_seam_mse`) of
       the shot's LAST frame vs the ANCHOR frame (shot 1's last frame, captured once), measured
       pre- and post- color-match. This is the drift curve.
   - Tokens: after each shot's prompt is finalized (the redirect / prompt-set point in the loop),
     emit `[[TOKENS <seg> <n>]]` where `n = len(pipe.tokenizer(prompt).input_ids)` for LTX
     (`backend.pipe.tokenizer`) — wrap in try/except and skip silently for Wan if its tokenizer
     attribute differs (check `WanVACEPipeline` for `.tokenizer` first; if present, emit there
     too).
2. `studio_core.py` — parse + persist:
   - Add next to `_VRAM` (`studio_core.py:20`):
     `_SEAMMSE`, `_DRIFT`, `_TOKENS` regexes (mirror the space-separated int-groups style).
   - `Job.__init__`: `self.seam_mse = []`, `self.drift = []`, `self.tok_counts = []`
     (lists of `[seg, ...ints]`). Append on parse in the `_run` loop (anchor: the `vm = _VRAM...`
     block). Add `"seam_mse", "drift", "tok_counts"` to `_FIELDS` (`studio_core.py:21-25`).
3. `experiment_log.py` — `build_record` (`experiment_log.py:43`): add
   `"seam_mse": list(getattr(job, "seam_mse", []) or [])`, same for `drift`, `tok_counts`; add
   `"pair_id": p.get("pair_id")`, `"pair_variant": p.get("pair_variant")`,
   `"replicate_set_id": p.get("replicate_set_id")` near `parent_id`.
4. `studio.py` — harness UI (model on the existing RE-ROLL flow):
   - Archive buttons row (`studio.py:1301`): add `Button("⇄ PAIR A/B", id="pairbtn")` and
     `Button("×N REPLICATE", id="replbtn")`.
   - `pairbtn` handler (model on the rerollbtn handler at `studio.py:1961-1990`): push a small
     ModalScreen (clone RerollScreen's shape, `studio.py:1090`) with two Inputs: dial name
     (must be one of: steps, cfg, res, seg, cond_strength, steadiness, backend, fps) and new
     value. On confirm: `cfg = self._clone_config(job)`; KEEP the seed; set
     `cfg[<dial>] = <value>`; `self._apply_config(cfg)`; queue via `self._queue_current_run()`;
     then set `j.params["pair_id"] = job.id; j.params["pair_variant"] = "B"; j.save()` and also
     back-annotate the source if unset (`job.params.setdefault("pair_id", job.id)`,
     `pair_variant = "A"`, `job.save()`). Guard enhance runs out (copy the reroll guard).
   - `replbtn` handler: one Input (N, default 3, clamp 2-5). Enqueue N re-rolls (random seeds —
     reuse the reroll internals) each with `params["replicate_set_id"] = job.id`.
   - Blind rating: add a `⚖ RATE PAIR` button in the same row. Handler: if the selected job has
     a `pair_id`, find its partner in `self.mgr.jobs`; push a modal showing ONLY the two output
     paths labeled "1" and "2" in RANDOM order (`random.random()`), buttons `1 better / 2 better
     / tie`; on pick append `{"pair_id":..., "winner": "<jobid or tie>", "ts": time.time()}` to
     `runs/pair_ratings.jsonl`. No video playback in-TUI — the user plays the files themselves;
     the modal exists to record the verdict blind.

**What NOT to change:** the meaning/format of any EXISTING marker; `_seam_static`'s
threshold/semantics for the HOLD skip; the reroll flow itself; `experiment_log.SCHEMA` (additive
fields only — do not bump).

**Pitfalls:**
- Marker parse loop: follow the existing pattern EXACTLY (search `vm = _VRAM.search(line)` in
  `studio_core._run`) — set `transition = True` only for SEAMMSE/DRIFT (they're per-shot), not
  TOKENS (noise).
- `job.tail` filtering: pure-marker lines (`line.startswith("[[")`) are already excluded from
  the raw-terminal tail — your new markers inherit that for free; don't re-add them.
- Old persisted jobs lack the new fields — `Job.load` setattr-if-present already tolerates
  this; ensure `getattr(..., []) or []` guards everywhere you read them.
- The drift anchor frame must survive `--resume` (checkpoint reload): store nothing new in
  state.json; recompute the anchor as `video[seg_frames-1]` (shot 1's last frame) from the
  reloaded frame list.
- studio.py is ~2300 lines; anchor every edit on the grep strings given, not on line numbers
  alone.

**Tests / acceptance:**
- `py_compile` all four files.
- CPU test (Write a script, run with `venv/bin/python`): instantiate `studio_core.Job`, feed
  synthetic `[[SEAMMSE 2 340]]` / `[[DRIFT 2 120 45]]` / `[[TOKENS 2 96]]` lines through the
  regexes, assert fields land; `experiment_log.build_record` includes the three arrays + pair
  fields; save/load roundtrip preserves them.
- TUI smoke (no GPU): `venv/bin/python -c` Textual pilot `run_test` opening the app, assert the
  new buttons exist (the repo has precedent for pilot tests; if flaky under WSL, a manual
  checklist in the summary is acceptable).
- GPU (user go-ahead): one 3-shot wan-turbo run; confirm `[[DRIFT]]`/`[[SEAMMSE]]` lines in the
  run log and arrays in the new experiments.jsonl row.

---

## Q2 — Drift-kill anchor pack (latent AdaIN + latent seam fuse + quantile palette lock)

**Goal:** Three flag-gated anti-drift mechanisms that anchor to SHOT 1 instead of the previous
tail: (a) AdaIN-normalize each new shot's latents toward shot-1 latents (`--latent_adain F`),
(b) blend the first K latent frames of each continuation toward the carried tail latents
(`--latent_fuse`), (c) replace per-channel mean color-match with 256-bin CDF quantile matching
against a shot-1 pixel pool (`--palette_lock S`). All default OFF (decision #3); OFF must be
bit-identical to current behavior.

**Reference implementations already on disk (READ THESE FIRST):**
- `venv/lib/python3.10/site-packages/diffusers/pipelines/ltx/pipeline_ltx_i2v_long_multi_prompt.py:145`
  `adain_normalize_latents(...)` — copy its exact normalization math (per-channel mean/std in
  latent space, factor-blended).
- same file `:212` — `linear_overlap_fuse(prev, new, overlap)` — the seam-fuse blend.
- `pipeline_ltx_latent_upsample.py:94` — `adain_filter_latent` (simpler variant; compare).
Import from diffusers if the functions are importable as-is; otherwise vendor a copy into
director.py with a comment naming the source file/line. Do NOT invent different math.

**Fix shape** — `director.py`:

1. Flags (next to `--latent_chain`, `director.py:464`): `--latent_adain` (float, default 0.0),
   `--latent_fuse` (store_true), `--palette_lock` (float, default 0.0),
   `--palette_lock_evolve` (store_true — palette lock applies to steadiness=evolve only when
   this is set; hold/balanced get it whenever `--palette_lock > 0`).
2. **Latent AdaIN + fuse** live inside the EXISTING latent-chain hooks
   (`director.py:232-288` — the `_lat_inj` / `_lat_stash` / `_carry` machinery; read the whole
   block first). Mechanics:
   - Capture the anchor once: the FIRST shot's stashed latents → `self._anchor_lat` (detached
     clone, keep on CPU to spare VRAM; move/cast at use).
   - Each subsequent shot, at the point where `_lat_stash["lat"]` is captured (during decode,
     `director.py:257-260`): if `args.latent_adain > 0`, apply
     `lat = adain_normalize_latents(lat, anchor, factor=args.latent_adain)` BEFORE both the
     decode-path use and the `_carry` assignment — drift correction must propagate to what the
     next shot is conditioned on.
   - If `args.latent_fuse` and this is a continuation: blend the leading K latent frames of the
     new shot's latents toward the carried tail using `linear_overlap_fuse` semantics, where
     K = the latent-frame overlap. NOTE the 8x temporal compression: pixel `--overlap` of 9
     ≈ 1-2 latent frames. If the run's overlap yields <2 latent frames, print a one-line
     warning (`latent_fuse: overlap too small (<2 latent frames), no-op — use --overlap 17+`)
     and skip. Both apply ONLY under `--latent_chain` (they're latent-space ops); if flags are
     set without `--latent_chain`, print a warning and ignore.
3. **Palette lock** (pixel-space, works for BOTH backends):
   - New function in director.py (or `palette.py` if >80 lines):
     ```python
     def palette_lock(frames, pool, strength):
         """256-bin per-channel CDF (quantile) match of each frame toward the anchor pixel
         pool. frames: list[PIL.Image]; pool: np.uint8 (N,3); strength 0..1 lerps between
         identity and fully-matched. Returns a new list; len/size unchanged."""
     ```
     Implementation: per channel, `np.interp` from each frame's CDF to the pool's inverse CDF
     (classic histogram matching), then `out = frame*(1-s) + matched*s`, uint8 round-trip,
     vectorized (no per-pixel python loops).
   - Anchor pool: after shot 1 completes, sample ~200k pixels uniformly from its frames
     (downscale frames to ~128px first). Optional decay: after each ACCEPTED shot, refresh 10%
     of the pool from the new shot (keeps slow scene evolution legal; hold this constant —
     10%, not a flag).
   - Wiring: at the three `color_match(...)` call sites (`director.py:705`, `:709`, `:717`):
     when `args.palette_lock > 0` (and steadiness gate per flag above), call
     `palette_lock(frames, pool, args.palette_lock)` INSTEAD of `color_match(...)`; else keep
     `color_match` exactly as-is.
4. Emit `[[DRIFT ...]]` (Q3's marker) with the post- value computed AFTER whichever correction
   ran, so the telemetry directly scores this item. (Q3 lands first; just make sure the
   measurement point sits after the palette/adain application.)

**What NOT to change:** `color_match` itself (it stays the default); the conditioning/mask
construction; overlap/frame math; Wan turbo LoRA logic; checkpoint schema (the anchor pool is
recomputed from frames on resume, same rule as Q3's anchor frame).

**Pitfalls:**
- `_lat_stash`/`_carry` interplay: the carry is consumed by the NEXT shot's conditioning hook
  (`director.py:248-256, 279-288`). Apply AdaIN before the stash is copied into `_carry`, or
  the correction never propagates. Read the whole hook block before editing; it is the
  subtlest code in the file.
- dtype/device: latents are bf16 on CUDA at stash time; the anchor lives on CPU — cast/move at
  use (`anchor.to(lat.device, lat.dtype)`), never permanently move the stash.
- The mu-shifted scheduler means latent scales differ early vs late in denoise — only touch
  latents at the stash/decode point (fully denoised), never mid-step.
- PIL/numpy round-trip in `palette_lock` must preserve exact frame count/size — `write_checkpoint`
  validates count.
- `strength` semantics: 0.0 must return the input list UNTOUCHED (identity object is fine) so
  the OFF path is bit-identical.
- EVOLVE mode wants drift — that is why the gate exists. Respect it.

**Tests / acceptance:**
- `py_compile`; OFF-path identity test (CPU): random PIL frames → `palette_lock(frames, pool, 0.0)`
  returns pixel-identical frames; with strength 1.0 the per-channel histograms of the output
  approach the pool's (assert wasserstein/CDF distance shrinks by >5x).
- AdaIN unit test (CPU): synthetic latent tensors — factor 0 = identity; factor 1 matches
  anchor per-channel mean/std to <1e-3.
- Offline drift replay (CPU, no GPU): script that loads two saved runs' frame dirs from
  `outputs/*_frames`, simulates chaining with (a) current `color_match`-to-previous vs (b)
  `palette_lock`-to-anchor at 0.5/1.0, plots per-channel mean drift vs shot index to a PNG in
  `outputs/`. Include the PNG path in the summary.
- GPU (user go-ahead): fixed-seed 4-shot LTX `--latent_chain` A/B: baseline vs
  `--latent_adain 0.5 --palette_lock 0.7`. Compare the `[[DRIFT]]` curves (Q3 telemetry) —
  acceptance = post-correction drift flat-or-shrinking instead of monotonically growing.

---

## Execution order

Q1 → Q3 → Q2, strictly sequential (all touch `director.py`; Q2's acceptance depends on Q3's
telemetry; Q3/Q2 should be tuned against Q1's checkpoint, not 0.9.0). One agent per phase; the
user reviews `git diff` + commits between phases. GPU validation happens at each phase end,
gated on the user granting a GPU window.

## Out of scope (deferred, do not touch)

Q4-Q10 of the roadmap (CFG surgery, VACE reference_images, VLM closed loop, NAG, RIFE morph
seams, feathered tiles, correlated noise); the dark horses (STG/SLG, Wan2.2 5B, SeedVR2); the
RATE screen / analysis charts (T10 slices 2-3); any 13B LTX checkpoint; enhance.py; Wan backend
changes of any kind.
