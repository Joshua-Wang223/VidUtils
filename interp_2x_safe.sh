#!/usr/bin/env bash
# =============================================================================
# nvinterpolate 2x 帧率 —— 崩溃安全版
#
# 做什么:
#   用 NVOFA 光流插帧把输入帧率翻倍（目标帧率 = 源帧率 x2），音频原样 copy。
#   例: 3840x2160 @ 24000/1001  →  48000/1001，时长不变。
#
# 只支持 2 倍: 滤镜串里 fps=source_fps*2、以及片头要去掉的重复帧数（HEAD_TRIM=3）
#   都是按 2x 写死的。1.25x/2.5x 的预热帧数不同，要用得改源码里这两处。
#
# 前置条件:
#   · ffmpeg 带 nvinterpolate 滤镜（本机 = /usr/local/bin/ffmpeg 7.1）
#   · NVIDIA GPU，Turing (CC 7.5) 或更新；驱动 >= 525
#   · 源视频有明确的帧率元数据 —— 目标帧率是拿它 x2 算出来的，不能是 VFR/0
#
# 为什么不是一条 ffmpeg 命令直接写 MP4:
#   2026-09-14 07:10 那次跑到 07:44 被中断（core dump 显示崩的是 node/codebuddy，
#   它把同进程组的 ffmpeg 一起带走了）。产物 2.1GB 的 mp4 只有 ftyp/free/mdat、
#   没有 moov → 完全不可读，34 分钟算力作废，且没有可续传的点。
#   所以这条链做三件事:
#     1) setsid 脱离会话  —— codebuddy/终端死掉不影响本任务
#     2) 每 L 秒一片写 TS —— TS 无全局索引，被杀时已完成的分片原样可用
#     3) 已存在的分片自动跳过 —— 重跑同一条命令就是"断点恢复"
#   最后 concat 成 MP4，音轨从原片一次性 copy（音频本来就没改，也避开 AAC 切点问题）。
#
# -----------------------------------------------------------------------------
# 用法:
#   bash interp_2x_safe.sh <输入视频> [输出视频]
#
# 位置参数:
#   <输入视频>   必需。分片目录与默认输出名都由它推导
#   [输出路径]   可选。两种形态都行:
#                   · 目录：以 / 结尾、或本身已是存在的目录
#                           → 自动用 <输入名>_2x.mp4 起名
#                   · 文件：带扩展名，原样使用
#                 默认 <WORKDIR>/<输入名>_2x.mp4
#
# 选项:
#   -w, --workdir DIR    分片/输出目录，默认 /workspace/interp_2x/<输入名>
#                        每个输入一个目录；换输入会自动分家（见下方"防呆"）
#   -p, --preset NAME    hevc_nvenc 预设，默认 p5（越大越慢、同码率画质越好）
#   -c, --cq N           恒定质量，默认 25（越大越省码率、画质越低）
#   -L, --seg-len SEC    每片秒数，默认 300
#                          · 越大 → 接缝越少，但崩一次损失越多
#                          · 越小 → 损失窗口小，但每片开头都要重跑一次 FRUC 预热
#       --cap SEC        >0 时只处理前 N 秒，可小数（试跑验证 / 只想要前一段）
#                        非法值（负数/非数字）会直接报错，不静默当成"不裁剪"
#                        超过源总长 = 无效果，与不设置等价
#       --overwrite      允许覆盖已存在的目标文件（默认拒绝，且尽早退出）
#   -h, --help           显示帮助
#
# 环境变量（可选；与命令行选项重名时命令行优先，保留是为了兼容旧调用）:
#   L  TOTAL_CAP  WORKDIR  PRESET  CQ
#   FFMPEG      ffmpeg 可执行文件，默认 /usr/local/bin/ffmpeg
#   FFPROBE     ffprobe 可执行文件，默认 ffprobe
#
# 容器: 输出扩展名决定容器，支持 mp4 / mov / mkv。mp4/mov 会加 -movflags
#   +faststart（moov 前置）；mkv 不加（Matroska 不认这个选项，传了会直接报错）。
#   这个校验在【编码开始前】做，避免白跑 50 分钟才发现输出写不出来。
#
# 产出（都在 $WORKDIR）:
#   parts/p00000.ts …   已完成的分片 —— **分片数就是进度**
#   parts/p00000.meta   该片的切法（`ss dt`），决定它能不能被复用
#   parts.txt           concat 用的清单（只列本次 NPARTS 片）
#   recipe.txt          「输入 + 参数」指纹，见下
#   成片                 路径见上面 [输出路径]
#
# 目录 / 文件 / 并发:
#   · WORKDIR、parts/、输出文件的父目录都会自动创建，不用先 mkdir
#   · 目标文件已存在时默认报错退出（早退，不等到收尾才失败）；--overwrite 才覆盖
#   · WORKDIR 上有单实例锁（$WORKDIR/.lock，flock）。同一目录同时只允许一个实例，
#     第二个会立刻退出并提示。原因：两个实例会互相删掉对方正在写的分片临时文件
#     （ffmpeg 还在往已被 unlink 的 inode 写），先跑完的把临时文件 mv 走，
#     后跑完的就报 "mv: cannot stat '.../p00002.ts.part'"，而且两边都白跑。
#     锁是内核级的，进程被 kill 会自动释放，不会留死锁。
#
# 防呆（两层，目的都是"绝不静默产出错内容"）:
#   第一层 recipe.txt：记 slice|in|L|preset|cq|trim。这些参数一变，目录里
#     **每一片**的内容都会不同（帧边界变了/画质档变了/换了视频）→ 整体拒绝，
#     并打印新旧差异。换输入默认就会另开目录；要显式复用就自己给 -w。
#   第二层 每片 .meta：记该片的 ss/dt。总时长只影响【末尾那一片】的切法，
#     所以改 --cap 不再整体拒绝，而是逐片比对、只重编边界那一片。
#     缺 .meta 的分片（旧版本留下的）一律视为需重做。
#
# 常见用法:
#   # 1) 正式跑：脱离会话后台执行
#   setsid bash /workspace/VidUtils/interp_2x_safe.sh /workspace/input_videos/xxx.mp4 \
#       > /workspace/interp_2x/run.log 2>&1 < /dev/null &
#
#   # 2) 看进度 / 判断是否跑完（启动日志会打印实际的 WORKDIR 与输出路径）
#   tail -f /workspace/interp_2x/run.log
#   ls /workspace/interp_2x/*/parts/          # 分片数 = 已完成进度
#
#   # 3) 中断后恢复：原样再执行 1) 即可，已完成分片秒过
#
#   # 4) 试跑：4 秒一片、只跑前 12 秒，产物扔 /tmp
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -w /tmp/demo -L 4 --cap 12
#
#   # 5) 输出到目录 → 自动起名 <输入名>_2x.mp4
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 /tmp/out/
#
#   # 6) 输出到指定文件 + 换画质参数
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 /tmp/head.mkv -c 23 -p p4
#
#   # 7) 只处理前 10 分钟；之后想补全片，把 --cap 去掉重跑即可
#   #    （前面的分片按 .meta 判定为可复用，只会重编边界那一片）
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 /tmp/head.mp4 --cap 600
#
#   # 8) 补全片：同一条命令去掉 --cap，复用同一个 -w
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -w /tmp/work
#
# 失败时的样子:
#   · 某片失败      → 打印 FAIL 并非 0 退出；半成品留在 pXXXXX.ts.part.<pid>。
#                     它不是 .ts，所以不会被误判成"已完成"；持锁启动时会自动清掉，
#                     重跑也会自动重做该片
#   · 拼接失败      → 分片都还在，可手工重拼，或直接重跑
#   · 被杀/断电      → 已改名成 .ts 的分片保留；临时文件下次持锁启动时自动清理
# =============================================================================
set -euo pipefail

