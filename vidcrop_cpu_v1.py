#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vidcrop_cpu_v1.py – 批量视频裁剪/覆盖缩放工具（CPU 多任务并发版）

功能
────
• 支持两种处理模式：
  - crop：居中裁剪（目标尺寸不能大于源尺寸）
  - cover：等比缩放至完全覆盖目标区域后居中裁剪
• 批量处理视频文件，支持递归扫描输入目录并保持输出目录结构
• 自动探测系统 CPU 核心数与可用内存，智能决定并行任务数及单任务线程数
• 可通过 --workers / --threads / --mem-per-job 手动控制并发策略
• 支持几乎所有 FFmpeg 视频编码器，自动匹配推荐封装容器
• 丰富的编码选项：CRF 质量控制、preset 预设、像素格式指定等
• 提供聚合进度面板（并行模式）或单文件细粒度进度条（顺序模式）
• 新增 --dry-run 仅预览命令不执行转码；--log 将所有输出记录到日志文件
• 支持通过 --extra-args 传递额外 FFmpeg 参数

主要参数
────────
--input                输入视频文件或目录（必选）
--output               输出视频文件或目录（必选）
--output-width         目标视频宽度  (必选)
--output-height        目标视频高度  (必选)
--mode                 crop | cover（默认 crop）
--codec                视频编码器（默认 libx264，全部选项见帮助）
--crf                  CRF 质量值（默认 20，仅对支持 CRF 的编码器生效）
--preset               编码器预设（默认 medium，仅 libx264/libx265）
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
python vidcrop_cpu_v1.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --codec libx264 --crf 20

# 等比覆盖裁剪到 1920×1080，使用 libx265，CRF 22，覆盖已有文件
python vidcrop_cpu_v1.py --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 --mode cover \
    --codec libx265 --crf 22 --overwrite

# 手动并发：2 个并行任务，每个任务 4 个线程
python vidcrop_cpu_v1.py --input ./videos --output ./out \
    --output-width 1920 --output-height 1080 --workers 2 --threads 4

# 仅预览将要执行的命令，不实际转码
python vidcrop_cpu_v1.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --dry-run

# 记录处理过程到日志文件
python vidcrop_cpu_v1.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --log process.log

# 在 FFmpeg 命令末尾追加自定义参数（注意最后的 --）
python vidcrop_cpu_v1.py --input ./videos --output ./out \
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
    "libaom-av1": ".mp4",
    "librav1e": ".mp4",
    "prores": ".mov",
    "prores_ks": ".mov",
    "mpeg4": ".mp4",
    "libxvid": ".avi",
    "mjpeg": ".avi",
    "copy": None,
}

PRESET_SUPPORTED_CODECS = {"libx264", "libx265"}

CRF_SUPPORTED_CODECS = {
    "libx264",
    "libx265",
    "libvpx-vp9",
    "libvpx",
    "libaom-av1",
    "librav1e",
}

PRESET_VALUES = {
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow", "placebo",
}

DEFAULT_PRESET = "medium"

PIX_FMT_DEFAULT_BY_CODEC = {
    "prores": "yuv422p10le",
    "prores_ks": "yuv422p10le",
}

