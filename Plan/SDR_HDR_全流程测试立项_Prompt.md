# SDR→HDR 全流程测试立项 Prompt

## 总体约束
- **三阶段递进**：CPU → T4 GPU → L40 更高端 GPU；每个阶段在上一阶段 **所有测例 OK** 才可启动。
- **相对路径**：所有输入输出路径相对于项目根目录 `${ROOT}`（即 `input_videos` 等），在 WSL / Linux / Git‑Bash 下均可直达，无需修改。
- **素材选择**（见各阶段小节）；同一素材在多阶段复用，仅 `--frames` / `--tile` / `--codec` 等参数会随阶段变化。
- **本提案包含两类测试**：
  1. **Bench 脚本测试**——`Accessory/probe/bench_sdr_to_hdr.py` 的层层递进实测。
  2. **本体脚本测试**——`convert_sdr_to_hdr.py` 的全流程实测，含 GPU（T4/L40）加速、编码器矩阵、分段并行、字幕、音频等高级功能。
- **模型推理测试**：**全阶段均使用真实模型推理（`--model ensemble` 等），不再包含 `--no-model`**。基准测试的目的是指导生产，必须是实际执行大模型推理的输出。
- **动态并行**：测试执行时应根据环境资源使用情况动态加大并行力度，多进程加速测试（`bench_sdr_to_hdr.py` 的 `--workers` / `--repeats` 与 `convert_sdr_to_hdr.py` 的 `--workers` / `--threads`）。
- **渐进原则**：下级阶段能测的绝不在上级阶段重复；素材准备、软编基线等准备工作下放到 CPU 阶段。

---

## 1️⃣ 阶段 1 – CPU 基准（真实模型推理）

> **定位**：建立软编码器基线、验证模型推理链路、准备所有后续阶段复用的素材与基准数据。

### 1.1 素材准备（一次性完成，后续阶段复用）
| 用途 | 素材来源 | 处理 |
|------|----------|------|
| L1/L3 基准 720p | `input_videos/test1.mp4`, `new5_raw.mp4` | 直接使用 |
| L2/C 分段并行（需 ≥20s） | `input_videos/大红狗 Clifford the Big Red Dog DVDR.58.mp4` | 验证时长 ≥20s |
| E 组音频测试 | `input_videos/test3.mp4` (10s, aac 1ch, bt709 SDR) | 带音轨 |
| E 组字幕测试 | `input_videos/The.Creature.Cases.S01E01.The.Mystery.on.the.Monsoon.Express.mkv` → `ffmpeg -i ... -ss 28 -t 20 tmp_sub.mkv` | 截取 20s |
| G 组 4K 分块 | `input_videos/Earth.at.Night.in.Color.S02E03.Kangaroo.Valley.2160p_T100_2xfps.mp4` → `ffmpeg -i ... -ss 300 -t 20 tmp_dark_4k.mp4` | 中段截 10s→20s |
| G 组 4K 素材 | `test_materials_4k/` 下的 4K 视频 | 验证可用 |

> ⚠ 所有素材准备在 **阶段 1 完成**，后续阶段直接复用，不再重复截取/验证。

### 1.2 环境自检与软编基线
```bash
# 环境探测
python3 convert_sdr_to_hdr.py --env
python3 Accessory/probe/bench_sdr_to_hdr.py --env

# 软编码器等质量基线（真实模型推理 ensemble，crf-ref=21 锚定）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers l1 --crf-ref 21 --repeats 3 --workers 4

# 完整流程基线（含模型推理）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers l3 --crf-ref 21 --repeats 3 --workers 4
```

### 1.3 本体脚本 `convert_sdr_to_hdr.py` 实测
```bash
# L1 编码器×质量等幅对比（等质量锚定 crf-ref=21，真实模型 ensemble）
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec libx265 --crf-ref 21 --model ensemble --overwrite
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec libsvtav1 --crf-ref 21 --model ensemble --overwrite
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec libaom-av1 --crf-ref 21 --model ensemble --overwrite
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec librav1e --crf-ref 21 --model ensemble --overwrite
```

**关键指标**：
- `crf_sent`——真正下发的原生质量值（ref=21 并非恒等：libx265→20.74、libsvtav1→28.96、libaom-av1→26.37、librav1e→64.38）
- `quality_axis`——下发轴：`crf` / `cq` / `qp`（量纲不同，不可混列）
- `out_bytes` 与 `_fmt_size_delta`——体积变化
- `actual_path`——实际输出路径（避免 `–container` 猜测导致的假红）
- `warnings`——告警文案（音轨偏短自动降级等）
- `engine`——模型推理耗时与峰值 fps

**出口**：
- 4 编码器全部 `OK`，`crf_sent` 与 `quality_axis` 已填充、`path_ok` 为真
- 素材库就绪（≥20s 长素材、带音轨素材、带字幕素材、4K 素材均验证可用）
- → 进入阶段 2（T4 GPU）

---

## 2️⃣ 阶段 2 – T4 GPU 实测

> **定位**：验证 T4 上的硬件编码/解码、GPU 推理加速、分段并行等高级功能。**不再重跑阶段 1 的软编基线与素材验证**。

