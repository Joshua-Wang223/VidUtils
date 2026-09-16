#!/usr/bin/env bash
# =============================================================================
# nvinterpolate 2x 帧率 —— 崩溃安全版（GPU 专版）
#
# 做什么:
#   用 NVOFA 光流插帧把输入帧率翻倍（目标帧率 = 源帧率 x2），音频原样 copy。
#   例: 3840x2160 @ 24000/1001  →  48000/1001，时长不变。
#
#   可以用 --SS / --TO / -T 只截取源视频的一段来做（见下面"选项"）：
#   输出就是这一个片段（音轨也只取同一段），时长 = 片段时长。
#
# 只支持 2 倍: 滤镜串里 fps=source_fps*2、以及片头要去掉的重复帧数（HEAD_TRIM=3）
#   都是按 2x 写死的。1.25x/2.5x 的预热帧数不同，要用得改源码里这两处。
#
# 只有一条后端（GPU）:
#   nvinterpolate(NVOFA 光流) + hevc_nvenc —— 需要 NVIDIA GPU（Turing CC7.5+，驱动 >= 525）。
#   开工前会【真跑一次试编码】（只读 4 帧，成本可忽略）：驱动坏了 / CUDA 解码不了这个源 /
#   显存不足这类问题会被拦在开始之前，而不是跑到第 30 片才炸；不通过就直接报错退出
#   （试编码的原因写在 ERROR 行里）。
#   跟本文件的"通用版" interp_2x_safe_v1.sh 的关系：那份多一条 CPU 回退后端
#   （minterpolate + libx265，慢一到两个数量级，4K 基本不可行）以及 --backend/--cpu-preset；
#   本 GPU 专版没有这些，其余特性（环境探测 / -j 并行 / 时间段截取）与它保持一致。
#
# 环境自动探测（开工前打印，并驱动下面的并行度决策）:
#   · CPU 逻辑核：**先看 cgroup 配额**（v2 cpu.max / v1 cfs_quota），再看 nproc。
#     容器里 nproc 会报宿主核数（本机就是 nproc=8 而配额只有 2 核）。
#   · 内存：cgroup v2 memory.max/current（扣掉可回收的 file+slab）→ cgroup v1 → /proc/meminfo。
#   · GPU：nvidia-smi 查型号/CC/显存占用/利用率/驱动版本 + 是否有别的计算进程在用；
#     **只告警不阻断**（你自己的视频增强流水线经常就在跑，见 memory/project_t4_gpu_capabilities.md）。
#   · 磁盘：WORKDIR 所在分区余量，少于源文件的 3 倍就告警（2x 产物与源同量级）。
#   · 其它实例：pgrep 找并发的 interp_2x_safe.sh，共用同一块 GPU 只会互相拖慢。
#
# 分片级并行（-j N）:
#   分片之间本来就互不依赖（各自 -ss + 独立滤镜实例 + 独立 TS），所以最多 N 片可以同时跑。
#   auto（默认）取 **1**。依据 memory/project_t4_gpu_capabilities.md 的实测 —— T4 的 NVENC
#   是单引擎，1/2/4/8 路并发**总吞吐基本不变**，单路吞吐随并发数成反比。
#   想要重叠收益就自己 -j 2/3/4（脚本会先做一次 K 路并发试编码确认真能跑起来）。
#   并发下每个任务都有自己的 CWD（$WORKDIR/runlogs/cwd/<片名>）与日志（runlogs/<片名>.log）：
#   前者是因为 nvinterpolate 会往**当前工作目录**写 NvOFFRUC/logFRUCError.txt，N 路并发必须
#   各写各的；后者是为了并发时日志不互相插花，失败时直接看那一片的日志就行。
#
# 前置条件:
#   · ffmpeg 带 nvinterpolate 滤镜 + hevc_nvenc 编码器
#     本机 = /usr/local/bin/ffmpeg 7.1
#   · NVIDIA GPU，Turing (CC 7.5) 或更新；驱动 >= 525
#   · 源视频有明确的帧率元数据 —— 目标帧率是拿它 x2 算出来的，不能是 VFR/0
#
# 为什么不是一条 ffmpeg 命令直接写 MP4:
#   2026-09-14 07:10 那次跑到 07:44 被中断（core dump 显示崩的是 node/codebuddy，
#   它把同进程组的 ffmpeg 一起带走了）。产物 2.1GB 的 mp4 只有 ftyp/free/mdat、
#   没有 moov → 完全不可读，34 分钟算力作废，且没有可续传的点。
#   所以这条链做三件事:
#     1) setsid 脱离会话  —— codebuddy/终端死掉不影响本任务
#     2) 每 L 秒一片写 TS —— TS 无全局索引，被杀时已完成的分片原样可用
#     3) 已存在的分片自动跳过 —— 重跑同一条命令就是"断点恢复"
#   最后 concat 成 MP4，音轨从原片一次性 copy（音频本来就没改，也避开 AAC 切点问题）。
#   音轨起点会补上片头被裁掉的那 HEAD_TRIM 帧（GPU 48000/1001 下 = 62.56ms），与视频内容对齐。
#
# -----------------------------------------------------------------------------
# 用法:
#   bash interp_2x_safe.sh <输入视频> [输出视频]
#
# 位置参数:
#   <输入视频>   必需。分片目录与默认输出名都由它推导
#   [输出路径]   可选。两种形态都行:
#                   · 目录：以 / 结尾、或本身已是存在的目录
#                           → 自动用 <输入名>_2x.mp4 起名
#                   · 文件：带扩展名，原样使用
#                 默认 <WORKDIR>/<输入名>_2x.mp4
#
# 选项:
#   -w, --workdir DIR    分片/输出目录，默认 /workspace/interp_2x/<输入名>
#                        每个输入一个目录；换输入会自动分家（见下方"防呆"）
#   -p, --preset NAME    hevc_nvenc 预设，默认 p5（越大越慢、同码率画质越好）
#   -c, --cq N           恒定质量，默认 25（越大越省码率、画质越低）
#                        即 nvenc 的 -cq
#   -j, --jobs N         并行片数（别名 --workers）
#                          0 = 自动（默认）：GPU 后端取 1（见下"环境探测与并发"）
#                          1 = 顺序执行（与旧版本行为一致）
#                        auto 只影响速度，不影响分片内容，所以 **不写进 recipe**：
#                        改 -j 复用同一个 -w 目录时会照旧 skip，不会整体重编。
#       --threads N      每片 ffmpeg 线程数。0 = 自动（**本脚本不下发**：瓶颈在 GPU 引擎，
#                        不是 CPU）；显式给 >0 会照实下发 -threads / -filter_complex_threads。
#                        （CPU 后端那边 --threads 也基本是装饰：minterpolate 串行、
#                          -threads 只改 libx265 的 frame threads；详见 v1 的说明）
#       --sequential     强制 1 路（等价 -j 1）
#   -L, --seg-len SEC    每片秒数，默认 300
#                          · 越大 → 接缝越少，但崩一次损失越多
#                          · 越小 → 损失窗口小，但每片开头都要重跑一次 FRUC 预热
#       --cap SEC        >0 时只处理前 N 秒，可小数（试跑验证 / 只想要前一段）
#                        非法值（负数/非数字）会直接报错，不静默当成"不裁剪"
#                        超过源总长 = 无效果，与不设置等价
#                        等价于 -T SEC（保留是为了兼容旧调用）
#       --SS TIME        从源视频的 TIME 处开始（默认 0 = 从头），只处理它之后的内容。
#                        TIME 支持秒数（90 / 90.5）或 HH:MM:SS[.ms] / MM:SS（如 01:30:00）。
#       --TO TIME        到源视频的 TIME 处结束。TIME 是【绝对时间戳】，不是时长。
#   -T  TIME            只处理 TIME 这么长（TIME 格式同上）。
#       三者关系: SS 定起点（默认 0）；终点由 --TO（绝对末尾）或 -T（时长）二者之一给出，
#                 都不给则一直到源末尾。--TO 与 -T（以及 --cap）**同时给会直接报错**，
#                 绝不静默选一个。终点超过源末尾 → 夹到末尾；--SS 已到/超过源末尾 → 报错
#                 （没有可处理的画面，早点告诉比跑出空片好）。
#                 片段只影响输出内容：目标帧率仍是源帧率 x2，分片/进度/断点恢复照旧。
#                 注意新选项 SS/TO 与 -T 只认命令行，没有同名环境变量（-T 太大路货，
#                 避免和别的工具的环境变量撞车）。
#       --overwrite      允许覆盖已存在的目标文件（默认拒绝，且尽早退出）
#   -h, --help           显示帮助
#
# 环境变量（可选；与命令行选项重名时命令行优先，保留是为了兼容旧调用）:
#   L  TOTAL_CAP  WORKDIR  PRESET  CQ  JOBS  THREADS
#   FFMPEG      ffmpeg 可执行文件，默认 /usr/local/bin/ffmpeg
#   FFPROBE     ffprobe 可执行文件，默认 ffprobe
#
# 容器: 输出扩展名决定容器，支持 mp4 / mov / mkv。mp4/mov 会加 -movflags
#   +faststart（moov 前置）；mkv 不加（Matroska 不认这个选项，传了会直接报错）。
#   这个校验在【编码开始前】做，避免白跑 50 分钟才发现输出写不出来。
#
# 产出（都在 $WORKDIR）:
#   parts/p00000.ts …   已完成的分片 —— **分片数就是进度**
#   parts/p00000.meta   该片的切法（`ss dt`），决定它能不能被复用
#   parts.txt           concat 用的清单（只列本次 NPARTS 片）
#   recipe.txt          「输入 + 参数」指纹，见下
#   runlogs/p00000.log  该片的 ffmpeg 输出（并发时不混进主日志；失败时看这里的末尾几行）
#   runlogs/cwd/p00000/ 该片的临时工作目录 —— nvinterpolate 的 NvOFFRUC/ 日志落在这儿，
#                       成功即删（失败才留下），所以不会再污染调用者的当前目录
#   成片                 路径见上面 [输出路径]
#   收尾日志             成片路径 + 成片属性（分辨率/帧率/编码/帧数/时长/码率/体积）+ 音轨 + **总耗时**
#                        （属性一次 ffprobe 取，不 -count_frames；按 key 解析，别按 csv 列序）
#
# 目录 / 文件 / 并发:
#   · WORKDIR、parts/、输出文件的父目录都会自动创建，不用先 mkdir
#   · 目标文件已存在时默认报错退出（早退，不等到收尾才失败）；--overwrite 才覆盖
#   · WORKDIR 上有单实例锁（$WORKDIR/.lock，flock）。同一目录同时只允许一个实例，
#     第二个会立刻退出并提示。原因：两个实例会互相删掉对方正在写的分片临时文件
#     （ffmpeg 还在往已被 unlink 的 inode 写），先跑完的把临时文件 mv 走，
#     后跑完的就报 "mv: cannot stat '.../p00002.ts.part'"，而且两边都白跑。
#     锁是内核级的，进程被 kill 会自动释放，不会留死锁。
#
# 防呆（两层，目的都是"绝不静默产出错内容"）:
#   第一层 recipe.txt：记 backend|enc|in|L|trim（+ 可选 ss）。这些参数一变，目录里
#     **每一片**的内容都会不同（帧边界变了/画质档变了/换了视频/换了 --SS 起点）→ 整体拒绝，
#     并打印新旧差异。换输入默认就会另开目录；要显式复用就自己给 -w。
#     这里的 backend 固定是 gpu —— 保留该字段是为了能和通用版 interp_2x_safe_v1.sh
#     共用/互相接管同一个分片目录。
#   第二层 每片 .meta：记该片的 ss/dt。片段终点（--TO / -T / --cap）只影响【末尾那一片】
#     的切法，所以改它不再整体拒绝，而是逐片比对、只重编边界那一片。
#     缺 .meta 的分片（旧版本留下的）一律视为需重做。
#   旧的 v0 格式 recipe（`slice=3|in|…|L|preset|…|cq|…|trim=…`）会被就地升级成新格式，
#   所以已经在跑/已跑完的老目录不会被脚本升级整体拒绝。
#
# 常见用法:
#   # 1) 正式跑：脱离会话后台执行
#   setsid bash /workspace/VidUtils/interp_2x_safe.sh /workspace/input_videos/xxx.mp4 \
#       > /workspace/interp_2x/run.log 2>&1 < /dev/null &
#
#   # 2) 看进度 / 判断是否跑完（启动日志会打印实际的 WORKDIR 与输出路径）
#   tail -f /workspace/interp_2x/run.log
#   ls /workspace/interp_2x/*/parts/          # 分片数 = 已完成进度
#
#   # 3) 中断后恢复：原样再执行 1) 即可，已完成分片秒过
#
#   # 4) 试跑：4 秒一片、只跑前 12 秒，产物扔 /tmp
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -w /tmp/demo -L 4 --cap 12
#
#   # 5) 输出到目录 → 自动起名 <输入名>_2x.mp4
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 /tmp/out/
#
#   # 6) 输出到指定文件 + 换画质参数
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 /tmp/head.mkv -c 23 -p p4
#
#   # 7) 只处理前 10 分钟；之后想补全片，把 --cap 去掉重跑即可
#   #    （前面的分片按 .meta 判定为可复用，只会重编边界那一片）
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 /tmp/head.mp4 --cap 600
#
#   # 8) 补全片：同一条命令去掉 --cap，复用同一个 -w
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -w /tmp/work
#
#   # 9) 只截取一段：从 90 秒处取 30 秒（或 --SS 00:01:30 --TO 00:02:00）
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -w /tmp/seg --SS 90 -T 30
#
#   # 10) 显式并行（GPU 单引擎默认只给 1 路，重叠收益要自己开）
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -j 2
#
#   # 11) 只跑并行度/环境探测，不真的开工：看日志头几行即可
#   bash /workspace/VidUtils/interp_2x_safe.sh /path/in.mp4 -w /tmp/probe --cap 2 -L 1
#
# 失败时的样子:
#   · 某片失败      → 打印 FAIL 并非 0 退出；**同时停止派发新片**，但在飞的片会跑完
#                     （它们的成果照样留在 parts/ 里，下次重跑直接 skip）。
#                     失败片的半成品留在 pXXXXX.ts.part.<pid>，它不是 .ts，所以不会被
#                     误判成"已完成"；持锁启动时会自动清掉，重跑也会自动重做该片。
#                     该片的 ffmpeg 输出（含真实报错）在 runlogs/pXXXXX.log，FAIL 行后面
#                     会直接附上末尾几行。
#   · 拼接失败      → 分片都还在，可手工重拼，或直接重跑
#   · 被杀/断电      → 已改名成 .ts 的分片保留；临时文件下次持锁启动时自动清理
#   · Ctrl+C/SIGTERM → trap 先杀掉所有在跑的 ffmpeg 再退出（130/143），已完成分片保留、
#                     原样重跑即续。收尾是**有界**的：先 TERM，最多等 5s 让它优雅收尾，
#                     还活着就 SIGKILL —— 4K minterpolate 的"优雅停止"可能要几十秒，
#                     不这样做就会"看着像按了 Ctrl+C 没反应"。
#   · 孤儿 ffmpeg    → 脚本被 kill -9（走不到 trap）时，在飞的 ffmpeg 可能变成 PPID=1 的孤儿
#                     继续烧 CPU。而且它**继承了锁的 fd 9** → 孤儿自己就占着锁，所以下次用
#                     同一个 -w 启动会先撞锁（实测事故就是这样：重跑一直报"另一个实例正在跑"）。
#                     脚本会区分"真实例"和"只有 ffmpeg 的孤儿"：后者先把 ffmpeg 收掉
#                     （TERM → 3s → KILL）再接管锁，然后清 .part 半成品。
#                     只靠 run_job 子 shell 的 trap + $! 是兜不住的：子 shell 被打死、或信号
#                     落在它"装 trap 之前"的那一瞬间，pid 就没人知道了。
# =============================================================================
set -euo pipefail

