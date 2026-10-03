---
name: 码率控制轴：--rc-mode / --qp / --lookahead / --bitrate（两脚本同名同默认）
description: 2026-09-22 两个裁剪脚本新增的四个码率控制参数——默认值基本=不下发（CR-2 例外：h264/hevc NVENC 的 auto 默认发 -rc vbr_hq）、-rc/-qp 是 NVENC 专属（非 NVENC 告警忽略、strict 报错）、--lookahead 按编码器映射且默认值三边不同、实测 -x265-params 后者整条覆盖前者故必须与 HDR 元数据合并成同一条；2026-09-28 追加：-qp 不是一套刻度（AV1 是 0~255 qindex、VAAPI 0~52），constqp 必须走 QP 尺度层；av1_nvenc 的 -cq 实为 0~63；2026-10-03 追加：等质量表 QUALITY_MAP 只覆盖软编、硬编回退 SIZE_MAP，GPU 标定的前置是扩展只支持软编的 harness（calibrate_equal_quality.py 聚合段对未落表的 nvenc 会 KeyError）；2026-10-04 追加：CR-2 把 NVENC 默认 rc 改为显式下发（h264/hevc=vbr_hq、av1=vbr，与 VE 一致）
type: project
---

## 事实

2026-09-22 给 `vidcrop_hwaccel.py` 与 `vidcrop_cpu_v2.py` **同时**新增四个参数（同名同默认），
组成一条"**码率控制轴**"（与解码/缩放/编码三轴正交，轴之间零冲突检查）：

| 参数 | 取值 | 默认 | 落到命令上 |
|---|---|---|---|
| `--rc-mode` | `auto` / `constqp` / `vbr` / `vbr_hq` / `cbr` / `cbr_hq` / `cbr_ld_hq`（可写 `nvenc-<mode>`，裸名也收） | `auto`＝**NVENC 显式下发默认 rc**（CR-2，2026-10-04）：h264/hevc=`-rc vbr_hq`、av1=`-rc vbr`；软编/VAAPI 不下发 | `-rc <mode>` |
| `--qp` | 0–51 | `None` | `-qp N`，**只在 `constqp` 下** |
| `--lookahead` | 0–250 | `None` | 见下面映射表 |
| `--bitrate` | `8M` / `8000k` / `12000000` | `None` | `-b:v <码率>`，**所有编码器都下发** |

### 默认值的两层口径（用户明确要求先讲清这个才动手）

- **CLI 层**：四个参数的默认都不表示某个具体值，而是"**不下发任何相关选项**"。
  ⚠ **2026-10-04（CR-2）有唯一例外**：NVENC 的 `auto` 现在会**显式下发默认 rc**
  （h264/hevc=`-rc vbr_hq`、av1=`-rc vbr`，与 VE 口径一致，见下方「追加：CR-2」）；软编仍不下发。
- **实际生效值**＝各编码器/preset 自带的默认，而**它们本来就不同**：
  `-rc` 默认 `-1`（不覆盖 preset，配 `-cq 23` 即"VBR + 目标质量"）；
  `-rc-lookahead` 在 NVENC 上默认 **0（关闭）**、x265 默认 **20**、x264 由自身决定
  （ffmpeg 选项默认 `-1`＝交给 x264）。⇒ 两脚本默认编码器不同
  （hwaccel `h264_nvenc` / v2 `libx264`），所以"都不写"时同一批素材的前向预测深度**本来就不一样**。
- 因此新参数**不能**把两侧拉平成一个数值——那会改掉现有输出。回归判据仍是
  「不传新参数时命令与改动前逐字相同」。

### `--lookahead` 的按编码器映射（都实测过 `ffmpeg -h encoder=<x>`）

| 编码器 | 下发 | 依据 |
|---|---|---|
| libx264 | `-rc-lookahead N` | 顶层选项存在（默认 -1） |
| `*_nvenc`（h264/hevc/av1） | `-rc-lookahead N` | 顶层选项存在（默认 0） |
| libx265 | `-x265-params rc-lookahead=N`，**与 HDR 元数据合并成同一条** | 无顶层选项；见下 |
| libvpx / libvpx-vp9 / libaom-av1 | `-lag-in-frames N` | 顶层选项存在（vp9 另有 0~25 的 `-rc_lookahead`，取通用的那个） |
| libsvtav1 及其它 | **不下发** + 告警 | 只有 `-svtav1-params`，键名未实测 → 不瞎发 |

