---
name: -shortest 截断缺陷的三层根因与自动降级
description: convert_sdr_to_hdr 音轨偏短时丢帧/失败的完整根因链、四个修法实测结论、显式参数被覆盖的处理
type: project
---

2026-10-09 排查并修复 `convert_sdr_to_hdr.py` 的音频截断缺陷。**症状是表症，病根在别处。**

**Why:** 用户报「音轨偏短时转换失败/丢帧」。实测发现是**三层缺陷叠加**，
只修表症（`-shortest`）会留下更隐蔽的失败；而四个候选修法里两个看起来最"标准"
的其实无效或与默认值冲突。

## 症状

`--audio copy`（默认）遇到源音轨比视频短：

| 源 | 修复前产物 | 结果 |
|---|---|---|
| 音轨短 17s（视频 22s/550 帧） | — | **EXIT=1**，BrokenPipe，丢 425 帧 |
| 音轨短 0.06s（真实素材 `test3.mp4`，视频 302 帧） | 298 帧 | 丢 4 帧，**EXIT=0 零告警** |
| 音轨长 15s（视频 5s/125 帧） | — | **EXIT=1**「期望 250 帧、实际 125」 |

## 三层根因

**① 病根（最严重）：`probe_source()` 的 `duration` 取 `format.duration`**
（容器时长，被**最长流即音轨**决定），不是视频流自己的 duration。
实测「视频 5s + 音轨 20s」的 mkv：`format.duration=20.0` 而 `video.duration=5.0`
⇒ 分段并行按 20s 切段，每段期望 20×25/2=250 帧、实际只有 62 帧
⇒ 触发 `run_job` 的截断检测，**整条转换判失败**。
⇒ 修法：`duration` 优先取 `video.duration`，容器值仅兜底。
**修完该素材 EXIT=1 → 0**（这条与 `-shortest` 无关，是独立的一层）。

**② `probe_source()` 不探测音轨时长** ⇒ 无法判断「音轨是否比视频短」
（那是 `-shortest` 截视频的前提）。新增 `audio_duration` 字段。
⚠ **踩坑**：`_frac_to_float()` 解析失败返回 **`None` 而不是 `0.0`**，
`None or None` 仍是 None ⇒ `if audio_duration <= 0:` 抛
`TypeError: '<=' not supported between 'NoneType' and 'int'`，
**带字幕的 mkv 直接崩溃**（mp4 与无字幕素材的 duration 都有值 ⇒ 只在 mkv 暴露）。
⇒ **`or` 链每一环都可能返回 None，链尾必须显式 `or 0.0`**。

**③ 表症：`-shortest` 本身** ⇒ 改为「自动降级为重编码 + `-af apad` 补静音」。

## 四个修法实测结论（勿再尝试）

| 修法 | 结果 |
|---|---|
| **A 直接删 `-shortest`** | 三类素材都 EXIT=0 且视频完整，**但音轨偏长时音轨保留 20s vs 视频 5s** ⇒ 播放末尾静默 15s。⚠ 更严重：删掉后若 `apad` 可达，音频**无界膨胀**（10s→1,048,624B、20s→1,572,912B 线性增长、`EXIT=124` 永不自行结束），中途产物是 `moov atom not found` 的**不可播放文件** ⇒ **`-shortest` 是 `apad` 的唯一边界，必须保留** |
| **B `-shortest` + `-max_interleave_delta`** | **无效**。实测 0.05/1.0 秒都仍截到 5s/125 帧。它管交错缓冲，不改截断判定 |
| **C `-af apad` + `-c:a copy`** | **不可行**：ffmpeg 明确报 `Filtergraph 'apad' was specified, but codec copy was selected` ⇒ **rc=234**。这是「自动降级」存在的根本原因 |
| **D `-t <视频时长>` + `-shortest`** | **无效**。`-t` 是输出选项、作用于所有流；`-shortest` 仍按最短流截断 |

## 最终方案：自动降级 + 显式告警

| 场景 | 行为 |
|---|---|
| 默认 `copy` + mp4/mkv | 降级 **aac** + `-af apad`（WebM 降 **libopus**） |
| 显式 `--audio-codec libopus` | **保留用户选择**，只加 `apad` |
| 显式 `--audio-codec copy` | **照样降级**（保帧优先于音轨原样），并明确告知 |
| `--audio none` | 不涉及、不降级（实测保住全部帧） |
| 音视频等长 / 音轨偏长 / 无音轨 | **不触发** |

**关键设计判据**（用户 2026-10-09 追加要求：「显式指定 copy 但预测会丢帧时，
也应自动降级保帧」）：用户显式指定的参数要分两类 ——
**值**（指定了 `libopus`/`aac` 这类编码器）⇒ 尊重不改写，只加补救；
**能力开关**（指定 `copy` 这个「不要重编码」的动作）⇒ **覆盖它**，
因为它正是与正确性冲突的点。覆盖时**必须在提示里点明「你显式指定的 X 同样会被降级」**
并给出去路，否则用户会以为那个参数是能关掉行为的开关。

⚠ 判「是否显式指定」**不能扫 `sys.argv` 全量** —— `--extra-args` 之后的参数是给
下游工具的（`_split_extra_args` 已切走），扫全量会把
`--extra-args -- --audio-codec copy` 误判成「用户显式指定脚本参数」（实测踩过）。
正确做法：`validate_args(args, argv)` 用 argparse 收到的 argv，拿不到时保守判 False。

## 阈值：按帧不按秒，1 帧

`_AUDIO_PAD_MIN_FRAMES = 1`。⚠ **别把它调松**：曾用 3 帧，理由是「丢 1~2 帧是容器
舍入噪声」，而真实素材 `test3.mp4` 音轨短 0.0597s = **1.79 帧** 不触发降级
⇒ 实测**仍丢 3 帧（302→299）且 EXIT=0、零告警** —— 真实丢帧被我的"噪声阈值"
判成了噪声。

噪声底实测：同源等长素材（lavfi 同时产视频与音频）ffprobe 报出的两流 duration
**完全相等（偏差 0.00 帧）**；等长偏差 0、音轨偏长为负 ⇒ 阈值降到 1 帧仍不误触发。
⇒ **给自己设的每个「噪声容差」都要先量噪声的真实幅度**，且要拿**已知的真实缺陷样本**
确认新阈值仍能抓到它。

## 门禁与跨仓影响（实测）

- `-shortest` 在本仓**只出现这一处**；`vidcrop_cpu_v2.py` / `vidcrop_hwaccel.py`
  **都没有** ⇒ 不存在「与孪生脚本逐字相同」的契约要维护。
- `verify_sdr_to_hdr.py` 八组全绿；`chains_before` / `enc_before` / `cmd_before` 逐字相同。
- `cmd_full.txt` diff 104 行是**陈旧基线**（存的是 `D:\...` Windows 路径，
  且 0 处 `shortest`）⇒ 与本改动无关，判据是退出码。
