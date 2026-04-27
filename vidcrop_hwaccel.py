#!/usr/bin/env python3
"""
vidcrop_hwaccel.py — 基于 FFmpeg 的视频批量居中裁剪工具

功能概述
────────────────────────────────────────────────────────────────────
  • 单文件 / 文件夹批量处理，自动收集常见视频格式
  • 居中裁剪至指定分辨率，音频流直接复制不重编码
  • 根据编码器自动选择容器扩展名并做兼容性校验
  • 智能区分 CPU / GPU 编码器的质量控制参数（-crf / -cq）
  • 实时进度条：百分比 / 已处理帧数 / 实时 fps / ETA
  • 单文件耗时、输入输出体积对比、批量总耗时与均值统计

硬件加速
────────────────────────────────────────────────────────────────────
  • 运行时探测（非编译字符串匹配）下列能力：
        CUDA 解码、h264_nvenc、hevc_nvenc、crop_cuda 滤镜、
        Vulkan、VA-API、OpenCL
  • 按优先级自动生成策略链并依次尝试，前一级失败自动降级：
        1) CUDA 全流水线（硬解 + crop_cuda + NVENC 硬编）
        2) 自动硬解 + NVENC 硬编（CPU 做 crop）
        3) 指定硬解（cuda/vulkan/vaapi/opencl）+ 软件编码
        4) auto 模式下的最佳硬解 + 软件编码
        5) 纯 CPU 处理
  • 失败时打印 FFmpeg stderr 末 20 行辅助诊断

编码器智能处理
────────────────────────────────────────────────────────────────────
  • 名称别名自动归一化：
        h265_nvenc → hevc_nvenc, x264 → libx264, x265 → libx265, …
  • preset 在 NVENC 风格（p1~p7）与 libx264 风格
    （ultrafast~veryslow）之间双向自动映射
  • 质量参数按编码器族自动选用：
        - CPU 编码器（libx264/265 等）使用 -crf（默认 17）
        - GPU 编码器（*_nvenc/_amf/_qsv 等）使用 -cq（默认 16）
  • 降级场景下若用户仅给了 --cq 而实际落到软件编码器，
    通过 cq_to_crf() 做等效视觉质量映射而非直接透传：
        hevc_nvenc → libx265 : crf = cq + 4
        h264_nvenc → libx264 : crf = cq + 1
  • 若 --ffmpeg-bin 指定了自定义路径，ffprobe 会从同目录推导，
    保证版本一致

用法示例
────────────────────────────────────────────────────────────────────
  # 1) 单文件 · CPU 编码（libx264 + CRF）
  python vidcrop_hwaccel.py \
      --input video.mp4 --output out.mp4 \
      --output-width 1280 --output-height 720 \
      --codec libx264 --crf 18 --preset slow

  # 2) 单文件 · GPU 编码（NVENC H.265 + CQ）
  python vidcrop_hwaccel.py \
      --input video.mp4 --output out.mp4 \
      --output-width 1280 --output-height 720 \
      --codec hevc_nvenc --cq 20 --preset p5

  # 3) 批量处理整个文件夹（自动选择最优编码器）
  python vidcrop_hwaccel.py \
      --input ./videos --output ./cropped \
      --output-width 640 --output-height 360 \
      --codec auto --overwrite

  # 4) 强制禁用硬件加速（纯 CPU 环境 / 调试用）
  python vidcrop_hwaccel.py \
      --input ./videos --output ./cropped \
      --output-width 1920 --output-height 1080 \
      --codec libx265 --crf 22 --hwaccel none

  # 5) 指定硬件加速后端（例如 Linux 上的 VA-API 仅做硬解）
  python vidcrop_hwaccel.py \
      --input clip.mkv --output clip_cropped.mp4 \
      --output-width 1280 --output-height 720 \
      --codec libx264 --crf 20 --hwaccel vaapi

  # 6) 使用编码器别名（自动归一化为 hevc_nvenc）
  python vidcrop_hwaccel.py \
      --input clip.mp4 --output clip_out.mp4 \
      --output-width 1920 --output-height 1080 \
      --codec h265_nvenc --cq 19 --preset p6

  # 7) 手动指定容器扩展名 + 自定义 FFmpeg 路径
  python vidcrop_hwaccel.py \
      --input ./raw --output ./out \
      --output-width 1280 --output-height 720 \
      --codec libx264 --crf 18 \
      --container .mkv \
      --ffmpeg-bin /opt/ffmpeg/bin/ffmpeg

  # 8) 显式提供原始分辨率（跳过 ffprobe 探测，适合超大批量）
  python vidcrop_hwaccel.py \
      --input ./videos --output ./cropped \
      --original-width 3840 --original-height 2160 \
      --output-width 1920 --output-height 1080 \
      --codec hevc_nvenc --cq 22
"""

import argparse
import subprocess
import json
import sys
import os
import tempfile
import time
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Dict

# ═══════════════════════════════════════════════════════════════════
#  常量定义
# ═══════════════════════════════════════════════════════════════════

# 常见视频文件扩展名
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.webm', '.ts'}

# 编码器名称别名映射（常见拼写 → FFmpeg 实际名称）
CODEC_ALIASES = {
    'h265_nvenc': 'hevc_nvenc',
    'h265_amf': 'hevc_amf',
    'h265_qsv': 'hevc_qsv',
    'h265_videotoolbox': 'hevc_videotoolbox',
    'x264': 'libx264',
    'x265': 'libx265',
    'nvenc': 'h264_nvenc',
    'nvenc_h264': 'h264_nvenc',
    'nvenc_h265': 'hevc_nvenc',
    'nvenc_hevc': 'hevc_nvenc',
}

# 编码器 -> 推荐容器扩展名映射表
CODEC_CONTAINER_MAP = {
    'libx264': '.mp4',
    'libx265': '.mp4',
    'h264_amf': '.mp4',
    'hevc_amf': '.mp4',
    'h264_nvenc': '.mp4',
    'hevc_nvenc': '.mp4',
    'libvpx-vp9': '.webm',
    'libvpx': '.webm',
    'libaom-av1': '.mp4',
    'librav1e': '.mp4',
    'prores': '.mov',
    'prores_ks': '.mov',
    'mpeg4': '.mp4',
    'libxvid': '.avi',
    'copy': None,  # 流复制沿用原容器
}

