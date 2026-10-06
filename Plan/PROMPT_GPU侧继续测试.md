# PROMPT · GPU 侧继续测试（VidUtils）

> **这份文档是做什么的**：VidUtils 的质量换算表与 GPU 编码路径**已有大量 CPU 侧
> 结论**，但**凡是只有 NVIDIA 卡才能验的部分，一律还没验或验了没留证据**。
> 本 prompt 把待测项分组、给出判据与前置条件，让接手的人（或 agent）在一台
> N 卡机上按序跑完、不漏项、不把「跑绿了」误当成「结论成立」。
>
> **本机（无 GPU）已完成的事不要再重复做** —— 见 §0。

---

## §0 前置：本机能做的已经做了

**不要**在无卡机上重跑这些（2026-10-06 实测全绿，已记入 git 历史）：

| 已完成 | 怎么证的 |
|---|---|
| 四道回归门 | `chains` / `cmd_default` / `enc_options` / `cmd_full` 全部无分叉 |
| 15 个 verify 套件 | 14 个通过；`verify_chroma_hook.py` 因本机无 NVENC 失败（已知项） |
| `check_readme_refs.sh` | ✓ 引用了全部 53 个工具文件 |
| 装置自证 | `t4_acceptance.py --selftest`（9 格）、`--local`（4 格）、`verify_nvenc_quality_gpu.py --selftest` |
| 等质量 CPU 侧 | `verify_equal_quality.py` 主门禁 5/5 达标（ΔVMAF ≤ 1.0），需 12 分钟 |

> ⚠ **`verify_chroma_hook.py` 在有 GPU 的机器上会变绿**——它在本机失败是因为
> 硬写「本机无 CUDA」。**这属于「环境假红」，不是回归**。见 §2 的 P0。

---

## §1 头号任务：先把「已完成」的证据补齐

**这是本次最优先的一项**，因为它决定后面所有结论**能不能被复核**。

### 现状（2026-10-06 实查）

- `convert_crf.py` 里 h264/hevc_nvenc 与 av1_nvenc 的表值**确实已落表**
  （提交 `6c71fed` T4 / `a439b72` L40，均 2026-10-04，两仓逐字相等）。
- 但**这两个提交都没有产出报告文件**：`git show --stat` 只有 `convert_crf.py`、
  `probe/`、`verify/`、`memory/` 的改动，**没有任何 json/md 落盘**。
- `Accessory/verification_report/` 里**唯一**一份是 `nvenc_quality_T4_20260928_062328.*`
  ——它是 **2026-09-28、p5 口径、`-cq 28` 旧表值**，与现行 p4 / `-cq 26` 不一致。
  按 `verify_nvenc_quality_gpu.py:59-60` 自己的说明，它**不可与现表逐条对比**
  （p5 vs p4 口径、且旧报告无 `-rc`）。

⇒ **2026-10-04 的 T4 / L40 落表目前只有 memory 里的文字断言，没有可复核的报告。**

### 要做什么

| 编号 | 动作 | 判据 |
|---|---|---|
| **P0-1** | 在 T4 上重跑 `probe/verify_nvenc_quality_gpu.py` 的 B 组（h264/hevc `-cq`），**把报告落盘**到 `Accessory/verification_report/`，文件名带日期与口径 | 报告里 `-cq` 值与 `convert_crf.py` 现行表**逐条相等** |
| **P0-2** | 在 L40（Ada）上重跑 C 组的 av1 格，报告落盘 | 同上，对 `QUALITY_MAP['av1_nvenc']` |
| **P0-3** | 重跑 `probe/t4_acceptance.py` 全 20 格，报告落盘 | `t4_acceptance.py:319-325` 的 rc 与产物双判据 |

**落盘要求**（照 `verify_nvenc_quality_gpu.py` 自己的输出约定）：文件名
`<脚本名>_<卡型>_<YYYYMMDD_HHMMSS>.{json,md}`，json 里必须含
**ffmpeg 版本戳 + 卡名 + 驱动版本 + 落表口径（p 档 / rc 模式）**——
否则过三个月又是一份「不可逐条对比」的旧报告。

