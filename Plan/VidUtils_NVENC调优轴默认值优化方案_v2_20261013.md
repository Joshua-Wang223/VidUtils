# VidUtils NVENC 调优轴（`--nvenc-tune` / `--nvenc-multipass`）默认值优化方案 v2

- 日期：2026-10-13
- 适用脚本：`vidcrop_hwaccel.py`、`vidcrop_cpu_v2.py`、`convert_sdr_to_hdr.py`（**孪生约定**：
  共享常量、取值表、`auto` 解析规则、报错首行都逐字一致；本文把**有意的不对称**单列成节）
- 依据：
  - `Plan/ffmpeg_nvenc_knowledge.md` §5 / §5.1（`-tune` / `-multipass` 语义 + T4 固定 CQ 的 ±multipass A/B）
  - `memory/project_rate_control_params.md` 追加段（2026-10-04，本轴落地记录）
  - 代码现状：`vidcrop_hwaccel.py` L425-455 / L3629-3745 / L5502-5510 / L5843-5849；
    `vidcrop_cpu_v2.py` L892-905 / L1493-1600 / L4286-4300 / L4560-4564；
    `convert_sdr_to_hdr.py` L455-468 / L1543-1645 / L3611-3620 / L4627-4640
- 判据：`Accessory/verify/verify_rc_lookahead.py` ⑩ E / ⑩ F 组、`verify_cli_parsing.py`、
  `verify_overview_lockstep.py`、`verify_sdr_to_hdr.py` ⑦ 组、`Accessory/test/dump_cmd_*.sh`
- 状态：**方案待评审**（未动代码）。三项待拍板见 §10。

---

## 0. v2 相对口头版 v1 的修正（为什么有这版）

1. **删除“cpu_v2 补 `--nvenc-aq`”**：cpu_v2 是纯 CPU 侧孪生脚本，默认路径与硬编无关；该参数只在
   `--codec *_nvenc` 透传这一条路上有意义，属低价值 + 高孪生维护成本。改为**登记为有意的不对称**（§6）。
2. **`--nvenc-tune` 的 `auto` 派生按脚本能力分级**：hwaccel / sdr_to_hdr 有探测、有参数级重试、
   有降级梯 ⇒ 允许 `auto` 派生 `uhq`；**cpu_v2 无探测、无重试、无降级 ⇒ 不派生**（盲猜 + 无兜底）。
3. **`_tune_tries` 参数级降档**明确为“仅 auto / `--archive` 派生值启用，显式用户值不降”，并映射到三个脚本
   各自的既有重试机制。

---

## 1. 一句话结论

三个脚本在这两个参数上的**代码默认值今天完全一致**（均 `None` = 不下发），差异全在**生效面**。
要适配高质量存档，只需把 `--nvenc-tune` 从“恒不下发”改为**按编码器能力自适应的 `auto`**
（hevc/av1 NVENC → `uhq`；h264/CPU → 不下发）；`--nvenc-multipass` 默认规则**不动**（T4 实测依据）；
其余存档向配置收敛到一个默认关闭的 `--archive` 开关。

---

## 2. 现状盘点：差异的真相

| 维度 | `vidcrop_hwaccel.py` | `vidcrop_cpu_v2.py` | `convert_sdr_to_hdr.py` |
|---|---|---|---|
| `--nvenc-tune` / `--nvenc-multipass` argparse 默认 | `None` / `None` | 同左 | 同左 |
| 取值表 / `_NVENC_UHQ_CODECS` / 自动 `fullres` 规则 | — | 逐字相同 | 逐字相同 |
| `constqp` / `--qp 0` 时 multipass | 忽略+告警，**strict 下报错退出 2** | 忽略+告警 | 忽略+告警 |
| **默认编码器** | **`h264_nvenc`**（默认即走 NVENC） | `libx264`（NVENC 需显式 `--codec` 透传） | `libx265`（同上） |
| `--nvenc-aq` | 有，默认关 | **无** | 有，默认关 |
| CLI 层 `uhq` + `h264_nvenc` 硬报错 | 有（L5843） | 有（L4560） | **无**（仅策略内告警 L1640） |
| 参数级降档重试 | 有（`_NVENC_PRESET_RETRY` / `_preset_tries`，L455 / L5103） | **无** | 有 ladder（L4627） |

**四点关键差异：**

1. **生效面不同**：hwaccel 默认编码器就是 NVENC，这两个轴的默认值“天天在跑”（默认 = ffmpeg 的
   `hq` + `disabled`）；cpu_v2 / sdr_to_hdr 默认是 CPU 软编，两参数默认**完全不起作用**，只有用户显式
   `--codec hevc_nvenc/av1_nvenc` 才生效。
