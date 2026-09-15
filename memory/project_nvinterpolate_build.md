---
name: FFmpeg 7.1 已合并 nvinterpolate 与 libvmaf（单一版本）
description: 本机现在只有一个 ffmpeg(/usr/local/bin, 7.1),已含 nvinterpolate + libvmaf + 全部 CUDA/NPP 滤镜;移植补丁位置与滤镜使用约束
type: project
---

## 现状（2026-09-14 完成合并，此后只需一个 ffmpeg）

`/usr/local/bin/ffmpeg` = **7.1**，已包含下列全部能力，**不需要 source 任何环境文件、
不需要设置 LD_LIBRARY_PATH**：

```
scale_cuda  scale_npp  sharpen_npp  transpose_npp  overlay_cuda
nvinterpolate  libvmaf  libx264  libx265  h264/hevc/av1_nvenc
```

原先为了 nvinterpolate 单独构建的那份旧 FFmpeg（`~/ffmpeg-nvinterpolate-build`，1.7G）
**已删除**，空出来的做法是：
- 把 `libNvOFFRUC.so` + `libcudart.so.11.6.55`（含 `libcudart.so.11.0`/`libcudart.so`
  软链）安装到 `/usr/local/lib/` 并 `ldconfig` —— 滤镜是 `dlopen("libNvOFFRUC.so")`，
  这样就不必再靠 LD_LIBRARY_PATH。
- `~/.nvinterpolate_env` 已删除（它本来就只是为旧构建准备的 PATH/LD_LIBRARY_PATH 前缀）。

**系统那份 `/usr/bin/ffmpeg` 6.1.1（apt/dpkg 管理）保留未动。** 不要删：实测
`imagemagick-6.q16` 依赖它，且 apt 可能重装。它平时不会被调用（`/usr/local/bin`
在 PATH 里更靠前）。

## 移植补丁（把 nvinterpolate 从 2024-01 的 FFmpeg 搬到 7.1）

补丁与移植后的源码保存在 **`/workspace/nvinterpolate-7.1-port/`**（`nvinterpolate-7.1.patch`
只有 10 增 9 删）。两处改动：

1. **删掉 `#include "internal.h"`**。`libavfilter/internal.h` 在 FFmpeg 7.x 已被拆分
   （内容进了 `filters.h` / `avfilter_internal.h`），而该文件本来就 include 了 `filters.h`。
2. **`AVFilterLink` 的 `frame_rate` / `hw_frames_ctx` 在 7.x 挪进了扩展结构 `FilterLink`**，
   必须用 `ff_filter_link(link)->字段` 访问（共 6 + 2 处）：
   - `inlink->frame_rate` → `ff_filter_link(inlink)->frame_rate`
   - `outlink->frame_rate` → `ff_filter_link(outlink)->frame_rate`
   - `ctx->inputs[0]->hw_frames_ctx` → `ff_filter_link(ctx->inputs[0])->hw_frames_ctx`
   （`FilterLink { AVFilterLink pub; … AVRational frame_rate; AVBufferRef *hw_frames_ctx; }`
   定义在 7.1 的 `libavfilter/filters.h`；`ff_filter_link()` 只是个类型转换。）

**踩坑提醒（我犯过）**：把滤镜放到树外、`gcc -I…libavutil …` 单独试编会"通过"——
因为 `"internal.h"` 被解析成了 **`libavutil/internal.h`** 而不是 libavfilter 的，是**假阳性**。
判断能否编过必须用**构建系统本身**：`make libavfilter/vf_nvinterpolate.o`。

## 重建步骤（换机器/重来时）

1. 7.1 源码树里放入移植后的 `vf_nvinterpolate.c`
2. 注册：`libavfilter/Makefile` 追加
   `OBJS-$(CONFIG_NVINTERPOLATE_FILTER) += vf_nvinterpolate.o`；
   `libavfilter/allfilters.c` 里插 `extern const AVFilter ff_vf_nvinterpolate;`
   ——**必须插在 `#include "libavfilter/filter_list.c"` 之前**（configure 靠扫描
   allfilters.c 的 extern 声明来生成 `CONFIG_*`）
3. 重新 configure，在原选项基础上加 `--enable-libvmaf`（pkg-config 能找到 2.3.1，
   在 `/usr/local/lib`）与 NvOFFRUC 的 include/lib
4. `make libavfilter/vf_nvinterpolate.o` 先单编确认，再 `make -j4`
5. **先在源码树里测 `./ffmpeg` 再 install**；注意树内二进制默认会加载 `/usr/local/lib`
   里**旧的**同名库，测试时要 `LD_LIBRARY_PATH` 指向树内 `libav*` 目录

