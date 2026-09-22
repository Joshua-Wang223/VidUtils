#!/usr/bin/env bash
# probe/probe_green_chroma.sh
# 目的：定案「vidcrop_hwaccel.py crop 模式产物色度归零（画面全绿）」到底坏在哪一层
#   0) 源属性 + 源自身色度（基准）
#   1) 解码层：NVDEC 解出来的帧 U/V 是不是就已经是 0
#   2) 编码层：同样的解码 + crop，换编码器看是谁把色度弄丢的
#   3) 候选 1 的前提：纯 ffmpeg 加 -hwaccel_output_format cuda + 显式 hwdownload，
#      不改一行代码就能知道「显式下载」有没有用
#   4) 脚本矩阵：vidcrop_hwaccel.py 的 6 个组合
#   5) --dry-run 抓远程真实命令，与本地 mock 枚举的结果对照
# 用法（只在有 NVIDIA GPU 的机器上跑，T4）：
#   SRC=/path/原片.mp4 bash probe/probe_green_chroma.sh
#   SRC=... SS=600 SECS=3 bash ...        # 截取片段的位置/长度
#   REPO=$HOME/VidUtils PY=python3 bash ...
#   SELFTEST=1 bash ...                   # 无 GPU 也能跑：只验证取样/判词装置
# ⚠ 工装已转正入库（probe/），随 git pull 同步，不再需要手动拷到 T4。

# ── 行尾自检（放在 set 之前：CRLF 会让下一行的 set 当场失败退出）──
# 本机 Git Bash 容忍 CR、Linux 的 bash 不容忍 → 本机验证通过 ≠ 目标机能跑。
# 必须用 grep -U：不带 -U 时 grep 会做行尾翻译，CRLF 文件也报 0（本机实测过）。
if grep -qU $'\r' "${BASH_SOURCE[0]}" 2>/dev/null
then
  echo "[ERROR] $(basename "${BASH_SOURCE[0]}") 是 CRLF 行尾，Linux 上跑不起来。"
  echo "        转 LF：sed -i 's/\r\$//' <本脚本>"
  exit 2
fi
set -euo pipefail

# ffmpeg/ffprobe 解析：环境变量 → 自建 7.1 → PATH。别写死路径（换机器就报难读的错）。
FF=${FFMPEG:-}
if [[ -z "$FF" ]]; then
  if [[ -x /usr/local/bin/ffmpeg ]]; then FF=/usr/local/bin/ffmpeg
  else FF=$(command -v ffmpeg || true); fi
fi
[[ -n "$FF" ]] || { echo "找不到 ffmpeg；用 FFMPEG=/path/to/ffmpeg 指定"; exit 2; }
FP=${FFPROBE:-}
if [[ -z "$FP" ]]; then
  if [[ -x "$(dirname "$FF")/ffprobe" ]]; then FP="$(dirname "$FF")/ffprobe"
  else FP=$(command -v ffprobe || true); fi
fi
[[ -n "$FP" ]] || { echo "找不到 ffprobe；用 FFPROBE=/path/to/ffprobe 指定"; exit 2; }

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# 工作目录放**仓库根的 temp/**（gitignored），理由同 probe_scale_cuda_crop.sh：
# 放 probe/ 下会被 `git add probe/` 顺手带进仓库（SELFTEST 会真产出 mp4/mkv）。
WORK="$(dirname "$HERE")/temp/chroma_work"
mkdir -p "$WORK"

# 仓库根目录：REPO 环境变量 → 脚本所在目录的上一级（探针放在 <repo>/probe/ 时就是
# 仓库根）→ 两个常见候选。别只看 $HOME：T4 上仓库在 /workspace/VidUtils 而不是
# /root/VidUtils（2026-09-20 用户实测踩到，报 "…下没有 vidcrop_hwaccel.py"）。
if [[ -z "${REPO:-}" ]]; then
  if [[ -f "$(dirname "$HERE")/vidcrop_hwaccel.py" ]]; then
    REPO="$(dirname "$HERE")"
  elif [[ -f "$HOME/VidUtils/vidcrop_hwaccel.py" ]]; then
    REPO="$HOME/VidUtils"
  else
    REPO=/workspace/VidUtils
  fi
fi
echo "  仓库根: $REPO"
PY=${PY:-python3}
SS=${SS:-600}          # 从原片第几秒开始截
SECS=${SECS:-3}        # 截多长