usage() {
    cat <<'EOF'
用法: bash interp_2x_safe.sh <输入视频> [输出路径] [选项]

  把输入帧率翻倍（目标 = 源帧率 x2），音频原样 copy，时长不变。
  只做 2 倍：fps=source_fps*2 和片头去重帧数都按 2x 写死。
  只有一条后端：nvinterpolate（NVOFA 光流）+ hevc_nvenc，需要 NVIDIA GPU；
  开工前会真跑一次试编码，不可用就直接报错退出（本脚本没有 CPU 回退后端 ——
  要 CPU 回退（minterpolate + libx265）请用通用版 interp_2x_safe_v1.sh）。

位置参数:
  <输入视频>    必需。分片目录与默认输出名都由它推导
  [输出路径]    可选。两种形态都行
                   · 目录：以 / 结尾、或本身已是存在的目录
                           → 自动用 <输入名>_2x.mp4 起名
                   · 文件：带扩展名，原样使用（mp4 / mov / mkv）
                 默认 <WORKDIR>/<输入名>_2x.mp4

选项:
  -w, --workdir DIR   分片/输出目录，默认 /workspace/interp_2x/<输入名>
  -p, --preset NAME   hevc_nvenc 预设，默认 p5（越大越慢、同码率画质越好）
  -c, --cq N          恒定质量，默认 25（越大越省码率、画质越低）
                      即 nvenc 的 -cq
  -j, --jobs N        并行片数（别名 --workers）。0=自动（默认），1=顺序。
                      auto：GPU 后端取 1（T4 实测 NVENC 单引擎，多路并发总吞吐
                      基本不变；想要重叠收益就显式 -j 2/3/4，脚本会先做并发试编码确认）
                      并行度不影响分片内容，不进 recipe：改 -j 不会触发整体重编
      --threads N     每片 ffmpeg 线程数，0=自动（GPU 后端不下发）
      --sequential    强制 1 路（等价 -j 1）
  -L, --seg-len SEC   每片秒数，默认 300（越大接缝越少、崩一次损失越多）
      --cap SEC       >0 时只处理前 N 秒，可小数（试跑验证 / 只要前一段）
                      负数或非数字会报错；超过源总长等于不设置
                      等价于 -T SEC（保留以兼容旧调用）
      --SS TIME       从源视频 TIME 处开始（默认 0=从头），只处理它之后的内容
                      TIME 支持秒数（90 / 90.5）或 HH:MM:SS[.ms] / MM:SS（如 01:30:00）
      --TO TIME       到源视频 TIME 处结束；TIME 是【绝对时间戳】，不是时长
  -T  TIME            只处理 TIME 这么长（时长；格式同 --SS）
                      · SS 定起点；终点由 --TO（绝对）或 -T（时长）给出，都不给则到源末尾
                      · --TO / -T / --cap 三者同时给会直接报错，不会静默选一个
                      · 终点超过源末尾 → 夹到末尾；--SS 已到/超过源末尾 → 报错
                      · 输出只有这一段（音轨也只取同一段），帧率仍是源帧率 x2
                      · 这三个只认命令行，没有同名环境变量
      --overwrite     允许覆盖已存在的目标文件（默认拒绝，且尽早退出）
  -h, --help          显示本帮助

环境自动探测（开工前打印）:
  CPU 逻辑核先看 cgroup 配额（v2 cpu.max / v1 cfs_quota）再看 nproc —— 容器里
  nproc 会报宿主核数；内存同样优先 cgroup（扣掉可回收页）；GPU 查型号/CC/显存/
  利用率并提示是否已有别的计算进程在跑；另打印 WORKDIR 所在分区余量与并发的
  其它 interp_2x_safe.sh 实例。探测结果用于告警与 -j 的建议值
  （GPU 后端 -j auto 固定取 1，理由见上）。

目录 / 文件 / 并发:
  · WORKDIR、parts/、runlogs/、输出文件的父目录都会自动创建
  · 目标文件已存在时默认报错退出；确认要覆盖再加 --overwrite
  · WORKDIR 上有单实例锁（.lock）。同一目录同时只允许一个实例，第二个会立刻
    退出并提示 —— 因为两个实例会互相删掉对方正在写的分片临时文件，
    导致 "mv: cannot stat ... .part" 且两边都白跑。
    （注意这是"实例之间"的互斥，和实例内部 -j 多路并行是两件事）
  · 每片在 runlogs/cwd/<片名>/ 里跑：nvinterpolate 会往当前工作目录写
    NvOFFRUC/logFRUCError.txt，并发时必须各写各的，也不会再落到你的调用目录

环境变量（可选；同名时命令行优先，保留是为了兼容旧调用）:
  L  TOTAL_CAP  WORKDIR  PRESET  CQ  JOBS  THREADS
  FFMPEG      默认 /usr/local/bin/ffmpeg
  FFPROBE     默认 ffprobe

产出（都在 WORKDIR）:
  parts/p*.ts       已完成的分片 —— 分片数就是进度
  parts/p*.meta     该片的切法（ss dt），决定它能不能被复用
  runlogs/p*.log    每片的 ffmpeg 输出（失败时看这里）
  recipe.txt        输入+后端+参数的指纹；不匹配会拒绝启动（防复用错分片）
  成片               路径见上面 [输出路径]

复用规则（两层）:
  · recipe.txt 里的 backend/enc/in/L/trim/ss 一变 → 整体拒绝（每片内容都会不同）
    ss --SS 起点会平移所有分片的帧边界，所以它进 recipe；--SS 为 0 时该字段不出现，
    旧目录照旧可用。换 --SS 请另给 -w，别指望复用。
  · 只改 --TO / -T / --cap → 不拒绝，逐片比对 .meta，只重编末尾那一片

示例（只截取源视频的一段）:
  # 从 90 秒处开始，取 30 秒（00:01:30 起 30s）
  bash interp_2x_safe.sh /path/in.mp4 -w /tmp/seg --SS 90 -T 30

  # 取 00:10:00 ~ 00:20:00 这一段
  bash interp_2x_safe.sh /path/in.mp4 -w /tmp/seg --SS 00:10:00 --TO 00:20:00

  # 与 --cap 等价：从 0 起只取前 12 秒
  bash interp_2x_safe.sh /path/in.mp4 -w /tmp/demo -L 4 -T 12

正式跑（脱离会话）:
  setsid bash interp_2x_safe.sh /path/in.mp4 -w /tmp/work \
      > /workspace/interp_2x/run.log 2>&1 < /dev/null &

看进度:
  tail -f /workspace/interp_2x/run.log
  ls /workspace/interp_2x/*/parts/

中断后恢复: 原样再执行同一条命令即可（已完成分片秒过）。
EOF
}

# --------------------------- 参数解析 ----------------------------------------
need_arg() { [[ $# -ge 2 ]] || { echo "错误: 选项 $1 需要一个值" >&2; exit 1; }; }

IN=""; OUT_ARG=""
WORKDIR_OPT=""; PRESET_OPT=""; CQ_OPT=""; L_OPT=""; CAP_OPT=""
JOBS_OPT=""; THREADS_OPT=""
SS_OPT=""; TO_OPT=""; T_OPT=""
SEQUENTIAL=false
OVERWRITE=false

while (( $# > 0 )); do
    case "$1" in
        -h|--help)      usage; exit 0 ;;
        -w|--workdir)   need_arg "$@"; WORKDIR_OPT=$2; shift 2 ;;
        -p|--preset)    need_arg "$@"; PRESET_OPT=$2;  shift 2 ;;
        -c|--cq)        need_arg "$@"; CQ_OPT=$2;      shift 2 ;;
        -L|--seg-len)   need_arg "$@"; L_OPT=$2;       shift 2 ;;
        -j|--jobs|--workers) need_arg "$@"; JOBS_OPT=$2; shift 2 ;;
        --threads)      need_arg "$@"; THREADS_OPT=$2; shift 2 ;;
        --sequential|--no-parallel) SEQUENTIAL=true; shift ;;
        --cap)          need_arg "$@"; CAP_OPT=$2;     shift 2 ;;
        --SS)           need_arg "$@"; SS_OPT=$2;      shift 2 ;;
        --TO)           need_arg "$@"; TO_OPT=$2;      shift 2 ;;
        -T)             need_arg "$@"; T_OPT=$2;       shift 2 ;;
        --overwrite)    OVERWRITE=true; shift ;;
        -*)             echo "错误: 未知选项 '$1'" >&2; usage; exit 1 ;;
        *)  if   [[ -z "$IN"      ]]; then IN=$1
            elif [[ -z "$OUT_ARG" ]]; then OUT_ARG=$1
            else echo "错误: 多余的参数 '$1'（最多 输入视频 + 输出路径）" >&2; usage; exit 1
            fi
            shift ;;
    esac
done
[[ -n "$IN" ]] || { usage; exit 1; }

# 生效优先级：命令行选项 > 环境变量 > 默认值
L=${L_OPT:-${L:-300}}                    # 每片时长(秒)。崩一次最多损失这么多
TOTAL_CAP=${CAP_OPT:-${TOTAL_CAP:-0}}     # >0 时只处理前 N 秒（验证用；等价 -T）
# --SS / --TO / -T 只认命令行，没有同名环境变量（-T 太大路货，避免和别的工具撞车）。
SS_RAW=$SS_OPT                           # --SS 片段起点（秒数或 HH:MM:SS）
TO_RAW=$TO_OPT                           # --TO 片段终点（绝对时间戳）
T_RAW=$T_OPT                             # -T  片段时长
PRESET=${PRESET_OPT:-${PRESET:-p5}}      # hevc_nvenc 预设
CQ=${CQ_OPT:-${CQ:-25}}                  # 恒定质量（nvenc -cq）
# 并行相关。0/空 都表示"自动"；解析成数字后再做校验，避免 (( )) 遇到
# 带前导零的写法（-j 08 会被 bash 当成八进制直接报语法错）或非数字时静默走偏。
JOBS_RAW=${JOBS_OPT:-${JOBS:-0}}
THREADS_RAW=${THREADS_OPT:-${THREADS:-0}}
FFMPEG=${FFMPEG:-/usr/local/bin/ffmpeg}
FFPROBE=${FFPROBE:-ffprobe}

BASE=$(basename "${IN%.*}")
WORKDIR=${WORKDIR_OPT:-${WORKDIR:-/workspace/interp_2x/$BASE}}

# 片头要裁掉的帧数（每个分片都是全新的滤镜实例，每片开头都会重演一次）:
#   nvinterpolate 2x 的**前 3 个输出帧是同一张画面重复** —— 光流要"前一帧"才能算，
#   实例刚起来时吐不出有效插值。实测：这 3 帧对源第 1 帧 ≈44.6dB、对源第 0 帧只 20dB，
#   第 4 个输出帧才是真正插出来的 → 必须剪掉。
# 这个值会写进 recipe.txt。
HEAD_TRIM=3
# 注意：-t 是【输出】选项，剪掉 3 帧后 ffmpeg 会自己多读 3 个源帧把这片补满 L 秒，
# 因此每片仍严格是 L 长、且相邻片的画面内容天然连续（实测 4 片 = 192/192/192 帧）。
#   · **视频侧**不要再做任何 ±offset：早期版本手工补 0.0625s 反而把帧数算多。
#     逐帧实测也证明这个偏移是常数、不随分片数累积（同帧号对齐 43.3dB vs 平移 3 帧 22.2dB）。
#   · 但代价是"整条视频内容比时间戳超前 3 个输出帧"（48000/1001 → 62.56ms），
#     所以**音轨**要在收尾 copy 时同量前移对齐（见下面 AUD_SS_OPTS 段）。

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# --- 并行参数的合法性 ---------------------------------------------------------
# 负数/非数字一律报错，不静默当成 0（"我明明写了 -j 4 却只跑一路"这种最坑）。
for _opt_pair in "JOBS:$JOBS_RAW" "THREADS:$THREADS_RAW"; do
    _opt_name=${_opt_pair%%:*}; _opt_val=${_opt_pair#*:}
    case "$_opt_val" in
        ''|*[!0-9]*) die "${_opt_name} 必须是非负整数，当前为 '$_opt_val'" ;;
    esac
done
unset _opt_pair _opt_name _opt_val
JOBS_OPT=$(( 10#$JOBS_RAW ))            # 10# 前缀：让 -j 08 也能当十进制解析
THREADS_OPT=$(( 10#$THREADS_RAW ))
# --sequential 等价 -j 1；写进 JOBS_OPT 后就不会再被 auto 推导覆盖
if [[ "$SEQUENTIAL" == true ]]; then JOBS_OPT=1; fi

# --- 时间段参数（--SS / --TO / -T）的换算与语法校验 ---------------------------
# 时间值支持三种写法，内部一律换算成秒（%.4f）：
#   90 / 90.5        纯秒数
#   MM:SS / M:SS     如 1:30 / 01:30.5
#   HH:MM:SS[.ms]    如 01:30:00 / 00:01:30.250
# 这里只做"语法"校验；"范围/冲突"校验要等 ffprobe 拿到源总长，放在下面的时长段。
time_to_sec() {   # <值> → stdout 秒；非法写法则非 0 退出（不打印）
    awk -v v="$1" 'BEGIN{
        n = split(v, a, ":")
        if (n < 1 || n > 3) exit 1
        for (i = 1; i <= n; i++) if (a[i] !~ /^[0-9]+(\.[0-9]+)?$/) exit 2
        if      (n == 1) s = a[1]
        else if (n == 2) s = a[1] * 60 + a[2]
        else             s = a[1] * 3600 + a[2] * 60 + a[3]
        printf "%.4f", s
    }'
}
# 秒 → 紧凑十进制（去掉多余的尾零）：0→0、2→2、90.5→90.5。
# .meta 里的 ss 走它 —— 好处是 --SS 为 0 时与旧版逐字节一致（回归测试按 `%d` 断言）。
fmt_num() {   # <秒> [加数]
    awk -v v="$1" -v add="${2:-0}" 'BEGIN{
        s = sprintf("%.4f", v + add); sub(/\.?0+$/, "", s); if (s == "") s = "0"; print s }'
}
# 用 awk 判大小，别用 (( ))：这些值允许小数，bash 的 (( )) 只吃整数。
is_pos() { awk -v v="$1" 'BEGIN{exit !(v > 0)}'; }

TIME_ERR_HINT="  支持秒数（90 / 90.5）或 HH:MM:SS[.ms] / MM:SS（如 01:30:00）。"
SS_SEC=""; TO_SEC=""; DUR_SEC=""
if [[ -n "$SS_RAW" ]]; then
    SS_SEC=$(time_to_sec "$SS_RAW") || die "--SS 时间格式非法: '$SS_RAW'
$TIME_ERR_HINT"
fi
if [[ -n "$TO_RAW" ]]; then
    TO_SEC=$(time_to_sec "$TO_RAW") || die "--TO 时间格式非法: '$TO_RAW'
$TIME_ERR_HINT"
    is_pos "$TO_SEC" || die "--TO 必须大于 0，当前为 '$TO_RAW'"
fi
if [[ -n "$T_RAW" ]]; then
    DUR_SEC=$(time_to_sec "$T_RAW") || die "-T 时间格式非法: '$T_RAW'
$TIME_ERR_HINT"
    is_pos "$DUR_SEC" || die "-T（片段时长）必须大于 0，当前为 '$T_RAW'"
fi
# --cap 语义保持不变：空/0 = 不裁剪；>0 = 片段时长（从 SS 起算）。非法值直接报错。
CAP_SEC=""
case "$TOTAL_CAP" in
    ''|0) ;;
    *)  awk -v c="$TOTAL_CAP" 'BEGIN{exit !(c ~ /^[0-9]+(\.[0-9]+)?$/)}' \
            || die "TOTAL_CAP 必须是非负数字，当前为 '$TOTAL_CAP'"
        CAP_SEC=$TOTAL_CAP ;;
esac
# --TO / -T / --cap 都描述"片段终点"，同时给两个以上无法自洽 → 报错，绝不静默取一个。
_n_end=0
if [[ -n "$TO_SEC"  ]]; then _n_end=$(( _n_end + 1 )); fi
if [[ -n "$DUR_SEC" ]]; then _n_end=$(( _n_end + 1 )); fi
if [[ -n "$CAP_SEC" ]]; then _n_end=$(( _n_end + 1 )); fi
if (( _n_end > 1 )); then
    die "--TO / -T / --cap 只能给一个（三者都描述片段终点）:
  --TO 绝对末尾、-T 时长、--cap 时长（--cap N 等价于 -T N）。
  同时给多个无法自洽，这里直接报错而不是替你选一个。"
fi
unset _n_end

# --- 信号处理 ----------------------------------------------------------------
# 并行之后父进程会带着一串子 ffmpeg；收到 INT/TERM/HUP 必须把它们一起收掉，
# 否则退出码看着正常、实际留下一堆还在写盘的 ffmpeg。
# SIGKILL 走不到这里（这正是分片设计的用途）：已完成分片保留、临时文件下次自动清。
declare -a CHILD_PIDS=()
on_signal() {
    trap - INT TERM HUP
    log "收到中断信号，先停掉在跑的 $((${#CHILD_PIDS[@]})) 个 ffmpeg（已完成的分片会保留，重跑即续）"
    if (( ${#CHILD_PIDS[@]} > 0 )); then
        kill "${CHILD_PIDS[@]}" 2>/dev/null || true
    fi
    # 不能只靠子 shell 的 trap：子 shell 被打死、或信号落在它"装 trap 之前"的那一瞬间，
    # ffmpeg 就成了没人认领的孤儿。这里直接按"还在写本目录 parts/ 的 ffmpeg"再收一遍
    # （内部 TERM → 最多等 5s → KILL），所以 Ctrl+C 不会再像"按了没反应"。
    stop_ffmpeg_writers "中断"
    wait 2>/dev/null || true
    exit 130
}
trap on_signal INT TERM HUP

# 注意：PARTS / LIST / RUNLOGS 这些路径要等 WORKDIR 绝对化【之后】再拼 ——
# 每片都会 cd 进自己的临时 CWD，用相对 -w 拼出来的相对路径在那时就失效了
# （症状：Error opening output work/parts/pXXXXX.ts.part.NNN: No such file or directory）。


# --- 输出路径：第 2 个位置参数既可以是文件，也可以是目录 ---------------------
#   · 以 / 结尾，或本身就是已存在的目录 → 当目录，自动用 <输入名>_2x.<ext> 起名
#   · 否则当最终文件路径，原样使用（必须带扩展名，否则 ffmpeg 认不出容器）
if [[ -z "$OUT_ARG" ]]; then
    OUT="$WORKDIR/${BASE}_2x.mp4"
elif [[ "$OUT_ARG" == */ || -d "$OUT_ARG" ]]; then
    OUT="${OUT_ARG%/}/${BASE}_2x.mp4"
