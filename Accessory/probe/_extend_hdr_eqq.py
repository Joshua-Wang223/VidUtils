#!/usr/bin/env python3
"""补扫：在已产出的母版/无损锚之上，只编码 GRID 中尚未存在的点，增量扩展曲线。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from calibrate_hdr_equal_quality import (GRID, SETPARAMS, encode, psnr_hdr)  # noqa: E402

WORK = ROOT / "Accessory" / "temp" / "hdr_eqq"
EXTEND = {
    "libsvtav1": ("-crf", [4, 8]),                 # 低端可能被用到的更低质量
    "libaom-av1": ("-crf", [36, 40, 44, 48]),      # 高质量端延伸
    "librav1e": ("-qp", [80, 88, 96, 104, 112]),   # 高质量端延伸
}

for jf in sorted(WORK.glob("*.json")):
    if jf.name == "aggregate.json":
        continue
    rec = json.loads(jf.read_text(encoding="utf-8"))
    work = WORK / Path(rec["material"]).stem
    master = work / "master_hdr.gbrp16le"
    ref = work / "master_lossless.mp4"
    if not master.exists() or not ref.exists():
        print(f"跳过 {rec['material']}（母版/锚不在盘上）")
        continue
    W, H = (int(x) for x in rec["wh"].split("x"))
    changed = False
    for codec, (flag, vals) in EXTEND.items():
        have = {p["value"] for p in rec["curves"].get(codec, {}).get("points", [])}
        add = [v for v in vals if v not in have]
        if not add:
            continue
        pts = rec["curves"].setdefault(codec, {"flag": flag, "points": []})["points"]
        for v in add:
            out = work / f"{codec}_{flag[1:]}{v}.mp4"
            if not encode(master, W, H, out, codec, flag, v):
                print(f"  [{rec['material'][:24]}] {codec} {flag}{v} 编码失败")
                continue
            p = psnr_hdr(out, ref)
            if p is None:
                continue
            pts.append({"value": v, "psnr": round(p, 3),
                        "kb": round(out.stat().st_size / 1024, 1)})
            changed = True
            print(f"  [{rec['material'][:24]}] {codec} {flag}{v:<4d} "
                  f"PSNR {p:7.3f}  {out.stat().st_size/1024:7.1f}KB", flush=True)
    if changed:
        jf.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ↳ 已更新 {jf.name}")