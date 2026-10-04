# CR-4 Handoff · av1_nvenc CONSTQP 的 QP 轴口径（VE → VU）

- 日期：2026-10-04
- 发起方：Video_Enhancement（VE）L40 等质量标定专项
- 接收方：VidUtils（VU）
- 依据：`Plan/PROMPT_L40_AV1等质量标定专项执行方案.md` §6 / §12（CR-4）
- 状态：**VE 已落表并同步门禁；请 VU 复核其对侧 QP 尺度层**

---

## 1. 背景（两仓共享 CQ 行 —— **2026-10-04 更新：需 VE→VU 同步新值**）

av1_nvenc 的 **CQ 轴**原两仓同为 `(1.4573, 1.1022, 0, 63)`。VE 侧在规范化标定
（`points/gpu_l40_cq`，17 素材，判据锚点 [0,27] → LOO 3.13）后**落表为新值**：

```
QUALITY_MAP['av1_nvenc'] = (1.4566, 1.2165, 0, 63)   # VE 已落（crf21 → -cq 32，与旧值同）
```

- 两值在 ref21 **同为 `-cq 32`**（31.805 vs 31.706），功能无差异；
- 但 `to_constqp_qp` 往返后 **QP 期望由 71 → 70**（`to_x264_crf(32)` 21.13 vs 21.20）；
- 旧值 `(1.4573, 1.1022)` 无法由任何 VE 池组合复现（非本仓规范化产出）。
  ⇒ **请 VU 将 `QUALITY_MAP['av1_nvenc']` 同步为 `(1.4566, 1.2165, 0, 63)`**，恢复跨仓 ⑨ 逐字相等。

## 2. 分叉点：QP 轴（constqp 的 `-qp`）

| | VE（本轮） | VU（现状） |
|---|---|---|
| 模型 | **仿射** `QP = 7.9338·crf − 97.5136`（0~255） | `_QP_SCALE['av1_nvenc'] = 3`（乘性） |
| 落点 | `QUALITY_MAP_QP['av1_nvenc']`（quality 口径优先） | 尺度层（`_QP_SCALE`，见 VU `Plan/VidUtils_质量控制参数修复方案.md` V13） |
| 证据 | 17 素材 × 锚点 18/21/24/27/30，LOO **3.64** | **仅 ref21 单锚点**扩扫 42/63/72/84/105/108 |

### 差异随基准轴放大（关键）

| 基准 crf | 素材自解等质 QP（17 素材中位） | VU ×3 | VE 仿射 | ×3 残差 |
|---|---|---|---|---|
| 18 | 44.7 | 54 | 45 | +9.3 |
| 21 | 65.7 | 63 | 69 | −2.7 |
| 24 | 92.3 | 72 | 93 | **−20.3** |
| 27 | 117.9 | 81 | 117 | **−36.9** |
| 30 | 140.8 | 90 | 141 | **−50.8** |

⇒ VU 的 ×3 只在 crf≈21 附近近似成立；**crf≥24 起系统性欠配**（crf30 用 ×3 会发 `-qp 90`，而等质需 ≈141）。

### 落带性一致（不矛盾）

VU C 组在 ref21 测 `-qp 63` ΔPSNR −1.14 落带内；VE 在 ref21 用仿射给 69，同带内（带宽 ±1.5 dB）。两仓在 ref21 **并不冲突**，分叉只在高 ref。

## 3. 请求 VU 的动作

1. **复核 VU 侧 QP 尺度层**：把 C 组扩到 **ref 24/27/30** 各扫一个 `-qp`（如 93/117/141），确认 ×3 在这些锚点是否超带；
2. 若确认欠配，**同步 VE 的仿射模型**（或至少记录 CR-4 已知分叉）；
3. 回填 `Plan/VidUtils_等质量标定_L40_AV1专项执行方案.md` 的 C 组结论（当前写「×3 成立、无需改动」仅覆盖 ref21）。

## 4. VE 侧已落地内容（供对照）

- `src/utils/quality_map.py`：`QUALITY_MAP_QP['av1_nvenc'] = (7.9338, -97.5136, 0, 255)`；
- `src/utils/convert_crf.py`：`QUALITY_MAP['av1_nvenc'] = (1.4566, 1.2165, 0, 63)`（本文件 §1 请求 VU 同步）；
- `Accessory/verify/crf_cq_unification_verify.py`：G3-7（CQ32→QP70）、G6-7/8（`-qp 70`）、G1-2 av1 `-cq:v 32`；
- 门禁复核：`crf_cq --quick --no-gpu` **104/0/0/11**、`plan_implementation_gate` 无失败；
- AC1 探针：表值 `-qp 70` 落带内 **PASS**；
- 标定报告：`Accessory/data/eqq_calibration/reports/av1_nvenc_L40_calibration_report_20261004.md`。

> ⚠ 已知边界：标定切片为 **720p prep**，生产/G7 在全分辨率上量测，映射非分辨率不变量
> （VE G7-6 在 word_world_2 全分辨率下 cq32 ΔPSNR −2.14 WARN，而 VU 在 720p 切片
> live_texture_frog 下 cq32 −0.34 PASS）—— 属内容/分辨率差异，非表错。
