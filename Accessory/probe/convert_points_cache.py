#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧格式 points_cache.json → 新格式 points.json（跨版本数据迁移）。

背景
----
标定 harness 早期版本按「整素材缓存」落 ``points_cache.json``，键为
``素材|时长|档位|参数``；现版本按「逐点」落 ``points.json``，键为
``素材|档位|参数``。VU 侧 2026-10-01 之前跑完的 7 素材标定用的是旧格式，
其数据可直接复用（免去重跑数小时 CPU），只需迁移键格式。

口径差异（旧缓存独有，迁移时保留但不参与计算）
    * ``vif_scale0`` / ``adm2``：旧版额外采集的指标，现版不再采集。
    * ``psnr`` / ``ssim`` / ``xpsnr``：旧缓存为 ``None``（当时未开 ``--with-filters``）。

用法
----
    python3 <this> --src <workdir>/points_cache.json --dst <workdir>/points.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def convert(src: Path, dst: Path, overwrite: bool = False):
    if dst.exists() and not overwrite:
        print(f'{dst} 已存在（加 --overwrite 覆盖）', file=sys.stderr)
        return None
    raw = json.loads(src.read_text(encoding='utf-8'))
    out, skipped = {}, 0
    for key, v in raw.items():
        parts = key.split('|')
        if len(parts) != 4:
            skipped += 1
            continue
        mat, _dur, tier, val = parts
        try:
            val_f = float(val)
        except ValueError:
            skipped += 1
            continue
        m = {k: v.get(k) for k in
             ('vmaf', 'psnr_hvs', 'psnr', 'ssim', 'xpsnr', 'kbps')}
        m['bytes'] = v.get('_bytes')
        # 旧缓存的额外指标（现版不采集，仅留存供诊断）
        for k in ('vif_scale0', 'adm2'):
            if v.get(k) is not None:
                m[k] = v[k]
        out[f'{mat}|{tier}|{val}'] = {'m': m, 'sec': None,
                                      'legacy': 'points_cache.json'}
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    return out, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='旧 points_cache.json 路径')
    ap.add_argument('--dst', required=True, help='新 points.json 输出路径')
    ap.add_argument('--overwrite', action='store_true')
    args = ap.parse_args()

    r = convert(Path(args.src), Path(args.dst), args.overwrite)
    if r is None:
        return 2
    out, skipped = r
    print(f'迁移 {len(out)} 点 → {args.dst}（跳过 {skipped} 个无法解析的键）')
    from collections import Counter
    print('  档位:', dict(Counter(k.split('|')[1] for k in out)))
    print('  素材:', dict(Counter(k.split('|')[0] for k in out)))
    novmaf = sum(1 for v in out.values() if v['m'].get('vmaf') is None)
    if novmaf:
        print(f'  ⚠ {novmaf} 点无 vmaf（迁移保留，LOO 会跳过）')
    return 0


if __name__ == '__main__':
    sys.exit(main())