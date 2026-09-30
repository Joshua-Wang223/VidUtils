#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等质量（equal perceptual quality）标定 —— 以 libx264 CRF 为基准轴，按 **VMAF** 插值。

与同目录 calibrate_soft_offsets_nocache.py（**等体积**口径）的区别：
  * 插值基准从「体积」改为 **VMAF**：在目标编码器「参数 → VMAF」曲线上取等 VMAF 参数；
  * 采集 **VMAF / PSNR-HVS**（libvmaf 单遍）与 **PSNR / SSIM / XPSNR**（独立滤镜，另一遍）；
  * 支持多素材（多次 --src），跨素材聚合（a 池化最小二乘 + b 取中位数）。

口径（对齐 VE v2 §4.1「唯一来源」，不可混用）：
  * VMAF      ← libvmaf pooled_metrics.vmaf.mean
  * PSNR-HVS  ← libvmaf feature=name=psnr_hvs（唯一来源）
  * PSNR      ← 独立 psnr 滤镜 average:（与既有 G7/AC7 同轴）
  * SSIM      ← 独立 ssim 滤镜 All:
  * XPSNR     ← 独立 xpsnr 滤镜（libvmaf 无此 feature）

无缓存：每次运行独立工作目录 + prep md5 审计（见 calibrate_soft_offsets.py 的缓存陷阱）。

用法：
  python3 probe/calibrate_equal_quality.py --src A.mp4 --src B.mp4 --duration 10
  python3 probe/calibrate_equal_quality.py --src A.mp4 --quick          # 快速自检
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRCS = [
    ROOT.parent / 'input_videos' / 'new5_raw.mp4',
    ROOT.parent / 'input_videos' / 'new4_raw.mp4',
    ROOT.parent / 'input_videos' / 'word_world_2.mp4',
]

ANCHOR_CRFS = [18, 21, 24, 27, 30]

# 目标编码器扫描点。低端够低使目标 VMAF 能超过 x264 crf18；避开过慢的极低 CRF。
# 扫描须覆盖 x264 锚点的 VMAF 区间（实测 x264 crf18..30 约 98.5→86.8）：
# 低端要够低（目标 VMAF 超过 x264 crf18）、高端要够高（目标 VMAF 降到 x264 crf30 以下）。
SWEEP = {
    'libx265':   [14, 19, 25, 31, 38, 46],
    'libvpx-vp9': [13, 20, 28, 36, 45, 55],
    'libaom-av1': [13, 20, 28, 36, 45, 55],
    'libsvtav1': [13, 20, 28, 36, 45, 55],
    'librav1e':  [30, 52, 76, 102, 130, 160],
}

# 各编码器**必须锁定**的配套参数（标定与下发必须一致，否则等效点漂移）。
LOCK = {
    'libx264':    ['-preset', 'medium'],
    'libx265':    ['-preset', 'medium'],
    'libvpx-vp9': ['-b:v', '0', '-deadline', 'good', '-cpu-used', '2'],
    'libaom-av1': ['-b:v', '0', '-cpu-used', '6'],
    'libsvtav1':  ['-preset', '8'],
    'librav1e':   ['-speed', '10'],   # 与 VidUtils 下发一致（_RAV1E_SPEED=10）
}
QUALITY_FLAG = {           # 质量参数的 CLI 名
    'libx264': '-crf', 'libx265': '-crf', 'libvpx-vp9': '-crf',
    'libaom-av1': '-crf', 'libsvtav1': '-crf', 'librav1e': '-qp',
}


def run(cmd, timeout=7200):
    """跑子进程；stdin 固定 /dev/null（否则后台进程组 + tty 会被 SIGTTOU 整组停住）。"""
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace',
                       stdin=subprocess.DEVNULL, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(' '.join(map(str, cmd)) + '\n' + (p.stderr or '')[-3000:])
    return p


def md5(path, nbytes=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        h.update(f.read(nbytes))
    return h.hexdigest()


def ffprobe_video(path):
    """返回 (nb_frames, duration, fps, width, height, pix_fmt, color_transfer)。"""
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
               'stream=nb_frames,r_frame_rate,width,height,pix_fmt,color_transfer',
               '-show_entries', 'format=duration', '-of', 'json', str(path)]).stdout
    d = json.loads(out)
    st = d['streams'][0]
    nb = int(st.get('nb_frames') or 0)
    dur = float(d.get('format', {}).get('duration') or 0.0)
    num, _, den = (st.get('r_frame_rate') or '0/1').partition('/')
    fps = (float(num) / float(den)) if float(den or 0) else 0.0
    # 帧数取 nb_frames 与 duration×fps 的较大值（-c copy 分段常见不一致）
    frames = max(nb, int(round(dur * fps)))
    return (frames, dur, fps, int(st['width']), int(st['height']),
            st.get('pix_fmt'), (st.get('color_transfer') or '').lower())


