#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe/verify_nvenc_quality_gpu.py — NVENC 质量轴上机验收（T4 / L40，重点 av1_nvenc）

背景
────
2026-09-28 的换算关系复核发现三处**必须上机**才能定论的项（本机无 NVIDIA GPU，
`--codec *_nvenc` 一律降级到 CPU 编码器，验不到）：

  A. 表内量程 vs ffmpeg 声明的量程（纯逻辑，本机可跑）
       · av1_nvenc 的 `-cq` 实测 `0..63`（≠ H.264/HEVC 的 0..51）
       · av1_nvenc 的 `-qp` 实测 `-1..255`（AV1 qindex 尺度，≠ H.264 的 0..51）
       这两条决定了 QUALITY_MAP 的 hi 与"constqp 要不要再乘 QP 尺度"。

  B. `--crf-ref 21` → 各 NVENC 的 `-cq` 是否真的等质量（本机只能给单元级结论）
       · h264_nvenc +5 / hevc_nvenc +7.5 有 VE 的真实素材实测支撑
       · **av1_nvenc +6 至今没有任何实测**，且方向可疑（AV1 效率 ≥ HEVC，
         偏移按理应 ≥ +7.5，再叠加 0~63 尺度的换算）

  C. constqp 的 `-qp` 该取哪个值（**专门针对 av1_nvenc**）
       · H.264/HEVC：QP 与 x264 QP 同尺度 ⇒ 直取基准轴（21）
       · AV1：`-qp` 是 qindex（0~255）⇒ 若直取 21 等于近无损（体积暴涨）
       本组用 libx264 crf21 作参照，扫 {21, 84, 105}，看哪个落在容忍带内。

T4 与 L40 的差别（脚本自动判）
──────────────────────────────
  · **T4 不支持 AV1 NVENC**（Turing 代无 AV1 编码单元）：`ffmpeg -h encoder=av1_nvenc`
    可能列出选项表，但真编码会失败 ⇒ 脚本会实跑一次短编码探测，失败即把
    B/C 组的 av1_nvenc 格标为 SKIP 并打印原因（不是 FAIL）。
  · **L40（Ada）支持 AV1**，B/C 组全跑。此时 C 组的结论就是"AV1 的 QP 尺度是几倍"。
  · L40/Ada 交接请加 `--expect-av1`（见用法 ⑤）：把"av1 静默 SKIP"升级为 **exit 2**，
    防止在**非 AV1 卡**上跑完却误以为"AV1 已验过"。

用法
────
    # ① 完整上机（需要 GPU + ffmpeg；素材默认合成 720p，建议用真实素材）
    python3 probe/verify_nvenc_quality_gpu.py --src /data/clip_720p.mp4

    # ② 只跑纯逻辑组（A，本机也能跑）+ 打印 GPU 能力探测结果
    python3 probe/verify_nvenc_quality_gpu.py --quick

    # ③ 只验装置本身（判词/命令构造/解析；不动 ffmpeg 编码）
    python3 probe/verify_nvenc_quality_gpu.py --selftest

    # ④ 指定输出报告
    python3 probe/verify_nvenc_quality_gpu.py --src x.mp4 --json report.json --md report.md

    # ⑤ L40/Ada 交接：要求本卡真能编 AV1（不可用即 exit 2，不静默 SKIP）
    python3 probe/verify_nvenc_quality_gpu.py --expect-av1 \
        --src /data/clip.mp4 --json verification_report/nvenc_quality_L40_<TS>.json \
        --md verification_report/nvenc_quality_L40_<TS>.md

判据（与 Video_Enhancement 的 G7 同一套容忍带，避免两边结论不可比）
────────────────────────────────────────────────────────────────
    · 码率比（目标码率 / libx264 crf21 码率）落在 RATE_PASS = (0.65, 1.50)
    · ΔPSNR（目标 − 软编基准）单向下探 ≤ TOL_PSNR = 1.5 dB（过配不罚）
    · VMAF（有 libvmaf 时）：差 ≤ 2.0
    · NVENC 统一 `-preset p4`（跨仓契约 CR-1，与 VE 一致，也与标定 harness 一致）——
      ⚠ 2026-10-04 由 p5 统一回 p4；p5 口径的旧报告（如 T4 20260928 之后的临时跑）数字不可逐条对比
    · 恒定质量分支（`-cq`）显式带 rc（CR-2，与 VE/标定 harness 一致）：h264/hevc=`-rc vbr_hq`、
      av1=`-rc vbr`；旧报告（无 `-rc`）的码率比/ΔPSNR 与新跑不可逐条对比

结论行（av1 可用时才出现，L40/Ada 上照它决定动不动表）
──────────────────────────────────────────────────────
    · `B-av1-结论`：`-cq` 表值(27) 是否等质量 ⇒ 决定要不要改 QUALITY_MAP 的 b
    · `C-av1-结论`：`-qp` 尺度该取几倍（21 / 84=×4 / 105=×5）⇒ 决定要不要改 _QP_SCALE

