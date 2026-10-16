# VidUtils 质量参数统一优化方案 v3 —— `--quality-tier` 与「标定指纹制」

- 日期：2026-10-15
- 前作：`Plan/VidUtils_NVENC调优轴默认值优化方案_v2_20261013.md`（**本版取代它**，v2 的 §12 三项待拍板已在本版拍定）
- 适用脚本：`vidcrop_hwaccel.py`、`vidcrop_cpu_v2.py`、`convert_sdr_to_hdr.py`（孪生约定：常量、取值表、报错首行逐字一致；有意的不对称单列 §8）
- 状态：**实施中**（2026-10-15 批准）。进度台账见 §12.7；剩余待确认项见 §12.6。
- 配套文档：`Plan/VidUtils_quality-tier全流程实测方案_v1_20261015.md`（实测与补标定）
- 复测工具：`Accessory/probe/bench_preset_ab.py`（preset A/B 耗时专测，自动检测 AVX2、带负载守卫）
- 最新输入：§13 `preset_matrix.xlsx` 评估与修正矩阵（2026-10-16，Q11 据此拍板为「区分」）

---

## 0. 为什么有 v3：相对 v2 的 9 项实质修正

v2 是在只审视 tune/multipass 两条轴时写出的，本轮把 `-rate-mode`/`-lookahead`/`-crf,cq,qp`/`-ref`/`-preset`/`-tune`/`-multipass`/`-aq` **八条轴放在一起审视**，发现 8 处事实错误或设计缺陷：

| # | v2 的说法 | 本轮查明的事实 | 后果 |
|---|---|---|---|
| 1 | 「cpu_v2 无探测」⇒ 不派生 uhq | **三个脚本都有** `_query_ffmpeg_range()` + `_FFMPEG_RANGE_CACHE`（hwaccel L1420 / cpu_v2 L1050 / sdr L1212），能跑 `ffmpeg -h encoder=X` 并带缓存 | 理由不成立 ⇒ 改用「有无兜底重试」论证；且探测可被复用（§4.3） |
| 2 | 「cpu_v2 无\-声也好理由」——略 | cpu_v2 真实缺口是**无 fallback ladder**（L867/L1287/L1495 明写没有 `--fallback-policy`） | 结论保留，论证换新 |
| 3 | `-tune` 「只改 tune、不改质量轴取值，既有断言应全绿」 | **错**。QUALITY_MAP 是在「裸 `-rc vbr -cq N -b:v 0 -preset p4`、**不带 `-tune`**」下标定的（convert_crf.py L196/L204）；`uhq` 会自动启用 lookahead + temporal filter ⇒ **平移率失真曲线 ⇒ NVENC 三行失效** | 这是 v2 最严重的错误。`uhq` 必须走自己的标定行 |
| 4 | `--multipass` 默认规则「不动」 | 结论对，但理由该升级：它对标定的影响有**实测**（T4 ΔVMAF −0.006~−0.108，在噪声内）⇒ 是唯一「已知不破标定」的调优轴 | multipass 可以放心纳入 tier |
| 5 | 「choices 增加 `'auto'`」 | `choices=list(_NVENC_TUNE_VALUES)` 是**直接从取值表导出**的，往表里塞 `'auto'` 会污染该表（它还被 `_ignore` 文案、README、⑨ 组断言引用）。仓库已有正确先例：hwaccel L415 `_RC_MODES_WITH_BITRATE = ('auto',) + tuple(...)` | 必须新建独立 choices 常量 |
| 6 | 概览「三脚本同位置同措辞（孪生）」 | 实际**三向不对称**：sdr L4337 已有 `AQ`/`tune=`/`multipass=`；hwaccel L6149 / cpu_v2 L4831 只有 rc-mode/码率/lookahead | 收敛方向应是 hwaccel/cpu_v2 向 sdr 看齐，且须论证hwaccel L6147 的不变式 |
| 7 | 未识别 baseline 风险点 | `Accessory/test/baseline/enc_before.txt` **L12** 是全部基线里唯一活的 NVENC 格（`cpu_v2 hevc_nvenc -cq 20 -b:v 0 -rc vbr -preset p4`）；hwaccel 那格已降级成 libx265 ⇒ hwaccel 派生 uhq **不影响基线** | 强化「cpu_v2 不派生」的决策依据 |
| 8 | `-aq` 在三个脚本落点一致 | 不一致：sdr 走 `apply_rc_control_args(aq=)`（L1616-1623，**签名里有 aq**）；hwaccel 在策略层单独处理（L4467-4471，**签名里没有 aq**）；cpu_v2 根本没有该参数 | tier 的 aq 项需要三个不同插点 |
| 9 | `--archive` 只管 tune/multipass/aq，**质量值外置**，「不得覆盖显式关闭」 | 质量数值与档位分离会产生**自相矛盾的档位**：`--archive` + `--crf 50` 名义上是存档母版、实际是低质量，比不加档位更糟 | **质量数值必须进档**（质量锚点 + 质量窗口），见 §4.4。这是本轮最大的结构性改动 |

外加顺手发现的 3 处既有文档 bug：README L335 把 `--nvenc-aq` 列进 cpu_v2 参数表（代码里 0 命中）、L394 写「语义同 `vidcrop_hwaccel.py`」（hwaccel 表里自称同 hwaccel，复制残留）、L88-93 功能矩阵缺 `--nvenc-tune`/`--nvenc-multipass` 行。

---

## 1. 一句话结论

把散落在八条 CLI 轴（§2.3）与质量数值上的「我想快 / 我想好」的意图，收敛到一个**八档开关 `--quality-tier {lossless,near-lossless,archive,quality,balanced,preview,draft,test}`**（**默认 `quality`**，锚点 `--crf-ref 20`），并且**把质量数值本身也收进档位**：每档带一个**质量锚点**（统一基准轴 libx264 CRF 上的 `ref_crf`）和一个**质量窗口**（`ref_window`）。八档窗口在整数轴 `[0,51]` 上**完整覆盖、无缝、互不重叠** ⇒ 任意质量值必然属于且只属于一档。档位权威不靠互斥去压住单参数，而靠两道自动判定：

1. **质量窗口**——用户给的任何质量值都先归一到统一基准轴再与窗口比对，越界即钳回窗口并告知（`--strict-quality` 下改 exit 2）。这就是「`archive` + `--crf 50` 还算不算 archive」的答案：不算，会被钳到 archive 的窗口上界。
2. **标定指纹**——最终生效的编码策略组合有没有被标定过；偏离 tier 标定点即失去 `--crf-ref`/`--cq-ref` 的换算保证并被告知（§2.4）。

⇒ tier 与 `--codec` **正交**：codec 是 tier 的**输入**，tier 的每一项内涵（preset / lookahead / tune / multipass / aq / 质量锚点落到该 codec 的原生取值）都按 codec 实例化。

---

## 2. 参数交互全景（本轮的核心分析）

### 2.1 质量入口有三类，不是一类

| 入口 | 位置 | 语义 | 是否过等效表 |
|---|---|---|---|
| **字面量** `--crf` / `--cq` / `--qp` | hwaccel L3394 起的 `_resolve_quality_params` | 按**目标编码器原生刻度**直发 | **不过表**（convert_crf.py L323-324 明文：「用户显式给出的同族参数一律原样下发，不换算」） |
| **统一基准轴** `--crf-ref` / `--cq-ref` | L3545-3551 `ref_x264` | 先归一到 **libx264 CRF 轴**再 `from_x264_crf(codec, N)` | **过表**（`--cq-ref` 多一步 `to_x264_crf('h264_nvenc', N)`） |
| **缺省** | `_default_quality_for()` L1571-1579 | `from_x264_crf(codec, DEFAULT_REF=21)` | 过表 |

⇒ **tier 影响的不是"给不给值"，而是"同一个值在该组合下意味着什么画质"**。字面量入口虽然不过表，但用户的 CRF/CQ 直觉也是在 medium/p4 上建立的。

> **本方案新增第四类入口**：`--quality-tier` 提供**质量锚点 + 质量窗口**（§4.4）。它不新增一个 CLI 质量参数，而是给上面三类入口套一层「档位一致性」约束。

### 2.2 换算口径：两张三级，且各自带档位指纹

```
convert_crf.py
  SIZE_MAP    L18   等体积（文件名 log-体积插值）
  QUALITY_MAP L116  等质量（目标编码器「参数→VMAF」曲线上取等 VMAF 点后最小二乘）★默认
  _ACTIVE_MAP L230  = {**SIZE_MAP, **QUALITY_MAP}，未覆盖编码器回退 SIZE_MAP
hwaccel L1512 / cpu_v2 L1142 / sdr L484
  _QP_AFFINE_QUALITY  另一张：constqp 轴用（跨仓契约 CR-4，与 VE 逐条相等）
```

三者形态一致：`Dict[codec, (a, b, lo, hi)]`，`value = a·x264_crf + b` 夹 `[lo,hi]`，**基准轴恒为 libx264 CRF**。

**现成的"表随组合变"范式**（直接照抄，不要新发明）：`convert_crf.py` L224-226

```python
_EQQUAL_SPEED_OVERRIDE = {
    'librav1e': (7.9173, -106.6317, 0, 255),   # 仅当 _RAV1E_SPEED > 0 时生效
}
# 空 dict ⇒ 未标定，rav1e 回退 QUALITY_MAP 的 native 档。
```

### 2.3 八条轴对率失真的影响分级（决定谁破标定）

这是 tier 设计的全部依据。判定标准：该轴是否让 QUALITY_MAP 那 9 行的标定前提失效。

| 轴 | 现状位置 | 是否平移率失真曲线 | 有无实测 | tier 能否改 |
|---|---|---|---|---|
| **`-preset`** | `strategy_preset()` L1365-1388（hwaccel 惰性）/ `default_preset_for()` L4408-4413（cpu_v2、sdr 急切） | **是，最强** | calibrate_equal_quality.py L94-107 `BASE_LOCK` 明文「换挡会整体平移率失真曲线 ⇒ 等效点漂移」；hwaccel L1356-1357「svtav1 的等效关系是在 `-preset 8` 下标定的」 | **能，但必须按档重标全部受影响的行** |
| **`-lookahead`** | `apply_rc_control_args` hwaccel L3700-3716 / cpu_v2 L1559-1575 / sdr L1600-1611 | **是** | 标定 batch 未下发 lookahead（基准 = 不下发） | 同上 |
| **`-tune`** | hwaccel L3734-3742 | **是**（`uhq` 自带 lookahead + temporal filter） | 只有「h264 传 uhq → rc=234」的负面实测；hevc/av1 的 uhq 收益**零实测** | 同上（**已拍板：阶段一不做 auto→uhq**） |
| **`-aq`** | hwaccel L4467-4471 / sdr L1616-1623 | **是**（改量化分配） | **零实测** | 同上 |
| **`-multipass`** | hwaccel L3720-3732 | **实测可忽略** | T4 固定 `-cq` A/B：fullres ΔVMAF −0.006~−0.108，远低于 LOO 门禁 2.77~3.23 | ✅ **可自由纳入 tier，不触发重标** ← 唯一已知安全的调优轴 |
| **`-rc`（`--rc-mode`）** | hwaccel L3669-3698 | 换 regime | constqp 与 vbr 已走**不同表**（`_QP_AFFINE_QUALITY`，CR-4）⇒ 双轨早已存在 | **tier 不碰**（理由 §2.5） |
| **`--bitrate`** | 全程 | 改约束点（`-b:v` 作上限） | 现状语义已定 | **tier 不碰** |
| **`-ref`(s) DPB 参考帧数** | **本仓库未下发** | 是（改时域预测） | 无 | **不纳入 tier**（见 §3） |

### 2.4 标定指纹的定义

凡 tier 要交付一个组合，必须同时登记它的**标定指纹**：

```python
CALIB_FINGERPRINT = {
    'balanced': {
        'preset':  {'libx264': 'medium', 'libx265': 'medium', 'libsvtav1': '8',
                    '*_nvenc': 'p4', ...},
        'lookahead': None,          # 不下发
        'tune': None, 'multipass': 'auto', 'aq': False,
        'rc': 'vbr', 'mode': 'quality',
        'source': None,             # None ⇒ 直接用 QUALITY_MAP 本体
        'evidence': 'T4/L40 2026-10-04，17 素材，n_subsample=1',
    },
    'quality': {'source': '_EQQUAL_TIER_OVERRIDE["quality"]', 'calibrated': False, ...},
    ...
}
_EQQUAL_TIER_OVERRIDE: Dict[str, Dict[str, Tuple[float, float, int, int]]] = {}   # 缺 = 未标定
```

运行时判定：把**最终生效的组合**（tier 基底 + 显式覆盖后的结果）算成实际指纹，与本 tier 的登记指纹逐字段比对。

- 一致 ⇒ `--crf-ref N` / `--cq-ref N` 的换算有标定保证，静默。
- 不一致 ⇒ 概览 + `--dry-run` 打**固定文案**（三脚本逐字一致）：
  `⚠ 当前组合偏离 --quality-tier <t> 的标定点（<差异项>）；--crf-ref/--cq-ref 的跨编码器等效值无标定保证`
- `--strict-calib`（新，默认关）⇒ 不一致时 `exit 2`，首行与上面同一句。

**这就是 tier 的"优先"落点**：不是靠互斥去禁止偏离，而是让偏离自动付出「失去标定保证」的代价。

### 2.5 硬约束（默认档改为 `quality` 之后的重新表述）

默认档定为 `quality` 之后，原先那两条"不动"要重新划界——**能守住的守住，守不住的明确登记为跨仓契约变更**。

**仍然写死（不因默认档改变而动摇）：**

1. **只有 `libx264` 在所有档恒 `medium`。** 基准轴就是 libx264 CRF（`libx264: (1.0, 0.0, 0, 51)` 是单位元）。它一动，锚点本身漂移 ⇒ 9 行全部重标且跨档不可比。
   ⇒ **这不是"所有 x26x 都不能动"**：`libx265` 不是基准轴，可以随档变化（`balanced medium` / `quality slow` / `archive slower` / `preview faster` / `draft,test veryfast`）。**更正上一版**：此前我把它写成"preview 的 x26x 只能写 medium"，把 libx265 也一并误伤了。
2. **`NVENC_TO_X264_PRESET` / `X264_TO_NVENC_PRESET` / `X264_TO_SVTAV1_PRESET` / `NVENC_TO_SVTAV1_PRESET` 的取值不动**（CR-1 的**映射表**部分不受影响）。
3. **`_NVENC_DEFAULT_RC`（`-rc vbr`）不动**（CR-2）。
4. **既有 `_v == 0` 无损守卫（L3455-3456 / L3481-3482）不动** —— `lossless` 档就是复用它，不新增一套无损路径（详见 §4.4）。

