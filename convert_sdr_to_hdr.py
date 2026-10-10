#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convert_sdr_to_hdr.py – SDR 视频转 HDR10（基于 HDRTVNet++，ICCV2021/JCVPR2023）

功能
────
• 把 SDR 视频（BT.709）逐帧转成 HDR10（BT.2020 / PQ / 10bit），神经网络用
  HDRTVNet++（github.com/xiaom233/HDRTVNet-plus，MIT 协议）的 Ensemble_AGCM_LE 模型
• 整条链路是**两个 ffmpeg + 一段 Python**：
    ① 解码：ffmpeg -i 源 -f rawvideo -pix_fmt gbrp16le pipe:1
    ② 推理：Ensemble_AGCM_LE（AGCM 全局色调映射 + LE 局部增强，级联为一层）
    ③ 编码：ffmpeg -f rawvideo -pixel_format gbrp16le ... -c:v libx265 HDR10
  帧数据全程走 16bit 十六进制原始管道，**不落中间 PNG、不依赖 OpenCV**，
  也不经过上游那套 BasicSR（basicsr==1.4.2 在 Python 3.12+ 上装不上）
• 上游模型定义**直接 import，不复制代码**：把 <repo>/codes 塞进 sys.path 后
  from models.modules.Ensemble_AGCM_LE_arch import Ensemble_AGCM_LE
  上游升级只需重新 clone，不用同步本文件的模型定义
• --no-model 跳过神经网络，只跑 ①→③ 编码链路。用途有二：
  - 本机无 GPU / 无 torch 时仍能验证整条编码链路（抽帧、色彩标签、HDR10 静态元数据、mux）
  - 作为「网络效果到底带来多少」的对照基线
• 尺寸必须对齐到 8 的倍数：模型下采样三次（down_conv1~3 stride=2），
  非 8 倍数会在第一次 stride 卷积处报错。--mod-crop 自动向内裁到 8 的倍数
• --tile 分块推理**不等价于**整帧推理：AGCM 的条件网络 Color_Condition 最后
  是 AdaptiveAvgPool2d(1)，即每块各自算一个「全局」色调映射向量。分块后每块的
  条件向量不同，块间色调会有差异（实测 320x240 整帧 vs 分块 32 无重叠，
  最大差 0.17）。它是为「4K 整帧塞不进内存」做的取舍，不是无损优化；
  1080p 及以下建议整帧（--tile 0）
• **批处理与并行**：输入可以是目录（-r 递归），文件级并行由 --workers 控制；
  单文件并行另由 --split-mode 选路径 —— segment（按时间切段并行处理后 concat）、
  workers（单解码 + 多推理进程 + 单编码）、auto（多文件→文件级；单文件且够长→
  segment）、off。CPU 核数/内存自动探测并最大化并行（--threads/--mem-per-job 可手控）
• **编解码器与质量轴**：--codec（默认 libx265；别名 x265/hevc/svtav1/av1…）、
  --crf/--cq/--crf-ref/--cq-ref/--quality-mode/--qp/--bitrate/--rc-mode/--lookahead、
  NVENC 调优 --nvenc-aq/--nvenc-tune/--nvenc-multipass、--decode {cpu,cuda,auto}。
  --crf-ref/--cq-ref 走统一基准轴，按 convert_crf.py 的等效表换算（默认 quality 口径）
• **GPU 自动降级**：硬件解码/编码不可用或运行中失败时，按 --fallback-policy
  （默认 auto）自动降级到 CPU 等价物并告警；strict 则直接报错（退出码 2）
• **输出信息**：环境检查、概览（编码器·质量·解码·系统资源·并行度）、逐文件进度与
  ETA、汇总，以及**转换前后的结构化对比**（分辨率·编码·像素格式·位深·色彩四参数·
  HDR 静态元数据·时长·帧数·音频·字幕·体积）
• ⚠ **NVENC 写不了 HDR10 静态元数据**（mastering display / MaxCLL，编码器封装限制；
  色彩三参数与 10bit 位深仍保留）：显式用 hevc_nvenc 等时会显著告警

本版相对之前的**默认值变更**（都是有意为之，升级时请注意）：
• --crf 默认从字面量 20 改为「按统一基准 DEFAULT_REF=21 逐编码器换算」（libx265 → CRF 21）
• --preset 默认从 slow 改为按编码器取（libx265 → medium，与两个 vidcrop 脚本一致）
• --threads 现在是「每任务 ffmpeg 线程数」（与两个 vidcrop 脚本同名同义），
  原来的 torch 线程数旋钮改名为 --torch-threads

主要参数
────────
--input / --output     输入视频文件或目录 / 输出文件或目录（必选）
--recursive, -r        递归扫描输入目录（批量模式）
--model-repo           HDRTVNet-plus 仓库目录（默认 <脚本同级>/../HDRTVNet-plus）；
                       需含 codes/ 与 pretrained_models/Ensemble_AGCM_LE.pth
--no-model             跳过神经网络推理，只做 SDR→PQ 值域转换 + HDR10 编码
--device               推理设备 auto/cpu/cuda（默认 auto：有 CUDA 用 cuda，否则 cpu）
--torch-threads        torch CPU 线程数（默认 0 = 不干预）
--tile / --tile-overlap
                       分块推理的块尺寸与重叠像素。4K 这类大图整帧推理会吃光内存，
                       分块后显存/内存需求降到 (tile+overlap)² 量级。
                       0 = 整帧推理（1080p 及以下推荐）

编解码器
--codec                视频编码器（默认 libx265）。硬件编码器 hevc_nvenc / av1_nvenc
                       可用但需显式指定（HDR10 静态元数据会丢失；不可用时按
                       --fallback-policy 降级）
--decode               解码后端 cpu（默认）/ cuda / auto。硬解必须 hwdownload 回 CPU
                       再转 gbrp16le，收益有限，故默认保守
--fallback-policy      auto（默认，自动降级并告警）/ strict（不可用直接报错）

质量轴
--crf / --cq           字面量质量值（原样下发；落到不支持该量纲的编码器时按等效表换算）。
                       都不给则由基准轴 DEFAULT_REF=21 换算到目标编码器（libx265 → 21）
--crf-ref / --cq-ref   以统一基准轴（libx264 CRF / h264_nvenc CQ）给出质量，按等效表换算
--quality-mode         quality（默认，等质量表）/ size（等体积表）
--qp                   恒定 QP（只在 --rc-mode constqp 下生效；量程随编码器不同）
--bit-depth            输出位深，默认 10（唯一可选值；SDR→HDR 必须 10bit）

码率控制（NVENC）与调优
--rc-mode              auto（默认）/ constqp / vbr / cbr（非 NVENC 编码器下告警忽略）
--lookahead            前向预测帧数 0~250（按编码器分别下发）
--bitrate              目标码率（如 8M），与质量参数并存=受码率约束的恒定质量
--nvenc-aq             打开 NVENC 自适应量化（-spatial-aq 1 -temporal-aq 1）
--nvenc-tune / --nvenc-multipass
                       NVENC 的 -tune（hq/ll/ull/lossless/uhq）与 -multipass
                       （disabled/qres/fullres）

输出与容器
--preset               编码器预设（默认按编码器取：CPU medium / GPU p4 / svtav1 8）
--container            输出容器扩展名（如 .mp4 / .mkv）；不给则按编码器推导
--suffix               批量/目录输出的文件名后缀（默认 "_hdr"）
--color-range          输出 color_range：pc（默认，HDR10 交付惯例）/ tv
--audio                copy（默认）/ none
                       ⚠ 源音轨比视频短时会**自动降级为重编码并补静音**
                       （`-af apad`），以保住全部视频帧：`-shortest` 会在音频
                       结束处停止输出，实测音轨短 17s 的素材原本**整条转换失败**。
                       默认降到 aac（WebM 下降到 libopus）；已显式指定其它
                       编码器（libopus 等）时保留你的选择、只补静音
                       ⚠ 阈值 1 帧：实测真实素材 test3.mp4（音轨短 0.0597s
                       =1.79 帧）原本会静默丢 3 帧，故宁可多一次重编码
--subs                 auto（默认）/ none / keep / burn
--subs-index              N       选源里第几条字幕轨（0=第一条，默认）
                       字幕原先从一开始就被丢弃（单文件与批量**行为一致**）；`auto` 现在
                       至少会**明确告警**而非静默丢。keep 受容器/codec 限制、与
                       `--split-mode segment` 互斥；burn 用 libass 烧进画面。
                       ⚠ burn 与 keep **都不需要提质**：burn 可直接吃中间格式
                       gbrp16le（仍是单次编码）；keep 在必要时的两阶段走 `-c copy`
                       （不重编码、画质无损，只多一次 remux）
--audio-codec          copy（默认）/ aac / libopus …
                       ⚠ 非 copy 才能补静音：`apad` 与 `-c:a copy` 不能共存
                       （ffmpeg rc=234）
                       ⚠ **显式写 copy 也关不掉自动降级**：音轨偏短时照样降级补静音
                       （保帧优先于音轨原样），脚本会明确告知
--audio-codec / --audio-bitrate
                       音频编码器（默认 copy）与重编码码率（默认 128k）
--master-display / --no-master-display / --max-cll
                       HDR10 静态元数据（默认写 BT.2020 常规 mastering display 与 1000,400）
--mod-crop             宽高向内裁到 8 的倍数（默认开；--no-mod-crop 关闭）
--overwrite            覆盖已存在的输出（批量时对已存在文件默认跳过）
--dry-run              只打印环境/概览/计划与两条 ffmpeg 命令，不实际处理
--log LOG_FILE         把全部输出追加写入日志文件
--extra-args           追加任意 ffmpeg 参数（放在编码命令末尾、输出路径之前）

并发
--workers              并行任务数（0=自动）
--threads              每任务 FFmpeg 编码线程数（0=自动；只对软件编码器下发）
--mem-per-job          单任务估计内存 GB（0=按编码器画像 + NN 开销）
--sequential           强制顺序执行，显示单文件细粒度进度条
--split-mode           单文件并行路径 auto（默认）/ segment / workers / off

调试
--duration / --frames  只处理前 N 秒 / 前 N 帧（两者都给时以 --frames 为准；分段并行下禁用）

退出码：0 正常 / 1 ffmpeg 或推理失败 / 2 参数错误

示例
────
# 1) 先用 --dry-run 看命令（不碰模型、不耗 CPU）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --dry-run

# 2) 无 GPU 环境下先验证编码链路（跳过神经网络）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --no-model

# 3) 完整流程（默认 libx265 / 软解 / 单文件顺序）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4

# 4) 冒烟：只跑前 48 帧、CPU 推理
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --frames 48 --device cpu

# 5) 用 NVENC 硬件编码（会告警静态元数据丢失）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --codec hevc_nvenc --cq 26 --nvenc-aq

# 6) 统一基准轴给质量（libx264 CRF 21 的等质量换算）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --crf-ref 21

# 7) 批量目录 + 文件级并行
python3 convert_sdr_to_hdr.py -i ./videos -o ./out -r --workers 2

# 8) 单文件分块并行（按时间切段 → 并行处理 → 拼接）
python3 convert_sdr_to_hdr.py -i big.mp4 -o big_hdr.mp4 --split-mode segment --workers 4
"""
import argparse
import json
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

# 质量轴换算表（等体积 SIZE_MAP / 等质量 QUALITY_MAP）来自同目录 convert_crf.py ——
# 与 vidcrop_cpu_v2.py / vidcrop_hwaccel.py 共用同一份真源，避免各处硬编码偏移互相
# 矛盾。--crf-ref / --cq-ref 在默认口径（quality）下即走 QUALITY_MAP 的等质量换算。
try:
    from convert_crf import (SIZE_MAP, QUALITY_MAP, convert_quality, from_x264_crf,
                             to_x264_crf, get_quality_mode, get_quality_map,
                             set_quality_mode)
except ImportError:                       # 从其他工作目录启动时 sys.path 未必含本脚本所在目录
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from convert_crf import (SIZE_MAP, QUALITY_MAP, convert_quality, from_x264_crf,
                             to_x264_crf, get_quality_mode, get_quality_map,
                             set_quality_mode)

VERSION = "1.0"

# 仓库外默认位置：与 VidUtils 同级，避免把模型权重纳入 git
# 相对脚本位置：../HDRTVNet-plus（兼容开发/生产环境）
DEFAULT_MODEL_REPO = str(Path(__file__).resolve().parent.parent / "HDRTVNet-plus")

# 帧在原始管道里的字节数：gbrp16le = 平面 G/B/R，各 16bit 大端无关（ffmpeg 侧小端）
RAW_PIX_FMT = "gbrp16le"
BYTES_PER_SAMPLE = 2
PLANES = 3

# 模型下采样三次 → 宽高必须是 8 的倍数
ALIGN = 8

# 帧率缺省值：探测失败时用这个
DEFAULT_FPS = 30.0

# HDR10 默认静态元数据：BT.2020 显示器的常规 mastering display
# （x265 语法顺序为 G()B()R()，色度单位 0.00002，亮度单位 0.0001 cd/m²）
DEFAULT_MASTER_DISPLAY = ("G(13250,34500)B(7500,3000)R(34000,16000)"
                          "WP(15635,16450)L(10000000,1)")
DEFAULT_MAX_CLL = "1000,400"


# ═══════════════════════════════════════════════════════════════════
#  编码器 / 质量轴常量（与 vidcrop_cpu_v2.py、vidcrop_hwaccel.py 孪生）
#
#  ⚠ 下面这些表与常量必须与 vidcrop_cpu_v2.py 的同名项**逐字相同**：两脚本共用
#  同一套换算表、能力判定与 preset 映射，任一侧改动都要连带核对另一侧，否则
#  同一条逻辑请求会在两处给出不同结果。
#
#  与裁剪脚本的差别（有意）：本脚本**默认编码器是 libx265**（SDR→HDR10 交付口径，
#  且只有 x265 能写完整 HDR10 静态元数据），--codec 默认值与 DEFAULT_CODEC 一并
#  是 libx265；裁剪脚本那边分别是 libx264 / h264_nvenc。
# ═══════════════════════════════════════════════════════════════════
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".m4v", ".ts", ".webm", ".mpg", ".mpeg",
}

# 编码器 → 默认封装容器（--container 未给时按它推导输出扩展名）
CODEC_CONTAINER_MAP = {
    "libx264": ".mp4",
    "libx265": ".mp4",
    "libvpx-vp9": ".webm",
    "libvpx": ".webm",
    "vp9_qsv": ".webm",
    "libaom-av1": ".mp4",
    "libsvtav1": ".mp4",
    "librav1e": ".mp4",
    "av1_nvenc": ".mp4",
    "av1_qsv": ".mp4",
    "av1_amf": ".mp4",
    "prores": ".mov",
    "prores_ks": ".mov",
    "mpeg4": ".mp4",
    "libxvid": ".avi",
    "mjpeg": ".avi",
    "copy": None,
}

# 编码器别名 → FFmpeg 标准名
CODEC_ALIASES: Dict[str, str] = {
    "x264": "libx264",
    "x265": "libx265",
    "h264": "libx264",
    "h265": "libx265",
    "hevc": "libx265",
    "avc": "libx264",
    "vp9": "libvpx-vp9",
    "vp8": "libvpx",
    "libvpx_vp9": "libvpx-vp9",
    "av1": "libaom-av1",
    "av01": "libaom-av1",
    "svtav1": "libsvtav1",
    "svt-av1": "libsvtav1",
    "libsvt-av1": "libsvtav1",
    "rav1e": "librav1e",
    "prores": "prores_ks",
    "mpeg2": "mpeg2video",
    "h264_nvenc": "h264_nvenc",
    "hevc_nvenc": "hevc_nvenc",
    "h265_nvenc": "hevc_nvenc",
    "nvenc_av1": "av1_nvenc",
    "av1_nvenc": "av1_nvenc",
    # ⚠ 下面 7 个别名只在 vidcrop_hwaccel.py 里有，vidcrop_cpu_v2.py 的表没有 ——
    # 本脚本**有意**比 cpu_v2 多收这几个：它支持硬件编码器，`--codec nvenc` 之类的
    # 常见写法不该落到 ffmpeg 报 `Unknown encoder 'nvenc'`（对抗审查实测过）。
    "nvenc": "h264_nvenc",
    "nvenc_h264": "h264_nvenc",
    "nvenc_h265": "hevc_nvenc",
    "nvenc_hevc": "hevc_nvenc",
    "h265_amf": "hevc_amf",
    "h265_qsv": "hevc_qsv",
    "h265_videotoolbox": "hevc_videotoolbox",
}

# NVENC 家族：p1~p7 风格 preset + -cq 质量控制
NVENC_CODECS = {"h264_nvenc", "hevc_nvenc", "av1_nvenc"}

# 支持 -crf 的编码器。⚠ librav1e **不在**此集（它的 ffmpeg 选项里没有 -crf，只有
# -qp 0~255）；移除后它的字面量路径与 -ref 路径统一走基准轴，不再双链分歧。
CRF_SUPPORTED_CODECS = {
    "libx264", "libx265", "libvpx-vp9", "libvpx",
    "libaom-av1", "libsvtav1",
}

# 支持 -cq 的编码器（ffmpeg 私有选项 `-cq` 真正存在的那些）。
# QSV 无 `-cq`（实测 `-h encoder=h264_qsv` 只有 `-preset 0~7`）；VideoToolbox 的质量
# 轴是 `-q:v`。二者均移出。
CQ_SUPPORTED_CODECS = {
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_amf", "hevc_amf", "av1_amf",
}

# 支持 -preset 的编码器（libaom-av1 用 -cpu-used、libvpx-vp9 用 -deadline、
# librav1e 用 -speed，传 -preset 只会被静默忽略，故不列入）。
PRESET_SUPPORTED_CODECS = {
    "libx264", "libx265",
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_amf", "hevc_amf", "av1_amf",
    "h264_qsv", "hevc_qsv", "av1_qsv",
    "h264_videotoolbox", "hevc_videotoolbox",
    "libsvtav1",
}

# QSV 族：preset 用 veryfast..veryslow 名字（实测枚举 0~7 命名档），**不认** p1~p7
_QSV_CODECS = {"h264_qsv", "hevc_qsv", "av1_qsv"}

# 硬件编码器族：ffmpeg 的帧级 `-threads` 对它们没有意义，一律跳过 -threads。
# ⚠ 必须与 vidcrop_cpu_v2.py / vidcrop_hwaccel.py 的同名常量逐字相同。
_HW_ENCODERS = {
    "h264_nvenc", "hevc_nvenc", "h265_nvenc", "av1_nvenc",
    "h264_qsv", "hevc_qsv", "av1_qsv", "vp9_qsv",
    "h264_amf", "hevc_amf", "av1_amf",
    "h264_vaapi", "hevc_vaapi",
    "h264_videotoolbox", "hevc_videotoolbox",
}

# 只认 `-qp`、既不认 `-crf` 也不认 `-cq` 的编码器（VAAPI 族，实测 `-qp (0 to 52)`）。
# 不列入任何能力集会让用户给的质量值被静默丢弃，故单列并归一到基准轴后经 -qp 下发。
_QP_ONLY_CODECS = {"h264_vaapi", "hevc_vaapi"}

# ── preset 映射 ────────────────────────────────────────────────────
# libsvtav1 的 -preset 是 0~13 整数，**不接受**名字类取值（实测传 'medium' 报
# "Unable to parse option value"），必须单独换算。
X264_TO_SVTAV1_PRESET = {
    "ultrafast": 13, "superfast": 12, "veryfast": 11, "faster": 10,
    "fast": 9, "medium": 8, "slow": 6, "slower": 4, "veryslow": 2,
    "placebo": 0,
}
# NVENC → libsvtav1 整数档（GPU→CPU 降级用）。默认档 p4 落 8（= medium 等效，CR-1）。
NVENC_TO_SVTAV1_PRESET = {
    "p1": 12, "p2": 11, "p3": 10, "p4": 8, "p5": 8, "p6": 6, "p7": 2,
}

# NVENC preset → libx264 preset（**用于 GPU→CPU 降级的档位等效**，不是逆表）。
# 取向：**medium 落在默认档 p4**（跨仓契约 CR-1）；保留 p5→medium 兼容显式 p5。
# ⚠ 必须与 vidcrop_hwaccel.py / vidcrop_cpu_v2.py 的同名表逐字相同。
NVENC_TO_X264_PRESET: Dict[str, str] = {
    "p1": "ultrafast",
    "p2": "superfast",
    "p3": "veryfast",
    "p4": "medium",
    "p5": "medium",
    "p6": "slow",
    "p7": "veryslow",
}

# x264 名字 → NVENC 档：**用户显式给 x264 风格 preset 时用**，与上一表**不是互逆**
# （x264 有 10 档、NVENC 只有 7 档，两端必须压缩）。两表都以 medium=p4 为准。
X264_TO_NVENC_PRESET: Dict[str, str] = {
    "ultrafast": "p1", "superfast": "p1",
    "veryfast": "p2", "faster": "p3", "fast": "p3",
    "medium": "p4", "slow": "p5", "slower": "p6",
    "veryslow": "p7", "placebo": "p7",
}

PRESET_VALUES = {
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow", "placebo",
}

# 统一质量基准（libx264 CRF 轴）。未给质量参数时按它换算到目标编码器的原生刻度，
# 保证软编/硬编的**默认视觉质量一致**。
DEFAULT_REF = 21

# 默认视频编码器：SDR→HDR10 用 libx265（见文件头常量块的说明）。
DEFAULT_CODEC = "libx265"

# 默认预设：CPU 软编 medium、GPU 硬编 p4（CR-1）、libsvtav1 固定 8（SIZE_MAP 的
# svtav1 行是在 `-preset 8` 下标定的，默认档不能随核数漂移）。
DEFAULT_PRESET_CPU = "medium"
DEFAULT_PRESET_GPU = "p4"
DEFAULT_PRESET_SVTAV1 = "8"

# ── 位深继承 / --pix-fmt 相关的目标像素格式表 ───────────────────────
# 10bit 源在各编码器下的目标格式（NVENC 用 p010le；h264_nvenc 只做 8bit，见下）
_PIXFMT_10BIT_BY_ENCODER = {
    "libx264": "yuv420p10le",
    "libx265": "yuv420p10le",
    "libsvtav1": "yuv420p10le",
    "libaom-av1": "yuv420p10le",
    "librav1e": "yuv420p10le",
    "libvpx-vp9": "yuv420p10le",
    "h264_nvenc": "p010le",
    "hevc_nvenc": "p010le",
    "av1_nvenc": "p010le",
    "av1_qsv": "p010le",
    "av1_amf": "p010le",
    "prores": "yuv422p10le",
    "prores_ks": "yuv422p10le",
}
# h264_nvenc 在列：NVENC 的 H.264 只做 8bit，喂 10bit 输入会直接失败（实测 rc=218），
# 故 10bit 源落到它身上时降为 8bit 而不是让它硬撑。
_ENCODERS_8BIT_ONLY = {"mpeg4", "libvpx", "mjpeg", "vp8", "h264_v4l2m2m",
                       "h264_nvenc"}

_PIXFMT_BY_DEPTH: Dict[int, Dict[str, str]] = {
    8: {
        "h264_nvenc": "yuv420p", "hevc_nvenc": "yuv420p", "av1_nvenc": "yuv420p",
        "av1_qsv": "yuv420p", "av1_amf": "yuv420p",
        "prores": "yuv422p", "prores_ks": "yuv422p",
    },
    10: dict(_PIXFMT_10BIT_BY_ENCODER),
    12: {
        "libx264": "yuv420p12le", "libx265": "yuv420p12le",
        "libsvtav1": "yuv420p12le", "libaom-av1": "yuv420p12le",
        "librav1e": "yuv420p12le", "libvpx-vp9": "yuv420p12le",
        "hevc_nvenc": "p012le", "av1_nvenc": "p012le",
        "av1_qsv": "p012le", "av1_amf": "p012le",
        "prores": "yuv422p12le", "prores_ks": "yuv422p12le",
    },
}
_DEFAULT_PIXFMT_BY_DEPTH: Dict[int, str] = {
    8: "yuv420p", 10: "yuv420p10le", 12: "yuv420p12le",
}
_BIT_DEPTH_CHOICES = (8, 10, 12)

# ⚠ 与 vidcrop 两脚本的差别（有意）：本脚本**没有** --output-width/height 与 --pix-fmt，
# 输出恒为 10bit、尺寸只由源（--mod-crop 内裁到 8 的倍数）决定，所以那套
# pix_fmt/尺寸奇偶校验（PIX_FMT_REQUIRE_*、PIX_FMT_DEFAULT_BY_CODEC、
# validate_output_dimensions、resolve_pix_fmt_for_*）在这里是死代码 —— 2026-10-08
# 对抗审查发现后已删除，留着只会让人以为"看起来支持"。
# _BIT_DEPTH_CHOICES / _PIXFMT_BY_DEPTH 仍然有用：前者被 argparse 的 choices 复用，
# 后者是选 pix_fmt 的唯一真源。

# ── 码率控制轴（--rc-mode / --qp / --lookahead / --bitrate）──────────
# ⚠ FFmpeg 9.0 的 `-rc` 枚举**只剩 constqp / vbr / cbr**（实测 2026-10-04：vbr_hq /
# cbr_hq / cbr_ld_hq / qvbr 全部 rc≠0 Invalid argument）。移出项不再接受（CLI 层
# 直接报错，好过透传后运行期失败）。
# ⚠ 与 vidcrop_cpu_v2.py / vidcrop_hwaccel.py 的表逐字一致（孪生约定）。
_RC_BACKEND = "nvenc"
_RC_MODES = ("constqp", "vbr", "cbr")
_RC_MODE_HELP = ("nvenc：" + " ".join(_RC_MODES)
                 + "\n  （constqp=恒定 QP（配 --qp）；vbr=可变码率；"
                   "cbr=恒定码率（配 --bitrate））")
_RC_MODES_WITH_BITRATE = ("auto",) + tuple(m for m in _RC_MODES if m != "constqp")
# CR-2：NVENC 的**默认 rc**（与 VE 口径一致）——h264/hevc/av1 全部 vbr。
_NVENC_DEFAULT_RC = {"h264_nvenc": "vbr", "hevc_nvenc": "vbr", "av1_nvenc": "vbr"}

# NVENC 调优轴：-tune 默认 hq（写等于没写，故默认不下发）；uhq 仅 hevc/av1 有。
# -multipass 默认 disabled，CQ 路径下发不升 VMAF，只在 CBR/受限码率时自动补 fullres。
_NVENC_TUNE_VALUES = ("hq", "ll", "ull", "lossless", "uhq")
_NVENC_MULTIPASS_VALUES = ("disabled", "qres", "fullres")
_NVENC_UHQ_CODECS = {"hevc_nvenc", "av1_nvenc"}

_LOOKAHEAD_RANGE = (0, 250)
_QP_RANGE = (0, 51)
_BITRATE_RE = re.compile(r"^\d+(\.\d+)?[kKmM]?$")
_LOOKAHEAD_HINT = f"x264 的上限就是 {_LOOKAHEAD_RANGE[1]}；不指定=沿用各编码器默认"
_QP_HINT = "真实 QP（与 x264 QP 同尺度，非 --cq 的 CQ 轴）；量程随编码器不同"

# ── CONSTQP 轴：`-qp` 是**真实量化步长**（与 x264 QP 同尺度），不是 CQ 轴 ──
# AV1 族的 `-qp` 是 AV1 qindex（≈ 4×QP），需再过一层尺度；各编码器 `-qp` 量程也与
# CQ 轴不同（av1_nvenc 的 CQ 是 0~63 而 `-qp` 是 0~255），故另立 _QP_LIMITS。
_QP_SCALE: Dict[str, int] = {"av1_nvenc": 3, "librav1e": 4}
_QP_LIMITS: Dict[str, Tuple[int, int]] = {
    "h264_nvenc": (0, 51), "hevc_nvenc": (0, 51),
    "av1_nvenc": (0, 255), "librav1e": (0, 255),
    "libsvtav1": (0, 63),
    "h264_vaapi": (0, 52), "hevc_vaapi": (0, 52),
    "libx264": (0, 51), "libx265": (0, 51),
}
# **quality 口径**的 QP 轴等质量行（CR-4 与 VE 同步；NVENC h264/hevc 于 T4、av1 于 L40）。
# AV1 的 `-qp`(qindex) 对基准 CRF 是**仿射带大负截距**，过原点的 ×3 只在 ref≈21 成立。
# ⚠ 数值逐条等于 VE 的 QUALITY_MAP_QP（跨仓契约 CR-4）。
_QP_AFFINE_QUALITY: Dict[str, Tuple[float, float]] = {
    "h264_nvenc": (0.9704, 1.4767),
    "hevc_nvenc": (1.1083, -2.9183),
    "av1_nvenc": (7.9338, -97.5136),
}

# librav1e 下发的 -speed：0 = 不下发（native 档），与共享表标定档一致。
_RAV1E_SPEED = 0

# WebM 只接受 Vorbis / Opus 音轨，其余需转 Opus（否则写头失败）
_WEBM_AUDIO_CODECS = {"opus", "vorbis"}
_WEBM_AUDIO_FALLBACK = "libopus"
_BITMAP_SUBS = {"dvd_subtitle", "dvb_subtitle", "dvb_teletext",
                "hdmv_pgs_subtitle", "xsub"}

# ── 字幕透传的容器分派（2026-10-09 实测，与音频那套同构）──────────────
# 实测矩阵（源：mkv 里的 subrip 软字幕，ffmpeg 9.0.2）：
#   mkv + -c:s copy✓ 直传成功
#   mp4  + -c:s copy    ✗ rc=234 "Could not find tag for codec subrip"
#   mp4  + -c:s mov_text ✓
#   mov  + -c:s copy    ✗ 同 mp4（subrip 不被 QuickTime 容器接受）
#   webm + 任何          ✗ rc=234（WebM 只收 WebVTT；且本条报错同时含视频编码限制）
# ⇒ 结论：**只有 mkv 能直传**；mp4/mov 需转 mov_text；webm 需转 webvtt。
_MP4_SUB_FALLBACK = "mov_text"
_WEBM_SUB_FALLBACK = "webvtt"
# webm 只接受 WebVTT 字幕
_WEBM_SUB_CODECS = {"webvtt"}
# 这些容器可以原样 copy 文本字幕（不转换仍安全）
_SUBS_DIRECT_CONTAINERS = {".mkv", ".mka"}
# ⚠ 这里**必须带前导点**：调用方传的是 `dst.suffix`（形如 ".mp4"）。
# 音频那边同名常量 `_MP4_FAMILY = {"mp4","m4v","mov"}` 不带点是**因为**它收到的是
# 已经 `normalize_container()` 过的值；照抄过来会让 `".mp4" in {...}` 恒为 False
# ⇒ 静默落到「未知容器」分支返回 copy ⇒ 不下发 -c:s ⇒ mp4 自动编码器选择失败 rc=8。
# （实测踩过：`-c:s mov_text` rc=0 ✓ / `copy` rc=234 / 不给 rc=8。）
_MP4_FAMILY = {".mp4", ".m4v", ".mov"}

# ── 解码轴 ────────────────────────────────────────────────────────
# auto：探测到可用的 CUDA(hwaccel) 才启用，否则软解。本脚本**默认 cpu**（保守），
# 想试硬件解码需显式 --decode cuda —— 因为 SDR 源的解码在整条链里占比很小
# （NN 推理才是瓶颈），而硬解输出必须 hwdownload 回 CPU 再转 gbrp16le。
DECODE_BACKENDS = ("auto", "cpu", "cuda")

# 编码器 / 每任务资源画像：(推荐线程数, 单任务估计内存 GB)
CODEC_PROFILE: Dict[str, Tuple[int, float]] = {
    "libx264": (4, 0.8),
    "libx265": (4, 1.2),
    "libvpx": (4, 0.8),
    "libvpx-vp9": (4, 1.0),
    "libaom-av1": (4, 1.5),
    "libsvtav1": (8, 2.0),
    "librav1e": (4, 1.2),
    "av1_nvenc": (2, 0.5),
    "av1_qsv": (2, 0.5),
    "vp9_qsv": (2, 0.5),
    "prores": (4, 1.0),
    "prores_ks": (4, 1.0),
    "mpeg4": (2, 0.4),
    "libxvid": (2, 0.4),
    "mjpeg": (2, 0.3),
}
# NN 推理的额外资源画像：torch 模型（~591k 参数）本体很小，但每帧的中间张量按
# 分辨率增长；给一个保守的常量，供并行度计算时叠加到编码器画像上。
NN_MEM_OVERHEAD_GB = 1.0
# 单任务（解码+推理+编码三段同时在跑）的最小内存冗余
_MIN_SYSTEM_RESERVE_GB = 0.2
_MAX_USABLE_RATIO = 0.80

_MIN_EFFORT_TIERS = (
    (16, 4, 7),
    (8, 5, 8),
    (4, 6, 9),
    (0, 8, 10),
)


# ═══════════════════════════════════════════════════════════════════
#  日志（Tee：终端 + 文件）
# ═══════════════════════════════════════════════════════════════════
class Tee:
    """将输出同时写入原始流和日志文件"""

    def __init__(self, original, log_file):
        self.original = original
        self.log = open(log_file, 'a', encoding='utf-8')
        self.log.write(f"\n\n{'='*60}\n")
        self.log.write(f"执行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log.write(f"命令行: {' '.join(sys.argv)}\n{'='*60}\n\n")

    def write(self, data):
        self.original.write(data)
        self.log.write(data)
        self.log.flush()

    def flush(self):
        self.original.flush()
        self.log.flush()

    def close(self):
        self.log.close()


def setup_log(log_path: str) -> None:
    """重定向标准输出/错误到 Tee 对象"""
    sys.stdout = Tee(sys.stdout, log_path)
    sys.stderr = Tee(sys.stderr, log_path)


# ═══════════════════════════════════════════════════════════════════
#  子进程注册 / 信号处理
#
#  与 vidcrop_cpu_v2.py 的同名设施逐字对应（孪生约定）：所有 ffmpeg 子进程登记到
#  _ACTIVE_PROCS，收到 SIGINT/SIGTERM 时统一终止，避免留下孤儿进程。
# ═══════════════════════════════════════════════════════════════════
_ACTIVE_PROCS: Set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()
_STOP_REQUESTED = threading.Event()


def _register_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCS.add(proc)


def _unregister_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCS.discard(proc)


def _terminate_active_procs(grace: float = 5.0) -> None:
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE_PROCS)

    for proc in procs:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    deadline = time.time() + grace
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
            except Exception:
                pass


def _signal_handler(_sig, _frame) -> None:
    _STOP_REQUESTED.set()
    print("\n[INFO] 收到中断信号，正在终止 FFmpeg 子进程……", file=sys.stderr)
    _terminate_active_procs()
    raise KeyboardInterrupt


def install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)


def _ffmpeg_env(cuda_visible_devices: str = "all",
                nvidia_driver_caps: str = "compute,video,utility") -> dict:
    """
    构造 FFmpeg / FFprobe 子进程的环境变量（与 vidcrop_hwaccel.py 的同名函数对应）。

    沙箱（Landlock+seccomp、受限容器等）中读取 /proc/sys/crypto/fips_enabled 会返回
    EIO，而 libgcrypt(>=1.10) 把非 ENOENT 的读取错误当致命错误并 abort()，导致 ffmpeg
    还没解析命令行就以 exit 134 退出。设置 LIBGCRYPT_FORCE_FIPS_MODE=0 后跳过该读取
    （值必须是 "0"；"1" 反而强制开启 FIPS 自检，在沙箱里更容易失败）。

    另补 NVIDIA 相关环境变量与常见 CUDA 库搜索路径，提升容器内 CUDA 初始化成功率。
    """
    env = os.environ.copy()
    env["LIBGCRYPT_FORCE_FIPS_MODE"] = "0"

    if "NVIDIA_VISIBLE_DEVICES" not in env:
        env["NVIDIA_VISIBLE_DEVICES"] = cuda_visible_devices
    if "NVIDIA_DRIVER_CAPABILITIES" not in env:
        env["NVIDIA_DRIVER_CAPABILITIES"] = nvidia_driver_caps

    cuda_lib_paths = [
        "/usr/local/cuda/lib64",
        "/usr/local/cuda/lib",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib64",
    ]
    existing_ld = env.get("LD_LIBRARY_PATH", "")
    for p in cuda_lib_paths:
        if os.path.isdir(p) and p not in existing_ld:
            existing_ld = f"{p}:{existing_ld}" if existing_ld else p
    if existing_ld != env.get("LD_LIBRARY_PATH", ""):
        env["LD_LIBRARY_PATH"] = existing_ld

    return env


# ═══════════════════════════════════════════════════════════════════
#  CPU / 内存探测
# ═══════════════════════════════════════════════════════════════════
def _detect_cpu() -> Tuple[int, str]:
    try:
        with open("/sys/fs/cgroup/cpu.max", "r", encoding="utf-8") as f:
            parts = f.read().strip().split()
        if len(parts) == 2 and parts[0] != "max":
            quota = int(parts[0])
            period = int(parts[1])
            if quota > 0 and period > 0:
                return max(1, int(round(quota / period))), f"cgroup v2 ({quota}/{period})"
    except Exception:
        pass

    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r", encoding="utf-8") as f:
            quota = int(f.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r", encoding="utf-8") as f:
            period = int(f.read().strip())
        if quota > 0 and period > 0:
            return max(1, int(round(quota / period))), f"cgroup v1 ({quota}/{period})"
    except Exception:
        pass

    try:
        n = len(os.sched_getaffinity(0))
        if n > 0:
            return n, "sched_getaffinity"
    except Exception:
        pass

    return max(1, os.cpu_count() or 1), "os.cpu_count"


def _read_cgroup_v2_memstat() -> Dict[str, int]:
    stat: Dict[str, int] = {}
    try:
        with open("/sys/fs/cgroup/memory.stat", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        stat[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except Exception:
        pass
    return stat


def _detect_memory_gb() -> Tuple[float, float, str]:
    try:
        with open("/sys/fs/cgroup/memory.max", "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v != "max":
            limit = int(v)
            current = 0
            try:
                with open("/sys/fs/cgroup/memory.current", "r", encoding="utf-8") as f:
                    current = int(f.read().strip())
            except Exception:
                pass

            stat = _read_cgroup_v2_memstat()
            reclaimable = stat.get("file", 0) + stat.get("slab_reclaimable", 0)
            non_reclaim = max(0, current - reclaimable)
            avail = max(limit - non_reclaim, limit // 20)

            return limit / (1024 ** 3), avail / (1024 ** 3), "cgroup v2"
    except Exception:
        pass

    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r", encoding="utf-8") as f:
            limit = int(f.read().strip())
        if limit < (1 << 62):
            usage = 0
            try:
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes", "r", encoding="utf-8") as f:
                    usage = int(f.read().strip())
            except Exception:
                pass

            cache = 0
            try:
                with open("/sys/fs/cgroup/memory/memory.stat", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("total_cache "):
                            cache = int(line.split()[1])
                            break
            except Exception:
                pass

            non_reclaim = max(0, usage - cache)
            avail = max(limit - non_reclaim, limit // 20)
            return limit / (1024 ** 3), avail / (1024 ** 3), "cgroup v1"
    except Exception:
        pass

    try:
        info: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                k, _, rest = line.partition(":")
                if rest:
                    info[k.strip()] = int(rest.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", total)
        if total > 0:
            return total / (1024 ** 3), avail / (1024 ** 3), "/proc/meminfo"
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength",                ctypes.c_ulong),
                    ("dwMemoryLoad",             ctypes.c_ulong),
                    ("ullTotalPhys",             ctypes.c_ulonglong),
                    ("ullAvailPhys",             ctypes.c_ulonglong),
                    ("ullTotalPageFile",         ctypes.c_ulonglong),
                    ("ullAvailPageFile",         ctypes.c_ulonglong),
                    ("ullTotalVirtual",          ctypes.c_ulonglong),
                    ("ullAvailVirtual",          ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            total = stat.ullTotalPhys / (1024 ** 3)
            avail = stat.ullAvailPhys / (1024 ** 3)
            if total > 0:
                return total, avail, "GlobalMemoryStatusEx"
        except Exception:
            pass

    return 2.0, 1.6, "fallback default"


def detect_system_resources() -> Tuple[int, float, float, str, str]:
    """返回 (逻辑核数, 总内存 GB, 可用内存 GB, CPU 探测来源, 内存探测来源)。

    与 vidcrop_cpu_v2.py 的同名函数一致（本函数不自己打印，概览块的 `系统资源`
    一行会把这几个值连同两个来源一并写出）。
    """
    cpu, cpu_src = _detect_cpu()
    total_gb, avail_gb, mem_src = _detect_memory_gb()
    return cpu, total_gb, avail_gb, cpu_src, mem_src


def compute_parallelism(
    num_pending: int,
    codec: str,
    cpu: int,
    total_mem_gb: float,
    avail_mem_gb: float,
    workers_override: int = 0,
    threads_override: int = 0,
    mem_per_job_override: float = 0.0,
    nn_enabled: bool = True,
    gpu_infer: bool = False,
) -> Tuple[int, int]:
    """
    算出 (并行任务数, 每任务线程数)。与 vidcrop_cpu_v2.py 的同名函数同构，另加两条
    本脚本特有的约束：

      · **NN 推理的资源画像**：每个任务除了 ffmpeg 编解码，还要在进程内跑 torch
        推理，故单任务内存画像 = CODEC_PROFILE[codec] + NN_MEM_OVERHEAD_GB
        （NN 关闭时不计）。
      · **GPU 推理默认单任务**：CUDA 下每个工作进程各持一份模型与 CUDA 上下文，
        并发只会在显存上互相挤（且 torch 一侧还可能有 GIL/驱动争用），所以
        `gpu_infer=True` 且用户未显式给 --workers 时并发固定为 1；显式给了就尊重
        用户意图（那是有意的过订），但仍不越过 num_pending。
    """
    pref_threads, default_mem = CODEC_PROFILE.get(codec, (4, 0.8))
    if nn_enabled:
        default_mem += NN_MEM_OVERHEAD_GB

    if num_pending <= 0:
        threads = threads_override if threads_override > 0 else min(pref_threads, max(1, cpu))
        return 0, max(1, threads)

    if num_pending == 1 and workers_override == 0:
        threads_per_job = threads_override if threads_override > 0 else cpu
        # 单文件：**文件级**并发恒为 1，但 --split-mode 的分段数/进程数需要一个
        # "期望并行宽度"，故这里按 CPU 与内存算一个宽度返回（调用方用
        # min(width, len(pending)) 收敛出文件级并发）。
        budget = max(0.1, min(total_mem_gb * _MAX_USABLE_RATIO,
                              max(0.0, avail_mem_gb - _MIN_SYSTEM_RESERVE_GB)))
        width_by_mem = max(1, int(budget / max(0.1, default_mem)))
        width = max(1, min(cpu // max(1, pref_threads), width_by_mem))
        return width, max(1, min(threads_per_job, cpu))

    if threads_override > 0:
        threads_per_job = min(threads_override, max(1, cpu))
    else:
        threads_per_job = min(pref_threads, max(1, cpu))

    if workers_override > 0:
        workers = workers_override
    elif gpu_infer:
        # GPU 推理：默认不并发（见 docstring）
        workers = 1
    else:
        max_by_cpu = max(1, cpu // max(1, threads_per_job))
        mem_per_job = mem_per_job_override if mem_per_job_override > 0 else default_mem

        budget_by_total = total_mem_gb * _MAX_USABLE_RATIO
        budget_by_avail = max(0.0, avail_mem_gb - _MIN_SYSTEM_RESERVE_GB)
        usable_mem = max(0.1, min(budget_by_total, budget_by_avail))

        max_by_mem = max(1, int(usable_mem / max(0.1, mem_per_job)))
        workers = min(max_by_cpu, max_by_mem)

    # ⚠ 这里**不再**按 num_pending 钳并发：单文件场景下 workers 还要驱动
    # --split-mode 的分段数/进程数（钳成 1 会把单文件并行整个关掉）。
    # 文件级并行由调用方按 min(workers, len(pending)) 收敛，多出来的 worker 无害。
    workers = max(1, workers)

    # [D] 显式 --workers 时按**最终并发数**反推每任务线程预算，避免 workers×threads
    # 超过逻辑核数（用户显式给了 --threads 则尊重其意图，不覆盖）。
    if workers_override > 0 and threads_override == 0:
        threads_per_job = max(1, cpu // max(1, workers))

    return workers, threads_per_job


def auto_effort(cpu_count: Optional[int] = None) -> Tuple[int, int]:
    """按可用核数返回 (libaom-av1 的 -cpu-used, libsvtav1 的 -preset)。"""
    if cpu_count is None:
        cpu_count, _src = _detect_cpu()
    for min_cores, aom_cpu_used, svtav1_preset in _MIN_EFFORT_TIERS:
        if cpu_count >= min_cores:
            return aom_cpu_used, svtav1_preset
    return 8, 10


# ═══════════════════════════════════════════════════════════════════
#  通用格式化 / 路径工具（与 vidcrop_cpu_v2.py 对齐，便于两脚本输出可比对）
# ═══════════════════════════════════════════════════════════════════
def _fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    if sec < 60:
        return f"{sec:.1f}s"
    total = int(sec)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02d}h{minutes:02d}m{seconds:02d}s"
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes}m{seconds:02d}s"


def _fmt_size(n_bytes: float) -> str:
    """格式化字节数为人类可读字符串。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n_bytes < 1024.0:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024.0
    return f"{n_bytes:.1f} PB"