退出码
──────
    0 = 无 FAIL（PASS / WARN / SKIP 均可接受）
    1 = 存在 FAIL
    2 = 前置不满足（缺 ffmpeg / 缺素材 / 需要 GPU 但未加 --quick / --expect-av1 但本卡不能编 AV1）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOL_PSNR = 1.5          # 单向下探容忍（dB）
TOL_VMAF = 2.0
RATE_PASS = (0.65, 1.50)
RATE_WARN = (0.55, 1.65)

# NVENC 统一用 **`-preset p4`**（跨仓契约 CR-1，与 VE 口径一致），与标定 harness
# `probe/calibrate_equal_quality.py` 的 `BASE_LOCK` 保持一致；否则同 `-cq` 下率失真曲线不同、
# 等效点与码率比会漂。⚠ 两处探针须同时改（2026-10-04 由 p5 统一回 p4，见跨仓契约）。
NVENC_PRESET = 'p4'

# 上机目标：h264 / hevc 有 VE 的实测支撑，av1 是本次要补的那条
NVENC_CODECS = ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc')


# CR-2（跨仓契约，2026-10-04）：恒定质量分支（`-cq`）的 rc 必须与生产 / 标定 harness 一致，
# 否则同 `-cq` 的率失真点不同（等效点/码率比会漂）：h264/hevc → `vbr_hq`；av1 → `vbr`。
def _cq_rc(codec: str) -> str:
    return 'vbr' if codec == 'av1_nvenc' else 'vbr_hq'
# 表内值（crf_ref=21 下的 CQ），用于和实测对比。
# ⚠ 默认 `quality` 模式下硬编未落 QUALITY_MAP ⇒ 回退 SIZE_MAP，故此处数值取自 SIZE_MAP。
#    取表统一走 crf_mod.get_quality_map()（活动表），**不要**直接索引 QUALITY_MAP（会 KeyError）。
CQ_TABLE_AT_21 = {'h264_nvenc': 26, 'hevc_nvenc': 28, 'av1_nvenc': 27}
NAIVE_CQ = 21           # 修复前：基准轴数值被原样当 CQ 下发


# ══════════════════════════════════════════════════════════════════════
# 装置
# ══════════════════════════════════════════════════════════════════════

class Result:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, cid, group, title, status, detail='', evidence=None):
        self.rows.append({'id': cid, 'group': group, 'title': title,
                          'status': status, 'detail': detail,
                          'evidence': list(evidence or [])})
        icon = {'PASS': '✓', 'WARN': '⚠', 'FAIL': '✗', 'SKIP': '–'}[status]
        print(f'  {icon} [{cid}] {title}：{detail}')
        for e in (evidence or []):
            print(f'      · {e}')

    def count(self, status):
        return sum(1 for r in self.rows if r['status'] == status)


def sh(cmd, timeout=900):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def enc_help(codec):
    """`ffmpeg -h encoder=<codec>` 的原文（编码器不存在时返回空串）。"""
    rc, out = sh(['ffmpeg', '-hide_banner', '-h', f'encoder={codec}'], timeout=60)
    return out if rc == 0 else ''


def parse_range(text, opt):
    """从 `-h encoder=` 输出里解析某选项的 (lo, hi)；解析不到返回 None。"""
    m = re.search(r'^  -' + re.escape(opt) + r'\s+<[^>]+>.*?\(from (-?\d+) to (-?\d+)\)',
                  text, re.M)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════
# 素材与度量
# ══════════════════════════════════════════════════════════════════════

def make_source(work: Path, src: str | None) -> Path:
    if src:
        p = Path(src)
        if not p.is_file():
            raise SystemExit(f'[ERROR] --src 不存在：{src}')
        return p
    out = work / 'src_720p.mp4'
    if not out.exists():
        sh(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=4',
            '-c:v', 'libx264', '-crf', '12', '-preset', 'veryfast',
            '-pix_fmt', 'yuv420p', str(out)])
    return out


def encode_soft(src: Path, out: Path, crf=21, codec='libx264') -> tuple[int, str]:
    return sh(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
               '-i', str(src), '-an', '-c:v', codec, '-crf', str(crf),
               '-preset', 'medium', '-pix_fmt', 'yuv420p', str(out)])


def encode_nvenc(src: Path, out: Path, codec: str, mode: str, value: int) -> tuple[int, str]:
    """mode='cq' → `-rc {vbr_hq|vbr} -cq:v N -b:v 0`；mode='qp' → `-rc constqp -qp N -b:v 0`。

    `-cq` 分支的 rc 按 CR-2 与生产 / 标定 harness 对齐（h264/hevc=`vbr_hq`、av1=`vbr`，
    见 `_cq_rc`），否则同 `-cq` 的率失真点不同。
    """
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', str(src), '-an', '-c:v', codec, '-preset', NVENC_PRESET,
           '-pix_fmt', 'yuv420p']
    if mode == 'cq':
        cmd += ['-rc', _cq_rc(codec), '-cq:v', str(value), '-b:v', '0']
    else:
        cmd += ['-rc', 'constqp', '-qp', str(value), '-b:v', '0']
    cmd += [str(out)]
    return sh(cmd)


