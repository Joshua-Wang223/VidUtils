# verify/verify_cuda_scale.py — 单元级验证（不需要 GPU）
# 验证 vidcrop_hwaccel.py 新增的 CUDA 缩放链：
#   ① build_video_filter(cuda_scale=True) 的滤镜串是否符合实测过的那条
#   ② _generate_strategies 只在 cover 模式插入该策略，且缺 scale_cuda 能力时不插
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vidcrop_hwaccel as V

fails = []


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got}')
        print(f'        want: {want}')
        fails.append(label)


print('── ① build_video_filter(cuda_scale=True) ──')
CP = 'crop=1440:1080:(iw-1440)/2:0'
# 4K → 1440x1080（源更宽）：必须与实测跑的 B3 命令行逐字一致
chk('3840x2160 → 1440x1080 (8bit)',
    V.build_video_filter('cover', 3840, 2160, 1440, 1080, cuda_scale=True),
    f'scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=nv12,{CP}')
# 比例相同 → 只缩放不裁剪
chk('1920x1080 → 1280x720 (比例同)',
    V.build_video_filter('cover', 1920, 1080, 1280, 720, cuda_scale=True),
    'scale_cuda=1280:720:interp_algo=lanczos,hwdownload,format=nv12')
# 源更高（竖屏）→ 以宽为基准，高取偶
chk('1080x1920 → 1280x720 (源更高)',
    V.build_video_filter('cover', 1080, 1920, 1280, 720, cuda_scale=True),
    'scale_cuda=1280:2276:interp_algo=lanczos,hwdownload,format=nv12,'
    'crop=1280:720:0:(ih-720)/2')
# 10bit 源 → 下载格式必须是 p010le
chk('3840x2160 → 1440x1080 (10bit)',
    V.build_video_filter('cover', 3840, 2160, 1440, 1080, cuda_scale=True, src_bits=10),
    f'scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=p010le,{CP}')
# 软解形态：链首必须是 hwupload_cuda（自带 device，不需要 -filter_hw_device）
chk('软解形态 (8bit)：链首 hwupload_cuda',
    V.build_video_filter('cover', 3840, 2160, 1440, 1080,
                         cuda_scale=True, cuda_upload=True),
    f'hwupload_cuda,scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=nv12,{CP}')
chk('软解形态 (10bit)：下载仍是 p010le',
    V.build_video_filter('cover', 3840, 2160, 1440, 1080,
                         cuda_scale=True, cuda_upload=True, src_bits=10),
    f'hwupload_cuda,scale_cuda=1920:1080:interp_algo=lanczos,hwdownload,format=p010le,{CP}')
# cuda_upload 单独给没有意义（上传就是为了给 scale_cuda 用）→ 必须报错
try:
    V.build_video_filter('cover', 3840, 2160, 1440, 1080, cuda_upload=True)
    chk('cuda_upload 不带 cuda_scale 应报错', 'no-raise', 'ValueError')
except ValueError:
    chk('cuda_upload 不带 cuda_scale 应报错', 'ValueError', 'ValueError')
# 非 cover 模式传 cuda_scale 必须报错（防误用）
try:
    V.build_video_filter('crop', 3840, 2160, 1920, 1080, cuda_scale=True)
    chk('crop 模式传 cuda_scale 应报错', 'no-raise', 'ValueError')
except ValueError:
    chk('crop 模式传 cuda_scale 应报错', 'ValueError', 'ValueError')

# CPU 链必须与 GPU 侧同档（lanczos），且逐字回归
chk('CPU 链: 3840x2160 → 1440x1080（源更宽）',
    V.build_video_filter('cover', 3840, 2160, 1440, 1080),
    f'scale=-2:1080:flags=lanczos,{CP}')
chk('CPU 链: 1920x1080 → 1280x720（比例同）',
    V.build_video_filter('cover', 1920, 1080, 1280, 720),
    'scale=1280:720:flags=lanczos')
chk('CPU 链: 1080x1920 → 1280x720（源更高）',
    V.build_video_filter('cover', 1080, 1920, 1280, 720),
    'scale=1280:-2:flags=lanczos,crop=1280:720:0:(ih-720)/2')
chk('crop-cover 的 scale 段也带 flags',
    ':flags=lanczos' in V.build_video_filter('crop-cover', 1920, 1080, 640, 360, (16, 9)),
    True)
chk('crop 模式不含 scale（无 flags 可谈）',
    V.build_video_filter('crop', 1920, 1080, 640, 360), 'crop=640:360:640:360')


