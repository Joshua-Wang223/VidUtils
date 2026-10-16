#!/bin/bash
# VMAF re-scoring pass (fixed frame pairing).
# Root cause of first-pass garbage: matroska 1/1000 timebase cannot represent
# 1/30s etc exactly (33ms != 33.333ms), so libvmaf framesync mispaired frames
# for every source whose pts are not ms-aligned (all non-25fps / VFR material).
# Fix: re-index BOTH inputs by frame order (settb=AVTB + setpts=N/30/TB), so
# frame i of the encode is compared with frame i of the reference. Metric
# itself unchanged: libvmaf n_subsample=1, model vmaf_v0.6.1, original slice
# as reference. Encodes/timings from the first pass stay valid and untouched.
set -u
ROOT=/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early
INROOT=/workspace/input_videos/eqq_calib
CELLS="$ROOT/libsvtav1_work/cells"
MAT="$1"   # material rel path
KEYBASE=$(echo "${MAT}" | sed 's#/#_#g; s#\.mp4$##')

for ANCHOR in 18 21 24 27 30; do
  for PRESET in 6 4; do
    D="$CELLS/${KEYBASE}_crf${ANCHOR}_p${PRESET}"
    IN="$INROOT/$MAT"
    [ -f "$D/run2.mkv" ] || { echo -e "MISSING_ENC\t${KEYBASE}_crf${ANCHOR}_p${PRESET}" >> "$ROOT/rescore_problems.tsv"; continue; }
    ffmpeg -nostdin -y -hide_banner -loglevel error -i "$D/run2.mkv" -i "$IN" \
      -lavfi "[0:v]settb=AVTB,setpts=N/30/TB[d];[1:v]settb=AVTB,setpts=N/30/TB[r];[d][r]libvmaf=n_subsample=1:model=version=vmaf_v0.6.1:log_fmt=json:log_path=$D/vmaf_fixed.json" \
      -f null - 2> "$D/vmaf_fixed.err"
    rc=$?
    v=$(python3 -c "
import json
try:
    d = json.load(open('$D/vmaf_fixed.json'))
    print(d['pooled_metrics']['vmaf']['mean'])
except Exception:
    print('ERR')
")
    n=$(python3 -c "
import json
try:
    d = json.load(open('$D/vmaf_fixed.json'))
    print(len(d['frames']))
except Exception:
    print(-1)
")
    echo -e "${KEYBASE}_crf${ANCHOR}_p${PRESET}\t${MAT}\t${ANCHOR}\t${PRESET}\t${rc}\t${v}\t${n}" >> "$ROOT/rescore_vmaf.tsv"
  done
done
