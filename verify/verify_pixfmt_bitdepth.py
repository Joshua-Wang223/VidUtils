# verify/verify_pixfmt_bitdepth.py — 验证 --pix-fmt / --bit-depth 的「能落地者赢」
# 背景：两者语义重叠（格式名里已含位深）但不在同一层级——
#   --pix-fmt 是实现级（格式名=位深+色度+排布，但合法性**与链相关**）
#   --bit-depth 是意图级（只有位深，但合法性**与链无关**）
# 恒定让谁赢两端都有反例，故实现为：先让 --pix-fmt 落地，落不了地由 --bit-depth
# 接管，并明说让位代价。本脚本把这些判据钉成断言。
import contextlib
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vidcrop_hwaccel as V

# fixture 是 **lavfi 生成的**，不入库：二进制素材进了 git 会永久占仓库空间，
# 而它随时能用一条命令造出来（跑完留在 temp/ 里，下次直接复用）。
SRC = Path(__file__).resolve().parent.parent / 'temp' / 'fixture_1080p.mp4'
if not SRC.exists():
    SRC.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25:duration=1',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    str(SRC)], check=True)
fails = []


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got}')
        print(f'        want: {want}')
        fails.append(label)


def chk_in(label, needle, hay):
    ok = needle in (hay or '')
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        子串 {needle!r} 不在 {hay!r}')
        fails.append(label)


def caps(cuda=True):
    c = V.HardwareCapabilities()
    c.has_decoder = True
    c.has_encoder_h264 = True
    c.has_encoder_hevc = True
    c.has_cuda_scale = cuda
    c.cuda_scale_upload_ok = cuda
    return c


def run(pix_fmt='auto', bit_depth=None, policy='auto', codec='hevc_nvenc', cuda=True):
    """跑一次 build_ffmpeg_cmd，返回 (vf, profile, -pix_fmt, 告警文本, 异常文本)。"""
    s = V._generate_strategies(codec, caps(cuda), 'auto', mode='cover',
                               scale_backend='auto', policy=policy)[0]
    vf = V.build_video_filter(
        'cover', 1920, 1080, 640, 360,
        use_cuda=s['use_hw_filter'], cuda_scale=bool(s.get('cuda_scale')),
        cuda_upload=bool(s.get('hwupload')), src_bits=8,
        sw_algo='lanczos', cuda_algo='lanczos')
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            cmd = V.build_ffmpeg_cmd(
                input_file=SRC, output_file=Path('temp/o.mp4'), vf_filter=vf,
                codec=s['codec'], crf=None, cq=23, preset='p4', overwrite=True,
                hwaccel=s.get('hwaccel'),
                hwaccel_output_format=s.get('hwaccel_output_format'),
                pix_fmt=pix_fmt, bit_depth=bit_depth, policy=policy)
    except ValueError as exc:
        return None, None, None, err.getvalue(), str(exc)

    def g(flag):
        v = [cmd[i + 1] for i, a in enumerate(cmd) if a == flag]
        return v[0] if v else None
    vf = g('-vf') or g('-filter:v:0')
    # 本脚本只关心"格式落到哪"；链尾的 setparams=（色彩属性标到帧上，2026-09-22 起
    # GPU 编码器也会有）由 verify/verify_color_tagging.py 单独验证 → 这里剥掉，
    # 免得每条断言都拖一段与 pix_fmt 无关的色彩后缀。
    if vf and ',setparams=' in vf:
        vf = vf.split(',setparams=', 1)[0]
    return vf, g('-profile:v'), g('-pix_fmt'), err.getvalue(), None


CUDA_10 = 'scale_cuda=640:360:interp_algo=lanczos:format=p010le,hwdownload,format=p010le'

print('── ① 让位：--pix-fmt 落不了地，由 --bit-depth 接管（原缺陷：双双失效）──')
vf, prof, pf, warn, exc = run('yuv420p10le', 10)
chk('①a 滤镜串落到 p010le', vf, CUDA_10)
chk('①b -profile:v main10', prof, 'main10')
chk('①c 零拷贝链上不下发 -pix_fmt', pf, None)
chk('①d 无异常', exc, None)
chk_in('①e 告警说明已让位', '已改由 --bit-depth 10 接管', warn)
chk_in('①f 告警说明无损失', '无信息损失', warn)