> 这正是 memory `feedback_report_accuracy` 记的坑：**锚点不同步才是真问题**。
> 报告里若不写「本次用的表值口径」，三个月后没人能判断它对应哪一版表。

---

## §2 P0：先排除「环境假红」，再谈回归

**在有卡的机器上，下面这些判据会红 —— 这是设计如此，不是回归**：

| 判据 | 位置 | 为什么必红 |
|---|---|---|
| `verify_decode_axis.sh` ⑦ 组 | `Accessory/verify/` | 硬写「本机无 CUDA / 无 N 卡」 |
| `verify_cuda_decode_codec.py` ⑥ 组 | 同上 | 同上 |
| `verify_quality_mapping.py` ① 组 | 同上 | 同上 |
| `verify_borrow_enhancement.py` ⑧ 组 | 同上 | 同上 |
| `dump_cmd_full.sh` 2 个用例 | `Accessory/test/` | GPU 机上默认编码器变 `h264_nvenc`，两脚本命令本就该不同（能力差异，不是分叉） |

**要做什么**：用 `git worktree` 拉一个**改动前**的提交，在这台 GPU 机上复跑同一批
判据，**对比两者的红/绿模式是否一致**：

```bash
cd /mnt/d/Workspace_Python/VidUtils
git worktree add /tmp/vu_base <改动前的 commit>
cd /tmp/vu_base && bash Accessory/verify/verify_decode_axis.sh; echo "base rc=$?"
cd /mnt/d/Workspace_Python/VidUtils && bash Accessory/verify/verify_decode_axis.sh; echo "head rc=$?"
```

**判据**：两边的红/绿**模式相同** ⇒ 属环境假红，不必修。
**模式不同** ⇒ 混在假红里的**真回归**被掩盖了，必须揪出来。

> 为什么放 P0：memory `project_silent_false_green` 记的正是这类——
> **判据在 GPU 机上失效时报「一切正常」**。不先排除它，后面所有绿灯都不可信。

---

## §3 A 组：T4 B1~B5 NVENC 真编码（本次变更重点）

脚本：`Accessory/probe/t4_acceptance.py`，用例定义 `:123-139`。

| 格 | 命令 | 验什么 |
|---|---|---|
| B1 | `--codec hevc_nvenc --rc-mode constqp --qp 18` | NVENC 是否接受 `-rc constqp -qp N` |
| B2 | 同上 `--qp 0` | 0 档形状（`-rc constqp -qp 0 -b:v 0`）是否被拒 |
| **B3** | `--codec hevc_nvenc`（默认路径） | **验证默认 rc `vbr` 真的下发**（`f617ffe` 把 `vbr_hq` 移除后的唯一验证点） |
| B4 | `--mode cover --codec hevc_nvenc` | GPU 策略链（硬解 + `scale_cuda` + NVENC）没被命令顺序统一带坏 |
| B5 | `--codec h264_nvenc --rc-mode constqp --qp 18` | 另一个 NVENC 编码器上 constqp 是否被接受 |

**判据**（`:319-325`）：每格需同时满足 **rc == 0** 且 **`made` 非空**
（`made = sorted(dst.glob("*.mp4"))`，任一不满足即记问题）。总 exit 0/1/2。

```bash
cd /mnt/d/Workspace_Python/VidUtils
python3 Accessory/probe/t4_acceptance.py --src '<T4 上的素材路径>'
```

**⚠ B3 是本组唯一不可省的一格** —— `f617ffe`（2026-10-03，FFmpeg 9.0 移除
`vbr_hq`）之后，只有它能证明默认 rc 真的落成了 `-rc vbr`。ffmpeg 9.0.2 下必须重跑。

**卡型约束**：T4 = Turing，**没有 av1_nvenc**（实测 `error code -22`）。
B 组这 5 格只用 h264/hevc，T4 可以跑。

---

## §4 B 组：NVENC 质量轴复测

脚本：`Accessory/probe/verify_nvenc_quality_gpu.py`。

### B 组 `-cq` 偏移（`:411-462`）

