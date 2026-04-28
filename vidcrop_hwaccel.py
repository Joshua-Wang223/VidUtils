#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vidcrop_hwaccel.py — 基于 FFmpeg 的视频批量裁剪工具（硬件加速版）

功能概述
────────────────────────────────────────────────────────────────────
  • 单文件 / 文件夹批量处理；-r 递归扫描子目录并保持目录结构
  • 两种处理模式：
      - crop （默认）：直接居中裁剪（目标尺寸不得大于源尺寸）
      - cover：等比缩放至完全覆盖目标区域后居中裁剪（任意尺寸）
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

        注：cover 模式因需要 scale 步骤，自动跳过策略 1（crop_cuda 不支持缩放）。

编码器智能处理
────────────────────────────────────────────────────────────────────
  • 名称别名自动归一化：h265_nvenc→hevc_nvenc, x264→libx264, x265→libx265 …
  • preset 在 NVENC（p1~p7）与 libx264（ultrafast~veryslow）之间双向映射
  • CPU 编码器使用 -crf（默认 17），GPU 编码器使用 -cq（默认 16）
  • 降级时 --cq 通过 cq_to_crf() 做等效视觉质量映射，而非直接透传

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
from typing import Dict, List, Optional, Set, Tuple

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
    'libaom-av1': '.mp4',
    'librav1e':   '.mp4',
    'prores':     '.mov',
    'prores_ks':  '.mov',
    'mpeg4':      '.mp4',
    'libxvid':    '.avi',
    'mjpeg':      '.avi',
    'copy':       None,
}

# 支持 -preset 的编码器集合
PRESET_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'h264_nvenc', 'hevc_nvenc',
    'h264_amf',   'hevc_amf',
    'h264_qsv',   'hevc_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox',
}

# 支持 -crf 的编码器集合（CPU 软件编码器）
CRF_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'libvpx-vp9', 'libvpx',
    'libaom-av1', 'librav1e',
}

# 支持 -cq 的编码器集合（GPU 硬件编码器）
CQ_SUPPORTED_CODECS = {
    'h264_nvenc', 'hevc_nvenc',
    'h264_amf',   'hevc_amf',
    'h264_qsv',   'hevc_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox',
}

# NVENC preset ↔ libx264 preset 双向映射表
NVENC_TO_X264_PRESET = {
    'p1': 'ultrafast',
    'p2': 'superfast',
    'p3': 'veryfast',
    'p4': 'medium',
    'p5': 'slow',
    'p6': 'slower',
    'p7': 'veryslow',
}

DEFAULT_CRF = 17
DEFAULT_CQ  = 16

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
#  硬件能力检测
# ═══════════════════════════════════════════════════════════════════

