# VidUtils · 视频实用工具集

> 一组基于 **FFmpeg** 的命令行视频处理工具，聚焦 *批量、可复现、生产可用* 的视频工程任务。
> 当前提供居中裁剪（CPU 顺序版 / CPU 并发版 / 硬件加速版三个变体），后续将逐步扩展缩放、修复、增强等能力。

---

## 目录

- [项目简介](#项目简介)
- [功能矩阵](#功能矩阵)
- [环境要求](#环境要求)
- [安装](#安装)
- [工具一览](#工具一览)
  - [vidcrop_cpu_v0.py — CPU 顺序裁剪](#vidcrop_cpu_v0py--cpu-顺序裁剪)
  - [vidcrop_cpu_v1.py — CPU 并发裁剪](#vidcrop_cpu_v1py--cpu-并发裁剪)
  - [vidcrop_hwaccel.py — 硬件加速裁剪](#vidcrop_hwaccelpy--硬件加速裁剪)
- [快速上手](#快速上手)
- [常见场景配方](#常见场景配方)
- [硬件加速说明](#硬件加速说明)
- [质量参数指南](#质量参数指南)
- [路线图（Roadmap）](#路线图roadmap)
- [目录结构](#目录结构)
- [常见问题（FAQ）](#常见问题faq)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 项目简介

**VidUtils** 是一个面向工程场景的视频处理工具集合，设计原则是：

- **薄封装，重工程**：不重造轮子，所有重活交给 FFmpeg；脚本只负责参数编排、容错、批量调度与可观测性。
- **批量友好**：统一支持"单文件 / 整目录"两种输入形态，同一条命令即可处理上百份素材。
- **可复现**：打印出完整的 FFmpeg 调用命令，便于审计、回放与手工调优。
- **智能但不黑盒**：自动推断容器、编码器、preset 与加速策略，但每一步都会显式提示，用户随时可以接管。
- **生产可观测**：实时进度条、耗时统计、输入输出体积对比、失败时打印 FFmpeg stderr 末 N 行。

---

## 功能矩阵

| 能力 | `vidcrop_cpu_v0.py` | `vidcrop_cpu_v1.py` | `vidcrop_hwaccel.py` |
|---|---|---|---|
| 居中裁剪（crop 模式） | ✅ | ✅ | ✅ |
| 等比缩放+居中裁剪（cover 模式） | ✅ | ✅ | ✅¹ |
| 单文件 / 目录批量 | ✅ | ✅ | ✅ |
| 递归扫描目录（`-r`） | ✅ | ✅ | ✅ |
| CPU 软件编码器（libx264/265、VP9、AV1、ProRes、MJPEG…） | ✅ | ✅ | ✅ |
| NVIDIA NVENC（h264 / hevc） | ❌ | ❌ | ✅ |
| AMD AMF（h264 / hevc） | ❌ | ❌ | ✅ |
| Intel QSV（h264 / hevc） | ❌ | ❌ | ✅（preset 映射） |
| 硬件解码（CUDA / Vulkan / VA-API / OpenCL） | ❌ | ❌ | ✅ |
| `crop_cuda` 全 GPU 流水线 | ❌ | ❌ | ✅² |
| 运行时硬件能力探测 | — | — | ✅ |
| 策略自动降级 | — | — | ✅ |
| 多任务并行处理 | ❌ | ✅ | ❌ |
| CPU / 内存自动探测与并发决策 | ❌ | ✅ | ❌ |
| 音频重编码（aac / libopus…） | ❌ | ✅ | ✅ |
| 同尺寸跳过优化 | ❌ | ✅ | ✅ |
| Dry-run 命令预览 | ✅ | ✅ | ✅ |
| 日志文件记录（`--log`） | ✅ | ✅ | ✅ |
| `--extra-args` 自定义 FFmpeg 参数 | ✅ | ✅ | ✅ |
| 实时进度条（%/帧/fps/ETA） | ✅ | ✅（并发时聚合面板） | ✅ |
| 文件级耗时与体积对比 | ✅ | ✅ | ✅ |
| 编码器别名归一化 | ❌ | ❌ | ✅ |
| preset 双向映射（NVENC ↔ x264） | ❌ | ❌ | ✅ |
| CQ → CRF 等效质量映射（降级场景） | ❌ | ❌ | ✅ |

> ¹ cover 模式在 hwaccel 版中始终使用 CPU 侧 `scale,crop` 滤镜完成缩放与裁剪，GPU 仅负责解码与编码（若可用）。  
> ² `crop_cuda` 全 GPU 流水线（策略 1）**仅在 crop 模式下可用**；cover 模式自动跳过策略 1，降级至策略 2 或以下。

> **如何选择？**
> - 有多核 CPU、无 GPU、批量大、需要并行提速 → **v1**
> - 需要 cover 模式且同时希望 GPU 加速解码 / 编码 → **hwaccel**（cover 模式下滤镜走 CPU，解编码仍可走 GPU）
> - 有 NVIDIA / AMD / Intel GPU、追求最高吞吐量（尤其是 crop 模式全流水线）→ **hwaccel**
> - 纯 CPU 环境、或需要细粒度进度条 + 并发控制 → **v0 / v1**

---

## 环境要求

- **Python** ≥ 3.8（仅使用标准库，无需 `pip install`）
- **FFmpeg** ≥ 4.4，且 `ffmpeg` / `ffprobe` 在 `PATH` 中可见（或通过 `--ffmpeg-bin` 指定，仅 hwaccel 版支持）
- **硬件加速版本额外需要**（可选，按需启用）：
  - NVIDIA CUDA：较新驱动 + 支持 NVENC 的显卡（Turing / Ampere / Ada 等）
  - AMD AMF：支持 AMF 的 Radeon 显卡与驱动
  - Intel QSV：集显或 Arc 系列 + 对应驱动
  - Vulkan：支持 Vulkan 1.1+ 的 GPU 与驱动
  - VA-API：Linux 下 Intel / AMD GPU 驱动
  - OpenCL：可用的 OpenCL 1.2+ 运行时

**检查 FFmpeg 是否具备硬件编译选项：**

```bash
ffmpeg -hide_banner -encoders | grep -E 'nvenc|amf|qsv|vaapi|videotoolbox'
ffmpeg -hide_banner -hwaccels
```

---

## 安装

VidUtils 以独立脚本分发，无需打包安装：

```bash
git clone https://github.com/<your-org>/vidutils.git
cd vidutils

# 可选：赋予执行权限（类 Unix 系统）
chmod +x vidcrop_cpu_v0.py vidcrop_cpu_v1.py vidcrop_hwaccel.py

# 快速自检
python vidcrop_cpu_v0.py --help
python vidcrop_cpu_v1.py --help
python vidcrop_hwaccel.py --help
```

---

## 工具一览

### `vidcrop_cpu_v0.py` — CPU 顺序裁剪

单进程顺序执行版，零硬件依赖，跨平台行为完全一致。每次处理一个文件并显示细粒度实时进度条。

**支持的编码器：** `libx264`, `libx265`, `libvpx`, `libvpx-vp9`, `libaom-av1`, `librav1e`, `prores`, `prores_ks`, `mpeg4`, `libxvid`, `mjpeg`, `copy`

**主要参数：**

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--input` | 必选 | 输入视频文件或目录 |
| `--output` | 必选 | 输出文件或目录 |
| `--output-width` | 必选 | 目标视频宽度 |
| `--output-height` | 必选 | 目标视频高度 |
| `--mode` | `crop` | `crop`=直接居中裁剪；`cover`=等比缩放至完全覆盖后居中裁剪 |
| `--original-width/height` | 自动检测 | 手动指定源视频尺寸，可跳过 ffprobe 探测 |
| `--codec` | `libx264` | 视频编码器 |
| `--crf` | FFmpeg 内置默认 | CRF 质量值（0–63，越小质量越高） |
| `--preset` | `slow` | 编码器预设，仅 libx264/libx265 生效 |
| `--pix-fmt` | `auto` | 像素格式；ProRes 自动设为 `yuv422p10le`，其余多数为 `yuv420p` |
| `--container` | 按编码器推断 | 手动指定容器扩展名，如 `.mp4` / `.mkv` |
| `--overwrite` | 否 | 覆盖已存在的输出文件 |
| `-r, --recursive` | 否 | 递归扫描输入目录 |
| `--dry-run` | 否 | 仅生成并显示 FFmpeg 命令，不执行转码 |
| `--log LOG_FILE` | 无 | 将所有输出同时记录到日志文件 |
| `--extra-args` | 无 | 追加到 FFmpeg 命令末尾的自定义参数（必须放在命令最后） |

适用场景：服务器批处理、CI 流水线、无 GPU 环境、需要 dry-run 验证、需要日志归档的任务。

---

### `vidcrop_cpu_v1.py` — CPU 并发裁剪

多任务并发版，在 v0 基础上增加了并行处理、智能资源分配和音频重编码能力。

**支持的编码器：** 同 v0（`libx264`, `libx265`, `libvpx`, `libvpx-vp9`, `libaom-av1`, `librav1e`, `prores`, `prores_ks`, `mpeg4`, `libxvid`, `mjpeg`, `copy`）

**在 v0 基础上新增的参数：**

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--workers` | `0`（自动） | 并行任务数；0 表示根据 CPU 核数与可用内存自动决定 |
| `--threads` | `0`（自动） | 每个 FFmpeg 任务的线程数；0 表示按编码器画像自动决定 |
| `--mem-per-job` | `0`（按编码器画像） | 单任务估计内存占用（GB），用于并发上限计算 |
| `--sequential` | 否 | 强制顺序执行，退化为 v0 行为（显示单文件细粒度进度条） |
| `--audio-codec` | `copy` | 音频编码器；如需重编码可指定 `aac` / `libopus` 等 |
| `--audio-bitrate` | `128k` | 音频重编码码率（仅在 `--audio-codec` 非 `copy` 时生效） |
| `--no-skip-same-size` | 否 | 即使源尺寸等于目标尺寸也强制转码（默认会跳过） |

**并发决策说明：**

v1 启动时自动探测 CPU 核数（支持 cgroup v1/v2 限制感知）和可用内存，结合编码器画像（推荐线程数、单任务内存占用）计算最大并行任务数。并发时显示聚合进度面板；单文件或顺序模式下显示细粒度实时进度条。

**编码器画像（推荐线程数 × 内存估算）：**

| 编码器 | 推荐线程数 | 单任务内存估算 |
|---|---|---|
| libx264 / libx265 | 4 | 0.8 / 1.2 GB |
| libvpx-vp9 / libaom-av1 | 4 | 1.0 / 1.5 GB |
| librav1e | 4 | 1.2 GB |
| mpeg4 / libxvid / mjpeg | 2 | 0.4 / 0.4 / 0.3 GB |
| copy | 1 | 0.1 GB |

适用场景：本地多核工作站、需要并行批量处理大量素材、或需要音频重编码的任务。

---

### `vidcrop_hwaccel.py` — 硬件加速裁剪

硬件加速版，运行时探测 GPU 能力，按优先级依次尝试策略链，前一级失败自动降级。功能与 CPU 版对齐，在此基础上叠加硬件加速与智能降级能力。

**支持的编码器：** 所有 CPU 软件编码器 + `h264_nvenc`, `hevc_nvenc`, `h264_amf`, `hevc_amf`，以及通过别名归一化支持 `x264`→`libx264`、`h265_nvenc`→`hevc_nvenc` 等常见写法

**主要参数：**

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--input` | 必选 | 输入视频文件或目录 |
| `--output` | 必选 | 输出文件或目录 |
| `-r, --recursive` | 否 | 递归扫描输入目录，输出自动镜像原始子目录结构 |
| `--output-width` | 必选 | 目标视频宽度 |
| `--output-height` | 必选 | 目标视频高度 |
| `--mode` | `crop` | `crop`=居中裁剪；`cover`=等比缩放至完全覆盖后裁剪（注¹） |
| `--original-width/height` | 自动检测 | 手动指定源视频尺寸，跳过 ffprobe 探测 |
| `--codec` | `libx264` | 视频编码器；支持 `auto` 自动选择与别名归一化 |
| `--crf` | `17` | CPU 编码器质量值（0–51） |
| `--cq` | `16` | GPU 编码器质量值（0–51，NVENC/AMF） |
| `--preset` | `slow` | 编码器预设；NVENC（p1~p7）与 libx264 风格自动双向映射 |
| `--audio-codec` | `copy` | 音频编码器，`copy` 流复制；可改为 `aac` / `libopus` 等 |
| `--audio-bitrate` | `128k` | 音频重编码码率（仅 `--audio-codec` 非 `copy` 时生效） |
| `--no-skip-same-size` | 否 | 即使源尺寸等于目标尺寸也强制转码（默认同尺寸跳过） |
| `--hwaccel` | `auto` | 硬件加速模式（详见下方说明） |
| `--overwrite` | 否 | 覆盖已存在的输出文件 |
| `--container` | 按编码器推断 | 手动指定容器扩展名 |
| `--ffmpeg-bin` | `ffmpeg` | 自定义 FFmpeg 可执行路径；ffprobe 自动从同目录推导 |
| `--dry-run` | 否 | 仅打印最优策略的 FFmpeg 命令，不执行转码 |
| `--log LOG_FILE` | 无 | 将所有终端输出同时写入日志文件（追加模式，带时间戳头） |
| `--extra-args` | 无 | 追加到 FFmpeg 命令末尾的自定义参数（需放在命令最后，以 `--` 分隔） |

> ¹ cover 模式下，`scale,crop` 滤镜在 CPU 侧执行（`crop_cuda` 不支持 scale 步骤），解码与编码仍可走 GPU。全 GPU 流水线（策略 1）自动仅对 crop 模式启用。

---

## 快速上手

```bash
# CPU 顺序版：居中裁剪，libx264，CRF 20
python vidcrop_cpu_v0.py \
    --input video.mkv --output out.mp4 \
    --output-width 1920 --output-height 1080 \
    --codec libx264 --crf 20 --preset slow

# CPU 并发版：等比填充+裁剪，批量，自动并行
python vidcrop_cpu_v1.py \
    --input ./videos --output ./cropped \
    --output-width 1280 --output-height 720 \
    --mode cover --codec libx265 --crf 18 --overwrite

# 硬件加速版：整目录批量，自动选择最优路径（NVENC 全流水线或自动降级）
python vidcrop_hwaccel.py \
    --input ./videos --output ./cropped \
    --output-width 1280 --output-height 720 \
    --codec auto --overwrite

# 硬件加速版：cover 模式（等比缩放+裁剪），GPU 解编码 + CPU 侧滤镜
python vidcrop_hwaccel.py \
    --input ./videos --output ./cropped \
    --output-width 1280 --output-height 720 \
    --mode cover --codec hevc_nvenc --cq 20 --overwrite

# 硬件加速版：递归扫描 + 音频重编码 + 日志记录
python vidcrop_hwaccel.py \
    --input ./footage --output ./out \
    --output-width 1920 --output-height 1080 \
    --recursive --audio-codec aac --audio-bitrate 192k \
    --log process.log --overwrite
```

---

## 常见场景配方

### 1. 4K → 1080p 批量裁剪（NVIDIA 工作站，全 GPU 流水线）

```bash
python vidcrop_hwaccel.py \
    --input ./raw_4k --output ./out_1080p \
    --original-width 3840 --original-height 2160 \
    --output-width 1920 --output-height 1080 \
    --codec hevc_nvenc --cq 20 --preset p5
```

### 2. 等比覆盖裁剪（cover 模式，hwaccel 版 — GPU 解编码 + CPU 滤镜）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --mode cover --codec hevc_nvenc --cq 20 --overwrite
```

### 3. 等比覆盖裁剪（cover 模式，CPU 并发版 — 多核并行）

```bash
python vidcrop_cpu_v1.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --mode cover --codec libx264 --crf 20 --workers 4
```

### 4. 递归扫描子目录 + 保留目录结构（hwaccel 版）

```bash
python vidcrop_hwaccel.py \
    --input ./footage --output ./out \
    --output-width 1920 --output-height 1080 \
    --recursive --codec hevc_nvenc --cq 22 --overwrite
```

### 5. Linux 服务器 + Intel 核显（VA-API 仅硬解，软件编码）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf 20 --hwaccel vaapi
```

### 6. CI / 容器环境（强制禁用所有硬件加速）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf 22 --hwaccel none
```

### 7. 干跑（Dry-run）预览命令，不转码

```bash
python vidcrop_cpu_v0.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --dry-run

# hwaccel 版同样支持，可预览实际硬件策略命令
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec hevc_nvenc --dry-run
```

### 8. 带日志归档的批量任务

```bash
# CPU 版（递归 + 日志）
python vidcrop_cpu_v1.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --recursive --log process.log

# hwaccel 版（递归 + GPU 编码 + 日志）
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --recursive --codec hevc_nvenc --cq 20 \
    --log process.log --overwrite
```

### 9. 网页分发（VP9 / WebM）

```bash
python vidcrop_cpu_v0.py \
    --input ./videos --output ./web \
    --output-width 854 --output-height 480 \
    --codec libvpx-vp9 --crf 32
```

### 10. 长期归档（AV1，极致压缩率）

```bash
python vidcrop_cpu_v1.py \
    --input ./videos --output ./archive \
    --output-width 1920 --output-height 1080 \
    --codec libaom-av1 --crf 30 --sequential
```

### 11. 音频重编码（hwaccel 版，GPU 转码 + AAC 音频）

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec hevc_nvenc --cq 20 \
    --audio-codec aac --audio-bitrate 192k --overwrite
```

### 12. 音频重编码（CPU 版，视频 + 音频统一转码）

```bash
python vidcrop_cpu_v1.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf 20 \
    --audio-codec aac --audio-bitrate 192k
```

### 13. 手动并发策略（CPU v1，2 任务 × 4 线程）

```bash
python vidcrop_cpu_v1.py \
    --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 \
    --workers 2 --threads 4
```

### 14. 自定义 FFmpeg 构建路径

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec auto \
    --ffmpeg-bin /opt/ffmpeg-7.0/bin/ffmpeg
```

### 15. 追加自定义 FFmpeg 参数（hwaccel 版）

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec hevc_nvenc --cq 20 \
    --extra-args -- -max_muxing_queue_size 4096
```

### 16. 追加自定义 FFmpeg 参数（CPU 版）

```bash
python vidcrop_cpu_v0.py \
    --input video.mp4 --output out.mp4 \
    --output-width 1280 --output-height 720 \
    --extra-args -- -max_muxing_queue_size 4096
```

---

## 硬件加速说明

`vidcrop_hwaccel.py` 启动后首先进行一次**运行时探测**，输出类似：

```
正在检测硬件加速能力...
  CUDA 解码:  可用 ✓
  h264_nvenc: 可用 ✓
  hevc_nvenc: 可用 ✓
  crop_cuda:  可用 ✓
  Vulkan:     不可用 ✗
  VA‑API:     不可用 ✗
  OpenCL:     可用 ✓
硬件能力总结：CUDA 解码=✓, h264_nvenc=✓, hevc_nvenc=✓, crop_cuda=✓, Vulkan=✗, VA‑API=✗, OpenCL=✓
```

**`--hwaccel` 参数语义：**

| 值 | 行为 |
|---|---|
| `auto`（默认） | 全面探测，按策略链自动选择 |
| `cuda` | 强制走 CUDA 路径；若 CUDA 组件完全不可用则直接退出 |
| `vulkan` / `vaapi` / `opencl` | 强制使用对应后端做硬解，编码走 CPU |
| `none` | 禁用所有硬件加速，相当于退化为 CPU 版行为 |

**5 级策略链（按优先级依次尝试）：**

1. CUDA 全流水线（硬解 + `crop_cuda` + NVENC 硬编）
2. auto 硬解 + NVENC 硬编（CPU 做 crop）
3. 指定硬解（CUDA / Vulkan / VA-API / OpenCL）+ CPU 软件编码
4. auto 模式下最佳硬解 + CPU 软件编码
5. 纯 CPU 兜底

**策略降级示例：**

> 用户请求 `--codec hevc_nvenc --cq 20`，但 NVENC 驱动异常：
> 1. 策略 1 / 2 失败（NVENC 不可用）
> 2. 策略 3 / 4 尝试硬解 + `libx265` 软件编码
> 3. `--cq 20` 自动映射为 `--crf 24`（`20 + 4`），视觉质量与用户预期一致
> 4. 若硬解也失败，兜底到纯 CPU 处理，整个批次仍能跑完

---

## 质量参数指南

```
CRF / CQ  →  0 = 无损，18 ≈ 视觉无损，23 = 默认，28 = 低码率，51 = 最低质量
```

| 用途 | 推荐 CRF（CPU） | 推荐 CQ（NVENC/AMF） |
|---|---|---|
| 归档 / 母带 | 16–18 | 16–19 |
| 通用发布 | 19–22 | 20–23 |
| 网页分发 | 23–26 | 24–27 |
| 极限压缩 | 27–30 | 28–32 |

**各版本默认质量值：**

| 版本 | CRF 默认值 | CQ 默认值 |
|---|---|---|
| `vidcrop_cpu_v0.py` | 不设默认（沿用 FFmpeg 内置） | — |
| `vidcrop_cpu_v1.py` | 20（支持 CRF 的编码器自动填充） | — |
| `vidcrop_hwaccel.py` | 17（CPU 路径） | 16（GPU 路径） |

**关键规则：**

- CPU 编码器用 `--crf`，GPU 编码器（NVENC/AMF）用 `--cq`
- hwaccel 版同时指定 `--crf` 和 `--cq` 时，根据实际落用的编码器自动选用对应参数
- 降级发生时：`--cq` 会按等效质量映射为 `--crf`，不会直接透传

**CQ → CRF 降级映射：**

| 原编码器 | 降级目标 | 映射公式 |
|---|---|---|
| `hevc_nvenc` | `libx265` | `crf = cq + 4` |
| `h264_nvenc` | `libx264` | `crf = cq + 1` |

---

## 路线图（Roadmap）

VidUtils 规划作为一个**命令行优先 / Python 原生**的视频工程工具集，当前与后续模块：

| 模块 | 状态 | 说明 |
|---|---|---|
| `vidcrop_cpu_v0.py` | ✅ 已发布 | CPU 顺序裁剪（crop + cover） |
| `vidcrop_cpu_v1.py` | ✅ 已发布 | CPU 并发裁剪（crop + cover，自动并行） |
| `vidcrop_hwaccel.py` | ✅ 已发布 | 硬件加速裁剪（CUDA/Vulkan/VA-API/OpenCL，crop + cover，功能与 CPU 版对齐） |
| `vidscale_*.py` | 🚧 规划中 | 视频缩放：双三次 / Lanczos / `scale_cuda` / `scale_npp`，支持按比例或目标分辨率 |
| `vidrepair_*.py` | 🚧 规划中 | 视频修复：容器修复（`-c copy` 重封装）、损坏帧跳过、时间戳重建、丢帧补偿 |
| `videnhance_*.py` | 🚧 规划中 | 视频增强：去噪（`hqdn3d` / `nlmeans`）、锐化（`unsharp` / `cas`）、去隔行、HDR→SDR、AI 超分接入 |
| `vidutils-cli` | 💭 设想中 | 统一 CLI 入口，子命令分发 `vidutils crop / scale / repair / enhance …` |

**统一设计约束（所有后续模块都会遵守）：**

- 参数命名风格一致（`--input / --output / --overwrite / --hwaccel / --ffmpeg-bin`）
- 批量语义一致（文件或目录均可作为 `--input`）
- 失败诊断一致（打印命令 + FFmpeg stderr 末 N 行）
- 进度与统计一致（实时进度条 + 文件级耗时 + 批量汇总）

---

## 目录结构

```
vidutils/
├── README.md                 # 本文件
├── vidcrop_cpu_v0.py         # CPU 顺序裁剪（crop + cover，单进程）
├── vidcrop_cpu_v1.py         # CPU 并发裁剪（crop + cover，多任务并行）
├── vidcrop_hwaccel.py        # 硬件加速裁剪（CUDA/Vulkan/VA-API/OpenCL，crop + cover）
├── docs/                     # （规划）设计文档与性能基准
├── examples/                 # （规划）示例素材与演示脚本
└── tests/                    # （规划）单元测试与端到端测试
```

---

## 常见问题（FAQ）

**Q1：v0、v1 和 hwaccel 如何选择？**

文件数量少或需要精确单文件进度条 → **v0**；多核 CPU、批量大、需要并行加速 → **v1**；有 NVIDIA / AMD / Intel GPU、追求吞吐 → **hwaccel**。hwaccel 与 v0/v1 在功能上已全面对齐（cover、递归、音频重编码、dry-run、日志、extra-args 均支持），区别仅在于是否有 GPU 参与以及并发模型不同。

**Q2：hwaccel 版的 cover 模式与 crop 模式有什么区别？**

cover 模式使用 `scale,crop` 组合滤镜：先按比例缩放至完全覆盖目标区域，再居中裁剪。该滤镜在 CPU 侧执行（`crop_cuda` 无法替代 `scale` 步骤），GPU 仍可负责解码与 NVENC 编码，因此仍比纯软件编码快得多。full GPU 流水线（策略 1，crop_cuda）仅对 crop 模式自动启用，cover 模式从策略 2 开始尝试。

**Q3：为什么硬件加速版探测阶段要真的跑一遍 FFmpeg？**

因为 `ffmpeg -encoders` 只反映编译期选项，**不代表运行时可用**。显卡驱动缺失、容器内无设备映射、NVENC 会话耗尽等场景都会让"编译有、运行时没有"。VidUtils 选择启动一个 64×64 单帧的测试任务，捕获特征错误串，从根本上避免误判。

**Q4：为何降级时要做 CQ → CRF 映射？**

libx265 的压缩效率高于 hevc_nvenc，**相同数值下软件编码质量偏高、文件偏大**。如果用户写了 `--cq 20` 却因 NVENC 故障落到 libx265，直接透传会生成远大于预期的文件。等效映射（`+4 / +1`）更接近用户的原始意图。

**Q5：输出路径是文件还是目录？**

规则：**输入是单文件且输出带扩展名 → 当作文件；否则当作目录**。批量模式下若输出误带扩展名会给出警告并自动去除扩展名当作目录。递归模式（`-r`）下输出目录结构与输入保持一致。

**Q6：v1 的并发任务数怎么确定？**

v1 会读取 CPU 配额（cgroup v1/v2 / `sched_getaffinity` / `os.cpu_count` 按优先级依次尝试）和内存（cgroup v1/v2 / `/proc/meminfo`）。然后结合编码器画像中的"推荐线程数"和"单任务内存估算"，同时满足 CPU 槽数限制和内存预算，取两者中较小的值作为并行任务数上限。可通过 `--workers` / `--threads` / `--mem-per-job` 手动覆盖。

**Q7：ProRes / AV1 编码非常慢怎么办？**

这是编码器本身的特性，VidUtils 不做额外猜测。可通过 `--preset` 调低（对 libx264/265），或改用 hwaccel 版配合 NVENC 换取吞吐。AV1 建议在 v1 中用 `--workers` 控制并发数，避免内存溢出。

**Q8：源文件损坏怎么办？**

三个脚本都启用了 `-err_detect ignore_err` 与 `-fflags +genpts+discardcorrupt`，损坏帧会被跳过并在日志中提示。完全无法解码的文件会被判失败，不会阻塞后续批次。

---

## 贡献指南

欢迎以 Issue / PR 的形式参与：

1. **提 Bug**：请附上 FFmpeg 版本（`ffmpeg -version`）、操作系统、完整命令行与 stderr 末若干行。
2. **提新功能**：优先对齐[路线图](#路线图roadmap)中已规划的模块；新模块请先发 Issue 讨论接口设计，以保持参数风格统一。
3. **代码风格**：遵循 PEP 8，函数级中文 docstring，关键分支要有 inline 注释说明"为什么这样写"而不仅是"在做什么"。
4. **测试**：对新增参数分支至少覆盖 happy path + 一个边界 / 失败 case。

---

## 许可证

本项目采用 **MIT License**（或根据实际情况替换为 Apache-2.0 / GPLv3）。
详见仓库根目录 `LICENSE` 文件。

---

> **VidUtils** — *Pragmatic video tooling, powered by FFmpeg.*
