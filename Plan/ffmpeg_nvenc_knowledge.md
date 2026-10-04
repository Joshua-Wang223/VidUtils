# FFmpeg NVENC 编码知识总结

## 1. 版本与 SDK 关系

- **FFmpeg 9.0 起**：NVENC 封装移除废弃选项，包括 `vbr_hq`、`cbr_hq`、`cbr_ld_hq`。
- **原因**：NVIDIA Video Codec SDK API 现代化，旧预设被拆解为可独立控制的参数。
- **要求**：FFmpeg 9.0 要求 NVENC SDK ≥ 11.1。
- **驱动与 SDK**：
  - 驱动 `580.65.06` → 对应 Video Codec SDK **13.0**。
  - Ubuntu 包 `libffmpeg-nvenc-dev 12.1.14.0` → 编译用头文件为 SDK **12.1**，与驱动兼容。
- **软编不受影响**：FFmpeg 9.0 的移除仅针对 NVENC，`libx264`、`libx265` 等软件编码器无变化。

## 2. 速率控制模式 `-rc`

FFmpeg NVENC 封装中，`-rc` 仅暴露三个基础值：

| 值 | 含义 | 特点 |
|---|---|---|
| `constqp` | 固定 QP | 画质相对恒定，码率/文件大小不可预测 |
| `vbr` | 可变码率 | 可配合 `-cq` 实现 QVBR，或配合 `-b:v` 实现目标码率 |
| `cbr` | 恒定码率 | 码率稳定，画质随画面复杂度波动 |

> **注意**：NVENC 封装中**没有**独立的 `-rc qvbr`。QVBR 通过 `-rc vbr -cq <值>` 隐式触发。  
> 其他封装（如 VAAPI）可能存在独立的 `-rc_mode QVBR`。

## 3. 质量参数

| 参数 | 适用模式 | 说明 |
|---|---|---|
| `-cq` | `-rc vbr` | 目标质量等级（0–51，越小越好），启用 QVBR |
| `-qp` | `-rc constqp` | 固定量化参数（0–51，越小越好） |
| `-crf` | 软编（x264/x265） | NVENC 无此原生参数，对应 `-cq` |
| `-qmin` / `-qmax` | CBR/VBR | 限制 QP 范围，间接约束画质 |
| `-b:v` / `-maxrate` / `-bufsize` | CBR/VBR | 码率控制核心参数 |

> **CQ 模式下 `-b:v` 会被丢弃**：FFmpeg 在 CQ 分支强制把 `averageBitRate`、`vbvBufferSize` 清零，只认 `-maxrate`（`nvenc.c` 中 "CQ mode shall discard avg bitrate/vbv buffer size and honor only max bitrate"）。
> 因此 `-b:v 0` 是冗余写法（FFmpeg 自己会清零），而**不给 `-maxrate` 时低 CQ 值可能突然飙到极高码率**。

## 4. 关键模式对比

| 模式 | 目标 | 码率行为 | 画质行为 | 文件大小 | 典型场景 |
|---|---|---|---|---|---|
| `-rc vbr -cq x`（QVBR） | 恒定感知质量 | 动态波动，需 `-maxrate` 限制 | 稳定 | 不可预测 | 高质量归档、本地收藏 |
| `-rc vbr -b:v x -multipass fullres` | 在码率预算内优化质量 | 受 `-b:v` / `-maxrate` 约束，multipass 提升命中精度 | 复杂场景可能波动 | 可预测 | 上传平台、有码率限制 |
| `-rc constqp -qp x` | 固定编码质量 | 完全不可控 | 相对恒定 | 不可预测 | 高质量中间格式 |
| `-rc cbr -b:v x` | 恒定码率 | 严格稳定 | 随画面波动 | 可预测 | 直播推流、带宽受限 |

> **QVBR vs VBR multipass**：QVBR 是“质量驱动，实时反馈”；VBR multipass 是“码率预算，计划分配”。

## 5. `-tune` 与 `-multipass`

### `-tune` 选项

| 值 | 含义 | 场景 |
|---|---|---|
| `hq` | 高质量（默认） | 本地录制、归档 |
| `ll` | 低延迟 | 直播、视频会议 |
| `ull` | 超低延迟 | 交互式远程控制 |
| `lossless` | 无损 | 像素级保留 |

> `uhq` 是 `hevc_nvenc` / `av1_nvenc` 的**原生取值**（`h264_nvenc` 的 `-tune` 只有 hq/ll/ull/lossless，无 uhq）。
> 它会自动启用 lookahead 与 temporal filter，显存占用更高；不需要、也无法用 `-preset p7` + `-multipass fullres` 去"近似"。
> 两者正交：uhq 是调优档位，multipass 是帧内两遍率控，互不替代。

