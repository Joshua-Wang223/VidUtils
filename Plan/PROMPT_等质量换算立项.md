# VidUtils 等质量换算表立项 Prompt

> 🔗 **姊妹仓（必须同步维护）**：`Video_Enhancement/Plan/PROMPT_等质量换算立项.md`
> * 该文档含 **VE 侧逐锚点实测证据**：`librav1e` 用等体积值 `(7.0032, −80.993)` 在
>   `word_world_2.mp4`（687 帧）上 crf18~30 的 ΔPSNR 为
>   **+0.59 / −1.21 / −2.57 / −4.17 / −5.79 dB**（码率比恒定 0.91~1.00）
>   —— 这是「等体积 ≠ 等质量」最直接的证据，本项目标定完成后应据此复核。
> * 质量参数方案交叉引用：`Video_Enhancement/Plan/Video_Enhancement_质量控制参数修复方案.md`
>   **§6.11.3**（口径分工）、**§6.10**（标定缓存污染）、**§6.11**（rav1e 性能与重标）。
>
> ⚠ **两仓的 `QUALITY_MAP` 必须逐条相等**（本仓判据 ⑨ 组断言）⇒ **等质量表也要两份同步副本**，
> 改一侧必须同步另一侧并回跑 ⑨ 组。

## 背景
当前 `QUALITY_MAP` 采用 **等体积（equal file size/bitrate）** 口径标定（V9 已落地），即：
- 锚点：libx264 CRF 18/21/24/27/30（-preset medium）
- 目标编码器扫 CRF → 记录体积 → 在 log(体积) 曲线上插值得到**等体积 CRF** → 最小二乘拟合 `value = a × x264_crf + b`
- ⚠ 已知：**等体积 ≠ 等质量**。同体积下，不同编码器的 PSNR/VMAF/主观画质差异可达 1~3 dB / 10~20 VMAF 分。

## 目标
建立 **等质量（equal perceptual quality）** 换算表，使：
```
libx264 CRF 21  ≈  libx265 CRF ?  ≈  libvpx-vp9 CRF ?  ≈  libsvtav1 CRF ?  ≈  libaom-av1 CRF ?
                     h264_nvenc -cq ?   ≈  hevc_nvenc -cq ?   ≈  av1_nvenc -cq ?
```
在**同一主观/客观画质水平**下互换，而非同文件大小。

## 技术路线

### 1. 质量度量指标（建议三维并行）
| 指标 | 适用场景 | 采集方式 |
|------|----------|----------|
| **VMAF** (vmaf_v0.6.1+neg) | 主流、与主观相关性最强 | `libvmaf` + `ffmpeg -vf libvmaf=model_version=vmaf_v0.6.1:log_fmt=json` |
| **PSNR-HVS / MS-SSIM** | 补充、无参考时兜底 | `ffmpeg -i ref -i dist -lavfi psnr/hvs/ssim` |
| **主观 AB 测试** | 最终定标、解决指标分歧 | 至少 3 人、双盲、随机序、ITU-R BT.500-13 |

### 2. 标定流程（参考 Netflix VMAF 标定流程）
```
对每个编码器：
  1. 选取 5~8 条代表性素材（动画/实拍/高动/低动/屏幕内容/暗场/高细节，各 10s，1080p）
  2. libx264 在 CRF 18/20/22/24/26/28/30 编码 → 得到 7 个质量锚点（VMAF/PSNR）
  3. 目标编码器扫 CRF/CQ/QP（建议 10~15 个点，覆盖锚点质量区间）
  4. 计算每个测试点的 VMAF/PSNR 相对 libx264 锚点
  5. 插值：在目标编码器「参数 → VMAF」曲线上，找到与 libx264 每个锚点**等 VMAF** 的参数值
  6. 对 (x264_CRF, 目标参数) 做分段线性/样条拟合 → 生成等质量映射表
  7. 交叉验证：留一素材法，验证预测误差（目标：ΔVMAF < 1.0，ΔPSNR < 0.3 dB）
```

### 3. 素材集建议（最小可行集 → 逐步扩充）
| 类别 | 来源 | 时长 | 分辨率 | 备注 |
|------|------|------|--------|------|
| 动画平涂 | new5_raw.mp4 切片 | 10s | 1080p | 现有 |
| 实拍自然 | new4_raw.mp4 切片 | 10s | 1080p | 现有 |
| 高动作/运动 | Earth.at.Night... 切片 | 10s | 1080p | 现有 |
| 屏幕内容/文字 | 需新增 | 10s | 1080p | 关键补齐 |
| 暗场/高噪 | 需新增 | 10s | 1080p | 关键补齐 |
| 高细节纹理 | 需新增 | 10s | 1080p | 关键补齐 |

