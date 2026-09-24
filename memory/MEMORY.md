# VidUtils · 工程记忆索引

这里放**工具背后的事实与踩坑**，不是 API 文档（用法看 `../README.md`）。

每条一行，指向同目录下的文件。文件格式沿用本机 codebuddy 自动记忆的约定：
`---` frontmatter 里带 `name` / `description` / `type`（project / user / feedback / reference），
正文对 project 与 feedback 类用「事实 → **Why:** → **How to apply:**」的结构，方便日后判断
这条记忆是不是还成立。

---

- [长时 ffmpeg 任务必须 setsid 分离 + 别直接写 MP4](project_long_ffmpeg_jobs.md)
  — 代码宿主崩溃会带走同进程组的 ffmpeg；MP4 缺 moov 整份作废（已发生过一次，34 分钟算力白跑）；
  并发实例互删临时文件；以及 `interp_2x_safe.sh` 里那套防呆（锁 / 两层复用校验 / PID 临时名）；
  2026-09-15 起又加了 cgroup 感知的环境探测、分片级并行 `-j`、时间段截取 `--SS/--TO/-T`；
  GPU 专版 `interp_2x_safe.sh` 与通用版 `interp_2x_safe_v1.sh`（多一条 CPU 回退）recipe 兼容、可互相接管；
  收尾 concat 的 `Non-monotonic DTS` 是某一分片比邻片少 1 帧引起，已用 `parts.txt` 里的 `duration` 指令修掉；
  片头 `trim=3` 让整条视频超前 3 个输出帧（接缝不跳、偏移不累积），已给音轨 copy 起点补同量对齐（62.56ms@48000/1001）；
  CPU auto 并行度按实测收敛到「每路 1 核」（08/16 三轮：4→2→1，插帧滤镜串行、单片 ≈1 核）并感知 cgroup 已有负载；
  `--threads` 会被翻成 `-x265-params pools=N`（`-threads` 只改 frame threads、`-filter_complex_threads` 对 minterpolate 无效）；`-w` 相对路径的坑已修；
  4K 单片 4.2~4.6GB → 8GiB 机器只能 1 路（强开必被 OOM 杀，实测 oom_kill 3→9），失败诊断已能识别 OOM 并过滤 x265 噪声；
  中断收尾改为有界（TERM→3s→SIGKILL）并能在下次启动自愈孤儿 ffmpeg（ffmpeg 对 TERM 要 11–13s 才退，实测）；
  并发度按「剩余待编片数」再收敛、撞锁报错打印持有者 pid/启动时间/命令行、新增 test/test_interp_2x_orphan.sh；
  另有一条实测教训：**别"原位"改正在被执行的脚本**（bash 会按字节偏移重读、把跑完的循环再跑一遍）
- [bash 并行调度的四个坑](project_bash_parallel_pitfalls.md)
  — `wait -n` 会提前返回不能当完成信号；`while read < <(tail)` 能永久卡死在 pipe_read（0% CPU）；
  `exec ffmpeg` 的子 shell 被杀会留下孤儿 ffmpeg；找"另一个实例"必须读 `/proc/<pid>/cmdline` 精确匹配
- [ffmpeg 挂起的两个根因](project_ffmpeg_stdin_hang.md)
  — SIGTTIN（状态 T，`-nostdin` 能修）vs 输出管道反压（状态 S 且 CPU 冻结，`-nostdin` 没用）
- [复刻 ls 版式与视频属性探测的踩坑](project_ls_probe_pitfalls.md)
  — vidls（ls/ll 替代，另有 `vidll` = `vidls -l`）的实测事实：coreutils 列算法的**三条真判据**
  （竖填、总宽**严格 <**、**最后一列不能空**）与 Tab 填充的取舍规则，并**推翻了原先记的
  「列宽下限 3」**（三处 bug 已在 Linux/Windows 两份实现里同时修好）；
  `ffprobe` 没有 `-hwaccel`/`-nostdin`（硬解数帧只能用 ffmpeg + `-progress pipe:1`）；
  帧数四档（包头/硬解/包数/估算）差异；AV1 降级实测（首波每个文件各试一次、失败尝试 0.36–0.50s）；
  Windows 移植必须处理的六件事（**CRLF 会让 diff 全红**、`.cmd` 必须纯 ASCII、
  控制台编码不要硬钉 UTF-8、`-l` 少三列、`total` 只能近似、CUDA 关键字要带 `.dll`）