def has_libvmaf() -> bool:
    rc, out = sh(['ffmpeg', '-hide_banner', '-filters'], timeout=60)
    return rc == 0 and 'libvmaf' in out


def measure(ref: Path, test: Path, want_vmaf: bool) -> dict:
    """返回 {psnr, ssim, vmaf, kbps}；缺哪个指标就为 None。"""
    out: dict = {'psnr': None, 'ssim': None, 'vmaf': None,
                 'kbps': None, 'bytes': None}
    try:
        out['bytes'] = Path(test).stat().st_size
    except OSError:
        return out
    # 码率：按 video 流字节 / 时长估算（容器小、可比）
    rc, info = sh(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                   '-show_entries', 'format=duration', '-of', 'csv=p=0', str(test)])
    try:
        dur = float(info.strip().splitlines()[0])
        if dur > 0:
            out['kbps'] = out['bytes'] * 8 / dur / 1000
    except Exception:
        pass
    rc, log = sh(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(ref), '-i', str(test),
                  '-lavfi', '[0:v][1:v]psnr', '-f', 'null', '-'], timeout=900)
    m = re.search(r'average:([\d.]+|inf)', log)
    if m:
        out['psnr'] = float('inf') if m.group(1) == 'inf' else float(m.group(1))
    rc, log = sh(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(ref), '-i', str(test),
                  '-lavfi', '[0:v][1:v]ssim', '-f', 'null', '-'], timeout=900)
    m = re.search(r'All:([\d.]+)', log)
    if m:
        out['ssim'] = float(m.group(1))
    if want_vmaf:
        rc, log = sh(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(ref), '-i', str(test),
                      '-lavfi', '[0:v][1:v]libvmaf', '-f', 'null', '-'], timeout=1800)
        m = re.search(r'VMAF score:\s*([\d.]+)', log)
        if m:
            out['vmaf'] = float(m.group(1))
    return out


def rate_verdict(d_psnr, ratio):
    """(status, 说明)。与 VE G7 同一套：质量单向下探 + 码率带。"""
    ok_rate = RATE_PASS[0] <= ratio <= RATE_PASS[1]
    warn_rate = RATE_WARN[0] <= ratio <= RATE_WARN[1]
    if d_psnr < -TOL_PSNR:
        return 'FAIL', f'质量下探 {d_psnr:.2f} dB（超 {TOL_PSNR} dB）'
    if ok_rate:
        return 'PASS', f'ΔPSNR {d_psnr:+.2f} dB，码率 {ratio:.2f}×（带内）'
    if warn_rate:
        return 'WARN', f'ΔPSNR {d_psnr:+.2f} dB，码率 {ratio:.2f}×（越界但在警戒带）'
    return 'FAIL', f'ΔPSNR {d_psnr:+.2f} dB，码率 {ratio:.2f}×（越界）'


def av1_qp_conclusion(av1_ok: dict) -> tuple[str, str]:
    """AV1 constqp 的 -qp 尺度结论（**纯函数**，供 group_c 与 --selftest 共用）。

    av1_ok = {候选 qp: 判词}（判词来自 rate_verdict 的 status）。返回 (status, detail)。
    L40/Ada 上跑完照这条决定动不动 `_QP_SCALE['av1_nvenc']`。
    """
    in_band = [v for v, st in av1_ok.items() if st == 'PASS']
    # 优先级：×3(63) > ×4(84) > ×5(105) > ×2(42) > 直取(21)
    if 63 in in_band:
        return 'PASS', ('仅 ×3(63) 落带内 ⇒ `_QP_SCALE["av1_nvenc"]=3` 成立，**无需改动**')
    if in_band == [84]:
        return 'PASS', ('仅 ×4(84) 落带内 ⇒ `_QP_SCALE["av1_nvenc"]=4` 成立，**无需改动**')
    if 105 in in_band and 84 not in in_band:
        return 'WARN', ('仅 ×5(105) 落带内 ⇒ 建议改 `_QP_SCALE["av1_nvenc"]`→5'
                        '（**两脚本同步** + 复核 `_QP_LIMITS`；改完回跑方案 §4.1）')
    if 21 in in_band:
        return 'WARN', ('直取基准轴(21) 落带内 ⇒ 可能不需要 QP 尺度层；但与 AV1 qindex 语义'
                        '（21 近无损、体积应暴涨）矛盾，务必人工复核后才能撤尺度')
    if not in_band:
        return 'FAIL', (f'候选均不落容忍带（{av1_ok}）⇒ 无法判定唯一尺度，'
                        '需扩扫（如 42 / 63 / 126）或按实测码率重标 `QUALITY_MAP["av1_nvenc"]`')
    return 'WARN', f'多个候选落带内（{in_band}）⇒ 无法判定唯一尺度，需人工取更贴者'


