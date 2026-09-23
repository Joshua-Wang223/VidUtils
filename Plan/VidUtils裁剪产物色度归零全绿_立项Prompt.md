# 立项 Prompt（v2 · 自包含）：vidcrop_hwaccel.py 裁剪产物色度归零（画面全绿）

> **用法**：整段复制给新的 AI 会话当任务书。**本文件自包含**，不需要再看 v1。
> v2 时间：2026-09-21　｜　v1（2026-09-20）已被本版取代：v1 里仍然有效的部分（现象签名、
> 排除下游的三条证据、防复发钩子设计、验收判据）全部保留并更新；被推翻的假设集中在 §4。
>
> 关联对象：`vidcrop_hwaccel.py`（仓库根）
> 生产环境：Linux + Tesla T4，仓库 `/workspace/VidUtils`
> 开发环境：Windows 11（**无 NVIDIA GPU**，只能跑探针的自测模式）
> 数据来源：2026-09-20 / 09-21 两轮 T4 实测 + 本机 mock 枚举与装置自测

---

## 0. 一句话现状（先读这个）

**尚未定案。** 已知：

- **ffmpeg 层全部清白**：NVDEC / NVENC / 软编（libx265）/ `crop` / 显式 `hwdownload`
  两轮实测都没问题；
- **`-hwaccel auto` 不是元凶**（第一轮的结论，已被第二轮推翻：显式 `--decode cuda` 一样绿）；
- 故障需要 **「硬解 + `hevc_nvenc` + 脚本那条命令多带的某组选项」三者同时在场**；
- 唯一还没测的面 = **脚本命令比"手写等价 ffmpeg 命令"多带的 6 组选项**（§5）。

**下一步只有一个动作**：在生产机上跑 `probe/probe_green_chroma.sh`，读它第 6 节的
「最小恢复集」。**不要**再凭推理提修复方案。

---

## 1. 任务

回答并落地一个问题：

> `vidcrop_hwaccel.py` 在 `--mode crop` 下把正常片源裁成了**色度全零**的 HEVC
> （下游增强流水线据此产出全绿成品）——**是哪一组参数把 U/V 写成了 0**？

产出物：

1. 一条**可定案**的证据链（点名到具体的选项组，可复述）；
2. 一个**最小改动**的修复（不得影响已验证的 cover 模式 51.9% 加速与
   PSNR 46.60 dB / VMAF 97.21 质量门）；
3. （可选）防复发钩子：产物色度自检 + 自动降级重跑（设计见 §9，阈值已验证不误伤）。

**不改动** Video_Enhancement 流水线（其无责见 §2.2）。

---

## 2. 已确认的事实（接手方不必重做）

### 2.1 故障签名（字节级，已 dump 验证）

坏文件 `S03E01_Dora Had A Little Lamb_Cropped.mp4`：
HEVC / `profile=Main` / `pix_fmt=yuv420p` / 768×432 / 30fps / 1500 s /
`color_space=smpte170m` / `color_transfer=unknown` / `color_primaries=unknown` /
`refs=1` / `has_b_frames=2` / `encoder=Lavf61.7.100`。

| 时间点 | YAVG | UAVG | VAVG |
|---|---|---|---|
| 0 s | 15.9999（空帧） | 0.0002 | 0.0002 |
| 30 s | 91.2 ~ 94.2 | 0.013 | 0.013 |
| 60 s | 107.36 | 0.0107 | 0.0107 |
| 120 s | 150.2 | 0.0031 | 0.0031 |
| 180 s | 156.4 | 0.0042 | 0.0042 |
| 240 s | 158.5 | 0.0046 | 0.0046 |

原始 YUV 平面 dump（t=60 s，每平面 82944 字节）：

- **U 平面 82172 个为 0（99.07%）**，其余只有 1/2/3，**最大值 3**；V 平面同分布；
- Y 平面 min 10 / max 242 / mean 107.36 / 232 个不同值 ⇒ **亮度完好**。

对照组（正常文件 `S02E01_Lost Squeaky_Croped.mp4`）：**U≈105 / V≈122.5**。
副作用佐证：缺色度极易压缩，5 段平均 373 kbps，同类正常片 982 kbps
（**低码率是结果不是原因**）。

> 判据口径：U/V 最大值只有 3（不是 64 的倍数）⇒ 不是"位深截断"，更像**色度平面压根没被写对**。

### 2.2 下游 Video_Enhancement 无责 —— 三条独立证据

