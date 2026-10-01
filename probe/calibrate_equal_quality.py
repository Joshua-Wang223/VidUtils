#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等质量（equal perceptual quality）标定 —— 以 libx264 CRF 为基准轴，按 **VMAF** 插值。

与同目录 calibrate_soft_offsets_nocache.py（**等体积**口径）的区别：
  * 插值基准从「体积」改为 **VMAF**：在目标编码器「参数 → VMAF」曲线上取等 VMAF 参数；
  * 采集 **VMAF / PSNR-HVS**（libvmaf 单遍）与 **PSNR / SSIM / XPSNR**（独立滤镜，另一遍）；
  * 支持多素材（多次 --src），跨素材聚合（a 池化最小二乘 + b 取中位数）；
  * librav1e 按 `-speed` 档**分别标定**（native 与 speed 10 各出一行）。

口径（对齐 VE 立项 v2 §4.1「唯一来源」，**不可混用**）：
  * VMAF      ← libvmaf `pooled_metrics.vmaf.mean`
  * PSNR-HVS  ← libvmaf `feature=name=psnr_hvs`（**唯一来源**，无独立滤镜）
  * PSNR      ← 独立 `psnr` 滤镜 `average:`
  * SSIM      ← 独立 `ssim` 滤镜 `All:`
  * XPSNR     ← 独立 `xpsnr` 滤镜（libvmaf 2.3.1 无此 feature）

无缓存：每次运行独立工作目录 + prep 重建 + md5 审计（见 calibrate_soft_offsets.py 的缓存陷阱）。
断点续跑：逐点落 `points.json`；`--resume` 跳过已完成点（长跑必备）。

用法：
  python3 <this> --selftest                                   # 纯逻辑自测（秒级）
  python3 <this> --quick                                      # 快速干跑（3s / 2 编码器 / 2 点）
  python3 <this> --resume                                     # 全量（默认 3 素材 × 10s）
  python3 <this> --src A.mp4 --src B.mp4 --duration 10 --resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path


# ── 项目根定位（marker walk）────────────────────────────────────────────────
# 同一份文件放在 VE 的 Accessory/probe/ 或 VidUtils 的 probe/ 下都能正确定位：
# 向上找到含 src/utils/convert_crf.py（VE）或 convert_crf.py（VU）的目录。
def _find_project():
    for p in Path(__file__).resolve().parents:
        if (p / 'src' / 'utils' / 'convert_crf.py').is_file():
            return p, p / 'src' / 'utils'
        if (p / 'convert_crf.py').is_file():
            return p, p
    raise SystemExit('找不到项目根（未找到 convert_crf.py）')


ROOT, UTILS = _find_project()
if str(UTILS) not in sys.path:
    sys.path.insert(0, str(UTILS))
import convert_crf as CRF                      # noqa: E402

INPUT_VIDEOS = ROOT.parent / 'input_videos'
DEFAULT_SRCS = [
    INPUT_VIDEOS / 'new5_raw.mp4',             # 1080p 实拍
    INPUT_VIDEOS / 'new4_raw.mp4',             # 1080p 高熵
    INPUT_VIDEOS / 'word_world_2.mp4',         # 720x576 门禁素材
]

ANCHOR_CRFS = [18, 22, 26, 30, 34]

# 目标编码器扫描点。低端必须够低，使目标 VMAF 能高于 x264 crf18（否则高端锚点插值落空）。
SWEEP = {
    'libx265':    [10, 14, 18, 22, 26, 30, 34, 38, 44, 51],
    'libvpx-vp9': [10, 16, 22, 28, 34, 40, 46, 52, 58, 63],
    'libaom-av1': [10, 16, 22, 28, 34, 40, 46, 52, 58, 63],
    'libsvtav1':  [10, 16, 22, 28, 34, 40, 46, 52, 58, 63],
    'librav1e':   [10, 30, 50, 70, 90, 110, 130, 155, 180, 210],
}