else
    OUT="$OUT_ARG"
fi
[[ "${OUT##*/}" == *.* ]] || die "输出路径 '$OUT' 没有扩展名，ffmpeg 无法判断容器。
  指定目录请以 / 结尾（会按 <输入名>_2x.mp4 起名）；指定文件请带扩展名。"

# --- 路径绝对化（并行之后每一个分片都在自己的临时 CWD 里跑，相对路径会失效）----
# 只把"给命令用"的路径绝对化：WORKDIR / OUT / IN_ABS。
# recipe.txt 里仍然用【原样的 $IN】—— 否则同一个输入写成相对路径就会让已有分片
# 目录的 recipe 对不上而整体拒绝复用。
mkdir -p "$WORKDIR" "$(dirname "$OUT")" 2>/dev/null || true
WORKDIR=$(cd "$WORKDIR" && pwd) || die "无法进入分片目录: $WORKDIR"
OUT=$(cd "$(dirname "$OUT")" && pwd)/"$(basename "$OUT")"

# 分片/日志路径在 WORKDIR 绝对化【之后】才拼（理由见上面）。绝对化前的相对 -w 到这里已变成绝对路径。
PARTS="$WORKDIR/parts"
LIST="$WORKDIR/parts.txt"
# 并行之后每个任务一个日志、一个临时 CWD（nvinterpolate 会把 NvOFFRUC/ 写在 CWD 下）
RUNLOGS="$WORKDIR/runlogs"
CWDROOT="$RUNLOGS/cwd"
EVENTS="$RUNLOGS/.events"
PROBE_ERR_FILE="$RUNLOGS/.probe_err"

