# VidUtils 等质量换算表立项 Prompt

> 🔗 **姊妹仓（必须同步维护）**：`Video_Enhancement/Plan/PROMPT_等质量换算立项.md`
> （对侧已升级为 **v2**，本仓同步于 2026-09-30）
>
> * 该文档含 **VE 侧逐锚点实测证据（双编码器）**，素材 `word_world_2.mp4`（720×576 / 25fps / 687 帧）：
>   * **`librav1e`** 等体积值 `(7.0032, −80.993)`，crf18~30 的 ΔPSNR =
>     **+0.59 / −1.21 / −2.57 / −4.17 / −5.79 dB**（码率比 0.91~1.00）
>   * **`libsvtav1`** 等体积值 `(2.145, −21.35)`，crf18~30 的 ΔPSNR =
>     **+1.26 / −0.03 / −1.59 / −3.65 / −5.34 dB**（**video-only** 码率比 **0.868~1.159**）
>   —— 两者同形态：**只有默认工作点准**。这是「等体积 ≠ 等质量」最直接的证据。
>   ⚠ **推论（v2 修正）**：缺陷**不是 rav1e 特例**，而是**线性等体积拟合模型**本身的性质
>   ⇒ 需修复的是 `QUALITY_MAP` **全部行**。
> * 质量参数方案交叉引用：`Video_Enhancement/Plan/Video_Enhancement_质量控制参数修复方案.md`
>   **§6.11.3**（口径分工）、**§6.10**（标定缓存污染）、**§6.11**（rav1e 性能与重标）。
>
> ⚠ **两仓的 `SIZE_MAP` / `QUALITY_MAP` 必须各自逐条相等**（本仓判据 ⑨ 组断言）⇒
> **等质量表也要两份同步副本**，改一侧必须同步另一侧并回跑 ⑨ 组。
> **⑨ 组已扩为 14/14**（2026-09-30 落地：新增 `SIZE_MAP` / `QUALITY_MAP` 逐条相等断言；
> 判据内**显式钉 `size`** 以复现既有等体积期望）。

> 📌 **实施状态（2026-09-30）**：命名已按实现统一 —— 等体积表 `QUALITY_MAP` → **`SIZE_MAP`**；
> 等质量表占用 **`QUALITY_MAP`**（本文档早期提案名 `QUALITY_MAP_QUALITY` / `QUALITY_MAP_VMAF` 作废）；
> CLI 为 `--quality-mode size|quality`（**默认 `quality`**）。详见本仓方案 **§4.12**。

## 修订记录

| 日期 | 修订 | 依据 |
|---|---|---|
| 2026-09-30 | 初稿 | — |
| 2026-09-30 | **评审同步（v2）**：① `model_version=` → **`model=`**；② `psnr/hvs/ssim` 修正为「libvmaf feature（PSNR-HVS）+ 独立滤镜（PSNR/SSIM/XPSNR）」并钉死唯一来源；③ 补 **PSNR-HVS / SSIM / XPSNR** 三指标；④ 缺陷归因从「rav1e 特例」改为模型通病；⑤ 补 ffmpeg 二进制锁定与指标同源硬约束；⑥ 修「立即可执行的第一步」引用了**有缓存 bug 的旧脚本**；⑦ 修「§4.1 门禁」悬空引用 | 与 VE 侧 v2 修订同步（本仓 2026-09-30 实测） |
| 2026-09-30 | **实施状态同步（v3）**：① 登记 M1/M4「非 GPU 部分」**已落地**（HEAD `6ddc46b`）；② 新增「**当前实施状态**」与「**待办清单（含 CPU/GPU 划分）**」；③ 里程碑表加「状态」列并补 **M5**（硬编上机）；④ 订正 §2(b)「7 锚点 + 分段拟合」→ 实际 **5 锚点 + 单直线**；⑤ 补 T4/L40 等硬件依赖标注 | 本仓代码核对 + 实测（2026-09-30） |

## 当前实施状态（2026-09-30，v3 同步）

**已落地**（HEAD `6ddc46b`，**仅非 GPU 部分**）：

