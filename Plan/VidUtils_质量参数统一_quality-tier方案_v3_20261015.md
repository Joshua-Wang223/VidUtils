# VidUtils 质量参数统一优化方案 v3 —— `--quality-tier` 与「标定指纹制」

- 日期：2026-10-15
- 前作：`Plan/VidUtils_NVENC调优轴默认值优化方案_v2_20261013.md`（**本版取代它**，v2 的 §12 三项待拍板已在本版拍定）
- 适用脚本：`vidcrop_hwaccel.py`、`vidcrop_cpu_v2.py`、`convert_sdr_to_hdr.py`（孪生约定：常量、取值表、报错首行逐字一致；有意的不对称单列 §8）
- 状态：**实施中**（2026-10-15 批准）。进度台账见 §12.7；剩余待确认项见 §12.6。
- 配套文档：`Plan/VidUtils_quality-tier全流程实测方案_v1_20261015.md`（实测与补标定）
- 复测工具：`Accessory/probe/bench_preset_ab.py`（preset A/B 耗时专测，自动检测 AVX2、带负载守卫）
- 最新输入：§13 `preset_matrix.xlsx` 评估与修正矩阵（2026-10-16，Q11 据此拍板为「区分」）
- **修订 R1（2026-10-17，审核后执行）**：① H1 拍板**读法 A**——窗口/钳制/锚点注入对**全档**生效（`balanced` 无特殊地位），旧 baseline 改为「钉档 + 四份 golden 全量重录 + 人工审阅」；② H2 定案**唯一权威 `TIER_PROFILES`**（§4.1 preset 列 = §13.10R = `preset_matrix.xlsx`）；③ H3 时序重排：harness 标定先行（`--preset-override` ✅ 已落地），CLI 端到端为阶段一 DoD；④ H4：`_EQQUAL_TIER_OVERRIDE` 非 `balanced` 键一律空。决策记录见 §13.15（R1-1~R1-7）。
- 行号引用说明（R1）：本文行号为写作时快照，**以符号名为准**；已修正的失效引用在各节就地注明。

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

> **R1 拍板（2026-10-17）**：两道自动判定（窗口钳制 / 标定指纹）对**全部八档一视同仁**——`balanced` 不再豁免（H1=读法 A，取代 v3 草稿里的「balanced 完全不变」）。连带：默认档先维持 `balanced`（阶段一时序，§6.2），但**旧 baseline 的「逐字节不变」不再成立，四道孪生门改为全量重录 + 人工审阅**（§7 / §11）。锚点/窗口/钳翻规则本身（§4.1/§4.4）不变。

---

## 2. 参数交互全景（本轮的核心分析）

### 2.1 质量入口有三类，不是一类

| 入口 | 位置 | 语义 | 是否过等效表 |
|---|---|---|---|
| **字面量** `--crf` / `--cq` / `--qp` | `_resolve_quality_params`（行号为快照，以符号名为准） | 按**目标编码器原生刻度**直发 | **不过表**（`convert_crf.py` 契约句「同族参数一律原样下发，不换算」）——⚠ **R1：tier 上线后该契约在档内被接管**：值先归一到基准轴、过窗口、按档换算（§4.4），**对全档生效、含 `balanced`** |
| **统一基准轴** `--crf-ref` / `--cq-ref` | L3545-3551 `ref_x264` | 先归一到 **libx264 CRF 轴**再 `from_x264_crf(codec, N)` | **过表**（`--cq-ref` 多一步 `to_x264_crf('h264_nvenc', N)`） |
| **缺省** | `default_quality_for()`（旧名 `_default_quality_for`） | `from_x264_crf(codec, DEFAULT_REF)`；`DEFAULT_REF` = 默认档锚点：阶段一 `24`（balanced）→ 翻档后 `20`（quality），R1-7 | 过表 |

⇒ **tier 影响的不是"给不给值"，而是"同一个值在该组合下意味着什么画质"**。字面量入口虽然不过表，但用户的 CRF/CQ 直觉也是在 medium/p4 上建立的。

> **本方案新增第四类入口**：`--quality-tier` 提供**质量锚点 + 质量窗口**（§4.4）。它不新增一个 CLI 质量参数，而是给上面三类入口套一层「档位一致性」约束。

### 2.2 换算口径：两张三级，且各自带档位指纹

```
convert_crf.py
  SIZE_MAP      等体积（文件名 log-体积插值）
  QUALITY_MAP   等质量（目标编码器「参数→VMAF」曲线上取等 VMAF 点后最小二乘）★默认
  _ACTIVE_MAP   = {**SIZE_MAP, **QUALITY_MAP}，未覆盖编码器回退 SIZE_MAP
  _EQQUAL_TIER_OVERRIDE  按档覆盖（R1-H4：非 balanced 键初始一律为空 = 未标定，见 §2.4）
三脚本（hwaccel / cpu_v2 / sdr）
  _QP_AFFINE_QUALITY  另一张：constqp 轴用（跨仓契约 CR-4，与 VE 逐条相等）
```

这些表形态一致：`Dict[codec, (a, b, lo, hi)]`，`value = a·x264_crf + b` 夹 `[lo,hi]`，**基准轴恒为 libx264 CRF**。

**现成的"表随组合变"范式**（直接照抄，不要新发明）：`convert_crf.py` 的 `_EQQUAL_SPEED_OVERRIDE`

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
| **`-preset`** | `strategy_preset()`（hwaccel，惰性）/ `default_preset_for()`（hwaccel 亦调用；cpu_v2、sdr 在 main 中**急切填充** ⇒ **必须先取 `preset_explicit` 快照**，§6.2） | **是，最强** | `calibrate_equal_quality.py` 的 `BASE_LOCK` 明文「换挡会整体平移率失真曲线 ⇒ 等效点漂移」；`default_preset_for()` 注释「svtav1 的等效关系是在 `-preset 8` 下标定的」 | **能，但必须按档重标全部受影响的行** |
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
                    'libvpx-vp9': 2, 'libaom-av1': 6, 'librav1e': '6（显式 -speed）',
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
_EQQUAL_TIER_OVERRIDE: Dict[str, Dict[str, Tuple[float, float, int, int]]] = {}   # 缺 = 未标定；R1-H4：非 balanced 键**初始必须为空**（speed/preset 不属于系数表）
```

运行时判定：把**最终生效的组合**算成实际指纹，与本 tier 的登记指纹逐字段比对。**M4（R1）**：指纹按「**最终生效编码器（含降级）** × 该 strategy」定义——降级后（如 `hevc_nvenc`→`libx265`）按落到的软编指纹比对，否则无 GPU 机器上每次运行都会误告警。

- 一致 ⇒ `--crf-ref N` / `--cq-ref N` 的换算有标定保证，静默。
- 不一致 ⇒ **换算回退 `balanced` 表**（R1 定案：统一取 §5.3 的「回退 + 告警」，不再有第二种说法）+ 概览 / `--dry-run` 打**固定文案**（三脚本逐字一致）：
  `⚠ 当前组合偏离 --quality-tier <t> 的标定点（<差异项>）；已回退 balanced 表，--crf-ref/--cq-ref 的跨编码器等效值无标定保证`
- `--strict-calib`（新，默认关）⇒ 不一致时 `exit 2`，首行与上面同一句。

**H4（R1）**：「是否已标定」**只由 `CALIB_FINGERPRINT` 判定**，不得用「`_EQQUAL_TIER_OVERRIDE` 键是否存在」判定；`balanced` 档 `source=None`（直接用 `QUALITY_MAP` 本体）即已标定。

**这就是 tier 的"优先"落点**：不是靠互斥去禁止偏离，而是让偏离自动付出「失去标定保证」的代价。

### 2.5 硬约束（默认档改为 `quality` 之后的重新表述）

默认档**最终**定为 `quality`（阶段一先维持 `balanced`，§12.5），并且 **R1 起窗口/钳制/锚点注入对全档生效（含 `balanced`，无豁免）**。原先那两条"不动"要重新划界——**能守住的守住，守不住的明确登记为契约变更**。

**仍然写死（不因默认档改变而动摇）：**

1. ~~只有 `libx264` 在所有档恒 `medium`~~ —— **R1 作废（§13.2）**：`libx264` **跟随 tier**（用户 10-16 指出并采纳，取代早先的「硬冻结/有条件冻结」两种写法）。基准轴仍是 libx264 CRF（`(1.0, 0.0, 0, 51)` 单位元），但**每档的轴含义 = 该档 libx264 preset** ⇒ 每档一套 `QUALITY_MAP`（按档重标；`balanced`=medium 的既有行仍成立）。未标定档上 `--crf-ref` 的跨编码器换算不可信（概览/`--dry-run` 以指纹告警提示）。`libx265` 随档变化不受影响（`balanced medium` / `quality slow` / `archive slower` / …）。
2. **`NVENC_TO_X264_PRESET` / `X264_TO_NVENC_PRESET` / `X264_TO_SVTAV1_PRESET` / `NVENC_TO_SVTAV1_PRESET` 的取值不动**（CR-1 的**映射表**部分不受影响）。
3. **`_NVENC_DEFAULT_RC`（`-rc vbr`）不动**（CR-2）。
4. **既有 `_v == 0` 无损守卫（`_resolve_quality_params` 顶部）不动** —— `lossless` 档复用它，不新增一套无损路径（§4.4）。**R1-M1**：任何来源（含窗口钳制）归一到 `0` 都必须走该哨兵；`lossless` 档不注入与 constqp 冲突的轴（lookahead/multipass 等），否则 `_ignore` 在 strict 下 `exit 2`。

**守不住、必须登记为契约变更（已按你的选择接受）：**

| 变更 | 原状 | 变更后 | 要对谁说 |
|---|---|---|---|
| 默认质量值 | `DEFAULT_REF = 21`（L388） | **R1 轨迹**：阶段一 = 默认档 `balanced` 的锚点 **`24`** ⇒ 翻档后 = `quality` 的锚点 **`20`**；README/memory 三态都写 | README 默认列、`memory/project_rate_control_params.md` |
| 默认 preset | `p4` / `medium` / `8` | **翻档后**：`p5` / `slow`（libx264 随档）/ `5`；**阶段一默认 balanced ⇒ CR-1 默认条款不动** | **跨仓契约 CR-1 的「默认档」条款**（2026-10-04 与 VE 约定 p4↔medium↔8）⇒ **翻档前需与 VE 同步重议** |
| 默认 lookahead / tune / multipass / aq | 全不下发 | **翻档后**：`250` / `uhq` / `fullres` / on（阶段一仍不下发） | 同上 + README |
| `memory` BASE_LOCK「默认路径永远裸 `-rc vbr -cq N -b:v 0 -preset p4`」 | 成立 | **翻档后失效**，改写为「默认路径 = 默认档基底」；阶段一（balanced）仍裸 `-rc vbr -cq N -b:v 0 -preset p4`，但**质量锚点 24 + 全档钳制**（与 memory 旧文仍有差异，需同步） | `memory/project_rate_control_params.md`、`MEMORY.md` L203 |
| 默认路径是否标定 | 标定（`QUALITY_MAP` 本体） | **阶段一（balanced）仍已标定**；**翻档后未标定**（`quality` 档还没有自己的 `_EQQUAL_TIER_OVERRIDE`）⇒ 默认路径挂告警，直到标定完成 | Part A §2.4 / Part B §B4 |

> ⚠ **R1 追补**：读法 A 下**阶段一也不是零行为变化**——`balanced` 自身会钳制、裸跑锚点 21→24；四份 golden 按「全量重录 + 人工审阅」处理（§7/§13.15 R1-1）。翻档后的「未标定默认路径」仍是真实代价，README 与 help 必须提前说明。

---

## 3. 术语对应（已澄清，不再单列）

你最初列的 `-rate-mode` / `-ref` 指的就是本仓的 `--rc-mode` 与 `--crf-ref` / `--cq-ref`，两者都是本方案里的既有成员，无需再澄清：

| 你的写法 | 本仓写法 | 在本方案里的位置 |
|---|---|---|
| `-rate-mode` | `--rc-mode`（NVENC `-rc`：`constqp`/`vbr`/`cbr`，hwaccel L407 `_RC_MODES`） | §2.3「不碰 rc」+ §4.2「不进 tier」：constqp 与 vbr 已是两套独立的表 + 两套独立的互斥规则（L5852-5867），再叠一档只会炸复杂度 |
| `-ref` | `--crf-ref` / `--cq-ref`（统一基准轴 CLI 入口，L3545-3551）+ `DEFAULT_REF`（质量基准常数） | §2.1「三类质量入口」+ §4.4：它们是 tier 的**服务对象**（换算保证的来源），不是 tier 的成员轴；**R1**：`DEFAULT_REF` 语义迁移为「默认档锚点」——阶段一 = `balanced` 的 `24`，翻档后 = `quality` 的 `20`（§2.5） |

> 附注：本仓确实**没有** ffmpeg 的 `-refs`（DPB 参考帧数）下发。若以后要引入，它是第九条轴且必然破标定 ⇒ 建议走 `--extra-args`，不进 tier。

---

## 4. tier 设计

### 4.1 八档定义（= 唯一权威 `TIER_PROFILES`）

**八档**，`--quality-tier` 默认 **`quality`**（最终态；阶段一先维持 `balanced`，见 §6.2/§12.5）。锚点与窗口**在整数轴 `[0,51]` 上完整覆盖、连续无缝、互不重叠**：

```
[0,0] [1,12] [13,18] [19,22] [23,26] [27,32] [33,40] [41,51]
 0      6      16      20      24      28      36      45     ← 锚点
