# `bench_sdr_to_hdr` T4 GPU 实测 Prompt

## 总体约束
- **三阶段递进**：CPU → T4 → L40；每个阶段在上一阶段 **所有测例 OK** 才可启动。
- **相对路径**：所有输入输出路径相对于项目根目录 `${ROOT}`（即 `input_videos` 等），在 WSL / Linux / Git‑Bash 下均可直达，无需修改。
- **素材选择**（见各阶段小节）；同一素材在多阶段复用，仅 `--frames` / `--tile` 等参数会随阶段变化。

---

## 阶段 1 – CPU 基准
- **命令示例**：
  ```bash
  python3 Accessory/probe/bench_sdr_to_hdr.py --selftest
  python3 Accessory/probe/bench_sdr_to_hdr.py --env
  python3 Accessory/probe/bench_sdr_to_hdr.py -i input_videos/test1.mp4 --layers l1,l3 --crf-ref 21 --repeats 3
  ```
- **素材**：`test1.mp4`, `new5_raw.mp4`, `CBeebies - Do You Know - Make a Disco Ball with Maddie.mp4`
- **出口**：`selftest` / `env` 通过 且 所有 L1/L3 组合 **OK** → 进入阶段 2。

---

## 阶段 2 – T4 GPU 实测
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

---

## 阶段 3 – L40（可选，若有更强 GPU）

- **命令结构**：同阶段 2，仅 `--device cuda` 保持不变，可增大 `--workers` / `--threads` 与 `--frames`。
- **额外测例**：
  - `--workers 8` / `--threads 8`（检查并发上限）
  - `--frames 30 / 120`（测试帧限对 tile 的影响）
  - `--vmaf` 大范围组合（如 `l1.libx265,l1.libaom-av1,l1.librav1e`）
- **出口**：所有测例 **OK** 或者 **EXPECTED_FAIL**（仅 OOM） → 基准完成，生成最终报告。

---

## 📌 执行要点回顾
| 步骤 | 关键检查 |
|------|----------|
| **阶段 1** | `selftest` / `env` 通过；L1/L3 全 `OK` |
| **阶段 2** | T4 探测通过；NVENC / 音频 / 字幕 行为符合文档；VMAF 门禁通过；`–container .mkv` 路径靠 stdout “输出文件” 行判定 |
| **阶段 3** | L40 复现 T4 结果，或记录 OOM 边界；若全部 OK 则基准完成，生成最终报告 |

---

> **记录**：在每个阶段结束后，将 `Accessory/probe/bench_sdr_to_hdr.py` 的 `--json` 输出（或手动记录 `wall_min`、`out_bytes`、`actual_codec` 等）写入本项目的 `memory/` 目录，便于后续对抗审查与回溯。

> **提示**：若在任何阶段出现 `FAIL` / `SKIP`，先检查对应的 **告警文案** 与 **`path_ok`** 是否匹配；必要时回退至上一阶段重跑，确保数据可靠后再递进。

---

**祝测试顺利 🚀**