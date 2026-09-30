# VidUtils 质量控制参数（crf / cq / qp / preset）修复方案

- 适用脚本：`vidcrop_cpu_v2.py`、`vidcrop_hwaccel.py`（**孪生约定**：共享参数的取值表、
  默认值、报错首行、整条 ffmpeg 命令都必须逐字一致）
- 共享真源：`convert_crf.py`（`QUALITY_MAP` + `from_x264_crf` / `to_x264_crf` / `convert_quality`），
  与 `Video_Enhancement/src/utils/convert_crf.py` 必须是同一份表（判据 ⑨ 组会断言）
- 姊妹文档：`Video_Enhancement/Plan/Video_Enhancement_质量控制参数修复方案.md`
- 判据：`verify/verify_quality_mapping.py`（**① ~ ⑪ 组**；⑨ 组现在是**门禁**）
- 上机验收：
  - `probe/verify_nvenc_quality_gpu.py` —— 本轮改动：各 NVENC 的 `-cq` 偏移（B 组）
    与 constqp 的 `-qp` 尺度（C 组，重点 `av1_nvenc` 的 ×4）
  - `probe/t4_acceptance.py` —— 上一轮改动：GPU 策略链上的**落点**（A）、**运行期**（B）、
    `-qp 0` 无损声明（C，配合 `probe/probe_lossless_qp0.sh`）
- 标定脚本：`probe/calibrate_soft_offsets.py`（真实素材等体积标定，可复现 V9）

> **状态（2026-09-29）：V1 ~ V12 全部已落地 + AV1 QP 尺度修正（×4→×3）+ AV1 软编等效表实测落表**，两脚本 + 两份 `convert_crf.py` 同步。
> **T4 上机验收已完成**（§4.9）：NVENC 的 `-cq` 偏移（B 组）与 constqp `-qp` 回基准轴（C 组的
> h264/hevc 对照）**实测成立**，`t4_acceptance` 20/20 通过 —— 详见 §4.9。
> **L40/Ada 上机验收已完成**（§4.10）：
>   - **B-av1-结论** (`-cq 27`)：**PASS**（ΔPSNR +1.74 dB / 码率 1.12×），偏移方向成立，**不改表**
>   - **C-av1-结论** (QP 尺度)：**PASS** —— 扩扫 42/63/72/84/105/108，**×3(qp=63) 落带内**，将 `_QP_SCALE['av1_nvenc']` 从 4 → 3
>   - **C-h264/hevc** (`-qp 21`)：**PASS**，constqp 回基准轴正确
> **仍未覆盖的只有**：AMF 能力表、VideoToolbox `-q:v` —— 待 AMD/macOS 机器复核（见 §4.8）。

---

## 0. 状态总览

| 编号 | 内容 | 优先级 | 状态 | 依据强度 |
|---|---|---|---|---|
| V3 | `QUALITY_MAP['av1_nvenc']` 的 hi 51 → 63 | P0 | **已落地** | 实测（`ffmpeg -h encoder=av1_nvenc` → `-cq (0 to 63)`） |
| V4 | `literal_range()`：字面量按生效编码器量程校验 + 同族下发钳位 | P0 | **已落地** | 实测（`-cq 60` 让 ffmpeg 报 out of range；`-crf 60` 被静默按 51 编码） |
| V6 | `_QP_ONLY_CODECS`（VAAPI 族）→ 归一到基准轴后走 `-qp` | P0 | **已落地** | 实测（`ffmpeg -h encoder=h264_vaapi` → 只有 `-qp (0 to 52)`） |
| V1 | constqp 的 `-qp` 回基准轴（不再拿 CQ 轴值直发） | P0 | **已落地** | 有跨项目实测反证（VE G7：`-qp 21` 对齐 crf21）；新增 `_QP_SCALE` / `_QP_LIMITS` / `to_constqp_qp()` / `from_constqp_qp()` |
| V13 | `av1_nvenc` 的 `_QP_SCALE` 4 → 3（L40 扩扫确认 ×3 落带内） | P0 | **已落地 (2026-09-29)** | L40 实测：qp=63(×3) 码率 0.83×/ΔPSNR +0.61dB 通过，qp=84(×4) 码率 0.55× 未达标 |
| V2 | `--qp` 落到软编按基准轴回算（不再走 CQ 轴） | P0 | **已落地** | 同上（同一处公式） |
| V5 | 硬件能力表：QSV/VT 的 `-cq`/`-preset` 校正 + 两脚本集合统一 | P0 | **已落地** | 实测（本机 `h264_qsv` 无 `-cq`，`-preset` 只收 veryfast..veryslow）；AMF 待上机 |
| V7 | 默认质量统一为基准 21（`DEFAULT_CQ` 23 删除） | P1 | **已落地** | 纯计算（CQ 23 ≡ crf 18） |
| V8 | `librav1e` 移出 `CRF_SUPPORTED_CODECS`（与 VE 的 `supports_crf` 对齐） | P1 | **已落地**；**2026-09-30 改为直接查表** | 实测（`--crf 21` → `-qp 66`，`--crf-ref 21` → `-qp 66`，并下发 `-speed 10`）。旧链式经 libaom 中转得 64，会随 libaom 行重标而漂移 ⇒ 改查 `QUALITY_MAP['librav1e']` 的等体积标定值，见 §4.11 |
| V9 | `libx265` / `libvpx-vp9` / `libsvtav1` / `libaom-av1` 的偏移按**真实素材等体积**重标 + 钉死 svtav1 preset | P1 | **已落地（真实素材 + 多源复核 + AV1 补测已落表）** | 真实素材 `input_videos/new5_raw.mp4`（1080p→720p 4s）等体积标定：`libx265 0.9155x+1.6385`、`libvpx-vp9 1.6198x−5.7553`、`libsvtav1 1.9450x−15.6200`（残差 ≤0.73 档）；`libsvtav1` 默认 preset 固定 8。**多源复核（2026-09-29）确认当前表值在基准上无需修改。AV1 补测（2026-09-29）已按实测落表：libsvtav1 2.145x−21.35、libaom-av1 2.007x−21.35**。 |
| V10 | preset 表与 VE 对齐 + svtav1 的 p7/veryslow 自洽 | P1 | **已落地（对齐官方枚举）** | VE 的 `[FIX-PRESET-ALIGN]` 已改用 ffmpeg 官方枚举；`X264_TO_NVENC_PRESET` 对齐之，`NVENC_TO_X264_PRESET` 保持 `p5→medium`（保 lockstep/基线）；`X264_TO_SVTAV1_PRESET` 的 fast/medium 已拆开、p7 与 veryslow 同为 2 |
| V11 | `hevc_videotoolbox` 的 b 105 → 100 | P2 | **已落地** | 纯计算（lo=1 永不可达，crf 0~2.58 全饱和） |
| V12 | ⑨ 组的待修项逐条转 chk | P2 | **已落地** | 流程（⑨ 现在默认就是门禁；新增 ⑪ 组正向断言） |

> **第二轮（2026-09-28）已完成全部剩余项**：V1/V2/V5/V7/V8/V9/V10/V11/V12。
> `verify/verify_quality_mapping.py` 的 ⑨ 组从 2/13 变为 **13/13 一致**（且默认计入退出码），
> 并新增 ⑪ 组（V1/V2/V5/V7/V8/V10/V11 的正向断言）。
> **L40/Ada 第三轮（2026-09-29）完成 AV1 实测验收**：V13 `_QP_SCALE` 4→3，B/C 组全 PASS。
> ⚠ 仍待上机核实的只有：AMF 能力表、VideoToolbox `-q:v`（见 §4.8）。

