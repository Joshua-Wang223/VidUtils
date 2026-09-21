#!/usr/bin/env bash
# probe/probe_scale_cuda_crop.sh
# 目的：决定 vidcrop_hwaccel.py 要不要引入 scale_cuda
#   A) scale_cuda 之后 crop 看到的 iw/ih（PROBE_10BIT=1 补 A3/A4：10bit 下载格式 + 让位路径的 -profile:v main10）
#   B) cover 模式下 CPU 侧 scale 占总 wall time 的比例
#   B4) scale_cuda 是否接受 -2 这类表达式
#   Q) 画质对照（PSNR / VMAF，差异只来自缩放器；恒等缩放时 PSNR=inf → 显示成"逐位相同"）
#   C) 软解 + hwupload_cuda 链的 device 与 crop 尺寸协商（2026-09-20 新增）
#   D) 软解 + 上传链 vs 软解 + CPU 缩放 —— 这条新链值不值得留（未实测）
#   E) 软解 + 上传链的画质（vs CPU lanczos 参考）
# 用法:  bash probe/probe_scale_cuda_crop.sh [4K源视频]     # 不给则用 lavfi 自造
#        --target WxH / TARGET=WxH                         # 覆盖链的目标尺寸（默认 1440x1080）
#                                                          #   要测**真缩放**就选与源不同的尺寸：
#                                                          #   1080p 源 + 默认目标 = 恒等缩放（测不到东西）
#                                                          #   1080p 源 + --target 720x480 = 真降采样
#        PROBE_10BIT=1 bash ...                            # 追加 10bit/p010le 下载路径
#        REPS=5 bash ...                                   # 每个变体重复次数（默认 3）
#        Q_FRAMES=300 bash ...                             # 判据 Q 的对照帧数（默认 150）
#        PROBE_VMAF=1 bash ...                             # 判据 Q 追加 libvmaf
#        SELFTEST=1 bash ...                               # 只验证计时/汇总装置（CPU-only，无需 GPU）
# 只在有 NVIDIA GPU 的机器上跑（T4）。无 GPU 时说明原因并退出 2。

# ── 行尾自检（放在 set 之前，因为 CRLF 会让下一行的 set 直接失败退出）──
# CRLF 在 Linux 的 bash 上：`set -euo pipefail` 里 -e 先生效，随后 `pipefail\r`
# 报 "invalid option name" → -e 触发退出，脚本当场死掉（2026-09-20 T4 实测）。
# 而**本机 Git Bash 容忍 CR、能正常跑完** → 本机验证通过 ≠ 目标机能跑。
# 检测必须用 `grep -U`：不带 -U 时 grep 会做行尾翻译，CRLF 文件也报 0（本机实测）。
if grep -qU $'\r' "${BASH_SOURCE[0]}" 2>/dev/null
then
  echo "[ERROR] $(basename "${BASH_SOURCE[0]}") 是 CRLF 行尾，Linux 上跑不起来。"
  echo "        转 LF：sed -i 's/\r$//' <本脚本>   （或用 python: "
  echo "        pathlib.Path(f).write_bytes(pathlib.Path(f).read_bytes().replace(b'\r\n', b'\n'))）"
  exit 2
fi
set -euo pipefail

# ffmpeg/ffprobe 解析：优先 FFMPEG 环境变量 → 自建 7.1（含 scale_cuda）→ PATH。
# 不要写死 /usr/local/bin/ffmpeg —— 换台机器就报 "No such file or directory"，
# 而且错得很难读。选错了 ffmpeg 由下面的 scale_cuda 检查兜住。
FF=${FFMPEG:-}
if [[ -z "$FF" ]]; then
  if [[ -x /usr/local/bin/ffmpeg ]]; then
    FF=/usr/local/bin/ffmpeg
  else
    FF=$(command -v ffmpeg || true)
  fi
fi
[[ -n "$FF" ]] || { echo "找不到 ffmpeg；用 FFMPEG=/path/to/ffmpeg 指定"; exit 2; }
FP=${FFPROBE:-}
if [[ -z "$FP" ]]; then
  if [[ -x "$(dirname "$FF")/ffprobe" ]]; then
    FP="$(dirname "$FF")/ffprobe"     # 与 ffmpeg 同目录，保证版本一致
  else
    FP=$(command -v ffprobe || true)
  fi
fi
[[ -n "$FP" ]] || { echo "找不到 ffprobe；用 FFPROBE=/path/to/ffprobe 指定"; exit 2; }

REPS=${REPS:-3}
# 覆盖链目标尺寸。默认沿用历史值 1440x1080（4K 那几轮的数字都是它）。
# ⚠ 源高 == 目标高时缩放是**恒等操作**（只裁剪），B/D 测不到缩放成本、Q/E 测不出
#   缩放器差异，判据 D 的差值就只是上传开销。要测真缩放必须让目标与源不同。
TARGET=${TARGET:-1440x1080}
SRC_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)    TARGET=${2:?--target 需要 WxH，如 --target 720x480}; shift 2 ;;
    --target=*)  TARGET=${1#*=}; shift ;;
    -h|--help)   sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) [[ -z "$SRC_ARG" ]] && SRC_ARG=$1; shift ;;
  esac
