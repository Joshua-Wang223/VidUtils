# Accessory/archive_manifest.md — 归档清单（2026-10-06）

`Accessory/archive/` 存放**不可再生**的本机产物：删掉就再也拿不回来的东西。
其余留在 `Accessory/temp/` 的是**可再生的中间编码产物**，已按本清单判定后清理。

## 为什么要有这个目录

`Accessory/temp/` 此前 483MB，其中绝大部分是探针/标定按 CRF 档位现编的
「编码样本」——它们由脚本常量行可证地重建，而真正的**结论数据**（标定表、
逐点 VMAF 原始测量、手工快照）只占 1MB 出头。体积与价值严重倒挂。

清理时最怕的是把两者一起删掉。本清单是判据：**每一条都写明「谁生成、
为何不可再生、重建命令或为什么重建不了」**，使后续任何人（包括 agent）
都能复核这个取舍，而不是只看体积就动手。

## 归档原则

| 判据 | 处理 |
|---|---|
| 含标定结论 / 测量原始数据 / 手工汇总 / 外部素材 | **归档**（本清单） |
| 脚本按档位现编的中间编码样本 | 可丢（重建命令已核实） |
| 脚本自造的 lavfi fixture | 可丢（源码即生成器） |
| 结论已落进 git 里的（如 `convert_crf.py` 表值） | 仍归档（那是**中间轮次**，非现行表） |

## 归档内容（82 个文件 / 34MB）

### `eqq_calib/` — 等质量（VMAF）标定的逐点原始数据 · 34 文件 / 1.2MB

生成者 `Accessory/probe/calibrate_equal_quality.py`（`--workroot`）。
**保留的是 json 而不是 mp4**：15 个轮次目录下的 `report.json`（池化表 +
每素材 `src_md5`/`prep_md5` 溯源链）、`points.json`（逐点 VMAF / PSNR_HVS /
VIF / ADM2 / kbps）、`vmaf.json`（libvmaf 帧级日志，内嵌 ffmpeg 版本戳）、
`points_cache.json`（旧格式）。

**为何不可再生**：重跑整套要数十小时 VMAF 测量；且 `vmaf.json` 内嵌
`ffmpeg version 8.0.1` 戳，换版本即得不同数字——无法与历史逐点比对。

轮次目录：`m1_2src_10s` `m2_7src` `m2_7src_6s_rav1e` `m2_7src_6s_rav1e_s10`
`m2_anchorA` `p0_rav1e_native` `s1_all` `s1b` `w2_aom` `w2_rav1e` `w2_svtav1`
`w2_vr` `w2_x265` `w_svtav1` `chunk_svtav1`

> ⚠ **这些是被后续轮次取代的中间态，不可当现行标定依据。**
> 例：`m2_7src/report.json` 的 `table.libx265=[1.1013,-2.6452]` 与已落表
> `convert_crf.py` 的 `(1.0979,-2.3119)` 不同。保留它们是为了保住逐点原始
> 记录（现行表里没有的信息），不是主张这些数值有效。

### `calib/` — 等体积软编偏移的结论 + 两份唯一副本 · 4 文件 / 26KB

`report.json`（4 个编码器的 a/b + 拟合点）、`calib.log`（各档 KiB 表）。
生成者 `Accessory/probe/calibrate_soft_offsets.py:29`。

另两份**全仓唯一副本**（`git log --all` 零记录、无第二份）：

- `m2_resume.log` — M2 标定 resume 过程的逐档实测记录（每档 KiB + VMAF +
  HVS，含 `prep.mp4` 的 md5 溯源）。
- `VE_subsample_alert.md` — VE 侧 `--subsample 8` 违反标定口径的告警
  （2026-10-02）。内含本机实测的偏置量：同文件 vp9 crf35
  `subsample1=96.62` vs `subsample8=98.56`，**差 1.9~3.0 且随编码器而异**。
  该陷阱的结论已进本仓 `memory/project_vmaf_subsample_trap.md`，但**原始
  告警文档与这组实测数字**只存在于此。