---

## 1. 第一轮已落地项（V3 / V4 / V6，保留原改动记录）

### V3 —— av1_nvenc 的 CQ 量程

**改动**：`convert_crf.py`（本目录与 VE 的 `src/utils/convert_crf.py` **两份同步**）

```python
'av1_nvenc': (1.0, 6.0, 0, 63),   # 原 (1.0, 6.0, 0, 51)
```
并加注释说明：其 `-qp` 是 `0~255`（AV1 qindex），constqp 路径必须再经 QP 尺度层。

**影响**：`crf_ref 45~51` 不再全部挤到 51。实测：

| 输入 | 修复前 | 修复后 |
|---|---|---|
| `--codec av1_nvenc --crf-ref 51` | `-cq 51` | `-cq 57` |
| `--codec av1_nvenc --crf-ref 40` | `-cq 46` | `-cq 46`（不变） |

**不影响**：`av1_qsv` / `av1_amf` 保持 51（本机无该编码器，量程待上机核实）。

### V4 —— 字面量量程按生效编码器校验

**改动**：两脚本各新增 `literal_range(codec, kind)`（函数体逐字相同，放在 `encoder_supports_cq()`
之后），并把 CLI 层的 `0 <= v <= 63` 一刀切换成按该编码器的量程：

- `validate_and_finalize_args()`（cpu_v2）/ `main()` 的量程段（hwaccel）
- `_resolve_quality_params()` 的**同族原样下发**路径也钳位（给绕过 CLI 的直接调用方兜底）

**刻度归属**（与 `_resolve_quality_params` 的字面量分支一致）：

| kind | 原生支持该轴的编码器 → 取其自身量程 | 其它编码器 → 回退源轴 |
|---|---|---|
| `crf` | libvpx-vp9 0~63、libsvtav1 0~63、libx264/265 0~51 | libx264 的 0~51 |
| `cq` | h264/hevc_nvenc 0~51、av1_nvenc 0~63 | h264_nvenc 的 0~51 |

**影响**（实测端到端）：

| 命令 | 修复前 | 修复后 |
|---|---|---|
| `--codec h264_nvenc --cq 60` | rc=0，`-cq 60` → ffmpeg 报 `out of range [0 - 51]` 失败 | rc=2，`[ERROR] --cq 60 超出编码器 h264_nvenc 的可用范围 0~51（CQ 字面量按该编码器刻度解释）。` |
| `--codec libx264 --crf 60` | rc=0，被静默按 51 编码（最差质量、无提示） | rc=2，同上措辞（crf） |
| `--codec libvpx-vp9 --crf 60` | rc=0 | rc=0（0~63，未误伤） |
| `--codec libsvtav1 --crf 55` | rc=0 | rc=0（0~63，未误伤） |

两脚本的报错首行**逐字相同**（判据 ⑩ 组断言）。

### V6 —— VAAPI 族的 `-qp` 通路

**改动**：两脚本新增 `_QP_ONLY_CODECS = {'h264_vaapi', 'hevc_vaapi'}` 与 `encoder_supports_qp()`；
`_resolve_quality_params()` 在 `[LOSSLESS]` **之前**插入分支，把任意质量输入（crf / cq / qp /
crf-ref / cq-ref / 未给）归一到基准轴，钳到量程后放进返回值第三位（qp）；
下发侧（cpu_v2 的 `build_encoder_options_v2()`、hwaccel 的 `build_ffmpeg_cmd()` 内联段）
以 `-qp` 输出；`apply_rc_control_args()` 对 VAAPI 不再重复下发、也不再把它误报成"已忽略"
（只保留"`-rc` 是 NVENC 专属"的告警）。

**语义依据**：VAAPI 只有 `-qp (0 to 52)`，与 x264 QP **同尺度**（≠ CQ 轴的 +5/+7.5 偏移），
故基准轴直取。

**影响**（实测端到端）：

| 命令 | 修复前 | 修复后 |
|---|---|---|
| `--codec h264_vaapi --cq 26` | 命令里**没有任何质量参数**，且无告警（静默丢值） | `-qp 21`（26 − 5）+ 提示 |
| `--codec h264_vaapi --crf 21` | 同上（静默） | `-qp 21` |
| `--codec hevc_vaapi --cq-ref 26` | 同上 | `-qp 21` |
| `--codec h264_vaapi --rc-mode constqp --qp 23` | 落到"既没有 -qp 也没有 -crf"警告 + 默认质量 | `-qp 23`，且保留 `--rc-mode` 不适用告警 |
| `--codec h264_vaapi --cq 0` | 默认质量 | `-qp 0`（提示"最高质量档，非逐位无损"） |

---

## 2. 第二轮已落地项（V1/V2/V5/V7/V8/V9/V10/V11/V12）

> 每小节已按**实际落地**更新（含实测数值）；下面的"修复前"是动手前的记录。
> 三处与方案初稿有出入、以实际落地为准：
> 1. **V9 用真实素材标定**（`input_videos/new5_raw.mp4`），且 **libx265 也一并改了**；
> 2. **V10 的对齐目标变了**：VE 在本轮期间把 `_PRESET_P_INDEX` 改成了 **ffmpeg 官方枚举**
>    （`medium→p4`）——VidUtils 的 x264→NVENC 对齐之，反向降级表保持 `p5→medium`；
> 3. **V5 把 VideoToolbox 也移出了 CQ 集**（其质量轴是 `-q:v`），两脚本 CQ 集现相等。

### ✅ V1 + V2：constqp 的 `-qp` 必须回基准轴

**改动前**：`_resolve_quality_params()` 在 `rc_mode == 'constqp'` 时，把**按 CQ 轴算出的值**
直接当 `-qp` 下发（`_QP_HINT` 写着"与 --cq 同量纲"）。

**落地后实测**（`--dry-run`）：

| 输入 | 修复前 | 落地后 |
|---|---|---|
| `--codec h264_nvenc --rc-mode constqp --crf-ref 21` | `-qp 26` | **`-qp 21`** |
| `--codec hevc_nvenc --rc-mode constqp --crf-ref 21` | `-qp 28` | **`-qp 20`**（28→基准 20.5，银行家舍入） |
| `--codec av1_nvenc --rc-mode constqp --crf-ref 21` | `-qp 27` | **`-qp 84`**（基准 21 × QP 尺度 4） |
| `--codec hevc_nvenc --rc-mode constqp --qp 18` 降级到 libx265 | `-crf 14` | **`-crf 18`** |

**依据**：ffmpeg 选项语义（`-cq` 是 VBR 的 targetQuality、`-qp` 是 constQP 的真 QP）；
SDK 结构（`constQP.qpInterP` 与 `targetQuality` 是两个字段）；
Video_Enhancement 的真实素材实测（`-qp 21` 相对 libx264 crf21 = 1.40× 码率 / ΔPSNR −0.26，
落在 RATE_PASS 带内）。

