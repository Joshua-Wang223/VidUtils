#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_sdr_to_hdr.py — convert_sdr_to_hdr.py 的生产基准（2026-10-08）

目的
────
在**同一输入视频 + 同输出质量**条件下，测 `convert_sdr_to_hdr.py` 不同参数与不同执行
路径的总耗时差异，回答三类问题：
  ① 编码器/质量轴：同样「等质量」下libx265 / libsvtav1 / libaom-av1 / librav1e
     各花多少时间、产多大。（SDR→HDR10 的交付口径是 libx265，但换编码器能省多少
     是个实操问题。）
  ② 执行路径：整条 vs 分段并行（`--split-mode segment`）vs 多进程推理
     （`--split-mode workers`），以及 `--tile` 分块推理的代价。
  ③ 瓶颈分解：模型推理占多少、编码占多少、`--no-model` 相对全流程省多少。

三条设计红线（都是本机实测踩出来的，改动前先读 memory 的对应条目）
────────────────────────────────────────────────────────────
1. **只信自己测的墙钟，不信被测脚本自报的 fps。**
   `convert_sdr_to_hdr.py:3023` 的「均速」用 `j.info["nb_frames"]`（**源探测帧数**）
   而非实际输出帧数——用 `--frames 10` 截断时实测打印「均速 7fps、峰值 4fps」，
   均速高于峰值、逻辑上不可能，真实值2.9fps。`j.speed` 全仓库无人赋值、恒为 `-`。
   ⇒ 本脚本用 `time.monotonic()` 测墙钟、用 `ffprobe -count_frames` 自己数帧。

2. **「参数不同」≠「路径不同」，必须断言组合真的生效。**
   实测四类静默退化：8秒素材 + `--workers 4` 仍返回 `'off'`（`_SEGMENT_MIN_SECONDS`
   = 20.0 阈值，且内存画像把 workers 压到 1）；`--frames`/`--duration` 与分段互斥、
   给了就一律降级 `'off'`；CUDA 推理下 `split-mode workers` 被降级；显式
   `segment` + `--no-model` 反而是合法的。
   ⇒ 本脚本抓 stdout 里的 `分段并行：N 段`（:4185）/ `单解码 + N 推理进程`（:4295）
   两行**确认路径生效**，否则把该组合标为 SKIP 而不是当成一个 baseline 报出去。

3. **「同质量」必须用 `--crf-ref` 锚定，且要断言目标编码器在换算表里。**
   ref=21 处**没有任何编码器是恒等映射**（实测 `from_x264_crf`）：libx265→20.74、
   hevc_nvenc→25.50、libaom-av1→26.37、libsvtav1→28.96、librav1e→64.38。只有
   libx264 恰好 21.00，而那是**回退到 SIZE_MAP 的副产品**、非显式声明。
   ⚠ 不在 QUALITY_MAP 里的编码器（libx264 / 全部 `*_qsv`/`*_amf`/`*_vaapi`/
   `*_videotoolbox`）会**静默按等体积口径**换算、不报错 —— 那就是「测了等体积却标成
   等质量」。故`--only-quality-codecs` 等价于一条断言：非表内编码器直接拒绝。

分层矩阵
────
  L1 编码器×质量    --no-model，跨编码器等质量对比（最快，不需要 torch）
  L2 执行路径       segment / workers / tile 分块（需要 ≥20s 素材与 torch）
  L3 瓶颈分解       no-model vs 全流程，量化模型推理占比
  L4 质量门禁       对抽样组合跑 VMAF（PQ 先 tonemap 到 8bit，n_subsample=1）
  C  并发/线程阶梯  单文件 workers=1/2/4（=分段数）与每任务 threads=1/2/4/8
  D  NVENC 组       hevc/av1_nvenc 的 -cq / -nvenc-aq / -tune uhq / rc-mode cbr
  E  解码/容器/音频 --decode cpu/cuda/auto、容器 mp4/mkv、音频 copy/none
  G  4K 分块推理    tile=0 / 1024+128 / 2048+256（1080p 整帧不吃内存，4K 才是主场）

⚠ 四组「看起来能测、实际在本机会退化」的轴已被显式拦住并标 SKIP（附原因）。
这是刻意设计：退化轴会产出「几组一模一样」的耗时，那看起来像一个很确定的
结论、其实什么都没测到。
  · D 组：本机无 NVIDIA 卡。实测 `--codec hevc_nvenc` 会**静默降级到 libx265
    且 ffmpeg token 逐字不变**（`ffmpeg -encoders` 列得出 nvenc ≠ 有卡能编）。
  · E 组解码轴：无卡时 `--decode cuda` 的 `-vf` 链与 cpu **完全相同**。
  · E 组音频轴：无音轨素材上 `--audio copy` 与 `--audio none` 命令**逐字相同**。
  · C 组 workers：单文件下 workers = 分段数，且**非单调**（实测 workers=4 比 2
    慢约 30%，属过订），本机内存上限约 2。
  ⚠ E 组的**容器轴是有效的**（mkv 与 mp4 都保住 HDR10 静态元数据，所以它测的是
    mux 开销、不是元数据差异）。

⚠ G 层务必配 `--frames`：实测 4K 单帧 CPU 推理 **整帧 tile=0 要 237.7 秒**
  （峰值内存 **4.13 GB**，本机可用内存 4~5 GB 刚好够跑完），tile=1024+128 分块
  是它的快路径。10 秒 480 帧的素材整帧单次要约 **31 小时**，故必须限帧。
  ⚠ 顺带纠正一个常见误解：**4K 整帧并非必然 OOM**（被测脚本注释说「4K 整帧会吃光
  内存」），它在 7.7GB 机器上跑通了 —— 但峰值 4.13GB 意味着**显存/内存更小的机器
  仍会失败**，那一格在别的机器上可能真的 OOM。
  但**加了 `--frames` 会让 C 组的分段轴被降级成 'off'**（被测脚本 :4079 的截断/
  分段互斥）—— 两者互斥，脚本会提前预告。

VMAF 的两个硬约束（实测，别改）
────────────────────────────────
  · **PQ/BT.2020 不能直接喂 libvmaf**：不报错，但 crf 20~32 区间曲线压平
    （实测 99.85/99.82/98.21，跨度仅 1.6 分）→ 判据分辨率不足、"同质量"不可判定。
    必须两侧都先 `tonemap=tonemap=hable:desat=0:peak=100,format=yuv420p` 落 8bit
    中间件（本机**无 zscale**，所以不能复用 calibrate_equal_quality.py:343 的链）。
  · **n_subsample 必须 = 1**：实测 1→91.0、8→94.3，虚高 3.3 分。代价是慢，
    故耗时基准与 VMAF 门禁**分开跑**，VMAF 只对抽样组合跑。

用法
────
  # 装置自检（不碰素材、不跑编码，CPU-only，门禁该绿）
  python3 Accessory/probe/bench_sdr_to_hdr.py --selftest

  # 环境与能力速查（哪些层本机能跑、哪些会被 SKIP）
  python3 Accessory/probe/bench_sdr_to_hdr.py --env

  # L1+L3 快测（--no-model，不需要 torch 与模型仓库）
  python3 Accessory/probe/bench_sdr_to_hdr.py -i <源视频> --layers l1,l3

  # 全量（真实素材 + torch；L2 需要 ≥20s 素材）
  python3 Accessory/probe/bench_sdr_to_hdr.py -i <≥20s源视频> --layers l1,l2,l3

  # 落报告
  python3 Accessory/probe/bench_sdr_to_hdr.py -i <源视频> --json /tmp/b.json --md /tmp/b.md

素材要求
────
  · L1/L3 用任意长度的 SDR 源即可。
  · **L2（分段并行）需要 ≥20 秒**（`_SEGMENT_MIN_SECONDS`），否则组合会被降级成
    `'off'` 而失去对比意义——脚本会显式 SKIP 并说明原因，不会静默跑一个假baseline。
  · 真实素材推荐 `Accessory/archive/m2_srcs/`（⚠ 文件名会骗人：`cc_anim_300s.mkv`
    实为 8.08s、`earth_dark_80s.mp4` 实为 6.15s）。testsrc2 只够冒烟：它是合成色块+
    硬边文字，无传感器噪声/胶片颗粒/真实高光roll-off，SDR→HDR 的色调映射分支得不到
    有效激励，且同等质量下所需码率显著高于真实素材。

退出码：0 正常（含 SKIP）/ 1 有组合失败 / 2 参数或环境错误（素材不存在等）
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _paths import repo_root, temp_root  # noqa: E402

ROOT = repo_root()
sys.path.insert(0, str(ROOT))

import convert_crf as CRF  # noqa: E402

VERSION = "1.0"

# ═══════════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════════

# 分段并行的时长阈值，与 convert_sdr_to_hdr.py 的 _SEGMENT_MIN_SECONDS 对齐。
# 重复写而不是 import 私有名：那个值一旦被改动，本脚本必须**显式失配**而不是
# 跟着悄悄变（跟着变会让「素材够长」的判断失准，组合被静默降级成 'off'）。
_SEGMENT_MIN_SECONDS = 20.0

# 模型下采样三次 ⇒ 宽高须为 8 的倍数。与 convert_sdr_to_hdr.py:186 的 ALIGN
# 同值，用于 tile_cost_table 复现其边缘块补齐逻辑（补到 8 的倍数，不是补到满 tile）。
ALIGN = 8

# 路径生效标记：从被测脚本 stdout 里抓这两行，确认组合真的走了预期路径
# （:4185 `分段并行：{n} 段 × {c} 并发` / :4295 `单解码 + {nproc} 推理进程 + 单编码（spawn）`）
_RE_SEGMENT = re.compile(r"分段并行：\s*(\d+)\s*段")
_RE_MULTIPROC = re.compile(r"单解码 \+ (\d+) 推理进程")
_RE_SEGMENT_WORKS = re.compile(r"(\d+)\s*段\s*×\s*(\d+)\s*并发")

# 「⚙ 分段并行」等标记行，用来区分「路径生效」与「静默降级成 off」
_RE_PATH_HINT = re.compile(r"分段并行|单解码 \+|已按整条处理|不可用，已按整条处理")

# 被测脚本打印的「✔ 完成，用时 3.5s」——用于交叉核对我们的墙钟（不作为主数据源，
# 因为它不含模型加载、且分段/多进程路径的口径与整条路径不一致）
_RE_DONE_ELAPSED = re.compile(r"✔\s*完成，用时\s*(\S+)")

# 被测脚本打印的「  输出文件    : /path/x.mp4」——**产物真实路径的唯一权威来源**。
# ⚠ 不要用「输入路径改扩展名」去猜产物名：`--container .mkv` 会把 `.mp4` 换成
# `.mkv`（实测 `e.container.mkv` 的产物是 `…_hdr.mkv` 而非 `….mp4`），猜错会导致
# 「退出码 0 但产物不存在」这种**把成功判成失败**的假红。
_RE_OUT_FILE = re.compile(r"输出文件\s*:\s*(\S+)")

# 「本次是否真的重编了」的判据（防秒返回的假加速比）。
# ⚠ 不能用「文本里有没有『跳过』」判断 —— 实测踩过：正常输出里也有
# 「模型仓库: 跳过检查（--no-model）」「推理: 跳过（--no-model）」这类**正常提示**，
# 粗暴的 `in text` 会把每次成功运行都误判成 FAIL（4/4 全红，而产物全都正常生成了）。
# 可靠判据是两个硬信号的组合：
#   · 真跳过 → 汇总行是「完成 0 … 跳过 1」，且**没有**「✔ 完成」行
#   · 真跑成 → 必有「✔ 完成，用时 Xs」（run_sequential :3935，单文件场景唯一逐文件耗时）
_RE_SUMMARY_DONE = re.compile(r"汇总\s*:\s*完成\s*(\d+)")
_RE_SUMMARY_SKIP = re.compile(r"汇总\s*:\s*完成\s*\d+\s+失败\s*\d+\s+跳过\s*(\d+)")
_RE_DONE_MARK = re.compile(r"✔\s*完成，用时")

# VMAF 参考池的画质档：`-crf 10` 的量化误差远小于被测编码器引入的误差
_VMAF_REF_CRF = 10

# 质量门禁：VMAF 允许的最大回退（与 verify_equal_quality.py 的 TOL_VMAF=1.0 同口径）
_VMAF_TOL = 1.0

# L1 默认的编码器矩阵。⚠ 只放**在 QUALITY_MAP 里**的编码器 —— 见文件头红线 3。
# libx264 不在表内（靠回退 SIZE_MAP 恰好恒等），故不放进来。
L1_CODECS = ("libx265", "libsvtav1", "libaom-av1", "librav1e")


# ═══════════════════════════════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════════════════════════════
def _fmt_time(sec: float) -> str:
    """秒 → 人类可读。口径与被测脚本的 _fmt_time 一致，便于对照。"""
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.1f}s"
    total = int(sec)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def _label(text: str, width: int = 14) -> str:
    """标签按显示宽度补齐（CJK 记 2 列）。"""
    cells = sum(2 if ord(c) > 0x2E7F else 1 for c in text)
    return text + " " * max(1, width - cells) + ": "


