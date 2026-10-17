#!/usr/bin/env python3
"""_fulllen_calib.py —— B「全长重标定」分片驱动器（§10.2 裁定 C+B）。

对每个素材：
  1) 复用校验阶段的全长母版 hdr_eqq_full/<stem>/master_hdr.gbrp16le（字节 = 校验对象，不再建）
  2) 无损锚复用 master_lossless_full.mp4（硬链接为 master_lossless.mp4，供 _anchor_targets.py 使用）
  3) 按 20 帧标定 json 的**同一点集**全长编码（产物存在即跳过 ⇒ 可断点续跑；PSNR 全部现算）
  4) 写 hdr_eqq_full/<stem>.json（`_final_fit.py` 兼容格式：curves + target_psnr；anchors 之后由
     `_anchor_targets.py` 追加）

用法（24 核建议 5 分片 × EQQ_ENC_THREADS=4）：
  EQQ_ENC_THREADS=4 python3 Accessory/probe/_fulllen_calib.py --shard 1/5
后续（逐素材，可并行）：
  EQQ_ENC_THREADS=4 python3 Accessory/probe/_anchor_targets.py --work Accessory/temp/hdr_eqq_full \
      --material <stem> --refs 15,18,21,24,27,30
  python3 Accessory/probe/_final_fit.py --work Accessory/temp/hdr_eqq_full --out <fit.json>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from calibrate_hdr_equal_quality import (TARGET_CODEC, TARGET_CRF, encode,  # noqa: E402
                                         psnr_hdr)

EQ = ROOT / "Accessory" / "temp" / "hdr_eqq"          # 20 帧标定（网格/口径来源）
FULL = ROOT / "Accessory" / "temp" / "hdr_eqq_full"   # 全长产物（母版复用）
CODECS = ("libsvtav1", "libaom-av1", "librav1e")


def ensure_lossless(wd: Path, master: Path, W: int, H: int) -> Path:
    ref = wd / "master_lossless.mp4"
    if ref.exists() and ref.stat().st_size > 0:
        return ref
    src = wd / "master_lossless_full.mp4"
    if src.exists() and src.stat().st_size > 0:
        try:
            os.link(src, ref)
        except OSError:
            shutil.copy2(src, ref)
        print("[link] master_lossless_full.mp4 -> master_lossless.mp4", flush=True)
        return ref
    if not encode(master, W, H, ref, "libx265", "-qp", 0):
        raise RuntimeError("无损锚编码失败")
    return ref


def run_stem(stem: str) -> dict:
    j20 = json.loads((EQ / f"{stem}.json").read_text(encoding="utf-8"))
    wd = FULL / stem
    wd.mkdir(parents=True, exist_ok=True)
    master = wd / "master_hdr.gbrp16le"
    if not (master.exists() and master.stat().st_size > 0):
        raise RuntimeError(f"缺少全长母版：{master}（需先完成 C 补测）")
    W, H = (int(x) for x in j20["wh"].split("x"))
    n = master.stat().st_size // (W * H * 3 * 2)
    print(f"[{stem}] 全长母版 {n} 帧 {W}x{H}（复用）", flush=True)
    ref = ensure_lossless(wd, master, W, H)

    rec: dict = {"material": f"{stem}.mp4", "frames": n, "wh": f"{W}x{H}",
                 "lossless_kb": round(ref.stat().st_size / 1024, 1), "curves": {},
                 "note": "B 全长重标定：母版=校验同源复用；网格=20 帧标定同点集；"
                         "度量=psnr 滤镜(10bit、不 tonemap)"}

    tgt = wd / f"{TARGET_CODEC}_crf{TARGET_CRF}.mp4"
    if not (tgt.exists() and tgt.stat().st_size > 0) and \
            not encode(master, W, H, tgt, TARGET_CODEC, "-crf", TARGET_CRF):
        raise RuntimeError("目标档编码失败")
    p = psnr_hdr(tgt, ref)
    if p is None:
        raise RuntimeError("目标档 PSNR 失败")
    rec["target_psnr"] = p
    rec["target_kb"] = round(tgt.stat().st_size / 1024, 1)
    print(f"[{stem}] 目标 {TARGET_CODEC} crf{TARGET_CRF}: PSNR {p:.3f}", flush=True)

    t0 = time.time()
    for codec in CODECS:
        cur20 = j20["curves"][codec]
        flag = cur20["flag"]
        curve = []
        for v in sorted(p_["value"] for p_ in cur20["points"]):
            out = wd / f"{codec}_{flag[1:]}{v}.mp4"
            if not (out.exists() and out.stat().st_size > 0):
                if not encode(master, W, H, out, codec, flag, v):
                    print(f"[{stem}] {codec} {flag}{v} 编码失败（跳过）", flush=True)
                    continue
            pp = psnr_hdr(out, ref)
            if pp is None:
                print(f"[{stem}] {codec} {flag}{v} PSNR 失败（跳过）", flush=True)
                continue
            curve.append({"value": v, "psnr": round(pp, 3),
                          "kb": round(out.stat().st_size / 1024, 1)})
            print(f"[{stem}] {codec} {flag}{v:<3d} PSNR {pp:7.3f} "
                  f"{out.stat().st_size / 1024:7.1f}KB  ({time.time()-t0:.0f}s)", flush=True)
        rec["curves"][codec] = {"flag": flag, "points": curve}

    (FULL / f"{stem}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"[{stem}] ↳ 写出 {FULL/stem}.json  ({time.time()-t0:.0f}s)", flush=True)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="B 全长重标定（分片驱动器）")
    ap.add_argument("--shard", help="i/N：本次只跑第 i 片（1 起）")
    ap.add_argument("--materials", help="逗号分隔（默认=eqq_calib 有 20f json 的全部素材）")
    args = ap.parse_args()

    if args.materials:
        stems = [x.strip() for x in args.materials.split(",") if x.strip()]
    else:
        stems = sorted(p.stem for p in EQ.glob("*.json") if p.name != "aggregate.json")
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        stems = stems[i - 1::n]
    print(f"= 本片素材（{len(stems)}）：{', '.join(stems)}", flush=True)
    for stem in stems:
        try:
            run_stem(stem)
        except Exception as e:  # noqa: BLE001 —— 单素材失败不拖垮整片
            print(f"[{stem}] ✗ {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
