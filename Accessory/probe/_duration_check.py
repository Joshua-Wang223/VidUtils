#!/usr/bin/env python3
"""_duration_check.py —— 选项 2：20 帧代理 vs 全长「差分 PSNR(codec−x265)」校验（§10.2）。

口径（与标定完全一致，复用 calibrate_hdr_equal_quality 的 build_master/encode/psnr_hdr）：
  · 全长母版 = 模型 CPU 推理全部帧（agcm、gbrp16le）→ libx265 -qp 0 无损锚
  · 20 帧参考差分：Δ20(v) = svtav1_curve_20f(v) − target_psnr_20f（取标定 per-material json）
  · 全长差分：Δfull(v) = PSNR(svt_full(v), 无损锚) − PSNR(x265_full(crf_sent21), 无损锚)
  · 判据：|Δfull − Δ20| ≤ 0.3 dB ⇒ 20 帧代理无偏（闭环）；否则按 §10.2 扩查重估
  · 编码点 v：标定实测曲线上**跨 v21 的两个点**（v21 = 20 帧曲线上取等 PSNR 的质量值）

用法：
  python3 Accessory/probe/_duration_check.py --materials cganim_edu_wordworld_src720x576,cganim_subs_src1920x1080
结果写 Accessory/temp/hdr_eqq_full/<material>/duration_check.json（+ 汇总打印）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'Accessory' / 'probe'))
from calibrate_hdr_equal_quality import (MAT_DIR, build_master, encode,  # noqa: E402
                                         psnr_hdr)

EQ_DIR = ROOT / 'Accessory/temp/hdr_eqq'
OUT = ROOT / 'Accessory/temp/hdr_eqq_full'
MAX_FRAMES = 100000  # 解码到 EOF（-frames:v 仅作上限保险）


def load_20f(stem: str) -> dict:
    return json.loads((EQ_DIR / f'{stem}.json').read_text(encoding='utf-8'))


def monotone(pts: list) -> list:
    cur = sorted(pts, key=lambda d: d['value'])
    changed = True
    while changed and len(cur) > 2:
        changed = False
        for i in range(len(cur) - 1):
            if cur[i + 1]['psnr'] > cur[i]['psnr'] + 1e-9:
                cur = cur[:i] + cur[i + 1:]
                changed = True
                break
    return cur


def interp_v(curve: list, target: float) -> float | None:
    for a, b in zip(curve, curve[1:]):
        if (a['psnr'] - target) * (b['psnr'] - target) <= 0 and a['psnr'] != b['psnr']:
            return a['value'] + (a['psnr'] - target) * (b['value'] - a['value']) / (a['psnr'] - b['psnr'])
    return None


def pick_points(curve: list, v21: float) -> list[int]:
    """跨 v21 的实测点（一低一高）；边界时退化为最近一点。"""
    vals = sorted(p['value'] for p in curve)
    lo = [v for v in vals if v <= v21]
    hi = [v for v in vals if v >= v21]
    picks = []
    if lo:
        picks.append(lo[-1])
    if hi and (not picks or hi[0] != picks[0]):
        picks.append(hi[0])
    return picks or [min(vals, key=lambda v: abs(v - v21))]


def run(name: str) -> dict:
    src = MAT_DIR / (name if name.endswith('.mp4') else name + '.mp4')
    stem = pathlib.Path(name).stem
    work = OUT / stem
    work.mkdir(parents=True, exist_ok=True)
    rec_path = work / 'duration_check.json'
    j20 = load_20f(stem)
    curve = monotone(j20['curves']['libsvtav1']['points'])
    t20 = j20['target_psnr']
    v21 = interp_v(curve, t20)
    if v21 is None:
        return {'material': name, 'error': '20 帧曲线取不到 v21'}
    pts = pick_points(curve, v21)

    master = work / 'master_hdr.gbrp16le'  # build_master 的固定产物名（本目录专用于全长）
    if not master.exists():
        build_master(src, work, MAX_FRAMES)
    wh = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries',
                         'stream=width,height', '-of', 'csv=p=0', str(src)],
                        capture_output=True, text=True).stdout.strip()
    W, H = (int(x) for x in wh.split(',')[:2])
    n_frames = master.stat().st_size // (W * H * 3 * 2)
    print(f'[{stem}] 全长母版 {n_frames} 帧 {W}x{H}  v21={v21:.2f}  编码点={pts}', flush=True)

    ref = work / 'master_lossless_full.mp4'
    if not ref.exists() and not encode(master, W, H, ref, 'libx265', '-qp', 0):
        return {'material': name, 'error': '全长无损锚编码失败'}

    crf_sent = j20['anchors']['21']['crf_sent']
    tgt = work / f'libx265_crf{crf_sent}_full.mp4'
    if not tgt.exists() and not encode(master, W, H, tgt, 'libx265', '-crf', crf_sent):
        return {'material': name, 'error': 'x265 目标全长编码失败'}
    t_full = psnr_hdr(tgt, ref)
    if t_full is None:
        return {'material': name, 'error': 'x265 目标全长 PSNR 失败'}

    rows = []
    for v in pts:
        outf = work / f'libsvtav1_crf{v}_full.mp4'
        if not outf.exists() and not encode(master, W, H, outf, 'libsvtav1', '-crf', v):
            rows.append({'crf': v, 'error': 'svtav1 全长编码失败'})
            continue
        p_full = psnr_hdr(outf, ref)
        p20 = next(p['psnr'] for p in curve if p['value'] == v)
        d20 = p20 - t20
        dfull = (p_full - t_full) if p_full is not None else None
        rows.append({'crf': v, 'psnr_20f': p20, 'psnr_full': p_full,
                     'target_20f': t20, 'target_full': t_full,
                     'delta_20f': round(d20, 3),
                     'delta_full': round(dfull, 3) if dfull is not None else None,
                     'diff': round(dfull - d20, 3) if dfull is not None else None})
        print(f'[{stem}] crf{v}: Δ20={d20:+.3f}  Δfull={dfull:+.3f}  diff={dfull-d20:+.3f} dB', flush=True)

    diffs = [abs(r['diff']) for r in rows if r.get('diff') is not None]
    rec = {'material': name, 'frames_full': n_frames, 'wh': f'{W}x{H}',
           'v21_20f': round(v21, 2), 'crf_sent_x265': crf_sent,
           'target_20f': t20, 'target_full': t_full, 'points': rows,
           'max_abs_diff': round(max(diffs), 3) if diffs else None,
           'verdict': ('PASS ≤0.3 dB' if diffs and max(diffs) <= 0.3 else
                       ('FAIL >0.3 dB' if diffs else 'ERROR'))}
    rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--materials', required=True, help='逗号分隔素材（eqq_calib/6s 文件名）')
    args = ap.parse_args()
    names = [x.strip() for x in args.materials.split(',') if x.strip()]
    recs = []
    for nm in names:
        recs.append(run(nm))
    print('\n===== 汇总（判据 |Δfull − Δ20| ≤ 0.3 dB）=====')
    ok = True
    for r in recs:
        if 'error' in r:
            print(f"  {r['material']}: ERROR {r['error']}")
            ok = False
            continue
        det = ' '.join(f"crf{p['crf']}:{p['diff']:+.3f}" for p in r['points'] if p.get('diff') is not None)
        print(f"  {r['material']}: max|diff|={r['max_abs_diff']} dB  [{det}]  {r['verdict']}")
        ok = ok and r['verdict'].startswith('PASS')
    print(f"\n总判：{'PASS —— 20 帧代理无偏（闭环）' if ok else 'CHECK —— 见上（>0.3 dB 或出错需扩查）'}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