done
T_W=${TARGET%x*}
T_H=${TARGET#*x*}
[[ "$T_W" =~ ^[0-9]+$ && "$T_H" =~ ^[0-9]+$ ]] \
  || { echo "[ERROR] --target 格式应为 WxH（如 1440x1080 / 720x480），收到 '$TARGET'"; exit 2; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# 工作目录放**仓库根的 temp/**（gitignored）：放在 probe/ 下会让探针产物
# （*.min / *.mkv / SELF_*）被 `git add probe/` 顺手带进仓库 —— 已经踩过一次。
# 探针不在仓库里跑时 dirname 只是个普通父目录，脚本照跑，产物落在它旁边。
WORK="$(dirname "$HERE")/temp/probe_scale_cuda"
mkdir -p "$WORK"

dim() { "$FP" -v error -select_streams v:0 -show_entries stream=width,height,pix_fmt \
        -of csv=p=0 "$1"; }

wall() {  # wall <标签> <ffmpeg args...> → stdout 秒数；失败则 stderr 报因并返回 1
  local tag=$1; shift; local t0 t1
  t0=$(date +%s.%N)
  if ! "$FF" -nostdin -y -hide_banner -loglevel warning -nostats -noautorotate \
        "$@" -f null - 2>"$WORK/$tag.err"; then
    echo "FAILED($tag): $(errline "$WORK/$tag.err")" >&2
    return 1
  fi
  t1=$(date +%s.%N)
  awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.3f", b-a}'
}

best() {  # best <标签> <ffmpeg args...> → 跑 REPS 次取最小，落盘 .min
  local tag=$1; shift; local -a ts=(); local v m
  for _ in $(seq "$REPS"); do
    v=$(wall "$tag" "$@" || true)          # 单个变体失败不该打断整轮
    [[ -n "$v" ]] && ts+=("$v")
  done
  if (( ${#ts[@]} == 0 )); then
    # **必须删掉上一轮留下的 .min**：否则汇总会把陈旧值当成这一轮的结果，算出
    # "CPU scale 净成本 -1.280s 占现行 -54.7%" 这类荒谬数字（2026-09-20 T4 实测：
    # 720p / 720x480 / 720x576 素材的 B2 全失败，汇总读到了 4K 那轮的值）。
    # 宁可让汇总显示"未产出"，也不要显示一个错的数。
    rm -f "$WORK/$tag.min"
    printf '  %-20s 全部失败（见 %s/%s.err）\n' "$tag" "$WORK" "$tag"
    return 0
  fi
  if (( ${#ts[@]} < REPS )); then
    printf '  %-20s ⚠ 只成功 %d/%d 次（其余见 %s/%s.err）\n' \
           "$tag" "${#ts[@]}" "$REPS" "$WORK" "$tag"
  fi
  m=$(printf '%s\n' "${ts[@]}" | sort -n | head -1)
  printf '  %-20s 全部: %s   min=%s\n' "$tag" "${ts[*]}" "$m"
  printf '%s\n' "$m" > "$WORK/$tag.min"
}

# ---------- 判词 / 汇总用的 awk 程序 ----------
# 单独放变量，好让 SELFTEST 用 `awk --posix` 把**同一段程序**预解析一遍。
# 为什么必须预解析：POSIX awk 不允许在 `:` 前换行（Newline 只允许跟在
# `, { && || do else` 之后），mawk（Ubuntu 默认 awk）会直接报
# "missing ) near end of line / syntax error at or near :"；而本机 Git Bash 的
# gawk 5.4.1 容忍这种跨行三元 → 本地 SELFTEST 照样过、到 T4 才炸。更糟的是脚本是
# `set -euo pipefail`，一个 awk 解析失败会把**后面的汇总与 VMAF 全部带走**
# （2026-09-20 实测踩过一次，T4 日志只剩三行 PSNR 就断了）。
# 规则：本文件里所有 awk 程序都别用跨行三元，判词用 if/else 逐行赋值。
AWK_Q_VERDICT='
BEGIN{
  if (b == "inf")    verdict = "✓ 两侧产物**逐位相同**（恒等缩放的必然结果，本判据无分辨力）"
  else if (b >= 40)  verdict = "✓ 基本复现（数值差异在噪声级，可放心用）"
  else if (b >= 0)   verdict = "⚠ 差异偏大：看下面的 VMAF，并考虑改用 CPU 链"
  else               verdict = "⚠ 未测到（看上面的 q_*.err）"
  if (b == "inf" || b < 0) printf "\n  判定：GPU lanczos vs CPU lanczos = %s → %s\n", b, verdict
  else                     printf "\n  判定：GPU lanczos vs CPU lanczos = %s dB → %s\n", b, verdict
}'
# VMAF 判词：PSNR ≥40 这个门槛**太松**——SD 放大素材实测 PSNR 48~51 dB 看着很好，
# 但 VMAF 只有 95~96.7（参见 CPU lanczos vs bicubic 的 98.3~98.8，两者同量级）。
# 所以 VMAF 要单独判，判读按分档给。
AWK_VMAF_VERDICT='
BEGIN{
  if (v == "")         verdict = "（没测到）"
  else if (v >= 99.9)  verdict = "✓ 差异不可见（缩小 / 恒等场景的常见值）"
  else if (v >= 99.0)  verdict = "✓ 差异基本不可见"
  else if (v >= 98.0)  verdict = "⚠ 可测量差异 —— 与 lanczos↔bicubic 的差异同量级，说明两种 lanczos 不是同一个重采样器"
  else                 verdict = "⚠ 差异明显（放大场景常见）：该素材可考虑改用 CPU 链"
  printf "  → VMAF 判读：%s\n", verdict
}'
AWK_SUMMARY='
BEGIN{
  if (t2 != "") printf "\n  CPU scale 净成本 (B1-B2)  = %+.3fs   占现行 %.1f%%\n", t1-t2, (t1-t2)/t1*100
  else          printf "\n  CPU scale 净成本 (B1-B2)  = N/A（B2 未产出：源比目标小，crop 隔离不了 scale）\n"
  printf "  改造净收益     (B1-B3)  = %+.3fs   占现行 %.1f%%\n", t1-t3, (t1-t3)/t1*100
  if (t1b != "") printf "  换 lanczos 使 CPU 变慢 (B1-B1b) = %+.3fs   占旧基准 %.1f%%（B1 是现在的基准，比旧 bicubic 慢这么多）\n", t1-t1b, (t1-t1b)/t1b*100
  printf "\n  判读：收益**随素材而异** —— CPU 缩放在总耗时里占得越多，显存内缩放省得越多。\n"
  printf "        所以**跨素材不可直接比较**：另两轮 4K 素材是 51.9%%~52.9%%，\n"
  printf "        而 short/低复杂度素材可能只有个位数（本轮 %.1f%%）。\n", (t1-t3)/t1*100
  printf "        要判断值不值得，请对照上面的时长/帧数，再看 B1 / B1b / B2 三行的绝对值。\n"
}'
# 判据 D 的判词：软解 + hwupload 链 到底比 软解 + CPU 缩放 快还是慢
AWK_UPLOAD_VERDICT='
BEGIN{
  d = t1 - t2
  pct = (t1 > 0) ? d / t1 * 100 : 0
  if (d > 0) verdict = "✓ 上传链更快，这条链值得留"
  else       verdict = "✗ 上传链并没有更快 —— 应当让 auto 缩放在软解时保守（不回退到 hwupload）"
  printf "\n  软解+CPU缩放 %.3fs  vs  软解+hwupload %.3fs  →  %+.3fs（%.1f%%）\n  %s\n", t1, t2, d, pct, verdict
}'

# 优先用 POSIX 严格模式（gawk --posix）；本机没有就退化为普通 awk
posix_awk() {
  if awk --posix 'BEGIN{}' >/dev/null 2>&1; then awk --posix "$@"; else awk "$@"; fi
}

# 从 ffmpeg 的 stderr 里挑"根因"那一行，而不是最后一行。
# ffmpeg 收尾常常只留一句结果性措辞（T4 日志里 C2 打出的就是
# "Nothing was written into output file, because at least one of its streams
#  received no packets."）—— 它只说"没写出东西"，不说为什么。
# 真正的原因在前面提到滤镜 / device / cuda 的那行，优先捞那行。
errline() {  # errline <err文件>
  local f=$1 line
  line=$(grep -m1 -iE 'hwupload|device|cuda|nvcuvid|scale_cuda|no such filter|impossible|invalid' \
         "$f" 2>/dev/null || true)
  if [[ -n "$line" ]]; then printf '%s' "$line"; else tail -n1 "$f"; fi
}

# 尺寸判读：给每格一个明确的 ✓ / ⚠，别让看日志的人自己对照数字猜。
# （A2 是靠"错误结果"来证明 bug 的，必须显式标注成预期，见判据 A 的注释。）
judge() {  # judge <实际 dim 输出> <期望前缀>
  local got=$1 want=$2
  case "$got" in
    "$want"*) printf '✓ 正确' ;;
    *)        printf "⚠ 与预期不符（期望 $want）" ;;
  esac
}

# 画质解析：定义在这里（而不是判据 Q 那一段）是因为 SELFTEST 要用它们做装置回归——
# SELFTEST 跑在文件开头，放在后面会 "command not found"。
q_psnr() {  # q_psnr <标签A> <标签B> → 原始值：数字 / inf / 空（空 = 真的没测到）
  local out
  out=$("$FF" -hide_banner -loglevel info -i "$WORK/q_$1.mkv" -i "$WORK/q_$2.mkv" \
        -lavfi psnr -f null - 2>&1 || true)
  # ⚠ 解析必须认 inf：恒等缩放（源尺寸 == 目标尺寸）时两侧产物**逐位相同**，ffmpeg
  #   打的是 `average:inf`；老写法 `grep -o 'average:[0-9.]*'` 只匹配到 `average:`，
  #   cut 完是空串 → 显示成 N/A，看起来像链路坏了（2026-09-20 T4 的 1080p 那两轮就是
  #   这么被误读的；本机实测复现：`PSNR y:inf u:inf v:inf average:inf ...`）。
  #   返回值保持**原始**（数字要留给 awk 做阈值比较），翻译交给 psnr_show()。
  printf '%s\n' "$out" | grep -oE 'average:[^ ]*' | tail -1 | cut -d: -f2
}

psnr_show() {  # psnr_show <q_psnr 的原始值> → 人能读的判读串
  case "$1" in
    inf) printf 'inf（两侧逐位相同 → 恒等缩放，本判据无分辨力）' ;;
    '')  printf 'N/A（未测到：psnr 滤镜协商失败或两侧帧数不等，看上面的 q_*.err）' ;;
    *)   printf '%s dB' "$1" ;;
  esac
}

# ---------- SELFTEST：只验证计时/汇总这套装置，CPU-only，不需要 GPU ----------
# 上机前先在本机跑一遍，确认 harness 没写错（免得 T4 那轮白跑）：
#   SELFTEST=1 bash probe/probe_scale_cuda_crop.sh
if [[ "${SELFTEST:-0}" == 1 ]]; then
  echo "── SELFTEST：验证 wall / best / awk 汇总装置（CPU-only，不需要 GPU）──"
  best SELF_a -f lavfi -i testsrc2=size=320x240:rate=25:duration=1 \
       -c:v libx264 -preset ultrafast
  best SELF_b -f lavfi -i testsrc2=size=320x240:rate=25:duration=1 \
       -c:v libx264 -preset ultrafast -vf scale=160:120
  if [[ -s "$WORK/SELF_a.min" && -s "$WORK/SELF_b.min" ]]; then
    awk -v t1="$(cat "$WORK/SELF_a.min")" -v t2="$(cat "$WORK/SELF_b.min")" 'BEGIN{
      printf "  装置可用：SELF_a=%.3fs SELF_b=%.3fs 差值 %+.3fs\n", t1, t2, t2-t1
      printf "  （加了一步 scale，两次应可分辨；只要不是 0 或空就说明计时有分辨力）\n"
    }'
  else
    echo "  ✗ 装置有问题：未产出 .min"; exit 1
  fi

  # 判词/汇总的 awk 程序必须先在 POSIX 模式下解析通过 —— T4 上是 mawk，
  # 本机是 gawk，gawk 容忍的写法 mawk 会拒绝。这里用假数据把**每一段**程序真跑一遍：
  # 既验语法，也验"喂进去能出东西"（空输出同样是装置故障）。
  # ⚠ 新增 awk 程序时**必须同步登记到这里**——判据 D 那次就是因为只登记了两段，
  # 第三段的守卫是空的。
  echo "  校验 awk 判词/汇总程序（POSIX 模式，抓 mawk 不兼容写法）…"
  # ⚠ 每段 awk 程序都要在这里登记，**且每个分支都要真的跑一遍**：只验语法不验分支的话，
  #   "t2 缺失"、"PSNR=inf" 这类只在异常路径上走的代码等于没测过（判据 D 那次就是因为
  #   只登记了两段、第三段的守卫是空的而漏过）。
  _q_out=$(posix_awk -v b=46.6 "$AWK_Q_VERDICT" 2>&1) || {
    echo "  ✗ AWK_Q_VERDICT 解析/执行失败（T4 的 mawk 会报同样错）：$_q_out"; exit 1; }
  _qi_out=$(posix_awk -v b=inf "$AWK_Q_VERDICT" 2>&1) || {
    echo "  ✗ AWK_Q_VERDICT(inf 分支) 解析/执行失败：$_qi_out"; exit 1; }
  _s_out=$(posix_awk -v t1=26.65 -v t1b=24.35 -v t2=19.09 -v t3=12.82 "$AWK_SUMMARY" 2>&1) || {
    echo "  ✗ AWK_SUMMARY 解析/执行失败（T4 的 mawk 会报同样错）：$_s_out"; exit 1; }
  _sn_out=$(posix_awk -v t1=26.65 -v t1b= -v t2= -v t3=12.82 "$AWK_SUMMARY" 2>&1) || {
    echo "  ✗ AWK_SUMMARY(缺失分支) 解析/执行失败：$_sn_out"; exit 1; }
  _u_out=$(posix_awk -v t1=27.19 -v t2=20.50 "$AWK_UPLOAD_VERDICT" 2>&1) || {
    echo "  ✗ AWK_UPLOAD_VERDICT 解析/执行失败（T4 的 mawk 会报同样错）：$_u_out"; exit 1; }
  _v_out=$(posix_awk -v v=95.04 "$AWK_VMAF_VERDICT" 2>&1) || {
    echo "  ✗ AWK_VMAF_VERDICT 解析/执行失败：$_v_out"; exit 1; }
  for _o in "$_q_out" "$_qi_out" "$_s_out" "$_sn_out" "$_u_out" "$_v_out"; do
    [[ -n "$_o" ]] || { echo "  ✗ awk 程序跑出了空输出"; exit 1; }
  done
  # 分支覆盖：异常路径也要产出**该产出的**判词，不能只是"没报错"
  grep -q '逐位相同'      <<<"$_qi_out" || { echo "  ✗ inf 分支没走到"; exit 1; }
  grep -q 'N/A（B2 未产出' <<<"$_sn_out" || { echo "  ✗ B2 缺失分支没走到"; exit 1; }
  grep -q '差异明显'      <<<"$_v_out" || { echo "  ✗ VMAF 低分档没走到"; exit 1; }
  echo "  ✓ 四段 awk 程序 POSIX 解析通过，且 inf / B2 缺失 / VMAF 低分档都走到了"
  printf '%s\n' "$_q_out" "$_qi_out" "$_s_out" "$_sn_out" "$_u_out" "$_v_out" \
    | sed 's/^/      /'

  # 装置级回归：q_psnr / psnr_show 的三态都要能正确翻译。
  # 为什么必须在这里测：`inf` 分支只有**恒等缩放**的素材才会走到（源尺寸==目标尺寸），
  # 上机时可能一次都遇不到 → 不测就等于没写（判据 Q 之前把 inf 显示成 N/A，就是这么来的）。
  echo "  校验 q_psnr / psnr_show 三态（数字 / inf / 空）…"
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
        -i 'testsrc2=size=320x240:rate=25:duration=1' -c:v ffv1 "$WORK/q_SELF_a.mkv" 2>/dev/null
  cp -f "$WORK/q_SELF_a.mkv" "$WORK/q_SELF_b.mkv"        # 逐位相同 → 期望 inf
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
        -i 'smptebars=size=320x240:rate=25:duration=1' -c:v ffv1 "$WORK/q_SELF_c.mkv" 2>/dev/null
  _p_inf=$(psnr_show "$(q_psnr SELF_a SELF_b)")
  _p_num=$(psnr_show "$(q_psnr SELF_a SELF_c)")
  _p_na=$(psnr_show "$(q_psnr SELF_a SELF_missing)")
  grep -q '逐位相同' <<<"$_p_inf" || { echo "  ✗ inf 态翻译错：$_p_inf"; exit 1; }
  grep -q 'dB'       <<<"$_p_num" || { echo "  ✗ 数字态翻译错：$_p_num"; exit 1; }
  grep -q 'N/A'      <<<"$_p_na"  || { echo "  ✗ 空态翻译错：$_p_na"; exit 1; }
  printf '      相同文件 → %s\n      不同画面 → %s\n      文件缺失 → %s\n' \
         "$_p_inf" "$_p_num" "$_p_na"

  # 装置级回归：best() 失败时必须删掉上一轮的 .min。
  # 不删的话汇总会把陈旧值当成这一轮结果（2026-09-20 T4：B2 全失败后打出
  # "CPU scale 净成本 -54.7%"）。
  echo "  校验 best() 失败时清理陈旧 .min…"
  printf '9.999\n' > "$WORK/SELF_fail.min"
  best SELF_fail -i "$WORK/definitely_missing_input.mp4" -f null - >/dev/null 2>&1
  [[ ! -e "$WORK/SELF_fail.min" ]] \
    || { echo "  ✗ best() 失败后 .min 仍然存在（汇总会读到陈旧值）"; exit 1; }
  echo "  ✓ best() 失败后已清理 .min"

  # 装置级回归：B2 的适用条件（源 ≥ 目标才用 crop 隔离 scale 成本）。
  # 条件写错就会让源比目标小的素材全线失败（720p / 720x480 / 720x576 都中过招）。
  _b2_cond() { (( $1 >= 1440 && $2 >= 1080 )) && echo run || echo skip; }
  [[ "$(_b2_cond 3840 2160)" == run  ]] || { echo "  ✗ B2 条件：4K 应为 run"; exit 1; }
  [[ "$(_b2_cond 1920 1080)" == run  ]] || { echo "  ✗ B2 条件：1080p 应为 run"; exit 1; }
  [[ "$(_b2_cond 1280  720)" == skip ]] || { echo "  ✗ B2 条件：720p 应为 skip"; exit 1; }
  [[ "$(_b2_cond  720  480)" == skip ]] || { echo "  ✗ B2 条件：720x480 应为 skip"; exit 1; }
  echo "  ✓ B2 适用条件（4K / 1080p → run；720p / 720x480 → skip）"

  # 装置级回归：所有读文件的 ffmpeg 调用都必须带 `-noautorotate`。
  # 不带的话，滤镜看到的是"旋转后的显示尺寸"，而 dim()/crop 用的是**存储尺寸**
  # → 两边打架：竖版素材（存储 1920x1080 + 90°）滤镜看到 1080x1920，
  # crop=1440:1080 直接报 "Invalid too big or non positive size"（2026-09-20 T4 实测）。
  # 本机造不出带 display matrix 的文件（ffmpeg 7 已移除 rotate metadata 写入），
  # 所以用 `-display_rotation 90` 模拟"输入带 90° 旋转"，跑**一正一反**两条：
  #   不带 -noautorotate → 素材被转成 1080x1920 → crop 1440 必然失败（对照组）
  #   带 -noautorotate   → 仍是 1920x1080   → 成功
  # 只测"成功"那条证明不了什么（crop 本来就会成功），必须有对照组。
  echo "  校验 -noautorotate（旋转坐标系对齐）…"
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
        -i 'testsrc2=size=1920x1080:rate=25:duration=0.2' -c:v libx264 -preset ultrafast \
        -pix_fmt yuv420p "$WORK/SELF_rot.mp4" 2>/dev/null
  _rot_crop='crop=1440:1080:0:0'
  if "$FF" -nostdin -y -hide_banner -loglevel error -display_rotation 90 \
        -i "$WORK/SELF_rot.mp4" -filter:v:0 "$_rot_crop" -frames:v 1 -f null - \
        >/dev/null 2>&1; then
    echo "  ✗ 对照组（无 -noautorotate）竟然成功了 —— -display_rotation 没生效，本回归无意义"
    exit 1
  fi
  "$FF" -nostdin -y -hide_banner -loglevel error -noautorotate -display_rotation 90 \
        -i "$WORK/SELF_rot.mp4" -filter:v:0 "$_rot_crop" -frames:v 1 -f null - \
        >/dev/null 2>&1 \
    || { echo "  ✗ 带 -noautorotate 时裁 1440x1080 失败 —— 旋转坐标系没对齐"; exit 1; }
  echo "  ✓ 对照组失败 / 带 -noautorotate 成功（证明它真的阻止了自动旋转）"
  exit 0
fi

# ---------- 前置检查：无 GPU 直说不装作能跑 ----------
command -v nvidia-smi >/dev/null 2>&1 \
    || { echo "没有 nvidia-smi：本机无 NVIDIA GPU，判据 A/B 都跑不了（本机只能 SELFTEST=1）"; exit 2; }
nvidia-smi -L >/dev/null 2>&1 \
    || { echo "nvidia-smi -L 失败（驱动 / 容器设备映射）"; exit 2; }
# 注意：不能写成 "$FF" -filters | grep -q —— grep 命中即退出会让 ffmpeg 收到
# SIGPIPE(141)，配合 set -o pipefail 会把"滤镜存在"误判成"不存在"。
# 先取回输出再匹配（本仓 install_ffmpeg_gpu_scale_plus_nvinterpolate.sh 踩过同款）。
FF_FILTERS=$("$FF" -hide_banner -filters 2>/dev/null || true)
grep -q ' scale_cuda ' <<<"$FF_FILTERS" \
    || { echo "$FF 里没有 scale_cuda（只有自建 7.1 有）；用 FFMPEG= 指定或先装"; exit 2; }

# 上传形态：优先 hwupload_cuda（自带 device，不需要 -filter_hw_device）。
# 若这个 ffmpeg 没编它，退到通用 hwupload + 显式 device —— 否则判据 D/E 会直接失败、
# 整轮什么都测不到。判据 C 会把两种形态都跑一遍做对照。
# （vidcrop_hwaccel 的 _probe_cuda_scale_upload() 只认 hwupload_cuda；
#   若 T4 这边落到通用形态，那边的功能探针会返回 False，功能自动不启用 —— 也要注意。）
if grep -q ' hwupload_cuda ' <<<"$FF_FILTERS"; then
  UPLOAD_VF='hwupload_cuda'
  UPLOAD_PRE=()
  echo "上传形态     : hwupload_cuda（自带 device，无需 -filter_hw_device）"
else
  UPLOAD_VF='hwupload'
  UPLOAD_PRE=(-init_hw_device cuda=cu:0 -filter_hw_device cu)
  echo "上传形态     : 通用 hwupload（本 ffmpeg 没有 hwupload_cuda → 改用显式 device）"
  echo "               ⚠ 此时 vidcrop_hwaccel 的功能探针会判定不可用，软解 GPU 缩放不会启用"
fi

# ---------- 素材：A 只需尺寸对错(1080p)，B 要最坏情况(4K) ----------
# 位深 → 下载格式：**不能硬编码 nv12**。10bit 源必须下载成 p010le，否则
# hwdownload 直接报 "Invalid output format nv12 for hwframe download"
# （2026-09-20 用 yuv420p10le 素材跑出来的就是这个错，把 B3/B4/Q 全打挂了）。
# 与 vidcrop_hwaccel 的 _src_download_fmt(src_bits) 对齐，那边是
# `nv12 if bits<=8 else ('p012le' if bits>=12 else 'p010le')` —— 12bit **及以上**
# 都归 p012le，所以 16bit 也必须走 p012le。早先只匹配 10/12bit，16bit 会掉进
# nv12 分支、与脚本分叉（本仓没有 16bit 素材，属预防性对齐）。
dl_fmt_of() {  # dl_fmt_of <pix_fmt> → nv12 / p010le / p012le
  case "$1" in
    *p16*|*16le*) printf 'p012le' ;;
    *p12*|*12le*) printf 'p012le' ;;
    *p10*|*10le*) printf 'p010le' ;;
    *)            printf 'nv12'   ;;
  esac
}
A_SRC="$WORK/a_1080p.mp4"
[[ -f "$A_SRC" ]] || "$FF" -nostdin -y -hide_banner -loglevel error \
    -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=4 \
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$A_SRC"
if [[ -n "$SRC_ARG" ]]; then
  B_SRC="$SRC_ARG"
