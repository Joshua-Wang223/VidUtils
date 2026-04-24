#!/usr/bin/env python3
"""
vidcrop_cpu.py — 基于 FFmpeg 的视频批量居中裁剪工具（纯 CPU 版本）

功能概述
────────────────────────────────────────────────────────────────────
  • 单文件 / 文件夹批量处理，自动收集常见视频格式
    (.mp4 / .mkv / .avi / .mov / .flv / .wmv / .m4v / .webm / .ts)
  • 居中裁剪至指定分辨率，音频流直接复制不重编码
  • 仅使用 CPU 软件编码器，无任何硬件加速依赖，跨平台一致
        支持：libx264, libx265, libvpx, libvpx-vp9,
              libaom-av1, librav1e, prores(_ks), mpeg4, libxvid, copy
  • 输入输出尺寸相同时自动跳过，目标尺寸大于源尺寸时安全拒绝
  • 损坏帧容错：-err_detect ignore_err + -fflags +genpts+discardcorrupt

编码与容器
────────────────────────────────────────────────────────────────────
  • 根据编码器自动推断推荐容器扩展名：
        libx264/libx265/av1/mpeg4 → .mp4
        libvpx / libvpx-vp9       → .webm
        prores / prores_ks        → .mov
        libxvid                   → .avi
        copy                      → 沿用源文件扩展名
  • 单文件模式下若显式指定的输出扩展名与编码器不匹配，会给出警告
  • 批量模式下自动生成 <原文件名>_cropped<推荐扩展名>
  • 可用 --container 强制指定扩展名，覆盖自动推断

质量与预设参数
────────────────────────────────────────────────────────────────────
  • --crf   : 仅对支持 CRF 的编码器生效
              (libx264 / libx265 / libvpx / libvpx-vp9 / libaom-av1 / librav1e)
              数值 0–51，越小质量越高；不指定时沿用 FFmpeg 默认
  • --preset: 仅对 libx264 / libx265 生效，其他编码器会被忽略
              可选：ultrafast, superfast, veryfast, faster, fast,
                    medium, slow(默认), slower, veryslow, placebo
  • 编码器不支持的参数会被自动忽略并给出提示

用法示例
────────────────────────────────────────────────────────────────────
  # 1) 单文件 · 默认 libx264 · 自动识别原始分辨率
  python vidcrop_cpu.py \
      --input video.mp4 --output out.mp4 \
      --output-width 1280 --output-height 720

  # 2) 单文件 · libx265 高质量归档
  python vidcrop_cpu.py \
      --input video.mkv --output out.mp4 \
      --output-width 1920 --output-height 1080 \
      --codec libx265 --crf 18 --preset slow

  # 3) 单文件 · 极速转码（优先速度而非压缩率）
  python vidcrop_cpu.py \
      --input clip.mov --output clip_fast.mp4 \
      --output-width 1280 --output-height 720 \
      --codec libx264 --crf 23 --preset ultrafast

  # 4) 批量处理整个文件夹（输出目录自动创建）
  python vidcrop_cpu.py \
      --input ./videos --output ./cropped \
      --output-width 640 --output-height 360 \
      --codec libx264 --crf 20 --overwrite

  # 5) 批量转 WebM（VP9）用于网页分发
  python vidcrop_cpu.py \
      --input ./videos --output ./web \
      --output-width 854 --output-height 480 \
      --codec libvpx-vp9 --crf 32

  # 6) 批量转 AV1（高压缩比，适合长期存档）
  python vidcrop_cpu.py \
      --input ./videos --output ./av1_out \
      --output-width 1920 --output-height 1080 \
      --codec libaom-av1 --crf 30

  # 7) 手动指定容器扩展名（例如输出 .mkv 以保留更多元数据）
  python vidcrop_cpu.py \
      --input ./raw --output ./out \
      --output-width 1280 --output-height 720 \
      --codec libx264 --crf 18 --container .mkv

  # 8) 显式提供原始分辨率（跳过 ffprobe 探测，批量场景提速）
  python vidcrop_cpu.py \
      --input ./videos --output ./cropped \
      --original-width 3840 --original-height 2160 \
      --output-width 1920 --output-height 1080 \
      --codec libx265 --crf 20
"""

import argparse
import subprocess
import json
import sys
import time
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Tuple

# 常见视频文件扩展名
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.webm', '.ts'}