2. **hwaccel 默认编码器恰是 `h264_nvenc`，而 `uhq` 只有 hevc/av1 有**（Plan §5：h264 传 `uhq` → rc=234）
   ⇒ 默认值必须“看编码器能力下菜碟”，一刀切 `uhq` 会在默认路径上每次多刷一条“uhq 不适用”告警。
3. **兜底能力不同**：hwaccel / sdr_to_hdr 有探测 / 重试 / ladder；cpu_v2 无探测、无重试、无降级
   （README ³ 脚注定位为“透传自负”）⇒ 同一默认值在三种脚本里的风险不同。
4. **静默丢弃风险已被现有实现防住**：显式值全部走 `_ignore` → `warn`，cpu_v2 三条路径都有出口
   （L3943 面板 / L4046 顺序 / L4879 dry-run）；且 `-tune` / `-multipass` 是 NVENC 私有语义——错发给
   libx264 会直接 rc≠0（libx264 的 `-tune` 是 `film/animation/…` 另一套体系）。现有“按编码器名判 + 告警”
   的闸门正是防这个。

---

## 3. 存档诉求 vs 现有默认（仓库既有实测依据）

- `-tune hq` **就是 ffmpeg 默认值**（`-h encoder` 实测），写了等于没写 ⇒ 当前默认在 `h264_nvenc` 上
  已是最高可得档；
- 但 `hevc_nvenc` / `av1_nvenc` 有更高档 **`uhq`**（自动启用 lookahead + temporal filter，显存/耗时更高）
  ⇒ **默认值在 HEVC/AV1 NVENC 存档路径上低了一档**，须显式 `--nvenc-tune uhq` 才拿得到；
- `-multipass`：T4 固定 `-cq` A/B 实测 fullres ΔVMAF **−0.006~−0.108**、qres −0.067~−0.335，码率
  ×0.98~0.997（Plan §5.1）⇒ **CQ 路径默认不开是正确的、不许改**；其价值在 CBR / 紧 VBV（那里已自动 `fullres`）；
- `uhq` 在 hevc/av1 上是否被 T4 / 驱动 580 接受**尚未实测**（只实测了 h264 传 `uhq` → rc=234）。

⇒ **只需动 `tune`；`multipass` 只把语义显式化为 `auto`。**

---

## 4. 方案总纲

1. **三态语义**：`auto`（新默认）＞ 显式值（原样透传，字节不变）＞ 物理默认（none）。
2. **解析位置**：放进 `apply_rc_control_args()` 内——只有那里知道**本策略实际编码器**
   （hwaccel 逐策略、sdr_to_hdr 逐降级档都会换编码器）。
3. **按脚本能力分级**：有探测 / 重试兜底的（hwaccel、sdr_to_hdr）才允许 `auto` 派生；无探测无重试的
   （cpu_v2）不派生，登记为**有意的不对称**（先例：`_SCALE_ALGO_HELP` 里“这份串故意不列 cuda”）。
4. **`--archive` 显式开关**把其余存档向配置收敛，默认关 ⇒ 默认字节变化最小、可逐条关闭。

---

## 5. 逐项设计

### 5.1 `--nvenc-tune`：`None` → `auto`

解析规则（`choices` 增加 `'auto'`，默认改 `'auto'`；`None` 仍等价 auto，供直接调用方）：

| `auto` 解析时实际编码器 | hwaccel / sdr_to_hdr | cpu_v2 |
|---|---|---|
| `hevc_nvenc` / `av1_nvenc` | `-tune uhq` | **不下发**（有意不对称，§6-2） |
| `h264_nvenc` | 不下发（无 uhq 档，`hq` 即默认） | 不下发 |
| 非 NVENC（降级策略） | 不下发，**静默**（不告警） | 不下发，静默 |

```
def resolve_tune(requested, codec, *, allow_auto_derive):   # 位置：apply_rc_control_args 内
    if requested not in (None, 'auto'):
        return requested                 # 显式值（含 'hq'）原样透传，字节逐字不变
    if codec not in NVENC_CODECS:
        return None                      # auto 在非 NVENC 上静默（不告警）
    if not allow_auto_derive:            # 仅 cpu_v2：无探测/无重试 ⇒ 不派生
        return None
    return 'uhq' if codec in _NVENC_UHQ_CODECS else None    # hevc/av1 → uhq；h264 → None
```

约束：
- 显式值永远优先；`--nvenc-tune hq` 是回到旧语义的开关（仍下发 `-tune hq`，与今天逐字相同）；
- strict 下 `auto` **不触发** `_ignore`（现状不变），显式值仍按现状 strict 报错；
- 告警区分“显式值 vs auto 派生值”：派生值在非 NVENC 上静默，否则 `--archive` + 降级会每条策略刷一条告警。

### 5.2 `--nvenc-multipass`：`None` → `auto`，**行为一字不改**

