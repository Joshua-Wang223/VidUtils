#!/usr/bin/env python3
"""
# calibrate_av1_vmaf.py — 用 VMAF（而非等体积）重标定软编等质量表
#
# 背景（2026-10-15）：C8 门禁暴露出现表把「同锚点」当成了「同画质」。
# 现表 libsvtav1/libaom-av1/librav1e 是按**等体积**口径标定的
# （见 calibrate_soft_offsets.py 的方法说明，其自述「⚠ 等体积 ≠ 等质量」），
# 实测 crf-ref 21 下三个 AV1 软编比 libx265 低 3.3~4.5 个 VMAF 点。
#
# 本脚本按**真 VMAF** 重标定：
#   1. 参照 = libx265 @ crf-ref 21（生产同一条管线：agcm 模型推理 → PQ/BT.2020 → 编码）
#   2. 每个候选编码器 × 每个候选原生质量值，跑同一条管线（--extra-args 注入原生值）
#   3. 用 **bench 自己的** `_tonemap_to_8bit` / `vmaf` 打分 —— 与门禁逐字同口径，
#      避免"标定用一套口径、门禁用另一套"导致标完仍不过
#   4. 选出让 VMAF ≥ 目标（默认 99.0，即门禁阈值 −1.0 刚好转正）的**最小质量值**
#      （= 最高质量、最小压缩率的那一档），符合"首先保质量不退"。
#
# ⚠ 原生质量值直接注入 `--extra-args`（如 `-crf 24` / `-qp 50`）。ffmpeg 对同名选项
#   取**最后一个**，故脚本会把表值也留在命令里（如 `-crf 29 -crf 24`），实际生效是后者。
#   这样测出来的就是"真下发值"，不是"表里写的值"。
#
# 用法：
#   python3 Accessory/probe/calibrate_av1_vmaf.py --frames 20 --target 99.0
#   python3 Accessory/probe/calibrate_av1_vmaf.py --selftest
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))

# 复用 bench 的打分函数 —— 保证与门禁**同一口径**（tonemap 链 + _VMAF_REF_CRF + n_subsample=1）
from bench_sdr_to_hdr import _tonemap_to_8bit, vmaf, _ffmpeg_bin  # noqa: E402

DEFAULT_SRC = ROOT.parent / "input_videos" / "test1.mp4"
REF_CODEC = "libx265"

# 候选扫描网格。⚠ 现表值（libsvtav1 crf29 / libaom-av1 crf26 / librav1e qp64）已在 C8 测过，
# 这里只补新点，避免重复消耗机时。
# ⚠ flag 必须**带连字符**（`-crf` / `-qp`），它会原样进 `--extra-args`。
#   写成 `"crf"` 会让 ffmpeg 把 `crf` 当成输出文件名 →「Unable to choose an output
#   format for 'crf'」（退出码 234），整个标定静默失败。踩过，别去掉横杠。
DEFAULT_GRID = {
    "libsvtav1": ("-crf", [26, 23, 20]),
    "libaom-av1": ("-crf", [23, 20, 17]),
    "librav1e": ("-qp", [56, 48, 40]),
}


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def encode_one(src: Path, out: Path, codec: str, flag: str | None,
               value: int | None, model: str, crf_ref: int, frames: int,
               timeout: int) -> tuple[bool, str]:
    """跑生产同一条管线；flag/value 给出时用 --extra-args 注入原生质量值。"""
    cmd = [sys.executable, str(ROOT / "convert_sdr_to_hdr.py"),
           "-i", str(src), "-o", str(out),
           "--model", model, "--codec", codec,
           "--crf-ref", str(crf_ref), "--frames", str(frames),
           "--device", "cpu", "--split-mode", "off",
           "--overwrite"]
    if flag:
        cmd += ["--extra-args", flag, str(value)]
    try:
        r = _run(cmd, timeout)
        return (r.returncode == 0 and out.exists()), (r.stdout or "")[-400:] + (r.stderr or "")[-400:]
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout}s"


def main() -> int:
    ap = argparse.ArgumentParser(description="用 VMAF 重标定软编等质量表")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--frames", type=int, default=20, help="扫描用帧数（最终复核建议 30）")
    ap.add_argument("--target", type=float, default=99.0, help="目标 VMAF（门禁 −1.0 的转正值）")
    ap.add_argument("--model", default="agcm")
    ap.add_argument("--crf-ref", type=int, default=21)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--work", default=str(ROOT / "Accessory" / "temp" / "calib_av1_vmaf"))
    ap.add_argument("--peak", type=float, default=1000.0, help="tonemap peak（与门禁同）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        print("✓ 自检：VMAF 依赖存在性")
        r = _run([_ffmpeg_bin(), "-hide_banner", "-filters"], 60)
        ok = r.returncode == 0 and "libvmaf" in (r.stdout or "") and "tonemap" in (r.stdout or "")
        print("  libvmaf+tonemap 滤镜:", "✓" if ok else "✗（本机缺滤镜，无法标定）")
        return 0 if ok else 2

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    src = Path(args.src)
    if not src.exists():
        print(f"✗ 素材不存在：{src}")
        return 2

    tasks = [(REF_CODEC, None, None)]          # 参照：无注入，用表值（=crf-ref 锚点）
    for codec, (flag, vals) in DEFAULT_GRID.items():
        for v in vals:
            tasks.append((codec, flag, v))

    print(f"▸ 参照：{REF_CODEC} @ crf-ref {args.crf_ref}")
    print(f"▸ 扫描 {len(tasks)-1} 个候选，{args.frames} 帧，agcm 推理，CPU")
    print(f"▸ 目标 VMAF ≥ {args.target}\n")

    produced: list[tuple[str, str | None, int | None, Path]] = []
    for i, (codec, flag, val) in enumerate(tasks, 1):
        tag = f"{codec}" + (f"_{flag.lstrip(chr(45))}{val}" if flag else "_REF")
        out = work / f"{tag}.mp4"
        label = f"[{i}/{len(tasks)}] {tag}"
        print(f"{label:34s} 编码中…", flush=True)
        t0 = time.time()
        ok, tail = encode_one(src, out, codec, flag, val, args.model,
                              args.crf_ref, args.frames, args.timeout)
        sz = out.stat().st_size if out.exists() else 0
        print(f"{'':34s} {'✓' if ok else '✗'} {time.time()-t0:6.1f}s  {sz/1024:.1f}KB")
        if not ok:
            print(f"{'':34s}   {tail.strip()[:200]}")
            continue
        produced.append((codec, flag, val, out))

    if not any(c == REF_CODEC for c, *_ in produced):
        print("\n✗ 参照未产出，无法打分")
        return 2

    print("\n▸ 打分（tonemap→x264 crf10→libvmaf n_subsample=1，与门禁同口径）")
    ref_out = next(p for c, *_ , p in produced if c == REF_CODEC)
    ref8 = work / "ref8.mp4"
    if not _tonemap_to_8bit(ref_out, ref8, args.frames, args.peak):
        print("✗ 参照 tonemap 失败")
        return 2

    rows = []
    for codec, flag, val, out in produced:
        if codec == REF_CODEC:
            rows.append({"codec": codec, "flag": None, "value": None,
                         "vmaf": 100.0, "kb": out.stat().st_size / 1024, "ref": True})
            print(f"  {codec:16s} {'REF':>6s}  VMAF 100.00  {out.stat().st_size/1024:8.1f}KB")
            continue
        d8 = work / f"{codec}_{flag.lstrip(chr(45))}{val}_8.mp4"
        if not _tonemap_to_8bit(out, d8, args.frames, args.peak):
            print(f"  {codec:16s} {flag}{val:<4d}  tonemap 失败")
            continue
        sc = vmaf(ref8, d8, args.frames)
        if sc is None:
            print(f"  {codec:16s} {flag}{val:<4d}  VMAF 失败")
            continue
        rows.append({"codec": codec, "flag": flag, "value": val, "vmaf": round(sc, 2),
                     "kb": out.stat().st_size / 1024, "ref": False})
        mark = "✓" if sc >= args.target else " "
        print(f"  {codec:16s} {flag}{val:<4d}  VMAF {sc:6.2f} {mark} {out.stat().st_size/1024:8.1f}KB")

    print("\n" + "=" * 78)
    print(f"建议（目标 VMAF ≥ {args.target}，取**最高质量**的那一档 = 数值最小）")
    print("=" * 78)
    recs = {}
    for codec in DEFAULT_GRID:
        flag = DEFAULT_GRID[codec][0]
        cand = [r for r in rows if r["codec"] == codec and r.get("flag") == flag]
        good = [r for r in cand if r["vmaf"] >= args.target]
        if good:
            best = min(good, key=lambda r: r["value"])
            recs[codec] = {"flag": flag, "value": best["value"],
                           "vmaf": best["vmaf"], "kb": round(best["kb"], 1)}
            print(f"  {codec:16s} {flag} = {best['value']:<4d} (VMAF {best['vmaf']}, {best['kb']}KB)  ✓")
        elif cand:
            best = max(cand, key=lambda r: r["vmaf"])
            recs[codec] = {"flag": flag, "value": None, "vmaf": best["vmaf"],
                           "kb": round(best["kb"], 1), "note": "扫描范围内未达标"}
            print(f"  {codec:16s} 未达标：最高 VMAF {best['vmaf']} @ {flag}{best['value']}"
                  f"（{best['kb']}KB）→ 需扩大扫描范围")
        else:
            print(f"  {codec:16s} 无有效数据")

    out_json = work / "result.json"
    out_json.write_text(json.dumps(
        {"args": vars(args), "rows": rows, "recommend": recs},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写入 {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())