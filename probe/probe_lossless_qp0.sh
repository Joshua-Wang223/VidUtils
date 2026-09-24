#!/usr/bin/env bash
# probe/probe_lossless_qp0.sh — 验「`-qp 0` / `-crf 0` 到底是不是**数学无损**」（2026-09-24）
#
# 为什么要单独验：脚本把 `--cq 0` 改写成 `-rc constqp -qp 0 -b:v 0` 并在提示里写
# 「真无损改写」，而这条说法**对任何 NVENC 编码器都成立**（含 `h264_nvenc`）——
# 但 NVIDIA 的 H.264 NVENC 按文档**没有**无损模式。这条声明从没被验证过。
#
# 原理（把结论钉在"编码器"这一层）：把滤镜链压成**像素恒等** —— `--mode crop` 且目标
# 尺寸 = 源尺寸、`--no-skip-same-size` 强制走一遍编码 ⇒ 既没缩放也没裁剪；源若是
# 8bit yuv420p（非 yuvj*）也不会插值域转换。于是**唯一可能丢信息的地方就是编码器**。
# 再逐帧比 `framemd5` 的**哈希列**（整行比会被 dts/pts/size 的容器差异假报不同）。
#
# 装置自带三格（缺一不可）：
#   正向格  `-qp 0`（本机替身 libx265 是 `-crf 0`）→ 相同 or 不同，**这正是要测的**
#   负向格  `-qp 18`（/`-crf 18`）→ **必须不同** —— 证明这套对比有分辨力（否则装置恒真）
#   附加格  换一个编码器跑 `-qp 0`（T4 上默认 h264_nvenc）→ 验"H.264 有没有无损"这条文档声明
#
# 用法：
#   上机（T4）：SRC='/workspace/.../xxx.mp4' bash probe/probe_lossless_qp0.sh
#   本机自证  ：LOCALCPU=1 SRC=temp/fixture_1080p.mp4 bash probe/probe_lossless_qp0.sh
#                （用 libx265 / libx264 跑**同一套装置**：libx265 的 lossless 应为真无损，
#                  所以本机这一跑同时充当"装置的正向格"——它通过了，上机的结论才可信）
#   装置自检  ：SELFTEST=1 bash probe/probe_lossless_qp0.sh   （只验判词与守卫，不动 ffmpeg）
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PY=${PY:-python}
SRC=${SRC:-}
WORK=${WORK:-temp/probe_lossless}
LOCALCPU=${LOCALCPU:-0}
if [ "$LOCALCPU" = "1" ]; then
  CODEC=${CODEC:-libx265}      # 本机替身：libx265 的 `-crf 0` + lossless=1 应为真无损
  CODEC_ALT=${CODEC_ALT:-libx264}
  QKNOB=crf
else
  CODEC=${CODEC:-hevc_nvenc}
  CODEC_ALT=${CODEC_ALT:-h264_nvenc}
  QKNOB=qp
fi

# ── 判词与守卫（与 ffmpeg 无关，可单独自检）─────────────────────────────────
_PAIRS=0
_fails=0

judge() {   # judge <标签> <same|diff> <expect:same|diff|?>  → 不符返回 1（但**总会打印**）
  local tag=$1 got=$2 want=$3 mark='✓' rc=0
  case "$want" in
    same) [ "$got" = same ] || { mark='✗'; rc=1; } ;;
    diff) [ "$got" = diff ] || { mark='✗'; rc=1; } ;;
  esac
  if [ "$want" = '?' ]; then
    # '?' = 只报告、不下断言：用「·」而不是对勾，免得被读成"判过了"
    printf '  · %-38s %s\n' "$tag" "$got"
  else
    printf '  %s %-38s %s（期望 %s）\n' "$mark" "$tag" "$got" "$want"
  fi
  return $rc
}

final_guard() {   # 0 = 可以报绿；1 = 空集 / 负向对照失败
  [ "${_PAIRS:-0}" -eq 0 ] && return 1      # 空集：什么都没比到 ≠ 通过
  [ "${_fails:-0}" -ne 0 ] && return 1
  return 0
}