def _label(text: str, width: int = 12) -> str:
    """把标签按显示宽度补齐到 width 列（CJK 记 2 列），输出「标签 + 空格 + ': '」。"""
    cells = sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)
    return text + " " * max(1, width - cells) + ": "


def _file_bytes(src: Path) -> int:
    """读取文件字节数；stat 失败按 0 处理（不参与吞吐量统计）。"""
    try:
        return src.stat().st_size
    except OSError:
        return 0


def _same_path(a: Path, b: Path) -> bool:
    """比较两个路径是否指向同一位置（优先解析符号链接）。

    resolve() 可能因权限不足、符号链接环等抛异常，此时回退到不解析链接的 abspath
    比较：它更保守（可能误报相同，不会漏报），而漏判等于覆盖源文件。
    """
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


def _size_change(src: Path, dst: Path) -> str:
    """输出「输入 → 输出（↓x%）」的体积变化描述。"""
    try:
        in_size, out_size = src.stat().st_size, dst.stat().st_size
    except OSError:
        return "—"
    if in_size <= 0:
        return _fmt_size(out_size)
    ratio = (1.0 - out_size / in_size) * 100
    direction = "↓" if ratio >= 0 else "↑"
    return f"{_fmt_size(in_size)} → {_fmt_size(out_size)}（{direction}{abs(ratio):.1f}%）"


class QueueETA:
    """整批队列的剩余时间预测（顺序执行时的「整批还要多久」）。

    口径：以「已处理字节 / 已耗时」作为吞吐率，外推剩余未开始文件的字节数。
    与 vidcrop_cpu_v2.py 的同名类一致。
    """

    BAR_WIDTH = 20

    def __init__(self, sizes: List[int]) -> None:
        self.total = len(sizes)
        self._remaining = sum(sizes)
        self._timed_bytes = 0
        self._timed_seconds = 0.0

    def add(self, size: int, elapsed: float) -> None:
        size = max(0, min(size, self._remaining))
        self._remaining -= size
        if elapsed > 0:
            self._timed_bytes += size
            self._timed_seconds += elapsed

    def eta(self) -> Optional[float]:
        if self._remaining <= 0 or self._timed_bytes <= 0 or self._timed_seconds <= 0:
            return None
        return self._remaining / (self._timed_bytes / self._timed_seconds)

    def rate(self) -> Optional[float]:
        if self._timed_bytes <= 0 or self._timed_seconds <= 0:
            return None
        return self._timed_bytes / self._timed_seconds

    def split(self, size: int) -> Tuple[Optional[float], Optional[float]]:
        rate = self.rate()
        if rate is None:
            return None, None
        return max(0, self._remaining - size) / rate, size / rate

    def line(self, handled: int, elapsed: float,
             done: int, failed: int, skipped: int) -> str:
        pct = handled / self.total if self.total else 1.0
        filled = int(round(self.BAR_WIDTH * pct))
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        eta = self.eta()
        eta_s = _fmt_time(eta) if eta is not None else "--"
        return (
            f"[{bar}] {handled}/{self.total}({pct * 100:.1f}%)  "
            f"完成 {done}  失败 {failed}  跳过 {skipped}  "
            f"累计 {_fmt_time(elapsed)}  预计剩余 {eta_s}"
        )


# ═══════════════════════════════════════════════════════════════════
#  依赖自检
# ═══════════════════════════════════════════════════════════════════
def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"[ERROR] 系统中未找到 {tool}，请先安装 FFmpeg。", file=sys.stderr)
            sys.exit(1)


def torch_install_hint() -> str:
    """缺 torch 时的安装指引。分发行版给不同命令。"""
    return (
        "本机未安装 PyTorch。安装方式（任选其一）：\n"
        "  · 发行版包（可能偏旧，但无需外网）：\n"
        "      sudo apt-get install -y python3-torch python3-numpy\n"
        "  · 官方 wheel（版本新，需外网；注意本机 Python 3.14 需 torch≥2.9）：\n"
        "      python3 -m pip install --break-system-packages torch numpy\n"
        "  · 或先用 --no-model 只验证编码链路（不需要 torch）"
    )


def check_model_repo(model_repo: Path) -> Tuple[bool, str]:
    """
    检查模型仓库是否就绪。
    Returns: (ready, message)
    """
    codes = model_repo / "codes"
    weights = model_repo / "pretrained_models" / "Ensemble_AGCM_LE.pth"
    
    if not codes.is_dir():
        return False, f"模型仓库不完整：{codes} 不存在"
    if not weights.is_file():
        return False, f"缺少预训练权重：{weights} 不存在"
    return True, "模型仓库就绪"


def check_torch_cuda() -> Tuple[bool, str]:
    """
    检查 torch 与 CUDA 可用性。
    Returns: (available, message)
    """
    try:
        import torch
    except ImportError:
        return False, "PyTorch 未安装"
    
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
        return True, f"CUDA 可用：{name} ({mem:.1f} GB)"
    else:
        return False, f"PyTorch 已安装 ({torch.__version__}) 但无 CUDA，将回退到 CPU 推理"


# ═══════════════════════════════════════════════════════════════════
#  编码器归一化 / 质量轴解析
#
#  与 vidcrop_cpu_v2.py 的同名函数逐字对应（孪生约定），差异只有一处、且是有意的：
#  本脚本没有 --fallback-policy 的逐策略降级链（默认编码器就是 CPU 侧的 libx265），
#  故 _resolve_quality_params() 不需要 src_codec 参数，`--cq`/`--qp` 的量纲一律按
#  默认硬件编码器 h264_nvenc 解释。
# ═══════════════════════════════════════════════════════════════════
def normalize_codec_name(codec: str) -> str:
    """将常见编码器别名归一化为 FFmpeg 标准名称。"""
    if codec in ("auto", "copy"):
        return codec
    lower = codec.lower()
    normalized = CODEC_ALIASES.get(lower, lower)
    if normalized != lower:
        print(f"提示：编码器名称 '{codec}' 已归一化为 '{normalized}'")
    elif normalized != codec:
        print(f"提示：编码器名称 '{codec}' 已转为小写 '{normalized}'")
    return normalized


def default_preset_for(codec: str) -> str:
    """按编码器类型给出默认预设：GPU 硬件编码器 p4，libsvtav1 固定 8，其余 medium。"""
    c = codec.lower()
    if c == "libsvtav1":
        return DEFAULT_PRESET_SVTAV1
    return DEFAULT_PRESET_GPU if c in CQ_SUPPORTED_CODECS else DEFAULT_PRESET_CPU


def normalize_preset(preset: str, target_codec: str) -> str:
    """在 NVENC (p1~p7) 与 libx264 风格 (ultrafast~veryslow) 之间自动双向映射。

    libsvtav1 另走一套：-preset 是 0~13 的整数，名字类取值一律先换算成整数。
    """
    if target_codec == "libsvtav1":
        p = preset.strip().lower()
        if p.lstrip("-").isdigit():
            return str(max(0, min(13, int(p))))
        mapped = NVENC_TO_SVTAV1_PRESET.get(p) or X264_TO_SVTAV1_PRESET.get(p)
        if mapped is not None:
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}' (libsvtav1 使用 0~13 整数 preset)。")
            return str(mapped)
        print(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，"
              f"使用默认 '{DEFAULT_PRESET_SVTAV1}'。")
        return DEFAULT_PRESET_SVTAV1

    if target_codec in NVENC_CODECS:
        if preset.startswith("p") and preset[1:].isdigit():
            return preset
        if preset in X264_TO_NVENC_PRESET:
            mapped = X264_TO_NVENC_PRESET[preset]
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}' ({target_codec} 使用 NVENC 风格 preset)。")
            return mapped
        print(f"  提示：--preset '{preset}' 在 {target_codec} 下无对应，"
              f"使用默认 '{DEFAULT_PRESET_GPU}'。")
        return DEFAULT_PRESET_GPU

    if target_codec in ("libx264", "libx265") or target_codec in _QSV_CODECS:
        if preset.startswith("p") and preset[1:].isdigit():
            mapped = NVENC_TO_X264_PRESET.get(preset, DEFAULT_PRESET_CPU)
            print(f"  提示：--preset '{preset}' 已映射为 '{mapped}' ({target_codec} 使用 libx264 风格 preset)。")
            return mapped
        return preset

    return preset


def encoder_supports_preset(codec: str) -> bool:
    return codec.lower() in PRESET_SUPPORTED_CODECS


def encoder_supports_crf(codec: str) -> bool:
    return codec.lower() in CRF_SUPPORTED_CODECS


def encoder_supports_cq(codec: str) -> bool:
    return codec.lower() in CQ_SUPPORTED_CODECS


def encoder_supports_qp(codec: str) -> bool:
    """编码器是否只认 `-qp`（VAAPI 族；与 NVENC 的 `-qp` 不是同一套选项体系）。"""
    return codec.lower() in _QP_ONLY_CODECS


# 运行时缓存：编码器 -> {option: (lo, hi)}
_FFMPEG_RANGE_CACHE: Dict[str, Dict[str, Tuple[int, int]]] = {}


def _query_ffmpeg_range(encoder: str, option: str) -> Optional[Tuple[int, int]]:
    """运行时查询 ffmpeg 对给定编码器指定选项（-crf/-cq/-qp）的实际可用量程。

    解析 `ffmpeg -h encoder=xxx` 里的 "(from X to Y)" / "(X to Y)"；失败返回 None，
    由调用方回退静态表。
    """
    cache = _FFMPEG_RANGE_CACHE.setdefault(encoder, {})
    if option in cache:
        return cache[option]

    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", f"encoder={encoder}"],
            capture_output=True, text=True, timeout=5, check=False,
            env=_ffmpeg_env(),
        ).stdout
        pattern = rf"^\s+-{re.escape(option)}\b.*?\((?:from\s+)?([-\d]+)\s+to\s+([-\d]+)\)"
        m = re.search(pattern, out, re.MULTILINE)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            cache[option] = (lo, hi)
            return (lo, hi)
    except Exception:
        pass
    return None


def literal_range(codec: str, kind: str = "crf") -> Tuple[int, int]:
    """字面量质量参数在给定编码器下的**实际可用量程** ``(lo, hi)``。

    实测 `--cq 60` 会让 ffmpeg 报 `out of range [0 - 51]` 直接失败；`--crf 60` 则被
    libx264/libx265 **静默按 51 编码**（最差质量、无提示）。故 CLI 层按生效编码器校验。
    """
    c = (codec or "").lower()
    if kind == "cq":
        key = c if encoder_supports_cq(c) else "h264_nvenc"
        option = "cq"
    else:
        key = c if encoder_supports_crf(c) else "libx264"
        option = "crf"

    runtime = _query_ffmpeg_range(key, option)
    if runtime is not None:
        return runtime

    _qm = get_quality_map()
    m = _qm.get(key) or _qm["h264_nvenc" if kind == "cq" else "libx264"]
    return int(m[2]), int(m[3])


def qp_scale(codec: str) -> int:
    """该编码器 `-qp` 相对 x264 QP 的尺度（AV1 的 qindex ≈ 4×QP，其余为 1）。"""
    return _QP_SCALE.get((codec or "").lower(), 1)


def qp_limits(codec: str) -> Tuple[int, int]:
    """该编码器 `-qp` 的实测量程（可能与 CQ 轴量程不同）。"""
    return _QP_LIMITS.get((codec or "").lower(), (0, 51))


def qp_range(codec: str) -> Tuple[int, int]:
    """CLI 层 `--qp` 的可用量程：按生效编码器取（AV1 族 0~255、VAAPI 0~52、其余 0~51）。"""
    return qp_limits(codec)


def to_constqp_qp(codec: str, value: int) -> int:
    """CQ 轴（targetQuality）的值 → 该编码器 CONSTQP 的 `-qp`。

    value --to_x264_crf--> 基准轴 --(仿射/×尺度)--> -qp（再钳到 _QP_LIMITS）。
    `value == 0` 是**无损档哨兵**：显式返回 0，不参与线性换算（否则 AV1 族会因表
    `b < 0` 算出非 0）。
    """
    c = (codec or "").lower()
    if value == 0:
        return 0
    ref = to_x264_crf(c, value)
    if ref is None:
        return int(value)
    lo, hi = qp_limits(c)
    aff = _QP_AFFINE_QUALITY.get(c) if get_quality_mode() == "quality" else None
    if aff is not None:
        qp = aff[0] * float(ref) + aff[1]
    else:
        qp = float(int(round(ref)) * qp_scale(c))
    return int(max(lo, min(hi, int(round(qp)))))


def from_constqp_qp(codec: str, qp: int) -> float:
    """`-qp` → 基准轴 libx264 CRF（`to_constqp_qp` 的反向，供降级换算用）。"""
    c = (codec or "").lower()
    aff = _QP_AFFINE_QUALITY.get(c) if get_quality_mode() == "quality" else None
    ref = (float(qp) - aff[1]) / aff[0] if aff is not None else float(qp) / qp_scale(codec)
    return max(0.0, min(51.0, ref))