# ---------- awk 程序（全部 POSIX 子集：T4 是 mawk，本机是 gawk）----------
# 规则：不许跨行三元（`?:` 前换行 mawk 直接语法错），判词一律 if/else 逐行赋值。
# 新增 awk 程序时必须同步登记到下面的 SELFTEST。
AWK_YUV='
{
  for (i = 1; i <= NF; i++) {
    if ($i ~ /signalstats\.(YAVG|UAVG|VAVG)=/) {
      k = $i
      sub(/.*signalstats\./, "", k)
      split(k, a, "=")
      n[a[1]]++
      s[a[1]] += a[2]
    }
  }
}
END {
  if (n["YAVG"] == 0 || n["UAVG"] == 0 || n["VAVG"] == 0) {
    printf "-\t-\t-\t✗ 无数据（signalstats 没输出，八成是命令失败了）\n"
    exit 0
  }
  y = s["YAVG"] / n["YAVG"]
  u = s["UAVG"] / n["UAVG"]
  v = s["VAVG"] / n["VAVG"]
  if (u < 16 && v < 16) vd = "✗ 色度归零（全绿）"
  else if (u < 96 || u > 160 || v < 96 || v > 160) vd = "⚠ 色度偏离中性区（正常应在 128 附近）"
  else vd = "✓ 正常"
  printf "%6.1f\t%6.1f\t%6.1f\t%s\n", y, u, v, vd
}'
AWK_VERDICT='
BEGIN{
  printf "\n  判读（按顺序命中第一条）：\n"
  if (us < 16 && vs < 16) {
    printf "   ⚠ 源本身就没有正常色度（U/V<16）→ 换素材重跑，本轮结论无效\n"
  } else if (a0 >= 0 && a0 < 16 && a1 >= 0 && a1 >= 16) {
    printf "   ⓪ -hwaccel auto 解码侧坏（显式 cuda 正常）—— 注意这条**已被 2026-09-21 第二轮推翻**：\n"
    printf "      那轮里 auto→nvdec、A0 正常，而脚本 --decode cuda 同样绿 → auto 不是元凶\n"
  } else if (b0 >= 0 && b0 < 16 && b1 >= 0 && b1 >= 16) {
    printf "   ⓪** -hwaccel auto + NVENC 组合坏（auto 单独解码正常）→ 落在编码/上载段\n"
  } else if (a1 >= 0 && a1 < 16) {
    printf "   ① NVDEC 解码层就已经是坏的（不经任何滤镜/编码器）\n"
    printf "      → 与 vidcrop 的代码无关；别去改 hwdownload，应查 NVDEC/驱动/源编码\n"
  } else if (b2 >= 0 && b2 < 16) {
    printf "   ② 硬解 + crop 之后、进入编码器之前就坏了（连软编 libx265 也绿）\n"
    printf "      → 查解码回传 / crop 这一段\n"
  } else if (b1 >= 0 && b1 < 16) {
    printf "   ③ 只在 hevc_nvenc 上坏（软编正常）→ NVENC 输入端上载/格式协商的问题\n"
  } else if (b1 < 0 && b2 < 0 && b3 < 0) {
    printf "   ⚠ 三个编码变体都没跑通，看上面的 FAILED 行\n"
  } else {
    printf "   ④ 手写的 ffmpeg 命令全部正常 → 差别只可能来自脚本那条命令里多带的选项组\n"
    printf "      → **看第 6 节的命令级二分**：V0 应复现绿色，逐组删选项后哪一格先恢复正常，\n"
    printf "        那一格删掉的组就是元凶（V8/V9 用来判「硬解 × 输出色彩四参」是否要成对出现）\n"
  }
  if (b1 >= 0 && b1 < 16 && c1 >= 0 && c1 >= 16) {
    printf "   候选 1 有救：加 -hwaccel_output_format cuda + 显式 hwdownload 后色度正常\n"
  } else if (b1 >= 0 && b1 < 16 && c1 >= 0 && c1 < 16) {
    printf "   候选 1 无效：显式 hwdownload 之后仍然绿 → 别改 2826 行，问题在别处\n"
  }
  printf "   （数字口径：U/V 正常在 128 附近；本缺陷实测 U=V≈0.01）\n"
}'

posix_awk() {   # 本机 gawk --posix 能复现 mawk 的严格解析；没有就退化为普通 awk
  if awk --posix 'BEGIN{}' >/dev/null 2>&1; then awk --posix "$@"; else awk "$@"; fi
}

errline() {  # 捞根因行，不要 tail -n1（ffmpeg 收尾只留结果性措辞）
  local f=$1 line
  # 先捞带 error/invalid/failed 的行（argparse 的 "error: ..." 也在这里命中）；
  # 再退到滤镜/device 关键字。**顺序不能反**：ffmpeg --help/usage 文本里就含 "cuda"，
  # 先匹配关键字会捞到 usage 的一行（本机实测捞到过 "[--cuda-diagnostics]"）。
  line=$(grep -m1 -iE 'error|invalid|no such|failed|cannot|unable|not supported' \
         "$f" 2>/dev/null || true)
  if [[ -z "$line" ]]; then
    line=$(grep -m1 -iE 'hwupload|device|cuda|nvcuvid|cuvid|nvenc|impossible' \
           "$f" 2>/dev/null || true)
  fi
  if [[ -n "$line" ]]; then printf '%s' "$line"; else tail -n1 "$f"; fi
}

