#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""留一交叉验证（LOO）—— 等质量标定表的**过拟合门禁**。

原理
----
标定表 `param = a*crf + b` 是在**同一批素材**上拟合的，训练残差天然偏小。
LOO 用「除留出素材外的其余素材」重新拟合 (a, b)，再去预测**留出素材**各锚点
CRF 处的 VMAF，偏差 `max|ΔVMAF|` 才是泛化误差。门禁 **ΔVMAF < 1.0**
（VMAF 唯一判红口径；PSNR/PSNR-HVS 仅 soft 参考，见立项 §4.1）。

用法
----
    # 单 workdir
    python3 <this> --workroot /tmp/eqq2 --tag 1280x720_10s_n4

    # 多 workdir 合并（Stage3 单独 workdir 时**必须**这样传，口径才与其它档位一致）
    python3 <this> --workroot /tmp/eqq2 --tag 1280x720_10s_n4 --tag 1280x720_10s_n2

    # 只看某几个档位
    python3 <this> --workroot /tmp/eqq2 --tiers libx265,librav1e@10 --tol 1.0

不重编码：直接读标定产出的 `points.json`（键 `素材|档位|参数`），用该素材
已测的 (param, vmaf) 曲线反查，因此秒级完成。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_TOL = 1.0


def _load_harness():
    """import 同目录的 calibrate_equal_quality.py（复用其拟合/插值函数）。"""
    here = Path(__file__).resolve().parent
    for cand in (here / 'calibrate_equal_quality.py',
                 here.parent / 'probe' / 'calibrate_equal_quality.py'):
        if cand.is_file():
            break
    else:
        raise SystemExit('找不到 calibrate_equal_quality.py')
    spec = importlib.util.spec_from_file_location('eqq', cand)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, cand


def collect(workroot, tags):
    """读一个或多个 workdir 的 points.json → {素材: {档位: [(param, vmaf), ...]}}。"""
    data = defaultdict(lambda: defaultdict(list))
    mats = defaultdict(set)                     # 档位 → 出现过的素材
    root = Path(workroot)
    for tag in tags:
        p = root / tag / 'points.json'
        if not p.is_file():
            print(f'⚠ 跳过（无 points.json）：{p}', file=sys.stderr)
            continue
        pts = json.loads(p.read_text(encoding='utf-8'))
        for key, v in pts.items():
            mat, tier, val = key.split('|')
            vmaf = (v.get('m') or {}).get('vmaf')
            if vmaf is None:
                continue
            data[mat][tier].append((float(val), float(vmaf)))
        print(f'  读入 {p}：{len(pts)} 点 / {len({k.split("|")[0] for k in pts})} 素材')
    for mat, tiers in data.items():
        for t in tiers:
            tiers[t].sort()
            mats[t].add(mat)
    return dict(data), {t: sorted(s) for t, s in mats.items()}


