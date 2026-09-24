#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe/t4_acceptance.py — T4 上机验收（2026-09-24 那轮改动剩下的验收面）

背景：那轮改动（质量参数单点换算 / `-threads` / `-pix_fmt` / 命令顺序统一 / 概览块对齐）
在本机已经全绿 —— 四道回归门 + 14 个 verify 套件 + 两处负向对照。**只剩三件必须上机**
（本机无 NVIDIA GPU，`--codec *_nvenc` 会一律降级到 CPU 编码器，所以验不到）：

  A. 落点：GPU 策略链上质量参数真的按设计下发（本机只有**单元级**结论：
     `_resolve_quality_params()` 的返回值；端到端命令在本机永远是"降级后"的形态）
  B. 运行期：NVENC 真的接受这些选项并出片（`-rc constqp -qp N` / `-qp 0`）
  C. 文档声明：`-qp 0` 到底是不是**数学无损**（交给 probe/probe_lossless_qp0.sh）

用法
────
    # ① T4 上机（默认跑 A + B + C；需要 GPU 与素材）
    python3 probe/t4_acceptance.py --src '/workspace/output_videos/Dora/Season 02/S02E08_xxx.mp4'

    # ② 本机降级自证：只跑"本机也成立"的格，GPU 专属格**显式标跳过**；
    #    C 组用 LOCALCPU 替身（libx265/libx264）跑同一套装置
    python3 probe/t4_acceptance.py --local

    # ③ 只验装置本身（判词 / 命令解析 / 汇总；不动 ffmpeg）
    python3 probe/t4_acceptance.py --selftest

输出约定
────────
每一格都带一列「本机状态」——**这一列比结论本身值钱**，它回答"跑完是绿的是否等于结论成立"：
    · 本机已验     → 本机与 T4 同结论，T4 只是复核
    · 只能单元级   → 本机验过函数返回值，端到端形态只有在 T4 才成立
    · 需上机       → 本机完全做不到
失败时会补一行「⇒ 说明」，写清这一格红了意味着什么、下一步看哪里。

退出码：0 = 全过；1 = 有失败格；2 = 前置不满足（缺 ffmpeg / 缺素材 / 无 GPU 且未加 --local）
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HW = ROOT / "vidcrop_hwaccel.py"
LOSSLESS_PROBE = ROOT / "probe" / "probe_lossless_qp0.sh"

CMD_RE = re.compile(r"命令[^:]*: (ffmpeg .*)$", re.M)

