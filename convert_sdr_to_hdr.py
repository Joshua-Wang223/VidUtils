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

主要参数
────────
--input                输入视频文件（必选）
--output               输出视频文件（必选）
--model-repo           HDRTVNet-plus 仓库目录（默认 /mnt/d/Workspace_Python/HDRTVNet-plus）；
                       需含 codes/ 与 pretrained_models/Ensemble_AGCM_LE.pth
--no-model             跳过神经网络推理，只做 SDR→PQ 值域转换 + HDR10 编码
--crf                  x265 质量，默认 20（HDR10 交付建议 ≤20；上游示例用 8）
--preset               x265 preset，默认 slow
--master-display       HDR10 mastering display 色度+亮度；不给则用 --no-master-display
--no-master-display    只写 max-cll 与色彩三参数，不写 mastering display
--max-cll              MaxCLL,MaxFALL，默认 "1000,400"
--bit-depth            输出位深，默认 10（唯一可选值；SDR→HDR 必须 10bit）
--mod-crop             宽高向内裁到 8 的倍数（默认开；--no-mod-crop 关闭）
--duration             只处理前 N 秒（调试/冒烟用）
--frames               只处理前 N 帧（比 --duration 精确，调试/冒烟用）
--device               推理设备 auto/cpu/cuda（默认 auto：有 CUDA 用 cuda，否则 cpu）
--threads              torch CPU 线程数（默认 0 = 不干预）
--tile / --tile-overlap
                       分块推理的块尺寸与重叠像素。4K 这类大图整帧推理会吃光内存，
                       分块后显存/内存需求降到 (tile+overlap)² 量级。
                       0 = 整帧推理（1080p 及以下推荐）
--audio                音频处理 copy（默认）/ none
--overwrite            覆盖已存在的输出
--dry-run              只打印将要执行的 ffmpeg 命令与阶段计划，不实际处理
--log LOG_FILE         把全部输出追加写入日志文件
--extra-args           追加任意 ffmpeg 参数（放在编码命令末尾）

退出码：0 正常 / 1 ffmpeg 或推理失败 / 2 参数错误

示例
────
# 1) 先用 --dry-run 看命令（不碰模型、不耗 CPU）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --dry-run

# 2) 无 GPU 环境下先验证编码链路（跳过神经网络）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --no-model

# 3) 完整流程（需要 torch；本机若无 torch 见报错里的安装指引）
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4

# 4) 冒烟：只跑前 2 秒、640x360、CPU 推理
python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --frames 48 --bit-depth 10 \
    --model-repo /mnt/d/Workspace_Python/HDRTVNet-plus
"""
import argparse
import json
import shlex
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "1.0"

# 仓库外默认位置：与 VidUtils 同级，避免把模型权重纳入 git
DEFAULT_MODEL_REPO = "/mnt/d/Workspace_Python/HDRTVNet-plus"

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
    duration = _frac_to_float((data.get("format") or {}).get("duration")) or 0.0

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
        "duration": duration,
    }


def build_decode_cmd(src: Path,
                     duration: Optional[float], frames: Optional[int],
                     ffmpeg: str = "ffmpeg") -> List[str]:
    """
    拼解码命令：源视频 → gbrp16le 原始帧流走 stdout。

    只取视频流（-an -sn -dn），音频留到编码阶段直接从源文件 map，省一次解码。
    宽高不在这里给：滤镜链不做缩放，尺寸由调用方按 --mod-crop 算好后，
    由 Python 侧按该尺寸切分原始帧字节。
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if duration is not None:
        # -t 是输入选项（可放在 -i 前），限制读入时长
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-i", str(src), "-an", "-sn", "-dn"]
    if frames is not None:
        # ⚠ -frames:v 是**输出**选项，必须放在 -i 之后：放在前面会被 ffmpeg 当成
        # 输入选项并直接报错退出（实测 "cannot be applied to input url … Move this
        # option before the file it belongs to"，退出码 234）。
        cmd += ["-frames:v", str(frames)]
    cmd += ["-vf", f"format={RAW_PIX_FMT}", "-pix_fmt", RAW_PIX_FMT,
            "-f", "rawvideo", "-"]
    return cmd