### ⭐ 实测：两次 `-x265-params` 是**后者整条覆盖前者**（这条是本轮最值钱的发现）

单变量三格（本机 ffmpeg，256×256 2 秒 lavfi）：

| 命令 | x265 报出的 Lookahead |
|---|---|
| `-x265-params rc-lookahead=40` | 40 |
| `-x265-params rc-lookahead=40` 再 `-x265-params log-level=info` | **20**（前一条被整条丢弃） |
| 反序 | 40 |

而 HDR10 静态元数据（`master-display` / `max-cll` / `hdr10=1`）也走 `-x265-params`，
且 `--extra-args` 排在它**之后**（hwaccel `cmd += extra_args`、v2 同序）⇒
**在 HDR 片源上用 `--extra-args -- -x265-params rc-lookahead=40` 会静默抹掉 HDR 元数据**。

修法：`build_hdr_args()` 加 `extra_x265_params=` 形参，把 lookahead 的键**合并进同一条**
（HDR 那三个键在前、顺序不变 → 不传 lookahead 时输出逐字不变）。
⚠ `--extra-args` 里手写的 `-x265-params` 仍会整条覆盖脚本自己发的那条（含 HDR）——
要加 x265 参数优先用 `--lookahead`；这条已写进 README 已知限制。

### 互斥/共存规则（"按 rc 模式区分"，用户拍板）

- `constqp` + `--bitrate` → **报错退出 2**。理由：constqp 是恒定 QP，NVENC **完全无视 `-b:v`**
  （T4 实测，见 `project_t4_gpu_capabilities.md`），并存等于静默丢码率。
- `constqp` 缺 `--qp` → **报错**（并给可抄示例 `--rc-mode constqp --qp 23`）。
- `constqp` + `--cq`/`--crf`/`--crf-ref`/`--cq-ref` → **报错**（量纲不同：constqp 用 `--qp` 表达质量）。
- `auto` / `vbr*` / `cbr*` + `--bitrate` + 质量参数 → **允许并存**，打一条提示：
  语义是"受码率约束的恒定质量"（`-b:v` 作上限；VP9 下即 constrained quality）。
  ⇒ VP9 的既有 `-b:v 0`（配 `-crf` 才是纯恒定质量）在给了 `--bitrate` 时**不再补**，
  否则同一个 `-b:v` 会发两次。
- `cbr*` 未给 `--bitrate` → 告警（会落到 ffmpeg 默认 200kbps）。
- 非 `*_nvenc` 编码器下给 `--rc-mode`/`--qp` → **告警忽略**（hwaccel `--fallback-policy strict`
  下报错）。判据是**实际编码器**（逐策略解析），所以判定放在 `apply_rc_control_args()`（命令构建处），
  不在 CLI 层。

### 其它实现要点

- ⚠ **在 Windows 上用 `Path.write_text()` 批量改写脚本会把整份文件转成 CRLF**（默认 newline
  转换），`git diff` 立刻变成"整文件重写"（本次实测：5175 行的 `vidcrop_hwaccel.py` 显示
  `10518 ++++----`）。改完必须 `file <脚本>` 确认还是 LF（HEAD 里两个脚本都是 LF），
  必要时用 `p.write_bytes(b.replace(b'\r\n', b'\n'))` 转回来。
  ⇒ 批量替换优先用 Edit 工具，非要用 Python 就**用二进制模式读写**。
  （另：核对行尾别用 `grep -c $'\r'`，它在某些写法下会退化成 `grep -c ''` 报总行数——用 `file`。）
- 报错走「函数抛 ValueError → 调用方打成 `[ERROR] …`」，**不交给 argparse 的 `type=`**，
  否则 `usage:` 行会抢在报错前面、两脚本的**报错首行就没法逐字对比**（孪生约定要它一致）。
  argparse 只保留 `type=int`（`--qp` / `--lookahead`）。
- `--rc-mode auto` **允许显式写**（含 `nvenc-auto`）——它的默认值就叫 auto，禁止它会重演
  "帮助里写的默认值敲不出来"那个坑（`--scale-algo auto` 就是这种简写）。
