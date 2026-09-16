---
name: T4 编解码能力/性能基线，以及本机可能有并发流水线抢占
description: Tesla T4 的 NVENC/NVDEC 能力边界与性能基线（含本机 ffmpeg 无任何 AV1 软编）；做性能测试前必须先在 /workspace/Video_Enhancement 查是否有流水线在跑（会占 CPU 40%+GPU 70%）
type: project
---

## 做性能测试前必须先查并发负载（2026-09-14 踩过）

本机（8 核 Xeon 8255C @2.5GHz + Tesla T4）经常有**用户自己的视频增强流水线在跑**：

```
python src/main_video_optimized.py ... --mode interpolate_then_upscale --upscale-factor 2
  --use-tensorrt-esrgan --use-tensorrt-ifrnet --codec-*-nvenc hevc_nvenc
```

**Why:** 2026-09-14 我跑 GPU 编码基准时，该流水线正在运行：CPU busy 40.7%（≈3.26/8 核）、
GPU 39–69%、`utilization.encoder` 20–29%、显存 3.4GB 被 TensorRT 占着，还有 ffmpeg 子进程
在做 NVDEC 解码 → `rgb24` 管道。**结果整份吞吐基准都不可信**（只删除了自己命名的产物，
没动它的文件）。
**How to apply:** 上机做任何性能/吞吐测量前，先跑
`pgrep -af 'main_video_optimized|ffmpeg'` + `nvidia-smi --query-gpu=utilization.gpu,utilization.encoder,utilization.decoder --format=csv`
确认空闲。**不要 kill 它**。注意 TensorRT 推理只体现在 `utilization.gpu`，不体现在
`utilization.encoder/decoder`，所以只看 ENC/DEC 会漏判。
另一个坑：`nvidia-smi` 的 compute-apps PID 可能是宿主 PID，与容器内 PID 对不上。

## T4 (Turing, CC 7.5, 驱动 580) 能力边界 —— 已实测

**编码**
- `h264_nvenc` ✓、`hevc_nvenc` ✓
- `av1_nvenc` **在编码器列表里存在但运行时打不开**（rc=187，`Could not open encoder`，
  error code -22）→ T4 无 AV1 编码器。列表里有 ≠ 能用，别被 `-encoders` 输出骗了。
- **本机 ffmpeg 也没有任何 AV1 软编**：`-encoders` 里没有 `libsvtav1` / `libaom-av1` / `librav1e`。
  两个事实叠加的直接后果：**这台机器上 AV1 根本编不出来**——`--codec av1_nvenc` 会在
  `_check_nvenc_available()` 的 1 帧试编里被判不可用（该函数是真试编，不会只看列表，所以判得对），
  随后按 `_get_software_fallback()` 降级到 `libsvtav1`，而 `libsvtav1` 不存在 → 硬失败。
  **Why:** 2026-09-16 核查 AV1/VP9 计划执行情况时实测确认；计划文档里"本机硬件已确认可用"
  的说法对 `av1_nvenc` 是错的（只看了 `-encoders` 列表）。
  **How to apply:** 本机验证 AV1 只能到 `--dry-run`（命令构造正确即可），真机编码结论必须标为
  "环境不支持"，别据此认为代码有问题。想要真机 AV1 得换含 `libsvtav1` 的 ffmpeg —— 那属于
  **环境变更，须另开立项**，不要塞进脚本改动里顺手做。VP9（`libvpx-vp9`）不受影响，可真机跑。
- 像素格式：h264_nvenc 支持 `yuv420p`/`yuv444p`，**不支持 `p010le`**（H.264 无 10bit）；
  hevc_nvenc 支持 `yuv420p`/`yuv444p`/`p010le`（10bit）。

**解码**：`h264_cuvid`/`hevc_cuvid`/`vp9_cuvid`/`mpeg2_cuvid`/`mjpeg_cuvid` 等一整套 cuvid。

**NVENC 是单引擎**：1/2/4/8 路并发总吞吐基本不变（300 帧 1080p 约 227–263 fps），
单路吞吐随并发数成反比。8 路会话都能建立（数据中心卡无消费级的会话数限制）。

**本机 ffmpeg 现状（2026-09-14 起）**：只有一个 `/usr/local/bin/ffmpeg`（7.1），
已含上述全部 T4 可用能力 **+ nvinterpolate（光流插帧）+ libvmaf**，无需任何环境文件。
注意"全部 T4 可用能力"里**不含任何 AV1 编码器**（硬编与软编都没有，见上）。
详见 project_nvinterpolate_build.md。系统 `/usr/bin/ffmpeg` 6.1.1 保留未动（被
imagemagick 依赖），平时不会被调用。

