#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vidcrop_cpu_v0.py – 批量视频裁剪/等比填充工具（单进程顺序版）

功能
────
• 支持两种模式：
  - crop：居中裁剪（目标尺寸不能大于源尺寸）
  - cover：等比缩放至完全覆盖目标区域后居中裁剪
• 单文件处理或目录批量处理，支持递归扫描并保持输出目录结构
• 自动检测视频尺寸、估算总帧数，显示细粒度实时进度条和 ETA
• 支持大多数 FFmpeg 视频编码器、CRF 质量控制、preset 预设、像素格式指定
• 音频默认 copy 流拷贝，无音频流时自动跳过
• 输出容器自动匹配或手动指定，并进行兼容性检查
• 完善的 Ctrl+C 中断处理，安全终止所有 FFmpeg 子进程
• 新增 --dry-run 仅预览命令不执行转码；--log 将所有输出记录到日志文件
• 支持通过 --extra-args 传递额外 FFmpeg 参数

主要参数
────────
--input                输入视频文件或目录（必选）
--output               输出视频文件或目录（必选）
--output-width         目标视频宽度（必选）
--output-height        目标视频高度（必选）
--mode                 crop | cover（默认 crop）
--codec                视频编码器（默认 libx264）
--crf                  CRF 质量值（默认使用 FFmpeg 内置默认值）
--preset               编码器预设（默认 slow，仅 libx264/libx265）
--pix-fmt              输出像素格式（auto / none / 具体格式）
--container            手动指定封装扩展名，如 .mp4 / .mkv
--overwrite            覆盖已存在的输出文件
-r, --recursive        递归扫描输入目录
--dry-run              仅生成并显示 FFmpeg 命令，不执行转码
--log LOG_FILE         将所有输出记录到指定日志文件
--extra-args ...       紧随 '--' 后的自定义 FFmpeg 参数（必须放在命令最后）

典型用例
────────
# 单文件裁剪到 1280×720，libx264，CRF 20
python vidcrop_cpu_v0.py --input video.mp4 --output out.mp4 \
    --output-width 1280 --output-height 720 --codec libx264 --crf 20

# 批量覆盖填充到 1920×1080，保持目录结构，覆盖已有
python vidcrop_cpu_v0.py --input ./videos --output ./cropped \
    --output-width 1920 --output-height 1080 --mode cover \
    --codec libx265 --crf 18 --recursive --overwrite

# 预览命令，不转码
python vidcrop_cpu_v0.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --dry-run

# 记录到日志文件
python vidcrop_cpu_v0.py --input ./videos --output ./out \
    --output-width 1280 --output-height 720 --log process.log

# 在 FFmpeg 命令末尾追加自定义参数
python vidcrop_cpu_v0.py --input video.mp4 --output out.mp4 \
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
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

# ══════════════════ 常量 ══════════════════
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".m4v", ".webm", ".ts", ".mpg", ".mpeg"
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
    "libx264", "libx265", "libvpx-vp9", "libvpx",
    "libaom-av1", "librav1e",
}

PRESET_VALUES = {
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow", "placebo",
}

DEFAULT_PRESET = "slow"

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

# 进程管理
_ACTIVE_PROCS: Set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()
_STOP_REQUESTED = threading.Event()



# ══════════════════ 日志记录 ══════════════════
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


def setup_log(log_path: str) -> None:
    """重定向标准输出/错误到 Tee 对象"""
    sys.stdout = Tee(sys.stdout, log_path)
    sys.stderr = Tee(sys.stderr, log_path)


# ══════════════════ 进程/信号处理 ══════════════════
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


# ══════════════════ 通用工具 ══════════════════
def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"[ERROR] 系统中未找到 {tool}，请先安装 FFmpeg。", file=sys.stderr)
            sys.exit(1)


