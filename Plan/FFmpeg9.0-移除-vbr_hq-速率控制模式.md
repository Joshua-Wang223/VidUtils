# FFmpeg 9.0 移除 `vbr_hq` 速率控制模式

## 一句话结论

`vbr_hq` 的移除是 **NVIDIA NVENC SDK 升级导致的必然结果**，并非 FFmpeg 自身的决定。虽然选项名称消失，但通过 `-rc vbr -tune hq -multipass fullres` 的组合仍可实现高质量可变码率编码。

---

## 一、事件背景

| 项目 | 内容 |
|---|---|
| 发生版本 | FFmpeg **9.0**（代号 "Lei"） |
| 变更内容 | 正式移除 `vbr_hq` 速率控制模式 |
| 根本原因 | 所依赖的 **NVIDIA NVENC 视频编码 SDK** 发生根本性变化 |

**因果链**：NVIDIA 在新版 SDK 中废弃并移除了 `VBR_HQ` 等一批旧速率控制模式 → FFmpeg 9.0 相应移除已废弃的 NVENC 选项 → 同时**停止支持 11.1 版本之前的 SDK**。

---

## 二、具体表现

在 FFmpeg 9.0+ 中继续使用 `-rc:v vbr_hq`，会报错：

```
Specified rc mode is deprecated.
Use -rc constqp/cbr/vbr, -tune and -multipass instead.
```

---

## 三、替代方案

官方迁移建议：用 `-rc vbr` 配合 `-tune hq` 和 `-multipass` 实现同等或更优的画质控制。

```bash
ffmpeg -i input.mp4 -c:v h264_nvenc -rc vbr -tune hq -cq 23 -b:v 0 output.mp4
```

- `-cq`：控制质量，**数值越低质量越高**
- `-b:v 0`：让编码器完全依据 `-cq` 值决定码率

---

## 四、移除原因：从“黑盒预设”到“模块化”

旧版 `VBR_HQ` 是一个**一体化的“高级”预设**，内部固定启用若干高质量特性（如多遍编码）。NVIDIA 从 **Video Codec SDK 10.0** 起重构了这一设计：

1. **解耦与细化** — 把过去捆绑的功能拆开，使 `-rc`（速率控制模式）、`-tune`（编码调优）、`-multipass`（多遍编码）可独立控制。
2. **提升灵活性** — 开发者可按需精细调配编码设置，而非只能在几个固定“套餐”中选择。

---

## 五、核心区别：新组合 vs. 旧模式

| 对比维度 | `-rc vbr_hq`（旧） | `-rc vbr -tune hq`（新） |
|---|---|---|
| **控制粒度** | 黑盒预设，固定启用高质量分析与多遍编码，无法单独调整 | 模块化组合：`-tune hq` 调整成本函数权重以偏向高质量，多遍编码由 `-multipass` 独立控制 |
| **参数构成** | 隐含“多遍编码”等特性 | `-tune hq` **不含**多遍编码，需手动添加 `-multipass fullres`（或 `qres`）才能贴近旧效果 |
| **API 架构** | 旧的、已废弃的 API 架构 | 全新推荐的 API 架构，未来持续支持 |

---

## 六、总结

NVIDIA 移除 `vbr_hq` 是为了推动编码器 API 向**更现代化、更灵活**的方向演进。新方案虽然需要手动配置更多选项，但也换来了**更精细的控制能力**。

> **迁移速记**：`-rc vbr_hq` → `-rc vbr -tune hq -multipass fullres`（或 `qres`）