# backend_of <日志>：从 verbose 日志里认 auto 被解析成了哪个后端
backend_of() {
  local f=$1 b
  b=$(grep -oiE 'cuvid|nvdec|vdpau|vaapi|opencl|vulkan|qsv|d3d11va|drm' "$f" 2>/dev/null \
      | tr 'A-Z' 'a-z' | sort -u | tr '\n' ' ' || true)
  if [[ -n "$b" ]]; then printf '%s' "$b"; else printf '未能判断（完整日志：%s）' "$f"; fi
}

# chk <标签> <文件>：对产物取样，输出一行并把数值存进 LAST_*
# ⚠ 取值管道末尾必须有 `|| true`：脚本是 `set -o pipefail`，只要**被测文件不存在**
# （或 ffmpeg 因任何原因失败），`line=$(ffmpeg ... | awk ...)` 整条赋值就是非零，
# `set -e` 会**当场杀掉整个探针**，后面所有格子一个都不跑、且不留任何报错。
# 2026-09-21 本机自测时踩到：报错只有一个 `exit=127`、trace 还因缓冲被截断。
_sanitize() {   # 把「无数据」行的 `-` 归一成 -1，免得下游 awk 把它当数值比较
  # ⚠ 必须允许**前导空格**：AWK_YUV 用 `%6.1f` 输出，小于 100 的值会带一个空格
  # （` 127.9`），写成 `^[0-9]` 会把好数据也判成"无数据"、整表变成 -1（实测踩到）。
  if [[ ! "$LAST_U" =~ ^[[:space:]]*[0-9] ]]; then LAST_U=-1; LAST_V=-1; fi
}

chk() {
  local tag=$1 f=$2 line
  line=$("$FF" -nostdin -hide_banner -loglevel info -ss 1 -i "$f" -frames:v 2 -an \
        -filter:v:0 'format=yuv420p,signalstats,metadata=print' -f null - 2>&1 \
        | awk "$AWK_YUV" || true)
  IFS=$'\t' read -r LAST_Y LAST_U LAST_V LAST_VD <<<"$line"
  _sanitize
  printf '  %-26s Y=%-6s U=%-6s V=%-6s %s\n' "$tag" "$LAST_Y" "$LAST_U" "$LAST_V" "$LAST_VD"
}

# chkd <标签> <文件> [前置 ffmpeg 参数...]：用指定参数【解码】取样（不落盘）
chkd() {
  local tag=$1 f=$2; shift 2
  local line
  line=$("$FF" -nostdin -hide_banner -loglevel info "$@" -ss 1 -i "$f" -frames:v 2 -an \
        -filter:v:0 'format=yuv420p,signalstats,metadata=print' -f null - 2>&1 \
        | awk "$AWK_YUV" || true)
  IFS=$'\t' read -r LAST_Y LAST_U LAST_V LAST_VD <<<"$line"
  _sanitize
  printf '  %-26s Y=%-6s U=%-6s V=%-6s %s\n' "$tag" "$LAST_Y" "$LAST_U" "$LAST_V" "$LAST_VD"
}

# enc <标签> <vf> <codec> [前置 ffmpeg 参数...]：转码后取样
enc() {
  local tag=$1 vf=$2 cv=$3; shift 3
  local out="$WORK/e_$tag.mp4" q
  rm -f "$out"
  if [[ "$cv" == libx* ]]; then q=(-preset ultrafast -crf 22); else q=(-preset p4 -cq 23); fi
  if "$FF" -nostdin -y -hide_banner -loglevel error "$@" -i "$SEG" \
        -filter:v:0 "$vf" -c:v "$cv" "${q[@]}" -an "$out" 2>"$WORK/e_$tag.err"; then
    chk "$tag" "$out"
  else
    LAST_U=-1; LAST_V=-1
    printf '  %-26s FAILED: %s\n' "$tag" "$(errline "$WORK/e_$tag.err")"
  fi
}

# scrun <标签> [脚本参数...]：跑 vidcrop_hwaccel.py 并取样
scrun() {
  local tag=$1; shift
  local out="$WORK/s_$tag.mp4" strat
  rm -f "$out"
  # 注意：脚本只有长选项 --input / --output（没有 -i / -o），写短选项会直接
  # 报 "the following arguments are required: --input, --output"。
  "$PY" "$REPO/vidcrop_hwaccel.py" --input "$SEG" --output "$out" --mode crop \
      --output-width 768 --output-height 432 --overwrite "$@" \
      >"$WORK/s_$tag.out" 2>&1 || true
  strat=$(grep -m1 '策略' "$WORK/s_$tag.out" | sed 's/^ *//' || true)
  if [[ -f "$out" ]]; then
    chk "$tag" "$out"
  else
    LAST_U=-1; LAST_V=-1
    printf '  %-26s 无产物（%s）\n' "$tag" "$(errline "$WORK/s_$tag.out")"
  fi
  [[ -n "$strat" ]] && printf '  %-26s ↳ %s\n' '' "$strat"
  return 0
}