else
  B_SRC="$WORK/b_4k.mp4"
  [[ -f "$B_SRC" ]] || "$FF" -nostdin -y -hide_banner -loglevel error \
      -f lavfi -i testsrc2=size=3840x2160:rate=25:duration=12 \
      -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$B_SRC"
fi
# B 素材的分辨率与位深都打印出来：判据 B/D 的结论**只对当前素材成立**，
# 之前那几轮是 4K/8bit，若换成 1080p 或 10bit 数字不可比（实测过：1080p 下
# 上传链反而慢 15.8%）。别让人拿不同素材的数字互相推翻。
B_DIM=$(dim "$B_SRC")
B_W=$(cut -d, -f1 <<<"$B_DIM")
B_H=$(cut -d, -f2 <<<"$B_DIM")
B_PIX=$(cut -d, -f3 <<<"$B_DIM")
DL_FMT=$(dl_fmt_of "$B_PIX")
# 源的旋转标签：`dim()` 用 ffprobe 读的是**存储尺寸**，而 ffmpeg 默认会**应用**旋转
# （滤镜看到的是显示尺寸）——两边不一致会让覆盖链算出错的 crop 尺寸（实测：
# 竖版素材的存储尺寸 1920x1080 + 90° 旋转 → 滤镜看到 1080x1920 → crop=1440:1080
# 报 "Invalid too big or non positive size"）。本探针所有 ffmpeg 调用都带
# `-noautorotate`，与 vidcrop_hwaccel 一致（它在 build_preserve_args 里就带了这个
# 加 -display_rotation），所以走**存储坐标系**；这里把旋转打出来，免得看日志的人
# 拿"1920x1080"去对竖版素材困惑。
B_ROT=$("$FP" -v error -select_streams v:0 -show_entries stream_side_data=rotation \
        -of csv=p=0 "$B_SRC" 2>/dev/null | head -1 || true)

