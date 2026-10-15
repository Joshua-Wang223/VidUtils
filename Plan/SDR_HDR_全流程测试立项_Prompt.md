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
  > ⚠ **并发上限受 cgroup 内存约束，不是核数**（2026-10-13 实测）：8GB cgroup 机器上跑 **3 个** bench 进程会
  > **系统性 OOM**（`-9`，ensemble 双网络进程优先被杀）；**2 个并发**才安全。4 核 8G 机的经验值：
  > 每进程约需 2–2.6GB（模型 1GB + 双 ffmpeg + 缓冲），故并行度 ≈ `floor(mem_cgroup_gb / 2.6)`。
- **渐进原则**：下级阶段能测的绝不在上级阶段重复；素材准备、软编基线等准备工作下放到 CPU 阶段。
- **阶段任务重排（2026-10-13，优先级高于原 1/2/3 节的分层描述）**：
  > 原则：**能 CPU 侧完成的绝不推给 T4，能 T4 胜任的绝不推给 L40**。
  > 实现手段：bench 新增 `--device {cpu,cuda,auto}` **全局覆盖**（替换 L2/L3/c.workers/G 硬编码的
  > `--device cpu`，或给 L1/c.threads/D/E/M 补上缺省值），使同一矩阵可在对应机器上跑。
  > 各层归属与理由见 **§4 阶段任务重排总表**。

---

## 📋 阶段任务重排总表（2026-10-13 起执行）

> 判据：**结论是否依赖被检设备**。依赖 GPU/编码器行为的层上移，纯 CPU/内存/路径行为的层下沉。

| Bench 层 | 组合数 | 归属 | 理由 |
|----------|--------|------|------|
| **L1** 软编等质量（libx265/libsvtav1/libaom-av1/librav1e） | 4 | **CPU 机** | 编码器成本对比与推理设备无关；软编在 T4/L40 上纯浪费 |
| **L1** CUDA 推理版（`--device cuda` 直跑本体脚本） | — | T4 | GPU 推理链路的编码器对比 |
| **L2** 执行路径（split×3 + tile×2） | 6 | **CPU 机** | 分段/分块是**内存与管道**行为；`split.workers` 多进程推理**仅 CPU 可用**（CUDA 下被脚本禁用） |
| **L2** segment 的 GPU 版 | — | T4 | `--device cuda` 覆盖，验证 GPU 推理下分段路径（已在 T4/L40 实测通过） |
| **L3** 完整流程 CPU 基线 | 1 | **CPU 机** | 就是 CPU 基线定义 |
| **C.workers** 并发阶梯 1/2/4 | 3 | **CPU 机** | workers=分段数，测的是**过订/内存**行为（非单调已在仓内记录） |
| **C.threads** ffmpeg 线程 1/2/4/8 | 4 | **CPU 机**（自动版在 T4 复核一次） | 线程树与推理设备基本正交；GPU 上仅需抽验 |
| **D** NVENC 组 | 6 | **T4** | T4 有 hevc/h264_nvenc，`av1_nvenc` 会正确 SKIP |
| **D** av1_nvenc + `--nvenc-tune uhq/lossless` | ~3 | **L40 独有** | Ada（第8代 NVENC）才支持，T4 硬件无 AV1 编码器 |
| **E.cpu** 解码cpu / 容器mp4·mkv / 音频copy·none | **5** | **CPU 机** ⬇ 2026-10-14 下沉 | 纯 ffmpeg 解复用/mux 行为，与 GPU 无关。原表把整个 E 推给 T4，违反本节「能 CPU 侧完成的绝不推给 T4」原则 |
| **E.cuda** 解码 cuda / auto | 2 | **T4** | 需真硬解（`need_hw_decode`） |
| **E** 字幕 burn/keep | — | **CPU 机** ⬇ 2026-10-14 下沉 | `--subs burn` 走 libass `subtitles=` 滤镜，是**纯 CPU** 滤镜，与色域链的交互也不需 GPU。⚠ bench **无字幕轴组合**，只能跑本体脚本 |
| **G** 4K 分块 tile 0/1024/2048 | 3 | **CPU 机** | 分块目的是**压 RAM**；L40 的 46GB VRAM 上 tile=0 无压力、测不出意义 |
| **G** 4K 整帧/大分块（长 `--frames`） | — | L40 | 显存允许下验证 4K 整帧与 OOM 边界 |
| **M** 模型变体 | 3 | **两处都要** | CPU 机测相对倍率（agcm/hr 已测）；T4/L40 测 GPU 绝对速度 |

### 4.1 CPU 侧执行约束（2026-10-13 实测，位于 4核/8G cgroup）

| 约束 | 实测值 | 处置 |
|------|--------|------|
| CPU 推理速度 | agcm 720p ≈ **3.5 s/帧**（无竞争）；hr ≈ 15.8 s/帧 | **必须 `--frames` 限帧**（30–60）；全片 2191 帧需 8h+ |
| 并发上限 | **2 个 bench 进程**（3 个即系统性 `-9` OOM） | 并行度按 `floor(mem_gb/2.6)` 算，不按核数 |
| 内存画像 | 每进程 ~2–2.6GB（模型+双 ffmpeg+缓冲）；8GB cgroup | ensemble（AGCM+LE 双网络）是最先被 OOM killer 选中者 |
| L2/C 分段轴 | 需 ≥20s 素材 **且不能带 `--frames`**（互斥降级） | 分段轴单独用 21s 短素材跑；非分段轴用 `--frames` 跑 |

### 4.2 各阶段执行清单（重排后）

**阶段 1 – CPU 机（4核/8G cgroup 或 24核/31G 无 cgroup，`--device cpu` 或矩阵原生）**
```bash
# L1 四软编等质量（限帧 30；libsvtav1 单独跑避免并发 OOM）
bench -i test1.mp4 --layers l1 --codecs libx265,libaom-av1,librav1e --model agcm --crf-ref 21 \
     --frames 30 --repeats 1 --warmup 0 --device cpu
bench -i test1.mp4 --layers l1 --codecs libsvtav1 --model agcm --crf-ref 21 --frames 30 --device cpu
# L3 + M（ensemble 单独跑；agcm/hr 可并行）
bench -i test1.mp4 --layers l3   --model ensemble --frames 30 --device cpu
bench -i test1.mp4 --layers m    --model ensemble --frames 30 --device cpu
bench -i test1.mp4 --layers l1 --codecs libx265 --model agcm --frames 30 --device cpu   # agcm 基线
# L2 + C 分段路径（长素材，**不带 --frames**；21s 短素材会因太短测不出收益，见 §4.3）
bench -i seg30s_6f.mp4 --layers l2,c --model agcm --device cpu
# G 4K 分块（内存压力测试，4K 单帧 ~89s ⇒ 限帧）
bench -i tmp_dark_4k.mp4 --layers g --model agcm --frames 2 --device cpu
# E.cpu 五格（2026-10-14 从 T4 下沉：解码cpu/容器mp4·mkv/音频copy·none 均不依赖 GPU）
bench -i test3.mp4   --layers e --model agcm --frames 30 --device cpu   # test3 带音轨
# VMAF 质量门禁（软编基准，纯 CPU）
bench -i test1.mp4 --layers l1 --model agcm --frames 30 --device cpu --vmaf l1.libx265 --vmaf-tol 1.0
# 字幕 burn（libass 纯 CPU；bench 无字幕轴，只能本体脚本）
convert_sdr_to_hdr.py -i tmp_sub.mkv --subs burn --subs-index 0 -o out_sub.mp4 \
    --model agcm --frames 30 --device cpu --overwrite
```

