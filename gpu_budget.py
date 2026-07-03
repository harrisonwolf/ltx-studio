#!/usr/bin/env python
"""Per-process VRAM ceiling for the local video studio (8GB GPU shared with Windows).

Call cap_vram() ONCE, right after `import torch`, before any CUDA allocation. It caps THIS
process's PyTorch caching allocator so total board usage stays near ~88% of physical VRAM,
leaving ~12% headroom for the Windows desktop/compositor — turning a system-choking 99%
spike into a clean, catchable torch.cuda.OutOfMemoryError instead of a frozen machine.

Verified on RTX 5070 Laptop (8151 MiB), torch 2.11.0+cu128, sm_120 Blackwell, WSL2.

SOFT guard, not a hard wall: it bounds ONLY PyTorch's caching allocator in THIS process.
It does NOT bound bitsandbytes 4-bit buffers, onnxruntime (RIFE), raw cudaMalloc from
3rd-party libs, the CUDA context itself, or OTHER processes. The fraction is of PHYSICAL
total, so cap_vram() derives it from mem_get_info() at startup to subtract VRAM Windows
already holds; if Windows usage grows afterward the cap does not shrink (hence the headroom).
Pair with PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True (already set by the studio).
NOTE: the confirmation line goes to STDERR — several callers use stdout as a JSON/marker
protocol channel, so this helper must never write to stdout.
"""
import os
import sys


def cap_vram(headroom_frac=None, dev=0, min_frac=0.10, max_frac=0.92):
    """Cap this process's CUDA allocator to leave ~headroom_frac of TOTAL VRAM free for the
    OS. headroom_frac defaults to env STUDIO_VRAM_HEADROOM, else 0.12. Idempotent per process.
    Returns the fraction applied, or None if CUDA is unavailable."""
    import torch
    if not torch.cuda.is_available():
        return None
    if getattr(cap_vram, "_applied", False):
        return getattr(cap_vram, "_frac", None)
    if headroom_frac is None:
        try:
            headroom_frac = float(os.environ.get("STUDIO_VRAM_HEADROOM", "0.12"))
        except ValueError:
            headroom_frac = 0.12
    free, total = torch.cuda.mem_get_info(dev)            # bytes; free reflects current Windows usage
    target_used = total * (1.0 - headroom_frac)           # absolute board-usage ceiling we tolerate
    already_used = total - free                           # held by Windows/other procs right now
    proc_budget = max(0, target_used - already_used)
    frac = max(min_frac, min(max_frac, proc_budget / total))   # fraction is of PHYSICAL total
    torch.cuda.set_per_process_memory_fraction(frac, dev)
    cap_vram._applied, cap_vram._frac = True, frac
    print(f"[gpu_budget] VRAM cap: this proc <= {frac * 100:.0f}% of total "
          f"(~{proc_budget / 1048576:.0f} MiB), leaving ~{headroom_frac * 100:.0f}% free for the OS; "
          f"{free / 1048576:.0f}/{total / 1048576:.0f} MiB free at cap-time", file=sys.stderr, flush=True)
    return frac


def free_vram_mb():
    """DISABLED — always returns 0 (unmeasurable) so budget_ok() fails OPEN. We CANNOT read board
    VRAM on this box: nvidia-smi / NVML in this WSL2 (RTX 5070 Blackwell + driver 591.74) destabilize
    the /dev/dxg GPU passthrough and restart the WHOLE WSL VM after repeated calls — REPRODUCED, even
    with no CUDA running. (Underlying bug is pre-2.7.0 WSL dxgkrnl on Blackwell; nvidia-smi just trips
    it.) torch.cuda.mem_get_info works but needs a CUDA context the TUI must not hold. So the live
    VRAM-budget probe is removed; oversubscription is handled by cap_vram() in the run SUBPROCESS —
    a clean, catchable torch OOM, never a VM crash."""
    return 0


# Approx FREE VRAM (MB) each high-GPU action needs to start without tipping the 8GB board.
# Deliberately conservative — these only need to catch 'far too little free', not be exact.
GPU_COST = {"single": 5500, "chained": 5500, "director": 6000, "enhance": 3000}


def budget_ok(cost_mb):
    """(ok: bool, free_mb: int) — is there enough free VRAM for an action needing ~cost_mb?
    Fails OPEN (ok=True) when VRAM can't be measured, so a probe failure never wrongly blocks."""
    free = free_vram_mb()
    if free <= 0:
        return True, 0
    return free >= cost_mb, free