# --- 收拾"还在往本目录 parts/ 写"的 ffmpeg（孤儿 & 在飞的都算）--------------------
# 为什么不能只靠 run_job 子 shell 里的 trap + $!：
#   · 子 shell 被打死、或信号正好落在它"装 trap 之前"那一瞬间 → ffmpeg 变孤儿，pid 没人知道；
#   · 就算 trap 跑了，ffmpeg 收到 TERM 走的是**优雅停止**（4K/minterpolate 下可能要几十秒），
#     看起来就像"按了 Ctrl+C 还在跑"；
#   · 脚本本体被 kill -9（走不到 trap）时，孤儿会一直烧 CPU 跑到自己结束为止。
# 所以统一按"命令行里引用了本 WORKDIR 的 parts/ 目录"识别：本目录有单实例锁，
# 不会有别人的进程命中。先 TERM、最多等 3s、还活着就 KILL。
# 为什么 grace 这么短：实测本机 ffmpeg 7.1 捕获了 INT/TERM（SigCgt 里 bit 2/15 都在），
# 但"优雅停止"要 **11–13s** 才真的退出（连 640x480 的 lavfi 编码都这样，4K 只会更久），
# 而这个被中断的半成品 .part 反正要丢弃 —— 不值得为它等十几秒。
# （只匹配名字以 ffmpeg 开头的进程，且必须是独立 argv 元素，避免误伤自己的 cat/grep。）
# 进程是否还活着 —— **僵尸不算活着**（它已经退出，只是父进程还没 reap）。
# 只用 kill -0 判会把僵尸当成"还在跑"，于是白等 5s 还打一条误导的"超时"日志。
proc_alive() {
    local st
    st=$(awk '{print $3}' "/proc/$1/stat" 2>/dev/null) || return 1
    [[ -n "$st" && "$st" != Z ]]
}
ffmpeg_writer_pids() {
    local d pid
    local -a argv=()
    [[ -n "${PARTS:-}" ]] || return 0
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        [[ -r "$d/cmdline" ]] || continue
        mapfile -d "" -t argv < "$d/cmdline" 2>/dev/null || continue
        (( ${#argv[@]} >= 2 )) || continue
        case "${argv[0]##*/}" in ffmpeg*) ;; *) continue ;; esac
        case " ${argv[*]} " in *" $PARTS/"*) printf "%s\n" "$pid" ;; esac
    done
}
stop_ffmpeg_writers() {   # <日志前缀>
    local what=${1:-ffmpeg} pids p alive w=0
    pids=$(ffmpeg_writer_pids)
    [[ -n "$pids" ]] || return 0
    log "$what: 还有 $(printf "%s\n" "$pids" | wc -l) 个 ffmpeg 在写 $PARTS/（pid $(printf "%s " $pids)）→ 先发 TERM"
    kill -TERM $pids 2>/dev/null || true
    while (( w < 30 )); do                       # 最多等 3s（见下）
        alive=0
        for p in $pids; do if proc_alive "$p"; then alive=$((alive + 1)); fi; done
        (( alive == 0 )) && break
        sleep 0.1; w=$((w + 1))
    done
    for p in $pids; do
        if proc_alive "$p"; then
            log "$what: pid $p 优雅收尾超时 → SIGKILL（它的 .part 半成品会被下次启动清掉）"
            kill -KILL "$p" 2>/dev/null || true
        fi
    done
    return 0
}
# 容器相关：-movflags +faststart 只对 mp4/mov 有意义，Matroska 不认这个选项、
# 传了会直接报错。这里在【编码开始前】就定好，避免白跑 50 分钟才发现输出写不出。
case "${OUT##*.}" in
    mp4|m4v|mov) MOVFLAGS=(-movflags +faststart) ;;
    mkv|webm)    MOVFLAGS=() ;;
    *)           die "输出扩展名 '.${OUT##*.}' 不支持；可用: mp4 / mov / mkv" ;;
esac

# 所有需要的目录都自动创建：WORKDIR、分片目录、runlogs、输出文件的父目录。
# dirname 可能是 "."（输出写到当前目录），mkdir -p . 无害。
mkdir -p "$WORKDIR" "$PARTS" "$RUNLOGS" "$CWDROOT" "$(dirname "$OUT")"

# --- 单实例锁 -----------------------------------------------------------------
# 放在覆盖检查【之前】：并发时第二个实例应当被告知"有实例在跑"（真正的原因），
# 而不是先撞上"目标文件已存在"这种与并发无关的提示。
#
# 同一个 WORKDIR 必须独占。两个实例共享它时，循环开头的 rm -f 会删掉对方正在
# 写的临时文件（ffmpeg 仍在往已被 unlink 的 inode 写），先跑完的把临时文件 mv 走，
# 后跑完的就报：
#   mv: cannot stat '.../p00002.ts.part': No such file or directory
# 而且双方都白跑。flock 是内核级的，进程被 kill 也会自动释放，不会留下死锁；
# .lock 文件本身留在盘上也无所谓 —— 占用与否只看锁，不看文件在不在。
# 谁占着这把锁：扫 /proc/<pid>/fd/* 找指向本 .lock 的进程（可能有多个）。
lock_holder_pids() {
    local d pid fd
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        [[ "$pid" == "$$" ]] && continue
        [[ -r "$d/cmdline" ]] || continue
        for fd in "$d"/fd/*; do
            [[ -e "$fd" ]] || continue
            if [[ "$(readlink "$fd" 2>/dev/null)" == "$LOCK" ]]; then
                printf '%s\n' "$pid"; break
            fi
        done
    done
}
is_ffmpeg_pid() {
    [[ -r "/proc/$1/cmdline" ]] || return 1
    [[ "$(tr '\0' ' ' < "/proc/$1/cmdline")" == *ffmpeg* ]]
}
LOCK="$WORKDIR/.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
    # 撞锁了。先分清楚持有者是哪一类 —— 这决定了能不能自动接管：
    #   · 非 ffmpeg 的进程 = 真的还有实例在跑（或它没退干净的 run_job 子 shell，命令行与脚本一样）
    #     → 老老实实退出，把 pid/启动时间/命令行报出来。
    #   · **只有 ffmpeg** 在持锁 = 孤儿：run_job 子 shell 里的 ffmpeg **继承了 fd 9**，
    #     脚本本体被 kill -9 后它就带着锁活下来了。这种情况必须先把它们收掉、再抢锁，
    #     否则"启动时清孤儿"那一步永远走不到（实测事故就是这样：孤儿占着锁，重跑一直撞锁）。
    HOLDERS=$(lock_holder_pids)
    H_INSTANCE=""; H_FFMPEG=""
    for h in $HOLDERS; do
        if is_ffmpeg_pid "$h"; then H_FFMPEG="$H_FFMPEG $h"; else H_INSTANCE="$H_INSTANCE $h"; fi
    done
    if [[ -n "${H_INSTANCE// /}" ]]; then
        HOLDER=${H_INSTANCE# }
        HOLDER=${HOLDER%% *}
        H_START=$(ps -o lstart= -p "$HOLDER" 2>/dev/null | sed 's/^ *//' || true)
        H_CMD=$(tr '\0' ' ' < "/proc/$HOLDER/cmdline" 2>/dev/null | cut -c1-240 || true)
        die "另一个实例正在跑同一个分片目录: $WORKDIR
  持有者: pid $HOLDER${H_START:+  启动于 $H_START}
  ${H_CMD:+命令行: $H_CMD}
  同一目录只能有一个实例（否则会互相删对方的临时文件、两边都白跑）。
  等它结束再原样重跑即可（已完成的分片会自动跳过），或换一个 -w 目录。
  确认它不该活着的话：kill -TERM $HOLDER（脚本会把它在跑的 ffmpeg 一起收掉再退）。"
    fi
    if [[ -n "${H_FFMPEG// /}" ]]; then
        log "残留: 锁被孤儿 ffmpeg 占着（脚本本体已不在）:${H_FFMPEG} → 先收掉它们再接管锁"
        stop_ffmpeg_writers "残留"
        ACQ=0
        for _i in $(seq 50); do
            if flock -n 9; then ACQ=1; break; fi
            sleep 0.1
        done
        unset _i
        (( ACQ == 1 )) || die "孤儿 ffmpeg 已收掉，但 $WORKDIR 的锁仍被占着（有别的进程持有）"
        log "残留: 已接管锁（孤儿 ffmpeg 已清掉），继续本次任务"
    else
        die "另一个实例正在跑同一个分片目录: $WORKDIR
  锁被占用，但找不到持有它的进程（可能刚好在这瞬间退出）。
  稍等再原样重跑即可；或换一个 -w 目录。"
    fi
fi

# 拿到锁 = 本目录没有别的实例在写 → 先把上一次留下的孤儿 ffmpeg 收掉。
# 放在覆盖检查【之前】：否则"目标文件已存在"这种与残留无关的早退，会把一个还在
# 白烧 CPU 的孤儿留在那里（它的来源见上面 ffmpeg_writer_pids 的说明）。
stop_ffmpeg_writers "残留"

# 目标文件默认【不】覆盖：已存在就立刻退出，别等跑了几十分钟到收尾才发现。
if [[ -e "$OUT" && "$OVERWRITE" != true ]]; then
    die "目标文件已存在: $OUT
  要覆盖请加 --overwrite；或换输出路径；或用 -w 换一个分片目录。"
fi

# 已经拿到锁、孤儿也在上面收掉了 → 清掉上次被 kill 留下的临时文件。
# （顺序：先杀进程再 rm —— 否则孤儿会继续往已被 unlink 的 inode 写，
#   现象是"文件删了体积还在涨"。）
for f in "$PARTS"/p*.ts.part*; do
    [[ -e "$f" ]] || continue
    log "clean 残留临时文件 $(basename "$f")"
    rm -f "$f"
done

# ------------------------------- 前置检查 ------------------------------------
[[ -f "$IN" ]] || die "找不到输入: $IN"
[[ -x "$FFMPEG" ]] || die "找不到可执行文件: $FFMPEG"
# 并行之后每个分片都在自己的临时 CWD 里跑（见 run_job），给命令用的路径必须绝对化。
IN_ABS=$(realpath "$IN" 2>/dev/null \
    || printf '%s/%s' "$(cd "$(dirname "$IN")" && pwd)" "$(basename "$IN")")

# 滤镜/编码器清单先整份取回再匹配，不要写成 `ffmpeg ... | grep -q`：
# 脚本开了 pipefail，grep -q 一读到就退出，ffmpeg 收到 SIGPIPE(141)，
# 整条管道算失败 → 明明有这个滤镜却判成"没有"。
FF_FILTERS=$("$FFMPEG" -hide_banner -filters 2>/dev/null || true)
FF_ENCODERS=$("$FFMPEG" -hide_banner -encoders 2>/dev/null || true)
have_filter()  { [[ "$FF_FILTERS"  == *" $1 "* ]]; }
have_encoder() { [[ "$FF_ENCODERS" == *" $1 "* ]]; }

# --- 源帧率与尺寸（一次 ffprobe 拿全） ----------------------------------------
# 源帧率 → 目标帧率（x2），只用于日志展示：nvinterpolate 的滤镜串直接写
# fps=source_fps*2 就行。
SRC_INFO=$("$FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=width,height,r_frame_rate -of csv=p=0 "$IN" 2>/dev/null || true)
IFS=',' read -r SRC_W SRC_H SRC_RATE <<<"$SRC_INFO"
[[ -n "$SRC_RATE" && "$SRC_RATE" != "0/0" ]] \
    || die "取不到 $IN 的帧率（VFR 或元数据缺失）: '$SRC_RATE'
  目标帧率 = 源帧率 x2，源帧率必须能从元数据里读到。"
SRC_W=${SRC_W:-0}; SRC_H=${SRC_H:-0}
TARGET_RATE=$(awk -v r="$SRC_RATE" 'BEGIN{
        split(r, a, "/"); n = a[1]; d = (a[2] == "" ? 1 : a[2]); printf "%d/%d", n*2, d }')

# --- 时长 / 分片数 -----------------------------------------------------------
# 放在并行度决策【之前】：并行度要先夹到 NPARTS（片数比核数还少时开那么多路没意义）。
FULL_TOTAL=$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$IN")
[[ -n "$FULL_TOTAL" ]] || die "取不到 $IN 的时长"
# 保留小数，不要截成整秒：末尾分片要按精确剩余时长切。整秒截断会漏掉尾巴
# （实测 10.0667s 的源被截成 10s → 成片少 4 帧）。

# --- 时间段（--SS / --TO / -T / --cap）→ 片段起点 / 终点 / 时长 --------------
# 语义：SS 定起点（默认 0）；终点取 --TO（绝对末尾）/ -T（时长）/ --cap（=时长）
# 三者之一，都不给就一直处理到源末尾。TOTAL 从"源总时长"变成"片段时长"，
# 后面所有地方（分片数、每片 dt、收尾 -t、进度）用的都是片段时长。
# 校验刻意全走 awk：这些值允许小数，bash 的 (( )) 只吃整数，写成 (( x > 0 )) 时
# 6.5 会打一行 syntax error 然后判假 → 静默当成"不裁剪"（用户以为跑了 6.5 秒、
# 实际拿到整片）。非法值一律报错，绝不静默放行。
SEG_START=${SS_SEC:-0}
SEG_END=""
SEGMENTED=false
if   [[ -n "$TO_SEC"  ]]; then
    SEG_END=$TO_SEC;  SEGMENTED=true
elif [[ -n "$DUR_SEC" ]]; then
    SEG_END=$(awk -v a="$SEG_START" -v t="$DUR_SEC" 'BEGIN{printf "%.4f", a+t}'); SEGMENTED=true
elif [[ -n "$CAP_SEC" ]]; then
    SEG_END=$(awk -v a="$SEG_START" -v t="$CAP_SEC" 'BEGIN{printf "%.4f", a+t}'); SEGMENTED=true
fi
if is_pos "$SEG_START"; then SEGMENTED=true; fi     # 只给 --SS（终点到源末尾）也算截取
SEG_END=${SEG_END:-$FULL_TOTAL}
if awk -v s="$SEG_START" -v t="$FULL_TOTAL" 'BEGIN{exit !(s >= t)}'; then
    die "--SS ${SS_RAW:-0}（= ${SEG_START}s）已到/超过源末尾（${FULL_TOTAL}s），没有可处理的画面"
fi
# 终点超过源末尾 → 夹到末尾（与旧 --cap 的超长行为一致，不报错）
if awk -v e="$SEG_END" -v t="$FULL_TOTAL" 'BEGIN{exit !(e > t)}'; then SEG_END=$FULL_TOTAL; fi
if awk -v a="$SEG_START" -v e="$SEG_END" 'BEGIN{exit !(e <= a)}'; then
    die "截取区间为空：起点 ${SEG_START}s，终点 ${SEG_END}s（终点必须大于起点）"
fi
TOTAL=$(awk -v a="$SEG_START" -v e="$SEG_END" 'BEGIN{printf "%.4f", e-a}')

# 给 ffmpeg 用的输入定位参数（视频侧）。
#   SS_OPTS —— 视频分片 / 试编码的起点（= --SS，SEG_START）
# --SS=0 时保持空数组 → 命令与旧版逐字节一致，不会有任何行为漂移。
# 音轨 copy 的起点额外补上片头裁掉的 HEAD_TRIM 帧，见下面后端装配之后的 AUD_SS_OPTS。
SS_OPTS=()
if is_pos "$SEG_START"; then SS_OPTS=(-ss "$SEG_START"); fi

NPARTS=$(awk -v t="$TOTAL" -v l="$L" 'BEGIN{ n=int(t/l); if (n*l < t-1e-9) n++; print n }')

# ------------------------- 环境自动探测（驱动并行度） -------------------------
# 口径照 vidcrop_cpu_v2.py：**先看 cgroup 配额，再看宿主核数**。容器里 nproc 报的是
# 宿主核数（本机 nproc=8，而 cgroup v2 cpu.max=200000/100000 说明真实配额只有 2 核），
# 按 8 核去铺并行会把自己挤死。
detect_cpu() {
    local q p
    if read -r q p < /sys/fs/cgroup/cpu.max 2>/dev/null; then
        case "$q" in
            ''|max) ;;
            *) if (( q > 0 && p > 0 )); then
                   CPU_CORES=$(awk -v q="$q" -v p="$p" 'BEGIN{printf "%d", q/p + 0.5}')
                   CPU_SRC="cgroup v2 $q/$p"
                   return
               fi ;;
        esac
    fi
    if read -r q < /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null \
       && read -r p < /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null; then
        if (( q > 0 && p > 0 )); then
            CPU_CORES=$(awk -v q="$q" -v p="$p" 'BEGIN{printf "%d", q/p + 0.5}')
            CPU_SRC="cgroup v1 $q/$p"
            return
        fi
    fi
    if command -v nproc >/dev/null 2>&1; then
        CPU_CORES=$(nproc 2>/dev/null || true)
        CPU_SRC="nproc（尊重 affinity）"
    else
        CPU_CORES=$(awk '/^processor/{n++} END{print n+0}' /proc/cpuinfo 2>/dev/null || true)
        CPU_SRC="/proc/cpuinfo"
    fi
    case "$CPU_CORES" in ''|*[!0-9]*) CPU_CORES=1; CPU_SRC="兜底" ;; esac
    if (( CPU_CORES < 1 )); then CPU_CORES=1; CPU_SRC="兜底"; fi
}