def build_encode_cmd(width: int, height: int, fps: float,
                     out: Path, src: Path,
                     crf: int, preset: str, pix_fmt: str,
                     x265_params: Optional[str],
                     audio: str, has_audio: bool,
                     ffmpeg: str = "ffmpeg") -> List[str]:
    """
    拼编码命令：gbrp16le 原始帧流（stdin）+ 源文件（取音频）→ HDR10。

    顺序契约（与仓库另两个脚本一致）：
      输入 → 滤镜 → 质量(-crf) → preset → pix_fmt → 色彩四参 → x265-params
      → 音频 → 容器收尾
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "rawvideo", "-pixel_format", RAW_PIX_FMT,
           "-video_size", f"{width}x{height}", "-framerate", f"{fps:.6f}",
           "-i", "pipe:0"]

    if audio != "none" and has_audio:
        # 音频直接从源文件流复制，不再解一次码。-map 1:a:0? 的 ? 表示源无音轨也不报错。
        cmd += ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0?",
                "-c:a", "copy", "-shortest"]

    vf = (f"scale=out_color_matrix=bt2020nc:out_range=pc,"
          "setparams=colorspace=bt2020nc:color_primaries=bt2020:"
          "color_trc=smpte2084:range=pc")
    cmd += ["-vf", vf,
            "-c:v", "libx265", "-pix_fmt", pix_fmt,
            "-crf", str(crf), "-preset", preset,
            "-colorspace", "bt2020nc",
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-color_range", "pc"]
    if x265_params:
        cmd += ["-x265-params", x265_params]
    cmd += [str(out)]
    return cmd


def build_x265_params(master_display: Optional[str],
                      max_cll: Optional[str]) -> Optional[str]:
    """
    拼 HDR10 静态元数据（单条 -x265-params）。

    ⚠ 必须是**一条**：实测 `-x265-params A -x265-params B` 是后者整条覆盖前者，
    与 vidcrop_cpu_v2.py 记录的同一坑一致。各项用冒号连接。
    """
    params: List[str] = []
    if master_display:
        params.append("master-display=" + master_display)
    if max_cll:
        params.append("max-cll=" + max_cll)
    params.append("hdr10=1")
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
                    "Ensemble_AGCM_LE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 1) 先看命令，不实际处理
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --dry-run

  # 2) 无 GPU / 无 torch 时验证编码链路（跳过神经网络）
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --no-model

  # 3) 完整流程
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4

  # 4) 冒烟：前 2 秒 + CPU 推理
  python3 convert_sdr_to_hdr.py -i in.mp4 -o out.mp4 --frames 48 --device cpu

  # 5) 4K 源分块推理（整帧会吃光内存）
  python3 convert_sdr_to_hdr.py -i uhd.mp4 -o out.mp4 --tile 1024 --tile-overlap 128

退出码：0 正常 / 1 ffmpeg 或推理失败 / 2 参数错误
""")
    p.add_argument("--input", "-i", required=True, help="输入视频文件")
    p.add_argument("--output", "-o", required=True, help="输出视频文件")

    g = p.add_argument_group("模型")
    g.add_argument("--model-repo", default=DEFAULT_MODEL_REPO,
                   help=f"HDRTVNet-plus 仓库目录（默认 {DEFAULT_MODEL_REPO}）")
    g.add_argument("--no-model", action="store_true",
                   help="跳过神经网络推理，只跑 SDR→PQ + HDR10 编码链路")
    g.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"),
                   help="推理设备（默认 auto：有 CUDA 用 cuda）")
    g.add_argument("--threads", type=int, default=0,
                   help="torch CPU 线程数（默认 0 = 不干预）")
    g.add_argument("--tile", type=int, default=0,
                   help="分块推理边长（默认 0 = 整帧；4K 建议 1024，最小 24）。"
                        "注意分块不等价于整帧：每块各算自己的全局色调映射")
    g.add_argument("--tile-overlap", type=int, default=0,
                   help="分块重叠像素（默认 0；须为 8 的倍数）")

    g = p.add_argument_group("输出与编码")
    g.add_argument("--crf", type=int, default=20, help="x265 CRF（默认 20）")
    g.add_argument("--preset", default="slow", help="x265 preset（默认 slow）")
    g.add_argument("--bit-depth", type=int, default=10, choices=(10,),
                   help="输出位深（默认 10；SDR→HDR 必须 10bit）")
    g.add_argument("--master-display", default=DEFAULT_MASTER_DISPLAY,
                   help="HDR10 mastering display 元数据")
    g.add_argument("--no-master-display", action="store_true",
                   help="不写 mastering display（只写 max-cll 与色彩三参数）")
    g.add_argument("--max-cll", default=DEFAULT_MAX_CLL,
                   help=f"MaxCLL,MaxFALL（默认 {DEFAULT_MAX_CLL}）")
    g.add_argument("--audio", default="copy", choices=("copy", "none"),
                   help="音频处理（默认 copy 流复制；none = 丢弃）")
    g.add_argument("--no-mod-crop", dest="mod_crop", action="store_false",
                   default=True,
                   help="关闭向内裁到 8 的倍数（默认开）")
    g.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出")
    g.add_argument("--dry-run", action="store_true",
                   help="只打印命令与阶段计划，不实际处理")
    g.add_argument("--log", help="把全部输出追加写入日志文件")
    g.add_argument("--extra-args", nargs=argparse.REMAINDER,
                   help="追加任意 ffmpeg 参数（放在编码命令末尾）")

    g = p.add_argument_group("裁剪（调试）")
    g.add_argument("--duration", type=float, default=None,
                   help="只处理前 N 秒（调试/冒烟用）")
    g.add_argument("--frames", type=int, default=None,
                   help="只处理前 N 帧（比 --duration 精确，调试/冒烟用）")

    args = p.parse_args(argv)
    return args


