#!/usr/bin/env python3
"""交叉对照实验：分离「素材」与「管线口径」两个因素对 VMAF 斜率的影响。

已有三方数据：
  [对方] 他们的素材 + SDR直编  + 源作参照      → 斜率 0.150/crf
  [我HDR] 我的素材 + SDR→HDR管线 + libx265参照 → 斜率 0.040/crf
  [我SDR] 我的素材 + SDR直编  + 源作参照      → 斜率 0.055/crf   ← 测试B

本脚本做 测试A：**对方的素材 + 我的 SDR→HDR 管线 + libx265 参照**
  · 若斜率 ≈0.15 ⇒ 差异来自**素材**
  · 若斜率 ≈0.04 ⇒ 差异来自**管线口径**（tonemap/PQ + 跨编码器锚）
"""
import subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from bench_sdr_to_hdr import _tonemap_to_8bit, vmaf   # noqa: E402

MATS = {
    "doc_grassland(实拍纪录片)": ROOT.parent / "input_videos" / "eqq_calib" / "6s" / "doc_grassland_src1280x720.mp4",
    "cganim_wordworld(三维动画)": ROOT.parent / "input_videos" / "eqq_calib" / "6s" / "cganim_edu_wordworld_src720x576.mp4",
    "live_kids_play(实拍+图形)": ROOT.parent / "input_videos" / "eqq_calib" / "6s" / "live_kids_play_src1280x720.mp4",
}
CRFS = [12, 20, 26]
FRAMES, PEAK, WORK = 20, 1000.0, ROOT / "Accessory" / "temp" / "crossctrl"
WORK.mkdir(parents=True, exist_ok=True)


def enc(src, out, codec, extra=None, frames=FRAMES):
    cmd = [sys.executable, str(ROOT / "convert_sdr_to_hdr.py"), "-i", str(src), "-o", str(out),
           "--model", "agcm", "--codec", codec, "--crf-ref", "21", "--frames", str(frames),
           "--device", "cpu", "--split-mode", "off", "--overwrite"]
    if extra:
        cmd += ["--extra-args"] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    return r.returncode == 0 and out.exists()


for label, src in MATS.items():
    if not src.exists():
        print(f"跳过 {label}（素材不存在）"); continue
    ref, ref8 = WORK / f"{src.stem}_ref.mp4", WORK / f"{src.stem}_ref8.mp4"
    t0 = time.time()
    if not enc(src, ref, "libx265") or not _tonemap_to_8bit(ref, ref8, FRAMES, PEAK):
        print(f"{label}: 参照失败"); continue
    print(f"\n{label}   参照 {ref.stat().st_size/1024:.0f}KB  ({time.time()-t0:.0f}s)")
    pts = []
    for c in CRFS:
        out = WORK / f"{src.stem}_svt{c}.mp4"
        if not enc(src, out, "libsvtav1", ["-crf", str(c)]):
            print(f"  crf{c:<3d} 编码失败"); continue
        d8 = WORK / f"{src.stem}_svt{c}_8.mp4"
        if not _tonemap_to_8bit(out, d8, FRAMES, PEAK):
            print(f"  crf{c:<3d} tonemap 失败"); continue
        sc = vmaf(ref8, d8, FRAMES)
        if sc is None:
            print(f"  crf{c:<3d} VMAF 失败"); continue
        pts.append((c, sc, out.stat().st_size / 1024))
        print(f"  crf{c:<3d} VMAF {sc:6.2f}  {out.stat().st_size/1024:7.1f}KB")
    if len(pts) >= 2:
        (c1, v1, _), (c2, v2, _) = pts[0], pts[-1]
        print(f"  ⇒ 斜率 {(v2-v1)/(c2-c1):+.3f} 分/crf")

print("\n" + "="*70)
print("  对方素材+SDR直编+源参照      0.150 分/crf")
print("  我的素材+SDR直编+源参照      0.055 分/crf")
print("  我的素材+SDR→HDR+libx265参照 0.040 分/crf")
print("  本实验：对方素材+SDR→HDR+libx265参照  ← 看落在 0.15 还是 0.04")
print("="*70)