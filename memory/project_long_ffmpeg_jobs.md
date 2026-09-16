---
name: 长时 ffmpeg 任务必须脱离会话跑，且不要直接写 MP4
description: codebuddy(node) 崩溃/退出会带走同进程组的 ffmpeg 子进程；直接写 MP4 会因缺 moov 整份作废——长任务要 setsid 分离 + TS/分片输出；另记录 interp_2x_safe 系列的 GPU 专版/通用版分工、时间段截取、片头 trim 造成的音画偏移与对齐、CPU auto 并行度的实测取值与负载感知、相对 -w 的坑，以及"别原位改运行中的脚本"
type: project
---

## 已发生的事故（2026-09-14）

07:10 启动的 4K 2x nvinterpolate 编码（源 Earth.at.Night.in.Color.S02E01.mp4，
`nvinterpolate=fps=48000/1001,trim=start_frame=3,setpts=PTS-STARTPTS` + `-fps_mode passthrough`
+ `hevc_nvenc -preset p5 -cq 25`），07:44 中断，跑了 34 分钟。

产物 `/workspace/input_videos/Earth.at.Night.in.Color.S02E01_2x.mp4` 大小 2.1GB，
box 结构只有 `ftyp` + `free` + `mdat`(size=0 延伸到 EOF)，**没有 moov**
→ `ffprobe` 报 `moov atom not found`，2.1GB 里 0 字节可用，没有可续传的点。

**Why:** `core.171559` 显示崩的是 **`node .../codebuddy -y` 自己**，不是 ffmpeg。
ffmpeg 是会话的子进程，codebuddy 一死就被一起带走；而 MP4 的 `moov` 是编码结束时一次性写的，
进程非正常退出就永远没有。`-movflags +faststart` 也救不了（它同样等编码跑完）。

**How to apply:** 本机任何预计超过几分钟的 ffmpeg 任务：

- 用 `setsid nohup ffmpeg ... &`（或 `setsid bash -c '...'`）**脱离会话**，别让它留在会话的进程组里
  ——这同时规避了 SIGTTIN 挂起和"随会话一起被杀"。
- 输出走 **TS 或 `-f segment`**（TS 无全局索引，被杀前已写入的部分仍可解码），
  最后再 `-f concat -c copy` 汇成 MP4。分片用 `-g` 控制 GOP 让切点可预测
  （`-force_key_frames` 在 nvenc 上不生效，实测切点会落在默认 GOP 250 帧）。
- 音频不要分片：视频分片时加 `-an`，收尾从原片一次性 `-c copy`（音频本来没改）。
- **不要直接写 MP4**，哪怕是一次就能跑完的任务。

现成实现：`/workspace/VidUtils/interp_2x_safe.sh`（4K 2x nvinterpolate 用；`setsid` + TS 分片 +
已存在分片跳过 + 收尾 concat/`-c copy` 音轨）。2026-09-14 实测：分片接缝无重复帧
（PSNR 47–51dB，与单次跑一致）、重跑幂等、成片 hevc 48000/1001 + 原 5.1 AAC 正确。

**2026-09-15 起该脚本又加了两件事**（细节见脚本注释与 README 对应小节）：

- **环境自动探测**：CPU 先看 cgroup 配额（本机 `nproc=8` 而 `cpu.max=200000/100000` 只有 2 核）、
  内存同样优先 cgroup（本机 4GB 配额 vs `/proc/meminfo` 的 32GB）、GPU 型号/显存/利用率/占用进程、
  磁盘余量、以及是否还有别的 `interp_2x_safe.sh` 在跑。探测结果直接决定并行度。
- **分片级并行 `-j N`**（`--jobs/--workers`，`--threads`、`--mem-per-job`、`--sequential`）：
  分片本来互不依赖。`auto` 时 CPU 后端按 CPU 槽位与内存算，**GPU 后端固定 1** ——
  依据 T4 实测的 NVENC 单引擎（1/2/4/8 路并发总吞吐基本不变）。显式 `-j N>1` 会先做 N 路并发试编码。
  并行度不影响分片内容，所以**不进 `recipe.txt`**（改 `-j` 复用旧目录照旧 skip）。
  四个 bash 调度陷阱见 [bash 并行调度的四个坑](project_bash_parallel_pitfalls.md)。