def _run(cmd: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout, check=False)


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _has_encoder(codec: str) -> bool:
    """该编码器在本机 ffmpeg 里**真的能跑**（编译进来 ≠ 有卡可跑）。

    判据是拿一小段 lavfi 真编一帧 —— 与被测脚本的 _probe_encoder 同源思路。
    硬编（*_nvenc 等）在本机无卡时会失败，从而被正确标成 SKIP 而不是「跑出结果」。
    """
    try:
        r = _run([_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
                  "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1:d=0.1",
                  "-frames:v", "1", "-c:v", codec, "-f", "null", "-"], timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _has_filter(name: str) -> bool:
    try:
        r = _run([_ffmpeg_bin(), "-hide_banner", "-filters"], timeout=30)
        return re.search(rf"\s{name}\s", r.stdout or "") is not None
    except Exception:
        return False


def _probe_json(path: Path, extra: Optional[List[str]] = None) -> Dict:
    """一次 ffprobe 拿 JSON。失败返回 {}。"""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json"] + (extra or []) + [str(path)]
    try:
        r = _run(cmd, timeout=60)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def probe_source(path: Path) -> Dict:
    """探测源视频：分辨率/fps/时长/帧数/像素格式。失败抛 ValueError。"""
    d = _probe_json(path, ["-show_format", "-show_streams"])
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise ValueError(f"{path} 里没有视频流")

    fps = _frac(v.get("avg_frame_rate")) or _frac(v.get("r_frame_rate")) or 0.0

    # 帧数：优先容器自报；缺失按 duration×fps 估（只用于展示，不用于任何判据）
    n = _to_int(v.get("nb_frames"))
    dur = _to_float((d.get("format") or {}).get("duration")) or 0.0
    if n <= 0 and dur > 0 and fps > 0:
        n = int(dur * fps)

    return {
        "path": str(path), "width": _to_int(v.get("width")),
        "height": _to_int(v.get("height")), "fps": fps, "duration": dur,
        "nb_frames": n, "pix_fmt": v.get("pix_fmt") or "",
        "vcodec": v.get("codec_name") or "",
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "color_transfer": (v.get("color_transfer") or "").lower(),
        "size": _to_int((d.get("format") or {}).get("size")) or _file_size(path),
    }


def _frac(v) -> Optional[float]:
    try:
        if isinstance(v, (list, tuple)):
            return float(v[0]) / float(v[1]) if float(v[1]) else None
        s = str(v)
        if "/" in s:
            a, _, b = s.partition("/")
            return float(a) / float(b) if float(b) else None
        return float(s)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v or default)
    except (TypeError, ValueError):
        return default


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def count_frames(path: Path) -> int:
    """自己数输出帧数。**不用**被测脚本自报的帧数（见文件头红线 1）。

    `-count_frames` 会真解一遍码，耗时≈一次解码；只对抽样的质量门禁组合用，
    耗时统计用 `frames` 字段（被测脚本的 job.frame 是实际写出帧数，那部分可信）。
    """
    d = _probe_json(path, ["-select_streams", "v:0", "-count_frames",
                           "-show_entries", "stream=nb_read_frames"])
    st = (d.get("streams") or [{}])[0]
    return _to_int(st.get("nb_read_frames"))


# ═══════════════════════════════════════════════════════════════════
#  能力探测
# ═══════════════════════════════════════════════════════════════════
def detect_env(model_repo: Optional[Path] = None) -> Dict:
    """探测本机跑 benchmark 需要的能力。**每一项都实测**，不靠猜。"""
    import importlib.util
    has_torch = importlib.util.find_spec("torch") is not None
    cuda = False
    torch_ver = ""
    if has_torch:
        try:
            import torch
            torch_ver = torch.__version__
            cuda = bool(torch.cuda.is_available())
        except Exception:
            has_torch = False

    repo = Path(model_repo) if model_repo else ROOT.parent / "HDRTVNet-plus"
    codes_ok = (repo / "codes").is_dir()
    weights_ok = (repo / "pretrained_models" / "Ensemble_AGCM_LE.pth").is_file()

    cpu, cpu_src = _detect_cpu()
    mem_total, mem_avail, mem_src = _detect_memory()

    # 硬件能力：**真编一帧**才算可用。`ffmpeg -encoders` 列得出 nvenc 只说明
    # 编译时带了它；无卡时真编会失败，而被测脚本会静默降级（见 Combo.need_hw_codec）。
    hw_codecs = {c for c in ("hevc_nvenc", "av1_nvenc", "h264_nvenc",
                             "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv")
                 if _has_encoder(c)}
    has_nv = any(Path(p).exists() for p in
                 ("/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm"))
    hw_decode = has_nv and _has_encoder("hevc_nvenc")

    return {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "torch": has_torch, "torch_version": torch_ver, "cuda": cuda,
        "model_repo": str(repo), "model_codes": codes_ok, "model_weights": weights_ok,
        "model_ready": codes_ok and weights_ok,
        "filters": {n: _has_filter(n) for n in ("libvmaf", "tonemap", "zscale")},
        "hw_codecs": sorted(hw_codecs), "hw_decode": hw_decode,
        "has_nvidia_dev": has_nv,
        "cpu": cpu, "cpu_src": cpu_src,
        "mem_total_gb": mem_total, "mem_avail_gb": mem_avail, "mem_src": mem_src,
    }


def _detect_cpu() -> Tuple[int, str]:
    for f in ("/sys/fs/cgroup/cpu.max",):
        try:
            parts = Path(f).read_text().strip().split()
            if len(parts) == 2 and parts[0] != "max":
                q, p = int(parts[0]), int(parts[1])
                if q > 0 and p > 0:
                    return max(1, round(q / p)), f"cgroup ({f})"
        except Exception:
            pass
    try:
        n = len(os.sched_getaffinity(0))
        if n > 0:
            return n, "sched_getaffinity"
    except Exception:
        pass
    return max(1, os.cpu_count() or 1), "os.cpu_count"


def _detect_memory() -> Tuple[float, float, str]:
    try:
        info = Path("/proc/meminfo").read_text()
        kv = {}
        for line in info.splitlines():
            k, _, rest = line.partition(":")
            if rest:
                kv[k.strip()] = _to_int(rest.strip().split()[0]) * 1024
        total = kv.get("MemTotal", 0)
        avail = kv.get("MemAvailable", total)
        if total > 0:
            return total / 1024**3, avail / 1024**3, "/proc/meminfo"
    except Exception:
        pass
    return 0.0, 0.0, "unknown"


def print_env(env: Dict) -> None:
    print("── 环境与能力 ──")
    print(f"  ffmpeg      : {env['ffmpeg'] or '✗ 未找到'}")
    print(f"  ffprobe     : {env['ffprobe'] or '✗ 未找到'}")
    print(f"  CPU         : {env['cpu']} 逻辑核（{env['cpu_src']}）")
    print(f"  内存        : 总 {env['mem_total_gb']:.1f} GB / 可用 "
          f"{env['mem_avail_gb']:.1f} GB（{env['mem_src']}）")
    tv = f"✓ {env['torch_version']}" if env["torch"] else "✗ 未安装"
    print(f"  torch       : {tv}   CUDA: {'✓' if env['cuda'] else '✗ 无'}")
    mr = env["model_repo"]
    if env["model_ready"]:
        print(f"  模型仓库    : ✓ 就绪（{mr}）")
    else:
        miss = []
        if not env["model_codes"]:
            miss.append("codes/")
        if not env["model_weights"]:
            miss.append("pretrained_models/Ensemble_AGCM_LE.pth")
        print(f"  模型仓库    : ✗ 缺 {' + '.join(miss)}（{mr}）")
    f = env["filters"]
    print(f"  滤镜        : libvmaf {'✓' if f['libvmaf'] else '✗'}   "
          f"tonemap {'✓' if f['tonemap'] else '✗'}   "
          f"zscale {'✓' if f['zscale'] else '✗（HDR 判据走 tonemap 链）'}")
    hw = env.get("hw_codecs") or []
    print(f"  硬件编码    : {'✓ ' + ', '.join(hw) if hw else '✗ 无（真编一帧探测）'}"
          + ("" if hw else "  ← D 组会整体 SKIP"))
    print(f"  硬件解码    : {'✓ CUDA' if env.get('hw_decode') else '✗ 无'}"
          + ("" if env.get("hw_decode") else "  ← E 组解码轴会 SKIP"))
    print()
    print("── 层可用性 ──")
    print(f"  L1 编码器×质量  : {'✓ 可跑' if env['ffmpeg'] else '✗ 需要 ffmpeg'}"
          f"（--no-model，不需 torch）")
    print(f"  L2 执行路径     : {'✓' if env['model_ready'] else '✗ 缺模型仓库'}"
          f" 需 torch；分段并行还需素材 ≥{_SEGMENT_MIN_SECONDS:.0f}s")
    print(f"  L3 瓶颈分解     : ✓ 可跑（模型侧{'已就绪' if env['model_ready'] else '会 SKIP'}）")
    print(f"  C  并发/线程    : {'✓ 可跑' if env['model_ready'] else '✗ 需模型仓库'}"
          f"；⚠ 单文件并发 = 分段数，且实测非单调（workers=4 可能比 2 慢 30%）")
    print(f"  D  NVENC 组     : {'✓ 可跑（可用 ' + ', '.join(hw) + '）' if hw else '✗ 本机无可用硬编 → 整组 SKIP'}")
    print(f"  E  解码/容器/音频: 部分 ✓（容器轴可跑；解码轴{'可跑' if env.get('hw_decode') else '会 SKIP'}；"
          f"音频轴需源带音轨）")
    print(f"  G  4K 分块      : {'✓ 可跑' if env['model_ready'] else '✗ 需模型仓库'}"
          f"；⚠ CPU 上 4K 单帧约 89s，务必配 --frames 限制帧数")


# ═══════════════════════════════════════════════════════════════════
#  组合定义
# ═══════════════════════════════════════════════════════════════════
@dataclass
class Combo:
    """一个待测组合。

    expect_path 是**声明的预期路径**（'off' / 'segment' / 'workers'）；跑完拿
    stdout 里的标记行去核对。对不上就标 SKIP 并写明实际走了哪条路 —— 绝不把
    一个静默降级成 `'off'` 的组合当成「分段并行 vs 整条」的baseline 报出去。
    """
    cid: str
    layer: str
    desc: str
    args: List[str]
    expect_path: str = "off"
    group: str = ""              # 同组内的组合互为对照（用于算加速比）
    need_torch: bool = False
    need_model: bool = False
    min_duration: float = 0.0
    codecs: Tuple[str, ...] = ()  # 覆盖默认探测（如 hevc_nvenc 需单独探测）
    # ── 能力门槛（2026-10-08 实测加的，见下）──
    # 需要**真正可用**的硬件编码器（如 hevc_nvenc）。编译进来 ≠ 能跑：本机
    # `ffmpeg -encoders` 列得出 nvenc，但无 /dev/nvidia0、探测真编一帧会失败，
    # 而被测脚本会自动降级到 libx265 且 ffmpeg token **逐字不变** ⇒ 不加这道
    # 门槛的话，D 组会在无卡机上「测出」三组一模一样的耗时，看着像结论。
    need_hw_codec: str = ""
    # 需要真·硬件解码。实测无卡时 `--decode cuda` 的 `-vf` 链与 cpu **完全相同**
    # （build_decode_cmd 只在 decode=="cuda" 时加 hwdownload，而那步在降级后
    # 根本不会发生），所以这一轴在无卡机上同样是退化的。
    need_hw_decode: bool = False
    # 需要源带音轨。实测无音轨素材上 `--audio copy` 与 `--audio none` 下发的
    # 命令**逐字相同**（build_encode_cmd 的 `-map 1:a:0?` 分支要求 has_audio），
    # 不拦住就会测出「两个组合一模一样」，看起来像结论、其实什么都没测到。
    need_audio: bool = False
    # 已知的**预期失败**（不是基准跑挂了，而是这条轴要揭示的事实本身）。
    # 'oom' = 4K 整帧推理吃光内存被 OOM killer 杀（实测退出码 -9）。
    # 分块推理的存在理由正是「4K 整帧塞不进内存」，所以这一格显示 FAIL
    # 会让读者以为基准坏了。标成 EXPECTED_FAIL 并附实测数字。
    expect_fail: str = ""
    # 断言「产物实际由哪个编码器写出」。⚠ 这是**运行中降级**的唯一可靠判据。
    # 被测脚本在 NVENC 运行中途失败时会按 `--fallback-policy auto` 静默降级到
    # libx265 并**重跑成功**（rc=0），stdout 里出现
    # 「[提示] 编码器 … 不可用…已自动降级为 libx265」「↻ 重试：编码器 libx265」
    # 「⚠ 第 1 次尝试失败…按阶梯降级重试」—— 三条**前缀都不在**我的告警采集
    # 元组里（只采「提示：」「警告：」），所以收不到；而它概览里的
    # 「编码器      : hevc_nvenc」读的是 args.codec，降级后**从不回写**，
    # 所以那条也抓不到。
    # ⇒ 唯一硬证据是产物自己的 stream_tags=encoder（实测降级后是 libx265）。
    expect_codec: str = ""
    # 该组合会**复用源音轨**（`-map 1:a:0?` + `-c:a copy`）。这类组合要额外做
    # 帧数守恒检查：被测脚本带 `-shortest`，而**音轨比视频短**时视频会被音频
    # 长度截断（实测 test3.mp3 丢 4 帧）。丢了帧的那格处理量与其它格不同，
    # 耗时不可横向比。
    expect_audio_mux: bool = False


@dataclass
class Result:
    cid: str
    layer: str
    desc: str
    group: str
    status: str = "PENDING"      # OK / FAIL / SKIP / EXPECTED_FAIL
    skip_reason: str = ""
    fail_reason: str = ""
    wall_list: List[float] = field(default_factory=list)
    elapsed_list: List[float] = field(default_factory=list)  # 被测脚本自报，仅交叉核对
    frames: int = 0
    frames_from: str = ""         # 帧数来源：产物容器自报 / 源探测（mkv 常缺 nb_frames）
    actual_codec: str = ""        # 产物 ffprobe 的 encoder tag（名义≠实际时的硬证据）
    out_bytes: int = 0
    crf_sent: Optional[int] = None   # 真正下发的原生质量值（从 dry-run 抓）
    quality_axis: str = ""         # 下发的是哪根轴：crf / qp / cq（数字含义不同，不能混列）
    actual_path: str = ""
    expect_path: str = "off"      # 声明的预期路径，跑完核对用
    path_ok: bool = True
    warnings: List[str] = field(default_factory=list)
    out_path: str = ""
    cmd: str = ""
    encode_cmd_preview: str = ""   # dry-run 抓到的编码命令（可核对下发参数）

    @property
    def wall_min(self) -> Optional[float]:
        return min(self.wall_list) if self.wall_list else None

    @property
    def wall_med(self) -> Optional[float]:
        return statistics.median(self.wall_list) if self.wall_list else None

    @property
    def fps(self) -> Optional[float]:
        """按**实际输出帧数**算的吞吐（不是被测脚本自报的那个）。"""
        w = self.wall_min
        return (self.frames / w) if (w and self.frames) else None


# 追加到**每个**组合上的额外被测参数（目前只有 --frames）。用模块级而不是
# 逐个组合传参：它对所有层是同义的（限帧），而逐层传会漏掉某层时不易察觉。
_EXTRA_SCRIPT_ARGS: List[str] = []


