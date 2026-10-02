# ═══════════════════════════════════════════════════════════════════════════
#  各编码器相对 libx264 CRF 的线性等效关系
#      value = a × x264_crf + b   （再夹到 [lo, hi]）
#
#  基准轴统一取 libx264 CRF，于是任意两个编码器都能互转：
#      src_value --to_x264_crf--> x264_crf --from_x264_crf--> dst_value
#
#  **两张口径的表**（由 set_quality_mode() 选择，**默认 'quality'**）：
#    · SIZE_MAP    —— **等体积**口径（同文件大小；原名 QUALITY_MAP，2026-09-30 改名）
#    · QUALITY_MAP —— **等质量**口径（同 VMAF；原名 QUALITY_MAP_QUALITY）
#  QUALITY_MAP 未覆盖的编码器（如待上机的硬编）**回退到 SIZE_MAP**。
#
#  本表是全局唯一来源：vidcrop_hwaccel.py / vidcrop_cpu_v2.py 均从这里 import，
#  不再各自硬编码偏移量（此前 cq_to_crf() 里的 +1/+4 与本表方向相反，已废弃）。
#
#  注意 VideoToolbox 的 a 为负：它的 q 值越高画质越好，与 CRF 含义相反。
# ═══════════════════════════════════════════════════════════════════════════
SIZE_MAP = {
    # ---------- 软件编码器 ----------
    'libx264':               (1.0, 0.0, 0, 51),
    # libx265：2026-09-29 真实素材「等体积」重标定（V9 多源复核）。
    #   素材 new5_raw.mp4（1080p→720p，4s），锚点 libx264 crf 18~30，
    #   目标编码器扫 CRF 后按 log(体积) 插值出等体积点，再最小二乘。
    #   实测 libx265 ≈ 0.927·x264 + 1.34（拟合点残差 ≤0.11 档）——比旧表 (1.0, 3.0)
    #   低约 2~3 档。旧表来自"质量等效"经验值（x265 CRF 定义不同，+3），
    #   本表按方案 V9 的**等体积**口径重标；⚠ 等体积 ≠ 等质量。
    #   ⚠ 标定素材单一（4s 真实片源 + 单分辨率），换素材应复核。
    'libx265':               (0.9272, 1.3360, 0, 51),
    # libvpx-vp9：同轮等体积重标定 → 1.638·x264 − 6.23（残差 ≤0.49 档）。
    #   旧表 (1.98, -14.46) 的零点/上限极端（crf 0~7 全落 0、crf 37~51 全落 63）。
    'libvpx-vp9':            (1.6381, -6.2289, 0, 63),

    # ---------- AV1 软件编码器 ----------
    # libaom-av1：2026-09-29 真实素材「等体积」重标定（V9 补测）。
    #   素材 input_videos/new5_raw.mp4（1080p→720p，4s），锚点 libx264 crf 18~30，
    #   实测 ≈ 2.007·x264 − 21.35（拟合点 21→20.4, 24→27.4, 27→33.1, 30→38.5）。
    #   旧表 (1.0, 4.0) 基于官方文档推导，与实测差异巨大（crf 21 处：表值 25 vs 实测 20.4）。
    'libaom-av1':            (2.007, -21.35, 0, 63),
    # SVT-AV1：同轮等体积重标定（V9 补测，preset 8 锁定）。
    #   实测 ≈ 2.145·x264 − 21.35（拟合点 18→16.5, 21→24.2, 24→30.8, 27→36.6, 30→42.5）。
    #   旧表 (1.945, -15.62) 低估了斜率与截距；crf 21 处：表值 25.2 vs 实测 24.2。
    # ⚠ 多素材复核显示 b 有一定波动（−19 ~ −29），当前值基于基准素材 new5_raw.mp4。
    'libsvtav1':             (2.145, -21.35, 0, 63),
    # rav1e: 0-255 量化器刻度。**本表的 a/b 只对"已声明的 -speed 档"成立。**
    #   ⚠ **档位口径（2026-09-30 复核）**：VidUtils 两脚本固定下发 `-speed 10`
    #     （`_RAV1E_SPEED=10`；VE 侧见 quality_map.RAV1E_SPEED）。本表现值即在该档下
    #     使用，并经门禁素材 word_world_2 实测：crf21 → **qp 66**、码率比 **0.91**、
    #     ΔPSNR **−1.21 dB** ⇒ AC7 判据 PASS。
    #   ⚠ 历史注释曾把本值记为"原生档（不下发 -speed）"的拟合，与下发档位不一致；
    #     不影响当前行为（判据 ⑨ 组期望 66），但该记录的档位口径**待 M1 等质量标定复核**。
    #   拟合记录（对基准轴等体积重标）：
    #     素材 new5_raw.mp4（1080p→720p，2s），锚点 libx264 crf 18~30，
    #     rav1e 扫 -qp 40~140（12 点），按 log(体积) 插值等体积点后最小二乘，
    #     5/5 锚点全部落在扫描区间内 ⇒
    #     实测 rav1e_qp ≈ 7.0032·x264_crf − 80.99（残差 1.84 qp）⇒ crf 21 → **qp 66**。
    #   参考（若换档位须换表）：`-speed 10` 的短 clip 等体积标定曾给
    #     (6.8159, −66.093) ⇒ crf21 → qp 77（ΔPSNR ≈ −2.7 dB）；等质量解需 qp ≈ 55
    #     （码率比 1.303，体积 +30%）。2s 短 clip 对 speed 10 缺乏代表性。
    #
    # ⚠ **旧值 (4.0, -4.0) 有误，已废弃。** 它是**经 libaom 中转**推导的：
    #     当时用「libaom crf 20/25/30/35 ↔ rav1e qp 60/80/100/120」得
    #     rav1e_qp = 4 × (libaom_crf − 5)，再代入**当时**的 libaom 行
    #     `libaom_crf = x264_crf + 4` 才得到 4·x264_crf − 4。
    #     但 libaom 行已于 2026-09-29 重标为 `2.007·x264 − 21.35`，
    #     ⇒ 同样的链式推导现在给出 4 × (20.80 − 5) = **63**，与旧表值 80 矛盾。
    #   本次直接实测证实 63~66（原生档）才对，80 偏大 14~17。
    #   旁证：本仓 `crf_to_rav1e_qp()` 走 libaom 链、判据 ⑨ 组期望值本已是 **64**，
    #   与"原生档"实测一致；该链式口径给出 63，**不读本行**，故本仓行为不变。
    #   旧值 80 的实测代价：码率比 0.778（体积偏小 22%）而 ΔPSNR +0.18 dB（画质偏高）
    #   ⇒ 典型的"多花画质、少给体积"，等体积口径下并不最优。
    'librav1e':              (7.0032, -80.993, 0, 255),

    # ---------- AV1 硬件编码器 ----------
    # ⚠ av1_nvenc 的 -cq 量程是 **0~63**（AV1 qindex 尺度），不是 H.264/HEVC 的
    # 0~51——实测 `ffmpeg -h encoder=av1_nvenc`：`-cq (0 to 63)`、`-qp (-1 to 255)`。
    # hi 写 51 会把 crf_ref≥45 全挤到 51（白丢 12 档可分辨区间）。
    # ⚠ 它的 -qp 是 0~255，与 H.264 的 0~51 **不是同一刻度**：constqp 路径必须再经
    # QP 尺度层（qindex ≈ 4×QP），不能拿 CQ 轴值直发（见 quality_map.to_constqp_qp）。
    'av1_nvenc':             (1.0, 6.0, 0, 63),
    # av1_qsv / av1_amf 的 -cq 量程本机无法核实（无该编码器）；若同为 AV1 原生
    # 尺度则 hi 也应是 63，上机核对后再改。
    'av1_qsv':               (1.0, 5.0, 1, 51),
    'av1_amf':               (1.0, 3.0, 0, 51),

    # ---------- 其他硬件编码器 ----------
    'h264_nvenc':            (1.0, 5.0, 0, 51),
    'hevc_nvenc':            (1.0, 7.5, 0, 51),
    'h264_qsv':              (1.0, 3.5, 1, 51),
    'hevc_qsv':              (1.0, 4.5, 1, 51),
    'h264_amf':              (1.0, 2.0, 0, 51),
    'hevc_amf':              (1.0, 4.0, 0, 51),
    'h264_vaapi':            (1.0, 3.0, 0, 51),
    'hevc_vaapi':            (1.0, 5.0, 0, 51),

    # ---------- VideoToolbox（q 值越高画质越好） ----------
    # hevc 的 b 原本是 105 > hi=100：crf 0 算出 105 被截到 100，而 lo=1 要
    # crf≈53.6 才可达（> 51 上限）⇒ 低端永远够不着、crf 0~2.58 全饱和到 100。
    # 改 100（与 h264_videotoolbox 对齐）后两端都可表达。
    'h264_videotoolbox':     (-99.0 / 51.0, 100.0, 1, 100),
    'hevc_videotoolbox':     (-99.0 / 51.0, 100.0, 1, 100),
}

