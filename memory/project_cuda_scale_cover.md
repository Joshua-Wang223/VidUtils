---
name: cover 模式的 CUDA 缩放（scale_cuda）——实测数据、两条硬约束、以及一个被激活的老 bug
description: 2026-09-20 给 vidcrop_hwaccel.py 的 cover 模式加了「CUDA 缩放 + CPU 裁剪」策略；真实 4K→1440x1080 实测快 51.9~52.9%（对着 lanczos 基准；对旧 bicubic 基准是 44.5%）；质量门已完整通过（PSNR 46.60dB + VMAF 97.21）；必须显式写 hwdownload 否则 crop 被静默丢弃；crop_cuda 上游不存在；顺带修掉 _src_download_fmt 的 p010/p012 非法 pix_fmt 名；探针的 awk 跨行三元在 T4 的 mawk 上炸过（gawk 兼容 ≠ mawk 兼容）；软解+hwupload 链的吞吐与画质（判据 D/E）仍是空白
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

**⚠ 这一轮仍没有判据 C/D/E**：T4 上的 `temp/` 是它自己的副本（`temp/` 被 `.gitignore` 覆盖，
不会随 git 同步），判据 C/D/E（软解 + `hwupload_cuda` 链的 device、尺寸协商、吞吐、画质）
是新增的，要**把更新后的 `temp/probe_scale_cuda_crop.sh` 拷过去再跑**。
→ **软解 + `hwupload_cuda` 那条链的吞吐至今仍是空白**；在判据 D 出结果前，`--scale-algo auto`
只在显式 `--decode cpu` 下才会走它（且要先过功能探针）。

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
  "尺寸对、计时快"。探针 `temp/probe_scale_cuda_crop.sh` 的判据 Q 已按"两条路同档"重写成：
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
- **质量门已过**：PSNR 46.60 dB（≥40）+ VMAF 97.21（>95），GPU lanczos ≈ CPU lanczos；
  但 **软解 + `hwupload_cuda` 那条链（判据 D/E）与 `10bit p010le` 路径仍是空白**；
  再动缩放算法/档位时要重跑判据 Q（+`PROBE_VMAF=1`）。
- **引用收益用 B1 vs B3 = 51.9%~52.9%**（B1 已含 lanczos）。别引用「换 lanczos 让 CPU 慢多少」
  ——三轮测出 9.4% 与 1.2%，落在噪声里。
- **T4 的 `temp/` 不会随 git 同步**（被 `.gitignore` 覆盖）：换了探针/工装要**手动拷过去**。
- **本机 awk 是 gawk、T4 是 mawk**：给 `temp/*.sh` 写 awk 时避免跨行三元等非 POSIX 写法，
  并跑 `SELFTEST=1`（里面已含 `awk --posix` 预解析守卫）。`set -e` 的脚本里一个 awk
  解析失败会带走后面所有输出，破坏性比看上去大。
- 判据 A 的探针技巧可复用：想验证某个滤镜"看到的 `iw/ih` 是多少"，用 `crop=iw/2:ih/2`
  让输出尺寸自己报出来，比 PSNR 对照便宜且无歧义。
- `scale_cuda` 没有 `in_range`/`out_range` → CUDA 链里 `--color-range tv|pc` 的**值域转换做不了**
  （`build_range_convert_filter` 会返回 None 并告警，只改标签）。这是结构性限制，不是 bug。
- 相关：`project_t4_gpu_capabilities.md`（零拷贝链里不能传 `-pix_fmt`）、
  `project_preset_equivalence.md`（两脚本一致性约定——本次没动 CLI，故不受影响）。
