---
name: cover 模式的 CUDA 缩放（scale_cuda）——实测数据、两条硬约束、以及一个被激活的老 bug
description: 2026-09-20 给 vidcrop_hwaccel.py 的 cover 模式加了「CUDA 缩放 + CPU 裁剪」策略；真实 4K→1440x1080 实测快 51.9~52.9%（对着 lanczos 基准；对旧 bicubic 基准是 44.5%）；质量门已完整通过（PSNR 46.60dB + VMAF 97.21）；必须显式写 hwdownload 否则 crop 被静默丢弃；crop_cuda 上游不存在；顺带修掉 _src_download_fmt 的 p010/p012 非法 pix_fmt 名；探针的 awk 跨行三元在 T4 的 mawk 上炸过（gawk 兼容 ≠ mawk 兼容）；软解+hwupload 链已实测（尺寸协商正确、比软解+CPU缩放快 2.9~3.2%、画质与零拷贝逐位相同，但绝对值是 44~46s vs 硬解零拷贝 12.8s，定位仍是「NVDEC 用不了时的出路」）；10bit p010le 路径仍空白
type: project
---

## 做了什么

`vidcrop_hwaccel.py` 的 `cover` 模式新增一条策略（现为策略链第 2 条）：

```
NVDEC → scale_cuda=W:H:interp_algo=lanczos → 显式 hwdownload,format=… → CPU crop → NVENC
```

只动 `vidcrop_hwaccel.py` 的策略/滤镜层。**没有新增任何 CLI 参数**，所以两脚本的对齐矩阵不受影响。
`vidcrop_cpu_v2.py` 在第一版里一字未动；随后同一天又按「两条路同档」把**两个脚本的 CPU 侧缩放算法
一并钉成 lanczos**（见下一节），那一处是两边同时改的。

## 缩放档位：两条路都显式钉 lanczos（CPU 侧不再是默认的 bicubic）

**事实（实测，2026-09-20）**：libswscale 的默认缩放算法是 **bicubic**——`scale` 滤镜自身的 `flags`
默认是空串、继承全局 `-sws_flags`，而后者 help 里写着 `(default bicubic)`。证据是帧级 MD5：
`scale=1280:720` 与 `scale=1280:720:flags=bicubic` 解出来的像素**逐位相同**（`psnr` 三平面全 `inf`；
注意**文件级** md5 会因容器差异不同，不能用它判断）。

于是两个脚本里都加了 `_SW_SCALE_FLAGS = 'lanczos'`，`cover` / `crop-cover` 的 `scale` 一律带
`:flags=lanczos`。为什么是 lanczos：

- **`scale_cuda` 的档位只有 4 个**（`interp_algo` 量程 0~4：nearest / bilinear / bicubic / lanczos，
  默认 0 未映射到具名档），lanczos 是它的最高档；
- libswscale 侧还有 `spline` / `sinc` / `gauss` / `area` / `bicublin` 等更多档，但 **GPU 侧没有对应档可选**
  → 取两者交集里的最高档 = lanczos；
- 用户明确要求「默认按 scale_cuda 的最高档算法 lanczos，确保画质」。

**"最高档"的边界**：这只是**档位维度**上的最高，不等于普适最优——lanczos 锐度导向、边缘有轻微振铃；
`scale_cuda` 还有个子旋钮 `param`（默认 999999 = 内置默认，对 lanczos 是 taps 数）**未碰也未实测**；
`scale_npp` 另有一套档位（含 `cubic2p_*` / `super`），要 `--enable-nonfree`，也没实测。

**一致性验证（两脚本滤镜链矩阵，同一组参数喂两边 + 各自专有参数）**：一致 8 / 失败 0，覆盖

| 用例 | 滤镜链 |
|---|---|
| cover 横 1920x1080→1440x1080（源更宽） | `scale=-2:1080:flags=lanczos,crop=1440:1080:(iw-1440)/2:0` |
| cover 竖 1080x1920→1280x720（源更高） | `scale=1280:-2:flags=lanczos,crop=1280:720:0:(ih-720)/2` |
| cover 比例相同 | `scale=1280:720:flags=lanczos` |
| crop-cover（有/无 `--crop-ratio`） | `crop=1920:1080:0:0,scale=640:360:flags=lanczos` |
| crop（无 scale） | `crop=640:360:640:360` |
| cover 同尺寸 + `--no-skip-same-size` | `scale=1920:1080:flags=lanczos` |

**写这类"比对命令行"的 harness 有个坑**：`shlex.join` 是**按需加引号**的——含括号/`-2` 的链会被
`'…'` 包住，`scale=1280:720:flags=lanczos,…` 这种不含 shell 特殊字符的**不加引号**。按"必须有单引号"
去 sed 提取，会只匹配上一部分用例、剩下的静默返回空串，而空串相等会被误判成"一致"。
→ 解析要按 token 取（引号可有可无），且**空值必须判失败**。

