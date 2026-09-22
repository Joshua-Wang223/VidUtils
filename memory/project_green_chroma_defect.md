---
name: 裁剪产物色度归零（画面全绿）——根因是 ffmpeg 自动插入的 auto_scale
description: 2026-09-22 定案。vidcrop_hwaccel 在「硬解 + NVENC + 输出端色彩四参」链上把产物 U/V 写成 0（下游 YUV→RGB 得 RGB(0,255,0) → 全绿）。根因不是 hwdownload、不是 -hwaccel auto、也不是 NVENC 本身，而是输出端 -colorspace 与解码帧 csp:unknown 不一致时，ffmpeg 自动在链尾插入一个 CPU scale（auto_scale_0）去凑 codec context，这个转换清零了色度。修法 = 用 setparams 把色彩属性标到帧上（GPU 编码器也要），并加「链尾是 CUDA 原生滤镜则不追加」的守卫；另加了产物色度自检钩子 + --no-chroma-check。附带修掉探针 6b 把「最小恢复集」算反（恒报 0 组）的 bug。
type: project
---

## 现象与根因（都已在 T4 上实测复现）

**现象**：`vidcrop_hwaccel.py --mode crop`（硬解 + NVENC）产出的 HEVC，U/V 平面几乎全 0
（原始 dump：99.07% 的 U 采样为 0、最大值 3），Y 平面完好。下游 Video_Enhancement 的
YUV→RGB 据此得到 RGB(0,255,0) → 成品全绿。**下游无责**（`split_video_by_time` 是
`-c copy` 比特流复制，物理上改不了像素）。

**根因（debug 日志一句话）**：

```
[format @ ...] auto-inserting filter 'auto_scale_0' between 'Parsed_crop_0' and 'format'
[auto_scale_0 @ ...] fmt:nv12 csp:unknown range:unknown -> fmt:nv12 csp:smpte170m range:tv
```

输出端 `-colorspace/-color_primaries/-color_trc/-color_range`（`build_color_args`，无条件下发）
让 ffmpeg 认为要把帧的**色彩属性**从"未知"凑成"smpte170m/tv"，于是在滤镜链尾与编码器之间
**自动插一个 CPU scale**。这个自建 ffmpeg 7.1 的 swscale 转换在该链型下把 U/V 清零。

## 单参数二分（本次新增，直接把范围缩到一个选项）

基线 = `-hwaccel cuda` + `crop` + `hevc_nvenc`（正常 U/V ≈ 148.75 / 109.74）：

| 只加这一个 | U/V | 结论 |
|---|---|---|
| `-colorspace smpte170m` | **0.003 / 0.003** | **单参数即复现** |
| `-color_primaries smpte170m` | 148.75 / 109.74 | 无关 |
| `-color_trc smpte170m` | 148.75 / 109.74 | 无关 |
| `-color_range tv` | 148.75 / 109.74 | 无关 |
| 四参 `bt709` | 147.54 / 111.22 | 不归零，但**也插了 auto-scale**、色度有偏移 |

- **硬解是必要条件**：四参 + 去硬解 → 正常（探针 V9）；软解 + NVENC + 四参 → 正常。
- 故障三要素：**硬解 + NVENC(+色彩四参)**；缺任一都不复现。
- 探针 `V1_删输出色彩四参` 恢复正常、`V8/V9` 判出「四参 × 硬解」成对；
  修正后的 6b 穷举给出**唯一最小恢复集 = 色彩四参（1 组）**，绿 32 / 正常 32。

## 修复（`vidcrop_hwaccel.py`）

把 `setparams` 的下发从「仅软件编码器」放宽到**所有编码器**（`copy` 除外）：

```python
_tail = vf_filter.rsplit(',', 1)[-1].split('=', 1)[0].strip()
if (_sp and codec.lower() != 'copy'
        and _tail not in _HW_OUTPUT_FILTERS            # 链尾还在显存里就接不住
        and not (not vf_filter and hwaccel_output_format == 'cuda')):
    vf_filter = f'{vf_filter},{_sp}' if vf_filter else _sp
```

帧属性被标成与输出端一致后，ffmpeg 就不再插 auto_scale。输出端四参**保留**——它负责写
容器 colr box；实测两者并存时既不触发转换、标签也更完整（`h264_nvenc` 此前只写出
`color_space`，现在 primaries/transfer 也正确落盘）。

`_HW_OUTPUT_FILTERS = _CUDA_NATIVE_FILTERS - {'hwdownload'}`：`hwdownload` 输出的是
**软件帧**，所以链尾是它时仍然要（且能）追加 setparams。

## 为什么不改 `vidcrop_cpu_v2.py`（孪生脚本）

1. **v2 没有硬解轴**（无 `--decode` / 不构造 `-hwaccel`）→ 缺三要素之一，实测不复现；
2. v2 的 `build_ffmpeg_cmd` **一直无条件追加 setparams**（`if _sp:` 不看编解码器）→
   它的 GPU 编码（软解 + NVENC）路径早就把帧标好了，压根没有 auto-scale。