# 各编码器**必须锁定**的配套参数（标定与下发必须一致，否则等效点漂移）。
# ⚠ librav1e 的 `-speed` 不在此处 —— 它按「档」在运行时追加（见 _lock_for）。
BASE_LOCK = {
    'libx264':    ['-preset', 'medium'],
    'libx265':    ['-preset', 'medium'],
    'libvpx-vp9': ['-b:v', '0', '-deadline', 'good', '-cpu-used', '2'],
    'libaom-av1': ['-b:v', '0', '-cpu-used', '6'],
    'libsvtav1':  ['-preset', '8'],
    'librav1e':   [],
}
QUALITY_FLAG = {
    'libx264': '-crf', 'libx265': '-crf', 'libvpx-vp9': '-crf',
    'libaom-av1': '-crf', 'libsvtav1': '-crf', 'librav1e': '-qp',
}

# 标定「档位」键：`<ffmpeg 编码器名>` 或 `<名>@<rav1e -speed>`。
# librav1e 的 `-speed` 会整体平移码率曲线 ⇒ native 与 speed10 必须各出一行。
def _tiers(rav1e_speeds):
    t = ['libx265', 'libvpx-vp9', 'libaom-av1', 'libsvtav1']
    t += ['librav1e' if s in (None, 'native') else f'librav1e@{s}'
          for s in rav1e_speeds]
    return t


def _ffcodec(key):
    """档位键 → ffmpeg 编码器名。"""
    return key.split('@')[0]


def _lock_for(key):
    """档位键 → 该次编码必须附带的配套参数。"""
    c = _ffcodec(key)
    lock = list(BASE_LOCK[c])
    if c == 'librav1e' and '@' in key:
        lock += ['-speed', key.split('@', 1)[1]]
    return lock


# 残差 / ΔVMAF 门限。参数刻度不同，残差门限按刻度缩放。
TOL_VMAF = 1.5                 # M1 单素材门禁：ΔVMAF < 1.5
MAX_RESID = {'librav1e': 5.0}  # 0~255 刻度
MAX_RESID_DEFAULT = 1.5        # 0~63 刻度


def _max_resid(key):
    return MAX_RESID.get(_ffcodec(key), MAX_RESID_DEFAULT)


def _fmt(x, n=3):
    """None → '—'（**不回落 0**，见立项 K3）。"""
    return '—' if x is None else f'{x:.{n}f}'


# ── 子进程 ──────────────────────────────────────────────────────────────────
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
    # 帧数取 nb_frames 与 duration×fps 的较大值（-c copy 分段常见不一致，见立项 §6.3）
    frames = max(nb, int(round(dur * fps)))
    return (frames, dur, fps, int(st['width']), int(st['height']),
            st.get('pix_fmt'), (st.get('color_transfer') or '').lower())


def make_prep(src, work, duration, width, height):
    """生成 720p yuv420p 中间素材（无缓存：先删后建）。HDR 源先 tonemap。"""
    prep = work / 'prep.mp4'
    prep.unlink(missing_ok=True)
    _, _, _, _, _, _, transfer = ffprobe_video(src)
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
        print(f'  ⚠ tonemap 失败，退化为直缩（HDR→SDR 未做色调映射）：{src}', file=sys.stderr)
        run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
             '-i', str(src), '-t', str(duration), '-an', '-vf',
             f'scale={width}:{height}:flags=lanczos',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '10',
             '-pix_fmt', 'yuv420p', str(prep)])
    return prep, hdr


def encode(ref, key, value, out):
    """用给定档位/质量值编码 ref。返回 (字节数, 秒)。"""
    out.unlink(missing_ok=True)
    c = _ffcodec(key)
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', str(ref), '-an', '-c:v', c, QUALITY_FLAG[c], str(value)]
    cmd += _lock_for(key)
    cmd += ['-pix_fmt', 'yuv420p', str(out)]
    t0 = time.time()
    run(cmd)
    return out.stat().st_size, time.time() - t0


def video_kbps(path):
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=bit_rate', '-of', 'csv=p=0', str(path)]).stdout.strip()
    try:
        return float(out) / 1000.0
    except ValueError:
        return None