- `probe/calibrate_equal_quality.py`（无缓存 + `--resume` + `n_subsample=1`）；
- `QUALITY_MAP`（**等质量**，VMAF 定标）+ `SIZE_MAP`（**等体积**，原名 `QUALITY_MAP` 改名），
  两表并存；`--quality-mode size|quality`（**默认 `quality`**）；零侵入接入
  （`_ACTIVE_MAP` / `set_quality_mode` / `get_quality_map`，未覆盖编码器回退 `SIZE_MAP`）；
- `verify/verify_equal_quality.py`（主门禁 `|ΔVMAF| ≤ 1.0`）、方案 §4.12、README；
- 判据 ⑨ 组 13 → **14**（跨仓 `SIZE_MAP`/`QUALITY_MAP` 逐条相等 + 默认口径 = `quality`）。
- 首版 **单素材** `new5_raw.mp4`（6s / 720p）、5 个软编，`max|ΔVMAF| ≤ 1.0`（判据 5/5）。
- 两仓两表**实测逐条相等**（本仓 2026-09-30 核对：`SIZE_MAP`、`QUALITY_MAP` 均 `True`）。

**未落地** → 见下方「待办清单（含 CPU/GPU 划分）」。

## 待办清单（含 CPU/GPU 划分，2026-09-30）

> **环境分档**：**CPU** = 任意机器（须有 `ffmpeg`；判据另须 `temp/fixture_1080p.mp4`）；
> **GPU** = 须对应硬件 —— NVENC → NVIDIA **T4/L40**（其中 `av1_nvenc` 须 **Ada/L40**，T4 无 AV1 编码器）；
> QSV → **Intel 核显机**；AMF → **AMD 机**；VideoToolbox → **macOS**。
> **M3 主观测试为人工**，不需 GPU。

### A. 仅需 CPU

| # | 待办 | 说明 |
|---|---|---|
| A1 | ~~平行门禁真正判红~~ → **已定案（2026-09-30）** | **VMAF 为唯一判红**（`\|ΔVMAF\| ≤ 1.0`）；`ΔPSNR`/`ΔPSNR-HVS` **降级为 soft 参考**（超界只 WARN、**不改退出码**）—— 等质量表以 VMAF 定标，紧 PSNR 判红会因**跨轴**假阳性。已落地于 `verify/verify_equal_quality.py`；本立项 §2/§5 与方案 §4.12 措辞同步为「参考」 |
| A2 | 判据 / 门禁**复跑确认** | ⑨ 组 14/14 与 `verify_equal_quality.py` 须在**有 `ffmpeg` + `temp/fixture_1080p.mp4`** 的机器复跑（本开发 checkout 无 `ffmpeg`、缺 fixture，⑦ 组即崩） |
| A3 | **rav1e `-speed` 跨仓口径统一**（**前置**） | VE 默认 native（`RAV1E_SPEED_DEFAULT=0`）vs VU 固定 `-speed 10`（`_RAV1E_SPEED=10`），却共用同一 `librav1e` 表值 ⇒ 两仓须选同一档并同步换表（rav1e 为软编，纯 CPU）；否则 M2 跨仓交叉验证不可比 |
| A4 | **M2**：多素材 + 留一交叉验证 | 5 个软编，纯 CPU；当前仅 1 条素材 |
| A5 | 素材补齐 | 屏幕内容/文字、暗场/高噪、高细节纹理（1080p，转码切片，CPU） |
| A6 | 锚点密度 + 拟合形式 | §2(b) 原写「7 锚点 + 分段」，**实际 5 锚点 + 单直线**（见订正）；补不补由残差上界决定 |
| A7 | 标定侧 **5 指标取齐** | 标定 `measure()` 默认 `with_filters=False`，只记 VMAF + PSNR-HVS；PSNR/SSIM/XPSNR 须在标定侧一并采集 |
| A8 | 文档 / 代码收尾 | ffmpeg 二进制指纹入档（§5.4）；`calibrate_equal_quality.py` 默认素材含 576p `word_world_2` 的池化陷阱；立项/方案状态同步 |

### B. 需 GPU（T4 / L40 等）

| # | 待办 | 需要的硬件 |
|---|---|---|
| B1 | NVENC 等质量标定（`h264_nvenc` / `hevc_nvenc`） | NVIDIA **T4 或 L40** |
| B2 | `av1_nvenc` 等质量标定 | **L40（Ada）** —— T4 无 AV1 编码器 |
| B3 | constqp/QP 轴等质量表（VE `D2b`，`to_constqp_qp`） | NVIDIA **T4/L40**（本仓无此路径，仅 VE 需要） |
| B4 | QSV 能力表 + 两仓口径统一（`-cq:v` vs `-global_quality/-q`） | **Intel 核显机** |
| B5 | AMF 能力表 | **AMD 机** |
| B6 | VideoToolbox `-q:v` | **macOS** |
| B7 | 硬编侧上机验收（`verify_equal_quality.py` 硬编条目现 SKIP） | 对应 GPU |

