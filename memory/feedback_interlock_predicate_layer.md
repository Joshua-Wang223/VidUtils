---
name: 互斥检查的判据要覆盖「实际会发生的路径」，不只「显式请求的路径」
description: keep×segment 互斥连踩三次——判据用错了层级（本次调用 vs 用户请求 vs 运行时决策），每次都在跑完/绕远后才报错
type: feedback
---

给 `convert_sdr_to_hdr.py` 加「`--subs keep` 与 `--split-mode segment` 互斥」时，
这个互斥检查我**改了三次判据**才做对，每次错法不同。共同点：**判据没有覆盖
「实际会发生什么」，只覆盖了「显式请求了什么」。**

| 版次 | 判据 | 实测后果 |
|---|---|---|
| ① | `_seg`（本次 `build_plan` 是否已带 `seg_start`） | `--split-mode segment --frames 10` 时 `seg_start` 仍是 None（分段要等运行时才由 `decide_single_file_mode` 决定）⇒ **互斥被跳过**，走到 remux 才报 `rc=234 Could not find tag for codec subrip` —— **报错指向 remux，而用户真正该改的是互斥** |
| ② | `args.split_mode == "segment"`（只看显式） | `auto` 在**长素材上真会分段**（实测 22s + workers 2 → `分段并行：2 段 × 2 并发`）⇒ 没拦，**转换跑完才在运行期抛 ValueError**，白跑一遍 |
| ③ | 复刻 `decide_single_file_mode` 的前提：`auto` 需 `dur ≥ 20s` + 无 `--duration`/`--frames` + `workers > 1` | 五种情形全对 |

**Why:** 互斥/前置校验的本质是「**提前**告诉用户不能这么做」。判据放错层级时，
它不会静默失效——而是在**更晚的地方**以另一个错误的面貌出现（rc=234、运行期异常），
把用户引向错误的修法。这是「错误信息比没有信息更贵」的典型：① 若不查，用户会以为是
remux 的 bug；② 用户已经白跑了一遍才知道白跑。

**How to apply:**
- 写互斥/前置校验时，先问**「这个条件在什么时候才为真」**：是「用户显式请求」、
  「本次函数调用」，还是「运行时才会决定」？三者要分别判，漏一个就漏一条路径。
- 前置校验的判据应**尽量贴近运行时的实际决策函数**（本次直接复刻了
  `decide_single_file_mode` 的四个前提）。宁可复刻得笨，也不要另写一套近似判据。
- ⚠ **报错必须指向用户该改的地方**。①那次报 `rc=234 subrip/mp4`，
  而真正原因是 `--split-mode segment` 与 keep 互斥 —— 报错层次比不报错更误导。
- ⚠ **互斥检查要覆盖「降级路径」**：分段在 `--frames` 存在时会被降级成 off，
  此时 auto 判定为「不分段」而放行 —— 这是**正确**的（实测放行且转换成功），
  别为了「保险」把该放行的也拦掉。判别方法：逐一列出每种参数组合，
  标出「应拦 / 不应拦」，**两边都要实测**（本次五种情形全测了，含三格不应拦的）。
- ⚠ 复刻判据时别抄错脚本的函数名。本次把 benchmark 脚本的 `_to_int` 写进了
  被测脚本 ⇒ `NameError`（本脚本用 `_frac_to_float`，没有 `_to_int`）。
  见 `feedback_test_and_cleanup_selftraps.md` 坑三。