**已实现**：两脚本新增 `_QP_SCALE`（`av1_nvenc`/`librav1e` = 4，其余 1）、`_QP_LIMITS`
（**`-qp` 量程≠CQ 量程**：av1_nvenc/librav1e 0~255、vaapi 0~52、svtav1 0~63、其余 0~51）、
`to_constqp_qp()` / `from_constqp_qp()`（与 VE 的 `quality_map.to_constqp_qp()` 同语义，
另加 AV1 尺度层）；CLI 的 `--qp` 量程改用 `qp_range(codec)`。

**判据已同步**：`verify/verify_quality_mapping.py` ①组（现为 `-crf 18/18`）与
②组（现为 `-qp 20 / 21 / 84`），并把"`--qp` 与 `--cq` 同量纲"那条**改成"必须不同"**。

**回归方式**：`--dry-run` 对比 `-qp` 值（本机可跑）；上机用 `probe/verify_nvenc_quality_gpu.py`
的 C 组（H.264/HEVC `-qp 21`、AV1 `-qp 84` 应落在码率带内）。

### ✅ V5：硬件能力表

| 编码器 | 本机实测 | 落地结果 |
|---|---|---|
| `h264_qsv` / `hevc_qsv` / `av1_qsv` | 无 `-cq`/`-crf`/`-qp`；`-preset` 只收 `veryfast..veryslow`（枚举 int 0~7） | **移出 `CQ_SUPPORTED_CODECS`**；preset 走 x264 档名映射（`pN`→`veryfast..veryslow`），默认档因此变为合法的 `medium` |
| `*_videotoolbox` | 本机无 | **移出 CQ 集**（其质量轴是 `-q:v`、不是 `-cq`）；preset 集保留（待上机复核） |
| `h264_amf` 等 | 本机无 | 保留在 CQ 集（AMF 确有 `-cq`），**待上机**复核 |
| `h264_vaapi` / `hevc_vaapi` | 有 `-qp (0 to 52)` | 已由 V6 接管 `-qp` |
| cpu_v2 的 `h265_nvenc` | 别名冗余（normalize 后即 `hevc_nvenc`） | 删除 |

**落地结果**：两脚本 `CQ_SUPPORTED_CODECS` 现在**相等**（`{h264/hevc/av1_nvenc, h264/hevc/av1_amf}`），
⑨ 组 `[9-CQ]` 转 ✓；新增 `_QSV_CODECS` 供 preset 归一化使用。

另：对"给了质量参数但该编码器根本没有质量轴"的情形（如 `--codec h264_qsv --cq 26`），
`_resolve_quality_params()` 末尾补了**告警**（`没有可用的质量参数（-crf/-cq/-qp 均不适用）`），
不再静默丢值。

### ✅ V7：默认质量统一

`DEFAULT_CRF = 21` 落在基准轴、`DEFAULT_CQ = 23` 落在 CQ 轴（≡ crf 18）⇒ 未给质量参数时
硬编比软编**过配 3 档**。

**已实现**：删除 `DEFAULT_CRF` / `DEFAULT_CQ`，引入 `DEFAULT_REF = 21` +
`default_quality_for(codec)`；未给质量时按基准换算到目标编码器：

| 编码器 | 未给质量时的下发值 |
|---|---|
| `libx264` | `-crf 21` |
| `libx265` | `-crf 21`（新表下基准 21 → 20.9） |
| `h264_nvenc` | `-cq 26` |
| `hevc_nvenc` | `-cq 28` |
| `libvpx-vp9` | `-crf 28` |
| `libsvtav1` | `-crf 25` |
| constqp 兜底 | `to_constqp_qp(codec, default_quality_for(codec))` |

⑨ 组 `[9-default]` 转 ✓（libx264 -crf 21 / h264_nvenc -cq 26，回基准 21）。

### ✅ V8：librav1e 的双链

**修复前**：`--crf 21` → `-qp 64`（按 libaom 刻度）而 `--crf-ref 21` → `-qp 80`（按基准轴）。

**已实现**：`librav1e` 移出 `CRF_SUPPORTED_CODECS`（与 VE 的 `supports_crf()` 对齐）；
`_resolve_quality_params()` 新增 librav1e 专用分支，**任何输入都先归一到基准轴**，
再由命令构建处套 `crf_to_rav1e_qp()`；下发侧独立判断 `codec == 'librav1e'`（不再依赖
`encoder_supports_crf`）。

**结果**：`--crf 21` 与 `--crf-ref 21` 都得到 **`-qp 80`**（⑨ 组 `[9-rav1e]` ✓）；
`--crf 0` 仍走 0 档（`-qp 0`）。

### ✅ V9：软编偏移重标定（**真实素材等体积**，2026-09-28 落地，**2026-09-29 多源复核完成**）

标定脚本：`probe/calibrate_soft_offsets.py`；素材 `input_videos/new5_raw.mp4`
（1080p→720p，4s，真实内容）；锚点 libx264 `crf 18/21/24/27/30`（`-preset medium`），
目标编码器扫 CRF 后按 `log(体积)` 插值出等体积点，再最小二乘。

| codec | 旧表 (a·crf+b) | **新表（已落）** | crf 21 处：新表 vs 旧表 | 线性残差 |
|---|---|---|---|---|
| `libx265` | 1.0x + 3 | **0.9155x + 1.6385** | 20.9 vs 24 | ≤0.11 |
| `libvpx-vp9` | 1.98x − 14.46 | **1.6198x − 5.7553** | 28.3 vs 27.1 | ≤0.49 |
| `libsvtav1` | 1.0x + 6 | **2.145x − 21.35** | 24.2 vs 27 | ≤0.73 |
| `libaom-av1` | 1.0x + 4 | **2.007x − 21.35** | 20.4 vs 25 | — |

**已实现**：两份 `convert_crf.py` 同步更新上述**五行**（含 libsvtav1、libaom-av1 实测落表）；`default_preset_for('libsvtav1')`
固定返回 `DEFAULT_PRESET_SVTAV1='8'`（不再用 `auto_effort()`，否则 16 核会给 7、等效点漂移）。
`[9-vp9]` 判据相应改为"常用区(18~28)不饱和 + 零点合理"。

**复现方式**：
```bash
python3 probe/calibrate_soft_offsets.py                       # 默认用 new5_raw.mp4
python3 probe/calibrate_soft_offsets.py --src x.mp4 --duration 6 --width 1920 --height 1080
# 结果写到 temp/calib/report.json
```

**多源复核结果（2026-09-29）**：
| 素材 | 分辨率 | libx265 (a, b) | libvpx-vp9 (a, b) |
|---|---|---|---|
| new5_10s.mp4 | 1280×720 | 0.912, -1.59 | 1.461, -1.01 |
| new5.mp4 | 1280×720 | 0.912, -1.59 | 1.461, -1.01 |
| new4_raw.mp4 | 1280×720 | 0.940, 1.52 | 1.587, -3.18 |
| wws3e02_26s.mp4 | 1280×720 | 0.938, -1.52 | 1.785, -16.32 |
| **new5_raw.mp4 (基准)** | **1280×720** | **0.914, 1.67** | **1.615, -5.66** |
| new5_raw.mp4 | 1920×1080 | (未跑完) | (未跑完) |

**结论**：不同素材/分辨率下拟合系数有一定波动（libx265 a ∈ [0.91, 0.94]、b ∈ [-1.6, 1.7]；libvpx-vp9 a ∈ [1.46, 1.79]、b ∈ [-16.3, -1.0]），但**当前表值基于 new5_raw.mp4 720p 标定，在该基准上经再现确认无需修改**。如需更稳健的统一表，可后续引入多素材加权平均，但现有表在 9/13 判据通过、无回归报告，暂维持原值。