- `--rc-mode` 的取值表/量程/提示常量在两脚本里逐字相同，判据里断言相等
  （`_RC_MODES` / `_RC_MODE_HELP` / `_RC_MODES_WITH_BITRATE` / `_LOOKAHEAD_RANGE` /
  `_QP_RANGE` / `_LOOKAHEAD_HINT` / `_QP_HINT`）。
- v2 没有 `--fallback-policy`（也没有 `--decode`），所以那边一律"告警"、没有 strict 分支：
  这是既有的**有意不对称**，不是分叉。

## Why

用户的原始诉求是问"两个脚本里 Rate_mode 与 Lookahead 怎么处理的"——**这两个参数当时并不存在**
（全仓 grep 只命中 x265 自己的日志行与他自己 TensorRT 流水线里的 `-rc vbr_hq`）。
把前提摆正后他按"先讲清现有默认值再动手"的顺序拍板了四轮：
取值范围（同时加 `--bitrate`）→ 覆盖范围（全编码器映射）→ `constqp` 的质量参数（新增 `--qp`）
→ `-b:v` 与质量参数的关系（按 rc 模式区分）+ 下发范围（所有编码器）。

## How to apply

- 报"默认值"时必须**分两层**说（CLI 默认＝不下发 / 编码器自带默认各不相同），别简化成一个数。
- 动 `-x265-params` 相关的任何东西：先记住"**后发的那条整条覆盖前一条**"，
  要写多个键就**合并成一条**；`--extra-args` 仍在最后，会覆盖脚本自己发的。
- 新增"某个后端专属"的参数时照这套走：取值表 + 前缀可选 + 非该后端时**告警忽略**
  （而不是报错），并在 README 已知限制里写清"本机/本编码器下拿不到什么"。
- 回归：`test/dump_enc_options.sh`（第三道门，比 `-c:v/-cq/-crf/-qp/-b:v/-rc/-rc-lookahead/
  -lag-in-frames/-preset/-x265-params`）对照 `test/baseline/enc_before.txt`；
  判据 `verify/verify_rc_lookahead.py`（九组，含 CLI 层两脚本报错首行对比）。
- ⚠ 上机（T4）仍需核一次 `ffmpeg -h encoder=hevc_nvenc` 的选项表：本机 ffmpeg 只验到
  "`-rc <name>` 的**名字**合法"（解析通过后死在 `Cannot load nvcuda.dll`），
  运行期语义以 `project_t4_gpu_capabilities.md` 的记录为准。

## 追加（2026-09-24）：`--qp` 的降级映射 + `0` 是特殊档哨兵 + `-ref` 能落到 constqp

这一轮把「质量意图 → 目标编码器原生参数」收敛到 `_resolve_quality_params` **一个点**
（返回值由 `(crf, cq)` 扩成 `(crf, cq, qp)`，两脚本同步）。四个缺口都是**实测复现**的：

| 缺口 | 改前（实测） | 改后 |
|---|---|---|
| `--rc-mode constqp --qp 18` 在 NVENC 不可用降级到 CPU 时 | QP **静默丢弃**、质量落到默认 `-crf 21` | 换算成 `-crf 14`（hevc_nvenc 量纲） |
| `--cq 0` / `--qp 0`（无损）降级到 libx265 | 只得 `-crf 3`（**不是无损**） | `-crf 0` + `-x265-params lossless=1` |
| `h264_nvenc` 的 cq **0~5** → libx264 / libvpx-vp9 | **全部** → `-crf 0` = 真无损（`--cq 4 --codec libx264` 静默产出巨大文件） | 非无损换算结果统一**钳到 ≥1** |
| 字面量 `--crf 21` 落到 `hevc_nvenc` | 忽略 + 回落默认 `-cq 23`（用户给的值蒸发） | 按 libx264 CRF 口径换算 → `-cq 28` |

