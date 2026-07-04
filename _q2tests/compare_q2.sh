#!/usr/bin/env bash
cd /home/wolve/video_gen/FramePack || exit 1
for n in q2_base q2_anchored; do
  echo "===== $n ====="
  grep -E 'LTX checkpoint:|Q2 latent anchors|latent_fuse:' "outputs/$n.log"
  grep -E '\[\[(DRIFT|SEAMMSE|VRAM)' "outputs/$n.log"
  echo
done