```

> ✅ **完整覆盖是个好性质**：任意 `-crf/-cq` 整数值**必然属于且只属于一档** ⇒ 「该建议改用哪个档」的答案永远存在且唯一，`min/max` 钳制后不需要处理"无归属"分支，判定代码是纯查表。
> ⚠ 也正因如此，**不存在"不指定档位"的第三种状态**了 —— 这就是 §12.5 那条选择的直接后果；并且 **R1 起窗口/钳制/锚点注入对全档生效（含 `balanced`），无豁免**。

`preset` 列**就是 §13.10R 的矩阵**（= `Accessory/preset_matrix.xlsx`）——同一张表，任何改动必须三处同改。

| tier | 中文定位 | preset（NVENC / libx264 / libx265 / svtav1 / vp9 / aom / rav1e） | lookahead | tune | multipass | aq | `ref_crf` | `ref_window` | 标定状态 |
|---|---|---|---|---|---|---|---|---|---|
| `lossless` | 无损母版 | `p7` / `veryslow` / `slower` / `0` / `0` / `0` / `0` | **不注入** | **不注入** | **不注入** | **不注入** | `0` | `[0, 0]` | ❌ 未标定 |
| `near-lossless` | 近无损母版 | `p7` / `veryslow` / `slower` / `1` / `0` / `1` / `1` | `250` | `uhq` | `fullres` | on | `6` | `[1, 12]` | ❌ 未标定 |
| `archive` | 存档母版 | `p6` / `slower` / `slower` / `3` / `0` / `2` / `2` | `250` | `uhq` | `fullres` | on | `16` | `[13, 18]` | ❌ 未标定 |
| **`quality`（默认）** | 高质量交付（分发母版） | `p5` / `slow` / `slow` / `5` / `1` / `3` / `4` | `250` | `uhq` | `fullres` | on | `20` | `[19, 22]` | ❌ **未标定** |
| `balanced` | 均衡（**= 今天的行为，但不再是默认**；无豁免） | `p4` / `medium` / `medium` / `8` / `2` / `6` / `6` | 不下发 | 不下发 | `auto` | off | `24` | `[23, 26]` | ✅ **已标定**（=今天的 QUALITY_MAP 本体） |
| `preview` | 预览 / 审片 | `p3` / `fast` / `faster` / `9` / `5` / `7` / `7` | 不下发 | `ll` | `disabled` | off | `28` | `[27, 32]` | ❌ 未标定 |
| `draft` | 草稿 / 粗剪 | `p2` / `veryfast` / `veryfast` / `11` / `6` / `8` / `8` | 不下发 | `ll` | `disabled` | off | `36` | `[33, 40]` | ❌ 未标定 |
| `test` | 冒烟测试（只要能解码就行） | `p1` / `ultrafast` / `ultrafast` / `13` / `8` / `8` / `10` | 不下发 | `ll` | `disabled` | off | `45` | `[41, 51]` | ❌ 未标定 |

注：

- **`lossless` 档复用既有 `_v == 0` 无损守卫**（`_resolve_quality_params` 顶部）⇒ 自动走 `-rc constqp -qp 0 -b:v 0`，**不需要 tier 去碰 `--rc-mode`**（守住 §2.5-3）。⚠ **R1-M1**：质量轴旁路 ⇒ **凡与 constqp 冲突的轴一律不注入**（lookahead/multipass 走 `_ignore` 会在 `--fallback-policy strict` 下直接 `exit 2`）；且**任何来源（含窗口钳制）归一到 `0` 都必须复核 `_v == 0` 哨兵**，不允许「钳到 0 后当普通值下发」。`preset` 影响的是无损产物的**体积**而非画质；NVENC 上 `-tune lossless` 是原生取值。
- **`libx264` 跟随 tier**（R1 定案，取代 v3 草稿「恒 medium」，§13.2/§13.10R）⇒ 每档一套标定表；`balanced`=medium 的既有行仍成立。
- **`draft` 与 `test` 有策略层区分**（Q11 已拍板「区分」，R1 补全）：test 比 draft 再快一档 —— NVENC `p1` vs `p2`、libx264 `ultrafast` vs `veryfast`、libx265 `ultrafast` vs `veryfast`、svtav1 `13` vs `11`、rav1e `10` vs `8`、vp9 `8` vs `6`；**aom 例外**：因 `balanced=6` 冻结后快端只剩 `7/8` 两档，test 与 draft 并档为 `8`（§13.10R 注）。
- `tune=ll` 与 `-tune lossless` 都是 NVENC 私有语义，**只有 NVENC 才下发**；降级到 CPU 策略时静默（不告警——否则每条降级策略刷一条）。
- `uhq` 必须过 §4.3 的探测，且**只对 `hevc_nvenc` / `av1_nvenc`** 下发；`h264_nvenc` 落到 `hq`（= 不下发）。
- **`quality` 与 `archive` 的分化 = D1 + D2 同时用**（Q3 已定）：既拉锚点（20 vs 16），也拉 preset（`p5`/`slow`/`5` vs `p6`/`slower`/`3`，libx264 `slow` vs `slower`，vp9 `1` vs `0`，aom `3` vs `2`，rav1e `4` vs `2`）。档位取值必须在 Part B §B5 的 D2 bench 里验证（§12.2 Q3 行）。

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

**R1 补充（M2/M3，tier 基底注入的三条硬规则）：**

- **注入时机在 `apply_rc_control_args` 的 `if lookahead is not None` 判断之前**（lookahead 默认要注入 `250`——若等函数内部再补，会绕过「None ⇒ 不下发」的既有语义）。
- **过滤按「最终生效编码器 × strategy」做**（不是按请求的 codec）：tier 注入的 NVENC 专属轴（tune/multipass/aq/lookahead）在 CPU 策略/降级策略上必须**静默不下发**（与 `tune` 同一原则），否则自动策略下逐策略刷告警、strict 下 `_ignore` 直接 `exit 2`。
- **`'auto'` 值必须编码为 `None`**：`apply_rc_control_args(multipass=None)` 才触发既有 auto-fullres 规则；传字符串会得到非法的 `-multipass auto`。lookahead 的 `250`、tune 的 `ll/uhq` 等同理——只传数值/枚举，不传字符串哨兵。

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
   | `--crf`（字面量） | `to_x264_crf(codec, N)` | `convert_crf.py` 的 `to_x264_crf` |
   | `--cq`（字面量） | `to_x264_crf(src, N)`，`src = src_codec if 支持cq(src_codec) else 'h264_nvenc'`（**R1-M6**：与 `_resolve_quality_params` 的 `_src` 降级换算同口径——`--codec libx264 --cq 23` 今天按 `h264_nvenc` 轴换算 ⇒ `-crf 18`，**不是** 23） | `_resolve_quality_params` 的 `_src` 解析 |
   | `--qp N`（constqp） | `from_constqp_qp(src, N)` —— **R1-M8：三脚本既有该函数（纯代数反函数），无需新增** | `_QP_AFFINE_QUALITY` 反查 |
   | 都没给 | 默认档的 `ref_crf`：**阶段一 = `balanced` ⇒ `24`；翻档后 = `quality` ⇒ `20`**（R1-7；等价于 `--crf-ref 24` / `--crf-ref 20`） | `DEFAULT_REF` 语义迁移为「默认档锚点」，见 §2.5 |

2. **无损哨兵短路**：值为 `0` ⇒ **永不钳制、不换算**，交既有 `_v == 0` 守卫处理（`_resolve_quality_params` 顶部 ⇒ `-rc constqp -qp 0 -b:v 0`）。`--quality-tier lossless`（锚点 0）正是走这条路，所以 **`lossless` 档不需要新代码**，也不需要 tier 去碰 `--rc-mode`。⚠ **R1-M1**：哨兵必须同时覆盖「**钳制到 0**」（如 lossless 窗外的 `--crf 5` 归一后被钳到 0）——不允许钳完当普通值下发；且 `lossless` 档**不注入**与 constqp 冲突的轴（lookahead/multipass 等，§4.1 注），否则 `_ignore` 在 strict 下 `exit 2`。
3. **窗口钳制**：先 `ref_int = round(ref)`（`int (min(max(ref_int, lo), hi))`）。**四个闭区间严格不重叠** ⇒ 任意整数**最多属于一档**，于是：
   - 在窗内 ⇒ 原值通过，无告警；
   - 越界 ⇒ 钳到该侧的窗边界，打**固定告警**（三脚本逐字一致）；`--strict-quality` ⇒ 改 `exit 2`，首行同句；
   - 告警的第③段要给出「该用哪个档」——直接线性扫 `TIER_PROFILES` 找窗内含 `ref_int` 的那档，**因为区间不重叠，答案是唯一的**；八档完整覆盖 `[0,51]` ⇒「都不包含」分支**不存在**（`ref > 32` 的兜底已删）。相比「允许重叠」，不需要任何仲裁规则。
4. **落值**：`from_x264_crf(codec, ref, table=<tier 的活动表>)` ⇒ 该 codec 的原生取值，再交给 `_resolve_quality_params` 原有链路下发。

**五个必须写进 README 与 verify 的样例（R1 新增 `balanced` 行）**

| 情形 | 归一到基准轴 | 窗口判定 | 结果 |
|---|---|---|---|
| `--codec libx265 --quality-tier archive --crf 50` | `to_x264_crf('libx265', 50) ≈ 47.6` → `48` | 越出 `[13, 18]` ⇒ 钳到 `18` | 下发 `-crf 17`（`from_x264_crf ≈ 17.45`），告警见下 |
| `--codec libx265 --quality-tier quality --crf-ref 26` | `26` | 越出 `[19, 22]` ⇒ 钳到 `22`（离 26 最近的窗边界） | 下发 `-crf 22`；告警建议「26 属于 `balanced` 的窗口，请改用 `--quality-tier balanced`」 |
| `--codec libx265 --quality-tier preview --crf-ref 30` | `30` | `30 ∈ [27, 32]` ⇒ 通过 | 下发 `-crf 31`，**不告警**（负向用例：窗内的显式值不受打扰） |
| `--codec libx265 --quality-tier archive --crf-ref 16` | `16` | `16 ∈ [13, 18]` ⇒ 通过 | 下发 `-crf 15`，不告警（正好是该档锚点） |
| `--codec libx265 --quality-tier balanced --crf 50` | `to_x264_crf('libx265', 50) ≈ 47.6` → `48` | 越出 `[23, 26]` ⇒ 钳到 `26` | 下发 `-crf 26`；告警（**R1：`balanced` 不豁免**——CRF 48 落在 `test` 档） |

`archive + --crf 50` 的告警文案（三脚本逐字一致）：
`⚠ --quality-tier archive 的质量窗口为 CRF 13~18；--crf 50（基准轴 ≈47.6）已钳到 18（libx265 -crf 17）。CRF 48 落在 test 档（41~51），请改用 --quality-tier test`

> ⚠ **基线含义已改（R1：H1=读法 A + §12.5「默认档最终 = `quality`」）**：不再存在"不指定档位"的第三种状态，`balanced` 也不再豁免窗口 ⇒ 「默认 ≡ 今天的行为」全面不成立。**R1 做法**：旧 inventory 显式钉上 `--quality-tier balanced`（把"旧默认"转成"balanced 档"用例）＋ **四份 golden 全量重录**（旧字节作废；重录 diff 必须逐条人工审阅）；新 default 基线在翻默认档时另建。详见 §7 与 §13.15（R1-1）。

**⚠ 与既有契约的冲突（必须显式登记，不能悄悄做）**

`convert_crf.py` 的契约句现在承诺：「用户显式给出的同族参数一律原样下发，不换算」。
**R1（读法 A）起这条被 tier 全档接管** —— **含 `balanced`**：显式值先归一到基准轴、过窗口、再按该档的活动表换算回目标编码器（会破旧 baseline，已按「全量重录」登记，§7）。
这个「契约接管」要写进三份 help、README、`memory/project_rate_control_params.md`，否则下一轮审查会当成 bug；实现时同步把契约句改写为「tier 协调前不换算」（§9）。

**概览展示**：`质量: crf-ref 18（钳自 --crf 50）`；三脚本同措辞（进 `verify_overview_lockstep.py`）。

#### 4.4.1 HDR 域 × tier 的复合规则（R1-M5）

`convert_sdr_to_hdr.py` 有独立的 HDR 等质量覆盖表（`set_quality_domain('hdr')`）。tier 与其复合时：

1. **归一**用**该域活动表**的反函数（HDR 域时用 HDR 行；用现成的 `row_limits()` / `forward()` / `inverse()` API，**不要元组手工解包**）；
2. **窗口判定**仍按统一基准轴（libx264 CRF）的 §4.1 窗口——HDR 覆盖表只改「轴 ↔ 原生值」的换算，不改变锚点/窗口语义；
3. **落值**经该域表（`from_x264_crf` 的 HDR 版）。

**范围边界**：HDR 行的**标定不在本方案**（属 SDR2HDR 工作流；「VMAF 在 SDR2HDR 管线不可标定」的结论见 `Plan/Handoff_VMAF在SDR2HDR管线不可标定_VU→eqq标定_20261015.md`）；tier × HDR 实测单独立项（§13.14 ⑤）。

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

> 附带清理一处**风格**不一致（R1 更正：**并非行为 bug**）：hwaccel 用 `CODEC_ALIASES` 归一化（L5845），cpu_v2 同样走 `normalize_codec_name`（等价）；统一到 hwaccel 的写法，新补的 sdr 也用它。

### 5.3 两个 strict 开关，并随 `--fallback-policy strict` 一并生效

| 开关 | 管哪道判定 | 默认 | 默认时的行为 |
|---|---|---|---|
| `--strict-quality` | 质量值越出该档窗口（§4.4 步骤 3） | 关 | 钳制回窗内 + 告警 |
| `--strict-calib` | 编码策略组合偏离该档标定点（§2.4） | 关 | 回退 `balanced` 表 + 告警 |

- 触发时 `exit 2`，首行与对应告警**逐字相同**（hwaccel / sdr 一致）。可以同时给两个。
- **并入 `--fallback-policy strict`**：该值隐含同时打开上面两个开关。这样既保留了「两个开关」的细粒度（可以只要 `--strict-quality`、不要 `--strict-calib`），又不必让 strict 用户再记两个新名字。
  - **R1 更正（读法 A）**：两个新开关默认关；但**不存在「旧用法逐字不变」**——`--fallback-policy strict` 隐含打开两道判定后，今天合法的 `--codec libx265 --crf 20`（归一 ≈20.3，越出 `[23,26]`）会直接 `exit 2`，`--crf 50` 之类同理。旧 baseline 已按此语义**全量重录**（§7）；help/README 与 `--strict-quality` 文案同步说明。
- **cpu_v2 例外**：cpu_v2 没有 `--fallback-policy`（L867 / L1287 / L1495），也不提供这两个开关——cpu_v2 是 hwaccel 的 CPU 简板，不必强行对称 ⇒ cpu_v2 里两道判定**恒为告警**，永远不升级为 `exit 2`。登记为第 5 项有意不对称（§8-5）。

---

## 6. 逐文件实施清单

### 6.1 阶段零：先修既有 bug（与 tier 正交，零字节影响）—— **✅ 已完成（994bee1，2026-10-16）**

| # | 文件:行 | 内容 |
|---|---|---|
| Z1 | `vidcrop_hwaccel.py` L5134 附近 | `build_ffmpeg_cmd` 是**裸调**（本循环内唯一有 try 的是 L5078 的 `build_video_filter`），`main()` 的 try（L6238）只捕 `KeyboardInterrupt`（L6339）⇒ `--fallback-policy strict` 下 `_ignore` 抛的 ValueError 会直接 traceback。补 `try/except ValueError` → 可读报错 + `return 2`。**sdr 已有**（L4726-4733），cpu_v2 无 strict 分支故不适用 ⇒ 这是三向不对称，不是 hwaccel 独有设计 |
| Z2 | `convert_sdr_to_hdr.py` | 补 CLI 层 `--nvenc-tune uhq` + `h264_nvenc` 硬报错，与另两个同首行（§5.2） |
| Z3 | `README.md` L335 | cpu_v2 参数表里的 `--nvenc-aq` 行删除或移表（cpu_v2 代码该词 0 命中） |
| Z4 | `README.md` L394 | 「语义同 `vidcrop_hwaccel.py`」→ `vidcrop_cpu_v2.py`（hwaccel 表里自称同 hwaccel 的复制残留） |
| Z5 | `README.md` L88-93 | 功能矩阵补 `--nvenc-tune` / `--nvenc-multipass` 行（加 tier 后更必要） |

> 阶段零已完成：Z1（hwaccel 两处 `try/except ValueError`）、Z2（sdr 补 `uhq`+`h264_nvenc` 硬报错）、Z3~Z5（README 三处）。行进记录见 §12.7。

### 6.15 阶段零点五：本地 CPU 的 D2 预跑 —— **✅ 已完成（证据见 §12.7）**

你的 Q3 回答要求「保持 D1+D2，且立刻把 svtav1 / x265 的 D2 部分先在本地跑一轮实测」。这一小步**不依赖任何 tier 代码**（纯 ffmpeg A/B），所以它**不用等阶段一**，现在就能开跑：

| 组合 | A 侧 | B 侧 | 在哪跑 |
|---|---|---|---|
| x265 | `libx265 -preset slow` | `libx265 -preset slower` | 本机 |
| svtav1 | `libsvtav1 -preset 6` | `libsvtav1 -preset 4` | 本机（**R1 注**：权威档值对已改为 `5`↔`3`，原理证据复用；CPU 环按新对重跑） |

- 素材与口径**直接沿用 Part B §B2**（17 条 eqq 池、锚点 18/21/24/27/30、`n_subsample=1`、720p prep）⇒ 结果与后续三环可直接拼接，不用重跑。
- 指标：**等 VMAF 码率比** + **Δ耗时**（定义见 Part B §B3）。
- 产物先落 `Accessory/calib/eqq_evidence/q3_bench/cpu_early/`，回归后并入 Part B §B6 的正式目录。
- **它的唯一目的是给 §12.3 的 Q3=D1+D2 决策兜底**：若判定 D2 不划算，回退动作只是改 `TIER_PROFILES` 常量，不动任何代码（这是当初选「留在常量层」的原因）。
- **R1 结论（已完成）**：D2 原理成立 —— svtav1 `6→4` **+19.6%** 码率收益、**1.68×** CPU；x265 `slow→slower` **+10.5%**、**4.67×** CPU（详见 §12.7/§13.11）。Q3=D1+D2 保留；CPU 环按 §13.10R 的终值对（svtav1 `5↔3`）复核。

### 6.2 阶段一：**把编码全部做完**（Q6：先完成编码，标定后置；R1：与 harness 标定可并行）

> **R1 时序（H3）**：标定不再等阶段一——harness 的 `--preset-override` **已落地**（2026-10-17）⇒ CPU→T4→L40 标定环即可开跑（§6.3~6.6）；阶段一的 CLI 实现与之并行，**CLI 端到端（dry-run/告警/窗口/指纹）是阶段一的 DoD**，不是标定的前置条件。

标定点一律先空着由数据结构自带「未标定」，不在这一阶段做任何实测。

| 文件 | 改动 |
|---|---|
| `convert_crf.py` | 新增 `_EQQUAL_TIER_OVERRIDE`（照抄 `_EQQUAL_SPEED_OVERRIDE` 范式；**R1-H4：非 `balanced` 键一律空 dict**——speed/preset 值属于 `TIER_PROFILES`/§13.10R，不进系数表）+ `set_quality_tier()` + `get_quality_map()`（按 tier 查、缺则回退 `QUALITY_MAP` 本体）+ 回调告知；新增 `CALIB_FINGERPRINT`（**M4**：按「最终生效编码器（含降级）× strategy」定义）。**初值全空** ⇒ 非 balanced 自动落"未标定回退"，不靠人记 |
| `vidcrop_hwaccel.py` | ① 新增 `--quality-tier`（`default='balanced'`，choices 用独立常量）+ `--strict-quality` / `--strict-calib`，并让 `--fallback-policy strict` 隐含打开后两者（§5.3）；② `TIER_PROFILES` 常量表（含 `ref_crf` / `ref_window`；**preset 列 = §13.10R**，§4.1）；③ **preset 已是惰性**（`args.preset` 保持 `None`）⇒ 只需把 `strategy_preset()` 里的默认改为 tier 感知；④ lookahead 默认值插在 `apply_rc_control_args` 的 `if lookahead is not None` **之前**（**M2**：过滤按「最终生效编码器 × strategy」，aq/multipass/lookahead/tune 一并纳入）；⑤ tune/multipass/aq 按 tier 基底（**M3**：`'auto'` 必须编码为 `None`）；⑥ `_tune_tries = [派生值, None]`（仅派生值启用）；⑦ **质量锚点/窗口**：给 `_resolve_quality_params` 加 `tier: str = 'balanced'` 参数并在**函数顶部**做 §4.4 的四步流程（**M1**：钳到 0 也走无损哨兵；**M6**：`--cq` 按 `_src` 口径归一；**M8**：`--qp` 用既有 `from_constqp_qp`，**无需新反函数**）；⑧ 概览补 `tier=` / `质量:` / `tune=` / `multipass=` / `AQ`（向 sdr 措辞看齐）+ 窗口告警 + 指纹告警 |
| `vidcrop_cpu_v2.py` | 同上，但：① **必须先在 `main()` 里捕获 `preset_explicit = args.preset is not None` 快照**（急切填充后分不出显式与否）——hwaccel 不需要、cpu_v2/sdr 必需；② **不加 `--nvenc-aq`**；③ **auto/tier 不派生 `uhq`**（§8-2）；④ 无 `_tune_tries`；⑤ **不提供 `--strict-quality` / `--strict-calib`，也无 `--fallback-policy`**（§8-5）⇒ 两道判定恒为告警 |
| `convert_sdr_to_hdr.py` | 同上（同样需要 `preset_explicit` 快照；另有 dataclass 默认需一并处理）；ladder 插「去 tune 重试」一级 + 同步更新 docstring 的阶梯编号 |
| 三份 help / README / `memory/project_rate_control_params.md` | 写清：tier = 策略基底 × 质量锚点/窗口 × 标定指纹，**codec 是输入不是成员**（§4.2）；质量数值**在档内**（§4.4）以及「契约接管（**全档，含 balanced**）」那一条；默认质量值轨迹 **`21 → 24`（阶段一）→ `20`（翻档）**（R1-7）；非 balanced 未标定（§2.4）；cpu_v2 四条有意不对称（§8） |

**阶段一的额外任务（R1 重排）：**

- **baseline 改造（读法 A）**：旧 inventory（`dump_cmd_*.sh`）里的默认调用**全部显式钉上 `--quality-tier balanced`**（把"旧默认"转成"balanced 档"用例，避免翻默认档时二次漂移）＋ **四份 golden 全量重录**（旧字节作废：balanced 现在也钳制、裸跑锚点 21→24）；重录 diff 逐条人工审阅后提交；翻默认档时另建 default（=`quality`）新基线（§7）。
- **`DEFAULT_REF` 语义迁移**（§2.5 / R1-7）：常量保留为单一来源，取值改为引用默认档锚点 `TIER_PROFILES[DEFAULT_TIER]['ref_crf']`（阶段一 = `balanced` 的 **24**；翻档后 = `quality` 的 **20**），不要两处写死。

**阶段一的完成判据（R1 重写）：**
1. 重录后的四道孪生门**逐 token 全绿**（新 goldens = 阶段一语义下的平衡档黄金文件；重录 diff 有人工审阅记录）；
2. 默认路径 `--dry-run` 与 §4.1/§4.4 表格人工核对一致（含 `balanced` 钳制样例）；
3. 八档均可 `--dry-run` 核对；§4.4 样例表（五个）全部成立；
4. `lossless` 档确认落到 `-rc constqp -qp 0 -b:v 0`（复用既有守卫；**钳到 0 的路径也走哨兵**，M1）；
5. `TIER_PROFILES` 结构自检（八档覆盖 `[0,51]` + 互斥 + preset 合法性）在 `verify_quality_mapping.py` 通过。

> 阶段一之后 `_EQQUAL_TIER_OVERRIDE` 仍全空 ⇒ 非 balanced 档全部处于「未标定」告警状态。**这是预期状态，不是缺陷**——没有实测数据之前，宁可显式告知也不假装标定过。

### 6.3～6.6 标定环（R1 重排：**harness 先行，与阶段一并行**）：按 **CPU → T4 → L40** 循序渐进实测补标定（Q7）

严格按「软→硬」推进：每一环都复用上一环确认过的素材池、锚点与口径，只有**被测编码器 / 机器**在变，这样能把手滑/口径漂移隔离出来。**R1 起主测走 harness**（`Accessory/probe/calibrate_equal_quality.py`），不依赖生产 CLI（H3）；生产 CLI 的端到端验证是阶段一的 DoD。

| 阶段 | 内容 | 前置 / 依赖（R1 更新） |
|---|---|---|
| **阶段二（已落盘）** | 《`--quality-tier` 全流程实测方案》已落盘为独立文档：`Plan/VidUtils_quality-tier全流程实测方案_v1_20261015.md`（= Part B） | — |
| **阶段三** | **CPU 环**（本机可做，无需 GPU）：`libx264` / `libx265` / `libvpx-vp9` / `libaom-av1` / `libsvtav1` / `librav1e` 各档实测（`quality`/`archive` 优先）；per-tier `_EQQUAL_TIER_OVERRIDE` 落表 | **✅ `--preset-override` 已落地（2026-10-17，R1 第一块砖）；`--tag` 已有** |
| **阶段四** | **T4 环**：`h264_nvenc` / `hevc_nvenc` 各档实测 + `p5/p6/p7` 可用性 + `_NVENC_PRESET_RETRY` 触发率 | 阶段三的 harness 与产物格式定型 |
| **阶段五** | **L40 环**：`av1_nvenc` 实测（`av1` 走 `--expect-av1` fail-fast；注意 Turing 代无 AV1 编码单元，`Accessory/probe/verify_nvenc_quality_gpu.py` 已记此约束） | 阶段四的 NVENC 流程已跑通 |

每一环的共同要求：

- harness `Accessory/probe/calibrate_equal_quality.py` 的 `--preset-override` ✅ **已落地**（2026-10-17；替换 `BASE_LOCK` 里的 medium/8，按 §13.10R 的档值传入）+ `--tag <tier>`（已有）；产物落 `Accessory/calib/eqq_evidence/<tier>_*/`（§B6）。**本环现已解锁，可直接开跑**。
- 门禁先过 **LOO**（`loo_equal_quality.py`：软编 ≤5.9、rav1e ≤7.5；`GATE_ANCHORS=(0,27)`），**不要**只看 `verify_equal_quality.py` 的 `TOL_VMAF=1.0`——README 自己写明它是 in-sample 回归门，「不能为表值背书」。
- **锚点扩展（R1-M7）**：`ANCHOR_CRFS=[18,21,24,27,30]` 只覆盖中段；`draft(36)/test(45)`、`near-lossless(6)/lossless(0)` 需扩锚（建议 +`[0,6,33,39,45]`），否则这些档**要么不标定（打「未标定」）、要么明确 LOO 放宽**——二选一写进报告，不默认。
- **NVENC 环必须同步重标 `_QP_AFFINE_QUALITY`**——CR-4 跨仓契约，⑨ 组断言逐条相等，只改 `QUALITY_MAP` 不改它会红。
- 每落一档或一个编码器，填 `_EQQUAL_TIER_OVERRIDE[tier]` + 翻 `CALIB_FINGERPRINT[tier]['calibrated']`（**按生效编码器 × strategy，M4**）+ 写 `evidence` ⇒ 对应告警自动消失。换 ffmpeg 构建/驱动须重标。
- **本机无法验 NVENC**：`verify_equal_quality.py` 在无 GPU 机器上 NVENC 三档全 SKIP ⇒ 阶段四/五必须在有卡机器上补跑，不能靠本机 CI 假装通过。

---

## 7. 测试与门禁

| 测试 | 改动 |
|---|---|
| **四道孪生门**（`dump_cmd_default.sh` / `dump_filter_chains.sh` / `dump_enc_options.sh` / `dump_cmd_full.sh`） | **R1（读法 A）**：旧 baseline 的「一行不改」不再成立。做法：旧 inventory 里的默认调用**全部显式加 `--quality-tier balanced`**（钉档、防翻默认档二次漂移）；**四份 golden 全量重录**，重录 diff 逐条人工审阅后提交；翻默认档时（§12.5 第二步）另建 default 新基线。**这两份一起构成阶段一的总闸** |
| 同上 | 新增 `--quality-tier lossless` 一格：确认落到 `-rc constqp -qp 0 -b:v 0`；新增 `--quality-tier lossless --crf 5` 一格（**R1-M1**：归一后钳到 0，也必须走哨兵、不得当普通值下发） |
| `Accessory/test/baseline/enc_before.txt` L12 | **R1：到期重录**（唯一活的 NVENC 格；`-cq 20` 归一 ≈16.05，越出 balanced 窗 `[23,26]` ⇒ 会钳到 23 并在 §4.4 语义下带走告警）。**重录 diff 必须逐条人工审阅**；cpu_v2 不派生 uhq 的承诺不变（那是另一维度） |
| `verify_quality_mapping.py` | **新增窗口钳制组**：`archive` + `--crf 50`（libx265）→ 下发 `-crf 17` 且带告警；**`balanced` + `--crf 50` → 钳到 `-crf 26` 且带告警（R1：balanced 不豁免）**；`preview` + `--crf-ref 30` → **通过且不告警**（负向用例：窗内的显式值不受打扰）；`--qp 0` 无损哨兵**不被钳**、**M1**：钳到 0 走哨兵；**M6**：`--codec libx264 --cq 23` 与降级换算同口径；**M8**：`from_constqp_qp` 与 `to_constqp_qp` 往返一致；八档 `ref_crf` 在无质量参数时正确注入；**新增对 `TIER_PROFILES` 的结构自检**：八档区间完整覆盖 `[0,51]` 且互不相交（改表红线由机器把守） |
| `verify_cli_parsing.py` | `--quality-tier bogus` 被 choices 拒绝；`--quality-tier archive --crf 50` 走真 CLI subprocess，断言 rc 与**报错/告警首行**三脚本逐字相等；`--strict-quality`（以及隐含打开它的 `--fallback-policy strict`）下同一组合 exit 2 |
| `verify_rc_lookahead.py` ⑩ F | 现有「默认不下发 `-tune`/`-multipass`」一条改为断言 `tier=balanced`；新增：各档 preset/lookahead/tune/multipass/aq 基底值（preset 列 = §13.10R）；显式覆盖 tier 的行为；`preview` 在非 NVENC 上静默不告警；cpu_v2 的 `quality` 档**不下发 tune**（有意不对称，负向对照） |
| `verify_rc_lookahead.py` ⑩ E（L303-309） | 现有 hwaccel aq；补 sdr；cpu_v2 断言「无该参数、不进命令」 |
| `verify_overview_lockstep.py` | `fields()`+`field_diff()` 比概览字段**序列**（顺序算差异）⇒ tier 行 + 指纹告警文案三脚本必须逐字一致、非 NVENC 不出现。沿用其已有 `--selftest`（4 格：判词装置正/负/空）给新行加负向格 |
| `verify_equal_quality.py` | 不碰 `-tune`，只吃 QUALITY_MAP ⇒ tier 一旦改变活动表，这里是回归面。**注意 L43-47 NVENC 三档在本机（无 GPU）全 SKIP** ⇒ 本机跑不出 GPU 侧的回归，阶段四（T4）/ 阶段五（L40）必须在有卡机器上补跑 |
| `check_readme_refs.sh` | 新增任何 verify/test 文件必须同步 README（漏登 exit 1，空集 exit 2） |
| `Plan/ffmpeg_nvenc_knowledge.md` §5.2 | 「默认路径保持裸 `-rc vbr -cq N -b:v 0 -preset p4`」改写为 tier × 默认矩阵，并写明 QUALITY_MAP 的标定点 = `balanced` |
| `memory/project_rate_control_params.md` + `MEMORY.md` L203 | L203 现在写着「不引入 `-tune hq`/`-multipass fullres」⇒ 必须同步；**默认质量值轨迹 `21 → 24（阶段一）→ 20（翻档）`（R1-7）** 一并写入 |

