#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test/split_diff_by_theme.py — 把工作区 diff 按「主题」拆成两条线，供按主题分提交。

背景（2026-09-22/23 实际场景）
────────────────────────────────────────────────────────────────────
同一个文件里压着**两条互不相关的改动线**（例：`vidcrop_hwaccel.py` 既有「色度归零修复」
又有「码率控制四参数」），而提交要按主题拆。难点不在 `git add -p`（逐 hunk），
在于**一个 hunk 里就混着两条线的改动**，必须逐行分开。

本工具把"怎么分"外置成**规则文件**（JSON），然后：
  1. 解析 `git diff`（默认工作区；`--rev` 可指定基线）成 hunk；
  2. 把每个 hunk 切成「段落」——**上下文 / 变更组**（连续的 `-`/`+` 行）；
  3. 逐段判定归属侧（A 或 B），判定顺序：整块覆盖 → 逐行规则 → 关键词 → 沿用上一段；
  4. 只输出 **A 侧补丁**（`git apply --cached A.patch` → commit A → `git add` 剩下的 → commit B）。

为什么只输出 A 侧：B 侧 = "工作区减去已提交的 A"，由 `git add` 自然得到 ——
补丁是对着**当前索引**写的，先算一份"对着 HEAD 的 B 补丁"再在 A 之后应用必然冲突。

为什么变更组是原子的：`-旧` / `+新` 必须落在同一侧，否则 A 那次提交里
「删了旧的、没加新的」会直接编译不过。关键词只在**新增行**上判，删除行随组走。

用法
────────────────────────────────────────────────────────────────────
    # 1) 生成规则模板（照抄改成你的两条线）
    python test/split_diff_by_theme.py --print-rules

    # 2) 只看分类报告（不写补丁）
    python test/split_diff_by_theme.py --rules test/split_rules.json --report

    # 3) 出 A 侧补丁并自证「A + 剩下的 == 工作区」
    python test/split_diff_by_theme.py --rules test/split_rules.json \
        --out temp/split --verify

    # 4) 工装自证（临时仓库里造两条线，断言分类结果与无损性；不碰本仓库）
    python test/split_diff_by_theme.py --selftest

⚠ 历史坑（都踩过）
────────────────────────────────────────────────────────────────────
· 手写 patch 的 `@@` 计数不对会报 `corrupt patch`；本工具**重算计数**并配合
  `git apply --recount`。
· 行尾：本工具全程二进制读写，不做换行转换（Windows 上 Python 文本模式会把整份文件转 CRLF）。
· CJK 路径：Git Bash 里 `git show origin/main:file` 会被 MSYS 改写参数 → 本工具给
  git 子进程统一加 `MSYS_NO_PATHCONV=1`。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

# ── 规则文件模板（JSON 不支持注释，故模板由本文件提供）────────────────────
RULES_TEMPLATE = {
    "files": ["vidcrop_hwaccel.py", "README.md", "memory/MEMORY.md"],
    "side_a": {"name": "chroma", "keywords": ["chroma", "setparams", "色度", "U/V", "归零"]},
    "side_b": {"name": "rc", "keywords": ["rc_mode", "lookahead", "bitrate", "码率"]},
    "default_side": "b",
    "line_fallback": "follow_previous",
    "whole_block": [
        {"file": "vidcrop_hwaccel.py",
         "first_line_prefix": "    extra = list(extra_x265_params",
         "side": "b"}
    ],
    "mixed_block": [
        {"file": "README.md",
         "first_line_prefix": "+python verify/verify_color_tagging.py",
         "a_line_substrings": ["verify_color_tagging", "verify_chroma_hook"]}
    ],
    "split_at_first_line_matching": [
        {"file": "memory/MEMORY.md", "pattern": "- [码率控制轴", "side": "b"}
    ],
}


def _env() -> Dict[str, str]:
    """给 git 子进程的环境：关掉 MSYS 的路径改写（CJK / 带冒号的 revision 会被它改坏）。"""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    return env