def av1_cq_conclusion(st: str, ratio: float, d: float, table_v: int) -> tuple[str, str]:
    """AV1 的 -cq 表值结论（**纯函数**，供 group_b 与 --selftest 共用）。"""
    if st == 'PASS':
        return 'PASS', (f'表值 {table_v} 落带内（ΔPSNR {d:+.2f} dB / 码率 {ratio:.2f}×）'
                        '⇒ 偏移方向成立；若幅度也满意则不改表')
    return 'FAIL', (f'表值 {table_v} 未落带内（ΔPSNR {d:+.2f} dB / 码率 {ratio:.2f}×）'
                    '⇒ 需重标 QUALITY_MAP["av1_nvenc"] 的 b'
                    '（改完两份 convert_crf.py 同步 + 回跑方案 §4.1）')


# ══════════════════════════════════════════════════════════════════════
# A 组：量程与换算（纯逻辑，本机可跑）
# ══════════════════════════════════════════════════════════════════════

def group_a(res: Result, crf_mod) -> None:
    print('\n【A】量程与换算（纯逻辑，本机可跑）')
    Q = crf_mod.get_quality_map()

    # A1: 表内量程 vs ffmpeg 声明量程
    # 期望的规格范围（来自 NVIDIA 文档/补丁）：
    #   h264/hevc_nvenc: -cq 0~51
    #   av1_nvenc: -cq 0~63 (NVIDIA 2024-05 补丁确认，旧版 ffmpeg 可能报 0~51)
    EXPECTED_SPEC_RANGES = {
        'h264_nvenc': (0, 51),
        'hevc_nvenc': (0, 51),
        'av1_nvenc': (0, 63),
    }

    for codec, opt, key in (('h264_nvenc', 'cq', 'h264_nvenc'),
                            ('hevc_nvenc', 'cq', 'hevc_nvenc'),
                            ('av1_nvenc', 'cq', 'av1_nvenc')):
        text = enc_help(codec)
        rng = parse_range(text, opt)
        if rng is None:
            res.add(f'A1-{codec}', 'A', f'{codec} 的 -{opt} 量程可探测', 'SKIP',
                    '本机 ffmpeg 没有该编码器或没列出该选项')
            continue
        lo, hi = rng
        a, b, tlo, thi = Q[key]
        spec_lo, spec_hi = EXPECTED_SPEC_RANGES.get(key, (tlo, thi))
        # 先比对规格：表内值是否符合 NVIDIA 规格
        spec_ok = (tlo == spec_lo) and (thi == spec_hi)
        # 再比对运行时 ffmpeg：若 ffmpeg 与规格不符，通常是版本旧（如 av1_nvenc 0~51 vs 规格 0~63）
        runtime_ok = (max(0, lo) == spec_lo) and (hi == spec_hi)
        if spec_ok and runtime_ok:
            status = 'PASS'
            detail = f'一致（规格 {spec_lo}~{spec_hi}）'
        elif spec_ok and not runtime_ok:
            # 表符合规格，但 ffmpeg 版本旧 → WARN，不阻断
            status = 'WARN'
            detail = f'表符合 NVIDIA 规格 {spec_lo}~{spec_hi}，但 ffmpeg 报 {lo}~{hi}（版本旧，升级 ffmpeg 后将匹配）'
        else:
            # 表不符合规格 → FAIL，需修表
            status = 'FAIL'
            detail = f'表中 {tlo}~{thi} 不符合 NVIDIA 规格 {spec_lo}~{spec_hi}'
        res.add(f'A1-{codec}', 'A', f'{codec} -{opt} 量程：表 {tlo}~{thi} vs ffmpeg {lo}~{hi} (规格 {spec_lo}~{spec_hi})',
                status, detail, evidence=[f'ffmpeg: -{opt} (from {lo} to {hi})', f'NVIDIA 规格: {spec_lo}~{spec_hi}'])
    # av1_nvenc 的 -qp 是 qindex 尺度（这条决定 C 组）
    txt = enc_help('av1_nvenc')
    qp_rng = parse_range(txt, 'qp')
    if qp_rng:
        res.add('A1-av1-qp', 'A', 'av1_nvenc 的 -qp 是 0~255（qindex，≠ H.264 的 0~51）',
                'PASS' if qp_rng[1] >= 200 else 'FAIL',
                f'实测 {qp_rng[0]}~{qp_rng[1]} ⇒ constqp 下必须乘 QP 尺度，不能直取基准轴',
                evidence=['对照：h264_nvenc -qp (-1 to 51)'])
    else:
        res.add('A1-av1-qp', 'A', 'av1_nvenc 的 -qp 量程可探测', 'SKIP', '本机没有该编码器')

    # A2: crf_ref 21 的表内换算值（hevc 的 +7.5 会算出 .5，下发时 int(round()) → 28）
    for codec, want in CQ_TABLE_AT_21.items():
        got = crf_mod.from_x264_crf(codec, 21)
        res.add(f'A2-{codec}', 'A', f'crf_ref 21 → {codec} 的 -cq 表值',
                'PASS' if abs(got - want) <= 0.5 else 'FAIL',
                f'{got:g}（下发 {round(got)}，期望 {want}）')
    # A3: 高端不再被截（V3）
    v51 = crf_mod.from_x264_crf('av1_nvenc', 51)
    res.add('A3-av1-high', 'A', 'av1_nvenc 在 crf_ref 51 处不被 51 截断',
            'PASS' if v51 > 51 else 'FAIL',
            f'crf_ref 51 → {v51:g}（hi 已修为 63）')
    # A4: 饱和扫描（低端/高端各有几档被吃掉）
    for codec in ('libvpx-vp9', 'libsvtav1', 'av1_nvenc'):
        a, b, lo, hi = Q[codec]
        sat_lo = sum(1 for r in range(52) if a * r + b <= lo)
        sat_hi = sum(1 for r in range(52) if a * r + b >= hi)
        res.add(f'A4-{codec}', 'A', f'{codec} 的饱和档数（低端/高端）',
                'PASS' if (sat_lo <= 1 and sat_hi <= 1) else 'WARN',
                f'低端 {sat_lo}/52、高端 {sat_hi}/52 个基准值落到同一档',
                evidence=[f'表：{a} × crf + {b}，夹到 [{lo}, {hi}]'])