# 覆盖几何：与 vidcrop 的 _build_cover_*_filter_str 完全一致 ——
#   源更宽 → 按目标高缩放（宽按比例取偶）后左右居中裁剪
#   源更高 → 按目标宽缩放（高按比例取偶）后上下居中裁剪
#   比例相同 → 只缩放、不裁剪
# 算出的 SW/SH 就是"缩放之后、裁剪之前"的尺寸，也是 CUDA 链要写进 scale_cuda 的
# 显式尺寸（脚本侧由 derive_even_dimension 算，这里用同一套取整：四舍五入后补偶）。
read -r SW SH Q_CP < <(awk -v sw="$B_W" -v sh="$B_H" -v tw="$T_W" -v th="$T_H" 'BEGIN{
  sr = sw / sh; dr = tw / th
  if (sr > dr + 0.001) {
    h = th; w = int(sw * h / sh + 0.5); if (w % 2) w += 1
    crop = sprintf("crop=%d:%d:(iw-%d)/2:0", tw, th, tw)
  } else if (sr < dr - 0.001) {
    w = tw; h = int(sh * w / sw + 0.5); if (h % 2) h += 1
    crop = sprintf("crop=%d:%d:0:(ih-%d)/2", tw, th, th)
  } else {
    w = tw; h = th; crop = ""
  }
  printf "%d %d %s\n", w, h, crop
}')
# 裁剪段（CPU 与 CUDA 两侧共用）；比例相同时为空，链里就只剩 scale