**`0` = 特殊档哨兵，不参与线性换算**（各编码器的 0 都是极值档，而 `a×x+b` 会把 0 当普通下界）。
5 路 0 值输入（`--crf` / `--cq` / `--qp` / `--crf-ref` / `--cq-ref`）统一投影成目标的
0 档：`libx265` → `-crf 0` + `lossless=1`；`libvpx*` → `-crf 0 -b:v 0`；`librav1e` → `-qp 0`；
NVENC → `-rc constqp -qp 0 -b:v 0`（显式 `--qp 0` 此前缺 `-b:v 0`，已统一）。
⚠ **低端饱和是固有性质**：`--cq 1~5` 落到 libx264 / libvpx-vp9 会被钳到同一个最小值、
在等效表里分辨不出来。

⭐ **「0 = 真无损」只对 CPU 编码器成立（2026-09-24 T4 + 本机实测）**：

| 目标 | 0 档的实际下法 | 是否**逐位**无损 |
|---|---|---|
| `libx264` / `libx265` | `-crf 0`（x265 另加 `lossless=1`） | ✅ 是（本机 `LOCALCPU=1`：framemd5 **0/50** 帧不同） |
| NVENC（hevc / h264） | `-rc constqp -qp 0 -b:v 0` | ❌ **不是**（T4：**43417/43448** 帧不同，只是最高质量档） |
| `libvpx-vp9` / `librav1e` / `libsvtav1` | `-crf 0 -b:v 0` / `-qp 0` | 未实测 |

装置可信度：同轮负向对照 `-qp 18` = 43443/43448（有分辨力），而同一装置在本机 libx265 上是 0/50
⇒ 那个「≠0」是真测量。**工具文案已据此改口**（原 cpu_v2 告警把 `--rc-mode constqp --qp 0`
当无损出路推荐，是错的；现在指向 `libx265`/`libx264` 的 `-crf 0`）。
探针 `probe/probe_lossless_qp0.sh`、验收 `probe/t4_acceptance.py` C 组。

**组合规则**（用户拍板）：`--rc-mode constqp` 下 `--qp` / `--crf-ref` / `--cq-ref` **三选一**
（同给报错——量纲不同）；字面量 `--crf` / `--cq` 与 `--bitrate` 仍拒。

**How to apply**：再遇到"某参数落到不支持它的编码器"时，默认动作是**换算 + 提示**，
不是丢弃；只有"0 / 无损"这类**语义哨兵**才跳过换算。判据
`verify/verify_quality_mapping.py`（八组，含"非无损换算永不落到 0"的小值扫描）。

## 追加（2026-09-28）：`-qp` 不是"一套刻度"，`-cq` 的量程也各有不同

### ⭐ 最值钱的一条：`-qp` 的刻度按编码器分家（`ffmpeg -h encoder=<x>` 实测）

| codec | `-cq` 量程 | `-qp` 量程 | 与 x264 QP 的关系 |
|---|---|---|---|
| h264_nvenc / hevc_nvenc | 0~51 | −1~51 | **同尺度**（QP 就是真 QP） |
| **av1_nvenc** | **0~63** | **−1~255** | `-qp` 是 **AV1 qindex**，≈ 4×QP |
| libx264 / libx265 | 无（用 -crf） | 0~51 | 同尺度 |
| libsvtav1 | 无 | 0~63 | = crf 刻度 |
| librav1e | 无 | −1~255 | 4×（表里已有 `(4.0, -4.0)`） |
| h264/hevc_vaapi | **无** | 0~52 | 同尺度 |
| h264_qsv 等 | **本机该编码器无 `-cq`**（`-preset` 也只收 `veryfast..veryslow`） | — | — |

⇒ **两项目在 constqp 上都把 CQ 轴值（或基准轴值）直接当 `-qp`**：
- VidUtils 拿 CQ 轴值（`crf_ref 21 --codec hevc_nvenc` → `-qp 28`），
- VE 的 `to_constqp_qp()` 回基准轴（→ `-qp 20/21`，对 H.264/HEVC 是对的），
- **但 AV1 上两边都错**：`-qp 21/27` 落在 0~255 上是**近无损**（体积暴涨），应为 ~84。
  正确做法是先有"QP 尺度层"（`_QP_SCALE = {av1_nvenc:4, librav1e:4, 其余:1}`），
  且 AV1 的 QP 量程要**另立表**（`QUALITY_MAP` 的 `(lo,hi)` 描述的是 CQ 轴，
  拿它夹 QP 会把 84 夹回 63）。

