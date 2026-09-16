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
  并发度按「剩余待编片数」再收敛、撞锁报错打印持有者 pid/启动时间/命令行、新增 test_interp_2x_orphan.sh；
  另有一条实测教训：**别"原位"改正在被执行的脚本**（bash 会按字节偏移重读、把跑完的循环再跑一遍）
- [bash 并行调度的四个坑](project_bash_parallel_pitfalls.md)
  — `wait -n` 会提前返回不能当完成信号；`while read < <(tail)` 能永久卡死在 pipe_read（0% CPU）；
  `exec ffmpeg` 的子 shell 被杀会留下孤儿 ffmpeg；找"另一个实例"必须读 `/proc/<pid>/cmdline` 精确匹配
- [ffmpeg 挂起的两个根因](project_ffmpeg_stdin_hang.md)
  — SIGTTIN（状态 T，`-nostdin` 能修）vs 输出管道反压（状态 S 且 CPU 冻结，`-nostdin` 没用）
- [T4 能力边界 + 测性能前先查并发流水线](project_t4_gpu_capabilities.md)
  — 别的流水线会抢 CPU/GPU 导致基准不可信；T4 无 AV1 编码器，且本机 ffmpeg 也无 AV1 软编
  （libsvtav1/libaom-av1/librav1e 都没有 → 本机 AV1 完全编不出来）；零拷贝管线里 `-pix_fmt` 无效
- [FFmpeg 7.1 已合并 nvinterpolate 与 libvmaf](project_nvinterpolate_build.md)
  — 单一 ffmpeg、无需环境文件；`nvinterpolate` 必须放滤镜链末尾否则段错误；移植补丁位置
- [两个裁剪脚本的行为一致约定](project_preset_equivalence.md)
  — 两裁剪脚本的 NVENC↔x264 表必须一致（曾错位一档：p4→medium/p5→slow，会让同一条 `--preset p5` 落不同档）；
  降级到 CPU 编码器时基准档取"请求的编码器"的默认值再换算；概览块只展示最终命令里真正会出现的参数
  （编码器名取策略链第一条、无 `-preset` 的编码器不展示 preset 字段）；
  `--codec auto` 必须解析成具体编码器，透传会得到 `-c:v auto` 使 ffmpeg 报 Unknown encoder（曾发生在 cpu_v2）

---

## 待办：还没迁进来的

`/workspace` 侧的 codebuddy 自动记忆里还有以下条目，与 VidUtils 相关但**尚未**迁入
（要么属于别的项目，要么等你确认再动）：

- `project_ffmpeg_gpu_script_gotchas.md` — `install_ffmpeg_gpu_scale.sh` 等安装脚本的坑。
  属于"装 FFmpeg"而不是"用 FFmpeg"，暂留原处。
- `feedback_gpu_testing.md` — 用户的验证顺序偏好（先验证 CPU/软编路径）。
  属于协作偏好而非项目事实，暂留原处。
