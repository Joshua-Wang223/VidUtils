---
name: cover 模式的 CUDA 缩放（scale_cuda）——实测数据、两条硬约束、以及一个被激活的老 bug
description: 2026-09-20 给 vidcrop_hwaccel.py 的 cover 模式加了「CUDA 缩放 + CPU 裁剪」策略；实测 4K→1440x1080 快 44.5%（CPU 侧 scale 占 17.2%）；必须显式写 hwdownload 否则 crop 被静默丢弃；crop_cuda 上游不存在；顺带修掉 _src_download_fmt 的 p010/p012 非法 pix_fmt 名
type: project
---

## 做了什么

`vidcrop_hwaccel.py` 的 `cover` 模式新增一条策略（现为策略链第 2 条）：

```
NVDEC → scale_cuda=W:H:interp_algo=lanczos → 显式 hwdownload,format=… → CPU crop → NVENC
```

只动 `vidcrop_hwaccel.py`。**没有新增任何 CLI 参数**，所以两脚本的对齐矩阵不受影响；
`vidcrop_cpu_v2.py` 一字未动，仍是 CPU 侧 `scale,crop`。

## 实测数据（2026-09-20，T4 + 自建 FFmpeg 7.1，`-f null -` 去 IO）

素材：真实 4K（`Earth.at.Night…S02E01_2x.mp4`，3840x2160），目标 1440x1080 覆盖链，min of 3：

| 变体 | 链 | 时间 |
|---|---|---|
| B1 现行 | `-hwaccel auto` + `scale=-2:1080,crop=…` | **23.11s / 23.35s**（两轮） |
| B2 去缩放 | `-hwaccel auto` + `crop=1440:1080:0:0` | 19.08s / 19.34s |
| B3 新链 | `-hwaccel cuda -hwaccel_output_format cuda` + `scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=nv12,crop=…` | **12.83s / 12.82s** |

- CPU 侧 `scale` 单独成本 **4.01~4.03s = 现行的 17.2~17.4%**（两轮高度一致）
- 新链净收益 **10.29~10.53s = 44.5~45.1%**
- 判据 B4：`scale_cuda=-2:1080` **被接受**（与 `scale` 共用 `ff_scale_eval_dimensions`）

**两处读数的坑（别误读）：**

1. **B2 不是干净的 floor**：B3（GPU 缩放+下载+裁剪，12.83s）比 B2（只有裁剪、不缩放，19.08s）
   **还快 6.2s** → 成本大头是**搬运 4K 帧的内存带宽**，在显存里尽早降采样就省掉了。
   所以该看的是 **B1 vs B3 = 快 44.5%**，而不是 17%。
2. **收益是内容相关的**：用 lavfi `testsrc2` 自造 4K 素材那轮，CPU scale 成本只有 **0.2%**、
   改造收益 **9.8%**（不过 10% 阈值）。合成内容解码极快、管线被别的环节吃掉，测不出差异。
   → 这个优化只在**真实高码率 4K** 上兑现。

## 两条硬约束（都有实测编号，改动时别踩）

### 1. 必须**显式**写 `hwdownload,format=…`，不能靠 FFmpeg 自动插入

同一轮判据 A（源 1920x1080，`scale_cuda=1280:720`，探针用 `crop=iw/2:ih/2`，
输出尺寸直接反映 crop 看到的是什么）：

| 链 | 输出 | 含义 |
|---|---|---|
| `scale_cuda=1280:720,hwdownload,format=nv12,crop=iw/2:ih/2` | **640x360** | crop 看到 1280x720 ✓ |
| `scale_cuda=1280:720,crop=iw/2:ih/2`（靠自动插入） | **1280x720** | **crop 被静默丢弃** ✗ |

A2 那格是本次最值钱的发现：尺寸 1280x720 本身**合法**、ffmpeg **不报任何错**，
但裁剪根本没发生、画面与预期不同。这与 `_prepend_hwdownload` 注释里记的老前科
（768x576 源 `crop=768:432` 后仍输出 768x576）是同一类。

### 2. `crop_cuda` 在 FFmpeg 上游**并不存在**（更正旧说法）