usage() {
    cat <<'EOF'
用法: bash interp_2x_safe.sh <输入视频> [输出路径] [选项]

  把输入帧率翻倍（目标 = 源帧率 x2），音频原样 copy，时长不变。
  只做 2 倍：fps=source_fps*2 和片头去重帧数都按 2x 写死。

位置参数:
  <输入视频>    必需。分片目录与默认输出名都由它推导
  [输出路径]    可选。两种形态都行
                   · 目录：以 / 结尾、或本身已是存在的目录
                           → 自动用 <输入名>_2x.mp4 起名
                   · 文件：带扩展名，原样使用（mp4 / mov / mkv）
                 默认 <WORKDIR>/<输入名>_2x.mp4

选项:
  -w, --workdir DIR   分片/输出目录，默认 /workspace/interp_2x/<输入名>
  -p, --preset NAME   hevc_nvenc 预设，默认 p5（越大越慢、同码率画质越好）
  -c, --cq N          恒定质量，默认 25（越大越省码率、画质越低）
  -L, --seg-len SEC   每片秒数，默认 300（越大接缝越少、崩一次损失越多）
      --cap SEC       >0 时只处理前 N 秒，可小数（试跑验证 / 只要前一段）
                      负数或非数字会报错；超过源总长等于不设置
      --overwrite     允许覆盖已存在的目标文件（默认拒绝，且尽早退出）
  -h, --help          显示本帮助

目录 / 文件 / 并发:
  · WORKDIR、parts/、输出文件的父目录都会自动创建
  · 目标文件已存在时默认报错退出；确认要覆盖再加 --overwrite
  · WORKDIR 上有单实例锁（.lock）。同一目录同时只允许一个实例，第二个会立刻
    退出并提示 —— 因为两个实例会互相删掉对方正在写的分片临时文件，
    导致 "mv: cannot stat ... .part" 且两边都白跑

环境变量（可选；同名时命令行优先，保留是为了兼容旧调用）:
  L  TOTAL_CAP  WORKDIR  PRESET  CQ
  FFMPEG      默认 /usr/local/bin/ffmpeg
  FFPROBE     默认 ffprobe

产出（都在 WORKDIR）:
  parts/p*.ts       已完成的分片 —— 分片数就是进度
  parts/p*.meta     该片的切法（ss dt），决定它能不能被复用
  recipe.txt        输入+参数的指纹；不匹配会拒绝启动（防复用错分片）
  成片               路径见上面 [输出路径]

复用规则（两层）:
  · recipe.txt 里的 in/L/preset/cq 一变 → 整体拒绝（每一片的内容都会不同）
  · 只改 --cap → 不拒绝，逐片比对 .meta，只重编末尾那一片

正式跑（脱离会话）:
  setsid bash interp_2x_safe.sh /path/in.mp4 -w /tmp/work \
      > /workspace/interp_2x/run.log 2>&1 < /dev/null &

看进度:
  tail -f /workspace/interp_2x/run.log
  ls /workspace/interp_2x/*/parts/

中断后恢复: 原样再执行同一条命令即可（已完成分片秒过）。
EOF
}

# --------------------------- 参数解析 ----------------------------------------
need_arg() { [[ $# -ge 2 ]] || { echo "错误: 选项 $1 需要一个值" >&2; exit 1; }; }

IN=""; OUT_ARG=""
WORKDIR_OPT=""; PRESET_OPT=""; CQ_OPT=""; L_OPT=""; CAP_OPT=""
OVERWRITE=false

while (( $# > 0 )); do
    case "$1" in
        -h|--help)      usage; exit 0 ;;
        -w|--workdir)   need_arg "$@"; WORKDIR_OPT=$2; shift 2 ;;
        -p|--preset)    need_arg "$@"; PRESET_OPT=$2;  shift 2 ;;
        -c|--cq)        need_arg "$@"; CQ_OPT=$2;      shift 2 ;;
        -L|--seg-len)   need_arg "$@"; L_OPT=$2;       shift 2 ;;
        --cap)          need_arg "$@"; CAP_OPT=$2;     shift 2 ;;
        --overwrite)    OVERWRITE=true; shift ;;
        -*)             echo "错误: 未知选项 '$1'" >&2; usage; exit 1 ;;
        *)  if   [[ -z "$IN"      ]]; then IN=$1
            elif [[ -z "$OUT_ARG" ]]; then OUT_ARG=$1
            else echo "错误: 多余的参数 '$1'（最多 输入视频 + 输出路径）" >&2; usage; exit 1
            fi
            shift ;;
    esac
done
[[ -n "$IN" ]] || { usage; exit 1; }

# 生效优先级：命令行选项 > 环境变量 > 默认值
L=${L_OPT:-${L:-300}}                    # 每片时长(秒)。崩一次最多损失这么多
TOTAL_CAP=${CAP_OPT:-${TOTAL_CAP:-0}}     # >0 时只处理前 N 秒（验证用）
PRESET=${PRESET_OPT:-${PRESET:-p5}}      # hevc_nvenc 预设
CQ=${CQ_OPT:-${CQ:-25}}                  # 恒定质量
FFMPEG=${FFMPEG:-/usr/local/bin/ffmpeg}
FFPROBE=${FFPROBE:-ffprobe}

BASE=$(basename "${IN%.*}")
WORKDIR=${WORKDIR_OPT:-${WORKDIR:-/workspace/interp_2x/$BASE}}

# nvinterpolate 2x 的片头会重复 3 帧（拿不到"前一帧"），要裁掉。
# 每个分片都是全新的滤镜实例，所以每片开头都会重演这 3 帧。
# 注意：-t 是【输出】选项，剪掉 3 帧后 ffmpeg 会自己多读 3 个源帧把这片补满 L 秒，
# 因此每片仍严格是 L 长、且相邻片的画面内容天然连续（实测 4 片 = 192/192/192 帧），
# 不需要任何 ±offset 补偿 —— 早期版本手工补 0.0625s 反而多算了帧。
HEAD_TRIM=3

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

PARTS="$WORKDIR/parts"
LIST="$WORKDIR/parts.txt"

# --- 输出路径：第 2 个位置参数既可以是文件，也可以是目录 ---------------------
#   · 以 / 结尾，或本身就是已存在的目录 → 当目录，自动用 <输入名>_2x.<ext> 起名
#   · 否则当最终文件路径，原样使用（必须带扩展名，否则 ffmpeg 认不出容器）
if [[ -z "$OUT_ARG" ]]; then
    OUT="$WORKDIR/${BASE}_2x.mp4"
elif [[ "$OUT_ARG" == */ || -d "$OUT_ARG" ]]; then
    OUT="${OUT_ARG%/}/${BASE}_2x.mp4"
