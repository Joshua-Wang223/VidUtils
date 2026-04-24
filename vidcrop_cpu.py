#!/usr/bin/env python3
"""
vidcrop.py - 基于 FFmpeg 的视频批量居中裁剪工具
自动根据编码器选择容器、智能处理编码参数。
支持 CPU (libx264/265) 和 GPU (h264_nvenc 等) 编码器质量控制。

用法示例：
    # 批量处理文件夹
    python vidcrop.py --input ./videos --output ./cropped --output-width 640 --output-height 360 --codec libx264 --crf 17

    # CPU 编码（使用 -crf）
    python vidcrop.py --input video.mp4 --output out.mp4 --output-width 640 --output-height 360
    python vidcrop.py --input video.mp4 --output out_dir --output-width 640 --output-height 360 --codec libx265 --crf 18 --preset medium
    
    # GPU 编码（使用 -cq）
    python vidcrop.py --input video.mp4 --output out_dir --output-width 640 --output-height 360 --codec h264_nvenc --cq 21 --preset p4


"""

import argparse
import subprocess
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# 常见视频文件扩展名
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.webm', '.ts'}

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
    cq: Optional[int],
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

    print(f"处理文件：{input_file} (原尺寸: {actual_width}x{actual_height})")

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

    # 检查是否已存在
    if output_file.exists() and not overwrite:
        print(f"  输出文件 {output_file} 已存在，跳过（使用 --overwrite 覆盖）。")
        return True

    # 构建 ffmpeg 命令（智能添加参数）
    cmd = ['ffmpeg', '-hide_banner', '-err_detect', 'ignore_err', '-fflags', '+genpts+discardcorrupt', '-i', str(input_file), '-vf', vf_filter, '-c:v', codec]

    # 质量控制参数处理
    quality_param_added = False
    if cq is not None:
        if encoder_supports_cq(codec):
            cmd += ['-cq', str(cq)]
            quality_param_added = True
        else:
            print(f"  警告：编码器 {codec} 不支持 -cq 参数，已忽略 --cq {cq}。", file=sys.stderr)
    
    if crf is not None and not quality_param_added:
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
    try:
        subprocess.run(cmd, check=True)
        print(f"  完成：{output_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  处理失败：{e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="批量裁剪视频（居中裁剪），自动根据编码器选择容器并智能处理参数。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
编码器 preset 说明（以 h264_nvenc 为例）：
  p1 = fastest (最低质量，最快速度)
  p2 = faster
  p3 = fast
  p4 = medium (默认)
  p5 = slow
  p6 = slower
  p7 = slowest (最高质量，最慢速度)
  
对于 libx264 / libx265，preset 可选值：
  ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow, placebo

质量控制参数说明：
  --crf : 用于 CPU 编码器（libx264/265 等），数值 0-51，越小质量越高（默认 17）。
  --cq  : 用于 GPU 编码器（h264_nvenc/hevc_nvenc 等），数值 0-51，越小质量越高。
          若同时提供 --crf 和 --cq，优先使用 --cq 适配当前编码器。
"""
    )
    parser.add_argument('--input', required=True, help='输入视频文件或包含视频的文件夹')
    parser.add_argument('--output', required=True, help='输出文件（单文件时）或输出文件夹（批量时）')
    parser.add_argument('--original-width', type=int, help='原始视频宽度（若不提供则自动检测）')
    parser.add_argument('--original-height', type=int, help='原始视频高度（若不提供则自动检测）')
    parser.add_argument('--output-width', type=int, required=True, help='目标视频宽度')
    parser.add_argument('--output-height', type=int, required=True, help='目标视频高度')
    parser.add_argument('--codec', default='libx264', help='视频编码器（默认 libx264）')
    parser.add_argument('--crf', type=int, default=None, help='CRF 质量控制值（0-51 越小质量越高，用于 CPU 编码器，如 libx264/265）')
    parser.add_argument('--cq', type=int, default=None, help='CQ 质量控制值（0-51，用于 GPU 编码器，如 h264_nvenc. 16约对应 CRF 17）')
    parser.add_argument('--preset', default='slow', help='编码器预设（默认 slow，详见下方说明）')
    parser.add_argument('--overwrite', action='store_true', help='覆盖已存在的输出文件')
    parser.add_argument('--container', help='手动指定封装容器扩展名（如 .mp4, .webm），覆盖自动选择')

    args = parser.parse_args()

    # 参数逻辑检查：若同时提供了 --crf 和 --cq，提示优先使用 --cq
    if args.crf is not None and args.cq is not None:
        print("提示：同时指定了 --crf 和 --cq，将根据实际编码器自动选用。")

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
            batch_mode=batch_mode
        ):
            success_count += 1

    print(f"\n处理完成：成功 {success_count} / 总数 {len(video_files)}")


if __name__ == '__main__':
    main()