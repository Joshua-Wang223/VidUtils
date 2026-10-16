#!/usr/bin/env python3
"""Synthetic validation of bench_driver.phase_report math (does NOT touch real progress)."""
import json, os, sys, shutil

sys.path.insert(0, "/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early/libx265_work")
import bench_driver as bd

TMP = "/tmp/synth_report"
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
bd.OUTROOT = TMP
bd.PROG_ENC = os.path.join(TMP, "progress_encode.jsonl")
bd.PROG_VMAF = os.path.join(TMP, "progress_vmaf.jsonl")
bd.RUN_META = os.path.join(TMP, "run_meta.json")
bd.POOL_PROBE = os.path.join(TMP, "pool_probe.json")
json.dump({"machine": {}, "ffmpeg": {}, "x265": {}, "libvmaf": {}, "pool": [],
           "encode_cmd_template": "", "concurrency": {}}, open(bd.POOL_PROBE, "w"))


def build(savings=0.04, time_ratio=1.2, unreachable_material=False, out_prefix=""):
    cells = bd.build_cells()
    tags = []
    for sub, name in bd.FILES:
        tags.append("%s_%s" % (sub, name[:-4]))
    enc_lines, vmaf_lines = [], []
    for c in cells:
        mi = tags.index(c["tag"])
        # geometric bitrate ladder per material (crf 18..30)
        base = 350.0 * (1.0 + 0.37 * mi)
        kbps = base * (2.05 ** (bd.ANCHORS.index(c["crf"])))
        if c["preset"] == "slower":
            kbps *= (1.0 - savings)
        # VMAF ladder: slow = 84 + 3.4*idx (perfectly linear in log10(kbps))
        idx = bd.ANCHORS.index(c["crf"])
        if unreachable_material and mi == 0:
            vmaf = 80.0 + 1.6 * idx  # max 86.4 -> cannot reach 95
        else:
            vmaf = 84.0 + 3.4 * idx
        rec = dict(c)
        rec.update({"rc_run2": 0, "rc_run1": 0, "t_run1_s": 10.0, "t_run2_s": 10.0,
                    "cpu_run1_s": 100.0, "cpu_run2_s": 100.0,
                    "md5_run1": "a", "md5_run2": "a", "md5_match": True,
                    "size_bytes": int(kbps * 1000 * 6.0 / 8), "duration_s": 6.0,
                    "nb_frames": 180, "fps": 30.0, "avg_fps": 30.0,
                    "duration_frames_s": 6.0, "duration_container_s": 6.0,
                    "duration_check_ok": True, "bitrate_kbps": kbps})
        if c["preset"] == "slower":
            rec["t_run2_s"] = 10.0 * time_ratio
            rec["cpu_run2_s"] = 100.0 * time_ratio
        enc_lines.append(rec)
        v = {"key": c["key"], "tag": c["tag"], "crf": c["crf"], "preset": c["preset"],
             "vmaf": vmaf, "vmaf_min": vmaf - 1, "vmaf_max": vmaf + 1,
             "vmaf_frame_mean": vmaf, "n_frames_scored": 180,
             "vmaf_log": c["vmaf_log"], "libvmaf_log_version": "3.2.0"}
        vmaf_lines.append(v)
    with open(bd.PROG_ENC, "w") as f:
        for r in enc_lines:
            f.write(json.dumps(r) + "\n")
    with open(bd.PROG_VMAF, "w") as f:
        for r in vmaf_lines:
            f.write(json.dumps(r) + "\n")
    return len(enc_lines)


def run_case(name, **kw):
    n = build(**kw)
    print("=== case %s (cells=%d)" % (name, n))
    bd.phase_report(partial=False)
    rep = json.load(open(os.path.join(TMP, "libx265_ab.json")))
    s = rep["summary"]
    print("    decision=%s savings=%.4f time_cost=%.3f br_geo=%.4f gain_med=%.3f n_used=%d below95=%s"
          % (s["decision"], s["bitrate_savings_at_matched_vmaf"], s["time_cost"],
             s["bitrate_ratio_geo_B_over_A"], s["vmaf_gain_at_matched_bitrate_median"],
             s["n_materials_used"], s["materials_below_vmaf95"]))
    print("    results=%d outliers_best=%s worst=%s loo=%s stable=%s"
          % (len(rep["results"]), s["outliers"]["best_material"]["tag"][:30],
             s["outliers"]["worst_material"]["tag"][:30],
             {k: v["decision"] for k, v in s["leave_one_out"].items() if isinstance(v, dict)},
             s["leave_one_out"].get("decision_stable")))
    print("    caveats (%d):" % len(rep["caveats"]))
    for c in rep["caveats"]:
        print("      - %s" % c[:150])
    return s


# ground truth: savings 4% (ratio .96), time 1.2x -> worth_it
s1 = run_case("worth_it", savings=0.04, time_ratio=1.2)
assert s1["decision"] == "worth_it", s1["decision"]
assert abs(s1["bitrate_ratio_geo_B_over_A"] - 0.96) < 1e-6
assert abs(s1["time_ratio_geo_B_over_A"] - 1.2) < 1e-9
assert abs(s1["vmaf_gain_at_matched_bitrate_median"] - 10.9 * (0.04 / 2.302585 - 0.5 * (0.04 / 2.302585) ** 2)) < 0.02  # a*log10(1/0.96) with a~10.9
assert len(json.load(open(os.path.join(TMP, "libx265_ab.json")))["results"]) == 170

# savings 4%, time 8x -> inconclusive (mixed)
s2 = run_case("inconclusive_mixed", savings=0.04, time_ratio=8.0)
assert s2["decision"] == "inconclusive", s2["decision"]

# savings 2%, time 8x -> not_worth_it
s3 = run_case("not_worth_it", savings=0.02, time_ratio=8.0)
assert s3["decision"] == "not_worth_it", s3["decision"]
assert abs(s3["bitrate_savings_at_matched_vmaf"] - 0.02) < 1e-9

# one material unreachable at 95 -> caveat + target lowered for it
s4 = run_case("unreachable95", savings=0.04, time_ratio=1.2, unreachable_material=True)
assert s4["decision"] == "worth_it"
assert len(s4["materials_below_vmaf95"]) == 1
assert any("cannot reach VMAF 95" in c for c in
           json.load(open(os.path.join(TMP, "libx265_ab.json")))["caveats"])

print("ALL SYNTHETIC CASES PASSED")