def _fmt_size(n_bytes: int) -> str:
    n = float(n_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m {sec:02d}s"
    if hours:
        return f"{hours}h {minutes:02d}m {sec:02d}s"
    return f"{minutes}m {sec:02d}s"


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


def normalize_extra_args(extra: Optional[List[str]]) -> List[str]:
    if not extra:
        return []
    extra = list(extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    return extra


def normalize_container(container: Optional[str]) -> Optional[str]:
    if not container:
        return None
    c = container.strip()
    if not c:
        return None
    if not c.startswith("."):
        c = "." + c
    return c.lower()


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
                f"像素格式 {pix_fmt} 要求输出宽高均为偶数；"
                f"当前为 {width}x{height}"
            )
    elif pf in PIX_FMT_REQUIRE_EVEN_WIDTH:
        if width % 2 != 0:
            raise ValueError(
                f"像素格式 {pix_fmt} 要求输出宽度为偶数；当前宽度为 {width}"
            )


# ══════════════════ 编码器/容器 ══════════════════
def get_extension_from_codec(codec: str) -> Optional[str]:
    c = codec.lower()
    if c == "copy":
        return None
    if c in CODEC_CONTAINER_MAP:
        return CODEC_CONTAINER_MAP[c]
    # 模糊匹配前缀
    base = c.split("_", 1)[0]
    for key, ext in CODEC_CONTAINER_MAP.items():
        if key != "copy" and key.startswith(base):
            return ext
    return ".mp4"


def check_container_compatibility(ext: str, codec: str) -> bool:
    ext_lower = ext.lower()
    codec_lower = codec.lower()

    # .mkv 几乎兼容一切
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


# ══════════════════ FFprobe ══════════════════
def get_video_dimensions(filepath: str) -> Tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", filepath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception as exc:
        print(f"错误：无法获取视频尺寸 {filepath} - {exc}", file=sys.stderr)
        raise


def _get_total_frames(filepath: str) -> Optional[int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames,duration,avg_frame_rate,r_frame_rate:format=duration",
        "-of", "json", filepath,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None

        s = streams[0]
        nb = s.get("nb_frames", "")
        if nb and nb not in {"N/A", ""}:
            return max(1, int(nb))

        duration = 0.0
        for candidate in (
            s.get("duration"),
            (data.get("format") or {}).get("duration"),
        ):
            try:
                duration = float(candidate or 0)
                if duration > 0:
                    break
            except Exception:
                pass

        rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
        num_s, _, den_s = rate.partition("/")
        try:
            num = float(num_s)
            den = float(den_s)
            fps = num / den if den > 0 else 0.0
        except Exception:
            fps = 0.0

        if duration > 0 and fps > 0:
            return max(1, int(duration * fps))
    except Exception:
        pass
    return None


# ══════════════════ [COLOR-FIX] 色彩元数据注入 ══════════════════

def probe_color_metadata(video_file: Path) -> Optional[Dict[str, str]]:
    """
    用 ffprobe 读取视频第一个视频流的色彩元数据
    （color_range / color_space / color_primaries / color_transfer）。

    Args:
        video_file: 视频文件路径。

    Returns:
        四项色彩值的字典（可能为 'unknown'）；无法探测时返回 None。
    """
    if not os.path.isfile(str(video_file)):
        return None

    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=color_range,color_space,color_primaries,color_transfer",
        "-of", "default=noprint_wrappers=1",
        str(video_file),
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
        )
        if r.returncode != 0:
            return None
        meta: Dict[str, str] = {}
        for line in r.stdout.strip().splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                meta[key] = val
        return meta or None
    except Exception:
        # 任何异常（超时/解析失败等）均容错返回 None，不阻断流程
        return None


def build_color_args(video_file: Path) -> List[str]:
    """
    检测源视频色彩元数据并构造 ffmpeg 输出端色彩参数列表。

    有值则逐项透传（color_space→-colorspace 等）；unknown/缺失项
    回退 BT.709 + Full Range(pc)：-colorspace bt709 -color_primaries bt709
    -color_trc bt709 -color_range pc（0-255 全范围）。

    Args:
        video_file: 源视频路径，用于探测色彩元数据。

    Returns:
        ffmpeg 参数列表，可直接追加到输出命令（输出文件之前）。
    """
    meta = probe_color_metadata(video_file) or {}

    def _pick(key: str, fallback: str) -> str:
        val = meta.get(key, "")
        if val and val.lower() not in ("unknown", "unspecified"):
            return val
        return fallback

    return [
        "-colorspace", _pick("color_space", "bt709"),
        "-color_primaries", _pick("color_primaries", "bt709"),
        "-color_trc", _pick("color_transfer", "bt709"),
        "-color_range", _pick("color_range", "pc"),
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
            vals[color_map[key]] = extra_args[i + 1]
            i += 2
        else:
            i += 1
    if not vals:
        return None
    return "setparams=" + ":".join(f"{k}={v}" for k, v in vals.items())


# ══════════════════ 滤镜构建（支持 crop/cover） ══════════════════
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


# ══════════════════ 输入输出路径 ══════════════════
def collect_video_files(input_path: Path, recursive: bool = False) -> List[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() in VIDEO_EXTENSIONS:
            return [input_path]
        print(f"警告：{input_path} 不是支持的视频文件格式，将跳过。", file=sys.stderr)
        return []

    if input_path.is_dir():
        iterator = input_path.rglob("*") if recursive else input_path.iterdir()
        files = [
            p for p in iterator
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        ]
        return sorted(set(files))

    print(f"错误：输入路径 {input_path} 不存在。", file=sys.stderr)
    return []


def make_output_file(
    input_file: Path,
    output_path: Path,
    codec: str,
    container: Optional[str],
    batch_mode: bool,
    input_root: Optional[Path],
) -> Path:
    if batch_mode or output_path.is_dir() or not output_path.suffix:
        ext = container if container else get_extension_from_codec(codec)
        if ext is None:
            ext = input_file.suffix

        output_dir = output_path
        if input_root and input_root.is_dir():
            try:
                rel_parent = input_file.parent.relative_to(input_root)
                output_dir = output_dir / rel_parent
            except Exception:
                pass

        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{input_file.stem}_cropped{ext}"

    output_file = output_path
    if container:
        output_file = output_file.with_suffix(container)

    output_file.parent.mkdir(parents=True, exist_ok=True)

    if container is None and codec.lower() != "copy":
        if not check_container_compatibility(output_file.suffix, codec):
            rec_ext = get_extension_from_codec(codec)
            print(
                f"  警告：输出扩展名 '{output_file.suffix}' 可能与编码器 "
                f"'{codec}' 不兼容，推荐使用 '{rec_ext}'",
                file=sys.stderr,
            )

    return output_file


# ══════════════════ 进度与 FFmpeg 执行 ══════════════════
def _inject_progress(cmd: List[str]) -> List[str]:
    prog_cmd = list(cmd)
    try:
        i_idx = prog_cmd.index("-i")
        prog_cmd[i_idx:i_idx] = ["-progress", "pipe:1", "-nostats"]
    except ValueError:
        prog_cmd += ["-progress", "pipe:1", "-nostats"]
    return prog_cmd


def _run_with_progress(cmd: List[str], total_frames: Optional[int]) -> Tuple[int, str]:
    prog_cmd = _inject_progress(cmd)
    term_w = shutil.get_terminal_size((80, 24)).columns
    bar_w = max(10, min(30, term_w - 52))

    t0 = time.perf_counter()
    frame = 0
    stderr_lines: List[str] = []
    proc: Optional[subprocess.Popen] = None

    try:
        proc = subprocess.Popen(
            prog_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        _register_proc(proc)

        assert proc.stderr is not None
        assert proc.stdout is not None

        def _drain_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line)
                if len(stderr_lines) > 500:
                    del stderr_lines[:250]

        t_stderr = threading.Thread(target=_drain_stderr, daemon=True)
        t_stderr.start()

        for raw_line in proc.stdout:
            line = raw_line.strip()
            if "=" not in line:
                continue

            key, _, val = line.partition("=")
            if key != "frame":
                continue

            try:
                frame = int(val.strip())
            except ValueError:
                continue

            elapsed = time.perf_counter() - t0
            fps = frame / elapsed if elapsed > 0 else 0.0

            if total_frames and total_frames > 0:
                pct = min(frame / total_frames, 1.0)
                filled = int(bar_w * pct)
                bar = "█" * filled + "░" * (bar_w - filled)
                eta = (total_frames - frame) / fps if fps > 0 else 0.0
                print(
                    f"\r  [{bar}] {pct * 100:5.1f}%"
                    f"  {frame}/{total_frames}帧"
                    f"  {fps:5.1f}fps"
                    f"  ETA {eta:.0f}s   ",
                    end="",
                    flush=True,
                )
            else:
                print(
                    f"\r  已处理 {frame} 帧  {fps:.1f}fps  {elapsed:.1f}s   ",
                    end="",
                    flush=True,
                )

        rc = proc.wait()
        t_stderr.join(timeout=3.0)
        print()
        return rc, "".join(stderr_lines)

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
        print()
        return 130, "".join(stderr_lines) or "interrupted"

    except Exception as exc:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        print()
        return 1, str(exc)

    finally:
        if proc is not None:
            _unregister_proc(proc)


# ══════════════════ 单文件处理 ══════════════════
def process_file(
    input_file: Path,
    output_path: Path,
    orig_width: Optional[int],
    orig_height: Optional[int],
    out_width: int,
    out_height: int,
    codec: str,
    crf: Optional[int],
    preset: str,
    pix_fmt: Optional[str],
    overwrite: bool,
    mode: str,                     # 'crop' or 'cover'
    container: Optional[str],
    batch_mode: bool,
    input_root: Optional[Path],
    extra_args: List[str],
    dry_run: bool = False,
) -> bool:
    if _STOP_REQUESTED.is_set():
        return False

    if orig_width is None or orig_height is None:
        try:
            actual_width, actual_height = get_video_dimensions(str(input_file))
        except Exception:
            return False
        if orig_width is not None:
            actual_width = orig_width
        if orig_height is not None:
            actual_height = orig_height
    else:
        actual_width, actual_height = orig_width, orig_height

    t_file_start = time.perf_counter()

    print(f"\n处理文件：{input_file}")
    print(f"  原始尺寸: {actual_width}x{actual_height} → 目标尺寸: {out_width}x{out_height}  模式: {mode}")

    if actual_width == out_width and actual_height == out_height:
        print(f"  跳过：目标尺寸与原始尺寸相同，无需处理。")
        return True

    if codec.lower() == "copy":
        print(
            "  跳过：使用视频裁剪/缩放滤镜时不能使用视频编码器 copy；"
            "请改用 libx264 / libx265 等软件编码器。",
            file=sys.stderr,
        )
        return False

    try:
        vf_filter = build_video_filter(mode, actual_width, actual_height, out_width, out_height)
    except ValueError as exc:
        print(f"  跳过：{exc}", file=sys.stderr)
        return False

    output_file = make_output_file(
        input_file=input_file,
        output_path=output_path,
        codec=codec,
        container=container,
        batch_mode=batch_mode,
        input_root=input_root,
    )

    if _same_path(input_file, output_file):
        print(
            f"  错误：输出文件与输入文件相同，拒绝覆盖源文件：{output_file}",
            file=sys.stderr,
        )
        return False

    if output_file.exists() and not overwrite:
        print(f"  输出文件 {output_file} 已存在，跳过（使用 --overwrite 覆盖）。")
        return True

    total_frames = _get_total_frames(str(input_file))

    def warn(msg: str) -> None:
        print(f"  警告：{msg}", file=sys.stderr)

    # [COLOR-FIX] 色彩元数据注入：检测源视频，有值透传，无值回退 BT.709+Full Range。
    # libx264 等软件编码器对输出端 -color_primaries/-color_trc 不写 VUI，
    # 需在滤镜链末尾追加 setparams 显式注入帧级色彩属性。
    color_args = build_color_args(input_file)
    _sp = _setparams_from_color_args(color_args)
    if _sp:
        vf_filter = f"{vf_filter},{_sp}"

    cmd: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-err_detect", "ignore_err",
        "-fflags", "+genpts+discardcorrupt",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-i", str(input_file),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-vf", vf_filter,
    ]

    cmd += build_encoder_options(codec, crf, preset, pix_fmt, warn)
    cmd += ["-c:a", "copy"]

    if output_file.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd += ["-movflags", "+faststart"]

    if extra_args:
        cmd += extra_args

    # [COLOR-FIX] 输出端色彩参数（写入容器 colr box / 编码器 VUI）；
    # 置于 extra_args 之后，与主项目合并注入行为一致。
    cmd += color_args

    cmd.append(str(output_file))

    # dry-run 模式：仅打印命令，不执行
    if dry_run:
        print(f"▶ {input_file.name} → {output_file.name}")
        print(f"  命令: {shlex.join(cmd)}\n")
        return True

    print(f"  执行命令：{shlex.join(cmd)}")

    rc, stderr_text = _run_with_progress(cmd, total_frames)

    if rc == 0:
        elapsed = time.perf_counter() - t_file_start
        try:
            in_size = input_file.stat().st_size
            out_size = output_file.stat().st_size
            ratio = (1.0 - out_size / in_size) * 100 if in_size > 0 else 0.0
            direction = "↓" if ratio >= 0 else "↑"
            print(f"  ✓ 完成：{output_file}")
            print(
                f"    大小：{_fmt_size(in_size)} → {_fmt_size(out_size)}"
                f"（{direction}{abs(ratio):.1f}%）  耗时：{_fmt_duration(elapsed)}"
            )
        except Exception:
            print(f"  ✓ 完成：{output_file}  耗时：{_fmt_duration(elapsed)}")
        return True

    err_lines = [line for line in stderr_text.strip().splitlines() if line.strip()]
    if err_lines:
        print(f"  FFmpeg 错误输出（末 {min(20, len(err_lines))} 行）：", file=sys.stderr)
        for line in err_lines[-20:]:
            print(f"    {line}", file=sys.stderr)

    print(f"  ✗ 处理失败（rc={rc}）", file=sys.stderr)

    if output_file.exists():
        try:
            output_file.unlink()
        except Exception:
            pass

    return False


# ══════════════════ CLI ══════════════════
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量裁剪/等比填充视频，纯 CPU 软件编码器。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：
  # 居中裁剪（crop）
  python vidcrop_cpu_v0_3.py --input video.mp4 --output out.mp4 \\
      --output-width 1280 --output-height 720 --codec libx264 --crf 20

  # 等比填充（cover）
  python vidcrop_cpu_v0_3.py --input video.mp4 --output out.mp4 \\
      --output-width 1920 --output-height 1080 --mode cover

  # 批量处理（保持目录结构）
  python vidcrop_cpu_v0_3.py --input ./videos --output ./cropped \\
      --output-width 1920 --output-height 1080 --codec libx265 --crf 18 \\
      --recursive --overwrite

  # 自定义附件参数（必须放在命令行最后）
  python vidcrop_cpu_v0_3.py --input video.mp4 --output out.mp4 \\
      --output-width 1280 --output-height 720 --extra-args -- -max_muxing_queue_size 4096

  # 预览将要执行的命令（不实际转码）
  python vidcrop_cpu_v0_3.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --dry-run

  # 将输出同时记录到日志文件
  python vidcrop_cpu_v0_3.py --input ./videos --output ./out \\
      --output-width 1280 --output-height 720 --log process.log
""",
    )

    parser.add_argument("--input", required=True, help="输入视频文件或包含视频的文件夹")
    parser.add_argument("--output", required=True, help="输出文件或输出文件夹")

    parser.add_argument("--original-width", type=int, help="原始视频宽度（不提供则自动检测）")
    parser.add_argument("--original-height", type=int, help="原始视频高度（不提供则自动检测）")

    parser.add_argument("--output-width", type=int, required=True, help="目标视频宽度")
    parser.add_argument("--output-height", type=int, required=True, help="目标视频高度")

    parser.add_argument(
        "--mode",
        default="crop",
        choices=["crop", "cover"],
        help="裁剪模式：crop=直接居中裁剪；cover=等比缩放+居中填充（默认 crop）",
    )

    parser.add_argument(
        "--codec",
        default="libx264",
        choices=sorted(CODEC_CONTAINER_MAP.keys()),
        help="视频编码器，默认 libx264",
    )
    parser.add_argument("--crf", type=int, default=None, help="CRF 质量值；不同编码器范围略有差异，常用 0-51 / 0-63")
    parser.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        choices=sorted(PRESET_VALUES),
        help=f"编码器预设，仅 libx264/libx265 生效，默认 {DEFAULT_PRESET}",
    )
    parser.add_argument(
        "--pix-fmt",
        default="auto",
        help="输出像素格式；auto=大多数编码器 yuv420p，ProRes 为 yuv422p10le；可填 none 禁用",
    )
    parser.add_argument("--container", help="手动指定封装扩展名，如 .mp4 / .mkv / .webm")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出文件")
    parser.add_argument("--recursive", "-r", action="store_true", help="递归扫描输入目录")
    parser.add_argument("--dry-run", action="store_true", help="仅生成并显示 FFmpeg 命令，不执行转码")
    parser.add_argument("--log", metavar="LOG_FILE", help="将所有输出同时记录到指定日志文件")

    parser.add_argument(
        "--extra-args",
        nargs=argparse.REMAINDER,
        help="追加到 FFmpeg 输出参数末尾的自定义参数；必须放在命令最后",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> Tuple[Optional[str], Optional[str], List[str]]:
    if args.original_width is not None and args.original_width <= 0:
        raise ValueError("--original-width 必须为正整数")
    if args.original_height is not None and args.original_height <= 0:
        raise ValueError("--original-height 必须为正整数")

    if args.crf is not None and not (0 <= args.crf <= 63):
        raise ValueError("--crf 建议范围为 0-63")

    container = normalize_container(args.container)
    pix_fmt = resolve_pix_fmt(args.codec, args.pix_fmt)
    validate_output_dimensions(args.output_width, args.output_height, pix_fmt)

    extra_args = normalize_extra_args(args.extra_args)
    return container, pix_fmt, extra_args


def main() -> int:
    check_tools()
    install_signal_handlers()

    try:
        args = parse_args()
        container_ext, pix_fmt, extra_args = validate_args(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    # 设置日志记录（如果指定了 --log）
    if args.log:
        setup_log(args.log)

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    video_files = collect_video_files(input_path, recursive=args.recursive)
    if not video_files:
        print("未找到任何视频文件，退出。", file=sys.stderr)
        return 1

    batch_mode = len(video_files) > 1 or input_path.is_dir()

    if batch_mode and output_path.suffix:
        print(
            f"警告：批量处理时输出路径 '{output_path}' 带扩展名，将视为目录。",
            file=sys.stderr,
        )
        output_path = output_path.with_suffix("")

    if batch_mode and input_path.is_dir() and _same_path(input_path, output_path):
        print("[ERROR] 批量模式下 --input 和 --output 不能为同一目录，避免覆盖源文件。", file=sys.stderr)
        return 2

    input_root = input_path if input_path.is_dir() else input_path.parent

    print(f"\n共找到 {len(video_files)} 个视频文件。")
    print(f"编码器：{args.codec}  CRF：{args.crf if args.crf is not None else 'FFmpeg默认'}  "
          f"preset：{args.preset}  pix_fmt：{pix_fmt or '不指定'}  模式：{args.mode}")

    if args.dry_run:
        print("─" * 64)
        print("DRY-RUN 模式：将仅显示命令，不执行转码。\n")

    t_main_start = time.perf_counter()
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
                preset=args.preset,
                pix_fmt=pix_fmt,
                overwrite=args.overwrite,
                mode=args.mode,
                container=container_ext,
                batch_mode=batch_mode,
                input_root=input_root,
                extra_args=extra_args,
                dry_run=args.dry_run,
            )
            if ok:
                success_count += 1

    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        print("\n[INFO] 已中断。", file=sys.stderr)
        return 130

    total_elapsed = time.perf_counter() - t_main_start

    if args.dry_run:
        print("─" * 64)
        print(f"DRY-RUN 完成：共预览 {len(video_files)} 个文件的命令，未执行任何转码。")
        return 0

    print(
        f"\n处理完成：成功 {success_count} / 总数 {len(video_files)}"
        f"  ·  总耗时 {_fmt_duration(total_elapsed)}"
    )
    if success_count > 1:
        print(f"  平均每文件：{_fmt_duration(total_elapsed / success_count)}")

    if _STOP_REQUESTED.is_set():
        return 130

    return 0 if success_count == len(video_files) else 1


if __name__ == "__main__":
    sys.exit(main())