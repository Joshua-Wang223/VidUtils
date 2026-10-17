# 选项 2 · 拉长片段差分校验（全长）证据 · 2026-10-16/17

回答「20 帧代理的等质量标定能否兑现到 6s 全长」。协议与结论全文见
`memory/hdr_equal_quality_calibration_10mat_20261016.md` §10.2。

## 内容

- `<stem>.duration_check.json` ×10 —— 每素材 Δ20/Δfull/diff 官方判据值（由 `_duration_check.py` 生成；
  末 3 条 talking_tom / molly_s03e01 / dark_earth 为 2026-10-17 C 补测并入）
- `decomp_summary.json` —— 逐帧分解：ctx/fset、x265/svt 上下文位移分量、20 帧分段 Δ（10 素材全量）

## 锚点（复核必读）

| 项 | 值 |
|---|---|
| 日期 | 2026-10-16/17（CPU，夜间/后台；17 日并入 C 补测） |
| 母版 | HDRTVNet agcm **CPU 全帧推理**，gbrp16le 720p（统一 720p 口径） |
| 无损锚 | libx265 -qp 0（全长） |
| 目标 | libx265 crf_sent 20.744（全长） |
| svtav1 端点 | 20 帧标定曲线跨 v21 的两个实测点（dark_earth 基准 = 10 帧） |
| 度量 | ffmpeg psnr 滤镜（10bit，不 tonemap；summary = 合并 MSE 口径） |
| 判据 | \|Δfull − Δ20\| ≤ 0.3 dB |

标定基准母版与全长母版前段**字节一致**（模型逐帧无状态；md5 如下；除 dark_earth 外基准为 20 帧）：

| 素材（stem） | md5（20 帧段；dark_earth = 10 帧段） |
|---|---|
| cganim_edu_wordworld_src720x576 | 37ad847e49bfcea431b9b3e2772c8c6b |
| cganim_subs_src1920x1080 | 47cb6fdc46cf1aa46752607d2cdf8d4d |
| live_kids_seated_src1920x1080 | 22e1745a55d4309d6d187f900f26d8d9 |
| live_kids_play_src1280x720 | 27662cfda1e4b8de6f7e23f00dfe89c7 |
| doc_grassland_src1280x720 | 9da1a62f686d19ed1954e0a31cb06aa2 |
| screen_ui_code_src1280x720 | e6f6f64bd2699060db052f1138915242 |
| tv_bbc_molly_s01e01_src1920x1080 | b1a336f18724c85d587435819e5c96af |
| cganim_talking_tom_src1920x1080 | 82b6e902b7b191288ecf47e48844a6a8 |
| tv_bbc_molly_s03e01_src1920x1080 | 8f54b5ff56955993c0dbe5921e9667c6 |
| doc_dark_earth_src3840x2160 | d5437d42c0bbe2f123ffefc26d42e220 |

> 校验法：`md5sum Accessory/temp/hdr_eqq/<stem>/master_hdr.gbrp16le` 应等于
> `head -c 110592000 Accessory/temp/hdr_eqq_full/<stem>/master_hdr.gbrp16le | md5sum`
> （dark_earth 用 `head -c 55296000`，基准为 10 帧）。

## 复现

    python3 Accessory/probe/_duration_check.py --materials <stem>[,<stem>...]
    python3 Accessory/probe/_duration_decomp.py <stem> [<stem>...]

媒体产物在 `Accessory/temp/hdr_eqq_full/`（gitignored，不入库）。