# 内存：cgroup v2（扣掉可回收的 file+slab_reclaimable）→ cgroup v1 → /proc/meminfo。
# 直接读 /proc/meminfo 在容器里会看到宿主的全部内存（本机 32GB vs 配额 4GB）。
detect_mem() {
    local limit cur reclaim non_reclaim avail
    if [[ -r /sys/fs/cgroup/memory.max ]] \
       && read -r limit < /sys/fs/cgroup/memory.max \
       && case "$limit" in ''|max) false ;; *) true ;; esac; then
        cur=0
        read -r cur < /sys/fs/cgroup/memory.current 2>/dev/null || cur=0
        case "$cur" in ''|*[!0-9]*) cur=0 ;; esac
        reclaim=$(awk '/^(file|slab_reclaimable) /{s+=$2} END{print s+0}' \
                  /sys/fs/cgroup/memory.stat 2>/dev/null || echo 0)
        non_reclaim=$(( cur - reclaim )); if (( non_reclaim < 0 )); then non_reclaim=0; fi
        avail=$(( limit - non_reclaim )); if (( avail < limit / 20 )); then avail=$(( limit / 20 )); fi
        MEM_TOTAL_GB=$(awk -v b="$limit" 'BEGIN{printf "%.2f", b/1073741824}')
        MEM_AVAIL_GB=$(awk -v b="$avail" 'BEGIN{printf "%.2f", b/1073741824}')
        MEM_SRC="cgroup v2"
        return
    fi
    if [[ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]] \
       && read -r limit < /sys/fs/cgroup/memory/memory.limit_in_bytes; then
        if (( limit > 0 && limit < 4611686018427387904 )); then
            cur=0
            read -r cur < /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || cur=0
            reclaim=$(awk '/^total_cache /{print $2}' \
                      /sys/fs/cgroup/memory/memory.stat 2>/dev/null || echo 0)
            non_reclaim=$(( cur - reclaim )); if (( non_reclaim < 0 )); then non_reclaim=0; fi
            avail=$(( limit - non_reclaim )); if (( avail < limit / 20 )); then avail=$(( limit / 20 )); fi
            MEM_TOTAL_GB=$(awk -v b="$limit" 'BEGIN{printf "%.2f", b/1073741824}')
            MEM_AVAIL_GB=$(awk -v b="$avail" 'BEGIN{printf "%.2f", b/1073741824}')
            MEM_SRC="cgroup v1"
            return
        fi
    fi
    read -r MEM_TOTAL_GB MEM_AVAIL_GB <<<"$(awk '
        /^MemTotal:/{t=$2} /^MemAvailable:/{a=$2}
        END{ if (a == "") a = t; printf "%.2f %.2f", t/1048576, a/1048576 }' \
        /proc/meminfo 2>/dev/null || true)"
    case "$MEM_TOTAL_GB" in ''|*[!0-9.]*) MEM_TOTAL_GB=0.00; MEM_AVAIL_GB=0.00 ;; esac
    MEM_SRC="/proc/meminfo"
}

# GPU 画像：只查，不判可用 —— 能不能用由下面的试编码说了算（运行时探测，不是字符串匹配）。
# 注意 nvidia-smi 在驱动缺失时会把报错**打到 stdout**（不是 stderr），且退出码非 0；
# 所以既要看退出码，也要校验解析出来的数字真的是数字 —— 否则会把
# "NVIDIA-SMI couldn't find libnvidia-ml.so…" 当成显卡型号。
GPU_NAME=""; GPU_CC=""; GPU_DRIVER=""; GPU_UTIL=""
GPU_VRAM_TOTAL_MB=""; GPU_VRAM_FREE_MB=""; GPU_BUSY_WHY=""
detect_gpu_info() {
    command -v nvidia-smi >/dev/null 2>&1 || return 1
    local out rc=0 line
    out=$(nvidia-smi --query-gpu=name,compute_cap,memory.total,memory.free,driver_version,utilization.gpu \
          --format=csv,noheader,nounits 2>/dev/null) || rc=$?
    if (( rc != 0 )); then out=""; fi
    line=$(printf '%s\n' "$out" | head -n 1)
    if [[ -n "$line" ]]; then
        line=${line//, /,}                      # csv 分隔是 ", "，去掉空格才好按逗号切
        IFS=',' read -r GPU_NAME GPU_CC GPU_VRAM_TOTAL_MB GPU_VRAM_FREE_MB GPU_DRIVER GPU_UTIL <<<"$line"
    else
        # 老驱动/字段不支持：退到较少字段再问一次（compute_cap 需要较新的驱动）
        out=$(nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version \
              --format=csv,noheader,nounits 2>/dev/null) || out=""
        line=$(printf '%s\n' "$out" | head -n 1)
        [[ -n "$line" ]] || return 1
        line=${line//, /,}
        IFS=',' read -r GPU_NAME GPU_VRAM_TOTAL_MB GPU_VRAM_FREE_MB GPU_DRIVER <<<"$line"
    fi
    # 显存字段必须是数字，否则说明拿到的是报错文本而不是数据
    case "$GPU_VRAM_TOTAL_MB" in ''|*[!0-9]*) return 1 ;; esac
    case "$GPU_VRAM_FREE_MB"  in ''|*[!0-9]*) return 1 ;; esac
    case "$GPU_UTIL"          in ''|*[!0-9]*) GPU_UTIL="" ;; esac
    # 有没有别人在用这张卡（你自己的增强流水线经常在跑，别把它当成"机器空闲"）。
    # 只认首字段是数字的行 —— 驱动坏了时 nvidia-smi 的报错又被当成"计算进程"了。
    local apps
    apps=$(nvidia-smi --query-compute-apps=pid,process_name,used_memory \
           --format=csv,noheader,nounits 2>/dev/null | awk '/^[0-9]/{printf "%s; ", $0}' || true)
    if [[ -n "${apps// /}" ]]; then
        GPU_BUSY_WHY="有计算进程占用：$apps"
    fi
    if [[ -n "$GPU_UTIL" ]] && (( GPU_UTIL >= 30 )); then
        GPU_BUSY_WHY="${GPU_BUSY_WHY:+$GPU_BUSY_WHY；}利用率已 ${GPU_UTIL}%"
    fi
    return 0
}

# 磁盘：2x 的产物与源同量级，跑几十片之前先看看够不够（只告警，不停机）。
DISK_FREE_GB=""
detect_disk() {
    command -v df >/dev/null 2>&1 || return 0
    local kb
    kb=$(df -Pk "$WORKDIR" 2>/dev/null | awk 'NR==2{print $4}' || true)
    case "$kb" in ''|*[!0-9]*) return 0 ;; esac
    DISK_FREE_GB=$(awk -v k="$kb" 'BEGIN{printf "%.1f", k/1048576}')
}

# 并发的其它实例：共用同一块 GPU 只会互相拖慢（同一个 -w 已由单实例锁挡住）。
# 判定必须精确，不然 100% 误报 —— 踩过的三个坑：
#   ① 调用者那条命令行里通常就写着本脚本路径（`setsid bash …/interp_2x_safe.sh …`），
#      按 `pgrep -f` 直接找字符串会把包装 shell 也算成实例；
#   ② bash 的 fork 出来的子 shell（命令替换、`( … ) &`）在 /proc/PID/cmdline 里与
#      父进程**一字不差**，每次 $(awk …) 都会被当成"另一个实例"；
#   ③ 收紧成"首个参数是 shell"也不行：`/bin/zsh -c …` 的首个参数是以 sh 结尾的
#      zsh，而它的**兄弟**子 shell（同一个 zsh -c 命令行）正好不在我们的进程树里。
# 所以直接读 /proc/<pid>/cmdline 逐个参数看：必须是「某个 shell + 本脚本作为第一个参数」。
fmt_size_gb() {
    awk -v b="${1:-0}" 'BEGIN{
        if (b < 1073741824) printf "%.1fMB", b/1048576; else printf "%.1fGB", b/1073741824 }'
}
OTHERS_N=0; OTHERS_PIDS=""
_SELF=" $$ "                       # 自己的祖先链 + 子孙树（命令替换 fork 出的子 shell 同名）
_p=$$; _i=0
while [[ -n "$_p" && "$_p" != 0 && "$_p" != 1 && $_i -lt 8 ]]; do
    _p=$(ps -o ppid= -p "$_p" 2>/dev/null | tr -d ' \n' || true)
    [[ -n "$_p" && "$_p" != 0 && "$_p" != 1 ]] || break
    _SELF="$_SELF$_p "
    _i=$(( _i + 1 ))
done
_frontier="$$"
for (( _d=0; _d<4; _d++ )); do
    _next=""
    for _p in $_frontier; do
        for _k in $(ps -o pid= --ppid "$_p" 2>/dev/null | tr -d ' ' || true); do
            _SELF="$_SELF$_k "; _next="$_next$_k "
        done
    done
    [[ -n "$_next" ]] || break
    _frontier=$_next
