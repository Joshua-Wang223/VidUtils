#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vidls — 带视频属性探测的 ls / ll 替代品。

设计要点
────────
1. 非视频文件：完全按 coreutils `ls` 的版式渲染（多列网格 / -l 长格式），
   属性只来自 lstat，行为与原生 ls 对齐。
2. 视频文件：默认版式下每条独占一行并追加属性列（分辨率 / 帧率 / 比特率 /
   编码器 / 容器 / 时长）；-l 版式下在长格式行尾追加同样的属性列。
   **帧数是可选列**（2026-09-17 起）：它是唯一需要解码或至少解复用的列，
   默认既不显示也不计算 —— 裸 `vidls` 因此只有一次 ffprobe 的成本。
   要看帧数加 `--show frames`，或 `--show-all` 连带其它可选列一起看。
3. 系统资源自动探测（cgroup 感知）→ 自动决定探测并发度并最大化并行。
4. 帧数（仅 `--show frames` / `--show-all`）采用四级降级链，每条结果带来源标签：

     档 1「包头」容器头 nb_frames                  —— 免费
     档 2「硬解」ffmpeg 显式 xxx_cuvid 整片解码数帧 —— 解码（GPU）
     档 3「包数」ffprobe -count_packets            —— 只解复用，不解码（CPU 侧降级）
     档 4「估算」duration × fps                    —— 兜底

   `--cpu` 跳过档 2 直接档 3；`--fast` 只走档 1（永不解码）；
   `--deep` 强制重新数帧（有 GPU 走档 2，否则档 3）。
   **这三个开关都依赖「显示帧数」**，没请求帧数列时会提示并忽略。

退出码：0 正常 / 1 有条目 stat 或探测失败 / 2 参数错误。
"""

from __future__ import annotations

import argparse
import grp
import json
import locale
import math
import os
import pwd
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

VERSION = "1.0.0"

# ═══════════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════════

# 以 vidcrop_cpu_v2.py:144 的 VIDEO_EXTS 为基线，补齐常见容器
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".m4v", ".ts", ".webm", ".mpg", ".mpeg",
    ".m2ts", ".mts", ".vob", ".ogv", ".3gp", ".mxf",
    ".rmvb", ".asf", ".f4v", ".y4m", ".m2v", ".divx",
}

# 与 vidcrop_cpu_v2.py 一致的资源预算常量
_MAX_USABLE_RATIO = 0.80
_MIN_SYSTEM_RESERVE_GB = 0.2
_MEM_PER_PROBE_GB = 0.1        # ffprobe 很轻，每路预留 0.1GB 足够
HW_DECODE_MAX_JOBS = 4         # 并发 NVDEC 会抢显存，深解档额外封顶

PROBE_TIMEOUT = 20             # 元数据探测
FRAME_COUNT_TIMEOUT = 300      # 数帧（整片解码），给足但不无限

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_EXIT_HAD_ERROR = False

# ── 两类硬解失败，处理方式完全不同（都是实测得到的） ──
#
# ① CUDA / 驱动层坏掉（libnvcuvid 加载不了、设备创建失败、沙箱不给设备）：
#    整个进程都不该再试硬解 → GpuProbe.disable_runtime()
_CUDA_FATAL_KEYS = [
    "cannot load libnvcuvid", "failed loading nvcuvid",
    "cannot load nvcuda", "failed to load nvcuda",
    "device creation failed", "hardware device setup failed",
    "could not dynamically load cuda", "no device available for decoder",
    "hwaccel initialisation returned error", "no cuda capable devices",
    "does not support device type cuda", "cuda_error_no_device",
    "operation not permitted",
]
# ② 这张卡解不了**这个编码器**（实测 T4 + AV1：解码器编译进来了，运行时报
#    "Codec av1_cuvid is not supported."；ffv1 这类则根本没有 xxx_cuvid）：
#    只把该编码器拉黑，别的编码器继续走硬解 → _NVDEC_CODEC_BLOCKED
_NVDEC_CODEC_KEYS = [
    "is not supported",
    "hardware is lacking required capabilities",
    "doesn't support hardware accelerated",
    "not supported by the gpu",
    "decoder not found",
]

# ffprobe 的 codec_name 与 cuvid 解码器名不完全对应
_CUVID_NAME_ALIAS = {"mpeg1video": "mpeg1", "mpeg2video": "mpeg2"}

_CUVID_LOCK = threading.Lock()
_CUVID_CACHE: Dict[str, frozenset] = {}
_NVDEC_CODEC_BLOCKED: set = set()      # 本进程内已确认这张卡解不了的解码器名
_NVDEC_SKIP_REPORTED: set = set()      # 已打过「跳过硬解」提示的编码器（避免刷屏）
_VERBOSE = False


def _vlog(msg: str) -> None:
    if _VERBOSE:
        print(f"[vidls] {msg}", file=sys.stderr)


def _note_hard_decode_skip(codec: str, reason: str) -> None:
    """-v 时说明「为什么这个文件的帧数不是硬解档」，同一编码器只说一次。"""
    if not _VERBOSE or codec in _NVDEC_SKIP_REPORTED:
        return
    with _CUVID_LOCK:
        if codec in _NVDEC_SKIP_REPORTED:
            return
        _NVDEC_SKIP_REPORTED.add(codec)
    _vlog(f"{codec}: 跳过硬解档（{reason}）→ 帧数走包数档")

# ═══════════════════════════════════════════════════════════════════
#  显示宽度（CJK 记 2 列）
# ═══════════════════════════════════════════════════════════════════


def disp_width(text: str) -> int:
    """按终端显示宽度计算字符串宽度（CJK 全角记 2 列）。"""
    w = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad_display(text: str, width: int, align: str = "left") -> str:
    """按显示宽度填充到 width。align: left / right。"""
    gap = width - disp_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    return text + " " * gap


# ═══════════════════════════════════════════════════════════════════
#  系统资源探测（镜像 vidcrop_cpu_v2.py:341-533）
# ═══════════════════════════════════════════════════════════════════


def _detect_cpu() -> Tuple[int, str]:
    try:
        with open("/sys/fs/cgroup/cpu.max", "r", encoding="utf-8") as f:
            parts = f.read().strip().split()
        if len(parts) == 2 and parts[0] != "max":
            quota, period = int(parts[0]), int(parts[1])
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
            st = _read_cgroup_v2_memstat()
            reclaimable = st.get("file", 0) + st.get("slab_reclaimable", 0)
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
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes", "r",
                          encoding="utf-8") as f:
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

    if sys.platform == "win32":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            s = _MEMORYSTATUSEX()
            s.dwLength = ctypes.sizeof(s)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            if s.ullTotalPhys > 0:
                return (s.ullTotalPhys / (1024 ** 3),
                        s.ullAvailPhys / (1024 ** 3), "GlobalMemoryStatusEx")
        except Exception:
            pass

    return 2.0, 1.6, "fallback default"


def compute_probe_parallelism(n_videos: int, cpu: int, total_gb: float,
                              avail_gb: float, workers_override: int = 0,
                              deep: bool = False) -> int:
    """探测并发度：min(CPU, 内存预算, 视频数)，深解档再封顶 HW_DECODE_MAX_JOBS。"""
    if n_videos <= 0:
        return 0
    if n_videos == 1 and workers_override == 0:
        return 1

    if workers_override > 0:
        workers = workers_override
    else:
        budget_by_total = total_gb * _MAX_USABLE_RATIO
        budget_by_avail = max(0.0, avail_gb - _MIN_SYSTEM_RESERVE_GB)
        usable = max(0.1, min(budget_by_total, budget_by_avail))
        max_by_mem = max(1, int(usable / _MEM_PER_PROBE_GB))
        workers = min(max(1, cpu), max_by_mem)
        if deep:
            workers = min(workers, HW_DECODE_MAX_JOBS)

    return max(1, min(workers, n_videos))


# ═══════════════════════════════════════════════════════════════════
#  FFmpeg 环境与 GPU 硬解探测（镜像 vidcrop_hwaccel.py:308 / :597）
# ═══════════════════════════════════════════════════════════════════


def _ffmpeg_env(cuda_visible_devices: str = "all",
                nvidia_driver_caps: str = "compute,video,utility") -> dict:
    """构造 ffmpeg/ffprobe 子进程环境。

    沙箱里读 /proc/sys/crypto/fips_enabled 可能返回 EIO，libgcrypt(>=1.10) 会
    把它当致命错误直接 abort()，ffmpeg 还没解析命令行就 exit 134。
    LIBGCRYPT_FORCE_FIPS_MODE=0 让它跳过该读取（值必须是 "0"）。
    """
    env = os.environ.copy()
    env["LIBGCRYPT_FORCE_FIPS_MODE"] = "0"
    if "NVIDIA_VISIBLE_DEVICES" not in env:
        env["NVIDIA_VISIBLE_DEVICES"] = cuda_visible_devices
    if "NVIDIA_DRIVER_CAPABILITIES" not in env:
        env["NVIDIA_DRIVER_CAPABILITIES"] = nvidia_driver_caps

    cuda_lib_paths = [
        "/usr/local/cuda/lib64", "/usr/local/cuda/lib",
        "/usr/lib/x86_64-linux-gnu", "/usr/lib64",
    ]
    existing = env.get("LD_LIBRARY_PATH", "")
    for p in cuda_lib_paths:
        if os.path.isdir(p) and p not in existing.split(":"):
            existing = f"{existing}:{p}" if existing else p
    if existing:
        env["LD_LIBRARY_PATH"] = existing
    return env


class GpuProbe:
    """CUDA 硬解可用性探测（结果进程内缓存一次；失败后不再重试）。"""

    _lock = threading.Lock()
    _state: Optional[bool] = None     # None=未探测
    _reason: str = ""
    _disabled_runtime = False         # 运行期硬解失败后置位，后续直接降级

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._state = None
            cls._reason = ""
            cls._disabled_runtime = False

    @classmethod
    def available(cls, ffmpeg_bin: str, verbose: bool = False) -> bool:
        with cls._lock:
            if cls._disabled_runtime:
                return False
            if cls._state is not None:
                return cls._state

        ok = cls._probe(ffmpeg_bin, verbose)
        with cls._lock:
            cls._state = ok
        return ok

    @classmethod
    def disable_runtime(cls, reason: str = "") -> None:
        with cls._lock:
            cls._disabled_runtime = True
            cls._reason = reason

    @classmethod
    def reason(cls) -> str:
        with cls._lock:
            return cls._reason

    @staticmethod
    def _probe(ffmpeg_bin: str, verbose: bool) -> bool:
        # 关键：必须用 testsrc2 + yuv420p。testsrc 默认 4:4:4，硬解必挂。
        for size in ("64x64", "320x240"):
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
                os.close(fd)
                gen = [ffmpeg_bin, "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                       "-f", "lavfi", "-i", f"testsrc2=s={size}:d=0.1:r=25",
                       "-pix_fmt", "yuv420p", "-c:v", "libx264",
                       "-frames:v", "2", tmp_path]
                r = subprocess.run(gen, capture_output=True, text=True, timeout=15,
                                   stdin=subprocess.DEVNULL, env=_ffmpeg_env())
                if r.returncode != 0 or not os.path.exists(tmp_path):
                    continue
                # 用**显式** h264_cuvid：-hwaccel cuda 的自动选择在不支持时会静默
                # 转软解，那样"探针通过"就不能代表 NVDEC 真能用。
                test = [ffmpeg_bin, "-nostdin", "-y", "-hide_banner", "-v", "error",
                        "-c:v", "h264_cuvid",
                        "-i", tmp_path, "-map", "0:v:0", "-frames:v", "2",
                        "-f", "null", "-", "-progress", "pipe:1"]
                r2 = subprocess.run(test, capture_output=True, text=True, timeout=15,
                                    stdin=subprocess.DEVNULL, env=_ffmpeg_env())
                err = (r2.stderr or "").lower()
                # 三道关：rc==0 不够（实测 ffmpeg 硬解失败时也可能返回 0），
                # 还要没有致命关键字、且真的数出了帧。
                if r2.returncode != 0 or any(k in err for k in _CUDA_FATAL_KEYS):
                    continue
                decoded = 0
                for line in (r2.stdout or "").splitlines():
                    if line.startswith("frame="):
                        try:
                            decoded = max(decoded, int(line.split("=", 1)[1].strip()))
                        except (IndexError, ValueError):
                            pass
                if decoded > 0:
                    if verbose:
                        print(f"[vidls] GPU 硬解探针 {size} 通过（解码 {decoded} 帧）",
                              file=sys.stderr)
                    return True
            except Exception:
                continue
            finally:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        return False


# ═══════════════════════════════════════════════════════════════════
#  元数据探测
# ═══════════════════════════════════════════════════════════════════

_PROBE_CACHE: Dict[str, dict] = {}
_PROBE_LOCK = threading.Lock()


def _probe_cache_key(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return os.path.abspath(path)


def _run(cmd: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    # 全部子进程带 -nostdin 且 stdin=DEVNULL：避免 SIGTTIN / 管道反压挂起
    # （见 memory/project_ffmpeg_stdin_hang.md）
    return subprocess.run(list(cmd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          stdin=subprocess.DEVNULL, env=_ffmpeg_env())


def _run_ffprobe_json(ffprobe_bin: str, args: Sequence[str],
                      timeout: int = PROBE_TIMEOUT) -> dict:
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", *args]
    r = _run(cmd, timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip().splitlines()[-1]
                           if (r.stderr or "").strip() else f"ffprobe rc={r.returncode}")
    return json.loads(r.stdout or "{}")


def _extract_rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list") or []:
        if "isplay Matrix" in (sd.get("side_data_type") or ""):
            try:
                return abs(int(float(sd.get("rotation") or 0))) % 360
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags") or {}
    for key in ("rotate", "rotation"):
        if key in tags:
            try:
                return abs(int(float(tags[key]))) % 360
            except (TypeError, ValueError):
                pass
    return 0


def _parse_rate(value) -> Optional[float]:
    """解析 "30000/1001" / "25" / 25.0 → float；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    s = str(value).strip()
    if not s or s in ("0/0", "N/A"):
        return None
    if "/" in s:
        num, _, den = s.partition("/")
        try:
            num_v, den_v = float(num), float(den)
            return num_v / den_v if den_v else None
        except ValueError:
            return None
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def _parse_bits(video: dict) -> int:
    for key in ("bits_per_raw_sample", "bits_per_coded_sample"):
        v = video.get(key)
        if v:
            try:
                b = int(v)
                if b > 0:
                    return b
            except (TypeError, ValueError):
                pass
    pix_fmt = video.get("pix_fmt") or ""
    for suffix, bits in (("p10le", 10), ("p12le", 12), ("p16le", 16)):
        if pix_fmt.endswith(suffix):
            return bits
    return 8


