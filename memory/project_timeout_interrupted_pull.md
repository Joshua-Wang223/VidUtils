---
name: 容器超时打断 git pull 会留下 0 字节工作区文件（含恢复法）
description: 工作区若干已跟踪文件变 0 字节但 HEAD/索引完好（git status 显示 M）——根因是 pull fast-forward 被容器超时打断；判据是 0 字节集合==diff ORIG_HEAD..HEAD 且 mtime 同刻；用 git checkout HEAD -- <文件> 恢复
type: project
---

**现象**：`git status` 里一批已跟踪文件显示 ` M`，但 `stat` 是 **0 字节**，而
`git cat-file -s HEAD:<file>` 有正常内容；`git diff --cached` 干净。

**根因**：`git pull origin main --no-rebase`（fast-forward）在**写工作区文件的过程中被容器
超时/断连打断**。ref 与 reflog 已更新到新 commit，但工作区只写了一部分，其余留在 0 字节。

**判据（两条同时满足即可断定）**：
1. 所有 0 字节文件与 `.git/ORIG_HEAD`、`.git/FETCH_HEAD` 的 **mtime 完全相同**
   （2026-10-16 那次是 `03:19:10.28`，纳秒级同刻 = 一次批量操作，不是逐个写坏）；
2. 0 字节文件集合 **==** `git diff --name-only ORIG_HEAD HEAD` 的集合（pull 要改写的文件）。

**影响面（2026-10-16 实测）**：**18 个** —— `vidcrop_hwaccel.py`（334 KB 主脚本！）
+ 17 个 `memory/` 文件。注意别只看用户提到的目录，主脚本也可能中招。

**恢复**：`git checkout HEAD -- <每个文件>`（**必须逐文件或正确分词**；zsh 下
`git checkout HEAD -- $files` 不会按换行分词，会把整串当一个 pathspec 报
`did not match any file(s)`）。索引已等于 HEAD，恢复只写工作区、不产生新 diff。

**Why:** 这种损坏**看起来像**「另一个会话把文件清空了」，容易误判成并发写坏、甚至误删工作区
去重来；实际是本地 pull 未完成，HEAD 完好无损，恢复零风险。漏判的代价是拿空脚本/空基准
数据继续干活。

**How to apply:** 容器超时/断连重连后，**先扫一遍再动手**：
```sh
git ls-files -z | while IFS= read -r -d '' f; do
  ws=$(stat -c %s "$f" 2>/dev/null || echo MISSING); hd=$(git cat-file -s "HEAD:$f" 2>/dev/null)
  [ "$ws" = 0 ] && [ "$hd" != 0 ] && echo "0字节: $f"
done
```
有命中就 `git checkout HEAD -- <file>` 恢复，恢复后建议对 `.py` 做 `compile()`、对 `.json`
做 `json.load()` 校验。恢复完再确认 `git status` 里除自己的改动外**无其它 modified**——
这同时证明没有其它半损坏文件。
