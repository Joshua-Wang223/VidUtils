# VidUtils · 工程记忆索引

这里放**工具背后的事实与踩坑**，不是 API 文档（用法看 `../README.md`）。

每条一行，指向同目录下的文件。文件格式沿用本机 codebuddy 自动记忆的约定：
`---` frontmatter 里带 `name` / `description` / `type`（project / user / feedback / reference），
正文对 project 与 feedback 类用「事实 → **Why:** → **How to apply:**」的结构，方便日后判断
这条记忆是不是还成立。

---

- [长时 ffmpeg 任务必须 setsid 分离 + 别直接写 MP4](project_long_ffmpeg_jobs.md)
  — 代码宿主崩溃会带走同进程组的 ffmpeg；MP4 缺 moov 整份作废（已发生过一次，34 分钟算力白跑）；
  并发实例互删临时文件；以及 `interp_2x_safe.sh` 里那套防呆（锁 / 两层复用校验 / PID 临时名）
- [ffmpeg 挂起的两个根因](project_ffmpeg_stdin_hang.md)
  — SIGTTIN（状态 T，`-nostdin` 能修）vs 输出管道反压（状态 S 且 CPU 冻结，`-nostdin` 没用）
- [T4 能力边界 + 测性能前先查并发流水线](project_t4_gpu_capabilities.md)
  — 别的流水线会抢 CPU/GPU 导致基准不可信；T4 无 AV1 编码器；零拷贝管线里 `-pix_fmt` 无效
- [FFmpeg 7.1 已合并 nvinterpolate 与 libvmaf](project_nvinterpolate_build.md)
  — 单一 ffmpeg、无需环境文件；`nvinterpolate` 必须放滤镜链末尾否则段错误；移植补丁位置

---

## 待办：还没迁进来的

`/workspace` 侧的 codebuddy 自动记忆里还有以下条目，与 VidUtils 相关但**尚未**迁入
（要么属于别的项目，要么等你确认再动）：

- `project_ffmpeg_gpu_script_gotchas.md` — `install_ffmpeg_gpu_scale.sh` 等安装脚本的坑。
  属于"装 FFmpeg"而不是"用 FFmpeg"，暂留原处。
- `feedback_gpu_testing.md` — 用户的验证顺序偏好（先验证 CPU/软编路径）。
  属于协作偏好而非项目事实，暂留原处。