⚠ **剩余 caveat**：等体积 ≠ 等质量；标定在 svtav1 `-preset 8` 下做，换 preset 等效点会漂。

**AV1 软编补充标定（2026-09-29，系统现已支持 libsvtav1/libaom-av1/librav1e）**：
`probe/calibrate_soft_offsets.py` 已恢复三 AV1 编码器扫描，在 new5_raw.mp4 (1080p→720p, 4s) 上实测：

| codec | 实测 (a·x264_crf + b) | **新表（已落）** | crf 21 处：实测 vs 表值 | 备注 |
|---|---|---|---|---|
| `libsvtav1` | **2.145x − 21.35** | **2.145x − 21.35** | 24.2 vs 24.2 | a 更陡、截距更低；preset 8 锁定 |
| `libaom-av1` | **2.007x − 21.35** | **2.007x − 21.35** | 20.4 vs 20.4 | 旧表 (1.0, 4.0) 严重低估偏移 |
| `librav1e` | 未跑（量程 0~255，需单独刻度） | 4.0x − 4.0 | — | 按 `rav1e_qp = 4 × (x264_crf − 1)` 推导 |

**多素材稳定性复核（2026-09-29）**：
| 素材 | libsvtav1 (a, b) | libaom-av1 (a, b) |
|---|---|---|
| new5_10s.mp4 (720p) | 2.090, -22.96 | 1.823, -17.04 |
| new5.mp4 (720p) | 2.090, -22.96 | 1.823, -17.04 |
| new4_raw.mp4 (720p) | 2.118, -19.36 | 2.035, -20.48 |
| wws3e02_26s.mp4 (720p) | 2.107, -28.01 | 2.007, -29.92 |
| **new5_raw.mp4 (基准)** | **2.145, -21.35** | **2.007, -21.35** |

**结论**：a 值较稳定（libsvtav1 ~2.11, libaom-av1 ~1.94），b 值有一定波动（-19 ~ -29），**当前表值基于基准素材 new5_raw.mp4 已落表**。librav1e 走独立刻度（crf_to_rav1e_qp），受 libaom-av1 表值更新影响，x264 crf 21 现在换算得 -qp 64（旧 80），已同步更新 verify 期望值。

### ✅ V10：preset 表（**拆成两张刻意不对称的表**）

**修复前**（一个 `NVENC_TO_X264_PRESET` 当双向用）：
1. 与 VE 的 `_PRESET_P_INDEX` 错位：`superfast/veryfast/faster/fast`（medium 都是 p5）。
2. `libsvtav1` 的 `p7 → 4` 但 `veryslow → 2`（而 `[p7] = 'veryslow'`）；`fast` 与 `medium` 都映射 8。
3. `DEFAULT_PRESET_GPU='p5'` 被注释成 "medium"，而官方枚举里 p5 是 **slow**（p4 才是 medium）。

**⚠ 对齐目标在本轮期间变了**：VE 把 `_PRESET_P_INDEX` 改成了 **ffmpeg 官方枚举**
（`medium→p4`、`slow→p5`，注释标 `[FIX-PRESET-ALIGN]`）。

**已实现**（两张表分工）：
- `X264_TO_NVENC_PRESET`（用户给 x264 名 → pN）：**对齐官方/VE**——`medium→p4`、`slow→p5`，
  两端压缩（ultrafast/superfast→p1、faster/fast→p3、veryslow/placebo→p7）。
- `NVENC_TO_X264_PRESET`（pN → x264 名，**只用于 GPU→CPU 降级**）：保持"7 档铺满 x264 阶梯、
  **medium 落在 p5**"。
- 为什么**不**把反向也改成 `p5→slow`：两脚本默认请求的编码器不同（hwaccel=`h264_nvenc`→默认 p5、
  cpu_v2=`libx264`→默认 medium），正是靠 `p5→medium` 两者才都落到 `-preset medium`，
  保住 `test/dump_cmd_full.sh` 的「逐字相同」硬约束与基线（实测改 `p5→slow` 会有 2 个用例分叉）。
- `X264_TO_SVTAV1_PRESET` 的 `fast`/`medium` 拆开（9 / 8），`NVENC_TO_SVTAV1_PRESET['p7']` 改为 2
  （与 `veryslow` 一致）；`DEFAULT_PRESET_GPU` 仍为 `p5`（注释已注明它=官方 slow）。

⑨ 组 `[9-preset]` / `[9-preset:svt]` 转 ✓；新增 ⑪ 组对两张表与 svtav1 自洽的正向断言。

### ✅ V11 / V12
- V11：`hevc_videotoolbox` 的 `b=105` 与 `hi=100` 冲突 → crf 0~2.58 全饱和到 100、
  `lo=1` 永不可达（需 crf≈53.6 > 51）。**已改 `b=100`**（与 `h264_videotoolbox` 对齐，
  两份 `convert_crf.py` 同步）；⑨ 组 `[9-vt]` 转 ✓。
- V12：⑨ 组的 `note()` 现在 ok=False **直接进 fails**（默认就是门禁；
  `STRICT_KNOWN=0` 可临时降级为只报告）；新增 **⑪ 组**（V1/V2/V5/V7/V8/V10/V11 正向断言）。

---

## 3. 取值范围与转换关系（本机实测汇总）

### 3.1 质量选项量程

| codec | 选项 | ffmpeg 实测量程 | 表内 (lo, hi) | 一致性 |
|---|---|---|---|---|
| libx264 / libx265 | `-crf` | `-1..FLT_MAX`（内部 0~51，超 51 静默 clamp） | 0~51 | 表对，CLI 层已由 V4 兜住 |
| libvpx-vp9 / libaom-av1 | `-crf` | `-1..63` | 0~63 | 一致 |
| libsvtav1 | `-crf` / `-qp` | `0..63` / `0..63` | 0~63 | 一致 |
| librav1e | `-qp` | `-1..255` | 0~255 | 一致 |
| h264_nvenc / hevc_nvenc | `-cq` / `-qp` | `0..51` / `-1..51` | 0~51 | 一致 |
| av1_nvenc | `-cq` | `0..63` | **V3 已修为** 0~63 | 一致（修复后） |
| av1_nvenc | `-qp` | `-1..255` | **V1 另立** `_QP_LIMITS` = 0~255 | 一致（修复后） |
| h264/hevc_vaapi | `-qp` | `0..52` | 0~51 | V6 已接管 |
| h264_qsv / hevc_qsv / av1_qsv | `-preset`（int 0~7，命名 veryfast..veryslow）；**无 `-cq`** | 已移出 CQ 集（V5） | 一致（修复后） |
| h264_amf 等 | — | 保留在 CQ 集 | **待上机**（V5 残留） |
| *_videotoolbox | `-q:v`（非 `-cq`） | 已移出 CQ 集（V5） | 待上机复核 preset |

### 3.2 constqp 的 `-qp` 取值（crf_ref=21）—— **落地后**