1. **代码**：`split_video_by_time()` 用 `ffmpeg -c copy -f segment`，**纯比特流复制**，物理上改不了像素；
2. **大小守恒**：102,531,512 B → 5 段合计 78,327,974 B，差 24,203,538 B ÷ 1500 s ≈ **129 kbps**，
   正是被 `-map 0:v` 丢掉的 AAC 128 kbps + 容器开销；
3. **用户直接核实**：裁剪**前**原片正常，裁剪**后**才绿。

另：色度 0 经 YUV→RGB→YUV 回环应得到 RGB(0,255,0)，反算回 Y≈144.5 / U≈53.8 / V≈34.2；
IFRNet 产物实测 U 54~62 / V 34~45 ⇒ **完全吻合**，下游只是忠实处理了坏输入。

### 2.3 判决口径（探针与手工检查共用）

- 正常：U/V 在 **128 附近**（本素材源的实测：U≈126.9 / V≈121.9）；
- 本缺陷：**U=V≈0.01**（判据：`U<16 且 V<16`）；
- 真灰度片是 U=V=**128**（中性），**不是 0** —— 不要用它当反例。

---

## 3. 两轮实测数据（证据总表）

### 3.1 第二轮（**当前状态**，2026-09-21）

源 = `S03E01_Dora Had A Little Lamb.mp4` 的 crop-前原片：
**h264 / High / 768×576 / yuv420p / 8bit**；目标 768×432（`crop=768:432:0:72`）。
探针与脚本用的是**同一个** ffmpeg（`/usr/local/bin/ffmpeg` 7.1）。

| 格 | 命令要点 | U/V | 结论 |
|---|---|---|---|
| A0 | `-hwaccel auto` 仅解码 | ✓ 正常（**auto→nvdec**） | auto 解析正确 |
| A1 / A2 | `-hwaccel cuda` / 软解 仅解码 | ✓ 正常 | 解码层清白 |
| B0 | `-hwaccel auto` + crop + nvenc | ✓ 正常 | auto 不是元凶 |
| B0b | `-hwaccel auto` + crop + libx265 | ✓ 正常 | — |
| B1 | `-hwaccel cuda` + crop + nvenc | ✓ 正常 | **手写最小命令不出问题** |
| B2 | `-hwaccel cuda` + crop + libx265 | ✓ 正常 | — |
| B3 | 软解 + crop + nvenc | ✓ 正常 | — |
| C1 | `hof=cuda` + 显式 `hwdownload,format=nv12` + nvenc | ✓ 正常 | 候选 1 不需要 |
| 脚本 `default` | 策略 [1/3] 自动硬件解码 + GPU 编码 | **✗ 0** | 复现 |
| 脚本 `--decode cuda` | 策略 [1/3] cuda 硬件解码 + GPU 编码 | **✗ 0** | **auto≠变量** |
| 脚本 `--decode cpu` | 策略 [1/2] 软件解码 + GPU 编码 | ✓ 正常 | — |
| 脚本 `--codec libx265` | 策略 [1/2] cuda 硬件解码 + CPU 编码 | ✓ 正常 | — |
| 脚本 `hwdec-swenc` | cuda 硬解 + libx265 | ✓ 正常 | — |
| 脚本 `all-cpu` | 策略 [1/1] 纯 CPU | ✓ 正常 | — |

⇒ **故障 = 硬解 + `hevc_nvenc` + 某组附加选项**，三者缺一不可。

### 3.2 第一轮（2026-09-20，结论**已作废**，仅留档）

同一源、同一组 ffmpeg 对照全部 ✓，脚本 `default` ✗。当时那台机器上的脚本**还是旧版**
（不支持 `--decode`，策略 2 把字面量 `'auto'` 硬写给 `-hwaccel`），于是误判
「元凶是 `-hwaccel auto`」，并推出「更新到含 `de4c776` 的版本即修」。

**该结论已被 3.1 推翻**（显式 `--decode cuda` 一样绿）。
⚠ 别再引用它，也别去改 `_generate_strategies()` 里与 `-hwaccel_output_format` 相关的那几行。

---

## 4. 已否决的假设（别重做）