# ═══════════════════════════════════════════════════════════════════════════
#  QUALITY_MAP —— 等质量（equal perceptual quality）换算表
#
#  口径：以 libx264 CRF 为基准轴，在**目标编码器「参数 → VMAF」曲线上取等 VMAF 参数**
#        后最小二乘拟合（主指标 VMAF；标定脚本 probe/calibrate_equal_quality.py）。
#  ⚠ 等质量 ≠ 等体积：同一 x264 锚点下两表给出的目标参数不同。
#  与 SIZE_MAP（等体积）并存，由 set_quality_mode('size'|'quality') 选择，**默认 'quality'**。
#  本表未覆盖的编码器（如待上机的硬编）**回退到 SIZE_MAP**。
#
#  标定口径（2026-09-30，纯 CPU）：素材 new5_raw（8s，720p prep，与等体积表同口径）；
#  锚点 x264 CRF 18/21/24/27/30；librav1e 按 -speed 10。
#  ⚠ 首版仅覆盖软件编码器；NVENC/QSV/AMF/VideoToolbox 待上机标定（见 M5）。
# ═══════════════════════════════════════════════════════════════════════════
QUALITY_MAP = {
    # 标定（2026-10-02，纯 CPU，**第六版**）：统一锚点 **18/21/24/27/30** +
    # **按素材去重**的 **12 条素材**池化。
    #
    # 素材池（12 条，两仓实测并集）：
    #   · VU 侧 7 条：new5_raw / new4_raw / cc_anim_300s（动画平涂）
    #     / cc_subs_105s（动画+烧录字幕）/ earth_dark_80s（暗场）
    #     / ui_screen_10s（屏幕 UI）/ natgeo_grass_40s（高细节纹理）
    #   · VE 侧 4 条：new5_raw / new4_raw / new1 / word_world_2
    #   · BBC 实拍剧集 3 条（1080p，素材库）：S01E01 / S03E01 / S05E01（Molly and Mack）
    #     —— 仅 rav1e native 档有数据
    #   覆盖立项 §3 全部 6 类内容 + 实拍影视。
    #
    # ⚠⚠ **第六版修正：BBC 3 条的锚点口径错配（第五版的真实缺口）**
    #   BBC 3 条是在「锚点两仓统一」决策**之后**才加入补标的，其 workdir
    #   （`/tmp/eqq2/1280x720_10s_n2`）用的是 **A 套 `18/22/26/30/34`**
    #   ⇒ 对 B 套只命中 **2/5**（仅 crf18/30），等于用 2 点拟合的素材混入池化。
    #   第六版已补测 B 套 crf21/24/27（9 点，workdir
    #   `/tmp/eqq2/1280x720_10s_bbc_anchorB`，`n_subsample=1`，单点 5~7s）⇒ 5/5 齐。
    #   **影响面（实测）**：仅 `librav1e` native 变化（11 样本池化）；
    #   其余 5 档 BBC 不参与，数值**逐位不变**。
    #
    # ⚠ **锚点统一决策（2026-10-02）**：此前两仓锚点不同（A `18/22/26/30/34` /
    #   B `18/21/24/27/30`），因各自缺测对方锚点而无法直接比较。补测缺口后，在**同批
    #   素材/同口径**下做了可比 LOO 对照，**B 套 6 个档位全部更优**：
    #     VU 侧 7 素材：x265 5.98→3.25 / svtav1 5.37→4.70 / rav1e 11.40→7.44 / rav1e@10 11.85→7.35
    #     VE 侧 4 素材：x265 6.89→4.49 / svtav1 13.73→7.33 / aom 6.88→5.33 / rav1e@10 5.05→4.04
    #   机制：**决定因素不是 crf 位置，而是锚点是否落在各素材 VMAF 的可分辨区间**。
    #   A 套强拉到 crf34，对跨度小的素材（如 ui_screen 跨度仅 12.0）过头、进入陡峭段，
    #   反查条件数反而变差。
    #
    # ⚠ **池化口径：按素材去重（第五版修正第四版的缺陷，第六版沿用）**
    #   第四版把 `new5_raw`/`new4_raw` 的 6s（VU）与 10s（VE）当作**两个独立样本**
    #   ⇒ 双倍权重，把 vp9 LOO从 4.59 推到 **6.71**（误判为超门禁）。
    #   第五版起改为**按素材名去重**，每条只计一次。
    #
    # ✅ **精度：门禁按编码器分档，全部达标**（2026-10-02 仓主裁定）
    #   · 软编 4 档门禁 **≤ 5.9**：x265 3.98 / vp9 4.59 / svtav1 4.66 / aom 5.13 —— 全 ✅
    #   · rav1e 两档门禁 **≤ 7.5**：native **5.37** / @10 5.92 —— 全 ✅
    #   （BBC 锚点补齐使 native由 5.64 → **5.37**）
    #
    # ⚠ **仍达不到立项 M2 的 |ΔVMAF| < 1.0**，经仓主裁定放宽（见上）。误差来源已定性
    #   （非标定执行错误），四条排除性证据：
    #     (a) 非过拟合 —— 部分素材连**训练内** ΔVMAF 都达 0.8~3.0；
    #     (b) 非素材不足 —— 素材数 4→7→12 几无改善；
    #     (c) 非表格式 —— 每素材**专属**表 LOO 4.02~13.77，比共享直线更差；分段/二次无增益；
    #     (d) 非锚点位置 —— 已在**同批素材**上完成两套锚点的可比对照并选定更优者（见上）。
    #   根因是**结构性的**：`(a,b,lo,hi)` 单行仿射 + VMAF 反查，跨素材存在精度上限。
    #   ⚠ 换素材集后 LOO 可能变化；**若素材异质性显著增加，需重跑标定并重新定门禁**。
    #
    # ⚠ **已知方法论隐患（同素材跨口径的重复测量）**：`new5_raw`/`new4_raw` 在 VU(6s)
    #   与 VE(10s) 两侧的同名锚点 VMAF **不同**（实测最大跨度 1.75，因时长不同）。
    #   本表按 **VE 侧(10s) 覆盖 VU 侧(6s)** 合并，与第五版同口径以便纵向可比。
    #   实测三种合并规则（VE优先 / VU优先 / 同key取均值）的 a 差异约 1.5%，
    #   LOO 全部仍达标（3.98~5.49）⇒ 不影响门禁判定，但严格来说表值**依赖合并顺序**。
    #   若要消除该依赖，应改用「同 key 取均值」并重跑一次全档拟合。
    #
    # ⚠ **指标口径**：libvmaf 必须 `n_subsample=1` —— subsample>1 会**偏置 VMAF**
    #   （同文件 vp9 crf35 差 1.9~3.0，且偏置随编码器而异），会污染等 VMAF 匹配；
    #   标定与判据必须**同参、同时长**。**本表全部数据均以 subsample=1 产出**
    #   （⚠ 早期 workdir `/tmp/eqq_calib/1280x720_10s_n3` 是 subsample=8 的作废数据，
    #   **不参与**本表任何计算）。
    # ⚠ 仅软件编码器；硬编（NVENC/QSV/AMF/VT）未覆盖 ⇒ 自动回退 SIZE_MAP（M4 待上机）。
    'libx265':     (1.0877, -2.4279, 0, 51),
    'libvpx-vp9':  (1.9933, -15.6126, 0, 63),
    'libaom-av1':  (2.2671, -20.9112, 0, 63),
    'libsvtav1':   (2.1886, -16.3312, 0, 63),
    # ⚠ `librav1e` 按 **native 档**（不下发 `-speed`）标定——与 `SIZE_MAP['librav1e']` 的档位一致。
    #   `-speed 10` 档另存 quality_map._EQQUAL_SPEED_OVERRIDE（LOO 5.92，同样达标）。
    #   ⚠ 第六版：BBC3 条锚点由 2/5 补齐至 5/5 ⇒ 本行 (7.9494,−101.2811)、LOO 5.64→5.37。
    'librav1e':    (7.9494, -101.2811, 0, 255),
}

