#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vidcrop_cpu_v2.py – 批量视频裁剪/覆盖缩放工具（CPU 多任务并发版 V2）

功能
────
• 支持两种处理模式：
  - crop：居中裁剪（目标尺寸不能大于源尺寸）
  - cover：等比缩放至完全覆盖目标区域后居中裁剪
• 新增 --crop-ratio 自动按目标宽高比（如 16:9）最大化裁剪，无需指定输出尺寸
• 批量处理视频文件，支持递归扫描输入目录并保持输出目录结构
• 自动探测系统 CPU 核心数与可用内存，智能决定并行任务数及单任务线程数
• 可通过 --workers / --threads / --mem-per-job 手动控制并发策略
• 支持几乎所有 FFmpeg 视频编码器，自动匹配推荐封装容器
• 编码格式覆盖 H.264 / H.265 / VP9 / AV1：
  - H.264/HEVC：libx264 / libx265
  - AV1：libsvtav1（推荐）/ libaom-av1 / librav1e
  - VP9：libvpx-vp9；VP8：libvpx
• 编码器别名自动归一化：x264→libx264, x265→libx265, h265_nvenc→hevc_nvenc,
  vp9→libvpx-vp9, av1→libaom-av1, svtav1→libsvtav1 等
• preset 双向映射：NVENC (p1~p7) 与 libx264 (ultrafast~veryslow) 自动转换
• 质量参数智能处理：CPU 编码器用 -crf (默认 21)，GPU 编码器用 -cq (默认 23)，
  降级/兼容时通过 cq_to_crf() 做等效视觉质量映射
• 丰富的编码选项：CRF/CQ 质量控制、preset 预设、像素格式指定等
• 默认值：libx264 + CRF 21 + preset medium
• 速度档位自动取值（无需手工加 --extra-args）：
  - libaom-av1 的 -cpu-used 与 libsvtav1 的 -preset 按 CPU 核数自动选档
    （ffmpeg 给 libaom-av1 的默认 -cpu-used=1 慢到不可用，实测仅 1fps）
• 提供聚合进度面板（并行模式）或单文件细粒度进度条（顺序模式）
• 新增 --dry-run 仅预览命令不执行转码；--log 将所有输出记录到日志文件
• 支持通过 --extra-args 传递额外 FFmpeg 参数

主要参数
────────
--input                输入视频文件或目录（必选）
--output               输出视频文件或目录（必选）
--output-width         目标视频宽度  (--crop-ratio 模式下可选)
--output-height        目标视频高度  (--crop-ratio 模式下可选)
--crop-ratio           目标宽高比，如 16:9 或 1.777，启用后自动计算最大化裁剪尺寸
--mode                 crop | cover（默认 crop）
--codec                视频编码器（默认 libx264，支持别名自动归一化）
--crf                  CRF 质量值（默认 21，仅对支持 CRF 的编码器生效）
--cq                   CQ 质量值（默认 23，仅对 NVENC/AMF/QSV 等 GPU 编码器生效，
                       CPU 编码器下自动映射为等效 CRF）
--preset               编码器预设（默认：CPU 编码器 medium / GPU 编码器 p5，支持 NVENC p1~p7 双向映射）
--pix-fmt              输出像素格式（auto / none / 具体格式）
--audio-codec          音频编码器（默认 copy，可选 aac / libopus 等）
--audio-bitrate        音频重编码码率（默认 128k）
--container            手动指定封装扩展名，如 .mp4 / .mkv
--overwrite            覆盖已存在的输出文件
-r, --recursive        递归扫描输入目录
--no-skip-same-size    即使源尺寸等于目标尺寸也强制转码
--workers              并行任务数（0=自动）
--threads              每任务 FFmpeg 线程数（0=自动）
--mem-per-job          单任务估计内存占用 GB（0=根据编码器画像）
--sequential           强制顺序执行，显示单文件进度条
--dry-run              仅生成并显示 FFmpeg 命令，不执行转码
--log LOG_FILE         将所有输出记录到指定日志文件
--extra-args ...       紧随 '--' 后的自定义 FFmpeg 参数（必须放在命令最后）

典型用例
────────
# 居中裁剪到 1280×720，使用 libx264，CRF 20
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --codec libx264 --crf 20

# 等比覆盖裁剪到 1920×1080，使用 libx265，CRF 22，覆盖已有文件
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 --mode cover \
    --codec libx265 --crf 22 --overwrite

# 自动宽高比裁剪：按 16:9 最大化裁剪，无需指定输出尺寸
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --crop-ratio 16:9 --codec libx264 --crf 20

# AV1（推荐 libsvtav1，比 libaom-av1 快一个数量级）
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 --codec svtav1 --crf 30 --preset medium

# AV1（libaom-av1；-cpu-used 已按 CPU 核数自动取值，无需再手工加 --extra-args）
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --codec av1 --crf 30

# VP9（默认容器 .webm；源音轨非 Opus/Vorbis 时会自动转 Opus）
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --codec vp9 --crf 32

# 使用编码器别名 (自动归一化为 libx264)
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --codec x264 --crf 20

# NVENC preset 映射：用户传 p5 会自动映射为 libx264 的 medium
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --codec libx264 --preset p5

# 手动并发：2 个并行任务，每个任务 4 个线程
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 --workers 2 --threads 4

# 仅预览将要执行的命令，不实际转码
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --dry-run

# 记录处理过程到日志文件
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --log process.log

# 在 FFmpeg 命令末尾追加自定义参数（注意最后的 --）
python vidcrop_cpu_v2.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --extra-args -- -max_muxing_queue_size 4096
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".m4v", ".ts", ".webm", ".mpg", ".mpeg"
}

CODEC_CONTAINER_MAP = {
    "libx264": ".mp4",
    "libx265": ".mp4",
    "libvpx-vp9": ".webm",
    "libvpx": ".webm",
    "vp9_qsv": ".webm",
    "libaom-av1": ".mp4",
    "libsvtav1": ".mp4",
    "librav1e": ".mp4",
    "av1_nvenc": ".mp4",
    "av1_qsv": ".mp4",
    "av1_amf": ".mp4",
    "prores": ".mov",
    "prores_ks": ".mov",
    "mpeg4": ".mp4",
    "libxvid": ".avi",
    "mjpeg": ".avi",
    "copy": None,
}

# GPU 硬件编码器同样支持 -preset（NVENC 使用 p1~p7），需纳入否则 --preset p5 会被丢弃。
# 注意：libaom-av1 / libvpx-vp9 / librav1e 没有 -preset（分别是 -cpu-used /
# -deadline / -speed），传了只会被静默忽略，故不纳入，避免"设了但没生效"。
# libsvtav1 的 -preset 是 0~13 整数，由 normalize_preset() 单独换算。
PRESET_SUPPORTED_CODECS = {
    "libx264", "libx265",
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_amf", "hevc_amf", "av1_amf",
    "h264_qsv", "hevc_qsv", "av1_qsv",
    "h264_videotoolbox", "hevc_videotoolbox",
    "libsvtav1",
}

CRF_SUPPORTED_CODECS = {
    "libx264",
    "libx265",
    "libvpx-vp9",
    "libvpx",
    "libaom-av1",
    "libsvtav1",
    "librav1e",
}

# NVENC 家族：p1~p7 风格 preset + -cq 质量控制。AV1 与 H.264/HEVC 同属此族。
NVENC_CODECS = {"h264_nvenc", "hevc_nvenc", "av1_nvenc"}

# AV1 软件编码器（cq_to_crf 等处按族处理）
AV1_SW_CODECS = {"libaom-av1", "libsvtav1", "librav1e"}

# libsvtav1 的 -preset 是 0~13 的整数（越大越快、质量越低），**不接受**
# ultrafast~veryslow 或 p1~p7 这类名字——实测传 'medium' 直接报
# "Unable to parse option value"，故必须单独换算。
X264_TO_SVTAV1_PRESET = {
    "ultrafast": 12, "superfast": 11, "veryfast": 10, "faster": 9,
    "fast": 8, "medium": 8, "slow": 6, "slower": 4, "veryslow": 2,
    "placebo": 0,
}
NVENC_TO_SVTAV1_PRESET = {
    "p1": 12, "p2": 11, "p3": 10, "p4": 9, "p5": 8, "p6": 6, "p7": 4,
}

PRESET_VALUES = {
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow", "placebo",
}


PIX_FMT_DEFAULT_BY_CODEC = {
    "prores": "yuv422p10le",
    "prores_ks": "yuv422p10le",
}

PIX_FMT_REQUIRE_EVEN_BOTH = {
    "yuv420p", "yuvj420p", "nv12", "nv21",
    # 4:2:0 高位深同样要求宽高均为偶数（位深继承后必须纳入校验）
    "yuv420p10le", "yuv420p12le", "p010le", "p012le", "p016le",
}

PIX_FMT_REQUIRE_EVEN_WIDTH = {
    "yuv422p", "yuvj422p", "yuv422p10le", "yuv422p12le"
}

# 编码器画像：(推荐线程数, 单任务估计内存 GB)
CODEC_PROFILE: Dict[str, Tuple[int, float]] = {
    "libx264": (4, 0.8),
    "libx265": (4, 1.2),
    "libvpx": (4, 0.8),
    "libvpx-vp9": (4, 1.0),
    "libaom-av1": (4, 1.5),
    # SVT-AV1 是线程与内存大户（内部按 tile 拆帧），给的画像比 x264 高一档
    "libsvtav1": (8, 2.0),
    "librav1e": (4, 1.2),
    "av1_nvenc": (2, 0.5),
    "av1_qsv": (2, 0.5),
    "vp9_qsv": (2, 0.5),
    "prores": (4, 1.0),
    "prores_ks": (4, 1.0),
    "mpeg4": (2, 0.4),
    "libxvid": (2, 0.4),
    "mjpeg": (2, 0.3),
    "copy": (1, 0.1),
}

_MIN_SYSTEM_RESERVE_GB = 0.2
_MAX_USABLE_RATIO = 0.80

_ACTIVE_PROCS: Set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()
_STOP_REQUESTED = threading.Event()

# ═══════════════════════════════════════════════════════════════════
#  新增：日志记录 Tee 类
# ═══════════════════════════════════════════════════════════════════
class Tee:
    """将输出同时写入原始流和日志文件"""
    def __init__(self, original, log_file):
        self.original = original
        self.log = open(log_file, 'a', encoding='utf-8')
        self.log.write(f"\n\n{'='*60}\n")
        self.log.write(f"执行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log.write(f"命令行: {' '.join(sys.argv)}\n{'='*60}\n\n")

    def write(self, data):
        self.original.write(data)
        self.log.write(data)
        self.log.flush()

    def flush(self):
        self.original.flush()
        self.log.flush()

    def close(self):
        self.log.close()

def setup_log(log_path: str):
    """重定向标准输出/错误到 Tee 对象"""
    sys.stdout = Tee(sys.stdout, log_path)
    sys.stderr = Tee(sys.stderr, log_path)

# ═══════════════════════════════════════════════════════════════════
#  子进程 / 信号
# ═══════════════════════════════════════════════════════════════════

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
    print("\n[INFO] 收到中断信号，正在终止 FFmpeg 子进程……", file=sys.stderr)
    _terminate_active_procs()
    raise KeyboardInterrupt


def install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)


# ═══════════════════════════════════════════════════════════════════
#  CPU / 内存探测
# ═══════════════════════════════════════════════════════════════════