else
    OUT="$OUT_ARG"
fi
[[ "${OUT##*/}" == *.* ]] || die "输出路径 '$OUT' 没有扩展名，ffmpeg 无法判断容器。
  指定目录请以 / 结尾（会按 <输入名>_2x.mp4 起名）；指定文件请带扩展名。"

# 容器相关：-movflags +faststart 只对 mp4/mov 有意义，Matroska 不认这个选项、
# 传了会直接报错。这里在【编码开始前】就定好，避免白跑 50 分钟才发现输出写不出。
case "${OUT##*.}" in
    mp4|m4v|mov) MOVFLAGS=(-movflags +faststart) ;;
    mkv|webm)    MOVFLAGS=() ;;
    *)           die "输出扩展名 '.${OUT##*.}' 不支持；可用: mp4 / mov / mkv" ;;
esac

# 所有需要的目录都自动创建：WORKDIR、分片目录、输出文件的父目录。
# dirname 可能是 "."（输出写到当前目录），mkdir -p . 无害。
mkdir -p "$WORKDIR" "$PARTS" "$(dirname "$OUT")"

# --- 单实例锁 -----------------------------------------------------------------
# 放在覆盖检查【之前】：并发时第二个实例应当被告知"有实例在跑"（真正的原因），
# 而不是先撞上"目标文件已存在"这种与并发无关的提示。
#
# 同一个 WORKDIR 必须独占。两个实例共享它时，循环开头的 rm -f 会删掉对方正在
# 写的临时文件（ffmpeg 仍在往已被 unlink 的 inode 写），先跑完的把临时文件 mv 走，
# 后跑完的就报：
#   mv: cannot stat '.../p00002.ts.part': No such file or directory
# 而且双方都白跑。flock 是内核级的，进程被 kill 也会自动释放，不会留下死锁；
# .lock 文件本身留在盘上也无所谓 —— 占用与否只看锁，不看文件在不在。
LOCK="$WORKDIR/.lock"
exec 9>"$LOCK"
flock -n 9 || die "另一个实例正在跑同一个分片目录: $WORKDIR
  同一目录只能有一个实例（否则会互相删对方的临时文件、两边都白跑）。
  等它结束再原样重跑即可（已完成的分片会自动跳过），或换一个 -w 目录。"

