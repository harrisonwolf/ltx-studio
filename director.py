#!/usr/bin/env python
"""Director v2 - chain short video segments into a long clip on 8GB.
Each segment continues from the previous one's TAIL (motion-preserving conditioning), with
per-segment color-matching to fight drift. The generation backend is pluggable:
  --backend ltx  (default) LTX-Video 2B via LTXConditionPipeline (tail = LTXVideoCondition)
  --backend wan  Wan 2.1-VACE-1.3B via WanVACEPipeline (tail = leading-clip keep/generate mask)
Both backends emit the SAME stdout markers + outputs, so studio_core/studio.py stay backend-agnostic.

  python director.py --prompt "..." --total 12 --seg 3 [--image start.png] [--backend wan]
"""
import argparse, os, gc, json, signal, sys, glob, re, types
import numpy as np
import torch
from gpu_budget import cap_vram
from PIL import Image
from diffusers import LTXConditionPipeline
from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
from diffusers.utils import export_to_video, load_image
import ltx_preview


# --- suspend signal ---
_SUSPEND = False


def _on_suspend(signum, frame):
    global _SUSPEND
    _SUSPEND = True


def _ascii1(s, n=200):
    """One ASCII line, no ']]', truncated -> safe inside a [[...]] marker."""
    s = " ".join(str(s).split())
    return s.encode("ascii", "ignore").decode().replace("]]", ") ")[:n]


def _write_preview(path, video):
    """Save a small (~160px) live-preview PNG of the latest frame. Never raises -> never kills a run."""
    if not path or not video:
        return
    try:
        im = video[-1]
        w, h = im.size
        ltx_preview.atomic_save_png(im.resize((160, max(1, round(160 * h / w)))), path)
    except Exception:
        pass


