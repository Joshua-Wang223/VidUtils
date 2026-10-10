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
- `actual_codec` 符合预期（NVENC 真跑还是降级、硬解真用还是回退）
- `path_ok` 为真
- 分段并行/多进程推理路径生效确认（stdout 抓 `分段并行：N 段` / `单解码 + N 推理进程`）
- 动态并行生效（观测 `--workers` 自动调整与内存/CPU 占用）
- → 进入阶段 3（L40）

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

**祝测试顺利 🚀**