### C. 无需硬件（人工）

| # | 待办 | 说明 |
|---|---|---|
| C1 | **M3** 主观 AB 测试（≥3 人、双盲、随机序、ITU-R BT.500-13） | 人 + 显示设备 |


## 背景
当前 `SIZE_MAP`（2026-09-30 由 `QUALITY_MAP` 改名）采用 **等体积（equal file size/bitrate）** 口径标定（V9 已落地），即：
- 锚点：libx264 CRF 18/21/24/27/30（-preset medium）
- 目标编码器扫 CRF → 记录体积 → 在 log(体积) 曲线上插值得到**等体积 CRF** → 最小二乘拟合 `value = a × x264_crf + b`
- ⚠ 已知：**等体积 ≠ 等质量**。VE 侧 2026-09-30 逐锚点实测（门禁素材 `word_world_2.mp4`，既有等体积表值）：
  | 编码器 | crf 18 | 21 | 24 | 27 | 30 |
  |---|---|---|---|---|---|
  | `librav1e` ΔPSNR | +0.59 | **−1.21** | −2.57 | −4.17 | **−5.79** dB |
  | `libsvtav1` ΔPSNR | +1.26 | **−0.03** | −1.59 | −3.65 | **−5.34** dB |

  ⇒ **只有默认工作点（crf 21）准**，非默认 CRF 单调恶化到 5 dB 以上。
  ⚠ **不是 rav1e 特例**（svtav1 同病且更早失守）⇒ 根因是**线性等体积拟合模型**本身。

## 目标
建立 **等质量（equal perceptual quality）** 换算表，使：
```
libx264 CRF 21  ≈  libx265 CRF ?  ≈  libvpx-vp9 CRF ?  ≈  libsvtav1 CRF ?  ≈  libaom-av1 CRF ?
                     h264_nvenc -cq ?   ≈  hevc_nvenc -cq ?   ≈  av1_nvenc -cq ?
```
在**同一主观/客观画质水平**下互换，而非同文件大小。

## 技术路线

### 1. 质量度量指标（五项客观 + 主观 AB）

| 指标 | 适用场景 | **唯一来源（必须照此取）** |
|------|----------|--------------------------|
| **VMAF** | **主指标**、与主观相关性最强 | `libvmaf` + `-lavfi libvmaf=model=version=vmaf_v0.6.1:log_fmt=json`，取 `pooled_metrics.vmaf.mean` |
| **PSNR-HVS** | 补充（人眼对比敏感度加权） | **libvmaf feature**：`feature=name=psnr_hvs` → `pooled_metrics.psnr_hvs`（**没有独立滤镜**） |
| **PSNR** | 与既有 G7/AC7 判据同轴 | **独立 `psnr` 滤镜**的 `average:` —— **不要**用 libvmaf 的 `psnr_y` |
| **SSIM** | 结构相似度 | 独立 `ssim` 滤镜；libvmaf `float_ssim` 仅作旁证 |
| **XPSNR** | 感知加权 PSNR（ffmpeg 8.0 新增） | 独立 `xpsnr` 滤镜（libvmaf 2.3.1 **无**此 feature） |
| **主观 AB 测试** | 最终定标、解决指标分歧 | 至少 3 人、双盲、随机序、ITU-R BT.500-13 |

