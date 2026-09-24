---
name: 复刻 ls 版式与视频属性探测的踩坑
description: vidls（ls/ll 替代 + 视频属性探测）背后的事实：coreutils 列算法的三条真判据（含 2026-09-17 用 1040 组样本推翻的「列宽下限 3」）、Tab 填充的取舍规则、ffprobe 无 -hwaccel/-nostdin、帧数四档来源、Windows 移植的六处必须不同
type: project
---

`vidls.sh` / `vidls.py` 是 `ls` / `ll` 的替代品（Windows 版是 `vidls.cmd` /
`vidls_win.py`）；另有 **`vidll`（Linux `vidll.sh` / Windows `vidll.cmd`）= `vidls -l`**，
两者只做参数转发、不含实现 —— 非视频按原生 `ls` 版式渲染，视频行追加属性列。
下面都是实测出来的事实，改版式或换探测手段前先看这里。

## 一、coreutils `ls` 的列布局（想逐字节一致就必须照抄）

**三条真判据**（2026-09-17 在 Windows + MSYS coreutils 8.32 上用 1040 组随机布局验证，
全部逐字节一致）：

1. **竖着填**（column-major）：`rows = ceil(N / cols)`，第 `c` 列放 `[c*rows, (c+1)*rows)`。
2. `总宽 < 终端宽`（**严格小于**），其中 `总宽 = sum(列宽) + 2*(cols-1)`。
3. **最后一列不能是空的**，即 `(cols-1) * rows < N`。
   **Why:** 这条极容易漏。30 个单字符名字、宽 80 时，16 列的总宽是 45 < 80 看似能放，
   但 rows=2 会让最后一列一个条目都没有，ls 会退到 15 列。实测 100 个双字符名字、
   宽 200 → 50 列（不是 51）。漏了这条就会与 ls 不一致。

- **列宽 = 该列显示宽最大值，没有「下限 3」**。`a  b  c` 的间隔就是 2，不是 4。
  **Why:** 原先两份实现里都写了 `MIN_COLUMN_WIDTH = 3` 并在每列取 `max(3, w)`，
  这是**错的** —— 短名字目录会比 ls 多出两格空白（实测 `a  b  c` 而非 `a    b`）。
  **How to apply:** 2026-09-17 起 `vidls.py` 与 `vidls_win.py` 都按实测重写了，两边一致；
  以后想加「最小列宽」前先用单字符名字目录对一次 `ls`。
- **列间填充用 Tab 跳制表位**（8 的倍数），但取舍有讲究：
  制表位个数 `n = to//8 - from//8`，**只有 `n < last_stop - from` 时才用 Tab**
  （`last_stop` 是最靠近 `to` 的那个制表位），否则整段用空格。
  **Why:** `from=7, to=15` 时「1 Tab + 7 空格」与「8 空格」字节数相同，ls 选空格；
  漏掉这个判据就会多吐一个 Tab（字节数一样但 diff 全红）。用 629 个受控样本拟合出来的；
  触发点很窄（`from % 8 == 7` 且只够一个制表位），所以长期没被发现。
  **How to apply:** 两份实现现在都是这条规则（Linux 版 2026-09-17 回修）。
  这种「字节数相同但内容不同」的坑，**只靠目视和少量样本抓不到，必须随机化布局 + 大量对拍**。
- 非 TTY 时 `ls` 会退化成每行一个；`vidls` **故意不退化**（仍按 80 列铺网格），
  这样管道里也是紧凑的。已确认这是有意偏离。
  **How to apply:** 想把 ls 拉成固定宽度做对比，用 `ls -C -w N`（非 tty 也强制多列与列宽），
  然后 `COLUMNS=N vidls`，两边都进管道即可逐字节 diff。
- `os.get_terminal_size()` 在 pty 没设尺寸时会返回 **columns=0**，必须当成失败回退到
  `$COLUMNS` / 80，否则整个布局退化成每行一个。

## 二、`-l` 长格式的细节

- 表头是 `total N`（`N = sum(st_blocks) // 2`）。跟随 locale 输出 `total`（本机 `LANG=en_US.UTF-8`）。
- 月份缩写要用 `strftime("%b")` 并 `setlocale(LC_TIME, "")`，与 `ls` 共用同一套 locale 数据；
  硬编码 `Jan/Feb/...` 在非 C locale 下会与 `ls` 不一致。