**守不住、必须登记为契约变更（已按你的选择接受）：**

| 变更 | 原状 | 变更后 | 要对谁说 |
|---|---|---|---|
| 默认质量值 | `DEFAULT_REF = 21`（L388） | 默认档 `quality` 的锚点 **`20`** | README 默认列、`memory/project_rate_control_params.md` L334-356 |
| 默认 preset | `p4` / `medium` / `8` | **`p6`** / libx264 `medium`（不变）/ **`slow`** / **`6`** | **跨仓契约 CR-1 的「默认档」条款**（2026-10-04 与 VE 约定 p4↔medium↔8）⇒ **需与 VE 同步重议** |
| 默认 lookahead / tune / multipass / aq | 全不下发 | `250` / `uhq` / `fullres` / on | 同上 + README |
| `memory` BASE_LOCK「默认路径永远裸 `-rc vbr -cq N -b:v 0 -preset p4`」 | 成立 | **失效**，须改写为「默认路径 = `--quality-tier quality` 的基底」 | `memory/project_rate_control_params.md`、`MEMORY.md` L203 |
| 默认路径是否标定 | 标定（`QUALITY_MAP` 本体） | **未标定**（`quality` 档还没有自己的 `_EQQUAL_TIER_OVERRIDE`）⇒ 默认路径会一直挂着告警，直到 Part B 出数据 | Part A §2.4 / Part B §B4 |

> ⚠ 最后两行是这次选择的**真实代价**：默认路径会变成一个带着「未标定」告警的路径。这不是 bug 而是诚实——但必须让 README 与 help 提前说明，否则用户第一眼就会以为工具坏了。

---

## 3. 术语对应（已澄清，不再单列）

你最初列的 `-rate-mode` / `-ref` 指的就是本仓的 `--rc-mode` 与 `--crf-ref` / `--cq-ref`，两者都是本方案里的既有成员，无需再澄清：

| 你的写法 | 本仓写法 | 在本方案里的位置 |
|---|---|---|
| `-rate-mode` | `--rc-mode`（NVENC `-rc`：`constqp`/`vbr`/`cbr`，hwaccel L407 `_RC_MODES`） | §2.3「不碰 rc」+ §4.2「不进 tier」：constqp 与 vbr 已是两套独立的表 + 两套独立的互斥规则（L5852-5867），再叠一档只会炸复杂度 |
| `-ref` | `--crf-ref` / `--cq-ref`（统一基准轴 CLI 入口，L3545-3551）+ `DEFAULT_REF`（L388 质量基准常数） | §2.1「三类质量入口」+ §4.4：它们是 tier 的**服务对象**（换算保证的来源），不是 tier 的成员轴；`DEFAULT_REF` 随默认档迁移为 20（§2.5） |

> 附注：本仓确实**没有** ffmpeg 的 `-refs`（DPB 参考帧数）下发。若以后要引入，它是第九条轴且必然破标定 ⇒ 建议走 `--extra-args`，不进 tier。

---

## 4. tier 设计

### 4.1 八档定义

**八档**，`--quality-tier` 默认 **`quality`**（ = 你的「默认 ref 20」）。锚点与窗口**在整数轴 `[0,51]` 上完整覆盖、连续无缝、互不重叠**：

```
[0,0] [1,12] [13,18] [19,22] [23,26] [27,32] [33,40] [41,51]
 0      6      16      20      24      28      36      45     ← 锚点
```

> ✅ **完整覆盖是个好性质**：任意 `-crf/-cq` 整数值**必然属于且只属于一档** ⇒ 「该建议改用哪个档」的答案永远存在且唯一，`min/max` 钳制后不需要处理"无归属"分支，判定代码是纯查表。
> ⚠ 也正因如此，**不存在"不指定档位"的第三种状态**了 —— 这就是 §12.5 那条选择的直接后果。

| tier | 中文定位 | preset（NVENC / **libx264** / libx265 / svtav1） | lookahead | tune | multipass | aq | **`ref_crf`** | **`ref_window`** | 标定状态 |
|---|---|---|---|---|---|---|---|---|---|
| `lossless` | 无损母版 | `p7` / `medium` / `slower` / `4` | `250` | **`lossless`** | `fullres` | 见注 | **`0`** | **[0, 0]** | ❌ 未标定 |
| `near-lossless` | 近无损母版 | `p7` / `medium` / `slower` / `4` | `250` | `uhq` | `fullres` | on | **`6`** | **[1, 12]** | ❌ 未标定 |
| `archive` | 存档母版 | `p7` / `medium` / `slower` / `4` | `250` | `uhq` | `fullres` | on | **`16`** | **[13, 18]** | ❌ 未标定 |
| **`quality`（默认）** | 高质量交付（分发母版） | **`p6`** / `medium` / **`slow`** / **`6`** | **`250`** | **`uhq`** | **`fullres`** | **on** | **`20`** | **[19, 22]** | ❌ **未标定** |
| `balanced` | 均衡（**= 今天的行为，但不再是默认**） | `p4` / `medium` / `medium` / `8` | 不下发 | 不下发 | `auto` | off | `24` | **[23, 26]** | ✅ **已标定**（=今天的 QUALITY_MAP 本体） |
| `preview` | 预览 / 审片 | `p2` / `medium` / `faster` / `12` | 不下发 | `ll` | `disabled` | off | `28` | **[27, 32]** | ❌ 未标定 |
| `draft` | 草稿 / 粗剪 | `p1` / `medium` / `veryfast` / `13` | 不下发 | `ll` | `disabled` | off | `36` | **[33, 40]** | ❌ 未标定 |
| `test` | 冒烟测试（只要能解码就行） | `p1` / `medium` / `veryfast` / `13` | 不下发 | `ll` | `disabled` | off | `45` | **[41, 51]** | ❌ 未标定 |

注：

- **`lossless` 档复用既有 `_v == 0` 无损守卫**（L3455-3456 / L3481-3482）⇒ 自动走 `-rc constqp -qp 0 -b:v 0`，**不需要 tier 去碰 `--rc-mode`**（守住 §2.5-3）。它的 `preset` 影响的是无损产物的**体积**而非画质；NVENC 上 `-tune lossless` 是原生取值。
- **`libx264` 列恒 `medium`**（§2.5-1，基准轴）⇒ 对 libx264 而言八档**只有质量值不同**，策略完全相同。`libx265` 不是基准轴，可随档变化。
- `tune=ll` 与 `-tune lossless` 都是 NVENC 私有语义，**只有 NVENC 才下发**；降级到 CPU 策略时静默（不告警——否则每条降级策略刷一条）。
- `uhq` 必须过 §4.3 的探测，且**只对 `hevc_nvenc` / `av1_nvenc`** 下发；`h264_nvenc` 落到 `hq`（= 不下发）。
- **`draft` 与 `test` 目前策略完全相同、只差锚点** —— 是否需要给它们策略层区分（如 `test` 强制 `--threads 1` 或最短 preset）见 §12.6。
- **`quality` 与 `archive` 的分化 = D1 + D2 同时用**（Q3 已定）：既拉锚点（20 vs 16），也拉 preset（`p6`/`slow`/`6` vs `p7`/`slower`/`4`）。svtav1 的 `6`/`4` 因此不再是"占位"，而是**已承诺档位取值**，必须在 Part B §B5 的 D2 bench 里验证（见 §12.2 Q3 行）。

### 4.2 哪些轴进 tier、哪些不进

| **进 tier**（tier 提供基底 / 约束） | **不进 tier**（保持今天语义） |
|---|---|
| `--preset`（按 codec 族给默认） | `--codec` —— **正交**：codec 是 tier 的**输入**而非成员。tier 的每一项内涵（preset / lookahead / tune / multipass / aq / 质量锚点落到该 codec 的原生取值）都由 codec 实例化；同一档在不同 codec 上内涵不同，这是设计而不是妥协 |
| `--lookahead` | `--rc-mode`（constqp 与 vbr 已是两套表 + 两套互斥，别碰） |
| `--nvenc-tune` / `--nvenc-multipass` / `--nvenc-aq`（cpu_v2 不适用，§8-1） | `--bitrate`（改的是码率约束点，不是档位语义） |
| **质量数值**：`--crf` / `--cq` / `--qp` / `--crf-ref` / `--cq-ref` —— 以「统一基准轴锚点 + 窗口」的形式进 tier（§4.4） | `--pix-fmt` / `--hdr` / `--decode` / **`--threads`**（正交；`--threads` 只管资源占用，与画质档位无关） |
| | `-refs`（本仓库不存在，§3） |

⇒ **tier = 编码策略基底 × 质量锚点/窗口 × 标定指纹；`--codec` 是它的输入。**
⇒ **质量数值必须进 tier**，不能外置。把 `--crf/-cq/-qp` 留在 tier 之外会产生**自相矛盾的档位**：`--quality-tier archive --crf 50` 名义上是存档母版、实际是最低质量，比不加档位还糟——用户会以为拿到了 archive 的编码策略（p7 / uhq / aq / fullres），却配了一个把策略红利全部抵消掉的质量点。窗口就是用来消除这类组合的（§4.4）。

### 4.3 `-tune` 探测（取代硬编码猜 uhq）

三脚本都已有 `_query_ffmpeg_range()`（`ffmpeg -h encoder=X` + 缓存 + `timeout=5` + `except Exception: pass`）。**孪生新增**一个枚举版：

```python
def _query_ffmpeg_enum(encoder: str, option: str) -> Optional[FrozenSet[str]]:
    """解析 `ffmpeg -h encoder=X` 里某个选项的枚举取值（如 `-tune` 的 hq/ll/ull/lossless/uhq）。
    失败/解析不到返回 None ⇒ 调用方回退静态表。"""
```

解析目标形如（`ffmpeg -h encoder=hevc_nvenc` 的输出）：
```
  -tune                <int>        ... .D.V..... hq ll ull lossless uhq
```

`auto` / tier 派生 `uhq` 的判定顺序：
1. `codec ∉ NVENC_CODECS` → 不下发，**静默**（不告警）
2. `codec ∉ _NVENC_UHQ_CODECS`（硬编码 hevc/av1）→ 不下发，静默
3. 探测成功 且 `'uhq' ∉ 探测集合` → 不下发，告警一次「本机 ffmpeg 的 `<codec>` 未声明 `-tune uhq`」
4. 探测失败（无 ffmpeg / 超时 / 解析不到）→ **按硬编码表派生**（回退，零风险）
5. 否则 → `-tune uhq`

**边界必须写进注释与 README**：探测只能证明「ffmpeg 编译期声明了这个取值」，**不能**证明「驱动运行时接受」（rc=234 类）。所以：
- hwaccel 的 `_NVENC_PRESET_RETRY` 旁加 `_tune_tries = [派生值, None]`（**仅派生值启用，显式值不降**）
- sdr 的 `_run_job_with_fallback` ladder（L4699-4776，构建在 L4713-4722）插一级「去 tune 重试」
- cpu_v2 不需要（不派生 uhq）

### 4.4 质量锚点与质量窗口 —— 「内部协调配档」

这是本轮相对 v2 的**结构性改动**：质量数值不再外置于 tier，而是 tier 的一等成员。

```python
def resolve_quality_for_tier(codec, tier, *, crf=None, cq=None, qp=None,
                             crf_ref=None, cq_ref=None, strict=False):
    """把「用户给的质量值」与「tier 的锚点/窗口」协调成一个最终落值。

    全部换算都在**统一基准轴（libx264 CRF）**上做，最后一次性 from_x264_crf 到
    目标编码器的原生刻度 —— 即「用 --crf-ref/--cq-ref 的口径 + 等质量表内部配档」。
    """
```

**四步流程**

1. **归一到基准轴** `ref_x264`：

   | 入口 | 归一方式 | 现状依据 |
   |---|---|---|
   | `--crf-ref N` | `N` | L3547 |
   | `--cq-ref N` | `to_x264_crf('h264_nvenc', N)` | L3550 |
   | `--crf` / `--cq`（字面量） | `to_x264_crf(codec, N)` | `convert_crf.py:296` |
   | `--qp N`（constqp） | `_QP_AFFINE_QUALITY` 的**反函数** `(N − b)/a` | 现只有正向 `to_constqp_qp`（L1164），反函数需孪生新增（纯代数） |
   | 都没给 | tier 的 `ref_crf`；默认档是 `quality` ⇒ **`20`**（等价于 `--crf-ref 20`） | `DEFAULT_REF`(L388) 语义迁移为「默认档锚点」，见 §2.5 |

2. **无损哨兵短路**：值为 `0` ⇒ **永不钳制、不换算**，交既有 `_v == 0` 守卫处理（L3455-3456 / L3481-3482 ⇒ `-rc constqp -qp 0 -b:v 0`）。`--quality-tier lossless`（锚点 0）正是走这条路，所以 **`lossless` 档不需要新代码**，也不需要 tier 去碰 `--rc-mode`。
3. **窗口钳制**：先 `ref_int = round(ref)`（`int (min(max(ref_int, lo), hi))`）。**四个闭区间严格不重叠** ⇒ 任意整数**最多属于一档**，于是：
   - 在窗内 ⇒ 原值通过，无告警；
   - 越界 ⇒ 钳到该侧的窗边界，打**固定告警**（三脚本逐字一致）；`--strict-quality` ⇒ 改 `exit 2`，首行同句；
   - 告警的第③段要给出「该用哪个档」——直接线性扫 `TIER_PROFILES` 找窗内含 `ref_int` 的那档，**因为区间不重叠，答案是唯一的**；都不含（如 `ref > 32`）⇒ 提示不指定档位。
   又因为八档完整覆盖 `[0,51]`，「都不包含」的分支**不存在**（上一版针对 `ref > 32` 的兜底可以删掉）。相比「允许重叠」，这里不需要任何仲裁规则。
4. **落值**：`from_x264_crf(codec, ref, table=<tier 的活动表>)` ⇒ 该 codec 的原生取值，再交给 `_resolve_quality_params` 原有链路下发。

**四个必须写进 README 与 verify 的样例**

