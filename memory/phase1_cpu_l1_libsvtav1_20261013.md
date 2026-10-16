# convert_sdr_to_hdr.py 基准报告（bench v1.0）

- 源：`../input_videos/test1.mp4` — 1280x720, 30.000fps, 19.13s, 574 帧
- 环境：8 核 / 可用 9.5 GB / torch 有 / 模型 就绪
- 口径：每组合 1 次取 min；等质量锚定 `--crf-ref 21`（quality 口径）

| 组合 | 层 | 状态 | 墙钟 min | 墙钟 median | fps | 体积 | 下发质量值 | 路径 |
|---|---|---|---|---|---|---|---|---|
| `l1.libsvtav1` | l1 | OK | 342.35s | 342.35s | 0.09 | 282.0 KB | crf 29 | off |

## 读数须知

- 墙钟含 Python 启动与模型加载；script_reported_elapsed 不含模型加载且三条执行路径口径不一致，仅供交叉核对。SKIP 不等于通过。
- `crf_sent` 是真正下发给编码器的原生值，不是 `--crf-ref` 的数字：该基准处**没有任何编码器是恒等映射**。
- SKIP 表示「本机/本素材跑不出有意义的对比」，**不是通过**。