```
_mp = multipass                                    # None 与 'auto' 等价
if _mp in (None, 'auto') and c in NVENC_CODECS and (rc_mode == 'cbr' or bitrate):
    _mp = 'fullres'
if _mp not in (None, 'auto'):
    ...（沿用现有 _ignore 三分支 + 下发）
```

三脚本逐字一致：只依赖用户显式给的 `rc_mode` / `bitrate`，是纯本地推导、不依赖环境探测，且 `fullres`
枚举值 T4 已实测跑通。

### 5.3 新增 `--archive`（三脚本同名同义，默认关）

仅在用户**未显式给**对应参数时生效（不得覆盖显式关闭）：

| 项 | hwaccel | sdr_to_hdr | cpu_v2 |
|---|---|---|---|
| `--nvenc-tune` | `uhq`（编码器不支持则提示不报错） | 同左 | **不生效** |
| `--nvenc-multipass` | `fullres`（`constqp` 除外；告警说明“画质无收益、只压码率 −0.2~2%、耗时/显存升”） | 同左 | 同左 |
| `--nvenc-aq` | 开 | 开 | **不适用**（无此参数） |
| preset / lookahead / `DEFAULT_REF` | 一律不动（避免破坏 CR-1 与等效表标定点），仅在概览提示“存档建议 `--preset p6/p7 --lookahead 250`” | 同左 | 同左 |

### 5.4 鲁棒性：`_tune_tries` 参数级降档（auto / `--archive` 派生值才启用）

- **hwaccel**：在既有 `_preset_tries`（L5103）旁加 `_tune_tries = [norm_tune, None]`；仅当 `norm_tune`
  来自 `auto` / `--archive` 派生时启用。驱动不认 `uhq`（rc≠0）时重试一次“不带 `-tune`”，避免为一个
  tune 档整条策略掉到 CPU 软编（慢一个数量级）。
- **sdr_to_hdr**：`_run_job_with_fallback`（L4627）阶梯插一级“去 tune 重试”。
- **cpu_v2**：不需要（不派生 `uhq`）；显式 `uhq` 踩雷属用户自负，README ³ 已定位。
- ⚠ 全部标注为**待上机项**：`hevc_nvenc -tune uhq` 在 T4 / 驱动 580 是否被接受、4K 显存与耗时影响，
  均无实测数据。

### 5.5 可观测性

- hwaccel 概览“码率控制”行（现有 `_rc_bits`）扩展：**仅当实际编码器为 NVENC 时**追加
  `tune: <解析值>`、`multipass: <解析值>`、`aq: on/off`；非 NVENC 不显示（避免噪声）。
  cpu_v2 / sdr_to_hdr 同位置同措辞（孪生）。
- 三份 help 写清 `auto` 规则与 `--archive` 档位差异；`--dry-run` 即人工核对面。

---

## 6. 有意的不对称登记（3 项）

1. **cpu_v2 不收 `--nvenc-aq`**（原方案已删除该项）→ README L92 该行 v2 列保持 ❌，并在
   `apply_rc_control_args` 常量区 + `memory/project_rate_control_params.md` 记一笔，防下一轮审查报
   “漏移植”。需要的人用 `--extra-args -- -spatial-aq 1 -temporal-aq 1`。
2. **cpu_v2 的 `--nvenc-tune auto` 不派生 `uhq`** → 三份 help + README L393 + 同一条 memory 记录；
   理由：无探测无重试 ⇒ “自适应”是盲猜，且新增默认路径失败面。
3. （既有先例）`--scale-algo cuda-*` 在 cpu_v2 被拒——新登记沿用其“有意为之”措辞。

---

## 7. 实施清单（文件级）

| 文件 | 改动 |
|---|---|
| `vidcrop_hwaccel.py` | ① argparse：两参数 default `'auto'`、choices 加 `'auto'`、help 重写；② 新增 `--archive`；③ `apply_rc_control_args`：auto 解析 + archive 分支 + `_ignore` 区分显式/派生；④ `process_file` 加 `_tune_tries`；⑤ 概览 `_rc_bits` 扩展 |
| `vidcrop_cpu_v2.py` | ①②③⑤ 同构，但 auto tune **不派生 uhq**（注释登记）；**不加 `--nvenc-aq`**；④ 不需要 |
| `convert_sdr_to_hdr.py` | ①②③⑤ 同 hwaccel；④ ladder 插一级；另补 CLI 层 `--nvenc-tune uhq` + `h264_nvenc` 硬报错（与另两个一致） |
| `README.md` | L336-337 / L393-394 / L486 参数表“默认”列改写 + `--archive` 新行；L92 v2 列保持 ❌；新增“有意的不对称”小节 |
| `Plan/ffmpeg_nvenc_knowledge.md` | §5 “默认路径保持裸 `-rc vbr -cq N -b:v 0 -preset p4`”结论改写为新默认矩阵 |
| `memory/project_rate_control_params.md` | 追加段：本轮默认值变更 + 3 项有意不对称登记 + `uhq` 待上机项 |
| `Accessory/verify/*`、`Accessory/test/*` | 见 §8 |