def build_matrix(layers: List[str], crf_ref: int,
                 codec_list: Tuple[str, ...]) -> List[Combo]:
    """按层生成组合矩阵。"""
    out: List[Combo] = []

    # ── L1 编码器 × 质量（等质量锚定在 --crf-ref）──
    if "l1" in layers:
        for c in codec_list:
            out.append(Combo(
                cid=f"l1.{c}", layer="l1",
                desc=f"{c} 等质量(crf-ref={crf_ref})",
                args=["--codec", c, "--crf-ref", str(crf_ref), "--no-model"],
                group="l1.codec",
            ))

    # ── L2 执行路径（需要 torch + 模型；分段需要长素材）──
    if "l2" in layers:
        for sm, exp in (("off", "off"), ("segment", "segment"), ("workers", "workers")):
            out.append(Combo(
                cid=f"l2.split.{sm}", layer="l2",
                desc=f"单文件路径 {sm}",
                args=["--split-mode", sm, "--workers", "2", "--device", "cpu"],
                expect_path=exp, group="l2.split",
                need_torch=True, need_model=True,
                min_duration=_SEGMENT_MIN_SECONDS if sm == "segment" else 0.0,
            ))
        # 分块推理：整帧 vs 分块（分块≠整帧，每块各算自己的全局色调映射）
        for tile, ov in ((512, 0), (512, 64)):
            out.append(Combo(
                cid=f"l2.tile{tile}+{ov}", layer="l2",
                desc=f"分块推理 tile={tile} overlap={ov}",
                args=["--tile", str(tile), "--tile-overlap", str(ov),
                      "--split-mode", "off", "--device", "cpu"],
                group="l2.tile", need_torch=True, need_model=True,
            ))

    # ── L3 瓶颈分解（有模型 vs --no-model）──
    if "l3" in layers:
        out.append(Combo(
            cid="l3.nomodel", layer="l3", desc="仅编码链路（--no-model）",
            args=["--no-model"], group="l3.infer",
        ))
        out.append(Combo(
            cid="l3.full", layer="l3", desc="完整流程（含模型推理）",
            args=["--split-mode", "off", "--device", "cpu"],
            group="l3.infer", need_torch=True, need_model=True,
        ))

    # ── C 并发/线程阶梯 ──────────────────────────────────────────
    # 实测结论（8核/可用内存约4GB 的本机）：单文件下 `--workers N` 的语义是
    # **分段数 = 进程数**（convert_sdr_to_hdr.py 的 compute_parallelism 对
    # num_pending==1 返回 (N, cpu//N)），且**非单调** —— workers=4 实测比
    # workers=2 慢约 30%（过订：每进程各持一份 torch 模型，内存与CPU 都抢）。
    # ⚠ 本机内存画像 default_mem = libx265 的 1.2GB + NN 的 1.0GB = 2.2GB/任务，
    # 可用内存约 4GB ⇒ **workers 上限 2**，4 起来必被内存压制。
    if "c" in layers:
        for w in (1, 2, 4):
            out.append(Combo(
                cid=f"c.workers{w}", layer="c",
                desc=f"单文件并发 workers={w}",
                args=["--split-mode", "auto", "--workers", str(w),
                      "--device", "cpu"],
                # workers=1 时 decide_single_file_mode 直接返回 'off'（:4086）；
                # ≥2 且素材够长时 auto 会真走 segment（:4104）。写实际预期值而
                # 不是笼统的 'auto'：路径核对是「不符就 SKIP」，预期值写成
                # 不会发生的字符串会让这一轴永远 SKIP。
                expect_path="off" if w == 1 else "segment",
                group="c.workers",
                need_torch=True, need_model=True,
                min_duration=_SEGMENT_MIN_SECONDS if w > 1 else 0.0,
            ))
        # --threads 是**每任务 ffmpeg 线程数**，与 workers 正交；显式给了它，
        # compute_parallelism 的 workers_override 分支会不再自动算 workers。
        for t in (1, 2, 4, 8):
            out.append(Combo(
                cid=f"c.threads{t}", layer="c",
                desc=f"每任务 ffmpeg 线程 threads={t}",
                args=["--threads", str(t), "--split-mode", "off", "--no-model"],
                group="c.threads",
            ))

    # ── D NVENC 组（本机无卡 ⇒ 全部 SKIP，见 Combo.need_hw_codec 的说明）──
    # ⚠ 分组原则：**同 group 的每一格必须是同质量**、且要有一个「无调优」的首格
    # 当对照基准。否则倍数会拿「开了AQ」去比「没开 AQ」，或拿 cbr 去比 vbr，
    # 读起来像「调优带来的开销」—— 实测确认过这个误读。
    if "d" in layers:
        # 基线格（无任何 NVENC 调优）：d.*_tune 与 d.*_rc 的对照基准都指向它
        out.append(Combo(
            cid="d.plain", layer="d",
            desc=f"hevc_nvenc 等质量基线（crf-ref={crf_ref}，无调优）",
            args=["--codec", "hevc_nvenc", "--crf-ref", str(crf_ref),
                  "--no-model", "--fallback-policy", "auto"],
            group="d.tune", need_hw_codec="hevc_nvenc", expect_codec="hevc_nvenc",
        ))
        for codec in ("hevc_nvenc", "av1_nvenc"):
            out.append(Combo(
                cid=f"d.{codec}.cq", layer="d",
                desc=f"{codec} -cq 档位（等质量换算）",
                args=["--codec", codec, "--crf-ref", str(crf_ref),
                      "--no-model", "--fallback-policy", "auto"],
                group="d.codec", need_hw_codec=codec, expect_codec=codec,
            ))
        # NVENC 专属调优轴：必须与基线同 group 才能得到「相对无调优」的有效倍数。
        # 三者在非 NVENC 下只会被告警忽略，所以必须配 NVENC 编码器才测得到。
        out.append(Combo(
            cid="d.aq", layer="d", desc="hevc_nvenc + --nvenc-aq",
            args=["--codec", "hevc_nvenc", "--crf-ref", str(crf_ref),
                  "--no-model", "--nvenc-aq"],
            group="d.tune", need_hw_codec="hevc_nvenc", expect_codec="hevc_nvenc",
        ))
        out.append(Combo(
            cid="d.tune_uhq", layer="d", desc="hevc_nvenc + --nvenc-tune uhq",
            args=["--codec", "hevc_nvenc", "--crf-ref", str(crf_ref),
                  "--no-model", "--nvenc-tune", "uhq"],
            group="d.tune", need_hw_codec="hevc_nvenc", expect_codec="hevc_nvenc",
        ))
        # ⚠ 必须带 --crf-ref：constqp 之外的 rc 模式仍由 -cq 决定质量，而缺了
        # 锚定就会用被测脚本的 default_quality_for(codec)（恒为 cq 26，等于
        # ref=21 档）。这样 d.rc_cbr 与 d.plafin 的cq 就落在不同质量上，
        # 而 report 只并排列出两个 cq 数字，读者会误把质量差当成 rc 开销。
        out.append(Combo(
            cid="d.rc_cbr", layer="d",
            desc="hevc_nvenc + rc-mode cbr（与基线同为 crf-ref 锚定）",
            args=["--codec", "hevc_nvenc", "--crf-ref", str(crf_ref),
                  "--rc-mode", "cbr", "--bitrate", "8M", "--no-model"],
            group="d.tune", need_hw_codec="hevc_nvenc", expect_codec="hevc_nvenc",
        ))

    # ── E 解码后端 / 容器 / 音频 ────────────────────────────────
    # ⚠ 实测三条退化的轴（本机无卡 + fixture 无音轨），这里仍然列出来，但由
    # need_hw_decode / has_audio 门槛把它们标成 SKIP，而不是产出「同一件事跑
    # 两遍」的假对比：
    #   · --decode cuda/auto 无卡时 -vf 链与 cpu **逐字相同**
    #   · mkv 与 mp4 **都保住** HDR10 静态元数据（不是元数据轴，只是 mux 开销）
    #   · 无音轨素材上 --audio copy 与 none 的命令**逐字相同**
    if "e" in layers:
        for dec in ("cpu", "cuda", "auto"):
            out.append(Combo(
                cid=f"e.decode.{dec}", layer="e",
                desc=f"解码后端 {dec}",
                args=["--decode", dec, "--no-model"],
                group="e.decode",
                need_hw_decode=(dec != "cpu"),
            ))
        for cont in (".mp4", ".mkv"):
            out.append(Combo(
                cid=f"e.container{cont}", layer="e",
                desc=f"容器 {cont}",
                args=["--container", cont, "--no-model"],
                group="e.container",
            ))
        for au in ("copy", "none"):
            out.append(Combo(
                cid=f"e.audio.{au}", layer="e",
                desc=f"音频 {au}",
                args=["--audio", au, "--no-model"],
                group="e.audio", need_audio=(au != "none"),
                expect_audio_mux=(au == "copy"),
            ))

    # ── G 高分辨率（4K）：分块推理的真实主场 ─────────────────────
    # 1080p 整帧推理不吃内存，所以分块的意义要在 4K 上才体现得出来。
    # ⚠ 实测 4K 单帧 CPU 推理约 **89 秒**（tile 1024+128），故这一层必须配合
    # `--frames`限制帧数，否则 10 秒 480 帧的素材单次要 ~12 小时。
    # `--frames` 与分段互斥（:4079 会把 segment 降级成 off），故 G 层只测 tile。
    if "g" in layers:
        g_base = ["--device", "cpu", "--split-mode", "off"]
        out.append(Combo(
            cid="g.4k.tile0", layer="g", desc="4K 整帧推理（tile=0，已知 OOM）",
            args=g_base + ["--tile", "0"], group="g.tile",
            need_torch=True, need_model=True, expect_fail="oom",
        ))
        for tile, ov in ((1024, 128), (2048, 256)):
            out.append(Combo(
                cid=f"g.4k.tile{tile}", layer="g",
                desc=f"4K 分块推理 tile={tile} overlap={ov}",
                args=g_base + ["--tile", str(tile), "--tile-overlap", str(ov)],
                group="g.tile", need_torch=True, need_model=True,
            ))
    return out


def check_combo_preconditions(c: Combo, env: Dict, meta: Dict) -> Tuple[bool, str]:
    """跑之前先剪枝。**返回 (False, 原因) 时必须 SKIP 而不是硬跑**。

    这就是红线 2 的落地点：宁可显式 SKIP，也不要产出一个「名义分段、实则整条」
    的数据点让人误读。
    """
    if c.need_torch and not env["torch"]:
        return False, "本机未安装 torch"
    if c.need_model and not env["model_ready"]:
        return False, f"模型仓库未就绪（{env['model_repo']}）"
    # 硬件能力门槛：本机无卡时这两组轴会**静默塌成同一条路径**
    # （NVENC 自动降级 libx265 且 ffmpeg token 逐字不变；--decode cuda 的
    #  -vf 链与 cpu 完全相同）。不拦住就会产出「三组一样」的假对比，
    #  而那看起来像一个很确定的结论。
    if c.need_hw_codec and c.need_hw_codec not in (env.get("hw_codecs") or []):
        avail = ", ".join(env.get("hw_codecs") or []) or "（无）"
        return False, (f"本机没有真正可用的 {c.need_hw_codec}（本机可用：{avail}）。"
                       f"⚠ 判据必须是「**集合不含所需编码器**」而不是「集合为空」——"
                       f"在 AMD/Intel 机或部分 NVENC 机上集合非空但仍没有该编码器，"
                       f"那样组合会被放行、然后静默降级到 libx265，测出"
                       f"「几组一模一样」的耗时且看不出问题"
                       f"（`ffmpeg -encoders` 列得出 ≠ 有卡能编）")
    if c.need_hw_decode and not env.get("hw_decode"):
        return False, ("本机没有可用的 CUDA 硬解（实测 --decode cuda 会静默回退为"
                       "软件解码，-vf 链与 cpu 逐字相同）")
    if c.need_audio and not meta.get("has_audio"):
        return False, ("源无音轨：`--audio copy` 与 `--audio none` 下发的命令逐字相同"
                       "（build_encode_cmd 的 -map 1:a:0? 分支要求 has_audio），"
                       "测这一轴没有意义")
    # 4K 整帧推理是**已知的 OOM**（实测退出码 -9 = 被 OOM killer 杀）。
    # 它不是「基准跑挂了」，而是这条轴要回答的问题本身：分块的存在理由就是
    # 「4K 整帧塞不进内存」。标成 EXPECTED_FAIL 并写明实测数字，好过让它
    # 显示成一个来路不明的 FAIL。
    if c.expect_fail == "oom":
        return True, ""
    # 分段轴与限帧互斥：被测脚本 :4079 会把 segment 降级成 off。与其跑出一个
    # 「名义 workers=2、实则整条串行」的数据点，不如直接说清为什么测不了。
    if (c.expect_path != "off" and _EXTRA_SCRIPT_ARGS
            and "--frames" in _EXTRA_SCRIPT_ARGS):
        return False, ("用了 --frames ⇒ 分段并行会被降级成整条处理"
                       "（被测脚本 :4079 的截断/分段互斥），"
                       "这一轴在限帧下测不到")
    if c.min_duration and meta["duration"] < c.min_duration:
        return False, (f"素材 {meta['duration']:.1f}s < {c.min_duration:.0f}s："
                       f"分段并行的时长阈值 _SEGMENT_MIN_SECONDS="
                       f"{_SEGMENT_MIN_SECONDS:.0f}，短于此会被静默降级成整条处理")
    # 质量锚定断言（红线 3）：非表内编码器会静默按等体积换算
    if "--codec" in c.args:
        codec = c.args[c.args.index("--codec") + 1]
        if codec not in CRF.QUALITY_MAP:
            return False, (f"{codec} 不在 convert_crf.QUALITY_MAP 里，会静默回退到"
                           f"等体积(SIZE_MAP)口径 —— 那不是等质量，拒绝测量")
    return True, ""


