#!/usr/bin/env bash
# test/test_green_chroma_regression.sh — 端到端回归：产物色度不能被写没（全绿）
#
# 背景（2026-09-22，见 memory/project_green_chroma_defect.md）：
#   输出端 -colorspace 与解码帧的 csp:unknown 不一致时，ffmpeg 会在滤镜链尾与编码器之间
#   自动插入一个 CPU scale（auto_scale_0）去凑 codec context，这个转换在「硬解 + NVENC」
#   链上会把 U/V 清零 → 产物全绿。修复 = 用 setparams 把色彩属性标到帧上。
#
# 本测试的四件事：
#   ① 绿灯：走脚本默认路径（硬解 + NVENC + SD 源 → 推 smpte170m）产出色度正常、标签正确；
#   ② 无自动转换：把同一条命令加 -loglevel verbose 复跑，日志里不应有 auto_scale；
#   ③ 红灯自检：把命令里的 setparams 段删掉再跑，**必须**复现 U/V<16 —— 否则本测试
#      没有区分力，等于没测（这条是本测试的"装置自检"）；
#   ④ 真片（可选）：真实原片整片重裁 + `-c copy` 切 3 段，逐段色度正常。
#
# 需要 NVIDIA GPU（硬解 + NVENC）；没有就打印 SKIP 并退出 0。
# 用法: bash test/test_green_chroma_regression.sh
#   环境变量： REAL_SRC=<真实原片>（默认 Dora S02E01；不存在则跳过第 ④ 步）
#             SKIP_REAL=1      跳过第 ④ 步
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

FF=${FFMPEG_BIN:-ffmpeg}
PY=${PYTHON:-python3}
WORK=temp/green_chroma_test
mkdir -p "$WORK"
REAL_SRC=${REAL_SRC:-"/workspace/output_videos/Dora/Season 02/S02E01_Lost Squeaky.mp4"}

PASS=0; FAIL=0
ok()  { printf '  [OK]   %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  [FAIL] %s\n     %s\n' "$1" "${2:-}"; FAIL=$((FAIL+1)); }

# ── 环境门槛：没有 NVENC / CUDA 硬解就直接跳过 ──
# ⚠ 不能写成 `ffmpeg -encoders | grep -q hevc_nvenc`：脚本是 pipefail，grep -q 命中即退出
#   会让 ffmpeg 收到 SIGPIPE 而非零退出，整条管道被判失败 → 明明有 NVENC 也走 SKIP。
ENCODERS=$("$FF" -hide_banner -encoders 2>/dev/null || true)
if ! grep -q 'hevc_nvenc' <<<"$ENCODERS"; then
  echo "SKIP: 当前 ffmpeg 没有 hevc_nvenc（无 NVIDIA GPU？）"
  exit 0
fi
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; then
  echo "SKIP: 没有可用的 NVIDIA 设备"
  exit 0
fi

# ── 色度取样（与 probe/probe_green_chroma.sh 同一口径；POSIX awk，T4 上是 mawk）──
# 输出 "Y\tU\tV"（两帧均值）；取不到数据输出 "-1\t-1\t-1"
AWK_UV='
/lavfi\.signalstats\.YAVG=/ { split($0,a,"YAVG="); sY+=a[2]; n++ }
/lavfi\.signalstats\.UAVG=/ { split($0,a,"UAVG="); sU+=a[2] }
/lavfi\.signalstats\.VAVG=/ { split($0,a,"VAVG="); sV+=a[2] }
END { if (n > 0) printf "%.1f\t%.1f\t%.1f\n", sY/n, sU/n, sV/n; else printf "-1\t-1\t-1\n" }'
uv() {  # uv <文件> [ss]
  local f=$1 ss=${2:-1}
  { "$FF" -nostdin -hide_banner -loglevel info -ss "$ss" -i "$f" -frames:v 2 -an \
      -filter:v:0 'format=yuv420p,signalstats,metadata=print' -f null - 2>&1 \
      | awk "$AWK_UV"; } || printf -- '-1\t-1\t-1\n'
}
# 判据用**缺陷签名**而不是"接近 128"：色度均值随内容变化（实测某段 U=89.9/V=167.5
# 是正常画面），只有 U/V 双双被清成 ~0 才是本缺陷。所以判"有色度"用 OR。
uv_has_chroma() { awk -v u="$1" -v v="$2" 'BEGIN{exit !(u>=16 || v>=16)}'; }
uv_zero()       { awk -v u="$1" -v v="$2" 'BEGIN{exit !(u<16 && v<16)}'; }
tags_of()   { ffprobe -v error -select_streams v:0 -show_entries \
                stream=color_space,color_primaries,color_transfer,color_range -of csv=p=0 "$1"; }

# ── ① 造 SD fixture（768x576 → 脚本会按"SD + 非 PAL"推成 smpte170m）──
SRC_FIX=$WORK/src_sd.mp4
if [ ! -f "$SRC_FIX" ]; then
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
    -i testsrc2=size=768x576:rate=30:duration=4 -c:v libx264 -preset ultrafast \
    -pix_fmt yuv420p "$SRC_FIX" || { echo "无法生成 fixture"; exit 1; }
fi

echo "── 0. 基准：源自身色度必须正常（否则本轮结论无效）──"
read -r _SY SU SV <<<"$(uv "$SRC_FIX" 1)"
if uv_has_chroma "$SU" "$SV"; then ok "源有色度（U=$SU V=$SV）"
else bad "源本身没色度（U=$SU V=$SV）——换素材重跑" "探针口径：源必须 U/V≥16"; fi

