# VidUtils · 视频实用工具集

> 一组基于 **FFmpeg** 的命令行视频处理工具，聚焦 *批量、可复现、生产可用* 的视频工程任务。
> 当前提供居中裁剪（CPU 顺序版 / CPU 并发版 / CPU 并发增强版 / 硬件加速版四个变体），后续将逐步扩展缩放、修复、增强等能力。

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
- [快速上手](#快速上手)
- [常见场景配方](#常见场景配方)
- [硬件加速说明](#硬件加速说明)
- [AV1 / VP9 编码支持](#av1--vp9-编码支持)
- [质量参数指南](#质量参数指南)
- [已知限制](#已知限制)
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
| `crop_cuda` 缺失 | FFmpeg 6.1 未编译该滤镜，全 GPU 流水线（策略 1）始终跳过 | 仍走"硬解 + CPU 裁剪 + NVENC 硬编" |
| `librav1e` 无 `-crf` | 编码器本身只支持 `-qp` | 脚本自动换算（实测标定） |
| `h264_nvenc` 无 10bit | NVENC H.264 只做 8bit | 自动降 8bit 保硬件；需 10bit 用 `hevc_nvenc` / `av1_nvenc` |
| 脚本依赖 `convert_crf.py` | 两个裁剪脚本运行时 import 同目录该文件 | 拷贝时一并带上 |

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
| `vidscale_*.py` | 🚧 规划中 | 视频缩放：双三次 / Lanczos / `scale_cuda` / `scale_npp` |
| `vidrepair_*.py` | 🚧 规划中 | 视频修复：容器修复、损坏帧跳过、时间戳重建、丢帧补偿 |
| `videnhance_*.py` | 🚧 规划中 | 视频增强：去噪、锐化、去隔行、HDR→SDR、AI 超分接入 |
| `vidutils-cli` | 💭 设想中 | 统一 CLI 入口，子命令分发 `vidutils crop / scale / repair / enhance …` |

**统一设计约束（所有后续模块都会遵守）：**

- 参数命名风格一致（`--input / --output / --overwrite / --hwaccel / --ffmpeg-bin`）
- 批量语义一致（文件或目录均可作为 `--input`）
- 失败诊断一致（打印命令 + FFmpeg stderr 末 N 行）
- 进度与统计一致（实时进度条 + 文件级耗时 + 批量汇总）
- 质量换算统一走 `convert_crf.py`，不在各脚本里硬编码偏移

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
├── AV1_VP9_UPGRADE_PLAN_v2.md # AV1/VP9 升级方案归档
├── docs/                     # （规划）设计文档与性能基准
├── examples/                 # （规划）示例素材与演示脚本
└── tests/                    # （规划）单元测试与端到端测试
```

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