PIX_FMT_REQUIRE_EVEN_BOTH = {
    "yuv420p", "yuvj420p", "nv12", "p010le", "p016le"
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
    "librav1e": (4, 1.2),
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


def resolve_pix_fmt(codec: str, pix_fmt_arg: str) -> Optional[str]:
    if pix_fmt_arg.lower() in {"none", "no", "disable"}:
        return None
    if pix_fmt_arg.lower() != "auto":
        return pix_fmt_arg
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
        return any(x in codec_lower for x in ["264", "265", "hevc", "av1", "rav1e", "mpeg4"])
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
        if preset != DEFAULT_PRESET:
            warn(f"编码器 {codec} 不支持 -preset，已忽略 --preset {preset}")

    if pix_fmt:
        opts += ["-pix_fmt", pix_fmt]

    return opts


# ═══════════════════════════════════════════════════════════════════
#  FFprobe
# ═══════════════════════════════════════════════════════════════════

def ffprobe_info(
    path: Path,
    original_width: Optional[int] = None,
    original_height: Optional[int] = None,
) -> Dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        data = json.loads(res.stdout)
    except Exception as exc:
        if original_width and original_height:
            return {
                "width": original_width,
                "height": original_height,
                "duration": 0.0,
                "nb_frames": 0,
                "probe_warning": str(exc),
            }
        return {"error": str(exc)}

    video_stream = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            video_stream = s
            break

    if video_stream is None:
        return {"error": "no video stream"}

    width = int(video_stream.get("width", 0) or 0)
    height = int(video_stream.get("height", 0) or 0)

    if original_width is not None:
        width = original_width
    if original_height is not None:
        height = original_height

    duration = 0.0
    fmt = data.get("format", {})
    for candidate in (fmt.get("duration"), video_stream.get("duration")):
        try:
            duration = float(candidate or 0)
            if duration > 0:
                break
        except Exception:
            pass

    nb_frames = 0
    try:
        nb_frames = int(video_stream.get("nb_frames", 0) or 0)
    except Exception:
        nb_frames = 0

    if nb_frames == 0 and duration > 0:
        rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/0"
        try:
            num_s, den_s = rate.split("/")
            num = float(num_s)
            den = float(den_s)
            if den > 0:
                nb_frames = int(duration * (num / den))
        except Exception:
            pass

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "nb_frames": nb_frames,
    }


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
        return out_dir / f"{src.stem}_cropped{ext}"

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

    jobs: List[Job] = []
    for src in sources:
        dst = make_output_file(
            src=src,
            output_path=output_path,
            codec=codec,
            container=container,
            batch_mode=batch_mode,
            input_root=input_root,
        )

        if _same_path(src, dst):
            print(f"[ERROR] 输出文件与输入文件相同，拒绝覆盖源文件：{src}", file=sys.stderr)
            sys.exit(2)

        if dst.exists() and not overwrite:
            jobs.append(Job(src=src, dst=dst, status="skipped", progress=1.0, error="已存在"))
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
    preset: str,
    pix_fmt: Optional[str],
    threads: int,
    overwrite: bool,
    audio_codec: str,
    audio_bitrate: str,
    extra_args: List[str],
    warn: Callable[[str], None],
) -> List[str]:
    if codec.lower() == "copy":
        raise ValueError("使用视频滤镜时不能使用 -c:v copy，请改用 libx264 / libx265 等编码器")

    vf = build_video_filter(mode, src_w, src_h, dst_w, dst_h)

    cmd: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-err_detect", "ignore_err",
        "-fflags", "+genpts+discardcorrupt",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-i", str(src),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-vf", vf,
    ]

    cmd += build_encoder_options(codec, crf, preset, pix_fmt, warn)
    cmd += ["-threads", str(max(1, threads))]

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
                    job.fps = float(val)
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

        line = (
            f"\r  [{_bar(j.progress)}] {j.progress * 100:5.1f}%  "
            f"fps={j.fps:5.1f}  speed={j.speed or '-':>6}  "
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

    if not args.no_skip_same_size:
        if src_w == args.output_width and src_h == args.output_height:
            job.status = "skipped"
            job.progress = 1.0
            job.error = "目标尺寸与源尺寸相同"
            return None

    if args.codec.lower() == "copy":
        job.status = "failed"
        job.error = "视频裁剪/缩放不能使用 -c:v copy"
        return None

    try:
        return build_ffmpeg_cmd(
            src=job.src,
            dst=job.dst,
            dst_w=args.output_width,
            dst_h=args.output_height,
            src_w=src_w,
            src_h=src_h,
            mode=args.mode,
            codec=args.codec,
            crf=args.crf,
            preset=args.preset,
            pix_fmt=args.pix_fmt_resolved,
            threads=threads,
            overwrite=args.overwrite,
            audio_codec=args.audio_codec,
            audio_bitrate=args.audio_bitrate,
            extra_args=args.extra_args_normalized,
            warn=warn,
        )
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        return None


def execute_job(job: Job, args: argparse.Namespace, threads: int, panel: Optional[AggregatePanel]) -> None:
    if job.status == "skipped":
        if panel:
            panel.emit(f"⏭  跳过 {job.name} ({job.error or '已存在'})")
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
            panel.emit(f"✔ 完成 {job.name}  用时 {_fmt_time(job.elapsed)}")
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
            panel.emit(f"⏭  跳过 {j.name} ({j.error or '已存在'})")

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
    for idx, job in enumerate(jobs, 1):
        if _STOP_REQUESTED.is_set():
            break

        print(f"\n[{idx}/{len(jobs)}] {job.name}")

        if job.status == "skipped":
            print(f"  ⏭  跳过：{job.error or '已存在'}")
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

        print(f"  执行命令：{shlex.join(cmd)}")

        progress = SingleProgress(job)
        job.status = "running"
        rc, tail = run_ffmpeg_with_progress(cmd, job, on_update=progress.update)
        progress.finish()

        if rc == 0:
            job.status = "done"
            job.progress = 1.0
            print(f"  ✔ 完成，用时 {_fmt_time(job.elapsed)}")
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

    ap.add_argument("--original-width", type=int, help="手动指定源视频宽度")
    ap.add_argument("--original-height", type=int, help="手动指定源视频高度")

    ap.add_argument("--output-width", type=int, required=True)
    ap.add_argument("--output-height", type=int, required=True)

    ap.add_argument(
        "--mode",
        choices=("crop", "cover"),
        default="crop",
        help="crop=仅裁剪；cover=等比缩放覆盖后裁剪。默认 crop",
    )

    ap.add_argument(
        "--codec",
        default="libx264",
        choices=sorted(CODEC_CONTAINER_MAP.keys()),
        help="视频编码器，默认 libx264",
    )
    ap.add_argument(
        "--crf",
        type=int,
        default=None,
        help="CRF 质量值；默认：支持 CRF 的编码器使用 20，不支持则忽略",
    )
    ap.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        choices=sorted(PRESET_VALUES),
        help=f"编码器预设，仅 libx264/libx265 生效，默认 {DEFAULT_PRESET}",
    )
    ap.add_argument(
        "--pix-fmt",
        default="auto",
        help="输出像素格式；auto=大多数编码器 yuv420p，ProRes 为 yuv422p10le；可填 none 禁用",
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

    if args.crf is None and args.codec.lower() in CRF_SUPPORTED_CODECS:
        args.crf = 20

    if args.crf is not None and not (0 <= args.crf <= 63):
        raise ValueError("--crf 建议范围为 0-63")

    if args.workers < 0:
        raise ValueError("--workers 不能为负数")
    if args.threads < 0:
        raise ValueError("--threads 不能为负数")
    if args.mem_per_job < 0:
        raise ValueError("--mem-per-job 不能为负数")

    args.container_normalized = normalize_container(args.container)
    args.extra_args_normalized = normalize_extra_args(args.extra_args)
    args.pix_fmt_resolved = resolve_pix_fmt(args.codec, args.pix_fmt)

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
    print(
        f"处理模式    : {args.mode}  "
        f"目标尺寸: {args.output_width}x{args.output_height}"
    )
    print(
        f"编码器      : {args.codec}   "
        f"preset: {args.preset}   "
        f"CRF: {args.crf if args.crf is not None else '不使用'}   "
        f"pix_fmt: {args.pix_fmt_resolved or '不指定'}"
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
                print(f"⏭  跳过 {job.name}: {job.error or '已存在'}")
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
                    print(f"⏭  跳过 {j.name} ({j.error or '已存在'})")

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