echo "── 1. 绿灯：脚本默认路径（硬解 + NVENC）产物色度必须正常 ──"
GREEN=$WORK/green.mp4
"$PY" vidcrop_hwaccel.py --input "$SRC_FIX" --output "$GREEN" --mode crop \
  --output-width 768 --output-height 432 --codec hevc_nvenc --overwrite >"$WORK/green.log" 2>&1
if [ -f "$GREEN" ]; then
  read -r _Y GU GV <<<"$(uv "$GREEN" 1)"
  if uv_has_chroma "$GU" "$GV"; then ok "产物有色度、未归零（U=$GU V=$GV）"
  else bad "产物色度归零（U=$GU V=$GV）——缺陷复发" "看 $WORK/green.log"; fi
  T=$(tags_of "$GREEN")
  if [[ "$T" == *smpte170m* ]]; then ok "容器色彩标签含 smpte170m（$T）"
  else bad "容器色彩标签不对（$T）"; fi
  if grep -q 'setparams=' "$WORK/green.log"; then ok "命令里下发了 setparams（标到帧上）"
  else bad "命令里没有 setparams" "$(grep -m1 '执行命令' "$WORK/green.log")"; fi
else
  bad "脚本没有产出文件" "$(tail -5 "$WORK/green.log")"
fi

echo "── 2/3. 命令级：verbose 复跑不应插入 auto_scale；去掉 setparams 应复现故障 ──"
DRY=$("$PY" vidcrop_hwaccel.py --input "$SRC_FIX" --output "$WORK/dry.mp4" --mode crop \
        --output-width 768 --output-height 432 --codec hevc_nvenc --overwrite --dry-run 2>&1)
CMD=$(grep -m1 '执行命令' <<<"$DRY" | sed -E 's/^.*执行命令[^:]*: //')
if [ -z "$CMD" ]; then
  bad "拿不到 --dry-run 的真实命令" "$(printf '%s\n' "$DRY" | tail -3)"
else
  # ② 无 auto_scale
  CMD_V=$(printf '%s' "$CMD" | sed -E "s|-loglevel warning|-loglevel verbose|; s|[^ ']*dry\.mp4|$WORK/dry_v.mp4|")
  eval "$CMD_V" >"$WORK/dry_v.log" 2>&1
  if grep -q "auto-inserting filter 'auto_scale" "$WORK/dry_v.log"; then
    bad "复跑时 ffmpeg 仍自动插入了 auto_scale" "见 $WORK/dry_v.log"
  else
    ok "复跑日志里没有 auto_scale（色彩属性已标到帧上）"
  fi
  # ③ 红灯自检
  CMD_R=$(printf '%s' "$CMD" | sed -E "s|,setparams=[^ ']*||; s|[^ ']*dry\.mp4|$WORK/red.mp4|")
  if grep -q ',setparams=' <<<"$CMD"; then
    eval "$CMD_R" >"$WORK/red.log" 2>&1
    if [ -f "$WORK/red.mp4" ]; then
      read -r _Y RU RV <<<"$(uv "$WORK/red.mp4" 1)"
      if uv_zero "$RU" "$RV"; then ok "红灯自检有效：删掉 setparams 立刻复现色度归零（U=$RU V=$RV）"
      else bad "红灯不亮：删掉 setparams 后色度仍正常（U=$RU V=$RV）→ 本测试没有区分力" \
               "退出码/环境可能有变，见 $WORK/red.log"; fi
    else
      bad "红灯命令没产出文件（预期能跑出绿色产物）" "$(tail -3 "$WORK/red.log")"
    fi
  else
    bad "原命令里没有 setparams，无法做红灯自检" "$CMD"
  fi
fi

echo "── 4. 真片：整片重裁 + -c copy 切 3 段，逐段色度正常 ──"
if [ "${SKIP_REAL:-0}" = 1 ]; then
  echo "  (SKIP_REAL=1，跳过)"
elif [ -f "$REAL_SRC" ]; then
  REAL_OUT=$WORK/real_crop.mp4
  "$PY" vidcrop_hwaccel.py --input "$REAL_SRC" --output "$REAL_OUT" --mode crop \
    --output-width 768 --output-height 432 --overwrite >"$WORK/real.log" 2>&1
  if [ -f "$REAL_OUT" ]; then
    read -r _Y RU2 RV2 <<<"$(uv "$REAL_OUT" 20)"
    if uv_has_chroma "$RU2" "$RV2"; then ok "整片重裁未归零（U=$RU2 V=$RV2）"
    else bad "整片重裁色度归零（U=$RU2 V=$RV2）" "看 $WORK/real.log"; fi
    for i in 0 1 2; do
      SEG=$WORK/real_seg$i.mp4
      "$FF" -nostdin -y -hide_banner -loglevel error -ss $((i * 400)) -t 30 \
        -i "$REAL_OUT" -c copy "$SEG" >/dev/null 2>&1
      read -r _Y SU2 SV2 <<<"$(uv "$SEG" 5)"
      if uv_has_chroma "$SU2" "$SV2"; then ok "切段 $i 未归零（U=$SU2 V=$SV2）"
      else bad "切段 $i 色度归零（U=$SU2 V=$SV2）——下游增强据此会出绿" ""; fi
    done
  else
    bad "真实原片重裁失败" "$(tail -5 "$WORK/real.log")"
  fi
else
  echo "  (真片不存在：$REAL_SRC，跳过；可用 REAL_SRC= 指定)"
fi

echo
printf '汇总: 通过 %d / 失败 %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