> ⚠ **两处 v2 修正（原稿有误）**
> 1. **选项名是 `model=`，不是 `model_version=`** —— 本机实测 `model_version=` 直接报
>    `Error applying option 'model_version' ... Option not found`；默认值已是 `version=vmaf_v0.6.1`。
> 2. **不存在 `hvs` 滤镜**，`-lavfi psnr/hvs/ssim` 无法执行。PSNR-HVS 只能经 **libvmaf feature** 取得。
>
> ⚠ **口径唯一性（硬要求）**：libvmaf 的 feature 与**独立滤镜不是同一个实现，数值不可互比** ——
> libvmaf `feature=name=psnr` 产出的是**分平面** `psnr_y`/`psnr_cb`/`psnr_cr`（无合并键），
> `float_ssim` 也是 libvmaf 自带实现。**定标用哪个口径，验收门禁就必须用哪个口径。**
>
> ⚠ **另两条 v2 实测要求**：
> * **`-frames:v` 必须取源的真实帧数**。VE 侧实测：取部分帧时，裸 `psnr` 与显式 `[0:v][1:v]psnr`
>   会给出两个不同的值并**在多次运行间互换**（±0.01 dB，时序抖动）——帧数不稳，比较就不可信。
> * **显式 `[0:v][1:v]` 标签保持强制**，且**每个新机器开工前须就地复现确认口径**
>   （VE 侧记录：该标签在 A 机复现出 3 dB 差异、在 B 机全长比较逐位相同 ⇒ **结论与环境相关**，
>   见 `Video_Enhancement/memory/ffmpeg-metric-measurement-traps.md`）。
>
> **一次运行出齐 VMAF + PSNR-HVS + PSNR + SSIM + VIF/ADM**：
> ```bash
> ffmpeg -nostdin -hide_banner -v info -i "$DIST" -i "$SRC" -frames:v "$N" \
>   -lavfi "libvmaf=feature=name=psnr_hvs|name=psnr|name=float_ssim:log_fmt=json:log_path=$OUT" -f null -
> ```
> **XPSNR 需第二次调用**（或 `split` 图）。

### 2. 标定流程（参考 Netflix VMAF 标定流程）
```
对每个编码器：
  0. 【先决】钉死主指标与各指标唯一来源（§1）；主指标 = VMAF；记录 ffmpeg 二进制指纹
  1. 选取 5~8 条代表性素材（动画/实拍/高动/低动/屏幕内容/暗场/高细节，各 ≥10s，1080p）
  2. libx264 在 CRF 18/20/22/24/26/28/30 编码 → 得到 7 个质量锚点（VMAF/PSNR-HVS/PSNR/SSIM/XPSNR）
  3. 目标编码器扫 CRF/CQ/QP（建议 10~15 个点，覆盖锚点质量区间）
  4. 计算每个测试点的各指标相对 libx264 锚点
  5. **单调性检查**后插值：在目标编码器「参数 → VMAF」曲线上，找到与 libx264 每个锚点**等 VMAF** 的参数值
     （⚠ VMAF 在低码率端/平涂内容可能出现非单调平台；非单调区间改用保序回归或直接取实测点）
  6. 对 (x264_CRF, 目标参数) 做**分段线性/样条**拟合 → 生成等质量映射表
  7. 交叉验证：留一素材法，验证预测误差（**主：ΔVMAF < 1.0**；ΔPSNR < 0.3 dB 作**参考**，不判红）
```

> ⚠ **三个 v2 补充**
> * **(a) 拟合目标必须与门禁判据同轴。** 步骤 5 用 **VMAF** 定标，步骤 7 却用 **ΔPSNR < 0.3 dB** 验收 ——
>   VMAF 等值**不蕴含** PSNR 等值，会产生"VMAF 达标但 PSNR 门禁红"的死结 ⇒ 主门禁改 ΔVMAF。
> * **(b) 步长/锚点密度**（**v3 订正**）：原稿写「本仓用 **7 个锚点 + 分段**拟合」，但**首版实现是 5 个锚点
>   （`ANCHOR_CRFS = 18/21/24/27/30`，与 VE 侧同）+ 单直线**（`calibrate_equal_quality.py:43,233` 的
>   `fit_line`，**非**分段/样条）—— 文档曾**滞后于实现**。是否补到 7 锚点 + 分段由残差上界决定
>   （见待办 **A6**）；无论选哪档，**两仓步长必须一致**，否则"等质量 vs 等体积差异量化"不可比。
> * **(c) 残差上界**：VE 侧证据显示误差跨区间累积（到 −5.79 / −5.34 dB）⇒ 开工前先用既有直线模型
>   反算残差上界，判定"直线 vs 分段"是否必要（VE 侧已把此项并入 M0）。

