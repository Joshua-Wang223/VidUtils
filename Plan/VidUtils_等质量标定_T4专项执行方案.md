# VidUtils 等质量标定 · T4 专项执行方案

> **适用机器**：NVIDIA **Tesla T4**（Turing / CC 7.5 / 驱动 580.x，ffmpeg ≥ 7.1 含 libvmaf）
> **范围**：`h264_nvenc` / `hevc_nvenc` 的 **`-cq` 等质量标定**，外加 constqp 回归复核、
> 硬编等质量门禁解锁（去 SKIP）、真机长视频验证。
> **不做**：`av1_nvenc`（**T4 无 AV1 NVENC** ⇒ 见姊妹方案
> `Plan/VidUtils_等质量标定_L40_AV1专项执行方案.md`）；QSV/AMF/VideoToolbox（另机，见 §1 尾注）。
>
> **孪生约定**：`vidcrop_cpu_v2.py` / `vidcrop_hwaccel.py` 共享参数取值表/默认值/报错首行逐字一致；
> `convert_crf.py` 与 VE `src/utils/convert_crf.py` 的 `SIZE_MAP` / `QUALITY_MAP` **逐字同步**。
> **姊妹文档**：`Plan/PROMPT_等质量换算立项.md`（任务划分 B 组）、
> `Plan/VidUtils_质量控制参数修复方案.md`（§4.9 T4 实测、§4.12 等质量表）、L40 专项方案。
> **门禁**：`verify/verify_equal_quality.py`（主门禁 `|ΔVMAF| ≤ 1.0`）、
> `verify/verify_quality_mapping.py` ⑨ 组（跨仓两表逐条相等）。
> **上机探针**：`probe/verify_nvenc_quality_gpu.py`、`probe/t4_acceptance.py`。
>
> **状态（2026-10-04）**：**T4 上机执行完毕，h264/hevc_nvenc 的 `-cq` 等质量行已落表两仓**。
> 前置（harness GPU 支持 / `--require-codecs` / `_table_range` 回退；跨仓契约 CR-1 preset p4 /
> CR-2 rc 显式 `vbr`；门禁与基线同步）全部就绪并复核通过。
> **上机实测结论**（Tesla T4 / 驱动 580.65.06 / ffmpeg 9.0.2，素材池 `input_videos/eqq_calib`
> 17 条 = 12×6s + 5×10s，锚点 18/21/24/27/30，`n_subsample=1`）：
> - 池化表（a=最小二乘 / b=各素材中位数）：`h264_nvenc=(0.9295, 6.2523)`、`hevc_nvenc=(1.1116, 2.1606)`，量程均 0~51；crf21→`-cq 26`。
> - LOO worst：**h264 3.97 / hevc 5.85** ⇒ 超立项 `<1.0`（与 CPU 软编结构性上限同源，单素材 dVMAF<0.65、非执行错误），
>   经仓主裁定按 **分档门禁 ≤5.9**（CPU 先例）判**达标**，两行已写入 VidUtils + VE `convert_crf.py`（逐字相等，⑨ 组 14/14 绿）。
> - 硬编门禁解锁：`verify_equal_quality.py` 默认 7 档全绿（h264 ΔVMAF=−0.044 / hevc +0.135，硬编由 SKIP 变实测）。
> - T3/T5/T6 探针与归档见 `verification_report/`：`verify_nvenc_quality_gpu.py` **PASS 15 / WARN 3 / FAIL 0 / SKIP 2**
>   （B 组表值 h264/hevc `-cq 26` 落带、C 组 h264/hevc `-qp 21` 落带、av1 SKIP）；`t4_acceptance.py` **20/20 通过**；
>   长片（`Earth at Night` 30s 4K）门禁 h264 `ΔVMAF`=+0.115 / hevc +0.049。
>   ⚠ GPU 机上 `dump_cmd_full.sh` 因「默认编码器变 h264_nvenc」而红，属 §7 R3 既知**环境假红**（与本次落表无关）。
>   **剩余**：`av1_nvenc`（L40/Ada）、QSV/AMF/VT 另机。

---

## 0. 一句话现状与目标

- 现行 `QUALITY_MAP` **只覆盖 5 个软编**；硬编（NVENC/QSV/AMF/VT）经 `set_quality_mode()` 的
  回退分支读 **`SIZE_MAP`（等体积）** —— 这正是 M5 要解除的部分。
