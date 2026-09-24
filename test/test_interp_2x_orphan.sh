#!/usr/bin/env bash
# =============================================================================
# interp_2x_safe*.sh 的回归测试：中断收尾 / 孤儿 ffmpeg
# （与 interp_2x_safe.sh 同属 VidUtils；SUT 默认取仓库根下的 interp_2x_safe.sh）
#
# 背景（这个测试要守住的原始事故，2026-09-15）:
#   用户在 4K 慢任务上按了 Ctrl+C 之后任务"还在跑"；过一会儿脚本本体没了，
#   却留下一个 **PPID=1 的孤儿 ffmpeg** 继续往 parts/p00003.ts.part.<pid> 写盘
#   （半小时后会白跑完一整片）。两个根因：
#     · Ctrl+C 只发给"终端前台进程组"，用 setsid/& 起的任务根本收不到；
#     · 即使收到，ffmpeg 捕获了 INT/TERM 也要 **11–13s** 才真退出（实测，连
#       640x480 的 lavfi 编码都这样），而旧代码 kill -TERM 之后就 wait，
#       整条链只能干等 → 看起来像"按了没反应"。
#   另外 run_job 是子 shell，`$!` 记的 pid 只活在它自己里；子 shell 被打死
#   （或信号落在"启动 ffmpeg"与"装 trap"之间）时，pid 就没人知道了。
#
# 现在守三条：
#   ① 启动自愈 —— 拿到锁后先按"命令行里在写本目录 parts/ 的 ffmpeg"识别并收掉残留
#      （TERM → 3s → KILL），然后才清 .part；且**不能误伤自己**（本次仍能正常出片）。
#   ② 收尾有界 —— 给脚本发 SIGTERM 后，所有在写的 ffmpeg 必须在数秒内归零，
#      脚本退出、锁释放（不能像旧版那样干等 ffmpeg 自己收尾）。
#   ③ 真孤儿也能自愈 —— 脚本被 kill -9（走不到 trap）后留下的孤儿，
#      下次用同一个 -w 启动时必须被收掉。
#
# 用法:
#   bash test/test_interp_2x_orphan.sh
#   SUT=/path/to/interp_2x_safe_v1.sh bash test/test_interp_2x_orphan.sh
#   SRC=/path/to/small1080p.mp4 bash test/test_interp_2x_orphan.sh    # 省掉现场生成素材
#
# 退出码:  0 = 全部通过   1 = 有用例失败   2 = 环境不具备，跳过
#
# 环境变量:
#   SUT          被测脚本（默认与本脚本同目录的 interp_2x_safe.sh）
#   SRC          测试素材；不给就用 ffmpeg 现场生成一段 1080p/30s
#   TERM_BUDGET  SIGTERM 后等待 ffmpeg 归零的秒数上限，默认 8
#   FFMPEG / FFPROBE
#
# 说明:
#   · 只用 mktemp 出来的 -w 目录，绝不碰默认的 /workspace/interp_2x 与别人的产物。
#   · 清理时只 kill 自己记录过的 pid；不做任何按模式的 pkill。
#   · 判定一律「看事实」：进程是否真死了、文件是否真没了、成片是否真出 —— 不采信脚本的自我报告。
#   · 进程枚举一律读 /proc（`ps -o --ppid` 与 `pgrep -f` 在本环境都不可靠：
#     前者会返回空，后者会把"命令行里含该模式"的自己匹配进去）。
# =============================================================================
set -uo pipefail

# ROOT 是**仓库根**（本脚本在 test/ 下）：SUT 默认值相对它解析，
# 整个 VidUtils 目录搬到哪儿都能直接跑。
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SUT=${SUT:-$ROOT/interp_2x_safe.sh}
FFMPEG=${FFMPEG:-/usr/local/bin/ffmpeg}
FFPROBE=${FFPROBE:-ffprobe}
TERM_BUDGET=${TERM_BUDGET:-8}

GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; NC=$'\033[0m'
PASS=0; FAIL=0; SKIP=0
ok()   { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  ${RED}FAIL${NC} %s\n" "$1"; FAIL=$((FAIL+1)); }
skip() { printf "  ${YEL}SKIP${NC} %s\n" "$1"; SKIP=$((SKIP+1)); }
assert_eq()     { [[ "$2" == "$3" ]] && ok "$1（$2）" || bad "$1：实际 '$2'，期望 '$3'"; }
assert_file()   { [[ -f "$2" ]] && ok "$1" || bad "$1：$2 不存在"; }
assert_absent() { [[ ! -e "$2" ]] && ok "$1" || bad "$1：$2 不该存在"; }
assert_grep()   { grep -qF -- "$2" "$3" && ok "$1" || bad "$1（$3 里没有 '$2'）"; }

# ---- 读 /proc 的小工具 --------------------------------------------------------
proc_alive() {   # 僵尸不算活着（kill -0 对僵尸返回成功，会把已退出的当成"还在跑"）
    local st
    st=$(awk '{print $3}' "/proc/$1/stat" 2>/dev/null) || return 1
    [[ -n "$st" && "$st" != Z ]]
}
writers() {      # 命令行里在写 $1（某目录）的 ffmpeg pid；只认 argv[0] 以 ffmpeg 开头的
    local d p; local -a a=()
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [[ -r "$d/cmdline" ]] || continue
        mapfile -d '' -t a < "$d/cmdline" || continue
        (( ${#a[@]} >= 2 )) || continue
        case "${a[0]##*/}" in ffmpeg*) ;; *) continue ;; esac
        case " ${a[*]} " in *"$1"*) proc_alive "$p" && printf '%s\n' "$p" ;; esac
    done
}
n_writers() { writers "$1" | wc -l | tr -d ' '; }
dump_tail() {   # $1=日志 $2=标签
    [[ -s "$1" ]] || { printf "        （%s 没有日志）\n" "$2"; return; }
    printf "        ---- %s 日志末 12 行 ----\n" "$2"
    tail -n 12 "$1" | sed 's/^/        /'
}
wait_writers_gone() {   # $1=目录 $2=上限秒 → stdout 实际等待秒数
    local t0 i
    t0=$(date +%s.%N)
    for i in $(seq $(( $2 * 5 ))); do
        [[ "$(n_writers "$1")" == 0 ]] && break
        sleep 0.2
    done
    awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN{printf "%.1f", b-a}'
}
ppid_of() { awk '/^PPid:/{print $2}' "/proc/$1/status" 2>/dev/null; }
children_of() {   # 直接子进程（不用 ps --ppid，本环境不可靠）
    local d p
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [[ "$(ppid_of "$p")" == "$1" ]] && printf '%s\n' "$p"
    done
}
is_ffmpeg() {
    [[ -r "/proc/$1/cmdline" ]] || return 1
    [[ "$(tr '\0' ' ' < "/proc/$1/cmdline")" == *ffmpeg* ]]
}
# 谁持有这把锁（排除 ffmpeg）—— 锁冲突时占锁的可能是"没退干净的 run_job 子 shell"，
# 子 shell 会继承父进程的 fd 9。按 fd 找比按进程树枚举稳（子 shell 可能在枚举之后才 fork）。
lock_holders() {   # $1=workdir  $2=排除的 pid（脚本本体）
    local d p fd
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [[ "$p" == "$2" ]] && continue
        for fd in "$d"/fd/*; do
            [[ -e "$fd" ]] || continue
            [[ "$(readlink "$fd" 2>/dev/null)" == "$1/.lock" ]] || continue
            is_ffmpeg "$p" || printf '%s\n' "$p"
            break
        done
    done
}
find_script_pid() {   # $1=SUT $2=workdir
    local d p; local -a a=()
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [[ -r "$d/cmdline" ]] || continue
        mapfile -d '' -t a < "$d/cmdline" || continue
        (( ${#a[@]} >= 2 )) || continue
        case "${a[0]##*/}" in bash|sh) ;; *) continue ;; esac
        case " ${a[*]} " in *" $1 "*" $2 "*) printf '%s\n' "$p"; return 0 ;; esac
    done
    return 1
}
lock_free() { ( exec 8>"$1/.lock" 2>/dev/null && flock -n 8 ); }
wait_proc_gone() {    # $1=pid $2=上限秒
    local i
    for i in $(seq $(( $2 * 5 ))); do
        proc_alive "$1" || return 0
        sleep 0.2
    done
    return 1
}

declare -a KILL_PIDS=()
cleanup() {
    local p
    for p in ${KILL_PIDS[@]+"${KILL_PIDS[@]}"}; do
        kill -9 "-$p" 2>/dev/null; kill -9 "$p" 2>/dev/null
    done
    if [[ -n "${W:-}" ]]; then
        while read -r p; do [[ -n "$p" ]] && kill -9 "$p" 2>/dev/null; done < <(writers "$W/parts/")
    fi
    [[ -n "${TMPROOT:-}" ]] && rm -rf "$TMPROOT"
}
trap cleanup EXIT

# ------------------------------- 前置检查 ------------------------------------
[[ -f "$SUT" ]] || { echo "找不到被测脚本: $SUT"; exit 2; }
[[ -x "$FFMPEG" ]] || { echo "找不到 $FFMPEG"; exit 2; }
command -v flock >/dev/null 2>&1 || { echo "没有 flock（util-linux），跳过"; exit 2; }

