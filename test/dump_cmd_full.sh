#!/usr/bin/env bash
# test/dump_cmd_full.sh — 两脚本**完整 ffmpeg 命令**的逐字相等门禁（第四道门）。
#
# 为什么还要这道门：前三道各只看一部分 —— dump_filter_chains.sh 只看滤镜链、
# dump_cmd_default.sh 只看 -hwaccel*、dump_enc_options.sh 只取一小撮 token。
# 于是「同一条逻辑请求，两个脚本下发的命令是否逐字相同」这件事一直没人管，
# 而它正是用户对这两个脚本的预期（README：CLI 与 v2 逐字对齐）。
#
# 判据（**自带断言**，不是只导出文本）：
#   · 每一格：hwaccel 与 cpu_v2 的整条命令必须逐字相同；
#   · 任一为空 → 判失败（"空 == 空"是最隐蔽的假绿）；
#   · 一格都没比到 → 判失败（过滤型检查器的"空集"陷阱）；
#   · 命令写 stdout（供 diff 基线 test/baseline/cmd_full.txt），判词写 stderr。
#
# 不覆盖什么（用 --decode cpu --scale-algo libswscale-lanczos 把 hwaccel 钉在三轴全
# CPU 上规避）：
#   · NVENC 轴：同一份 CLI 下 hwaccel 会**真降级**到 libx265、cpu_v2 是原样透传
#     NVENC 编码器，两者本来就该不同（能力差异，不是分叉）；
#   · GPU 专属选项 -hwaccel* / -profile:v / -spatial-aq；
#   · 10bit 源的 -pix_fmt（hwaccel→p010le、cpu_v2→yuv420p10le，既有差异，另案）。
#
# 用法:
#   bash test/dump_cmd_full.sh > out.txt      # 判词在 stderr；exit 1 = 有分叉
#   diff test/baseline/cmd_full.txt out.txt   # 基线回归
#   SELFTEST=1 bash test/dump_cmd_full.sh     # 自检判词装置（正/负/空三格）
#   SABOTAGE=1 bash test/dump_cmd_full.sh     # 负向对照：门必须判失败（期望 exit 1）
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