- [T4 能力边界 + 测性能前先查并发流水线](project_t4_gpu_capabilities.md)
  — 别的流水线会抢 CPU/GPU 导致基准不可信；**VP9 有硬解但从来没有硬编**、
  **AV1 硬解硬编都没有**（`av1_cuvid` 在列表里但运行时报 not supported）；
  「本机 AV1 完全编不出来」这条已更正：custom ffmpeg 7.1 没有、系统 ffmpeg 6.1.1 有
  libsvtav1/libaom-av1；零拷贝管线里 `-pix_fmt` 无效；
  ⇒ **硬解能力是「分编解码器」的、不是布尔量**：2026-09-21 起 `vidcrop_hwaccel.py`
  按源 codec 用真实输入试解 1 帧（`has_decoder` 那个 H.264 微流探针只代表 H.264）
- [FFmpeg 7.1 已合并 nvinterpolate 与 libvmaf](project_nvinterpolate_build.md)
  — 单一 ffmpeg、无需环境文件；`nvinterpolate` 必须放滤镜链末尾否则段错误；移植补丁位置
- [两个裁剪脚本的行为一致约定](project_preset_equivalence.md)
  — 两裁剪脚本的 NVENC↔x264 表必须一致（曾错位一档：p4→medium/p5→slow，会让同一条 `--preset p5` 落不同档）；
  降级到 CPU 编码器时基准档取"请求的编码器"的默认值再换算；概览块只展示最终命令里真正会出现的参数
  （编码器名取策略链第一条、无 `-preset` 的编码器不展示 preset 字段）；
  `--codec auto` 必须解析成具体编码器，透传会得到 `-c:v auto` 使 ffmpeg 报 Unknown encoder（曾发生在 cpu_v2）；
  **`--mode` 的语义/校验/跳过判定也要两边一致**：2026-09-18 同时加了 `crop-cover`
  （先裁剪再缩放覆盖；无 `--crop-ratio` 必须给全宽高、有则只给一个按比例推导；
  后缀 `_cropcovered`；比例与源不同时即使同尺寸也不能跳过）；
  同日又逐字对齐了 17 条校验文案与校验顺序（cpu_v2 的「crop-ratio + 显式尺寸」由
  「忽略+提示」改为**报错**、量程检查提到 `_resolve_quality_params` 之前）；
  **2026-09-23**：`--crop-ratio` + **只给一个** `--output-*` 维度从"静默丢弃该维度"
  改为**三个模式统一按比例补全**（比例定形状、尺寸定分辨率）—— 真机踩坑是
  `--mode cover --crop-ratio 16:9 --output-height 1080` 打在本身 16:9 的源上会算成
  源尺寸 → **同尺寸跳过**（1080p 请求变 no-op）；两个都给仍报错，
  判据 `verify/verify_ratio_single_dim.py`；
  同轮还按"以 hwaccel 为权威"对齐了两处既有分叉（**crop 模式目标大于源：v2 由「失败」
  改「跳过」、rc 1→0**；删掉 v2 在 crop-cover 下多打的一条提示）+ 同尺寸跳过的措辞；
  唯一保留：跳过行的外层格式（v2 带文件名）两边仍不同；
  **2026-09-24 追加「约定 5：完整 ffmpeg 命令逐字相同」**：新增**第四道门**
  `test/dump_cmd_full.sh`（17 用例，自带断言 + `SELFTEST`/`SABOTAGE`），为此统一了
  `-pix_fmt`（8bit auto 两边都不发，原先 v2 恒发 `yuv420p` → **静默降 4:2:2/4:4:4 色度**）、
  `-threads`（hwaccel 新增 `--threads`；两边都只对软编下发）、选项物理顺序；
  cpu_v2 的 `pix_fmt` 还拆成"约束值 / 下发值"（合并会让偶数校验静默失效）；
  ⚠ 有意保留：NVENC 轴（hwaccel 真降级 vs v2 原样透传）与 10bit `-pix_fmt` 不在门内