## 性能基线（1080p，**在有并发负载的情况下测得，绝对值偏低，仅供相对参考**）

- libx264：ultrafast 304 / veryfast 125 / medium 64 fps
- h264_nvenc：p1 523 / p4 337 / p7 204 fps；hevc_nvenc p4 310 fps；p4+scale_cuda→720p 543 fps
- 纯解码：NVDEC 543 fps vs **软解 1162 fps**（合成低复杂度 testsrc2；NVDEC 每帧有额外
  surface 开销，简单内容上软解反而更快）
- 零拷贝（`-hwaccel cuda -hwaccel_output_format cuda`）比「软解+NVENC」快约 6%；
  而 `h264_cuvid + hwupload_cuda` 比软解还慢（多一次拷贝）→ **推荐用 `-hwaccel cuda -hwaccel_output_format cuda`**
- 质量（6Mbps，PSNR）：libx264 medium 45.05dB / veryfast 44.26dB；h264_nvenc p4 44.23dB / p7 44.82dB
  → **NVENC p4 的质量约等于 x264 veryfast，但快 2.7x**

## 参数陷阱（全部 2026-09-14 实测）

**1. CUDA 零拷贝管线里不要传 `-pix_fmt`**

机制：`AVFrame` 有两个格式字段——`format`（帧本身的格式，硬解时是 `AV_PIX_FMT_CUDA`）和
`sw_format`（硬件帧背后的内存布局，8bit 4:2:0 是 `NV12`）。`-pix_fmt` 设的是 `format`，
**不是** `sw_format`。零拷贝链路里全是 cuda 帧，要求软件帧就协商失败。

实测（`-hwaccel_output_format cuda` 输入 + nvenc）：

| `-pix_fmt` | 结果 |
|---|---|
| 不传 / `cuda` | ✅ |
| `nv12` | ❌ `Impossible to convert` |
| `yuv420p` | ❌ `Impossible to convert` |
| `scale_cuda=format=yuv420p` + `-pix_fmt yuv420p` | ❌ `Impossible to convert` |
| 软件解码 + 同一编码器 + `-pix_fmt yuv420p` | ✅ |

**注意连 `nv12` 都失败**——别以为写底层格式就能"对齐"。要改格式用
`scale_cuda=format=yuv420p|yuv444p|p010le|nv12`，或 `hwdownload,format=...`。
`-pix_fmt` 只在不用 `-hwaccel_output_format cuda` 时才有意义。

改像素格式要配 `-profile:v`：`scale_cuda=format=yuv444p` + `-profile:v high444p` →
`h264/High 4:4:4 Predictive`；`format=p010le` + `-profile:v main10` → `hevc/Main 10`。
写 `-profile`（不带 `:v`）会报 `Option not found`。

**2. `-b:v` 是平均目标，不是上限；片段越短超得越狠**

仅 `-b:v 6M`、无 maxrate，1080p testsrc2 实测：2s→9.02M(**+50%**)、5s→7.55M(+26%)、
10s→6.90M(+15%)、30s→6.35M(+6%)、60s→6.18M(+3%)。
加 `-maxrate 6M -bufsize 12M` 后 60s→6.02M(+0.3%)，**但 2s 仍 7.44M(+24%)**——
短片段光加 maxrate 也压不住（整段都没超过一个 bufsize 窗口）。

模式语义：`-rc constqp -qp N` 和 `-cq N` 会**完全无视 `-b:v`**（码率由质量参数决定）；
`-cq 23` 与 `-b:v 0 -cq 23` 结果逐位相同。要卡码率用 `-b:v X -maxrate X -bufsize 2X` 或
`-rc cbr`；要画质用 `-cq N` 且别设 `-b:v`。

**3. `-rc vbr_hq`（用户流水线在用）在 FFmpeg 7.1 仍可用**，未被移除
（同组 `vbr_2pass`/`ll_2pass_quality` 已标 deprecated，但 `vbr_hq` 没有）。

**4. 测 PSNR 时两边长度必须一致**

`psnr` 滤镜基于 framesync，长度不等时会把短输入的结束帧继续跟长输入比，把平均值拉垮。
同一个 2s 编码产物：跟 60s 源比 = **23.86 dB**，跟等长 2s 片段比 = **47.16 dB**。
（`-shortest` 救不了，仍是 23.86。）
教训：对比画质前先确认两个输入帧数一致，否则会得出"短片段画质极差"的错误结论。
