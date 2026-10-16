# 《VidUtils `--quality-tier` 全流程实测方案》v1

- 日期：2026-10-15
- 主方案（本文是为它服务的）：`Plan/VidUtils_质量参数统一_quality-tier方案_v3_20261015.md`
  （本文中的 §B1~§B8 等编号即主方案 Part B 的延续；主方案 §6.2「阶段一编码」完成并通过 DoD 后才能全面开跑，否则测的是半成品）
- 适用脚本：`vidcrop_hwaccel.py` / `vidcrop_cpu_v2.py` / `convert_sdr_to_hdr.py`

---

## B0. 这份方案要回答的 5 个问题

| # | 问题 | 决定什么 | 由哪一步产出 |
|---|---|---|---|
| **P1** | 非 `balanced` 三档，各自**实际**比 `balanced` 好多少 / 贵多少？ | 档位到底值不值得存在 | §B4 三环主测 |
| **P2** | 各档的 `QUALITY_MAP` 行能否按 LOO 门禁 ≤5.9 标定？ | 能否把 `_EQQUAL_TIER_OVERRIDE` 填上、把"未标定"告警摘掉 | §B4 三环主测 |
| **P3** | `quality` 与 `archive` 该靠 **D1 锚点**还是 **D2 preset** 还是 **tune** 区分？ | Part A §12.3 的 Q3 | **§B5 专项 bench** |
| **P4** | `p6` / `p7` 的可靠性到底如何（含 `_NVENC_PRESET_RETRY` 实际触发率）？ | 修正上一版的过度解读 | **§B5.4** |
| **P5** | `-tune uhq` 在 hevc/av1 上到底有没有收益、代价多大？ | Part A §0-3 指出的空白 | **§B5.3** |

---

## B1. 实测对象矩阵

| 环 | 机器 / GPU | 编码器 | 参与 tier |
|---|---|---|---|
| **CPU 环**（阶段三） | 本机（无需 GPU） | `libx265`、`libvpx-vp9`、`libaom-av1`、`libsvtav1`、`librav1e`(+`-speed 10`) | `archive` / **`quality`（默认，优先测）** / `balanced` 作基线；`preview`/`draft`/`test` 次之；`lossless`/`near-lossless` 最后（LOO 门禁单独放宽） |
| **T4 环**（阶段四） | Tesla T4 / 驱动 580.65.06 / ffmpeg 9.0.2 | `h264_nvenc`、`hevc_nvenc` | 同左（**`quality` 优先**，因为它是默认档） |
| **L40 环**（阶段五） | NVIDIA L40 / 驱动 580.65.06 / ffmpeg 9.0.2 | `av1_nvenc`（`--expect-av1` fail-fast；Turing 代无 AV1 编码单元，见 `Accessory/probe/verify_nvenc_quality_gpu.py:27`） | 同左 |

> **每环只换「机器 / 编码器」，素材池、锚点、口径全部沿用上一环** —— 这样一旦 CPU 环与 T4 环的结论打架，能确定是"口径漂移"而非"素材差异"。

---

## B2. 素材池与口径（锁死，不得中途改）

沿用现有等质量标定口径（`convert_crf.py:112-130`）：

- **素材池**：现有 **17 条 eqq 素材池**（12×6s + 5×10s），覆盖立项 §3 全部 6 类内容 + 实拍影视 + 暗场
- **锚点**：`ANCHOR_CRFS = [18, 21, 24, 27, 30]`（`calibrate_equal_quality.py:72`）
- **VMAF**：`libvmaf`，**必须 `n_subsample=1`**（`convert_crf.py:180-184`：subsample>1 会偏置 VMAF，且偏置随编码器而异）
- **预处理**：720p prep
- **同一个 key 的多条观测取算术均值**（跨越时长的 7 条素材适用；`.json` 内重复观测跨度实测最大 2.05，属时长效应非测量误差）

---

## B3. 测量项定义