| 情形 | 归一到基准轴 | 窗口判定 | 结果 |
|---|---|---|---|
| `--codec libx265 --quality-tier archive --crf 50` | `to_x264_crf('libx265', 50) ≈ 47.7` → `48` | 越出 `[13, 18]` ⇒ 钳到 `18` | 下发 `-crf 17`（`from_x264_crf ≈ 17.45`），告警见下 |
| `--codec libx265 --quality-tier quality --crf-ref 26` | `26` | 越出 `[19, 22]` ⇒ 钳到 `22`（离 26 最近的窗边界） | 下发 `-crf 22`；告警建议「26 属于 `balanced` 的窗口，请改用 `--quality-tier balanced`」 |
| `--codec libx265 --quality-tier preview --crf-ref 30` | `30` | `30 ∈ [27, 32]` ⇒ 通过 | 下发 `-crf 31`，**不告警**（负向用例：窗内的显式值不受打扰） |
| `--codec libx265 --quality-tier archive --crf-ref 16` | `16` | `16 ∈ [13, 18]` ⇒ 通过 | 下发 `-crf 15`，不告警（正好是该档锚点） |

`archive + --crf 50` 的告警文案（三脚本逐字一致）：
`⚠ --quality-tier archive 的质量窗口为 CRF 13~18；--crf 50（基准轴 ≈47.7）已钳到 18（libx265 -crf 17）。CRF 48 落在 test 档（41~51），请改用 --quality-tier test`

> ⚠ **基线含义已改（§12.5 已拍板走「默认档 = quality」）**：不再存在"不指定档位"的第三种状态 ⇒ 「默认 ≡ 今天的行为」这条不成立。**补偿做法**：把现有四份 baseline 整体转为 **`--quality-tier balanced` 的黄金文件**（旧 inventory 全部显式带上该参数），再另建一份新 default 基线。详见 §7。

**⚠ 与既有契约的冲突（必须显式登记，不能悄悄做）**

`convert_crf.py` L323-324 现在承诺：「用户显式给出的同族参数一律原样下发，不换算」。
tier ≠ `balanced` 时这条**被 tier 接管**——用户显式给了值，但该值会被钳制并按该档的换算表重新配档。
`balanced` 档**完全不变** ⇒ 不破任何既有断言、不破任何 baseline。
这个「契约接管」要写进三份 help、README、`memory/project_rate_control_params.md`，否则下一轮审查会当成 bug。

**概览展示**：`质量: crf-ref 18（钳自 --crf 50）`；三脚本同措辞（进 `verify_overview_lockstep.py`）。

---

## 5. tier 与单参数的关系：**不是互斥，是两道自动判定**

### 5.1 三层优先级（取代「互斥」）

```
① 显式单参数          最高：用户点名了某个轴，意图比档位更具体
② tier 基底           中间：tier 给该轴的默认值（含质量锚点）
③ 全局默认            最低：即今天的行为
```

为什么不用互斥"tier 优先"硬压：
1. `--preset` / `--lookahead` / 三个 nvenc 轴的 argparse default 都是 `None`，天然支持这个三态，不需要改语义；
2. 现有互斥面已经够复杂（ref ⊕ 字面量、crf-ref ⊕ cq-ref、constqp ⊕ ref），再叠一层会指数膨胀；
3. **真正该管的不是"谁改了值"，而是"改完之后还成不成立"**——于是交给两道判定：
   - **质量窗口**（§4.4）：值本身是否与档位语义自洽（防「archive + crf 50」）；
   - **标定指纹**（§2.4）：编码策略组合是否还有换算保证（防「改了 preset 还拿 balanced 的表换算」）。

⇒ 「tier 优先」的落点是：**tier 给出的组合是唯一既自洽又有标定保证的组合**；单参数覆盖 = 自愿退出这两项保证，且两处都会被明确告知。这既尊重了 tier 的权威，又不制造脆弱的互斥。

### 5.2 现有互斥：一条都不动

| 规则 | 位置 | 行为 |
|---|---|---|
| `--crf-ref` ⊕ `--cq-ref` | hwaccel L5695-5700 | exit 2 |
| ref ⊕ {`--crf`, `--cq`} | L5701-5705 | exit 2 |
| constqp 下 `--qp` ⊕ ref | L5852-5867 | exit 2 |
| `--nvenc-tune uhq` + `h264_nvenc` | hwaccel L5843-5849（`return 2`）/ cpu_v2 L4560-4564（`raise ValueError` → L4712-4714 转 `return 2`） | exit 2 |
| 同上，**sdr 缺失** | sdr 全文无此检查（只有策略内 `_ignore` 告警 L1640-1645） | ← 本期补，且与另两个同首行 |

> 附带修一处现成不一致：hwaccel 用 `CODEC_ALIASES` 归一化（L5845）而 cpu_v2 直接比字符串（L4561）⇒ 别名（如 `h264nvenc`）在两脚本行为不同。统一到 hwaccel 的归一化版本，新补的 sdr 也用它。

### 5.3 两个 strict 开关，并随 `--fallback-policy strict` 一并生效

| 开关 | 管哪道判定 | 默认 | 默认时的行为 |
|---|---|---|---|
| `--strict-quality` | 质量值越出该档窗口（§4.4 步骤 3） | 关 | 钳制回窗内 + 告警 |
| `--strict-calib` | 编码策略组合偏离该档标定点（§2.4） | 关 | 回退 `balanced` 表 + 告警 |

- 触发时 `exit 2`，首行与对应告警**逐字相同**（hwaccel / sdr 一致）。可以同时给两个。
- **并入 `--fallback-policy strict`**：该值隐含同时打开上面两个开关。这样既保留了「两个开关」的细粒度（可以只要 `--strict-quality`、不要 `--strict-calib`），又不必让 strict 用户再记两个新名字。
  - **零回归保证**：两个新开关默认关；而 `--fallback-policy strict` 的既有用法默认走 `balanced` 档 ⇒ 窗口全轴 + 已标定 ⇒ 两道判定都不触发 ⇒ 行为与今天逐字相同。
- **cpu_v2 例外**：cpu_v2 没有 `--fallback-policy`（L867 / L1287 / L1495），也不提供这两个开关——cpu_v2 是 hwaccel 的 CPU 简板，不必强行对称 ⇒ cpu_v2 里两道判定**恒为告警**，永远不升级为 `exit 2`。登记为第 5 项有意不对称（§8-5）。

---

## 6. 逐文件实施清单

### 6.1 阶段零：先修既有 bug（与 tier 正交，零字节影响，建议单发 commit）

| # | 文件:行 | 内容 |
|---|---|---|
| Z1 | `vidcrop_hwaccel.py` L5134 附近 | `build_ffmpeg_cmd` 是**裸调**（本循环内唯一有 try 的是 L5078 的 `build_video_filter`），`main()` 的 try（L6238）只捕 `KeyboardInterrupt`（L6339）⇒ `--fallback-policy strict` 下 `_ignore` 抛的 ValueError 会直接 traceback。补 `try/except ValueError` → 可读报错 + `return 2`。**sdr 已有**（L4726-4733），cpu_v2 无 strict 分支故不适用 ⇒ 这是三向不对称，不是 hwaccel 独有设计 |
| Z2 | `convert_sdr_to_hdr.py` | 补 CLI 层 `--nvenc-tune uhq` + `h264_nvenc` 硬报错，与另两个同首行（§5.2） |
| Z3 | `README.md` L335 | cpu_v2 参数表里的 `--nvenc-aq` 行删除或移表（cpu_v2 代码该词 0 命中） |
| Z4 | `README.md` L394 | 「语义同 `vidcrop_hwaccel.py`」→ `vidcrop_cpu_v2.py`（hwaccel 表里自称同 hwaccel 的复制残留） |
| Z5 | `README.md` L88-93 | 功能矩阵补 `--nvenc-tune` / `--nvenc-multipass` 行（加 tier 后更必要） |

### 6.15 阶段零点五：本地 CPU 的 D2 预跑（**立刻开始，可与阶段一并行**）

你的 Q3 回答要求「保持 D1+D2，且立刻把 svtav1 / x265 的 D2 部分先在本地跑一轮实测」。这一小步**不依赖任何 tier 代码**（纯 ffmpeg A/B），所以它**不用等阶段一**，现在就能开跑：

| 组合 | A 侧 | B 侧 | 在哪跑 |
|---|---|---|---|
| x265 | `libx265 -preset slow` | `libx265 -preset slower` | 本机 |
| svtav1 | `libsvtav1 -preset 6` | `libsvtav1 -preset 4` | 本机 |

- 素材与口径**直接沿用 Part B §B2**（17 条 eqq 池、锚点 18/21/24/27/30、`n_subsample=1`、720p prep）⇒ 结果与后续三环可直接拼接，不用重跑。
- 指标：**等 VMAF 码率比** + **Δ耗时**（定义见 Part B §B3）。
- 产物先落 `Accessory/calib/eqq_evidence/q3_bench/cpu_early/`，回归后并入 Part B §B6 的正式目录。
- **它的唯一目的是给 §12.3 的 Q3=D1+D2 决策兜底**：若判定 D2 不划算，回退动作只是改 `TIER_PROFILES` 常量，不动任何代码（这是当初选「留在常量层」的原因）。

### 6.2 阶段一：**把编码全部做完**（Q6：先完成编码，标定后置）

标定点一律先空着由数据结构自带「未标定」，不在这一阶段做任何实测。

| 文件 | 改动 |
|---|---|
| `convert_crf.py` | 新增 `_EQQUAL_TIER_OVERRIDE`（照抄 `_EQQUAL_SPEED_OVERRIDE` 范式，L224-226）+ `set_quality_tier()` + `get_quality_map()` 按 tier 查、缺则回退 + 回调告知；新增 `CALIB_FINGERPRINT` 常量。**初值全空** ⇒ 非 balanced 自动落到"未标定回退"，不靠人记 |
| `vidcrop_hwaccel.py` | ① 新增 `--quality-tier`（`default='balanced'`，choices 用独立常量）+ `--strict-quality` / `--strict-calib`，并让 `--fallback-policy strict` 隐含打开后两者（§5.3）；② `TIER_PROFILES` 常量表（含 `ref_crf` / `ref_window`，§4.1）；③ **preset 已是惰性**（L5938-5940 注释：`args.preset` 保持 `None`）⇒ 只需把 `strategy_preset()`（L1365-1388）里的默认改为 tier 感知；④ lookahead 默认值插在 `apply_rc_control_args` 的 `if lookahead is not None`（L3700）**之前**；⑤ tune/multipass/aq 按 tier 基底；⑥ `_tune_tries`（L5103-5107 旁）；⑦ **质量锚点/窗口**：给 `_resolve_quality_params`（L3394 起）加 `tier: str = 'balanced'` 参数并在**函数顶部**做 §4.4 的四步流程——它是唯一同时知道 codec、tier 与三类质量入口的地方；**constqp 反函数也在本阶段做**（Q6），放在 `_QP_AFFINE_QUALITY`（L1512）旁边，并与正向 `to_constqp_qp` 做往返一致性断言；⑧ 概览 L6147-6157 补 `tier=` / `质量:` / `tune=` / `multipass=` / `AQ`（**向 sdr L4337 的措辞看齐**）+ 窗口告警 + 指纹告警 |
| `vidcrop_cpu_v2.py` | 同上，但：① **必须先在 `main()` 里捕获 `preset_explicit = args.preset is not None` 快照**（L4408-4413 是急切填充，填完就分不出显式与否）——这是 hwaccel 不需要、cpu_v2/sdr 必需的关键一步；② **不加 `--nvenc-aq`**；③ **auto/tier 不派生 `uhq`**（§8-2）；④ 无 `_tune_tries`；⑤ **不提供 `--strict-quality` / `--strict-calib`，也无 `--fallback-policy`**（§8-5）⇒ 两道判定恒为告警 |
| `convert_sdr_to_hdr.py` | 同上（同样需要 `preset_explicit` 快照，L3882-3884；另有 dataclass 默认 L2281 需一并处理）；ladder L4713-4722 插「去 tune 重试」一级 + 同步更新 docstring L4702-4708 的阶梯编号 |
| 三份 help / README / `memory/project_rate_control_params.md` | 写清：tier = 策略基底 × 质量锚点/窗口 × 标定指纹，**codec 是输入不是成员**（§4.2）；质量数值**在档内**（§4.4）以及「契约接管」那一条；`libx264` 恒 medium（§2.5）；非 balanced 未标定（§2.4）；cpu_v2 四条有意不对称（§8） |

**阶段一的额外任务（默认档改成 `quality` 之后新增）：**

- **baseline 迁移**：把现有四份 `Accessory/test/baseline/*.txt` 与 `dump_cmd_*.sh` 里的默认调用**全部显式加上 `--quality-tier balanced`** ⇒ 旧文件成为 `balanced` 档的黄金文件；再**另建一份 default（=`quality`）新基线**。这一步不改语义，只把"旧默认"钉成"balanced 档"（详见 §7）。
- **`DEFAULT_REF` 语义迁移**（§2.5）：常量保留为单一来源（L388），取值改为引用默认档锚点 `TIER_PROFILES[DEFAULT_TIER]['ref_crf']`（=20），不要两处写死。

**阶段一的完成判据**：
1. 迁移后的四道孪生门**逐 token 全绿**（旧字节作为 `balanced` 档被完整保留）；
2. 新建的 default 基线与 `--dry-run` 人工核对一致；
3. 八档均可 `--dry-run` 核对；§4.4 四个样例全部成立；
4. `lossless` 档确认落到 `-rc constqp -qp 0 -b:v 0`（复用既有守卫，无新增路径）。

> 阶段一之后 `_EQQUAL_TIER_OVERRIDE` 仍全空 ⇒ 非 balanced 档全部处于「未标定」告警状态。**这是预期状态，不是缺陷**——没有实测数据之前，宁可显式告知也不假装标定过。

### 6.3～6.6 后续阶段：先出方案，再按 **CPU → T4 → L40** 循序渐进实测补标定（Q6 / Q7）

严格按「软→硬」推进：每一环都复用上一环确认过的素材池、锚点与口径，只有**被测编码器 / 机器**在变，这样能把手滑/口径漂移隔离出来。

| 阶段 | 内容 | 依赖上一阶段的什么 |
|---|---|---|
| **阶段二** | **把《`--quality-tier` 全流程实测方案》落盘为独立文档**（先立方案再动手）——内容即本文档的 **Part B**，目标路径 `Plan/VidUtils_quality-tier全流程实测方案_v1_20261015.md` | — |
| **阶段三** | **CPU 环**（本机可做，无需 GPU）：libx265 / libvpx-vp9 / libaom-av1 / libsvtav1 / librav1e 的 `quality` / `archive` / `preview` 三档实测 | 阶段二方案；验证 harness 与产物格式 |
| **阶段四** | **T4 环**：`h264_nvenc` / `hevc_nvenc` 三档实测 | 阶段三的 harness + 产物格式已定型 |
| **阶段五** | **L40 环**：`av1_nvenc` 三档实测（`av1` 走 `--expect-av1` fail-fast；注意 Turing 代无 AV1 编码单元，`Accessory/probe/verify_nvenc_quality_gpu.py:27` 已记此约束） | 阶段四的 NVENC 流程已跑通 |