### 已落地（2026-09-28，两脚本 + 两份 convert_crf.py）
- `QUALITY_MAP['av1_nvenc']` 的 hi **51 → 63**（原先把 `crf_ref 45~51` 全挤在 51）。
- 新增 `literal_range()`：字面量按**生效编码器**量程校验（CLI 层拒绝 + 同族下发钳位）。
  修前 `--cq 60 --codec h264_nvenc` 让 ffmpeg 报 out of range 直接失败、
  `--crf 60 --codec libx264` 被静默按 51 编码。
- 新增 `_QP_ONLY_CODECS = {h264_vaapi, hevc_vaapi}` → `encoder_supports_qp()`；
  质量输入归一到基准轴后走 `-qp`。修前 `--cq 26 --codec h264_vaapi`
  **一条选项都不发、且无告警**（静默丢值）。
- 判据：`verify/verify_quality_mapping.py` 新增 **⑨ 组（跨项目体检，13 项）** 与
  **⑩ 组（V3/V4/V6 正向断言）**；⑨ 组默认只报告、`STRICT_KNOWN=1` 转门禁。

### 追加（2026-09-28 第二轮）：V1/V2/V5/V7/V8/V9/V10/V11/V12 全部落地

这一轮把方案 `Plan/VidUtils_质量控制参数修复方案.md` 的剩余项全部做完，两脚本 + 两份
`convert_crf.py` 同步（VE 侧 `src/utils/convert_crf.py` 与本仓逐字相同）。

| 项 | 落地要点 |
|---|---|
| **V1** | `-qp` 回**基准轴**（不再拿 CQ 轴值直发）。新增 `_QP_SCALE`（AV1 族 ×4）/ `_QP_LIMITS`（av1_nvenc 0~255，**不能**拿 QUALITY_MAP 的 CQ 量程夹）/ `to_constqp_qp()` / `from_constqp_qp()`；constqp 的 `-crf-ref`/`-cq-ref` 与 `--crf` 落硬编都走它。`h264_nvenc cref21 → -qp 21`、`hevc_nvenc → 20`（=VE 的 `to_constqp_qp`）、`av1_nvenc → 84`（21×4） |
| **V2** | `--qp` 落软编按基准轴回算：`hevc_nvenc constqp --qp 18 → libx265 -crf 18`（此前按 CQ 轴得 14） |
| **V5** | QSV 移出 `CQ_SUPPORTED_CODECS`（实测无 `-cq`）、VT 也移出（质量轴是 `-q:v`）、去掉 cpu_v2 的 `h265_nvenc` 冗余 ⇒ 两脚本 CQ 集相等。QSV 的 preset 走 x264 档名映射（实测 `-preset` 收 veryfast..veryslow）；对"有质量输入但无质量轴"的编码器补**告警**（不再静默丢值） |
| **V7** | `DEFAULT_CRF`/`DEFAULT_CQ` → `DEFAULT_REF=21`；未给质量时按基准换算（libx264 21 / libx265 21 / h264_nvenc 26 / hevc_nvenc 28），修掉"硬编默认过配 3 档" |
| **V8** | `librav1e` 移出 `CRF_SUPPORTED_CODECS`（对齐 VE 的 `supports_crf`）；字面量 `--crf` 与 `--crf-ref` 都走基准轴 ⇒ 都 `-qp 80`（此前字面量 64）。build 层独立判断 librav1e 下发 `-qp` |
| **V9** | **真实素材等体积标定**（`input_videos/new5_raw.mp4` 1080p→720p 4s，`probe/calibrate_soft_offsets.py`）：`libx265 → 0.9155x+1.6385`、`libvpx-vp9 → 1.6198−5.7553`、`libsvtav1 → 1.9450−15.62`；两份 `convert_crf.py` 同步。`default_preset_for('libsvtav1')` 固定 `8`（不再随核数漂）。⚠ 等体积≠等质量、素材单一 |
| **V10** | preset 表拆成两张**刻意不对称**的表（详见 `project_preset_equivalence.md` 约定 1） |
| **V11** | 两份 `convert_crf.py` 的 `hevc_videotoolbox` b **105→100**（lo=1 可达、不再 crf 0~2.58 全饱和） |
| **V12** | 判据 ①②⑥ 期望值更新、⑨ 待修项逐条转 `chk`（**⑨ 现在默认就是门禁**，`STRICT_KNOWN=0` 可降级为只报告）、新增 **⑪ 组正向断言**、基线更新（`enc_before.txt` 的 hevc_nvenc 行 crf 16→13） |