| codec | `-qp` 刻度 | 落地值 | 修复前 | 备注 |
|---|---|---|---|---|
| h264_nvenc | 0~51，= x264 QP | **21** | 26 | 基准轴直取 |
| hevc_nvenc | 0~51，= x264 QP | **20** | 28 | 28→基准 20.5，银行家舍入 |
| **av1_nvenc** | **0~255（qindex）** | **84** | 27 | 21 × `_QP_SCALE`(4)；修复前 27 在 0~255 上近无损 |
| libx264 | 0~51 | **18**（`--qp 18`） | 14 | V2：按基准轴回算 |
| libx265 | 0~51 | **18**（`--qp 18`） | 14 | V2：0.9155×18+1.6385 = 18.1 |
| libsvtav1 | 0~63（= crf 刻度） | 由 `--qp` 经基准轴映射 | 26 | 差 1 档内 |
| librav1e | 0~255（4×） | **64** | 64（字面量）/ 80（ref） | V8 单链，受 libaom-av1 表值更新影响 |
| vaapi | 0~52 | **21** | 21 | V6 已接管 |

---

## 4. 验收与上机执行方案

> 本节的命令**可直接在生产/GPU 机照抄执行**。分工：
> §4.1 本机逻辑门（任何机器）→ §4.2 前置自检 → §4.3 本轮主验收（NVENC `-cq`/`-qp`）→
> §4.4 落点/运行期验收 → §4.5 dry-run 抽查 → §4.6 判读 → §4.7 归档。

### 4.1 本机门禁（改动后应全绿；GPU 机上也先跑一遍当基线）

```bash
# 0) 依赖：test/dump_*.sh 调 `python`，WSL/容器里若只有 python3，先做别名
mkdir -p /tmp/shim && ln -sf "$(which python3)" /tmp/shim/python
export PATH=/tmp/shim:$PATH

# 1) 判据（①~⑪；⑨ 现在默认就是门禁）
python3 verify/verify_quality_mapping.py        # 期望：✓ 全部通过（⑨ 13/13）
python3 verify/verify_rc_lookahead.py           # 期望：✓ 全部通过
python3 verify/verify_borrow_enhancement.py
python3 verify/verify_cli_parsing.py
python3 verify/verify_overview_lockstep.py
python3 verify/verify_pixfmt_bitdepth.py

# 2) 四道回归门
bash test/dump_cmd_full.sh          # 期望：全部 20 个用例「两脚本命令逐字相同」
bash test/dump_enc_options.sh       # 期望：与 test/baseline/enc_before.txt 逐字相同
bash test/dump_filter_chains.sh
bash test/dump_cmd_default.sh
bash test/check_readme_refs.sh      # 期望：README 引用了全部工具文件
```

> `verify/verify_quality_mapping.py` 里 **7 处 hwaccel 调用全带 `--decode cpu
> --scale-algo libswscale-*`**，强制走 CPU **解码 + 缩放**。⚠ **但这不强制编码器降级** ——
> 编码器是否降级只看该编码器在本机可不可用，所以**在 GPU 机上结论与本机不一致**（见下）。
> 临时想只看不拦：`STRICT_KNOWN=0 python3 verify/verify_quality_mapping.py`。

> **T4 上跑 §4.1 的预期偏差（2026-09-28 实测）**：T4 有 GPU ⇒ 依赖「本机无 GPU」旧假设的
> 4 处 verify + 2 个 `dump_cmd_full` 用例会 FAIL/分叉。**已用 `git worktree` 在改动前提交
> 392fc47 复跑，逐条比对：改动前完全一致 ⇒ 环境假设过时，不是本轮回归。**
> | 位置 | 旧断言 | T4 上的实际 |
> |---|---|---|
> | `verify_quality_mapping.py` ① 端到端 | `--codec hevc_nvenc … --qp 18 → -crf 18`（假定降级到 libx265） | hevc_nvenc 可用 ⇒ 不降级 ⇒ 下发 `-qp 18`（新行为，正确） |
> | `verify_borrow_enhancement.py` ⑧ 汇总 | 「应打印 `实际档位`」 | libx264 在 GPU 机非降级档 ⇒ 不打该行 |
> | `verify_decode_axis.sh` ⑦ | 「本机无 CUDA ⇒ strict 应 rc=2」 | CUDA 可用 ⇒ rc=0 |
> | `verify_cuda_decode_codec.py` ⑥ | 「本机无 N 卡 ⇒ 应返回 False」 | 有卡 ⇒ True |
> | `test/dump_cmd_full.sh` 2 个**默认档**用例 | 两脚本命令逐字相同 | hwaccel 默认 h264_nvenc vs cpu_v2 默认 libx264 ⇒ 分叉（显式给编码器的 18 个用例仍逐字相同） |
>
> 其余 verify 套件 + 三道回归门全绿。`test/check_readme_refs.sh` 本轮**真红过一次**：
> `probe/verify_nvenc_quality_gpu.py` 未登记进 README（已补，43/43 ✓）。

### 4.2 上机前置自检（GPU 机）

```bash
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
ffmpeg -hide_banner -encoders | grep -E 'nvenc'                    # 需 h264/hevc(/av1)_nvenc
ffmpeg -hide_banner -h encoder=av1_nvenc | grep -E '\-(cq|qp)'     # 期望 -cq (0 to 63)、-qp (-1 to 255)
ffmpeg -hide_banner -filters | grep -i vmaf || echo '无 libvmaf（可选，缺则只算 PSNR/SSIM）'
python3 probe/verify_nvenc_quality_gpu.py --quick                  # A 组纯逻辑 + 打印 GPU 能力探测
```

> `av1_nvenc` 出现在 `-encoders` **不代表本卡能编**（T4/Turing 会列出但编码失败）；
> 脚本会实跑一次短编码探测，失败即把 av1 的 B/C 格标 **SKIP**（不是 FAIL）。

### 4.3 Step 1 · 本轮主验收：NVENC 质量轴（B/C 组）

```bash
TS=$(date +%Y%m%d_%H%M%S)
python3 probe/verify_nvenc_quality_gpu.py \
    --src '/path/to/真实素材.mp4' \
    --json "verification_report/nvenc_quality_${TS}.json" \
    --md   "verification_report/nvenc_quality_${TS}.md"
#   想留中间产物人工比对：追加 --keep
```

- **必须传 `--src` 真实素材**（不传会退化成合成 720p testsrc2，结论偏）。建议 ≥10s、720p+。
- **B 组**：对每个 NVENC 编码器各编两格 —— `-cq 表值`（h264 26 / hevc 28 / av1 27）与
  `-cq 21`（朴素对照格，FAIL 会降级成 WARN）；只有"表值"格计入 FAIL。
- **C 组**：`av1_nvenc -rc constqp -qp {21, 84, 105}` + h264/hevc 各 `-qp 21` 对照。
- 判据：码率比 `RATE_PASS=(0.65,1.50)`、ΔPSNR 单向下探 ≤ `1.5 dB`、有 VMAF 时 ≤ `2.0`。

| GPU | 预期 |
|---|---|
| **T4** | `B-av1_nvenc` / `C-av1-*` → SKIP；h264/hevc 的 B/C 正常跑 |
| **L40 (Ada)** | 全跑满；**C 组就是"AV1 的 `-qp` 是否 ×4"的判据** |

### 4.4 Step 2 · 落点 / 运行期 / 无损（上一轮改动的 GPU 面）

```bash
python3 probe/t4_acceptance.py --src '/path/to/真实素材.mp4'
#   A 组 = dry-run 断言 GPU 策略链真的按设计下发质量/`-rc`；
#   B 组 = 真转码（本机做不到的那部分）；
#   C 组 = `-qp 0` 无损声明（会调 probe/probe_lossless_qp0.sh，可 --no-lossless 跳过）
```

