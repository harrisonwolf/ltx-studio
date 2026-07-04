#!/usr/bin/env bash
cd /home/wolve/video_gen/FramePack || exit 1
echo "=== now: $(date +%H:%M:%S) ==="
echo "=== driver log ==="
cat outputs/gpu_accept.driver.log 2>/dev/null
echo "=== hold-stress driver ==="
cat outputs/hold_stress.driver.log 2>/dev/null || echo "  (hold-stress not started)"
echo "=== per-run progress (last SEG / done / checkpoint) ==="
for n in q2_base q2_anchored q1_090 q1_095 p1_runltx_095 hold_base hold_anchored; do
  f="outputs/$n.log"
  if [ -f "$f" ]; then
    segs=$(grep -c '\[\[SEG' "$f")
    last=$(grep -E '\[\[SEG|DIRECTOR_DONE|LTX_DONE|LTX checkpoint:' "$f" | tail -1)
    echo "  $n: SEG-count=$segs | last: $last"
  else
    echo "  $n: (not started)"
  fi
done
echo "=== active proc ==="
pgrep -af 'director.py|run_ltx.py' | grep -v pgrep | cut -c1-70
