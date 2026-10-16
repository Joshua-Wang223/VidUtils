#!/usr/bin/env python3
"""calibrate_hdr_equal_quality.py —— SDR→HDR 管线的等质量标定（母版锚 + HDR 域 PSNR）

## 为什么是这个方法
`bench_sdr_to_hdr.py --vmaf` 的 tonemap→8bit 口径测不出质量档位差异
（三步收敛实验：换锚 −0.020→−0.013、换度量 →+0.005、**换域 →+0.190**）。
本脚本按修正后的方法重做：

  锚   = HDR 母版（模型输出的**无压缩**帧 → libx265 -qp 0 无损编码）
         —— 这是 `eqq_calib` 那套「公共锚，不是编码器互比」原则在 HDR 域的等价物
  度量 = **HDR 域直接 PSNR**（10bit PQ 对 10bit PQ，**不 tonemap**）
  目标 = 找出使 PSNR 与 **libx265 @ crf-ref 21**（现表基准档）相等的原生质量值

## 单素材模式（便于按素材并行）
  python3 calibrate_hdr_equal_quality.py --material doc_grassland_src1280x720.mp4 --frames 20
聚合：
  python3 calibrate_hdr_equal_quality.py --aggregate
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from bench_sdr_to_hdr import _ffmpeg_bin, _run  # noqa: E402
import convert_sdr_to_hdr as C  # noqa: E402

FF = _ffmpeg_bin()
MAT_DIR = ROOT.parent / "input_videos" / "eqq_calib" / "6s"

# 扫掠网格：覆盖现表值两侧，够拟合一条曲线
import os
_G = os.environ.get("EQQ_GRID", "")
GRID = {
    "libsvtav1": ("-crf", [12, 16, 20, 24, 28, 32]),
    "libaom-av1": ("-crf", [12, 16, 20, 24, 28, 32]),
    "librav1e": ("-qp", [24, 32, 40, 48, 56, 64, 72]),
}
if _G:
    # 形如 EQQ_GRID='libsvtav1=-crf:10,14,18,22;libaom-av1=-crf:18,22,26,30'
    for _part in _G.split(";"):
        if not _part.strip():
            continue
        _name, _rest = _part.split("=", 1)
        _flag, _vals = _rest.split(":", 1)
        GRID[_name.strip()] = (_flag, [int(v) for v in _vals.split(",")])
TARGET_CODEC = "libx265"
TARGET_CRF = int(os.environ.get("EQQ_TARGET_CRF", "21"))  # libx265 实际下发的 crf
SETPARAMS = ("setparams=colorspace=bt2020nc:color_primaries=bt2020:"
             "color_trc=smpte2084:range=pc")


def sh(cmd, timeout=3600):
    return _run(cmd, timeout=timeout)


def psnr_hdr(dist: Path, ref: Path) -> float | None:
    """HDR 域 PSNR：两侧都是 10bit PQ，中间不做任何 tonemap。"""
    r = subprocess.run(
        [FF, "-hide_banner", "-i", str(dist), "-i", str(ref),
         "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    m = re.findall(r"average:([0-9.]+)", r.stderr or "")
    if not m:
        return None
    try:
        return float(m[-1])
    except ValueError:
        return None


def build_master(src: Path, work: Path, frames: int) -> tuple[Path, int, int]:
    """产出该素材的 HDR 母版（无压缩 gbrp16le 原始帧）+ 尺寸。"""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(src)],
                         capture_output=True, text=True).stdout.strip()
    W, H = out.split(",")[:2]
    W, H = int(W), int(H)
    dec = subprocess.run(
        [FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
         "-an", "-sn", "-dn", "-frames:v", str(frames), "-vf", "format=gbrp16le",
         "-pix_fmt", "gbrp16le", "-f", "rawvideo", "-"], capture_output=True, timeout=1800)
    if dec.returncode != 0:
        raise RuntimeError(f"解码失败: {dec.stderr[-300:]}")
    fb = W * H * 3 * 2
    n = len(dec.stdout) // fb
    engine = C.HdrTvNetPlus(str(ROOT.parent / "HDRTVNet-plus"), device="cpu",
                            variant="agcm", verbose=False)
    buf = bytearray()
    for i in range(n):
        # ⚠ 必须用被测脚本自己的 raw_to_frame 反 planar（gbrp=[G,B,R] 三张整帧平面）。
        #   手写 reshape 会把三通道错位，母版退化成近纯色（产物仅 1~3KB 即此症状）。
        fr = C.raw_to_frame(dec.stdout[i * fb:(i + 1) * fb], W, H)
        buf += C.frame_to_raw(engine.enhance(fr))
    master = work / "master_hdr.gbrp16le"
    master.write_bytes(bytes(buf))
    return master, W, H


def encode(master: Path, W: int, H: int, out: Path, codec: str,
           flag: str | None, val: int | None) -> bool:
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "rawvideo", "-pixel_format", "gbrp16le",
           "-video_size", f"{W}x{H}", "-framerate", "30", "-i", str(master),
           "-vf", SETPARAMS, "-c:v", codec]
    if flag:
        cmd += [flag, str(val)]
    cmd += ["-pix_fmt", "yuv420p10le", str(out)]
    r = sh(cmd)
    return r.returncode == 0 and out.exists()


def run_material(name: str, frames: int, workroot: Path) -> dict:
    src = MAT_DIR / name
    if not src.exists():
        return {"material": name, "error": "素材不存在"}
    work = workroot / Path(name).stem
    work.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    master, W, H = build_master(src, work, frames)
    n_frames = master.stat().st_size // (W * H * 3 * 2)
    print(f"[{name}] 母版 {n_frames} 帧 {W}x{H}  ({time.time()-t0:.0f}s)", flush=True)

    # 公共锚：母版的无损编码（母版本身是 raw，psnr 滤镜吃不了）
    ref = work / "master_lossless.mp4"
    if not encode(master, W, H, ref, "libx265", "-qp", 0):
        return {"material": name, "error": "无损母版编码失败"}
    print(f"[{name}] 无损锚 {ref.stat().st_size/1024:.0f}KB", flush=True)

    rec: dict = {"material": name, "frames": n_frames, "wh": f"{W}x{H}",
                 "lossless_kb": round(ref.stat().st_size / 1024, 1), "curves": {}}

    # 目标档：libx265 @ crf-ref 21
    tgt = work / f"{TARGET_CODEC}_crf{TARGET_CRF}.mp4"
    if encode(master, W, H, tgt, TARGET_CODEC, "-crf", TARGET_CRF):
        p = psnr_hdr(tgt, ref)
        rec["target_psnr"] = p
        rec["target_kb"] = round(tgt.stat().st_size / 1024, 1)
        print(f"[{name}] 目标 {TARGET_CODEC} crf{TARGET_CRF}: "
              f"PSNR {p}  {tgt.stat().st_size/1024:.0f}KB", flush=True)
    else:
        return {"material": name, "error": "目标档编码失败"}

    for codec, (flag, vals) in GRID.items():
        curve = []
        for v in vals:
            out = work / f"{codec}_{flag[1:]}{v}.mp4"
            if not encode(master, W, H, out, codec, flag, v):
                print(f"[{name}] {codec} {flag}{v} 编码失败", flush=True)
                continue
            p = psnr_hdr(out, ref)
            if p is None:
                continue
            curve.append({"value": v, "psnr": round(p, 3),
                          "kb": round(out.stat().st_size / 1024, 1)})
            print(f"[{name}] {codec} {flag}{v:<3d} PSNR {p:7.3f}  "
                  f"{out.stat().st_size/1024:7.1f}KB", flush=True)
        if curve:
            rec["curves"][codec] = {"flag": flag, "points": curve}

    (workroot / f"{Path(name).stem}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def interpolate(curve: list, target_psnr: float) -> dict:
    """在「质量值 → PSNR」曲线上找等于 target_psnr 的点（线性插值）。"""
    pts = sorted(curve, key=lambda d: d["value"])
    if len(pts) < 2:
        return {"value": None, "note": "点不足"}
    # PSNR 随质量值（越大越差）单调下降
    for i in range(len(pts) - 1):
        v1, p1 = pts[i]["value"], pts[i]["psnr"]
        v2, p2 = pts[i + 1]["value"], pts[i + 1]["psnr"]
        if (p1 - target_psnr) * (p2 - target_psnr) <= 0 and p1 != p2:
            x = v1 + (p1 - target_psnr) * (v2 - v1) / (p1 - p2)
            return {"value": round(x, 2),
                    "between": [v1, v2],
                    "psnr_at": [p1, p2],
                    "kb": round(pts[i]["kb"] + (pts[i + 1]["kb"] - pts[i]["kb"])
                                * (x - v1) / (v2 - v1), 1)}
    # 不在区间内
    best = min(pts, key=lambda d: abs(d["psnr"] - target_psnr))
    if best["psnr"] > target_psnr:
        return {"value": None, "note": f"PSNR 全程高于目标（最低 {best['psnr']}），"
                                       f"需下调质量值档位继续扫"}
    return {"value": None, "note": f"PSNR 全程低于目标（最高 {best['psnr']}），"
                                   f"需上调质量值档位继续扫"}


def aggregate(workroot: Path) -> int:
    files = [f for f in sorted(workroot.glob("*.json")) if f.name != "aggregate.json"]
    if not files:
        print("无单素材结果可聚合"); return 2
    recs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    recs = [r for r in recs if "error" not in r]
    if not recs:
        print("单素材结果全为错误"); return 2

    print(f"\n聚合 {len(recs)} 个素材  目标 = libx265@crf{TARGET_CRF} 的 HDR 域 PSNR\n")
    print(f"{'素材':<30}{'目标PSNR':<12}" +
          "".join(f"{c:<26}" for c in GRID))
    per_codec: dict[str, list] = {c: [] for c in GRID}
    for r in recs:
        row = f"{Path(r['material']).stem[:28]:<30}{r.get('target_psnr', 0):<12.3f}"
        for codec, (flag, _) in GRID.items():
            pts = r["curves"].get(codec, {}).get("points", [])
            if not pts:
                row += f"{'—':<26}"; continue
            iv = interpolate(pts, r["target_psnr"])
            if iv.get("value") is not None:
                per_codec[codec].append((iv["value"], Path(r["material"]).stem))
                row += f"{flag}{iv['value']:<10.1f}({iv['kb']:.0f}K){'':<8}"
            else:
                row += f"{'超范围':<26}"
        print(row)

    print("\n" + "=" * 78)
    print("推荐等质量值（各素材插值结果的统计）")
    print("=" * 78)
    out = {}
    for codec, (flag, vals) in GRID.items():
        got = per_codec[codec]
        if not got:
            print(f"  {codec:<14} 无素材落在扫掠范围内"); continue
        xs = sorted(x for x, _ in got)
        med = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs)//2-1] + xs[len(xs)//2]) / 2
        print(f"  {codec:<14} {flag}{med:>7.1f}   "
              f"中位  n={len(xs)}  范围 [{xs[0]:.1f}, {xs[-1]:.1f}]  "
              f"各素材: {', '.join(f'{x:.0f}' for x in xs)}")
        out[codec] = {"flag": flag, "median": med, "n": len(xs),
                      "min": xs[0], "max": xs[-1], "per_material": got}
    (workroot / "aggregate.json").write_text(
        json.dumps({"target": f"{TARGET_CODEC} crf{TARGET_CRF}",
                    "materials": [r["material"] for r in recs],
                    "recommend": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细写入 {workroot/'aggregate.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="HDR 域等质量标定（母版锚 + PSNR）")
    ap.add_argument("--material", help="素材文件名（在 eqq_calib/6s 下）")
    ap.add_argument("--materials", help="逗号分隔多个素材")
    ap.add_argument("--aggregate", action="store_true")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--work", default=str(ROOT / "Accessory" / "temp" / "hdr_eqq"))
    args = ap.parse_args()
    workroot = Path(args.work)

    if args.aggregate:
        return aggregate(workroot)

    names = []
    if args.material:
        names.append(args.material)
    if args.materials:
        names += [x.strip() for x in args.materials.split(",") if x.strip()]
    if not names:
        ap.error("需要 --material / --materials 或 --aggregate")
    workroot.mkdir(parents=True, exist_ok=True)
    for nm in names:
        run_material(nm, args.frames, workroot)
    return 0


if __name__ == "__main__":
    sys.exit(main())