⚠ `report.json` 的 `libx265 a=0.9155 b=1.6385` 与现行表
`(0.9272,1.3360)` 不一致，是被重标定取代的旧轮次。

### `snapshots/` — 手工决策快照 · 3 文件 / 20KB

`state_2026-10-02.json`（第七版 `QUALITY_MAP_current` 表 + 6 个 run 表与
per_material 明细 + 手写 `_note`：「LOO red recorded, not gating」、
「INVALID: subsample=8」）、`comparison_summary.json`
（`crf21_vmaf` 76.8/69.8/73.1/74.0 与结论句，**全仓唯一副本**）、
`anchorB_x264.json`（从易失的 `/tmp/eqq2/` 拷回的备份）。

**为何不可再生**：全仓 grep `snapshots` / `state_2026` / `anchorB_x264` /
`comparison_summary` 在 `*.py`/`*.sh`/`*.md` 中**零命中** ⇒ 无生成脚本，
属手工汇总。部分表值抄在 `Plan/VidUtils_质量控制参数修复方案.md:698-702`，
但 `crf21_vmaf` 对照结论与 n3 作废判定只存在于此。

> 复核注：上面那条 grep 必须排除 `snapshots/` 自身（`state_2026-10-02.json`
> 内部含 `VE_10s_anchorB_x264` 键，会自己命中自己）。正确写法见文末「复核方式」。

### `plan_archive/AV1_VP9_UPGRADE_PLAN.md` — 第一版计划书 · 12KB

被 `Plan/AV1_VP9_UPGRADE_PLAN_v2.md` 取代（v2:14 明写「已被本版取代」），但
`git log --all` 对该文件**零记录** ⇒ 从未入库，这是唯一副本。

### `m2_srcs/` — 7 素材标定的上游原片 · 9 文件 / 32MB

`cc_anim_300s.mkv` / `cc_subs_105s.mp4` / `earth_dark_80s.mp4` /
`natgeo_grass_40s.mp4` / `ui_screen_10s.mp4` + `cc_eng.srt` /
`cc_eng_shift.srt` / `make_ui_screen.py` / `ui_code.txt`。

**为何不可再生**：`input_videos/eqq_calib/manifest_6s.json` 的
`source_path_resolved` 字段**逐条指向**这 5 个原片（如
`doc_grassland`→`natgeo_grass_40s.mp4`），删掉即断溯源链。仅
`ui_screen_10s.mp4` 可由同目录 `make_ui_screen.py` 重建，其余 4 个是外部
素材（BBC/NatGeo 原片依赖 `/mnt/f/` 挂载）。两个 `.srt` 无任何生成者。

> 复制到 `input_videos/` 的必要性：`Plan/PROMPT_等质量换算立项.md:374`
> 已警告「`VidUtils/temp/m2_srcs/*` 临时原片随时会丢」。

### `sdr_hdr/out_model.mp4` — 模型推理路径的唯一物证 · 28KB

640x360 / 6 帧 / hevc Main10 / bt2020nc / smpte2084。画面均值 **0.49**（实测 `ffmpeg format=gbrp16le` 后按帧取均值），
对照 `--no-model` 的 **0.19** —— 即「HDRTVNet++ 在本机真跑通并确实改变了
画面」的物证（结论已写进 `convert_sdr_to_hdr.py` 的 docstring 与本仓
memory `project_hdrtvnet_plus.md`）。

**为何不可再生**：`convert_sdr_to_hdr.py` 无工作目录逻辑，目录整体是手工
敲 CLI 的 ad-hoc 产物（帧数/尺寸/`--frames 6` 都是人工决定），命令未记录。
重跑还需 torch + 仓库外的 `/mnt/d/Workspace_Python/HDRTVNet-plus`。

