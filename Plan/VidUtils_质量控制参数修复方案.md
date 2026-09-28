# VidUtils 质量控制参数（crf / cq / qp / preset）修复方案

- 适用脚本：`vidcrop_cpu_v2.py`、`vidcrop_hwaccel.py`（**孪生约定**：共享参数的取值表、
  默认值、报错首行、整条 ffmpeg 命令都必须逐字一致）
- 共享真源：`convert_crf.py`（`QUALITY_MAP` + `from_x264_crf` / `to_x264_crf` / `convert_quality`），
  与 `Video_Enhancement/src/utils/convert_crf.py` 必须是同一份表（判据 ⑨ 组会断言）
- 姊妹文档：`Video_Enhancement/Plan/Video_Enhancement_质量控制参数修复方案.md`
- 上机验证脚本：`probe/verify_nvenc_quality_gpu.py`（T4 / L40，重点 av1_nvenc）
- 判据：`verify/verify_quality_mapping.py`（① ~ ⑩ 组）

---

## 0. 状态总览

| 编号 | 内容 | 优先级 | 状态 | 依据强度 |
|---|---|---|---|---|
| V3 | `QUALITY_MAP['av1_nvenc']` 的 hi 51 → 63 | P0 | **已落地** | 实测（`ffmpeg -h encoder=av1_nvenc` → `-cq (0 to 63)`） |
| V4 | `literal_range()`：字面量按生效编码器量程校验 + 同族下发钳位 | P0 | **已落地** | 实测（`-cq 60` 让 ffmpeg 报 out of range；`-crf 60` 被静默按 51 编码） |
| V6 | `_QP_ONLY_CODECS`（VAAPI 族）→ 归一到基准轴后走 `-qp` | P0 | **已落地** | 实测（`ffmpeg -h encoder=h264_vaapi` → 只有 `-qp (0 to 52)`） |
| V1 | constqp 的 `-qp` 回基准轴（不再拿 CQ 轴值直发） | P0 | **已落地** | 有跨项目实测反证（VE G7：`-qp 21` 对齐 crf21）；新增 `_QP_SCALE` / `_QP_LIMITS` / `to_constqp_qp()` / `from_constqp_qp()` |
| V2 | `--qp` 落到软编按基准轴回算（不再走 CQ 轴） | P0 | **已落地** | 同上（同一处公式） |
| V5 | 硬件能力表：QSV/VT 的 `-cq`/`-preset` 校正 + 两脚本集合统一 | P0 | **已落地** | 实测（本机 `h264_qsv` 无 `-cq`，`-preset` 只收 veryfast..veryslow）；AMF 待上机 |
| V7 | 默认质量统一为基准 21（`DEFAULT_CQ` 23 删除） | P1 | **已落地** | 纯计算（CQ 23 ≡ crf 18） |
| V8 | `librav1e` 移出 `CRF_SUPPORTED_CODECS`（与 VE 的 `supports_crf` 对齐） | P1 | **已落地** | 实测（`--crf 21` → `-qp 64`，`--crf-ref 21` → `-qp 80`） |
| V9 | `libsvtav1` / `libvpx-vp9` / `libx265` 的偏移按**真实素材等体积**重标 + 钉死 svtav1 的 preset | P1 | **已落地（真实素材）** | 真实素材 `input_videos/new5_raw.mp4`（1080p→720p 4s）等体积标定：x265 `0.9155x+1.64`、vp9 `1.620x−5.76`、svtav1 `1.945x−15.62`（残差 ≤0.73 档） |
| V10 | preset 表与 VE 对齐 + svtav1 的 p7/veryslow 自洽 | P1 | **已落地（对齐官方枚举）** | VE 的 `[FIX-PRESET-ALIGN]` 已改用 ffmpeg 官方枚举；VidUtils 的 x264→NVENC 对齐之，反向降级表保持 `p5→medium`（保 lockstep/基线） |
| V11 | `hevc_videotoolbox` 的 b 105 → 100 | P2 | **已落地** | 纯计算（lo=1 永不可达，crf 0~2.58 全饱和） |
| V12 | ⑨ 组的待修项逐条转 chk | P2 | **已落地** | 流程（⑨ 现在默认就是门禁；新增 ⑪ 组正向断言） |