# ---------- SELFTEST：无 GPU 也要能验证这一套装置 ----------
if [[ "${SELFTEST:-0}" == 1 ]]; then
  echo "── SELFTEST：验证取样 / 判词装置（CPU-only，不需要 GPU）──"
  _t="$WORK/selftest.mp4"
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
        -i testsrc2=size=320x240:rate=25:duration=2 -c:v libx264 -preset ultrafast \
        -pix_fmt yuv420p "$_t"
  echo "  取样一个正常文件（应当 U/V 在 128 附近）："
  chk SELF_normal "$_t"
  echo "  取样一个人造的「色度归零」文件（应当判成全绿）："
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
        -i color=c=gray:s=320x240:d=2 -c:v libx264 -preset ultrafast -pix_fmt gray \
        "$WORK/selftest_gray.mp4"
  chk SELF_gray "$WORK/selftest_gray.mp4"   # 灰度片：理论上是 U=V=128，不该判绿
  # 正向：真·零色度必须被判成「全绿」。只测"正常片不误伤"是不够的——
  # 一个恒返回 ✓ 的检测器也能通过那一格（空值/恒真是最隐蔽的假阳性）。
  "$FF" -nostdin -y -hide_banner -loglevel error -f lavfi \
        -i testsrc2=size=320x240:rate=25:duration=2 -c:v libx264 -preset ultrafast \
        -pix_fmt yuv420p -filter:v 'format=yuv420p,lutyuv=u=0:v=0' \
        -color_range tv "$WORK/selftest_zero.mp4"
  echo "  取样一个人造的「零色度」文件（必须判成全绿，否则检测器是坏的）："
  chk SELF_zerochroma "$WORK/selftest_zero.mp4"
  echo "  校验 awk 程序（POSIX 模式，抓 mawk 不兼容写法）…"
  _y=$(posix_awk "$AWK_YUV" <<<'lavfi.signalstats.UAVG=0.01 lavfi.signalstats.YAVG=100 lavfi.signalstats.VAVG=0.01' 2>&1) || {
    echo "  ✗ AWK_YUV 解析/执行失败（T4 的 mawk 会报同样错）：$_y"; exit 1; }
  _v=$(posix_awk -v us=105 -v vs=122 -v a1=0.01 -v b1=0.01 -v b2=0.01 -v b3=105 -v c1=105 \
       "$AWK_VERDICT" 2>&1) || {
    echo "  ✗ AWK_VERDICT 解析/执行失败（T4 的 mawk 会报同样错）：$_v"; exit 1; }
  [[ -n "$_y" && -n "$_v" ]] || { echo "  ✗ awk 程序跑出了空输出"; exit 1; }
  echo "  ✓ 两段 awk 程序 POSIX 解析通过且有输出（-v 必须写在程序文本之前）"
  printf '%s\n' "$_v" | sed 's/^/    /'
  exit 0
fi

# ---------- 前置检查 ----------
# LOCALCPU=1：无 GPU 的机器上只跑 CPU 那几格，用来在**上机前**验证 enc/scrun 这套
# harness 本身（SELFTEST 只验取样与判词，验不到函数）。T4 上不要加这个开关。
gpu_ok() { [[ "${LOCALCPU:-0}" != 1 ]]; }
if gpu_ok; then
  command -v nvidia-smi >/dev/null 2>&1 \
    || { echo "没有 nvidia-smi：本机无 NVIDIA GPU，只能跑 SELFTEST=1 或 LOCALCPU=1"; exit 2; }
  nvidia-smi -L >/dev/null 2>&1 || { echo "nvidia-smi -L 失败（驱动 / 容器设备映射）"; exit 2; }
else
  echo "⚠ LOCALCPU=1：跳过所有需要 GPU 的格子（A1/B1/B2/C1 记为未跑=-1）"
fi
SRC=${SRC:-}
[[ -n "$SRC" && -f "$SRC" ]] || { echo "用 SRC=/path/原片.mp4 指定源文件"; exit 2; }
[[ -f "$REPO/vidcrop_hwaccel.py" ]] || { echo "$REPO 下没有 vidcrop_hwaccel.py；用 REPO= 指定"; exit 2; }

echo "═══ 0. 源属性与基准色度 ═══"
"$FP" -v error -select_streams v:0 -show_entries \
      stream=codec_name,profile,pix_fmt,width,height,bits_per_raw_sample -of csv=p=0 "$SRC" \
  | sed 's/^/  源: /'
