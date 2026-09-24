---
name: 两个裁剪脚本的行为一致约定（preset 换算、概览展示、--codec auto 解析、--mode 语义与校验）
description: vidcrop_hwaccel.py 与 vidcrop_cpu_v2.py 的 NVENC↔x264 preset 表必须一致；降级到 CPU 编码器时 preset 要按"请求的编码器"换算；概览块只展示最终命令里真正会出现的参数；--codec auto 必须解析成具体编码器而不能透传；--mode（含 crop-cover）的语义、参数校验、同尺寸跳过判定也必须两边一样
type: project
---

## 约定 1：两个裁剪脚本的 preset 表必须逐字一致

`NVENC_TO_X264_PRESET` 在 `vidcrop_hwaccel.py` 与 `vidcrop_cpu_v2.py` 里各有一份，
**两处必须相同**。2026-09-16 发现它们曾经错位一档：

| NVENC | hwaccel（错） | cpu_v2（对） |
|---|---|---|
| p4 | medium | faster |
| p5 | slow | medium |
| p6 | slower | slow |

**Why:** 后果是同一条 `--preset p5` 在两个脚本里落到不同档位（一个 `slow` 一个 `medium`），
用户按 README 的"两个脚本参数一致"预期去用会被坑。而且错位那版还会把 `--preset faster`
判成"无对应"并静默退回默认值。已按 cpu_v2 对齐（把 NVENC 7 档均匀铺在 x264 阶梯上，
两端各留一档：`faster` 起、`veryslow` 止）。
**How to apply:** 动其中一处就要同步另一处。若为某个编码器新增映射，两个文件一起改，
并顺手确认 `README.md` 里 `--preset` 那一行是否还说得到。

## 约定 2：降级后的 preset 要"与降级前等效"

`h264_nvenc` / `hevc_nvenc` / `av1_nvenc` 不可用时会被替换成 CPU 编码器
（见 `_get_software_fallback`）。**用户没指定 `--preset` 时，基准档位要取"用户请求的那个
编码器"的默认值，再换算到实际编码器**（见 `vidcrop_hwaccel.py` 的 `strategy_preset()`），
而不是取目标编码器自己的默认值：

- `h264_nvenc`(默认 p5) → `libx264`  得 `medium`
- `av1_nvenc` (默认 p5) → `libsvtav1` 得 `8`

**Why:** 取目标编码器自己的默认值会让档位漂移——`libsvtav1` 的默认值还是按 CPU 核数变
（`_AUTO_EFFORT_TIERS`：≥16 核给 7、8 核给 8、4 核给 9、更少给 10），同一份请求在不同
机器上会得到不同的速度/质量档，用户拿到的就不是他请求的那个档位了。
**How to apply:** 新增"硬件编码器 → CPU 编码器"的降级路径时，照 `strategy_preset()` 的
写法走，别在策略循环里直接 `default_preset_for(当前编码器)`。

## 配套：概览块里的 preset 字段怎么展示

概览块的「编码器」行展示的必须是**实际会生效**的那一个，三个具体要求：

1. 编码器名取 `_generate_strategies(...)` **第一条策略的 codec**——`--codec auto` 由此被解析成
   具体编码器（h264_nvenc 或 libx264），不用再单独判断一遍；NVENC 不可用时的 CPU 降级也已
   反映在里面。
2. 默认质量参数按实际生效的编码器取（`libsvtav1` 只认 `-crf`，若还按请求的 `av1_nvenc` 显示
   `CQ: 23` 就与本行编码器自相矛盾）。
3. `encoder_supports_preset()` 为假的编码器（`libvpx-vp9` / `libaom-av1` / `librav1e`）
   **不展示** preset 字段——命令构建处根本不下发，展示了就是假信息。两个脚本都要这样判断。

**Why:** 这三点都是"显示了命令里其实没有（或不是这个值）的参数"，用户在 dry-run 里一比对
就会发现概览不可信，进而怀疑整份命令。
**How to apply:** 改概览块时先问"这一行显示的值，最终命令里真的有吗"。逐策略的换算提示照旧
逐文件打印，概览那次用 `quiet=True`，免得同一句话打两遍。

## 约定 3：`--codec auto` 必须解析成具体编码器，不能透传