> **第二轮（2026-09-28）已完成全部剩余项**：V1/V2/V5/V7/V8/V9/V10/V11/V12。
> `verify/verify_quality_mapping.py` 的 ⑨ 组从 2/13 变为 **13/13 一致**（且默认计入退出码），
> 并新增 ⑪ 组（V1/V2/V5/V7/V8/V10/V11 的正向断言）。上机验收脚本
> `probe/verify_nvenc_quality_gpu.py`（T4 无 AV1 NVENC 自动 SKIP、L40 跑满）；
> 标定脚本 `probe/calibrate_soft_offsets.py`。
> ⚠ 仍待**上机**核实的只有：AMF 的能力表、以及 av1_nvenc 的 `-cq` 偏移（B/C 组）。

---

## 1. 已落地项（V3 / V4 / V6）

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

## 2. 各项改法（**均已落地**，下列文字保留为改动依据/历史）

> 2026-09-28 第二轮已完成 V1/V2/V5/V7/V8/V9/V10/V11/V12，落地细节与实测数值见
> `memory/project_rate_control_params.md`；本节的"现状/改法"是**动手前**的记录。
> 三处与原文有出入、以实际落地为准：
> 1. **V9 用真实素材标定**（`input_videos/new5_raw.mp4`），且 **libx265 也一并改了**
>    （`0.9155x+1.6385`）；实际落表：`libx265 0.9155/1.6385`、`libvpx-vp9 1.6198/−5.7553`、
>    `libsvtav1 1.9450/−15.62`。`[9-vp9]` 的判据相应改成"常用区不饱和 + 零点合理"。
> 2. **V10 的对齐目标变了**：VE 在本轮期间把 `_PRESET_P_INDEX` 改成了 **ffmpeg 官方枚举**
>    （`medium→p4`）。VidUtils 的 `X264_TO_NVENC_PRESET` 对齐之；`NVENC_TO_X264_PRESET`
>    刻意保持 `p5→medium`（保两脚本命令 lockstep 与基线）。
> 3. **V5 把 VideoToolbox 也移出了 CQ 集**（其质量轴是 `-q:v`），两脚本 CQ 集现相等。

### V1 + V2：constqp 的 `-qp` 必须回基准轴

**现状**：`_resolve_quality_params()` 在 `rc_mode == 'constqp'` 时，把**按 CQ 轴算出的值**
直接当 `-qp` 下发（`_QP_HINT` 写着"与 --cq 同量纲"）。

| 输入 | 现在 | 应为 | 差 |
|---|---|---|---|
| `--codec h264_nvenc --rc-mode constqp --crf-ref 21` | `-qp 26` | `-qp 21` | 5 |
| `--codec hevc_nvenc --rc-mode constqp --crf-ref 21` | `-qp 28` | `-qp 20~21` | 8 |
| `--codec hevc_nvenc --rc-mode constqp --qp 18` 降级到 libx265 | `-crf 14` | `-crf 21` | 7 |

**依据**：ffmpeg 选项语义（`-cq` 是 VBR 的 targetQuality、`-qp` 是 constQP 的真 QP）；
SDK 结构（`constQP.qpInterP` 与 `targetQuality` 是两个字段）；
Video_Enhancement 的真实素材实测（`-qp 21` 相对 libx264 crf21 = 1.40× 码率 / ΔPSNR −0.26，
落在 RATE_PASS 带内）。

**改法**：新增 `to_constqp_qp(codec, value)`（与 VE 同名函数对应）+ `_QP_SCALE` 表
（h264/hevc_nvenc = 1、**av1_nvenc = 4**、librav1e = 4 已有、vaapi = 1），constqp 分支与
"`--qp` 落到软编"分支都改走它。