- [cover 的 CUDA 缩放：实测数据、两条硬约束与质量门](project_cuda_scale_cover.md)
  — 2026-09-20 给 `vidcrop_hwaccel.py` 的 cover 加了「`scale_cuda` + 显式 `hwdownload` + CPU crop」
  策略：真实 4K→1440x1080 实测 **26.65s → 12.82s（快 51.9%）**，CPU 侧 `scale(lanczos)` 占 28.4%
  （对着旧 bicubic 基准 24.35s 则是 44.5%；合成 testsrc2 测不出差异，只有 0.2%——收益是内容相关的）；
  **质量门已完整通过**：`PSNR(GPU lanczos vs CPU lanczos) = 46.60 dB ≥ 40`、
  `VMAF = 97.21`；
  **软解 + `hwupload_cuda` 链已实测**（判据 C/D/E）：crop 尺寸协商正确（640x360）、
  **比软解+CPU缩放快 2.9~3.2%**、画质与零拷贝链逐位相同；
  但**绝对值是 44~46s vs 硬解零拷贝 12.8s** → 定位仍是「NVDEC 用不了时的出路」；
  **10bit `p010le` 下载路径已跑通**（用 HLG 的 new4_raw，1920x1080 `yuv420p10le`，
  零拷贝链 2.058s、B4 `-2` 接受、`DL_FMT=p010le` ✓）——此前"仍空白"的说法已过期；
  ⚠ **PSNR/libvmaf 在 10bit 上失效**（存成 yuv420p10le 后 psnr 协商失败 → 全 N/A）
  → 所有路滤镜末尾统一加 `,format=yuv420p` 降 8bit 再比（8bit 源是空操作，不影响旧数字）；
  ⚠ **恒等缩放陷阱**：new4_raw 源高就是 1080，覆盖链缩放是恒等操作（只裁剪不缩放）
  → B/D/Q/E 全与缩放无关，VMAF 99.98 不是画质结论；探针现在检测到"源高==1080"会给出警示；
  ⚠ **收益是素材相关的，不是分辨率相关的**：同为 4K，Earth 素材净收益 51.9~52.9%、
  new4_raw_4k 只有 **6.4%**（CPU 缩放只占总耗时 6.6%）→ 「4K 就稳赚 50%」不成立，
  跨素材不可比，报数要连时长/帧数一起报；
  **第八轮（2026-09-21，12 组素材）把规律钉死了 =「位深 + 是否真在缩放」**：
  源 ≥10bit **且**真缩放 → 上传链快 **+14~25%**；8bit 真缩放 → **+0.4% ~ −14%**；
  **恒等缩放（无论位深）必亏 −1.6% ~ −34.7%**（10bit 恒等也是 **−22.0%** ← 判决性格）；
  ⇒ 门槛已落地 `_hwupload_skip_reason()`，**只作用于 `--scale-algo auto`**（显式 `cuda-*` 照跑），
  跳过时打印具体理由；判据 `verify/verify_hwupload_worth.py`；
  **工装脚本已转正**：`test/`（回归门 + `baseline/`）、`verify/`、`probe/` 三个目录都在 git 里、
  随 `git pull` 同步 → **"改了探针要手动拷到 T4"的约定作废**；`temp/` 现在只是本机工作目录；
  **必须显式写 `hwdownload,format=…`**：靠 FFmpeg 自动插入时 `crop` 会被**静默丢弃**
  （`scale_cuda=1280:720,crop=iw/2:ih/2` 输出 1280x720 而非 640x360，无任何报错）；
  **`crop_cuda` 在上游并不存在**（不是"6.1 未编译"）→ 策略 1 实际永远跳过、crop 只能在 CPU；
  `crop-cover` 不纳入（要先裁剪，GPU 缩放需额外 `hwupload_cuda`）；
  顺带修掉 `_src_download_fmt` 返回非法 pix_fmt 名（`p010`/`p012` → `p010le`/`p012le`），
  该 bug 此前因唯一调用路径不可达而没暴露；
  **探针自身的 bug 值得记**：判据 Q 的判词用了跨行三元，POSIX awk 不允许在 `:` 前换行，
  T4 的 **mawk** 直接语法错；本机是 **gawk** 所以 SELFTEST 没抓到，而 `set -e` 把后面的
  VMAF 与汇总一起带走了 → 已修 + 给 SELFTEST 加了 `awk --posix` 预解析守卫