- **目标**：为 `h264_nvenc` / `hevc_nvenc` 产出 **等质量（VMAF 定标）**行，写入两仓 `QUALITY_MAP`，
  使 `get_quality_map()` 不再回退 `SIZE_MAP`。
- **关键前置（必做）**：`probe/calibrate_equal_quality.py` 当前**不支持 NVENC** ——
  `SWEEP`（`:67`）/`QUALITY_FLAG`（`:85`）/`BASE_LOCK`（`:77`）均无 nvenc 条目，
  `main()` 的 `SWEEP[_ffcodec(key)]`（`:589`）、`QUALITY_FLAG[...]`（`:608`）、
  以及聚合段的 `CRF.QUALITY_MAP[_ffcodec(key)][2:4]`（`:649`）都会 **KeyError**。
  故本方案的**第 1 个可执行任务就是扩展 harness（T0）**，不能跳过。

---

## 1. GPU 等质量标定总待办清单（T4 / L40 分列）

> 这是两份专项方案共用的导航表。`本仓` = VidUtils；`两仓` = VidUtils + Video_Enhancement。

| 编号 | 待办 | 硬件 | 归属 | 状态 | 依据 |
|---|---|---|---|---|---|
| **G0** | **扩展 `calibrate_equal_quality.py` 支持 NVENC**（`-cq` 轴 + `-b:v 0` + 可用性探测 + 指纹 + range 回退） | 任意有 N 卡 | 两仓同源 | **已落地**（2026-10-03） | 当前 harness 已支持 nvenc；§3 阶段 1 |
| **G1** | `h264_nvenc` 的 `-cq` 等质量标定 | **T4** | 两仓 | **已落表**（LOO 3.97，≤5.9 分档门禁） | 立项 B1；§3 阶段 3~4 |
| **G2** | `hevc_nvenc` 的 `-cq` 等质量标定 | **T4** | 两仓 | **已落表**（LOO 5.85，≤5.9 分档门禁） | 立项 B1；§3 阶段 3~4 |
| **G3** | `av1_nvenc` 的 `-cq` 等质量标定 | **L40/Ada** | 两仓 | 未开始 | 立项 B2；T4 编不了 |
| **G4** | constqp/QP 轴等质量 | h264/hevc **T4**；av1 **L40** | VE 主 / 本仓复核 | 未开始 | 立项 B3；**本仓无独立 QP 表**，只需复核 `to_constqp_qp()` |
| **G5** | 硬编等质量门禁解锁（`verify_equal_quality.py` 硬编条目去 SKIP） | T4 + L40 | 本仓 | **已解锁**（默认 7 档全绿） | 方案 §4.12 |
| **G6** | 真机长视频验证 + 生产管线 GPU 实跑判据 | T4 / L40 | 两仓 | 未开始 | 立项 B4 |
| **G7** | 落点/运行期/无损回归复核（`-cq`/`-qp` 落点、NVENC 真出片） | T4（L40 可选） | 本仓 | **已复核**（探针 + t4_acceptance） | 方案 §4.9 / §4.10 |

> **T4 与 L40 不可互替**：T4=Turing、L40=Ada，**NVENC 代际不同**，同一 `-cq` 的等质量落点
> 可能不同（尤其 `av1_nvenc` 只有 L40 有）。**h264/hevc 的标定以 T4 为准**；
> 若产品需在 Ada 上跑 h264/hevc，须在 L40 侧做一次**跨代复核**（见 §7 风险 R4）。
> **非 NVENC 硬编**（QSV→Intel 核显机 / AMF→AMD 机 / VideoToolbox→macOS）不在本两份方案内，
> 仍挂在立项 M5，按原计划另机执行。

---

## 2. T4 专项待办（本机）