def _rmtree(path: Path) -> None:
    """删目录（Windows 上 git 对象文件是只读的，直接 rmtree 会失败 → 先去掉只读位）。"""
    if not path.exists():
        return
    for p in path.rglob("*"):
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def git(args: Sequence[str], cwd: Optional[Path] = None,
        env_extra: Optional[Dict[str, str]] = None) -> str:
    env = _env()
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(["git", *args], cwd=str(cwd or ROOT), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{r.stderr.strip()}")
    return r.stdout


# ═══════════════════════════════════════════════════════════════════
#  diff 解析
# ═══════════════════════════════════════════════════════════════════

class Seg:
    """hunk 内的一个段落：context（上下文）或 change（变更组，含 `-`/`+` 行）。

    `adds` / `dels` 分别只装新增行与删除行（去掉行首标记），`raw` 是原始行（带标记）。
    """

    __slots__ = ("kind", "raw", "adds", "dels")

    def __init__(self, kind: str, raw: List[str]):
        self.kind = kind
        self.raw = raw
        self.adds = [l[1:] for l in raw if l.startswith("+")]
        self.dels = [l[1:] for l in raw if l.startswith("-")]

    def text(self) -> str:
        return "".join(self.adds) or "".join(self.dels)

    def preview(self) -> str:
        first = next((l for l in self.adds or self.dels), "")
        return first.rstrip("\n")[:96]


class Hunk:
    def __init__(self, header: str, lines: List[str]):
        self.header = header
        self.lines = lines
        self.old_count = 0
        self.new_count = 0
        self.segs: List[Seg] = []
        self._build()

    def _build(self) -> None:
        """按当前 lines 重建段落与计数。**必须在 lines 填齐之后调用**（解析器先建空 Hunk）。"""
        self.segs = []
        for ln in self.lines:
            kind = "context" if not (ln.startswith("+") or ln.startswith("-")) else "change"
            if self.segs and self.segs[-1].kind == kind:
                self.segs[-1] = Seg(kind, [*self.segs[-1].raw, ln])
            else:
                self.segs.append(Seg(kind, [ln]))
        self.old_count = sum(1 for l in self.lines if not l.startswith("+"))
        self.new_count = sum(1 for l in self.lines if not l.startswith("-"))


class FileDiff:
    def __init__(self, path: str, header: List[str], hunks: List[Hunk]):
        self.path = path
        self.header = header
        self.hunks = hunks
        for h in hunks:
            h._build()      # 解析时行是后追加的 → 在这里统一补建段落（幂等）


def parse_diff(text: str) -> List[FileDiff]:
    """把 `git diff` 文本解析成 [FileDiff]（只处理 unified diff 的常规形态）。"""
    files: List[FileDiff] = []
    header: List[str] = []
    hunks: List[Hunk] = []
    cur_path: Optional[str] = None
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur_path is not None:
                files.append(FileDiff(cur_path, header, hunks))
            m = re.match(r"diff --git a/(.*) b/(.*)\n", line)
            cur_path = (m.group(2) if m else line.split()[-1]).strip()
            header, hunks = [line], []
        elif line.startswith("@@"):
            hunks.append(Hunk(line, []))
        elif hunks:
            hunks[-1].lines.append(line)
        elif cur_path is not None:
            header.append(line)
    if cur_path is not None:
        files.append(FileDiff(cur_path, header, hunks))
    return files


# ═══════════════════════════════════════════════════════════════════
#  归属判定
# ═══════════════════════════════════════════════════════════════════

class Classifier:
    def __init__(self, rules: dict):
        self.a_name = rules.get("side_a", {}).get("name", "a")
        self.b_name = rules.get("side_b", {}).get("name", "b")
        self.a_kw = list(rules.get("side_a", {}).get("keywords", []))
        self.b_kw = list(rules.get("side_b", {}).get("keywords", []))
        self.default = rules.get("default_side", "b")
        self.whole = {(_norm(f.get("file")), f.get("first_line_prefix")): f.get("side")
                      for f in rules.get("whole_block", [])}
        self.mixed = [(_norm(f.get("file")), f.get("first_line_prefix"),
                       list(f.get("a_line_substrings", [])))
                      for f in rules.get("mixed_block", [])]
        self.split_at = [(_norm(f.get("file")), f.get("pattern"), f.get("side"))
                         for f in rules.get("split_at_first_line_matching", [])]
        self.notes: List[str] = []          # 需要人工复核的点
        self.stats = {"a": 0, "b": 0, "a_lines": 0, "b_lines": 0}

    def whole_side(self, path: str, seg: Seg) -> Optional[str]:
        for (f, prefix), side in self.whole.items():
            if f == _norm(path) and seg.preview().startswith((prefix or "").lstrip("+")[:40]):
                return side
        return None

    def mixed_rule(self, path: str, seg: Seg):
        for f, prefix, subs in self.mixed:
            if f != _norm(path):
                continue
            if prefix and seg.preview().startswith(prefix.lstrip("+")[:40]):
                return subs
        return None

    def assign(self, path: str, seg: Seg, prev: Optional[str],
               ) -> Tuple[List[int], str, str, bool]:
        """判定变更组里**哪些原始行归 A**。

        Returns:
            (A 侧保留的行下标, 标签 'A'/'B'/'A+B', 本组末尾所在侧, 是否有"靠启发式"的告警)

        变更组是原子的：`-旧`/`+新` 必须同侧，否则 A 那次提交会「删了旧的、没加新的」。
        只有逐行规则（mixed_block）那一支会真的切一个组 —— 此时 `-` 行归 A
        （A 先落删除，B 应用时再改），并记一条人工复核提示。
        """
        n = len(seg.raw)
        all_idx = list(range(n))
        # ① 整块覆盖（关键词被注释里的字眼带偏时用）
        w = self.whole_side(path, seg)
        if w:
            if w == "a":
                return all_idx, "A", "a", False
            return [], "B", "b", False
        # ② 逐行规则：`+` 行逐行判（先看 A 子串、再看 B 关键词，**都没有则沿用上一行**——
        #    函数体 / 空行 / 续行不会有任何关键词，不沿用就会被整块切给另一条线）；
        #    `-` 行随 A（A 先落删除，B 应用时再改）。
        subs = self.mixed_rule(path, seg)
        if subs is not None:
            keep: List[int] = []
            cur = "a" if any(s in seg.raw[0] for s in subs) else "b"
            for i, l in enumerate(seg.raw):
                if l.startswith("-"):
                    keep.append(i)
                    continue
                if any(s in l for s in subs):
                    cur = "a"
                elif any(k in l for k in self.b_kw):
                    cur = "b"
                if cur == "a":
                    keep.append(i)
            if not keep:
                return [], "B", cur, False
            if len(keep) == n:
                return all_idx, "A", cur, False
            if seg.dels:
                self.notes.append(
                    f"{path}: 逐行规则下同一组含删除行 → 删除行归 A、B 会再改一次，"
                    f"请人工核对：{seg.preview()}")
            return keep, "A+B", cur, False
        # ③ 关键词（新增行优先；纯删除组看删除行）
        text = "".join(seg.adds) or "".join(seg.dels)
        a_hit = [k for k in self.a_kw if k in text]
        b_hit = [k for k in self.b_kw if k in text]
        if a_hit and not b_hit:
            return all_idx, "A", "a", False
        if b_hit and not a_hit:
            return [], "B", "b", False
        if a_hit and b_hit:
            self.notes.append(
                f"{path}: 同一变更组同时命中两条线的关键词 → 按 default={self.default} 处理，"
                f"建议补 whole_block / mixed_block 规则：{seg.preview()}")
            if self.default == "a":
                return all_idx, "A", "a", True
            return [], "B", "b", True
        # ④ 无关键词：**纯新增**（续行）沿用上一段；**含删除**的替换/删除组无从判断，
        #    按 default 处理并显式标记待复核 —— 这里最容易静默分错线。
        if seg.dels:
            self.notes.append(
                f"{path}: 无关键词的替换/删除组（`-` 与 `+` 都没有两条线的关键词）→ "
                f"按 default={self.default} 处理，若它其实属于另一条线请加 whole_block 规则："
                f"{seg.preview()}")
            if self.default == "a":
                return all_idx, "A", "a", True
            return [], "B", "b", True
        if (prev or self.default) == "a":
            return all_idx, "A", "a", False
        return [], "B", "b", False

    def split_at_side(self, path: str, text: str) -> Optional[str]:
        """`split_at_first_line_matching`：从命中该模式的行起（含），之后全归某侧。"""
        for f, pat, side in self.split_at:
            if f == _norm(path) and pat and pat in text:
                return side
        return None


def _norm(p: Optional[str]) -> str:
    return (p or "").replace("\\", "/").lstrip("./")


def build_a_patch(fdiffs: List[FileDiff], cl: Classifier) -> Tuple[str, List[str]]:
    """生成 A 侧补丁文本 + 分类报告。只保留含 A 段落（或 A 侧删除组）的 hunk。

    计数：删除行不落 A 侧（随组走）→ 老侧内容与原始 hunk 相同，故 old_count 不变；
    新侧 = 上下文 + A 侧新增行。用 --recount 兜底。
    """
    out: List[str] = []
    report: List[str] = []
    for fd in fdiffs:
        kept: List[Hunk] = []
        forced: Optional[str] = None      # split_at_first_line_matching：命中后整段归属固定
        for h in fd.hunks:
            prev: Optional[str] = None
            picked: List[str] = []
            for seg in h.segs:
                if seg.kind == "context":
                    picked.extend(seg.raw)      # ⚠ 必须逐行，拼成一坨会让两侧计数少算
                    continue
                hit = cl.split_at_side(fd.path, "".join(seg.raw))
                if hit:
                    forced = hit
                if forced:
                    keep = list(range(len(seg.raw))) if forced == "a" else []
                    label, trailing, flagged = ("A", "a", False) if forced == "a" else ("B", "b", False)
                else:
                    keep, label, trailing, flagged = cl.assign(fd.path, seg, prev)
                report.append(f"  [{label + ('?' if flagged else ''):4}] "
                              f"{fd.path}: {seg.preview()}")
                if keep:
                    cl.stats["a"] += 1
                    cl.stats["a_lines"] += len(keep)
                    picked.extend(seg.raw[i] for i in keep)
                else:
                    cl.stats["b"] += 1
                    cl.stats["b_lines"] += len(seg.raw)
                prev = trailing
            if any(l.startswith("+") or l.startswith("-") for l in picked):
                kept.append(_recount(h, picked))
        if kept:
            out.extend(fd.header)
            for h in kept:
                out.append(h.header)
                out.extend(h.lines)
    return "".join(out), report


def _recount(h: Hunk, picked: List[str]) -> Hunk:
    """重建 hunk 并重算 `@@` 计数。

    老侧 = 保留下来的「上下文 + 删除行」条数，新侧 = 「上下文 + 新增行」条数 ——
    被整组丢给 B 的变更组会同时少掉它的 `-`/`+` 行，两侧计数都要跟着变。
    起始行号沿用原值（丢弃只发生在 hunk 内部，老侧相对文件的位置不变）。
    """
    old_n = sum(1 for l in picked if not l.startswith("+"))
    new_n = sum(1 for l in picked if not l.startswith("-"))
    m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*?)\n?$", h.header)
    if not m:
        return Hunk(h.header, picked)
    # ⚠ 尾部（git 附的函数上下文）要原样保留，**换行必须补回来** —— 否则 `@@` 行会与
    #   下一行粘连，git 直接报 patch does not apply（本轮真实提交对照抓到的）
    hdr = f"@@ -{m.group(1)},{old_n} +{m.group(3)},{new_n} @@{m.group(5)}\n"
    return Hunk(hdr, picked)


