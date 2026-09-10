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

    # 各编码器映射 (编码器名: (计算值, 最小值, 最大值))
    mappings = {
        # ---------- 软件编码器 ----------
        'libx264':              (x264_crf,                      0, 51),
        'libx265':              (x264_crf + 3.0,                0, 51),
        'libvpx-vp9':           (1.98 * x264_crf - 14.46,       0, 63),

        # ---------- AV1 软件编码器 ----------
        # libaom: 官方文档 AV1 CRF 23 ≈ x264 CRF 19，即偏移 +4
        'libaom-av1':           (x264_crf + 4.0,                0, 63),
        # SVT-AV1: 速度极快，CRF 刻度略偏，达到同感知质量需高 2 左右
        'libsvtav1':            (x264_crf + 6.0,                0, 63),
        # rav1e: 使用 0-255 量化器刻度，q 值 ≈ 4 × libaom_crf
        'librav1e':             (4.0 * (x264_crf + 4.0),        0, 255),

        # ---------- AV1 硬件编码器 ----------
        'av1_nvenc':            (x264_crf + 6.0,                0, 51),
        'av1_qsv':              (x264_crf + 5.0,                1, 51),
        'av1_amf':              (x264_crf + 3.0,                0, 51),

        # ---------- 其他硬件编码器 ----------
        'NVENC H.264':          (x264_crf + 5.0,                0, 51),
        'NVENC H.265':          (x264_crf + 7.5,                0, 51),
        'QSV H.264':            (x264_crf + 3.5,                1, 51),
        'QSV H.265':            (x264_crf + 4.5,                1, 51),
        'AMF H.264':            (x264_crf + 2.0,                0, 51),
        'AMF H.265':            (x264_crf + 4.0,                0, 51),
        'VAAPI H.264':          (x264_crf + 3.0,                0, 51),
        'VAAPI H.265':          (x264_crf + 5.0,                0, 51),

        # ---------- VideoToolbox (q 值越高画质越好) ----------
        'VideoToolbox H.264':   (100.0 - (x264_crf / 51.0) * 99.0,        1, 100),
        'VideoToolbox H.265':   (100.0 - (x264_crf / 51.0) * 99.0 + 5.0,  1, 100),
    }

    result = []
    for encoder, (value, min_val, max_val) in mappings.items():
        clamped = max(min_val, min(max_val, value))
        result.append((encoder, int(round(clamped)), (min_val, max_val)))

    return result


if __name__ == '__main__':
    ref_crf = 21
    print(f"libx264 CRF {ref_crf} 对应的等效 CRF/CQ/Q 值：\n")
    print(f"{'编码器':<22} {'等效值':>6}   取值范围")
    print("-" * 48)
    for enc, val, rng in convert_crf(ref_crf):
        print(f"{enc:<22} {val:>6}   {rng}")