> **关于 SABOTAGE**：v2 §11 的 DoD 写「⑩ F 组含 SABOTAGE 负向对照」，但 verify 目录**没有** SABOTAGE 机制（只有 `dump_cmd_full.sh:65` 有）。本版改为沿用 `verify_overview_lockstep.py` 已有的 `--selftest` 形态，成本更低且已有先例。

---

## 8. 不对称登记（6 项：前 5 项**有意**保留；第 6 项 **已由阶段零 Z1 修复**（994bee1）；全部写进 `apply_rc_control_args` 常量区注释 + README 新小节 + 同一条 memory）

1. **cpu_v2 不收 `--nvenc-aq`** — README L92 v2 列保持 ❌。理由：纯 CPU 孪生脚本，该轴只在 `--codec *_nvenc` 透传路上有意义。需要的人走 `--extra-args -- -spatial-aq 1 -temporal-aq 1`。
2. **cpu_v2 的 tier 不派生 `uhq`** — 理由不是"无探测"（它有），而是**无 fallback ladder**（L867/L1287/L1495）⇒ 派生值踩雷只能硬失败。措辞沿用 `--scale-algo cuda-*` 的那套（README L1562「v2 **拒绝** cuda-*，这是有意的两脚本不对称」+ cpu_v2 L2837-2844「这份串**故意不列 cuda**」）。
3. **hwaccel 的 `--nvenc-aq` 走策略层（L4467）、不走 `apply_rc_control_args`**（sdr 走后者，签名有 `aq`）— 历史成因，tier 的 aq 项需两个不同插点，登记防下一轮审查报"漏移植"。
4. **libx264 跟随 tier**（R1 定案，§13.2）— 不再是「恒 `medium`」；每档一套标定表（按档重标），`balanced`=medium 的既有行仍成立。登记为**有意成本**而非遗漏。
5. **cpu_v2 不提供 `--strict-quality` / `--strict-calib`，也没有 `--fallback-policy`**（§5.3）⇒ 它的两道 tier 判定**恒为告警**，永不升级为 `exit 2`。理由：cpu_v2 是 hwaccel 的 CPU 简板，不是它的镜像，不必强行对称（与既有的「cpu_v2 没有 fallback ladder」同源）。
6. ⚠ **非有意**：hwaccel 的 `build_ffmpeg_cmd` 裸调导致 `--fallback-policy strict` 下 traceback，而 sdr 的 `build_plan` 已有 try/except（L4726-4733）、cpu_v2 无 strict 分支 ⇒ 三向不一致。**已由阶段零 Z1 修复（994bee1）**，保留登记供审查对照。