---

## 8. 测试与门禁

| 测试 | 新增 / 修改断言 |
|---|---|
| `verify_rc_lookahead.py` **⑩ F** | 由现有“默认不下发 `-tune`/`-multipass`”一条拆为 8 条：h264+auto 无 `-tune`（旧字节）；**hwaccel / sdr_to_hdr** hevc+auto → `-tune uhq`；**cpu_v2** hevc+auto 无 `-tune`（有意不对称）；`--nvenc-tune hq` 回旧语义；非 NVENC+auto 静默无告警；cbr/bitrate 自动 `fullres`（三脚本一致）；显式 `disabled` 优先；constqp 忽略 multipass |
| `verify_rc_lookahead.py` ⑩ E | 已有 hwaccel aq；补 sdr_to_hdr；cpu_v2 断言“无该参数”（不进命令） |
| `verify_cli_parsing.py` | `auto` 被 choices 收；`--nvenc-tune bogus` 仍拒绝；`--archive` 与显式值组合不互相覆盖 |
| `verify_overview_lockstep.py` | 概览 tune/multipass/aq 三脚本同措辞；非 NVENC 编码器下不出现 |
| `verify_sdr_to_hdr.py` ⑦ | 补 tune/multipass 默认 + `uhq`+h264 CLI exit 2 |
| `dump_cmd_default.sh` / `baseline/cmd_full.txt` | 回归确认：孪生门**不含 NVENC 格**（文件头已说明能力差异）⇒ 不破门；`dump_enc_options.sh` 若含 NVENC token 需核对 |
| `verify_equal_quality.py` | `auto→uhq` 只改 `-tune`、不改质量轴取值，既有断言应全绿 |
| `check_readme_refs.sh` | README / 代码默认描述一致 |

---

## 9. 分期与风险

| 阶段 | 内容 | 风险 |
|---|---|---|
| **一** | auto 语义 + §5.4 + §5.5 | 字节变化**仅限** hevc/av1 NVENC 请求（hwaccel / sdr_to_hdr）；h264、CPU、cpu_v2 全部路径逐字不变 |
| **二** | `--archive`（默认关） | 零基线风险；aq / multipass 收益待上机 A/B |
| **三** | 依据 A/B 决定是否把 aq / preset / lookahead 纳入**默认** | 动 `DEFAULT_REF` / preset 会破坏 CR-1 与等效表标定点，需重新标定 |

| 风险 | 缓解 |
|---|---|
| hevc/av1 的 `uhq` 旧驱动不认（rc≠0） | `_tune_tries` 参数级降档（仅 auto / archive 派生值）；显式值不降 |
| 显存 / 耗时上升（uhq 自带 lookahead + temporal filter） | 4K 复测；概览 / help 提示；`--nvenc-tune hq` 回退 |
| cpu_v2 无重试却自动下发 uhq | 有意不对称：cpu_v2 不派生（§6-2） |
| hwaccel strict + 显式 NVENC-only 参数 + 降级策略 ⇒ `_ignore` 抛出的 ValueError 逃出 `build_ffmpeg_cmd`（L5100 附近无 try）成 traceback | 阶段二顺带修：`process_file` 捕获 ValueError → 退出 2 可读报错 |
| 默认字节变化动基线 | 孪生门不含 NVENC 格；⑩ F 显式更新；README / Plan / memory 三处同步 |

---

## 10. 明确不改

`DEFAULT_REF=21`；preset 默认（CPU `medium` / GPU `p4` / svtav1 `8`）；`NVENC_TO_*` 映射表；
`_NVENC_TUNE_VALUES` / `_NVENC_MULTIPASS_VALUES` / `_NVENC_UHQ_CODECS` 取值本身（只往 choices 加 `'auto'`）；
multipass 的 CQ 路径默认不开；`--nvenc-aq` 默认关。

---

## 11. 完成判据（DoD）

三脚本 `--dry-run` 可按 §5 表格人工核对；⑩ F 组全绿且含 SABOTAGE 负向对照；
`dump_cmd_default.sh` / `dump_enc_options.sh` / `verify_equal_quality.py` 无回归；
README / Plan / memory 三处默认描述与代码一致。

---

## 12. 待拍板（3 项）

1. 阶段一的 `auto→uhq`（hevc/av1，hwaccel / sdr_to_hdr）现在就上，还是先只上阶段二
   （`--archive`，零字节变化）？
2. cpu_v2 的 `--nvenc-tune auto` 不派生 `uhq`（有意不对称）是否认可？
3. `--archive` 命名是否 OK（备选：`--tier archive` / `--quality-tier high`）？