| 项 | 定义 | 采集方式 |
|---|---|---|
| **VMAF** | `libvmaf` 分数，`n_subsample=1` | `ffmpeg -i dist -i ref -lavfi libvmaf=... -f null -` |
| **码率** | 成品实际平均码率（kbps） | `ffprobe` / 文件大小 ÷ 时长 |
| **等 VMAF 码率比** | 目标 VMAF 相同时 `码率_候选 / 码率_基线`（BD-rate 的简化版） | 按锚点扫 10 点后插值（`SWEEP`，`calibrate_equal_quality.py:75-87`） |
| **耗时** | 墙钟编码时间（秒），跑 2 次取均值（首轮含预热要丢弃） | 脚本计时 |
| **峰值显存**（仅 NVENC 环） | 编码期间 GPU 显存峰值 | `nvidia-smi --query-gpu=memory.used --format=csv` 采样取最大 |
| **确定性** | 同参数同素材连跑 2 次，成品 **md5 是否一致** | `md5sum` |
| **`_NVENC_PRESET_RETRY` 触发率**（仅 NVENC 环） | 落到 p5/p6/p7 的命令中，有多少比例打印了「降档到 p4 重试」（hwaccel L5125） | 抓 stdout / stderr 计数 |

---

## B4. 三环主测（回答 P1 / P2）

每环、每个编码器、每个 tier 各跑一轮：

```
# 结构示例（以 CPU 环 libx265、archive 档为例）
python3 vidcrop_hwaccel.py <src> <dst> \
    --codec libx265 --pix-fmt yuv420p --decode cpu --scale-algo libswscale-lanczos \
    --quality-tier archive --strict-calib
    # ↑ --strict-calib 让「未标定」直接 exit 2，避免测到一张来历不明的表
```

采集：VMAF / 码率 / 耗时 / 确定性；NVENC 环加采峰值显存与 `_NVENC_PRESET_RETRY` 触发率。

**标定入表判据**：先过 **LOO**（`loo_equal_quality.py` L45-48：软编 ≤5.9、rav1e ≤7.5；`GATE_ANCHORS=(0,27)` L57），**不要**只看 `verify_equal_quality.py` 的 `TOL_VMAF=1.0`（L31）——README L55-60 已写明它是 in-sample 回归门，「不能为表值背书」。

**NVENC 环额外要求**：必须**同步重标 `_QP_AFFINE_QUALITY`**（hwaccel L1512 / cpu_v2 L1142 / sdr L484）——它是 CR-4 跨仓契约、⑨ 组会断言逐条相等，只改 `QUALITY_MAP` 不改它会红。

**本机限制**：`verify_equal_quality.py` L43-47 在无 GPU 机器上 NVENC 三档**全 SKIP** ⇒ T4 / L40 环必须在有卡机器上补跑，不得用本机 CI 假装通过。

---

## B5. Q3 专项 bench（回答 P3 / P4 / P5）

这部分是 Part A §12.3 的数据来源，**先出数据再定档位定义**。

### B5.1 A/B 组合清单

每条组合都在**同一 VMAF 目标**下比较码率与耗时：

| 轴 | A 侧 | B 侧 | 在哪一环做 |
|---|---|---|---|
| **D2 preset** | NVENC `p6` | NVENC `p7` | T4 / L40 环 |
| **D2 preset** | `libx265 slow` | `libx265 slower` | **阶段零点五已先跑**（Part A §6.15）；CPU 环只做复核 |
| **D2 preset** | `libsvtav1 6` | `libsvtav1 4` | **阶段零点五已先跑**（Part A §6.15）；CPU 环只做复核 |
| **P5 tune** | 不带 `-tune` | `-tune uhq`（仅 hevc/av1，先过 Part A §4.3 的探测） | T4 / L40 环 |

### B5.2 为什么要把 tune 也放进同一个 bench

`-tune uhq` 会自动启用 lookahead + temporal filter（`Plan/ffmpeg_nvenc_knowledge.md` §5），它对率失真的移动可能比 `p6→p7` 更大或更小——两者必须在**同一批素材、同一口径**下横向比较，否则无法判断「`quality`/`archive` 的分化该落在 preset 上还是 tune 上」。

而且若 `uhq` 收益显著，还有额外好处：可以把 `uhq` 收敛到**只有 archive 一档**上（`quality` 用 `hq`），从而少破一份标定。

### B5.3 `_NVENC_PRESET_RETRY` 触发率（P4）

上一版把该常量误读为「p6/p7 已知偶发初始化失败」并据此下判断，已撤回（见 Part A §12.3.2 更正）。本环把它作为**直接测量项**：

- 每条落在 `p5/p6/p7` 的命令都记录是否出现「降档到 p4 重试」（hwaccel L5125 的那行输出）
- 统计 `触发次数 / 总次数`，连同机器、驱动、ffmpeg 版本一并记入报告
- **触发率 > 某阈值 ⇒ `archive` 档不应采用该 preset**（阈值待你确认，我建议 **>1% 即判定不可接受**，因为 archive 是"一次转好、长期保存"场景，不允许偶发重来）