# 编码器 -> 推荐容器扩展名映射表（仅 CPU 软件编码器）
CODEC_CONTAINER_MAP = {
    'libx264': '.mp4',
    'libx265': '.mp4',
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

# 支持 -preset 参数的 CPU 编码器集合
PRESET_SUPPORTED_CODECS = {
    'libx264', 'libx265',
}

# 支持 -crf 参数的编码器集合（CPU 软件编码器）
CRF_SUPPORTED_CODECS = {
    'libx264', 'libx265',
    'libvpx-vp9', 'libvpx', 'libaom-av1', 'librav1e'
}


# ═══════════════════════════════════════════════════════════════════
#  进度条与耗时辅助
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


def _get_total_frames(filepath: str) -> Optional[int]:
    """
    通过 ffprobe 探测视频总帧数。
    优先读取 nb_frames 字段；不可用时按 duration × fps 估算。
    失败时返回 None（进度条退化为已处理帧数显示）。
    """
    cmd = [
        'ffprobe', '-v', 'error',
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

    向命令注入 -progress pipe:1 -nostats，使 FFmpeg 将结构化进度写入 stdout；
    通过独立线程异步收集 stderr，失败时可打印末尾诊断行。

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


def get_video_dimensions(filepath: str) -> Tuple[int, int]:
    """
    使用 ffprobe 获取视频的宽度和高度
    """
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
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
    """
    收集输入路径下的所有视频文件（单文件或文件夹）
    """
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


def build_crop_filter(orig_w: int, orig_h: int, out_w: int, out_h: int) -> str:
    """
    根据原始尺寸和目标尺寸生成居中裁剪的 crop 滤镜参数
    返回格式：crop=out_w:out_h:x:y
    """
    if out_w > orig_w or out_h > orig_h:
        raise ValueError(f"目标尺寸 ({out_w}x{out_h}) 不能大于原始尺寸 ({orig_w}x{orig_h})")

    x_offset = (orig_w - out_w) // 2
    y_offset = (orig_h - out_h) // 2
    return f"crop={out_w}:{out_h}:{x_offset}:{y_offset}"


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
    overwrite: bool,
    container: Optional[str] = None,
    batch_mode: bool = False
) -> bool:
    """
    处理单个视频文件
    """
    # 确定原始尺寸
    if orig_width is None or orig_height is None:
        try:
            actual_width, actual_height = get_video_dimensions(str(input_file))
        except Exception:
            return False
        if orig_width is None:
            orig_width = actual_width
        if orig_height is None:
            orig_height = actual_height
    else:
        actual_width, actual_height = orig_width, orig_height

    t_file_start = time.perf_counter()
    print(f"\n处理文件：{input_file}")
    print(f"  原始尺寸: {actual_width}x{actual_height} → 目标裁剪尺寸: {out_width}x{out_height}")

    # 尺寸相同时跳过
    if actual_width == out_width and actual_height == out_height:
        print(f"  跳过：目标尺寸 ({out_width}x{out_height}) 与原始尺寸相同，无需裁剪。")
        return True

    # 构建裁剪滤镜
    try:
        vf_filter = build_crop_filter(actual_width, actual_height, out_width, out_height)
    except ValueError as e:
        print(f"  跳过：{e}", file=sys.stderr)
        return False

    # 构建输出文件名（增强逻辑）
    if batch_mode or not output_path.suffix:
        # 批量模式 或 输出路径无扩展名 → 视为目录
        output_dir = output_path
        output_dir.mkdir(parents=True, exist_ok=True)
        # 根据编码器自动选择扩展名
        ext = container if container else get_extension_from_codec(codec)
        if ext is None:
            ext = input_file.suffix
        output_file = output_dir / f"{input_file.stem}_cropped{ext}"
    else:
        # 单文件模式，输出路径包含扩展名 → 视为文件
        output_file = output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if container is None and codec != 'copy':
            if not check_container_compatibility(output_file.suffix, codec):
                rec_ext = get_extension_from_codec(codec)
                print(f"  警告：指定的输出扩展名 '{output_file.suffix}' 可能与编码器 '{codec}' 不兼容，"
                      f"推荐使用 '{rec_ext}'", file=sys.stderr)

    # 探测总帧数（用于进度条）
    total_frames = _get_total_frames(str(input_file))

    # 检查是否已存在
    if output_file.exists() and not overwrite:
        print(f"  输出文件 {output_file} 已存在，跳过（使用 --overwrite 覆盖）。")
        return True

    # 构建 ffmpeg 命令（智能添加参数）
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-err_detect', 'ignore_err', '-fflags', '+genpts+discardcorrupt',
           '-i', str(input_file), '-vf', vf_filter, '-c:v', codec]

    # 质量控制参数处理（CRF）
    if crf is not None:
        if encoder_supports_crf(codec):
            cmd += ['-crf', str(crf)]
        else:
            print(f"  警告：编码器 {codec} 不支持 -crf 参数，已忽略 --crf {crf}。", file=sys.stderr)

    if encoder_supports_preset(codec):
        cmd += ['-preset', preset]
    else:
        if preset != 'slow':  # 用户主动修改了预设值才警告
            print(f"  警告：编码器 {codec} 不支持 -preset 参数，已忽略 '{preset}'。", file=sys.stderr)

    cmd += ['-c:a', 'copy', '-y' if overwrite else '-n', str(output_file)]

    print(f"  执行命令：{' '.join(cmd)}")
    rc, stderr_text = _run_with_progress(cmd, total_frames)
    if rc == 0:
        elapsed  = time.perf_counter() - t_file_start
        in_size  = input_file.stat().st_size
        out_size = output_file.stat().st_size
        ratio    = (1.0 - out_size / in_size) * 100 if in_size > 0 else 0.0
        direction = "↓" if ratio >= 0 else "↑"
        print(f"  ✓ 完成：{output_file}")
        print(f"    大小：{_fmt_size(in_size)} → {_fmt_size(out_size)}"
              f"（{direction}{abs(ratio):.1f}%）  耗时：{_fmt_duration(elapsed)}")
        return True
    else:
        err_lines = [l for l in stderr_text.strip().splitlines() if l.strip()]
        if err_lines:
            print(f"  FFmpeg 错误输出（末 {min(20, len(err_lines))} 行）：",
                  file=sys.stderr)
            for el in err_lines[-20:]:
                print(f"    {el}", file=sys.stderr)
        print(f"  ✗ 处理失败（rc={rc}）", file=sys.stderr)
        if output_file.exists():
            try:
                output_file.unlink()
            except Exception:
                pass
        return False