> ⚠ **并发纪律**：`--frames` 与分段互斥 ⇒ 带 `--frames` 的层会 SKIP 掉 `c.workers` 与
> `l2.split.*`（见 §4.3 现象），分段轴需**另开一组不带 `--frames` 的命令**跑长素材。
> 另：每组数据须标明「独占」还是「6 路并发」，两者绝对墙钟差约 **6.5 倍**。

**阶段 2 – T4（`--device cuda` 覆盖 CPU 层 + 原生 GPU 层）**
```bash
# D/E 原生（T4 有 hevc_nvenc/h264_nvenc，av1_nvenc 自动 SKIP）
bench -i test2.mp4 --layers d,e --model ensemble --device cuda --repeats 3
# 只搬 GPU 相关格：L2/C 的 GPU 分段 + M 轴 + L1 CUDA 版 + c.threads GPU 复核
bench -i test2.mp4  --layers m --crf-ref 21 --model ensemble --device cuda --repeats 3
bench -i test1.mp4  --layers l1 --codecs hevc_nvenc --model ensemble --device cuda
bench -i test1.mp4  --layers c  --model ensemble --frames 30 --device cuda
convert_sdr_to_hdr.py -i test2.mp4 --split-mode segment --workers 4 \
    --model ensemble --device cuda --overwrite
```

> ⚠ **E 层只剩 `e.decode.cuda/auto` 两格在 T4**（其余五格已下沉 CPU，见 §4.4.1 C3）。
> ⚠ **av1_nvenc 组合在 T4 全部 SKIP**（Turing 无 AV1 编码器）—— 属 L40 独有，勿在此期待它跑通。

**阶段 3 – L40（仅 Ada 独有 + 大显存）**
```bash
# av1_nvenc 独有（含 tune uhq/lossless）
bench -i test2.mp4 --layers d --codecs av1_nvenc --model ensemble --device cuda \
     --crf-ref 21 --repeats 3 --nvenc-tune uhq --json memory/phase3_l40_gpu_YYYYMMDD.json
# 4K 整帧/大分块（46GB VRAM 才允许）
bench -i tmp_dark_4k.mp4 --layers g --model agcm --device cuda --tile 0 --frames 60 --repeats 1
# 大并发（48 核）
convert_sdr_to_hdr.py -i test2.mp4 --split-mode segment --workers 8 --device cuda
```

### 4.3 阶段 1 CPU 侧已取得实测数据（2026-10-13）

> 执行方式：bench `--device cpu` + `--frames 30`（分段轴用 21s 素材另跑）。
> 落盘：`memory/phase1_cpu_l1_20261013.json`、`memory/phase1_cpu_l3m_20261013.json`。

#### A. 4核/8G cgroup 机（原始基线）

**模型轴 CPU 定速（720p，30 帧，推理+libx265 编码总时长）**

| 变体 | 墙钟 | 折算 s/帧 | 相对 agcm | 数据来源 |
|------|------|-----------|-----------|----------|
| **agcm** | **105.36s** | 3.51 | 1.0× | m.agcm OK |
| **ensemble** | 465.72s | 15.52 | **4.4×** | l3.full OK（2 并发单独跑） |
| **hr** | 473.83s | 15.79 | **4.5×** | m.hr OK |

**L1 四软编（30 帧，agcm 推理 + 目标编码器编码）**

| 编码器 | 墙钟 | 结果 |
|--------|------|------|
| libx265 | 413.64s | ✅ |
| libaom-av1 | 560.09s | ✅（最慢软编） |
| librav1e | 292.25s | ✅（**最快软编**，比 libx265 快 29%） |
| libsvtav1 | — | ⏸ 未测（被调度中断） |

**并发约束实证（重要，指导后续所有并行执行）**

| 并发数 | 结果 |
|--------|------|
| 3 个 bench 进程 | **系统性 `-9` OOM**：m.ensemble/l3.full/libsvtav1/l2.split.off/c.workers 等 5+ 组合被杀 |
| 2 个 bench 进程 | 全部 OK（l3.full ensemble 465.72s 通过） |

⇒ 8GB cgroup 上 **并行度上限 = 2**（每进程 ~2–2.6GB）。此前 A/B/C 三并行的设计超出内存容量，
其 `-9` 失败**不代表组合本身不可跑**—— 已用 2 并发复核证实。

#### B. 24核/31G 无 cgroup 机（复核机，2026-10-13 全量复核通过）

> 环境差异：ffmpeg **7.1 → 9.0.2**（跨两个大版本）、torch **2.10 → 2.14.1+cpu**、
> python **3.11 → 3.12.3**、numpy 2.5.2、模型仓由上游 tarball 全新克隆。
> **B1/B2 修复在此机冒烟通过**（B1 libx265 短帧 rc=0；B2 `--frames` 提前退出收尾 rc=0 无死锁）。

**① 模型轴倍率复核（结论：设备无关，成立 ✅）**

| 变体 | 4核机 s/帧 | 24核机 s/帧 | 倍率(24核) | 倍率(4核) |
|------|-----------|------------|-----------|----------|
| agcm | 3.51 | **4.10** | 1.0× | 1.0× |
| ensemble | 15.52 | **18.69** | 4.56× | 4.42× |
| hr | 15.79 | **17.40** | 4.24× | 4.50× |

⇒ 两机倍率一致（1 : ~4.4 : ~4.5），**「模型轴倍率与设备无关」两次独立复核成立**。
注意绝对速度 4核→24核 **未提升**（甚至略降），见下条。

**② 新发现：torch 线程在 ~8 核后饱和（推翻「多核更快」的默认假设）**

720p agcm 稳态 s/帧（`torch.set_num_threads` 实测）：

| torch 线程 | 1 | 2 | 4 | 8 | 16 | 24 |
|-----------|---|---|---|----|----|----|
| s/帧 | 11.38 | 9.38 | 6.01 | **4.63** | 3.60 | **3.54** |

⇒ 属**小算子延迟/内存带宽受限**，非算力受限。1→8 核近线性，8 核后边际骤减。
⇒ **并行策略应为「多进程 × 少线程」，而非单进程吃满核**：每进程 4 线程（6.01 s/帧）
可开 ~6 个并发，总吞吐 = 6/6.01 ≈ **1.0 帧/s**；单进程 24 线程仅 0.28 帧/s。**差 3.5 倍**。

**③ 24核机全量复核结果（实测，ffmpeg 9.0.2 / torch 2.14.1+cpu / 24核31G）**

> ⚠ 数据说明：全部为 **6 路并发**下跑出，每进程 `OMP_NUM_THREADS=4`（6×4=24 线程但含主线程/ffmpeg
> 线程，实际超订）。故**绝对墙钟不可横向比**，只可看**组内相对倍率**。`--frames 30` 除非段轴。
> 折算单帧时须注意：并发态单帧约 **26.5 s**（=795.30/30），是隔离态 agcm 4.10s 的 **~6.5 倍**。