done
_self_name=$(basename -- "$0")
_cands=""
for _d in /proc/[0-9]*; do
    _pid=${_d#/proc/}
    case "$_SELF" in *" $_pid "*) continue ;; esac
    mapfile -d '' -t _argv < "$_d/cmdline" 2>/dev/null || continue
    (( ${#_argv[@]} >= 2 )) || continue
    case "${_argv[0]##*/}" in
        bash|sh|dash|ash|ksh|zsh) ;;
        *) continue ;;
    esac
    # basename 要加 --：别的进程第一个参数可能是 "-c" 这种以 - 开头的东西
    [[ "$(basename -- "${_argv[1]}")" == "$_self_name" ]] || continue
    _cands="$_cands$_pid "
done
# 另一个实例自己的子 shell 同样带这个名字（改不了），所以只保留"祖先里没有其它
# 候选者"的那些 —— 否则一个正在跑的实例会把自己的一串子 shell 都报成实例。
for _pid in $_cands; do
    _q=$_pid; _j=0; _is_child=0
    while [[ -n "$_q" && "$_q" != 0 && "$_q" != 1 && $_j -lt 8 ]]; do
        _q=$(ps -o ppid= -p "$_q" 2>/dev/null | tr -d ' \n' || true)
        [[ -n "$_q" && "$_q" != 0 && "$_q" != 1 ]] || break
        case " $_cands " in *" $_q "*) _is_child=1; break ;; esac
        _j=$(( _j + 1 ))
    done
    if (( _is_child == 0 )); then OTHERS_PIDS="${OTHERS_PIDS}${_pid}"$'\n'; fi
done
unset _p _i _d _j _k _next _frontier _SELF _pid _q _argv _self_name _cands _is_child
if [[ -n "${OTHERS_PIDS//[$'\n' ]/}" ]]; then
    OTHERS_N=$(printf '%s\n' "$OTHERS_PIDS" | grep -c '[0-9]' || true)
fi

detect_cpu
detect_mem
detect_disk
log "环境  : CPU 核 ${CPU_CORES} (${CPU_SRC})  MEM 总 ${MEM_TOTAL_GB}GB 可用 ${MEM_AVAIL_GB}GB (${MEM_SRC})"
SRC_BYTES=$(stat -c%s "$IN" 2>/dev/null || echo 0)
if [[ -n "$DISK_FREE_GB" ]]; then
    log "磁盘  : $WORKDIR 可用 ${DISK_FREE_GB}GB（源 $(fmt_size_gb "$SRC_BYTES")，2x 产物与源同量级）"
    if awk -v f="$DISK_FREE_GB" -v b="$SRC_BYTES" 'BEGIN{exit !(f*1073741824 < 3*b)}'; then
        log "提示  : 磁盘余量不到源文件的 3 倍，分片+成片可能写不下；先腾点空间或换 -w"
    fi
fi
if (( OTHERS_N > 0 )); then
    log "提示  : 检测到另 ${OTHERS_N} 个 interp_2x_safe.sh 实例在跑（pid $(printf '%s' "$OTHERS_PIDS" | tr '\n' ' ')）"
    log "        共用同一块 GPU 时只会互相拖慢；确认不是重复起了同一个任务"
fi
if detect_gpu_info; then
    log "GPU   : ${GPU_NAME}${GPU_CC:+ (CC ${GPU_CC})}${GPU_DRIVER:+, 驱动 ${GPU_DRIVER}}  显存 ${GPU_VRAM_FREE_MB}/${GPU_VRAM_TOTAL_MB}MB 可用${GPU_UTIL:+, 利用率 ${GPU_UTIL}%}"
    if [[ -n "$GPU_BUSY_WHY" ]]; then
        log "提示  : GPU 已有负载（${GPU_BUSY_WHY}）—— 耗时与并行收益都会受影响，"
        log "        先确认不是在跑你自己的增强流水线（memory/project_t4_gpu_capabilities.md）"
    fi
    case "$GPU_VRAM_FREE_MB" in
        ''|*[!0-9]*) ;;
        *) if (( GPU_VRAM_FREE_MB < 1536 )); then
               log "提示  : 显存只剩 ${GPU_VRAM_FREE_MB}MB，试编码很可能失败"
           fi ;;
    esac
fi

# --- GPU 参数装配 -------------------------------------------------------------
# $HEAD_TRIM / $FC / $DEC_OPTS / $ENC_OPTS 成套定义；HEAD_TRIM 会写进 recipe.txt，
# 混用不同 trim 的分片目录会被拒绝。
setup_gpu() {
    DEC_OPTS=(-hwaccel cuda -hwaccel_output_format cuda)
    FC="nvinterpolate=fps=source_fps*2,trim=start_frame=${HEAD_TRIM},setpts=PTS-STARTPTS"
    ENC_OPTS=(-c:v hevc_nvenc -preset "$PRESET" -cq "$CQ")
}

# GPU 到底能不能用：**跑一遍才知道**。只查 nvidia-smi / 滤镜清单是不够的 ——
# 驱动在但显存被占满、CUDA 解码不了这个源的编码格式、FRUC 初始化失败……
# 这些都要真跑一次才暴露。只读 4 帧写 /dev/null，成本可忽略。
# 传 k>1 就是"确认 k 路并发真能同时跑起来"（NVENC 会话数 / 显存 / 驱动限制）。
GPU_WHY=""
probe_gpu_one() {
    local rc=0 err
    mkdir -p "$CWDROOT/probe"
    # 2>&1 >/dev/null：只留 stderr（给回退原因用），stdout 的 -f null 输出丢掉。
    # 在独立 CWD 里跑：nvinterpolate 会往当前目录写 NvOFFRUC/。
    err=$(
        cd "$CWDROOT/probe" || exit 1
        exec 9>&-        # 同上：试编码的 ffmpeg 也不该持有分片目录的锁
        exec "$FFMPEG" -nostdin -y -hide_banner -loglevel error -nostats \
            "${DEC_OPTS[@]}" "${SS_OPTS[@]}" -i "$IN_ABS" -frames:v 4 \
            -filter_complex "$FC" -fps_mode passthrough \
            "${ENC_OPTS[@]}" -an -f null - 2>&1 >/dev/null
    ) || rc=$?
    if (( rc != 0 )); then
        printf '%s\n' "$(printf '%s' "$err" | tail -n 1)" >> "$PROBE_ERR_FILE"
    fi
    return "$rc"
}

probe_gpu() {
    local k=$1 i pids=() rc=0 why=""
    command -v nvidia-smi >/dev/null 2>&1 || { GPU_WHY="没有 nvidia-smi"; return 1; }
    nvidia-smi -L >/dev/null 2>&1 || { GPU_WHY="nvidia-smi 起不来（驱动没装好？）"; return 1; }
    have_filter nvinterpolate \
        || { GPU_WHY="$FFMPEG 里没有 nvinterpolate 滤镜"; return 1; }
    have_encoder hevc_nvenc \
        || { GPU_WHY="$FFMPEG 里没有 hevc_nvenc 编码器"; return 1; }
    : > "$PROBE_ERR_FILE" 2>/dev/null || true
    for (( i=0; i<k; i++ )); do
        probe_gpu_one &
        pids+=("$!")
    done
    for i in "${pids[@]}"; do
        wait "$i" || rc=1
    done
    if (( rc != 0 )); then
        if [[ -s "$PROBE_ERR_FILE" ]]; then why=$(head -n 1 "$PROBE_ERR_FILE"); fi
        GPU_WHY="试编码失败（${k} 路并发，CUDA 解码 / FRUC / nvenc 至少一处不可用）${why:+：$why}"
        return 1
    fi
    return 0
}

# 显式 -j K：先确认 K 路并发真的能跑；跑不了 k 路但能跑 1 路时降为 1 路并说明，
# 而不是让长任务在第 3 片才炸。auto 且未给 -j 时 K=1，等价老的单路探针。
PROBE_K=1; if (( JOBS_OPT > 1 )); then PROBE_K=$JOBS_OPT; fi
if (( PROBE_K > NPARTS )); then PROBE_K=$NPARTS; fi
gpu_probe_adjust() {
    if probe_gpu "$PROBE_K"; then return 0; fi
    local why="$GPU_WHY"
    if (( PROBE_K > 1 )) && probe_gpu 1; then
        GPU_WHY="$why"
        log "提示  : GPU 能跑 1 路但 ${PROBE_K} 路并发试编码失败 → 并行度降为 1（$why）"
        JOBS_OPT=1
        return 0
    fi
    GPU_WHY="$why"
    return 1
}

# GPU 专版：只有这一条后端。装配好参数后用真输入试编码（必要时 K 路并发），
# 不通过就直接报错退出 —— 没有 CPU 回退可选（要那个请用 interp_2x_safe_v1.sh）。
setup_gpu
gpu_probe_adjust || die "GPU 不可用，无法开工: $GPU_WHY
  本脚本是 GPU 专版（nvinterpolate + hevc_nvenc），没有 CPU 回退后端。
  要 CPU 回退（minterpolate + libx265，慢一到两个数量级）请改用 interp_2x_safe_v1.sh；
  或先修好驱动 / ffmpeg 滤镜与编码器 / 显存占用。"

# --- 音轨起点偏移（收尾 copy 用）----------------------------------------------
# 放在后端装配【之后】：HEAD_TRIM 是后端属性（本脚本 setup_gpu 里 = GPU_HEAD_TRIM = 3）。
# 每个分片都会丢掉 nvinterpolate 的 HEAD_TRIM 帧无效前导，再用 -t 从源尾部补满 L 秒。
# 这等价于**整条视频内容相对时间戳前移了 HEAD_TRIM / 目标帧率 秒**
# （48000/1001 下 3 帧 = 62.56ms）。音轨是原样 copy、时间戳没动，所以必须让音轨也
# 前移同样多，否则画面比声音超前。实测（4s / 25→50fps，白帧+click 同刻）：
#   修复前 白帧 0.9375s / click 1.0000s（偏差 62.5ms）；修复后两者都 0.9375s（偏差 0.0ms）。
# 逐帧对比"一次跑完"与"两片拼接"也证明该偏移是**常数**（同帧号对齐 43.3dB、
# 平移 3 帧只 22.2dB），不随分片数累积 —— 即接缝处不会跳，只是整体差这一点。
HEAD_SHIFT=$(awk -v k="$HEAD_TRIM" -v r="$TARGET_RATE" 'BEGIN{
    split(r, a, "/"); f = a[1] / (a[2] == "" ? 1 : a[2]); printf "%.6f", (f > 0 ? k / f : 0) }')
AUD_SS=$(awk -v s="$SEG_START" -v h="$HEAD_SHIFT" 'BEGIN{printf "%.6f", s + h}')
AUD_SS_OPTS=()
if is_pos "$AUD_SS"; then AUD_SS_OPTS=(-ss "$AUD_SS"); fi

# ------------------------------- 并行度 --------------------------------------
#   GPU_JOBS_AUTO=1      见文件头：T4 实测 NVENC 单引擎，多路并发总吞吐基本不变，
#                        所以 auto 固定 1 路，想要重叠收益得显式 -j
GPU_JOBS_AUTO=1

vram_per_job_profile() {     # 单任务显存画像（GB）：仅用于给出"建议 ≤ N"的告警
    local h=${1:-0}
    if   (( h <= 1080 )); then printf '0.7'
    elif (( h <= 1440 )); then printf '1.2'
    elif (( h <= 2160 )); then printf '2.2'
    else printf '4.0'; fi
}

if (( JOBS_OPT > 0 )); then
    JOBS=$JOBS_OPT
    JOBS_SRC="显式 -j"
else
    JOBS=$GPU_JOBS_AUTO
    JOBS_SRC="GPU 单引擎实测，-j 可覆盖"
fi
if (( JOBS > NPARTS )); then JOBS=$NPARTS; fi
if (( JOBS < 1 )); then JOBS=1; fi

# 每片线程数（THREADS）：GPU 后端**默认不下发** —— 瓶颈在 GPU 引擎，锁 CPU 线程数只会拖慢喂数据；
# 显式给 --threads N>0 才照实下发。
# 抽成函数：JOBS 在 TODO 扫描之后还会因为"实际要编几片"再收敛一次，那时 THREADS 要跟着回算。
set_threads_from_jobs() {
    if (( THREADS_OPT > 0 )); then
        THREADS=$THREADS_OPT
    else
        THREADS=0
    fi
    THREAD_OPTS=(); FCTHREAD_OPTS=()
    if (( THREADS > 0 )); then
        THREAD_OPTS=(-threads "$THREADS")
        FCTHREAD_OPTS=(-filter_complex_threads "$THREADS")
    fi
}
set_threads_from_jobs

