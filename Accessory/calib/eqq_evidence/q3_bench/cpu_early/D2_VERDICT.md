# 阶段零点五 D2 预跑 —— 最终判定（2026-10-15）

- 数据源：`libsvtav1_ab.json` + `libx265_ab.json`，各 **170 个测量点**（17 素材 × 5 锚点 × 2 preset）
- 素材：`/workspace/input_videos/eqq_calib` 真实 17 条池（12×6s + 5×10s，720p/yuv420p 切片，直接作 reference）
- VMAF：`libvmaf=n_subsample=1`，**帧序重索引后**（`settb=AVTB,setpts=N/30/TB`，见下方方法学修正 1）
- **判定口径：只以 VMAF / 码率为准** —— `enc_seconds` 一列被外部 opencode session 污染（负载曾达 77/24 核），已弃用，见 `CONTENTION_NOTE.md`

## 判定：两条腿都支持保留 preset 分化（D2 成立）

| 腿 | A（quality 侧） | B（archive 侧） | 等 VMAF 码率收益 | 等码率 VMAF 增益 | 结论 |
|---|---|---|---|---|---|
| svtav1 | preset 6 | preset 4 | **19.6%** | +0.138 | **worth_it** |
| libx265 | `slow` | `slower` | **10.5%** | +0.183 | **worth_it** |

两侧收益都远超 3% 门槛 ⇒ **主方案 §4.1 的 preset 分化予以保留，无需拉平**：

| 档位 | NVENC | libx265 | svtav1 |
|---|---|---|---|
| `archive` | p7 | slower | 4 |
| `quality` | p6 | slow | 6 |

## 两个 agent 原本都判 `inconclusive`，原因是耗时项

判据原文是合取式：`收益≥3% 且 耗时≤+50% ⇒ worth_it`。两侧收益都过线，但耗时列给出
svtav1 +50.25%、x265 +320.8%（CPU 时间交叉校验 4.55×）⇒ 落入"其余组合"。
**耗时列已按你的说明弃用**，去掉该项后两侧都是 worth_it。

> ⚠ 因此这里的结论只回答"D2 分化有没有画质/体积价值"，**不回答"quality 档该不该用更快 preset 保吞吐"**。
> 后者需要空载机器上重测耗时比。已知 x265 的 4.2× 是本机特有（E5-2620 v2 无 AVX2，slower 的 SIMD 惩罚更大）；
> 现代 AVX2 机器上 slower/slow 通常在 1.5~2.5×，仍可能 >1.5×。

## 稳健性

| 检查 | svtav1 | libx265 |
|---|---|---|
| 拟合失败 | 0 | 0 |
| 数据问题记录 | 0 | 1 格 md5 不一致（见下） |
| 去最好素材 | — | 9.8% |
| 去最差素材 | — | 11.1% |
| 码率比范围 | 0.375 ~ 0.968 | 0.784 ~ 0.994 |

x265 的 leave-one-out 后收益仍在 9.8~11.1%，结论稳定。
svtav1 的离散度大（0.375~0.968）主要来自内容类别差异，不是测量噪声。

## 数据质量备注

- **1 个格子 md5 不一致**：`10s_anim2d_subs_tobot_src1280x720|21|slow`。x265 多线程编码本身不保证字节确定，
  该格 run1/run2 产物不同但 VMAF 应仍有效（打分用的是 run2）。建议复查该格 VMAF 是否异常，若异常重测。
- **无时长校验失败、无 rc 非零**（x265 agent 自己记了 `duration_check_ok` / `rc_run1` / `rc_run2`）。
- 两侧都记录 `first_pass_invalid`：svtav1 150/170、x265 若干 —— 这是**首轮 VMAF 因帧序错配失效后重打分**的标记，
  记录下来的值是修正后的值（已抽查确认曲线单调、无 95/40 交替）。

## 方法学修正 1（重要，可复用）

**matroska 1/1000 时基 + 非整帧率素材会让 libvmaf framesync 按时间戳错配。**
1/30s = 33.333ms 在 1/1000 时基下被表示成 33ms，累积偏移使奇偶帧配对错位，逐帧 VMAF 在 ~95 与 ~40 间交替，
pooled 均值被严重拉低。25fps 以外的素材全部中招。

**修复**：VMAF 命令对两路输入都加 `settb=AVTB,setpts=N/30/TB`，按帧序重新索引后再配对。指标口径不变。
⇒ **任何用 mkv 作 dist 的 VMAF 标定都要防这个**，否则 pooled VMAF 会静默偏低。

## 方法学修正 2（我自己的错误，记录以免复发）

我最初报告"agent 交回的 summary 是空的"，据此自己重算并得到 svtav1 22.3% 收益、判定那是拟合伪影。
**前半句是错的**：我给 agent 的 schema 用的是 `equal_vmaf_bitrate_ratio_pct` 等字段名，而 agent 实际返回了
更丰富的另一套字段名（`bitrate_ratio_B_over_A_geomean` 等），我用错 key 去读，才看到全 `None`。
agent 的分析是完整的，而且**它自己已经处理了饱和 VMAF 问题**：`target_rule` 明确写了
"target = 95 clamped into the two presets' common achievable VMAF interval"，并记录只有 1/17 素材的 95 在公共区间内。

我用"同 CRF 直接对比"得到的 13.0% 是一个**更保守的估计量**（无拟合、无外推），与 agent 的 19.6%（等 VMAF 口径）
并不矛盾——同 CRF 下 p4 只拿到中位 +0.066 VMAF，等 VMAF 口径下收益理应更大。两者同向。
⇒ 教训：**结构化输出对不上时，先打印 `sorted(d.keys())` 确认真实字段名，不要断定对方没交数据。**

## 遗留

- [ ] 空载机器上重测 6↔4（svtav1）与 slow↔slower（x265）的墙钟/CPU 耗时比，回答"quality 档 preset 该不该调快"。
- [ ] 复查 `10s_anim2d_subs_tobot|21|slow` 的 VMAF 是否受 md5 不一致影响。
- [ ] harness 侧把「帧序重索引」固化为默认（`settb=AVTB,setpts=N/30/TB`），别再靠踩坑发现。
- [ ] svtav1 只有 1/17 素材的 VMAF 95 落在公共区间内，方法 A 样本量太小；要扩样本需往 CRF 33~42 补测锚点。
