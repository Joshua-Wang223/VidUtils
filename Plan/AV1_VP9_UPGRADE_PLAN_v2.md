# AV1 / VP9 编码支持升级（综合定稿版）

> **状态：已完成（2026-09-23 核验）。本文档是历史计划，留档用 —— 不代表待办。**
> 下面的改动清单已全部落地：AV1 降级链（`libsvtav1` > `librav1e` > `libaom-av1`，见
> `_get_software_fallback()`）、`has_encoder_av1`、`av1_qsv` / `av1_amf` / `libsvtav1`
> 的注册都已在代码里。**现状的权威说明**见 `README.md` 与 `memory/MEMORY.md`。
> ⚠ 两处已随后的重构失效，别再照着做：
> ① 文中的 `--fallback-policy nvenc-only` 分支**已不存在**（三轴正交重构把该参数收敛为
> `auto` / `strict`，见 `memory/project_three_axis_model.md`）；
> ② 全文**行号已整体漂移**（`_generate_strategies` 等符号名仍有效）⇒ 按符号名检索。
>
> 归档时间：2026-09-23　｜　原始写作时间：2026-09-10

整合 `AV1_VP9_UPGRADE_PLAN.md`（**已被本版取代，2026-09-23 移除，不再随仓库发布**）与
`eventual-lemur.md` 两份计划，并依据**实际代码核验**修正了二者的错误与遗漏。
所有"现状"结论均已通过读取源码与 `ffmpeg -encoders` 验证。

## 目标

为 `vidcrop_hwaccel.py`（GPU 硬件加速版）与 `vidcrop_cpu_v2.py`（CPU 版）补齐 AV1 / VP9 全链路支持：

- CPU 软编：`libaom-av1`、`libsvtav1`、`librav1e`、`libvpx-vp9`
- GPU 硬编：`av1_nvenc`（主）、`av1_qsv`、`av1_amf`（显式指定可用）
- `av1_nvenc` 不可用时降级到 **CPU AV1 链**（保持编码家族不变）

## 已确认决策

| 决策点 | 选择 |
|---|---|
| GPU 广度 | 含 `av1_qsv` + `av1_amf` 完整支持（仅常量注册，不进策略链自动探测） |
| `libsvtav1` | 补全全套注册，作为**首选** CPU 降级编码器 |
| 降级行为 | `av1_nvenc` 不可用 → CPU AV1 链（libsvtav1 > librav1e > libaom-av1） |
| 交付物 | 会话计划文件 + 导出一份到 `d:\Workspace_Python\AV1_VP9_UPGRADE_PLAN_v2.md` |

## 现状核验（关键事实）

**环境**：`ffmpeg -encoders` 确认可用：`av1_nvenc`、`av1_qsv`、`av1_amf`、`libsvtav1`、`libaom-av1`、`librav1e`、`libvpx-vp9`。**不存在 `vp9_nvenc`**（VP9 硬编仅有 vaapi/qsv），计划 2 中该项为幻觉，已剔除。

**两处类名/函数名与计划描述不符**（实施时以代码为准）：
- hwaccel 能力类实为 `HardwareCapabilities`（`vidcrop_hwaccel.py:363`），非计划 1 所称 `HWAccelCaps`
- 策略函数为 `_generate_strategies`（:1840），非 `build_strategies`；hwaccel **无** `build_encoder_options`（编码参数在 `build_ffmpeg_cmd` 内联，:2074-2086）

**`libsvtav1` 注册缺失（两份计划均漏）**：仅存在于 `_PIXFMT_10BIT_BY_ENCODER`（hwaccel:908 / cpu_v2:957），**不在** `CODEC_ALIASES`、`CRF_SUPPORTED_CODECS`、`CODEC_CONTAINER_MAP`、`CODEC_PROFILE` 中。

**`_get_software_fallback` 缺 AV1 分支（两份计划均漏，最危险）**：`vidcrop_hwaccel.py:1821` 只映射 hevc 系 → `libx265`，其余**一律返回 `libx264`**。不修改则 `av1_nvenc` 失败会静默降级为 H.264。

**第二处 NVENC 探测（两份计划均漏）**：`vidcrop_hwaccel.py:2759-2762`（`--fallback-policy nvenc-only` 分支）绕过 `detect_cuda_capabilities`，漏改会导致该模式下 `has_encoder_av1` 恒为 False。

**hwaccel 无 `CODEC_PROFILE`**（仅 cpu_v2:170 有）— 计划 2 要求给 hwaccel 加画像表属目标错误。

