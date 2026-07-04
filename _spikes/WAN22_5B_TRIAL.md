# Wan 2.2 TI2V-5B — trial prep (2026-07-04)

**Status: CONDITIONAL go.** Everything is staged; the trial is two commands away. But it *will* contend for the shared 8 GB GPU and lean on swap — run it only when you have a free GPU window.

## The honest constraints
- **No diffusers bump needed** — 0.38.0 already has the Wan i2v pipeline. Stay on the current pin.
- **RAM is the real risk.** Sequential offload pushes ~10 GB into system RAM. Your box is **32 GB physical**, WSL at **26 GB** — you *cannot* set WSL to 32 GB (that's the whole machine; Windows would starve). Sane max ≈ **28 GB**. Past ~26 GB used it spills to the 48 GB swap (absorbs it, but slowly).
- **Speed:** expect **~15–30 min for a 2 s 480p clip**. This is a *fidelity* option, not a speed one — LTX / wan-turbo stay the fast paths.
- **Quality caveat:** diffusers#12034 — 5B i2v can look *softer* in diffusers than the official Space until it's patched.

## To run it (only with a free GPU window + your go)
1. *(optional)* Bump WSL RAM to 28 GB: edit `C:\Users\wolve\.wslconfig` → `memory=28GB`, then `wsl --shutdown` (**this kills all running WSL/GPU jobs**) and reopen.
2. **Download weights** (~10–11 GB, one time, network only — no GPU):
   ```
   cd /home/wolve/video_gen/FramePack && venv/bin/huggingface-cli download Wan-AI/Wan2.2-TI2V-5B-Diffusers
   ```
3. **Run the smoke test** with any start image:
   ```
   cd /home/wolve/video_gen/FramePack && venv/bin/python _spikes/wan22_5b_smoketest.py --image <path-to-start.png>
   ```
   Defaults: 832×480, 49 frames (~2 s), 30 steps. It prints `load` vs `denoise+decode` vs `total` wall-clock at the end — **that number is your go/no-go.**

## Reading the result
- **OOM-kills the WSL VM** → 5B isn't viable on this box; stop here.
- **Finishes but total > ~20 min for 2 s** → hero-shot-only option, not for chained long clips.
- **Quality clears the softness concern AND wall-clock is tolerable** → then wire a `--backend wan22-5b` sibling to WanBackend (i2v via `image=` / `last_image=`, ~80% code reuse) as an opt-in fidelity backend.

_I won't run anything GPU/network for this without your explicit word. The weight download (step 2) is network-only and safe to background anytime you say "download it."_