**CPU 回退后端（`minterpolate` + `libx265`）的一个坑**：`minterpolate` 吐不出最后一帧
（没有下一帧可插），撞到 EOF 时输出凭空少 3 帧 —— 整片单跑也一样（8s/24fps 源应 384 帧实得 381）。
已在滤镜串前置 `tpad=stop_mode=clone:stop_duration=<2 个源帧>` 补克隆帧、多出的由 `-t` 裁掉。
另外 `minterpolate` 的 `fps` **只吃具体数值**，写 `source_fps*2` 会报
`Unable to parse option value … as video rate`（`nvinterpolate` 则支持该写法）。

回归测试：`/workspace/VidUtils/test_interp_2x_lock.sh`（80 项断言，退出码 0/1/2；
`SUT=` 指向变异版可验证它确实能抓到锁失效）。用独立 mktemp 工作目录，
可以在正式任务跑着的时候执行。耗时取决于**实际后端**：GPU 后端几分钟，
CPU 后端（本机 cgroup 配额 2 核）实测约 **15 分钟** —— 它要反复编解码十几遍 10 秒素材。
断言分两类：日志断言（看脚本打印了什么）与
**事实断言**（分片+meta 指纹 / 文件是否真从盘上消失 / 成片帧数）——后者不依赖脚本的
自我报告，能抓到"日志说做了、实际没做"。

脚本里那套防呆值得照搬（都是踩出来的）：
- **单实例锁**：`$WORKDIR/.lock` 上 `flock -n`，且**放在覆盖检查之前**（否则并发时
  第二个实例会先撞上"目标文件已存在"这种与并发无关的提示）。
- **两层复用校验**：`recipe.txt` 记 `slice|backend|enc|in|L|trim`（这些一变，每一片内容
  都不同 → 整体拒绝；GPU/CPU 两套后端的分片画面不同，所以 `backend`/`trim` 也在指纹里）；
  每片另有 `.meta` 记 `ss dt`（总时长只影响末尾那一片 → 只重编
  边界片，不整体拒绝）。**只影响速度的参数（`-j`/`--threads`/`--mem-per-job`）不进指纹** ——
  否则换个并行度就要整体重编。
- **临时文件名带 PID** + 持锁启动时清理历史残留（`p*.ts.part.<pid>`）。
- **`--overwrite` 门控目标文件**，且尽早退出（别跑 50 分钟才在收尾发现）。

**并发是这类分片任务的隐藏杀手**：两个实例共用一个分片目录时，循环开头的
`rm -f $tmp` 会删掉对方正在写的临时文件（ffmpeg 仍往已 unlink 的 inode 写），
先跑完的把临时文件 `mv` 走、后跑完的就报
`mv: cannot stat '.../p00002.ts.part': No such file or directory` —— 两边都白跑。
所以分片脚本必须在工作目录上放 `flock` 单实例锁，且临时文件名带 PID。

踩到的两个点：
- `-t` 是**输出**选项时 ffmpeg 会自己补足时长，所以每片剪掉片头 3 帧后仍严格是 L 秒，
  且相邻片内容天然连续 —— **视频侧**不要再手工做 ±offset（早期补 0.0625s 反而把帧数算多）。
  但音轨侧要补，见下面「片头 3 帧的代价」一节。
- 收尾拼接的临时文件名要保留 `.mp4` 后缀（`$OUT.part` 会让 ffmpeg 认不出容器，
  报 "Unable to choose an output format"）。

## 片头 3 帧的代价：接缝不跳，但整条视频超前 3 个输出帧（2026-09-15 实测 + 已修）