三编码器 × {表值 26/26/32, 朴素 21}。判据 `rate_verdict`（`:262-272`）：

- **只有「表值」格计 FAIL**；「朴素值」格降为 WARN（`:437-439`）
- 码率比（目标/基准）落在 `RATE_PASS = (0.65, 1.50)`，警戒带 `(0.55, 1.65)`
- ΔPSNR 下探 ≤ `TOL_PSNR`（1.5 dB）

### C 组 constqp `-qp` 尺度（`:465-525`）

- av1 扫 {21, 仿射 70, 63, 105}，判据是 **|ΔVMAF| ≤ `TOL_VMAF` = 2.0**
  （`:90`）—— 比 CPU 侧宽松，因为 NVENC 的 `-qp` 与 VMAF 的关系本就不是线性的
- PSNR / 码率只作 evidence，不判红（`:489-490`）
- 结论行 `av1_qp_conclusion`（`:275-295`）是纯函数，已由 `--selftest` 自证

**⚠ 卡型不可互替**（`Plan/VidUtils_等质量标定_T4专项执行方案.md:74-76`）：
T4 无 AV1 NVENC ⇒ **C 组的 av1 格必须在 L40/Ada 上跑**。

**⚠ T4 上跑 C 组不要加 `--expect-av1`**：该参数会把静默 SKIP 升级为 exit 2
（`:609-611,647-656`），在 T4 上必然失败。

---

## §5 C 组：脚本需先扩，不能直接跑

### C-8：VU 侧 C 组扩到 ref 24/27/30

**出处**：`Plan/CR-4_av1_QP轴_handoff_VE_to_VU_20261004.md:51`

CR-4 已确认「av1 的 ×3 QP 尺度**只在 ref≈21 成立**」。当前探针只测 ref21
（`:466` `vu_qp21`），结论覆盖不到高 ref。

**要做什么**（**脚本需先改**）：
1. 给 `verify_nvenc_quality_gpu.py` 的 C 组加 ref 24/27/30 三档，各扫一个 `-qp`
   （按仿射推算约 93/117/141）
2. 在 Ada 卡上跑，确认 ×3 尺度在高 ref 是否超带

**判据**：三档各自落带；若 ×3 欠配，则 `Plan/VidUtils_质量控制参数修复方案.md:803-805`
的 C-7（VU 恒等 ref×1 vs VE 仿射）对齐方式要跟着改。

### C-7 与 C-8 的顺序

**先做 C-8 再定 C-7** —— 若 C-8 证明高 ref 下 ×3 欠配，C-7 的对齐方案会变。
这两项**互斥方向**，不要并行决策。

---

## §6 D 组：需要别的机器（不与 T4/L40 批次绑定）

| 编号 | 项 | 需要的机器 | 出处 |
|---|---|---|---|
| C-1 | QSV 能力表 + 两仓口径统一（本仓写 `-global_quality/-q`，VE 归 `_CQ_CODECS` 下发 `-cq:v`） | Intel 核显/Arc | `Plan/VidUtils_质量控制参数修复方案.md:743` |
| C-2 | AMF 能力表（复核 `-cq`/`-preset` 是否真在 CQ 集） | AMD | 同上 `:744,193` |
| C-4 | `av1_qsv`/`av1_amf` 量程（现写死 51，确认是否 0~63） | Intel / AMD（与 C-1/C-2 同机可合并） | 同上 `:96` |
| C-3 | VideoToolbox `-q:v` / preset 集复核 | macOS | 同上 `:744,192` |
| C-5 | `--hdr sdr` tone mapping 观感（hable/mobius/reinhard 在真实 HDR 片源上对比） | T4 + HDR 素材（`new4_raw.mp4`） | `README.md:1764` |
| C-6 | `crop-cover` 的 CUDA 缩放（该路径未实测；`crop_cuda` 上游不存在 ⇒ 必先 CPU crop） | 任意 N 卡 | `README.md:1765,113` |
| C-9 | NVENC 代际跨代复核（T4 与 Ada 表值不得互相覆盖） | T4 + L40 | `Plan/…T4专项…:334-335` |
| C-10 | `eqq_calib` 17 素材在 GPU 机就位（切片 + manifest 先落机） | T4/L40 | `Plan/…T4专项…:326-327` |