# ``-speed 10`` 下的**等质量**标定值（2026-10-02 落表，两仓同源）。
# ``QUALITY_MAP['librav1e']`` 只对 native 档成立；``-speed`` 整体平移码率曲线，
# 故 speed 10 档另存本表。值来源同 QUALITY_MAP（统一锚点 18/21/24/27/30 +
# 按素材去重的 12 条素材池化，720p prep，``n_subsample=1``），LOO worst
# |ΔVMAF| = **5.92** —— 按 rav1e 专用门禁 **≤7.5** 判定为**达标**。
# 空 dict ⇒ 未标定，rav1e 回退 ``QUALITY_MAP`` 的 native 档。
_EQQUAL_SPEED_OVERRIDE = {
    'librav1e': (7.8982, -104.5072, 0, 255),   # 仅当 _RAV1E_SPEED > 0 时生效
}
# 当前生效表 + 口径（由 set_quality_mode 维护）；默认 'quality'
_QUALITY_MODE = 'quality'
_ACTIVE_MAP = {**SIZE_MAP, **QUALITY_MAP}


def set_quality_mode(mode):
    """选择换算口径：'size'（等体积）或 'quality'（等质量，**默认**）。"""
    global _QUALITY_MODE, _ACTIVE_MAP
    m = str(mode).lower()
    if m not in ('size', 'quality'):
        raise ValueError(f"quality mode 必须是 'size' 或 'quality'，收到 {mode!r}")
    _QUALITY_MODE = m
    # QUALITY_MAP 未覆盖的编码器回退到 SIZE_MAP，避免局部表导致 None
    _ACTIVE_MAP = ({**SIZE_MAP, **QUALITY_MAP}
                   if m == 'quality' else SIZE_MAP)
    return _QUALITY_MODE