GPU 侧每个分片都要 `trim=start_frame=3` 丢掉 nvinterpolate 的无效前导（前 3 个输出帧是同一张
画面重复：实测对源第 1 帧 ≈44.6dB、对第 0 帧只 20dB，第 4 帧才是真插值帧；CPU 的
`minterpolate` 无此前导，所以 `trim=0`）。**每个分片是一次独立 ffmpeg + 独立滤镜实例，
所以每片都要裁**——这也是 `trim` 必须进 recipe 的原因。

**结论 1：接缝处不会跳、也不会"每缝丢 3 帧"。** 实测（4s / 25→50fps）：

- 一次跑完 4s = 200 帧；两片各 2s 再 concat = 200 帧；
- 逐帧比：**同帧号对齐平均 43.3dB / 最低 35.9dB**，平移 3 帧对齐只有 22.2dB → 一一对应；
- 接缝那一帧 39.4dB（两侧 43–45dB），略低只是它属于另一次编码，不是内容跳。
- 原因：3 帧前导对每片**都一样**，`-t` 又把每片补满 L 秒 → 第 i 片的结束内容正好等于
  第 i+1 片的开始内容；偏移是**常数、不累积**。

**结论 2：代价是"整条视频内容比时间戳超前 HEAD_TRIM / 目标帧率 秒"**（48000/1001 下 3 帧
= 62.56ms）。实测：成片第 0 帧 ≈ 源 60ms 处（对 r1/r2 各 22.2dB、对 r0 只 18.2dB）；
成片第 100 帧(2.000s) ≈ 源 2.060s（对源 #51/#52 各 ~20dB）。音轨是原样 copy、时间戳没动，
于是**画面比声音超前同样多**。

**How to apply:** 收尾给音轨 copy 的起点补上这一段：`-ss SEG_START + HEAD_TRIM/目标帧率`
（脚本里是 `AUD_SS_OPTS`，在**后端装配之后**计算，因为 `HEAD_TRIM` 由 setup_gpu/setup_cpu 决定；
CPU 侧 HEAD_TRIM=0 → 与旧命令逐字节一致）。只动音轨起点、不动任何分片、不进 recipe，
所以**已跑完的成片用新脚本重拼一次即可对齐**（分片全 skip）。
端到端验证（源：1.000s 处同时一帧白 + 一声 1kHz click，PCM 音轨避开 AAC 延迟）：

| | 白帧 | click | 偏差 |
|---|---|---|---|
| 修复前 GPU | 0.9375s | 1.0000s | **62.5ms** |
| 修复后 GPU | 0.9400s | 0.9400s | **0.0ms** |
| 修复后 CPU | 1.0000s | 1.0000s | 0.0ms（无偏移，符合预期） |

**踩坑记录**：第一版把 `HEAD_SHIFT` 算在了 `SS_OPTS` 旁边（后端装配之前），v1 直接
`HEAD_TRIM: unbound variable` 退出（`set -u`）——v1 的 `HEAD_TRIM` 是在 `setup_gpu`/`setup_cpu`
里才赋值的。凡是依赖"后端属性"的量，都必须放在后端装配之后。

## 中断收尾：孤儿 ffmpeg 与"按了 Ctrl+C 还在跑"（2026-09-15 修）

两个现象是同一个根。

**现象 1：按下 Ctrl+C 后任务还在跑。** 两层原因：

1. Ctrl+C 只发给**终端的前台进程组**。脚本自己推荐用 `setsid ... &` 起长任务（让终端/宿主
   死掉带不走它）—— 那种情况下 Ctrl+C **完全收不到**，这是设计行为不是 bug。
   判断办法：按了 Ctrl+C 若**提示符立刻回来**，说明任务根本不在前台进程组。
2. 即使收到了，**ffmpeg 捕获了 INT/TERM 但要 11–13s 才真的退出**：实测本机 ffmpeg 7.1，
   `/proc/<pid>/status` 的 `SigCgt` 里 SIGINT(2)/SIGTERM(15) 都注册了，但连 640x480 的
   lavfi → mpegts 都要 **11.4s / 13.1s** 才退出（4K minterpolate 只会更久）。旧代码
   `kill -TERM` 之后就 `wait`，整条链得等它自己走完 —— 看起来就是"按了没反应"。