# ── 指标采集（两遍，口径按「唯一来源」）────────────────────────────────────
def _vmaf_pass(dist, ref, nframes, log, subsample=1):
    """libvmaf 单遍：VMAF + PSNR-HVS（**唯一来源**）。

    ⚠ **`n_subsample` 必须为 1**（除 1 以外不写该选项）。实测 `subsample>1` 会
    **偏置 VMAF**（同文件 vp9 crf35 差 1.9~3.0），且偏置量**随编码器而异** ⇒
    会污染等 VMAF 匹配，使等质量表系统性失真（2026-10-01 二次标定实测确认：
    3 素材 204 点用 subsample=8 跑出的表，留一交叉验证 6 个编码器里 5 个超标）。
    涨本换准确：本机 subsample=1 单点约 44.7s（vs subsample=8 的 7.7s）。
    """
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


# 无匹配 ⇒ None（**绝不回落 0**；立项 K3：把空值当 0 会造成「ΔPSNR 恒 0」假象）
_NUM = r'([0-9.]+|inf|-inf)'


def _filters_pass(dist, ref, nframes):
    """独立滤镜口径：PSNR / SSIM / XPSNR（一次 split 图跑完）。"""
    filt = ('[0:v]split=3[a][b][c];[1:v]split=3[d][e][f];'
            '[a][d]psnr;[b][e]ssim;[c][f]xpsnr')
    p = run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-v', 'info',
             '-i', str(dist), '-i', str(ref), '-frames:v', str(nframes),
             '-lavfi', filt, '-f', 'null', '-'])
    txt = p.stderr

    def grab(pat):
        m = re.search(pat, txt)
        return float(m.group(1)) if m else None

    return {'psnr': grab(r'PSNR\s+.*?average:\s*' + _NUM),
            'ssim': grab(r'SSIM\s+.*?All:\s*' + _NUM),
            'xpsnr': grab(r'XPSNR\s+y:\s*' + _NUM)}


def measure(dist, ref, nframes, tmp, with_filters=False, subsample=1):
    """采集一副 dist/ref 的指标。

    默认**只跑 VMAF + PSNR-HVS**（libvmaf 单遍）——标定的拟合轴只需要 VMAF，
    再花一遍跑 PSNR/SSIM/XPSNR 是纯浪费（它们由 ``verify_equal_quality.py`` 作
    平行门禁单独采集）。``with_filters=True`` 时额外跑那一遍。

    ⚠ ``subsample`` 默认 **1**（禁用子采样）：`>1` 会偏置 VMAF 且偏置随编码器而异，
    详见 :func:`_vmaf_pass`。用 `--subsample N` 显式降本只在**同编码器内做趋势判断**
    时可接受，**不可用于跨编码器的等 VMAF 标定**。
    """
    m = _vmaf_pass(dist, ref, nframes, tmp / 'vmaf.json', subsample=subsample)
    if with_filters:
        m.update(_filters_pass(dist, ref, nframes))
    else:
        m.update({'psnr': None, 'ssim': None, 'xpsnr': None})
    return m


# ── 插值 / 拟合 ─────────────────────────────────────────────────────────────
def pava_nonincreasing(vals):
    """保序回归（PAVA）使序列**单调不增**（参数增大 → VMAF 应下降）。

    VMAF 在低码率端/平涂内容可能出现非单调平台；先做保序再插值，
    否则「首个命中区间」会选到错误（偏高质量）的参数。
    """
    y = [-float(v) for v in vals]           # 取负 → 转成非递减问题
    n = len(y)
    stack = []                              # (start, end_excl, mean)
    for i in range(n):
        cur = [i, i + 1, y[i]]
        while stack and stack[-1][2] > cur[2]:
            s, _, m = stack.pop()
            cnt = cur[1] - s
            total = m * (cur[0] - s) + cur[2] * (cur[1] - cur[0])
            cur = [s, cur[1], total / cnt]
        stack.append(cur)
    res = [0.0] * n
    for s, e, m in stack:
        for i in range(s, e):
            res[i] = m
    return [-v for v in res]                # 取负还原


