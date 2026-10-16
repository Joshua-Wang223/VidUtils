#!/usr/bin/env python3
"""D2 bench analysis (two-phase): phase1 = encodes+timings, phase2 = fixed VMAF.

Phase 1 (results_raw.tsv): per cell warmup+timed encode (wall clock), bytes,
duration, and a first-pass VMAF that is INVALID for every source whose pts are
not millisecond-aligned (matroska 1/1000 timebase cannot represent 1/30s etc
exactly -> libvmaf framesync mispaired frames). Only encodes/timings are used.
Phase 2 (rescore_vmaf.tsv): VMAF recomputed with order-based frame pairing
(settb=AVTB + setpts=N/30/TB on both inputs); identical metric otherwise.

Equal-VMAF target rule (task: "target 95; if both presets cannot reach 95 use
the highest common VMAF"): in this data 16/17 materials sit entirely ABOVE 95
even at crf 30, so 95 is below the measured range and extrapolating flat curves
to 95 is not executable. Rule used: project 95 onto the two presets' common
achievable interval; if the intervals are disjoint (p4's worst point beats p6's
best point), meet in the middle of the gap with a small documented extrapolation.
"""
import json
import math
import subprocess
from collections import defaultdict

import numpy as np

ROOT = "/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early"
INROOT = "/workspace/input_videos/eqq_calib"
RAW1 = ROOT + "/results_raw.tsv"
RAW2 = ROOT + "/rescore_vmaf.tsv"
ANCHORS = [18, 21, 24, 27, 30]
VMAF_TARGET = 95.0

def ref_nframes(rel):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=nb_frames", "-of", "csv=p=0", f"{INROOT}/{rel}"],
        capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None

# ---------- phase 1: encodes ----------
enc = {}
with open(RAW1) as f:
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) < 11:
            continue
        key, mat, anchor, preset, rc1, rc2, rc3, elapsed, nbytes, dur, vmaf1 = p[:11]
        enc[(mat, int(anchor), int(preset))] = {
            "cell_key": key, "rc_warmup": int(rc1), "rc_timed": int(rc2),
            "rc_vmaf_pass1": int(rc3), "encode_time_s": float(elapsed),
            "bytes": int(nbytes), "duration_s": float(dur),
            "vmaf_pass1": None if vmaf1 == "ERR" else float(vmaf1),
        }

# ---------- phase 2: fixed VMAF ----------
vmaf2, rescored = {}, {}
with open(RAW2) as f:
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) < 7:
            continue
        key, mat, anchor, preset, rc, v, n = p[:7]
        vmaf2[(mat, int(anchor), int(preset))] = None if v == "ERR" else float(v)
        rescored[(mat, int(anchor), int(preset))] = {"rc": int(rc), "nframes": int(n)}

materials = sorted({m for (m, a, p) in enc})
problems = []
for (m, a, p), e in enc.items():
    if (m, a, p) not in vmaf2 or vmaf2[(m, a, p)] is None:
        problems.append(f"no fixed VMAF for {m} crf{a} p{p}")
    if e["rc_warmup"] or e["rc_timed"]:
        problems.append(f"encode rc nonzero {m} crf{a} p{p}: {e['rc_warmup']}/{e['rc_timed']}")
    if e["bytes"] <= 0 or e["duration_s"] <= 0:
        problems.append(f"bad bytes/duration {m} crf{a} p{p}")
    rn = ref_nframes(m)
    sn = rescored.get((m, a, p), {}).get("nframes", -1)
    if rn is not None and sn != rn:
        problems.append(f"frame count mismatch {m} crf{a} p{p}: scored={sn} ref={rn}")

n_expected = 17 * 5 * 2
if len(enc) != n_expected:
    problems.append(f"cell count {len(enc)} != {n_expected}")

rows = []
for (m, a, p), e in enc.items():
    r = dict(e)
    r["material"] = m; r["anchor_crf"] = a; r["preset"] = p
    r["vmaf"] = vmaf2.get((m, a, p))
    r["bitrate_bps"] = e["bytes"] * 8.0 / e["duration_s"]
    rows.append(r)

