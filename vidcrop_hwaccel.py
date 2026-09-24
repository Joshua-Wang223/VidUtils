#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vidcrop_hwaccel.py — 基于 FFmpeg 的视频批量裁剪工具（硬件加速版）

功能概述
────────────────────────────────────────────────────────────────────
  • 单文件 / 文件夹批量处理；-r 递归扫描子目录并保持目录结构
  • 三种处理模式：
      - crop （默认）：直接居中裁剪（目标尺寸不得大于源尺寸）
      - cover：等比缩放至完全覆盖目标区域后居中裁剪（任意尺寸）
      - crop-cover：先对源画面做裁剪，再把裁剪结果缩放覆盖到最终尺寸（可放大）。
                    裁剪步骤的比例由 --crop-ratio 决定，未给出时即目标宽高比。
  • --crop-ratio 自动按目标宽高比（如 16:9）最大化裁剪，无需指定输出尺寸；
    与 --output-width/height 并用时前者定画面比例、后者定分辨率，可只给一个维度
    （另一个按比例推导为偶数；crop / cover 下该尺寸即最终尺寸，
    crop-cover 下是裁剪后缩放覆盖的目标尺寸）
  • --scale-algo 选缩放算法，写法 <backend>-<algo> 或裸 <algo>（后端自动）：
        libswscale-*  fast_bilinear bilinear bicubic neighbor area bicublin
                      gauss sinc lanczos spline
        cuda-*        nearest bilinear bicubic lanczos（仅 cover 模式，
                      需 --enable-cuda-nvcc 的自建 FFmpeg，走显存内缩放）
    默认裸 lanczos：GPU 可用则 cuda-lanczos、否则 libswscale-lanczos。
    前缀用于**强制**后端；裸名字要求两个后端都认，只在一个后端有的算法
    必须带前缀（如 libswscale-spline）。crop 模式不做缩放，该参数不生效。
  • 编码格式覆盖 H.264 / H.265 / VP9 / AV1：
      - H.264/HEVC：libx264 / libx265（CPU）、h264_nvenc / hevc_nvenc（GPU）
      - AV1：libsvtav1 / libaom-av1 / librav1e（CPU）、av1_nvenc（GPU 策略 1/2）
      - VP9：libvpx-vp9（CPU）。NVENC 无 VP9 编码器，故 VP9 的"硬件路径"
        是「硬件解码 + CPU 编码」，仍比纯 CPU 路径省掉解码开销
  • 音频默认流复制（--audio-codec copy），可指定重编码（aac / libopus 等）
  • 同尺寸自动跳过（--no-skip-same-size 可强制转码）
  • --dry-run 预览最优策略命令，不执行转码
  • --log 将所有终端输出同时写入日志文件
  • --extra-args 在 FFmpeg 命令末尾追加自定义参数
  • Ctrl+C 安全中断，自动清理 FFmpeg 子进程

三个正交轴（互不干涉，可任意组合）
────────────────────────────────────────────────────────────────────
  解码 --decode       auto(默认) / cuda / vulkan / vaapi / opencl / cpu
  缩放 --scale-algo   auto(默认) / libswscale-<algo> / cuda-<algo>
  编码 --codec        任意编码器（默认 h264_nvenc）
  策略 --fallback-policy  auto(默认) / strict —— 只回答「显式点名的后端
                      不可用/失败时降级还是报错」，与前三个轴正交。

  轴之间没有任何冲突检查：--decode cpu --codec h264_nvenc（软解 + NVENC 硬编）
  与 --decode cuda --codec libx264（硬解 + 软编）都合法。
  ⚠ --decode cpu 只关解码，**不再等于纯 CPU**；纯 CPU 请写
    --decode cpu --scale-algo libswscale-lanczos --codec libx264
  ⚠ 旧名 --hwaccel 已硬更名为 --decode（取值 none → cpu），用旧名直接报错。

硬件加速运行时行为
────────────────────────────────────────────────────────────────────
  • 运行时探测（非编译字符串匹配）下列能力：
        CUDA 解码、h264_nvenc、hevc_nvenc、crop_cuda / scale_cuda 滤镜、
        Vulkan、VA-API、OpenCL。
        探测按轴按需进行：三轴都显式指向 CPU 时跳过全部 GPU 探测。
  • --scale-algo auto（默认）= 优先 cuda、失败回退 cpu。判定用**功能探针**
    （真跑 1 帧 hwupload_cuda,scale_cuda → null），只在 auto 且零拷贝路径
    不可用时才跑；显式 --scale-algo cuda-* / libswscale-* 不探针、直接执行。
  • 按优先级自动生成策略链并依次尝试，前一级失败自动降级（--fallback-policy
    strict 时不追加降级策略，失败即报错退出 2）：
        1) CUDA 全流水线（硬解 + crop_cuda + NVENC 硬编）—— 仅 crop 模式。
           注意 crop_cuda 在 FFmpeg 上游并不存在（与编译选项无关），
           所以这条在本项目所有环境下都会跳过
        2) CUDA 缩放 + CPU 裁剪（硬解 + scale_cuda + 显式 hwdownload + CPU crop
           + NVENC 硬编）—— 仅 cover 模式；crop 只能回 CPU，因为 crop_cuda 不存在
        2b) CUDA 缩放 + CPU 裁剪（**软件解码** + hwupload_cuda + scale_cuda +
           显式 hwdownload + CPU crop）—— 仅 cover 模式，且只在
           1) / 2) 都没法用、而功能探针证明 CUDA 缩放能跑时才插入
        3) 解码轴给的后端（auto → -hwaccel auto）+ NVENC 硬编（CPU 做 vf 滤镜）
        4) 指定硬解（cuda/vulkan/vaapi/opencl）+ 软件编码
        5) auto 模式下的最佳硬解 + 软件编码
        6) 纯 CPU 处理

        注：crop 模式从第 1 条起、cover 从第 2 条起、crop-cover 从第 3 条起
            （crop-cover 要先裁剪，GPU 缩放得额外 hwupload_cuda 一次，未纳入）。

编码器智能处理
────────────────────────────────────────────────────────────────────
  • 名称别名自动归一化：h265_nvenc→hevc_nvenc, x264→libx264, vp9→libvpx-vp9,
    av1→libaom-av1, svtav1→libsvtav1 …
  • preset 在 NVENC（p1~p7）与 libx264（ultrafast~veryslow）之间双向映射；
    libsvtav1 的 preset 是 0~13 整数（传 'medium' 会直接报错），自动换算
  • CPU 编码器使用 -crf（默认 21），GPU 编码器使用 -cq（默认 23）
  • 降级时 --cq 通过 cq_to_crf() 做等效视觉质量映射，而非直接透传
  • 质量参数两种给法，二者互斥（混用拒绝执行）：
      --crf / --cq     字面量原样下发给目标编码器，不换算
      --crf-ref N      以 libx264 CRF 为统一基准，按等效表换算
      --cq-ref N       以 h264_nvenc CQ 为统一基准，按等效表换算
    等效表见同目录 convert_crf.py；例：--crf-ref 21 → libvpx-vp9 -crf 27、
    libsvtav1 -crf 27、libx265 -crf 24、hevc_nvenc -cq 28
  • 默认值：h264_nvenc + --cq 23 + --preset p5；无 NVENC 自动降级为
    libx264 + --crf 21 + --preset medium（preset 按"请求的编码器"的默认档换算到
    各策略实际用的编码器，降级前后档位等效，如 av1_nvenc p5 → libsvtav1 8）
  • 速度档位自动取值：libaom-av1 的 -cpu-used 与 libsvtav1 的 -preset 按 CPU 核数
    自动选档（ffmpeg 给 libaom-av1 的默认 -cpu-used=1 慢到不可用，实测仅 1fps）
  • librav1e 没有 -crf（实测传 -crf 只会被 ffmpeg 静默忽略），自动换算为等效 -qp
  • 输出为 .webm（VP9/AV1 默认容器）时，若源音轨是 AAC 等非 WebM 格式，
    自动改为 Opus 重编码——否则 ffmpeg 写头直接失败

用法示例
────────────────────────────────────────────────────────────────────
  # 1) 单文件 · GPU 编码（NVENC H.265 + CQ）
  python vidcrop_hwaccel.py \\
      --input video.mp4 --output out.mp4 \\
      --output-width 1280 --output-height 720 \\
      --codec hevc_nvenc --cq 20 --preset p5

  # 2) 批量批量 · cover 模式（等比缩放+裁剪，保持目录结构）
  python vidcrop_hwaccel.py \\
      --input ./raw --output ./out \\
      --output-width 1280 --output-height 720 \\
      --mode cover --codec libx264 --crf 20 --overwrite

  # 2b) AV1 · GPU 编码（av1_nvenc + CQ，需 Ada/RTX40 及以上）
  python vidcrop_hwaccel.py \\
      --input ./raw --output ./out --recursive \\
      --output-width 1920 --output-height 1080 \\
      --codec av1_nvenc --cq 24 --preset p5

  # 2c) AV1 · CPU 编码（libsvtav1；不可用 GPU 时 av1_nvenc 也会自动降级到这里）
  python vidcrop_hwaccel.py \\
      --input video.mp4 --output out.mp4 \\
      --output-width 1280 --output-height 720 \\
      --codec svtav1 --crf 30 --preset medium

  # 2d) VP9 · 硬件解码 + CPU 编码（NVENC 无 VP9 编码器）
  python vidcrop_hwaccel.py \\
      --input video.mp4 --output out.webm \\
      --output-width 1280 --output-height 720 \\
      --codec vp9 --crf 32 --decode cuda

  # 2e) 统一质量基准：--crf-ref 以 libx264 CRF 为轴，按等效表换算到目标编码器
  python vidcrop_hwaccel.py \\
      --input ./raw --output ./out \\
      --output-width 1920 --output-height 1080 \\
      --codec libvpx-vp9 --crf-ref 21      # → -crf 27

  # 2f) crop-cover 模式：先按 16:9 裁剪，再把结果缩放覆盖到 1920x1080
  python vidcrop_hwaccel.py \\
      --input video.mp4 --output out.mp4 \\
      --mode crop-cover --crop-ratio 16:9 \\
      --output-width 1920 --output-height 1080

  # 2g) crop-cover + 单维度：--crop-ratio 已定比例，只给宽度即可（高度自动推导）
  python vidcrop_hwaccel.py \\
      --input video.mp4 --output out.mp4 \\
      --mode crop-cover --crop-ratio 16:9 --output-width 1280   # → 1280x720

  # 2h) 其余模式 + --crop-ratio + 单维度：比例定形状、尺寸定分辨率（→ 1920x1080）
  python vidcrop_hwaccel.py \\
      --input video.mp4 --output out.mp4 \\
      --mode cover --crop-ratio 16:9 --output-height 1080

  # 3) 递归扫描 + 音频重编码 + 追加参数
  python vidcrop_hwaccel.py \\
      --input ./footage --output ./out \\
      --output-width 1920 --output-height 1080 \\
      --recursive --audio-codec aac --audio-bitrate 192k \\
      --extra-args -- -max_muxing_queue_size 4096

  # 4) Dry-run 预览命令（不执行转码）
  python vidcrop_hwaccel.py \\
      --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --dry-run

  # 5) 指定日志文件 + 三轴全 CPU（CI/容器环境；也等价于旧的 --hwaccel none）
  python vidcrop_hwaccel.py \\
      --input ./clips --output ./out \\
      --output-width 1280 --output-height 720 \\
      --decode cpu --scale-algo libswscale-lanczos --codec libx264 \\
      --log process.log

  # 5b) 软解 + 显存内缩放（需要自带 scale_cuda 的自建 FFmpeg）
  python vidcrop_hwaccel.py \\
      --input ./clips --output ./out \\
      --output-width 1280 --output-height 720 \\
      --decode cpu --scale-algo cuda-lanczos --codec hevc_nvenc

  # 6) 显式提供原始分辨率（跳过 ffprobe 探测，适合超大批量）
  python vidcrop_hwaccel.py \\
      --input ./videos --output ./cropped \\
      --original-width 3840 --original-height 2160 \\
      --output-width 1920 --output-height 1080 \\
      --codec hevc_nvenc --cq 22

  # 7) 自定义 FFmpeg 路径 + 手动指定容器
  python vidcrop_hwaccel.py \\
      --input ./raw --output ./out \\
      --output-width 1280 --output-height 720 \\
      --codec libx264 --crf 18 --container .mkv \\
      --ffmpeg-bin /opt/ffmpeg/bin/ffmpeg
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

# 各编码器之间的质量等效换算表（以 libx264 CRF 为基准轴）来自同目录的
# convert_crf.py，两个裁剪脚本共用同一套换算，避免各处硬编码偏移互相矛盾。
# 只在"GPU 编码器降级为 CPU 编码器"这一条路径上使用；用户显式给出的同族参数
# （CPU 的 --crf、GPU 的 --cq）一律原样下发。
try:
    from convert_crf import convert_quality, from_x264_crf, to_x264_crf
except ImportError:                       # 从其他工作目录启动时 sys.path 未必含本脚本所在目录
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from convert_crf import convert_quality, from_x264_crf, to_x264_crf

# ═══════════════════════════════════════════════════════════════════
#  常量定义
# ═══════════════════════════════════════════════════════════════════

VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv',
    '.m4v', '.webm', '.ts', '.mpg', '.mpeg',
}

# 编码器名称别名映射（常见拼写 → FFmpeg 实际名称）
CODEC_ALIASES = {
    'h265_nvenc':       'hevc_nvenc',
    'h265_amf':         'hevc_amf',
    'h265_qsv':         'hevc_qsv',
    'h265_videotoolbox':'hevc_videotoolbox',
    'x264':             'libx264',
    'x265':             'libx265',
    'nvenc':            'h264_nvenc',
    'nvenc_h264':       'h264_nvenc',
    'nvenc_h265':       'hevc_nvenc',
    'nvenc_hevc':       'hevc_nvenc',
    # VP9 / VP8
    'vp9':              'libvpx-vp9',
    'vp8':              'libvpx',
    'libvpx_vp9':       'libvpx-vp9',
    # AV1
    'av1':              'libaom-av1',
    'av01':             'libaom-av1',
    'svtav1':           'libsvtav1',
    'svt-av1':          'libsvtav1',
    'libsvt-av1':       'libsvtav1',
    'rav1e':            'librav1e',
    'nvenc_av1':        'av1_nvenc',
    'av1_nvenc':        'av1_nvenc',
}

# 编码器 → 推荐容器扩展名
CODEC_CONTAINER_MAP = {
    'libx264':    '.mp4',
    'libx265':    '.mp4',
    'h264_nvenc': '.mp4',
    'hevc_nvenc': '.mp4',
    'h264_amf':   '.mp4',
    'hevc_amf':   '.mp4',
    'h264_qsv':   '.mp4',
    'hevc_qsv':   '.mp4',
    'libvpx-vp9': '.webm',
    'libvpx':     '.webm',
    'vp9_qsv':    '.webm',
    'libaom-av1': '.mp4',
    'libsvtav1':  '.mp4',
    'librav1e':   '.mp4',
    'av1_nvenc':  '.mp4',
    'av1_qsv':    '.mp4',
    'av1_amf':    '.mp4',
    'prores':     '.mov',
    'prores_ks':  '.mov',
    'mpeg4':      '.mp4',
    'libxvid':    '.avi',
    'mjpeg':      '.avi',
    'copy':       None,
}

# 支持 -preset 的编码器集合
# 注意：libaom-av1 / libvpx-vp9 / librav1e 都没有 -preset（分别是 -cpu-used /
# -deadline / -speed），传了只会被静默忽略，故不纳入，避免出现"设了但没生效"。
PRESET_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
    'h264_amf',   'hevc_amf',   'av1_amf',
    'h264_qsv',   'hevc_qsv',   'av1_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox',
    # libsvtav1 的 -preset 是 0~13 的整数，与上面几类的取值完全不同，
    # 由 normalize_preset() 单独换算（见 NVENC_TO_SVTAV1_PRESET）。
    'libsvtav1',
}

# 支持 -crf 的编码器集合（CPU 软件编码器）
CRF_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'libvpx-vp9', 'libvpx',
    'libaom-av1', 'libsvtav1', 'librav1e',
}

# 支持 -cq 的编码器集合（GPU 硬件编码器）
CQ_SUPPORTED_CODECS = {
    'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
    'h264_amf',   'hevc_amf',   'av1_amf',
    'h264_qsv',   'hevc_qsv',   'av1_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox',
}

# NVENC 家族：p1~p7 风格 preset + -cq 质量控制，可走"硬解 + NVENC 硬编"策略。
# AV1 与 H.264/HEVC 同属此族（av1_nvenc 同样是 NVENC 封装）。
NVENC_CODECS = {'h264_nvenc', 'hevc_nvenc', 'av1_nvenc'}

# libsvtav1 的 -preset 是 0~13 的整数（越大越快、质量越低），**不接受**
# ultrafast~veryslow 或 p1~p7 这类名字——实测传 'medium' 直接报
# "Unable to parse option value"，故必须单独换算。
X264_TO_SVTAV1_PRESET = {
    'ultrafast': 12, 'superfast': 11, 'veryfast': 10, 'faster': 9,
    'fast': 8, 'medium': 8, 'slow': 6, 'slower': 4, 'veryslow': 2,
    'placebo': 0,
}
NVENC_TO_SVTAV1_PRESET = {
    'p1': 12, 'p2': 11, 'p3': 10, 'p4': 9, 'p5': 8, 'p6': 6, 'p7': 4,
}

# NVENC preset ↔ libx264 preset 双向映射表。
# 必须与 vidcrop_cpu_v2.py 的同名表一致：两张表错位会让同一条 `--preset p5`
# 在两个脚本里落到不同档位（历史上这里错位一档：p4→medium / p5→slow，
# 而 cpu_v2 是 p4→faster / p5→medium / p6→slow，已按 cpu_v2 对齐）。
# 对应关系是把 NVENC 的 7 档均匀铺在 x264 阶梯上，两端各留一档（faster 起、veryslow 止）。
NVENC_TO_X264_PRESET = {
    'p1': 'ultrafast',
    'p2': 'superfast',
    'p3': 'veryfast',
    'p4': 'faster',
    'p5': 'medium',
    'p6': 'slow',
    'p7': 'veryslow',
}

DEFAULT_CRF = 21
DEFAULT_CQ  = 23

# 未指定 --preset 时的默认预设：CPU 软件编码器 medium，GPU 硬件编码器 p5，
# libsvtav1 为 8（0~13 整数中速度与质量的平衡点）
DEFAULT_PRESET_CPU = 'medium'
DEFAULT_PRESET_GPU = 'p5'
DEFAULT_PRESET_SVTAV1 = '8'

# ── 码率控制轴：--rc-mode / --qp / --lookahead / --bitrate ────────────────
# 写法沿用 --scale-algo 的 `<backend>-<取值>` / 裸 `<取值>` 约定。本轴只有
# NVENC 一个后端——`-rc` 是 NVENC 专属选项，libx264 / libx265 没有"码率控制模式"
# 这个开关（它们用 -crf / -b:v / -qp 的组合来表达），故按"单后端时前缀可省"
# 处理：裸名与 `nvenc-` 前缀都收。
_RC_BACKEND = 'nvenc'
_RC_MODES = ('constqp', 'vbr', 'vbr_hq', 'cbr', 'cbr_hq', 'cbr_ld_hq')
_RC_MODE_HELP = ('nvenc：' + ' '.join(_RC_MODES)
                 + '\n  （constqp=恒定 QP（配 --qp）；vbr / vbr_hq=可变码率；'
                   'cbr / cbr_hq / cbr_ld_hq=恒定码率（配 --bitrate））')
# 允许与 --bitrate 共存的 rc 模式。含 auto（= 不下发 -rc，由 preset 决定的 VBR）。
# constqp 不在其中：它是恒定 QP 模式、**会完全无视 -b:v**（T4 实测，见仓库
# memory/project_t4_gpu_capabilities.md），共存等于静默丢掉用户给的码率 → CLI 层报错。
_RC_MODES_WITH_BITRATE = ('auto',) + tuple(m for m in _RC_MODES if m != 'constqp')
# lookahead 范围：x264 的上限就是 250；NVENC / x265 无上限，250 足够且统一。
_LOOKAHEAD_RANGE = (0, 250)
# --qp 与 --cq 同量纲（NVENC 的 -qp 是 0~51）
_QP_RANGE = (0, 51)
# 码率写法：ffmpeg 记法，如 8M / 8000k / 12000000（裸数字按 bps 解释）
_BITRATE_RE = re.compile(r'^\d+(\.\d+)?[kKmM]?$')
# 量程报错里的"为什么"必须与 vidcrop_cpu_v2.py 逐字一致（报错首行可对比）
_LOOKAHEAD_HINT = f'x264 的上限就是 {_LOOKAHEAD_RANGE[1]}；不指定=沿用各编码器默认'
_QP_HINT = '与 --cq 同量纲（NVENC 的 -qp 量程）'

# [借鉴1] NVENC 策略失败时的 preset 降档重试表：只降不升，p4 及以下不再重试。
# 对照 Video_Enhancement 的 SDK 路径——InitializeEncoder 返回 code=8（驱动不认该
# preset）时降到 p4 重试一次、RC/LA 不变（见其 main.py _setup_level1_nvenc）。
# p4 是实测在 T4/旧驱动上被接受的安全档。默认档是 p5，故 p5/p6/p7 都映射到 p4。
_NVENC_PRESET_RETRY = {'p5': 'p4', 'p6': 'p4', 'p7': 'p4'}

# ═══════════════════════════════════════════════════════════════════
#  进程管理与信号处理
# ═══════════════════════════════════════════════════════════════════

_ACTIVE_PROCS: Set[subprocess.Popen] = set()
_ACTIVE_LOCK  = threading.Lock()
_STOP_REQUESTED = threading.Event()


def _register_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCS.add(proc)


def _unregister_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCS.discard(proc)


def _ffmpeg_env(cuda_visible_devices: str = 'all',
                 nvidia_driver_caps: str = 'compute,video,utility') -> dict:
    """
    构造 FFmpeg / FFprobe 子进程的环境变量。

    沙箱（Codex CLI 的 Landlock+seccomp、受限容器等）中读取
    /proc/sys/crypto/fips_enabled 会返回 EIO，而 libgcrypt(>=1.10) 把非 ENOENT 的
    读取错误当作致命错误并 abort()，导致 ffmpeg 还没解析命令行就以 exit 134 退出。
    设置 LIBGCRYPT_FORCE_FIPS_MODE=0 后 libgcrypt 会跳过该文件的读取。

    注意：值必须是 "0"。设为 "1" 会强制开启 FIPS 自检，在沙箱中更容易失败。

    新增：为容器环境添加 NVIDIA 相关环境变量，提升 CUDA 初始化成功率。
    """
    env = os.environ.copy()
    env['LIBGCRYPT_FORCE_FIPS_MODE'] = '0'

    # 容器友好的 NVIDIA 环境变量（仅在未显式设置时添加）
    if 'NVIDIA_VISIBLE_DEVICES' not in env:
        env['NVIDIA_VISIBLE_DEVICES'] = cuda_visible_devices
    if 'NVIDIA_DRIVER_CAPABILITIES' not in env:
        env['NVIDIA_DRIVER_CAPABILITIES'] = nvidia_driver_caps

    # 补充常见 CUDA 库搜索路径
    cuda_lib_paths = [
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/usr/lib/x86_64-linux-gnu',
        '/usr/lib64',
    ]
    existing_ld = env.get('LD_LIBRARY_PATH', '')
    for p in cuda_lib_paths:
        if os.path.isdir(p) and p not in existing_ld:
            existing_ld = f"{p}:{existing_ld}" if existing_ld else p
    if existing_ld != env.get('LD_LIBRARY_PATH', ''):
        env['LD_LIBRARY_PATH'] = existing_ld

    return env


def _terminate_active_procs(grace: float = 5.0) -> None:
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE_PROCS)

    for proc in procs:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    deadline = time.time() + grace
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
            except Exception:
                pass


def _signal_handler(_sig, _frame) -> None:
    _STOP_REQUESTED.set()
    print('\n[INFO] 收到中断信号，正在终止 FFmpeg 子进程……', file=sys.stderr)
    _terminate_active_procs()
    raise KeyboardInterrupt


def install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _signal_handler)


# ═══════════════════════════════════════════════════════════════════
#  日志记录（--log）
# ═══════════════════════════════════════════════════════════════════