### `probe_lossless/*.log` — `-crf 0`/`-qp 0` 无损性自证 · 3 文件 / 20KB

完整 ffmpeg 命令行、`大小变化 3.2 MB → 5.8 MB（↑81.7%）`、x265 build info。
结论已落 README，但 log 是本机自证留痕。视频本身可丢（重建见下）。

### `chroma_work/` — 色度归零探针留痕 · 20 文件 / 80KB

`*.log`（V0-V9 变体消融）、`s_all-cpu.out` / `s_decode-cpu.out`
（**本机硬件探测事实**：`crop_cuda 不可用`、`av1_nvenc 不可用：AV1 硬编需
8 代 NVENC`）、`e_B3_软解+crop+nvenc.err`（`Cannot load nvcuda.dll` 失败证据）。

硬件探测结论**换机器就变**，重跑得到的会是另一台机器的结论。

### `probe_scale_cuda/` — CUDA 缩放裁剪探针留痕 · 5 文件 / 96KB

`probe_scale_cuda_{720p,1080p,4k,newtest,newtest2}.log`：各分辨率下的
上传形态判定、耗时与画质对比记录，含**本机策略链的实测选择**
（如「hwupload_cuda（自带 device，无需 -filter_hw_device）」）。
结论已落 README 与 memory，但逐分辨率的原始日志只存在于此。

### `verification_report/` — T4 上机验收报告 · 2 文件 / 12KB

`nvenc_quality_T4_20260928_062328.json` / `.md`。

## 已丢弃的可再生产物（159MB）

| 目录 | 释放 | 重建依据 |
|---|---|---|
| `eqq_calib/*/*.mp4` | 70.8M | `calibrate_equal_quality.py` 按 `ANCHOR_CRFS=[18,21,24,27,30]` 现编 |
| `calib/*.mp4` `*.webm` | 71M | `calibrate_soft_offsets.py:31-38` 扫档（文件名与档位一一对应） |
| `probe_lossless/*.mp4` | 14M | `LOCALCPU=1 SRC=Accessory/temp/fixture_1080p.mp4 bash Accessory/probe/probe_lossless_qp0.sh`（数分钟，纯 CPU） |
| `chroma_work/*.mp4` | 8M | `SELFTEST=1 bash Accessory/probe/probe_green_chroma.sh` |
| `lockstep/` | 3.4M | 5 个 `dump_*.sh` 里的 testsrc2 合成 |
| `verify_ratio/` | 3.2M | `verify_ratio_single_dim.py:38-46` `ensure_fixtures()` |
| `verify_equal_quality/` `verify_sdr_hdr/` `verify_quality/` | 0.2M | 各自 verify 脚本的 lavfi fixture（12 分钟 / 数秒） |
| `probe_scale_cuda/` | 736K | `SELFTEST=1 bash Accessory/probe/probe_scale_cuda_crop.sh` |
| `t4_acceptance/` | 720K | 见下方⚠ |
| `sdr_hdr/` 其余 4 个 | 1.3M | lavfi 源 + `--no-model` 产物 |
| `videos/` | 273M | **不可再生，已移出**至 `input_videos/probe_screen_recordings/` |

### ⚠ `t4_acceptance/B1-B5` 为什么可丢

这批文件名是「B 组 NVENC 接受度测试」，但**本机产物并不是 NVENC 编码**：
`strings` 实测 `B1` 为 `Lavc63.1.102 libx265`（x265 SEI）、`B5` 为
`x264 core 165` —— 是 NVENC 降级到 CPU 软编后的结果（本机无 `nvidia-smi`）。
它们**不构成 T4 接受度证据**，留着反而容易被后人误读成「B 组已过」。

B 组真结论需在 T4 上重跑，脚本与 `B_CASES` 定义都在 git 里
（`Accessory/probe/t4_acceptance.py:124-139`）。

### `videos/` 移出（273MB，不可再生）