---

## 9. 明确不改

`DEFAULT_REF` 作为**常量名与单一来源**保留，取值随默认档迁移（阶段一 `24` → 翻档后 `20`；不再写死 21，**R1-7**）；`NVENC_TO_X264_PRESET` / `X264_TO_NVENC_PRESET` / `X264_TO_SVTAV1_PRESET` / `NVENC_TO_SVTAV1_PRESET` 的取值（CR-1）；`_NVENC_DEFAULT_RC`（`-rc vbr`，CR-2）；`SIZE_MAP` / `QUALITY_MAP` 的现有行（非 balanced 档只**新增** `_EQQUAL_TIER_OVERRIDE`，不覆盖）；`_QP_AFFINE_QUALITY` 的现有值（要到阶段四 / 阶段五的 NVENC 环才按档重标）；`_NVENC_TUNE_VALUES` / `_NVENC_MULTIPASS_VALUES` / `_NVENC_UHQ_CODECS` 本身（**只新建 `__CHOICES` 派生常量**，§0-5）；现有五条互斥规则（§5.2）；`--rc-mode` / `--bitrate` 不进 tier（§4.2）；**R1 改写两处**：① `convert_crf.py` 的「显式值原样下发不换算」契约**在档内被接管（全档，含 `balanced`）**（§4.4）——实现时同步改写为「tier 协调前不换算」，旧 baseline 已改为全量重录；② `_EQQUAL_TIER_OVERRIDE` 非 balanced 键**初始必须为空**（H4）。既有 `_v == 0` 无损守卫不改（`lossless` 档复用它；**M1**：钳到 0 也走它）。