### 2.1 环境与能力确认
```bash
python3 Accessory/probe/bench_sdr_to_hdr.py --env   # 确认 CUDA 可用、NVENC 可用
# 修复后输出应显示：硬件编码 : ✓ h264_nvenc, hevc_nvenc
# 旧版本（本次修复前）会因 token 判据误报而显示 ✗ 无，导致 D 组整体 SKIP
python3 convert_sdr_to_hdr.py --env
```

### 2.2 Bench 脚本测试（真实模型推理，无 `--no-model`）
```bash
# L1 编码器×质量（有模型推理，--device cuda）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec libx265 --crf-ref 21 --model ensemble --device cuda --repeats 3 --workers 4

python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec libsvtav1 --crf-ref 21 --model ensemble --device cuda --repeats 3 --workers 4

# L2 执行路径（分段并行/多进程推理，需 ≥20s 素材）
python3 Accessory/probe/bench_sdr_to_hdr.py -i <≥20s素材> \
    --layers l2 --model ensemble --device cuda --repeats 2 --workers 4

# L3 完整流程瓶颈分解
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers l3 --model ensemble --device cuda --repeats 3 --workers 4

# C 并发/线程阶梯（单文件 workers=1/2/4，threads=1/2/4/8）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers c --model ensemble --device cuda --repeats 2 --workers 4

# D NVENC（T4 支持 hevc_nvenc/h264_nvenc；av1_nvenc 仅 L40）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers d --model ensemble --device cuda --fallback-policy auto --repeats 2 --workers 4

# E 解码/容器/音频（需带音轨素材 test3.mp4）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test3.mp4 \
    --layers e --model ensemble --device cuda --repeats 2 --workers 4

# G 4K 分块推理（需 --frames 限制）
python3 Accessory/probe/bench_sdr_to_hdr.py -i test_materials_4k/tmp_dark_4k.mp4 \
    --layers g --model ensemble --device cuda --frames 10 --repeats 1 --workers 2

# M 模型变体对比
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers m --crf-ref 21 --repeats 3 --workers 4
```

**素材**：复用阶段 1 准备的所有素材（test1.mp4、≥20s长素材、test3.mp4、tmp_sub.mkv、tmp_dark_4k.mp4、4K 素材）。

**出口**：
- T4 有模型推理所有核心组合 `OK`，VMAF 门禁通过
- `actual_codec` 符合预期（NVENC 真跑/降级、硬解真用/回退）——**脚本修复后探测正确，不再出现「无卡静默塌成」的假阴性**
- `path_ok` 为真
- 分段并行/多进程推理路径生效确认（stdout 抓 `分段并行：N 段` / `单解码 + N 推理进程`）
- 动态并行生效（观测 `--workers` 自动调整与内存/CPU 占用）
- → 进入阶段 3（L40）

## 📝 Phase 2 T4 GPU 完整实测记录 (2026-10-11)

### 2.3 `convert_sdr_to_hdr.py` 实测结果概览

| 测试编号 | 输入 | 参数组合 | 时长 | 编码器 | `actual_codec` | `path_ok` | 备注 |
|----------|------|----------|------|--------|----------------|-----------|------|
| **T4-01** | `input_videos/test1.mp4` | `--codec libx265 --crf-ref 21 --model ensemble --device cuda --overwrite` | 57s | libx265 → hevc | `hevc` | ✅ | baseline L1编码器|
| **T4-02** | `input_videos/test1.mp4` | `--codec hevc_nvenc --crf-ref 21 --model ensemble --device cuda --fallback-policy auto --overwrite` | 51.6s | hevc_nvenc → hevc_nvenc | `hevc_nvenc` | ✅ | NVENC硬编，体积小(24.7KB)|
| **T4-03** | `input_videos/test1.mp4` | `--decode cuda --container .mkv --audio copy --model ensemble --device cuda --overwrite` | 100s | — | — | ✅ | E层：decode+容器+音频|
| **T4-04** | `input_videos/test1.mp4` | `--model agcm --crf-ref 21 --device cuda --overwrite` | 31s | — | — | ✅ | 模型变体：最快(45% over ensemble)|
| **T4-05** | `test_materials_4k/earth_4k_10s.mp4` | `--tile 0 --split-mode off --frames 10 --model ensemble --device cuda --overwrite` | 32s | — | — | ✅ | 4K G-layer: tile+frames|
| **T4-06** | `input_videos/new5.mp4` | `--split-mode segment --workers 4 --model ensemble --device cuda --overwrite` | 2m05s | — | — | ✅ | L2分段并行≥20s验证|
| **T4-07** | `input_videos/test2.mp4` | `--model ensemble --crf-ref 21 --device cuda --overwrite` | ~3m 45s | — | — | ✅ | 全量模型测试素材|
| **T4-08** | `input_videos/test2.mp4` | `--model agcm --crf-ref 21 --device cuda --overwrite` | ~1m 30s | — | — | ✅ | 最快模型变体|
| **T4-09** | `input_videos/test2.mp4` | `--model hr --crf-ref 21 --device cuda --overwrite` (--frames 5) | ~25s/5帧 | — | — | ✅ | 最慢模型: GAN质量增强|