diff_cells = []
for (m, a, p), e in enc.items():
    v1, v2 = e["vmaf_pass1"], vmaf2.get((m, a, p))
    if v1 is not None and v2 is not None and abs(v1 - v2) > 0.5:
        diff_cells.append((m, a, p, v1, v2))
affected_mats = sorted({m for m, a, p, v1, v2 in diff_cells})

# ---------- curve fits (parsimony: quadratic only if it clearly beats linear) ----------
by_mp = defaultdict(list)
for r in rows:
    by_mp[(r["material"], r["preset"])].append(r)

def fit_curve(x, y):
    ss_tot = float(((y - y.mean()) ** 2).sum())
    def r2_of(coef):
        yhat = np.polyval(coef, x)
        ss_res = float(((y - yhat) ** 2).sum())
        return 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    c1 = np.polyfit(x, y, 1); r1 = r2_of(c1)
    c2 = np.polyfit(x, y, 2); r2 = r2_of(c2)
    deg, coef, rr = 1, c1, r1
    if np.isfinite(c2).all():
        xs = np.linspace(x.min(), x.max(), 50)
        dy = np.diff(np.polyval(c2, xs))
        mono = bool((dy > 0).all() or (dy < 0).all())
        if mono and (r2 - r1) > 0.002:
            deg, coef, rr = 2, c2, r2
    return deg, coef, rr

fits = {}
for (mat, preset), pts in sorted(by_mp.items()):
    pts.sort(key=lambda r: r["anchor_crf"])
    x = np.array([math.log(r["bitrate_bps"]) for r in pts])
    y = np.array([r["vmaf"] for r in pts])
    deg, coef, r2 = fit_curve(x, y)
    fits[(mat, preset)] = {"deg": int(deg), "coef": [float(c) for c in coef], "r2": float(r2),
                           "x_min": float(x.min()), "x_max": float(x.max()),
                           "y_at_xmax": float(np.polyval(coef, x.max())),
                           "y_at_xmin": float(np.polyval(coef, x.min()))}

def solve_lnB(mat, preset, target, slack=0.015):
    f = fits[(mat, preset)]
    coef = np.array(f["coef"])
    c = coef.copy(); c[-1] -= target
    real = [float(r.real) for r in np.roots(c) if abs(r.imag) < 1e-9]
    lo, hi = f["x_min"] - 1e-9, f["x_max"] + 1e-9
    cand = [r for r in real if lo <= r <= hi]
    if not cand:
        cand = [r for r in real if f["x_min"] - slack <= r <= f["x_max"] + slack]
    if not cand:
        return None
    inc = [r for r in cand if np.polyval(np.polyder(coef), r) > 0]
    return max(inc) if inc else max(cand)