def _looks_hdr(video: dict, bits: int) -> bool:
    transfer = (video.get("color_transfer") or "").lower()
    primaries = (video.get("color_primaries") or "").lower()
    if transfer in ("smpte2084", "arib-std-b67", "bt2020-10"):
        return True
    if transfer.startswith("bt2020") and bits >= 10:
        return True
    return primaries == "bt2020" and bits >= 10


def _has_hdr_side_data(video: dict) -> bool:
    for sd in video.get("side_data_list") or []:
        t = (sd.get("side_data_type") or "").lower()
        if "hdr" in t or "mastering display" in t or "content light level" in t:
            return True
    return False


# 容器名归一：ffprobe 的 format_name 是一串「同族容器」，不能直接取首位 ——
# .mp4 与 .mov 都报 'mov,mp4,m4a,3gp,3g2,mj2'，.mkv 与 .webm 都报 'matroska,webm'。
_CONTAINER_ALIAS = {
    "m4v": "mp4", "m4a": "mp4", "ts": "mpegts", "mts": "mpegts", "m2ts": "mpegts",
}

def _normalize_container(format_name: str, path: str = "") -> str:
    """把 format_name 归一成扩展名口径的容器名（mp4 / mov / mkv / webm …）。"""
    if not format_name:
        return "—"
    parts = [p.strip().lower() for p in format_name.split(",") if p.strip()]
    if not parts:
        return "—"

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext:
        if ext in parts:
            return ext
        alias = _CONTAINER_ALIAS.get(ext)
        if alias and alias in parts:
            return ext
        if ext == "mkv" and "matroska" in parts:
            return "mkv"
        if ext == "webm" and "webm" in parts:
            return "webm"
    return parts[0]


def _fmt_bitrate(bps: Optional[float]) -> str:
    if not bps or bps <= 0:
        return "—"
    if bps >= 1_000_000:
        v = bps / 1_000_000.0
        return f"{v:.1f} Mbps" if v < 100 else f"{v:.0f} Mbps"
    return f"{bps / 1000.0:.0f} kbps"


