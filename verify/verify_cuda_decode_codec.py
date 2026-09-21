# verify/verify_cuda_decode_codec.py — 验证「按源编解码器的 CUDA 硬解确认」
# 背景：has_decoder 是拿 **H.264 微流**探出来的**机器级**标志，而 NVDEC 的解码能力
# **分编解码器**（T4/Turing 能解 H.264/HEVC，解不了 AV1，那要 Ampere 起的第 5 代）。
# 拿前者推断后者会让 `--decode auto` 误选 cuda（每个该编码的文件白跑一次必败的链），
# `--fallback-policy strict` 下更是以 rc=2 退出，而软解其实完全可行。
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vidcrop_hwaccel as V

# fixture 是 lavfi 生成的、不入库（同 verify_pixfmt_bitdepth.py 的理由）。
FIXTURE = Path(__file__).resolve().parent.parent / 'temp' / 'fixture_1080p.mp4'
if not FIXTURE.exists():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25:duration=1',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    str(FIXTURE)], check=True)
fails = []


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got}')
        print(f'        want: {want}')
        fails.append(label)


def caps(decoder=True, decode_ok=None, **kw):
    c = V.HardwareCapabilities()
    c.has_decoder = decoder
    c.has_encoder_h264 = kw.get('h264', True)
    c.has_encoder_hevc = kw.get('hevc', True)
    c.has_cuda_scale = kw.get('cuda_scale', True)
    c.cuda_scale_upload_ok = kw.get('upload', True)
    c.has_vulkan = kw.get('vulkan', False)
    if decode_ok:
        c.cuda_decode_ok.update(decode_ok)
    return c


AV1_NO = {'av1': False}          # 模拟 T4：机器能硬解，但解不了 AV1

print('── ① cuda_decodes：机器级标志 × 编解码器级实测 ──')
chk('无硬解 → 任何 codec 都 False', caps(decoder=False).cuda_decodes('h264'), False)
chk('有硬解 + 未探测 → 乐观 True（保持既有行为）', caps().cuda_decodes('av1'), True)
chk('有硬解 + 空 codec → True（只反映机器能力）', caps().cuda_decodes(''), True)
chk('有硬解 + 实测该 codec 可解 → True', caps(decode_ok={'av1': True}).cuda_decodes('av1'), True)
chk('有硬解 + 实测该 codec 不可解 → False', caps(decode_ok=AV1_NO).cuda_decodes('av1'), False)
chk('codec 名大小写不敏感', caps(decode_ok=AV1_NO).cuda_decodes('AV1'), False)
chk('只影响被测过的 codec（hevc 仍 True）', caps(decode_ok=AV1_NO).cuda_decodes('hevc'), True)

print('── ② _select_best_hwaccel ──')
chk('默认（未探测）→ cuda', V._select_best_hwaccel(caps()), 'cuda')
chk('av1 不可解且无其它后端 → None', V._select_best_hwaccel(caps(decode_ok=AV1_NO), 'av1'), None)
chk('av1 不可解 → 让位给 vulkan',
    V._select_best_hwaccel(caps(decode_ok=AV1_NO, vulkan=True), 'av1'), 'vulkan')
chk('同一次运行里 h264 不受 av1 结论影响',
    V._select_best_hwaccel(caps(decode_ok=AV1_NO), 'h264'), 'cuda')

print('── ③ _decode_hwaccel ──')
chk('auto + av1 不可解 → None（软解）',
    V._decode_hwaccel('auto', caps(decode_ok=AV1_NO), 'av1'), None)
chk('auto + h264 → cuda',
    V._decode_hwaccel('auto', caps(decode_ok=AV1_NO), 'h264'), 'cuda')
chk('cpu → None', V._decode_hwaccel('cpu', caps(), 'av1'), None)
chk('显式 cuda + av1 不可解 → None（交给 fallback-policy）',
    V._decode_hwaccel('cuda', caps(decode_ok=AV1_NO), 'av1'), None)
chk('显式 cuda + 未探测过 → cuda', V._decode_hwaccel('cuda', caps(), 'av1'), 'cuda')

print('── ④ _generate_strategies：策略链真的躲开了必败的 cuda 链 ──')
COVER = dict(mode='cover', scale_backend='auto', policy='auto')
s_h264 = V._generate_strategies('hevc_nvenc', caps(), **COVER, src_codec='h264')
s_av1 = V._generate_strategies('hevc_nvenc', caps(decode_ok=AV1_NO), **COVER, src_codec='av1')
chk('h264 首选 = 零拷贝 CUDA 缩放',
    s_h264[0]['name'], 'CUDA 缩放 + CPU 裁剪（显存内缩放）')
chk('h264 首选带 -hwaccel cuda', s_h264[0].get('hwaccel'), 'cuda')
chk('av1 首选不再是零拷贝 CUDA 缩放',
    s_av1[0]['name'] != 'CUDA 缩放 + CPU 裁剪（显存内缩放）', True)
chk('av1 整条策略链里没有任何 cuda 硬解',
    [x.get('hwaccel') for x in s_av1 if x.get('hwaccel') == 'cuda'], [])
chk('av1 仍保留了可用策略（没有空链）', len(s_av1) > 0, True)
chk('显式 --scale-algo cuda + av1 不可解：零拷贝形态不出现（仍可走 hwupload）',
    [x.get('cuda_scale') for x in V._generate_strategies(
        'hevc_nvenc', caps(decode_ok=AV1_NO), mode='cover',
        scale_backend='cuda', policy='auto', src_codec='av1') if x.get('hwaccel') == 'cuda'],
    [])

print('── ⑤ 回归：不传 src_codec 时与 h264 链逐字相同 ──')
# 既有调用方（--list-strategies、老单测）都不传 src_codec，必须乐观处理、行为不变
KEY = ('name', 'hwaccel', 'hwaccel_output_format', 'use_hw_filter', 'cuda_scale', 'hwupload', 'codec')
s_none = V._generate_strategies('hevc_nvenc', caps(), **COVER)
s_h264b = V._generate_strategies('hevc_nvenc', caps(decode_ok=AV1_NO), **COVER, src_codec='h264')
chk('不传 src_codec ≡ 传 "未探测过的 codec"',
    [tuple(x.get(k) for k in KEY) for x in s_none],
    [tuple(x.get(k) for k in KEY) for x in s_h264b])

print('── ⑥ 探测函数本身（本机无 N 卡 → 应返回 False）──')
if FIXTURE.exists():
    chk('_probe_cuda_decode_codec(真实 mp4) 在本机 → False',
        V._probe_cuda_decode_codec('ffmpeg', FIXTURE), False)
else:
    print('  [SKIP] 缺 fixture，跳过')

print()
if fails:
    print(f'✘ {len(fails)} 项失败：{fails}')
    sys.exit(1)
print('✓ 全部通过')