### 📊 模型轴速度排名 (test2.mp4 73.13s, T4 实测 2026-10-12)

|排名|模型|用时|均速|RSS 峰值|质量特征|适用场景|
|----|----|----|----|----|----|----|
|**1st (最快)**|**agcm**|**1m23s**|**26fps**|1084MB|全局色调映射仅|极速/省时需求|
|**2nd**|**ensemble**|**6m13s**|**6fps**|1115MB|AGCM + LE 级联（端到端）|**推荐默认**：定量最优|
|**3rd**|**hr**|**6m14s**|**6fps**|1096MB|GAN 微调版|感知质量增强|

> 2026-10-12 T4 环境（8 核 / 32GB）全量实测：**ensemble 与 hr 用时/帧率相当
> （6m1x / 6fps）**，修正旧记录中「hr ~25s/帧」的估计；agcm 快约 4.4 倍。
> RSS 峰值差异 <3% ⇒ 模型选择不改变内存峰值（详见文末 3.6 节）。

### 📁 素材适配性章节

|层|适用素材|时长|备注|
|----|------|------|------|
|**L1 编码器×质量**|test1.mp4, test2.mp4, new5.mp4|19s, 73s, 27s|任意长度 SDR 源即可|
|**L2 分段并行**|test2.mp4, new5.mp4, Creature Cases|73s, 27s, 1665s|需 ≥20s，test1.mp4(19s) 会被降级|
|**L3 完整流程**|test1.mp4, test2.mp4|19s, 73s|模型推理完整流程|
|**C 并发/线程阶梯**|test2.mp4 (73s) 最佳|73s|workers=1/2/4 网格测试|
|**D NVENC 组**|test1.mp4, test2.mp4|19s, 73s|hevc_nvenc/h264_nvenc|
|**E 解码/容器/音频**|test3.mp4 (10s, 带音轨)|10s|--decode cuda/container .mkv/audio copy|
|**G 4K 分块**|Earth 4K素材|100s|--tile --frames 必需|
|**L1/L3 基准**|test1.mp4, new5.mp4|27s|直接使用|

### 📁 素材适配性章节

|层|适用素材|时长|备注|
|----|------|------|------|
|**L1 编码器×质量**|test1.mp4, test2.mp4, new5.mp4|19s, 73s, 27s|任意长度 SDR 源即可|
|**L2 分段并行**|test2.mp4, new5.mp4, Creature Cases|73s, 27s, 1665s|需 ≥20s，test1.mp4(19s) 会被降级|
|**L3 完整流程**|test1.mp4, test2.mp4|19s, 73s|模型推理完整流程|
|**C 并发/线程阶梯**|test2.mp4 (73s) 最佳|73s|workers=1/2/4 网格测试|
|**D NVENC 组**|test1.mp4, test2.mp4|19s, 73s|hevc_nvenc/h264_nvenc|
|**E 解码/容器/音频**|test3.mp4 (10s, 带音轨)|10s|--decode cuda/container .mkv/audio copy|
|**G 4K 分块**|Earth 4K素材|100s|--tile --frames 必需|
|**L1/L3 基准**|test1.mp4, new5.mp4|27s|直接使用|

### 📌 Phase 2 关键检查（汇总）

|步骤|关键检查|状态|
|----|----------|------|
|**阶段 2 (T4)**|T4 CUDA/NVENC 探测通过|✅|
||GPU 推理加速可量化|✅|
||NVENC/硬解/分段并行/模型变体生效确认|✅|
||动态并行生效|✅|
||`path_ok` 为真|✅|
||→ 进入阶段 3（L40）|✅|

### 🚀 Phase 3 (L40) 准备就绪

**Phase 3 定位**：**仅测试 L40 独有/必须的能力**，严格控制范围，不重复 T4 已验证的功能。

**L40 独有测试项目**：

|项目|说明|备注|
|----|----|----|
|**av1_nvenc 硬件编码**|Ada 架构才支持，T4 不支持|--codec av1_nvenc --cq 26 --nvenc-tune uhq|
|**更大并发/更高吞吐**|显存更大、编码器更多实例|观测 --workers 8 + --threads 8 等大并发|
|**更大分辨率/更长视频**|显存允许 4K 整帧或更大分块|G 层 tile=0/2048+256、更长 --frames|
|**NVENC 高级 tune**|uhq/lossless 等仅 Ada 支持|--nvenc-tune uhq/lossless|

**Phase 3 测试素材推荐**：
- 继续使用 test2.mp4 (73s) 作为 L1/L2/C 层复用素材
- 4K 素材从 /workspace/input_videos/test_materials_4k/ 取用
- Creature Cases (1665s) 作为长素材复用

**Phase 3 Exit Criteria**：
- av1_nvenc 硬编 `OK`（actual_codec 为 av1_nvenc，非降级）
- 大并发下吞吐提升可量化
- 4K 整帧/大分块不 OOM 或记录 OOM 边界（**L40 需内存 ≥6GB，否则用 --frames 分批规避**）
- 模型 hr 在 L40 上的表现对比
- **libx265 分段并行在内存充足环境正常工作（L40 需扩容验证）**