TMPROOT=$(mktemp -d /tmp/interp2x_orphan.XXXXXX)
SRC=${SRC:-$TMPROOT/src.mp4}
if [[ ! -f "$SRC" ]]; then
    echo "生成测试素材：1080p/30s（慢后端才抓得住「在飞的分片」）…"
    if ! "$FFMPEG" -y -v error -f lavfi -i "testsrc2=size=1920x1080:rate=25" -t 30 \
            -c:v libx264 -pix_fmt yuv420p -preset ultrafast "$SRC"; then
        echo "素材生成失败（$SRC）"; exit 2
    fi
fi

# SUT 支持 --backend 就压到 cpu：minterpolate 慢，分片"在飞"的时间足够长，容易观察
if "$SUT" --help 2>&1 | grep -q -- '--backend'; then
    BE=(--backend cpu --cpu-preset ultrafast); BE_DESC="cpu（minterpolate）"
else
    BE=(); BE_DESC="脚本默认后端"
fi

echo "被测脚本 : $SUT"
echo "测试素材 : $SRC"
echo "后端参数 : $BE_DESC"
echo "工作目录 : $TMPROOT"
echo

# =============================================================================
echo "=== 用例 1：启动自愈 —— 收掉在写本目录 parts/ 的残留，且不误伤自己的编码 ==="
W=$TMPROOT/c1
mkdir -p "$W/parts"
"$FFMPEG" -nostdin -y -v error -f lavfi -i "testsrc2=size=640x480:rate=25:d=120" \
    -c:v libx264 -preset ultrafast -f mpegts "$W/parts/p99999.ts.part.999" >"$TMPROOT/c1.foreign.log" 2>&1 &
FOREIGN=$!
FOREIGN_OK=0
for _ in $(seq 50); do            # 最多等 10s，等它真的把数据写进去
    [[ -s "$W/parts/p99999.ts.part.999" ]] && { FOREIGN_OK=1; break; }
    sleep 0.2
done
if (( FOREIGN_OK == 1 )) && proc_alive "$FOREIGN"; then
    ok "前置：外来写手已就位（pid $FOREIGN，正在写 $W/parts/）"
    bash "$SUT" "$SRC" "$W/out.mp4" -w "$W" "${BE[@]}" -T 2 -L 1 >"$TMPROOT/c1.log" 2>&1
    assert_eq "脚本自身 exit code" "$?" "0"
    assert_grep "启动时报告了残留" "残留" "$TMPROOT/c1.log"
    proc_alive "$FOREIGN" && bad "外来写手仍活着（未被收掉）" || ok "外来写手已被收掉"
    assert_absent "它的 .part 半成品已被清掉" "$W/parts/p99999.ts.part.999"
    assert_file "脚本自己的成片正常产出（没误伤自己）" "$W/out.mp4"
    assert_eq "在写本目录的 ffmpeg 归零" "$(n_writers "$W/parts/")" "0"
else
    skip "外来写手没起来/没写出数据，跳过用例 1"
    dump_tail "$TMPROOT/c1.foreign.log" "外来写手"
fi
kill -9 "$FOREIGN" 2>/dev/null

# =============================================================================
echo
echo "=== 用例 2：收尾有界 —— SIGTERM 后数秒内 ffmpeg 归零，脚本退出、锁释放 ==="
W=$TMPROOT/c2
setsid bash "$SUT" "$SRC" "$W/out.mp4" -w "$W" "${BE[@]}" -T 30 -L 15 \
    >"$TMPROOT/c2.log" 2>&1 </dev/null &
SPID=""
for _ in $(seq 60); do SPID=$(find_script_pid "$SUT" "$W") && break; sleep 0.2; done
[[ -n "$SPID" ]] && KILL_PIDS+=("$SPID")
GOT=0
for _ in $(seq 150); do        # 最多等 30s 出现"在写的分片"
    [[ "$(n_writers "$W/parts/")" != 0 ]] && { GOT=1; break; }
    sleep 0.2
done
if [[ -z "$SPID" ]]; then
    skip "拿不到脚本 pid，跳过用例 2"
elif (( GOT == 0 )); then
    skip "30s 内没有分片在写（素材太短 / 后端太快），跳过用例 2"
else
    ok "前置：$(n_writers "$W/parts/") 个 ffmpeg 正在写分片"
    kill -TERM "$SPID" 2>/dev/null
    EL=$(wait_writers_gone "$W/parts/" "$TERM_BUDGET")
    if [[ "$(n_writers "$W/parts/")" == 0 ]]; then
        ok "SIGTERM 后 ${EL}s 内 ffmpeg 全部归零（预算 ${TERM_BUDGET}s）"
    else
        bad "SIGTERM 后 ${EL}s 仍有 $(n_writers "$W/parts/") 个 ffmpeg 在写"
    fi
    wait_proc_gone "$SPID" 5 && ok "脚本本体已退出" || bad "脚本本体仍未退出"
    lock_free "$W" && ok "锁已释放" || bad "锁仍被占用（有子 shell 没退干净）"
    assert_grep "日志里有有界收尾的痕迹" "中断" "$TMPROOT/c2.log"
