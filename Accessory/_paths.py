#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Accessory/_paths.py — 仓库根定位（被 Accessory/{verify,probe,test} 下的脚本共用）

为什么需要这个文件（2026-10-06）
────────────────────────────────
`verify/` `probe/` `test/` 三个目录原先在仓库根下，脚本用
`Path(__file__).resolve().parent.parent` 拿仓库根。目录移进 `Accessory/` 后，
那一行会指向 `Accessory/` 而不是仓库根 —— 而**这种错误不会立刻报错**：
`sys.path.insert` 照样成功、`ROOT / 'convert_crf.py'` 之类的路径只是「不存在」，
于是 verify_quality_mapping.py 的**跨仓真源断言会静默降级为 SKIP**（只打一行 ℹ），
门禁由绿变假绿。本仓此前已吃过一次「假门禁」的亏，故这里改成显式定位 + 硬失败。

用法
────
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Accessory/
from _paths import repo_root
ROOT = repo_root()          # 仓库根，跨目录层级稳定
```

为什么向上搜而不是数 `.parent` 的层数：目录再被移动一次（仓库内已经发生过一次）
时，数层数的写法会**再次**悄悄失效，而向上搜的写法与层级无关。
"""
from pathlib import Path

# 仓库根的判定标记：按可靠性排序。`.git` 最可靠（任何仓库都有），
# 退而求其次用根级脚本 `convert_crf.py`（打包成 tar 快照时没有 .git）。
_ROOT_MARKERS = ('.git', 'convert_crf.py')


def repo_root(start: Path = None) -> Path:
    """
    从 start（默认本文件所在位置）向上搜出仓库根。

    Raises:
        RuntimeError: 一路搜到文件系统根都没找到标记 —— **故意不静默返回**。
            静默兜底会让 verify 脚本拿着错误的 ROOT 继续跑，
            把「路径写错」变成「门禁假绿」，那比直接失败难查得多。
    """
    here = Path(start) if start is not None else Path(__file__).resolve()
    for cand in [here, *here.parents]:
        for marker in _ROOT_MARKERS:
            if (cand / marker).exists():
                return cand
    raise RuntimeError(
        f'从 {here} 向上找不到仓库根（找过 {", ".join(_ROOT_MARKERS)}）。'
        f'通常意味着脚本被移到了仓库之外，或仓库结构变了 —— '
        f'请确认本文件仍在 <repo>/Accessory/_paths.py 位置。')


def temp_root(create: bool = False) -> Path:
    """
    本机临时目录（测试素材、中间产物、探针日志）的根，即 `<repo>/Accessory/temp`。

    2026-10-06：`temp/` 从仓库根迁到 `Accessory/temp/`（与 verify/probe/test 同归一处）。
    脚本里**不要**再写 `ROOT / 'temp'` —— 那是迁移前的路径，会静默指向不存在的目录。
    统一走这个函数；`create=True` 时顺带建目录。

    注意：本目录是 gitignored 的本机状态，不入库。
    """
    path = repo_root() / 'Accessory' / 'temp'
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path

