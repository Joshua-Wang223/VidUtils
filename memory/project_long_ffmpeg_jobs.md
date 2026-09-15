---
name: 长时 ffmpeg 任务必须脱离会话跑，且不要直接写 MP4
description: codebuddy(node) 崩溃/退出会带走同进程组的 ffmpeg 子进程；直接写 MP4 会因缺 moov 整份作废——长任务要 setsid 分离 + TS/分片输出
type: project
---

## 已发生的事故（2026-09-14）

07:10 启动的 4K 2x nvinterpolate 编码（源 Earth.at.Night.in.Color.S02E01.mp4，
`nvinterpolate=fps=48000/1001,trim=start_frame=3,setpts=PTS-STARTPTS` + `-fps_mode passthrough`
+ `hevc_nvenc -preset p5 -cq 25`），07:44 中断，跑了 34 分钟。

产物 `/workspace/input_videos/Earth.at.Night.in.Color.S02E01_2x.mp4` 大小 2.1GB，
box 结构只有 `ftyp` + `free` + `mdat`(size=0 延伸到 EOF)，**没有 moov**
→ `ffprobe` 报 `moov atom not found`，2.1GB 里 0 字节可用，没有可续传的点。

**Why:** `core.171559` 显示崩的是 **`node .../codebuddy -y` 自己**，不是 ffmpeg。
ffmpeg 是会话的子进程，codebuddy 一死就被一起带走；而 MP4 的 `moov` 是编码结束时一次性写的，
进程非正常退出就永远没有。`-movflags +faststart` 也救不了（它同样等编码跑完）。

**How to apply:** 本机任何预计超过几分钟的 ffmpeg 任务：

- 用 `setsid nohup ffmpeg ... &`（或 `setsid bash -c '...'`）**脱离会话**，别让它留在会话的进程组里
  ——这同时规避了 SIGTTIN 挂起和"随会话一起被杀"。
- 输出走 **TS 或 `-f segment`**（TS 无全局索引，被杀前已写入的部分仍可解码），
  最后再 `-f concat -c copy` 汇成 MP4。分片用 `-g` 控制 GOP 让切点可预测
  （`-force_key_frames` 在 nvenc 上不生效，实测切点会落在默认 GOP 250 帧）。
- 音频不要分片：视频分片时加 `-an`，收尾从原片一次性 `-c copy`（音频本来没改）。
- **不要直接写 MP4**，哪怕是一次就能跑完的任务。

现成实现：`/workspace/VidUtils/interp_2x_safe.sh`（4K 2x nvinterpolate 用；`setsid` + TS 分片 +
已存在分片跳过 + 收尾 concat/`-c copy` 音轨）。2026-09-14 实测：分片接缝无重复帧
（PSNR 47–51dB，与单次跑一致）、重跑幂等、成片 hevc 48000/1001 + 原 5.1 AAC 正确。

回归测试：`/workspace/VidUtils/test_interp_2x_lock.sh`（60 项断言，约 43s，退出码 0/1/2；
`SUT=` 指向变异版可验证它确实能抓到锁失效）。用独立 mktemp 工作目录，
可以在正式任务跑着的时候执行。断言分两类：日志断言（看脚本打印了什么）与
**事实断言**（分片+meta 指纹 / 文件是否真从盘上消失 / 成片帧数）——后者不依赖脚本的
自我报告，能抓到"日志说做了、实际没做"。

脚本里那套防呆值得照搬（都是踩出来的）：
- **单实例锁**：`$WORKDIR/.lock` 上 `flock -n`，且**放在覆盖检查之前**（否则并发时
  第二个实例会先撞上"目标文件已存在"这种与并发无关的提示）。
- **两层复用校验**：`recipe.txt` 记 `slice|in|L|preset|cq|trim`（这些一变，每一片内容
  都不同 → 整体拒绝）；每片另有 `.meta` 记 `ss dt`（总时长只影响末尾那一片 → 只重编
  边界片，不整体拒绝）。
- **临时文件名带 PID** + 持锁启动时清理历史残留（`p*.ts.part.<pid>`）。
- **`--overwrite` 门控目标文件**，且尽早退出（别跑 50 分钟才在收尾发现）。

**并发是这类分片任务的隐藏杀手**：两个实例共用一个分片目录时，循环开头的
`rm -f $tmp` 会删掉对方正在写的临时文件（ffmpeg 仍往已 unlink 的 inode 写），
先跑完的把临时文件 `mv` 走、后跑完的就报
`mv: cannot stat '.../p00002.ts.part': No such file or directory` —— 两边都白跑。
所以分片脚本必须在工作目录上放 `flock` 单实例锁，且临时文件名带 PID。

踩到的两个点：
- `-t` 是**输出**选项时 ffmpeg 会自己补足时长，所以每片剪掉片头 3 帧后仍严格是 L 秒，
  且相邻片内容天然连续 —— **不要**再手工做 ±0.0625s 补偿（做了反而多算 3 帧）。
- 收尾拼接的临时文件名要保留 `.mp4` 后缀（`$OUT.part` 会让 ffmpeg 认不出容器，
  报 "Unable to choose an output format"）。

## 遗留

- 那份坏的 2.1GB 文件已于 2026-09-14 删除。
- 但 08:17 执行过 `aliyunpan upload Earth.at.Night.in.Color.S02E01_2x.mp4 /temp`，
  **云端 `/temp` 里那份同样是不可读的**，将来若要用需重新上传正确成品。
- 用户当时决定**不重跑**这次 2x 转码。
