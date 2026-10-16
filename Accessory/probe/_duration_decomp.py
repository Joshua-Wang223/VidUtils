#!/usr/bin/env python3
"""_duration_decomp.py —— 选项 2 配套：全长差分漂移的逐帧分解（§10.2 扩查）。

与 _duration_check.py 配套使用（读它产出的 duration_check.json），不重编码，
只对既有全长产物做 psnr 统计重算与分解。

口径（与 psnr_hdr / 标定 JSON 完全同构）：
  psnr 滤镜 summary = 「合并 MSE」口径 ⇒ 子集统计取逐帧 mse_avg 的算术平均再转 dB：
    PSNR_pooled(subset) = 10log10( MAX² / mean_mse(subset) )，MAX=1023（10bit PQ）
  Δ(subset) = PSNR_pooled(svt, subset) − PSNR_pooled(x265, subset)
            = 10log10( mean_mse_x(subset) / mean_mse_s(subset) )

分解（total = Δfull − Δ20(json)）：
  context   = Δ(全长编码@前20帧) − Δ20(json)      ← 同帧集、编码上下文 20 帧→全长
  frame-set = Δfull − Δ(全长编码@前20帧)          ← 同上下文、帧集合 开头20帧→全帧
  另给 context 的两个分量（各编解码器自身 20ctx→fullctx 位移）：
    x265: PSNR_pooled(全长编码@前20帧) − target_20f(json)
    svt : PSNR_pooled(全长编码@前20帧) − psnr_20f(json 曲线点)

用法：
  python3 Accessory/probe/_duration_decomp.py <stem> [<stem> ...] [--out 路径]
结果：逐条打印 + 汇总写 Accessory/temp/hdr_eqq_full/_decomp_stats/decomp_summary.json
（表格解释见 memory/hdr_equal_quality_calibration_10mat_20261016.md §10.2；证据归档
 Accessory/calib/eqq_evidence/duration_check_full_20261017/）
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'Accessory' / 'probe'))
from bench_sdr_to_hdr import _ffmpeg_bin  # noqa: E402

FF = _ffmpeg_bin()
WORK = ROOT / 'Accessory/temp/hdr_eqq_full'
EQ = ROOT / 'Accessory/temp/hdr_eqq'
STATS = WORK / '_decomp_stats'
MAX2 = 1023.0 ** 2


def make_stats(dist: pathlib.Path, ref: pathlib.Path, out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [FF, '-hide_banner', '-nostdin', '-i', str(dist), '-i', str(ref),
         '-lavfi', f'psnr=stats_file={out}', '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f'{dist.name}: {r.stderr[-300:]}')


def get_stats(dist: pathlib.Path, ref: pathlib.Path, out: pathlib.Path) -> list[float]:
    if not out.exists():
        make_stats(dist, ref, out)
    return [float(m.group(1))
            for line in out.read_text().splitlines()
            if (m := re.match(r'n:\d+\s+mse_avg:([0-9.]+)', line))]


def pooled(mse: list[float], a: int | None = None, b: int | None = None) -> float:
    part = mse[a:b] if a is not None else mse
    return 10 * math.log10(MAX2 / (sum(part) / len(part)))


def delta(ms: list[float], mx: list[float], a=None, b=None) -> float:
    """Δ = PSNR_s − PSNR_x（合并 MSE 口径）。"""
    return pooled(ms, a, b) - pooled(mx, a, b)


def decomp(stem: str, out_summary: dict) -> None:
    wk = WORK / stem
    j = json.loads((wk / 'duration_check.json').read_text(encoding='utf-8'))
    j20 = json.loads((EQ / f'{stem}.json').read_text(encoding='utf-8'))
    ref = wk / 'master_lossless_full.mp4'
    mx = get_stats(wk / f'libx265_crf{j["crf_sent_x265"]}_full.mp4', ref,
                   STATS / f'{stem}.stats_x265')
    n = len(mx)
    t20 = j20['target_psnr']
    res = {'material': stem, 'frames': n,
           'x265': {'target_20f': t20, 'fullctx_f20': round(pooled(mx, 0, 20), 3),
                    'fullctx_all': round(pooled(mx), 3),
                    'json_target_full': round(j['target_full'], 3)},
           'points': []}
    for p in j['points']:
        v = p['crf']
        ms = get_stats(wk / f'libsvtav1_crf{v}_full.mp4', ref, STATS / f'{stem}.stats_svt{v}')
        d_full = delta(ms, mx)
        d_f20 = delta(ms, mx, 0, 20)
        d20 = p['delta_20f']
        ctx = d_f20 - d20
        fset = d_full - d_f20
        segs = [round(delta(ms, mx, a, min(a + 20, n)), 2) for a in range(0, n, 20)]
        rec = {'crf': v, 'delta_full': round(d_full, 3),
               'json_delta_full': p['delta_full'],
               'delta_f20_fullctx': round(d_f20, 3), 'delta_20f_json': d20,
               'context': round(ctx, 3), 'frame_set': round(fset, 3),
               'total': round(d_full - d20, 3),
               'svt_ctx_shift': round(pooled(ms, 0, 20) - p['psnr_20f'], 3),
               'x265_ctx_shift': round(pooled(mx, 0, 20) - t20, 3),
               'segments_20': segs}
        res['points'].append(rec)
        print(f"  crf{v}: Δfull={d_full:+.3f} (json {p['delta_full']:+.3f})  "
              f"Δf20={d_f20:+.3f}  Δ20={d20:+.3f}  | ctx={ctx:+.3f} fset={fset:+.3f} "
              f"total={d_full - d20:+.3f}")
        print(f"        位移分量: x265 {rec['x265_ctx_shift']:+.3f} / svt {rec['svt_ctx_shift']:+.3f}")
        print('        分段: ' + '  '.join(f'{a}-{min(a + 20, n)}:{d:+.2f}'
                                          for a, d in zip(range(0, n, 20), segs)))
    out_summary[stem] = res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('stems', nargs='+', help='素材 stem（有 duration_check.json 的）')
    ap.add_argument('--out', default=str(STATS / 'decomp_summary.json'))
    args = ap.parse_args()

    summary = {}
    for stem in args.stems:
        print(f'===== {stem}')
        if not (WORK / stem / 'duration_check.json').exists():
            print('  （无 duration_check.json，跳过）')
            continue
        decomp(stem, summary)
        x = summary[stem]
        print(f"  x265 汇总: 20f(json)={x['x265']['target_20f']:.3f}  "
              f"fullctx@f20={x['x265']['fullctx_f20']:.3f}  "
              f"fullctx@all={x['x265']['fullctx_all']:.3f}  "
              f"(json full={x['x265']['json_target_full']:.3f})")
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n汇总已写 {out}')


if __name__ == '__main__':
    main()