| # | 假设 | 否决依据 |
|---|---|---|
| 1 | 缺显式 `hwdownload` → 自动插入的下载协商错位 | `hof=None` 时帧在**解码器侧**就回传成软件帧，滤镜串里根本没有 hwdownload；且 C1（显式下载）与 B1（无下载）**都正常** |
| 2 | 10bit `p010` 被当 nv12 取（"假设 A"） | 源是 **8bit**；且 Y 平面完好无损 |
| 3 | `crop_cuda` / 全 GPU 流水线（策略 1）参与 | `crop_cuda` 在上游**不存在**，策略 1 永不命中 |
| 4 | `-hwaccel auto` 选的不是 cuda | 实测 `auto→nvdec`，且显式 cuda 同样坏 |
| 5 | 色彩元数据（`smpte170m`）导致播放器误判 | 探针量的是**像素**（signalstats 在解码后取样），不是容器标签 |
| 6 | NVDEC / NVENC / 驱动 / 源编码有问题 | 手写命令在两轮里全绿 |
| 7 | `-err_detect ignore_err` 掩盖了真实报错 | 未被排除，但它是"放大器"不是根因（见 §5；**待第 6 节判定**） |

---

## 5. 唯一未测面：脚本命令多带的 6 组选项（**逐 token 差异**）

脚本 `--dry-run` 出的真命令（关注与"手写最小命令"的差异）：

```
ffmpeg -hide_banner -loglevel warning -noautorotate -err_detect ignore_err \
  -fflags +genpts+discardcorrupt -nostdin -hwaccel cuda -hwaccel_device 0 -y \
  -i seg.mp4 -map 0:0 -map '0:a?' -map_metadata 0 -map_chapters 0 \
  -filter:v:0 crop=768:432:0:72 -c:v hevc_nvenc -cq 23 -preset p5 \
  -c:a copy -movflags +faststart \
  -colorspace smpte170m -color_primaries smpte170m -color_trc smpte170m -color_range tv \
  out.mp4
```

手写最小命令（**正常**）：

```
ffmpeg -nostdin -y -hide_banner -loglevel error -hwaccel cuda -hwaccel_device 0 \
  -i seg.mp4 -filter:v:0 crop=768:432:0:72 -c:v hevc_nvenc -preset p4 -cq 23 -an out.mp4
```

差异分组（探针第 6 节就是按这 6 组删的，删除表达式已写好）：

| # | 组 | 脚本有 / 手写没有 | 备注 |
|---|---|---|---|
| 1 | 输出色彩四参 | `-colorspace/-color_primaries/-color_trc smpte170m -color_range tv` | **唯一会进 `AVCodecContext`、能被编码器/滤镜图读到的**；且坏文件 metadata 正是 `color_space=smpte170m` |
| 2 | 输入容错开关 | `-err_detect ignore_err -fflags +genpts+discardcorrupt` | 静默吞错，与本缺陷长期没被发现直接相关 |
| 3 | 流映射 | `-map 0:0 -map '0:a?' -map_metadata 0 -map_chapters 0` | 手写命令无 `-map` |
| 4 | preset 值 | `-preset p5` vs `p4` | 观感无关，但要排除 |
| 5 | 容器收尾 | `-c:a copy -movflags +faststart` vs `-an` | — |
| 6 | 输入侧旋转 | `-noautorotate` | 手写命令没有 |

（`-hide_banner / -loglevel / -nostdin / -y` 碰不到像素，已排除。）

> 注：脚本的**色彩参数是按源推断的**（`build_color_args`：PAL/SD → `smpte170m`）。
> 换 HD 源会变成 `bt709` —— 这是一个便宜且信息量大的旁证：
> **若元凶是第 1 组，用 HD 源裁剪很可能不复现**（值得顺手验一次）。

---

## 6. 探针：怎么跑、怎么读

### 6.1 运行（生产机）

```bash
cd /workspace/VidUtils
SRC="/workspace/output_videos/Dora/Season 03/S03E01_Dora Had A Little Lamb.mp4" \
REPO=/workspace/VidUtils \
bash probe/probe_green_chroma.sh
```

- **`SRC` 必须是 crop 之前的原片**。用裁剪后的坏产物当 SRC 会让"基准色度"失效，
  探针第 0 节会直接报「源本身没有正常色度 → 本轮结论无效」。
- 默认从第 600 秒截 3 秒（`SS=`/`SECS=` 可改）；`temp/` 不入库，工作目录是
  `<脚本所在目录>/chroma_work/`。
- 第 6b 节默认跑 **2^6 = 64 个子集**（每个只转 3 秒片段，T4 上约一两分钟）。
  只想快速看逐组结果可 `EXHAUST_BITS=0`。

