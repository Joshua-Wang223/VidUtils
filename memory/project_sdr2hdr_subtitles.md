---
name: 字幕处理 --subs auto/keep/burn 的实现、决策与 ffmpeg 组合坑
description: convert_sdr_to_hdr 的字幕三模式已落地（用户已拍板A+条件两阶段）；含「常量点约定不匹配」与「字幕轨稀疏导出0字节」两个新坑
type: project
---

`convert_sdr_to_hdr.py` 原本**从一开始就不保留字幕**（单文件与批量行为一致，
2026-10-09 用户追问后我误答过「只有批量不保留」，已纠正）。用户 2026-09-09 要求
实现 `--subs auto|keep|burn`，**已全部落地并通过全量测试**。

## 用户已拍板的两项决策（不要再重问）

1. **burn不加质量补偿**（选项 A）：burn 保持**单次编码**，不加 `--subs-burn-crf-offset`。
2. **keep 按需两阶段**：只在「会触发 ffmpeg bug」时自动改走两阶段，其余情况单次编码。
   （这是我当时没给到的第三种选项——我原本只问了「一律两阶段」与「一律报错」。）

| 模式 | 行为 |
|---|---|
| `auto`（默认） | 不保留，但**源含字幕时明确告警**（轨数/语言/如何启用）——修复了静默丢失 |
| `none` | 显式声明不要 |
| `keep` | 透传；受容器/codec 限制；**与 `--split-mode segment` 互斥**（报错） |
| `burn` | libass 烧进画面，需 `--subs-index N`（默认 0=第一条） |

全场景实测 8 组全EXIT=0：mkv+auto（字幕0）、mkv+keep/keep+index1（字幕1、两阶段）、
mkv+burn/burn+index1（字幕0，已烧进画面）、mp4+keep（字幕1、单次）、
无字幕源+keep/burn（降级none）。

## ⚠ 新坑一：常量与调用方的「点约定」不匹配（照抄相邻常量时踩过）

我照抄音频侧的 `_MP4_FAMILY = {"mp4","m4v","mov"}` 给字幕用（**不带点**），
而调用方传的是 `dst.suffix`（**带点**形如 `".mp4"`）⇒ `".mp4" in {"mp4",...}`
恒为 False ⇒ 静默落到「未知容器」分支返回 copy ⇒ 不下发 `-c:s`
⇒ mp4 报 rc=8「Automatic encoder selection failed」。

**Why 危险**：常量本身**看起来完全正确**，只在与调用方约定不一致时才发作，
而且症状（rc=8）在三层之下（脚本只报「编码端提前退出」）。
**How to apply**：复用相邻模块的常量前，先确认**调用方传的是哪种形态**；
容器/扩展名类常量统一带点（`dst.suffix`/`Path.suffix` 天然带点），
若某处需要不带点必须显式 `.lstrip(".")`。

## ⚠ 新坑二：mkv 字幕轨是「稀疏」的 ⇒ 导出 rc=0 但文件 0 字节

截取一段**没有字幕事件**的区间（实测 33 轨素材的 300~310s，两条轨都是）时，
导出字幕 `ffmpeg -map 0:s:0 out.srt` 返回 **rc=0、零告警、文件 0 字节**
（stderr 只有一句 `Output file is empty, nothing was encoded`）。
只判 `rc==0 and exists()` 会当成功 ⇒ libass 拿到空文件 ⇒ 编码端报
`Unable to open ...srt` ⇒ 整条转换失败，而真正的信息被埋在三层报错之下。

⇒ 导出后必须查 `stat().st_size > 0`，并给出「本片段时间内没有字幕事件，
换一条轨或用 `--subs keep`」这样的可操作提示。

⚠ 相关素材事实：`../input_videos/The.Creature.Cases.S01E01.The.Mystery.on.the.Monsoon.Express.mkv`
（33 轨 subrip）的字幕**从 31.3 秒才开始有事件**，之前/之后的间隙导出即0 字节。
测 burn 用 `-ss 28 -t 20`。

## 头号坑：ffmpeg「音频 + 字幕 + `-shortest`」共存产出**畸形 mkv**

**症状**：`rc=0`、脚本报「✔ 完成」，但产物只有 3688 字节、
`ffprobe` 报 `Duplicate element`、**视频帧读不出来**。结构对比表整段不输出
（`probe_video_summary` 读不出视频流），这是唯一的侧信道信号。

**二分定位**：只音频+shortest → 正常；只字幕（不带音频）→ 正常；
两者+`-shortest` → **畸形**；去掉 `-shortest` → 正常。脱离脚本手工执行同样复现
⇒ 不是脚本逻辑。

**触发条件（已精确化）**：**源容器不提供各流时长**（`probe_source` 的
`stream_durations_known=False`，典型是 mkv：v/a 的 duration=`N/A`、只有
`format.duration`）。mp4 源各流 duration 齐全 ⇒ 走单次编码正常。
⇒ keep 命中该条件时自动改走两阶段（先编码 → `-c copy` remux 挂字幕，
不重编码、画质无损，只多一次 remux），并**明确告知**已改两阶段。