## 2026-09-20 追加：`--scale-algo`（缩放算法/后端可选）

在"两边都钉 lanczos"之上加了显式开关，写法 `<backend>-<algo>` 或裸 `<algo>`：

| 写法 | 含义 |
|---|---|
| 不传 / 裸 `lanczos` | **默认 = 既有行为**（GPU 可用走 `cuda-lanczos`，否则 `libswscale-lanczos`） |
| `libswscale-<algo>` | 强制 CPU 链，**不插** CUDA 缩放策略 |
| `cuda-<algo>` | 强制 CUDA 缩放链（仅 cover 模式） |

- 两张表：`libswscale` = `fast_bilinear bilinear bicubic neighbor area bicublin gauss sinc
  lanczos spline`（不收 `experimental`，它要配 `+unstable`）；`cuda` = `nearest bilinear
  bicubic lanczos`（`scale_cuda` 的全部具名档）。`nearest` ↔ `neighbor` 互为别名。
- **裸名字的解析在两个脚本里是有意不同的**：hwaccel 有两个后端 → 要求两表都认，`spline`
  这类必须写 `libswscale-spline`（否则报歧义）；v2 只有一个后端 → **任何 libswscale 算法
  都能省前缀**（这是用户明确要的"v2 可以省去 libswscale- 前缀"）。所以同一条
  `--scale-algo spline` 在 v2 能跑、在 hwaccel 报错——**要两边都能跑就统一带前缀**。
- 降级/冲突（复刻 `--hwaccel cuda` 的两档行为）：一件 CUDA 组件都没有 → **报错退出 2**；
  有 CUDA 但缺 `scale_cuda` → 退回 `libswscale-<同档>` + 告警；`cuda-*` 与 `--hwaccel none` /
  `--fallback-policy cpu-only` 同时给 → **报错退出 2**；`crop` 模式给了 → 提示不生效、继续跑。
- v2 **拒绝** `cuda-*`（纯 CPU 路径）并指向 hwaccel —— 与"v2 没有 `--hwaccel`"同类的既有权不对称。

**验证（本机实测）**：`parse_scale_algo` 矩阵 15 组（合法/非法/别名/未知前缀/空 algo/大小写）；
不传该参数时两脚本 8 个用例 × 2 脚本 = **16/16 行滤镜链与改动前逐字相同**（`git` 前基线
对 diff）；`--scale-algo` 跨脚本矩阵 8/9 一致（第 9 个 `CUDA-Lanczos` 两边都按预期失败）；
单元 22 项（策略链插不插、滤镜串落地、两脚本默认值/取值表/别名表一致、未知名字报错首行一致）。

**为什么"不传 = 保持现状"是硬要求**：默认值一旦改成 `libswscale-lanczos`，就会把刚验证到的
**44.5% 提速对所有现有用户静默关掉**。所以默认必须是"后端自动"，而不是某个具体后端。


## 实测数据（2026-09-20，T4 + 自建 FFmpeg 7.1，`-f null -` 去 IO）

素材：真实 4K（`Earth.at.Night…S02E01_2x.mp4`，3840x2160），目标 1440x1080 覆盖链，min of 3：

| 变体 | 链 | 时间 |
|---|---|---|
| B1 现行 | `-hwaccel auto` + `scale=-2:1080,crop=…` | **23.11s / 23.35s**（两轮） |
| B2 去缩放 | `-hwaccel auto` + `crop=1440:1080:0:0` | 19.08s / 19.34s |
| B3 新链 | `-hwaccel cuda -hwaccel_output_format cuda` + `scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=nv12,crop=…` | **12.83s / 12.82s** |

- CPU 侧 `scale` 单独成本 **4.01~4.03s = 现行的 17.2~17.4%**（两轮高度一致）
- 新链净收益 **10.29~10.53s = 44.5~45.1%**
- 判据 B4：`scale_cuda=-2:1080` **被接受**（与 `scale` 共用 `ff_scale_eval_dimensions`）

**两处读数的坑（别误读）：**

1. **B2 不是干净的 floor**：B3（GPU 缩放+下载+裁剪，12.83s）比 B2（只有裁剪、不缩放，19.08s）
   **还快 6.2s** → 成本大头是**搬运 4K 帧的内存带宽**，在显存里尽早降采样就省掉了。
   所以该看的是 **B1 vs B3 = 快 44.5%**，而不是 17%。
2. **收益是内容相关的**：用 lavfi `testsrc2` 自造 4K 素材那轮，CPU scale 成本只有 **0.2%**、
   改造收益 **9.8%**（不过 10% 阈值）。合成内容解码极快、管线被别的环节吃掉，测不出差异。
   → 这个优化只在**真实高码率 4K** 上兑现。

### 第三轮（2026-09-20，`--scale-algo` 落地后、CPU 侧已换 lanczos；**首次跑通质量门**）