- `-h` 人类可读：base 1024、**向上取整**（coreutils 是 ceiling），`<10` 保留 1 位小数
  （`3000 → 3.0K`，`88356 → 87K`）。
- 时间：半年内（含未来时间）→ `%b %e %H:%M`，否则 → `%b %e  %Y`（两个空格）。
- **操作数本身也要排序**：`ls dir_a /tmp/emptydir` 里 `dir_a` 在前 —— GNU ls 会连命令行
  参数一起按当前排序规则排，不是按书写顺序。

## 三、帧数四档来源（差异很大，必须带标签；**帧数是可选列**）

> 2026-09-17 起：帧数**默认既不显示也不计算**，要看必须 `--show frames` / `--show-all`。
> **Why:** 它是唯一需要解码（或至少解复用）的列，默认算它就是让「随手列个目录」
> 白付解码代价；拆成可选列后裸 `vidls` 只剩一次 ffprobe。
> **How to apply:** `--cpu` / `--fast` / `--deep` 都只是「帧数怎么算」的开关，
> 没请求帧数列时**提示并忽略**（提示走 stderr，退出码不变）；
> 门控点有两个 —— `probe_video` 里的降级链、`main` 里的并发封顶（`may_decode`）。

| 档 | 标签 | 手段 | 成本 / 可信度 |
|---|---|---|---|
| 1 | 包头 | 容器头 `stream=nb_frames` | 免费；mp4/mov 精确，**mkv/webm 常常是 N/A** |
| 2 | 硬解 | `ffmpeg -c:v <codec>_cuvid … -f null - -progress pipe:1`，取最后一行 `frame=N` | 整片解码（GPU）；最准 |
| 3 | 包数 | `ffprobe -count_packets -show_entries stream=nb_read_packets` | 只解复用不解码；CPU 侧降级手段 |
| 4 | 估算 | `duration × fps` | 免费兜底（实测 2s×25fps 估出 51，真值 50） |

- **`ffprobe` 没有 `-hwaccel` 选项**（6.1.1 实测：`Failed to set value 'cuda' for option
  'hwaccel': Option not found`），`-nostdin` 也不认（`Option not found` 后 rc=1）。
  **Why:** 最初按「ffprobe -hwaccel cuda -count_frames」写，结果是**每次必失败**，
  默默全落到包数档，看起来"降级正常"其实硬解档从没跑通过。
  **How to apply:** 硬解数帧只能用 ffmpeg；防挂起靠 `stdin=DEVNULL` 而不是 `-nostdin`。
- 硬解初始化失败时 **ffmpeg/ffprobe 仍可能返回 0**，不能只看 returncode，要扫 stderr 里的
  CUDA 关键字表（含 `operation not permitted`）。命中后本进程不再重试 GPU。
- 硬解探针必须用 `testsrc2` + `-pix_fmt yuv420p`：`testsrc` 默认 4:4:4，硬解必挂
  （与 `vidcrop_hwaccel.py` 同源的坑）。
- `-select_streams` 给**裸数字 = 全局流索引**。用 `v:0` 会在有封面流（`attached_pic`，
  也是 `codec_type=video`）时选到封面 → 数出 1 帧。

## 四、其它坑

- `-h` 与 argparse 默认的 help 冲突：`add_help=False` + 显式 `--help`，否则 `-h` 被抢走。
  同理 `--all` 被 `ls` 的「显示隐藏文件」占用，追加全列只能叫 `--show-all`。
- 探测失败的行**不能参与列宽计算**：错误串很长会把整张表撑爆（实测 `不可探测（...）`
  让分辨率列变成 60+ 宽）。
- `-l` 行尾接属性列时，**head 不能 `rstrip()`**：名字是按显示宽补齐过的，rstrip 会把补齐
  空格一起削掉，于是每条视频的属性列都从自己名字后面开始 —— 名字长短不同就整块歪掉
  （实测 `clip.mkv` 与 `中文测试影片.mp4` 相差 12 列，肉眼一眼能看出没对齐）。
  正确做法：head 保留补齐再拼 `  {cells}`；只有不带属性列的普通文件行才 rstrip。
- `-l` 里视频行的名字补齐宽度用**视频自己的最大名宽**，不是全表名宽 —— 否则目录里出现一个
  超长普通文件名，就会把整块属性列推到屏幕外面去（实测 60 字符的 .log 把属性列推到第 108 列）。
  普通文件行仍按全表名宽（它的名字是最后一列，rstrip 后不影响）。
