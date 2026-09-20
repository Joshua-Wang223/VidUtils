---
name: 像素格式 / 位深 / HDR 三个新参数（--pix-fmt / --bit-depth / --hdr）的设计与实测边界
description: 2026-09-20 给 vidcrop_hwaccel.py 与 vidcrop_cpu_v2.py 新增三个参数；含零拷贝链不能传 -pix_fmt、scale_cuda=format= 与 -profile:v 的约束、tonemap_cuda 上游不存在、--pix-fmt 优先于 --bit-depth 的决策
type: project
---

## 三个参数（2026-09-20 落地）

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--pix-fmt` | `auto` / `none` / 具体 ffmpeg 名 | `auto` | hwaccel 此前**没有**这个参数，cpu_v2 有；现已补齐，取值与校验两脚本一致 |
| `--bit-depth` | `auto` / `8` / `10` / `12` | `auto` | 位深此前完全隐式（从源继承），现可显式指定 |
| `--hdr` | `auto` / `keep` / `drop` / `sdr[:<algo>]` | `auto` | `sdr` 真做 HDR→SDR tone mapping；algo 默认 `mobius` |

**硬要求**：三个默认都是 `auto`，不传时命令与改动前**逐字相同**（16/16 + 默认路径 5/5 + 命令级 7/7 三道门）。

## 关键约束与决策

- **零拷贝 CUDA 链（`-hwaccel_output_format cuda`）不能传 `-pix_fmt`**：`-pix_fmt` 设的是
  `AVFrame.format`（该链上是 `AV_PIX_FMT_CUDA`）而非 `sw_format`，传 `nv12`/`yuv420p`
  实测都报 `Impossible to convert`（`project_t4_gpu_capabilities.md`）。
  → 已按链型分别落地：零拷贝链写进 `scale_cuda=format=` 并自动配 `-profile:v`
  （`p010le`→`main10`、`yuv444p`→`high444p`）；软件帧链才下发 `-pix_fmt`。
- **`scale_cuda=format=` 只支持 `nv12 / yuv420p / yuv444p / p010le`**；请求其它格式按
  `--fallback-policy` 处理（auto=提示并保持，strict=报错退出 2）。
- **改了 `scale_cuda` 的 format 后，紧随的 `hwdownload,format=` 必须同步改**——那个值是按
  源位深烘进滤镜串的，不改会出现「缩放输出 p010le、却要按 nv12 下载」的错配。
- **`--pix-fmt` 优先于 `--bit-depth`**（用户决策）：两者语义重叠（格式名已含位深），
  显式给了 `--pix-fmt` 时 `--bit-depth` 被忽略并提示。实现上 `--bit-depth` 只在
  `--pix-fmt` 保持 auto 时才参与解析。
- **`tonemap_cuda` 上游不存在**（实测 `Unknown filter`，与 `crop_cuda` 同款）→ CUDA 侧没有
  硬件 tone mapping。`--hdr sdr` 走 CPU 的 `zscale + tonemap + zscale`；CUDA cover 链本来
  就在 crop 前 `hwdownload` 成软件帧，接链尾即可（且应保留 p010le 下载以免提前丢精度）。
- 能力探测是**惰性**的：`--pix-fmt` 显式时才跑 `ffmpeg -pix_fmts`，`--hdr sdr` 时才探测
  `tonemap` / `zscale`；探测失败一律放行或按策略降级，不阻塞任务。

## Why

原来的位深完全隐式、HDR 只透传元数据（libx265 写 `-x265-params`，NVENC 直接告警丢弃），
用户要 10bit→8bit、或要把 HLG 片源转成 SDR 时只能靠 `--extra-args` 手搓滤镜串，
而手搓又正好会踩到「零拷贝链不能传 `-pix_fmt`」这条不显然的约束。

## How to apply

- 再动这三个参数：先跑三道回归门，再改；尤其 `--pix-fmt` 的新格式支持要确认
  `scale_cuda` 是否认。
- 报结论时区分**链型**（软件帧链 / 零拷贝 CUDA 链），别笼统说"改了 pix_fmt"。
- ⚠ **`--hdr sdr` 的 tone mapping 是全新能力，还没有 T4 实测数据**（滤镜配方、desat=0、
  算法选择都待验）。用于生产前必须在真实 HDR 片源（如 HLG 的 `new4_raw.mp4`）上验。
- 相关：`project_t4_gpu_capabilities.md`（零拷贝 `-pix_fmt` 实测表 + `-profile:v` 要求）、
  `project_three_axis_model.md`（--decode/--scale-algo/--codec 三轴，与这三个新轴正交）、
  `project_preset_equivalence.md`（两脚本逐字对齐约定）。