# ═══════════════════════════════════════════════════════════════════
#  单次执行
# ═══════════════════════════════════════════════════════════════════
def resolve_native_quality(args: List[str],
                           tag: str = "crfprobe") -> Tuple[Optional[int], str, str]:
    """从 `--dry-run` 抓真正下发的原生质量值。

    返回 (值, 轴名, 完整编码命令)，轴名 ∈ crf / qp / cq / ""。
    ⚠ 轴名必须一并返回：**不同编码器下发的数字不可直接横向比较**
    （libx265 下发 -crf 21、librav1e 下发 -qp 64、NVENC 下发 -cq 26，
    三者量纲完全不同）。把它们并排写成「质量值 21 / 64」会误导读者以为
    64 那个质量更差 —— 实际它只是刻度不同。

    为什么必须实抓：ref=21 处没有编码器是恒等映射（libx265→20.74、
    libsvtav1→28.96…）。报告里若写「crf 21」就是把等质量说成了字面值 ——
    而「看起来在测其实没生效」的假绿正是本仓反复吃过的亏。

    ⚠ **输出路径必须是一个从未存在过的文件名**（并先 unlink）。实测踩过的坑：
    复用某个已存在的输出路径时，被测脚本的 `collect_jobs` 会把该 job 标成
    `skipped`，dry-run 直接打印「⏭ 跳过」而**一条 ffmpeg 命令都不打印**
    ⇒ 这里返回 (None, "", "")，报告里的「质量值」列就成了 `—`，而表面看
    只是「这一列没数据」，不会报错。这类静默失效最难查。

    ⚠ 探针文件名带 **PID**：并发跑两个 bench 实例时（实测审查 agent 与主流程
    同时跑）若共用同一个探针路径，会互删对方的探针文件，于是抓取随机失败、
    报告里偶发出现「质量值 —」。这类**偶发**失败比稳定失败更难查。
    """
    out = temp_root() / "bench_sdr_to_hdr" / f"_{tag}_{os.getpid()}_probe.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass
    try:
        r = _run([sys.executable, str(ROOT / "convert_sdr_to_hdr.py"),
                  "-i", str(args[0]), "-o", str(out), "--overwrite", "--dry-run"]
                 + args[1:], timeout=300)
    except Exception:
        return None, "", ""
    text = r.stdout or ""
    # 抓**编码命令**（dry-run 按固定顺序打印两条：① 解码 ② 编码；见被测脚本
    # print_plan 的「① 解码 →/ ② 编码 →」）。
    #
    # ⚠ 这里**不能**用「命令行里不含 -x265-params」来区分两条 —— 实测踩过：
    # libx265 的**编码命令必然含** -x265-params（HDR 静态元数据走这条选项），
    # 那个过滤条件会把编码命令一起排掉，于是永远抓不到 -crf。判据只能是
    # 「是不是第 2 条」/「有没有 -c:v」。
    enc_lines = [ln.strip() for ln in text.splitlines()
                 if "命令: ffmpeg" in ln and "-c:v" in ln]
    for line in enc_lines:
        m = re.search(r"-crf (\d+)", line)
        if m:
            return int(m.group(1)), "crf", line
        # ⚠ 不是所有编码器都下发 -crf：librav1e 只有 -qp（实测 ref=21 → `-qp 64`），
        # NVENC 走 -cq。只认 -crf 会让这些组合的「质量值」列空着 —— 而空格与
        # 「没测到」在报告里长得一样，属于会误导人的静默缺失。
        m_qp = re.search(r"-qp (\d+)", line)
        if m_qp:
            return int(m_qp.group(1)), "qp", line
        m_cq = re.search(r"-cq (\d+)", line)
        if m_cq:
            return int(m_cq.group(1)), "cq", line
    if enc_lines:
        return None, "", enc_lines[0]      # 有编码命令但没质量 token
    return None, "", ""


def resolve_actual_path(stdout: str) -> Tuple[str, str]:
    """从 stdout 判定实际走了哪条路径。返回 (path, 命中的标记原文)。"""
    m_seg = _RE_SEGMENT.search(stdout)
    if m_seg:
        return "segment", f"分段并行：{m_seg.group(1)} 段"
    m_mp = _RE_MULTIPROC.search(stdout)
    if m_mp:
        return "workers", f"单解码 + {m_mp.group(1)} 推理进程"
    if "已按整条处理" in stdout:
        return "off", "已按整条处理（被降级）"
    return "off", ""


def run_once(combo: Combo, src: Path, work: Path, timeout: int) -> Tuple[int, str, float]:
    """跑一次，返回 (rc, stdout+stderr, 墙钟秒)。

    墙钟用 `time.monotonic()` 且**包含** Python 解释器启动与模型加载 —— 这是
    「用户按下回车到拿到文件」的真实口径。脚本自报的 elapsed 不含模型加载、
    且三条路径口径不一致（见红线 1 与 memory），故只作交叉核对。
    """
    out = work / f"{combo.cid}.mp4"
    # 跑前先删：否则被测脚本的 collect_jobs 会把它标成 skipped、不重编，
    # 于是「秒返回」被误当成「很快」—— 一个只删一个文件就造出来的假加速比。
    #
    # ⚠ 必须连带删掉**同名的其它扩展名**：`--container .mkv` 会让被测脚本把
    # 扩展名从 .mp4 换成 .mkv（实测 e.container.mkv 的产物是 ….mkv）。只删
    # .mp4 的话，第二轮跑的是上一轮的 .mkv 残留 ⇒ 变成 skipped ⇒ 又一个假样本。
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        stale = work / f"{combo.cid}{ext}"
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass
    argv = [sys.executable, str(ROOT / "convert_sdr_to_hdr.py"),
            "-i", str(src), "-o", str(out), "--overwrite"] + combo.args + _EXTRA_SCRIPT_ARGS

    t0 = time.monotonic()
    try:
        r = _run(argv, timeout=timeout)
        rc, text = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, text = 124, f"[bench] 超时（>{timeout}s）"
    return rc, text, time.monotonic() - t0


def execute(combo: Combo, res: Result, src: Path, meta: Dict, env: Dict,
            work: Path, repeats: int, warmup: int, timeout: int) -> None:
    """执行一个组合的全部重复，返回填好的 res。"""
    res.expect_path = combo.expect_path
    ok, why = check_combo_preconditions(combo, env, meta)
    if not ok:
        res.status = "SKIP"
        res.skip_reason = why
        return

    argv = [sys.executable, str(ROOT / "convert_sdr_to_hdr.py"),
            "-i", str(src), "-o", str(work / f"{combo.cid}.mp4")] + combo.args
    res.cmd = " ".join(argv)

    # 预热：不计入统计。目的是让磁盘缓存与首次 import 的成本不落在第一个样本上。
    for _ in range(warmup):
        run_once(combo, src, work, timeout)

    # 质量值实抓（只抓一次，换算与重复无关）。⚠ dry-run 用的是**独立输出路径**，
    # 见 resolve_native_quality 的注释：复用已存在路径会让它静默跳过而不打印命令。
    res.crf_sent, res.quality_axis, res.encode_cmd_preview = resolve_native_quality(
        [str(src)] + combo.args + _EXTRA_SCRIPT_ARGS, tag=combo.cid.replace(".", "_"))

    for i in range(repeats):
        rc, text, wall = run_once(combo, src, work, timeout)
        if rc != 0:
            # 预期失败（当前只有 4K 整帧 OOM）：确认症状符合就收下，不算基准失败。
            # ⚠ 必须核对**症状**而不只是「失败了」：否则一个真 bug（比如路径写错）
            # 也会被当成 OOM 而静默放过 —— 那正是「让失效可见」要防的方向。
            if combo.expect_fail == "oom" and rc == -9:
                res.status = "EXPECTED_FAIL"
                res.fail_reason = ("整帧推理被 OOM killer 杀掉（退出码 -9）—— "
                                   "这正是分块存在的理由，不是基准故障")
                return
            res.status = "FAIL"
            tail = [ln for ln in text.splitlines() if ln.strip()][-4:]
            if combo.expect_fail:
                res.fail_reason = (f"预期失败（{combo.expect_fail}）但症状不符："
                                   f"退出码 {rc} —— " + " ⏐ ".join(tail)[:260])
            else:
                res.fail_reason = f"退出码 {rc}：" + " ⏐ ".join(tail)[:300]
            return
        # 「本次是否真的重编了」的判据（防秒返回的假加速比）。
        # ⚠ 不能用「文本里有没有『跳过』」判断 —— 实测踩过：正常输出里也有
        # 「模型仓库: 跳过检查（--no-model）」这类**正常提示**，粗暴的 `in text`
        # 会把每次成功运行都误判成 FAIL（4/4 全红，而产物全都正常生成了）。
        # 可靠判据是这两个硬信号：
        #   · 真跳过 → 没有「✔ 完成」行，且汇总行是「完成 0 … 跳过 1」
        #   · 真跑成 → 必有「✔ 完成，用时 Xs」
        m_done = _RE_SUMMARY_DONE.search(text)
        m_skip = _RE_SUMMARY_SKIP.search(text)
        really_ran = bool(_RE_DONE_MARK.search(text))
        if m_done and m_done.group(1) == "0" and (m_skip and m_skip.group(1) != "0"):
            res.status = "FAIL"
            res.fail_reason = ("被测脚本汇总为「完成 0、跳过 "
                               f"{m_skip.group(1)}」—— 本次没有真正重编"
                               "（产物已存在且未被 --overwrite 覆盖），"
                               "不是有效的耗时样本")
            return
        if not really_ran:
            res.status = "FAIL"
            res.fail_reason = ("输出里没有「✔ 完成」行，无法确认本次真的重编了"
                               "（既没成功也没被识别为跳过）")
            return
        res.wall_list.append(wall)

        m = _RE_DONE_ELAPSED.search(text)
        if m:
            res.elapsed_list.append(_parse_human_time(m.group(1)))
        # 产物真实路径以 stdout 的「输出文件」行为准（--container 会改扩展名，
        # 猜不出来）。多次重复时以第一次为准即可 —— 参数不变则路径不变。
        if i == 0:
            m_out = _RE_OUT_FILE.search(text)
            if m_out:
                res.out_path = m_out.group(1)
        p, mark = resolve_actual_path(text)
        if i == 0:
            res.actual_path = p
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith(("提示：", "警告：")):
                if s not in res.warnings:
                    res.warnings.append(s)

    # 路径生效核对（红线 2）
    res.path_ok = (res.actual_path == combo.expect_path)
    if not res.path_ok:
        res.status = "SKIP"
        res.skip_reason = (f"实际走了 {res.actual_path!r} 而非预期的 "
                           f"{combo.expect_path!r}（脚本按设计静默降级："
                           f"时长阈值 / 截断互斥 / 设备限制）")
        return

    # 产物校验：以 stdout 抓到的真实路径为准（`--container` 会改扩展名，见
    # _RE_OUT_FILE 的注释）。抓不到才回退到按 cid 推的路径。
    out = Path(res.out_path) if res.out_path else work / f"{combo.cid}.mp4"
    if not out.is_file():
        # 兜底再查一次按 cid 命名的文件：stdout 的解析万一漏了，不能因此把
        # 一次成功的运行判成 FAIL——但也不能因为找到同名文件就放过（见下）。
        guess = work / f"{combo.cid}.mp4"
        if guess.is_file():
            out = guess
        else:
            res.status = "FAIL"
            res.fail_reason = (f"退出码 0 且报告了 ✔ 完成，但产物不存在"
                               f"（找过 {out} 与 {guess}）")
            return
    res.out_path = str(out)
    res.out_bytes = _file_size(out)

    # 「实际写出的编码器」断言（D 组在有卡机上的关键防线，见 Combo.expect_codec）。
    # 只在声明了 expect_codec 时查，避免给每个组合都多跑一次 ffprobe。
    if combo.expect_codec:
        tag_codec = _probe_encoder_tag(out)
        res.actual_codec = tag_codec or ""
        if not tag_codec:
            res.status = "FAIL"
            res.fail_reason = ("产物没有 stream_tags=encoder，无法确认实际使用的"
                               f"编码器（期望 {combo.expect_codec}）")
            return
        if tag_codec != combo.expect_codec:
            # 这就是「名义 NVENC、实际 libx265」——耗时与质量两列同时失真，
            # 而报告原本会把它当一个正常的 NVENC 数据点。
            res.status = "FAIL"
            res.fail_reason = (f"**名义 {combo.expect_codec}、实际写出的是 "
                               f"{tag_codec}**（运行中降级）。"
                               f"这一格测到的不是 {combo.expect_codec} 的耗时，"
                               f"不能进报告")
            return

    # ⚠ 帧数守恒检查（只对映射了音轨的组合有意义）。
    # **这条检查在 2026-09 仍是必需的**（当时被测脚本在音轨偏短时会静默丢帧）：
    #   `--audio copy` 带 `-shortest`，而音轨比视频短时视频会被音频长度截断
    #   ——test3.mp4（视频 302 帧 / 10.067s、音轨 10.007s）曾丢到 **299 帧**，
    #   而 `--audio none` 是完整的 302 帧。
    # ⚠ 2026-10-09 起被测脚本已**自动降级补静音**（阈值 1 帧），这类丢帧应当不再发生；
    #   但**保留本检查作为回归哨兵**：若将来阈值被调高、或降级路径再次失效，
    #   它会立刻把「静默丢帧」变成响亮的 FAIL，而不是一组看着正常的耗时数字。
    # ⇒两格耗时不可直接横向比（处理帧数可能不同），报告必须点出来，
    #   否则「copy 与 none 耗时相同」会被读成「音轨处理零开销」。
    n_out = _probe_frames_metadata(out)
    n_src = _to_int(meta.get("nb_frames"))
    # ⚠ 判据用**绝对帧数**而不是百分比。曾用「丢帧 >2% 才判失败」，结果
    # test3.mp4 实测丢 4 帧 = 1.32% 被归入「尾帧舍入」而放过—— 但同一逻辑在
    # 10 万帧的素材上就是「丢 1300 帧也算舍入」。百分比阈值随素材长度漂移，
    # 判据必须与素材规模无关。
    # 容差取 2 帧：容器尾帧舍入实测就在这个量级（见 memory 里「时长不同 VMAF
    # 曲线不同」那类 ±1~2 帧的容器精度问题），超过它就该追问原因。
    _FRAME_TOL = 2
    if combo.expect_audio_mux and n_out and n_src and (n_src - n_out) > _FRAME_TOL:
        lost = n_src - n_out
        res.status = "FAIL"
        res.fail_reason = (f"帧数不守恒：源 {n_src} 帧 → 产物 {n_out} 帧"
                           f"（丢 {lost} 帧）。疑似被测脚本的 `-shortest` 因"
                           f"**音轨比视频短**而截断了视频（音频复用路径专有）。"
                           f"这一格处理量与其它格不同，耗时不可横向比")
        return

    res.frames = _probe_frames_metadata(out)
    if not res.frames:
        # mkv 容器通常**不带** nb_frames（实测 e.container.mkv 的 fps 列因此
        # 显示为空，而它其实跑了 20 秒）。回退用源探测帧数 —— 与被测脚本
        # print_summary 的做法一致，但这里只用于 fps 显示、不参与任何判据。
        res.frames = _to_int(meta.get("nb_frames"))
        res.frames_from = "源探测（产物容器无 nb_frames）"
    else:
        res.frames_from = "产物容器自报"
    res.status = "OK"


