# VidUtils · 视频实用工具集

> 一组基于 **FFmpeg** 的命令行视频处理工具，聚焦 *批量、可复现、生产可用* 的视频工程任务。
> 当前提供居中裁剪（CPU / 硬件加速双版本），后续将逐步扩展缩放、修复、增强等能力。

---

## 目录

- [项目简介](#项目简介)
- [功能矩阵](#功能矩阵)
- [环境要求](#环境要求)
- [安装](#安装)
- [工具一览](#工具一览)
  - [vidcrop_cpu.py — 纯 CPU 居中裁剪](#vidcrop_cpupy--纯-cpu-居中裁剪)
  - [vidcrop_hwaccel.py — 硬件加速居中裁剪](#vidcrop_hwaccelpy--硬件加速居中裁剪)
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

| 能力 | `vidcrop_cpu.py` | `vidcrop_hwaccel.py` |
|---|---|---|
| 居中裁剪 | ✅ | ✅ |
| 单文件 / 目录批量 | ✅ | ✅ |
| CPU 软件编码器（libx264/265、VP9、AV1、ProRes…） | ✅ | ✅ |
| NVIDIA NVENC（h264 / hevc） | ❌ | ✅ |
| 硬件解码（CUDA / Vulkan / VA-API / OpenCL） | ❌ | ✅ |
| `crop_cuda` 全 GPU 流水线 | ❌ | ✅ |
| 运行时硬件能力探测 | — | ✅ |
| 策略自动降级 | — | ✅ |
| 实时进度条（%/帧/fps/ETA） | ❌ | ✅ |
| 文件级耗时与体积对比 | ❌ | ✅ |
| 编码器别名归一化 | ❌ | ✅ |
| preset 双向映射（NVENC ↔ x264） | ❌ | ✅ |
| CQ → CRF 等效质量映射（降级场景） | ❌ | ✅ |

> 简而言之：**CPU 版**稳定、极简、零依赖；**硬件加速版**在 CPU 版基础上叠加了硬件加速、智能降级与可观测能力。两者可在同一 pipeline 中按需互换。

---

## 环境要求

- **Python** ≥ 3.8（仅使用标准库，无需 `pip install`）
- **FFmpeg** ≥ 4.4，且 `ffmpeg` / `ffprobe` 在 `PATH` 中可见（或通过 `--ffmpeg-bin` 指定）
- **硬件加速版本额外需要**（可选，按需启用）：
  - NVIDIA CUDA：较新驱动 + 支持 NVENC 的显卡（Turing / Ampere / Ada 等）
  - Vulkan：支持 Vulkan 1.1+ 的 GPU 与驱动
  - VA-API：Linux 下 Intel / AMD GPU 驱动
  - OpenCL：可用的 OpenCL 1.2+ 运行时

**检查 FFmpeg 是否具备硬件编译选项：**

```bash
ffmpeg -hide_banner -encoders | grep -E 'nvenc|vaapi|qsv|amf|videotoolbox'
ffmpeg -hide_banner -hwaccels
```

---

## 安装

VidUtils 以独立脚本分发，无需打包安装：

```bash
git clone https://github.com/<your-org>/vidutils.git
cd vidutils

# 可选：赋予执行权限（类 Unix 系统）
chmod +x vidcrop_cpu.py vidcrop_hwaccel.py

# 快速自检
python vidcrop_cpu.py --help
python vidcrop_hwaccel.py --help
```

---

## 工具一览

### `vidcrop_cpu.py` — 纯 CPU 居中裁剪

- 零硬件依赖，跨平台行为完全一致
- 支持 `libx264`, `libx265`, `libvpx`, `libvpx-vp9`, `libaom-av1`, `librav1e`, `prores(_ks)`, `mpeg4`, `libxvid`, `copy`
- 根据编码器自动推断容器扩展名（`.mp4 / .webm / .mov / .avi`）
- 不支持的参数（如给 `libvpx-vp9` 传 `--preset`）会被自动忽略并提示

适用场景：服务器批处理、CI 流水线、无 GPU 环境、对结果可复现性要求高的归档任务。

### `vidcrop_hwaccel.py` — 硬件加速居中裁剪

- **运行时**探测硬件能力（非编译字符串匹配），逐项启动微型 FFmpeg 任务验证
- 根据硬件能力自动生成 **5 级策略链** 并按优先级尝试：
  1. CUDA 全流水线（硬解 + `crop_cuda` + NVENC 硬编）
  2. 硬解（auto） + NVENC 硬编
  3. 指定硬解（CUDA / Vulkan / VA-API / OpenCL） + CPU 编码
  4. auto 模式下选择最佳硬解 + CPU 编码
  5. 纯 CPU 兜底
- 前一级失败自动降级到下一级，**不会因某个硬件问题导致整批失败**
- 编码器别名自动归一化：`h265_nvenc → hevc_nvenc`、`x264 → libx264`、`x265 → libx265` …
- preset 在 NVENC（`p1~p7`）与 libx264（`ultrafast~veryslow`）之间自动双向映射
- 降级场景下 `--cq` 按等效视觉质量映射为 `--crf`（不盲目透传数值）：

  | 源编码器 | 目标编码器 | 映射公式 |
  |---|---|---|
  | `hevc_nvenc` | `libx265` | `crf = cq + 4` |
  | `h264_nvenc` | `libx264` | `crf = cq + 1` |

适用场景：本地工作站、拥有 NVIDIA / Intel / AMD GPU 的环境、需要处理大量素材且对吞吐量敏感的任务。

---

## 快速上手

**最常见的两条命令：**

```bash
# 把一个文件夹下的所有视频居中裁剪为 1280×720（自动选择最佳编码器）
python vidcrop_hwaccel.py \
    --input ./videos --output ./cropped \
    --output-width 1280 --output-height 720 \
    --codec auto --overwrite

# 纯 CPU 环境 · libx265 高质量归档
python vidcrop_cpu.py \
    --input video.mkv --output out.mp4 \
    --output-width 1920 --output-height 1080 \
    --codec libx265 --crf 18 --preset slow
```

---

## 常见场景配方

### 1. 4K → 1080p 批量裁剪（NVIDIA 工作站）

```bash
python vidcrop_hwaccel.py \
    --input ./raw_4k --output ./out_1080p \
    --original-width 3840 --original-height 2160 \
    --output-width 1920 --output-height 1080 \
    --codec hevc_nvenc --cq 20 --preset p5
```

### 2. Linux 服务器 + Intel 核显（VA-API 仅硬解，软件编码）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf 20 --hwaccel vaapi
```

### 3. CI / 容器环境（强制禁用所有硬件加速）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf 22 --hwaccel none
```

### 4. 网页分发（VP9 / WebM）

```bash
python vidcrop_cpu.py \
    --input ./videos --output ./web \
    --output-width 854 --output-height 480 \
    --codec libvpx-vp9 --crf 32
```

### 5. 长期归档（AV1，极致压缩率）

```bash
python vidcrop_cpu.py \
    --input ./videos --output ./archive \
    --output-width 1920 --output-height 1080 \
    --codec libaom-av1 --crf 30
```

### 6. 自定义 FFmpeg 构建路径

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec auto \
    --ffmpeg-bin /opt/ffmpeg-7.0/bin/ffmpeg
```

> `ffprobe` 会从 `--ffmpeg-bin` 所在目录推断，保证版本一致。

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

| 用途 | 推荐 CRF（CPU） | 推荐 CQ（NVENC） |
|---|---|---|
| 归档 / 母带 | 16–18 | 16–19 |
| 通用发布 | 19–22 | 20–23 |
| 网页分发 | 23–26 | 24–27 |
| 极限压缩 | 27–30 | 28–32 |

**关键规则：**

- CPU 编码器用 `--crf`，GPU 编码器用 `--cq`
- 两者都不指定时：CPU 默认 `--crf 17`，GPU 默认 `--cq 16`
- 两者同时指定：按实际编码器族自动选用对应参数
- 降级发生时：`--cq` 会按等效质量映射为 `--crf`，不会直接透传

---

## 路线图（Roadmap）

VidUtils 规划作为一个**命令行优先 / Python 原生**的视频工程工具集，当前与后续模块：

| 模块 | 状态 | 说明 |
|---|---|---|
| `vidcrop_cpu.py` | ✅ 已发布 | 纯 CPU 居中裁剪 |
| `vidcrop_hwaccel.py` | ✅ 已发布 | 硬件加速居中裁剪（CUDA/Vulkan/VA-API/OpenCL） |
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
├── vidcrop_cpu.py            # 纯 CPU 居中裁剪
├── vidcrop_hwaccel.py        # 硬件加速居中裁剪
├── docs/                     # （规划）设计文档与性能基准
├── examples/                 # （规划）示例素材与演示脚本
└── tests/                    # （规划）单元测试与端到端测试
```

---

## 常见问题（FAQ）

**Q1：为什么硬件加速版探测阶段要真的跑一遍 FFmpeg？**
因为 `ffmpeg -encoders` 只反映编译期选项，**不代表运行时可用**。显卡驱动缺失、容器内无设备映射、NVENC 会话耗尽等场景都会让"编译有、运行时没有"。VidUtils 选择启动一个 64×64 单帧的测试任务，捕获特征错误串，从根本上避免误判。

**Q2：为何降级时要做 CQ → CRF 映射？**
libx265 的压缩效率高于 hevc_nvenc，**相同数值下软件编码质量偏高、文件偏大**。如果用户写了 `--cq 20` 却因 NVENC 故障落到 libx265，直接透传会生成远大于预期的文件。等效映射（`+4 / +1`）更接近用户的原始意图。

**Q3：输出路径是文件还是目录？**
规则：**输入是单文件且输出带扩展名 → 当作文件；否则当作目录**。批量模式下若输出误带扩展名会给出警告并自动去除扩展名当作目录。

**Q4：ProRes / AV1 编码非常慢怎么办？**
这是编码器本身的特性，VidUtils 不做额外猜测。可通过 `--preset` 调低（对 libx264/265），或改用 NVENC（硬件加速版）换取吞吐。

**Q5：源文件损坏怎么办？**
两个脚本都启用了 `-err_detect ignore_err` 与 `-fflags +genpts+discardcorrupt`，损坏帧会被跳过并在日志中提示。完全无法解码的文件会被判失败，不会阻塞后续批次。

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