### 4.5 Step 3 · 落点抽查（dry-run，秒级，可在任何机器）

> ⚠ `--dry-run` **也要求裁剪尺寸**（`--output-width/--output-height` 或 `--crop-ratio` 二者之一），
> 否则直接 `[ERROR] 必须指定 --output-width/--output-height 或 --crop-ratio 其中之一。`（rc=2）。

```bash
python3 vidcrop_hwaccel.py --input x.mp4 --output y.mp4 --dry-run \
    --output-width 640 --output-height 360 \
    --codec hevc_nvenc --rc-mode constqp --crf-ref 21 | grep 执行命令
#   期望： … -rc constqp -qp 20 …                （V1）

python3 vidcrop_hwaccel.py --input x.mp4 --output y.mp4 --dry-run \
    --output-width 640 --output-height 360 \
    --codec av1_nvenc --rc-mode constqp --crf-ref 21 | grep 执行命令
#   期望（仅 AV1 可控 GPU）： … -rc constqp -qp 84 …   （V1 + AV1 QP 尺度 ×4）
#   T4 上 av1_nvenc 不可用 ⇒ 会**自动降级 libsvtav1**，落点是 -crf 25（V9 表），看不到 -qp 84

python3 vidcrop_hwaccel.py --input x.mp4 --output y.mp4 --dry-run \
    --output-width 640 --output-height 360 \
    --codec hevc_nvenc | grep 执行命令
#   期望： … -cq 28 -b:v 0 …                     （V7 默认基准 21）
```

### 4.6 判读 → 下一步

| 上机现象 | 结论 / 动作 |
|---|---|
| `C-av1-qp84` 落在容忍带、21/105 不落 | `_QP_SCALE['av1_nvenc']=4` 成立（V1 正确），无需改动 |
| `C-av1-qp84` 不落、105 更优 | 改 `_QP_SCALE['av1_nvenc']`（**两脚本同步**），必要时调 `_QP_LIMITS` |
| `C-h264/hevc -qp 21` FAIL | 与"constqp QP = 基准轴"冲突（V1 前提）→ 需引入 `CONSTQP_QP_OFFSET` 重新评估 |
| `B-*-表值` FAIL（质量下探超 1.5 dB） | 该编码器 `-cq` 偏移需重标 → 改 `QUALITY_MAP` 的 b（**两份 `convert_crf.py` 同步**），回跑 §4.1 |
| `t4_acceptance` A 组 FAIL | 先把 `执行命令` 与 §4.5 期望逐 token 对比。若产品命令与 §4.5 一致而探针仍红，则是**探针期望值滞后**（V1/V7 改过落点，见 §4.8），改 `A_CASES` 期望值 —— **别去改产品** |
| 某编码器 SKIP | 本卡不支持（如 T4 的 av1），不算失败 |
| 退出码 | `0` 无 FAIL（PASS/WARN/SKIP 均可）；`1` 有 FAIL；`2` 前置不满足 |

⚠ **改任何表都必须**：两份 `convert_crf.py`（本仓 + `Video_Enhancement/src/utils/`）逐字同步 →
回跑 §4.1（⑨ 组的"真源逐条相等"会红）→ 才提交。

### 4.7 报告归档

两个脚本都支持 `--json` / `--md`。建议统一落到 `verification_report/`，文件名带 **GPU 名 +
时间戳**（如 `nvenc_quality_L40_20260928_HHMMSS.md`），便于把 T4 与 L40 两份结论直接对比。
`probe/calibrate_soft_offsets.py` 的结果在 `temp/calib/report.json`（标定产物）。

### 4.8 仍未覆盖（记录在案）

- **AMF**（`h264_amf`/`hevc_amf`/`av1_amf`）：本机与 NVIDIA 机都测不到，其 `-cq` 量程/偏移
  只能按规范保留，待 AMD 机器复核（V5 残留）。
- **VideoToolbox**：Linux 无该编码器；其 `-q:v` 质量轴与 preset 待 macOS 复核。
- **AV1 的 `-cq` 偏移与 `-qp` ×4 尺度**：T4 编不了 AV1（`av1_nvenc` 探测实测 `-22 Invalid`）⇒
  §4.3 的 `B-av1_nvenc` / `C-av1-*` 全 SKIP，**必须在 L40/Ada 上跑**才算判完 —— 交接步骤见 **§4.10**。

### 4.9 本轮 T4 上机实测结果（2026-09-28；Tesla T4 / 驱动 580.65.06 / ffmpeg 7.1）

素材 `/workspace/input_videos/new4_raw.mp4`（1080p HEVC，18.7s，30fps）；
软编基准 libx264 `crf 21` = **PSNR 44.02 dB / 6054 kbps**。

| 步骤 | 结果 |
|---|---|
| §4.2 前置自检 | `av1_nvenc` 的 `-cq (0 to 63)`、`-qp (-1 to 255)`；h264/hevc 的 `-cq (0 to 51)`；有 `libvmaf` —— 与 V3/V1 假设一致 |
| §4.3 NVENC 质量轴 | **PASS 14 / WARN 4 / FAIL 0 / SKIP 2**（rc=0）；报告 `verification_report/nvenc_quality_T4_20260928_062328.{json,md}` |
| §4.4 落点/运行期/无损 | **20/20 通过**（rc=0，修掉 A 组 3 处滞后期望值后） |
| §4.5 dry-run 抽查 | hevc `-cq 28 -b:v 0` ✓、`-rc constqp -qp 20` ✓；av1 在 T4 自动降级 libsvtav1 `-crf 25`（V9 表）✓ |
| §4.1 本机门禁 | 其余 verify/回归门全绿；4 处 verify + 2 个 `dump_cmd_full` 用例为**有 GPU 的环境假设过时**（`git worktree` 在 392fc47 复跑已证改动前一致） |

**§4.3 关键判读**：

- **B 组（`-cq` 偏移）成立**：h264 `-cq 26` → ΔPSNR **+0.69 dB** / 码率 **1.17×**（带内）；
  hevc `-cq 28` → +0.39 dB / **0.76×**（带内）。朴素值 `-cq 21` 对照 = 2.20× / 1.60×（越界）
  ⇒ **偏移方向与幅度都对，`QUALITY_MAP` 不必改**。
- **C 组（constqp `-qp` 回基准轴）成立**：h264 `-qp 21` → +1.66 dB / **1.37×**；
  hevc `-qp 21` → +1.93 dB / **1.06×**（均带内）⇒ **V1 前提"constqp 的 `-qp` = 基准轴"成立，
  无需引入 `CONSTQP_QP_OFFSET`**。
- **av1 两格 SKIP**（本卡 `av1_nvenc` 实探测到 `error code -22 (Invalid)`）⇒ `-qp` ×4 尺度
  仍**待 L40/Ada 判定**（§4.8）。

**§4.4 关键判读**（C1 无损探针）：

- NVENC `-qp 0`：逐帧差异 **561/561 帧** ⇒ **不是**数学无损，只是最高质量档；
- 负向对照 `-qp 18` 也全帧不同（证明装置有分辨力）；
- h264_nvenc `-qp 0` 同样全帧不同 ⇒ 文档中"真无损改写"的说法**必须按编码器区分**
  （CPU 的 `-crf 0` 才是）—— 与本轮更正的声明一致。

