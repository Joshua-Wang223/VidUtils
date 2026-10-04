---
name: GPU 等质量标定（M5）—— NVENC 全部完成（T4 h264/hevc + L40 AV1）
description: VidUtils GPU 等质量标定（立项 M5）进度：2026-10-04 在 Tesla T4 完成 h264_nvenc/hevc_nvenc、在 NVIDIA L40 完成 av1_nvenc 的 -cq 等质量标定并落表两仓（LOO[0,27] 2.77/3.23/2.79，监控[全] 3.97/5.85/5.76，均按 ≤5.9 分档门禁）；L40 另把 av1 -qp 改仿射（CR-4）、解锁硬编门禁（8 档全绿）、长视频达标。剩余：QSV/AMF/VT 另机
type: project
---

**事实**（2026-10-04；NVENC 三条 `-cq` 等质量行已写入**两仓** `convert_crf.py` 的 `QUALITY_MAP`，逐字相等）：

**(a) T4**（Tesla T4 / 驱动 580.65.06 / ffmpeg 9.0.2）—— `h264_nvenc` / `hevc_nvenc`：

| 编码器 | a | b | 区间 | crf21→ | LOO[0,27]（门禁）/ 监控[全] |
|---|---|---|---|---|---|
| `h264_nvenc` | 0.9295 | 6.2523 | 0–51 | `-cq 26` | 2.77 / 3.97 |
| `hevc_nvenc` | 1.1116 | 2.1606 | 0–51 | `-cq 26` | 3.23 / 5.85 |

**(b) L40**（NVIDIA L40 / 驱动 580.65.06 / ffmpeg 9.0.2）—— `av1_nvenc`：

| 编码器 | a | b | 区间 | crf21→ | LOO[0,27]（门禁）/ 监控[全] |
|---|---|---|---|---|---|
| `av1_nvenc` | 1.4566 | 1.2165 | 0–63 | `-cq 32` | 2.79 / 5.76 |

- 两轮素材池一致 = `input_videos/eqq_calib` **17 条**（12×6s + 5×10s），锚点 `18/21/24/27/30`，
  `n_subsample=1`，`-cq` 轴 + `-b:v 0 -preset p4 -rc vbr`（CR-1/CR-2）。硬编不再回退 `SIZE_MAP`。
- 硬编门禁解锁：`verify/verify_equal_quality.py` 的 `HARD` 含 h264/hevc **+ `av1_nvenc`**（表覆盖 + 本机实编双判据）⇒
  默认 **8 档全绿**（h264 `ΔVMAF`=−0.044 / hevc +0.135 / **av1 +0.004**）。
- L40 探针（`verify_nvenc_quality_gpu.py --expect-av1`）：`C-av1-结论` = **VU 仿射映射值 70 等质量落带**
  （quality 口径；CR-4 与 VE 同源）⇒ 无需改动；`B-av1-结论` = 表值 `-cq 32` 落带（ΔPSNR −0.34 dB / 0.96×）。
- **判据锚点 = 生产工作区间 [0,27]**（2026-10-04 仓主裁定，见 `feedback_loo_gate_anchor_range.md`）：
  LOO 门禁只覆盖 [0,27]，crf>27 作监控列；分档门禁（软编/NVENC ≤5.9、rav1e ≤7.5）。
- 真机长视频（`Earth at Night` 30s 4K）：av1 `ΔVMAF`=**+0.179**（对照 h264 +0.105 / hevc +0.038，与 T4 长片一致）。

**Why:**
- LOO 超立项 `<1.0`，但与 **CPU 软编结构性上限同源**（单素材 in-sample dVMAF < 0.65；av1 < 0.44，非执行错误）——
  **仓主裁定按分档门禁 ≤5.9**（CPU 先例）判达标。**别再试图靠加素材/换拟合压到 <1.0**：
  CPU 侧已证每素材专属表更差、分段/二次无增益。