### `-multipass` 选项

| 值 | 含义 | 质量 | 速度 |
|---|---|---|---|
| `disabled` | 单遍 | 最低 | 最快 |
| `qres` | 四分之一分辨率两遍 | 良好 | 平衡 |
| `fullres` | 全分辨率两遍 | 最佳 | 最慢 |

> **`-multipass` 与速率控制模式的关系**（2026-10-04 校正）
>
> 1. **CQ 不是独立于 VBR 的模式**：`-rc vbr -cq N`（QVBR）在 NVENC API 层就是 `NV_ENC_PARAMS_RC_VBR`
>    + `targetQuality`。FFmpeg `nvenc.c` 中未显式给 `-rc` 而给了 `-cq` 时，直接把 rc 判定为 VBR。
>    所以"multipass 仅在 VBR/CBR 有效、CQ 模式下无效"字面上自相矛盾——CQ 本就是 VBR 的一个子模式。
> 2. **真正不适用的是 `constqp`**：固定 QP 没有码率目标可优化，驱动会忽略 multipass
>    （NVEncC 选项文档：`--multipass` 仅对 `--vbr` / `--cbr` 可用）。
> 3. **FFmpeg 不做模式门控**：`rcParams.multiPass = ctx->multipass` 是无条件下发给驱动的，是否生效由驱动决定。
>    唯一会覆盖它的是 legacy preset 别名：`slow` → P7 + 两遍，`medium` / `fast` → 强制单遍。
>    ⚠️ 因此**从 `-preset slow` 改成 `-preset p7` 会静默丢掉两遍率控**（现代 p1–p7 别名不带任何 multipass 标记）。
> 4. **`-tune hq` 是默认值**（`h264_nvenc` / `hevc_nvenc` 均为 `default hq`），显式写上等于没写，
>    不可能带来任何画质变化。它只有在覆盖 `ll` / `ull` / `lossless` 时才有意义。
> 5. **CQ 下的取舍**：NVIDIA 对 multipass 的官方描述是"提升码率控制精度，使实际码率贴近目标，
>    尤其利于 CBR / 紧 VBV，代价是编码时间与显存"。CQ 不设平均码率目标，收益边际很小，
>    却要付出约 2 倍耗时与数 GB 显存。故 CQ 归档场景**可以不加**——但理由是"收益小、成本高"，
>    而不是"会掉画质"。若要叠加，官方自身也有先例（SDK 13.1 指南 §6.2.8"Ultra-High Quality Exports"
>    即 `-preset p7 -tune uhq -rc vbr -cq 19 -maxrate 80M -multipass fullres`）。
>    另注：有第三方实测指出 multipass 可能让输出**非确定**（同参数两次编码结果略有差异）；
>    但 **本机 T4 复跑同一参数两次为 byte-identical（未复现该非确定性，见 §5.1）** —— 标定仍默认不开
>    （主因是 CQ 无收益 + 开销），换驱动 / 并发环境可再复测。
>
> ⚠️ **已撤回的旧论断**：本文此前称"CQ 模式下叠加 `-tune hq -multipass fullres` 会导致质量下降约 2.7–3.3 VMAF，
> 中等质量点（cq=30）可达 4 VMAF 以上"。经核对，其数字来源（arXiv 2605.01187，Netflix Chimera + Twitch 序列）
> 使用的是**纯 CBR**、**全文未测试 `-multipass`**、报的是 **BD-Rate %** 而非 VMAF 分值，与结论三重错配；
> 且"降质量 + 增体积"在码率控制层面自相矛盾。已删除，改为上文的定性表述。
> **✅ 已实测（2026-10-04，Tesla T4 / ffmpeg 9.0.2）**：固定 CQ 的 ±multipass A/B 见表 §5.1 ——
> multipass **不提升 VMAF**（fullres ΔVMAF −0.006~−0.108、qres −0.067~−0.335，码率 ×0.98~0.997），
> 只是把码率控制得更紧。故 **CQ 路径默认不开**；真正受益的是 CBR / 受限码率。
>
> **核对依据**：本机 `ffmpeg 8.1.2` 的 `-h encoder=h264_nvenc` / `-h encoder=hevc_nvenc`；
> FFmpeg master `libavcodec/nvenc.c`（L1036、L1038-1041、L1043-1048、L1139-1151、L240-242）；
> NVIDIA Video Codec SDK 13.1《Using FFmpeg with NVIDIA GPU Hardware Acceleration》§6.1.10 / §6.2.8；
> NVEncC（rigaya）选项文档。
> T4 复核（2026-10-04，`ffmpeg 9.0.2`）：`-tune` 默认 `hq`（h264 1~4 / hevc 1~5）；`uhq` 仅
> hevc/av1（h264 传 `uhq` → rc=234）；`-multipass` 默认 `disabled`（0~2）。