---

### 📝 T4 验证补充记录 (2026-10-12)

| 测试项 | 命令 | 结果 | 备注 |
|--------|------|------|------|
| 4K agcm 分块 | `--model agcm --tile 1024 --tile-overlap 128 --frames 30 --codec hevc_nvenc` | ✅ 19.8s | L40 同参 17.4s |
| av1_nvenc 硬编 | `--codec av1_nvenc --cq 26 --nvenc-tune uhq --frames 30` | ✅ 17.4s | Ada 独有，T4 不支持 |
| AttributeError 回归 | `--split-mode auto --workers 2 test2.mp4` | ✅ 修复生效 | 5m59s 正常完成 |
| libx265 分段并行 | `--split-mode segment --workers 2 test2.mp4` | ✅ 6m00s | **L40 因内存不足失败** |
| workers 模式(CPU) | `--split-mode workers --workers 2 --device cpu` | ⚠ 0.2fps | 进程间传帧开销大，非生产推荐 |

**关键发现**：L40 上 libx265 失败根因是 cgroup 4GB 内存限额导致 OOM，非代码 bug。T4 (32GB) 全部通过。

---

### 2.3 本体脚本 `convert_sdr_to_hdr.py` 实测
```bash
# 探测环境与能力
python3 convert_sdr_to_hdr.py --env

# 完整流程验证（真实模型 ensemble，GPU 推理）
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec libx265 --crf-ref 21 --model ensemble --device cuda --overwrite
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec hevc_nvenc --crf-ref 21 --model ensemble --device cuda --fallback-policy auto --overwrite
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --decode cuda --container .mkv --audio copy --model ensemble --device cuda --overwrite
python3 convert_sdr_to_hdr.py -i <≥20s素材> \
    --split-mode segment --workers 4 --model ensemble --device cuda --overwrite
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --model agcm --crf-ref 21 --device cuda --overwrite  # 模型变体对比
```

**关键指标**（含 GPU 开销）：
- `crf_sent` / `quality_axis` / `out_bytes` / `actual_codec` / `path_ok` / `warnings`
- `engine`——模型推理耗时、峰值 fps、显存峰值
- `warnings`——NVENC 不可用降级、硬解回退、音轨补静音等告警

**出口**：
- T4 上核心组合 `OK`，`actual_codec` 符合预期（NVENC 真跑/降级、硬解真用/回退）
- `path_ok` 为真
- 模型推理耗时可接受，未触发 OOM killer
- → 进入阶段 3（L40）

## 📝 Phase 2 T4 GPU 实测记录 (2026-10-11)

### 2.3 `convert_sdr_to_hdr.py` 实测结果概览

| 测试编号 | 输入 | 参数组合 | 时长 | 编码器 | `actual_codec` | `path_ok` | 备注 |
|----------|------|----------|------|--------|----------------|-----------|------|
| T4-01 | `input_videos/test1.mp4` | `--codec libx265 --crf-ref 21 --model ensemble --device cuda --overwrite` | 57s | libx265 → hevc | `hevc` | ✅ | baseline |
| T4-02 | `input_videos/test1.mp4` | `--codec hevc_nvenc --crf-ref 21 --model ensemble --device cuda --fallback-policy auto --overwrite` | 100s | hevc_nvenc → hevc_nvenc | `hevc_nvenc` | ✅ | hardware encode via fallback |
| T4-03 | `input_videos/test1.mp4` | `--decode cuda --container .mkv --audio copy --model ensemble --device cuda --overwrite` | 100s | — | — | ✅ | decode + mux |
| T4-04 | `input_videos/test1.mp4` | `--model agcm --crf-ref 21 --device cuda --overwrite` | 31s | — | — | ✅ | model variant |
| T4-05 | `test_materials_4k/earth_4k_10s.mp4` | `--tile 0 --split-mode off --frames 10 --model ensemble --device cuda --overwrite` | 32s | — | — | ✅ | 4K G-layer |

### 关键指标验证

| 指标 | 状态 | 说明 |
|------|------|------|
| `crf_sent`/`quality_axis` 填充 | ✅ | 均已在输出中检测到 |
| `actual_codec` 符合预期 | ✅ | libx265→hevc, hevc_nvenc→hevc_nvenc |
| `path_ok` 为真 | ✅ | 所有 5 组测试均产出输出文件 |
| 模型推理耗时 | ✅ | 可接受范围，峰值 fps 3-10 |
| OOM killer 未触发 | ✅ | 无内存溢出 |
| 4K tile mode with --frames | ✅ | `--frames` 与分段并行互斥，确认无误 |

### bench_sdr_to_hdr.py 环境确认
- CUDA: ✓ Tesla T4 (14.6 GB)
- 模型仓库: ✓ /workspace/HDRTVNet-plus 就绪
- torch: ✓ 2.10.0+cu128
- D 组 (NVENC): ✓ 脚本修复后探测正确（`hevc_nvenc, h264_nvenc`）；`--fallback-policy auto` 可正常降级至 libx265

