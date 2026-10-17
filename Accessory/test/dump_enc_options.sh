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
#   例外（2026-09-24，有意变更，两条一起）：
#     · `-threads` 纳入本门的 token 集（--threads 是 2026-09-24 新增的轴，两脚本
#       现在都对**软件编码器**下发它）→ 每一行都会多一个 `-threads <N>`；
#     · cpu_v2 在 `--pix-fmt auto` + 8bit 源上**不再下发** `-pix_fmt yuv420p`
#       （对齐 hwaccel：强制 4:2:0 会把 4:2:2 / 4:4:4 的 8bit 源静默降色度）→
#       全部 cpu_v2 行少一个 `-pix_fmt yuv420p`。基线已按新行为更新。
#       行为判据见 verify/verify_quality_mapping.py。
#   例外（2026-09-28，有意变更，V9/V10）：
#     · V9 重标定 QUALITY_MAP 的 libx265 / libvpx-vp9 / libsvtav1（真实素材等体积），
#       ⇒ hevc_nvenc 降级到 libx265 的换算值 crf 16 → 13。
#     · V10 preset：x264→NVENC 方向对齐 ffmpeg 官方/VE 枚举（medium→p4）；GPU→CPU 的
#       反向降级表**刻意保持** medium 落在 p5（否则两脚本默认请求的编码器不同会让
#       本门的「逐字相同」破功）——故本门输出不变。判据见 verify 的 ⑨/⑪ 组。
#   例外（2026-10-04，有意变更，跨仓契约 CR-1）：
#     · 产品默认 GPU preset 由 p5 改为 p4（与 VE 口径一致，`DEFAULT_PRESET_GPU='p4'`）；
#       反向降级表把 medium 挪到 p4（`NVENC_TO_X264_PRESET['p4']='medium'`，并保留 p5→medium）
#       ⇒ 两脚本默认仍都落到 `-preset medium`，本门「逐字相同」不破；但 cpu_v2 的默认 GPU
#       档由 p5 变 p4 ⇒ `hevc_nvenc 显式 --cq` 一行 `-preset p5 → p4`，基线已同步更新。
#   例外（2026-10-04，有意变更，跨仓契约 CR-2）：
#     · h264/hevc NVENC 的**默认 rc 由「不发 -rc」改为 `-rc vbr`**（与 VE 一致；av1 不变）。
#       ⇒ `hevc_nvenc 显式 --cq` 一行多出 `-rc vbr`（`-cq 20 -b:v 0 -rc vbr -preset p4`），
#       基线已同步更新。判据见 verify 的 verify_rc_lookahead.py ①/⑩A 与跨仓契约 CR-2。
#   例外（2026-10-06，基线补录，**不是代码回归**）：
#     · `hevc_nvenc 显式 --cq` 一行的 `-crf 13 → 15`。起因是 6c71fed（2026-10-04
#       「T4 上机标定落表 h264/hevc_nvenc」）把 QUALITY_MAP 的 hevc_nvenc 行改成
#       (1.1116, 2.1606)，而本基线最后一次更新在它之前的 f617ffe（2026-10-03），
#       当时漏了同步 ⇒ 本门一直红着。核实结论：
#         · 两仓 convert_crf.py 的 QUALITY_MAP[hevc_nvenc] 与 [libx265] **逐条相等**
#           （(1.1116, 2.1606) / (1.0979, -2.3119)），即 -crf 15 是标定后的正确值；
#         · ⑨ 跨仓组 14/14 通过，说明表本身没问题。
#       故更新的是**过期基线**，不是把代码改回旧行为。判据见 verify_quality_mapping.py ⑨。
#   例外（2026-10-17，有意变更，R1 quality-tier）：
#     · 显式钉 `--quality-tier balanced`（阶段一默认即 balanced，与不写逐字等价）——
#       阶段三翻默认档到 quality 时本门不随默认漂移。单格自带的 --quality-tier（下面
#       lossless 两格）写在钉住值之后、argparse 后写覆盖 ⇒ 以单格为准。
#     · 默认质量锚点 21 → 24：无质量参数的格 `-crf 21` → `-crf 24`。
#     · 显式值被**档窗口**（balanced = 基准轴 23~26）钳制：
#         `libx264 --cq 23`（基准轴 ≈18 ⇒ 钳 23 → `-crf 23`）、`--cq 4`（≈-1 ⇒ 钳 23）、
#         `hevc_nvenc --cq 20`（基准轴 ≈16.05 ⇒ 钳 23 → 有 GPU 时 `-cq 28`；
#         无 GPU 的机器 hwaccel 降级 libx265 → `-crf 23`，与既有 hevc_nvenc 格同因）；
#         `libvpx-vp9 --crf 32` 归一到基准轴（≈23.3）在窗内 ⇒ 不变；`libsvtav1 --crf 30`
#         按「基准轴量纲」处理（svt 不在 CRF_SUPPORTED_CODECS）⇒ 30 越上界钳 26 → `-crf 34`；
#     · 新增 lossless 两格：`--quality-tier lossless`（锚点注入 ⇒ 基准轴 0 ⇒ 通道哨兵）
#       与 `lossless + --crf 5`（显式 5 被窗 [0,0] 钳到 0，步骤 2b ⇒ 同一份无损下发，
#       仅多一条钳制告警）。NVENC 侧 hwaccel 把 0 改写成 `-rc constqp -qp 0 -b:v 0`、
#       cpu_v2 原样透传 `-cq 0 -b:v 0 -rc vbr`（既有 ⑩ C 的差异化，不是本轮新增）。
# 用法: bash test/dump_enc_options.sh > out.txt
set -uo pipefail
# 仓库根定位：向上搜标记（.git / convert_crf.py），不数 /.. 的层数 ——
# 本目录已移进 Accessory/，数层数会在目录再移动时悄悄失效。
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$_HERE/../_paths.sh"
cd "$VU_ROOT" || exit 1
F=$VU_TEMP/lockstep
mkdir -p "$F"
[ -f "$F/land.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/land.mp4"

# 只留与本次判定相关的 flag+取值，丢掉路径/元数据/滤镜等无关部分。
# 两个脚本的标签不同（hwaccel 是「执行命令」，cpu_v2 是「命令」），故一并匹配。
tokens() {
  sed -n 's/.*命令[^:]*: \(ffmpeg .*\)$/\1/p' <<< "$1" \
    | grep -oE '\-(c:v|cq|crf|qp|b:v|rc|rc-lookahead|lag-in-frames|preset|threads|pix_fmt|profile:v|x264-params|x265-params) [^ ]+' \
    | tr '\n' ' '
}
hw() { python vidcrop_hwaccel.py --input "$F/land.mp4" --output "$F/o" --dry-run "$@" 2>&1; }
cv() { python vidcrop_cpu_v2.py  --input "$F/land.mp4" --output "$F/o" --dry-run "$@" 2>&1; }

# hwaccel 固定三轴全 CPU（与 dump_filter_chains.sh 同因：跳过 GPU 探测、与 v2 可比）
CPU_ONLY=(--decode cpu --scale-algo libswscale-lanczos)
BASE=(--mode crop --output-width 640 --output-height 360)
# R1（2026-10-17）：显式钉 `--quality-tier balanced`（阶段一默认即 balanced；单格若自带
# --quality-tier 写在其后 ⇒ argparse 后写覆盖，以单格为准）。
TIER=(--quality-tier balanced)
emit() {  # emit <标签> <参数...>
  local tag=$1; shift
  printf 'hwaccel  %-34s %s\n' "$tag" "$(tokens "$(hw "${CPU_ONLY[@]}" "${TIER[@]}" "$@" "${BASE[@]}")")"
  printf 'cpu_v2   %-34s %s\n' "$tag" "$(tokens "$(cv "${TIER[@]}" "$@" "${BASE[@]}")")"
}

emit "默认编码器(无质量参数)"
emit "libx265"                       --codec libx265
emit "libvpx-vp9"                    --codec libvpx-vp9 --crf 32
emit "libsvtav1"                     --codec libsvtav1 --crf 30
emit "libx264 显式 --cq(降级换算)"   --codec libx264 --cq 23
emit "hevc_nvenc 显式 --cq"          --codec hevc_nvenc --cq 20
# R1 新增：lossless 两格 —— ①锚点注入（无质量参数）；②显式 --crf 5 被窗 [0,0] 钳到 0
# （步骤 2b）⇒ 下发命令与 ① 相同、只多一条钳制告警（见文件头的例外说明）。
emit "hevc_nvenc · lossless 档"       --codec hevc_nvenc --quality-tier lossless
emit "hevc_nvenc · lossless + --crf 5" --codec hevc_nvenc --quality-tier lossless --crf 5