### 5.1 实测：固定 CQ 的 ±multipass A/B（2026-10-04，Tesla T4 / 驱动 580.65.06 / ffmpeg 9.0.2）

口径：`new5_raw` 6s → 720p prep（与等质量标定同口径），`-c:v <codec> -rc vbr -cq <N> -b:v 0
-preset p4`，仅切换 `-multipass`；指标 `libvmaf` 的 pooled VMAF（`n_subsample=1`）。

| codec | cq | disabled VMAF / kbps | qres ΔVMAF / 码率比 | fullres ΔVMAF / 码率比 |
|---|---|---|---|---|
| h264_nvenc | 26 | 99.087 / 3525 | **−0.132** / 0.979× | **−0.034** / 0.994× |
| h264_nvenc | 34 | 87.382 / 1243 | **−0.335** / 0.986× | **−0.108** / 0.997× |
| hevc_nvenc | 26 | 99.265 / 3359 | **−0.067** / 0.980× | **−0.006** / 0.996× |
| hevc_nvenc | 34 | 89.930 / 1177 | **−0.243** / 0.989× | **−0.048** / 0.997× |

**结论**：
- 固定 CQ 下 multipass **不升 VMAF**（qres 更差、fullres 略差），而是把码率收得更紧（命中精度↑）
  ⇒ 「CQ 归档可以不加」由定性变为**有据**：加了只会略降 VMAF/码率、并增加耗时与显存。
- 因此 **CQ / QVBR 路径默认不开**；multipass 的价值在 **CBR / 紧 VBV / 受限码率**（把实际码率
  拉近目标）。这与 §5 中 NVIDIA 对 multipass 的官方定位一致。
- 短片段（6s / 720p）在 T4 上编码耗时差异不明显（各 ~1.3–1.6s）；长片 / 高分辨率才是
  2× 量级的代价来源（本表未放大该成本）。
- ⚠ 有第三方报告 multipass 可能非确定；**本机 T4 复跑同一参数两次 byte-identical（未复现）**。
  故「CQ 路径不开」的**主因是 CQ 无 VMAF 收益 + 额外耗时/显存**，非确定性仅作参考（换环境需复测）。
- **确定性抽测**（h264/hevc × `-cq 34` × 60 帧，各跑两遍）：`-multipass fullres` 与默认的产物
  **md5 逐字节相同**（h264 fullres `90078a8…` / disabled `bab7a01…`；hevc fullres `ed4bcd6…` /
  disabled `992a2ec…`）⇒ 本机**未复现**「multipass 非确定」。

### 5.2 VidUtils / VE 落地（2026-10-04）

- **默认路径保持裸 `-rc vbr -cq N -b:v 0 -preset p4`**：不加 `-tune hq`（默认值，写了等于没写）、
  不加 `-multipass`（上表：CQ 无收益 + 额外开销）。
- 新增**显式 opt-in**（VidUtils 两脚本孪生 `--nvenc-tune` / `--nvenc-multipass`）：
  · `--nvenc-tune {hq,ll,ull,lossless,uhq}` —— 默认不发；`uhq` 仅 hevc/av1（h264 报错）；
  · `--nvenc-multipass {disabled,qres,fullres}` —— 默认不发；`--rc-mode cbr` 或给了 `--bitrate`
    时**自动补 fullres**（该场景才有意义），可用本参数显式覆盖（含 `disabled`）。
- 等质量标定 harness（`probe/calibrate_equal_quality.py`）**不受影响**：`BASE_LOCK` 依旧裸 `-rc vbr`。
- ⚠ 别把 `-preset p7` 当 two-pass：现代 `p1~p7` 别名不带 multipass 标记（只有 legacy `slow` 会开两遍）。

## 6. 软编 vs 硬编

- **软件编码**：无 `-rc` 参数，通过参数组合隐式指定模式：
  - 恒定质量：`-crf 23`
  - 目标码率：`-b:v 6M`
  - 恒定码率：`-b:v 6M -maxrate 6M -minrate 6M -bufsize 12M`
  - 固定 QP：`-qp 23`
- **FFmpeg 9.0 对软编无影响**，命令无需修改。

## 7. 常用命令示例

### QVBR（恒定质量）
```bash
ffmpeg -i input.mp4 -c:v h264_nvenc -rc vbr -cq 23 -maxrate 10M -b:v 0 output.mp4