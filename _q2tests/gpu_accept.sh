#!/usr/bin/env bash
# Q2 (mine) + Q1 + P1 GPU acceptance, sequential (8GB can't run two at once). NO nvidia-smi anywhere.
# Each run logs to outputs/<name>.log; a DRIFT/marker digest is printed at the end.
cd /home/wolve/video_gen/FramePack || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
PY=venv/bin/python
FOX="a red fox trotting in fresh snow, photoreal"
stamp() { date +%H:%M:%S; }
run() {  # run <logname> <args...>
  local name="$1"; shift
  echo ">>> [$(stamp)] START $name" | tee -a outputs/gpu_accept.driver.log
  if "$PY" "$@" >"outputs/$name.log" 2>&1; then
    echo ">>> [$(stamp)] OK    $name -> $(grep -c '\[\[SEG' "outputs/$name.log") SEG markers" | tee -a outputs/gpu_accept.driver.log
  else
    echo ">>> [$(stamp)] FAIL  $name (rc=$?) tail:" | tee -a outputs/gpu_accept.driver.log
    tail -5 "outputs/$name.log" | tee -a outputs/gpu_accept.driver.log
  fi
}

echo "==== GPU acceptance batch start $(date) ====" | tee outputs/gpu_accept.driver.log

# --- Q2 (my item) FIRST: 4-shot LTX 0.9.5, baseline vs anchored, identical seed/res ---
run q2_base     director.py --prompt "$FOX" --total 7 --seg 2 --steps 30 --cfg 3 --seed 42 \
                --backend ltx --latent_chain \
                --out outputs/q2_base.mp4 --frames_dir outputs/q2_base_frames
run q2_anchored director.py --prompt "$FOX" --total 7 --seg 2 --steps 30 --cfg 3 --seed 42 \
                --backend ltx --latent_chain --latent_adain 0.5 --palette_lock 0.7 \
                --out outputs/q2_anchored.mp4 --frames_dir outputs/q2_anchored_frames

# --- Q1: checkpoint A/B (0.9.0 baseline vs 0.9.5 default), 2-shot at 704x480 (plan-specified) ---
run q1_090 director.py --prompt "$FOX" --total 4 --seg 2 --steps 30 --cfg 3 --seed 42 \
           --backend ltx --latent_chain --width 704 --height 480 \
           --ltx_repo Lightricks/LTX-Video \
           --out outputs/q1_090.mp4 --frames_dir outputs/q1_090_frames
run q1_095 director.py --prompt "$FOX" --total 4 --seg 2 --steps 30 --cfg 3 --seed 42 \
           --backend ltx --latent_chain --width 704 --height 480 \
           --out outputs/q1_095.mp4 --frames_dir outputs/q1_095_frames

# --- P1: single-clip parity via run_ltx.py (0.9.5 default) ---
run p1_runltx_095 run_ltx.py --prompt "$FOX" --seconds 3 --steps 30 --cfg 3 --seed 42 \
                  --width 704 --height 480 --out outputs/p1_runltx_095.mp4

echo "" | tee -a outputs/gpu_accept.driver.log
echo "==== DIGEST ====" | tee -a outputs/gpu_accept.driver.log
for n in q2_base q2_anchored q1_090 q1_095 p1_runltx_095; do
  echo "--- $n ---" | tee -a outputs/gpu_accept.driver.log
  grep -E 'LTX checkpoint|forced LTX base|Q2 latent anchors|latent_fuse:|DIRECTOR_DONE|LTX_DONE' "outputs/$n.log" 2>/dev/null | tee -a outputs/gpu_accept.driver.log
  grep -E '\[\[(DRIFT|SEAMMSE|VRAM)' "outputs/$n.log" 2>/dev/null | tee -a outputs/gpu_accept.driver.log
done
echo "==== GPU acceptance batch done $(date) ====" | tee -a outputs/gpu_accept.driver.log