- `format_name` 是一串同族容器，不能直接取首位：`.mp4`/`.mov` 都报
  `mov,mp4,m4a,3gp,3g2,mj2`，`.mkv`/`.webm` 都报 `matroska,webm` → 要按扩展名反查。
- 探测并发：只对视频条目开线程池（非视频只要 `lstat`）；并发度按 cgroup 感知的 CPU/内存算，
  每路 ffprobe 预留 0.1GB；**深解档额外封顶 4 路**（多路 NVDEC 会抢显存）。
- 资源探测行只在 `-v` 时打 stderr —— `ls` 替代品默认不能往 stdout/stderr 刷屏。

## 五、GPU 环境实测（T4，2026-09-16，驱动就绪时）

> 同一天早些时候这台机器报「找不到 libnvidia-ml.so、硬解 Operation not permitted」，
> 后来又恢复正常 —— **GPU 可用性会随沙箱/驱动挂载变化**，所以代码只能靠运行期探针判断，
> 不能靠 `nvidia-smi` 是否存在。

**① `-hwaccel cuda` 会静默软解，标签会撒谎。**
实测 ffv1（NVDEC 根本不支持）用 `-hwaccel cuda` 照样成功、帧数也对，只是全程软解 ——
`-v verbose` 里连一条 hwaccel 相关日志都没有。所以档 2 必须：
先查 `ffmpeg -decoders` 里有没有 `<codec>_cuvid`（本机：h264/hevc/vp8/vp9/av1/mpeg1/
mpeg2/mpeg4/vc1/mjpeg），有才走档 2，并且用**显式** `-c:v xxx_cuvid`（放 `-i` 之前，
它是输入侧解码器选项），失败会大声报错而不是偷偷退回软解。

**② 硬解失败分两级，粒度不能搞错。**
- 驱动/设备层坏掉（`cannot load libnvcuvid`、`no device available for decoder`、
  `Operation not permitted`）→ 整个进程关掉硬解；
- 卡解不了这个编码器（实测 T4 + AV1：`Codec av1_cuvid is not supported.`）→ **只拉黑该
  编码器**。AV1 拉黑后同批的 h264/hevc/vp9 仍然走硬解（实测同一次调用里三种编码器都拿到
  「硬解」标签），否则一个 AV1 文件会把整批拖回包数档。

**③ 成本模型（900 帧 1080p h264，T4）。**

| 档 | 耗时 | 说明 |
|---|---|---|
| 包头 | 0 | 搭主探测的车 |
| 包数 | **0.10s** | 与时长**无关**（只解复用） |
| 硬解 | **2.26s** | 0.55s CUDA context 固定开销 + ≈500fps；`-hwaccel_output_format cuda` 只省 ~0.4s |
| 估算 | 0 | 实测偏差 +1 帧（2.045s×25 → 51） |

按这个模型，2 小时 1080p 片子硬解一次 ≈ 7 分钟；包数恒定 0.1s。
**实测 6 种编码器/容器（h264/h264-B帧/h264-隔行/hevc/vp9/av1，mp4/mkv/webm/ts）包数与
解码真值全部相等** —— 所以对长片/大目录，`--cpu` 是理性的保底选择。
（用户已拍板：**默认仍走硬解**，长片自己加 `--cpu`。）

**④ 硬解并发拐点是 4 路**（8 × 900 帧）：`-j 1/2/4/8` → 18.1 / 10.6 / **9.4** / 10.4 秒。
4 路时 GPU 利用率才 40%、显存 540MiB（T4 16GB），说明瓶颈不是解码引擎而是每文件的
CUDA context 启动与 CPU 侧帧处理。`HW_DECODE_MAX_JOBS = 4` 就是这么定的。

**⑤ 抢卡不挂**：后台跑满 `h264_nvenc` 编码时，再并发 3 个 vidls 实例（每个 -j 4）
全部正常出结果，无挂起、无失败。**要注意的是**：`_NVDEC_CODEC_BLOCKED` / `GpuProbe`
都是**进程内**状态，跨实例不共享，所以多实例会各自再试一次失败的编码器 —— 代价可接受。

**⑥ 本地验证用素材**放在仓库 `temp/`（已加进 `.git/info/exclude`），别再用 `/tmp`：
用户明确要求临时文件放项目内，省得清理时反复审批 `rm`。