class Tee:
    """将输出同时写入原始流和日志文件，两路均实时 flush。"""

    def __init__(self, original, log_file: str):
        self.original = original
        self.log = open(log_file, 'a', encoding='utf-8')
        self.log.write(f"\n\n{'='*60}\n")
        self.log.write(f"执行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log.write(f"命令行: {' '.join(sys.argv)}\n{'='*60}\n\n")

    def write(self, data: str) -> None:
        self.original.write(data)
        self.log.write(data)
        self.log.flush()

    def flush(self) -> None:
        self.original.flush()
        self.log.flush()

    def close(self) -> None:
        self.log.close()


def setup_log(log_path: str) -> None:
    """重定向 stdout / stderr 到 Tee，使所有输出同时写入日志文件。"""
    sys.stdout = Tee(sys.stdout, log_path)
    sys.stderr = Tee(sys.stderr, log_path)


# ═══════════════════════════════════════════════════════════════════
#  硬件能力缓存（避免重复探测）
# ═══════════════════════════════════════════════════════════════════

_HW_CACHE_FILE = Path.home() / '.cache' / 'vidutils' / 'hwaccel_cache.json'

def _load_hw_cache() -> Optional[Dict]:
    """从缓存文件加载历史探测结果（仅用于快速启动，不跳过实时探测）。"""
    try:
        if _HW_CACHE_FILE.exists():
            import json
            with open(_HW_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_hw_cache(caps: 'HardwareCapabilities', ffmpeg_version: str = '') -> None:
    """将探测结果保存到缓存文件，供下次快速参考。"""
    try:
        _HW_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        import json
        cache_data = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'ffmpeg_version_hint': ffmpeg_version,
            'decoder': caps.has_decoder,
            'h264_nvenc': caps.has_encoder_h264,
            'hevc_nvenc': caps.has_encoder_hevc,
            'av1_nvenc': caps.has_encoder_av1,
            'crop_cuda': caps.has_crop_cuda,
            'cuda_scale': caps.has_cuda_scale,
            'vulkan': caps.has_vulkan,
            'vaapi': caps.has_vaapi,
            'opencl': caps.has_opencl,
        }
        with open(_HW_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 缓存写入失败不影响主流程


# ═══════════════════════════════════════════════════════════════════
#  硬件能力检测
# ═══════════════════════════════════════════════════════════════════

class HardwareCapabilities:
    """硬件加速能力检测结果（基于运行时探测）。"""

    def __init__(self):
        self.has_decoder       = False
        self.has_encoder_h264  = False
        self.has_encoder_hevc  = False
        self.has_encoder_av1   = False
        self.has_crop_cuda     = False
        self.has_cuda_scale    = False
        # 功能探针：软解路径的「hwupload_cuda → scale_cuda → hwdownload」真能跑通吗。
        # 与 has_cuda_scale（只是 `-filters` 里有这个滤镜名）是两件事，见下面谓词的注释。
        self.cuda_scale_upload_ok = False
        # 按**源编解码器**的硬解实测结论：codec_name（h264 / hevc / av1 …）→ 能否硬解。
        # has_decoder 只是"H.264 能硬解"的机器级结论；NVDEC 的能力分编解码器
        # （T4 解不了 AV1），所以每个源 codec 各自确认一次，见 cuda_decodes()。
        self.cuda_decode_ok: Dict[str, bool] = {}
        self.has_vulkan        = False
        self.has_vaapi         = False
        self.has_opencl        = False
        self._detected: Set[str] = set()

    def _mark_detected(self, item: str) -> None:
        self._detected.add(item)

    def has_nvenc(self, codec: str) -> bool:
        if codec == 'h264_nvenc':
            return self.has_encoder_h264
        if codec == 'hevc_nvenc':
            return self.has_encoder_hevc
        if codec == 'av1_nvenc':
            return self.has_encoder_av1
        return False

    def has_any_encoder(self) -> bool:
        return self.has_encoder_h264 or self.has_encoder_hevc or self.has_encoder_av1

    def cuda_decodes(self, src_codec: str = '') -> bool:
        """本机的 CUDA 硬解能不能解这个**源编解码器**（不是"这个编码器"）。

        与 has_decoder 的分工：`has_decoder` = "机器有可用的 CUDA 硬解"，但它是由
        **H.264 微流**探出来的；NVDEC 的解码能力分编解码器（T4/Turing 解不了 AV1），
        所以这里再叠一层"该 codec 实测能解"。

        未探测过（字典里没这个 codec）时按 has_decoder **乐观**处理——这样不传
        src_codec 的既有调用方（如 --list-strategies）行为逐字不变，只有实测失败的
        codec 才会降级。
        """
        if not self.has_decoder:
            return False
        if not src_codec:
            return True
        return self.cuda_decode_ok.get(src_codec.strip().lower(), True)

    def can_full_pipeline(self, codec: str) -> bool:
        """全 GPU 流水线：硬解 + crop_cuda + NVENC 编码（仅 crop 模式可用）。"""
        return self.has_decoder and self.has_crop_cuda and self.has_nvenc(codec)

    def can_cuda_scale_zerocopy(self, codec: str) -> bool:
        """零拷贝 CUDA 缩放：硬解供 CUDA 帧 + scale_cuda + NVENC（仅 cover 模式）。

        只把【缩放】留在显存里，裁剪仍回 CPU——crop_cuda 在 FFmpeg 上游并不存在，
        所以链路必然是 scale_cuda → hwdownload → crop（一次下载，与现状次数相同）。
        """
        return self.has_decoder and self.has_cuda_scale and self.has_nvenc(codec)

    def can_cuda_scale_upload(self) -> bool:
        """软解 CUDA 缩放：软件帧 → hwupload_cuda → scale_cuda → hwdownload → crop。

        与零拷贝路径的关键区别：**不要求 has_decoder**——缩放轴与解码轴正交，硬解
        不可用（或解不了该编码）时照样能把重采样放进显存。也不要求 NVENC：链尾已经
        是软件帧（hwdownload + CPU crop），任何编码器都能接。

        判据用功能探针 cuda_scale_upload_ok，而不是 has_cuda_scale（滤镜名存在）：
        「ffmpeg 带 scale_cuda 但机器没有 N 卡 / 容器没挂设备」是真实存在的组合，
        只查滤镜名会把这种情况误判成可用，而策略链不跨文件记忆失败 →
        每个文件都要先跑一次必然失败的链才回退。
        """
        return self.cuda_scale_upload_ok

    def can_cuda_scale(self, codec: str) -> bool:
        """兼容别名（等价于 can_cuda_scale_zerocopy，保留给既有调用方/单测）。"""
        return self.can_cuda_scale_zerocopy(codec)

    def has_hwaccel(self, hwaccel_type: str) -> bool:
        return {
            'cuda':   self.has_decoder,
            'vulkan': self.has_vulkan,
            'vaapi':  self.has_vaapi,
            'opencl': self.has_opencl,
        }.get(hwaccel_type, False)

    def summary(self, only_detected: bool = False) -> str:
        items = [
            ('CUDA 解码', self.has_decoder,       'has_decoder'),
            ('h264_nvenc', self.has_encoder_h264,  'has_encoder_h264'),
            ('hevc_nvenc', self.has_encoder_hevc,  'has_encoder_hevc'),
            ('av1_nvenc',  self.has_encoder_av1,   'has_encoder_av1'),
            ('crop_cuda',  self.has_crop_cuda,     'has_crop_cuda'),
            ('scale_cuda', self.has_cuda_scale,    'has_cuda_scale'),
            ('hwupload缩放', self.cuda_scale_upload_ok, 'cuda_scale_upload_ok'),
            ('Vulkan',     self.has_vulkan,         'has_vulkan'),
            ('VA‑API',     self.has_vaapi,          'has_vaapi'),
            ('OpenCL',     self.has_opencl,         'has_opencl'),
        ]
        parts = []
        for name, available, key in items:
            if only_detected and key not in self._detected:
                continue
            parts.append(f"{name}={'✓' if available else '✗'}")
        return ', '.join(parts)


def _check_nvenc_available(ffmpeg_bin: str = 'ffmpeg', codec: str = 'h264_nvenc') -> bool:
    """运行时探测 NVENC 编码器：启动 1 帧 160×160 null 编码任务，捕获错误。

    探针尺寸必须是 160×160：NVENC 有最小编码分辨率限制，此前的 64×64 低于该下限，
    驱动会直接报 "Frame Dimension less than the minimum supported value"，
    导致在装有 Tesla T4 等可用 NVENC 的机器上也被误判为不可用。
    """
    test_cmd = [
        # -nostdin 必需：ffmpeg 默认会开 stdin 交互线程，若父进程的 stdin 是一个
        # 既不关闭也无数据的管道（CI / 后台任务 / 工具托管执行），它会一直阻塞在
        # read() 上，CPU 占用 0%，且 Python 的 timeout= 也兜不住（实测：kill 之后
        # communicate() 同样不返回，只能靠外部强杀）。转码命令里已有 -nostdin，
        # 探测命令此前漏了，导致 --decode auto 卡在"正在检测硬件加速能力…"。
        ffmpeg_bin, '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'nullsrc=s=160x160:d=0.04:r=25',
        '-frames:v', '1', '-c:v', codec, '-f', 'null', '-',
    ]
    try:
        r = subprocess.run(test_cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, timeout=15,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        if r.returncode != 0:
            # 退出码非 0 说明这次 1 帧探针编码确实失败了。
            # 旧逻辑只在 stderr 命中 NVENC 关键字时才返回 False，导致「非 NVENC 原因的
            # 失败」（如 libgcrypt abort、lavfi 异常、沙箱限制）被误判为「NVENC 可用」，
            # 直到真正转码时才暴露并临时降级。
            # 现在只要失败且 stderr 有内容就判为不可用（保守方向：退到 CPU，结果仍正确）；
            # stderr 为空时无法归因，才保守放行。
            err = (r.stderr or '').strip()
            return not err
        return True
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _extract_ffmpeg_error(stderr_text: str,
                          keywords: Optional[List[str]] = None,
                          max_lines: int = 3,
                          max_len: int = 400) -> str:
    """从 FFmpeg stderr 中提取真正相关的错误行。

    直接取 stderr 前 N 个字符只能拿到 FFmpeg 的常规 banner（Input #0 / Metadata /
    Duration / Stream …），真正的失败原因被淹没在后面，导致诊断信息看起来像
    「文件打不开」。这里优先返回命中 keywords 的行，未命中时退化为末尾若干行。
    """
    lines = [l.strip() for l in (stderr_text or '').splitlines() if l.strip()]
    if not lines:
        return '(stderr 为空)'
    hits: List[str] = []
    if keywords:
        kws = [k.lower() for k in keywords]
        hits = [l for l in lines if any(k in l.lower() for k in kws)]
    picked = hits[:max_lines] if hits else lines[-max_lines:]
    text = ' ⏎ '.join(picked)
    return text if len(text) <= max_len else text[:max_len] + '…'


def _probe_stream_args(size_str: str) -> List[str]:
    """生成硬件解码探针用的微型 H.264 流参数。

    注意：不能直接用 `testsrc` —— 它默认输出 yuv444p，libx264 会编成
    High 4:4:4 Predictive，而所有硬件解码器（NVDEC / Vulkan / VA‑API）都不支持
    4:4:4，探针必然报 "Hardware is lacking required capabilities" 而被误判为
    「硬解不可用」。testsrc2 + 显式 yuv420p 才是硬件解码器普遍支持的组合。
    """
    return [
        '-f', 'lavfi', '-i', f'testsrc2=s={size_str}:d=0.1:r=25',
        '-pix_fmt', 'yuv420p',
    ]


# CUDA 解码探针判定"失败"的关键词。抽成常量是因为有**两处**探针要用
# （机器级的 _check_cuda_decoder_available 与 按源编解码器的 _probe_cuda_decode_codec），
# 各写一份必然慢慢漂移。
_CUDA_ERROR_MARKERS = (
    'cannot load libnvcuvid', 'failed loading nvcuvid',
    'cannot load nvcuda', 'failed to load nvcuda',
    'device creation failed',
    'hardware device setup failed',
    'could not dynamically load cuda',
    'no device available for decoder',
    'hwaccel initialisation returned error',
    'no cuda capable devices',
    'does not support device type cuda',
    'cuda_error_no_device',
    'operation not permitted',
)


def _check_cuda_decoder_available(ffmpeg_bin: str = 'ffmpeg',
                                    diagnostics: bool = False) -> bool:
    """运行时探测 CUDA 硬件解码：生成微型 **H.264** 流后以 -hwaccel cuda 解码。

    ⚠ 本函数只证明「H.264 能硬解」，所以它得到的是**编解码器无关的机器级标志**。
    NVDEC 的解码能力其实是分编解码器的（T4/Turing 解不了 AV1），判断"某个源能不能
    硬解"必须另用 `_probe_cuda_decode_codec()` 拿真实输入试解 1 帧——见 `cuda_decodes()`。

    新增：支持多分辨率探针（64×64、192×192、320×240），避免尺寸特定问题；
         diagnostics=True 时输出详细错误信息，帮助定位环境缺陷。
    """
    resolutions = [
        ('标准', '64x64'),
        ('中等', '192x192'),
        ('320x240', '320x240'),
    ]
    errors_by_res = {}

    for label, size_str in resolutions:
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
            os.close(fd)
            gen = [
                ffmpeg_bin, '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                *_probe_stream_args(size_str),
                '-c:v', 'libx264', '-frames:v', '2', tmp_path,
            ]
            r = subprocess.run(gen, capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL, env=_ffmpeg_env())
            if r.returncode != 0 or not os.path.exists(tmp_path):
                errors_by_res[label] = f'生成失败(rc={r.returncode})'
                continue
            test = [
                ffmpeg_bin, '-nostdin', '-y', '-hide_banner',
                '-hwaccel', 'cuda', '-hwaccel_device', '0',
                '-i', tmp_path, '-frames:v', '1', '-f', 'null', '-',
            ]
            r2 = subprocess.run(test, capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL, env=_ffmpeg_env())
            err = (r2.stderr or '').lower()
            if any(e in err for e in _CUDA_ERROR_MARKERS):
                errors_by_res[label] = (
                    '检测到 CUDA 错误: '
                    + _extract_ffmpeg_error(r2.stderr, list(_CUDA_ERROR_MARKERS))
                )
            else:
                if diagnostics:
                    print(f'    [CUDA 诊断] 探针 {size_str} 通过')
                return True
        except Exception as exc:
            errors_by_res[label] = f'异常: {exc}'
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    if diagnostics:
        print('  [CUDA 诊断] 所有探针尺寸均失败：')
        for label, msg in errors_by_res.items():
            print(f'    {label}: {msg}')
        print('  提示：若环境缺 CUDA 解码侧依赖（如 libnvcuvid、nvidia-driver 版本不匹配、')
        print('        容器缺 /dev/nvidia* 设备映射、或沙箱限制设备访问），将正确降级到')
        print('        「硬解不可用 + NVENC 硬编」或纯 CPU 路径，属于安全行为。')
    return False


def _check_hwaccel_available(ffmpeg_bin: str = 'ffmpeg',
                              hwaccel_type: str = 'cuda') -> bool:
    """运行时探测指定硬件加速器（Vulkan / VA-API / OpenCL 等）。"""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)
        gen = [
            ffmpeg_bin, '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
            *_probe_stream_args('64x64'),
            '-c:v', 'libx264', '-frames:v', '2', tmp_path,
        ]
        r = subprocess.run(gen, capture_output=True, text=True, timeout=15,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        if r.returncode != 0 or not os.path.exists(tmp_path):
            return False
        test = [
            ffmpeg_bin, '-nostdin', '-y', '-hide_banner',
            '-hwaccel', hwaccel_type,
        ]
        # OpenCL 策略固定使用 -hwaccel_output_format nv12；探针必须带相同参数，
        # 否则 "QSV to OpenCL mapping not usable" 等错误只会在实际转码时才暴露。
        if hwaccel_type == 'opencl':
            test += ['-hwaccel_output_format', 'nv12']
        test += ['-i', tmp_path, '-frames:v', '1', '-f', 'null', '-']
        r2 = subprocess.run(test, capture_output=True, text=True, timeout=15,
                            stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        err = r2.stderr.lower()
        errors = [
            f'cannot load {hwaccel_type}', f'failed loading {hwaccel_type}',
            'hwaccel initialisation returned error',
            f'no {hwaccel_type} capable devices',
            f'does not support device type {hwaccel_type}',
            f'{hwaccel_type}_error', 'creation failure', 'device creation failed',
            'no device available for decoder', 'hardware device setup failed',
            'error opening device', 'instance creation failure',
            'failed to initialize', 'unsupported device',
            # OpenCL 特有：QSV-OpenCL 内存映射失败（实证）
            'qsv to opencl mapping not usable',
            'opencl mapping not usable',
            'mapping not usable',
        ]
        return not any(e in err for e in errors)
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def validate_cuda_environment(ffmpeg_bin: str = 'ffmpeg',
                              cuda_device_id: int = 0) -> Dict[str, str]:
    """主动验证 CUDA 环境：检查 nvidia-smi、设备节点、库路径、FFmpeg 编译选项。

    返回字典：问题分类 → 诊断信息（为空表示无明显问题）。
    用于在检测失败时输出可操作的修复建议，而非仅报告「不可用」。
    """
    findings: Dict[str, str] = {}

    # 1. 检查 nvidia-smi
    try:
        r = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            findings['nvidia_smi'] = f'nvidia-smi 退出码 {r.returncode}; 可能缺少 NVIDIA 驱动。'
        else:
            # 提取设备名称和驱动版本（可选，用于日志）
            pass
    except FileNotFoundError:
        findings['nvidia_smi'] = '未找到 nvidia-smi；请确认 NVIDIA 驱动已安装并在 PATH 中。'
    except Exception as exc:
        findings['nvidia_smi'] = f'nvidia-smi 执行异常: {exc}'

    # 2. 检查 /dev/nvidia* 设备节点（容器常缺）
    nvidia_devs = list(Path('/dev').glob('nvidia*')) if Path('/dev').exists() else []
    if not nvidia_devs:
        findings['device_nodes'] = '未检测到 /dev/nvidia* 设备节点；容器环境需添加 --gpus all 或 --device=/dev/nvidia0。'

    # 3. 检查 CUDA 库路径（ldconfig / LD_LIBRARY_PATH）
    lib_paths = os.environ.get('LD_LIBRARY_PATH', '').split(':')
    cuda_lib_dirs = [
        '/usr/local/cuda/lib64', '/usr/local/cuda/lib',
        '/usr/lib/x86_64-linux-gnu', '/usr/lib64',
    ]
    lib_found = False
    for p in cuda_lib_dirs + lib_paths:
        if p and os.path.isfile(os.path.join(p, 'libcuda.so')):
            lib_found = True
            break
        # 也检查 libnvcuvid（解码库）
        if p and os.path.isfile(os.path.join(p, 'libnvcuvid.so')):
            lib_found = True
            break
    if not lib_found:
        findings['cuda_libraries'] = '未找到 libcuda.so / libnvcuvid.so；检查 LD_LIBRARY_PATH 或 CUDA 安装。'

    # 4. 检查 FFmpeg 编译选项中是否包含 cuda
    try:
        r = subprocess.run([ffmpeg_bin, '-hide_banner', '-hwaccels'],
                           capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        if 'cuda' not in r.stdout.lower():
            findings['ffmpeg_cuda'] = 'FFmpeg 编译选项中未包含 cuda（-hwaccels 无 cuda）；可能需要重编译。'
    except Exception as exc:
        findings['ffmpeg_cuda'] = f'FFmpeg -hwaccels 检查失败: {exc}'

    return findings


def detect_cuda_capabilities(ffmpeg_bin: str = 'ffmpeg',
                              decode: Optional[str] = None,
                              diagnostics: bool = False,
                              cuda_device_id: int = 0,
                              *,
                              probe_encoders: bool = True,
                              probe_filters: bool = False) -> HardwareCapabilities:
    """运行时全面探测硬件加速能力。

    **探测也按轴分开**（三轴正交的必然要求）：
      · decode 轴（--decode）只决定探不探**解码器**；
      · 编码器探不探由 probe_encoders 决定（--codec 与 --scale-algo 需要 NVENC 时才探）；
      · 滤镜（crop_cuda / scale_cuda）探不探由 probe_filters 决定。

    旧实现把编码器与滤镜探测全塞在 `if detect_cuda:` 里，于是
    「软解 + NVENC」这类组合（今天写成 --decode cpu）根本不探滤镜，
    还会在下面印出「当前 FFmpeg 里没有 scale_cuda」这种**假**结论。

    注意 has_cuda_scale 只代表「`-filters` 里有这个滤镜名」；能否真跑要看
    HardwareCapabilities.can_cuda_scale_upload()（功能探针，见 _probe_cuda_scale_upload）。
    """
    caps = HardwareCapabilities()
    # 解码轴归一化：None / 'none'（旧值）/ 'cpu' 都是「软解」
    _decode = 'cpu' if decode in (None, 'none', 'cpu') else decode
    # 一件都不探就直接返回：调用方在「三轴全显式 CPU」时走这条路，省掉全部 GPU 探测
    if _decode == 'cpu' and not probe_encoders and not probe_filters:
        return caps

    # 新增：先做主动环境验证（仅在诊断模式下打印详细结果）
    env_findings = {}
    if diagnostics:
        print('  [CUDA 诊断] 正在运行主动环境验证...')
        env_findings = validate_cuda_environment(ffmpeg_bin, cuda_device_id)
        if env_findings:
            for cat, msg in env_findings.items():
                print(f'    环境问题 [{cat}]: {msg}')
        else:
            print('    环境验证：无明显缺陷（nvidia-smi 可访问、设备节点存在、库路径可用、FFmpeg 支持 cuda）。')

    print('正在检测硬件加速能力...')
    detect_all   = _decode == 'auto'
    detect_cuda  = detect_all or _decode == 'cuda'
    detect_vulkan = detect_all or _decode == 'vulkan'
    detect_vaapi = detect_all or _decode == 'vaapi'
    detect_opencl = detect_all or _decode == 'opencl'

    if detect_cuda:
        print('  CUDA 解码:  ', end='', flush=True)
        # 新增：传入诊断模式与设备 ID
        caps.has_decoder = _check_cuda_decoder_available(
            ffmpeg_bin, diagnostics=diagnostics
        )
        caps._mark_detected('has_decoder')
        print('可用 ✓' if caps.has_decoder else '不可用 ✗')

    # 编码器与解码轴无关：软解照样可以 NVENC 编码
    if probe_encoders:
        print('  h264_nvenc: ', end='', flush=True)
        caps.has_encoder_h264 = _check_nvenc_available(ffmpeg_bin, 'h264_nvenc')
        caps._mark_detected('has_encoder_h264')
        print('可用 ✓' if caps.has_encoder_h264 else '不可用 ✗')

        print('  hevc_nvenc: ', end='', flush=True)
        caps.has_encoder_hevc = _check_nvenc_available(ffmpeg_bin, 'hevc_nvenc')
        caps._mark_detected('has_encoder_hevc')
        print('可用 ✓' if caps.has_encoder_hevc else '不可用 ✗')

        # av1_nvenc 仅 Ada Lovelace（RTX 40xx）及以后支持，老卡探测结果必然是
        # 不可用；单独探测一次，避免 --codec av1_nvenc 时误走全 GPU 策略。
        print('  av1_nvenc:  ', end='', flush=True)
        caps.has_encoder_av1 = _check_nvenc_available(ffmpeg_bin, 'av1_nvenc')
        caps._mark_detected('has_encoder_av1')
        print('可用 ✓' if caps.has_encoder_av1 else '不可用 ✗')

    # 滤镜同样与解码轴无关：软解 + hwupload_cuda 一样能用 scale_cuda
    if probe_filters:
        try:
            r = subprocess.run(
                [ffmpeg_bin, '-hide_banner', '-filters'],
                capture_output=True, text=True, timeout=10,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL, env=_ffmpeg_env(),
            )
            _flist = r.stdout or ''
            caps.has_crop_cuda = 'crop_cuda' in _flist
            caps.has_cuda_scale = 'scale_cuda' in _flist
        except Exception:
            caps.has_crop_cuda = False
            caps.has_cuda_scale = False
        caps._mark_detected('has_crop_cuda')
        print(f"  crop_cuda:  {'可用 ✓' if caps.has_crop_cuda else '不可用 ✗'}")
        caps._mark_detected('has_cuda_scale')
        print(f"  scale_cuda: {'可用 ✓' if caps.has_cuda_scale else '不可用 ✗'}")

    if detect_vulkan:
        print('  Vulkan:     ', end='', flush=True)
        caps.has_vulkan = _check_hwaccel_available(ffmpeg_bin, 'vulkan')
        caps._mark_detected('has_vulkan')
        print('可用 ✓' if caps.has_vulkan else '不可用 ✗')

    if detect_vaapi:
        print('  VA‑API:     ', end='', flush=True)
        caps.has_vaapi = _check_hwaccel_available(ffmpeg_bin, 'vaapi')
        caps._mark_detected('has_vaapi')
        print('可用 ✓' if caps.has_vaapi else '不可用 ✗')

    if detect_opencl:
        print('  OpenCL:     ', end='', flush=True)
        caps.has_opencl = _check_hwaccel_available(ffmpeg_bin, 'opencl')
        caps._mark_detected('has_opencl')
        print('可用 ✓' if caps.has_opencl else '不可用 ✗')

    # 只打 ✓/✗ 分不清"本机不支持"和"根本没这个能力"，这里对高频问号给出
    # 原因与真实影响，省得误判成脚本故障：
    #   · av1_nvenc —— 7 代 NVENC（Turing/T4）无 AV1 编码器，属硬件限制，无法修复；
    #   · crop_cuda —— **该滤镜在 FFmpeg 上游并不存在**（不是"没编译"），
    #                  所以 crop 模式的全 GPU 流水线（策略 1）永远跳过；
    #   · scale_cuda —— 自建（--enable-cuda-nvcc）才有，cover 的 CUDA 缩放策略依赖它。
    # 注意每条都要求「该项本轮确实探过」——否则会像旧实现那样，在没探滤镜的组合下
    # （如旧的 nvenc-only 分支）印出「当前 FFmpeg 里没有 scale_cuda」这种假结论。
    _notes: List[str] = []
    if probe_encoders and not caps.has_encoder_av1:
        _notes.append('av1_nvenc 不可用：AV1 硬编需 8 代 NVENC（Ada / RTX 40 / L40 及以上）；'
                      '--codec av1_nvenc 会自动降级为 libsvtav1 CPU 编码')
    if probe_filters and not caps.has_crop_cuda:
        _notes.append('crop_cuda 不可用：该滤镜在 FFmpeg 上游并不存在（与编译选项无关），'
                      'crop 模式的全 GPU 流水线跳过；裁剪实际在 CPU 侧完成')
    if probe_filters and not caps.has_cuda_scale:
        _notes.append('scale_cuda 不可用：显存内缩放（--scale-algo cuda-*）走不了，'
                      '退回 CPU 缩放（需 --enable-cuda-nvcc 的自建 FFmpeg）')
    if _notes:
        print('  ── 说明 ──')
        for _n in _notes:
            print(f'  · {_n}')

    return caps


def _probe_cuda_decode_codec(ffmpeg_bin: str, input_file,
                             timeout: int = 30) -> bool:
    """用**真实输入**试解 1 帧，判断本机的 CUDA 硬解能不能解这个源。

    为什么不复用 `_check_cuda_decoder_available()`：那个探针是拿 **H.264 微流**跑的，
    结论只是"H.264 能硬解"。而 NVDEC 的解码能力是**分编解码器**的——T4（Turing，
    第 4 代 NVDEC）能解 H.264 / HEVC / VP9 / MPEG-2/4 / VC-1，但**解不了 AV1**
    （要 Ampere 起的第 5 代）。拿机器级标志去推断"这个源能硬解"会误判，后果不止
    多一次无用尝试：
      · `--decode auto` 会选到 cuda → 每个该编码的文件都白跑一次必然失败的链；
      · `--fallback-policy strict` 下更糟：首选策略失败且不降级 → 直接退出 2，
        而这条素材走软解其实完全可行。
    实测（2026-09-20 T4 + AV1 素材）：
      `[av1 @ ...] Failed setup for format cuda: hwaccel initialisation returned error`

    判据是"真的解一帧"：rc == 0 且 stderr 里没有 CUDA 类错误关键词。比查
    `ffmpeg -decoders` 列表可靠——表里有 `av1_cuvid` 只说明它**编译进来了**，
    不代表这台设备的 NVDEC 支持它。`-f null` 不落盘、`-frames:v 1` 只解一帧，
    成本在几十毫秒量级；结果由调用方按 codec 名缓存，一批文件只探一次。
    """
    cmd = [
        ffmpeg_bin, '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
        '-hwaccel', 'cuda', '-hwaccel_device', '0',
        '-i', str(input_file), '-frames:v', '1', '-f', 'null', '-',
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
    except Exception:
        return False
    if r.returncode != 0:
        return False
    err = (r.stderr or '').lower()
    return not any(e in err for e in _CUDA_ERROR_MARKERS)


def _probe_cuda_scale_upload(ffmpeg_bin: str = 'ffmpeg') -> bool:
    """功能探针：软解路径的「上传 → 显存缩放 → 下载」真能跑通吗。

    为什么不复用 `-filters` 里有没有 scale_cuda：那只证明滤镜**编进了二进制**。
    「ffmpeg 带 scale_cuda 但机器没有 N 卡 / 容器没挂设备」是真实存在的组合
    （本项目的 Windows 开发机就是这样），此时生成的链必然失败。而策略链
    **不跨文件记忆**失败（process_file 每个文件重新生成并逐级尝试），
    误判一次 = 每个文件一次失败尝试 + 噪声日志。

    为什么用 hwupload_cuda 而不是通用 hwupload：后者必须配 -filter_hw_device
    （否则报 "A hardware device reference is required"），而 hwupload_cuda
    自带 device，不需要给 build_ffmpeg_cmd 增加任何新参数。
    """
    test_cmd = [
        ffmpeg_bin, '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'nullsrc=s=160x160:d=0.04:r=25',
        '-vf', 'format=nv12,hwupload_cuda,scale_cuda=128:128,hwdownload,format=nv12',
        '-frames:v', '1', '-f', 'null', '-',
    ]
    try:
        r = subprocess.run(test_cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, timeout=20,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        return r.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════════

def normalize_codec_name(codec: str) -> str:
    """将常见编码器别名归一化为 FFmpeg 实际名称，同时强制小写。"""
    if codec in ('auto', 'copy'):
        return codec
    lower = codec.lower()
    normalized = CODEC_ALIASES.get(lower, lower)
    if normalized != lower:
        print(f"提示：编码器名称 '{codec}' 已归一化为 '{normalized}'")
    elif normalized != codec:
        print(f"提示：编码器名称 '{codec}' 已转为小写 '{normalized}'")
    return normalized


def normalize_preset(preset: str, target_codec: str, quiet: bool = False) -> str:
    """在 NVENC（p1~p7）与 libx264 风格（ultrafast~veryslow）之间自动双向映射。

    libsvtav1 另走一套：它的 -preset 是 0~13 的整数，名字类取值一律先换算成
    整数再下发，否则 ffmpeg 解析失败（"Unable to parse option value"）。

    quiet=True 时不打印换算提示：调用方用"默认档位"（而非用户显式给的 --preset）
    触发换算时，提示里的 `--preset xxx` 会让用户困惑（他从没写过这个值）。
    """
    def _note(msg: str) -> None:
        if not quiet:
            print(msg)

    if target_codec == 'libsvtav1':
        p = preset.strip().lower()
        if p.lstrip('-').isdigit():
            # 已是整数写法，仅收敛到 libsvtav1 的合法区间 0~13
            return str(max(0, min(13, int(p))))
        mapped = NVENC_TO_SVTAV1_PRESET.get(p) or X264_TO_SVTAV1_PRESET.get(p)
        if mapped is not None:
            _note(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
                  f"（libsvtav1 使用 0~13 整数 preset）。")
            return str(mapped)
        _note(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，"
              f"使用默认 '{DEFAULT_PRESET_SVTAV1}'。")
        return DEFAULT_PRESET_SVTAV1

    if target_codec in NVENC_CODECS:
        if preset.startswith('p') and preset[1:].isdigit():
            return preset
        rev = {v: k for k, v in NVENC_TO_X264_PRESET.items()}
        if preset in rev:
            mapped = rev[preset]
            _note(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
                  f"（{target_codec} 使用 NVENC 风格 preset）。")
            return mapped
        _note(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，"
              f"使用默认 '{DEFAULT_PRESET_GPU}'。")
        return DEFAULT_PRESET_GPU

    if target_codec in ('libx264', 'libx265'):
        if preset.startswith('p') and preset[1:].isdigit():
            mapped = NVENC_TO_X264_PRESET.get(preset, DEFAULT_PRESET_CPU)
            _note(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
                  f"（{target_codec} 使用 libx264 风格 preset）。")
            return mapped
        return preset

    return preset


def normalize_extra_args(extra: Optional[List[str]]) -> List[str]:
    """剥离 '--' 分隔符前缀，返回干净的 FFmpeg 额外参数列表。"""
    if not extra:
        return []
    args = list(extra)
    if args and args[0] == '--':
        args = args[1:]
    return args


def get_extension_from_codec(codec: str) -> Optional[str]:
    """根据编码器返回推荐容器扩展名；copy 返回 None（沿用源扩展名）。"""
    if codec == 'copy':
        return None
    if codec in CODEC_CONTAINER_MAP:
        return CODEC_CONTAINER_MAP[codec]
    base = codec.split('_')[0] if '_' in codec else codec
    for key, ext in CODEC_CONTAINER_MAP.items():
        if key and key.startswith(base):
            return ext
    return '.mp4'


def check_container_compatibility(ext: str, codec: str) -> bool:
    ext_l = ext.lower()
    cod_l = codec.lower()
    if ext_l == '.mkv':
        return True
    if ext_l in {'.mp4', '.m4v'}:
        # VP9 虽以 .webm 为默认容器，但 ISO-BMFF 同样能封装 VP9（实测可写），
        # 用户用 --container .mp4 强制时不应误报警告。
        return any(x in cod_l for x in
                   ['264', '265', 'hevc', 'av1', 'rav1e', 'mpeg4', 'vp9'])
    if ext_l == '.webm':
        return any(x in cod_l for x in ['vpx', 'vp8', 'vp9', 'av1'])
    if ext_l == '.mov':
        return any(x in cod_l for x in ['prores', '264', '265', 'hevc'])
    if ext_l == '.avi':
        return any(x in cod_l for x in ['xvid', 'mpeg4', 'mjpeg'])
    return True


def default_preset_for(codec: str) -> str:
    """按编码器类型给出默认预设：GPU 硬件编码器 p5，libsvtav1 按资源取档，其余 medium。"""
    c = codec.lower()
    if c == 'libsvtav1':
        # 0~13 整数，按 CPU 核数自动取值（见 auto_effort）
        return str(auto_effort()[1])
    return DEFAULT_PRESET_GPU if c in CQ_SUPPORTED_CODECS else DEFAULT_PRESET_CPU


def strategy_preset(user_preset: Optional[str], requested_codec: str,
                    strategy_codec: str, quiet: bool = False) -> str:
    """算出某条策略最终要下发的 --preset。

    用户显式给了 --preset 就按本策略的编码器换算。没给时，基准档位取
    **用户请求的那个编码器**的默认值，再换算到本策略的编码器 —— 这样降级前后
    的档位是等效的：
        h264_nvenc(默认 p5) → libx264  得到 medium
        av1_nvenc (默认 p5) → libsvtav1 得到 8
    若反过来按"本策略编码器自己的默认值"取，档位会随编码器漂移（libsvtav1 的
    默认值还会随 CPU 核数变），用户拿到的速度/质量就与请求的不等价了。

    --codec auto 是例外：它没有"请求的编码器"可言，策略链本身就是自适应的
    （NVENC 策略取 p5、CPU 策略取 medium），故仍按本策略编码器取默认值。

    由默认档位触发的换算不打印提示（用户没写过那个 --preset）；实际生效值由
    任务概览块统一展示，避免逐文件重复刷屏。quiet=True 则连用户显式给的
    --preset 的换算提示也一并静音，供概览块这类"只取值不报细节"的调用方使用。
    """
    if user_preset:
        return normalize_preset(user_preset, strategy_codec, quiet=quiet)
    base = (default_preset_for(strategy_codec) if requested_codec == 'auto'
            else default_preset_for(requested_codec))
    return normalize_preset(base, strategy_codec, quiet=True)


def encoder_supports_preset(codec: str) -> bool:
    return codec in PRESET_SUPPORTED_CODECS


def encoder_supports_crf(codec: str) -> bool:
    return codec in CRF_SUPPORTED_CODECS


def encoder_supports_cq(codec: str) -> bool:
    return codec in CQ_SUPPORTED_CODECS


# WebM 只接受 Vorbis / Opus 音轨。VP9 / AV1 的默认容器就是 .webm，而绝大多数
# 片源的音轨是 AAC——实测 `-c:a copy` 直接写头失败：
#   "Only VP8 or VP9 or AV1 video and Vorbis or Opus audio and WebVTT subtitles
#    are supported for WebM."
# 因此遇到 .webm 输出时必须把非 Opus/Vorbis 音轨转成 Opus，而不是让整条命令失败。
_WEBM_AUDIO_CODECS = {'opus', 'vorbis'}
_WEBM_AUDIO_FALLBACK = 'libopus'


def _src_audio_codecs(meta: Optional[Dict]) -> List[str]:
    """取源文件的音频编码列表；meta 为 None（探测失败）时返回空列表。"""
    if not meta:
        return []
    return [s.get('codec_name') for s in (meta.get('streams') or [])
            if s.get('codec_type') == 'audio' and s.get('codec_name')]


def resolve_audio_codec_for_container(audio_codec: str,
                                      container_ext: str,
                                      meta: Optional[Dict],
                                      warn: Optional[Callable[[str], None]] = None) -> str:
    """按目标容器修正音频编码方式（目前只有 WebM 需要干预）。

    Args:
        audio_codec: 用户指定的 --audio-codec（'copy' 表示流复制）。
        container_ext: 输出容器扩展名（含点，如 '.webm'）。
        meta: probe_full_metadata 结果，用于判断源音轨能否直接复制。
        warn: 告警回调。

    Returns:
        实际应下发的音频编码器名称。
    """
    if (container_ext or '').lower() != '.webm':
        return audio_codec

    c = (audio_codec or 'copy').lower()
    if c == 'copy':
        srcs = _src_audio_codecs(meta)
        if not srcs:
            # 探测不到音轨信息（含探测失败）：-c:a libopus 在无音轨时同样无害，
            # 故按"可能不兼容"处理，宁可多一次转码也不要写头失败。
            if warn:
                warn('输出为 .webm 但无法确认源音轨格式，音频改用 '
                     f'{_WEBM_AUDIO_FALLBACK} 重编码以确保可写入')
            return _WEBM_AUDIO_FALLBACK
        if all(s.lower() in _WEBM_AUDIO_CODECS for s in srcs):
            return audio_codec
        if warn:
            warn(f'WebM 只支持 Opus/Vorbis 音轨，源音轨为 {" / ".join(srcs)}，'
                 f'已改用 {_WEBM_AUDIO_FALLBACK} 重编码')
        return _WEBM_AUDIO_FALLBACK

    base = c.split('_')[-1] if c.startswith('lib') else c
    if base not in _WEBM_AUDIO_CODECS:
        if warn:
            warn(f'WebM 只支持 Opus/Vorbis 音轨，--audio-codec {audio_codec} '
                 f'不适用，已改用 {_WEBM_AUDIO_FALLBACK}')
        return _WEBM_AUDIO_FALLBACK
    return audio_codec


def detect_cpu_profile() -> Tuple[int, float]:
    """返回 (逻辑 CPU 核数, 可用内存 GB)。探测失败时回退 (os.cpu_count() or 4, 0.0)。"""
    try:
        cpu = os.cpu_count() or 4
    except Exception:
        cpu = 4
    try:
        # psutil 未必安装；Linux 直接读 /proc/meminfo 的 MemAvailable
        avail_gb = 0.0
        with open('/proc/meminfo', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    avail_gb = int(line.split()[1]) / 1024.0 / 1024.0
                    break
    except Exception:
        avail_gb = 0.0
    return cpu, avail_gb


# 按资源自动选"编码器速度档位"的档位表：(最小逻辑核数, libaom-av1 的 -cpu-used,
#   libsvtav1 的 -preset)
#
# 背景：ffmpeg 给 libaom-av1 的默认 -cpu-used=1 慢到不可用（实测 320x240 只有 1fps，
# 同一段素材 libsvtav1 是 35fps，差一个数量级）。过去只能让用户自己补
# `--extra-args -- -cpu-used 8`，这里改为按核数自动取值：
#   · 资源越少 → 档位越高（更偏速度），否则小机器上耗时不可接受；
#   · 资源越多 → 档位略降（更偏压缩率），反正核多有时间做更精细的搜索。
_AUTO_EFFORT_TIERS = (
    (16, 4, 7),
    (8,  5, 8),
    (4,  6, 9),
    (0,  8, 10),
)


def auto_effort(cpu_count: Optional[int] = None) -> Tuple[int, int]:
    """按可用核数返回 (libaom-av1 的 -cpu-used, libsvtav1 的 -preset)。"""
    if cpu_count is None:
        cpu_count, _ = detect_cpu_profile()
    for min_cores, aom_cpu_used, svtav1_preset in _AUTO_EFFORT_TIERS:
        if cpu_count >= min_cores:
            return aom_cpu_used, svtav1_preset
    return 8, 10


def _get_ffprobe_bin(ffmpeg_bin: str) -> str:
    """从 ffmpeg 路径推导同目录的 ffprobe，保证版本一致。"""
    p = Path(ffmpeg_bin)
    if p.parent == Path('.'):
        return 'ffprobe'
    return str(p.parent / ('ffprobe' + p.suffix))


def _fmt_size(n_bytes: int) -> str:
    n = float(n_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024.0:
            return f'{n:.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} TB'


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f'{seconds:.1f}s'
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    if days:
        return f'{days}d {hours:02d}h {minutes:02d}m {sec:02d}s'
    if hours:
        return f'{hours}h {minutes:02d}m {sec:02d}s'
    return f'{minutes}m {sec:02d}s'


def _eta_tail(frame: int, total_frames: int, fps: float) -> str:
    """进度条尾部的「剩余」字段。

    两种情况下 (total-frame)/fps 会恒等于 0 或不可用，套公式只会显示误导性的
    0.0s：① 还没收到第一帧，fps 为 0；② 帧数已满但 FFmpeg 尚未退出（编码器
    flush、-movflags +faststart 重写 moov），剩余帧数是 0 但进程还在跑。
    故按状态给出明确文案，而不是一个假的 0.0s。
    """
    if frame <= 0:
        return '  预计中...  '
    if frame < total_frames and fps > 0:
        return f'  剩余 {_fmt_duration((total_frames - frame) / fps)}   '
    return '  收尾中...  '


def _queue_tail(queue_rest: Optional[float], queue_cur: Optional[float],
                frame: int, total_frames: Optional[int], fps: float) -> str:
    """进度条尾部的「整批剩余」字段。

    当前文件的剩余优先按实时帧率算，这样整批剩余会跟着当前任务一起倒数；
    尚未起步（无帧率）时退化为该文件的字节估算；帧数已跑满（收尾中）则该文件
    不再计入。非批量模式（构件为 None）返回空串。
    """
    if queue_rest is None or queue_cur is None:
        return ''
    if total_frames and total_frames > 0 and 0 < frame < total_frames and fps > 0:
        cur_left = (total_frames - frame) / fps
    elif frame <= 0:
        cur_left = queue_cur
    else:
        cur_left = 0.0
    return f'  整批剩余 {_fmt_duration(queue_rest + cur_left)}'


def _same_path(a: Path, b: Path) -> bool:
    """比较两个路径是否指向同一位置（优先解析符号链接）。

    resolve() 可能因权限不足、符号链接环等抛异常，此时一律返回 False 会
    让调用方误判为"不同路径"从而放行——_same_path 用于拦"批量模式输入
    输出同目录"与"原地覆盖"，漏判等于覆盖源文件。故回退到不解析链接的
    abspath 比较：它更保守（可能误报相同，不会漏报）。
    """
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


# ═══════════════════════════════════════════════════════════════════
#  [META-KEEP] 原输入视频元数据探测与保留
#
#  一次 ffprobe 拿全量（-show_streams 默认即含 side_data_list：旋转 display matrix；
#  HDR10 静态元数据在 6.1 只在帧级暴露，故必要时补一次单帧探测），
#  按 abspath|size|mtime 缓存，供尺寸/帧数/色彩/命令构建共用，避免重复探测。
# ═══════════════════════════════════════════════════════════════════

_PROBE_CACHE: Dict[str, Dict] = {}
_PROBE_CACHE_LOCK = threading.Lock()

# 10bit 源在各编码器下的目标像素格式（软件编码器 yuv420p10le，NVENC p010le）
_PIXFMT_10BIT_BY_ENCODER = {
    'libx264': 'yuv420p10le',
    'libx265': 'yuv420p10le',
    'libsvtav1': 'yuv420p10le',
    'libaom-av1': 'yuv420p10le',
    'librav1e': 'yuv420p10le',
    'libvpx-vp9': 'yuv420p10le',
    'h264_nvenc': 'p010le',
    'hevc_nvenc': 'p010le',
    'av1_nvenc': 'p010le',
    'av1_qsv':   'p010le',
    'av1_amf':   'p010le',
    'prores': 'yuv422p10le',
    'prores_ks': 'yuv422p10le',
}
# 不支持 10bit 的编码器。
# h264_nvenc 在列：NVENC 的 H.264 编码器只做 8bit，实测喂 10bit 输入会以 rc=218 失败，
# 导致整条 GPU 策略报废并退回 CPU。宁可降 8bit 也要保住硬件编码（见 build_ffmpeg_cmd）。
# hevc_nvenc / av1_nvenc / av1_qsv / av1_amf 支持 10bit（p010），不在此列。
_ENCODERS_8BIT_ONLY = {'mpeg4', 'libvpx', 'mjpeg', 'vp8', 'h264_v4l2m2m',
                       'h264_nvenc'}

# ── --bit-depth 用：位深 → 各编码器的目标像素格式 ────────────────────────
# 10bit 一栏直接复用 _PIXFMT_10BIT_BY_ENCODER（改一处要同步另一处）；
# 12bit 走 yuv420p12le / p012le；8bit 的 NVENC 是 yuv420p、prores 是 yuv422p。
# 与 vidcrop_cpu_v2.py 的同名表逐字一致（孪生约定）。
_PIXFMT_BY_DEPTH: Dict[int, Dict[str, str]] = {
    8: {
        'h264_nvenc': 'yuv420p', 'hevc_nvenc': 'yuv420p', 'av1_nvenc': 'yuv420p',
        'av1_qsv': 'yuv420p', 'av1_amf': 'yuv420p',
        'prores': 'yuv422p', 'prores_ks': 'yuv422p',
    },
    10: dict(_PIXFMT_10BIT_BY_ENCODER),
    12: {
        'libx264': 'yuv420p12le', 'libx265': 'yuv420p12le',
        'libsvtav1': 'yuv420p12le', 'libaom-av1': 'yuv420p12le',
        'librav1e': 'yuv420p12le', 'libvpx-vp9': 'yuv420p12le',
        'hevc_nvenc': 'p012le', 'av1_nvenc': 'p012le',
        'av1_qsv': 'p012le', 'av1_amf': 'p012le',
        'prores': 'yuv422p12le', 'prores_ks': 'yuv422p12le',
    },
}
# 表里没列出的编码器按位深取这个默认值（多数 8bit 编码器本来就是 yuv420p）
_DEFAULT_PIXFMT_BY_DEPTH: Dict[int, str] = {
    8: 'yuv420p', 10: 'yuv420p10le', 12: 'yuv420p12le',
}
_BIT_DEPTH_CHOICES = (8, 10, 12)


def resolve_pix_fmt_for_depth(codec: str, depth: int,
                              warn: Optional[Callable[[str], None]] = None,
                              policy: str = 'auto') -> Optional[str]:
    """--bit-depth 显式指定时，算出对应的目标像素格式。

    命中 _ENCODERS_8BIT_ONLY（含 h264_nvenc：NVENC 的 H.264 只做 8bit，喂 10bit
    输入实测 rc=218 失败）却要求 10bit+ 时：auto 策略下降 8bit 并告警，
    strict 策略下抛错（由调用方转成退出 2）。
    """
    if depth not in _BIT_DEPTH_CHOICES:
        return None
    c = (codec or '').lower()
    if depth >= 10 and c in _ENCODERS_8BIT_ONLY:
        if policy == 'strict':
            raise ValueError(f'--bit-depth {depth}：{c} 不支持 10bit 以上编码'
                             f'（--fallback-policy strict 不降级）')
        if warn:
            warn(f'{c} 不支持 {depth}bit 编码，已降级为 8bit 输出'
                 f'（如需 {depth}bit 请用 hevc_nvenc / av1_nvenc 或 CPU 编码器）')
        return _PIXFMT_BY_DEPTH[8].get(c, _DEFAULT_PIXFMT_BY_DEPTH[8])
    return _PIXFMT_BY_DEPTH.get(depth, {}).get(c, _DEFAULT_PIXFMT_BY_DEPTH[depth])


# ── --hdr 相关 ────────────────────────────────────────────────────────
# `--hdr sdr` 用的滤镜配方：先转到线性光（zscale），做 tone mapping，再转回
# BT.709 的 SDR。tonemap 滤镜要求线性输入，不能直接对 gamma 编码的帧做。
#   · npl=100      标称峰值亮度（HLG/PQ 内容的常用取值）
#   · desat=0      ffmpeg 的 desat 默认 2 会明显掉饱和度，做 HDR→SDR 时通常关掉
# ⚠ `tonemap_cuda` **在上游不存在**（实测 `ffmpeg -h filter=tonemap_cuda` →
#   Unknown filter，与 crop_cuda 同款），所以没有 CUDA 侧的硬件 tone mapping：
#   CUDA 链必须先把帧下载成软件帧再做（cover 链本来就在 crop 前 hwdownload，
#   因此直接接在链尾即可，且应保留 p010le 下载以免提前丢精度）。
_HDR_TONEMAP_ALGOS = ('none', 'linear', 'gamma', 'clip', 'reinhard', 'hable',
                      'mobius')
_HDR_TONEMAP_DEFAULT = 'mobius'
_HDR_MODES = ('auto', 'keep', 'drop', 'sdr')
# 需要"把色彩标签按 SDR 重写"的两种模式（像素层面 drop 不改、sdr 会真转换）
_HDR_SDR_TAGGING_MODES = ('drop', 'sdr')


def parse_hdr_spec(spec: Optional[str]) -> Tuple[str, str]:
    """解析 --hdr → (mode, tonemap_algo)。

    auto  沿用今天的行为（元数据尽力透传）
    keep  尽力保留 HDR 静态元数据（写不进去时提示换 libx265）
    drop  不写 HDR 静态元数据，色彩标签按 SDR(bt709) 写；**像素不动**
    sdr   真的做 HDR→SDR tone mapping，色彩标签写 bt709
    后两者都可以带算法：`--hdr sdr:hable`（默认 mobius）。
    """
    if not spec or not spec.strip():
        return 'auto', _HDR_TONEMAP_DEFAULT
    s = spec.strip().lower()
    mode, sep, algo = s.partition(':')
    if mode not in _HDR_MODES:
        raise ValueError(
            f"--hdr '{spec}' 无效：只支持 {' / '.join(_HDR_MODES)}"
            f"（sdr 可带算法，如 sdr:hable）。")
    if algo:
        if mode != 'sdr':
            raise ValueError(f"--hdr '{spec}'：只有 sdr 模式能带 tone mapping 算法。")
        if algo not in _HDR_TONEMAP_ALGOS:
            raise ValueError(
                f"--hdr '{spec}' 无效：未知 tone mapping 算法 '{algo}'，"
                f"可用 {' / '.join(_HDR_TONEMAP_ALGOS)}。")
    else:
        algo = _HDR_TONEMAP_DEFAULT
    return mode, algo


def _filter_exists(ffmpeg_bin: str, name: str) -> bool:
    """惰性探测单个滤镜是否存在（只在 --hdr sdr 时才跑）。"""
    try:
        r = subprocess.run(
            [ffmpeg_bin, '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-f', 'lavfi', '-i', 'nullsrc=s=16x16:d=0.04:r=25',
             '-vf', name, '-frames:v', '1', '-f', 'null', '-'],
            capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        return r.returncode == 0
    except Exception:
        return False


def build_tonemap_filter(algo: str) -> str:
    """HDR→SDR 的滤镜串（zscale → tonemap → zscale）。"""
    return (f'zscale=t=linear:npl=100,'
            f'tonemap=tonemap={algo}:desat=0,'
            f'zscale=t=bt709:m=bt709:r=tv')

# ── --pix-fmt 相关 ──────────────────────────────────────────────────────
# 4:2:0 系（含高位深）要求宽高**均为**偶数；4:2:2 系只要求宽为偶数。
# 与 vidcrop_cpu_v2.py 的同名集合逐字一致（孪生约定）。
_PIXFMT_REQUIRE_EVEN_BOTH = {
    'yuv420p', 'yuvj420p', 'nv12', 'nv21',
    'yuv420p10le', 'yuv420p12le', 'p010le', 'p012le', 'p016le',
}
_PIXFMT_REQUIRE_EVEN_WIDTH = {'yuv422p', 'yuvj422p', 'yuv422p10le', 'yuv422p12le'}

# 零拷贝 CUDA 链（-hwaccel_output_format cuda）**不能**传 -pix_fmt：
# -pix_fmt 设的是 AVFrame.format（该链上是 AV_PIX_FMT_CUDA）而不是 sw_format，
# 传 nv12 / yuv420p 都会报 "Impossible to convert"（T4 实测，见
# memory/project_t4_gpu_capabilities.md）。该链上要改格式只能写进 scale_cuda=format=。
_SCALE_CUDA_FORMATS = ('nv12', 'yuv420p', 'yuv444p', 'p010le')
# scale_cuda=format= 改格式后必须配 -profile:v，否则 profile 与像素格式不匹配
# （同出处：format=p010le → main10、yuv444p → high444p）。
_SCALE_CUDA_PROFILE = {'p010le': 'main10', 'yuv444p': 'high444p'}
# 零拷贝链上「用户想要的格式 → scale_cuda 能表达的等价格式 + 为什么等价」。
# 只收真正等价的：yuv420p10le 与 p010le 都是 10bit 4:2:0，差别只在后者是半 planar
# （显存里的排布），CUDA 链上这就是同一个东西——不映射的话用户会以为\"10bit 用不了\"，
# 其实只是换个名字。没有等价格式的（yuv422p / p012le 等）不收，走无替代的提示分支。
_CUDA_PIX_FMT_ALIAS: Dict[str, Tuple[str, str]] = {
    'yuv420p10le': ('p010le', '同为 10bit 4:2:0 的显存排布'),
    'yuv420p10be': ('p010le', '同为 10bit 4:2:0 的显存排布'),
    'p010be':      ('p010le', '仅字节序不同'),
    'yuvj420p':    ('yuv420p', 'CUDA 链上没有 jpeg 值域变体，按 tv 处理'),
}


def _cuda_pixfmt_hint(req_pf: str) -> str:
    """零拷贝 CUDA 链上 --pix-fmt 用不了时，给一句**可直接照抄**的替代方案。

    两种情况分开说，否则用户分不清\"换个名字就行\"和\"这条链真的做不到\"：
      · 有等价格式 → 点名那个格式（并说明会自动配的 -profile:v）
      · 没有       → 说明只有离开零拷贝链（软件帧链）才能用原格式
    """
    alt = _CUDA_PIX_FMT_ALIAS.get(req_pf)
    if alt is None:
        return ('该格式在 CUDA 链上没有对应项；改用软件帧链'
                '（--scale-algo libswscale-lanczos）后原格式可正常下发')
    name, note = alt
    prof = _SCALE_CUDA_PROFILE.get(name)
    return (f'改用 --pix-fmt {name}（{note}'
            + (f'，会自动配 -profile:v {prof}' if prof else '') + '）')


def _pixfmt_shape(name: str) -> Tuple[int, str]:
    """从像素格式名解析 (位深, 色度采样)，用于判断"让位"是否伴随信息损失。

    `--pix-fmt` 落不了地、改由 `--bit-depth` 接管时，两者的差异必须说清：
    `yuv420p10le` → `p010le` 是同一个东西（无损失），而 `yuv444p` → `p010le`
    是把 4:4:4 降成了 4:2:0（有损失，strict 下要报错）。所以要能比对"形状"。

    只覆盖 yuv* / yuvj* / p0* / nv12 这一族名字；认不出的返回 (8, '')，
    调用方按"形状不同"保守处理——宁可多报一次降级，也不少报。
    """
    n = (name or '').strip().lower()
    if n.startswith('p0') and len(n) >= 4 and n[2:4].isdigit():
        depth = int(n[2:4])                     # p010le → 10、p012le → 12
    elif len(n) >= 5 and n[-2:] in ('le', 'be') and n[-4:-2].isdigit():
        depth = int(n[-4:-2])                   # yuv420p10le → 10
    else:
        depth = 8                               # yuv420p / nv12 / yuv444p
    chroma = ''
    for tag in ('444', '422', '420'):
        if tag in n:
            chroma = tag                        # yuv420p10le → 420
            break
    if not chroma and (n.startswith('nv12') or n.startswith('p0')):
        chroma = '420'                          # 半 planar 的 4:2:0
    return depth, chroma


def validate_output_dimensions(width: int, height: int,
                               pix_fmt: Optional[str]) -> None:
    """校验输出尺寸与像素格式的奇偶约束（与 vidcrop_cpu_v2.py 的同名函数一致）。

    4:2:0 高位深（yuv420p10le / p010le / p012le …）要求宽高均为偶数——继承位深后
    若目标尺寸是奇数，ffmpeg 会在编码器初始化时才报错，这里提前拦住。
    """
    if width <= 0 or height <= 0:
        raise ValueError(f'输出尺寸必须为正整数，当前 {width}x{height}')
    if not pix_fmt:
        return
    pf = pix_fmt.lower()
    if pf in _PIXFMT_REQUIRE_EVEN_BOTH:
        if width % 2 or height % 2:
            raise ValueError(
                f'像素格式 {pix_fmt} 要求输出宽高均为偶数；当前为 {width}x{height}')
    elif pf in _PIXFMT_REQUIRE_EVEN_WIDTH:
        if width % 2:
            raise ValueError(
                f'像素格式 {pix_fmt} 要求输出宽度为偶数；当前为 {width}')


def _pix_fmt_exists(ffmpeg_bin: str, pix_fmt: str) -> bool:
    """惰性校验像素格式名是否是 ffmpeg 认识的名字（ffmpeg -pix_fmts）。

    只在用户**显式**给出 --pix-fmt 时才跑：拼错一个格式名原本要等到 ffmpeg 才报，
    错误信息是难读的 "Unrecognized pixel format" 之类，早一步拦住更省事。
    探测本身失败（找不到 ffmpeg / 超时 / 沙箱）时放行，不因此阻塞正常任务。
    """
    try:
        r = subprocess.run([ffmpeg_bin, '-hide_banner', '-pix_fmts'],
                           capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        if r.returncode != 0:
            return True
        import re
        return re.search(r'^\S+\s+' + re.escape(pix_fmt) + r'\s',
                         r.stdout or '', re.M) is not None
    except Exception:
        return True


def _apply_pix_fmt_to_cuda_filter(vf_filter: str, pix_fmt: str) -> Optional[str]:
    """零拷贝 CUDA 链上把 format= 写进链首的 scale_cuda（该链不能传 -pix_fmt）。

    只在链首确实是 scale_cuda 时成立；链首是别的滤镜就返回 None，由调用方按
    --fallback-policy 决定降级还是报错。
    """
    first, sep, rest = vf_filter.partition(',')
    name, eq, args = first.partition('=')
    if name.strip() != 'scale_cuda' or not eq:
        return None
    out = f'{name}{eq}{args}:format={pix_fmt}'
    if sep:
        # ⚠ 缩放输出的格式变了，紧随其后的 hwdownload 必须按**同一个**格式下载：
        # 链里那个 format= 是按源位深烘进去的（_src_download_fmt），不改就会出现
        # "scale_cuda 输出 p010le、却要 hwdownload,format=nv12" 的错配。
        import re
        out = (f"{out}{sep}"
               f"{re.sub(r'hwdownload,format=[^,]+', f'hwdownload,format={pix_fmt}', rest)}")
    return out


_BITMAP_SUBS = {'dvd_subtitle', 'dvb_subtitle', 'dvb_teletext',
                'hdmv_pgs_subtitle', 'xsub'}
_MP4_FAMILY = {'mp4', 'm4v', 'mov'}

# ffmpeg 输出端 -color_trc 与 setparams 滤镜接受的取值集合不一致（6.1 实测）：
#   -color_trc           只认 libavutil 规范名 gamma22/gamma28（BT.470M/BT.470BG）
#   setparams=color_trc  只认别名 bt470m/bt470bg，传 gamma28 直接报错
# 因此输出端用规范名、滤镜端用别名，两者语义等价（-color_trc gamma28 写出 bt470bg）。
_TRC_OUTPUT_NAMES = {'bt470bg': 'gamma28', 'bt470m': 'gamma22'}
_TRC_FILTER_NAMES = {v: k for k, v in _TRC_OUTPUT_NAMES.items()}

_FFMPEG_MAJOR: Optional[int] = None


def _probe_cache_key(path: str) -> str:
    """缓存键：绝对路径 + size + mtime（与 ffprobe 版本无关，进程内安全）。"""
    ap = os.path.abspath(path)
    try:
        st = os.stat(ap)
        return f'{ap}|{st.st_size}|{int(st.st_mtime)}'
    except OSError:
        return ap


def _frac_to_float(value) -> Optional[float]:
    """ffprobe 有理数：'34000/50000' / [34000, 50000] / 0.68 → float。"""
    try:
        if isinstance(value, (list, tuple)):
            num, den = float(value[0]), float(value[1])
        else:
            s = str(value)
            if '/' in s:
                a, _, b = s.partition('/')
                num, den = float(a), float(b)
            else:
                num, den = float(s), 1.0
        return num / den if den else None
    except (TypeError, ValueError):
        return None


def _parse_rate(rate) -> Optional[float]:
    """'25/1' / '30000/1001' → float；无法解析返回 None。"""
    try:
        if isinstance(rate, (list, tuple)):
            num, den = float(rate[0]), float(rate[1])
        else:
            num_s, _, den_s = str(rate).partition('/')
            num, den = float(num_s), float(den_s) if den_s else 1.0
        return num / den if den > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_bits(video_stream: Dict) -> int:
    """位深：bits_per_raw_sample 优先（实测可能是 'N/A'），回退从 pix_fmt 名解析。"""
    try:
        n = int(str(video_stream.get('bits_per_raw_sample')).strip())
        if n in (8, 10, 12, 14, 16):
            return n
    except (TypeError, ValueError):
        pass
    pf = (video_stream.get('pix_fmt') or '').lower()
    for token, bits in (('p016le', 16), ('p014le', 14), ('p012le', 12),
                        ('p010le', 10), ('p16le', 16), ('p12le', 12),
                        ('p10le', 10)):
        if token in pf:
            return bits
    return 8


def _norm_rotation(deg) -> int:
    try:
        return int(round(float(deg))) % 360
    except (TypeError, ValueError):
        return 0


def _extract_rotation(stream: Dict) -> int:
    """优先 side_data_list 的 Display Matrix，兜底容器里的 rotate tag。"""
    for sd in (stream.get('side_data_list') or []):
        if sd.get('rotation') is not None:
            return _norm_rotation(sd['rotation'])
    tags = stream.get('tags') or {}
    for key in ('rotate', 'rotation'):
        if key in tags:
            return _norm_rotation(tags[key])
    return 0


def _extract_hdr_from_side_data(sd_list: List[Dict]) -> Dict:
    """
    解析 HDR10 静态元数据。

    ffprobe 6.1 实测：Mastering display metadata / Content light level metadata
    只在**帧级** side_data 暴露（-show_frames），字段为扁平的 red_x/green_x/.../
    white_point_x/max_luminance；老版本或某些容器则给出 display_primaries 数组。
    两种形态都兼容，解析不出就返回空，调用方优雅降级。
    """
    out: Dict = {'master_display': None, 'max_cll': None}
    for sd in sd_list or []:
        stype = (sd.get('side_data_type') or '').lower()
        if 'mastering display' in stype:
            # 老版本/部分容器给出 display_primaries 数组，先摊平成 red_x/... 形态
            if 'red_x' not in sd and (sd.get('display_primaries')
                                      or sd.get('display_primaries_rgb')
                                      or sd.get('white_point')):
                prim = sd.get('display_primaries') or sd.get('display_primaries_rgb') or []
                wpt = sd.get('white_point') or []
                flat: Dict = {}
                for name, item in (('red', prim[0] if len(prim) > 0 else None),
                                   ('green', prim[1] if len(prim) > 1 else None),
                                   ('blue', prim[2] if len(prim) > 2 else None),
                                   ('white_point', wpt or None)):
                    if item is None:
                        continue
                    vals = item if isinstance(item, (list, tuple)) \
                        else (item.get('x'), item.get('y'))
                    try:
                        flat[name + '_x'] = vals[0]
                        flat[name + '_y'] = vals[1]
                    except (TypeError, IndexError, AttributeError):
                        pass
                sd = {**sd, **flat}

            def _chroma(key: str) -> Optional[int]:
                """色度坐标 → x265 单位（0.00002）。"""
                v = _frac_to_float(sd.get(key))
                return None if v is None else int(round(v * 50000))

            def _luma(key: str) -> Optional[int]:
                """亮度 → x265 单位（0.0001 cd/m²）。"""
                v = _frac_to_float(sd.get(key))
                return None if v is None else int(round(v * 10000))

            r = (_chroma('red_x'), _chroma('red_y'))
            g = (_chroma('green_x'), _chroma('green_y'))
            b = (_chroma('blue_x'), _chroma('blue_y'))
            wp = (_chroma('white_point_x'), _chroma('white_point_y'))
            mx, mn = _luma('max_luminance'), _luma('min_luminance')
            if None not in (*r, *g, *b, *wp) and mx is not None and mn is not None:
                # x265 语法顺序为 G()B()R()
                out['master_display'] = (
                    f'G({g[0]},{g[1]})B({b[0]},{b[1]})R({r[0]},{r[1]})'
                    f'WP({wp[0]},{wp[1]})L({mx},{mn})'
                )
        elif 'content light level' in stype:
            max_c = sd.get('max_content')
            avg = sd.get('max_average', sd.get('max_pic_average'))
            if max_c is not None and avg is not None:
                try:
                    out['max_cll'] = f'{int(max_c)},{int(avg)}'
                except (TypeError, ValueError):
                    pass
    return out


def _extract_hdr(stream: Dict) -> Dict:
    return _extract_hdr_from_side_data(stream.get('side_data_list') or [])


def _looks_hdr(video_stream: Dict, src_bits: int) -> bool:
    """判断是否需要为 HDR 静态元数据额外做一次帧级探测。"""
    if src_bits < 10:
        return False
    trc = (video_stream.get('color_transfer') or '').lower()
    prim = (video_stream.get('color_primaries') or '').lower()
    return trc in ('smpte2084', 'arib-std-b67', 'smpte2084-hdr10') or prim == 'bt2020'


def _probe_frame_side_data(path: str, ffmpeg_bin: str = 'ffmpeg') -> List[Dict]:
    """
    读首帧 side_data（HDR10 静态元数据在 ffmpeg 6.1 只在帧级暴露）。
    只读 1 帧，开销可忽略；失败返回空列表。
    """
    cmd = [_get_ffprobe_bin(ffmpeg_bin), '-v', 'error', '-print_format', 'json',
           '-select_streams', 'v:0', '-show_frames',
           '-read_intervals', '%+#1', path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=20,
                           check=False, env=_ffmpeg_env())
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        for frame in data.get('frames') or []:
            sd = frame.get('side_data_list')
            if sd:
                return sd
    except Exception:
        pass
    return []


def probe_full_metadata(video_file, ffmpeg_bin: str = 'ffmpeg',
                        errors: Optional[List[str]] = None) -> Optional[Dict]:
    """
    一次 ffprobe 拿到 format.tags / 各流 tags+disposition / side_data / pix_fmt /
    bits_per_raw_sample / SAR / 帧率 / 章节，并按 abspath|size|mtime 缓存。

    Returns:
        {'format':..., 'video':<原始视频流dict>, 'streams':..., 'chapters':...,
         'derived':{rotation,width,height,effective_width,effective_height,pix_fmt,
                    src_bits,video_index,cover_indices,subtitle_codecs,
                    is_hdr,master_display,max_cll}}
        探测失败返回 None。
    """
    path = str(video_file)
    if not os.path.isfile(path):
        return None

    key = _probe_cache_key(path)
    with _PROBE_CACHE_LOCK:
        cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached

    cmd = [_get_ffprobe_bin(ffmpeg_bin), '-v', 'error', '-print_format', 'json',
           '-show_format', '-show_streams', '-show_chapters', path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=15,
                           check=True, env=_ffmpeg_env())
        data = json.loads(r.stdout)
    except Exception as exc:
        if errors is not None:
            errors.append(str(exc))
        return None

    streams = data.get('streams') or []
    video = next((s for s in streams
                  if s.get('codec_type') == 'video'
                  and not (s.get('disposition') or {}).get('attached_pic')), None)
    if video is None:
        video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if video is None:
        if errors is not None:
            errors.append('no video stream')
        return None

    rotation = _extract_rotation(video)
    width = int(video.get('width') or 0)
    height = int(video.get('height') or 0)
    derived = {
        'rotation': rotation,
        'width': width,
        'height': height,
        # 显示尺寸：90/270 度旋转时宽高互换
        'effective_width': height if rotation in (90, 270) else width,
        'effective_height': width if rotation in (90, 270) else height,
        'pix_fmt': video.get('pix_fmt') or '',
        'src_bits': _parse_bits(video),
        'video_index': int(video.get('index') or 0),
        'cover_indices': [int(s['index']) for s in streams
                          if (s.get('disposition') or {}).get('attached_pic')],
        'subtitle_codecs': [s.get('codec_name') for s in streams
                            if s.get('codec_type') == 'subtitle'],
    }
    hdr = _extract_hdr(video)
    if not (hdr['master_display'] or hdr['max_cll']) \
            and _looks_hdr(video, derived['src_bits']):
        hdr = _extract_hdr_from_side_data(_probe_frame_side_data(path, ffmpeg_bin))
    derived.update(hdr)
    derived['is_hdr'] = bool(hdr['master_display'] or hdr['max_cll'])

    meta = {
        'path': path,
        'format': {
            'format_name': (data.get('format') or {}).get('format_name') or '',
            'duration': (data.get('format') or {}).get('duration') or '',
            'bit_rate': (data.get('format') or {}).get('bit_rate') or '',
            'tags': dict((data.get('format') or {}).get('tags') or {}),
        },
        'video': video,
        'streams': streams,
        'chapters': data.get('chapters') or [],
        'derived': derived,
    }
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[key] = meta
    return meta


def _ffmpeg_major(ffmpeg_bin: str = 'ffmpeg') -> int:
    """ffmpeg 主版本号（用于选择旋转写法）；探测失败按 6 处理。"""
    global _FFMPEG_MAJOR
    if _FFMPEG_MAJOR is not None:
        return _FFMPEG_MAJOR
    try:
        out = subprocess.run([ffmpeg_bin, '-version'], capture_output=True,
                             text=True, timeout=10).stdout
        import re
        m = re.search(r'ffmpeg version (\d+)\.', out)
        _FFMPEG_MAJOR = int(m.group(1)) if m else 6
    except Exception:
        _FFMPEG_MAJOR = 6
    return _FFMPEG_MAJOR


def resolve_pix_fmt_for_source(codec: str, src_bits: int,
                               warn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """源为 10bit+ 时选择保持位深的目标 pix_fmt；8bit 源返回 None（沿用默认）。"""
    if src_bits < 10:
        return None
    c = (codec or '').lower()
    if c in _ENCODERS_8BIT_ONLY:
        if warn:
            warn(f'源为 {src_bits}bit，编码器 {c} 不支持 10bit，已降级为 yuv420p'
                 f'（高光可能出现色带）')
        return 'yuv420p'
    return _PIXFMT_10BIT_BY_ENCODER.get(c, 'yuv420p10le')


def resolve_subtitle_codec(src_subs: List[Optional[str]], container: str,
                           warn: Optional[Callable[[str], None]] = None):
    """
    按「源字幕 codec × 目标容器」决定字幕编码方式。

    mp4 里 -c:s copy 对 subrip/ass 会硬失败（Could not find tag for codec），
    位图字幕则根本无法转换，必须丢弃并告警。
    """
    ctr = (container or '').lower().lstrip('.')
    subs = [s for s in (src_subs or []) if s]
    if not subs:
        return None, False
    if ctr in ('mkv', 'webm'):
        if ctr == 'mkv':
            return 'copy', True
        return ('copy', True) if all(s == 'webvtt' for s in subs) else (None, False)
    if ctr in _MP4_FAMILY:
        if any(s in _BITMAP_SUBS for s in subs):
            if warn:
                warn('位图字幕（PGS/DVDSUB 等）无法写入 mp4，已丢弃')
            return None, False
        if warn and any(s in ('ass', 'ssa') for s in subs):
            warn('ASS/SSA 转为 mov_text 后会丢失样式')
        return 'mov_text', True
    return None, False


def build_hdr_args(meta: Optional[Dict], codec: str,
                   warn: Optional[Callable[[str], None]] = None,
                   hdr_mode: str = 'auto',
                   extra_x265_params: Optional[List[str]] = None) -> List[str]:
    """
    HDR10 静态元数据写入。色彩三参数由 build_color_args 从源透传，此处不重复指定。

    hdr_mode 由 --hdr 给出：
      auto / keep  沿用既有行为（libx265 显式写；NVENC 与其它的告警）
      drop / sdr   **不写** HDR 静态元数据（这两种模式都按 SDR 打标签；
                   sdr 还会在滤镜链里真做 tone mapping，见 build_tonemap_filter）
    - libx265：显式 -x265-params，可靠。
    - NVENC：依赖帧 side_data 自动传播；走 hwdownload/CPU 回退链路时会丢失，故告警。
    - 其余编码器：只能保住色彩三参数与位深。

    extra_x265_params 是**另外要写进 -x265-params 的键**（来源见 apply_rc_control_args
    与 build_ffmpeg_cmd：--lookahead 的 rc-lookahead、crf=0 的 lossless=1）。为什么
    必须合并成同一条：实测 `-x265-params A -x265-params B` 是**后者整条覆盖前者**
    （本机 ffmpeg：先 `rc-lookahead=40` 再 `log-level=info`，x265 报出的 Lookahead
    回到默认 20）——HDR 元数据与 lookahead/lossless 都走这条选项，各发一条会让后发的
    静默抹掉 HDR 元数据。

    meta 允许为 None（探测失败 / --keep-metadata 关闭）：此时只落 extra_x265_params，
    HDR 部分跳过。调用方**必须无条件调用本函数**——过去用 `if meta is not None` 包住，
    会把 lookahead / lossless 这些与 HDR 无关的键一起静默丢掉。
    """
    c = (codec or '').lower()
    extra = list(extra_x265_params or []) if c == 'libx265' else []
    d = meta['derived'] if meta else None
    params: List[str] = []
    if d and d['is_hdr'] and hdr_mode not in _HDR_SDR_TAGGING_MODES:
        if c == 'libx265' and d['master_display']:
            params = ['master-display=' + d['master_display']]
            if d['max_cll']:
                params.append('max-cll=' + d['max_cll'])
            params.append('hdr10=1')
        elif c.endswith('_nvenc'):
            # 实测（ffmpeg 6.1 + Tesla T4）：NVENC 无论走 cuda 全 GPU 还是 CPU 解码都
            # 不写入 mastering display / MaxCLL，hevc_metadata bsf 也无此能力。
            if warn and d['master_display']:
                warn('NVENC 不写入 mastering display / MaxCLL，HDR10 静态元数据会丢失'
                     '（色彩三参数与 10bit 位深仍保留）；如需完整 HDR10 元数据请用 libx265')
        elif warn and d['master_display']:
            warn(f'编码器 {c} 无法写入 mastering display / MaxCLL，仅保留色彩三参数与位深')
    params += extra
    return ['-x265-params', ':'.join(params)] if params else []


def build_aspect_args(meta: Dict) -> List[str]:
    """
    仅当源为变形（SAR≠1:1）时显式 -aspect 保持源 DAR。
    方像素源不加：crop 后 ffmpeg 保持 SAR 自动算出正确的新 DAR。
    """
    sar = meta['video'].get('sample_aspect_ratio') or '1:1'
    try:
        n_s, _, d_s = str(sar).partition(':')
        n, d = int(n_s), int(d_s) if d_s else 1
    except (TypeError, ValueError):
        return []
    if n <= 0 or d <= 0 or (n == 1 and d == 1):
        return []
    src_w, src_h = meta['derived']['width'], meta['derived']['height']
    if not src_w or not src_h:
        return []
    from math import gcd
    dan, dad = n * src_w, d * src_h
    g = gcd(dan, dad) or 1
    return ['-aspect', f'{dan // g}/{dad // g}']


def build_preserve_args(meta: Optional[Dict], container: str, codec: str,
                        ffmpeg_bin: str = 'ffmpeg',
                        warn: Optional[Callable[[str], None]] = None) -> Dict[str, List[str]]:
    """
    生成保留原片元数据所需的 ffmpeg 参数，按位置分成三组：

      input: 必须放在 -i 之前（-noautorotate / -display_rotation）
      map  : 紧跟 -i 之后（流映射、-map_metadata、-map_chapters、creation_time、-aspect）
      post : 放在编码器选项之后（封面/字幕的逐流 codec，需覆盖 -c:v 通用设置）

    meta 为 None（探测失败）时三组均为空，调用方回退原有窄映射行为。
    """
    empty: Dict[str, List[str]] = {'input': [], 'map': [], 'post': []}
    if meta is None:
        return empty

    d = meta['derived']
    ctr = (container or '').lower().lstrip('.')
    inp: List[str] = ['-noautorotate']      # 不烘焙旋转，保留 display matrix
    mp: List[str] = []
    post: List[str] = []

    # 旋转：6.x+ 用 input 侧 -display_rotation（6.1 无流说明符，作用于后续 -i 的
    # 整个文件，此处只有一个输入故安全）；更老版本用 mov 的 rotate tag
    if d['rotation']:
        if _ffmpeg_major(ffmpeg_bin) >= 6:
            inp += ['-display_rotation', str(d['rotation'])]
        else:
            post += ['-metadata:s:v:0', f"rotate={d['rotation']}"]

    # 主视频轨用绝对索引，天然避开 mp4 封面轨（attached_pic）
    mp += ['-map', f"0:{d['video_index']}"]
    # 封面轨单独映射，且必须显式 copy（否则单帧 PNG/MJPEG 会被送去编码而失败）
    for i, ci in enumerate(d['cover_indices'][:1]):
        mp += ['-map', f'0:{ci}']
        post += [f'-c:v:{i + 1}', 'copy', f'-disposition:v:{i + 1}', 'attached_pic']
    mp += ['-map', '0:a?']

    sub_codec, need_sub = resolve_subtitle_codec(d['subtitle_codecs'], ctr, warn)
    if need_sub:
        mp += ['-map', '0:s?']
        if sub_codec:
            post += ['-c:s', sub_codec]
    if ctr in ('mkv', 'webm'):
        mp += ['-map', '0:t?']

    # -metadata 会覆盖 -map_metadata，故 creation_time 必须放在其后
    mp += ['-map_metadata', '0', '-map_chapters', '0']
    creation_time = (meta['format'].get('tags') or {}).get('creation_time')
    if creation_time:
        mp += ['-metadata', f'creation_time={creation_time}']

    mp += build_aspect_args(meta)
    return {'input': inp, 'map': mp, 'post': post}


# ═══════════════════════════════════════════════════════════════════
#  ffprobe 探测
# ═══════════════════════════════════════════════════════════════════

def get_video_metadata(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Optional[Dict]:
    """返回 probe_full_metadata 结果（带缓存）；探测失败返回 None。"""
    return probe_full_metadata(filepath, ffmpeg_bin)


def get_video_dimensions_ex(
    filepath: str, ffmpeg_bin: str = 'ffmpeg'
) -> Tuple[int, int, int, int, int]:
    """
    返回 (width, height, rotation, effective_width, effective_height)。

    width/height 是**存储坐标系**（未旋转），与 crop/crop_cuda 的裁剪坐标同一坐标系；
    rotation 非 0 时 effective_* 才是播放器里的显示尺寸。
    """
    meta = probe_full_metadata(filepath, ffmpeg_bin)
    if not meta:
        raise RuntimeError(f'无法探测视频尺寸：{filepath}')
    d = meta['derived']
    return (d['width'], d['height'], d['rotation'],
            d['effective_width'], d['effective_height'])


def get_video_dimensions(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Tuple[int, int]:
    """通过 ffprobe 获取视频的宽度和高度（存储坐标系，未应用旋转）。"""
    meta = probe_full_metadata(filepath, ffmpeg_bin)
    if not meta:
        # 探测失败：保留原有报错行为
        raise RuntimeError(f'无法探测视频尺寸：{filepath}')
    d = meta['derived']
    return d['width'], d['height']


def _get_total_frames(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Optional[int]:
    """
    探测视频总帧数。
    优先读取 nb_frames；不可用时按 duration × fps 估算；失败则返回 None。
    复用 probe_full_metadata 缓存，避免同一文件重复 ffprobe。
    """
    meta = probe_full_metadata(filepath, ffmpeg_bin)
    if not meta:
        return None
    s = meta['video']
    nb = s.get('nb_frames', '')
    if nb and nb not in ('N/A', ''):
        try:
            return max(1, int(nb))
        except (TypeError, ValueError):
            pass
    # 降级：duration × fps
    duration = 0.0
    for c in (s.get('duration'), meta['format'].get('duration')):
        try:
            duration = float(c or 0)
            if duration > 0:
                break
        except (TypeError, ValueError):
            pass
    fps = _parse_rate(s.get('r_frame_rate') or '0/1') or 0.0
    if duration > 0 and fps > 0:
        return max(1, int(duration * fps))
    return None


# ═══════════════════════════════════════════════════════════════════
#  [COLOR-FIX] 色彩元数据注入
# ═══════════════════════════════════════════════════════════════════

def probe_color_metadata(video_file: Path, ffmpeg_bin: str = 'ffmpeg') -> Optional[Dict[str, str]]:
    """
    用 ffprobe 读取视频第一个视频流的色彩元数据
    （color_range / color_space / color_primaries / color_transfer）。

    Args:
        video_file: 视频文件路径。
        ffmpeg_bin: FFmpeg 可执行文件路径，ffprobe 从同目录推导（保证版本一致）。

    Returns:
        四项色彩值的字典（可能为 'unknown'）；无法探测时返回 None。
    """
    m = probe_full_metadata(video_file, ffmpeg_bin)
    if not m:
        return None
    v = m['video']
    return {k: v.get(k, 'unknown') for k in
            ('color_range', 'color_space', 'color_primaries', 'color_transfer')}


def _effective_source_range(meta: Dict) -> str:
    """源的实际 color_range：有值取源值，unknown 时按 tv（与 ffmpeg 默认解释一致）。"""
    v, d = meta['video'], meta['derived']
    rng = (v.get('color_range') or '').lower()
    if rng in ('tv', 'pc'):
        return rng
    return 'pc' if d['pix_fmt'].lower().startswith('yuvj') else 'tv'


def build_range_convert_filter(meta: Optional[Dict], color_range: Optional[str],
                               vf_first_filter: Optional[str] = None,
                               warn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    --color-range 的配套实现：把像素值域真正转到目标 range。

    只在「显式强制 --color-range tv/pc」且「与源实际 range 不同」时才需要转换。
    auto / 未指定 / 与源相同 时不插入任何滤镜，零额外开销。
    scale 是 CPU 滤镜，链首若是 crop_cuda / scale_cuda 等 CUDA 原生滤镜则无法直接
    接在后面，此时返回 None 并告警（避免生成必然失败的命令）。

    Returns:
        scale 滤镜字符串；无需转换或无法转换时返回 None。
    """
    if meta is None or not color_range or color_range.lower() not in ('tv', 'pc'):
        return None
    src, tgt = _effective_source_range(meta), color_range.lower()
    if src == tgt:
        return None
    if vf_first_filter and vf_first_filter in _CUDA_NATIVE_FILTERS:
        if warn:
            warn('CUDA 原生滤镜链路（scale_cuda / crop_cuda）不在中途做 CPU 端 '
                 'scale，无法插入 range 转换，已跳过（color_range 只改标签）')
        return None
    if warn:
        warn(f'color_range 由源 {src} 转换为 {tgt}（插入 scale 滤镜做实际值域转换）')
    return f'scale=w=iw:h=ih:in_range={src}:out_range={tgt}'


def build_color_args(video_file: Path, ffmpeg_bin: str = 'ffmpeg',
                     meta: Optional[Dict] = None,
                     color_range: Optional[str] = None,
                     hdr_mode: str = 'auto') -> List[str]:
    """
    构造 ffmpeg 输出端色彩参数列表。

    color_range（--color-range）:
        None / 'auto'  自动：源 color_range 为 unknown 时取 tv，否则取源值。
                       （unknown 时 ffmpeg 本就按 tv 解释并解码，取 tv 可与源
                       逐像素保持一致；只有 pix_fmt 为 yuvj* 才是真的 full range）
        'tv' / 'pc'    强制覆盖，忽略源值；若与源实际值域不同，会自动插入 scale
                       滤镜做真正的像素值域转换（不再只改标签），值域一致时无开销。

    Args:
        video_file: 源视频路径，用于探测色彩元数据。
        ffmpeg_bin: FFmpeg 可执行文件路径，ffprobe 从同目录推导。
        meta: 可选的 probe_full_metadata 结果，传入可避免重复 ffprobe。
        color_range: 显式 range 覆盖（'tv' / 'pc' / 'auto' / None）。

    Returns:
        ffmpeg 参数列表；探测失败时返回空列表（让编码器自行决定，不瞎猜）。
    """
    m = meta if meta is not None else probe_full_metadata(video_file, ffmpeg_bin)
    if not m:
        return []

    v = m['video']
    d = m['derived']

    def _val(key: str) -> Optional[str]:
        x = (v.get(key) or '').lower()
        return None if x in ('', 'unknown', 'unspecified', 'n/a') else x

    space = _val('color_space')
    prim = _val('color_primaries')
    trc = _val('color_transfer')
    rng = _val('color_range')

    if rng is None:
        # auto：只有 yuvj* 才是真的 full range；其余一律按 tv（limited）
        rng = 'pc' if d['pix_fmt'].lower().startswith('yuvj') else 'tv'
    if color_range and color_range.lower() in ('tv', 'pc'):
        rng = color_range.lower()          # 显式覆盖优先于探测值与 auto 推断

    if space is None or prim is None or trc is None:
        if d['src_bits'] >= 10 and (d['width'] >= 1920 or d['height'] >= 1080):
            guess = ('bt2020nc', 'bt2020', 'bt709')
        elif d['height'] >= 720:
            guess = ('bt709', 'bt709', 'bt709')
        elif d['src_bits'] >= 10:
            # 高位深的小分辨率内容基本不存在标清广播电视色彩，按 bt709 更合理
            guess = ('bt709', 'bt709', 'bt709')
        else:
            fps = _parse_rate(v.get('avg_frame_rate') or v.get('r_frame_rate'))
            is_pal = fps is not None and (abs(fps - 25) < 0.3 or abs(fps - 50) < 0.3)
            guess = ('bt470bg', 'bt470bg', 'bt470bg') if is_pal else \
                    ('smpte170m', 'smpte170m', 'smpte170m')
        space = space or guess[0]
        prim = prim or guess[1]
        trc = trc or guess[2]

    # 输出端 -color_trc 不接受 bt470bg/bt470m，需换成 libavutil 规范名
    trc = _TRC_OUTPUT_NAMES.get(trc, trc)

    # --hdr drop / sdr：整条按 SDR 交付，色彩标签必须跟着改成 BT.709，
    # 否则容器里还写着 bt2020/arib-std-b67，播放器会当成 HDR 去解释 SDR 像素。
    if hdr_mode in _HDR_SDR_TAGGING_MODES:
        space, prim, trc = 'bt709', 'bt709', 'bt709'

    return [
        '-colorspace', space,
        '-color_primaries', prim,
        '-color_trc', trc,
        '-color_range', rng,
    ]


def _setparams_from_color_args(extra_args: List[str]) -> Optional[str]:
    """
    从 build_color_args 生成的色彩参数列表中提取取值，构造 setparams 滤镜字符串。

    原因：libx264 等软件编码器对输出端 -color_primaries/-color_trc 参数不写入 VUI，
    需用 setparams 滤镜显式注入帧级色彩属性（NVENC/AMF 等 GPU 编码器靠输出端参数即可）。

    Args:
        extra_args: ffmpeg 输出端参数列表（含 -colorspace/-color_primaries 等）。

    Returns:
        setparams 滤镜字符串（如 'setparams=colorspace=bt709:color_primaries=bt709:...'），
        无色彩参数时返回 None。
    """
    color_map = {
        '-colorspace': 'colorspace',
        '-color_primaries': 'color_primaries',
        '-color_trc': 'color_trc',
        '-color_range': 'range',
    }
    vals: Dict[str, str] = {}
    i = 0
    while i < len(extra_args) - 1:
        key = extra_args[i]
        if key in color_map:
            value = extra_args[i + 1]
            # 滤镜端只认别名（bt470bg），不认规范名（gamma28）
            vals[color_map[key]] = _TRC_FILTER_NAMES.get(value, value)
            i += 2
        else:
            i += 1
    if not vals:
        return None
    return 'setparams=' + ':'.join(f'{k}={v}' for k, v in vals.items())


# ═══════════════════════════════════════════════════════════════════
#  产物色度自检（防复发钩子）
# ═══════════════════════════════════════════════════════════════════
# 为什么需要：2026-09-21 的色度归零缺陷（产物全绿）在线下跑了几个小时才被发现，
# 因为当时没有任何"产物对不对"的检查，只有 ffmpeg 的 rc。这里给策略执行加一道
# 像素级自检，失败即当成策略失败 → 复用既有的降级链（软解/软编策略），不用新写降级。
#
# 阈值口径（探针 SELFTEST 已验证不误伤，见 probe/probe_green_chroma.sh）：
#   · 真灰度片是 U=V=128（中性），**不是 0** ⇒ 不触发；
#   · 满屏纯绿 RGB(0,255,0) → U≈54 / V≈0 ⇒ U<16 不成立 ⇒ 不触发；
#   · 本缺陷 U=V≈0 而源 U≈127 ⇒ 触发；
#   · 源本身退化（U_src<16 且 V_src<16）⇒ 直接返回 OK。

def _parse_signalstats_avg(text: str) -> Optional[Tuple[float, float, float]]:
    """从 `signalstats,metadata=print` 的日志里取 (YAVG, UAVG, VAVG) 的帧均值。"""
    out: List[float] = []
    for key in ('YAVG', 'UAVG', 'VAVG'):
        vals = [float(x) for x in
                re.findall(r'lavfi\.signalstats\.' + key + r'=([0-9.]+)', text)]
        if not vals:
            return None
        out.append(sum(vals) / len(vals))
    return out[0], out[1], out[2]


def _sample_uv_avg(path, ffmpeg_bin: str = 'ffmpeg', ss: float = 1.0,
                   timeout: int = 30) -> Optional[Tuple[float, float, float]]:
    """解码取样某文件的 (YAVG, UAVG, VAVG)。失败返回 None（由调用方按"不判定"处理）。"""
    cmd = [ffmpeg_bin, '-nostdin', '-hide_banner', '-loglevel', 'info',
           '-ss', f'{max(0.0, ss):.3f}', '-i', str(path), '-frames:v', '2', '-an',
           '-vf', 'format=yuv420p,signalstats,metadata=print', '-f', 'null', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout, env=_ffmpeg_env())
    except Exception:
        return None
    return _parse_signalstats_avg((r.stdout or '') + (r.stderr or ''))


def _chroma_sample_ss(meta: Optional[Dict]) -> float:
    """自检采样点：clamp(时长 × 0.1, 1, 60)。

    不用固定的第 1 秒：实测片头常是空白帧（正常片 ss=1 时 Y=16.0、U=V=128），
    落在 Y 门（16~235）之外会让"是否归零"根本无从判定，白白漏掉一次。
    """
    try:
        dur = float((meta or {}).get('format', {}).get('duration') or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    return min(max(dur * 0.1, 1.0), 60.0)


def _chroma_verdict(src: Tuple[float, float, float],
                    out: Tuple[float, float, float]) -> Tuple[bool, str]:
    """纯判定：给定源/产物的 (YAVG, UAVG, VAVG)，判断产物是否色度归零。

    与 _sample_uv_avg 解耦，方便不启 ffmpeg 直接单测阈值（verify/verify_chroma_hook.py）。
    """
    _ys, us, vs = src
    yo, uo, vo = out
    if us < 16 and vs < 16:
        return True, f'源本身无色度（U={us:.1f}/V={vs:.1f}），跳过自检'
    bad = (16 <= yo <= 235) and (uo < 16) and (vo < 16) and (abs(uo - vo) < 8) \
        and (us >= 16 or vs >= 16) \
        and (abs(us - uo) > 32 or abs(vs - vo) > 32)
    if bad:
        return False, (f'产物色度疑似归零：源 U={us:.1f}/V={vs:.1f} → '
                       f'产物 U={uo:.1f}/V={vo:.1f}')
    return True, f'色度正常（U={uo:.1f}/V={vo:.1f}）'


def _chroma_check(input_file, output_file, ffmpeg_bin: str = 'ffmpeg',
                  ss: float = 1.0) -> Tuple[bool, str]:
    """产物色度自检。返回 (是否正常, 说明)。

    取样失败 / 源本身无色度时一律判"正常"——这道钩子是**宁可漏判也不能误伤**，
    它只加一道保险，不该因为探针本身出问题就让整个任务失败。
    """
    src = _sample_uv_avg(input_file, ffmpeg_bin, ss)
    out = _sample_uv_avg(output_file, ffmpeg_bin, ss)
    if src is None or out is None:
        return True, '取样失败，跳过色度自检'
    ok, why = _chroma_verdict(src, out)
    return ok, (why if ok else f'{why}（取样 t={ss:.0f}s）')


# ═══════════════════════════════════════════════════════════════════
#  文件收集
# ═══════════════════════════════════════════════════════════════════

def collect_video_files(input_path: Path, recursive: bool = False) -> List[Path]:
    """
    收集输入路径下的所有视频文件。
    recursive=True 时递归扫描子目录（使用 rglob），并保持相对目录结构。
    """
    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_EXTENSIONS:
            return [input_path]
        print(f'警告：{input_path} 不是支持的视频格式，已跳过。', file=sys.stderr)
        return []

    if input_path.is_dir():
        if recursive:
            files = {
                p for p in input_path.rglob('*')
                if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
            }
        else:
            files = {
                p for p in input_path.iterdir()
                if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
            }
        return sorted(files)

    print(f'错误：输入路径 {input_path} 不存在。', file=sys.stderr)
    return []


# ═══════════════════════════════════════════════════════════════════
#  视频滤镜（crop / cover，含 CUDA 支持）
# ═══════════════════════════════════════════════════════════════════

# CPU 侧 scale 滤镜的缩放算法。**显式钉 lanczos**，理由两条：
#   ① 不吃 libswscale 的默认值。`scale` 滤镜自身的 flags 默认是空串、继承全局
#      `-sws_flags`，而后者默认是 bicubic——实测 `scale=W:H` 与
#      `scale=W:H:flags=bicubic` 的帧级 MD5 完全相同（psnr 三个平面全 inf）。
#      默认值等于"没指定、随实现走"，画质不可预期。
#   ② 与 GPU 侧同档。新链用 `scale_cuda=…:interp_algo=lanczos`（scale_cuda 的
#      最高档，量程 0~4 只到 lanczos），若 CPU 侧留 bicubic，同一批文件会在
#      GPU 上更锐、一旦降级到 CPU（无 scale_cuda 的构建 / 显式 --scale-algo
#      libswscale-* / 三轴全 CPU / 新链失败）就变软。
# 注意：libswscale 还有 spline / sinc / gauss / area 等档，但 scale_cuda 没有
# 对应档可选，取两者交集里最高的那个 → lanczos。
_SW_SCALE_FLAGS = 'lanczos'
# CUDA 侧 scale_cuda 的【默认】档（--scale-algo 未指定时用）
_CUDA_SCALE_ALGO = 'lanczos'

# ── --scale-algo 的取值表 ─────────────────────────────────────────────
# libswscale 侧只收 `scale` 滤镜 flags 里【真的能当算法用】的那些：
#   不收 experimental（要配 +unstable 才生效，收了等于给一个必然报错的取值）；
#   也不收 accurate_rnd / full_chroma_int / full_chroma_inp / bitexact /
#   error_diffusion / print_info / unstable 这些修饰位。
_SW_SCALE_ALGOS = (
    'fast_bilinear', 'bilinear', 'bicubic', 'neighbor', 'area',
    'bicublin', 'gauss', 'sinc', 'lanczos', 'spline',
)
# CUDA 侧 = scale_cuda 的 interp_algo 全部具名档（量程 0~4，0 未映射到具名档）
_CUDA_SCALE_ALGOS = ('nearest', 'bilinear', 'bicubic', 'lanczos')
# 两个后端唯一的"同名不同字"：libswscale 叫 neighbor、scale_cuda 叫 nearest
_SW_ALGO_ALIAS = {'nearest': 'neighbor'}
_CUDA_ALGO_ALIAS = {'neighbor': 'nearest'}

_SCALE_ALGO_HELP = ('libswscale：' + ' '.join(_SW_SCALE_ALGOS)
                    + '\n  cuda      ：' + ' '.join(_CUDA_SCALE_ALGOS)
                    + '（需自带 scale_cuda 的自建 FFmpeg）')


def parse_scale_algo(spec: Optional[str]) -> Tuple[str, str, str]:
    """
    解析 --scale-algo → (backend, sw_algo, cuda_algo)。

    backend ∈ 'auto' | 'libswscale' | 'cuda'。sw_algo / cuda_algo **两个都给全**——
    这样"强制 cuda 但环境不可用"时能直接拿 sw_algo 退回，不必再解析一遍。

    规则：
      · 未指定 / 空串      → ('auto', 默认, 默认)，即保持既有行为（不传就等于裸 lanczos）
      · 'libswscale-<a>'  → 强制 CPU 链 + 该算法
      · 'cuda-<a>'        → 强制 CUDA 缩放链 + 该算法
      · 裸 '<a>'          → 只定算法、后端自动；**要求两表都认**，
                            只在一个后端存在的算法必须带前缀（如 libswscale-spline）
    前缀大小写不敏感；nearest / neighbor 互为别名。
    """
    if not spec:
        return 'auto', _SW_SCALE_FLAGS, _CUDA_SCALE_ALGO
    s = spec.strip().lower()
    backend, algo = 'auto', s
    # 算法名里没有连字符（多词用下划线，如 fast_bilinear），所以出现 '-' 就说明
    # 用户想写前缀 → 前缀不认识就直接点明，别退化成"裸名字"给一句绕的报错。
    head, sep, tail = s.partition('-')
    if sep:
        if head not in ('libswscale', 'cuda'):
            raise ValueError(
                f"--scale-algo '{spec}' 无效：未知后端前缀 '{head}-'"
                f"（只支持 libswscale- / cuda-）。\n"
                f"  可用值：<backend>-<algo>，或裸 <algo>（后端自动）。\n  {_SCALE_ALGO_HELP}")
        backend, algo = head, tail
    if not algo:
        raise ValueError(
            f"--scale-algo '{spec}' 无效：前缀 '{backend}-' 后面缺少算法名。\n"
            f"  可用值：<backend>-<algo>，或裸 <algo>（后端自动）。\n  {_SCALE_ALGO_HELP}")

    def _sw(name: str) -> Optional[str]:
        n = _SW_ALGO_ALIAS.get(name, name)
        return n if n in _SW_SCALE_ALGOS else None

    def _cuda(name: str) -> Optional[str]:
        n = _CUDA_ALGO_ALIAS.get(name, name)
        return n if n in _CUDA_SCALE_ALGOS else None

    if backend == 'libswscale':
        sw = _sw(algo)
        if sw is None:
            raise ValueError(
                f"--scale-algo '{spec}' 无效：libswscale 没有算法 '{algo}'。\n"
                f"  可用值：<backend>-<algo>，或裸 <algo>（后端自动）。\n  {_SCALE_ALGO_HELP}")
        return backend, sw, _CUDA_SCALE_ALGO
    if backend == 'cuda':
        cuda = _cuda(algo)
        if cuda is None:
            raise ValueError(
                f"--scale-algo '{spec}' 无效：cuda 没有算法 '{algo}'。\n"
                f"  可用值：<backend>-<algo>，或裸 <algo>（后端自动）。\n  {_SCALE_ALGO_HELP}")
        return backend, _SW_SCALE_FLAGS, cuda

    sw, cuda = _sw(algo), _cuda(algo)
    if sw is None and cuda is None:
        raise ValueError(
            f"--scale-algo '{spec}' 无效：未知算法 '{algo}'。\n"
            f"  可用值：<backend>-<algo>，或裸 <algo>（后端自动）。\n  {_SCALE_ALGO_HELP}")
    if sw is None or cuda is None:
        missing = 'libswscale' if sw is None else 'cuda'
        raise ValueError(
            f"--scale-algo '{spec}' 无效：裸 '{algo}' 无法在两个后端同时确定"
            f"（{missing} 侧没有这个算法）。\n"
            f"  只在一个后端有的算法请带前缀，例如 --scale-algo {missing}-{algo}。\n"
            f"  可用值：<backend>-<algo>，或裸 <algo>（后端自动）。\n  {_SCALE_ALGO_HELP}")
    return backend, sw, cuda


def parse_rc_mode(spec: Optional[str]) -> str:
    """
    解析 --rc-mode → 'auto' | 'constqp' | 'vbr' | 'vbr_hq' | 'cbr' | 'cbr_hq' | 'cbr_ld_hq'。

    规则与 parse_scale_algo 同形（`<backend>-<取值>` 或裸 `<取值>`），但有两处**有意**
    的不同：

      · 本轴只有 NVENC 一个后端（`-rc` 是 NVENC 专属，libx264/libx265 没有这个开关），
        所以裸名不会歧义，一律接受，不必像 --scale-algo 那样要求"两个后端都认"；
      · **允许显式写 auto**。它的默认值就叫 auto，禁止它就会重演"帮助里写的默认值
        敲不出来"那个坑（--scale-algo auto 就是这种简写、不能敲）。
    """
    if not spec:
        return 'auto'
    s = spec.strip().lower()
    head, sep, tail = s.partition('-')
    if sep:
        if head != _RC_BACKEND:
            raise ValueError(
                f"--rc-mode '{spec}' 无效：未知后端前缀 '{head}-'"
                f"（本轴只有 {_RC_BACKEND}- —— -rc 是 NVENC 专属选项）。\n"
                f"  可用值：auto，或 <mode>（也可写 {_RC_BACKEND}-<mode>）。\n  {_RC_MODE_HELP}")
        s = tail
    if not s:
        raise ValueError(
            f"--rc-mode '{spec}' 无效：前缀 '{_RC_BACKEND}-' 后面缺少模式名。\n"
            f"  可用值：auto，或 <mode>（也可写 {_RC_BACKEND}-<mode>）。\n  {_RC_MODE_HELP}")
    # auto 也放行：它既是不传时的默认值，也允许显式写（含 nvenc-auto 前缀形式）
    if s == 'auto' or s in _RC_MODES:
        return s
    raise ValueError(
        f"--rc-mode '{spec}' 无效：NVENC 没有模式 '{s}'。\n"
        f"  可用值：auto，或 <mode>（也可写 {_RC_BACKEND}-<mode>）。\n  {_RC_MODE_HELP}")


def _build_crop_filter_str(orig_w: int, orig_h: int, out_w: int, out_h: int,
                            use_cuda: bool = False) -> str:
    """
    生成居中裁剪滤镜字符串。
    use_cuda=True 时使用 crop_cuda（需配合 -hwaccel_output_format cuda）。
    """
    if out_w > orig_w or out_h > orig_h:
        raise ValueError(
            f"crop 模式下目标尺寸 ({out_w}x{out_h}) 不能大于原始尺寸 ({orig_w}x{orig_h})"
        )
    x = (orig_w - out_w) // 2
    y = (orig_h - out_h) // 2
    fname = 'crop_cuda' if use_cuda else 'crop'
    return f'{fname}={out_w}:{out_h}:{x}:{y}'


def _cover_scale_dims(src_w: int, src_h: int, dst_w: int, dst_h: int) -> Tuple[int, int]:
    """cover 链「缩放之后、裁剪之前」的尺寸（CPU / CUDA 两侧共用的一套几何）。

    源更宽 → 以高度为基准缩放、宽按比例取偶；源更高 → 以宽度为基准；
    比例相同 → 直接就是目标尺寸。`-2` 的取偶语义在这里显式算出来
    （CUDA 链必须写显式尺寸，见 _build_cover_cuda_filter_str）。

    也用来回答「这次缩放是不是恒等操作」——恒等时缩放成本为 0，
    显存内缩放就没有可省的东西，只剩上载开销（实测必亏）。
    """
    if src_w <= 0 or src_h <= 0:
        return dst_w, dst_h
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if abs(src_ratio - dst_ratio) < 1e-3:
        return dst_w, dst_h
    if src_ratio > dst_ratio:
        return derive_even_dimension(src_w * dst_h / src_h), dst_h
    return dst_w, derive_even_dimension(src_h * dst_w / src_w)


def _build_cover_filter_str(src_w: int, src_h: int, dst_w: int, dst_h: int,
                            sw_algo: str = _SW_SCALE_FLAGS) -> str:
    """
    生成等比缩放+居中裁剪滤镜字符串（cover 模式）。
    策略：比较宽高比，确定缩放方向，再裁剪到目标区域。
    缩放算法由 --scale-algo 决定（默认见 _SW_SCALE_FLAGS 的注释：不吃 libswscale
    的默认 bicubic，且与 GPU 侧 scale_cuda 的默认档一致）。
    """
    sfx = f':flags={sw_algo}'
    if src_w <= 0 or src_h <= 0:
        return f'scale={dst_w}:{dst_h}{sfx}'

    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h

    if abs(src_ratio - dst_ratio) < 1e-3:
        # 比例完全一致，直接缩放
        return f'scale={dst_w}:{dst_h}{sfx}'

    if src_ratio > dst_ratio:
        # 源比目标更宽：以高度为基准缩放，左右裁剪
        return f'scale=-2:{dst_h}{sfx},crop={dst_w}:{dst_h}:(iw-{dst_w})/2:0'
    else:
        # 源比目标更高（或更窄）：以宽度为基准缩放，上下裁剪
        return f'scale={dst_w}:-2{sfx},crop={dst_w}:{dst_h}:0:(ih-{dst_h})/2'


def _build_cover_cuda_filter_str(src_w: int, src_h: int, dst_w: int, dst_h: int,
                                 download_fmt: str,
                                 cuda_algo: str = _CUDA_SCALE_ALGO,
                                 upload: bool = False) -> str:
    """
    生成 cover 模式的 CUDA 缩放链：scale_cuda → hwdownload,format=… → crop。

    upload=True 时在前面加 hwupload_cuda——那是「软件解码」这一侧用的形态
    （缩放轴与解码轴正交：硬解不可用时也能把重采样放进显存）。

    几何与 CPU 侧 _build_cover_filter_str 完全一致，只把重采样搬到显存：
      源更宽   → 先按高度缩放（宽按比例取偶），再左右居中裁剪
      源更高   → 先按宽度缩放（高按比例取偶），再上下居中裁剪
      比例相同 → 只缩放、不裁剪

    三处刻意的选择：

    · **显式写 hwdownload,format=…**，不依赖 FFmpeg 自动插入。实测（2026-09-20，
      T4）自动插入那条路下 crop 会被静默丢弃：scale_cuda=1280:720,crop=iw/2:ih/2
      实际输出 1280x720 而不是 640x360——尺寸合法、无任何报错，画面却是错的。
    · **中间尺寸在 Python 侧算成偶数**，不吃 scale_cuda 的 -2 取偶语义；
      interp_algo 显式指定（scale_cuda 的默认值是 0、未映射到具名档，
      默认取 _CUDA_SCALE_ALGO，可用 --scale-algo 覆盖）。
    · upload 用 **hwupload_cuda**（自带 device）而不是通用 hwupload：后者必须配
      `-filter_hw_device`，否则报 "A hardware device reference is required"；
      hwupload_cuda 不需要给 build_ffmpeg_cmd 加任何新参数。
    """
    prefix = 'hwupload_cuda,' if upload else ''

    def _sc(w: int, h: int) -> str:
        return f'{prefix}scale_cuda={w}:{h}:interp_algo={cuda_algo}'

    dl = f'hwdownload,format={download_fmt}'
    sw, sh = _cover_scale_dims(src_w, src_h, dst_w, dst_h)
    if src_w <= 0 or src_h <= 0 or abs(src_w / src_h - dst_w / dst_h) < 1e-3:
        # 源未知，或比例完全一致：只缩放不裁剪
        return f'{_sc(sw, sh)},{dl}'
    if src_w / src_h > dst_w / dst_h:
        return f'{_sc(sw, sh)},{dl},crop={dst_w}:{dst_h}:(iw-{dst_w})/2:0'
    return f'{_sc(sw, sh)},{dl},crop={dst_w}:{dst_h}:0:(ih-{dst_h})/2'


def _build_crop_cover_filter_str(src_w: int, src_h: int, dst_w: int, dst_h: int,
                                 crop_ratio: Optional[Tuple[int, int]] = None,
                                 sw_algo: str = _SW_SCALE_FLAGS) -> str:
    """
    生成「先裁剪、后缩放覆盖」滤镜字符串（crop-cover 模式）。

    第一段按 crop_ratio 最大化居中裁剪：裁剪尺寸由 calculate_auto_crop_size 算出，
    天然不超过源尺寸，故本模式不受 crop 模式「目标不得大于源」的限制；
    crop_ratio 为 None 时用目标宽高比，此时第二段的比例与裁剪结果一致，
    整条链退化为 `crop=... ,scale=dst_w:dst_h`（纯裁剪 + 纯缩放，不再二次裁剪）。
    比例不一致时（--crop-ratio 与目标尺寸不同）再补一次居中裁剪。

    crop-cover 需要 scale 步骤，crop_cuda 不支持，故始终使用 CPU 侧滤镜。
    """
    rn, rd = crop_ratio if crop_ratio else (dst_w, dst_h)
    crop_w, crop_h = calculate_auto_crop_size(src_w, src_h, rn, rd)
    crop = _build_crop_filter_str(src_w, src_h, crop_w, crop_h)
    cover = _build_cover_filter_str(crop_w, crop_h, dst_w, dst_h, sw_algo)
    return f'{crop},{cover}' if cover else crop


def build_video_filter(mode: str, src_w: int, src_h: int, dst_w: int, dst_h: int,
                       use_cuda: bool = False,
                       crop_ratio: Optional[Tuple[int, int]] = None,
                       cuda_scale: bool = False,
                       cuda_upload: bool = False,
                       src_bits: int = 8,
                       sw_algo: str = _SW_SCALE_FLAGS,
                       cuda_algo: str = _CUDA_SCALE_ALGO) -> str:
    """
    根据 mode 生成对应的 FFmpeg 视频滤镜字符串。

    mode='crop': 直接居中裁剪，use_cuda=True 时使用 crop_cuda（全 GPU 流水线专用）。
    mode='cover': 等比缩放+裁剪。
                  cuda_scale=True 时走「CUDA 缩放 + CPU 裁剪」（scale_cuda →
                  显式 hwdownload,format=… → crop）；此时若 cuda_upload=True 则在
                  链首加 hwupload_cuda（软件解码路径），否则要求 -hwaccel_output_format
                  cuda 由解码器直接供 CUDA 帧（零拷贝路径）。
                  两者都不给则用 CPU 侧 scale（此时忽略 use_cuda：crop_cuda 不是 scale 的替代品）。
    mode='crop-cover': 先按 crop_ratio（未给出时即目标宽高比 dst_w:dst_h）最大化
                  居中裁剪，再把裁剪结果等比缩放覆盖到 dst_w×dst_h。含两步变换，
                  不走 CUDA 缩放链（要先裁剪 → GPU 缩放需额外 hwupload_cuda，未实测）。

    Args:
        crop_ratio: (分子, 分母)，crop-cover 的裁剪步骤所用比例；None 时用目标宽高比。
        cuda_scale: 是否使用 scale_cuda 做缩放（仅 cover 模式；cover 之外传 True 报错）。
        cuda_upload: cuda_scale 的软解形态——链首加 hwupload_cuda（仅 cover 模式）。
        src_bits:   源位深，决定 hwdownload 的下载格式（8bit→nv12，10bit→p010le）。
    """
    if cuda_upload and not cuda_scale:
        raise ValueError('cuda_upload=True 必须同时 cuda_scale=True（上传是为了给 scale_cuda 用）')
    if cuda_scale:
        if mode != 'cover':
            raise ValueError('CUDA 缩放链（scale_cuda）目前只支持 cover 模式')
        return _build_cover_cuda_filter_str(src_w, src_h, dst_w, dst_h,
                                            _src_download_fmt(src_bits), cuda_algo,
                                            upload=cuda_upload)
    if mode == 'cover':
        return _build_cover_filter_str(src_w, src_h, dst_w, dst_h, sw_algo)
    if mode == 'crop-cover':
        return _build_crop_cover_filter_str(src_w, src_h, dst_w, dst_h, crop_ratio, sw_algo)
    return _build_crop_filter_str(src_w, src_h, dst_w, dst_h, use_cuda=use_cuda)


def parse_crop_ratio(ratio_str: str) -> Tuple[int, int]:
    """
    解析宽高比字符串，支持 '16:9'、'4:3' 或浮点数 '1.777' 格式。
    返回 (numerator, denominator) 元组。
    """
    ratio_str = ratio_str.strip()
    if ':' in ratio_str:
        try:
            num_str, den_str = ratio_str.split(':', 1)
            num = int(num_str)
            den = int(den_str)
            if num <= 0 or den <= 0:
                raise ValueError
            return num, den
        except Exception:
            raise ValueError(f"无效的宽高比格式: '{ratio_str}'，应为 '16:9' 或 '4:3' 格式")
    else:
        try:
            ratio_val = float(ratio_str)
            if ratio_val <= 0:
                raise ValueError
            # 将浮点数转为近似分数（分母限制在 1000 以内）
            from fractions import Fraction
            frac = Fraction(ratio_val).limit_denominator(1000)
            return frac.numerator, frac.denominator
        except Exception:
            raise ValueError(f"无效的宽高比格式: '{ratio_str}'，应为 '16:9' 或浮点数如 '1.777'")


def calculate_auto_crop_size(src_w: int, src_h: int, target_num: int, target_den: int) -> Tuple[int, int]:
    """
    根据源尺寸和目标宽高比，计算最大化裁剪后的输出尺寸（保持原始分辨率，仅裁剪）。
    
    逻辑：
    - 目标比例 = target_num / target_den
    - 源比例 = src_w / src_h
    - 如果源比例 > 目标比例（视频更宽）：裁剪左右，保持高度不变
      out_w = src_h * target_num / target_den, out_h = src_h
    - 如果源比例 < 目标比例（视频更高）：裁剪上下，保持宽度不变
      out_w = src_w, out_h = src_w * target_den / target_num
    - 结果取整为偶数（编码器要求）
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError("源宽高必须为正整数")
    
    src_ratio = src_w / src_h
    target_ratio = target_num / target_den
    
    if abs(src_ratio - target_ratio) < 1e-6:
        # 比例完全一致，无需裁剪
        out_w, out_h = src_w, src_h
    elif src_ratio > target_ratio:
        # 源视频更宽：裁剪左右两侧，保持高度
        out_w = int(round(src_h * target_num / target_den))
        out_h = src_h
    else:
        # 源视频更高：裁剪上下两侧，保持宽度
        out_w = src_w
        out_h = int(round(src_w * target_den / target_num))
    
    # 确保偶数尺寸（大多数编码器要求宽高为偶数）
    out_w = out_w if out_w % 2 == 0 else out_w + 1
    out_h = out_h if out_h % 2 == 0 else out_h + 1
    
    # 安全检查：不得超过源尺寸
    out_w = min(out_w, src_w)
    out_h = min(out_h, src_h)
    
    return out_w, out_h


def derive_even_dimension(value: float) -> int:
    """
    把按比例推导出的边长取整为不小于 2 的偶数。

    4:2:0 系编码器（含 yuv420p / yuv420p10le / p010le）要求宽高均为偶数，
    故推导出的维度不能是任意整数。取整到最近的偶数，向上补 1 而不是向下
    （宁可多 1 像素，也不要因为向下取整让画面被裁掉一条）。
    """
    n = max(2, int(round(value)))
    return n if n % 2 == 0 else n + 1


# ═══════════════════════════════════════════════════════════════════
#  质量参数解析
# ═══════════════════════════════════════════════════════════════════

def cq_to_crf(cq: int, target_codec: str, src_codec: str = 'h264_nvenc') -> int:
    """
    硬件编码器的 CQ 值 → 目标软件编码器的等效 CRF 值。

    换算基准统一走同目录 convert_crf.py 的 QUALITY_MAP（以 libx264 CRF 为轴）：
        src_codec(cq) ──to_x264_crf──▶ x264 CRF ──from_x264_crf──▶ target_codec(crf)
    旧的硬编码偏移（libx264 +1 / libx265 +4 / AV1 +6）与本表方向相反，已废弃。

    Args:
        cq: 用户给的 --cq 值（按 src_codec 的量纲解释）
        target_codec: 实际要用的软件编码器
        src_codec: 用户原本请求的硬件编码器，默认 h264_nvenc

    Returns:
        等效 CRF；任一端无映射时原样返回 cq（未知编码器不做猜测）。
    """
    v = convert_quality(src_codec, cq, target_codec)
    return int(round(v)) if v is not None else int(cq)


def crf_to_rav1e_qp(crf: int) -> int:
    """
    librav1e 没有 -crf（实测传 -crf 只会被 ffmpeg 静默忽略并告警，质量退回默认值），
    只有 0~255 的 -qp。按实测标定换算：
      qp = (crf - 5) × 4
    标定数据（ffmpeg 6.1，640x480 testsrc2 2s，输出体积互差 < 5%）：
      libaom crf 20/25/30/35  ←→  rav1e qp 60/80/100/120
    """
    return max(0, min(255, (int(crf) - 5) * 4))


def _resolve_quality_params(
    codec: str,
    user_crf: Optional[int],
    user_cq: Optional[int],
    src_codec: Optional[str] = None,
    crf_ref: Optional[int] = None,
    cq_ref: Optional[int] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    根据编码器类型确定最终 (crf, cq) 值，处理参数不匹配和降级映射。

    两种取值方式（由调用方保证互斥，混用会被拒绝执行）：
      1. --crf / --cq：字面量原样下发——GPU 编码器用 --cq，CPU 编码器用 --crf，
         都不换算；只有"请求 GPU 却降级到 CPU 软编"时才按等效表换算。
      2. --crf-ref / --cq-ref：统一基准轴——先归一到 libx264 CRF，
         再换算到目标编码器（见 convert_crf.py 的 QUALITY_MAP）。

    Args:
        codec: 实际要用的编码器
        user_crf / user_cq: 用户原始参数（None 表示未指定）
        src_codec: 用户原本请求的编码器，用于判断 --cq 的量纲
        crf_ref: --crf-ref，以 libx264 CRF 为基准
        cq_ref: --cq-ref，以 h264_nvenc CQ 为基准
    """
    # ── 方式 2：统一基准轴换算 ────────────────────────────────────────────
    if crf_ref is not None or cq_ref is not None:
        if crf_ref is not None:
            ref_x264: Optional[float] = float(crf_ref)
            ref_desc = f'--crf-ref {crf_ref}（libx264 CRF 基准）'
        else:
            ref_x264 = to_x264_crf('h264_nvenc', cq_ref)
            ref_desc = f'--cq-ref {cq_ref}（h264_nvenc CQ 基准）'
            if ref_x264 is None:
                ref_x264 = float(cq_ref or 0)
        if codec == 'librav1e':
            # librav1e 没有 -crf；其实测标定以 AV1(libaom) CRF 为输入，
            # 故先落到 AV1 CRF 轴，再由命令构建处套 crf_to_rav1e_qp()。
            _v = from_x264_crf('libaom-av1', ref_x264)
            _out = int(round(_v)) if _v is not None else None
            if _out is not None:
                print(f'  提示：{ref_desc} → {codec} 的 -qp {crf_to_rav1e_qp(_out)}。')
            return _out, None
        _v2 = from_x264_crf(codec, ref_x264)
        if _v2 is None:
            print(f'  警告：{codec} 不在等效换算表中，{ref_desc} 无法换算，改用默认质量。')
            return (None, DEFAULT_CQ) if encoder_supports_cq(codec) \
                else (DEFAULT_CRF, None)
        _val = int(round(_v2))
        print(f'  提示：{ref_desc} → {codec} 的 '
              f'{"-cq" if encoder_supports_cq(codec) else "-crf"} {_val}。')
        if encoder_supports_cq(codec):
            return None, _val
        return _val, None

    # ── 方式 1：字面量原样下发 ────────────────────────────────────────────
    if encoder_supports_cq(codec):
        if user_cq is not None:
            return None, user_cq
        if user_crf is not None:
            print(f'  提示：编码器 {codec} 不支持 -crf，使用默认 -cq {DEFAULT_CQ}（可通过 --cq 指定）。')
        return None, DEFAULT_CQ

    if encoder_supports_crf(codec):
        if user_crf is not None:
            return user_crf, None
        if user_cq is not None:
            # --cq 的量纲取决于用户原本请求的编码器；请求的就是 CPU 编码器时
            # （没有可参照的 GPU 编码器）按默认 GPU 编码器 h264_nvenc 解释。
            _src = src_codec if (src_codec and encoder_supports_cq(src_codec)) \
                else 'h264_nvenc'
            mapped_crf = cq_to_crf(user_cq, codec, _src)
            print(f'  提示：编码器 {codec} 不支持 -cq，'
                  f'已将 --cq {user_cq}（{_src} 量纲）映射为 -crf {mapped_crf}（等效视觉质量）。')
            return mapped_crf, None
        return DEFAULT_CRF, None

    return None, None


def apply_rc_control_args(codec: str,
                          rc_mode: str = 'auto',
                          qp: Optional[int] = None,
                          lookahead: Optional[int] = None,
                          policy: str = 'auto',
                          warn: Optional[Callable[[str], None]] = None,
                          ) -> Tuple[List[str], List[str]]:
    """
    把 --rc-mode / --qp / --lookahead 落到 ffmpeg 参数上。

    Returns:
        (args, x265_params)
        · args        直接追加进命令的选项（-rc / -qp / -rc-lookahead / -lag-in-frames）；
        · x265_params **必须由调用方合并进同一条 -x265-params**（libx265 的 lookahead
          只能这样传）。为什么不在这里直接发一条：实测两次 -x265-params 是"后者整条
          覆盖前者"，而 HDR 静态元数据也走 -x265-params —— 各发一条会让后发的那条
          **静默抹掉 HDR 元数据**，故统一交给 build_hdr_args() 合并。

    取值与生效范围（"能力不存在就明确告知"，与 --pix-fmt 在链上落不了地时同一套）：

      · `-rc` / `-qp`：只有 NVENC 认。libx264 / libx265 没有"码率控制模式"这个开关
        （它们用 -crf / -b:v / -qp 的组合表达），故非 NVENC 编码器下忽略并告知。
      · lookahead：libx264 → `-rc-lookahead`；NVENC → `-rc-lookahead`
        （**但 `rc_mode == 'constqp'` 时不下发**：该模式下硬件会静默禁用 lookahead，
        与 Video_Enhancement 的 ffmpeg_io.py 一致，见下）；
        libx265 → 写进 `-x265-params rc-lookahead=`（无顶层选项）；
        libvpx / libvpx-vp9 / libaom-av1 → `-lag-in-frames`（vp9 另有 0~25 的
        `-rc_lookahead`，但 `-lag-in-frames` 是两者通用且无上限的那个，故用它）；
        其余（libsvtav1 / prores …）键名未实测 → 不下发，明确告知（不瞎发一个
        可能不存在的键）。

    为什么 constqp 下的 lookahead 要拦下来：NVENC 在 constqp 模式会**静默忽略**
    lookahead（对照 Video_Enhancement 的 ffmpeg_io.py——那条路径同样不发
    `-rc-lookahead`，并在 SDK 侧把 la_depth 显式清零）。下发一条不生效的选项会让
    用户以为设了却没生效，故按「能力不存在就明确告知」处理。
    """
    c = (codec or '').lower()
    args: List[str] = []
    x265_params: List[str] = []

    def _ignore(what: str, why: str) -> None:
        msg = f'{what} 未生效（{why}），已忽略'
        if policy == 'strict':
            raise ValueError(msg + '（--fallback-policy strict 不降级）')
        if warn:
            warn(msg)

    _want_rc = rc_mode != 'auto' or qp is not None
    if _want_rc:
        if c in NVENC_CODECS:
            if rc_mode != 'auto':
                args += ['-rc', rc_mode]
            if qp is not None:
                args += ['-qp', str(qp)]
        else:
            _asked = ' 与 '.join(
                n for n, on in (('--rc-mode', rc_mode != 'auto'), ('--qp', qp is not None)) if on)
            _ignore(_asked, f'-rc / -qp 是 NVENC 专属选项，编码器 {c} 没有这个开关')

    if lookahead is not None:
        if c in NVENC_CODECS:
            if rc_mode == 'constqp':
                # constqp 下硬件静默禁用 lookahead（见函数 docstring）。
                _ignore(f'--lookahead {lookahead}',
                        '-rc constqp 下 NVENC 静默禁用 lookahead，未下发')
            else:
                args += ['-rc-lookahead', str(lookahead)]
        elif c == 'libx264':
            args += ['-rc-lookahead', str(lookahead)]
        elif c == 'libx265':
            x265_params.append(f'rc-lookahead={lookahead}')
        elif c in ('libvpx', 'libvpx-vp9', 'libaom-av1'):
            args += ['-lag-in-frames', str(lookahead)]
        else:
            _ignore(f'--lookahead {lookahead}',
                    f'编码器 {c} 的 lookahead 选项名未经实测，不代为下发')

    return args, x265_params


# ═══════════════════════════════════════════════════════════════════
#  策略生成
# ═══════════════════════════════════════════════════════════════════

def _get_software_fallback(codec: str) -> str:
    if codec in ('hevc_nvenc', 'hevc_amf', 'hevc_qsv', 'libx265'):
        return 'libx265'
    if codec in ('av1_nvenc', 'av1_qsv', 'av1_amf'):
        # libsvtav1 与 libaom-av1 同为 AV1；选前者是因为它快一个数量级，
        # 而降级路径本就慢，没必要再雪上加霜。
        return 'libsvtav1'
    return 'libx264'


def _select_best_hwaccel(hw_caps: HardwareCapabilities,
                         src_codec: str = '') -> Optional[str]:
    """按 CUDA > Vulkan > VA-API > OpenCL 优先级选择最佳硬件加速器。

    src_codec 是**源流的编解码器名**（h264 / hevc / av1 …）：CUDA 那一档走
    `cuda_decodes()`，因为 NVDEC 的解码能力分编解码器（T4 解不了 AV1）。
    其余后端暂不加同类判据——本项目没有它们存在编解码器级差异的实测证据，
    不凭猜测加约束。
    """
    if hw_caps.cuda_decodes(src_codec):
        return 'cuda'
    if hw_caps.has_vulkan:
        return 'vulkan'
    if hw_caps.has_vaapi:
        return 'vaapi'
    if hw_caps.has_opencl:
        return 'opencl'
    return None


# 用了这些解码后端时，解码器产出的仍是**软件帧**（vulkan/vaapi/opencl 都是先下载回
# 系统内存，opencl 那点 nv12 也是软件帧）→ 可以再接 hwupload_cuda 上载到显存。
_DECODE_SW_FRAME_BACKENDS = ('vulkan', 'vaapi', 'opencl')


def _decode_hwaccel(decode: str, hw_caps: HardwareCapabilities,
                    src_codec: str = '') -> Optional[str]:
    """解码轴 → `-hwaccel` 的实际取值（None = 软解、不下发）。

    **`auto` 与 `--scale-algo auto` 是同一套逻辑：先探测、再定，探测不到就降级 cpu。**
    也就是不再把 `-hwaccel auto` 丢给 ffmpeg 让它自己试——那样"实际用了什么"我们并不
    知道，概览块也报不出确定答案。现在：探测到可用的硬解就给**那个具体后端**，
    一个都没有就明确走软解（不下发 `-hwaccel`），与 `--scale-algo auto` 的
    「优先 cuda、失败回退 cpu」对称。

    src_codec 透传给 `_select_best_hwaccel()`：`--decode auto` 的探测必须按**源编解码器**
    做，否则"机器能硬解"会被误当成"这个源能硬解"（T4 上 AV1 就是这么被误判的）。

    顺序沿用既有的 `_select_best_hwaccel()`：CUDA > Vulkan > VA-API > OpenCL。
    `auto` 不是显式请求，所以探测不到时只是降级，不算"够不到"（不触发 strict 报错）。
    """
    if decode == 'auto':
        return _select_best_hwaccel(hw_caps, src_codec)
    if decode == 'cpu':
        return None
    if decode == 'cuda':
        # 显式点 cuda 时同样要按源 codec 判：解不了就该返回 None，让上层按
        # --fallback-policy 走"显式后端够不到"的既有分支（auto 降级 / strict 报错）。
        return decode if hw_caps.cuda_decodes(src_codec) else None
    return decode if hw_caps.has_hwaccel(decode) else None


def _decode_output_format(hw: Optional[str]) -> Optional[str]:
    """`-hwaccel_output_format`：opencl 需要 nv12，其余留空（与既有策略 3/4 一致）。"""
    return 'nv12' if hw == 'opencl' else None


def _combo_hint(hwaccel: Optional[str], cuda_scale: bool, hwupload: bool,
                codec: str) -> str:
    """按（实际生效的解码后端, 缩放形态, 编码器）给一句「这个组合大概会怎样」。

    三轴可自由组合，所以有些组合能跑但没有收益甚至更慢。脚本只负责把话说明白，
    实际效果由用户判断（不阻断执行）。返回空串表示这个组合没什么特别要提醒的。
    hwaccel 传**策略实际会用的**值（可能是降级后的），不是用户请求的轴值——
    否则概览说的和真正跑的对不上。
    """
    if cuda_scale and hwupload:
        return ('软解 + hwupload_cuda + 显存内缩放：要多一次整帧上载，'
                '可能不如「软解 + CPU 缩放」；只在 NVDEC 用不了/解不了该编码时更优')
    if cuda_scale:
        if codec in NVENC_CODECS:
            return ('硬解 + 显存内缩放 + 硬编：全 GPU 零拷贝，最快路径'
                    '（4K→1440x1080 实测快 51.9%）')
        return '硬解 + 显存内缩放，但编码在 CPU：链尾仍要下载一次，收益有限'
    if hwaccel in ('cuda', 'vulkan', 'vaapi', 'opencl'):
        return '硬解 + CPU 缩放：CPU 段在关键路径上，并发 CPU 作业会直接把它拖慢'
    if codec not in NVENC_CODECS:
        return '纯 CPU：没有 GPU 参与'
    if hwaccel is None:
        return '软解 + GPU 硬编：解码开销在 CPU，编码在 GPU'
    return ''


def _hwupload_skip_reason(src_bits: int, scale_identity: bool) -> Optional[str]:
    """`--scale-algo auto` 在软解时**不**回退到 hwupload 链的理由（None = 该回退）。

    依据 T4 实测（两批共 12 组素材，见 memory/project_cuda_scale_cover.md 判据 D）：

      · 源 ≥10bit **且真在缩放** → 上传链快 14~25%
        （4K 10bit +16.5% / +19.3%；720p 10bit +25.0%）
      · 8bit + 真在缩放          → 打平或更慢
        （4K 8bit +0.3% / +0.4%；720p 8bit −9.0% / −14.3%；SD −0.3% ~ −1.6%）
      · 恒等缩放（**无论位深**） → 一定亏
        （−1.6% ~ −34.7%；注意 10bit 恒等也是 −22.0%）

    机理：上载 / 回下载的开销基本固定，而 p010le 的 CPU 缩放比 8bit 贵得多；
    恒等缩放时 CPU 侧本来就没有重采样成本可省 —— 所以只有"高位深 + 真的省下缩放"
    两项同时成立才划算。

    只用来管 **auto**：显式 `--scale-algo cuda-*` 是用户点名要的，照旧直接执行
    （执行效果用户自负），与既有的"显式不跑功能探针"约定一致。
    """
    if scale_identity:
        return ('这次是恒等缩放（源尺寸 == 缩放后尺寸），显存内缩放没有可省的重采样，'
                '只剩上载开销 —— 实测恒等时反而慢 1.6%~34.7%')
    if src_bits < 10:
        return (f'源是 {src_bits}bit，上传链的收益要到 10bit+ 才显现'
                f'（8bit 实测打平或更慢，最差 −14%）')
    return None


def _generate_strategies(
    user_codec: str,
    hw_caps: HardwareCapabilities,
    decode: str = 'auto',
    mode: str = 'crop',
    scale_backend: str = 'auto',
    policy: str = 'auto',
    src_codec: str = '',
    src_bits: int = 8,
    scale_identity: bool = False,
) -> List[Dict]:
    """
    根据**三个正交轴**（解码 --decode / 缩放 --scale-algo / 编码 --codec）与处理模式
    生成策略列表（优先级从高到低）。

    轴之间没有冲突检查：每个轴各自决定「要用哪个后端」，本函数只把它们拼成链。
    policy='strict' 时**不追加降级策略**（只留首选策略），执行失败由上层直接报错退出；
    policy='auto' 时逐级降级。

    src_codec 是**源流的编解码器名**（h264 / hevc / av1 …），只用于回答一个问题：
    「本机的 CUDA 硬解能不能解这个源」。它不参与"三轴"语义——硬件解码是否可用
    本来就是**具体到编解码器**的事实（T4 能解 H.264 但解不了 AV1），不传时按
    `has_decoder` 乐观处理，行为与改动前逐字相同。

    src_bits / scale_identity 只用于 `--scale-algo auto` 的"值不值得"门槛：
    软解时是否回退到 hwupload 链由 `_hwupload_skip_reason()` 判（10bit+ 且真在
    缩放才划算，见那里的实测依据）。默认值让不传的调用方行为不变。

    每个策略字段：
        name                  描述名称
        hwaccel               None / 'auto' / 'cuda' 等
        hwaccel_output_format None / 'cuda' / 'nv12'
        use_hw_filter         是否使用 crop_cuda 滤镜（仅 crop 模式 + 全 GPU 流水线）
        cuda_scale            是否使用 scale_cuda 做缩放（仅 cover 模式的 CUDA 缩放链）
        hwupload              cuda_scale 的软解形态：链首加 hwupload_cuda
        codec                 实际编码器名称
        fallback              是否为降级策略
    """
    decode = 'cpu' if decode in (None, 'none') else decode
    strategies = []

    # ── 确定首选编码器 ──
    if user_codec == 'auto':
        preferred_codec = 'h264_nvenc' if hw_caps.has_encoder_h264 else 'libx264'
    else:
        preferred_codec = user_codec

    is_nvenc       = preferred_codec in NVENC_CODECS
    nvenc_available = hw_caps.has_nvenc(preferred_codec) if is_nvenc else False
    sw_fallback    = _get_software_fallback(preferred_codec)

    # 用户显式指定非 NVENC 的具体编码器，软件策略沿用该编码器
    sw_codec = user_codec if (not is_nvenc and user_codec != 'auto') else sw_fallback

    decode_hw = _decode_hwaccel(decode, hw_caps, src_codec)

    # ── 策略 1：全 GPU 流水线（硬解 + crop_cuda + NVENC 编码）──
    # cover / crop-cover 模式需要 scale 步骤，crop_cuda 不支持，
    # 此策略仅在 crop 模式下可用。（crop_cuda 上游不存在，实际永不命中。）
    if (decode != 'cpu'
            and mode == 'crop'
            and is_nvenc
            and hw_caps.cuda_decodes(src_codec)
            and hw_caps.can_full_pipeline(preferred_codec)):
        strategies.append({
            'name':                  'CUDA 全加速（硬解 + crop_cuda + NVENC 编码）',
            'hwaccel':               'cuda',
            'hwaccel_output_format': 'cuda',
            'use_hw_filter':         True,
            'codec':                 preferred_codec,
            'fallback':              False,
        })

    # ── 策略 1b / 1c：CUDA 缩放 + CPU 裁剪（仅 cover 模式）──
    # 为什么不是全 GPU：crop_cuda 在 FFmpeg 上游不存在（不是编译选项问题），
    # 所以 crop 只能回 CPU，链路必然是 [hwupload_cuda →] scale_cuda → 显式
    # hwdownload → crop —— 下载次数与现状相同（1 次），省掉的是 CPU 侧的重采样。
    #
    # 实测依据（2026-09-20，T4 + 自建 7.1，4K→1440x1080 覆盖链，-f null 去 IO）：
    #   现行「硬解+CPU scale(lanczos)+crop+NVENC」26.65s → 零拷贝形态 12.82s，快 51.9%
    #   （对着旧 bicubic 基准 24.35s 则是 44.5%）；质量 PSNR(GPU vs CPU lanczos)=46.60dB。
    #   同轮判据：scale_cuda 之后若依赖 FFmpeg【自动插入】的 hwdownload，
    #   crop 会被静默丢弃（实测 scale_cuda=1280:720,crop=iw/2:ih/2 输出 1280x720
    #   而不是 640x360）→ 所以链里必须显式写 hwdownload,format=...
    #
    # 两种形态二选一：
    #   零拷贝（hwaccel='cuda' + hof='cuda'）——解码器直接供 CUDA 帧，要求有硬解
    #   hwupload（链首 hwupload_cuda，hwaccel=None）——软件帧上载，**不要求硬解**
    #     （缩放轴与解码轴正交：NVDEC 用不了或解不了该编码时照样能用 GPU 缩放）
    #
    # 何时插 hwupload 形态：显式 --scale-algo cuda-*，或者显式要了软解
    # （--decode cpu，用户已确认「auto 缩放优先 cuda，与 --decode cpu 不矛盾」）
    # 且零拷贝不可用。**纯默认（--decode auto + --scale-algo auto）不插**：
    # 那条链的吞吐尚未实测（判据 D 未跑），不该在用户什么都没点时自动启用。
    #
    # crop-cover 不纳入：它必须先裁剪，GPU 缩放要额外 hwupload_cuda 一次，未实测。
    if mode == 'cover' and scale_backend != 'libswscale':
        _want_cuda = (scale_backend == 'cuda'
                      or (scale_backend == 'auto' and decode == 'cpu'))
        # auto 缩放保持既有判据（含 NVENC 门，字节级不变）；显式 cuda-* 时放宽到
        # 「任何编码器」——链尾本来就是软件帧，硬编/软编都接得住。
        _zc_direct = (hw_caps.cuda_decodes(src_codec) and hw_caps.has_cuda_scale
                      and (is_nvenc if scale_backend == 'auto' else True))
        _zc = decode != 'cpu' and _zc_direct
        # auto 缩放要求功能探针通过（免得自动选到一条必然失败的链）；
        # 显式 cuda-* 只要求滤镜存在——直接执行，失败由 --fallback-policy 处理。
        _up_ok = (hw_caps.can_cuda_scale_upload() if scale_backend == 'auto'
                  else hw_caps.has_cuda_scale)
        # auto 还要过"值不值得"这一关：实测 8bit 打平或更慢、恒等缩放必亏，
        # 只有「≥10bit 且真在缩放」才划算（见 _hwupload_skip_reason）。
        # 显式 cuda-* 不看这一条——用户点名要的，直接执行。
        _up_skip = (_hwupload_skip_reason(src_bits, scale_identity)
                    if scale_backend == 'auto' else None)
        _up = _want_cuda and not _zc and _up_ok and _up_skip is None
        if _zc:
            strategies.append({
                'name':                  'CUDA 缩放 + CPU 裁剪（显存内缩放）',
                'hwaccel':               'cuda',
                'hwaccel_output_format': 'cuda',
                'use_hw_filter':         False,
                'cuda_scale':            True,
                'codec':                 preferred_codec,
                'fallback':              False,
            })
        elif _up:
            # hwupload 形态必须不带 -hwaccel_output_format cuda：帧是软件帧。
            # 解码轴若显式点了会产生软件帧的后端（vulkan/vaapi/opencl）就沿用，
            # 否则（cpu / auto 但拿不到硬解）走纯软解。
            _up_hw = (decode if (decode in _DECODE_SW_FRAME_BACKENDS
                                 and hw_caps.has_hwaccel(decode)) else None)
            strategies.append({
                'name':                  'CUDA 缩放 + CPU 裁剪（软件解码 + hwupload）',
                'hwaccel':               _up_hw,
                'hwaccel_output_format': None,
                'use_hw_filter':         False,
                'cuda_scale':            True,
                'hwupload':              True,
                'codec':                 preferred_codec,
                'fallback':              False,
            })

    # ── 策略 2：解码轴后端 + NVENC 编码（CPU 侧 vf 滤镜）──
    # 不再硬写 'auto'：auto 由 _decode_hwaccel() 按探测结果解析成具体后端（或软解），
    # 显式 --decode cuda 也就真拿到 -hwaccel cuda。
    if is_nvenc and nvenc_available:
        if decode == 'auto':
            _n2 = '自动硬件解码 + GPU 编码'          # 默认路径的字面量，保持不变
        elif decode_hw is None:
            _n2 = '软件解码 + GPU 编码'
        else:
            _n2 = f'{decode_hw} 硬件解码 + GPU 编码'
        strategies.append({
            'name':                  _n2,
            'hwaccel':               decode_hw,
            'hwaccel_output_format': _decode_output_format(decode_hw),
            'use_hw_filter':         False,
            'codec':                 preferred_codec,
            'fallback':              False,
        })

    # ── 策略 3：指定硬件加速解码 + 软件编码 ──
    specific_hwaccels = ('cuda', 'vulkan', 'vaapi', 'opencl')
    _hw3_ok = (hw_caps.cuda_decodes(src_codec) if decode == 'cuda'
               else hw_caps.has_hwaccel(decode))
    if decode in specific_hwaccels and _hw3_ok:
        strategies.append({
            'name':                  f'{decode} 硬件解码 + CPU 编码',
            'hwaccel':               decode,
            'hwaccel_output_format': _decode_output_format(decode),
            'use_hw_filter':         False,
            'codec':                 sw_codec,
            'fallback':              False,
        })

    # ── 策略 4：auto 模式下选最佳硬解 + 软件编码 ──
    if decode == 'auto':
        best_hw = _select_best_hwaccel(hw_caps, src_codec)
        if best_hw is not None:
            strategies.append({
                'name':                  f'{best_hw} 硬件解码 + CPU 编码',
                'hwaccel':               best_hw,
                'hwaccel_output_format': _decode_output_format(best_hw),
                'use_hw_filter':         False,
                'codec':                 sw_codec,
                'fallback':              is_nvenc,
            })

    # ── 策略 5：纯 CPU 处理（兜底）──
    strategies.append({
        'name':                  '纯 CPU 处理',
        'hwaccel':               None,
        'hwaccel_output_format': None,
        'use_hw_filter':         False,
        'codec':                 sw_codec,
        'fallback':              True,
    })

    # --fallback-policy strict：不降级，只跑首选策略；失败由上层报错退出。
    if policy == 'strict':
        strategies = strategies[:1]

    return strategies


# ═══════════════════════════════════════════════════════════════════
#  FFmpeg 命令构建
# ═══════════════════════════════════════════════════════════════════

# 可直接消费 CUDA 帧的滤镜（无需回传系统内存）。
# ⚠ 只列**确实存在**的滤镜：这里曾经写过 `crop_cuda` 与 `tonemap_cuda`，但两者在
# FFmpeg 上游都不存在（实测 `ffmpeg -h filter=<name>` 均返回 Unknown filter），
# 与编译选项无关。留着它们会让"链首是 CUDA 原生滤镜"这个判断基于假名字，
# 进而以为某条链能跑（crop_cuda 那条策略因此从未命中过，见 can_full_pipeline）。
# `crop_cuda` 暂时保留并标注：策略 1 依赖它做分类，删掉需要一并清理那条死策略。
_CUDA_NATIVE_FILTERS = frozenset({
    'crop_cuda',  # 上游不存在，仅为策略 1（死代码）的分类保留
    'scale_cuda', 'yadif_cuda', 'overlay_cuda', 'thumbnail_cuda',
    'hwupload_cuda', 'hwdownload', 'hwupload',
})

# 输出**显存帧**的滤镜：链尾是它们时帧还在 GPU 上，CPU 滤镜（如 setparams）接不住。
# 与 _CUDA_NATIVE_FILTERS 的唯一差别是 hwdownload——它输出的是软件帧，所以不在内。
_HW_OUTPUT_FILTERS = _CUDA_NATIVE_FILTERS - {'hwdownload'}


def _prepend_hwdownload(vf_filter: str,
                        hwaccel_output_format: Optional[str],
                        src_bits: int = 8,
                        download_fmt: Optional[str] = None) -> str:
    """`-hwaccel_output_format cuda` 遇到 CPU 侧滤镜时，链首插 hwdownload,format=...。

    问题：hof=cuda 让解码器输出 CUDA 帧；若滤镜链以 CPU 滤镜（如 crop）开头，
    FFmpeg 6.1 自动插入的 hwdownload 其输出 link 尺寸会回退到解码器
    hw_frames_ctx 的原始尺寸，导致 crop 静默失效（实测 768x576 源 +
    crop=768:432:0:72 仍输出 768x576，稳定复现）。

    只插 hwdownload 也不够：它按链尾协商出的格式输出，与 CUDA 帧的
    sw_format(nv12) 不匹配时会报 "Invalid output format yuv420p for hwframe
    download"，故必须紧跟 format=... 固定下载格式。

    src_bits >= 10 时必须下载为 p010/p012 而非 nv12：nv12 只有 8bit，
    会把 10bit 源强制降为 8bit，并连带丢失 HDR 的帧级 side_data。
    """
    if hwaccel_output_format != 'cuda' or not vf_filter:
        return vf_filter
    first = vf_filter.split(',', 1)[0].split('=', 1)[0].strip()
    if first in _CUDA_NATIVE_FILTERS:
        return vf_filter
    if download_fmt:
        # 调用方强制 8bit（p010 下载不被支持时的回退）。
        # 10bit 源不能写成 hwdownload,format=nv12 —— 实测（T4/驱动 580）直接报
        # "Invalid output format nv12 for hwframe download"；必须先按源位深下载，
        # 再接一个 format=nv12 做降位深转换。
        if src_bits <= 8:
            return f'hwdownload,format={download_fmt},{vf_filter}'
        return (f'hwdownload,format={_src_download_fmt(src_bits)},'
                f'format={download_fmt},{vf_filter}')
    return f'hwdownload,format={_src_download_fmt(src_bits)},{vf_filter}'


def _src_download_fmt(src_bits: int) -> str:
    """按源位深选择 hwdownload 的下载格式（10bit→p010le，12bit+→p012le）。

    ⚠ 必须是 **p010le / p012le**，不能写 p010 / p012：ffmpeg 的 pix_fmt 表里
    只有带字节序后缀的名字（实测 `ffmpeg -pix_fmts` 里没有 p010、p012），
    写成 p010 会在 hwdownload 协商时报未知像素格式。
    此前这个函数一直返回 p010/p012——因为唯一会用它的路径（策略 1 的
    crop_cuda 全 GPU 流水线）在本项目所有环境下都不可达，所以没暴露；
    接入 cover 的 CUDA 缩放链后才真正被调用。
    """
    return 'nv12' if src_bits <= 8 else ('p012le' if src_bits >= 12 else 'p010le')


def build_ffmpeg_cmd(
    input_file: Path,
    output_file: Path,
    vf_filter: str,
    codec: str,
    crf: Optional[int],
    cq: Optional[int],
    preset: str,
    overwrite: bool,
    hwaccel: Optional[str] = None,
    hwaccel_output_format: Optional[str] = None,
    ffmpeg_bin: str = 'ffmpeg',
    audio_codec: str = 'copy',
    audio_bitrate: str = '128k',
    extra_args: Optional[List[str]] = None,
    hw_download_fmt: Optional[str] = None,
    color_range: Optional[str] = None,
    pix_fmt: Optional[str] = 'auto',
    bit_depth: Optional[int] = None,
    hdr: str = 'auto',
    policy: str = 'auto',
    rc_mode: str = 'auto',
    qp: Optional[int] = None,
    lookahead: Optional[int] = None,
    bitrate: Optional[str] = None,
    nvenc_aq: bool = False,
) -> List[str]:
    """
    构建完整的 FFmpeg 命令列表。

    audio_codec:   音频编码器，'copy' 表示流复制；其他值触发重编码。
    audio_bitrate: 仅在音频重编码时生效，默认 '128k'。
    extra_args: 追加到输出文件名之前的自定义 FFmpeg 参数（已剥离 '--' 前缀）。
    rc_mode / qp / lookahead / bitrate: 码率控制轴（默认值全部＝不下发任何相关选项，
                   即与引入这四个参数之前逐字相同）。
    nvenc_aq:      True 时给 NVENC 编码器加 -spatial-aq 1 -temporal-aq 1
                   （对照 Video_Enhancement SDK 的 enableAQ/enableTemporalAQ）。
                   非 NVENC 编码器忽略并告警；默认 False（不改变既有输出）。
    """
    extra_args = extra_args or []
    if codec.lower() == 'copy' and vf_filter:
        # 视频滤镜与流复制互斥：与其让 ffmpeg 报难以定位的错误，不如在此明确失败
        raise ValueError(
            '使用视频滤镜时不能使用 -c:v copy；若仅需保留元数据请直接用 ffmpeg remux'
        )

    def _warn(msg: str) -> None:
        print(f'  ⚠ {msg}', file=sys.stderr)

    _hdr_mode, _hdr_algo = parse_hdr_spec(hdr)

    # [META-KEEP] 元数据探测（带缓存，全文件只探一次）
    meta = probe_full_metadata(input_file, ffmpeg_bin)
    src_bits = meta['derived']['src_bits'] if meta else 8

    pres = build_preserve_args(meta, output_file.suffix, codec, ffmpeg_bin, warn=_warn)
    if not pres['map']:
        pres['map'] = ['-map', '0:v:0', '-map', '0:a?']

    cmd = [ffmpeg_bin, '-hide_banner', '-loglevel', 'warning']
    cmd += pres['input']                 # -noautorotate / -display_rotation（须在 -i 前）
    cmd += ['-err_detect', 'ignore_err']
    cmd += ['-fflags', '+genpts+discardcorrupt']
    cmd += ['-nostdin']

    # 硬件加速
    if hwaccel:
        cmd += ['-hwaccel', hwaccel]
        if hwaccel in ('cuda', 'opencl'):
            cmd += ['-hwaccel_device', '0']
        if hwaccel_output_format:
            cmd += ['-hwaccel_output_format', hwaccel_output_format]

    cmd += ['-y' if overwrite else '-n']
    cmd += ['-i', str(input_file)]
    cmd += pres['map']                   # 流映射 / -map_metadata / -map_chapters / creation_time

    # [COLOR-FIX] 色彩元数据注入：有值透传，unknown 按分辨率/位深/帧率推断。
    # 所有编码器都在滤镜链末尾追加 setparams，把色彩属性写到**帧**上，而不只写输出端
    # 参数。原因（2026-09-22 实测，见 memory/project_green_chroma_defect.md）：输出端
    # -colorspace 与解码帧的 csp:unknown 不一致时，ffmpeg 会在滤镜链尾与编码器之间
    # **自动插入一个 CPU scale**（debug 日志里的 auto_scale_0）去凑 codec context；
    # 这个转换在「硬解 + NVENC」链上会把 U/V 清零 → 产物全绿。给帧标好属性后两者
    # 一致，自动转换不再出现。vidcrop_cpu_v2.py 一直是这么做的（无条件 setparams）。
    # 例外的只有「链尾仍是显存帧」与 copy（滤镜和流复制互斥），见下面的守卫。
    color_args = build_color_args(input_file, ffmpeg_bin, meta=meta,
                                  color_range=color_range, hdr_mode=_hdr_mode)
    _sp = _setparams_from_color_args(color_args)
    # 强制 --color-range tv|pc 且与源实际值域不同 → 自动做真正的像素值域转换。
    # 必须在 _prepend_hwdownload 之前判断链首滤镜是否为 CUDA 原生。
    _conv = build_range_convert_filter(
        meta, color_range,
        vf_first_filter=vf_filter.split(',', 1)[0].split('=', 1)[0].strip(),
        warn=_warn)
    if _conv:
        vf_filter = f'{vf_filter},{_conv}'
    # --hdr sdr：真做 HDR→SDR。接在链尾（此时帧已是软件帧：CUDA cover 链在 crop 前
    # 就 hwdownload 过），且必须在 setparams 之前——setparams 写的是转换后的 SDR 属性。
    if _hdr_mode == 'sdr':
        vf_filter = f'{vf_filter},{build_tonemap_filter(_hdr_algo)}'
    # setparams 是 CPU 滤镜：链尾若还在显存里（没有 hwdownload 收尾的零拷贝链）就接不住，
    # 那种情况跳过；空链 + hof=cuda 同理。其余（含全部 GPU 编码器）一律追加。
    _tail = vf_filter.rsplit(',', 1)[-1].split('=', 1)[0].strip()
    if (_sp and codec.lower() != 'copy'
            and _tail not in _HW_OUTPUT_FILTERS
            and not (not vf_filter and hwaccel_output_format == 'cuda')):
        vf_filter = f'{vf_filter},{_sp}' if vf_filter else _sp

    # ── --pix-fmt / --bit-depth：显式指定时的落地 ──────────────────────
    # auto 完全沿用下面「[META-KEEP] 位深继承」那段既有逻辑（10bit 源才下发、
    # NVENC 内部再分），所以两个参数都不传时命令与改动前逐字相同。
    #
    # 两者语义重叠（格式名里已含位深），但**不在同一层级**：
    #   · --pix-fmt   实现级：格式名 = 位深 + 色度 + 排布，信息量是超集，
    #                 但合法性**与链相关**（零拷贝 CUDA 链只收 4 个值）。
    #   · --bit-depth 意图级：只有位深，信息量是子集，但合法性**与链无关**
    #                 （"10bit"在任何链上都成立，具体格式由链 + 编码器推导）。
    # 所以"恒定让谁赢"两端都有反例：--pix-fmt 恒赢会让
    # `--pix-fmt yuv420p10le --bit-depth 10` 双双失效（实测输出掉回 8bit nv12）；
    # --bit-depth 恒赢会把 `--pix-fmt yuv444p --bit-depth 10` 静默降成 4:2:0。
    # 正解是「能落地者赢」——与 --decode auto / --scale-algo auto 同一套哲学：
    # 先让 --pix-fmt 试；它在当前链上落不了地时由 --bit-depth 接管，并把让位的
    # 代价（位深/色度变化）明确说出来。
    _req_pf: Optional[str] = None
    _pf_handled = False
    _pf_explicit = False        # 用户显式写了 --pix-fmt（既非 auto 也非 none）
    _bd_declared = bit_depth is not None and bit_depth in _BIT_DEPTH_CHOICES
    if pix_fmt:
        _v = pix_fmt.strip().lower()
        if _v == 'none':
            _pf_handled = True                  # 明确不下发 -pix_fmt
        elif _v != 'auto':
            _req_pf = _v
            _pf_handled = True
            _pf_explicit = True
        elif _bd_declared:
            # --pix-fmt 保持 auto：--bit-depth 直接生效
            _req_pf = resolve_pix_fmt_for_depth(codec, bit_depth,
                                                warn=_warn, policy=policy)
            _pf_handled = True

    # 零拷贝 CUDA 链（-hwaccel_output_format cuda）不能传 -pix_fmt：
    # -pix_fmt 设的是 AVFrame.format（该链上是 AV_PIX_FMT_CUDA）而非 sw_format，
    # 传 nv12 / yuv420p 实测都报 "Impossible to convert"（memory/project_t4_gpu_capabilities.md）。
    # 该链上改格式只能写进 scale_cuda=format=，并配 -profile:v。
    _cuda_profile: Optional[str] = None
    if _req_pf is not None and hwaccel_output_format == 'cuda':
        _why = ''
        _how = ''
        if _req_pf not in _SCALE_CUDA_FORMATS:
            # 分两种：有等价格式（yuv420p10le → p010le）和无等价格式（yuv422p 等）。
            # 混成一句话会让用户以为"10bit 在这条链上做不到"，其实只是换个名字。
            _why = ('零拷贝 CUDA 链不能用 -pix_fmt 下发（该链帧格式是 AV_PIX_FMT_CUDA，'
                    f'实测传 nv12 / yuv420p 都报 "Impossible to convert"）；'
                    '改格式只能写进 scale_cuda=format=，而它只接受 '
                    f'{" / ".join(_SCALE_CUDA_FORMATS)}')
            _how = _cuda_pixfmt_hint(_req_pf)
        else:
            _new_vf = _apply_pix_fmt_to_cuda_filter(vf_filter, _req_pf)
            if _new_vf is not None:
                vf_filter = _new_vf
                _cuda_profile = _SCALE_CUDA_PROFILE.get(_req_pf)
            else:
                _why = (f'零拷贝 CUDA 链的链首是 '
                        f'{vf_filter.split(",", 1)[0].split("=", 1)[0]} 而不是 scale_cuda，'
                        f'没地方写 format={_req_pf}')
                _how = ('scale_cuda 只出现在 cover 模式；改用 --mode cover，'
                        '或改走软件帧链（--scale-algo libswscale-lanczos）')
        if _why:
            # ── 让位：--pix-fmt 在本链上落不了地时，由 --bit-depth 接管 ──
            # 只在"用户显式写了 --pix-fmt"时才谈让位：若 _req_pf 本身就是
            # --bit-depth 推出来的，它失败说明该位深在本链上确实做不到，无路可退。
            _bd_pf: Optional[str] = None
            if _pf_explicit and _bd_declared:
                _cand = resolve_pix_fmt_for_depth(codec, bit_depth,
                                                  warn=_warn, policy=policy)
                _cand_vf = (_apply_pix_fmt_to_cuda_filter(vf_filter, _cand)
                            if _cand in _SCALE_CUDA_FORMATS else None)
                if _cand_vf is not None:
                    vf_filter = _cand_vf
                    _cuda_profile = _SCALE_CUDA_PROFILE.get(_cand)
                    _bd_pf = _cand
            if _bd_pf is not None:
                _o_d, _o_c = _pixfmt_shape(_req_pf)
                _n_d, _n_c = _pixfmt_shape(_bd_pf)
                _diff = []
                if _o_d != _n_d:
                    _diff.append(f'位深 {_o_d}→{_n_d}bit')
                if _o_c and _n_c and _o_c != _n_c:
                    _diff.append(f'色度 {":".join(_o_c)}→{":".join(_n_c)}')
                _msg = (f'--pix-fmt {_req_pf} 在零拷贝 CUDA 链上无法下发，'
                        f'已改由 --bit-depth {bit_depth} 接管：'
                        f'scale_cuda=…:format={_bd_pf}'
                        + ('（同为该链原生格式，无信息损失）' if not _diff
                           else f'（注意：{" / ".join(_diff)}）'))
                if _diff and policy == 'strict':
                    raise ValueError(_msg + '\n     （--fallback-policy strict 不降级）')
                _warn(_msg)
            else:
                # 后果必须说清：忽略后不再下发 -pix_fmt，输出格式由链上 hwdownload 的
                # format（按源位深推导）决定 —— 所以"要 10bit 却给了 8bit 源"会真的掉位深。
                _fallback = _src_download_fmt(src_bits)
                _loss = ('→ 位深要求被丢弃' if _fallback == 'nv12'
                         and _req_pf.endswith(('10le', '12le')) else '')
                # 标题按来源写：--bit-depth 推出来的格式失败时说成 --pix-fmt 会误导
                _head = (f'--pix-fmt {_req_pf} 在零拷贝 CUDA 链上无法下发' if _pf_explicit
                         else f'--bit-depth {bit_depth} 推出的 {_req_pf}'
                              f' 在零拷贝 CUDA 链上无法下发')
                _tail = (f'\n     原因：{_why}'
                         f'\n     怎么办：{_how}'
                         f'\n     当前后果：已忽略该设置，输出按链上的 {_fallback} 走'
                         f'（源 {src_bits}bit）{_loss}')
                if policy == 'strict':
                    raise ValueError(_head + _tail + '\n     （--fallback-policy strict 不降级）')
                _warn(_head + _tail)
        _req_pf = None                          # 无论成功与否都不再下发 -pix_fmt

    # 视频滤镜 & 编码器
    vf_filter = _prepend_hwdownload(vf_filter, hwaccel_output_format,
                                    src_bits, hw_download_fmt)
    # 映射了封面轨时不能用 -vf：它会作用到所有输出视频流，与封面的 -c:v:N copy 冲突
    cmd += ['-filter:v:0' if meta is not None else '-vf', vf_filter]
    cmd += ['-c:v', codec]

    # 码率控制轴（--rc-mode / --qp / --lookahead）。生效范围与"忽略并告知"都在这里判：
    # 只有走到这一步才知道**本策略真正要用的编码器**（--codec auto / NVENC 不可用时的
    # 降级都会改变它）。libx265 的 lookahead 走 x265_params，必须与 HDR 元数据合并成
    # 同一条 -x265-params，故不在这里直接下发。
    rc_args, rc_x265 = apply_rc_control_args(
        codec, rc_mode, qp, lookahead, policy, _warn)

    # 质量参数（cq / crf 互斥，由 _resolve_quality_params 决定）。
    # [LOSSLESS] crf/cq == 0 的真无损改写（对照 Video_Enhancement 的 ffmpeg_io.py
    # crf=0 分支）：
    #   · libx264 的 -crf 0 本身就是无损，无需改写；
    #   · libx265 的 -crf 0 只是"近无损"，必须写 lossless=1（并入 -x265-params）；
    #   · *_nvenc 的 -cq 0 不是无损，rc_mode=auto 时改写为 -rc constqp -qp 0。
    _quality_is_zero = (
        (cq is not None and encoder_supports_cq(codec) and cq == 0)
        or (crf is not None and encoder_supports_crf(codec) and crf == 0)
    )
    _nvenc_lossless = (_quality_is_zero and codec in NVENC_CODECS
                       and rc_mode == 'auto' and not bitrate)

    if cq is not None and encoder_supports_cq(codec):
        if _nvenc_lossless:
            # -cq 0 在 VBR 下不是无损；rc_mode=auto（用户未指定模式）时改写为真无损。
            cmd += ['-rc', 'constqp', '-qp', '0', '-b:v', '0']
            print('  提示：--cq 0 → NVENC 真无损改写（-rc constqp -qp 0 -b:v 0）')
            # [借鉴2] 改写后**有效模式是 constqp**，而 constqp 下 NVENC 静默禁用
            # lookahead（对照 Video_Enhancement：crf=0 强制 constqp 且 LA=0）。
            # apply_rc_control_args 看到的仍是用户给的 rc_mode=auto，已按非 constqp
            # 下发了 `-rc-lookahead` —— 那会是一条**不生效**的选项，故就地摘掉并说明。
            if '-rc-lookahead' in rc_args:
                _i = rc_args.index('-rc-lookahead')
                del rc_args[_i:_i + 2]
                _warn(f'--lookahead {lookahead} 未生效（--cq 0 已改写为 NVENC constqp'
                      f' 真无损，该模式静默禁用 lookahead），已忽略')
        else:
            cmd += ['-cq', str(cq)]
            # [CQ-B0] NVENC 的 -cq 必须配 -b:v 0 才是纯恒定质量，否则受 ffmpeg
            # 默认码率约束（等价于 constrained quality）。对照 Video_Enhancement 的
            # `-cq:v N -b:v 0`。constqp 用 --qp 表达质量、无 -cq；给了 --bitrate 时
            # 用户要的正是"受码率约束"语义，两者都不补 0。
            if codec in NVENC_CODECS and not bitrate and rc_mode != 'constqp':
                cmd += ['-b:v', '0']
    elif crf is not None and encoder_supports_crf(codec):
        if codec in ('libvpx', 'libvpx-vp9') and not bitrate:
            # VP8/VP9 的 CRF 必须配合 -b:v 0 才是纯恒定质量，否则退化成
            # 受码率上限约束的 constrained quality。
            # 用户给了 --bitrate 时**不补这个 0**：同一个 -b:v 发两次会互相打架，
            # 而且此时用户要的正是"受码率约束"（constrained quality）语义。
            cmd += ['-b:v', '0']
        if codec == 'librav1e':
            # rav1e 不认 -crf（会被静默忽略），换算成等效 -qp
            cmd += ['-qp', str(crf_to_rav1e_qp(crf))]
        else:
            cmd += ['-crf', str(crf)]
        if _quality_is_zero and codec == 'libx265':
            # libx265 的 -crf 0 不是无损，必须显式 lossless=1；与 HDR 元数据、
            # lookahead 合并进同一条 -x265-params（见 build_hdr_args）。
            rc_x265.append('lossless=1')
            print('  提示：--crf 0 → libx265 真无损改写（lossless=1）')

    # NVENC 且用户显式指定了别的 rc_mode（或给了 --bitrate）时，--cq 0 无法在不改模式
    # 的前提下变无损 → 明确告知，不擅自改写。
    if _quality_is_zero and codec in NVENC_CODECS and not _nvenc_lossless:
        _warn('--cq 0 在 NVENC 下不是真无损：如需无损请用 '
              '--rc-mode constqp --qp 0（勿与 --bitrate 同给）')

    # --bitrate：所有编码器都下发 -b:v（libx264/265、libvpx*、libaom、libsvtav1 都认）。
    # 与质量参数并存时按 rc 模式分别处理，规则集中在 main() 的量纲校验里。
    if bitrate:
        cmd += ['-b:v', bitrate]
    cmd += rc_args                      # -rc / -qp / -rc-lookahead / -lag-in-frames

    # [借鉴2/E] NVENC 自适应量化（对照 Video_Enhancement SDK 的 enableAQ +
    # enableTemporalAQ）。非 NVENC 编码器没有这两个选项 → 忽略并告知。
    if nvenc_aq:
        if codec in NVENC_CODECS:
            cmd += ['-spatial-aq', '1', '-temporal-aq', '1']
        else:
            _warn(f'--nvenc-aq 仅对 *_nvenc 编码器生效，编码器 {codec} 已忽略')

    # libaom-av1 的速度档位：ffmpeg 默认 -cpu-used=1 慢到不可用（实测 320x240 仅 1fps），
    # 按资源自动取值；用户若已在 --extra-args 显式给过则尊重用户。
    if codec == 'libaom-av1' and '-cpu-used' not in extra_args:
        cmd += ['-cpu-used', str(auto_effort()[0])]

    # 编码器预设（已由调用方 normalize_preset 归一化，此处直接使用）
    if encoder_supports_preset(codec):
        cmd += ['-preset', preset]

    # 零拷贝 CUDA 链上改过格式时要配 -profile:v，否则 profile 与像素格式不匹配。
    if _cuda_profile:
        cmd += ['-profile:v', _cuda_profile]

    # [META-KEEP] 位深继承：10bit 源不再被降为 8bit。
    # 显式给了 --pix-fmt / --bit-depth 就以它们为准（_pf_handled 已置位）；
    # 'none'，或已在 CUDA 链上经 scale_cuda=format= 处理过，也整段跳过。
    if _req_pf is not None:
        cmd += ['-pix_fmt', _req_pf]
    elif not _pf_handled and meta is not None and src_bits >= 10:
        if codec.lower().endswith('_nvenc'):
            # NVENC 链路原先完全不传 -pix_fmt，交给 hw_frames_ctx 协商。但
            # h264_nvenc 只支持 8bit，喂 10bit 输入会让整条 GPU 策略以 rc=218 失败，
            # 结果退回 CPU 编码只为保住 10bit——得不偿失。故这里显式降 8bit 保住硬件加速。
            #
            # ⚠ 隐患已修：这段过去不看链型，零拷贝链（hof=cuda）上会同时拿到
            # -hwaccel_output_format cuda 与 -pix_fmt yuv420p，而那种组合实测会
            # "Impossible to convert"（memory/project_t4_gpu_capabilities.md）。
            # 现在零拷贝链走 _apply_pix_fmt_to_cuda_filter()（scale_cuda=format=），
            # 只有软件帧链才落到这里下发 -pix_fmt。
            if codec.lower() in _ENCODERS_8BIT_ONLY:
                _warn(f'{codec} 不支持 10bit 编码，已降级为 8bit 输出以保住硬件加速'
                      f'（如需 10bit 请用 hevc_nvenc / av1_nvenc 或 CPU 编码器）')
                cmd += ['-pix_fmt', 'yuv420p']
            # hevc_nvenc / av1_nvenc 支持 10bit：继续让 hw_frames_ctx 自行协商 p010le
        else:
            _pf = resolve_pix_fmt_for_source(codec, src_bits, warn=_warn)
            if _pf:
                cmd += ['-pix_fmt', _pf]

    cmd += pres['post']                  # 封面/字幕逐流 codec，需覆盖上面的 -c:v

    # 音频（WebM 容器不接受 AAC 等音轨，必要时自动改 Opus 重编码）
    audio_codec = resolve_audio_codec_for_container(
        audio_codec, output_file.suffix, meta, _warn)
    if audio_codec.lower() == 'copy':
        cmd += ['-c:a', 'copy']
    else:
        cmd += ['-c:a', audio_codec]
        if audio_bitrate:
            cmd += ['-b:a', audio_bitrate]

    # [META-KEEP] HDR10 静态元数据（libx265 走 -x265-params，其余尽力而为）。
    # 一律调用（meta 可能为 None）：rc_x265 里的 lookahead / lossless 也必须落地，
    # 过去用 `if meta is not None` 包住会让探测失败时把它们连同 HDR 一起静默丢弃。
    cmd += build_hdr_args(meta, codec, warn=_warn, hdr_mode=_hdr_mode,
                          extra_x265_params=rc_x265)

    # mp4/mov 快速启动
    if output_file.suffix.lower() in ('.mp4', '.m4v', '.mov'):
        cmd += ['-movflags', '+faststart']

    # 自定义追加参数
    if extra_args:
        cmd += extra_args

    # [COLOR-FIX] 输出端色彩参数（写入容器 colr box / 编码器 VUI）；
    # 置于 extra_args 之后，与主项目合并注入行为一致。
    # 与上面的 setparams 是**互补**而非二选一：setparams 把属性写到帧上（避免 ffmpeg
    # 自动插色彩转换），这里写容器标签。两者取值同源，不会互相冲突。
    cmd += color_args

    cmd += [str(output_file)]
    return cmd


# ═══════════════════════════════════════════════════════════════════
#  进度条执行
# ═══════════════════════════════════════════════════════════════════

def _file_bytes(path) -> int:
    """读取输入文件字节数；stat 失败按 0 处理（不参与吞吐量统计）。"""
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


class QueueETA:
    """整批队列的剩余时间预测（单任务进度条之外的「整批还要多久」）。

    口径：以「已处理字节 / 已耗时」作为吞吐率，外推剩余未开始文件的字节数。
    按字节加权而非「平均单文件耗时 × 剩余个数」，是因为同一批素材的分辨率、
    帧率与画质参数一致时耗时近似正比于输入体积，文件大小差异能被反映出来；
    且无需为未处理文件预先 ffprobe。
    """

    BAR_WIDTH = 20

    def __init__(self, sizes: List[int]) -> None:
        self.total = len(sizes)
        self._remaining = sum(sizes)
        self._timed_bytes = 0
        self._timed_seconds = 0.0

    def add(self, size: int, elapsed: float) -> None:
        """登记一个已结束的文件。

        失败/跳过同样出列（不会再被处理），但只有实际耗时 > 0 的文件计入
        吞吐率样本，否则跳过文件会把速率拉低到失真。
        """
        size = max(0, min(size, self._remaining))
        self._remaining -= size
        if elapsed > 0:
            self._timed_bytes += size
            self._timed_seconds += elapsed

    def eta(self) -> Optional[float]:
        """剩余整批预计秒数；无样本或已无剩余时返回 None。"""
        if self._remaining <= 0 or self._timed_bytes <= 0 or self._timed_seconds <= 0:
            return None
        return self._remaining / (self._timed_bytes / self._timed_seconds)

    def rate(self) -> Optional[float]:
        """当前吞吐率（字节/秒）；尚无样本返回 None。"""
        if self._timed_bytes <= 0 or self._timed_seconds <= 0:
            return None
        return self._timed_bytes / self._timed_seconds

    def split(self, size: int) -> Tuple[Optional[float], Optional[float]]:
        """把队列剩余拆成「当前文件之后的剩余」与「当前文件的字节估算」。

        供进度条使用：当前文件的剩余部分随任务实际帧率实时倒数，只有档下
        已经无法计入帧数的收尾阶段才退化回字节估算，避免整批剩余在整个任务
        期间纹丝不动。
        """
        rate = self.rate()
        if rate is None:
            return None, None
        return max(0, self._remaining - size) / rate, size / rate

    def line(self, handled: int, elapsed: float,
             done: int, failed: int, skipped: int) -> str:
        pct    = handled / self.total if self.total else 1.0
        filled = int(round(self.BAR_WIDTH * pct))
        bar    = '█' * filled + '░' * (self.BAR_WIDTH - filled)
        eta    = self.eta()
        eta_s  = _fmt_duration(eta) if eta is not None else '--'
        return (
            f'[{bar}] {handled}/{self.total}({pct * 100:.1f}%)  '
            f'完成 {done}  失败 {failed}  跳过 {skipped}  '
            f'累计 {_fmt_duration(elapsed)}  预计剩余 {eta_s}'
        )


def _run_with_progress(
    cmd: List[str],
    total_frames: Optional[int],
    queue_rest: Optional[float] = None,
    queue_cur: Optional[float] = None,
) -> Tuple[int, str]:
    """
    执行 FFmpeg 命令并在终端显示实时进度条。
    通过 -progress pipe:1 -nostats 获取结构化进度流；
    异步线程收集 stderr，失败时返回完整错误文本。
    进程注册到 _ACTIVE_PROCS 以支持 Ctrl+C 安全中断。

    queue_rest / queue_cur: 批量模式的整批剩余时间构件（秒），两者都非 None 时
               进度条尾部常驻「整批剩余」= 当前文件剩余 + 后续文件剩余，让用户
               不必回头翻历史输出就知道"这批还要多久"。当前文件剩余优先按实时
               帧率算，于是该数字会跟着当前任务一起倒数。
    """
    prog_cmd = list(cmd)
    try:
        i_idx = prog_cmd.index('-i')
        prog_cmd[i_idx:i_idx] = ['-progress', 'pipe:1', '-nostats']
    except ValueError:
        prog_cmd += ['-progress', 'pipe:1', '-nostats']

    # 整批剩余字段常驻尾随，为避免进度条换行，先从可用宽度里预留它的长度
    has_q   = queue_rest is not None and queue_cur is not None
    reserve = len('  整批剩余 00m00s') if has_q else 0
    term_w  = shutil.get_terminal_size((80, 24)).columns
    bar_w   = max(10, min(30, term_w - 52 - reserve))
    t0      = time.perf_counter()
    frame   = 0
    stderr_lines: List[str] = []

    try:
        proc = subprocess.Popen(
            prog_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # 命令里已带 -nostdin，这里再兜一层：彻底切断 stdin 继承，
            # 避免任何遗漏 -nostdin 的路径把长任务挂在 read() 上。
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            env=_ffmpeg_env(),
        )
        _register_proc(proc)

        def _drain_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line)

        t_stderr = threading.Thread(target=_drain_stderr, daemon=True)
        t_stderr.start()

        speed = ''
        for raw_line in proc.stdout:
            if _STOP_REQUESTED.is_set():
                proc.terminate()
                break
            line = raw_line.strip()
            if '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            if key == 'speed':
                speed = val.strip()
                continue
            if key != 'frame':
                continue
            try:
                frame = int(val.strip())
            except ValueError:
                continue

            elapsed = time.perf_counter() - t0
            fps     = frame / elapsed if elapsed > 0 else 0
            q_tail  = _queue_tail(queue_rest, queue_cur, frame, total_frames, fps)

            if total_frames and total_frames > 0:
                # 预探测的帧数偏小时 ffmpeg 报出的帧数会超出它；不上修的话进度会被
                # 钉在 100%、剩余恒为 0，而实际还在编码。
                total_frames = max(total_frames, frame)
                pct    = min(frame / total_frames, 1.0)
                filled = int(bar_w * pct)
                bar    = '█' * filled + '░' * (bar_w - filled)
                tail   = _eta_tail(frame, total_frames, fps)
                print(
                    f'\r  [{bar}] {pct*100:5.1f}%'
                    f'  {frame}/{total_frames}帧'
                    f'  fps={fps:5.1f}'
                    f'  speed={speed or "-":>6}'
                    f'  已用 {_fmt_duration(elapsed)}'
                    f'{tail}'
                    f'{q_tail}   ',
                    end='', flush=True,
                )
            else:
                print(
                    f'\r  已处理 {frame} 帧  {fps:.1f}fps  {elapsed:.1f}s   '
                    f'{q_tail}   ',
                    end='', flush=True,
                )

        proc.wait()
        t_stderr.join(timeout=3)
        print()  # 进度条换行

        return proc.returncode, ''.join(stderr_lines)

    except Exception as exc:
        print()
        return 1, str(exc)
    finally:
        if 'proc' in dir():
            _unregister_proc(proc)


# ═══════════════════════════════════════════════════════════════════
#  日志对齐辅助
# ═══════════════════════════════════════════════════════════════════

_SEP = '─' * 64


def _label(text: str, width: int = 12) -> str:
    """把标签按显示宽度补齐到 width 列（CJK 记 2 列），输出「标签 + 空格 + ': '」。

    用于让概览块各字段的冒号纵向对齐，与 vidcrop_cpu_v2.py 保持一致。
    """
    cells = sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)
    return text + ' ' * max(1, width - cells) + ': '


def _result(status: str, frames: int = 0, elapsed: float = 0.0,
            strategy: Optional[str] = None,
            fallback: bool = False) -> Dict[str, object]:
    """统一 process_file 的返回结构，供调用方汇总统计。

    status: 'done' / 'skipped' / 'failed' / 'dry-run'
    strategy / fallback: 成功时**实际生效**的那条策略名与它是否为降级策略。
        供批汇总块报告"本次真正走的档位"（对照 Video_Enhancement 的 _active_level），
        避免概览块报的计划档位与实跑不符。
    """
    return {'status': status, 'frames': frames, 'elapsed': elapsed,
            'strategy': strategy, 'fallback': fallback}


# ═══════════════════════════════════════════════════════════════════
#  单文件处理
# ═══════════════════════════════════════════════════════════════════

def process_file(
    input_file: Path,
    output_path: Path,
    orig_width: Optional[int],
    orig_height: Optional[int],
    out_width: int,
    out_height: int,
    codec: str,
    crf: Optional[int],
    cq: Optional[int],
    preset: str,
    overwrite: bool,
    container: Optional[str],
    batch_mode: bool,
    input_root: Optional[Path],
    decode: str,
    hw_caps: HardwareCapabilities,
    crf_ref: Optional[int] = None,
    cq_ref: Optional[int] = None,
    ffmpeg_bin: str = 'ffmpeg',
    mode: str = 'crop',
    audio_codec: str = 'copy',
    audio_bitrate: str = '128k',
    extra_args: Optional[List[str]] = None,
    no_skip_same_size: bool = False,
    dry_run: bool = False,
    file_index: int = 0,
    file_total: int = 0,
    suffix: Optional[str] = None,
    color_range: Optional[str] = None,
    crop_ratio: Optional[Tuple[int, int]] = None,
    scale_backend: str = 'auto',
    sw_algo: str = _SW_SCALE_FLAGS,
    cuda_algo: str = _CUDA_SCALE_ALGO,
    policy: str = 'auto',
    pix_fmt: Optional[str] = 'auto',
    bit_depth: Optional[int] = None,
    hdr: str = 'auto',
    rc_mode: str = 'auto',
    qp: Optional[int] = None,
    lookahead: Optional[int] = None,
    bitrate: Optional[str] = None,
    nvenc_aq: bool = False,
    chroma_check: bool = True,
    queue_rest: Optional[float] = None,
    queue_cur: Optional[float] = None,
) -> Dict[str, object]:
    """
    处理单个视频文件，支持动态策略降级。

    返回处理状态：'done' / 'skipped' / 'failed' / 'dry-run'，供调用方汇总统计。

    新增参数：
        mode            'crop'（居中裁剪）或 'cover'（等比缩放+裁剪）
        audio_codec     音频编码器，'copy' 流复制，其他值重编码
        audio_bitrate   音频重编码码率
        extra_args      追加到 FFmpeg 命令末尾的自定义参数列表
        no_skip_same_size  True 时即使尺寸相同也强制转码
        dry_run         True 时仅打印最优策略命令，不实际执行
        file_index      当前文件序号（1 起），用于 [i/n] 前缀
        file_total      文件总数，用于 [i/n] 前缀
        suffix          输出文件名后缀标记（`--suffix`），None 时用默认 _cropped / _covered / _cropcovered
        crop_ratio      crop-cover 模式裁剪步骤所用比例 (分子, 分母)；None 时用目标宽高比
        rc_mode / qp / lookahead / bitrate  码率控制轴：逐**策略**落到命令上（因为实际
                        编码器是逐策略解析出来的）。默认值全部表示"不下发任何相关选项"，
                        即与引入这四个参数之前逐字相同。
        nvenc_aq        True 时给 NVENC 策略加 -spatial-aq 1 -temporal-aq 1（逐策略判，
                        非 NVENC 的那条策略会忽略并告警）；默认 False。
        chroma_check    产物色度自检（默认 True）：策略成功后取样比对源与产物的 U/V，
                        疑似归零则判该策略失败、走既有降级链；--no-chroma-check 关闭
        queue_rest / queue_cur  整批剩余时间的两个构件（秒），透传给进度条常驻
                                显示；两者皆为 None 时不显示该字段
    """
    extra_args = extra_args or []

    # ── 确定原始尺寸 ──
    if orig_width is None or orig_height is None:
        try:
            actual_width, actual_height = get_video_dimensions(
                str(input_file), ffmpeg_bin
            )
        except Exception:
            return _result('failed')
    else:
        actual_width, actual_height = orig_width, orig_height

    t_file_start = time.perf_counter()
    # 括号内不再嵌套括号，避免 "crop（居中裁剪）)" 这类观感，故用 mode_inline 而非
    # 带全角括号的 mode_label（后者仅用于 main() 的概览输出）。
    mode_inline = {
        'cover': 'cover 等比缩放+裁剪',
        'crop-cover': 'crop-cover 先裁剪后缩放覆盖',
    }.get(mode, 'crop 居中裁剪')
    idx_tag = f'[{file_index}/{file_total}] ' if file_total else ''
    print(f'\n{idx_tag}{input_file.name}')
    # [META-KEEP] 源含旋转时提示显示尺寸：裁剪坐标基于存储坐标系（与 ffprobe 的
    # width/height 一致），旋转标签会原样保留，播放器里显示尺寸是互换后的。
    _rot_note = ''
    _meta = get_video_metadata(str(input_file), ffmpeg_bin)
    if _meta and _meta['derived']['rotation'] in (90, 270):
        _d = _meta['derived']
        _rot_note = (f'，含 {_d["rotation"]}° 旋转标签（显示 '
                     f'{_d["effective_width"]}x{_d["effective_height"]}）')
    # 源位深：决定 CUDA 缩放链里 hwdownload 的下载格式（8bit→nv12，10bit→p010le）
    _src_bits = _meta['derived']['src_bits'] if _meta else 8
    print('  ' + _label('目标尺寸')
          + f'{out_width}x{out_height} (源 {actual_width}x{actual_height}, '
            f'{mode_inline}{_rot_note})')

    # crop-cover 的裁剪步骤是否为空操作。裁剪比例与源比例不同时，即使最终尺寸
    # 与源相同，画面也已经变了（先裁掉一圈再缩放回来），不能按同尺寸跳过。
    _cc_noop = True
    if mode == 'crop-cover':
        _rn, _rd = crop_ratio if crop_ratio else (out_width, out_height)
        _cc_noop = calculate_auto_crop_size(
            actual_width, actual_height, _rn, _rd) == (actual_width, actual_height)

    # ── 同尺寸跳过（可通过 --no-skip-same-size 关闭）──
    if (actual_width == out_width and actual_height == out_height
            and _cc_noop and not no_skip_same_size):
        print('  ⏭  跳过：目标尺寸与原始尺寸相同（--no-skip-same-size 可强制转码）。')
        return _result('skipped')

    # ── crop 模式下目标不能大于源（cover / crop-cover 可放大，无此限制）──
    if mode == 'crop':
        if out_width > actual_width or out_height > actual_height:
            print(
                f'  ⏭  跳过：crop 模式下目标尺寸 ({out_width}x{out_height}) '
                f'大于原始尺寸 ({actual_width}x{actual_height})',
                file=sys.stderr,
            )
            return _result('skipped')

    # ── 构建输出文件路径 ──
    ext_codec = codec if codec not in ('auto', 'copy') else 'libx264'
    if batch_mode or not output_path.suffix:
        output_dir = output_path
        if input_root and input_root.is_dir():
            try:
                rel = input_file.parent.relative_to(input_root)
                output_dir = output_path / rel
            except Exception:
                pass
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = container if container else get_extension_from_codec(ext_codec)
        if ext is None:
            ext = input_file.suffix
        # 未指定 --suffix 时用模式默认后缀：cover → _covered，crop-cover → _cropcovered
        name_suffix = suffix if suffix else {
            'cover': '_covered',
            'crop-cover': '_cropcovered',
        }.get(mode, '_cropped')
        output_file = output_dir / f'{input_file.stem}{name_suffix}{ext}'
    else:
        output_file = output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if container is None and codec not in ('copy', 'auto'):
            if not check_container_compatibility(output_file.suffix, codec):
                rec = get_extension_from_codec(codec)
                print(
                    f'  警告：输出扩展名 \'{output_file.suffix}\' 可能与编码器 '
                    f'\'{codec}\' 不兼容，推荐使用 \'{rec}\'',
                    file=sys.stderr,
                )

    # ── 已存在检查 ──
    if output_file.exists() and not overwrite:
        print(f'  ⏭  跳过：输出文件已存在（--overwrite 可覆盖）：{output_file}')
        return _result('skipped')

    # ── 预探测总帧数（供进度条使用）──
    total_frames = _get_total_frames(str(input_file), ffmpeg_bin)

    # ── 按**源编解码器**确认 CUDA 硬解（NVDEC 的能力分编解码器：T4 解不了 AV1）──
    # has_decoder 是拿 H.264 微流探出来的机器级标志，直接拿它推断"这个源能硬解"
    # 会误判 → 每个该编码的文件都白跑一次必然失败的链；strict 下更是直接退出 2，
    # 而软解其实完全可行。这里拿**真实输入**试解 1 帧，结果按 codec 名缓存，
    # 同一批文件里同种编码只探一次。
    _src_codec = ''
    if _meta is not None:
        _src_codec = ((_meta.get('video') or {}).get('codec_name') or '').strip().lower()
    if (decode != 'cpu' and hw_caps.has_decoder and _src_codec
            and _src_codec not in hw_caps.cuda_decode_ok):
        _dec_ok = _probe_cuda_decode_codec(ffmpeg_bin, input_file)
        hw_caps.cuda_decode_ok[_src_codec] = _dec_ok
        if not _dec_ok:
            # 显式点名 --decode cuda 时，这一条就是"点名的后端够不到"——与全局那段
            # （--fallback-policy 唯一作用的所在）同语义：strict 失败、auto 降级。
            # 改动前 strict 在这条素材上**也是失败的**（只是要先真跑一次必败的链），
            # 所以这里不是放松语义，而是**提前失败 + 说清原因**。
            if decode == 'cuda' and policy == 'strict':
                print(f'  ✘ 源编解码器 {_src_codec} 在本机无法 CUDA 硬解（NVDEC 不支持）；'
                      f'--decode cuda 是你点名的后端，--fallback-policy strict 下不降级。',
                      file=sys.stderr)
                return _result('failed')
            print(f'  ⚠ 源编解码器 {_src_codec} 在本机无法 CUDA 硬解（NVDEC 不支持），'
                  f'本文件按软解处理')

    # 这次缩放是不是**恒等操作**（源尺寸 == 缩放后尺寸）：CUDA 缩放链只在 cover 模式
    # 生效，恒等时 CPU 侧本来就没有重采样成本可省 → 显存内缩放无利可图。
    _cover_w, _cover_h = _cover_scale_dims(actual_width, actual_height,
                                           out_width, out_height)
    _scale_identity = (mode == 'cover'
                       and (_cover_w, _cover_h) == (actual_width, actual_height))

    # ── 生成策略链 ──
    all_strategies = _generate_strategies(codec, hw_caps, decode, mode=mode,
                                          scale_backend=scale_backend, policy=policy,
                                          src_codec=_src_codec,
                                          src_bits=_src_bits,
                                          scale_identity=_scale_identity)

    # auto 缩放在软解时可能因为"不划算"而主动不走 hwupload 链 —— 不说清楚会被当成 bug。
    if mode == 'cover' and scale_backend == 'auto' and decode == 'cpu':
        _skip = _hwupload_skip_reason(_src_bits, _scale_identity)
        if _skip:
            print(f'  提示：--scale-algo auto 本次不走 hwupload 显存缩放链 —— {_skip}；'
                  f'要强制使用请显式写 --scale-algo cuda-lanczos')

    # ── Dry-run 模式：打印最优策略命令后返回 ──
    if dry_run:
        strategy = all_strategies[0]
        try:
            vf_filter = build_video_filter(
                mode, actual_width, actual_height, out_width, out_height,
                use_cuda=strategy['use_hw_filter'], crop_ratio=crop_ratio,
                cuda_scale=bool(strategy.get('cuda_scale', False)),
                cuda_upload=bool(strategy.get('hwupload', False)),
                src_bits=_src_bits,
                sw_algo=sw_algo, cuda_algo=cuda_algo,
            )
        except ValueError as exc:
            print(f'  ✘ 失败：滤镜构建 — {exc}', file=sys.stderr)
            return _result('failed')

        current_crf, current_cq = _resolve_quality_params(
            strategy['codec'], crf, cq, src_codec=codec,
            crf_ref=crf_ref, cq_ref=cq_ref,
        )
        norm_preset = strategy_preset(preset, codec, str(strategy['codec']))
        cmd = build_ffmpeg_cmd(
            input_file=input_file,
            output_file=output_file,
            vf_filter=vf_filter,
            codec=strategy['codec'],
            crf=current_crf,
            cq=current_cq,
            preset=norm_preset,
            overwrite=overwrite,
            hwaccel=strategy.get('hwaccel'),
            hwaccel_output_format=strategy.get('hwaccel_output_format'),
            ffmpeg_bin=ffmpeg_bin,
            audio_codec=audio_codec,
            audio_bitrate=audio_bitrate,
            extra_args=extra_args,
            color_range=color_range,
            pix_fmt=pix_fmt,
            bit_depth=bit_depth,
            hdr=hdr,
            rc_mode=rc_mode,
            qp=qp,
            lookahead=lookahead,
            bitrate=bitrate,
            nvenc_aq=nvenc_aq,
            policy=policy,
        )
        print('  ' + _label('输出文件') + str(output_file))
        print('  ' + _label('策略') + f'[1/{len(all_strategies)}] {strategy["name"]}')
        print('  ' + _label('执行命令') + shlex.join(cmd))
        return _result('dry-run')

    # ── 依次尝试策略链 ──
    for i, strategy in enumerate(all_strategies):
        if _STOP_REQUESTED.is_set():
            return _result('failed')

        current_codec    = strategy['codec']
        use_hw_filter    = strategy['use_hw_filter']
        use_cuda_scale   = bool(strategy.get('cuda_scale', False))
        use_cuda_upload  = bool(strategy.get('hwupload', False))
        hwaccel          = strategy.get('hwaccel')
        hwaccel_out_fmt  = strategy.get('hwaccel_output_format')

        # 构建视频滤镜
        try:
            vf_filter = build_video_filter(
                mode, actual_width, actual_height, out_width, out_height,
                use_cuda=use_hw_filter, crop_ratio=crop_ratio,
                cuda_scale=use_cuda_scale, cuda_upload=use_cuda_upload,
                src_bits=_src_bits,
                sw_algo=sw_algo, cuda_algo=cuda_algo,
            )
        except ValueError as exc:
            print(f'  ✘ 失败：滤镜构建 — {exc}', file=sys.stderr)
            return _result('failed')

        current_crf, current_cq = _resolve_quality_params(
            current_codec, crf, cq, src_codec=codec,
            crf_ref=crf_ref, cq_ref=cq_ref)
        # preset 为 None 表示用户没指定，此时按"请求的编码器"的默认档位换算到本策略
        # 的编码器（见 strategy_preset），保证降级前后档位等效。
        norm_preset = strategy_preset(preset, codec, current_codec)

        # [借鉴1] NVENC 策略失败时**先按 preset 降档重试一次**，再考虑整条策略降级。
        # 对照 Video_Enhancement：SDK 在 InitializeEncoder 返回 code=8（驱动不认该
        # preset）时降到 p4 重试、RC/LA 不变。ffmpeg CLI 侧没有 code=8 这一说，但
        # 旧驱动/T4 对高档 preset 同样可能拒绝——先降档能保住 GPU 路径，避免直接掉到
        # 软件编码（慢一个数量级）。只对 NVENC 且档位在 _NVENC_PRESET_RETRY 内追加；
        # 其余策略只有原档位（_preset_tries 长度 1）。
        _preset_tries: List[str] = [norm_preset]
        _retry_preset = (_NVENC_PRESET_RETRY.get(norm_preset)
                         if current_codec in NVENC_CODECS else None)
        if _retry_preset and _retry_preset != norm_preset:
            _preset_tries.append(_retry_preset)

        # [META-KEEP] 10bit 源 + hof=cuda 时 hwdownload 先试 p010；旧驱动或不支持
        # 10bit 下载的设备会失败，此时同一策略回退 nv12 再试一次（代价：降为 8bit、
        # HDR 帧级 side_data 一并丢失），而不是直接放弃整个 GPU 策略。
        # 注意：CUDA 缩放链（cuda_scale）的下载格式烘在滤镜串里，这套 hw_download_fmt
        # 重试够不到它——那边 p010 下载失败就是整条策略失败、降级到 CPU 缩放（结果仍正确）。
        _first_filter = vf_filter.split(',', 1)[0].split('=', 1)[0].strip()
        _dl_formats: List[Optional[str]] = [None]
        # 只有真正会插入 hwdownload,format=p010 时才值得回退重试；
        # crop_cuda / scale_cuda 这类原生滤镜不经过 _prepend_hwdownload，重试只会重复同一条命令。
        if (hwaccel_out_fmt == 'cuda' and _src_bits >= 10
                and _first_filter not in _CUDA_NATIVE_FILTERS):
            _dl_formats.append('nv12')

        rc, stderr_text = 1, ''
        for _pt_i, _preset_try in enumerate(_preset_tries):
            if _pt_i > 0:
                print(f'  ⚠ preset {norm_preset} 失败，降档到 {_preset_try} 重试'
                      f'（对照 NVENC code=8 降档；RC / lookahead 不变）',
                      file=sys.stderr)
            for _dl in _dl_formats:
                if _dl == 'nv12':
                    print('  ⚠ p010/p012 下载不被当前设备支持，回退为 8bit 下载'
                          '（将降级为 8bit，HDR 静态元数据与精细色阶会丢失）',
                          file=sys.stderr)

                cmd = build_ffmpeg_cmd(
                    input_file=input_file,
                    output_file=output_file,
                    vf_filter=vf_filter,
                    codec=current_codec,
                    crf=current_crf,
                    cq=current_cq,
                    preset=_preset_try,
                    overwrite=overwrite,
                    hwaccel=hwaccel,
                    hwaccel_output_format=hwaccel_out_fmt,
                    ffmpeg_bin=ffmpeg_bin,
                    audio_codec=audio_codec,
                    audio_bitrate=audio_bitrate,
                    extra_args=extra_args,
                    hw_download_fmt=_dl,
                    color_range=color_range,
                    pix_fmt=pix_fmt,
                    bit_depth=bit_depth,
                    hdr=hdr,
                    rc_mode=rc_mode,
                    qp=qp,
                    lookahead=lookahead,
                    bitrate=bitrate,
                    nvenc_aq=nvenc_aq,
                    policy=policy,
                )

                tag = '（降级）' if strategy.get('fallback', False) else ''
                print('  ' + _label('策略')
                      + f'[{i + 1}/{len(all_strategies)}] {strategy["name"]}{tag}')
                print('  ' + _label('执行命令') + shlex.join(cmd))

                rc, stderr_text = _run_with_progress(
                    cmd, total_frames, queue_rest, queue_cur)

                # rc=0 不代表产物存在：ffmpeg 在少数静默错误下会以 0 退出却不写文件。
                # 直接 stat() 会抛 FileNotFoundError 中断整批处理，故显式判为策略失败。
                if rc == 0 and not output_file.exists():
                    rc = 1
                    stderr_text = (stderr_text or '') + \
                        '\nffmpeg 返回 0 但未生成输出文件'

                # [CHROMA-CHECK] 产物色度自检（防复发钩子）。失败时把 rc 打成 1，
                # 后面那段既有的"策略失败 → 清理产物 → 试下一策略"会原样接管，
                # 自动退到软解/软编策略，不需要另写降级逻辑。
                if rc == 0 and chroma_check:
                    _c_ss = _chroma_sample_ss(probe_full_metadata(input_file, ffmpeg_bin))
                    _c_ok, _c_why = _chroma_check(
                        input_file, output_file, ffmpeg_bin, _c_ss)
                    if not _c_ok:
                        rc = 1
                        stderr_text = (stderr_text or '') + f'\n[色度自检] {_c_why}'
                        print(f'  ⚠ 色度自检未通过：{_c_why}', file=sys.stderr)

                if rc == 0:
                    elapsed  = time.perf_counter() - t_file_start
                    in_size  = input_file.stat().st_size
                    out_size = output_file.stat().st_size
                    ratio    = (1.0 - out_size / in_size) * 100 if in_size > 0 else 0.0
                    direction = '↓' if ratio >= 0 else '↑'
                    print(f'  ✔ 完成，用时 {_fmt_duration(elapsed)}')
                    print('  ' + _label('输出文件') + str(output_file))
                    print(
                        '  ' + _label('大小变化')
                        + f'{_fmt_size(in_size)} → {_fmt_size(out_size)}'
                        f'（{direction}{abs(ratio):.1f}%）'
                    )
                    # 记录**实际生效**的策略（对照 Video_Enhancement 的 _active_level），
                    # 供批汇总块报告真正走的档位，而非计划档位。
                    return _result('done', total_frames, elapsed,
                                   strategy=str(strategy['name']),
                                   fallback=bool(strategy.get('fallback', False)))

                if output_file.exists():
                    try:
                        output_file.unlink()
                    except Exception:
                        pass

        # 策略失败：打印 stderr 末 20 行，清理残留文件，尝试下一策略
        err_lines = [l for l in stderr_text.strip().splitlines() if l.strip()]
        if err_lines:
            print(f'  FFmpeg 错误输出（末 {min(20, len(err_lines))} 行）：',
                  file=sys.stderr)
            for el in err_lines[-20:]:
                print(f'    {el}', file=sys.stderr)
        print(f'  ✗ 策略失败（rc={rc}），尝试下一策略...', file=sys.stderr)
        if output_file.exists():
            try:
                output_file.unlink()
            except Exception:
                pass

    if policy == 'strict':
        print('  ✘ 失败：--fallback-policy strict 不降级，首选策略失败即终止。\n'
              '     去掉 strict（或改用 auto）可逐级降级重试。', file=sys.stderr)
    else:
        print('  ✘ 失败：所有策略均失败，放弃处理。', file=sys.stderr)
    return _result('failed')


# ═══════════════════════════════════════════════════════════════════
#  命令行解析与主入口
# ═══════════════════════════════════════════════════════════════════

# ── 三个正交轴 ──────────────────────────────────────────────────────
# 解码（--decode）/ 缩放（--scale-algo）/ 编码（--codec）互不干涉、可任意组合：
# 轴之间不再有任何「冲突检查」——`--decode cpu --codec h264_nvenc`（软解 + NVENC 硬编）
# 与 `--decode cuda --codec libx264`（硬解 + 软编）都是合法组合。
# 唯一横跨三者的开关是 --fallback-policy，且它只回答一个问题：
# 「显式点名的后端不可用/执行失败时，降级继续还是报错退出」。
_DECODE_BACKENDS = ('auto', 'cuda', 'vulkan', 'vaapi', 'opencl', 'cpu')
# 旧值 none 是 cpu 的同义词。它来自旧的 `--hwaccel none`——那个参数当年把整块 GPU
# （含 NVENC 编码与 scale_cuda 缩放）一起关掉；拆成三轴后 `none` 的字面意思只剩下
# 「不要硬件解码」，因此归一为 cpu，保留它是为了不让既有命令直接失效。
_DECODE_LEGACY_VALUES = {'none': 'cpu'}

_FALLBACK_POLICIES = ('auto', 'strict')
# 已删除的旧值 → 等价的三轴写法（报错文案里直接给可抄的命令，省得用户查文档）
_FALLBACK_LEGACY_VALUES = {
    'cpu-only':    '--decode cpu --scale-algo libswscale-lanczos --codec libx264',
    'nvenc-only':  '--decode cpu --codec h264_nvenc',
    'strict-cuda': '--decode cuda --fallback-policy strict',
}


def _decode_value(spec: str) -> str:
    """--decode 的取值校验（把旧值 none 归一为 cpu）。"""
    v = spec.strip().lower()
    v = _DECODE_LEGACY_VALUES.get(v, v)
    if v not in _DECODE_BACKENDS:
        raise argparse.ArgumentTypeError(
            f"'{spec}' 无效：只支持 {'/'.join(_DECODE_BACKENDS)}"
            f"（旧值 none 等价于 cpu）")
    return v


def _fallback_policy_value(spec: str) -> str:
    """--fallback-policy 的取值校验（旧值给出等价三轴写法）。"""
    v = spec.strip().lower()
    if v in _FALLBACK_LEGACY_VALUES:
        raise argparse.ArgumentTypeError(
            f"'{spec}' 已删除：--fallback-policy 现在只回答「显式点名的后端不可用时"
            f"降级还是报错」，只接受 {'/'.join(_FALLBACK_POLICIES)}。\n"
            f"  等价的三轴写法：{_FALLBACK_LEGACY_VALUES[v]}")
    if v not in _FALLBACK_POLICIES:
        raise argparse.ArgumentTypeError(
            f"'{spec}' 无效：只支持 {'/'.join(_FALLBACK_POLICIES)}")
    return v


def parse_bitrate(spec: Optional[str]) -> Optional[str]:
    """解析/校验 --bitrate（ffmpeg 记法：8M / 8000k / 12000000，裸数字按 bps）。

    与 parse_rc_mode 一样只抛 ValueError——报错由调用方打成 `[ERROR] …`，这样
    两个脚本的报错首行能逐字对比（交给 argparse 的 type= 会先印 usage 行）。
    """
    if not spec:
        return None
    s = str(spec).strip()
    if not _BITRATE_RE.match(s):
        raise ValueError(
            f"--bitrate '{spec}' 无效：需要码率写法，如 8M / 8000k / 12000000"
            f"（裸数字按 bps 解释）。")
    return s


def check_int_range(value: int, opt: str, rng: Tuple[int, int], why: str) -> int:
    """校验整数量程（--lookahead / --qp 共用）；越界抛 ValueError。

    非整数输入在 argparse 的 type=int 那层就被挡下了，此处只管量程。
    """
    lo, hi = rng
    if not (lo <= value <= hi):
        raise ValueError(
            f'{opt} 超出范围：需要 {lo}~{hi} 的整数（{why}），收到 {value}。')
    return value


class _RejectRenamedFlag(argparse.Action):
    """旧名 --hwaccel 命中即报错退出 2（硬改名，不做静默兼容）。"""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.exit(
            2,
            '\n[ERROR] --hwaccel 已更名为 --decode（该轴的语义也收窄为「只管解码」）。\n'
            '  取值改名：none → cpu，auto / cuda / vulkan / vaapi / opencl 不变。\n'
            '  例：--hwaccel none → --decode cpu     （只关硬解，编码/缩放仍可走 GPU）\n'
            '      --hwaccel cuda → --decode cuda\n'
            '  想要旧的「纯 CPU」行为，请用三轴写法：\n'
            '      --decode cpu --scale-algo libswscale-lanczos --codec libx264\n')


class _RejectRenamedSuffixFlag(argparse.Action):
    """旧名 --flag 命中即报错退出 2（硬更名为 --suffix，不做静默兼容）。"""

    def __call__(self, parser, namespace, values, option_string=None):
        _eq = f'--suffix {values}' if values is not None else '--suffix "<后缀>"'
        parser.exit(
            2,
            '\n[ERROR] --flag 已更名为 --suffix（取值与语义完全不变）。\n'
            f'  把 --flag 原样换成 --suffix 即可，例：{_eq}\n'
            '  它只改自动生成的输出名后缀（默认 _cropped / _covered / _cropcovered）；\n'
            '  --output 指定了完整文件名时不生效。\n')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='批量裁剪视频，支持 NVIDIA CUDA 硬件加速及智能降级。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
三个正交轴（互不干涉，可任意组合）：
  --decode auto/cuda/vulkan/vaapi/opencl/cpu
                    只决定「帧在哪解出来」（默认 auto）。
                    auto 与 --scale-algo auto 同一套逻辑：**先探测再定** ——
                    探测到可用硬解就给那个具体后端（CUDA > Vulkan > VA-API > OpenCL），
                    一个都没有就降级 cpu（不下发 -hwaccel）。
                    cpu=纯软解，但不影响编码与缩放：--decode cpu --codec h264_nvenc
                    = 软解 + NVENC 硬编；--decode cpu --scale-algo cuda-lanczos
                    = 软解 + 显存内缩放（自动加 hwupload_cuda）。
                    注意：--decode cpu 不再等于「纯 CPU」，纯 CPU 请写
                    --decode cpu --scale-algo libswscale-lanczos --codec libx264
  --scale-algo auto/libswscale-<algo>/cuda-<algo>
                    只决定「重采样在哪、用什么算法」（默认 auto=优先 cuda，失败回退 cpu）
  --codec <编码器> 只决定「用哪个编码器」（默认 h264_nvenc）
  --fallback-policy auto/strict
                    只决定「显式点名的后端不可用/失败时降级还是报错」（默认 auto=降级）

  旧名 --hwaccel 已更名为 --decode（取值 none → cpu），用旧名会直接报错。

处理模式：
  --mode crop       直接居中裁剪（默认，目标尺寸不能大于源尺寸）
  --mode cover      等比缩放至完全覆盖目标区域后居中裁剪（任意目标尺寸）
  --mode crop-cover 先裁剪、再缩放覆盖到最终尺寸（可放大）
                    裁剪步骤的比例取 --crop-ratio，未给出时即目标宽高比：
                      · 无 --crop-ratio：必须同时给出 --output-width 和 --output-height
                        （两者之比就是裁剪比例，链路退化为 裁剪 + 缩放）；
                      · 有 --crop-ratio：最终尺寸只需给一个维度，另一个按比例推导。
                    例： --mode crop-cover --crop-ratio 16:9 --output-width 1280
                         → 按 16:9 最大化裁剪后缩放为 1280x720

质量参数：
  --crf  CPU 编码器（libx264/265 等），默认 21，0-51 越小越好
  --cq   GPU 编码器（NVENC/AMF 等），默认 23，0-51 越小越好
  降级时 --cq 自动映射为对应 CRF（hevc_nvenc→libx265 时 +4，h264_nvenc→libx264 时 +1）

编码器别名（自动归一化）：
  h265_nvenc → hevc_nvenc,  x264 → libx264,  x265 → libx265

preset 映射（NVENC ↔ libx264 自动转换）：
  NVENC:   p1(fastest) ~ p7(slowest)，默认 slow（自动映射为 p5）
  libx264: ultrafast ~ veryslow

自定义 FFmpeg 参数：
  --extra-args 必须放在命令行最后，紧跟 '--' 分隔符：
  python vidcrop_hwaccel.py ... --extra-args -- -max_muxing_queue_size 4096
""",
    )

    # 基础输入输出
    parser.add_argument('--input', required=True,
                        help='输入视频文件或包含视频的目录')
    parser.add_argument('--output', required=True,
                        help='输出文件（单文件）或输出目录（批量）')
    parser.add_argument('-r', '--recursive', action='store_true',
                        help='递归扫描输入目录（默认仅扫描顶层）')

    # 尺寸
    parser.add_argument('--original-width',  type=int,
                        help='原始视频宽度（不提供则自动通过 ffprobe 检测）')
    parser.add_argument('--original-height', type=int,
                        help='原始视频高度（不提供则自动通过 ffprobe 检测）')
    parser.add_argument('--output-width',  type=int, default=None,
                        help='目标视频宽度（与 --crop-ratio 二选一；'
                             '配合 --crop-ratio 时可只给一个维度，另一个按比例推导）')
    parser.add_argument('--output-height', type=int, default=None,
                        help='目标视频高度（与 --crop-ratio 二选一；'
                             '配合 --crop-ratio 时可只给一个维度，另一个按比例推导）')
    parser.add_argument('--crop-ratio', type=str, default=None,
                        help='自动计算裁剪尺寸的目标宽高比，如 16:9 或 1.777'
                             '（与 --output-width/height 二选一；与其一并用时'
                             '前者定画面比例、后者定分辨率；crop-cover 下后者是缩放目标）')

    # 处理模式
    parser.add_argument('--mode', choices=['crop', 'cover', 'crop-cover'], default='crop',
                        help='处理模式：crop=居中裁剪（默认，目标不得大于源）；'
                             'cover=等比缩放覆盖后居中裁剪（任意尺寸）；'
                             'crop-cover=先按 --crop-ratio（未给出时用目标宽高比）'
                             '最大化裁剪，再缩放覆盖到 --output-width/height')

    parser.add_argument('--scale-algo', default=None, metavar='SPEC',
                        help='缩放算法，写法 <backend>-<algo> 或裸 <algo>（后端自动）。'
                             'libswscale-*：' + ' '.join(_SW_SCALE_ALGOS) + '；'
                             'cuda-*（仅 cover 模式、需自带 scale_cuda 的自建 FFmpeg）：'
                             + ' '.join(_CUDA_SCALE_ALGOS) + '。'
                             f'默认裸 {_SW_SCALE_FLAGS}：GPU 可用则走 cuda-{_CUDA_SCALE_ALGO}、'
                             f'否则 libswscale-{_SW_SCALE_FLAGS}。'
                             'libswscale-* / cuda-* 前缀用于强制后端；'
                             '裸名字要求两个后端都认（只在一个后端有的算法必须带前缀，'
                             '如 libswscale-spline）。crop 模式不做缩放、该参数不生效')

    # 视频编码
    parser.add_argument('--codec', default='h264_nvenc',
                        help='视频编码器（默认 h264_nvenc + --cq 23 + --preset p5；'
                             '无 NVENC 时自动降级为 libx264 + --crf 21 + --preset medium。'
                             '可用 auto；支持别名）。'
                             'H.264/HEVC：libx264/libx265、h264_nvenc/hevc_nvenc；'
                             'AV1：libsvtav1/libaom-av1/librav1e、av1_nvenc；'
                             'VP9：libvpx-vp9（NVENC 无 VP9 编码器，走硬解+CPU 编码）')
    parser.add_argument('--crf', type=int, default=None,
                        help='CRF 质量值（CPU 编码器，0-51，不指定时默认 21）；'
                             '字面量原样下发给目标编码器，不做换算')
    parser.add_argument('--cq',  type=int, default=None,
                        help='CQ 质量值（GPU 编码器，0-51，不指定时默认 23）；'
                             '字面量原样下发给目标编码器，不做换算')
    parser.add_argument('--crf-ref', type=int, default=None, metavar='N',
                        help='以 libx264 CRF 为统一基准给出质量值，按等效表换算到目标编码器。'
                             '例：--codec libvpx-vp9 --crf-ref 21 → -crf 27。'
                             '与 --crf / --cq 互斥')
    parser.add_argument('--cq-ref', type=int, default=None, metavar='N',
                        help='以 h264_nvenc CQ 为统一基准给出质量值，按等效表换算到目标编码器。'
                             '例：--codec hevc_nvenc --cq-ref 26 → -cq 28。'
                             '与 --crf / --cq 互斥')
    parser.add_argument('--rc-mode', default='auto', metavar='MODE',
                        help='NVENC 的码率控制模式（默认 auto=不下发 -rc，由 preset 决定，'
                             '与不写等价）。可选：constqp（恒定 QP，需 --qp）；'
                             'vbr / vbr_hq（可变码率）；cbr / cbr_hq / cbr_ld_hq'
                             '（恒定码率，需 --bitrate）。写法 <mode> 或 nvenc-<mode>'
                             '（本轴只有 NVENC 一个后端，故裸名不歧义）。'
                             '仅对 *_nvenc 编码器生效：libx264 / libx265 没有这个开关'
                             '（它们用 -crf / -b:v / -qp 的组合表达），届时告警忽略'
                             '（--fallback-policy strict 下报错）')
    parser.add_argument('--qp', type=int, default=None, metavar='N',
                        help='NVENC 恒定 QP 值（0-51）：只在 --rc-mode constqp 下生效，'
                             '与该模式外的 --cq / --crf / --crf-ref / --cq-ref 互斥'
                             '（constqp 用 --qp 表达质量，其它量纲混用无法判定意图）')
    parser.add_argument('--lookahead', type=int, default=None, metavar='N',
                        help='前向预测帧数（0-250）；不指定=沿用各编码器默认。'
                             '按编码器分别下发：libx264 与 *_nvenc 用 -rc-lookahead，'
                             'libx265 写进 -x265-params（与 HDR 元数据合并成同一条），'
                             'libvpx-vp9 / libaom-av1 用 -lag-in-frames；'
                             '其余编码器（如 libsvtav1）的选项名未实测，会告警忽略。'
                             '默认值本身不同：NVENC 是 0（关闭）、x265 是 20、x264 由自身决定')
    parser.add_argument('--bitrate', default=None, metavar='RATE',
                        help='目标码率（如 8M / 8000k / 12000000），按编码器下发 -b:v。'
                             '默认不指定。与质量参数同给时按 rc 模式区分：'
                             'auto / vbr* / cbr* 下并存 =「受码率约束的恒定质量」'
                             '（-b:v 视作上限）；constqp 下报错（该模式完全无视 -b:v）。'
                             'cbr* 模式未给本参数会落到 ffmpeg 默认码率（200kbps），会告警')
    parser.add_argument('--nvenc-aq', action='store_true',
                        help='给 NVENC 编码器开启自适应量化（-spatial-aq 1 -temporal-aq 1），'
                             '同码率下画质略升、速度略降（对照 Video_Enhancement 的 '
                             'enableAQ/enableTemporalAQ）。默认关闭；非 NVENC 策略会忽略'
                             '并告警（逐策略判断，因为降级策略的编码器可能不是 NVENC）')
    parser.add_argument('--preset', default=None,
                        help='编码器预设。默认：CPU 编码器 medium，GPU 编码器 p5；'
                             'NVENC（p1~p7）与 libx264 风格（ultrafast~veryslow）自动双向映射')
    parser.add_argument('--suffix', default=None, metavar='SUFFIX',
                        help='输出文件名后缀标记，用于替代默认的 _cropped / _covered / '
                             '_cropcovered。'
                             '例：--suffix "_Croped" → abc.mp4 输出为 abc_Croped.mp4。'
                             '仅对工具自动生成的输出名生效（批量模式或 --output 为目录）；'
                             '--output 指定了完整文件名时不改动。'
                             '（旧名 --flag 已更名为 --suffix，用旧名直接报错）')
    # 旧名硬拒绝：注册成无操作、被隐藏的参数，命中即由 Action 报错退出 2
    parser.add_argument('--flag', nargs='?', action=_RejectRenamedSuffixFlag,
                        default=None, help=argparse.SUPPRESS)

    # 音频编码
    parser.add_argument('--audio-codec', default='copy',
                        help='音频编码器（默认 copy 流复制，可改为 aac / libopus 等）')
    parser.add_argument('--audio-bitrate', default='128k',
                        help='音频重编码码率（仅 --audio-codec 非 copy 时生效，默认 128k）')
    parser.add_argument('--color-range', choices=['auto', 'tv', 'pc'], default='auto',
                        help='输出 color_range：auto=源为 unknown 时取 tv、否则取源值'
                             '（不做值域转换）；tv=强制 limited(16-235)；'
                             'pc=强制 full(0-255)。强制 tv/pc 时若与源实际值域不同，'
                             '会自动插入 scale 滤镜做真正的像素值域转换（而非只改标签）')

    # 容器与文件处理
    parser.add_argument('--container',
                        help='手动指定容器扩展名（如 .mp4 / .mkv）')
    parser.add_argument('--overwrite', action='store_true',
                        help='覆盖已存在的输出文件')
    parser.add_argument('--no-skip-same-size', action='store_true',
                        help='即使源尺寸等于目标尺寸也强制转码（默认同尺寸跳过）')
    parser.add_argument('--no-chroma-check', action='store_false', dest='chroma_check',
                        default=True,
                        help='关闭「产物色度自检」（默认开启）：每条策略成功后取样比对'
                             '源与产物的 U/V，疑似把色度写没了（产物全绿）就判该策略失败、'
                             '自动降级。每次多跑 2 次短取样 ffmpeg（约 0.1~0.5s）。'
                             '源本身无色度、或取样失败时不判定，不会误伤。')

    # 解码轴（只管解码；编码看 --codec，缩放看 --scale-algo，三者互不干涉）
    parser.add_argument('--decode', type=_decode_value, default='auto', metavar='BACKEND',
                        help='硬件解码后端（默认 auto）：auto / cuda / vulkan / vaapi / '
                             'opencl / cpu。auto 与 --scale-algo auto 同逻辑：先探测，'
                             '可用则给那个具体后端（CUDA > Vulkan > VA-API > OpenCL），'
                             '都不可用则降级 cpu。cpu=纯软解，但**不影响** --codec 与 '
                             '--scale-algo（这两轴仍可走 GPU）；旧值 none 等价于 cpu。'
                             '注意 --decode cpu 不再等于「纯 CPU」——要纯 CPU 请用'
                             ' --decode cpu --scale-algo libswscale-lanczos --codec libx264')
    # 旧名硬拒绝：注册成无操作、被隐藏的参数，命中即由 Action 报错退出 2
    parser.add_argument('--hwaccel', nargs='?', action=_RejectRenamedFlag,
                        default=None, help=argparse.SUPPRESS)
    parser.add_argument('--ffmpeg-bin', default='ffmpeg',
                        help='FFmpeg 可执行文件路径（默认 ffmpeg）')
    parser.add_argument('--cuda-diagnostics', action='store_true',
                        help='启用 CUDA 诊断模式：输出多分辨率探针结果及详细错误信息，'
                             '帮助定位「硬解不可用」的根本原因（如驱动缺失、'
                             '库路径问题、容器设备映射缺失等）')
    parser.add_argument('--cuda-device-id', type=int, default=0,
                        help='CUDA 设备 ID（默认 0），用于多 GPU 环境选择解码设备')
    parser.add_argument('--fallback-policy', type=_fallback_policy_value,
                        default='auto', metavar='POLICY',
                        help='显式点名的后端不可用/执行失败时怎么办（默认 auto）：'
                             'auto=降级到下一档并提示；strict=报错退出 2，不降级。'
                             '它只回答这一个问题，与三个轴正交。'
                             '已删除的旧值：cpu-only / nvenc-only / strict-cuda'
                             '（用旧值会报错并给出等价的三轴写法）')
    parser.add_argument('--pix-fmt', default='auto', metavar='FMT',
                        help='输出像素格式（默认 auto）：'
                             'auto=继承源位深（10bit 源在软件编码器上是 yuv420p10le、'
                             'NVENC 是 p010le；h264_nvenc 只支持 8bit 会降为 yuv420p 并提示）；'
                             'none=不下发 -pix_fmt（交给 ffmpeg 自行协商）；'
                             '或写具体名字（yuv420p / yuv420p10le / p010le / nv12 / '
                             'yuv422p10le …），会校验该名字是否为 ffmpeg 认识的格式。'
                             '注：零拷贝 CUDA 链（-hwaccel_output_format cuda）不能传 -pix_fmt'
                             '（实测 Impossible to convert），该链上改格式会用 '
                             'scale_cuda=format= 并自动配 -profile:v；'
                             'scale_cuda 仅支持 nv12 / yuv420p / yuv444p / p010le。'
                             '与 --bit-depth 语义重叠：本参数是实现级（要特定色度/排布'
                             '时用它），在零拷贝链上落不了地时由 --bit-depth 接管；'
                             '只关心位深请直接用 --bit-depth——格式名是链相关的，位深不是')
    parser.add_argument('--bit-depth', type=int, default=None, metavar='N',
                        help='目标位深：8 / 10 / 12（默认 auto=继承源）。'
                             '按编码器选对应像素格式（如 libx265 的 10bit → '
                             'yuv420p10le，hevc_nvenc → p010le）；'
                             'h264_nvenc 只支持 8bit，要求 10bit+ 时会告警并降 8bit'
                             '（--fallback-policy strict 下改为报错）。'
                             '注意：与 --pix-fmt 语义重叠——同时给出时优先按 --pix-fmt 落地，'
                             '它在当前链上不可用（如零拷贝 CUDA 链只收 4 种格式）时改由本参数'
                             '接管，让位的代价会明确提示。只关心位深时建议只用本参数，'
                             '链和编码器会自动选对格式名')
    parser.add_argument('--hdr', default='auto', metavar='MODE',
                        help='HDR 处理（默认 auto）：'
                             'auto/keep=尽力保留 HDR10 静态元数据'
                             '（libx265 会写 master-display/max-cll；NVENC 写不进去，会告警）；'
                             'drop=不写 HDR 静态元数据、色彩标签按 SDR(bt709) 写，'
                             '**像素不动**；sdr=真的做 HDR→SDR tone mapping'
                             '（zscale+tonemap，可带算法：sdr:hable / sdr:reinhard …，'
                             '默认 mobius）。'
                             '注意：tonemap_cuda 上游不存在，所以 CUDA 链上没有硬件 '
                             'tone mapping —— 会先下载成软件帧再做')

    # 可观测性
    parser.add_argument('--dry-run', action='store_true',
                        help='仅生成并显示最优策略的 FFmpeg 命令，不执行转码')
    parser.add_argument('--log', metavar='LOG_FILE',
                        help='将所有终端输出同时写入指定日志文件')

    # 自定义 FFmpeg 参数
    parser.add_argument('--extra-args', nargs=argparse.REMAINDER,
                        help='追加到 FFmpeg 输出参数末尾的自定义参数（必须放在命令最后）')

    return parser.parse_args()


def main() -> int:
    install_signal_handlers()

    args = parse_args()

    # 设置日志记录
    if args.log:
        setup_log(args.log)

    ffmpeg_bin = args.ffmpeg_bin

    # 验证 FFmpeg 可用性
    for tool in ('ffmpeg', 'ffprobe'):
        bin_path = tool if tool == 'ffmpeg' else _get_ffprobe_bin(ffmpeg_bin)
        if bin_path in ('ffmpeg', 'ffprobe'):
            if shutil.which(tool) is None:
                print(f'[ERROR] 系统中未找到 {tool}，请先安装 FFmpeg。', file=sys.stderr)
                return 1
        # 自定义路径时不强制检查（allow custom ffmpeg-bin locations）

    # 验证参数冲突：--crop-ratio 与 --output-width/height 不能同时指定
    has_explicit_size = args.output_width is not None and args.output_height is not None
    has_any_size = args.output_width is not None or args.output_height is not None
    has_crop_ratio = args.crop_ratio is not None
    is_crop_cover = args.mode == 'crop-cover'

    if is_crop_cover:
        # crop-cover 是唯一允许 --crop-ratio 与 --output-width/height 并用的模式：
        # 前者定裁剪比例（未给出时用目标宽高比），后者定缩放后的最终尺寸。
        #   · 无 --crop-ratio：裁剪比例就来自目标宽高比，两个维度缺一不可；
        #   · 有 --crop-ratio：比例已定，最终尺寸只需一个维度，另一个按比例推导。
        if has_crop_ratio:
            if not has_any_size:
                print('[ERROR] --mode crop-cover 配合 --crop-ratio 时，'
                      '还需提供 --output-width 或 --output-height 之一。', file=sys.stderr)
                return 2
        elif not has_explicit_size:
            print('[ERROR] --mode crop-cover 未提供 --crop-ratio 时，'
                  '必须同时提供 --output-width 和 --output-height。', file=sys.stderr)
            return 2
    else:
        if has_explicit_size and has_crop_ratio:
            print('[ERROR] --crop-ratio 与 --output-width/--output-height 不能同时指定，请二选一。', file=sys.stderr)
            return 2

        if not has_explicit_size and not has_crop_ratio:
            print('[ERROR] 必须指定 --output-width/--output-height 或 --crop-ratio 其中之一。', file=sys.stderr)
            return 2

    # -ref 系列与字面量 --crf/--cq 互斥：两者量纲不同，混用无法判断用户意图
    _refs = [n for n, v in (('--crf-ref', args.crf_ref), ('--cq-ref', args.cq_ref))
             if v is not None]
    if len(_refs) > 1:
        print('[ERROR] ' + ' 与 '.join(_refs) + ' 只能二选一（两者基准轴不同）。',
              file=sys.stderr)
        return 2
    if _refs and (args.crf is not None or args.cq is not None):
        _given = [n for n, v in (('--crf', args.crf), ('--cq', args.cq)) if v is not None]
        print('[ERROR] ' + _refs[0] + ' 与 ' + ' / '.join(_given)
              + ' 互斥：前者按统一基准轴换算，后者字面量原样下发，'
                '混用无法确定以哪个为准。请只保留其中一种。', file=sys.stderr)
        return 2

    # 验证显式尺寸（凡是给出来的维度都必须是正整数）
    for _opt, _value in (('--output-width', args.output_width),
                         ('--output-height', args.output_height)):
        if _value is not None and _value <= 0:
            print(f'[ERROR] {_opt} 必须为正整数。', file=sys.stderr)
            return 2

    # 解析 crop-ratio（如果指定了）
    crop_ratio_num = None
    crop_ratio_den = None
    if has_crop_ratio:
        try:
            crop_ratio_num, crop_ratio_den = parse_crop_ratio(args.crop_ratio)
            print(f'自动裁剪模式：目标宽高比 {crop_ratio_num}:{crop_ratio_den} (≈{crop_ratio_num/crop_ratio_den:.3f})')
        except ValueError as exc:
            print(f'[ERROR] {exc}', file=sys.stderr)
            return 2

    # --crop-ratio + 只给了一个维度：按裁剪比例补全另一个。三个模式同语义 ——
    # 比例定画面形状，尺寸定分辨率（crop-cover 下补全的即最终尺寸；crop / cover
    # 下尺寸本来就是最终尺寸，crop 模式仍受"目标不得大于源"限制）。
    # 补全后的尺寸即目标尺寸，故 width : height == crop_ratio_num : crop_ratio_den。
    # 此前非 crop-cover 模式下那个维度会被**静默丢弃**：`--crop-ratio 16:9
    # --output-height 1080` 会退化成"按 16:9 最大化裁剪"，源恰为 16:9 时直接命中
    # 同尺寸跳过、什么都没做（把 1080p 请求变成 no-op）。
    if has_crop_ratio and has_any_size and not has_explicit_size:
        if args.output_width is None:
            args.output_width = derive_even_dimension(
                args.output_height * crop_ratio_num / crop_ratio_den)
        else:
            args.output_height = derive_even_dimension(
                args.output_width * crop_ratio_den / crop_ratio_num)
        print(f'提示：--mode {args.mode} 仅给了一个维度，已按裁剪比例 '
              f'{crop_ratio_num}:{crop_ratio_den} 补全为 '
              f'{args.output_width}x{args.output_height}。')

    # --scale-algo：解析成 (backend, sw_algo, cuda_algo)。位置固定在
    # 「尺寸/crop-ratio 那一组校验之后、质量参数之前」——两脚本必须同顺序。
    try:
        args.scale_backend, args.sw_algo, args.cuda_algo = parse_scale_algo(args.scale_algo)
    except ValueError as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 2
    if args.mode == 'crop' and args.scale_algo:
        print('提示：--mode crop 不做缩放，--scale-algo 不生效。')
    # 三个轴之间**不做任何冲突检查**（解码 / 缩放 / 编码各自独立、可任意组合：
    # `--decode cpu --codec h264_nvenc` 与 `--decode cuda --codec libx264` 都合法）。
    # --fallback-policy 也不再与轴冲突——它只回答「够不到时降级还是报错」。
    # 旧的 `--fallback-policy cpu-only|nvenc-only|strict-cuda` 已在 argparse 层
    # 报错并给出等价的三轴写法，所以到这里不会再有「轴 vs 策略」的矛盾组合。

    # --pix-fmt：只在**显式**给出（非 auto / none）时才惰性校验格式名。
    # 拼错原本要等到 ffmpeg 才报，错误信息是难读的 "Unrecognized pixel format"；
    # 这里提前拦住并给出查列表的方法。探测本身失败时不阻塞（_pix_fmt_exists 放行）。
    _pf_arg = (args.pix_fmt or 'auto').strip().lower()
    if _pf_arg not in ('auto', 'none') and not _pix_fmt_exists(ffmpeg_bin, _pf_arg):
        print(f'[ERROR] --pix-fmt {args.pix_fmt} 不是 {ffmpeg_bin} 认识的像素格式。\n'
              f'  用 `{ffmpeg_bin} -pix_fmts` 查看可用列表（取 NAME 列）。', file=sys.stderr)
        return 2

    # --bit-depth：None 即 auto（继承源）。给了非 8/10/12 的值直接报错。
    if args.bit_depth is not None and args.bit_depth not in _BIT_DEPTH_CHOICES:
        print(f'[ERROR] --bit-depth 只支持 {" / ".join(str(d) for d in _BIT_DEPTH_CHOICES)}'
              f'（不指定即 auto=继承源），收到 {args.bit_depth}。', file=sys.stderr)
        return 2
    # 两个参数语义重叠（格式名里已含位深），但不在同一层级：--pix-fmt 是实现级
    # （要特定色度/排布时用它），--bit-depth 是意图级（只要位深就用它，链和编码器
    # 会自动选对格式名）。同时给出时先按 --pix-fmt 落地，它在当前链上不可用时
    # 再由 --bit-depth 接管——所以这里不能说"忽略 --bit-depth"（那正是曾经的误导：
    # 两个参数会一起失效，用户却以为 --pix-fmt 生效了）。
    if args.bit_depth is not None and _pf_arg not in ('auto', 'none'):
        print(f'提示：--pix-fmt {args.pix_fmt} 与 --bit-depth {args.bit_depth} 语义重叠'
              f'（格式名已含位深）：优先按 --pix-fmt 落地，它在当前链上不可用时'
              f'（如零拷贝 CUDA 链只收 {" / ".join(_SCALE_CUDA_FORMATS)}）'
              f'由 --bit-depth {args.bit_depth} 接管。')
        print('      只关心位深时建议直接用 --bit-depth——格式名是链相关的，位深不是。')
    # --hdr：解析校验 + 能力探测（tone mapping 需要 zscale 与 tonemap 两个滤镜）
    try:
        _hdr_mode, _hdr_algo = parse_hdr_spec(args.hdr)
    except ValueError as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 2
    if _hdr_mode == 'sdr':
        for _f in ('tonemap', 'zscale'):
            if _filter_exists(ffmpeg_bin, _f):
                continue
            if args.fallback_policy == 'strict':
                print(f'[ERROR] --hdr sdr 需要 {_f} 滤镜，但 {ffmpeg_bin} 没有'
                      f'（--fallback-policy strict 不降级）。', file=sys.stderr)
                return 2
            print(f'  ⚠ --hdr sdr 需要 {_f} 滤镜，但 {ffmpeg_bin} 没有；'
                  f'已降级为 --hdr drop（只改写色彩标签、不做像素转换）', file=sys.stderr)
            args.hdr = 'drop'
            break

    # ── 码率控制轴（--rc-mode / --qp / --bitrate）：量程已在 argparse 层校验，
    #    这里只处理**量纲冲突**与"必须有配套参数"两类规则。──
    # 为什么"非 NVENC 编码器 → 忽略"不在这里判：hwaccel 的实际编码器是**逐策略**
    # 解析出来的（--codec auto、NVENC 不可用时的降级都会改变它），要等命令构建
    # 拿到真正要用的编码器再告知，见 apply_rc_control_args()。
    # 取值/量程校验统一在这里做并打成 `[ERROR] …`（不交给 argparse 的 type=，
    # 否则 usage 行会抢在报错前面，两脚本的报错首行就没法逐字对比了）。
    try:
        args.rc_mode = parse_rc_mode(args.rc_mode)
        args.bitrate = parse_bitrate(args.bitrate)
        if args.lookahead is not None:
            check_int_range(args.lookahead, '--lookahead', _LOOKAHEAD_RANGE, _LOOKAHEAD_HINT)
        if args.qp is not None:
            check_int_range(args.qp, '--qp', _QP_RANGE, _QP_HINT)
    except ValueError as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 2

    _quality_given = [n for n, v in (('--crf', args.crf), ('--cq', args.cq),
                                     ('--crf-ref', args.crf_ref),
                                     ('--cq-ref', args.cq_ref)) if v is not None]
    if args.rc_mode == 'constqp':
        if args.qp is None:
            print('[ERROR] --rc-mode constqp 需要 --qp 指定恒定 QP（0-51）。\n'
                  '  例：--rc-mode constqp --qp 23', file=sys.stderr)
            return 2
        if args.bitrate:
            print('[ERROR] --rc-mode constqp 与 --bitrate 不能同时使用：\n'
                  '  constqp 是恒定 QP 模式，码率由 QP 决定，NVENC 会完全无视 -b:v。\n'
                  '  要限定码率请改用 --rc-mode vbr / vbr_hq / cbr*（或去掉 --rc-mode）。',
                  file=sys.stderr)
            return 2
        if _quality_given:
            print('[ERROR] --rc-mode constqp 与 ' + ' / '.join(_quality_given)
                  + ' 不能同时使用：constqp 用 --qp 表达质量，而 '
                  + ' / '.join(_quality_given)
                  + ' 属于 VBR 家族的量纲，混用无法确定以哪个为准。', file=sys.stderr)
            return 2
    else:
        if args.qp is not None:
            print(f'  ⚠ --qp 只在 --rc-mode constqp 下生效，当前 rc-mode={args.rc_mode}，'
                  f'已忽略。', file=sys.stderr)
            args.qp = None
        if args.bitrate and _quality_given:
            print('提示：--bitrate 与 ' + ' / '.join(_quality_given)
                  + ' 同时给出 → 按 ffmpeg 语义是「受码率约束的恒定质量」'
                    '（-b:v 视作上限，质量参数决定下限，VP9 下即 constrained quality）。'
                    '要纯恒定质量请去掉 --bitrate。')
        if args.rc_mode.startswith('cbr') and not args.bitrate:
            print(f'  ⚠ --rc-mode {args.rc_mode} 是恒定码率模式但未给 --bitrate，'
                  f'ffmpeg 会用它自己的默认码率（200kbps）。建议补 --bitrate 8M 之类。',
                  file=sys.stderr)

    if args.crf is not None and not (0 <= args.crf <= 63):
        print('[ERROR] --crf 建议范围为 0-63。', file=sys.stderr)
        return 2

    if args.cq is not None and not (0 <= args.cq <= 63):
        print('[ERROR] --cq 建议范围为 0-63。', file=sys.stderr)
        return 2

    # -ref 的基准轴量程：libx264 CRF 与 h264_nvenc CQ 都是 0-51
    if args.crf_ref is not None and not (0 <= args.crf_ref <= 51):
        print('[ERROR] --crf-ref 范围为 0-51（libx264 CRF 量程）。', file=sys.stderr)
        return 2

    if args.cq_ref is not None and not (0 <= args.cq_ref <= 51):
        print('[ERROR] --cq-ref 范围为 0-51（h264_nvenc CQ 量程）。', file=sys.stderr)
        return 2

    # 归一化编码器名称
    args.codec = normalize_codec_name(args.codec)

    # --preset 未指定时保持 None，由 process_file 按「请求的编码器」的默认档位换算到
    # 每条策略实际用的编码器（见 strategy_preset）：既让 GPU 策略拿到 p5、降级到
    # libx264 时得到 medium，也让 av1_nvenc 降级到 libsvtav1 时得到 p5 的等效档 8。

    # 归一化容器扩展名
    container_ext = args.container
    if container_ext:
        if not container_ext.startswith('.'):
            container_ext = '.' + container_ext
        container_ext = container_ext.lower()

    # 处理 --extra-args
    extra_args = normalize_extra_args(args.extra_args)

    # 双参数提示
    if args.crf is not None and args.cq is not None:
        print('提示：同时指定了 --crf 和 --cq，将根据实际编码器自动选用对应参数。')

    # ── 探测硬件能力：**按轴按需探测**（三轴正交，探测也必须正交）──
    # 解码轴决定探不探解码器；编码轴（--codec）与缩放轴（--scale-algo）都可能需要
    # NVENC，所以由它们决定探不探编码器；滤镜与解码轴无关，只要不是「三轴全 CPU」就探。
    #
    # 编码轴要不要 NVENC：--codec auto 可能落到 NVENC，所以也算需要。
    want_nvenc = (args.codec == 'auto' or args.codec in NVENC_CODECS
                  or args.scale_backend == 'cuda')
    # 三轴都显式指向 CPU → 一件都不探（这是旧 --hwaccel none / cpu-only 的快速路径）
    all_cpu = (args.decode == 'cpu'
               and args.scale_backend == 'libswscale'
               and not want_nvenc)

    if all_cpu:
        hw_caps = HardwareCapabilities()
        print('三轴都显式指向 CPU（--decode cpu + libswscale-* + CPU 编码器），跳过全部 GPU 探测。')
    else:
        hw_caps = detect_cuda_capabilities(
            ffmpeg_bin, args.decode,
            diagnostics=args.cuda_diagnostics,
            cuda_device_id=args.cuda_device_id,
            probe_encoders=want_nvenc,
            probe_filters=True,
        )
        if args.cuda_diagnostics:
            print(f'  [诊断] CUDA 解码状态: {"可用" if hw_caps.has_decoder else "不可用"}')

        # CUDA 缩放的功能探针：只在 **auto** 缩放、且零拷贝路径拿不到 CUDA 帧、
        # 且滤镜确实存在时才跑。显式 --scale-algo cuda-* 不探针（用户已确认：
        # 「直接执行，如遇失败则按 --fallback-policy 处理」）。
        # 默认路径（T4：有 NVDEC + scale_cuda → 走零拷贝 1b）不会多这一次 ffmpeg 调用。
        _zc_possible = hw_caps.has_decoder and hw_caps.has_cuda_scale
        if (args.mode == 'cover' and args.scale_backend == 'auto'
                and hw_caps.has_cuda_scale and not _zc_possible):
            print('  hwupload缩放: ', end='', flush=True)
            hw_caps.cuda_scale_upload_ok = _probe_cuda_scale_upload(ffmpeg_bin)
            hw_caps._mark_detected('cuda_scale_upload_ok')
            print('可用 ✓' if hw_caps.cuda_scale_upload_ok else '不可用 ✗')

        try:
            _save_hw_cache(hw_caps, ffmpeg_version=ffmpeg_bin)
        except Exception:
            pass  # 缓存写入失败不影响主流程

    # ── 显式点名的后端够不到时：auto 降级并提示 / strict 直接报错退出 ──
    # 这是 `--fallback-policy` 唯一的作用，与三个轴都正交。
    _downgrades: List[str] = []
    _strict_fail: List[str] = []

    if args.decode in ('cuda', 'vulkan', 'vaapi', 'opencl') \
            and not hw_caps.has_hwaccel(args.decode):
        _strict_fail.append(f'--decode {args.decode}：该后端不可用')
        _downgrades.append(f'--decode {args.decode} 不可用，已改为软解')

    if args.scale_backend == 'cuda' and not hw_caps.has_cuda_scale:
        _strict_fail.append('--scale-algo cuda-*：当前 FFmpeg 里没有 scale_cuda 滤镜')
        _downgrades.append(f'--scale-algo cuda-* 不可用（无 scale_cuda 滤镜），'
                           f'已改用 libswscale-{args.sw_algo}')

    if _strict_fail:
        if args.fallback_policy == 'strict':
            print('[ERROR] --fallback-policy strict：显式点名的后端不可用，不降级。',
                  file=sys.stderr)
            for _m in _strict_fail:
                print(f'  · {_m}', file=sys.stderr)
            print('  改用 --fallback-policy auto 可自动降级；或按提示改轴参数。',
                  file=sys.stderr)
            return 2
        for _m in _downgrades:
            print(f'  ⚠ {_m}', file=sys.stderr)
        if args.scale_backend == 'cuda' and not hw_caps.has_cuda_scale:
            args.scale_backend = 'libswscale'

    # 收集输入文件
    input_path  = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    video_files = collect_video_files(input_path, recursive=args.recursive)
    if not video_files:
        print('未找到任何视频文件，退出。', file=sys.stderr)
        return 1

    batch_mode = len(video_files) > 1 or input_path.is_dir()

    if batch_mode and output_path.suffix:
        print(f"警告：批量处理时输出路径 '{output_path}' 带扩展名，将视为目录。",
              file=sys.stderr)
        output_path = output_path.with_suffix('')

    if batch_mode and input_path.is_dir() and _same_path(input_path, output_path):
        print('[ERROR] 批量模式下 --input 和 --output 不能为同一目录。', file=sys.stderr)
        return 2

    if args.suffix and not batch_mode and output_path.suffix:
        print('提示：--output 已指定完整文件名，--suffix 不生效。', file=sys.stderr)

    input_root = input_path if input_path.is_dir() else input_path.parent

    # ── 打印任务概览（与 vidcrop_cpu_v2.py 对齐的双分割线结构化块）──
    mode_label = {
        'cover': 'cover（等比缩放+裁剪）',
        'crop-cover': 'crop-cover（先裁剪后缩放覆盖）',
    }.get(args.mode, 'crop（居中裁剪）')
    print(_SEP)
    print(_label('待处理文件') + f'{len(video_files)} 个')
    if has_crop_ratio:
        # 给了尺寸就一并显示（crop-cover 的尺寸定的是缩放目标；crop / cover 下
        # ratio 与尺寸并用时，尺寸就是最终尺寸 —— 只给一个维度时已按比例补全）
        _tail = (f'  最终尺寸: {args.output_width}x{args.output_height}'
                 if has_any_size else '')
        print(_label('处理模式')
              + f'{mode_label}  自动裁剪比例: {crop_ratio_num}:{crop_ratio_den}{_tail}')
    else:
        print(_label('处理模式')
              + f'{mode_label}  目标尺寸: {args.output_width}x{args.output_height}')
    # 只展示真正会生效的质量参数：显式指定的直接显示；都未指定时显示当前
    # 编码器对应的默认值。不再输出「CRF: 不使用」这类无效字段。
    quality_parts = []
    if args.crf is not None:
        quality_parts.append(f'CRF: {args.crf}')
    if args.cq is not None:
        quality_parts.append(f'CQ: {args.cq}')
    if args.crf_ref is not None:
        quality_parts.append(f'CRF-ref: {args.crf_ref}（libx264 基准，按等效表换算）')
    if args.cq_ref is not None:
        quality_parts.append(f'CQ-ref: {args.cq_ref}（h264_nvenc 基准，按等效表换算）')
    # 概览块展示**实际会生效**的编码器与缩放链：直接看策略链首条，这样
    #   · --codec auto 会被解析成具体编码器（h264_nvenc 或 libx264），不再显示 "auto"；
    #   · 请求的 NVENC 编码器不可用时也已反映为 CPU 编码器（见 _get_software_fallback）。
    # 概览块整批只打印一次，策略层那次才逐文件重复。
    _strategies = _generate_strategies(
        args.codec, hw_caps, args.decode, mode=args.mode,
        scale_backend=args.scale_backend, policy=args.fallback_policy)
    _primary = _strategies[0] if _strategies else {}
    _effective_codec = str(_primary.get('codec', args.codec))
    _eff_cuda_scale = bool(_primary.get('cuda_scale'))
    _eff_hwupload = bool(_primary.get('hwupload'))
    # 未指定 --preset 时按"请求的编码器"的默认档位换算，降级前后档位等效
    # （见 strategy_preset），避免概览显示 p5 而命令里却是 libsvtav1 的 8。
    # quiet：换算提示留给逐策略那次打印，概览块只展示结果值。
    _shown_preset = strategy_preset(args.preset, args.codec, _effective_codec,
                                    quiet=True)
    if not quality_parts:
        # 默认质量参数同样按实际生效的编码器取：libsvtav1 只认 -crf，
        # 若还按请求的 av1_nvenc 显示 "CQ: 23" 就与本行编码器自相矛盾。
        if encoder_supports_cq(_effective_codec):
            quality_parts.append(f'CQ: {DEFAULT_CQ}')
        elif encoder_supports_crf(_effective_codec):
            quality_parts.append(f'CRF: {DEFAULT_CRF}')
    # 没有 -preset 选项的编码器（libvpx-vp9 / libaom-av1 / librav1e）不展示 preset：
    # build_ffmpeg_cmd 里 encoder_supports_preset() 为假时根本不下发，展示了就是假信息。
    _preset_field = (f'preset: {_shown_preset}   '
                     if encoder_supports_preset(_effective_codec) else '')
    print(_label('编码器')
          + f'{_effective_codec}   {_preset_field}' + '   '.join(quality_parts))
    # 码率控制轴：只在用户真的点了相关参数时才出现这一行（默认全为空 → 概览块与
    # 引入这四个参数之前逐字相同）。
    _rc_bits = []
    if args.rc_mode != 'auto':
        _rc_bits.append(f'rc-mode: {args.rc_mode}')
    if args.qp is not None:
        _rc_bits.append(f'QP: {args.qp}')
    if args.bitrate:
        _rc_bits.append(f'码率: {args.bitrate}')
    if args.lookahead is not None:
        _rc_bits.append(f'lookahead: {args.lookahead}')
    if _rc_bits:
        print(_label('码率控制') + '   '.join(_rc_bits))
    # 只展示真正会生效的缩放档：crop 模式不缩放；cover 模式看策略链首条用的是哪条链
    if args.mode != 'crop':
        if _eff_cuda_scale:
            _scale_desc = (f'cuda-{args.cuda_algo}（显存内'
                           + ('，+hwupload_cuda' if _eff_hwupload else '') + '）')
        elif args.scale_backend == 'auto' and hw_caps.has_cuda_scale:
            # auto 缩放想用 cuda 但没落成：把原因说清，别让人以为参数没生效
            _scale_desc = (f'libswscale-{args.sw_algo}（CPU；'
                           + ('CUDA 缩放探针未通过' if not hw_caps.has_decoder
                              else '编码器不是 NVENC') + '，已按 auto 回退）')
        else:
            _scale_desc = f'libswscale-{args.sw_algo}（CPU）'
        print(_label('缩放') + _scale_desc)
    elif args.scale_algo:
        print(_label('缩放') + '不适用（crop 模式不缩放）')
    # 组合效果评估：三轴怎么搭会得到什么，说一句就够（效果本身用户自负）。
    _combo = _combo_hint(_primary.get('hwaccel'), _eff_cuda_scale, _eff_hwupload,
                         _effective_codec)
    if _combo:
        print(_label('组合') + _combo)
    # 只在"用户点名要的 NVENC 编码器"被换掉时才提示；--codec auto 解析出的具体
    # 编码器属正常自适应，不是降级。
    if args.codec in NVENC_CODECS and _effective_codec != args.codec:
        _why = ('硬件加速不可用或未探测到该 NVENC 编码器'
                if not hw_caps.has_any_encoder()
                else '当前环境未检测到该 NVENC 编码器')
        print(_label('降级提示')
              + f'{args.codec} 不可用（{_why}），实际将改用 CPU 编码器 {_effective_codec}。')
    print(_label('音频') + args.audio_codec
          + (f' @ {args.audio_bitrate}' if args.audio_codec.lower() != 'copy' else ''))
    if args.color_range != 'auto':
        print(_label('color_range') + args.color_range + '（必要时自动做值域转换）')
    if extra_args:
        print(_label('额外参数') + shlex.join(extra_args))
    if all_cpu:
        print(_label('解码') + '软件（三轴全 CPU，已跳过 GPU 探测）')
    else:
        # 展示**策略实际会用的**解码后端，而不是光回显请求值——否则
        # `--decode vulkan` 明明降级成软解了，概览却还写着 vulkan。
        _want_hw = args.decode
        _eff_hw = _primary.get('hwaccel')
        if _want_hw == 'cpu':
            _dec_desc = '软件（--decode cpu；只关解码，编码/缩放仍可能走 GPU）'
        elif _eff_hw is None:
            _dec_desc = ('软件（auto 探测无可用硬解，已降级 cpu）' if _want_hw == 'auto'
                         else f'软件（--decode {_want_hw} 不可用，已降级）')
        elif _eff_hw == _want_hw:
            _dec_desc = _want_hw
        else:
            _dec_desc = f'{_want_hw} → {_eff_hw}'
        print(_label('解码') + f'{_dec_desc}   '
              + (hw_caps.summary(only_detected=True) or '无可用加速组件'))
    print(_label('运行模式') + '顺序执行（细粒度实时进度条）')
    if args.dry_run:
        print(_SEP)
        print('DRY-RUN 模式：将仅显示命令，不执行转码。\n')
    else:
        print(_SEP)

    # 批量处理
    done_count = skipped_count = failed_count = 0
    # [借鉴1] 实际生效档位统计（见循环里对 res['strategy']/res['fallback'] 的收集）
    fallback_count = 0
    _last_strategy = ''
    peak_fps = 0.0
    sum_frames = 0
    sum_enc_elapsed = 0.0

    sizes = [_file_bytes(vf) for vf in video_files]
    queue = QueueETA(sizes) if len(video_files) > 1 else None

    try:
        for idx, vf in enumerate(video_files, start=1):
            if _STOP_REQUESTED.is_set():
                break

            # --crop-ratio 自己决定输出尺寸，仅限「crop / cover 且没给任何
            # --output-*」：给了任一维度就不再是"源最大化裁剪"，缺失的那个已在
            # 参数校验里按比例补全；crop-cover 只把 ratio 当裁剪比例，尺寸另给。
            if has_crop_ratio and not is_crop_cover and not has_any_size:
                if args.original_width is not None and args.original_height is not None:
                    src_w, src_h = args.original_width, args.original_height
                else:
                    src_w, src_h = get_video_dimensions(str(vf), ffmpeg_bin)
                out_width, out_height = calculate_auto_crop_size(src_w, src_h, crop_ratio_num, crop_ratio_den)
            else:
                out_width, out_height = args.output_width, args.output_height

            # 整批剩余拆成两段交给进度条：当前文件的剩余部分随实时帧率倒数，
            # 该文件之后的部分按字节估算（尚无样本时为 None，不显示该字段）
            q_rest = q_cur = None
            if queue is not None:
                q_rest, q_cur = queue.split(sizes[idx - 1])

            res = process_file(
                input_file=vf,
                output_path=output_path,
                orig_width=args.original_width,
                orig_height=args.original_height,
                out_width=out_width,
                out_height=out_height,
                codec=args.codec,
                crf=args.crf,
                cq=args.cq,
                preset=args.preset,
                overwrite=args.overwrite,
                container=container_ext,
                batch_mode=batch_mode,
                input_root=input_root,
                decode=args.decode,
                hw_caps=hw_caps,
                crf_ref=args.crf_ref,
                cq_ref=args.cq_ref,
                ffmpeg_bin=ffmpeg_bin,
                mode=args.mode,
                audio_codec=args.audio_codec,
                audio_bitrate=args.audio_bitrate,
                extra_args=extra_args,
                no_skip_same_size=args.no_skip_same_size,
                dry_run=args.dry_run,
                file_index=idx,
                file_total=len(video_files),
                suffix=args.suffix,
                color_range=args.color_range,
                crop_ratio=(crop_ratio_num, crop_ratio_den) if has_crop_ratio else None,
                scale_backend=args.scale_backend,
                sw_algo=args.sw_algo,
                cuda_algo=args.cuda_algo,
                policy=args.fallback_policy,
                pix_fmt=args.pix_fmt,
                bit_depth=args.bit_depth,
                hdr=args.hdr,
                rc_mode=args.rc_mode,
                qp=args.qp,
                lookahead=args.lookahead,
                bitrate=args.bitrate,
                nvenc_aq=args.nvenc_aq,
                chroma_check=args.chroma_check,
                queue_rest=q_rest,
                queue_cur=q_cur,
            )
            st = res['status']
            if st == 'done':
                done_count += 1
                # 统计**实际生效**的档位（对照 Video_Enhancement 的 _active_level）：
                # 只关心"有多少个文件没能走首选策略"，用于批汇总块的一句提醒。
                _used = res.get('strategy')
                if _used:
                    _last_strategy = str(_used)
                if res.get('fallback'):
                    fallback_count += 1
            elif st == 'failed':
                failed_count += 1
            elif st == 'skipped':
                skipped_count += 1
            frames, el = int(res['frames']), float(res['elapsed'])
            if el > 0 and frames > 0:
                sum_frames += frames
                sum_enc_elapsed += el
                peak_fps = max(peak_fps, frames / el)

            # 每个文件结束后刷新一次整批队列进度（最后一个文件由末尾「汇总」收尾）
            if queue is not None and not args.dry_run and idx < len(video_files):
                queue.add(sizes[idx - 1], el)
                print('  ' + _label('队列进度') + queue.line(
                    idx, sum_enc_elapsed,
                    done_count, failed_count, skipped_count,
                ))

    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        print('\n[INFO] 已中断。', file=sys.stderr)
        return 130

    if args.dry_run:
        print(_SEP)
        print(f'DRY-RUN 完成：共预览 {len(video_files)} 个文件的命令，未执行任何转码。')
        return 0

    avg_fps = sum_frames / sum_enc_elapsed if sum_enc_elapsed > 0 else 0.0
    print(_SEP)
    print(
        _label('汇总')
        + f'完成 {done_count}  失败 {failed_count}  跳过 {skipped_count}  '
        f'累计编码用时 {_fmt_duration(sum_enc_elapsed)}  '
        f'均速 {avg_fps:.0f}fps  峰值 {peak_fps:.0f}fps'
    )
    # [借鉴1] 只在真有降级时多打一行——计划档位（概览块）与实际档位不符时给出提醒。
    if fallback_count > 0:
        print(
            _label('实际档位')
            + f'{fallback_count} 个文件走了降级策略（末次：{_last_strategy}），'
            f'其余为各文件首选策略'
        )
    print(_SEP)

    if _STOP_REQUESTED.is_set():
        return 130

    return 0 if failed_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