def quality_for_codec(args: argparse.Namespace, codec: str
                      ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """按用户**原始**质量输入，为目标编码器重算 (crf, cq, qp)。

    运行中降级（NVENC 失败 → libx265）时调用。不重算的话 `--codec hevc_nvenc --cq 26`
    降级后会带着 cq=26 落到 libx265 —— 后者既不认 `-cq`、`st.crf` 又是 None，
    结果**一个质量选项都不下发**、退回 x265 自带默认 CRF 28（质量静默回退）。
    """
    o = getattr(args, "quality_originals", None) or {}
    return _resolve_quality_params(
        codec, o.get("crf"), o.get("cq"), crf_ref=o.get("crf_ref"),
        cq_ref=o.get("cq_ref"), qp=o.get("qp"), rc_mode=args.rc_mode,
        src_codec=o.get("requested"))


def default_quality_for(codec: str) -> int:
    """未给质量参数时，按统一基准 DEFAULT_REF 换算到目标编码器的原生刻度。"""
    v = from_x264_crf(codec, DEFAULT_REF)
    if v is None:
        return DEFAULT_REF
    return max(1, int(round(v)))


def cq_to_crf(cq: int, target_codec: str, src_codec: str = "h264_nvenc") -> int:
    """硬件编码器的 CQ → 目标软件编码器的等效 CRF（走 convert_crf 的统一换算表）。"""
    v = convert_quality(src_codec, cq, target_codec)
    return int(round(v)) if v is not None else int(cq)


def crf_to_cq(crf: int, target_codec: str, src_codec: str = "libx264") -> int:
    """libx264 CRF → 目标硬件编码器的等效 CQ/QP（`cq_to_crf` 的反向，同一张表）。

    ⚠ 调用方负责把结果钳到 ≥1：目标编码器的 0 是**特殊档**（CPU 编码器上逐位无损、
    NVENC 上只是最高质量档），不是"最高质量"，线性表在低端会把小值算成 0 而意外命中。
    """
    v = convert_quality(src_codec, crf, target_codec)
    return int(round(v)) if v is not None else int(crf)


def crf_to_rav1e_qp(crf: int) -> int:
    """基准轴（libx264 CRF）→ librav1e 的 ``-qp``（0~255），直接查当前生效表。

    ⚠ `-speed` 会整体平移 rav1e 的码率曲线，故本值只对已声明的 speed 档成立；本仓
    默认 native 档（`_RAV1E_SPEED = 0`，不下发 `-speed`），表值与之配套。
    """
    v = from_x264_crf("librav1e", crf)
    if v is None:
        v = from_x264_crf("librav1e", DEFAULT_REF) or 0.0
    return max(0, min(255, int(round(v))))


def _resolve_quality_params(
    codec: str,
    user_crf: Optional[int],
    user_cq: Optional[int],
    crf_ref: Optional[int] = None,
    cq_ref: Optional[int] = None,
    qp: Optional[int] = None,
    rc_mode: str = "auto",
    src_codec: Optional[str] = None,
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    根据编码器类型确定最终 **(crf, cq, qp)** 三元组，处理参数不匹配与边界。

    与 vidcrop_cpu_v2.py 的同名函数逐字对应（孪生约定）。三种取值方式（由调用方保证
    互斥，混用会被拒绝执行）：
      1. --crf / --cq：字面量原样下发；只有"落到不支持该量纲的编码器"时才按等效表换算。
      2. --crf-ref / --cq-ref：统一基准轴 —— 先归一到 libx264 CRF，再换算到目标编码器；
         目标处于 `--rc-mode constqp` 时结果落到 `-qp`。
      3. --qp（只在 constqp 下有效）：目标支持 -qp 就透传，落到 CPU 编码器时换算为 -crf。

    ⚠ **0 是特殊档哨兵，不参与线性换算**：各编码器的 0 都是"极值档"（libx265 还要配
    `lossless=1`、VP9 要配 `-b:v 0`），而线性表会把 0 当普通下界 —— 后果是 cq 0~5 被
    算成同一个"接近无损"值，甚至**意外命中目标编码器的 0**。
    ⚠ 但别把它一律叫"真无损"：本机实测 libx265/libx264 的 `-crf 0` 是逐位无损，而
    T4 实测 **NVENC 的 `-rc constqp -qp 0` 不是**。非 0 换算的结果**统一钳到 ≥1**。
    """
    # 用户 `--cq` 的量纲轴 = **用户请求的编码器**（若它认 -cq），否则回退 h264_nvenc。
    # ⚠ 不能硬编码 h264_nvenc：`--codec hevc_nvenc --cq 28` 在无卡机器上会先降级到
    # libx265，若按 h264 轴换算会比 hevc 轴偏 3 档（size 口径实测 cq23/26/28/30
    # 差 2/3/3/3 档），且提示还会写成「（h264_nvenc 量纲）」自相矛盾。
    # 与 vidcrop_hwaccel.py 的同名逻辑一致（那边由调用方传 src_codec=codec）。
    _src = src_codec if (src_codec and encoder_supports_cq(src_codec)) else "h264_nvenc"

    # ── VAAPI 等"只认 -qp"的编码器：所有质量输入先归一到基准轴，再经 -qp 下发 ──
    if codec in _QP_ONLY_CODECS:
        _zero_qp = next((_n for _n, _v in (("--crf", user_crf), ("--cq", user_cq),
                                           ("--qp", qp), ("--crf-ref", crf_ref),
                                           ("--cq-ref", cq_ref)) if _v == 0), None)
        if qp is not None:
            _qp_val = qp
        elif user_crf is not None:
            _qp_val = int(round(to_x264_crf("libx264", user_crf) or 0))
        elif user_cq is not None:
            _qp_val = int(round(to_x264_crf(_src, user_cq) or 0))
        elif crf_ref is not None:
            _qp_val = int(crf_ref)
        elif cq_ref is not None:
            _qp_val = int(round(to_x264_crf("h264_nvenc", cq_ref) or 0))
        else:
            _qp_val = DEFAULT_REF
        _lo, _hi = qp_limits(codec)
        _qp_val = max(_lo, min(_hi, _qp_val))
        if _zero_qp is not None:
            print(f"  提示：{_zero_qp}=0 → 编码器 {codec} 的 -qp 0"
                  f"（最高质量档，非逐位无损）。")
        else:
            print(f"  提示：编码器 {codec} 只认 -qp（无 -crf/-cq），"
                  f"质量已归一到基准轴 → -qp {_qp_val}。")
        return None, None, _qp_val

    # ── [LOSSLESS] 0 档：跳过换算，直接给目标编码器的 0 档 ──────────────
    _zero = next((_n for _n, _v in (("--crf", user_crf), ("--cq", user_cq),
                                    ("--qp", qp), ("--crf-ref", crf_ref),
                                    ("--cq-ref", cq_ref)) if _v == 0), None)
    if _zero is not None:
        if codec in NVENC_CODECS:
            if rc_mode == "constqp":
                return None, None, 0
            return None, 0, None
        if encoder_supports_crf(codec) or codec == "librav1e":
            print(f"  提示：{_zero}=0 是无损请求，已按编码器 {codec} 的 0 档下发。")
            return 0, None, None
        print(f"  警告：{_zero}=0 是无损请求，但编码器 {codec} 没有对应的 0 档，改用默认质量。")
        return (None, default_quality_for(codec), None) if encoder_supports_cq(codec) \
            else (default_quality_for(codec), None, None)

    # ── librav1e：只有 -qp（0~255）────────────────────────────────────────
    if codec == "librav1e":
        if qp is not None:
            _ref = from_constqp_qp(_src, qp)
        elif user_crf is not None:
            _ref = float(user_crf)
        elif user_cq is not None:
            _ref = to_x264_crf(_src, user_cq)
        elif crf_ref is not None:
            _ref = float(crf_ref)
        elif cq_ref is not None:
            _ref = to_x264_crf("h264_nvenc", cq_ref)
        else:
            _ref = float(DEFAULT_REF)
        if _ref is None:
            _ref = float(DEFAULT_REF)
        _out = int(round(_ref))
        _qp = crf_to_rav1e_qp(_out)
        print(f"  提示：librav1e 无 -crf，已按基准轴换算为 -qp {_qp}"
              f"（{'speed ' + str(_RAV1E_SPEED) if _RAV1E_SPEED > 0 else 'native 档'}口径）。")
        return _out, None, None

    # ── 方式 3：--qp（constqp 的恒定 QP）─────────────────────────────────
    if qp is not None:
        if codec in NVENC_CODECS:
            return None, None, qp
        if encoder_supports_crf(codec):
            _ref = from_constqp_qp(_src, qp)
            _v = from_x264_crf(codec, _ref)
            mapped_crf = max(1, int(round(_v))) if _v is not None else int(qp)
            print(f"  提示：编码器 {codec} 不支持 -qp，"
                  f"已将 --qp {qp}（{_src} QP 基准）映射为 -crf {mapped_crf}（等效视觉质量）。")
            return mapped_crf, None, None
        print(f"  警告：编码器 {codec} 既没有 -qp 也没有 -crf，--qp {qp} 无法换算，改用默认质量。")
        return (None, default_quality_for(codec), None) if encoder_supports_cq(codec) \
            else (default_quality_for(codec), None, None)

    # ── 方式 2：统一基准轴换算 ────────────────────────────────────────────
    if crf_ref is not None or cq_ref is not None:
        if crf_ref is not None:
            ref_x264: Optional[float] = float(crf_ref)
            ref_desc = f"--crf-ref {crf_ref}（libx264 CRF 基准）"
        else:
            ref_x264 = to_x264_crf("h264_nvenc", cq_ref)
            ref_desc = f"--cq-ref {cq_ref}（h264_nvenc CQ 基准）"
            if ref_x264 is None:
                ref_x264 = float(cq_ref or 0)
        _v2 = from_x264_crf(codec, ref_x264)
        if _v2 is None:
            print(f"  警告：{codec} 不在等效换算表中，{ref_desc} 无法换算，改用默认质量。")
            return (None, default_quality_for(codec), None) if encoder_supports_cq(codec) \
                else (default_quality_for(codec), None, None)
        _val = max(1, int(round(_v2)))
        if encoder_supports_cq(codec):
            if rc_mode == "constqp":
                _qp = to_constqp_qp(codec, _val)
                print(f"  提示：{ref_desc} → {codec} 的 -qp {_qp}（rc-mode constqp）。")
                return None, None, _qp
            print(f"  提示：{ref_desc} → {codec} 的 -cq {_val}。")
            return None, _val, None
        print(f"  提示：{ref_desc} → {codec} 的 -crf {_val}。")
        return _val, None, None

    # ── 方式 1：字面量原样下发 ────────────────────────────────────────────
    if encoder_supports_cq(codec):
        if user_cq is not None:
            _lo, _hi = literal_range(codec, "cq")
            return None, max(_lo, min(_hi, user_cq)), None
        if user_crf is not None:
            mapped = max(1, crf_to_cq(user_crf, codec))
            if rc_mode == "constqp":
                _qp = to_constqp_qp(codec, mapped)
                print(f"  提示：编码器 {codec} 不支持 -crf，已将 --crf {user_crf}"
                      f"（libx264 CRF 量纲）映射为 -qp {_qp}（rc-mode constqp）。")
                return None, None, _qp
            print(f"  提示：编码器 {codec} 不支持 -crf，已将 --crf {user_crf}"
                  f"（libx264 CRF 量纲）映射为 -cq {mapped}（等效视觉质量）。")
            return None, mapped, None
        if rc_mode == "constqp":
            return None, None, to_constqp_qp(codec, default_quality_for(codec))
        return None, default_quality_for(codec), None

    if encoder_supports_crf(codec):
        if user_crf is not None:
            _lo, _hi = literal_range(codec, "crf")
            return max(_lo, min(_hi, user_crf)), None, None
        if user_cq is not None:
            mapped_crf = max(1, cq_to_crf(user_cq, codec, _src))
            print(f"  提示：编码器 {codec} 不支持 -cq，"
                  f"已将 --cq {user_cq}（{_src} 量纲）映射为 -crf {mapped_crf}（等效视觉质量）。")
            return mapped_crf, None, None
        return default_quality_for(codec), None, None

    _asked = next((n for n, v in (("--crf", user_crf), ("--cq", user_cq),
                                  ("--crf-ref", crf_ref), ("--cq-ref", cq_ref),
                                  ("--qp", qp)) if v is not None), None)
    if _asked is not None:
        print(f"  警告：编码器 {codec} 没有可用的质量参数（-crf/-cq/-qp 均不适用），"
              f"{_asked} 无法下发，已忽略。")
    return None, None, None


def apply_rc_control_args(codec: str,
                          rc_mode: str = "auto",
                          qp: Optional[int] = None,
                          lookahead: Optional[int] = None,
                          warn: Optional[Callable[[str], None]] = None,
                          tune: Optional[str] = None,
                          multipass: Optional[str] = None,
                          bitrate: Optional[str] = None,
                          aq: bool = False,
                          ) -> Tuple[List[str], List[str]]:
    """把 --rc-mode / --qp / --lookahead / --nvenc-tune / --nvenc-multipass / --nvenc-aq
    落到 ffmpeg 参数上（与 vidcrop_cpu_v2.py 的同名函数对应，另加 --nvenc-aq）。

    Returns:
        (args, x265_params)
        · args       直接追加进命令的选项（-rc / -qp / -rc-lookahead / -lag-in-frames /
                     -spatial-aq / -temporal-aq / -multipass / -tune）；
        · x265_params **必须由调用方合并进同一条 -x265-params**（libx265 的 lookahead
          只能这样传）。实测两次 -x265-params 是"后者整条覆盖前者"，而 HDR 静态元数据
          也走这条选项，各发一条会让后发的那条**静默抹掉 HDR 元数据**。

    取值与生效范围：
      · -rc / -qp：只有 NVENC 认；非 NVENC → 忽略并告知。
      · lookahead：libx264 → -rc-lookahead；NVENC → -rc-lookahead（constqp 下硬件
        静默禁用，不下发）；libx265 → 写进 -x265-params rc-lookahead=；
        libvpx/libaom → -lag-in-frames；其余键名未实测 → 不下发并告知。
      · nvenc-aq：仅 NVENC 族下发 `-spatial-aq 1 -temporal-aq 1`（非 NVENC 只告警）。
      · multipass：rc_mode==cbr 或给了 --bitrate 时自动补 fullres（显式值优先）；
        constqp 无码率目标，驱动会忽略。
    """
    c = (codec or "").lower()
    args: List[str] = []
    x265_params: List[str] = []
    default_rc = _NVENC_DEFAULT_RC.get(c)

    def _ignore(what: str, why: str) -> None:
        if warn:
            warn(f"{what} 未生效（{why}），已忽略")

    _want_rc = rc_mode != "auto" or qp is not None or default_rc is not None
    if _want_rc:
        if c in NVENC_CODECS:
            if qp == 0:
                # 无损：必须**显式进 constqp**，否则 -qp 在 VBR 下毫无意义
                args += ["-rc", "constqp", "-qp", "0", "-b:v", "0"]
            else:
                if rc_mode != "auto":
                    args += ["-rc", rc_mode]
                elif default_rc is not None:
                    args += ["-rc", default_rc]
                if qp is not None:
                    args += ["-qp", str(qp)]
        elif c in _QP_ONLY_CODECS:
            if rc_mode != "auto":
                _ignore(f"--rc-mode {rc_mode}",
                        f"-rc 是 NVENC 专属选项，编码器 {c} 没有这个开关")
        elif rc_mode != "auto":
            _ignore(f"--rc-mode {rc_mode}",
                    f"-rc 是 NVENC 专属选项，编码器 {c} 没有这个开关")
        else:
            _ignore(f"--qp {qp}",
                    f"-qp 是 NVENC 专属选项，编码器 {c} 没有这个开关")

    if lookahead is not None:
        if c in NVENC_CODECS:
            if rc_mode == "constqp":
                _ignore(f"--lookahead {lookahead}",
                        "-rc constqp 下 NVENC 静默禁用 lookahead，未下发")
            else:
                args += ["-rc-lookahead", str(lookahead)]
        elif c == "libx264":
            args += ["-rc-lookahead", str(lookahead)]
        elif c == "libx265":
            x265_params.append(f"rc-lookahead={lookahead}")
        elif c in ("libvpx", "libvpx-vp9", "libaom-av1"):
            args += ["-lag-in-frames", str(lookahead)]
        else:
            _ignore(f"--lookahead {lookahead}",
                    f"编码器 {c} 的 lookahead 选项名未经实测，不代为下发")

    # ── NVENC 自适应量化（--nvenc-aq）──
    # 实测语义：`-spatial-aq 1` + `-temporal-aq 1` 同时开，能改善平坦区域的
    # 块效应（HDR 高光/暗部尤其明显）。仅 NVENC 族认这两个选项。
    if aq:
        if c in NVENC_CODECS:
            args += ["-spatial-aq", "1", "-temporal-aq", "1"]
        else:
            _ignore("--nvenc-aq", f"它只对 *_nvenc 编码器生效，编码器 {c} 没有这两个选项")

    # ── NVENC 调优：--nvenc-tune / --nvenc-multipass ──
    _mp = multipass
    if _mp is None and c in NVENC_CODECS and (rc_mode == "cbr" or bitrate):
        _mp = "fullres"
    if _mp is not None:
        if c not in NVENC_CODECS:
            _ignore(f"--nvenc-multipass {_mp}",
                    f"-multipass 是 NVENC 专属选项，编码器 {c} 没有这个开关")
        elif rc_mode == "constqp" or qp == 0:
            _ignore(f"--nvenc-multipass {_mp}",
                    "-rc constqp 无码率目标，NVENC 会忽略 multipass")
        else:
            args += ["-multipass", _mp]

    if tune is not None:
        if c not in NVENC_CODECS:
            _ignore(f"--nvenc-tune {tune}",
                    f"-tune 是 NVENC 专属选项，编码器 {c} 没有这个开关")
        elif tune == "uhq" and c not in _NVENC_UHQ_CODECS:
            _ignore("--nvenc-tune uhq",
                    f"{c} 没有 uhq 档（仅 hevc / av1 NVENC 有），已忽略")
        else:
            args += ["-tune", tune]

    return args, x265_params


def parse_rc_mode(spec: Optional[str]) -> str:
    """解析 --rc-mode → 'auto' | 'constqp' | 'vbr' | 'cbr'（与 cpu_v2 逐字对应）。

    本轴只有 NVENC 一个后端（`-rc` 是 NVENC 专属），故裸名不歧义、一律接受；且
    允许显式写 auto（它就是默认值），避免重演"帮助里写的默认值敲不出来"那个坑。
    """
    if not spec:
        return "auto"
    s = spec.strip().lower()
    head, sep, tail = s.partition("-")
    if sep:
        if head != _RC_BACKEND:
            raise ValueError(
                f"--rc-mode '{spec}' 无效：未知后端前缀 '{head}-'"
                f"（本轴只有 {_RC_BACKEND}- —— -rc 是 NVENC 专属选项）。\n"
                f"  可用值：auto，或 <mode>（也可写 {_RC_BACKEND}-<mode>）。\n  {_RC_MODE_HELP}")
        s = tail
    if not s:
        raise ValueError(
            f"--rc-mode '{spec}' 无效：前缀 '{_RC_BACKEND}-' 后面缺少模式名。\n"
            f"  可用值：auto，或 <mode>（也可写 {_RC_BACKEND}-<mode>）。\n  {_RC_MODE_HELP}")
    if s == "auto" or s in _RC_MODES:
        return s
    raise ValueError(
        f"--rc-mode '{spec}' 无效：NVENC 没有模式 '{s}'。\n"
        f"  可用值：auto，或 <mode>（也可写 {_RC_BACKEND}-<mode>）。\n  {_RC_MODE_HELP}")


def parse_bitrate(spec: Optional[str]) -> Optional[str]:
    """解析/校验 --bitrate（ffmpeg 记法：8M / 8000k / 12000000，裸数字按 bps）。"""
    if not spec:
        return None
    s = str(spec).strip()
    if not _BITRATE_RE.match(s):
        raise ValueError(
            f"--bitrate '{spec}' 无效：需要码率写法，如 8M / 8000k / 12000000"
            f"（裸数字按 bps 解释）。")
    return s


def check_int_range(value: int, opt: str, rng: Tuple[int, int], why: str) -> int:
    """校验整数量程（--lookahead / --qp 共用）；越界抛 ValueError。"""
    lo, hi = rng
    if not (lo <= value <= hi):
        raise ValueError(
            f"{opt} 超出范围：需要 {lo}~{hi} 的整数（{why}），收到 {value}。")
    return value


def _source_audio_codecs(src: Path) -> List[str]:
    """取源文件的音频编码名列表（探测失败返回空列表）。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "json", str(src)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, check=False, env=_ffmpeg_env())
        return [s.get("codec_name")
                for s in (json.loads(r.stdout or "{}").get("streams") or [])
                if s.get("codec_name")]
    except Exception:
        return []


def _has_libass() -> bool:
    """本机 ffmpeg 是否有 libass（`subtitles`/`ass` 滤镜）—— burn 的前提。"""
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-filters"],
                       capture_output=True, text=True, timeout=30, check=False)
        return ("subtitles" in r.stdout) and (" ass " in f" {r.stdout} ")
    except Exception:
        return False


def _probe_json(path: Path, extra: Optional[List[str]] = None) -> Dict:
    """一次 ffprobe 拿 JSON；失败返回 {}（不抛，调用方自己判空）。

    与 `probe_video_summary` 同风格（本脚本原先没有通用封装，各处直接
    `json.loads(r.stdout)`）。字幕导出要先知道源字幕轨的真实 codec（决定导成
    .ass 还是 .srt），才需要这个「只取一条流一个字段」的轻量查询。
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json"] + (extra or []) \
        + [str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=60, check=False)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def _export_subtitle(src: Path, index: int,
                     work_sub_dir: Path, tag: str) -> Path:
    """把源里第 index 条字幕导出成文件，供 libass 渲染（burn 用）。

    为什么必须先导出：libass 的 `subtitles=` 滤镜吃的是**文件路径**，不吃容器内嵌流。
    ⚠ 格式保持原样（subrip→.srt、ass→.ass），**不要**一律导成 srt ——
    ass 带样式（字体/颜色/定位），导成 srt 会全部丢失（实测 libass 读 srt 时
    只能用它的默认样式）。
    """
    work_sub_dir.mkdir(parents=True, exist_ok=True)
    # 先探一次真实 codec 决定扩展名（ass 的样式不能降级成 srt）
    probe = _probe_json(src, ["-select_streams", f"s:{index}",
                              "-show_entries", "stream=codec_name"])
    st_ = (probe.get("streams") or [{}])[0]
    codec = (st_.get("codec_name") or "").lower()
    ext = ".ass" if codec == "ass" else ".srt"
    out = work_sub_dir / f"{tag}_s{index}{ext}"
    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass
    # -y覆盖、-nostdin 避免无终端时卡住；失败会抛 ValueError（带原因）
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-y", "-i", str(src), "-map", f"0:s:{index}", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, check=False)
    if r.returncode != 0 or not out.exists():
        raise ValueError(
            f"导出字幕（index={index}）失败，rc={r.returncode}："
            f"{(r.stderr or '').strip()[:200]}")
    # ⚠ **必须查非空**：mkv 的字幕轨是「稀疏」的—— 某段时间内没有字幕事件时，
    # 导出**rc=0 但文件 0 字节**（实测：截取 300~310s 的 10s 片段，那段无字幕 ⇒
    # 「Output file is empty, nothing was encoded」）。只判rc+exists 会把它当成功，
    # 于是 libass 拿到空文件、编码端报 "Unable to open …srt" ⇒ 整条转换失败，
    # 而真正的信息（这段没字幕）被埋在三层报错之下。
    if out.stat().st_size <= 0:
        raise ValueError(
            f"源字幕轨 index={index} 在本片段时间内**没有任何字幕事件**"
            f"（导出得到 0 字节文件）。\n"
            f"    · 换一条轨：`--subs-index N`（用 --env 或 ffprobe 看有哪些轨）；\n"
            f"    · 或用 `--subs keep`（不烧录、只透传，不受此限）。")
    return out


def resolve_audio_codec_for_container(audio_codec: str, container_ext: str,
                                      src: Optional[Path] = None,
                                      warn: Optional[Callable[[str], None]] = None) -> str:
    """按目标容器修正音频编码方式（与 vidcrop_cpu_v2.py 的同名函数同语义）。

    只有 WebM 需要干预：它只接受 Vorbis / Opus 音轨，而绝大多数片源是 AAC ——
    实测 `-c:a copy` 写 .webm 直接失败：
      "Only VP8 or VP9 or AV1 video and Vorbis or Opus audio and WebVTT subtitles
       are supported for WebM."
    （本常量 `_WEBM_AUDIO_CODECS` / `_WEBM_AUDIO_FALLBACK` 早已定义，却在移植时
    漏了消费者 —— 2026-10-08 对抗审查发现的死常量。）
    """
    if (container_ext or "").lower() != ".webm":
        return audio_codec

    c = (audio_codec or "copy").lower()
    if c == "none":
        return "none"
    if c == "copy":
        srcs = _source_audio_codecs(Path(src)) if src is not None else []
        if srcs and all(s.lower() in _WEBM_AUDIO_CODECS for s in srcs):
            return audio_codec
        if warn:
            warn("WebM 只支持 Opus/Vorbis 音轨"
                 + (f"，源音轨为 {' / '.join(srcs)}" if srcs else "（无法确认源音轨）")
                 + f"，已改用 {_WEBM_AUDIO_FALLBACK} 重编码")
        return _WEBM_AUDIO_FALLBACK

    base = c.split("_")[-1] if c.startswith("lib") else c
    if base not in _WEBM_AUDIO_CODECS:
        if warn:
            warn(f"WebM 只支持 Opus/Vorbis 音轨，--audio-codec {audio_codec} 不适用，"
                 f"已改用 {_WEBM_AUDIO_FALLBACK}")
        return _WEBM_AUDIO_FALLBACK
    return audio_codec


def resolve_subtitle_codec_for_container(container_ext: str,
                                         src_codec: str,
                                         src: Optional[Path] = None,
                                         warn: Optional[Callable[[str], None]] = None
                                         ) -> Optional[str]:
    """按目标容器决定字幕编码（`--subs keep` 用）。返回 None 表示「直接 copy」。

    与 `resolve_audio_codec_for_container` 同构的分派，但**多一层位图字幕判断**：
    位图字幕（PGS/DVD/DVB teletext/xsub）无法转码成文本容器格式（mov_text/webvtt），
    只能原样 copy —— 而 mkv 之外没有容器收它⇒ 这种情况必须报错而不是静默降级。

    实测依据见 `_MP4_SUB_FALLBACK` 上方的矩阵注释。
    """
    ext = (container_ext or "").lower()
    src_codec = (src_codec or "").lower()

    if src_codec in _BITMAP_SUBS:
        # 位图字幕：只有 mkv 能原样带走，其它容器一律做不到。
        if ext in _SUBS_DIRECT_CONTAINERS:
            return None                      # copy
        if warn:
            warn(f"源字幕是位图格式（{src_codec}），无法转成 {_MP4_SUB_FALLBACK}"
                 f"/{_WEBM_SUB_FALLBACK}；且{ext} 容器不接受它。\n"
                 f"    · 请输出到 .mkv 以原样保留，或改用 --subs burn"
                 f"（位图字幕**不能**烧录，libass 只认文本字幕）、或 --subs none")
        return None                          # 交给上层报错/跳过

    if ext in _SUBS_DIRECT_CONTAINERS:
        return None                          # mkv：copy 最稳，不转码

    if ext in _MP4_FAMILY:
        #⚠ **不能**因为「源已经是 mov_text」就返回 None（copy）——实测那样会失败：
        #   `-c:s copy` 到 mp4 ⇒ rc=234（copy 不做容器适配）；
        #   完全不给 `-c:s` ⇒ rc=8「Automatic encoder selection failed」。
        #   必须**显式**下发 -c:s mov_text（源是 mov_text 时它是无损直通）。
        if src_codec and src_codec != "mov_text" and warn:
            warn(f"{ext} 不接受 {src_codec} 字幕，已转为 {_MP4_SUB_FALLBACK}")
        return _MP4_SUB_FALLBACK

    if ext == ".webm":
        if src_codec in _WEBM_SUB_CODECS:
            return None
        if warn:
            warn(f"WebM 只支持 WebVTT 字幕，{src_codec} 已转为 {_WEBM_SUB_FALLBACK}")
        return _WEBM_SUB_FALLBACK

    # 未知容器：copy（ffmpeg 会自己报错，比我们猜要诚实）
    return None


# ═══════════════════════════════════════════════════════════════════
#  硬件能力探测 / 自动降级
#
#  设计取向（本脚本与裁剪脚本的可比差异，由仓主 2026-10-08 拍板）：
#    · **默认编码器 libx265、默认解码 cpu** —— 即"不指定任何 GPU 参数时，行为与
#      引入 GPU 支持之前完全一致"，既有 verify/门禁不回归；
#    · 只有用户**显式** `--codec *_nvenc` / `--decode cuda|auto` 才会走硬件路径；
#    · 硬件不可用或运行中失败时，按 `--fallback-policy`（默认 auto）自动降级到
#      CPU 等价物并告警；strict 则直接报错（退出码 2）。
# ═══════════════════════════════════════════════════════════════════
_HW_PROBE_CACHE: Dict[str, bool] = {}
_HW_PROBE_LOCK = threading.Lock()

# 运行中降档重试表（**非孪生表**，仅 hwaccel 有；cpu_v2 不存在同名表）。
# 语义：NVENC 在 p5~p7 偶尔初始化失败/资源不足时，降一档到 p4 重试一次即可成功。
_NVENC_PRESET_RETRY = {"p5": "p4", "p6": "p4", "p7": "p4"}


def _probe_encoder(codec: str) -> bool:
    """用一次极小量的 lavfi 编码探测该编码器**真的可用**（编译进来 ≠ 能跑）。

    结果按编码器名缓存（同一次运行里只探一次）。探测命令失败即视为不可用，
    不区分"没这个编码器"与"驱动/显存问题"——两者对调用方的结论一致：降级。
    """
    with _HW_PROBE_LOCK:
        if codec in _HW_PROBE_CACHE:
            return _HW_PROBE_CACHE[codec]

    ok = False
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
             "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1:d=0.1",
             "-frames:v", "1", "-c:v", codec, "-f", "null", "-"],
            capture_output=True, text=True, timeout=30, check=False,
            env=_ffmpeg_env(),
        )
        ok = (r.returncode == 0)
    except Exception:
        ok = False

    with _HW_PROBE_LOCK:
        _HW_PROBE_CACHE[codec] = ok
    return ok


def _probe_decode_backend(backend: str, sample: Optional[Path] = None) -> bool:
    """探测硬件解码后端是否可用。

    `ffmpeg -hwaccels` 只列出**编译进来**的后端，不代表运行时可跑（无 GPU/驱动不匹配
    时照样列出 cuda）。所以真判据是**拿一个真实文件试着硬解 1 帧**：有 sample 就用它，
    没有就退化为"hwaccels 里有 + 设备节点存在"的弱判据。
    """
    if backend != "cuda":
        return False

    try:
        listed = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"],
                                capture_output=True, text=True, timeout=10,
                                check=False, env=_ffmpeg_env()).stdout
        if "cuda" not in (listed or "").lower():
            return False
    except Exception:
        return False

    if sample is not None and Path(sample).is_file():
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-hwaccel", "cuda", "-i", str(sample), "-frames:v", "1",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=60, check=False,
                env=_ffmpeg_env(),
            )
            return r.returncode == 0
        except Exception:
            return False

    # 弱判据：设备节点在就算"可能可用"（后续真跑失败仍会降级）
    return any(os.path.exists(p) for p in ("/dev/nvidia0", "/dev/nvidiactl"))


def is_8bit_only(codec: str) -> bool:
    return (codec or "").lower() in _ENCODERS_8BIT_ONLY


def is_h264_family(codec: str) -> bool:
    c = (codec or "").lower()
    return c == "libx264" or "h264" in c


def hdr10_capability(codec: str) -> Tuple[str, str]:
    """该编码器能否承载 HDR10 交付。返回 (level, message)，level ∈ ok/warn/error。

    · error —— 连 10bit 都做不了（_ENCODERS_8BIT_ONLY，如 h264_nvenc/libvpx）：
      PQ 是 10bit 传输函数，8bit 承载不出 HDR10，直接拒绝。
    · warn  —— 能做 10bit 但不是 HDR10 的交付格式（H.264 家族、VP9）：能跑，
      但播放器/交付链路一般不认，提示改用 hevc 系或 av1。
    · ok    —— hevc 系 / av1 系 / libx265。
    """
    c = (codec or "").lower()
    if is_8bit_only(c):
        return "error", (f"{c} 只支持 8bit 编码，而 SDR→HDR10 必须 10bit（PQ 传输函数）："
                         f"请改用 hevc_nvenc / av1_nvenc 或 libx265。")
    if is_h264_family(c):
        return "warn", (f"{c} 不是 HDR10 的交付格式（HDR10 标准封装是 HEVC Main10）："
                        f"能跑，但播放器/交付链路一般不认，建议改用 hevc_nvenc 或 libx265。")
    return "ok", ""


def resolve_codec(requested: str, policy: str,
                  warn: Optional[Callable[[str], None]] = None) -> str:
    """把请求的编码器解析成**实际可用**的编码器，必要时降级到 CPU 等价物。

    · 非硬件编码器：原样返回（不需要探测）。
    · *_nvenc：探测可用则原样；不可用则按 policy 降级到 libx265（档位经
      NVENC_TO_X264_PRESET 换算，保证降级前后速度档等效）或 strict 报错。
    """
    c = (requested or "").lower()
    if c not in _HW_ENCODERS:
        return c

    if _probe_encoder(c):
        return c

    if policy == "strict":
        raise ValueError(
            f"--codec {c} 在当前环境不可用（探测失败），且 --fallback-policy strict "
            f"不允许降级。\n  去掉 strict 或改用 libx265 / hevc_nvenc。")

    if c in NVENC_CODECS:
        if warn:
            warn(f"编码器 {c} 不可用（NVENC 探测失败，常见原因：无 NVIDIA GPU / "
                 f"驱动或 ffmpeg 未启用 nvenc），已自动降级为 libx265")
        return "libx265"
    if warn:
        warn(f"编码器 {c} 不可用，已自动降级为 libx265")
    return "libx265"


def resolve_decode_backend(requested: str, policy: str,
                           sample: Optional[Path] = None,
                           warn: Optional[Callable[[str], None]] = None) -> str:
    """把 --decode 解析成实际使用的解码后端（'cpu' 或 'cuda'）。

    默认 cpu（保守）：源是 SDR，解码在整条链里占比小（NN 推理才是瓶颈），而硬解
    输出必须 hwdownload 回 CPU 再转 gbrp16le（scale_cuda 无 16bit 白名单、
    hwdownload 无 p016le），多一趟显存↔内存拷贝。故只有显式要求才启用。
    """
    req = (requested or "cpu").lower()
    if req == "cpu":
        return "cpu"

    if _probe_decode_backend("cuda", sample=sample):
        return "cuda"

    if policy == "strict" and req == "cuda":
        raise ValueError(
            "--decode cuda 在当前环境不可用（探测失败），且 --fallback-policy strict "
            "不允许降级。\n  去掉 strict 或改用 --decode cpu。")
    if warn:
        if req == "cuda":
            warn("--decode cuda 不可用（无 CUDA 硬解能力），已回退为软件解码")
        else:
            warn("--decode auto 未探测到可用的 CUDA 硬解，使用软件解码")
    return "cpu"


# ═══════════════════════════════════════════════════════════════════
#  ffmpeg / ffprobe
# ═══════════════════════════════════════════════════════════════════
def _frac_to_float(value) -> Optional[float]:
    """ffprobe 有理数：'30000/1001' / [30000, 1001] / 30.0 → float。"""
    try:
        if isinstance(value, (list, tuple)):
            num, den = float(value[0]), float(value[1])
        else:
            s = str(value)
            if "/" in s:
                a, _, b = s.partition("/")
                num, den = float(a), float(b)
            else:
                num, den = float(s), 1.0
        return num / den if den else None
    except (TypeError, ValueError):
        return None


def probe_source(path: Path) -> Dict:
    """
    一次 ffprobe 拿到宽高/帧率/像素格式/色彩标签/音轨。

    Returns:
        {'width','height','fps','pix_fmt','src_bits','sar',
         'color_space','color_primaries','color_transfer','has_audio','duration'}
        探测失败抛 ValueError。
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=30, check=True)
        data = json.loads(r.stdout)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"ffprobe 失败：{(exc.stderr or '').strip() or exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe 输出无法解析为 JSON：{exc}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams
                  if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise ValueError(f"{path} 里没有视频流")

    fps = _frac_to_float(video.get("avg_frame_rate"))
    if not fps or fps <= 0:
        fps = _frac_to_float(video.get("r_frame_rate")) or DEFAULT_FPS

    src_bits = 8
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        v = video.get(key)
        if v:
            try:
                b = int(v)
                if b > 0:
                    src_bits = b
                    break
            except (TypeError, ValueError):
                pass
    if src_bits == 8:
        # h264/hevc 的 bits_per_raw_sample 常为空；按 pix_fmt 兜底判断
        pf = (video.get("pix_fmt") or "").lower()
        if pf and (pf.endswith("10le") or pf.endswith("10be")):
            src_bits = 10
        elif pf and (pf.endswith("12le") or pf.endswith("12be")):
            src_bits = 12

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    # 音频/视频流的 duration 是否缺失（2026-09-09，`--subs keep` 的两阶段判据）。
    #实测（ffmpeg 9.0.2，rawvideo 管道输入 + `-map 音频` + `-map 字幕` + `-shortest`）：
    #   mkv 源（v/a 的 duration=N/A、只有 format.duration=12s）⇒ 产出**畸形 mkv**
    #        （3688 字节、ffprobe 报 Duplicate element、视频帧读不出）
    #   mp4 源（v/a/s 的 duration 都有值）⇒ 正常（65821 字节 / 50 帧 / 三流齐全）
    # ⇒ 触发条件是**音频或视频流拿不到自己的 duration**，`-shortest` 无法比较各流
    #    时就会写坏容器。已在这种源上改走两阶段（先编码、再 remux 挂字幕）。
    _dur_known = True
    for _t in ("video", "audio"):
        _s = next((x for x in streams if x.get("codec_type") == _t), None)
        if _s is not None and (_frac_to_float(_s.get("duration")) or 0.0) <= 0:
            _dur_known = False
            break
    stream_durations_known = _dur_known
    # 音轨自身的时长（2026-10-09 加）。用于判断「音轨比视频短」——那是`-shortest`
    # 会截断视频的前提条件。容器/视频流时长都拿不到这个信息（容器时长取最长流，
    # 视频流时长只描述视频），所以必须单独探测音频流。
    audio_duration = 0.0
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        # ⚠ `_frac_to_float` 解析失败时返回 **None**（不是 0.0）。mkv 的字幕流
        # duration 常为 None，实测「带字幕的 mkv」会让下面的 `<= 0` 直接抛
        # TypeError（`'NoneType' <= 'int'`）⇒ 整条转换崩溃。
        # ⇒ 这里必须用 `or 0.0` 把 None 一并归零，而不是只挡 `<= 0`。
        audio_duration = (_frac_to_float(s.get("duration"))
                          or _frac_to_float((s.get("tags") or {}).get("DURATION"))
                          or 0.0)
        if audio_duration <= 0:
            # 部分容器（mkv/webm）不在 stream.duration 里给时长，回退读
            # format.duration 与视频流时长的差值：多数封装里音频比视频短一点，
            # 差值即音频长度。估不准只影响告警文案，不影响任何处理行为。
            fmt_dur = _frac_to_float((data.get("format") or {}).get("duration")) or 0.0
            vid_dur = _frac_to_float(video.get("duration")) or 0.0
            if fmt_dur > 0 and vid_dur > 0 and fmt_dur > vid_dur:
                audio_duration = vid_dur      # 保守：宁可认为不短
        break
    # ⚠ **duration 必须取视频流自己的，不能取容器值**（2026-10-09 修）。
    #
    # 缺陷实测（`/tmp/long_audio.mp4`：视频 5s/125 帧 + 音轨 20s）：
    #   format.duration = 20.0（被**最长流即音轨**决定）而 stream.duration = 5.0
    #   ⇒ 旧代码拿 20s 当视频时长，于是：
    #     ① 分段并行按 20s 切段，每段期望 20×25/2 = 250 帧，实际只有 ~62 帧
    #        ⇒ 触发 run_job 的截断检测，**整条转换判失败**（实测 EXIT=1
    #          「输出不完整：期望 250 帧、实际 125 帧」）；
    #     ② 分段边界按错误时长切，拼接后音画必然错位。
    #
    # 为什么此前没暴露：绝大多数素材音视频等长，容器值与视频流值一致
    # （实测 short_audio / equal_audio / test3 三者全部相同）。只有
    #「音轨明显长于视频」这种少见素材才会命中。
    #
    # 取值顺序：视频流 duration →（缺失时）nb_frames/fps →（再缺失时）容器 duration。
    # 最后那步兜底仍会被音轨污染，但那种素材本身就没有可靠时长可依，
    # 且nb_frames 通常可用；宁可退到「帧数正确」也不要退到「时长错误」。
    duration = _frac_to_float(video.get("duration")) or 0.0
    if duration <= 0:
        duration = _frac_to_float((data.get("format") or {}).get("duration")) or 0.0

    # 帧数：优先用容器自报，缺失时按 duration×fps 估（供进度条与汇总用；估不准只会
    # 让进度条的百分比略有偏差，不会影响实际处理帧数）
    nb_frames = 0
    try:
        nb_frames = int(video.get("nb_frames") or 0)
    except (TypeError, ValueError):
        nb_frames = 0
    if nb_frames <= 0 and duration > 0 and fps > 0:
        nb_frames = int(duration * fps)

    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "pix_fmt": video.get("pix_fmt") or "",
        "src_bits": src_bits,
        "sar": video.get("sample_aspect_ratio") or "",
        "color_space": (video.get("color_space") or "").lower(),
        "color_primaries": (video.get("color_primaries") or "").lower(),
        "color_transfer": (video.get("color_transfer") or "").lower(),
        "has_audio": has_audio,
        "audio_duration": audio_duration,
        "stream_durations_known": stream_durations_known,
        # 字幕轨清单（2026-10-09 加，`--subs` 需要）。每项含 index/codec/language/
        # is_bitmap —— burn 时位图格式（PGS/DVD 等）**烧不了**（libass 只认文本字幕），
        # keep 时位图也无法 copy 到 mp4。
        "subs": [
            {"index": i,
             "codec": (s.get("codec_name") or "").lower(),
             "language": ((s.get("tags") or {}).get("language") or ""),
             "is_bitmap": (s.get("codec_name") or "").lower() in _BITMAP_SUBS}
            for i, s in enumerate(
                [x for x in streams if x.get("codec_type") == "subtitle"])
        ],
        "duration": duration,
        "nb_frames": nb_frames,
    }