F=temp/lockstep
mkdir -p "$F"
[ -f "$F/land.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/land.mp4" || exit 2

OUT="$F/o_full"     # dry-run 不写盘；两脚本共用同一路径才能逐字对比

# 两个脚本的命令行标签不同（hwaccel「执行命令」/ cpu_v2「命令」），一并匹配
cmd_of() { sed -n 's/.*命令[^:]*: \(ffmpeg .*\)$/\1/p' <<< "$1" | head -1; }
# hwaccel 固定「三轴全 CPU」：否则默认路径会带 GPU 项、与 cpu_v2 不可比。
# 两边都**不显式给 --threads**，这样连自动值（两脚本共用同一套 cgroup 感知探测）
# 也一并钉在门里。
hw() { python vidcrop_hwaccel.py --input "$F/land.mp4" --output "$OUT" \
         --decode cpu --scale-algo libswscale-lanczos --dry-run "$@" 2>&1; }
cv() { python vidcrop_cpu_v2.py --input "$F/land.mp4" --output "$OUT" \
         --dry-run "$@" 2>&1; }

FAILS=0
PAIRS=0
cmp_pair() {   # cmp_pair <标签> <hwaccel 命令> <cpu_v2 命令>
  local tag=$1 h=$2 c=$3
  [ "${SABOTAGE:-0}" = "1" ] && c="$c -sabotage"      # 负向对照用
  PAIRS=$((PAIRS + 1))
  if [ -z "$h" ] || [ -z "$c" ]; then
    printf '  ✗ %-30s 空命令（hwaccel=%d 字节、cpu_v2=%d 字节）\n' \
      "$tag" "${#h}" "${#c}" >&2
    FAILS=$((FAILS + 1)); return 2
  fi
  if [ "$h" = "$c" ]; then
    printf '  ✓ %-30s 逐字相同\n' "$tag" >&2
    return 0
  fi
  printf '  ✗ %-30s 命令不同：\n' "$tag" >&2
  python - "$h" "$c" <<'PY' >&2
import difflib, shlex, sys
a, b = shlex.split(sys.argv[1]), shlex.split(sys.argv[2])
for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
    if op == 'equal':
        continue
    print(f'      {op:8s} hwaccel[{" ".join(a[i1:i2]) or "-"}]'
          f'   cpu_v2[{" ".join(b[j1:j2]) or "-"}]')
PY
  FAILS=$((FAILS + 1)); return 1
}

emit() {   # emit <标签> <公共参数...>
  local tag=$1; shift
  local h c
  h=$(cmd_of "$(hw "$@")")
  c=$(cmd_of "$(cv "$@")")
  printf 'hwaccel  %-30s %s\n' "$tag" "$h"
  printf 'cpu_v2   %-30s %s\n' "$tag" "$c"
  cmp_pair "$tag" "$h" "$c"
}

if [ "${SELFTEST:-0}" = "1" ]; then
  echo "── SELFTEST：判词装置的正/负/空三格 + 起始计数 ──" >&2
  _f0=$FAILS
  cmp_pair "自检·相同" "ffmpeg -i a" "ffmpeg -i a"; _r1=$?
  cmp_pair "自检·不同" "ffmpeg -i a -crf 1" "ffmpeg -i a -crf 2"; _r2=$?
  cmp_pair "自检·空值" "" "ffmpeg -i a"; _r3=$?
  _bad=0
  [ "$_r1" = 0 ] || { echo "  ✗ 自检失败：相同的一对应判 0，实得 $_r1" >&2; _bad=1; }
  [ "$_r2" = 1 ] || { echo "  ✗ 自检失败：不同的一对应判 1，实得 $_r2" >&2; _bad=1; }
  [ "$_r3" = 2 ] || { echo "  ✗ 自检失败：空值应判 2，实得 $_r3" >&2; _bad=1; }
  [ "$_f0" = 0 ] || { echo "  ✗ 自检失败：装置在自检前就有失败计数" >&2; _bad=1; }
  [ "$_bad" = 0 ] && echo "SELFTEST 通过（3 格判词 + 起始计数为 0）" >&2 || exit 1
  exit 0
fi

BASE=(--mode crop --output-width 640 --output-height 360)

echo "── 两脚本应逐字相同的用例（CPU 轴）──" >&2
emit "默认(无质量参数)"            "${BASE[@]}"
emit "libx265"                     --codec libx265                "${BASE[@]}"
emit "libx265 --crf 20"            --codec libx265 --crf 20       "${BASE[@]}"
emit "libvpx-vp9 --crf 32"         --codec libvpx-vp9 --crf 32    "${BASE[@]}"
emit "libsvtav1 --crf 30"          --codec libsvtav1 --crf 30     "${BASE[@]}"
emit "libx264 --cq 23（换算成 crf）" --codec libx264 --cq 23     "${BASE[@]}"
emit "libx264 --cq 4（下界钳 1）"   --codec libx264 --cq 4        "${BASE[@]}"
emit "libx265 --cq 0（无损）"       --codec libx265 --cq 0        "${BASE[@]}"
emit "libx265 --crf 0（无损）"      --codec libx265 --crf 0       "${BASE[@]}"
emit "libx265 constqp --qp 18"     --codec libx265 --rc-mode constqp --qp 18 "${BASE[@]}"
emit "libx265 --crf-ref 21"        --codec libx265 --crf-ref 21  "${BASE[@]}"
emit "cover 模式"                  --mode cover --codec libx265   --output-width 640 --output-height 360
emit "crop-cover + ratio"          --mode crop-cover --crop-ratio 16:9 --output-width 640 --output-height 360
emit "--color-range pc"            --codec libx265 --color-range pc "${BASE[@]}"
emit "--pix-fmt yuv422p（显式）"    --codec libx265 --pix-fmt yuv422p "${BASE[@]}"
emit "--bit-depth 10（显式）"       --codec libx265 --bit-depth 10 "${BASE[@]}"
# ⚠ `--extra-args` 是 REMAINDER，**必须放在最后**（否则它会把后面的
# --output-width 之类一起吞掉，两边都产不出命令 → 被门判成"空命令"）。
# 另外这里刻意**不写**文档里的 `--extra-args -- X` 形式：Python 3.12 的
# argparse 在 nargs=REMAINDER 下不再容忍开头的 `--`，两个脚本都会报
# "unrecognized arguments: -- …"（既有 bug，另案）。
emit "--extra-args（必须最后）"      --codec libx265 --mode crop --output-width 640 \
                                     --output-height 360 --extra-args -max_muxing_queue_size 4096

echo >&2
if [ "$PAIRS" -eq 0 ]; then
  echo "✗ 一个用例都没比到（空集）—— 装置失效，不能报绿。" >&2
  exit 1
fi
if [ "$FAILS" -eq 0 ]; then
  echo "✓ 全部 $PAIRS 个用例：两脚本命令逐字相同。" >&2
  exit 0
fi
echo "✗ $PAIRS 个用例里有 $FAILS 个分叉（详见上面逐 token 差异）。" >&2
exit 1
