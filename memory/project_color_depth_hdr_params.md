---
name: 像素格式 / 位深 / HDR 三个新参数（--pix-fmt / --bit-depth / --hdr）的设计与实测边界
description: 2026-09-20 给 vidcrop_hwaccel.py 与 vidcrop_cpu_v2.py 新增三个参数（--pix-fmt / --bit-depth / --hdr）；含零拷贝链不能传 -pix_fmt、scale_cuda=format= 与 -profile:v 的约束、tonemap_cuda 上游不存在、以及 2026-09-21 修掉的 --pix-fmt×--bit-depth「能落地者赢」规则
type: project
---

## 三个参数（2026-09-20 落地）

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--pix-fmt` | `auto` / `none` / 具体 ffmpeg 名 | `auto` | hwaccel 此前**没有**这个参数，cpu_v2 有；现已补齐，取值与校验两脚本一致 |
| `--bit-depth` | `auto` / `8` / `10` / `12` | `auto` | 位深此前完全隐式（从源继承），现可显式指定 |
| `--hdr` | `auto` / `keep` / `drop` / `sdr[:<algo>]` | `auto` | `sdr` 真做 HDR→SDR tone mapping；algo 默认 `mobius` |

**硬要求**：三个默认都是 `auto`，不传时命令与改动前**逐字相同**（16/16 + 默认路径 5/5 + 命令级 7/7 三道门）。

## 关键约束与决策

- **零拷贝 CUDA 链（`-hwaccel_output_format cuda`）不能传 `-pix_fmt`**：`-pix_fmt` 设的是
  `AVFrame.format`（该链上是 `AV_PIX_FMT_CUDA`）而非 `sw_format`，传 `nv12`/`yuv420p`
  实测都报 `Impossible to convert`（`project_t4_gpu_capabilities.md`）。
  → 已按链型分别落地：零拷贝链写进 `scale_cuda=format=` 并自动配 `-profile:v`
  （`p010le`→`main10`、`yuv444p`→`high444p`）；软件帧链才下发 `-pix_fmt`。
- **`scale_cuda=format=` 只支持 `nv12 / yuv420p / yuv444p / p010le`**；链上用不了的格式
  且**同时给了 `--bit-depth`** 时由它接管（「能落地者赢」，见下），否则按 `--fallback-policy`
  处理（auto=四段式告警并保持，strict=报错退出 2）。
- **改了 `scale_cuda` 的 format 后，紧随的 `hwdownload,format=` 必须同步改**——那个值是按
  源位深烘进滤镜串的，不改会出现「缩放输出 p010le、却要按 nv12 下载」的错配。
- **`--pix-fmt` / `--bit-depth` 的冲突规则 =「能落地者赢」**（2026-09-21 修）：两者语义重叠
  但**不在同一层级**——`--pix-fmt` 是实现级（格式名 = 位深 + 色度 + 排布，信息量是超集，
  但合法性**与链相关**，零拷贝 CUDA 链只收 4 个值）；`--bit-depth` 是意图级（只有位深，
  信息量是子集，但合法性**与链无关**，"10bit"在任何链上都成立，具体格式由链 + 编码器推导）。
  所以"恒定让谁赢"**两端都有反例**：`--pix-fmt` 恒赢 → `--pix-fmt yuv420p10le --bit-depth 10`
  双双失效（实测输出掉回 8bit nv12）；`--bit-depth` 恒赢 → `--pix-fmt yuv444p --bit-depth 10`
  静默降成 4:2:0。正解：先让 `--pix-fmt` 落地；它在当前链上落不了地时由 `--bit-depth` 接管，
  并明说让位代价（`_pixfmt_shape()` 比对位深/色度；有损失且 `strict` → 报错）。
  判据见 `temp/verify_pixfmt_bitdepth.py`（37 格，含两端反例与回归格）。
  ⚠ **曾经有过一个真 bug**：`--pix-fmt` 显式时置 `_pf_handled = True`，而 `--bit-depth` 的
  解析挂在同一条 `elif` 上 → 两个参数同时给出时 `--bit-depth` **压根没被算过**，等
  `--pix-fmt` 被链拒绝后 `_pf_handled` 仍为 True，把整段位深逻辑一起跳过 → **10bit 静默蒸发**，
  而 CLI 那句"已按 --pix-fmt 为准，忽略 --bit-depth"反而让用户以为 `--pix-fmt` 生效了。
- **`tonemap_cuda` 上游不存在**（实测 `Unknown filter`，与 `crop_cuda` 同款）→ CUDA 侧没有
  硬件 tone mapping。`--hdr sdr` 走 CPU 的 `zscale + tonemap + zscale`；CUDA cover 链本来
  就在 crop 前 `hwdownload` 成软件帧，接链尾即可（且应保留 p010le 下载以免提前丢精度）。
- 能力探测是**惰性**的：`--pix-fmt` 显式时才跑 `ffmpeg -pix_fmts`，`--hdr sdr` 时才探测
  `tonemap` / `zscale`；探测失败一律放行或按策略降级，不阻塞任务。

## Why

原来的位深完全隐式、HDR 只透传元数据（libx265 写 `-x265-params`，NVENC 直接告警丢弃），
用户要 10bit→8bit、或要把 HLG 片源转成 SDR 时只能靠 `--extra-args` 手搓滤镜串，
而手搓又正好会踩到「零拷贝链不能传 `-pix_fmt`」这条不显然的约束。

## How to apply

- 再动这三个参数：先跑三道回归门（16/16 滤镜链、默认路径 5/5、命令级 7/7），再改；尤其
  `--pix-fmt` 的新格式支持要确认 `scale_cuda` 是否认。动「优先级 / 冲突」类逻辑还要跑
  `temp/verify_pixfmt_bitdepth.py`——**优先级 bug 不会在单参数测试里暴露**，必须喂
  两参数组合（这次就是两个同给才炸；单给 `--pix-fmt` 或单给 `--bit-depth` 全都正常）。
- 判断"哪个参数该赢"时先问**谁知道能不能落地**：知识在链/编码器那一侧的参数（意图级）
  才适合做仲裁；用户手写的名字（实现级）只适合在它能表达时生效。这跟 `--decode auto`
  用 `_select_best_hwaccel()` 探测、`--scale-algo auto` 用 `_probe_cuda_scale_upload()`
  探测是同一个道理——**别把决定权交给一个不知情的一方**。
- 让位/降级类的自动选择，**必须把代价说出来**（`_pixfmt_shape()` 比对位深 + 色度）；
  静默从 4:4:4 掉到 4:2:0 比直接报错更糟。`strict` 语义 = "不降级"，所以**等价让位
  （`yuv420p10le`→`p010le`）不算降级**，不报错；有信息损失才 raise。
- 报结论时区分**链型**（软件帧链 / 零拷贝 CUDA 链），别笼统说"改了 pix_fmt"。
- **让位路径的上机验证工装**（已就位，仍待跑）：`temp/probe_scale_cuda_crop.sh` +
  `PROBE_10BIT=1` 会跑 A3/A4 两格——A3 = `format=p010le`（脚本让位后下发的滤镜形状），
  A4 = A3 **再加 `-profile:v main10`**（脚本也会下发的那一项），两者是单变量对照，
  **两格都成功**才算让位路径成立。
  ⚠ A4 的 profile 项**没有分辨力**：10bit HEVC 必然 Main 10，传不传 `-profile:v` 都一样
  （libx265 替身演练实测：with/without 都是 `Main 10,…`）→ 该格的增量信息是
  "**不失败 + 输出仍是 yuv420p10le**"，别读成"profile 变了 ⇒ `-profile:v` 生效"。
  又一个"恒等操作测不出东西"的实例（同 `project_cuda_scale_cover.md` 的恒等缩放陷阱）。
- 探针里的 `dl_fmt_of()`（格式名 → 下载格式）是 `_src_download_fmt()`（位深整数 →
  下载格式）的手工镜像，2026-09-21 补齐 16bit → `p012le`（原来 16bit 掉进 `nv12` 分支、
  与脚本分叉，而注释还写着"保持一致"）。**改任一侧都要核另一侧**——这类"声明一致、
  实际分叉"的手工镜像最容易悄悄漂移。
- ⚠ **`--hdr sdr` 的 tone mapping 是全新能力，还没有 T4 实测数据**（滤镜配方、desat=0、
  算法选择都待验）。用于生产前必须在真实 HDR 片源（如 HLG 的 `new4_raw.mp4`）上验。
- 相关：`project_t4_gpu_capabilities.md`（零拷贝 `-pix_fmt` 实测表 + `-profile:v` 要求）、
  `project_three_axis_model.md`（--decode/--scale-algo/--codec 三轴，与这三个新轴正交）、
  `project_preset_equivalence.md`（两脚本逐字对齐约定）。