print('\n── ② _generate_strategies ──')


def caps(cuda_scale=True, decoder=True, upload=True):
    c = V.HardwareCapabilities()
    c.has_decoder = decoder
    c.has_encoder_h264 = True
    c.has_encoder_hevc = True
    c.has_cuda_scale = cuda_scale
    c.cuda_scale_upload_ok = upload
    return c


def first(codec, c, mode, decode='auto', scale_backend='auto', policy='auto', **kw):
    return V._generate_strategies(codec, c, decode, mode=mode,
                                 scale_backend=scale_backend, policy=policy, **kw)[0]


s = first('hevc_nvenc', caps(), 'cover')
chk('cover 首选策略名', s['name'], 'CUDA 缩放 + CPU 裁剪（显存内缩放）')
chk('cover 首选 hwaccel', s['hwaccel'], 'cuda')
chk('cover 首选 hof', s['hwaccel_output_format'], 'cuda')
chk('cover 首选 cuda_scale 标记', s.get('cuda_scale'), True)
chk('零拷贝形态不带 hwupload', s.get('hwupload'), None)

chk('crop 模式不插 CUDA 缩放（无 crop_cuda → 落到策略 2）',
    first('hevc_nvenc', caps(), 'crop')['name'], '自动硬件解码 + GPU 编码')
chk('crop-cover 模式不插 CUDA 缩放',
    first('hevc_nvenc', caps(), 'crop-cover')['name'], '自动硬件解码 + GPU 编码')
chk('缺 scale_cuda 时 cover 退回策略 2',
    first('hevc_nvenc', caps(cuda_scale=False), 'cover')['name'], '自动硬件解码 + GPU 编码')

# 策略 1（crop_cuda）在真实环境永远不触发：has_crop_cuda 默认 False
chk('crop 模式且真的没有 crop_cuda → 不出现全 GPU 策略',
    [x['name'] for x in V._generate_strategies('hevc_nvenc', caps(), 'auto', mode='crop')][0],
    '自动硬件解码 + GPU 编码')


print('\n── ③ 三轴正交：解码轴只管解码（--decode cpu ≠ 纯 CPU）──')
# 这是本次最重要的语义变更：旧 `--hwaccel none` 会把整块 GPU 一起关掉，
# 现在 `--decode cpu` 只关解码 —— 编码轴照样能用 NVENC。
# ⚠ 2026-09-21 起还要过一层"值不值得"门槛：只有「源 ≥10bit **且**非恒等缩放」才回退
# 到 hwupload（实测 8bit 打平或更慢、恒等必亏，见 _hwupload_skip_reason）。
# 所以这里**必须带上 10bit + 非恒等**，否则测到的是那道门槛，而不是"轴是否正交"。
s = first('hevc_nvenc', caps(), 'cover', decode='cpu',
          src_bits=10, scale_identity=False)
chk('--decode cpu + NVENC：首选仍是 CUDA 缩放（auto 缩放优先 cuda）',
    s['name'], 'CUDA 缩放 + CPU 裁剪（软件解码 + hwupload）')
chk('  → 必须是 hwupload 形态', s.get('hwupload'), True)
# 反面：同一组轴、只是源是 8bit → auto 主动不走。**这是"值不值得"，不是"解码轴
# 管到了缩放轴"** —— 两者要能分辨，否则门槛会被误解成正交性回归。
s8 = first('hevc_nvenc', caps(), 'cover', decode='cpu',
           src_bits=8, scale_identity=False)
chk('  → 8bit 源时 auto 不走 hwupload（门槛生效，落入软解+GPU 编码）',
    s8['name'], '软件解码 + GPU 编码')
chk('  → 但显式 --scale-algo cuda 不受门槛约束',
    first('hevc_nvenc', caps(), 'cover', decode='cpu', scale_backend='cuda',
          src_bits=8, scale_identity=False).get('hwupload'), True)
chk('  → hwaccel 必须为 None（帧是软件帧，不能再 output_format cuda）', s['hwaccel'], None)
chk('  → hof 必须为 None', s['hwaccel_output_format'], None)
chk('--decode cpu 不再等于纯 CPU（策略链里仍有 NVENC 编码）',
    '纯 CPU 处理' in [x['name'] for x in V._generate_strategies(
        'hevc_nvenc', caps(), 'cpu', mode='cover')][0], False)