2026-09-16 发现 `vidcrop_cpu_v2.py` 的 `--codec auto` 会把 `auto` **原样**下发成 `-c:v auto`，
ffmpeg 报 `Unknown encoder 'auto'`（rc=8）直接失败；概览块也原样显示 `编码器: auto`。
根因是 `normalize_codec_name()` 里那句 `if codec in ("auto", "copy"): return codec` —— `copy`
是合法的 ffmpeg 取值可以透传，`auto` 不是，这句是从硬件版抄过来的残留。

修法：硬件版取策略链第一条的 codec（`auto` 由此解析成 `h264_nvenc` 或 `libx264`）；
CPU 版把 `auto` 解析成 `DEFAULT_CODEC`（= `libx264`），并用同一个常量做 argparse 默认值，
从结构上保证"不指定 --codec"和"`--codec auto`"落到同一个编码器。解析必须放在
`default_preset_for()` **之前**——否则 `encoder_supports_preset("auto")` 为假，概览会漏掉
preset 字段。

**Why:** `auto` 是用户最容易顺手写的值（README 里硬件版的 `--codec` 就写着"支持 auto"），
透传后只得到一个 ffmpeg 层面的 `Unknown encoder`，既不提是哪个脚本的哪个参数，也不好排查。
**How to apply:** 两脚本的 `--codec auto` 在**没有 NVENC 的机器上**必须给出逐字相同的概览与
命令（`libx264  preset: medium  CRF: 21` / `-c:v libx264 -crf 21 -preset medium`）；
在有 NVENC 的机器上硬件版解析成 `h264_nvenc` 属预期差异（CPU 版没有硬件路径），不算不一致。
再往 `normalize_codec_name()` 的透传白名单里加值时，先确认它在 ffmpeg 侧是合法取值。

## 约定 4：`--mode` 的语义、校验与跳过判定两个脚本一模一样

2026-09-18 加了 `--mode crop-cover`（**先裁剪，再把裁剪结果缩放覆盖到最终尺寸**，
可放大）。这个模式是**两个脚本一起改**的，改的时候必须保证：

**① 参数校验逻辑与文案逐字一致**（2026-09-18 逐个用例比对过 17 条，全部逐字相同）：

| 情形 | 结果 |
|---|---|
| `--mode crop-cover` 且**无** `--crop-ratio` | 必须同时提供 `--output-width` 和 `--output-height`，否则报错 |
| `--mode crop-cover` 且**有** `--crop-ratio` | 只需给其中一个维度，另一个按 `crop_ratio` 推导 |
| 其余模式 | `--crop-ratio` 与 `--output-*` **互斥报错** |

⚠️ 最后一条是 2026-09-18 才统一的：**cpu_v2 原先走的是「忽略尺寸 + 打印提示」继续跑**
（`提示：使用 --crop-ratio 时，--output-width/height 将被忽略，自动按比例计算。`），
于是 `--mode crop --crop-ratio 16:9 --output-width 320 --output-height 180`
在 hwaccel 里报错、在 cpu_v2 里静默出结果。用户选择「跟随 hwaccel 改为报错」，
那句提示已删除。**README 与两个 `--help` 都写着"二选一"，报错才是文档承诺的行为。**

同一轮还统一了三类纯文案差异：句末句号（hwaccel 一律带 `。`，cpu_v2 的 `raise ValueError`
原先多数不带）、全角括号（`（libx264 CRF 量程）` vs `(libx264 CRF 量程)`）、
以及「都不给 / 只给一个维度且无 ratio」那条措辞（cpu_v2 原为
`未指定 --crop-ratio 时，必须提供 --output-width 和 --output-height`，
已改为 hwaccel 的 `必须指定 --output-width/--output-height 或 --crop-ratio 其中之一。`）。

**校验顺序也要一致**：量程检查（`--crf` / `--cq` / `--crf-ref` / `--cq-ref`）必须排在
`_resolve_quality_params()` **之前**。cpu_v2 原先顺序反了，`--crf-ref 99` 会先打印
`提示：--crf-ref 99 (libx264 CRF 基准) → libx264 的 -crf 51。` 再报"范围为 0-51"，自相矛盾。
hwaccel 的完整顺序是：`-ref` 互斥 → 尺寸校验（crop-cover 分支 / 互斥 / 正整数 / crop-ratio
解析 / 单维度补全）→ `--crf`/`--cq` 量程 → `-ref` 量程 →（`_resolve_quality_params` 留到
`process_file` 里）。cpu_v2 的 `validate_and_finalize_args()` 已按同一顺序重排。