### B5.4 判据与阈值（提议，需你拍板）

| 情形 | 结论 |
|---|---|
| `p7` 相对 `p6`（`slower` 相对 `slow`）等 VMAF 码率收益 **< 3%** 且耗时 **> +50%** | **取消 D2**：`quality` / `archive` 的 preset 拉平，只靠 D1 区分 |
| 收益 **≥ 3%** 且 §B5.3 触发率可接受 | 保留 D2，两档标定一起做 |
| `uhq` 收益显著（同样按上面的 3% 门槛） | **改走「分化落在 tune」路线**：`quality` 用 `hq` / `archive` 用 `uhq`，preset 拉平 |
| `uhq` 收益不明显但代价明显 | 从 `quality` / `archive` 里**摘掉 uhq**，减少破标定的面 |

> **需要你确认**：①`3%` 与 `+50%` 这两个阈值；②§B5.3 的触发率红线（我建议 1%）。

---

## B6. 产物命名与落盘位置

```
Accessory/calib/eqq_evidence/
  <tier>_<ring>/            # 例：archive_cpu/、archive_t4/、quality_l40/
    points.json             # 原始测量点
    report.json             # 拟合 a/b、残差、ΔVMAF
    ring_meta.json          # 机器 / 驱动 / ffmpeg 版本 / 采集参数（指纹）
  q3_bench/
    preset_ab_{nvenc,x265,svtav1}.json
    tune_uhq_ab.json
    preset_retry_trigger.json
```

- 每份产物都要能回答「这数据是哪台机器、哪个驱动、哪个 ffmpeg 出的」。
- 换构建 / 驱动须重标（`convert_crf.py` L200 / L214 的指纹要求已是如此）。

---

## B7. 失败处理与重跑规则

| 情况 | 处理 |
|---|---|
| 单次编码 rc≠0 | 若为 NVENC preset 初始化失败 ⇒ 已被 `_NVENC_PRESET_RETRY` 兜底但要**计入触发率**；其余按 `--fallback-policy` 处理，失败的组合记为 **FAIL** 而非静默丢弃 |
| VMAF 计算失败 | 重跑一次；再失败则该素材记 SKIP，并在报告里列出 SKIP 清单 |
| 某编码器整体拿不到数据（如本机无 AV1 NVENC） | 该编码器留空 ⇒ `_EQQUAL_TIER_OVERRIDE` 缺键即自动回退 `balanced` 表并告警，**不假装已标定** |
| 某 tier 的 LOO 超门禁 | **禁止落表**，保持未标定状态；宁可让用户看到告警也不能给错的值 |

---

## B8. 数据回填清单（实测完成后要改的地方）

| # | 位置 | 动作 |
|---|---|---|
| 1 | `convert_crf.py` `_EQQUAL_TIER_OVERRIDE` | 按 tier × codec 填 `(a, b, lo, hi)`；缺键保持缺失（自动回退） |
| 2 | `convert_crf.py` `CALIB_FINGERPRINT` | 翻 `calibrated` 与 `evidence`（含机器/驱动/ffmpeg 指纹） |
| 3 | 三脚本 `_QP_AFFINE_QUALITY` | NVENC 环同步重标（CR-4 跨仓契约，须与 VE 同步） |
| 4 | 三脚本 `TIER_PROFILES` | 依 §B5 结论定 `quality`/`archive` 的 preset 与 tune 分化方案；若决定拉平则改占位注释 |
| 5 | `Plan/ffmpeg_nvenc_knowledge.md` §5 | 补 `-tune uhq` 在 hevc/av1 上的实测结论（现在是空白） |
| 6 | `Plan/ffmpeg_nvenc_knowledge.md` §5.1 附近 | 补 `p6`/`p7` 可靠性实测结论，**替换掉那句从 VE 借来的、未经验证的兜底说明的定位** |
| 7 | `memory/project_rate_control_params.md` | 追加本轮实测结论与 tier 处方的最终取值 |
| 8 | `README.md` | 八档参数表填入实测-backed 数值，删掉"占位"字样 |

> ⚠ 第 8 步之前，README 里凡是占位值都必须显式标着「**占位 / 待实测**」，不得写成已确认的语气。
