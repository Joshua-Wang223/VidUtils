#!/usr/bin/env python3
"""anchor_test_v2 —— 用**真正的 HDR master** 做公共锚，验证 VMAF 在 HDR 管线是否恢复分辨率。

修正上一版两个错误：
  ① `-qp 0` 经 `--extra-args` 下发**没生效**（model 模式下 libx265 的 -crf 与 -qp 冲突，
     产物 69KB 与 crf21 参照同体积，根本不是无损）⇒ 上一版「master」其实还是 crf21，
     算出了与 libx265-锚完全相同的数字，等于没换锚。
  ② 本版**绕开编码**：直接取模型输出帧 → tonemap → 公共锚，全程不经有损环节。

严格对齐对方 run_cell.sh 的「公共锚」思路：
  master  = 模型输出的 HDR 帧（无压缩母版）
  anchor8 = tonemap(master)                       ← 公共锚，只算一次
  对比侧 = tonemap(同一 master 经各编码器按质量档位编码的产物)
  ⇒ 所有编码器都与**同一份无压缩母版**比，不存在「拿 HEVC 当锚偏袒 HEVC」。
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from bench_sdr_to_hdr import _tonemap_to_8bit, vmaf, _ffmpeg_bin, _run, _VMAF_REF_CRF  # noqa: E402

SRC = ROOT.parent / "input_videos" / "eqq_calib" / "6s" / "doc_grassland_src1280x720.mp4"
FRAMES, PEAK = 20, 1000.0
WORK = ROOT / "Accessory" / "temp" / "anchor_v2"
WORK.mkdir(parents=True, exist_ok=True)
FF = _ffmpeg_bin()
SETPARAMS = ("setparams=colorspace=bt2020nc:color_primaries=bt2020:"
             "color_trc=smpte2084:range=pc")


def ffprobe_dims(p):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True).stdout.strip()
    w, h = o.split(",")[:2]
    return w, h


print(f"素材 {SRC.name}  {FRAMES} 帧  agcm 模型\n")

# ── 1. 产出无压缩 HDR 母版（gbrp16le 原始帧）────────────────────────────
t0 = time.time()
W, H = ffprobe_dims(SRC)
fb = int(W) * int(H) * 3 * 2
dec = subprocess.run(
    [FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(SRC),
     "-an", "-sn", "-dn", "-frames:v", str(FRAMES), "-vf", "format=gbrp16le",
     "-pix_fmt", "gbrp16le", "-f", "rawvideo", "-"],
    capture_output=True, timeout=1800)
if dec.returncode != 0:
    sys.exit(f"解码失败: {dec.stderr[-300:]}")

# 送过模型，得到真正的 HDR 母版
import convert_sdr_to_hdr as C  # noqa: E402

engine = C.HdrTvNetPlus(str(ROOT.parent / "HDRTVNet-plus"), device="cpu",
                        variant="agcm", verbose=False)
n = len(dec.stdout) // fb
hdr = bytearray()
for i in range(n):
    # ⚠ 必须用脚本自己的 raw_to_frame 反 planar（gbrp = [G,B,R] 三张整帧平面）。
    #   我先前手写 `.reshape(H, W, 6)` 把它当逐像素交错 → 三通道整体错位，
    #   母版退化成近乎纯色，编码产物只有 1~3KB（1280x720×20帧 不可能这么小）。
    fr = C.raw_to_frame(dec.stdout[i * fb:(i + 1) * fb], int(W), int(H))
    out_frame = engine.enhance(fr)
    hdr += C.frame_to_raw(out_frame)
master_raw = WORK / "master_hdr.gbrp16le"
master_raw.write_bytes(bytes(hdr))
print(f"母版 {n} 帧 {W}x{H} HDR/10bit（模型输出，无压缩）  ({time.time()-t0:.0f}s)\n")

# ── 2. 公共锚：母版经门禁同一条 tonemap 链（不经任何编码）────────────────
anchor_src = WORK / "anchor_tm.mkv"
r = _run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
          "-f", "rawvideo", "-pixel_format", "gbrp16le",
          "-video_size", f"{W}x{H}", "-framerate", "30", "-i", str(master_raw),
          "-vf", f"tonemap=tonemap=hable:desat=0:peak={PEAK},format=yuv420p",
          "-c:v", "libx264", "-crf", str(_VMAF_REF_CRF), "-pix_fmt", "yuv420p",
          str(anchor_src)], timeout=1800)
if r.returncode != 0 or not anchor_src.exists():
    sys.exit(f"锚 tonemap 失败: {r.stderr[-300:]}")
anchor8 = WORK / "anchor8.mp4"
_run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
      "-i", str(anchor_src), "-c:v", "libx264", "-crf", str(_VMAF_REF_CRF),
      "-pix_fmt", "yuv420p", str(anchor8)], timeout=1800)
print(f"公共锚（母版直接 tonemap，未经任何编码）：{anchor8.stat().st_size/1024:.0f}KB\n")

# ── 3. 各编码器吃同一份母版 → tonemap → 对锚打分 ────────────────────────
GRID = (("libsvtav1", "-crf", [12, 20, 26]),
        ("libaom-av1", "-crf", [12, 20, 26]),
        ("librav1e", "-qp", [20, 40, 60]))

for codec, flag, vals in GRID:
    print(f"{codec}:")
    got = []
    for v in vals:
        outp = WORK / f"{codec}_{flag[1:]}{v}.mp4"
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "rawvideo", "-pixel_format", "gbrp16le",
               "-video_size", f"{W}x{H}", "-framerate", "30", "-i", str(master_raw),
               "-vf", SETPARAMS, "-c:v", codec, flag, str(v),
               "-pix_fmt", "yuv420p10le", str(outp)]
        rr = _run(cmd, timeout=3600)
        if rr.returncode != 0 or not outp.exists():
            print(f"  {flag}{v:<4d} ✗ 编码失败 {rr.stderr[-140:]}")
            continue
        d8 = WORK / f"{codec}_{v}_8.mp4"
        if not _tonemap_to_8bit(outp, d8, FRAMES, PEAK):
            print(f"  {flag}{v:<4d} ✗ tonemap 失败")
            continue
        sc = vmaf(anchor8, d8, FRAMES)
        if sc is None:
            print(f"  {flag}{v:<4d} ✗ VMAF 失败")
            continue
        got.append((v, sc, outp.stat().st_size / 1024))
        print(f"  {flag}{v:<4d} VMAF {sc:6.2f}  {outp.stat().st_size/1024:7.1f}KB")
    if len(got) >= 2:
        (v1, s1, _), (v2, s2, _) = got[0], got[-1]
        print(f"  ⇒ 斜率 {(s2 - s1) / abs(v2 - v1):+.3f} 分/质量单位\n")

print("=" * 74)
print("  libx265 当锚（上轮）            斜率 -0.020（平坦）")
print("  母版直接 tonemap 当公共锚（本轮） 斜率见上")
print("=" * 74)