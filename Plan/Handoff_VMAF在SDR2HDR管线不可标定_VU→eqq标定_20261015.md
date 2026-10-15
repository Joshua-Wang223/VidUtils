# Handoff · VMAF 在 SDR→HDR 管线中不可用于等质量标定（VU 测试侧 → 等质量标定/quality-tier 负责人）

- 日期：2026-10-15
- 发起方：VidUtils（VU）SDR→HDR 全流程测试（`Plan/SDR_HDR_全流程测试立项_Prompt.md` §4.4.1 C8）
- 接收方：`Accessory/calib/eqq_evidence/q3_bench/` 等质量标定 / quality-tier 方案负责人
- 依据：`bench_sdr_to_hdr.py` 的 `--vmaf` 质量门禁实测（C8）
- 状态：**⚠ 请勿把本发现外推到你们的 SDR 直编标定路径 —— 你们的做法是正确的**（详见 §3）

---

## 1. 起因

`bench_sdr_to_hdr.py --layers l1 --vmaf …` 跑出的门禁：

| 组合 | VMAF（参照 = libx265@crf-ref21） | 门禁 |
|---|---|---|
| l1.libx265 | REF 100.00 | 参照池 |
| l1.libsvtav1 | 95.50 | ⚠ −4.50 |
| l1.libaom-av1 | 96.40 | ⚠ −3.60 |
| l1.librav1e | 96.69 | ⚠ −3.31 |

三个 AV1 软编全部低于 libx265 3.3~4.5 点。**注意：这里的参照物是「基准里第一个 OK 组合」，
即 libx265 自己**—— 这是跨编码器互比，锚点天然偏袒 HEVC。

## 2. 决定性实验：差距无法用质量档位关闭

（`Accessory/probe/calibrate_av1_vmaf.py` + `probe_vmaf_floor.py`，打分复用 bench 自己的
`_tonemap_to_8bit` / `vmaf`，与门禁逐字同口径；生产同一条管线：agcm 推理→PQ/BT.2020→编码）

| 编码器 | 原表值 | 扫描（体积→VMAF） | 近无损档 | 体积/参照 | VMAF |
|---|---|---|---|---|---|
| libsvtav1 | crf29 (95.50) | 253K→95.46 / 315K→95.71 / 375K→95.80 | **crf12** | **2.96×** | **96.12** |
| libaom-av1 | crf26 (96.40) | 239K→96.11 / 270K→96.21 / 319K→96.32 | **crf10** | **2.21×** | **96.60** |
| librav1e | qp64 (96.69) | 293K→96.45 / 335K→96.51 / 402K→96.59 | **qp20** | **3.45×** | **96.69** |

**码率拉到 2.2~3.5 倍（近无损），VMAF 只涨 0.2~0.6 分，天花板卡死在 ~96。**

## 3. 归因：不是素材，是**管线口径**（已用交叉对照排除素材因素）

`Accessory/probe/crossctrl_material_vs_pipeline.py`。斜率 = VMAF 对 crf 的响应（分/crf）：

| # | 素材 | 管线 | 参照 | 斜率 |
|---|---|---|---|---|
| 1 | **你们那批** | SDR 直编 | 源 | **+0.150** ← 健康 |
| 2 | 我的 test1.mp4 | SDR 直编 | 源 | +0.055 |
| 3 | 我的 test1.mp4 | SDR→HDR | libx265 | +0.040 |
| 4 | **doc_grassland（你们那批）** | SDR→HDR | libx265 | **−0.020** |
| 5 | **cganim_wordworld（你们那批）** | SDR→HDR | libx265 | **−0.008** |
| 6 | **live_kids_play（你们那批）** | SDR→HDR | libx265 | **−0.051** |

**行 1 vs 行 4~6 是关键**：素材完全换成你们的，斜率照样塌成 0（甚至微负）⇒
**压平由「SDR→HDR 管线 + 跨编码器锚」造成，与素材无关。**

两个叠加成因：

1. **跨编码器锚（主因）**：`quality_gate` 拿「第一个 OK 组合」当唯一锚。VMAF 在 tonemap 后的
   SDR 域评估，对不同失真形态打分不同 ⇒ **用 HEVC 当锚天然偏袒 HEVC**，对 AV1 系有
   **~3~4 分系统性负偏**。
