#!/usr/bin/env python3
"""_final_fit.py —— 汇总「素材 × 锚点 × 编码器」的等质量点，给出修正仿射 + 离散度/LOO。

口径：
  · 曲线 PSNR(codec, value) 与锚点无关；先做**单调化过滤**（剔除最低质量值端的凹陷
    异常点——实测 SVT-AV1 近无损档在 3/10 素材存在该凹陷，可复现，非测量误差）
  · V_ref(material) = 过滤后曲线上线性插值 target_psnr(ref)
  · 拟合：池化最小二乘 a + 各素材截距中位数 b（与现表「池化 a / b=各素材中位数」口径一致）
  · 离散度：per-ref 中位数/IQR/范围；LOO：逐素材留一，误差给出 native 单位与 dB 当量

用法：
  python3 _final_fit.py --work Accessory/temp/hdr_eqq \
      --out memory/hdr_equal_quality_calibration_10mat_20261016.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from convert_crf import QUALITY_MAP  # noqa: E402

CODECS = ("libsvtav1", "libaom-av1", "librav1e")
DISPLAY = {"libsvtav1": "-crf", "libaom-av1": "-crf", "librav1e": "-qp"}


def monotone_filter(pts):
    """按 value 升序，PSNR 应单调不增；剔除破坏单调性的点（默认剔除低值端）。
    返回 (过滤后点列表, 被剔除的点列表)。"""
    cur = sorted(pts, key=lambda d: d["value"])
    dropped = []
    changed = True
    while changed and len(cur) > 2:
        changed = False
        for i in range(len(cur) - 1):
            if cur[i + 1]["psnr"] > cur[i]["psnr"] + 1e-9:
                dropped.append(cur[i])
                cur = cur[:i] + cur[i + 1:]
                changed = True
                break
    return cur, dropped


def interp(pts, t):
    lo = min(p["psnr"] for p in pts)
    hi = max(p["psnr"] for p in pts)
    if not (lo <= t <= hi):
        return None
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if (a["psnr"] - t) * (b["psnr"] - t) <= 0 and a["psnr"] != b["psnr"]:
            return a["value"] + (a["psnr"] - t) * (b["value"] - a["value"]) / (a["psnr"] - b["psnr"])
    return None


def local_slope_dB_per_unit(pts, v):
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a["value"] <= v <= b["value"] and b["value"] != a["value"]:
            return abs((b["psnr"] - a["psnr"]) / (b["value"] - a["value"]))
    return None


def fit_pooled_a(points):
    n = len(points)
    sx = sum(x for x, _ in points); sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points); sxy = sum(x * y for x, y in points)
    return (n * sxy - sx * sy) / (n * sxx - sx * sx)


def fit_b(rows, a, codec, refs):
    bs = []
    for row in rows:
        offs = [row["vals"][(codec, r)] - a * r for r in refs
                if row["vals"].get((codec, r)) is not None]
        if offs:
            bs.append(st.median(offs))
    return st.median(bs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(ROOT / "Accessory" / "temp" / "hdr_eqq"))
    ap.add_argument("--out", default=str(ROOT / "memory" / "hdr_equal_quality_calibration_10mat_20261016.json"))
    args = ap.parse_args()
    work = Path(args.work)

    rows = []
    for jf in sorted(work.glob("*.json")):
        if jf.name == "aggregate.json":
            continue
        rec = json.loads(jf.read_text(encoding="utf-8"))
        anchors = rec.get("anchors") or {}
        if not anchors:
            continue
        row = {"material": Path(rec["material"]).stem, "vals": {}, "curves": {}, "dropped": {}}
        for codec in CODECS:
            pts, dropped = monotone_filter(rec["curves"][codec]["points"])
            row["curves"][codec] = pts
            row["dropped"][codec] = [f"v={p['value']} psnr={p['psnr']}" for p in dropped]
            for ref_s, a in anchors.items():
                row["vals"][(codec, int(ref_s))] = interp(pts, a["psnr"])
        rows.append(row)

    refs = sorted({r for row in rows for (_, r) in row["vals"]})
    print(f"素材 {len(rows)} × 锚点 {refs}\n")
    result = {"refs": refs, "materials": [r["material"] for r in rows], "codecs": {}}

    for codec in CODECS:
        print(f"===== {codec} ({DISPLAY[codec]}) =====")
        med = {}
        for ref in refs:
            xs = [row["vals"].get((codec, ref)) for row in rows]
            miss = sum(1 for x in xs if x is None)
            xs = sorted(x for x in xs if x is not None)
            if not xs:
                print(f"  ref{ref}: 全部素材超范围")
                continue
            q1, q3 = st.quantiles(xs, n=4)[0], st.quantiles(xs, n=4)[2]
            med[ref] = st.median(xs)
            print(f"  ref{ref:<3} 中位 {med[ref]:7.2f}  范围[{xs[0]:.1f}, {xs[-1]:.1f}]  "
                  f"IQR {q3 - q1:.1f}  n={len(xs)}" + (f"  超范围{miss}" if miss else ""))
        pts = [(ref, row["vals"][(codec, ref)]) for row in rows for ref in refs
               if row["vals"].get((codec, ref)) is not None]
        a = fit_pooled_a(pts)
        b = fit_b(rows, a, codec, refs)
        # 分段候选（2026-10-16 §9）：稳健增量累积（LOO 采纳）与逐锚点中位值连线（对照）
        knots_med = [(ref, med[ref]) for ref in refs if ref in med]
        knots_cum = [(knots_med[0][0], knots_med[0][1])]
        for (r0, _), (r1, _) in zip(knots_med, knots_med[1:]):
            deltas = [row["vals"][(codec, r1)] - row["vals"][(codec, r0)] for row in rows
                      if row["vals"].get((codec, r0)) is not None
                      and row["vals"].get((codec, r1)) is not None]
            knots_cum.append((r1, knots_cum[-1][1] + st.median(deltas)))
        cur_a, cur_b, lo, hi = QUALITY_MAP[codec]
        f = lambda A, B, r: A * r + B  # noqa: E731
        print(f"  新仿射 a={a:.4f} b={b:.2f}   → " +
              " / ".join(f"ref{r} {f(a, b, r):.1f}" for r in (18, 21, 24, 27, 30)))
        print(f"  现表   a={cur_a:.4f} b={cur_b:.2f}   → " +
              " / ".join(f"ref{r} {f(cur_a, cur_b, r):.1f}" for r in (18, 21, 24, 27, 30)))
        res_abs = sorted(abs(y - (a * x + b)) for x, y in pts)
        db = []
        for row in rows:
            for ref in refs:
                v = row["vals"].get((codec, ref))
                if v is None:
                    continue
                sl = local_slope_dB_per_unit(row["curves"][codec], v)
                if sl:
                    db.append(abs(v - (a * ref + b)) * sl)
        db_sorted = sorted(db)
        print(f"  仿射残差 |ΔV|: 中位 {st.median(res_abs):.1f}  P90 {res_abs[int(len(res_abs) * 0.9) - 1]:.1f}  max {res_abs[-1]:.1f}   "
              f"| dB 当量: 中位 {st.median(db):.2f}  P90 {db_sorted[int(len(db_sorted) * 0.9) - 1]:.2f}  max {db_sorted[-1]:.2f} dB")
        loo_v, loo_db = [], []
        for i, row in enumerate(rows):
            others = [r for j, r in enumerate(rows) if j != i]
            pts_o = [(ref, r["vals"][(codec, ref)]) for r in others for ref in refs
                     if r["vals"].get((codec, ref)) is not None]
            a_o = fit_pooled_a(pts_o)
            b_o = fit_b(others, a_o, codec, refs)
            for ref in refs:
                v = row["vals"].get((codec, ref))
                if v is None:
                    continue
                e = abs(v - (a_o * ref + b_o))
                loo_v.append(e)
                sl = local_slope_dB_per_unit(row["curves"][codec], v)
                if sl:
                    loo_db.append(e * sl)
        loo_v.sort(); loo_db.sort()
        print(f"  LOO |ΔV|: 中位 {st.median(loo_v):.1f}  P90 {loo_v[int(len(loo_v) * 0.9) - 1]:.1f}  max {loo_v[-1]:.1f}   "
              f"| dB 当量: 中位 {st.median(loo_db):.2f}  P90 {loo_db[int(len(loo_db) * 0.9) - 1]:.2f}  max {loo_db[-1]:.2f} dB")
        print("  分段节点（稳健累积）: " + " / ".join(f"ref{r} {y:.4f}" for r, y in knots_cum))
        result["codecs"][codec] = {
            "flag": DISPLAY[codec], "per_ref_median": {str(k): v for k, v in med.items()},
            "fit": {"a": a, "b": b, "lo": lo, "hi": hi},
            "current": {"a": cur_a, "b": cur_b},
            "resid_abs_median": st.median(res_abs), "resid_abs_max": res_abs[-1],
            "resid_db_median": st.median(db), "resid_db_max": db_sorted[-1],
            "loo_abs_median": st.median(loo_v), "loo_abs_max": loo_v[-1],
            "loo_db_median": st.median(loo_db), "loo_db_max": loo_db[-1],
            "per_material": {row["material"]: {
                str(ref): (round(row["vals"][(codec, ref)], 2)
                           if row["vals"].get((codec, ref)) is not None else None)
                for ref in refs} for row in rows},
            "piecewise": {
                "knots_median": knots_med,
                "knots_cuminc": knots_cum,
                "note": "knots_cuminc = 逐段素材内增量中位数累积（稳健；LOO 采纳，见 md §9）；"
                        "knots_median = 逐锚点中位值连线（对照，LOO 最坏点劣化，未采纳）",
            },
        }
    result["dropped_points"] = {row["material"]: row["dropped"] for row in rows
                                if any(row["dropped"].values())}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细写入 {args.out}")
    if result["dropped_points"]:
        print("\n⚠ 单调化剔除的异常点（低质量值端凹陷）：")
        for m, d in result["dropped_points"].items():
            for c, lst in d.items():
                if lst:
                    print(f"  {m} {c}: {lst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