echo "A 素材 : $A_SRC  $(dim "$A_SRC")"
echo "B 素材 : $B_SRC  $B_DIM   (覆盖链 → ${T_W}x${T_H}；下载格式 $DL_FMT)"
# 时长与帧数：没有它们就无法解释"同样是 4K，一轮 26s、一轮 4s"这种量级差
# （实测遇到过）。nb_frames 有时是 N/A，打不出来就显示 ?。
B_DUR=$("$FP" -v error -show_entries format=duration -of default=nw=1:nk=1 \
        "$B_SRC" 2>/dev/null | head -1 || true)
B_NB=$("$FP" -v error -select_streams v:0 -show_entries stream=nb_frames \
       -of default=nw=1:nk=1 "$B_SRC" 2>/dev/null | head -1 || true)
echo "         时长 ${B_DUR:-?}s  帧数 ${B_NB:-?}  ← 跨素材比较耗时前先看这两个数"
echo "         缩放后/裁剪前尺寸: ${SW}x${SH}${Q_CP:+  裁剪: $Q_CP}"
if [[ -n "${B_ROT:-}" && "${B_ROT}" != 0 ]]; then
  echo "         ⚠ 源带 ${B_ROT}° 旋转标签：上面报的 ${B_W}x${B_H} 是**存储尺寸**，"
  echo "           显示（播放）尺寸是 ${B_H}x${B_W}。本探针按**存储坐标系**处理"
  echo "           （所有 ffmpeg 调用都带 -noautorotate，与 vidcrop_hwaccel 一致），"
  echo "           所以 B/D/Q 的尺寸与裁剪坐标都以存储尺寸为准 —— 不是探针看错了。"
fi
if [[ "$DL_FMT" != nv12 ]]; then
  echo "         ⚠ 非 8bit 源：下载格式取 $DL_FMT。**这条 10bit+ 路径此前从未实测**，"
  echo "           跑出来的结果（成功/失败/耗时）都是首轮数据，别当既有结论。"
fi
if ! grep -q '^3840,' <<<"$B_DIM"; then
  echo "         ⚠ B 素材不是 4K（实际 $(cut -d, -f1,2 <<<"$B_DIM")）：判据 B/D 的"
  echo "           吞吐数字只对该分辨率成立，与 4K 那几轮不可直接比较。"
fi
# 若"缩放后尺寸 == 源尺寸"，说明缩放是**恒等操作**（只裁剪）；
# 此时 B/D 测不到缩放成本、Q/E 也测不出缩放器差异 —— 判据 D 那个差值就只是
# "上传开销"本身（2026-09-20 用 1920x1080 源 + 1440x1080 目标跑出 -28.6%，就是这么来的）。
if [[ "$SW" == "$B_W" && "$SH" == "$B_H" ]]; then
  echo "         ⚠ 缩放是**恒等操作**（${B_W}x${B_H} → ${SW}x${SH}，只裁剪、不缩放）："
  echo "           判据 B/D 不含缩放成本，判据 Q/E 无法反映缩放质量。"
  echo "           改 --target 让目标与源不同（如 1080p 源用 --target 720x480），或换更高的源。"