**必须同步改判据**：`verify/verify_quality_mapping.py` 的 ①组（`--qp 18 → -crf 14/16` →
`21/24`）与 ②组（`constqp -crf-ref 21 → -qp 28/26` → `21/20`）。这两处目前把**待修值**
钉成了期望值。

**回归方式**：`--dry-run` 对比 `-qp` 值；上机用 `probe/verify_nvenc_quality_gpu.py` 的 C 组
（H.264/HEVC `-qp 21` 应落在码率带内）。

### V5：硬件能力表

| 编码器 | 本机实测 | 现状 | 改法 |
|---|---|---|---|
| `h264_qsv` / `hevc_qsv` / `av1_qsv` | 无 `-cq`/`-crf`/`-qp`/`-global_quality`；`-preset` 只收 `veryfast..veryslow`（int 0~7） | 在 `CQ_SUPPORTED_CODECS` 与 `PRESET_SUPPORTED_CODECS` 里；默认即下发 `-cq 23 -preset p5` | 移出 CQ 集；preset 加 QSV 档名映射（或不下发） |
| `h264_amf` 等 | 本机无 | 同上 | **待上机**核实后再定 |
| `*_videotoolbox` | 本机无 | hwaccel 在 CQ/PRESET 集里、cpu_v2 只在 PRESET 集里（两脚本不一致） | 待上机核实；至少先把两脚本集合统一 |
| `h264_vaapi` / `hevc_vaapi` | 有 `-qp (0 to 52)` | 已由 V6 接管 `-qp` | 完成 |

⚠ 两脚本的 `CQ_SUPPORTED_CODECS` **当前不相等**（hwaccel 含 videotoolbox、cpu_v2 含
h265_nvenc），违反孪生约定；⑨ 组 `[9-CQ]` 在盯这一条。

### V7：默认质量统一

`DEFAULT_CRF = 21` 落在基准轴、`DEFAULT_CQ = 23` 落在 CQ 轴（≡ crf 18）⇒ 未给质量参数时
硬编比软编**过配 3 档**。改法：引入 `DEFAULT_REF = 21`，未给质量时按基准换算到目标编码器
（h264_nvenc → 26、hevc_nvenc → 28、libx265 → 24）；constqp 兜底同理（现为 `DEFAULT_CQ`）。

### V8：librav1e 的双链

`--crf 21` → `-qp 64`（按 libaom 刻度）而 `--crf-ref 21` → `-qp 80`（按基准轴）。
改法：把 `librav1e` 移出 `CRF_SUPPORTED_CODECS`（与 VE 的 `supports_crf()` 对齐），
两条路径都走基准轴 → 都是 80，同时打印换算提示。

### V9：软编偏移重标定（本机等体积实测，640×480 testsrc2 2s，默认速度档）

| codec | 表（a·crf+b） | 实测拟合 | crf 21 处：实测 vs 表 | 结论 |
|---|---|---|---|---|
| libx265 | 1.0x+3 | 0.740x+8.54 | 24.0 vs 24 | 常用区（18~28）误差 ≤0.7 档，**保持** |
| libvpx-vp9 | 1.98x−14.46 | 1.685x−4.82 | 29.9 vs 27.1（+2.8） | 表偏小 ⇒ 过配约 25~30% 体积 |
| libsvtav1 | 1.0x+6 | 1.40x+3.16 | 32.7 vs 27（+5.7） | 表系统性偏小 ⇒ 过配约 30% |
| libaom-av1 | 1.0x+4 | 数据不足 | 27.3@crf24 vs 28 | 该点吻合，需补点 |

**改法**：按实测拟合更新两处 b/a，**并在下发 `libsvtav1` 时显式钉 preset**
（其默认 `-2` 会按核数/分辨率自动变，等效点本身会漂移）。

⚠ 标定的三条 caveat：合成素材（真实素材通常另有 2 dB 量级差异）、单一分辨率
（高分辨率下 AV1/VP9 优势更大、等效点更高）、等体积 ≠ 等质量。**建议先在真实素材复核**
（`probe/verify_nvenc_quality_gpu.py` 的 B 组同套判据），再落表。