if [ "${SELFTEST:-0}" = "1" ]; then
  echo "── SELFTEST：判词装置（正 / 负 / 空集 / 负向对照失败）──"
  bad=0
  _chk() { [ "$2" = "$3" ] && printf '  ✓ %s\n' "$1" || { printf '  ✗ %s（得到 %s，期望 %s）\n' "$1" "$2" "$3"; bad=1; }; }
  judge "期望 same 收到 same" same same;            _chk "自检·同格应判 0" "$?" 0
  judge "期望 same 收到 diff" diff same;            _chk "自检·异格应判 1" "$?" 1
  judge "期望 diff 收到 same" same diff;            _chk "自检·负向格该拦下" "$?" 1
  judge "期望 ? 收到任意"     diff '?';             _chk "自检·? 不该拦" "$?" 0
  _PAIRS=0; _fails=0; final_guard;                  _chk "自检·空集必须判失败" "$?" 1
  _PAIRS=3; _fails=1; final_guard;                  _chk "自检·负向失败必须判失败" "$?" 1
  _PAIRS=3; _fails=0; final_guard;                  _chk "自检·都过才报绿" "$?" 0
  [ "$bad" = 0 ] && echo "SELFTEST 通过（6 格）" || { echo "SELFTEST 失败"; exit 1; }
  exit 0
fi

# ── 前置检查（缺前提要响亮失败，不能静默跑出一片空白）──────────────────────
for t in ffmpeg ffprobe; do command -v "$t" >/dev/null || { echo "✗ 找不到 $t"; exit 2; }; done
[ -n "$SRC" ] && [ -f "$SRC" ] || { echo "✗ 源不存在：SRC='${SRC}'（用 SRC=<路径> 指定）"; exit 2; }
rm -rf "$WORK"; mkdir -p "$WORK"

read -r SW SH SPIX < <(ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,pix_fmt -of csv=p=0 "$SRC" | tr ',' ' ')
echo "源        : ${SW}x${SH}  ${SPIX}"
echo "编码器    : 主格=$CODEC   附加格=$CODEC_ALT   （LOCALCPU=${LOCALCPU}）"
case "${SW}${SH}" in
  *[13579]) echo "✗ 源是奇数尺寸（${SW}x${SH}）—— 4:2:0 系编码器要求偶数，换一个源再跑"; exit 2 ;;
esac
case "$SPIX" in
  yuvj*) echo "⚠ 源是 $SPIX（full-range）：滤镜链可能插一次值域转换，那种差异**不属于编码器**，结论会被带偏" ;;
esac

# ── 恒等裁剪转码：--mode crop + 目标=源尺寸 + --no-skip-same-size ──────────
# ⚠ 质量参数要**按模式组装**：`--rc-mode constqp` 只对 NVENC 有意义，而它与字面量
# `--crf` 是互斥的（2026-09-24 起报错退出 2）⇒ 本机替身走 `--crf N`、上机走
# `--rc-mode constqp --qp N`。第一版探针就是在这里写错、被"空集守卫"拦下的。
encode() {   # encode <输出名> <编码器> <0|18>
  local name=$1 codec=$2 q=$3
  local opts=() qa=(--"$QKNOB" "$q")
  if [ "$LOCALCPU" = "1" ]; then
    opts=(--decode cpu --scale-algo libswscale-lanczos)
  else
    qa=(--rc-mode constqp --"$QKNOB" "$q")
  fi
  "$PY" vidcrop_hwaccel.py --input "$SRC" --output "$WORK/$name" --overwrite \
    "${opts[@]}" --mode crop --output-width "$SW" --output-height "$SH" \
    --no-skip-same-size --no-chroma-check --codec "$codec" "${qa[@]}" \
    > "$WORK/$name.log" 2>&1
}
echo "── 转码（恒等裁剪：目标 = 源尺寸 + --no-skip-same-size）──"
encode main0 "$CODEC"     0  ; rc_a=$?
encode mainN "$CODEC"     18 ; rc_b=$?
encode alt0  "$CODEC_ALT" 0  ; rc_c=$?
echo "退出码    : main0=$rc_a  mainN=$rc_b  alt0=$rc_c（都应为 0）"

# ── 逐帧哈希（只取哈希列）──────────────────────────────────────────────────
hashes() { ffmpeg -v error -i "$1" -map 0:v:0 -f framemd5 - 2>/dev/null \
             | grep -v '^#' | awk -F', *' '{print $NF}' > "$2"; }
