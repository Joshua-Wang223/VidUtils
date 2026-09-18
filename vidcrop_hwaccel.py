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
                    裁剪步骤的比例由 --crop-ratio 决定，未给出时即目标宽高比，
                    因此该模式下 --crop-ratio 可与 --output-width/height 并用：
                    有 --crop-ratio 时最终尺寸只需给一个维度，另一个按比例推导。
  • --crop-ratio 自动按目标宽高比（如 16:9）最大化裁剪，无需指定输出尺寸
    （crop-cover 模式下与 --output-width/height 并用，用于指定最终的缩放尺寸）
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

硬件加速
────────────────────────────────────────────────────────────────────
  • 运行时探测（非编译字符串匹配）下列能力：
        CUDA 解码、h264_nvenc、hevc_nvenc、crop_cuda 滤镜、
        Vulkan、VA-API、OpenCL
  • 按优先级自动生成策略链并依次尝试，前一级失败自动降级：
        1) CUDA 全流水线（硬解 + crop_cuda + NVENC 硬编）—— 仅 crop 模式
        2) 自动硬解 + NVENC 硬编（CPU 做 vf 滤镜）
        3) 指定硬解（cuda/vulkan/vaapi/opencl）+ 软件编码
        4) auto 模式下的最佳硬解 + 软件编码
        5) 纯 CPU 处理

        注：cover / crop-cover 模式因需要 scale 步骤，自动跳过策略 1
            （crop_cuda 不支持缩放）。

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
      --codec vp9 --crf 32 --hwaccel cuda

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

  # 5) 指定日志文件 + 禁用硬件加速（CI/容器环境）
  python vidcrop_hwaccel.py \\
      --input ./clips --output ./out \\
      --output-width 1280 --output-height 720 \\
      --hwaccel none --log process.log

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

    def can_full_pipeline(self, codec: str) -> bool:
        """全 GPU 流水线：硬解 + crop_cuda + NVENC 编码（仅 crop 模式可用）。"""
        return self.has_decoder and self.has_crop_cuda and self.has_nvenc(codec)

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
        # 探测命令此前漏了，导致 --hwaccel auto 卡在"正在检测硬件加速能力…"。
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


