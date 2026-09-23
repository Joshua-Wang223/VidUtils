#!/usr/bin/env bash
# test/check_readme_refs.sh — 收尾核对：README 必须引用仓库里每个「工具文件」的文件名。
#
# 起因（2026-09-23）：memory/ 索引、README「目录结构」树、probe/enum_cmds.py 都出现过
# 「文件已入库、README 却没提」的漏登 —— 一次补了 6 条。靠肉眼每次重核不可靠，做成可跑的
# 判据：跑一遍，漏登的逐条列出并 exit 1。
#
# 覆盖范围（相对仓库根的 git 路径）：
#   · probe/  verify/ 下的全部文件
#   · test/ 下的脚本（test/*.sh、test/*.py），**不含** test/baseline/ 夹具
#   · 仓库根的 *.py / *.sh / *.cmd 可执行工具
# 明确不看：README.md 自身、memory/（另有 memory/MEMORY.md 索引）、Plan/、*.md 文档、
# .gitignore / gitignore.txt / tar_excludes.txt（基础设施，非用户工具）。
#
# 用法：bash test/check_readme_refs.sh    # 漏登则 exit 1
#      SELFTEST=1 bash test/check_readme_refs.sh   # 自检判据本身（四格，不碰本仓 README）
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$HERE")
README="$ROOT/README.md"
SELF="${BASH_SOURCE[0]}"

# ═══════════════════════════════════════════════════════════════════
#  自检：在 temp/ 里造一个临时仓库，把判据的四个分支真跑一遍
#  （正向 / 漏登 / 空集 / README 缺失）—— 恒真装置最容易蒙混过关，
#  尤其要覆盖"一个工具文件都没扫到"这种**假阳性**形态。
# ═══════════════════════════════════════════════════════════════════
selftest() {
  local work="$ROOT/temp/readme_refs_selftest" pass=0 fail=0 rc=0
  rm -rf "$work"
  mkdir -p "$work/test"
  cp "$SELF" "$work/test/"

  set +e
  (
    cd "$work" || exit 1
    git init -q -b main >/dev/null 2>&1 || { echo "  [FAIL] git init 失败"; exit 1; }
    git config user.email t@t
    git config user.name t
    echo x > tool_demo.sh
    git add tool_demo.sh >/dev/null
  )
  [[ $? -eq 0 ]] || { echo "✗ 临时仓库准备失败"; rm -rf "$work"; return 1; }

  ck() {   # ck <标签> <期望 exit> <实际 exit>
    if [[ "$3" == "$2" ]]; then
      echo "  [OK] $1（exit $3）"; pass=$((pass + 1))
    else
      echo "  [FAIL] $1：期望 exit $2，实得 $3"; fail=$((fail + 1))
    fi
  }
  # ⚠ 子进程必须显式 `SELFTEST=0`：否则它会继承本进程的 SELFTEST=1、
  #   再进自检 → 无限递归建目录（2026-09-23 实测，走了两分钟才发现）。
  run() { (cd "$work" && SELFTEST=0 bash test/check_readme_refs.sh >/dev/null 2>&1); echo $?; }

  printf '# README\n\n- `tool_demo.sh`\n' > "$work/README.md"
  ck "正向：README 提到了工具 → 0" 0 "$(run)"

  printf '# README\n\n（故意不提）\n' > "$work/README.md"
  ck "负向：README 漏登 → 1" 1 "$(run)"

  # 空集：文件还在但不再被跟踪 → 扫到 0 个工具文件。**必须判失败**，
  # 否则"过滤规则写错 / git ls-files 失败"会被报成"全部引用齐全"。
  (cd "$work" && git rm -q --cached tool_demo.sh >/dev/null)
  printf '# README\n' > "$work/README.md"
  ck "空集：一个工具文件都没扫到 → 2（不假阳性）" 2 "$(run)"

  rm -f "$work/README.md"
  ck "异常：README 缺失 → 2" 2 "$(run)"

  set -e
  rm -rf "$work"
  echo
  if (( fail > 0 )); then
    echo "✘ 自检 $fail 项失败（通过 $pass 项）"
    return 1
  fi
  echo "✓ 自检全部通过（$pass 项）"
  return 0
}

if [[ "${SELFTEST:-0}" == "1" ]]; then
  selftest
  exit $?
fi

[[ -f "$README" ]] || { echo "找不到 $README"; exit 2; }

# 先把文件清单取全并把失败显式暴露出来：`while … < <(git ls-files | …)` 的进程替换
# 若失败或为空，循环一次都不进、miss 仍是 0 → 会**静默报绿**（2026-09-23 实测）。
raw=$(git ls-files) || { echo "✗ git ls-files 失败（不在 git 仓库里？）"; exit 2; }
list=$(printf '%s\n' "$raw" \
  | grep -E '^(probe/|verify/|test/[^/]+\.(sh|py)$|[^/]+\.(py|sh|cmd)$)' || true)
total=$(printf '%s\n' "$list" | grep -c . || true)
if (( total == 0 )); then
  echo "✗ 一个工具文件都没扫到 —— 判据失效（git ls-files 输出为空？过滤规则写错？）"
  exit 2
fi

miss=0
while IFS= read -r f; do
  base=$(basename "$f")
  if ! grep -qF -- "$base" "$README"; then
    echo "MISS  $f"
    miss=$((miss + 1))
  fi
done <<< "$list"

if (( miss > 0 )); then
  echo "✗ $miss/$total 个工具文件未被 README 引用（补进「目录结构」树或对应章节）"
  exit 1
fi
echo "✓ README 引用了全部 $total 个工具文件"