## 滤镜使用约束（实测，合并后依然成立）

- **`nvinterpolate` 必须是滤镜链最后一个。** 接它后面的滤镜都会挂：
  `,scale_cuda` → **段错误(RC=139)**；`,scale_npp` / `,hwdownload` →
  `Error reinitializing filters`。要缩放就放在**它之前**
  （`scale_cuda=960:540,nvinterpolate=fps=60` 可用）。
- **输入必须是 CUDA 帧**：`-hwaccel cuda -hwaccel_output_format cuda` 两个都要写；
  只写 `-hwaccel cuda` 或都不写会直接失败。
- **只支持目标帧率是源帧率的整数倍**：30→60 ✓、24→48 ✓；24→30 / 24→60 ✗
  （帧数/帧率看着对，但部分中间帧其实是"就近复制"源帧，实测时间位置偏差可达 16.7ms）。
- **片头会重复若干帧**（拿不到"前一帧"）：1.25x 约 2 帧 / 2x 约 3 帧 / 2.5x 约 4 帧，
  且源的第 1 帧被丢弃。要干净可用
  `nvinterpolate=fps=60,trim=start_frame=3,setpts=PTS-STARTPTS`（`start_time=0` 无效）。
- 用 `-cq N` 而不是 `-b:v`（NVENC 的 `-b:v` 是均值目标非上限，短片段实测超标 15%~50%）。
- **所有调用加 `-nostdin`**（见 project_ffmpeg_stdin_hang.md，会被 SIGTTIN 停住）。
- `libvmaf` 的 `VMAF score:` 是 **INFO 级**日志，用 `-loglevel error` 看不到。

## 三个安装脚本的分工（2026-09-14 起）

- **`/workspace/install_ffmpeg_gpu_scale_plus_nvinterpolate.sh` = 合并版**（推荐用这个）。
  一个脚本两种模式：
  · 默认（**模式 A 构建**）= 原来的 scale 行为，含 `--sdk-path` 时集成 nvinterpolate
  · `--patch-only --tree DIR [--sdk-path SDK]`（**模式 B 打补丁**）= 原来的 nvinterp 行为
  模式 B 下"装运行库到 /usr/local/lib"必须**显式** `--install-libs`（不会默认写系统目录）；
  模式 A 下默认就装，用 `--no-install-nvoffruc` 关。
  它的构建流程与 scale 脚本逐字一致（已用归一化 diff 核对，仅 8 行有意差异）。
- **`/workspace/install_ffmpeg_gpu_scale.sh`**：原构建脚本（保留，未删）。
  新增 CLI：`--sdk-path`（给了就集成 nvinterpolate）、`--cuda-version`、
  `--ffmpeg-version`、`-j`、`--no-vmaf`、`--nvi-src`、`--no-install-nvoffruc`；
  也保留 `CUDA_VERSION` 等环境变量。
- **`/workspace/install_ffmpeg_gpu_nvinterpolate.sh`**：原源码树补丁工具（保留，未删）。
  用法 `--tree <FFmpeg源码树> --sdk-path <SDK> [--nvi-src] [--install-libs]`，
  只做"取源码 → 打 7.x 移植补丁 → 注册进树 → 打印要追加的 configure 参数"。

合并时踩到的一个点：原 scale 脚本把 nvinterpolate 的函数定义放在"第 3 步之后"，
而模式 B 必须**跳过前 3 步**（不能装依赖/下 FFmpeg）——所以合并时把那些函数
挪到了模式分派之前（`integrate_nvinterpolate` 现在接收第二个参数 = 预备目录，
`NVI_REPO_DIR` 由调用方设置）。

同时修掉的 scale 脚本旧缺陷：nvcc 架构原先硬编码 sm_75（非 Turing 会编出跑不了的库）
→ 改按实际 compute_cap；判断滤镜是否启用原先 grep `config.h`/`ffbuild/config.log`
（那里根本没有滤镜开关）→ 改查 `config_components.h` 的 `CONFIG_*_FILTER`；
`ffmpeg -filters | grep -q` 的 SIGPIPE 假阴性 → 先取回输出再匹配；
`CUDA_VERSION` 原先默认 12.4（本机已有 12.8 会白下几 GB）→ 自动探测。

`/workspace/nvinterpolate-7.1-port/` 里那份独立补丁现在**只是可读的 diff 记录**，
补丁已内建进三个脚本（sed 规则），运行时不再依赖此目录。
