#!/usr/bin/env python3
"""_extend_points.py —— 通用增量补扫：向已有 per-material json 追加指定 (codec, 值) 点。

用法：
    EQQ_ENC_THREADS=4 python3 _extend_points.py --work Accessory/temp/hdr_eqq \
        --material doc_grassland_src1280x720 \
        --add "libsvtav1=-crf:36,40,44;libaom-av1=-crf:52,56;librav1e=-qp:120,128"
（点清单可由 _plan_extensions.py 自动生成）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from calibrate_hdr_equal_quality import encode, psnr_hdr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--material", required=True)
    ap.add_argument("--add", required=True,
                    help='"libsvtav1=-crf:36,40;libaom-av1=-crf:52,56"')
    args = ap.parse_args()

    work, name = Path(args.work), args.material
    jf = work / f"{name}.json"
    rec = json.loads(jf.read_text(encoding="utf-8"))
    W, H = (int(x) for x in rec["wh"].split("x"))
    wd = work / Path(name).stem
    master, ref = wd / "master_hdr.gbrp16le", wd / "master_lossless.mp4"

    changed = False
    for part in args.add.split(";"):
        if not part.strip():
            continue
        codec, rest = part.split("=", 1)
        flag, vals = rest.split(":", 1)
        codec = codec.strip()
        cur = rec["curves"].setdefault(codec, {"flag": flag, "points": []})
        have = {p["value"] for p in cur["points"]}
        for v in (int(x) for x in vals.split(",")):
            if v in have:
                continue
            out = wd / f"{codec}_{flag[1:]}{v}.mp4"
            if not encode(master, W, H, out, codec, flag, v):
                print(f"[{name}] {codec} {flag}{v} 编码失败", flush=True)
                continue
            p = psnr_hdr(out, ref)
            if p is None:
                print(f"[{name}] {codec} {flag}{v} PSNR 失败", flush=True)
                continue
            cur["points"].append({"value": v, "psnr": round(p, 3),
                                  "kb": round(out.stat().st_size / 1024, 1)})
            print(f"[{name}] {codec} {flag}{v} PSNR {p:.3f}  "
                  f"{out.stat().st_size / 1024:.1f}KB", flush=True)
            changed = True
    if changed:
        jf.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{name}] ↳ 已更新 {jf.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