def _probe_encoder_tag(p: Path) -> str:
    """读产物里 ffmpeg 自己写的编码器标记，如 `Lavc63.1.102 libx265`。

    为什么需要它：判断「实际是不是用请求的编码器编码的」时，stdout 的
    「编码器：X」行不可信——它读的是 args.codec，而运行中降级**从不回写**
    args.codec；告警行也因为前缀不匹配而收不到。产物自身的 tag 是唯一
    不受降级影响的硬证据。
    """
    d = _probe_json(p, ["-select_streams", "v:0",
                       "-show_entries", "stream_tags=encoder"])
    st = (d.get("streams") or [{}])[0]
    tag = (st.get("tags") or {}).get("encoder") or ""
    # 形如 "Lavc63.1.102 libx265" → 取最后一段的编码器名
    parts = str(tag).split()
    return parts[-1] if parts else ""


def _probe_frames_metadata(p: Path) -> int:
    """用容器自报的 nb_frames（快）。⚠ 截断场景下容器可能报原片帧数，故只作参考；
    质量门禁里会用 `-count_frames` 复核。"""
    d = _probe_json(p, ["-select_streams", "v:0", "-show_entries", "stream=nb_frames"])
    st = (d.get("streams") or [{}])[0]
    return _to_int(st.get("nb_frames"))


def _parse_human_time(s: str) -> float:
    """解析被测脚本的 `3.5s` / `1h05m03s` / `2m03s`。"""
    s = s.strip()
    try:
        if "h" in s:
            h, rem = s.split("h", 1)
            m, sec = rem.split("m", 1)
            return int(h) * 3600 + int(m) * 60 + float(sec.rstrip("s"))
        if "m" in s:
            m, sec = s.split("m", 1)
            return int(m) * 60 + float(sec.rstrip("s"))
        return float(s.rstrip("s"))
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════
#  质量门禁（VMAF）
# ═══════════════════════════════════════════════════════════════════
def _tonemap_to_8bit(src: Path, dst: Path, frames: int, peak: float) -> bool:
    """PQ/BT.2020 → 8bit 中间件。

    为什么必须这一步：PQ 码值域上跑 VMAF 不报错但曲线压平（实测 crf 20~32 只差
    1.6 分），判据分辨率不足。⚠ 本机**无 zscale**，所以不能用
    calibrate_equal_quality.py:343 的 zscale 链；tonemap 滤镜原生吃 bt2020/PQ。
    """
    vf = (f"tonemap=tonemap=hable:desat=0:peak={peak},format=yuv420p")
    try:
        r = _run([_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-i", str(src), "-frames:v", str(frames), "-an", "-vf", vf,
                  "-c:v", "libx264", "-crf", str(_VMAF_REF_CRF),
                  "-pix_fmt", "yuv420p", str(dst)], timeout=1800)
        return r.returncode == 0 and dst.exists()
    except Exception:
        return False


def vmaf(ref8: Path, dist8: Path, frames: int) -> Optional[float]:
    """算 VMAF。**`n_subsample=1` 是硬要求**（实测 1→91.0、8→94.3，虚高 3.3 分）。

    ⚠ 参考侧与被测侧必须用**同一条** tonemap 链与同一个参考档（`_VMAF_REF_CRF`），
    否则中间件的量化误差会混进判据。
    """
    log = dist8.with_suffix(".vmaf.json")
    filt = ("libvmaf=feature=name=psnr_hvs:model=version=vmaf_v0.6.1:"
            f"log_fmt=json:log_path={log}:n_subsample=1")
    try:
        r = _run([_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
                  "-i", str(dist8), "-i", str(ref8), "-frames:v", str(frames),
                  "-lavfi", filt, "-f", "null", "-"], timeout=3600)
        if r.returncode != 0:
            return None
        data = json.loads(log.read_text(encoding="utf-8", errors="replace") or "{}")
        return float(data["pooled_metrics"]["vmaf"]["mean"])
    except Exception:
        return None


def quality_gate(results: List[Result], work: Path, frames: int,
                 peak: float, tol: float) -> List[Dict]:
    """对 OK 的组合跑质量门禁，返回门禁记录。

    只对 `--vmaf` 指定的组合跑（默认全跑，但 n_subsample=1 很慢，调用方通常
    只抽少数组合）。⚠ 参考池取**第一个** OK 组合的产物：它代表「这个基准里
    公认达标的那一档」，其余组合与之比 —— 但它**不代表**绝对质量，只保证
    「同质量轴下各编码器彼此接近」。
    """
    oks = [r for r in results if r.status == "OK" and r.out_path]
    if not oks:
        return []
    ref = oks[0]
    recs: List[Dict] = []
    ref8 = work / "vmaf_ref.mp4"
    if not _tonemap_to_8bit(Path(ref.out_path), ref8, frames, peak):
        return [{"cid": ref.cid, "status": "FAIL",
                 "detail": "参考侧 tonemap 到 8bit 失败（libvmaf 不可用或滤镜缺失）"}]

    for r in oks:
        if r is ref:
            recs.append({"cid": r.cid, "status": "REF", "vmaf": 100.0,
                         "detail": "参考池（基准里第一个 OK 组合）"})
            continue
        d8 = work / f"vmaf_{r.cid}.mp4"
        if not _tonemap_to_8bit(Path(r.out_path), d8, frames, peak):
            recs.append({"cid": r.cid, "status": "FAIL",
                         "detail": "被测侧 tonemap 到 8bit 失败"})
            continue
        score = vmaf(ref8, d8, frames)
        if score is None:
            recs.append({"cid": r.cid, "status": "FAIL", "detail": "libvmaf 无输出"})
            continue
        recs.append({"cid": r.cid, "status": "OK" if score >= 100.0 - tol else "WARN",
                     "vmaf": round(score, 3),
                     "detail": f"相对参考 {score:.2f}（阈值 -{tol}）"})
    return recs


# ═══════════════════════════════════════════════════════════════════
#  报告
# ═══════════════════════════════════════════════════════════════════
def render_table(results: List[Result], src: Path, meta: Dict,
                 env: Dict, crf_ref: int, repeats: int) -> str:
    L: List[str] = []
    L.append("── 基准结果 " + "─" * 46)
    L.append(f"  源      : {src}")
    L.append(f"  规格    : {meta['width']}x{meta['height']}  {meta['fps']:.3f}fps  "
             f"{meta['duration']:.2f}s  {meta['nb_frames']}帧  {meta['pix_fmt']}")
    L.append(f"  环境    : {env['cpu']}核 / 可用 {env['mem_avail_gb']:.1f}GB  "
             f"torch={'有' if env['torch'] else '无'}"
             f"{'（CUDA）' if env['cuda'] else ''}"
             f"  模型={'就绪' if env['model_ready'] else '缺'}")
    L.append(f"  口径    : 每组合 {repeats} 次取 min（另有预热不计）"
             f"；等质量锚定 --crf-ref {crf_ref}")
    L.append("")

    # 分组：同 group 内互为对照
    groups: Dict[str, List[Result]] = {}
    for r in results:
        groups.setdefault(r.group or r.layer, []).append(r)

    for g, rs in groups.items():
        L.append(f"  ▸ {g}")
        # 同组内第一个 OK 的组合作对照基准（相对它的倍数才有意义）。
        # ⚠ **必须把基准是谁印出来**：裸倍数 `(1.21x)` 读者无法判断分母是哪个组合。
        # 而且 base 会因状态而漂移：若语义首项 FAIL/SKIP，base 会静默变成下一格OK，
        # 同一组合的倍数能从 2.00x 变成 0.25x 而零提示（审查实测复现）。
        base = next((x for x in rs if x.status == "OK"), None)
        if base is None:
            L.append(f"    （本组无 OK 组合 ⇒ 无对照基准，以下只有各自的状态/原因）")
        else:
            # 若基准不是组内第一格，说明首格没能跑成 —— 明确提示，
            # 否则读者会把倍数当成「相对第一格」。
            first_ok_idx = next((i for i, x in enumerate(rs)
                                 if x.status == "OK"), None)
            note = f"基准 = {base.cid}"
            if first_ok_idx is not None and first_ok_idx > 0:
                skipped = [x.cid for x in rs[:first_ok_idx]
                           if x.status in ("FAIL", "SKIP", "EXPECTED_FAIL")]
                note += (f"（⚠ 组内首格未能跑成：{', '.join(skipped)} 已"
                         f"{'SKIP/FAIL' }，基准顺延）")
            L.append(f"    {note}")
        L.append(f"    {'组合':<26} {'状态':<5} {'墙钟min':>9} {'墙钟med':>9} "
                 f"{'fps':>7} {'体积(Δ相对基准)':>18} {'质量值':>7}  路径")
        for r in rs:
            if r.status == "SKIP":
                L.append(f"    {r.cid:<26} {'SKIP':<5} {'—':>9} {'—':>9} "
                         f"{'—':>7} {'—':>18} {'—':>7}  {r.skip_reason[:60]}")
                continue
            if r.status == "FAIL":
                L.append(f"    {r.cid:<26} {'FAIL':<5} {'—':>9} {'—':>9} "
                         f"{'—':>7} {'—':>18} {'—':>7}  {r.fail_reason[:60]}")
                continue
            if r.status == "EXPECTED_FAIL":
                # 预期失败要和真 FAIL 视觉上分开：它是「这条轴揭示的事实」，
                # 与「基准跑挂了」是两回事
                L.append(f"    {r.cid:<26} {'OOM':<5} {'—':>9} {'—':>9} "
                         f"{'—':>7} {'—':>18} {'—':>7}  {r.fail_reason[:60]}")
                continue
            wmin = f"{r.wall_min:.2f}s" if r.wall_min else "—"
            wmed = f"{r.wall_med:.2f}s" if r.wall_med else "—"
            fps = f"{r.fps:.2f}" if r.fps else "—"
            sz = _fmt_size_delta(r.out_bytes,
                                base.out_bytes if base is not None else None)
            crf = (f"{r.quality_axis} {r.crf_sent}"
                   if r.crf_sent is not None and r.quality_axis
                   else ("crf " + str(r.crf_sent) if r.crf_sent is not None else "—"))
            rel = ""
            if base is not None and base is not r and base.wall_min and r.wall_min:
                rel = f"  ({base.wall_min / r.wall_min:.2f}x)"
            L.append(f"    {r.cid:<26} {'OK':<5} {wmin:>9} {wmed:>9} {fps:>7} "
                     f"{sz:>18} {crf:>7}  {r.actual_path}{rel}")
        L.append("")

    warns = [(r.cid, w) for r in results for w in r.warnings]
    if warns:
        L.append("  ▸ 被测脚本的告警（原样转录）")
        for cid, w in warns:
            L.append(f"    {cid}: {w}")
        L.append("")

    n_ok = sum(1 for r in results if r.status == "OK")
    n_skip = sum(1 for r in results if r.status == "SKIP")
    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_exp = sum(1 for r in results if r.status == "EXPECTED_FAIL")
    L.append(f"  小结    : OK {n_ok}  SKIP {n_skip}  FAIL {n_fail}"
             + (f"  OOM(预期) {n_exp}" if n_exp else ""))
    if n_skip:
        L.append("  ⚠ 有组合被 SKIP —— SKIP 是「本机/本素材跑不出有意义的对比」，"
                 "不是「通过」。")
    if n_exp:
        L.append("  ℹ OOM(预期) 是 4K 整帧推理被 OOM killer 杀 —— 这是分块推理"
                 "存在的理由，不是基准故障。")

    # G 层才需要 tile 代价表（其它层不涉及 tile）
    if any(r.layer == "g" for r in results):
        note = render_tile_note(meta)
        if note:
            L.append("")
            L.append(note)
    return "\n".join(L)