def _check_cuda_decoder_available(ffmpeg_bin: str = 'ffmpeg',
                                    diagnostics: bool = False) -> bool:
    """运行时探测 CUDA 硬件解码：生成微型 H.264 流后以 -hwaccel cuda 解码。

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
            cuda_errors = [
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
            ]
            if any(e in err for e in cuda_errors):
                errors_by_res[label] = (
                    '检测到 CUDA 错误: '
                    + _extract_ffmpeg_error(r2.stderr, cuda_errors)
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
                              hwaccel: Optional[str] = None,
                              diagnostics: bool = False,
                              cuda_device_id: int = 0) -> HardwareCapabilities:
    """运行时全面探测硬件加速能力，根据 --hwaccel 选择性探测。"""
    caps = HardwareCapabilities()
    if hwaccel == 'none':
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
    detect_all   = hwaccel in (None, 'auto')
    detect_cuda  = detect_all or hwaccel == 'cuda'
    detect_vulkan = detect_all or hwaccel == 'vulkan'
    detect_vaapi = detect_all or hwaccel == 'vaapi'
    detect_opencl = detect_all or hwaccel == 'opencl'

    if detect_cuda:
        print('  CUDA 解码:  ', end='', flush=True)
        # 新增：传入诊断模式与设备 ID
        caps.has_decoder = _check_cuda_decoder_available(
            ffmpeg_bin, diagnostics=diagnostics
        )
        caps._mark_detected('has_decoder')
        print('可用 ✓' if caps.has_decoder else '不可用 ✗')

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

        try:
            r = subprocess.run(
                [ffmpeg_bin, '-hide_banner', '-filters'],
                capture_output=True, text=True, timeout=10,
                env=_ffmpeg_env(),
            )
            caps.has_crop_cuda = 'crop_cuda' in r.stdout
        except Exception:
            caps.has_crop_cuda = False
        caps._mark_detected('has_crop_cuda')
        print(f"  crop_cuda:  {'可用 ✓' if caps.has_crop_cuda else '不可用 ✗'}")

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

    # 只打 ✓/✗ 分不清"本机不支持"和"根本没这个能力"，这里对两类高频问号给出
    # 原因与真实影响，省得误判成脚本故障：
    #   · av1_nvenc —— 7 代 NVENC（Turing/T4）无 AV1 编码器，属硬件限制，无法修复；
    #   · crop_cuda —— FFmpeg 6.1 根本没有该滤镜（只有 CPU 侧 crop），
    #                  故"全 GPU 流水线"在官方构建上必然跳过。
    _notes: List[str] = []
    if detect_cuda and not caps.has_encoder_av1:
        _notes.append('av1_nvenc 不可用：AV1 硬编需 8 代 NVENC（Ada / RTX 40 / L40 及以上）；'
                      '--codec av1_nvenc 会自动降级为 libsvtav1 CPU 编码')
    if detect_cuda and not caps.has_crop_cuda:
        _notes.append('crop_cuda 不可用：当前 FFmpeg 无此滤镜（6.1 只有 CPU 侧 crop），'
                      '全 GPU 流水线跳过；仍可走「硬解 + CPU 裁剪 + NVENC 硬编」')
    if _notes:
        print('  ── 说明 ──')
        for _n in _notes:
            print(f'  · {_n}')

    return caps


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


def build_hdr_args(meta: Dict, codec: str,
                   warn: Optional[Callable[[str], None]] = None) -> List[str]:
    """
    HDR10 静态元数据写入。色彩三参数由 build_color_args 从源透传，此处不重复指定。

    - libx265：显式 -x265-params，可靠。
    - NVENC：依赖帧 side_data 自动传播；走 hwdownload/CPU 回退链路时会丢失，故告警。
    - 其余编码器：只能保住色彩三参数与位深。
    """
    d = meta['derived']
    if not d['is_hdr']:
        return []
    c = (codec or '').lower()
    out: List[str] = []
    if c == 'libx265' and d['master_display']:
        params = ['master-display=' + d['master_display']]
        if d['max_cll']:
            params.append('max-cll=' + d['max_cll'])
        params.append('hdr10=1')
        out += ['-x265-params', ':'.join(params)]
    elif c.endswith('_nvenc'):
        # 实测（ffmpeg 6.1 + Tesla T4）：NVENC 无论走 cuda 全 GPU 还是 CPU 解码都
        # 不写入 mastering display / MaxCLL，hevc_metadata bsf 也无此能力。
        if warn and d['master_display']:
            warn('NVENC 不写入 mastering display / MaxCLL，HDR10 静态元数据会丢失'
                 '（色彩三参数与 10bit 位深仍保留）；如需完整 HDR10 元数据请用 libx265')
    elif warn and d['master_display']:
        warn(f'编码器 {c} 无法写入 mastering display / MaxCLL，仅保留色彩三参数与位深')
    return out


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
    scale 是 CPU 滤镜，链首若是 crop_cuda 等 CUDA 原生滤镜则无法直接接在后面，
    此时返回 None 并告警（避免生成必然失败的命令）。

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
            warn('CUDA 原生滤镜链路（crop_cuda 等）不支持插入 CPU 端 scale 做 '
                 'range 转换，已跳过转换（仅改标签）')
        return None
    if warn:
        warn(f'color_range 由源 {src} 转换为 {tgt}（插入 scale 滤镜做实际值域转换）')
    return f'scale=w=iw:h=ih:in_range={src}:out_range={tgt}'


def build_color_args(video_file: Path, ffmpeg_bin: str = 'ffmpeg',
                     meta: Optional[Dict] = None,
                     color_range: Optional[str] = None) -> List[str]:
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


def _build_cover_filter_str(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """
    生成等比缩放+居中裁剪滤镜字符串（cover 模式）。
    策略：比较宽高比，确定缩放方向，再裁剪到目标区域。
    cover 模式始终使用 CPU 侧滤镜，不支持 crop_cuda（crop_cuda 无法替代 scale 步骤）。
    """
    if src_w <= 0 or src_h <= 0:
        return f'scale={dst_w}:{dst_h}'

    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h

    if abs(src_ratio - dst_ratio) < 1e-3:
        # 比例完全一致，直接缩放
        return f'scale={dst_w}:{dst_h}'

    if src_ratio > dst_ratio:
        # 源比目标更宽：以高度为基准缩放，左右裁剪
        return f'scale=-2:{dst_h},crop={dst_w}:{dst_h}:(iw-{dst_w})/2:0'
    else:
        # 源比目标更高（或更窄）：以宽度为基准缩放，上下裁剪
        return f'scale={dst_w}:-2,crop={dst_w}:{dst_h}:0:(ih-{dst_h})/2'


def _build_crop_cover_filter_str(src_w: int, src_h: int, dst_w: int, dst_h: int,
                                 crop_ratio: Optional[Tuple[int, int]] = None) -> str:
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
    cover = _build_cover_filter_str(crop_w, crop_h, dst_w, dst_h)
    return f'{crop},{cover}' if cover else crop


def build_video_filter(mode: str, src_w: int, src_h: int, dst_w: int, dst_h: int,
                       use_cuda: bool = False,
                       crop_ratio: Optional[Tuple[int, int]] = None) -> str:
    """
    根据 mode 生成对应的 FFmpeg 视频滤镜字符串。

    mode='crop': 直接居中裁剪，use_cuda=True 时使用 crop_cuda（全 GPU 流水线专用）。
    mode='cover': 等比缩放+裁剪，始终使用 CPU 侧滤镜（忽略 use_cuda 参数）。
                  cover 需要 scale 步骤，crop_cuda 不支持，调用方已在策略生成阶段
                  排除了全 GPU 流水线（策略 1），此处 use_cuda 永远为 False。
    mode='crop-cover': 先按 crop_ratio（未给出时即目标宽高比 dst_w:dst_h）最大化
                  居中裁剪，再把裁剪结果等比缩放覆盖到 dst_w×dst_h。同样含 scale
                  步骤，故与 cover 一样只用 CPU 侧滤镜。

    Args:
        crop_ratio: (分子, 分母)，crop-cover 的裁剪步骤所用比例；None 时用目标宽高比。
    """
    if mode == 'cover':
        return _build_cover_filter_str(src_w, src_h, dst_w, dst_h)
    if mode == 'crop-cover':
        return _build_crop_cover_filter_str(src_w, src_h, dst_w, dst_h, crop_ratio)
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


def _select_best_hwaccel(hw_caps: HardwareCapabilities) -> Optional[str]:
    """按 CUDA > Vulkan > VA-API > OpenCL 优先级选择最佳硬件加速器。"""
    if hw_caps.has_decoder:
        return 'cuda'
    if hw_caps.has_vulkan:
        return 'vulkan'
    if hw_caps.has_vaapi:
        return 'vaapi'
    if hw_caps.has_opencl:
        return 'opencl'
    return None


def _generate_strategies(
    user_codec: str,
    hw_caps: HardwareCapabilities,
    hw_mode: str,
    mode: str = 'crop',
) -> List[Dict]:
    """
    根据硬件能力、用户意图和处理模式生成策略列表（优先级从高到低）。

    每个策略字段：
        name                  描述名称
        hwaccel               None / 'auto' / 'cuda' 等
        hwaccel_output_format None / 'cuda'
        use_hw_filter         是否使用 crop_cuda 滤镜（仅 crop 模式 + 全 GPU 流水线）
        codec                 实际编码器名称
        fallback              是否为降级策略
    """
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

    # ── 策略 1：全 GPU 流水线（硬解 + crop_cuda + NVENC 编码）──
    # cover / crop-cover 模式需要 scale 步骤，crop_cuda 不支持，
    # 此策略仅在 crop 模式下可用。
    if (hw_mode != 'none'
            and mode == 'crop'
            and is_nvenc
            and hw_caps.can_full_pipeline(preferred_codec)):
        strategies.append({
            'name':                  'CUDA 全加速（硬解 + crop_cuda + NVENC 编码）',
            'hwaccel':               'cuda',
            'hwaccel_output_format': 'cuda',
            'use_hw_filter':         True,
            'codec':                 preferred_codec,
            'fallback':              False,
        })

    # ── 策略 2：自动硬解 + NVENC 编码（CPU 侧 vf 滤镜）──
    if hw_mode != 'none' and is_nvenc and nvenc_available:
        strategies.append({
            'name':                  '自动硬件解码 + GPU 编码',
            'hwaccel':               'auto',
            'hwaccel_output_format': None,
            'use_hw_filter':         False,
            'codec':                 preferred_codec,
            'fallback':              False,
        })

    # ── 策略 3：指定硬件加速解码 + 软件编码 ──
    specific_hwaccels = ('cuda', 'vulkan', 'vaapi', 'opencl')
    if hw_mode in specific_hwaccels and hw_caps.has_hwaccel(hw_mode):
        hof = 'nv12' if hw_mode == 'opencl' else None
        strategies.append({
            'name':                  f'{hw_mode} 硬件解码 + CPU 编码',
            'hwaccel':               hw_mode,
            'hwaccel_output_format': hof,
            'use_hw_filter':         False,
            'codec':                 sw_codec,
            'fallback':              False,
        })

    # ── 策略 4：auto 模式下选最佳硬解 + 软件编码 ──
    if hw_mode == 'auto':
        best_hw = _select_best_hwaccel(hw_caps)
        if best_hw is not None:
            hof = 'nv12' if best_hw == 'opencl' else None
            strategies.append({
                'name':                  f'{best_hw} 硬件解码 + CPU 编码',
                'hwaccel':               best_hw,
                'hwaccel_output_format': hof,
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

    return strategies


# ═══════════════════════════════════════════════════════════════════
#  FFmpeg 命令构建
# ═══════════════════════════════════════════════════════════════════

# 可直接消费 CUDA 帧的滤镜（无需回传系统内存）
_CUDA_NATIVE_FILTERS = frozenset({
    'crop_cuda', 'scale_cuda', 'yadif_cuda', 'overlay_cuda', 'tonemap_cuda',
    'thumbnail_cuda', 'hwupload_cuda', 'hwdownload', 'hwupload',
})


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
    """按源位深选择 hwdownload 的下载格式（10bit 用 p010，12bit+ 用 p012）。"""
    return 'nv12' if src_bits <= 8 else ('p012' if src_bits >= 12 else 'p010')


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
) -> List[str]:
    """
    构建完整的 FFmpeg 命令列表。

    audio_codec:   音频编码器，'copy' 表示流复制；其他值触发重编码。
    audio_bitrate: 仅在音频重编码时生效，默认 '128k'。
    extra_args: 追加到输出文件名之前的自定义 FFmpeg 参数（已剥离 '--' 前缀）。
    """
    extra_args = extra_args or []
    if codec.lower() == 'copy' and vf_filter:
        # 视频滤镜与流复制互斥：与其让 ffmpeg 报难以定位的错误，不如在此明确失败
        raise ValueError(
            '使用视频滤镜时不能使用 -c:v copy；若仅需保留元数据请直接用 ffmpeg remux'
        )

    def _warn(msg: str) -> None:
        print(f'  ⚠ {msg}', file=sys.stderr)

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
    # 软件编码器（libx264 等）对输出端 -color_primaries/-color_trc 不写 VUI，
    # 需在滤镜链末尾追加 setparams；GPU 编码器（NVENC/AMF/QSV）靠输出端参数写 VUI，
    # 且 crop_cuda 全 GPU 流水线不应被 CPU 滤镜破坏，故仅软件编码器追加。
    color_args = build_color_args(input_file, ffmpeg_bin, meta=meta,
                                  color_range=color_range)
    _sp = _setparams_from_color_args(color_args)
    _is_sw_codec = codec.lower() not in CQ_SUPPORTED_CODECS and codec.lower() != 'copy'
    # 强制 --color-range tv|pc 且与源实际值域不同 → 自动做真正的像素值域转换。
    # 必须在 _prepend_hwdownload 之前判断链首滤镜是否为 CUDA 原生。
    _conv = build_range_convert_filter(
        meta, color_range,
        vf_first_filter=vf_filter.split(',', 1)[0].split('=', 1)[0].strip(),
        warn=_warn)
    if _conv:
        vf_filter = f'{vf_filter},{_conv}'
    if _sp and _is_sw_codec:
        vf_filter = f'{vf_filter},{_sp}'

    # 视频滤镜 & 编码器
    vf_filter = _prepend_hwdownload(vf_filter, hwaccel_output_format,
                                    src_bits, hw_download_fmt)
    # 映射了封面轨时不能用 -vf：它会作用到所有输出视频流，与封面的 -c:v:N copy 冲突
    cmd += ['-filter:v:0' if meta is not None else '-vf', vf_filter]
    cmd += ['-c:v', codec]

    # 质量参数（cq / crf 互斥，由 _resolve_quality_params 决定）
    if cq is not None and encoder_supports_cq(codec):
        cmd += ['-cq', str(cq)]
    elif crf is not None and encoder_supports_crf(codec):
        if codec in ('libvpx', 'libvpx-vp9'):
            # VP8/VP9 的 CRF 必须配合 -b:v 0 才是纯恒定质量，否则退化成
            # 受码率上限约束的 constrained quality。
            cmd += ['-b:v', '0']
        if codec == 'librav1e':
            # rav1e 不认 -crf（会被静默忽略），换算成等效 -qp
            cmd += ['-qp', str(crf_to_rav1e_qp(crf))]
        else:
            cmd += ['-crf', str(crf)]

    # libaom-av1 的速度档位：ffmpeg 默认 -cpu-used=1 慢到不可用（实测 320x240 仅 1fps），
    # 按资源自动取值；用户若已在 --extra-args 显式给过则尊重用户。
    if codec == 'libaom-av1' and '-cpu-used' not in extra_args:
        cmd += ['-cpu-used', str(auto_effort()[0])]

    # 编码器预设（已由调用方 normalize_preset 归一化，此处直接使用）
    if encoder_supports_preset(codec):
        cmd += ['-preset', preset]

    # [META-KEEP] 位深继承：10bit 源不再被降为 8bit。
    if meta is not None and src_bits >= 10:
        if codec.lower().endswith('_nvenc'):
            # NVENC 链路原先完全不传 -pix_fmt，交给 hw_frames_ctx 协商。但
            # h264_nvenc 只支持 8bit，喂 10bit 输入会让整条 GPU 策略以 rc=218 失败，
            # 结果退回 CPU 编码只为保住 10bit——得不偿失。故这里显式降 8bit 保住硬件加速。
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

    # [META-KEEP] HDR10 静态元数据（libx265 走 -x265-params，其余尽力而为）
    if meta is not None:
        cmd += build_hdr_args(meta, codec, warn=_warn)

    # mp4/mov 快速启动
    if output_file.suffix.lower() in ('.mp4', '.m4v', '.mov'):
        cmd += ['-movflags', '+faststart']

    # 自定义追加参数
    if extra_args:
        cmd += extra_args

    # [COLOR-FIX] 输出端色彩参数（写入容器 colr box / GPU 编码器 VUI）；
    # 置于 extra_args 之后，与主项目合并注入行为一致。
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


def _result(status: str, frames: int = 0, elapsed: float = 0.0) -> Dict[str, object]:
    """统一 process_file 的返回结构，供调用方汇总统计。

    status: 'done' / 'skipped' / 'failed' / 'dry-run'
    """
    return {'status': status, 'frames': frames, 'elapsed': elapsed}


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
    hw_mode: str,
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
    flag: Optional[str] = None,
    color_range: Optional[str] = None,
    crop_ratio: Optional[Tuple[int, int]] = None,
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
        flag            输出文件名后缀标记，None 时用默认 _cropped / _covered / _cropcovered
        crop_ratio      crop-cover 模式裁剪步骤所用比例 (分子, 分母)；None 时用目标宽高比
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
        suffix = flag if flag else {
            'cover': '_covered',
            'crop-cover': '_cropcovered',
        }.get(mode, '_cropped')
        output_file = output_dir / f'{input_file.stem}{suffix}{ext}'
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

    # ── 生成策略链 ──
    all_strategies = _generate_strategies(codec, hw_caps, hw_mode, mode=mode)

    # ── Dry-run 模式：打印最优策略命令后返回 ──
    if dry_run:
        strategy = all_strategies[0]
        try:
            vf_filter = build_video_filter(
                mode, actual_width, actual_height, out_width, out_height,
                use_cuda=strategy['use_hw_filter'], crop_ratio=crop_ratio,
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
        hwaccel          = strategy.get('hwaccel')
        hwaccel_out_fmt  = strategy.get('hwaccel_output_format')

        # 构建视频滤镜
        try:
            vf_filter = build_video_filter(
                mode, actual_width, actual_height, out_width, out_height,
                use_cuda=use_hw_filter, crop_ratio=crop_ratio,
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

        # [META-KEEP] 10bit 源 + hof=cuda 时 hwdownload 先试 p010；旧驱动或不支持
        # 10bit 下载的设备会失败，此时同一策略回退 nv12 再试一次（代价：降为 8bit、
        # HDR 帧级 side_data 一并丢失），而不是直接放弃整个 GPU 策略。
        _src_meta = probe_full_metadata(str(input_file), ffmpeg_bin)
        _src_bits = _src_meta['derived']['src_bits'] if _src_meta else 8
        _first_filter = vf_filter.split(',', 1)[0].split('=', 1)[0].strip()
        _dl_formats: List[Optional[str]] = [None]
        # 只有真正会插入 hwdownload,format=p010 时才值得回退重试；
        # crop_cuda 这类原生滤镜不产生 hwdownload，重试只会重复同一条命令。
        if (hwaccel_out_fmt == 'cuda' and _src_bits >= 10
                and _first_filter not in _CUDA_NATIVE_FILTERS):
            _dl_formats.append('nv12')

        rc, stderr_text = 1, ''
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
                preset=norm_preset,
                overwrite=overwrite,
                hwaccel=hwaccel,
                hwaccel_output_format=hwaccel_out_fmt,
                ffmpeg_bin=ffmpeg_bin,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                extra_args=extra_args,
                hw_download_fmt=_dl,
                color_range=color_range,
            )

            tag = '（降级）' if strategy.get('fallback', False) else ''
            print('  ' + _label('策略')
                  + f'[{i + 1}/{len(all_strategies)}] {strategy["name"]}{tag}')
            print('  ' + _label('执行命令') + shlex.join(cmd))

            rc, stderr_text = _run_with_progress(cmd, total_frames, queue_rest, queue_cur)

            # rc=0 不代表产物存在：ffmpeg 在少数静默错误下会以 0 退出却不写文件。
            # 直接 stat() 会抛 FileNotFoundError 中断整批处理，故显式判为策略失败。
            if rc == 0 and not output_file.exists():
                rc = 1
                stderr_text = (stderr_text or '') + \
                    '\nffmpeg 返回 0 但未生成输出文件'

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
                return _result('done', total_frames, elapsed)

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

    print('  ✘ 失败：所有策略均失败，放弃处理。', file=sys.stderr)
    return _result('failed')


# ═══════════════════════════════════════════════════════════════════
#  命令行解析与主入口
# ═══════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='批量裁剪视频，支持 NVIDIA CUDA 硬件加速及智能降级。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
硬件加速选项：
  --hwaccel auto   自动检测并启用 CUDA 组件（默认）
  --hwaccel cuda   强制启用 CUDA（若缺失组件则降级）
  --hwaccel none   禁用硬件加速，纯 CPU 处理

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
                             '--mode crop-cover 配合 --crop-ratio 时可只给一个维度）')
    parser.add_argument('--output-height', type=int, default=None,
                        help='目标视频高度（与 --crop-ratio 二选一；'
                             '--mode crop-cover 配合 --crop-ratio 时可只给一个维度）')
    parser.add_argument('--crop-ratio', type=str, default=None,
                        help='自动计算裁剪尺寸的目标宽高比，如 16:9 或 1.777'
                             '（与 --output-width/height 二选一；'
                             '--mode crop-cover 下两者并用，前者定裁剪比例、后者定最终尺寸）')

    # 处理模式
    parser.add_argument('--mode', choices=['crop', 'cover', 'crop-cover'], default='crop',
                        help='处理模式：crop=居中裁剪（默认，目标不得大于源）；'
                             'cover=等比缩放覆盖后居中裁剪（任意尺寸）；'
                             'crop-cover=先按 --crop-ratio（未给出时用目标宽高比）'
                             '最大化裁剪，再缩放覆盖到 --output-width/height')

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
    parser.add_argument('--preset', default=None,
                        help='编码器预设。默认：CPU 编码器 medium，GPU 编码器 p5；'
                             'NVENC（p1~p7）与 libx264 风格（ultrafast~veryslow）自动双向映射')
    parser.add_argument('--flag', default=None, metavar='SUFFIX',
                        help='输出文件名后缀标记，用于替代默认的 _cropped / _covered / '
                             '_cropcovered。'
                             '例：--flag "_Croped" → abc.mp4 输出为 abc_Croped.mp4。'
                             '仅对工具自动生成的输出名生效（批量模式或 --output 为目录）；'
                             '--output 指定了完整文件名时不改动。')

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

    # 硬件加速
    parser.add_argument('--hwaccel',
                        choices=['auto', 'cuda', 'vulkan', 'vaapi', 'opencl', 'none'],
                        default='auto',
                        help='硬件加速模式（默认 auto）')
    parser.add_argument('--ffmpeg-bin', default='ffmpeg',
                        help='FFmpeg 可执行文件路径（默认 ffmpeg）')
    parser.add_argument('--cuda-diagnostics', action='store_true',
                        help='启用 CUDA 诊断模式：输出多分辨率探针结果及详细错误信息，'
                             '帮助定位「硬解不可用」的根本原因（如驱动缺失、'
                             '库路径问题、容器设备映射缺失等）')
    parser.add_argument('--cuda-device-id', type=int, default=0,
                        help='CUDA 设备 ID（默认 0），用于多 GPU 环境选择解码设备')
    parser.add_argument('--fallback-policy',
                        choices=['auto', 'strict-cuda', 'nvenc-only', 'cpu-only'],
                        default='auto',
                        help='策略降级控制（默认 auto）：'
                             'auto=完整策略链；strict-cuda=无 CUDA 则退出；'
                             'nvenc-only=仅用 NVENC，跳过硬解；cpu-only=纯 CPU')

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

    # crop-cover + --crop-ratio：只给了一个维度时，按裁剪比例补全另一个。
    # 比例即最终画面比例（裁剪后按比例缩放覆盖，不产生黑边或额外裁剪），
    # 故 width : height == crop_ratio_num : crop_ratio_den。
    if is_crop_cover and has_crop_ratio and not has_explicit_size:
        if args.output_width is None:
            args.output_width = derive_even_dimension(
                args.output_height * crop_ratio_num / crop_ratio_den)
        else:
            args.output_height = derive_even_dimension(
                args.output_width * crop_ratio_den / crop_ratio_num)
        print(f'提示：--mode crop-cover 仅给了一个维度，已按裁剪比例 '
              f'{crop_ratio_num}:{crop_ratio_den} 补全为 '
              f'{args.output_width}x{args.output_height}。')

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

    # 探测硬件能力（传入诊断模式与设备 ID）
    if args.hwaccel == 'none':
        hw_caps = HardwareCapabilities()
        print('硬件加速已禁用，使用纯 CPU 处理。')
    else:
        # 新增：根据 --fallback-policy 调整探测行为
        if args.fallback_policy == 'cpu-only':
            hw_caps = HardwareCapabilities()
            print('已指定纯 CPU 路径 (--fallback-policy cpu-only)，跳过 GPU 探测。')
        elif args.fallback_policy == 'nvenc-only':
            # 仅探测 NVENC 编码能力，不尝试硬解
            print('已指定 NVENC-only 路径 (--fallback-policy nvenc-only)，仅探测编码器。')
            hw_caps = HardwareCapabilities()
            hw_caps.has_encoder_h264 = _check_nvenc_available(ffmpeg_bin, 'h264_nvenc')
            hw_caps.has_encoder_hevc = _check_nvenc_available(ffmpeg_bin, 'hevc_nvenc')
            hw_caps.has_encoder_av1 = _check_nvenc_available(ffmpeg_bin, 'av1_nvenc')
            hw_caps._mark_detected('has_encoder_h264')
            hw_caps._mark_detected('has_encoder_hevc')
            hw_caps._mark_detected('has_encoder_av1')
            # 对 NVENC-only 模式，不尝试 CUDA 解码（避免环境缺陷导致不必要失败）
            print(f'硬件能力总结：NVENC h264={"✓" if hw_caps.has_encoder_h264 else "✗"}, '
                  f'hevc={"✓" if hw_caps.has_encoder_hevc else "✗"}, '
                  f'av1={"✓" if hw_caps.has_encoder_av1 else "✗"}')
        elif args.fallback_policy == 'strict-cuda':
            # 强制 CUDA：若检测失败直接退出（不尝试其他策略）
            print('已指定 strict-cuda 路径：若 CUDA 不可用则直接退出，不尝试降级。')
            hw_caps = detect_cuda_capabilities(
                ffmpeg_bin, args.hwaccel,
                diagnostics=args.cuda_diagnostics,
                cuda_device_id=args.cuda_device_id,
            )
            if args.cuda_diagnostics:
                print(f'  [诊断] CUDA 解码状态: {"可用" if hw_caps.has_decoder else "不可用"}')
        else:
            # 默认 auto 模式
            hw_caps = detect_cuda_capabilities(
                ffmpeg_bin, args.hwaccel,
                diagnostics=args.cuda_diagnostics,
                cuda_device_id=args.cuda_device_id,
            )
            # 详细结果已由 detect_cuda_capabilities 逐项打印；汇总行统一放在
            # 概览块的「硬件加速」字段，避免与逐项输出重复。

        if args.hwaccel == 'cuda' and args.fallback_policy != 'strict-cuda':
            # 当未强制 strict-cuda 时：仅当完全无 CUDA 组件才退出；否则正确降级
            if not hw_caps.has_decoder and not hw_caps.has_any_encoder():
                print('错误：强制启用 CUDA 但未检测到任何可用的 CUDA 组件。', file=sys.stderr)
                if args.cuda_diagnostics:
                    print('  提示：使用 --cuda-diagnostics 查看详细环境诊断信息。', file=sys.stderr)
                return 1

        # 新增：保存探测结果到缓存（仅在非诊断模式下快速参考）
        # ffmpeg_version_hint 只做记录、没有任何读取方，故不再为此 fork 一次
        # `ffmpeg -version`；这里沿用原先写入的编码器路径。
        if args.hwaccel != 'none' and args.fallback_policy != 'cpu-only':
            try:
                _save_hw_cache(hw_caps, ffmpeg_version=ffmpeg_bin)
            except Exception:
                pass  # 缓存写入失败不影响主流程

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

    if args.flag and not batch_mode and output_path.suffix:
        print('提示：--output 已指定完整文件名，--flag 不生效。', file=sys.stderr)

    input_root = input_path if input_path.is_dir() else input_path.parent

    # ── 打印任务概览（与 vidcrop_cpu_v2.py 对齐的双分割线结构化块）──
    mode_label = {
        'cover': 'cover（等比缩放+裁剪）',
        'crop-cover': 'crop-cover（先裁剪后缩放覆盖）',
    }.get(args.mode, 'crop（居中裁剪）')
    print(_SEP)
    print(_label('待处理文件') + f'{len(video_files)} 个')
    if has_crop_ratio:
        # crop-cover 下 --crop-ratio 定的是裁剪比例，最终尺寸另由 --output-* 给出
        _tail = (f'  最终尺寸: {args.output_width}x{args.output_height}'
                 if is_crop_cover else '')
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
    # 概览块展示**实际会生效**的编码器：直接取策略链的第一条，这样
    #   · --codec auto 会被解析成具体编码器（h264_nvenc 或 libx264），不再显示 "auto"；
    #   · 请求的 NVENC 编码器不可用时也已反映为 CPU 编码器（见 _get_software_fallback）。
    # 概览块整批只打印一次，策略层那次才逐文件重复。
    _effective_codec = str(_generate_strategies(
        args.codec, hw_caps, args.hwaccel, mode=args.mode)[0]['codec'])
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
    # 只在"用户点名要的 NVENC 编码器"被换掉时才提示；--codec auto 解析出的具体
    # 编码器属正常自适应，不是降级。
    if args.codec in NVENC_CODECS and _effective_codec != args.codec:
        _why = ('已禁用 GPU 处理（--hwaccel none / --fallback-policy cpu-only）'
                if args.hwaccel == 'none' or args.fallback_policy == 'cpu-only'
                else '当前环境未检测到该 NVENC 编码器')
        print(_label('降级提示')
              + f'{args.codec} 不可用（{_why}），实际将改用 CPU 编码器 {_effective_codec}。')
    print(_label('音频') + args.audio_codec
          + (f' @ {args.audio_bitrate}' if args.audio_codec.lower() != 'copy' else ''))
    if args.color_range != 'auto':
        print(_label('color_range') + args.color_range + '（必要时自动做值域转换）')
    if extra_args:
        print(_label('额外参数') + shlex.join(extra_args))
    if args.hwaccel == 'none':
        print(_label('硬件加速') + '已禁用（--hwaccel none）')
    else:
        print(_label('硬件加速') + (hw_caps.summary(only_detected=True) or '无可用加速组件'))
    print(_label('运行模式') + '顺序执行（细粒度实时进度条）')
    if args.dry_run:
        print(_SEP)
        print('DRY-RUN 模式：将仅显示命令，不执行转码。\n')
    else:
        print(_SEP)

    # 批量处理
    done_count = skipped_count = failed_count = 0
    peak_fps = 0.0
    sum_frames = 0
    sum_enc_elapsed = 0.0

    sizes = [_file_bytes(vf) for vf in video_files]
    queue = QueueETA(sizes) if len(video_files) > 1 else None

    try:
        for idx, vf in enumerate(video_files, start=1):
            if _STOP_REQUESTED.is_set():
                break

            # crop-ratio 在 crop / cover 模式下决定输出尺寸；crop-cover 模式下它
            # 只决定裁剪步骤的比例，最终尺寸固定为 --output-width/height。
            if has_crop_ratio and not is_crop_cover:
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
                hw_mode=args.hwaccel,
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
                flag=args.flag,
                color_range=args.color_range,
                crop_ratio=(crop_ratio_num, crop_ratio_den) if has_crop_ratio else None,
                queue_rest=q_rest,
                queue_cur=q_cur,
            )
            st = res['status']
            if st == 'done':
                done_count += 1
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
    print(_SEP)

    if _STOP_REQUESTED.is_set():
        return 130

    return 0 if failed_count == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