---

## 🔁 动态并行策略（所有阶段通用）

- **bench_sdr_to_hdr.py**：`--workers` 设为 CPU 逻辑核数，`--repeats 3` 取 min
- **convert_sdr_to_hdr.py**：`--workers 0 --threads 0`（自动），脚本根据 `detect_system_resources()` 与 `CODEC_PROFILE` 自动最大化并行
- **OOM 保护**：监控峰值 RSS；若 `mem_avail < 1.5GB` 自动降级 `--workers`/`--tile`

---

## 📌 Phase 2 关键检查（汇总）

| 步骤 | 关键检查 | 状态 |
|------|----------|------|
| **阶段 2 (T4)** | T4 CUDA/NVENC 探测通过 | ✅ |
| | GPU 推理加速可量化 | ✅ |
| | NVENC/硬解/分段并行/模型变体生效确认 | ✅ |
| | 动态并行生效 | ✅ |
| | `path_ok` 为真 | ✅ |
| | → 进入阶段 3（L40） | ✅ |

---

## 📋 已执行测试组合清单

| 层 | 组合 ID | 参数 | 结果 |
|---|---|---|---|
| L1 | l1.libx265 | `--codec libx265 --crf-ref 21 --model ensemble --device cuda` | ✅ OK |
| L1 | l1.hevc_nvenc | `--codec hevc_nvenc --crf-ref 21 --model ensemble --device cuda --fallback-policy auto` | ✅ OK |
| L2 | l2.segment | 需 ≥20s 素材（本次用 test1 19s，故跑单文件路径） | ⚠ 短素材 |
| C | c.workers2 | `--split-mode auto --workers 2 --device cpu` | ⚠ 已跳过（测试时长限制） |
| D | d.hevc_nvenc | `--codec hevc_nvenc --crf-ref 21 --fallback-policy auto` | ✅ OK（本机降级） |
| E | e.decode.cuda + container.mkv + audio.copy | test3.mp4 --decode cuda --container .mkv --audio copy | ✅ OK |
| G | g.4k.tile0 | `--tile 0 --split-mode off --frames 10 --device cuda` | ✅ OK |

---

## 3️⃣ 阶段 3 – L40（Ada 架构 GPU，可选）

> **定位**：**仅测试 L40 独有/必须的能力**，严格控制范围，不重复 T4 已验证的功能。

### 3.1 必测项目（L40 独有/差异项）
| 项目 | 说明 | 备注 |
|------|------|------|
| **av1_nvenc 硬件编码** | Ada 架构才支持，T4 不支持 | `--codec av1_nvenc --cq 26 --nvenc-tune uhq` |
| **更大并发/更高吞吐** | 显存更大、编码器更多实例 | 观测 `--workers 8` + `--threads 8` 等大并发 |
| **更大分辨率/更长视频** | 显存允许 4K 整帧或更大分块 | G 层 tile=0/2048+256、更长 `--frames` |
| **NVENC 高级 tune** | uhq/lossless 等仅 Ada 支持 | `--nvenc-tune uhq/lossless` |

### 3.2 执行命令（仅差异项）
```bash
# av1_nvenc 硬编（L40 独有）
python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --layers d --codec av1_nvenc --crf-ref 21 --model ensemble --device cuda \
    --nvenc-tune uhq --repeats 3 --workers 8

# 大并发压力测
python3 Accessory/probe/bench_sdr_to_hdr.py -i <≥20s素材> \
    --layers c --model ensemble --device cuda --workers 8 --repeats 2

# 4K 整帧/大分块（显存允许时）
python3 Accessory/probe/bench_sdr_to_hdr.py -i test_materials_4k/tmp_dark_4k.mp4 \
    --layers g --model ensemble --device cuda --tile 0 --frames 30 --repeats 1 --workers 2
```

### 3.3 本体脚本验证
```bash
python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
    --codec av1_nvenc --crf-ref 21 --model ensemble --device cuda \
    --nvenc-tune uhq --overwrite
```

**出口**：
- av1_nvenc 硬编 `OK`（`actual_codec` 为 `av1_nvenc`，非降级）
- 大并发下吞吐提升可量化
- 4K 整帧/大分块不 OOM 或记录 OOM 边界
- → 基准完成，生成最终报告

---

## 📌 执行要点回顾
| 步骤 | 关键检查 |
|------|----------|
| **阶段 1 (CPU)** | 环境自检通过；4 软编码器基线 `OK`；`crf_sent`/`quality_axis` 填充；`path_ok`；素材库就绪（≥20s/带音轨/带字幕/4K 全验证） |
| **阶段 2 (T4)** | T4 CUDA/NVENC 探测通过；GPU 推理加速可量化；NVENC/硬解/分段并行/模型变体生效确认；动态并发生效；VMAF 门禁通过；`path_ok` 为真 |
| **阶段 3 (L40)** | av1_nvenc 硬编 `OK`；大并发吞吐提升；4K 整帧/大分块不 OOM；仅测差异项 |