8 个 `video_20260921_*.mp4`，容器带 `com.android.version=16` /
`com.android.capture.fps=60` ⇒ **手机录屏原件**，非 ffmpeg 产物。全仓
grep `temp/videos`、`video_2026` 在 `Accessory/{probe,verify,test}` 与
README/Plan 中**零命中** ⇒ 无生成脚本。

已迁至 `/mnt/d/Workspace_Python/input_videos/probe_screen_recordings/`
（8 个文件 md5 逐一校验一致，该目录在仓库外、不入库）。
仓内不再保留副本。

## `temp/` 清理后的剩余内容（4.3MB，全部可再生）

清理后 `Accessory/temp/` 只剩两类东西，**都不是归档对象**：

| 文件 | 体积 | 说明 |
|---|---|---|
| `fixture_1080p.mp4` | 1.6M | 各 verify/探针的公共 fixture（见下方「重建 fixture」） |
| `fixture_gray.mp4` / `fixture_zerochroma.mp4` | 各 0.5M | 色度归零回归的对照组，脚本内可重建 |
| `fixture_borrow.mp4` / `borrow_real.mp4` | 30K | `verify_borrow_enhancement.py` 的夹具与样本 |
| `eqq_dashboard.py` / `m2_after_ve.sh` | 6K | 本机辅助脚本（看板、标定启动器），docstring 明写「放在 temp/，仅本机使用」 |
| `vidls.py.bak` | 72K | 旧版备份，`.gitignore` 已有 `*.bak*` 规则 |

已删的零散文件：`中文测试影片.mp4`（1 字节，`moov atom not found`，
ffprobe 无法解析 —— 是文件名编码测试的残骸）、`m2_6s_rav1e.log`（0 字节）。

### 重建 fixture（清理后实测）

`fixture_1080p.mp4` 被 6 处引用（`verify_cli_parsing` / `verify_color_tagging` /
`verify_cuda_decode_codec` / `verify_overview_lockstep` / `verify_chroma_hook` /
`t4_acceptance`），其中只有部分带 `if not exists` 守卫。**实际可用的重建命令**：

```bash
python3 Accessory/verify/verify_color_tagging.py   # 它会自建 fixture 后跑自己的判据
# 或直接手搓：
ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 \
       -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
       Accessory/temp/fixture_1080p.mp4
```

> ⚠ **此前 `verify_cli_parsing.py` 的缺素材提示写的是「先跑一次
> `test/dump_filter_chains.sh` 生成」—— 这是错的**：该脚本建的是
> `temp/lockstep/land.mp4` 与 `port.mp4`，**不产 `fixture_1080p.mp4`**。
> 已在本次一并改正（否则清理 temp 后照提示操作会一直失败）。
> 这条也是「报告断言必须核实」的一个实例：提示语本身也会过期。

## 复核方式

本清单的每条判定都可用以下方式独立复核：

```bash
# 归档内容确在
find Accessory/archive -type f | wc -l          # 应为 82

# 「无生成脚本」类判据
grep -rn "temp/videos\|video_2026" Accessory/{probe,verify,test} README.md   # 应零命中

# 「手工汇总」类判据（须排除 archive_manifest.md 自身 —— 它要提到这些名字，
# 且 --exclude 按名字匹配、不认相对/绝对路径，故用 --exclude-dir 之外的
# -w 过滤最稳：只扫代码与文档，不扫本清单）
grep -rn "comparison_summary\|state_2026\|anchorB_x264" \
     Accessory/probe Accessory/verify Accessory/test Accessory/test \
     Plan README.md memory 2>/dev/null | wc -l   # 应为 0
```

## 维护约定

- 归档**只增不删**：新出现的不可再生结论按同样格式补条目与判据
- `archive/` 本身 gitignored，**只有本清单入库**——清单记录「有什么、
  为何不能删」，内容留在本机
- 清理 `temp/` 前先核对本清单：清单里的文件已归档，其余可按「重建依据」列重跑
