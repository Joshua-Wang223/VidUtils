# libsvtav1 D2 bench —— preset 6 vs 4 分析（修正版）

- 数据源：`libsvtav1_ab.json`（170 个测量点 = 17 素材 × 5 锚点 × 2 preset）
- 口径：`/workspace/input_videos/eqq_calib` 真实 17 条池（12×6s + 5×10s，720p/yuv420p 切片）
- VMAF：`libvmaf=n_subsample=1`，reference = 素材池原片；170/170 点 `rc_vmaf=0`
- 判定口径：**只以 VMAF/码率为准**（`enc_seconds` 被外部 opencode session 污染，见 `CONTENTION_NOTE.md`）

## ⚠ 先说方法学修正

原始 agent 交回的 `summary` 是空的（`verdict=inconclusive`、各比率为 `None`），所以我从 170 个原始点重算。
第一版我用「逐素材拟合 VMAF=f(log bitrate) 后取该素材公共 VMAF 目标」，得到几何平均 **77.7%（收益 22.3%）**——
**这个数不能用**，原因：

- `screen_ui_code` 两档的 VMAF 全程贴在 **99.7~99.9**（VMAF 饱和区），拟合区间只有 0.15 个 VMAF 单位，
  拟合斜率被放到极大，微小偏移就放大成巨大码率比 ⇒ 该素材算出 34.5%
- 多个素材的目标 VMAF 取在 99.8+，等于在**外推**锚点范围之外取点

⇒ 已弃用。下面用两个不依赖该假设的方法重算。

## 结论：preset 4 相对 preset 6，等 VMAF 码率收益约 11~13%

### 方法 B（主判据，无拟合、无外推，n=85）

同 CRF 直接对比：

| 指标 | 值 |
|---|---|
| 码率比 p4/p6（几何平均） | **87.0%** |
| ⇒ 码率收益 | **13.0%** |
| ΔVMAF（p4 − p6）中位 | **+0.066** |
| ΔVMAF 范围 | −0.10 ~ +0.29 |

读法：preset 4 用 **87% 的码率**拿到**基本持平（中位 +0.07）**的 VMAF。
因为 p4 同时略占 VMAF 优势，等 VMAF 口径下的码率收益应 ≥13%。

### 方法 A（辅证，固定 VMAF 目标 95）

只有 **1/17** 素材的两档都跨过 VMAF 95（`doc_dark_earth`，其余素材在全部锚点上都 >96）：
码率比 **88.5%** ⇒ 收益 **11.5%**。n=1，只作方向性佐证，权重远低于方法 B。

> n 小是素材池特性：这批素材对 svtav1 偏"容易"，CRF 18~30 全程 VMAF >94。
> 想扩大方法 A 的样本，需要往更高 CRF（33~40）补测锚点——那正是 `preview`/`draft`/`test` 档的工作区间。

## 判定

**远超 3% 门槛，D2 分化在 svtav1 侧成立。**

| 档位 | preset | 理由 |
|---|---|---|
| `archive` | **4** | 一次编码、长期保存：编码耗时占比低，体积/画质占比高 ⇒ 13% 码率收益直接变现 |
| `quality` | 6 | 交付场景：吞吐更重要，且 6→4 的收益对"分发后不再重编码"的产物价值有限 |

⇒ **主方案 §4.1 的 svtav1 分化（archive 4 / quality 6）予以保留**，无需拉平。

## 遗留

- [ ] `enc_seconds` 一列废弃；空载机器上补测 6 vs 4 的墙钟耗时比后才能定量回答"quality 档是否该用更快 preset"。
- [ ] 若要把方法 A 的样本量做起来，往 CRF 33/36/39/42 补测（覆盖 preview/draft/test 区间）。
- [ ] 原始 agent 的 `summary` 字段为空这件事本身是个缺陷：它交回了 170 个原始点却没完成分析。已由本文件替代，但 harness 侧应加"results 非空即必须给出 summary"的校验。