# ── A 组：质量参数在 GPU 策略链上的落点（dry-run 断言）──────────────────────
# ⚠ 最后一列 `local_ok` = **这一格在 --local（本机无 GPU）下是否仍成立**。
#   别拿"本机已验"当这个判据：A7~A9（回归格）确实在本机验过，但那是**回归门**
#   （test/dump_enc_options.sh / dump_cmd_full.sh）用 CPU 轴验的 —— 本脚本这一格写的是
#   NVENC 形态，本机 `--codec *_nvenc` 必然降级 ⇒ 期望不成立、必须跳过。
# (id, 标签, 参数, 必须含[], 必须不含[], local_ok, 本机状态, 红了说明什么)
A_CASES = [
    ("A1", "nvenc constqp --qp 18", ["--codec", "hevc_nvenc", "--rc-mode", "constqp", "--qp", "18"],
     ["-c:v hevc_nvenc", "-rc constqp -qp 18"], ["libx265", "-cq"], False,
     "只能单元级", "GPU 上没走 constqp 透传；先确认这条命令里有没有 libx265（被降级了）"),
    ("A2", "nvenc constqp --crf-ref 21", ["--codec", "hevc_nvenc", "--rc-mode", "constqp",
                                          "--crf-ref", "21"],
     ["-c:v hevc_nvenc", "-rc constqp -qp 28"], ["-cq"], False,
     "只能单元级", "-ref 换算到 qp 的值与单元级推算(28)不一致；先看是否被降级"),
    ("A3", "nvenc constqp --cq-ref 23", ["--codec", "hevc_nvenc", "--rc-mode", "constqp",
                                         "--cq-ref", "23"],
     ["-c:v hevc_nvenc", "-rc constqp -qp 26"], ["-cq"], False,
     "只能单元级", "-ref(cq 量纲) 换算到 qp 的值与单元级推算(26)不一致"),
    ("A4", "字面量 --crf 21 → -cq", ["--codec", "hevc_nvenc", "--crf", "21"],
     ["-c:v hevc_nvenc", "-cq 28", "-b:v 0"], ["-crf", "libx265"], False,
     "只能单元级", "这是本轮新能力（此前静默回落默认 -cq 23）；若还是 23 说明换算没生效"),
    ("A5", "constqp --qp 0（无损）", ["--codec", "hevc_nvenc", "--rc-mode", "constqp", "--qp", "0"],
     ["-rc constqp -qp 0", "-b:v 0"], ["-cq"], False,
     "只能单元级", "无损形状不对（`-b:v 0` 是本轮补上的：显式 --qp 0 以前缺它）"),
    ("A6", "--cq 0 → 真无损改写", ["--codec", "hevc_nvenc", "--cq", "0"],
     ["-rc constqp -qp 0", "-b:v 0"], ["-cq 0"], False,
     "只能单元级", "既有的 cq0 改写回归；若变成 -cq 0 说明无损改写被本轮改动带坏了"),
    ("A7", "默认（回归）", ["--codec", "hevc_nvenc"],
     ["-c:v hevc_nvenc", "-cq 23", "-b:v 0"], ["libx265"], False,
     "本机已验（回归门覆盖）", "既有默认路径被本轮改动带了（应逐字不变）"),
    ("A8", "--cq 20（回归）", ["--codec", "hevc_nvenc", "--cq", "20"],
     ["-cq 20", "-b:v 0"], ["libx265"], False,
     "本机已验（回归门覆盖）", "既有 -cq 路径被带了（应逐字不变）"),
    ("A9", "vbr_hq + cq + lookahead（回归）",
     ["--codec", "hevc_nvenc", "--cq", "20", "--rc-mode", "vbr_hq", "--lookahead", "40"],
     ["-cq 20", "-b:v 0", "-rc vbr_hq", "-rc-lookahead 40"], [], False,
     "本机已验（回归门覆盖）", "rc 轴 / lookahead 的既有组合被带了（应逐字不变）"),
    ("A10", "--threads 硬件编码器不发", ["--codec", "hevc_nvenc", "--threads", "4"],
     [], ["-threads"], False,
     "本机已验（回归门覆盖）", "硬件编码器不该拿到 -threads（ffmpeg 帧级线程对它没意义）"),
    ("A11", "--threads 软编照发", ["--codec", "libx265", "--threads", "4"],
     ["-c:v libx265", "-threads 4"], [], True,
     "本机已验", "软编路径的 -threads 丢了"),
    ("A12", "概览 constqp 显示 QP 而非 CQ",
     ["--codec", "hevc_nvenc", "--rc-mode", "constqp", "--qp", "18"],
     ["QP: 18"], ["CQ:"], False,
     "只能单元级", "概览印了命令里根本没有的 CQ（本机降级时它应显示 CRF: 14 —— 两种都算对，"
                   "但 CQ 一定不对）"),
    # ↓ 两格是"本机也成立"的：用 CPU 编码器把**同一个装置**（解析 + 断言 + 概览抽取）跑通
    #   （换算值别记混：h264_nvenc cq 23 → libx264 = **18**，→ libx265 = **21**）
    ("A13", "本机版：libx264 --cq 23 → -crf 18", ["--codec", "libx264", "--cq", "23"],
     ["-c:v libx264", "-crf 18"], [], True,
     "本机已验", "字面量 --cq 落到 CPU 软编的换算变了（本机与 T4 同结论）"),
    ("A14", "本机版：概览 constqp 显示 CRF 而非 CQ",
     ["--codec", "libx265", "--rc-mode", "constqp", "--qp", "18"],
     ["CRF:"], ["CQ:"], True,
     "本机已验", "概览在降级（CPU 编码器）形态下印了 CQ —— 本机这一格与 T4 不同但同源，"
                 "它红说明显示层修坏了"),
]
# A12 的断言要在**概览行**上做，与其它格（命令行）不同，单独标出
OVERVIEW_CASES = {"A12", "A14"}