pick() { ls "$WORK/$1"/*.mp4 2>/dev/null | head -1; }
for pair in "src:$SRC" "main0:$(pick main0)" "mainN:$(pick mainN)" "alt0:$(pick alt0)"; do
  n=${pair%%:*}; f=${pair#*:}
  if [ -z "$f" ] || [ ! -f "$f" ]; then
    echo "⚠ $n 没有产出文件（看 $WORK/$n.log）—— 该格跳过"
  else
    hashes "$f" "$WORK/$n.md5"
  fi
done

echo "── 逐帧哈希对比（源 vs 产物）──"
# ⚠ 判据比的是 framemd5 的**哈希列**（纯像素），**帧数单独一行报** —— 这样时间戳/容器
#   层面的差异不会被混进"像素是否相同"的结论里。
# ⚠ 但**不要用 psnr**：实测（本机 ffmpeg N-122480，2026-09-24）对**逐位相同**的两份文件
#   （rawvideo 像素流 md5 相同、framemd5 全列也相同）psnr 仍报 average:26.0 dB，
#   而且**交换输入顺序会得到不同值**（26.0 vs 23.8）—— 说明这个构建下多输入 psnr 会按
#   两侧解码器的格式/范围插一次隐式转换，数字不可信。它给出的是**与事实相反**的结论
#   （"有损"），比报错危险得多。（同族的坑：psnr/libvmaf 只认 8bit。）

if [ -f "$WORK/src.md5" ]; then
  _cnt() { [ -f "$WORK/$1.md5" ] && printf '%s %s  ' "$1" "$(wc -l < "$WORK/$1.md5")"; }
  echo "帧数      : src $(wc -l < "$WORK/src.md5")  $(_cnt main0)$(_cnt mainN)$(_cnt alt0)"
fi
diffs_of() {   # diffs_of <名> → "K/N"（不同的帧数 / 总帧数）
  [ -f "$WORK/$1.md5" ] || { echo "—"; return; }
  local k
  k=$(diff "$WORK/src.md5" "$WORK/$1.md5" 2>/dev/null | grep -c '^<' || true)
  echo "${k}/$(wc -l < "$WORK/src.md5") 帧"
}

same_of() { diff -q "$WORK/src.md5" "$WORK/$1.md5" >/dev/null 2>&1 && echo same || echo diff; }
if [ -f "$WORK/main0.md5" ]; then
  got=$(same_of main0); _PAIRS=$((_PAIRS + 1))
  judge "${CODEC} --$QKNOB 0（正向格，结论见下）" "$got" '?'
  echo "      ⇒ 逐帧差异 $(diffs_of main0)：$([ "$got" = same ] && echo '数学无损**成立**' \
                             || echo '**不是**数学无损，只是"最高质量档"')"
fi
if [ -f "$WORK/mainN.md5" ]; then
  got=$(same_of mainN); _PAIRS=$((_PAIRS + 1))
  # 负向对照：18 档在任何编码器上都不该是无损 ⇒ 必须报"不同"。
  # 它一旦报"相同"，说明这套对比没有分辨力（装置恒真），后面的结论一律不算数。
  judge "${CODEC} --$QKNOB 18（负向对照，必须不同）" "$got" diff || _fails=$((_fails + 1))
  echo "      ⇒ 逐帧差异 $(diffs_of mainN)（应与总帧数接近；0 就说明装置失效）"
fi
if [ -f "$WORK/alt0.md5" ]; then
  got=$(same_of alt0); _PAIRS=$((_PAIRS + 1))
  judge "${CODEC_ALT} --$QKNOB 0（附加格）" "$got" '?'
  echo "      ⇒ 逐帧差异 $(diffs_of alt0)：$([ "$got" = same ] && echo '换的编码器也能做到无损' \
        || echo '**换的编码器做不到无损**：脚本/文档里对*任何*编码器都印「真无损改写」的说法要按编码器区分')"
fi

echo
if ! final_guard; then
  if [ "$_PAIRS" -eq 0 ]; then
    echo "✗ 一个用例都没比到（空集）—— 装置失效，不能报绿。看 $WORK/*.log"
  else
    echo "✗ 负向对照没通过 ⇒ 装置本身可疑，先别看正向结论"
  fi
  exit 1
fi
echo "✓ 装置有效（负向对照已确认有分辨力）；正向/附加格的结论见上面两行 ⇒ 文字。"
echo "  产物与逐帧哈希留在 $WORK/ 供复核。"