| 编号 | 待办 | 交付 | 前置 | 预计判据 |
|---|---|---|---|---|
| **T0** | 扩展 `probe/calibrate_equal_quality.py` 支持 NVENC（见 §3 阶段 1 改动清单） | harness 支持 `--codecs h264_nvenc,hevc_nvenc` | 有 N 卡（T4 即可） | `--selftest` 过；`--quick` 单素材跑通 |
| **T1** | `h264_nvenc` 的 `-cq` 等质量标定（多素材 + LOO） | `QUALITY_MAP['h264_nvenc'] = (a,b,0,51)` | T0 | 池化 `max|ΔVMAF| ≤ 1.0`；LOO 达标 |
| **T2** | `hevc_nvenc` 的 `-cq` 等质量标定（多素材 + LOO） | `QUALITY_MAP['hevc_nvenc'] = (a,b,0,51)` | T0 | 同上 |
| **T3** | constqp 回归复核（`-qp` 回基准轴 + 新 `_QP_SCALE` 无涉） | 探针报告 | T1/T2 落表后 | `verify_nvenc_quality_gpu.py` C 组 h264/hevc `-qp 21` 落带 |
| **T4** | 硬编等质量门禁解锁 | `verify_equal_quality.py` 硬编条目从 SKIP 变实测 | T1/T2 落表 | 硬编 `|ΔVMAF| ≤ 1.0` |
| **T5** | 真机长视频验证（生产管线） | 长片报告 | T1/T2 | 见立项 B4 判据 |
| **T6** | 落点/运行期/无损回归复核 + 归档 | `verification_report/*T4_<TS>.*` | 全程 | `t4_acceptance` 通过；探针 PASS |

---

## 3. 执行步骤（可直接照抄）

### 阶段 0 · 上机前置自检（必须过，否则别开始）

```bash
# 1) 卡与驱动
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
#    期望：Tesla T4（不是 L40 就别跑 AV1 相关）

# 2) 编码器与量程（T4 应有 h264/hevc，av1 列表里有但编不了）
ffmpeg -hide_banner -encoders | grep -E 'nvenc'
ffmpeg -hide_banner -h encoder=h264_nvenc | grep -E '\-(cq|qp)'
ffmpeg -hide_banner -h encoder=hevc_nvenc | grep -E '\-(cq|qp)'
#    期望：-cq (0 to 51)、-qp (-1 to 51)

# 3) 度量链（VMAF 是唯一判红口径，必须有）
ffmpeg -hide_banner -filters | grep -i vmaf || echo '无 libvmaf：本方案不可执行'

# 4) ffmpeg 指纹（标定与门禁须锁同一二进制；换构建须重标）
ffmpeg -hide_banner -version | head -1

# 5) 素材池（**关键**：与 CPU 表同池才能逐条可比）
ls /workspace/input_videos/eqq_calib/ 2>/dev/null || echo '缺 eqq_calib 切片：见 §7 R1'
ls /workspace/VidUtils/temp/m2_srcs/ 2>/dev/null || echo '（可选）本仓 7 素材源'

# 6) ⚠ 并发负载：T4 机器常有用户流水线抢资源，测量前必须确认空闲（不 kill）
pgrep -af 'main_video_optimized|ffmpeg' || echo '无并发进程'
nvidia-smi --query-gpu=utilization.gpu,utilization.encoder,utilization.decoder --format=csv
```

> ⚠ 所有脚本命令**一律加 `< /dev/null`**：后台进程组 + tty stdin 下会被 SIGTTOU 整组停住。

### 阶段 1 · 扩展 harness（任务 T0）

`probe/calibrate_equal_quality.py` 的最小改动清单（**只加不动软编路径**，保持既有软编结果逐位不变）：

| 位置 | 现状 | 改为 |
|---|---|---|
| `SWEEP`（`:67`） | 仅 5 个软编 | 增 `h264_nvenc` / `hevc_nvenc` 扫描点（见下） |
| `BASE_LOCK`（`:77`） | 无 nvenc | 增 `'h264_nvenc': ['-b:v','0','-preset','p4','-rc','vbr']`、`'hevc_nvenc': [...]`（**锁定 `-preset p4`（CR-1）与 `-rc vbr`（CR-2）**，均与 VE 一致，否则等效点漂移） |
| `QUALITY_FLAG`（`:85`） | 无 nvenc | 增 `'h264_nvenc': '-cq'`、`'hevc_nvenc': '-cq'` |
| `encode()`（`:191`） | 软解 + `QUALITY_FLAG` | 复用即可（`-cq` + lock 里的 `-b:v 0` 已能把 NVENC 置为恒定质量）；**sw 解码 + `-pix_fmt yuv420p` 对 NVENC 安全**（零拷贝 cuda 管线才禁传 `-pix_fmt`） |
| 可用性探测 | 无 | 仿 `verify_nvenc_quality_gpu.nvenc_usable()`：实跑一次短编码，不可用即把该档标 SKIP（**T4 的 av1 即此情形**）；新增 `--expect-codecs` fail-fast |
| range 查询（`:649`） | `CRF.QUALITY_MAP[key][2:4]` | 改 `CRF.get_quality_map().get(key)` 失败时**回退 `CRF.SIZE_MAP[key][2:4]`**（首次标定时 QUALITY_MAP 尚无 nvenc 行，否则 KeyError） |
| 指纹（`:560`） | 仅 ffmpeg 版本 | 追加 `nvidia-smi` 的 GPU 名 / 驱动、以及各 nvenc 的 `-cq` 量程 |
| 报告 | `report.json` | 追加 `gpu` 字段，供归档 |