**⑦ AV1 降级实测（`--deep` 全目录 + 计数包装器数调用次数）。**
用包装 ffmpeg/ffprobe 记录每次调用，再跑 8 个文件（3 个 AV1 无包头、1 个 AV1 有包头、
ffv1、h264/hevc/vp9 对照）：

- **帧数全部正确**：3 个 AV1 文件退回「包数」后，与 CPU 解码真值相等（各 50 帧）；
  AV1 但**容器头有 `nb_frames`** 的那个走「包头」，不用解码。
- **ffv1 是 0 次尝试** ✓：它根本没有 `ffv1_cuvid`，被「先查解码器名单」那步短路掉，
  一次 ffmpeg 都没起 —— 这条短路是有效的。
- **`av1_cuvid` 被试了 4 次（不是 1 次）**：拉黑判断在尝试**之前**做，并发首波里
  4 个线程都还没等到别人写进黑名单就已经各自通过了闸门。
  **Why:** 「拉黑」只能防后续波次，防不住首波；这不是 bug 但要知道。
- **一次失败的 `av1_cuvid` 尝试要 0.36–0.50s**（CUDA context 启动占绝大部分，
  成功的 h264 解码也才 0.64s）—— **失败并不便宜**，别以为可以随便重试。
  4 个并发 AV1 文件实测 2.34s 收尾；若首波只试一次理论上是 ~0.5s。
- `-v` 下这三类情况要能分辨（实测输出）：进程级关闭时只打
  `GPU 硬解：不可用`；编码器级拉黑打
  `av1_cuvid: 本机 GPU 不支持该编码器，已拉黑（本进程后续同类文件直接走包数档）`
  （同一编码器只报一次，并发首波不会刷屏）；没有 cuvid 解码器时打
  `ffv1: 跳过硬解档（本机 ffmpeg 没有编译 ffv1_cuvid）→ 帧数走包数档`。

## 六、Windows 移植（`vidls_win.py` 内核 + `vidls.cmd` 启动器，2026-09-17）

Linux 版原样保留、**互不 import**，Windows 版是独立文件。移植时实测出这些差异：

**A. 编码：Python 在 Windows 上默认把 `\n` 写成 `\r\n`。**
这是最阴的一个 —— 布局全对但 `diff` 永远全红（`cat -A` 下 ls 是 `$`、我们是 `^M$`）。
必须 `sys.stdout.reconfigure(newline="\n")`。

**B. 控制台编码不要硬钉 UTF-8，要跟随控制台。**
实测 PowerShell 下 Python 的 stdout 是 `gbk`（控制台代码页 936）。Git Bash / MSYS 是
UTF-8 世界 → 输出 UTF-8，与 coreutils ls 逐字节可比；cmd.exe / PowerShell 用原生编码，
中文才显示得对。硬钉 utf-8 会让 cmd.exe 里全是乱码。

**C. `.cmd` 启动器必须是纯 ASCII。**
**Why:** cmd.exe 用 **ANSI 代码页**（本机 936）解析批处理文件，UTF-8 中文注释会被拆成
乱码并**破坏解析**（实测报 `'□□跑（Git' is not recognized as an internal or external command`）。
`.cmd` 也只该用 CRLF 换行（LF-only 的批处理在 `goto`/label 上有已知坑）。
`.py` 内核里照常写中文，只有 `.cmd` 受限。

**D. `-l` 的列与 ls 不同（三处，都是平台限制，宁可不显示也不显示错的）：**
- **不显示属主 / 属组**：`st_uid`/`st_gid` 恒为 0；MSYS 的 ls 显示
  `Administrator 197121`，那个 gid 是 MSYS 自己的映射表，复刻不了。
- **不显示硬链接数**：`os.scandir` 的 `DirEntry.stat()` 在 Windows 上 `st_nlink` 恒为 0
  （真值要额外 open 一次文件句柄，本工具刻意不为非视频条目多做这次 open）。
- **mode 是 Python 的合成模式**：文件 0666 / 目录 0777（只反映只读位）→ `-rw-rw-rw-`、
  `drwxrwxrwx`；MSYS 的 ls 显示 ACL 推出来的 `-rw-r--r--` / `drwxr-xr-x`。

**E. `total` 只能近似。**
Windows 的 `os.stat` **没有 `st_blocks`**。实测 MSYS 的口径是：`size==0 → 0`；
`size ≤ ~700`（NTFS 常驻，边界还跟文件名长度有关）`→ 1KB`；其余 `→ ceil(size/4096)*4KB`；
目录 → 0。**模型本身是准的**（17 个同尺寸新副本：ls 890、模型 890），
但 NTFS 实际分配受写入历史/碎片影响会差几 KB（同一批原文件是 898）。
所以 `total` 是唯一不较真的地方；`-a` 时 `.`/`..` 也给 0（MSYS 各给 4KB）。