fi

# ═══ 判据 A：crop 看到的 iw/ih ═══
# 探针 crop=iw/2:ih/2 —— 输出尺寸 = crop 看到尺寸的一半，无歧义：
#   看到 1280x720(正确) → 640x360
#   实测自动插入那一格出来的是 1280x720 —— 不是"尺寸回退成 1920x1080"，
#   而是 crop **整个被静默丢弃**（输出就是 scale_cuda 的结果，没裁过）。
#   先前的注释猜的是 960x540，被实测纠正过一次，这里按实测写。
#
# ⚠ 本判据里有**一格是刻意演示 bug 的**（A2 靠 FFmpeg 自动插入 hwdownload，
# crop 会被静默丢弃，所以输出 1280x720 而不是 640x360）。它和正确那一格打印出来
# 长得一模一样，必须显式标注，否则看日志的人会当成"也能跑"或当成故障。
# 统一用 judge_ok/judge_bug 给出判读，别让人自己猜。
echo; echo "═══ 判据 A：scale_cuda 后 crop 看到的 iw/ih（源 1920x1080 → scale_cuda 1280x720）═══"
echo "  （crop=iw/2:ih/2 → 期望 640x360；1280x720 表示 crop 被静默丢弃）"
probe_a() {  # probe_a <标签> <滤镜串> [判读: ok|bug]
  local tag=$1 vf=$2 expect=${3:-ok} out="$WORK/$1.mp4" got
  if "$FF" -nostdin -y -hide_banner -loglevel error \
        -hwaccel cuda -hwaccel_output_format cuda -noautorotate -i "$A_SRC" \
        -filter:v:0 "$vf" -c:v hevc_nvenc -cq 30 -an "$out" 2>"$WORK/$tag.err"; then
    got=$(dim "$out")
    if [[ "$expect" == bug ]]; then
      printf '  %-22s %-18s ← 预期如此：这就是「自动插入 hwdownload 会丢 crop」的证据\n' \
             "$tag" "$got"
    else
      printf '  %-22s %-18s %s\n' "$tag" "$got" "$(judge "$got" '640,360')"
    fi
  else
    printf '  %-22s FAILED: %s\n' "$tag" "$(errline "$WORK/$tag.err")"
  fi
}
probe_a A1_explicit_hwdownload 'scale_cuda=1280:720,hwdownload,format=nv12,crop=iw/2:ih/2'
probe_a A2_auto_hwdownload     'scale_cuda=1280:720,crop=iw/2:ih/2' bug
# 用 if 而不是 [[ ]] && cmd —— 条件为假时整句返回非 0，set -e 会直接退出脚本
if [[ "${PROBE_10BIT:-0}" == 1 ]]; then
  probe_a A3_10bit_p010 \
    'scale_cuda=1280:720:format=p010le,hwdownload,format=p010le,crop=iw/2:ih/2'

  # A4：验证「让位路径」真正下发的那条链。vidcrop_hwaccel 在零拷贝 CUDA 链上把
  # `--pix-fmt yuv420p10le` 交给 `--bit-depth 10` 时（"能落地者赢"），下发的是
  # `scale_cuda=…:format=p010le` **外加** `-profile:v main10`（_SCALE_CUDA_PROFILE）；
  # A3 只有 format=p010le、没有 -profile:v，所以验不到这一格。
  # 与 A3 是**单变量对照**（源、滤镜串、编码器、cq 全同，只差 `-profile:v main10`）：
  # 两格都成功才算"让位路径在 T4 上成立"。
  #
  # ⚠ 判据的性质必须说清，否则会被读成"profile 变了 ⇒ -profile:v 生效"：
  #   本格真正要验的是 ① **不失败**（-profile:v main10 与这条链、与 -cq 不打架）
  #   ② **输出仍是 yuv420p10le**（让位真的落地，没掉回 8bit nv12）。
  #   而 profile 那一项**没有分辨力**：10bit HEVC 必然 Main 10，不传 -profile:v 也一样
  #   —— 本机拿 libx265 做替身演练实测（with / without `-profile:v main10` 都得
  #   `Main 10,640,360,yuv420p10le`），所以**别把"profile 是 Main 10"当成
  #   -profile:v 生效的证据**（同款"恒等操作测不出东西"的坑）。
  #
  # ⚠ ffprobe 的 `-of csv=p=0` 字段顺序是**固定的 profile,width,height,pix_fmt**，
  #   不按 -show_entries 里的书写顺序（实测：请求 width,height,pix_fmt,profile 也把
  #   profile 排在最前），所以判据串得按这个顺序写。
  A4_OUT="$WORK/A4_10bit_profile.mp4"
  if "$FF" -nostdin -y -hide_banner -loglevel error \
        -hwaccel cuda -hwaccel_output_format cuda -noautorotate -i "$A_SRC" \
        -filter:v:0 'scale_cuda=1280:720:format=p010le,hwdownload,format=p010le,crop=iw/2:ih/2' \
        -c:v hevc_nvenc -cq 30 -profile:v main10 -an "$A4_OUT" 2>"$WORK/A4_10bit_profile.err"; then
    A4_DIM=$("$FP" -v error -select_streams v:0 \
             -show_entries stream=width,height,pix_fmt,profile -of csv=p=0 "$A4_OUT")
    printf '  %-22s %-30s %s\n' A4_10bit_profile "$A4_DIM" \
           "$(judge "$A4_DIM" 'Main 10,640,360,yuv420p10le')"
  else
    printf '  %-22s FAILED: %s\n' A4_10bit_profile "$(errline "$WORK/A4_10bit_profile.err")"
  fi
fi

# ═══ 判据 C：软解 + hwupload_cuda 链的 device 与 crop 尺寸协商 ═══
# 与判据 A 同一套「让输出尺寸自我报告」的手法，但这条链是**软件解码**：
# 帧在系统内存 → 必须先 hwupload_cuda 才能用 scale_cuda。
# 只查 `-filters` 有 scale_cuda 是不够的（本机 Windows 就是这样：滤镜在、没 N 卡），
# 所以 vidcrop_hwaccel 的 auto 缩放用**功能探针**判定，这里就是那个探针的完整版。
echo; echo "═══ 判据 C：软解 + hwupload_cuda → scale_cuda → hwdownload → crop ═══"
echo "  （判据同 A：crop=iw/2:ih/2 → 看到 1280x720 就该输出 640x360）"
probe_c() {  # probe_c <标签> <滤镜串> [前置参数...]
  local tag=$1 vf=$2 out="$WORK/$1.mp4" got
  shift 2
  if "$FF" -nostdin -y -hide_banner -loglevel error "$@" -noautorotate -i "$A_SRC" \
        -filter:v:0 "$vf" -c:v hevc_nvenc -cq 30 -an "$out" 2>"$WORK/$tag.err"; then
    got=$(dim "$out")
    printf '  %-28s %-18s %s\n' "$tag" "$got" "$(judge "$got" '640,360')"
  else
    printf '  %-28s FAILED: %s\n' "$tag" "$(errline "$WORK/$tag.err")"
  fi
}
probe_c C1_hwupload_cuda \
  'format=nv12,hwupload_cuda,scale_cuda=1280:720:interp_algo=lanczos,hwdownload,format=nv12,crop=iw/2:ih/2'
