---
name: bench_sdr_to_hdr 的实测约束与陷阱
description: 给 convert_sdr_to_hdr 做 benchmark 时实测到的执行路径/质量轴/环境约束，含多类「名义不同实则同路径」与「自造公式被推翻」的陷阱
type: project
---

`Accessory/probe/bench_sdr_to_hdr.py`（2026-10-09 新增）的实测约束。同源产物：
`Accessory/probe/t4_acceptance.py` 等。

**Why:** 这些不是读代码能得出的结论，而是「读代码会得到错误答案」的点——
同一组合在参数上不同，实际执行路径可能完全相同，基准会据此得出假的加速比；
或自造的估算公式与被测实现不同构，却因"看起来合理"而印进报告。

**How to apply:** 改 benchmark 或扩展组合矩阵时先按这几条剪枝，别把名义组合当实测差异。

## 1. 脚本自报的 fps 不可信，必须自己测墙钟

`print_summary` 的「均速」用 `j.info["nb_frames"]`（**源探测帧数**）而非实际输出帧数。
用 `--frames 10` 截断时实测打印「均速 7fps、峰值 4fps」—— 均速高于峰值，逻辑上不可能；
真实值 2.9fps（10 帧 / 3.5s）。`j.speed` 全仓库无人赋值、恒为 `-`，别解析它。
⇒ benchmark 只信自己 `time.monotonic()` 测的墙钟 + 自己 `-count_frames` 数帧。

## 2. 三条执行路径的 `job.elapsed` 口径不一致，不可横向比

- `run_job`（整条）：t0 在两个 ffmpeg `Popen` **之后**，**不含**模型加载
- `run_job_segmented` / `run_job_multiproc`：t0 在**建模型/建池之前**，**含**模型加载
⇒ 模型加载开销只能靠 `wall - elapsed` 近似。

## 3. 「参数不同」≠「路径不同」——四类静默退化

- **分段被时长阈值吃掉**：`_SEGMENT_MIN_SECONDS = 20.0`。8 秒素材 + `--workers 4`
  依然返回 `'off'`。要真触发分段必须用 **≥20 秒**的素材。
- **`--frames`/`--duration` 与分段互斥**：给了就一律降级 `'off'`（被测脚本 :4079）。
- **`--split-mode segment` + `--no-model` 是合法的**。
- **CUDA 推理下 `split-mode workers` 被降级**为 `'off'`。
⇒ 必须从 stdout 抓 `分段并行：N 段`（:4185）/ `单解码 + N 推理进程`（:4295）
**确认组合真生效**。⚠ `--dry-run` **抓不到**分段结论（dry-run 分支在
`decide_single_file_mode` 之前就 return 了），只能抓真跑的输出。

## 4.「同质量」必须用 `--crf-ref` 锚定

ref=21 处**没有任何编码器是恒等映射**（实测 `from_x264_crf`）：
libx265→20.74、hevc_nvenc→25.50、libaom-av1→26.37、libsvtav1→28.96、librav1e→64.38。
只有 libx264 恰好 21.00，而那是**回退到 SIZE_MAP 的副产品**。
⚠ 不在 QUALITY_MAP 里的编码器会**静默按等体积口径**换算、不报错 ⇒ 必须前置断言。
⚠ 同一 group 内每格都要带**同一个** `--crf-ref`（漏了的那格用默认 cq26，
  同组落在不同质量上，读者会把质量差当成调优开销）。

## 5. 本机环境（2026-10-09 实测）

- torch 2.9.1 **CPU-only**；numpy 2.3.5；**无 basicsr**
- 模型仓库 `/mnt/d/Workspace_Python/HDRTVNet-plus` **完整可用**
  （codes + Ensemble_AGCM_LE.pth 2.4MB）—— ⚠ 别被 `ls | head -5` 截断误导成空仓库
- CPU 推理：1080p 约 **24s/帧**；4K 整帧 237.7s / tile1024 89s
- 8 核 / 内存 7.7GB 可用约 4GB（**内存是硬约束**，单文件并发上限约 2）
- 滤镜：有 libvmaf / tonemap / signalstats；**无 zscale**

## 6. 真实素材

- `Accessory/archive/m2_srcs/` —— ⚠ **文件名会骗人**：`cc_anim_300s.mkv` 实为 8.08s、
  `earth_dark_80s.mp4` 实为 6.15s。**仓库内没有 ≥20s 的素材** ⇒ 分段轴永远 SKIP。
- `../input_videos/The.Creature.Cases.S01E01...mkv` —— **33 条 subrip 字幕轨**，
  1080p/24fps/27:45，适合测多轨索引。
  ⚠ **字幕从 31.3s 才开始有事件**（实测全片扫描得到）—— 截取没有字幕的区间会让
  burn 报「导出得到 0 字节文件」。