**F. CUDA 致命错误关键字必须带 `.dll` 变体。**
Windows 无 NVIDIA 驱动时的报错是 `Cannot load nvcuvid.dll` / `Failed loading nvcuvid.`
（Linux 是 `libnvcuvid.so`）。**Why:** 关键字表漏了它们就会走到「编码器级」分支，
把 h264/hevc/vp9 逐个拉黑，降级路径整个走错。实测命中清单：
`cannot load nvcuvid` / `failed loading nvcuvid` / `nvcuvid.dll` / `operation not permitted`。

**G. 其它：**
- 隐藏文件跟 MSYS 的 ls 一致**只看 `.` 前缀**，不看 Windows 的 H 属性
  （实测 `attrib +H` 的文件照样出现在 `ls` 里）。
- 路径存在性检查要补 `.exe`（`os.path.exists(r'…\bin\ffprobe')` 会假阴性）；
  执行时不用补（CreateProcess 自己找 `.exe`）。
- `--install` 不做软链（Windows 建软链要管理员/开发者模式），改成在**已在 PATH 里**的目录
  （实测本机 `%USERPROFILE%\.local\bin` 就在用户 PATH 里）写四个 shim：
  `vidls.cmd` / `vidll.cmd`（cmd / PowerShell）与无扩展名的 `vidls` / `vidll`（Git Bash）。
  shim 指向**仓库里**的 `vidls_win.py` 绝对路径，`vidll` 那两份只是在参数里多带一个 `-l`
  —— 改代码立刻生效、不用重装，代价是仓库搬家要重跑 `--install`。
  目标目录不在 PATH 时**只打印** PowerShell 的 `SetEnvironmentVariable` 命令，不擅自改；
  并提醒别用 `setx`（会把 PATH 截断到 1024 字符）。
- **在 Windows 上测 Linux 版 `--install` 要当心**：`vidls.py` 顶部 `import grp/pwd`
  在 Windows 不存在，可以用桩模块先塞进 `sys.modules` 再 `exec_module` 加载整份模块；
  但它的 `_path_contains()` **按 `:` 切 PATH**（Linux 正确写法），所以在 Windows 上
  用「往 PATH 里注入临时目录」的办法隔离是**无效的** —— 它会认为目录不在 PATH，
  转而调 `_append_to_rc()` **往真实 `~/.zshrc` 写一行**（实测踩到过，事后把新建的
  文件删掉了）。Windows 上要测这一步，就把 `HOME`/`USERPROFILE` 指到临时目录，
  或者干脆别跑 `cmd_install`，只静态检查。

**H. WSL 会把 Windows 的 PATH 带进来（用户 2026-09-17 实际踩到）。**
现象：在 WSL 里敲 `vidll`，报
`/mnt/c/Users/Administrator/.local/bin/vidll: 8: exec: /c/Program Files/Python312/python.exe: not found`。
**Why:** WSL 默认 `appendWindowsPath=true`，于是 Windows 侧装在
`C:\Users\<你>\.local\bin` 的那份**无扩展名 sh shim** 在 WSL 里也成了可执行命令并被命中；
而它只认 MSYS 的 `/c/...` 路径 + Windows 的 `python.exe`，在 WSL 里必然失败
（就算 exec 成功，Windows 内核对 WSL 的 cwd/路径也理解不了，结果是错的）。
**How to apply:** 那份 shim 必须按 `uname -s` 分流：

| `uname -s` | 行为 |
|---|---|
| `Linux*`（WSL） | `exec` 仓库里的 `vidls.sh`（用 `_to_wsl_path` 把 `D:\a` 换成 `/mnt/d/a`）—— 交给 Linux 版实现 |
| `MINGW*` / `MSYS*` | Git Bash：Windows python + `vidls_win.py` |
| 其它 | 明确报错，不猜 |

这样 WSL 里 PATH 谁在前都能工作。**注意 shim 是生成物快照**：改了模板要重跑
`vidls --install`（Windows 侧）才会生效。测试方法是拿桩 `uname` 覆盖 PATH 里的
`uname`，分别验三个分支（Linux 分支还要把 `WSL_LAUNCHER` 换成桩来验参数转发）。
