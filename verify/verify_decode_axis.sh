#!/usr/bin/env bash
# verify/verify_decode_axis.sh — CLI 层验证「解码/缩放/编码三轴正交」
# 只做命令构造与退出码检查（--dry-run），不需要 GPU。
# 用法: bash verify/verify_decode_axis.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

F=temp/lockstep
mkdir -p "$F"
[ -f "$F/land.mp4" ] || ffmpeg -nostdin -y -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p "$F/land.mp4"

PASS=0; FAIL=0
ok()   { printf '  [OK]   %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  [FAIL] %s\n     %s\n' "$1" "${2:-}"; FAIL=$((FAIL+1)); }

# hw <参数...> → 输出全文；exit 取真实退出码
hw() { python vidcrop_hwaccel.py --input "$F/land.mp4" --output "$F/o" --dry-run "$@" 2>&1; }
# 只取 -filter:v:0 的值（shlex.join 是按需加引号的，别假设有引号）
vf() { sed -n "s/.*-filter:v:0 \('[^']*'\|[^ ][^ ]*\).*/\1/p" <<< "$1" | sed "s/^'//; s/'\$//"; }

echo "── ① 旧名 --hwaccel 必须硬拒绝 ──"
out=$(hw --mode cover --output-width 1440 --output-height 1080 --hwaccel none); rc=$?
if [ "$rc" = 2 ] && grep -q '已更名为 --decode' <<<"$out"; then
  ok "--hwaccel 退出 2 且提示改名"
else bad "--hwaccel 应退出 2 且提示改名" "rc=$rc"; fi
if grep -q -- '--decode cpu' <<<"$out"; then ok "提示里给出了三轴等价写法"; else bad "提示里应给出等价写法"; fi

echo "── ② --decode 取值 ──"
out=$(hw --mode cover --output-width 1440 --output-height 1080 --decode none --codec libx264); rc=$?
[ "$rc" = 0 ] && ok "--decode none 等价 cpu（接受）" || bad "--decode none 应被接受" "rc=$rc"
out=$(hw --mode cover --output-width 1440 --output-height 1080 --decode nope); rc=$?
[ "$rc" = 2 ] && ok "--decode 非法值退出 2" || bad "--decode 非法值应退出 2" "rc=$rc"

echo "── ③ --fallback-policy 收敛为 auto/strict，旧值报错且给等价写法 ──"
for old in cpu-only nvenc-only strict-cuda; do
  out=$(hw --mode cover --output-width 1440 --output-height 1080 --fallback-policy "$old"); rc=$?
  if [ "$rc" = 2 ] && grep -q '等价的三轴写法' <<<"$out"; then
    ok "--fallback-policy $old 退出 2 且给等价写法"
  else bad "--fallback-policy $old 应退出 2 并给等价写法" "rc=$rc"; fi
done
out=$(hw --mode cover --output-width 1440 --output-height 1080 --fallback-policy strict); rc=$?
[ "$rc" = 0 ] && ok "--fallback-policy strict 被接受" || bad "strict 应被接受" "rc=$rc"

echo "── ④ 三轴相互独立：--decode cpu 不再等于纯 CPU ──"
out=$(hw --mode cover --output-width 1440 --output-height 1080 --decode cpu --codec h264_nvenc); rc=$?
if [ "$rc" = 0 ] && ! grep -q '\-hwaccel' <<<"$out"; then
  ok "--decode cpu + h264_nvenc：不出现 -hwaccel（解码轴只管解码）"
else bad "--decode cpu 不应下发 -hwaccel" "rc=$rc"; fi
# 硬解 + 软编：这里只能验「被接受、不报冲突」——本机无 CUDA，-hwaccel cuda 是否真的
# 下发取决于硬件，那条由 verify/verify_cuda_scale.py 的假 caps 单测覆盖。
out=$(hw --mode cover --output-width 1440 --output-height 1080 --decode cuda --codec libx264); rc=$?
if [ "$rc" = 0 ] && ! grep -qi '冲突' <<<"$out"; then
  ok "--decode cuda + libx264：合法组合，不报冲突（实际后端由硬件决定）"
else bad "硬解 + 软编应合法" "rc=$rc"; fi

echo "── ⑤ 三轴全 CPU → 跳过全部 GPU 探测（旧 --hwaccel none 的快速路径）──"
out=$(hw --mode cover --output-width 1440 --output-height 1080 \
        --decode cpu --scale-algo libswscale-lanczos --codec libx264); rc=$?
if [ "$rc" = 0 ] && grep -q '跳过全部 GPU 探测' <<<"$out" \
   && ! grep -q '正在检测硬件加速能力' <<<"$out"; then
  ok "三轴全 CPU：跳过探测、无探测输出"
else bad "三轴全 CPU 应跳过探测" "rc=$rc"; fi

echo "── ⑥ 软解 + 显存内缩放（hwupload_cuda 链）──"
out=$(hw --mode cover --output-width 1440 --output-height 1080 \
        --decode cpu --scale-algo cuda-lanczos); rc=$?
v=$(vf "$out")
if [ "$rc" = 0 ] && [[ "$v" == hwupload_cuda,scale_cuda=* ]] && ! grep -q '\-hwaccel' <<<"$out"; then
  ok "链首 hwupload_cuda，且不下发 -hwaccel"
else bad "应为 hwupload_cuda 链且无 -hwaccel" "rc=$rc vf=$v"; fi
[[ "$v" == *'hwdownload,format=nv12'* ]] \
  && ok "显式 hwdownload,format=nv12（自动插入会静默丢 crop）" \
  || bad "缺显式 hwdownload" "$v"

echo "── ⑦ strict 不降级：显式后端不可用即报错 ──"
out=$(hw --mode cover --output-width 1440 --output-height 1080 \
        --decode cuda --fallback-policy strict); rc=$?
if [ "$rc" = 2 ] && grep -q 'strict' <<<"$out"; then
  ok "--decode cuda + strict（本机无 CUDA）→ 退出 2"
else bad "strict 下不可用应退出 2" "rc=$rc"; fi
out=$(hw --mode cover --output-width 1440 --output-height 1080 --decode cuda); rc=$?
[ "$rc" = 0 ] && ok "--decode cuda + auto（默认）→ 降级继续" || bad "auto 应降级继续" "rc=$rc"

echo
echo "── 一致 $PASS / 失败 $FAIL ──"
[ "$FAIL" = 0 ] || exit 1