# 用户显式开多路时，对照画像给个"建议值"，只告警不拦 —— 他要压满机器是他的选择。
if (( JOBS > 1 )); then
    if [[ -n "$GPU_VRAM_FREE_MB" ]]; then
        ADVISED_VRAM=$(awk -v f="$GPU_VRAM_FREE_MB" -v v="$(vram_per_job_profile "$SRC_H")" \
            'BEGIN{ n = int(f/1024*0.8/v); if (n < 1) n = 1; print n }')
        if (( JOBS > ADVISED_VRAM )); then
            log "提示  : -j ${JOBS} 偏高：按 ${SRC_H}p 每路约 $(vram_per_job_profile "$SRC_H")GB 估算，"
            log "        当前可用显存 ${GPU_VRAM_FREE_MB}MB 只够约 ${ADVISED_VRAM} 路（显存不足会让试编码/编码失败）"
        fi
    fi
    ADVISED_CPU=$(( CPU_CORES / 2 )); if (( ADVISED_CPU < 1 )); then ADVISED_CPU=1; fi
    if (( JOBS > ADVISED_CPU )); then
        log "提示  : -j ${JOBS} 偏高：CPU 只有 ${CPU_CORES} 核（cgroup 配额，${CPU_SRC}），建议 ≤ ${ADVISED_CPU}"
    fi
fi

# --- 防呆第一层：会改变「每一片」内容的参数 → 整体拒绝 -----------------------
# recipe.txt 记 backend|enc|in|L|trim（+ 可选 ss）。这些一变，目录里**所有**
# 分片的内容都不同（帧边界变了 / 画质档变了 / 换了视频 / 换了后端），复用就是
# 静默产出错内容。
# 注意 total 不在里面：总时长只影响【末尾那一片】的切法，这种变化由每片的
# .meta 逐片判断（第二层），只重编边界片即可，不必整体拒绝。
# --SS 起点虽然也算"边界"，但它平移的是**每一片**的 -ss（片 0 也变），所以它
# 必须进 recipe，跟 L 同一个性质；--TO / -T / --cap 只动末尾那一片 → 不进 recipe。
ENC_TAG="hevc_nvenc|$PRESET|cq=$CQ"
# backend 固定 gpu —— 保留该字段是为了和通用版 interp_2x_safe_v1.sh 共用分片目录。
RECIPE="slice=3|backend=gpu|enc=$ENC_TAG|in=$IN|L=$L|trim=$HEAD_TRIM"
# ss 只在 >0 时追加：--SS 为 0 时 recipe 与旧版逐字节一致，已经在跑的长任务
# 不会因为脚本升级被整体拒绝；带 --SS 的新任务换一个 -w 目录即可。
if is_pos "$SEG_START"; then
    RECIPE="${RECIPE}|ss=$(fmt_num "$SEG_START")"
fi
# 加 backend 字段之前的格式（= 更早的 GPU-only 版本写的）：遇到它就就地升级，
# 免得已经在跑/已跑完的老目录因为脚本升级被迫从头重编。
LEGACY_RECIPE="slice=3|in=$IN|L=$L|preset=$PRESET|cq=$CQ|trim=$HEAD_TRIM"
STAMP="$WORKDIR/recipe.txt"
if [[ -f "$STAMP" ]]; then
    OLD_RECIPE=$(cat "$STAMP")
    if [[ "$OLD_RECIPE" == "$RECIPE" ]]; then
        :
    elif [[ "$OLD_RECIPE" == "$LEGACY_RECIPE" ]] && ! is_pos "$SEG_START"; then
        printf '%s\n' "$RECIPE" > "$STAMP"
        log "recipe 升级（补 backend 字段）: $OLD_RECIPE -> $RECIPE"
    else
        die "分片目录与当前任务不匹配，拒绝复用:
  目录 : $WORKDIR
  已有 : $OLD_RECIPE
  本次 : $RECIPE
换输入 / 换 --SS 起点请另给 WORKDIR，或删掉该目录后重跑。"
    fi
else
    printf '%s\n' "$RECIPE" > "$STAMP"
fi

if (( THREADS > 0 )); then THREADS_TXT="${THREADS} 线程/片"; else THREADS_TXT="ffmpeg 自选"; fi
log "后端  : gpu（nvinterpolate + hevc_nvenc）"
log "输入  : $IN  (${SRC_W}x${SRC_H} @ ${SRC_RATE})"
if [[ "$SEGMENTED" == true ]]; then
    log "片段  : ${SEG_START}s → ${SEG_END}s（时长 ${TOTAL}s；源整片 ${FULL_TOTAL}s）"
fi
log "输出  : $OUT"
log "分片  : $NPARTS 片 x ${L}s  ->  $PARTS"
log "编码  : ${ENC_OPTS[*]}（视频）；音轨收尾时 -c copy"
log "插帧  : $FC  （源 ${SRC_RATE} → 目标 ${TARGET_RATE}）"
log "并发  : ${JOBS} 路 × ${THREADS_TXT}（${JOBS_SRC}）"

# ------------------------- 逐片编码（最多 JOBS 路并行） -----------------------
# 分片之间互不依赖（各自 -ss + 独立滤镜实例 + 独立 TS），所以可以同时跑。
# 但**判定顺序与日志格式必须和顺序模式一致**（skip/redo/run/done 的文案与次数
# 都有回归测试盯着），所以先把待办清单算出来，再按序派发。
declare -a TODO_TAG=() TODO_SS=() TODO_DT=()
for (( i=0; i<NPARTS; i++ )); do
    tag=$(printf 'p%05d' "$i")
    part="$PARTS/$tag.ts"
    meta="$PARTS/$tag.meta"

    # 先把本片"应该怎么切"算出来，再拿去和元数据比 —— 所以这段必须在
    # 跳过判断之前。
    # S = 片段内偏移（0/L/2L…），ss = 该片在【源视频】里的绝对 -ss（含 --SS 偏移）。
    # .meta 记的是绝对 ss：它就是这个片真实用的切法，且 --SS 已被 recipe 钉住，
    # 所以同一目录里绝对/相对只是记法差别，不存在歧义。
    # fmt_num 让 --SS=0 时仍是 `0 / 2 / 4`（不是 0.0000），与旧版逐字节一致。
    S=$(( i * L ))
    ss=$(fmt_num "$SEG_START" "$S")
    dt=$(awk -v t="$TOTAL" -v s="$S" -v l="$L" \
         'BEGIN{ d=t-s; if (d>l) d=l; printf "%.4f", d }')
    want="$ss $dt"

    # --- 防呆第二层：逐片判断切法 -------------------------------------------------
    # 「可复用」= 分片文件在 + .meta 在 + .meta 记的切法与本片需要的完全一致。
    # 只有末尾那一片的 dt 会随片段终点变，所以改 --TO / -T / --cap 时最多只重编
    # 边界那一片，前面的片照旧复用 —— 不需要整体拒绝，也不会拼错。
    # （--SS 会改所有片的 ss，所以它在上面第一层 recipe 里整体拦，走不到这里。）
    if [[ -f "$part" && -f "$meta" ]]; then
        if [[ "$(cat "$meta")" == "$want" ]]; then
            log "skip  $tag  （已完成，$(stat -c%s "$part") bytes）"
            continue
        fi
        log "redo  $tag  （切法变了: 记 '$(cat "$meta")' → 需 '$want'）"
    elif [[ -f "$part" ]]; then
        log "redo  $tag  （缺 $tag.meta，无法确认切法）"
    fi

    TODO_TAG+=("$tag"); TODO_SS+=("$ss"); TODO_DT+=("$dt")