def make_prep(src, work, duration, width, height):
    """生成 720p yuv420p 中间素材（无缓存：先删后建）。HDR 源先 tonemap。"""
    prep = work / 'prep.mp4'
    prep.unlink(missing_ok=True)
    _, _, _, _, _, pix_fmt, transfer = ffprobe_video(src)
    vf = f'scale={width}:{height}:flags=lanczos'
    hdr = transfer in ('smpte2084', 'arib-std-b67')
    if hdr:
        vf += (',zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,'
               'tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p')
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', str(src), '-t', str(duration), '-an', '-vf', vf,
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '10',
           '-pix_fmt', 'yuv420p', str(prep)]
    try:
        run(cmd)
    except RuntimeError:
        if not hdr:
            raise
        # tonemap 失败则退化为直缩（并告警）
        print(f'  ⚠ tonemap 失败，退化为直缩（HDR→SDR 未做色调映射）：{src}',
              file=sys.stderr)
        run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
             '-i', str(src), '-t', str(duration), '-an',
             '-vf', f'scale={width}:{height}:flags=lanczos',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '10',
             '-pix_fmt', 'yuv420p', str(prep)])
    return prep, hdr


def encode(src, codec, value, out):
    out.unlink(missing_ok=True)
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', str(src), '-an', '-c:v', codec,
           QUALITY_FLAG[codec], str(value)]
    cmd += LOCK[codec]
    cmd += ['-pix_fmt', 'yuv420p', str(out)]
    t0 = time.time()
    run(cmd)
    return out.stat().st_size, time.time() - t0


def video_kbps(path):
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=bit_rate', '-of', 'csv=p=0',
               str(path)]).stdout.strip()
    try:
        return float(out) / 1000.0
    except ValueError:
        return None


# ── 指标采集（两遍）─────────────────────────────────────────────────────
def _vmaf_pass(dist, ref, nframes, log, subsample=1):
    """libvmaf 单遍：VMAF + PSNR-HVS（唯一来源）。

    ⚠ n_subsample>1 **会偏置 VMAF**（实测同文件 vp9 crf35：subsample1=96.62 vs
      subsample8=98.56，差 1.9~3.0；x264 锚点几乎不偏）⇒ 标定与判据**必须**用
      subsample=1，否则等 VMAF 匹配被污染。默认 1。"""
    log.unlink(missing_ok=True)
    filt = ('libvmaf=feature=name=psnr_hvs:'
            'model=version=vmaf_v0.6.1:log_fmt=json:log_path=' + str(log))
    if subsample and subsample > 1:
        filt += f':n_subsample={subsample}'
    run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
         '-i', str(dist), '-i', str(ref), '-frames:v', str(nframes),
         '-lavfi', filt, '-f', 'null', '-'])
    pm = json.loads(log.read_text(encoding='utf-8'))['pooled_metrics']
    def mean(k):
        return pm[k]['mean'] if k in pm else None
    return {'vmaf': mean('vmaf'), 'psnr_hvs': mean('psnr_hvs'),
            'vif_scale0': mean('integer_vif_scale0'), 'adm2': mean('integer_adm2')}


def _filters_pass(dist, ref, nframes):
    filt = ('[0:v]split=3[a][b][c];[1:v]split=3[d][e][f];'
            '[a][d]psnr;[b][e]ssim;[c][f]xpsnr')
    p = run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-v', 'info',
             '-i', str(dist), '-i', str(ref), '-frames:v', str(nframes),
             '-lavfi', filt, '-f', 'null', '-'])
    txt = p.stderr
    def grab(pat):
        m = re.search(pat, txt)
        if m is None:
            return None                    # 无匹配 ⇒ None（不得回落 0，见 VE K3）
        return float(m.group(1))
    return {'psnr': grab(r'PSNR .*?average:\s*([0-9.]+)'),
            'ssim': grab(r'SSIM .*?All:\s*([0-9.]+)'),
            'xpsnr': grab(r'XPSNR\s+y:\s*([0-9.]+)')}