def get_quality_mode():
    return _QUALITY_MODE


def get_quality_map():
    """返回当前生效的 {codec: (a, b, lo, hi)}。"""
    return _ACTIVE_MAP


# convert_crf() 输出用的展示名（键为 FFmpeg 编码器名）
_DISPLAY_NAMES = {
    'libx264': 'libx264',
    'libx265': 'libx265',
    'libvpx-vp9': 'libvpx-vp9',
    'libaom-av1': 'libaom-av1',
    'libsvtav1': 'libsvtav1',
    'librav1e': 'librav1e',
    'av1_nvenc': 'av1_nvenc',
    'av1_qsv': 'av1_qsv',
    'av1_amf': 'av1_amf',
    'h264_nvenc': 'NVENC H.264',
    'hevc_nvenc': 'NVENC H.265',
    'h264_qsv': 'QSV H.264',
    'hevc_qsv': 'QSV H.265',
    'h264_amf': 'AMF H.264',
    'hevc_amf': 'AMF H.265',
    'h264_vaapi': 'VAAPI H.264',
    'hevc_vaapi': 'VAAPI H.265',
    'h264_videotoolbox': 'VideoToolbox H.264',
    'hevc_videotoolbox': 'VideoToolbox H.265',
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def from_x264_crf(codec, x264_crf, table=None):
    """libx264 CRF → 指定编码器的等效质量值；未知编码器返回 None。

    ``table`` 非 None 时用它做**一次性**换算（不改变全局模式，无副作用）；
    默认 None ⇒ 走当前活动表（``get_quality_map()``，默认模式即 ``QUALITY_MAP``）。
    """
    m = (table if table is not None else get_quality_map()).get(str(codec).lower())
    if m is None:
        return None
    a, b, lo, hi = m
    return _clamp(a * float(x264_crf) + b, lo, hi)


def to_x264_crf(codec, value, table=None):
    """指定编码器的质量值 → 等效 libx264 CRF；未知编码器或 a≈0 时返回 None。

    ``table`` 语义同 :func:`from_x264_crf`。
    """
    m = (table if table is not None else get_quality_map()).get(str(codec).lower())
    if m is None:
        return None
    a, b, lo, hi = m
    if abs(a) < 1e-9:
        return None
    return _clamp((float(value) - b) / a, 0.0, 51.0)


def convert_quality(src_codec, src_value, dst_codec, table=None):
    """
    任意两个编码器之间的等效质量换算（以 libx264 CRF 为中间轴）。

    Args:
        src_codec: 源编码器名（FFmpeg 名称，如 'h264_nvenc'）
        src_value: 源编码器下的质量值
        dst_codec: 目标编码器名（如 'libx265'）
        table: 可选的换算表覆盖（``{codec: (a, b, lo, hi)}``）；None ⇒ 用活动表

    Returns:
        目标编码器下的等效质量值（float）；任一端无映射时返回 None。

    注意：裁剪脚本只把它用于"GPU 编码器降级为 CPU 编码器"这一条路径；
    用户显式给出的同族参数（CPU 的 --crf、GPU 的 --cq）一律原样下发，不换算。
    """
    ref = to_x264_crf(src_codec, src_value, table=table)
    if ref is None:
        return None
    return from_x264_crf(dst_codec, ref, table=table)


def convert_crf(x264_crf):
    """
    根据输入的 libx264 CRF 值，返回其他编码器的等效 CRF/CQ/Q 值列表。

    参数:
        x264_crf (float/int): libx264 的 CRF 值，建议范围 0-51。

    返回:
        list of tuple: [(编码器名称, 等效值, 取值范围), ...]
        注意：VideoToolbox 的 q 值越高画质越好，与 CRF 含义相反。
    """
    if not (0 <= x264_crf <= 51):
        raise ValueError("x264 CRF 必须在 0 到 51 之间")

    result = []
    for codec, (a, b, lo, hi) in get_quality_map().items():
        value = a * x264_crf + b
        clamped = _clamp(value, lo, hi)
        result.append((_DISPLAY_NAMES.get(codec, codec),
                       int(round(clamped)), (lo, hi)))

    return result


if __name__ == '__main__':
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description='把 libx264 的 CRF 值换算成其他编码器的等效 CRF/CQ/Q 值。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python convert_crf.py 21                输出全部编码器的等效值
  python convert_crf.py -e libx265 -e libsvtav1 21
  python convert_crf.py -e "svt, libx265, h.265" 18   多个编码器用逗号分隔（需引号）
  python convert_crf.py --json 18         以 JSON 输出，便于脚本解析

注意：
  VideoToolbox 的 q 值越高画质越好，与 CRF 含义相反。
""",
    )
    parser.add_argument('crf', type=float, nargs='?', default=21.0,
                        help='libx264 的 CRF 值，0-51，默认 21')
    parser.add_argument('-e', '--encoder', action='append', metavar='NAME',
                        dest='encoders',
                        help='只输出指定编码器；逗号分隔或重复指定，'
                             '支持子串匹配（不区分大小写）')
    parser.add_argument('--json', action='store_true',
                        help='以 JSON 格式输出')
    parser.add_argument('--quality-mode', choices=['size', 'quality'],
                        default='quality',
                        help='换算口径：size=等体积（文件大小优先）；'
                             'quality=等质量（画质优先，默认）')

    args = parser.parse_args()

    if not 0 <= args.crf <= 51:
        parser.error("CRF 必须在 0 到 51 之间")

    set_quality_mode(args.quality_mode)
    rows = convert_crf(args.crf)

    if args.encoders:
        keys = [k.strip().lower()
                for part in args.encoders
                for k in part.split(',') if k.strip()]
        rows = [r for r in rows
                if any(k in r[0].lower() for k in keys)]
        if not rows:
            available = ', '.join(enc for enc, _, _ in convert_crf(args.crf))
            parser.error(f"没有匹配 {keys} 的编码器，可用：{available}")

    if args.json:
        print(json.dumps(
            [{'encoder': enc, 'value': val, 'range': list(rng)}
             for enc, val, rng in rows],
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"libx264 CRF {args.crf:g} 对应的等效 CRF/CQ/Q 值：\n")
        print(f"{'编码器':<22} {'等效值':>6}   取值范围")
        print("-" * 48)
        for enc, val, rng in rows:
            print(f"{enc:<22} {val:>6}   {rng}")