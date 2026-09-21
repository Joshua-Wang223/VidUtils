# VidUtils · 视频实用工具集

> 一组基于 **FFmpeg** 的命令行视频处理工具，聚焦 *批量、可复现、生产可用* 的视频工程任务。
> 当前提供居中裁剪（CPU 顺序版 / CPU 并发版 / CPU 并发增强版 / 硬件加速版四个变体），
> 光流插帧 2x 工具（`interp_2x_safe.sh` GPU 专版 / `interp_2x_safe_v1.sh` 通用版，后者多一条 CPU 回退后端；含配套回归测试），
> 以及 `ls` / `ll` 替代品 **`vidls`**（列目录时顺带显示视频的分辨率 / 帧率 / 比特率 / 编码器 / 容器 / 时长，
> 帧数用 `--show frames` 另开；
> 另有 **`vidll`** = `vidls -l` 的快捷方式。Linux 版 `vidls.sh` / `vidll.sh` + `vidls.py`，
> Windows 版 `vidls.cmd` / `vidll.cmd` + `vidls_win.py`）；
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
  - [vidls.sh — ls / ll 替代 + 视频属性探测](#vidlssh--ls--ll--替代--视频属性探测)
  - [Windows 版 vidls — ls / ll 替代（Windows）](#windows-版vidlscmd--vidls_winpy)
  - [vidll — vidls -l 的快捷方式（Linux + Windows）](#vidll--vidls--l-的快捷方式linux--windows)
  - [test/test_interp_2x_lock.sh — 回归测试（并发与锁）](#testtest_interp_2x_locksh--回归测试并发与锁)
  - [test/test_interp_2x_orphan.sh — 回归测试（中断收尾与孤儿 ffmpeg）](#testtest_interp_2x_orphansh--回归测试中断收尾与孤儿-ffmpeg)
- [快速上手](#快速上手)
- [常见场景配方](#常见场景配方)
- [硬件加速说明](#硬件加速说明)
- [进度显示](#进度显示)
- [AV1 / VP9 编码支持](#av1--vp9-编码支持)
- [质量参数指南](#质量参数指南)
- [已知限制](#已知限制)
- [路线图（Roadmap）](#路线图roadmap)
- [目录结构](#目录结构)
- [回归与验证](#回归与验证)
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
| 先裁剪后缩放覆盖（crop-cover 模式） | ❌ | ❌ | ✅ | ✅¹ |
| 单文件 / 目录批量 | ✅ | ✅ | ✅ | ✅ |
| 递归扫描目录（`-r`） | ✅ | ✅ | ✅ | ✅ |
| `--crop-ratio` 按比例自动算裁剪尺寸 | ❌ | ❌ | ✅ | ✅ |
| `--flag` 自定义输出名后缀 | ❌ | ❌ | ✅ | ✅ |
| `--color-range` 值域控制（含真转换） | ❌ | ❌ | ✅ | ✅⁵ |
| `--pix-fmt` 输出像素格式 | ✅ | ❌ | ✅ | ✅⁶ |
| `--bit-depth` 输出位深（8/10/12） | ❌ | ❌ | ✅ | ✅ |
| `--hdr` HDR 处理（含 HDR→SDR） | ❌ | ❌ | ✅ | ✅⁷ |
| CPU 软件编码器（libx264/265、VP9、AV1、ProRes、MJPEG…） | ✅ | ✅ | ✅ | ✅ |
| AV1：`libsvtav1` / `libaom-av1` / `librav1e` | ❌ / ✅ / ✅ | ❌ / ✅ / ✅ | ✅ | ✅ |
| VP9：`libvpx-vp9` | ✅ | ✅ | ✅ | ✅ |
| NVIDIA NVENC（h264 / hevc / av1²） | ❌ | ❌ | ✅³ | ✅ |
| AMD AMF / Intel QSV（含 av1） | ❌ | ❌ | ✅³ | ✅³ |
| 硬件解码（CUDA / Vulkan / VA-API / OpenCL） | ❌ | ❌ | ❌ | ✅ |
| `crop_cuda` 全 GPU 流水线 | ❌ | ❌ | ❌ | ✅¹ |
| `scale_cuda` 显存内缩放（仅 cover，策略 2） | ❌ | ❌ | ❌ | ✅¹ |
| 软解 + 显存内缩放（`hwupload_cuda` 链） | ❌ | ❌ | ❌ | ✅⁴ |
| 解码 / 缩放 / 编码三轴独立组合 | ❌ | ❌ | ❌ | ✅ |
| `--scale-algo` 选缩放算法 | ❌ | ❌ | ✅（仅 libswscale） | ✅（含 `cuda-*`） |
| 运行时硬件能力探测 | — | — | — | ✅ |
| 策略链自动降级（6 级） | — | — | — | ✅ |
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

> ¹ FFmpeg 上游**并没有 `crop_cuda`** 这个滤镜（与编译选项无关：实测 `-filters` 列表里没有它、`-h filter=crop_cuda` 报 `Unknown filter`），所以「全 GPU 流水线」在真实环境里**永远跳过**。cover 模式在带 `scale_cuda` 的构建（需 `--enable-cuda-nvcc` 的自建 FFmpeg）上把**缩放**放进显存、裁剪仍回 CPU（`scale_cuda → 显式 hwdownload → CPU crop → NVENC`），实测 4K→1440×1080 覆盖链**快 51.9%~52.9%**（对着旧 bicubic 基准是 44.5%）；质量门已完整通过（`PSNR(GPU lanczos vs CPU lanczos) = 46.60 dB ≥ 40`、`VMAF = 97.21`）。`crop-cover` 保留 CPU 侧 `crop,scale`（它必须先裁剪，GPU 缩放要额外 `hwupload`，未实测）。
> ² `av1_nvenc` 需第 8 代 NVENC（Ada / RTX 40 / L40 及以上），否则自动降级为 `libsvtav1`。
> ³ v2 可以**使用**硬件编码器（写 `--codec h264_nvenc` 等），但不做硬件探测、也不做硬件解码——解码全程走 CPU。是否可用由 FFmpeg 与驱动自行决定。
> ⁴ 软解 + 显存内缩放 = `--decode cpu --scale-algo cuda-*`，链为 `hwupload_cuda → scale_cuda → 显式 hwdownload → CPU crop`。**已实测（T4，两轮一致）：比「软解 + CPU 缩放」快 2.9%~3.2%**（45.9s→44.6s / 46.0s→44.6s），画质与零拷贝链逐位相同（PSNR 同为 46.603743 dB）。
> 但要看清**绝对值**：软解链路整体是 44~46s，而硬解零拷贝只要 12.8s —— 上传链再快也只是「NVDEC 用不了 / 解不了该编码时的出路」，**不是**用来替代硬解的。`--scale-algo auto` 只在显式 `--decode cpu` 下才会自动走它，且会先跑功能探针。
> ⁵ hwaccel 的 `--color-range` **不完全等同 v2**：链首是 CUDA 原生滤镜时会跳过值域转换并告警（只写标签）。见下文参数表与已知限制。
> ⁶ hwaccel 此前没有 `--pix-fmt`，现已补齐；两个脚本的取值与校验一致。
> ⁷ `--hdr sdr` 的 tone mapping 走 CPU（`zscale` + `tonemap`）；`tonemap_cuda` 上游不存在，CUDA 链上没有硬件 tone mapping。

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

- **`interp_2x_safe.sh`（GPU 专版）额外需要**（光流插帧，条件比上面严）：
  - FFmpeg 编译时带 `nvinterpolate` 滤镜 —— **发行版官方包一般没有**，需自行构建（本机 `/usr/local/bin/ffmpeg` 7.1 已含）；还需要 `hevc_nvenc` 编码器；NVIDIA GPU 为 **Turing（CC 7.5）或更新**，驱动 ≥ 525（光流跑在 NVOFA 硬件上，不是 NVENC/NVDEC）
  - Bash ≥ 4.4 与 `flock`（util-linux）—— 单实例锁用它；`ps` / `df` / `nproc` 用于环境探测（缺了会优雅降级）
  - 没有 CPU 回退。要 CPU 回退（`minterpolate` + `libx265`）请用 `interp_2x_safe_v1.sh`

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

### 把 `vidls` 装成一条命令（可选）

`vidls` 是 `ls` / `ll` 的替代品，用法见 [工具一览](#vidlssh--ls--ll-替代--视频属性探测)。
它自带安装器，会自检环境、按要求补依赖，再把命令接入 `PATH`：

```bash
chmod +x vidls.sh
./vidls.sh --install          # 等价 -I；加 -y/--yes 可免交互
```

安装器做四件事：① 检查 Python ≥ 3.8；② 检查 `ffmpeg` / `ffprobe`，缺失时按检测到的包管理器
（`apt-get` / `dnf` / `yum` / `apk` / `brew`）**询问后**安装；③ 探测 GPU 与 CUDA 硬解，
打印帧数会走哪一档；④ 给 `vidls.sh` 加可执行权限，并在 `/usr/local/bin`（无权限时退回
`~/.local/bin`）建名为 `vidls` 与 `vidll` 的**两条**软链；若该目录不在 `PATH` 里，会往 shell 配置文件追加一行
`export PATH=...`。**不改动 `ls` / `ll` 本身。**

想装到别处：`./vidls.sh --install --prefix ~/bin`。

> 软链用的是**绝对路径**，仓库目录搬家后需要重跑一次 `--install`。
> 卸载：`rm /usr/local/bin/vidls`（再删掉 rc 文件里那行 PATH 即可）。

**Windows** 用另一个入口（细节见 [Windows 版](#windows-版vidlscmd--vidls_winpy)）：

```bat
vidls.cmd --install       :: 在仓库里这样敲；装好之后从任何目录都是 vidls --install
                          :: 等价 -I；加 -y 可免交互
```

同样做四件事，但第 ② 步用 Windows 的包管理器（`winget` → `choco` → `scoop` 依次探测），
第 ④ 步**不建软链**（要管理员 / 开发者模式），改成在已在 PATH 的目录
（默认 `%USERPROFILE%\.local\bin`）写四个启动器（`vidls` / `vidll` 各两个）。

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
| `--scale-algo` | 裸 `lanczos` | 缩放算法。本脚本是纯 CPU 路径：写 `libswscale-<algo>` 或**裸 `<algo>`（前缀可省）**——`fast_bilinear` `bilinear` `bicubic` `neighbor` `area` `bicublin` `gauss` `sinc` `lanczos` `spline`。默认 `lanczos`（不吃 libswscale 的默认 `bicubic`）。`cuda-*` 请用 hwaccel 版（本脚本会直接报错） |
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
| `--output-width` / `--output-height` | 与 `--crop-ratio` 二选一 | 目标视频宽 / 高；`crop-cover` 模式下配合 `--crop-ratio` 时可只给一个维度（另一个按比例推导，取偶数） |
| `--crop-ratio` | 无 | 目标宽高比（`16:9` / `4:3` / 浮点 `1.777`），自动算最大化裁剪尺寸；`crop-cover` 模式下与 `--output-width/height` 并用（前者定裁剪比例、后者定最终尺寸） |
| `--mode` | `crop` | `crop` / `cover` / `crop-cover`（`crop-cover`=先按 `--crop-ratio`（未给出时即目标宽高比）最大化裁剪，再缩放覆盖到最终尺寸） |
| `--codec` | `libx264` | 视频编码器；支持别名（`vp9`→`libvpx-vp9`、`av1`→`libaom-av1`、`svtav1`→`libsvtav1`、`rav1e`→`librav1e` …）；`auto` 等同 `libx264`（本脚本为纯 CPU 路径，与硬件版的 `auto` 在无 NVENC 时解析结果一致） |
| `--crf` | `21` | CPU 编码器质量（0–51）；**字面量原样下发，不换算** |
| `--cq` | `23` | GPU 编码器质量（0–51）；落到 CPU 软编时按等效表换算为 CRF |
| `--crf-ref` | 无 | 以 **libx264 CRF** 为基准给出质量，按等效表换算到目标编码器；与 `--crf`/`--cq` 互斥 |
| `--cq-ref` | 无 | 以 **h264_nvenc CQ** 为基准给出质量，按等效表换算；与 `--crf`/`--cq` 互斥 |
| `--preset` | CPU `medium` / GPU `p5` | 支持 x264 风格与 NVENC `p1~p7`，自动双向映射；`libsvtav1` 自动转 0~13 整数档 |
| `--pix-fmt` | `auto` | 输出像素格式（`yuv420p` / `yuv420p10le` / `p010le` / `yuv422p10le` …）；`auto`=继承源位深，`none`=不下发。显式给出时会校验该名字是否被 ffmpeg 认识 |
| `--bit-depth` | `auto` | 目标位深 `8` / `10` / `12`；`auto`=继承源。按编码器选格式（如 `libx265` 的 10bit→`yuv420p10le`、`hevc_nvenc`→`p010le`）。与 `--pix-fmt` 语义重叠：**同时给出时以 `--pix-fmt` 为准**并提示；只关心位深时建议只用本参数（格式名要跟着编码器走，位深不用） |
| `--hdr` | `auto` | HDR 处理：`auto` / `keep`=尽力保留 HDR10 静态元数据；`drop`=不写元数据、**色彩标签按 SDR(bt709) 写，像素不动**；`sdr`=真的做 HDR→SDR tone mapping（可带算法 `sdr:hable` / `sdr:reinhard` …，默认 `mobius`） |
| `--color-range` | `auto` | `tv` / `pc` 强制值域，且与源不同时会插入 scale 滤镜做**真值域转换** |
| `--flag` | `_cropped` / `_covered` / `_cropcovered` | 自定义输出名后缀（仅对自动生成的输出名生效） |
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
| `--output-width` / `--output-height` | 与 `--crop-ratio` 二选一 | 目标视频宽 / 高；`crop-cover` 模式下配合 `--crop-ratio` 时可只给一个维度（另一个按比例推导，取偶数） |
| `--crop-ratio` | 无 | 目标宽高比，自动算最大化裁剪尺寸；`crop-cover` 模式下与 `--output-width/height` 并用（前者定裁剪比例、后者定最终尺寸） |
| `--mode` | `crop` | `crop` / `cover` / `crop-cover`（`crop-cover`=先按 `--crop-ratio`（未给出时即目标宽高比）最大化裁剪，再缩放覆盖到最终尺寸）（注¹） |
| `--scale-algo` | 裸 `lanczos` | 缩放算法，写法 `<backend>-<algo>` 或裸 `<algo>`（后端自动）。`libswscale-*`：同 v2 的那 10 个；`cuda-*`：`nearest` `bilinear` `bicubic` `lanczos`（**仅 cover 模式**，走显存内缩放，需自建 FFmpeg）。前缀用于**强制**后端；裸名字要求两表都认（只在一个后端有的必须带前缀，如 `libswscale-spline`）。降级与冲突处理见[硬件加速说明](#硬件加速说明) |
| `--original-width/height` | 自动检测 | 手动指定源尺寸，跳过 ffprobe |
| `--codec` | **`h264_nvenc`** | 支持 `auto`；无 NVENC 时自动降级为 **`libx264`** |
| `--cq` | **`23`** | GPU 编码器质量（0–51）；字面量原样下发 |
| `--crf` | **`21`** | CPU 编码器质量（0–51）；字面量原样下发 |
| `--crf-ref` / `--cq-ref` | 无 | 统一质量基准（同上），与 `--crf`/`--cq` **互斥，混用直接报错退出** |
| `--preset` | GPU `p5` / CPU `medium` | NVENC（p1~p7）↔ libx264 风格双向映射；`libsvtav1` 自动转整数档。降级到 CPU 编码器时按**请求的编码器**换算，档位保持等效（`h264_nvenc` 的 p5 → `libx264` 的 medium、`av1_nvenc` 的 p5 → `libsvtav1` 的 8），概览块显示的就是实际下发的值 |
| `--pix-fmt` | `auto` | 输出像素格式；`auto`=继承源位深、`none`=不下发，具体名会校验。**零拷贝 CUDA 链不能传 `-pix_fmt`**（实测 `Impossible to convert`）→ 那条链上改用 `scale_cuda=format=` 并自动配 `-profile:v`；`scale_cuda` 仅支持 `nv12` / `yuv420p` / `yuv444p` / `p010le`，链上不可用且给了 `--bit-depth` 时**由它接管**（明说让位代价），否则按 `--fallback-policy` 处理。与 `--bit-depth` 语义重叠：**需要特定色度/排布（4:4:4 / 4:2:2）时用它**，只关心位深请改用 `--bit-depth` |
| `--bit-depth` | `auto` | 目标位深 `8` / `10` / `12`；`auto`=继承源。与 `--pix-fmt` 语义重叠：**优先按 `--pix-fmt` 落地**，它在当前链上不可用（零拷贝 CUDA 链只收 4 种格式）时**由本参数接管**并明说让位代价（位深/色度变化，`strict` 下有损失即报错）；只关心位深时建议只用本参数。`h264_nvenc` 只支持 8bit，要求 10bit+ 会告警降 8bit（`strict` 下报错） |
| `--hdr` | `auto` | 同 v2 的四种取值。⚠ `tonemap_cuda` 上游不存在，所以 CUDA 链上没有硬件 tone mapping——会先下载成软件帧再做（cover 链本来就在 crop 前 `hwdownload`） |
| `--color-range` | `auto` | **不完全同 v2**：链首是 CUDA 原生滤镜（`scale_cuda` 等）时会跳过值域转换并告警，只写标签（见已知限制） |
| `--flag` | `_cropped` / `_covered` | 同 v2 |
| `--audio-codec` / `--audio-bitrate` | `copy` / `128k` | 音频编码；WebM 下自动换 `libopus` |
| `--no-skip-same-size` | 否 | 同尺寸强制转码 |
| `--decode` | `auto` | 解码后端：`auto` / `cuda` / `vulkan` / `vaapi` / `opencl` / `cpu`（旧值 `none` ≡ `cpu`）。**只管解码**（见[硬件加速说明](#硬件加速说明)）；`auto` 的探测**按源编解码器**做（NVDEC 的能力是分编解码器的，T4 解不了 AV1）——拿真实输入试解 1 帧，结果按 codec 缓存；旧名 `--hwaccel` 已**硬更名**，用旧名直接报错退出 2 |
| `--fallback-policy` | `auto` | 显式点名的后端不可用/失败时：`auto`=降级并提示 / `strict`=报错退出 2。旧值 `strict-cuda` / `nvenc-only` / `cpu-only` **已删除**，用旧值报错并给出等价三轴写法 |
| `--cuda-diagnostics` | 否 | 输出多分辨率探针与详细错误，定位"硬解不可用"根因 |
| `--cuda-device-id` | `0` | 多 GPU 环境下选择解码设备 |
| `--overwrite` / `--container` | — | 覆盖 / 容器扩展名 |
| `--ffmpeg-bin` | `ffmpeg` | 自定义 FFmpeg 路径；ffprobe 自动从同目录推导 |
| `--dry-run` / `--log` / `--extra-args` | — | 预览 / 日志 / 追加参数 |

> ¹ `cover` 模式在带 `scale_cuda` 的构建上把缩放放进显存（`scale_cuda → 显式 hwdownload → CPU crop`，实测快 51.9%），否则 `scale,crop` 在 CPU 侧执行；`crop-cover` 始终在 CPU 侧（它要先裁剪）。解码与编码仍可走 GPU。两条路的缩放算法都显式钉 `lanczos`（GPU 侧 `interp_algo=lanczos`、CPU 侧 `flags=lanczos`）——`scale_cuda` 只到 lanczos 一档，取两者交集里的最高档并显式钉死，免得 GPU 更锐、降级到 CPU 反而变软。

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

### `interp_2x_safe.sh` — 光流插帧 2x（崩溃安全版 · GPU 专版）

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
| `-c, --cq N` | `25` | 恒定质量（`hevc_nvenc` 的 `-cq`），越大越省码率 |
| `-j, --jobs N`（别名 `--workers`） | `0`=自动 | 并行片数；`1`=顺序。**GPU 专版自动取 1**（T4 实测 NVENC 单引擎，多路并发总吞吐基本不变）；通用版 CPU 后端按 `(本 cgroup 核数 − 已用核数) / 2` 取，见下"环境探测与并发" |
| `--threads N` | `0`=自动 | 每片 ffmpeg 线程数。GPU 专版不下发（瓶颈在 GPU 引擎）。**CPU 后端别指望它分核**：`minterpolate` 是串行的（`-filter_complex_threads` 给 1/2/8 的 wall 时间一样、都只吃 ~1.1 核），`-threads` 对 libx265 也只改 frame threads、不动线程池；给 `>0` 会被翻译成 `-x265-params pools=N`（实测 `pools=2` → 2.28 核）—— **CPU 后端真正有效的杠杆是 `-j`** |
| `--sequential`（=`--no-parallel`） | 关 | 强制 1 路，等价 `-j 1` |
| `-L, --seg-len SEC` | `300` | 每片秒数。越大接缝越少但崩一次损失越多；越小损失窗口小但每片开头都要重跑一次光流预热 |
| `--cap SEC` | 关 | 只处理前 N 秒，可小数（试跑验证 / 只要前一段）。负数或非数字直接报错；超过源总长等于不设置。等价 `-T SEC` |
| `--SS TIME` | `0` | **片段起点**：只处理源视频 `TIME` 之后的内容。`TIME` 支持秒数（`90` / `90.5`）或 `HH:MM:SS[.ms]` / `MM:SS`（如 `01:30:00`） |
| `--TO TIME` | 源末尾 | **片段终点**，是**绝对时间戳**（不是时长）。与 `-T`/`--cap` 互斥 |
| `-T TIME` | 关 | **片段时长**（格式同 `--SS`）。与 `--TO`/`--cap` 互斥 |
| `--overwrite` | 关 | 允许覆盖已存在的目标文件。**默认拒绝**，且在任何编码开始前就退出 |

`--SS` / `--TO` / `-T` 三者都大于等于源时长时：终点超源末尾会被夹到末尾；`--SS` 已到/超过源末尾直接报错。
`--TO` / `-T` / `--cap` 都描述"片段终点"，**同时给两个以上直接报错**（不静默取一个）。这三个只认命令行，没有同名环境变量。

`L` / `TOTAL_CAP` / `WORKDIR` / `PRESET` / `CQ` / `JOBS` / `THREADS`
也认同名环境变量，命令行优先（非法的 `JOBS` / `THREADS` 会直接报错，不会静默退回默认值）。

**环境探测与并发**

开工前先探一遍系统（**先看 cgroup 配额，再看宿主**），结果用于告警与 `-j` 的建议值；
并行度本身在 GPU 专版下 **auto 固定取 1 路**：

```
[..] 环境  : CPU 核 8 (cgroup v2 800000/100000)  MEM 总 32.00GB 可用 30.22GB (cgroup v2)
[..] 磁盘  : /workspace/interp_2x 可用 91.4GB（源 1.2MB，2x 产物与源同量级）
[..] GPU   : Tesla T4 (CC 7.5, 驱动 580.65)  显存 12400/15109MB 可用, 利用率 39%
[..] 提示  : GPU 已有负载（有计算进程占用：p-12345; …）—— 耗时与并行收益都会受影响
[..] 并发  : 1 路 × ffmpeg 自选（GPU 单引擎实测，-j 可覆盖）
```

| 探测项 | 口径 |
|---|---|
| CPU 逻辑核 | cgroup v2 `cpu.max` → cgroup v1 `cfs_quota` → `nproc`。容器里 `nproc` 报的是**宿主**核数（本机 `nproc=8` 而配额只有 2 核） |
| 内存 | cgroup v2 `memory.max/current`（扣掉可回收的 `file`+`slab_reclaimable`）→ cgroup v1 → `/proc/meminfo` |
| GPU | `nvidia-smi` 查型号 / CC / 显存 / 利用率 / 驱动版本 + **是否有别的计算进程在用**；只告警不阻断（你自己的增强流水线经常就在跑） |
| 磁盘 | WORKDIR 所在分区余量；不足源文件的 3 倍时告警（只告警） |
| 负载 | 采样本 cgroup 的 `cpu.stat`（0.4s）算出**当前已被别的进程占用的核数**，从可用核里扣掉 —— 容器里不能用 `/proc/loadavg`（那是宿主的负载，和 `nproc` 同一个坑）。**只有通用版走 CPU 路径时用得到**；GPU 专版 `-j auto` 固定 1，不依赖它 |
| 其它实例 | 逐个读 `/proc/<pid>/cmdline` 找"另一个正在执行本脚本的 shell"（排掉自己的祖先链、子孙树，以及**另一个实例自己的子 shell**） |

自动并行度：CPU 后端取 `min(CPU 槽位 = (本 cgroup 核数 - 已用核数) ÷ 每路 1 核, 内存槽位)`；**GPU 后端取 1**。

> `CPU_PREF_THREADS` 一路收敛：4（照搬裁剪工具的 `CODEC_PROFILE`）→ 2 → **1**，依据是同机实测：
> 按线程采样一个在跑的 4K `minterpolate` 片，**25 个线程里只有 1 个满转（1.01 核）、其余 24 个 ≈0**
> —— 插帧滤镜是**串行**的，单片就是 ~1 核（lab 里 `-filter_complex_threads` 1/2/8 的 wall 时间
> 完全一样也印证了这点）。所以 8 核机器空闲时 auto = **8 路**，实际上限通常由内存画像给出
> （4K 按 4GB/片 → 21GB 可用 ≈ **5 路**）；有别的任务在跑时按"扣掉已用核数"收敛。
> 1080p 基准（8 核，当时有背景负载）：`-j 1` 85.7s、`-j 2` 49.1s、`-j 4` 27.2s、`-j 8` 27.6s。

**GPU 后端取 1** —— 依据 `memory/project_t4_gpu_capabilities.md` 的实测：T4 的 NVENC 是单引擎，
1/2/4/8 路并发总吞吐基本不变、单路吞吐随并发数成反比。所以想要"重叠收益"要自己开：

```bash
bash interp_2x_safe.sh /path/in.mp4 -j 2          # 显式 2 路
bash interp_2x_safe.sh /path/in.mp4 -j 4          # 4 路（会先做一次 4 路并发试编码确认真能跑）
bash interp_2x_safe.sh /path/in.mp4 --sequential  # 强制顺序
```

显示 `-j N`（N>1）时，脚本会先用真实输入做一次 **N 路并发试编码**（每路只读 4 帧）；跑不了就降为 1 路并说明原因，
而不是让长任务在第 3 片才炸。并行度**不影响分片内容**，因此不进 `recipe.txt`：改 `-j` 复用同一个 `-w` 会照旧 skip。

> 并行度相关的命令片段均由 Python 工具的同名口径移植（`CODEC_PROFILE` / 内存画像 / 0.8 可用比 / 0.2GB 保留）。

**只有一条后端（GPU）**

`interp_2x_safe.sh` 是 **GPU 专版**：`nvinterpolate`（NVOFA 光流硬件）+ `hevc_nvenc`，4K 2x 约 50 分钟 / 半小时素材。
它**没有 CPU 回退** —— 要 CPU 回退（`minterpolate` + `libx265`，慢一到两个数量级、4K 基本不可行）
以及配套的 `--backend` / `--cpu-preset`，用通用版 **`interp_2x_safe_v1.sh`**（其余特性两者一致）。

`interp_2x_safe.sh` 的可用性判定不是"查一下 `nvidia-smi` 在不在"就完事 —— 驱动在但显存被占满、
CUDA 解码不了这个源的编码格式、FRUC 初始化失败，这些**只有真跑一遍才暴露**。所以它会在开工前
用真实输入做一次**试编码**（只读 4 帧写 `/dev/null`），不通过就打印原因并非 0 退出：

```
[...] ERROR: GPU 不可用，无法开工: 试编码失败（1 路并发，CUDA 解码 / FRUC / nvenc 至少一处不可用）: ...
[...]   本脚本是 GPU 专版（nvinterpolate + hevc_nvenc），没有 CPU 回退后端。
```

两版写出的 `recipe.txt` 里 `backend` 字段都是 `gpu`，所以**同一个分片目录可以被两者互相接管**
（GPU 专版能接着通用版跑出的目录继续；反之亦然）。更早的 GPU-only 版本用的旧 recipe 格式会被就地升级。

**前置条件**

- FFmpeg 带 `nvinterpolate` 滤镜（本机 `/usr/local/bin/ffmpeg` 7.1 已含）+ `hevc_nvenc` 编码器
- NVIDIA GPU **Turing（CC 7.5）或更新**，驱动 ≥ 525
- 通用：源视频有**明确的帧率元数据**（目标帧率是拿它 ×2 算出来的，VFR / 0 不行）

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
音轨的**起点会补上片头被裁掉的那 3 帧**（GPU `48000/1001` 下 = 62.56ms）：因为 `trim=start_frame=3`
等价于整条视频内容比时间戳超前 3 个输出帧，音轨不补就"画面比声音快"。CPU 后端 `trim=0`，无此偏移。

> 这个偏移**只在接缝之外整体存在、不随分片数累积**：一次跑完 4s 与两片各 2s 拼接逐帧比，
> 同帧号对齐平均 **43.3dB**、平移 3 帧只有 22.2dB —— 接缝处不会跳。详见 `memory/project_long_ffmpeg_jobs.md`。

**产出**

| 路径 | 说明 |
|---|---|
| `parts/p00000.ts …` | 已完成的分片 —— **分片数就是进度** |
| `parts/p00000.meta …` | 该片的切法（`ss dt`），决定它能不能被复用 |
| `parts.txt` | concat 清单（只列本次要用的片） |
| `recipe.txt` | 「输入 + 参数」指纹；不匹配会**拒绝启动** |
| `runlogs/p00000.log …` | 每片 ffmpeg 的输出。并发时不混进主日志（否则会互相插花），失败时 `FAIL` 行后面直接附上该片日志末 20 行 |
| `runlogs/cwd/p00000/` | 该片的临时工作目录 —— `nvinterpolate` 会把 `NvOFFRUC/logFRUCError.txt` 写在**当前工作目录**下，N 路并发必须各写各的；成功即删，失败才留下 |
| `<输入名>_2x.mp4` | 成片 |

**收尾日志**：跑完会打印成片路径、**成片属性**、音轨，最后一行是**总耗时**：

```
[..] 完成  : /workspace/interp_2x/<输入名>/<输入名>_2x.mp4
[..] 成片  : 3840x2160 @ 48000/1001  hevc/yuv420p  4795 帧  100.010000s  12.6Mbps  150.1MB
[..] 音轨  : aac 6ch 48000Hz（原样 -c copy，未重编码）        # 无音轨时打印「音轨  : 无」
[..] 参考值: 片段 100.0000s ≈ 2398 源帧 x2 = 4796 帧；源整片 40648 帧
[..] 总耗时: 3m42s
```

属性用一次 `ffprobe` 取（**不** `-count_frames`，否则会把整片解码一遍），且**按 key 解析**：ffprobe 的
csv 列序是结构体固定序、跟你 `-show_entries` 里写的顺序无关。

**防呆（两层，目的都是"绝不静默产出错内容"）**

- **第一层 `recipe.txt`**：记 `backend|enc|in|L|trim`。这些一变，目录里**每一片**的内容都会不同
  （帧边界变了 / 画质档变了 / 换了视频 / 换了后端）→ 整体拒绝，并打印新旧差异。
- **第二层 每片 `.meta`**：记该片的 `ss dt`。总时长只影响**末尾那一片**的切法，所以改 `--cap`
  不再整体拒绝，而是逐片比对、**只重编边界那一片**。
- `-j` / `--threads` / `--mem-per-job` **不进** recipe（它们不影响内容），换并行度复用旧目录照旧 skip。

**两层"并发"，别混为一谈**

| | 守什么 | 机制 |
|---|---|---|
| **实例之间** | 同一个 `-w` 只允许一个实例 | `$WORKDIR/.lock` 上的 `flock`。第二个实例立刻退出并提示 —— 两个实例共用一个分片目录时，一方的清理会删掉对方正在写的临时文件（ffmpeg 仍往已被 unlink 的 inode 写），先跑完的把临时文件 `mv` 走、后跑完的就报 `mv: cannot stat '.../p00002.ts.part'`，**而且两边都白跑** |
| **实例内部** | 最多 `-j N` 片同时编码 | 分片天然互不依赖，各自 `-ss` + 独立滤镜实例 + 独立 TS；每片有自己的临时 CWD 与日志，结果通过事件文件回报给调度器 |

锁是内核级的，进程被 kill 会自动释放，不会留死锁；临时文件名另带 PID 作纵深防御。撞锁时报错会**打印持有者的 pid / 启动时间 / 命令行**（扫 `/proc/<pid>/fd` 找指向该 `.lock` 的进程）—— 占锁的未必是「另一个实例」，也可能是上一个实例没退干净的 run_job 子 shell（子 shell 会继承父进程的 fd 9，即使脚本本体已经没了；这时报出来的命令行就是那个 bash 子 shell）。

**失败时的样子**

| 情况 | 表现 |
|---|---|
| 某片失败 | 打印 `FAIL`（附该片 `runlogs/pXXXXX.log` 末 20 行）并非 0 退出；**停止派发新片**，但在飞的片会跑完（成果保留、可复用）。半成品留在 `pXXXXX.ts.part.<pid>`，它不是 `.ts`，不会被误判成"已完成"；持锁启动时会自动清掉，重跑也会重做该片 |
| 拼接失败 | 分片都还在，可手工重拼，或直接重跑 |
| 被杀 / 断电 | 已成名的 `.ts` 分片保留；临时文件下次持锁启动时自动清理 |
| Ctrl+C / SIGTERM | `trap` 会把在跑的 ffmpeg 一起收掉再退出（130/143）。收尾是**有界且有日志**的：`TERM → 最多 3s → SIGKILL`（实测 ffmpeg 捕获了 INT/TERM 但要 11–13s 才真退出，而半成品 .part 反正要丢，不值得等）。注意 Ctrl+C 只发给**终端前台进程组**：`setsid` / `&` 起的任务收不到，要用 `kill -TERM <脚本pid>` 或 `kill -TERM -<pgid>` |
| 留下孤儿 ffmpeg（脚本被 `kill -9`） | 走不到 trap，在飞的 ffmpeg 变成 **PPID=1 的孤儿**继续写盘烧 CPU。更麻烦的是 `exec 9>"$LOCK"` 的 fd **被子 shell 与 ffmpeg 继承** → 孤儿自己占着锁，下次同 `-w` 启动会先撞「另一个实例正在跑」，**"启动清孤儿"那段代码永远走不到**（实测事故：两次重跑都撞锁） | 根因已修：run_job 子 shell / 试编码 subshell 里 `exec 9>&-`，**锁只留在脚本本体** → 脚本一死锁立刻释放，孤儿交给启动时的 `残留` 清理（`TERM → 3s → KILL`）+ 清 `.part`。撞锁另有兜底：只有 ffmpeg 持有 → 收掉再接管锁；否则报错并给出持有者 pid/启动时间/命令行。回归测试：`test/test_interp_2x_orphan.sh` |

**注意**

- 所有 ffmpeg 调用都带 `-nostdin`：非前台进程组且 stdin 指向终端时，会被 SIGTTIN 停住而永久挂起。
- 每片开头会重复约 3 帧（光流拿不到"前一帧"），脚本已用 `trim=start_frame=3,setpts=PTS-STARTPTS` 裁掉。

---

### `test/test_interp_2x_lock.sh` — 回归测试（并发与锁）

守住 `interp_2x_safe.sh` 的**单实例锁与并发安全**，也就是上面那个"两边都白跑"的原始 bug；
同时把分片复用、残留清理、`--overwrite` 早退、检查顺序等不变量一起钉住。

```bash
bash test/test_interp_2x_lock.sh                                  # 自动找小素材
SUT=./interp_2x_safe.sh bash test/test_interp_2x_lock.sh
TEST_INPUT=/path/small.mp4 bash test/test_interp_2x_lock.sh
```

> 耗时取决于**被测脚本用的后端**：GPU（`nvinterpolate`）几分钟内跑完；若把 `SUT` 指向通用版
> `interp_2x_safe_v1.sh` 且回退到 CPU（`minterpolate`），因为测试要反复编解码十几遍 10 秒素材，
> 本机实测约 **15 分钟**（80 项断言）。

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
| 6 实例内并行 `-j 2` | 产出与顺序模式等价（每片切法逐片校验）；`-j` 不进 recipe → 换 `-j` 复用旧目录全 skip；并行下删掉一片只重编那一片 |
| 全局 | 所有日志都不该出现 `mv: cannot stat` |

### `test/test_interp_2x_orphan.sh` — 回归测试（中断收尾与孤儿 ffmpeg）

守住 2026-09-15 那次事故：**按了 Ctrl+C 任务"还在跑"，随后脚本本体没了，却留下一个 PPID=1 的
孤儿 ffmpeg 继续往 `parts/` 写盘**。根因两条：Ctrl+C 只发给终端前台进程组（`setsid`/`&` 起的
任务收不到）；ffmpeg 捕获了 INT/TERM 但要**11–13s** 才真退出（实测，连 640x480 都这样），
而旧代码 `kill -TERM` 之后就 `wait`。

```bash
bash test/test_interp_2x_orphan.sh                                # 现场生成 1080p 素材
SUT=./interp_2x_safe_v1.sh bash test/test_interp_2x_orphan.sh
SRC=/path/small1080p.mp4 bash test/test_interp_2x_orphan.sh       # 省掉现场生成
```

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `SUT` | 同目录的 `interp_2x_safe.sh` | 被测脚本；支持 `--backend` 的会被压到 CPU（`minterpolate` 慢，分片"在飞"的时间够长） |
| `SRC` | 现场生成的 1080p/30s | 测试素材 |
| `TERM_BUDGET` | `8` | SIGTERM 后等 ffmpeg 归零的秒数上限 |

| 用例 | 守什么 |
|---|---|
| 1 启动自愈 | 在写本目录 `parts/` 的外来写手被启动时收掉、它的 `.part` 被清；**且不误伤自己**（本次仍正常出片） |
| 2 收尾有界 | 给脚本 SIGTERM 后，`TERM_BUDGET` 内所有 ffmpeg 归零、脚本退出、**锁释放** |
| 3 真孤儿 | 杀掉脚本 + 它所有非 ffmpeg 子进程（真造出 PPID=1 的孤儿）→ 下次启动必须收掉它 |

**退出码**：`0` 全部通过 / `1` 有用例失败 / `2` 环境不具备（无 `flock` / 素材生成失败）→ 跳过。
拿不到前置条件（素材太短、后端太快抓不住"在飞的分片"、锁被子 shell 占着）会记 `SKIP` 而不是误判通过。

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

### `vidls.sh` — `ls` / `ll` 替代 + 视频属性探测

`ls` / `ll` 的**即插即用替代品**：非视频文件完全按 coreutils `ls` 的原生版式渲染（多列网格 /
`-l` 长格式，含 `total N`、人类可读体积、列间 Tab 填充 —— 实测与 `ls` 逐字节一致），
**视频文件额外追加属性列**。实现分两层：`vidls.sh` 是启动器（负责定位同目录的 `vidls.py`，
所以走软链调用也没问题），`vidls.py` 是内核（纯标准库）。

```bash
vidls                         # 列当前目录：普通文件走多列网格，视频独占一行带属性
vidls -l                      # ll 长格式 + 视频属性
vidls -lh                     # + 人类可读体积（-h 是 human-readable，不是 help）
vidls --show frames           # 加上「帧数」列（唯一会解码/解复用的列）
vidls --show frames --cpu     # 要帧数但只走 CPU 档（0.1s，不解码）
vidls --show-all              # 全部可选列：帧数/像素格式/位深/音轨/字幕/HDR
vidls --show audio,subs       # 只追加音轨与字幕
vidls -lt /path/to/dir        # 按修改时间排序
vidls --install               # 自检环境 + 装依赖 + 接入 PATH
```

默认恒显 **6 列**：**分辨率 / 帧率 / 比特率 / 编码器 / 容器 / 时长**。

**帧数是可选列**（`--show frames` / `--show-all`）—— 它是唯一需要解码或至少解复用的列，
所以**默认既不显示也不计算**：裸 `vidls` 的成本只有一次 ffprobe。
要看帧数加 `--show frames`；此时该列落在原来的位置（比特率与编码器之间）。
帧数后面带来源标签 —— 不同来源的成本与可信度差很多，所以必须标出来：

| 档 | 标签 | 手段 | 成本 |
|---|---|---|---|
| 1 | `包头` | 容器头 `nb_frames` | 免费（mp4 / mov 精确） |
| 2 | `硬解` | `ffmpeg` 显式 `xxx_cuvid` 整片解码数帧 | 解码（GPU），最准 |
| 3 | `包数` | `ffprobe -count_packets` | 只解复用、**不解码**（CPU 侧降级手段） |
| 4 | `估算` | `duration × fps` | 免费，兜底 |

**降级链**：档 1 免费所以永远先试；缺帧数时 GPU 可用走档 2，不可用（或 `--cpu`）走档 3；
都拿不到才算档 4。

> `--cpu` / `--fast` / `--deep` 都是「帧数**怎么算**」的开关，**只在显示帧数时才有意义**。
> 没请求帧数列时它们会被忽略，并往 stderr 打一行提示（不影响退出码）：
> ```
> vidls: --deep 只在显示帧数时才有意义 —— 当前没请求帧数列，已忽略。要帧数请加 --show frames（或 --show-all）
> ```

**硬解档实测成本（T4 + 1080p，900 帧样本，仅 `--show frames` 时才付）** —— 这一档明显比其它档贵：

| 档 | 耗时 | 模型 |
|---|---|---|
| `包头` | 0 | 搭主探测的车（仅 mp4/mov 可靠） |
| `包数` | **0.10s** | **与时长无关**（只解复用） |
| `硬解` | **2.26s** | 0.55s CUDA context 固定开销 + 时长/≈500fps |
| `估算` | 0 | 2.045s × 25fps 实测估出 51（真值 50） |

**所以：要帧数、又在长片或大目录上，请用 `--show frames --cpu`（或 `--fast`）** ——
实测 6 种编码器/容器组合（h264 / h264-B帧 / h264-隔行 / hevc / vp9 / av1，容器 mp4/mkv/webm/ts）里，
**包数与解码真值全部相等**，但硬解要按 `时长/500fps` 付代价（2 小时片子约 7 分钟），
包数恒定 0.1s。要帧数时默认仍走硬解（最准），想秒回就加 `--cpu` 切到包数档。

**硬解失败分两级，粒度不同**（都是实测踩出来的）：
- **CUDA / 驱动层坏掉**（`cannot load libnvcuvid`、`no device available for decoder`、
  沙箱不给设备）→ 整个进程不再试硬解，全部回落到包数档；
- **这张卡解不了某个编码器**（实测 T4 遇到 AV1：`Codec av1_cuvid is not supported.`）→
  **只拉黑该编码器**，同批的 h264 / hevc / vp9 继续走硬解。

档 2 用**显式** `-c:v xxx_cuvid` 而不是 `-hwaccel cuda` 的自动选择：后者遇到 NVDEC 不支持的
编码器（实测 ffv1）会**静默转软解** —— 帧数虽然对，但「硬解」这个标签是假的。所以先查
`ffmpeg -decoders` 里有没有该编码器的 `_cuvid` 解码器，没有就直接走包数档。

**硬解并发拐点是 4 路**（8 × 900 帧 1080p 实测 `-j 1/2/4/8` → 18.1 / 10.6 / **9.4** / 10.4 秒；
4 路时 GPU 利用率仅 40%、显存 540MiB，瓶颈在每文件的 CUDA context 启动）。

| 参数 | 说明 |
|---|---|
| `-l` / `-1` / `-a` / `-A` / `-d` / `-t` / `-S` / `-r` / `-h` | 与 `ls` 同义；**`-h` 是 `--human-readable`**，帮助看 `--help` |
| `--show LIST` / `--show-all` | 追加可选列，逗号分隔：`frames,pixfmt,bits,audio,subs,hdr`；`--show-all` = 全部 |
| `--cpu` | **需 `--show frames`**：帧数跳过 GPU 硬解、走「包数」档（**比硬解快 20 倍以上**，长片 / 大目录建议加） |
| `--fast` | **需 `--show frames`**：永不解码，帧数只取容器头，缺则估算 |
| `--deep` | **需 `--show frames`**：强制重新数帧（忽略容器头），有 GPU 走硬解，否则包数 |
| `-j N` | 探测并发数（`0` = 按 CPU / 内存自动决定，深解档额外封顶 4 路） |
| `--ffmpeg-bin` / `--ffprobe-bin` | 指定 FFmpeg 路径（可给目录，也可给可执行文件路径） |
| `-v` | 把资源探测结果、视频数、并发度、硬解是否可用打到 stderr |
| `-I` / `--install` | 环境自检与安装（见 [安装](#把-vidls-装成一条命令可选)）；配套 `--prefix DIR`、`-y` |

**并发与资源**：只对视频条目起 `ThreadPoolExecutor`（非视频只要 `lstat`），
并发度按 cgroup 感知的 CPU / 内存探测自动算（沿用 `vidcrop_cpu_v2.py` 的预算常量，
每路 ffprobe 预留 0.1GB），结果按原索引回填，**输出顺序稳定**。
单个文件探测失败只让那一行显示 `不可探测（原因）`，不中断整批。

**退出码**：`0` 正常 / `1` 有条目 stat 或探测失败 / `2` 参数错误（如 `--show` 里出现未知字段名）。

**已知不做**：`-R` 递归、`--color` 配色、`-i` inode、`--time-style` 等冷门开关 —— 需要时请用真 `ls`。

#### Windows 版：`vidls.cmd` + `vidls_win.py`

Windows 上另起两个文件，**Linux 版原样保留、两者互不 import**。
`vidls.cmd` 是启动器（用 `%~dp0` 定位自己，所以在仓库里直接敲也行），`vidls_win.py` 是内核；
`vidll.cmd` 是 `vidls -l` 的快捷方式（只转发，见 [vidll 一节](#vidll--vidls--l-的快捷方式linux--windows)）。

**为什么内核带 `_win`、启动器不带**：内核加 `_win` 是为了**一眼区分**哪份 `.py` 是哪边的
（`vidls.py` 是 Linux、`vidls_win.py` 是 Windows）；启动器叫 `vidls.cmd` 是为了让
**命令名在 Windows 上也是 `vidls`**，与 Linux 完全一致 —— 装好之后两边都是
`vidls` / `vidll` / `vidls --install`，敲的时候不用想自己在哪台机器上。

```bat
vidls                     :: 列当前目录（普通文件走网格，视频独占一行带属性）
vidls -l                  :: ll 长格式 + 视频属性
vidll                     :: == vidls -l
vidls -lh D:\videos       :: 人类可读体积
vidls --install           :: 自检 + 把 vidls / vidll 写进 PATH（默认 %USERPROFILE%\.local\bin）
```

> 在仓库目录里可以直接 `vidls.cmd`（cmd.exe 会先找当前目录）；装过 `--install`
> 之后从任何目录都能直接敲 `vidls`。

`--install` **不做软链**（Windows 建软链要管理员 / 开发者模式），而是在一个**已经在 PATH 里**
的目录写四个启动器：`vidls.cmd` / `vidll.cmd`（cmd / PowerShell）与无扩展名的 `vidls` / `vidll`（Git Bash）。
四个都指向**仓库里**同一个 `vidls_win.py` 绝对路径 —— 改代码立刻生效、不用重装；
代价同样是**仓库搬家后要重跑 `--install`**。目标目录不在 PATH 里时**只打印指引**，不擅自改
（并提醒别用 `setx`：它会把 PATH 截断到 1024 字符）。

**非视频部分的版式在 Git Bash 下与 coreutils ls 8.32 逐字节一致** —— 实测 **1040 组随机布局**
（名字长度 1~15、混 CJK、终端宽 12~300）全部 byte-identical，其中 160 组是端到端走 PATH 上
装好的 `vidls.cmd` 验的。四处已知差异（都是平台限制，宁可不显示也不显示错的）：

| 差异 | 原因 |
|---|---|
| `-l` 没有属主 / 属组 / 硬链接数三列 | Windows `os.stat` 的 `st_uid`/`st_gid` 恒为 0；MSYS 的 `197121` 是它自己的映射表；`st_nlink` 从 `os.scandir` 拿恒为 0（真值要额外 open 一次文件句柄） |
| `-l` 的 mode 是 `-rw-rw-rw-` / `drwxrwxrwx` | Python 给的是**合成**模式（文件 0666 / 目录 0777，只反映只读位）；MSYS 的 `0644`/`0755` 是从 ACL 推的 |
| `-l` 的 `total` 是**近似值** | Windows 没有 `st_blocks`，按「4K 簇 + NTFS 常驻小文件（≤~700B）记 1KB」模拟。模型本身实测准（17 个同尺寸新副本：ls 890 = 模型 890），但 NTFS 实际分配受写入史影响会差几 KB（同批原文件是 898） |
| 隐藏文件只看 `.` 前缀 | 与 MSYS 的 ls 一致，**不看 Windows 的 H 属性**（实测 `attrib +H` 的文件在 `ls` 里照样出现） |

> 输出编码**跟随控制台**：Git Bash 下是 UTF-8（所以能跟 `ls` 逐字节比），
> cmd.exe / PowerShell 下是 GBK（中文才显示得对）。另：`.cmd` 启动器**故意写成纯 ASCII**
> —— cmd.exe 用 ANSI 代码页（本机 936）解析批处理，UTF-8 中文注释会被拆成乱码并**破坏解析**。

> ⚠️ **WSL 会把 Windows 的 PATH 带进来** —— 装在 `C:\Users\<你>\.local\bin` 的
> `vidls` / `vidll` 在 WSL 里也会被找到并执行，而它们本来只会用 Windows 的
> `python.exe` 跑 Windows 内核，在 WSL 里必然报
> `exec: /c/Program Files/Python312/python.exe: not found`（实测踩到过）。
> 所以那份**无扩展名的 shim 里带了三分支**，按 `uname -s` 自己选路：
>
> | `uname -s` | 行为 |
> |---|---|
> | `Linux*`（WSL） | **转交给仓库里的 `vidls.sh`**（`/mnt/d/.../vidls.sh`）—— 只有 Linux 版才懂 WSL 的路径语义；找不到就明确报错并给出 `bash /mnt/d/.../vidls.sh --install` |
> | `MINGW*` / `MSYS*`（Git Bash） | 用 Windows 的 python 跑 `vidls_win.py` |
> | 其它 | 明确报错，不猜 |
>
> 结果：**WSL 里 `vidll` / `vidls` 谁排在 PATH 前面都能正常工作** —— 命中 Linux 软链就直接跑，
> 命中 Windows shim 就自动转交给 Linux 版。生成的 shim 是**快照**，改完要重跑一次
> `vidls --install`（Windows 侧）才会更新。

> 💡 **移植时的额外收获**：用这 1040 组样本还查出 Linux 版 `vidls.py` 有三处版式 bug，
> 并**已于 2026-09-17 回修**（两份实现现在一致，Linux 版那三处函数的注释里也记了实测依据）：
> ① 列宽下限 `MIN_COLUMN_WIDTH = 3` 是错的 —— 短名字目录比 `ls` 多两格空白
> （实测 `ls` 的 `a  b  c` 间隔是 2，不是 4）；
> ② 缺「**最后一列不能是空的**」判据（30 个单字符名字、宽 80 时会比 `ls` 多铺一列）；
> ③ Tab 填充缺「**Tab 不省字节就用空格**」判据（`from % 8 == 7` 时多吐一个 Tab）。
> 前两条只在名字普遍 ≥3 字符、条目数又不巧时才显形，所以最初那轮 40/60/80/100/166 列
> 的对比没抓到 —— 是**随机化布局 + 大量样本**才把它逼出来的。

---

### vidll — vidls -l 的快捷方式（Linux + Windows）

`ll` 的替代品：**`vidll` 完全等于 `vidls -l`**，一个字符都不差。

| 平台 | 文件 | 说明 |
|---|---|---|
| Linux | `vidll.sh` | 3 行逻辑：把 `-l` 塞到参数最前面，`exec` 给同目录的 `vidls.sh` |
| Windows | `vidll.cmd` | 同样只做转发，`call` 给同目录的 `vidls.cmd` |

```bash
vidll                 # == vidls -l
vidll -h              # == vidls -l -h（人类可读体积）
vidll -lt /path       # == vidls -l -lt /path
vidll --show-all      # == vidls -l --show-all
```

**为什么用转发而不是复制逻辑**：vidll 自己没有实现，参数解析、版式、退出码、探测行为
全部由 vidls 决定 —— 所以两者**永远同步**，改 vidls 不用回来动 vidll。
实测（Windows）：`vidll <dir>` 与 `vidls -l <dir>` 输出**逐字节相同**，
退出码 `0/1/2`、`--help` 文本、`--show bogus` 报错全都一致。

`--install` 会**一起装上**（Linux 建第二条软链 `vidll -> vidll.sh`；Windows 多写
`vidll.cmd` 与 `vidll` 两个启动器），所以装好之后 `vidls` / `vidll` 都能直接敲。

> `vidll` 的实现在 Windows 上只做了 `call` 转发（不重复探测 Python），
> 在 Linux 上是 `exec` 转发（不产生多余进程）—— 两边的错误处理都留在 vidls 里。

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

# 先裁剪后缩放覆盖（crop-cover）：按 16:9 最大化裁剪，再缩放覆盖到 1920x1080
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --mode crop-cover --crop-ratio 16:9 \
    --output-width 1920 --output-height 1080

# 同上，但只给一个维度（高度按 16:9 自动推导为 720）
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --mode crop-cover --crop-ratio 16:9 --output-width 1280

# 干跑预览实际命令
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --dry-run

# 列目录时顺带看视频属性（vidls）
vidls                                  # 当前目录：普通文件多列，视频行带属性
vidls -l                               # ll 风格
vidls -lh /path/to/dir                 # 人类可读体积
vidls -l --show-all                    # 追加像素格式 / 位深 / 音轨 / 字幕 / HDR
vidls --show frames                    # 加帧数列（默认不算：它是唯一要解码的列）
vidls --show frames --deep --cpu        # 强制重新数帧，且只走 CPU 档（对拍用）
vidll                                  # == vidls -l（ll 替代；两个平台都有）
vidll -h /path/to/dir                  # == vidls -l -h

# Windows 版：命令名与 Linux 一致（装过 --install 之后两边都是 `vidls`）。
# 仓库目录里可以直接用 vidls.cmd（cmd.exe 会先找当前目录）；Git Bash 里 ./vidls.cmd 也行。
vidls                                  # 当前目录：普通文件多列，视频行带属性
vidls -l                               # ll 风格 + 视频属性
vidls -lh D:\videos                    # 人类可读体积
vidls --install                        # 自检 + 把 vidls 写进 PATH

# 光流插帧 2x：脱离会话后台跑，日志落盘（4K 素材约 50 分钟 / 半小时）
setsid bash interp_2x_safe.sh /path/in.mp4 -w /tmp/work \
    > /tmp/work/run.log 2>&1 < /dev/null &

# 开实例内并行（GPU 单引擎默认只给 1 路，重叠收益要自己开；脚本会先做并发试编码）
bash interp_2x_safe.sh /path/in.mp4 -j 2

# 先小规模验证：4 秒一片、只跑前 12 秒，产物扔 /tmp
bash interp_2x_safe.sh /path/in.mp4 -w /tmp/demo -L 4 --cap 12

# 只处理前 10 分钟；之后想补全片，把 --cap 去掉用同一个 -w 重跑即可
bash interp_2x_safe.sh /path/in.mp4 /tmp/head.mp4 --cap 600

# 锁与并发安全的回归测试（退出码 0/1/2；GPU 后端几分钟，CPU 后端约 12 分钟）
bash test/test_interp_2x_lock.sh
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

### 4. 先裁剪后缩放覆盖（crop-cover 模式）

```bash
# 按 16:9 最大化裁剪，再缩放覆盖到 1280x720
python vidcrop_cpu_v2.py \
    --input ./clips --output ./out \
    --mode crop-cover --crop-ratio 16:9 \
    --output-width 1280 --output-height 720

# --crop-ratio 已定比例：只给一个维度即可（高度按 16:9 自动推导为 720）
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --mode crop-cover --crop-ratio 16:9 --output-width 1280 \
    --codec hevc_nvenc --cq-ref 22
```

> 与 `cover` 的区别：`cover` 按**目标**比例缩放再裁（裁掉哪一圈由尺寸比决定）；
> `crop-cover` 先按 `--crop-ratio` **独立**裁出画面，再缩放覆盖到目标尺寸 ——
> 想定向"裁掉画面哪一圈"时用后者。不带 `--crop-ratio` 时裁剪比例就是目标宽高比
> （此时两个维度都必须给）。
>
> 整条链是**单次 ffmpeg** 调用（`crop=…,scale=…` 一条滤镜链，逐帧流式通过），
> 不落中间文件、只编解码一次，因此没有二次有损压缩。

### 5. 按比例裁剪（不指定具体尺寸）

```bash
python vidcrop_cpu_v2.py \
    --input ./clips --output ./out \
    --crop-ratio 16:9 --codec libx264 --crf-ref 21
```

### 6. 递归扫描子目录 + 保留目录结构

```bash
python vidcrop_hwaccel.py \
    --input ./footage --output ./out \
    --output-width 1920 --output-height 1080 \
    --recursive --codec hevc_nvenc --cq-ref 22 --overwrite
```

### 7. Linux 服务器 + Intel 核显（VA-API 仅硬解，软件编码）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --codec libx264 --crf-ref 21 --decode vaapi
```

### 8. CI / 容器环境（三轴全 CPU，等价于旧的 `--hwaccel none`）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --decode cpu --scale-algo libswscale-lanczos --codec libx264
```

> 只写 `--decode cpu` **不等于纯 CPU** —— 那只关解码，`--scale-algo auto` 仍会优先尝试显存内缩放（软解时走 `hwupload_cuda`），`--codec` 默认仍是 `h264_nvenc`。纯 CPU 要把三个轴都钉死（如上），此时会跳过全部 GPU 探测。

### 8b. 软解 + 显存内缩放（NVDEC 解不了源编码，或驱动有缺陷时）

```bash
python vidcrop_hwaccel.py \
    --input ./clips --output ./out \
    --output-width 1280 --output-height 720 \
    --decode cpu --scale-algo cuda-lanczos --codec hevc_nvenc
```

> 链为 `hwupload_cuda → scale_cuda → 显式 hwdownload → CPU crop → hevc_nvenc`。要多一次整帧上载，吞吐未必优于「软解 + CPU 缩放」；**先看概览块「组合」那行的提示**。

### 9. 网页分发（VP9 / WebM）

```bash
# 输出自动为 .webm；若源音轨是 AAC，会自动换成 libopus（WebM 不收 AAC）
python vidcrop_cpu_v2.py \
    --input ./videos --output ./web \
    --output-width 854 --output-height 480 \
    --codec vp9 --crf-ref 24
```

### 10. 长期归档（AV1，极致压缩率）

```bash
# libsvtav1 比 libaom-av1 快一个数量级，是首选
python vidcrop_cpu_v2.py \
    --input ./videos --output ./archive \
    --output-width 1920 --output-height 1080 \
    --codec svtav1 --crf-ref 21
```

### 11. AV1 硬件编码（需 Ada / RTX 40 / L40 及以上）

```bash
# 硬件不支持时自动降级为 libsvtav1 CPU 编码，并打印提示
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 \
    --codec av1_nvenc --cq-ref 21
```

### 12. 音频重编码

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --codec hevc_nvenc --cq-ref 20 \
    --audio-codec aac --audio-bitrate 192k --overwrite
```

### 13. 手动并发策略（CPU v2，2 任务 × 4 线程）

```bash
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 \
    --workers 2 --threads 4
```

### 14. 自定义 FFmpeg 构建路径 + 诊断硬解故障

```bash
python vidcrop_hwaccel.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --ffmpeg-bin /opt/ffmpeg-7.0/bin/ffmpeg --cuda-diagnostics
```

### 15. 追加自定义 FFmpeg 参数

```bash
python vidcrop_cpu_v2.py \
    --input video.mp4 --output out.mp4 \
    --output-width 1280 --output-height 720 \
    --extra-args -- -max_muxing_queue_size 4096
```

### 16. 带日志归档的批量任务

```bash
python vidcrop_cpu_v2.py \
    --input ./videos --output ./out \
    --output-width 1280 --output-height 720 \
    --recursive --log process.log --overwrite
```

---

## 硬件加速说明

`vidcrop_hwaccel.py` 启动后首先进行一次**运行时探测**（实测输出，Tesla T4 / 自建 FFmpeg 7.1）：

```
正在检测硬件加速能力...
  CUDA 解码:  可用 ✓
  h264_nvenc: 可用 ✓
  hevc_nvenc: 可用 ✓
  av1_nvenc:  不可用 ✗
  crop_cuda:  不可用 ✗
  scale_cuda: 可用 ✓
  Vulkan:     不可用 ✗
  VA‑API:     不可用 ✗
  OpenCL:     不可用 ✗
  ── 说明 ──
  · av1_nvenc 不可用：AV1 硬编需 8 代 NVENC（Ada / RTX 40 / L40 及以上）；--codec av1_nvenc 会自动降级为 libsvtav1 CPU 编码
  · crop_cuda 不可用：该滤镜在 FFmpeg 上游并不存在（与编译选项无关），crop 模式的全 GPU 流水线跳过；裁剪实际在 CPU 侧完成
```

不可用项会给出**原因说明**，而不是只打一个 ✗。

**探测是按轴按需进行的**，不是每次都全探：

- 三轴都显式指向 CPU（`--decode cpu` + `--scale-algo libswscale-*` + CPU 编码器）→ **跳过全部 GPU 探测**，不打印上面任何一行。
- 编码器与滤镜的探测**与解码轴无关**（软解照样可能用 NVENC 编码、用 `scale_cuda` 缩放），由 `--codec` / `--scale-algo` 决定要不要探。
- `--scale-algo auto` 且零拷贝路径拿不到 CUDA 帧时，会多跑一次**功能探针**（真跑 1 帧 `hwupload_cuda,scale_cuda → null`），打印一行 `hwupload缩放: 可用 ✓ / 不可用 ✗`。显式 `--scale-algo cuda-*` **不跑探针**（直接执行）。
- 只有**真的探过**的项才会出现在「说明」里——不会出现「没探却说它不可用」的假结论。

### 三个正交轴

解码 / 缩放 / 编码是**三个互相独立的轴**，可以任意组合，轴之间**没有任何冲突检查**：

| 轴 | 参数 | 取值 | 只管什么 |
|---|---|---|---|
| 解码 | **`--decode`** | `auto`（默认）/ `cuda` / `vulkan` / `vaapi` / `opencl` / `cpu`（旧值 `none` ≡ `cpu`） | 帧在哪解出来（要不要下发 `-hwaccel`） |
| 缩放 | `--scale-algo` | `auto`（默认）/ `libswscale-<algo>` / `cuda-<algo>` | 重采样在哪、用什么算法 |
| 编码 | `--codec` | `auto` / `h264_nvenc` / `libx264` / … | 用哪个编码器 |
| 降级 | `--fallback-policy` | `auto`（默认）/ `strict` | **只回答一件事**：显式点名的后端不可用/失败时，降级还是报错 |

合法组合举例：

- `--decode cpu --codec h264_nvenc` → 软解 + NVENC 硬编（等于旧 `--fallback-policy nvenc-only`）
- `--decode cuda --codec libx264` → 硬解 + 软编
- `--decode cpu --scale-algo cuda-lanczos` → 软解 + 显存内缩放（`hwupload_cuda` 链）

> ⚠️ **`--decode cpu` 只关解码，不再等于「纯 CPU」。** 这是相对旧 `--hwaccel none` 的**语义收窄**——旧的那个值会把整块 GPU（含 NVENC 编码与 `scale_cuda` 缩放）一起关掉。
> 想要旧的「纯 CPU」行为，请写三轴形式：
> `--decode cpu --scale-algo libswscale-lanczos --codec libx264`
> （这条组合会被识别为「三轴全 CPU」，**跳过全部 GPU 探测**，与旧 `cpu-only` 一样快。）

**`--decode` 参数语义：**

| 值 | 行为 |
|---|---|
| `auto`（默认） | **先探测再定**（与 `--scale-algo auto` 同一套逻辑）：探测到可用硬解就给那个具体后端（顺序 CUDA > Vulkan > VA-API > OpenCL）；一个都没有就降级 `cpu`、不下发 `-hwaccel`。`auto` 不是显式请求，所以探测不到只是降级、不触发 `strict` 报错 |
| `cuda` | 强制 CUDA 硬解；不可用时按 `--fallback-policy` 处理（`auto` 降级 + 提示 / `strict` 报错退出 2） |
| `vulkan` / `vaapi` / `opencl` | 用对应后端做硬解；产出的仍是软件帧，因此可与 `--scale-algo cuda-*`（走 `hwupload_cuda`）或任意编码器组合 |
| `cpu` | 纯软解。**不影响** `--codec` 与 `--scale-algo` |

> 旧名 `--hwaccel` 已**硬更名**为 `--decode`：用旧名会直接报错退出 2，并在提示里给出等价的 `--decode` 写法。
>
> `auto` 不会再下发 `-hwaccel auto` 让 ffmpeg 自己试 —— 那样「实际用了什么」脚本是不
> 知道的，概览块也报不出确定答案。现在探测说了算，概览块的「解码」行会直接给出
> `auto → cuda` 或「软件（auto 探测无可用硬解，已降级 cpu）」。

**`--fallback-policy` 参数语义：**

| 值 | 行为 |
|---|---|
| `auto`（默认） | 显式点名的后端不可用/执行失败 → **降级到下一档并提示**，继续跑完整策略链 |
| `strict` | 显式点名的后端不可用/执行失败 → **报错退出 2**，不降级（策略链只保留首选策略） |

> 旧值 `strict-cuda` / `nvenc-only` / `cpu-only` 已删除（它们其实是「三轴预设」，不是策略）。用旧值会报错并给出可直接抄的等价写法：
> - `cpu-only` → `--decode cpu --scale-algo libswscale-lanczos --codec libx264`
> - `nvenc-only` → `--decode cpu --codec h264_nvenc`
> - `strict-cuda` → `--decode cuda --fallback-policy strict`

**策略链（按优先级依次尝试，`strict` 时只剩首选）：**

1. CUDA 全流水线（硬解 + `crop_cuda` + NVENC 硬编）— 仅 crop 模式；**该滤镜上游不存在，实际永远跳过**
2. CUDA 缩放 + CPU 裁剪（硬解 + `scale_cuda` + 显式 `hwdownload` + CPU crop + NVENC 硬编）— 仅 cover 模式
2b. CUDA 缩放 + CPU 裁剪（**软解** + `hwupload_cuda` + `scale_cuda` + 显式 `hwdownload` + CPU crop）— 仅 cover 模式
3. 解码轴给的后端（`auto` → `-hwaccel auto`）+ NVENC 硬编（CPU 做 vf 滤镜）
4. 指定硬解（CUDA / Vulkan / VA-API / OpenCL）+ CPU 软件编码
5. `auto` 模式下最佳硬解 + CPU 软件编码
6. 纯 CPU 兜底

**`--scale-algo`：缩放算法怎么选、后端怎么定**

写法 `<backend>-<algo>` 或裸 `<algo>`。前缀决定**强制**哪个后端，裸名字则交给自动选择。
不传 = `auto`：**优先 cuda、失败回退 cpu**。

| 写法 | 含义 |
|---|---|
| `auto`（不传） | 显式 `--decode cpu` 时也会优先显存内缩放（走 `hwupload_cuda`），但**先跑功能探针**确认真能跑通；纯默认（`--decode auto`）只在零拷贝可用时用 cuda |
| `lanczos` / `bicubic` / `nearest` … | 只定算法、后端自动。hwaccel 下**要求两张表都认**（避免歧义）；v2 下只查 libswscale 表（所以能省前缀） |
| `libswscale-<algo>` | 强制 CPU 侧 `scale=…:flags=<algo>`，**不插** CUDA 缩放策略 |
| `cuda-<algo>` | 强制 CUDA 缩放链（仅 cover 模式）：有硬解走零拷贝 `scale_cuda=…`，否则走 `hwupload_cuda,scale_cuda=…` |

- `libswscale` 侧：`fast_bilinear` `bilinear` `bicubic` `neighbor` `area` `bicublin` `gauss` `sinc` `lanczos` `spline`（= `scale` 滤镜 flags 里真能当算法用的那些；不收 `experimental`，它要配 `+unstable`）。
- `cuda` 侧：`nearest` `bilinear` `bicubic` `lanczos`（= `scale_cuda` 的全部具名档，量程 0~4；默认值 0 未映射到具名档，所以必须显式给）。`nearest` ↔ `neighbor` 互为别名。
- **显式 `cuda-*` 不要求 NVENC 编码器**：链尾本来就是软件帧（`hwdownload` + CPU crop），硬编软编都接得住。
- **`cuda-*` 的降级**：当前 FFmpeg 没有 `scale_cuda` 滤镜 → `auto` 时**退回 `libswscale-<同档>` 并告警**，`strict` 时**报错退出 2**。显式 `cuda-*` **不跑功能探针**，直接执行；执行失败也按同一个策略处理。
- `crop` 模式不做缩放 → 给了只提示"不生效"，继续跑。
- v2 **拒绝** `cuda-*`（纯 CPU 路径，没有 GPU 缩放链），并指向 hwaccel 版——这是有意的两脚本不对称。

**组合效果提示：** 概览块会按实际生效的（解码, 缩放, 编码）打印一行「组合」评估，例如「全 GPU 零拷贝，最快路径」「软解 + hwupload_cuda：要多一次整帧上载，可能不如 CPU 缩放」。它只是提示，**不会阻断执行**。

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
| `crop_cuda` 缺失 | 该滤镜**在 FFmpeg 上游并不存在**（不是编译选项问题），crop 模式的全 GPU 流水线（策略 1）始终跳过 | 仍走"硬解 + CPU 裁剪 + NVENC 硬编"；代价是**吞吐对 CPU 敏感**，见 [FAQ Q13](#常见问题faq) |
| cover 的 CUDA 缩放需自建 FFmpeg | 只有 `--enable-cuda-nvcc` 编出来的 FFmpeg 才有 `scale_cuda`，发行版 gpl 构建通常没有 | 没有就自动退回 CPU 侧 `scale`（结果正确、只是慢）；要拿那 51.9% 的提速需自建 |
| `--decode cpu` 不再等于纯 CPU | 旧 `--hwaccel none` 会把整块 GPU 一起关掉；现在 `--decode cpu` 只关解码，`--scale-algo auto` 仍会优先尝试显存内缩放（软解时走 `hwupload_cuda`），`--codec` 默认仍是 `h264_nvenc` | 要纯 CPU 用三轴写法 `--decode cpu --scale-algo libswscale-lanczos --codec libx264`（会跳过全部 GPU 探测） |
| 旧名 `--hwaccel` 与三个旧 `--fallback-policy` 值已删除 | `--hwaccel` 硬更名成 `--decode`；`strict-cuda` / `nvenc-only` / `cpu-only` 不是"策略"而是"三轴预设"，已移除 | 用旧名/旧值都会报错退出 2，并在提示里给出可直接抄的等价写法 |
| 硬件解码能力**分编解码器**，不是"有 / 没有" | 能力探测里的 `has_decoder` 是拿 **H.264 微流**探出来的**机器级**标志；而 NVDEC 实际是分编解码器的——T4（Turing）能解 H.264 / HEVC / VP9 / MPEG-2/4 / VC-1，但**解不了 AV1**（要 Ampere 起的第 5 代）。拿机器级标志推断"这个源能硬解"会误判，代价不只是多一次无用尝试：`--decode auto` 会让每个 AV1 文件都白跑一次必然失败的链；`--fallback-policy strict` 下更是直接退出 2，而这条素材走软解其实完全可行（实测 `[av1 @ ...] Failed setup for format cuda: hwaccel initialisation returned error`） | 现在按**源编解码器**用**真实输入**试解 1 帧（`-frames:v 1 -f null`，几十毫秒），结果按 codec 名缓存、一批文件只探一次；解不了就按软解处理并提示，显式 `--decode cuda` + `strict` 则提前报错（不会白跑一次转码才发现）。正常素材（H.264 / HEVC）只多这一次 1 帧解码，命令逐字不变 |
| **软解 + `hwupload_cuda` 只在高位深且真缩放时划算** | 上载 / 回下载开销固定，而 p010le 的 CPU 缩放比 8bit 贵得多。T4 两批共 12 组素材实测：**源 ≥10bit 且真在缩放 → +14~25%**；8bit 真缩放 → **+0.4% ~ −14%**；**恒等缩放（无论位深）→ −1.6% ~ −34.7%**（注意 10bit 恒等也是 −22%）。绝对值上软解链路整体 44~46s，而硬解零拷贝只要 12.8s | 有硬解时永远该走硬解。`--scale-algo auto` 只在显式 `--decode cpu` 下才自动走它，且要过两道关：**功能探针**（链真能跑通）+ **值不值**（≥10bit 且非恒等）；不满足时会打印具体理由。要强制使用请显式写 `--scale-algo cuda-lanczos` |
| 显式 `cuda-*` 执行失败要等到运行期才发现 | `--scale-algo cuda-*` **不跑功能探针**（按设计直接执行） | `--fallback-policy auto`（默认）会自动降级到 `libswscale-<同档>`；要"不可用就报错"用 `strict` |
| **零拷贝 CUDA 链不能传 `-pix_fmt`** | 该链上 `-hwaccel_output_format cuda` 时帧是 CUDA 帧，`-pix_fmt` 设的是 `AVFrame.format`（= `AV_PIX_FMT_CUDA`）而非 `sw_format`，传 `nv12` / `yuv420p` 实测都报 `Impossible to convert` | 已按链型分别落地：零拷贝链改用 `scale_cuda=format=` + `-profile:v`，只有软件帧链才下发 `-pix_fmt`。用户请求 `scale_cuda` 不支持的格式时按 `--fallback-policy` 降级或报错 |
| **`tonemap_cuda` 上游不存在** | 实测 `ffmpeg -h filter=tonemap_cuda` → `Unknown filter`（与 `crop_cuda` 同款，非编译选项问题）。CUDA 侧没有硬件 HDR→SDR | `--hdr sdr` 走 CPU 的 `zscale` + `tonemap`；CUDA 链本来就先 `hwdownload` 成软件帧，直接接在链尾即可。滤镜缺失时会降级为 `--hdr drop` 并提示 |
| `--hdr sdr` 的 tone mapping 未实测 | 这是全新能力：滤镜配方、desat、各算法（hable / mobius / reinhard）的观感差异都还没有 T4 数据 | 先在真实 HDR 片源（如 HLG 的 `new4_raw`）上验一遍再用于生产；算法可换（`--hdr sdr:hable`） |
| `crop-cover` 不走 CUDA 缩放 | 它必须先裁剪，而裁剪只能在 CPU（无 `crop_cuda`）→ GPU 缩放要额外一次 `hwupload_cuda` | 保留 CPU 侧 `crop,scale`（该路径未实测）；要用 CUDA 缩放请改用 `--mode cover` |
| CPU 侧缩放是 lanczos（比旧版慢） | `cover` / `crop-cover` 默认用 `flags=lanczos`（原先是 libswscale 默认的 bicubic），抽头更多 → CPU 侧吞吐略降 | 这是为了与 GPU 侧同档、避免降级时画质变软。换档用 `--scale-algo`（如 `--scale-algo bicubic`） |
| v0 / v1 与 v2/hwaccel 的缩放档位不同 | v0/v1 仍吃 libswscale 的默认 `bicubic`（它们定位是"对照旧行为"，这次没跟着改） | 要同档用 v2 / hwaccel；要对照旧输出用 v0/v1 |
| `--scale-algo` 的裸名字在 v2 与 hwaccel 上不完全等价 | v2 只有一个后端 → **任何 libswscale 算法都能省前缀**（`--scale-algo spline` 可用）；hwaccel 有两个后端 → 裸名字要求两表都认，`spline` 这类只有 libswscale 有的**必须**写成 `libswscale-spline`，否则报错 | 想让同一条命令两边都能跑就统一带前缀（`libswscale-<algo>`）；只在 v2 上用才可省 |
| `--crop-ratio` + **只给一个**维度（非 crop-cover） | 互斥判据是"两个维度都给才算同时指定"，只给一个不算 → 那个维度被**静默忽略**（`--mode crop --crop-ratio 16:9 --output-width 320` 里 `320` 不生效）。两个脚本行为一致 | 按比例裁剪就别给尺寸；要指定最终尺寸用 `--mode crop-cover`（该模式明确支持单维度） |
| hwaccel 不校验 `--original-width/height` | 只有 `vidcrop_cpu_v2.py` 校验正整数；hwaccel 传负值会一路带进尺寸计算 | 手填源尺寸时自己保证为正；不确定就用默认的 ffprobe 探测 |
| `librav1e` 无 `-crf` | 编码器本身只支持 `-qp` | 脚本自动换算（实测标定） |
| `h264_nvenc` 无 10bit | NVENC H.264 只做 8bit | 自动降 8bit 保硬件；需 10bit 用 `hevc_nvenc` / `av1_nvenc` |
| 脚本依赖 `convert_crf.py` | 两个裁剪脚本运行时 import 同目录该文件 | 拷贝时一并带上 |
| `interp_2x_safe.sh` 只做 **2 倍** | `fps=source_fps*2` 与片头去重帧数都按 2x 写死 | 要 1.25x / 2.5x 需改源码里这两处 |
| 光流插帧需要**自建 FFmpeg** | 发行版官方包不含 `nvinterpolate`；且要求 Turing（CC 7.5）或更新、驱动 ≥ 525 | 用带该滤镜的 ffmpeg；**没有 GPU 或显卡不满足时**用通用版 `interp_2x_safe_v1.sh --backend cpu`（`minterpolate`，慢很多） |
| 4K 2x 很慢 | 实测约 **0.6× 实时**（半小时素材约 50 分钟），且会与其它 GPU 任务争用 | 用 `setsid` 起 + 分片；`-L` 调小以缩小单次损失 |
| GPU 后端并行收益有限 | T4 实测 **NVENC 单引擎**：1/2/4/8 路并发总吞吐基本不变，单路吞吐随并发数成反比（`memory/project_t4_gpu_capabilities.md`） | 所以 `auto` 在 GPU 后端只给 1 路；想要重叠收益自己 `-j 2/3/4`（脚本会先做并发试编码），实测后再决定长期用哪个值 |
| 并行度受 cgroup 配额限制 | 容器里 `nproc` 报宿主核数（本机 8），实际配额可能只有 2 核 | 脚本按 cgroup 配额算并行度；也可 `-j` / `--threads` 手动定 |
| 每个分片开头重复约 3 帧 | GPU 后端：光流拿不到"前一帧"，每个新起的滤镜实例都要预热 | 脚本已用 `trim=start_frame=3,setpts=PTS-STARTPTS` 裁掉；**不要**再手工加 ±offset 补偿 |
| CPU 后端末片会少 3 帧（已修） | 通用版的 `minterpolate` 吐不出最后一帧（没有下一帧可插），撞到 EOF 时输出凭空少 3 帧，整片单跑也一样 | 脚本在滤镜串前置 `tpad=stop_mode=clone` 补 2 个克隆帧，多出的由 `-t` 裁掉（实测帧数已与参考值一致） |
| 同一个 `-w` 只允许一个实例 | `flock` 单实例锁会立刻拒绝第二个实例（注意这是"实例之间"，与实例内 `-j` 并行无关） | 换 `-w`；或等前一个结束再原样重跑（已完成分片会自动跳过） |
| 收尾报 `Non-monotonic DTS ...`（已修） | concat 解复用器没有显式 `duration` 时只能**推断**上一片 TS 的时长；某片比邻片少 1 帧时下一片会提前约 1 帧落位，于是顶一次 DTS 并留下一个 ~11µs 的畸形帧间隔（实测 `-T 100 -L 20` 的 `p00000` 958 帧 vs 其余 959 帧 → 4 个接缝里 1 处；改动前 S02E05 各片等长 → 0 处）。成片 DTS 仍单调、不丢帧，残留影响仅该接缝后视频时间轴前移约 1 帧 | `parts.txt` 里每条 `file` 后补 `duration <该片 dt>`（与逐片编码同一算法，`sum(dt)==TOTAL`）。同一批分片重拼实测：告警 0 次、畸形间隔 0 处、帧数不变。只改拼接清单，老目录可直接用新脚本重拼 |
| `nvinterpolate` 会往 CWD 写日志 | 滤镜在**当前工作目录**下创建 `NvOFFRUC/logFRUCError.txt` | 脚本给每片一个独立 CWD（`runlogs/cwd/<片名>/`，成功即删），并发不打架、也不会再落到调用者目录。手工直接调 `ffmpeg` 时仍需自己注意 |
| GPU 成片"画面比声音快"约 62.56ms（已修） | 每片都要 `trim=start_frame=3` 丢掉 nvinterpolate 的无效前导；`-t` 又把它补满 L 秒 → 等价于**整条视频超前 3 个输出帧**（`48000/1001` 下 62.56ms），而音轨是原样 copy、时间戳没动。**接缝处不跳、偏移也不累积**（同帧号对齐 43.3dB vs 平移 3 帧 22.2dB） | 收尾给音轨 copy 起点补 `HEAD_TRIM/目标帧率`（脚本里的 `AUD_SS_OPTS`；CPU `trim=0` 不受影响）。只动音轨、不动分片，老目录重拼一次即可对齐。实测白帧+click：修复前 62.5ms → 修复后 0.0ms |
| 4K 的 `-j` 到底能开几路？ | 上限几乎总是**内存**，不是 CPU：实测单片峰值 RSS（`-threads 4` + libx265 medium）720p 0.61GB、1080p 1.16GB、**4K 4.59GB**（脚本画像已按此调成 0.7 / 1.3 / 5.0，取实测×1.1）。而 CPU 侧单片只吃 ~1 核（`minterpolate` 串行），所以核数不是瓶颈 | 8GiB 的 cgroup：4K `floor(5.57/5.0)=1` 路；32GB 上 `floor(25/5)=5` 路。想看脚本算出来的值，看日志的 `并发  : N 路 × M 线程/片（CPU 槽位 … × 内存 …）` 那一行 |
| `-w` 用**相对路径**会失败 | `PARTS=` 等变量在 WORKDIR 绝对化**之前**就赋值了，而每片会 `cd` 进自己的临时 CWD → 分片报 `Error opening output <相对路径>/parts/pXXXXX.ts.part.NNN` | 用绝对路径 `-w /path/work`（文档示例与回归测试都是绝对路径，所以这个坑一直没暴露） |
| Ctrl+C 之后任务好像还在跑 | ① Ctrl+C 只发给**终端前台进程组** —— 用 `setsid`（脚本推荐的起法）或 `&` 起的任务根本收不到；② 即使收到了，ffmpeg **捕获了 INT/TERM 但要 11–13s 才真的退出**（实测连 640x480 的 lavfi 编码都这样，4K 的 `minterpolate` 只会更久），旧代码 `kill -TERM` 之后就 `wait`，于是看起来像没反应 | 现在收尾有界且有日志：`中断: 还有 N 个 ffmpeg 在写 … → 先发 TERM`，3s 后 `优雅收尾超时 → SIGKILL`。想立刻停用 `kill -TERM <脚本pid>`（或 `-<pgid>` 连整个进程组） |
| `vidls` 的「硬解」档要用 ffmpeg 而不是 ffprobe | **ffprobe 没有 `-hwaccel` 选项**（实测 6.1.1：`Failed to set value 'cuda' for option 'hwaccel': Option not found`，连 `-nostdin` 也不认） | 档 2 改成 `ffmpeg -hwaccel cuda … -f null - -progress pipe:1`，取最后一行 `frame=N` |
| `vidls` 默认的「硬解」档对长片很贵 | 成本 = 0.55s 固定 + 时长/≈500fps（T4 实测），2 小时 1080p 约 7 分钟；而 6 种编码器/容器上「包数」与解码真值完全相等 | 长片 / 大目录加 `--cpu`（走 0.1s 的包数档）或 `--fast`（只读容器头） |
| T4 不能硬解 AV1 | `av1_cuvid` 解码器编译进来了，但 Turing 运行时报 `Codec av1_cuvid is not supported.` | 只对该编码器降级到包数档，同批的 h264/hevc/vp9 仍走硬解 |
| `vidls` 不实现 `-R` / `--color` | 列布局是"连续非视频项攒成一组铺网格，视频行独占一行"，递归与配色会破坏这个约定 | 需要时用真 `ls`；`vidls` 只覆盖单目录场景 |
| `vidls` 的软链是绝对路径 | 建的是 `/usr/local/bin/vidls -> /仓库/vidls.sh` | 仓库搬家后重跑 `vidls --install` |
| Windows 版 `-l` 少三列、mode 是合成值、`total` 只有近似 | Windows 的 `os.stat` 给不出属主/属组/硬链接数、没有 `st_blocks`，mode 只反映只读位（详见 [Windows 版](#windows-版vidlscmd--vidls_winpy)） | 这是移植的取舍：宁可不显示也不显示错的。需要真值就用 Git Bash 的 `ls -l` |
| Windows 版 `vidls.cmd` 是纯 ASCII、不改 PATH | cmd.exe 用 ANSI 代码页解析批处理（UTF-8 中文注释会破坏解析）；改 PATH 属系统级改动 | 中文都在 `.py` 内核里；PATH 不在位时只打印可粘贴的 PowerShell 命令 |
| 脚本被 `kill -9` 后还有 ffmpeg 在烧 CPU（孤儿） | 走不到 trap；`$!` 记的 pid 只活在 run_job 子 shell 里，子 shell 被打死就丢了。**更关键：子 shell 与 ffmpeg 都继承了锁的 fd 9** → 孤儿自己占着锁，旧逻辑下重跑一直撞锁、"启动清孤儿"永远走不到 | ①根因：子 shell / 试编码 subshell 里 `exec 9>&-`（锁不再外泄）；②启动时按「命令行里在写本目录 `parts/`」收残留（`TERM → 3s → KILL`）再清 `.part`；③撞锁兜底：只有 ffmpeg 持有 → 收掉再接管锁，否则报错并打印持有者 pid/启动时间/命令行 |

---

## 路线图（Roadmap）

VidUtils 规划作为一个**命令行优先 / Python 原生**的视频工程工具集，当前与后续模块：

| 模块 | 状态 | 说明 |
|---|---|---|
| `vidcrop_cpu_v0.py` | ✅ 已发布 | CPU 顺序裁剪（crop + cover） |
| `vidcrop_cpu_v1.py` | ✅ 已发布 | CPU 并发裁剪（crop + cover，自动并行） |
| `vidcrop_cpu_v2.py` | ✅ 已发布 | CPU 并发裁剪增强版（AV1/VP9、别名、preset 映射、`-ref` 基准、crop-ratio、crop-cover、color-range） |
| `vidcrop_hwaccel.py` | ✅ 已发布 | 硬件加速裁剪（CUDA/Vulkan/VA-API/OpenCL，6 级策略链；三种模式 crop / cover / crop-cover，CLI 与 v2 逐字对齐） |
| `convert_crf.py` | ✅ 已发布 | 质量换算单一事实来源 |
| `interp_2x_safe.sh` | ✅ 已发布 | 光流插帧 2x **GPU 专版**（`nvinterpolate` + `hevc_nvenc`，**无 CPU 回退**；**cgroup 感知的环境自动探测** + **分片级并行 `-j`** + **时间段截取 `--SS/--TO/-T`** + `setsid` + TS 分片 + 断点恢复 + 单实例锁） |
| `interp_2x_safe_v1.sh` | ✅ 已发布 | 同上的**通用版**：多一条 CPU 回退后端（`minterpolate` + `libx265`）与 `--backend` / `--cpu-preset`；其余特性（环境探测 / `-j` / `--SS/--TO/-T`）与 GPU 专版一致，两者的 `recipe.txt` 兼容、可互相接管分片目录 |
| `test/test_interp_2x_lock.sh` | ✅ 已发布 | 上面两版的回归测试（单实例锁 / 并发安全，退出码 0/1/2），默认 `SUT` 为 `interp_2x_safe.sh` |
| `test/test_interp_2x_orphan.sh` | ✅ 已发布 | 上面两版的回归测试（中断收尾 / 孤儿 ffmpeg 自愈，退出码 0/1/2），默认 `SUT` 为 `interp_2x_safe.sh` |
| `vidls.sh` + `vidls.py`（另有 `vidll.sh`） | ✅ 已发布 | `ls` / `ll` 替代品（`vidll` == `vidls -l`）：非视频按原生 `ls` 版式（实测逐字节一致），视频追加分辨率 / 帧率 / 比特率 / 编码器 / 容器 / 时长（帧数为可选列 `--show frames`）；帧数四级降级链（包头 → 硬解 → 包数 → 估算）+ cgroup 感知的自动并行 + `--install` 自检装机 |
| `vidls.cmd` + `vidls_win.py`（另有 `vidll.cmd`） | ✅ 已发布 | 上面的 Windows 移植（Linux 版原样保留、两者互不 import）：非视频版式在 Git Bash 下与 coreutils ls 8.32 **逐字节一致**（1040 组随机布局实测）+ 同样的四级降级链 + 控制台编码自适应 + 写启动器进 PATH 的 `--install` |
| `vidscale_*.py` | 🚧 规划中 | 视频缩放：双三次 / Lanczos / `scale_cuda` / `scale_npp` |
| `vidrepair_*.py` | 🚧 规划中 | 视频修复：容器修复、损坏帧跳过、时间戳重建、丢帧补偿 |
| `videnhance_*.py` | 🚧 规划中 | 视频增强：去噪、锐化、去隔行、HDR→SDR、AI 超分接入 |
| `vidutils-cli` | 💭 设想中 | 统一 CLI 入口，子命令分发 `vidutils crop / scale / repair / enhance …` |

**统一设计约束（所有后续模块都会遵守）：**

- 参数命名风格一致（`--input / --output / --overwrite / --decode / --ffmpeg-bin`）
- 批量语义一致（文件或目录均可作为 `--input`）
- 失败诊断一致（打印命令 + FFmpeg stderr 末 N 行）
- 进度与统计一致（实时进度条 + 文件级耗时 + 批量汇总 + 整批剩余 ETA）
- 质量换算统一走 `convert_crf.py`，不在各脚本里硬编码偏移

> **已知例外**：`interp_2x_safe.sh` 早于这套约定，输入/输出走**位置参数**（`<输入视频> [输出路径]`）
> 而不是 `--input / --output`；与位置参数无关的其余约定它都遵守（有 `--overwrite`、失败时给出
> 可诊断信息、失败非 0 退出，并行相关参数也照 Python 工具的命名：`-j/--jobs/--workers`、`--threads`、
> `--mem-per-job`、`--sequential`）。将来若要并入 `vidutils-cli`，需要先统一它的参数风格。

---

## 目录结构

```
vidutils/
├── README.md                 # 本文件
├── vidcrop_cpu_v0.py         # CPU 顺序裁剪（crop + cover，单进程）
├── vidcrop_cpu_v1.py         # CPU 并发裁剪（crop + cover，多任务并行）
├── vidcrop_cpu_v2.py         # CPU 并发裁剪增强版（推荐；AV1/VP9、别名、preset 映射、-ref 基准）
├── vidcrop_hwaccel.py        # 硬件加速裁剪（CUDA/Vulkan/VA-API/OpenCL，6 级策略链）
├── convert_crf.py            # 质量换算表（被 v2 / hwaccel 依赖，单一事实来源）
├── interp_2x_safe.sh         # 光流插帧 2x · GPU 专版（nvinterpolate + hevc_nvenc；环境探测 + -j 并行 + --SS/--TO/-T + TS 分片 + 断点恢复）
├── interp_2x_safe_v1.sh      # 同上的通用版（多一条 CPU 回退 minterpolate + libx265 与 --backend/--cpu-preset）
├── vidls.sh                  # ls / ll 替代（启动器；--install 把自己接进 PATH）
├── vidll.sh                  # vidll == vidls -l，只有 3 行转发逻辑
├── vidls.py                  # 上面的内核（纯标准库）：列布局、资源探测、帧数四级降级链（Linux 版）
├── vidls.cmd                 # 同上 Windows 版启动器（纯 ASCII + CRLF：cmd.exe 按 ANSI 代码页解析批处理）
├── vidll.cmd                 # Windows 版 vidll（只转发给 vidls.cmd）
├── vidls_win.py              # Windows 版内核：版式判据按实测重写、控制台编码自适应、写启动器的 --install
├── test/                     # 全部回归测试（基线在 test/baseline/，见「回归与验证」）
│   ├── dump_filter_chains.sh        # 裁剪脚本：滤镜链回归（16 行基线）
│   ├── dump_cmd_default.sh          # 裁剪脚本：命令级回归（7 用例基线）
│   ├── baseline/                    # 上面两个的基线文件
│   ├── test_interp_2x_lock.sh       # 插帧脚本：单实例锁 / 并发安全
│   └── test_interp_2x_orphan.sh     # 插帧脚本：中断收尾 / 孤儿 ffmpeg 自愈
├── verify/                   # 裁剪脚本的验证套件（单测 + CLI 层，都不需要 GPU）
├── probe/                    # 上机探针：T4 实测用（无 GPU 时只能跑 SELFTEST=1）
├── memory/                   # 工程记忆：工具背后的事实与踩坑，索引见 memory/MEMORY.md
├── AV1_VP9_UPGRADE_PLAN_v2.md # AV1/VP9 升级方案归档
├── docs/                     # （规划）设计文档与性能基准
├── examples/                 # （规划）示例素材与演示脚本
└── temp/                     # 本机临时目录（gitignored）：测试素材、中间产物、探针日志
```

---

## 回归与验证

改 `vidcrop_*.py` 的滤镜链、策略生成或参数落地之后，按这个顺序跑（**都不需要 GPU**）：

```bash
# 1) 回归门：不传新参数时命令必须逐字不变
bash test/dump_filter_chains.sh > /tmp/after.txt
diff test/baseline/chains_before.txt /tmp/after.txt     # 16 行（8 用例 × 2 脚本）
bash test/dump_cmd_default.sh > /tmp/after2.txt
diff test/baseline/cmd_before.txt /tmp/after2.txt       # 7 个命令级用例

# 2) 验证套件
python verify/verify_cuda_scale.py        # CUDA 缩放链 + 策略生成
python verify/verify_scale_algo.py        # --scale-algo 解析
python verify/verify_pixfmt_bitdepth.py   # --pix-fmt × --bit-depth 的「能落地者赢」
python verify/verify_cuda_decode_codec.py # 按源编解码器的硬解确认（AV1）
python verify/verify_hwupload_worth.py    # auto 缩放的 hwupload 门槛
bash   verify/verify_decode_axis.sh       # CLI 层三轴正交（15 项）
```

插帧脚本的回归（**需要 GPU + `nvinterpolate`**，会抢一点 GPU；退出码 0/1/2）：

```bash
bash test/test_interp_2x_lock.sh      # 单实例锁 / 并发安全
bash test/test_interp_2x_orphan.sh    # 中断收尾 / 孤儿 ffmpeg 自愈
```

上机探针（**需要 NVIDIA GPU**；没有就只能跑装置自检）：

```bash
SELFTEST=1 bash probe/probe_scale_cuda_crop.sh             # 只验计时/汇总装置，CPU-only
PROBE_VMAF=1 bash probe/probe_scale_cuda_crop.sh <源视频>   # 完整判据 A/B/C/D/Q
```

> 测试素材与探针工作目录都在 `temp/`（gitignored），**不入库**；
> `verify/*.py` 需要 fixture 时会用 lavfi 按需生成。

---

## 工程记忆（memory/）

`memory/` 放的是**工具背后的事实与踩坑**，不是 API 文档 —— 用法看本文件，为什么这么做、踩过什么坑看那里。
索引在 [`memory/MEMORY.md`](memory/MEMORY.md)。当前条目：

- [长时 ffmpeg 任务必须 setsid 分离 + 别直接写 MP4](memory/project_long_ffmpeg_jobs.md)
  —— 宿主崩溃会带走同进程组的 ffmpeg；MP4 缺 moov 整份作废（已发生过一次，34 分钟算力白跑）；
  并发实例互删临时文件；以及 `interp_2x_safe.sh` 里那套防呆（锁 / 两层复用校验 / PID 临时名）；
  2026-09-15 起该脚本又加了 cgroup 感知的环境探测、分片级并行 `-j`、时间段截取 `--SS/--TO/-T`；
  GPU 专版与通用版 `interp_2x_safe_v1.sh` 的 recipe 兼容、可互相接管分片目录；
  收尾 concat 的 `Non-monotonic DTS`（某片比邻片少 1 帧）已用 `parts.txt` 的 `duration` 指令修掉；
  片头 `trim=3` 让视频超前 3 个输出帧（接缝不跳、不累积），已给音轨 copy 起点补同量对齐；
  CPU auto 并行度按实测收敛到「每路 1 核」（三轮 4→2→1：插帧滤镜串行、单片 ≈1 核）并感知 cgroup 已有负载；
  `--threads` 翻成 `-x265-params pools=N`（`-threads` 只改 frame threads、`-filter_complex_threads` 对 minterpolate 无效）；`-w` 相对路径的坑已修；
  中断收尾改为有界（TERM→3s→SIGKILL）并能在下次启动自愈孤儿 ffmpeg（ffmpeg 对 TERM 要 11–13s 才退，实测）；
  并发度按「剩余待编片数」再收敛、撞锁报错打印持有者 pid/启动时间/命令行、新增 test/test_interp_2x_orphan.sh；
  另有一条实测教训：**别"原位"改正在被执行的脚本**（bash 会按字节偏移重读、把跑完的循环再跑一遍）
- [bash 并行调度的四个坑](memory/project_bash_parallel_pitfalls.md)
  —— `wait -n` 会提前返回、不能当完成信号；`while read < <(tail)` 能永久卡死在 pipe_read（0% CPU）；
  `exec` 出去的子任务被杀会留孤儿；找"另一个实例"要读 `/proc/<pid>/cmdline` 精确匹配
- [FFmpeg 7.1 已合并 nvinterpolate 与 libvmaf](memory/project_nvinterpolate_build.md)
  —— 单一 ffmpeg、无需环境文件；`nvinterpolate` 必须放滤镜链末尾否则段错误；移植补丁位置
- [两个裁剪脚本的行为一致约定](memory/project_preset_equivalence.md)
  —— 两个裁剪脚本的 NVENC↔x264 preset 表必须一致（曾错位一档，同一条 `--preset p5` 会落不同档）；
  降级到 CPU 编码器时基准档取"请求的编码器"的默认值再换算，保持档位等效；
  概览块只展示最终命令里真正会出现的参数；`--codec auto` 必须解析成具体编码器（透传会报 Unknown encoder）
- [ffmpeg 挂起的两个根因](memory/project_ffmpeg_stdin_hang.md)
  —— SIGTTIN（状态 T，`-nostdin` 能修）vs 输出管道反压（状态 S 且 CPU 冻结，`-nostdin` 没用）
- [复刻 ls 版式与视频属性探测的踩坑](memory/project_ls_probe_pitfalls.md)
  —— `vidls` 的实测事实：coreutils 列算法的**三条真判据**（竖填、总宽**严格 `<`**、
  **最后一列不能为空**）与 Tab 填充的取舍规则，并**推翻了原先记的「列宽下限 3」**；
  `ffprobe` 没有 `-hwaccel` / `-nostdin`（硬解数帧只能用 ffmpeg + `-progress pipe:1`）；
  帧数四档（包头 / 硬解 / 包数 / 估算）的可信度差异；AV1 降级实测；
  Windows 移植必须处理的六件事（**CRLF 会让 diff 全红**、`.cmd` 必须纯 ASCII、
  编码跟随控制台、`-l` 少三列、`total` 只能近似、CUDA 关键字要带 `.dll`）
- [T4 能力边界 + 测性能前先查并发流水线](memory/project_t4_gpu_capabilities.md)
  —— 别的流水线会抢 CPU/GPU 导致基准不可信；**VP9 有硬解但从来没有硬编**、
  **AV1 硬解硬编都没有**（`av1_cuvid` 在列表里，运行时报 not supported）；
  「本机 AV1 完全编不出来」这条已更正为：custom `ffmpeg` 7.1 没有 AV1 软编，
  但系统 `ffmpeg` 6.1.1 有 `libsvtav1`/`libaom-av1`；零拷贝管线里 `-pix_fmt` 无效

写法沿用本机 codebuddy 自动记忆的约定：frontmatter 带 `name` / `description` / `type`，
正文对 project / feedback 类用「事实 → **Why:** → **How to apply:**」的结构，
方便日后判断这条记忆是不是还成立。

---

## 常见问题（FAQ）

**Q1：v0、v1、v2 和 hwaccel 如何选择？**

无 GPU 或批量 CPU 处理 → **v2**（功能最全，v1 的超集）；有 NVIDIA / AMD / Intel GPU、追求吞吐 → **hwaccel**；要对照历史行为 → v0 / v1。v2 与 hwaccel 功能基本对齐（cover、递归、音频重编码、dry-run、日志、extra-args、`-ref` 基准均支持），区别在是否有硬件解码与策略链。

**Q2：crop / cover / crop-cover 三种模式有什么区别？**

| 模式 | 滤镜链 | 目标尺寸限制 |
|---|---|---|
| `crop`（默认） | `crop=W:H:x:y` | 目标不得大于源尺寸 |
| `cover` | `scale=…,crop=W:H`（先缩放再居中裁剪） | 任意尺寸，比例按目标算 |
| `crop-cover` | `crop=…,scale=…`（先裁剪再缩放覆盖） | 任意尺寸，裁剪比例由 `--crop-ratio` 单独决定 |

> 两条涉及缩放的链里的 `scale` **默认显式带 `:flags=lanczos`**（`scale=…:flags=lanczos,crop=…`）。
> libswscale 的默认是 `bicubic`（实测：`scale=W:H` 与 `scale=W:H:flags=bicubic` 的帧级 MD5 完全相同），
> 而 `scale_cuda` 只到 `lanczos` 一档——取两边交集里的最高档并显式钉死，让 GPU 路径与 CPU 降级路径同档。
> 想换档用 `--scale-algo`（如 `--scale-algo libswscale-spline`）；
> 代价：CPU 侧比 bicubic 略慢（`lanczos` 抽头更多）。

`crop-cover` 与 `cover` 的区别是**裁剪比例可以独立于最终尺寸**：`--crop-ratio` 定裁剪比例，
`--output-width/height` 定缩放后的最终尺寸。因此该模式下两者可以并用，且**只给一个维度即可**
（另一个按比例推导为偶数）：

```bash
# 两种写法等价（结果都是按 16:9 裁剪后缩放到 1280x720）
--mode crop-cover --crop-ratio 16:9 --output-width 1280 --output-height 720
--mode crop-cover --crop-ratio 16:9 --output-width 1280

# 不给 --crop-ratio 时裁剪比例就是目标宽高比，两个维度都必须给
--mode crop-cover --output-width 1280 --output-height 720
```

`cover` 模式的缩放现在可以留在显存里，有两种形态：

- **零拷贝**（有硬解、编码器是 NVENC）：`scale_cuda → 显式 hwdownload → CPU crop → NVENC`（策略 2）——实测 4K→1440×1080 比 CPU 侧 `scale` 快 **51.9%~52.9%**（对着旧 bicubic 基准是 44.5%），
质量门 `PSNR(GPU lanczos vs CPU lanczos) = 46.60 dB`、`VMAF = 97.21`（差异在感知上可忽略）。
- **软解 + 上载**（`--decode cpu --scale-algo cuda-*`）：`hwupload_cuda → scale_cuda → 显式 hwdownload → CPU crop`（策略 2b）。缩放轴与解码轴正交，硬解用不了时也能把重采样放进显存。
  T4 实测（两轮一致）：比「软解 + CPU 缩放」**快 2.9%~3.2%**；判据 C 确认 crop 尺寸协商正确（输出 640×360，
  没被静默丢弃）；判据 E 确认画质与零拷贝链**逐位相同**（PSNR 都是 46.603743 dB）。
  ⚠ 但**绝对值**要看清：软解链路整体 44~46s，硬解零拷贝只要 12.8s —— 这条链的定位是
  「NVDEC 用不了 / 解不了该编码时的出路」，不是替代硬解。

两处细节是刻意的：**必须显式写 `hwdownload,format=…`**（依赖 FFmpeg 自动插入时 `crop` 会被静默丢弃——实测
`scale_cuda=1280:720,crop=iw/2:ih/2` 输出 1280×720 而不是 640×360，且没有任何报错）；`interp_algo` 也显式钉
`lanczos`（`scale_cuda` 的默认值是 0，未映射到具名档）。

上载用的是 **`hwupload_cuda`** 而不是通用 `hwupload`：后者必须配 `-filter_hw_device`，否则报
`A hardware device reference is required`；`hwupload_cuda` 自带 device，不需要额外的命令行管道。

CPU 侧同一天也钉成了 `:flags=lanczos`（两个脚本一起改），**两条路同档**——否则同一批文件会在 GPU 上更锐、
一旦降级到 CPU（无 `scale_cuda` 的构建 / 显式 `--scale-algo libswscale-*` / 三轴全 CPU / 新链失败）就变软。

`crop-cover` 仍是 CPU 侧 `crop,scale`：它要先裁剪，而裁剪只能在 CPU（`crop_cuda` 不存在），GPU 缩放得额外
`hwupload_cuda` 一次。crop 模式的全 GPU 流水线（策略 1）因为 `crop_cuda` 不存在，实际永远跳过。

另外 `crop-cover` 的同尺寸跳过判定会先看裁剪步骤是否为空操作：裁剪比例与源比例不同时，
即使最终尺寸等于源尺寸也会改变画面，因此**不会**被跳过（`--no-skip-same-size` 可强制转码）。

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

本环境（`crop_cuda` 不可用）的 crop 模式实际流水线是 `CUDA 硬解 → hwdownload → CPU 侧 crop → 回传显存 → NVENC 硬编`，CPU 段在关键路径上；NVENC 跑到 1300+ fps 时每帧只摊到约 0.7 ms CPU 预算。8 核机器上任何一个吃满 CPU 的并发作业（典型：算 SSIM/PSNR 的 `-lavfi ssim` 质量对比、另一个转码任务）就会把它从约 47x 拖到约 20x。

`cover` 模式原先更吃 CPU——它比 crop 多一步 `scale`（实测 4K→1440×1080 上 CPU 侧缩放单独占 **28.4%**，lanczos；换成 bicubic 时是 17.2%）。现在若 FFmpeg 带 `scale_cuda`，缩放搬进显存后这一步不再占 CPU，同一条链从 26.65s 降到 12.82s（**快 51.9%**）。

> 顺带说明排查时的**参数命名**：解码轴参数是 `--decode`（旧名 `--hwaccel` 已硬更名）。`--decode cpu` 只关解码，不等于纯 CPU；要确认当前到底走了哪条链，看概览块的「缩放」与「组合」两行。

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