### 6.2 各节读法

| 节 | 内容 | 怎么用 |
|---|---|---|
| 0 | 源属性、探针/脚本各用哪个 ffmpeg、推导的下载格式、居中裁剪偏移、源自身 U/V（基准） | 先确认"基准正常"与"两个 ffmpeg 是同一个" |
| 1 | A0/A1/A2 解码层 + `auto→实际后端` | 若 A1/A2 就绿 → 问题在解码层，与脚本无关 |
| 2 | B0/B0b/B1/B2/B3 编码层 | 若只有 B1 绿 → 落在 NVENC 上载 |
| 3 | C1 显式 `hwdownload` + `hof=cuda` | 判"候选 1"（给 crop 加 hof）是否有用 |
| 4 | 脚本矩阵 6 组（default / decode-cpu / decode-cuda / codec-libx265 / hwdec-swenc / all-cpu），每行附**实际生效的策略** | `default` 必须复现绿色；否则先查 SRC 与脚本版本 |
| 5 | `--dry-run` 打印真命令 | 与本地 mock 枚举对照；也是第 6 节的输入 |
| **6** | **命令级二分**：`V0` 原命令 → `V1..V6` 各组单独删 → `V7` 最小集 → `V8/V9` 判"硬解×色彩四参"是否成对 | 哪一格恢复正常 = 那一组是元凶 |
| **6b** | **全组合穷举 2^6**，打印「绿 N / 正常 M」与**最小恢复集** | 组合效应也能一次定位，不必再往返一轮 |

### 6.3 版本自检（**必做**，`probe/` 已入库但目标机副本可能过期）

```bash
grep -c '6b. 全组合穷举' probe/probe_green_chroma.sh   # 必须 ≥1
grep -n '═══ 6\.' probe/probe_green_chroma.sh          # 必须有第 6 节标题
```
（参考指纹：526 行、md5 `836238efb96411d4681b4650dcb05f88`；md5 仅供参考，以 6b 字样为准。）
若不符：取 `git -C /workspace/VidUtils fetch origin` 后
`git checkout origin/main -- probe/probe_green_chroma.sh`（只取这一个文件，
不动其它本地改动），或从开发机拷一份。

### 6.4 装置自检（在**任何**机器上都能跑，先跑它）

```bash
SELFTEST=1  bash probe/probe_green_chroma.sh   # 验取样/判词装置：正常片 ✓、真灰度片不误伤、零色度片判成绿
LOCALCPU=1 EXHAUST_BITS=2 SRC=<任意mp4> REPO=<仓库> bash probe/probe_green_chroma.sh
                                               # 无 GPU 也能端到端跑通 enc/scrun/二分/穷举循环
```

---

## 7. 拿到第 6 节结果后的判读表

| 最小恢复集 | 结论 | 下一步 |
|---|---|---|
| 只含**输出色彩四参** | 这四个参数在「硬解 + NVENC」链上产生了像素级副作用 | §8 候选 A |
| 只含 **`-err_detect/-fflags`** | 输入容错开关吞掉了真实错误 | §8 候选 B |
| 只含其它单组 | 对应那一组是元凶 | 按组处理（见 §8 说明） |
| **两组及以上** | 组合效应（例如"硬解 + 色彩四参"成对才出问题） | 按最小集处理，并在代码注释里写清"为何不能单独删" |
| **穷举全绿**（无任何子集恢复） | 触发面不在 §5 的 6 组里 | 回到"逐 token diff"：把第 5 节命令与手写命令**逐 token** 对齐（含**选项顺序**），并把 diff 贴进结论 |
| `default` 那一格本来就不绿 | 复现失败：SRC 不对 / 脚本版本不对 / 源已换 | 先修复现，再谈元凶 |

**判据要求**：结论必须写成「删掉 X 组后 U/V 从 ≈0 回到 ≈127（`V0` 绿、`Vx` 正常）」这种
**可核对**的形式，而不是"应该是 X 的问题"。

---

## 8. 修复候选（**按 §7 分叉，尚未拍板**）

- **候选 A（第 1 组是元凶）**：把输出端色彩四参的**下发条件**收紧 ——
  当前它是无条件下发的，而脚本对**软件编码器**本就另有 `setparams` 滤镜注入路径。
  必须分清两件事：①**标签**（容器/VUI 里的色彩描述，本次可疑对象）；
  ②**像素值域转换**（`build_range_convert_filter` 插入的 `scale=…:in_range/out_range`，
  与本题无关）。修的时候不要牵动 ②。