### 3. 素材集建议（最小可行集 → 逐步扩充）
| 类别 | 来源 | 时长 | 分辨率 | 备注 |
|------|------|------|--------|------|
| 动画平涂 | new5_raw.mp4 切片 | 10s | 1080p | 现有 |
| 实拍自然 | new4_raw.mp4 切片 | 10s | 1080p | 现有 |
| 高动作/运动 | Earth.at.Night... 切片 | 10s | 1080p | 现有 |
| 屏幕内容/文字 | 需新增 | 10s | 1080p | 关键补齐 |
| 暗场/高噪 | 需新增 | 10s | 1080p | 关键补齐 |
| 高细节纹理 | 需新增 | 10s | 1080p | 关键补齐 |

### 4. 编码器覆盖范围
| 编码器 | 质量参数 | 备注 |
|--------|----------|------|
| libx264 | CRF | 基准轴 |
| libx265 | CRF | 已有等体积表 |
| libvpx-vp9 | CRF | 已有等体积表 |
| libsvtav1 | CRF | 已有等体积表，**preset 固定 8** |
| libaom-av1 | CRF | 已有等体积表，**cpu-used 固定 6** |
| librav1e | QP | 走独立刻度 `crf_to_rav1e_qp`；⚠ **本仓固定下发 `-speed 10`**（`vidcrop_cpu_v2.py`），而对侧 **VE 默认 native（不下发）** —— 见下方口径缺口 |
| h264_nvenc | -cq | CQ 轴 |
| hevc_nvenc | -cq | CQ 轴 |
| av1_nvenc | -cq | CQ 轴，**量程 0~63** |
| h264/hevc_vaapi | -qp | QP 轴（≈ x264 QP） |
| h264/hevc_qsv | 待上机确认 | ⚠ **两仓口径不一致**：本仓写 `-global_quality / -q`，而 VE 的 `quality_map.py` 把 qsv 归入 `_CQ_CODECS`（下发 **`-cq:v`**）。上机确认后须**两仓统一**（无 Intel 机不可断言） |
| *_videotoolbox | -q:v | 需 macOS 上机 |

> ⚠ **跨仓口径缺口（v2 新发现，须在标定前澄清；v3 仍未闭环 → 待办 A3）**
> 两仓共用同一 **`SIZE_MAP['librav1e'] = (7.0032, −80.993)`**（等体积；等质量行为 `(7.6674, −87.5783)`）。
> **本仓固定下发 `-speed 10`**（`vidcrop_cpu_v2.py` 的 `_RAV1E_SPEED=10`；判据 ⑪ 组断言其必须存在），
> 而**对侧 VE 默认 native（`RAV1E_SPEED_DEFAULT=0`，不下发）**；`-speed` 会整体平移码率曲线 ——
> VE 侧记 speed 10 的等体积解为 **qp 77**（`(6.8159, −66.093)`），与表值给出的 qp 66 不同。
> ⇒ **两仓当前落在不同的 rav1e speed 口径上**（VE = native、VU = speed 10），却共用同一张表值。
> ⇒ **须两仓统一 speed 口径并同步换表**（rav1e 为软编，**纯 CPU 即可**，见待办 **A3**），
> 否则 M2 的跨仓交叉验证与「等体积 vs 等质量差异量化」都不可比。参见对侧 §4.4。

### 5. 交付物
1. `probe/calibrate_equal_quality.py` —— 标定脚本（可复现、可扩展素材集；按 §2 步骤 0~7，含单调性检查与残差上界）
2. `QUALITY_MAP`（等质量换算表；原等体积表改名 **`SIZE_MAP`**，两表并存不覆盖）
3. `verify/verify_equal_quality.py` —— 回归判据（**主门禁 ΔVMAF**；PSNR 为**参考**，不判红；指标按 §1 唯一来源取数）
4. 方案文档新增章节：记录标定方法、素材集、拟合参数、误差分析、已知局限 + **各指标唯一来源与 ffmpeg 二进制指纹**
5. README 更新：说明何时用等体积表、何时用等质量表

> ⚠ **跨仓交付物差异（v2）**：VE 侧另需 **`QUALITY_MAP_QP`（QP/constqp 轴）** ——
> 因为 VE 走 `external/*/nvenc_sdk.py` 的 **ctypes 直连 SDK**，`to_constqp_qp()` 需要该轴的等质量值。
> 本仓（VidUtils）无此路径，**不需要**该表，但 **`QUALITY_MAP` 必须与 VE 侧逐条相等**。

## 里程碑

