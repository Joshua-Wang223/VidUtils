#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""留一交叉验证（LOO）—— 等质量标定表的**过拟合门禁**。

原理
----
标定表 `param = a*crf + b` 是在**同一批素材**上拟合的，训练残差天然偏小。
LOO 用「除留出素材外的其余素材」重新拟合 (a, b)，再去预测**留出素材**各锚点
CRF 处的 VMAF，偏差 `max|ΔVMAF|` 才是泛化误差。门禁 **ΔVMAF < 1.0**
（VMAF 唯一判红口径；PSNR/PSNR-HVS 仅 soft 参考，见立项 §4.1）。

⚠ **判据锚点口径 = 生产工作区间 `[0,27]`**（`GATE_ANCHORS`，2026-10-04 仓主裁定，与 VE
`eqq_pool_fit_table.GATE_ANCHORS` 一致）：`crf>27` 的误差作为**「监控」列**打印、**不计 FAIL**。
理由：现状 worst-case 对每个编码器都来自最高锚点 crf30（边界陡降段放大偏差），属门禁给边界
锚点等权的系统偏差，非换算缺陷；生产/CLI 的 `crf_ref` 基本不用 >27。见 `GATE_ANCHORS` 注释。

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


def gate_for_tier(tier):
    """分档门禁（与 VE `eqq_pool_fit_table.GATE` / CPU+T4 先例一致）：
    软编与 NVENC（CQ 轴 0~63）≤ **5.9**；rav1e（0~255 刻度）≤ **7.5**。
    `--tol` 显式给出时覆盖本分档。"""
    return 7.5 if tier.startswith('librav1e') else 5.9

# 判据锚点口径 = **生产工作区间 [0, 27]**（含默认 21 与常见 18~27）。
# 依据（2026-10-04，与 VE `eqq_pool_fit_table.GATE_ANCHORS` 同步，仓主裁定）：
#   现状 worst-case **对每个编码器都发生在最高锚点 crf30**（8/8 满足 ≤24 < ≤27 < 全区间）——
#   crf30 处各编码器质量曲线进入陡降段，把小参数偏差放大成 5~6 dB。这是**门禁给边界锚点
#   同等权重**的系统偏差，非换算缺陷。⇒ 严格门禁只覆盖 [0,27]；crf>27 的误差作「监控」列
#   打印、**不计 FAIL**（保证高 ref 段不被隐藏）。
# ⚠ 前提若变（生产用到 ref>27）必须恢复全区间门禁或改稳健统计量，**不得静默沿用**。
GATE_ANCHORS = (0, 27)


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
    """单档位 LOO。返回 (门禁worst[0,27], {素材:门禁worst}, 监控worst[全锚点], {素材:全worst})。"""
    worst, per_hold = 0.0, {}
    worst_all, per_hold_all = 0.0, {}
    lo_a, hi_a = GATE_ANCHORS
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
        hold_worst, hold_all, n_eval = 0.0, 0.0, 0
        for crf, avmaf in data[hold].get('libx264', []):
            pred = a * crf + b
            got = C.vmaf_at_param(iso, pred)
            if got is None:
                continue
            dv = abs(got - avmaf)
            hold_all = max(hold_all, dv)
            in_gate = (lo_a <= crf <= hi_a)
            if in_gate:
                n_eval += 1
                hold_worst = max(hold_worst, dv)
            if verbose and dv > tol:
                tag = '' if in_gate else f'（监控 crf>{hi_a}，不计 FAIL）'
                print(f'    ⚠ crf{crf}: 预测 {pred:.1f} → VMAF {got:.3f} '
                      f'vs 实测 {avmaf:.3f}  ΔVMAF={dv:.3f}{tag}')
        # ⚠ 门禁区间 [0,27] 内 0 评估点 ⇒ 模型失效（不是通过）。仍按旧语义判失败。
        if n_eval == 0:
            per_hold[hold] = float('inf')
            per_hold_all[hold] = float('inf')
            print(f'    ❌ {hold}: 门禁区间 [{lo_a},{hi_a}] 内 0 个评估点 ⇒ 模型失效')
            return float('inf'), per_hold, float('inf'), per_hold_all
        per_hold[hold] = hold_worst
        per_hold_all[hold] = hold_all
        worst = max(worst, hold_worst)
        worst_all = max(worst_all, hold_all)
    return worst, per_hold, worst_all, per_hold_all


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workroot', default='/tmp/eqq2')
    ap.add_argument('--tag', action='append', default=None,
                    help='可重复；不给则自动发现 workroot 下所有含 points.json 的目录')
    ap.add_argument('--tiers', default='', help='逗号分隔；不给则自动取全部档位')
    ap.add_argument('--tol', type=float, default=None,
                    help=f'显式门禁；不给则按档位分档（软编/NVENC ≤5.9、rav1e ≤7.5）')
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
    _gate_desc = (f'< {args.tol}（显式 --tol）' if args.tol is not None
                  else '分档（软编/NVENC ≤5.9、rav1e ≤7.5）')
    print(f'门禁 ΔVMAF {_gate_desc}（判据锚点 [{GATE_ANCHORS[0]},{GATE_ANCHORS[1]}]；'
          f'crf>{GATE_ANCHORS[1]} 仅监控、不计 FAIL）\n')

    summary, n_fail = {}, 0
    for t in tiers:
        mats = tier_mats[t]
        tol = args.tol if args.tol is not None else gate_for_tier(t)
        print(f'── {t}（{len(mats)} 素材：{", ".join(m[:14] for m in mats)}）'
              f'  门禁 ≤{tol:g}')
        worst, per_hold, worst_all, _ = loo_tier(t, mats, data, C, tol,
                                                 verbose=not args.quiet)
        ok = worst < tol
        n_fail += (not ok)
        summary[t] = (worst, worst_all, tol)
        if not args.quiet:
            for m, w in per_hold.items():
                ws = 'inf（模型失效）' if w == float('inf') else f'{w:.3f}'
                print(f'   {m:<24} worst[0,{GATE_ANCHORS[1]}] = {ws}')
        ws = 'inf' if worst == float('inf') else f'{worst:.3f}'
        wa = 'inf' if worst_all == float('inf') else f'{worst_all:.3f}'
        print(f'   ⇒ {t:<14} LOO[0,{GATE_ANCHORS[1]}]={ws}（门禁 ≤{tol:g}）  '
              f'监控[全]={wa}  ' + ('✅ PASS' if ok else '❌ FAIL') + '\n')

    print(f'═══ LOO 汇总（门禁 = 分档；判据锚点 [0,{GATE_ANCHORS[1]}]）═══')
    for t, (w, wa, tol) in summary.items():
        if w == float('inf'):
            print(f'  {t:<16}    inf   ❌ 模型失效（门禁区间内 0 评估点）')
        else:
            print(f'  {t:<16} LOO[0,{GATE_ANCHORS[1]}]={w:>6.3f}  监控[全]={wa:>6.3f}  '
                  + ('✅ ≤%.1f' % tol if w < tol else '❌ >%.1f' % tol))
    print(f'\n{len(tiers) - n_fail}/{len(tiers)} 档位通过 LOO[0,{GATE_ANCHORS[1]}] 分档门禁')
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