**现象 2：脚本被 `kill -9` 后留下还在烧 CPU 的 ffmpeg（孤儿）。** 走不到 trap；而且 run_job
是**子 shell**，`$!` 记的 pid 只活在它自己里 —— 子 shell 被打死、或信号正好落在
"`&` 启动 ffmpeg" 与"装 trap"之间那一瞬间，pid 就没人知道了。

**修法（两个脚本都改了）：**

- 不再依赖 `$!`：按「**命令行里引用了本 WORKDIR 的 parts/ 目录**」找进程（读
  `/proc/<pid>/cmdline`，且只认 argv[0] 以 ffmpeg 开头的）—— 本目录有单实例锁，不会误伤别人。
- `on_signal`：杀完子 shell 之后，再 `stop_ffmpeg_writers "中断"`：`TERM → 最多 3s → SIGKILL`。
  3s 是刻意的：优雅停止既然要十几秒、而被中断的 `.part` 反正要丢弃，没必要为它等。
- **启动自愈**：拿到锁之后立刻 `stop_ffmpeg_writers "残留"` 收掉上次留下的孤儿，**然后**才清
  `.part`（先杀进程再 rm —— 否则孤儿会继续往已被 unlink 的 inode 写，现象是"文件删了体积还在涨"）。
  这一步放在**覆盖检查之前**，否则「目标文件已存在」这种早退会把孤儿留在那儿烧 CPU。
- 判活要看 `/proc/<pid>/stat` 的 state：**僵尸不算活着**（`kill -0` 对僵尸返回成功，会把已退出
  的进程当成"还在跑"，白等 3s 还打一条误导的「超时」日志）。

- **根因：锁的 fd 被泄漏给后代（已修）**。`exec 9>"$LOCK"` 在脚本本体里打开 fd 9，而 run_job 的
  子 shell 与它启动的 ffmpeg **都继承了这个 fd** → 脚本被 `kill -9` 后，孤儿 ffmpeg 自己就是锁的
  持有者：下次同 `-w` 启动在 `flock` 那一步直接撞锁，**"启动时清孤儿"那段代码永远走不到**。
  实测事故正是如此：用户 09:34/09:35 两次重跑都报"另一个实例正在跑"。
  修法：在 run_job 子 shell（以及试编码的 subshell）里 `exec 9>&-` —— **锁只留在脚本本体手里**，
  脚本一死锁立刻释放，孤儿就退化成"没爹的 ffmpeg"，交给启动时的 `stop_ffmpeg_writers "残留"` 收拾。
- **撞锁时的自愈兜底（为了收拾老版本留下的残留）**：分类持有者 ——
  有非 ffmpeg 的持有者（真实例，或它没退干净的 run_job 子 shell，命令行与脚本一模一样）
  → 报错退出并打印 pid / 启动时间 / 命令行（也顺带解决"为什么还在跑"的排查）；
  **只有 ffmpeg** → 认定孤儿：先收掉它们，再用 `flock -n 9` 重试接管锁（最多等 5s），然后继续。
  顺序不能反 —— 必须先夺锁成功才谈得上清孤儿。
**验证**：造一个"正在写 `$W/parts/` 的 ffmpeg"当孤儿 → 启动时日志
`残留: 还有 1 个 ffmpeg 在写 … → 先发 TERM` → 3s 后 `超时 → SIGKILL` → 孤儿没了、`.part` 被清，
且脚本自己的编码不受影响（2 片、100 帧正常出片）。SIGTERM 路径：收紧 grace 前 5.3s、收紧后 3s 内归零。