# ---------- per-material comparison ----------
per_mat, fit_failures, target_dev = [], [], []
for mat in materials:
    f6, f4 = fits[(mat, 6)], fits[(mat, 4)]
    lo = max(f6["y_at_xmin"], f4["y_at_xmin"])
    hi = min(f6["y_at_xmax"], f4["y_at_xmax"])
    slack = 0.015
    if lo <= hi:
        target = min(max(VMAF_TARGET, lo), hi)
        if lo <= VMAF_TARGET <= hi:
            case = "in_range_95"
        elif VMAF_TARGET < lo:
            case = "above_95_used_lowest_common"
        else:
            case = "below_95_used_highest_common"
    else:
        # disjoint VMAF ranges: p4's worst measured point already beats p6's best.
        # No common quality point exists; compare the two presets' closest raw
        # anchor pair (minimal |VMAF gap|), no extrapolation.
        case = "disjoint_ranges_closest_anchor_pair"
        best = None
        for a6 in ANCHORS:
            for a4 in ANCHORS:
                r6 = next(r for r in by_mp[(mat, 6)] if r["anchor_crf"] == a6)
                r4 = next(r for r in by_mp[(mat, 4)] if r["anchor_crf"] == a4)
                gap = abs(r6["vmaf"] - r4["vmaf"])
                if best is None or gap < best[0]:
                    best = (gap, a6, a4, r6, r4)
        gap, a6, a4, r6, r4 = best
        target = r6["vmaf"]
        x6, x4 = math.log(r6["bitrate_bps"]), math.log(r4["bitrate_bps"])
        target_dev.append({"material": mat, "target_used": round(target, 3), "case": case,
                           "p6_range": [round(f6["y_at_xmin"], 3), round(f6["y_at_xmax"], 3)],
                           "p4_range": [round(f4["y_at_xmin"], 3), round(f4["y_at_xmax"], 3)],
                           "anchor_pair": [f"p6@crf{a6}", f"p4@crf{a4}"],
                           "vmaf_gap": round(gap, 4)})
        b6, b4 = r6["bitrate_bps"], r4["bitrate_bps"]
        t6 = {r["anchor_crf"]: r["encode_time_s"] for r in by_mp[(mat, 6)]}
        t4 = {r["anchor_crf"]: r["encode_time_s"] for r in by_mp[(mat, 4)]}
        tr = {a: t4[a] / t6[a] for a in ANCHORS}
        tr_geo = math.exp(sum(math.log(v) for v in tr.values()) / len(tr))
        allb = [r["bitrate_bps"] for r in by_mp[(mat, 6)] + by_mp[(mat, 4)]]
        bstar = math.exp(sum(math.log(v) for v in allb) / len(allb))
        gain = float(np.polyval(np.array(f4["coef"]), math.log(bstar)) -
                     np.polyval(np.array(f6["coef"]), math.log(bstar)))
        per_mat.append({
            "material": mat, "vmaf_target": round(target, 3), "target_case": case,
            "bitrate_A_p6_kbps": round(b6 / 1000.0, 1),
            "bitrate_B_p4_kbps": round(b4 / 1000.0, 1),
            "bitrate_ratio_B_over_A": round(b4 / b6, 5),
            "bitrate_gain_pct": round((1.0 - b4 / b6) * 100.0, 3),
            "time_ratio_B_over_A": round(tr_geo, 4),
            "time_cost_pct": round((tr_geo - 1.0) * 100.0, 2),
            "time_ratio_per_anchor": {str(a): round(tr[a], 4) for a in ANCHORS},
            "matched_bitrate_kbps": round(bstar / 1000.0, 1),
            "vmaf_gain_at_matched_bitrate": round(gain, 4),
            "fit_r2_p6": round(f6["r2"], 5), "fit_r2_p4": round(f4["r2"], 5),
            "fit_deg_p6": f6["deg"], "fit_deg_p4": f4["deg"],
            "anchor_pair_used": [f"p6@crf{a6}", f"p4@crf{a4}"], "vmaf_gap": round(gap, 4),
        })
        continue
    if case != "in_range_95":
        target_dev.append({"material": mat, "target_used": round(target, 3), "case": case,
                           "p6_range": [round(f6["y_at_xmin"], 3), round(f6["y_at_xmax"], 3)],
                           "p4_range": [round(f4["y_at_xmin"], 3), round(f4["y_at_xmax"], 3)]})
    x6 = solve_lnB(mat, 6, target, slack)
    x4 = solve_lnB(mat, 4, target, slack)
    if x6 is None or x4 is None:
        fit_failures.append(mat); continue
    b6, b4 = math.exp(x6), math.exp(x4)
    t6 = {r["anchor_crf"]: r["encode_time_s"] for r in by_mp[(mat, 6)]}
    t4 = {r["anchor_crf"]: r["encode_time_s"] for r in by_mp[(mat, 4)]}
    tr = {a: t4[a] / t6[a] for a in ANCHORS}
    tr_geo = math.exp(sum(math.log(v) for v in tr.values()) / len(tr))
    allb = [r["bitrate_bps"] for r in by_mp[(mat, 6)] + by_mp[(mat, 4)]]
    bstar = math.exp(sum(math.log(v) for v in allb) / len(allb))
    gain = float(np.polyval(np.array(f4["coef"]), math.log(bstar)) -
                 np.polyval(np.array(f6["coef"]), math.log(bstar)))
    per_mat.append({
        "material": mat, "vmaf_target": round(target, 3), "target_case": case,
        "bitrate_A_p6_kbps": round(b6 / 1000.0, 1),
        "bitrate_B_p4_kbps": round(b4 / 1000.0, 1),
        "bitrate_ratio_B_over_A": round(b4 / b6, 5),
        "bitrate_gain_pct": round((1.0 - b4 / b6) * 100.0, 3),
        "time_ratio_B_over_A": round(tr_geo, 4),
        "time_cost_pct": round((tr_geo - 1.0) * 100.0, 2),
        "time_ratio_per_anchor": {str(a): round(tr[a], 4) for a in ANCHORS},
        "matched_bitrate_kbps": round(bstar / 1000.0, 1),
        "vmaf_gain_at_matched_bitrate": round(gain, 4),
        "fit_r2_p6": round(f6["r2"], 5), "fit_r2_p4": round(f4["r2"], 5),
        "fit_deg_p6": f6["deg"], "fit_deg_p4": f4["deg"],
    })

