#!/usr/bin/env python3
"""决定性实验：把 AV1 质量推到**极高**（近无损），看 VMAF 差距是否结构性存在。

若 crf 12 / qp 20 这种近乎无损的档位，VMAF 仍停在 ~96 ⇒ 差距**不是质量档位问题**，
而是 VMAF-on-tonemapped-PQ 这一口径的系统性偏差（bench 自己的注释已预警
「PQ 码值域上跑 VMAF 曲线压平，判据分辨率不足」）⇒ **调表无法关闭该差距**。
"""
import subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from bench_sdr_to_hdr import _tonemap_to_8bit, vmaf   # noqa: E402

WORK = ROOT / "Accessory" / "temp" / "calib_av1_vmaf"
SRC = ROOT.parent / "input_videos" / "test1.mp4"
FRAMES, PEAK = 20, 1000.0

# 极高质量档：AV1 近无损
PROBES = [("libsvtav1", "-crf", 12), ("libaom-av1", "-crf", 10), ("librav1e", "-qp", 20)]

ref_src = WORK / "libx265_REF.mp4"
ref8 = WORK / "ref8.mp4"
if not ref_src.exists():
    sys.exit("参照不存在，请先跑 calibrate_av1_vmaf.py")
if not ref8.exists() and not _tonemap_to_8bit(ref_src, ref8, FRAMES, PEAK):
    sys.exit("参照 tonemap 失败")

print(f"参照 libx265 (crf-ref21) → VMAF 100.00，{ref_src.stat().st_size/1024:.1f}KB")
print(f"探针：近无损档，复用同一参照与同一打分口径\n")

for codec, flag, val in PROBES:
    out = WORK / f"probe_{codec}_{flag.lstrip('-')}{val}.mp4"
    cmd = [sys.executable, str(ROOT / "convert_sdr_to_hdr.py"), "-i", str(SRC), "-o", str(out),
           "--model", "agcm", "--codec", codec, "--crf-ref", "21", "--frames", str(FRAMES),
           "--device", "cpu", "--split-mode", "off", "--overwrite",
           "--extra-args", flag, str(val)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    if r.returncode != 0 or not out.exists():
        print(f"  {codec:12s} {flag}{val:<4d} ✗ 编码失败"); continue
    d8 = WORK / f"probe_{codec}_{val}_8.mp4"
    if not _tonemap_to_8bit(out, d8, FRAMES, PEAK):
        print(f"  {codec:12s} {flag}{val:<4d} ✗ tonemap 失败"); continue
    sc = vmaf(ref8, d8, FRAMES)
    print(f"  {codec:12s} {flag}{val:<4d} VMAF {sc if sc is None else f'{sc:6.2f}'}  "
          f"{out.stat().st_size/1024:7.1f}KB  ({time.time()-t0:.0f}s)")