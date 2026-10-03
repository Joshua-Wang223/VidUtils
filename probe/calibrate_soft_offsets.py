#!/usr/bin/env python3
# probe/calibrate_soft_offsets.py — 真实素材「等体积」标定软编偏移（V9）
#
# 目的：重新标定 QUALITY_MAP 里 libvpx-vp9 / libsvtav1 / libaom-av1 / libx265
# 相对 libx264 的线性等效关系  value = a × x264_crf + b。
#
# 方法（与 Plan/VidUtils_质量控制参数修复方案.md 的 V9 同套）：
#   1. 锚点 libx264 在若干 CRF 上编码，记输出体积；
#   2. 目标编码器扫一串 CRF，记体积；
#   3. 对每个锚点体积，在目标编码器「CRF → log(体积)」曲线上插值，得到**等体积**的 CRF；
#   4. 对 (锚点 CRF, 等体积 CRF) 做最小二乘拟合 → a, b。
#
# ⚠ 等体积 ≠ 等质量：本脚本只复现方案 V9 的标定口径。落表前仍应对照 PSNR / 人眼。
#
# 用法：
#   python3 probe/calibrate_soft_offsets.py [--src PATH] [--duration 4] [--width 1920]
#   python3 probe/calibrate_soft_offsets.py --selftest     # 只跑几秒钟的装置自检
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT.parent / 'input_videos' / 'new5_raw.mp4'
OUT = ROOT / 'temp' / 'calib'

ANCHOR_CRFS = [18, 21, 24, 27, 30]
TARGET_SWEEPS = {
    'libx265':     [15, 18, 21, 24, 27, 30, 33, 36],
    'libsvtav1':   [15, 20, 25, 30, 35, 40, 45, 50],
    'libvpx-vp9':  [15, 20, 25, 30, 35, 40, 45, 50],
    'libaom-av1':  [15, 20, 25, 30, 35, 40, 45, 50],
}


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if p.returncode != 0:
        raise RuntimeError(' '.join(cmd) + '\n' + (p.stderr or '')[-2000:])
    return p


def encode(src, codec, crf, out, preset_args, quiet=True):
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner']
    if quiet:
        cmd += ['-loglevel', 'error']
    cmd += ['-i', str(src), '-an']
    if codec == 'libx264':
        cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf)]
    elif codec == 'libx265':
        cmd += ['-c:v', 'libx265', '-preset', 'medium', '-crf', str(crf)]
    elif codec == 'libsvtav1':
        cmd += ['-c:v', 'libsvtav1', '-preset', '8', '-crf', str(crf)]
    elif codec == 'libsvtav1':
        cmd += ['-c:v', 'libsvtav1', '-preset', '8', '-crf', str(crf)]
    elif codec == 'libvpx-vp9':
        cmd += ['-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', str(crf),
                '-deadline', 'good', '-cpu-used', '2']
    elif codec == 'libaom-av1':
        cmd += ['-c:v', 'libaom-av1', '-b:v', '0', '-crf', str(crf),
                '-cpu-used', '6']
    else:
        raise ValueError(codec)
    cmd += preset_args + [str(out)]
    run(cmd)
    return out.stat().st_size


def interp_crf_for_size(sweep, target_size):
    """在 (crf, size) 单调下降曲线上插值出等于 target_size 的 crf。"""
    pts = sorted(sweep)                      # 按 crf 升序，size 递减
    for (c0, s0), (c1, s1) in zip(pts, pts[1:]):
        lo, hi = min(s0, s1), max(s0, s1)
        if lo <= target_size <= hi:
            # 在 log(size) 线性插值更稳
            l0, l1 = math.log(s0), math.log(s1)
            lt = math.log(target_size)
            if abs(l1 - l0) < 1e-9:
                return (c0 + c1) / 2.0
            t = (lt - l0) / (l1 - l0)
            return c0 + t * (c1 - c0)
    return None                              # 落在扫描范围外


def fit_line(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None, None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return a, my - a * mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=str(DEFAULT_SRC))
    ap.add_argument('--duration', type=float, default=4.0)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    src = Path(args.src)
    if not src.is_file():
        print(f'源素材不存在：{src}', file=sys.stderr)
        return 2

    # 统一预处理成无音频、目标分辨率、固定时长的中间素材（yuv420p，供各编码器吃）
    #
    # ⚠⚠ **缓存陷阱（2026-09-29 定位）**：本行按「文件存在即复用」缓存 prep.mp4，
    #   **不校验 --src / --duration / --width / --height 是否变化**。于是换一个
    #   --src 或换分辨率重跑时，会静默沿用**上一条素材**的 prep，得到"不同素材
    #   跑出几乎相同数值"的假象（实测曾出现 libaom 在两条不同素材上 crf 30~50
    #   体积逐位相同）。换素材/换分辨率前**必须** `rm -f temp/calib/prep.mp4`。
    #   需要自动隔离时改用同目录的 `calibrate_soft_offsets_nocache.py`
    #   （每次运行独立工作目录 + 打印 prep 的 md5，可审计）。
    prep = OUT / 'prep.mp4'
    if not prep.exists() or args.selftest:
        run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
             '-i', str(src), '-t', str(args.duration), '-an',
             '-vf', f'scale={args.width}:{args.height}:flags=lanczos',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '10',
             '-pix_fmt', 'yuv420p', str(prep)])
    else:
        print(f'⚠ 复用已存在的 {prep.name}（{prep.stat().st_size/1024:.1f} KiB）——'
              f'若 --src/--duration/--width/--height 与上次不同，结果会失真！'
              f'请先 rm -f {prep}，或改用 calibrate_soft_offsets_nocache.py。',
              file=sys.stderr)

    codecs = ['libx264'] + list(TARGET_SWEEPS)
    if args.selftest:
        codecs = ['libx264', 'libx265', 'libsvtav1']

    sizes = {}
    for codec in codecs:
        crfs = ANCHOR_CRFS if codec == 'libx264' else TARGET_SWEEPS[codec]
        if args.selftest:
            crfs = [21, 30]
        sizes[codec] = []
        for crf in crfs:
            out = OUT / f'{codec}_{crf}.{"webm" if codec == "libvpx-vp9" else "mp4"}'
            sz = encode(prep, codec, crf, out, [])
            sizes[codec].append((crf, sz))
            print(f'  {codec} crf {crf:>3} → {sz/1024:.1f} KiB', flush=True)

    anchors = sizes['libx264']
    report = {}
    print('\n── 等体积标定结果 ──')
    for codec in codecs:
        if codec == 'libx264':
            continue
        xs, ys = [], []
        for acrf, asize in anchors:
            c = interp_crf_for_size(sizes[codec], asize)
            if c is not None:
                xs.append(acrf)
                ys.append(c)
        if len(xs) < 2:
            print(f'  {codec}: 采样点不足，跳过')
            continue
        a, b = fit_line(xs, ys)
        report[codec] = {'a': a, 'b': b, 'points': list(zip(xs, ys))}
        print(f'  {codec}: a={a:.3f}  b={b:.2f}   '
              f'（拟合点 ' + ', '.join(f'{x}→{y:.1f}' for x, y in zip(xs, ys)) + '）')

    (OUT / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n结果写入 {OUT / "report.json"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