同一素材、同一目标链，min of 3。本轮新增 B1b（CPU bicubic，复现旧基准）并跑了判据 Q：

| 变体 | 链 | 时间 |
|---|---|---|
| B1 现行 | `-hwaccel auto` + `scale=-2:1080:flags=lanczos,crop=…` | **26.65s** |
| B1b 旧基准 | `-hwaccel auto` + `scale=-2:1080,crop=…`（libswscale 默认 bicubic） | 24.35s |
| B2 去缩放 | `-hwaccel auto` + `crop=1440:1080:0:0` | 19.09s |
| B3 新链 | `-hwaccel cuda -hwaccel_output_format cuda` + `scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=nv12,crop=…` | **12.82s** |

- CPU 侧 scale（lanczos）单独成本 **7.56s = 现行的 28.4%**（比 bicubic 那轮的 17.2% 高，
  因为 lanczos 抽头更多）；新链净收益 **13.83s = 现行的 51.9%**
- 换 lanczos 让 CPU 基准变慢 **2.30s = 旧基准的 9.4%** → 之前记的 44.5% 是**偏保守**的
- **判据 Q（质量门）**：`PSNR(GPU lanczos vs CPU lanczos) = 46.60 dB`，远超 `≥ 40 dB` 门槛
  → **GPU lanczos 基本复现了 CPU lanczos**。另两条对照：CPU lanczos vs CPU bicubic =
  56.05 dB（该素材上 lanczos 与 bicubic 差异本就很小）、GPU vs CPU bicubic = 46.53 dB

**⚠ 这一轮 T4 日志被探针自身的 bug 截断了**（值得单独记）：判据 Q 的判词用了**跨行三元
表达式**，而 POSIX awk **不允许在 `:` 前换行**（Newline 只允许跟在 `, { && || do else` 后），
T4 的 **mawk** 直接报 `missing ) near end of line` / `syntax error at or near :`；脚本是
`set -euo pipefail`，于是这次 awk 解析失败把**后面的 VMAF 与汇总段一起带走**——日志只剩
三行 PSNR 就断了。本机 Git Bash 是 **gawk 5.4.1**，容忍该写法，所以 SELFTEST 当时没抓到。
→ 已修（改成 if/else 逐行赋值），并给 SELFTEST 加了 **`awk --posix` 预解析守卫**：三段
awk 程序（判词 + 汇总 + 上传链判词）提成变量，SELFTEST 用假数据真跑一遍，语法/空输出都判失败。
**教训：gawk 兼容 ≠ mawk 兼容；只在本机 gawk 下跑过的 awk 片段等于没验证过。**

### 第四轮（2026-09-20，awk bug 修好后复跑；**VMAF 首次产出，质量门完整通过**）

同一素材、同一目标链，min of 3：

| 变体 | 时间 | （第三轮对照） |
|---|---|---|
| B1 现行（CPU lanczos） | **27.19s** | 26.65s |
| B1b 旧基准（CPU bicubic） | 26.86s | 24.35s |
| B2 去缩放 | 18.83s | 19.09s |
| B3 新链（硬解零拷贝） | **12.82s** | 12.82s |

- CPU 侧 scale 单独成本 **8.37s = 现行的 30.8%**；新链净收益 **14.38s = 现行的 52.9%**
- **B3 极稳**（12.82 / 12.82 / 12.82~12.84 三轮一致），B1 在 26.6~27.2s 之间波动
- ⚠ **「换 lanczos 让 CPU 慢多少」这个差值不稳**：第三轮 2.30s（9.4%），本轮只有 0.33s（1.2%）。
  别引用这个数字下结论——它落在测量噪声里；**稳定的结论只有 B1 vs B3（51.9%~52.9%）**
- **判据 Q**：`PSNR(GPU lanczos vs CPU lanczos) = 46.60 dB`（≥40 门槛，与第三轮一致）
- **判据 Q + `PROBE_VMAF=1`（首次产出）**：
  `VMAF 新链(GPU) vs CPU lanczos = 97.21`、`VMAF CPU lanczos vs bicubic = 97.88`
  → **质量门完整通过**（PSNR ≥ 40 且 VMAF > 95），GPU lanczos 与 CPU lanczos 的差异在感知上可忽略

### 第五轮（2026-09-20，判据 C/D/E 首次实测：**软解 + `hwupload_cuda` 链出结果**）

把更新后的 `probe/probe_scale_cuda_crop.sh` 拷到 T4 后连跑两轮，结果一致。

**判据 C（device 与 crop 尺寸协商，源 1920x1080，`crop=iw/2:ih/2` 自我报告）**

| 变体 | 结果 | 判读 |
|---|---|---|
| `C1_hwupload_cuda` | **640,360** | crop 看到 1280×720，**尺寸协商正确**，没被静默丢弃 |
| `C2_hwupload_no_device` | 失败：`[hwupload] A hardware device reference is required to upload frames to.` | 通用 `hwupload` 不配 device 确实不行 → `hwupload_cuda` 的"自带 device"是真优势 |
| `C3_hwupload_explicit_device` | **640,360** | 通用 `hwupload` + `-init_hw_device cuda=cu:0 -filter_hw_device cu` 这条路也可用（退路） |