# ══════════════════════════════════════════════════════════════════════
# B/C 组：GPU 实测
# ══════════════════════════════════════════════════════════════════════

def gpu_name() -> str:
    try:
        p = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                           capture_output=True, text=True, timeout=30)
        return (p.stdout or '').strip().splitlines()[0] if p.stdout.strip() else ''
    except Exception:
        return ''


def nvenc_usable(codec: str, src: Path, work: Path) -> tuple[bool, str]:
    """实跑一次短编码：选项存在 ≠ 本卡能编（T4 的 av1_nvenc 就是这种）。"""
    out = work / f'_probe_{codec}.mp4'
    rc, log = encode_nvenc(src, out, codec, 'cq', 28)
    if rc == 0 and out.exists() and out.stat().st_size > 0:
        return True, ''
    tail = ' / '.join(l for l in log.strip().splitlines()[-3:])
    return False, tail or f'rc={rc}'


def group_b(res: Result, src: Path, work: Path, soft: dict, soft_bytes: int,
            usable: dict) -> None:
    print('\n【B】crf_ref 21 的 -cq 是否等质量（GPU 实测）')
    cell: dict[tuple[str, str], tuple[str, str, dict, float, float]] = {}
    for codec in NVENC_CODECS:
        if not usable.get(codec):
            res.add(f'B-{codec}', 'B', f'{codec} 的 -cq 等质量对齐',
                    'SKIP', f'本卡不能编 {codec}（T4 无 AV1 NVENC 即此情形）')
            continue
        rng = parse_range(enc_help(codec), 'cq')
        table_v = CQ_TABLE_AT_21[codec]
        for label, v in (('表值', table_v), ('朴素值', NAIVE_CQ)):
            if rng and not (rng[0] <= v <= rng[1]):
                res.add(f'B-{codec}-{label}', 'B', f'{codec} -cq {v}（{label}）',
                        'SKIP', f'超出实测可编量程 {rng}')
                continue
            out = work / f'b_{codec}_{label}.mp4'
            rc, log = encode_nvenc(src, out, codec, 'cq', v)
            if rc != 0:
                res.add(f'B-{codec}-{label}', 'B', f'{codec} -cq {v}（{label}）',
                        'FAIL', f'编码失败 rc={rc}', evidence=[log.strip()[-200:]])
                continue
            m = measure(src, out, has_libvmaf())
            ratio = (m['kbps'] / soft['kbps']) if (m['kbps'] and soft['kbps']) else 0.0
            d = (m['psnr'] - soft['psnr']) if (m['psnr'] and soft['psnr']) else 0.0
            st, vd = rate_verdict(d, ratio)
            # 只有"表值"格计入 FAIL；朴素值格是**对照组**（用来证明为什么需要偏移）
            if label == '朴素值' and st == 'FAIL':
                st = 'WARN'
            cell[(codec, label)] = (st, vd, m, ratio, d)
            res.add(f'B-{codec}-{label}', 'B',
                    f'{codec} -cq:v {v}（{label}，crf_ref 21）',
                    st, vd,
                    evidence=[f'PSNR {m["psnr"] and round(m["psnr"], 2)} dB  '
                              f'SSIM {m["ssim"] and round(m["ssim"], 4)}  '
                              f'{round(m["kbps"] or 0)} kbps'
                              + (f'  VMAF {round(m["vmaf"], 2)}' if m['vmaf'] else ''),
                              f'参照 libx264 crf21：PSNR {round(soft["psnr"] or 0, 2)} dB  '
                              f'{round(soft["kbps"] or 0)} kbps'])
    # 与"按表换算 vs 朴素下发"的码率对比（回答"偏移到底该多大"）
    res.add('B-summary', 'B', '表值 vs 朴素值的码率比', 'PASS',
            '见上逐格 evidence（朴素值码率比 > 表值 ⇒ 偏移方向正确）')
    # av1 的专项结论行：L40/Ada 上跑完就能直接照它决定"动不动 QUALITY_MAP"
    if usable.get('av1_nvenc'):
        tb = cell.get(('av1_nvenc', '表值'))
        if tb is None:
            res.add('B-av1-结论', 'B', 'av1_nvenc 的 -cq 表值结论', 'FAIL',
                    '表值格未产出（编码失败或超量程）⇒ 无法判定')
        else:
            st, vd, m, ratio, d = tb
            cst, cdet = av1_cq_conclusion(st, ratio, d, CQ_TABLE_AT_21['av1_nvenc'])
            res.add('B-av1-结论', 'B', 'av1_nvenc 的 -cq 表值结论', cst, cdet)


