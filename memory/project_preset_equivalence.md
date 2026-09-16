---
name: 两个裁剪脚本的行为一致约定（preset 换算、概览展示、--codec auto 解析）
description: vidcrop_hwaccel.py 与 vidcrop_cpu_v2.py 的 NVENC↔x264 preset 表必须一致；降级到 CPU 编码器时 preset 要按"请求的编码器"换算；概览块只展示最终命令里真正会出现的参数；--codec auto 必须解析成具体编码器而不能透传
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
