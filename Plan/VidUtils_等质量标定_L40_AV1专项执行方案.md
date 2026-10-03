# VidUtils 等质量标定 · L40（AV1）专项执行方案

> **适用机器**：NVIDIA **L40 / L4 / RTX 40 系（Ada 及以上 NVENC）**
> —— 唯一能编 **`av1_nvenc`** 的硬件；**T4 编不了 AV1**（`error code -22 Invalid`）。
> **范围（**只针对 AV1**）**：`av1_nvenc` 的 **`-cq` 等质量标定**，外加 AV1 的 constqp `-qp` 尺度
> 回归复核、AV1 硬编等质量门禁解锁、AV1 真机长视频验证。
> **不做**：`h264_nvenc` / `hevc_nvenc`（**以 T4 方案为准**，见
> `Plan/VidUtils_等质量标定_T4专项执行方案.md`）；QSV/AMF/VideoToolbox（另机）。
>
> **孪生约定**：`vidcrop_cpu_v2.py` / `vidcrop_hwaccel.py` 共享参数逐字一致；
> `convert_crf.py` 与 VE `src/utils/convert_crf.py` 的两表**逐字同步**。
> **姊妹文档**：`Plan/PROMPT_等质量换算立项.md`（B2/B3）、
> `Plan/VidUtils_质量控制参数修复方案.md`（**§4.10 L40 交接步骤**、§4.12、V13）、T4 专项方案。
> **门禁**：`verify/verify_equal_quality.py`（主门禁 `|ΔVMAF| ≤ 1.0`）、
> `verify/verify_quality_mapping.py` ⑨ 组。
> **上机探针**：`probe/verify_nvenc_quality_gpu.py --expect-av1`（**必须加**）、`probe/t4_acceptance.py`。
>
> **状态（2026-10-03）**：
> - AV1 的 **`-cq` 偏移方向**已由 §4.10 实测 **PASS**（表值 `-cq 27`：ΔPSNR +1.74 dB / 码率 1.12×）——
>   但那是**单点、与等体积表的比对**，**`-cq` 的等质量全表仍未标定**。
> - AV1 的 **`-qp` 尺度**已由 §4.10 定为 **`_QP_SCALE['av1_nvenc'] = 3`**（扩扫 42/63/72/84/105/108，
>   ×3(qp=63) 落带内）—— **勿改回 4**。
> - 本方案的**新增工作**是 `av1_nvenc` 的 **VMAF 等质量表**（写入 `QUALITY_MAP`），
>   以及 `-qp` 尺度在新表值下的**回归复核**。**全部未开始**（G3/G4）。

---

## 0. 一句话现状与目标

- `QUALITY_MAP` 只覆盖 5 个软编；`av1_nvenc` 经回退读 **`SIZE_MAP`**（`(1.0, 6.0, 0, 63)`，等体积）。
- **目标**：为 `av1_nvenc` 产出 **等质量（VMAF 定标）**行 `(a, b, 0, 63)`，写入两仓 `QUALITY_MAP`，
  解除 `SIZE_MAP` 回退；并在新表值下**回归复核** `to_constqp_qp()` 的 `_QP_SCALE=3`。
- **关键前置与 T4 方案共用**：扩展 `probe/calibrate_equal_quality.py` 支持 NVENC（任务 G0/T0）。
  **L40 上还要额外满足 AV1 前提**：`--expect-av1` 的 fail-fast 必须通过（见阶段 0）。
- ⚠ **AV1 的量程与轴**（与 h264/hevc 不同，务必记住）：
  - `av1_nvenc` 的 **`-cq` = 0~63**（AV1 qindex 尺度，**不是** 0~51）；
  - `av1_nvenc` 的 **`-qp` = -1~255**（qindex），与 x264 QP **不同刻度** ⇒
    constqp 必须走 QP 尺度层（`_QP_SCALE`），**不能**拿 CQ 轴值直发。

---

## 1. 本方案在「GPU 待办总清单」中的位置