---

## 10. 风险

| 风险 | 缓解 |
|---|---|
| `uhq` 在 hevc/av1 上的收益**零实测** | 阶段一不做 `auto→uhq`，只在 `quality`/`archive` 里；`--strict-calib` 让用户显式承担未标定；要到阶段四（T4）/ 阶段五（L40）实测出数据后，才决定是否把某档提升为默认 |
| 旧驱动不认 `uhq`（rc≠0） | 探测（§4.3）+ `_tune_tries` 参数级降档，仅 tier 派生值启用 |
| 4K 下 `uhq` 显存/耗时 | 概览 + help 提示；`--nvenc-tune hq` 是本档内的回退开关 |
| cpu_v2 无兜底却拿到激进组合 | 有意不对称 8-2 + `preset_explicit` 快照保证「用户显式给的 preset/轴」不被 tier 覆盖（**R1 注意**：裸跑**质量锚点** 21→24 属既定变更，不在该快照的保证范围） |
| 重标定工作量爆炸（多编码器 × 8 档 × GPU/CPU；**R1：libx264 也随档 ⇒ 加一列**） | `_EQQUAL_TIER_OVERRIDE` 缺值 ⇒ 自动回退 balanced 表并告警，未标定由数据结构自带，允许分档、分编码器逐步落地（balanced 已标定 = 安全网） |
| `--quality-tier balanced` 破坏既有基线 | **R1 已接受此破坏**：改为「旧 inventory 钉档 + 四份 golden 全量重录 + 人工审阅 diff」（§7/§11）；重录后四道门必须全绿 |
| **钳制让用户困惑**（"我明明给了 50"） | 告警必须写出「钳到多少 + 目标编码器实际下发多少 + 想低质量该用哪个档」三段信息，不能只说"已钳制"；概览与 `--dry-run` 同文案 |
| **契约接管**（§4.4）被当成 bug 报回来 | 三份 help 第一句就写「**全档**（含 `balanced`）会对显式质量值做钳制与按档换算；默认质量值轨迹 `21 → 24 → 20`」；README 参数表 + memory 同步；**R1 起不存在"零感知"路径**（已登记为主动接受的代价） |
| constqp 反函数引错刻度（`_QP_AFFINE_QUALITY` vs `_QP_SCALE`×3） | 正向函数在哪就用哪张表的反函数（`quality` 口径用仿射、`size` 口径用 ×3）；往返一致性进 `verify_quality_mapping.py` |
| **跨仓契约 CR-1 的「默认档」条款被改**（**翻档后** p4→p5 / medium→slow / 8→5；libx264 亦随档） | Video_Enhancement 侧已按 p4↔medium↔8 对齐，**必须与 VE 同步重议**（R1 时序：§13.15 R1-3）。**分两步提交**：阶段一先只加 tier 能力（默认仍 `balanced`，CR-1 默认条款不动），VE 确认后再翻默认值 |
| **翻档后默认路径变成未标定**（`quality` 档还没有 `_EQQUAL_TIER_OVERRIDE`）⇒ 默认跑起来就带告警（阶段一 default=balanced 已标定，无此问题） | 这是诚实的代价（§2.4 / Part B §B4），不是 bug；但必须在三份 help 与 README 首屏讲清，否则用户会以为工具坏了；§B4 完成后告警自动消失 |
| **八档 × (8~9 编码器) 标定工作量爆炸** | `_EQQUAL_TIER_OVERRIDE` 缺键即自动回退 `balanced` 表 ⇒ 允许分档、分编码器逐步落地；`lossless`/`near-lossless`/`draft`/`test` 四档**优先级最低**，且它们的 LOO 门禁应单独放宽（超低画质区 VMAF 反查条件数本来就差，见 §B7「LOO 超门禁禁止落表」） |
| 日后改表把区间改成有缝或有重叠 | `verify_quality_mapping.py` 的结构自检（覆盖性 + 互斥性）直接对 `TIER_PROFILES` 断言 |
| **极限档锚点超出 B2 锚点区**（R1-M7） | `draft`36 / `test`45 / `near-lossless`6 / `lossless`0 在 `[18,30]` 之外 ⇒ 先定扩锚（建议 +`0/6/33/39/45`）；若不扩，则这四档「不标定、LOO 放宽」并写入报告（§6.3~6.6 / Part B B2） |
| **`TIER_PROFILES` 单源漂移**（代码常量 ↔ §4.1/§13.10R ↔ `preset_matrix.xlsx` 三处不一致） | `verify_quality_mapping.py` 结构自检 + 「改表三处同改」流程约束（§13.10R）；R1 已据此驳回四处旧值 |
| **HDR 域 × tier 复合未做实测** | R1 已明确复合规则（§4.4.1）与**范围边界**：HDR 行标定不在本方案（属 SDR2HDR 工作流，见 Handoff 文档 + Part B 顶部范围声明） |