**起始扫描点**（低端必须够低覆盖 x264 crf18 的 VMAF，高端够高覆盖 crf30；实际以「5 个锚点都插值命中」为准微调）：

```python
'h264_nvenc': [10, 15, 19, 23, 26, 29, 33, 37, 42, 48, 51],   # 0~51
'hevc_nvenc': [12, 17, 21, 25, 28, 32, 36, 40, 45, 51],       # 0~51
```

> ⚠ `(lo,hi)` 描述的是 **CQ 轴量程**（h264/hevc = 0~51），**不是** `-qp` 量程；
> AV1 的 `-qp`/`_QP_SCALE` 是另一条轴，不在本方案。

**改动后必过**：

```bash
python3 probe/calibrate_equal_quality.py --selftest          # 纯逻辑，秒级
python3 probe/calibrate_equal_quality.py --quick --src <素材> # 干跑，验证 nvenc 档位
```

### 阶段 2 · 小样门禁（单素材，先证明装置成立）

```bash
python3 probe/calibrate_equal_quality.py \
    --src /workspace/input_videos/new5_raw.mp4 \
    --duration 6 --codecs h264_nvenc,hevc_nvenc \
    --workroot /tmp/eqq_gpu --tag t4_smoke
#   期望：两档位都给出 a/b；单素材 dVMAF ≤ 1.5（M1 单素材门限）
```

### 阶段 3 · 全量标定（多素材 + 断点续跑）

**素材口径必须与 CPU 表一致**（同一 prep、同一锚点 `18/21/24/27/30`、同一 `n_subsample=1`、
同一 duration），否则 GPU 行与 CPU 行不可比、也无法并入同一 `QUALITY_MAP` 体系。

- **首选**：复用 M2 素材池（VE 仓 `input_videos/eqq_calib/`，两仓共用）与时长口径（6s + 10s 两遍）。
- **最小可行**（时间不够时）：本仓 7 素材（`temp/m2_srcs/`）单一口径（6s）出**首版**，
  并在报告里显式标注「单口径首版，待补 10s 侧」。

```bash
# 每个时长/素材桶独立 workdir、独立 tag（== LOO 合并的输入）
python3 probe/calibrate_equal_quality.py \
    --src <A> --src <B> ... \
    --duration 6 --codecs h264_nvenc,hevc_nvenc \
    --workroot /tmp/eqq_gpu --tag t4_720p_6s --resume < /dev/null
```

> - `--resume` 逐点落 `points.json`，长跑必备；断点续跑靠 `素材|档位|参数` 键。
> - VMAF `n_subsample=1` 单点约 45s；2 档 × (5 锚点 + ~10 扫描点) × N 素材是**小时级**长跑，
>   务必后台 + `--resume`。
> - 每素材独立 workdir（同目录并行会丢点）。

**LOO 留一**（直接读 `points.json`，秒级，无需重编码）：

```bash
python3 probe/loo_equal_quality.py \
    --workroot /tmp/eqq_gpu --tag t4_720p_6s [--tag t4_720p_10s] \
    --tiers h264_nvenc,hevc_nvenc --tol 1.0
#   期望：worst ΔVMAF < 1.0；若结构性超标，按 CPU 侧先例**记录并申请分档门禁**（勿私自放宽）
```

### 阶段 4 · 落表 + 两仓同步 + ⑨ 组