| 编号 | 待办 | 硬件 | 归属 | 状态 | 本方案是否覆盖 |
|---|---|---|---|---|---|
| G0 | 扩展 harness 支持 NVENC | 任意有 N 卡 | 两仓同源 | 未开始（**共用前置**） | ✅ 阶段 1（细节同 T4 方案） |
| G1/G2 | h264/hevc_nvenc `-cq` 等质量 | T4 | 两仓 | 未开始 | ❌ 见 T4 方案 |
| **G3** | **`av1_nvenc` 的 `-cq` 等质量标定** | **L40/Ada** | 两仓 | 未开始 | ✅ **本方案主体** |
| **G4** | **AV1 constqp `-qp` 尺度回归复核** | **L40/Ada** | VE 主 / 本仓复核 | 部分（×3 已定） | ✅ 本方案阶段 5 |
| **G5** | AV1 硬编等质量门禁解锁 | L40 | 本仓 | 未开始 | ✅ 本方案阶段 6 |
| **G6** | AV1 真机长视频验证 | L40 | 两仓 | 未开始 | ✅ 本方案阶段 7 |
| G7 | 落点/运行期/无损回归复核 | T4（L40 可选） | 本仓 | 部分已做 | ⚠ 可选 |

> ⚠ **T4 与 L40 不可互替**：L40 虽也能编 h264/hevc，但二者 NVENC 代际不同。
> **h264/hevc 的等质量行以 T4 标定为准**；L40 上若跑 h264/hevc 只为**跨代交叉核对**，
> 不得直接覆盖 T4 的表值（除非产品确定只跑 Ada）。

---

## 2. L40（AV1）专项待办

| 编号 | 待办 | 交付 | 前置 | 判据 |
|---|---|---|---|---|
| **A0** | 复用 G0：harness 支持 NVENC + `--expect-av1` fail-fast | harness 可跑 `av1_nvenc` | 有 Ada 卡 | `--expect-av1` 不报 exit 2 |
| **A1** | `av1_nvenc` 的 `-cq` 等质量标定（多素材 + LOO） | `QUALITY_MAP['av1_nvenc'] = (a,b,0,63)` | A0 | 池化 `max|ΔVMAF| ≤ 1.0`；LOO 达标 |
| **A2** | AV1 constqp `-qp` 尺度回归（`_QP_SCALE=3`） | 探针结论行 | A1 落表后 | `C-av1-结论` PASS（仅 ×3 落带） |
| **A3** | AV1 硬编等质量门禁解锁 | AV1 条目从 SKIP 变实测 | A1/A2 | 硬编 `|ΔVMAF| ≤ 1.0` |
| **A4** | AV1 真机长视频验证 | 长片报告 | A1 | 见立项 B4 |
| **A5** | 报告归档 + 与 T4 报告对比 | `verification_report/*L40_<TS>.*` | 全程 | A 组与 T4 一致；多出 av1 格 |

---

## 3. 执行步骤（可直接照抄）

### 阶段 0 · 上机前置自检（必须过）

```bash
# 1) 必须是 Ada 及以上（L40 / L4 / RTX 40 系）
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv

# 2) av1_nvenc 的选项与量程
ffmpeg -hide_banner -h encoder=av1_nvenc | grep -E '\-(cq|qp)'
#    期望：-cq (0 to 63)、-qp (-1 to 255)

# 3) ⚠ 关键：加 --expect-av1 实编一次短探测（不是只看 -encoders 列表）
python3 probe/verify_nvenc_quality_gpu.py --expect-av1 --src '<素材>'
#    ✓ 「--expect-av1：av1_nvenc 可编 ⇒ B/C 组会跑满」 ⇒ 继续
#    ✗ 「[ERROR] --expect-av1 未满足 …」+ exit 2 ⇒ 本卡不是 AV1 卡，**停**，别继续

# 4) 指纹（标定与门禁锁同一 ffmpeg；Ada 的 ffmpeg 版本可能与 T4 机不同）
ffmpeg -hide_banner -version | head -1

# 5) 素材池（与 CPU 表同池；见 T4 方案 §7 R1）
ls /workspace/input_videos/eqq_calib/ 2>/dev/null || echo '缺 eqq_calib 切片'

# 6) ⚠ 并发负载（Ada 机器可能跑 TensorRT 流水线，只体现在 utilization.gpu）
pgrep -af 'main_video_optimized|ffmpeg' || echo '无并发进程'
nvidia-smi --query-gpu=utilization.gpu,utilization.encoder,utilization.decoder --format=csv
```

> `--expect-av1` 是**显式 opt-in 的 fail-fast**：不加它，非 AV1 卡上 B/C 的 av1 格**静默 SKIP
> 且退出码仍 0**（极易误判成「跑绿了」）。**本方案一律加。**

