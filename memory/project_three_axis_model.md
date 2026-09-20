---
name: vidcrop_hwaccel 的解码/缩放/编码三轴模型（--hwaccel 已硬更名为 --decode）
description: 2026-09-20 把 vidcrop_hwaccel.py 的 --hwaccel 拆成三个正交轴（--decode / --scale-algo / --codec）外加一个纯策略开关 --fallback-policy(auto/strict)；旧 --hwaccel 与 strict-cuda/nvenc-only/cpu-only 全部硬删除；新增「软解 + hwupload_cuda」显存缩放链；scale_cuda 探测与 can_cuda_scale 不再绑在解码上
type: project
---

## 事实

`vidcrop_hwaccel.py` 的 `--hwaccel` 原来是三个轴缠在一起的一个参数，实测确认三处硬伤：

1. `detect_cuda_capabilities()` 在 `hwaccel == 'none'` 时直接返回空 caps → **连 NVENC 编码
   也一起关掉了**，不只是解码。
2. `can_cuda_scale()` = `has_decoder and has_cuda_scale and has_nvenc(codec)` → **GPU 缩放
   被硬绑在 GPU 解码上**，软解 + GPU 缩放结构上不可能。
3. `scale_cuda` 只在 `detect_cuda` 分支里探测；旧的 `--fallback-policy nvenc-only` 只探编码器
   → `has_cuda_scale` 恒为 False，还会印出「当前 FFmpeg 里没有 scale_cuda」这种**假**结论。

2026-09-20 改成 **三个正交轴 + 一个纯策略开关**，轴之间**零冲突检查**：

| 轴 | 参数 | 取值 | 只管什么 |
|---|---|---|---|
| 解码 | `--decode` | `auto`(默认) / `cuda` / `vulkan` / `vaapi` / `opencl` / `cpu`（旧值 `none` ≡ `cpu`） | 是否下发 `-hwaccel` |
| 缩放 | `--scale-algo` | `auto`(默认) / `libswscale-<algo>` / `cuda-<algo>` | 重采样在哪、用什么算法 |
| 编码 | `--codec` | `auto` / `h264_nvenc` / `libx264` / … | `-c:v` |
| 策略 | `--fallback-policy` | `auto`(默认) / `strict` | **只回答一件事**：显式点名的后端不可用/失败时，降级还是报错 |

由此成立、以前被明令禁止的组合：`--decode cpu --codec h264_nvenc`（软解 + NVENC 硬编）、
`--decode cuda --codec libx264`（硬解 + 软编）、`--decode cpu --scale-algo cuda-lanczos`
（软解 + 显存内缩放）。

**破坏性变更（都已确认接受）**：
- `--hwaccel` **硬更名**为 `--decode`（不做兼容别名），用旧名由 argparse Action 直接退出 2
  并打印改名提示 + 三轴等价写法。只有旧**值** `none` 保留为 `cpu` 的同义词。
- `--fallback-policy` 从 5 个值收敛为 `auto / strict`，旧值 `strict-cuda` / `nvenc-only` /
  `cpu-only` **直接删除**（报错退出 2 并给出等价三轴写法）——它们本来是"三轴预设"而不是"策略"，
  混在同一个参数里是语义污染。
- **`--decode cpu` 不再等于「纯 CPU」**（相对旧 `--hwaccel none` 的语义收窄）。纯 CPU 要写成
  三轴全 CPU：`--decode cpu --scale-algo libswscale-lanczos --codec libx264`。

**新增链条**：软解 + 显存内缩放 = `hwupload_cuda → scale_cuda → 显式 hwdownload → CPU crop`。
用 **`hwupload_cuda`** 而不是通用 `hwupload`——后者必须配 `-filter_hw_device`（否则报
`A hardware device reference is required to upload frames to.`），`hwupload_cuda` 自带 device，
所以 `build_ffmpeg_cmd()` / `_prepend_hwdownload()` **零改动**。

**探测也按轴分开**：`detect_cuda_capabilities(decode=..., *, probe_encoders, probe_filters)`。
三轴全显式 CPU 时**一件都不探**（旧 `cpu-only` 的快速路径）。

**`auto` 缩放的判定用功能探针**：`_probe_cuda_scale_upload()` 真跑 1 帧
`format=nv12,hwupload_cuda,scale_cuda=128:128,hwdownload,format=nv12 → null`。
只在「`--scale-algo auto` + 拿不到 CUDA 帧 + `-filters` 里确有 `scale_cuda`」时才跑
（默认路径在 T4 上不会多这一次 ffmpeg 调用）。显式 `cuda-*` **不跑探针**、直接执行。

**Why:** 用户的原话是「`--hwaccel` 负责区分软件还是硬件进行编码和解码，`--scale-algo`
负责用哪种 scale 算法 …… 三者相互独立可以自由组合。简化判断逻辑」。纠缠的后果不只是难看：
`can_cuda_scale` 的硬绑定让「NVDEC 用不了但 GPU 缩放可用」这个真实场景**结构上无法表达**，
而 `nvenc-only` 下的漏探测还会输出假结论误导排查。

**How to apply:**
- 再加任何"后端选择"类参数时，先问它属于哪个轴；**不要**让新参数顺带改变别的轴的行为。
- 改 `--decode` 的判定或 `_decode_hwaccel()` 时注意：`auto → 'auto'`（字面量必须保持，默认路径
  靠它逐字不变）、`cpu → None`、其余后端不可用时也退化为 `None`（**报不报错交给
  `--fallback-policy`**，这一层只负责"实际能用哪个"）。
- `scale_backend == 'auto'` 的判据里 **NVENC 门必须保留**（否则 `--codec libx264` 会在有硬解的
  机器上凭空多出一条 CUDA 缩放链，破坏"默认行为逐字不变"）；只有显式 `cuda-*` 才放宽成
  "任何编码器都能接"。
- 改完必须跑三道回归：`temp/dump_filter_chains.sh`（16 行逐字）、默认路径 5 个用例逐字
  （`temp/chains_before_default.txt`）、`temp/verify_decode_axis.sh`（CLI 层 15 项）。
- 相关：`project_cuda_scale_cover.md`（缩放链的实测数据与质量门）、
  `project_t4_gpu_capabilities.md`（零拷贝链不能传 `-pix_fmt`）、
  `project_preset_equivalence.md`（两脚本一致性约定）。