- [vidcrop_hwaccel 的三轴模型：--hwaccel 已硬更名为 --decode](project_three_axis_model.md)
  — 2026-09-20 把纠缠的 `--hwaccel` 拆成 `--decode` / `--scale-algo` / `--codec` **三个正交轴**
  + 纯策略开关 `--fallback-policy(auto/strict)`，轴之间**零冲突检查**；
  **三处硬伤**：`none` 顺带关掉 NVENC、`can_cuda_scale` 把 GPU 缩放硬绑在硬解上、
  `nvenc-only` 下漏探滤镜还输出假结论；
  **破坏性变更**：`--hwaccel` 硬删（旧值 `none`≡`cpu`）、三个旧 fallback 值删除并给等价三轴写法、
  **`--decode cpu` 不再等于纯 CPU**；新增「软解 + `hwupload_cuda`」链（自带 device，
  所以 `build_ffmpeg_cmd` 零改动）；`auto` 缩放用**功能探针**判定，显式 `cuda-*` 不探针；
  回归判据 = 16/16 滤镜链 + 默认路径 5 用例逐字 + `verify_decode_axis.sh` 15 项；
  **补（2026-09-21）**：`auto` / `cuda` 的解码判定改成**按源编解码器**（T4 解不了 AV1，
  原来是拿 H.264 探针的全局标志硬套 → 每个 AV1 文件白跑一次必败的链、strict 下误退出 2），
  判据 `verify/verify_cuda_decode_codec.py`
- [像素格式 / 位深 / HDR 三个新参数](project_color_depth_hdr_params.md)
  — 2026-09-20 两脚本都加了 `--pix-fmt`（hwaccel 此前没有）/ `--bit-depth` / `--hdr`，
  默认全 `auto`（不传时命令逐字不变）；
  ⚠ **零拷贝 CUDA 链不能传 `-pix_fmt`**（实测 Impossible to convert）→ 改用
  `scale_cuda=format=` + `-profile:v`，且**紧随的 `hwdownload,format=` 必须同步改**；
  **`tonemap_cuda` 上游不存在** → `--hdr sdr` 走 CPU 的 zscale+tonemap（**未实测**）；
  **`--pix-fmt`×`--bit-depth` =「能落地者赢」**（2026-09-21 修）：先按 `--pix-fmt` 落地，
  它在当前链上不可用时由 `--bit-depth` 接管并明说让位代价（位深/色度，strict 下有损失报错）；
  恒定让谁赢两端都有反例；判据 `verify/verify_pixfmt_bitdepth.py`
- [裁剪产物色度归零（全绿）：元凶是 ffmpeg 自动插入的 auto_scale](project_green_chroma_defect.md)
  — 2026-09-22 定案：`-colorspace smpte170m` 与解码帧 `csp:unknown` 不一致 → ffmpeg 在
  链尾自动插 CPU scale（`auto_scale_0`）→ 硬解 + NVENC 链上把 U/V 清零（下游 YUV→RGB 得
  RGB(0,255,0) 全绿）；**不是** hwdownload / `-hwaccel auto` / NVENC 本身（都是上一轮的错方向）；
  修法 = `setparams` 下发放宽到 GPU 编码器（与 cpu_v2 孪生行为对齐）+「链尾是 CUDA 原生滤镜
  则不追加」守卫；新增产物色度自检 + `--no-chroma-check`（失败自动降级，采样点取
  `clamp(时长×0.1,1,60)` 避开片头空白帧）；顺带修掉探针 6b「最小恢复集」算反（恒报 0 组）；
  探针目标尺寸改为 `OUT_W`/`OUT_H` 并加可裁剪性预检：源 == 目标（crop 恒等）时直接 exit 2，
  不再静默跑出"无产物/空"（拿已裁剪成品跑过一轮，第 2/3 与 4/5/6 节全部失效）；尾判读 ④
  改为按 V0 三分（V0 不绿 → 判「本轮没有复现故障」，不再冤指脚本选项组）；
  判据 `verify/verify_color_tagging.py`、`verify/verify_chroma_hook.py`、
  `test/test_green_chroma_regression.sh`（含"删掉 setparams 必须复现"的红灯自检）