### 阶段 1 · 扩展 harness（A0，与 T4 方案 T0 同源）

改动清单与 T4 方案 §3 阶段 1 **完全相同**，AV1 额外要点：

| 位置 | AV1 特有 | 说明 |
|---|---|---|
| `SWEEP` | 增 `'av1_nvenc': [12, 18, 23, 27, 31, 36, 41, 47, 54, 63]` | **0~63** 量程；低端够低覆盖锚点 VMAF，高端到 63 |
| `QUALITY_FLAG` | `'av1_nvenc': '-cq'` | CQ 轴 |
| `BASE_LOCK` | `['-b:v','0','-preset','p4','-rc','vbr']` | 锁定 `-preset p4`（CR-1，与 VE 一致）+ **av1 显式 `-rc vbr`**（CR-2，与 VE 的 `-rc:v vbr` 同形式） |
| range 回退 | `SIZE_MAP['av1_nvenc'][2:4] = (0,63)` | 首次标定时 `QUALITY_MAP` 无该行，防 KeyError |
| 可用性探测 | 实编探测 + `--expect-av1` fail-fast | **T4 上此档必 SKIP；L40 上必须通过** |

```bash
python3 probe/calibrate_equal_quality.py --selftest
#   ⚠ 若 harness 加了 AV1 档位相关的纯函数，必须补进 selftest（参照探针做法）
```

### 阶段 2 · 小样门禁（单素材）

```bash
python3 probe/calibrate_equal_quality.py \
    --src /workspace/input_videos/new5_raw.mp4 \
    --duration 6 --codecs av1_nvenc \
    --workroot /tmp/eqq_gpu --tag l40_smoke
#   期望：av1_nvenc 给出 a/b；单素材 dVMAF ≤ 1.5
```

### 阶段 3 · 全量标定（多素材 + 断点续跑）

素材口径与 CPU 表一致：同一 prep、锚点 `18/21/24/27/30`、`n_subsample=1`、同 duration。

```bash
python3 probe/calibrate_equal_quality.py \
    --src <A> --src <B> ... \
    --duration 6 --codecs av1_nvenc \
    --workroot /tmp/eqq_gpu --tag l40_720p_6s --resume < /dev/null
#   时间不够时先出单口径首版，报告标注「待补 10s 侧」
```

**LOO 留一**：

```bash
python3 probe/loo_equal_quality.py \
    --workroot /tmp/eqq_gpu --tag l40_720p_6s [--tag l40_720p_10s] \
    --tiers av1_nvenc --tol 1.0
#   期望：worst ΔVMAF < 1.0；结构性超标则记录 + 申请分档门禁
```

> ⚠ **AV1 的 VMAF 曲线更易在低码率端/平涂内容出现非单调平台**：harness 的
> `pava_nonincreasing` + `interp_iso`（取最低参数解）会自动处理；若出现 `insufficient_points`，
> 说明扫描点未覆盖锚点 VMAF，扩扫描。

### 阶段 4 · 落表 + 两仓同步 + ⑨ 组

```python
# 两仓 convert_crf.py（本仓 + VE src/utils/convert_crf.py，逐字相同）
'av1_nvenc': (a, b, 0, 63),      # (lo,hi) 是 **CQ 轴** 量程，不是 -qp
```

```bash
python3 verify/verify_quality_mapping.py     # ⑨ 组跨仓逐条相等必须全绿
```

> ⚠ 同步更新：方案 §4.12 表、立项 M5、`convert_crf.py` 中 `av1_nvenc` 行的注释
> （记 GPU 名 / 驱动 / ffmpeg 指纹 / 素材池）。

### 阶段 5 · AV1 constqp `-qp` 尺度回归（A2）

**背景**：`_QP_SCALE['av1_nvenc']` 已由 L40 扩扫定为 **3**（勿改回 4）。但**落新 `-cq` 等质量表后**，
`to_constqp_qp()` 的输入基准轴值会变 ⇒ 需**回归复核**当前尺度在新表值下仍成立。

