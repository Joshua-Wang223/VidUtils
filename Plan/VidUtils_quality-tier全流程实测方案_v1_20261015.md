# 《VidUtils `--quality-tier` 全流程实测方案》v1

- 日期：2026-10-15（**R1 修订：2026-10-17**）
- 主方案（本文是为它服务的）：`Plan/VidUtils_质量参数统一_quality-tier方案_v3_20261015.md`
  （本文中的 §B1~§B8 等编号即主方案 Part B 的延续）
- 适用脚本：`vidcrop_hwaccel.py` / `vidcrop_cpu_v2.py` / `convert_sdr_to_hdr.py`
- **R1 修订要点**（对应主方案 §13.15 R1-1~R1-7）：
  1. **主测不再等阶段一 CLI**（H3 时序重排）：标定主测走 harness `Accessory/probe/calibrate_equal_quality.py`（`--preset-override` **已落地**，2026-10-17；`--tag` 已有）；生产 CLI 的端到端验证（dry-run / 窗口钳制 / 指纹告警 / strict exit 2）是**阶段一的 DoD**，与本方案并行推进。
  2. **三处阈值已拍板**：画质 **3%** / 耗时 **1.5×**（判据 = **CPU(user+sys) 时间比**，不是墙钟）/ `_NVENC_PRESET_RETRY` 红线 **1%**（原「需你确认」的段落随之定稿）。
  3. **A/B 档值对按 §13.10R 终值更新**：NVENC `p5↔p6`、x265 `slow↔slower`、svtav1 `5↔3`（旧 `6↔4` 仅作原理证据）；CPU 环新增 **`libx264`**（随档后必须进环）。
  4. **锚点扩展（M7）**：`ANCHOR_CRFS` 只覆盖中段，`draft/test/near-lossless/lossless` 需扩锚（+`0/6/33/39/45`）或明确 LOO 放宽——二选一写进报告。
  5. **范围边界**：**HDR 域 × tier 的标定不在本方案**（属 SDR2HDR 工作流；复合规则见主方案 §4.4.1 与 `Plan/Handoff_VMAF在SDR2HDR管线不可标定_VU→eqq标定_20261015.md`）。

---

## B0. 这份方案要回答的 5 个问题

| # | 问题 | 决定什么 | 由哪一步产出 |
|---|---|---|---|
| **P1** | 非 `balanced` **七档**，各自**实际**比 `balanced` 好多少 / 贵多少？ | 档位到底值不值得存在 | §B4 三环主测 |
| **P2** | 各档的 `QUALITY_MAP` 行能否按 LOO 门禁（软编 ≤5.9 / rav1e ≤7.5）标定？ | 能否把 `_EQQUAL_TIER_OVERRIDE` 填上、把"未标定"告警摘掉 | §B4 三环主测 |
| **P3** | `quality` 与 `archive` 该靠 **D1 锚点**还是 **D2 preset** 还是 **tune** 区分？ | Part A §12.3 的 Q3（**已拍板 D1+D2**；本 bench 验证收益与代价、保留常量层回退） | **§B5 专项 bench** |
| **P4** | `p5` / `p6` / `p7` 的可靠性到底如何（含 `_NVENC_PRESET_RETRY` 实际触发率）？ | 修正上一版的过度解读；**红线 1% 已拍板** | **§B5.3** |
| **P5** | `-tune uhq` 在 hevc/av1 上到底有没有收益、代价多大？ | `quality`/`archive` 的分化落 preset 还是 tune | **§B5.1 / §B5.4** |

---

## B1. 实测对象矩阵

| 环 | 机器 / GPU | 编码器 | 参与 tier |
|---|---|---|---|
| **CPU 环**（阶段三） | 本机（无需 GPU） | **`libx264`（R1 新增：随档后必须进环）**、`libx265`、`libvpx-vp9`、`libaom-av1`、`libsvtav1`、`librav1e` | `archive` / **`quality`（翻档后的默认，优先测）** / `balanced` 作基线；`preview`/`draft`/`test` 次之；`lossless`/`near-lossless` 最后（LOO 门禁单独放宽） |
| **T4 环**（阶段四） | Tesla T4 / 驱动 580.65.06 / ffmpeg 9.0.2 | `h264_nvenc`、`hevc_nvenc` | 同左（**`quality` 优先**，因为它是默认档） |
| **L40 环**（阶段五） | NVIDIA L40 / 驱动 580.65.06 / ffmpeg 9.0.2 | `av1_nvenc`（`--expect-av1` fail-fast；Turing 代无 AV1 编码单元，见 `Accessory/probe/verify_nvenc_quality_gpu.py:27`） | 同左 |

> **每环只换「机器 / 编码器」，素材池、锚点、口径全部沿用上一环** —— 这样一旦 CPU 环与 T4 环的结论打架，能确定是"口径漂移"而非"素材差异"。