# ── B 组：运行期（真转码；本机完全做不到）──────────────────────────────────
# (id, 标签, 参数, 本机状态, 红了说明什么)
B_CASES = [
    ("B1", "constqp --qp 18 真跑通",
     ["--codec", "hevc_nvenc", "--rc-mode", "constqp", "--qp", "18"],
     "需上机", "NVENC 不接受 `-rc constqp -qp N`，或本机 GPU/驱动不满足"),
    ("B2", "constqp --qp 0 真跑通",
     ["--codec", "hevc_nvenc", "--rc-mode", "constqp", "--qp", "0"],
     "需上机", "无损形状（-rc constqp -qp 0 -b:v 0）被 NVENC 拒绝"),
    ("B3", "默认路径真跑通（回归）",
     ["--codec", "hevc_nvenc"],
     "需上机", "既有默认路径跑不动了 ⇒ 本轮改动带坏了 GPU 链"),
    ("B4", "cover + CUDA 缩放链真跑通",
     ["--mode", "cover", "--codec", "hevc_nvenc"],
     "需上机", "GPU 策略链（硬解 + scale_cuda + NVENC）跑不动了 ⇒ 命令顺序统一时带坏"),
    ("B5", "h264_nvenc constqp --qp 18 真跑通",
     ["--codec", "h264_nvenc", "--rc-mode", "constqp", "--qp", "18"],
     "需上机", "另一个 NVENC 编码器上 constqp 不被接受"),
]
BASE = ["--mode", "crop", "--output-width", "640", "--output-height", "360"]


# ═══════════════════════════════════════════════════════════════════
#  装置（判词 / 解析 / 汇总）—— --selftest 会单独真跑一遍
# ═══════════════════════════════════════════════════════════════════
class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, bool | None]] = []   # (id, 标签, 本机状态, ok)

    def add(self, cid: str, label: str, local: str, ok: bool | None) -> None:
        self.rows.append((cid, label, local, ok))

    def mark(self, cid: str, label: str, local: str, ok: bool) -> None:
        print(f"  [{'✓' if ok else '✗'}] {cid} {label:<34} （本机状态：{local}）")
        self.add(cid, label, local, ok)

    def skip(self, cid: str, label: str, local: str, why: str) -> None:
        print(f"  [·] {cid} {label:<34} （本机状态：{local}）")
        print(f"        跳过：{why}")
        self.add(cid, label, local, None)

    def rich(self, cid: str, label: str, local: str, problems: list[str],
             hint: str = "") -> None:
        ok = not problems
        self.mark(cid, label, local, ok)
        if not ok:
            for p in problems:
                print(f"        ✗ {p}")
            if hint:
                print(f"        ⇒ 说明：{hint}")

    def summary(self) -> int:
        fail = sum(1 for *_, ok in self.rows if ok is False)
        skipn = sum(1 for *_, ok in self.rows if ok is None)
        passn = len(self.rows) - fail - skipn
        print()
        # ⚠ 空集守卫：**一个【有结论】的格都没有**（一个格没跑到 / 全是跳过格）时不能报绿。
        #    只判 `not self.rows` 是不够的 —— 跳过格也会往 rows 里塞一行，
        #    于是 `--local` 之外全是跳过时旧写法会打印"通过 0 失败 0"并返回 0。
        if passn == 0:
            print(f"✗ 一个【有结论】的格都没有（空集 / 全是跳过格：共 {len(self.rows)} 格）"
                  "—— 装置失效，不能报绿")
            return 1
        print(f"汇总：通过 {passn}　失败 {fail}　跳过 {skipn}（共 {len(self.rows)} 格）")
        if skipn:
            print("      ⚠ 有跳过格 ⇒ **结论不完整**，别看「没红」就当成通过")
        return 0 if fail == 0 else 1


def cmd_of(text: str) -> str:
    m = CMD_RE.search(text or "")
    return m.group(1) if m else ""


def overview_line(text: str, prefix: str) -> str:
    for ln in (text or "").splitlines():
        if ln.startswith(prefix):
            return ln
    return ""