def loo_tier(tier, mats, data, C, tol, verbose=True):
    """单档位 LOO。返回 (最差 ΔVMAF, {留出素材: 最差ΔVMAF})。"""
    worst, per_hold = 0.0, {}
    for hold in mats:
        train = [m for m in mats if m != hold]
        xs, ys, bs = [], [], []
        for m in train:
            r = C.calibrate_tier(tier, data[m]['libx264'], data[m][tier])
            if 'a' not in r:
                continue
            xs += [x for x, _ in r['points']]
            ys += [y for _, y in r['points']]
        if len(xs) < 2:
            continue
        a, _ = C.fit_line(xs, ys)
        for m in train:
            r = C.calibrate_tier(tier, data[m]['libx264'], data[m][tier])
            if 'a' in r:
                bs.append(statistics.median([y - a * x for x, y in r['points']]))
        if not bs:
            continue
        b = statistics.median(bs)

        # 用留出素材**实测**的 (param, vmaf) 曲线评估（先保序非增，与拟合侧同口径）
        iso_v = C.pava_nonincreasing([v for _, v in data[hold][tier]])
        iso = [(p, v) for (p, _), v in zip(data[hold][tier], iso_v)]
        hold_worst, n_eval = 0.0, 0
        for crf, avmaf in data[hold].get('libx264', []):
            pred = a * crf + b
            got = C.vmaf_at_param(iso, pred)
            if got is None:
                continue
            n_eval += 1
            dv = abs(got - avmaf)
            hold_worst = max(hold_worst, dv)
            if verbose and dv > tol:
                print(f'    ⚠ crf{crf}: 预测 {pred:.1f} → VMAF {got:.3f} '
                      f'vs 实测 {avmaf:.3f}  ΔVMAF={dv:.3f}')
        # ⚠ 预测落在扫描区间外 ⇒ 评估点为 0。这**不是**通过，是模型失效
        #   （曾据此误报「二次模型 LOO 全 0.000 ✅」）。必须显式判失败。
        if n_eval == 0:
            per_hold[hold] = float('inf')
            print(f'    ❌ {hold}: 预测参数全部落在扫描区间外，0 个评估点 ⇒ 模型失效')
            return float('inf'), per_hold
        per_hold[hold] = hold_worst
        worst = max(worst, hold_worst)
    return worst, per_hold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workroot', default='/tmp/eqq2')
    ap.add_argument('--tag', action='append', default=None,
                    help='可重复；不给则自动发现 workroot 下所有含 points.json 的目录')
    ap.add_argument('--tiers', default='', help='逗号分隔；不给则自动取全部档位')
    ap.add_argument('--tol', type=float, default=DEFAULT_TOL, help=f'门禁（默认 {DEFAULT_TOL}）')
    ap.add_argument('--quiet', action='store_true', help='只打汇总，不打逐条')
    args = ap.parse_args()

    C, harness = _load_harness()
    print(f'harness: {harness}')

    root = Path(args.workroot)
    tags = args.tag or sorted(d.name for d in root.iterdir()
                             if d.is_dir() and (d / 'points.json').is_file()) \
        if root.is_dir() else []
    if not tags:
        print('无 points.json，LOO 无法进行', file=sys.stderr)
        return 2

    print(f'\n══ 读入points ══')
    data, tier_mats = collect(args.workroot, tags)

    tiers = ([t.strip() for t in args.tiers.split(',') if t.strip()]
             if args.tiers else sorted(tier_mats))
    tiers = [t for t in tiers if t in tier_mats and 'libx264' in data.get(tier_mats[t][0], {})]
    if not tiers:
        print('无可用档位（缺 libx264 锚点或无数据）', file=sys.stderr)
        return 2

    print(f'\n素材 {len({m for t in tiers for m in tier_mats[t]})}：'
          f'{sorted({m for t in tiers for m in tier_mats[t]})}')
    print(f'门禁 ΔVMAF < {args.tol}\n')

    summary, n_fail = {}, 0
    for t in tiers:
        mats = tier_mats[t]
        print(f'── {t}（{len(mats)} 素材：{", ".join(m[:14] for m in mats)}）')
        worst, per_hold = loo_tier(t, mats, data, C, args.tol, verbose=not args.quiet)
        ok = worst < args.tol
        n_fail += (not ok)
        summary[t] = worst
        for m, w in per_hold.items():
            ws = 'inf（模型失效）' if w == float('inf') else f'{w:.3f}'
            print(f'   {m:<24} worst ΔVMAF = {ws}')
        ws = 'inf' if worst == float('inf') else f'{worst:.3f}'
        print(f'   ⇒ {t:<14} worst={ws}  ' + ('✅ PASS' if ok else '❌ FAIL') + '\n')

    print('═══ LOO 汇总 ═══')
    for t, w in summary.items():
        if w == float('inf'):
            print(f'  {t:<16}    inf   ❌ 模型失效（预测越界）')
        else:
            print(f'  {t:<16} {w:>7.3f}   ' + ('✅ <%.1f' % args.tol if w < args.tol
                                              else '❌ ≥%.1f' % args.tol))
    print(f'\n{len(tiers) - n_fail}/{len(tiers)} 档位通过 ΔVMAF < {args.tol}')
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