- 落新表后**默认质量值**变了 ⇒ 凡断言默认/`--crf` 落点的用例都要同步：`hevc` 28→26、**`av1_nvenc` 27→32**
  （`probe/verify_nvenc_quality_gpu.CQ_TABLE_AT_21`、README 默认值已同步）；`--crf-ref 21 + constqp` 的 `-qp`
  （h264 21 / hevc 20 / av1 63）不变。
- ⚠ **T4 ≠ L40**：h264/hevc 行按 Turing 标定；L40 长片对照与 T4 一致（无跨代异常），但表值**仍以 T4 为准**。

**How to apply:**
- 剩余硬编（QSV/AMF/VT）按各自专项方案另机执行；`av1_nvenc` 必须过 `--expect-av1` 的 fail-fast
  （非 Ada 卡上加它即 exit 2，防把 B/C 组 av1 静默 SKIP 当成"已验"）。
- 改 nvenc 落点/默认值后回跑：`verify/verify_quality_mapping.py`（⑨ 组 14/14）、
  `verify/verify_equal_quality.py`（8 档全绿）、`probe/verify_nvenc_quality_gpu.py`、`probe/t4_acceptance.py`。
- ⚠ 探针 C 组的候选尺度（21/84/105）是**对照组**，单格 FAIL 不计门禁（判词由 `C-av1-结论` 汇总）；
  B 组「朴素值」同理（同处理见 `verify_nvenc_quality_gpu.py` group_b/group_c）。
- ⚠ 标定口径铁律不变：`n_subsample=1`、与 CPU 表同锚点同 prep；**换 ffmpeg 构建须重标**
  （指纹 T4/L40 均 580.65.06 / ffmpeg 9.0.2）。

**追加（2026-10-04）· CR-4：av1 `-qp`（quality 口径）改仿射** ——
VE 落其 `QUALITY_MAP_QP['av1_nvenc']` 后通知 VU（触发跨仓契约 **CR-4**）。VU 用 VE 的 17 素材 QP 点
**独立复现**：等质 QP 中位 **65.7/92.3/117.9/140.8**（ref 21/24/27/30），拟合 **a=7.9338 / b=−97.5136**
（残差 ≤3.4）；**过原点 ×3 只在 ref≈21 成立**（ref24/27/30 偏低 −20/−37/−51）。
⇒ 两脚本加 `_QP_AFFINE_QUALITY`：**quality 口径用仿射、size 口径保留 `×3`**（与 VE 分口径一致）；
`to_constqp_qp`/`from_constqp_qp` 按 `get_quality_mode()` 分流；⑪ 组新增 quality 断言。
- ⚠ **判据轴**：探针 C 组 av1 改以 **VMAF** 判（qp70：ΔVMAF ≈ −0.2 = 等质量，但 ΔPSNR 越 PSNR 代理带）
  —— 与等质量表同轴；PSNR/码率降为 evidence。
- ✅ **h264/hevc 的 quality 口径已一并对齐 VE**（`_QP_AFFINE_QUALITY` 加 `(0.9704,1.4767)` /
  `(1.1083,−2.9183)`）⇒ 两仓 `-qp` 在 quality 与 size 两口径、**全 ref 0..51 逐点一致（0 差异）**。
- ⚠ **av1 CQ 行取 VE 规范化值 `(1.4566,1.2165)`**（VE 20b9e81；旧 `(1.4573,1.1022)` 无法由 VE 池复现）
  ⇒ quality 口径 ref21 的 `-qp` 由 71 → **70**。
- ⚠ **⑨ 跨仓门禁曾有假门禁**：`verify_quality_mapping.py` 加载 VE `quality_map.py` 时，其
  `from convert_crf import …` 命中 `sys.modules` 里 **VU** 的同名缓存 ⇒ 「两仓表逐条相等」退化为
  VU↔VU、**从不检查 VE**。已修（临时把 VE convert_crf 挂名 `convert_crf` 再加载 VE quality_map，
  并把 [9] 功能比对统一用 VU_CRF）；真实跨仓态势看 `probe/calibrate_equal_quality.py::cross_repo_status`。
