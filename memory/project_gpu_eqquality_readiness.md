---
name: GPU 等质量标定（M5）准备已收口，等待硬件
description: VidUtils GPU 等质量标定（立项 M5）的准备阶段——harness GPU 支持、跨仓契约 CR-1(preset p4)/CR-2(rc 显式 vbr_hq/vbr)、探针/门禁/基线——已于 2026-10-04 全部收口并提交；唯一待办是上机跑标定。T4 与 L40(AV1) 分列两份专项方案；本文件给"GPU 机上会话"的起跑指针
type: project
---

**事实**（2026-10-04）：GPU 等质量标定（立项 M5）的**准备阶段已收口，等待 GPU 就位**。
所有非 GPU 工作已完成、已提交并推送；剩余全部是"上机跑标定"。

**Why:** 本仓当前无可用 GPU（NVENC 在列表里但编不了），且 GPU 机上有既知的一批"环境假设过时"
假红。先把 harness / 跨仓契约 / 探针 / 门禁 / 基线全部做完，硬件一到即可直接开跑，避免上机后
才发现缺口而白跑一轮标定（跨仓契约 CR-1/CR-2 正是为这个目的先裁定的）。

**How to apply（GPU 就位后照此起跑）:**
- **T4（`h264_nvenc` / `hevc_nvenc`）** → 打开 `Plan/VidUtils_等质量标定_T4专项执行方案.md`，
  从 **§3 阶段 0（前置自检）** 起；§1 是 T4/L40 分列的 GPU 待办总表。
- **L40 / Ada（仅 `av1_nvenc`）** → `Plan/VidUtils_等质量标定_L40_AV1专项执行方案.md`；
  **必须**先过 `--expect-av1` 的 fail-fast（防把 SKIP 当"已验过 AV1"）。
- 阶段 1 要扩展的 harness 已实现（NVENC `-cq`/`-b:v 0`、可用性探测、`--require-codecs`/
  `--expect-av1`、`_table_range` 回退、跨仓态势 `cross_repo_status`、GPU 指纹）——直接开跑。
- 跨仓契约 **CR-1（preset p4）/ CR-2（rc 显式 `vbr_hq`/`vbr`）已落地**，两仓 `SIZE_MAP` /
  `QUALITY_MAP` 仍**逐条相等**（判据 `verify/verify_quality_mapping.py` ⑨ 组）。
- ⚠ 上机跑前先按方案 §3 阶段 0 查**并发负载**（别 kill 别人的流水线）；有 GPU 机器上
  `verify/` 与 `dump_cmd_full` 有一批**既知假红**（环境假设过时，非回归）。
- ⚠ 标定出来的表值落表后：**两仓 `convert_crf.py` 同步** + 回跑 ⑨ 组 + 更新方案 §4.12。