**关键设计决定（用户拍板）**：
- V9 用**真实素材**标定再落表（不用方案里合成素材的拟合值）——结果与合成拟合差异大，
  且 libx265 的真实值也偏离旧表（故三张表都改）。
- V10 preset 对齐 **VE 当前的官方 ffmpeg 枚举**，但 `DEFAULT_PRESET_GPU` 保持 `p5`、
  GPU→CPU 反向表保持 `p5→medium`（否则破坏两脚本命令 lockstep + 基线）。
- V5 以**实测/规范**为准统一两脚本能力集。

**How to apply**：
- 再动 `QUALITY_MAP` 的 a/b：两份 `convert_crf.py` 必须逐字同步，并重跑
  `verify/verify_quality_mapping.py`（⑨ 的"真源逐条相等"会红）。
- 再动 preset 表：先看 `project_preset_equivalence.md` 约定 1，跑 `dump_cmd_full.sh` + `dump_enc_options.sh`。
- 再动 `-qp`：记住它**不是**一套刻度（AV1 是 0~255 qindex、VAAPI 0~52、H.264/HEVC 0~51），
  且 constqp 走 `to_constqp_qp()`（基准轴），CLI 量程用 `qp_range(codec)`。
- 判据：`verify/verify_quality_mapping.py`（①~⑪ 组，⑨ 现在是门禁）；
  上机脚本 `probe/verify_nvenc_quality_gpu.py`（T4 无 AV1 NVENC 会自动 SKIP、L40 跑满）；
  标定脚本 `probe/calibrate_soft_offsets.py`。

## T4 上机实测结果（2026-09-28，Tesla T4 / 驱动 580.65.06）

素材 `input_videos/new4_raw.mp4`（1080p HEVC 18.7s）；软编基准 libx264 `crf 21` =
PSNR 44.02 dB / 6054 kbps。报告：`verification_report/nvenc_quality_T4_20260928_062328.{json,md}`。

- **B 组（`-cq` 偏移）成立**：h264 `-cq 26` → ΔPSNR **+0.69 dB** / 码率 **1.17×**（容忍带内）；
  hevc `-cq 28` → **+0.39 dB** / **0.76×**（带内）。朴素值 `-cq 21` 对照 = 2.20× / 1.60×（越界）
  ⇒ **偏移方向与幅度都对，`QUALITY_MAP` 的 b 不必改**。
- **C 组（constqp `-qp` 回基准轴）成立**：h264 `-qp 21` → **+1.66 dB** / **1.37×**；
  hevc `-qp 21` → **+1.93 dB** / **1.06×** ⇒ **V1 前提「constqp 的 `-qp` = 基准轴」成立，
  无需引入 `CONSTQP_QP_OFFSET`**。
- **av1 两格 SKIP**：T4 的 `av1_nvenc` 实探测到 `error code -22 (Invalid)`（列表里有、编不了）
  ⇒ `-cq` 偏移与 `-qp` ×4 尺度**仍待 L40/Ada 判定**。
- `probe/t4_acceptance.py`：**20/20 通过**（修掉 A 组 3 处滞后期望值后，见
  `project_t4_gpu_capabilities.md` 末节）；C1 复证 NVENC `-qp 0` **不是**逐帧无损（561/561 帧不同）。

**L40/Ada 交接（2026-09-28 备好，方案 §4.10）**：AV1 的 `-cq` 偏移与 `-qp` ×4 尺度是
**唯一还没端到端实测**的两条，需 Ada 及以上 NVENC。探针已加 `--expect-av1`：**显式 opt-in 的
fail-fast**——不加它时非 AV1 卡上那几格静默 SKIP（退出码仍 0，易误判成"跑绿了"），加了它则
要求本卡能编 AV1，否则 **exit 2**。跑完 B/C 组各多一行 `-结论`（`B-av1-结论` / `C-av1-结论`），
直接给「动不动 `QUALITY_MAP` 的 b / 动不动 `_QP_SCALE`」的可执行结论；两个纯函数结论已纳入
`--selftest`（避免首次上机才暴露）。步骤、判读矩阵、与 T4 报告对比见方案 §4.10。

