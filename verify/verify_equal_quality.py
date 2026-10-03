#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等质量换算表（QUALITY_MAP）回归判据。

对每条已落表（软编）编码器 × 真实素材：用**等质量表**算出目标参数 → 实测重编码 →
与同一 libx264 锚点比质量。判据（口径对齐 VE v2 §4.1「唯一来源 / 同轴」原则）：
  * 主门禁（**唯一 FAIL 依据**）：|ΔVMAF| ≤ 1.0
  * 平行**参考**指标（**soft，只 WARN 不判红**）：
      - |ΔPSNR|     ≤ 0.3 dB（独立 psnr 滤镜）
      - |ΔPSNR-HVS| ≤ 0.5 dB（libvmaf feature）
    ⚠ 等质量表以 **VMAF** 定标 ⇒ 同 VMAF **不蕴含**同 PSNR，拿紧 PSNR 判红必然假阳性。

无 GPU：硬编条目不在 QUALITY_MAP 内 ⇒ 自然 SKIP；若**全部** SKIP 则退出码 2
（防"静默通过"）。

用法：
  python3 verify/verify_equal_quality.py < /dev/null
  python3 verify/verify_equal_quality.py --src <素材> --crf 24 --duration 6 < /dev/null
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOL_VMAF = 1.0   # 主门禁（唯一 FAIL 依据）：等质量表以 VMAF 定标，故只用 VMAF 判红
TOL_PSNR = 0.3   # 参考阈值（soft）：跨轴指标，超界只 WARN（见 §4.1「同轴」原则）
TOL_HVS = 0.5    # 参考阈值（soft）：同上

SOFT = ('libx265', 'libvpx-vp9', 'libaom-av1', 'libsvtav1', 'librav1e')
DEFAULT_SRC = ROOT.parent / 'input_videos' / 'new5_raw.mp4'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 复用标定脚本同口径的 prep / encode / measure，避免两份实现漂移
C = _load('eqq_calib', ROOT / 'probe' / 'calibrate_equal_quality.py')
CRF = _load('vu_convert_crf', ROOT / 'convert_crf.py')


def _f(v, spec):
    return 'NA' if v is None else format(v, spec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=str(DEFAULT_SRC))
    ap.add_argument('--duration', type=float, default=6.0,
                    help='须与标定口径一致（标定用 6s）；时长不同 VMAF 曲线不同会判红')
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--crf', type=int, default=21, help='libx264 锚点 CRF')
    ap.add_argument('--codecs', default=','.join(SOFT))
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_file():
        print(f'源素材不存在：{src}', file=sys.stderr)
        return 2

    work = ROOT / 'temp' / 'verify_equal_quality'
    work.mkdir(parents=True, exist_ok=True)

    codecs = [c.strip() for c in args.codecs.split(',') if c.strip()]
    table = getattr(CRF, 'QUALITY_MAP', {})
    tested = [c for c in codecs if c in table]
    skipped = [c for c in codecs if c not in table]

    print('── 等质量回归判据 ──')
    print(f'源: {src.name}  {args.duration:g}s  锚点 libx264 CRF {args.crf}')
    print(f'等质量表覆盖: {sorted(table) or "（空）"}')
    if skipped:
        print(f'SKIP（等质量表未覆盖，需上机/待标定）: {skipped}')
    if not tested:
        print('✗ 无任何可测条目（等质量表为空或所选编码器均未覆盖）')
        return 2

    prep, hdr = C.make_prep(src, work, args.duration, args.width, args.height)
    nframes, _, _, _, _, _, _ = C.ffprobe_video(prep)
    print(f'prep: md5={C.md5(prep)}  frames={nframes}  hdr={hdr}')

    # 锚点：libx264 crf → 质量
    anchor_out = work / 'anchor.mp4'
    C.encode(prep, 'libx264', args.crf, anchor_out)
    a = C.measure(anchor_out, prep, nframes, work, with_filters=True, subsample=1)
    print(f'  锚点 libx264 crf {args.crf}: vmaf={a["vmaf"]:.3f} '
          f'psnr={a["psnr"]:.3f} hvs={a["psnr_hvs"]:.3f} '
          f'ssim={_f(a["ssim"], ".4f")} xpsnr={_f(a["xpsnr"], ".3f")}')

    CRF.set_quality_mode('quality')
    fails, warns, rows = [], [], []
    try:
        for codec in tested:
            p = CRF.from_x264_crf(codec, args.crf)
            if p is None:
                fails.append(f'{codec}: 等质量表无值')
                continue
            p = int(round(p))
            out = work / f'{codec}_{p}.mp4'
            C.encode(prep, codec, p, out)
            t = C.measure(out, prep, nframes, work, with_filters=True, subsample=1)
            dv = (t['vmaf'] - a['vmaf']) if (t['vmaf'] is not None and a['vmaf'] is not None) else None
            dp = (t['psnr'] - a['psnr']) if (t['psnr'] is not None and a['psnr'] is not None) else None
            dh = (t['psnr_hvs'] - a['psnr_hvs']) if (t['psnr_hvs'] is not None and a['psnr_hvs'] is not None) else None
            # 主门禁：VMAF（唯一 FAIL 依据）
            ok = (dv is not None and abs(dv) <= TOL_VMAF)
            if not ok:
                fails.append(f'{codec}: |ΔVMAF|={abs(dv):.2f} > {TOL_VMAF}')
            # 参考指标（soft）：跨轴，超界只 WARN，不进入 fails、不影响退出码
            w = (dp is not None and abs(dp) > TOL_PSNR) or (dh is not None and abs(dh) > TOL_HVS)
            if dp is not None and abs(dp) > TOL_PSNR:
                warns.append(f'{codec}: |ΔPSNR|={abs(dp):.2f} > {TOL_PSNR}（参考，不判红）')
            if dh is not None and abs(dh) > TOL_HVS:
                warns.append(f'{codec}: |ΔPSNR-HVS|={abs(dh):.2f} > {TOL_HVS}（参考，不判红）')
            rows.append({'codec': codec, 'param': p, 'vmaf': t['vmaf'],
                         'psnr': t.get('psnr'), 'psnr_hvs': t.get('psnr_hvs'),
                         'ssim': t.get('ssim'), 'xpsnr': t.get('xpsnr'),
                         'd_vmaf': dv, 'd_psnr': dp, 'd_psnr_hvs': dh, 'ok': bool(ok)})
            flag = ('✓' if ok else '✗') + (' ⚠' if w else '')
            print(f'  {flag} {codec:11} q={p:>4}  vmaf={t["vmaf"]:.3f}  '
                  f'ΔVMAF={dv:+.3f}  ΔPSNR={dp:+.3f}  ΔPSNR-HVS={dh:+.3f}  '
                  f'| ssim={_f(t.get("ssim"), ".4f")} xpsnr={_f(t.get("xpsnr"), ".3f")}  '
                  f'(参考门限 {TOL_PSNR}/{TOL_HVS}，不判红)')
    finally:
        CRF.set_quality_mode('size')
        if not args.keep:
            for f in work.glob('*.mp4'):
                f.unlink(missing_ok=True)

    print(f'\n结果: {sum(r["ok"] for r in rows)}/{len(rows)} 达标（主门禁 ΔVMAF）'
          + (f'，{len(skipped)} 项 SKIP' if skipped else ''))
    if warns:
        print(f'  ⚠ 参考指标超界 {len(warns)} 项（不影响退出码）:')
        for x in warns:
            print(f'    · {x}')
    if fails:
        for f in fails:
            print(f'  ✗ {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