### V10：preset 表

1. 与 VE 的 `_PRESET_P_INDEX` 错位 1 档：`superfast/veryfast/faster/fast`
   （medium 两边都是 p5，slower/veryslow 一致）。
2. `libsvtav1` 的 `p7 → 4` 但 `veryslow → 2`（而 `NVENC_TO_X264_PRESET['p7'] = 'veryslow'`）；
   `fast` 与 `medium` 都映射 8。
3. 参考：ffmpeg 官方枚举为 `p4=medium(default) / p5=slow / p7=slowest(best quality)`，
   而 `DEFAULT_PRESET_GPU='p5'` 被注释成 "medium"（实际落在官方 slow 档）。

### V11 / V12
- V11：`hevc_videotoolbox` 的 `b=105` 与 `hi=100` 冲突 → crf 0~2.58 全饱和到 100，
  `lo=1` 永不可达（需 crf≈53.6 > 51）。改 `b=100`（与 `h264_videotoolbox` 对齐）。
- V12：⑨ 组待修项逐条转 `chk`，最后把 `STRICT_KNOWN` 默认打开。

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
| av1_nvenc | `-qp` | `-1..255` | 未区分（见 V1） | 待修 |
| h264/hevc_vaapi | `-qp` | `0..52` | 0~51 | V6 已接管 |
| qsv / amf / videotoolbox | — | 本机无 | — | 待上机（V5） |

### 3.2 constqp 的 `-qp` 取值（crf_ref=21）

| codec | `-qp` 刻度 | 正确值 | 现状 | 备注 |
|---|---|---|---|---|
| h264_nvenc | 0~51，= x264 QP | 21 | 26（V1 待修） | 差 5 档 ⇒ 画质偏松 |
| hevc_nvenc | 0~51，= x264 QP | 21 | 28（V1 待修） | 差 7.5 档 |
| **av1_nvenc** | **0~255（qindex）** | **约 84（21×4）** | **27 / 21** | 两项目都错：27 在 0~255 上是近无损 |
| libx264 / libx265 | 0~51 | 21 / 24 | 14 / 16（V2 待修） | 差 7 |
| libsvtav1 | 0~63（= crf 刻度） | 27 | 26 | 差 1，可接受 |
| librav1e | 0~255（4×） | 80 | 64（字面量）/ 80（ref） | V8 待修 |
| vaapi | 0~52 | 21 | 21（V6 已修） | 完成 |

---

## 4. 回归与验收

| 门 | 命令 | 覆盖 |
|---|---|---|
| 判据（① ~ ⑩） | `python3 verify/verify_quality_mapping.py` | 质量换算、0 档、量程、VAAPI 通路、跨项目体检 |
| 门禁模式 | `STRICT_KNOWN=1 python3 verify/verify_quality_mapping.py` | ⑨ 组待修项也计入退出码 |
| 命令逐字一致 | `bash test/dump_cmd_full.sh` | 两脚本同一条逻辑请求 → 逐字相同命令 |
| 编码选项快照 | `bash test/dump_enc_options.sh` | 对照 `test/baseline/enc_before.txt` |
| 上机（T4 / L40） | `python3 probe/verify_nvenc_quality_gpu.py --src <真实素材>` | NVENC 的 CQ/QP 实测（B/C 组） |

`probe/verify_nvenc_quality_gpu.py` 要点：
- 自动探测 **T4 无 AV1 NVENC**（选项表存在但真编码失败）→ B/C 组的 av1 格标 SKIP 并说明，
  不是 FAIL；**L40（Ada）才跑满**。
- 判据与 VE 的 G7 同一套容忍带（码率比 0.65~1.50、ΔPSNR 下探 ≤1.5 dB），保证两边结论可比。
- `--quick` 只跑纯逻辑组（本机也能跑）；`--selftest` 只验装置。