- **候选 B（第 2 组是元凶）**：`-err_detect ignore_err` + `-fflags +genpts+discardcorrupt`
  是"宁可静默也不能失败"的取向，代价正是本缺陷几小时没被发现。最小改动 = 去掉这两个开关
  （或按需启用），并确认不会因此让批量任务在个别损坏文件上整体失败。
- **候选 C（其它单组）**：按组最小化处理；`-noautorotate` / `-map*` 属元数据与流映射，
  真出问题说明是 ffmpeg 侧行为，需要给出"为什么"再改。
- **候选 D（穷举无解）**：不要硬改任何一行；先补一轮"逐 token diff"把差异缩到 1 个 token。

**通用约束**：改动后必须重跑 §10 的第 3 条（cover 质量门 + crop 吞吐），
并同步 README / 仓库 `memory/**`（这是发布物，见 `feedback_docs_sync_then_commit.md` 的约定）。

---

## 9. 防复发钩子（沿用 v1 设计，阈值已正面验证）

**挂点**：策略执行循环内、`if rc == 0:` **之前**。自检失败时置 `rc = 1`，即可复用既有的
"策略失败 → 清理 → 下一策略"降级逻辑，天然退到纯 CPU，**不需要新写降级**。
加 `--no-chroma-check` 开关（默认开启）。

**取样成本**：2 次 ffmpeg（源与产物各 `-ss 1 -frames:v 2 -f null`），1 次 ≈0.1~0.5 s。

**阈值（已验证不误伤）**：

```python
bad = (16 <= Y <= 235) and (U < 16) and (V < 16) and (abs(U - V) < 8) \
      and (U_src >= 16 or V_src >= 16) \
      and (abs(U_src - U) > 32 or abs(V_src - V) > 32)
```

- 真灰度片是 **U=V=128（中性）**，不是 0 ⇒ 不触发；
- 满屏纯绿 RGB(0,255,0) → U≈54 / V≈0 ⇒ `U<16` 不成立 ⇒ 不触发；
- 本缺陷 U=V≈0.003 而源 U≈126.9 ⇒ 触发；
- 源本身退化（`U_src<16 and V_src<16`）⇒ 直接返回 OK。

（探针 `SELFTEST=1` 已把"正常片 / 真灰度片 / 人造零色度片"三格跑通，可作为该钩子的回归基线。）

---

## 10. 验收判据

| # | 判据 | 通过标准 |
|---|---|---|
| 1 | 定位 | 第 6/6b 节给出**唯一且非空**的最小恢复集，能用"删前/删后命令"复述差异 |
| 2 | 线上复现校验 | 修复后重跑脚本矩阵：`default` 与 `--decode cuda` 的 U/V 回到 100~130（对照源 U≈126.9 / V≈121.9） |
| 3 | 无退化 | cover 模式质量门 PSNR 46.60 dB / VMAF 97.21 不下降；crop 吞吐不慢于修复前 |
| 4 | 真片验证 | 用**真实 S03E01 原片**重裁一次，产物 U/V 正常，且 `-c copy` 切出的分段不再发绿 |
| 5 | （可选）自检有效 | 坏文件喂自检 ⇒ 判失败并自动降级到纯 CPU 且产出正常；真灰度片 ⇒ 不误伤 |

---

## 11. 不要做的事（两轮踩出来的）

**方法层面**

1. 不要在无 GPU 的开发机上跑 `--dry-run` **取证**（探测不到 NVENC，只会落到纯 CPU 策略）；
   取证必须在生产机上做。
2. **不要只凭静态分析提修复**。两轮实测里，静态分析给出的两个主假设（缺显式 hwdownload、
   `-hwaccel auto`）**都是错的**。
3. 不要把缺陷归到 Video_Enhancement（§2.2 已排除）。
4. 不要把"码率低"当独立故障（是结果）。
5. 坏文件必须**重新裁剪**，不能直接拿去跑增强。
6. **不要把"部件都在"当验证**。本轮探针自身就栽在"我以为它跑完了"上：
   - `chk()` 里的取值管道 `line=$(ffmpeg … | awk …)` 遇上 `set -o pipefail` + `set -e`，
     **任何一格 ffmpeg 失败都会当场杀掉整个探针**，症状只是一个 `exit=127`、无报错。
     ⇒ 所有"取值管道"末尾都要 `|| true`。
   - `local tag=$1 c=$2 out="…$tag…"` 是**非法自引用**（bash 先展开所有词再赋值，
     `set -u` 下报 unbound）。
   - `c=$(f)` 里 `f` 设的变量**传不出子 shell**。
   - AWK 输出用 `%6.1f` 时小于 100 的值带**前导空格**，用 `^[0-9]` 判断"是否为数字"
     会把好数据判成"无数据"。
