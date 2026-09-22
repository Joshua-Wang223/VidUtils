# verify/verify_color_tagging.py — 验证色彩属性是**标到帧上**（setparams）而不只写输出端参数
#
# 背景（2026-09-22，见 memory/project_green_chroma_defect.md）：
#   输出端 `-colorspace smpte170m` 与解码帧的 `csp:unknown` 不一致时，ffmpeg 会在
#   滤镜链尾与编码器之间**自动插入一个 CPU scale**（debug 日志里的 auto_scale_0）去凑
#   codec context；这个转换在「硬解 + NVENC」链上会把 U/V 清零 → 产物全绿。
#   给帧标好色彩属性后两者一致，自动转换不再出现。
#
# 本脚本把这些判据钉成断言（纯命令构造，不需要 GPU）：
#   ① 所有编码器（含 GPU）在 CPU 链尾都要有 setparams，取值与输出端一致；
#   ② 输出端色彩四参仍然保留（写容器 colr box）；
#   ③ 链尾还在显存里时不追加（CPU 滤镜接不住）；
#   ④ copy 不追加（滤镜与流复制互斥）；
#   ⑤ 软编码器行为与此前一致（回归锁定）。
#
# 用法: python verify/verify_color_tagging.py
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vidcrop_hwaccel as V

# fixture 是 lavfi 生成的、不入库（同 verify_pixfmt_bitdepth.py 的约定）
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
        print(f'        got : {got!r}')
        print(f'        want: {want!r}')
        fails.append(label)


def chk_true(label, got):
    chk(label, bool(got), True)


def build(codec, vf='crop=768:432:0:72', hwaccel='cuda', hof=None):
    """按给定链构造真命令（build_ffmpeg_cmd 返回 List[str]，取 token 不用管引号）。"""
    return V.build_ffmpeg_cmd(
        input_file=SRC, output_file=SRC.with_name('color_tag_out.mp4'),
        vf_filter=vf, codec=codec, crf=None, cq=23, preset='p5', overwrite=True,
        hwaccel=hwaccel, hwaccel_output_format=hof, ffmpeg_bin='ffmpeg',
        audio_codec='copy', audio_bitrate='128k', extra_args=None,
        hw_download_fmt=None, color_range=None, pix_fmt='auto',
        bit_depth=None, hdr='auto', policy='auto')


def vf_of(cmd):
    i = cmd.index('-filter:v:0') if '-filter:v:0' in cmd else cmd.index('-vf')
    return cmd[i + 1]


def setparams_of(cmd):
    vf = vf_of(cmd)
    return vf.split(',setparams=', 1)[1] if ',setparams=' in vf else None


print('── ① GPU 编码器 + CPU 链尾：必须有 setparams（本缺陷的修复点）──')
for codec in ('hevc_nvenc', 'h264_nvenc', 'av1_nvenc'):
    cmd = build(codec)
    sp = setparams_of(cmd)
    chk_true(f'{codec}: 链尾追加了 setparams', sp is not None)
    chk(f'{codec}: setparams 取到源推断的 matrix（HD→bt709）',
        sp, 'colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv')
    # 输出端四参仍保留（写容器标签），与 setparams 互补
    chk_true(f'{codec}: 输出端 -colorspace 仍保留', '-colorspace' in cmd)

print('── ② 软编码器：行为与此前一致（回归锁定）──')
for codec in ('libx264', 'libx265'):
    cmd = build(codec)
    chk(f'{codec}: 链尾仍是 setparams',
        vf_of(cmd), 'crop=768:432:0:72,setparams=colorspace=bt709:'
                    'color_primaries=bt709:color_trc=bt709:range=tv')

print('── ③ 链尾还在显存里：不追加（setparams 是 CPU 滤镜，接不住 CUDA 帧）──')
for vf in ('scale_cuda=1920:1080:interp_algo=lanczos', 'crop_cuda=640:360'):
    cmd = build('hevc_nvenc', vf=vf, hof='cuda')
    chk(f'{vf.split("=", 1)[0]} 链尾: 无 setparams', vf_of(cmd), vf)
cmd = build('hevc_nvenc', vf='', hof='cuda')
chk_true('空链 + hof=cuda: 不追加 setparams',
         not any('setparams' in c for c in cmd))

print('── ④ hwdownload 收尾 = 软件帧：仍要追加（_HW_OUTPUT_FILTERS 不含 hwdownload）──')
chk_true('_HW_OUTPUT_FILTERS 不含 hwdownload', 'hwdownload' not in V._HW_OUTPUT_FILTERS)
chk_true('_HW_OUTPUT_FILTERS 含 scale_cuda', 'scale_cuda' in V._HW_OUTPUT_FILTERS)
cmd = build('hevc_nvenc', vf='crop=768:432:0:72,hwdownload,format=nv12', hof='cuda')
chk_true('hwdownload 收尾: 追加了 setparams', setparams_of(cmd) is not None)

print('── ⑤ copy：不追加（视频滤镜与流复制互斥）──')
# 注：空 vf 时函数仍会吐出 `-filter:v:0 ''`（既有行为，与本次改动无关，且真实模式
# 下 vf_filter 不会是空的）；这里只断言"没有 setparams"这一条契约。
cmd = build('copy', vf='', hwaccel=None)
chk_true('copy: 无 setparams', not any('setparams' in c for c in cmd))

print('── ⑥ trc 别名：滤镜端只认 bt470bg/gamma28 不认规范名 ──')
chk('gamma28 → bt470bg（滤镜别名）',
    V._setparams_from_color_args(['-color_trc', 'gamma28']),
    'setparams=color_trc=bt470bg')
chk_true('_TRC_OUTPUT_NAMES 把 bt470bg 换成 gamma28（输出端规范名）',
         V._TRC_OUTPUT_NAMES.get('bt470bg') == 'gamma28')

if fails:
    print(f'\n✘ {len(fails)} 项失败')
    sys.exit(1)
print('\n✔ 全部通过')