def measure(dist, ref, nframes, tmp, with_filters=False, subsample=1):
    m = _vmaf_pass(dist, ref, nframes, tmp / 'vmaf.json', subsample=subsample)
    if with_filters:
        m.update(_filters_pass(dist, ref, nframes))
    else:   # 标定只需 VMAF（拟合轴）+ PSNR-HVS；PSNR/SSIM/XPSNR 交由判据脚本另一遍采集
        m.update({'psnr': None, 'ssim': None, 'xpsnr': None})
    return m


# ── 插值与拟合 ──────────────────────────────────────────────────────────
def interp(pts, target):
    """在 (param, value) 上按 value 线性插值出 param；要求 param 升序、value 单调。"""
    pts = sorted(pts)
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        lo, hi = min(v0, v1), max(v0, v1)
        if lo <= target <= hi and abs(v1 - v0) > 1e-9:
            return p0 + (target - v0) / (v1 - v0) * (p1 - p0)
    return None


def vmaf_at_param(sweep, param):
    """在 (param, vmaf) 上按 param 插值出 vmaf。"""
    pts = sorted(sweep)
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        if p0 <= param <= p1:
            if abs(p1 - p0) < 1e-9:
                return v0
            return v0 + (param - p0) / (p1 - p0) * (v1 - v0)
    return None


def monotonic(sweep):
    """检查 (param↑, vmaf↓) 是否单调不增（允许极小平坦）。返回最大违例幅度。"""
    vs = [v for _, v in sorted(sweep)]
    return max((max(0.0, vs[i + 1] - vs[i]) for i in range(len(vs) - 1)), default=0.0)