⚠ 曾在这里浪费很多轮改自己的 `-map` 位置。**教训：排查「产物坏了」先手工跑
一遍 ffmpeg（把 `pipe:0` 换成真实 rawvideo 文件喂 `-i`）**，以区分
「ffmpeg 的行为」与「脚本的逻辑」。

## burn 不需要中间编码（用户已确认采纳此结论）

用户要求「为防重复解码编码，中间编码应把 crf/cq/qp 提高 1-2 级」，实测两条前提
都不成立：`subtitles` 滤镜能直接吃 `gbrp16le`（脚本中间格式）⇒ 仍是单次编码；
且烧字幕的码率代价**只有 1%**（同 crf 下 194KB→196KB，crf 26/25/24 三档一致）
⇒ 提质 1-2 级会让体积涨 15-30%，属过度补偿。

⚠ 另实测：删掉 `-shortest` 后若 `apad` 可达，音频会**无界膨胀**
（10s→1,048,624B、20s→1,572,912B 线性增长、`EXIT=124` 永不自行结束），
中途产物是 `moov atom not found` 的**不可播放文件**。⇒ `-shortest` 是
`apad` 的**唯一边界**，必须保留（与 `project_shortest_audio_truncation` 一致）。

## burn 的滤镜顺序（有讲究）

**`subtitles=...,<hdr_vf>`**（先烧字幕、再转 BT.2020/PQ），而不是反过来。
两种顺序实测都 rc=0 但语义不同：先烧则字幕按**源色域**渲染，随后的
scale/setparams 把整帧（含字幕）一起转到 HDR ⇒ 字幕颜色与画面一致；
反过来字幕会按 PQ/BT.2020 渲染再被转一次，白字偏色/过亮。

## `-map 1:s:N` 的位置（踩过两次）

字幕 map **必须夹在 `1:a:0?` 与 `-shortest` 之间**。放在 `-shortest` **之后**
⇒ 命令变成「输出选项后接输入选项」⇒ 编码端立刻 BrokenPipe。
⚠ 曾试过「自己再补一次 `-i src` 避免跨分支共享标志」—— 那样同样把 `-map`
放到 `-shortest` 之后 ⇒ 产物是畸形mkv。

## 容器/codec 矩阵（实测，与音频那套同构）

| 容器 | `-c:s copy` | 转码 |
|---|---|---|
| mkv | ✅ 唯一能直传 | `-c:s mov_text` **rc=218** 不支持 |
| mp4/mov | ❌ `Could not find tag for codec subrip` ⇒ rc=234 | `mov_text` ✅ |
| webm | ❌ 只收 WebVTT | `webvtt` ✅（若视频编码也合规） |

⚠ **mp4 即使源已是 mov_text 也必须显式给 `-c:s mov_text`**：给 `copy` 报
rc=234、**完全不给报 rc=8「Automatic encoder selection failed」**。源已是
mov_text 时它是无损直通。⇒ resolver 对 mp4 家族**永不返回 None**。

⚠ **位图字幕**（`_BITMAP_SUBS`：PGS/DVD/DVB/xsub）无法转成文本格式（rc=234）
⇒ `keep` 遇位图字幕**报错而非跳过**；`burn` 也报错（libass 只认文本字幕）。

## 顺带修掉的回归（我自己引入的）

给 `probe_source()` 加音轨时长探测时，**带字幕的 mkv 会崩溃**：
`TypeError: '<=' not supported between instances of 'NoneType' and 'int'`。
根因：`_frac_to_float()` 解析失败返回 **`None` 而不是 `0.0`**，`None or None`
仍是 None。⇒ **`or` 链每一环都可能返回 None，链尾必须显式 `or 0.0`**。
⚠ 该回归**只在 mkv + 字幕时暴露**（mp4 与无字幕素材的 duration 都有值）。

## 本脚本没有通用子进程封装（写新代码时注意）

`convert_sdr_to_hdr.py` 各处直接 `subprocess.run(...)`，**没有** `_run()` /
`_ffmpeg_bin()` 这类helper（那是 benchmark 脚本才有的）。我按 benchmark 的
习惯写了 `_run(...)` 与 `_ffmpeg_bin()` ⇒ `NameError` 连击三次。
⇒ 在本文件加代码前先 grep 同类调用，照既有风格写。

## 造带字幕素材的可用命令

```bash
printf "1\n00:00:00,500 --> 00:00:03,000\nHELLO SUB\n\n" > /tmp/s.srt
ffmpeg -nostdin -f lavfi -i "testsrc2=size=320x240:rate=10:duration=5" \
  -f lavfi -i "sine=frequency=440:duration=5" -i /tmp/s.srt \
  -map 0:v -map 1:a -map 2:0 -c:v libx264 -crf 26 -pix_fmt yuv420p \
  -c:a aac -c:s srt /tmp/withsub.mkv
```
⚠ mkv 内嵌字幕要喂给 libass 必须**先导出成文件**（`subtitles=` 吃路径不吃流）；
导出时**保持原格式**（ass 要导 `.ass` 不能导 `.srt`，否则样式全丢）。
⚠ 本机 ffmpeg 命令**必须加 `-nostdin`**，否则无终端环境下会阻塞等 stdin
（曾表现为「一个 5 秒的导出跑 4 分钟不返回」，且`ps` 显示 ffmpeg 占 128% CPU）。