def main():
    parser = argparse.ArgumentParser(
        description="批量裁剪视频（居中裁剪），CPU 软件编码器版本。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
对于 libx264 / libx265，preset 可选值：
  ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow, placebo

质量控制参数说明：
  --crf : 用于 CPU 编码器（libx264/265 等），数值 0-51，越小质量越高（默认 17）。
"""
    )
    parser.add_argument('--input', required=True, help='输入视频文件或包含视频的文件夹')
    parser.add_argument('--output', required=True, help='输出文件（单文件时）或输出文件夹（批量时）')
    parser.add_argument('--original-width', type=int, help='原始视频宽度（若不提供则自动检测）')
    parser.add_argument('--original-height', type=int, help='原始视频高度（若不提供则自动检测）')
    parser.add_argument('--output-width', type=int, required=True, help='目标视频宽度')
    parser.add_argument('--output-height', type=int, required=True, help='目标视频高度')
    parser.add_argument('--codec', default='libx264', help='视频编码器（默认 libx264，仅支持 CPU 软件编码器）')
    parser.add_argument('--crf', type=int, default=None, help='CRF 质量控制值（0-51 越小质量越高，用于 CPU 编码器，如 libx264/265）')
    parser.add_argument('--preset', default='slow', help='编码器预设（默认 slow，详见下方说明）')
    parser.add_argument('--overwrite', action='store_true', help='覆盖已存在的输出文件')
    parser.add_argument('--container', help='手动指定封装容器扩展名（如 .mp4, .webm），覆盖自动选择')

    args = parser.parse_args()

    # 输入路径处理
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
    t_main_start = time.perf_counter()
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
            preset=args.preset,
            overwrite=args.overwrite,
            container=container_ext,
            batch_mode=batch_mode
        ):
            success_count += 1

    total_elapsed = time.perf_counter() - t_main_start
    print(f"\n处理完成：成功 {success_count} / 总数 {len(video_files)}"
          f"  ·  总耗时 {_fmt_duration(total_elapsed)}")
    if success_count > 1:
        print(f"  平均每文件：{_fmt_duration(total_elapsed / success_count)}")


if __name__ == '__main__':
    main()