1. 取 `report.json` 的 `table`（`a=池化最小二乘`、`b=各素材中位数`），写入
   **两仓** `convert_crf.py` 的 `QUALITY_MAP`（本仓 + VE `src/utils/convert_crf.py`，**逐字相同**）：

```python
'h264_nvenc': (a, b, 0, 51),
'hevc_nvenc': (a, b, 0, 51),
```

2. ⚠ **回跑 ⑨ 组**（跨仓 `SIZE_MAP`/`QUALITY_MAP` 逐条相等会红）：

```bash
python3 verify/verify_quality_mapping.py        # 期望 ⑨ 组全绿
```

3. 更新方案 §4.12 表格、立项 M5 状态、`convert_crf.py` 注释（记 GPU 名 + 驱动 + ffmpeg 指纹）。

### 阶段 5 · 硬编等质量门禁解锁（任务 T4）

`verify/verify_equal_quality.py` 的 `SOFT`（`:34`）只列软编 ⇒ 硬编天然 SKIP。
落表后需让硬编条目进入实测：

- 把 `h264_nvenc`/`hevc_nvenc` 纳入可选编码器集（**按 `QUALITY_MAP` 是否覆盖决定是否测**，
  保留无 GPU 时的 SKIP + 全 SKIP 退出码 2 的空集守卫）；
- 复用 harness 的 `encode`（T0 已支持 nvenc 的 `-cq`），门禁口径**不变**：
  主门禁 `|ΔVMAF| ≤ 1.0`，`ΔPSNR`/`ΔPSNR-HVS` 仅 soft 参考。

```bash
python3 verify/verify_equal_quality.py --src <素材> --duration 6 \
    --codecs h264_nvenc,hevc_nvenc < /dev/null
#   期望：硬编条目由 SKIP 变实测，主门禁达标
```

### 阶段 6 · 真机长视频 + 探针复核 + 归档

```bash
# a) 落点/运行期/无损回归复核
python3 probe/verify_nvenc_quality_gpu.py --src '<真实素材>' \
    --json verification_report/nvenc_quality_T4_<TS>.json \
    --md   verification_report/nvenc_quality_T4_<TS>.md
python3 probe/t4_acceptance.py --src '<真实素材>'

# b) 真机长视频（生产管线实跑，判据见对侧 Plan/…真机长视频验证…Prompt.md）
#    在长片（≥数分钟、真实内容）上复核等质量落点的 VMAF/码率稳定性
```

报告统一落 `verification_report/`，**文件名带 GPU 名 + 时间戳**。

---

## 4. 判读矩阵

| 上机现象 | 结论 / 动作 |
|---|---|
| `h264_nvenc` / `hevc_nvenc` 池化 `max|ΔVMAF| ≤ 1.0` 且 LOO 达标 | 落表，解除 `SIZE_MAP` 回退 |
| LOO 结构性超标（多条同向） | **先排除**执行错误（时长/prep/subsample/指纹），确认为模型上限后**记录并申请分档门禁**（参照 CPU 侧 `≤5.9` 先例），不得私自放宽 |
| 单档 `dVMAF` 大但池化正常 | 查该素材 VMAF 非单调段 ⇒ PAVA 保应生效；否则检查扫描点是否覆盖锚点 |
| 某扫描点插值落空（`insufficient_points`） | 扩扫描：低端再降 / 高端再升 |
| 探针 C 组 h264/hevc `-qp 21` FAIL | 与「constqp `-qp` = 基准轴」冲突 ⇒ 复核 `to_constqp_qp()`，**勿动产品先** |
| `t4_acceptance` A 组 FAIL | 先比对产品 `执行命令` 与方案 §4.5；命令一致而探针红 ⇒ **改探针期望值**（`A_CASES`），别改产品 |
| 探针 av1 格 SKIP | T4 正常现象（无 AV1 NVENC），不算失败 |

---

## 5. 交付物清单

1. `probe/calibrate_equal_quality.py` —— 扩展 NVENC 支持（T0）。
2. 两仓 `convert_crf.py` 的 `QUALITY_MAP` —— 新增 `h264_nvenc` / `hevc_nvenc` 行（逐字同步）。
3. `verify/verify_equal_quality.py` —— 硬编条目解锁。
4. 报告：`/tmp/eqq_gpu/<tag>/report.json`、`verification_report/nvenc_quality_T4_<TS>.{json,md}`。
5. 文档：本方案 + `Plan/PROMPT_等质量换算立项.md` M5 状态 + `Plan/VidUtils_质量控制参数修复方案.md` §4.12；
   工程记忆 `memory/` 更新（见 memory 约定）。