> C2 那格的报错行值得一提：第一版探针用 `tail -n1` 取错误，打出来的是
> `Nothing was written into output file, because at least one of its streams received
> no packets.` —— 只说"没写出东西"、不说为什么。改成 `errline()`（优先捞提到
> 滤镜 / device / cuda 的那行）之后才看到真正的根因。诊断输出要捞**根因行**。

**判据 D（软解路径吞吐对照，4K→1440x1080，min of 3）**

| 变体 | 第一轮 | 第二轮 |
|---|---|---|
| D1 软解 + CPU scale（lanczos） | 45.91s | 46.02s |
| D2 软解 + `hwupload_cuda` + `scale_cuda` | **44.57s** | **44.56s** |
| 差 | +1.34s（**2.9%**） | +1.46s（**3.2%**） |

→ **上传链确实更快，判读过（保留）**。但两轮都只有约 3%，别当成一个大数字引用。

**判据 E（画质）**：`PSNR(软解+上传链 vs CPU lanczos) = 46.603743 dB`
—— **与硬解零拷贝链的数字逐位相同**（两者用的是同一个 `scale_cuda` + lanczos，
所以本来就该一致；这反过来说明上传路径没引入任何额外差异）。

**⚠ 别误读判据 D（这是本轮最容易被带偏的地方）**

- 上传链"快 3%"是**在软解这个大前提下**的对比。看**绝对值**：
  软解链路整体 44~46s，而硬解零拷贝（B3）只要 **12.8s** —— 差 3.5 倍。
- 所以这条链的定位**仍然是**「NVDEC 用不了 / 解不了该编码时的出路」，
  **不是**用来替代硬解的吞吐优化。`--scale-algo auto` 只在显式 `--decode cpu`
  下才自动走它（且要先过功能探针），这个保守选择是对的。
- memory 里那条 `cuvid + hwupload_cuda` 比软解还慢的前科**没有被推翻**——那说的是
  「硬解后再上传」这种多一次拷贝的组合；这里比的是「软解后上传缩放」vs「软解后 CPU 缩放」。

### 第六轮（换 10bit 素材 new4_raw，1920x1080 `yuv420p10le`）：探针的两个 bug + 一个新结论

**① 探针把下载格式硬编码成 `nv12` → 10bit 源全线挂掉（已修）**

`hwdownload,format=nv12` 对 10bit 源报
`Invalid output format nv12 for hwframe download`，判据 B3 / B4 / Q(gpu) 全挂。
脚本侧其实是对的（`_src_download_fmt(src_bits)` 会给 `p010le`），**错的是探针**。
已加 `dl_fmt_of <pix_fmt>` 按源位深推导（与脚本保持一致），B3/B4/D2/Q 改用 `$DL_FMT`；
判据 A/C 用的是自造的 8bit A 素材，保持 `nv12`。

**② B4 因此误报过一次**（又是"结果性措辞顶替根因"）：它报「✗ 不接受 -2」，
而真实原因是下载格式不匹配，**跟 `-2` 无关**。现在先 grep `invalid output format`，
命中就明确说"不是 -2 的锅"。

**③ 判据 D 的结论与素材强相关 —— 别跨素材比**

| 素材 | D1 软解+CPU缩放 | D2 软解+上传 | 结论 |
|---|---|---|---|
| 4K / 8bit（三轮） | 45.9~46.0s | 44.3~44.6s | **+2.9~3.5%**（上传链更快） |
| 1080p / 10bit（new4_raw） | 4.240s | 4.909s | **-15.8%**（上传链更慢） |

→ 上传开销按**帧面积**付，分辨率越低越不划算。所以"上传链更快"**只在 4K 成立**。
⚠ 这直接影响 `--scale-algo auto` 在软解时是否回退 hwupload —— 可能该加一个
**分辨率门槛**；判据 D 需要补几档分辨率才能定。

**④ 10bit `p010le` 路径：修好探针后**跑通了**（首轮成功）**

B3（硬解零拷贝）2.058s、B4 `-2` 接受、DL_FMT 正确取到 `p010le`。
→ 脚本侧 `scale_cuda → hwdownload,format=p010le → CPU crop` **可用**（此前一直是空白）。

**⑤ 判据 Q 在 10bit 源上全 N/A（已修）**：`q_enc` 存的 ffv1 是 `yuv420p10le`，
`psnr` 滤镜协商失败（libvmaf 也只认 8bit）。已在所有路的滤镜末尾统一加
`,format=yuv420p` 降到 8bit 再存盘 —— 两侧同样降，比较仍公平；
8bit 源本来就是 yuv420p，这步是空操作，不影响 4K/8bit 那几轮的数字。