def _detect_cpu() -> Tuple[int, str]:
    try:
        with open("/sys/fs/cgroup/cpu.max", "r", encoding="utf-8") as f:
            parts = f.read().strip().split()
        if len(parts) == 2 and parts[0] != "max":
            quota = int(parts[0])
            period = int(parts[1])
            if quota > 0 and period > 0:
                return max(1, int(round(quota / period))), f"cgroup v2 ({quota}/{period})"
    except Exception:
        pass

    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r", encoding="utf-8") as f:
            quota = int(f.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r", encoding="utf-8") as f:
            period = int(f.read().strip())
        if quota > 0 and period > 0:
            return max(1, int(round(quota / period))), f"cgroup v1 ({quota}/{period})"
    except Exception:
        pass

    try:
        n = len(os.sched_getaffinity(0))
        if n > 0:
            return n, "sched_getaffinity"
    except Exception:
        pass

    return max(1, os.cpu_count() or 1), "os.cpu_count"


def _read_cgroup_v2_memstat() -> Dict[str, int]:
    stat: Dict[str, int] = {}
    try:
        with open("/sys/fs/cgroup/memory.stat", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        stat[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except Exception:
        pass
    return stat


def _detect_memory_gb() -> Tuple[float, float, str]:
    try:
        with open("/sys/fs/cgroup/memory.max", "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v != "max":
            limit = int(v)
            current = 0
            try:
                with open("/sys/fs/cgroup/memory.current", "r", encoding="utf-8") as f:
                    current = int(f.read().strip())
            except Exception:
                pass

            stat = _read_cgroup_v2_memstat()
            reclaimable = stat.get("file", 0) + stat.get("slab_reclaimable", 0)
            non_reclaim = max(0, current - reclaimable)
            avail = max(limit - non_reclaim, limit // 20)

            return limit / (1024 ** 3), avail / (1024 ** 3), "cgroup v2"
    except Exception:
        pass

    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r", encoding="utf-8") as f:
            limit = int(f.read().strip())
        if limit < (1 << 62):
            usage = 0
            try:
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes", "r", encoding="utf-8") as f:
                    usage = int(f.read().strip())
            except Exception:
                pass

            cache = 0
            try:
                with open("/sys/fs/cgroup/memory/memory.stat", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("total_cache "):
                            cache = int(line.split()[1])
                            break
            except Exception:
                pass

            non_reclaim = max(0, usage - cache)
            avail = max(limit - non_reclaim, limit // 20)
            return limit / (1024 ** 3), avail / (1024 ** 3), "cgroup v1"
    except Exception:
        pass

    try:
        info: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                k, _, rest = line.partition(":")
                if rest:
                    info[k.strip()] = int(rest.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", total)
        if total > 0:
            return total / (1024 ** 3), avail / (1024 ** 3), "/proc/meminfo"
    except Exception:
        pass

    # Windows：通过 ctypes 调用 GlobalMemoryStatusEx 读取物理内存
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength",                ctypes.c_ulong),
                    ("dwMemoryLoad",             ctypes.c_ulong),
                    ("ullTotalPhys",             ctypes.c_ulonglong),
                    ("ullAvailPhys",             ctypes.c_ulonglong),
                    ("ullTotalPageFile",         ctypes.c_ulonglong),
                    ("ullAvailPageFile",         ctypes.c_ulonglong),
                    ("ullTotalVirtual",          ctypes.c_ulonglong),
                    ("ullAvailVirtual",          ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            total = stat.ullTotalPhys / (1024 ** 3)
            avail = stat.ullAvailPhys / (1024 ** 3)
            if total > 0:
                return total, avail, "GlobalMemoryStatusEx"
        except Exception:
            pass

    return 2.0, 1.6, "fallback default"


def detect_system_resources() -> Tuple[int, float, float]:
    cpu, cpu_src = _detect_cpu()
    total_gb, avail_gb, mem_src = _detect_memory_gb()
    print(
        f"资源探测    : CPU={cpu} ({cpu_src})   "
        f"MEM 总={total_gb:.2f}GB 可用={avail_gb:.2f}GB ({mem_src})"
    )
    return cpu, total_gb, avail_gb


def compute_parallelism(
    num_pending: int,
    codec: str,
    cpu: int,
    total_mem_gb: float,
    avail_mem_gb: float,
    workers_override: int = 0,
    threads_override: int = 0,
    mem_per_job_override: float = 0.0,
) -> Tuple[int, int]:
    pref_threads, default_mem = CODEC_PROFILE.get(codec, (4, 0.8))

    if num_pending <= 0:
        threads = threads_override if threads_override > 0 else min(pref_threads, max(1, cpu))
        return 0, max(1, threads)

    if num_pending == 1 and workers_override == 0:
        threads_per_job = threads_override if threads_override > 0 else cpu
        return 1, max(1, min(threads_per_job, cpu))

    if threads_override > 0:
        threads_per_job = min(threads_override, max(1, cpu))
    else:
        threads_per_job = min(pref_threads, max(1, cpu))

    if workers_override > 0:
        workers = workers_override
    else:
        max_by_cpu = max(1, cpu // max(1, threads_per_job))
        mem_per_job = mem_per_job_override if mem_per_job_override > 0 else default_mem

        budget_by_total = total_mem_gb * _MAX_USABLE_RATIO
        budget_by_avail = max(0.0, avail_mem_gb - _MIN_SYSTEM_RESERVE_GB)
        usable_mem = max(0.1, min(budget_by_total, budget_by_avail))

        max_by_mem = max(1, int(usable_mem / max(0.1, mem_per_job)))
        workers = min(max_by_cpu, max_by_mem)

    workers = max(1, min(workers, num_pending))
    return workers, threads_per_job


# ═══════════════════════════════════════════════════════════════════
#  通用工具
# ═══════════════════════════════════════════════════════════════════

def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"[ERROR] 系统中未找到 {tool}，请先安装 FFmpeg。", file=sys.stderr)
            sys.exit(1)


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


def _fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    if sec < 60:
        return f"{sec:.1f}s"
    total = int(sec)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02d}h{minutes:02d}m{seconds:02d}s"
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes}m{seconds:02d}s"


def normalize_container(container: Optional[str]) -> Optional[str]:
    if not container:
        return None
    c = container.strip()
    if not c:
        return None
    if not c.startswith("."):
        c = "." + c
    return c.lower()


def normalize_extra_args(extra: Optional[List[str]]) -> List[str]:
    if not extra:
        return []
    extra = list(extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    return extra


def resolve_pix_fmt(codec: str, pix_fmt_arg: str,
                    src_bits: Optional[int] = None,
                    warn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    解析 --pix-fmt。

    none/no/disable → 不指定；具体值 → 原样返回；
    auto 且源为 10bit+ → 继承位深（见 resolve_pix_fmt_for_source）；
    否则沿用编码器默认（多数 yuv420p，ProRes 为 yuv422p10le）。
    """
    if pix_fmt_arg.lower() in {"none", "no", "disable"}:
        return None
    if pix_fmt_arg.lower() != "auto":
        return pix_fmt_arg
    if src_bits and src_bits >= 10:
        return resolve_pix_fmt_for_source(codec, src_bits, warn=warn)
    return PIX_FMT_DEFAULT_BY_CODEC.get(codec.lower(), "yuv420p")


def validate_output_dimensions(width: int, height: int, pix_fmt: Optional[str]) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("--output-width 和 --output-height 必须为正整数")

    if not pix_fmt:
        return

    pf = pix_fmt.lower()
    if pf in PIX_FMT_REQUIRE_EVEN_BOTH:
        if width % 2 != 0 or height % 2 != 0:
            raise ValueError(
                f"像素格式 {pix_fmt} 要求输出宽高均为偶数；当前为 {width}x{height}"
            )
    elif pf in PIX_FMT_REQUIRE_EVEN_WIDTH:
        if width % 2 != 0:
            raise ValueError(f"像素格式 {pix_fmt} 要求输出宽度为偶数；当前为 {width}")


# ═══════════════════════════════════════════════════════════════════
#  编码器 / 容器
# ═══════════════════════════════════════════════════════════════════

def get_extension_from_codec(codec: str) -> Optional[str]:
    c = codec.lower()
    if c == "copy":
        return None
    if c in CODEC_CONTAINER_MAP:
        return CODEC_CONTAINER_MAP[c]

    base = c.split("_", 1)[0]
    for key, ext in CODEC_CONTAINER_MAP.items():
        if key != "copy" and key.startswith(base):
            return ext
    return ".mp4"


def check_container_compatibility(ext: str, codec: str) -> bool:
    ext_lower = ext.lower()
    codec_lower = codec.lower()

    if ext_lower == ".mkv":
        return True
    if ext_lower in {".mp4", ".m4v"}:
        # VP9 虽以 .webm 为默认容器，但 ISO-BMFF 同样能封装 VP9（实测可写），
        # 用户用 --container .mp4 强制时不应误报警告。
        return any(x in codec_lower for x in
                   ["264", "265", "hevc", "av1", "rav1e", "mpeg4", "vp9"])
    if ext_lower == ".webm":
        return any(x in codec_lower for x in ["vpx", "vp8", "vp9", "av1", "rav1e"])
    if ext_lower == ".mov":
        return any(x in codec_lower for x in ["prores", "264", "265", "hevc"])
    if ext_lower == ".avi":
        return any(x in codec_lower for x in ["xvid", "mpeg4", "mjpeg"])
    return True


def encoder_supports_preset(codec: str) -> bool:
    return codec.lower() in PRESET_SUPPORTED_CODECS


def encoder_supports_crf(codec: str) -> bool:
    return codec.lower() in CRF_SUPPORTED_CODECS


# ═══════════════════════════════════════════════════════════════════
#  新增：编码器别名、质量参数、Preset 映射 (从 hwaccel 移植，纯 CPU 逻辑)
# ═══════════════════════════════════════════════════════════════════

# 编码器别名映射：常见别名 -> FFmpeg 标准名称
CODEC_ALIASES: Dict[str, str] = {
    "x264": "libx264",
    "x265": "libx265",
    "h264": "libx264",
    "h265": "libx265",
    "hevc": "libx265",
    "avc": "libx264",
    "vp9": "libvpx-vp9",
    "vp8": "libvpx",
    "libvpx_vp9": "libvpx-vp9",
    "av1": "libaom-av1",
    "av01": "libaom-av1",
    "svtav1": "libsvtav1",
    "svt-av1": "libsvtav1",
    "libsvt-av1": "libsvtav1",
    "rav1e": "librav1e",
    "prores": "prores_ks",
    "mpeg2": "mpeg2video",
    # NVENC 别名 (兼容性：用户可能习惯传这些，虽然后端用 CPU 编码)
    "h264_nvenc": "h264_nvenc",
    "hevc_nvenc": "hevc_nvenc",
    "h265_nvenc": "hevc_nvenc",
    "nvenc_av1": "av1_nvenc",
    "av1_nvenc": "av1_nvenc",
}

# NVENC preset 到 libx264 preset 的双向映射表
NVENC_TO_X264_PRESET: Dict[str, str] = {
    "p1": "ultrafast",
    "p2": "superfast",
    "p3": "veryfast",
    "p4": "faster",
    "p5": "medium",
    "p6": "slow",
    "p7": "veryslow",
}

# 支持 CQ (Constant Quality) 的编码器集合 (GPU 编码器)
CQ_SUPPORTED_CODECS = {
    "h264_nvenc", "hevc_nvenc", "h265_nvenc", "av1_nvenc",
    "h264_amf", "hevc_amf", "av1_amf",
    "h264_qsv", "hevc_qsv", "av1_qsv",
}

DEFAULT_CRF = 21
DEFAULT_CQ = 23

# 未指定 --preset 时的默认预设：CPU 软件编码器 medium，GPU 硬件编码器 p5，
# libsvtav1 为 8（0~13 整数中速度与质量的平衡点）
DEFAULT_PRESET_CPU = "medium"
DEFAULT_PRESET_GPU = "p5"
DEFAULT_PRESET_SVTAV1 = "8"


def detect_cpu_profile() -> Tuple[int, float]:
    """返回 (逻辑 CPU 核数, 可用内存 GB)。探测失败时回退 (os.cpu_count() or 4, 0.0)。"""
    try:
        cpu = os.cpu_count() or 4
    except Exception:
        cpu = 4
    try:
        avail_gb = 0.0
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
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
    (8, 5, 8),
    (4, 6, 9),
    (0, 8, 10),
)


def auto_effort(cpu_count: Optional[int] = None) -> Tuple[int, int]:
    """按可用核数返回 (libaom-av1 的 -cpu-used, libsvtav1 的 -preset)。"""
    if cpu_count is None:
        cpu_count, _ = detect_cpu_profile()
    for min_cores, aom_cpu_used, svtav1_preset in _AUTO_EFFORT_TIERS:
        if cpu_count >= min_cores:
            return aom_cpu_used, svtav1_preset
    return 8, 10


def default_preset_for(codec: str) -> str:
    """按编码器类型给出默认预设：GPU 硬件编码器 p5，libsvtav1 按资源取档，其余 medium。"""
    c = codec.lower()
    if c == "libsvtav1":
        # 0~13 整数，按 CPU 核数自动取值（见 auto_effort）
        return str(auto_effort()[1])
    return DEFAULT_PRESET_GPU if c in CQ_SUPPORTED_CODECS else DEFAULT_PRESET_CPU


def normalize_codec_name(codec: str) -> str:
    """将常见编码器别名归一化为 FFmpeg 标准名称。"""
    if codec in ("auto", "copy"):
        return codec
    lower = codec.lower()
    normalized = CODEC_ALIASES.get(lower, lower)
    if normalized != lower:
        print(f"提示：编码器名称 '{codec}' 已归一化为 '{normalized}'")
    elif normalized != codec:
        print(f"提示：编码器名称 '{codec}' 已转为小写 '{normalized}'")
    return normalized


def normalize_preset(preset: str, target_codec: str) -> str:
    """在 NVENC (p1~p7) 与 libx264 风格 (ultrafast~veryslow) 之间自动双向映射。

    libsvtav1 另走一套：它的 -preset 是 0~13 的整数，名字类取值一律先换算成
    整数再下发，否则 ffmpeg 解析失败（"Unable to parse option value"）。
    """
    if target_codec == "libsvtav1":
        p = preset.strip().lower()
        if p.lstrip("-").isdigit():
            # 已是整数写法，仅收敛到 libsvtav1 的合法区间 0~13
            return str(max(0, min(13, int(p))))
        mapped = NVENC_TO_SVTAV1_PRESET.get(p) or X264_TO_SVTAV1_PRESET.get(p)
        if mapped is not None:
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}' (libsvtav1 使用 0~13 整数 preset)。")
            return str(mapped)
        print(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，"
              f"使用默认 '{DEFAULT_PRESET_SVTAV1}'。")
        return DEFAULT_PRESET_SVTAV1

    # NVENC 编码器使用 p1~p7
    if target_codec in NVENC_CODECS:
        if preset.startswith("p") and preset[1:].isdigit():
            return preset
        # libx264 preset -> NVENC preset
        rev = {v: k for k, v in NVENC_TO_X264_PRESET.items()}
        if preset in rev:
            mapped = rev[preset]
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}' ({target_codec} 使用 NVENC 风格 preset)。")
            return mapped
        print(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，"
              f"使用默认 '{DEFAULT_PRESET_GPU}'。")
        return DEFAULT_PRESET_GPU

    # libx264/libx265 使用 ultrafast~veryslow
    if target_codec in ("libx264", "libx265"):
        if preset.startswith("p") and preset[1:].isdigit():
            mapped = NVENC_TO_X264_PRESET.get(preset, DEFAULT_PRESET_CPU)
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}' ({target_codec} 使用 libx264 风格 preset)。")
            return mapped
        return preset

    return preset


def encoder_supports_cq(codec: str) -> bool:
    """检查编码器是否支持 -cq 参数。"""
    return codec in CQ_SUPPORTED_CODECS


def cq_to_crf(cq: int, target_codec: str) -> int:
    """
    将 NVENC CQ 值映射到软件编码器等效 CRF 值。
    直接透传 CQ 会导致软件编码器质量偏高、文件偏大，因此做等效视觉质量修正：
      hevc_nvenc -> libx265 : crf = cq + 4
      h264_nvenc -> libx264 : crf = cq + 1
      av1_nvenc  -> AV1 软编 : crf = cq + 6  （AV1 比 HEVC 更高效，且 CRF 量程是 0~63）
      其他编码器            : 直接返回 cq (未知编码器，不做猜测)

    实测（ffmpeg 6.1，640x480 testsrc2 2s）：libaom-av1 crf28 输出体积
    （184 KB）≈ libx264 crf24（195 KB），即 AV1 在同体积下 CRF 比 x264 高约 4~5。
    """
    if target_codec == "libx265":
        return min(51, cq + 4)
    elif target_codec == "libx264":
        return min(51, cq + 1)
    elif target_codec in AV1_SW_CODECS:
        return min(63, cq + 6)
    return cq


def crf_to_rav1e_qp(crf: int) -> int:
    """
    librav1e 没有 -crf（实测传 -crf 只会被 ffmpeg 静默忽略并告警，质量退回默认值），
    只有 0~255 的 -qp。按实测标定换算：
      qp = (crf - 5) × 4
    标定数据（ffmpeg 6.1，640x480 testsrc2 2s，输出体积互差 < 5%）：
      libaom crf 20/25/30/35  <- rav1e qp 60/80/100/120
    """
    return max(0, min(255, (int(crf) - 5) * 4))


def _resolve_quality_params(
    codec: str,
    user_crf: Optional[int],
    user_cq: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    """
    根据编码器类型确定最终 (crf, cq) 值，处理参数不匹配和兼容性映射。
    GPU 编码器优先使用 user_cq，CPU 编码器优先使用 user_crf。
    兼容场景：用户传了 --cq 但实际用的是 CPU 编码器，通过 cq_to_crf() 映射。
    """
    if encoder_supports_cq(codec):
        if user_cq is not None:
            return None, user_cq
        if user_crf is not None:
            print(f"  提示：编码器 {codec} 不支持 -crf，使用默认 -cq {DEFAULT_CQ} (可通过 --cq 指定)。")
        return None, DEFAULT_CQ

    if encoder_supports_crf(codec):
        if user_crf is not None:
            return user_crf, None
        if user_cq is not None:
            mapped_crf = cq_to_crf(user_cq, codec)
            print(f"  提示：编码器 {codec} 不支持 -cq，"
                  f"已将 --cq {user_cq} 映射为 -crf {mapped_crf} (等效视觉质量)。")
            return mapped_crf, None
        return DEFAULT_CRF, None

    return None, None


def _fmt_size(n_bytes: int) -> str:
    """格式化字节数为人类可读字符串。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n_bytes < 1024.0:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024.0
    return f"{n_bytes:.1f} PB"


def _fmt_duration(seconds: float) -> str:
    """格式化秒数为人类可读时长字符串。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _label(text: str, width: int = 12) -> str:
    """把标签按显示宽度补齐到 width 列（CJK 记 2 列），输出「标签 + 空格 + ': '」。

    用于让概览块与单文件块各字段纵向对齐，与 vidcrop_hwaccel.py 保持一致。
    """
    cells = sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)
    return text + " " * max(1, width - cells) + ": "


def _size_change(src: Path, dst: Path) -> str:
    """输出「输入 → 输出（↓x%）」的体积变化描述（与 vidcrop_hwaccel.py 对齐）。"""
    try:
        in_size, out_size = src.stat().st_size, dst.stat().st_size
    except OSError:
        return "—"
    if in_size <= 0:
        return _fmt_size(out_size)
    ratio = (1.0 - out_size / in_size) * 100
    direction = "↓" if ratio >= 0 else "↑"
    return f"{_fmt_size(in_size)} → {_fmt_size(out_size)}（{direction}{abs(ratio):.1f}%）"


def _same_path(a: Path, b: Path) -> bool:
    """比较两个路径是否指向同一位置 (解析符号链接)。"""
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return False


def parse_crop_ratio(ratio_str: str) -> Tuple[int, int]:
    """
    解析宽高比字符串，支持 '16:9'、'4:3' 或浮点数 '1.777' 格式。
    返回 (numerator, denominator) 元组。
    """
    ratio_str = ratio_str.strip()
    if ":" in ratio_str:
        try:
            num_str, den_str = ratio_str.split(":", 1)
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
            # 将浮点数转为近似分数 (分母限制在 1000 以内)
            from fractions import Fraction
            frac = Fraction(ratio_val).limit_denominator(1000)
            return frac.numerator, frac.denominator
        except Exception:
            raise ValueError(f"无效的宽高比格式: '{ratio_str}'，应为 '16:9' 或浮点数如 '1.777'")


def calculate_auto_crop_size(src_w: int, src_h: int, target_num: int, target_den: int) -> Tuple[int, int]:
    """
    根据源尺寸和目标宽高比，计算最大化裁剪后的输出尺寸 (保持原始分辨率，仅裁剪)。

    逻辑：
    - 目标比例 = target_num / target_den
    - 源比例 = src_w / src_h
    - 如果源比例 > 目标比例 (视频更宽)：裁剪左右，保持高度不变
      out_w = src_h * target_num / target_den, out_h = src_h
    - 如果源比例 < 目标比例 (视频更高)：裁剪上下，保持宽度不变
      out_w = src_w, out_h = src_w * target_den / target_num
    - 结果取整为偶数 (编码器要求)
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

    # 确保偶数尺寸 (大多数编码器要求宽高为偶数)
    out_w = out_w if out_w % 2 == 0 else out_w + 1
    out_h = out_h if out_h % 2 == 0 else out_h + 1

    # 安全检查：不得超过源尺寸
    out_w = min(out_w, src_w)
    out_h = min(out_h, src_h)

    return out_w, out_h


def build_encoder_options(
    codec: str,
    crf: Optional[int],
    preset: str,
    pix_fmt: Optional[str],
    warn: Callable[[str], None],
) -> List[str]:
    c = codec.lower()
    opts: List[str] = ["-c:v", codec]

    if c == "copy":
        return opts

    if crf is not None:
        if encoder_supports_crf(c):
            if c in {"libvpx", "libvpx-vp9"}:
                opts += ["-b:v", "0"]
            opts += ["-crf", str(crf)]
        else:
            warn(f"编码器 {codec} 不支持 -crf，已忽略 --crf {crf}")

    if encoder_supports_preset(c):
        opts += ["-preset", preset]
    else:
        if preset != default_preset_for(c):
            warn(f"编码器 {codec} 不支持 -preset，已忽略 --preset {preset}")

    if pix_fmt:
        opts += ["-pix_fmt", pix_fmt]

    return opts


def build_encoder_options_v2(
    codec: str,
    crf: Optional[int],
    cq: Optional[int],
    preset: str,
    pix_fmt: Optional[str],
    warn: Callable[[str], None],
) -> List[str]:
    """V2 版本：支持 crf 和 cq 双参数"""
    c = codec.lower()
    opts: List[str] = ["-c:v", codec]

    if c == "copy":
        return opts

    if cq is not None and encoder_supports_cq(codec):
        opts += ["-cq", str(cq)]
    elif crf is not None and encoder_supports_crf(codec):
        if c in ("libvpx", "libvpx-vp9"):
            # VP8/VP9 的 CRF 必须配合 -b:v 0 才是纯恒定质量，否则退化成
            # 受码率上限约束的 constrained quality。
            opts += ["-b:v", "0"]
        if c == "librav1e":
            # rav1e 不认 -crf（会被静默忽略），换算成等效 -qp
            opts += ["-qp", str(crf_to_rav1e_qp(crf))]
        else:
            opts += ["-crf", str(crf)]
    elif crf is not None:
        warn(f"编码器 {codec} 不支持 -crf，已忽略 --crf {crf}")
    elif cq is not None:
        warn(f"编码器 {codec} 不支持 -cq，已忽略 --cq {cq}")

    # libaom-av1 的速度档位：ffmpeg 默认 -cpu-used=1 慢到不可用（实测 320x240 仅 1fps，
    # libsvtav1 同期 35fps），按资源自动取值。--extra-args 追加在编码器选项之后，
    # 因此用户显式指定时以其为准。
    if c == "libaom-av1":
        opts += ["-cpu-used", str(auto_effort()[0])]

    if encoder_supports_preset(c):
        opts += ["-preset", preset]
    else:
        if preset != default_preset_for(c):
            warn(f"编码器 {codec} 不支持 -preset，已忽略 --preset {preset}")

    if pix_fmt:
        opts += ["-pix_fmt", pix_fmt]

    return opts


# ═══════════════════════════════════════════════════════════════════
#  [META-KEEP] 原输入视频元数据探测与保留
#
#  一次 ffprobe 拿全量（-show_streams 默认即含 side_data_list：旋转 / HDR
#  mastering display / content light level），按 abspath|size|mtime 缓存，
#  供 ffprobe_info / build_color_args / build_ffmpeg_cmd 共用，避免重复探测。
# ═══════════════════════════════════════════════════════════════════

_PROBE_CACHE: Dict[str, Dict] = {}
_PROBE_CACHE_LOCK = threading.Lock()

# 10bit 源在各编码器下的目标像素格式（软件编码器 yuv420p10le，NVENC p010le）
_PIXFMT_10BIT_BY_ENCODER = {
    "libx264": "yuv420p10le",
    "libx265": "yuv420p10le",
    "libsvtav1": "yuv420p10le",
    "libaom-av1": "yuv420p10le",
    "librav1e": "yuv420p10le",
    "libvpx-vp9": "yuv420p10le",
    "h264_nvenc": "p010le",
    "hevc_nvenc": "p010le",
    "av1_nvenc": "p010le",
    "prores": "yuv422p10le",
    "prores_ks": "yuv422p10le",
}
# h264_nvenc 在列：NVENC 的 H.264 编码器只做 8bit，喂 10bit 输入会直接失败，
# 故 10bit 源落到它身上时降为 8bit 而不是让它硬撑（hevc_nvenc / av1_nvenc 支持 10bit）。
_ENCODERS_8BIT_ONLY = {"mpeg4", "libvpx", "mjpeg", "vp8", "h264_v4l2m2m",
                       "h264_nvenc"}

_BITMAP_SUBS = {"dvd_subtitle", "dvb_subtitle", "dvb_teletext",
                "hdmv_pgs_subtitle", "xsub"}
_MP4_FAMILY = {"mp4", "m4v", "mov"}

# ffmpeg 输出端 -color_trc 与 setparams 滤镜接受的取值集合不一致（6.1 实测）：
#   -color_trc          只认 libavutil 规范名 gamma22/gamma28（BT.470M/BT.470BG）
#   setparams=color_trc 只认别名 bt470m/bt470bg，传 gamma28 直接报错
# 因此输出端用规范名、滤镜端用别名，两者语义等价（-color_trc gamma28 写出 bt470bg）。
_TRC_OUTPUT_NAMES = {"bt470bg": "gamma28", "bt470m": "gamma22"}
_TRC_FILTER_NAMES = {v: k for k, v in _TRC_OUTPUT_NAMES.items()}

_FFMPEG_MAJOR: Optional[int] = None


def _probe_cache_key(path: str) -> str:
    """缓存键：绝对路径 + size + mtime（与 ffprobe 版本无关，进程内安全）。"""
    ap = os.path.abspath(path)
    try:
        st = os.stat(ap)
        return f"{ap}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return ap


def _rat(value) -> Optional[int]:
    """ffprobe 有理数字段可能是 '34000/50000'、[34000, 50000] 或纯数字，取分子。"""
    try:
        if isinstance(value, (list, tuple)):
            return int(str(value[0]).split("/")[0])
        return int(str(value).split("/")[0])
    except Exception:
        return None


def _parse_rate(rate) -> Optional[float]:
    """'25/1' / '30000/1001' → float；无法解析返回 None。"""
    try:
        if isinstance(rate, (list, tuple)):
            num, den = float(rate[0]), float(rate[1])
        else:
            num_s, _, den_s = str(rate).partition("/")
            num, den = float(num_s), float(den_s) if den_s else 1.0
        return num / den if den > 0 else None
    except Exception:
        return None


def _parse_bits(video_stream: Dict) -> int:
    """位深：bits_per_raw_sample 优先（实测可能是 'N/A'），回退从 pix_fmt 名解析。"""
    try:
        n = int(str(video_stream.get("bits_per_raw_sample")).strip())
        if n in (8, 10, 12, 14, 16):
            return n
    except (TypeError, ValueError):
        pass
    pf = (video_stream.get("pix_fmt") or "").lower()
    for token, bits in (("p016le", 16), ("p014le", 14), ("p012le", 12),
                        ("p010le", 10), ("p16le", 16), ("p12le", 12),
                        ("p10le", 10)):
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
    for sd in (stream.get("side_data_list") or []):
        if sd.get("rotation") is not None:
            return _norm_rotation(sd["rotation"])
    tags = stream.get("tags") or {}
    for key in ("rotate", "rotation"):
        if key in tags:
            return _norm_rotation(tags[key])
    return 0


def _frac_to_float(value) -> Optional[float]:
    """ffprobe 有理数：'34000/50000' / [34000, 50000] / 0.68 → float。"""
    try:
        if isinstance(value, (list, tuple)):
            num, den = float(value[0]), float(value[1])
        else:
            s = str(value)
            if "/" in s:
                a, _, b = s.partition("/")
                num, den = float(a), float(b)
            else:
                num, den = float(s), 1.0
        return num / den if den else None
    except (TypeError, ValueError):
        return None


def _extract_hdr_from_side_data(sd_list: List[Dict]) -> Dict:
    """
    解析 HDR10 静态元数据。

    ffprobe 6.1 实测：
      Mastering display metadata → 'Content light level metadata' 只在**帧级**
      side_data 暴露（-show_frames），字段名是扁平的 red_x/green_x/.../
      white_point_x/max_luminance；老版本/部分容器则给出 display_primaries 数组。
      两种形态都要兼容，解析不出就返回空，调用方优雅降级。
    """
    out: Dict = {"master_display": None, "max_cll": None}
    for sd in sd_list or []:
        stype = (sd.get("side_data_type") or "").lower()
        if "mastering display" in stype:
            # 老版本/部分容器给出 display_primaries 数组，先摊平成 red_x/... 形态
            if "red_x" not in sd and (sd.get("display_primaries")
                                      or sd.get("display_primaries_rgb")
                                      or sd.get("white_point")):
                prim = sd.get("display_primaries") or sd.get("display_primaries_rgb") or []
                wpt = sd.get("white_point") or []
                flat: Dict = {}
                for name, item in (("red", prim[0] if len(prim) > 0 else None),
                                   ("green", prim[1] if len(prim) > 1 else None),
                                   ("blue", prim[2] if len(prim) > 2 else None),
                                   ("white_point", wpt or None)):
                    if item is None:
                        continue
                    vals = item if isinstance(item, (list, tuple)) \
                        else (item.get("x"), item.get("y"))
                    try:
                        flat[name + "_x"] = vals[0]
                        flat[name + "_y"] = vals[1]
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

            r = (_chroma("red_x"), _chroma("red_y"))
            g = (_chroma("green_x"), _chroma("green_y"))
            b = (_chroma("blue_x"), _chroma("blue_y"))
            wp = (_chroma("white_point_x"), _chroma("white_point_y"))
            mx, mn = _luma("max_luminance"), _luma("min_luminance")
            if None not in (*r, *g, *b, *wp) and mx is not None and mn is not None:
                # x265 语法顺序为 G()B()R()
                out["master_display"] = (
                    f"G({g[0]},{g[1]})B({b[0]},{b[1]})R({r[0]},{r[1]})"
                    f"WP({wp[0]},{wp[1]})L({mx},{mn})"
                )
        elif "content light level" in stype:
            max_c = sd.get("max_content")
            avg = sd.get("max_average", sd.get("max_pic_average"))
            if max_c is not None and avg is not None:
                try:
                    out["max_cll"] = f"{int(max_c)},{int(avg)}"
                except (TypeError, ValueError):
                    pass
    return out


def _extract_hdr(stream: Dict) -> Dict:
    return _extract_hdr_from_side_data(stream.get("side_data_list") or [])


def _looks_hdr(video_stream: Dict, src_bits: int) -> bool:
    """判断是否需要为 HDR 静态元数据额外做一次帧级探测。"""
    if src_bits < 10:
        return False
    trc = (video_stream.get("color_transfer") or "").lower()
    prim = (video_stream.get("color_primaries") or "").lower()
    return trc in ("smpte2084", "arib-std-b67", "smpte2084-hdr10") or prim == "bt2020"


def _probe_frame_side_data(path: str) -> List[Dict]:
    """
    读首帧的 side_data（HDR10 的 mastering display / content light level 在
    ffmpeg 6.1 只在帧级暴露）。只读 1 帧，开销可忽略；失败返回空列表。
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-select_streams", "v:0", "-show_frames",
           "-read_intervals", "%+#1", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20, check=False)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        for frame in data.get("frames") or []:
            sd = frame.get("side_data_list")
            if sd:
                return sd
    except Exception:
        pass
    return []


def probe_full_metadata(video_file, errors: Optional[List[str]] = None) -> Optional[Dict]:
    """
    一次 ffprobe 拿到 format.tags / 各流 tags+disposition / side_data / pix_fmt /
    bits_per_raw_sample / SAR / 帧率 / 章节，并按 abspath|size|mtime 缓存。

    Args:
        video_file: 视频路径（str 或 Path）。
        errors: 可选的收集器，失败时写入错误信息（供调用方生成提示）。

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

    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", "-show_chapters", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15, check=True)
        data = json.loads(r.stdout)
    except Exception as exc:
        if errors is not None:
            errors.append(str(exc))
        return None

    streams = data.get("streams") or []
    video = next((s for s in streams
                  if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        if errors is not None:
            errors.append("no video stream")
        return None

    rotation = _extract_rotation(video)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    derived = {
        "rotation": rotation,
        "width": width,
        "height": height,
        # 显示尺寸：90/270 度旋转时宽高互换
        "effective_width": height if rotation in (90, 270) else width,
        "effective_height": width if rotation in (90, 270) else height,
        "pix_fmt": video.get("pix_fmt") or "",
        "src_bits": _parse_bits(video),
        "video_index": int(video.get("index") or 0),
        "cover_indices": [int(s["index"]) for s in streams
                          if (s.get("disposition") or {}).get("attached_pic")],
        "subtitle_codecs": [s.get("codec_name") for s in streams
                            if s.get("codec_type") == "subtitle"],
    }
    hdr = _extract_hdr(video)
    if not (hdr["master_display"] or hdr["max_cll"]) \
            and _looks_hdr(video, derived["src_bits"]):
        # 6.1 下 HDR 静态元数据只在帧级 side_data 暴露，仅在色彩特征像 HDR 时额外探测
        hdr = _extract_hdr_from_side_data(_probe_frame_side_data(path))
    derived.update(hdr)
    derived["is_hdr"] = bool(hdr["master_display"] or hdr["max_cll"])

    meta = {
        "path": path,
        "format": {
            "format_name": (data.get("format") or {}).get("format_name") or "",
            "duration": (data.get("format") or {}).get("duration") or "",
            "bit_rate": (data.get("format") or {}).get("bit_rate") or "",
            "tags": dict((data.get("format") or {}).get("tags") or {}),
        },
        "video": video,
        "streams": streams,
        "chapters": data.get("chapters") or [],
        "derived": derived,
    }
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[key] = meta
    return meta


def _ffmpeg_major(ffmpeg_bin: str = "ffmpeg") -> int:
    """ffmpeg 主版本号（用于选择旋转写法）；探测失败按 6 处理。"""
    global _FFMPEG_MAJOR
    if _FFMPEG_MAJOR is not None:
        return _FFMPEG_MAJOR
    try:
        out = subprocess.run([ffmpeg_bin, "-version"], capture_output=True,
                             text=True, timeout=10).stdout
        import re
        m = re.search(r"ffmpeg version (\d+)\.", out)
        _FFMPEG_MAJOR = int(m.group(1)) if m else 6
    except Exception:
        _FFMPEG_MAJOR = 6
    return _FFMPEG_MAJOR


def resolve_pix_fmt_for_source(codec: str, src_bits: int,
                               warn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    源为 10bit+ 时选择保持位深的目标 pix_fmt；8bit 源返回 None（沿用各文件默认策略）。
    """
    if src_bits < 10:
        return None
    c = (codec or "").lower()
    if c in _ENCODERS_8BIT_ONLY:
        if warn:
            warn(f"源为 {src_bits}bit，编码器 {c} 不支持 10bit，已降级为 yuv420p（高光可能出现色带）")
        return "yuv420p"
    return _PIXFMT_10BIT_BY_ENCODER.get(c, "yuv420p10le")


def resolve_subtitle_codec(src_subs: List[Optional[str]], container: str,
                           warn: Optional[Callable[[str], None]] = None):
    """
    按「源字幕 codec × 目标容器」决定字幕编码方式。

    Returns:
        (codec_or_None, need_map)：need_map=False 表示不要映射字幕流。

    注意：mp4 里 -c:s copy 对 subrip / ass 会硬失败（Could not find tag for codec），
    位图字幕则根本无法转换，必须丢弃并告警。
    """
    ctr = (container or "").lower().lstrip(".")
    subs = [s for s in (src_subs or []) if s]
    if not subs:
        return None, False
    if ctr in ("mkv", "webm"):
        if ctr == "mkv":
            return "copy", True
        return ("copy", True) if all(s == "webvtt" for s in subs) else (None, False)
    if ctr in _MP4_FAMILY:
        if any(s in _BITMAP_SUBS for s in subs):
            if warn:
                warn("位图字幕（PGS/DVDSUB 等）无法写入 mp4，已丢弃")
            return None, False
        if warn and any(s in ("ass", "ssa") for s in subs):
            warn("ASS/SSA 转为 mov_text 后会丢失样式")
        return "mov_text", True
    return None, False


# WebM 只接受 Vorbis / Opus 音轨。VP9 / AV1 的默认容器就是 .webm，而绝大多数
# 片源的音轨是 AAC——实测 `-c:a copy` 直接写头失败：
#   "Only VP8 or VP9 or AV1 video and Vorbis or Opus audio and WebVTT subtitles
#    are supported for WebM."
# 因此遇到 .webm 输出时必须把非 Opus/Vorbis 音轨转成 Opus，而不是让整条命令失败。
_WEBM_AUDIO_CODECS = {"opus", "vorbis"}
_WEBM_AUDIO_FALLBACK = "libopus"


def _src_audio_codecs(meta: Optional[Dict]) -> List[str]:
    """取源文件的音频编码列表；meta 为 None（探测失败）时返回空列表。"""
    if not meta:
        return []
    return [s.get("codec_name") for s in (meta.get("streams") or [])
            if s.get("codec_type") == "audio" and s.get("codec_name")]


def resolve_audio_codec_for_container(audio_codec: str,
                                      container_ext: str,
                                      meta: Optional[Dict],
                                      warn: Optional[Callable[[str], None]] = None) -> str:
    """
    按目标容器修正音频编码方式（目前只有 WebM 需要干预）。

    Args:
        audio_codec: 用户指定的 --audio-codec（'copy' 表示流复制）。
        container_ext: 输出容器扩展名（含点，如 '.webm'）。
        meta: probe_full_metadata 结果，用于判断源音轨能否直接复制。
        warn: 告警回调。

    Returns:
        实际应下发的音频编码器名称。
    """
    if (container_ext or "").lower() != ".webm":
        return audio_codec

    c = (audio_codec or "copy").lower()
    if c == "copy":
        srcs = _src_audio_codecs(meta)
        if not srcs:
            # 探测不到音轨信息（含探测失败）：-c:a libopus 在无音轨时同样无害，
            # 故按"可能不兼容"处理，宁可多一次转码也不要写头失败。
            if warn:
                warn("输出为 .webm 但无法确认源音轨格式，音频改用 "
                     f"{_WEBM_AUDIO_FALLBACK} 重编码以确保可写入")
            return _WEBM_AUDIO_FALLBACK
        if all(s.lower() in _WEBM_AUDIO_CODECS for s in srcs):
            return audio_codec
        if warn:
            warn(f"WebM 只支持 Opus/Vorbis 音轨，源音轨为 {' / '.join(srcs)}，"
                 f"已改用 {_WEBM_AUDIO_FALLBACK} 重编码")
        return _WEBM_AUDIO_FALLBACK

    base = c.split("_")[-1] if c.startswith("lib") else c
    if base not in _WEBM_AUDIO_CODECS:
        if warn:
            warn(f"WebM 只支持 Opus/Vorbis 音轨，--audio-codec {audio_codec} "
                 f"不适用，已改用 {_WEBM_AUDIO_FALLBACK}")
        return _WEBM_AUDIO_FALLBACK
    return audio_codec


def build_hdr_args(meta: Dict, codec: str,
                   warn: Optional[Callable[[str], None]] = None) -> List[str]:
    """
    HDR10 静态元数据写入。色彩三参数由 build_color_args 从源透传，此处不重复指定。

    - libx265：显式 -x265-params master-display / max-cll / hdr10=1，可靠。
    - NVENC：依赖帧 side_data 自动传播；走 CPU 回退链路时会丢失，故告警。
    - 其余编码器：只能保住色彩三参数与位深。
    """
    d = meta["derived"]
    if not d["is_hdr"]:
        return []
    c = (codec or "").lower()
    out: List[str] = []
    if c == "libx265" and d["master_display"]:
        params = ["master-display=" + d["master_display"]]
        if d["max_cll"]:
            params.append("max-cll=" + d["max_cll"])
        params.append("hdr10=1")
        out += ["-x265-params", ":".join(params)]
    elif c.endswith("_nvenc"):
        # 实测（ffmpeg 6.1 + Tesla T4）：NVENC 不写入 mastering display / MaxCLL，
        # hevc_metadata bsf 也无此能力，属编码器封装限制。
        if warn and d["master_display"]:
            warn("NVENC 不写入 mastering display / MaxCLL，HDR10 静态元数据会丢失"
                 "（色彩三参数与 10bit 位深仍保留）；如需完整 HDR10 元数据请用 libx265")
    elif warn and d["master_display"]:
        warn(f"编码器 {c} 无法写入 mastering display / MaxCLL，仅保留色彩三参数与位深")
    return out


def build_aspect_args(meta: Dict) -> List[str]:
    """
    仅当源为变形（SAR≠1:1）时显式 -aspect 保持源 DAR。
    方像素源不加：crop 后 ffmpeg 保持 SAR 自动算出正确的新 DAR。
    """
    sar = meta["video"].get("sample_aspect_ratio") or "1:1"
    try:
        n_s, _, d_s = str(sar).partition(":")
        n, d = int(n_s), int(d_s) if d_s else 1
    except (ValueError, TypeError):
        return []
    if n <= 0 or d <= 0 or (n == 1 and d == 1):
        return []
    src_w, src_h = meta["derived"]["width"], meta["derived"]["height"]
    if not src_w or not src_h:
        return []
    from math import gcd
    dan, dad = n * src_w, d * src_h
    g = gcd(dan, dad) or 1
    return ["-aspect", f"{dan // g}/{dad // g}"]


def build_preserve_args(meta: Optional[Dict], container: str, codec: str,
                        warn: Optional[Callable[[str], None]] = None) -> Dict[str, List[str]]:
    """
    生成保留原片元数据所需的 ffmpeg 参数，按位置分成三组：

      input: 必须放在 -i 之前（-noautorotate / -display_rotation）
      map  : 紧跟 -i 之后（流映射、-map_metadata、-map_chapters、creation_time、-aspect）
      post : 放在编码器选项之后（封面/字幕的逐流 codec，需覆盖 -c:v 通用设置）

    meta 为 None（探测失败）时三组均为空，调用方回退原有窄映射行为。
    """
    empty: Dict[str, List[str]] = {"input": [], "map": [], "post": []}
    if meta is None:
        return empty

    d = meta["derived"]
    ctr = (container or "").lower().lstrip(".")
    inp: List[str] = ["-noautorotate"]      # 不烘焙旋转，保留 display matrix
    mp: List[str] = []
    post: List[str] = []

    # 旋转：6.x+ 用 input 侧 -display_rotation（6.1 无流说明符，作用于后续 -i 的
    # 整个文件，此处只有一个输入故安全）；更老版本用 mov 的 rotate tag
    if d["rotation"]:
        if _ffmpeg_major() >= 6:
            inp += ["-display_rotation", str(d["rotation"])]
        else:
            post += ["-metadata:s:v:0", f"rotate={d['rotation']}"]

    # 主视频轨用绝对索引，天然避开 mp4 封面轨（attached_pic）
    mp += ["-map", f"0:{d['video_index']}"]
    # 封面轨单独映射，且必须显式 copy（否则单帧 PNG/MJPEG 会被送去编码而失败）
    for i, ci in enumerate(d["cover_indices"][:1]):
        mp += ["-map", f"0:{ci}"]
        post += [f"-c:v:{i + 1}", "copy", f"-disposition:v:{i + 1}", "attached_pic"]
    mp += ["-map", "0:a?"]

    sub_codec, need_sub = resolve_subtitle_codec(d["subtitle_codecs"], ctr, warn)
    if need_sub:
        mp += ["-map", "0:s?"]
        if sub_codec:
            post += ["-c:s", sub_codec]
    if ctr in ("mkv", "webm"):
        mp += ["-map", "0:t?"]

    # -metadata 会覆盖 -map_metadata，故 creation_time 必须放在其后
    mp += ["-map_metadata", "0", "-map_chapters", "0"]
    creation_time = (meta["format"].get("tags") or {}).get("creation_time")
    if creation_time:
        mp += ["-metadata", f"creation_time={creation_time}"]

    mp += build_aspect_args(meta)
    return {"input": inp, "map": mp, "post": post}


# ═══════════════════════════════════════════════════════════════════
#  FFprobe
# ═══════════════════════════════════════════════════════════════════

def ffprobe_info(
    path: Path,
    original_width: Optional[int] = None,
    original_height: Optional[int] = None,
) -> Dict:
    """
    探测视频基本信息。走 probe_full_metadata（带缓存），因此同一文件重复调用
    只会真正执行一次 ffprobe。

    返回值保持 width/height/duration/nb_frames 四个既有键不变（下游依赖），
    并追加元数据保留所需的字段与全量结果 meta。
    """
    errors: List[str] = []
    meta = probe_full_metadata(path, errors=errors)

    if meta is None:
        msg = errors[0] if errors else "ffprobe 探测失败"
        if original_width and original_height:
            return {
                "width": original_width,
                "height": original_height,
                "duration": 0.0,
                "nb_frames": 0,
                "probe_warning": msg,
            }
        return {"error": msg}

    video = meta["video"]
    d = meta["derived"]

    width = d["width"] if original_width is None else original_width
    height = d["height"] if original_height is None else original_height

    duration = 0.0
    for candidate in (meta["format"].get("duration"), video.get("duration")):
        try:
            duration = float(candidate or 0)
            if duration > 0:
                break
        except (TypeError, ValueError):
            pass

    nb_frames = 0
    try:
        nb_frames = int(video.get("nb_frames", 0) or 0)
    except (TypeError, ValueError):
        nb_frames = 0

    if nb_frames == 0 and duration > 0:
        fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        if fps and fps > 0:
            nb_frames = int(duration * fps)

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "nb_frames": nb_frames,
        # ↓ [META-KEEP] 元数据保留所需
        "rotation": d["rotation"],
        "effective_width": d["effective_width"],
        "effective_height": d["effective_height"],
        "src_bits": d["src_bits"],
        "pix_fmt": d["pix_fmt"],
        "video_index": d["video_index"],
        "cover_indices": d["cover_indices"],
        "subtitle_codecs": d["subtitle_codecs"],
        "is_hdr": d["is_hdr"],
        "creation_time": (meta["format"].get("tags") or {}).get("creation_time"),
        "meta": meta,
    }


# ═══════════════════════════════════════════════════════════════════
#  [COLOR-FIX] 色彩元数据注入
# ═══════════════════════════════════════════════════════════════════

def probe_color_metadata(video_file: Path) -> Optional[Dict[str, str]]:
    """
    用 ffprobe 读取视频第一个视频流的色彩元数据
    （color_range / color_space / color_primaries / color_transfer）。

    Args:
        video_file: 视频文件路径。

    Returns:
        四项色彩值的字典（可能为 'unknown'）；无法探测时返回 None。
    """
    m = probe_full_metadata(video_file)
    if not m:
        return None
    v = m["video"]
    return {k: v.get(k, "unknown") for k in
            ("color_range", "color_space", "color_primaries", "color_transfer")}


def _effective_source_range(meta: Dict) -> str:
    """源的实际 color_range：有值取源值，unknown 时按 tv（与 ffmpeg 默认解释一致）。"""
    v, d = meta["video"], meta["derived"]
    rng = (v.get("color_range") or "").lower()
    if rng in ("tv", "pc"):
        return rng
    return "pc" if d["pix_fmt"].lower().startswith("yuvj") else "tv"


def build_range_convert_filter(meta: Optional[Dict], color_range: Optional[str],
                               warn: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    --color-range 的配套实现：把像素值域真正转到目标 range。

    只在「显式强制 --color-range tv/pc」且「与源实际 range 不同」时才需要转换——
    否则只改标签会让解码映射错位（limited 内容标 pc → 暗部抬升、高光压缩）。
    auto / 未指定 / 与源相同 时不插入任何滤镜，零额外开销。

    Returns:
        scale 滤镜字符串；无需转换时返回 None。
    """
    if meta is None or not color_range or color_range.lower() not in ("tv", "pc"):
        return None
    src, tgt = _effective_source_range(meta), color_range.lower()
    if src == tgt:
        return None
    if warn:
        warn(f"color_range 由源 {src} 转换为 {tgt}（插入 scale 滤镜做实际值域转换）")
    return f"scale=w=iw:h=ih:in_range={src}:out_range={tgt}"


def build_color_args(video_file: Path, meta: Optional[Dict] = None,
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
        meta: 可选的 probe_full_metadata 结果，传入可避免重复 ffprobe。
        color_range: 显式 range 覆盖（'tv' / 'pc' / 'auto' / None）。

    Returns:
        ffmpeg 参数列表；探测失败时返回空列表（让编码器自行决定，不瞎猜）。
    """
    m = meta if meta is not None else probe_full_metadata(video_file)
    if not m:
        return []

    v = m["video"]
    d = m["derived"]

    def _val(key: str) -> Optional[str]:
        x = (v.get(key) or "").lower()
        return None if x in ("", "unknown", "unspecified", "n/a") else x

    space = _val("color_space")
    prim = _val("color_primaries")
    trc = _val("color_transfer")
    rng = _val("color_range")

    if rng is None:
        # auto：只有 yuvj* 才是真的 full range；其余一律按 tv（limited）
        rng = "pc" if d["pix_fmt"].lower().startswith("yuvj") else "tv"
    if color_range and color_range.lower() in ("tv", "pc"):
        rng = color_range.lower()          # 显式覆盖优先于探测值与 auto 推断

    if space is None or prim is None or trc is None:
        if d["src_bits"] >= 10 and (d["width"] >= 1920 or d["height"] >= 1080):
            guess = ("bt2020nc", "bt2020", "bt709")
        elif d["height"] >= 720:
            guess = ("bt709", "bt709", "bt709")
        elif d["src_bits"] >= 10:
            # 高位深的小分辨率内容基本不存在标清广播电视色彩，按 bt709 更合理
            guess = ("bt709", "bt709", "bt709")
        else:
            fps = _parse_rate(v.get("avg_frame_rate") or v.get("r_frame_rate"))
            is_pal = fps is not None and (abs(fps - 25) < 0.3 or abs(fps - 50) < 0.3)
            guess = ("bt470bg", "bt470bg", "bt470bg") if is_pal else \
                    ("smpte170m", "smpte170m", "smpte170m")
        space = space or guess[0]
        prim = prim or guess[1]
        trc = trc or guess[2]

    # 输出端 -color_trc 不接受 bt470bg/bt470m，需换成 libavutil 规范名
    trc = _TRC_OUTPUT_NAMES.get(trc, trc)

    return [
        "-colorspace", space,
        "-color_primaries", prim,
        "-color_trc", trc,
        "-color_range", rng,
    ]


def _setparams_from_color_args(extra_args: List[str]) -> Optional[str]:
    """
    从 build_color_args 生成的色彩参数列表中提取取值，构造 setparams 滤镜字符串。

    原因：libx264 等编码器对输出端 -color_primaries/-color_trc 参数不写入 VUI，
    需用 setparams 滤镜显式注入帧级色彩属性（GPU 编码器则靠输出端参数即可）。

    Args:
        extra_args: ffmpeg 输出端参数列表（含 -colorspace/-color_primaries 等）。

    Returns:
        setparams 滤镜字符串（如 'setparams=colorspace=bt709:color_primaries=bt709:...'），
        无色彩参数时返回 None。
    """
    color_map = {
        "-colorspace": "colorspace",
        "-color_primaries": "color_primaries",
        "-color_trc": "color_trc",
        "-color_range": "range",
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
    return "setparams=" + ":".join(f"{k}={v}" for k, v in vals.items())


# ═══════════════════════════════════════════════════════════════════
#  滤镜
# ═══════════════════════════════════════════════════════════════════

def build_crop_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    if dst_w > src_w or dst_h > src_h:
        raise ValueError(
            f"crop 模式下目标尺寸 ({dst_w}x{dst_h}) 不能大于源尺寸 ({src_w}x{src_h})"
        )

    x = (src_w - dst_w) // 2
    y = (src_h - dst_h) // 2
    return f"crop={dst_w}:{dst_h}:{x}:{y}"


def build_cover_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    if src_w <= 0 or src_h <= 0:
        return f"scale={dst_w}:{dst_h}"

    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h

    if abs(src_ratio - dst_ratio) < 1e-3:
        return f"scale={dst_w}:{dst_h}"

    if src_ratio > dst_ratio:
        return f"scale=-2:{dst_h},crop={dst_w}:{dst_h}:(iw-{dst_w})/2:0"

    return f"scale={dst_w}:-2,crop={dst_w}:{dst_h}:0:(ih-{dst_h})/2"


def build_video_filter(mode: str, src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    if mode == "crop":
        return build_crop_filter(src_w, src_h, dst_w, dst_h)
    if mode == "cover":
        return build_cover_filter(src_w, src_h, dst_w, dst_h)
    raise ValueError(f"未知处理模式：{mode}")


# ═══════════════════════════════════════════════════════════════════
#  输入输出任务
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Job:
    src: Path
    dst: Path
    info: Dict = field(default_factory=dict)
    status: str = "pending"   # pending / running / done / failed / skipped
    progress: float = 0.0
    fps: float = 0.0
    peak_fps: float = 0.0
    speed: str = ""
    frame: int = 0
    total_frames: int = 0
    elapsed: float = 0.0
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def name(self) -> str:
        return self.src.name


def collect_video_files(input_path: Path, recursive: bool) -> List[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_EXTS:
            return [input_path]
        return []

    if input_path.is_dir():
        iterator = input_path.rglob("*") if recursive else input_path.iterdir()
        return sorted({
            p for p in iterator
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        })

    return []


def make_output_file(
    src: Path,
    output_path: Path,
    codec: str,
    container: Optional[str],
    batch_mode: bool,
    input_root: Optional[Path],
    flag: Optional[str] = None,
    mode: str = "crop",
) -> Path:
    if batch_mode or output_path.is_dir() or not output_path.suffix:
        ext = container if container else get_extension_from_codec(codec)
        if ext is None:
            ext = src.suffix

        out_dir = output_path
        if input_root and input_root.is_dir():
            try:
                rel_parent = src.parent.relative_to(input_root)
                out_dir = out_dir / rel_parent
            except Exception:
                pass

        out_dir.mkdir(parents=True, exist_ok=True)
        # 未指定 --flag 时与 vidcrop_hwaccel.py 一致：cover 模式 _covered，否则 _cropped
        suffix = flag if flag else ("_covered" if mode == "cover" else "_cropped")
        return out_dir / f"{src.stem}{suffix}{ext}"

    dst = output_path
    if container:
        dst = dst.with_suffix(container)

    dst.parent.mkdir(parents=True, exist_ok=True)

    if container is None and codec.lower() != "copy":
        if not check_container_compatibility(dst.suffix, codec):
            rec = get_extension_from_codec(codec)
            print(
                f"警告：输出扩展名 '{dst.suffix}' 可能与编码器 '{codec}' 不兼容，"
                f"推荐使用 '{rec}'",
                file=sys.stderr,
            )

    return dst


def collect_jobs(
    input_path: Path,
    output_path: Path,
    codec: str,
    container: Optional[str],
    overwrite: bool,
    recursive: bool,
    crop_ratio_num: Optional[int] = None,
    crop_ratio_den: Optional[int] = None,
    flag: Optional[str] = None,
    mode: str = "crop",
) -> Tuple[List[Job], bool, Path]:
    if not input_path.exists():
        print(f"[ERROR] 输入路径不存在：{input_path}", file=sys.stderr)
        sys.exit(2)

    sources = collect_video_files(input_path, recursive)
    if not sources:
        return [], False, output_path

    batch_mode = len(sources) > 1 or input_path.is_dir()

    if batch_mode and output_path.suffix:
        print(
            f"警告：批量处理时输出路径 '{output_path}' 带扩展名，将视为目录。",
            file=sys.stderr,
        )
        output_path = output_path.with_suffix("")

    if batch_mode and input_path.is_dir() and _same_path(input_path, output_path):
        print("[ERROR] 批量模式下 --input 和 --output 不能为同一目录，避免覆盖源文件。", file=sys.stderr)
        sys.exit(2)

    input_root = input_path if input_path.is_dir() else input_path.parent

    if flag and not batch_mode and output_path.suffix:
        print("提示：--output 已指定完整文件名，--flag 不生效。", file=sys.stderr)

    jobs: List[Job] = []
    for src in sources:
        dst = make_output_file(
            src=src,
            output_path=output_path,
            codec=codec,
            container=container,
            batch_mode=batch_mode,
            input_root=input_root,
            flag=flag,
            mode=mode,
        )

        if _same_path(src, dst):
            print(f"[ERROR] 输出文件与输入文件相同，拒绝覆盖源文件：{src}", file=sys.stderr)
            sys.exit(2)

        if dst.exists() and not overwrite:
            jobs.append(Job(
                src=src, dst=dst, status="skipped", progress=1.0,
                error="已存在，--overwrite 可覆盖",
            ))
        else:
            jobs.append(Job(src=src, dst=dst))

    return jobs, batch_mode, output_path


# ═══════════════════════════════════════════════════════════════════
#  FFmpeg 命令与进度
# ═══════════════════════════════════════════════════════════════════

def build_ffmpeg_cmd(
    src: Path,
    dst: Path,
    dst_w: int,
    dst_h: int,
    src_w: int,
    src_h: int,
    mode: str,
    codec: str,
    crf: Optional[int],
    cq: Optional[int],
    preset: str,
    pix_fmt: Optional[str],
    threads: int,
    overwrite: bool,
    audio_codec: str,
    audio_bitrate: str,
    extra_args: List[str],
    warn: Callable[[str], None],
    info: Optional[Dict] = None,
    keep_metadata: bool = True,
    color_range: Optional[str] = None,
) -> List[str]:
    if codec.lower() == "copy":
        raise ValueError("使用视频滤镜时不能使用 -c:v copy，请改用 libx264 / libx265 等编码器")

    vf = build_video_filter(mode, src_w, src_h, dst_w, dst_h)

    # [META-KEEP] 复用 ffprobe_info 的探测结果（带缓存），拿不到时自行探测一次
    meta = (info or {}).get("meta") if info else None
    if meta is None and keep_metadata:
        meta = probe_full_metadata(src)

    # [COLOR-FIX] 色彩元数据注入：有值透传，unknown 按分辨率/位深/帧率推断。
    # libx264 等软件编码器对输出端 -color_primaries/-color_trc 不写 VUI，
    # 需在滤镜链末尾追加 setparams 显式注入帧级色彩属性。
    color_args = build_color_args(src, meta=meta, color_range=color_range)
    # 强制 --color-range tv|pc 且与源实际值域不同 → 自动做真正的像素值域转换
    _conv = build_range_convert_filter(meta, color_range, warn=warn)
    if _conv:
        vf = f"{vf},{_conv}"
    _sp = _setparams_from_color_args(color_args)
    if _sp:
        vf = f"{vf},{_sp}"

    # [META-KEEP] 输入侧选项（旋转不烘焙）、流映射、逐流 codec
    pres = build_preserve_args(meta, dst.suffix, codec, warn=warn) if keep_metadata \
        else {"input": [], "map": [], "post": []}
    if not pres["map"]:
        pres["map"] = ["-map", "0:v:0", "-map", "0:a?"]

    cmd: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
    ]
    cmd += pres["input"]                 # -noautorotate / -display_rotation（须在 -i 前）
    cmd += [
        "-err_detect", "ignore_err",
        "-fflags", "+genpts+discardcorrupt",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-i", str(src),
    ]
    cmd += pres["map"]                   # 流映射 / -map_metadata / -map_chapters / creation_time
    # 必须用 -filter:v:0 而非 -vf：映射了封面轨（attached_pic）时 -vf 会作用到
    # 所有输出视频流，与封面的 -c:v:N copy 冲突（filtering and streamcopy 互斥）。
    cmd += ["-filter:v:0" if keep_metadata and meta is not None else "-vf", vf]

    # 编码器选项 (支持 crf 和 cq)
    cmd += build_encoder_options_v2(codec, crf, cq, preset, pix_fmt, warn)
    cmd += pres["post"]                  # 封面/字幕逐流 codec，需覆盖上面的 -c:v
    cmd += ["-threads", str(max(1, threads))]

    # [META-KEEP] HDR10 静态元数据（libx265 走 -x265-params，其余尽力而为）
    if meta is not None:
        cmd += build_hdr_args(meta, codec, warn)

    # WebM 容器不接受 AAC 等音轨，必要时自动改 Opus 重编码
    audio_codec = resolve_audio_codec_for_container(audio_codec, dst.suffix, meta, warn)
    if audio_codec.lower() == "copy":
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", audio_codec]
        if audio_bitrate:
            cmd += ["-b:a", audio_bitrate]

    if dst.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd += ["-movflags", "+faststart"]

    if extra_args:
        cmd += extra_args

    # [COLOR-FIX] 输出端色彩参数（写入容器 colr box / 编码器 VUI）；
    # 置于 extra_args 之后，与主项目合并注入行为一致。
    cmd += color_args

    cmd.append(str(dst))
    return cmd


def _inject_progress(cmd: List[str]) -> List[str]:
    prog_cmd = list(cmd)
    try:
        i_idx = prog_cmd.index("-i")
        prog_cmd[i_idx:i_idx] = ["-progress", "pipe:1", "-nostats"]
    except ValueError:
        prog_cmd += ["-progress", "pipe:1", "-nostats"]
    return prog_cmd


def run_ffmpeg_with_progress(
    cmd: List[str],
    job: Job,
    on_update: Optional[Callable[[Job], None]] = None,
) -> Tuple[int, str]:
    job.started_at = time.time()
    total_frames = int(job.info.get("nb_frames", 0) or 0)
    duration = float(job.info.get("duration", 0.0) or 0.0)
    job.total_frames = total_frames

    stderr_lines: List[str] = []
    proc: Optional[subprocess.Popen] = None
    last_callback = 0.0

    def maybe_update(force: bool = False) -> None:
        nonlocal last_callback
        now = time.time()
        if force or now - last_callback >= 0.25:
            job.elapsed = now - job.started_at
            last_callback = now
            if on_update:
                on_update(job)

    try:
        proc = subprocess.Popen(
            _inject_progress(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # 同 vidcrop_hwaccel.py：ffmpeg 默认开 stdin 交互线程，父进程 stdin
            # 若是不关闭、无数据的管道会让它阻塞在 read() 上（0% CPU，且 Python
            # 的 timeout= 也兜不住）。命令虽已带 -nostdin，这里再彻底切断。
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        _register_proc(proc)

        assert proc.stdout is not None
        assert proc.stderr is not None

        def _drain_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line.rstrip())
                if len(stderr_lines) > 500:
                    del stderr_lines[:250]

        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_err.start()

        for raw in proc.stdout:
            line = raw.strip()
            if "=" not in line:
                continue

            key, _, val = line.partition("=")
            val = val.strip()

            if key == "frame":
                try:
                    f = int(val)
                    job.frame = max(job.frame, f)
                    if total_frames > 0:
                        job.progress = min(1.0, max(job.progress, f / total_frames))
                except ValueError:
                    pass

            elif key == "out_time_ms":
                try:
                    us = int(val)
                    if us >= 0 and duration > 0:
                        sec = us / 1_000_000.0
                        job.progress = min(1.0, max(job.progress, sec / duration))
                except (ValueError, TypeError):
                    pass

            elif key == "fps":
                try:
                    f = float(val)
                    # FFmpeg 收尾行会报 fps=0，直接采信会让实时 fps 归零，
                    # 故只采纳非零值。
                    if f > 0:
                        job.fps = f
                        if job.fps > job.peak_fps:
                            job.peak_fps = job.fps
                except ValueError:
                    pass

            elif key == "speed":
                job.speed = val

            elif key == "progress":
                if val == "end":
                    job.progress = 1.0
                    maybe_update(force=True)

            maybe_update()

        rc = proc.wait()
        t_err.join(timeout=3.0)

        job.finished_at = time.time()
        job.elapsed = job.finished_at - job.started_at
        if rc == 0:
            job.progress = 1.0

        maybe_update(force=True)
        return rc, "\n".join(stderr_lines[-30:])

    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except Exception:
                pass
        return 130, "interrupted"

    except Exception as exc:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        return 1, str(exc)

    finally:
        if proc is not None:
            _unregister_proc(proc)
        job.finished_at = time.time()
        if job.started_at:
            job.elapsed = job.finished_at - job.started_at


# ═══════════════════════════════════════════════════════════════════
#  进度显示
# ═══════════════════════════════════════════════════════════════════

_BAR_WIDTH = 24


def _bar(p: float, width: int = _BAR_WIDTH) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(width * p))
    return "█" * filled + "░" * (width - filled)


class AggregatePanel:
    def __init__(self, jobs: List[Job]):
        self.jobs = jobs
        self.total = len(jobs)
        self.lock = threading.Lock()
        self.start_ts = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._last_lines = 0
        self._events: List[str] = []

    def emit(self, msg: str) -> None:
        with self.lock:
            self._events.append(msg)
            if len(self._events) > 6:
                self._events.pop(0)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._render(final=True)

    def _aggregate(self) -> Tuple[float, int, int, int, int, int, float]:
        done = failed = skipped = running = pending = 0
        prog_sum = fps_sum = 0.0

        for j in self.jobs:
            if j.status == "done":
                done += 1
                prog_sum += 1.0
            elif j.status == "failed":
                failed += 1
                prog_sum += 1.0
            elif j.status == "skipped":
                skipped += 1
                prog_sum += 1.0
            elif j.status == "running":
                running += 1
                prog_sum += j.progress
                fps_sum += j.fps          # 累加各 worker 实时 fps
            else:
                pending += 1

        overall = prog_sum / self.total if self.total else 1.0
        return overall, done, failed, skipped, running, pending, fps_sum

    def _render(self, final: bool = False) -> None:
        with self.lock:
            overall, done, failed, skipped, running, pending, fps_total = self._aggregate()
            elapsed = time.time() - self.start_ts
            events = list(self._events)

        if self._last_lines > 0:
            sys.stdout.write(f"\033[{self._last_lines}A")
            sys.stdout.write("\033[J")

        fps_str = f"  {fps_total:.0f}fps" if running > 0 and fps_total > 0 else ""
        line1 = (
            f"  [{_bar(overall)}] {overall * 100:5.1f}%  "
            f"完成 {done}  失败 {failed}  跳过 {skipped}  "
            f"运行中 {running}  等待 {pending}  已用 {_fmt_time(elapsed)}"
            f"{fps_str}"
        )

        sys.stdout.write(line1 + "\n")
        for ev in events:
            sys.stdout.write("    " + ev + "\n")
        sys.stdout.flush()

        self._last_lines = 1 + len(events)

        if final:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._render()
            self._stop.wait(0.5)


class SingleProgress:
    def __init__(self, job: Job):
        self.job = job
        self.start_ts = time.time()

    def update(self, j: Job) -> None:
        elapsed = time.time() - self.start_ts
        eta = 0.0
        if j.progress > 0.001:
            eta = elapsed * (1 - j.progress) / j.progress

        # 与 vidcrop_hwaccel.py 对齐：帧数 / fps / 倍速 / 已用 / 剩余
        frames = f"{j.frame}/{j.total_frames}帧" if j.total_frames else f"{j.frame}帧"
        # 部分编码器（如 libx265）未通过 -progress 上报 fps，退化为 帧数/耗时，
        # 与 vidcrop_hwaccel.py 的算法保持一致。
        fps = j.fps if j.fps > 0 else (j.frame / elapsed if elapsed > 0 else 0.0)
        line = (
            f"\r  [{_bar(j.progress)}] {j.progress * 100:5.1f}%  "
            f"{frames}  "
            f"fps={fps:5.1f}  speed={j.speed or '-':>6}  "
            f"已用 {_fmt_time(elapsed)}  剩余 {_fmt_time(eta)}   "
        )
        sys.stdout.write(line)
        sys.stdout.flush()

    def finish(self) -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════
#  执行任务
# ═══════════════════════════════════════════════════════════════════

def prepare_job_command(
    job: Job,
    args: argparse.Namespace,
    threads: int,
    warn: Callable[[str], None],
) -> Optional[List[str]]:
    if job.status == "skipped":
        return None

    info = ffprobe_info(job.src, args.original_width, args.original_height)
    if "probe_warning" in info:
        warn(f"{job.name}: ffprobe 失败但使用了手动原始尺寸；进度可能不准确：{info['probe_warning']}")

    if "error" in info:
        job.status = "failed"
        job.error = f"ffprobe: {info['error']}"
        return None

    job.info = info

    src_w = int(info.get("width", 0) or 0)
    src_h = int(info.get("height", 0) or 0)

    # 处理 --crop-ratio 模式：自动计算输出尺寸
    has_crop_ratio = args.crop_ratio is not None
    if has_crop_ratio:
        # 从 job.info 获取 crop_ratio (collect_jobs 中已存入)
        crop_ratio_num = job.info.get("crop_ratio_num", args.crop_ratio_num)
        crop_ratio_den = job.info.get("crop_ratio_den", args.crop_ratio_den)
        try:
            dst_w, dst_h = calculate_auto_crop_size(src_w, src_h, crop_ratio_num, crop_ratio_den)
        except ValueError as exc:
            job.status = "failed"
            job.error = str(exc)
            return None
    else:
        dst_w, dst_h = args.output_width, args.output_height

    # 同尺寸跳过检查
    if not args.no_skip_same_size:
        if src_w == dst_w and src_h == dst_h:
            job.status = "skipped"
            job.progress = 1.0
            job.error = "目标尺寸与源尺寸相同，--no-skip-same-size 可强制转码"
            return None

    # crop 模式下目标不能大于源 (cover 模式无此限制)
    if args.mode == "crop":
        if dst_w > src_w or dst_h > src_h:
            job.status = "failed"
            job.error = f"crop 模式下目标尺寸 ({dst_w}x{dst_h}) 不能大于源尺寸 ({src_w}x{src_h})"
            return None

    if args.codec.lower() == "copy":
        job.status = "failed"
        job.error = "视频裁剪/缩放不能使用 -c:v copy"
        return None

    # [META-KEEP] 位深继承：10bit 源不再被降为 8bit。
    # 必须在 validate_output_dimensions 之前决策——yuv420p10le/p010le 要求宽高偶数。
    src_bits = int(info.get("src_bits", 8) or 8)
    is_auto_fmt = (args.pix_fmt or "auto").lower() == "auto"
    if is_auto_fmt:
        pix_fmt = resolve_pix_fmt_for_source(args.codec, src_bits, warn=warn) \
            or PIX_FMT_DEFAULT_BY_CODEC.get(args.codec.lower(), "yuv420p")
    else:
        pix_fmt = args.pix_fmt_resolved

    try:
        validate_output_dimensions(dst_w, dst_h, pix_fmt)
    except ValueError as exc:
        # auto 模式下自动继承来的高位深与奇数尺寸冲突 → 降级 8bit 而非失败；
        # 用户显式指定 --pix-fmt 时尊重用户，保持失败。
        if is_auto_fmt and (dst_w % 2 or dst_h % 2) and pix_fmt in PIX_FMT_REQUIRE_EVEN_BOTH:
            warn(f"{exc}；已自动降级为 yuv420p")
            pix_fmt = "yuv420p"
        else:
            job.status = "failed"
            job.error = str(exc)
            return None

    try:
        return build_ffmpeg_cmd(
            src=job.src,
            dst=job.dst,
            dst_w=dst_w,
            dst_h=dst_h,
            src_w=src_w,
            src_h=src_h,
            mode=args.mode,
            codec=args.codec,
            crf=args.crf,
            cq=args.cq,
            preset=args.preset,
            pix_fmt=pix_fmt,
            threads=threads,
            overwrite=args.overwrite,
            audio_codec=args.audio_codec,
            audio_bitrate=args.audio_bitrate,
            extra_args=args.extra_args_normalized,
            warn=warn,
            info=info,
            color_range=args.color_range,
        )
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        return None


def execute_job(job: Job, args: argparse.Namespace, threads: int, panel: Optional[AggregatePanel]) -> None:
    if job.status == "skipped":
        if panel:
            panel.emit(f"⏭  跳过 {job.name} ({job.error or '已存在，--overwrite 可覆盖'})")
        return

    warnings: List[str] = []
    cmd = prepare_job_command(job, args, threads, warnings.append)

    if panel:
        for w in warnings:
            panel.emit(f"⚠ {w}")

    if cmd is None:
        if panel:
            if job.status == "skipped":
                panel.emit(f"⏭  跳过 {job.name} ({job.error})")
            elif job.status == "failed":
                panel.emit(f"✘ 失败 {job.name}: {job.error}")
        return

    job.status = "running"
    if panel:
        panel.emit(f"▶ 启动 {job.name}  (threads={threads})")

    rc, tail = run_ffmpeg_with_progress(cmd, job)

    if rc == 0:
        job.status = "done"
        job.progress = 1.0
        if panel:
            # 面板逐行重绘，故体积变化压缩在同一行内输出
            panel.emit(
                f"✔ 完成 {job.name}  用时 {_fmt_time(job.elapsed)}  "
                f"大小 {_size_change(job.src, job.dst)}"
            )
    else:
        job.status = "failed"
        job.error = "interrupted" if _STOP_REQUESTED.is_set() else (tail or f"ffmpeg rc={rc}")
        if panel:
            panel.emit(f"✘ 失败 {job.name}: rc={rc}")


def run_parallel(jobs: List[Job], args: argparse.Namespace, workers: int, threads: int) -> None:
    panel = AggregatePanel(jobs)
    panel.start()

    pending = [j for j in jobs if j.status == "pending"]
    for j in jobs:
        if j.status == "skipped":
            panel.emit(f"⏭  跳过 {j.name} ({j.error or '已存在，--overwrite 可覆盖'})")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {
                ex.submit(execute_job, j, args, threads, panel): j
                for j in pending
            }

            for fut in as_completed(fut_map):
                job = fut_map[fut]
                try:
                    fut.result()
                except Exception as exc:
                    job.status = "failed"
                    job.error = f"内部错误: {exc}"
                    panel.emit(f"✘ 失败 {job.name}: {job.error}")

    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        raise

    finally:
        panel.stop()


def run_sequential(jobs: List[Job], args: argparse.Namespace, threads: int) -> None:
    has_crop_ratio = args.crop_ratio is not None
    for idx, job in enumerate(jobs, 1):
        if _STOP_REQUESTED.is_set():
            break

        print(f"\n[{idx}/{len(jobs)}] {job.name}")

        if job.status == "skipped":
            print(f"  ⏭  跳过：{job.error or '已存在，--overwrite 可覆盖'}")
            continue

        warnings: List[str] = []
        cmd = prepare_job_command(
            job=job,
            args=args,
            threads=threads,
            warn=warnings.append,
        )

        for w in warnings:
            print(f"  警告：{w}", file=sys.stderr)

        if cmd is None:
            if job.status == "skipped":
                print(f"  ⏭  跳过：{job.error}")
            elif job.status == "failed":
                print(f"  ✘ 失败：{job.error}")
            continue

        # crop-ratio 模式显示计算出的尺寸
        if has_crop_ratio:
            info = ffprobe_info(job.src, args.original_width, args.original_height)
            src_w = int(info.get("width", 0) or 0)
            src_h = int(info.get("height", 0) or 0)
            crop_ratio_num = job.info.get("crop_ratio_num", args.crop_ratio_num)
            crop_ratio_den = job.info.get("crop_ratio_den", args.crop_ratio_den)
            try:
                dst_w, dst_h = calculate_auto_crop_size(src_w, src_h, crop_ratio_num, crop_ratio_den)
                print(f"  {_label('目标尺寸')}{dst_w}x{dst_h} (源 {src_w}x{src_h}, {args.mode} 自动裁剪比例 {crop_ratio_num}:{crop_ratio_den})")
            except Exception:
                pass

        print(f"  {_label('执行命令')}{shlex.join(cmd)}")

        progress = SingleProgress(job)
        job.status = "running"
        rc, tail = run_ffmpeg_with_progress(cmd, job, on_update=progress.update)
        progress.finish()

        if rc == 0:
            job.status = "done"
            job.progress = 1.0
            print(f"  ✔ 完成，用时 {_fmt_time(job.elapsed)}")
            print(f"  {_label('输出文件')}{job.dst}")
            print(f"  {_label('大小变化')}{_size_change(job.src, job.dst)}")
        else:
            job.status = "failed"
            job.error = "interrupted" if _STOP_REQUESTED.is_set() else (tail or f"ffmpeg rc={rc}")
            print(f"  ✘ 失败 (rc={rc})")
            if tail:
                print(tail)

        if rc == 130:
            _STOP_REQUESTED.set()
            break


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="批量视频裁剪 / 覆盖式缩放裁剪（CPU 多任务版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 与单进程版一致：仅居中裁剪，目标尺寸不能大于源尺寸
  python vidcrop_cpu_v1.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --codec libx264 --crf 20

  # 使用旧 V1 行为：先等比缩放覆盖目标，再居中裁剪
  python vidcrop_cpu_v1.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --mode cover --codec libx264 --crf 20

  # 并行参数手动指定
  python vidcrop_cpu_v1.py --input ./videos --output ./out \\
      --output-width 1920 --output-height 1080 --workers 2 --threads 4

  # 预览将要执行的命令（不实际转码）
  python vidcrop_cpu_v1.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --dry-run

  # 将输出记录到日志文件
  python vidcrop_cpu_v1.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --log process.log

  # 追加 FFmpeg 参数，--extra-args 必须放最后
  python vidcrop_cpu_v1.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --extra-args -- -max_muxing_queue_size 4096
""",
    )

    ap.add_argument("--input", required=True, help="输入视频文件或目录")
    ap.add_argument("--output", required=True, help="输出视频文件或目录")

    ap.add_argument("--original-width", type=int, help="手动指定源视频宽度 (跳过 ffprobe 探测，适合超大批量)")
    ap.add_argument("--original-height", type=int, help="手动指定源视频高度 (跳过 ffprobe 探测，适合超大批量)")

    ap.add_argument("--output-width", type=int, help="目标视频宽度 (--crop-ratio 模式下可选)")
    ap.add_argument("--output-height", type=int, help="目标视频高度 (--crop-ratio 模式下可选)")

    ap.add_argument(
        "--crop-ratio",
        type=str,
        help="目标宽高比，如 16:9 或 4:3 或浮点数 1.777。指定后自动计算最大化裁剪尺寸，无需指定 --output-width/height",
    )

    ap.add_argument(
        "--mode",
        choices=("crop", "cover"),
        default="crop",
        help="crop=仅裁剪；cover=等比缩放覆盖后裁剪。默认 crop",
    )

    ap.add_argument(
        "--codec",
        default="libx264",
        help="视频编码器，默认 libx264 (支持别名: x264, x265, h264, h265, hevc, "
             "vp9→libvpx-vp9, av1→libaom-av1, svtav1→libsvtav1, rav1e→librav1e 等)",
    )
    ap.add_argument(
        "--crf",
        type=int,
        default=None,
        help="CRF 质量值 (CPU 编码器)；默认 21，范围 0-51 越小质量越好",
    )
    ap.add_argument(
        "--cq",
        type=int,
        default=None,
        help="CQ 质量值 (GPU 编码器 NVENC/AMF/QSV)；默认 23，范围 0-51。CPU 编码器下自动映射为等效 CRF",
    )
    ap.add_argument(
        "--preset",
        default=None,
        help="编码器预设。默认：CPU 编码器 medium，GPU 编码器 p5。"
             "支持 libx264 风格 (ultrafast~veryslow) 和 NVENC 风格 (p1~p7)，自动双向映射",
    )
    ap.add_argument(
        "--flag",
        default=None,
        metavar="SUFFIX",
        help="输出文件名后缀标记，用于替代默认的 _cropped / _covered。"
             '例：--flag "_Croped" → abc.mp4 输出为 abc_Croped.mp4。'
             "仅对工具自动生成的输出名生效（批量模式或 --output 为目录）；"
             "--output 指定了完整文件名时不改动。",
    )
    ap.add_argument(
        "--pix-fmt",
        default="auto",
        help="输出像素格式；auto=大多数编码器 yuv420p，ProRes 为 yuv422p10le；可填 none 禁用",
    )

    ap.add_argument(
        "--color-range",
        choices=("auto", "tv", "pc"),
        default="auto",
        help="输出 color_range：auto=源为 unknown 时取 tv、否则取源值（不做值域转换）；"
             "tv=强制 limited(16-235)；pc=强制 full(0-255)。"
             "强制 tv/pc 时若与源实际值域不同，会自动插入 scale 滤镜做真正的"
             "像素值域转换（而非只改标签）",
    )

    ap.add_argument("--audio-codec", default="copy", help="音频编码器，默认 copy；如需重编码可用 aac / libopus")
    ap.add_argument("--audio-bitrate", default="128k", help="音频重编码码率，默认 128k")
    ap.add_argument("--container", help="手动指定容器扩展名，如 .mp4 / .mkv / .webm")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有输出文件")
    ap.add_argument("--recursive", "-r", action="store_true", help="递归扫描输入目录")
    ap.add_argument("--no-skip-same-size", action="store_true", help="即使源尺寸等于目标尺寸也强制转码")

    ap.add_argument("--workers", type=int, default=0, help="并行任务数，0=自动")
    ap.add_argument("--threads", type=int, default=0, help="每任务 FFmpeg 线程数，0=自动")
    ap.add_argument("--mem-per-job", type=float, default=0.0, help="单任务估计内存占用 GB，0=按编码器画像")
    ap.add_argument("--sequential", action="store_true", help="强制顺序执行，显示单文件细粒度进度条")

    # 新增参数
    ap.add_argument("--dry-run", action="store_true", help="仅生成并显示 FFmpeg 命令，不执行转码")
    ap.add_argument("--log", metavar="LOG_FILE", help="将所有输出同时记录到指定日志文件")

    ap.add_argument(
        "--extra-args",
        nargs=argparse.REMAINDER,
        help="追加到 FFmpeg 输出参数末尾的自定义参数；必须放在命令最后",
    )

    return ap.parse_args()


def validate_and_finalize_args(args: argparse.Namespace) -> None:
    if args.original_width is not None and args.original_width <= 0:
        raise ValueError("--original-width 必须为正整数")
    if args.original_height is not None and args.original_height <= 0:
        raise ValueError("--original-height 必须为正整数")

    # 归一化编码器名称 (别名映射)
    args.codec = normalize_codec_name(args.codec)

    # 未指定 --preset 时按编码器类型取默认：GPU 编码器 p5，CPU 编码器 medium
    if not args.preset:
        args.preset = default_preset_for(args.codec)

    # 归一化 preset (NVENC <-> libx264 双向映射)
    args.preset = normalize_preset(args.preset, args.codec)

    # 解析质量参数 (crf/cq 互斥与映射)
    args.crf, args.cq = _resolve_quality_params(args.codec, args.crf, args.cq)

    # --crop-ratio 模式：输出宽高可选，但需要原始尺寸 (稍后由 ffprobe 或 --original-width/height 获取)
    has_crop_ratio = args.crop_ratio is not None
    if has_crop_ratio:
        try:
            args.crop_ratio_num, args.crop_ratio_den = parse_crop_ratio(args.crop_ratio)
        except ValueError as exc:
            raise ValueError(str(exc))
        # crop-ratio 模式下 output-width/height 变为可选
        if args.output_width is not None or args.output_height is not None:
            print("提示：使用 --crop-ratio 时，--output-width/height 将被忽略，自动按比例计算。")
    else:
        # 传统模式：output-width/height 必选
        if args.output_width is None or args.output_height is None:
            raise ValueError("未指定 --crop-ratio 时，必须提供 --output-width 和 --output-height")

    if args.crf is not None and not (0 <= args.crf <= 63):
        raise ValueError("--crf 建议范围为 0-63")
    if args.cq is not None and not (0 <= args.cq <= 63):
        raise ValueError("--cq 建议范围为 0-63")

    if args.workers < 0:
        raise ValueError("--workers 不能为负数")
    if args.threads < 0:
        raise ValueError("--threads 不能为负数")
    if args.mem_per_job < 0:
        raise ValueError("--mem-per-job 不能为负数")

    args.container_normalized = normalize_container(args.container)
    args.extra_args_normalized = normalize_extra_args(args.extra_args)
    args.pix_fmt_resolved = resolve_pix_fmt(args.codec, args.pix_fmt)

    # 只有非 crop-ratio 模式才校验输出尺寸
    if not has_crop_ratio:
        validate_output_dimensions(args.output_width, args.output_height, args.pix_fmt_resolved)


def print_summary(jobs: List[Job]) -> int:
    done = sum(1 for j in jobs if j.status == "done")
    failed = sum(1 for j in jobs if j.status == "failed")
    skipped = sum(1 for j in jobs if j.status == "skipped")
    done_jobs = [j for j in jobs if j.status == "done"]
    total_time = sum(j.elapsed for j in done_jobs)
    total_frames = sum(int(j.info.get("nb_frames", 0) or 0) for j in done_jobs)
    avg_fps_str = f"  均速 {total_frames / total_time:.0f}fps" if total_time > 0 and total_frames > 0 else ""
    peak_fps = max((j.peak_fps for j in done_jobs), default=0.0)
    peak_fps_str = f"  峰值 {peak_fps:.0f}fps" if peak_fps > 0 else ""

    print("─" * 64)
    print(
        f"汇总        : 完成 {done}  失败 {failed}  跳过 {skipped}  "
        f"累计编码用时 {_fmt_time(total_time)}{avg_fps_str}{peak_fps_str}"
    )

    if failed:
        print("失败列表：")
        for j in jobs:
            if j.status == "failed":
                print(f"  ✘ {j.name}  →  {j.error}")

    return 0 if failed == 0 else 1


def main() -> int:
    check_tools()
    install_signal_handlers()

    try:
        args = parse_args()
        validate_and_finalize_args(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    # 设置日志记录（如果指定了 --log）
    if args.log:
        setup_log(args.log)

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    try:
        jobs, _batch_mode, output_path = collect_jobs(
            input_path=input_path,
            output_path=output_path,
            codec=args.codec,
            container=args.container_normalized,
            overwrite=args.overwrite,
            recursive=args.recursive,
            crop_ratio_num=args.crop_ratio_num if hasattr(args, 'crop_ratio_num') else None,
            crop_ratio_den=args.crop_ratio_den if hasattr(args, 'crop_ratio_den') else None,
            flag=args.flag,
            mode=args.mode,
        )
    except KeyboardInterrupt:
        _terminate_active_procs()
        return 130

    if not jobs:
        print(f"[INFO] 未在 {input_path} 中找到可处理的视频。")
        return 0

    cpu, total_mem, avail_mem = detect_system_resources()

    pending = [j for j in jobs if j.status == "pending"]

    workers, threads = compute_parallelism(
        num_pending=len(pending),
        codec=args.codec,
        cpu=cpu,
        total_mem_gb=total_mem,
        avail_mem_gb=avail_mem,
        workers_override=args.workers,
        threads_override=args.threads,
        mem_per_job_override=args.mem_per_job,
    )

    print("─" * 64)
    print(f"系统资源    : CPU 逻辑核 {cpu}  ·  内存 总 {total_mem:.1f} GB / 可用 {avail_mem:.1f} GB")
    print(
        f"待处理文件  : {len(pending)} 个"
        + (f"（另有 {len(jobs) - len(pending)} 个已存在或将跳过）" if len(jobs) != len(pending) else "")
    )
    mode_label = "cover（等比缩放+裁剪）" if args.mode == "cover" else "crop（居中裁剪）"
    has_crop_ratio = args.crop_ratio is not None
    if has_crop_ratio:
        print(
            _label("处理模式")
            + f"{mode_label}  自动裁剪比例: {args.crop_ratio_num}:{args.crop_ratio_den}"
        )
    else:
        print(
            _label("处理模式")
            + f"{mode_label}  目标尺寸: {args.output_width}x{args.output_height}"
        )
    # 只展示真正会生效的质量参数：显式指定的直接显示；都未指定时显示当前
    # 编码器对应的默认值。不再输出「CRF: 不使用」这类无效字段。
    quality_parts = []
    if args.crf is not None:
        quality_parts.append(f"CRF: {args.crf}")
    if args.cq is not None:
        quality_parts.append(f"CQ: {args.cq}")
    if not quality_parts:
        if encoder_supports_cq(args.codec):
            quality_parts.append(f"CQ: {DEFAULT_CQ}")
        elif encoder_supports_crf(args.codec):
            quality_parts.append(f"CRF: {DEFAULT_CRF}")
    print(
        _label("编码器")
        + f"{args.codec}   preset: {args.preset}   "
        + "   ".join(quality_parts)
        + f"   pix_fmt: {args.pix_fmt_resolved or '不指定'}"
        + f"   color_range: {args.color_range}"
    )
    print(f"音频        : {args.audio_codec}" + (f" @ {args.audio_bitrate}" if args.audio_codec != "copy" else ""))

    if pending:
        print(
            f"并发策略    : {workers} 个并行任务 × 每任务 {threads} 线程  "
            f"(≈ {workers * threads} CPU 槽)"
        )
    else:
        print("并发策略    : 无待处理任务")

    # Dry-run 模式：仅生成命令并退出
    if args.dry_run:
        print("─" * 64)
        print("DRY-RUN 模式：将仅显示命令，不执行转码。\n")
        for job in jobs:
            if job.status == "skipped":
                print(f"⏭  跳过 {job.name}: {job.error or '已存在，--overwrite 可覆盖'}")
                continue

            warnings: List[str] = []
            cmd = prepare_job_command(job, args, threads, warnings.append)
            for w in warnings:
                print(f"  ⚠ 警告：{w}")

            if cmd is None:
                if job.status == "skipped":
                    print(f"⏭  跳过 {job.name}: {job.error}")
                elif job.status == "failed":
                    print(f"✘ 失败 {job.name}: {job.error}")
                continue

            print(f"▶ {job.name} → {job.dst.name}")
            print(f"  命令: {shlex.join(cmd)}\n")
        print("─" * 64)
        return 0

    sequential = args.sequential or workers <= 1 or len(pending) <= 1
    print(
        "运行模式    : "
        + ("顺序执行（细粒度实时进度条）" if sequential else "并行执行（聚合进度面板）")
    )
    print("─" * 64)

    try:
        if pending:
            if sequential:
                run_sequential(jobs, args, threads)
            else:
                run_parallel(jobs, args, workers, threads)
        else:
            for j in jobs:
                if j.status == "skipped":
                    print(f"⏭  跳过 {j.name} ({j.error or '已存在，--overwrite 可覆盖'})")

    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        print("\n[INFO] 已中断。", file=sys.stderr)
        return 130

    if _STOP_REQUESTED.is_set():
        return 130

    return print_summary(jobs)


if __name__ == "__main__":
    sys.exit(main())