---

## 6. 沿用的优秀做法（照抄，不要重新发明）

> 这些是 CPU 侧 M1~M4 与 T4 前几轮上机已经验证过的方法，直接复用。

1. **口径唯一 + 同轴**：主门禁**只**用 `VMAF`（`libvmaf pooled_metrics.vmaf.mean`）；
   `PSNR`/`SSIM`/`XPSNR` 用独立滤镜、`PSNR-HVS` 用 libvmaf feature —— **定标用哪个口径，门禁就用哪个**。
2. **`n_subsample=1` 铁律**：`>1` 会偏置 VMAF（同文件差 1.9~3.0 且随编码器而异）；标定与判据**同参、同时长**。
3. **同二进制指纹**：标定与门禁锁同一个 `ffmpeg`（记录版本行）；换构建须重标。
4. **无缓存 + `--resume` + md5 审计 + 每素材独立 workdir**：prep 先删后建，避免旧产物复用污染。
5. **PAVA 保序 + 等 VMAF 插值取最低参数解**：处理 VMAF 低码率/平涂内容的非单调平台。
6. **多素材跨素材聚合**：`a` = 池化最小二乘、`b` = 各素材中位数；**LOO 留一**做过拟合门禁。
7. **fail-fast 的显式 opt-in**（`--expect-av1` 类推）：不加时非目标卡静默 SKIP（退出码 0 易误判），
   加了则要求本卡满足前提，否则 **exit 2**。
8. **空集守卫**：一个「有结论」的格都没有时**不能报绿**（`t4_acceptance.Report.summary` 已实现）。
9. **结论行直接给可执行动作**（参照 `B-av1-结论` / `C-av1-结论`）：跑完直接说「动不动表」，别只给数字。
10. **两段式验收**：`--dry-run` 落点断言（秒级、任何机器）+ 真转码运行期（GPU 机）。
11. **探针期望值滞后 ≠ 产品回归**：A 组红先比对 §4.5 命令，一致则改探针 `A_CASES`。
12. **报告归档带 GPU 名 + 时间戳**，便于跨卡（T4/L40）对比。
13. **改表必回跑 ⑨ 组**；**改落点顺手同步 `A_CASES` 与 `--selftest`**。
14. **上机前查并发负载**：`pgrep` + `nvidia-smi` 三项 utilization，**不 kill 用户进程**。

### 6.1 `-tune` / `-multipass` 的取舍（2026-10-04 T4 实测）

> 依据 `Plan/ffmpeg_nvenc_knowledge.md` §5 / §5.1 / §5.2 + 本机 A/B（下表）。

| codec | cq | disabled VMAF | qres ΔVMAF | fullres ΔVMAF |
|---|---|---|---|---|
| h264_nvenc | 26 | 99.087 | −0.132 | −0.034 |
| h264_nvenc | 34 | 87.382 | −0.335 | −0.108 |
| hevc_nvenc | 26 | 99.265 | −0.067 | −0.006 |
| hevc_nvenc | 34 | 89.930 | −0.243 | −0.048 |

- **`-tune hq` 是 ffmpeg 默认值**（`-h encoder` 实测 `default hq`）⇒ 写了等于没写，**标定/生产都别加**。
  `uhq` 是 hevc/av1 专属（h264_nvenc 传 uhq → rc=234），是真正的画质杠杆（自动开 lookahead +
  temporal filter），需要时用 `--nvenc-tune uhq`。
- **固定 `-cq` 下 multipass 不升 VMAF**（上表：fullres −0.006~−0.108、qres −0.067~−0.335，
  码率 ×0.98~0.997）⇒ **标定与生产 CQ 路径都不加**。VE 方案里那条
  `-rc:v vbr_hq → -rc:v vbr -tune hq -multipass fullres` 的迁移路径**不适用于本仓 CQ 路径**：
  `-tune hq` 冗余、`-multipass` 无收益（第三方称其输出可能非确定，**本机复跑未复现**）。