- `../input_videos/Earth.at.Night...2160p_T100_2xfps.mp4` —— 4K/48fps/100s，适合测 tile。
- `../input_videos/test3.mp4` —— 1280x720/10s/**302 帧**、音轨短 0.06s（真实丢帧样本）。
  ⚠ 帧数是 **302 不是 300**，配 `--frames` 时别超。

## 7. libvmaf 的两个硬约束

- **PQ/BT.2020 不能直接喂 libvmaf**：不报错，但 crf 20~32 区间曲线压平
  （实测 99.85/99.82/98.21，跨度仅 1.6 分）⇒ 判据分辨率不足。
  必须先 `tonemap=tonemap=hable:desat=0:peak=100,format=yuv420p` 落 8bit。
- **`n_subsample` 必须 = 1**：实测 1→91.0、8→94.3，虚高 3.3 分。代价是慢
  ⇒ 耗时基准与 VMAF 门禁**分开跑**。

## 8~9. 三个判据陷阱（都实测踩过）

- **`--dry-run` 复用已存在的输出路径 ⇒ 一条命令都不打印**（被测脚本 `collect_jobs`
  标 skipped）⇒ 抓质量值返回 None。dry-run 必须用**从未存在过**的路径并先 unlink。
- **用「文本里有没有『跳过』」判跳过 ⇒ 4/4 组合假红**。正常输出里就有
  「模型仓库: 跳过检查（--no-model）」。可靠判据是**汇总行 + 「✔ 完成」行**。
- **`--container .mkv` 会改产物扩展名 ⇒ 按 cid 猜路径把成功判成 FAIL**。
  权威来源是 stdout 的「输出文件」行。跑前清理要连带删同名其它扩展名。

## 10. 产物路径/数值呈现的三处修正

- mkv **不带 `nb_frames`** ⇒ fps 列静默变空，要回退到源帧数并记 `frames_from`。
- 体积列必须给**字节级 Δ**（`1.7 MB (Δ+48,126B)`）—— 只印「1.7 MB」会让读者
  反过来怀疑「音轨没生效、这轴是假绿」。
- 倍数必须配 `基准 = <组合名>`；首格 FAIL 时提示「基准顺延」；整组无 OK 时说明无基准。
- 帧数守恒判据用**绝对量**（丢 >2 帧即 FAIL），**别用百分比** —— 百分比会让阈值
  随素材长度漂移（同逻辑在 10 万帧素材上是「丢 1300 帧也算舍入」）。

## 11. ⚠ 自造的估算公式会与被测实现不同构（最危险的一类）

我写过「分块推理处理量估算表」，用 `total = 块数 × tile²`（假设每块满 tile），
算出 waste 1.4x/1.9x/3.0x 并**当成「实测」印进 docstring、报告和 memory**。
对抗审查用「桩掉真实推理、统计实际喂给模型的张量尺寸」证明：被测实现的边缘块
只补到 **8 的倍数**（`ceil(ph/8)*8`）⇒ 4K 下三档真实总处理量**都是 10.51 Mpx / 1.27x**。
**连结论方向都反了**（我暗示"tile 越大越浪费"，真实是三档几乎相同）。

⇒ 写完估算函数必须**逐行对齐被测实现**（本次核对了边界裁剪、补齐取整、步长三处），
并用**桩掉真实实现 + 统计实际输入**来验；自检要用**手算的独立值**钉住
（本次手算 `2416×4352`，我第一版算成 `2288×4096` 漏了末列，**是期望值错不是代码错**）。
⇒ 修完公式必须**同步重写依赖它的文字结论**，否则会留下方向相反的结论。

## 12. 铺矩阵必须加能力门槛

`--codec hevc_nvenc` / `--decode cuda` / `--audio copy` / `--container .webm`
在特定环境下会退化成「命令逐字相同」。当耗时轴测会产出"几组一模一样"的结果，
而那看起来像一个很确定的结论。
⇒ 能力要**实测**（硬编要**真编一帧**，`ffmpeg -encoders` 列得出不算），
不满足就 **SKIP 并写明具体原因**；SKIP ≠ 通过。

## 13. 环境过载会伪装成代码 bug

benchmark 与审查 agent 并行跑时，`--selftest` 里的 dry-run 抓取**超时返回 None**，
报告出现「质量值 —」，看着像刚改的代码坏了。真因是对方的 4K 实测让 ffmpeg 占到 363% CPU。
⇒ 数字异常时**先 `ps aux --sort=-%cpu | head` 看有没有别人在抢**。

## 14. 长时间运行的 gate/verify

- `verify_equal_quality.py` 要跑 **约 15 分钟**（真实素材重编码），
  几百秒的超时会把它误读成「断言失败」（实际退出 124、0 项红）。
- `verify_sdr_to_hdr.py` 八组约 1 分钟。
- ⚠ 测试用 `--frames N` **不得超过素材实际帧数**，否则「输出不完整：期望 N 帧」
  是**测试参数问题**而非产品 bug（本会话因此白查两轮）。
