---
name: ffmpeg 挂起的两种根因：SIGTTIN 与输出管道反压
description: ffmpeg 挂死有两种根因——SIGTTIN(状态T，加-nostdin修)和输出管道写满反压(状态S且CPU冻结，-nostdin没用)；诊断方法不同
type: project
---

在本机（CodeBuddy Bash 工具、CI、后台任务等托管环境）调用 ffmpeg 时，**必须显式加
`-nostdin`（或给 subprocess 传 `stdin=subprocess.DEVNULL`）**。ffprobe 不受影响
（没有 stdin 交互）。

## 根因 1：SIGTTIN —— `-nostdin` 能修

当 ffmpeg 处于**非前台进程组**（典型：被放到后台运行），而 stdin 指向该会话的**控制终端**
时，它一读 tty，内核就发 SIGTTIN 把它**停止**，进程状态变成 **`T`**。
表现：`%CPU 0%`、无任何输出、永不退出，看着像死锁。

**2026-09-14 在 T4 上用 pty 对照实验直接抓到状态：**

| 场景 | ffmpeg 状态 | 结果 |
|---|---|---|
| 后台运行 + stdin=控制终端，**不加** `-nostdin` | `T` | 被 SIGTTIN 停止，永久挂住 |
| 同上，**加** `-nostdin` | `S` | 正常运行完成 |

## 根因 2：输出管道反压 —— `-nostdin` **没用**

父进程把 ffmpeg 的 stdout（或 stderr）接成 PIPE 却**从不读取**，管道 64KB 缓冲写满后
ffmpeg 阻塞在 write 上。进程状态是 **`S`**（不是 `T`），且 **CPU ticks 冻结不增长**。

**2026-09-14 实测（`-f rawvideo -`，720p 每帧 1.38MB，首帧即撑爆管道）：**

| 场景 | 观测 | 结果 |
|---|---|---|
| stdout 接 PIPE 不读 | `state=S`，cpu_ticks 连续 5 次采样**恒为 26** | 30s 未退出，确认阻塞 |
| stdout 接 DEVNULL（对照） | — | 2.0s 完成 |

修法：用 `stdin/stdout/stderr=DEVNULL`，或用 `subprocess.communicate()` 真正把管道读空。
加 `-nostdin` 对此**完全无效**。

## 已验证「不会阻塞」的情况（别往这些方向排查）

- stdin 是**不关闭也无数据的管道/FIFO** → 正常完成
- stdin 是空闲 **pty**、但进程在**前台**进程组 → 正常完成
- 因此旧说法「stdin 无数据的管道会让 ffmpeg 阻塞在 read()」**是错的**，已废弃。
  原因：ffmpeg 用 `select(0)` 轮询 stdin，空闲时根本不 read。

## 排查决策树（先看 `%CPU`，0% 且无输出时）

1. `cat /proc/<pid>/stat`（取 `) ` 之后的第 1 个字段）看状态字符：
   - **`T`** → SIGTTIN/SIGTSTP 被停 → 补 `-nostdin`
   - **`S`** → 再连采两次 CPU ticks（stat 第 14+15 字段）：
     - **冻结不增长** → 输出管道反压 → 把 stdout/stderr 接 DEVNULL 或读空
     - 持续增长 → 只是慢，不是挂起
2. 该挂起**用 Python `subprocess.run(..., timeout=N)` 兜不住**（历史实测超时后
   `kill()` 再 `communicate()` 仍不返回），只能外部 `timeout -s KILL` 强杀（rc=137）。

## 已修案例

- `VidUtils/vidcrop_hwaccel.py` 三个硬件探测辅助函数漏了 `-nostdin`，
  默认 `--hwaccel auto` 永久卡在"正在检测硬件加速能力…"；修完 >600s → 4.6s。
- 2026-09-14 给 `/workspace/install_ffmpeg_gpu_scale.sh` 第 6 步全部 5 处 ffmpeg 转码调用补上 `-nostdin`。

## 其他

- 诊断时不要依赖管道输出——进程被 SIGKILL 时内容会丢，**把结果写进文件再 cat**。