def color_match(frames, ref_img):
    """Match each frame's per-channel mean/std to ref_img (the prior tail) -> fights drift."""
    ref = np.asarray(ref_img).astype(np.float32)
    rmean, rstd = ref.mean((0, 1)), ref.std((0, 1)) + 1e-5
    out = []
    for f in frames:
        a = np.asarray(f).astype(np.float32)
        a = (a - a.mean((0, 1))) / (a.std((0, 1)) + 1e-5) * rstd + rmean
        out.append(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    return out


# ============================ #2 Q2: drift-kill anchors ============================
# Three flag-gated anti-drift mechanisms that anchor to SHOT 1 instead of the previous tail. All
# default OFF; the OFF path is bit-identical to the pre-Q2 behavior.
#   adain_normalize_latents / linear_overlap_fuse: VENDORED VERBATIM (math unchanged) from
#     venv/lib/python3.10/site-packages/diffusers/pipelines/ltx/pipeline_ltx_i2v_long_multi_prompt.py
#     (adain_normalize_latents :145, linear_overlap_fuse :212). Do NOT "improve" this math -- the plan
#     mandates the upstream formulas so the anchor pack matches diffusers' own long-video pipeline.

def adain_normalize_latents(curr_latents, ref_latents, factor):
    """Channel-wise mean/variance matching of curr_latents to ref_latents, blended by factor in [0,1]
    (0 keeps current stats; 1 matches reference). Stats over (T,H,W); shapes may differ in T."""
    if ref_latents is None or factor is None or factor <= 0:
        return curr_latents
    eps = torch.tensor(1e-6, device=curr_latents.device, dtype=curr_latents.dtype)
    mu_curr = curr_latents.mean(dim=(2, 3, 4), keepdim=True)
    sigma_curr = curr_latents.std(dim=(2, 3, 4), keepdim=True)
    mu_ref = ref_latents.mean(dim=(2, 3, 4), keepdim=True).to(device=curr_latents.device, dtype=curr_latents.dtype)
    sigma_ref = ref_latents.std(dim=(2, 3, 4), keepdim=True).to(device=curr_latents.device, dtype=curr_latents.dtype)
    mu_blend = (1.0 - float(factor)) * mu_curr + float(factor) * mu_ref
    sigma_blend = (1.0 - float(factor)) * sigma_curr + float(factor) * sigma_ref
    sigma_blend = torch.clamp(sigma_blend, min=float(eps))
    curr_norm = (curr_latents - mu_curr) / (sigma_curr + eps)
    return curr_norm * sigma_blend + mu_blend


def linear_overlap_fuse(prev, new, overlap):
    """Temporal linear crossfade between two latent clips [B,C,F,H,W] over the overlap region.
    Returns [B,C, F_prev + F_new - overlap, H,W]. overlap<=1 concatenates without a blend."""
    if overlap <= 1:
        return torch.cat([prev, new], dim=2)
    alpha = torch.linspace(1, 0, overlap + 2, device=prev.device, dtype=prev.dtype)[1:-1]
    shape = [1] * prev.ndim
    shape[2] = alpha.size(0)
    alpha = alpha.reshape(shape)
    blended = alpha * prev[:, :, -overlap:] + (1 - alpha) * new[:, :, :overlap]
    return torch.cat([prev[:, :, :-overlap], blended, new[:, :, overlap:]], dim=2)


def _sample_pixels(frames, n, size=128, rng=None):
    """Uniformly sample up to n RGB pixels from frames, each downscaled so its long side ~= size.
    Vectorized (np.asarray per frame); returns uint8 (M,3) with M <= n."""
    rng = rng if rng is not None else np.random.default_rng(0)
    pools = []
    for im in frames:
        w, h = im.size
        sc = size / max(1, max(w, h))
        sm = im.convert("RGB").resize((max(1, round(w * sc)), max(1, round(h * sc))))
        pools.append(np.asarray(sm, dtype=np.uint8).reshape(-1, 3))
    allpix = np.concatenate(pools, axis=0) if pools else np.zeros((0, 3), np.uint8)
    if allpix.shape[0] > n:
        allpix = allpix[rng.choice(allpix.shape[0], n, replace=False)]
    return allpix


def build_palette_pool(frames, seed=0, n=200000, size=128):
    """The palette-lock anchor pool: ~n pixels sampled uniformly from SHOT-1 frames (downscaled ~128px)."""
    return _sample_pixels(frames, n, size, np.random.default_rng(seed))


def refresh_palette_pool(pool, new_frames, seed=0, frac=0.10, size=128):
    """Decay: replace ~frac (10%) of the pool with pixels from a newly ACCEPTED shot so slow, legitimate
    scene evolution stays legal. Pool size is preserved. Constant 10% by design (not a flag)."""
    if pool is None or pool.shape[0] == 0 or not new_frames:
        return pool
    rng = np.random.default_rng(seed)
    k = max(1, int(pool.shape[0] * frac))
    fresh = _sample_pixels(new_frames, k, size, rng)
    if fresh.shape[0] == 0:
        return pool
    k = min(k, fresh.shape[0])
    out = pool.copy()
    out[rng.choice(pool.shape[0], k, replace=False)] = fresh[:k]
    return out


def palette_lock(frames, pool, strength):
    """256-bin per-channel CDF (quantile) match of each frame toward the anchor pixel pool.
    frames: list[PIL.Image]; pool: np.uint8 (N,3); strength 0..1 lerps between identity and
    fully-matched. Returns a new list; len/size unchanged. strength<=0 returns the input UNTOUCHED
    (same list object) so the OFF path is bit-identical."""
    if strength <= 0 or not frames or pool is None or pool.shape[0] == 0:
        return frames
    s = float(max(0.0, min(1.0, strength)))
    # Precompute the pool's per-channel (quantile -> value) template once (classic histogram matching).
    tmpl = []
    for c in range(3):
        vals, counts = np.unique(pool[:, c], return_counts=True)
        q = np.cumsum(counts).astype(np.float64) / pool.shape[0]
        tmpl.append((q, vals.astype(np.float64)))
    out = []
    for im in frames:
        arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
        H, W, _ = arr.shape
        res = np.empty(arr.shape, dtype=np.float64)
        for c in range(3):
            src = arr[:, :, c].reshape(-1)
            s_vals, s_inv, s_counts = np.unique(src, return_inverse=True, return_counts=True)
            s_q = np.cumsum(s_counts).astype(np.float64) / src.size
            mapped = np.interp(s_q, tmpl[c][0], tmpl[c][1])       # frame CDF -> pool inverse CDF (vectorized)
            matched = mapped[s_inv].reshape(H, W)
            res[:, :, c] = arr[:, :, c] * (1.0 - s) + matched * s
        out.append(Image.fromarray(np.clip(np.round(res), 0, 255).astype(np.uint8)))
    return out


def _fold_anchors(prompt, anchors):
    """In chained (non-director) mode the ONE prompt is reused for every shot with no VLM to keep the
    subject/style consistent -> fold the anchors (the 'style leash') straight into that prompt. In
    director mode the VLM folds anchors in per seam, so this is skipped there."""
    a = (anchors or "").strip()
    return (prompt.rstrip(" .,") + ", " + a) if a else prompt


def write_checkpoint(ckpt_dir, seg_idx, video, current_prompt, directive,
                     W, H, seg_frames, target, overlap, est_segs, args):
    """Persist all accumulated frames + state.json atomically (state.json LAST)."""
    frames_dir = os.path.join(ckpt_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    # Incremental: frames are append-only except near the seam (crossfade touches ~overlap frames), so
    # re-encoding EVERY frame each shot is O(n^2) and stalls late shots. Atomic per-frame (tmp+replace)
    # so a mid-write kill can never leave a torn PNG that passes _ckpt_valid's count check.
    rewrite_from = max(0, len(video) - seg_frames - overlap - 1)
    for i, im in enumerate(video):
        fp = os.path.join(frames_dir, f"{i:04d}.png")
        if i < rewrite_from and os.path.exists(fp):
            continue
        tmp = fp + ".tmp"
        im.save(tmp, format="PNG")
        os.replace(tmp, fp)
    state = {
        "schema": 1,
        "seg_idx": int(seg_idx),
        "n_frames": int(len(video)),
        "current_prompt": current_prompt,
        "directive": directive,
        "W": int(W), "H": int(H),
        "seg_frames": int(seg_frames), "target": int(target),
        "overlap": int(overlap), "est_segs": int(est_segs),
        "args": {
            "prompt": args.prompt, "directive": args.directive, "vlm": bool(args.vlm),
            "anchors": args.anchors, "n_prompt": args.n_prompt, "image": args.image,
            "total": args.total, "seg": args.seg, "overlap": args.overlap,
            "fps": args.fps, "width": args.width, "height": args.height,
            "steps": args.steps, "cfg": args.cfg, "seed": args.seed,
            "frames_dir": args.frames_dir, "out": args.out, "steadiness": args.steadiness,
            "backend": args.backend,
        },
    }
    tmp = os.path.join(ckpt_dir, "state.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, os.path.join(ckpt_dir, "state.json"))
    print("[[CKPT %d %d]]" % (seg_idx, len(video)), flush=True)


def load_checkpoint(ckpt_dir, expect_backend=None):
    """Load (video, seg_idx, current_prompt) from a checkpoint. Fails loudly on mismatch.
    A ckpt is bound to the backend that wrote it -- the tail/seam semantics differ, so resuming
    under a different backend is refused rather than silently producing a broken seam."""
    with open(os.path.join(ckpt_dir, "state.json")) as fh:
        state = json.load(fh)
    saved_backend = state.get("args", {}).get("backend", "ltx")
    if expect_backend is not None and saved_backend != expect_backend:
        raise RuntimeError(
            "checkpoint backend mismatch: saved with --backend %s but resuming with --backend %s "
            "(tail/seam semantics differ; resume with the original backend)"
            % (saved_backend, expect_backend))
    frames_dir = os.path.join(ckpt_dir, "frames")
    png_paths = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    video = [load_image(p) for p in png_paths]
    if state["n_frames"] != len(video):
        raise RuntimeError(
            "checkpoint corrupt: state.json n_frames=%d but found %d PNGs in %s"
            % (state["n_frames"], len(video), frames_dir))
    return video, int(state["seg_idx"]), state["current_prompt"]


# ============================ video backends ============================
# Pluggable generation backend. A subclass owns ONLY the backend-specific surfaces: the pipeline,
# the conditioning objects, the per-shot generation call, and the per-step live preview. main()
# owns ALL orchestration (markers, chaining loop, checkpoints, the VLM director, crossfade/
# color-match) and emits identical stdout markers + file outputs regardless of backend, so
# studio_core.py and studio.py never need to know which backend ran.

class VideoBackend:
    name = "base"
    strips_cond_head = False     # True -> gen() returns continuation-only frames (cond tail stripped)

    def __init__(self, args):
        self.args = args

    def dims(self, width, height):
        """Round to a tile-safe (W, H). //32 for both backends keeps the [[SEG]] denominator aligned
        with studio.py's untouchable nseg formula."""
        return (width // 32) * 32, (height // 32) * 32

    def configure(self, W, H, seg_frames, overlap=None):
        self.W, self.H, self.seg_frames = W, H, seg_frames
        self.overlap = overlap            # #2 Q2: pixel overlap -> latent-frame overlap for --latent_fuse

    def fps_for(self, requested):
        return requested

    def to_frames(self, sec, fps):
        raise NotImplementedError

    def load(self):
        raise NotImplementedError

    def first_cond(self, image_path):
        raise NotImplementedError

    def tail_cond(self, tail_frames, strength):
        raise NotImplementedError

    def gen(self, cond, seed, prompt, neg, step_cb, is_continuation):
        raise NotImplementedError

    def preview_step(self, i, cbk, path):
        return


class LTXBackend(VideoBackend):
    """LTX-Video 2B via LTXConditionPipeline. This is the project's original, validated generation
    path moved verbatim into a backend: same checkpoint, same sequential offload, same color-mush
    schedule fix, same #10 latent-space chaining hooks, same conditioning API."""
    name = "ltx"
    strips_cond_head = False

    def __init__(self, args):
        super().__init__(args)
        self._carry = None
        self._lat_inj = {"lat": None}     # previous shot's latents -> condition next shot (None = pixel fallback)
        self._lat_stash = {"lat": None}   # the current shot's own latents, captured during its decode
        self._anchor_lat = None           # #2 Q2: shot-1 latents on CPU (AdaIN reference); captured once
        self._fuse_warned = False         # #2 Q2: latent_fuse overlap-too-small warning fires at most once
        self._resumed = False             # #2 Q2: set on --resume -> shot-1 latent anchor is unrecoverable
        self._adain_resume_warned = False

    def to_frames(self, sec, fps):
        n = int(sec * fps)
        return (n // 8) * 8 + 1

    def load(self):
        args = self.args
        print("[[LOAD 2 5 loading LTX checkpoint (largest part)]]", flush=True)
        base = "Lightricks/LTX-Video"                        # T5 + tokenizer already in the HF cache
        repo = getattr(args, "ltx_repo", None) or "Lightricks/LTX-Video-0.9.5"
        self._distilled = getattr(args, "ltx_variant", None) == "distilled"
        extra = {}
        if self._distilled:
            # 0.9.8-distilled 2B: verified present as a single-file checkpoint in the 0.9.0 base repo
            # (ltxv-2b-0.9.8-distilled.safetensors, 6.3GB bf16). Pass it in as the transformer= override
            # so the pipeline never downloads/builds the repo's own default transformer.
            from diffusers import LTXVideoTransformer3DModel
            from huggingface_hub import hf_hub_url
            url = hf_hub_url(base, "ltxv-2b-0.9.8-distilled.safetensors")
            extra["transformer"] = LTXVideoTransformer3DModel.from_single_file(url, torch_dtype=torch.bfloat16)
        if repo == base:
            pipe = LTXConditionPipeline.from_pretrained(base, torch_dtype=torch.bfloat16, **extra)
        else:
            # T5Tokenizer (slow), NOT T5TokenizerFast: model_index.json in both repos specifies the slow
            # class, which tokenizes via the already-installed `sentencepiece` package directly. The fast
            # class has no precomputed tokenizer.json in either repo's tokenizer/ subfolder, so loading it
            # would trigger transformers' slow->fast conversion, which requires `protobuf` (not installed
            # here, and not otherwise needed by this venv -- verified via the successful base-repo load).
            from transformers import T5EncoderModel, T5Tokenizer
            te = T5EncoderModel.from_pretrained(base, subfolder="text_encoder", torch_dtype=torch.bfloat16)
            tok = T5Tokenizer.from_pretrained(base, subfolder="tokenizer")
            pipe = LTXConditionPipeline.from_pretrained(repo, text_encoder=te, tokenizer=tok,
                                                        torch_dtype=torch.bfloat16, **extra)
        print(f"LTX checkpoint: {repo}" + (" (variant: 0.9.8-distilled 2B)" if self._distilled else ""),
              flush=True)
        # 0.9.5's VAE is timestep-conditioned (verified: vae/config.json timestep_conditioning=true, vs
        # absent/false on the 0.9.0 base) -- only pass the decode kwargs when the loaded VAE supports them.
        self._decode_kwargs = {} if repo == base else {"decode_timestep": 0.05, "decode_noise_scale": 0.025}
        print("[[LOAD 3 5 building pipeline]]", flush=True)
        print("[[PHASE offload]]", flush=True)
        print("[[LOAD 4 5 wiring CPU<->GPU offload (8GB mode)]]", flush=True)
        pipe.enable_sequential_cpu_offload()
        pipe.vae.enable_tiling()
        self.pipe = pipe

        # diffusers BUG FIX (the "color-mush" bug): LTXConditionPipeline builds its timesteps from
        # linear_quadratic_schedule with NO shift, which renders as incoherent color mush on this LTX
        # checkpoint -- while LTXPipeline's linspace sigmas + dynamic-shift (mu) schedule renders correctly
        # (VERIFIED: same prompt/model/seed, condition pipe = mush, base pipe = clean; still true on 0.38).
        # Force the condition pipe onto the base pipe's schedule: compute mu via calculate_shift on the
        # latent token count using the diffusers DEFAULT shift constants (a deliberately tuned override,
        # validated to render clean -- NOT read from this checkpoint's scheduler_config, which differs) and
        # override set_timesteps to use linspace sigmas + that mu.
        # --ltx_no_mush_patch lets the user A/B against 0.9.5's own scheduler config without a code change;
        # the distilled variant always disables it (few-step CFG-distilled sampling uses its own schedule).
        if not args.ltx_no_mush_patch and not self._distilled:
            def _calc_shift(seq, base=256, maxs=4096, bs=0.5, ms=1.15):
                m = (ms - bs) / (maxs - base)
                return seq * m + (bs - m * base)
            _lnf = (self.seg_frames - 1) // pipe.vae_temporal_compression_ratio + 1
            _seq = _lnf * (self.H // pipe.vae_spatial_compression_ratio) * (self.W // pipe.vae_spatial_compression_ratio)
            self._MU = _calc_shift(_seq)
            _orig_set_timesteps = pipe.scheduler.set_timesteps
            _MU = self._MU
            _steps = args.steps

            def _set_timesteps_fixed(sched_self, num_inference_steps=None, device=None, sigmas=None,
                                     timesteps=None, mu=None, **kw):
                n = num_inference_steps if num_inference_steps is not None else (
                    len(timesteps) if timesteps is not None else _steps)
                sig = np.linspace(1.0, 1.0 / n, n)
                return _orig_set_timesteps(num_inference_steps=n, device=device, sigmas=sig, mu=_MU)
            pipe.scheduler.set_timesteps = types.MethodType(_set_timesteps_fixed, pipe.scheduler)
            print("note: forced LTX base-pipeline schedule on condition pipe (linspace + mu=%.3f)" % self._MU)

        # --- #10 Phase 1: latent-space chaining (opt-in --latent_chain) ---
        # Carry each shot's LATENTS forward as the next shot's condition instead of decoding to pixels and
        # re-encoding the tail (a lossy VAE round-trip that compounds blur over a long clip). Two hooks:
        # _denormalize_latents is patched to STASH each shot's unpacked NORMALIZED latents (captured for free
        # during the shot's own decode); retrieve_latents is patched so the next shot's conditioning uses
        # those latents, pre-denormalized so the path's own re-normalize cancels -> exact latents, no round-trip.
        if args.latent_chain:
            import diffusers.pipelines.ltx.pipeline_ltx_condition as _ltxmod
            _orig_retrieve = _ltxmod.retrieve_latents
            _lat_inj = self._lat_inj

            # sample_mode defaults to "argmax" (vs upstream "sample"): on the non-injected path (e.g. a
            # shot-1 image condition) take the posterior MODE -> a clean, deterministic conditioning encode,
            # consistent with this feature carrying EXACT latents forward. Switch to "sample" for stock parity.
            def _retrieve_patched(encoder_output, generator=None, sample_mode="argmax"):
                real = _orig_retrieve(encoder_output, generator, sample_mode)
                if _lat_inj["lat"] is None:
                    return real
                K = real.shape[2]
                tail = _lat_inj["lat"][:, :, -K:, :, :].to(real.dtype).to(real.device)
                m = pipe.vae.latents_mean.view(1, -1, 1, 1, 1).to(tail.device, tail.dtype)
                s = pipe.vae.latents_std.view(1, -1, 1, 1, 1).to(tail.device, tail.dtype)
                return tail * s + m
            _ltxmod.retrieve_latents = _retrieve_patched
            _orig_denorm = type(pipe)._denormalize_latents
            _lat_stash = self._lat_stash
            self._overlap_lat = (self.overlap - 1) // 8 if self.overlap else 0   # 8x VAE temporal compression

            def _denorm_stash(latents, latents_mean, latents_std, scaling_factor=1.0):
                # #2 Q2 drift-kill anchors. RULE: nothing that has been modified here may enter the carry --
                # corrections (AdaIN / fuse) are DECODE-SIDE ONLY. _lat_stash feeds _carry (see gen(), below),
                # which becomes the NEXT shot's hard tail conditioning; carrying CORRECTED latents creates a
                # positive-feedback loop (the model treats the statistical shift as content, regresses toward
                # natural stats, the next correction over-shoots further) -- falsified by the 2026-07-03 8-shot
                # hold-stress test (anchored drift ~5x baseline by shot 3, monotonically diverging thereafter).
                # Always stash/carry RAW; apply adain/fuse only to the tensor handed to the decoder.
                raw = latents
                out = raw
                if args.latent_adain > 0:
                    if self._anchor_lat is None:
                        # A resumed run has no shot-1 latents (not persisted, same as the latent carry) -> DON'T
                        # anchor to a mid-clip post-resume shot (that would pull toward drift). Skip + warn once.
                        if self._resumed:
                            if not self._adain_resume_warned:
                                print("latent_adain: shot-1 latent anchor unrecoverable after --resume "
                                      "(not persisted, like the latent carry); AdaIN skipped this run", flush=True)
                                self._adain_resume_warned = True
                        else:
                            self._anchor_lat = raw.detach().clone().to("cpu")   # shot 1 = the anchor; capture once (CPU), RAW
                    else:
                        anchor = self._anchor_lat.to(raw.device, raw.dtype)  # cast/move at use; never move the stash
                        out = adain_normalize_latents(raw, anchor, factor=args.latent_adain)
                if args.latent_fuse and self._carry is not None:            # continuations only (carry = prev tail)
                    prev_tail = self._carry.to(out.device, out.dtype)
                    K = min(self._overlap_lat, prev_tail.shape[2], out.shape[2])
                    if K >= 2:
                        out = linear_overlap_fuse(prev_tail[:, :, -K:], out, K)   # blend leading K frames -> seam
                    elif not self._fuse_warned:
                        print("latent_fuse: overlap too small (<2 latent frames), no-op — use --overlap 17+", flush=True)
                        self._fuse_warned = True
                _lat_stash["lat"] = raw.detach()      # RAW only -- see rule above; this becomes _carry in gen()
                return _orig_denorm(out, latents_mean, latents_std, scaling_factor)
            pipe._denormalize_latents = _denorm_stash
            print("director: LATENT-SPACE chaining ON (carry latents, skip the VAE round-trip)", flush=True)
            if args.latent_adain > 0 or args.latent_fuse:
                print("director: Q2 latent anchors -> adain=%.2f fuse=%s (overlap_lat=%d)"
                      % (args.latent_adain, bool(args.latent_fuse), self._overlap_lat), flush=True)

        # per-step live-preview config (LTX projects latents -> RGB, cheap; Wan has no equivalent)
        self._PREVIEW_EVERY = 3
        self._PREVIEW_START = max(1, int(0.3 * args.steps))
        self._LF, self._LH, self._LW = ltx_preview.latent_grid_dims(self.W, self.H, self.seg_frames)

    def first_cond(self, image_path):
        return [LTXVideoCondition(image=load_image(image_path), frame_index=0)] if image_path else None

    def tail_cond(self, tail_frames, strength):
        return [LTXVideoCondition(video=tail_frames, frame_index=0,
                                  strength=max(0.05, min(1.0, strength)))]

    def gen(self, cond, seed, prompt, neg, step_cb, is_continuation):
        args = self.args
        if args.latent_chain and is_continuation:
            self._lat_inj["lat"] = self._carry      # condition this shot on the previous shot's tail latents
        g = torch.Generator("cpu").manual_seed(seed)
        out = self.pipe(conditions=cond, prompt=prompt, negative_prompt=neg,
                        width=self.W, height=self.H, num_frames=self.seg_frames,
                        num_inference_steps=args.steps, guidance_scale=args.cfg,
                        generator=g, callback_on_step_end=step_cb,
                        **self._decode_kwargs).frames[0]
        if args.latent_chain:
            self._lat_inj["lat"] = None
            self._carry = self._lat_stash["lat"]    # this shot's latents -> condition the next shot
        torch.cuda.empty_cache(); gc.collect()
        return out

    def preview_step(self, i, cbk, path):
        args = self.args
        if path and (i + 1 == args.steps or (i >= self._PREVIEW_START and i % self._PREVIEW_EVERY == 0)):
            try:
                if ltx_preview.write_preview_from_latents(cbk.get("latents"), path, self._LF, self._LH, self._LW):
                    print(f"[[PREVIEW {i + 1}]]", flush=True)
            except Exception as e:
                print("[[PREVIEW-ERR]]", e, flush=True)


class WanBackend(VideoBackend):
    """Wan 2.1-VACE-1.3B via WanVACEPipeline. Chains by conditioning each new shot on the previous
    shot's last K frames: build a leading clip [tail frames + gray placeholders] with a mask that is
    BLACK (keep/condition) over the tail and WHITE (generate) over the rest; VACE encodes the masked
    tail as control and denoises the continuation. The first K output frames reproduce the tail, so
    they're stripped. bf16 VAE (not the fp32 default) is REQUIRED: fp32 OOM-kills the 2nd in-process
    decode on this shared 8GB card (validated 2026-06-25); bf16 fits and quality holds."""
    name = "wan"
    strips_cond_head = True
    turbo = False     # B2: when True, load the Self-Forcing DMD distill LoRA + run few-step / cfg 1.0 ("Wan turbo")
    MODEL = "Wan-AI/Wan2.1-VACE-1.3B-diffusers"

    def dims(self, width, height):
        """Wan 2.1 is trained at ~480p and degrades HARD below it (the studio's default 512x320 renders
        washed-out + silhouetted). Rescale the requested aspect so the shorter side is ~480, rounded to
        //32 (VACE spatial constraint). Frame counts are time-based, so this never changes the SEG count."""
        W, H = max(32, (width // 32) * 32), max(32, (height // 32) * 32)   # floor at 32 (never 0 -> no ZeroDivision below)
        short = min(W, H)
        if short < 480:
            s = 480.0 / short
            W = max(32, round(W * s / 32) * 32)
            H = max(32, round(H * s / 32) * 32)
        return W, H

    def fps_for(self, requested):
        return 16     # Wan 2.1 is a 16fps model; 24fps gives wrong motion speed + more frames to decode

    def to_frames(self, sec, fps):
        n = max(1, round(sec * fps))      # round (not floor) so a round-tripped seg_sec recovers the frame count
        return ((n - 1) // 4) * 4 + 1     # VACE: (num_frames - 1) % vae_temporal(4) == 0

    def load(self):
        from diffusers import WanVACEPipeline, AutoencoderKLWan, UniPCMultistepScheduler
        print("[[LOAD 2 5 loading Wan-VACE-1.3B checkpoint (largest part)]]", flush=True)
        vae = AutoencoderKLWan.from_pretrained(self.MODEL, subfolder="vae", torch_dtype=torch.bfloat16)
        pipe = WanVACEPipeline.from_pretrained(self.MODEL, vae=vae, torch_dtype=torch.bfloat16)
        print("[[LOAD 3 5 building pipeline]]", flush=True)
        pipe.scheduler = UniPCMultistepScheduler.from_config(
            pipe.scheduler.config, prediction_type="flow_prediction", use_flow_sigmas=True, flow_shift=3.0)
        if self.turbo:      # B2: Self-Forcing DMD distill LoRA -> few-step denoise (validated: loads cleanly on VACE)
            print("[[LOAD 3 5 loading 4-step distill LoRA (turbo)]]", flush=True)
            pipe.load_lora_weights(
                "Kijai/WanVideo_comfy",
                weight_name="LoRAs/Wan2_1_self_forcing_1_3B/Wan2_1_self_forcing_dmd_1_3B_lora_rank_32_fp16.safetensors",
                adapter_name="distill")
            pipe.set_adapters("distill", 1.0)
        print("[[PHASE offload]]", flush=True)
        print("[[LOAD 4 5 wiring CPU<->GPU offload (8GB mode)]]", flush=True)
        pipe.enable_sequential_cpu_offload()
        # smaller VAE decode tiles (default 256) -> lower decode-moment VRAM peak so the 2nd in-process
        # shot decode survives at 480p on the shared 8GB card (the death point at the default tile size).
        pipe.vae.enable_tiling(tile_sample_min_height=160, tile_sample_min_width=160,
                               tile_sample_stride_height=128, tile_sample_stride_width=128)
        try:
            pipe.vae.enable_slicing()
        except Exception:
            pass
        self.pipe = pipe
        self._gray = Image.new("RGB", (self.W, self.H), (128, 128, 128))
        self._black = Image.new("L", (self.W, self.H), 0)
        self._white = Image.new("L", (self.W, self.H), 255)
        self._PREVIEW_EVERY = 3
        self._PREVIEW_START = max(1, int(0.3 * self.args.steps))
        print("director: Wan-VACE-1.3B backend (bf16 VAE, tail-mask chaining)", flush=True)

    def first_cond(self, image_path):
        img = load_image(image_path).convert("RGB").resize((self.W, self.H)) if image_path else None
        return ("first", img)

    def tail_cond(self, tail_frames, strength):
        return ("tail", [im.resize((self.W, self.H)) for im in tail_frames],
                max(0.05, min(1.0, strength)))

    def _build(self, cond):
        """-> (video_frames, mask_frames, conditioning_scale, K) for WanVACEPipeline.
        mask BLACK(0)=keep/condition, WHITE(255)=generate (prepare_video_latents inactive/reactive split)."""
        nf = self.seg_frames
        if cond[0] == "first":
            img = cond[1]
            if img is None:                                   # full generation (text-to-video via VACE)
                return [self._gray] * nf, [self._white] * nf, 1.0, 0
            return ([img] + [self._gray] * (nf - 1),          # image-anchored opening
                    [self._black] + [self._white] * (nf - 1), 1.0, 1)
        tail, cs = cond[1], cond[2]
        K = len(tail)
        return (tail + [self._gray] * (nf - K),
                [self._black] * K + [self._white] * (nf - K), cs, K)

    def gen(self, cond, seed, prompt, neg, step_cb, is_continuation):
        args = self.args
        video, mask, cs, K = self._build(cond)
        g = torch.Generator("cpu").manual_seed(seed)
        out = self.pipe(prompt=prompt, negative_prompt=neg, video=video, mask=mask,
                        conditioning_scale=cs, width=self.W, height=self.H, num_frames=self.seg_frames,
                        num_inference_steps=(min(args.steps, 8) if self.turbo else args.steps),  # distill is few-step
                        guidance_scale=(1.0 if self.turbo else args.cfg),   # CFG-distilled LoRA blurs at cfg>1
                        generator=g, callback_on_step_end=step_cb, output_type="pil").frames[0]
        torch.cuda.empty_cache(); gc.collect()
        if is_continuation and K:
            return out[K:]      # strip the K reproduced conditioning-tail frames -> continuation-only
        return out

    def preview_step(self, i, cbk, path):
        """Per-step live preview: project the Wan latent to a rough RGB (no VAE decode -> cheap/safe on
        8GB). Color is approximate; the clean frame still lands per-shot via _write_preview. Studio polls
        the PNG by mtime, so writing it is what refreshes the pane. Best-effort -> never kills a run."""
        args = self.args
        if path and (i + 1 == args.steps or (i >= self._PREVIEW_START and i % self._PREVIEW_EVERY == 0)):
            try:
                if ltx_preview.atomic_save_png(ltx_preview.wan_latent_preview(cbk.get("latents")), path):
                    print("[[PREVIEW %d]]" % (i + 1), flush=True)
            except Exception as e:
                print("[[PREVIEW-ERR]]", e, flush=True)


def make_backend(args):
    if args.backend in ("wan", "wan-turbo"):
        b = WanBackend(args)
        b.turbo = args.backend == "wan-turbo"     # B2: Wan-VACE + 4-step distill LoRA
        return b
    return LTXBackend(args)


SEAM_STATIC_MSE = 18.0    # below this (luminance MSE on a 48px downscale) the seam is "static" -> HOLD skips the director


def _seam_mse(a, b):
    """Cheap luminance MSE between two frames (48px downscale). Shared by the HOLD static-skip
    check and the SEAMMSE/DRIFT telemetry markers (Q3)."""
    from PIL import ImageChops
    sa, sb = a.convert("L").resize((48, 48)), b.convert("L").resize((48, 48))
    hist = ImageChops.difference(sa, sb).histogram()
    return sum(i * i * n for i, n in enumerate(hist)) / 2304.0   # 48*48 pixels


def _seam_static(a, b):
    """True if seam frame a is ~unchanged vs b. HOLD mode uses it to skip the per-seam director
    when the scene is holding (prompt still works); real drift -> larger MSE -> the director still
    runs to correct it. Tune SEAM_STATIC_MSE up to skip more, down to skip less."""
    return _seam_mse(a, b) < SEAM_STATIC_MSE


def _emit_tokens(backend, seg_idx, prompt):
    """[[TOKENS seg n]] -- prompt token count driving this shot (Q3 telemetry). Best-effort: a
    backend/pipeline with no discoverable .tokenizer is silently skipped."""
    try:
        n = len(backend.pipe.tokenizer(prompt).input_ids)
        print("[[TOKENS %d %d]]" % (seg_idx, n), flush=True)
    except Exception:
        pass


def main():
    print("[[PHASE importing]]", flush=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True, help="opening-shot prompt")
    ap.add_argument("--directive", default=None, help="overall vision for the VLM director (defaults to --prompt)")
    ap.add_argument("--vlm", action="store_true", help="closed-loop: VLM rewrites the prompt at each seam")
    ap.add_argument("--anchors", default="", help="subject/style anchors to keep in every segment")
    ap.add_argument("--steadiness", default="hold", choices=["hold", "balanced", "evolve"])
    ap.add_argument("--n_prompt", default="worst quality, inconsistent motion, blurry, jittery, distorted")
    ap.add_argument("--image", default=None, help="optional start frame for the first segment")
    ap.add_argument("--total", type=float, default=12.0, help="total seconds")
    ap.add_argument("--seg", type=float, default=3.0, help="seconds per segment")
    ap.add_argument("--overlap", type=int, default=9, help="tail frames used to condition the next segment")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=320)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--cfg", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--frames_dir", default=None)
    ap.add_argument("--out", default="outputs/director.mp4")
    ap.add_argument("--ckpt_dir", default=None, help="directory for resumable checkpoints")
    ap.add_argument("--resume", default=None, help="checkpoint directory to resume from")
    ap.add_argument("--preview", default=None, help="path to write a small live-preview PNG each segment")
    ap.add_argument("--latent_chain", action="store_true",
                    help="#10 (LTX only): carry shot latents forward instead of decode->re-encode")
    ap.add_argument("--latent_adain", type=float, default=0.0,
                    help="#2 (LTX+latent_chain): AdaIN-normalize each shot's latents toward shot 1 (0=off..1=full)")
    ap.add_argument("--latent_fuse", action="store_true",
                    help="#2 (LTX+latent_chain): blend each continuation's leading latent frames toward the carried tail")
    ap.add_argument("--palette_lock", type=float, default=0.0,
                    help="#2 (both backends): 256-bin CDF palette match to a shot-1 pixel pool instead of color_match (0=off..1=full)")
    ap.add_argument("--palette_lock_evolve", action="store_true",
                    help="#2: also apply --palette_lock in steadiness=evolve (default: hold/balanced only)")
    ap.add_argument("--ltx_repo", default="Lightricks/LTX-Video-0.9.5",
                    help="LTX diffusers checkpoint repo (escape hatch: Lightricks/LTX-Video = 0.9.0)")
    ap.add_argument("--ltx_no_mush_patch", action="store_true",
                    help="disable the linspace+mu scheduler override (A/B against the repo's own scheduler config)")
    ap.add_argument("--ltx_variant", default=None, choices=["distilled"],
                    help="LTX transformer variant (distilled = 0.9.8-distilled 2B, forces few-step/cfg=1.0)")
    ap.add_argument("--cond_strength", type=float, default=1.0,
                    help="tail-conditioning strength 0-1 (continuity dial: 1.0=tight, lower=looser/freer)")
    ap.add_argument("--no_crossfade", action="store_true", help="disable the seam crossfade (hard cut)")
    ap.add_argument("--backend", default="ltx", choices=["ltx", "wan", "wan-turbo"],
                    help="video backend (ltx=LTX-2B, wan=Wan-VACE-1.3B, wan-turbo=Wan + 4-step distill LoRA)")
    args = ap.parse_args()
    if args.backend == "wan-turbo":
        # the distill runs few-step; clamp HERE so [[STEP]] totals, the decode-phase flip, and the
        # per-step preview gates (all driven by args.steps) agree with what gen() actually runs.
        args.steps = min(args.steps, 8)
    if args.ltx_variant == "distilled":
        if args.backend != "ltx":
            print("note: --ltx_variant is LTX-only; ignored for backend=%s" % args.backend, flush=True)
        else:
            # same reasoning as wan-turbo above: CFG-distilled + few-step.
            args.steps = min(args.steps, 8)
            args.cfg = 1.0
    if (args.latent_adain > 0 or args.latent_fuse) and not args.latent_chain:
        # #2 Q2: AdaIN + fuse are latent-space ops living inside the latent-chain hooks; ignore them otherwise.
        print("note: --latent_adain/--latent_fuse require --latent_chain (latent-space ops); ignoring", flush=True)

    signal.signal(signal.SIGUSR1, _on_suspend)
    cap_vram()                          # leave ~12% VRAM free for the OS (clean OOM, never a choke)

    LOAD_TOTAL = 5   # the sidecar VLM loads lazily per seam, not at startup
    print("[[LOAD 1 %d initializing torch + diffusers]]" % LOAD_TOTAL, flush=True)

    backend = make_backend(args)
    fps = backend.fps_for(args.fps)     # Wan runs at its native 16fps; LTX keeps the requested fps
    W, H = backend.dims(args.width, args.height)
    seg_frames = backend.to_frames(args.seg, fps)
    target = backend.to_frames(args.total, fps)
    overlap = max(1, min(args.overlap, seg_frames - 8))   # floor at 1: a short Wan seg_frames must never go negative
    backend.configure(W, H, seg_frames, overlap)

    print("[[PHASE loading]]", flush=True)
    backend.load()                      # emits [[LOAD 2/3/4]] + [[PHASE offload]]
    print(f"target={target}f  seg={seg_frames}f  overlap={overlap}f  {W}x{H}  backend={args.backend}")

    def _step_cb(pp, i, t, cbk):
        if i == 0:
            print("[[PHASE generating]]", flush=True)
        print(f"[[STEP {i + 1} {args.steps}]]", flush=True)
        if i + 1 == args.steps:
            print("[[PHASE decoding]]", flush=True)
        backend.preview_step(i, cbk, args.preview)
        return cbk

    def gen(cond, seed, prompt, is_continuation):
        out_frames = backend.gen(cond, seed, prompt, args.n_prompt, _step_cb, is_continuation)
        try:
            if torch.cuda.is_available():   # per-shot peak -> studio_core -> experiment_log's peak_vram_mb
                print("[[VRAM %d]]" % int(torch.cuda.max_memory_allocated() / 1048576), flush=True)
        except Exception:
            pass
        return out_frames

    # --- optional closed-loop director (Qwen3-VL sidecar in an isolated venv) ---
    director = bool(args.vlm)
    directive = args.directive or args.prompt
    # faithful default: an empty/echoing directive can never invent a story, regardless of the flag
    if (not args.directive) or args.directive.strip() == args.prompt.strip():
        args.steadiness = "hold"
    # #2 Q2 palette-lock gate: hold/balanced get it whenever --palette_lock>0; evolve is excluded unless
    # --palette_lock_evolve (evolve WANTS drift). palette_pool is built after shot 1 (below); None = off.
    palette_active = args.palette_lock > 0 and (args.steadiness != "evolve" or args.palette_lock_evolve)
    palette_pool = None

    def _recolor(frames, ref):
        """#2 Q2: palette-lock each shot's frames toward the shot-1 pool when active, else the original
        per-channel color_match toward `ref` (the prior tail). OFF path -> color_match, unchanged."""
        if palette_active and palette_pool is not None:
            return palette_lock(frames, palette_pool, args.palette_lock)
        return color_match(frames, ref)

    # chained (non-director) runs reuse ONE prompt for every shot with no VLM rewriting -> fold the
    # anchors into it so the style leash actually bites (director mode lets the VLM fold them per seam).
    base_prompt = args.prompt if director else _fold_anchors(args.prompt, args.anchors)
    if base_prompt != args.prompt:
        print("chained mode: folded anchors into the prompt -> %s" % _ascii1(base_prompt, 200), flush=True)
    DIRECTOR_PY = "/home/wolve/video_gen/director_venv/bin/python"
    SIDECAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlm_director7b.py")
    if director:
        print("director: Qwen3-VL sidecar (loads per seam, then frees the GPU)", flush=True)

    est_segs = max(1, 1 + -(-(target - seg_frames) // max(1, seg_frames - overlap)))

    beats = []   # running "story so far": the prompt issued for each shot
    # mode-aware redirect cadence: EVOLVE re-plans every shot; HOLD/BALANCED reload the 7B director only
    # every 3rd seam (faithful modes barely change the prompt) and pass --fit_check so the director answers
    # KEEP unless the frame has visibly drifted -> ~1/3 the director reloads, prompt changes only on real drift.
    redirect_every = 1 if args.steadiness == "evolve" else 3
    fit_check = args.steadiness != "evolve"
    if director:
        print(f"director cadence: steadiness={args.steadiness}, redirect_every={redirect_every}, fit_check={fit_check}", flush=True)
    last_redirect = 0
    _last_directed = None       # the seam frame the director last looked at (for the HOLD static-skip)
    _dproc = [None]             # the resident CPU director daemon (A1); None until first spawned

    def _log_director(seg_idx, raw_obj):
        """Append the director's full per-seam record to <ckpt_dir>/director.jsonl. Never fatal."""
        ckpt = getattr(args, "ckpt_dir", None)
        if not ckpt or not raw_obj:
            return
        try:
            os.makedirs(ckpt, exist_ok=True)
            rec = {"seg": int(seg_idx), "raw": raw_obj.get("raw", ""),
                   "plan": raw_obj.get("plan", ""), "prompt": raw_obj.get("prompt", ""),
                   "load_ms": raw_obj.get("load_ms"), "infer_ms": raw_obj.get("infer_ms"),
                   "system": raw_obj.get("system", ""), "user": raw_obj.get("user", "")}
            with open(os.path.join(ckpt, "director.jsonl"), "a") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _director_daemon():
        """Spawn (once) the resident per-seam director on CPU (A1) — it never touches the GPU, so the
        video pipe is never evicted (no cold re-warm each shot). Returns the process, or None to fall
        back to the per-seam GPU subprocess."""
        if _dproc[0] is not None and _dproc[0].poll() is None:
            return _dproc[0]
        import subprocess
        cmd = [DIRECTOR_PY, SIDECAR, "--daemon", "--orig_prompt", args.prompt, "--directive", directive,
               "--anchors", args.anchors, "--steadiness", args.steadiness, "--total", str(est_segs)]
        if fit_check:
            cmd.append("--fit_check")
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=os.path.dirname(SIDECAR))
            line = p.stdout.readline()
            if line and json.loads(line.strip()).get("ready"):
                _dproc[0] = p
                print("director: resident CPU daemon ready (loads once, no GPU eviction)", flush=True)
                return p
            try: p.kill()
            except Exception: pass
        except Exception as e:
            print(f"director daemon unavailable ({e}); using the per-seam subprocess", flush=True)
        return None

    def _kill_director_daemon():
        if _dproc[0] is not None:
            try:
                _dproc[0].stdin.write(json.dumps({"quit": True}) + "\n"); _dproc[0].stdin.flush()
            except Exception:
                pass
            try: _dproc[0].terminate()
            except Exception: pass
            _dproc[0] = None

    def redirect(seg_idx, video, current_prompt):
        """Direct the next shot from the seam frame. Prefers the resident CPU daemon (A1); falls back
        to the original per-seam GPU subprocess if the daemon is unavailable."""
        print("[[PHASE redirecting]]", flush=True)
        import tempfile
        seam = os.path.join(tempfile.gettempdir(), f"seam_{os.getpid()}.png")
        video[-1].save(seam)
        history = " || ".join(beats[-3:])
        p, plan_txt, raw_obj = current_prompt, "", {}
        load_ms, infer_ms = None, None
        daemon = _director_daemon()
        if daemon is not None:
            try:
                req = {"image": seam, "prev": current_prompt or "", "seg": seg_idx, "history": history}
                daemon.stdin.write(json.dumps(req) + "\n"); daemon.stdin.flush()
                resp = json.loads((daemon.stdout.readline() or "{}").strip())
                if resp and "error" not in resp:
                    p = resp.get("prompt") or p
                    plan_txt = resp.get("plan") or ""
                    raw_obj = resp
                    load_ms, infer_ms = 0, int(resp.get("infer_ms", 0))   # resident -> no reload
                else:
                    print(f"director daemon error ({resp.get('error') if resp else 'empty'}); keeping prompt", flush=True)
            except Exception as e:
                print(f"director daemon failed ({e}); falling back to subprocess", flush=True)
                _kill_director_daemon()
                daemon = None
        if daemon is None:                                  # fallback: the original per-seam GPU subprocess
            import subprocess
            cmd = [DIRECTOR_PY, SIDECAR, "--image", seam, "--orig_prompt", args.prompt,
                   "--directive", directive, "--anchors", args.anchors,
                   "--prev", current_prompt or "", "--history", history,
                   "--seg", str(seg_idx), "--total", str(est_segs), "--steadiness", args.steadiness]
            if fit_check:
                cmd.append("--fit_check")
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                out = (r.stdout or "").strip()
                if r.returncode == 0 and out:
                    pm = re.search(r"\[\[PLAN\s+(.*?)\]\]", out)
                    if pm:
                        plan_txt = pm.group(1).strip()
                    nonmarker = [ln for ln in out.splitlines() if not ln.strip().startswith("[[")]
                    if nonmarker:
                        p = nonmarker[-1].strip()
                    for ln in out.splitlines():
                        if ln.startswith("[[RAW]] "):
                            try: raw_obj = json.loads(ln[len("[[RAW]] "):])
                            except Exception: raw_obj = {}
                    d = re.search(r"\[\[DIRECT_MS load=(\d+) infer=(\d+)\]\]", out)
                    if d:
                        load_ms, infer_ms = int(d.group(1)), int(d.group(2))
                else:
                    print(f"director sidecar rc={r.returncode}; keeping prompt. {(r.stderr or '')[-200:]}", flush=True)
            except Exception as e:
                print(f"director sidecar error: {e}; keeping prompt", flush=True)
            torch.cuda.empty_cache(); gc.collect()          # the subprocess used the GPU
        beats.append(p)
        print(f"  director> {p[:100]}")
        print("[[PLAN %d %s]]" % (seg_idx, _ascii1(plan_txt, 400)), flush=True)
        if load_ms is not None:
            print("[[DIRECT_MS %s %s %s]]" % (seg_idx, load_ms, infer_ms), flush=True)
        print(f"[[DIRECT {_ascii1(p, 400)}]]", flush=True)
        _log_director(seg_idx, raw_obj)
        return p

    # --- segment 0 (fresh or resumed) ---
    if args.resume:
        video, seg_idx, current_prompt = load_checkpoint(args.resume, args.backend)
        setattr(backend, "_resumed", True)     # #2 Q2: latent AdaIN anchor can't be reconstructed from a ckpt
        anchor_frame = video[seg_frames - 1]   # Q3: never persisted -- recompute (shot 1's last frame)
        if args.palette_lock > 0:              # #2 Q2: never persisted -- recompute the pool from shot-1 frames
            palette_pool = build_palette_pool(video[:seg_frames], args.seed)
        print(f"resumed from {args.resume}: {len(video)} frames, seg_idx={seg_idx}")
        print(f"[[SEG {seg_idx + 1} {est_segs}]]", flush=True)   # the shot we're about to make
        print("[[PHASE warmup]]", flush=True)
        print("[[LOAD %d %d warming up CUDA (resumed run)]]" % (LOAD_TOTAL, LOAD_TOTAL), flush=True)
    else:
        conds = backend.first_cond(args.image)
        current_prompt = base_prompt
        print(f"[[SEG 1 {est_segs}]]", flush=True)
        print("segment 1 (opening) ...")
        print("[[PHASE warmup]]", flush=True)
        print("[[LOAD %d %d warming up CUDA (first step is slow)]]" % (LOAD_TOTAL, LOAD_TOTAL), flush=True)
        _emit_tokens(backend, 1, current_prompt)
        video = gen(conds, args.seed, current_prompt, False)
        _write_preview(args.preview, video)
        seg_idx = 1
        anchor_frame = video[-1]   # Q3: shot 1's last frame anchors the drift curve for every later shot
        if args.palette_lock > 0:  # #2 Q2: sample the anchor pixel pool from shot 1 (all its frames)
            palette_pool = build_palette_pool(video, args.seed)
        print("[[DRIFT 1 0 0]]", flush=True)   # seeds the curve; SEAMMSE is continuations-only (no seam yet)
        if director:
            beats.append(args.prompt)
            if (seg_idx - last_redirect) >= redirect_every:
                current_prompt = redirect(1, video, current_prompt)
                last_redirect = 1
                _last_directed = video[-1]
        if args.ckpt_dir:
            write_checkpoint(args.ckpt_dir, seg_idx, video, current_prompt, directive,
                             W, H, seg_frames, target, overlap, est_segs, args)

    # --- continuations ---
    while len(video) < target:
        if _SUSPEND:
            if args.ckpt_dir:
                print("[[SUSPENDED %s]]" % args.ckpt_dir, flush=True)
                sys.exit(99)
            # no checkpoint dir (planned as single-segment): exiting 99 would strand an unrecoverable
            # 'suspended' zombie with nothing on disk — ignore the request and finish normally.
            print("  suspend ignored: no checkpoint dir — finishing this run normally", flush=True)
            globals()["_SUSPEND"] = False
        seg_idx += 1
        tail = video[-overlap:]
        prev_last = tail[-1]   # Q3: the join point BEFORE this shot lands (crossfade rewrites video[-overlap:])
        print(f"[[SEG {seg_idx} {est_segs}]]", flush=True)
        print("[[PHASE warmup]]", flush=True)   # clear the stale 'redirecting' phase so the UI never reads "plan shot N+1"
        print(f"segment {seg_idx} (continuing from tail) ...")
        _emit_tokens(backend, seg_idx, current_prompt)
        out = gen(backend.tail_cond(tail, args.cond_strength), args.seed + seg_idx, current_prompt, True)
        drift_pre = _seam_mse(out[-1], anchor_frame)   # Q3: drift vs anchor BEFORE this shot's color correction
        if backend.strips_cond_head:
            # Wan: gen() already stripped the reproduced conditioning tail -> 'out' is continuation-only.
            new = _recolor(out, video[-1])
            video += new
        elif args.no_crossfade or len(out) <= overlap:
            new = out[overlap:] if len(out) > overlap else out
            new = _recolor(new, video[-1])
            video += new
        else:
            # seam polish: crossfade the overlap region -> blend shot N's tail (fading out) with
            # shot N+1's matched head (fading in) so the join is seamless instead of a hard cut.
            tail_n, head = video[-overlap:], out[:overlap]
            video = video[:-overlap] + [Image.blend(tail_n[i], head[i], (i + 1) / (overlap + 1))
                                        for i in range(overlap)]
            new = _recolor(out[overlap:], video[-1])
            video += new
        # Q3 telemetry: seam continuity + drift-vs-anchor, measured AFTER whichever correction just ran
        # (color_match OR #2 Q2's palette_lock / AdaIN), so the marker directly scores this work.
        print("[[SEAMMSE %d %d]]" % (seg_idx, int(_seam_mse(new[0], prev_last) * 100)), flush=True)
        print("[[DRIFT %d %d %d]]" % (seg_idx, int(drift_pre * 100),
                                      int(_seam_mse(video[-1], anchor_frame) * 100)), flush=True)
        if palette_pool is not None:   # #2 Q2 decay: refresh 10% of the pool from this accepted shot
            palette_pool = refresh_palette_pool(palette_pool, new, args.seed + seg_idx)
        _write_preview(args.preview, video)
        if director and len(video) < target and (seg_idx - last_redirect) >= redirect_every:
            if args.steadiness == "hold" and _last_directed is not None and _seam_static(video[-1], _last_directed):
                last_redirect = seg_idx          # scene is holding -> skip the VLM entirely, keep the prompt
                print("  director> (skipped — scene is holding steady; prompt unchanged)", flush=True)
            else:
                current_prompt = redirect(seg_idx, video, current_prompt)
                last_redirect = seg_idx
                _last_directed = video[-1]
        if args.ckpt_dir:
            write_checkpoint(args.ckpt_dir, seg_idx, video, current_prompt, directive,
                             W, H, seg_frames, target, overlap, est_segs, args)

    _kill_director_daemon()
    video = video[:target]
    print("[[PHASE saving]]", flush=True)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    export_to_video(video, args.out, fps=fps)
    if args.frames_dir:
        os.makedirs(args.frames_dir, exist_ok=True)
        for i, im in enumerate(video):
            im.save(os.path.join(args.frames_dir, f"{i:04d}.png"))
    print(f"DIRECTOR_DONE {args.out}  ({len(video)} frames, {len(video)/fps:.1f}s, {seg_idx} segments)")


if __name__ == "__main__":
    main()