2. **PQ→tonemap 压平**：`bench_sdr_to_hdr.py` 自己的 `_tonemap_to_8bit` docstring 早已预警
   「PQ 码值域上跑 VMAF 不报错但**曲线压平**（实测 crf 20~32 只差 1.6 分），判据分辨率不足」。
   本实验是该预警的**定量确认**。

## 4. ⚠ 明确不适用于你们（重要，避免误伤）

**你们的 SDR 直编标定路径完全没问题，不要改**：

| 项 | 你们的做法 | 评价 |
|---|---|---|
| 参照物 | **原始素材**（`run_cell.sh:34` `-i "$IN"`） | ✅ **正是正确做法**——公共锚，不是互比 |
| 斜率 | 7 个素材一致 **+0.15 分/crf** | ✅ 健康，可用于最小二乘拟合 |
| 方法 | 「参数→VMAF 曲线上取等 VMAF 点」 | ✅ 口径正确 |

你们 v3 方案 §里写的 `QUALITY_MAP = 等质量（目标编码器「参数→VMAF」曲线上取等 VMAF 点后最小二乘）`
**方向正确**，且素材库覆盖三维动画 / 实拍纪录片 / 实拍剧集 / 屏幕 UI 多类别，方法学比我这侧严谨得多。

**本发现唯一的作用是划一条边界**：
> **SDR 直编标定得到的表，不要直接搬到 SDR→HDR 管线上用 VMAF 再验证/再调参。**
> HDR 管线的 VMAF 门禁没有判据分辨率，调它只会白烧码率。

## 5. 我们这侧的行动（已落地）

- **等质量表 `QUALITY_MAP` 保持不动**。它按**等体积**口径标定
  （`calibrate_soft_offsets.py` 方法说明自述「⚠ 等体积 ≠ 等质量」），仍是合理生产折中；
  为追 VMAF 调表 = 3 倍码率换 0.3~0.6 分，**明确不做**。
- 生产选型**优先 libx265**，理由是**元数据能力**而非 VMAF：libx265 能写
  mastering display / MaxCLL，而 libsvtav1 / libaom-av1 / librav1e 实测**均写不了**。
- 若日后确需 HDR 管线的等质量标定，正确做法是**换参照而非换参数**：以**公共近无损锚**
  （源片本身，或所有编码器都远高于目标的档位）衡量各编码器「相对同一锚」的距离；
  或改用 HDR 域直接指标（PQ 码值上的 SSIM / tonemap 后 PSNR），而非 VMAF。

## 6. 交付物（可直接复用/复核）

| 文件 | 用途 |
|---|---|
| `Accessory/probe/calibrate_av1_vmaf.py` | 真 VMAF 重标定器；**复用 bench 的 tonemap/vmaf 保证口径一致**；原生质量值经 `--extra-args` 注入 |
| `Accessory/probe/probe_vmaf_floor.py` | 天花板探针（近无损档） |
| `Accessory/probe/crossctrl_material_vs_pipeline.py` | §3 的素材/管线交叉对照 |
| `memory/av1_equal_quality_vmaf_investigation_20261015.json` | 完整数据与结论 |

**两个坑（省你们时间）**：

1. `--extra-args` 的 flag **必须带连字符**。写成 `--extra-args crf 26`（少个横杠）会让 ffmpeg
   把 `crf` 当成输出文件名 →「Unable to choose an output format for 'crf'」退出码 234，
   **整轮标定静默全失败**。
2. librav1e 无法经 `--crf`/`--qp` 直调，表极粗（`crf 18~21` 只对应 `qp 41/49/56/64` 四档），
   必须 `--extra-args -qp N` 直接注入才能细扫。

## 7. 若你们要复核，一句话版

拿 `doc_grassland_src1280x720.mp4`，用**我的**管线（agcm 模型 → PQ/BT.2020 → 编码 →
tonemap 8bit → VMAF，参照取 libx265@crf21）扫 libsvtav1 的 crf 12/20/26。
**若斜率仍在 ±0.05 以内 ⇒ 确认是管线口径，与素材无关。**（我这边实测 −0.020，已复现）