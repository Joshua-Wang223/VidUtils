# SDR→HDR 全流程测试立项 Prompt

## 总体约束
- **三阶段递进**：CPU → T4 GPU → L40 更高端 GPU；每个阶段在上一阶段 **所有测例 OK** 才可启动。
- **相对路径**：所有输入输出路径相对于项目根目录 `${ROOT}`（即 `input_videos` 等），在 WSL / Linux / Git‑Bash 下均可直达，无需修改。
- **素材选择**（见各阶段小节）；同一素材在多阶段复用，仅 `--frames` / `--tile` / `--codec` 等参数会随阶段变化。
- **本提案包含两类测试**：
  1. **Bench 脚本测试**——`Accessory/probe/bench_sdr_to_hdr.py` 的层层递进实测（已在旧提案中列出）。
  2. **本体脚本测试**——`convert_sdr_to_hdr.py` 的全流程实测，含 GPU（T4/L40）加速、编码器矩阵、分段并行、字幕、音频等高级功能。

---

## 1️⃣ 阶段 1 – CPU 基准（无模型）

### 1.1 Bench 脚本测试（已在旧提案中列出）
- **命令示例**：见旧提案 `阶段 1 – CPU 基准`。
- **素材**：`test1.mp4`, `new5_raw.mp4`, `CBeebies - Do You Know - Make a Disco Ball with Maddie.mp4`。
- **出口**：`selftest` / `env` 通过 且 所有 L1/L3 组合 **OK** → 进入阶段 2。

### 1.2 本体脚本 `convert_sdr_to_hdr.py` 实测
- **目的**：验证无模型下的编码链路（解码→PQ转换→编码→容器封装），确认 `--no-model` 标志下的行为与 `--dry-run` 计划一致。
- **运行脚本**：
  ```bash
  # 探测环境与能力
  python3 convert_sdr_to_hdr.py --env
  ```
- **主体测试**（使用 `--no-model`，不需要 torch / 模型仓库）：
  ```bash
  # L1 编码器×质量等幅对比（等质量锚定 crf-ref=21）
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec libx265 --crf-ref 21 --no-model --overwrite
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec libsvtav1 --crf-ref 21 --no-model --overwrite
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec libaom-av1 --crf-ref 21 --no-model --overwrite
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec librav1e --crf-ref 21 --no-model --overwrite
  ```
- **关键指标**：
  - `crf_sent`——真正下发的原生质量值（对抗审查必看：ref=21 并非所有编码器恒等，libx265→20.74、libsvtav1→28.96 等）。
  - `quality_axis`——下发的是哪根轴：`crf` / `cq` / `qp`（数字含义不同，不能混列）。
  - `out_bytes` 与 `_fmt_size_delta`——体积变化，便于看到音频/编码差异。
  - `actual_path`——实际输出路径，避免 `–container` 猜测导致的假红。
  - `warnings`——告警文案（特别是“音轨偏短自动降级”相关）。
- **出口**：
  - 所有 4 编码器 **OK**，`crf_sent` 与 `quality_axis` 已填充且不为空。
  - 没有 “编码器不在 QUALITY_MAP 里会静默换算” 的情况。
  - `path_ok` 为真（实际路径=预期路径）。
  - → 进入阶段 2（T4 GPU）。

### 1.2.1 小结（阶段 1）
- `selftest` / `env` 通过。
- 4 编码器全部 `OK`，`crf_sent` 均已填充，`quality_axis` 均为 `crf`。
- `path_ok` 为真，无未检测的编码器降级。
- **门槛**：全部 OK → 进入阶段 2。

---

## 2️⃣ 阶段 2 – T4 GPU 实测

### 2.1 Bench 脚本测试（已在旧提案中列出）
- **前置检查**：
  ```bash
  python3 Accessory/probe/bench_sdr_to_hdr.py --env   # 确认 CUDA 可用
  ```
- **主体命令**（示例，逐一替换 `codec` / `split-mode` / `tile`）：
  ```bash
  # L1 编码器×质量
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec libx265 --crf-ref 21 --repeats 3
  
  # L2 执行路径 (segment)
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --split-mode segment --workers 4 --device cpu --repeats 2
  
  # L3 瓶颈分解 (VMAF)
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --vmaf libx265,libsvtav1 --vmaf-frames 60 --vmaf-peak 100 --repeats 1
  
  # C 并发/线程阶梯
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --threads 4 --split-mode off --repeats 2
  
  # D NVENC (T4 有卡)
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec hevc_nvenc --crf-ref 21 --fallback-policy auto --repeats 2
  
  # E 解码/容器/音频
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --decode cuda --container .mkv --audio copy --repeats 2
  
  # G 4K 分块 (--frames 60)
  python3 Accessory/probe/bench_sdr_to_hdr.py -i test_materials_4k/your4kfile.mp4 \
      --frames 60 --tile 1024 --repeats 1
  ```
- **素材**：
  - 基准 720p：`test1.mp4`, `new5_raw.mp4`
  - 长冒烟：`大红狗 Clifford the Big Red Dog DVDR.58.mp4`
  - 暗场：从 `Earth.at.Night.in.Color.S02E03.Kangaroo.Valley.2160p_T100_2xfps.mp4` 截取 10 s 片段 → `tmp_dark_4k.mp4`
  - 字幕：`The.Creature.Cases.S01E01.The.Mystery.on.the.Monsoon.Express.mkv`，截取 `ffmpeg -i ... -ss 28 -t 20 tmp_sub.mkv`