def build_decode_cmd(src: Path,
                     duration: Optional[float], frames: Optional[int],
                     decode: str = "cpu",
                     src_bits: int = 8,
                     start: Optional[float] = None,
                     ffmpeg: str = "ffmpeg") -> List[str]:
    """
    拼解码命令：源视频 → gbrp16le 原始帧流走 stdout。

    只取视频流（-an -sn -dn），音频留到编码阶段直接从源文件 map，省一次解码。
    宽高不在这里给：滤镜链不做缩放，尺寸由调用方按 --mod-crop 算好后，
    由 Python 侧按该尺寸切分原始帧字节。

    start/duration 用于**分段并行**（--split-mode segment）：`-ss` 放在 `-i` 之前是
    输入侧快速定位，ffmpeg 会先跳到关键帧再解码到精确时间点（故分段点不必落在关键帧
    上，输出仍是精确切分）。

    --decode cuda（硬件解码）时的关键约束：NVDEC 的输出留在显存里，本链路要的是
    CPU 侧的 gbrp16le 原始帧，所以必须 `hwdownload` 拉回内存再转格式 —— 而
    hwdownload 只能落到 nv12 / p010le（**没有 p016le**），故按源位深选下载格式
    （8bit 源 nv12、10bit+ 源 p010le），再交给 `format=gbrp16le` 做精度的
    向上转换。⚠ 这一段是本脚本唯一"待上机"验证的路径（本机无 GPU）。
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if decode == "cuda":
        # -hwaccel_output_format cuda：把解码结果留在显存，避免自动回落成 CPU 帧
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    if duration is not None:
        # -t 是输入选项（可放在 -i 前），限制读入时长
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-i", str(src), "-an", "-sn", "-dn"]
    if frames is not None:
        # ⚠ -frames:v 是**输出**选项，必须放在 -i 之后：放在前面会被 ffmpeg 当成
        # 输入选项并直接报错退出（实测 "cannot be applied to input url … Move this
        # option before the file it belongs to"，退出码 234）。
        cmd += ["-frames:v", str(frames)]
    if decode == "cuda":
        dl_fmt = "p010le" if int(src_bits or 8) >= 10 else "nv12"
        cmd += ["-vf", f"hwdownload,format={dl_fmt},format={RAW_PIX_FMT}"]
    else:
        cmd += ["-vf", f"format={RAW_PIX_FMT}"]
    cmd += ["-pix_fmt", RAW_PIX_FMT, "-f", "rawvideo", "-"]
    return cmd


def _default_warn(msg: str) -> None:
    """默认告警出口（调用方没给 warn 回调时用）。

    为什么需要它：build_encode_cmd / apply_rc_control_args 里的告警（如
    `--nvenc-aq` 在非 NVENC 编码器下被忽略）走 warn 回调，若调用方传 None 就**整条
    静默丢弃** —— 实测 dry-run 时 --nvenc-aq 一声不吭。默认出口保证告警一定可见。
    """
    print(f"  警告：{msg}", file=sys.stderr)


@dataclass
class EncodeSettings:
    """一次编码所需的全部已解析参数（由 build_encode_settings 从 args 算出）。"""
    codec: str
    crf: Optional[int] = None
    cq: Optional[int] = None
    qp: Optional[int] = None
    preset: str = DEFAULT_PRESET_CPU
    pix_fmt: Optional[str] = None
    rc_mode: str = "auto"
    lookahead: Optional[int] = None
    bitrate: Optional[str] = None
    nvenc_tune: Optional[str] = None
    nvenc_multipass: Optional[str] = None
    nvenc_aq: bool = False
    audio_codec: str = "copy"
    audio_bitrate: str = "128k"
    master_display: Optional[str] = None
    max_cll: Optional[str] = None
    color_range: str = "pc"
    threads: int = 0
    # 分段并行（--split-mode segment）用：音频输入也要按同一区间切，否则各段
    # 拼起来音画会逐段错位。None 表示整条（不切）。
    seg_start: Optional[float] = None
    seg_dur: Optional[float] = None
    # 音视频各自的时长（秒）。用于在拼命令时判断「音轨是否比视频短」——
    # 那是 `-shortest` 会截断视频的前提条件（见 build_encode_cmd 里的告警）。
    audio_duration: float = 0.0
    video_duration: float = 0.0
    # 音轨比视频短 ⇒ 已自动把音频从「流复制」降级为重编码，并对音频加 `-af apad`
    # 补静音到视频长度（否则 `-shortest` 会在音频 EOF 处截断视频）。设False 时
    # 一切照原样；见 build_plan 里的降级判定与 _audio_pad_needed。
    audio_pad: bool = False
    # ── 字幕（--subs，2026-09）─────────────────────────────────────
    subs_mode: str = "none"          # 实际生效的模式（auto 在无字幕时会退成 none）
    subs_index: int = 0              # 选中的源字幕轨序号
    subs_map: Optional[str] = None   # keep 用：给 -map 的 spec（如 "1:s:0"）
    subs_codec: Optional[str] = None  # keep 用：None=copy，否则强制转换码
    subs_burn_file: str = ""          # burn 用：导出的字幕文件路径
    subs_two_stage: bool = False      # keep 用：需remux 挂字幕（源流时长缺失时）


def _hdr_vf(color_range: str = "pc") -> str:
    """SDR(gbrp16le) → PQ/BT.2020 的滤镜链（含 setparams 帧级色彩注入）。

    与引入本参数之前的滤镜串**逐字一致**（verify_sdr_to_hdr ③ 会断言
    `setparams=colorspace=bt2020nc` 与 `-colorspace bt2020nc` 等 token）。
    """
    return (f"scale=out_color_matrix=bt2020nc:out_range={color_range},"
            "setparams=colorspace=bt2020nc:color_primaries=bt2020:"
            f"color_trc=smpte2084:range={color_range}")


def build_subs_remux_cmd(stage1: Path, src: Path, out: Path,
                         subs_index: int, subs_codec: Optional[str],
                         ffmpeg: str = "ffmpeg") -> List[str]:
    """阶段2 命令：把字幕 `-c copy`（必要时转码）挂进已编码好的产物。

    ⚠ 全程 `-c copy`（除字幕容器不兼容时）⇒ **不重编码、画质无损**，只是多一次
    remux。这也是「burn 不需要提质」的对偶：keep 的两阶段同样不损伤画质。
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(stage1), "-i", str(src),
           "-map", "0:v", "-map", "0:a?", "-map", f"1:s:{subs_index}"]
    cmd += ["-c", "copy"]
    if subs_codec:
        cmd += ["-c:s", subs_codec]
    cmd.append(str(out))
    return cmd


def build_encode_cmd(width: int, height: int, fps: float,
                     out: Path, src: Path,
                     st: EncodeSettings,
                     has_audio: bool,
                     warn: Optional[Callable[[str], None]] = None,
                     extra_args: Optional[List[str]] = None,
                     ffmpeg: str = "ffmpeg") -> List[str]:
    """
    拼编码命令：gbrp16le 原始帧流（stdin）+ 源文件（取音频）→ HDR10。

    顺序契约（与仓库另两个脚本一致）：
      输入 → 滤镜 → -c:v → 质量(-cq/-crf/-qp) → -b:v → rc 轴 → 速度档(-preset/
      -cpu-used) → -pix_fmt → -threads → 色彩四参 → 音频 → -x265-params(合并 HDR
      静态元数据与 rc-lookahead/lossless) → 容器收尾 → --extra-args → 输出路径。

    ⚠ **输出路径永远是最后一个 token、--extra-args 紧邻其前** —— 这条 token 顺序
    契约被 verify_sdr_to_hdr ③ 逐字断言（`shlex.split(cmd)[-3:]`）。
    ⚠ HDR10 静态元数据与 rc 轴产生的 x265 键（lookahead/lossless）必须合并成
    **同一条** -x265-params：实测两条 -x265-params 是后者整条覆盖前者。
    """
    c = st.codec.lower()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "rawvideo", "-pixel_format", RAW_PIX_FMT,
           "-video_size", f"{width}x{height}", "-framerate", f"{fps:.6f}",
           "-i", "pipe:0"]

    if st.audio_codec != "none" and has_audio:
        # 音频直接从源文件流复制/重编码，不再解一次视频码。
        # -map 1:a:0? 的 ? 表示源无音轨也不报错。
        # 分段并行时音频输入也要按同一区间切（-ss 在 -i 前 + -t），否则拼接后逐段错位。
        a_in: List[str] = []
        if st.seg_start is not None:
            a_in += ["-ss", f"{st.seg_start:.6f}"]
        if st.seg_dur is not None:
            a_in += ["-t", f"{st.seg_dur:.6f}"]
        cmd += a_in + ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0?"]
        # ── 字幕透传的 `-map`（--subs keep）───────────────────────
        # ⚠ 位置很讲究：必须夹在 `1:a:0?` 与 `-shortest` **之间**。
        #   · 放在 `-shortest` **之后** ⇒ 命令变成「…-shortest -i src -map…」，
        #     而 `-i` 是**输入选项**、`-shortest` 是**输出选项**，ffmpeg 要求输入
        #     选项在前 ⇒ 编码端立刻 BrokenPipe（实测「编码端提前退出」）。
        # ⚠ `-map` 必须给**具体 index**（`1:s:N`），给 `-map 1:s` 会带出全部字幕轨。
        if st.subs_map:
            cmd += ["-map", st.subs_map]
        cmd += ["-shortest"]
        # ⚠ `-shortest` 保留是有意的（它防的是**音轨比视频长**时输出尾巴拖长，
        # 实测音轨 20s / 视频 5s 的素材靠它把音轨正确截到 5s）。
        #
        # 「音轨比视频短」的副作用已在 **build_plan 里先修掉**：那里会把
        # audio_codec 从 copy 降级为 aac/libopus 并置 `st.audio_pad=True`
        # （见 `_audio_pad_needed`），于是音频被 apad 补长、不再短于视频，
        # `-shortest` 也就不会截断视频。
        #
        # ⚠ 兜底告警：若走到这里仍是 copy 且音轨确实偏短，说明降级没生效
        #   （例如调用方直接构造 EncodeSettings 而没走 build_plan）。
        #   补静音需要重编码，而 `apad` 与 `-c:a copy` 不能共存（ffmpeg rc=234），
        #   所以这里只能提示、不能自己修。
        if warn and not st.audio_pad and st.audio_duration and st.video_duration:
            _short = st.video_duration - st.audio_duration
            _lost = _short * fps
            if _lost >= _AUDIO_PAD_MIN_FRAMES:
                warn(f"源音轨比视频短 {_short:.2f}s（约 {int(_lost)} 帧）且音频仍是"
                     f"流复制（未走 build_plan 的自动降级）：`-shortest` 会丢掉这些帧。"
                     f"改用 `--audio-codec aac` 可补静音保住帧数。")

    # ── 字幕 burn：把 libass 片段**接在 _hdr_vf 之前** ──
    # ⚠ 顺序理由（实测两种顺序都可行，但语义不同）：字幕先按**源色域**渲染，
    #   随后的 scale/setparams 再把整帧（含字幕）一起转到 BT.2020/PQ ⇒ 字幕颜色
    #   与画面一致。反过来（先转 HDR 再烧）字幕会按 PQ/BT.2020 渲染再被转一次，
    #   白字会偏色/过亮。
    # ⚠ libass 能直接吃 gbrp16le（实测），所以 burn **不需要中间编码** ——
    #   整条链仍是「解码→NN 推理→一次编码」，不存在二次编码的画质损失。
    _vf = _hdr_vf(st.color_range)
    if st.subs_burn_file:
        _sub_path = str(st.subs_burn_file).replace("\\", "\\\\") \
                                .replace(":", r"\:").replace("'", r"\'")
        _vf = f"subtitles='{_sub_path}',{_vf}"
    cmd += ["-vf", _vf, "-c:v", st.codec]

    # ── 质量轴（与 vidcrop_cpu_v2.py 的 build_encoder_options_v2 同序同判）──
    if encoder_supports_qp(st.codec):
        # VAAPI 族只有 -qp（值已由 _resolve_quality_params 归一到基准轴）
        if st.qp is not None:
            cmd += ["-qp", str(st.qp)]
    elif st.cq is not None and encoder_supports_cq(st.codec):
        cmd += ["-cq", str(st.cq)]
        # [CQ-B0] NVENC 的 -cq 需配 -b:v 0 才是纯恒定质量，否则受 ffmpeg 默认码率
        # 约束（等价于 constrained quality）。给了 --bitrate 时不补这个 0。
        if c in NVENC_CODECS and not st.bitrate:
            cmd += ["-b:v", "0"]
    elif st.crf is not None and c == "librav1e":
        cmd += ["-qp", str(crf_to_rav1e_qp(st.crf))]
        if _RAV1E_SPEED > 0:
            cmd += ["-speed", str(_RAV1E_SPEED)]
    elif st.crf is not None and encoder_supports_crf(st.codec):
        if c in ("libvpx", "libvpx-vp9") and not st.bitrate:
            cmd += ["-b:v", "0"]
        cmd += ["-crf", str(st.crf)]

    if st.bitrate:
        cmd += ["-b:v", st.bitrate]

    # ── rc 轴 / NVENC 调优（-rc/-qp/-rc-lookahead/-lag-in-frames/-spatial-aq/
    #    -temporal-aq/-multipass/-tune）；libx265 的 rc-lookahead 走下面的
    #    -x265-params 合并 ──
    rc_args, rc_x265 = apply_rc_control_args(
        st.codec, st.rc_mode, st.qp, st.lookahead, warn,
        tune=st.nvenc_tune, multipass=st.nvenc_multipass,
        bitrate=st.bitrate, aq=st.nvenc_aq)
    cmd += rc_args

    # ── 速度档与像素格式 ──
    if c == "libaom-av1":
        cmd += ["-cpu-used", str(auto_effort()[0])]
    if encoder_supports_preset(c):
        cmd += ["-preset", st.preset]
    elif st.preset != default_preset_for(c) and warn:
        warn(f"编码器 {st.codec} 不支持 -preset，已忽略 --preset {st.preset}")
    if st.pix_fmt:
        cmd += ["-pix_fmt", st.pix_fmt]

    # --threads：只对**软件编码器**下发（硬件编码器不吃帧级线程）
    if st.threads and c not in _HW_ENCODERS:
        cmd += ["-threads", str(max(1, st.threads))]

    # ── 色彩四参（HDR10 交付口径；由 --color-range 控制 range，默认 pc）──
    cmd += ["-colorspace", "bt2020nc",
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-color_range", st.color_range]

    # ── 音频（按容器修正：WebM 只收 Opus/Vorbis，必要时代用 libopus 重编码）──
    audio_codec = resolve_audio_codec_for_container(st.audio_codec, out.suffix, src,
                                                    warn)
    # 音轨比视频短时已在上面把 audio_codec 降级为重编码（见 _audio_pad_needed 的
    # 调用处）；这里只负责下发它依赖的 `-af apad`。⚠ `-af` 与 `-c:a copy`
    # **不能共存**（ffmpeg 明确报 "Filtergraph 'apad' was specified, but codec
    # copy was selected" ⇒ rc=234），所以 apad 只在已降级为重编码时下发。
    if audio_codec != "none" and has_audio:
        if st.audio_pad:
            # 补静音到视频长度：`-shortest` 于是按视频收尾（音轨不再更短），
            # 既保住全部视频帧，也让音画时长对齐。
            cmd += ["-af", "apad"]
        cmd += ["-c:a", audio_codec]
        if audio_codec.lower() != "copy" and st.audio_bitrate:
            cmd += ["-b:a", st.audio_bitrate]

    # ── 字幕编码（--subs keep；burn 无需 -c:s，已烧进画面）──────────
    # None = copy（mkv 直传 / 已是容器原生格式）；否则按容器强制转换码。
    if st.subs_map and st.subs_codec:
        cmd += ["-c:s", st.subs_codec]

    # ── HDR10 静态元数据 + rc/lossless 键：**只有 libx265 能真正写入** ──
    # `-x265-params` 是 libx265 的私有选项：别的编码器收到它只会被**静默忽略**
    # （ffmpeg 仅在 warning 级打印 "has not been used for any stream"，而本脚本跑在
    #  -loglevel error ⇒ 用户什么都看不到），且 libx265 的 rc-lookahead / lossless=1
    #  也只有它认。故按编码器分派并发告警，与孪生脚本的 build_hdr_args 同语义。
    if c == "libx265":
        # --crf 0 的**真无损改写**：libx265 的 `-crf 0` 只是近无损，必须配 lossless=1
        # （与 vidcrop_cpu_v2.py:3319-3336 / vidcrop_hwaccel.py 的 [LOSSLESS] 块一致；
        #  本脚本此前**只在 help 与 docstring 里承诺**却从不下发 —— 对抗审查发现）。
        if (st.crf == 0) or (st.cq == 0):
            rc_x265.append("lossless=1")
            if warn:
                warn("--crf 0 → libx265 真无损改写（lossless=1）")
        x265 = build_x265_params(st.master_display, st.max_cll, extra=rc_x265)
        if x265:
            cmd += ["-x265-params", x265]
    elif c != "libx265" and st.master_display and warn:
        if c in NVENC_CODECS or c.endswith("_nvenc"):
            warn("NVENC 不写入 mastering display / MaxCLL，HDR10 静态元数据会丢失"
                 "（色彩三参数与 10bit 位深仍保留）；如需完整 HDR10 元数据请用 libx265")
        else:
            warn(f"编码器 {st.codec} 无法写入 mastering display / MaxCLL，"
                 f"仅保留色彩三参数与位深")

    if out.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        cmd += ["-movflags", "+faststart"]

    if extra_args:
        # 插在输出路径之前，保持「输出文件永远在最后」的 token 顺序契约
        cmd += list(extra_args)

    cmd.append(str(out))
    return cmd


def build_x265_params(master_display: Optional[str],
                      max_cll: Optional[str],
                      extra: Optional[List[str]] = None) -> Optional[str]:
    """
    拼 HDR10 静态元数据（单条 -x265-params）。

    ⚠ 必须是**一条**：实测 `-x265-params A -x265-params B` 是后者整条覆盖前者，
    与 vidcrop_cpu_v2.py 记录的同一坑一致。各项用冒号连接。

    extra 是**另外要写进同一条的参数**（如 rc-lookahead / lossless=1，见
    apply_rc_control_args 与 --crf 0 的无损改写）。它必须并入本函数而不是各发一条，
    否则后发的那条会静默抹掉 HDR 元数据。
    """
    params: List[str] = []
    if master_display:
        params.append("master-display=" + master_display)
    if max_cll:
        params.append("max-cll=" + max_cll)
    params.append("hdr10=1")
    params.extend(extra or [])
    return ":".join(params) if params else None


# ═══════════════════════════════════════════════════════════════════
#  模型侧：直接 import 上游定义，不复制代码
# ═══════════════════════════════════════════════════════════════════
def resolve_model_paths(model_repo: Path) -> Tuple[Path, Path]:
    """
    校验模型仓库布局，返回 (codes 目录, 权重文件路径)。

    缺失即抛 ValueError，错误信息里给出 clone 指引。
    """
    repo = Path(model_repo)
    codes = repo / "codes"
    weights = repo / "pretrained_models" / "Ensemble_AGCM_LE.pth"
    if not codes.is_dir():
        raise ValueError(
            f"模型仓库不完整：{codes} 不存在。\n"
            f"请先克隆：\n"
            f"    git clone --depth 1 https://github.com/xiaom233/HDRTVNet-plus.git "
            f"{repo}\n"
            f"或用 --model-repo 指定已有仓库的位置。")
    if not weights.is_file():
        raise ValueError(
            f"缺少预训练权重：{weights} 不存在。\n"
            f"HDRTVNet-plus 的权重随仓库一起分发（pretrained_models/，共约 7MB），"
            f"重新 clone 即可；若仓库是手工拷贝的，确认 pretrained_models/ 目录完整。")
    return codes, weights


class HdrTvNetPlus:
    """
    HDRTVNet++ 的 Ensemble_AGCM_LE 级联模型（AGCM + LE 合成一层）。

    上游是 BasicSR 框架（test.py + yml + dataset 类），但**推理链本身只依赖 torch**：
    models/modules/{Ensemble_AGCM_LE,Condition,HDRUNet3T1}_arch.py 与 arch_util.py
    只 import torch 与标准库；basicsr / opencv / scipy / lmdb / piq 都只在数据集层
    与训练配置层出现，推理用不到。因此这里不引入 BasicSR，直接 import 模型定义，
    绕开 basicsr==1.4.2 在 Python 3.12+ 上装不上的问题。

    数值约定（与上游 codes/data/util.py:read_img + utils/util.py:tensor2img 一致）：
      - 输入：RGB、float32、[0,1]、CHW。上游读 SDR PNG 时 uint8 除 255、
        uint16 除 65535；本脚本统一收 [0,1]，由 ffmpeg 的 16bit 管道保证精度。
      - 输出：RGB、float32、[0,1]、CHW（即 PQ 编码的 BT.2020 值，0..1 对应 0..10000 nit）
    """

    def __init__(self, model_repo: Path, device: str = "auto",
                 threads: int = 0, verbose: bool = True):
        self.codes_dir, self.weights = resolve_model_paths(model_repo)
        self.verbose = verbose

        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(torch_install_hint()) from exc

        import torch

        if threads and threads > 0:
            torch.set_num_threads(threads)

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("--device cuda 但当前 torch 报告无 CUDA 可用；"
                               "请改用 --device cpu")
        self.device = device
        self.torch = torch

        # 关键：把 <repo>/codes 放进 sys.path，让 models.* 包级 import 生效。
        # 上游用的是 `from .Condition_arch import ...` 相对引用，因此必须以包的方式
        # 导入整个 models 包，不能按文件路径加载。
        codes_str = str(self.codes_dir)
        if codes_str not in sys.path:
            sys.path.insert(0, codes_str)
        try:
            from models.modules.Ensemble_AGCM_LE_arch import Ensemble_AGCM_LE
        except Exception as exc:
            raise RuntimeError(
                f"import 上游模型定义失败：{exc}\n"
                f"已把 {codes_str} 加入 sys.path；若报的是 torch 相关错误，"
                f"多半是 torch 版本过低。") from exc

        self.net = Ensemble_AGCM_LE(classifier="color_condition", cond_c=6,
                                    in_nc=3, out_nc=3, nf=32,
                                    act_type="relu", weighting_network=False)
        state = torch.load(str(self.weights), map_location="cpu")
        # 上游 convert_pretrain_models.py 把 AGCM/LE 两个 .pth 合并成一份
        # 裸 OrderedDict（无 'params' 包装），键前缀为 'AGCM.' / 'LE.'，
        # 与 Ensemble_AGCM_LE 的子模块名一一对应。
        if isinstance(state, dict) and "params" in state \
                and isinstance(state["params"], dict):
            state = state["params"]
        missing, unexpected = self.net.load_state_dict(state, strict=False)
        if missing or unexpected:
            raise RuntimeError(
                f"权重与模型定义不匹配：缺 {len(missing)} 项"
                f"（示例 {missing[:3]}），多 {len(unexpected)} 项"
                f"（示例 {unexpected[:3]}）。"
                f"通常是 HDRTVNet-plus 仓库版本与权重不匹配，确认两者来自同一 commit。")
        self.net.eval()
        self.net.to(self.device)

        if self.verbose:
            nparam = sum(p.numel() for p in self.net.parameters())
            print(f"[模型] Ensemble_AGCM_LE 已加载：{self.weights}")
            print(f"[模型] 参数量 {nparam:,}，设备 {self.device}"
                  + (f"（{torch.cuda.get_device_name(0)}）"
                     if self.device == "cuda" else ""))

    # ── 内部：张量搬运 ────────────────────────────────────────────
    def _to_tensor(self, frame):
        """H×W×3 的 [0,1] float（ndarray 或嵌套 list）→ torch 张量 [1,3,H,W]。"""
        torch = self.torch
        np = _numpy()
        if np is not None and isinstance(frame, np.ndarray):
            # 有 numpy 时 from_numpy 零拷贝，比 torch.tensor(list) 快很多
            arr = torch.from_numpy(np.ascontiguousarray(frame, dtype=np.float32))
        else:
            arr = torch.tensor(frame, dtype=torch.float32)
        return arr.permute(2, 0, 1).unsqueeze(0).to(self.device)

    def _to_frame(self, tensor):
        """torch 张量 [1,3,H,W] → H×W×3 的 [0,1] float（钳到 [0,1]）。"""
        t = tensor.squeeze(0).clamp_(0.0, 1.0).permute(1, 2, 0)
        np = _numpy()
        if np is not None:
            return t.cpu().numpy()
        return t.cpu().tolist()

    # ── 推理 ────────────────────────────────────────────────────
    def enhance(self, frame, tile: int = 0, tile_overlap: int = 0):
        """
        对单帧做 SDR→HDR 增强。

        Args:
            frame: H×W×3 的 [0,1] float（RGB 顺序，ndarray 或嵌套 list）。
            tile: 分块边长，0 表示整帧推理。
            tile_overlap: 分块重叠像素（仅 tile>0 时生效）。

        Returns:
            H×W×3 的 [0,1] float，PQ 编码的 BT.2020 值。
        """
        torch = self.torch
        height = len(frame)
        width = len(frame[0]) if height else 0

        if not tile or tile <= 0:
            with torch.no_grad():
                x = self._to_tensor(frame)
                # 上游 Ensemble_AGCM_LE.forward(x) 收一个 [content, cond] 列表；
                # cond 用同一张图即可——全局色调映射的条件网络自己做 4 次 stride-2
                # 平均池化 + AdaptiveAvgPool2d(1) 汇总成 6 维条件向量，
                # 不需要预生成 bicx4 副本。上游 test_Ensemble_AGCM_LE.yml 里的
                # dataroot_cond 只是为复现论文数值而设，README 也写明这一步
                # 「不是必需的」。
                out = self.net([x, x])[0]
            return self._to_frame(out)

        if tile_overlap < 0:
            raise ValueError("--tile-overlap 不能为负")
        # 重叠必须为偶数倍的 8，否则拼接处尺寸对不齐
        step = tile - tile_overlap
        if step <= 0:
            raise ValueError(f"--tile({tile}) 必须大于 --tile-overlap({tile_overlap})")
        if step % ALIGN != 0:
            raise ValueError(
                f"分块步长 {step}（= tile {tile} - overlap {tile_overlap}）"
                f"必须是 {ALIGN} 的倍数，否则块间尺寸无法对齐。"
                f"请把 --tile-overlap 调成 {ALIGN} 的倍数。")

        np = _numpy()
        if np is not None:
            out_frame = np.empty((height, width, 3), dtype=np.float32)
        else:
            out_frame = [[[0.0] * 3 for _ in range(width)] for _ in range(height)]

        for y0 in range(0, height, step):
            y1 = min(y0 + tile, height)
            for x0 in range(0, width, step):
                x1 = min(x0 + tile, width)
                # 边缘块可能不是 tile 的整数倍（也不一定是 8 的倍数），
                # 用复制最外侧像素的方式补到对齐尺寸，算完只取回真实区域。
                ph, pw = y1 - y0, x1 - x0
                ph_pad = (ph + ALIGN - 1) // ALIGN * ALIGN
                pw_pad = (pw + ALIGN - 1) // ALIGN * ALIGN

                if np is not None:
                    patch = frame[y0:y1, x0:x1]
                    if (ph_pad, pw_pad) != (ph, pw):
                        patch = np.pad(patch, ((0, ph_pad - ph), (0, pw_pad - pw),
                                               (0, 0)), mode="edge")
                else:
                    patch = [row[x0:x1] for row in frame[y0:y1]]
                    if (ph_pad, pw_pad) != (ph, pw):
                        patch = [[list(px) + [list(px[-1])] * (pw_pad - pw)
                                  for px in row] for row in patch]
                        edge = patch[-1]
                        patch += [[list(px) for px in edge]
                                  for _ in range(ph_pad - ph)]

                with torch.no_grad():
                    t = self._to_tensor(patch)
                    res = self.net([t, t])[0]
                block = self._to_frame(res)

                if np is not None:
                    out_frame[y0:y1, x0:x1] = block[:ph, :pw]
                else:
                    for yy in range(ph):
                        row_out = out_frame[y0 + yy]
                        row_blk = block[yy]
                        for xx in range(pw):
                            row_out[x0 + xx] = row_blk[xx]
        return out_frame


# ═══════════════════════════════════════════════════════════════════
#  原始帧编解码（gbrp16le）
#
#  帧的内部表示随 numpy 是否可用而有两种，接口一致：
#    - 有 numpy：H×W×3 float32 ndarray（[0,1]，RGB 顺序）—— 快路径
#    - 无 numpy：H×W×3 的嵌套 list —— 仅供 --no-model 在没装 numpy 时兜底
#  为什么要有这个区分：纯 Python 逐像素循环在 1280x720 上要 ~2.7s/帧
#  （实测 raw→list 0.92s、list→raw 0.67s、占位转换 1.13s），
#  而模型路径本来就必须有 torch（连带 numpy），所以让它走 numpy；
#  没装 numpy 时仍保留纯 Python 路径，好让 --no-model 的编码链路验证
#  在零第三方依赖的环境下也能跑。
# ═══════════════════════════════════════════════════════════════════
def _numpy():
    """惰性取 numpy；没装返回 None。"""
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def raw_to_frame(buf: bytes, width: int, height: int):
    """
    gbrp16le 原始帧字节 → H×W×3 的 [0,1] float（RGB 顺序）。

    gbrp 是**平面**布局：先整帧 G 平面，再整帧 B 平面，再整帧 R 平面，
    每像素 16bit 小端。上游 read_img 读的是 cv2 的 BGR 再在数据集里翻成 RGB；
    这里直接按 R/G/B 重组，省掉一次翻转。
    """
    n = width * height
    need = n * PLANES * BYTES_PER_SAMPLE
    if len(buf) < need:
        raise ValueError(
            f"原始帧字节数不足：收到 {len(buf)}，期望 {need}"
            f"（{width}x{height} gbrp16le）")

    np = _numpy()
    if np is not None:
        a = np.frombuffer(buf, dtype="<u2").reshape(PLANES, height, width)
        # gbrp 平面顺序 = [G, B, R] → 取 [R, G, B] 还原成 RGB
        return np.stack((a[2], a[0], a[1]), axis=-1).astype(np.float32) / 65535.0

    scale = 1.0 / 65535.0
    rows = []
    for y in range(height):
        base = y * width
        row = []
        for x in range(base, base + width):
            r = struct.unpack_from("<H", buf, (2 * n + x) * 2)[0] * scale
            g = struct.unpack_from("<H", buf, x * 2)[0] * scale
            b = struct.unpack_from("<H", buf, (n + x) * 2)[0] * scale
            row.append([r, g, b])
        rows.append(row)
    return rows


def frame_to_raw(frame) -> bytes:
    """H×W×3 的 [0,1] float → gbrp16le 原始帧字节（G/B/R 平面，各 16bit 小端）。"""
    np = _numpy()
    if np is not None:
        a = np.asarray(frame, dtype=np.float32)
        # 先钳到 [0,1] 再 +0.5 取整：越界的值不能溢出成 >65535
        q = np.clip(a * 65535.0 + 0.5, 0.0, 65535.0).astype("<u2")
        # ⚠ 必须沿 axis=0 堆叠成**平面**布局（gbrp = 整帧 G、整帧 B、整帧 R）。
        # 沿 axis=-1 堆叠得到的是 HWC 逐像素交错，那是 rgb48 的布局，
        # 送进解码 ffmpeg 会让三通道整体错位（2026-10-06 实测：verify ① 的
        # 往返断言抓到的就是这一条）。
        return np.ascontiguousarray(
            np.stack((q[..., 1], q[..., 2], q[..., 0]), axis=0)
        ).tobytes()

    height = len(frame)
    width = len(frame[0]) if height else 0
    n = width * height
    out = bytearray(n * 6)
    mv = memoryview(out)
    for plane_index, channel in enumerate((1, 2, 0)):  # gbrp 平面顺序 G/B/R
        pos = plane_index * n * 2
        for row in frame:
            for px in row:
                v = px[channel]
                if v <= 0.0:
                    q = 0
                elif v >= 1.0:
                    q = 65535
                else:
                    q = int(v * 65535.0 + 0.5)
                mv[pos] = q & 0xFF
                mv[pos + 1] = (q >> 8) & 0xFF
                pos += 2
    return bytes(out)


# ═══════════════════════════════════════════════════════════════════
#  纯 ffmpeg 旁路（--no-model）：SDR 帧直接当 PQ 值用
# ═══════════════════════════════════════════════════════════════════
def sdr_frame_to_pq(frame, pivot: float = 0.58):
    """
    不跑网络时的占位转换：把 SDR 的 gamma 值直接当作 PQ 值输出。

    严格说这不是 HDR（画面整体偏暗、动态范围没变），只是让 --no-model 走通
    编码链路。它的用途是：
      - 验证抽帧 → 编码 → mux → HDR10 标签这条链路本身
      - 作为「网络到底带来了什么」的对照基线

    做法是在 gamma 域做一次幂函数提亮，pivot 为输入中位亮度附近的锚点，
    使中灰落在 PQ 的 0.18 附近——否则画面会暗到看不出编码是否正确。
    """
    inv_gamma = 1.0 / 2.4
    gain = (0.18 / (pivot ** inv_gamma)) * 1.6

    np = _numpy()
    if np is not None:
        a = np.asarray(frame, dtype=np.float32)
        return np.clip(np.power(np.clip(a, 0.0, 1.0), inv_gamma) * gain,
                       0.0, 1.0)

    out = []
    for row in frame:
        out.append([[min(1.0, max(0.0, (v ** inv_gamma) * gain)) for v in px]
                    for px in row])
    return out


# ═══════════════════════════════════════════════════════════════════
#  批处理：任务收集与输出命名
# ═══════════════════════════════════════════════════════════════════
_BAR_WIDTH = 24


def _bar(p: float, width: int = _BAR_WIDTH) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(width * p))
    return "█" * filled + "░" * (width - filled)