```bash
python3 probe/verify_nvenc_quality_gpu.py --expect-av1 \
    --src '<>=10s、720p+ 真实素材>' \
    --json verification_report/nvenc_quality_L40_$(date +%Y%m%d_%H%M%S).json \
    --md   verification_report/nvenc_quality_L40_$(date +%Y%m%d_%H%M%S).md
#   看 C-av1-结论：
#     PASS（仅 ×3(63) 落带） ⇒ _QP_SCALE=3 成立，无需改动
#     WARN/FAIL            ⇒ 按结论行动作（见 §4 判读矩阵），改尺度须两脚本同步 + 回跑⑨组
```

> 本仓（VidUtils）**无独立 QP 轴表**：`to_constqp_qp()` = 基准轴值 × `_QP_SCALE`，
> 故本阶段只做回归复核。**VE 侧另需 `QUALITY_MAP_QP`（QP/constqp 轴等质量表）**，
> 属 VE 交付物，不在本仓范围。

### 阶段 6 · AV1 硬编等质量门禁解锁（A3）

`verify/verify_equal_quality.py` 的 `SOFT`（`:34`）只列软编 ⇒ AV1 天然 SKIP。落表后：

- 让 `av1_nvenc` 进入可选编码器集（**按 `QUALITY_MAP` 是否覆盖 + 本卡是否可编**决定是否测）；
- `C.encode`（T0 已支持 nvenc 的 `-cq`）直接复用；门禁口径**不变**（只 `ΔVMAF` 判红）。

```bash
python3 verify/verify_equal_quality.py --src <素材> --duration 6 \
    --codecs av1_nvenc < /dev/null
#   期望：AV1 条目由 SKIP 变实测，主门禁达标
```

### 阶段 7 · AV1 真机长视频 + 归档（A4/A5）

```bash
# a) 真机长视频（生产管线实跑 AV1 链；判据见对侧真机长视频验证 Prompt）
# b) 与 T4 报告对比、归档
#    T4 报告：verification_report/nvenc_quality_T4_20260928_062328.{json,md}
#    期望：A 组完全相同；h264/hevc 对照可复现；额外多出 av1 三格 + 两条结论行
```

---

## 4. 判读矩阵

| 上机现象 | 结论 / 动作 |
|---|---|
| `A-av1_nvenc` 池化 `max|ΔVMAF| ≤ 1.0` 且 LOO 达标 | 落表，解除 `SIZE_MAP` 回退 |
| LOO 结构性超标 | 先排除执行错误，确认后**记录 + 申请分档门禁**（参照 CPU 侧先例） |
| `--expect-av1` 报 exit 2 | **本卡不是 Ada**，停；AV1 标定必须上 L40/Ada |
| `C-av1-结论` = PASS（仅 ×3(63) 落带） | `_QP_SCALE['av1_nvenc']=3` 成立，**无需改动**（勿改回 4） |
| `C-av1-结论` = WARN（仅 ×4 落带） | 兼容旧表；扩扫确认后再定 |
| `C-av1-结论` = WARN（仅 ×5 / 仅 21 落带） | 按结论行动作；改尺度须两脚本同步 + 回跑 ⑨ 组 |
| `C-av1-结论` = FAIL（均不落带） | 扩扫（42/63/126）或按实测码率重标 `QUALITY_MAP['av1_nvenc']` |
| `B-av1-结论` = FAIL | 表值未落带 ⇒ 重标 b（两仓同步 + 回跑 ⑨ 组） |
| 探针 av1 格 SKIP | **只应出现在非 Ada 卡**；加 `--expect-av1` 后绝不该静默 SKIP |
| L40 上 §4.1 的假红 | 与 T4 相同（有 GPU ⇒ 4 处 verify + 2 个 `dump_cmd_full` 用例），非本方案回归 |

---

## 5. 交付物清单

1. `probe/calibrate_equal_quality.py` —— NVENC + AV1 支持（A0，与 T4 共用）。
2. 两仓 `convert_crf.py` 的 `QUALITY_MAP` —— 新增 `av1_nvenc` 行（逐字同步）。
3. `verify/verify_equal_quality.py` —— AV1 条目解锁。
4. 报告：`/tmp/eqq_gpu/<tag>/report.json`、`verification_report/nvenc_quality_L40_<TS>.{json,md}`。
5. 文档：本方案 + 立项 M5 状态 + 方案 §4.10/§4.12 更新；工程记忆 `memory/` 更新。

---

## 6. 沿用的优秀做法（照抄，不要重新发明）

> 与 T4 方案 §6 同源；AV1 相关项已加粗。