def monotonic_violation(pts):
    """(param 升序, vmaf) 上，VMAF 上行（应为下行）的最大幅度。"""
    vs = [v for _, v in sorted(pts)]
    return max((max(0.0, vs[i + 1] - vs[i]) for i in range(len(vs) - 1)), default=0.0)


def interp_iso(pts, target):
    """在**已保序非增**的 (param, vmaf) 上求等 VMAF 的参数；取最低参数解。"""
    pts = sorted(pts)
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        lo, hi = min(v0, v1), max(v0, v1)
        if lo <= target <= hi:
            if abs(v1 - v0) < 1e-12:
                return p0
            return p0 + (target - v0) / (v1 - v0) * (p1 - p0)
    return None


def vmaf_at_param(pts, param):
    """在 (param, vmaf) 上按 param 插值出 vmaf。"""
    pts = sorted(pts)
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        if p0 <= param <= p1:
            if abs(p1 - p0) < 1e-12:
                return v0
            return v0 + (param - p0) / (p1 - p0) * (v1 - v0)
    return None


def fit_line(xs, ys):
    n = len(xs)
    if n < 2:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None, None
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return a, my - a * mx


def fit_piecewise(xs, ys, split):
    """两段最小二乘（在 split 处断开）；返回 [(a,b,lo,hi), (a,b,lo,hi)]。"""
    segs = []
    for lo, hi in ((min(xs), split), (split, max(xs))):
        px = [x for x in xs if lo <= x <= hi]
        py = [ys[xs.index(x)] for x in px]
        if len(px) >= 2:
            a, b = fit_line(px, py)
        elif len(px) == 1:
            a, b = 0.0, py[0]
        else:
            continue
        segs.append((a, b, lo, hi))
    return segs


def _predict_linear(x, a, b):
    return a * x + b


def _predict_piecewise(x, segs):
    for a, b, lo, hi in segs:
        if lo <= x <= hi:
            return a * x + b
    return None


