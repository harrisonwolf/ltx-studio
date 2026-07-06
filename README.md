# LTX Studio

A local, offline AI-video studio that runs modern video-diffusion models on a **single 8 GB laptop GPU** — driven entirely from a terminal UI. No cloud, no API keys, open weights only.

LTX Studio wraps [LTX-Video](https://github.com/Lightricks/LTX-Video) and [Wan](https://github.com/Wan-Video) diffusion backends in a Pip-Boy–styled [Textual](https://textual.textualize.io/) TUI, and adds the layer those research repos leave out: a job queue, live previews, calibrated time estimates, a blind A/B harness, and a per-run experiment log so quality changes are *measured*, not guessed at.

It was built under a hard constraint — an RTX 5070 Laptop (Blackwell, `sm_120`) with 8 GB of VRAM *shared with the Windows desktop* — and most of the interesting engineering falls out of taking that constraint seriously.

> **Status:** a working personal tool, iterated over many sessions. Single author. This repo is the studio and its orchestration; the model weights and the diffusion research code are external dependencies (see [Running it](#running-it)).

---

## What it looks like

**Job control** — the NEW RUN screen, with the self-calibrating READOUT meters (VRAM headroom, clip budget, time estimate, predicted quality, drift risk):

![NEW RUN screen with READOUT meters](media/tui-job-control.png)

**A live render** — the worker subprocess streaming progress and a last-frame preview into the LIVE tab, with per-phase timing (load → warm → gen → decode → save):

![LIVE render view with last-frame preview](media/tui-live-render.png)

**The archive** — every run is a first-class record: status, per-run timing, favorites, and a lineage panel (here: a replicate source and its enhanced output) so provenance survives re-rolls, replicates, and enhancement passes. REVEAL / RATE PAIR / PAIR A/B are the blind-comparison harness:

![ARCHIVE view with run lineage panel and blind A/B controls](media/tui-archive.png)

**Sample output** — generated locally on the 8 GB laptop GPU:

| | |
|---|---|
| ![Fireflies drifting over a glowing forest stream](media/job_105358-5.gif) | ![Bioluminescent forest at night](media/job_232906-2.gif) |

*Left: fireflies over a glowing forest stream. Right: "bioluminescent forest at night — glowing moths, luminescent moss, a stream lit from within" (the run shown in the LIVE screenshot above). Full-quality clips: [job_105358-5.mp4](media/job_105358-5.mp4) · [job_232906-2.mp4](media/job_232906-2.mp4).*

![A corgi standing on a beach at golden hour, backlit by the sunset](media/corgi_redo-2.gif)

*A corgi on the beach at golden hour — generated, upscaled ×4, and RIFE-interpolated end to end in about 15 minutes on the same card. Full clip: [corgi_redo-2.mp4](media/corgi_redo-2.mp4).*

---

## Why it looks the way it does

Three principles drove almost every design decision.

**1. The UI process never touches CUDA.**
The Textual app (`studio.py`) does not import `torch`. Generation runs in a *separate* Python subprocess with its own CUDA context; the two communicate over a tiny one-way text protocol on stdout. This means a driver OOM or a CUDA segfault kills the worker, not the UI — the studio stays responsive, reports the failure, and lets you re-queue. It also means the 4,000-line UI stays testable without a GPU in the loop.

**2. Measure, don't guess.**
Every run appends a structured record to `runs/experiments.jsonl` — config, per-phase wall-clock, peak VRAM, and quality telemetry (seam MSE across shot boundaries, motion drift, token counts). The time-estimate model and the READOUT gauges *calibrate themselves from that log*. When you want to know whether a change actually helped, there's a **blind A/B harness** with a double coin-flip (it randomizes both the on-screen label *and* the render order) and a reveal gate, so you rate output without knowing which variant you're looking at.

**3. New behavior ships as an opt-in toggle, defaulting to the old output.**
A quality lever that silently changes results poisons every future comparison. So new features (drift anchors, context windows, distilled variants) are dials that default to the previous behavior — byte-identical output unless you opt in. Experiments stay honest across versions.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  studio.py          Textual TUI  ·  never imports torch          │
│  (~4,000 lines)     NEW RUN · QUEUE · blind A/B · READOUT meters  │
└─────────────────┬──────────────────────────────────────────────┘
                  │  spawns a worker subprocess, reads its stdout
                  │  parses  [[MARKER]]  lines   ◄── one-way protocol
┌─────────────────┴──────────────────────────────────────────────┐
│  studio_core.py     JobManager  ·  process lifecycle, marker     │
│  (~500 lines)       parsing, phase-timing provenance, run JSON   │
└─────────────────┬──────────────────────────────────────────────┘
                  │  argv  →  python director.py …  (own CUDA context)
┌─────────────────┴──────────────────────────────────────────────┐
│  director.py        Generation engine  ·  multi-shot chaining,   │
│  run_ltx.py         LTX / Wan backends, drift anchors, context   │
│  (~1,200 lines)     windows, preview + telemetry emission        │
└────────────────────────────────────────────────────────────────┘
```

### The marker protocol

The worker prints progress as line-oriented markers; `studio_core` parses them with regexes and updates job state. That's the entire coupling between the two processes — no shared memory, no RPC, no `torch` in the UI.

```
[[PHASE generating]]      ← phase boundary  → drives provenance + the progress budget
[[SEG 2/4]]               ← shot 2 of 4 started
[[STEP 12/20]]            ← denoising step within the current shot
[[PREVIEW /path.png]]     ← a fresh preview frame is on disk
[[VRAM 7.1]]              ← peak GB this phase
[[SEAMMSE 0.0043]]        ← boundary discontinuity between chained shots
[[DRIFT 0.11]]            ← accumulated motion drift vs. the anchor frame
[[CKPT 1]]               ← a resumable checkpoint was written
```

Because phase boundaries are explicit, the studio accumulates real per-phase timings (`load` / `warmup` / `generating` / `decoding` / `saving`) per run. Those feed two things: a **wall-time progress bar** that knows decoding is slow and weights the bar accordingly, and the **self-calibrating ETA** that reads back the experiment log.

---

## Feature tour

- **Pip-Boy TUI** — NEW RUN form, live QUEUE, and a persistent right-hand rail with field schematics and global READOUT meters. Responsive layout that restacks below 76 columns.
- **Blind A/B** — queue two variants of one config, rate them blind, reveal after. Ratings and pairings are logged to `runs/pair_*.jsonl`.
- **Live preview** — the worker decodes a preview frame mid-generation; the UI refreshes it on a wall-clock cadence so you can bail on a bad seed early.
- **READOUT gauges** — five meters (speed, VRAM headroom, seam quality, drift, throughput) that auto-refit their scales from your own run history.
- **Field schematics** — every dial has a BF6-style ASCII block-art tooltip explaining what it does and its safe range, width-fitted so nothing wraps raggedly.
- **Style presets** — named bundles of anchor words (`Cinematic`, `Golden Hour`, `Noir`, …) that append into the prompt, stackable and user-extensible via JSON.
- **Multi-shot director** — chains shots into longer clips with latent anchoring (AdaIN + palette lock) to fight drift, plus context windows so long clips don't OOM.
- **Distilled few-step backends** — LTX 0.9.5 / 0.9.8-distilled and a 4-step Wan-turbo DMD path, with the step/CFG clamps surfaced rather than hidden.
- **Inspect & clone from the queue** — read-only provenance and one-click re-queue of any run's exact config.

---

## Repo map

| File | Role |
|------|------|
| `studio.py` | The Textual TUI — forms, queue, blind A/B, readout, layout. The centerpiece. |
| `studio_core.py` | `JobManager`: subprocess lifecycle, `[[MARKER]]` parsing, phase-timing provenance, per-run JSON. |
| `director.py` | Multi-shot generation engine: LTX/Wan backends, drift anchors, context windows, telemetry emission. |
| `run_ltx.py` | Single-clip LTX runner (the simple path). |
| `experiment_log.py` | Appends structured run records to `runs/experiments.jsonl` — the measurement backbone. |
| `readout.py` | The five self-calibrating READOUT gauges. |
| `field_visuals.py` | ASCII block-art schematics for every form field. |
| `style_presets.py` | Named anchor-word bundles for the STYLE dropdown. |
| `gpu_budget.py` | VRAM budgeting helpers for the 8 GB envelope. |
| `ltx_preview.py` | Mid-generation preview-frame decode/save. |
| `dials_help.py` | Help text for the dials. |
| `vlm_director*.py`, `vlm_planner.py` | Optional Qwen-VL sidecars for auto-prompting / shot planning. |
| `_q2tests/`, `_t22tests/` | CPU-only test harnesses (drift replay, hold-stress, readout units). |
| `_spikes/` | Research spikes (e.g. a Wan 2.2-5B smoke test) — kept as a record of what was tried. |

The launch scripts (`studio.sh`, `ltx.sh`, `ltx-studio.sh`) wire up the Python env and drop you into the TUI.

---

## Running it

LTX Studio orchestrates open-weights models it does **not** vendor. You supply:

- A Python 3.10 environment with the deps in `requirements.txt`.
- On Blackwell / RTX 50-series, a CUDA 12.8 build of PyTorch:
  `pip install torch --index-url https://download.pytorch.org/whl/cu128`
- The model weights (LTX-Video 2B, optionally Wan 2.1-VACE-1.3B), fetched on first run via `huggingface_hub` into a local cache.

Then:

```bash
./studio.sh          # launch the full studio TUI
```

Designed for WSL2 on Windows with an 8 GB GPU, but nothing is Windows-specific — it's a terminal app and a subprocess.

---

## Testing

The test harnesses are CPU-only by design — they exercise the parsing, telemetry, layout-fitting, and readout math without touching CUDA:

```bash
python _t22tests/test_readout.py     # readout gauge math + auto-refit
python _q2tests/test_units.py        # drift / seam telemetry units
```

The strict separation between the UI process and the CUDA worker is what makes this possible: the interesting logic lives on the testable side of the process boundary.

---

## License

MIT — see [LICENSE](LICENSE).