fi

# =============================================================================
echo
echo "=== 用例 3：真孤儿 —— 脚本被 kill -9 后留下的 ffmpeg，下次启动必须收掉 ==="
W=$TMPROOT/c3
setsid bash "$SUT" "$SRC" "$W/out.mp4" -w "$W" "${BE[@]}" -T 30 -L 15 \
    >"$TMPROOT/c3.log" 2>&1 </dev/null &
SPID=""
for _ in $(seq 60); do SPID=$(find_script_pid "$SUT" "$W") && break; sleep 0.2; done
GOT=0
for _ in $(seq 150); do
    [[ "$(n_writers "$W/parts/")" != 0 ]] && { GOT=1; break; }
    sleep 0.2
done
if [[ -z "$SPID" || "$GOT" == 0 ]]; then
    skip "没等到「脚本 + 在飞分片」的前置，跳过用例 3"
else
    FPID=$(writers "$W/parts/" | head -1)
    ok "前置：脚本 pid=$SPID、在写的 ffmpeg pid=$FPID"
    # 造孤儿的要点：脚本本体和**所有持锁的非 ffmpeg 进程**（run_job 子 shell 继承了 fd 9）
    # 都必须死，锁才会放；而 ffmpeg 要留着当孤儿。子 shell 可能在枚举之后才 fork，
    # 所以边杀边重试几轮。
    for _ in $(seq 20); do
        kill -9 "$SPID" 2>/dev/null
        while read -r k; do
            [[ -n "$k" ]] || continue
            kill -9 "$k" 2>/dev/null
        done < <(lock_holders "$W" "$SPID")
        lock_free "$W" && break
        sleep 0.3
    done
    sleep 0.5
    if ! proc_alive "$FPID"; then
        skip "没造出孤儿（ffmpeg 已自己退出），跳过用例 3 后半段"
    elif ! lock_free "$W"; then
        skip "锁仍被某个进程占着 → 无法验证启动自愈（用例前提不成立）"
    else
        ok "已造出真孤儿（pid $FPID 还活着）且锁已释放"
        # 重跑必须用**与建目录时相同的参数**（换成别的 L/T 会被 recipe 挡下 —— 那是另一条防线，
        # 不是本用例要测的东西）。这里只观察"启动阶段"，所以放后台、确认完就收掉，不等它跑完整片。
        setsid bash "$SUT" "$SRC" "$W/out.mp4" -w "$W" "${BE[@]}" -T 30 -L 15 \
            >"$TMPROOT/c3b.log" 2>&1 </dev/null &
        RPID=""
        for _ in $(seq 75); do RPID=$(find_script_pid "$SUT" "$W") && break; sleep 0.2; done
        [[ -n "$RPID" ]] && KILL_PIDS+=("$RPID")
        ACQ=0
        for _ in $(seq 100); do            # 最多 20s：等它报"残留"（= 已拿到锁、正在清残留）
            grep -q "残留" "$TMPROOT/c3b.log" && { ACQ=1; break; }
            sleep 0.2
        done
        if (( ACQ == 1 )); then
            ok "重跑拿到了锁并开始清残留（没被孤儿占着的锁挡住）"
        else
            bad "重跑 20s 内没报残留（可能仍被锁挡着）"
            dump_tail "$TMPROOT/c3b.log" "重跑"
        fi
        grep -q "拒绝复用" "$TMPROOT/c3b.log" && bad "重跑被 recipe 拒绝了（不该）" || ok "重跑没被 recipe 拒绝"
        # 清理是「TERM → 最多 3s → KILL」，所以要等它真的消失再断言（别一看到"残留"就判）
        GONE=0
        for _ in $(seq 60); do proc_alive "$FPID" || { GONE=1; break; }; sleep 0.2; done
        if (( GONE == 1 )); then
            ok "孤儿已被下次启动收掉（在 TERM→3s→KILL 窗口内）"
        else
            bad "孤儿 ffmpeg 12s 后仍活着（启动自愈失败）"
        fi
        if [[ -n "$RPID" ]]; then
            kill -TERM "$RPID" 2>/dev/null
            wait_proc_gone "$RPID" 8 || kill -9 "$RPID" 2>/dev/null
        fi
        while read -r p; do [[ -n "$p" ]] && kill -9 "$p" 2>/dev/null; done < <(writers "$W/parts/")
    fi
fi

# =============================================================================
echo
printf '结果: %s 通过, %s 失败, %s 跳过\n' "$PASS" "$FAIL" "$SKIP"
if [[ "$FAIL" == 0 ]]; then echo "${GREEN}全部通过${NC}"; exit 0; fi
echo "${RED}有失败${NC}"; exit 1