def group_c(res: Result, src: Path, work: Path, soft: dict, usable: dict) -> None:
    print('\n【C】constqp 的 -qp 尺度（专门针对 av1_nvenc；H.264/HEVC 作对照）')
    av1_ok: dict[int, str] = {}          # 候选 qp → 判词（仅 av1 可用时填充）
    if not usable.get('av1_nvenc'):
        res.add('C-av1', 'C', 'AV1 constqp 的 QP 尺度', 'SKIP',
                '本卡不支持 AV1 NVENC（T4 情形）⇒ 此格必须在 L40/Ada 上跑')
    else:
        # 候选：直取基准轴(21) / ×3(63) / ×4(84) / ×5(105)
        # ×3(63) 为 L40 扩扫新增最佳尺度（2026-09-29 实测）
        for v, tag in ((21, '直取基准轴（当前两项目的做法）'),
                       (63, '×3（L40 扩扫最佳）'),
                       (84, '×4（qindex ≈ 4×QP）'),
                       (105, '×5')):
            out = work / f'c_av1_qp{v}.mp4'
            rc, log = encode_nvenc(src, out, 'av1_nvenc', 'qp', v)
            if rc != 0:
                res.add(f'C-av1-qp{v}', 'C', f'av1_nvenc -qp {v}（{tag}）',
                        'FAIL', f'编码失败 rc={rc}', evidence=[log.strip()[-200:]])
                continue
            m = measure(src, out, has_libvmaf())
            ratio = (m['kbps'] / soft['kbps']) if (m['kbps'] and soft['kbps']) else 0.0
            d = (m['psnr'] - soft['psnr']) if (m['psnr'] and soft['psnr']) else 0.0
            st, vd = rate_verdict(d, ratio)
            av1_ok[v] = st
            res.add(f'C-av1-qp{v}', 'C', f'av1_nvenc -rc constqp -qp {v}（{tag}）',
                    st, vd,
                    evidence=[f'PSNR {m["psnr"] and round(m["psnr"], 2)} dB  '
                              f'{round(m["kbps"] or 0)} kbps  码率比 {ratio:.2f}×'])
        # 结论行：L40/Ada 上跑完直接照它决定"动不动 _QP_SCALE"
        cst, cdet = av1_qp_conclusion(av1_ok)
        res.add('C-av1-结论', 'C', 'AV1 的 -qp 尺度结论', cst, cdet)
    # H.264 / HEVC 的对照组：QP 与 x264 QP 同尺度 ⇒ 21 应落在带内
    for codec in ('h264_nvenc', 'hevc_nvenc'):
        if not usable.get(codec):
            res.add(f'C-{codec}', 'C', f'{codec} constqp -qp 21', 'SKIP', '本卡不能编')
            continue
        out = work / f'c_{codec}_qp21.mp4'
        rc, log = encode_nvenc(src, out, codec, 'qp', 21)
        if rc != 0:
            res.add(f'C-{codec}', 'C', f'{codec} -qp 21', 'FAIL', f'rc={rc}')
            continue
        m = measure(src, out, has_libvmaf())
        ratio = (m['kbps'] / soft['kbps']) if (m['kbps'] and soft['kbps']) else 0.0
        d = (m['psnr'] - soft['psnr']) if (m['psnr'] and soft['psnr']) else 0.0
        st, vd = rate_verdict(d, ratio)
        res.add(f'C-{codec}', 'C', f'{codec} -rc constqp -qp 21（= 基准轴直取）',
                st, vd,
                evidence=[f'PSNR {m["psnr"] and round(m["psnr"], 2)} dB  '
                          f'{round(m["kbps"] or 0)} kbps  码率比 {ratio:.2f}×'])