# 目标文件默认【不】覆盖：已存在就立刻退出，别等跑了几十分钟到收尾才发现。
if [[ -e "$OUT" && "$OVERWRITE" != true ]]; then
    die "目标文件已存在: $OUT
  要覆盖请加 --overwrite；或换输出路径；或用 -w 换一个分片目录。"
fi

# 已经拿到锁 → 库里不会有别的实例在写 → 清掉上次被 kill 留下的临时文件
for f in "$PARTS"/p*.ts.part*; do
    [[ -e "$f" ]] || continue
    log "clean 残留临时文件 $(basename "$f")"
    rm -f "$f"
done

# ------------------------------- 前置检查 ------------------------------------
[[ -f "$IN" ]] || die "找不到输入: $IN"
[[ -x "$FFMPEG" ]] || die "找不到可执行文件: $FFMPEG"
command -v nvidia-smi >/dev/null 2>&1 || die "没有 nvidia-smi"
"$FFMPEG" -hide_banner -filters 2>/dev/null | grep -q ' nvinterpolate ' \
    || die "$FFMPEG 里没有 nvinterpolate 滤镜"

TOTAL=$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$IN")
[[ -n "$TOTAL" ]] || die "取不到 $IN 的时长"
# 保留小数，不要截成整秒：末尾分片要按精确剩余时长切。整秒截断会漏掉尾巴
# （实测 10.0667s 的源被截成 10s → 成片少 4 帧）。
# TOTAL_CAP 允许小数。校验必须走 awk：bash 的 (( )) 只吃整数，
# 写成 (( TOTAL_CAP > 0 )) 时 TOTAL_CAP=6.5 会打一行 syntax error 然后
# 判假 → 静默当成"不裁剪"，用户以为只跑了 6.5 秒、实际拿到整片。
# -1 / abc 之类非法值也一律报错，绝不静默放行。
case "$TOTAL_CAP" in
    ''|0) ;;                      # 不裁剪
    *)  awk -v c="$TOTAL_CAP" 'BEGIN{exit !(c ~ /^[0-9]+(\.[0-9]+)?$/)}' \
            || die "TOTAL_CAP 必须是非负数字，当前为 '$TOTAL_CAP'"
        if awk -v t="$TOTAL" -v c="$TOTAL_CAP" 'BEGIN{exit !(c < t)}'; then
            TOTAL=$TOTAL_CAP
        fi
        ;;