---

## §7 建议执行顺序（含依赖）

```
P0-1/P0-2  补齐 2026-10-04 的报告          ← 最高优先：没有它，下面全是不可复核的断言
   ↓
P0         排除环境假红（§2 worktree 对比）  ← 不做这步，后续绿灯不可信
   ↓
§3 B3      验证 vbr 真的下发（f617ffe 的唯一验证点）
   ↓
§4 B 组    NVENC -cq 复测（依赖落表口径 p4 + -rc vbr）
§4 C 组    constqp -qp 尺度（av1 须 L40；T4 上不加 --expect-av1）
   ↓
§5 C-8     C 组扩到 ref 24/27/30（脚本需先扩）
   ↓
§5 C-7     h264/hevc quality 口径对齐（须等 C-8 结论）
   ↓
§3 其余    A 组落点 / B1,B2,B4,B5 / C1 无损探针
   ↓
verify_equal_quality.py 的 GPU 档（§8）
§6 D 组    需另机，可并行
```

---

## §8 硬编门禁：GPU 档从 SKIP 变实测

`Accessory/verify/verify_equal_quality.py` 的 `HARD = ('h264_nvenc','hevc_nvenc','av1_nvenc')`
在无卡机上全部 SKIP。有卡后它会变**实测**：

- 主门禁 **|ΔVMAF| ≤ 1.0**（唯一判红口径；PSNR/PSNR-HVS 仅 soft 参考）
- **全 SKIP ⇒ 退出码 2**（`:39` 注释所述的「空集守卫，防静默通过」）——
  有卡机上若仍 exit 2，说明**表没覆盖该编码器**或**探测失败**，不是「通过」

**前置**：需 12 分钟（真实素材重编码）。参考值（2026-10-04 落表，CPU 侧记录）：
h264 ΔVMAF = −0.044 / hevc = +0.135。

---

## §9 素材前置

| 用途 | 素材 | 状态 |
|---|---|---|
| T4/L40 标定 | `input_videos/eqq_calib` 17 条（12×6s + 5×10s） | **在 VE 仓，不入 git** ⇒ 上机前须先落机（C-10） |
| 无损探针 C1 | `Accessory/temp/fixture_1080p.mp4` | 可自建（见下） |
| tone mapping C5 | `input_videos/new4_raw.mp4`（HDR 10-bit） | 需 tonemap 后使用 |
| 通用夹具 | `ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 -c:v libx264 -preset ultrafast -pix_fmt yuv420p <路径>` | **可 lavfi 现造** |

> `fixture_1080p.mp4` 若缺失，跑 `python3 Accessory/verify/verify_color_tagging.py`
> 会自动生成。**不要**照旧提示去跑 `dump_filter_chains.sh`——它建的是
> `temp/lockstep/{land,port}.mp4`，不产这个文件（该提示已在 2026-10-06 改正）。

---

## §10 每轮收尾：留证据 + 更新文档

**每跑完一项**：

1. **报告落盘**到 `Accessory/verification_report/`，命名
   `<脚本>_<卡型>_<YYYYMMDD_HHMMSS>.{json,md}`；
   json 必含 **ffmpeg 版本 + 卡名 + 驱动版本 + 落表口径（p 档 / rc 模式）**
2. 把报告路径与关键数字**写进 `memory/project_rate_control_params.md`**
   （T4/L40 实测那节）——memory 里目前只有文字描述，没有报告链接
3. 若表值变了：改 `convert_crf.py`（**两仓逐字相等**），并跑
   `python3 Accessory/verify/verify_quality_mapping.py`（⑨ 组 14/14）
4. 若结论推翻了某条 memory：**当场改 memory**，不要只在新提交里提一句
5. 更新本文档的勾选状态

**已知会红、且属预期**：`dump_cmd_full.sh` 在 GPU 机上因「默认编码器变
`h264_nvenc`」而红 —— 是 §7 既知的环境假红，与本次改动无关。
