#!/usr/bin/env bash
# digest.sh <logname> [<logname>...] -- print provenance + telemetry markers for each run log.
cd /home/wolve/video_gen/FramePack || exit 1
for n in "$@"; do
  f="outputs/$n.log"
  echo "===== $n ====="
  if [ ! -f "$f" ]; then echo "  (no log)"; continue; fi
  grep -E 'LTX checkpoint:|forced LTX base|Q2 latent anchors|latent_fuse:|decode_timestep|DIRECTOR_DONE|LTX_DONE' "$f"
  grep -E '\[\[(DRIFT|SEAMMSE|VRAM)' "$f"
  grep -iE 'error|traceback|out of memory|cuda' "$f" | grep -v '\[\[' | head -3
  echo
done
