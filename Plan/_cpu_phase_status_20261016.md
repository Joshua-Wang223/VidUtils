# §13.14 CPU 阶段专项测试计划 — 执行状态（2026-10-16；**R1 更新 2026-10-17**）

依据 Plan/VidUtils_质量参数统一_quality-tier方案_v3_20261015.md §13.14（**R1 压缩版**）。⚠ **进度以主方案 §12.7（R1 合并台账）为准**，此表仅为 CPU 阶段细目。

> **R1（2026-10-17）**：任务 1 探针已完成（结论见下行）；任务 3 已完成；任务 2/4 的执行方式改走 harness（`--preset-override` ✅ 已落地），且与阶段一 CLI 的时序被重排为并行（H3）。

| # | 任务 | 状态 | 产出 / 结论 | 阻塞 / 备注 |
|---|---|---|---|---|
| 1 | **aom / vp9 恒等 effort 定级** | ✅ 完成（2026-10-16/17） | 探针报告：`Accessory/calib/eqq_evidence/q3_bench/aom_effort_probe/`、`vp9_effort_probe/`。**R1 定案**：aom `balanced=6` 冻结（6≡8、4 对 balanced 过慢；BASE_LOCK 口径）、vp9 `balanced=2` 冻结（负值无增益）；快/慢端终值见主方案 §13.10R，xlsx 已同步 | 无 |
| 2 | **NVENC 三行量程与 p6/p7 可用性** | ⬜ **SKIP（本机无 GPU）** | 需在 T4 / L40 阶段补跑（`verify_nvenc_quality_gpu.py` 已记约束） | 依赖 GPU 机器 |
| 3 | **bench_preset_ab.py 并发守卫** | ✅ 完成（代码已改，默认 ≤ 逻辑核/2） | 代码 diff 在 `Accessory/probe/bench_preset_ab.py` L192-206 | 无 |
| 4 | **libx264 / rav1e per-tier 标定排期** | ✅ 方案已文档化（见下）；**全量标定：CPU 环可本机先行（空载窗口），T4/L40 待资源排期** | 排期表（8 档 × 2 编码器 = 16 条 `_EQQUAL_TIER_OVERRIDE` 条目）已定义；标定命令模板已就位（`--preset-override` ✅ 已落地） | 每档跑 3 素材 × 5 锚点，预计每档 30~60 分钟 |

---

## 任务 1：aom / vp9 恒等 effort 定级（§13.4 结论：改为恒等值）

### 背景修正（已撤回先前误读）
- `librav1e` / `libaom-av1` **不是 preset 轴**，但矩阵的「控制参数」列明确写了 `speed` / `cpu-used`，是我先前误读撤回的内容（§13.2 / 13.3）。
- **只有 libaom-av1 按核数自适应**（`auto_effort()[0]` 三处调用）；`auto_effort()[1]`（svtav1 列）在全仓引用 0 次，是死代码。svtav1 早在 V9 已冻结 preset（`DEFAULT_PRESET_SVTAV1='8'`，`vidcrop_hwaccel.py:1354-1357`）。
- 实测证据（用户提供，他机 Xeon Platinum 8255，AVX2 启用）：
  - libaom-av1 `cpu-used=4` → 508,975 字节；`cpu-used=8` → 607,995 字节（+19.5%），说明同一档不同 cpu-used 产物差异显著。
  - x265 slow→slower 在 AVX2 机器上 CPU 比约 4.8×，略高于无 AVX2 机器的 4.55~4.67×，证明该属性稳健但需更多数据定终值。

### 恒等值待定方案
| 编码器 | 当前 BASE_LOCK | 待定恒等值 | 依据 |
|---|---|---|---|
| `libaom-av1` | `['-b:v', '0', '-cpu-used', '6']` | **先冻结 6，再定级**（或根据探针结果调整） | §13.4 建议先固定再重标；待探针数据 |
| `libvpx-vp9` | `['-b:v', '0', '-deadline', 'good', '-cpu-used', '2']` | **先冻结 2，再定级** | 同上；注意 vp9 量程 `-8~8`（aom `0~8`） |

**探针命令模板**（已发送背景 agent 执行）：
```bash
# aom
python3 -c "
import subprocess, os, json
for cu in [4,5,6,8]:
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=5:duration=5',
           '-c:v', 'libaom-av1', '-b:v', '0', '-cpu-used', str(cu), '-crf', '30',
           'out_aom_cu%d.mkv' % cu]
    r = subprocess.run(cmd, capture_output=True, text=True)
    sz = os.path.getsize('out_aom_cu%d.mkv' % cu) if r.returncode==0 else -1
    print('cpu-used=%d rc=%d bytes=%d' % (cu, r.returncode, sz))
"
```

