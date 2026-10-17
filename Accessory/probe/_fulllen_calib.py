#!/usr/bin/env python3
"""_fulllen_calib.py —— B「全长重标定」分片驱动器（§10.2 裁定 C+B）。

对每个素材：
  1) 复用校验阶段的全长母版 hdr_eqq_full/<stem>/master_hdr.gbrp16le（字节 = 校验对象，不再建）
  2) 无损锚复用 master_lossless_full.mp4（硬链接为 master_lossless.mp4，供 _anchor_targets.py 使用）
  3) 按 20 帧标定 json 的**同一点集**全长编码（产物存在即跳过 ⇒ 可断点续跑；PSNR 全部现算）
  4) 写 hdr_eqq_full/<stem>.json（`_final_fit.py` 兼容格式：curves + target_psnr；anchors 之后由
     `_anchor_targets.py` 追加）

⚠ 2026-10-17 修复（首轮全部中断于 aom crf12 3600s 超时——TimeoutExpired 未捕获导致整素材中止）：
  · 单点编码超时/失败不再中断素材（捕获 subprocess.TimeoutExpired，删除半成品，继续）
  · --timeout 可调单点上限；--codecs 选查编码器；--only/--skip 选点（如 libaom-av1:12）
  · 写 json 时与既有文件**逐点合并**（保留其他编码器曲线、anchors、target 字段）⇒ 支持增量多阶段
  · --only 中含 j20 点集之外的值即追加新点（如长尾需要的 libaom-av1:8）
  · 策略：低 crf 的 aom 点（12 等）全长极慢 ⇒ 主跑 `--skip libaom-av1:12`，
    随后高线程专用通道 `--only libaom-av1:12 --timeout 大值` 补跑

用法（24 核建议 5~7 分片 × EQQ_ENC_THREADS=4）：
  EQQ_ENC_THREADS=4 python3 Accessory/probe/_fulllen_calib.py --shard 1/7 --skip libaom-av1:12
慢点专用通道（aom crf12 单点可 >1h）：
  EQQ_ENC_THREADS=10 python3 Accessory/probe/_fulllen_calib.py --only libaom-av1:12 --timeout 21600
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
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Accessory" / "probe"))
from bench_sdr_to_hdr import _run  # noqa: E402
from calibrate_hdr_equal_quality import (FF, SETPARAMS, TARGET_CODEC,  # noqa: E402
                                         TARGET_CRF, psnr_hdr)

EQ = ROOT / "Accessory" / "temp" / "hdr_eqq"          # 20 帧标定（网格/口径来源）
FULL = ROOT / "Accessory" / "temp" / "hdr_eqq_full"   # 全长产物（母版复用）
CODECS = ("libsvtav1", "libaom-av1", "librav1e")


def _rm(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def enc(master: Path, W: int, H: int, out: Path, codec: str,
        flag: str | None, val, timeout: float) -> bool | None:
    """单点编码。True=成功；False=失败；None=超时（半成品已删除）。
    argv 与 calibrate_hdr_equal_quality.encode() 一致（含 EQQ_ENC_THREADS）。"""
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "rawvideo", "-pixel_format", "gbrp16le",
           "-video_size", f"{W}x{H}", "-framerate", "30", "-i", str(master),
           "-vf", SETPARAMS, "-c:v", codec]
    t = os.environ.get("EQQ_ENC_THREADS")
    if t:
        cmd += ["-threads", str(t)]
    if flag:
        cmd += [flag, str(val)]
    cmd += ["-pix_fmt", "yuv420p10le", str(out)]
    try:
        r = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        _rm(out)
        return None
    if r.returncode != 0 or not (out.exists() and out.stat().st_size > 0):
        _rm(out)
        return False
    return True


def ensure_lossless(wd: Path, master: Path, W: int, H: int, timeout: float) -> Path:
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
    if not enc(master, W, H, ref, "libx265", "-qp", 0, timeout):
        raise RuntimeError("无损锚编码失败")
    return ref


def load_points(jf: Path, codec: str) -> dict:
    """既有 json 中该编码器的点（value -> point），用于增量合并。"""
    if not jf.exists():
        return {}
    try:
        old = json.loads(jf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {p["value"]: p for p in old.get("curves", {}).get(codec, {}).get("points", [])}


def merge_write(stem: str, rec: dict) -> None:
    """写出 json；若已存在则并保其他编码器曲线与 anchors / target 字段。"""
    jf = FULL / f"{stem}.json"
    if jf.exists():
        old = json.loads(jf.read_text(encoding="utf-8"))
        for c, cv in old.get("curves", {}).items():
            rec["curves"].setdefault(c, cv)          # 本次已处理的编码器以本次为准
        for k in ("anchors", "target_psnr", "target_kb", "lossless_kb"):
            if k not in rec and k in old:
                rec[k] = old[k]
    jf.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def run_stem(stem: str, codecs: tuple, only: set, skip: set, timeout: float) -> dict:
    j20 = json.loads((EQ / f"{stem}.json").read_text(encoding="utf-8"))
    wd = FULL / stem
    jf = FULL / f"{stem}.json"
    wd.mkdir(parents=True, exist_ok=True)
    master = wd / "master_hdr.gbrp16le"
    if not (master.exists() and master.stat().st_size > 0):
        raise RuntimeError(f"缺少全长母版：{master}（需先完成 C 补测）")
    W, H = (int(x) for x in j20["wh"].split("x"))
    n = master.stat().st_size // (W * H * 3 * 2)
    print(f"[{stem}] 全长母版 {n} 帧 {W}x{H}（复用）", flush=True)
    ref = ensure_lossless(wd, master, W, H, timeout)

    rec: dict = {"material": f"{stem}.mp4", "frames": n, "wh": f"{W}x{H}",
                 "lossless_kb": round(ref.stat().st_size / 1024, 1), "curves": {},
                 "note": "B 全长重标定：母版=校验同源复用；网格=20 帧标定同点集；"
                         "度量=psnr 滤镜(10bit、不 tonemap)"}

    if not only:
        tgt = wd / f"{TARGET_CODEC}_crf{TARGET_CRF}.mp4"
        if not (tgt.exists() and tgt.stat().st_size > 0):
            if enc(master, W, H, tgt, TARGET_CODEC, "-crf", TARGET_CRF, timeout) is not True:
                raise RuntimeError("目标档编码失败")
        p = psnr_hdr(tgt, ref)
        if p is None:
            raise RuntimeError("目标档 PSNR 失败")
        rec["target_psnr"] = p
        rec["target_kb"] = round(tgt.stat().st_size / 1024, 1)
        print(f"[{stem}] 目标 {TARGET_CODEC} crf{TARGET_CRF}: PSNR {p:.3f}", flush=True)
    else:
        print(f"[{stem}] --only 模式：跳过目标档重算（沿用既有 json）", flush=True)

    t0 = time.time()
    for codec in codecs:
        cur20 = j20["curves"][codec]
        flag = cur20["flag"]
        extra = sorted(int(k.split(":")[1]) for k in only
                       if k.split(":")[0] == codec and ":" in k)
        vals = sorted({p_["value"] for p_ in cur20["points"]} | set(extra))
        points = load_points(jf, codec)
        for v in vals:
            key = f"{codec}:{v}"
            if only and key not in only:
                continue
            if key in skip:
                print(f"[{stem}] {codec} {flag}{v} 跳过（--skip）", flush=True)
                continue
            out = wd / f"{codec}_{flag[1:]}{v}.mp4"
            st_: bool | None = True
            if not (out.exists() and out.stat().st_size > 0):
                st_ = enc(master, W, H, out, codec, flag, v, timeout)
            if st_ is None:
                print(f"[{stem}] {codec} {flag}{v} 编码超时（>{timeout:.0f}s，跳过）", flush=True)
                continue
            if st_ is False:
                print(f"[{stem}] {codec} {flag}{v} 编码失败（跳过）", flush=True)
                continue
            pp = psnr_hdr(out, ref)
            if pp is None:
                print(f"[{stem}] {codec} {flag}{v} PSNR 失败（跳过）", flush=True)
                continue
            points[v] = {"value": v, "psnr": round(pp, 3),
                         "kb": round(out.stat().st_size / 1024, 1)}
            print(f"[{stem}] {codec} {flag}{v:<3d} PSNR {pp:7.3f} "
                  f"{out.stat().st_size / 1024:7.1f}KB  ({time.time()-t0:.0f}s)", flush=True)
        if points:
            rec["curves"][codec] = {"flag": flag,
                                    "points": [points[v] for v in sorted(points)]}

    merge_write(stem, rec)
    print(f"[{stem}] ↳ 写出 {jf}  ({time.time()-t0:.0f}s)", flush=True)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="B 全长重标定（分片驱动器）")
    ap.add_argument("--shard", help="i/N：本次只跑第 i 片（1 起）")
    ap.add_argument("--materials", help="逗号分隔（默认=eqq_calib 有 20f json 的全部素材）")
    ap.add_argument("--codecs", default=",".join(CODECS), help="逗号分隔编码器子集")
    ap.add_argument("--only", default="",
                    help="只跑这些点，codec:value 逗号分隔（如 libaom-av1:12；含 j20 外值即追加新点）")
    ap.add_argument("--skip", default="", help="跳过这些点，codec:value 逗号分隔")
    ap.add_argument("--timeout", type=float, default=3600.0, help="单点编码超时秒（默认 3600）")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    codecs = tuple(x.strip() for x in args.codecs.split(",") if x.strip())
    for key in only | skip:
        c = key.split(":")[0]
        if c not in CODECS or ":" not in key:
            ap.error(f"非法点选择：{key}（须为 codec:value，codec ∈ {CODECS}）")
    for c in codecs:
        if c not in CODECS:
            ap.error(f"非法编码器：{c}")

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
            run_stem(stem, codecs, only, skip, args.timeout)
        except Exception as e:  # noqa: BLE001 —— 单素材失败不拖垮整片
            print(f"[{stem}] ✗ {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