# 对照：通用 hwupload 不配 device **应当失败** —— 这一格是刻意演示的预期失败，
# 用来证明 hwupload_cuda 的"自带 device"是真优势。必须标注成"预期内"，
# 否则看日志的人会以为软解链路坏了。
if "$FF" -nostdin -y -hide_banner -loglevel error -noautorotate -i "$A_SRC" \
      -filter:v:0 'format=nv12,hwupload,scale_cuda=1280:720' -frames:v 1 -f null - \
      2>"$WORK/C2.err"; then
  printf '  %-28s 居然成功 —— ⚠ 与预期不符（本该缺 device 而失败）\n' C2_hwupload_no_device
else
  printf '  %-28s 失败（✓ 预期内）：%s\n' \
         C2_hwupload_no_device "$(errline "$WORK/C2.err")"
fi
# 对照：通用 hwupload + 显式 device（自建 7.1 若缺 hwupload_cuda 时的退路）
probe_c C3_hwupload_explicit_device \
  'format=nv12,hwupload,scale_cuda=1280:720:interp_algo=lanczos,hwdownload,format=nv12,crop=iw/2:ih/2' \
  -init_hw_device cuda=cu:0 -filter_hw_device cu

# ═══ 判据 B4：scale_cuda 是否接受 -2 表达式 ═══
echo; echo "═══ 判据 B4：scale_cuda 的 -2 表达式（vidcrop 现行链就是 scale=-2:1080）═══"
if "$FF" -nostdin -y -hide_banner -loglevel error \
      -hwaccel cuda -hwaccel_output_format cuda -noautorotate -i "$B_SRC" -frames:v 1 \
      -filter:v:0 "scale_cuda=-2:${T_H},hwdownload,format=$DL_FMT" -f null - 2>"$WORK/b4.err"; then
  echo "  ✓ 接受 -2 → patch 可直接沿用表达式写法"
else
  # 别急着把失败归给 -2：这一格的链里还有 hwdownload / -hwaccel cuda，其它环节先挂掉
  # 都会伪装成"-2 不被接受"。按报错内容区分根因：
  #   · 下载格式不对（10bit 源被硬写成 nv12）→ "Invalid output format ... for hwframe download"
  #   · **该编解码器在本机不能硬解**（如 T4 的 NVDEC 没有 AV1）→
  #     "Failed setup for format cuda: hwaccel initialisation returned error"
  #     这类源根本进不到 scale_cuda，此时判 -2 毫无意义（2026-09-20 T4 用 AV1 素材误报过）。
  if grep -qi 'invalid output format' "$WORK/b4.err"; then
    echo "  ✗ 无法判定 -2：下载格式先出了问题（不是 -2 的锅）"
  elif grep -qiE 'hwaccel initialisation returned error|failed setup for format cuda' \
         "$WORK/b4.err"; then
    echo "  ✗ 无法判定 -2：源在该机**不能硬解**（NVDEC 不支持该编解码器）——"
    echo "     这条链根本没走到 scale_cuda，换 H.264 / HEVC 素材再测 -2"
  else
    echo "  ✗ 不接受 -2 → 必须在 Python 侧算好偶数尺寸（derive_even_dimension 已有）"
  fi
  errline "$WORK/b4.err" | sed 's/^/    /'
fi

# ═══ 判据 B：cover 链 wall time ═══
# B1 现行（CPU lanczos）/ B1b 旧基准（CPU bicubic，看换档后 CPU 慢了多少）/
# B2 去掉 scale（隔离 CPU scale 成本）/ B3 新链（GPU scale）。
# 四者编码器输入都是 1440x1080，-f null 去掉磁盘 IO → 差异只来自 scale 那一步。
echo; echo "═══ 判据 B：$(cut -d, -f1,2 <<<"$B_DIM") → ${T_W}x${T_H} 覆盖链的 wall time（min of $REPS，-f null 去 IO）═══"
best B1_cur_hwaccel_auto -hwaccel auto -i "$B_SRC" \
     -filter:v:0 "scale=${SW}:${SH}:flags=lanczos${Q_CP:+,$Q_CP}" -c:v hevc_nvenc -cq 25 -an
best B1b_cur_bicubic -hwaccel auto -i "$B_SRC" \
     -filter:v:0 "scale=${SW}:${SH}:flags=bicubic${Q_CP:+,$Q_CP}" -c:v hevc_nvenc -cq 25 -an
# ⚠ B2 的手法（crop 到目标尺寸、不做缩放）**只对"源 ≥ 目标"的缩小场景成立**：
# 源比目标小时 crop 尺寸超过源，ffmpeg 直接报
# "Invalid too big or non positive size for width '1440' or height '1080'"，
# 于是 B2 全失败、汇总读到上一轮的陈旧 .min，算出"CPU scale 净成本 -54.7%"这种
# 荒谬值（2026-09-20 T4：720p / 720x480 / 720x576 素材全部中招）。
# 放大场景**没有**等价的"不缩放但同尺寸"写法（用 neighbor 就不是"无缩放"了），
# 所以这里干脆跳过并删掉旧值，让汇总显式打 N/A。
if (( B_W >= T_W && B_H >= T_H )); then
  best B2_no_scale -hwaccel auto -i "$B_SRC" \
       -filter:v:0 "crop=${T_W}:${T_H}:0:0" -c:v hevc_nvenc -cq 25 -an
else
  rm -f "$WORK/B2_no_scale.min"
  printf '  %-20s 跳过：源 %sx%s 小于目标 %sx%s，crop 无法用于放大场景（B1-B2 会显式打 N/A）\n' \
         B2_no_scale "$B_W" "$B_H" "$T_W" "$T_H"
fi
best B3_gpu_scale -hwaccel cuda -hwaccel_output_format cuda -i "$B_SRC" \
     -filter:v:0 "scale_cuda=${SW}:${SH}:interp_algo=lanczos,hwdownload,format=$DL_FMT${Q_CP:+,$Q_CP}" \
     -c:v hevc_nvenc -cq 25 -an

# ═══ 判据 D：软解 + hwupload 链值不值得留 ═══
# D1 软解 + CPU scale（现状替代方案）/ D2 软解 + hwupload_cuda + scale_cuda。
# 两者都是软件解码，唯一区别是重采样在哪 → 直接对比就是这条新链的净收益。
# （D3 对照 = 上面的 B3，硬解零拷贝；软解链不可能比它快，别拿它当基准。）
# ⚠ 这是判据 D 的**结论用途**：若 D2 并没有更快，`--scale-algo auto` 在软解时
#   就不该自动回退到 hwupload（见 project_cuda_scale_cover.md 的风险 1）。
echo; echo "═══ 判据 D：软解路径对照（$(cut -d, -f1,2 <<<"$B_DIM") → ${T_W}x${T_H}，min of $REPS，-f null 去 IO）═══"
best D1_sw_cpu_scale -i "$B_SRC" \
     -filter:v:0 "scale=${SW}:${SH}:flags=lanczos${Q_CP:+,$Q_CP}" \
     -c:v hevc_nvenc -cq 25 -an
