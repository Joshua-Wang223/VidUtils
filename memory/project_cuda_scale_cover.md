---
name: cover 模式的 CUDA 缩放（scale_cuda）——实测数据、两条硬约束、以及一个被激活的老 bug
description: 2026-09-20 给 vidcrop_hwaccel.py 的 cover 模式加了「CUDA 缩放 + CPU 裁剪」策略；实测 4K→1440x1080 快 44.5%（CPU 侧 scale 占 17.2%）；必须显式写 hwdownload 否则 crop 被静默丢弃；crop_cuda 上游不存在；顺带修掉 _src_download_fmt 的 p010/p012 非法 pix_fmt 名
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
- 改这条链时守住两条：**显式 `hwdownload,format=`**（否则画面静默错）、**显式 `interp_algo`**
  （`scale_cuda` 的默认值是 0，未映射到具名档，而 CPU 侧 `scale` 默认是 bicubic）。
- 判据 A 的探针技巧可复用：想验证某个滤镜"看到的 `iw/ih` 是多少"，用 `crop=iw/2:ih/2`
  让输出尺寸自己报出来，比 PSNR 对照便宜且无歧义。
- `scale_cuda` 没有 `in_range`/`out_range` → CUDA 链里 `--color-range tv|pc` 的**值域转换做不了**
  （`build_range_convert_filter` 会返回 None 并告警，只改标签）。这是结构性限制，不是 bug。
- 相关：`project_t4_gpu_capabilities.md`（零拷贝链里不能传 `-pix_fmt`）、
  `project_preset_equivalence.md`（两脚本一致性约定——本次没动 CLI，故不受影响）。