esac
NPARTS=$(awk -v t="$TOTAL" -v l="$L" 'BEGIN{ n=int(t/l); if (n*l < t-1e-9) n++; print n }')

# --- 防呆第一层：会改变「每一片」内容的参数 → 整体拒绝 -----------------------
# recipe.txt 记 slice|in|L|preset|cq|trim。这些一变，目录里**所有**分片的内容
# 都不同（帧边界变了 / 画质档变了 / 换了视频），复用就是静默产出错内容。
# 注意 total 不在里面：总时长只影响【末尾那一片】的切法，这种变化由每片的
# .meta 逐片判断（第二层），只重编边界片即可，不必整体拒绝。
RECIPE="slice=3|in=$IN|L=$L|preset=$PRESET|cq=$CQ|trim=$HEAD_TRIM"
STAMP="$WORKDIR/recipe.txt"
if [[ -f "$STAMP" ]]; then
    [[ "$(cat "$STAMP")" == "$RECIPE" ]] || die "分片目录与当前任务不匹配，拒绝复用:
  目录 : $WORKDIR
  已有 : $(cat "$STAMP")
  本次 : $RECIPE
换输入请另给 WORKDIR，或删掉该目录后重跑。"
else
    printf '%s\n' "$RECIPE" > "$STAMP"
fi

log "输入  : $IN"
log "输出  : $OUT"
log "分片  : $NPARTS 片 x ${L}s  ->  $PARTS"
log "编码  : hevc_nvenc -preset $PRESET -cq $CQ（视频）；音轨收尾时 -c copy"

FC="nvinterpolate=fps=source_fps*2,trim=start_frame=${HEAD_TRIM},setpts=PTS-STARTPTS"