---

## 11. DoD

- 四份 baseline：旧 inventory 显式钉 `--quality-tier balanced`，**golden 全量重录 + diff 人工审阅通过**（R1 读法 A；不再要求旧字节 diff 为零）；翻默认档后 default 新基线另建可用；
- 八档的 `--dry-run` 可按 §4.1 表格人工核对（preset 列 = §13.10R）；非 balanced 且未标定时，三脚本打出**逐字一致**的告警；
- §4.4 的五个样例（archive+crf50 钳制、quality+crf-ref26 钳制、**balanced+crf50 钳制〔R1 新增〕**、preview+crf-ref30 通过、archive+crf-ref16 通过）在 `verify_quality_mapping.py` / `verify_cli_parsing.py` 里双向成立；`--qp 0` 与「钳到 0」都走无损哨兵（M1）；`--cq` 归一与 `_src` 同口径（M6）；`from_constqp_qp` 往返一致（M8）；
- `--strict-quality` 在窗口越界时、`--strict-calib` 在标定失配时各自 exit 2，首行与对应告警同句；`--fallback-policy strict` 隐含同时打开两者；cpu_v2 无此三开关，恒为告警；
- 现有五条互斥规则 + Z1~Z5 全部就位（**Z 已完成，994bee1**）；`TIER_PROFILES` 结构自检通过；`_EQQUAL_TIER_OVERRIDE` 非 balanced 键为空、`CALIB_FINGERPRINT` 按生效编码器 × strategy（M4）；README / `Plan/ffmpeg_nvenc_knowledge.md` / `memory/` 三处描述与代码一致（含默认质量值轨迹 21→24→20）。

---

## 12. 待确定问题（Open Questions）

Q1~Q10 **全部已拍板**（12.1 / 12.2 / 12.3），12.5 的阻塞项也已拍板（**选 2：默认档 = `quality`**）。**12.6 是新增的三条待确认**。未拍板的条目一律按「占位值 + 显式注释」落地，不得默默取定。

### 12.1 已拍板 —— 档位数值（Q1 / Q2 / Q4 / Q5）

| # | 结论 | 落点 |
|---|---|---|
| Q1 | **八档锚点（终值）**：`lossless 0` / `near-lossless 6` / `archive 16` / **`quality 20`（默认档）** / `balanced 24` / `preview 28` / `draft 36` / `test 45`。修订原因：原 `balanced` 锚点 21 落在 `quality` 窗口 (14,24) 内 ⇒ 语义自相矛盾；本次一并补齐高端（无损/近无损）与低端（draft/test） | §4.1 |
| Q2 | **八档窗口（终值）**：`[0,0]` / `[1,12]` / **`[13,18]`** / `[19,22]` / `[23,26]` / `[27,32]` / `[33,40]` / `[41,51]` —— 在整数轴 `[0,51]` 上**完整覆盖、无缝、互不重叠**。判窗前 `round` 成整数 ⇒ 任意整数**必然属于且只属于一档**，「该改用哪个档」恒有唯一答案，且不需要"无归属"兜底分支 | §4.1 / §4.4 |
| Q1b | **`--crf-ref` 默认随默认档锚点（R1-7）**：阶段一 = `balanced` 的 **`24`**（旧 21 的直接后果，已接受）→ 翻档后 = `quality` 的 **`20`** | §4.1 / §12.5 |
| Q4 | svtav1 preset 终值 = §13.10R（`13/11/9/8/5/3/1/0`；**R1 取代占位 `12/6/4`**） | §4.1 / §13.10R |
| Q5 | `preview` 的 NVENC preset —— **被 R1 矩阵取代**：`preview=p3`、`draft=p2`、`test=p1`（八档化后的完整梯） | §13.10R |

### 12.2 已拍板 —— 设计与分期（Q6 ~ Q10）

| # | 结论 | 落点 |
|---|---|---|
| Q6 | **先把编码全部做完，实测标定后置**（**R1-H3：与 harness 标定可并行**）；**M8 更正：反函数已存在**（`from_constqp_qp`，三脚本），阶段一只加与其往返一致性断言 | §6.2 |
| Q7 | **先软后硬**，严格按 **CPU → T4 → L40** 循序渐进；每一环只换「被测编码器 / 机器」，素材池、锚点、口径全部沿用上一环 ⇒ 把手滑与口径漂移隔离出来 | §6.3~6.6 |
| Q8 | **`--strict-*` 并入 `--fallback-policy strict`**（该值隐含打开两者）；cpu_v2 是 hwaccel 的 CPU 简板，**不提供这两个开关、也没有 `--fallback-policy`** ⇒ 它的两道判定恒为告警 | §5.3 / §8-5 |
| Q9 | **拆成两个开关**：`--strict-quality`（窗口越界）/ `--strict-calib`（标定失配），各自默认关，可单独给 | §5.3 |
| Q10 | **`--threads` 与 tier 无关，不进 tier** | §4.2 |
| **Q3-落地** | **保持 D1+D2**（不退回只靠 D1），并且**已在本机先跑一轮 CPU 侧 D2 实测**（x265 `slow`↔`slower`、svtav1 `6`↔`4`；**R1 注**：权威档值对 svtav1 为 `5`↔`3`，CPU 环按新对复核）。回退路径留在常量层：若判定 D2 不划算，只改 `TIER_PROFILES`，不动代码 | §6.15 / §12.3 |

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
| **D2 preset** | **为逼近这个点肯花多少搜索代价**：压缩效率 ↔ 速度 | 「同样的 95 分，用 10 分钟慢慢压还是 1 分钟快速压」——同价位换更好的性价比 | `-preset P` | `p5` | `p6` |
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

**✅ Q3 已拍板：采用 D1 + D2 同时分化** —— `quality` / `archive` 既拉锚点（20 vs 16，差 4 档），也拉 preset（`p5`/`slow`/`5` vs `p6`/`slower`/`3`；libx264 `slow` vs `slower`；vp9 `1` vs `0`；aom `3` vs `2`；rav1e `4` vs `2`，R1 终值见 §13.10R）。
代价与连带：①两档各要一套 `_EQQUAL_TIER_OVERRIDE`（D2 移动了率失真曲线，不能共用 `balanced` 的表）；②svtav1 的 `5`/`3`（R1 终值）不是"可选占位"，而是**已承诺的档位取值**，必须进 Part B 的 D2 bench 验证；③若 §B5 bench 判定 D2 收益不划算，则退回只靠 D1 —— 这条回退路径要在实现里留成常量改动而非代码改动。

~~推荐：现阶段只承诺 D1~~（已被上述决定取代，保留此句仅说明决策变更轨迹。）

> ⚠ **更正（你指出的那一条）**：我在上一版把 `_NVENC_PRESET_RETRY = {'p5':'p4','p6':'p4','p7':'p4'}`（hwaccel L451-455）当作「p6/p7 已知偶发初始化失败」的**反面证据**，这是**过度解读**，已撤回。
> 该常量的真实来源是注释自己写的「**[借鉴1]** …对照 Video_Enhancement 的 SDK 路径——`InitializeEncoder` 返回 code=8（驱动不认该 preset）时降到 p4 重试一次」，并且**本仓实测过的只有一句**：「**p4 是实测在 T4 / 旧驱动上被接受的安全档**」。全仓另有检索（Plan / memory）**没有任何 p5/p6/p7 失败率的实测记录**。
> ⇒ 正确表述是：**p6/p7 的可靠性未知**，它是一条从 VE 借来的防御性兜底，不是本仓验证过的失败率。它既不能当作「p6/p7 不稳」的证据，也不能当作「p6/p7 稳定」的证据 —— **必须靠 Part B 实测量化**（见 Part B §5 的 Q3 专项 bench，把 `_NVENC_PRESET_RETRY` 触发率直接作为测量项）。
### 12.4 历史残留（R1 清理）

- 原「档位命名已定：`preview` / `balanced` / `quality` / `archive`（4 档，精简版）」**作废**——已扩为八档（§4.1），本节不再承载需求；更细的要求已并入 §13 各节与 §12.6。

### 12.5 ✅ 已拍板 —— 「默认档 = `quality`」（选 2，含扩到八档）

**起因**：把窗口改成严格分区后，`balanced` 从「全轴放行」变成了一个**有边界的真实档位**（`[23,26]`）；再加上默认 ref 由 21 改为 20，产生了两条连锁后果：

1. **旧的守基线负向用例失效**：`--quality-tier balanced --crf 50` 从「一字不改」变成「被钳到 `-crf 26`」。`balanced` 不再等价于"不指定档位"。
2. **默认档 = `quality`**（20 正是 `quality` 的锚点）⇒ **默认行为整体改变**，连带以下契约要重议：

> **R1 追补（2026-10-17）**：H1 拍板**读法 A** —— `balanced` 不再豁免窗口/钳制/锚点注入（`--quality-tier balanced --crf 50` → `-crf 26`），也不再等价于"不指定档位"；旧 baseline 按「钉档 + 全量重录」处理（§7）。下列两条后果**全量成立、无例外**；并且**阶段一（默认仍 balanced）也不是零行为变化**。

| 受影响项 | 现状 | 默认档改成 `quality` 后 |
|---|---|---|
| `--crf-ref` / `DEFAULT_REF` 默认质量 | `21`（`DEFAULT_REF` 常量；`default_quality_for()`） | **R1 轨迹**：阶段一 **`24`**（`balanced` 锚点）→ 翻档后 **`20`** |
| 默认 preset | `p4` / `medium` / `8`（CR-1 跨仓契约） | **翻档后**：`p5` / `slow`（libx264 随档）/ `5` ⇒ **CR-1 需与 VE 重议**（阶段一默认仍 balanced，**CR-1 默认条款不动**） |
| 默认 lookahead / tune / multipass / aq | 全不下发 | **翻档后**：`250` / `uhq` / `fullres` / on（阶段一仍不下发） |
| 四道孪生门 baseline（`dump_cmd_*.sh` + `Accessory/test/baseline/*.txt`） | 全绿 | **R1：全量重录**（旧字节作废——即使阶段一，balanced 也钳制、裸跑锚点 21→24；含 `enc_before.txt`） |
| `memory` BASE_LOCK「默认路径永远裸 `-rc vbr -cq N -b:v 0 -preset p4`」 | 成立 | 阶段一仍裸 `-rc vbr -cq N -b:v 0 -preset p4` 但**锚点 24 + 全档钳制**；**翻档后**失效且**默认路径变成未标定**（`quality` 档没有自己的 `_EQQUAL_TIER_OVERRIDE`）⇒ 两处都要改写 |
| §2.5 硬约束、§9「明确不改」 | 有效 | **R1 已就地重写**（§2.5 / §9） |

**你的选择**：选 2（按字面执行）+ 扩到八档（`lossless` / `near-lossless` / `draft` / `test`）+ `archive` 区间收窄为 `[13,18]`（锚点 16 不变）。**R1 追补**：读法 A（钳制/锚点对全档生效、含 `balanced`），旧 baseline 按「钉档 + 全量重录」处理。