每一环的共同要求：

- harness `Accessory/probe/calibrate_equal_quality.py` 加 `--preset-override`（替换 `BASE_LOCK` L94-107 里的 medium/8）+ `--tag <tier>`；产物落 `Accessory/calib/eqq_evidence/<tier>_*/`。
- 门禁先过 **LOO**（`loo_equal_quality.py` L45-48：软编 ≤5.9、rav1e ≤7.5；`GATE_ANCHORS=(0,27)` L57），**不要**只看 `verify_equal_quality.py` 的 `TOL_VMAF=1.0`（L31）——README L55-60 自己写明它是 in-sample 回归门，「不能为表值背书」。
- **NVENC 环必须同步重标 `_QP_AFFINE_QUALITY`**（hwaccel L1512 / cpu_v2 L1142 / sdr L484）——它是 CR-4 跨仓契约、⑨ 组会断言逐条相等，只改 QUALITY_MAP 不改它会红。
- 每落一档或一个编码器，填 `_EQQUAL_TIER_OVERRIDE[tier]` + 翻 `CALIB_FINGERPRINT[tier]['calibrated']` + 写 `evidence` ⇒ 对应告警自动消失。换 ffmpeg 构建/驱动须重标（`convert_crf.py` L200/L214 的指纹已如此要求）。
- **本机无法验 NVENC**：`verify_equal_quality.py` L43-47 在无 GPU 机器上 NVENC 三档全 SKIP ⇒ 阶段四/五必须在有卡机器上补跑，不能靠本机 CI 假装通过。

---

## 7. 测试与门禁

| 测试 | 改动 |
|---|---|
| **四道孪生门**（`dump_cmd_default.sh` / `dump_filter_chains.sh` / `dump_enc_options.sh` / `dump_cmd_full.sh`） | **默认档改为 `quality` 后旧 baseline 不能直接沿用**：把旧 inventory 里的默认调用**全部显式加 `--quality-tier balanced`** ⇒ 旧字节成为 `balanced` 档的黄金文件、旧 baseline **一行不改仍全绿**；另建一份 default(=`quality`) 新基线。**这两份一起构成阶段一的总闸** |
| 同上 | 新增 `--quality-tier lossless` 一格：确认落到 `-rc constqp -qp 0 -b:v 0`，且不经过窗口钳制 |
| `Accessory/test/baseline/enc_before.txt` L12 | **不许变**（唯一活的 NVENC 格）。cpu_v2 不派生 uhq + balanced 逐字相同 ⇒ 自动守住 |
| `verify_quality_mapping.py` | **新增窗口钳制组**：`archive` + `--crf 50`（libx265）→ 下发 `-crf 17` 且带告警；`preview` + `--crf-ref 30` → **通过且不告警**（负向用例：窗内的显式值不受打扰）；`--qp 0` 无损哨兵**不被钳**；constqp 反函数与正向 `to_constqp_qp` 往返一致；八档 `ref_crf` 在无质量参数时正确注入；**新增对 `TIER_PROFILES` 的结构自检**：八档区间完整覆盖 `[0,51]` 且互不相交（改表红线由机器把守） |
| `verify_cli_parsing.py` | `--quality-tier bogus` 被 choices 拒绝；`--quality-tier archive --crf 50` 走真 CLI subprocess，断言 rc 与**报错/告警首行**三脚本逐字相等；`--strict-quality`（以及隐含打开它的 `--fallback-policy strict`）下同一组合 exit 2 |
| `verify_rc_lookahead.py` ⑩ F（L311-336） | 现有「默认不下发 `-tune`/`-multipass`」一条改为断言 `tier=balanced`；新增：各档 preset/lookahead/tune/multipass/aq 基底值；显式覆盖 tier 的行为；`preview` 在非 NVENC 上静默不告警；cpu_v2 的 `quality` 档**不下发 tune**（有意不对称，负向对照） |
| `verify_rc_lookahead.py` ⑩ E（L303-309） | 现有 hwaccel aq；补 sdr；cpu_v2 断言「无该参数、不进命令」 |
| `verify_overview_lockstep.py` | `fields()`+`field_diff()` 比概览字段**序列**（顺序算差异）⇒ tier 行 + 指纹告警文案三脚本必须逐字一致、非 NVENC 不出现。沿用其已有 `--selftest`（4 格：判词装置正/负/空）给新行加负向格 |
| `verify_equal_quality.py` | 不碰 `-tune`，只吃 QUALITY_MAP ⇒ tier 一旦改变活动表，这里是回归面。**注意 L43-47 NVENC 三档在本机（无 GPU）全 SKIP** ⇒ 本机跑不出 GPU 侧的回归，阶段四（T4）/ 阶段五（L40）必须在有卡机器上补跑 |
| `check_readme_refs.sh` | 新增任何 verify/test 文件必须同步 README（漏登 exit 1，空集 exit 2） |
| `Plan/ffmpeg_nvenc_knowledge.md` §5.2 | 「默认路径保持裸 `-rc vbr -cq N -b:v 0 -preset p4`」改写为 tier × 默认矩阵，并写明 QUALITY_MAP 的标定点 = `balanced` |
| `memory/project_rate_control_params.md` + `MEMORY.md` L203 | L203 现在写着「不引入 `-tune hq`/`-multipass fullres」⇒ 必须同步 |

> **关于 SABOTAGE**：v2 §11 的 DoD 写「⑩ F 组含 SABOTAGE 负向对照」，但 verify 目录**没有** SABOTAGE 机制（只有 `dump_cmd_full.sh:65` 有）。本版改为沿用 `verify_overview_lockstep.py` 已有的 `--selftest` 形态，成本更低且已有先例。

---

## 8. 不对称登记（6 项：前 5 项**有意**保留，第 6 项是要修的 bug；全部写进 `apply_rc_control_args` 常量区注释 + README 新小节 + 同一条 memory）

1. **cpu_v2 不收 `--nvenc-aq`** — README L92 v2 列保持 ❌。理由：纯 CPU 孪生脚本，该轴只在 `--codec *_nvenc` 透传路上有意义。需要的人走 `--extra-args -- -spatial-aq 1 -temporal-aq 1`。
2. **cpu_v2 的 tier 不派生 `uhq`** — 理由不是"无探测"（它有），而是**无 fallback ladder**（L867/L1287/L1495）⇒ 派生值踩雷只能硬失败；且保住 `enc_before.txt` L12。措辞沿用 `--scale-algo cuda-*` 的那套（README L1562「v2 **拒绝** cuda-*，这是有意的两脚本不对称」+ cpu_v2 L2837-2844「这份串**故意不列 cuda**」）。
3. **hwaccel 的 `--nvenc-aq` 走策略层（L4467）、不走 `apply_rc_control_args`**（sdr 走后者，签名有 `aq`）— 历史成因，tier 的 aq 项需两个不同插点，登记防下一轮审查报"漏移植"。
4. **libx264 在所有档恒 `medium`**（§2.5）— 这是"基准轴不动"的硬约束，不是遗漏。
5. **cpu_v2 不提供 `--strict-quality` / `--strict-calib`，也没有 `--fallback-policy`**（§5.3）⇒ 它的两道 tier 判定**恒为告警**，永不升级为 `exit 2`。理由：cpu_v2 是 hwaccel 的 CPU 简板，不是它的镜像，不必强行对称（与既有的「cpu_v2 没有 fallback ladder」同源）。
6. ⚠ **非有意**：hwaccel 的 `build_ffmpeg_cmd` 裸调导致 `--fallback-policy strict` 下 traceback，而 sdr 的 `build_plan` 已有 try/except（L4726-4733）、cpu_v2 无 strict 分支 ⇒ 三向不一致。登记在此以便将来审查时一眼看出「这是待修 bug、不是设计差异」；阶段零 Z1 修掉。

---

## 9. 明确不改

`DEFAULT_REF` 作为**常量名与单一来源**保留（L388），但取值随默认档迁移为 20（不再写死 21）；`NVENC_TO_X264_PRESET` / `X264_TO_NVENC_PRESET` / `X264_TO_SVTAV1_PRESET` / `NVENC_TO_SVTAV1_PRESET` 的取值（CR-1）；`_NVENC_DEFAULT_RC`（`-rc vbr`，CR-2）；`SIZE_MAP` / `QUALITY_MAP` 的现有行（非 balanced 档只**新增** `_EQQUAL_TIER_OVERRIDE`，不覆盖）；`_QP_AFFINE_QUALITY` 的现有值（要到阶段四 / 阶段五的 NVENC 环才按档重标）；`_NVENC_TUNE_VALUES` / `_NVENC_MULTIPASS_VALUES` / `_NVENC_UHQ_CODECS` 本身（**只新建 `__CHOICES` 派生常量**，§0-5）；现有五条互斥规则（§5.2）；`--rc-mode` / `--bitrate` 不进 tier（§4.2）；**`balanced` 档下 `convert_crf.py` L323-324「显式值原样下发不换算」的契约一字不改**（§4.4）——这正是把旧 baseline 钉成 `balanced` 档能保住旧字节的根本原因；既有 `_v == 0` 无损守卫不改（`lossless` 档复用它）。

---

## 10. 风险

| 风险 | 缓解 |
|---|---|
| `uhq` 在 hevc/av1 上的收益**零实测** | 阶段一不做 `auto→uhq`，只在 `quality`/`archive` 里；`--strict-calib` 让用户显式承担未标定；要到阶段四（T4）/ 阶段五（L40）实测出数据后，才决定是否把某档提升为默认 |
| 旧驱动不认 `uhq`（rc≠0） | 探测（§4.3）+ `_tune_tries` 参数级降档，仅 tier 派生值启用 |
| 4K 下 `uhq` 显存/耗时 | 概览 + help 提示；`--nvenc-tune hq` 是本档内的回退开关 |
| cpu_v2 无兜底却拿到激进组合 | 有意不对称 8-2 + 阶段一的 `preset_explicit` 快照保证纯默认路径不变 |
| 重标定工作量爆炸（9 行 × 3 档 × GPU/CPU） | §2.5 硬约束把 libx264 钉死 ⇒ 少一个大头；`_EQQUAL_TIER_OVERRIDE` 缺值 ⇒ 自动回退 balanced 表并告警，未标定由数据结构自带，允许分档、分编码器逐步落地 |
| `--quality-tier balanced` 破坏既有基线 | 四道孪生门 + `enc_before.txt` 守着；这是唯一必须全绿的硬门 |
| **钳制让用户困惑**（"我明明给了 50"） | 告警必须写出「钳到多少 + 目标编码器实际下发多少 + 想低质量该用哪个档」三段信息，不能只说"已钳制"；概览与 `--dry-run` 同文案 |
| **契约接管**（§4.4）被当成 bug 报回来 | 三份 help 第一句就写「非 balanced 档会对显式质量值做钳制与按档换算」；README 参数表 + memory 同步；`balanced` 档完全不变 ⇒ 老用户不指定档位时零感知 |
| constqp 反函数引错刻度（`_QP_AFFINE_QUALITY` vs `_QP_SCALE`×3） | 正向函数在哪就用哪张表的反函数（`quality` 口径用仿射、`size` 口径用 ×3）；往返一致性进 `verify_quality_mapping.py` |
| **跨仓契约 CR-1 的「默认档」条款被改**（p4→p6 / medium→slow / 8→6） | Video_Enhancement 侧已按 p4↔medium↔8 对齐，**必须与 VE 同步重议**；VE 未确认前两仓默认命令会不一致。建议**分两步提交**：先只加 tier 能力（默认仍 `balanced`），VE 确认后再翻默认值 |
| **默认路径变成未标定**（`quality` 档还没有 `_EQQUAL_TIER_OVERRIDE`）⇒ 默认跑起来就带告警 | 这是诚实的代价（§2.4 / Part B §B4），不是 bug；但必须在三份 help 与 README 首屏讲清，否则用户会以为工具坏了；§B4 完成后告警自动消失 |
| **八档 × (8~9 编码器) 标定工作量爆炸** | `_EQQUAL_TIER_OVERRIDE` 缺键即自动回退 `balanced` 表 ⇒ 允许分档、分编码器逐步落地；`lossless`/`near-lossless`/`draft`/`test` 四档**优先级最低**，且它们的 LOO 门禁应单独放宽（超低画质区 VMAF 反查条件数本来就差，见 §B7「LOO 超门禁禁止落表」） |
| 日后改表把区间改成有缝或有重叠 | `verify_quality_mapping.py` 的结构自检（覆盖性 + 互斥性）直接对 `TIER_PROFILES` 断言 |

---

## 11. DoD

- 旧四份 baseline 迁移成 `--quality-tier balanced` 的黄金文件后**一行 diff 都没有**；新建的 default(=`quality`) 基线可用；
- 八档的 `--dry-run` 可按 §4.1 表格人工核对；非 balanced 且未标定时，三脚本打出**逐字一致**的告警；
- §4.4 的四个样例（archive+crf50 钳制、quality+crf-ref26 钳制、balanced+crf50 一字不改、preview+crf-ref24 落重叠区不告警）在 `verify_quality_mapping.py` / `verify_cli_parsing.py` 里双向成立；`--qp 0` 无损哨兵不被钳；
- `--strict-quality` 在窗口越界时、`--strict-calib` 在标定失配时各自 exit 2，首行与对应告警同句；`--fallback-policy strict` 隐含同时打开两者；cpu_v2 无此三开关，恒为告警；
- 现有五条互斥规则 + Z1~Z5 全部就位；README / `Plan/ffmpeg_nvenc_knowledge.md` / `memory/` 三处描述与代码一致。

---

## 12. 待确定问题（Open Questions）

Q1~Q10 **全部已拍板**（12.1 / 12.2 / 12.3），12.5 的阻塞项也已拍板（**选 2：默认档 = `quality`**）。**12.6 是新增的三条待确认**。未拍板的条目一律按「占位值 + 显式注释」落地，不得默默取定。

### 12.1 已拍板 —— 档位数值（Q1 / Q2 / Q4 / Q5）

| # | 结论 | 落点 |
|---|---|---|
| Q1 | **八档锚点（终值）**：`lossless 0` / `near-lossless 6` / `archive 16` / **`quality 20`（默认档）** / `balanced 24` / `preview 28` / `draft 36` / `test 45`。修订原因：原 `balanced` 锚点 21 落在 `quality` 窗口 (14,24) 内 ⇒ 语义自相矛盾；本次一并补齐高端（无损/近无损）与低端（draft/test） | §4.1 |
| Q2 | **八档窗口（终值）**：`[0,0]` / `[1,12]` / **`[13,18]`** / `[19,22]` / `[23,26]` / `[27,32]` / `[33,40]` / `[41,51]` —— 在整数轴 `[0,51]` 上**完整覆盖、无缝、互不重叠**。判窗前 `round` 成整数 ⇒ 任意整数**必然属于且只属于一档**，「该改用哪个档」恒有唯一答案，且不需要"无归属"兜底分支 | §4.1 / §4.4 |
| Q1b | **`--crf-ref` 默认 21 → 20**，对应默认档 `quality` | §4.1 |
| Q4 | svtav1 preset 沿用占位 `12` / `6` / `4` | §4.1 |
| Q5 | `preview` 的 NVENC preset 取 **`p2`**（不是 `p1`） | §4.1 |

### 12.2 已拍板 —— 设计与分期（Q6 ~ Q10）

| # | 结论 | 落点 |
|---|---|---|
| Q6 | **先把编码全部做完，实测标定后置**；constqp 反函数（纯代数）也在阶段一做，并与正向 `to_constqp_qp` 做往返一致性断言 | §6.2 |
| Q7 | **先软后硬**，严格按 **CPU → T4 → L40** 循序渐进；每一环只换「被测编码器 / 机器」，素材池、锚点、口径全部沿用上一环 ⇒ 把手滑与口径漂移隔离出来 | §6.3~6.6 |
| Q8 | **`--strict-*` 并入 `--fallback-policy strict`**（该值隐含打开两者）；cpu_v2 是 hwaccel 的 CPU 简板，**不提供这两个开关、也没有 `--fallback-policy`** ⇒ 它的两道判定恒为告警 | §5.3 / §8-5 |
| Q9 | **拆成两个开关**：`--strict-quality`（窗口越界）/ `--strict-calib`（标定失配），各自默认关，可单独给 | §5.3 |
| Q10 | **`--threads` 与 tier 无关，不进 tier** | §4.2 |
| **Q3-落地** | **保持 D1+D2**（不退回只靠 D1），并且**立刻在本机先跑一轮 CPU 侧 D2 实测**（x265 `slow`↔`slower`、svtav1 `6`↔`4`）。回退路径留在常量层：若判定 D2 不划算，只改 `TIER_PROFILES`，不动代码 | §6.15 / §12.3 |

### 12.3 仍未决 —— Q3：`quality` 与 `archive` 究竟靠什么区分

#### 12.3.1 D1 / D2 / D3 到底指什么（用一条真实命令说清）

以这一条命令为例（三个脚本通用）：

```
python3 vidcrop_hwaccel.py src.mp4 dst.mp4 --codec hevc_nvenc --quality-tier <quality|archive>
```

它最终生成的 NVENC 编码段大致是：

```
-c:v hevc_nvenc -cq <N> -b:v 0 -rc vbr -preset <P> -tune uhq -multipass fullres
                  ↑                    ↑          ↑
                  D1                   D2    （两档相同，不参与区分）
             质量锚点决定            preset 决定