def validate_args(args: argparse.Namespace) -> None:
    """参数校验。违反约定抛 ValueError（调用方转成退出码 2）。"""
    if not Path(args.input).is_file():
        raise ValueError(f"输入文件不存在：{args.input}")
    if not (0 <= args.crf <= 51):
        raise ValueError(f"--crf 必须在 0..51 之间，给的是 {args.crf}")
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
    if not args.no_model:
        # 只有真要跑网络时才校验模型仓库；--no-model 与 --dry-run 不该被它挡住
        resolve_model_paths(Path(args.model_repo))


# ═══════════════════════════════════════════════════════════════════
#  计划与执行
# ═══════════════════════════════════════════════════════════════════
def build_plan(args: argparse.Namespace, meta: Dict) -> Dict:
    """
    算出对齐后的尺寸与两条 ffmpeg 命令（纯函数，dry-run 与真跑共用）。

    尺寸处理：模型下采样三次，宽高须为 8 的倍数。--mod-crop（默认开）向内裁到
    8 的倍数；关闭则原样交给模型，由其在第一次 stride 卷积处报错——那样报错信息
    埋在 torch traceback 里，很难定位，所以默认给出可读的裁剪提示。
    """
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
    master_display = None if args.no_master_display else args.master_display
    x265_params = build_x265_params(master_display, args.max_cll or None)

    decode_cmd = build_decode_cmd(Path(args.input), args.duration, args.frames)
    encode_cmd = build_encode_cmd(ow, oh, fps, Path(args.output), Path(args.input),
                                  args.crf, args.preset, f"yuv420p{args.bit_depth}le",
                                  x265_params, args.audio, meta["has_audio"])
    if args.extra_args:
        # 插在输出路径之前，保持「输出文件永远在最后」的 token 顺序契约
        encode_cmd = encode_cmd[:-1] + list(args.extra_args) + [encode_cmd[-1]]

    return {
        "src_width": w, "src_height": h,
        "out_width": ow, "out_height": oh,
        "fps": fps, "x265_params": x265_params,
        "decode_cmd": decode_cmd, "encode_cmd": encode_cmd,
    }