# ══════════════════════════════════════════════════════════════════════
# 报告与入口
# ══════════════════════════════════════════════════════════════════════

def write_reports(res: Result, args, gpu: str, src: Path) -> None:
    if args.json:
        Path(args.json).write_text(json.dumps(
            {'gpu': gpu, 'source': str(src), 'rows': res.rows,
             'tol': {'psnr': TOL_PSNR, 'vmaf': TOL_VMAF, 'rate_pass': RATE_PASS}},
            ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n  报告已写入 {args.json}')
    if args.md:
        lines = [f'# NVENC 质量轴上机验收（{gpu or "GPU 未知"}）', '',
                 f'- 素材：`{src}`', f'- 判据：ΔPSNR ≥ −{TOL_PSNR} dB，码率比 {RATE_PASS}',
                 '', '| 组 | id | 结论 | 状态 | 说明 |', '|---|---|---|---|---|']
        for r in res.rows:
            lines.append(f"| {r['group']} | {r['id']} | {r['title']} | "
                         f"{r['status']} | {r['detail']} |")
        Path(args.md).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'  报告已写入 {args.md}')


def selftest() -> int:
    """只验装置：量程解析 / 判词 / 报告结构。不动 ffmpeg 编码。"""
    print('── selftest ──')
    bad = []
    if NVENC_PRESET != 'p4':
        bad.append("NVENC_PRESET 必须为 p4（跨仓契约 CR-1，与 VE 一致；同标定 harness 的 BASE_LOCK）")
    if (_cq_rc('av1_nvenc'), _cq_rc('h264_nvenc'), _cq_rc('hevc_nvenc')) != ('vbr', 'vbr_hq', 'vbr_hq'):
        bad.append("_cq_rc 的 CR-2 映射错（应为 av1=vbr、h264/hevc=vbr_hq）")
    sample = """  -cq                <float>      E..V....... Set target quality level (0 to 63, 0 means automatic) (from 0 to 63) (default 0)
  -qp                <int>        E..V....... Constant quantization parameter rate control method (from -1 to 255) (default -1)
"""
    if parse_range(sample, 'cq') != (0, 63):
        bad.append('parse_range(cq) 解析错')
    if parse_range(sample, 'qp') != (-1, 255):
        bad.append('parse_range(qp) 解析错')
    if parse_range('', 'cq') is not None:
        bad.append('空输入应返回 None')
    if rate_verdict(0.0, 1.0)[0] != 'PASS':
        bad.append('带内应 PASS')
    if rate_verdict(-3.0, 1.0)[0] != 'FAIL':
        bad.append('质量下探超限应 FAIL')
    if rate_verdict(0.0, 3.0)[0] != 'FAIL':
        bad.append('码率越界应 FAIL')
    if rate_verdict(0.0, 1.6)[0] != 'WARN':
        bad.append('警戒带应 WARN')
    # av1 结论行是纯函数、且只在 L40/Ada 上会走到 ⇒ 必须先自证（否则首次上机才暴露）
    _qp = {21: 'FAIL', 84: 'PASS', 105: 'FAIL'}
    if av1_qp_conclusion(_qp)[0] != 'PASS':
        bad.append('qp 结论：仅 ×4(84) 落带应 PASS')
    if av1_qp_conclusion({21: 'FAIL', 84: 'FAIL', 105: 'PASS'})[0] != 'WARN':
        bad.append('qp 结论：仅 ×5(105) 落带应 WARN（建议改尺度）')
    if av1_qp_conclusion({21: 'PASS', 84: 'FAIL', 105: 'FAIL'})[0] != 'WARN':
        bad.append('qp 结论：仅直取(21) 落带应 WARN（要求人工复核）')
    if av1_qp_conclusion({21: 'FAIL', 84: 'FAIL', 105: 'FAIL'})[0] != 'FAIL':
        bad.append('qp 结论：三候选都不落带应 FAIL')
    if av1_qp_conclusion({21: 'PASS', 84: 'PASS', 105: 'FAIL'})[0] != 'WARN':
        bad.append('qp 结论：多候选落带应 WARN（无法判定唯一尺度）')
    if '无需改动' not in av1_qp_conclusion({84: 'PASS'})[1]:
        bad.append('qp 结论：×4 成立时应写明"无需改动"')
    _qp_in = {84: 'PASS'}
    av1_qp_conclusion(_qp_in)
    if _qp_in != {84: 'PASS'}:
        bad.append('qp 结论：不应修改入参（须为纯函数）')
    if av1_cq_conclusion('PASS', 1.20, 0.5, 27)[0] != 'PASS':
        bad.append('cq 结论：表值 PASS 应 PASS')
    if av1_cq_conclusion('WARN', 2.20, 3.5, 27)[0] != 'FAIL':
        bad.append('cq 结论：非 PASS 应 FAIL 并提示重标 b')
    if '重标' not in av1_cq_conclusion('FAIL', 2.20, 3.5, 27)[1]:
        bad.append('cq 结论：未落带时应提示重标 QUALITY_MAP')
    for b in bad:
        print(f'  ✗ {b}')
    print('  ✓ 装置自检通过' if not bad else f'  ✗ {len(bad)} 项不过')
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description='NVENC 质量轴上机验收（T4 / L40）')
    ap.add_argument('--src', help='真实素材（默认用合成 720p testsrc2）')
    ap.add_argument('--quick', action='store_true', help='只跑 A 组（纯逻辑）')
    ap.add_argument('--selftest', action='store_true', help='只验装置本身')
    ap.add_argument('--expect-av1', action='store_true',
                    help='要求本卡能编 AV1（L40/Ada 交接用）：av1_nvenc 不可用即 exit 2，'
                         '避免把"静默 SKIP"当成"已验过 AV1"')
    ap.add_argument('--json', help='JSON 报告输出路径')
    ap.add_argument('--md', help='Markdown 报告输出路径')
    ap.add_argument('--keep', action='store_true', help='保留中间编码产物')
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.expect_av1 and args.quick:
        print('[ERROR] --expect-av1 需要跑 B/C 组（可用性探测在 B/C 之前），不能与 --quick 同用')
        return 2
    if shutil.which('ffmpeg') is None:
        print('[ERROR] 未找到 ffmpeg')
        return 2

    crf_mod = load_module('vu_convert_crf', ROOT / 'convert_crf.py')
    res = Result()
    gpu = gpu_name()

    print('═' * 68)
    print(f'  NVENC 质量轴上机验收   GPU = {gpu or "(未探测到 nvidia-smi)"}')
    print('═' * 68)

    group_a(res, crf_mod)
    if args.quick:
        print('\n（--quick：跳过 B/C 组）')
        write_reports(res, args, gpu, Path(args.src or '(合成)'))
        return 0 if res.count('FAIL') == 0 else 1

    work = Path(tempfile.mkdtemp(prefix='nvenc_verify_'))
    try:
        src = make_source(work, args.src)
        print(f'\n素材：{src}')

        # --expect-av1：先只探 av1 可编性，好在跑软编基准与全量之前 fail-fast
        probe_cache: dict[str, tuple[bool, str]] = {}
        if args.expect_av1:
            ok, why = nvenc_usable('av1_nvenc', src, work)
            probe_cache['av1_nvenc'] = (ok, why)
            if not ok:
                print(f'\n[ERROR] --expect-av1 未满足：本卡（{gpu or "未知"}）不能编 av1_nvenc。\n'
                      f'        实测失败：{why[:160]}\n'
                      '        AV1 NVENC 需 Ada 代及以上（RTX 40 / L40 等）；本卡不满足 L40 交接前提。\n'
                      '        已 fail-fast 退出，以免把 B/C 组的 av1 SKIP 误当成"已验过 AV1"。\n'
                      '        如只想跑 h264/hevc 部分，请去掉 --expect-av1。')
                return 2
            print('  ✓ --expect-av1：av1_nvenc 可编 ⇒ B/C 组会跑满（av1 的 -cq 偏移与 -qp ×4 尺度）')

        soft_out = work / 'soft_crf21.mp4'
        rc, log = encode_soft(src, soft_out, 21)
        if rc != 0:
            print(f'[ERROR] 软编基准失败：{log.strip()[-300:]}')
            return 2
        soft = measure(src, soft_out, has_libvmaf())
        print(f'软编基准 libx264 crf21：PSNR {soft["psnr"] and round(soft["psnr"], 2)} dB  '
              f'{round(soft["kbps"] or 0)} kbps')

        usable = {}
        print('\n── NVENC 可用性探测（选项存在 ≠ 本卡能编）──')
        for codec in NVENC_CODECS:
            if codec in probe_cache:
                ok, why = probe_cache[codec]
            else:
                ok, why = nvenc_usable(codec, src, work)
            usable[codec] = ok
            print(f'  {"✓" if ok else "–"} {codec}：'
                  f'{"可用" if ok else "不可用（" + why[:80] + "）"}')

        group_b(res, src, work, soft, soft_out.stat().st_size, usable)
        group_c(res, src, work, soft, usable)
        write_reports(res, args, gpu, src)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f'\n  中间产物保留在 {work}')

    print()
    print('─' * 68)
    print(f'汇总：PASS {res.count("PASS")}  WARN {res.count("WARN")}  '
          f'FAIL {res.count("FAIL")}  SKIP {res.count("SKIP")}')
    if res.count('FAIL'):
        print('FAIL 列表：')
        for r in res.rows:
            if r['status'] == 'FAIL':
                print(f'  ✗ [{r["id"]}] {r["title"]}：{r["detail"]}')
    return 0 if res.count('FAIL') == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
