#!/usr/bin/env bash
# 8-shot HOLD-scene stress test: baseline vs anchored (adain 0.7 + palette 1.0). A near-static
# still-life isolates COLOR drift from motion, and 8 shots let any color random-walk accumulate --
# the conditions the Q2 anchors target. Self-serializes behind any running render (8GB fits one).
cd /home/wolve/video_gen/FramePack || exit 1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
PY=venv/bin/python
# steadiness is forced to "hold" for non-director chained runs, so this is inherently a hold run.
HOLD="a still life of a ceramic vase with white roses on an oak table, soft morning window light, locked-off camera, no motion, photoreal"
stamp() { date +%H:%M:%S; }

echo "==== hold-stress start $(date) ====" | tee outputs/hold_stress.driver.log
echo ">>> [$(stamp)] waiting for the current batch to free the GPU..." | tee -a outputs/hold_stress.driver.log
while pgrep -f 'director.py|run_ltx.py' >/dev/null 2>&1; do sleep 20; done
echo ">>> [$(stamp)] GPU free; starting hold stress" | tee -a outputs/hold_stress.driver.log

run() {  # run <logname> <args...>
  local name="$1"; shift
  echo ">>> [$(stamp)] START $name" | tee -a outputs/hold_stress.driver.log
  if "$PY" "$@" >"outputs/$name.log" 2>&1; then
    echo ">>> [$(stamp)] OK    $name" | tee -a outputs/hold_stress.driver.log
  else
    echo ">>> [$(stamp)] FAIL  $name (rc=$?)" | tee -a outputs/hold_stress.driver.log
    tail -6 "outputs/$name.log" | tee -a outputs/hold_stress.driver.log
  fi
}

# total 13 / seg 2 / overlap 9 (default) -> 8 segments at 512x320. Same seed/res both arms.
run hold_base     director.py --prompt "$HOLD" --total 13 --seg 2 --steps 30 --cfg 3 --seed 42 \
                  --backend ltx --latent_chain \
                  --out outputs/hold_base.mp4 --frames_dir outputs/hold_base_frames
run hold_anchored director.py --prompt "$HOLD" --total 13 --seg 2 --steps 30 --cfg 3 --seed 42 \
                  --backend ltx --latent_chain --latent_adain 0.7 --palette_lock 1.0 \
                  --out outputs/hold_anchored.mp4 --frames_dir outputs/hold_anchored_frames

echo "" | tee -a outputs/hold_stress.driver.log
echo "==== HOLD DIGEST ====" | tee -a outputs/hold_stress.driver.log
for n in hold_base hold_anchored; do
  echo "--- $n ---" | tee -a outputs/hold_stress.driver.log
  grep -E 'LTX checkpoint:|Q2 latent anchors|latent_fuse:|DIRECTOR_DONE' "outputs/$n.log" | tee -a outputs/hold_stress.driver.log
  grep -E '\[\[(DRIFT|SEAMMSE)' "outputs/$n.log" | tee -a outputs/hold_stress.driver.log
done
echo "==== hold-stress done $(date) ====" | tee -a outputs/hold_stress.driver.log