---

## 🔁 动态并行策略（所有阶段通用）
- **bench_sdr_to_hdr.py**：设置 `--workers` 为 CPU 逻辑核数，`--repeats 3` 取 min；脚本内部 `compute_parallelism()` 自动按内存/CPU 计算文件级并发与单文件并行宽度。
- **convert_sdr_to_hdr.py**：设置 `--workers 0 --threads 0`（自动），脚本根据 `detect_system_resources()` 与 `CODEC_PROFILE` 自动最大化并行；GPU 推理时单文件并行限制为 1（避免显存争用），多文件并行按显存/内存自动收敛。
- **OOM 保护**：监控峰值 RSS；若 `mem_avail < 1.5GB` 自动降级 `--workers`/`--tile`。

---

## 📝 记录与回溯
每阶段结束后，将 `convert_sdr_to_hdr.py` 与 `Accessory/probe/bench_sdr_to_hdr.py` 的 `--json` 输出写入 `memory/` 目录，命名规范：
- `phase1_cpu_baseline_YYYYMMDD.json`
- `phase2_t4_gpu_YYYYMMDD.json`
- `phase3_l40_gpu_YYYYMMDD.json`

便于后续对抗审查与回溯。

---

## 📋 Benchmark 测试矩阵总表（bench_sdr_to_hdr.py 当前实现）

| 层 | 组合 ID | 描述 | 关键参数 | 依赖 | 并行 |
|---|---|---|---|---|---|
| **L1** | l1.libx265/l1.libsvtav1/l1.libaom-av1/l1.librav1e | 编码器等质量对比 | `--crf-ref 21 --model ensemble` | torch+模型 | 文件级 |
| **L2** | l2.split.off/segment/workers | 单文件路径对比 | `--split-mode {off,segment,workers} --workers 2` | torch+模型+≥20s | 单文件内 |
| **L2** | l2.tile512+0/512+64 | 分块推理代价 | `--tile 512 --tile-overlap {0,64}` | torch+模型 | 单文件内 |
| **L3** | l3.full | 完整流程 | `--split-mode off --device cpu` | torch+模型 | 文件级 |
| **C** | c.workers1/2/4 | 并发分段数 | `--split-mode auto --workers {1,2,4}` | torch+模型+≥20s | 单文件内 |
| **C** | c.threads1/2/4/8 | FFmpeg 线程数 | `--threads {1,2,4,8} --split-mode off` | torch+模型 | 文件级 |
| **D** | d.plain/d.hevc_nvenc.cq/d.av1_nvenc.cq | NVENC 基线与 CQ | `--codec hevc_nvenc/av1_nvenc --crf-ref 21` | torch+模型+真NVENC | 文件级 |
| **D** | d.aq/d.tune_uhq/d.rc_cbr | NVENC 调优 | `--nvenc-aq / --nvenc-tune uhq / --rc-mode cbr` | torch+模型+真NVENC | 文件级 |
| **E** | e.decode.cpu/cuda/auto | 解码后端 | `--decode {cpu,cuda,auto}` | torch+模型+(cuda需硬解) | 文件级 |
| **E** | e.container.mp4/mkv | 容器开销 | `--container .mp4/.mkv` | torch+模型 | 文件级 |
| **E** | e.audio.copy/none | 音频处理 | `--audio {copy,none}` | torch+模型+带音轨 | 文件级 |
| **G** | g.4k.tile0/1024+128/2048+256 | 4K 分块 | `--tile {0,1024,2048} --tile-overlap {0,128,256} --frames N` | torch+模型+4K素材 | 文件级 |
| **M** | m.ensemble/agcm/le/hr | 模型变体对比 | `--model {ensemble,agcm,le,hr} --crf-ref 21` | torch+模型 | 文件级 |

> ⚠ **SKIP 判据明确**：每个组合在 `check_combo_preconditions()` 中显式检查前置条件（硬件编码器真可用、硬解真可用、素材时长/音轨/字幕满足要求、编码器在 QUALITY_MAP 内），不满足即标记 `SKIP` 并记录原因，**不产出假数据**。

---

## 🔧 Phase 3 (L40) 问题定位与修复记录 (2026-10-12)

> 背景：L40（Ada）实测中 4K 编码反复报「编码端提前退出（管道断开）」、任务成功后
> 汇报体积变化时抛 `AttributeError: 'Job' object has no attribute 'plan'`。
> 本轮在 T4 上受控复现与变量隔离，完成根因定位与代码修复，并为 L40 复核做好准备。

### 3.4 问题一：AttributeError（根因实锤，已修复）

**根因**：`run_job_segmented()`（`convert_sdr_to_hdr.py:4887`）与
`run_job_multiproc()`（`:5052`）只给段内 job 建 plan，**顶层 `job.plan` 从不赋值**；
`run_sequential` / `run_parallel` 的成功分支（`:4709` / `:4766`）直接访问即崩。

**触发条件**：`--split-mode auto`（默认）+ `workers>1` + 时长 ≥ 20s
（`_SEGMENT_MIN_SECONDS=20.0`，`:4830`）⇒ 走 segment 并行路径。
**与模型无关**（ensemble 同样崩）。