# 支持 -preset 参数的编码器集合
PRESET_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'h264_amf', 'hevc_amf',
    'h264_nvenc', 'hevc_nvenc',
    'h264_qsv', 'hevc_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox'
}

# 支持 -crf 参数的编码器集合（CPU 软件编码器）
CRF_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'libvpx-vp9', 'libvpx', 'libaom-av1', 'librav1e'
}

# 支持 -cq 参数的编码器集合（GPU 硬件编码器）
CQ_SUPPORTED_CODECS = {
    'h264_nvenc', 'hevc_nvenc',
    'h264_amf', 'hevc_amf',
    'h264_qsv', 'hevc_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox'
}

# NVENC preset ↔ libx264 preset 双向映射
NVENC_TO_X264_PRESET = {
    'p1': 'ultrafast',
    'p2': 'superfast',
    'p3': 'veryfast',
    'p4': 'medium',
    'p5': 'slow',
    'p6': 'slower',
    'p7': 'veryslow',
}

# 默认质量控制值
DEFAULT_CRF = 17
DEFAULT_CQ = 16


# ═══════════════════════════════════════════════════════════════════
#  硬件能力检测（运行时探测，参考 ffmpeg_io.py）
# ═══════════════════════════════════════════════════════════════════

class HardwareCapabilities:
    """硬件加速能力检测结果（基于运行时探测）"""

    def __init__(self):
        # CUDA/NVENC
        self.has_decoder = False       # CUDA 硬件解码运行时可用
        self.has_encoder_h264 = False  # h264_nvenc 运行时可用
        self.has_encoder_hevc = False  # hevc_nvenc 运行时可用
        self.has_crop_cuda = False     # crop_cuda 滤镜可用
        
        # 其他硬件加速器
        self.has_vulkan = False        # Vulkan 硬件加速可用
        self.has_vaapi = False         # VA-API 硬件加速可用
        self.has_opencl = False        # OpenCL 硬件加速可用
        
        # 记录哪些项目被实际检测过
        self._detected = set()

    def _mark_detected(self, item: str):
        """标记项目已被检测"""
        self._detected.add(item)

    def has_nvenc(self, codec: str) -> bool:
        """检查指定的 NVENC 编码器是否运行时可用"""
        if codec == 'h264_nvenc':
            return self.has_encoder_h264
        if codec == 'hevc_nvenc':
            return self.has_encoder_hevc
        return False

    def has_any_encoder(self) -> bool:
        return self.has_encoder_h264 or self.has_encoder_hevc

    def can_full_pipeline(self, codec: str) -> bool:
        """全 GPU 流水线：解码 + crop_cuda + NVENC 编码"""
        return self.has_decoder and self.has_crop_cuda and self.has_nvenc(codec)

    def has_hwaccel(self, hwaccel_type: str) -> bool:
        """检查特定硬件加速器是否可用"""
        if hwaccel_type == 'cuda':
            return self.has_decoder  # 使用解码能力作为代理
        elif hwaccel_type == 'vulkan':
            return self.has_vulkan
        elif hwaccel_type == 'vaapi':
            return self.has_vaapi
        elif hwaccel_type == 'opencl':
            return self.has_opencl
        else:
            return False

    def summary(self, only_detected: bool = False) -> str:
        """返回能力总结字符串
        
        Args:
            only_detected: 如果为 True，只返回实际检测过的项目
        """
        items = [
            ('CUDA 解码', self.has_decoder, 'has_decoder'),
            ('h264_nvenc', self.has_encoder_h264, 'has_encoder_h264'),
            ('hevc_nvenc', self.has_encoder_hevc, 'has_encoder_hevc'),
            ('crop_cuda', self.has_crop_cuda, 'has_crop_cuda'),
            ('Vulkan', self.has_vulkan, 'has_vulkan'),
            ('VA‑API', self.has_vaapi, 'has_vaapi'),
            ('OpenCL', self.has_opencl, 'has_opencl'),
        ]
        parts = []
        for name, available, key in items:
            if only_detected and key not in self._detected:
                continue
            parts.append(f"{name}={('✓' if available else '✗')}")
        return ', '.join(parts)