def check_tokens(text: str, must: list[str], must_not: list[str]) -> list[str]:
    """返回问题列表（空 = 通过）；空串一律判失败（空 == 空 是最隐蔽的假绿）。"""
    if not text.strip():
        return ["拿不到要比对的文本（命令/概览行为空）"]
    probs = [f"缺少 {n!r}" for n in must if n not in text]
    probs += [f"不该出现 {n!r}" for n in must_not if n in text]
    return probs


def run(args: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    p = subprocess.run([sys.executable] + args, cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def selftest() -> int:
    print("── SELFTEST：判词 / 解析 / 汇总装置（不动 ffmpeg）──")
    bad = 0
    for label, got, want in (
        ("命令解析·带引号", cmd_of("  执行命令    : ffmpeg -i 'a b.mp4' -crf 1"), "ffmpeg -i 'a b.mp4' -crf 1"),
        ("命令解析·无引号", cmd_of("  命令: ffmpeg -i a.mp4 -crf 1"), "ffmpeg -i a.mp4 -crf 1"),
        ("命令解析·无命令", cmd_of("没有任何命令行"), ""),
        ("概览行抽取", overview_line("编码器      : libx265  QP: 18", "编码器"),
         "编码器      : libx265  QP: 18"),
    ):
        ok = got == want
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        bad |= 0 if ok else 1
    for label, text, must, must_not, want_empty in (
        ("断言·全中", "ffmpeg -c:v hevc_nvenc -qp 18", ["-qp 18"], ["-cq"], True),
        ("断言·缺项", "ffmpeg -c:v hevc_nvenc", ["-qp 18"], [], False),
        ("断言·负向命中", "ffmpeg -c:v libx265", [], ["libx265"], False),
        ("断言·空文本必须判失败", "   ", [], [], False),
    ):
        got = check_tokens(text, must, must_not)
        ok = (not got) == want_empty
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
        bad |= 0 if ok else 1
    r = Report()
    r.add("x", "y", "z", None)          # 空集（只有跳过格）也要判失败
    if r.summary() == 0:
        print("  [FAIL] 汇总·只有跳过格时必须判失败")
        bad = 1
    else:
        print("  [OK] 汇总·只有跳过格时判失败")
    print("SELFTEST 通过" if bad == 0 else "SELFTEST 失败")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="T4 上机验收（落点 / 运行期 / 无损）")
    ap.add_argument("--src", default="", help="素材路径（默认 temp/fixture_1080p.mp4，缺则按需生成）")
    ap.add_argument("--local", action="store_true",
                    help="本机降级自证：只跑本机成立的格，GPU 专属格显式跳过")
    ap.add_argument("--selftest", action="store_true", help="只验装置本身")
    ap.add_argument("--no-lossless", action="store_true", help="不调用 probe_lossless_qp0.sh")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    for t in ("ffmpeg", "ffprobe"):
        if shutil.which(t) is None:
            print(f"[ERROR] 找不到 {t}", file=sys.stderr)
            return 2

    src = Path(a.src) if a.src else ROOT / "temp" / "fixture_1080p.mp4"
    if not src.exists():
        if a.src:
            print(f"[ERROR] 素材不存在：{src}", file=sys.stderr)
            return 2
        src.parent.mkdir(parents=True, exist_ok=True)
        print(f"（素材缺失，按需生成 {src}）")
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25:duration=1",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        str(src)], check=False)
    out_root = ROOT / "temp" / "t4_acceptance"
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"素材      : {src}")
    print(f"模式      : {'本机降级自证（--local）' if a.local else 'T4 上机'}")

    rep = Report()

    # ── A 组 ───────────────────────────────────────────────────────────
    print("\n── A. 质量参数在 GPU 策略链上的落点（--dry-run，比命令/概览文本）──")
    for cid, label, extra, must, nots, local_ok, local, hint in A_CASES:
        args = [str(HW), "--input", str(src), "--output", str(out_root / cid),
                "--dry-run"] + BASE + extra
        if a.local and not local_ok:
            rep.skip(cid, label, local, f"这一格是 NVENC 形态，本机 `--codec *_nvenc` 必然降级 ⇒ "
                                        f"期望不成立（{local}）")
            continue
        rc, text = run(args)
        target = overview_line(text, "编码器") if cid in OVERVIEW_CASES else cmd_of(text)
        probs = check_tokens(target, must, nots)
        if rc != 0 and not probs:
            probs = [f"脚本退出码 {rc}（命令/概览已拿到，但整体没跑通）"]
        rep.rich(cid, label, local, probs, hint)

    # ── B 组 ───────────────────────────────────────────────────────────
    print("\n── B. 运行期：NVENC 真接受这些选项并出片（本机完全做不到）──")
    if a.local:
        for cid, label, extra, local, hint in B_CASES:
            rep.skip(cid, label, local, "需要真 NVENC；本机 `--codec *_nvenc` 会降级，测不到")
    else:
        for cid, label, extra, local, hint in B_CASES:
            dst = out_root / cid
            shutil.rmtree(dst, ignore_errors=True)
            extra = list(extra)
            if extra[:2] == ["--mode", "cover"]:
                extra = extra + ["--output-width", "640", "--output-height", "360"]
            args = [str(HW), "--input", str(src), "--output", str(dst),
                    "--overwrite", "--no-chroma-check"] + extra
            if "--mode" not in extra:
                args += ["--mode", "crop", "--output-width", "640", "--output-height", "360"]
            rc, text = run(args)
            made = sorted(dst.glob("*.mp4")) if dst.exists() else []
            probs = []
            if rc != 0:
                probs.append(f"退出码 {rc}")
            if not made:
                probs.append("没有产出文件")
            if probs:
                tail = [ln for ln in text.splitlines() if ln.strip()][-4:]
                probs += ["日志尾部：" + " | ".join(tail)] if tail else []
            rep.rich(cid, label, local, probs, hint)
            if made:   # 出片了就顺手报一下产物属性（位深/编码器）
                p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                    "-show_entries", "stream=codec_name,pix_fmt,width,height",
                                    "-of", "csv=p=0", str(made[0])],
                                   capture_output=True, text=True)
                print(f"        产物：{made[0].name}  {(p.stdout or '').strip()}")

    # ── C 组 ───────────────────────────────────────────────────────────
    print("\n── C. 文档声明：`-qp 0` 到底是不是数学无损（交给专用探针）──")
    if a.no_lossless:
        rep.skip("C1", "无损探针（--no-lossless）", "需上机", "本轮显式跳过")
    elif not LOSSLESS_PROBE.exists():
        rep.rich("C1", "无损探针存在", "需上机", [f"找不到 {LOSSLESS_PROBE}"])
    else:
        # ⚠ 探针是 .sh：`bash` 未必在 **Python 的** PATH 里（本机 Windows 实测
        #   subprocess 找不到 bash → rc=127）。找不到就显式跳过并给出手工命令，
        #   别报成"探针失败"。
        bash = shutil.which("bash") or shutil.which("bash.exe")
        if bash is None:
            rep.skip("C1", "无损探针（含负向对照）", "需上机",
                     "找不到 bash（本机 Python 的 PATH 里没有 Git Bash）—— "
                     f"手工跑：SRC={src} bash probe/probe_lossless_qp0.sh")
        else:
            import os
            env = dict(os.environ)
            env["SRC"] = str(src)
            if a.local:
                env["LOCALCPU"] = "1"
            print(f"        调用：{'LOCALCPU=1 ' if a.local else ''}SRC={src} "
                  f"bash probe/probe_lossless_qp0.sh")
            p = subprocess.run([bash, str(LOSSLESS_PROBE)], cwd=str(ROOT), env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            body = (p.stdout or "") + (p.stderr or "")
            for ln in body.splitlines():
                if ln.strip() and ("⇒" in ln or ln.lstrip().startswith(("✓", "✗", "[", "·"))):
                    print("        " + ln.rstrip())
            local = "本机已验（LOCALCPU 替身）" if a.local else "需上机"
            rep.rich("C1", "无损探针（含负向对照）", local,
                     [] if p.returncode == 0 else [f"探针退出码 {p.returncode}"],
                     "探针内部会自己判：负向对照（18 档）必须报「不同」，否则它拒绝给结论")

    rc = rep.summary()
    print(f"\n产物留在 {out_root}/ 供复核。")
    if rc == 0 and any(ok is None for *_, ok in rep.rows):
        print("⚠ 仍有跳过格（本机无 GPU）⇒ 上机跑一次才能定案。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
