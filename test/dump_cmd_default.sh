#!/usr/bin/env bash
# test/dump_cmd_default.sh — 抓「默认调用」时完整命令里与解码相关的部分。
# 为什么单独一个工装：现有的 dump_filter_chains.sh 只比 `-filter:v:0`，
# 而 `--decode auto` 的影响体现在 `-hwaccel` / `-hwaccel_output_format` 上，
# 只看滤镜链会漏掉。用法: bash test/dump_cmd_default.sh > out.txt
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
F=temp/lockstep
mkdir -p "$F"
[ -f "$F/land.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/land.mp4"

# 只留"解码轴会改到的那几个 token"，其余（路径/元数据）与本次判定无关
decode_bits() {
  local out=$1 hw hof
  hw=$(sed -n 's/.*\(-hwaccel [^ ]*\).*/\1/p' <<<"$out")
  hof=$(sed -n 's/.*\(-hwaccel_output_format [^ ]*\).*/\1/p' <<<"$out")
  printf '%-26s %s\n' "${hw:-<无 -hwaccel>}" "${hof:-<无 hof>}"
}

emit() {  # emit <标签> <参数...>
  local tag=$1; shift
  local out
  out=$(python vidcrop_hwaccel.py --input "$F/land.mp4" --output "$F/o" --dry-run "$@" 2>&1)
  printf '%-34s %s\n' "$tag" "$(decode_bits "$out")"
}

emit "默认(cover 1440x1080)"       --mode cover --output-width 1440 --output-height 1080
emit "默认(crop 640x360)"          --mode crop  --output-width 640  --output-height 360
emit "默认(crop-cover 16:9)"       --mode crop-cover --crop-ratio 16:9 --output-width 640
emit "--decode cpu"                --mode cover --output-width 1440 --output-height 1080 --decode cpu
emit "--decode cuda"               --mode cover --output-width 1440 --output-height 1080 --decode cuda
emit "--decode vaapi"              --mode cover --output-width 1440 --output-height 1080 --decode vaapi
emit "--decode opencl"             --mode cover --output-width 1440 --output-height 1080 --decode opencl
