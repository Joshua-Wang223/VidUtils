# verify/verify_scale_algo.py — --scale-algo 的单元验证（本机无 GPU，用假 caps）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vidcrop_hwaccel as H
import vidcrop_cpu_v2 as C

fails = []


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got}\n        want: {want}')
        fails.append(label)


def caps(cuda_scale=True, decoder=True, nvenc=True):
    c = H.HardwareCapabilities()
    c.has_decoder = decoder
    c.has_encoder_h264 = nvenc
    c.has_encoder_hevc = nvenc
    c.has_cuda_scale = cuda_scale
    return c


def first_has_cuda_scale(spec, mode='cover', c=None):
    c = c or caps()
    backend, sw, cuda = H.parse_scale_algo(spec)
    st = H._generate_strategies('hevc_nvenc', c, 'auto', mode=mode, scale_backend=backend)
    return any(s.get('cuda_scale') for s in st)


print('── 策略链：--scale-algo 是否影响「插不插 CUDA 缩放策略」──')
chk('不传（默认）→ 插（= 既有行为）', first_has_cuda_scale(None), True)
chk('裸 lanczos    → 插', first_has_cuda_scale('lanczos'), True)
chk('cuda-lanczos  → 插（显式强制）', first_has_cuda_scale('cuda-lanczos'), True)
chk('libswscale-lanczos → 不插（强制 CPU 链）', first_has_cuda_scale('libswscale-lanczos'), False)
chk('libswscale-spline  → 不插', first_has_cuda_scale('libswscale-spline'), False)
chk('缺 scale_cuda 时裸 lanczos → 不插', first_has_cuda_scale('lanczos', c=caps(cuda_scale=False)), False)
chk('crop 模式永远不插', first_has_cuda_scale(None, mode='crop'), False)

print('\n── 滤镜串：algo 是否真的落到链上 ──')
chk('CPU cover + bicubic',
    H.build_video_filter('cover', 3840, 2160, 1440, 1080, sw_algo='bicubic'),
    'scale=-2:1080:flags=bicubic,crop=1440:1080:(iw-1440)/2:0')
chk('GPU cover + cuda bicubic',
    H.build_video_filter('cover', 3840, 2160, 1440, 1080, cuda_scale=True,
                         cuda_algo='bicubic'),
    'scale_cuda=1920:1080:interp_algo=bicubic,hwdownload,format=nv12,'
    'crop=1440:1080:(iw-1440)/2:0')
chk('GPU cover + cuda nearest',
    H.build_video_filter('cover', 3840, 2160, 1440, 1080, cuda_scale=True,
                         cuda_algo='nearest'),
    'scale_cuda=1920:1080:interp_algo=nearest,hwdownload,format=nv12,'
    'crop=1440:1080:(iw-1440)/2:0')
chk('CPU crop-cover + spline',
    H.build_video_filter('crop-cover', 1920, 1080, 640, 360, (16, 9), sw_algo='spline'),
    'crop=1920:1080:0:0,scale=640:360:flags=spline')
chk('v2 cover + area',
    C.build_video_filter('cover', 3840, 2160, 1440, 1080, None, 'area'),
    'scale=-2:1080:flags=area,crop=1440:1080:(iw-1440)/2:0')
chk('v2 crop-cover + spline',
    C.build_video_filter('crop-cover', 1920, 1080, 640, 360, (16, 9), 'spline'),
    'crop=1920:1080:0:0,scale=640:360:flags=spline')

print('\n── 两脚本默认值必须一致 ──')
chk('默认 sw_algo 一致', H._SW_SCALE_FLAGS, C._SW_SCALE_FLAGS)
chk('默认 cuda_algo 一致', H._CUDA_SCALE_ALGO, C._CUDA_SCALE_ALGO)
chk('两张取值表一致', tuple(H._SW_SCALE_ALGOS) == tuple(C._SW_SCALE_ALGOS)
    and tuple(H._CUDA_SCALE_ALGOS) == tuple(C._CUDA_SCALE_ALGOS), True)
chk('别名表一致', (H._SW_ALGO_ALIAS, H._CUDA_ALGO_ALIAS)
    == (C._SW_ALGO_ALIAS, C._CUDA_ALGO_ALIAS), True)

print('\n── 未知名字的报错首行必须一致（对齐约定：以 hwaccel 为权威）──')
for spec in ('nope', 'cuda-', 'x-y', 'libswscale-'):
    def first_line(f):
        try:
            f(spec)
            return '(没报错)'
        except ValueError as e:
            return str(e).splitlines()[0]
    a, b = first_line(H.parse_scale_algo), first_line(C.parse_scale_algo)
    chk(f'{spec} 首行一致', a, b)

print()
if fails:
    print(f'✗ {len(fails)} 项未通过: {fails}')
    sys.exit(1)
print('✓ 全部通过')