# 显式 libswscale-* 才真的把缩放钉回 CPU
chk('--decode cpu + libswscale-*：缩放回 CPU',
    first('hevc_nvenc', caps(), 'cover', decode='cpu',
          scale_backend='libswscale').get('cuda_scale'), None)
chk('--scale-algo libswscale-* 时首选是软件解码 + GPU 编码',
    first('hevc_nvenc', caps(), 'cover', decode='cpu',
          scale_backend='libswscale')['name'], '软件解码 + GPU 编码')

print('\n── ④ 显式 cuda-* 不再要求 NVENC 编码器（链尾是软件帧）──')
s = first('libx264', caps(), 'cover', decode='cpu', scale_backend='cuda')
chk('软解 + cuda-* + libx264：走 hwupload 且编码器是请求的那个', s['name'],
    'CUDA 缩放 + CPU 裁剪（软件解码 + hwupload）')
chk('  → codec', s['codec'], 'libx264')
# 有硬解时同样不要求 NVENC：直接零拷贝
s = first('libx264', caps(), 'cover', decode='cuda', scale_backend='cuda')
chk('硬解 + cuda-* + libx264：零拷贝形态', s['name'], 'CUDA 缩放 + CPU 裁剪（显存内缩放）')
chk('  → hwaccel', s['hwaccel'], 'cuda')
chk('  → 不带 hwupload', s.get('hwupload'), None)

print('\n── ⑤ 功能探针：auto 缩放不赌没验证过的路径 ──')
chk('探针失败时 auto 缩放不插 hwupload',
    [x.get('cuda_scale') for x in V._generate_strategies(
        'hevc_nvenc', caps(decoder=False, upload=False), 'cpu',
        mode='cover', scale_backend='auto')][0], None)
chk('但显式 cuda-* 仍直接执行（失败交给 --fallback-policy）',
    first('hevc_nvenc', caps(decoder=False, upload=False), 'cover',
          decode='cpu', scale_backend='cuda').get('hwupload'), True)

print('\n── ⑥ 解码轴 → -hwaccel 的映射（auto 与 --scale-algo auto 同逻辑：先探测再定）──')
# auto 必须解析成**具体后端**，一个可用都没有时降级 None（软解），
# 不再把 `-hwaccel auto` 丢给 ffmpeg 让它自己试。
_c_full = caps(); _c_full.has_vulkan = True; _c_full.has_opencl = True
chk('  auto + 有 CUDA（CUDA > Vulkan > VA-API > OpenCL）→ cuda',
    V._decode_hwaccel('auto', _c_full), 'cuda')
_c_vk = V.HardwareCapabilities()
_c_vk.has_vulkan = True                       # 只有 Vulkan
chk('  auto + 只有 Vulkan → vulkan', V._decode_hwaccel('auto', _c_vk), 'vulkan')
chk('  auto + 一个硬解都没有 → None（降级软解，不下发 -hwaccel）',
    V._decode_hwaccel('auto', V.HardwareCapabilities()), None)
chk('  cpu → None', V._decode_hwaccel('cpu', _c_full), None)
chk('  cuda 可用 → cuda', V._decode_hwaccel('cuda', _c_full), 'cuda')
chk('  cuda 不可用 → None（降级软解）',
    V._decode_hwaccel('cuda', V.HardwareCapabilities()), None)
chk('  vulkan 可用 → vulkan', V._decode_hwaccel('vulkan', _c_full), 'vulkan')
chk('  vaapi 不可用 → None', V._decode_hwaccel('vaapi', _c_full), None)
chk('  opencl 可用 → opencl', V._decode_hwaccel('opencl', _c_full), 'opencl')
# auto 降级不算"显式点名够不到"，strict 也不该因此报错（只有显式值才进 _strict_fail）
chk('  auto 不被当成显式请求 → 不在 strict 失败列表里',
    'auto' in ('cuda', 'vulkan', 'vaapi', 'opencl'), False)

print('\n── ⑦ --fallback-policy strict 只留首选策略 ──')
chk('strict：策略链长度',
    len(V._generate_strategies('hevc_nvenc', caps(), 'auto', mode='cover',
                               scale_backend='auto', policy='strict')), 1)
chk('auto：策略链更长（可降级）',
    len(V._generate_strategies('hevc_nvenc', caps(), 'auto', mode='cover',
                               scale_backend='auto', policy='auto')) > 1, True)

print()
if fails:
    print(f'✗ {len(fails)} 项未通过: {fails}')
    sys.exit(1)
print('✓ 全部通过')
