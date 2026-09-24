#!/usr/bin/env bash
# test/dump_enc_options.sh — 把「编码/质量/码率控制」相关的命令 token 导成稳定文本，
# 供"改动前后逐字对比"用。
#
# 为什么还要第三道门：dump_filter_chains.sh 只比 `-filter:v:0`，dump_cmd_default.sh
# 只比 `-hwaccel*`，而 --rc-mode / --qp / --lookahead / --bitrate 全都落在
# `-c:v / -cq / -crf / -qp / -b:v / -rc / -rc-lookahead / -lag-in-frames / -preset /
# -x265-params` 这一组上 —— 只看前两者等于没回归（见
# feedback_verify_consistency_claims.md「改命令构造却只比滤镜链」）。
#
# 判据：不传任何新参数时，本脚本的输出必须逐字不变。
#   例外（2026-09-23，有意变更）：NVENC 的 `-cq` 现在默认配 `-b:v 0`（纯恒定质量，
#   对照 Video_Enhancement 的 `-cq:v N -b:v 0`），故 `hevc_nvenc 显式 --cq` 一行比旧
#   基线多一个 `-b:v 0`；基线已按新行为更新。行为判据见 verify/verify_rc_lookahead.py 第 ⑩ 组。
# 用法: bash test/dump_enc_options.sh > out.txt
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
F=temp/lockstep
mkdir -p "$F"
[ -f "$F/land.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/land.mp4"

# 只留与本次判定相关的 flag+取值，丢掉路径/元数据/滤镜等无关部分。
# 两个脚本的标签不同（hwaccel 是「执行命令」，cpu_v2 是「命令」），故一并匹配。
tokens() {
  sed -n 's/.*命令[^:]*: \(ffmpeg .*\)$/\1/p' <<< "$1" \
    | grep -oE '\-(c:v|cq|crf|qp|b:v|rc|rc-lookahead|lag-in-frames|preset|pix_fmt|profile:v|x264-params|x265-params) [^ ]+' \
    | tr '\n' ' '
}
hw() { python vidcrop_hwaccel.py --input "$F/land.mp4" --output "$F/o" --dry-run "$@" 2>&1; }
cv() { python vidcrop_cpu_v2.py  --input "$F/land.mp4" --output "$F/o" --dry-run "$@" 2>&1; }

# hwaccel 固定三轴全 CPU（与 dump_filter_chains.sh 同因：跳过 GPU 探测、与 v2 可比）
CPU_ONLY=(--decode cpu --scale-algo libswscale-lanczos)
BASE=(--mode crop --output-width 640 --output-height 360)
emit() {  # emit <标签> <参数...>
  local tag=$1; shift
  printf 'hwaccel  %-34s %s\n' "$tag" "$(tokens "$(hw "${CPU_ONLY[@]}" "$@" "${BASE[@]}")")"
  printf 'cpu_v2   %-34s %s\n' "$tag" "$(tokens "$(cv "$@" "${BASE[@]}")")"
}

emit "默认编码器(无质量参数)"
emit "libx265"                       --codec libx265
emit "libvpx-vp9"                    --codec libvpx-vp9 --crf 32
emit "libsvtav1"                     --codec libsvtav1 --crf 30
emit "libx264 显式 --cq(降级换算)"   --codec libx264 --cq 23
emit "hevc_nvenc 显式 --cq"          --codec hevc_nvenc --cq 20
