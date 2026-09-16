---
name: preset 档位换算与展示的三条约定（两脚本同表、降级后等效、概览只显示真参数）
description: vidcrop_hwaccel.py 与 vidcrop_cpu_v2.py 的 NVENC↔x264 preset 表必须一致；降级到 CPU 编码器时 preset 要按"请求的编码器"换算；概览块只展示最终命令里真正会出现的参数
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