- multipass 的价值在 **CBR / 紧 VBV**（把实际码率拉近目标）；VU 生产在 `--rc-mode cbr` 或给了
  `--bitrate` 时**自动补 `-multipass fullres`**（显式 `--nvenc-multipass` 优先），CQ 路径不动。
- ⚠ 别把 `-preset p7` 当 two-pass：现代 `p1~p7` 别名不带 multipass 标记（只有 legacy `slow` 会开两遍）。
- 落地：两脚本新增 `--nvenc-tune` / `--nvenc-multipass`（默认不发 ⇒ 现有命令逐字不变）；
  `probe/calibrate_equal_quality.py` 的 `BASE_LOCK` 仍为裸 `-rc vbr`。

---

## 7. 风险与坑

- **R1 · 素材池不在机器上**：M2 的 17 素材在 **VE 仓 `input_videos/eqq_calib/`（仓库外、不入 git）**；
  T4 机器需先把切片 + manifest 就位。缺失时用本仓 7 素材出首版并标注局限。
- **R2 · 时长效应**：同一素材跨时长锚点 VMAF 不同（crf30 跨差 ~1.75）⇒ 不同时长桶须**分开 LOO**，
  合并时才用「同 key 取均值」；**池化前打印文件数/逐文件点数/ACC/合并重复点数**四项核对（素材名去重 ≠ 数据完整）。
- **R3 · 有 GPU 的机器上跑 `verify/` 与回归门的「假红」**（既知，非本方案引入）：
  `verify_quality_mapping.py` ①、`verify_borrow_enhancement.py` ⑧、`verify_decode_axis.sh` ⑦、
  `verify_cuda_decode_codec.py` ⑥、`dump_cmd_full.sh` 的 2 个默认档用例会因「本机有 GPU」而红/分叉。
  判定法：`git worktree` 在改动前提交复跑，逐条比对一致即环境所致。
- **R4 · NVENC 代际差异**：T4(Turing) 标定的 h264/hevc 行**不保证**在 Ada(L40) 上等质量。
  若产品跨代使用，须在 L40 侧做一次复核并按需加代际行。
- **R5 · `-b:v` 与恒定质量**：NVENC 的 `-rc constqp -qp N` 与 `-cq N` **完全无视 `-b:v`**；
  本方案用 `-cq N -b:v 0`。**别**在标定里加 `-maxrate`，会改变等效点。
- **R6 · 零拷贝管线**：测量走软解 + `-pix_fmt yuv420p`（安全）；**只**在 `-hwaccel_output_format cuda`
  零拷贝链里禁传 `-pix_fmt`。生产落点属 `t4_acceptance` B 组覆盖范围，与本标定分离。
- **R7 · 改动落点**：本方案主要是**表值**变更；若连带改了 `_resolve_quality_params` 落点，
  必须同步 `t4_acceptance.A_CASES` 与 `--selftest`，并回跑 `dump_cmd_full.sh` / `dump_enc_options.sh`。

---

## 附录 · 命令速查

```bash
# 前置
nvidia-smi --query-gpu=name,driver_version --format=csv
ffmpeg -h encoder=h264_nvenc | grep -E '\-(cq|qp)'

# 标定（多素材，断点续跑）
python3 probe/calibrate_equal_quality.py --selftest
python3 probe/calibrate_equal_quality.py --quick --src <素材>
python3 probe/calibrate_equal_quality.py \
    --src <A> --src <B> --duration 6 \
    --codecs h264_nvenc,hevc_nvenc \
    --workroot /tmp/eqq_gpu --tag t4_720p_6s --resume < /dev/null

# LOO
python3 probe/loo_equal_quality.py --workroot /tmp/eqq_gpu --tag t4_720p_6s \
    --tiers h264_nvenc,hevc_nvenc --tol 1.0

# 落表后门禁
python3 verify/verify_quality_mapping.py
python3 verify/verify_equal_quality.py --src <素材> --duration 6 \
    --codecs h264_nvenc,hevc_nvenc < /dev/null

# 上机探针
python3 probe/verify_nvenc_quality_gpu.py --src <素材> \
    --json verification_report/nvenc_quality_T4_$(date +%Y%m%d_%H%M%S).json \
    --md   verification_report/nvenc_quality_T4_$(date +%Y%m%d_%H%M%S).md
python3 probe/t4_acceptance.py --src <素材>
```
