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
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$HERE")
README="$ROOT/README.md"
cd "$ROOT"

[[ -f "$README" ]] || { echo "找不到 $README"; exit 2; }

miss=0
total=0
while IFS= read -r f; do
  total=$((total + 1))
  base=$(basename "$f")
  if ! grep -qF -- "$base" "$README"; then
    echo "MISS  $f"
    miss=$((miss + 1))
  fi
done < <(git ls-files \
  | grep -E '^(probe/|verify/|test/[^/]+\.(sh|py)$|[^/]+\.(py|sh|cmd)$)' \
  | sort)

if (( miss > 0 )); then
  echo "✗ $miss/$total 个工具文件未被 README 引用（补进「目录结构」树或对应章节）"
  exit 1
fi
echo "✓ README 引用了全部 $total 个工具文件"