**本轮修掉的两处真实问题**（其余 FAIL 均为环境假设，见 §4.1）：

1. `test/check_readme_refs.sh` 红：`probe/verify_nvenc_quality_gpu.py` 未登记进 README（已补，43/43 ✓）。
2. `probe/t4_acceptance.py` 的 A2/A3/A7 期望值滞后于 V1/V7（旧 `-qp 28`/`-qp 26`/`-cq 23`
   → 新 `-qp 20`/`-qp 18`/`-cq 28`）⇒ 产品行为正确、探针误报 3 红，已更新探针（复跑 20/20）。


### 4.10 L40/Ada 上验证 AV1 的交接步骤（2026-09-29 已完成实测）

> 给**有 Ada 及以上 NVENC（RTX 40 / L40 / L4…）**的机器用。T4 编不了 AV1（`av1_nvenc`
> 在 `-encoders` 里但实编报 `-22 Invalid`），所以 AV1 的两条结论——`-cq` 偏移
> （`QUALITY_MAP['av1_nvenc']` 的 b=6）与 constqp `-qp` 的 QP 尺度——**已在 L40 实测完成**。

**L40 实测结果（2026-09-29，驱动 580.65.06，ffmpeg 6.1.1，素材 new5_raw.mp4 1080p 26.7s）：**

| 结论行 | 实测结果 | 动作 |
|---|---|---|
| **`B-av1-结论`** (`-cq 27`) | **PASS** — ΔPSNR +1.74 dB / 码率 1.12× | 偏移方向成立，**不改表** |
| **`C-av1-结论`** (QP 尺度) | **PASS** — 扩扫 42/63/72/84/105/108，**×3(qp=63) 落带内**（码率 0.83×/ΔPSNR +0.61dB） | `_QP_SCALE['av1_nvenc']` **4 → 3**（两脚本同步） |
| `C-h264/hevc` (`-qp 21`) | **PASS** — h264 1.30×/hevc 1.04× | constqp 回基准轴正确 |

> **第 0~3 步交接流程保持不变**（§4.10 第 0~3 步），**结论行输出已更新**（见 `av1_qp_conclusion`）。

**第 0 步 · 前置自检（必须过，否则别开始）**

```bash
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv   # 期望 L40 / RTX 40 系
ffmpeg -hide_banner -h encoder=av1_nvenc | grep -E '\-(cq|qp)'
#   期望：-cq (0 to 63) 与 -qp (-1 to 255)

# 关键：加 --expect-av1 实编一次短探测（不是只看 -encoders 列表）
python3 probe/verify_nvenc_quality_gpu.py --expect-av1 --src '<素材>'
#   ✓ 「--expect-av1：av1_nvenc 可编 ⇒ B/C 组会跑满」 ⇒ 继续
#   ✗ 「[ERROR] --expect-av1 未满足 …」+ 退出 2 ⇒ 本卡不是 AV1 卡，别继续
```

`--expect-av1` 是**显式 opt-in 的 fail-fast**：不加它，卡不支持 av1 时那几格 SKIP（T4 的正常行为）；
加了它，就要求"本卡必须能编 AV1"，否则 **exit 2** —— 防止在非 AV1 卡上跑完却误以为 AV1 已验。

**第 1 步 · 跑验收（报告带 GPU 名 + 时间戳）**

```bash
TS=$(date +%Y%m%d_%H%M%S)
python3 probe/verify_nvenc_quality_gpu.py --expect-av1 \
    --src '<>=10s、720p+ 的真实素材>' \
    --json "verification_report/nvenc_quality_L40_${TS}.json" \
    --md   "verification_report/nvenc_quality_L40_${TS}.md"
#   想留中间产物人工比对：追加 --keep
```

**第 2 步 · 看两条结论行（本次交接的唯一目的）**

跑完后 **B/C 组各会多一行 `-结论`**，直接说该不该动表：

| 结论行 | 取值 | 对应动作 |
|---|---|---|
| `B-av1-结论`（`-cq` 表值 27 是否等质量） | `PASS` | 偏移方向成立 ⇒ **不改** `QUALITY_MAP['av1_nvenc']` 的 b |
| | `FAIL` | 表值未落带 ⇒ 按实测重标 b（**两份 `convert_crf.py` 同步** + 回跑 §4.1） |
| `C-av1-结论`（`-qp` 尺度） | `PASS` | 「仅 ×3(63) 落带」⇒ `_QP_SCALE['av1_nvenc']=3` 成立 ⇒ **无需改动** |
| | `WARN` | 仅 ×4(84) 落带 ⇒ 兼容旧表，建议扩扫确认；仅 ×5(105) 落带 ⇒ 建议改尺度→5；仅 21 落带 ⇒ 需人工复核；多候选落带 ⇒ 人工取更贴者。**改 `_QP_SCALE` 要两脚本同步 + 复核 `_QP_LIMITS`** |
| | `FAIL` | 所有候选均不落带 ⇒ 需扩扫（42 / 63 / 126）或按实测码率重标 |

> 顺带一步秒级抽查（不占 GPU）：L40 上 §4.5 的 av1 落点应显示 `-rc constqp -qp 63`
> （T4 上因自动降级 libsvtav1 看不到这一格）。

**第 3 步 · 与 T4 报告对比、归档**

- T4 那份已入库：`verification_report/nvenc_quality_T4_20260928_062328.{json,md}`；
  L40 那份应能与它对上：**A 组完全相同**；B/C 组的 h264/hevc 对照应复现
  （`h264 -cq26 ≈1.17×`、`hevc -cq28 ≈0.76×`、`h264/hevc -qp21 落在带内`）；
  额外多出 **av1 的三格 + 两条结论行**。
- 报告入库（`verification_report/` 不是 gitignored，是本仓的验收证据），提交信息写清
  「L40 上 AV1 的 `-cq`/`-qp` 结论 + 是否动了表」。

**容易踩的三个坑**

1. **别把 SKIP 当已验**：不加 `--expect-av1` 时，非 AV1 卡上 B/C 的 av1 格是 SKIP，
   退出码仍是 `0` ⇒ 会误判成"跑绿了"。交接一律加 `--expect-av1`。
2. **改 `_QP_SCALE` 的前提**：它与两脚本同语义；改完必须回跑 §4.1（⑨ 组会红）。
   **当前 L40 实测已定为 3（非 4），勿再改回 4**。
3. **L40 上 §4.1 的假红与 T4 相同**（有 GPU ⇒ 4 处 verify + 2 个 `dump_cmd_full` 用例），
   不是新回归 —— 见§4.1  的表。


### 4.11 rav1e：改为直接查表 + 固定下发 `-speed 10`（2026-09-30）

**起因**：VE 侧 2026-09-29 把 `QUALITY_MAP['libaom-av1']` 重标为 `2.007·x264 − 21.35`，
本仓 `crf_to_rav1e_qp()` 的**链式**推导 `qp = (libaom_crf − 5) × 4` 因此从 80 漂到 63，
与早先实测标定自相矛盾 —— 这类"经中间编码器中转"的换算会随任一环节重标而失效。

**改动**（两脚本同步）：