print('── ② strict：等价让位不算降级（不报错）──')
vf, prof, pf, warn, exc = run('yuv420p10le', 10, policy='strict')
chk('②a 滤镜串仍落到 p010le', vf, CUDA_10)
chk('②b 不抛错', exc, None)

print('── ③ 让位有损失时必须说清（位深 + 色度）──')
vf, prof, pf, warn, exc = run('yuv422p', 10)
chk('③a 仍让位到 p010le', vf, CUDA_10)
chk_in('③b 点出位深变化', '位深 8→10bit', warn)
chk_in('③c 点出色度变化', '色度 4:2:2→4:2:0', warn)

print('── ④ strict：有损失的让位改为报错 ──')
vf, prof, pf, warn, exc = run('yuv422p', 10, policy='strict')
chk('④a 抛 ValueError', exc is not None, True)
chk_in('④b 报错说明 strict 不降级', 'strict 不降级', exc)

print('── ⑤ --pix-fmt 能落地时不让位（yuv444p 必须保住 4:4:4）──')
vf, prof, pf, warn, exc = run('yuv444p', 10)
chk('⑤a 滤镜串用 format=yuv444p',
    vf, 'scale_cuda=640:360:interp_algo=lanczos:format=yuv444p,'
        'hwdownload,format=yuv444p')
chk('⑤b -profile:v high444p', prof, 'high444p')
chk('⑤c 没有让位告警', '接管' in warn, False)

print('── ⑥ 没给 --bit-depth 时保持现状（四段式告警，不静默）──')
vf, prof, pf, warn, exc = run('yuv420p10le')
chk('⑥a 未落到 p010le', vf, 'scale_cuda=640:360:interp_algo=lanczos,'
                            'hwdownload,format=nv12')
chk_in('⑥b 告警含原因', '原因：', warn)
chk_in('⑥c 告警含怎么办', '怎么办：', warn)
chk_in('⑥d 告警含后果', '当前后果：', warn)

print('── ⑦ 只给 --bit-depth（用户实测成功的写法，必须不变）──')
vf, prof, pf, warn, exc = run(bit_depth=10)
chk('⑦a 滤镜串落到 p010le', vf, CUDA_10)
chk('⑦b -profile:v main10', prof, 'main10')
chk('⑦c 无告警', warn.strip(), '')

print('── ⑧ 显式写对名字（--pix-fmt p010le）也不变 ──')
vf, prof, pf, warn, exc = run('p010le')
chk('⑧a 滤镜串落到 p010le', vf, CUDA_10)
chk('⑧b -profile:v main10', prof, 'main10')
chk('⑧c 无告警', warn.strip(), '')

print('── ⑨ 回归：两个参数都不给 / none / 软件帧链 ──')
vf, prof, pf, warn, exc = run()
chk('⑨a 默认零拷贝链不加 format=',
    vf, 'scale_cuda=640:360:interp_algo=lanczos,hwdownload,format=nv12')
chk('⑨b 默认不下发 -pix_fmt', pf, None)
chk('⑨c 默认无 profile', prof, None)
chk('⑨d 默认无告警', warn.strip(), '')

vf, prof, pf, warn, exc = run('none')
chk('⑨e --pix-fmt none 不下发 -pix_fmt', pf, None)
chk('⑨f --pix-fmt none 无告警', warn.strip(), '')

vf, prof, pf, warn, exc = run('yuv420p10le', cuda=False)
chk('⑨g 软件帧链照常下发 -pix_fmt', pf, 'yuv420p10le')
chk('⑨h 软件帧链无告警', warn.strip(), '')

print('── ⑩ _pixfmt_shape 单元 ──')
for name, want in [('yuv420p10le', (10, '420')), ('yuv420p12le', (12, '420')),
                   ('yuv444p', (8, '444')), ('yuv422p10le', (10, '422')),
                   ('p010le', (10, '420')), ('p012le', (12, '420')),
                   ('nv12', (8, '420')), ('yuv420p', (8, '420')),
                   ('yuvj420p', (8, '420'))]:
    chk(f'⑩ _pixfmt_shape({name})', V._pixfmt_shape(name), want)

print()
if fails:
    print(f'✘ {len(fails)} 项失败：{fails}')
    sys.exit(1)
print('✓ 全部通过')