def _fmt_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "—"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_fps(fps: Optional[float]) -> str:
    if not fps or fps <= 0:
        return "—"
    if abs(fps - round(fps)) < 1e-4:
        return f"{round(fps)} fps"
    return f"{fps:.3f} fps"


def cuvid_decoders(ffmpeg_bin: str) -> frozenset:
    """ffmpeg 里编译进来的 `*_cuvid` 解码器名单（进程内缓存一次）。"""
    with _CUVID_LOCK:
        cached = _CUVID_CACHE.get(ffmpeg_bin)
    if cached is not None:
        return cached
    names: set = set()
    try:
        r = subprocess.run([ffmpeg_bin, "-hide_banner", "-decoders"],
                           capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL, env=_ffmpeg_env())
        for part in (r.stdout or "").split():
            if part.endswith("_cuvid") and part.replace("_", "").isalnum():
                names.add(part)
    except Exception:                             # noqa: BLE001
        pass
    result = frozenset(names)
    with _CUVID_LOCK:
        _CUVID_CACHE[ffmpeg_bin] = result
    return result


def cuvid_decoder_for(codec: str, ffmpeg_bin: str) -> Optional[str]:
    """该编码器在本机有没有可用的 NVDEC 解码器；没有就说明不该走档 2。

    关键：`-hwaccel cuda` 对 NVDEC 不支持的编码器（实测 ffv1）会**静默软解**、
    还照样返回 0 —— 那样标出来的「硬解」是假的。所以这里必须先确认解码器存在，
    再用**显式** `-c:v xxx_cuvid`（失败会大声报错，不会偷偷退回软解）。
    """
    base = _CUVID_NAME_ALIAS.get(codec, codec)
    name = f"{base}_cuvid"
    if name in _NVDEC_CODEC_BLOCKED:      # 本进程里已被证明这张卡解不了
        _note_hard_decode_skip(codec, f"本机 GPU 试过 {name}，不支持")
        return None
    if name not in cuvid_decoders(ffmpeg_bin):
        _note_hard_decode_skip(codec, f"本机 ffmpeg 没有编译 {name}")
        return None
    return name


def _count_frames_gpu(ffmpeg_bin: str, path: str, stream_sel: str,
                      decoder: str) -> Optional[int]:
    """档 2：NVDEC 硬解整片解码精确数帧。

    为什么用 ffmpeg 而不是 ffprobe：
      - ffprobe **没有 -hwaccel 选项**（实测 6.1.1：`Failed to set value 'cuda' for
        option 'hwaccel': Option not found`），也没有 -nostdin；
      - 所以改成 ffmpeg 硬解到 null muxer，再从 `-progress pipe:1` 的最后一行
        `frame=N` 取解码帧数（第一行是 frame=0，必须取最后一行）。
      - 解码器要**显式**指定（`-c:v xxx_cuvid` 放在 -i 之前），不能用 -hwaccel cuda
        的自动选择：后者遇到不支持的编码器会静默转软解，帧数没错但方法名不实。
    """
    cmd = [ffmpeg_bin, "-nostdin", "-v", "error",
           "-c:v", decoder,
           "-i", path, "-map", f"0:{stream_sel}", "-f", "null", "-",
           "-progress", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=FRAME_COUNT_TIMEOUT,
                       stdin=subprocess.DEVNULL, env=_ffmpeg_env())
    # 注意：硬解失败时 ffmpeg 仍可能返回 0（实测），所以不能只看 returncode。
    err = (r.stderr or "").lower()
    if any(k in err for k in _CUDA_FATAL_KEYS):
        GpuProbe.disable_runtime("硬解数帧命中 CUDA 致命错误")
        raise RuntimeError("硬解数帧：CUDA 不可用")
    if any(k in err for k in _NVDEC_CODEC_KEYS):
        with _CUVID_LOCK:
            first_time = decoder not in _NVDEC_CODEC_BLOCKED
            _NVDEC_CODEC_BLOCKED.add(decoder)
        if first_time:
            # 并发时同一编码器可能被多个线程同时试（首波各一次），只报一次
            _vlog(f"{decoder}: 本机 GPU 不支持该编码器，已拉黑"
                  f"（本进程后续同类文件直接走包数档）")
        raise RuntimeError(f"硬解数帧：{decoder} 不受支持")
    if r.returncode != 0 and not (r.stdout or "").strip():
        raise RuntimeError("硬解数帧失败")

    frames: Optional[int] = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("frame="):
            try:
                frames = int(line.split("=", 1)[1].strip())
            except (IndexError, ValueError):
                pass
    return frames if frames and frames > 0 else None


def _count_packets(ffprobe_bin: str, path: str, stream_sel: str) -> Optional[int]:
    """档 3：只解复用数包，不解码（CPU 侧的降级手段）。"""
    data = _run_ffprobe_json(ffprobe_bin, [
        "-select_streams", stream_sel, "-count_packets",
        "-show_entries", "stream=nb_read_packets", path,
    ], timeout=FRAME_COUNT_TIMEOUT)
    streams = data.get("streams") or []
    if not streams:
        return None
    try:
        n = int(streams[0].get("nb_read_packets"))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def probe_video(path: str, ffprobe_bin: str, ffmpeg_bin: str,
                opts: "Options") -> dict:
    """探测单个视频，返回渲染所需的属性字典（失败时带 error 字段）。"""
    key = _probe_cache_key(path)
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached

    info: Dict = {"error": None}
    try:
        data = _run_ffprobe_json(ffprobe_bin, [
            "-show_format", "-show_streams", path,
        ])
    except Exception as exc:                       # noqa: BLE001
        info["error"] = f"不可探测（{_short_err(exc)}）"
        with _PROBE_LOCK:
            _PROBE_CACHE[key] = info
        return info

    streams = data.get("streams") or []
    # 封面流（attached_pic）也是 codec_type=video，必须排掉，否则会当成主视频流
    video_streams = [s for s in streams
                     if s.get("codec_type") == "video"
                     and not (s.get("disposition") or {}).get("attached_pic")]
    if not video_streams:
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        info["error"] = "不可探测（无视频流）"
        with _PROBE_LOCK:
            _PROBE_CACHE[key] = info
        return info

    video = video_streams[0]
    # ffprobe 的 -select_streams 接受「裸数字 = 全局流索引」。用 v:N 会在有封面流时
    # 选到封面（mjpeg，1 帧），所以这里取选中流的绝对 index。
    try:
        stream_sel = str(int(video.get("index") or 0))
    except (TypeError, ValueError):
        stream_sel = "v:0"

    rotation = _extract_rotation(video)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if rotation in (90, 270):
        width, height = height, width

    fmt = data.get("format") or {}
    duration = _parse_rate(fmt.get("duration"))
    if duration is None:
        duration = _parse_rate(video.get("duration"))
    if duration is None:
        tags = video.get("tags") or {}
        duration = _parse_rate(tags.get("DURATION") or tags.get("duration"))

    bitrate = _parse_rate(fmt.get("bit_rate"))
    if not bitrate:
        bitrate = _parse_rate(video.get("bit_rate"))
    if not bitrate and duration and duration > 0:
        try:
            bitrate = os.path.getsize(path) * 8 / duration
        except OSError:
            bitrate = None

    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))

    nb_frames = None
    try:
        nb = int(video.get("nb_frames") or 0)
        nb_frames = nb if nb > 0 else None
    except (TypeError, ValueError):
        nb_frames = None

    # ── 帧数四级降级链 ──
    # **只在请求了帧数列（--show frames / --show-all）时才跑**：这一列是唯一
    # 需要解码或至少解复用的，默认不显示也就不算 —— 默认路径因此只剩一次
    # ffprobe，大目录能快一大截。
    frames: Optional[int] = None
    frames_src = "—"
    if opts.want_frames:
        if not opts.deep:
            if nb_frames:
                frames, frames_src = nb_frames, "包头"

        if frames is None and not opts.fast:
            codec = video.get("codec_name") or ""
            # 档 2 需要三个条件同时成立：没被 --cpu 禁掉、CUDA 探针通过、
            # 且**这个编码器**在本机有 NVDEC 解码器（否则 ffmpeg 会静默软解，标签会撒谎）
            decoder = (cuvid_decoder_for(codec, ffmpeg_bin)
                       if (not opts.cpu) and GpuProbe.available(ffmpeg_bin, opts.verbose)
                       else None)
            if decoder:
                try:
                    n = _count_frames_gpu(ffmpeg_bin, path, stream_sel, decoder)
                    if n:
                        frames, frames_src = n, "硬解"
                except Exception:                   # noqa: BLE001
                    frames = None
            if frames is None:
                try:
                    n = _count_packets(ffprobe_bin, path, stream_sel)
                    if n:
                        frames, frames_src = n, "包数"
                except Exception:                   # noqa: BLE001
                    frames = None

        if frames is None and duration and fps:
            est = int(round(duration * fps))
            if est > 0:
                frames, frames_src = est, "估算"

    bits = _parse_bits(video)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    info.update({
        "width": width,
        "height": height,
        "fps": fps,
        "bitrate": bitrate,
        "frames": frames,
        "frames_src": frames_src,
        "codec": video.get("codec_name") or "—",
        "container": _normalize_container(fmt.get("format_name") or "", path),
        "duration": duration,
        "pix_fmt": video.get("pix_fmt") or "—",
        "bits": bits,
        "hdr": bool(_has_hdr_side_data(video) or _looks_hdr(video, bits)),
        "audio": _describe_audio(audios),
        "subs": _describe_subs(subs),
    })

    with _PROBE_LOCK:
        _PROBE_CACHE[key] = info
    return info