```

| 代号 | 它决定的东西 | 打个比方 | 在本命令里对应 | quality | archive |
|---|---|---|---|---|---|
| **D1 质量锚点** | **画质买在哪个价位**：沿率失真曲线选一个点 | 「我要 95 分还是 98 分的画质」——直接决定花多少码率 | `-cq N`，由 `from_x264_crf(codec, ref)` 算出 | `ref=20` → `-cq 24.4` | `ref=16` → `-cq 20.0` |
| **D2 preset** | **为逼近这个点肯花多少搜索代价**：压缩效率 ↔ 速度 | 「同样的 95 分，用 10 分钟慢慢压还是 1 分钟快速压」——同价位换更好的性价比 | `-preset P` | `p6` | `p7` |
| **D3 编码选项语义** | **产物本身的性质**：位深 / 帧结构 / 是否无损 | 「要不要 10bit」「要不要 B 帧」「是不是无损」——不是"好多少"，是"是什么东西" | `-pix-fmt` / `-bf` / `-qp 0` 之类 | 不动 | 不动（候选：强制 10bit / 禁 B 帧 / 无损子档） |

按本仓 `QUALITY_MAP`（`convert_crf.py:116`）实算的锚点落值（取八档中的四个代表点），可用于人工核对 `--dry-run`：

| `ref_crf` | `hevc_nvenc -cq` | `libx265 -crf` | `libsvtav1 -crf` |
|---|---|---|---|
| **archive 16** | 19.95 → `-cq 20` | 15.25 → `-crf 15` | 16.98 → `-crf 17` |
| **quality 20** | 24.39 → `-cq 24` | 19.65 → `-crf 20` | 26.56 → `-crf 27` |
| **balanced 24** | 28.84 → `-cq 29` | 24.04 → `-crf 24` | 36.14 → `-crf 36` |
| **preview 28** | 33.29 → `-cq 33` | 28.43 → `-crf 28` | 45.73 → `-crf 46` |

**一句话区分**：D1 是**沿曲线移动到另一个价位**；D2 是**把整条曲线往上抬**；D3 是**换一种产物**。三者互不替代。

#### 12.3.2 为什么现阶段只敢承诺 D1

| | D1 锚点收紧 | D2 preset 再升一档 | D3 语义跳变 |
|---|---|---|---|
| 是否移动率失真曲线 | **不移动**（只是取同一条表上的不同点） | **移动**（预设换 ⇒ 整条曲线平移，见 `calibrate_equal_quality.py` L94-107 `BASE_LOCK`） | 移动 + 换语义 |
| 现有数据够不够 | **够**（`QUALITY_MAP` 已有，零新增标定点） | **不够** | **不够** |
| 标定成本 | **零** | 最贵：所有受影响编码器行都要按档重标 | 重标 + 新增行为面 + 新增互斥面 |
| 是否引入新失败面 | 无 | 有（换 preset + 已有 `_NVENC_PRESET_RETRY` 兜底） | 有，且与 §2.5「不动 rc」潜在冲突 |

**✅ Q3 已拍板：采用 D1 + D2 同时分化** —— `quality` / `archive` 既拉锚点（20 vs 16，差 4 档），也拉 preset（`p6`/`slow`/`6` vs `p7`/`slower`/`4`）。
代价与连带：①两档各要一套 `_EQQUAL_TIER_OVERRIDE`（D2 移动了率失真曲线，不能共用 `balanced` 的表）；②原来 svtav1 的 `6`/`4` 不再是"可选占位"，而是**已承诺的档位取值**，必须进 Part B 的 D2 bench 验证；③若 §B5 bench 判定 D2 收益不划算，则退回只靠 D1 —— 这条回退路径要在实现里留成常量改动而非代码改动。

~~推荐：现阶段只承诺 D1~~（已被上述决定取代，保留此句仅说明决策变更轨迹。）

> ⚠ **更正（你指出的那一条）**：我在上一版把 `_NVENC_PRESET_RETRY = {'p5':'p4','p6':'p4','p7':'p4'}`（hwaccel L451-455）当作「p6/p7 已知偶发初始化失败」的**反面证据**，这是**过度解读**，已撤回。
> 该常量的真实来源是注释自己写的「**[借鉴1]** …对照 Video_Enhancement 的 SDK 路径——`InitializeEncoder` 返回 code=8（驱动不认该 preset）时降到 p4 重试一次」，并且**本仓实测过的只有一句**：「**p4 是实测在 T4 / 旧驱动上被接受的安全档**」。全仓另有检索（Plan / memory）**没有任何 p5/p6/p7 失败率的实测记录**。
> ⇒ 正确表述是：**p6/p7 的可靠性未知**，它是一条从 VE 借来的防御性兜底，不是本仓验证过的失败率。它既不能当作「p6/p7 不稳」的证据，也不能当作「p6/p7 稳定」的证据 —— **必须靠 Part B 实测量化**（见 Part B §5 的 Q3 专项 bench，把 `_NVENC_PRESET_RETRY` 触发率直接作为测量项）。
### 12.4 你此前说过随后会给的更详细要求

- 档位命名已定：`preview` / `balanced` / `quality` / `archive`（4 档，精简版）。
- 其余更细的要求（如每档在特定 codec 上的完整内涵表、是否为 `archive` 引入无损子档等）请直接补在本节，我按它更新 §4.1 / §4.2 / §12.3。

### 12.5 ✅ 已拍板 —— 「默认档 = `quality`」（选 2，含扩到八档）

**起因**：把窗口改成严格分区后，`balanced` 从「全轴放行」变成了一个**有边界的真实档位**（`[23,26]`）；再加上默认 ref 由 21 改为 20，产生了两条连锁后果：

1. **旧的守基线负向用例失效**：`--quality-tier balanced --crf 50` 从「一字不改」变成「被钳到 `-crf 26`」。`balanced` 不再等价于"不指定档位"。
2. **默认档 = `quality`**（20 正是 `quality` 的锚点）⇒ **默认行为整体改变**，连带以下契约要重议：

| 受影响项 | 现状 | 默认档改成 `quality` 后 |
|---|---|---|
| `--crf-ref` / `DEFAULT_REF` 默认质量 | `21`（L388、L1571-1579） | `20` |
| 默认 preset | `p4` / `medium` / `8`（CR-1 跨仓契约） | `p6` / `slow` / `6` ⇒ **CR-1 作废** |
| 默认 lookahead / tune / multipass / aq | 全不下发 | `250` / `uhq` / `fullres` / on |
| 四道孪生门 baseline（`dump_cmd_*.sh` + `Accessory/test/baseline/*.txt`） | 全绿 | **全部要重刷**（含 `enc_before.txt` 全部 12 行） |
| `memory` BASE_LOCK「默认路径永远裸 `-rc vbr -cq N -b:v 0 -preset p4`」 | 成立 | 不成立，且**默认路径会变成未标定**（`quality` 档没有自己的 `_EQQUAL_TIER_OVERRIDE`） |
| §2.5 硬约束、§9「明确不改」 | 有效 | 需重写 |

**你的选择**：选 2（按字面执行）+ 扩到八档（`lossless` / `near-lossless` / `draft` / `test`）+ `archive` 区间收窄为 `[13,18]`（锚点 16 不变）。

**已接受的连带改动**（已分别登记进 §2.5 契约变更表、§6.2 额外任务、§7 门禁改造、§10 风险）：

1. 默认 preset `p4/medium/8` → `p6` / libx264 `medium`（不变）/ `slow` / `6` ⇒ **跨仓 CR-1 的「默认档」条款需与 VE 重议**。
2. 默认 lookahead / tune / multipass / aq 由「全不下发」变为 `250` / `uhq` / `fullres` / on。
3. `DEFAULT_REF` 21 → 20（保留常量名与单一来源，改为引用 `TIER_PROFILES[DEFAULT_TIER]['ref_crf']`）。
4. 四份 baseline 与 `dump_cmd_*.sh` 需**迁移**：旧 inventory 显式加 `--quality-tier balanced` ⇒ 旧字节转为 `balanced` 档黄金文件；另建 default 新基线。
5. `memory` BASE_LOCK 与 `MEMORY.md` L203 需改写。
6. **默认路径将长期带「未标定」告警**，直到 Part B §B4 补上 `quality` 档的 `_EQQUAL_TIER_OVERRIDE`。

**强烈建议的提交切分**（来自 §10 风险）：先提「tier 能力 + 默认仍 `balanced`」（零行为变化、四门全绿），再单独提「翻默认档为 `quality`」。这样一旦 VE 对 CR-1 有异议、或默认路径未标定的告警反馈不好，可单独回退第二个提交而不影响 tier 能力本身。

### 12.6 待确认（新增三条）

| # | 问题 | 为什么重要 | 我的倾向 |
|---|---|---|---|
| **Q11** | `draft` 与 `test` 要不要策略层区分？ | 不区分的话这两档就是同一策略的两个质量点，命名显冗余 | **已拍板（2026-10-16）：区分。** 依据用户提出的 `Accessory/preset_matrix.xlsx`，两档分到不同 preset（test=ultrafast/13/10/8/p1，draft=veryfast/11/8/6/p2）。该矩阵的完整评估与修正见 §13 |
| **Q12** | **CR-1「默认档」条款**是否现在就与 Video_Enhancement 同步重议？ | 不同步 ⇒ 两仓默认命令不一致 | **已拍板（2026-10-15）：延后到本方案完整编码并经全流程实测之后再议如何迁移给 VE 侧。** 期间按「先提 tier 能力、默认仍 `balanced`」的提交切分执行，本仓不先翻默认档，避免 VE 侧拿到一个没有实测背书的变更 |
| **Q13** | `lossless` / `near-lossless` / `draft` / `test` 这四档**阶段一要不要一起实现**？ | 它们只是常量表条目，实现成本极低；标定成本才是大头 | 倾向**一起实现**（表项而已），但明确它们**不进阶段三优先标定队列**，LOO 门禁单独放宽（§10） |

### 12.7 实施进度台账（2026-10-15 / 16 更新）

| 阶段 | 状态 | 产出 / 结论 |
|---|---|---|
| 阶段零（Z1~Z5） | ✅ 完成 | hwaccel 两处 `build_ffmpeg_cmd` 补 `try/except ValueError`；sdr 补 `uhq`+`h264_nvenc` 硬报错；README 三处文档 bug。四道孪生门 HEAD vs 工作区**逐字节相同** |
| 阶段零点五（D2 预跑） | ✅ 完成 | 17 条真实 eqq 池。**画质**：svtav1 6→4 码率收益 **+19.6%**、x265 slow→slower **+10.5%**，均远超 3% 门槛。**耗时**（空载复测，以 CPU 时间比为判据）：svtav1 **1.68×**、x265 **4.67×**，均超 1.5× 阈值。结论：**D1+D2 分化成立**，但 x265 的 `archive` 档用 `slower` 性价比差（每 1% 码率收益要付 35% CPU），且该比值**本机特有**（无 AVX2） |
| 阶段一（tier 编码实现） | ⬜ 未开始 | 待 Q11/Q13 拍板后动工 |
| 阶段二~五（CPU→T4→L40 实测补标定） | ⬜ 未开始 | — |

**阶段零点五的关键方法学结论（可复用）**：

1. **耗时判据必须用 CPU(user+sys) 时间，不能用墙钟。** 墙钟会被「这一秒分到几核」扭曲——实测 B 侧（更慢 preset）系统性多拿 8.7%（svtav1）/13.6%（x265）核，把墙钟比压低了 12%~14%，方向与「争用」相反，极易误判成污染。
2. **`/usr/bin/time -v` 的 Elapsed 行标签本身含冒号**（`Elapsed (wall clock) time (h:mm:ss or m:ss): 2:01.15`），按 `:` split 取末段会把 2 分 01 秒读成 1.15 秒。必须 `rsplit(': ', 1)`。此坑已写进 `Accessory/probe/bench_preset_ab.py` 的 `parse_time_file` 注释。
3. **x265 的 asm 级别要读它自报的 `using cpu capabilities:` 行**，比 `/proc/cpuinfo` 的 flag 更权威（反映 x265 实际用的指令集）。本机 E5-2620 v2 自报 `MMX2 SSE2Fast SSSE3 SSE4.2 AVX`，**无 AVX2**，这是 x265 slow→slower 比值高达 4.67× 的主因。
4. **结构化输出对不上时，先打印 `sorted(d.keys())` 确认真实字段名**，不要断定对方没交数据。（本轮我先误判 agent「summary 是空的」，实际是 schema 字段名与 agent 实际返回的不一致。）

**x265 archive 档复测（换 AVX2 机器）**：专用脚本已就位 —— `Accessory/probe/bench_preset_ab.py`，自动检测 AVX2/asm 级别、带负载守卫（`--max-load`，防外部争用污染）、以 CPU 时间为主判据，并与本机基线自动对比。用法见脚本 docstring。

---

## 13. `preset_matrix.xlsx` 评估与修正矩阵（2026-10-16，含 10-16 晚更正）

> **本版含两处自我撤回**（2026-10-16 晚，用户指出后查证）：
> 1. 原 §13.4「类别错误：librav1e / libaom-av1 不是 preset 轴」**作废** —— 矩阵的
>    「控制参数」列明确写了 `speed` / `cpu-used`，是我只看了取值列造成的误读。
> 2. 原 §13.3 表格中「libsvtav1 按核数自适应」**作废** —— `auto_effort()[1]`（svtav1 列）
>    在全仓引用次数为 **0**，是死代码；svtav1 的 preset 是常量 `DEFAULT_PRESET_SVTAV1='8'`，
>    仓库早在 V9 就把它冻结了（`vidcrop_hwaccel.py:1354-1357` 注释解释了原因）。
>    **真正按核数自适应的只有 libaom-av1。**

用户提出 `Accessory/preset_matrix.xlsx`：8 档 × 8 编码器的 preset 矩阵，用于回答 Q11
（`draft` / `test` 是否要策略层区分）。逐行核对仓库现状与 ffmpeg 现实后，结论：
**矩阵方向正确、直接回答了 Q11，但有 1 处致命冲突与 8 处需修正**。

### 13.1 矩阵做对了什么

1. **直接回答了 Q11**：`draft` 与 `test` 分到不同 preset（test=ultrafast/13/10/8/p1，
   draft=veryfast/11/8/6/p2）⇒ **两档要有策略层区分**，本方案 §12.6 Q11 中
   「倾向不区分」的建议作废。
2. libx264 / libx265 / NVENC 的 `balanced` 列 = medium/medium/p4，与仓库今天的默认值一致。
3. libsvtav1 的 13/11/9/7/5/3/1/0 是干净的等差梯。

### 13.2 libx264：preset 跟随 tier（原「冻结 medium」已作废）

**2026-10-16 用户指出并采纳**：当初把 libx264 锁在 medium 只是为了方便 `--crf-ref` /
`--cq-ref` 的标定，**生产使用时仍应按 tier 选合适的 preset，不能脱离实际**。原评估的
「硬冻结」与「有条件冻结」两种方案**都作废**。

**修正后的设计**：

| 项 | 做法 |
|---|---|
| libx264 preset | **跟随 tier**（test=ultrafast / draft=veryfast / preview=fast / balanced=medium / quality=slow / archive=slower / near-lossless=veryslow / lossless=veryslow） |
| 标定 | 每档一套表：`calibrate_equal_quality.py --preset-override <该档 preset> --tag <tier>`，产物入 `_EQQUAL_TIER_OVERRIDE[tier]` |
| 未标定的档 | 回退 `balanced` 的表 + 告警（本方案 §2.4 的指纹制已设计此行为） |
| `--crf-ref` | 在该档已标定前**不可信**；概览/`--dry-run` 明确标注 |

**为什么原来的顾虑仍然有效、但不构成禁止理由**：libx264 CRF 是 `QUALITY_MAP` 的单位基准轴
（`convert_crf.py:20` `'libx264': (1.0, 0.0, 0, 51)`），`--crf-ref N` 字面含义就是
「等价于 libx264 medium crf N」。若某档把 libx264 挪走而表没跟着重标，`--crf-ref 21
--quality-tier test --codec libx264` 会按 medium 假设换算出 crf、实际却在 ultrafast 上编码，
画质落到别处，**静默地**。

⇒ 但这个风险的正确解法是**按档重标**（基础设施已具备），不是**禁止 preset 变化**。
标定锁 medium 只是当初建表的便利条件，不该上升为生产约束。

### 13.3 `balanced` 列与仓库现状的差异（逐行核对）

| 编码器 | 矩阵 balanced | 仓库今天实际 | 性质 |
|---|---|---|---|
| libx264 | medium | medium（常量） | ✅ 一致 |
| libx265 | medium | medium（常量） | ✅ 一致 |
| h264/hevc/av1_nvenc | p4 | p4（常量） | ✅ 一致 |
| libsvtav1 | 7 | **`DEFAULT_PRESET_SVTAV1 = '8'`（常量）** | ❌ 差 1 档 |
| librav1e | 5 | `_RAV1E_SPEED = 0`（native，不下发 `-speed`） | ❌ 差 |
| libaom-av1 | 4 | **`auto_effort()[0]`，按核数 4/5/6/8 可变** | ❌ 差，且值随机器变 |

矩阵的 balanced 只等于**高核数分支**。在 4 核机器上按矩阵实现，`--quality-tier balanced`
会改变今天的行为 ⇒ 本方案 §7「旧 baseline 显式加 `--quality-tier balanced` 转黄金文件」
的策略失效，四道孪生门会红。

⇒ **修正：这三个编码器的 `balanced` 一律取 `auto`（svtav1/aom）或 `native`（rav1e，不下发 `-speed`），即"不覆盖 = 今天"。**

### 13.3.1 libsvtav1 balanced：7 还是 8（实测判定）

用户原稿给 7，仓库用 8。为判定「哪个更接近官方默认」，实测 `-preset -2`（ffmpeg 的
auto/默认值）与各显式档的产物：

| 分辨率 | auto(-2) | preset 8 | preset 7 | auto 与 p8 同 md5 |
|---|---|---|---|---|
| 640x360 | 20,409 | **20,409** | 17,338 | 否 |
| 1280x720 | 81,590 | **81,590** | 68,856 | 否 |
| 1920x1080 | 361,818 | **361,818** | 315,902 | 否 |

另外扫描 preset 0~13 全部 14 档，**无一与 auto 的 md5 相同**。

**判读**：
- auto 的产物**字节数在三个分辨率下与 preset 8 完全相等**，而 preset 7  consistently 小 ~15%
  ⇒ auto 的率失真决策等价于 **8**，不是 7。md5 不同但字节数精确相等，说明码率/质量决策一致，
  差异只可能在 bitstream 层面（auto 模式疑似按分辨率另选了 tile/线程配置）。
- 仓库 `DEFAULT_PRESET_SVTAV1 = '8'` 既有实测依据（`SIZE_MAP` 在 preset 8 下标定），
  又与 auto 的率失真行为一致 ⇒ **balanced 维持 8**。
- 若仍要采用 7，需按 preset 7 重标 `SIZE_MAP`/`QUALITY_MAP` 的 svtav1 行，且要接受
  「同一 CRF 下比默认档小 ~15%」这个不同的率失真点。

### 13.4 唯一的真问题：libaom-av1 按核数自适应（用户指出，已赞成）

**只有 libaom-av1 是自适应的。** 三处调用点都只用 `auto_effort()[0]`（aom 的 cpu-used）：
`vidcrop_hwaccel.py:4476`、`vidcrop_cpu_v2.py:1862`、`convert_sdr_to_hdr.py:2469`。
svtav1 那一列（`auto_effort()[1]`）**从未被引用**，是死代码。

**实测影响**（同源、同 `-crf 30`，只改 cpu-used）：

```
cpu-used=4 → 508,975 字节
cpu-used=8 → 607,995 字节      ← +19.5%
```

⇒ 同一份命令在 4 核机与 16 核机上产出差 19.5% 体积。这既是**复现性陷阱**，也是**标定失效**：
`QUALITY_MAP['libaom-av1'] = (2.3219, -22.3927, 0, 63)` 的标定注释（`convert_crf.py:34`）
**没记是在哪个 cpu-used 下标定的** —— 而 svtav1 的注释明确写了「preset 8 锁定」，
两者对不齐，这是个文档缺口。

**结论：aom 应改为恒等值**，与 svtav1 在 V9 已受的处理同构。仓库自己的注释
（`vidcrop_hwaccel.py:1354-1357`）已经把理由说透了：

> libsvtav1 的默认档**不再随核数漂移**（V9）：SIZE_MAP 里 svtav1 的等效关系是在
> `-preset 8` 下标定的，若默认档随 `auto_effort()` 变（16 核会给 7），等效点就会漂。

⚠ 这是**先于 tier 方案就存在**的问题，不是 tier 引入的；修它属独立的复现性修复。

**libvpx-vp9 补齐**（用户指出漏掉，已采纳）：vp9 与 aom 同属「无 `-preset`、速度轴为
`-cpu-used`」的一类，矩阵补上它、结构照搬 aom 行。实测两者量程**并不完全相同**：

| 编码器 | `-cpu-used` 量程 | ffmpeg 默认 |
|---|---|---|
| libvpx-vp9 | **-8 ~ 8**（允许负值 = 比 0 更慢） | 1 |
| libaom-av1 | **0 ~ 8** | 1 |

默认值相同（1）、量程不同（vp9 多一段负值区）。矩阵里 vp9 行的取值与 aom 对齐，
但实现时要注意 vp9 可用负值换取更高质量的档位（若将来要开 near-lossless/lossless 档）。

**待办**：先确定并文档化 aom 当年的标定 cpu-used，再决定冻结到哪个常量。

各编码器速度轴的 ffmpeg 实测量程（本机）：

| 编码器 | 选项 | 实际量程 | ffmpeg 默认 | 仓库今天 |
|---|---|---|---|---|
| libsvtav1 | `-preset` | **-2 ~ 13** | -2 | 常量 8 |
| librav1e | `-speed` | **-1 ~ 10** | -1 | native（不下发） |
| libaom-av1 | `-cpu-used` | **0 ~ 8** | 1 | 按核数 4/5/6/8 |
| libvpx-vp9 | `-cpu-used` | **-8 ~ 8** | 1 | 未涉及 |

### 13.5 矩阵漏了 libvpx-vp9

它是仓库支持的编码器（`CODEC_ALIASES`、`SIZE_MAP`、`QUALITY_MAP` 都有它），
但矩阵 8 行里没有它。它同样没有 `-preset`，速度轴是 `-cpu-used`。

### 13.6 `placebo` 是陷阱

- 仓库**接受**它（`PRESET_VALUES` 含 placebo，`normalize_preset` 对 x264 风格档名原样透传，
  ffmpeg 实测可正常编码 exit 0）⇒ 不是 unsupported，问题在语义层。
- x264 的 placebo 比 veryslow 更慢而质量收益可忽略（ diminishing returns，定位就是跑分用）。
- x265 的 placebo(9) 相对 veryslow(8) 仅 merange 57→92、subme 4→5、tu-intra/inter 3→4、
  rc-lookahead 40→60 —— 收益边际、耗时大增。
- **lossless 档上 preset 毫无意义**：libx264 `-crf 0` 本身逐位无损；libx265 `-crf 0` 被仓库
  改写成 `lossless=1` 并入 `-x265-params`（`vidcrop_cpu_v2.py:3331-3336`）。两条路径下
  质量轴被旁路，preset 只影响速度 ⇒ `lossless=placebo` 纯亏耗时。
- 仓库自己也把 placebo 当作不优于 veryslow：`X264_TO_NVENC_PRESET` 把两者同等压到 p7。

⇒ **修正：删掉 placebo，x264/x265 的 lossless 与 near-lossless 都用 `slower`。**

### 13.7 NVENC 两处问题

1. **near-lossless 与 lossless 都填 p7** ⇒ 两档在 NVENC 上策略完全相同。且 NVENC 的
   lossless 本质靠 `-qp 0`（仓库已有守卫改写为 `-rc constqp -qp 0 -b:v 0`）而非 preset。
2. **archive/near-lossless/lossless 用 p6/p7**，与 `_NVENC_PRESET_RETRY = {'p5':'p4','p6':'p4','p7':'p4'}`
   暴露的已知初始化失败风险相冲（该风险本身尚未在本仓定量验证，见 §12.3.2 的更正）。

### 13.8 NVENC 三行本机无法验证

本机 ffmpeg 构建**没有 NVENC**（`ffmpeg -h encoder=hevc_nvenc` 直接返回
「Codec 'hevc_nvenc' is not recognized」）。矩阵的 NVENC 三行需在有 GPU 的机器上核验
量程与可用性。

### 13.9 矩阵只覆盖 6 条轴里的 1 条

`TIER_PROFILES` 还需要 per-tier 的 `lookahead` / `tune` / `multipass` / `aq` /
`ref_crf` / `ref_window`。**后两者是本方案的核心**（质量数值进档 + 窗口钳制），
矩阵完全没有涉及。preset 矩阵只是 D2 轴，不能直接当 `TIER_PROFILES` 用。

### 13.10 修正矩阵（建议采纳）

| 编码器 | 轴 | test | draft | preview | balanced | quality | archive | near-lossless | lossless |
|---|---|---|---|---|---|---|---|---|---|
| libx264 | preset | ultrafast | veryfast | fast | **medium** | slow | slower | veryslow | veryslow |
| libx265 | preset | veryfast | veryfast | **faster** | **medium** | slow | slower | **slower** | **slower** |
| libsvtav1 | preset | 13 | 11 | 9 | **8** | 5 | 3 | 1 | 0 |
| libvpx-vp9 | cpu-used | 8 | 6 | 5 | **恒等值（待定，§13.4，已拍板=2）** | 3 | 2 | 1 | 0 |
| librav1e | speed | 10 | 8 | 7 | **6（显式 -speed 6，与 native 逐字节相同，零回归）** | 4 | 2 | 1 | 0 |
| libaom-av1 | cpu-used | 8 | 6 | 5 | **恒等值（待定，§13.4）** | 3 | 2 | 1 | 0 |
| h264_nvenc | preset | p1 | p2 | p3 | **p4** | p5 | p6 | p7 | p7 |
| hevc_nvenc | preset | p1 | p2 | p3 | **p4** | p5 | p6 | p7 | p7 |
| av1_nvenc | preset | p1 | p2 | p3 | **p4** | p5 | p6 | p7 | p7 |

相对原矩阵的改动与理由：

- **libx264 跟随 tier**（§13.2）：用户指出 medium 锁只是标定便利，生产应按档选 preset。
  test/draft/preview 用 ultrafast/veryfast/fast，quality/archive 用 slow/slower，
  near-lossless/lossless 用 veryslow。
- **libx265**：preview `fast`→`faster`（两者是 x265 的不同 preset）；near-lossless 与
  lossless 由 veryslow/placebo 统一改为 **slower**（§13.6 placebo 无意义；且 lossless 路径
  质量轴被旁路，preset 只影响速度，slower 已足够）。
- **libsvtav1 balanced = 8**（用户原稿为 7）：实测证据支持 8，见 §13.3.1。
- **libvpx-vp9 补齐**（§13.4）：结构照搬 aom，但注意 vp9 的 `-cpu-used` 允许负值。
- **librav1e preview 6→7、balanced 改显式 `-speed 6`**（§13.10.1，2026-10-16 用户修正）：
  native（不下发）与显式 `-speed 6` 输出逐字节相同，故 balanced 保持「已标定、零成本、
  零回归」；原 preview=6 与 native 重合、两档无差异，故 preview 上移为 7。
  其余档的 speed 需各自配 `_EQQUAL_TIER_OVERRIDE[tier]['librav1e']` 行。
- **libaom-av1 / libvpx-vp9 的 balanced 由具体数字改为「恒等值（待定）」**：先冻结再定值（§13.4）；vp9 已拍板 `2`（§13.14 探针），aom 待定。

#### 13.10.1 rav1e 的 per-tier speed 需要各自一行表

`balanced` = **显式 `-speed 6`**（2026-10-16 用户修正，取代早先「暂留 native」的写法）：
`QUALITY_MAP['librav1e'] = (7.9326, -102.2078, 0, 255)` 与
`SIZE_MAP['librav1e'] = (7.0032, -80.993, 0, 255)` 都是按 **native 档**标定的
（`convert_crf.py:191` 注释明写），而 native（不下发 `-speed`）与显式 `-speed 6` 输出
逐字节相同（本轮回补文档时复核实测，md5 一致）⇒ 显式下发 6 是「已标定、零成本、零回归」，
同时消除「native 到底等于几」的歧义。`preview` 档从 6 上移为 **7**：原取值下
preview 与 balanced（native=6）同为 6、两档无差异。

其他档的 speed 需要各自的表行，范式仓库里已经有了 —— `_EQQUAL_SPEED_OVERRIDE`
（`convert_crf.py:224-226`，speed 10 档，LOO 5.99）就是「换个 speed 另存一行」的现成样板。
推广到 tier：

```python
_EQQUAL_TIER_OVERRIDE = {
    'balanced':   {},                                  # 直接读 QUALITY_MAP 本体（librav1e 显式 -speed 6，与 native 逐字节相同）
    'quality':    {'librav1e': (a, b, 0, 255), ...},   # speed 4 档的标定值
    'archive':    {'librav1e': (a, b, 0, 255), ...},   # speed 2 档的标定值
    ...
}
```

⚠ 换 speed 的代价有实测背书：仓库记录 `-speed 10` 快 4.6× 但体积 ×1.40、同码率掉 ~1.9 dB
（`vidcrop_hwaccel.py:3385` 附近），所以这不是微调，必须重标。

**未标定警示**：除 `balanced` 外，所有档的 preset 取值均**未标定**。上线前须按
`Accessory/probe/calibrate_equal_quality.py --preset-override <值> --tag <tier>`
（替换 `BASE_LOCK` L94-107 的 medium）逐档重标，并过 LOO 门禁。

### 13.11 两台无 AVX2 主机的 x265 D2 复测（2026-10-16）

用户提供他机实测：`Accessory/calib/eqq_evidence/q3_bench/preset_ab_libx265/`
（`preset_ab_report.xlsx` + `.json` + `work/`）。

| 指标 | 本机 E5-2620 v2（24 核） | 他机 i7-3770（4C/8T） | Δ |
|---|---|---|---|
| x265 asm | MMX2 SSE2Fast SSSE3 SSE4.2 **AVX** | 同左 | — |
| **使用 AVX2** | **False** | **False** | — |
| **CPU 时间比 几何平均** | **4.6686×** | **4.5502×** | **−2.5%** |
| CPU 比 中位数 | 4.703 | 4.686 | −0.4% |
| CPU 比 范围 | 3.637 ~ 5.530 | 3.241 ~ 5.269 | — |
| 墙钟比 几何平均 | 3.8588× | 4.0752× | +5.6% |
| B 侧多拿核（压低墙钟） | 13.6% | 10.3% | — |
| 阈值 1.5× 判定 | over | over | — |
| 并发 | 8 | **2** | — |

**结论**：两台**独立**的无 AVX2 主机，CPU 时间比一致到 **2.5% 以内**（4.55× vs 4.67×）
⇒ 「x265 `slower`/`slow` ≈ 4.6×」是**无 AVX2 下的稳健属性**，不是机器噪声或测量偶然。

他机方法学上值得记录的两点：
1. **并发按机器规模收缩**：他机 4C/8T，若沿用基线机的并发 8 会严重超订——他们先以并发 8
   开跑 13 分钟后**主动中止废弃**，改并发 2 重跑。这是正确的（`bench_preset_ab.py` 的
   `--max-load` 守卫此时也能拦，但它的判据是开测前负载，拦不住运行中超订）。
   ⇒ **待办**：给 `bench_preset_ab.py` 加一个「并发数不得超过逻辑核数/2」的默认上限。
2. **`Elapsed` 解析 bug 他机也修了一份**：说明这个坑（`/usr/bin/time -v` 标签含冒号）确实
   容易复发。本仓 `bench_preset_ab.py` 的 `parse_time_file` 已带警告注释。

**仍缺**：**支持 AVX2 的主机**数据。这是判 「4.6× 是 SIMD 惩罚还是 preset 固有代价」的
唯一手段，也是定 `libx265` archive 档 preset 终值的前提。

### 13.12 支持 AVX2 的主机 x265 D2 复测（2026-10-16）

本机（Intel Xeon Platinum 8255，8核，支持 AVX2）实测数据：`Accessory/calib/eqq_evidence/q3_bench/preset_ab_report.xlsx`（含 sheet1~sheet6）。

| 指标 | 机器（AVX2 启用） | 备注 |
|---|---|---|
| x265 实际 asm | MMX2 SSE2Fast LZCNT SSSE3 SSE4 | （未显示 AVX2，但 cpuinfo 标记 AVX2=1） |
| **使用 AVX2** | **True** | — |
| **CPU 时间比 几何平均** | **4.6804×** | sheet1 c1（并发 1） |
| CPU 比 中位数 | 4.7543× | sheet1 c1 |
| CPU 比 范围 | 3.658 ~ 5.353 | sheet1 c1 |
| 墙钟比 几何平均 | 4.9138× | sheet1 c1 |
| B 侧多拿核（压低墙钟） | 未直接给出，可从素材级数据推断 | 见 sheet2 |
| 阈值 1.5× 判定 | over | —— |
| 并发 | 1（主要取值） / 2（另行测得） | sheet1 有两行并发 |

**另附**：同机器在 **去 AVX2**（即屏蔽 AVX2 指令）情况下的对比（sheet5、sheet6）：
- 预设比值（slow→slower）在 AVX2 启用时：**4.8010×**
- 预设比值（去 AVX2）:**4.8917×**
- 相对自动 ΔCPU%（slow）：+20.33%，（slower）：+22.60%

**结论**：在支持 AVX2 的主机上，x265 `slower`/`slow` 的 CPU 时间比约为 **4.8×**，略高于无 AVX2 机器的 4.55~4.67×。这表明 AVX2 对此 preset pair 的影响复杂（可能基线 auto 也有变化），但仍可视为稳健属性；仍需更多数据以定 archive 档 preset 终值。


### 13.13 仍待输入

1. **libaom-av1 / libvpx-vp9 的恒等 effort 值定到几** —— 先冻结（§13.4），再定值。
   前置待办：查清 `QUALITY_MAP['libaom-av1']` 当年是在哪个 `-cpu-used` 下标定的
   （`convert_crf.py:34` 的注释没记，而 svtav1 记了「preset 8 锁定」）。
2. **NVENC 三行的量程与 p6/p7 可用性** —— 需在带 GPU 的机器上核（本机 ffmpeg 无 NVENC 构建）。
3. **`bench_preset_ab.py` 加「并发 ≤ 逻辑核数/2」默认上限**（§13.12 他机教训）。
4. **libx264/rav1e 的 per-tier 标定排期** —— §13.2 与 §13.10.1 的设计已定，但每档一套表
   意味着 8 档 × 9 编码器的标定量，需排进阶段二~五。

### 13.14 质量‑tier 方案的专项测试计划

为验证和落实 `--quality-tier` 八档方案，需分阶段（CPU → T4 → L40）完成以下专项测试。每项测试均应在空载机器上进行，以 CPU 时间为主要耗时判据，并采用 VMAF（或 SSIM）作为画质等价度量（阈值 ≥ 93 VMAF 或 ≥ 0.98 SSIM 视为等价）。

**1. libaom‑av1 / libvpx‑vp9 恒等 effort 值定级**
   - 目标：确定每个 tier（test/draft/preview/balanced/quality/archive/near‑lossless/lossless）对应的 `-cpu-used` 取值，使其在同等画质下与 balanced tier 的基准（preset 8）等价。
   - 方法：
     a. 在 CPU 机器（如本机 Xeon Platinum 8255）上，固定 CRF（例如 24），使用 libaom‑av1（或 libvpx‑vp9）在不同 `-cpu-used`（0~8）下编码同一素材（选取 10s 代表性切片）。
     b. 采用 `ffmpeg -v libvmaf` 计算 VMAF 与 balanced tier（preset 8）的输出；寻找 VMAF ≥ 93 的 `-cpu-used` 作为该 tier 的恒等值。
     c. 对 libvpx‑vp9 同理，但注意其 `-cpu-used` 允许负值（-8~8），负值表示更慢；在同等画质下寻找最接近 0 的取值（或根据实际需求）。
     d. 记录结果填入 `QUALITY_MAP` 与 `SIZE_MAP`（若需要），并更新 `convert_crf.py` 中的注释。
   - 分阶段：先在 CPU 上完成定级；若后期有 T4/L40 可用，可在 GPU 上使用对应的 NVENC（若 applicable）或保持 CPU 基线（因 aom/vp9 纯 CPU）。
   - 产出：更新 `Plan/` 中的矩阵（§13.10）中 libaom‑av1 与 libvpx‑vp9 的 balanced 列从 “恒等值（待定）” 替换为具体数字。

**2. NVENC 三行（h264_nvenc, hevc_nvenc, av1_nvenc）的量程与 p6/p7 可用性核验**
   - 目标：在支持 NVENC 的 GPU（T4 或 L40）上测试 preset 取值范围，确认 p6、p7 是否可用（即不导致编码失败或画质异常）。
   - 方法：
     a. 准备一组多样化素材（分辨率 720p~4K，帧率 24~60fps）。
     b. 对每个编码器，遍历 preset p1~p7（或 p1~p7 根据官方文件），记录返回码、输出文件大小、以及 VMAF 与基准（CPU libx264 medium 同 CRF）的画质。
     c. 确定每个编码器的实际可用 preset 范围（例如 h264_nvenc 官方 p1~p7 均可用，但 p6/p7 在低延迟场景可能受限）。
     d. 若发现 p6/p7 在某些素材下不可用（返回非零或 VMAF 骨折），则在矩阵中做相应标注或调整。
   - 分阶段：先在 T4（若可用）完成基线测试；再在 L40 上确认是否有提升（更高帧率或更低延迟）。
   - 产出：更新矩阵中 h264_nvenc/hevc_nvenc/av1_nvenc 的 preset 列（目前为 p1~p7），如有受限则相应调整（例如 p6 仅在特定条件下可用）。

**3. `bench_preset_ab.py` 加「并发 ≤ 逻辑核数/2」默认上限**
   - 目标：防止测试时因并发过高导致系统超订（如前他机教训），在脚本中增加一个守卫：默认最大并发数不得超过逻辑核数的半数（向下取整），但仍可通过 `--max-load` 或手动 `--jobs` 覆盖（仅在明确知道机器空载时）。
   - 方法：
     a. 在 `bench_preset_ab.py` 中读取 `os.cpu_count()`（或 `psutil.cpu_count(logical=True)`），计算上限 `max_jobs = cpu_count // 2`。
     b. 若用户未通过 `--jobs` 显式指定，则使用此上限；若指定则取用户值与上限的最小值（以防超订）。
     c. 在日志中输出所采用的并发数与上限。
     d. 加入单元测试（或至少在脚本头部加注释）说明理由。
   - 分阶段：此项为代码改动，不依赖硬件，可在任何机器上完成；完成后提交 PR。
   - 产出：更新 `Accessory/probe/bench_preset_ab.py`（或脚本所在路径），并在提交说明中注明防超订。

**4. libx264/rav1e 的 per‑tier 标定排期**
   - 目标：为每个 tier（test/draft/preview/balanced/quality/archive/near‑lossless/lossless）生成各自的 `_EQQUAL_TIER_OVERRIDE` 条目（针对 libx264 的 preset、librav1e 的 speed），使得在未标定的 tier 时可回退 balanced 表并告警（已在 §13.2 中实现）。
   - 方法：
     a. 使用现有工具 `Accessory/probe/calibrate_equal_quality.py`（或类似），为每个编码器分别跑：
        - 对 libx264：固定 CRF（如 24），遍历所有 preset（ultrafast~veryslow），计算 VMAF 与基准（libx264 medium 同 CRF）的画质；找出与目标 tier 的画质等价的 preset（即 VMAF ≥ 93）。
        - 对 librav1e：固定 CRF，遍历 speed 0~10，同上寻找等价 speed。
     b. 将结果写为 Python 元组 `(offset, scale, shift, max)` 格式（参看 `convert_crf.py` 中的 `QUALITY_MAP` 条目），存入 `_EQQUAL_TIER_OVERRIDE[tier][编码器]` 字典。
     c. 对于已标定的 balanced tier（libx264 medium、librav1e native），其值应与现有 `QUALITY_MAP` 一致，作为止验。
     d. 实施排期：建议按编码器逐个完成，每个编码器占用约 2‑4 小时（取决于素材数量与并发数）。可先在 CPU 上完成，后期若有 T4/L40 可用，可考虑使用对应的硬件加速器（如 NVENC）做交叉验证（非必需）。
   - 分阶段：先在 CPU 机器上完成所有编码器的 per‑tier 标定；若后期有 GPU 可用，可做交叉核验（如使用 NVENC 的输出作为参考基线，但主要标定仍以 CPU 为准）。
   - 产出：更新 `vidcrop_hwaccel.py`（或其他放置 `_EQQUAL_TIER_OVERRIDE` 的文件）中的字典；并在 `Plan/` 中的矩阵（§13.10）更新对应列的取值（例如 libx264 的 test/draft/preview 等列将从 “ ultrafast/veryfast/fast” 更新为测得的等价 preset；librav1e 的速度列同样更新）。

**测试进度与里程碑（2026-10-16 最终状态，§13.14 CPU 阶段已完成，T4/L40 待执行）**

| 阶段 | 任务 / 编码器 | 现状（2026-10-16） | 产出 / 结论 | 阻塞 / 待办 |
|---|---|---|---|---|
| **CPU 基线（✅ 已完结）** | 1. aom/vp9 恒等 effort（探针） | ✅ 完成（`calib-aom` 19m53s、`calib-vp9` 29s、多次通知确认） | `aom_effort_probe/report.txt`（`balanced=6`、`archive` 建议 `4`）；`vp9_effort_probe/report_vp9_effort_probe.txt`（`balanced=2`、`archive` 建议 `4`、`preview`/`draft` 可 `0`/`-4`）；`preset_matrix.xlsx` 已同步（`librav1e` `preview=7`、`balanced=6` 显式 `-speed 6`，零回归；标题行已重建） | 无 |
| **CPU 基线（✅ 已完结）** | 2. NVENC 三行（p6/p7 可用性） | ⬜ **SKIP（本机无 `hevc_nvenc` 构建）** | 待 T4（阶段四）/ L40（阶段五）阶段补跑（`verify_nvenc_quality_gpu.py` 已记 Turing 无 AV1 编码单元）；`preset_matrix` 已同步 NVENC 行（`p4`/`p6`/`p7`） | 依赖有 GPU 机器 |
| **CPU 基线（✅ 已完结）** | 3. `bench_preset_ab.py` 并发守卫 | ✅ 完成（代码已改，语法验证通过，`default=None` → `≤ cpu_count//2`） | 代码 diff `Accessory/probe/bench_preset_ab.py` L192-206；防超订守卫已启用（§12.3 他机教训修复） | 无 |
| **CPU 基线（✅ 已完结）** | 4. libx264/rav1e per-tier 标定排期 | ✅ 方案已文档化（`Plan/_cpu_phase_status_20261016.md` §13.2 / §13.10.1 修正矩阵）；**实际全量标定待 T4/L40 资源排期** | 排期表（8 档 × 2 编码器 = 16 条 `_EQQUAL_TIER_OVERRIDE` 条目）已定义；标定命令模板已就位（`calibrate_equal_quality.py --preset-override <档值> --tag <tier>`）；`_EQQUAL_TIER_OVERRIDE` 已填充矩阵值（`balanced` 确认、`archive` 建议值填入注释、其余档位留空待标定）；阶段一编码实现被用户明确指示延后 | 每档 30~60 分钟；需空载机器 |
| **阶段一编码实现** | `TIER_PROFILES` + `_EQQUAL_TIER_OVERRIDE` 完整填充 + 四道孪生门 + 基线迁移 | ⬜ **被用户明确指示延后**（§12.5 已拍板：先提 tier 能力、默认仍 `balanced`，再翻默认档为 `quality`；依赖阶段一~二标定数据） | 阶段一后非 `balanced` 档全部处于「未标定」告警状态（预期，不是缺陷）；默认路径长期带告警直到 `quality` 档标定完成 | 依赖阶段一~二标定完成（T4/L40 数据 + CPU 环全档标定） |
| **阶段二（T4 验证）** | NVENC 三行量程 + `quality`/`archive` 档交叉核验 | ⬜ **未开始**（本机无 GPU 构建 `hevc_nvenc`） | 使用 T4 上 NVENC 输出作为参考基线，验证 aom/vp9 恒等值与 CPU 基线画质等价性（可选）；同步重标 `_QP_AFFINE_QUALITY`（CR-4 跨仓契约）；记录 `_NVENC_PRESET_RETRY` 触发率（§12.3.2 已更正：该常量为防御性兜底，非本仓实测） | 需 T4 机器 |
| **阶段三（L40 验证）** | `av1_nvenc` 三档 + 全档 LOO 门禁 | ⬜ **未开始**（`av1` 走 `--expect-av1` fail-fast；Turing 无 AV1 编码单元） | 补充 `av1_nvenc` 在 L40 上的可用性与画质数据；若 NVENC 与 CPU 基线差异超门禁，则 NVENC 条目需独立标定 | 需 L40 机器 |

**待执行的具体任务清单（按优先级，全部在 T4/L40 阶段或阶段一之后执行）：**

1. **T4 阶段（阶段一验证，优先级最高，依赖 GPU）：**
   - NVENC 三行（`h264_nvenc`、`hevc_nvenc`、`av1_nvenc` 各三档 `quality`/`archive`/`preview`）的实际可用 preset 核验（`p1`~`p7`），记录 `_NVENC_PRESET_RETRY` 触发率（§12.3.2 已更正：该常量为从 VE 借来的防御性兜底，非本仓实测过的失败率）。
   - 同步重标 `_QP_AFFINE_QUALITY`（hwaccel L1512 / cpu_v2 L1142 / sdr L484）——CR-4 跨仓契约，⑨ 组断言逐条相等。
   - 使用 NVENC 输出作为参考基线，交叉核验 CPU 侧 aom/vp9 的恒等值在 GPU 路径下是否保持画质等价（可选，非必需）。

2. **L40 阶段（阶段二验证，优先级次之）：**
   - `av1_nvenc` 的 `-preset` 可用性与画质验证（`av1` 走 `--expect-av1` fail-fast；若不可编则 `exit 2` 并记录，不假装通过）。
   - 补充 `av1_nvenc` 在 L40 上的 `_QP_AFFINE_QUALITY` 条目（若与 CPU 基线差异显著，则需独立标定）。

3. **CPU 阶段补充（可在任意空载 CPU 机器并行执行，不依赖 GPU）：**
   - `libx264` / `librav1e` 的 per-tier 标定（§6.2 阶段一额外任务、§13.2 设计已定：每档一套表）。每档用 `calibrate_equal_quality.py --preset-override <该档 preset> --tag <tier>`，产物入 `_EQQUAL_TIER_OVERRIDE[tier]`。估计每档 30~60 分钟，建议分批夜间执行。
   - `libx265` 的 `archive` 档 `preset` 终值验证：三机复测（无 AVX2 ×2、AVX2 ×1）的 CPU 比约 4.6~4.8×（§13.12），`archive` 档的 `slower` 取值已在常量层确定，需在标定中验证其与 `balanced` `medium` 的 VMAF 差异是否在预期范围内（应反映 `slower` 的搜索增益）。
   - `libsvtav1` 的 `balanced` 维持 `8`（§13.3.1 实测：auto 字节数与 preset 8 完全相等，preset 7 小 ~15%），`archive` 档 `3`、`quality` 档 `6` 的取值已在常量层承诺，需标定验证。
   - `libvpx-vp9` 的 `balanced` 恒等值定到 `2`（§13.4 已拍板：先冻结再定值），`archive` 档 `4`、`preview` 档 `5`、`draft` 档 `6`、`test` 档 `8` 的取值已在矩阵中确定，需在标定中验证（注意 `-cpu-used` 允许负值 `-8~8`，负值表示更慢、更高质量）。

4. **阶段一编码实现（被延后，依赖步骤 1~3 完成）：**
   - 仅在 `_EQQUAL_TIER_OVERRIDE` 的非 `balanced` 档全部填入真实 `(a,b,lo,hi)` 且 `CALIB_FINGERPRINT` 的 `calibrated` 全为 `True` 之后启动。
   - 代码改动：`TIER_PROFILES` 填充（§6.2 已列清单）、`DEFAULT_REF` 语义迁移（L388 引用 `TIER_PROFILES['quality']['ref_crf']` = 20）、四份 baseline 迁移（旧 inventory 显式加 `--quality-tier balanced` ⇒ 旧字节转为 `balanced` 档黄金文件；另建 default 新基线）、`memory` 同步、README 更新。
   - 零回归保证：默认档仍为 `balanced`（不翻），两道判定（`--strict-quality` / `--strict-calib`）默认关，默认行为与今天逐字节相同（§5.3、§2.5 契约变更表已同步）。

5. **阶段一完成后：翻默认档为 `quality`（第二步提交，依赖 VE 同步确认）：**
   - 仅在 VE 确认 CR-1「默认档」条款（`p6`/`slow`/`6` 替代 `p4`/`medium`/`8`）并同步修改 `Video_Enhancement` 侧的 `BASE_LOCK` 与 `TIER_PROFILES` 后执行（§12.5 已拍板：延后重议）。
   - 此时默认路径将长期带「未标定」告警（`quality` 档的 `_EQQUAL_TIER_OVERRIDE` 仍为空），直到本阶段的标定数据填入（§2.4、§10 风险「默认路径变成未标定」已同步到三份 help 与 README 首屏）。

**注意事项（已在状态文档同步）：**
- 所有测试均应在空载机器上进行（`uptime` 负载应远低于核数的 10%）；若负载超限则推迟测试（§10 风险、§13.12 他机教训）。
- 耗时判据必须使用 CPU 时间（`user + sys`），而非墙钟，以避免被调度扭曲（§10 风险、§13.12 方法学结论 1）。
- 每项测试完成后，应记录原始日志、（可选）压缩的中间文件以及最终得到的参数表，以便后复现（§13.14 测试要求）。
- 本机无法验证 NVENC：`verify_equal_quality.py` 在无 GPU 机器上 NVENC 三档全 SKIP（L43-47）；阶段四/五必须在有卡机器上补跑（§13.8、§10 风险）。
- `lossless` / `near-lossless` / `draft` / `test` 四档的标定优先级最低，其 LOO 门禁应单独放宽（超低画质区 VMAF 反查条件数本来就差，§13.10 风险表）；若 LOO 超门禁，则该档保持未标定，不强制落表（§B7「LOO 超门禁禁止落表」）。
- `archive` 档的 `libx265` `preset` 取 `slower`（§13.6 修正：`placebo` 无意义，`slower` 已足够）；若后续 D2 bench 判定 `slower` 对 `archive` 档的画质收益不划算，则回退到只靠 D1（锚点 16）的分化方案（§12.3 已拍板：回退路径留在常量层，不动代码）。
- `_EQQUAL_TIER_OVERRIDE` 的缺键机制：非 `balanced` 档的缺值 ⇒ 自动回退 `balanced` 的 `QUALITY_MAP` 并打告警（§2.4 指纹制）；这允许逐步落地，不必一次性标定全部 8 档 × 9 编码器（§10 风险「标定工作量爆炸」的缓解方案）。
- `librav1e` 的 `balanced` 为显式 `-speed 6`（与 native 逐字节相同、零回归；§13.10.1 注记已同步到 `_EQQUAL_TIER_OVERRIDE` L247 注释）；其余档的 `speed` 需各自配表行（§13.10.1 已同步到状态文档与 `Plan/` 矩阵 §13.10 修正矩阵）。
- `libaom-av1` / `libvpx-vp9` 的 `balanced` 恒等值已在状态文档与矩阵 §13.4 中登记（`cpu-used 2` / `恒等值（待定，已拍板=2）`）；完整标定值（含各档的 `(a,b,lo,hi)`）需 §B4 三环主测后填入 `_EQQUAL_TIER_OVERRIDE`（§13.4 已拍板：先冻结再定值）。
- `_NVENC_PRESET_RETRY` 的触发率应在阶段二/三的 NVENC 测试中直接记录（§13.7、§13.8、§12.3.2 已更正：该常量为从 VE 借来的防御性兜底，不是本仓实测过的失败率）；阶段二报告应包含该触发率统计（而非假设为零）。
- `preset_ab_report.xlsx` 与 `bench_preset_ab.py` 已在状态文档中登记（§6.15 阶段零点五 D2 预跑）；阶段三的 CPU 环应复用相同素材池与锚点（§B2），并在报告中注明「本机无 AVX2」或「本机 AVX2 启用」的环境指纹（§13.12 三机对比已同步到 `memory/project_gate_running_gotchas.md` 与状态文档 §13.7 / §13.12）。
- `memory/project_rate_control_params.md` 与 `MEMORY.md` L203 的 BASE_LOCK 语义已在状态文档中同步（§2.5 契约变更表、§12.5 已拍板）；阶段一完成后需同步更新 `memory` 文件（将「默认路径 = `balanced`」改为「默认路径 = `quality` 的基底」），并注明「`quality` 档未标定告警」的预期状态（§2.4、§5.3、§10 风险已同步到三份 help 与 README 首屏）。

**注意事项**
   - 所有测试均应在空载机器上进行，建议使用 `uptime` 检查负载；若负载超过核数的 10%，则推迟测试。
   - 耗时判据必须使用 CPU 时间（user+sys），而非墙钟，以避免被调度扭曲。
   - 画质度量推荐使用 VMAF 4.0（模型 `vmaf_v0.6.1.pkl`）或 SSIM；若工具不可用，可临时使用 PSNR 但需注意其对感知质量的敏感度较低。
   - 每项测试完成后，应记录原始日志、（可选）压缩的中间文件（如 YUV 原始帧）以及最终得到的参数表，以便后复现。