- [按主题拆分同一文件里的两条改动线（test/split_diff_by_theme.py）](project_commit_split_tool.md)
  — 规则驱动（整块覆盖 → 逐行规则 → 关键词 → 沿用上一段），**只出 A 侧补丁**；
  `--verify` 在临时索引上证明「A + 剩余 == 工作区」，`--selftest` 在临时仓库自证；
  ⭐ 自证当场抓到 4 个真 bug（Hunk 段落建太早 / 两条线相邻时 git 只给一个变更组、
  无关键词的续行要沿用上一行 / 无关键词的替换组必须标 `?` 待复核 /
  `@@` 头重算丢换行与计数少算 —— 后两个被 `git apply --recount` 掩盖，
  只有与 `git diff` 逐字节对比才暴露）
- [码率控制轴：`--rc-mode` / `--qp` / `--lookahead` / `--bitrate`](project_rate_control_params.md)
  — 四个参数默认值全部 = **不下发**（不传时命令逐字不变；第三道回归门
  `test/dump_enc_options.sh` + `baseline/enc_before.txt` 钉住）；`-rc` / `-qp` 是
  **NVENC 专属**（非 NVENC 告警忽略、hwaccel `strict` 下报错）；`--lookahead` 按编码器映射
  （x264/NVENC 用 `-rc-lookahead`、x265 走 `-x265-params`、vp9/aom 用 `-lag-in-frames`、
  svtav1 不下发），且**默认值三边不同**（NVENC 0（关闭）/ x265 20 / x264 自定）；
  ⭐ 实测 `-x265-params A -x265-params B` 是**后者整条覆盖前者** → lookahead 必须与
  HDR 元数据合并成同一条（已修，否则静默抹掉 HDR）；`constqp` × `--bitrate`/质量参数报错、
  `vbr*`/`cbr*` 下与质量参数并存（= 受码率约束的恒定质量）；判据 `verify/verify_rc_lookahead.py`；
  **2026-09-24 追加**：把「质量意图 → 目标编码器原生参数」收敛到 `_resolve_quality_params`
  一个点（返回 `(crf, cq, qp)`）。实测修掉四个缺口：`--qp` 降级到 CPU 被**静默丢弃**
  （现换算成 `-crf`）、`--cq 0`/`--qp 0` 降到 libx265 只得 `-crf 3`（现 `-crf 0`+`lossless=1`）、
  **cq 0~5 曾全部算成 `-crf 0` = 真无损**（现非无损换算一律钳到 ≥1）、字面量 `--crf` 落到 NVENC
  被回落默认（现换算成 `-cq`）；**`0` 是无损哨兵**，5 路 0 值输入不走换算表，直接给目标无损档；
  `--rc-mode constqp` 下 `--qp` / `--crf-ref` / `--cq-ref` 三选一（同给报错）；
  判据 `verify/verify_quality_mapping.py`

---

## 待办：还没迁进来的

`/workspace` 侧的 codebuddy 自动记忆里还有以下条目，与 VidUtils 相关但**尚未**迁入
（要么属于别的项目，要么等你确认再动）：

- `project_ffmpeg_gpu_script_gotchas.md` — `install_ffmpeg_gpu_scale.sh` 等安装脚本的坑。
  属于"装 FFmpeg"而不是"用 FFmpeg"，暂留原处。
- `feedback_gpu_testing.md` — 用户的验证顺序偏好（先验证 CPU/软编路径）。
  属于协作偏好而非项目事实，暂留原处。
