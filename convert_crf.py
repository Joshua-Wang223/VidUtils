# ═══════════════════════════════════════════════════════════════════════════
#  各编码器相对 libx264 CRF 的线性等效关系
#      value = a × x264_crf + b   （再夹到 [lo, hi]）
#
#  基准轴统一取 libx264 CRF，于是任意两个编码器都能互转：
#      src_value --to_x264_crf--> x264_crf --from_x264_crf--> dst_value
#
#  本表是全局唯一来源：vidcrop_hwaccel.py / vidcrop_cpu_v2.py 均从这里 import，
#  不再各自硬编码偏移量（此前 cq_to_crf() 里的 +1/+4 与本表方向相反，已废弃）。
#
#  注意 VideoToolbox 的 a 为负：它的 q 值越高画质越好，与 CRF 含义相反。
# ═══════════════════════════════════════════════════════════════════════════
QUALITY_MAP = {
    # ---------- 软件编码器 ----------
    'libx264':               (1.0, 0.0, 0, 51),
    'libx265':               (1.0, 3.0, 0, 51),
    'libvpx-vp9':            (1.98, -14.46, 0, 63),

    # ---------- AV1 软件编码器 ----------
    # libaom: 官方文档 AV1 CRF 23 ≈ x264 CRF 19，即偏移 +4
    'libaom-av1':            (1.0, 4.0, 0, 63),
    # SVT-AV1: 速度极快，CRF 刻度略偏，达到同感知质量需高 2 左右
    'libsvtav1':             (1.0, 6.0, 0, 63),
    # rav1e: 0-255 量化器刻度。按下述实测标定（非文档推导）：
    #   实测（ffmpeg 6.1，640x480 testsrc2 2s，等体积，互差 <5%）：
    #     libaom crf 20/25/30/35  ↔  rav1e qp 60/80/100/120
    #   ⇒ rav1e_qp = 4 × (libaom_crf − 5)
    #   而本表 libaom 行为 libaom_crf = x264_crf + 4，代入得
    #   ⇒ rav1e_qp = 4 × (x264_crf − 1) = 4·x264_crf − 4
    # 旧值 (4.0, 16.0) 是按"qp ≈ 4 × libaom_crf"推导的，与实测差 20 个单位，已废弃。
    'librav1e':              (4.0, -4.0, 0, 255),

    # ---------- AV1 硬件编码器 ----------
    'av1_nvenc':             (1.0, 6.0, 0, 51),
    'av1_qsv':               (1.0, 5.0, 1, 51),
    'av1_amf':               (1.0, 3.0, 0, 51),

    # ---------- 其他硬件编码器 ----------
    'h264_nvenc':            (1.0, 5.0, 0, 51),
    'hevc_nvenc':            (1.0, 7.5, 0, 51),
    'h264_qsv':              (1.0, 3.5, 1, 51),
    'hevc_qsv':              (1.0, 4.5, 1, 51),
    'h264_amf':              (1.0, 2.0, 0, 51),
    'hevc_amf':              (1.0, 4.0, 0, 51),
    'h264_vaapi':            (1.0, 3.0, 0, 51),
    'hevc_vaapi':            (1.0, 5.0, 0, 51),

    # ---------- VideoToolbox（q 值越高画质越好） ----------
    'h264_videotoolbox':     (-99.0 / 51.0, 100.0, 1, 100),
    'hevc_videotoolbox':     (-99.0 / 51.0, 105.0, 1, 100),
}