---

## B2. 素材池与口径（锁死，不得中途改）

沿用现有等质量标定口径（`convert_crf.py` 的等质量分支）：

- **素材池**：现有 **17 条 eqq 素材池**（12×6s + 5×10s），覆盖立项 §3 全部 6 类内容 + 实拍影视 + 暗场
- **锚点**：`ANCHOR_CRFS = [18, 21, 24, 27, 30]` 只覆盖中段；**R1-M7 扩展**：`draft(36)` / `test(45)` / `near-lossless(6)` / `lossless(0)` 需加 `[0, 6, 33, 39, 45]`（harness 内为常量）——若不扩，则这四档明确「不标定、LOO 放宽」并写进报告，**不默认**
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
| **耗时** | **CPU(user+sys) 时间（秒）为主判据，跑 2 次取均值**（R1 拍板；墙钟仅辅助——墙钟会被「这一秒分到几核」扭曲，主方案 §13.11 有实测教训） | `/usr/bin/time -v` 解析（注意 `Elapsed` 标签含冒号，须 `rsplit(': ', 1)`；`bench_preset_ab.py` 的 `parse_time_file` 已带此坑注释） |
| **峰值显存**（仅 NVENC 环） | 编码期间 GPU 显存峰值 | `nvidia-smi --query-gpu=memory.used --format=csv` 采样取最大 |
| **确定性** | 同参数同素材连跑 2 次，成品 **md5 是否一致** | `md5sum` |
| **`_NVENC_PRESET_RETRY` 触发率**（仅 NVENC 环） | 落到 p5/p6/p7 的命令中，有多少比例打印了降档到 p4 重试（`_NVENC_PRESET_RETRY` 的兜底分支） | 抓 stdout / stderr 计数 |

---

## B4. 三环主测（回答 P1 / P2）

每环、每个编码器、每个 tier 各跑一轮：

```
# 主测结构（R1：harness 驱动，不依赖生产 CLI —— H3；以 CPU 环 libx265、archive 档为例）
python3 Accessory/probe/calibrate_equal_quality.py \
    --codec libx265 --tag archive_cpu \
    --preset-override <档值>  # ✅ 已落地（2026-10-17）：替换 BASE_LOCK 的 medium/8；传 §13.10R 档值
    # ↑ 其余参数以脚本 --help 为准（--tag 已有）
```

采集：VMAF / 码率 / 耗时 / 确定性；NVENC 环加采峰值显存与 `_NVENC_PRESET_RETRY` 触发率。

**CLI 端到端（阶段一 DoD，R1）**：窗口钳制、指纹告警、`--strict-quality` / `--strict-calib` 的 exit 2、五样例表（主方案 §4.4）在三脚本上的行为验证——与标定主测**并行、互不阻塞**（主方案 §6.2 / §11）。

**标定入表判据**：先过 **LOO**（`loo_equal_quality.py`：软编 ≤5.9、rav1e ≤7.5；`GATE_ANCHORS=(0,27)`），**不要**只看 `verify_equal_quality.py` 的 `TOL_VMAF=1.0`——README 已写明它是 in-sample 回归门，「不能为表值背书」。

**NVENC 环额外要求**：必须**同步重标 `_QP_AFFINE_QUALITY`**（hwaccel / cpu_v2 / sdr **三处逐条相等**）——它是 CR-4 跨仓契约、⑨ 组会断言逐条相等，只改 `QUALITY_MAP` 不改它会红。

**本机限制**：`verify_equal_quality.py` 在无 GPU 机器上 NVENC 三档**全 SKIP** ⇒ T4 / L40 环必须在有卡机器上补跑，不得用本机 CI 假装通过。

---

## B5. Q3 专项 bench（回答 P3 / P4 / P5）

这部分是 Part A §12.3 的数据来源。**R1 注**：preset 档值已按 §13.10R 终值定案（Q3=D1+D2 已拍板），本 bench 的职责从「选值」改为**验证收益与代价**——超阈值时走常量层回退（只改 `TIER_PROFILES`，不动代码）。

### B5.1 A/B 组合清单

每条组合都在**同一 VMAF 目标**下比较码率与耗时：

| 轴 | A 侧 | B 侧 | 在哪一环做 |
|---|---|---|---|
| **D2 preset** | NVENC `p5` | NVENC `p6` | T4 / L40 环（**R1 终值对**；`p7` 移入 §B5.3 可靠性检查） |
| **D2 preset** | `libx265 slow` | `libx265 slower` | **阶段零点五已先跑**（主方案 §6.15 / §13.11）；CPU 环复核 |
| **D2 preset** | `libsvtav1 5` | `libsvtav1 3` | **阶段零点五已跑 `6↔4`**（原理证据）；**CPU 环按 R1 终值对重跑** |
| **D2 preset** | `libx264 slow` | `libx264 slower` | CPU 环（**R1 新增**：libx264 随档后，quality↔archive 的差分轴） |
| **复核项** | `libaom-av1 cpu-used 2` | `libaom-av1 cpu-used 4` | CPU 环（探针提示 4 有增益但 ~2.5× CPU；R1 暂取 2，超 3% / 1.5× 阈值则维持 2） |
| **P5 tune** | 不带 `-tune` | `-tune uhq`（仅 hevc/av1，先过主方案 §4.3 的探测） | T4 / L40 环 |