**CLI 只有长选项**：`--input / --output / --output-width / --output-height / --dry-run`；**不存在** `-i/-o/-ow/-oh`（计划 1 的测试命令需重写）。

---

## 改动清单

### 1. `d:\Workspace_Python\VidUtils\vidcrop_hwaccel.py`

| 位置 | 改动 |
|---|---|
| `CODEC_ALIASES` :112 | 新增 `av1`→`libaom-av1`、`av01`→`libaom-av1`、`svtav1`→`libsvtav1`、`vp9`→`libvpx-vp9`、`vp08`→`libvpx`、`nvenc_av1`→`av1_nvenc`、`av1_nvenc`→`av1_nvenc` |
| `CODEC_CONTAINER_MAP` :126 | 新增 `libsvtav1`→`.mp4`、`av1_nvenc`→`.mp4`、`av1_qsv`→`.mp4`、`av1_amf`→`.mp4` |
| `PRESET_SUPPORTED_CODECS` :148 | 新增 `av1_nvenc`、`av1_qsv`、`av1_amf` |
| `CRF_SUPPORTED_CODECS` :157 | 新增 `libsvtav1` |
| `CQ_SUPPORTED_CODECS` :164 | 新增 `av1_nvenc`、`av1_qsv`、`av1_amf` |
| `_PIXFMT_10BIT_BY_ENCODER` :905 | 新增 `av1_qsv`→`p010le`、`av1_amf`→`p010le`（`av1_nvenc` 已有） |
| `HardwareCapabilities.__init__` :366 | 新增 `self.has_encoder_av1 = False` |
| `has_nvenc()` :379 | 新增 `av1_nvenc` → 返回 `has_encoder_av1` |
| `has_any_encoder()` :386 | 纳入 `has_encoder_av1` |
| `summary()` :402 | items 增加 `('av1_nvenc', self.has_encoder_av1, 'has_encoder_av1')` |
| `detect_cuda_capabilities()` :713 后 | 新增 `av1_nvenc` 探测块 + `_mark_detected('has_encoder_av1')`（仿 :712-720） |
| `_get_software_fallback()` :1821 | **新增 AV1 分支**（见降级设计） |
| `_generate_strategies()` :1865 | `is_nvenc` 元组加入 `av1_nvenc` |
| 重复探测 :2759-2762 | 补 `has_encoder_av1 = _check_nvenc_available(..., 'av1_nvenc')` + `_mark_detected`；:2764 汇总打印补 av1 |
| `cq_to_crf()` :1772 | 新增 AV1 映射（见下） |

`build_ffmpeg_cmd` 的质量/预设/像素格式分支（:2077-2093）均由上述集合驱动，**无需改动**；:2090 的 `endswith('_nvenc')` 已自动覆盖 `av1_nvenc`。`check_container_compatibility`（:827/:829）已兼容 `av1`/`vp9`。

### 2. `d:\Workspace_Python\VidUtils\vidcrop_cpu_v2.py`

| 位置 | 改动 |
|---|---|
| `CODEC_ALIASES`（vp9/av1/rav1e 已有） | 新增 `svtav1`→`libsvtav1`、`av01`→`libaom-av1`、`av1_nvenc`→`av1_nvenc` |
| `CODEC_CONTAINER_MAP` :115 | 新增 `libsvtav1`→`.mp4`、`av1_nvenc`→`.mp4`、`av1_amf`→`.mp4` |
| `PRESET_SUPPORTED_CODECS` :131 | 新增 `av1_nvenc`、`av1_amf`（`av1_qsv` 已在 CQ 集合，需同步进 PRESET） |
| `CRF_SUPPORTED_CODECS` :139 | 新增 `libsvtav1` |
| `CQ_SUPPORTED_CODECS` :641 | 新增 `av1_nvenc`、`av1_amf`（`av1_qsv` 已有） |
| `CODEC_PROFILE` :170 | 新增 `"libsvtav1": (4, 1.3)` |
| `normalize_preset()` :676 | NVENC 白名单元组加入 `av1_nvenc` |
| `cq_to_crf()` :705 | 新增 AV1 映射（见下） |

`default_preset_for()`（:655）以 `CQ_SUPPORTED_CODECS` 判定 → 加入后 `av1_nvenc` 自动取 `p5`。
`build_encoder_options` / `_v2`（:871/:904）已正确处理 `libvpx-vp9` 的 `-b:v 0`，**无需改动**。

### 3. `cq_to_crf` 映射（两文件同步）

AV1 编码器 CRF 量纲与 x264/x265 不同，**不可复用 `min(51, ...)` 上限**：

```
libaom-av1 / libsvtav1 : crf = min(63,  cq + 4)
librav1e               : crf = min(255, cq + 4)
```