def _check_nvenc_available(ffmpeg_bin: str = 'ffmpeg', codec: str = 'h264_nvenc') -> bool:
    """
    运行时探测 NVENC 编码器可用性。
    参考 ffmpeg_io.py 的 _check_nvenc_available()：
    实际启动一个微型编码任务（1帧 64×64 null 源），检查是否报 NVENC 特有错误。
    """
    test_cmd = [
        ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'nullsrc=s=64x64:d=0.04:r=25',
        '-frames:v', '1',
        '-c:v', codec,
        '-f', 'null', '-',
    ]
    try:
        result = subprocess.run(
            test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=15
        )
        stderr_lower = result.stderr.lower()
        if result.returncode != 0:
            nvenc_errors = [
                'openencodesessionex failed',
                'no capable devices found',
                'unsupported device',
                'cannot load libnvidia-encode',
                'error while opening encoder',
                'no nvenc capable devices',
                'unknown encoder',
                'encoder.*not found',
            ]
            is_nvenc_error = any(kw in stderr_lower for kw in nvenc_errors)
            if is_nvenc_error:
                return False
            # 非 NVENC 特有错误（可能是其他警告），保守假定可用
            return True
        return True
    except subprocess.TimeoutExpired:
        # [BUG-2] 64×64 单帧编码超时 15s 必然是驱动异常或卡死，不应视为可用
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _check_cuda_decoder_available(ffmpeg_bin: str = 'ffmpeg') -> bool:
    """
    运行时探测 CUDA 硬件解码是否可用。
    生成一个微型 H.264 测试流，然后用 -hwaccel cuda 解码，检查 stderr 中是否有加载失败的错误。
    """
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)

        # 第1步：生成微型 H.264 测试流
        gen_cmd = [
            ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc=s=64x64:d=0.1:r=25',
            '-c:v', 'libx264', '-frames:v', '2', tmp_path
        ]
        r = subprocess.run(gen_cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not os.path.exists(tmp_path):
            print(f'      CUDA 解码检测：生成测试视频失败，stderr: {r.stderr[:200]}')
            return False

        # 第2步：用 -hwaccel cuda 尝试解码
        test_cmd = [
            ffmpeg_bin, '-y', '-hide_banner',
            '-hwaccel', 'cuda',
            '-hwaccel_device', '0',
            '-i', tmp_path,
            '-frames:v', '1', '-f', 'null', '-'
        ]
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
        stderr_lower = result.stderr.lower()

        cuda_decode_errors = [
            'cannot load libnvcuvid',
            'failed loading nvcuvid',
            'hwaccel initialisation returned error',
            'no cuda capable devices',
            'does not support device type cuda',
            'cuda_error_no_device',
        ]
        if any(err in stderr_lower for err in cuda_decode_errors):
            # 打印前500字符的错误信息供调试
            err_lines = [line for line in result.stderr.split('\n') if any(err in line.lower() for err in cuda_decode_errors)]
            if err_lines:
                print(f'      CUDA 解码检测到错误：{err_lines[0][:200]}')
            else:
                print(f'      CUDA 解码检测到未知错误，stderr前200字符：{result.stderr[:200]}')
            return False
        return True
    except Exception as e:
        print(f'      CUDA 解码检测异常：{e}')
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _check_hwaccel_available(ffmpeg_bin: str = 'ffmpeg', hwaccel_type: str = 'cuda') -> bool:
    """
    运行时探测指定硬件加速器是否可用。
    生成一个微型 H.264 测试流，然后用 -hwaccel <type> 解码，检查 stderr 中是否有加载失败的错误。
    """
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)

        # 第1步：生成微型 H.264 测试流
        gen_cmd = [
            ffmpeg_bin, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc=s=64x64:d=0.1:r=25',
            '-c:v', 'libx264', '-frames:v', '2', tmp_path
        ]
        r = subprocess.run(gen_cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not os.path.exists(tmp_path):
            print(f'      {hwaccel_type} 解码检测：生成测试视频失败，stderr: {r.stderr[:200]}')
            return False

        # 第2步：用 -hwaccel <type> 尝试解码
        test_cmd = [
            ffmpeg_bin, '-y', '-hide_banner',
            '-hwaccel', hwaccel_type,
            '-i', tmp_path,
            '-frames:v', '1', '-f', 'null', '-'
        ]
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
        stderr_lower = result.stderr.lower()

        hwaccel_errors = [
            f'cannot load {hwaccel_type}',
            f'failed loading {hwaccel_type}',
            'hwaccel initialisation returned error',
            f'no {hwaccel_type} capable devices',
            f'does not support device type {hwaccel_type}',
            f'{hwaccel_type}_error',
            'creation failure',
            'device creation failed',
            'no device available for decoder',
            'hardware device setup failed',
            'error opening device',
            'instance creation failure',
            'failed to initialize',
            'unsupported device',
        ]
        if any(err in stderr_lower for err in hwaccel_errors):
            err_lines = [line for line in result.stderr.split('\n') if any(err in line.lower() for err in hwaccel_errors)]
            if err_lines:
                print(f'      {hwaccel_type} 解码检测到错误：{err_lines[0][:200]}')
            else:
                print(f'      {hwaccel_type} 解码检测到未知错误，stderr前200字符：{result.stderr[:200]}')
            return False
        return True
    except Exception as e:
        print(f'      {hwaccel_type} 解码检测异常：{e}')
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def detect_cuda_capabilities(ffmpeg_bin: str = 'ffmpeg', hwaccel: Optional[str] = None) -> HardwareCapabilities:
    """运行时检测硬件加速能力"""
    caps = HardwareCapabilities()
    
    # 如果明确指定不检测，直接返回空对象
    if hwaccel == 'none':
        return caps
    
    print("正在检测硬件加速能力...")
    
    # 确定需要检测的加速器类型
    # None 或 'auto' 表示检测所有
    detect_all = hwaccel is None or hwaccel == 'auto'
    detect_cuda = detect_all or hwaccel == 'cuda'
    detect_vulkan = detect_all or hwaccel == 'vulkan'
    detect_vaapi = detect_all or hwaccel == 'vaapi'
    detect_opencl = detect_all or hwaccel == 'opencl'
    
    # CUDA/NVENC 相关检测（仅当需要 CUDA 或检测所有时）
    if detect_cuda:
        # 检测 CUDA 硬件解码
        print("  CUDA 解码:  ", end='', flush=True)
        caps.has_decoder = _check_cuda_decoder_available(ffmpeg_bin)
        caps._mark_detected('has_decoder')
        print("可用 ✓" if caps.has_decoder else "不可用 ✗", flush=True)
        
        # 检测 h264_nvenc 编码器
        print("  h264_nvenc: ", end='', flush=True)
        caps.has_encoder_h264 = _check_nvenc_available(ffmpeg_bin, 'h264_nvenc')
        caps._mark_detected('has_encoder_h264')
        print("可用 ✓" if caps.has_encoder_h264 else "不可用 ✗", flush=True)
        
        # 检测 hevc_nvenc 编码器
        print("  hevc_nvenc: ", end='', flush=True)
        caps.has_encoder_hevc = _check_nvenc_available(ffmpeg_bin, 'hevc_nvenc')
        caps._mark_detected('has_encoder_hevc')
        print("可用 ✓" if caps.has_encoder_hevc else "不可用 ✗", flush=True)
        
        # 检测 crop_cuda 滤镜（编译时检测即可，不依赖运行时驱动库）
        try:
            fil = subprocess.run(
                [ffmpeg_bin, '-hide_banner', '-filters'],
                capture_output=True, text=True, timeout=10
            )
            caps.has_crop_cuda = 'crop_cuda' in fil.stdout
            caps._mark_detected('has_crop_cuda')
        except Exception:
            caps.has_crop_cuda = False
        print(f"  crop_cuda:  {'可用 ✓' if caps.has_crop_cuda else '不可用 ✗'}", flush=True)
    else:
        # 跳过 CUDA 相关检测，保持默认值 False
        pass
    
    # Vulkan 检测
    if detect_vulkan:
        print("  Vulkan:     ", end='', flush=True)
        caps.has_vulkan = _check_hwaccel_available(ffmpeg_bin, 'vulkan')
        caps._mark_detected('has_vulkan')
        print("可用 ✓" if caps.has_vulkan else "不可用 ✗", flush=True)
    
    # VA-API 检测
    if detect_vaapi:
        print("  VA‑API:     ", end='', flush=True)
        caps.has_vaapi = _check_hwaccel_available(ffmpeg_bin, 'vaapi')
        caps._mark_detected('has_vaapi')
        print("可用 ✓" if caps.has_vaapi else "不可用 ✗", flush=True)
    
    # OpenCL 检测
    if detect_opencl:
        print("  OpenCL:     ", end='', flush=True)
        caps.has_opencl = _check_hwaccel_available(ffmpeg_bin, 'opencl')
        caps._mark_detected('has_opencl')
        print("可用 ✓" if caps.has_opencl else "不可用 ✗", flush=True)
    
    return caps


# ═══════════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════════

def normalize_codec_name(codec: str) -> str:
    """将常见的编码器别名归一化为 FFmpeg 实际名称"""
    if codec in ('auto', 'copy'):
        return codec
    lower = codec.lower()
    # [BUG-1] 回退值使用 lower 而非原始 codec，避免非全小写输入（如 H264_NVENC）
    #         导致后续所有 set 查找（CRF/CQ/PRESET_SUPPORTED_CODECS）全部 miss
    normalized = CODEC_ALIASES.get(lower, lower)
    if normalized != lower:
        print(f"提示：编码器名称 '{codec}' 已归一化为 '{normalized}'")
    elif normalized != codec:
        print(f"提示：编码器名称 '{codec}' 已转为小写 '{normalized}'")
    return normalized


def normalize_preset(preset: str, target_codec: str) -> str:
    """
    将用户输入的 preset 转换为目标编码器可接受的值。
    自动在 NVENC (p1~p7) 和 libx264 (ultrafast~veryslow) 之间转换。
    发生映射或回退时打印提示，保持与 _resolve_quality_params 一致的透明化风格。
    """
    if target_codec in ('h264_nvenc', 'hevc_nvenc'):
        # 已经是 NVENC 风格则直接返回（无需提示）
        if preset.startswith('p') and preset[1:].isdigit():
            return preset
        # libx264 风格 → NVENC 风格
        reverse_map = {v: k for k, v in NVENC_TO_X264_PRESET.items()}
        if preset in reverse_map:
            mapped = reverse_map[preset]
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
                  f"（编码器 {target_codec} 使用 NVENC 风格 preset）。")
            return mapped
        # 未知 preset，回退到默认
        print(f"  提示：--preset '{preset}' 在编码器 {target_codec} 下无对应映射，"
              f"使用默认值 'p4'（可通过 --preset p1~p7 指定）。")
        return 'p4'

    if target_codec in ('libx264', 'libx265'):
        # NVENC 风格 → libx264 风格
        if preset.startswith('p') and preset[1:].isdigit():
            if preset in NVENC_TO_X264_PRESET:
                mapped = NVENC_TO_X264_PRESET[preset]
                print(f"  提示：--preset '{preset}' 已映射为 '{mapped}'"
                      f"（编码器 {target_codec} 使用 libx264 风格 preset）。")
                return mapped
            print(f"  提示：--preset '{preset}' 在编码器 {target_codec} 下无对应映射，"
                  f"使用默认值 'medium'。")
            return 'medium'
        # 已经是 libx264 风格，直接返回（无需提示）
        return preset

    # 其他编码器原样透传
    return preset