class HardwareCapabilities:
    """硬件加速能力检测结果（基于运行时探测）。"""

    def __init__(self):
        self.has_decoder       = False
        self.has_encoder_h264  = False
        self.has_encoder_hevc  = False
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
        return False

    def has_any_encoder(self) -> bool:
        return self.has_encoder_h264 or self.has_encoder_hevc

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
    """运行时探测 NVENC 编码器：启动 1 帧 64×64 null 编码任务，捕获特征错误串。"""
    test_cmd = [
        ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'nullsrc=s=64x64:d=0.04:r=25',
        '-frames:v', '1', '-c:v', codec, '-f', 'null', '-',
    ]
    try:
        r = subprocess.run(test_cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, timeout=15)
        if r.returncode != 0:
            err = r.stderr.lower()
            nvenc_errors = [
                'openencodesessionex failed', 'no capable devices found',
                'unsupported device', 'cannot load libnvidia-encode',
                'error while opening encoder', 'no nvenc capable devices',
                'unknown encoder', 'encoder.*not found',
            ]
            if any(kw in err for kw in nvenc_errors):
                return False
        return True
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _check_cuda_decoder_available(ffmpeg_bin: str = 'ffmpeg') -> bool:
    """运行时探测 CUDA 硬件解码：生成微型 H.264 流后以 -hwaccel cuda 解码。"""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)
        gen = [
            ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc=s=64x64:d=0.1:r=25',
            '-c:v', 'libx264', '-frames:v', '2', tmp_path,
        ]
        r = subprocess.run(gen, capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not os.path.exists(tmp_path):
            return False
        test = [
            ffmpeg_bin, '-y', '-hide_banner',
            '-hwaccel', 'cuda', '-hwaccel_device', '0',
            '-i', tmp_path, '-frames:v', '1', '-f', 'null', '-',
        ]
        r2 = subprocess.run(test, capture_output=True, text=True, timeout=15)
        err = r2.stderr.lower()
        cuda_errors = [
            'cannot load libnvcuvid', 'failed loading nvcuvid',
            'hwaccel initialisation returned error', 'no cuda capable devices',
            'does not support device type cuda', 'cuda_error_no_device',
        ]
        return not any(e in err for e in cuda_errors)
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _check_hwaccel_available(ffmpeg_bin: str = 'ffmpeg',
                              hwaccel_type: str = 'cuda') -> bool:
    """运行时探测指定硬件加速器（Vulkan / VA-API / OpenCL 等）。"""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)
        gen = [
            ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc=s=64x64:d=0.1:r=25',
            '-c:v', 'libx264', '-frames:v', '2', tmp_path,
        ]
        r = subprocess.run(gen, capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not os.path.exists(tmp_path):
            return False
        test = [
            ffmpeg_bin, '-y', '-hide_banner',
            '-hwaccel', hwaccel_type,
            '-i', tmp_path, '-frames:v', '1', '-f', 'null', '-',
        ]
        r2 = subprocess.run(test, capture_output=True, text=True, timeout=15)
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


def detect_cuda_capabilities(ffmpeg_bin: str = 'ffmpeg',
                              hwaccel: Optional[str] = None) -> HardwareCapabilities:
    """运行时全面探测硬件加速能力，根据 --hwaccel 选择性探测。"""
    caps = HardwareCapabilities()
    if hwaccel == 'none':
        return caps

    print('正在检测硬件加速能力...')
    detect_all   = hwaccel in (None, 'auto')
    detect_cuda  = detect_all or hwaccel == 'cuda'
    detect_vulkan = detect_all or hwaccel == 'vulkan'
    detect_vaapi = detect_all or hwaccel == 'vaapi'
    detect_opencl = detect_all or hwaccel == 'opencl'

    if detect_cuda:
        print('  CUDA 解码:  ', end='', flush=True)
        caps.has_decoder = _check_cuda_decoder_available(ffmpeg_bin)
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

        try:
            r = subprocess.run(
                [ffmpeg_bin, '-hide_banner', '-filters'],
                capture_output=True, text=True, timeout=10,
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


def normalize_preset(preset: str, target_codec: str) -> str:
    """在 NVENC（p1~p7）与 libx264 风格（ultrafast~veryslow）之间自动双向映射。"""
    if target_codec in ('h264_nvenc', 'hevc_nvenc'):
        if preset.startswith('p') and preset[1:].isdigit():
            return preset
        rev = {v: k for k, v in NVENC_TO_X264_PRESET.items()}
        if preset in rev:
            mapped = rev[preset]
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
                  f"（{target_codec} 使用 NVENC 风格 preset）。")
            return mapped
        print(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，使用默认 'p4'。")
        return 'p4'

    if target_codec in ('libx264', 'libx265'):
        if preset.startswith('p') and preset[1:].isdigit():
            mapped = NVENC_TO_X264_PRESET.get(preset, 'medium')
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
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
        return any(x in cod_l for x in ['264', '265', 'hevc', 'av1', 'rave1', 'mpeg4'])
    if ext_l == '.webm':
        return any(x in cod_l for x in ['vpx', 'vp8', 'vp9', 'av1'])
    if ext_l == '.mov':
        return any(x in cod_l for x in ['prores', '264', '265', 'hevc'])
    if ext_l == '.avi':
        return any(x in cod_l for x in ['xvid', 'mpeg4', 'mjpeg'])
    return True


def encoder_supports_preset(codec: str) -> bool:
    return codec in PRESET_SUPPORTED_CODECS


def encoder_supports_crf(codec: str) -> bool:
    return codec in CRF_SUPPORTED_CODECS


def encoder_supports_cq(codec: str) -> bool:
    return codec in CQ_SUPPORTED_CODECS


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


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


# ═══════════════════════════════════════════════════════════════════
#  ffprobe 探测
# ═══════════════════════════════════════════════════════════════════

def get_video_dimensions(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Tuple[int, int]:
    """通过 ffprobe 获取视频的宽度和高度。"""
    ffprobe_bin = _get_ffprobe_bin(ffmpeg_bin)
    cmd = [
        ffprobe_bin, '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'json', filepath,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if 'corrupt' in r.stderr or 'conceal' in r.stderr:
            print(f'  警告：源文件可能包含损坏数据。')
        data = json.loads(r.stdout)
        s = data['streams'][0]
        return int(s['width']), int(s['height'])
    except Exception as exc:
        print(f'错误：无法获取视频尺寸 {filepath} - {exc}', file=sys.stderr)
        raise


def _get_total_frames(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Optional[int]:
    """
    探测视频总帧数。
    优先读取 nb_frames；不可用时按 duration × fps 估算；失败则返回 None。
    """
    ffprobe_bin = _get_ffprobe_bin(ffmpeg_bin)
    cmd = [
        ffprobe_bin, '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_frames,duration,r_frame_rate:format=duration',
        '-of', 'json', filepath,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
        streams = data.get('streams') or []
        if not streams:
            return None
        s = streams[0]
        nb = s.get('nb_frames', '')
        if nb and nb not in ('N/A', ''):
            return max(1, int(nb))
        # 降级：duration × fps
        duration = 0.0
        for c in (s.get('duration'), (data.get('format') or {}).get('duration')):
            try:
                duration = float(c or 0)
                if duration > 0:
                    break
            except Exception:
                pass
        rate = s.get('r_frame_rate', '0/1')
        num_s, _, den_s = rate.partition('/')
        try:
            fps = float(num_s) / float(den_s) if float(den_s) > 0 else 0.0
        except Exception:
            fps = 0.0
        if duration > 0 and fps > 0:
            return max(1, int(duration * fps))
    except Exception:
        pass
    return None


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


def build_video_filter(mode: str, src_w: int, src_h: int, dst_w: int, dst_h: int,
                       use_cuda: bool = False) -> str:
    """
    根据 mode 生成对应的 FFmpeg 视频滤镜字符串。

    mode='crop': 直接居中裁剪，use_cuda=True 时使用 crop_cuda（全 GPU 流水线专用）。
    mode='cover': 等比缩放+裁剪，始终使用 CPU 侧滤镜（忽略 use_cuda 参数）。
                  cover 需要 scale 步骤，crop_cuda 不支持，调用方已在策略生成阶段
                  排除了全 GPU 流水线（策略 1），此处 use_cuda 永远为 False。
    """
    if mode == 'cover':
        return _build_cover_filter_str(src_w, src_h, dst_w, dst_h)
    return _build_crop_filter_str(src_w, src_h, dst_w, dst_h, use_cuda=use_cuda)


# ═══════════════════════════════════════════════════════════════════
#  质量参数解析
# ═══════════════════════════════════════════════════════════════════

def cq_to_crf(cq: int, target_codec: str) -> int:
    """
    将 NVENC CQ 值映射到软件编码器等效 CRF 值。
    直接透传 CQ 会导致软件编码器质量偏高、文件偏大，因此做等效视觉质量修正：
      hevc_nvenc → libx265 : crf = cq + 4
      h264_nvenc → libx264 : crf = cq + 1
      其他编码器            : 直接返回 cq（未知编码器，不做猜测）
    """
    if target_codec == 'libx265':
        return min(51, cq + 4)
    elif target_codec == 'libx264':
        return min(51, cq + 1)
    return cq


def _resolve_quality_params(
    codec: str,
    user_crf: Optional[int],
    user_cq: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    """
    根据编码器类型确定最终 (crf, cq) 值，处理参数不匹配和降级映射。
    GPU 编码器优先使用 user_cq，CPU 编码器优先使用 user_crf。
    降级场景（用户只给了 --cq 但实际落到 CPU 编码器）通过 cq_to_crf() 映射。
    """
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
            mapped_crf = cq_to_crf(user_cq, codec)
            print(f'  提示：编码器 {codec} 不支持 -cq，'
                  f'已将 --cq {user_cq} 映射为 -crf {mapped_crf}（等效视觉质量）。')
            return mapped_crf, None
        return DEFAULT_CRF, None

    return None, None


# ═══════════════════════════════════════════════════════════════════
#  策略生成
# ═══════════════════════════════════════════════════════════════════

def _get_software_fallback(codec: str) -> str:
    if codec in ('hevc_nvenc', 'hevc_amf', 'hevc_qsv', 'libx265'):
        return 'libx265'
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

    is_nvenc       = preferred_codec in ('h264_nvenc', 'hevc_nvenc')
    nvenc_available = hw_caps.has_nvenc(preferred_codec) if is_nvenc else False
    sw_fallback    = _get_software_fallback(preferred_codec)

    # 用户显式指定非 NVENC 的具体编码器，软件策略沿用该编码器
    sw_codec = user_codec if (not is_nvenc and user_codec != 'auto') else sw_fallback

    # ── 策略 1：全 GPU 流水线（硬解 + crop_cuda + NVENC 编码）──
    # cover 模式需要 scale 步骤，crop_cuda 不支持，此策略仅在 crop 模式下可用。
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
) -> List[str]:
    """
    构建完整的 FFmpeg 命令列表。

    audio_codec:   音频编码器，'copy' 表示流复制；其他值触发重编码。
    audio_bitrate: 仅在音频重编码时生效，默认 '128k'。
    extra_args:    追加到输出文件名之前的自定义 FFmpeg 参数（已剥离 '--' 前缀）。
    """
    cmd = [ffmpeg_bin, '-hide_banner', '-loglevel', 'warning']
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

    # 流选择：视频主流 + 可选音频流
    cmd += ['-map', '0:v:0', '-map', '0:a?']

    # 视频滤镜 & 编码器
    cmd += ['-vf', vf_filter]
    cmd += ['-c:v', codec]

    # 质量参数（cq / crf 互斥，由 _resolve_quality_params 决定）
    if cq is not None and encoder_supports_cq(codec):
        cmd += ['-cq', str(cq)]
    elif crf is not None and encoder_supports_crf(codec):
        if codec in ('libvpx', 'libvpx-vp9'):
            cmd += ['-b:v', '0']
        cmd += ['-crf', str(crf)]

    # 编码器预设（已由调用方 normalize_preset 归一化，此处直接使用）
    if encoder_supports_preset(codec):
        cmd += ['-preset', preset]

    # 音频
    if audio_codec.lower() == 'copy':
        cmd += ['-c:a', 'copy']
    else:
        cmd += ['-c:a', audio_codec]
        if audio_bitrate:
            cmd += ['-b:a', audio_bitrate]

    # mp4/mov 快速启动
    if output_file.suffix.lower() in ('.mp4', '.m4v', '.mov'):
        cmd += ['-movflags', '+faststart']

    # 自定义追加参数
    if extra_args:
        cmd += extra_args

    cmd += [str(output_file)]
    return cmd


# ═══════════════════════════════════════════════════════════════════
#  进度条执行
# ═══════════════════════════════════════════════════════════════════

def _run_with_progress(
    cmd: List[str],
    total_frames: Optional[int],
) -> Tuple[int, str]:
    """
    执行 FFmpeg 命令并在终端显示实时进度条。
    通过 -progress pipe:1 -nostats 获取结构化进度流；
    异步线程收集 stderr，失败时返回完整错误文本。
    进程注册到 _ACTIVE_PROCS 以支持 Ctrl+C 安全中断。
    """
    prog_cmd = list(cmd)
    try:
        i_idx = prog_cmd.index('-i')
        prog_cmd[i_idx:i_idx] = ['-progress', 'pipe:1', '-nostats']
    except ValueError:
        prog_cmd += ['-progress', 'pipe:1', '-nostats']

    term_w = shutil.get_terminal_size((80, 24)).columns
    bar_w  = max(10, min(30, term_w - 52))
    t0     = time.perf_counter()
    frame  = 0
    stderr_lines: List[str] = []

    try:
        proc = subprocess.Popen(
            prog_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _register_proc(proc)

        def _drain_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line)

        t_stderr = threading.Thread(target=_drain_stderr, daemon=True)
        t_stderr.start()

        for raw_line in proc.stdout:
            if _STOP_REQUESTED.is_set():
                proc.terminate()
                break
            line = raw_line.strip()
            if '=' not in line:
                continue
            key, _, val = line.partition('=')
            if key.strip() != 'frame':
                continue
            try:
                frame = int(val.strip())
            except ValueError:
                continue

            elapsed = time.perf_counter() - t0
            fps     = frame / elapsed if elapsed > 0 else 0

            if total_frames and total_frames > 0:
                pct    = min(frame / total_frames, 1.0)
                filled = int(bar_w * pct)
                bar    = '█' * filled + '░' * (bar_w - filled)
                eta    = (total_frames - frame) / fps if fps > 0 else 0
                print(
                    f'\r  [{bar}] {pct*100:5.1f}%'
                    f'  {frame}/{total_frames}帧'
                    f'  {fps:5.1f}fps'
                    f'  ETA {eta:.0f}s   ',
                    end='', flush=True,
                )
            else:
                print(
                    f'\r  已处理 {frame} 帧  {fps:.1f}fps  {elapsed:.1f}s   ',
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
    ffmpeg_bin: str = 'ffmpeg',
    mode: str = 'crop',
    audio_codec: str = 'copy',
    audio_bitrate: str = '128k',
    extra_args: Optional[List[str]] = None,
    no_skip_same_size: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    处理单个视频文件，支持动态策略降级。

    新增参数：
        mode            'crop'（居中裁剪）或 'cover'（等比缩放+裁剪）
        audio_codec     音频编码器，'copy' 流复制，其他值重编码
        audio_bitrate   音频重编码码率
        extra_args      追加到 FFmpeg 命令末尾的自定义参数列表
        no_skip_same_size  True 时即使尺寸相同也强制转码
        dry_run         True 时仅打印最优策略命令，不实际执行
    """
    extra_args = extra_args or []

    # ── 确定原始尺寸 ──
    if orig_width is None or orig_height is None:
        try:
            actual_width, actual_height = get_video_dimensions(
                str(input_file), ffmpeg_bin
            )
        except Exception:
            return False
    else:
        actual_width, actual_height = orig_width, orig_height

    t_file_start = time.perf_counter()
    print(f'\n处理文件：{input_file}')
    mode_label = 'cover（等比缩放+裁剪）' if mode == 'cover' else 'crop（居中裁剪）'
    print(f'  原始尺寸: {actual_width}x{actual_height}  模式: {mode_label}  目标: {out_width}x{out_height}')

    # ── 同尺寸跳过（可通过 --no-skip-same-size 关闭）──
    if actual_width == out_width and actual_height == out_height and not no_skip_same_size:
        print('  跳过：目标尺寸与原始尺寸相同（--no-skip-same-size 可强制转码）。')
        return True

    # ── crop 模式下目标不能大于源（cover 模式无此限制）──
    if mode == 'crop':
        if out_width > actual_width or out_height > actual_height:
            print(
                f'  跳过：crop 模式下目标尺寸 ({out_width}x{out_height}) '
                f'大于原始尺寸 ({actual_width}x{actual_height})',
                file=sys.stderr,
            )
            return False

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
        suffix = '_covered' if mode == 'cover' else '_cropped'
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
        print(f'  输出文件已存在，跳过（使用 --overwrite 覆盖）：{output_file}')
        return True

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
                use_cuda=strategy['use_hw_filter'],
            )
        except ValueError as exc:
            print(f'  ✗ 滤镜构建失败：{exc}', file=sys.stderr)
            return False

        current_crf, current_cq = _resolve_quality_params(
            strategy['codec'], crf, cq
        )
        norm_preset = normalize_preset(preset, strategy['codec'])
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
        )
        print(f'▶ {input_file.name} → {output_file.name}')
        print(f'  策略: {strategy["name"]}')
        print(f'  命令: {shlex.join(cmd)}\n')
        return True

    # ── 依次尝试策略链 ──
    for i, strategy in enumerate(all_strategies):
        if _STOP_REQUESTED.is_set():
            return False

        current_codec    = strategy['codec']
        use_hw_filter    = strategy['use_hw_filter']
        hwaccel          = strategy.get('hwaccel')
        hwaccel_out_fmt  = strategy.get('hwaccel_output_format')

        # 构建视频滤镜
        try:
            vf_filter = build_video_filter(
                mode, actual_width, actual_height, out_width, out_height,
                use_cuda=use_hw_filter,
            )
        except ValueError as exc:
            print(f'  跳过：{exc}', file=sys.stderr)
            return False

        current_crf, current_cq = _resolve_quality_params(current_codec, crf, cq)
        norm_preset = normalize_preset(preset, current_codec)

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
        )

        tag = '（降级）' if strategy.get('fallback', False) else ''
        print(f'  策略 [{i + 1}/{len(all_strategies)}]: {strategy["name"]}{tag}')
        print(f'  命令：{shlex.join(cmd)}')

        rc, stderr_text = _run_with_progress(cmd, total_frames)

        if rc == 0:
            elapsed  = time.perf_counter() - t_file_start
            in_size  = input_file.stat().st_size
            out_size = output_file.stat().st_size
            ratio    = (1.0 - out_size / in_size) * 100 if in_size > 0 else 0.0
            direction = '↓' if ratio >= 0 else '↑'
            print(f'  ✓ 完成：{output_file}')
            print(
                f'    大小：{_fmt_size(in_size)} → {_fmt_size(out_size)}'
                f'（{direction}{abs(ratio):.1f}%）  耗时：{_fmt_duration(elapsed)}'
            )
            return True

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

    print('  ✗ 所有策略均失败，放弃处理。', file=sys.stderr)
    return False


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
  --mode crop      直接居中裁剪（默认，目标尺寸不能大于源尺寸）
  --mode cover     等比缩放至完全覆盖目标区域后居中裁剪（任意目标尺寸）

质量参数：
  --crf  CPU 编码器（libx264/265 等），默认 17，0-51 越小越好
  --cq   GPU 编码器（NVENC/AMF 等），默认 16，0-51 越小越好
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
    parser.add_argument('--output-width',  type=int, required=True,
                        help='目标视频宽度')
    parser.add_argument('--output-height', type=int, required=True,
                        help='目标视频高度')

    # 处理模式
    parser.add_argument('--mode', choices=['crop', 'cover'], default='crop',
                        help='处理模式：crop=居中裁剪（默认），cover=等比缩放+居中裁剪')

    # 视频编码
    parser.add_argument('--codec', default='libx264',
                        help='视频编码器（默认 libx264，可用 auto；支持别名）')
    parser.add_argument('--crf', type=int, default=None,
                        help='CRF 质量值（CPU 编码器，0-51，不指定时默认 17）')
    parser.add_argument('--cq',  type=int, default=None,
                        help='CQ 质量值（GPU 编码器，0-51，不指定时默认 16）')
    parser.add_argument('--preset', default='slow',
                        help='编码器预设（默认 slow，NVENC/x264 风格自动转换）')

    # 音频编码
    parser.add_argument('--audio-codec', default='copy',
                        help='音频编码器（默认 copy 流复制，可改为 aac / libopus 等）')
    parser.add_argument('--audio-bitrate', default='128k',
                        help='音频重编码码率（仅 --audio-codec 非 copy 时生效，默认 128k）')

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

    # 验证输出尺寸
    if args.output_width <= 0 or args.output_height <= 0:
        print('[ERROR] --output-width 和 --output-height 必须为正整数。', file=sys.stderr)
        return 2

    if args.crf is not None and not (0 <= args.crf <= 63):
        print('[ERROR] --crf 建议范围为 0-63。', file=sys.stderr)
        return 2

    if args.cq is not None and not (0 <= args.cq <= 63):
        print('[ERROR] --cq 建议范围为 0-63。', file=sys.stderr)
        return 2

    # 归一化编码器名称
    args.codec = normalize_codec_name(args.codec)

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

    # 探测硬件能力
    if args.hwaccel == 'none':
        hw_caps = HardwareCapabilities()
        print('硬件加速已禁用，使用纯 CPU 处理。')
    else:
        hw_caps = detect_cuda_capabilities(ffmpeg_bin, args.hwaccel)
        print(f'硬件能力总结：{hw_caps.summary(only_detected=True)}')

        if args.hwaccel == 'cuda':
            if not hw_caps.has_decoder and not hw_caps.has_any_encoder():
                print('错误：强制启用 CUDA 但未检测到任何可用的 CUDA 组件。', file=sys.stderr)
                return 1

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

    input_root = input_path if input_path.is_dir() else input_path.parent

    # 打印任务概览
    mode_label = 'cover（等比缩放+裁剪）' if args.mode == 'cover' else 'crop（居中裁剪）'
    print(f'\n共找到 {len(video_files)} 个视频文件。')
    print(f'处理模式    : {mode_label}  目标尺寸: {args.output_width}x{args.output_height}')
    print(f'编码器      : {args.codec}  preset: {args.preset}  '
          f'CRF: {args.crf if args.crf is not None else "默认"}  '
          f'CQ: {args.cq if args.cq is not None else "默认"}')
    print(f'音频        : {args.audio_codec}'
          + (f' @ {args.audio_bitrate}' if args.audio_codec.lower() != 'copy' else ''))
    if extra_args:
        print(f'额外参数    : {shlex.join(extra_args)}')
    if args.dry_run:
        print('─' * 64)
        print('DRY-RUN 模式：将仅显示命令，不执行转码。\n')

    # 批量处理
    t_main_start  = time.perf_counter()
    success_count = 0

    try:
        for vf in video_files:
            if _STOP_REQUESTED.is_set():
                break

            ok = process_file(
                input_file=vf,
                output_path=output_path,
                orig_width=args.original_width,
                orig_height=args.original_height,
                out_width=args.output_width,
                out_height=args.output_height,
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
                ffmpeg_bin=ffmpeg_bin,
                mode=args.mode,
                audio_codec=args.audio_codec,
                audio_bitrate=args.audio_bitrate,
                extra_args=extra_args,
                no_skip_same_size=args.no_skip_same_size,
                dry_run=args.dry_run,
            )
            if ok:
                success_count += 1

    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        print('\n[INFO] 已中断。', file=sys.stderr)
        return 130

    total_elapsed = time.perf_counter() - t_main_start

    if args.dry_run:
        print('─' * 64)
        print(f'DRY-RUN 完成：共预览 {len(video_files)} 个文件的命令，未执行任何转码。')
        return 0

    print(
        f'\n处理完成：成功 {success_count} / 总数 {len(video_files)}'
        f'  ·  总耗时 {_fmt_duration(total_elapsed)}'
    )
    if success_count > 1:
        avg = total_elapsed / success_count
        print(f'  平均每文件：{_fmt_duration(avg)}')

    if _STOP_REQUESTED.is_set():
        return 130

    return 0 if success_count == len(video_files) else 1


if __name__ == '__main__':
    sys.exit(main())