@dataclass
class Job:
    src: Path
    dst: Path
    info: Dict = field(default_factory=dict)
    status: str = "pending"   # pending / running / done / failed / skipped
    progress: float = 0.0
    fps: float = 0.0
    peak_fps: float = 0.0
    speed: str = ""
    frame: int = 0
    total_frames: int = 0
    elapsed: float = 0.0
    error: str = ""
    stage: str = ""
    warnings: List[str] = field(default_factory=list)
    # 运行期告警出口：由 run_sequential / run_parallel 在开跑前注入（打印或进面板）。
    # ⚠ 必须由运行路径注入而不是事后遍历 job.warnings —— 计划是在**跑的过程中**才
    # 构建的，事后遍历时缓冲里还是空的（老实现就是这样把真跑告警全吞掉的）。
    warn_cb: Optional[Callable[[str], None]] = None
    # 本任务实际使用的 ffmpeg 线程数（由 compute_parallelism 算出后注入，
    # 覆盖 args.threads 的默认 0 —— 否则"--threads 自动"只是一个显示值）。
    threads: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def name(self) -> str:
        return self.src.name


def _job_warn(job: Job, msg: str) -> None:
    """把告警记进 job 并立即送出口（打印 / 面板）。见 Job.warn_cb 的说明。"""
    job.warnings.append(msg)
    cb = job.warn_cb
    if cb is not None:
        try:
            cb(msg)
        except Exception:
            pass


def collect_video_files(input_path: Path, recursive: bool) -> List[Path]:
    """收集待处理的视频文件（单文件直接返回；目录按 --recursive 决定是否递归）。"""
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in VIDEO_EXTS else []
    if input_path.is_dir():
        iterator = input_path.rglob("*") if recursive else input_path.iterdir()
        return sorted({
            p for p in iterator
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        })
    return []


def get_extension_from_codec(codec: str) -> Optional[str]:
    c = (codec or "").lower()
    if c == "copy":
        return None
    if c in CODEC_CONTAINER_MAP:
        return CODEC_CONTAINER_MAP[c]
    base = c.split("_", 1)[0]
    for key, ext in CODEC_CONTAINER_MAP.items():
        if key != "copy" and key.startswith(base):
            return ext
    return ".mp4"


def make_output_file(src: Path, output_path: Path, codec: str,
                     container: Optional[str], batch_mode: bool,
                     input_root: Optional[Path],
                     suffix: Optional[str] = None) -> Path:
    """按「批量/单文件」「--container」「--suffix」推导输出文件路径。

    自动生成名的默认后缀是 `_hdr`（与裁剪脚本的 _cropped/_covered 同精神）。
    """
    if batch_mode or output_path.is_dir() or not output_path.suffix:
        ext = container if container else get_extension_from_codec(codec)
        if ext is None:
            ext = src.suffix

        out_dir = output_path
        if input_root and input_root.is_dir():
            try:
                out_dir = out_dir / src.parent.relative_to(input_root)
            except Exception:
                pass
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{src.stem}{suffix or '_hdr'}{ext}"

    dst = output_path
    if container:
        dst = dst.with_suffix(container)
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


def collect_jobs(input_path: Path, output_path: Path, codec: str,
                 container: Optional[str], overwrite: bool, recursive: bool,
                 suffix: Optional[str] = None) -> Tuple[List[Job], bool, Path]:
    """扫描输入、推导每个文件的输出路径，返回 (jobs, batch_mode, output_path)。"""
    if not input_path.exists():
        print(f"[ERROR] 输入路径不存在：{input_path}", file=sys.stderr)
        sys.exit(2)

    sources = collect_video_files(input_path, recursive)
    if not sources:
        return [], False, output_path

    batch_mode = len(sources) > 1 or input_path.is_dir()

    if batch_mode and output_path.suffix:
        print(f"警告：批量处理时输出路径 '{output_path}' 带扩展名，将视为目录。",
              file=sys.stderr)
        output_path = output_path.with_suffix("")

    if batch_mode and input_path.is_dir() and _same_path(input_path, output_path):
        print("[ERROR] 批量模式下 --input 和 --output 不能为同一目录，避免覆盖源文件。",
              file=sys.stderr)
        sys.exit(2)

    input_root = input_path if input_path.is_dir() else input_path.parent
    if suffix and not batch_mode and output_path.suffix:
        print("提示：--output 已指定完整文件名，--suffix 不生效。", file=sys.stderr)

    jobs: List[Job] = []
    for src in sources:
        dst = make_output_file(src, output_path, codec, container, batch_mode,
                               input_root, suffix)
        if _same_path(src, dst):
            print(f"[ERROR] 输出文件与输入文件相同，拒绝覆盖源文件：{src}", file=sys.stderr)
            sys.exit(2)
        if dst.exists() and not overwrite:
            jobs.append(Job(src=src, dst=dst, status="skipped", progress=1.0,
                            error="已存在，--overwrite 可覆盖"))
        else:
            jobs.append(Job(src=src, dst=dst))
    return jobs, batch_mode, output_path