- **并发度还要看"实际要编几片"**：`NPARTS` 是**总**片数，但复用（skip）之后真正要编的只剩
  `TODO` 片。扫描待办清单之后再收敛一次：`JOBS = min(JOBS, TODO)`，并把 `THREADS` 回算
  （活少了就给每片多分线程）—— 所以把 `THREADS/THREAD_OPTS` 抽成了 `set_threads_from_jobs()`。
  日志：`并发  : 只剩 2 片要编（共 8 片，其余复用）→ 由 4 路收敛为 2 路、每片 4 线程`。
- **撞锁时报持有者**：`lock_holder_pid` 扫 `/proc/<pid>/fd/*` 找指向本 `.lock` 的进程，报
  `pid + 启动时间 + 命令行`。占锁的未必是"另一个实例" —— 也可能是**上一个实例没退干净的
  run_job 子 shell**（子 shell 继承父进程的 fd 9，脚本本体没了锁照样不放）；实测就撞到过这种。
- **新建 `test_interp_2x_orphan.sh`**（与 lock 测试并列）：用例 = 启动自愈 / 收尾有界 / 真孤儿自愈；
  拿不到前置（素材太短、后端太快抓不住"在飞的分片"、锁被子 shell 占着）一律记 `SKIP`，不会误判通过。
  实测两个 SUT 都通过（13 通过 / 0 失败）。
> 复现/测试时的两个坑：① `ps -o pid= --ppid` 在本环境**不可靠**（返回空），枚举子进程要直接读
> `/proc/*/status` 的 `PPid`；② run_job 子 shell **继承了锁的 fd 9**，所以只要有子 shell 活着，
> 锁就一直被占 —— 想造"真孤儿"必须把脚本连同它所有非 ffmpeg 子进程都杀掉，否则下次启动会先撞锁。

## 相对 `-w` 会失败（既有坑，2026-09-15 已修）

症状：`-w ./work` 这类相对路径下，分片 ffmpeg 报
`Error opening output work/parts/pXXXXX.ts.part.NNN: No such file or directory`。

**Why:** `PARTS="$WORKDIR/parts"` / `RUNLOGS=...` 这些**在 WORKDIR 绝对化之前**就赋值了，
而每片会 `cd` 进自己的临时 CWD（`runlogs/cwd/<片名>/`），那一刻相对路径就失效了。
脚本所有示例与回归测试都用绝对 `-w`，所以这个坑一直没暴露。

**How to apply:** 路径类变量必须在 `WORKDIR=$(cd "$WORKDIR" && pwd)` **之后**再拼
（已把 `PARTS`/`LIST`/`RUNLOGS`/`CWDROOT`/`EVENTS`/`PROBE_ERR_FILE` 整个块搬到绝对化之后）。
同理：`HEAD_SHIFT` 依赖 `HEAD_TRIM`（后端属性），必须放在后端装配之后——我第一版放错了，
v1 直接 `HEAD_TRIM: unbound variable` 退出。

## CPU 并行度：每路按 1 核 + 感知已有负载 + `--threads` 的真相（2026-09-15/16 三轮收敛）

**1) 先说结论：CPU 后端真正有效的并行杠杆是 `-j`（多片并发），不是 `--threads`。** 两个独立的失效点：

- **`-filter_complex_threads` 对 `minterpolate` 完全无效**：给 1/2/8 的 wall 时间一样
  （1080p 单分片：35.9 / 36.8 / 36.1s），并行度都是 **1.09~1.12 核** → 这个滤镜是**串行**的。
- **`-threads` 对 libx265 只改 frame threads，不改线程池**：`-threads 1/2/8` 的并行度
  2.92/2.93/3.09 核、CPU 时间都是 ~5.25s；`-threads 1` 时 x265 仍打印
  `Thread pool created using 8 threads`，只有改 `frame threads` 那一行。真要限 x265 得用
  **`-x265-params pools=N`**（实测 `pools=2` → 2.28 核，`pools=1` → 1 核）。
- 脚本真实形态（tpad+minterpolate+libx265 medium）：fct/thr = 1/2/8 → wall 69.4/66.3/70.1s，
  并行度 1.22 核 —— 端到端一样无效。