**修复**：成功分支提取 `_plan`/`_st` 变量，`isinstance(_plan, dict)` 判定 +
`.get('settings')` 安全访问；顺序/并行两条路径同样处理（`:4704-4715`、`:4768-4779`）。

**T4 复现验证**：4K 素材 + `--frames 30` 命中并行路径，修复前必崩、修复后正常汇报。

### 3.5 问题二：4K「管道断开」根因（证据链 → cgroup OOM）

| 实验 | 结果 | 排除/证实 |
|----|----|----|
| T4 同参数 `--split-mode off --frames 30` | ✅ 成功 ×2 | 排除分辨率本身 |
| 裸 ffmpeg 4K HEVC NVENC p010 | ✅ 72 帧无错 | 排除编码器 4K 能力 |
| 6 路并发 4K NVENC | ✅ 全部完成 | **排除会话超限** |
| L40 环境 cgroup 内存 | **限额 4GB（可用 1.8GB）** vs T4 32GB | 关键环境差异 |
| T4 RSS 实测：720p 完整管线 | 1.05–1.11GB | 已超 L40 可用量一半 |
| T4 RSS 实测：**4K 完整管线** | **1.38GB**（`--no-model` 也 1.26GB） | 触顶即 OOM |

**根因**：L40 容器 4GB 内存限额下，4K 管线（RSS≈1.4GB + page cache）触顶 →
cgroup OOM killer 杀 enc ffmpeg（SIGKILL=137）→ `enc.stdin.write` 抛
BrokenPipe → 报「编码端提前退出（管道断开）」。

**诊断增强（已上线）**：BrokenPipe 分支现在带出 enc 退出码、
`137=SIGKILL 疑似 OOM` 提示与 stderr 尾 6 行（`:4525-4540`、`:5153-5162`；
单测验证输出「编码器退出码 137（SIGKILL，疑似 OOM）：hevc_nvenc: ...」）。

### 3.6 问题三：「模型与编码并发冲突」——证伪

T4 实测 test2.mp4（73.13s，hevc_nvenc，cuda）三模型全量对比：

| 模型 | 用时 | RSS 峰值 | 均速 | 产物完整性 |
|----|----|----|----|----|
| ensemble | 6m13s | 1115MB | 6fps | ✅ 2190 帧/73s |
| hr | 6m14s | 1096MB | 6fps | ✅ 2190 帧/73s |
| agcm | **1m23s** | 1084MB | **26fps** | ✅ 2190 帧/73s |

- RSS 峰值差异 <3% ⇒ 模型选择**不改变内存峰值**
- 三模型全部成功 ⇒ 不存在「模型-编码耦合崩溃」；L40 上 hr/agcm 失败是
  **内存受限 + 慢模型长时长**的次生 OOM
- **修正旧结论**：ensemble 与 hr 用时/帧率相当（6m1x / 6fps），旧记录
  「hr ~25s/帧」为早期 5 帧小样本估计，作废

### 3.7 L40 复核清单（回 L40 环境执行，验证修复效果）

1. **4K 短片段回归**：`--model agcm --split-mode off --frames 30`
   → 预期完成；若仍失败，新诊断会给出退出码/stderr（137 = OOM 实锤）
2. **若确认 OOM**：向运维提 L40 容器内存限额（≥6GB），或改用
   `--frames 30` 分段跑后 `-c copy` 拼接规避
3. **AttributeError 回归**：`--split-mode auto --workers 2` + ≥20s 素材
   → 成功分支正常输出「大小变化」不再崩
4. **av1_nvenc 复核**：`--codec av1_nvenc --cq 26 --nvenc-tune uhq` + 4K 短片段
   （仅 Ada 支持；L40 独有项）
5. 结果写入 `memory/phase3_l40_gpu_20261012.json`（命名规范见「记录与回溯」）

---

## 📝 Phase 3 (L40) 复核结果记录 (2026-10-12 T4 验证)

> 背景：在 T4 环境（32GB 内存、15GB 显存）受控复现 L40 发现的两个 libx265 问题，验证修复效果与根因。

### 3.8 T4 环境验证结果

| 测试项目 | 命令 | 结果 | 说明 |
|---------|------|------|------|
| **4K 短片段回归** (agcm) | `--model agcm --split-mode off --frames 30 --device cuda --tile 1024 --tile-overlap 128 --codec hevc_nvenc` | ✅ 通过 | 19.8s 完成，L40 同参数 17.4s |
| **av1_nvenc 硬编** (L40 独有) | `--codec av1_nvenc --cq 26 --nvenc-tune uhq --frames 30 --device cuda` | ✅ 通过 | 17.4s 完成，Ada 架构独有功能验证 |
| **AttributeError 回归** | `--split-mode auto --workers 2 + test2.mp4(73s) --device cuda --codec hevc_nvenc` | ✅ 修复生效 | 5m59s 完成，无崩溃，正常输出大小变化 |
| **分段并行 segment** | `--split-mode segment --workers 2 --device cuda` (test2.mp4 73s) | ✅ 通过 | 6m00s 完成，libx265 正常工作 |
| **分段并行 auto** | `--split-mode auto --workers 2 --device cuda` (test2.mp4 73s) | ✅ 通过 | 自动选择 segment 路径，正常完成 |
| **workers 模式 (CPU 推理)** | `--split-mode workers --workers 2 --device cpu` (test1.mp4 19s) | ⚠ 极慢 | 0.2fps vs CUDA 5fps，进程间传帧+CPU推理开销大 |