# ═══════════════════════════════════════════════════════════════════
#  转换前后的结构化信息对比
#
#  裁剪脚本只打「大小变化」；本脚本把**视频结构**（分辨率/像素格式/位深/色彩四参数/
#  HDR 静态元数据/时长/帧数/音频/字幕）逐项列成「转换前 → 转换后」，因为 SDR→HDR10
#  的正确性主要靠这些字段判断（而它们恰恰是转完后最容易出错、又最难肉眼发现的部分）。
# ═══════════════════════════════════════════════════════════════════
def probe_video_summary(path: Path) -> Dict:
    """一次 ffprobe 拿到用于结构对比的字段（探测失败返回 {}）。"""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           check=False, env=_ffmpeg_env())
        data = json.loads(r.stdout or "{}")
    except Exception:
        return {}

    streams = data.get("streams") or []
    video = next((s for s in streams
                  if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        return {}

    fps = _frac_to_float(video.get("avg_frame_rate")) \
        or _frac_to_float(video.get("r_frame_rate")) or 0.0

    bits = 8
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        try:
            b = int(video.get(key) or 0)
            if b > 0:
                bits = b
                break
        except (TypeError, ValueError):
            pass
    pf = (video.get("pix_fmt") or "").lower()
    if bits == 8 and ("10le" in pf or "10be" in pf or "p010" in pf):
        bits = 10
    elif bits == 8 and ("12le" in pf or "p012" in pf):
        bits = 12

    # HDR 静态元数据在帧级 side_data 才暴露（与 vidcrop_cpu_v2 的探测同源）
    side = []
    trc = (video.get("color_transfer") or "").lower()
    prim = (video.get("color_primaries") or "").lower()
    if trc in ("smpte2084", "arib-std-b67") or prim == "bt2020" or bits >= 10:
        try:
            rr = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-select_streams", "v:0", "-show_frames",
                 "-read_intervals", "%+#1", str(path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20, check=False, env=_ffmpeg_env())
            for fr in (json.loads(rr.stdout or "{}").get("frames") or []):
                if fr.get("side_data_list"):
                    side = fr["side_data_list"]
                    break
        except Exception:
            side = []

    md = cll = ""
    for sd in side:
        stype = (sd.get("side_data_type") or "").lower()
        if "mastering display" in stype and sd.get("red_x") is not None:
            md = (f"R({sd.get('red_x')},{sd.get('red_y')}) "
                  f"G({sd.get('green_x')},{sd.get('green_y')}) "
                  f"B({sd.get('blue_x')},{sd.get('blue_y')}) "
                  f"L({sd.get('max_luminance')},{sd.get('min_luminance')})")
        elif "content light level" in stype:
            cll = f"{sd.get('max_content')},{sd.get('max_average')}"

    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        nb_frames = int(video.get("nb_frames") or 0)
    except (TypeError, ValueError):
        nb_frames = 0
    if nb_frames == 0 and duration > 0 and fps > 0:
        nb_frames = int(duration * fps)

    acodecs = [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"]
    subs = [s.get("codec_name") for s in streams if s.get("codec_type") == "subtitle"]
    try:
        size = int(fmt.get("size") or 0) or _file_bytes(Path(path))
    except (TypeError, ValueError):
        size = _file_bytes(Path(path))

    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "pix_fmt": video.get("pix_fmt") or "",
        "bits": bits,
        "vcodec": video.get("codec_name") or "",
        "space": video.get("color_space") or "",
        "prim": video.get("color_primaries") or "",
        "trc": video.get("color_transfer") or "",
        "range": video.get("color_range") or "",
        "master_display": md,
        "max_cll": cll,
        "duration": duration,
        "nb_frames": nb_frames,
        "acodecs": acodecs,
        "subs": subs,
        "size": size,
    }


def print_structural_diff(src: Path, dst: Path,
                          warn: Optional[Callable[[str], None]] = None) -> None:
    """打印「源 → 输出」的结构化字段对比（缺一侧时优雅降级）。"""
    a = probe_video_summary(src) if Path(src).is_file() else {}
    b = probe_video_summary(dst) if Path(dst).is_file() else {}
    if not b:
        if warn:
            warn(f"输出结构探测失败，跳过结构对比：{dst}")
        return

    def _row(name: str, ka: str, kb: Optional[str] = None) -> str:
        kb = kb or ka
        va = a.get(ka, "—") if a else "—"
        vb = b.get(kb, "—")
        if isinstance(va, float):
            va = f"{va:.3f}".rstrip("0").rstrip(".")
        if isinstance(vb, float):
            vb = f"{vb:.3f}".rstrip("0").rstrip(".")
        if isinstance(va, list):
            va = "/".join(str(x) for x in va) or "—"
        if isinstance(vb, list):
            vb = "/".join(str(x) for x in vb) or "—"
        va = va if va not in ("", None) else "—"
        vb = vb if vb not in ("", None) else "—"
        mark = "  " if str(va) == str(vb) else "≠ "
        return f"    {mark}{name:<10} {va}  →  {vb}"

    res_a = f"{a.get('width', '?')}x{a.get('height', '?')}" if a else "—"
    res_b = f"{b.get('width', '?')}x{b.get('height', '?')}"
    print(f"    结构对比  （≠ 表示与源不同）")
    print(f"      {'分辨率':<10} {res_a}  →  {res_b}")
    print(_row("编码", "vcodec"))
    print(_row("像素格式", "pix_fmt"))
    print(f"      {'位深':<10} {a.get('bits', '—')}bit  →  {b.get('bits', '—')}bit")
    print(_row("色彩空间", "space"))
    print(_row("原色", "prim"))
    print(_row("传输函数", "trc"))
    print(_row("色域范围", "range"))
    md_a = a.get("master_display") or "无"
    md_b = b.get("master_display") or "无"
    print(f"    {'  ' if md_a == md_b else '≠ '}{'静态元数据':<10} "
          f"{'有' if md_a != '无' else '无'}  →  {'有' if md_b != '无' else '无'}"
          f"    MaxCLL {a.get('max_cll') or '—'} → {b.get('max_cll') or '—'}")
    print(_row("时长(s)", "duration"))
    print(_row("帧数", "nb_frames"))
    print(_row("音频", "acodecs"))
    print(_row("字幕", "subs"))
    print(f"      {'体积':<10} {_size_change(Path(src), Path(dst))}")


# ═══════════════════════════════════════════════════════════════════
#  进度显示
# ═══════════════════════════════════════════════════════════════════
class AggregatePanel:
    """并行执行的聚合进度面板（与 vidcrop_cpu_v2.py 的同名类对应）。"""

    def __init__(self, jobs: List[Job]):
        self.jobs = jobs
        self.total = len(jobs)
        self.sizes = [_file_bytes(j.src) for j in jobs]
        self.lock = threading.Lock()
        self.start_ts = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._last_lines = 0
        self._events: List[str] = []

    def emit(self, msg: str) -> None:
        with self.lock:
            self._events.append(msg)
            if len(self._events) > 6:
                self._events.pop(0)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._render(final=True)

    def _aggregate(self):
        done = failed = skipped = running = pending = 0
        prog_sum = fps_sum = 0.0
        for j in self.jobs:
            if j.status == "done":
                done += 1
                prog_sum += 1.0
            elif j.status == "failed":
                failed += 1
                prog_sum += 1.0
            elif j.status == "skipped":
                skipped += 1
                prog_sum += 1.0
            elif j.status == "running":
                running += 1
                prog_sum += j.progress
                fps_sum += j.fps
            else:
                pending += 1
        overall = prog_sum / self.total if self.total else 1.0
        return overall, done, failed, skipped, running, pending, fps_sum

    def _queue_eta(self, elapsed: float) -> Optional[float]:
        done_bytes = remain_bytes = 0.0
        for job, size in zip(self.jobs, self.sizes):
            if job.status == "skipped":
                continue
            p = min(1.0, max(0.0, job.progress))
            done_bytes += size * p
            remain_bytes += size * (1.0 - p)
        # 开跑头几秒样本太薄（进度几乎为 0，除法会被放大成离谱数字），宁可暂不显示
        if elapsed <= 3.0 or done_bytes <= 0 or remain_bytes <= 0:
            return None
        return remain_bytes / (done_bytes / elapsed)

    def _render(self, final: bool = False) -> None:
        with self.lock:
            overall, done, failed, skipped, running, pending, fps_total = self._aggregate()
            elapsed = time.time() - self.start_ts
            eta = self._queue_eta(elapsed)
            events = list(self._events)

        if self._last_lines > 0:
            sys.stdout.write(f"\033[{self._last_lines}A")
            sys.stdout.write("\033[J")

        fps_str = f"  {fps_total:.0f}fps" if running > 0 and fps_total > 0 else ""
        eta_str = f"  预计剩余 {_fmt_time(eta)}" if eta is not None else ""
        line1 = (
            f"  [{_bar(overall)}] {overall * 100:5.1f}%  "
            f"完成 {done}  失败 {failed}  跳过 {skipped}  "
            f"运行中 {running}  等待 {pending}  已用 {_fmt_time(elapsed)}"
            f"{eta_str}{fps_str}"
        )
        sys.stdout.write(line1 + "\n")
        for ev in events:
            sys.stdout.write("    " + ev + "\n")
        sys.stdout.flush()

        self._last_lines = 1 + len(events)
        if final:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._render()
            self._stop.wait(0.5)


class SingleProgress:
    """顺序执行的单文件细粒度进度条（与 vidcrop_cpu_v2.py 的同名类对应）。"""

    def __init__(self, job: Job, queue_rest: Optional[float] = None,
                 queue_cur: Optional[float] = None):
        self.job = job
        self.start_ts = time.time()
        self.queue_rest = queue_rest
        self.queue_cur = queue_cur
        # 进度条用 \r 原地重绘；不先换行会把它上面刚打印的那行提示（如
        # 「单文件并行 / 分段并行」）直接覆盖掉。
        self._started = False

    def _queue_tail(self, progress: float, elapsed: float) -> str:
        if self.queue_rest is None or self.queue_cur is None:
            return ""
        if 0 < progress < 1.0:
            cur_left = elapsed * (1 - progress) / progress
        elif progress <= 0:
            cur_left = self.queue_cur
        else:
            cur_left = 0.0
        return f"  整批剩余 {_fmt_time(self.queue_rest + cur_left)}"

    def update(self, j: Job) -> None:
        elapsed = time.time() - self.start_ts
        if not self._started:
            self._started = True
            sys.stdout.write("\n")
        if j.progress <= 0.001:
            tail = "  预计中...  "
        elif j.progress < 1.0:
            eta = elapsed * (1 - j.progress) / j.progress
            tail = f"  剩余 {_fmt_time(eta)}   "
        else:
            tail = "  收尾中...  "

        frames = f"{j.frame}/{j.total_frames}帧" if j.total_frames else f"{j.frame}帧"
        fps = j.fps if j.fps > 0 else (j.frame / elapsed if elapsed > 0 else 0.0)
        stage = f"{j.stage}  " if j.stage else ""
        line = (
            f"\r  [{_bar(j.progress)}] {j.progress * 100:5.1f}%  "
            f"{frames}  "
            f"fps={fps:5.1f}  speed={j.speed or '-':>6}  "
            f"{stage}已用 {_fmt_time(elapsed)}{tail}{self._queue_tail(j.progress, elapsed)}   "
        )
        sys.stdout.write(line)
        sys.stdout.flush()

    def finish(self) -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()


def print_summary(jobs: List[Job]) -> int:
    done = sum(1 for j in jobs if j.status == "done")
    failed = sum(1 for j in jobs if j.status == "failed")
    skipped = sum(1 for j in jobs if j.status == "skipped")
    done_jobs = [j for j in jobs if j.status == "done"]
    total_time = sum(j.elapsed for j in done_jobs)
    total_frames = sum(int(j.info.get("nb_frames", 0) or 0) for j in done_jobs)
    avg_fps_str = f"  均速 {total_frames / total_time:.0f}fps" \
        if total_time > 0 and total_frames > 0 else ""
    peak_fps = max((j.peak_fps for j in done_jobs), default=0.0)
    peak_fps_str = f"  峰值 {peak_fps:.0f}fps" if peak_fps > 0 else ""

    print("─" * 64)
    print(f"汇总        : 完成 {done}  失败 {failed}  跳过 {skipped}  "
          f"累计用时 {_fmt_time(total_time)}{avg_fps_str}{peak_fps_str}")
    if failed:
        print("失败列表：")
        for j in jobs:
            if j.status == "failed":
                print(f"  ✘ {j.name}  →  {j.error}")
    return 0 if failed == 0 else 1


# ═══════════════════════════════════════════════════════════════════
#  参数校验
# ═══════════════════════════════════════════════════════════════════
def _split_extra_args(argv: List[str]) -> Tuple[List[str], Optional[List[str]]]:
    """
    把 `--extra-args` 之后的整段切出来（前导的 `--` 分隔符剥掉一个）。

    与 vidcrop_cpu_v2.py / vidcrop_hwaccel.py 的同名函数逐字对应（孪生约定）：
    不交给 argparse 的 nargs=REMAINDER，因为 Python 3.12 起它不再容忍开头的 `--`。

    Returns:
        (交给 argparse 的头部 argv, extra 参数列表或 None)。
        None 表示用户没写 `--extra-args`，调用方不要覆盖 argparse 的默认值。
    """
    key = "--extra-args"
    if key not in argv:
        return argv, None
    i = argv.index(key)
    head, tail = argv[:i + 1], list(argv[i + 1:])
    if tail and tail[0] == "--":
        tail = tail[1:]
    return head, tail


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SDR 视频转 HDR10（BT.2020/PQ/10bit），神经网络用 HDRTVNet++ "
                    "Ensemble_AGCM_LE；支持批量、CPU/GPU 编码与自动降级",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 1) 先看命令（解码/编码两条），不实际处理
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --dry-run

  # 2) 无 GPU / 无 torch 时验证编码链路（跳过神经网络）
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --no-model

  # 3) 完整流程（默认 libx265 / 软解 / 单文件顺序，与引入 GPU 支持之前一致）
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4

  # 4) 冒烟：前 48 帧 + CPU 推理
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --frames 48 --device cpu

  # 5) 4K 源分块推理（整帧会吃光内存）
  python3 convert_sdr_to_hdr.py -i uhd.mp4 -o out.mp4 --tile 1024 --tile-overlap 128

  # 6) 用 NVENC 硬件编码（HDR10 静态元数据会丢失，见 --codec 说明）
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --codec hevc_nvenc --cq 26

  # 7) 以统一基准轴给质量：libx264 CRF 21 的等质量换算（默认 quality 口径）
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --crf-ref 21

  # 8) 批量目录：多文件并行（外加 / 保留目录结构）
  python3 convert_sdr_to_hdr.py -i ./videos -o ./out -r --workers 2

  # 9) 单文件分块并行（按时间切段→并行处理→拼接）
  python3 convert_sdr_to_hdr.py -i big.mp4 -o big_hdr.mp4 --split-mode segment --workers 4

退出码：0 正常 / 1 ffmpeg 或推理失败 / 2 参数错误
""")
    p.add_argument("--input", "-i", required=True, help="输入视频文件或目录")
    p.add_argument("--output", "-o", required=True, help="输出视频文件或目录")

    g = p.add_argument_group("模型")
    g.add_argument("--model-repo", default=DEFAULT_MODEL_REPO,
                   help=f"HDRTVNet-plus 仓库目录（默认 {DEFAULT_MODEL_REPO}）")
    g.add_argument("--no-model", action="store_true",
                   help="跳过神经网络推理，只跑 SDR→PQ + HDR10 编码链路")
    g.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"),
                   help="推理设备（默认 auto：有 CUDA 用 cuda）")
    g.add_argument("--torch-threads", type=int, default=0, metavar="N",
                   help="torch CPU 线程数（默认 0 = 不干预）。⚠ 旧版的 --threads 是"
                        "这个含义，现按姊妹脚本统一为「每任务 ffmpeg 线程数」，故更名")
    g.add_argument("--tile", type=int, default=0,
                   help="分块推理边长（默认 0 = 整帧；4K 建议 1024，最小 24）。"
                        "注意分块不等价于整帧：每块各算自己的全局色调映射")
    g.add_argument("--tile-overlap", type=int, default=0,
                   help="分块重叠像素（默认 0；须为 8 的倍数）")

    g = p.add_argument_group("编解码器")
    g.add_argument("--codec", default=DEFAULT_CODEC,
                   help=f"视频编码器（默认 {DEFAULT_CODEC}，唯一能写完整 HDR10 静态元数据的"
                        f"交付编码器）。支持别名 x265/hevc/svtav1/av1 等；硬件编码器 "
                        f"hevc_nvenc / av1_nvenc 可用但需显式指定（HDR10 静态元数据会丢失，"
                        f"且不可用时按 --fallback-policy 降级）")
    g.add_argument("--decode", default="cpu", choices=DECODE_BACKENDS,
                   help="解码后端（默认 cpu）。cuda=硬件解码；auto=探测到 CUDA 才用。"
                        "⚠ 源是 SDR、解码在整条链里占比小，而硬解必须 hwdownload 回 CPU "
                        "再转 gbrp16le，收益有限，故默认保守")
    g.add_argument("--fallback-policy", default="auto", choices=("auto", "strict"),
                   help="硬件不可用或失败时的策略（默认 auto=自动降级到 CPU 等价物并告警；"
                        "strict=直接报错退出 2）")

    g = p.add_argument_group("质量轴")
    g.add_argument("--crf", type=int, default=None,
                   help="CRF 质量值（字面量，原样下发给支持 -crf 的编码器；落到只认 "
                        "-cq/-qp 的编码器时按 libx264 CRF 口径换算）。不指定则由基准轴 "
                        f"{DEFAULT_REF} 换算到目标编码器（libx265 → CRF {DEFAULT_REF}）。"
                        "0 = 无损档（libx265 会配 lossless=1；NVENC 的 0 不是真无损）")
    g.add_argument("--cq", type=int, default=None,
                   help="CQ 质量值（GPU 编码器 NVENC/AMF 的原生刻度）；落到 CPU 编码器时"
                        "按等效表换算为 -crf")
    g.add_argument("--crf-ref", type=int, default=None, metavar="N",
                   help="以 libx264 CRF 为统一基准给出质量，按等效表换算到目标编码器"
                        "（默认 quality 口径=等质量；--quality-mode size 则等体积）。"
                        "与 --crf / --cq 互斥")
    g.add_argument("--cq-ref", type=int, default=None, metavar="N",
                   help="以 h264_nvenc CQ 为统一基准给出质量，按等效表换算。"
                        "与 --crf / --cq 互斥")
    g.add_argument("--quality-mode", choices=("size", "quality"), default="quality",
                   help="换算口径（--crf-ref / --cq-ref / 降级换算用）：quality=等质量"
                        "（默认，QUALITY_MAP）；size=等体积（SIZE_MAP）")
    g.add_argument("--bit-depth", type=int, default=10, choices=(10,),
                   help="输出位深（默认 10；SDR→HDR10 必须 10bit）")

    g = p.add_argument_group("码率控制（NVENC）")
    g.add_argument("--rc-mode", default="auto", metavar="MODE",
                   help="NVENC 码率控制模式：auto / constqp / vbr / cbr"
                        "（也可写 nvenc-<mode>）。auto 时 h264/hevc/av1 均下发 vbr。"
                        "非 NVENC 编码器下告警忽略")
    g.add_argument("--qp", type=int, default=None, metavar="N",
                   help="恒定 QP（只在 --rc-mode constqp 下生效；量程随编码器不同，"
                        "AV1 为 0~255）。落到 CPU 编码器时换算为等效 -crf")
    g.add_argument("--lookahead", type=int, default=None, metavar="N",
                   help="前向预测帧数（0~250）。按编码器分别下发：libx264/*_nvenc 用 "
                        "-rc-lookahead，libx265 写进 -x265-params，vp9/aom 用 -lag-in-frames")
    g.add_argument("--bitrate", default=None, metavar="RATE",
                   help="目标码率（如 8M / 8000k）。与质量参数并存时按 ffmpeg 语义是"
                        "「受码率约束的恒定质量」；constqp 下不允许")
    g.add_argument("--nvenc-aq", action="store_true",
                   help="给 NVENC 打开自适应量化（-spatial-aq 1 -temporal-aq 1），"
                        "改善平坦区/高光的块效应。仅 *_nvenc 生效，其余编码器告警忽略")
    g.add_argument("--nvenc-tune", default=None, choices=list(_NVENC_TUNE_VALUES),
                   metavar="TUNE",
                   help="NVENC -tune 档：hq（默认值，写不写一样）/ ll / ull / lossless / "
                        "uhq（仅 hevc/av1 NVENC 有）。默认不下发")
    g.add_argument("--nvenc-multipass", default=None,
                   choices=list(_NVENC_MULTIPASS_VALUES), metavar="MP",
                   help="NVENC -multipass：disabled / qres / fullres。默认不在 CQ 路径下发；"
                        "rc-mode=cbr 或给了 --bitrate 时自动补 fullres")

    g = p.add_argument_group("输出与容器")
    g.add_argument("--preset", default=None,
                   help=f"编码器预设（默认按编码器取：CPU 软编 {DEFAULT_PRESET_CPU}、"
                        f"GPU 硬编 {DEFAULT_PRESET_GPU}、svtav1 {DEFAULT_PRESET_SVTAV1}）。"
                        "支持 libx264 风格与 NVENC 风格，自动双向映射")
    g.add_argument("--container", default=None,
                   help="输出容器扩展名（如 .mp4 / .mkv）；不给则按编码器推导")
    g.add_argument("--suffix", default=None, metavar="SUFFIX",
                   help='批量/目录输出时自动生成名的后缀（默认 "_hdr"），'
                        '如 --suffix "_HDR10" → abc.mp4 输出 abc_HDR10.mp4')
    g.add_argument("--color-range", default="pc", choices=("pc", "tv"),
                   help="输出 color_range（默认 pc=full range，HDR10/PQ 的交付惯例）")
    g.add_argument("--audio", default="copy", choices=("copy", "none"),
                   help="音频处理（默认 copy 流复制；none = 丢弃）。"
                        "源音轨比视频短时会**自动降级为重编码并补静音**"
                        "（-af apad）以保住全部视频帧（默认降 aac，WebM 降 libopus）")
    g.add_argument("--subs", default="auto",
                   choices=("auto", "none", "keep", "burn"),
                   help="字幕处理：auto（默认，保持现状**不保留**字幕，但源含字幕时"
                        "会告警）/ none（同 auto，显式声明不要）/ keep（透传到产物，"
                        "受容器与 codec 限制）/ burn（烧进画面，需 libass）")
    g.add_argument("--subs-index", type=int, default=0, metavar="N",
                   help="选源里的第几条字幕轨（0=第一条，默认）。"
                        "keep=透传这一条；burn=烧这一条。"
                        "⚠ 位图字幕（PGS/DVD/DVB 等）既不能透传到 mp4 也不能 burn")
    g.add_argument("--audio-codec", default="copy",
                   help="音频编码器（默认 copy；重编码可用 aac / libopus）。"
                        "⚠ 只有非 copy 才能补静音（apad 与流复制不能共存，"
                        "ffmpeg rc=234）；音轨偏短时若已显式指定本项，则保留你的"
                        "选择并只补静音，不会改写成别的编码器。"
                        "⚠ **显式写 copy 也关不掉自动降级** —— 保住视频帧优先于音轨原样")
    g.add_argument("--audio-bitrate", default="128k",
                   help="音频重编码码率（默认 128k，仅 --audio-codec 非 copy 时生效）")
    g.add_argument("--master-display", default=DEFAULT_MASTER_DISPLAY,
                   help="HDR10 mastering display 元数据")
    g.add_argument("--no-master-display", action="store_true",
                   help="不写 mastering display（只写 max-cll 与色彩三参数）")
    g.add_argument("--max-cll", default=DEFAULT_MAX_CLL,
                   help=f"MaxCLL,MaxFALL（默认 {DEFAULT_MAX_CLL}）")
    g.add_argument("--no-mod-crop", dest="mod_crop", action="store_false",
                   default=True,
                   help="关闭向内裁到 8 的倍数（默认开）")
    g.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出")
    g.add_argument("--recursive", "-r", action="store_true", help="递归扫描输入目录")
    g.add_argument("--dry-run", action="store_true",
                   help="只打印环境/计划与两条 ffmpeg 命令，不实际处理")
    g.add_argument("--log", help="把全部输出追加写入日志文件")
    g.add_argument("--extra-args", nargs=argparse.REMAINDER,
                   help="追加任意 ffmpeg 参数（放在编码命令末尾、输出路径之前）")

    g = p.add_argument_group("并发")
    g.add_argument("--workers", type=int, default=0,
                   help="并行任务数（0=自动，按 CPU/内存/推理设备推算）")
    g.add_argument("--threads", type=int, default=0, metavar="N",
                   help="每任务 FFmpeg 编码线程数（0=自动）。只对软件编码器下发 —— "
                        "硬件编码器不吃帧级线程")
    g.add_argument("--mem-per-job", type=float, default=0.0,
                   help="单任务估计内存占用 GB（0=按编码器画像 + NN 开销）")
    g.add_argument("--sequential", action="store_true",
                   help="强制顺序执行，显示单文件细粒度进度条")
    g.add_argument("--split-mode", default="auto",
                   choices=("auto", "segment", "workers", "off"),
                   help="单文件内并行的路径（默认 auto：多文件→文件级并行；单文件大→"
                        "segment。segment=按时间切段并行处理后拼接；workers=单解码+"
                        "多推理进程+单编码；off=不做单文件并行）")

    g = p.add_argument_group("裁剪（调试）")
    g.add_argument("--duration", type=float, default=None,
                   help="只处理前 N 秒（调试/冒烟用）")
    g.add_argument("--frames", type=int, default=None,
                   help="只处理前 N 帧（比 --duration 精确，调试/冒烟用）")

    args = p.parse_args(argv)
    return args


def normalize_container(container: Optional[str]) -> Optional[str]:
    if not container:
        return None
    c = container.strip()
    if not c:
        return None
    if not c.startswith("."):
        c = "." + c
    return c.lower()


def validate_args(args: argparse.Namespace,
                  argv_argv: Optional[List[str]] = None) -> None:
    """参数校验与归一化。违反约定抛 ValueError（调用方转成退出码 2）。

    顺序（与 vidcrop_cpu_v2.py 的 validate_and_finalize_args 大体对齐）：
      编码器归一 → 质量互斥/量程 → 换算质量参数 → rc 轴 → preset → HDR10 能力 →
      编码器可用性降级 → 解码后端 → 音频/容器 → 并发参数 → 模型仓库。
    ⚠ 量程检查必须**排在 _resolve_quality_params 之前**：否则超范围输入会先被换算并
    打印出一行建议值，紧接着才报范围错误，自相矛盾。
    """
    # 换算口径（等质量 / 等体积）必须在任何换算之前选定
    set_quality_mode(args.quality_mode)

    if not Path(args.input).exists():
        raise ValueError(f"输入路径不存在：{args.input}")
    if args.duration is not None and args.duration <= 0:
        raise ValueError(f"--duration 必须为正数，给的是 {args.duration}")
    if args.frames is not None and args.frames <= 0:
        raise ValueError(f"--frames 必须为正数，给的是 {args.frames}")
    if args.tile < 0:
        raise ValueError(f"--tile 不能为负，给的是 {args.tile}")
    if args.tile_overlap < 0:
        raise ValueError(f"--tile-overlap 不能为负，给的是 {args.tile_overlap}")
    if args.tile > 0:
        if args.tile % ALIGN != 0:
            raise ValueError(
                f"--tile 必须是 {ALIGN} 的倍数（模型下采样三次），给的是 {args.tile}")
        # Color_Condition 有 4 次 stride-2 平均池化，末层空间尺寸必须 >1，
        # 否则 InstanceNorm 报 "Expected more than 1 spatial element"。
        # 4 次 /2 即 ÷16，再加一次 /2 余量 ⇒ 最小 24（实测 16 失败、24 通过）。
        if args.tile < 24:
            raise ValueError(
                f"--tile 不能小于 24（模型的条件网络要 4 次 stride-2 池化，"
                f"块太小会让末层退化成 1x1，InstanceNorm 直接报错），给的是 {args.tile}")
        if args.tile_overlap and args.tile_overlap % ALIGN != 0:
            raise ValueError(
                f"--tile-overlap 必须是 {ALIGN} 的倍数，给的是 {args.tile_overlap}")
        if args.tile_overlap >= args.tile:
            raise ValueError(
                f"--tile({args.tile}) 必须大于 --tile-overlap({args.tile_overlap})")

    # ── 编码器归一 ──
    args.codec = normalize_codec_name(args.codec)
    if args.codec == "auto":
        # 与姊妹脚本的 auto→CPU 默认编码器同精神：本脚本的 CPU 默认是 libx265。
        args.codec = DEFAULT_CODEC
        print(f"提示：--codec auto 已解析为 {DEFAULT_CODEC}。")
    _requested_codec = args.codec

    # ── HDR10 能力检查（对**请求的**编码器判定；8bit-only 直接拒绝）──
    _level, _msg = hdr10_capability(_requested_codec)
    if _level == "error":
        raise ValueError(_msg)
    if _level == "warn":
        args.warnings.append(_msg)

    # ── 编码器可用性 & 自动降级 ──
    # ⚠ 必须排在质量换算**之前**：降级换了编码器，质量值要按**新编码器**的刻度换算
    # （否则 --codec hevc_nvenc --cq 26 降级到 libx265 后，CQ 26 会原样留下而
    # libx265 既不认 -cq、也没了 -crf —— 质量值静默蒸发）。
    args.codec = resolve_codec(_requested_codec, args.fallback_policy,
                               warn=args.warnings.append)

    # ── 质量参数互斥（-ref 系列与字面量量纲不同，混用无法判定意图）──
    _refs = [n for n, v in (("--crf-ref", args.crf_ref), ("--cq-ref", args.cq_ref))
             if v is not None]
    if len(_refs) > 1:
        raise ValueError(" 与 ".join(_refs) + " 只能二选一（两者基准轴不同）。")
    if _refs and (args.crf is not None or args.cq is not None):
        _given = [n for n, v in (("--crf", args.crf), ("--cq", args.cq))
                  if v is not None]
        raise ValueError(
            _refs[0] + " 与 " + " / ".join(_given)
            + " 互斥：前者按统一基准轴换算，后者字面量原样下发，"
              "混用无法确定以哪个为准。请只保留其中一种。")
    if args.crf is not None and args.cq is not None:
        # ⚠ 两个字面量并存时，_resolve_quality_params 只会取其中一个（取决于编码器认
        # 哪条轴）、另一个**静默丢弃** —— 用户无法从输出判断生效值是多少。
        raise ValueError(
            "--crf 与 --cq 不能同时使用：两者量纲不同（CRF 轴 / CQ 轴），"
            "并存时只有一个会生效。\n"
            "  要指定统一质量请用 --crf-ref / --cq-ref（按等效表换算到目标编码器）。")

    # ── 字面量量程按**请求的**编码器判定（不再一刀切 0~51）；它必须在降级之前，
    #    否则 av1_nvenc 的 --cq 60（合法 CQ 轴 0~63）降级到 libx265 后会被 0~51 误拒 ──
    if args.crf is not None:
        _lo, _hi = literal_range(_requested_codec, "crf")
        if not (_lo <= args.crf <= _hi):
            raise ValueError(
                f"--crf {args.crf} 超出编码器 {_requested_codec} 的可用范围 {_lo}~{_hi}"
                f"（CRF 字面量按该编码器刻度解释）。")
    if args.cq is not None:
        _lo, _hi = literal_range(_requested_codec, "cq")
        if not (_lo <= args.cq <= _hi):
            raise ValueError(
                f"--cq {args.cq} 超出编码器 {_requested_codec} 的可用范围 {_lo}~{_hi}"
                f"（CQ 字面量按该编码器刻度解释）。")
    if args.crf_ref is not None and not (0 <= args.crf_ref <= 51):
        raise ValueError("--crf-ref 范围为 0-51（libx264 CRF 量程）。")
    if args.cq_ref is not None and not (0 <= args.cq_ref <= 51):
        raise ValueError("--cq-ref 范围为 0-51（h264_nvenc CQ 量程）。")

    # ── rc 轴 ──
    args.rc_mode = parse_rc_mode(args.rc_mode)
    args.bitrate = parse_bitrate(args.bitrate)
    if args.lookahead is not None:
        check_int_range(args.lookahead, "--lookahead", _LOOKAHEAD_RANGE, _LOOKAHEAD_HINT)
    if args.qp is not None:
        check_int_range(args.qp, "--qp", qp_range(_requested_codec), _QP_HINT)

    _literal = [n for n, v in (("--crf", args.crf), ("--cq", args.cq)) if v is not None]
    if args.rc_mode == "constqp":
        if args.qp is None and not _refs:
            raise ValueError(
                "--rc-mode constqp 需要 --qp、--crf-ref 或 --cq-ref 三者之一来指定"
                "恒定质量（QP 0-51）。\n  例：--rc-mode constqp --qp 23")
        if args.qp is not None and _refs:
            raise ValueError(
                "--rc-mode constqp 下 --qp 与 " + " / ".join(_refs)
                + " 不能同时使用（都表达恒定质量，但量纲不同）。")
        if args.bitrate:
            raise ValueError(
                "--rc-mode constqp 与 --bitrate 不能同时使用：\n"
                "  constqp 是恒定 QP 模式，码率由 QP 决定，NVENC 会完全无视 -b:v。")
        if _literal:
            raise ValueError(
                "--rc-mode constqp 与 " + " / ".join(_literal)
                + " 不能同时使用：constqp 用 --qp 表达质量，而字面量量纲不同。")
    else:
        if args.qp is not None:
            print(f"  ⚠ --qp 只在 --rc-mode constqp 下生效，当前 rc-mode={args.rc_mode}，"
                  f"已忽略。")
            args.qp = None
        if args.bitrate and (_literal or _refs):
            print("提示：--bitrate 与质量参数同时给出 → 按 ffmpeg 语义是「受码率约束的"
                  "恒定质量」。要纯恒定质量请去掉 --bitrate。")
        if args.rc_mode.startswith("cbr") and not args.bitrate:
            print(f"  ⚠ --rc-mode {args.rc_mode} 是恒定码率模式但未给 --bitrate，"
                  f"ffmpeg 会用它自己的默认码率（200kbps）。建议补 --bitrate 8M 之类。")

    # ── 换算质量参数（crf/cq/qp 三元组）──
    # 先留一份**用户原始输入**：运行中降级换了编码器时要按新编码器重算
    # （否则 `--codec hevc_nvenc --cq 26` 降级到 libx265 后质量值会静默蒸发）。
    args.quality_originals = {
        "crf": args.crf, "cq": args.cq, "crf_ref": args.crf_ref,
        "cq_ref": args.cq_ref, "qp": args.qp, "requested": _requested_codec,
    }
    args.crf, args.cq, args.qp = _resolve_quality_params(
        args.codec, args.crf, args.cq, crf_ref=args.crf_ref, cq_ref=args.cq_ref,
        qp=args.qp, rc_mode=args.rc_mode, src_codec=_requested_codec)

    # ── preset 默认值与归一（按**最终**编码器取默认档；显式 NVENC 风格 preset 会被
    #    映射到 libx264 风格，如降级后 --preset p6 → slow）──
    if not args.preset:
        args.preset = default_preset_for(args.codec)
    args.preset = normalize_preset(args.preset, args.codec)

    # ── 解码后端 ──
    _in_path = Path(args.input)
    args.decode_resolved = resolve_decode_backend(
        args.decode, args.fallback_policy,
        sample=_in_path if _in_path.is_file() else None,
        warn=args.warnings.append)

    # ── 音频 / 容器 ──
    if args.audio == "none":
        args.audio_codec_effective = "none"
        if args.audio_codec != "copy":
            print("  ⚠ --audio none 会丢弃音轨，--audio-codec "
                  f"{args.audio_codec} 已忽略。", file=sys.stderr)
    else:
        args.audio_codec_effective = args.audio_codec
        if args.audio_codec != "copy":
            # --audio-codec 的优先级高于 --audio copy（两者语义重叠，必须说清楚）
            print(f"提示：--audio-codec {args.audio_codec} 优先于 --audio copy，"
                  f"音轨将重编码。")
    # 用户是否**显式**写了 --audio-codec（2026-10-09 加）。用途：音轨偏短时脚本
    # 会自动降级补静音（否则 -shortest 会丢视频帧），而「显式指定 copy 却被改写」
    # 与「默认值 copy 被改写」对用户的意义不同 —— 前者明确覆盖了用户意图，
    # 提示必须区分，否则用户会以为 `--audio-codec copy` 是能关掉降级的开关。
    #
    # ⚠ 判据不能用 `args.audio_codec != "copy"`：分不出「默认」与「显式 copy」。
    #   也**不能扫 `sys.argv`** —— main() 里 `--extra-args` 之后的参数是给
    #   ffmpeg 的（`_split_extra_args` 已把它们切走），扫全量 argv 会把
    #   `--extra-args -- --audio-codec copy` 误判成「用户显式指定了脚本参数」。
    #   正确做法：用 argparse 收到的 argv（已切掉 --extra-args 段）。
    #   拿不到 argv（被当库调用）时保守判 False —— 少说一句提示好过误报。
    args.audio_codec_explicit = any(
        a == "--audio-codec" or a.startswith("--audio-codec=")
        for a in (argv_argv or []))
    args.container_normalized = normalize_container(args.container)

    # ── 并发参数 ──
    if args.workers < 0:
        raise ValueError("--workers 不能为负数")
    if args.threads < 0:
        raise ValueError("--threads 不能为负数")
    if args.mem_per_job < 0:
        raise ValueError("--mem-per-job 不能为负数")
    if args.torch_threads < 0:
        raise ValueError("--torch-threads 不能为负数")

    if not args.no_model:
        # 只有真要跑网络时才校验模型仓库。--no-model 时跳过（不需要模型/权重）。
        # --dry-run 不在此豁免：它仍需打印完整命令含模型路径，仓库缺失时应报错退出 2
        # （docstring 示例 1「先看命令」在缺模型仓库时应报错，而非静默通过）。
        resolve_model_paths(Path(args.model_repo))


# ═══════════════════════════════════════════════════════════════════
#  计划与执行
# ═══════════════════════════════════════════════════════════════════
# 音轨偏短时判定「要不要为了保帧而重编码」的阈值（帧）。
# ⚠ 为什么是 1 而不是更大值：实测 ffprobe 对**同源等长**素材（lavfi 同时产视频与
# 音频）报出的两流 duration **完全相等**（偏差 0.00 帧），所以 1 帧不会被容器噪声
# 误触发；而等长偏差为 0、偏长为负，都不会越过这个阈值。
#   曾用 3 帧，代价是 `../input_videos/test3.mp4`（音轨短 0.0597s = **1.79 帧**）
#   不触发降级 ⇒ 实测仍丢 3 帧（302→299）且 EXIT=0、**零告警**——真丢帧被当成了
#   容器舍入噪声（调研实测确认）。阈值过高的代价是「静默丢帧」，比多一次重编码
#   严重得多：后者有告警且可事后用 --audio none 规避。
_AUDIO_PAD_MIN_FRAMES = 1

# 音频重编码的默认编码器（音轨偏短时自动降级到这里）。选 aac 是因为：
#   · mp4/mkv 的通用兼容最好，HDR10 交付链路都认；
#   · ffmpeg 内建（libavcodec），不依赖任何外部编码器；
#   · 与 --audio-codec aac 走同一条已实测可用的路径。
_AUDIO_FALLBACK_CODEC = "aac"


def _segment_durations(meta: Dict, seg_start: Optional[float],
                       seg_dur: Optional[float]) -> Tuple[float, float]:
    """返回 (本段视频时长, 本段音轨时长)，单位秒。`seg_start=None` 即整条。

    ⚠ 分段并行必须按**本段**算，不能拿全片时长直接减全片音轨时长 ——
    那两个数分属不同的区间，差值没有物理意义。实测（4 段、视频 44s）：
    音轨 16s 时段 1 [11,22) 真实短 6s，而「全片口径」算出 11−16 = **−5**
    ⇒ 判定「不短」⇒ 不补静音 ⇒ `-shortest` 在音频 EOF 处截断 ⇒ BrokenPipe，
    **整条转换失败**（EXIT=1）；音轨 30s / 22s 两例同样失败。
    反过来末段（`seg_dur=None`）退回全片时长又会**高估**（真实短 11s 却报 34s）。

    本段音轨 = `[seg_start, 本段视频末]` 与 `[0, 全片音轨长]` 的**交集**：
    音轨在段中途结束 ⇒ 只算到结束点；音轨在该段开始前就结束了 ⇒ 交集为 0
    （这是「本段确实没有音频」的**已知**事实，与「探测不到音轨时长」不同，
    故调用方仍须先用全片 `audio_duration > 0` 把「未知」情形挡在外面）。
    """
    v_total = float(meta.get("duration") or 0.0)
    a_total = float(meta.get("audio_duration") or 0.0)
    if seg_start is None:
        return v_total, a_total
    start = max(0.0, float(seg_start))
    v_seg = float(seg_dur) if seg_dur else max(0.0, v_total - start)
    a_seg = max(0.0, min(start + v_seg, a_total) - start) if a_total > 0 else 0.0
    return v_seg, a_seg


def _audio_pad_needed(meta: Dict, fps: float,
                      seg_start: Optional[float] = None,
                      seg_dur: Optional[float] = None) -> Tuple[bool, float]:
    """判定是否需要为「保住全部视频帧」而把音频从流复制降级为重编码。

    返回 (需要否, 短多少秒)。

    背景：`-shortest`（复用源音轨时下发）在**音轨比视频短**时会在音频 EOF 处
    停止输出，而视频来自 `pipe:0`（父进程持续写入）⇒ ffmpeg 提前退出，父进程
    写 stdin 触发 BrokenPipe。实测音轨短 17s 的素材**整条转换失败**、短 0.06s
    的丢 3 帧。

    唯一的根因解是给音频补静音（`apad`），但 **`apad` 与 `-c:a copy` 不能共存**
    （ffmpeg 报 "Filtergraph 'apad' was specified, but codec copy was
    selected" ⇒ rc=234）⇒ 必须重编码。这就是本函数存在的理由。

    ⚠ 分段并行按**本段**区间判（见 `_segment_durations`）：音轨可能只在某段偏短，
    甚至某段完全没有音频。用全片口径判会同时产生漏判（⇒ 整条失败）与高估。
    """
    if fps <= 0:
        return False, 0.0
    # 全片音轨时长探测不到（mkv 常见）⇒ 无法判定，不动音频路径（保守：
    # 宁可漏判也不在没有依据时把用户的 copy 改掉）。
    if float(meta.get("audio_duration") or 0.0) <= 0:
        return False, 0.0
    v_dur, a_dur = _segment_durations(meta, seg_start, seg_dur)
    if v_dur <= 0:
        return False, 0.0
    short = v_dur - a_dur
    return (short * fps >= _AUDIO_PAD_MIN_FRAMES), max(0.0, short)


def build_encode_settings(args: argparse.Namespace, codec: Optional[str] = None,
                          preset: Optional[str] = None,
                          threads: Optional[int] = None,
                          quality: Optional[Tuple[Optional[int], Optional[int],
                                                  Optional[int]]] = None,
                          ) -> EncodeSettings:
    """把已校验的 args 装配成一次编码的 EncodeSettings。

    codec / preset / threads / quality 可覆盖：
      · 运行中降级（NVENC 失败→libx265）时换一套参数重试；
      · threads 由调用方传入 compute_parallelism 算出的**每任务线程预算**，否则
        `--threads 0`（自动）永远进不了命令、只在概览里显示一个不生效的数字；
      · quality 由 `quality_for_codec()` 按新编码器重算后传入（防质量值蒸发）。
    """
    c = (codec or args.codec).lower()
    p = preset or args.preset
    q_crf, q_cq, q_qp = quality if quality is not None else (args.crf, args.cq, args.qp)

    # 像素格式：SDR→HDR10 固定 10bit（--bit-depth 的 choices 只有 10）。
    pix_fmt = (_PIXFMT_BY_DEPTH.get(args.bit_depth, {}).get(c)
               or _DEFAULT_PIXFMT_BY_DEPTH[args.bit_depth])
    if is_8bit_only(c):
        # 正常到不了这里（hdr10_capability 已拒绝），仅防御：别让 8bit 编码器收 10bit
        pix_fmt = "yuv420p"

    return EncodeSettings(
        codec=c, crf=q_crf, cq=q_cq, qp=q_qp, preset=p, pix_fmt=pix_fmt,
        rc_mode=args.rc_mode, lookahead=args.lookahead, bitrate=args.bitrate,
        nvenc_tune=args.nvenc_tune, nvenc_multipass=args.nvenc_multipass,
        nvenc_aq=args.nvenc_aq,
        audio_codec=getattr(args, "audio_codec_effective", args.audio_codec),
        audio_bitrate=args.audio_bitrate,
        master_display=None if args.no_master_display else args.master_display,
        max_cll=args.max_cll or None,
        color_range=args.color_range,
        threads=args.threads if threads is None else max(0, int(threads)),
    )


def build_plan(args: argparse.Namespace, meta: Dict,
               src: Optional[Path] = None, dst: Optional[Path] = None,
               codec: Optional[str] = None, preset: Optional[str] = None,
               decode: Optional[str] = None,
               seg_start: Optional[float] = None,
               seg_dur: Optional[float] = None,
               threads: Optional[int] = None,
               quality: Optional[Tuple[Optional[int], Optional[int], Optional[int]]] = None,
               warn: Optional[Callable[[str], None]] = None) -> Dict:
    """
    算出对齐后的尺寸与两条 ffmpeg 命令（纯函数，dry-run 与真跑共用）。

    尺寸处理：模型下采样三次，宽高须为 8 的倍数。--mod-crop（默认开）向内裁到
    8 的倍数；关闭则原样交给模型，由其在第一次 stride 卷积处报错——那样报错信息
    埋在 torch traceback 里，很难定位，所以默认给出可读的裁剪提示。

    src/dst 缺省取 --input/--output（单文件场景）；批量场景由调用方按 job 传入。
    seg_start/seg_dur 用于分段并行，同时作用于视频解码与音频输入，保证音画区间一致。
    """
    src = Path(src) if src is not None else Path(args.input)
    dst = Path(dst) if dst is not None else Path(args.output)

    w, h = meta["width"], meta["height"]
    if w <= 0 or h <= 0:
        raise ValueError(f"源尺寸异常：{w}x{h}")
    ow, oh = w, h
    if args.mod_crop:
        ow, oh = w - (w % ALIGN), h - (h % ALIGN)
        if (ow, oh) != (w, h):
            print(f"[尺寸] {w}x{h} 不是 {ALIGN} 的倍数，"
                  f"向内裁到 {ow}x{oh}（模型下采样三次所致）")
    if ow < ALIGN or oh < ALIGN:
        raise ValueError(
            f"源尺寸 {w}x{h} 太小：裁剪后 {ow}x{oh}，"
            f"需宽高各至少 {ALIGN} 像素")

    fps = meta["fps"]
    st = build_encode_settings(args, codec=codec, preset=preset, threads=threads,
                               quality=quality)
    st.seg_start, st.seg_dur = seg_start, seg_dur

    # 分段时用段区间替代全局截断；非分段时保持 --duration / --frames 原语义。
    _seg = seg_start is not None
    _dec_dur = seg_dur if _seg else args.duration
    _dec_frames = None if _seg else args.frames

    # 音视频各自时长（供 build_encode_cmd 判断 `-shortest` 的截断风险）。
    # 分段时按**本段**区间算，不是全片 —— 告警要对应实际会丢的那一段。
    # ⚠ 这两个值同时是 build_encode_cmd 里「降级没生效」兜底告警的基准，
    #   口径必须与 _audio_pad_needed 一致（同一个 _segment_durations），
    #   否则会出现「build_plan 判要补、兜底告警却说短得更多/更少」。
    _seg_v_dur, _seg_a_dur = _segment_durations(
        meta, seg_start if _seg else None, seg_dur)
    st.audio_duration = _seg_a_dur
    st.video_duration = _seg_v_dur

    # ── 字幕（--subs）─────────────────────────────────────────────────
    # 三种模式（2026-09）：
    #   auto/none = 不保留，但**必须告警**（此前是静默丢，用户无从知道）；
    #   keep      = 透传，受容器/codec 限制，且与分段并行互斥；
    #   burn      = 烧进画面，需 libass。
    _warn = warn or _default_warn
    _subs = list(meta.get("subs") or [])
    _mode = getattr(args, "subs", "auto")
    st.subs_mode = _mode
    st.subs_index = int(getattr(args, "subs_index", 0) or 0)
    st.subs_map = None            # keep 用：'-map' 的第四个 spec
    st.subs_codec = None          # keep 用：None=copy

    if not _subs:
        if _mode in ("keep", "burn"):
            _warn(f"--subs {_mode} 但源没有字幕轨，已按 --subs none 处理"
                  f"（产物不含字幕）")
        _mode = "none"
        st.subs_mode = "none"

    if _subs:
        if st.subs_index >= len(_subs):
            _warn(f"--subs-index {st.subs_index} 越界（源只有 {len(_subs)} 条字幕轨，"
                  f"index {0}~{len(_subs) - 1}）；已退回 index 0")
            st.subs_index = 0
        _pick = _subs[st.subs_index]
        _lang = f"（{_pick['language']}）" if _pick.get("language") else ""

        if _mode == "auto":
            _warn(f"源含 {len(_subs)} 条字幕轨{_lang}，当前**不保留字幕**"
                  f"（默认行为）。字幕不会出现在产物里。\n"
                  f"    · 要保留：`--subs keep`（透传，受容器限制）"
                  f"或 `--subs burn`（烧进画面）；\n"
                  f"    · 选另一条轨：`--subs-index N`（默认 0=第一条）")
        elif _mode == "keep":
            # 与分段并行互斥：分段只对音频做时间区间切分，字幕是**单文件全局**的，
            # 各段独立编码后 concat 会让字幕与视频错位/丢失。
            #
            # ⚠ 判据必须覆盖**三种** split_mode 的实际后果，而不只是「显式 segment」。
            # 实测两个踩中的坑：
            # ① 只看 `_seg`（本次调用是否已带 seg_start）⇒ `--split-mode segment
            #    --frames 10` 时 build_plan 还没拿到 seg_start（分段要到运行时才由
            #    decide_single_file_mode 决定），互斥被跳过 ⇒ 走到 remux 才报
            #    「字幕 remux 失败 rc=234」，报错指向 remux 而非真正该改的地方；
            # ② 只拦显式 `segment` ⇒ `auto` 在**长素材上真会分段**
            #    （实测 22s + workers 2 → `分段并行：2 段 × 2 并发`），
            #    转换跑完才在运行期抛 ValueError，白跑一遍。
            # ⇒ 判据 = 「本次会不会分段」，三个取值都要算进去。
            _sm = getattr(args, "split_mode", "auto")
            _want_seg = _sm == "segment"
            if _sm == "auto":
                # auto 的分段前提（对齐 decide_single_file_mode）：单文件、
                # 素材够长、未给截断、且 workers>1（并发宽度 > 1 才可能分段）
                _dur0 = _frac_to_float(meta.get("duration")) or 0.0
                _want_seg = (not _seg
                            and _dur0 >= _SEGMENT_MIN_SECONDS
                            and getattr(args, "duration", None) is None
                            and getattr(args, "frames", None) is None
                            and int(getattr(args, "workers", 0) or 0) > 1)
            if _seg or _want_seg:
                raise ValueError(
                    f"--subs keep 与分段并行（--split-mode {_sm}）互斥："
                    f"分段只切音频区间，而字幕是全局单文件，各段独立编码再拼接"
                    f"会导致字幕错位。\n"
                    f"    · 改用 `--split-mode off`，或\n"
                    f"    · 用 `--subs burn`（字幕烧进画面，随视频帧走，不受影响）")
            if _pick.get("is_bitmap"):
                raise ValueError(
                    f"--subs-index {st.subs_index} 是**位图字幕**"
                    f"（{_pick['codec']}），无法转码进 {dst.suffix or '目标'} 容器"
                    f"（只能原样 copy，而 mp4/mov 不收位图字幕）。\n"
                    f"    · 输出到 .mkv 可原样保留；\n"
                    f"    · 或 `--subs burn` —— 但位图字幕**同样烧不了**"
                    f"（libass 只认文本字幕）；\n"
                    f"    · 或 `--subs-index` 选一条文本字幕。")
            _codec = resolve_subtitle_codec_for_container(
                dst.suffix or ".mp4", _pick["codec"], src, warn)
            st.subs_map = f"1:s:{st.subs_index}"
            st.subs_codec = _codec
            # ── 两阶段降级（仅在会触发 ffmpeg bug 时）──────────────
            # 见 probe_source 里的 stream_durations_known 注释：当源流拿不到自己的
            # duration（典型是 mkv）时，「音频+字幕+`-shortest`」共存会产出**畸形
            # 容器**（rc=0、体积几 KB、视频帧读不出）。那种源改成：
            #   阶段1 = 按无字幕编码（不含 `-map字幕`，`-shortest` 只管音视频）；
            #   阶段2 = `-c copy` 把字幕 remux 进产物（不重编码、画质无损）。
            st.subs_two_stage = not meta.get("stream_durations_known", True)
            if st.subs_two_stage:
                st.subs_map = None      # 阶段1 不map 字幕，故不发 -map/-c:s
                st.subs_codec = None
                _warn(f"源容器不提供各流时长（{dst.suffix or '目标'} 的 "
                      f"`-shortest` + 字幕共存会导致 ffmpeg 产出畸形文件，"
                      f"已实测）：已改用**两阶段** —— 先编码、再把字幕 `-c copy` "
                      f"挂进产物（不重编码、画质无损，多一次remux 开销）。")
        elif _mode == "burn":
            if _pick.get("is_bitmap"):
                raise ValueError(
                    f"--subs burn 不支持位图字幕（{_pick['codec']}）："
                    f"libass 只渲染**文本**字幕。\n"
                    f"    · 位图字幕请用 `--subs keep` 并输出 .mkv（可原样保留）。")
            if not _has_libass():
                raise ValueError(
                    "--subs burn 需要 ffmpeg 的 libass（subtitles/ass 滤镜），"
                    "但本机 ffmpeg 没有这两个滤镜。\n"
                    f"    · 可改用 `--subs keep`（若容器支持）；\n"
                    f"    · 或先换一个带 libass 的 ffmpeg 构建。")
            st.subs_burn_file = _export_subtitle(
                src, st.subs_index, work_sub_dir=(dst.parent / ".subs_burn"),
                tag=dst.stem)

    # ── 音轨偏短 ⇒ 补静音保住全部视频帧 ──
    # `-shortest` 在音轨比视频短时会截断视频，而补静音（`-af apad`）是唯一的根因解。
    # 这里对**任何音频路径**都生效，只要该路径**能加滤镜**（即非流复制）：
    #   · copy（默认）→ 降级为 aac（唯一根因解，apad 与 copy 不共存）；
    #   · 已显式给了非 copy 编码器（aac/libopus/…）→ 保留用户的选择，只加 apad。
    # ⚠ 早先只处理 copy，导致显式 `--audio-codec libopus` 的场景漏掉 apad，
    #   实测仍 EXIT=1 / BrokenPipe（用户以为选了重编码就安全了，其实没有）。
    _want_audio = (st.audio_codec != "none" and meta.get("has_audio"))
    if _want_audio:
        _pad, _short = _audio_pad_needed(meta, fps,
                                        seg_start if _seg else None,
                                        seg_dur if _seg else None)
        if _pad:
            _was_copy = st.audio_codec.lower() == "copy"
            if _was_copy:
                # ⚠ 降级目标必须**尊重容器限制**：WebM 只收 Opus/Vorbis，
                # 若这里选 aac，`build_encode_cmd` 里的
                # resolve_audio_codec_for_container 会再换一次 libopus ⇒
                # 多一轮无用替换，且告警文案会说「降级为 aac」而实际是 libopus
                # （实测过：文案与产物不一致）。故先问一次容器。
                _ext = normalize_container(args.container) or dst.suffix
                _cand = _AUDIO_FALLBACK_CODEC
                if (_ext or "").lower() == ".webm":
                    _cand = _WEBM_AUDIO_FALLBACK
                st.audio_codec = _cand
            st.audio_pad = True
            _lost = int(_short * fps)
            _tail = (f"    · 想维持流复制：`--audio none`（出无声版）或换素材。\n"
                     f"    · ⚠ **加 `--audio-codec copy` 并不能避免降级** —— "
                     f"流复制与 `-af apad` 不能共存（ffmpeg rc=234），"
                     f"保住视频帧只能重编码。")
            if _was_copy:
                _origin = (
                    f"已自动把音频从流复制降级为 {st.audio_codec}"
                    + ("（**你显式指定的 `--audio-codec copy` 同样会被降级**："
                       "它是默认值同一条路径，且保帧优先于音轨原样）"
                       if getattr(args, "audio_codec_explicit", False)
                       else "（默认音频策略）"))
            else:
                _origin = f"音频已按你指定的 {st.audio_codec} 重编码"
            (warn or _default_warn)(
                f"源音轨比视频短 {_short:.2f}s（约 {_lost} 帧）：`-shortest` 会在音频"
                f"结束处停止输出而丢掉这些帧。{_origin}，并补静音"
                f"（`-af apad`），视频帧全部保留。\n"
                f"    · 音频被重编码（非逐位保留），体积可能有微小变化。\n"
                + _tail)

    decode_cmd = build_decode_cmd(src, _dec_dur, _dec_frames,
                                  decode=decode or getattr(args, "decode_resolved", "cpu"),
                                  src_bits=meta.get("src_bits", 8),
                                  start=seg_start if _seg else None)
    encode_cmd = build_encode_cmd(ow, oh, fps, dst, src, st, meta["has_audio"],
                                  warn=warn or _default_warn,
                                  extra_args=getattr(args, "extra_args", None))
    # 两阶段（keep + 源流时长缺失）：阶段1 先写 `_stage1`，remux 成功才移到 dst。
    # 之所以要中间文件：`-c copy` 的 remux 必须以阶段1 的产物为输入，而 ffmpeg
    # 不允许输入与输出同路径。
    if st.subs_two_stage:
        # 契约：输出路径必须是最后一个 token（verify_sdr_to_hdr ③ 逐字断言）
        assert encode_cmd[-1] == str(dst), f"输出路径不在末尾：{encode_cmd[-1]!r}"
        _stage1 = dst.with_name(dst.stem + "_stage1" + dst.suffix)
        encode_cmd[-1] = str(_stage1)
        _subs_stage1 = str(_stage1)     # 由下方 return 带给执行层
    else:
        _subs_stage1 = ""

    # 展示用的 x265-params **从真正下发的命令里取**（单一真源）：
    # 之前用 build_x265_params(..., 无 extra) 单独构造，漏掉了 rc-lookahead 等键，
    # 于是计划面板显示的和实发命令不一致（可核对性上的假绿）。
    _xp = None
    if "-x265-params" in encode_cmd:
        _i = encode_cmd.index("-x265-params")
        if _i + 1 < len(encode_cmd):
            _xp = encode_cmd[_i + 1]

    return {
        "src_width": w, "src_height": h,
        "out_width": ow, "out_height": oh,
        "fps": fps,
        "settings": st,
        "seg_start": seg_start, "seg_dur": seg_dur,
        "total_frames": int(meta.get("nb_frames") or 0),
        "x265_params": _xp,
        "decode": decode or getattr(args, "decode_resolved", "cpu"),
        "decode_cmd": decode_cmd, "encode_cmd": encode_cmd,
        # 仅两阶段（--subs keep + 源流时长缺失）时非空，指向阶段1 的产物路径；
        # 执行层据此在编码成功后做字幕 remux（见 _run_job_with_fallback）。
        "subs_stage1": _subs_stage1,
    }


def _quality_desc(st: EncodeSettings) -> str:
    """把 EncodeSettings 的质量轴描述成一行人类可读文本。"""
    bits = []
    if st.cq is not None:
        bits.append(f"CQ {st.cq}")
    if st.crf is not None:
        if st.codec == "librav1e":
            bits.append(f"QP {crf_to_rav1e_qp(st.crf)}")
        else:
            bits.append(f"CRF {st.crf}")
    if st.qp is not None:
        bits.append(f"QP {st.qp}")
    if not bits:
        bits.append("默认质量")
    if st.bitrate:
        bits.append(f"码率 {st.bitrate}")
    if st.rc_mode != "auto":
        bits.append(f"rc={st.rc_mode}")
    if st.lookahead is not None:
        bits.append(f"lookahead {st.lookahead}")
    if st.nvenc_aq:
        bits.append("AQ")
    if st.nvenc_tune:
        bits.append(f"tune={st.nvenc_tune}")
    if st.nvenc_multipass:
        bits.append(f"multipass={st.nvenc_multipass}")
    return "  ".join(bits)


def print_plan(args: argparse.Namespace, plan: Dict, meta: Dict,
               src: Optional[Path] = None, dst: Optional[Path] = None) -> None:
    st = plan["settings"]
    print("── 阶段计划 ──")
    print(f"  源        : {src or args.input}")
    print(f"  输出      : {dst or args.output}")
    print(f"  尺寸      : {plan['src_width']}x{plan['src_height']}"
          + (f" → {plan['out_width']}x{plan['out_height']}"
             if (plan['out_width'], plan['out_height'])
             != (plan['src_width'], plan['src_height']) else ""))
    print(f"  帧率      : {plan['fps']:.6f} fps")
    print(f"  源标签    : {meta['color_space'] or '-'} / "
          f"{meta['color_primaries'] or '-'} / {meta['color_transfer'] or '-'} "
          f"（{meta['src_bits']}bit {meta['pix_fmt']}）")
    if args.duration or args.frames:
        print(f"  截断      : "
              + (f"前 {args.duration}s" if args.duration else "")
              + (f" 前 {args.frames} 帧" if args.frames else ""))
    if args.no_model:
        print("  推理      : 跳过（--no-model，仅编码链路）")
    else:
        print(f"  推理      : Ensemble_AGCM_LE，设备 {args.device}"
              + (f"，分块 {args.tile}+{args.tile_overlap}"
                 if args.tile > 0 else "，整帧"))
    print(f"  解码      : {plan['decode']}"
          + ("（硬件解码，hwdownload 回 CPU 后转 gbrp16le）"
             if plan["decode"] == "cuda" else "（软件解码）"))
    print(f"  编码      : {st.codec}  preset {st.preset}  "
          f"{_quality_desc(st)}  pix_fmt {st.pix_fmt or '自动'}")
    print(f"  HDR10     : bt2020nc / smpte2084 / {st.color_range}，"
          f"x265-params={plan['x265_params'] or '(无)'}")
    print(f"  音频      : {st.audio_codec}"
          + ("" if meta["has_audio"] else "（源无音轨）"))
    print()
    print("── ffmpeg 命令 ──")
    print("① 解码 →")
    print("  命令: " + shlex.join(plan["decode_cmd"]))
    print("② 编码 →")
    print("  命令: " + shlex.join(plan["encode_cmd"]))


def _read_exact(stream, n: int) -> bytes:
    """从二进制流精确读满 n 字节；EOF 提前则返回已读到的部分。"""
    chunks: List[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _spawn_stderr_drain(proc: subprocess.Popen, sink: List[str],
                        cap: int = 500) -> threading.Thread:
    """给子进程的 stderr 起一个**守护线程**持续排空，返回该线程。

    ⚠ 这是硬需求而不是优化：两个 ffmpeg 的 stderr 都开了 PIPE，若父进程在帧循环
    期间不读它，只要任一子进程写满管道（默认 64KiB），它就会阻塞在 write(stderr)
    → 不再读 stdin / 不再产 stdout → 父进程随之永久阻塞。触发条件并不罕见：
    ① 用户经 `--extra-args` 提高日志级别（追加在命令末尾会覆盖脚本的
    `-loglevel error`）；② 损坏源在 error 级别下仍吐出大量报错行。
    （2026-10-08 实测：720p + `-loglevel debug` 约 108s 时管道积压 61KB 后整条卡死，
    父进程 40s+ 零进展。）孪生脚本 vidcrop_cpu_v2.py 的 `_drain_stderr` 就是干这个的。
    """
    def _drain() -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                sink.append(raw.decode("utf-8", "replace").rstrip())
                if len(sink) > cap:
                    del sink[:cap // 2]
        except Exception:
            pass

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    return t


def run_job(args: argparse.Namespace, job: Job, plan: Dict,
            engine: Optional[HdrTvNetPlus],
            on_update: Optional[Callable[[Job], None]] = None) -> int:
    """
    跑一个文件的 ①→②→③。返回 0 成功 / 1 失败。

    用 stdin/stdout 管道连成一条链：解码 ffmpeg 的 stdout 逐帧读进 Python，
    推理后写进编码 ffmpeg 的 stdin。两个子进程都不落中间文件，磁盘零额外占用。
    每处理若干帧把进度写回 job 并回调 on_update（进度条 / 聚合面板用）。

    三条**必须保留**的健壮性措施（2026-10-08 对抗审查发现并补上）：
      · 两个子进程的 stderr 各起一个守护线程排空 —— 否则日志写满 64KiB 管道即死锁；
      · `except Exception` + `finally` 无条件收尾 —— 否则一次非管道异常会留下两个
        孤儿 ffmpeg、并让整批任务中断（孪生脚本都有 `except Exception` + `finally`）；
      · 末帧不满只丢弃不报错的话，**截断产物会被当成功交付** —— 现在判失败。
    """
    w, h = plan["out_width"], plan["out_height"]
    frame_bytes = w * h * PLANES * BYTES_PER_SAMPLE

    total = None
    if args.frames:
        total = args.frames
    elif plan.get("seg_start") is not None and plan["fps"] > 0:
        # 分段并行：期望帧数按**本段**算。末段 seg_dur=None（跑到结尾），此时不能退回
        # 全片帧数 —— 那会把"末段只有剩余那几十帧"误判成"输出不完整"。
        if plan.get("seg_dur"):
            total = max(1, int(round(float(plan["seg_dur"]) * plan["fps"])))
        elif plan.get("total_frames"):
            total = max(1, int(plan["total_frames"])
                        - int(round(float(plan["seg_start"]) * plan["fps"])))
    elif args.duration and plan["fps"] > 0:
        total = max(1, int(round(args.duration * plan["fps"])))
    elif plan.get("total_frames"):
        # 整条处理（未截断）：用源探测到的帧数，否则进度条会全程停在 0.0%
        total = int(plan["total_frames"])
    job.total_frames = total or 0

    env = _ffmpeg_env()
    dec = subprocess.Popen(plan["decode_cmd"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           stdin=subprocess.DEVNULL, bufsize=0)
    _register_proc(dec)
    enc = subprocess.Popen(plan["encode_cmd"], stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           env=env, bufsize=0)
    _register_proc(enc)
    dec_sink: List[str] = []
    enc_sink: List[str] = []
    dec_t = _spawn_stderr_drain(dec, dec_sink)
    enc_t = _spawn_stderr_drain(enc, enc_sink)

    n_done = 0
    truncated = False
    t0 = time.time()
    last_cb = 0.0
    rc = 1
    finished_normal = False
    try:
        while True:
            buf = _read_exact(dec.stdout, frame_bytes)
            if len(buf) < frame_bytes:
                if buf:
                    truncated = True
                    print(f"[WARN] 末帧不完整（{len(buf)}/{frame_bytes} 字节），丢弃",
                          file=sys.stderr)
                break
            if total is not None and n_done >= total:
                break

            frame = raw_to_frame(buf, w, h)
            if engine is not None:
                out_frame = engine.enhance(frame, tile=args.tile,
                                           tile_overlap=args.tile_overlap)
            else:
                out_frame = sdr_frame_to_pq(frame)

            enc.stdin.write(frame_to_raw(out_frame))
            n_done += 1
            job.frame = n_done

            now = time.time()
            el = now - t0
            if el > 0:
                job.fps = n_done / el
                if job.fps > job.peak_fps:
                    job.peak_fps = job.fps
            if total:
                job.progress = min(1.0, n_done / total)
            if on_update and (now - last_cb >= 0.25 or (total and n_done == total)):
                last_cb = now
                on_update(job)

        # ── 正常收尾：关 stdin 让编码端 flush，再等两侧退出码 ──
        try:
            enc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        enc_t.join(timeout=5.0)
        dec_t.join(timeout=5.0)
        enc_rc = enc.wait()
        dec_rc = dec.wait()
        job.elapsed = time.time() - t0
        finished_normal = True

        if dec_rc != 0:
            job.error = (f"解码 ffmpeg 退出码 {dec_rc}: "
                         f"{chr(10).join(dec_sink[-8:]).strip()[:400]}")
            rc = 1
        elif enc_rc != 0:
            job.error = (f"编码 ffmpeg 退出码 {enc_rc}: "
                         f"{chr(10).join(enc_sink[-8:]).strip()[:400]}")
            rc = 1
        elif truncated or (total and n_done < total - max(2, int(total * 0.01))):
            # 数据不完整就不算成功：源被截断 / 实际解码帧数明显少于预期。
            # 容差 max(2 帧, 1%)：容器自报的 nb_frames 与按 duration×fps 估出来的值
            # 都可能差一两帧，分段边界取整也会差 —— 这些不该被判成失败。
            job.error = (f"输出不完整：期望 {total or '?'} 帧、实际 {n_done} 帧"
                         + ("（末帧不完整）" if truncated else "")
                         + "；源可能被截断，产物已生成但不视为成功")
            rc = 1
        else:
            if total and n_done < total:
                _job_warn(job, f"实际输出 {n_done} 帧、源自报 {total} 帧"
                               f"（差额在容差内，可能是容器自报值不精确）")
            rc = 0
    except BrokenPipeError:
        job.error = "编码端提前退出（管道断开）"
        rc = 1
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        job.error = f"处理出错：{type(exc).__name__}: {exc}"
        rc = 1
    finally:
        # 只要没走到正常收尾，就无条件终止子进程并注销 —— 避免孤儿 ffmpeg
        if not finished_normal:
            try:
                enc.stdin.close()
            except Exception:
                pass
            _terminate(dec, enc)
            job.elapsed = time.time() - t0
        _unregister_proc(dec)
        _unregister_proc(enc)
        for _t in (dec_t, enc_t):
            try:
                _t.join(timeout=1.0)
            except Exception:
                pass
        # burn 导出的字幕是**临时文件**：编码端在本次任务里已经读完它，
        # 留着只会在每个输出目录攒下一堆 .srt（批量跑一遍就是 N 个）。
        # ⚠ 只删自己导出的那一个文件 + 目录（若空），不碰别人的。
        # ⚠ 本函数里没有 `st`，设置在 `plan["settings"]`（见函数开头）。
        _burn_file = plan.get("settings").subs_burn_file \
            if plan.get("settings") else ""
        if _burn_file:
            try:
                _bf = Path(_burn_file)
                if _bf.is_file():
                    _bf.unlink()
                _bd = _bf.parent
                if _bd.is_dir() and _bd.name == ".subs_burn" \
                        and not any(_bd.iterdir()):
                    _bd.rmdir()
            except Exception:
                pass        # 清理失败不该影响转换结果
    return rc


def _run_job_with_fallback(args: argparse.Namespace, job: Job, meta: Dict,
                           engine: Optional[HdrTvNetPlus],
                           on_update: Optional[Callable[[Job], None]] = None) -> int:
    """按「NVENC 降档 → CPU 等价物」的重试阶梯跑一个文件。

    阶梯（仅在 auto 策略下）：
      ① 原参数；② 若 NVENC 且 preset ∈ {p5,p6,p7}，降档重试（_NVENC_PRESET_RETRY，
      实测这几个档偶尔初始化失败）；③ 若仍是硬件编码器，换 libx265（preset 按
      NVENC_TO_X264_PRESET 换算，保证降级前后速度档等效）。
    ⚠ GPU 路径无法在本机验证（无 GPU），该阶梯属「待上机」项。
    """
    codec = args.codec
    preset = args.preset
    base_decode = getattr(args, "decode_resolved", "cpu")
    ladder = [(codec, preset, base_decode)]
    if codec in NVENC_CODECS and preset in _NVENC_PRESET_RETRY:
        ladder.append((codec, _NVENC_PRESET_RETRY[preset], base_decode))
    if codec in _HW_ENCODERS and args.fallback_policy != "strict":
        ladder.append(("libx265", normalize_preset(preset, "libx265"), base_decode))
    if base_decode == "cuda" and args.fallback_policy != "strict":
        # 「硬件解码运行中失败也自动降级」：启动前的探针只能证明"能解一帧"，
        # 跑到中途驱动出错时靠这一档兜住（文档里承诺过的行为）。
        _last = ladder[-1]
        ladder.append((_last[0], _last[1], "cpu"))

    last_rc = 1
    for i, (c, p, dec) in enumerate(ladder):
        try:
            plan = build_plan(args, meta, src=job.src, dst=job.dst,
                              codec=c, preset=p, decode=dec, threads=job.threads,
                              quality=(quality_for_codec(args, c) if c != codec else None),
                              warn=lambda m, _j=job: _job_warn(_j, m))
        except ValueError as exc:
            job.error = str(exc)
            return 1
        if i > 0:
            print(f"  ↻ 重试：编码器 {c} preset {p} 解码 {dec}"
                  + ("（运行中降级）" if c != codec or dec != base_decode
                     else "（降档重试）"))
        last_rc = run_job(args, job, plan, engine, on_update=on_update)
        if last_rc == 0:
            # ── 字幕阶段2（仅 keep + 源流时长缺失时）─────────────────
            # run_job 把阶段1 产物写在 `subs_stage1`；这里用 `-c copy` 把字幕
            # remux 进最终路径（不重编码 ⇒ 画质无损），成功后才替换成品。
            # ⚠ 放在这里而不是 run_job 内：只有本层知道 job.dst（最终路径）。
            if plan.get("subs_stage1"):
                _st1 = Path(plan["subs_stage1"])
                try:
                    _rx = build_subs_remux_cmd(
                        _st1, job.src, job.dst, plan["settings"].subs_index,
                        plan["settings"].subs_codec)
                    _r = subprocess.run(_rx, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=900, check=False)
                    if _r.returncode != 0 or not job.dst.exists():
                        raise RuntimeError(
                            f"字幕 remux 失败 rc={_r.returncode}："
                            f"{(_r.stderr or '').strip()[:200]}")
                    _st1.unlink(missing_ok=True)
                except Exception as _ex:
                    job.status = "failed"
                    job.error = f"字幕 remux 阶段失败：{_ex}"
                    # ⚠ 阶段1 的产物**留在这个路径**（实测 96KB），它其实是可用的
                    #   ——只是没挂上字幕。不清理是故意的（用户可手工改名取用），
                    #   但**必须说出来**，否则它会变成一个来源不明的孤儿文件，
                    #   而最终路径上什么都没有 ⇒ 看起来像「转好了但文件没了」。
                    if _st1.exists():
                        job.warnings.append(
                            f"阶段1 产物已保留在 {_st1}（可用，只是没挂字幕）")
                    return 1
            job.plan = plan
            return 0
        if _STOP_REQUESTED.is_set():
            return last_rc
        if i + 1 < len(ladder):
            print(f"  ⚠ 第 {i + 1} 次尝试失败（{job.error}），按阶梯降级重试……",
                  file=sys.stderr)
    return last_rc


def _prepare_job(args: argparse.Namespace, job: Job) -> bool:
    """探测源、构建计划，写回 job.info；失败时置 job.status=failed 并返回 False。"""
    try:
        meta = probe_source(job.src)
    except ValueError as exc:
        job.status = "failed"
        job.error = f"探测失败：{exc}"
        return False
    job.info = meta
    return True


def run_sequential(args: argparse.Namespace, jobs: List[Job],
                   engine: Optional[HdrTvNetPlus], threads: int,
                   engine_factory=None, workers: int = 1) -> None:
    sizes = [_file_bytes(j.src) for j in jobs]
    queue = QueueETA(sizes) if len(jobs) > 1 else None
    n_pending = sum(1 for j in jobs if j.status == "pending")

    for idx, job in enumerate(jobs, 1):
        if _STOP_REQUESTED.is_set():
            break
        if queue is not None and idx > 1:
            prev = jobs[idx - 2]
            queue.add(sizes[idx - 2], prev.elapsed)
            finished = jobs[:idx - 1]
            print("  " + _label("队列进度") + queue.line(
                idx - 1, sum(j.elapsed for j in finished),
                sum(1 for j in finished if j.status == "done"),
                sum(1 for j in finished if j.status == "failed"),
                sum(1 for j in finished if j.status == "skipped")))

        print(f"\n[{idx}/{len(jobs)}] {job.name}")
        if job.status == "skipped":
            print(f"  ⏭  跳过：{job.error or '已存在，--overwrite 可覆盖'}")
            continue

        if not _prepare_job(args, job):
            print(f"  ✘ 失败：{job.error}")
            continue
        job.warn_cb = lambda m: print(f"  警告：{m}", file=sys.stderr)
        job.threads = threads

        job.status = "running"
        mode = decide_single_file_mode(args, job.info, n_pending, workers)
        if mode != "off":
            print(f"  单文件并行 : {mode}"
                  + (f"（{workers} 路）" if mode == "segment" else f"（{workers} 进程）"))
        q_rest, q_cur = queue.split(sizes[idx - 1]) if queue else (None, None)
        progress = SingleProgress(job, q_rest, q_cur)
        rc = run_one_job(args, job, job.info, mode, workers, threads,
                         engine, engine_factory or (lambda: engine),
                         on_update=progress.update)
        progress.finish()

        if rc == 0:
            job.status = "done"
            job.progress = 1.0
            print(f"  ✔ 完成，用时 {_fmt_time(job.elapsed)}")
            print(f"  {_label('输出文件')}{job.dst}")
            print(f"  {_label('大小变化')}{_size_change(job.src, job.dst)}")
            print_structural_diff(job.src, job.dst, warn=None)
        else:
            job.status = "failed"
            job.error = "interrupted" if _STOP_REQUESTED.is_set() else job.error
            print(f"  ✘ 失败：{job.error}")
        if _STOP_REQUESTED.is_set():
            break


def run_parallel(args: argparse.Namespace, jobs: List[Job],
                 engine_factory, workers: int, threads: int) -> None:
    """多文件并行（线程池）。engine_factory() 给每个 worker 一份推理引擎。

    ⚠ 每个 worker 各自持一份 torch 模型：CPU 推理时是内存翻倍，CUDA 推理时还会各占
    一份 CUDA 上下文，故 compute_parallelism() 在 gpu_infer 下默认把并发压到 1。
    """
    panel = AggregatePanel(jobs)
    panel.start()

    pending = [j for j in jobs if j.status == "pending"]
    for j in jobs:
        if j.status == "skipped":
            panel.emit(f"⏭  跳过 {j.name} ({j.error or '已存在，--overwrite 可覆盖'})")

    # 引擎按**执行线程**分配（threading.local），不是按任务序号：
    # 按序号（i % workers）分配时，一个短文件跑完后取出的下一个任务可能落到仍在运行
    # 的同一槽位 ⇒ 两个线程同时对同一个 torch 模块做前向（未加锁、不保证线程安全）。
    _tls = threading.local()

    def _worker() -> Optional[HdrTvNetPlus]:
        eng = getattr(_tls, "engine", None)
        if eng is None:
            eng = engine_factory()
            _tls.engine = eng
        return eng

    def _execute(job: Job) -> None:
        if not _prepare_job(args, job):
            panel.emit(f"✘ 失败 {job.name}: {job.error}")
            return
        job.warn_cb = lambda m: panel.emit(f"⚠ {m}")
        job.threads = threads
        job.status = "running"
        panel.emit(f"▶ 启动 {job.name}  (threads={threads})")
        try:
            eng = _worker()
        except Exception as exc:
            job.status = "failed"
            job.error = f"推理引擎初始化失败：{exc}"
            panel.emit(f"✘ 失败 {job.name}: {job.error}")
            return
        rc = _run_job_with_fallback(args, job, job.info, eng)
        if rc == 0:
            job.status = "done"
            job.progress = 1.0
            panel.emit(f"✔ 完成 {job.name}  用时 {_fmt_time(job.elapsed)}  "
                       f"大小 {_size_change(job.src, job.dst)}")
        else:
            job.status = "failed"
            job.error = "interrupted" if _STOP_REQUESTED.is_set() else job.error
            panel.emit(f"✘ 失败 {job.name}: {job.error}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {ex.submit(_execute, j): j for j in pending}
            for fut in as_completed(fut_map):
                job = fut_map[fut]
                try:
                    fut.result()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    job.status = "failed"
                    job.error = f"内部错误: {exc}"
                    panel.emit(f"✘ 失败 {job.name}: {job.error}")
    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        raise
    finally:
        panel.stop()


def _terminate(*procs) -> None:
    for p in procs:
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════════
#  单文件并行（--split-mode）
#
#  两条路径（仓主 2026-10-08 拍板：两条都做，用 CLI 参数区分）：
#    · segment —— 按时间切 N 段，每段独立跑「解码→推理→编码」，最后 concat 拼接。
#      实现直接、能线性加速；代价是段边界各段独立编码（码率分配非全局最优），
#      且需临时文件。切点不必落在关键帧：`-ss` 放在 -i 之前会先跳关键帧再解码到
#      精确时间点，输出仍是精确切分。
#    · workers —— 单解码进程持续出帧，N 个推理**进程**有序消费（imap 保序），单编码
#      进程收帧。无段边界问题、码率全局最优；代价是要走进程间传原始帧（每帧
#      w*h*6 字节），且每个进程各持一份 torch 模型（内存 ×N）。
#  auto —— 多文件→文件级并行；单文件且够长→segment；否则不做。
# ═══════════════════════════════════════════════════════════════════

# auto 判定"够长值得切段"的阈值：太短的片子切完每段只有几帧，拼接开销占比过高。
_SEGMENT_MIN_SECONDS = 20.0
_SEGMENT_MAX_PARTS = 16


def is_big_enough_to_segment(meta: Dict) -> bool:
    try:
        return float(meta.get("duration") or 0.0) >= _SEGMENT_MIN_SECONDS
    except (TypeError, ValueError):
        return False


def resolve_infer_device(args: argparse.Namespace) -> str:
    """实际推理设备：cpu / cuda（把 --device auto 解析开，供并行策略判断）。"""
    if args.no_model:
        return "cpu"
    if args.device == "cpu":
        return "cpu"
    if args.device == "cuda":
        return "cuda"
    return "cuda" if check_torch_cuda()[0] else "cpu"


def decide_single_file_mode(args: argparse.Namespace, meta: Dict,
                            n_pending: int, workers: int) -> str:
    """返回本文件应走的路径：'off' | 'segment' | 'workers'。"""
    if args.split_mode == "off":
        return "off"
    if n_pending > 1:
        # 多文件优先做文件级并行：再切单文件会把 worker 抢成两半，反而更慢
        return "off"
    if args.duration is not None or args.frames is not None:
        # 截断语义下分段会与 --frames/--duration 互相干扰，退化为整条处理
        if args.split_mode in ("segment", "workers"):
            print(f"  ⚠ 显式请求 --split-mode {args.split_mode}，但同时给了 "
                  f"--duration/--frames（截断语义与分段冲突），已按整条处理。",
                  file=sys.stderr)
        return "off"
    if workers <= 1:
        return "off"

    if args.split_mode == "workers":
        if args.no_model:
            print("提示：--split-mode workers 需要神经网络推理，--no-model 下无意义，"
                  "已按整条处理。")
            return "off"
        if resolve_infer_device(args) == "cuda":
            print("  ⚠ --split-mode workers 在 CUDA 推理下不可用（多进程各持一份 CUDA "
                  "上下文易崩且显存翻倍）；建议 --split-mode segment 或 --device cpu。"
                  "已按整条处理。", file=sys.stderr)
            return "off"
        return "workers"

    if args.split_mode == "segment":
        return "segment"
    # auto
    return "segment" if is_big_enough_to_segment(meta) else "off"


def _split_ranges(duration: float, n: int) -> List[Tuple[Optional[float], Optional[float]]]:
    """把 [0, duration] 均分成 n 段，返回 [(start, dur), ...]（末段 dur=None 表示到结尾）。"""
    if n <= 1 or not duration or duration <= 0:
        return [(None, None)]
    step = duration / n
    return [(i * step, (step if i < n - 1 else None)) for i in range(n)]


def run_job_segmented(args: argparse.Namespace, job: Job, meta: Dict,
                      nseg: int, engine_factory, threads: int,
                      on_update: Optional[Callable[[Job], None]] = None) -> int:
    """按时间切段并行处理后拼接（--split-mode segment）。

    ⚠ 每段各自独立编码 ⇒ 码率分配不是全局最优（这是本路径**已知的取舍**）；
    段边界处也可能有极小的质量接缝。要在意这两点请用 --split-mode workers
    （单解码+多推理+单编码，码率全局最优）——代价是进程间传帧与内存 ×N。
    """
    nseg = max(2, min(nseg, _SEGMENT_MAX_PARTS))
    duration = float(meta.get("duration") or 0.0)
    ranges = _split_ranges(duration, nseg)
    if len(ranges) < 2:
        # 退化（时长探测不到 ⇒ 切不出两段）：必须**照常跑推理**。
        # 此前这里直接传 engine=None，会静默退化成"只做 SDR→PQ 占位"——正是本仓
        # 反复强调的假绿：产物看着是 HDR10，其实没跑网络。
        try:
            _eng = engine_factory()
        except Exception as exc:
            job.error = f"推理引擎初始化失败：{exc}"
            return 1
        return _run_job_with_fallback(args, job, meta, _eng, on_update)

    # ⚠ 本函数的用时/帧率必须自己累计：分段路径不经过 run_job 的成功收尾，
    # 此前 job.elapsed 始终留在 0.0 —— 汇总里的「累计用时 0.0s」是错的，
    # 而且均速（需要 total_time>0）会整条缺省。
    t_seg = time.time()

    tmpdir = job.dst.parent / f".hdrseg_{job.dst.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    seg_paths = [tmpdir / f"seg{i:03d}{job.dst.suffix}" for i in range(len(ranges))]

    engines: List[Optional[HdrTvNetPlus]] = [None] * len(ranges)
    eng_lock = threading.Lock()
    prog_lock = threading.Lock()
    seg_jobs = [Job(src=job.src, dst=p, status="running") for p in seg_paths]
    ok_flags = [False] * len(ranges)

    def _engine_for(slot: int):
        with eng_lock:
            if engines[slot] is None:
                engines[slot] = engine_factory()
            return engines[slot]

    def _bump():
        with prog_lock:
            done = sum(s.progress for s in seg_jobs) / len(seg_jobs)
            job.progress = done
            job.fps = sum(s.fps for s in seg_jobs if s.fps > 0)
            if job.fps > job.peak_fps:
                job.peak_fps = job.fps
            if on_update:
                on_update(job)

    def _one(i: int) -> None:
        start, dur = ranges[i]
        sj = seg_jobs[i]
        try:
            eng = _engine_for(i)
        except Exception as exc:
            sj.error = f"推理引擎初始化失败：{exc}"
            return
        plan = build_plan(args, meta, src=job.src, dst=seg_paths[i],
                          seg_start=start, seg_dur=dur, threads=job.threads,
                          warn=lambda m, _j=job: _job_warn(_j, m))
        rc = run_job(args, sj, plan, eng, on_update=lambda _j: _bump())
        ok_flags[i] = (rc == 0)
        if rc != 0:
            sj.error = sj.error or "段处理失败"

    print(f"  ⚙ 分段并行：{len(ranges)} 段 × {min(len(ranges), max(1, nseg))} 并发")
    try:
        with ThreadPoolExecutor(max_workers=min(len(ranges), max(1, nseg))) as ex:
            list(ex.map(_one, range(len(ranges))))
    except KeyboardInterrupt:
        _terminate_active_procs()
        # 中断也要清临时段文件（否则 GB 级 .hdrseg_* 残留在输出目录，且因
        # mkdir(exist_ok=True) 下次不会自愈）
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    if not all(ok_flags):
        bad = next((i for i, ok in enumerate(ok_flags) if not ok), 0)
        job.error = seg_jobs[bad].error or "分段处理失败"
        job.progress = 1.0
        job.elapsed = time.time() - t_seg
        shutil.rmtree(tmpdir, ignore_errors=True)
        return 1

    # 拼接：各段参数完全一致，直接 -c copy（HDR10 静态元数据随流保留）
    listfile = tmpdir / "concat.txt"
    listfile.write_text("".join(f"file '{p.name}'\n" for p in seg_paths),
                        encoding="utf-8")
    concat_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-f", "concat", "-safe", "0", "-i", str(listfile),
                  "-c", "copy"]
    if job.dst.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        concat_cmd += ["-movflags", "+faststart"]
    concat_cmd += [str(job.dst)]

    rc = 0
    try:
        r = subprocess.run(concat_cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800,
                           check=False, env=_ffmpeg_env())
        if r.returncode != 0:
            job.error = f"拼接失败（rc={r.returncode}）：{(r.stderr or '').strip()[:400]}"
            rc = 1
    except Exception as exc:
        job.error = f"拼接失败：{exc}"
        rc = 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    job.elapsed = time.time() - t_seg
    if rc == 0:
        job.progress = 1.0
        # 帧率按「总帧数 / 总用时」给（并行段的 fps 相加没有意义）
        n_frames = sum(s.frame for s in seg_jobs)
        job.frame = n_frames
        if job.elapsed > 0 and n_frames > 0:
            job.fps = n_frames / job.elapsed
            job.peak_fps = max(job.peak_fps, job.fps)
    return rc


# ── workers 模式：多进程有序推理 ─────────────────────────────────────
_MP_ENGINE = None
_MP_ARGS: Optional[Tuple[int, int, int, int]] = None


def _mp_init(model_repo: str, device: str, torch_threads: int,
             tile: int, tile_overlap: int, w: int, h: int) -> None:
    """子进程初始化：各进程独立加载一份模型（内存 ×N，见文件头说明）。"""
    global _MP_ENGINE, _MP_ARGS
    _MP_ARGS = (tile, tile_overlap, w, h)
    _MP_ENGINE = HdrTvNetPlus(Path(model_repo), device=device,
                              threads=torch_threads, verbose=False)


def _mp_infer(buf: bytes) -> bytes:
    tile, tile_overlap, w, h = _MP_ARGS
    frame = raw_to_frame(buf, w, h)
    out = _MP_ENGINE.enhance(frame, tile=tile, tile_overlap=tile_overlap)
    return frame_to_raw(out)


def run_job_multiproc(args: argparse.Namespace, job: Job, meta: Dict,
                      nproc: int,
                      on_update: Optional[Callable[[Job], None]] = None) -> int:
    """单解码 + N 推理进程 + 单编码（--split-mode workers）。

    两个**必须遵守**的实现约束（本机实测踩过，2026-10-08）：
      ① 用 **spawn** 而不是 fork。父进程若已 import torch / 加载过模型，fork 出来的
         子进程会带着已初始化的 OpenMP 线程状态，实测直接挂死（2 分钟无任何输出）。
      ② **先建进程池、再起 ffmpeg 子进程**。反过来（先 Popen 解码/编码再 fork）会让
         子进程继承 enc.stdin 的写端副本，父进程关掉 stdin 后编码端仍等不到 EOF，
         ffmpeg 永不退出。
    另外调用方在 workers 模式下**不要**在父进程预加载模型（见 main）。
    """
    import multiprocessing as mp

    plan = build_plan(args, meta, src=job.src, dst=job.dst,
                      threads=job.threads,
                      warn=lambda m, _j=job: _job_warn(_j, m))
    w, h = plan["out_width"], plan["out_height"]
    frame_bytes = w * h * PLANES * BYTES_PER_SAMPLE

    total = None
    if meta.get("duration") and plan["fps"] > 0:
        total = max(1, int(round(float(meta["duration"]) * plan["fps"])))
    job.total_frames = total or 0

    env = _ffmpeg_env()
    n_done = 0
    t0 = time.time()
    last_cb = 0.0
    print(f"  ⚙ 单解码 + {nproc} 推理进程 + 单编码（spawn）")

    ctx = mp.get_context("spawn")
    try:
        pool = ctx.Pool(processes=nproc, initializer=_mp_init,
                        initargs=(args.model_repo, resolve_infer_device(args),
                                  args.torch_threads, args.tile, args.tile_overlap,
                                  w, h))
    except Exception as exc:
        job.error = f"推理进程池创建失败：{exc}"
        return 1

    # 池已建好，此时才起 ffmpeg（约束 ②）
    dec = subprocess.Popen(plan["decode_cmd"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env,
                           stdin=subprocess.DEVNULL, bufsize=0)
    _register_proc(dec)
    enc = subprocess.Popen(plan["encode_cmd"], stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           env=env, bufsize=0)
    _register_proc(enc)
    dec_sink: List[str] = []
    enc_sink: List[str] = []
    dec_t = _spawn_stderr_drain(dec, dec_sink)
    enc_t = _spawn_stderr_drain(enc, enc_sink)

    def _frames():
        while True:
            buf = _read_exact(dec.stdout, frame_bytes)
            if len(buf) < frame_bytes:
                return
            yield buf

    # 有界窗口：Pool.imap 的**结果侧没有消费端背压**（_handle_results 会把结果持续
    # 抽进无界的 deque），而 1080p 一帧 gbrp16le ≈ 11.9MiB —— 编码吞吐低于推理聚合
    # 吞吐时会积压数 GB 甚至被 OOM kill。用信号量把在飞帧数压在 ~2×nproc。
    in_flight = threading.BoundedSemaphore(max(2, nproc * 2))

    def _frames_bounded():
        for buf in _frames():
            in_flight.acquire()
            yield buf

    rc = 0
    try:
        for out_bytes in pool.imap(_mp_infer, _frames_bounded(), chunksize=1):
            enc.stdin.write(out_bytes)
            in_flight.release()
            n_done += 1
            job.frame = n_done
            now = time.time()
            el = now - t0
            if el > 0:
                job.fps = n_done / el
                if job.fps > job.peak_fps:
                    job.peak_fps = job.fps
            if total:
                job.progress = min(1.0, n_done / total)
            if on_update and (now - last_cb >= 0.25 or (total and n_done == total)):
                last_cb = now
                on_update(job)
    except BrokenPipeError:
        job.error = "编码端提前退出（管道断开）"
        rc = 1
    except KeyboardInterrupt:
        _terminate(dec, enc)
        _unregister_proc(dec)
        _unregister_proc(enc)
        pool.terminate()
        pool.join()
        raise
    except Exception as exc:
        job.error = f"多进程推理失败：{exc}"
        rc = 1
    finally:
        pool.close()
        pool.join()
        # 失败/中断时无条件收尾：否则这两个 ffmpeg 会变成孤儿、且下面的 wait 会卡死
        if rc != 0:
            try:
                enc.stdin.close()
            except Exception:
                pass
            _terminate(dec, enc)
            _unregister_proc(dec)
            _unregister_proc(enc)

    if rc != 0:
        job.elapsed = time.time() - t0
        return 1

    try:
        enc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    enc_t.join(timeout=5.0)
    dec_t.join(timeout=5.0)
    enc_rc = enc.wait()
    dec_rc = dec.wait()
    _unregister_proc(dec)
    _unregister_proc(enc)

    job.elapsed = time.time() - t0
    if dec_rc != 0:
        job.error = (f"解码 ffmpeg 退出码 {dec_rc}: "
                     f"{chr(10).join(dec_sink[-8:]).strip()[:400]}")
        return 1
    if enc_rc != 0:
        job.error = (f"编码 ffmpeg 退出码 {enc_rc}: "
                     f"{chr(10).join(enc_sink[-8:]).strip()[:400]}")
        return 1
    if total:
        job.progress = 1.0
    return 0


def run_one_job(args: argparse.Namespace, job: Job, meta: Dict,
                mode: str, workers: int, threads: int,
                engine: Optional[HdrTvNetPlus], engine_factory,
                on_update: Optional[Callable[[Job], None]] = None) -> int:
    """按 decide_single_file_mode 的结论分派到对应执行路径。"""
    if mode == "segment":
        return run_job_segmented(args, job, meta, workers, engine_factory,
                                 threads, on_update=on_update)
    if mode == "workers":
        return run_job_multiproc(args, job, meta, workers, on_update=on_update)
    return _run_job_with_fallback(args, job, meta, engine, on_update=on_update)


# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════
def print_env_status(args: argparse.Namespace) -> None:
    """启动时打印环境状态，不自动安装、不自动 clone（生产环境策略）。"""
    print("── 环境检查 ──")

    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        print(f"  {tool}: {'✓ ' + path if path else '✗ 未找到'}")

    torch_ok, torch_msg = check_torch_cuda()
    print(f"  PyTorch: {'✓ ' + torch_msg if torch_ok else '✗ ' + torch_msg}")

    if not args.no_model:
        repo = Path(args.model_repo)
        repo_ok, repo_msg = check_model_repo(repo)
        print(f"  模型仓库 ({repo}): {'✓ ' + repo_msg if repo_ok else '✗ ' + repo_msg}")
        if not repo_ok:
            print(f"    如需获取：git clone --depth 1 "
                  f"https://github.com/xiaom233/HDRTVNet-plus.git {repo}")
    else:
        print("  模型仓库: 跳过检查（--no-model）")

    # 硬件路径只在**真的请求**时才探测（探测要起一次 ffmpeg，约 0.2s）
    wants_hw = (getattr(args, "codec", "") in _HW_ENCODERS
                or getattr(args, "decode", "cpu") != "cpu")
    if wants_hw:
        if getattr(args, "codec", "") in _HW_ENCODERS:
            got = _probe_encoder(args.codec)
            print(f"  NVENC/硬件编码 {args.codec}: {'✓ 可用' if got else '✗ 不可用'}")
        if getattr(args, "decode", "cpu") != "cpu":
            got = _probe_decode_backend("cuda",
                                        sample=Path(args.input)
                                        if Path(args.input).is_file() else None)
            print(f"  CUDA 硬件解码: {'✓ 可用' if got else '✗ 不可用'}")
        print(f"  降级策略: {getattr(args, 'fallback_policy', 'auto')}"
              + ("（不可用即降级到 CPU 等价物）"
                 if getattr(args, "fallback_policy", "auto") == "auto"
                 else "（不允许降级，不可用直接报错）"))
    print()


def print_overview(args: argparse.Namespace, jobs: List[Job],
                   workers: int, threads: int,
                   cpu: int, total_mem: float, avail_mem: float,
                   cpu_src: str, mem_src: str,
                   meta_first: Optional[Dict] = None) -> None:
    """规整的信息输出块（环境→输入输出→编码轴→系统资源→并行度）。"""
    pending = [j for j in jobs if j.status == "pending"]
    _in = Path(args.input)
    if _in.is_dir():
        print(f"输入        : {args.input}（目录，递归={args.recursive}）")
        print(f"输出        : {args.output}")
    else:
        print(f"输入        : {args.input}")
        print(f"输出        : {jobs[0].dst if jobs else args.output}")

    if meta_first:
        m = meta_first
        print(f"源信息      : {m['width']}x{m['height']}  {m['fps']:.3f}fps  "
              f"{m['src_bits']}bit {m['pix_fmt']}  "
              f"色彩 {m['color_space'] or '-'}/{m['color_primaries'] or '-'}/"
              f"{m['color_transfer'] or '-'}"
              + ("  含音轨" if m["has_audio"] else "  无音轨"))

    codec = args.codec
    _preset = args.preset
    _preset_field = f"preset: {_preset}   " if encoder_supports_preset(codec) else ""
    _q = []
    if args.crf is not None:
        _q.append(f"CRF: {args.crf}")
    if args.cq is not None:
        _q.append(f"CQ: {args.cq}")
    if args.qp is not None:
        _q.append(f"QP: {args.qp}")
    if args.crf_ref is not None:
        _q.append(f"CRF-ref: {args.crf_ref}（libx264 基准，{args.quality_mode} 口径换算）")
    if args.cq_ref is not None:
        _q.append(f"CQ-ref: {args.cq_ref}（h264_nvenc 基准，{args.quality_mode} 口径换算）")
    print(_label("编码器") + f"{codec}   {_preset_field}" + "   ".join(_q)
          + f"   pix_fmt: {(_PIXFMT_BY_DEPTH.get(args.bit_depth, {}).get(codec) or _DEFAULT_PIXFMT_BY_DEPTH[args.bit_depth])}")
    print(_label("解码") + f"{getattr(args, 'decode_resolved', args.decode)}"
          + ("（硬件解码）" if getattr(args, "decode_resolved", "cpu") == "cuda"
             else "（软件，本脚本默认）"))
    _md_state = ("不写" if args.no_master_display
                 else ("写入" if args.codec == "libx265"
                       else f"不适用（{args.codec} 无法写入）"))
    print(_label("HDR10") + f"bt2020nc / smpte2084 / {args.color_range}   "
          f"master-display: {_md_state}   "
          f"max-cll: {args.max_cll or '不写'}")
    print(_label("推理") + ("跳过（--no-model）" if args.no_model
                            else f"Ensemble_AGCM_LE，设备 {args.device}"
                                 + (f"，分块 {args.tile}+{args.tile_overlap}"
                                    if args.tile > 0 else "，整帧")))
    if args.split_mode != "off":
        print(_label("单文件并行") + f"split-mode={args.split_mode}")
    print(_label("音频") + f"{args.audio_codec_effective}"
          + (f" @ {args.audio_bitrate}" if args.audio_codec_effective not in ("copy", "none") else ""))
    if args.extra_args:
        print("额外参数    : " + shlex.join(list(args.extra_args)))

    print("─" * 64)
    print(f"系统资源    : CPU 逻辑核 {cpu}（{cpu_src}）  ·  "
          f"内存 总 {total_mem:.1f} GB / 可用 {avail_mem:.1f} GB（{mem_src}）")
    print(f"待处理文件  : {len(pending)} 个"
          + (f"（另有 {len(jobs) - len(pending)} 个跳过）"
             if len(jobs) != len(pending) else ""))
    if pending:
        print(f"并发策略    : {workers} 个并行任务 × 每任务 {threads} 线程  "
              f"(≈ {workers * threads} CPU 槽)")
    else:
        print("并发策略    : 无待处理任务")
    print("运行模式    : "
          + ("顺序执行（细粒度实时进度条）"
             if args.sequential or workers <= 1 or len(pending) <= 1
             else "并行执行（聚合进度面板）"))


def make_engine_factory(args: argparse.Namespace):
    """返回一个「按需构造推理引擎」的工厂（--no-model 时直接给 None）。"""
    if args.no_model:
        return lambda: None

    def _make():
        return HdrTvNetPlus(Path(args.model_repo), device=args.device,
                            threads=args.torch_threads)

    return _make


def main() -> int:
    check_tools()
    install_signal_handlers()

    # 先解析参数以便知道 --no-model 等标志
    try:
        # `--extra-args` 的取值自己切（Python 3.12 的 argparse 不吃开头的 `--`，
        # 见 _split_extra_args 的注释）；没写该参数时不动 argparse 给的默认值。
        _argv, _extra_tail = _split_extra_args(sys.argv[1:])
        args = parse_args(_argv)
        if _extra_tail is not None:
            args.extra_args = _extra_tail
        args.warnings = []          # validate_args 往里收集告警
        validate_args(args, _argv)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if args.log:
        setup_log(args.log)

    print_env_status(args)

    for w in args.warnings:
        print(f"[提示] {w}", file=sys.stderr)

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    try:
        jobs, _batch_mode, output_path = collect_jobs(
            input_path=input_path, output_path=output_path, codec=args.codec,
            container=args.container_normalized, overwrite=args.overwrite,
            recursive=args.recursive, suffix=args.suffix)
    except KeyboardInterrupt:
        _terminate_active_procs()
        return 130

    if not jobs:
        print(f"[INFO] 未在 {input_path} 中找到可处理的视频。")
        return 0

    # 首个待处理文件的源探测（概览与并行度都要用）
    first_meta: Optional[Dict] = None
    for j in jobs:
        if j.status == "pending":
            try:
                first_meta = probe_source(j.src)
            except ValueError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                return 2
            break

    # SDR→HDR 的前提。源已是 HDR 时继续跑会让模型吃 PQ 值当 SDR 学，结果无意义。
    if first_meta and (first_meta["color_transfer"] in
                       ("smpte2084", "arib-std-b67", "smpte2084-hdr10")
                       or first_meta["color_primaries"] == "bt2020"):
        print(f"[WARN] 源看起来已经是 HDR（trc={first_meta['color_transfer'] or '-'} "
              f"prim={first_meta['color_primaries'] or '-'}）。"
              f"本工具面向 SDR 源；HDR 源应直接用 vidcrop_hwaccel.py 转封装。",
              file=sys.stderr)

    cpu, total_mem, avail_mem, cpu_src, mem_src = detect_system_resources()
    pending = [j for j in jobs if j.status == "pending"]
    gpu_infer = (not args.no_model) and args.device in ("auto", "cuda") \
        and check_torch_cuda()[0]
    workers, threads = compute_parallelism(
        num_pending=len(pending), codec=args.codec, cpu=cpu,
        total_mem_gb=total_mem, avail_mem_gb=avail_mem,
        workers_override=args.workers, threads_override=args.threads,
        mem_per_job_override=args.mem_per_job,
        nn_enabled=not args.no_model, gpu_infer=gpu_infer)

    # 文件级并发收敛到待处理文件数；而 workers 本身（不收敛）仍要传给
    # 单文件并行的分段/进程数决策 —— 两者用途不同，别混用。
    file_workers = max(1, min(workers, len(pending))) if pending else 0

    print("─" * 64)
    print_overview(args, jobs, file_workers, threads, cpu, total_mem, avail_mem,
                   cpu_src, mem_src, meta_first=first_meta)
    print("─" * 64)

    # ── dry-run：逐文件打印计划与两条命令，然后退出 ──
    if args.dry_run:
        for job in jobs:
            if job.status == "skipped":
                print(f"⏭  跳过 {job.name}: {job.error or '已存在，--overwrite 可覆盖'}")
                continue
            try:
                meta = probe_source(job.src)
                plan = build_plan(args, meta, src=job.src, dst=job.dst)
            except ValueError as exc:
                print(f"✘ 失败 {job.name}: {exc}", file=sys.stderr)
                continue
            print(f"\n▶ {job.name} → {job.dst.name}")
            print_plan(args, plan, meta, src=job.src, dst=job.dst)
        print()
        print("[dry-run] 未实际处理。去掉 --dry-run 即开始转换。")
        return 0

    # ── 真跑 ──
    engine_factory = make_engine_factory(args)
    engine: Optional[HdrTvNetPlus] = None
    # workers 模式下父进程**不能**预加载模型：spawn 之前父进程已 import torch 会
    # 让子进程初始化异常（见 run_job_multiproc 的 ①② 约束）。分段/整条路径则需要。
    single_mode = (decide_single_file_mode(args, first_meta or {}, len(pending), workers)
                   if len(pending) == 1 else "off")
    if not args.no_model and single_mode != "workers":
        try:
            engine = engine_factory()
        except (RuntimeError, ValueError) as exc:
            print(f"[ERROR] 模型初始化失败：{exc}", file=sys.stderr)
            print("[提示] 只验证编码链路可加 --no-model", file=sys.stderr)
            return 1

    for job in jobs:
        job.dst.parent.mkdir(parents=True, exist_ok=True)

    sequential = args.sequential or file_workers <= 1 or len(pending) <= 1
    try:
        if pending:
            if sequential:
                run_sequential(args, jobs, engine, threads,
                               engine_factory=engine_factory, workers=workers)
            else:
                run_parallel(args, jobs, engine_factory, file_workers, threads)
        else:
            for j in jobs:
                if j.status == "skipped":
                    print(f"⏭  跳过 {j.name} ({j.error or '已存在，--overwrite 可覆盖'})")
    except KeyboardInterrupt:
        _STOP_REQUESTED.set()
        _terminate_active_procs()
        print("\n[INFO] 已中断。", file=sys.stderr)
        return 130

    if _STOP_REQUESTED.is_set():
        return 130
    return print_summary(jobs)


if __name__ == "__main__":
    sys.exit(main())