- **出口**：
  - T4 所有核心组合 **OK**，VMAF 门禁通过，`actual_codec` 符合预期（无意外降级），`path_ok` 为真 → 进入阶段 3。

### 2.2 本体脚本 `convert_sdr_to_hdr.py` 实测
- **目的**：在 T4 有模型的情况下，验证完整的编码链路（解码→NN推理→编码→封装），确认 `--model-repo` / `--device cuda` 等参数的行为，并对照 `--dry-run` 计划校验。
- **运行脚本**：
  ```bash
  # 探测环境与能力
  python3 convert_sdr_to_hdr.py --env
  
  # 主体测试（真跑模型，非 --no-model）
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec libx265 --crf-ref 21 --device cuda --overwrite
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --codec hevc_nvenc --crf-ref 21 --fallback-policy auto --device cuda --overwrite
  python3 convert_sdr_to_hdr.py -i input_videos/test1.mp4 \
      --decode cuda --container .mkv --audio copy --device cuda --overwrite
  ```
- **关键指标**（同阶段 1，但含模型开销）：
  - `crf_sent`——真正下发的原生质量值（含模型推理后的数值变化）。
  - `quality_axis`——下发的是哪根轴：`crf` / `cq` / `qp`。
  - `out_bytes` 与 `_fmt_size_delta`——体积变化（模型推理后可能因码率控制不同而变化）。
  - `actual_codec`——产物 ffprobe 的 `stream.tags=encoder`（判断是否真的跑了 NVENC 还是被降级为 libx265）。
  - `path_ok`——实际输出路径是否等于预期路径（避免 `–container .mkv` 的假红）。
  - `warnings`——告警文案（特别是“编码器 … 不可用 … 已自动降级”为 … libx265）。
  - `engine`——模型推理耗时与峰值 fps。
- **出口**：
  - T4 有卡时 `hevc_nvenc` / `av1_nvenc` **可用**（`_probe_encoder` 返回 True）。
  - 同组其它编码器（libx265, libsvtav1…）同样 OK，但耗时对比可见。
  - `–container .mkv` 路径要靠 stdout 的 “输出文件” 一行判定，不得按扩展名猜测。
  - 音频 `copy` 在有音轨时会被 **自动降级** 为 aac（告警已出现），`none` 保持无音轨。
  - VMAF 有效（`n_subsample=1`，曲线无压平），用于质量门禁。
  - `–frames` 与 `–split-mode segment` 互斥，跑完后 `expect_path` 标记正确。
  - 模型推理耗时可接受（峰值 RSS 在合理范围），未触发 OOM killer。
- **出口**：
  - T4 上所有核心组合 **OK**，VMAF 门禁通过，`actual_codec` 符合预期，`path_ok` 为真 → 进入阶段 3（L40）。

### 2.2.1 小结（阶段 2）
- T4 探测通过；NVENC / 音频 / 字幕 行为符合文档；VMAF 门禁通过；`–container .mkv` 路径靠 stdout “输出文件” 行判定。
- **门槛**：全部 OK → 进入阶段 3。

---

## 3️⃣ 阶段 3 – L40（可选，若有更强 GPU）

- **命令结构**：同阶段 2，仅 `--device cuda` 保持不变，可增大 `--workers` / `--threads` 与 `--frames`。
- **额外测例**：
  - `--workers 8` / `--threads 8`（检查并发上限）。
  - `--frames 30 / 120`（测试帧限对 tile 的影响）。
  - `--vmaf` 大范围组合（如 `l1.libx265,l1.libaom-av1,l1.librav1e`）。
- **出口**：所有测例 **OK** 或者 **EXPECTED_FAIL**（仅 OOM） → 基准完成，生成最终报告。

---

## 📌 执行要点回顾
| 步骤 | 关键检查 |
|------|----------|
| **阶段 1** | `selftest` / `env` 通过；4编码器全 `OK`；`crf_sent` / `quality_axis` 已填充；`path_ok` 为真 |
| **阶段 2** | T4 探测通过；NVENC / 音频 / 字幕 行为符合文档；VMAF 门禁通过；`–container .mkv` 路径靠 stdout “输出文件” 行判定 |
| **阶段 3** | L40 复现 T4 结果，或记录 OOM 边界；若全部 OK 则基准完成，生成最终报告 |

---

> **记录**：在每个阶段结束后，将 `convert_sdr_to_hdr.py` 与 `Accessory/probe/bench_sdr_to_hdr.py` 的 `--json` 输出（或手动记录 `wall_min`、`out_bytes`、`crf_sent`、`actual_codec` 等）写入本项目的 `memory/` 目录，便于后续对抗审查与回溯。

> **提示**：若在任何阶段出现 `FAIL` / `SKIP`，先检查对应的 **告警文案** 与 **`path_ok`** 是否匹配；必要时回退至上一阶段重跑，确保数据可靠后再递进。

---

**祝测试顺利 🚀**