- **按线程采样的现场证据**（最直观）：一个在跑的 4K minterpolate 片有 **25 个线程，其中只有
  1 个满转（1.01 核）、其余 24 个 ≈0**，全进程合计 **1.01 / 8 核**。

**2) `CPU_PREF_THREADS` 因此收敛到 1**（4 → 2 → 1）：单片 ≈1 核，所以按 1 核/路把可用核铺满，
4 核配额 → 4 路、8 核 → 8 路；真正的上限通常来自**内存画像**（4K 按 4GB/片 → 21GB 可用 ≈ 5 路）。

**3) `--threads` 现在会被翻译成 `-x265-params pools=N`**（新增 `build_cpu_enc_opts`）：
它必须在 **JOBS/THREADS 定下来之后**拼 ENC_OPTS —— 但 `setup_cpu()` 是在并行度之前调用的，
所以 `setup_cpu` 只铺一版"没有 THREADS"的，`set_threads_from_jobs()` 末尾（CPU 后端）再重拼一次。
`--threads 3 -j 2` → 命令行实测 `-threads 4 -filter_complex_threads 4 … -x265-params pools=4:log-level=error`。
**注意**：`-filter_complex_threads` 照发但无实际作用，保留它只是为了"预算写清楚"。

**4) auto 会感知"机器已经有别的负载"**：采样本 cgroup 的 `cpu.stat`（0.4s，
`delta(usage_usec)/delta(微秒)` = 该 cgroup 内所有进程实际消耗的核数），从可用核里扣掉：

```
CPU_SLOTS = max(floor((CGROUP_CORES - USED_CORES) / CPU_PREF_THREADS), 1)
```

- **不要用 `/proc/loadavg`**：容器里它报的是**宿主**的负载（和 `nproc` 报宿主核数是同一个坑）。
- 实测：有别的 4K 任务在跑（≈2.3 核）时 → `floor(5.7/1)=5 路` 会被内存夹到更小；
  空闲时按 cgroup 核数铺满。
- 取不到 `cpu.stat`（老 cgroup / 非容器）就退化成"不看负载"，即旧行为。
- 启发式的量终究是启发式：想要更满就显式 `-j`（脚本对 `-j` 只做试编码可行性检查，不拦）。

**5) `MEM_PER_JOB_GB` 画像复核（按实测改）**。用 `/usr/bin/time -v` 的
`Maximum resident set size` 量单分片（`-threads 4`、libx265 medium、脚本同款滤镜串）：

| 分辨率 | 实测峰值 RSS | 旧画像 | 新画像 |
|---|---|---|---|
| 1280x720（`new5_10s.mp4`） | 0.61 GB | 0.6 | **0.7** |
| 1920x1080（合成 + 真实 `new5_raw.mp4`） | 1.16 / 1.17 GB | 1.2 | **1.3** |
| 3840x2160（Kangaroo 4K） | **4.59 GB**（`pools=1` 时 4.25） | **4.0（偏低 ~15%）** | **5.0** |
| 8K（未测，按像素外推） | ≈18 GB | 8.0 | **18.0** |

拟合式 `m ≈ 0.11 + 0.54 × 百万像素`（1080p→1.23、1440p→2.10，与旧值吻合；只有 2160p 那格偏低）。
新值取「实测 × ~1.1」。**这一格直接决定 4K 能开几路**：8GiB 的 cgroup 上
`floor(5.57/5.0)=1` 路（内存定死），大内存机器上 32GB → `floor(25/5)=5` 路而不会超。

> 环境提醒：这台机器的 cgroup 在本轮从 **8 核/32GB 被缩到 4 核/8GiB**（`cpu.max=400000/100000`、
> `memory.max=8589934592`）。所以脚本日志里的"CPU 核 N / MEM 总 N"会跟着变，看 `-j` 结果时先看这两行。