**⑥ 恒等缩放陷阱（本轮最容易被误读的点）**

覆盖链目标高固定 1080，而 new4_raw **源高就是 1080** → 缩放步骤是
**恒等操作**（1920x1080 → 1920x1080，只裁剪、不缩放）。所以：
- 判据 B/D **不含缩放成本**；B3 只比 B1 快 10.8%（4K 时是 52%），因为没缩放可省。
- 判据 Q/E **测不出缩放器差异**（VMAF 99.98 就是这么来的，不是画质结论）。
- 判据 D 那个 **-28.6% 是纯上传开销**（约 1.15s），没有任何缩放收益可以对冲。

**这也把"分辨率依赖"解释清楚了**：上传开销按**帧面积**付，缩放收益也按帧面积走。
4K 时缩放省 7~8s 能盖住上传开销（净 +3%）；1080p 恒等时收益为 0，只剩 -28.6%。
→ 探针现在会检测"源高 == 1080"并给出恒等缩放警示；判据 B/D 标题也改成打印实际分辨率。

**关于 new4_raw 素材本身（用户怀疑"色调异常/损坏"）**：实测
`ffmpeg -v error -i ... -f null -` **零输出 → 码流没坏**。
元数据 `bt2020nc + arib-std-b67(HLG) + bt2020 + color_range=tv` → 是 **HLG HDR** 片源。
"色调异常"是 **HDR 在 SDR 显示器上没做 tone mapping** 的表现，不是损坏、也不是
值域标签错（`color_range=tv` 自洽）。要修得走 `tonemap` / `zscale`，脚本目前不做。

### 第七轮（换 4K/8bit 素材 new4_raw_4k，3840×2160 `yuv420p`）：**收益只有 6.4%**

| 变体 | 时间 |
|---|---|
| B1 现行（硬解 + CPU lanczos） | 4.227s |
| B1b 旧基准（CPU bicubic） | 3.955s |
| B2 去缩放（隔离 CPU scale） | 3.950s |
| B3 GPU 缩放（硬解零拷贝） | 3.957s |

- CPU scale 净成本 **0.277s = 现行的 6.6%**；改造净收益 **0.270s = 6.4%**
- 判据 D：软解+CPU缩放 7.777s vs 软解+上传 7.680s → **+1.2%**
- 质量：PSNR 51.65 dB、VMAF 99.98（比 Earth 素材更高，因为该素材更"干净"）

**⚠ 这轮推翻了"4K 就稳赚 50%"的直觉 —— 收益是素材相关的**

| 素材 | CPU scale 占比 | 改造净收益 | 判据 D（软解上传） |
|---|---|---|---|
| Earth 4K/8bit（三轮） | 27~31% | **51.9~52.9%** | +2.9~3.5% |
| new4_raw_4k 4K/8bit（本轮） | 6.6% | **6.4%** | +1.2% |
| new4_raw 1080p/10bit（恒等缩放） | 0%（没真缩） | 10.8% | **-28.6%**（纯上传开销） |

三点解释：
1. **收益取决于 CPU 缩放占总耗时的比例**，不是分辨率本身。本轮素材解码+编码只要 3.95s，
   缩放只占 6.6% → 就算把缩放完全归零也只省 6.6%。
2. 本轮 **B3(3.957) ≈ B2(3.950)**，而 Earth 那几轮是 **B3(12.8) < B2(19.1)**
   ——"在显存里尽早降采样省掉内存带宽"这个效应在本轮看不见。
3. **判据 D 才是"软解 + 上传链"的净收益**：4K 上是 **+1.2~3.5%**（很小但为正），
   1080p 上是 -28.6%（不过那次是恒等缩放的混淆，见下）。

⇒ 对「`--scale-algo auto` 在软解时是否回退 hwupload」这件事：现在有 3 个 4K 数据点
（+3.0% / +3.5% / +1.2%），**收益都在噪声边缘**。1080p 那次 -28.6% 是恒等缩放造成的
混淆（缩放收益为 0、只剩上传开销），**不能直接当作 1080p 的结论**。
要定分辨率门槛，还需要用 `--target` 让 1080p 走**真缩放**再测一次。

**探针同时修了两处（本轮暴露）**
1. 判读文案硬写「原 44.5%…只会比 44.5% 更大」→ 本轮实际 6.4%，**自相矛盾**。
   改成数据驱动：打印本轮百分比 + 明确「跨素材不可直接比较」。
2. 不打印 B 素材的时长/帧数 → 无法解释"同样 4K，一轮 26s、一轮 4s"。已补上。

**🚦 本轮拍板的决定（2026-09-20，用户定）—— ⚠ 2026-09-21 已被第八轮取代，见下**

- **先保持现状**：`--scale-algo auto` 在软解时**仍然尝试**显存内缩放（先过功能探针），
  **现在不加分辨率门槛**。理由：4K 上收益虽小但为正，且功能探针已挡掉"不可用"的情况。