## 追加（2026-10-03）：等质量 GPU 标定的**前置缺口**与两份专项方案

**事实**：等质量表 `QUALITY_MAP`（VMAF 定标，`--quality-mode quality` 默认）**目前只覆盖 5 个软编**；
硬编（NVENC/QSV/AMF/VT）经 `set_quality_mode()` 的 `_ACTIVE_MAP` **回退读 `SIZE_MAP`（等体积）**。
GPU 侧（立项 M5）要做的就是把 NVENC 的等质量行补进 `QUALITY_MAP`、解除回退。

⭐ **最值钱的一条（动手前必看）**：为 NVENC 做等质量标定，**不能直接跑旧 harness** ——
旧版 `probe/calibrate_equal_quality.py` **只支持软编**：`SWEEP` / `QUALITY_FLAG` / `BASE_LOCK`
均无 nvenc；聚合段的 `CRF.QUALITY_MAP[codec][2:4]` 对**尚未落表**的硬编会 **KeyError**
（硬编当前回退 `SIZE_MAP`）。**⇒ 已落地修复（2026-10-03，本仓；VE 侧随后自行维护其 harness，
含 VE 独有 `--axis`）**：

- 新增 NVENC 档位：`SWEEP` / `BASE_LOCK`（`-b:v 0` + `-preset p4`（CR-1）+ 显式默认 rc（CR-2））/
  `QUALITY_FLAG='-cq'`（h264/hevc 量程 0~51、av1 0~63）；
- **聚合段量程改走 `_table_range()`**：`QUALITY_MAP` → `SIZE_MAP` 回退，修掉 KeyError；
- **可用性探测** `probe_hw_codec()`：真编一小段、判 `rc==0 且产物非空`（列表里有 ≠ 本机可编，
  T4 的 av1_nvenc 会「报 -22、0 字节」）；不可用即跳过该档；
- **fail-fast**：`--require-codecs`（通用）/ `--expect-av1`（= 要求 av1_nvenc 且并入档位，
  不可编即 **exit 2**，防把 SKIP 当已验）；`--expect-av1` 与 `--quick` 互斥；
- **指纹**：报告与启动打印加入 `gpu`（nvidia-smi 名字/驱动）、`hw_avail`；
- **跨仓态势感知** `cross_repo_status()`（新增）：启动即打印并在 `report.json` 落 `cross_repo` ——
  本仓角色（VU/VE）、对侧仓库路径、**两表 `SIZE_MAP`/`QUALITY_MAP` 是否逐条相等**、
  对侧 harness 的 md5 是否同版、对侧方案文档位置。`--sibling-root` 可显式指定，缺对侧则优雅降级。
- 自测 `--selftest` 已补 9 项纯逻辑断言（`_table_range` 回退、`_lock_for`、`_missing_required`、
  `_cmp_tables`、`_repo_role`），无需 ffmpeg 即可跑。

⚠ **三处仍需注意**：① **VE 侧 harness 已自行维护**（含 VE 独有 `--axis`/QP 轴，见跨仓契约 CR-3）；
两 harness **有意不同版** ⇒ 跨仓态势报「与本仓不同」属**预期**、不是未同步（VE 用 `-rc:v`、VU 用裸
`-rc`，行为等价）；② **CR-1 已落实**：标定 harness
与验收探针 `probe/verify_nvenc_quality_gpu.py` 统一 `-preset p4`（与 VE 口径一致），且
**产品默认 `DEFAULT_PRESET_GPU` 由 p5 改为 p4**（两脚本孪生：`vidcrop_cpu_v2.py` + `vidcrop_hwaccel.py`；
降级点随之 `NVENC_TO_X264_PRESET['p4']='medium'`、`NVENC_TO_SVTAV1_PRESET['p4']=8`，基线
`test/baseline/enc_before.txt` 第 12 行 `p5→p4` 已同步；lockstep 不破）；
③ ⭐ **验收探针 `group_a` 曾直接索引 `crf_mod.QUALITY_MAP`**：2026-09-30 把等体积表改名
`SIZE_MAP` 后，`QUALITY_MAP` 里不再有 nvenc 行 ⇒ `--quick`/A 组直接 **KeyError: 'h264_nvenc'**。
已改走 `crf_mod.get_quality_map()`（活动表，默认 quality 模式下硬编回退 SIZE_MAP）。**教训：
凡按编码器查表，一律走 `get_quality_map()`，别直接索引 `QUALITY_MAP`/`SIZE_MAP`。**