| 阶段 | 交付 | 验收标准 | 状态（2026-10-01 更新） | 环境 |
|------|------|----------|--------------------|------|
| M1 | 标定脚本 + 3 条核心素材跑通 libx265/libvpx-vp9/libsvtav1 | **主门禁 ΔVMAF < 1.5**；待验收侧同源 | **部分落地**：脚本 + 5 软编已跑通、主门禁达标；素材仅 **1 条**（非 3 条） | CPU |
| M2 | 补齐 6 条素材 + 软编交叉验证（留一法） | 留一法 **ΔVMAF < 1.0**；ΔPSNR < 0.3 dB 作**参考**（不判红） | ❌ **LOO 已跑但未达标**（worst ΔVMAF 4.18~13.73 vs 门禁 1.0）。根因＝**表格式（单行仿射）承载不了跨素材关系**，非过拟合/素材污染。工具：`probe/loo_equal_quality.py` | CPU |
| M3 | 主观 AB 测试（≥3 人）修正系统性偏差 | 主观与 VMAF 预测一致性 > 85% | **未做** | 人工（不需 GPU） |
| M4 | 两份 `QUALITY_MAP` 同步 + 判据入库 + 文档归档 | ⑨ 组全绿（**基线 13/13 → 14/14**）、无回归 | **已落地**：两仓两表逐条相等 + ⑨ 组 14 项 + §4.12/README 归档（2026-10-01 复跑 ⑨ 组 14/14 全绿） | CPU |
| **M5** | 硬编（NVENC/QSV/AMF/VT）等质量标定 + constqp/QP 轴（VE）+ 解除回退 | 各硬编 `max|ΔVMAF| ≤ 1.0`；QSV 两仓口径统一 | **未做** | **GPU**：NVENC→T4/L40（`av1_nvenc` 须 **L40**）；QSV→Intel 机；AMF→AMD 机；VT→macOS |

> ⚠ **v2 修正**：原 M4 的验收写「§4.1 门禁全绿」，但本文档**没有** §4.1 门禁节（疑为从 VE 侧拷贝的悬空引用）。
> 本仓真实门禁 = `verify/verify_quality_mapping.py` 的 ⑨ 组；VE 侧门禁是 `crf_cq_unification_verify.py` 的 G0~G10。
>
> ⚠ **v3 修正**：原 M2 写「全编码器覆盖」，把软编（CPU）与硬编（GPU）混在一个里程碑里 ⇒
> 拆分为 **M2 = 软编多素材/留一（CPU）** 与 **M5 = 硬编上机标定（GPU）**。
> 里程碑表现已加「状态 / 环境」列；M1 为**部分落地**（素材数不足），M4 为**已落地**。
>
> ⚠ **v4 修正（2026-10-01）**：M2 的 LOO 门禁**实测未达标**，且根因已定性为
> **表格式**（`(a, b, lo, hi)` 单行仿射）而非标定执行或素材选择 —— `libsvtav1` 连
> **训练内**（素材自身拟合）ΔVMAF 都达 5.61。**表值尚未回填**（落表格式待裁定）。
> 同时 harness 已与 VE 侧同源（新增 PAVA 保序、分段诊断、`--selftest`、`points.json` 断点），
> `--loo` 迁为独立脚本 `probe/loo_equal_quality.py`。详见对侧报告 §3.2 / §3.3。

## 风险与对策
| 风险 | 对策 |
|------|------|
| VMAF 对某些内容（动画/屏幕录制）不准 | 引入 **PSNR-HVS**（libvmaf feature，已确认可用）互补，主观测试兜底。⚠ MS-SSIM 未在本轮确认（libvmaf feature 名为 `float_ms_ssim`），使用前须先核实 |
| **指标口径分叉**（v2 新增） | libvmaf feature 与独立滤镜**数值不可比**（PSNR 分平面 vs 合并；`float_ssim` vs `ssim`）⇒ **定标与门禁必须同源**（§1） |
| **拟合目标与门禁不同轴**（v2 新增） | 用 VMAF 定标却用 ΔPSNR 验收会产生死结 ⇒ 主门禁改 ΔVMAF，PSNR 平行（§2(a)） |
| **线性模型不足**（v2 新增） | VE 侧证据：误差跨区间累积至 −5.79 / −5.34 dB ⇒ 先验证残差上界；**首版实为单直线**（非分段，见 §2(b) 订正），是否改分段见待办 **A6**；两仓口径须可比 |
| 编码器升级导致表值漂移 | 版本锁定 ffmpeg/编码器版本（**并记录二进制指纹**）；CI 定期跑回归 |
| 素材集不具代表性 | 按 Netflix 公开测试集分类学覆盖；后续持续扩充 |
| 计算资源/时间过大 | 先跑「快速模式」：少素材、少参数点、先跑 VMAF；再全量。⚠ rav1e 实测 **native 0.055× 实时**、`-speed 10` **0.177× 实时**（VE 侧 2026-09-30 计时），native 标定可接受 |