实测 `ffmpeg -filters` 里没有、`ffmpeg -h filter=crop_cuda` → `Unknown filter 'crop_cuda'`
（本机 master gpl-shared 与 T4 自建 7.1 都如此）。旧记忆/README 写的"FFmpeg 6.1 未编译该滤镜"
不准确——**不是编译选项问题，是上游没有这个滤镜**。所以：

- `vidcrop_hwaccel.py` 的**策略 1（硬解 + crop_cuda + NVENC）在真实环境永远跳过**，
  crop 模式今天走的其实是策略 2。读代码会以为"策略 1 是最高优先级的全 GPU 路径"，实际没跑过。
- **GPU 永远只能接管 cover 的 `scale`，`crop` 只能留在 CPU** → 链路必然
  `scale_cuda → hwdownload → crop`（一次下载，与现状次数相同）。
- 因此 `crop-cover` **不纳入** CUDA 缩放：它必须先裁剪，GPU 缩放要额外 `hwupload` 一次（未实测）。

## 顺带修掉的一个老 bug（此前不可达，被本次改动激活）

`_src_download_fmt()` 原来返回 `p010` / `p012`，但 ffmpeg 的 pix_fmt 表里**只有 `p010le` / `p012le`**
（实测 `ffmpeg -pix_fmts` 里查不到 `p010`/`p012`）。此前它只被 `_prepend_hwdownload` 调用，
而那条路只有 `hof='cuda'` 才走——**只有死掉的策略 1 会给 `hof='cuda'`**，所以一直没暴露。
新增的 CUDA 缩放策略同样给 `hof='cuda'`，10bit 源一进来就会拿 `hwdownload,format=p010` 去协商 → 必然失败。
已改成 `p010le` / `p012le`。

**教训**：新功能接上一条"以前没人走"的代码路径时，要先假设那条路径是**没被测过**的。

## 还没做 / 还没过

- **质量门未过**：GPU `lanczos` 与 libswscale 的缩放在数值上不逐像素相同，44.5% 目前只是
  "尺寸对、计时快"。探针脚本 `temp/probe_scale_cuda_crop.sh` 已加判据 Q（ref=lanczos 参考 /
  cur=现行 bicubic / gpu=新链，各出一份 ffv1 无损后比 PSNR，`PROBE_VMAF=1` 可加 VMAF），
  但**尚未在 T4 上跑**。
- 10bit + `h264_nvenc`（8bit-only 编码器）走新链时，`-pix_fmt yuv420p` 仍由 `build_ffmpeg_cmd`
  追加——因为链里已显式 `hwdownload` 成软件帧，这一步是合法的降位深（与旧行为一致），但**未实测**。
- 新链的下载格式烘在滤镜串里，`process_file` 那套 `hw_download_fmt` 重试够不到它；
  p010 下载失败就是整条策略失败、降级到 CPU 缩放（结果仍正确）。

## Why

用户会**按"值不值得动手"来要数字**：他先要"高度概括的可行性"，再直接让写探针脚本上机实测，
不接受"读代码推出应该能省 CPU"。所以这类改动必须先把**判据与阈值事先钉死**
（本次：CPU scale 占比与改造收益**都 ≥10% 才改**），数据一到就是机械结论。
本次也正是靠实测才发现 A2 那个**静默**失败——只看代码或只看"跑通了"都会漏掉。

## How to apply

- 谈论 cover 的 GPU 化时直接引用上面的 B 表，别重新推导；也**别说"策略 1 会接管"**——它不会。
- 改这条链时守住两条：**显式 `hwdownload,format=`**（否则画面静默错）、**显式 `interp_algo`**
  （`scale_cuda` 的默认值是 0，未映射到具名档，而 CPU 侧 `scale` 默认是 bicubic）。
- 判据 A 的探针技巧可复用：想验证某个滤镜"看到的 `iw/ih` 是多少"，用 `crop=iw/2:ih/2`
  让输出尺寸自己报出来，比 PSNR 对照便宜且无歧义。
- `scale_cuda` 没有 `in_range`/`out_range` → CUDA 链里 `--color-range tv|pc` 的**值域转换做不了**
  （`build_range_convert_filter` 会返回 None 并告警，只改标签）。这是结构性限制，不是 bug。
- 相关：`project_t4_gpu_capabilities.md`（零拷贝链里不能传 `-pix_fmt`）、
  `project_preset_equivalence.md`（两脚本一致性约定——本次没动 CLI，故不受影响）。