SRC_PIX=$("$FP" -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 "$SRC")
case "$SRC_PIX" in
  *p12*|*p012*) DL_FMT=p012le ;;
  *p10*|*p010*) DL_FMT=p010le ;;
  *)            DL_FMT=nv12   ;;
esac
echo "  推导的 hwdownload 下载格式: $DL_FMT"
# 探针用的 ffmpeg（/usr/local/bin/ffmpeg）与脚本用的（PATH 里的 ffmpeg）可能不是同一个
# 二进制 —— 这是除 -hwaccel 之外的第二个变量，必须显式摆出来。
PATH_FF=$(command -v ffmpeg || true)
echo "  探针用的 ffmpeg : $FF  $("$FF" -version 2>/dev/null | head -1 | awk '{print $3}')"
echo "  脚本用的 ffmpeg : ${PATH_FF:-未找到}  $("${PATH_FF:-$FF}" -version 2>/dev/null | head -1 | awk '{print $3}')"
SRC_H=$("$FP" -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$SRC")
CROP_Y=$(( (SRC_H - 432) / 2 )); (( CROP_Y < 0 )) && CROP_Y=0
echo "  居中裁剪 y 偏移 : $CROP_Y（与脚本 _build_crop_filter_str 同一算法）"

SEG="$WORK/seg.mp4"
"$FF" -nostdin -y -hide_banner -loglevel error -ss "$SS" -t "$SECS" -i "$SRC" \
      -c copy -map 0:v:0 "$SEG" 2>"$WORK/seg.err" \
  || { echo "  -ss $SS 截取失败（源可能比 $SS 秒短），改用开头 3 秒"; \
       "$FF" -nostdin -y -hide_banner -loglevel error -t "$SECS" -i "$SRC" \
             -c copy -map 0:v:0 "$SEG"; }
echo "  片段: $SEG（$SECS 秒，纯 -c copy，不重新编码）"
chk '源[CPU解码,基准]' "$SEG"
US=$LAST_U; VS=$LAST_V

echo; echo "═══ 1. 解码层：NVDEC 解出来的帧是不是就已经是坏的 ═══"
# ⚠ 这一格的来历（两轮实测的修正）：
#   第一轮（09-20）：探针手写的 -hwaccel cuda 全 ✓，脚本（旧版策略 2）的 -hwaccel auto 是 ✗
#     → 当时判定「auto 是元凶」。**该结论已被第二轮推翻。**
#   第二轮（09-21，HEAD 已支持 --decode）：auto→nvdec、A0 正常，而脚本显式 --decode cuda
#     **同样绿** → ffmpeg 那一层（含 auto）全清白，元凶在脚本命令多带的选项里（见第 6 节）。
#   保留 A0 这一格是因为"auto 解析成哪个后端"本身仍值得记录，而不是因为它可疑。
if gpu_ok; then
  chkd A0_硬解auto仅解码 "$SEG" -hwaccel auto
  A0U=$LAST_U
  # auto 到底被 ffmpeg 解析成了哪个后端？（按枚举顺序 VDPAU 在 CUDA 之前，
  # 所以"auto"未必等于 cuda —— 这正是不能把它丢给 ffmpeg 的理由）
  "$FF" -nostdin -hide_banner -loglevel verbose -hwaccel auto -i "$SEG" \
        -frames:v 2 -an -f null - 2>"$WORK/auto_backend.err" || true
  printf '  %-26s %s\n' 'auto→实际后端' "$(backend_of "$WORK/auto_backend.err")"
else
  A0U=-1; echo "  A0_硬解auto仅解码            （跳过：无 GPU）"
fi
if gpu_ok; then
  chkd A1_硬解仅解码 "$SEG" -hwaccel cuda -hwaccel_device 0
  A1U=$LAST_U
else
  A1U=-1; echo "  A1_硬解仅解码              （跳过：无 GPU）"
fi
chkd A2_软解仅解码 "$SEG"
A2U=$LAST_U

echo; echo "═══ 2. 编码层：同解码 + crop，换编码器（谁把色度弄丢的）═══"
CROP="crop=768:432:0:$CROP_Y"     # 与脚本发出的裁剪参数逐字一致
if gpu_ok; then
  enc B0_auto+crop+nvenc   "$CROP" hevc_nvenc -hwaccel auto
  B0U=$LAST_U
  enc B0b_auto+crop+libx265 "$CROP" libx265   -hwaccel auto
  B0BU=$LAST_U
  enc B1_硬解+crop+nvenc   "$CROP" hevc_nvenc -hwaccel cuda -hwaccel_device 0
  B1U=$LAST_U
  enc B2_硬解+crop+libx265 "$CROP" libx265    -hwaccel cuda -hwaccel_device 0
  B2U=$LAST_U
else
  B0U=-1; B0BU=-1; B1U=-1; B2U=-1; echo "  B0/B0b/B1/B2               （跳过：无 GPU）"
fi
enc B3_软解+crop+nvenc   "$CROP" hevc_nvenc
B3U=$LAST_U

echo; echo "═══ 3. 候选 1 的前提（纯 ffmpeg，不改代码）：显式 hwdownload 有没有用 ═══"
if gpu_ok; then
  enc C1_显式hwdownload+nvenc \
      "hwdownload,format=$DL_FMT,$CROP" hevc_nvenc \
      -hwaccel cuda -hwaccel_device 0 -hwaccel_output_format cuda
  C1U=$LAST_U
else
  C1U=-1; echo "  C1_显式hwdownload+nvenc    （跳过：无 GPU）"
fi

echo; echo "═══ 4. 脚本矩阵：vidcrop_hwaccel.py（768x432 crop）═══"
# T4 上跑的可能是旧版（没有 --decode，策略 2 硬写 -hwaccel auto）——先把版本摆出来，
# 再按版本把「解码轴」翻译成它认的参数名，否则整节都是 unrecognized arguments。
DECODE_NEW=1
if ! "$PY" "$REPO/vidcrop_hwaccel.py" --help 2>&1 | grep -q -- '--decode'; then DECODE_NEW=0; fi
dec() {  # dec <auto|cpu|cuda> → 该版本对应的两个参数
  local v=$1
  if (( DECODE_NEW )); then
    printf '%s %s' --decode "$v"
  else
    if [[ "$v" == cpu ]]; then printf '%s %s' --hwaccel none
    else printf '%s %s' --hwaccel "$v"; fi
  fi
}
echo "  脚本 HEAD: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo 未知)"
if (( DECODE_NEW )); then
  echo "  支持 --decode：是（74ed684+，auto 会先探测再下发具体后端）"
else
  echo "  支持 --decode：**否** → 旧版，策略 2 硬写 -hwaccel auto；"
  echo "                 先 `git -C $REPO pull` 到 HEAD 再跑一遍 default，看是否已修"
fi
if gpu_ok; then
  scrun default
  scrun decode-cpu     $(dec cpu)
  scrun decode-cuda    $(dec cuda)
  scrun codec-libx265  --codec libx265
  scrun hwdec-swenc    $(dec cuda) --codec libx265
else
  scrun decode-cpu     $(dec cpu)
fi
scrun all-cpu        $(dec cpu) --codec libx265

echo; echo "═══ 5. 远程真实命令（--dry-run，与本地 mock 枚举对照）═══"
"$PY" "$REPO/vidcrop_hwaccel.py" --input "$SEG" --output "$WORK/dry.mp4" --mode crop \
    --output-width 768 --output-height 432 --codec hevc_nvenc --overwrite --dry-run 2>&1 \
  | grep -E '策略|执行命令' | sed 's/^/  /' || true

# ═══ 6. 命令级二分：拿脚本自己吐出来的那条真命令，逐组删选项再跑 ═══
# 为什么需要这一节（2026-09-21 T4 第二轮）：第 1~3 节里**纯手写的 ffmpeg 命令全部正常**，
# 而脚本的 default 与 --decode cuda **两种解码参数都绿** —— 说明既不是 -hwaccel auto，
# 也不是 ffmpeg/NVDEC/NVENC 本身，而是脚本那条命令里**多带的某几组选项**。
# 手写对照测不出来（我写的 B1 少了 6 组选项就正常了），所以这里改为：
# 直接把 dry-run 打印的命令字符串拿来做输入，成组删除后重跑，谁删了谁恢复就是谁。
echo; echo "═══ 6. 命令级二分：照脚本自己的命令成组删选项（含 6b 全组合穷举）═══"
if [[ -f "$REPO/vidcrop_hwaccel.py" ]]; then
  CMD=$("$PY" "$REPO/vidcrop_hwaccel.py" --input "$SEG" --output "$WORK/dry.mp4" \
        --mode crop --output-width 768 --output-height 432 --codec hevc_nvenc \
        --overwrite --dry-run 2>&1 \
        | grep -m1 '执行命令' | sed -E 's/^.*执行命令[^:]*: //' || true)
  if [[ -z "$CMD" ]]; then
    echo "  ✗ 没能从 --dry-run 输出里解析出「执行命令」（看上面的打印）"
  else
    echo "  抓到的原命令长度: ${#CMD} 字符"
    # drop <命令串> <sed 表达式...>：按顺序删掉若干组选项
    drop() {
      local c=$1; shift; local e
      for e in "$@"; do c=$(printf '%s' "$c" | sed -E "$e"); done
      printf '%s' "$c"
    }
    # prep_cmd <标签> <命令串> → 回显改好输出路径的命令；产物路径放进 VOUT
    # 注意：不能写成 `local tag=$1 c=$2 out="$WORK/v_$tag.mp4"` —— bash 会把**所有**
    # 参数词先展开再执行 local，那一刻 tag 还没绑定，set -u 直接报 unbound variable。
    # ⚠ prep_cmd **不能**用 `c=$(prep_cmd ...)` 调用：命令替换会开子 shell，函数里
    # 赋的 VOUT/VCMD 传不出来（`VOUT: unbound variable`，本机自测踩到）。所以它
    # 什么都不回显，只负责设置下面这两个全局变量。
    prep_cmd() {
      local tag=$1 c=$2
      VOUT="$WORK/v_$tag.mp4"
      c=$(printf '%s' "$c" | sed "s|$WORK/dry\.mp4|$VOUT|")
      # 兜底：Windows 上 python 打印的是 `'D:\...\dry.mp4'`，上面的 POSIX 路径 sed
      # 匹配不到 → 命令会写回 dry.mp4、而检查的是 VOUT（本机自测时踩到）。必须把
      # **整个路径 token** 换掉：只换 `dry.mp4` 会留下 `D:\...\chroma_work\` 这种残根，
      # 拼出一个不存在的路径（也踩到过）。`[^ ']*` 匹配到引号为止，故 Windows 那份的
      # 引号会被保留，eval 照样吃得下。
      c=$(printf '%s' "$c" | sed -E "s|[^ ']*dry\.mp4|$VOUT|")
      VCMD=$c
    }
    runv() {  # runv <标签> <命令字符串>：跑完打印一行（人类可读）
      local tag=$1
      prep_cmd "$tag" "$2"
      rm -f "$VOUT"
      if eval "$VCMD" >"$WORK/v_$tag.log" 2>&1; then
        chk "$tag" "$VOUT"
      else
        LAST_U=-1
        printf '  %-26s FAILED: %s\n' "$tag" "$(errline "$WORK/v_$tag.log")"
      fi
    }
    runq() {  # runq <标签> <命令字符串>：静默版，只为穷举用；绿色返回 0
      local tag=$1
      prep_cmd "q_$tag" "$2"
      rm -f "$VOUT"
      if eval "$VCMD" >"$WORK/v_q_$tag.log" 2>&1; then
        chk __q__ "$VOUT" >/dev/null      # 只为填 LAST_U，不打印
      else
        LAST_U=-1
      fi
      if [[ "$LAST_U" =~ ^[[:space:]]*[0-9] ]] && awk -v u="$LAST_U" 'BEGIN{exit !(u<16)}'; then
        return 0                          # 绿色 = 复现了故障
      fi
      return 1
    }
    # 各组选项的 sed 删除表达式（`+`/`?` 在 -E 下要转义；-map 的取值可能带引号）
    E_COL='s/ -colorspace [^ ]+ -color_primaries [^ ]+ -color_trc [^ ]+ -color_range [^ ]+//'
    E_ERR='s/ -err_detect ignore_err -fflags \+genpts\+discardcorrupt//'
    E_ROT='s/ -noautorotate//'
    E_MAP='s/ -map [^ ]+ -map [^ ]+ -map_metadata 0 -map_chapters 0//'
    # preset 的取值随策略变（NVENC 是 p5、降级到 libx265 是 medium）→ 用通配，
    # 写死 p5 会在非 NVENC 策略上静默不匹配（本机自测时就是这样）
    E_PRE='s/ -preset [^ ]+//'
    E_TAIL='s/ -c:a copy -movflags \+faststart//'
    E_HW='s/ -hwaccel cuda -hwaccel_device 0//'

    runv V0_脚本原命令 "$CMD"                    # 应当复现绿色
    runv V1_删输出色彩四参 "$(drop "$CMD" "$E_COL")"
    runv V2_删err_detect_fflags "$(drop "$CMD" "$E_ERR")"
    runv V3_删noautorotate "$(drop "$CMD" "$E_ROT")"
    runv V4_删map一组 "$(drop "$CMD" "$E_MAP")"
    runv V5_删preset "$(drop "$CMD" "$E_PRE")"
    runv V6_删尾部ca_movflags "$(drop "$CMD" "$E_TAIL")"
    VMIN=$(drop "$CMD" "$E_ERR" "$E_ROT" "$E_MAP" "$E_PRE" "$E_TAIL")
    runv V7_最小集_删其余全部 "$VMIN"             # ≈ 第 2 节的 B1，应当正常
    VCOL=$(drop "$CMD" "$E_ERR" "$E_ROT" "$E_MAP" "$E_PRE" "$E_TAIL")
    runv V8_只留色彩四参_带硬解 "$VCOL"
    runv V9_只留色彩四参_去硬解 "$(drop "$VCOL" "$E_HW")"
    echo "  读法：V0 绿、V7 正常 → 元凶就在被删的那几组里；V1~V6 哪一格恢复正常 = 那一组是元凶；"
    echo "        若 V1~V6 全仍绿 → 组合效应，看下面的穷举结果（V8/V9 判「硬解×色彩四参」是否成对）。"

    # ── 6b. 全组合穷举（默认 2^6 = 64 组）：避免"组合效应"再往返一轮 ──
    # 逐组删只能定位**单个**元凶；组合效应（两个选项同时在场才出问题）需要按子集枚举。
    # 6 组共 64 个子集，每个只转 3 秒片段，在 T4 上是一两分钟的事，比再让用户手动来回一轮便宜。
    # 本机自测时用 EXHAUST_BITS=2 快速验证循环本身。
    GROUP_NAMES=(色彩四参 err_detect+fflags noautorotate map组 preset 尾部ca+movflags)
    GROUP_SED=("$E_COL" "$E_ERR" "$E_ROT" "$E_MAP" "$E_PRE" "$E_TAIL")
    EXHAUST_BITS=${EXHAUST_BITS:-6}
    if (( EXHAUST_BITS > 0 )); then
      echo; echo "  ── 6b. 全组合穷举（2^$EXHAUST_BITS 个子集）──"
      TOTAL=$(( 1 << EXHAUST_BITS ))
      GREEN_CNT=0; FAIL_CNT=0; MIN_N=99; MIN_MASK=-1
      for (( m=0; m<TOTAL; m++ )); do
        _ex=(); for (( b=0; b<EXHAUST_BITS; b++ )); do
          (( (m >> b) & 1 )) && _ex+=("${GROUP_SED[$b]}")
        done
        # runq 返回 0 = 绿色（复现故障），返回非 0 = 这一格不是绿色。
        if runq "$m" "$(drop "$CMD" ${_ex[@]+"${_ex[@]}"})"; then
          GREEN_CNT=$(( GREEN_CNT + 1 ))
        else
          # LAST_U<0 ⇒ 该子集的命令**自己失败了**，不能当成"恢复正常"。
          if [[ "$LAST_U" =~ ^[[:space:]]*[0-9] ]]; then
            # 「最小恢复集」= 让故障消失的**最小删除集** → 在恢复正常的子集里取最小 popcount。
            # ⚠ 原来这段写在绿色分支里，于是永远是 m=0（删 0 组），打印成"共 0 组"。
            _n=0; for (( b=0; b<EXHAUST_BITS; b++ )); do (( (m >> b) & 1 )) && _n=$(( _n + 1 )); done
            if (( _n < MIN_N )); then MIN_N=$_n; MIN_MASK=$m; fi
          else
            FAIL_CNT=$(( FAIL_CNT + 1 ))
          fi
        fi
      done
      printf '  绿 %d / 正常 %d / 失败 %d（共 %d 个子集）\n' \
        "$GREEN_CNT" "$(( TOTAL - GREEN_CNT - FAIL_CNT ))" "$FAIL_CNT" "$TOTAL"
      if (( MIN_MASK < 0 )); then
        echo "  ✗ $TOTAL 个子集里**没有任何一个让故障消失** → 元凶不在这 $EXHAUST_BITS 组里："
        echo "      · 说明连「删掉全部这几组」都还绿 → 触发条件在它们之外（换个思路查）"
        echo "      · 也可能 m=0（原命令）本来就没复现 → 先确认第 4 节 default 是绿的"
        echo "      · 下一步：把第 5 节那条命令与第 2 节手写命令逐 token diff"
      else
        _names=''
        for (( b=0; b<EXHAUST_BITS; b++ )); do
          (( (MIN_MASK >> b) & 1 )) && _names+="${GROUP_NAMES[$b]}  "
        done
        if (( MIN_N == 0 )); then
          echo "  ✔ 最小恢复集 = 0 组：原命令（一个都不删）本来就正常 → 本轮**没有复现故障**。"
          echo "      若脚本已打上色度修复，这是预期结果；否则先看第 4 节 default 是不是真的绿。"
        else
          printf '  ✔ 最小恢复集（删掉这些组后色度就正常，共 %d 组）: %s\n' "$MIN_N" "$_names"
          echo "      即：元凶 = 上面这些组（若只列一组，就是那一个选项/一组选项）。"
        fi
      fi
    fi
  fi
fi

awk -v us="${US:--1}" -v vs="${VS:--1}" -v a0="${A0U:--1}" -v a1="${A1U:--1}" \
    -v b0="${B0U:--1}" -v b0b="${B0BU:--1}" -v b1="${B1U:--1}" \
    -v b2="${B2U:--1}" -v b3="${B3U:--1}" -v c1="${C1U:--1}" "$AWK_VERDICT"
echo; echo "产物与日志都在 $WORK/"
