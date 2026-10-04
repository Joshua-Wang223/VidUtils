---
name: GPU 等质量标定（M5）—— T4(h264/hevc) 已上机完成，L40(AV1) 待做
description: VidUtils GPU 等质量标定（立项 M5）进度：2026-10-04 在 Tesla T4 完成 h264_nvenc/hevc_nvenc 的 -cq 等质量标定并落表两仓（LOO 3.97/5.85，按 ≤5.9 分档门禁）；harness GPU 支持 / 跨仓契约 CR-1(p4)/CR-2(rc vbr) / 探针 / 门禁 / 基线已同步。剩余：L40(AV1) + QSV/AMF/VT 另机
type: project
---

**事实**（2026-10-04，Tesla T4 / 驱动 580.65.06 / ffmpeg 9.0.2）：`h264_nvenc` / `hevc_nvenc` 的
`-cq` 等质量行已标定并写入两仓 `convert_crf.py` 的 `QUALITY_MAP`（逐字相等）：

| 编码器 | a | b | 区间 | crf21→ | LOO worst ΔVMAF |
|---|---|---|---|---|---|
| `h264_nvenc` | 0.9295 | 6.2523 | 0–51 | `-cq 26` | 3.97 |
| `hevc_nvenc` | 1.1116 | 2.1606 | 0–51 | `-cq 26` | 5.85 |

- 素材池 = `input_videos/eqq_calib` **17 条**（6s 侧 12 + 10s 侧 5），锚点 18/21/24/27/30，
  `n_subsample=1`，`-cq` 轴 + `-b:v 0 -preset p4 -rc vbr`（CR-1/CR-2）。硬编不再回退 `SIZE_MAP`。
- 硬编门禁解锁：`verify/verify_equal_quality.py` 默认 `--codecs` 已含 nvenc，默认 **7 档全绿**
  （h264 `ΔVMAF`=−0.044 / hevc +0.135）。
- T3 探针（`verify_nvenc_quality_gpu.py` C 组）复核 h264/hevc `-qp 21` 落带；报告归档 `verification_report/`。

**Why:**
- LOO 超立项 `<1.0`，但与 **CPU 软编结构性上限同源**（单素材 dVMAF<0.65、非执行错误）——
  **仓主裁定按分档门禁 ≤5.9**（CPU 先例）判达标。**别再试图靠加素材/换拟合压到 <1.0**：
  CPU 侧已证每素材专属表更差、分段/二次无增益。
- **`hevc_nvenc` 默认 `-cq` 由 SIZE_MAP 的 28 变为等质量表的 26** ⇒ 凡断言 hevc 默认/`--crf`
  落点的用例都要同步（`probe/t4_acceptance.py` A2/A4/A7、`probe/verify_nvenc_quality_gpu.py::CQ_TABLE_AT_21`、
  README 默认值）。⚠ `--crf-ref 21 + constqp` 的 `-qp` 也由 20 → 21（表近似可逆）。
- ⚠ **T4 ≠ L40**：本行按 Turing 标定；Ada(L40) 上跑 h264/hevc 若需等质量，须做跨代复核。

**How to apply:**
- 新增硬编（L40 的 `av1_nvenc`、QSV/AMF/VT）仍按各自专项方案另机执行；`av1_nvenc` 必须过
  `--expect-av1` 的 fail-fast。
- 改 nvenc 落点/默认值后回跑：`verify/verify_quality_mapping.py`（⑨ 组 14/14）、
  `verify/verify_equal_quality.py`（7 档全绿）、`probe/t4_acceptance.py`、`probe/verify_nvenc_quality_gpu.py`。
- ⚠ 标定口径铁律不变：`n_subsample=1`、与 CPU 表同锚点同 prep；**换 ffmpeg 构建须重标**
  （本表指纹 T4 / 580.65.06 / 9.0.2）。
