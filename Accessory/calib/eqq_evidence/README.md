# verify_equal_quality.py 实测记录 · CPU 软编 5 档

> 这份记录回答「某次 `verify_equal_quality.py` 跑出来到底是什么数字」。
> 记录它是因为本仓吃过一次亏：**报告若不含口径锚点，三个月后没人能判断它
> 对应哪一版表**（见 `Plan/PROMPT_GPU侧继续测试.md` §10 的落盘要求）。

## 本次运行的锚点（缺了这条，记录就不可复核）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-06 |
| 脚本 | `Accessory/verify/verify_equal_quality.py`（未改参数，走默认） |
| 源素材 | `/mnt/d/Workspace_Python/input_videos/new5_raw.mp4`（53.3 MB） |
| 切片 | 6s / 1280x720 / 180 帧 / 30 fps |
| **prep md5** | `0209b9e62a8b740031b4a890dc88cbb5` |
| 锚点 | libx264 CRF 21 → `vmaf=99.135 psnr=42.393 hvs=42.807 ssim=0.9805 xpsnr=37.862` |
| 表口径 | `QUALITY_MAP`（**等质量**，`convert_crf.py` 第八版） |
| 环境 | Ubuntu 26.04 / Python 3.14.4 / **ffmpeg 9.0.2** / 本机无 GPU |
| 退出码 | **0** |

> `prep md5` 与 `Accessory/calib/eqq_evidence/m2_7src/report.json` 里
> `new5_raw.mp4` 的 `prep_md5` **完全一致** ⇒ 本次口径与七素材标定同源，
> 结果可与归档的逐点数据直接对照。

## 结果：主门禁 5/5 达标，3 项 SKIP

主门禁 **|ΔVMAF| ≤ 1.0**（唯一 FAIL 依据；等质量表以 VMAF 定标，
同 VMAF 不蕴含同 PSNR，故 PSNR/HVS 只作参考不判红）。

| 编码器 | 质量值 | VMAF | ΔVMAF | 判定 | ΔPSNR | ΔPSNR-HVS | ssim | xpsnr |
|---|---|---|---|---|---|---|---|---|
| `libx265` | crf 21 | 99.318 | **+0.183** | ✅ | +0.710 ⚠ | +0.412 | 0.9823 | 38.232 |
| `libvpx-vp9` | crf 26 | 99.771 | **+0.636** | ✅ | +0.811 ⚠ | +0.437 | 0.9806 | 38.227 |
| `libaom-av1` | crf 26 | 99.320 | **+0.185** | ✅ | +0.223 | −0.415 | 0.9796 | 37.231 |
| `libsvtav1` | crf 29 | 99.423 | **+0.287** | ✅ | +0.082 | −0.545 ⚠ | 0.9794 | 37.187 |
| `librav1e` | crf 64 | 99.617 | **+0.482** | ✅ | +1.021 ⚠ | +1.002 ⚠ | 0.9833 | 38.313 |

**最大 |ΔVMAF| = 0.636**（`libvpx-vp9`），距门限 1.0 尚有余量。

5 项 ⚠ 是参考指标（`|ΔPSNR| ≤ 0.3` / `|ΔPSNR-HVS| ≤ 0.5`）超界，
**不影响退出码**。这是设计如此，不是回归。

### SKIP 的 3 项（硬编，本机无 GPU）

`h264_nvenc` / `hevc_nvenc` / `av1_nvenc` 走「表覆盖 + 本机实编探测」双重判据
（`verify_equal_quality.py:93-102`），探测到 `-22 (Invalid argument)` ⇒ SKIP 而非判红。
**这三档至今无 CPU 侧数据**，须上机（已列入 `Plan/PROMPT_GPU侧继续测试.md` §8）。

## 可复现性

同日两次独立运行（间隔约 12 分钟）**逐项数值完全一致**：
ΔVMAF / ΔPSNR / ΔPSNR-HVS / ssim / xpsnr 全部逐位相同。
单次耗时 11 分 39 秒（180 帧 × 5 档 VMAF 测量）。

## ⚠ 这份记录**不能**为表值背书

README:1659 已写明：`verify_equal_quality.py` 跑的是**单素材 in-sample**
（`new5_raw` crf21 就在标定集内），**结构上测不到跨素材问题** ——
它是「表改动后没有把软编档改坏」的回归门，不是「表值正确」的判据。
落表的前置门禁是 **LOO**（留一交叉验证）。

这条绿灯能证明的只有：**当前 `QUALITY_MAP` 的 5 个软编行，在单素材口径下
仍能复现标定时测到的质量水平**。

## 原始产物位置

- 帧级 `vmaf.json`（118 KB，180 帧 × {vmaf, psnr_hvs, vif, adm2}）——
  `Accessory/temp/verify_equal_quality/`，**gitignored**（含 libvmaf 版本戳，
  换机器即失效，见 `Accessory/archive_manifest.md` 的同类判据）
- 编码样本 `libx265_21.mp4` 等 5 个 —— 同上目录，未入库