def get_extension_from_codec(codec: str) -> Optional[str]:
    """根据编码器返回推荐的容器扩展名（包含点号），若为 copy 返回 None 表示沿用原扩展名"""
    if codec == 'copy':
        return None
    if codec in CODEC_CONTAINER_MAP:
        return CODEC_CONTAINER_MAP[codec]
    # 模糊匹配
    base_codec = codec.split('_')[0] if '_' in codec else codec
    for key, ext in CODEC_CONTAINER_MAP.items():
        if key and key.startswith(base_codec):
            return ext
    return '.mp4'  # 默认回退


def check_container_compatibility(ext: str, codec: str) -> bool:
    """粗略检查扩展名是否与编码器兼容"""
    ext_lower = ext.lower()
    codec_lower = codec.lower()
    if ext_lower == '.mp4':
        return any(c in codec_lower for c in ['264', '265', 'hevc', 'av1', 'mpeg4'])
    if ext_lower == '.webm':
        return any(c in codec_lower for c in ['vp8', 'vp9', 'av1'])
    if ext_lower == '.mov':
        return any(c in codec_lower for c in ['prores', 'h264', 'hevc'])
    if ext_lower == '.avi':
        return any(c in codec_lower for c in ['xvid', 'mpeg4'])
    return True


def encoder_supports_preset(codec: str) -> bool:
    """检查编码器是否接受 -preset 参数"""
    return codec in PRESET_SUPPORTED_CODECS


def encoder_supports_crf(codec: str) -> bool:
    """检查编码器是否接受 -crf 参数"""
    return codec in CRF_SUPPORTED_CODECS


def encoder_supports_cq(codec: str) -> bool:
    """检查编码器是否接受 -cq 参数"""
    return codec in CQ_SUPPORTED_CODECS


# [BUG-3] 根据 ffmpeg 路径推导同目录的 ffprobe，修复自定义 --ffmpeg-bin 时
#         仍走系统 ffprobe 导致版本不一致的问题
def _get_ffprobe_bin(ffmpeg_bin: str) -> str:
    """根据 ffmpeg 可执行文件路径推导同目录的 ffprobe 路径"""
    p = Path(ffmpeg_bin)
    # 纯文件名（无目录分量）：假定 ffprobe 也在 PATH 中
    if p.parent == Path('.'):
        return 'ffprobe'
    # 同目录下的 ffprobe，保留可能的后缀（如 Windows 的 .exe）
    return str(p.parent / ('ffprobe' + p.suffix))