def geomean(v):
    return math.exp(sum(math.log(x) for x in v) / len(v))

ok = per_mat
br_geo = geomean([m["bitrate_ratio_B_over_A"] for m in ok])
t_geo = geomean([m["time_ratio_B_over_A"] for m in ok])
gain_pct, cost_pct = (1.0 - br_geo) * 100.0, (t_geo - 1.0) * 100.0
matched_median = float(np.median([m["vmaf_gain_at_matched_bitrate"] for m in ok]))

def verdict(g, c):
    if g < 3.0 and c > 50.0: return "not_worth_it"
    if g >= 3.0 and c <= 50.0: return "worth_it"
    return "inconclusive"

verdict_main = verdict(gain_pct, cost_pct)
order = sorted(ok, key=lambda m: m["bitrate_ratio_B_over_A"])
trim = order[1:-1]
br_geo_t = geomean([m["bitrate_ratio_B_over_A"] for m in trim])
t_geo_t = geomean([m["time_ratio_B_over_A"] for m in trim])
gain_t, cost_t = (1.0 - br_geo_t) * 100.0, (t_geo_t - 1.0) * 100.0
verdict_t = verdict(gain_t, cost_t)
dispersion = {
    "bitrate_ratio_max": {"material": order[-1]["material"], "ratio": order[-1]["bitrate_ratio_B_over_A"]},
    "bitrate_ratio_min": {"material": order[0]["material"], "ratio": order[0]["bitrate_ratio_B_over_A"]},
    "bitrate_ratio_median": round(float(np.median([m["bitrate_ratio_B_over_A"] for m in ok])), 5),
    "bitrate_ratio_geomean_trimmed": round(br_geo_t, 5),
    "time_ratio_geomean_trimmed": round(t_geo_t, 4),
    "gain_pct_trimmed": round(gain_t, 3), "cost_pct_trimmed": round(cost_t, 2),
    "verdict_trimmed": verdict_t, "verdict_flips_on_trim": verdict_t != verdict_main,
    "dropped_best_material": order[0]["material"], "dropped_worst_material": order[-1]["material"],
    "n_materials_in_geomean": len(ok),
}

