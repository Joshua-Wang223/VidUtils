#!/usr/bin/env python3
"""_anchor_targets.py —— 为每个素材补测 libx265 在多个 crf-ref 锚点上的**目标** PSNR。

原理（本轮最重要发现，已实测验证）：
    等质量点 V_ref = 曲线插值( PSNR(codec, value) 曲线, PSNR_x265@ref )
    而 **PSNR(codec, value) 曲线与锚点无关**（它是编码器在该素材母版上的固有性质），
    所以**换锚点只需重测目标**，完全不必重扫曲线。3 素材 × 3 编码器交叉验证：
      doc_grassland  svtav1 Δ+0.04 / aom Δ+0.47 / rav1e Δ-0.18
      live_kids_play svtav1 预测 8.6（网格没测到）/ aom Δ+0.42 / rav1e Δ-0.10
      tv_bbc_molly  svtav1 Δ+0.02 / aom Δ+0.06 / rav1e Δ-0.52

⚠ 目标必须用 libx265 在**该 ref 下真正下发的值**（非恒等映射：1.0979*ref-2.3119，
   ref21 → 20.744），否则锚点本身有偏。

用法（每素材一个进程，10 个并行）：
    EQQ_ENC_THREADS=4 python3 _anchor_targets.py --work Accessory/temp/hdr_eqq \
        --material doc_grassland_src1280x720
写入 {material}.json 的 "anchors"：{"18": {"crf_sent": 17.4503, "psnr": ..., "kb": ...}, ...}
冗字段 target_psnr（旧，x265@21.0）保留作交叉核对。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from calibrate_hdr_equal_quality import TARGET_CODEC, encode, psnr_hdr  # noqa: E402
from convert_crf import from_x264_crf  # noqa: E402

REFS = (18, 21, 24, 27, 30)
CODECS = ("libsvtav1", "libaom-av1", "librav1e")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--material", required=True, help="素材 stem（对应 {stem}.json）")
    ap.add_argument("--refs", default=",".join(map(str, REFS)))
    args = ap.parse_args()

    work, name = Path(args.work), args.material
    jf = work / f"{name}.json"
    rec = json.loads(jf.read_text(encoding="utf-8"))
    W, H = (int(x) for x in rec["wh"].split("x"))
    wd = work / Path(name).stem
    master, anchor_mp4 = wd / "master_hdr.gbrp16le", wd / "master_lossless.mp4"
    anchors = rec.setdefault("anchors", {})

    for ref in (int(x) for x in args.refs.split(",")):
        key = str(ref)
        if anchors.get(key, {}).get("psnr"):
            print(f"[{name}] ref{ref} 已有，跳过", flush=True)
            continue
        crf = round(float(from_x264_crf(TARGET_CODEC, ref)), 4)
        t0 = time.time()
        out = wd / f"libx265_ref{ref}.mp4"
        if not encode(master, W, H, out, TARGET_CODEC, "-crf", crf):
            print(f"[{name}] ref{ref} 编码失败", flush=True)
            continue
        p = psnr_hdr(out, anchor_mp4)
        if p is None:
            print(f"[{name}] ref{ref} PSNR 失败", flush=True)
            continue
        anchors[key] = {"crf_sent": crf, "psnr": round(p, 3),
                        "kb": round(out.stat().st_size / 1024, 1)}
        cov = []
        for codec in CODECS:
            pts = rec.get("curves", {}).get(codec, {}).get("points", [])
            if not pts:
                cov.append(f"{codec}:无曲线")
                continue
            lo = min(x["psnr"] for x in pts)
            hi = max(x["psnr"] for x in pts)
            cov.append(f"{codec}:{'IN' if lo <= p <= hi else f'OUT{lo - p:+.2f}'}")
        print(f"[{name}] ref{ref} x265-crf={crf} PSNR={p:.3f} "
              f"{' '.join(cov)}  ({time.time() - t0:.0f}s)", flush=True)
        jf.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