⇒ 本次改动等于**让 hwaccel 向 v2 的孪生行为看齐**，只多了一条零拷贝 CUDA 链的守卫。
`project_preset_equivalence.md` 的一致性约定（preset 表 / mode 语义 / 校验文案）不受影响。

## 防复发钩子（默认开）

- `_chroma_verdict(src, out)` 纯判定 + `_chroma_check()` 取样（各跑 1 次 `-frames:v 2 -f null`
  的 signalstats）→ 挂在策略循环 `rc==0` 判定处，失败即把 `rc` 打成 1，复用既有
  「策略失败 → 清理 → 下一策略」降级链，**不需要新写降级**。
- 阈值（SELFTEST 已验证不误伤）：`16<=Y<=235 且 U<16 且 V<16 且 |U-V|<8 且源 U/V≥16
  且 |ΔU|>32 或 |ΔV|>32`。真灰度是 U=V=128（不是 0）→ 不触发；纯绿 RGB(0,255,0) U≈54 → 不触发。
- ⚠ **采样点不用固定第 1 秒**：实测片头是空白帧（正常片 `ss=1` 时 Y=16.0、U=V=128），
  落在 Y 门之外会让"是否归零"无从判定。改成 `clamp(时长×0.1, 1, 60)`。
- `--no-chroma-check` 可关（每次多 2 次短取样，实测 +0.38s/文件）。

## 探针 6b 的 bug（一并修）

`probe/probe_green_chroma.sh` 的 6b 把「最小恢复集」的 popcount 取在**绿色（复现故障）**
分支里 → 永远是 `m=0`（删 0 组）→ 打印「共 0 组」。已移到 else（恢复正常）分支，并把
命令本身失败的子集单独计为"失败"、不再当成"恢复"。改后输出：
`绿 32 / 正常 32 / 失败 0`、`最小恢复集 = 色彩四参（1 组）`。
另：当 `MIN_N==0`（一个都不删就正常）时打印"本轮没有复现故障"，避免与"共 0 组"混淆。

## 验收数据（2026-09-22，T4）

- 脚本矩阵：`default` / `--decode cuda` 的 U/V 从 0 回到 ~107~127（源 126.9/121.9）；
  `--decode cpu` / `--codec libx265` / `all-cpu` 不退化。
- 探针在**修复后的脚本**上重跑：第 4 节 6 格全正常、`V0` 已不绿、6b 打印
  `绿 0 / 正常 8 / 失败 0` + 「本轮没有复现故障」——两个方向都验证过（改前 1 组、改后 0 组）。
- 真片 S02E01（768×576）：整片重裁未归零，`-c copy` 切 3 段逐段有色度。
- 吞吐（crop 60s 片段，min of 3 墙钟）：改前 5.89s → 改后（关钩子）**5.53s**
  （去掉 auto_scale 反而略快）；开钩子 6.27s（多出的 0.38s 就是取样）。
- cover（4K 30s → 1440x1080，min of 3 墙钟）：改前 **17.57s** → 改后 **12.26s**
  （**快 30%**，省下的正是那个多余的 auto_scale 转换）；标签由
  `tv,bt709,unknown,unknown` 变为 `tv,bt709,bt709,bt709`；PSNR(改后 vs 改前) = **47.50 dB**
  （差异就是去掉了那次转换，改后才是链路的真实输出）。
- 新工装：`verify/verify_color_tagging.py`（命令级，无需 GPU）、`verify/verify_chroma_hook.py`
  （阈值/取样/降级）、`test/test_green_chroma_regression.sh`（端到端 + **红灯自检**：
  删掉命令里的 setparams 必须复现 U/V<16，否则测试算失效）。
- `test/dump_filter_chains.sh` 与 baseline 逐字不变（它的 `pick` 本来就剥掉 `,setparams=`）；
  `verify/verify_pixfmt_bitdepth.py` 的 `run()` 也补了同样的剥离（色彩后缀由
  `verify_color_tagging.py` 单独管）。
- ⚠ `verify/verify_decode_axis.sh` 第 ⑦ 组与 `verify_cuda_decode_codec.py` 第 ⑥ 组
  在**有 GPU 的机器上必失败**（它们硬写"本机无 CUDA / 无 N 卡"）——改前改后一样，
  是环境假设过时，不是本次回归。

## Why

这条链上"部件都在、rc=0、尺寸对"全都不代表产物对：缺陷产物字节合法、能解码、只是像素
被写坏，而且**下游还在忠实处理**（把 U/V=0 渲染成绿色）。所以定位必须靠**逐 token 二分**，
不能靠静态分析——上一轮凭代码推的两个假设（缺显式 hwdownload、`-hwaccel auto`）都是错的。

## How to apply

- 再遇到"产物颜色不对"，先上这条判据链：`ffmpeg -ss ... -vf signalstats,metadata=print`
  看 UAVG/VAVG → 再用 `-loglevel verbose` 看有没有 `auto-inserting filter 'auto_scale'`。
- 改色彩相关代码时守住：**帧属性与输出端参数必须一致**（否则 ffmpeg 会插转换），
  且 `setparams` 必须放在链尾、链尾在显存里时不能加。
- 别把 `-colorspace` 输出参数当纯标签删掉——它同时写容器 colr box；正解是"两边都写、取值同源"。