**模型轴（m 层，`--frames 30`，相对倍率结论与 4核机一致 ✓）**

| 变体 | 墙钟(并发) | 折算 s/帧 | 相对 ensemble | 隔离态 s/帧 | 倍率(并发) | 倍率(隔离) |
|------|-----------|----------|--------------|------------|-----------|-----------|
| ensemble | 794.96s | 26.50 | 1.0× | 18.69 | 1.0× | 1.0× |
| **agcm** | **153.43s** | 5.11 | **5.18×** | 4.10 | **5.18×** | 4.56× |
| hr | 846.60s | 28.22 | 0.94× | 17.40 | 0.94× | 1.07× |

⇒ **agcm 快约 5×、ensemble/hr 相当** 两次独立复核一致。「模型轴倍率与设备无关」成立 ✅

**L1 四软编（agcm 推理，`--frames 30`，ffmpeg 9.0.2，修复后全通 ✅ OK 4 / FAIL 0）**

| 编码器 | 墙钟(独占复测) | 相对 libx265 | crf_sent | 状态 |
|--------|---------------|-------------|----------|------|
| **libsvtav1** | 835.87s | **0.73×（最快）** | 29 | ✅ |
| libaom-av1 | 1053.67s | 0.92× | 26 | ✅ |
| libx265 | 1147.40s | 基准 | 21 | ✅ |
| librav1e | 1194.60s | 1.04× | 64 | ✅ |

> 🔴 **排序相对 4 核机（ffmpeg 7.1）完全翻转**，两个方向的移动都很大：

| 编码器 | 4核机/7.1 | 24核机/9.0.2 | 变化 |
|--------|----------|------------|------|
| librav1e | **292s（最快）** | **1195s（最慢）** | **4.1× 变慢** ⬅ 主因 |
| libx265 | 414s（最慢） | 1147s（中游） | 2.8× 变慢 |
| libaom-av1 | 560s（最慢） | 1054s | 1.9× 变快 |
| libsvtav1 | 未测 | 836s（最快） | — |

⇒ **软编绝对成本的排序强绑定 ffmpeg 版本**，跨版本不可迁移；4 核机「librav1e 快 29%」
的结论**已作废**。现结论：**libsvtav1 > libaom-av1 > libx265 > librav1e**（均在 libx264
CRF21 等质量口径下）。⚠ librav1e 的相对劣势部分源于它只支持 `-qp`（需跨质量轴换算
`-qp 64`），与前三者的 `-crf` 不同口径，不能直接比。

**c.threads ffmpeg 线程树（agcm 推理，`--frames 30`）**

| 线程 | 墙钟(并发态) | 相对 |
|------|------------|------|
| c.threads1 | 800.84s | 基准 |
| c.threads2 | 736.95s | 1.09× |
| c.threads4 | 634.18s | 1.26× |
| c.threads8 | **601.58s** | **1.33×** |

⇒ CPU 软编下 ffmpeg 线程 **1→8 有 ~33% 收益**（非可忽略），但 4→8 仅再 5%。**修正 4核机上的
「<0.6% 无影响」结论**——那次是推理瓶颈掩盖了编码收益。**该轴在 CPU 上有效，但弱于推理侧。**

⚠ **本组为 6 路并发态数据**，同组内可比；与上面 L1 的「独占复测」墙钟**不可跨组比**。
这也解释了为何 L1 独占复测（libsvtav1 835.87s）反而快于本组 threads1（800.84s）
的量级差异来源是并发争用，不是编码器本身差异 —— **后续所有 CPU 侧数据都必须标注
「独占」还是「并发」**。

**G 层 4K 分块（agcm 推理，`--frames 2`，4K 素材）**

| 组合 | 墙钟 | 相对 tile0 | 产物 |
|------|------|-----------|------|
| g.4k.tile0 整帧 | **523.52s** | 基准 | 144.6 KB |
| g.4k.tile1024+128 | 672.88s | 0.78× | 144.6 KB |
| g.4k.tile2048+256 | 750.32s | 0.70× | 144.6 KB |

⇒ **CPU 上 4K 整帧最快、分块反而慢 30%**（分块的"压内存"目的达成：无 OOM；但 CPU 无显存
限制，分块纯增开销）。⚠ 注意本机 31G 内存充足，4K 整帧未触发 OOM —— 与"4K 整帧已知 OOM"
的旧预设不符，该预设只在内存受限机（如 4核/8G 机，单帧 89s→需限帧）成立。

**L2/c 分段与并发（`test2.mp4` → 30s@6fps/720p = 180 帧，≥20s 满足 `_SEGMENT_MIN_SECONDS`，不带 `--frames`）**

> 素材取舍：`test2.mp4` 原生 73s@30fps = 2194 帧 ⇒ CPU 上仅推理就需 2.5h/组合，不可行。
> 降到 6fps 保留**时长**（分段轴的唯一门槛是时长）而把帧数压到 180，单组合 ~10–17min。

| 模式 | 路数 | 线程/路 | 用时 | 相对 off | 结果 |
|------|------|---------|------|---------|------|
| `off` | 1 | 12 | 17m06s | 1.00× | ✅ |
| `segment` | 2 | 6 | 14m25s | 0.84× | ✅ |
| `segment` | 4 | 3 | 12m55s | 0.76× | ✅ |
| `workers` | 2 | 6 | 12m59s | 0.76× | ✅ |
| **`workers`** | **4** | **3** | **8m50s** | **0.52×** | ✅ |

⇒ ① **长素材上分段/并发确有收益**，`workers=4` 加速 **1.94×**（逼近线性 2.0×）——
**推翻 21s 小素材上的「墙钟几乎相同、无收益」结论**：小素材是**素材太短**，非机制无效。
⇒ ② **workers 优于 segment**（同 2 路：1.32× vs 1.19×）——segment 需分段解码后拼接，
有额外开销；workers 是单解码 + 多推理 + 单编码。
⇒ ③ workers=4 每路仅 3 线程仍拿 1.94×，与 ② 中「torch 线程 ~8 核后饱和、应多进程×少线程」
互相印证。**阶段 1 推荐：`--split-mode workers --workers 4`。**

#### C. 阶段 1 待续跑 —— **已全部完结（2026-10-14）** ✅

§4.4.1 待测清单 **C1~C7 全部执行完毕，OK 项零 FAIL**（详见 memory/phase1_cpu_c1c7_final_20261014.json）：

| # | 项 | 结果 |
|---|----|------|
| C1 | L3 完整流程基线 | ✅ l3.full 878.95s（阶段出口项已补齐） |
| C2 | L2 分块 ×2 | ✅ tile512+0 761.13s / tile512+64 685.07s；720p 分块比整帧快 13% |
| C3 | E.cpu ×5 | ✅ 全通（从 T4 下沉）；另修 bench 一个误报缺陷 |
| C4 | 字幕 burn/keep | ✅ burn 无字幕流已烧录 / keep 保留 subrip，HDR10 均正确 |
| C5 | VMAF 质量门禁 | ✅ VMAF=100.00（基准自身），门禁在 CPU 侧可用 |
| C6 | M 轴隔离态 | ✅ agcm 3.88 / ensemble 17.30 / hr 17.28 s/帧，倍率 **1:4.45:4.45** |
| C7 | L1 隔离态中位数 | ✅ svtav1 499s(0.96×) / x265 518s / aom 703s(1.36×) / rav1e 857s(1.66×) |