### B5.2 为什么要把 tune 也放进同一个 bench

`-tune uhq` 会自动启用 lookahead + temporal filter（`Plan/ffmpeg_nvenc_knowledge.md` §5），它对率失真的移动可能比 `p5→p6` 更大或更小——两者必须在**同一批素材、同一口径**下横向比较，否则无法判断「`quality`/`archive` 的分化该落在 preset 上还是 tune 上」。

而且若 `uhq` 收益显著，还有额外好处：可以把 `uhq` 收敛到**只有 archive 一档**上（`quality` 用 `hq`），从而少破一份标定。

### B5.3 `_NVENC_PRESET_RETRY` 触发率（P4）

上一版把该常量误读为「p6/p7 已知偶发初始化失败」并据此下判断，已撤回（见 Part A §12.3.2 更正）。本环把它作为**直接测量项**：

- 每条落在 `p5/p6/p7` 的命令都记录是否出现降档到 p4 重试（`_NVENC_PRESET_RETRY` 的兜底分支输出）
- 统计 `触发次数 / 总次数`，连同机器、驱动、ffmpeg 版本一并记入报告
- **触发率红线 = 1%（R1 已拍板）**：超线 ⇒ 该 preset 判定不可接受（archive 是"一次转好、长期保存"场景，不允许偶发重来）⇒ **走常量层回退**（如 `p6→p5` 或 `slower→slow`），回退动作只改 `TIER_PROFILES`

### B5.4 判据与阈值（**R1 已拍板**）

| 情形 | 结论 |
|---|---|
| `p6` 相对 `p5`（`slower` 相对 `slow`）等 VMAF 码率收益 **< 3%** 且耗时 **≥ 1.5×**（CPU 时间比） | **取消 D2**：`quality` / `archive` 的 preset 拉平，只靠 D1 区分（改常量层） |
| 收益 **≥ 3%** 且 §B5.3 触发率可接受 | 保留 D2，两档标定一起做 |
| `uhq` 收益显著（同样按上面的 3% 门槛） | **改走「分化落在 tune」路线**：`quality` 用 `hq` / `archive` 用 `uhq`，preset 拉平 |
| `uhq` 收益不明显但代价明显 | 从 `quality` / `archive` 里**摘掉 uhq**，减少破标定的面 |

> **R1 定稿（2026-10-17）**：①画质阈值 **3%**；②耗时阈值 **1.5×**（判据 = CPU(user+sys) 时间比）；③`_NVENC_PRESET_RETRY` 红线 **1%**。原三处「待确认」全部关闭。

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
    aom_effort_probe/ · vp9_effort_probe/     # R1：已完成（结论见主方案 §13.10R）
```

- 每份产物都要能回答「这数据是哪台机器、哪个驱动、哪个 ffmpeg 出的」。
- 换构建 / 驱动须重标（`convert_crf.py` 的指纹机制已是如此）。

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
| 2 | `convert_crf.py` `CALIB_FINGERPRINT` | 翻 `calibrated` 与 `evidence`（含机器/驱动/ffmpeg 指纹）；**M4：按「生效编码器（含降级）× strategy」登记** |
| 3 | 三脚本 `_QP_AFFINE_QUALITY` | NVENC 环同步重标（CR-4 跨仓契约，须与 VE 同步） |
| 4 | 三脚本 `TIER_PROFILES` | R1：preset 已按 §13.10R 定案；本项改为「依 §B5 结论**确认或常量层回退**」（含 tune 分化按 §B5.4 表） |
| 5 | `Plan/ffmpeg_nvenc_knowledge.md` §5 | 补 `-tune uhq` 在 hevc/av1 上的实测结论（现在是空白） |
| 6 | `Plan/ffmpeg_nvenc_knowledge.md` §5.1 附近 | 补 `p5`/`p6`/`p7` 可靠性实测结论，**替换掉那句从 VE 借来的、未经验证的兜底说明的定位** |
| 7 | `memory/project_rate_control_params.md` | 追加本轮实测结论与 tier 处方的最终取值 |
| 8 | `README.md` | 八档参数表填入实测-backed 数值，删掉"占位"字样 |

> ⚠ 第 8 步之前，README 里凡是占位值都必须显式标着「**占位 / 待实测**」，不得写成已确认的语气。