### 4. 编码器覆盖范围
| 编码器 | 质量参数 | 备注 |
|--------|----------|------|
| libx264 | CRF | 基准轴 |
| libx265 | CRF | 已有等体积表 |
| libvpx-vp9 | CRF | 已有等体积表 |
| libsvtav1 | CRF | 已有等体积表，**preset 固定 8** |
| libaom-av1 | CRF | 已有等体积表，**cpu-used 固定 6** |
| librav1e | QP | 走独立刻度 `crf_to_rav1e_qp` |
| h264_nvenc | -cq | CQ 轴 |
| hevc_nvenc | -cq | CQ 轴 |
| av1_nvenc | -cq | CQ 轴，**量程 0~63** |
| h264/hevc_vaapi | -qp | QP 轴（≈ x264 QP） |
| h264/hevc_qsv | -global_quality / -q | 需上机确认 |
| *_videotoolbox | -q:v | 需 macOS 上机 |

### 5. 交付物
1. `probe/calibrate_equal_quality.py` —— 标定脚本（可复现、可扩展素材集）
2. `QUALITY_MAP_QUALITY` / `QUALITY_MAP_VMAF` —— 等质量换算表（与现有 `QUALITY_MAP` 并存，不覆盖）
3. `verify/verify_equal_quality.py` —— 回归判据（VMAF/PSNR 误差门限）
4. 方案文档新增章节：记录标定方法、素材集、拟合参数、误差分析、已知局限
5. README 更新：说明何时用等体积表、何时用等质量表

## 里程碑

| 阶段 | 交付 | 验收标准 |
|------|------|----------|
| M1 | 标定脚本 + 3 条核心素材跑通 libx265/libvpx-vp9/libsvtav1 | 单素材 ΔVMAF < 1.5 |
| M2 | 补齐 6 条素材 + 全编码器覆盖 + 交叉验证 | 留一法 ΔVMAF < 1.0、ΔPSNR < 0.3 dB |
| M3 | 主观 AB 测试（≥3 人）修正系统性偏差 | 主观与 VMAF 预测一致性 > 85% |
| M4 | 两份 `convert_crf.py` 同步 + 判据入库 + 文档归档 | §4.1 门禁全绿、无回归 |

## 风险与对策
| 风险 | 对策 |
|------|------|
| VMAF 对某些内容（动画/屏幕录制）不准 | 引入 PSNR-HVS/MS-SSIM 互补，主观测试兜底 |
| 编码器升级导致表值漂移 | 版本锁定 ffmpeg/编码器版本；CI 定期跑回归 |
| 素材集不具代表性 | 按 Netflix 公开测试集分类学覆盖；后续持续扩充 |
| 计算资源/时间过大 | 先跑「快速模式」：少素材、少参数点、只跑 VMAF；再全量 |

## 与现有体系的兼容
- **不删除**现有 `QUALITY_MAP`（等体积口径），保留给「码率受限、文件大小优先」场景
- 新增 `QUALITY_MAP_QUALITY`（等质量口径），供「画质优先、存储/带宽次要」场景
- CLI 新增 `--quality-mode volume|quality` 选择换算表（默认保持 `volume` 兼容旧行为）
- `_resolve_quality_params()` 读取对应表，**零侵入**现有逻辑

## 立即可执行的第一步
```bash
# 1. 创建标定脚本骨架（复用 calibrate_soft_offsets.py 结构）
cp probe/calibrate_soft_offsets.py probe/calibrate_equal_quality.py
# 2. 修改：体积插值 → VMAF 插值；增加 libvmaf 调用；增加多素材循环
# 3. 先跑 libx265 + 1 条素材（new5_raw.mp4）验证流程
# 4. 产出首版对比表：等体积 vs 等质量 差异量化
```

## 参考资料
- Netflix VMAF 标定流程：https://github.com/Netflix/vmaf/tree/master/resource/doc
- FFmpeg VMAF 滤镜用法：`ffmpeg -i ref -i dist -lavfi libvmaf=model_version=vmaf_v0.6.1:log_fmt=json -f null -`
- ITU-R BT.500-13 主观测试方法学
- 现有方案文档：`Plan/VidUtils_质量控制参数修复方案.md` §V9、§3、§4

---

**优先级**：P1（画质一致性是视频处理工具的核心竞争力）
**预估工期**：M1~M2 共 2~3 周（含 GPU 机上机验收 NVENC/QSV/AMF/VT）
**负责人**：待指派
**评审人**：需包含有主观测试经验的工程师