7. 别用 Python 文本模式写 `.sh`（Windows 上会把 LF 变成 CRLF，Linux 上
   `set -euo pipefail\r` 直接失败）；交付前按**字节**核行尾，且 `grep` 要带 `-U`。

**代码层面**

8. 不要动 `vidcrop_hwaccel.py` 里与 `-hwaccel_output_format` / `_prepend_hwdownload` 相关的行
   （第一轮的错方向）。
9. 不要在 `vidcrop_cpu_v2.py` 里塞 GPU 滤镜（它是 CPU 对照与回退路径）。
10. `vidcrop_cpu_v0/v1.py` 与 `*- Copy.py` 是有意的旧版/备份，**不要同步改**。

---

## 12. 环境与约定

| 项 | 生产机（T4） | 开发机 |
|---|---|---|
| 仓库 | `/workspace/VidUtils` | 本地工作副本 |
| GPU | Tesla T4（驱动 580 / CUDA 13） | **无 NVIDIA GPU**（只能探针自测） |
| ffmpeg | `/usr/local/bin/ffmpeg` 7.1（自建，含 `scale_cuda`） | 另一套 win64 构建 |
| python | `python3` | `python` |

- **`temp/` 不入库**（`.gitignore`），所以目标机上的 `temp/` 是它自己的副本；
  **`probe/` 入库**，探针放这里，改完提交就能随 git 走。
- ⚠ **T4 上 `$HOME` 是 `/root`**，仓库却在 `/workspace/VidUtils` ——
  别用 `$HOME/VidUtils` 推导仓库根（探针已改成从脚本自身位置推导，仍可用 `REPO=` 覆盖）。
- ⚠ **目标机 awk 是 mawk、开发机是 gawk**：awk 只写 POSIX 子集（不许跨行三元），
  且 `-v` 必须写在**程序文本之前**。探针的 `SELFTEST` 用 `awk --posix` 预解析守卫这一点。
- 相关文档（都在仓库里）：`memory/project_green_chroma_defect.md`（本议题的工程记忆）、
  `memory/project_cuda_filters_and_cover_gpu_scale.md`（cover 的 CUDA 缩放链与实测依据）、
  `memory/project_dual_env.md`（三套环境差异）。

---

## 附录 A：速查命令

```bash
# 一眼判定某个文件是否"色度归零"（正常 U≈105~127 / 本缺陷 U≈V≈0.01）
ffmpeg -v info -ss 60 -i FILE.mp4 -frames:v 1 \
  -vf 'format=yuv420p,signalstats,metadata=print' -f null - 2>&1 | grep -E 'UAVG|VAVG'

# 字节级确认（比 signalstats 更硬）
ffmpeg -v error -ss 60 -i FILE.mp4 -frames:v 1 -f rawvideo -pix_fmt yuv420p - | \
python3 -c "import sys,collections;d=sys.stdin.buffer.read();n=768*432;u=d[n:n+n//4];print(collections.Counter(u).most_common(5))"

# 生产机上取一份可复现的 3 秒片段（纯 -c copy）
ffmpeg -y -ss 600 -t 3 -i SRC.mp4 -c copy -map 0:v:0 seg3.mp4

# 脚本真实命令（只有在有 GPU 的机器上才有意义）
python3 vidcrop_hwaccel.py --input seg3.mp4 --output /tmp/dry.mp4 --mode crop \
  --output-width 768 --output-height 432 --codec hevc_nvenc --overwrite --dry-run
```

## 附录 B：本议题的固定参数（两轮实测都用它，便于横向对比）

- 裁剪：`--mode crop --output-width 768 --output-height 432`（源 768×576 → `crop=768:432:0:72`）
- 编码：`--codec hevc_nvenc --cq 23 --preset p5`（手写对照用 `p4`/`-an`）
- 解码：`-hwaccel cuda -hwaccel_device 0`（探针另有 `auto` / 软解对照格）
- 判据阈值：`U<16 且 V<16` 判为色度归零；"正常"取 `96 ≤ U,V ≤ 160`
