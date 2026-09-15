#!/usr/bin/env bash
# =============================================================================
# interp_2x_safe.sh 的回归测试：单实例锁 / 并发安全
# （与 interp_2x_safe.sh 同属 VidUtils；SUT 默认取本脚本同目录，整体搬迁后仍可用）
#
# 背景（这个测试要守住的原始 bug）:
#   两个实例共享同一个 -w 目录时，循环开头的 rm -f 会删掉对方正在写的临时文件
#   （ffmpeg 仍往已被 unlink 的 inode 写），先跑完的把临时文件 mv 走，后跑完的就报
#       mv: cannot stat '.../p00002.ts.part': No such file or directory
#   而且双方都白跑。现在靠 $WORKDIR/.lock 上的 flock 挡住，这个测试就是守它。
#
# 用法:
#   bash test_interp_2x_lock.sh [测试用输入视频]
#   TEST_INPUT=/path/to/small.mp4 bash test_interp_2x_lock.sh
#   SUT=/path/to/interp_2x_safe.sh bash test_interp_2x_lock.sh
#
# 退出码:  0 = 全部通过   1 = 有用例失败   2 = 环境不具备，跳过
#
# 环境变量:
#   TEST_INPUT  测试输入（默认自动在 <本脚本目录>/input_videos/ 与
#               /workspace/input_videos/ 找 new5_10s.mp4）
#   SUT         被测脚本（默认与本脚本同目录的 interp_2x_safe.sh）
#   SEG         分片长度，默认 2 秒（越小片越多、并发窗口越大）
#   FFMPEG / FFPROBE
#
# 说明:
#   · 只用自己 mktemp 出来的 -w 目录，绝不碰默认的 /workspace/interp_2x，
#     所以可以直接在你正跑正式任务的机器上执行（只是会抢一点 GPU）。
#   · 每个用例都显式指定输出路径，不吃输入文件名推导出的默认名，便于换输入。
#   · 清理时只 kill 自己记录过的 PID，不做任何按模式的 pkill。
#   · 断言分两类，两类都要有：
#       日志断言 —— 看 SUT 打印了什么（skip/redo/run 行、报错文案）。
#       事实断言 —— 分片+meta 的指纹（名字/大小/纳秒 mtime）、文件是否真的从盘上
#                   消失、成片帧数。这类不依赖 SUT 的自我报告，能抓到"日志说做了、
#                   实际没做/做错了"。
#   · 环境满足不了某条断言的前提时记 SKIP（黄字）并计入统计，绝不静默跳过 ——
#     典型例子：并发用例需要 A 先产出至少一片，否则"未触碰已有分片"的指纹对比
#     会变成拿空对空（假通过）。
#   · 成片帧数用 ±2 容差断言：分片边界落在源时长的小数部分上，可能差一两帧。
# =============================================================================
set -uo pipefail

# ROOT 必须先算：SUT 的默认值要相对本脚本所在的目录解析，
# 这样整个 VidUtils 目录搬到哪儿都能直接跑。
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SUT=${SUT:-$ROOT/interp_2x_safe.sh}
SEG=${SEG:-2}
FFMPEG=${FFMPEG:-/usr/local/bin/ffmpeg}
FFPROBE=${FFPROBE:-ffprobe}

# 测试输入：显式指定优先；否则在几个候选位置里找。
# （脚本搬到 VidUtils/ 之后，素材仍在 /workspace/input_videos/，所以留了候选。）
IN=${TEST_INPUT:-${1:-}}
if [[ -z "$IN" ]]; then
    for c in "$ROOT/input_videos/new5_10s.mp4" \
             "/workspace/input_videos/new5_10s.mp4" \
             "$ROOT/../input_videos/new5_10s.mp4"; do
        [[ -f "$c" ]] && { IN=$c; break; }
    done
fi
[[ -n "$IN" ]] || IN=/workspace/input_videos/new5_10s.mp4   # 仅用于把错误信息说清楚

# SUT 与 IN 统一成绝对路径。本脚本稍后会切到自己的临时目录再跑 SUT（见下），
# 那时相对路径会失效 —— 而 `SUT=./interp_2x_safe.sh` 正是文档里推荐的写法。
abspath() {
    if [[ -e "$1" ]]; then
        printf '%s/%s' "$(cd "$(dirname "$1")" && pwd)" "$(basename "$1")"
    else
        printf '%s' "$1"
    fi
}
SUT=$(abspath "$SUT")
IN=$(abspath "$IN")

GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; NC=$'\033[0m'
PASS=0; FAIL=0; SKIP=0
ok()   { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  ${RED}FAIL${NC} %s\n" "$1"; FAIL=$((FAIL+1)); }
# 环境满足不了某条断言的前提 → 记 SKIP 而不是 FAIL，但绝不静默跳过
skip() { printf "  ${YEL}SKIP${NC} %s\n" "$1"; SKIP=$((SKIP+1)); }

assert_eq()    { [[ "$2" == "$3" ]] && ok "$1（$2）" || bad "$1：实际 '$2'，期望 '$3'"; }
assert_file()  { [[ -f "$2" ]]     && ok "$1"        || bad "$1：$2 不存在"; }
assert_grep()  { grep -qF -- "$2" "$3" && ok "$1"    || bad "$1（$3 里没有 '$2'）"; }
assert_re()    { grep -qE -- "$2" "$3" && ok "$1"    || bad "$1（$3 里没有匹配 /$2/）"; }
assert_ngrep() { ! grep -qF -- "$2" "$3" && ok "$1"  || bad "$1（$3 里不该有 '$2'）"; }
assert_near()  { local d=$(( $2 - $3 )); (( d < 0 )) && d=$(( -d ));
                 (( d <= 2 )) && ok "$1（$2，期望 $3±2）" || bad "$1：实际 $2，期望 $3±2"; }
assert_lt()    { [[ "$2" =~ ^[0-9]+$ && "$3" =~ ^[0-9]+$ ]] && (( $2 < $3 )) \
                 && ok "$1（$2 < $3）" || bad "$1：期望 $2 < $3"; }
assert_absent(){ [[ ! -e "$2" ]] && ok "$1" || bad "$1：$2 不该存在"; }

in_log() { grep -qF -- "$2" "$1"; }        # in_log <日志> <子串>

# ------------------------------- 前置检查 ------------------------------------
[[ -f "$SUT"  ]] || { echo "找不到被测脚本: $SUT"; exit 2; }
[[ -f "$IN"   ]] || {
    echo "找不到测试输入: $IN"
    echo "  已尝试: $ROOT/input_videos/new5_10s.mp4 以及 /workspace/input_videos/new5_10s.mp4"
    echo "  请用 TEST_INPUT=/path/to/small.mp4 指定一个小的 mp4（几百帧即可，测试要反复编解码）"
    exit 2
}
[[ -x "$FFMPEG" ]] || { echo "找不到 $FFMPEG"; exit 2; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "没有 nvidia-smi，跳过"; exit 2; }
command -v flock     >/dev/null 2>&1 || { echo "没有 flock（util-linux），跳过"; exit 2; }
"$FFMPEG" -hide_banner -filters 2>/dev/null | grep -q ' nvinterpolate ' \
    || { echo "$FFMPEG 里没有 nvinterpolate 滤镜，跳过"; exit 2; }

frames() {   # 先读容器里的 nb_frames；拿不到再老实数
    local f
    f=$("$FFPROBE" -v error -select_streams v:0 -show_entries stream=nb_frames -of csv=p=0 "$1" 2>/dev/null)
    if [[ -z "$f" || "$f" == "N/A" ]]; then
        f=$("$FFPROBE" -v error -select_streams v:0 -count_frames \
             -show_entries stream=nb_read_frames -of csv=p=0 "$1" 2>/dev/null)
    fi
    printf '%s' "$f"
}
count_ts()   { ls -1 "$1"/parts/*.ts    2>/dev/null | wc -l; }
count_meta() { ls -1 "$1"/parts/*.meta  2>/dev/null | wc -l; }
count_temp() { ls -1 "$1"/parts/*.part* 2>/dev/null | wc -l; }
actions()    { grep -cE '(skip|redo|run) +p[0-9]+' "$1" 2>/dev/null || true; }
n_skip()     { grep -cE 'skip +p[0-9]+' "$1" 2>/dev/null || true; }
n_run()      { grep -cE 'run +p[0-9]+'  "$1" 2>/dev/null || true; }
n_redo()     { grep -cE 'redo +p[0-9]+' "$1" 2>/dev/null || true; }
# 分片+meta 的指纹（名字/大小/纳秒级 mtime）。用来证明"真的复用了"——
# 只要有一片被重编，它的大小或 mtime 就会变，指纹必然改变。
parts_fp() {
    { ls -1 "$1"/parts/*.ts "$1"/parts/*.meta 2>/dev/null | sort; } \
        | while IFS= read -r f; do stat -c '%n %s %y' "$f"; done \
        | md5sum | cut -d' ' -f1
}
# 只对清单里的文件算指纹。并发时目录整体一直在变（A 还在加新片），
# 只有"某一刻已存在的那批文件"才适合做前后对比。
parts_fp_list() {   # $1=workdir  $2=文件清单
    while IFS= read -r f; do
        [[ -n "$f" ]] || continue
        if [[ -e "$f" ]]; then stat -c '%n %s %y' "$f"; else printf 'MISSING %s\n' "$f"; fi
    done < "$2" | md5sum | cut -d' ' -f1
}
# 独立于日志的完整性检查：每片都得有配对 meta，且 meta 记的切法 == 它该有的切法。
# 能抓到"一个实例改了 part、另一个改了 meta"这类撕裂 —— 光数个数是抓不到的。
assert_metas_consistent() {   # $1=标签  $2=workdir
    local label="$1" W="$2" n=0 i tag want got
    for (( i=0; i<NPARTS; i++ )); do
        tag=$(printf 'p%05d' "$i")
        want=$(awk -v t="$TOTAL" -v i="$i" -v l="$SEG" \
               'BEGIN{ d=t-i*l; if (d>l) d=l; printf "%d %.4f", i*l, d }')
        if [[ -f "$W/parts/$tag.meta" ]]; then
            got=$(cat "$W/parts/$tag.meta")
            [[ "$got" == "$want" ]] || { bad "$label: $tag.meta 不一致（'$got' vs '$want'）"; n=$((n+1)); }
        else
            bad "$label: 缺 $tag.meta"; n=$((n+1))
        fi
    done
    (( n == 0 )) && ok "$label: $NPARTS 片的 meta 全部配对、切法正确"
    return 0
}

SRC_FRAMES=$(frames "$IN")
EXPECT=$(( SRC_FRAMES * 2 ))
TOTAL=$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$IN")
NPARTS=$(awk -v t="$TOTAL" -v l="$SEG" 'BEGIN{ n=int(t/l); if (n*l < t-1e-9) n++; print n }')

TMPROOT=$(mktemp -d /tmp/interp2x_test.XXXXXX)
T0=$SECONDS
KILL_PIDS=()
# 切到自己的临时目录再跑：nvinterpolate 会在【当前工作目录】下创建
# NvOFFRUC/logFRUCError.txt，不切的话每跑一次就往调用者所在目录拉一坨。
# 本测试传给 SUT 的路径全是绝对路径（-w / 输出 / 输入），所以切 CWD 是安全的。
cd "$TMPROOT" || exit 2
cleanup() {
    local p
    for p in ${KILL_PIDS[@]+"${KILL_PIDS[@]}"}; do
        kill -TERM "-$p" 2>/dev/null
    done
    rm -rf "$TMPROOT"
}
trap cleanup EXIT

echo "被测脚本 : $SUT"
echo "测试输入 : $IN  （$SRC_FRAMES 帧 / ${TOTAL}s）"
echo "分片长度 : ${SEG}s → $NPARTS 片；成片期望 ≈ $EXPECT 帧"
# 提示（不是断言）：机器上是否还有别的实例在跑。
# 必须排掉自身与父进程 —— 否则"执行本测试的那条 shell 命令行"里只要含
# interp_2x_safe.sh（很常见，比如 bash -n 一下）就会误报。
OTHERS=$(pgrep -f 'interp_2x_safe\.sh' 2>/dev/null | grep -vx -e "$$" -e "${PPID:-0}" || true)
if [[ -n "$OTHERS" ]]; then
    echo "${YEL}提示${NC}: 检测到别的 interp_2x_safe.sh 实例在跑（本测试用独立 -w，不受影响，只是会抢 GPU）"
fi
echo

# =============================================================================
echo "=== 用例 1：顺序跑两次 —— 锁必须在第一次结束后释放 ==="
# 顺带验证一个常见误解：.lock 文件跑完会留在盘上，但占用与否只看锁、不看文件。
W=$TMPROOT/t1; OUT="$W/out.mp4"
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" >"$W.1.log" 2>&1; rc1=$?
assert_eq "第 1 次 exit code" "$rc1" "0"
assert_file "第 1 次产出成片" "$OUT"
assert_file "跑完后 .lock 仍留在盘上（这也是对的）" "$W/.lock"
assert_near "第 1 次成片帧数（≈2×源帧数）" "$(frames "$OUT")" "$EXPECT"

FP1=$(parts_fp "$W")
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" --overwrite >"$W.2.log" 2>&1; rc2=$?
assert_eq "第 2 次 exit code（锁已释放，不该被误挡）" "$rc2" "0"
assert_ngrep "第 2 次没有报锁" "另一个实例正在跑" "$W.2.log"
assert_eq "第 2 次全部走 skip" "$(n_skip "$W.2.log")" "$NPARTS"
assert_eq "第 2 次没有编码任何片（run 数）" "$(n_run "$W.2.log")" "0"
assert_eq "第 2 次分片+meta 指纹未变（真的是复用，不是重编）" "$(parts_fp "$W")" "$FP1"
assert_metas_consistent "第 2 次" "$W"

# =============================================================================
echo
echo "=== 用例 2：外部持锁 —— 必须被拒、零编码动作、且不得碰已有分片 ==="
W=$TMPROOT/t2; OUT="$W/out.mp4"
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" >"$W.setup.log" 2>&1; rcSetup=$?
assert_eq "前置：先跑出一套完整分片" "$rcSetup" "0"
FP2=$(parts_fp "$W")

( exec 8>"$W/.lock"; flock -n 8 && sleep 5 ) &      # 模拟"另一个实例正占着锁"
HOLDER=$!
sleep 0.5
# 故意不加 --overwrite：此刻目标文件已存在，但覆盖检查排在锁之后，
# 所以必须报"锁冲突"而不是"目标文件已存在"（这也是对检查顺序的回归）
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" >"$W.log" 2>&1; rc=$?
assert_eq "被拒 exit code" "$rc" "1"
assert_grep "报的是锁冲突（而不是目标文件已存在）" "另一个实例正在跑" "$W.log"
assert_eq "编码动作数（应为 0）" "$(actions "$W.log")" "0"
assert_eq "已有分片+meta 指纹未变（被拒方没碰任何文件）" "$(parts_fp "$W")" "$FP2"
assert_eq "没多出临时文件" "$(count_temp "$W")" "0"
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null

# =============================================================================
echo
echo "=== 用例 3：真并发 —— A 在跑，B 随后启动（B 带 --overwrite 以排除覆盖检查干扰）==="
W=$TMPROOT/t3; OUT="$W/out.mp4"
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" >"$W.A.log" 2>&1 & A=$!

# 等 A 真的产出至少一片完整分片再往下走。
# 不能用固定 sleep：A 还没产出时快照是空的，后面的"局部指纹"对比就是拿空对空（假通过）。
GOT_PART=0
for _ in $(seq 600); do                # 最多等 60s（SEG 调大时第一片会更久）
    if ls -1 "$W"/parts/*.ts >/dev/null 2>&1; then GOT_PART=1; break; fi
    sleep 0.1
done

if ( exec 9>"$W/.lock"; flock -n 9 ); then
    if kill -0 "$A" 2>/dev/null; then
        bad "A 运行期间从外部能抢到锁 —— 锁没生效"
    else
        bad "A 已经跑完了，抢到锁不算异常 —— 前置不成立，无法验证锁（建议调大 SEG）"
    fi
else
    ok "A 运行期间从外部抢不到锁"
fi

if (( GOT_PART )); then
    # 记下 B 启动前已在盘上的那批分片；B 被拒后，这些文件必须一字未动。
    # （目录整体还在变 —— A 仍在产出新片 —— 所以只能做这种"局部指纹"对比）
    ls -1 "$W"/parts/*.ts "$W"/parts/*.meta 2>/dev/null | sort > "$W/preB.list"
    FPB=$(parts_fp_list "$W" "$W/preB.list")
    ok "快照到 A 已产出的 $(wc -l < "$W/preB.list") 个文件，可用于局部指纹对比"
else
    skip "等了 60s，A 还没产出完整分片（轮询上限 60s；SEG=$SEG 偏大或机器很忙）→ 跳过'B 未触碰已有分片'的指纹断言"
fi

bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" --overwrite >"$W.B.log" 2>&1; rcB=$?
assert_eq "B exit code" "$rcB" "1"
assert_grep "B 报的是锁冲突（而不是目标文件已存在）" "另一个实例正在跑" "$W.B.log"
assert_eq "B 的编码动作数（应为 0）" "$(actions "$W.B.log")" "0"
(( GOT_PART )) && assert_eq "B 没碰过 A 已有的分片（局部指纹未变）" \
    "$(parts_fp_list "$W" "$W/preB.list")" "$FPB"
wait "$A"; rcA=$?
assert_eq "A exit code" "$rcA" "0"
assert_near "A 成片帧数" "$(frames "$OUT")" "$EXPECT"
assert_eq "A 的分片数" "$(count_ts "$W")" "$NPARTS"
assert_eq "A 的 meta 数" "$(count_meta "$W")" "$NPARTS"
assert_metas_consistent "A" "$W"
assert_eq "B 被拒后无残留临时文件" "$(count_temp "$W")" "0"

# =============================================================================
echo
echo "=== 用例 4：零延迟同时启动 —— 必须恰好一个成功 ==="
W=$TMPROOT/t4; OUT="$W/out.mp4"
( bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" >"$W.C.log" 2>&1; echo $? >"$W.C.rc" ) &
( bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" >"$W.D.log" 2>&1; echo $? >"$W.D.rc" ) &
wait
rcC=$(cat "$W.C.rc"); rcD=$(cat "$W.D.rc")
WINS=0; [[ "$rcC" == 0 ]] && WINS=$((WINS+1)); [[ "$rcD" == 0 ]] && WINS=$((WINS+1))
LOCKED=0
in_log "$W.C.log" "另一个实例正在跑" && LOCKED=$((LOCKED+1))
in_log "$W.D.log" "另一个实例正在跑" && LOCKED=$((LOCKED+1))
assert_eq "成功实例数（C rc=$rcC / D rc=$rcD）" "$WINS" "1"
assert_eq "被拒的那方报的是锁" "$LOCKED" "1"
assert_near "成片帧数（没被两个实例写坏）" "$(frames "$OUT")" "$EXPECT"
assert_eq "分片数" "$(count_ts "$W")" "$NPARTS"
assert_eq "meta 数" "$(count_meta "$W")" "$NPARTS"
assert_metas_consistent "4: 两个实例竞争后" "$W"
assert_eq "残留临时文件" "$(count_temp "$W")" "0"

# =============================================================================
echo
echo "=== 用例 5：持锁进程被 SIGKILL —— 锁必须释放；真实残留必须被清掉 ==="
W=$TMPROOT/t5; OUT="$W/out.mp4"; mkdir -p "$W"
# 用 setsid 起：$$ 即新会话/PGID 首领 → 能整组 kill -9，且不会误伤别的进程
setsid bash -c "echo \$\$ > '$W/pid'; exec bash '$SUT' '$IN' '$OUT' -w '$W' -L '$SEG'" \
    >"$W.kill.log" 2>&1 &
disown $! 2>/dev/null || true          # 移出作业表，免得被 kill -9 后 bash 打一行 "Killed" 噪声

# 关键：等第一片的临时文件真的【写进数据】（非空）再 SIGKILL。
#   · 杀在"片间空档"上根本不留残留 → 用例前提不成立（假通过）
#   · 只等到文件"被创建"也不够 —— ffmpeg 先建文件、缓冲区满了才落盘，
#     那时残留是 0 字节，验证不到"半成品被清掉"这件事
LEFTOVER=""
for _ in $(seq 300); do                # 最多等 30s
    LEFTOVER=$(ls -1 "$W"/parts/p*.ts.part.* 2>/dev/null | head -1)
    [[ -n "$LEFTOVER" && -s "$LEFTOVER" ]] && break
    sleep 0.1
done

P=$(cat "$W/pid" 2>/dev/null || true)
if [[ -n "$P" ]]; then
    KILL_PIDS+=("$P")                  # 万一后面没杀干净，EXIT 时兜底
    kill -9 "-$P" 2>/dev/null
    ok "已对持锁进程组发送 SIGKILL（pid=$P）"
else
    bad "拿不到持锁进程的 pid"
fi
sleep 0.5

LEFT_SIZE=0
if [[ -n "$LEFTOVER" && -s "$LEFTOVER" ]]; then
    LEFT_SIZE=$(stat -c%s "$LEFTOVER")
    ok "SIGKILL 落在写入中途，留下非空残片：$(basename "$LEFTOVER")（$LEFT_SIZE bytes）"
else
    bad "30s 内没等到非空的 p*.ts.part.* —— 用例前提不成立，无法验证清理逻辑"
fi

# 残缺数据绝不能被当成正式分片（这是"杀在中途"最危险的后果）
if [[ -n "$LEFTOVER" ]]; then
    IDX=$(basename "$LEFTOVER" | cut -d. -f1)
    assert_absent "残缺数据没被提升成正式分片（$IDX.ts）" "$W/parts/$IDX.ts"
fi

# 锁必须已经释放：flock 是内核级的，进程死了就释放，不能留下死锁
if ( exec 9>"$W/.lock"; flock -n 9 ); then
    ok "SIGKILL 后锁已释放（不会死锁）"
else
    bad "SIGKILL 后锁仍被持有 —— 重跑会被永远挡住"
fi

# 重跑：清掉真实残留 + 重做被中断的那一片 + 拿到完整成片
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" --overwrite >"$W.after.log" 2>&1; rc=$?
assert_eq "SIGKILL 后立刻重跑 exit code" "$rc" "0"
assert_ngrep "重跑没有报锁" "另一个实例正在跑" "$W.after.log"
if [[ -n "$LEFTOVER" ]]; then
    assert_grep "重跑清掉了真实残留" "clean 残留临时文件 $(basename "$LEFTOVER")" "$W.after.log"
    assert_absent "真实残留已从盘上消失" "$LEFTOVER"
fi
assert_eq "残留临时文件归零" "$(count_temp "$W")" "0"
assert_re "被中断的那一片被重做" 'run +p00000' "$W.after.log"
assert_eq "重跑后分片数" "$(count_ts "$W")" "$NPARTS"
assert_eq "重跑后 meta 数" "$(count_meta "$W")" "$NPARTS"
assert_metas_consistent "5: SIGKILL 后重跑" "$W"
assert_near "重跑后成片帧数" "$(frames "$OUT")" "$EXPECT"
# 残片确实是被"砍断"的：它必须明显小于重跑出来的那个完整分片
FULL_SIZE=$(stat -c%s "$W/parts/p00000.ts" 2>/dev/null || echo 0)
assert_lt "残片确实被截断（残片 < 完整片）" "$LEFT_SIZE" "$FULL_SIZE"

# --- 5b) 残留临时文件不该连累已完成的分片被重做 ------------------------------
# 先给分片+meta 拍指纹：这是"真的复用"的硬证据（日志可以骗人，mtime/size 不会）
FP_BEFORE=$(parts_fp "$W")
touch "$W/parts/p00000.ts.part.999"
bash "$SUT" "$IN" "$OUT" -w "$W" -L "$SEG" --overwrite >"$W.after2.log" 2>&1; rc=$?
assert_eq "5b: 再跑一次 exit code" "$rc" "0"
assert_grep "5b: 清掉了这条残留" "clean 残留临时文件 p00000.ts.part.999" "$W.after2.log"
# 日志说"已清"不等于盘上真没了 —— 单独断言一次，防止"只打日志不删文件"
assert_absent "5b: 这条残留确实从盘上消失了" "$W/parts/p00000.ts.part.999"

assert_eq "5b: 已完成分片全部走 skip" "$(n_skip "$W.after2.log")" "$NPARTS"
# 注意必须断言 run==0，不能只断言 redo==0：分片文件整个缺失时代码只打 run、不打 redo
assert_eq "5b: 没有任何片被编码（run 数）" "$(n_run "$W.after2.log")" "0"
assert_eq "5b: 没有任何片被标记重做（redo 数）" "$(n_redo "$W.after2.log")" "0"
assert_eq "5b: 分片+meta 指纹未变（真的是复用，不是重编）" "$(parts_fp "$W")" "$FP_BEFORE"
assert_eq "5b: 分片数不变" "$(count_ts "$W")" "$NPARTS"
assert_near "5b: 复用后的成片仍正确" "$(frames "$OUT")" "$EXPECT"

# =============================================================================
echo
echo "=== 全局断言：任何日志里都不该出现原始症状 ==="
if grep -rlF 'cannot stat' "$TMPROOT" 2>/dev/null | grep -q .; then
    bad "有日志出现 'cannot stat' —— 并发保护失效"
    grep -rhF 'cannot stat' "$TMPROOT" 2>/dev/null | head -3 | sed 's/^/      /'
else
    ok "所有日志都没有 'mv: cannot stat'"
fi

# =============================================================================
echo
printf '结果: %s 通过, %s 失败, %s 跳过（耗时 %ss）\n' "$PASS" "$FAIL" "$SKIP" "$((SECONDS-T0))"
if [[ "$FAIL" == 0 ]]; then echo "${GREEN}全部通过${NC}"; exit 0; fi
echo "${RED}有失败${NC}"; exit 1
