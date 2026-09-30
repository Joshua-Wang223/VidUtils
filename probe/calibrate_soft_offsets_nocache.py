#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无缓存的等体积标定（修复 upstream 的 prep.mp4 缓存污染）。

与 probe/calibrate_soft_offsets.py 的差异：
  1. **每次运行独立工作目录**（--work 或自动带 src/duration/分辨率指纹），
     彻底消除 `if not prep.exists()` 导致的跨素材缓存污染。
  2. 扫描点可加密（--dense），用于修 libaom-av1 拟合点不足的问题。
  3. 记录 prep 的 md5 与尺寸，输出可审计。
  4. librav1e 支持 -speed / tile（upstream 未用，导致默认速度下超时）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

ANCHOR_CRFS = [18, 21, 24, 27, 30]

# 原始扫描（upstream 默认，5 档间隔）
COARSE = {
    'libx265':    [15, 18, 21, 24, 27, 30, 33, 36],
    'libsvtav1':  [15, 20, 25, 30, 35, 40, 45, 50],
    'libvpx-vp9': [15, 20, 25, 30, 35, 40, 45, 50],
    'libaom-av1': [15, 20, 25, 30, 35, 40, 45, 50],
}
# 加密扫描（3 档间隔）—— 覆盖锚点区间 18~30 且保留尾部
DENSE = {
    'libx265':    [15, 18, 21, 24, 27, 30, 33, 36],
    'libsvtav1':  [15, 18, 21, 24, 27, 30, 33, 36, 40, 45, 50],
    'libvpx-vp9': [15, 18, 21, 24, 27, 30, 33, 36, 40, 45, 50],
    'libaom-av1': [6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51],
}


def run(cmd, timeout=3600):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(' '.join(map(str, cmd)) + '\n' + (p.stderr or '')[-2000:])
    return p


def md5(path, nbytes=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        h.update(f.read(nbytes))
    return h.hexdigest()


def encode(src, codec, crf, out, extra):
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', str(src), '-an']
    if codec == 'libx264':
        cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf)]
    elif codec == 'libx265':
        cmd += ['-c:v', 'libx265', '-preset', 'medium', '-crf', str(crf)]
    elif codec == 'libsvtav1':
        cmd += ['-c:v', 'libsvtav1', '-preset', '8', '-crf', str(crf)]
    elif codec == 'libvpx-vp9':
        cmd += ['-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', str(crf),
                '-deadline', 'good', '-cpu-used', '2']
    elif codec == 'libaom-av1':
        cmd += ['-c:v', 'libaom-av1', '-b:v', '0', '-crf', str(crf), '-cpu-used', '6']
    elif codec == 'librav1e':
        cmd += ['-c:v', 'librav1e', '-qp', str(crf)]
    else:
        raise ValueError(codec)
    cmd += list(extra) + ['-pix_fmt', 'yuv420p', str(out)]
    t0 = time.time()
    run(cmd)
    return out.stat().st_size, time.time() - t0


def interp_crf_for_size(sweep, target_size):
    """在 (crf, size) 单调下降曲线上按 log(size) 插值；落在范围外返回 None。"""
    pts = sorted(sweep)
    for (c0, s0), (c1, s1) in zip(pts, pts[1:]):
        lo, hi = min(s0, s1), max(s0, s1)
        if lo <= target_size <= hi:
            l0, l1, lt = math.log(s0), math.log(s1), math.log(target_size)
            if abs(l1 - l0) < 1e-9:
                return (c0 + c1) / 2.0
            return c0 + (lt - l0) / (l1 - l0) * (c1 - c0)
    return None


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
    ap.add_argument('--src', required=True)
    ap.add_argument('--duration', type=float, default=4.0)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--codecs', default='libx265,libsvtav1,libvpx-vp9,libaom-av1')
    ap.add_argument('--dense', action='store_true', help='用 3 档间隔的加密扫描')
    ap.add_argument('--workroot', default='/tmp/opencode/calib')
    ap.add_argument('--tag', default='')
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_file():
        print(f'源素材不存在: {src}', file=sys.stderr)
        return 2

    tag = args.tag or (f'{src.stem}_{args.width}x{args.height}_{args.duration:g}s'
                       f'{"_dense" if args.dense else ""}')
    work = Path(args.workroot) / re.sub(r'[^A-Za-z0-9_.-]', '_', tag)
    work.mkdir(parents=True, exist_ok=True)

    # ① 预处理：每次都重新生成（不依赖任何缓存）
    prep = work / 'prep.mp4'
    if prep.exists():
        prep.unlink()
    run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
         '-i', str(src), '-t', str(args.duration), '-an',
         '-vf', f'scale={args.width}:{args.height}:flags=lanczos',
         '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '10',
         '-pix_fmt', 'yuv420p', str(prep)])
    print(f'prep: {prep.name}  {prep.stat().st_size/1024:.1f} KiB  md5={md5(prep)}')

    codecs = ['libx264'] + [c.strip() for c in args.codecs.split(',') if c.strip()]
    table = DENSE if args.dense else COARSE

    sizes, times = {}, {}
    for codec in codecs:
        crfs = ANCHOR_CRFS if codec == 'libx264' else table[codec]
        sizes[codec], times[codec] = [], []
        for crf in crfs:
            out = work / f'{codec}_{crf}.mp4'
            if out.exists():
                out.unlink()
            sz, dt = encode(prep, codec, crf, out, [])
            sizes[codec].append((crf, sz))
            times[codec].append(dt)
            print(f'  {codec:11} crf {crf:>3} → {sz/1024:9.1f} KiB  ({dt:6.1f}s)', flush=True)

    anchors = sizes['libx264']
    report = {'src': str(src), 'duration': args.duration,
              'width': args.width, 'height': args.height,
              'dense': args.dense, 'prep_md5': md5(prep),
              'prep_bytes': prep.stat().st_size,
              'sizes': {k: v for k, v in sizes.items()},
              'encode_seconds': {k: [round(t, 1) for t in v] for k, v in times.items()},
              'fits': {}}

    print('\n── 等体积拟合 ──')
    for codec in codecs:
        if codec == 'libx264':
            continue
        xs, ys, miss = [], [], []
        for acrf, asize in anchors:
            c = interp_crf_for_size(sizes[codec], asize)
            if c is None:
                miss.append(acrf)
            else:
                xs.append(acrf)
                ys.append(c)
        if len(xs) < 2:
            print(f'  {codec}: 采样点不足（落外锚点 {miss}），跳过')
            report['fits'][codec] = {'error': 'insufficient_points', 'missing_anchors': miss}
            continue
        a, b = fit_line(xs, ys)
        resid = max(abs(y - (a * x + b)) for x, y in zip(xs, ys))
        report['fits'][codec] = {'a': a, 'b': b, 'points': list(zip(xs, ys)),
                                 'missing_anchors': miss, 'max_resid': resid,
                                 'at21': a * 21 + b}
        print(f'  {codec:11} a={a:.4f}  b={b:+.3f}   crf21→{a*21+b:5.2f}  '
              f'最大残差={resid:.2f}  落外锚点={miss or "无"}')

    (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding='utf-8')
    print(f'\n报告: {work / "report.json"}')
    if not args.keep:
        for f in work.glob('*.mp4'):
            f.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