# ═══════════════════════════════════════════════════════════════════
#  无损自证：A + B == 工作区（B 由 temp 索引上真实算出来，不靠推导）
# ═══════════════════════════════════════════════════════════════════

def verify_lossless(files: List[str], a_patch: str, workdir: Path,
                    base_rev: Optional[str] = None) -> Tuple[bool, str]:
    """在**临时索引**上应用 A、再取索引↔工作区的差作 B 并应用，最后逐文件比对 blob。

    不碰用户真实索引；临时文件落在仓库 `temp/` 下（符合本仓约定）。
    """
    tmp = Path(workdir) / "split_verify"
    tmp.mkdir(parents=True, exist_ok=True)
    idx = tmp / "index"
    a_path = tmp / "a.patch"
    a_path.write_bytes(a_patch.encode("utf-8"))
    env = {"GIT_INDEX_FILE": str(idx)}
    try:
        if idx.exists():
            idx.unlink()
        # 基线必须与 diff 的基线一致：`--rev R` 时补丁是对 R 写的；不带 --rev 时
        # 补丁是对**当前索引**写的（把真实索引整份拷过来，索引里可能已有暂存内容）。
        if base_rev:
            git(["read-tree", base_rev], env_extra=env)
        else:
            real_idx = Path(git(["rev-parse", "--git-dir"]).strip()) / "index"
            if not real_idx.is_absolute():
                real_idx = (ROOT / real_idx).resolve()
            shutil.copy(real_idx, idx)
        git(["apply", "--cached", "--recount", str(a_path)], env_extra=env)
        # B = 索引(A 之后) ↔ 工作区
        b_patch = git(["diff", "--", *files], env_extra=env)
        if b_patch.strip():
            b_path = tmp / "b.patch"
            b_path.write_bytes(b_patch.encode("utf-8"))
            git(["apply", "--cached", "--recount", str(b_path)], env_extra=env)
        bad = []
        for f in files:
            staged = subprocess.run(["git", "show", f":{f}"], cwd=str(ROOT),
                                    env={**_env(), **env}, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            if staged.returncode != 0:
                bad.append(f"{f}: 索引里没有该文件")
                continue
            work = (ROOT / f).read_text(encoding="utf-8", errors="replace")
            if staged.stdout != work:
                bad.append(f"{f}: 索引 blob 与工作区不一致")
        return (not bad), ("；".join(bad) if bad else "A + 剩余 == 工作区（逐文件逐字节一致）")
    finally:
        _rmtree(tmp)


# ═══════════════════════════════════════════════════════════════════
#  工装自证（临时仓库，不碰本仓）
# ═══════════════════════════════════════════════════════════════════

SELFTEST_BASE = """def f():
    return 1


def g():
    return 2
"""

# 两条线交织：A 纯增块（含无关键词的续行）/ B 纯增块 / A+B 相邻的混合块 /
# B 的替换组（-旧 +新 必须整组归 B）/ 命中 A 关键词但属于 A 的注释（靠 whole_block 纠正）
SELFTEST_NEW = """def f():
    return 1


# A1: chroma 自检
def _chroma_check():
    return True


# B1: 码率控制
RC_MODES = ("vbr", "cbr")


# A2: 色度 与 B2: rc_mode 相邻混排
# A3: chroma 只此一行
# B3: rc_mode 只此一行
# A4: 这行提到 rc_mode 但属 A（whole_block 纠正）
def g():
    return 3
"""

SELFTEST_EXPECT_A = """def f():
    return 1


# A1: chroma 自检
def _chroma_check():
    return True


# A2: 色度 与 B2: rc_mode 相邻混排
# A3: chroma 只此一行
# A4: 这行提到 rc_mode 但属 A（whole_block 纠正）
def g():
    return 2
"""

SELFTEST_RULES = {
    "files": ["demo.py"],
    "side_a": {"name": "a", "keywords": ["chroma", "色度"]},
    "side_b": {"name": "b", "keywords": ["rc_mode", "RC_MODES", "码率"]},
    "default_side": "b",
    # 关键词会被注释里的字眼带偏（A4 那行提到 rc_mode）→ 整块覆盖
    "whole_block": [{"file": "demo.py",
                     "first_line_prefix": "# A4: 这行提到 rc_mode 但属 A",
                     "side": "a"}],
    # 两条线的新增行相邻时 git 只给**一个**变更组 → 必须逐行判（含无关键词的续行）
    "mixed_block": [{"file": "demo.py",
                     "first_line_prefix": "# A1: chroma 自检",
                     "a_line_substrings": ["# A1", "# A2", "# A3", "# A4", "chroma"]}],
    "split_at_first_line_matching": [],
}


def selftest() -> int:
    """在 temp/ 里造一个临时仓库，跑完整流程并断言分类结果 + 无损性。"""
    work = ROOT / "temp" / "split_selftest"
    _rmtree(work)
    repo = work / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    if (repo / ".git").exists():
        raise RuntimeError(f"临时仓库没清干净，请手动删除：{repo}")
    fails: List[str] = []

    def chk(label: str, ok: bool, detail: str = "") -> None:
        print(f'  [{"OK" if ok else "FAIL"}] {label}' + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            fails.append(label)

    try:
        git(["init", "-q", "-b", "main"], cwd=repo)
        git(["config", "user.email", "t@t"], cwd=repo)
        git(["config", "user.name", "t"], cwd=repo)
        (repo / "demo.py").write_text(SELFTEST_BASE, encoding="utf-8", newline="")
        git(["add", "demo.py"], cwd=repo)
        git(["commit", "-qm", "base"], cwd=repo)
        (repo / "demo.py").write_text(SELFTEST_NEW, encoding="utf-8", newline="")

        raw = subprocess.run(["git", "diff", "--", "demo.py"], cwd=str(repo), env=_env(),
                             capture_output=True, text=True, encoding="utf-8").stdout
        fdiffs = parse_diff(raw)
        chk("解析出 1 个文件的 diff", len(fdiffs) == 1, f"{len(fdiffs)}")

        cl = Classifier(SELFTEST_RULES)
        a_patch, report = build_a_patch(fdiffs, cl)
        print("  ── 分类报告 ──")
        for line in report:
            print("  " + line)
        chk("两条线都分到了行（A 与 B 都非空）",
            cl.stats["a_lines"] > 0 and cl.stats["b_lines"] > 0,
            f'a={cl.stats["a_lines"]} b={cl.stats["b_lines"]}')
        chk("A 侧补丁里不能出现 B 的内容（RC_MODES / return 3）",
            ("RC_MODES" not in a_patch) and ("return 3" not in a_patch))

        # 逐字节断言：把 A 补丁应用到临时索引后，demo.py 必须等于 SELFTEST_EXPECT_A
        tmp = work / "idx"
        tmp.mkdir(exist_ok=True)
        idx = tmp / "index"
        a_path = work / "a.patch"
        a_path.write_bytes(a_patch.encode("utf-8"))
        env = {"GIT_INDEX_FILE": str(idx)}
        git(["read-tree", "HEAD"], cwd=repo, env_extra=env)
        git(["apply", "--cached", "--recount", str(a_path)], cwd=repo, env_extra=env)
        got = subprocess.run(["git", "show", ":demo.py"], cwd=str(repo),
                             env={**_env(), **env}, capture_output=True, text=True,
                             encoding="utf-8").stdout
        chk("A 侧结果 == 期望文本（B 的增行与替换都没进来）", got == SELFTEST_EXPECT_A,
            "见下" if got != SELFTEST_EXPECT_A else "")
        if got != SELFTEST_EXPECT_A:
            print("    ---- got ----"); print(got)
            print("    ---- want ----"); print(SELFTEST_EXPECT_A)

        # 无损性：A 之后再应用"索引↔工作区"的差，必须回到 SELFTEST_NEW 逐字节
        b_patch = git(["diff", "--", "demo.py"], cwd=repo, env_extra=env)
        b_path = work / "b.patch"
        b_path.write_bytes(b_patch.encode("utf-8"))
        git(["apply", "--cached", "--recount", str(b_path)], cwd=repo, env_extra=env)
        final = subprocess.run(["git", "show", ":demo.py"], cwd=str(repo),
                               env={**_env(), **env}, capture_output=True, text=True,
                               encoding="utf-8").stdout
        chk("A + B == 工作区（逐字节）", final == SELFTEST_NEW)

        # 负向格：无关键词的替换组**必须**被标记待复核（静默分错线是最坏的结局）
        print("  ── 工具给出的告警 ──")
        for n in cl.notes:
            print("    · " + n)
        chk("无关键词的替换/删除组被标记待复核",
            any("无关键词的替换/删除组" in n for n in cl.notes))
        # 正向格：补上整块规则后，告警必须消失（规则真的能解决问题）
        rules2 = json.loads(json.dumps(SELFTEST_RULES))
        rules2["whole_block"].append({"file": "demo.py", "first_line_prefix": "    return 3",
                                      "side": "b"})
        cl2 = Classifier(rules2)
        build_a_patch(parse_diff(raw), cl2)
        chk("补上 whole_block 规则后不再有告警", cl2.notes == [], str(cl2.notes))
        chk("补上规则后 A 侧结果仍等于期望文本（结论不变）",
            cl2.stats["a_lines"] == cl.stats["a_lines"])
    finally:
        _rmtree(work)

    print()
    if fails:
        print(f"✘ {len(fails)} 项失败：{fails}")
        return 1
    print("✓ 自证全部通过")
    return 0


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="按主题拆分工作区 diff（只出 A 侧补丁）")
    ap.add_argument("--rules", help="规则 JSON 文件")
    ap.add_argument("--rev", default=None, help="基线 revision（默认工作区 vs 索引）")
    ap.add_argument("--out", default=None, help="A 侧补丁输出路径（默认 temp/split/a.patch）")
    ap.add_argument("--report", action="store_true", help="只打印分类报告，不写补丁")
    ap.add_argument("--verify", action="store_true",
                    help="在临时索引上自证「A + 剩余 == 工作区」（不碰真实索引）")
    ap.add_argument("--print-rules", action="store_true", help="打印规则模板后退出")
    ap.add_argument("--selftest", action="store_true", help="工装自证（临时仓库，不碰本仓）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.print_rules:
        print(json.dumps(RULES_TEMPLATE, ensure_ascii=False, indent=2))
        return 0
    if not args.rules:
        print("[ERROR] 需要 --rules（可用 --print-rules 生成模板）。", file=sys.stderr)
        return 2

    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    files = [_norm(f) for f in rules.get("files", [])]
    if not files:
        print("[ERROR] 规则里 files 为空。", file=sys.stderr)
        return 2

    diff_args = ["diff"]
    if args.rev:
        diff_args.append(args.rev)
    diff_args += ["--", *files]
    raw = git(diff_args)
    if not raw.strip():
        print(f"（{args.rev or '工作区'} 相对基线没有改动，无需拆分。）")
        return 0

    cl = Classifier(rules)
    a_patch, report = build_a_patch(parse_diff(raw), cl)
    print(f"── 分类报告（A={cl.a_name} / B={cl.b_name}）──")
    for line in report:
        print(line)
    print(f"\nA 侧变更组 {cl.stats['a']} 个（{cl.stats['a_lines']} 行）；"
          f"B 侧变更组 {cl.stats['b']} 个（{cl.stats['b_lines']} 行）")
    if cl.notes:
        print("\n⚠ 需人工复核：")
        for n in cl.notes:
            print(f"  · {n}")
    if args.report:
        return 0

    out = Path(args.out or (ROOT / "temp" / "split" / "a.patch"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(a_patch.encode("utf-8"))
    print(f"\nA 侧补丁已写出：{out}")
    print("下一步：git apply --cached --recount " + str(out) +
          "  →  提交 A  →  git add <files>  →  提交 B")

    if args.verify:
        ok, msg = verify_lossless(files, a_patch, ROOT / "temp", base_rev=args.rev)
        print(("✓ " if ok else "✘ ") + msg)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