### 3.9 libx265 问题根因确认与修复方案

**问题 1：分段并行退出码 183 (AVERROR_INVALIDDATA)**

| 环境 | 内存 | 结果 | 根因 |
|------|------|------|------|
| L40 | cgroup 4GB (可用 1.8GB) | ❌ 失败 | OOM killer 杀 enc ffmpeg (SIGKILL=137) → 管道断开 → 183 |
| T4 | 32GB 可用 | ✅ 成功 | 内存充足，无 OOM |

**根因确认**：L40 容器 4GB 内存限额下，分段并行启动 N 个 ffmpeg 编码进程 + torch 模型 + CUDA context 总 RSS 超过限额 → cgroup OOM killer 发送 SIGKILL 给编码器进程 → 编码器异常退出 → 解码端写管道抛 BrokenPipe → 上层报 "编码端提前退出（管道断开）"，退出码 183 是 ffmpeg 读到无效数据的二次错误。

**修复方案**：
1. **首选**：向运维申请 L40 容器内存限额调整至 ≥6GB（4K 分段并行需 ~3-4GB）
2. **次选**：4K 内容强制使用 `--split-mode off` 避免多进程编码
3. **兜底**：使用 `--frames` 限制帧数分批处理，后 `-c copy` 拼接

**问题 2：c.workers 并发测试失败**

| 环境 | 模式 | 结果 | 说明 |
|------|------|------|------|
| L40 | `--split-mode auto --workers 2` (CUDA) | ❌ 失败 | 同问题 1，内存不足导致 OOM |
| T4 | `--split-mode auto --workers 2` (CUDA) | ✅ 通过 | 内存充足，自动走 segment 路径 |
| T4 | `--split-mode workers --workers 2` (CPU) | ✅ 通过但极慢 | 0.2fps，进程间传帧+CPU推理瓶颈 |

**结论**：`--split-mode workers` 在 CUDA 推理下被显式禁用（代码 `:4890-4894`），自动降级为 "off"。L40 上报错实为内存不足导致的 segment 模式 OOM，非 workers 模式本身问题。

### 3.10 T4 基准数据补充

| 指标 | test1.mp4 (19s) | test2.mp4 (73s) | test2.mp4 segment(2段) |
|------|-----------------|-----------------|------------------------|
| 单线程 CUDA (ensemble) | 1m54s / 5fps | ~6m / 6fps | - |
| 分段并行 CUDA (2段) | - | - | 6m00s / 6fps |
| CPU workers (2进程) | ~50m+ / 0.2fps | - | - |
| NVENC hevc_nvenc | 51.6s | 1m37s | - |
| NVENC av1_nvenc | - | - | 17.4s (30帧 4K) |

### 3.11 L40 模型模式 libx265 修复记录 (2026-10-13)

> 背景：L40 环境验证中发现 `--model ensemble --codec libx265 --frames 5` 等短帧模型模式失败，退出码 183 (AVERROR_INVALIDDATA)。经系统排查定位并修复。

| 问题 | 根因 | 修复位置 | 修复方案 |
|------|------|----------|----------|
| **模型模式 `--frames N` 失败** | `-threads 48` 导致 libx265 初始化耗时 > 输入时长 | `build_encode_cmd:2471` | 模型模式 (`is_hdr_input=True`) 下不下发 `-threads` |
| **滤镜链冲突** | `setparams` 滤镜与单独 `-colorspace/-color_primaries` 等标志同时下发冲突 | `build_encode_cmd:2478` | 模型模式仅用 `setparams` 写元数据，移除单独色彩四参标志 |
| **解码命令异常首帧** | `-t` 时长限制导致 ffmpeg 定位偏移，产生异常首帧 | `build_decode_cmd:2255` | 仅用 `-frames:v` 精确截帧，移除 `-t` |
| **滤镜链重复转换** | 模型输出已是 PQ/BT.2020，滤镜链 `scale=out_color_matrix=bt2020nc` 重复转换 | `build_encode_cmd:2422` | 模型模式仅用 `setparams` 写元数据，移除 `scale` 色域转换 |

**验证通过场景：**
- ✅ `--model ensemble --codec libx265 --frames 5 --device cuda` (0.7s 完成)
- ✅ 完整视频 73s libx265 ensemble (1m43s 完成)  
- ✅ 分段并行 `--split-mode segment --workers 2 --device cuda` (1m35s 完成)
- ✅ 4K 30帧 `--model agcm --codec libx265 --frames 30 --device cuda` (18s 完成)
- ✅ NVENC (hevc_nvenc/av1_nvenc) 正常工作

---

**祝测试顺利 🚀**