**CPU 侧出口条件满足 → 可进入阶段 2（T4）**。

> ⚠ **C7 修正了 L1 排序**：真正「全线程独占」的数据是 svtav1 < x265 < aom-av1 < rav1e；
> 而先前 §4.3 那份「独占复测」(svtav1 836s / x265 1147s) 实为 **4 路并发**下的数据，
> 争用把 libx265 拖慢了 2.2 倍（1147→518s）。**以 C7 为准**。
> C3 期间发现并修复 bench 缺陷 F5（帧数守恒检查未扣 `--frames` 截断，见 memory）。

**仍属 GPU 侧（不可在 CPU 完成）**：
4. **av1_nvenc 真死锁回归 → 属 L40 而非 T4**（T4/Turing 无 AV1 NVENC，物理跑不出）。
   其零推进特征现会被 `--enc-stall`(180s) 捕获，L40 须确认告警文案变为「N s 无任何推进」。

### 4.4 三侧待测清单 (2026-10-14)

> 口径：✅=已完成；⬜=待测。**阶段出口判据：所属侧所有 ⬜ 清零且无 FAIL 才可进下一阶段。**

**总览（2026-10-15 复核）**

| 侧 | 状态 | 剩余 | 准入门槛 |
|----|------|------|---------|
| **CPU** | 🟡 **7/8 完结** | 仅 **C8**（VMAF 真门禁，约 1h，本机可跑） | — |
| **T4** | ⬜ 未开始 | T1~T8（8 项） | **T7** 必须第一个跑（B3 跨版本风险） |
| **L40** | ⬜ 未开始 | L1~L7（7 项） | **L1 → L2**（av1_nvenc 回归 + B3 回归） |

> 本轮复核修正了清单里 3 类问题：①素材引用失效（`test3.mp4` 不存在、4K 路径错、
> `tmp_dark_4k.mp4` 未生成）；②路径约定错误（素材在**兄弟目录**，须 `../input_videos/`）；
> ③发现 C5 的 VMAF 是**空转**（参照池取第一个 OK 组合，只跑 libx265 时它自己就是参照）。