def calibrate_tier(key, metrics_ref, metrics_tgt):
    """单素材单档位：等 VMAF 插值 → 拟合 → 残差/ΔVMAF 诊断。

    metrics_ref / metrics_tgt: [(param, vmaf)]（ref 为 libx264 锚点）。
    返回 dict（含 a/b、points、max_resid_param、max_delta_vmaf、model、单调性）。
    """
    anchors = sorted(metrics_ref)
    raw = sorted(metrics_tgt)
    mono = monotonic_violation(raw)

    # 保序后再插值（非单调时记录 method）
    iso_v = pava_nonincreasing([v for _, v in raw])
    iso = [(p, v) for (p, _), v in zip(raw, iso_v)]
    method = 'isotonic' if mono > 1e-6 else 'raw'

    xs, ys = [], []
    for acrf, avmaf in anchors:
        p = interp_iso(iso, avmaf)
        if p is not None:
            xs.append(acrf)
            ys.append(p)
    out = {'points': list(zip(xs, ys)), 'monotonic_violation': mono, 'method': method}
    if len(xs) < 2:
        out['error'] = 'insufficient_points'
        return out

    # 直线
    a, b = fit_line(xs, ys)
    resid = max(abs(y - _predict_linear(x, a, b)) for x, y in zip(xs, ys))
    dv = []
    for acrf, avmaf in anchors:
        vp = vmaf_at_param(iso, _predict_linear(acrf, a, b))
        if vp is not None:
            dv.append(abs(vp - avmaf))
    out.update(a=a, b=b, max_resid_param=resid,
               max_delta_vmaf=(max(dv) if dv else None), model='linear')

    # 直线不达标 → 试分段（报告两套，供执行者决策；表格式 (a,b,lo,hi) 只能承载直线）
    gate_resid = _max_resid(key)
    if resid > gate_resid or (out['max_delta_vmaf'] or 0) > TOL_VMAF:
        split = ANCHOR_CRFS[len(ANCHOR_CRFS) // 2]
        segs = fit_piecewise(xs, ys, split)
        if len(segs) == 2:
            dv2 = []
            for acrf, avmaf in anchors:
                pp = _predict_piecewise(acrf, segs)
                vp = vmaf_at_param(iso, pp) if pp is not None else None
                if vp is not None:
                    dv2.append(abs(vp - avmaf))
            out['piecewise'] = {'segments': segs,
                                'max_delta_vmaf': (max(dv2) if dv2 else None)}
            out['needs_piecewise'] = bool(
                dv2 and (out['max_delta_vmaf'] or 0) > TOL_VMAF
                and max(dv2) < out['max_delta_vmaf'])
    return out


# ── 点级 checkpoint（断点续跑）──────────────────────────────────────────────
def _ptkey(material, key, value):
    return f'{material}|{key}|{value}'


def _load_points(path):
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_points(path, pts):
    path.write_text(json.dumps(pts, ensure_ascii=False, indent=1), encoding='utf-8')


# ── 自测（纯逻辑，不调 ffmpeg）──────────────────────────────────────────────
def selftest():
    ok = True

    def chk(name, got, want, tol=1e-9):
        nonlocal ok
        good = (got is not None and abs(got - want) <= tol) if isinstance(want, float) \
            else (got == want)
        print(f'  {"✓" if good else "✗"} {name}: got={got!r} want={want!r}')
        ok = ok and good

    # 直线拟合
    a, b = fit_line([1, 2, 3], [2, 4, 6])
    chk('fit_line a', a, 2.0, 1e-12)
    chk('fit_line b', b, 0.0, 1e-12)

    # 单调插值（VMAF 随 param 下降）
    pts = [(10, 98.0), (20, 95.0), (30, 90.0)]
    chk('interp_iso 命中中点', interp_iso(pts, 96.5), 15.0, 1e-9)
    chk('interp_iso 端点', interp_iso(pts, 98.0), 10.0, 1e-9)
    chk('interp_iso 区间外', interp_iso(pts, 99.0), None)

    # 保序回归：故意给一个上行违例 → 应被抹平为单调不增
    iso = pava_nonincreasing([98.0, 95.0, 96.0, 90.0])
    chk('pava 单调不增', all(iso[i] >= iso[i + 1] - 1e-12 for i in range(len(iso) - 1)), True)
    chk('pava 端点保持', iso[0], 98.0, 1e-9)

    # 违例度量
    chk('monotonic_violation', monotonic_violation([(1, 90.0), (2, 92.0), (3, 91.0)]), 2.0, 1e-9)
    chk('monotonic_violation 无违例', monotonic_violation([(1, 92.0), (2, 90.0)]), 0.0, 1e-9)

    # 端到端：构造已知等质量关系 param = 1.5*crf - 3 → 应被复原
    ref = [(18, 99.0), (21, 96.0), (24, 92.0), (27, 87.0), (30, 80.0)]
    tgt = [(1.5 * c - 3, v) for c, v in ref]
    r = calibrate_tier('libx265', ref, tgt)
    chk('端到端 a', round(r['a'], 6), 1.5, 1e-6)
    chk('端到端 b', round(r['b'], 6), -3.0, 1e-6)
    chk('端到端 ΔVMAF≈0', r['max_delta_vmaf'], 0.0, 1e-9)

    # 档位辅助
    chk('_ffcodec', _ffcodec('librav1e@10'), 'librav1e')
    chk('_lock_for rav1e native', _lock_for('librav1e'), [])
    chk('_lock_for rav1e@10', _lock_for('librav1e@10'), ['-speed', '10'])
    chk('_lock_for x265', _lock_for('libx265'), ['-preset', 'medium'])
    chk('_max_resid rav1e', _max_resid('librav1e@10'), 5.0, 1e-9)
    chk('_max_resid x265', _max_resid('libx265'), 1.5, 1e-9)

    print('\n自测' + ('通过 ✅' if ok else '失败 ❌'))
    return 0 if ok else 1


# ── 主流程 ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', action='append', default=None, help='可重复；不给则用默认 3 条')
    ap.add_argument('--duration', type=float, default=10.0)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--codecs', default='libx265,libvpx-vp9,libaom-av1,libsvtav1,librav1e')
    ap.add_argument('--rav1e-speed', default='native,10',
                    help='rav1e speed 档，逗号分隔；native 表示不下发 -speed（默认 native,10）')
    ap.add_argument('--workroot', default='/tmp/eqq_calib')
    ap.add_argument('--tag', default='')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--resume', action='store_true', help='跳过 points.json 里已完成的点')
    ap.add_argument('--subsample', type=int, default=1,
                    help='libvmaf n_subsample（默认 1 = 全帧。⚠ 仅 1 才可用于标定：'
                         '>1 会偏置 VMAF 且偏置随编码器而异）')
    ap.add_argument('--with-filters', action='store_true',
                    help='标定时也采集 PSNR/SSIM/XPSNR（默认跳过，由判据脚本另采一遍）')
    ap.add_argument('--quick', action='store_true', help='快速干跑（3s / 2 编码器 / 2 点）')
    ap.add_argument('--selftest', action='store_true', help='纯逻辑自测后退出')
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    # 解析 rav1e 档位
    speeds = []
    for s in str(args.rav1e_speed).split(','):
        s = s.strip()
        if not s:
            continue
        speeds.append(None if s.lower() in ('native', '0', '') else s)

    codecs = [c.strip() for c in args.codecs.split(',') if c.strip()]
    tiers = []
    for c in codecs:
        if c == 'librav1e':
            tiers += ['librav1e' if s is None else f'librav1e@{s}' for s in speeds]
        else:
            tiers.append(c)

    if args.quick:
        args.duration = 3.0
        tiers = [t for t in tiers if t in ('libx265', 'libsvtav1')] or tiers[:2]
        for k in SWEEP:
            SWEEP[k] = [21, 30]

    srcs = [Path(s) for s in (args.src or [str(p) for p in DEFAULT_SRCS])]
    srcs = [s for s in srcs if s.is_file()]
    if not srcs:
        print('无可用源素材', file=sys.stderr)
        return 2

    tag = args.tag or (f'{args.width}x{args.height}_{args.duration:g}s_n{len(srcs)}'
                       + ('_quick' if args.quick else ''))
    work = Path(args.workroot) / re.sub(r'[^A-Za-z0-9_.-]', '_', tag)
    work.mkdir(parents=True, exist_ok=True)
    pts_path = work / 'points.json'
    points = _load_points(pts_path) if args.resume else {}

    ffver = run(['ffmpeg', '-hide_banner', '-version']).stdout.splitlines()[0]
    report = {'ffmpeg': ffver, 'project_root': str(ROOT),
              'width': args.width, 'height': args.height, 'duration': args.duration,
              'tiers': tiers, 'lock': {t: _lock_for(t) for t in tiers},
              'anchors': ANCHOR_CRFS, 'tol_vmaf': TOL_VMAF,
              'subsample': args.subsample, 'with_filters': bool(args.with_filters),
              'max_resid': {t: _max_resid(t) for t in tiers},
              'metric_sources': {
                  'vmaf': 'libvmaf pooled_metrics.vmaf.mean',
                  'psnr_hvs': 'libvmaf feature=name=psnr_hvs',
                  'psnr': 'standalone psnr filter average:',
                  'ssim': 'standalone ssim filter All:',
                  'xpsnr': 'standalone xpsnr filter'},
              'per_material': {}}
    print(f'ffmpeg: {ffver}')
    print(f'根: {ROOT}\n工作目录: {work}\n档位: {tiers}')

    per_tier_points = {t: [] for t in tiers}
    for src in srcs:
        print(f'\n══ 素材 {src.name} ══')
        prep, hdr = make_prep(src, work, args.duration, args.width, args.height)
        nframes, _, fps, _, _, _, _ = ffprobe_video(prep)
        print(f'  prep: {prep.name}  {prep.stat().st_size/1024:.1f} KiB  '
              f'md5={md5(prep)}  frames={nframes}  fps={fps:.2f}  hdr={hdr}')

        # 锚点（libx264）与目标都对齐同一个 prep —— 显式断言防回归
        assert prep.is_file(), 'prep 缺失'
        metrics = {}
        for key in ['libx264'] + tiers:
            vals = ANCHOR_CRFS if key == 'libx264' else SWEEP[_ffcodec(key)]
            metrics[key] = []
            for v in vals:
                pk = _ptkey(src.name, key, v)
                if args.resume and pk in points:
                    m = points[pk]['m']
                    metrics[key].append((v, m['vmaf']))
                    print(f'    [skip] {key:14} v={v:>3}  vmaf={_fmt(m["vmaf"], 3)}')
                    continue
                out = work / f'{key.replace("@", "_")}_{v}.mp4'
                sz, dt = encode(prep, key, v, out)
                m = measure(out, prep, nframes, work,
                            with_filters=args.with_filters, subsample=args.subsample)
                m['kbps'] = video_kbps(out)
                m['bytes'] = sz
                out.unlink(missing_ok=True)
                points[pk] = {'m': m, 'sec': round(dt, 2), 'material_md5': md5(prep)}
                _save_points(pts_path, points)
                metrics[key].append((v, m['vmaf']))
                print(f'    {key:14} {QUALITY_FLAG[_ffcodec(key)]} {v:>3} '
                      f'→ {sz/1024:8.1f} KiB  vmaf={_fmt(m["vmaf"], 3)} '
                      f'hvs={_fmt(m["psnr_hvs"])} psnr={_fmt(m["psnr"])} '
                      f'ssim={_fmt(m["ssim"])} xpsnr={_fmt(m["xpsnr"])}  ({dt:5.1f}s)',
                      flush=True)

        ref_m = [(v, m) for v, m in metrics['libx264']]
        res = {}
        for key in tiers:
            r = calibrate_tier(key, ref_m, metrics[key])
            res[key] = r
            if 'a' in r:
                per_tier_points[key] += list(r['points'])
            print(f'  [{key}] a={r.get("a")} b={r.get("b")} '
                  f'resid={r.get("max_resid_param")} dVMAF={r.get("max_delta_vmaf")} '
                  f'model={r.get("model")} mono={r.get("monotonic_violation")}'
                  + ('  ⚠needs_piecewise' if r.get('needs_piecewise') else ''))
        report['per_material'][src.name] = {
            'src_md5': md5(src), 'prep_md5': md5(prep), 'frames': nframes,
            'fps': round(fps, 3), 'hdr': hdr, 'eqq': res}
        (work / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if not args.keep:
            for f in work.glob('*.mp4'):
                f.unlink(missing_ok=True)

    # ── 跨素材聚合 ──────────────────────────────────────────────────────────
    table = {}
    print('\n── 跨素材聚合（a=池化最小二乘，b=各素材中位数）──')
    for key in tiers:
        pts = per_tier_points[key]
        if len(pts) < 2:
            print(f'  {key}: 点不足，跳过')
            continue
        a, _ = fit_line([x for x, _ in pts], [y for _, y in pts])
        bs = []
        for mat in report['per_material'].values():
            r = mat['eqq'].get(key, {})
            if 'a' in r:
                bs.append(statistics.median([y - a * x for x, y in r['points']]))
        b = statistics.median(bs) if bs else 0.0
        lo, hi = CRF.QUALITY_MAP[_ffcodec(key)][2], CRF.QUALITY_MAP[_ffcodec(key)][3]
        table[key] = [round(a, 4), round(b, 4), int(lo), int(hi)]
        print(f'  {key:14} a={a:.4f} b={b:+.3f}  区间=[{lo},{hi}]  '
              f'b_m范围=[{min(bs):+.2f}, {max(bs):+.2f}]  crf21→{a*21+b:.2f}')
    report['table'] = table

    (work / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告: {work / "report.json"}')
    print('候选 QUALITY_MAP（等质量，软编）；rav1e@10 请另存 _EQQUAL_SPEED_OVERRIDE：')
    for k, v in table.items():
        print(f"    '{k}': ({v[0]}, {v[1]}, {v[2]}, {v[3]}),")
    return 0


if __name__ == '__main__':
    sys.exit(main())