（hevc→libx265 的 `+4` 与 h264→libx264 的 `+1` 保持不变）

### 4. 降级设计

```
--codec av1_nvenc
  ├─ has_encoder_av1 = True  → 策略1 全GPU流水线 (crop模式) / 策略2 硬解+av1_nvenc
  └─ 不可用 → _get_software_fallback('av1_nvenc')
        ├─ libsvtav1  (速度/质量平衡，首选)
        ├─ librav1e   (质量优先)
        └─ libaom-av1 (参考实现，末选)
```

`_get_software_fallback` 改写为准则：AV1 系（`av1_nvenc`/`av1_qsv`/`av1_amf`/`libaom-av1`/`libsvtav1`/`librav1e`）→ `libsvtav1`；HEVC 系 → `libx265`；其余 → `libx264`。**降级时打印明确提示**，说明编码器已被替换及其原因。

`vp9` / `libvpx-vp9`：无硬件路径，统一走 CPU，失败即报错。

---

## 实施顺序

1. **cpu_v2 常量层**：别名 / 容器 / CRF / CQ / PRESET / CODEC_PROFILE 补齐 → `--dry-run` 冒烟
2. **cpu_v2 逻辑层**：`normalize_preset`、`cq_to_crf` → `--dry-run` 复验
3. **hwaccel 常量层**：同上 + `_PIXFMT_10BIT_BY_ENCODER`
4. **hwaccel 能力探测**：`HardwareCapabilities` 三处 + `detect_cuda_capabilities` + :2759 重复分支
5. **hwaccel 策略层**：`_get_software_fallback` AV1 分支 + `is_nvenc` 元组 + `cq_to_crf`
6. **真机验证**：`av1_nvenc` 实跑（本机硬件已确认可用）
7. **回归**：H.264 / HEVC 全路径

## 验收标准

**Dry-run 门禁**（阶段 1-5 每步执行，确认命令含正确编码器、容器扩展、质量参数、滤镜链）：

```
python vidcrop_hwaccel.py --input <test> --output ./out --output-width 1280 --output-height 720 --codec av1_nvenc --cq 25 --preset p5 --dry-run
python vidcrop_cpu_v2.py  --input <test> --output ./out --output-width 1280 --output-height 720 --codec libsvtav1 --crf 25 --dry-run
python vidcrop_cpu_v2.py  --input <test> --output ./out --output-width 1280 --output-height 720 --codec vp9 --crf 30 --dry-run
```

**真机编码**：`av1_nvenc`、`libsvtav1`、`libaom-av1`、`libvpx-vp9` 各跑一个短片，确认输出可播放、扩展名正确（AV1→`.mp4`、VP9→`.webm`）。

**降级**：`--fallback-policy cpu-only` 下指定 `av1_nvenc` → 落到 `libsvtav1` 且打印提示；`--fallback-policy nvenc-only` 下 `has_encoder_av1` 正确（验证 :2759 分支已同步）。

**回归**：`libx264`/`libx265`/`h264_nvenc`/`hevc_nvenc` 输出与改动前一致；crop / cover / `--crop-ratio` / 音频 / 批量递归正常。

## 风险与注意事项

- **副本漂移**：目录下存在 `vidcrop_hwaccel - Copy.py`、`vidcrop_cpu_v2 - Copy.py`（与主文件同大小同日期）。**只改主文件**，不要误编辑副本；如需保留请先确认其用途。
- **QSV/AMF 验证边界**：本机为 NVIDIA 环境，`av1_qsv`/`av1_amf` 只能做 dry-run 验证，无法真机编码；且 hwaccel 无 QSV/AMF 运行时探测框架，故二者仅支持**显式指定**，不参与策略链自动选择（新增探测框架属范围外）。
- **`libaom-av1` 性能**：单线程极慢，`CODEC_PROFILE` 给 `(4, 1.5)`，批量场景下确保它处于降级链末位。
- **CRF 量纲**：切勿对 AV1 复用 x264 的 51 上限（见上文映射）。
- **参考实现**：`Video_Enhancement\Video_Enhancement\src\utils\video_utils.py`（3411 行）仅作为取值参考；仓库内另有 3 份同名副本，勿混用。

## 交付

- 本计划文件（会话计划）
- 批准后导出至 `d:\Workspace_Python\AV1_VP9_UPGRADE_PLAN_v2.md`

## 关键文件

- `d:\Workspace_Python\VidUtils\vidcrop_hwaccel.py`（2963 行）
- `d:\Workspace_Python\VidUtils\vidcrop_cpu_v2.py`（2866 行）
