---
name: preset A/B 耗时基准的事实（libx265 slow→slower；并发与 AVX2 的影响）
description: 耗时/比值基准必须串行 c=1（并发把 CPU 比压低约 7%、墙钟放大数倍）；本机 8255C 串行 slow→slower CPU 比 4.68 ≈ 无 AVX2 基线 4.67；AVX2 只影响绝对耗时(+21%)、比值仅动 +1.9%；x265 asm 掩码强开 XOP/FMA4 会 SIGILL
type: project
---

来源：`Accessory/probe/bench_preset_ab.py` 与 `Accessory/calib/eqq_evidence/q3_bench/`
（2026-10-16 实测；工作簿 `preset_ab_report.xlsx`）。

## 事实 1：耗时/比值基准必须串行（concurrency=1）

同一素材（`anim2d_forest` 1280×720，crf24，slow vs slower）：

| 并发 | CPU 比(B/A) | A/B 核数 | 墙钟比 |
|---|---|---|---|
| c=1 | **4.85** | 5.79 / 5.53 | 5.27（仅被压低 4.5%） |
| c=2 | **4.51（−7%）** | 3.16 / 5.51 | 2.58（压低 74%） |
| c=8 | — | ~1 / ~1 | 单次编码 117s → **>600s（≈5×）** |

**Why:** 并发（超订）会让 x265 线程自旋等待、并叠加残存外部负载，**系统性改变 CPU 比值**；
墙钟更不可用。且 VidUtils 生产是**单文件编码**，c=1 才是代表性工作区间。
`bench_preset_ab.py` 的 `--max-load` 只防**外部**负载，防不了**自己开的并发** —— 这点要自己把关。

## 事实 2：本机串行结果

标定机 Intel Xeon Platinum 8255C（8 vCPU，Cascade Lake，AVX2 + AVX-512），17 条 720p 素材：
CPU 比几何平均 **4.680**（中位 4.754，范围 3.658~5.353），墙钟比 4.914。
与脚本内 `BASELINE`（E5-2620 v2，**无 AVX2**）的 4.669 几乎相同。

## 事实 3：AVX2 反证（同机只改 asm 掩码）

`bench_avx2_falsify.py`，`auto` vs `asm=1023`：

- 绝对 CPU：slow **+20.3%**、slower **+22.6%**（去 AVX2 后变慢）
- 预设比值：自动 **4.801** → 去 AVX2 **4.892**，**漂移仅 +1.9%**

⇒ **AVX2 主要影响绝对耗时（~1.2×），对 slow→slower 的比值基本无影响。**
故脚本 docstring 里「AVX2 机器该比值通常 1.5~2.5×」的假设**不成立**，4.7× 级才是真实水平。

## 事实 4：x265 `asm` 掩码的坑（会 SIGILL）

x265 的 `-x265-params asm=<整数>` **不是朴素位掩码**。实测（本机，slow preset）：

| asm | x265 自报能力 | rc |
|---|---|---|
| 255 | MMX2 SSE2 LZCNT SSSE3 SSE4.1 | 0 |
| 511 | MMX2 SSE2 LZCNT SSSE3 SSE4.2 | 0 |
| **1023** | **MMX2 SSE2 LZCNT SSSE3 SSE4.2 AVX** ← Intel 上有效的「去 AVX2」 | 0 |
| 2047 | …AVX **XOP** | **132 (SIGILL)** |
| 4095 | …AVX XOP **FMA4** | **132 (SIGILL)** |
| 8191 | …AVX XOP FMA4 **FMA3** | **132 (SIGILL)** |
| 0/1/256/426/2048 | 「none!」（被判无效集合） | 0 |

**Why:** XOP/FMA4 是 **AMD 专属**指令集；在 Intel 上被掩码强开后会真正执行到、触发非法指令。
⚠ 且**只有 slow/slower 的 RD 路径**才走得到相应原语——用 `ultrafast` 探针**探不出来**（会漏判，
第一版就因此跑了 10 分钟全 132 才被发现）。所以「掩码自证」必须用**与实验同档的 preset** 跑真编。

**How to apply:** 引用 preset 相对耗时用**串行 4.68×**，别套「AVX2 会大幅收窄比值」；
跨机器比较该比值时标注 asm 级别。要在 Intel 上关 AVX2 用 `asm=1023`；探针一律用真实 preset。
