#!/bin/bash
# D2 bench cell runner: libsvtav1 preset 6 vs 4, one (material, anchor, preset) cell.
# args: $1=material rel path (e.g. 6s/xxx.mp4)  $2=anchor crf  $3=preset
# Per cell: run1 warmup (discarded) -> run2 timed (wall clock) -> VMAF n_subsample=1.
# Encoder command contains ONLY the sanctioned axis: -c:v libsvtav1 -crf <a> -preset <p>.
set -u
MAT="$1"; ANCHOR="$2"; PRESET="$3"
ROOT=/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early
INROOT=/workspace/input_videos/eqq_calib
KEY=$(echo "${MAT}" | sed 's#/#_#g; s#\.mp4$##')_crf${ANCHOR}_p${PRESET}
D="$ROOT/libsvtav1_work/cells/$KEY"
IN="$INROOT/$MAT"
mkdir -p "$D"
ENC="ffmpeg -nostdin -y -hide_banner -loglevel error"

# ---- run 1: warmup (discarded) ----
$ENC -i "$IN" -an -c:v libsvtav1 -crf "$ANCHOR" -preset "$PRESET" \
     -f matroska "$D/warm.mkv" 2> "$D/warm.err"
rc1=$?
rm -f "$D/warm.mkv"

# ---- run 2: timed (wall clock) ----
t0=$(date +%s.%N)
$ENC -i "$IN" -an -c:v libsvtav1 -crf "$ANCHOR" -preset "$PRESET" \
     -f matroska "$D/run2.mkv" 2> "$D/run2.err"
rc2=$?
t1=$(date +%s.%N)
elapsed=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.3f", b-a}')

bytes=$(stat -c%s "$D/run2.mkv" 2>/dev/null || echo -1)
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$D/run2.mkv" 2>/dev/null)

# ---- VMAF pass: n_subsample=1, vmaf_v0.6.1, original slice as reference ----
$ENC -i "$D/run2.mkv" -i "$IN" \
     -lavfi "libvmaf=n_subsample=1:model=version=vmaf_v0.6.1:log_fmt=json:log_path=$D/vmaf.json" \
     -f null - 2> "$D/vmaf.err"
rc3=$?
vmaf=$(python3 -c "
import json
try:
    d = json.load(open('$D/vmaf.json'))
    print(d['pooled_metrics']['vmaf']['mean'])
except Exception:
    print('ERR')
")

echo -e "${KEY}\t${MAT}\t${ANCHOR}\t${PRESET}\t${rc1}\t${rc2}\t${rc3}\t${elapsed}\t${bytes}\t${dur}\t${vmaf}" >> "$ROOT/results_raw.tsv"
