#!/usr/bin/env bash
#
# vidll —— `vidls -l` 的快捷方式（也就是 `ll` 的替代品）。
#
# 本脚本**不含任何逻辑**：只把 `-l` 塞到参数最前面再交给同目录的 vidls.sh。
# 这样参数解析、版式、退出码、探测行为都跟 `vidls -l` 逐字一致，
# 以后改 vidls 也不用回来同步这里。
#
#   vidll              == vidls -l
#   vidll -h           == vidls -l -h
#   vidll -lt /path    == vidls -l -lt /path
#
# 安装：`vidls --install` 会同时把 vidls 与 vidll 接进 PATH。
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

if [ ! -f "$DIR/vidls.sh" ]; then
    echo "[ERROR] 未找到 $DIR/vidls.sh —— vidll 需要它与自己同目录。" >&2
    echo "        （vidll 只是 vidls -l 的快捷方式，本身没有实现）" >&2
    exit 1
fi

exec "$DIR/vidls.sh" -l "$@"