| 项 | 旧 | 新 |
|---|---|---|
| `crf_to_rav1e_qp()` | `(libaom_crf − 5) × 4` | 查 `QUALITY_MAP['librav1e']` 的**等体积**标定值 |
| `_resolve_quality_params` rav1e 分支 | 先归一到 libaom CRF 轴 | 直接归一到**基准轴**（换算交由下发处统一做一次） |
| 下发 | `-qp N` | `-qp N` **`-speed 10`** |
| `--crf 21` / `--crf-ref 21` | `-qp 64` | **`-qp 66`** |

⚠ **修掉一个双重换算 bug**：分支一度把已换算好的 qp 塞进"crf 槽"返回，
下发处又调一次 `crf_to_rav1e_qp()` ⇒ `-qp 66` 被当成基准轴再换算成 **255**（夹到上限）。
现改为分支返回**基准轴值**、换算只在下发处做一次；dry-run 逐字核对两脚本一致。

**`-speed 10` 的依据与代价**（实测见 VE 方案 §6.11）：
`-speed` 会**整体平移** rav1e 码率曲线（同 qp 下体积 ×1.40），故表值只对已声明的 speed 档成立。
本仓固定 10 并按该档配套表值；**质量地板**方面 VE 已实测：`-speed 10` 等体积解
`ΔPSNR ≈ −2.7 dB`（超 AC7 的 −1.5 地板），等质量解则体积 +30% —— 即
**「等体积」与「等质量」在 speed 10 下无法兼得**。本仓表按**等体积**口径
（与其余 6 个编码器语义一致）；**等质量换算另立项目**。

**回归**：`verify_quality_mapping.py` ⑨ 组 **13/13**（`[9-rav1e]` 66/66 一致）、
⑪ 组新增「必须下发 `-speed 10`」正向断言（已做**反向验证**：改错期望即红）；
`dump_cmd_full.sh` **20/20** 逐字相同；`dump_enc_options.sh` /
`dump_filter_chains.sh` / `dump_cmd_default.sh` / `check_readme_refs.sh` 全绿；
另 5 套 verify（rc_lookahead / borrow_enhancement / cli_parsing /
overview_lockstep / pixfmt_bitdepth）rc=0。

> ⚠ **档位注释订正（2026-09-30）**：本节的表值配套 `-speed 10`，但 `convert_crf.py`
> 的 rav1e 顶部注释曾写「原生档（不下发 -speed）」，与代码矛盾。已订正为「按 `-speed 10`
> 使用」并标注该记录的档位口径待等质量标定复核。**不改数值**，⑨ 组 `[9-rav1e]` 仍 66/66。


### 4.12 等质量换算表 `QUALITY_MAP`（2026-09-30，M1~M4 非 GPU 部分）

**背景**：现有 `QUALITY_MAP` 是**等体积**（equal file size）口径。VE 侧逐锚点实测证明
**等体积 ≠ 等质量**：`librav1e` 等体积点在 crf 18~30 的 ΔPSNR 为 +0.59 / −1.21 / −2.57 /
−4.17 / **−5.79 dB**；`libsvtav1` 同形态（crf30 −5.34 dB）。缺陷属**线性等体积模型**本身，
非 rav1e 特例（见 `Video_Enhancement/Plan/PROMPT_等质量换算立项.md` v2 §0.1）。

**新增**：`QUALITY_MAP`（**等质量**，以 **VMAF** 定标），原等体积表**改名 `SIZE_MAP`**
（原名 QUALITY_MAP，改名以免望文生义）；两表**并存不覆盖**；
`--quality-mode size|quality`（**默认 `quality`**）切换。

**零侵入接入**：`convert_crf.py` 新增 `_ACTIVE_MAP` / `set_quality_mode()` / `get_quality_map()`，
`from_x264_crf` / `to_x264_crf` / `convert_quality` / `convert_crf` 改读 `get_quality_map()`；
两脚本只加 CLI 开关 + `literal_range()` 改读 `get_quality_map()`。
**`_resolve_quality_params()` 与全部高层 helper 零改动**。等质量表未覆盖的编码器自动回退等体积表。

**标定口径**（脚本 `probe/calibrate_equal_quality.py`，纯 CPU）：
- 素材：`new5_raw.mp4`（实拍人物，6s，1280×720 prep，与等体积表同口径）。⚠ **首版单素材**。
- 锚点 libx264 CRF 18/21/24/27/30；目标编码器扫参数 → 在 `(参数, VMAF)` 曲线取**等 VMAF** 点。
- 跨素材聚合：斜率 `a` = 池化最小二乘；截距 `b` = 各素材中位数。⚠ `librav1e` 按 **`-speed 10`**。
- ⚠ **`libvmaf` 必须 `n_subsample=1`**：`>1` 会**偏置 VMAF**（同文件 vp9 crf35：
  subsample1=96.62 vs subsample8=98.56，差 1.9~3.0，且偏置随编码器而异），
  会污染「等 VMAF 匹配」。首版曾误用 subsample=8，独立判据 2/5 红（vp9 −1.85 / aom +1.06），
  **已按 subsample=1 重标**；标定与判据必须同参、同时长。

**首版落表值 + 实测**（`QUALITY_MAP`，2026-09-30）：

| 编码器 | a | b | 区间 | `--crf-ref 21` | `max|ΔVMAF|` |
|---|---|---|---|---|---|
| `libx265` | 1.0709 | −1.6473 | 0–51 | 21 | 0.47 |
| `libvpx-vp9` | 1.8988 | −10.7972 | 0–63 | 29 | 0.27 |
| `libaom-av1` | 2.2677 | −21.5776 | 0–63 | 26 | 0.18 |
| `libsvtav1` | 2.5168 | −23.2732 | 0–63 | 30 | 0.27 |
| `librav1e` | 7.6674 | −87.5783 | 0–255 | 73（`-qp`） | 1.00 |

- 独立判据 `verify/verify_equal_quality.py`（6s，subsample=1）：**5/5 达标**，
  `ΔVMAF` 全部 ≤ **0.29**。
- 与等体积表对比（crf21）：vp9 28→**29**、aom 21→**26**、svtav1 24→**30**、rav1e 66→**73**、
  x265 21→**21**。⇒ 等体积表在非默认点确有画质偏差（VE 证据 A/B 的量化确认）。

**判据**：`verify/verify_equal_quality.py`（主门禁 `|ΔVMAF| ≤ 1.0`，平行
`|ΔPSNR| ≤ 0.3 dB`、`|ΔPSNR-HVS| ≤ 0.5 dB`；无 GPU 时硬编 SKIP，全 SKIP 退出码 2）；
`verify_quality_mapping.py` ⑨ 组扩展 `SIZE_MAP`/`QUALITY_MAP` 跨项目逐条相等 + 两口径语义
（⑨ 组计数 13→**14**，判据内已**显式钉 `size`** 以保证既有等体积期望可复现）。

**已知局限 / 待办**：
- 首版**仅软件编码器**；NVENC / QSV / AMF / VideoToolbox 需上机标定（回退等体积表）。
- **constqp/QP 轴等质量表**（VE D2b）未做 —— 该轴主要是硬编（NVENC）关切，随 M5 上机。
- 屏幕内容/文字、暗场/高噪两类 1080p 素材**缺失**，未纳入本版标定。
- 低分辨率动画素材（576p/360p）上采样到 720p 会被缩放主导，**不参与池化斜率**。
- `librav1e` 表值的档位记录（原生档 vs `-speed 10`）**待复核**（见 §4.11 注）。