done
TODO=${#TODO_TAG[@]}

# 并发度再看一眼"这次到底要编几片"：NPARTS 是**总**片数，而复用（skip）之后真正要编的
# 只剩 TODO 片。没那么多活时铺那么多路只会白占资源、日志也误导（说 4 路其实只跑 2 片）。
# THREADS 跟着回算 —— 活少了就给每片多分点线程。
if (( TODO > 0 && JOBS > TODO )); then
    JOBS_OLD=$JOBS
    JOBS=$TODO
    set_threads_from_jobs
    log "并发  : 只剩 ${TODO} 片要编（共 ${NPARTS} 片，其余复用）→ 由 ${JOBS_OLD} 路收敛为 ${TODO} 路${THREADS:+、每片 ${THREADS} 线程}"
fi

# 单片任务（在【后台子 shell】里跑）：
#   · 自己的 CWD（runlogs/cwd/<片名>）：nvinterpolate 会往当前工作目录写
#     NvOFFRUC/logFRUCError.txt，N 路并发必须各写各的；成功就删掉，失败才留下
#   · 自己的日志文件：并发时若都写主日志会互相插花
#   · 不预先删旧 part：ffmpeg 写临时文件，成功后 mv 覆盖 —— 中途失败时旧分片+旧
#     meta 原样留着，下次仍判定为"需重做"。临时名带 PID（$$ 在子 shell 里仍是父
#     PID）、片名又各不相同，所以并发也不会互相踩
#   · ffmpeg 用**后台 + wait** 起、并且在本子 shell 里装 TERM/INT trap 转发：
#     不能用 `exec ffmpeg` —— 那样杀掉本子 shell（父进程 trap 里做的事）之后
#     ffmpeg 会变成孤儿继续写盘（实测 `kill <脚本pid>` 后还剩 2 个 ffmpeg 在跑）
#   · 结果写进事件文件（单行追加是原子的），父进程据此计数、打印、决定是否续派
run_job() {
    local tag=$1 ss=$2 dt=$3
    local part="$PARTS/$tag.ts" meta="$PARTS/$tag.meta"
    local tmp="$part.part.$$" jlog="$RUNLOGS/$tag.log" jcwd="$CWDROOT/$tag"
    local rc=0 t0=$SECONDS size=0
    rm -f "$tmp"
    if ! mkdir -p "$jcwd"; then
        printf '%s 1 0 0\n' "$tag" >> "$EVENTS"
        return 0
    fi
    (
        cd "$jcwd" || exit 1
        # 关掉继承来的锁 fd（父进程的 9）：**别把锁泄漏给 ffmpeg**。
        # 否则脚本被 kill -9 后，孤儿 ffmpeg 会一直占着锁 → 下次启动在 flock 那步就死了，
        # "启动时清孤儿"永远走不到（实测事故）。子 shell 自己不需要这把锁。
        exec 9>&-
        "$FFMPEG" -nostdin -y -hide_banner -loglevel warning -nostats \
            "${DEC_OPTS[@]}" "${THREAD_OPTS[@]}" \
            -ss "$ss" -i "$IN_ABS" -t "$dt" \
            "${FCTHREAD_OPTS[@]}" -filter_complex "$FC" -fps_mode passthrough \
            "${THREAD_OPTS[@]}" "${ENC_OPTS[@]}" -an \
            -f mpegts "$tmp" &
        local fpid=$!
        # 收到信号先把 ffmpeg 带走再退：退出码 143 不会被当成"编码失败"，
        # 半成品留在 pXXXXX.ts.part.<pid>，重跑即续
        trap 'kill -TERM "$fpid" 2>/dev/null; exit 143' TERM INT HUP
        wait "$fpid"
    ) >"$jlog" 2>&1 || rc=$?
    if (( rc == 0 )); then
        if [[ -f "$tmp" ]]; then
            mv -f "$tmp" "$part" || rc=1
        else
            rc=98      # ffmpeg 返回 0 却没产出文件（磁盘满/锁被绕过），单独报
        fi
    fi
    if (( rc == 0 )); then
        printf '%s\n' "$ss $dt" > "$meta"   # 先就位分片、再写 meta → 半成品永不被当可用
        size=$(stat -c%s "$part" 2>/dev/null || echo 0)
        rm -rf "$jcwd"
    fi
    # 被信号打断时不写事件：父进程此刻正在退出，写了反而会多出一行"失败"
    if (( rc != 143 )); then
        printf '%s %s %s %s\n' "$tag" "$rc" "$size" "$(( SECONDS - t0 ))" >> "$EVENTS"
    fi
    return 0
}

# 完成事件收集：从上次读到的偏移继续读，打印 done/FAIL（失败附带该片日志末几行），
# 并顺带打一行进度 + ETA。
fmt_secs() {   # 秒 → 1h02m / 4m12s / 8s
    local s=${1:-0}
    if   (( s >= 3600 )); then printf '%dh%02dm' $(( s / 3600 )) $(( (s % 3600) / 60 ))
    elif (( s >= 60 ));   then printf '%dm%02ds' $(( s / 60 )) $(( s % 60 ))
    else printf '%ds' "$s"; fi
}
EVENT_READ=0; COMPLETED=0; DONE_PARTS=0; RUN_FAILED=0
drain_events() {
    local line tag rc sz el remain n=0
    [[ -s "$EVENTS" ]] || return 0
    # 用普通文件重定向逐行读，**不要**写成 `done < <(tail …)` 或 `tail … | while`：
    # 实测那样会和还在跑的后台任务共享管道写端，读端永远等不到 EOF →
    # 脚本卡在 pipe_read 上一动不动（0% CPU、日志停在某一行）。文件重定向既没有
    # 管道也没有 fork，而且 EVENT_READ 直接取"实际读过几行"，顺带消灭了
    # "tail 读的快照 / wc -l 数的行数"之间的竞态（那会让某条 done 被漏掉）。
    while IFS= read -r line; do
        n=$(( n + 1 ))
        if (( n <= EVENT_READ )); then continue; fi
        [[ -n "$line" ]] || continue
        read -r tag rc sz el <<<"$line"
        case "$rc" in ''|*[!0-9]*) rc=1 ;; esac
        case "$el" in ''|*[!0-9]*) el=0 ;; esac
        COMPLETED=$(( COMPLETED + 1 ))
        if (( rc == 0 )); then
            log "done  $tag  $sz bytes"
            DONE_PARTS=$(( DONE_PARTS + 1 ))
        else
            RUN_FAILED=1
            if (( rc == 98 )); then
                log "FAIL  $tag（ffmpeg 返回成功但没有产出 $PARTS/$tag.ts）
  常见原因：磁盘满（df -h /）或锁被绕过、有另一个实例在写同一个 -w 目录。"
            else
                log "FAIL  $tag（rc=$rc，半成品保留在 $PARTS/$tag.ts.part.$$）"
            fi
            if [[ -s "$RUNLOGS/$tag.log" ]]; then
                log "      该片日志末 20 行（完整日志：$RUNLOGS/$tag.log）："
                mapfile -t _tail < "$RUNLOGS/$tag.log"   # 同样用文件重定向，不走管道
                local _b=$(( ${#_tail[@]} > 20 ? ${#_tail[@]} - 20 : 0 ))
                local _i
                for (( _i=_b; _i<${#_tail[@]}; _i++ )); do
                    printf '        %s\n' "${_tail[_i]}"
                done
                unset _tail
            fi
        fi
        if (( TODO >= 3 && COMPLETED < TODO && DONE_PARTS > 0 )); then
            remain=$(( (SECONDS - SCHED_START) * (TODO - COMPLETED) / DONE_PARTS / JOBS ))
            log "进度  ${COMPLETED}/${TODO} 片  并行 ${JOBS}  用时 $(fmt_secs $(( SECONDS - SCHED_START )))  预计剩余 $(fmt_secs "$remain")"
        fi
    done < "$EVENTS"
    EVENT_READ=$n
}

# 回收已结束的子进程。**不要用 `wait -n` 当"完成信号"** —— 实测它会在仍有任务
# 在跑的时候就返回 0（我们有 1 片还在编码，它却立刻返回），拿它驱动调度会漏片、
# 甚至空转。完成与否一律以事件文件为准；这里只负责别攒僵尸，并回答"还有活的吗"。
reap_children() {
    local p alive=0
    for p in "${CHILD_PIDS[@]}"; do
        if kill -0 "$p" 2>/dev/null; then
            alive=1
        else
            wait "$p" 2>/dev/null || true
        fi
    done
    ANY_ALIVE=$alive
}

if (( TODO > 0 )); then
    : > "$EVENTS"
    SCHED_START=$SECONDS
    DISPATCHED=0; NEXT=0; ANY_ALIVE=1
    while (( COMPLETED < TODO )); do
        # 一旦知道有片失败就不再派发新的（在飞的照旧让它跑完，成果可复用）
        while (( NEXT < TODO && RUN_FAILED == 0 && DISPATCHED - COMPLETED < JOBS )); do
            log "run   ${TODO_TAG[$NEXT]}  -ss ${TODO_SS[$NEXT]} -t ${TODO_DT[$NEXT]}"
            run_job "${TODO_TAG[$NEXT]}" "${TODO_SS[$NEXT]}" "${TODO_DT[$NEXT]}" &
            CHILD_PIDS+=("$!")
            DISPATCHED=$(( DISPATCHED + 1 ))
            NEXT=$(( NEXT + 1 ))
        done
        # 失败即停止派发，但**不打断在飞的**：它们跑完的成果照样留在 parts/ 里可复用
        if (( RUN_FAILED )); then
            log "停止派发新片，先等在跑的 $(( DISPATCHED - COMPLETED )) 片收尾"
            break
        fi
        if (( DISPATCHED > COMPLETED )); then
            drain_events
            if (( COMPLETED >= TODO )); then break; fi
            reap_children
            # 兜底：**没得可派**（NEXT 已到底）、也没人在跑，结果却没前进 → 与其空转，
            # 不如带着现场退出。注意必须同时看 NEXT：只有一片在跑时，它一结束就会出现
            # "无活着的子进程"，但那是正常的空槽待派状态，不是错误。
            if (( ANY_ALIVE == 0 && NEXT >= TODO )); then
                die "内部错误：还有 $(( TODO - COMPLETED )) 片没有回报结果，但已无在跑的任务。
  已完成的分片与 runlogs/ 都还在；原样重跑即可续（已完成的分片会 skip）。"
            fi
            sleep 0.2       # 轮询间隔：只在"没等到新结果"时小睡，对分钟级任务可忽略
        else
            break
        fi
    done
    wait 2>/dev/null || true      # 收剩余在飞的（失败路径也在这里收干净）
    drain_events
    CHILD_PIDS=()
    if (( RUN_FAILED )); then
        die "有分片失败（见上面的 FAIL 与该片日志）；已停在原地，修好后原样重跑即从断点续"
    fi
fi

# ------------------------------- 收尾拼接 ------------------------------------
log "全部分片就绪，拼接 + 复制音轨"
# 只列本次要用的 0..NPARTS-1。不能 glob p*.ts —— 改小 TOTAL_CAP 时目录里会
# 留下更多分片，glob 会把它们一起拼进去。
#
# 每个 file 后面跟一条 duration：concat 解复用器用它来决定"下一片从什么时候开始"。
# 不写的话它只能从上一片 TS 里推断时长，而 TS 的结尾时长本来就是推断值 ——
# 首片比后续片少一帧时（实测 -T 100 -L 20：p00000 958 帧 / 其余 959 帧），
# 第 1 个接缝处下一片会提前约 1 帧落位，concat 便报
#   "Non-monotonic DTS ... changing to ...; This may result in incorrect timestamps"
# 并留下一个 11µs 的畸形帧间隔（S02E05 各片等长时 0 处、L=20 时 1 处，实测对照）。
# 写上显式 duration 后接缝落在精确的帧网格上：该告警消失、畸形间隔归零，帧数不变。
# 这里用的 dt 与逐片编码用的是同一套算法（末尾片=剩余时长），所以 sum(dt)==TOTAL。
: > "$LIST"
for (( i=0; i<NPARTS; i++ )); do
    tag=$(printf 'p%05d' "$i")
    dt=$(awk -v t="$TOTAL" -v s="$(( i * L ))" -v l="$L" \
         'BEGIN{ d=t-s; if (d>l) d=l; printf "%.4f", d }')
    printf "file '%s'\nduration %s\n" "$PARTS/$tag.ts" "$dt" >> "$LIST"
done

# 音轨从原片一次性 copy（音频没被改过）。写成 1:a:0? ——
# 末尾的 ? 表示"没有这条流就忽略"，否则输入本身无音轨时 map 会直接报错。
# 起点用 AUD_SS_OPTS（= --SS + 片头裁掉的 HEAD_TRIM 帧，推导见上面 SS_OPTS 段）。
# -t "$TOTAL" 是片段时长，音频和视频因此长度一致。
# 临时名保留与 $OUT 相同的扩展名，否则 ffmpeg 会按 .part 猜不到容器。
if is_pos "$HEAD_SHIFT"; then
    log "对齐  : 音轨起点再前移 ${HEAD_SHIFT}s（片头裁掉 ${HEAD_TRIM} 帧 → 视频内容整体前移，音轨同量对齐）"
fi
OUT_TMP="$OUT.part.${OUT##*.}"
if ! "$FFMPEG" -nostdin -y -hide_banner -loglevel warning -nostats \
        -f concat -safe 0 -i "$LIST" "${AUD_SS_OPTS[@]}" -i "$IN" \
        -map 0:v:0 -map 1:a:0? -c copy -t "$TOTAL" "${MOVFLAGS[@]}" \
        "$OUT_TMP"; then
    die "拼接失败（分片都还在 $PARTS，可手工重拼）"
fi
mv -f "$OUT_TMP" "$OUT"

log "完成  : $OUT"

# --- 成片属性（一次 ffprobe 拿全）---------------------------------------------
# 按 key 解析而不是按列的位置：ffprobe 的 csv 输出顺序是**结构体固定序**，
# 跟你 -show_entries 里写的顺序无关（实测 stream=width,height,r_frame_rate,…
# 会输出成 codec_name,width,height,pix_fmt,r_frame_rate,…）。按 key 取才不会读反。
# 不要 -count_frames：那会把整片解码一遍。
OUT_META=$("$FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_frames:format=duration,size,bit_rate \
    -of default=nw=1 "$OUT" 2>/dev/null || true)
V_CODEC=""; V_W=""; V_H=""; V_PIX=""; V_RATE=""; V_FRAMES=""
FMT_DUR=""; FMT_SIZE=""; FMT_BR=""
while IFS='=' read -r _k _v; do
    case "$_k" in
        codec_name)   V_CODEC=$_v ;;
        width)        V_W=$_v ;;
        height)       V_H=$_v ;;
        pix_fmt)      V_PIX=$_v ;;
        r_frame_rate) V_RATE=$_v ;;
        nb_frames)    V_FRAMES=$_v ;;
        duration)     FMT_DUR=$_v ;;
        size)         FMT_SIZE=$_v ;;
        bit_rate)     FMT_BR=$_v ;;
    esac
done <<<"$OUT_META"
A_META=$("$FFPROBE" -v error -select_streams a:0 \
    -show_entries stream=codec_name,sample_rate,channels -of default=nw=1 "$OUT" 2>/dev/null || true)
A_CODEC=""; A_RATE=""; A_CH=""
while IFS='=' read -r _k _v; do
    case "$_k" in
        codec_name)  A_CODEC=$_v ;;
        sample_rate) A_RATE=$_v ;;
        channels)    A_CH=$_v ;;
    esac
done <<<"$A_META"
unset _k _v OUT_META A_META
case "$FMT_BR" in
    ''|N/A) BR_TXT="码率?" ;;
    *)      BR_TXT=$(awk -v b="$FMT_BR" 'BEGIN{printf "%.1fMbps", b/1000000}') ;;
esac
log "成片  : ${V_W:-?}x${V_H:-?} @ ${V_RATE:-?}  ${V_CODEC:-?}/${V_PIX:-?}  ${V_FRAMES:-?} 帧  ${FMT_DUR:-?}s  ${BR_TXT}  $(fmt_size_gb "${FMT_SIZE:-0}")"
if [[ -n "$A_CODEC" ]]; then
    log "音轨  : ${A_CODEC} ${A_CH:-?}ch ${A_RATE:-?}Hz（原样 -c copy，未重编码）"
else
    log "音轨  : 无"
fi

# 参考帧数按【片段时长】算（无截取时段时 TOTAL 就是源整片时长，与旧版等价）。
# 用容器里的 nb_frames（即时可得），不要 -count_frames：那会把整片解码一遍。
SRC_FRAMES=$("$FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=nb_frames -of csv=p=0 "$IN" 2>/dev/null || true)
SRC_FRAMES=${SRC_FRAMES:-0}
SEG_FRAMES=$(awk -v t="$TOTAL" -v r="$SRC_RATE" 'BEGIN{
    split(r, a, "/"); f = a[1] / (a[2] == "" ? 1 : a[2]); printf "%d", t*f + 0.5 }')
if [[ "$SEGMENTED" == true ]]; then
    log "参考值: 片段 ${TOTAL}s ≈ ${SEG_FRAMES} 源帧 x2 = $(( SEG_FRAMES * 2 )) 帧；源整片 ${SRC_FRAMES} 帧"
elif (( SRC_FRAMES > 0 )); then
    log "参考值: 源 ${SRC_FRAMES} 帧 x2 = $(( SRC_FRAMES * 2 )) 帧；时长应与源一致（约 ${TOTAL}s）"
fi

# 总耗时放最后一行：整条命令从启动到成片落盘的全部时间。
log "总耗时: $(fmt_secs "$SECONDS")"