# ------------------------------- 逐片编码 ------------------------------------
for (( i=0; i<NPARTS; i++ )); do
    tag=$(printf 'p%05d' "$i")
    part="$PARTS/$tag.ts"
    meta="$PARTS/$tag.meta"

    # 先把本片"应该怎么切"算出来，再拿去和元数据比 —— 所以这段必须在
    # 跳过判断之前。
    S=$(( i * L ))
    ss=$S
    dt=$(awk -v t="$TOTAL" -v s="$S" -v l="$L" \
         'BEGIN{ d=t-s; if (d>l) d=l; printf "%.4f", d }')
    want="$ss $dt"

    # --- 防呆第二层：逐片判断切法 -------------------------------------------------
    # 「可复用」= 分片文件在 + .meta 在 + .meta 记的切法与本片需要的完全一致。
    # 只有末尾那一片的 dt 会随总时长变，所以改 TOTAL_CAP 时最多只重编边界那一片，
    # 前面的片照旧复用 —— 不需要整体拒绝，也不会拼错。
    if [[ -f "$part" && -f "$meta" ]]; then
        if [[ "$(cat "$meta")" == "$want" ]]; then
            log "skip  $tag  （已完成，$(stat -c%s "$part") bytes）"
            continue
        fi
        log "redo  $tag  （切法变了: 记 '$(cat "$meta")' → 需 '$want'）"
    elif [[ -f "$part" ]]; then
        log "redo  $tag  （缺 $tag.meta，无法确认切法）"
    fi

    # 不预先删旧 part：ffmpeg 写临时文件，成功后 mv 覆盖。
    # 这样中途失败时旧分片+旧 meta 原样留着，下次仍然判定为"需重做"并重试。
    # 临时名带 PID：即使锁被绕过（手工并发），两个实例也不会踩对方正在写的文件。
    tmp="$part.part.$$"
    rm -f "$tmp"
    log "run   $tag  -ss $ss -t $dt"

    if ! "$FFMPEG" -nostdin -y -hide_banner -loglevel warning -nostats \
            -hwaccel cuda -hwaccel_output_format cuda \
            -ss "$ss" -i "$IN" -t "$dt" \
            -filter_complex "$FC" -fps_mode passthrough \
            -c:v hevc_nvenc -preset "$PRESET" -cq "$CQ" -an \
            -f mpegts "$tmp"; then
        log "FAIL  $tag（半成品保留在 $tmp，可单独检查）"
        exit 1
    fi

    # ffmpeg 返回 0 却没产出文件：给出可诊断的信息，而不是让 mv 抛裸错
    [[ -f "$tmp" ]] || die "$tag: ffmpeg 返回成功但没有产出 $tmp。
  常见原因：磁盘满（df -h /）或锁被绕过、有另一个实例在写同一个 -w 目录。"
    mv -f "$tmp" "$part"              # 先就位分片
    printf '%s\n' "$want" > "$meta"   # 再写元数据 → 半成品永远不会被当成可用
    log "done  $tag  $(stat -c%s "$part") bytes"
done

# ------------------------------- 收尾拼接 ------------------------------------
log "全部分片就绪，拼接 + 复制音轨"
# 只列本次要用的 0..NPARTS-1。不能 glob p*.ts —— 改小 TOTAL_CAP 时目录里会
# 留下更多分片，glob 会把它们一起拼进去。
: > "$LIST"
for (( i=0; i<NPARTS; i++ )); do
    printf "file '%s'\n" "$PARTS/$(printf 'p%05d' "$i").ts" >> "$LIST"
done

# 音轨从原片一次性 copy（音频没被改过）。写成 1:a:0? ——
# 末尾的 ? 表示"没有这条流就忽略"，否则输入本身无音轨时 map 会直接报错。
# 临时名保留与 $OUT 相同的扩展名，否则 ffmpeg 会按 .part 猜不到容器。
OUT_TMP="$OUT.part.${OUT##*.}"
if ! "$FFMPEG" -nostdin -y -hide_banner -loglevel warning -nostats \
        -f concat -safe 0 -i "$LIST" -i "$IN" \
        -map 0:v:0 -map 1:a:0? -c copy -t "$TOTAL" "${MOVFLAGS[@]}" \
        "$OUT_TMP"; then
    die "拼接失败（分片都还在 $PARTS，可手工重拼）"
fi
mv -f "$OUT_TMP" "$OUT"

log "完成: $OUT"
log "成片: $("$FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=r_frame_rate,nb_frames -of csv=p=0 "$OUT")  (r_frame_rate, nb_frames)"

# 用容器里的 nb_frames（即时可得）。不要 -count_frames：那会把整片解码一遍。
SRC_FRAMES=$("$FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=nb_frames -of csv=p=0 "$IN" 2>/dev/null || true)
SRC_FRAMES=${SRC_FRAMES:-0}
if (( SRC_FRAMES > 0 )); then
    log "参考值: 源 ${SRC_FRAMES} 帧 x2 = $(( SRC_FRAMES * 2 )) 帧；时长应与源一致（约 ${TOTAL}s）"
fi