**踩坑**：这轮排查里 `ps -o pid= --ppid` 在本环境会返回空、`pgrep -f` 会把"命令行里含该模式"
的自己匹配进去 —— 判断进程/线程一律读 `/proc`（`/proc/<pid>/status` 的 PPid/Threads、
`/proc/<pid>/stat` 的 state/utime+stime、`/proc/<pid>/fd` 找锁的持有者）。

## 时间段截取（`--SS` / `--TO` / `-T`，2026-09-15 加）

只做源视频的一段：`--SS` 起点、`--TO` 绝对终点、`-T` 时长，三者都吃秒数（90 / 90.5）
或 `HH:MM:SS[.ms]` / `MM:SS`。这几个只认命令行（`-T` 太大路货，不给同名环境变量）。

- **`TOTAL` 的语义变了**：从"源总时长"变成"片段时长"。下游（分片数、每片 `dt`、收尾 `-t`、
  进度）用的都是片段时长；每片的 `-ss` 是**绝对时间戳** = `SEG_START + 片内偏移`。
  收尾音轨 `-c copy` 的起点 = `SEG_START + HEAD_TRIM/目标帧率`（**输入选项**，放在
  `-i "$IN"` 之前）——比视频的 SEG_START 多出的那一截是为了对齐片头被裁掉的 3 帧，见上文。
- **谁进 recipe、谁进 `.meta`，判据是"会不会改变每一片的内容"**：
  `--SS` 平移所有片的 `-ss`（片 0 也变）→ 跟 `L` 同性质，进 `recipe.txt`（追加 `|ss=`，
  **只在 >0 时追加**，这样 `--SS` 为 0 时 recipe 与旧版逐字节一致、在跑的长任务不会被整体拒绝）；
  `--TO` / `-T` / `--cap` 只动**末尾那一片**的 `dt` → 交给每片 `.meta` 逐片判断，只重编边界片。
  同理 `.meta` 记的是绝对 `ss`；为了与旧版整数格式（`0` / `300`）逐字节兼容，格式化时去掉尾零。
- `--cap` 语义不变（空/0 = 不裁剪；>0 = 片段时长，从 SS 起算，即等价 `-T`）。
  `--TO` / `-T` / `--cap` 都描述终点，**同时给两个以上直接报错**，绝不静默取一个。
- 边界：终点超过源末尾 → 夹到末尾（不报错，与旧 `--cap` 一致）；
  `--SS` 已到/超过源末尾 → 报错（没有可处理的画面，早点说比跑出空片好）。

## 两个脚本的分工（2026-09-15 定）

`interp_2x_safe_v1.sh` 是**通用版**，`interp_2x_safe.sh` 是**GPU 专版**（由通用版去掉 CPU 回退派生）。
两者共享其它全部特性：cgroup 感知的环境探测、分片级并行 `-j`、时间段截取 `--SS/--TO/-T`、
事件文件驱动的并行调度（每片独立日志 + 独立 CWD + trap 收子进程）。

- GPU 专版**没有** CPU 回退，所以没有 `--backend` / `--cpu-preset`（也没有只服务 CPU 自动并行度的
  `--mem-per-job`）；试编码不过就直接报错退出，并在错误里指路"要回退请用 v1"。
  `-j auto` 固定 1（T4 NVENC 单引擎实测）。
- **两者的 `recipe.txt` 完全兼容**：都用 `slice=3|backend=gpu|enc=hevc_nvenc|<preset>|cq=<n>|in=…|L=…|trim=3`
  （+ 可选 `|ss=`），所以**同一个分片目录可以被两者互相接管**。更早的 GPU-only 版本写的旧格式
  （`slice=3|in=…|L|preset|…|cq|…|trim=…`，无 `backend`）会被就地升级而不是整体拒绝 ——
  这样脚本升级不会让已在跑/已跑完的长任务被迫重编。
- 回归测试 `test_interp_2x_lock.sh` 默认 `SUT` 就是 GPU 专版的 `interp_2x_safe.sh`：
  2026-09-15 用它跑 **80 项断言全过（0 失败 0 跳过，76s，GPU 后端）**，含 `-j 2` 并行等价性、
  换 `-j` 不触发重编、补单片、SIGKILL 后重跑等。