1. **口径唯一 + 同轴**：主门禁**只**用 VMAF；PSNR/SSIM/XPSNR 独立滤镜、PSNR-HVS 用 libvmaf feature。
2. **`n_subsample=1` 铁律**（>1 偏置 VMAF 且随编码器而异）；标定与判据同参、同时长。
3. **同二进制指纹**；换 ffmpeg 构建须重标。
4. **无缓存 + `--resume` + md5 审计 + 每素材独立 workdir**。
5. **PAVA 保序 + 等 VMAF 插值取最低参数解**（**AV1 尤其需要**，低码率易非单调）。
6. **多素材聚合（a 池化最小二乘 / b 中位数）+ LOO 留一**。
7. **`--expect-av1` 显式 opt-in fail-fast**：防「静默 SKIP 被当成已验 AV1」。
8. **空集守卫**：一个「有结论」的格都没有时不能报绿。
9. **结论行直接给可执行动作**（`B-av1-结论` / `C-av1-结论`）。
10. **两段式验收**：dry-run 落点 + 真转码运行期。
11. **探针期望值滞后 ≠ 产品回归**（先比对 §4.5 命令）。
12. **报告归档带 GPU 名 + 时间戳**，与 T4 报告可对比。
13. **改表必回跑 ⑨ 组**；**改 `_QP_SCALE` 要两脚本同步 + 复核 `_QP_LIMITS`**。
14. **上机前查并发负载**（Ada 机 TensorRT 只体现在 `utilization.gpu`）。

---

## 7. 风险与坑

- **R1 · 非 Ada 卡误跑**：不加 `--expect-av1` 时非 AV1 卡静默 SKIP 且 exit 0 ⇒ 误判「AV1 已验」。
  **本方案强制加 `--expect-av1`**。
- **R2 · `_QP_SCALE` 改回 4**：§4.10 已定为 **3**，勿改回（改尺度须两脚本同步 + 回跑 ⑨ 组）。
- **R3 · `(lo,hi)` 是 CQ 轴量程**：`av1_nvenc` 写 `0~63`；**不能**拿它夹 `-qp`（`_QP_LIMITS` 才是 QP 轴）。
- **R4 · 素材池/时长效应/指纹**：同 T4 方案 §7 R1/R2/R3。
- **R5 · L40 上 `verify/` 假红**：与 T4 相同（有 GPU 的环境假设过时），非本方案回归。
- **R6 · §4.1 门禁须在有 `ffmpeg` + `temp/fixture_1080p.mp4` 的机器复跑**：本开发 checkout 缺 fixture 时 ⑦ 组即崩。
- **R7 · `-b:v` 语义**：AV1 NVENC 同样 `-cq N` 无视 `-b:v`；标定用 `-cq N -b:v 0`，别加 `-maxrate`。

---

## 附录 · 命令速查

```bash
# 前置（必须通过 --expect-av1）
nvidia-smi --query-gpu=name,driver_version --format=csv
ffmpeg -h encoder=av1_nvenc | grep -E '\-(cq|qp)'      # 期望 -cq 0~63 / -qp -1~255
python3 probe/verify_nvenc_quality_gpu.py --expect-av1 --src '<素材>'

# 标定
python3 probe/calibrate_equal_quality.py --selftest
python3 probe/calibrate_equal_quality.py --quick --src <素材> --codecs av1_nvenc
python3 probe/calibrate_equal_quality.py \
    --src <A> --src <B> --duration 6 --codecs av1_nvenc \
    --workroot /tmp/eqq_gpu --tag l40_720p_6s --resume < /dev/null

# LOO
python3 probe/loo_equal_quality.py --workroot /tmp/eqq_gpu --tag l40_720p_6s \
    --tiers av1_nvenc --tol 1.0

# 落表后门禁
python3 verify/verify_quality_mapping.py
python3 verify/verify_equal_quality.py --src <素材> --duration 6 --codecs av1_nvenc < /dev/null

# 上机探针（看 B/C 两条 av1 结论行）
python3 probe/verify_nvenc_quality_gpu.py --expect-av1 --src <素材> \
    --json verification_report/nvenc_quality_L40_$(date +%Y%m%d_%H%M%S).json \
    --md   verification_report/nvenc_quality_L40_$(date +%Y%m%d_%H%M%S).md
```