推导出的维度必须取**偶数**（两文件同名函数 `derive_even_dimension()`，取整后奇数就 +1，
不低于 2）：4:2:0 系 pix_fmt（`yuv420p` / `yuv420p10le` / `p010le`）要求宽高均为偶数。

**已知仍未对齐的（暂按现状，两边行为一致所以不构成分叉）**：

- 非 crop-cover 模式下 `--crop-ratio` + **只给一个**维度（如 `--mode crop --crop-ratio 16:9
  --output-width 320`）：两边都**静默忽略**那个维度（判据是 `has_explicit_size` 要求两个都
  给，只给一个不算"同时指定"）。要改就两边一起改成报错。
- hwaccel **没有** `--original-width/height` 的正整数校验（cpu_v2 有），传负值会一路带下去。

**② 滤镜链**：`crop=<按 ratio 最大化裁剪>,<cover 缩放>`。未给 `--crop-ratio` 时第二段的
比例与裁剪结果一致，整条链退化成 `crop=...,scale=W:H`（纯裁剪 + 纯缩放，没有二次裁剪）。
`--crop-ratio` 与最终尺寸比例不一致时才会走 `scale=-2:H,crop=...`。
`crop-cover` 含 scale 步骤，硬解版里同样要**跳过策略 1（crop_cuda 全 GPU 流水线）**——
判据已经是 `mode == 'crop'`，加新模式时别再写成"非 cover 都行"。

**②b 是单次 ffmpeg，不是跑两遍**（2026-09-18 用户专门问过）：两段滤镜在
`_build_crop_cover_filter_str()` 里用逗号拼成**一个字符串**，作为**单个** `-filter:v:0`
下发；每次策略尝试只 `_run_with_progress(cmd)` 一次 = 一个 `Popen`。实测（640×480 →
crop-cover → 1280×720，耗时 2.7s）：并发 ffmpeg 进程数采样 `1 1 1 1 0 0 …`（峰值 1），
输出目录只有最终产物、无中间文件，`-i` 只出现一次。所以是**一次解码 + 一次编码**，
"先裁剪后缩放"只体现为滤镜图内的链式依赖（逐帧流式通过，不等整段裁完）。
别再把它描述成"两遍转码"——那会多一次有损压缩和一次额外 IO。

**③ 同尺寸跳过要先看"裁剪步骤是否为空操作"**：比例与源不同时，即使最终尺寸等于源尺寸，
画面也已经变了（先裁掉一圈再缩放回来）。实测 640×480 源 + `--crop-ratio 16:9` +
`--output-width 640 --output-height 480` **必须转码**（得 `crop=640:360:0:60,scale=-2:480,
crop=640:480:...`）；而无 `--crop-ratio` 的同尺寸则正常跳过。两个脚本都按这个判据。

**④ 输出后缀**：`crop`→`_cropped`、`cover`→`_covered`、`crop-cover`→`_cropcovered`
（两脚本同一张映射表，`--flag` 仍可覆盖）。

**Why:** 用户是按"两个脚本参数一致"的预期在用的（README 与 `--help` 都这么写），
任何一边漏改都会让同一条命令行在一边报错、在另一边静默出另一种结果；
而跳过判定这类"看起来等价"的地方最容易漏——尺寸一样就跳过是原有判据，
新模式下它会把"比例不同、画面确实要变"的文件误判成无需处理。
**How to apply:** 以后再加 `--mode` 取值或改模式语义：两个文件一起改，改完用
`--dry-run` 在同一个源上比两边的滤镜链是否等价，并各跑一次"尺寸同源但比例不同"的用例。
`vidcrop_hwaccel - Copy.py` / `vidcrop_cpu_v2 - Copy.py` 是用户自己的备份，**不要同步改**。

**边界（哪些参数可以不齐）**：`--decode` / `--fallback-policy` / `--cuda-diagnostics` /
`--cuda-device-id` 只在 hwaccel 侧存在，v2 没有——这是有意的（v2 是纯 CPU 后端）。
但**共享参数**的取值表 / 别名表 / 默认值 / 报错首行必须逐字一致（`--scale-algo` 是当前
唯一的共享新参数，已按此对齐；唯一有意的差异是 v2 可省 `libswscale-` 前缀、且拒绝 `cuda-*`）。
三轴模型本身见 `project_three_axis_model.md`。