## 收尾 concat 的 "Non-monotonic DTS" 告警：已修（2026-09-15）

现象：收尾 `-f concat -c copy` 时打印

```
[vost#0:0/copy @ ...] Non-monotonic DTS; previous: 1792416, current: 1792414;
changing to 1792417. This may result in incorrect timestamps in the output file.
```

**根因（实测）**：concat 解复用器在 `parts.txt` 只写 `file` 行时，只能从上一片 TS 的结尾
**推断**它的时长 —— 而 TS 结尾时长本身就是推断值。一旦某片比相邻片少 1 帧，下一片就会
提前约 1 帧落位。实测触发条件 `-T 100 -L 20`：

- `p00000.ts` 时长 19.978278s（**958** 帧），`p00001..p00004.ts` 都是 19.999133s（**959** 帧）
  （各片 PTS 起点相同，都是 mpegts 默认的 1.462567）；
- 于是第 1 个接缝处（20s 附近）出现 **11µs** 的畸形帧间隔（正常帧间隔 20.85ms），
  全部 4 个接缝里**只有这 1 处**。
- 对照：改动前生成的 S02E05（`L=300`，各片等长 300.008s）**0 处**。所以它是"某片比邻片少 1 帧"
  时才出现，不是普遍现象；`-L` 小只是让接缝变多、更容易撞上。
- 影响面：ffmpeg 会把 DTS 顶成单调，成片 DTS 单调、无丢帧/重复，总帧数照旧正确
  （`-T 100` 得 4795 帧 = 100s × 48000/1001，脚本那行"参考值 4796"只是取整偏低）。
  残留影响 = 该接缝之后视频时间轴前移约 1 帧（≈21ms），肉眼不可见。

**How to apply:** `parts.txt` 里每个 `file` 后面补一条 `duration <该片 dt>`，让解复用器
有精确的推进量。dt 与逐片编码用同一算法（末尾片 = 剩余时长），所以 `sum(dt) == TOTAL`。
实测**同一批分片**重拼：告警 0 次、畸形间隔 0 处、帧数不变（4795）、体积不变。
只改拼接清单，不动分片、也不进 recipe —— 所以老目录可以直接用新脚本重拼（分片全 skip）。

## 长任务在跑时，别"原位"改它正在执行的脚本（2026-09-15 实测）

给正在被执行的脚本（当时是 `interp_2x_safe_v1.sh`）做**原位**编辑（同 inode 重写），
而另一个 4K 任务正在跑同一个文件时，会危及那个任务。

**Why:** bash **不会**把脚本整体读进内存 —— 它执行完一个顶层复合命令（比如那个 `while`
分片循环）后，会**按文件字节偏移继续往下读**。原位改动让文件长度/偏移变了之后，
接着读到的就是错位内容。实测：把"循环前面多出 N 字节"的脚本交给正在跑的 bash，
它会先报一行 `…: command not found`，然后**把已经跑完的循环又重跑一遍**。

**How to apply:**

- 改这类脚本前先确认没有实例在跑（`ps -o args | grep <脚本名>`；脚本自己也有"另一个实例"探测）。
  已经在跑就**不要原位改**：先把改前版本 `cp` 出去、让在跑的任务继续用旧文件，或等它结束 / 干净地停掉。
- 恢复成本很低，不必慌：分片都在，原样重跑即从断点续。而且 `--SS=0` 时**新脚本生成的
  `recipe.txt` 与旧版逐字节一致**，所以就算任务被打断，也能直接用新脚本接着跑。

## 遗留

- 那份坏的 2.1GB 文件已于 2026-09-14 删除。
- 但 08:17 执行过 `aliyunpan upload Earth.at.Night.in.Color.S02E01_2x.mp4 /temp`，
  **云端 `/temp` 里那份同样是不可读的**，将来若要用需重新上传正确成品。
- 用户当时决定**不重跑**这次 2x 转码。