**已接受的连带改动**（已分别登记进 §2.5 契约变更表、§6.2 额外任务、§7 门禁改造、§10 风险）：

1. **翻档后**默认 preset `p4/medium/8` → `p5` / libx264 `slow`（随档）/ `slow` / `5` ⇒ **跨仓 CR-1 的「默认档」条款需与 VE 重议**（阶段一不动）。
2. **翻档后**默认 lookahead / tune / multipass / aq 由「全不下发」变为 `250` / `uhq` / `fullres` / on。
3. `DEFAULT_REF` 轨迹 21 → **24（阶段一）** → 20（保留常量名与单一来源，改为引用 `TIER_PROFILES[DEFAULT_TIER]['ref_crf']`）。
4. 四份 baseline 与 `dump_cmd_*.sh` 需**迁移**：旧 inventory 显式钉 `--quality-tier balanced` ＋ **golden 全量重录**（R1 修订：不再是"旧字节保留"）；另建 default 新基线。
5. `memory` BASE_LOCK 与 `MEMORY.md` L203 需改写（含三态轨迹）。
6. **翻档后**默认路径将长期带「未标定」告警，直到 Part B §B4 补上 `quality` 档的 `_EQQUAL_TIER_OVERRIDE`（阶段一 balanced 已标定，无此问题）。

**强烈建议的提交切分**（来自 §10 风险）：先提「tier 能力 + 默认仍 `balanced`」（**R1 更正：不再声称零行为变化**——balanced 自身也钳制、裸跑锚点 21→24，四门 golden 随之全量重录；但**默认 preset 与 CR-1 条款不变**，翻档风险仍可独立回退），再单独提「翻默认档为 `quality`」。这样一旦 VE 对 CR-1 有异议、或默认路径未标定的告警反馈不好，可单独回退第二个提交而不影响 tier 能力本身。

### 12.6 待确认（新增三条）

| # | 问题 | 为什么重要 | 我的倾向 |
|---|---|---|---|
| **Q11** | `draft` 与 `test` 要不要策略层区分？ | 不区分的话这两档就是同一策略的两个质量点，命名显冗余 | **已拍板（2026-10-16）：区分。** 依据用户提出的 `Accessory/preset_matrix.xlsx`，两档分到不同 preset（test=ultrafast/13/10/8/p1，draft=veryfast/11/8/6/p2）。**R1 落全**：libx265 的 test 由 `veryfast` 改为 `ultrafast`（旧稿该格未落实 Q11）；aom 因 `balanced=6` 冻结而 test/draft 并档为 `8`（§13.10R 注） |
| **Q12** | **CR-1「默认档」条款**是否现在就与 Video_Enhancement 同步重议？ | 不同步 ⇒ 两仓默认命令不一致 | **已拍板（2026-10-15）：延后到本方案完整编码并经全流程实测之后再议如何迁移给 VE 侧。** 期间按「先提 tier 能力、默认仍 `balanced`」的提交切分执行，本仓不先翻默认档，避免 VE 侧拿到一个没有实测背书的变更。**R1 注**：阶段一并非零行为变化（balanced 自身钳制 + 锚点 24 + golden 重录），但这不动 CR-1 默认 preset 条款；翻档动作仍后置 |
| **Q13** | `lossless` / `near-lossless` / `draft` / `test` 这四档**阶段一要不要一起实现**？ | 它们只是常量表条目，实现成本极低；标定成本才是大头 | 倾向**一起实现**（表项而已），但明确它们**不进阶段三优先标定队列**，LOO 门禁单独放宽（§10）。**R1 已落**：八档全部进 §4.1/§13.10R |

### 12.7 实施进度台账（**R1 合并版**，2026-10-17；§13.14 的进度表已并入本表）

| 阶段 | 状态 | 产出 / 结论 |
|---|---|---|
| 阶段零（Z1~Z5） | ✅ 完成（994bee1） | hwaccel 两处 `build_ffmpeg_cmd` 补 `try/except ValueError`；sdr 补 `uhq`+`h264_nvenc` 硬报错；README 三处文档 bug。四道孪生门 HEAD vs 工作区**逐字节相同** |
| 阶段零点五（D2 预跑） | ✅ 完成 | 17 条真实 eqq 池。**画质**：svtav1 6→4 码率收益 **+19.6%**、x265 slow→slower **+10.5%**，均远超 3% 门槛。**耗时**（空载复测，以 CPU 时间比为判据）：svtav1 **1.68×**、x265 **4.67×**，均超 1.5× 阈值。结论：**D1+D2 分化成立**，但 x265 的 `archive` 档用 `slower` 性价比差（每 1% 码率收益要付 35% CPU），且该比值**本机特有**（无 AVX2）。**R1 注**：权威档值对 svtav1=`5↔3`、x265=`slow↔slower`；CPU 环按终值复核 |
| aom / vp9 恒等 effort 探针 | ✅ 完成 | `aom_effort_probe`：balanced 冻结 **6**（6≡8；4 过慢）；`vp9_effort_probe`：balanced **2**（BASE_LOCK 甜点；负值无增益）。R1 已据此定 §13.10R（vp9 慢端 `1/0/0/0`、aom 快端 `8/8/7`） |
| `bench_preset_ab.py` 并发守卫 | ✅ 完成 | 默认 ≤ 逻辑核数/2（`--jobs` 仍可显式覆盖）；防超订污染 |
| 阶段一（tier 编码实现） | 🟡 进行中（2026-10-17 起分步提交） | 实现清单 §6.2；DoD §11。**已完成/已提交**：①共享核心（`convert_crf.py`：TIER_PROFILES / 指纹 / 档值助手 / 活动表按档叠加）＋②`vidcrop_hwaccel.py` 接线（`--quality-tier`/`--strict-quality`/`--strict-calib`、§4.4 四步、rc 四轴按档注入、vp9/aom/rav1e 速度轴档值、tune 参数级降档、标定指纹告警）。**待做**：③`vidcrop_cpu_v2.py`、④`convert_sdr_to_hdr.py`（M5 HDR×tier）、⑤ verifier 期望值更新、⑥ golden 重录（dump 脚本钉 `--quality-tier balanced`） |
| harness `--preset-override` | ✅ 完成（2026-10-17） | R1 第一块砖：`_lock_for` 统一替换速度轴（libx264/x265/svtav1/NVENC=-preset、vp9/aom=-cpu-used）；自测 +4 例（§6.3~6.6） |
| 阶段三（CPU 环标定） | ⬜ 未开始 | 走 harness；per-tier `_EQQUAL_TIER_OVERRIDE` 落表 |
| 阶段四（T4）/ 阶段五（L40） | ⬜ 未开始（本机无 GPU） | NVENC 可用性 + `_NVENC_PRESET_RETRY` 触发率（红线 1%）+ `_QP_AFFINE_QUALITY` 重标 |
| 翻默认档（第二步） | ⬜ 待 VE 同步 | CR-1 重议后执行（§12.5 提交切分） |

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
> 3. **R1（2026-10-17）**：§13.10 升格为**唯一权威 `TIER_PROFILES` preset 表**（下文 §13.10R 改定，取代 §4.1/§12.3.2 的旧值；与 `preset_matrix.xlsx` 同步）。用户拍板：libx265 test=`ultrafast`、vp9 慢端 `1/0/0/0`、aom 快端 `8/8/7`。决策记录见 §13.15。

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

**R1 已决**：标定口径 = `BASE_LOCK` 的 **`cpu-used 6`**（探针：6≡8、4 对 balanced 过慢）⇒ aom 的 `balanced` 冻结为 **6**（§13.10R）；`auto_effort()` 的按核漂移随之消除。

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

⇒ **修正：删掉 placebo；libx265 的 lossless 与 near-lossless 用 `slower`；libx264 维持 `veryslow`**（R1 对齐 §13.10R：lossless 路径质量轴被旁路，preset 只影响速度/体积，x264 不必再降一档）。

### 13.7 NVENC 两处问题

1. **near-lossless 与 lossless 都填 p7** ⇒ 两档在 NVENC 上策略完全相同。**R1 保留此并档**：`p1~p7` 七档对八规则必有一处并档，并档点选在 near-lossless/lossless（两者由质量轴区分：`-cq` vs `-qp 0`）；且 NVENC 的 lossless 本质靠 `-qp 0`（仓库已有守卫改写为 `-rc constqp -qp 0 -b:v 0`）而非 preset。
2. **archive/near-lossless/lossless 用 p6/p7（R1 终值）**，与 `_NVENC_PRESET_RETRY = {'p5':'p4','p6':'p4','p7':'p4'}`
   的防御性兜底重叠——该风险尚未在本仓定量验证 ⇒ **T4 环直接测触发率（红线 1%，§B5.4）**，超限走常量层回退。

### 13.8 NVENC 三行本机无法验证

本机 ffmpeg 构建**没有 NVENC**（`ffmpeg -h encoder=hevc_nvenc` 直接返回
「Codec 'hevc_nvenc' is not recognized」）。矩阵的 NVENC 三行需在有 GPU 的机器上核验
量程与可用性。

### 13.9 矩阵只覆盖 6 条轴里的 1 条

`TIER_PROFILES` 还需要 per-tier 的 `lookahead` / `tune` / `multipass` / `aq` /
`ref_crf` / `ref_window`。**后两者是本方案的核心**（质量数值进档 + 窗口钳制），
矩阵完全没有涉及。preset 矩阵只是 D2 轴，不能直接当 `TIER_PROFILES` 用。

（**R1**：六轴已全部并入 §4.1 的 `TIER_PROFILES` 表——本节的批评对 §4.1 生效时已消除；本节作为设计依据保留。）

### 13.10 修正矩阵 —— **R1 定案（= 唯一权威 `TIER_PROFILES` preset 列）**

> **R1（2026-10-17）**：下表即 §4.1 `preset` 列与 `Accessory/preset_matrix.xlsx` 的**同一张表**。
> 四源冲突（§4.1 旧值 / §12.3.2 / 本节旧稿 / §13.14 正文）从此以**本表**为准；来源与理由见下与 §13.15。

| 编码器 | 轴 | test | draft | preview | balanced | quality | archive | near-lossless | lossless |
|---|---|---|---|---|---|---|---|---|---|
| libx264 | preset | ultrafast | veryfast | fast | **medium** | slow | slower | veryslow | veryslow |
| libx265 | preset | **ultrafast** | veryfast | **faster** | **medium** | slow | slower | **slower** | **slower** |
| libsvtav1 | preset | 13 | 11 | 9 | **8** | **5** | **3** | 1 | 0 |
| libvpx-vp9 | cpu-used | 8 | 6 | 5 | **2（探针冻结）** | **1** | **0** | **0** | **0** |
| librav1e | speed | 10 | 8 | 7 | **6（显式 -speed 6，与 native 逐字节相同）** | 4 | 2 | 1 | 0 |
| libaom-av1 | cpu-used | **8** | **8** | **7** | **6（探针冻结，BASE_LOCK 口径）** | 3 | 2 | 1 | 0 |
| h264_nvenc | preset | p1 | p2 | p3 | **p4** | **p5** | **p6** | p7 | p7 |
| hevc_nvenc | preset | p1 | p2 | p3 | **p4** | **p5** | **p6** | p7 | p7 |
| av1_nvenc | preset | p1 | p2 | p3 | **p4** | **p5** | **p6** | p7 | p7 |