# convert_crf() 输出用的展示名（键为 FFmpeg 编码器名）
_DISPLAY_NAMES = {
    'libx264': 'libx264',
    'libx265': 'libx265',
    'libvpx-vp9': 'libvpx-vp9',
    'libaom-av1': 'libaom-av1',
    'libsvtav1': 'libsvtav1',
    'librav1e': 'librav1e',
    'av1_nvenc': 'av1_nvenc',
    'av1_qsv': 'av1_qsv',
    'av1_amf': 'av1_amf',
    'h264_nvenc': 'NVENC H.264',
    'hevc_nvenc': 'NVENC H.265',
    'h264_qsv': 'QSV H.264',
    'hevc_qsv': 'QSV H.265',
    'h264_amf': 'AMF H.264',
    'hevc_amf': 'AMF H.265',
    'h264_vaapi': 'VAAPI H.264',
    'hevc_vaapi': 'VAAPI H.265',
    'h264_videotoolbox': 'VideoToolbox H.264',
    'hevc_videotoolbox': 'VideoToolbox H.265',
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def from_x264_crf(codec, x264_crf):
    """libx264 CRF → 指定编码器的等效质量值；未知编码器返回 None。"""
    m = QUALITY_MAP.get(str(codec).lower())
    if m is None:
        return None
    a, b, lo, hi = m
    return _clamp(a * float(x264_crf) + b, lo, hi)


def to_x264_crf(codec, value):
    """指定编码器的质量值 → 等效 libx264 CRF；未知编码器或 a≈0 时返回 None。"""
    m = QUALITY_MAP.get(str(codec).lower())
    if m is None:
        return None
    a, b, lo, hi = m
    if abs(a) < 1e-9:
        return None
    return _clamp((float(value) - b) / a, 0.0, 51.0)


def convert_quality(src_codec, src_value, dst_codec):
    """
    任意两个编码器之间的等效质量换算（以 libx264 CRF 为中间轴）。

    Args:
        src_codec: 源编码器名（FFmpeg 名称，如 'h264_nvenc'）
        src_value: 源编码器下的质量值
        dst_codec: 目标编码器名（如 'libx265'）

    Returns:
        目标编码器下的等效质量值（float）；任一端无映射时返回 None。

    注意：裁剪脚本只把它用于"GPU 编码器降级为 CPU 编码器"这一条路径；
    用户显式给出的同族参数（CPU 的 --crf、GPU 的 --cq）一律原样下发，不换算。
    """
    ref = to_x264_crf(src_codec, src_value)
    if ref is None:
        return None
    return from_x264_crf(dst_codec, ref)


def convert_crf(x264_crf):
    """
    根据输入的 libx264 CRF 值，返回其他编码器的等效 CRF/CQ/Q 值列表。

    参数:
        x264_crf (float/int): libx264 的 CRF 值，建议范围 0-51。

    返回:
        list of tuple: [(编码器名称, 等效值, 取值范围), ...]
        注意：VideoToolbox 的 q 值越高画质越好，与 CRF 含义相反。
    """
    if not (0 <= x264_crf <= 51):
        raise ValueError("x264 CRF 必须在 0 到 51 之间")

    result = []
    for codec, (a, b, lo, hi) in QUALITY_MAP.items():
        value = a * x264_crf + b
        clamped = _clamp(value, lo, hi)
        result.append((_DISPLAY_NAMES.get(codec, codec),
                       int(round(clamped)), (lo, hi)))

    return result


if __name__ == '__main__':
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description='把 libx264 的 CRF 值换算成其他编码器的等效 CRF/CQ/Q 值。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python convert_crf.py 21                输出全部编码器的等效值
  python convert_crf.py -e libx265 -e libsvtav1 21
  python convert_crf.py -e "svt, libx265, h.265" 18   多个编码器用逗号分隔（需引号）
  python convert_crf.py --json 18         以 JSON 输出，便于脚本解析

注意：
  VideoToolbox 的 q 值越高画质越好，与 CRF 含义相反。
""",
    )
    parser.add_argument('crf', type=float, nargs='?', default=21.0,
                        help='libx264 的 CRF 值，0-51，默认 21')
    parser.add_argument('-e', '--encoder', action='append', metavar='NAME',
                        dest='encoders',
                        help='只输出指定编码器；逗号分隔或重复指定，'
                             '支持子串匹配（不区分大小写）')
    parser.add_argument('--json', action='store_true',
                        help='以 JSON 格式输出')

    args = parser.parse_args()

    if not 0 <= args.crf <= 51:
        parser.error("CRF 必须在 0 到 51 之间")

    rows = convert_crf(args.crf)

    if args.encoders:
        keys = [k.strip().lower()
                for part in args.encoders
                for k in part.split(',') if k.strip()]
        rows = [r for r in rows
                if any(k in r[0].lower() for k in keys)]
        if not rows:
            available = ', '.join(enc for enc, _, _ in convert_crf(args.crf))
            parser.error(f"没有匹配 {keys} 的编码器，可用：{available}")

    if args.json:
        print(json.dumps(
            [{'encoder': enc, 'value': val, 'range': list(rng)}
             for enc, val, rng in rows],
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"libx264 CRF {args.crf:g} 对应的等效 CRF/CQ/Q 值：\n")
        print(f"{'编码器':<22} {'等效值':>6}   取值范围")
        print("-" * 48)
        for enc, val, rng in rows:
            print(f"{enc:<22} {val:>6}   {rng}")