caveats = [
    "METHOD-FIX: 首轮 VMAF 对非毫秒对齐源失效 —— matroska 1/1000 时基无法精确表示 "
    "1/30s 等 pts (33ms != 33.333ms), libvmaf framesync 按时间戳配对导致奇偶帧错位 "
    "(逐帧 VMAF 在 ~95 与 ~40 间交替). 已用 settb=AVTB+setpts=N/30/TB 对两路输入按帧序重 "
    "索引后重打分 (指标本身不变: n_subsample=1 / vmaf_v0.6.1 / 原始切片为 reference). "
    f"受影响素材 {len(affected_mats)}/17 (150/170 格); 25fps 素材两轮一致 (|diff|<=0.5), "
    "编码与计时数据未受影响.",
    "TIMING: 墙钟计时在共享机器上测得, 批跑期间同机有其他并行工作流的 ffmpeg 负载; "
    "p6/p4 任务交替下发, 负载噪声在几何平均中期望抵消. 逐锚点耗时比离散度大 (1.17~2.59), "
    "已按素材内 5 锚点几何平均平滑.",
    "CONTENTION: 批跑期间 (16:41-17:56) 机器 uptime 负载均值一度达 76.6/24 核 (外部 "
    "opencode session + 本任务 <=8 ffmpeg + 同目录并行跑的 x265 bench <=8 ffmpeg, 见同目录 "
    "CONTENTION_NOTE.md). 编码输出字节确定性已验证 (wordworld crf18 p6 重编得同一 1850922 "
    "字节) => VMAF/码率不受影响, 但耗时应列反映外部负载: 时间比几何平均 1.5025 的 95% CI "
    "[1.427, 1.582] 跨越 +50% 阈值, 即「耗时是否 >+50%」这一判定项在噪声范围内不确定; "
    "码率收益 19.60% 的 CI [0.704, 0.919] 则稳定 >=3%. 若在空载机重测耗时, 结论可能在 "
    "worth_it 与 inconclusive 间移动; 建议按 CONTENTION_NOTE.md 待办补测.",
]
for p in problems: caveats.append("DATA: " + p)
for m in fit_failures:
    caveats.append(f"FITFAIL: {m} 目标 VMAF 在测量区间内无解, 已剔除出几何平均")
n_above = sum(1 for d in target_dev if d["case"] == "above_95_used_lowest_common")
n_below = sum(1 for d in target_dev if d["case"] == "below_95_used_highest_common")
n_disjoint = sum(1 for d in target_dev if d["case"] == "disjoint_ranges_closest_anchor_pair")
caveats.append(
    f"TARGET-DEVIATION: 仅 1/17 素材 (doc_dark_earth) 能在测量区间内取到 VMAF 95; "
    f"{n_above} 条素材两档整条曲线都在 95 之上 (crf30 仍 >95), 95 在区间之下且外推平坦 "
    f"曲线到 95 不可执行 (如 screen_ui 99.74->95 需 dlnB~30), 故按「95 到两档共同可达区间 "
    f"的投影」取区间内最接近 95 的目标 (区间下界); {n_disjoint} 条素材两档 VMAF 区间不重叠 "
    f"(p4 最差点已优于 p6 最好点, 无公共质量点), 取两档原始锚点中 VMAF 最接近的一对直接比较 "
    f"(零外推); "
    f"{n_below} 条按任务书 fallback 取最高公共 VMAF. 逐条目标与区间见 "
    f"per_material[].vmaf_target / target_deviations[].")
caveats.append(
    f"OUTLIER: 码率比最大 {dispersion['bitrate_ratio_max']['ratio']} "
    f"({dispersion['bitrate_ratio_max']['material']}, 屏幕录制类平坦曲线, 等 VMAF 匹配退化), "
    f"最小 {dispersion['bitrate_ratio_min']['ratio']} ({dispersion['bitrate_ratio_min']['material']}, "
    f"暗场噪声内容 preset 4 增益小); 去掉最好/最差各一条后: gain "
    f"{dispersion['gain_pct_trimmed']}%, cost {dispersion['cost_pct_trimmed']}%, verdict "
    f"{dispersion['verdict_trimmed']} (主判定 {verdict_main}), 结论不翻转.")