- **门槛推迟到数据够了再定**。待办的两类补测：
  1. **1080p 真缩放**（用 `--target 720x480` 之类，避开恒等缩放混淆）——目前唯一能说清
     "低分辨率到底亏不亏"的办法，这是当前最高优先级的一测。
  2. **多组真实 4K 素材**（含 10bit）——看收益的波动范围，判断 6.4% 是特例还是常见值。
- 定门槛时要一并给出：门槛值、该值下的实测收益、以及"低于门槛就只用 CPU 缩放"是否需
  要提示。

**补测用的命令（探针已支持 --target；工装已转正入库，`git pull` 即可，不用手动拷）**
```bash
# 1080p 真缩放：判据 D 才有意义（默认 1440x1080 对 1080p 源是恒等缩放，测不出东西）
PROBE_VMAF=1 bash probe/probe_scale_cuda_crop.sh --target 720x480 \
    /workspace/input_videos/new4_raw.mp4          # 1080p / 10bit HLG
# 4K/10bit 素材：直接跑（B/D 都含真缩放）
PROBE_VMAF=1 bash probe/probe_scale_cuda_crop.sh <4K素材>

### 第八轮（2026-09-21，两批共 12 组素材）：判据 D 的规律 = **「位深」+「是否真在缩放」**，不是分辨率

前七轮一直在"分辨率"上找门槛，其实那是**两个正交条件**。12 组素材拉平后：

| 条件 | 判据 D（软解+上传 vs 软解+CPU 缩放）| 素材 |
|---|---|---|
| **源 ≥10bit 且真在缩放** | **+14% ~ +25%** | 4K 10bit +16.5% / +19.3%；720p 10bit +25.0% |
| 8bit + 真在缩放 | **+0.4% ~ −14%** | 4K 8bit +0.4% / +0.3%；720p 8bit −9.0% / −14.3%；SD −0.3% ~ −1.6% |
| **恒等缩放（无论位深）** | **−1.6% ~ −34.7%（必亏）** | 1080p 8bit −1.6% / −34.7%；**1080p 10bit −22.0%** |

机理：上载 / 回下载开销基本固定，而 **p010le 的 CPU 缩放比 8bit 贵得多**；恒等缩放时
CPU 侧本来就没有重采样成本可省 → 只有"高位深 **且** 真的省下缩放"两项同时成立才划算。

⚠ **`new4_raw`（10bit）恒等时是 −22.0%**，这一格是判决性的：位深**不是**充分条件。
前几轮把它读成"10bit 就该用上传链"是错的。

复现性（同素材两批）：4K 10bit 真缩放 +16.5% → +19.3%；4K 8bit 真缩放 +0.4% → +0.3%。

**⇒ 2026-09-21 门槛落地（用户拍板）**：写进 `_hwupload_skip_reason()`，
**只作用于 `--scale-algo auto`**（显式 `cuda-*` 是用户点名要的，照旧直接执行）：
恒等 → 不走；源 < 10bit → 不走；其余（≥10bit 且真缩放）→ 走（先过功能探针）。
跳过时打印**具体理由** + "要强制用请显式写 `--scale-algo cuda-lanczos`"。
判据 `verify/verify_hwupload_worth.py`；`verify/verify_cuda_scale.py` 的第 ③ 组已补上
"10bit 非恒等"前置条件并新增"8bit 不走"的反面格 —— **别把这道门槛误读成正交性回归**。

**VMAF 的落差规律（同批）**：4K 缩小 99.95~99.98；720p 8bit 放大 99.38~99.70；
**SD 放大（720x480/576）只有 95.0~96.7** —— 而对照的 `CPU lanczos vs bicubic` 也是
98.3~98.8，**两者同量级** ⇒ 放大场景下 `scale_cuda` 的 lanczos 与 libswscale 的
lanczos **不是同一个重采样器**。所以 PSNR ≥40 那道门槛太松（SD 素材 PSNR 有 48~51 dB
看着很好、VMAF 只有 95）→ 判据 Q 已加 VMAF 分档判词。

**探针在这一批里又修了三处**（都是"误报会被读成结论"）：
① `best()` 失败时不清旧 `.min` → 汇总读到上一轮的值，打出 `CPU scale 净成本 -54.7%`；
② 恒等缩放时 PSNR = `inf`，老解析器把它变成 `N/A`（看着像链路坏了）；
③ **B4 对"本机解不了该编码"的源误报"不接受 `-2`"**（真根因是 `cuda` 初始化失败）。

### ⚠ 探针必须带 `-noautorotate`（2026-09-21，第 4 处）

`dim()` 用 ffprobe 读的是**存储尺寸**，而 ffmpeg 默认会**应用**旋转（滤镜看到显示尺寸）
—— 两边不一致就会算出错的 crop 尺寸。实测：竖版素材（存储 1920x1080 + 90° 旋转，
`vidls` 报的竖版 1080x1920）→ 滤镜看到 1080x1920 → `crop=1440:1080` 直接报
`Invalid too big or non positive size`。

这是脚本 `-noautorotate` 的**镜像缺失**（`vidcrop_hwaccel` 的 `build_preserve_args`
里就带着它 + `-display_rotation`，整条链在**存储坐标系**里工作）→ 探针所有读文件的
ffmpeg 调用都补上了，并在检测到旋转标签时打印说明（免得看日志的人拿 1920x1080
对竖版素材困惑）。SELFTEST 用 `-display_rotation 90` 跑**正反两条**（不带必须失败、
带必须成功）—— 只测成功那条证明不了什么。

## 两条硬约束（都有实测编号，改动时别踩）

### 1. 必须**显式**写 `hwdownload,format=…`，不能靠 FFmpeg 自动插入

同一轮判据 A（源 1920x1080，`scale_cuda=1280:720`，探针用 `crop=iw/2:ih/2`，
输出尺寸直接反映 crop 看到的是什么）：

| 链 | 输出 | 含义 |
|---|---|---|
| `scale_cuda=1280:720,hwdownload,format=nv12,crop=iw/2:ih/2` | **640x360** | crop 看到 1280x720 ✓ |
| `scale_cuda=1280:720,crop=iw/2:ih/2`（靠自动插入） | **1280x720** | **crop 被静默丢弃** ✗ |

A2 那格是本次最值钱的发现：尺寸 1280x720 本身**合法**、ffmpeg **不报任何错**，
但裁剪根本没发生、画面与预期不同。这与 `_prepend_hwdownload` 注释里记的老前科
（768x576 源 `crop=768:432` 后仍输出 768x576）是同一类。

### 2. `crop_cuda` 在 FFmpeg 上游**并不存在**（更正旧说法）

实测 `ffmpeg -filters` 里没有、`ffmpeg -h filter=crop_cuda` → `Unknown filter 'crop_cuda'`
（本机 master gpl-shared 与 T4 自建 7.1 都如此）。旧记忆/README 写的"FFmpeg 6.1 未编译该滤镜"
不准确——**不是编译选项问题，是上游没有这个滤镜**。所以：

- `vidcrop_hwaccel.py` 的**策略 1（硬解 + crop_cuda + NVENC）在真实环境永远跳过**，
  crop 模式今天走的其实是策略 2。读代码会以为"策略 1 是最高优先级的全 GPU 路径"，实际没跑过。
- **GPU 永远只能接管 cover 的 `scale`，`crop` 只能留在 CPU** → 链路必然
  `scale_cuda → hwdownload → crop`（一次下载，与现状次数相同）。
- 因此 `crop-cover` **不纳入** CUDA 缩放：它必须先裁剪，GPU 缩放要额外 `hwupload` 一次（未实测）。

## 顺带修掉的一个老 bug（此前不可达，被本次改动激活）

`_src_download_fmt()` 原来返回 `p010` / `p012`，但 ffmpeg 的 pix_fmt 表里**只有 `p010le` / `p012le`**
（实测 `ffmpeg -pix_fmts` 里查不到 `p010`/`p012`）。此前它只被 `_prepend_hwdownload` 调用，
而那条路只有 `hof='cuda'` 才走——**只有死掉的策略 1 会给 `hof='cuda'`**，所以一直没暴露。
新增的 CUDA 缩放策略同样给 `hof='cuda'`，10bit 源一进来就会拿 `hwdownload,format=p010` 去协商 → 必然失败。
已改成 `p010le` / `p012le`。

**教训**：新功能接上一条"以前没人走"的代码路径时，要先假设那条路径是**没被测过**的。

## 还没做 / 还没过

- **质量门未过**：GPU `lanczos` 与 libswscale 的缩放在数值上不逐像素相同，44.5% 目前只是
  "尺寸对、计时快"。探针 `probe/probe_scale_cuda_crop.sh` 的判据 Q 已按"两条路同档"重写成：
  `ref`=CPU `flags=lanczos`（现在就是发货的 CPU 链）/ `gpu`=新链 / `bic`=CPU bicubic（旧基准，仅参照），
  各出一份 ffv1 无损后比 PSNR；**主判据是 `PSNR(gpu vs ref) ≥ 40dB`**（GPU lanczos 复现 CPU lanczos），
  按 token 解析 psnr 的 `average:`。`PROBE_VMAF=1` 可加 libvmaf。**尚未在 T4 上跑**。
  判据 B 也加了 `B1b`（CPU bicubic）——用来量"换 lanczos 让 CPU 慢了多少"，
  因为那 44.5% 的收益是对着**旧 bicubic 基准**测的，换成 lanczos 后基准更慢、相对收益只会更大。
- 10bit + `h264_nvenc`（8bit-only 编码器）走新链时，`-pix_fmt yuv420p` 仍由 `build_ffmpeg_cmd`
  追加——因为链里已显式 `hwdownload` 成软件帧，这一步是合法的降位深（与旧行为一致），但**未实测**。
- 新链的下载格式烘在滤镜串里，`process_file` 那套 `hw_download_fmt` 重试够不到它；
  p010 下载失败就是整条策略失败、降级到 CPU 缩放（结果仍正确）。

## Why

用户会**按"值不值得动手"来要数字**：他先要"高度概括的可行性"，再直接让写探针脚本上机实测，
不接受"读代码推出应该能省 CPU"。所以这类改动必须先把**判据与阈值事先钉死**
（本次：CPU scale 占比与改造收益**都 ≥10% 才改**），数据一到就是机械结论。
本次也正是靠实测才发现 A2 那个**静默**失败——只看代码或只看"跑通了"都会漏掉。

## How to apply

- 谈论 cover 的 GPU 化时直接引用上面的 B 表，别重新推导；也**别说"策略 1 会接管"**——它不会。
  **引用收益时用第三轮的 51.9%**（B1 已含 lanczos），别再用对着旧 bicubic 基准的 44.5%。
- 改这条链时守住两条：**显式 `hwdownload,format=`**（否则画面静默错）、**显式 `interp_algo`**
  （`scale_cuda` 的默认值是 0，未映射到具名档，而 CPU 侧 `scale` 默认是 bicubic）。
- **质量门已完整通过**：PSNR 46.60 dB（≥40）+ VMAF 97.21（>95），GPU lanczos ≈ CPU lanczos。
- **软解 + `hwupload_cuda` 链已实测**（判据 C/D/E）：尺寸协商正确、**比软解+CPU缩放快 2.9%~3.2%**、
  画质与零拷贝链逐位相同。但**绝对值是 44~46s vs 硬解零拷贝的 12.8s** → 定位仍是
  「NVDEC 用不了时的出路」，别当吞吐优化卖。（10bit `p010le` 下载路径**已跑通**，见上文 ④：
  B3 2.058s、B4 `-2` 接受、`DL_FMT=p010le` —— 此前"仍未测"的说法已过期，2026-09-24 更正。）
- **引用收益用 B1 vs B3 = 51.9%~52.9%**（B1 已含 lanczos）。别引用「换 lanczos 让 CPU 慢多少」
  ——四轮测出 9.4% / 1.2% / 13.0% / 11.8%，完全落在噪声里。
- ⚠ **拿 `psnr` 当"逐位相同"的判据时要限定条件**（2026-09-24 实测）：本机 ffmpeg
  `N-122480` 下，`ffmpeg -i A -i B -lavfi '[0:v][1:v]psnr'` 对**逐位相同**的两份文件
  （rawvideo 像素流 md5 相同、framemd5 全列相同）仍报 `average:26.0 dB`，且**交换输入顺序
  得到不同值**（26.0 vs 23.8）⇒ 多输入 psnr 会按两侧解码器的格式/范围插一次隐式转换。
  本探针用的两侧都是自己产的 **ffv1**（同格式同链），所以判据 Q/E 的数字仍然有效；
  但**跨解码器/跨 pix_fmt** 比较时不要用 psnr —— 改用 `framemd5`（比哈希列 + 单独报帧数），
  见 `probe/probe_lossless_qp0.sh` 的实测记录。
- **工装脚本已转正（2026-09-21）**：`test/`（回归门 + `baseline/`）、`verify/`（验证套件）、
  `probe/`（上机探针）三个目录**都在 git 里**，随 `git pull` 同步 → 以前"改了探针要手动拷到
  T4"的约定**作废**。`temp/` 仍是本机工作目录（素材、产物、日志、`fixture_1080p.mp4`），
  只有它不同步。
- **本机 awk 是 gawk、T4 是 mawk**：给 `test/` `verify/` `probe/` 下的 `*.sh` 写 awk 时避免
  跨行三元等非 POSIX 写法，
  并跑 `SELFTEST=1`（里面已含 `awk --posix` 预解析守卫）。`set -e` 的脚本里一个 awk
  解析失败会带走后面所有输出，破坏性比看上去大。
- 判据 A 的探针技巧可复用：想验证某个滤镜"看到的 `iw/ih` 是多少"，用 `crop=iw/2:ih/2`
  让输出尺寸自己报出来，比 PSNR 对照便宜且无歧义。
- `scale_cuda` 没有 `in_range`/`out_range` → CUDA 链里 `--color-range tv|pc` 的**值域转换做不了**
  （`build_range_convert_filter` 会返回 None 并告警，只改标签）。这是结构性限制，不是 bug。
- 相关：`project_t4_gpu_capabilities.md`（零拷贝链里不能传 `-pix_fmt`）、
  `project_preset_equivalence.md`（两脚本一致性约定——本次没动 CLI，故不受影响）。
