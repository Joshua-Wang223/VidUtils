#!/usr/bin/env bash
# Accessory/_paths.sh — 仓库根与临时目录定位（供 Accessory/{verify,probe,test} 下的 .sh 共用）
#
# 2026-10-06：verify/ probe/ test/ 从仓库根移进 Accessory/ 后，各 .sh 里
# `cd "$(dirname "${BASH_SOURCE[0]}")/.."` 会停在 Accessory/ 而不是仓库根。
# 那类错误**不会立刻失败**：cd 成功、后续相对路径只是"不存在"，
# 于是探针/回归脚本可能改在 Accessory/ 下另建目录、或断言静默降级。
#
# 用法（在任何 .sh 顶部）：
#   HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   . "$HERE/../_paths.sh"        # 定义 VU_ROOT / VU_TEMP
#   cd "$VU_ROOT" || exit 1
#
# 为什么向上搜标记而不是数 `/..` 的层数：目录再被移动一次时，数层数的写法会
# 再次悄悄失效。找不到标记就 exit 1，不静默兜底。
set -euo pipefail

# 从本文件位置向上找仓库根（.git 优先，退用根级 convert_crf.py）
_vu_find_root() {
  local d
  d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  while [ "$d" != "/" ]; do
    if [ -e "$d/.git" ] || [ -f "$d/convert_crf.py" ]; then
      printf '%s\n' "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  echo "[ERROR] 从 ${BASH_SOURCE[0]} 向上找不到仓库根（找过 .git / convert_crf.py）。" >&2
  echo "        通常意味着脚本被移到了仓库之外，或仓库结构变了。" >&2
  return 1
}

VU_ROOT="$(_vu_find_root)" || return 1 2>/dev/null || exit 1
# 本机临时目录：2026-10-06 起 temp/ 从仓库根迁到 Accessory/temp/
VU_TEMP="$VU_ROOT/Accessory/temp"
