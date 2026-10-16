#!/usr/bin/env python3
"""_plan_extensions.py —— 读 per-material json：检查每个 (编码器 × 锚点) 的目标 PSNR
是否落在已测曲线范围内；落在范围外的，按局部斜率外推生成补扫点清单。

输出可**直接**喂 `_extend_points.py --add`（--all 时按素材输出一行行 tab 分隔）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STEP = {"libsvtav1": 4, "libaom-av1": 4, "librav1e": 8}
HI = {"libsvtav1": 63, "libaom-av1": 63, "librav1e": 255}
FLAG = {"libsvtav1": "-crf", "libaom-av1": "-crf", "librav1e": "-qp"}


def extrapolate(pts, t, codec):
    """目标 PSNR t 在曲线范围外时，给出补扫值（含余量）。"""
    st = STEP[codec]
    have = {p["value"] for p in pts}
    ps = [p["psnr"] for p in pts]
    lo, hi = min(ps), max(ps)
    if lo <= t <= hi:
        return []
    if t < lo:  # 目标比曲线最低还低 → 需更低画质（更大值）
        v1, p1 = pts[-2]["value"], pts[-2]["psnr"]
        v2, p2 = pts[-1]["value"], pts[-1]["psnr"]
        if p2 >= p1:
            return []
        est = v2 + (t - p2) * (v2 - v1) / (p2 - p1)
        top = min(HI[codec], int(est) + 3 * st)
        vals = list(range(int(v2) + st, top + 1, st))
        if vals and vals[-1] < est:
            vals.append(min(HI[codec], vals[-1] + st))
    else:  # 目标比曲线最高还高 → 需更高画质（更小值）
        v1, p1 = pts[0]["value"], pts[0]["psnr"]
        v2, p2 = pts[1]["value"], pts[1]["psnr"]
        if p2 >= p1:
            return []
        est = v1 + (t - p1) * (v1 - v2) / (p1 - p2)
        bottom = max(0, int(est) - 3 * st)
        vals = list(range(bottom, int(v1), st))
    return sorted({v for v in vals if 0 <= v <= HI[codec] and v not in have})


def plan_one(rec):
    anchors = rec.get("anchors", {})
    adds, notes = {}, []
    for codec, cur in rec.get("curves", {}).items():
        if codec not in STEP:
            continue
        pts = sorted(cur["points"], key=lambda d: d["value"])
        ps = [p["psnr"] for p in pts]
        lo, hi = min(ps), max(ps)
        need = set()
        for ref, a in sorted(anchors.items(), key=lambda kv: int(kv[0])):
            t = a["psnr"]
            if lo <= t <= hi:
                notes.append(f"{codec}@{ref}:IN")
            else:
                need.update(extrapolate(pts, t, codec))
                notes.append(f"{codec}@{ref}:OUT({'需更低画质' if t < lo else '需更高画质'})")
        if need:
            adds[codec] = sorted(need)
    return adds, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--material")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    work = Path(args.work)
    if args.material:
        stems = [args.material]
    else:
        stems = [f.stem for f in sorted(work.glob("*.json")) if f.name != "aggregate.json"]
    for stem in stems:
        jf = work / f"{stem}.json"
        rec = json.loads(jf.read_text(encoding="utf-8"))
        if not rec.get("anchors"):
            print(f"{stem}\t（无 anchors，未补测）")
            continue
        adds, notes = plan_one(rec)
        if args.all:
            s = ";".join(f"{c}={FLAG[c]}:{','.join(map(str, vs))}" for c, vs in adds.items())
            print(f"{stem}\t{s}")
        else:
            print(f"== {stem}")
            print("   " + "  ".join(notes))
            for c, vs in adds.items():
                print(f"   add {c}={FLAG[c]}:{','.join(map(str, vs))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
