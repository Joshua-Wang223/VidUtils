---
name: run_job 双进程管道收尾死锁与 libx265 模型模式修复
description: convert_sdr_to_hdr.py run_job 帧循环提前退出未排空解码器 stdout 导致 dec.wait() 死锁（症状 GPU0% 显存恒定）；含 libx265 模型模式 exit183 四项修复
type: project
---

`convert_sdr_to_hdr.py` `run_job()`（约 :4408）用「解码 ffmpeg stdout → Python 推理 →
编码 ffmpeg stdin」双进程管道。两处必知：

## 1. 收尾死锁：帧循环提前退出 ⇒ 解码器 stdout 反压（2026-10-13 修）

**症状**：进度 100%「收尾中...」后无输出；`nvidia-smi` **GPU 0% 但显存恒定 ~11.5G 不降**；
进程活着不退。MP4/MKV 均复现，ESC 中断后无产物。曾被误归因为「av1_nvenc 硬件 flush 卡死」。

**真实根因**：`total` 取 ffprobe `nb_frames`（test2.mp4 自报 2194，实际输出 2191）。
帧循环在 `n_done >= total` 或读满后**先于解码器 EOF 退出**，主进程停止读 `dec.stdout` ⇒
64KiB 管道写满 ⇒ **解码器阻塞在 write()** ⇒ `dec.wait()` 永久挂起。编码器已无新帧但
CUDA context 未释放，即「GPU 0% + 显存恒定」组合签名。

**归因方法**（收紧结论用）：换编码器对照。libx265/hevc_nvenc/软编 AV1 全正常 ⇒ 排除
AV1 编码器本身；真正差异是**各编码器吞吐节奏使帧循环更易在解码器 EOF 前触达 total**。

**修复**（`run_job` 收尾段，约 :4503-4533）：
1. 关 `enc.stdin` 后**先排空 `dec.stdout` 到 EOF**（`while dec.stdout.read(1<<16): pass`）
   —— 这是**根治**，让解码器写完退出
2. 防御性兜底：`enc.wait(timeout=120)` / `dec.wait(timeout=10)`，超时走 `_terminate`

**复验**：L40 av1_nvenc MP4×2 + MKV×1 均 1m46s/21fps 干净退出（未触发超时兜底）。

## 2. libx265 模型模式 exit 183 组合拳（2026-10-13 修）

`--model ensemble --codec libx265` 报 exit 183 (AVERROR_INVALIDDATA)，四项叠加根因：

| 根因 | 修复 |
|------|------|
| `-threads 48` 使 libx265 初始化超过首帧到达时间（>16 线程即现） | `build_encode_cmd` 对 libx265 **线程上限 16** |
| `setparams` 滤镜与单独 `-colorspace/-color_primaries` 等标志**冲突** | `is_hdr_input=True` 时**不下发单独色彩四参** |
| 模型输出已是 PQ/BT.2020，滤镜 `scale=out_color_matrix=bt2020nc` **重复转换** | `is_hdr_input=True` 时仅 `setparams` 写元数据，**移除 scale** |
| 解码侧 `-t` 时长限制导致定位偏移、异常首帧 | 仅用 `-frames:v` 精确截帧（`-t` 与 `-frames:v` 互斥） |

**相关**：`is_hdr_input` 参数贯穿 `build_encode_cmd`/`build_plan`，判据 = `not args.no_model`。

## 教训

- 「GPU 0% + 显存恒定」不必然是编码器卡死：先看**解码器**是否 stdout 反压（`dec.wait()` 挂）
- ffprobe `nb_frames` 不可靠（2194 vs 实际 2191），帧循环 `total` 判据要容差；更稳的是**读到解码器 EOF 为准**，排空 stdout 是收尾必需
- 两个 ffmpeg 子进程的 `wait()` **必须带超时**，托管环境里无超时等待 = 永久挂起风险