**已落地方案**（供后续直接照抄执行）：
- `Plan/VidUtils_等质量标定_T4专项执行方案.md` —— `h264_nvenc`/`hevc_nvenc` 的 `-cq` 等质量
  标定 + constqp 回归 + 硬编门禁解锁 + 真机长视频；
- `Plan/VidUtils_等质量标定_L40_AV1专项执行方案.md` —— **仅 `av1_nvenc`**（T4 无 AV1 NVENC，
  `--expect-av1` 会 exit 2）+ AV1 `-qp` 尺度（`_QP_SCALE=3`，**勿改回 4**）回归 + AV1 门禁。

**两条不可忘**：① **T4 与 L40 不可互替**（NVENC 代际不同，h264/hevc 以 T4 为准）；
② `(lo,hi)` 是 **CQ 轴量程**（av1_nvenc 0~63），**不能**拿它夹 `-qp`（`_QP_LIMITS` 才是 QP 轴）。

## 追加（2026-10-04）：CR-2 —— h264/hevc NVENC 默认 rc 统一到 vbr_hq

**事实**（跨仓契约 CR-2「路线 B」）：为让两仓共享的 `QUALITY_MAP` NVENC 行在同一率失真点标定，
VU 把 **NVENC 的默认 rc 由 `auto`(=不下发) 改为显式下发**（与 VE 一致）：
**h264/hevc → `-rc vbr_hq`**；**av1 → `-rc vbr`**（VE 的 harness 与生产 av1 降级路径都显式
`-rc:v vbr`，VU 现在同样显式下发，连「不下发 vs 显式」这处**形式差异**也消掉）。

- 落地：两脚本各加 `_NVENC_DEFAULT_RC = {'h264_nvenc': 'vbr_hq', 'hevc_nvenc': 'vbr_hq',
  'av1_nvenc': 'vbr'}`，
  `apply_rc_control_args()` 在 `rc_mode == 'auto'` 且编码器在表中时下发 `-rc vbr_hq`；
  显式 `--rc-mode` 仍原样下发；av1/软编不受影响。⚠ 用**裸 `-rc`**（不带 `:v`）：与既有
  `-rc <mode>` 一致，且 `dump_enc_options.sh` 的 token 正则会丢掉带冒号的形式 ⇒ 裸写法才被回归门覆盖。
- ⚠ **`--cq 0` 的 constqp 改写要联动**：hwaccel 在 auto 下把 `-cq 0` 改写为
  `-rc constqp -qp 0 -b:v 0`，此时必须**从 rc_args 里摘掉** auto 默认下发的 `-rc vbr_hq`
  （以及 `-rc-lookahead`），否则命令里出现两条 `-rc`。cpu_v2 不改写（只告警）→ 其 cq0 仍带 vbr_hq。
- **VE 侧为何不改生产**：VE h264/hevc 走 ctypes 直连 SDK，`nvenc_sdk` 不支持 `vbr`（会静默当 CONSTQP）
  ⇒ 只能「VE 保持 vbr_hq、VU 向 VE 靠拢」。
- 判据/基线同步：`verify/verify_rc_lookahead.py` ①/⑩A、`verify/verify_borrow_enhancement.py` ①、
  `probe/t4_acceptance.py` A4/A7/A8、`test/baseline/enc_before.txt` 第 12 行；harness `BASE_LOCK`。
  ⚠ **仓内探针也要跟**：`probe/verify_nvenc_quality_gpu.py::encode_nvenc(mode='cq')` 原来只发
  `-cq:v N -b:v 0`（跑在 preset 默认 VBR，≠ 生产的 vbr_hq）⇒ 已补 `_cq_rc()`（h264/hevc=vbr_hq、
  av1=vbr）；否则验收探针会在**与生产不同的率失真点**上判 PASS/FAIL。
