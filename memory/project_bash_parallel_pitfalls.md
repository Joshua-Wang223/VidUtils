---
name: bash 并行调度的四个坑（wait -n / 进程替换卡死 / exec 孤儿 / 找其它实例）
description: 2026-09-15 给 interp_2x_safe.sh 加 -j 分片级并行时实测踩到的四个 bash 陷阱：wait -n 会提前返回、`while read < <(tail)` 能永久卡死在 pipe_read、被 exec 的 ffmpeg 会变孤儿、pgrep 找实例必须读 /proc cmdline
type: project
---

## 事实

在 bash 里写"最多 N 个任务并行 + 结果回收"的调度器（`interp_2x_safe.sh -j`），
下面四条都是实测踩出来的，不是理论担忧：

**1. `wait -n` 不能当"完成信号"**

它会在**仍有任务在跑**的时候返回 0。实测：4 个子任务、还剩 1 个在编码，
`wait -n` 立刻返回 0。若用它驱动"完成一个就补一个"的循环，会漏片。
（对照实验：4 个 `sleep 0.2/0.4/0.6/3` 的裸后台任务，行为正常；
真实任务里子进程自己还会 fork，job 表状态与想象的不一致。）

**2. `while read … done < <(tail …)` / `tail … | while read …` 会永久卡死**

症状：脚本进程 **0% CPU、状态 S、`wchan=pipe_read`**，日志停在某一行不再前进；
`/proc/<pid>/fd` 里同时能看到**同一个 pipe 的读端和写端**，所以永远等不到 EOF。
只发生在有其它后台子进程存活的时候（实测卡了 1 小时）。
换成普通文件重定向逐行读（`done < "$EVENTS"`）就没这问题 —— 没有管道、没有 fork。

**3. `exec ffmpeg` 放在任务子 shell 里 → 杀子 shell 会留下孤儿 ffmpeg**

`kill <脚本 pid>` 后 trap 杀掉了任务子 shell，但 ffmpeg 是它 `exec` 出来的，
父进程一死就变成孤儿继续写盘（实测剩 2 个 ffmpeg 还在跑）。
改成"后台起 ffmpeg + `wait`，并在子 shell 里 `trap 'kill -TERM $fpid; exit 143' TERM INT HUP`"才收得干净。

**4. 找"另一个实例"不能只靠 `pgrep -f`**

`pgrep -f interp_2x_safe.sh` 会把三类东西也算进来：调用者那条命令行（里面就写着脚本路径）、
bash 为命令替换/`( )` fork 出的**同名子 shell**（`/proc/PID/cmdline` 与父进程一字不差）、
以及**另一个实例自己的子 shell**。可靠做法：逐个读 `/proc/<pid>/cmdline`，
要求"首个参数是以 bash/sh 结尾的 shell、且**第二个参数**就是本脚本名"，
再排掉自己的祖先链与子孙树，最后只保留"祖先里没有其它候选者"的那些。

## Why

这些都是 bash 的语义细节，不看现场根本猜不到：
`wait -n` 的返回时机、进程替换的 fd 继承、`exec` 与信号传播、`pgrep -f` 是子串匹配。
而且它们的症状都很像"脚本没反应"，很容易误判成 ffmpeg 卡住/机器慢 ——
本次就是先看到 `-j 4` 跑一个 8 秒素材用了 7 分钟，才顺着 `wchan=pipe_read` 找到根因。

## How to apply

- 分片/批处理任务的结果回收一律**以"事件文件 + 显式存活检查"为准**：
  子任务结束时向事件文件追加一行（`>>` 单行追加是原子的），父进程轮询读它；
  调度的"还能不能派"看 `dispatched - completed`，判"是不是真出错了"看
  `kill -0 <pid>`（无活着的子进程 + 没得可派 + 结果没前进 = 真错误）。
  `wait -n` 顶多用来回收僵尸，不要依赖它的返回时刻与状态。
- 读文件**不要**用进程替换 / 管道进 while 循环；需要"只读新增部分"就重开文件、跳过已读行数
  （顺便避免 `tail` 快照与 `wc -l` 之间的竞态漏行）。
- 子任务里起外部长任务用"后台 + `wait` + trap 转发信号"，不要 `exec`。
- 新写类似调度器时，先按上面四条自检一遍；`test_interp_2x_lock.sh` 用例 6 已把
  "并行产出与顺序等价 + 并行度不进 recipe"钉住。