# 空数组在 `set -u` 下的安全展开（旧 bash 直接 "${arr[@]}" 会报 unbound）
best D2_sw_hwupload ${UPLOAD_PRE[@]+"${UPLOAD_PRE[@]}"} -i "$B_SRC" \
     -filter:v:0 "format=$DL_FMT,${UPLOAD_VF},scale_cuda=${SW}:${SH}:interp_algo=lanczos,hwdownload,format=$DL_FMT${Q_CP:+,$Q_CP}" \
     -c:v hevc_nvenc -cq 25 -an
echo "  对照 D3：见上面的 B3_gpu_scale（硬解零拷贝，软解链不可能快过它）"
T1d=$(cat "$WORK/D1_sw_cpu_scale.min" 2>/dev/null || echo "")
T2d=$(cat "$WORK/D2_sw_hwupload.min"  2>/dev/null || echo "")
if [[ -n "$T1d" && -n "$T2d" ]]; then
  awk -v t1="$T1d" -v t2="$T2d" "$AWK_UPLOAD_VERDICT"
else
  echo "  判据 D 跳过：有变体未产出 .min"
fi

# ═══ 判据 Q：画质对照 ═══
# 三路各出一份【无损】ffv1（编码器不引入任何噪声 → 差异只来自缩放器）：
#   ref = CPU scale:flags=lanczos  ← 2026-09-20 起就是发货的 CPU 链，同时当参考
#   gpu = scale_cuda:interp_algo=lanczos + 显式 hwdownload,format=$DL_FMT（按源位深）
#   bic = CPU scale:flags=bicubic  ← 改动前的默认，仅用于看 lanczos 与旧默认差多少
# 固定 -frames:v N 从第 0 帧起（不做 -ss，避免三路起点错帧；
# psnr 滤镜长度不等/错帧都会把均值拉垮）。
Q_N=${Q_FRAMES:-150}
echo; echo "═══ 判据 Q：画质对照（前 $Q_N 帧，ffv1 无损，差异只来自缩放器）═══"
# 所有路都统一在末尾 `,format=yuv420p` 降到 8bit 再存盘。
# 为什么：10bit 源存成 yuv420p10le 后 `psnr` 滤镜协商失败 → 全部 N/A
# （2026-09-20 用 HLG 10bit 素材踩过）。libvmaf 也只认 8bit。
# 两侧同样降 8bit，比较仍然公平；8bit 源本来就是 yuv420p，这步是空操作，
# 所以不会影响之前 4K/8bit 那几轮的数字。
Q_8BIT=',format=yuv420p'
q_enc() {  # q_enc <标签> <输入与滤镜参数...>
  local tag=$1; shift
  "$FF" -nostdin -y -hide_banner -loglevel error -noautorotate "$@" \
        -frames:v "$Q_N" -an -c:v ffv1 "$WORK/q_$tag.mkv" 2>"$WORK/q_$tag.err" \
    || { echo "  q_$tag FAILED: $(errline "$WORK/q_$tag.err")"; return 1; }
}
# q_psnr / psnr_show 定义在文件前部（SELFTEST 要用它们做装置回归），这里直接调用。
q_enc ref -hwaccel auto -i "$B_SRC" -filter:v:0 "scale=${SW}:${SH}:flags=lanczos${Q_CP:+,$Q_CP}$Q_8BIT"
q_enc bic -hwaccel auto -i "$B_SRC" -filter:v:0 "scale=${SW}:${SH}:flags=bicubic${Q_CP:+,$Q_CP}$Q_8BIT"
q_enc gpu -hwaccel cuda -hwaccel_output_format cuda -i "$B_SRC" \
      -filter:v:0 "scale_cuda=${SW}:${SH}:interp_algo=lanczos,hwdownload,format=$DL_FMT${Q_CP:+,$Q_CP}$Q_8BIT"
# 软解 + 上传形态：**不加 -hwaccel**，帧是软件帧，靠 ${UPLOAD_VF} 上载
q_enc up ${UPLOAD_PRE[@]+"${UPLOAD_PRE[@]}"} -i "$B_SRC" \
      -filter:v:0 "format=$DL_FMT,${UPLOAD_VF},scale_cuda=${SW}:${SH}:interp_algo=lanczos,hwdownload,format=$DL_FMT${Q_CP:+,$Q_CP}$Q_8BIT"

P_GPU_REF=$(q_psnr gpu ref)
P_REF_BIC=$(q_psnr ref bic)
P_GPU_BIC=$(q_psnr gpu bic)
P_UP_REF=$(q_psnr up ref)
printf '  PSNR  新链(GPU lanczos) vs CPU lanczos   = %s   ← 主判据：两者同档，应基本一致\n' "$(psnr_show "$P_GPU_REF")"
printf '  PSNR  CPU lanczos vs CPU bicubic         = %s   ← lanczos 比旧默认锐多少\n' "$(psnr_show "$P_REF_BIC")"
printf '  PSNR  新链(GPU) vs CPU bicubic           = %s   ← 与改动前那条链的差异\n' "$(psnr_show "$P_GPU_BIC")"
printf '  PSNR  软解+上传链 vs CPU lanczos 参考     = %s   ← 判据 E：≥40 说明两条缩放实现一致\n' "$(psnr_show "$P_UP_REF")"
if [[ -n "$P_GPU_REF" ]]; then
  awk -v b="$P_GPU_REF" "$AWK_Q_VERDICT"
fi
if [[ "${PROBE_VMAF:-0}" == 1 ]]; then
  vmaf_of() {  # libvmaf 入参顺序是 [distorted][reference] → 数值（空表示没测到）
    local out
    out=$("$FF" -hide_banner -loglevel info -i "$WORK/q_$1.mkv" -i "$WORK/q_$2.mkv" \
          -lavfi '[0:v][1:v]libvmaf' -f null - 2>&1 || true)
    printf '%s\n' "$out" | grep -oE 'VMAF score: [0-9.]+' | tail -1 | cut -d' ' -f3
  }
  V_GPU=$(vmaf_of gpu ref)
  V_BIC=$(vmaf_of ref bic)
  echo "  VMAF  新链(GPU) vs CPU lanczos : ${V_GPU:-N/A}"
  echo "  VMAF  CPU lanczos vs bicubic   : ${V_BIC:-N/A}"
  awk -v v="$V_GPU" "$AWK_VMAF_VERDICT"
fi

# ═══ 汇总 ═══
T1b=$(cat "$WORK/B1b_cur_bicubic.min"     2>/dev/null || echo "")
T1=$(cat "$WORK/B1_cur_hwaccel_auto.min" 2>/dev/null || echo "")
T2=$(cat "$WORK/B2_no_scale.min"          2>/dev/null || echo "")
T3=$(cat "$WORK/B3_gpu_scale.min"         2>/dev/null || echo "")
# 只要求 B1 / B3（核心对照）。B2 / B1b 缺失是**正常情况**（放大场景 B2 直接跳过），
# 由 AWK_SUMMARY 逐行判空处理——老写法要求四个全有，会让一次 B2 跳过把整段汇总
# （含并不依赖 B2 的"改造净收益"）一起吞掉。
if [[ -n "$T1" && -n "$T3" ]]; then
  awk -v t1="$T1" -v t1b="$T1b" -v t2="$T2" -v t3="$T3" "$AWK_SUMMARY"
else
  echo; echo "  汇总跳过：B1 / B3 未产出 .min（先看上面的 FAILED 与 $WORK/*.err）"
fi
echo; echo "原始日志在 $WORK/*.err"
