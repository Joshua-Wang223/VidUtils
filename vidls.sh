#!/usr/bin/env bash
#
# vidls —— 带视频属性探测的 ls / ll 替代品（启动器）
#
# 真正实现在同目录的 vidls.py。本脚本只负责「找到自己真实所在的目录并起 Python」，
# 这样即使通过 /usr/local/bin/vidls 这类软链调用，也能定位到 vidls.py。
#
# 安装：vidls --install   （自检环境 + 建软链 + 配 PATH）
#
set -euo pipefail

SOURCE=${BASH_SOURCE[0]:-$0}
# 逐层跟随软链，直到拿到真实路径（不依赖 readlink -f，macOS 上也有）
while [ -L "$SOURCE" ]; do
    DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)
    SOURCE=$(readlink "$SOURCE")
    case $SOURCE in
        /*) ;;
        *)  SOURCE=$DIR/$SOURCE ;;
    esac
done
DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY=$cand
        break
    fi
done

if [ -z "$PY" ]; then
    echo "[ERROR] 未找到 python3（需要 Python 3.8+）。" >&2
    echo "        请安装 Python 后重试，或直接用解释器调用：$DIR/vidls.py" >&2
    exit 1
fi

if [ ! -f "$DIR/vidls.py" ]; then
    echo "[ERROR] 未找到 $DIR/vidls.py —— 请确认 vidls.py 与本脚本在同一目录。" >&2
    exit 1
fi

exec "$PY" "$DIR/vidls.py" "$@"