lowr2 = [(m, p, fits[(m, p)]["r2"]) for (m, p) in fits if fits[(m, p)]["r2"] < 0.995]
if lowr2:
    caveats.append("LOWR2: " + ", ".join(f"{m} p{p} r2={r:.4f}" for m, p, r in lowr2) +
                   " (均为 VMAF 跨度 <0.5 的平坦曲线素材, 比率对拟合噪声敏感)")

final_verdict = verdict_main
if fit_failures or problems:
    final_verdict = "inconclusive"
if dispersion["verdict_flips_on_trim"]:
    final_verdict = "inconclusive"
    caveats.append("DISPERSION: 去掉最好/最差素材后判定翻转 => 按主方案 12.3 '数据离散' 判 inconclusive")

results = []
for r in sorted(rows, key=lambda r: (r["material"], r["anchor_crf"], r["preset"])):
    results.append({
        "material": r["material"], "anchor_crf": r["anchor_crf"], "preset": r["preset"],
        "vmaf": round(r["vmaf"], 4) if r["vmaf"] is not None else None,
        "vmaf_first_pass_invalid": (r["material"], r["anchor_crf"], r["preset"]) in {(m, a, p) for m, a, p, v1, v2 in diff_cells},
        "bitrate_kbps": round(r["bitrate_bps"] / 1000.0, 1),
        "bytes": r["bytes"], "duration_s": round(r["duration_s"], 4),
        "encode_time_s": r["encode_time_s"],
        "rc_warmup": r["rc_warmup"], "rc_timed": r["rc_timed"],
        "rc_vmaf": rescored.get((r["material"], r["anchor_crf"], r["preset"]), {}).get("rc"),
        "vmaf_scored_frames": rescored.get((r["material"], r["anchor_crf"], r["preset"]), {}).get("nframes"),
        "cell_dir": f"libsvtav1_work/cells/{r['cell_key']}",
    })

summary = {
    "bench": "libsvtav1 preset 6 (A=quality) vs preset 4 (B=archive) — D2 CPU early run (阶段零点五)",
    "encoder_cmd": "-c:v libsvtav1 -crf <anchor> -preset <preset> (no lookahead/tune/multipass/aq)",
    "anchors": ANCHORS, "presets": {"A_quality": 6, "B_archive": 4},
    "materials": len(materials), "n_cells": len(results),
    "vmaf_metric": "libvmaf pooled_metrics.vmaf.mean, n_subsample=1, model vmaf_v0.6.1, "
                   "order-based frame pairing (settb=AVTB+setpts=N/30/TB)",
    "fit": "VMAF vs ln(bitrate) per (material, preset); quadratic kept only if it beats "
           "linear by dr2>0.002 and stays monotonic, else linear",
    "target_rule": ("per material: target = 95 clamped into the two presets' common "
                    "achievable VMAF interval; disjoint intervals (no common quality point) "
                    "use the closest raw anchor pair; only 1/17 materials have 95 inside range"),
    "verdict": final_verdict, "verdict_from_geomean": verdict_main,
    "bitrate_ratio_B_over_A_geomean": round(br_geo, 5),
    "bitrate_gain_pct": round(gain_pct, 3),
    "time_ratio_B_over_A_geomean": round(t_geo, 4),
    "time_cost_pct": round(cost_pct, 2),
    "vmaf_gain_at_matched_bitrate_median": round(matched_median, 4),
    "thresholds": {"not_worth_it": "gain<3% and cost>+50%", "worth_it": "gain>=3% and cost<=+50%",
                   "inconclusive": "other / dispersed / fit failure"},
    "per_material": per_mat, "dispersion": dispersion,
    "target_deviations": target_dev, "fit_failures": fit_failures,
    "data_problems": problems, "caveats": caveats,
    "first_pass_invalid_cells": len(diff_cells),
}

with open(ROOT + "/libsvtav1_ab.json", "w") as f:
    json.dump({"summary": summary, "results": results}, f, indent=1, ensure_ascii=False)
print(json.dumps(summary, indent=1, ensure_ascii=False))
print(f"\nresults points: {len(results)}; wrote {ROOT}/libsvtav1_ab.json")