相对本节旧稿（10-16 版）的 **R1 改动与理由**：

- **NVENC quality/archive = p5/p6**（旧稿已是 ⇒ 保留；驳回 §4.1 草稿的 p6/p7）：与「D2 只差一档」的设计（x265 slow→slower、libx264 slow→slower 均一档）一致；`p5/p6/p7` 可用性 + `_NVENC_PRESET_RETRY` 触发率由 T4 环实测量化（§B5.4，红线 1%），超限走常量层回退。
- **libsvtav1 quality/archive = 5/3**（旧稿已是 ⇒ 保留；驳回 §4.1 草稿的 6/4）：与矩阵自 `faf74a4` 起一致，比 6/4 更激进（更优压缩）。
- **libx265 test = `ultrafast`**（**R1 修**：旧稿 test 与 draft 同为 veryfast，是全表唯一未落实 Q11 的格子）——test 与 libx264 对齐取 ultrafast，draft 保持 veryfast。
- **libvpx-vp9 慢端修正（R1）**：`balanced=2`（探针/BASE_LOCK）冻结后，旧稿 `quality=3 / archive=2` 与 balanced **倒挂（3 > 2 = 更快）且 archive = balanced**（复制了 rav1e 被修正过的「两档同 effort」问题）⇒ 改为 `quality=1 / archive=0 / near=0 / lossless=0`（更慢 = 更优压缩）；**不采用负值**——探针里 `-4` 未显示超出 0 的增益。
- **libaom-av1 balanced = 6 冻结 + 快端修正（R1）**：探针结论「保留 6」（6≡8、4 对 balanced 过慢；BASE_LOCK 即 6，`QUALITY_MAP` 在该口径下标定）。因 `balanced=6` 上移，旧稿快端 8/6/5 会倒挂 ⇒ 改为 `test=8 / draft=8 / preview=7`（0~8 量程内 6 之上只剩两档，test/draft 并档）；慢端 3/2/1/0 保持。
- **libx264 跟随 tier**（§13.2）；**librav1e** balanced=显式 `-speed 6`（§13.10.1）；**x265 near/lossless = slower**（§13.6）；**x264 near/lossless = veryslow**（保留矩阵值；§13.6 文字已同步）。

**待 T4 / CPU 环复核的清单（R1 后仍开放）**：NVENC `p5/p6/p7` 可用性与 `_NVENC_PRESET_RETRY` 触发率（T4）；aom `quality=3/archive=2` 相对探针建议 `archive=4` 的取舍（CPU 环）；x265 `test=ultrafast` 的输出可用性（冒烟档，CPU 环抽查）；vp9 慢端 `0` 与负值的进一步对比（可选）。

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
（替换 `BASE_LOCK` 的 medium/8；**该参数已落地，2026-10-17**）逐档重标，并过 LOO 门禁。

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
   ⇒ **✅ 已完成**：`bench_preset_ab.py` 的并发守卫（默认 ≤ 逻辑核数/2，`--jobs` 可显式覆盖；§12.7）。
2. **`Elapsed` 解析 bug 他机也修了一份**：说明这个坑（`/usr/bin/time -v` 标签含冒号）确实
   容易复发。本仓 `bench_preset_ab.py` 的 `parse_time_file` 已带警告注释。

**R1 更新**：AVX2 主机数据已补齐（§13.12）；`libx265` archive 档终值已定为 `slower`（§13.10R），CPU 环标定只需验证其 VMAF 差异在预期范围内。

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

**结论**：在支持 AVX2 的主机上，x265 `slower`/`slow` 的 CPU 时间比约为 **4.8×**，略高于无 AVX2 机器的 4.55~4.67×。这表明 AVX2 对此 preset pair 的影响复杂（可能基线 auto 也有变化），但仍可视为稳健属性。**R1 已定 `archive` 档 libx265 = `slower`**（§13.10R）；CPU 环标定验证与 `balanced` 的 VMAF 差异即可。


### 13.13 仍待输入（**R1 更新**）

1. ~~libaom-av1 / libvpx-vp9 的恒等 effort 值定到几~~ —— **已定（R1）**：aom `balanced=6`（探针：6≡8、4 过慢，BASE_LOCK 口径即 6）、vp9 `balanced=2`（探针/BASE_LOCK）；快端/慢端终值见 §13.10R。「`QUALITY_MAP['libaom-av1']` 当年 cpu-used」的文档缺口已由 BASE_LOCK=6 口径登记补齐。
2. **NVENC 三行的量程与 `p5/p6/p7` 可用性** —— 需在带 GPU 的机器上核（本机 ffmpeg 无 NVENC 构建）；含 `_NVENC_PRESET_RETRY` 触发率（红线 1%）。
3. ~~`bench_preset_ab.py` 加「并发 ≤ 逻辑核数/2」默认上限~~ —— ✅ 已完成（§12.7）。
4. **per-tier 标定排期** —— 8 档 × 9 编码器；R1 起改走 harness（`--preset-override` ✅ 已落地），CPU 环可先行（§6.3~6.6）。

### 13.14 专项测试计划（**R1 压缩版**；判据与细节归 Part B，进度归 §12.7）

> **R1（2026-10-17）**：本节旧稿（`≥93 VMAF / ≥0.98 SSIM` 判据、「遍历 preset 找等价 preset」误述、重复的注意事项段、与 §12.7 重复的进度表）已按审核意见压缩为下述任务清单；判据、阈值、口径一律以 Part B 为准。原任务 1（aom/vp9 恒等值）已由探针完成、原任务 3（并发守卫）已完成——现状统一见 §12.7。

**① CPU 环标定（可立即开始）**：`calibrate_equal_quality.py` 的 `--preset-override` ✅ **已落地**（2026-10-17）→ 按 §13.10R 档值对 `libx264` / `libx265` / `libvpx-vp9` / `libaom-av1` / `libsvtav1` / `librav1e` 逐档跑标定，过 LOO（软编 ≤5.9、rav1e ≤7.5、`GATE_ANCHORS=(0,27)`）后填 `_EQQUAL_TIER_OVERRIDE[tier]`。**M7**：`draft(36)/test(45)/near-lossless(6)/lossless(0)` 需先扩锚（建议 +`0/6/33/39/45`）或明确 LOO 放宽——二选一写进报告。

**② T4 环**：NVENC `h264_nvenc` / `hevc_nvenc` 各档量程 + `p5/p6/p7` 可用性 + `_NVENC_PRESET_RETRY` 触发率（红线 1%）+ `_QP_AFFINE_QUALITY` 同步重标（CR-4，⑨ 组逐条断言）。产出：矩阵 NVENC 行经实测核准（不可用则走常量层回退）。

**③ L40 环**：`av1_nvenc` 同上（`av1` 走 `--expect-av1` fail-fast；Turing 代无 AV1 编码单元）。


**④ D1/D2/tune 归因 bench**：主方案 Part B §B5。**阈值裁决（R1）**：画质 3% / 耗时 1.5×（CPU 时间比）/ `_NVENC_PRESET_RETRY` 触发率红线 1%。

> 进度表已并入 §12.7（R1 合并）；aom/vp9 探针、并发守卫、libx264/rav1e 排期的现状见该表。

**⑤ HDR 域 × tier**：**不在本方案范围**（Part B 顶部范围声明 + `Plan/Handoff_VMAF在SDR2HDR管线不可标定_VU→eqq标定_20261015.md`）；tier × HDR 复合规则见主方案 §4.4.1，单独立项。

> **R1 作废声明**：原「阶段一编码实现被延后、依赖 1~4 完成」与「零回归保证（默认行为逐字节相同）」两条条款**作废**——H3：阶段一与标定并行（§6.2）；H1=读法 A：不存在零行为变化路径（§5.3/§12.5）。

**注意事项（唯一一份，R1 去重）**：

- 全部在**空载**机器上跑（负载 < 核数 10%）；耗时判据用 **CPU(user+sys) 时间**，墙钟仅辅助。
- 判据用 **LOO + 等 VMAF 码率比**；**不要**用 in-sample 的 `verify_equal_quality.py` 门（不能为表值背书）。
- 每档标定命令 = `calibrate_equal_quality.py --preset-override <§13.10R 档值> --tag <tier>`；产物含机器/驱动/ffmpeg 指纹（Part B §B6）。
- `lossless` / `near-lossless` / `draft` / `test` 四档优先级最低、LOO 单独放宽；超门禁保持未标定（Part B §B7）。
- `_NVENC_PRESET_RETRY` 触发率必须实测记录（不假设为零）；本机无 GPU ⇒ 阶段四/五在有卡机器补跑。
- `_EQQUAL_TIER_OVERRIDE` 缺键 ⇒ 自动回退 `balanced` 表并告警，允许逐步落地（§2.4 / §10）。

---

## 13.15 R1 审核决策记录（2026-10-17）

| # | 决策 | 内容 | 依据 |
|---|---|---|---|
| R1-1 | **H1 = 读法 A** | 窗口/钳制/锚点注入对**全档**生效（含 `balanced`）；旧 baseline 改为「钉档 + 全量重录 + 人工审阅」；`convert_crf.py` 契约句在实现时改写 | 用户拍板（2026-10-17） |
| R1-2 | **H2 = 唯一权威表** | `TIER_PROFILES` preset 列 = §13.10R = `Accessory/preset_matrix.xlsx`；§4.1/§12.3.2/§13.14 旧值全部作废 | 用户拍板「同意」 |
| R1-3 | **H3 = 时序重排** | harness 标定先行（`--preset-override` ✅ 已落地）→ 与阶段一 CLI 并行；CLI 端到端（dry-run/告警/窗口/指纹）为阶段一 DoD | 用户拍板 |
| R1-4 | **H4 = 占位清理** | `_EQQUAL_TIER_OVERRIDE` 非 `balanced` 键一律空 dict；「已标定」只由 `CALIB_FINGERPRINT` 判定（M4：按生效编码器 × strategy） | 用户拍板 |
| R1-5 | **M1~M9 条文** | M1→§4.1 注/§4.4 步2；M2/M3→§4.3；M4→§2.4；M5→§4.4.1；M6/M8→§4.4 步1；M7→§6.3~6.6；M9→§4.4 样例表/§11 | 用户拍板 |
| R1-6 | **文档卫生** | 行号→符号（文首注）；§12.7 与 §13.14 台账合并；残留清理（§12.4 等）；「slsx」拼写在实际实现提交中处理 | 用户拍板 |
| R1-7 | **默认路径轨迹** | 裸跑默认质量值：`21` → **`24`**（阶段一，默认档 `balanced` 锚点）→ **`20`**（翻档后）；三态写进 README/help/memory | R1-1 × Q12 的直接推论 |