def get_video_dimensions(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Tuple[int, int]:
    """使用 ffprobe 获取视频的宽度和高度"""
    ffprobe_bin = _get_ffprobe_bin(ffmpeg_bin)  # [BUG-3]
    cmd = [
        ffprobe_bin, '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'json', filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        stderr_output = result.stderr
        if 'corrupt' in stderr_output or 'conceal' in stderr_output:
            print(f"  警告：源文件可能包含损坏数据，输出视频中对应位置可能受影响。")
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        return int(stream['width']), int(stream['height'])
    except Exception as e:
        print(f"错误：无法获取视频尺寸 {filepath} - {e}", file=sys.stderr)
        raise


def collect_video_files(input_path: Path) -> List[Path]:
    """收集输入路径下的所有视频文件（单文件或文件夹）"""
    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_EXTENSIONS:
            return [input_path]
        else:
            print(f"警告：{input_path} 不是支持的视频文件格式，将跳过。", file=sys.stderr)
            return []
    elif input_path.is_dir():
        video_files = []
        for ext in VIDEO_EXTENSIONS:
            video_files.extend(input_path.glob(f'*{ext}'))
            video_files.extend(input_path.glob(f'*{ext.upper()}'))
        return sorted(set(video_files))  # 去重排序
    else:
        print(f"错误：输入路径 {input_path} 不存在。", file=sys.stderr)
        return []


def build_crop_filter(orig_w: int, orig_h: int, out_w: int, out_h: int,
                      use_cuda: bool = False) -> str:
    """
    生成裁剪滤镜字符串。
    use_cuda=True 时使用 crop_cuda（需要 -hwaccel_output_format cuda 配合）。
    """
    if out_w > orig_w or out_h > orig_h:
        raise ValueError(f"目标尺寸 ({out_w}x{out_h}) 不能大于原始尺寸 ({orig_w}x{orig_h})")
    x_offset = (orig_w - out_w) // 2
    y_offset = (orig_h - out_h) // 2
    filter_name = "crop_cuda" if use_cuda else "crop"
    return f"{filter_name}={out_w}:{out_h}:{x_offset}:{y_offset}"


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
) -> List[str]:
    """
    构建 FFmpeg 命令。
    hwaccel: None / 'auto' / 'cuda'
    hwaccel_output_format: None / 'cuda'（仅全 GPU 流水线时使用）
    """
    # [WARN-2] warning 级别保留 FFmpeg 警告输出，便于失败时诊断
    cmd = [ffmpeg_bin, '-hide_banner', '-loglevel', 'warning']

    # 忽略某些解码错误
    cmd += ['-err_detect', 'ignore_err']

    # 生成时间戳并丢弃完全损坏的帧
    cmd += ['-fflags', '+genpts+discardcorrupt']

    # 硬件加速参数
    if hwaccel:
        cmd += ['-hwaccel', hwaccel]
        # 为某些加速器添加设备参数
        if hwaccel in ('cuda', 'opencl'):
            cmd += ['-hwaccel_device', '0']
        if hwaccel_output_format:
            cmd += ['-hwaccel_output_format', hwaccel_output_format]

    cmd += ['-i', str(input_file)]

    # 视频滤镜
    cmd += ['-vf', vf_filter]

    # 视频编码器
    cmd += ['-c:v', codec]

    # 质量控制参数：cq 和 crf 互斥，由调用方决定传入哪个
    if cq is not None and encoder_supports_cq(codec):
        cmd += ['-cq', str(cq)]
    elif crf is not None and encoder_supports_crf(codec):
        cmd += ['-crf', str(crf)]

    # 编码器预设
    # [BUG-4] preset 已由调用方（process_file）归一化，此处直接使用，
    #         避免二次 normalize_preset() 在降级场景下错误逆映射
    if encoder_supports_preset(codec):
        cmd += ['-preset', preset]

    # 音频流复制
    cmd += ['-c:a', 'copy']

    # 覆盖 / 跳过
    cmd += ['-y' if overwrite else '-n']

    cmd += [str(output_file)]
    return cmd


# ═══════════════════════════════════════════════════════════════════
#  进度条与耗时辅助（OPT-1 / OPT-2）
# ═══════════════════════════════════════════════════════════════════

def _fmt_size(n_bytes: int) -> str:
    """将字节数格式化为人类可读的大小字符串"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n_bytes < 1024.0:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024.0
    return f"{n_bytes:.1f} TB"


def _fmt_duration(seconds: float) -> str:
    """将秒数格式化为 Xs / Xm Xs / Xh Xm Xs"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def _get_total_frames(filepath: str, ffmpeg_bin: str = 'ffmpeg') -> Optional[int]:
    """
    通过 ffprobe 探测视频总帧数。
    优先读取 nb_frames 字段；不可用时按 duration × fps 估算。
    失败时返回 None（进度条退化为已处理帧数显示）。
    """
    ffprobe_bin = _get_ffprobe_bin(ffmpeg_bin)
    cmd = [
        ffprobe_bin, '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_frames,duration,r_frame_rate',
        '-of', 'json', filepath,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        d = json.loads(r.stdout)['streams'][0]
        nb = d.get('nb_frames', '')
        if nb and nb not in ('N/A', ''):
            return int(nb)
        # 降级：duration × fps 估算（容器不存储帧数时）
        dur = float(d.get('duration', 0) or 0)
        rfr = d.get('r_frame_rate', '0/1')
        num_s, _, den_s = rfr.partition('/')
        fps = float(num_s) / float(den_s) if float(den_s) > 0 else 0
        if dur > 0 and fps > 0:
            return max(1, int(dur * fps))
    except Exception:
        pass
    return None


def _run_with_progress(
    cmd: List[str],
    total_frames: Optional[int],
) -> Tuple[int, str]:
    """
    执行 FFmpeg 命令并在终端显示实时进度条。

    实现方式：
      - 向命令注入 -progress pipe:1 -nostats，使 FFmpeg 将结构化进度写入 stdout
      - 将 -loglevel 提升为 warning（已在 build_ffmpeg_cmd 中设置），
        通过独立线程异步收集 stderr，失败时打印最后若干行
      - stdout 主循环解析 key=value 行，提取 frame= 字段驱动进度条

    Returns:
        (returncode, stderr_full_text)
    """
    # 注入 -progress pipe:1 -nostats（插在第一个 -i 之前）
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

        # 异步读取 stderr，避免缓冲区满导致死锁
        def _drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        t_stderr = threading.Thread(target=_drain_stderr, daemon=True)
        t_stderr.start()

        for raw_line in proc.stdout:
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
                    f"\r  [{bar}] {pct*100:5.1f}%"
                    f"  {frame}/{total_frames}帧"
                    f"  {fps:5.1f}fps"
                    f"  ETA {eta:.0f}s   ",
                    end='', flush=True,
                )
            else:
                print(
                    f"\r  已处理 {frame} 帧  {fps:.1f}fps  {elapsed:.1f}s   ",
                    end='', flush=True,
                )

        proc.wait()
        t_stderr.join(timeout=3)
        print()  # 进度条换行

        return proc.returncode, ''.join(stderr_lines)

    except Exception as exc:
        print()
        return 1, str(exc)


# ═══════════════════════════════════════════════════════════════════
#  策略生成
# ═══════════════════════════════════════════════════════════════════

def _get_software_fallback(codec: str) -> str:
    """根据用户请求的编码器确定软件回退编码器"""
    if codec in ('hevc_nvenc', 'hevc_amf', 'hevc_qsv', 'libx265'):
        return 'libx265'
    return 'libx264'


def _select_best_hwaccel(hw_caps: HardwareCapabilities) -> Optional[str]:
    """根据硬件能力选择最佳硬件加速器，返回 None 表示无可用加速器"""
    # 优先级顺序：CUDA > Vulkan > VA-API > OpenCL
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
) -> List[Dict]:
    """
    根据硬件能力和用户意图生成策略列表，按优先级从高到低排序。
    每个策略包含：
        name:                   描述名称
        hwaccel:                None / 'auto' / 'cuda'
        hwaccel_output_format:  None / 'cuda'
        use_hw_filter:          是否使用 crop_cuda 滤镜
        codec:                  实际使用的编码器
        fallback:               是否为降级策略
    """
    strategies = []

    # ── 确定首选编码器 ──
    if user_codec == 'auto':
        if hw_caps.has_encoder_h264:
            preferred_codec = 'h264_nvenc'
        else:
            preferred_codec = 'libx264'
    else:
        preferred_codec = user_codec

    is_nvenc = preferred_codec in ('h264_nvenc', 'hevc_nvenc')
    nvenc_available = hw_caps.has_nvenc(preferred_codec) if is_nvenc else False
    sw_fallback = _get_software_fallback(preferred_codec)

    # 如果用户指定了非 NVENC 的具体编码器（如 libx265），软件策略使用该编码器
    if not is_nvenc and user_codec not in ('auto',):
        sw_codec = user_codec
    else:
        sw_codec = sw_fallback

    # ── 策略1：全 GPU 流水线（解码 + crop_cuda + NVENC 编码）──
    if hw_mode != 'none' and is_nvenc and hw_caps.can_full_pipeline(preferred_codec):
        strategies.append({
            'name': 'CUDA 全加速（解码+crop_cuda+NVENC 编码）',
            'hwaccel': 'cuda',
            'hwaccel_output_format': 'cuda',
            'use_hw_filter': True,
            'codec': preferred_codec,
            'fallback': False,
        })

    # ── 策略2：自动硬件解码 + NVENC 编码（CPU crop）──
    if hw_mode != 'none' and is_nvenc and nvenc_available:
        strategies.append({
            'name': '自动硬件解码 + GPU 编码',
            'hwaccel': 'auto',
            'hwaccel_output_format': None,
            'use_hw_filter': False,
            'codec': preferred_codec,
            'fallback': False,
        })

    # ── 策略3：特定硬件加速解码 + 软件编码 ──
    specific_hwaccels = ('cuda', 'vulkan', 'vaapi', 'opencl')
    if hw_mode in specific_hwaccels and hw_caps.has_hwaccel(hw_mode):
        # 为 OpenCL 设置输出格式以避免 QSV 映射警告
        hwaccel_output_format = None
        if hw_mode == 'opencl':
            hwaccel_output_format = 'nv12'  # OpenCL 常用输出格式
        strategies.append({
            'name': f'{hw_mode} 硬件解码 + CPU 编码',
            'hwaccel': hw_mode,
            'hwaccel_output_format': hwaccel_output_format,
            'use_hw_filter': False,
            'codec': sw_codec,
            'fallback': False,  # 用户明确要求此加速器
        })

    # ── 策略4：自动硬件解码 + 软件编码 ──
    if hw_mode == 'auto':
        best_hw = _select_best_hwaccel(hw_caps)
        if best_hw is not None:
            # 为 OpenCL 设置输出格式以避免 QSV 映射警告
            hwaccel_output_format = None
            if best_hw == 'opencl':
                hwaccel_output_format = 'nv12'  # OpenCL 常用输出格式
            strategies.append({
                'name': f'{best_hw} 硬件解码 + CPU 编码',
                'hwaccel': best_hw,
                'hwaccel_output_format': hwaccel_output_format,
                'use_hw_filter': False,
                'codec': sw_codec,
                'fallback': is_nvenc,  # 仅当用户原本想用 NVENC 时才算降级
            })
        # 如果没有可用的硬件加速器，则跳过此策略，直接回退到纯 CPU 处理

    # ── 策略5：纯 CPU 处理 ──
    strategies.append({
        'name': '纯 CPU 处理',
        'hwaccel': None,
        'hwaccel_output_format': None,
        'use_hw_filter': False,
        'codec': sw_codec,
        'fallback': True,
    })

    return strategies