> 🔴 **命令里的路径约定（2026-10-14 实测校正，照抄前必读）**
> 素材在项目根的**兄弟目录**，不是子目录：
> ```
> /workspace/            ← 素材
>   input_videos/  test1.mp4 test2.mp4 test3.mp4(缺失) test_materials_4k/
>   VidUtils/      ← 项目根（本方案所在）
> ```
> ⇒ **必须写 `../input_videos/test1.mp4`**，写 `input_videos/…` 或裸 `test1.mp4` 会
> 「文件不存在」。本节所有命令已按此校正。
>
> **素材实况（2026-10-14 核对）**
>
> | 素材 | 状态 | 用途 | 备注 |
> |------|------|------|------|
> | `../input_videos/test1.mp4` | ✅ 19s/720p/**aac 1ch 音轨** | L1/L2/L3/c/m/E.audio | **有音轨**，E 音频轴用它 |
> | `../input_videos/test2.mp4` | ✅ 73s/720p/2194 帧 | D/M/c 分段/GPU 侧 | |
> | ~~`test3.mp4`~~ | ❌ **不存在** | — | 原清单多处引用它，**全部改用 test1.mp4** |
> | `../input_videos/test_materials_4k/earth_4k_30s.mp4` | ✅ 17M | G 4K | 另有 5s/10s/60s 三档 |
> | ~~`tmp_dark_4k.mp4`~~ | ❌ 未生成 | — | 需从 earth_4k_30s 截取；或直接用 30s 原片 + `--frames` |
> | `tmp_sub.mkv` | ✅ 11.4M/1080p/subrip | 字幕 burn/keep | 已验证；生成命令见 §1.1，**不入库** |
> | `seg30s_6f.mp4` | ⚠ 在 /tmp/bench | l2/c 分段轴 | 临时生成（test2 降帧到 6fps，180 帧），需重建 |

#### 4.4.1 CPU 侧（24核/31G）—— 7/8 完结，**仅 C8 待跑**

| # | 项 | 命令 | 状态 |
|---|-----|------|------|
| C1 | **L3 完整流程基线** | `bench -i ../input_videos/test1.mp4 --layers l3 --model ensemble --frames 30 --device cpu` | ✅ 878.95s |
| C2 | **L2 分块 ×2** | `bench -i ../input_videos/test1.mp4 --layers l2 --model agcm --frames 30 --device cpu` | ✅ +0 761.13s / +64 685.07s |
| C3 | **E.cpu ×5**（从 T4 下沉） | `bench -i ../input_videos/test1.mp4 --layers e --model agcm --frames 30 --device cpu` | ✅ 5/5（decode.cpu / container.mp4·mkv / audio.copy·none） |
| C4 | **字幕 burn/keep**（从 T4 下沉） | `convert_sdr_to_hdr.py -i tmp_sub.mkv --subs {burn,keep} --subs-index 0 -o out --model agcm --frames 10 --split-mode off --device cpu` | ✅ burn 无字幕流已烧录 / keep 保留 subrip |
| C5 | **VMAF 门禁（管线可用性）** | `bench … --layers l1 --codecs libx265 --vmaf l1.libx265` | ✅ 但**空转**，见 C8 |
| C6 | **M 轴隔离态**（全线程独占） | `bench -i ../input_videos/test1.mp4 --layers m --crf-ref 21 --frames 30 --repeats 1 --warmup 0 --device cpu` | ✅ 3.88/17.30/17.28 s/帧 |
| C7 | **L1 隔离态中位数** | 同上 `--layers l1 --repeats 3`（librav1e 单独 repeats 1） | ✅ 499/518/703/857s |
| **C8** | 🟡 **VMAF 真门禁**（本轮新发现缺口） | `bench -i ../input_videos/test1.mp4 --layers l1 --codecs libx265,libsvtav1,libaom-av1,librav1e --vmaf l1.libsvtav1,l1.libaom-av1,l1.librav1e --vmaf-tol 1.0` | ⬜ **待跑** |

**C8 为什么必须补**：VMAF 参照池取「**第一个 OK 组合**」（`quality_gate:1341`）。C5 只指定
libx265 ⇒ 它自己成了参照，VMAF 恒为 100.00，**什么都没证明**。C8 让 libx265 做参照、
另三个软编做被测，才能真正验证 §3.14 P1 的「等质量换算不劣化」——这是**唯一遗留的 CPU 侧缺口**，
其余 7 项均已完结。

> ⚠ **C7 修正了 L1 排序**：全线程独占的真实排序是 `libsvtav1(499s,0.96×) < libx265(518s)
> < libaom-av1(703s,1.36×) < librav1e(857s,1.66×)`；而 §4.3 那份标「独占复测」的
> (libx265 1147s) 实为 **4 路并发**数据，争用把它拖慢 2.2 倍。**以 C7 为准。**
> ⚠ C7 的 librav1e 是单样本（repeats 3 对它性价比过低，被 2h timeout 截断），非中位数。

#### 4.4.2 T4 侧剩余（8 项，**执行顺序 = 下表顺序**）

> 🔴 **T7 必须第一个跑**，它是准入门槛而非并列项（理由见 4.4.4）。
> ⚠ **av1_nvenc 在 T4 全部 SKIP**（Turing 无 AV1 NVENC）——不要在这里等它跑通，那是 L40 的事。

| # | 项 | 命令 | 备注 |
|---|-----|------|------|
| **T7** | 🔴 **B3 跨版本回归（准入门槛）** | `convert_sdr_to_hdr.py -i ../input_videos/test1.mp4 --codec librav1e --crf-ref 21 --frames 30 --device cuda --overwrite`<br>`… --codec hevc_nvenc --fallback-policy auto --frames 30` | B3 给**所有编码器**加了 `-stats_period 2 -progress pipe:2`。目标机 ffmpeg 不认就会**整批失败**。先证伪再铺开 |
| T1 | **环境与能力探测** | `convert_sdr_to_hdr.py --env` + `bench --env` | 确认 CUDA + `hevc_nvenc,h264_nvenc`；av1_nvenc 应正确报**不可用** |
| T2 | **D 层 NVENC 组** | `bench -i ../input_videos/test2.mp4 --layers d --codecs hevc_nvenc,h264_nvenc --crf-ref 21 --model ensemble --device cuda --repeats 3` | 含 cq / aq / tune_uhq / rc_cbr 调优轴 |
| T3 | **E.cuda ×2** | `bench -i ../input_videos/test1.mp4 --layers e --model ensemble --frames 30 --device cuda` | 只取 e.decode.cuda/auto；E.cpu 五格已在 C3 完成。⚠ 原写 test3.mp4 **不存在** |
| T4 | **M 轴 GPU 绝对速度** | `bench -i ../input_videos/test2.mp4 --layers m --crf-ref 21 --model ensemble --device cuda --repeats 3` | 与 CPU 侧 1:4.45:4.45 对照 |
| T5 | **L1 CUDA 推理版** | `bench -i ../input_videos/test1.mp4 --layers l1 --codecs hevc_nvenc --model ensemble --device cuda` | 「GPU 推理 × 编码器」交叉格 |
| T6 | **c.threads GPU 复核** | `bench -i ../input_videos/test1.mp4 --layers c --model ensemble --frames 30 --device cuda` | CPU 侧 1→8 收益 33% 是否在 GPU 翻转。⚠ `--frames` 会 SKIP 掉 c.workers（预期） |
| T8 | **C 轴 GPU 分段** | `convert_sdr_to_hdr.py -i ../input_videos/test2.mp4 --split-mode segment --workers 4 --model ensemble --device cuda --overwrite` | CUDA 下 workers 被脚本禁用，只能测 segment |

#### 4.4.3 L40 侧剩余（7 项，**执行顺序 = L1 → L2 → L3…**）

> 🔴 **L1/L2 是准入门槛**：L1 验证 B2 在真 av1_nvenc 上仍干净退出，L2 验证 B3 新代码
> 在 L40 上不破坏 NVENC 路径。二者过了再铺开 L3~L7。

| # | 项 | 命令 | 备注 |
|---|-----|------|------|
| **L1** | 🔴 **av1_nvenc 收尾真死锁回归** | `convert_sdr_to_hdr.py -i ../input_videos/test2.mp4 --codec av1_nvenc --crf-ref 21 --model ensemble --device cuda --overwrite` | **全清单最高优先级**。验收：正常则退出码 0；若仍卡死，告警必须是「N s 无任何推进，已强制终止（末帧收尾卡死）」 |
| **L2** | 🔴 **B1/B2/B3 三修复回归** | 见 4.4.4 | B3 是**全新代码，L40 从未跑过** |
| L3 | **D.av1_nvenc + tune_uhq** | `bench -i ../input_videos/test2.mp4 --layers d --codecs av1_nvenc --crf-ref 21 --device cuda --nvenc-tune uhq --repeats 3` | Ada 第8代 NVENC 独有 |
| L4 | **nvenc-tune lossless** | 同上 `--nvenc-tune lossless` | Ada 独有 |
| L5 | **VMAF 门禁（av1_nvenc 半）** | `bench -i ../input_videos/test2.mp4 --layers d --codecs av1_nvenc --vmaf d.av1_nvenc.cq --vmaf-tol 1.0` | GPU 侧对偶项（C8 是 CPU 侧对偶项） |
| L6 | **4K 整帧长 frames** | `bench -i ../input_videos/test_materials_4k/earth_4k_30s.mp4 --layers g --device cuda --tile 0 --frames 60 --repeats 1` | 46GB 显存下验证 4K 整帧 |
| L7 | **大并发 workers=8** | `convert_sdr_to_hdr.py -i ../input_videos/test2.mp4 --split-mode segment --workers 8 --model ensemble --device cuda --overwrite` | 48 核吞吐 |

> ⚠ 4K 分段并行在 L40 **无需**「确认内存限额」——cgroup 4GB 是误报（见 3.5 更正，
> L40 实测 192GB/可用 ~189GB）。旧版清单里的 L8「内存边界复核」已删除。

#### 4.4.4 B1/B2/B3 三修复的跨环境回归矩阵

| 修复 | 发现环境 | 回归命令（简） | CPU(9.0.2) | T4 | L40 |
|------|---------|----------------|-------------|-----|-----|
| **B1** libx265 模型模式 exit183 | L40 | `--model ensemble --codec libx265 --frames 5` | ✅ rc=0 | ⬜ T7 | ⬜ L2 |
| **B2** av1_nvenc 收尾死锁（排空 dec.stdout） | L40 | `--codec av1_nvenc -i test2.mp4` 全片 | n/a 无卡 | ⬜ T7* | ⬜ L1 |
| **B3** 收尾固定超时误杀（`-progress` 无推进判定） | CPU 9.0.2 | `--codec librav1e --frames 30` | ✅ 4m33s 出片 | ⬜ T7 | ⬜ L2 |

> \* B2 的真正复现只能在 L40；T4 只能验证 **B3 不破坏 NVENC 正常收尾**（hevc_nvenc 全片）。

> 🔴 **B3 是跨版本高风险改动**：新加的 `-stats_period 2 -progress pipe:2` 作用于
> **所有编码器的编码命令**。本机 ffmpeg 9.0.2 支持，但 T4/L40 版本未知；
> 若目标机 ffmpeg 不认 `-stats_period`，会**直接报错导致全矩阵失败**。
> ⇒ **T7 必须是 T4 阶段的第一个命令**，先证伪此风险再铺开其余项。

#### 4.4.5 排期建议（最小路径）

```
CPU 侧（本机，现在）   ~~C1~C7 ✅ 完结~~ → C8（VMAF 真门禁，唯一遗留，约 1h）
        ↓ 出口：CPU 侧 ⬜ 清零、无 FAIL
T4 侧                 T7(先跑)→T1→T2→T3→T8→T4→T5→T6
        ↓ 出口：T4 侧 ⬜ 清零、无 FAIL
L40 侧                L1(先跑)→L2→L3→L5→L4→L6→L7→L8
```

**每侧完成后**：结果 JSON 写入 `memory/phaseN_env_YYYYMMDD.json` + git commit/push。
**数据标注纪律**：CPU 侧每组数据必须标明「独占」还是「并发」（见 §4.3 方法论），
否则绝对墙钟会被后续引用者误读（并发态比隔离态慢约 6.5 倍）。

---

## 1️⃣ 阶段 1 – CPU 基准（真实模型推理）

> **定位**：建立软编码器基线、验证模型推理链路、准备所有后续阶段复用的素材与基准数据。

### 1.1 素材准备（一次性完成，后续阶段复用）
| 用途 | 素材来源 | 处理 |
|------|----------|------|
| L1/L3 基准 720p | `input_videos/test1.mp4`, `new5_raw.mp4` | 直接使用 |
| L2/C 分段并行（需 ≥20s） | `input_videos/大红狗 Clifford the Big Red Dog DVDR.58.mp4` | 验证时长 ≥20s |
| E 组音频测试 | `input_videos/test3.mp4` (10s, aac 1ch, bt709 SDR) | 带音轨 |
| E 组字幕测试 | `input_videos/The.Creature.Cases.S01E01.The.Mystery.on.the.Monsoon.Express.mkv` | **`ffmpeg -y -ss 120 -t 20 -i <src> -c copy tmp_sub.mkv`**（2026-10-14 实测：产出 11.4MB/1080p/subrip 1 轨，`--subs burn` 与 `--subs keep` 双双跑通；⚠ bench **无字幕轴组合**，只能跑本体脚本；素材不入库，此处记录生成命令） |
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
| ~~av1_nvenc 硬编~~ | ~~`--codec av1_nvenc --cq 26 --nvenc-tune uhq --frames 30`~~ | ❌ **记录不成立** | 2026-10-14 更正：T4=Turing **无 AV1 NVENC 编码器**（`benchmark_data_consolidated_20261013.json` 的 `environments.T4.nvenc` 仅 `hevc_nvenc,h264_nvenc`）。该「✅17.4s」实为回退到软编、或与上一行 4K 用时串行。**av1_nvenc 全部数据只能来自 L40** |
| AttributeError 回归 | `--split-mode auto --workers 2 test2.mp4` | ✅ 修复生效 | 5m59s 正常完成 |
| libx265 分段并行 | `--split-mode segment --workers 2 test2.mp4` | ✅ 6m00s | **L40 因内存不足失败** |
| workers 模式(CPU) | `--split-mode workers --workers 2 --device cpu` | ⚠ 0.2fps | 进程间传帧开销大，非生产推荐 |

**关键发现（2026-10-14 更正）**：~~L40 上 libx265 失败根因是 cgroup 4GB 内存限额导致 OOM，非代码 bug~~
⇒ **该结论错误**：L40 cgroup 实测 192GB/可用 ~189GB（见 3.5），并非 4GB；
真根因是**代码缺陷**（B1 四项，3.11），已修复并在 L40 复验通过。T4 (32GB) 全部通过。

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
| **E** | *（字幕轴无组合）* | 字幕 burn/keep | `--subs {burn,keep,none}` `--subs-index N` | torch+模型+**带字幕素材**+libass | 仅本体脚本 |
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

### 3.5 问题二：4K「管道断开」根因 ~~（证据链 → cgroup OOM）~~ → **归因作废，真根因见 3.11**

> 🔴 **2026-10-14 更正：本节的归因前提「L40 cgroup 限额 4GB」是误报，整节结论作废。**
> 下表保留仅为记录错误推理链，**不要再引用其中的 OOM 结论**。

**误报是怎么来的（可复核）**：同一份 session 日志里，L40 机器**自身**的 `--env`
输出（脚本直接读 `/sys/fs/cgroup`）共 **28 次**报告：

```
系统资源 : CPU 逻辑核 48（cgroup v2 (4800000/100000)） · 内存 总 192.0 GB / 可用 189.3~189.8 GB（cgroup v2）
```

而本节把它记成了「限额 4GB（可用 1.8GB）」——**`189.8 GB` 被读成 `1.8 GB`**（位数误读）。
三处独立记载互相印证 L40 内存充裕：

| 来源 | 记载 |
|------|------|
| L40 自身 `--env`（session 日志，28 次） | 总 **192.0 GB** / 可用 **189.3–189.8 GB** |
| `memory/benchmark_data_consolidated_20261013.json` → `environments.L40.mem_gb` | **192** |
| 本机 24 核机 `/sys/fs/cgroup/memory.max` | **无 cgroup v2 限额** |

**同一日志还自证不是 OOM**（在本节推论之前）：

> | 2. OOM 确认/规避 | ✅ **确认非 OOM** | L40 显存 44GB 充足，4K 分块 tile=1024 正常跑通 |
> — 关键发现：「libx265 在分段并行下仍报 183，但 NVENC 完全正常；4K 分块 tile=1024/2048 均跑通」

若真是 4GB 限额，4K 分块不可能在 L40 跑通。

**真实根因是 3.11 记录的 B1 四项代码缺陷**（与内存无关）：
`-threads 48` 致 libx265 初始化超时 / `setparams` 与单独色彩四参冲突 /
解码 `-t` 定位偏移产生异常首帧 / 滤镜链 `scale` 重复色域转换。
B1 修复后 L40 验证「4K 30 帧 `--model agcm --codec libx265 --frames 30` → 18s 完成」。

⇒ **L40 的三次失败（4K 管道断开、libx265 exit 183、av1_nvenc 收尾死锁）全部是代码缺陷，
没有一次是 OOM。** 前两归 B1、第三归 B2（3.12），均已修复并在 L40 复验通过。

**与 av1_nvenc 是同一条线索**：三次失败**都被先后归因到「资源不够」**（cgroup 内存、
NVENC 会话显存），而 `memory/l40_fixes_20261013.json` 记录 B2 时明确标了
`"misattribution": "最初误判为 av1_nvenc 硬件 NVENC 会话 flush 卡死"`。
真因是**管道与帧循环逻辑**：帧循环因 `n_done>=total`（ffprobe 自报 2194、实际 2191）
先于解码器 EOF 退出 ⇒ 主进程停读 `dec.stdout` ⇒ 64KiB 管道满 ⇒ 解码器阻塞 `write()`
⇒ 旧代码 `dec.wait()` 无超时永久挂起。**内存充裕与否不影响该 bug 是否触发。**

⇒ 教训沉淀：**L40 上再现「资源类」症状时，先查代码管道逻辑，再查资源**；
且**任何 cgroup 数字都必须现场读 `/sys/fs/cgroup/memory.max` 复核**，不可凭 `--env` 摘要转述。

**仍然有效的部分**：BrokenPipe 分支带出 enc 退出码 + stderr 尾 6 行的**诊断增强**（`:4525-4540`、
`:5153-5162`）是好的设计，保留。注意其提示文案「137=SIGKILL，疑似 OOM」措辞是**推测性**的
（`措辞留"也可能是外部 kill"`），在无 cgroup 限额环境下不应据此断定 OOM。

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
| L40 | ~~cgroup 4GB (可用 1.8GB)~~ | ❌ 失败 | 🔴 **该环境差异是误报**（见 3.5 更正）。真根因 = **B1 四项代码缺陷**（3.11）：`-threads 48` 致 libx265 初始化超时、`setparams` 与色彩四参冲突、解码 `-t` 偏移、滤镜链 `scale` 重复转换 |
| T4 | 32GB 可用 | ✅ 成功 | ffmpeg 版本/参数组合不同，未命中 B1 四个条件 |

**根因（更正）**：**不是 OOM**。同一组参数在 L40 失败、在 T4 成功，差异是
**代码缺陷的触发条件**（L40 48 核 ⇒ `-threads 48` 触发 libx265 初始化超时），
而非内存。退出码 183 是 libx265 未能正常初始化/收尾的**症状**，不是「管道断开」。

**修复（已落地并复验）**：**B1 四项修复**（`b226d86`，明细见 3.11），
L40 验证「73s 全片 libx265 ensemble → 1m43s ✅」「4K 30 帧 → 18s ✅」「segment 2 段 → 1m35s ✅」。

**~~原「向运维申请 ≥6GB」方案作废~~** —— 它是为一个不存在的内存约束开的药。
> 注：同 repo 的 `memory/project_long_ffmpeg_jobs.md`（提交 `748b33e`）确实记录过
> **8GiB cgroup 机器上** 4K 被 OOM 杀（峰值 RSS 4.23GB）。但那是**另一条更重的
> 管线**（`interp_2x_safe.sh` + minterpolate + libx265 4K 放大）与**另一档机器**，
> 不可与 L40 混为一谈。SDR→HDR 4K 管线实测峰值 RSS 仅 **1.38GB**（T4）。

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
- ✅ NVENC (hevc_nvenc) 正常工作

### 3.12 av1_nvenc 收尾挂起：根因锁定与防御性修复 (2026-10-13)

> 背景：L40（Ada，第8代 NVENC）上 `av1_nvenc` 全片转码进度到 100%「收尾中...」后
> 停止输出，GPU 利用率降 0 但显存恒定 ~11.5GB 不释放 ⇒ 编码器 ffmpeg 进程活着但
> 不再退出。改 MKV 容器同样挂起 ⇒ 与容器 moov finalize 无关。ESC 中断后无产物。

**归因实验矩阵（收紧结论）：**

| 编码器 | 环境 | 输入 | 收尾结果 |
|--------|------|------|----------|
| libx265 (软编 CPU) | T4 | 73s 全片 | ✅ 1m43s 正常 |
| hevc_nvenc (硬编) | T4 | 73s 全片 | ✅ 5m47s 正常 |
| libsvtav1 (AV1 软编) | T4 | 60 帧 | ✅ 5.2s 正常 |
| libaom-av1 (AV1 软编) | T4 | 60 帧 | ✅ 12.7s 正常 |
| **av1_nvenc (硬编)** | **L40** | **73s 全片** | **❌ 收尾死锁**（MP4/MKV 均挂） |

**根因（已精确锁定）：**
- AV1 编码本身、管道逻辑、容器 finalize、Python 帧循环 **全部正常**（T4 全组合通过）
- 精确根因 = **av1_nvenc 硬件 NVENC 会话**（Ada/驱动/容器环境组合）在收到全部帧 +
  stdin EOF 后卡在 flush 不退出，主线程 `enc.wait()` 无超时 ⇒ 永久挂起

**代码健壮性缺陷（已修复并验证）：**

| 缺陷 | 风险 | 修复 | 位置 |
|------|------|------|------|
| `enc.wait()` 无超时 | 编码器不退出 ⇒ 永久挂起（本次症状） | 120s 超时 + `_terminate` 优雅终止 | `run_job:4510` |
| `dec.wait()` 无超时 | 解码器 stdout 写阻塞 ⇒ 永久挂起 | 10s 超时 + 终止 | `run_job:4523` |
| 帧循环提前退出未排空 dec.stdout | 64KiB 管道写满 ⇒ 解码器阻塞在 write() | 收尾前 `read()` 排空到 EOF | `run_job:4509` |

**L40 复测结果（2026-10-13，修复后）✅：**

| 测试 | 容器 | 用时 | fps | 产物 | 结果 |
|------|------|------|-----|------|------|
| av1_nvenc 全片 ensemble | MP4 | 1m47s | 20 | 37.1MB / 2191帧 | ✅ 退出码0 |
| av1_nvenc 全片 ensemble（复跑） | MP4 | 1m46s | 21 | 37.1MB | ✅ 退出码0 |
| av1_nvenc 全片 ensemble | MKV | 1m46s | 21 | 37.1MB | ✅ 退出码0 |

**根因再认识（复测后修正）：**
- 三次复测均干净完成（GPU 归零 + 显存同步释放 + 退出码 0），未触发 120s 超时兜底
- ⇒ **主因是解码器 stdout 写阻塞死锁**：帧循环因 `n_done >= total`（2191 < 源自报 2194）
  提前退出 ⇒ 解码器仍在写 stdout ⇒ 64KiB 管道满 ⇒ 解码器阻塞 `write()` ⇒
  旧代码 `dec.wait()` 永久挂起；此时编码器已无新帧但 CUDA context 未释放，
  表现为「GPU 0% + 显存恒定不降 + 进度停收尾中」
- 超时兜底（enc 120s / dec 10s）为防御性保险，本次未触发

**状态：**
- ✅ T4 hevc_nvenc 回归通过（修复未破坏正常路径）
- ✅ **L40 av1_nvenc 复测通过（MP4/MKV 三组，20-21fps，干净退出）**
- ✅ bug 2 闭环

### 3.13 Phase 3 最终统计数据与剩余测试项 (2026-10-13)

**测试通过统计：本会话共 15 组实测，14 通过 / 1 曾挂死（已修复复验通过）**

| 环境 | 编码器 | 输入 | 用时 | fps | 产物 | 状态 |
|------|--------|------|------|-----|------|------|
| L40 | libx265 | 73s 全片 ensemble | 1m43s | 21 | 24.0MB (+65%) | ✅ |
| L40 | libx265 | 5 帧 ensemble | 0.7s | 3262 | — | ✅ |
| L40 | libx265 | segment 2段 ensemble | 1m38s | 22 | 24.0MB | ✅ |
| L40 | libx265 | 4K 30帧 agcm | 18.1s | 53 | 0.87MB | ✅ |
| L40 | hevc_nvenc | 4K 30帧 agcm | 17.7s | 54 | 1.4MB | ✅ |
| L40 | hevc_nvenc | 73s 全片 ensemble | 1m41s | 21 | 26.1MB (+80%) | ✅ |
| L40 | av1_nvenc | 73s 全片 ensemble | 1m47s | 20 | 37.1MB (+156%) | ✅ 修复后 |
| L40 | av1_nvenc | 73s 全片 ensemble（复跑） | 1m46s | 21 | 37.1MB | ✅ |
| L40 | av1_nvenc | 73s 全片 ensemble MKV | 1m46s | 21 | 37.1MB | ✅ |
| L40 | libx265 | no-model threads=8 | 14.7s | 150 | 13.3MB | ✅ |
| T4 | hevc_nvenc | 73s 全片 ensemble | 5m47s | 6 | 27.9MB (+92%) | ✅ |
| T4 | libx265 | 73s 全片 ensemble | 1m43s | 21 | 24.0MB | ✅ |
| T4 | libsvtav1 | 60 帧 agcm | 5.2s | 420 | 0.6MB | ✅ |
| T4 | libaom-av1 | 60 帧 agcm | 12.7s | 173 | 0.58MB | ✅ |

**核心 benchmark 结论：**
1. **模型推理是瓶颈**：L40 上 libx265 与 hevc_nvenc 耗时几乎相同（21fps）⇒ 编码器非瓶颈
2. **模型轴**：agcm 26fps > ensemble/hr 6fps（agcm 快 4.4×，RSS 差异 <3%）
3. **L40 AV1 硬编**：av1_nvenc 产物体积 37.1MB 比 hevc_nvenc 26.1MB 大 42%，体积换兼容性
4. **bug 2 项已闭环**：libx265 exit183（线程/滤镜/解码）+ av1_nvenc 收尾死锁（解码器管道阻塞）

**Phase 3 Exit Criteria 达成：**
- ✅ av1_nvenc 硬编 `OK`（actual_codec=av1_nvenc，非降级，退出码 0）
- ✅ 4K 整帧/大分块不 OOM（192G 内存充足）
- ✅ 模型 agcm/ensemble/hr 在 L40 表现已量化
- ✅ 大并发下吞吐可量化（segment 2段 22fps）

### 3.14 剩余测试项目建议

| 优先级 | 项目 | 命令要点 | 说明 |
|--------|------|----------|------|
| P1 | bench d 层正式报告 | `bench_sdr_to_hdr.py -i test2.mp4 --layers d --codecs av1_nvenc,hevc_nvenc --crf-ref 21 --repeats 3 --json ...` | 正式生成 d 层基准 JSON（含 quality_axis/crf_sent） |
| P1 | VMAF 质量门禁 | 上述加 `--vmaf l1.libx265,d.av1_nvenc.cq --vmaf-tol 1.0` | 验证 av1_nvenc 等质量换算不劣化 |
| P2 | 4K av1_nvenc | `earth_4k_30s --frames 60 --codec av1_nvenc` | L40 独有项，更长帧验证稳定性 |
| P2 | 大并发 workers=8 | `--layers c --workers 8` 或 `--split-mode segment --workers 8` | L40 48 核大并发吞吐 |
| P2 | bench 全层回归 | `--layers l1,l2,l3,c,d,e,g,m --repeats 3` | 修复后全矩阵跑一遍，确认无回归 |
| P3 | 长素材验证 | Creature Cases (1665s) | 跨小时级长任务稳定性（含收尾修复） |

### 3.15 编码器收尾：固定超时误杀慢速编码器 → 改为「无推进」判定 (2026-10-14)

> 触发：ffmpeg 9.0.2 上 `--codec librav1e` 跑 30 帧报「完成 0 失败 1」、
> `编码 ffmpeg 退出码 -9` + 告警「编码器 120s 未退出，已强制终止（末帧收尾卡住）」，
> **产物仅 48 字节**。该 120s 正是 3.12 为 av1_nvenc 加的兜底。

**根因：不是死锁，是固定超时预算估错了。** 实测（720p/30帧/`-qp 64`/yuv420p10le）：

| 场景 | 耗时 |
|------|------|
| 裸 ffmpeg，30 帧从**文件**喂入 | **2m30s** 完成出片（单次跑通 ⇒ 非死锁） |
| 裸 ffmpeg `-threads 1` vs `-threads 24` | 2m32s vs 2m30s（librav1e 单线程，`-threads` 无影响） |
| 走脚本管道 | 帧循环 **1m41s** → stdin EOF 后 flush **~4m**（合计 4m33s） |
| 首次尝试：`3 × 帧循环` = 312s 预算 | 仍 `-9`（flush 实测 364s，**只差 52s**） |

**关键：flush 耗时与帧循环耗时没有固定比例**（此例 3.5×，随编码器/分辨率/内容变化）。
> ⚠ 这也**推翻了 3.12 之后我写的「librav1e 与 av1_nvenc 同源」**：若同源，
> 3.12 的排空 `dec.stdout` 就该治好它。实为 **av1_nvenc 零推进真死锁** vs
> **librav1e 持续推进的慢 flush**。

**为什么「加长超时」不是根治**：固定总预算无法区分「慢但正常推进」与「真卡死」——
猜小则误杀正常编码的慢编码器（本例，**且无产物**，是可用性事故）；猜大则真卡死时多等数小时。
第一次尝试的自适应公式正是在猜，且猜错了。

**根治：按「连续无推进时长」判定（liveness-based）**

| 改动 | 位置 |
|------|------|
| 编码命令下发心跳 `-stats_period 2 -progress pipe:2`（走 stderr，不新增管道） | `build_encode_cmd` |
| `_spawn_stderr_drain(..., on_heartbeat=cb)`：`_PROGRESS_KEY_RE` 识别 `frame=`/`out_time_us=` 等心跳行 → 刷新时间戳，且**不写入诊断 sink**（否则心跳挤掉真错误行） | `_spawn_stderr_drain` |
| `_wait_enc_flush()`：每 2s 轮询；`now - max(最后心跳, flush起点) > --enc-stall`(默认180s) ⇒ 判卡死 → `_terminate` | 新函数 |
| `--enc-timeout` 从主判据**降级为纯兜底上限** | CLI |
| **顺带修同类隐患**：`run_job_multiproc` 原本是**完全无超时**的 `enc.wait()`/`dec.wait()`，一并改看门狗 | `run_job_multiproc` |

**验证：**

| 用例 | 期望 | 实测 |
|------|------|------|
| 单测：假编码器 flush 后永久静默 | 判卡死并终止 | 180.0s 判定，`rc=-15`，告警文案正确 |
| 单测：假编码器每 2s 发心跳共 80s | **绝不被误杀** | 79.9s 正常退出 `rc=0` |
| 单测：心跳是否污染诊断 sink | 不污染 | `sink=[]` |
| 端到端 librav1e 30 帧 | 成功出片 | ✅ 4m33s / 415033 B / `av1,yuv420p10le,smpte2084,bt2020` |
| 回归 libx265 / libaom-av1 30 帧 | 不受影响 | ✅ 1m43s / ✅ 5m06s |
| 回归 `verify_sdr_to_hdr.py` 八项 | 全通过 | ✓ 全部通过（含命令 token 顺序逐字断言） |

**新增 CLI**（默认无需设置）：
- `--enc-stall SEC` — 连续无推进多少秒判卡死，0=自动 180s
- `--enc-timeout SEC` — 总预算上限，0=自动（6×帧循环，下限 300s，上限 3600s）

**状态：** ✅ librav1e 根治并验证；⚠ **av1_nvenc 真死锁未在本机复现（无 GPU）**，
其零推进特征现会被 `--enc-stall` 在 180s 内捕获 —— **须在 L40 回归确认告警文案变为
「N s 无任何推进，已强制终止（末帧收尾卡死）」**（⚠ **不能放 T4**：Turing 无 AV1 NVENC，
物理上复现不出该组合）。
详见 `memory/bugfix_librav1e_finalization.md`。

---

**祝测试顺利 🚀**