def print_plan(args: argparse.Namespace, plan: Dict, meta: Dict) -> None:
    print("── 阶段计划 ──")
    print(f"  源        : {args.input}")
    print(f"  输出      : {args.output}")
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
    print(f"  编码      : libx265 CRF {args.crf} preset {args.preset} "
          f"yuv420p{args.bit_depth}le")
    print(f"  HDR10     : bt2020nc / smpte2084 / pc，"
          f"x265-params={plan['x265_params'] or '(无)'}")
    print(f"  音频      : {args.audio}"
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


def run_pipeline(args: argparse.Namespace, plan: Dict,
                 engine: Optional[HdrTvNetPlus]) -> int:
    """
    跑 ①→②→③。任一步失败返回 1。

    用 stdin/stdout 管道连成一条链：解码 ffmpeg 的 stdout 逐帧读进 Python，
    推理后写进编码 ffmpeg 的 stdin。两个子进程都不落中间文件，磁盘零额外占用。
    """
    w, h = plan["out_width"], plan["out_height"]
    frame_bytes = w * h * PLANES * BYTES_PER_SAMPLE

    total = None
    if args.frames:
        total = args.frames
    elif args.duration and plan["fps"] > 0:
        total = max(1, int(round(args.duration * plan["fps"])))

    print("── 执行 ──")
    print(f"  帧尺寸 {w}x{h}，每帧 {frame_bytes:,} 字节（gbrp16le）"
          + (f"，共 {total} 帧" if total else "，帧数由源决定"))

    dec = subprocess.Popen(plan["decode_cmd"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, bufsize=0)
    enc = subprocess.Popen(plan["encode_cmd"], stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           bufsize=0)

    n_done = 0
    t0 = time.time()
    try:
        while True:
            buf = _read_exact(dec.stdout, frame_bytes)
            if len(buf) < frame_bytes:
                if buf:
                    print(f"[WARN] 末帧不完整（{len(buf)}/{frame_bytes} 字节），丢弃", file=sys.stderr)
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

            if n_done % 10 == 0 or (total and n_done == total):
                el = time.time() - t0
                fps = n_done / el if el > 0 else 0.0
                eta = (total - n_done) / fps if (total and fps > 0) else 0.0
                msg = f"  帧 {n_done}" + (f"/{total}" if total else "")
                msg += f"  {fps:.2f} fps  已用 {el:.1f}s"
                if eta > 0:
                    msg += f"  剩余 ~{eta:.0f}s"
                print(msg, end="\r", file=sys.stderr, flush=True)
    except BrokenPipeError:
        print("\n[ERROR] 编码端提前退出（管道断开）", file=sys.stderr)
        _terminate(dec, enc)
        return 1
    except KeyboardInterrupt:
        print("\n[WARN] 用户中断，正在终止子进程…", file=sys.stderr)
        _terminate(dec, enc)
        return 1

    print("", file=sys.stderr)
    try:
        enc.stdin.close()
    except BrokenPipeError:
        pass

    enc_err = enc.stderr.read().decode("utf-8", "replace")
    enc_rc = enc.wait()
    dec_err = dec.stderr.read().decode("utf-8", "replace")
    dec_rc = dec.wait()

    el = time.time() - t0
    print(f"  处理 {n_done} 帧，用时 {el:.1f}s"
          + (f"（{n_done / el:.2f} fps）" if el > 0 else ""))

    if dec_rc != 0:
        print(f"[ERROR] 解码 ffmpeg 退出码 {dec_rc}", file=sys.stderr)
        if dec_err.strip():
            print(dec_err.strip(), file=sys.stderr)
        return 1
    if enc_rc != 0:
        print(f"[ERROR] 编码 ffmpeg 退出码 {enc_rc}", file=sys.stderr)
        if enc_err.strip():
            print(enc_err.strip(), file=sys.stderr)
        return 1

    out_size = Path(args.output).stat().st_size if Path(args.output).exists() else 0
    print(f"  完成：{args.output}（{out_size:,} 字节）")
    return 0


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
#  入口
# ═══════════════════════════════════════════════════════════════════
def main() -> int:
    check_tools()

    try:
        # `--extra-args` 的取值自己切（Python 3.12 的 argparse 不吃开头的 `--`，
        # 见 _split_extra_args 的注释）；没写该参数时不动 argparse 给的默认值。
        _argv, _extra_tail = _split_extra_args(sys.argv[1:])
        args = parse_args(_argv)
        if _extra_tail is not None:
            args.extra_args = _extra_tail
        validate_args(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if args.log:
        setup_log(args.log)

    src = Path(args.input)
    out = Path(args.output)
    if out.exists() and not args.overwrite and not args.dry_run:
        print(f"[ERROR] 输出已存在：{out}（要覆盖请加 --overwrite）", file=sys.stderr)
        return 2

    try:
        meta = probe_source(src)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    # SDR→HDR 的前提。源已是 HDR 时继续跑会让模型吃 PQ 值当 SDR 学，结果无意义，
    # 所以明确告警而不是静默放行。
    if (meta["color_transfer"] in ("smpte2084", "arib-std-b67", "smpte2084-hdr10")
            or meta["color_primaries"] == "bt2020"):
        print(f"[WARN] 源看起来已经是 HDR（trc={meta['color_transfer'] or '-'} "
              f"prim={meta['color_primaries'] or '-'}）。"
              f"本工具面向 SDR 源；HDR 源应直接用 vidcrop_hwaccel.py 转封装，"
              f"否则相当于把 PQ 值再喂给 SDR→HDR 网络。", file=sys.stderr)

    try:
        plan = build_plan(args, meta)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print_plan(args, plan, meta)
        print()
        print("[dry-run] 未实际处理。去掉 --dry-run 即开始转换。")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)

    engine = None
    if not args.no_model:
        try:
            engine = HdrTvNetPlus(Path(args.model_repo), device=args.device,
                                  threads=args.threads)
        except (RuntimeError, ValueError) as exc:
            print(f"[ERROR] 模型初始化失败：{exc}", file=sys.stderr)
            print("[提示] 只验证编码链路可加 --no-model", file=sys.stderr)
            return 1

    return run_pipeline(args, plan, engine)


if __name__ == "__main__":
    sys.exit(main())