# ═══════════════════════════════════════════════════════════════════
#  质量参数解析
# ═══════════════════════════════════════════════════════════════════

def cq_to_crf(cq: int, target_codec: str) -> int:
    """
    将 NVENC CQ 值映射到软件编码器等效 CRF 值。

    背景：libx265 编码效率高于 hevc_nvenc，相同视觉质量下需要更高的 CRF 数值。
    直接透传 CQ 值会导致软件编码产生远大于预期的文件（质量过高）。

    映射规则：
      hevc_nvenc → libx265 : crf = cq + 4  （效率差距较大）
      h264_nvenc → libx264 : crf = cq + 1  （同标准，接近等价，保守修正）
      其他编码器           : 直接返回 cq   （未知编码器，不做猜测）

    Args:
        cq:           用户指定的 NVENC CQ 值（0-51）
        target_codec: 实际使用的软件编码器名称

    Returns:
        映射后的 CRF 值，限制在 [0, 51] 范围内
    """
    if target_codec == 'libx265':
        return min(51, cq + 4)
    elif target_codec == 'libx264':
        return min(51, cq + 1)
    else:
        return cq


def _resolve_quality_params(
    codec: str,
    user_crf: Optional[int],
    user_cq: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    """
    根据编码器类型和用户提供的参数，确定最终的 (crf, cq) 值。
    规则：
      - GPU 编码器：优先使用 user_cq，无则使用 DEFAULT_CQ
      - CPU 编码器：优先使用 user_crf，无则使用 DEFAULT_CRF
      - 用户只提供了不匹配的参数时（如给 GPU 传 --crf），打印提示并使用默认值
    """
    if encoder_supports_cq(codec):
        # GPU 编码器
        if user_cq is not None:
            return None, user_cq
        elif user_crf is not None:
            # 用户只给了 --crf 但当前编码器是 GPU 的；不做猜测转换，使用默认值并提示
            print(f"  提示：编码器 {codec} 不支持 -crf，使用默认 -cq {DEFAULT_CQ}（可通过 --cq 指定）。")
            return None, DEFAULT_CQ
        else:
            return None, DEFAULT_CQ
    elif encoder_supports_crf(codec):
        # CPU 编码器
        if user_crf is not None:
            return user_crf, None
        elif user_cq is not None:
            # 用户只给了 --cq，但当前编码器是 CPU 软件编码器（发生了降级）。
            # 不能直接透传 CQ 值，因为软件编码器效率更高，相同数值质量偏高、文件偏大。
            # 使用 cq_to_crf() 做等效视觉质量映射。
            mapped_crf = cq_to_crf(user_cq, codec)
            print(f"  提示：编码器 {codec} 不支持 -cq，"
                  f"已将 --cq {user_cq} 映射为 -crf {mapped_crf}（等效视觉质量）。")
            return mapped_crf, None
        else:
            return DEFAULT_CRF, None
    else:
        # 其他编码器不支持 crf/cq
        return None, None


# ═══════════════════════════════════════════════════════════════════
#  文件处理
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
    hw_mode: str,
    hw_caps: HardwareCapabilities,
    ffmpeg_bin: str = 'ffmpeg',
) -> bool:
    """处理单个视频文件，支持动态策略降级"""

    # ── 确定原始尺寸 ──
    if orig_width is None or orig_height is None:
        try:
            actual_width, actual_height = get_video_dimensions(str(input_file), ffmpeg_bin)  # [BUG-3]
        except Exception:
            return False
    else:
        actual_width, actual_height = orig_width, orig_height

    t_file_start = time.perf_counter()  # [OPT-2] 文件级计时开始
    print(f"\n处理文件：{input_file}")
    print(f"  原始尺寸: {actual_width}x{actual_height} → 目标裁剪尺寸: {out_width}x{out_height}")

    # [OPT-1] 预先探测总帧数，供进度条使用（失败时退化为帧计数显示）
    total_frames = _get_total_frames(str(input_file), ffmpeg_bin)

    # 尺寸相同时跳过
    if actual_width == out_width and actual_height == out_height:
        print(f"  跳过：目标尺寸与原始尺寸相同，无需裁剪。")
        return True

    # 尺寸过大时跳过
    if out_width > actual_width or out_height > actual_height:
        print(f"  跳过：目标尺寸 ({out_width}x{out_height}) "
              f"大于原始尺寸 ({actual_width}x{actual_height})", file=sys.stderr)
        return False

    # ── 构建输出文件名 ──
    # 确定输出扩展名用的编码器（auto 时暂按 libx264 推断）
    ext_codec = codec if codec not in ('auto', 'copy') else 'libx264'
    if batch_mode or not output_path.suffix:
        output_dir = output_path
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = container if container else get_extension_from_codec(ext_codec)
        if ext is None:
            ext = input_file.suffix
        output_file = output_dir / f"{input_file.stem}_cropped{ext}"
    else:
        output_file = output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if container is None and codec not in ('copy', 'auto'):
            if not check_container_compatibility(output_file.suffix, codec):
                rec_ext = get_extension_from_codec(codec)
                print(f"  警告：输出扩展名 '{output_file.suffix}' 可能与编码器 '{codec}' 不兼容，"
                      f"推荐使用 '{rec_ext}'", file=sys.stderr)

    # 检查是否已存在
    if output_file.exists() and not overwrite:
        print(f"  输出文件 {output_file} 已存在，跳过（使用 --overwrite 覆盖）。")
        return True

    # ── 生成可行策略列表 ──
    all_strategies = _generate_strategies(codec, hw_caps, hw_mode)

    # ── 依次尝试策略 ──
    for i, strategy in enumerate(all_strategies):
        current_codec = strategy['codec']
        use_hw_filter = strategy['use_hw_filter']
        hwaccel = strategy.get('hwaccel')
        hwaccel_output_format = strategy.get('hwaccel_output_format')

        # 构建裁剪滤镜
        try:
            vf_filter = build_crop_filter(
                actual_width, actual_height, out_width, out_height,
                use_cuda=use_hw_filter
            )
        except ValueError as e:
            print(f"  跳过：{e}", file=sys.stderr)
            return False

        # 确定质量参数
        current_crf, current_cq = _resolve_quality_params(current_codec, crf, cq)

        # 归一化 preset
        normalized_preset = normalize_preset(preset, current_codec)

        # 构建命令
        cmd = build_ffmpeg_cmd(
            input_file=input_file,
            output_file=output_file,
            vf_filter=vf_filter,
            codec=current_codec,
            crf=current_crf,
            cq=current_cq,
            preset=normalized_preset,
            overwrite=overwrite,
            hwaccel=hwaccel,
            hwaccel_output_format=hwaccel_output_format,
            ffmpeg_bin=ffmpeg_bin,
        )

        # 显示策略信息
        tag = "（降级）" if strategy.get('fallback', False) else ""
        print(f"  策略 [{i + 1}/{len(all_strategies)}]: {strategy['name']}{tag}")
        print(f"  命令：{' '.join(cmd)}")

        # [OPT-1] 实时进度条执行；[WARN-2] 捕获 stderr 供失败诊断
        rc, stderr_text = _run_with_progress(cmd, total_frames)
        if rc == 0:
            elapsed  = time.perf_counter() - t_file_start  # [OPT-2]
            in_size  = input_file.stat().st_size
            out_size = output_file.stat().st_size
            ratio    = (1.0 - out_size / in_size) * 100 if in_size > 0 else 0.0
            direction = "↓" if ratio >= 0 else "↑"
            print(f"  ✓ 完成：{output_file}")
            print(f"    大小：{_fmt_size(in_size)} → {_fmt_size(out_size)}"  # [OPT-2]
                  f"（{direction}{abs(ratio):.1f}%）  耗时：{_fmt_duration(elapsed)}")
            return True
        else:
            # [WARN-2] 打印 stderr 最后 20 行，帮助定位失败原因
            err_lines = [l for l in stderr_text.strip().splitlines() if l.strip()]
            if err_lines:
                print(f"  FFmpeg 错误输出（末 {min(20, len(err_lines))} 行）：",
                      file=sys.stderr)
                for el in err_lines[-20:]:
                    print(f"    {el}", file=sys.stderr)
            print(f"  ✗ 策略失败（rc={rc}），尝试下一策略...", file=sys.stderr)
            # 清理可能不完整的输出文件
            if output_file.exists():
                try:
                    output_file.unlink()
                except Exception:
                    pass
            continue

    print(f"  ✗ 所有策略均失败，放弃处理。", file=sys.stderr)
    return False