def tile_cost_table(width: int, height: int,
                    tiles: Tuple[int, ...],
                    overlaps: Dict[int, int]) -> List[Dict]:
    """算各 tile 档在给定分辨率下的**真实**块数与总处理像素。

    ⚠ 这里的算法必须与被测脚本的 `HdrTvNetPlus.enhance()` 分块循环**同构**
    （convert_sdr_to_hdr.py:2382-2390）：

        for y0 in range(0, height, step):      step = tile - overlap
            y1 = min(y0 + tile, height)
            ph = y1 - y0
            ph_pad = ceil(ph / ALIGN) * ALIGN  # ALIGN = 8，**不是补到满 tile**

    ⇒ **边缘块不是满tile×tile**，只补到 8 的倍数。

    **Why 这条必须写对：** 我最初写成 `total = 块数 × tile²`（假设每块都是满
    tile），算出的 waste 是 1.4x/1.9x/3.0x，并把它当「实测」印进报告与docstring。
    对抗审查用「桩掉真实推理、统计实际喂给 net 的张量尺寸」证明：4K 下三档的
    **真实总处理量都是 10.51 Mpx、waste 都是 1.27x**（因为 3840 与 2160 在
    step=448/896/1792 下恰好都被补到整数块）。那个错误的公式连**结论方向**
    都搞反了 —— 它暗示「tile 越大浪费越多」，而真实情况是三档几乎相同。
    同理1080p 下真实是 1.27x/1.27x/1.07x（我算的是 1.90x/3.03x/4.05x）。

    **How to apply:** 任何「估算被测实现代价」的函数，写完必须与被测实现逐行
    对齐，而不是按直觉建模；估错会同时污染表格数字与文字结论，且看起来完全正常。
    """
    align = ALIGN
    rows: List[Dict] = []
    for t in tiles:
        if t <= 0:
            rows.append({"tile": 0, "overlap": 0, "blocks": 1,
                         "grid": "整帧",
                         "block_mpx": width * height / 1e6,
                         "total_mpx": width * height / 1e6,
                         "waste": 1.0})
            continue
        ov = overlaps.get(t, 0)
        step = t - ov
        if step <= 0:
            continue
        total = 0
        n = 0
        for y0 in range(0, height, step):
            y1 = min(y0 + t, height)
            ph = -(-(y1 - y0) // align) * align          # ceil(ph/8)*8
            for x0 in range(0, width, step):
                x1 = min(x0 + t, width)
                pw = -(-(x1 - x0) // align) * align      # ceil(pw/8)*8
                total += ph * pw
                n += 1
        ideal = width * height
        rows.append({"tile": t, "overlap": ov, "blocks": n,
                     "grid": f"{n}块",
                     "block_mpx": (total / n) / 1e6 if n else 0.0,
                     "total_mpx": total / 1e6,
                     "waste": total / ideal if ideal else 0.0})
    return rows


def _fmt_size_delta(cur: int, base: Optional[int]) -> str:
    """体积 + 与组内基准的字节级差值。

    Why:只印``1.7 MB`` 会把 48KB 的音轨差异四舍五入掉，读者会以为两个组合
    体积完全相同、进而怀疑「音频轴没测到东西」（实测 e.audio 两组合耗时 0.99x
    且体积都显示 1.7 MB，但实际差 48126 字节 —— 一查 ffprobe 就确认音轨确实
    被保留/丢弃，不是假绿）。
    """
    s = _fmt_size(cur) if cur else "—"
    if not cur or not base:
        return s
    d = cur - base
    if d == 0:
        return s + " (Δ0)"
    sign = "+" if d > 0 else "−"
    # 同时给字节数：小于 0.1KB 时 KB 也会显示成 0.0
    return f"{s} (Δ{sign}{abs(d):,}B)"


def render_tile_note(meta: Dict) -> str:
    """把 tile 的块数/总处理量表打进报告（G 层用）。

    ⚠ 文案必须与 `tile_cost_table` 的**真实**算法一致。原来这里写的是
    「块数↓ ≠ 更快：重叠越大重复计算越多」，那是从错误的 `n*t*t` 公式推出来的
    结论；按真实算法，4K 三档的总处理量几乎相同（见下表），所以**不能**再拿
    「重叠浪费」解释 tile 档位之间的速度差。
    """
    w, h = _to_int(meta.get("width")), _to_int(meta.get("height"))
    if not (w and h):
        return ""
    rows = tile_cost_table(w, h, (0, 512, 1024, 2048),
                           {512: 64, 1024: 128, 2048: 256})
    if not rows:
        return ""
    lines = [f"  ▸ tile 档位在 {w}x{h} 下的实际处理量"
             f"（算法与被测脚本的分块循环同构：边缘块补到 8 的倍数，不是补到满 tile）"]
    lines.append(f"    {'tile':>6} {'overlap':>8} {'块数':>6} {'均块Mpx':>9} "
                 f"{'总处理Mpx':>10} {'相对理想':>9}")
    for r in rows:
        lines.append(f"    {r['tile']:>6} {r['overlap']:>8} {str(r['blocks']):>6} "
                     f"{r['block_mpx']:>9.2f} {r['total_mpx']:>10.2f} "
                     f"{r['waste']:>8.2f}x")
    ideals = [r for r in rows if r["tile"] > 0]
    if ideals:
        lo = min(ideals, key=lambda r: r["total_mpx"])
        hi = max(ideals, key=lambda r: r["total_mpx"])
        spread = (hi["total_mpx"] / lo["total_mpx"]) if lo["total_mpx"] else 1.0
        lines.append(f"    ⇒ 选 tile 的判据是**总处理量最小**（本分辨率下最省的是 "
                     f"tile={lo['tile']}，{lo['total_mpx']:.2f} Mpx / "
                     f"{lo['waste']:.2f}x），不是块数最少。")
        if spread < 1.25:
            lines.append(f"    ⇒ ⚠ **各档总处理量差异仅 {spread:.2f}x** —— 所以"
                         f"tile 档位之间的速度差**不该**归因于重叠重复计算"
                         f"（实测同一命令重复 N 次的墙钟离散度与此同量级）。"
                         f"本分辨率下 {lo['tile']}~{hi['tile']} 各档处理量基本相同。")
    lines.append("    ⇒ 分块推理的目的是**压内存**（4K 整帧峰值内存可达 4GB+、"
                 "内存更紧的机器会被 OOM killer 杀），不是提速。")
    return "\n".join(lines)


def to_json(results: List[Result], src: Path, meta: Dict, env: Dict,
            crf_ref: int, repeats: int, gate: List[Dict], argv: List[str]) -> Dict:
    return {
        "bench_version": VERSION, "argv": argv,
        "source": {**meta, "path": str(src)},
        "env": env,
        "anchor": {"mode": CRF.get_quality_mode(), "crf_ref": crf_ref,
                   "note": "crf_sent 是真正下发的原生值；ref 处无编码器恒等"},
        "repeats": repeats,
        "results": [
            {"cid": r.cid, "layer": r.layer, "desc": r.desc, "group": r.group,
             "status": r.status, "skip_reason": r.skip_reason,
             "fail_reason": r.fail_reason,
             "wall_min": r.wall_min, "wall_median": r.wall_med,
             "wall_all": r.wall_list,
             "script_reported_elapsed": r.elapsed_list,
             "frames": r.frames, "frames_from": r.frames_from, "out_bytes": r.out_bytes,
             "crf_sent": r.crf_sent, "quality_axis": r.quality_axis,
             "actual_path": r.actual_path,
             "expect_path": r.expect_path,
             "path_ok": r.path_ok, "warnings": r.warnings,
             "out_path": r.out_path, "cmd": r.cmd}
            for r in results
        ],
        "quality_gate": gate,
        "note": ("墙钟含 Python 启动与模型加载；script_reported_elapsed 不含模型加载"
                 "且三条执行路径口径不一致，仅供交叉核对。SKIP 不等于通过。"),
    }


def render_md(data: Dict) -> str:
    env, src = data["env"], data["source"]
    L = [f"# convert_sdr_to_hdr.py 基准报告（bench v{data['bench_version']}）", ""]
    L.append(f"- 源：`{src['path']}` — {src['width']}x{src['height']}, "
             f"{src['fps']:.3f}fps, {src['duration']:.2f}s, {src['nb_frames']} 帧")
    L.append(f"- 环境：{env['cpu']} 核 / 可用 {env['mem_avail_gb']:.1f} GB / "
             f"torch {'有' if env['torch'] else '无'}"
             f"{' (CUDA)' if env['cuda'] else ''} / "
             f"模型 {'就绪' if env['model_ready'] else '缺失'}")
    L.append(f"- 口径：每组合 {data['repeats']} 次取 min；"
             f"等质量锚定 `--crf-ref {data['anchor']['crf_ref']}`"
             f"（{data['anchor']['mode']} 口径）")
    L.append("")
    L.append("| 组合 | 层 | 状态 | 墙钟 min | 墙钟 median | fps | 体积 | 下发质量值 | 路径 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in data["results"]:
        if r["status"] != "OK":
            note = r["skip_reason"] or r["fail_reason"]
            # OOM(预期) 用醒目措辞：它不是「基准跑挂了」，而是分块推理存在的理由
            st = r["status"]
            if st == "EXPECTED_FAIL":
                st = "**OOM（预期）**"
            else:
                st = f"**{st}**"
            L.append(f"| `{r['cid']}` | {r['layer']} | {st} | — | — | — "
                     f"| — | — | {note} |")
            continue
        _q = r["crf_sent"]
        crf = (f"{r.get('quality_axis') or 'crf'} {_q}"
               if _q is not None else "—")
        wmin = r["wall_min"]
        wmed = r["wall_median"]
        fps = (f"{r['frames'] / wmin:.2f}"
               if (wmin and r.get("frames")) else "—")
        L.append(f"| `{r['cid']}` | {r['layer']} | OK | "
                 f"{f'{wmin:.2f}s' if wmin else '—'} | "
                 f"{f'{wmed:.2f}s' if wmed else '—'} | {fps} | "
                 f"{_fmt_size(r['out_bytes']) if r['out_bytes'] else '—'} | "
                 f"{crf} | {r['actual_path']} |")
    L.append("")
    if data["quality_gate"]:
        L.append("## 质量门禁（VMAF，PQ 先 tonemap 到 8bit，n_subsample=1）")
        L.append("")
        L.append("| 组合 | 状态 | VMAF | 说明 |")
        L.append("|---|---|---|---|")
        for g in data["quality_gate"]:
            L.append(f"| `{g['cid']}` | {g['status']} | "
                     f"{g.get('vmaf', '—')} | {g.get('detail', '')} |")
        L.append("")
    L.append("## 读数须知")
    L.append("")
    L.append(f"- {data['note']}")
    L.append("- `crf_sent` 是真正下发给编码器的原生值，不是 `--crf-ref` 的数字："
             "该基准处**没有任何编码器是恒等映射**。")
    L.append("- SKIP 表示「本机/本素材跑不出有意义的对比」，**不是通过**。")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════════
#  装置自检（--selftest）
# ═══════════════════════════════════════════════════════════════════
def selftest() -> int:
    """验装置本身，不碰素材、不跑编码。CPU-only。

    逐条验的是「benchmark 会不会自己算错」——即红线 1/2/3 各自的守卫：
      ① 墙钟测量确实单调递增、能测出 sleep 的量级（不信被测脚本的自报数）
      ② 路径判定能把 stdout 里的三种标记正确解析出来（含「被降级」那一支）
      ③ 质量锚定断言会拒绝非表内编码器（而不是静默按等体积换算）
      ④ 统计取 min 而不是 mean（抗首个样本的冷启动离群）
    """
    fails = []

    def chk(label, got, want):
        if got != want:
            fails.append(f"{label}\n      得到 {got!r}\n      期望 {want!r}")

    def chk_true(label, cond, detail=""):
        if not cond:
            fails.append(f"{label}" + (f"\n      {detail}" if detail else ""))

    # ① 墙钟：测 sleep(0.4) 三次，min 应在 0.4~0.9 之间且三次都有值
    walls = []
    for _ in range(3):
        t0 = time.monotonic()
        time.sleep(0.4)
        walls.append(time.monotonic() - t0)
    chk("① 墙钟三次都有值", len(walls), 3)
    if not all(0.35 <= w <= 1.5 for w in walls):
        fails.append(f"① 墙钟量级不对：{walls}（应围绕 0.4s）")
    if not walls[0] >= min(walls):
        fails.append("① 墙钟不是单调可比（首个样本不该小于 min）")
    # min 应 <= median <= max（取 min 的前提）
    chk("① min<=median<=max", (min(walls) <= statistics.median(walls)
                                <= max(walls)), True)

    # ② 路径判定：三种标记 + 降级那一支
    chk("② 分段标记", resolve_actual_path("  ⚙ 分段并行：4 段 × 4 并发")[0], "segment")
    chk("② 多进程标记", resolve_actual_path("  ⚙ 单解码 + 3 推理进程 + 单编码（spawn）")[0],
        "workers")
    chk("② 降级标记（截断互斥）",
        resolve_actual_path("  ⚠ 已按整条处理。")[0], "off")
    chk("② 无标记即整条", resolve_actual_path("  推理      : 跳过")[0], "off")
    chk("② 分段段数解析", _RE_SEGMENT.search("分段并行：4 段 × 4 并发").group(1), "4")

    # ③ 质量锚定：libx265 在表内（应放行）、fake_codec 不在（应拒绝）
    meta = {"duration": 60.0}
    env = {"torch": True, "model_ready": True}
    ok_x265, _ = check_combo_preconditions(
        Combo("t", "l1", "", ["--codec", "libx265"]), env, meta)
    chk("③ libx265 在表内 → 放行", ok_x265, True)
    ok_fake, why_fake = check_combo_preconditions(
        Combo("t", "l1", "", ["--codec", "h264_qsv"]), env, meta)
    chk("③ 非表内编码器 → 拒绝（防静默按等体积换算）", ok_fake, False)
    if ok_fake:
        fails.append("③ 拒绝理由里应点明 QUALITY_MAP")
    chk("③ 拒绝原因点明表名", "QUALITY_MAP" in why_fake, True)

    # 时长阈值：8s 素材跑分段必须被 SKIP（这正是本机最容易误判的那条）
    ok_short, why_short = check_combo_preconditions(
        Combo("t", "l2", "", ["--codec", "libx265"], min_duration=20.0),
        env, {"duration": 8.08})
    chk("③ 8s 素材 + 分段 → SKIP", ok_short, False)
    chk("③ SKIP 原因点明阈值", "_SEGMENT_MIN_SECONDS" in why_short, True)
    ok_long, _ = check_combo_preconditions(
        Combo("t", "l2", "", ["--codec", "libx265"], min_duration=20.0),
        env, {"duration": 25.0})
    chk("③ 25s 素材 + 分段 → 放行", ok_long, True)

    # ④ 统计取 min：造一个离群样本，min 不该被它拉高
    r = Result(cid="x", layer="l", desc="", group="g")
    r.wall_list = [1.0, 1.1, 9.9]
    chk("④ 取 min 抗离群", r.wall_min, 1.0)
    chk("④ median 居中", round(r.wall_med, 2), 1.1)

    # ⑤ 时间解析：被测脚本的 _fmt_time 有 h/m/s 三种形态
    chk("⑤ 解析 3.5s", _parse_human_time("3.5s"), 3.5)
    chk("⑤ 解析 2m03s", _parse_human_time("2m03s"), 123.0)
    chk("⑤ 解析 1h05m03s", _parse_human_time("1h05m03s"), 3903.0)

    # ⑥ 质量锚定的核心事实：ref=21 处 libx265 不是恒等（防止有人「优化」成恒等）
    CRF.set_quality_mode("quality")
    v = CRF.from_x264_crf("libx265", 21)
    if v is not None and abs(v - 21.0) < 1e-6:
        fails.append("⑥ libx265 在 ref=21 成了恒等 —— 换算表变了？"
                     "报告里「crf_sent ≠ crf_ref」的说明需要同步更新")

    # ⑦ --dry-run 能真抓下发质量值（不跑编码，只拼命令）
    src = ROOT / "Accessory" / "temp" / "fixture_1080p.mp4"
    if src.exists():
        got, axis, cmdline = resolve_native_quality(
            [str(src), "--codec", "libx265", "--crf-ref", "21", "--no-model"],
            tag="selftest")
        if got is None:
            fails.append("⑦ resolve_native_quality 没抓到质量值（报告会显示「质量值 —」）"
                         + ("；抓到的命令=" + cmdline[:80] if cmdline else ""))
        elif axis != "crf":
            fails.append(f"⑦ libx265 应下发 -crf（轴=crf），实际轴={axis!r}")
        elif "命令: ffmpeg" not in cmdline:
            # ref=21 处 libx265 换算 20.74 → 取整就是 21，数字上与提示行相同，
            # 所以唯一能证明「抓的是编码命令而非提示行」的判据是命令原文本身。
            fails.append("⑦ 抓到的不是 ffmpeg 编码命令 —— 换算值可能被误读")

        # ⑦b librav1e 只认 -qp（实测 ref=21 → `-qp 64`）。若只认 -crf，它的
        # 「质量值」列会是空的 —— 而空格与「没测到」在报告里看起来一样。
        got_rav, axis_rav, _ = resolve_native_quality(
            [str(src), "--codec", "librav1e", "--crf-ref", "21", "--no-model"],
            tag="selftest_rav")
        chk("⑦b librav1e 的质量轴是 qp（不是 crf）", axis_rav, "qp")
        chk_true("⑦b librav1e 抓到了质量值", got_rav is not None,
                 f"得到 {got_rav!r}")
    else:
        print(f"  ℹ 跳过 ⑦（无 {src}）")

    # ⑧ 「跳过」必须被识别为无效样本（防秒返回的假加速比）。
    # ⚠ 这两条是**真实踩过的坑**：正常输出里也有「模型仓库: 跳过检查（--no-model）」
    # 「推理: 跳过（--no-model）」，而产物全都正常生成。判据必须是汇总行 +「✔ 完成」，
    # 不能是「文本里有没有『跳过』」。
    def _really_ran(text: str) -> bool:
        md, ms = _RE_SUMMARY_DONE.search(text), _RE_SUMMARY_SKIP.search(text)
        skipped_all = bool(md and md.group(1) == "0" and ms and ms.group(1) != "0")
        return (not skipped_all) and bool(_RE_DONE_MARK.search(text))

    normal = ("  模型仓库: 跳过检查（--no-model）\n"
              "推理        : 跳过（--no-model）\n"
              "  ✔ 完成，用时 6.4s\n"
              "汇总        : 完成 1  失败 0  跳过 0  累计用时 6.4s  均速 4fps  峰值 5fps")
    chk("⑧ 含『跳过』提示但真跑成了 → 仍算有效样本", _really_ran(normal), True)

    skipped = ("  模型仓库: 跳过检查（--no-model）\n"
               "待处理文件  : 0 个（另有 1 个跳过）\n"
               "⏭  跳过 fixture_1080p.mp4 (已存在，--overwrite 可覆盖)\n"
               "汇总        : 完成 0  失败 0  跳过 1  累计用时 0.0s")
    chk("⑧ 汇总『完成 0/跳过 1』→ 判为无效样本", _really_ran(skipped), False)

    chk("⑧ 真跳过不会被误判成 segment/workers",
        resolve_actual_path("⏭  跳过：已存在，--overwrite 可覆盖")[0], "off")

    # ⑩ 四类退化轴必须被拦住（否则产出「几组一模一样」的假对比）。
    # 这几条的判据都是「前置条件不满足时必须 False」，用假 env/meta 驱动。
    _env_nohw = {"torch": True, "model_ready": True,
                 "hw_codecs": [], "hw_decode": False}
    _env_hw = {"torch": True, "model_ready": True,
               "hw_codecs": ["hevc_nvenc"], "hw_decode": True}
    # D 组：无卡 ⇒ 必须 SKIP，且原因里要点明「降级」（否则读的人不知道测不了什么）
    ok_d, why_d = check_combo_preconditions(
        Combo("t", "d", "", ["--codec", "hevc_nvenc"], need_hw_codec="hevc_nvenc"),
        _env_nohw, {"duration": 60.0, "has_audio": True})
    chk("⑩ D 组无卡 → 拒绝测量", ok_d, False)
    chk_true("⑩ D 组拒绝原因点明静默降级", "降级" in why_d, why_d[:80])
    # D 组：有卡 ⇒ 放行（不能因为「本机没卡」写死成永远 SKIP，那会让 GPU 机白跑）
    ok_d2, _ = check_combo_preconditions(
        Combo("t", "d", "", ["--codec", "hevc_nvenc"], need_hw_codec="hevc_nvenc"),
        _env_hw, {"duration": 60.0, "has_audio": True})
    chk("⑩ D 组有卡 → 放行", ok_d2, True)

    # E 组解码：无硬解 ⇒ SKIP；有硬解 ⇒ 放行
    ok_e, _ = check_combo_preconditions(
        Combo("t", "e", "", ["--decode", "cuda"], need_hw_decode=True),
        _env_nohw, {"duration": 60.0, "has_audio": True})
    chk("⑩ E 解码轴无 CUDA → 拒绝测量", ok_e, False)
    ok_e2, _ = check_combo_preconditions(
        Combo("t", "e", "", ["--decode", "cuda"], need_hw_decode=True),
        _env_hw, {"duration": 60.0, "has_audio": True})
    chk("⑩ E 解码轴有 CUDA → 放行", ok_e2, True)

    # E 组音频：无音轨 ⇒ audio copy 那格拒绝（copy 与 none 命令逐字相同）
    ok_a, why_a = check_combo_preconditions(
        Combo("t", "e", "", ["--audio", "copy"], need_audio=True),
        _env_hw, {"duration": 60.0, "has_audio": False})
    chk("⑩ E 音频轴无音轨 → copy 那一格拒绝", ok_a, False)
    chk_true("⑩ 音频拒绝原因点明命令逐字相同", "逐字相同" in why_a, why_a[:80])
    ok_a2, _ = check_combo_preconditions(
        Combo("t", "e", "", ["--audio", "copy"], need_audio=True),
        _env_hw, {"duration": 60.0, "has_audio": True})
    chk("⑩ E 音频轴有音轨 → 放行", ok_a2, True)

    # C 组：限帧与分段互斥 ⇒ 带 --frames 时 expect_path='segment' 的那格必须拒绝。
    # 判别力关键：workers=1 的 expect_path 是 'off'，**不该**被这道闸拦住。
    _saved = list(_EXTRA_SCRIPT_ARGS)
    try:
        _EXTRA_SCRIPT_ARGS.extend(["--frames", "2"])
        ok_f, why_f = check_combo_preconditions(
            Combo("t", "c", "", [], expect_path="segment"),
            _env_hw, {"duration": 60.0, "has_audio": True})
        chk("⑩ C 分段轴 + --frames → 拒绝（会退化成 off）", ok_f, False)
        chk_true("⑩ 限帧拒绝原因点明互斥", "互斥" in why_f or "整条" in why_f, why_f[:80])
        ok_off = check_combo_preconditions(
            Combo("t", "c", "", [], expect_path="off"),
            _env_hw, {"duration": 60.0, "has_audio": True})
        chk("⑩ C 整条轴 + --frames → 仍放行（判别力）", ok_off[0], True)
    finally:
        _EXTRA_SCRIPT_ARGS[:] = _saved

    # ⑨ 产物真实路径必须来自 stdout，不能按扩展名猜。
    # 实测踩过：`--container .mkv` 让被测脚本把 .mp4 换成 .mkv，而基准按
    # 「cid + .mp4」去找 ⇒ 「退出码 0 但产物不存在」，把一次**成功**的运行
    # 判成 FAIL（假红）。这条断言锁住「以 stdout 为准」这个判据。
    txt_mkv = ("  ✔ 完成，用时 6.4s\n"
               "  输出文件    : /tmp/w/e.container.mkv\n"
               "汇总        : 完成 1  失败 0  跳过 0  累计用时 6.4s")
    m_of = _RE_OUT_FILE.search(txt_mkv)
    chk_true("⑨ 能从 stdout 抓到产物路径", m_of is not None, "正则没匹配上")
    if m_of:
        chk("⑨ 抓到的路径保留了真实扩展名", m_of.group(1).endswith(".mkv"), True)
    # 判别力：错的判据（按 cid 猜 .mp4）在 mkv 这一格必然找不到文件
    chk("⑨ 按 cid 猜 .mp4 对 mkv 组合是错的（回归对照）",
        "/tmp/w/e.container.mkv".endswith(".mp4"), False)
    _p, _m = resolve_actual_path("  ⚙ 分段并行：4 段 × 2 并发")
    chk("⑨ 分段标记仍能被路径判定抓到", _p, "segment")
    _p2, mark2 = resolve_actual_path(txt_mkv)
    chk("⑨ 单文件正常输出判定为 off", _p2, "off")

    # ⑩ tile_cost_table 必须与被测脚本的分块循环**同构**（边缘块补到 8 的
    #倍数，不是补到满 tile）。这条是回归锁：审查曾发现我按 `n*t*t` 建模，
    #算出 1.4x/1.9x/3.0x 的 waste 并当「实测」印进报告，而真实值三档都是 1.27x
    # —— 连结论方向都反了（误成「tile 越大越浪费」）。
    rows = tile_cost_table(3840, 2160, (0, 1024, 2048), {1024: 128, 2048: 256})
    chk("⑩ 4K tile=0 是一块", rows[0]["blocks"], 1)
    chk("⑩ 4K tile=1024 块数", rows[1]["blocks"], 15)
    chk("⑩ 4K tile=2048 块数", rows[2]["blocks"], 6)
    # 人工逐块复算4K/tile1024（也等于对抗审查独立算出的10.51 Mpx）：
    #   step=896。y 行：y0=0/896/1792 → y1=1024/1920/2160 → ph=1024/1024/368
    #   x 列：x0=0/896/1792/2688/3584 → x1=1024/1920/2816/3840/3840
    #         → pw=1024/1024/1024/1024/256（5 列，末列是 256 不是 1024）
    #   合计 = (1024+1024+368) × (1024×4+256) = 2416 × 4352 = 10,514,432
    chk_true("⑩ 4K tile=1024 总处理像素（手算 2416×4352）",
             abs(rows[1]["total_mpx"] * 1e6 - 2416 * 4352) < 1e3,
             f"实得 {rows[1]['total_mpx'] * 1e6:.0f}，手算 {2416 * 4352}")
    # 关键判别：错误的 n*t*t 模型会给 15×1024×1024=15,728,640（1.90x）
    chk_true("⑩ 与错误的 n*t*t 模型明确区分（该模型会给 15728640）",
             abs(rows[1]["total_mpx"] * 1e6 - 15 * 1024 * 1024) > 1e6,
             "两者过于接近，说明公式退回旧模型了")
    # 真实 waste 三档应几乎相同（审查实测同为 1.27x）
    w1 = rows[1]["waste"]
    w2 = rows[2]["waste"]
    chk_true("⑩ 4K 下各档 waste 接近（真实模型下差异应很小）",
             abs(w1 - w2) < 0.10,
             f"tile1024={w1:.2f}x tile2048={w2:.2f}x —— 差 {abs(w1-w2):.2f}"
             f"（若接近 1.12x 说明退回 n*t*t 的错误模型了）")

    # ⑪ 硬件门槛必须判「集合**含**所需编码器」，不是「集合非空」。
    # 审查实测：在 AMD 机（hw_codecs=['hevc_amf','h264_amf']）上，原判据会把
    # 5 个 hevc_nvenc 组合全部放行 → 静默降级 libx265 → 报告「几组一样」的耗时。
    env_amd = {"torch": True, "model_ready": True, "hw_codecs": ["hevc_amf"],
               "hw_decode": False}
    c_nv = Combo("t", "d", "", ["--codec", "hevc_nvenc"], need_hw_codec="hevc_nvenc")
    ok_amd, why_amd = check_combo_preconditions(c_nv, env_amd, {"duration": 60.0})
    chk("⑪ AMD 机（集合非空但无 hevc_nvenc）必须拒绝", ok_amd, False)
    chk_true("⑪ 拒绝原因里应列出本机实际可用的编码器",
             "hevc_amf" in why_amd, f"实际原因={why_amd[:90]}")
    # 本机可用集合含所需编码器时必须放行
    env_has = {"torch": True, "model_ready": True,
               "hw_codecs": ["hevc_amf", "hevc_nvenc"], "hw_decode": True}
    ok_has, _ = check_combo_preconditions(c_nv, env_has, {"duration": 60.0})
    chk("⑪ 可用集合含所需编码器 → 放行", ok_has, True)

    # ⑫ D 组同 group 的每一格都必须锚定同一个 crf_ref —— 缺了会让同组内
    # 落在不同质量上，而报告只并排列出 cq 数字，读者会把质量差当成调优开销。
    d_combos = [c for c in build_matrix(["d"], 21, L1_CODECS)]
    missing_ref = [c.cid for c in d_combos
                   if "--crf-ref" not in c.args and "--crf" not in c.args
                   and "--cq" not in c.args]
    chk("⑫ D 组每个组合都带质量锚定参数", missing_ref, [])
    # 且同 group 内锚定值必须一致
    by_group: Dict[str, set] = {}
    for c in d_combos:
        if "--crf-ref" in c.args:
            v = c.args[c.args.index("--crf-ref") + 1]
            by_group.setdefault(c.group, set()).add(v)
    for gname, vals in by_group.items():
        chk(f"⑫ D 组 {gname} 内锚定值一致", len(vals), 1)

    # ⑬ 体积列要带**字节级**Δ。只印 "1.7 MB" 会把 48KB 的音轨差四舍五入掉，
    # 读者会以为 e.audio 两组合体积相同 ⇒ 怀疑「这一轴没测到东西」。
    chk("⑬ 体积 Δ 渲染：比基准大", _fmt_size_delta(1814129, 1766003),
        "1.7 MB (Δ+48,126B)")
    chk("⑬ 体积 Δ 渲染：比基准小", _fmt_size_delta(1766003, 1814129),
        "1.7 MB (Δ−48,126B)")
    chk("⑬ 体积 Δ 渲染：无基准时只给体积", _fmt_size_delta(1814129, None), "1.7 MB")
    chk("⑬ 体积 Δ 渲染：相同则标 Δ0", _fmt_size_delta(1000, 1000), "1000.0 B (Δ0)")

    print()
    if fails:
        print(f"✗ {len(fails)} 项不通过：")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print(f"✓ 装置自检全部通过（{VERSION}）")
    return 0


# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="convert_sdr_to_hdr.py 的生产基准：同输入 + 同输出质量下，"
                    "测不同参数/执行路径的总耗时差异",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 装置自检（不碰素材、不跑编码，CPU-only）
  python3 Accessory/probe/bench_sdr_to_hdr.py --selftest

  # 环境与层可用性速查
  python3 Accessory/probe/bench_sdr_to_hdr.py --env

  # 快测（只跑编码路径，不需要 torch 与模型仓库）
  python3 Accessory/probe/bench_sdr_to_hdr.py -i in.mp4 --layers l1,l3

  # 全量（L2 的分段并行需要 ≥20s 素材）
  python3 Accessory/probe/bench_sdr_to_hdr.py -i long.mp4 --layers l1,l2,l3

  # 落报告 + 只对两个组合跑 VMAF 门禁
  python3 Accessory/probe/bench_sdr_to_hdr.py -i in.mp4 --json /tmp/b.json --md /tmp/b.md \\
      --vmaf l1.libx265,l1.libsvtav1

退出码：0 正常（含 SKIP）/ 1 有组合失败 / 2 参数或环境错误
""")
    p.add_argument("--input", "-i", help="源视频（SDR）")
    p.add_argument("--layers", default="l1,l3",
                   help="要跑哪些层，逗号分隔（默认 l1,l3；见文件头「分层矩阵」）")
    p.add_argument("--crf-ref", type=int, default=CRF.DEFAULT_REF if hasattr(CRF, "DEFAULT_REF") else 21,
                   metavar="N", help="等质量锚点（libx264 CRF 基准，默认 21）")
    p.add_argument("--quality-mode", choices=("quality", "size"), default="quality",
                   help="换算口径（默认 quality=等质量）")
    p.add_argument("--codecs", default="",
                   help="L1 的编码器列表（逗号分隔；默认 "
                        f"{','.join(L1_CODECS)}）。⚠ 不在 QUALITY_MAP 里的会被拒绝")
    p.add_argument("--repeats", type=int, default=3, help="每组合重复次数（默认 3，取 min）")
    p.add_argument("--warmup", type=int, default=1, help="预热次数（默认 1，不计统计）")
    p.add_argument("--timeout", type=int, default=3600, help="单次超时秒（默认 3600）")
    p.add_argument("--frames", type=int, default=None, metavar="N",
                   help="给被测脚本限帧（G 层必需：CPU 上 4K 单帧约 89 秒）。"
                        "⚠ 与分段并行互斥 —— 被测脚本会因此把 segment 降级成 off"
                        "（:4079），所以带 --frames 时 C/D组的分段轴会自动 SKIP")
    p.add_argument("--work", default=None,
                   help="工作目录（默认 Accessory/temp/bench_sdr_to_hdr）")
    p.add_argument("--keep", action="store_true", help="保留产物（默认跑完删）")
    p.add_argument("--json", help="结果 JSON 落盘路径")
    p.add_argument("--md", help="Markdown 报告落盘路径")
    p.add_argument("--vmaf", default="",
                   help="对指定组合跑 VMAF 质量门禁（逗号分隔的 cid；如 "
                        "l1.libx265,l1.libsvtav1）。⚠ n_subsample=1，很慢")
    p.add_argument("--vmaf-frames", type=int, default=60, help="VMAF 取前 N 帧（默认 60）")
    p.add_argument("--vmaf-peak", type=float, default=100.0,
                   help="tonemap 的 peak（默认 100，按素材实际峰值调）")
    p.add_argument("--vmaf-tol", type=float, default=_VMAF_TOL,
                   help=f"VMAF 允许的最大回退（默认 {_VMAF_TOL}，与 verify_equal_quality 同口径）")
    p.add_argument("--env", action="store_true", help="只打印环境与层可用性后退出")
    p.add_argument("--selftest", action="store_true", help="装置自检（不碰素材）")
    p.add_argument("--model-repo", default=None,
                   help=f"HDRTVNet-plus 仓库目录（默认 {ROOT.parent / 'HDRTVNet-plus'}）")

    g = p.add_argument_group("4K 素材制备（G 层用）")
    g.add_argument("--make-4k-from", default=None, metavar="SRC",
                   help="从一个长视频截取中段 10s 落盘并用它当源（G 层必需：4K 素材"
                        "比 1080p 慢约 4 倍，而 CPU 上单帧就要 ~89 秒）。"
                        "会打印产物路径，然后退出")
    g.add_argument("--make-4k-at", type=float, default=None, metavar="SEC",
                   help="截取起点秒（默认取时长中点，避开片头黑场）")
    return p.parse_args(argv)


def make_4k_fixture(src: Path, out: Path, seconds: float = 10.0,
                    at: Optional[float] = None) -> int:
    """从长视频截取中段 seconds 秒存成 H.264，供G 层（4K 分块推理）使用。

    为什么需要它：G 层要在**真实 4K** 上测分块推理，而仓库里现成的 fixture 是
    1080p/1 秒（见 memory）—— 1080p 整帧推理不吃内存，分块的意义体现不出来。
    真实 4K 素材在仓库外（`../input_videos/`），且动辄 100 秒/150MB，直接拿来跑
    一个基准要~12 小时，所以必须先截一段。

    截取参数：`-ss` 放-i 之前做输入侧快速定位；重编码用 libx264/CRF 22/veryfast，
    保证解码快（基准要测的是推理与编码，不是解码）。
    """
    if not src.is_file():
        print(f"[ERROR] 源视频不存在：{src}", file=sys.stderr)
        return 2
    try:
        meta = probe_source(src)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    dur = meta["duration"]
    if dur <= 0:
        print(f"[ERROR] 探测不到源时长（{dur}），无法选截取点", file=sys.stderr)
        return 2
    if dur < seconds + 2:
        print(f"[ERROR] 源只有 {dur:.1f}s，不足以截 {seconds:.0f}s（需 ≥{seconds + 2:.0f}s）",
              file=sys.stderr)
        return 2
    start = at if at is not None else max(0.0, (dur - seconds) / 2.0)

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    print(f"  源      : {src}")
    print(f"  规格    : {meta['width']}x{meta['height']}  {dur:.2f}s")
    print(f"  截取    : {start:.2f}s 起 {seconds:.0f}s → {out}")
    try:
        r = _run([_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{seconds:.2f}",
                  "-an", "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
                  "-pix_fmt", "yuv420p", str(out)], timeout=3600)
    except Exception as exc:
        print(f"[ERROR] 截取失败：{exc}", file=sys.stderr)
        return 2
    if r.returncode != 0 or not out.is_file():
        print(f"[ERROR] 截取失败：{(r.stderr or '').strip()[:300]}", file=sys.stderr)
        return 2

    m2 = probe_source(out)
    print(f"  ✓ 完成  : {m2['width']}x{m2['height']}  {m2['duration']:.2f}s  "
          f"{m2['nb_frames']} 帧  {_fmt_size(_file_size(out))}")
    print(f"\n用它跑 G 层（务必配 --frames，否则单次要数小时）：\n"
          f"  python3 {Path(__file__).name} -i '{out}' --layers g --frames 2")
    return 0


def main() -> int:
    args = parse_args()

    if args.selftest:
        return selftest()

    CRF.set_quality_mode(args.quality_mode)

    env = detect_env(Path(args.model_repo) if args.model_repo else None)
    if env["ffmpeg"] is None or env["ffprobe"] is None:
        print("[ERROR] 未找到 ffmpeg / ffprobe，无法做基准", file=sys.stderr)
        return 2

    if args.env:
        print_env(env)
        return 0

    # 4K 素材制备：造完就退出（不跑基准）
    if args.make_4k_from:
        work = Path(args.work) if args.work else \
            temp_root() / "bench_sdr_to_hdr" / f"run_{os.getpid()}"
        dst = work / "fixture_4k.mp4"
        return make_4k_fixture(Path(args.make_4k_from), dst,
                              seconds=10.0, at=args.make_4k_at)

    if not args.input:
        print("[ERROR] 需要 --input（或用 --env / --selftest）", file=sys.stderr)
        return 2
    src = Path(args.input)
    if not src.is_file():
        print(f"[ERROR] 源视频不存在：{src}", file=sys.stderr)
        return 2
    if args.repeats < 1 or args.warmup < 0:
        print("[ERROR] --repeats ≥1、--warmup ≥0", file=sys.stderr)
        return 2

    try:
        meta = probe_source(src)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    layers = [x.strip() for x in args.layers.split(",") if x.strip()]
    bad = [x for x in layers if x not in ("l1", "l2", "l3", "l4", "c", "d", "e", "g")]
    if bad:
        print(f"[ERROR] 未知层：{bad}（可用 l1,l2,l3,l4,c,d,e,g）", file=sys.stderr)
        return 2

    codec_list = tuple(x.strip() for x in args.codecs.split(",") if x.strip()) \
        if args.codecs else L1_CODECS

    # 工作目录**带 PID**：基准会删掉自己的产物来防止「跳过不重编」那个假样本，
    # 而两个并发实例的 cid 完全相同 ⇒ 共用目录会互删对方正在跑的产物，
    # 把对方变成 skipped（实测审查 agent 与主流程并行时踩到）。
    work = Path(args.work) if args.work else \
        temp_root() / "bench_sdr_to_hdr" / f"run_{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)

    if args.frames:
        if args.frames < 1:
            print("[ERROR] --frames 需 ≥1", file=sys.stderr)
            return 2
        _EXTRA_SCRIPT_ARGS.extend(["--frames", str(args.frames)])

    print_env(env)
    print(f"── 计划 ──\n  源        : {src}")
    print(f"  层: {', '.join(layers)}")
    print(f"  重复      : {args.repeats} 次取 min（预热 {args.warmup} 次不计）")
    print(f"  锚点      : --crf-ref {args.crf_ref}（{args.quality_mode} 口径）")
    if args.frames:
        print(f"  限帧      : --frames {args.frames}")
        # 实测：--frames/--duration 与分段并行互斥，被测脚本会把 segment 降级成
        # off（:4079）。不预告的话，用户会以为C 组测的是并发、其实是整条串行。
        if "c" in layers:
            print(f"  ⚠ --frames 与分段并行互斥 ⇒ **C 组的分段轴会被降级成 'off' 并标 SKIP**"
                  f"（被测脚本 :4079）。要测真分段请去掉 --frames 并用 ≥"
                  f"{_SEGMENT_MIN_SECONDS:.0f}s 素材。")
    print(f"  工作目录  : {work}")
    if any(c.min_duration for c in build_matrix(layers, args.crf_ref, codec_list)):
        if meta["duration"] < _SEGMENT_MIN_SECONDS:
            print(f"  ⚠ 素材只有 {meta['duration']:.1f}s < {_SEGMENT_MIN_SECONDS:.0f}s："
                  f"L2 的分段并行组合会被**静默降级成整条处理**，届时将标 SKIP"
                  f"（不会产出假的对比数据）")
    print()

    combos = build_matrix(layers, args.crf_ref, codec_list)
    results: List[Result] = []
    t_start = time.monotonic()
    for i, c in enumerate(combos, 1):
        res = Result(cid=c.cid, layer=c.layer, desc=c.desc, group=c.group)
        print(f"  [{i}/{len(combos)}] {c.cid:<26} {c.desc}", flush=True)
        execute(c, res, src, meta, env, work, args.repeats, args.warmup, args.timeout)
        if res.status == "OK":
            print(f"        → OK   墙钟min {res.wall_min:.2f}s  "
                  f"体积 {_fmt_size(res.out_bytes)}  路径 {res.actual_path}")
        elif res.status == "SKIP":
            print(f"        → SKIP {res.skip_reason}")
        else:
            print(f"        → FAIL {res.fail_reason}")
        results.append(res)

    # 质量门禁
    gate: List[Dict] = []
    if args.vmaf:
        if not (env["filters"]["libvmaf"] and env["filters"]["tonemap"]):
            print("\n  ⚠ 质量门禁需要 libvmaf + tonemap，本机不具备，跳过"
                  "（不是通过）")
        else:
            wanted = {x.strip() for x in args.vmaf.split(",") if x.strip()}
            sub = [r for r in results if r.cid in wanted and r.status == "OK"]
            if not sub:
                print(f"\n  ⚠ --vmaf 指定的组合里没有一个是 OK，无质量门禁可跑")
            else:
                print(f"\n  质量门禁（{len(sub)} 个组合，n_subsample=1，慢）…", flush=True)
                gate = quality_gate(sub, work, args.vmaf_frames,
                                    args.vmaf_peak, args.vmaf_tol)

    print()
    print(render_table(results, src, meta, env, args.crf_ref, args.repeats))
    if gate:
        print()
        print("  ▸ 质量门禁（VMAF，PQ 先 tonemap 到 8bit，n_subsample=1）")
        for g in gate:
            v = f"{g.get('vmaf'):.2f}" if isinstance(g.get("vmaf"), float) else "—"
            print(f"    {g['cid']:<26} {g['status']:<5} VMAF {v:>7}  {g.get('detail','')}")
        print("  ℹ 参考池是本基准里第一个 OK 组合 —— 保证「彼此接近」，"
              "不代表绝对质量。")

    total_wall = time.monotonic() - t_start

    if args.json or args.md:
        data = to_json(results, src, meta, env, args.crf_ref, args.repeats, gate,
                       sys.argv[1:])
        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n  JSON  : {args.json}")
        if args.md:
            Path(args.md).parent.mkdir(parents=True, exist_ok=True)
            Path(args.md).write_text(render_md(data), encoding="utf-8")
            print(f"  MD    : {args.md}")

    if not args.keep and not args.json:
        shutil.rmtree(work, ignore_errors=True)
        print(f"\n  （产物已清理：{work}；--keep 可保留）")
    else:
        print(f"\n  产物：{work}")

    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_skip = sum(1 for r in results if r.status == "SKIP")
    n_exp = sum(1 for r in results if r.status == "EXPECTED_FAIL")
    print(f"  总耗时：{_fmt_time(total_wall)}"
          + (f"（SKIP {n_skip} 个：见上表原因）" if n_skip else "")
          + (f"（OOM 预期 {n_exp} 个）" if n_exp else ""))
    # ⚠ 只有**真FAIL** 才算失败。EXPECTED_FAIL（4K 整帧 OOM）是这条轴要揭示的
    # 事实本身，SKIP 是本机跑不出对比 —— 两者都不该让基准报红，否则「G 层跑出
    # OOM」会被误读成「基准坏了」。
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())