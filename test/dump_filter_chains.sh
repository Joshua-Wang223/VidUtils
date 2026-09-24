#!/usr/bin/env bash
# test/dump_filter_chains.sh — 把两个脚本在各种模式/参数下的滤镜链导成稳定的文本，
# 供"改动前后逐字对比"用（不传 --scale-algo 时，默认行为必须一字节不变）。
# hwaccel 侧固定以「三轴全 CPU」调用，好与 cpu_v2 的 CPU 链逐字对比；
# 默认路径（不传任何轴参数）的回归另行对比 test/baseline/chains_before_default.txt。
# 用法: bash test/dump_filter_chains.sh [额外参数...]
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
F=temp/lockstep
mkdir -p "$F"
[ -f "$F/land.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/land.mp4"
[ -f "$F/port.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1080x1920:rate=25:duration=1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/port.mp4"

# 解析滤镜链：按 token 取（shlex.join 是按需加引号的，引号可有可无）
pick() {
  sed -n "s/.*-filter:v:0 \('[^']*'\|[^ ][^ ]*\).*/\1/p;s/.*-vf \('[^']*'\|[^ ][^ ]*\).*/\1/p" <<< "$1" \
    | sed "s/^'//; s/'\$//" | sed 's/,setparams=.*//'
}
hw() { python vidcrop_hwaccel.py --input "$1" --output "$F/o" --dry-run "${@:2}" 2>&1; }
cv() { python vidcrop_cpu_v2.py  --input "$1" --output "$F/o" --dry-run "${@:2}" 2>&1; }

# hwaccel 侧固定用「三轴全 CPU」写法，它同时是旧 --hwaccel none 的等价物，
# 且会走「跳过全部 GPU 探测」的快路径 → 与 cpu_v2 的 CPU 链可逐字对比。
# 注意别退回成 `--decode cpu` 单独用：那只关解码，缩放轴仍是 auto（优先 cuda），
# 在带 scale_cuda 的机器上会生成 hwupload 链，与 cpu_v2 不可比。
CPU_ONLY=(--decode cpu --scale-algo libswscale-lanczos --codec libx264)
emit() {  # emit <标签> <源> <公共参数...>
  local tag=$1 src=$2; shift 2
  printf 'hwaccel  %-46s %s\n' "$tag" "$(pick "$(hw "$src" "${CPU_ONLY[@]}" "$@")")"
  printf 'cpu_v2   %-46s %s\n' "$tag" "$(pick "$(cv "$src" "$@")")"
}

emit "cover 1920x1080->1440x1080 源更宽" "$F/land.mp4" --mode cover --output-width 1440 --output-height 1080
emit "cover 1080x1920->1280x720 源更高"  "$F/port.mp4" --mode cover --output-width 1280 --output-height 720
emit "cover 1920x1080->1280x720 比例同"  "$F/land.mp4" --mode cover --output-width 1280 --output-height 720
emit "cover 同尺寸+--no-skip"            "$F/land.mp4" --mode cover --output-width 1920 --output-height 1080 --no-skip-same-size
emit "crop-cover ratio16:9 w=640"        "$F/land.mp4" --mode crop-cover --crop-ratio 16:9 --output-width 640
emit "crop-cover 无 ratio 两维度"        "$F/land.mp4" --mode crop-cover --output-width 640 --output-height 360
emit "crop 640x360"                      "$F/land.mp4" --mode crop --output-width 640 --output-height 360
emit "cover 带 --color-range pc"         "$F/land.mp4" --mode cover --output-width 1440 --output-height 1080 --color-range pc