def _short_err(exc: Exception) -> str:
    msg = str(exc).strip().replace("\n", " ")
    if not msg:
        msg = exc.__class__.__name__
    return msg[:60]


def _describe_audio(audios: List[dict]) -> str:
    if not audios:
        return "无"
    a = audios[0]
    codec = a.get("codec_name") or "?"
    ch = a.get("channels")
    try:
        ch_s = f"{int(ch)}ch" if ch else "?ch"
    except (TypeError, ValueError):
        ch_s = "?ch"
    rate = _parse_rate(a.get("sample_rate"))
    rate_s = f"{rate / 1000:.1f}kHz" if rate else "?kHz"
    s = f"{codec} {ch_s} {rate_s}"
    if len(audios) > 1:
        s += f" (+{len(audios) - 1})"
    return s


def _describe_subs(subs: List[dict]) -> str:
    if not subs:
        return "无"
    names = []
    for s in subs[:3]:
        names.append(s.get("codec_name") or "?")
    s = ",".join(names)
    if len(subs) > 3:
        s += f",…共{len(subs)}"
    return f"{len(subs)}轨({s})"


# ═══════════════════════════════════════════════════════════════════
#  条目收集与排序
# ═══════════════════════════════════════════════════════════════════


class Entry:
    __slots__ = ("name", "path", "st", "is_video", "info")

    def __init__(self, name: str, path: str, st: os.stat_result) -> None:
        self.name = name
        self.path = path
        self.st = st
        self.is_video = False
        self.info: Optional[dict] = None

    @property
    def sort_mtime(self) -> float:
        return self.st.st_mtime


def _is_hidden(name: str) -> bool:
    return name != "." and name != ".." and name.startswith(".")


def _collect_dir(dirpath: str, args: "Options") -> List[Entry]:
    entries: List[Entry] = []
    try:
        with os.scandir(dirpath) as it:
            for de in it:
                name = de.name
                if not args.all and not args.almost_all and _is_hidden(name):
                    continue
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError as exc:
                    print(f"vidls: 无法访问 '{name}': {exc.strerror}", file=sys.stderr)
                    _mark_error()
                    continue
                entries.append(Entry(name, os.path.join(dirpath, name), st))
    except OSError as exc:
        print(f"vidls: 无法打开目录 '{dirpath}': {exc.strerror}", file=sys.stderr)
        _mark_error()
        return []

    if args.all:
        for dot in (".", ".."):
            p = os.path.join(dirpath, dot)
            try:
                entries.append(Entry(dot, p, os.lstat(p)))
            except OSError:
                pass
    return entries


def _mark_error() -> None:
    global _EXIT_HAD_ERROR
    _EXIT_HAD_ERROR = True


def _sort_key(name: str):
    try:
        return locale.strxfrm(name)
    except Exception:
        return name


def sort_entries(entries: List[Entry], args: "Options") -> None:
    entries.sort(key=lambda e: _sort_key(e.name))
    if args.sort_time:
        entries.sort(key=lambda e: e.st.st_mtime, reverse=True)
    elif args.sort_size:
        entries.sort(key=lambda e: e.st.st_size, reverse=True)
    if args.reverse:
        entries.reverse()


def mark_videos(entries: List[Entry]) -> None:
    for e in entries:
        if statmod.S_ISDIR(e.st.st_mode):
            continue
        ext = os.path.splitext(e.name)[1].lower()
        if ext in VIDEO_EXTS and e.st.st_size > 0:
            e.is_video = True


# ═══════════════════════════════════════════════════════════════════
#  版式渲染
# ═══════════════════════════════════════════════════════════════════


def terminal_width() -> int:
    try:
        cols = os.get_terminal_size(sys.stdout.fileno()).columns
        if cols > 0:      # pty 未设置尺寸时会返回 0，必须回退，否则退化成每行一个
            return cols
    except Exception:
        pass
    try:
        cols = int(os.environ.get("COLUMNS", ""))
        if cols > 0:
            return cols
    except ValueError:
        pass
    return 80


TAB_SIZE = 8


