# VidUtils · 视频实用工具集

> 一组基于 **FFmpeg** 的命令行视频处理工具，聚焦 *批量、可复现、生产可用* 的视频工程任务。
> 当前提供居中裁剪（CPU 顺序版 / CPU 并发版 / CPU 并发增强版 / 硬件加速版四个变体），
> 以及基于 NVIDIA 光流硬件的光流插帧 2x 工具（`interp_2x_safe.sh`，含配套回归测试）；
> 后续将逐步扩展缩放、修复、增强等能力。

---

## 目录

- [项目简介](#项目简介)
- [功能矩阵](#功能矩阵)
- [环境要求](#环境要求)
- [安装](#安装)
- [工具一览](#工具一览)
  - [vidcrop_cpu_v0.py — CPU 顺序裁剪](#vidcrop_cpu_v0py--cpu-顺序裁剪)
  - [vidcrop_cpu_v1.py — CPU 并发裁剪](#vidcrop_cpu_v1py--cpu-并发裁剪)
  - [vidcrop_cpu_v2.py — CPU 并发裁剪（推荐）](#vidcrop_cpu_v2py--cpu-并发裁剪推荐)
  - [vidcrop_hwaccel.py — 硬件加速裁剪](#vidcrop_hwaccelpy--硬件加速裁剪)
  - [convert_crf.py — 质量换算表（被上面两个脚本依赖）](#convert_crfpy--质量换算表被上面两个脚本依赖)
  - [interp_2x_safe.sh — 光流插帧 2x（崩溃安全版）](#interp_2x_safesh--光流插帧-2x崩溃安全版)
  - [test_interp_2x_lock.sh — 回归测试（并发与锁）](#test_interp_2x_locksh--回归测试并发与锁)
- [快速上手](#快速上手)
- [常见场景配方](#常见场景配方)
- [硬件加速说明](#硬件加速说明)
- [进度显示](#进度显示)
- [AV1 / VP9 编码支持](#av1--vp9-编码支持)
- [质量参数指南](#质量参数指南)
- [已知限制](#已知限制)
- [路线图（Roadmap）](#路线图roadmap)
- [目录结构](#目录结构)
- [工程记忆（memory/）](#工程记忆memory)
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
- **进度两层可见**：单任务进度条（%/帧/fps/speed/已用/剩余）+ 整批队列进度与剩余时间预测，跑的过程中就知道"这批还要多久"。

---

## 功能矩阵

| 能力 | `v0` | `v1` | `v2` | `hwaccel` |
|---|---|---|---|---|
| 居中裁剪（crop 模式） | ✅ | ✅ | ✅ | ✅ |
| 等比缩放+居中裁剪（cover 模式） | ✅ | ✅ | ✅ | ✅¹ |
| 单文件 / 目录批量 | ✅ | ✅ | ✅ | ✅ |
| 递归扫描目录（`-r`） | ✅ | ✅ | ✅ | ✅ |
| `--crop-ratio` 按比例自动算裁剪尺寸 | ❌ | ❌ | ✅ | ✅ |
| `--flag` 自定义输出名后缀 | ❌ | ❌ | ✅ | ✅ |
| `--color-range` 值域控制（含真转换） | ❌ | ❌ | ✅ | ✅ |
| CPU 软件编码器（libx264/265、VP9、AV1、ProRes、MJPEG…） | ✅ | ✅ | ✅ | ✅ |
| AV1：`libsvtav1` / `libaom-av1` / `librav1e` | ❌ / ✅ / ✅ | ❌ / ✅ / ✅ | ✅ | ✅ |
| VP9：`libvpx-vp9` | ✅ | ✅ | ✅ | ✅ |
| NVIDIA NVENC（h264 / hevc / av1²） | ❌ | ❌ | ✅³ | ✅ |
| AMD AMF / Intel QSV（含 av1） | ❌ | ❌ | ✅³ | ✅³ |
| 硬件解码（CUDA / Vulkan / VA-API / OpenCL） | ❌ | ❌ | ❌ | ✅ |
| `crop_cuda` 全 GPU 流水线 | ❌ | ❌ | ❌ | ✅¹ |
| 运行时硬件能力探测 | — | — | — | ✅ |
| 策略链自动降级（5 级） | — | — | — | ✅ |
| 编码器级降级（GPU 编码器 → CPU 软编） | — | — | ✅ | ✅ |
| 多任务并行处理 | ❌ | ✅ | ✅ | ❌ |
| CPU / 内存自动探测与并发决策 | ❌ | ✅ | ✅ | ❌ |
| 音频重编码（aac / libopus…） | ❌ | ✅ | ✅ | ✅ |
| 容器不兼容时自动换音频编码（WebM→Opus） | ❌ | ❌ | ✅ | ✅ |
| 同尺寸跳过优化 | ❌ | ✅ | ✅ | ✅ |
| Dry-run 命令预览 | ✅ | ✅ | ✅ | ✅ |
| 日志文件记录（`--log`） | ✅ | ✅ | ✅ | ✅ |
| `--extra-args` 自定义 FFmpeg 参数 | ✅ | ✅ | ✅ | ✅ |
| 实时进度条（%/帧/fps/ETA） | ✅ | ✅（并发时聚合面板） | ✅（并发时聚合面板） | ✅ |
| 整批队列进度 + 整批剩余 ETA | ❌ | ❌ | ✅（并发面板实时；顺序模式每个文件结束后刷新） | ✅（常驻在进度条尾部） |
| 文件级耗时与体积对比 | ✅ | ✅ | ✅ | ✅ |
| 编码器别名归一化 | ❌ | ❌ | ✅ | ✅ |
| preset 双向映射（NVENC ↔ x264 ↔ SVT-AV1） | ❌ | ❌ | ✅ | ✅ |
| 按 CPU 核数自动选速度档（AV1） | ❌ | ❌ | ✅ | ✅ |
| `--crf-ref` / `--cq-ref` 统一质量基准 | ❌ | ❌ | ✅ | ✅ |
| `--fallback-policy` 策略控制 / `--cuda-diagnostics` | ❌ | ❌ | ❌ | ✅ |

> ¹ hwaccel 版 cover 模式始终使用 CPU 侧 `scale,crop` 滤镜，GPU 仅负责解码与编码；`crop_cuda` 全 GPU 流水线（策略 1）仅对 crop 模式启用。
> ² `av1_nvenc` 需第 8 代 NVENC（Ada / RTX 40 / L40 及以上），否则自动降级为 `libsvtav1`。
> ³ v2 可以**使用**硬件编码器（写 `--codec h264_nvenc` 等），但不做硬件探测、也不做硬件解码——解码全程走 CPU。是否可用由 FFmpeg 与驱动自行决定。

> **如何选择？**
> - 有多核 CPU、无 GPU、批量大 → **v2**（默认选择，功能最全）
> - 有 NVIDIA / AMD / Intel GPU、追求吞吐 → **hwaccel**
> - 只需要最简单的顺序执行、或要对照旧行为 → **v0 / v1**

---

## 环境要求

- **Python** ≥ 3.8（仅使用标准库，无需 `pip install`）
- **FFmpeg** ≥ 4.4，且 `ffmpeg` / `ffprobe` 在 `PATH` 中可见（或通过 `--ffmpeg-bin` 指定，仅 hwaccel 版支持）
- **AV1 / VP9 额外需要** FFmpeg 编译时带上对应库（Ubuntu 官方包通常已带；`libsvtav1` 需 FFmpeg ≥ 4.4 且 `--enable-libsvtav1`）：

```bash
ffmpeg -hide_banner -encoders 2>/dev/null | grep -E 'libsvtav1|libaom-av1|librav1e|libvpx-vp9|av1_nvenc'
```

- **硬件加速版本额外需要**（可选，按需启用）：
  - NVIDIA CUDA：较新驱动 + 支持 NVENC 的显卡（Turing / Ampere / Ada 等）
  - AMD AMF：支持 AMF 的 Radeon 显卡与驱动
  - Intel QSV：集显或 Arc 系列 + 对应驱动
  - Vulkan：支持 Vulkan 1.1+ 的 GPU 与驱动
  - VA-API：Linux 下 Intel / AMD GPU 驱动
  - OpenCL：可用的 OpenCL 1.2+ 运行时

- **`interp_2x_safe.sh` 额外需要**（光流插帧，条件比上面严）：
  - FFmpeg 编译时带 `nvinterpolate` 滤镜 —— **发行版官方包一般没有**，需自行构建（本机 `/usr/local/bin/ffmpeg` 7.1 已含）
  - NVIDIA GPU 为 **Turing（CC 7.5）或更新**，驱动 ≥ 525（光流跑在 NVOFA 硬件上，不是 NVENC/NVDEC）
  - Bash ≥ 4.4 与 `flock`（util-linux）—— 单实例锁用它

```bash
ffmpeg -hide_banner -filters | grep nvinterpolate
nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader
```

**检查 FFmpeg 是否具备硬件编译选项：**

```bash
ffmpeg -hide_banner -encoders | grep -E 'nvenc|amf|qsv|vaapi|videotoolbox'
ffmpeg -hide_banner -hwaccels
```

> 注意：`ffmpeg -encoders` 只反映**编译期**选项，不代表运行时可用。hwaccel 版会真的跑一遍探针来确认（见 [FAQ Q3](#常见问题faq)）。

---

## 安装

VidUtils 以独立脚本分发，无需打包安装：

```bash
git clone https://github.com/<your-org>/vidutils.git
cd vidutils

# 可选：赋予执行权限（类 Unix 系统）
chmod +x vidcrop_cpu_*.py vidcrop_hwaccel.py

# 快速自检
python vidcrop_cpu_v2.py --help
python vidcrop_hwaccel.py --help
```

> `vidcrop_cpu_v2.py` 与 `vidcrop_hwaccel.py` 运行时会 import 同目录的 `convert_crf.py`（质量换算表）。**拷贝脚本时请一并带上它**，否则会 ImportError。

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
| `--output-width` / `--output-height` | 必选 | 目标视频宽 / 高 |
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

多任务并发版，在 v0 基础上增加了并行处理、智能资源分配和音频重编码能力。功能已被 v2 覆盖，**新任务建议直接用 v2**。

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

### `vidcrop_cpu_v2.py` — CPU 并发裁剪（推荐）

v1 的增强版：保留并发模型，补齐 **AV1 / VP9 全链路**、编码器别名、preset 双向映射、`--crop-ratio`、`--color-range`、`--flag` 与统一质量基准 `--crf-ref` / `--cq-ref`。

**支持的编码器：**

| 家族 | 编码器 |
|---|---|
| H.264 / HEVC | `libx264` `libx265` `h264_nvenc` `hevc_nvenc` `h264_amf` `hevc_amf` `h264_qsv` `hevc_qsv`³ |
| AV1 | `libsvtav1`（推荐） `libaom-av1` `librav1e` `av1_nvenc`² `av1_qsv` `av1_amf`³ |
| VP9 / VP8 | `libvpx-vp9` `libvpx` `vp9_qsv` |
| 其他 | `prores` `prores_ks` `mpeg4` `libxvid` `mjpeg` `copy` |

> ² `av1_nvenc` 需第 8 代 NVENC；³ v2 不做硬件探测，硬件编码器名可用但解码仍走 CPU。

**主要参数：**

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--input` / `--output` | 必选 | 输入 / 输出（文件或目录） |
| `--output-width` / `--output-height` | 与 `--crop-ratio` 二选一 | 目标视频宽 / 高 |
| `--crop-ratio` | 无 | 目标宽高比（`16:9` / `4:3` / 浮点 `1.777`），自动算最大化裁剪尺寸 |
| `--mode` | `crop` | `crop` / `cover` |
| `--codec` | `libx264` | 视频编码器；支持别名（`vp9`→`libvpx-vp9`、`av1`→`libaom-av1`、`svtav1`→`libsvtav1`、`rav1e`→`librav1e` …） |
| `--crf` | `21` | CPU 编码器质量（0–51）；**字面量原样下发，不换算** |
| `--cq` | `23` | GPU 编码器质量（0–51）；落到 CPU 软编时按等效表换算为 CRF |
| `--crf-ref` | 无 | 以 **libx264 CRF** 为基准给出质量，按等效表换算到目标编码器；与 `--crf`/`--cq` 互斥 |
| `--cq-ref` | 无 | 以 **h264_nvenc CQ** 为基准给出质量，按等效表换算；与 `--crf`/`--cq` 互斥 |
| `--preset` | CPU `medium` / GPU `p5` | 支持 x264 风格与 NVENC `p1~p7`，自动双向映射；`libsvtav1` 自动转 0~13 整数档 |
| `--pix-fmt` | `auto` | 输出像素格式；可填 `none` 禁用 |
| `--color-range` | `auto` | `tv` / `pc` 强制值域，且与源不同时会插入 scale 滤镜做**真值域转换** |
| `--flag` | `_cropped` / `_covered` | 自定义输出名后缀（仅对自动生成的输出名生效） |
| `--audio-codec` / `--audio-bitrate` | `copy` / `128k` | 音频编码；WebM 容器下 `copy` 遇到不兼容音轨会自动换成 `libopus` |
| `--workers` / `--threads` / `--mem-per-job` | `0`（自动） | 并发控制 |
| `--sequential` | 否 | 强制顺序执行 |
| `--recursive, -r` | 否 | 递归扫描 |
| `--container` / `--overwrite` / `--no-skip-same-size` | — | 容器 / 覆盖 / 同尺寸不跳过 |
| `--dry-run` / `--log` / `--extra-args` | — | 预览 / 日志 / 追加参数 |

**编码器画像（推荐线程数 × 内存估算）：**

| 编码器 | 推荐线程数 | 单任务内存估算 |
|---|---|---|
| libx264 / libx265 | 4 | 0.8 / 1.2 GB |
| libvpx-vp9 | 4 | 1.0 GB |
| libaom-av1 | 4 | 1.5 GB |
| libsvtav1 | 8 | 2.0 GB |
| librav1e | 4 | 1.2 GB |
| av1_nvenc / av1_qsv | 2 | 0.5 GB |
| mpeg4 / libxvid / mjpeg | 2 | 0.4 / 0.4 / 0.3 GB |
| copy | 1 | 0.1 GB |

---

### `vidcrop_hwaccel.py` — 硬件加速裁剪

硬件加速版，运行时探测 GPU 能力，按优先级依次尝试策略链，前一级失败自动降级。功能与 v2 对齐，在此基础上叠加硬件解码与策略降级能力。

**支持的编码器：** 所有 CPU 软件编码器 + `h264_nvenc` `hevc_nvenc` `av1_nvenc`² `h264_amf` `hevc_amf` `av1_amf` `h264_qsv` `hevc_qsv` `av1_qsv` `vp9_qsv`，并支持 `auto` 自动选择与别名归一化（`x264`→`libx264`、`h265_nvenc`→`hevc_nvenc`、`av1`→`libaom-av1`、`svtav1`→`libsvtav1` …）

**主要参数：**

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--input` / `--output` | 必选 | 输入 / 输出（文件或目录） |
| `-r, --recursive` | 否 | 递归扫描输入目录，输出自动镜像原始子目录结构 |
| `--output-width` / `--output-height` | 与 `--crop-ratio` 二选一 | 目标视频宽 / 高 |
| `--crop-ratio` | 无 | 目标宽高比，自动算最大化裁剪尺寸 |
| `--mode` | `crop` | `crop` / `cover`（注¹） |
| `--original-width/height` | 自动检测 | 手动指定源尺寸，跳过 ffprobe |
| `--codec` | **`h264_nvenc`** | 支持 `auto`；无 NVENC 时自动降级为 **`libx264`** |
| `--cq` | **`23`** | GPU 编码器质量（0–51）；字面量原样下发 |
| `--crf` | **`21`** | CPU 编码器质量（0–51）；字面量原样下发 |
| `--crf-ref` / `--cq-ref` | 无 | 统一质量基准（同上），与 `--crf`/`--cq` **互斥，混用直接报错退出** |
| `--preset` | GPU `p5` / CPU `medium` | NVENC（p1~p7）↔ libx264 风格双向映射；`libsvtav1` 自动转整数档 |
| `--color-range` | `auto` | 同 v2 |
| `--flag` | `_cropped` / `_covered` | 同 v2 |
| `--audio-codec` / `--audio-bitrate` | `copy` / `128k` | 音频编码；WebM 下自动换 `libopus` |
| `--no-skip-same-size` | 否 | 同尺寸强制转码 |
| `--hwaccel` | `auto` | `auto` / `cuda` / `vulkan` / `vaapi` / `opencl` / `none` |
| `--fallback-policy` | `auto` | `auto` / `strict-cuda` / `nvenc-only` / `cpu-only`（见[硬件加速说明](#硬件加速说明)） |
| `--cuda-diagnostics` | 否 | 输出多分辨率探针与详细错误，定位"硬解不可用"根因 |
| `--cuda-device-id` | `0` | 多 GPU 环境下选择解码设备 |
| `--overwrite` / `--container` | — | 覆盖 / 容器扩展名 |
| `--ffmpeg-bin` | `ffmpeg` | 自定义 FFmpeg 路径；ffprobe 自动从同目录推导 |
| `--dry-run` / `--log` / `--extra-args` | — | 预览 / 日志 / 追加参数 |

> ¹ cover 模式下 `scale,crop` 滤镜在 CPU 侧执行（`crop_cuda` 不支持 scale 步骤），解码与编码仍可走 GPU。

---

### `convert_crf.py` — 质量换算表（被上面两个脚本依赖）

单一事实来源：以 **libx264 CRF** 为轴，登记各编码器的线性换算系数 `value = a × x264_crf + b` 与合法区间。

```python
from convert_crf import from_x264_crf, to_x264_crf, convert_quality

from_x264_crf('libvpx-vp9', 21)              # → 27
convert_quality('h264_nvenc', 23, 'libx264')  # → 18
```

改动换算系数只需改这一个文件，两个裁剪脚本自动跟随。详见 [质量参数指南](#质量参数指南)。

---

### `interp_2x_safe.sh` — 光流插帧 2x（崩溃安全版）

用 NVIDIA **NVOFA 光流硬件**（FFmpeg 的 `nvinterpolate` 滤镜）把帧率**翻倍**：目标帧率 = 源帧率 × 2，
**时长不变**，音轨原样 `-c copy`。例如 4K `24000/1001`（23.976fps）→ `48000/1001`（47.952fps）。

与裁剪工具不同，这不是"换个参数跑 ffmpeg"，而是**把长任务做成一堆可恢复的分片** ——
因为 4K 2x 的真实耗时是"约 50 分钟 / 半小时素材"，中途挂一次就是全损。

```bash
# 正式跑：脱离会话后台执行（务必这样起，理由见下）
setsid bash interp_2x_safe.sh /path/in.mp4 -w /tmp/work \
    > /tmp/work/run.log 2>&1 < /dev/null &

# 看进度：分片数就是进度
tail -f /tmp/work/run.log
ls /tmp/work/parts/

# 中断后恢复：原样再执行同一条命令，已完成的分片秒过
```

**位置参数**

| 参数 | 必需 | 说明 |
|---|---|---|
| `<输入视频>` | 是 | 分片目录与默认输出名由它推导 |
| `[输出路径]` | 否 | 可以是**目录**（以 `/` 结尾，或本身就是已存在的目录 → 自动起名 `<输入名>_2x.mp4`），也可以是**文件**（带扩展名，支持 `mp4` / `mov` / `mkv`）。默认 `<WORKDIR>/<输入名>_2x.mp4` |

**选项**

| 选项 | 默认 | 说明 |
|---|---|---|
| `-w, --workdir DIR` | `/workspace/interp_2x/<输入名>` | 分片与输出的目录；**每个输入一个目录**，换输入自动分家 |
| `-p, --preset NAME` | `p5` | `hevc_nvenc` 预设，越大越慢、同码率画质越好 |
| `-c, --cq N` | `25` | 恒定质量，越大越省码率 |
| `-L, --seg-len SEC` | `300` | 每片秒数。越大接缝越少但崩一次损失越多；越小损失窗口小但每片开头都要重跑一次光流预热 |
| `--cap SEC` | 关 | 只处理前 N 秒，可小数（试跑验证 / 只要前一段）。负数或非数字直接报错；超过源总长等于不设置 |
| `--overwrite` | 关 | 允许覆盖已存在的目标文件。**默认拒绝**，且在任何编码开始前就退出 |

`L` / `TOTAL_CAP` / `WORKDIR` / `PRESET` / `CQ` 也认同名环境变量，命令行优先。

**前置条件**

- FFmpeg **带 `nvinterpolate` 滤镜**（本机 `/usr/local/bin/ffmpeg` 7.1 已含）
- NVIDIA GPU，**Turing（CC 7.5）或更新**；驱动 ≥ 525
- 源视频有**明确的帧率元数据**（目标帧率是拿它 ×2 算出来的，VFR / 0 不行）

**只支持 2 倍**：滤镜串里的 `fps=source_fps*2` 与片头要去掉的重复帧数都按 2x 写死。
1.25x / 2.5x 的预热帧数不同，要用得改源码里这两处。

**为什么不是一条 `ffmpeg ... out.mp4`**

2026-09-14 有一次 4K 2x 跑到第 34 分钟被中断（代码宿主自己崩了，把同进程组的 ffmpeg 一起带走），
产物 2.1GB 但**没有 moov** → `ffprobe` 报 `moov atom not found`，0 字节可用，也没有可续传的点。
所以这条链做了三件事：

1. **`setsid` 脱离会话** —— 宿主 / 终端死掉不影响本任务；
2. **每 `L` 秒一片写 TS** —— TS 没有全局索引，被杀时已完成的分片原样可用；
3. **已存在的分片自动跳过** —— 重跑同一条命令就是"断点恢复"。

收尾再把分片 `concat` 成 MP4，音轨从原片一次性 `-c copy`（音频本来没改，也避开 AAC 切点问题）。

**产出**

| 路径 | 说明 |
|---|---|
| `parts/p00000.ts …` | 已完成的分片 —— **分片数就是进度** |
| `parts/p00000.meta …` | 该片的切法（`ss dt`），决定它能不能被复用 |
| `parts.txt` | concat 清单（只列本次要用的片） |
| `recipe.txt` | 「输入 + 参数」指纹；不匹配会**拒绝启动** |
| `<输入名>_2x.mp4` | 成片 |

**防呆（两层，目的都是"绝不静默产出错内容"）**

- **第一层 `recipe.txt`**：记 `slice|in|L|preset|cq|trim`。这些一变，目录里**每一片**的内容都会不同
  （帧边界变了 / 画质档变了 / 换了视频）→ 整体拒绝，并打印新旧差异。
- **第二层 每片 `.meta`**：记该片的 `ss dt`。总时长只影响**末尾那一片**的切法，所以改 `--cap`
  不再整体拒绝，而是逐片比对、**只重编边界那一片**。

**并发：同一个 `-w` 只允许一个实例**

`$WORKDIR/.lock` 上有 `flock`，第二个实例会立刻退出并提示。原因是两个实例共用一个分片目录时，
循环开头的 `rm -f` 会删掉对方正在写的临时文件（ffmpeg 仍在往已被 unlink 的 inode 写），
先跑完的把临时文件 `mv` 走、后跑完的就报
`mv: cannot stat '.../p00002.ts.part': No such file or directory`，**而且两边都白跑**。
锁是内核级的，进程被 kill 会自动释放，不会留死锁；临时文件名另带 PID 作纵深防御。

**失败时的样子**

| 情况 | 表现 |
|---|---|
| 某片失败 | 打印 `FAIL` 并非 0 退出；半成品留在 `pXXXXX.ts.part.<pid>`。它不是 `.ts`，不会被误判成"已完成"；持锁启动时会自动清掉，重跑也会重做该片 |
| 拼接失败 | 分片都还在，可手工重拼，或直接重跑 |
| 被杀 / 断电 | 已成名的 `.ts` 分片保留；临时文件下次持锁启动时自动清理 |

**注意**

- 所有 ffmpeg 调用都带 `-nostdin`：非前台进程组且 stdin 指向终端时，会被 SIGTTIN 停住而永久挂起。
- 每片开头会重复约 3 帧（光流拿不到"前一帧"），脚本已用 `trim=start_frame=3,setpts=PTS-STARTPTS` 裁掉。

---

### `test_interp_2x_lock.sh` — 回归测试（并发与锁）

守住 `interp_2x_safe.sh` 的**单实例锁与并发安全**，也就是上面那个"两边都白跑"的原始 bug；
同时把分片复用、残留清理、`--overwrite` 早退、检查顺序等不变量一起钉住。

```bash
bash test_interp_2x_lock.sh                                  # 自动找小素材，约 43s
SUT=./interp_2x_safe.sh bash test_interp_2x_lock.sh
TEST_INPUT=/path/small.mp4 bash test_interp_2x_lock.sh
```

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `TEST_INPUT` | 自动在 `<本脚本目录>/input_videos/` 与 `/workspace/input_videos/` 找 `new5_10s.mp4` | 测试输入，小素材即可（要反复编解码） |
| `SUT` | 同目录的 `interp_2x_safe.sh` | 被测脚本。**指向变异版可以验证测试本身是否真能抓到问题** |
| `SEG` | `2` | 分片长度，越小片越多、并发窗口越大 |

**退出码**：`0` 全部通过 / `1` 有用例失败 / `2` 环境不具备（无 GPU、无 `nvinterpolate`、无 `flock`、找不到素材）→ 跳过。

**覆盖的用例**

| 用例 | 守什么 |
|---|---|
| 1 顺序跑两次 | 锁在第一次结束后释放；`.lock` 文件留在盘上也不误挡；分片被**真的复用**（指纹未变） |
| 2 外部持锁 | 必须被拒、零编码动作、**不得碰已有分片**；报的是锁冲突而不是"目标文件已存在"（回归检查顺序） |
| 3 真并发 | A 在跑时 B 被拒；B 没碰过 A 已有的分片（局部指纹） |
| 4 零延迟同时启动 | **恰好一个成功**；分片/meta 配对且切法正确；成片没被写坏 |
| 5 持锁进程被 SIGKILL | 锁必须释放（不死锁）；真实残片被清掉；残缺数据没被提升成正式分片 |
| 5b 残留临时文件 | 不该连累已完成分片被重做；日志说"已清"之后文件要真从盘上消失 |
| 全局 | 所有日志都不该出现 `mv: cannot stat` |

**断言分两类，两类都要有**

- **日志断言**：看脚本打印了什么（`skip` / `redo` / `run` 行、报错文案）。
- **事实断言**：分片 + meta 的**指纹**（名字 / 大小 / 纳秒 mtime 的 md5）、文件是否真的从盘上消失、
  成片帧数。这类**不依赖脚本的自我报告** —— 实测把"删残留"改成"只打日志不删文件"，
  五项日志断言全绿，只有事实断言抓到了。

环境满足不了某条断言的前提时记 `SKIP`（黄字，计入统计），**绝不静默跳过** ——
典型例子：并发用例需要 A 先产出至少一片，否则"未触碰已有分片"的指纹对比会变成拿空对空（假通过）。

测试只用自己 `mktemp` 出来的 `-w` 目录，**不碰默认的 `/workspace/interp_2x`**，
所以可以在正式任务跑着的时候执行（只是会抢一点 GPU）；清理只 kill 自己记录过的 PID，
不做任何按模式的 `pkill`。

---

## 快速上手

```bash
# CPU 并发版（推荐）：居中裁剪，libx264，CRF 21，preset medium
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720

# CPU 并发版：等比填充+裁剪，AV1（libsvtav1），用统一质量基准
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --mode cover --codec svtav1 --crf-ref 21

# 硬件加速版：整目录批量，默认 h264_nvenc + cq 23 + p5，自动选最优路径
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --overwrite

# 硬件加速版：HEVC 硬编，统一基准（cq-ref 26 → hevc_nvenc -cq 28）
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 \
    --codec hevc_nvenc --cq-ref 26

# 按比例裁剪，不必手算尺寸（16:9）
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out --crop-ratio 16:9

# 干跑预览实际命令
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --dry-run

# 光流插帧 2x：脱离会话后台跑，日志落盘（4K 素材约 50 分钟 / 半小时）
setsid bash interp_2x_safe.sh /path/in.mp4 -w /tmp/work \
    > /tmp/work/run.log 2>&1 < /dev/null &

# 先小规模验证：4 秒一片、只跑前 12 秒，产物扔 /tmp
bash interp_2x_safe.sh /path/in.mp4 -w /tmp/demo -L 4 --cap 12

# 只处理前 10 分钟；之后想补全片，把 --cap 去掉用同一个 -w 重跑即可
bash interp_2x_safe.sh /path/in.mp4 /tmp/head.mp4 --cap 600

# 锁与并发安全的回归测试（约 43s，退出码 0/1/2）
bash test_interp_2x_lock.sh
```

---

## 常见场景配方

### 1. 4K → 1080p 批量裁剪（NVIDIA 工作站）

```bash
python vidcrop_hwaccel.py \
    --input ./raw_4k --output ./out_1080p \
    --original-width 3840 --original-height 2160 \
    --output-width 1920 --output-height 1080 \
    --codec hevc_nvenc --cq-ref 20
```

### 2. 等比覆盖裁剪（cover 模式，hwaccel 版 — GPU 解编码 + CPU 滤镜）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --mode cover --codec hevc_nvenc --cq-ref 20 --overwrite
```

### 3. 等比覆盖裁剪（cover 模式，CPU 并发版）

```bash
python vidcrop_cpu_v2.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --mode cover --workers 4
```

### 4. 按比例裁剪（不指定具体尺寸）

```bash
python vidcrop_cpu_v2.py \
    --input ./clips --output ./out \
    --crop-ratio 16:9 --codec libx264 --crf-ref 21
```

### 5. 递归扫描子目录 + 保留目录结构

```bash
python vidcrop_hwaccel.py \
    --input ./footage --output ./out \
    --output-width 1920 --output-height 1080 \
    --recursive --codec hevc_nvenc --cq-ref 22 --overwrite
```

### 6. Linux 服务器 + Intel 核显（VA-API 仅硬解，软件编码）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf-ref 21 --hwaccel vaapi
```

### 7. CI / 容器环境（强制禁用所有硬件加速）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf-ref 21 --hwaccel none
```

### 8. 网页分发（VP9 / WebM）

```bash
# 输出自动为 .webm；若源音轨是 AAC，会自动换成 libopus（WebM 不收 AAC）
python vidcrop_cpu_v2.py \
    --input ./videos --output ./web \
    --output-width 854 --output-height 480 \
    --codec vp9 --crf-ref 24
```

### 9. 长期归档（AV1，极致压缩率）

```bash
# libsvtav1 比 libaom-av1 快一个数量级，是首选
python vidcrop_cpu_v2.py \
    --input ./videos --output ./archive \
    --output-width 1920 --output-height 1080 \
    --codec svtav1 --crf-ref 21
```

### 10. AV1 硬件编码（需 Ada / RTX 40 / L40 及以上）

```bash
# 硬件不支持时自动降级为 libsvtav1 CPU 编码，并打印提示
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 \
    --codec av1_nvenc --cq-ref 21
```

### 11. 音频重编码

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec hevc_nvenc --cq-ref 20 \
    --audio-codec aac --audio-bitrate 192k --overwrite
```

### 12. 手动并发策略（CPU v2，2 任务 × 4 线程）

```bash
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 \
    --workers 2 --threads 4
```

### 13. 自定义 FFmpeg 构建路径 + 诊断硬解故障

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --ffmpeg-bin /opt/ffmpeg-7.0/bin/ffmpeg --cuda-diagnostics
```

### 14. 追加自定义 FFmpeg 参数

```bash
python vidcrop_cpu_v2.py \
    --input video.mp4 --output out.mp4 \
    --output-width 1280 --output-height 720 \
    --extra-args -- -max_muxing_queue_size 4096
```

### 15. 带日志归档的批量任务

```bash
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --recursive --log process.log --overwrite
```

---

## 硬件加速说明

`vidcrop_hwaccel.py` 启动后首先进行一次**运行时探测**（实测输出，Tesla T4 / FFmpeg 6.1）：

```
正在检测硬件加速能力...
  CUDA 解码:  可用 ✓
  h264_nvenc: 可用 ✓
  hevc_nvenc: 可用 ✓
  av1_nvenc:  不可用 ✗
  crop_cuda:  不可用 ✗
  Vulkan:     不可用 ✗
  VA‑API:     不可用 ✗
  OpenCL:     不可用 ✗
  ── 说明 ──
  · av1_nvenc 不可用：AV1 硬编需 8 代 NVENC（Ada / RTX 40 / L40 及以上）；--codec av1_nvenc 会自动降级为 libsvtav1 CPU 编码
  · crop_cuda 不可用：当前 FFmpeg 无此滤镜（6.1 只有 CPU 侧 crop），全 GPU 流水线跳过；仍可走「硬解 + CPU 裁剪 + NVENC 硬编」
```

不可用项会给出**原因说明**，而不是只打一个 ✗。

**`--hwaccel` 参数语义：**

| 值 | 行为 |
|---|---|
| `auto`（默认） | 全面探测，按策略链自动选择 |
| `cuda` | 强制走 CUDA 路径；若 CUDA 组件完全不可用则直接退出 |
| `vulkan` / `vaapi` / `opencl` | 强制使用对应后端做硬解，编码走 CPU |
| `none` | 禁用所有硬件加速，相当于退化为 CPU 版行为 |

**`--fallback-policy` 参数语义：**

| 值 | 行为 |
|---|---|
| `auto`（默认） | 完整 5 级策略链，逐级降级 |
| `strict-cuda` | 无 CUDA 则直接退出，不降级 |
| `nvenc-only` | 只用 NVENC 编码，跳过硬件解码（驱动有缺陷时的兜底） |
| `cpu-only` | 纯 CPU 路径，跳过全部 GPU 探测 |

**5 级策略链（按优先级依次尝试）：**

1. CUDA 全流水线（硬解 + `crop_cuda` + NVENC 硬编）— 仅 crop 模式
2. auto 硬解 + NVENC 硬编（CPU 做 crop）
3. 指定硬解（CUDA / Vulkan / VA-API / OpenCL）+ CPU 软件编码
4. auto 模式下最佳硬解 + CPU 软件编码
5. 纯 CPU 兜底

**策略降级示例：**

> 用户请求 `--codec av1_nvenc --cq-ref 21`，但显卡不支持 AV1 硬编：
> 1. 策略 1 / 2 失败（`has_encoder_av1 = False`）
> 2. 降级目标由编码家族决定：AV1 系 → `libsvtav1`（而非 `libx264`），保持编码家族不变
> 3. `--cq-ref 21` 归一到 libx264 CRF 21，再换算成 `libsvtav1 -crf 27`，并打印提示
> 4. 若硬解也失败，兜底到纯 CPU 处理，整个批次仍能跑完

**10bit 源与 NVENC：** NVENC 的 H.264 编码器只做 8bit。10bit 源 + `h264_nvenc` 时**不会**退回 libx264 去保 10bit，而是降为 8bit 输出以保住硬件加速，并给出提示；如需 10bit，请用 `hevc_nvenc` / `av1_nvenc` 或 CPU 编码器。

---

## 进度显示

进度分两层：**当前任务做到哪儿**与**整批还要多久**，两者同时可见。

### 单任务进度条

```text
  [███████████████████░░░░░]  81.2%  487/600帧  fps=245.5  speed=  13x  已用 2.0s  剩余 0.5s     整批剩余 4.0s
```

| 字段 | 含义 |
|---|---|
| `%` / `帧` | 已编码帧数 ÷ 预探测总帧数；FFmpeg 上报帧数超过预估值时按实际值上修，进度不会提前钉死在 100% |
| `fps` | 已耗时内的平均帧率；部分编码器（如 `libx265`）不通过 `-progress` 上报 fps，退化为帧数 ÷ 耗时 |
| `speed` | FFmpeg 上报的处理倍速（`-progress` 的 `speed`） |
| `已用` | 本任务已耗时 |
| `剩余` / `预计中...` / `收尾中...` | 剩余帧数 ÷ 平均帧率。**还没收到第一帧**（无从算速率）显示 `预计中...`；**帧数已满但 FFmpeg 未退出**（编码器 flush、`-movflags +faststart` 重写 moov）显示 `收尾中...` —— 这两种状态套公式必然得到 0，故用具名文案而不是假的 `0.0s` |
| `整批剩余` | 整批还要多久，仅批量模式（>1 个文件）出现 |

### 整批队列 ETA

| 场景 | 呈现方式 |
|---|---|
| hwaccel（顺序） | 每个文件结束后打印一行队列摘要；同时把 `整批剩余` 挂在该任务进度条尾部，每次刷新都可见 |
| v2 顺序模式（`--sequential`） | 同上，与 hwaccel 行为对齐 |
| v2 并发模式 | 聚合面板首行实时显示 `预计剩余`，与 `完成 / 失败 / 跳过 / 运行中 / 等待 / 已用 / fps` 同帧刷新 |

队列摘要样例：

```text
  队列进度    : [██████████░░░░░░░░░░] 2/4(50.0%)  完成 2  失败 0  跳过 0  累计 6.5s  预计剩余 6.5s
```

估算口径：**吞吐率 = 已处理字节 ÷ 已耗时**，整批剩余 = 剩余文件字节 ÷ 吞吐率。

- 按字节加权，而非「平均单文件耗时 × 剩余个数」——同一批素材分辨率/帧率/画质一致时耗时近似正比于输入体积，长短片混杂也能算准
- 不需要为未处理文件预先 ffprobe，每个输入只多用一次 `stat()`
- 跳过 / 失败的文件从剩余里出列，但不计入吞吐率样本，否则大量跳过会把速率拉低到失真
- 尚无样本（首个文件未跑完、或全部跳过）时显示 `--`，不输出离谱预测；最后一个文件由末尾「汇总」收尾，不再重复打印

---

## AV1 / VP9 编码支持

### 编码器与默认容器

| 用途 | 编码器 | 默认容器 | 质量参数 | 速度参数 |
|---|---|---|---|---|
| AV1（推荐） | `libsvtav1` | `.mp4` | `-crf` | `-preset` **0~13 整数** |
| AV1（参考实现） | `libaom-av1` | `.mp4` | `-crf` | `-cpu-used` |
| AV1 | `librav1e` | `.mp4` | **`-qp`**（无 `-crf`） | — |
| AV1 硬编 | `av1_nvenc`² / `av1_qsv` / `av1_amf` | `.mp4` | `-cq` | `-preset p1~p7` |
| VP9 | `libvpx-vp9` | `.webm` | `-crf` + **`-b:v 0`** | `-cpu-used` / `-deadline` |
| VP8 | `libvpx` | `.webm` | `-crf` + `-b:v 0` | — |

### 脚本已帮你处理的坑

这些坑静态看代码发现不了，脚本已内置处理，不需要你手工加 `--extra-args`：

| 现象 | 处理 |
|---|---|
| `libsvtav1 -preset medium` 直接报 `Unable to parse option value` | 自动把 `medium` / `p5` 等名字映射为 0~13 整数档 |
| `libaom-av1` 默认 `-cpu-used=1` 慢到不可用（实测 1 fps） | 按 CPU 核数自动选档（同素材实测 30.6 s → 4.4 s） |
| `librav1e` 没有 `-crf`，传了被 FFmpeg 静默忽略 | 自动换算为等效 `-qp`（本机实测标定） |
| `libvpx-vp9` 不配 `-b:v 0` 就不是纯 CRF 模式 | 自动追加 `-b:v 0` |
| WebM 容器不收 AAC 音轨，`-c:a copy` 写头失败 | 自动改为 `libopus` 并提示 |
| `av1_nvenc` 在老卡上初始化失败 | 运行时探测，自动降级为 `libsvtav1` |

**AV1 编码器怎么选：** `libsvtav1`（快一个数量级，首选）> `librav1e`（质量优先）> `libaom-av1`（参考实现，末选）。

---

## 质量参数指南

```
CRF / CQ  →  0 = 无损，18 ≈ 视觉无损，23 = 默认，28 = 低码率，51 = 最低质量
```

| 用途 | libx264 CRF | h264_nvenc CQ |
|---|---|---|
| 归档 / 母带 | 16–18 | 21–23 |
| 通用发布 | 19–22 | 24–27 |
| 网页分发 | 23–26 | 28–31 |
| 极限压缩 | 27–30 | 32–35 |

### 两种取值方式（互斥，混用报错退出）

| 方式 | 语义 | 适用 |
|---|---|---|
| `--crf N` / `--cq N` | **字面量原样下发**给目标编码器，不做任何换算 | 你明确知道该编码器的量纲 |
| `--crf-ref N` / `--cq-ref N` | **统一基准轴**：`--crf-ref` 按 libx264 CRF 理解，`--cq-ref` 按 h264_nvenc CQ 理解，再按等效表换算到目标编码器 | 跨编码器批量、希望质量一致 |

```bash
--codec libvpx-vp9 --crf-ref 21   # → -crf 27
--codec hevc_nvenc --cq-ref 26    # → -cq 28
--codec svtav1     --crf-ref 21   # → -crf 27
--codec libx265    --crf-ref 21   # → -crf 24
```

> `-ref` 与 `--crf` / `--cq` 混用（或同时给 `--crf-ref` 与 `--cq-ref`）会**直接拒绝执行**并返回退出码 2，不会静默取其一。

### 等效换算表（`convert_crf.py`）

以 libx264 CRF 为轴，`value = a × x264_crf + b`：

| 编码器 | a | b | 区间 | `--crf-ref 21` 的结果 |
|---|---|---|---|---|
| `libx264` | 1.0 | 0 | 0–51 | 21 |
| `libx265` | 1.0 | 3 | 0–51 | 24 |
| `libvpx-vp9` | 1.98 | −14.46 | 0–63 | 27 |
| `libaom-av1` | 1.0 | 4 | 0–63 | 25 |
| `libsvtav1` | 1.0 | 6 | 0–63 | 27 |
| `librav1e` | 4.0 | −4 | 0–255 | 80（`-qp`） |
| `h264_nvenc` | 1.0 | 5 | 0–51 | 26 |
| `hevc_nvenc` | 1.0 | 7.5 | 0–51 | 28 |
| `av1_nvenc` | 1.0 | 6 | 0–51 | 27 |
| `h264_qsv` / `hevc_qsv` | 1.0 | 3.5 / 4.5 | 1–51 | 24 / 25 |

**降级时的换算**（LLM 常说的"NVENC cq 23 ≈ x264 crf 18"就是这张表算出来的）：

| 场景 | 等效结果 |
|---|---|
| `h264_nvenc cq 23` → `libx264` | `-crf 18` |
| `hevc_nvenc cq 23` → `libx265` | `-crf 18` |
| `av1_nvenc cq 24` → `libsvtav1` | `-crf 24` |

> 注：旧版 README 里的 `crf = cq + 4 / +1` 是**方向相反**的硬编码偏移，已被本表取代。

**关键规则：**

- CPU 编码器用 `--crf`，GPU 编码器（NVENC / AMF / QSV）用 `--cq`
- 字面量模式下不做换算；只有"给的是 `--cq` 但落到 CPU 软编"时才按等效表换算
- 同时指定 `--crf` 和 `--cq` 时，按实际落用的编码器自动选用对应参数

---

## 已知限制

| 限制 | 说明 | 规避 |
|---|---|---|
| 无 VP9 硬件编码 | FFmpeg 从未提供 `vp9_nvenc`；VP9 硬编只有 `vp9_qsv` / VA-API | VP9 走 CPU 编码（可配硬解） |
| `av1_nvenc` 需新卡 | 第 8 代 NVENC（Ada / RTX 40 / L40+）才有；Turing / Ampere 没有 | 自动降级 `libsvtav1` |
| `crop_cuda` 缺失 | FFmpeg 6.1 未编译该滤镜，全 GPU 流水线（策略 1）始终跳过 | 仍走"硬解 + CPU 裁剪 + NVENC 硬编"；代价是**吞吐对 CPU 敏感**，见 [FAQ Q13](#常见问题faq) |
| `librav1e` 无 `-crf` | 编码器本身只支持 `-qp` | 脚本自动换算（实测标定） |
| `h264_nvenc` 无 10bit | NVENC H.264 只做 8bit | 自动降 8bit 保硬件；需 10bit 用 `hevc_nvenc` / `av1_nvenc` |
| 脚本依赖 `convert_crf.py` | 两个裁剪脚本运行时 import 同目录该文件 | 拷贝时一并带上 |
| `interp_2x_safe.sh` 只做 **2 倍** | `fps=source_fps*2` 与片头去重帧数都按 2x 写死 | 要 1.25x / 2.5x 需改源码里这两处 |
| 光流插帧需要**自建 FFmpeg** | 发行版官方包不含 `nvinterpolate`；且要求 Turing（CC 7.5）或更新、驱动 ≥ 525 | 用带该滤镜的 ffmpeg；显卡不满足只能退到 `minterpolate`（慢很多、质量更差） |
| 4K 2x 很慢 | 实测约 **0.6× 实时**（半小时素材约 50 分钟），且会与其它 GPU 任务争用 | 用 `setsid` 起 + 分片；`-L` 调小以缩小单次损失 |
| 每个分片开头重复约 3 帧 | 光流拿不到"前一帧"，每个新起的滤镜实例都要预热 | 脚本已用 `trim=start_frame=3,setpts=PTS-STARTPTS` 裁掉；**不要**再手工加 ±offset 补偿 |
| 同一个 `-w` 只允许一个实例 | `flock` 单实例锁会立刻拒绝第二个实例 | 换 `-w`；或等前一个结束再原样重跑（已完成分片会自动跳过） |
| `nvinterpolate` 会往 CWD 写日志 | 滤镜在**当前工作目录**下创建 `NvOFFRUC/logFRUCError.txt`（在哪个目录里跑就落在哪个目录） | 已加入 `.gitignore`；`test_interp_2x_lock.sh` 会先 `cd` 到自己的临时目录再跑。手工跑正式任务时建议也在专门目录里起 |

---

## 路线图（Roadmap）

VidUtils 规划作为一个**命令行优先 / Python 原生**的视频工程工具集，当前与后续模块：

| 模块 | 状态 | 说明 |
|---|---|---|
| `vidcrop_cpu_v0.py` | ✅ 已发布 | CPU 顺序裁剪（crop + cover） |
| `vidcrop_cpu_v1.py` | ✅ 已发布 | CPU 并发裁剪（crop + cover，自动并行） |
| `vidcrop_cpu_v2.py` | ✅ 已发布 | CPU 并发裁剪增强版（AV1/VP9、别名、preset 映射、`-ref` 基准、crop-ratio、color-range） |
| `vidcrop_hwaccel.py` | ✅ 已发布 | 硬件加速裁剪（CUDA/Vulkan/VA-API/OpenCL，5 级策略链） |
| `convert_crf.py` | ✅ 已发布 | 质量换算单一事实来源 |
| `interp_2x_safe.sh` | ✅ 已发布 | 光流插帧 2x（NVOFA / `nvinterpolate`；`setsid` + TS 分片 + 断点恢复 + 单实例锁） |
| `test_interp_2x_lock.sh` | ✅ 已发布 | `interp_2x_safe.sh` 的回归测试（单实例锁 / 并发安全，退出码 0/1/2） |
| `vidscale_*.py` | 🚧 规划中 | 视频缩放：双三次 / Lanczos / `scale_cuda` / `scale_npp` |
| `vidrepair_*.py` | 🚧 规划中 | 视频修复：容器修复、损坏帧跳过、时间戳重建、丢帧补偿 |
| `videnhance_*.py` | 🚧 规划中 | 视频增强：去噪、锐化、去隔行、HDR→SDR、AI 超分接入 |
| `vidutils-cli` | 💭 设想中 | 统一 CLI 入口，子命令分发 `vidutils crop / scale / repair / enhance …` |

**统一设计约束（所有后续模块都会遵守）：**

- 参数命名风格一致（`--input / --output / --overwrite / --hwaccel / --ffmpeg-bin`）
- 批量语义一致（文件或目录均可作为 `--input`）
- 失败诊断一致（打印命令 + FFmpeg stderr 末 N 行）
- 进度与统计一致（实时进度条 + 文件级耗时 + 批量汇总 + 整批剩余 ETA）
- 质量换算统一走 `convert_crf.py`，不在各脚本里硬编码偏移

> **已知例外**：`interp_2x_safe.sh` 早于这套约定，输入/输出走**位置参数**（`<输入视频> [输出路径]`）
> 而不是 `--input / --output`；与位置参数无关的其余约定它都遵守（有 `--overwrite`、失败时给出
> 可诊断信息、失败非 0 退出）。将来若要并入 `vidutils-cli`，需要先统一它的参数风格。

---

## 目录结构

```
vidutils/
├── README.md                 # 本文件
├── vidcrop_cpu_v0.py         # CPU 顺序裁剪（crop + cover，单进程）
├── vidcrop_cpu_v1.py         # CPU 并发裁剪（crop + cover，多任务并行）
├── vidcrop_cpu_v2.py         # CPU 并发裁剪增强版（推荐；AV1/VP9、别名、preset 映射、-ref 基准）
├── vidcrop_hwaccel.py        # 硬件加速裁剪（CUDA/Vulkan/VA-API/OpenCL，5 级策略链）
├── convert_crf.py            # 质量换算表（被 v2 / hwaccel 依赖，单一事实来源）
├── interp_2x_safe.sh         # 光流插帧 2x（NVOFA/nvinterpolate；setsid + TS 分片 + 断点恢复）
├── test_interp_2x_lock.sh    # 上面这个脚本的回归测试（单实例锁 / 并发安全）
├── memory/                   # 工程记忆：工具背后的事实与踩坑，索引见 memory/MEMORY.md
├── AV1_VP9_UPGRADE_PLAN_v2.md # AV1/VP9 升级方案归档
├── docs/                     # （规划）设计文档与性能基准
├── examples/                 # （规划）示例素材与演示脚本
└── tests/                    # （规划）单元测试与端到端测试；test_interp_2x_lock.sh 暂放根目录
```

---

## 工程记忆（memory/）

`memory/` 放的是**工具背后的事实与踩坑**，不是 API 文档 —— 用法看本文件，为什么这么做、踩过什么坑看那里。
索引在 [`memory/MEMORY.md`](memory/MEMORY.md)。当前条目：

- [长时 ffmpeg 任务必须 setsid 分离 + 别直接写 MP4](memory/project_long_ffmpeg_jobs.md)
  —— 宿主崩溃会带走同进程组的 ffmpeg；MP4 缺 moov 整份作废（已发生过一次，34 分钟算力白跑）；
  并发实例互删临时文件；以及 `interp_2x_safe.sh` 里那套防呆（锁 / 两层复用校验 / PID 临时名）
- [FFmpeg 7.1 已合并 nvinterpolate 与 libvmaf](memory/project_nvinterpolate_build.md)
  —— 单一 ffmpeg、无需环境文件；`nvinterpolate` 必须放滤镜链末尾否则段错误；移植补丁位置
- [ffmpeg 挂起的两个根因](memory/project_ffmpeg_stdin_hang.md)
  —— SIGTTIN（状态 T，`-nostdin` 能修）vs 输出管道反压（状态 S 且 CPU 冻结，`-nostdin` 没用）
- [T4 能力边界 + 测性能前先查并发流水线](memory/project_t4_gpu_capabilities.md)
  —— 别的流水线会抢 CPU/GPU 导致基准不可信；T4 无 AV1 编码器；零拷贝管线里 `-pix_fmt` 无效

写法沿用本机 codebuddy 自动记忆的约定：frontmatter 带 `name` / `description` / `type`，
正文对 project / feedback 类用「事实 → **Why:** → **How to apply:**」的结构，
方便日后判断这条记忆是不是还成立。

---

## 常见问题（FAQ）

**Q1：v0、v1、v2 和 hwaccel 如何选择？**

无 GPU 或批量 CPU 处理 → **v2**（功能最全，v1 的超集）；有 NVIDIA / AMD / Intel GPU、追求吞吐 → **hwaccel**；要对照历史行为 → v0 / v1。v2 与 hwaccel 功能基本对齐（cover、递归、音频重编码、dry-run、日志、extra-args、`-ref` 基准均支持），区别在是否有硬件解码与策略链。

**Q2：hwaccel 版的 cover 模式与 crop 模式有什么区别？**

cover 模式使用 `scale,crop` 组合滤镜，该滤镜在 CPU 侧执行（`crop_cuda` 无法替代 `scale`），GPU 仍负责解码与编码。全 GPU 流水线（策略 1，`crop_cuda`）仅对 crop 模式启用，cover 模式从策略 2 开始尝试。

**Q3：为什么硬件加速版探测阶段要真的跑一遍 FFmpeg？**

因为 `ffmpeg -encoders` 只反映编译期选项，**不代表运行时可用**。典型例子：`av1_nvenc` 在任何 FFmpeg 6.1 里都会被 `ffmpeg -encoders` 列出来，但在 Tesla T4（第 7 代 NVENC）上初始化必然失败。VidUtils 选择真的跑一次探针，并对不可用项打印原因说明。

**Q4：为什么降级时要做 CQ → CRF 换算？**

不同编码器的质量值量纲不同：同样写 23，`h264_nvenc` 大约相当于 `libx264` 的 18。直接透传会产出与预期明显不同的体积/质量。降级时按 `convert_crf.py` 的等效表换算，更接近用户的原始意图。

**Q5：`--crf-ref` 和 `--crf` 有什么区别？**

`--crf` 是字面量，你写 27 就给 `libvpx-vp9 -crf 27`；`--crf-ref 21` 是说"我要 libx264 CRF 21 那个档次的画质"，脚本换算成 `-crf 27`。跨编码器批量时用 `-ref` 能保证各编码器的观感一致。两者互斥。

**Q6：AV1 该选哪个编码器？**

`libsvtav1`。它比 `libaom-av1` 快一个数量级（同素材实测 35 fps vs 1 fps），且脚本会按 CPU 核数自动选速度档。`libaom-av1` 仅作参考实现，`librav1e` 质量优先但生态较弱。

**Q7：为什么输出是 `.webm`？**

`libvpx-vp9` / `libvpx` 的默认容器是 WebM。可用 `--container .mkv` 改成 Matroska。注意 WebM 只接受 Vorbis / Opus 音轨，脚本遇到 AAC 会自动换成 `libopus`。

**Q8：输出路径是文件还是目录？**

规则：**输入是单文件且输出带扩展名 → 当作文件；否则当作目录**。批量模式下若输出误带扩展名会给出警告并自动去除扩展名当作目录。递归模式（`-r`）下输出目录结构与输入保持一致。

**Q9：v2 的并发任务数怎么确定？**

读取 CPU 配额（cgroup v1/v2 / `sched_getaffinity` / `os.cpu_count` 按优先级依次尝试）和内存，结合编码器画像中的"推荐线程数"和"单任务内存估算"，同时满足 CPU 槽数限制和内存预算，取较小值。可用 `--workers` / `--threads` / `--mem-per-job` 手动覆盖。

**Q10：ProRes / AV1 编码非常慢怎么办？**

这是编码器本身的特性。AV1 建议用 `libsvtav1`（而非 `libaom-av1`）；hwaccel 版可配 NVENC 换吞吐；v2 用 `--workers` 控制并发避免内存溢出。

**Q11：源文件损坏怎么办？**

各脚本都启用了 `-err_detect ignore_err` 与 `-fflags +genpts+discardcorrupt`，损坏帧会被跳过并在日志中提示。完全无法解码的文件会被判失败，不会阻塞后续批次。

**Q12：为什么脚本会卡在"正在检测硬件加速能力..."？**

旧版本有此问题：FFmpeg 会继承 stdin 并阻塞读，Python 的 `timeout` 也救不回来。当前版本已对所有 FFmpeg 调用加 `-nostdin` 与 `stdin=DEVNULL`，探测耗时约 5 秒。若仍卡住请提交 Issue 并附 `ffmpeg -version`。

**Q13：同样的命令，这次比上次慢了一倍？**

先看 ffmpeg 自己上报的 `speed=`：它由 ffmpeg 按自身吞吐算出，Python 侧的打印开销改不了它。`speed` 真的掉了，基本是**资源争用**（见 [进度显示](#进度显示) 里那行样例的任务字段）：

```bash
uptime                                                              # load 是否远超核数
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv   # 是否有其它 NVENC 会话
```

本环境（`crop_cuda` 不可用）的实际流水线是 `CUDA 硬解 → hwdownload → CPU 侧 crop → 回传显存 → NVENC 硬编`，CPU 段在关键路径上；NVENC 跑到 1300+ fps 时每帧只摊到约 0.7 ms CPU 预算。8 核机器上任何一个吃满 CPU 的并发作业（典型：算 SSIM/PSNR 的 `-lavfi ssim` 质量对比、另一个转码任务）就会把它从约 47x 拖到约 20x。

实测对照（同一文件、同一条命令、`vidcrop_hwaccel.py`）：并发 SSIM 作业运行时 **1m11s（610 fps / 20.5x）**，该作业结束后同样命令 **32s（1378 fps / 46.7x）**。所以排查顺序是：先看 `speed`，再查并发作业，最后才怀疑脚本；批量任务与质量对比任务请错开运行。

---

## 贡献指南

欢迎以 Issue / PR 的形式参与：

1. **提 Bug**：请附上 FFmpeg 版本（`ffmpeg -version`）、操作系统、完整命令行与 stderr 末若干行。
2. **提新功能**：优先对齐[路线图](#路线图roadmap)中已规划的模块；新模块请先发 Issue 讨论接口设计，以保持参数风格统一。
3. **代码风格**：遵循 PEP 8，函数级中文 docstring，关键分支要有 inline 注释说明"为什么这样写"而不仅是"在做什么"。
4. **测试**：对新增参数分支至少覆盖 happy path + 一个边界 / 失败 case。
5. **质量换算**：新增编码器请只改 `convert_crf.py` 的 `QUALITY_MAP`，不要在脚本里硬编码偏移。

---

## 许可证

本项目采用 **MIT License**（或根据实际情况替换为 Apache-2.0 / GPLv3）。
详见仓库根目录 `LICENSE` 文件。

---

> **VidUtils** — *Pragmatic video tooling, powered by FFmpeg.*