## 与现有体系的兼容
- **不删除**现有等体积表（现名 **`SIZE_MAP`**），保留给「码率受限、文件大小优先」场景
- 新增 **`QUALITY_MAP`**（等质量口径），供「画质优先、存储/带宽次要」场景
- CLI 新增 `--quality-mode size|quality` 选择换算表（**默认 `quality`**；等体积路径完整保留，可切回）
- `_resolve_quality_params()` 读取对应表，**零侵入**现有逻辑

## 首版落地步骤（2026-09-30 **已执行**）

> 下列步骤已落地为 `probe/calibrate_equal_quality.py` 等（**M1 部分完成**）；此处保留作历史记录。
> 剩余项见「待办清单（含 CPU/GPU 划分）」。
```bash
# 0. 记录 ffmpeg 指纹（口径随构建变化；姊妹仓实测为 8.0.1-+vmaf）
ffmpeg -hide_banner -version | head -1

# 1. 创建标定脚本骨架（⚠ 必须复用**无缓存**版，v2 修正）
cp probe/calibrate_soft_offsets_nocache.py probe/calibrate_equal_quality.py
#    ⚠ 原稿写的是 probe/calibrate_soft_offsets.py —— 那正是在**对侧**（VE 立项）§6.2 记载的
#      「prep.mp4 按文件名复用、不校验 --src/分辨率」的有缓存 bug 版本，不可复用。
# 2. 修改：体积插值 → VMAF 插值（+ 单调性检查）；增加 libvmaf feature 调用
#    （psnr_hvs / psnr / float_ssim）+ 独立 ssim/xpsnr 调用；增加多素材循环
# 3. 先跑 libx265 + 1 条素材（new5_raw.mp4）验证流程
# 4. 产出首版对比表：等体积 vs 等质量 差异量化
```

> ⚠ **素材路径**：`input_videos/` 与项目根同级（WSL 开发机为 `/mnt/d/Workspace_Python/input_videos`，
> 生产侧 `/workspace/input_videos`）⇒ 脚本内用变量表达，勿硬编码。
> ⚠ **所有脚本一律加 `< /dev/null`**（后台进程组 + tty stdin 下会被 SIGTTOU 整组停住）。

## 参考资料
- Netflix VMAF 标定流程：https://github.com/Netflix/vmaf/tree/master/resource/doc
- FFmpeg VMAF 滤镜用法（⚠ **选项名是 `model=`**，`model_version=` 会报 `Option not found`）：
  `ffmpeg -i dist -i ref -lavfi libvmaf=model=version=vmaf_v0.6.1:log_fmt=json -f null -`
- PSNR-HVS（libvmaf feature）：`libvmaf=feature=name=psnr_hvs`（**无独立滤镜**）
- ITU-R BT.500-13 主观测试方法学
- 现有方案文档：`Plan/VidUtils_质量控制参数修复方案.md` §V9、§3、§4
- 姊妹仓立项（v2，含双编码器逐锚点实测）：`Video_Enhancement/Plan/PROMPT_等质量换算立项.md`

---

**优先级**：P1（画质一致性是视频处理工具的核心竞争力）
**预估工期**：**M1~M4（CPU）大部分已落地**；剩余工时主要在 **M2 多素材补齐（CPU）** 与 **M5 硬编上机（T4/L40 等 GPU）**
**负责人**：待指派
**评审人**：需包含有主观测试经验的工程师
**v2 修订**：2026-09-30 与 VE 侧同步（口径修正 + 三指标补齐 + 范围修正）
**v3 修订**：2026-09-30 实施状态同步（登记已落地项 + **CPU/GPU 待办划分** + 补 M5 + 订正滞后内容）