# ═══════════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="批量裁剪视频（居中裁剪），支持 NVIDIA CUDA 硬件加速及智能降级。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
硬件加速选项：
  --hwaccel auto   : 自动检测并启用 CUDA 组件（默认）
  --hwaccel cuda   : 强制启用 CUDA（若缺失组件则降级）
  --hwaccel none   : 禁用硬件加速，纯 CPU 处理

编码器名称别名（自动归一化）：
  h265_nvenc → hevc_nvenc,  x264 → libx264,  x265 → libx265

编码器 preset 说明（NVENC ↔ libx264 自动转换）：
  NVENC:   p1(fastest) ~ p7(slowest)，默认 p4
  libx264: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow

质量控制参数说明：
  --crf : 用于 CPU 编码器（libx264/265 等），0-51 越小质量越高，不指定时默认 17。
  --cq  : 用于 GPU 编码器（h264_nvenc 等），0-51 越小质量越高，不指定时默认 21。
  当发生编码器降级时，程序会自动使用对应编码器的默认值并提示。
"""
    )
    parser.add_argument('--input', required=True,
                        help='输入视频文件或包含视频的文件夹')
    parser.add_argument('--output', required=True,
                        help='输出文件（单文件时）或输出文件夹（批量时）')
    parser.add_argument('--original-width', type=int,
                        help='原始视频宽度（若不提供则自动检测）')
    parser.add_argument('--original-height', type=int,
                        help='原始视频高度（若不提供则自动检测）')
    parser.add_argument('--output-width', type=int, required=True,
                        help='目标视频宽度')
    parser.add_argument('--output-height', type=int, required=True,
                        help='目标视频高度')
    parser.add_argument('--codec', default='libx264',
                        help='视频编码器（默认 libx264，可使用 auto 自动选择；'
                             '支持别名如 h265_nvenc → hevc_nvenc）')
    parser.add_argument('--crf', type=int, default=None,
                        help='CRF 质量控制值（0-51，用于 CPU 编码器，不指定时默认 17）')
    parser.add_argument('--cq', type=int, default=None,
                        help='CQ 质量控制值（0-51，用于 GPU 编码器，不指定时默认 21）')
    parser.add_argument('--preset', default='slow',
                        help='编码器预设（默认 slow，NVENC/x264 格式自动转换）')
    parser.add_argument('--overwrite', action='store_true',
                        help='覆盖已存在的输出文件')
    parser.add_argument('--container',
                        help='手动指定容器扩展名（如 .mp4）')
    parser.add_argument('--hwaccel', choices=['auto', 'cuda', 'vulkan', 'vaapi', 'opencl', 'none'], default='auto',
                        help='硬件加速模式（默认 auto，可选 cuda, vulkan, vaapi, opencl, none）。OpenCL 仅加速解码，编码仍使用软件。')
    parser.add_argument('--ffmpeg-bin', default='ffmpeg',
                        help='FFmpeg 可执行文件路径（默认 ffmpeg）')

    args = parser.parse_args()

    ffmpeg_bin = args.ffmpeg_bin

    # ── 归一化编码器名称 ──
    args.codec = normalize_codec_name(args.codec)

    # ── 检测硬件能力 ──
    if args.hwaccel == 'none':
        hw_caps = HardwareCapabilities()  # 全部禁用
        print("硬件加速已禁用，使用纯 CPU 处理。")
    else:
        hw_caps = detect_cuda_capabilities(ffmpeg_bin, args.hwaccel)
        print(f"硬件能力总结：{hw_caps.summary(only_detected=True)}")

        if args.hwaccel == 'cuda':
            # 强制 CUDA 模式下，至少要有一个 CUDA 组件可用
            if not hw_caps.has_decoder and not hw_caps.has_any_encoder():
                print("错误：强制启用 CUDA 但未检测到任何可用的 CUDA 组件。",
                      file=sys.stderr)
                sys.exit(1)

    # ── 参数提示 ──
    if args.crf is not None and args.cq is not None:
        print("提示：同时指定了 --crf 和 --cq，将根据实际编码器自动选用。")

    # ── 输入路径处理 ──
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    video_files = collect_video_files(input_path)
    if not video_files:
        print("未找到任何视频文件，退出。", file=sys.stderr)
        sys.exit(1)

    # 批量模式判断：多个文件 或 输入为文件夹
    batch_mode = len(video_files) > 1 or input_path.is_dir()

    # 当输出路径明确为文件（有扩展名）且输入为多文件时，视为目录
    if batch_mode and output_path.suffix:
        print(f"警告：批量处理时输出路径 '{output_path}' 带扩展名，将视为目录。",
              file=sys.stderr)
        output_path = output_path.with_suffix('')

    container_ext = args.container
    if container_ext and not container_ext.startswith('.'):
        container_ext = f'.{container_ext}'

    print(f"\n共找到 {len(video_files)} 个视频文件。")

    # ── 批量处理 ──
    t_main_start = time.perf_counter()  # [OPT-2] 总计时开始
    success_count = 0
    for vf in video_files:
        if process_file(
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
            hw_mode=args.hwaccel,
            hw_caps=hw_caps,
            ffmpeg_bin=ffmpeg_bin,
        ):
            success_count += 1

    total_elapsed = time.perf_counter() - t_main_start  # [OPT-2]
    print(f"\n处理完成：成功 {success_count} / 总数 {len(video_files)}"  # [OPT-2]
          f"  ·  总耗时 {_fmt_duration(total_elapsed)}")
    if success_count > 1:
        print(f"  平均每文件：{_fmt_duration(total_elapsed / success_count)}")


if __name__ == '__main__':
    main()