def indent_pad(from_col: int, to_col: int) -> str:
    """coreutils 的 indent()：从显示列 from_col 走到 to_col 的填充串。

    GNU ls 的列间填充用的是 Tab + 空格（`ls … | cat -A` 能看到 ^I），但这套东西
    的**取舍规则是实测拟合出来的**（629 个受控样本，MSYS coreutils 8.32）：

      1. 制表位是 8 的倍数；能落到的制表位个数 `n = to//8 - from//8`。
      2. **只有当「n 个 Tab + 尾部空格」比「纯空格」更短时才用 Tab**，判据是
         `n >= last_stop - from_col`（last_stop 是最靠近 to 的那个制表位）。

    第 2 条是 2026-09-17 修掉的 bug：旧写法只看「下一个制表位是否 ≤ to_col」，
    于是 `from=7, to=15` 会吐「1 Tab + 7 空格」—— 与「8 空格」**字节数相同**，
    但 ls 这时选空格；同样 `from=79, to=81` 也是。逐字节对比时这两处会红。
    """
    if to_col <= from_col:
        return ""
    n_tabs = to_col // TAB_SIZE - from_col // TAB_SIZE
    if n_tabs <= 0:
        return " " * (to_col - from_col)
    last_stop = (to_col // TAB_SIZE) * TAB_SIZE
    if n_tabs >= last_stop - from_col:
        return " " * (to_col - from_col)
    return "\t" * n_tabs + " " * (to_col - last_stop)


def format_grid(names: Sequence[str], width: int) -> List[str]:
    """coreutils ls 的列布局：竖着填（column-major），列宽 = 该列**实际最大显示宽**。

    三条判据（2026-09-17 在 Windows + MSYS coreutils 8.32 上用 1040 组随机布局验证，
    全部逐字节一致）：

      1. `总宽 < 终端宽`（**严格小于**），`总宽 = sum(列宽) + 2*(cols-1)`；
      2. **最后一列不能是空的**，即 `(cols-1) * rows < n`；
      3. 从最大列数往下试，第一个满足上面两条的胜出；连 2 列都不行就退化成每行一个。

    第 2 条与「列宽下限 3」都是这次修掉的 bug：
      · **没有「列宽下限 3」这回事** —— 旧代码每列取 `max(3, w)`，于是单字符名字的目录
        会比 ls 多出两格空白（实测 ls 的 `a  b  c` 间隔是 2，不是 4）。
      · **末列判据**：30 个单字符名字、宽 80 时，16 列的总宽 45 < 80 看似能放，
        但 rows=2 会让最后一列一个条目都没有，ls 会退到 15 列。实测 100 个双字符名字、
        宽 200 → 50 列（不是 51）。
    """
    n = len(names)
    if n == 0:
        return []
    if n == 1:
        return [names[0]]

    widths = [disp_width(x) for x in names]
    chosen: Optional[Tuple[int, List[int]]] = None
    for cols in range(n, 1, -1):
        rows = (n + cols - 1) // cols
        if (cols - 1) * rows >= n:      # 最后一列会是空的 → ls 不接受
            continue
        colw: List[int] = []
        for c in range(cols):
            seg = widths[c * rows:(c + 1) * rows]
            colw.append(max(seg) if seg else 0)
        total = sum(colw) + 2 * (cols - 1)
        if total < width:
            chosen = (cols, colw)
            break

    if chosen is None:
        return list(names)

    cols, colw = chosen
    rows = (n + cols - 1) // cols
    starts: List[int] = []
    pos = 0
    for c in range(cols):
        starts.append(pos)
        pos += colw[c] + 2

    lines: List[str] = []
    for r in range(rows):
        buf: List[str] = []
        col = 0
        for c in range(cols):
            i = c * rows + r
            if i >= n:
                continue
            buf.append(names[i])
            col += disp_width(names[i])
            if c != cols - 1:
                buf.append(indent_pad(col, starts[c + 1]))
                col = starts[c + 1]
        line = "".join(buf).rstrip()
        if line:
            lines.append(line)
    return lines


def human_size(nbytes: int) -> str:
    """coreutils 语义：base 1024，**向上取整**，<10 保留 1 位小数。"""
    n = int(nbytes)
    if n < 1024:
        return str(n)
    units = "KMGTPEZY"
    val = float(n)
    i = -1                      # 除法次数；-1 表示还没除（<1024，直接返回字节）
    while val >= 1024 and i + 1 < len(units) - 1:
        val /= 1024.0
        i += 1
    sfx = units[i] if i >= 0 else ""
    if val < 10:
        return f"{math.ceil(val * 10) / 10:.1f}{sfx}"
    return f"{math.ceil(val)}{sfx}"


def _month_abbr(dt: datetime) -> str:
    """月份缩写跟随 LC_TIME（与 coreutils ls 用同一套 locale 数据）。"""
    try:
        return dt.strftime("%b")
    except Exception:
        return MONTHS[dt.month - 1]


def _fmt_mtime(mtime: float, now: float) -> str:
    """coreutils 语义：半年内（含未来时间）→ 'Sep 16 07:37'，否则 'Sep 16  2025'。"""
    dt = datetime.fromtimestamp(mtime)
    mon = _month_abbr(dt)
    if mtime < now and (now - mtime) > 31556952 / 2:
        return f"{mon} {dt.day:2d}  {dt.year}"
    return f"{mon} {dt.day:2d} {dt.hour:02d}:{dt.minute:02d}"


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except Exception:
        return str(gid)


def _filemode(st: os.stat_result) -> str:
    return statmod.filemode(st.st_mode)


# ── 视频属性列 ──
#
# 三组键：
#   DEFAULT_KEYS  —— 默认就显示（只靠一次 ffprobe 的元数据，全是免费的）
#   OPTIONAL_KEYS —— 要用 --show <名字> / --show-all 才出来
#   ALL_KEYS      —— 展示顺序（过滤用），所以「帧数」被 --show frames 打开时
#                    仍在原来的位置（比特率与编码器之间），不会跑到最右边
#
# 注意 **frames 属于可选列**（2026-09-17 起）：它是唯一要解码/解复用的那一列，
# 默认不显示也就不计算 —— 想省时间的目录直接 `vidls` 就行，要看帧数才付代价。
DEFAULT_KEYS = ["res", "fps", "bitrate", "codec", "container", "duration"]
OPTIONAL_KEYS = ["frames", "pixfmt", "bits", "audio", "subs", "hdr"]
ALL_KEYS = ["res", "fps", "bitrate", "frames", "codec", "container", "duration",
            "pixfmt", "bits", "audio", "subs", "hdr"]
FIELD_HEADER = {
    "res": "分辨率", "fps": "帧率", "bitrate": "比特率", "frames": "帧数",
    "codec": "编码器", "container": "容器", "duration": "时长",
    "pixfmt": "像素格式", "bits": "位深", "audio": "音轨",
    "subs": "字幕", "hdr": "HDR",
}
FIELD_ALIGN = {
    "res": "left", "fps": "right", "bitrate": "right", "frames": "right",
    "codec": "left", "container": "left", "duration": "right",
    "pixfmt": "left", "bits": "left", "audio": "left", "subs": "left",
    "hdr": "left",
}


def active_fields(args: "Options") -> List[str]:
    """当前该显示哪些列：默认列 + 被 --show/--show-all 打开的可选列。

    按 **ALL_KEYS 的顺序**返回（不是「默认列在前、可选列追加在后」），
    这样 `--show frames` 时「帧数」就落在它原本的位置上。
    """
    keys = set(DEFAULT_KEYS) | set(args.show_set)
    return [k for k in ALL_KEYS if k in keys]


def video_cells(entry: Entry, fields: Sequence[str]) -> Dict[str, str]:
    info = entry.info or {}
    if info.get("error"):
        return {"res": info["error"]}

    cells: Dict[str, str] = {}
    for key in fields:
        if key == "res":
            w, h = info.get("width") or 0, info.get("height") or 0
            cells[key] = f"{w}x{h}" if w and h else "—"
        elif key == "fps":
            cells[key] = _fmt_fps(info.get("fps"))
        elif key == "bitrate":
            cells[key] = _fmt_bitrate(info.get("bitrate"))
        elif key == "frames":
            n = info.get("frames")
            cells[key] = f"{n}帧[{info.get('frames_src', '—')}]" if n else "—"
        elif key == "codec":
            cells[key] = info.get("codec") or "—"
        elif key == "container":
            cells[key] = info.get("container") or "—"
        elif key == "duration":
            cells[key] = _fmt_duration(info.get("duration"))
        elif key == "pixfmt":
            cells[key] = info.get("pix_fmt") or "—"
        elif key == "bits":
            cells[key] = f"{info.get('bits') or 8}bit"
        elif key == "audio":
            cells[key] = info.get("audio") or "—"
        elif key == "subs":
            cells[key] = info.get("subs") or "—"
        elif key == "hdr":
            cells[key] = "HDR" if info.get("hdr") else "SDR"
    return cells


def render_block(entries: List[Entry], args: "Options", out) -> None:
    """渲染一个目录块（默认分组版式或 -l 长格式）。"""
    fields = active_fields(args)
    videos = [e for e in entries if e.is_video]
    video_rows = {id(e): video_cells(e, fields) for e in videos}
    # 探测失败的行不构成表格：它的错误串很长，会把整张表的列宽撑爆
    ok_videos = [e for e in videos if not (e.info or {}).get("error")]

    colw: Dict[str, int] = {}
    for key in fields:
        colw[key] = max([disp_width(FIELD_HEADER[key])] +
                        [disp_width(video_rows[id(e)].get(key, "")) for e in ok_videos] or [1])
    namew = max([disp_width(e.name) for e in ok_videos], default=0)

    if args.long:
        _render_long(entries, args, videos, ok_videos, video_rows, fields, colw, out)
        return

    if args.one:
        for e in entries:
            line = e.name
            row = video_rows.get(id(e))
            if row:
                line = _join_video(e.name, namew, row, fields, colw)
            print(line, file=out)
        return

    # 默认版式：连续的非视频项攒成 run 走网格；视频每行独占
    run: List[str] = []
    width = terminal_width()
    header_printed = False

    def flush_run() -> None:
        nonlocal run
        if run:
            for line in format_grid(run, width):
                print(line, file=out)
            run = []

    for e in entries:
        row = video_rows.get(id(e))
        if row is None:
            run.append(e.name)
            continue
        flush_run()
        if (e.info or {}).get("error"):
            print(f"{e.name}  {row['res']}", file=out)
            continue
        if len(ok_videos) >= 2 and not header_printed:
            print(_header_line(namew, fields, colw), file=out)
            header_printed = True
        print(_join_video(e.name, namew, row, fields, colw), file=out)
    flush_run()


def _header_line(namew: int, fields: Sequence[str], colw: Dict[str, int]) -> str:
    parts = [pad_display("", namew)]
    for key in fields:
        parts.append(pad_display(FIELD_HEADER[key], colw[key], FIELD_ALIGN[key]))
    return "  ".join(parts).rstrip()


def _join_video(name: str, namew: int, row: Dict[str, str],
                fields: Sequence[str], colw: Dict[str, int]) -> str:
    if "res" in row and len(row) == 1:        # 探测失败
        return f"{pad_display(name, namew)}  {row['res']}"
    parts = [pad_display(name, namew)]
    for key in fields:
        parts.append(pad_display(row.get(key, ""), colw[key], FIELD_ALIGN[key]))
    return "  ".join(parts).rstrip()


def _render_long(entries: List[Entry], args: "Options", videos: List[Entry],
                 ok_videos: List[Entry], video_rows: Dict[int, Dict[str, str]],
                 fields: Sequence[str], colw: Dict[str, int], out) -> None:
    now = time.time()
    nlinkw = max([len(str(e.st.st_nlink)) for e in entries], default=1)
    sizes: List[str] = []
    for e in entries:
        sizes.append(human_size(e.st.st_size) if args.human else str(e.st.st_size))
    sizew = max([len(s) for s in sizes], default=1)

    owners = [_user_name(e.st.st_uid) for e in entries]
    groups = [_group_name(e.st.st_gid) for e in entries]
    ownerw = max([disp_width(x) for x in owners], default=1)
    groupw = max([disp_width(x) for x in groups], default=1)
    # 普通文件行的名字是最后一列（与 ls 相同，后面不留空格）；视频行后面还要接属性列，
    # 所以额外按**视频名宽度**补齐 —— 补的是视频自己的最大名宽，不是全表名宽，
    # 否则目录里有个超长普通文件名就会把属性列整体推到屏幕外面去。
    namew = max([disp_width(e.name) for e in entries], default=0)
    vnamew = max([disp_width(e.name) for e in ok_videos], default=0)

    def build_head(entry: Entry, row_no: int, name_width: int) -> str:
        return (f"{_filemode(entry.st)} {entry.st.st_nlink:>{nlinkw}} "
                f"{pad_display(owners[row_no], ownerw)} "
                f"{pad_display(groups[row_no], groupw)} "
                f"{sizes[row_no]:>{sizew}} "
                f"{_fmt_mtime(entry.st.st_mtime, now)} "
                f"{pad_display(entry.name, name_width)}")

    header_printed = False
    for i, e in enumerate(entries):
        row = video_rows.get(id(e))
        if row is None:
            print(build_head(e, i, namew).rstrip(), file=out)
            continue
        # 注意：视频行**不能 rstrip** head —— 那会把名字的补齐空格一起削掉，
        # 于是每条视频的属性列都从自己名字后面开始，整块看起来就是歪的。
        head = build_head(e, i, vnamew)
        if len(ok_videos) >= 2 and not header_printed:
            print(pad_display("", disp_width(head)) + "  " +
                  "  ".join(pad_display(FIELD_HEADER[k], colw[k], FIELD_ALIGN[k])
                            for k in fields), file=out)
            header_printed = True
        if (e.info or {}).get("error"):
            print(f"{head.rstrip()}  {row['res']}", file=out)
            continue
        cells = "  ".join(pad_display(row.get(k, ""), colw[k], FIELD_ALIGN[k])
                          for k in fields)
        print(f"{head}  {cells}".rstrip(), file=out)


# ═══════════════════════════════════════════════════════════════════
#  --install：环境自检 + 依赖安装 + PATH 接入
# ═══════════════════════════════════════════════════════════════════

_PKG_MANAGERS = [
    ("apt-get", ["apt-get", "install", "-y"], {"ffmpeg": "ffmpeg", "python3": "python3"}),
    ("dnf", ["dnf", "install", "-y"], {"ffmpeg": "ffmpeg", "python3": "python3"}),
    ("yum", ["yum", "install", "-y"], {"ffmpeg": "ffmpeg", "python3": "python3"}),
    ("apk", ["apk", "add"], {"ffmpeg": "ffmpeg", "python3": "python3"}),
    ("brew", ["brew", "install"], {"ffmpeg": "ffmpeg", "python3": "python3"}),
]

_RC_FILES = [".zshrc", ".bashrc", ".profile"]


def _which_any(names: Sequence[str]) -> Optional[str]:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _detect_pkg_manager():
    for pm, cmd, pkgs in _PKG_MANAGERS:
        if shutil.which(pm):
            return pm, cmd, pkgs
    return None


def _confirm(question: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        ans = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def _try_install(pm: str, cmd: Sequence[str], pkg: str, yes: bool) -> bool:
    full = list(cmd) + [pkg]
    print(f"    命令：{' '.join(full)}")
    if not yes and not _confirm(f"    是否用 {pm} 安装 {pkg}？"):
        print("    已跳过（可手动执行上面的命令）")
        return False
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=900)
    except Exception as exc:                      # noqa: BLE001
        print(f"    安装失败：{exc}")
        return False
    if r.returncode != 0:
        print(f"    安装失败（rc={r.returncode}）："
              f"{(r.stderr or '').strip().splitlines()[-1] if (r.stderr or '').strip() else ''}")
        return False
    print(f"    {pkg} 安装完成")
    return True


def _ensure_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
        if not mode & 0o111:
            path.chmod(mode | 0o755)
            print(f"  [OK] 已赋予可执行权限：{path}")
        else:
            print(f"  [OK] 可执行权限已就绪：{path}")
        return True
    except OSError as exc:
        print(f"  [WARN] 无法设置可执行权限 {path}：{exc}")
        return False


def _pick_install_dir(prefix: Optional[str]) -> Path:
    if prefix:
        return Path(prefix).expanduser()
    candidates = [Path("/usr/local/bin"), Path.home() / ".local/bin"]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".vidls_write_test"
            probe.touch()
            probe.unlink()
            return c
        except OSError:
            continue
    return Path.home() / ".local/bin"


def _path_contains(d: Path) -> bool:
    cur = os.environ.get("PATH", "")
    target = str(d)
    return any(os.path.abspath(p) == os.path.abspath(target) for p in cur.split(":") if p)


def _append_to_rc(d: Path) -> Optional[Path]:
    line = f'export PATH="{d}:$PATH"'
    home = Path.home()
    shell = os.environ.get("SHELL", "")
    order = list(_RC_FILES)
    if shell.endswith("zsh"):
        order = [".zshrc", ".bashrc", ".profile"]
    elif shell.endswith("bash"):
        order = [".bashrc", ".profile", ".zshrc"]

    chosen: Optional[Path] = None
    for name in order:
        rc = home / name
        if rc.exists():
            chosen = rc
            break
    if chosen is None:
        chosen = home / order[0]

    try:
        existing = chosen.read_text(encoding="utf-8") if chosen.exists() else ""
    except OSError:
        existing = ""
    if line in existing or str(d) in existing:
        print(f"  [OK] {chosen} 已包含 PATH 配置，未重复写入")
        return chosen
    try:
        with chosen.open("a", encoding="utf-8") as f:
            f.write(f"\n# vidls: 由 vidls --install 自动添加\n{line}\n")
        print(f"  [OK] 已写入 {chosen}：{line}")
        return chosen
    except OSError as exc:
        print(f"  [WARN] 无法写入 {chosen}：{exc}")
        return None


def cmd_install(args: "Options") -> int:
    print("== vidls 环境自检与安装 ==")
    print()
    rc = 0

    # 1. 运行时
    print(f"[1/4] 运行时：python3 {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 8):
        print("  [FAIL] 需要 Python >= 3.8")
        rc = 1
    else:
        print("  [OK] Python 版本满足要求")

    # 2. ffprobe / ffmpeg
    print()
    print("[2/4] FFmpeg 工具链")
    ffprobe_bin = args.ffprobe or _which_any(["ffprobe"])
    ffmpeg_bin = args.ffmpeg or _which_any(["ffmpeg"])
    pm_info = _detect_pkg_manager()
    for label, found in (("ffprobe", ffprobe_bin), ("ffmpeg", ffmpeg_bin)):
        if found:
            print(f"  [OK] {label}: {found}")
        else:
            print(f"  [MISS] {label}: 未找到")
            if pm_info and (args.yes or _confirm(f"    是否尝试安装 {label}？")):
                pm, cmd, pkgs = pm_info
                ok = _try_install(pm, cmd, pkgs["ffmpeg"], True)
                if not ok:
                    rc = 1
            else:
                print("    请手动安装 FFmpeg（ffprobe 与 ffmpeg 通常在同一包内）后重试；"
                      "或显式指定路径：vidls --ffprobe /path/to/ffprobe")
                rc = 1

    # 3. GPU / 硬解能力
    print()
    print("[3/4] GPU 与硬解能力")
    nvidia = _which_any(["nvidia-smi"])
    if nvidia:
        try:
            r = subprocess.run([nvidia, "-L"], capture_output=True, text=True, timeout=15)
            # nvidia-smi 存在但缺 libnvidia-ml.so 时 rc≠0，报错在 stdout —— 必须按失败处理
            names = [l for l in (r.stdout or "").splitlines() if l.strip()]
            if r.returncode == 0 and names and not names[0].startswith("NVIDIA-SMI"):
                print(f"  [OK] nvidia-smi: {names[0]}")
                for extra in names[1:]:
                    print(f"       {extra}")
            else:
                detail = names[0] if names else (r.stderr or "").strip()
                print(f"  [INFO] nvidia-smi 调用异常（{(detail or '无输出')[:80]}）"
                      f" —— 按无 GPU 处理")
        except Exception as exc:                   # noqa: BLE001
            print(f"  [WARN] nvidia-smi 调用失败：{exc}")
    else:
        print("  [INFO] 未找到 nvidia-smi —— 将走 CPU 降级链（帧数用「包数/包头」档）")

    ffmpeg_for_probe = ffmpeg_bin or "ffmpeg"
    if args.cpu or args.fast:
        print("  [SKIP] 指定了 --cpu/--fast，跳过硬解探针")
        tier = "包数（CPU 降级）" if not args.fast else "包头（--fast）"
        print(f"  [INFO] 帧数档位：{tier}")
    elif shutil.which(ffmpeg_for_probe) or os.path.exists(ffmpeg_for_probe):
        GpuProbe.reset()
        ok = GpuProbe.available(ffmpeg_for_probe, verbose=True)
        print(f"  [{'OK' if ok else 'INFO'}] CUDA 硬解：{'可用' if ok else '不可用'}"
              f" —— 帧数档位：{'硬解（精确数帧）' if ok else '包数 / 包头（CPU 降级）'}")
    else:
        print("  [INFO] ffmpeg 不可用，跳过硬解探针")

    # 4. 自身安装 + PATH
    print()
    print("[4/4] 命令安装与 PATH 接入")
    here = Path(__file__).resolve().parent
    # vidll 是 `vidls -l` 的快捷方式（ll 替代），它自己的脚本里没有逻辑，
    # 只是把 -l 转给 vidls.sh —— 所以两个命令一起装、一起维护。
    launchers = [("vidls", here / "vidls.sh"), ("vidll", here / "vidll.sh")]
    for _, path in launchers:
        if not path.exists():
            print(f"  [FAIL] 未找到启动器 {path}")
            return 1
        _ensure_executable(path)

    target_dir = _pick_install_dir(args.prefix)
    for name, launcher in launchers:
        link = target_dir / name
        try:
            if link.is_symlink() or link.exists():
                try:
                    current = os.readlink(str(link)) if link.is_symlink() else None
                except OSError:
                    current = None
                if current == str(launcher):
                    print(f"  [OK] 软链已存在且指向正确：{link} -> {launcher}")
                else:
                    if not (args.yes or _confirm(
                            f"    {link} 已存在（指向 {current}），是否覆盖？")):
                        print(f"    已跳过覆盖 {name}")
                    else:
                        link.unlink()
                        link.symlink_to(launcher)
                        print(f"  [OK] 已重建软链：{link} -> {launcher}")
            else:
                link.symlink_to(launcher)
                print(f"  [OK] 已创建软链：{link} -> {launcher}")
        except OSError as exc:
            print(f"  [FAIL] 无法在 {target_dir} 创建软链：{exc}")
            print(f"    可手动执行：ln -sf {launcher} {link}")
            return 1

    if _path_contains(target_dir):
        print(f"  [OK] {target_dir} 已在 PATH 中")
    else:
        print(f"  [WARN] {target_dir} 不在当前 PATH 中，尝试写入 shell 配置")
        written = _append_to_rc(target_dir)
        if written is None:
            print(f"    请手动执行：export PATH=\"{target_dir}:$PATH\"")
            rc = 1
        else:
            print(f"    新开一个终端（或 source {written}）后即可使用 vidls / vidll 命令")

    print()
    print("== 完成 ==")
    print("  用法：vidls            # 列当前目录（视频带属性）")
    print("        vidll            # 等价 vidls -l（长格式 + 视频属性）")
    print("        vidls --show frames       # 加上帧数列（会解码/解复用，默认不算）")
    print("        vidls --show-all          # 全部可选列（帧数/像素格式/位深/音轨/字幕/HDR）")
    if rc == 0:
        print("  自检结果：全部通过")
    else:
        print("  自检结果：有未解决项（见上面的 [FAIL]/[MISS]/[WARN]）")
    return rc


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════


class Options:
    """ argparse.Namespace 的轻量替身，便于类型标注。"""
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vidls",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="ls / ll 的替代品：非视频文件按原生 ls 版式渲染，"
                    "视频文件追加分辨率 / 帧率 / 比特率 / 编码器 / 容器 / 时长；"
                    "帧数（唯一要解码的那一列）默认既不显示也不计算，"
                    "要看请加 --show frames 或 --show-all。",
        epilog="""\
示例：
  vidls                        列出当前目录（视频独占一行并带属性）
  vidls -l                     ll 长格式 + 视频属性
  vidll                        等价 vidls -l（就是 ll；--install 会一起装上）
  vidll -h                     等价 vidls -l -h
  vidls -lh                    长格式 + 人类可读体积
  vidls --show frames          加上「帧数」列（唯一会解码/解复用的列）
  vidls --show frames --cpu    要帧数但只走 CPU 档（0.1s，不解码）
  vidls --show-all             显示全部可选列（帧数/像素格式/位深/音轨/字幕/HDR）
  vidls --show audio,subs      只追加音轨与字幕两列
  vidls -lt                    按修改时间排序（最新在前）
  vidls --install              自检环境、装依赖并把 vidls / vidll 接入 PATH

帧数这一列怎么来的（要 --show frames / --show-all 才出现）：
  它是唯一需要解码或至少解复用的列，**默认不显示也就不算** —— 所以裸 `vidls`
  的成本只有一次 ffprobe。四条降级链，每条结果带来源标签（成本实测自 T4 + 1080p，
  900 帧样本）：
    包头  容器头 nb_frames（免费，仅 mp4/mov 可靠）
    硬解  ffmpeg 显式 xxx_cuvid 整片解码数帧（0.55s 固定 + 时长/≈500fps）
    包数  ffprobe -count_packets（0.10s 且与时长无关，只解复用不解码）
    估算  duration × fps（免费兜底）
  长片或大目录建议 `--show frames --cpu`（或 --fast）：硬解与包数在实测样本上帧数
  完全相同，但硬解按 时长/500fps 付代价（2 小时片子约 7 分钟），包数恒定 0.1s。
  --cpu / --fast / --deep 都只是「帧数怎么算」的开关，没请求帧数列时会提示并忽略。

注意：ffprobe 没有 -hwaccel 选项（实测 6.1.1），所以「硬解」档只能用 ffmpeg
跑 `-f null -` 再从 `-progress pipe:1` 的最后一行 frame= 取数；且必须用显式
`-c:v xxx_cuvid`（`-hwaccel cuda` 遇到不支持的编码器会静默转软解）。

退出码：0 正常 / 1 有条目 stat 或探测失败 / 2 参数错误
""",
    )
    parser.add_argument("--help", action="help", help="显示本帮助并退出")
    parser.add_argument("--version", action="version",
                        version=f"vidls {VERSION}", help="显示版本并退出")

    disp = parser.add_argument_group("显示开关（对齐 ls / ll）")
    disp.add_argument("-l", "--long", action="store_true", help="长格式（等价 ll）")
    disp.add_argument("-1", dest="one", action="store_true", help="每行一个条目")
    disp.add_argument("-a", "--all", action="store_true", help="包含 . 与 ..")
    disp.add_argument("-A", "--almost-all", action="store_true",
                      help="包含隐藏项，但不列 . 与 ..")
    disp.add_argument("-d", "--directory", action="store_true",
                      help="目录本身作为条目，不展开")
    disp.add_argument("-t", dest="sort_time", action="store_true",
                      help="按修改时间排序（最新在前）")
    disp.add_argument("-S", dest="sort_size", action="store_true",
                      help="按体积排序（最大在前）")
    disp.add_argument("-r", "--reverse", action="store_true", help="反转排序")
    disp.add_argument("-h", "--human-readable", dest="human", action="store_true",
                      help="长格式下体积用人类可读单位（注意：-h 不是 help，帮助看 --help）")

    probe = parser.add_argument_group("探测策略")
    probe.add_argument("--cpu", action="store_true",
                       help="【需 --show frames】帧数强制走 CPU 档：跳过 GPU 硬解，"
                            "用「包数」（只解复用、与时长无关，比硬解快 20 倍以上）。"
                            "没请求帧数列时本开关无效")
    probe.add_argument("--fast", action="store_true",
                       help="【需 --show frames】永不解码：帧数只取容器头，缺则估算。"
                            "没请求帧数列时本开关无效")
    probe.add_argument("--deep", action="store_true",
                       help="【需 --show frames】强制重新数帧：忽略容器头，"
                            "GPU 可用走硬解（整片解码，实测 0.55s 固定 + 时长/≈500fps），"
                            "否则走包数。没请求帧数列时本开关无效")
    probe.add_argument("--show", metavar="LIST", default="",
                       help="追加可选列，逗号分隔："
                            "frames,pixfmt,bits,audio,subs,hdr")
    # 注意：ls 的 -a/--all 已经被「显示隐藏文件」占用，这里只能叫 --show-all
    probe.add_argument("--show-all", dest="show_all", action="store_true",
                       help="追加全部可选列"
                            "（等价 --show frames,pixfmt,bits,audio,subs,hdr）")
    probe.add_argument("-j", "--jobs", type=int, default=0, metavar="N",
                       help="探测并发数（0=按系统资源自动决定）")
    probe.add_argument("--ffmpeg-bin", dest="ffmpeg", default=None, metavar="PATH",
                       help="ffmpeg 路径或所在目录（ffprobe 取同目录）")
    probe.add_argument("--ffprobe-bin", dest="ffprobe", default=None, metavar="PATH",
                       help="ffprobe 路径（默认与 ffmpeg 同目录）")
    probe.add_argument("-v", "--verbose", action="store_true",
                       help="打印资源探测与档位信息到 stderr")

    inst = parser.add_argument_group("安装")
    inst.add_argument("-I", "--install", action="store_true",
                      help="自检环境、安装缺失依赖并把 vidls 接入 PATH")
    inst.add_argument("--prefix", default=None, metavar="DIR",
                      help="--install 时指定命令安装目录（默认 /usr/local/bin 或 ~/.local/bin）")
    inst.add_argument("-y", "--yes", action="store_true",
                      help="--install 时自动确认，不交互提问")

    parser.add_argument("paths", nargs="*", default=None,
                        help="要列出的路径（缺省为当前目录）")
    return parser


def _resolve_bins(args) -> Tuple[str, str]:
    if args.ffmpeg:
        p = Path(args.ffmpeg)
        if p.is_dir():
            ffmpeg_bin, ffprobe_bin = str(p / "ffmpeg"), str(p / "ffprobe")
        else:
            ffmpeg_bin = str(p)
            ffprobe_bin = str(p.with_name("ffprobe"))
    else:
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
    if args.ffprobe:
        ffprobe_bin = args.ffprobe
    return ffmpeg_bin, ffprobe_bin


def _parse_show(spec: str, show_all: bool) -> List[str]:
    if show_all:
        return list(OPTIONAL_KEYS)
    if not spec.strip():
        return []
    fields = []
    for item in spec.split(","):
        item = item.strip().lower()
        if not item:
            continue
        if item not in OPTIONAL_KEYS:
            print(f"vidls: 未知的 --show 字段 '{item}'"
                  f"（可选：{','.join(OPTIONAL_KEYS)}）", file=sys.stderr)
            sys.exit(2)
        if item not in fields:
            fields.append(item)
    return fields


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _EXIT_HAD_ERROR, _VERBOSE
    _EXIT_HAD_ERROR = False

    # 排序与月份都要跟随本地 locale，才能与 coreutils ls 的输出对齐
    for category in (locale.LC_COLLATE, locale.LC_TIME):
        try:
            locale.setlocale(category, "")
        except locale.Error:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    args.show_set = _parse_show(args.show, args.show_all)
    # 帧数是可选列：只有请求了它才值得去解码/解复用（见 probe_video 的降级链）。
    args.want_frames = "frames" in args.show_set
    _VERBOSE = bool(args.verbose)

    if not args.want_frames:
        # --deep / --cpu / --fast 都是「帧数怎么算」的开关，没请求帧数时无处施加。
        # 只提示、不当错误：它们本来就只是优化提示，用户加不加都不该让命令失败。
        ignored = [n for n, on in (("--deep", args.deep), ("--cpu", args.cpu),
                                   ("--fast", args.fast)) if on]
        if ignored:
            print(f"vidls: {'/'.join(ignored)} 只在显示帧数时才有意义 —— "
                  f"当前没请求帧数列，已忽略。要帧数请加 --show frames（或 --show-all）",
                  file=sys.stderr)

    if args.install:
        return cmd_install(args)

    ffmpeg_bin, ffprobe_bin = _resolve_bins(args)

    for tool, label in ((ffprobe_bin, "ffprobe"),):
        if not (os.path.exists(tool) or shutil.which(tool)):
            print(f"[ERROR] 未找到 {label}（{tool}）。"
                  f"请先安装 FFmpeg，或运行 vidls --install 自动配置。", file=sys.stderr)
            return 1

    paths = list(args.paths) if args.paths else ["."]

    # GNU ls 会**连操作数本身一起排序**（实测 `ls dir_a /tmp/emptydir` 里 dir_a 在前），
    # 所以先把所有操作数按同一套排序规则排好，再切成「普通文件」与「目录」两组。
    operands: List[Entry] = []
    for p in paths:
        try:
            st = os.lstat(p)
        except OSError as exc:
            print(f"vidls: 无法访问 '{p}': {exc.strerror}", file=sys.stderr)
            _mark_error()
            continue
        operands.append(Entry(p, p, st))
    sort_entries(operands, args)

    file_entries = [e for e in operands
                    if not (statmod.S_ISDIR(e.st.st_mode) and not args.directory)]
    dir_args = [e.name for e in operands
                if statmod.S_ISDIR(e.st.st_mode) and not args.directory]

    blocks: List[Tuple[Optional[str], List[Entry]]] = []
    if file_entries:
        blocks.append((None, file_entries))
    for d in dir_args:
        ents = _collect_dir(d, args)
        sort_entries(ents, args)
        blocks.append((d, ents))

    # 分类 + 并行探测
    all_videos: List[Entry] = []
    for _, ents in blocks:
        mark_videos(ents)
        all_videos.extend(e for e in ents if e.is_video)

    if all_videos:
        # 是否会走到「整片解码」档：请求了帧数、非 --fast、非强制 CPU 时才可能用 GPU 硬解，
        # 那种情况并发要额外封顶，避免多路 NVDEC 抢显存；不算帧数就完全不会解码。
        may_decode = args.want_frames and (not args.fast) and (not args.cpu)
        cpu, _cpu_src = _detect_cpu()
        total_gb, avail_gb, _mem_src = _detect_memory_gb()
        workers = compute_probe_parallelism(len(all_videos), cpu, total_gb, avail_gb,
                                            args.jobs, deep=may_decode)
        if args.verbose:
            print(f"[vidls] CPU={cpu} ({_cpu_src})  "
                  f"MEM 总={total_gb:.2f}GB 可用={avail_gb:.2f}GB ({_mem_src})",
                  file=sys.stderr)
            print(f"[vidls] 视频 {len(all_videos)} 个，探测并发 {workers}",
                  file=sys.stderr)
            if not args.want_frames:
                print("[vidls] 未请求帧数列 —— 不走任何解码档（要帧数加 --show frames）",
                      file=sys.stderr)
            elif not (args.cpu or args.fast):
                print(f"[vidls] GPU 硬解："
                      f"{'可用' if GpuProbe.available(ffmpeg_bin, args.verbose) else '不可用'}",
                      file=sys.stderr)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(probe_video, e.path, ffprobe_bin, ffmpeg_bin, args): e
                       for e in all_videos}
            for fut in as_completed(futures):
                e = futures[fut]
                try:
                    e.info = fut.result()
                except Exception as exc:                  # noqa: BLE001
                    e.info = {"error": f"不可探测（{_short_err(exc)}）"}
                if e.info.get("error"):
                    _mark_error()

    out = sys.stdout
    multi = len(blocks) > 1            # 与 ls 一致：操作数多于一个时才打标题
    first = True
    for name, ents in blocks:
        if multi and name is not None:
            if not first:
                print(file=out)
            print(f"{name}:", file=out)
        if args.long and name is not None:
            # 与 coreutils ls 一致：total 以 1K 块计；-h 时连它也带单位（实测 `total 316K`）
            total_kb = sum(e.st.st_blocks for e in ents
                           if hasattr(e.st, "st_blocks")) // 2
            shown = human_size(total_kb * 1024) if args.human else str(total_kb)
            print(f"total {shown}", file=out)
        if ents:
            render_block(ents, args, out)
        first = False

    return 1 if _EXIT_HAD_ERROR else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