---

## 任务 2：NVENC 三行（SKIP，待 T4/L40）
- 本机 `ffmpeg -h encoder=hevc_nvenc` 无可用构建（无 GPU）。
- 矩阵中的 NVENC 行（h264/hevc/av1_nvenc）已按 preset p4~p7 填写，但实际可用范围需在有卡机器上验证（§13.8）。
- 待办：在 T4 阶段重复 §13.14 任务 2，使用 `verify_nvenc_quality_gpu.py:27` 记录的约束（Turing 无 AV1 编码单元，L40 有）。

---

## 任务 3：bench_preset_ab.py 并发守卫（✅ 完成）
- 代码改动：`--concurrency` 默认 `None` → 自动计算 `max(1, os.cpu_count() // 2)`；用户手动指定时取 `min(指定值, 上限)` 并打印守卫信息。
- 这是 §12.3 / §13.12 他机教训的直接修复：他机 4C/8T 以并发 8 开跑 13 分钟后主动中止重跑，并发 2 才正确。守卫防止此类超订。

---

## 任务 4：libx264 / rav1e per-tier 标定排期（方案已定，执行待排期）

### 设计结论（已采纳 §13.2 用户修正：preset 随 tier 变化，不再冻结 medium）

**libx264**：每档一套 preset（不再恒 `medium`），标定命令模板：
```python
# 每档一个标定任务，替代 calibrate_equal_quality.py 的 BASE_LOCK
calibrate_equal_quality.py \
  --codec libx264 \
  --preset-override <档值: e.g. ultrafast/veryfast/fast/medium/slow/slower/veryslow> \
  --tag <tier> \
  --src ... \
  --resume
```

排期表（8 档 × 1 编码器 = 8 条表行）：

| tier | preset | 说明 |
|---|---|---|
| `lossless` | veryslow | 无损母版，preset 对质量无影响但影响速度 |
| `near-lossless` | veryslow | — |
| `archive` | slower | — |
| `quality`（默认） | slow | — |
| `balanced` | medium | 与今天一致，基准轴 |
| `preview` | fast | — |
| `draft` | veryfast | — |
| `test` | ultrafast | — |

**librav1e**：每档一套 `-speed`（native / 0~10）。**R1 档值（= §13.10R）**：test=10 / draft=8 / preview=7 / balanced=**6（显式 `-speed 6`，与 native 逐字节相同）** / quality=4 / archive=2 / near=1 / lossless=0：

| tier | speed | 说明 |
|---|---|---|
| 全部八档 | 10 / 8 / 7 / 6 / 4 / 2 / 1 / 0 | 每档需独立 `_EQQUAL_TIER_OVERRIDE` **系数行**（speed 值本身不进系数表——它是 `TIER_PROFILES` 的职责） |

**待办（实际执行）**：
1. 对每档运行 `calibrate_equal_quality.py --preset-override <档值> --tag <tier>`，产物入 `_EQQUAL_TIER_OVERRIDE[tier]`。
2. 更新 `CALIB_FINGERPRINT`（`calibrated` → True，`source` 指向标定产物路径，`evidence` 写入素材列表与环境指纹）。
3. 更新概览 `verify_overview_lockstep.py`，确保各档概览字段逐字一致。

---

## 当前阻塞与后续步骤（R1 更新）
- **状态以主方案 §12.7（R1 合并台账）为准**；本文件不再单独维护进度。
- 任务 1/3 已完成；任务 2 待有卡机器；任务 4 的档值已定（§13.10R），**执行前置（`--preset-override`）✅ 已落地**。
- **下一步（按序）**：
  1. ~~`Accessory/probe/calibrate_equal_quality.py` 落 `--preset-override`~~ ✅ 已完成（2026-10-17；`_lock_for` 统一替换速度轴 + 自测 4 例）。
  2. 启动 CPU 环：§13.10R 档值 × 六编码器逐档标定（LOO 软编 ≤5.9 / rav1e ≤7.5），产物落 `Accessory/calib/eqq_evidence/<tier>_cpu/`；§B2 的锚点扩展（+0/6/33/39/45）先决。
  3. 主方案阶段一 CLI 实现与 CPU 环**并行**（H3）；其 DoD 见主方案 §11。
  4. T4 / L40 阶段补 NVENC 量程核验、`p5/p6/p7` 触发率（红线 1%）与 `_QP_AFFINE_QUALITY` 重标（任务 2 + Part B §B5）。