def fit_line(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None, None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return a, my - a * mx


def calibrate_one(work, ref, nframes, codecs, duration, src_name='src', cache=None):
    """对单条素材：锚点 + 目标扫描 + 等 VMAF 插值 → {codec: {...}}。

    cache: {key: metrics} 断点续跑（key = "素材|编码器|参数"）；命中则跳过编码/度量。
    """
    cache = cache if cache is not None else {}
    cache_file = work / 'points_cache.json'

    def save():
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')

    sizes, metrics = {}, {}
    allc = ['libx264'] + codecs
    for codec in allc:
        vals = ANCHOR_CRFS if codec == 'libx264' else SWEEP[codec]
        sizes[codec], metrics[codec] = [], []
        for v in vals:
            key = f'{src_name}|{duration:g}s|{codec}|{v}'
            if key in cache:
                m = cache[key]
                sz = m.get('_bytes', 0)
                dt = 0.0
                tag = '(cached)'
            else:
                out = work / f'{codec}_{v}.mp4'
                sz, dt = encode(ref, codec, v, out)
                m = measure(out, ref, nframes, work)
                m['kbps'] = video_kbps(out)
                out.unlink(missing_ok=True)
                m['_bytes'] = sz
                cache[key] = m
                save()
                tag = ''
            sizes[codec].append((v, sz))
            metrics[codec].append((v, m['vmaf'], m))
            print(f'    {codec:11} {"crf" if codec != "librav1e" else "qp"} {v:>3} '
                  f'→ {sz/1024:8.1f} KiB  vmaf={m["vmaf"]:.2f} '
                  f'hvs={m["psnr_hvs"]:.2f}  ({dt:5.1f}s) {tag}', flush=True)

    anchors = [(c, m['vmaf']) for c, _, m in metrics['libx264']]
    res = {}
    for codec in codecs:
        sweep = [(p, v) for p, v, _ in metrics[codec]]
        xs, ys, mono = [], [], monotonic(sweep)
        for acrf, avmaf in anchors:
            p = interp(sweep, avmaf)
            if p is not None:
                xs.append(acrf)
                ys.append(p)
        if len(xs) < 2:
            res[codec] = {'error': 'insufficient_points', 'points': list(zip(xs, ys))}
            continue
        a, b = fit_line(xs, ys)
        resid = max(abs(y - (a * x + b)) for x, y in zip(xs, ys))
        # 用直线预测的参数回查 VMAF，得到真实 ΔVMAF 估计
        dv = []
        for acrf, avmaf in anchors:
            vp = vmaf_at_param(sweep, a * acrf + b)
            if vp is not None:
                dv.append(abs(vp - avmaf))
        res[codec] = {'a': a, 'b': b, 'points': list(zip(xs, ys)),
                      'max_resid_param': resid,
                      'max_delta_vmaf': (max(dv) if dv else None),
                      'monotonic_violation': mono, 'lo': 0, 'hi': None}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', action='append', default=None,
                    help='可重复；不给则用默认 3 条')
    ap.add_argument('--duration', type=float, default=10.0)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--codecs', default='libx265,libvpx-vp9,libaom-av1,libsvtav1,librav1e')
    ap.add_argument('--workroot', default='/tmp/eqq_calib')
    ap.add_argument('--tag', default='')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--quick', action='store_true', help='快速自检（1 素材 / 少点 / 3s）')
    ap.add_argument('--no-resume', action='store_true',
                    help='忽略断点缓存（默认复用 work 目录下的 points_cache.json）')
    args = ap.parse_args()

    if args.quick:
        args.duration = 3.0
        codecs = ['libx265', 'libsvtav1']
        for k in SWEEP:
            SWEEP[k] = [21, 30]
    else:
        codecs = [c.strip() for c in args.codecs.split(',') if c.strip()]

    srcs = [Path(s) for s in (args.src or [str(p) for p in DEFAULT_SRCS])]
    srcs = [s for s in srcs if s.is_file()]
    if not srcs:
        print('无可用源素材', file=sys.stderr)
        return 2

    tag = args.tag or (f'{args.width}x{args.height}_{args.duration:g}s'
                       f'_n{len(srcs)}' + ('_quick' if args.quick else ''))
    work = Path(args.workroot) / re.sub(r'[^A-Za-z0-9_.-]', '_', tag)
    work.mkdir(parents=True, exist_ok=True)

    ffver = run(['ffmpeg', '-hide_banner', '-version']).stdout.splitlines()[0]
    report = {'ffmpeg': ffver, 'width': args.width, 'height': args.height,
              'duration': args.duration, 'codecs': codecs, 'lock': LOCK,
              'anchors': ANCHOR_CRFS, 'per_material': {}}

    per_codec_points = {c: [] for c in codecs}
    for src in srcs:
        print(f'\n══ 素材 {src.name} ══')
        prep, hdr = make_prep(src, work, args.duration, args.width, args.height)
        nframes, _, fps, _, _, _, transfer = ffprobe_video(prep)
        print(f'  prep: {prep.name}  {prep.stat().st_size/1024:.1f} KiB  '
              f'md5={md5(prep)}  frames={nframes}  fps={fps:.2f}  hdr={hdr}')
        _cf = work / 'points_cache.json'
        cache = {} if args.no_resume or not _cf.exists() else json.loads(
            _cf.read_text(encoding='utf-8'))
        res = calibrate_one(work, prep, nframes, codecs, args.duration,
                            src_name=src.name, cache=cache)
        report['per_material'][src.name] = {
            'src_md5': md5(src), 'prep_md5': md5(prep), 'frames': nframes,
            'fps': round(fps, 3), 'hdr': hdr, 'eqq': res}
        for c, r in res.items():
            if 'a' in r:
                per_codec_points[c] += [(x, y) for x, y in r['points']]
        # 增量落盘：中断也不丢已完成素材
        (work / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if not args.keep:
            for f in work.glob('*.mp4'):
                f.unlink(missing_ok=True)

    # ── 跨素材聚合 ──────────────────────────────────────────────────────
    table = {}
    print('\n── 跨素材聚合（a=池化最小二乘，b=各素材中位数）──')
    for c in codecs:
        pts = per_codec_points[c]
        if len(pts) < 2:
            print(f'  {c}: 点不足，跳过')
            continue
        a, _ = fit_line([x for x, _ in pts], [y for _, y in pts])
        bs = []
        for name, mat in report['per_material'].items():
            r = mat['eqq'].get(c, {})
            if 'a' in r:
                bs.append(statistics.median([y - a * x for x, y in r['points']]))
        b = statistics.median(bs) if bs else 0.0
        table[c] = [round(a, 4), round(b, 4), 0, (255 if c == 'librav1e' else 63)]
        print(f'  {c:11} a={a:.4f}  b={b:+.3f}   '
              f'b_m范围=[{min(bs):+.2f}, {max(bs):+.2f}]  '
              f'crf21→{a*21+b:.2f}')
    report['table'] = {k: v for k, v in table.items()}

    (work / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告: {work / "report.json"}')
    print('候选 QUALITY_MAP（等质量，软编）：')
    for k, v in table.items():
        print(f"    '{k}': ({v[0]}, {v[1]}, 0, {v[3]}),")
    return 0


if __name__ == '__main__':
    sys.exit(main())
