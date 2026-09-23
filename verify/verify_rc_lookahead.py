# verify/verify_rc_lookahead.py — 验证码率控制轴四个新参数
#   --rc-mode / --qp / --lookahead / --bitrate（两脚本同名同默认）
#
# 背景与设计（详见 README「码率控制」一节）：
#   · --rc-mode / --qp 是 **NVENC 专属**（-rc / -qp 只有 NVENC 有；libx264/libx265 没有
#     "码率控制模式"这个开关，它们用 -crf / -b:v / -qp 的组合表达）→ 非 NVENC 编码器
#     下"忽略并告知"（hwaccel 在 --fallback-policy strict 下报错）。
#   · --lookahead 按编码器分别下发；libx265 只能走 -x265-params，而实测
#     `-x265-params A -x265-params B` 是**后者整条覆盖前者** → 必须与 HDR 元数据
#     合并成同一条，否则会静默抹掉 HDR 静态元数据。
#   · --bitrate 与质量参数按 rc 模式区分：auto/vbr*/cbr* 下并存（受码率约束的恒定质量）、
#     constqp 下报错（该模式完全无视 -b:v）；VP9 的 -b:v 0 在用户给了码率时不再补。
#
# 判据分三组：① 默认路径逐字不变 ② 各参数落到命令上的形状 + 忽略/报错 ③ 两脚本一致性。
import contextlib
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import vidcrop_hwaccel as H
import vidcrop_cpu_v2 as C

# fixture 是 lavfi 生成的，不入库（与 verify_pixfmt_bitdepth.py 同因）
SRC = ROOT / 'temp' / 'fixture_1080p.mp4'
if not SRC.exists():
    SRC.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25:duration=1',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    str(SRC)], check=True)
fails = []

RC_FLAGS = ('-rc', '-qp', '-cq', '-crf', '-b:v', '-rc-lookahead', '-lag-in-frames',
            '-x265-params')


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


def tokens(cmd, flags=RC_FLAGS):
    """把命令里与本次判定相关的 flag+取值抓成字符串（顺序保留）。"""
    return ' '.join(f'{t} {cmd[i + 1]}' for i, t in enumerate(cmd[:-1]) if t in flags)


# ── 两脚本各跑一次 build_ffmpeg_cmd 的薄封装 ────────────────────────────────
def hw(codec, policy='auto', **kw):
    """hwaccel：warn 是内部 _warn（打 stderr），故用 contextlib 抓告警文本。"""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        cmd = H.build_ffmpeg_cmd(
            SRC, SRC.with_name('o.mp4'), 'crop=640:360:640:360', codec,
            kw.pop('crf', None), kw.pop('cq', None), 'medium', True,
            policy=policy, **kw)
    return cmd, buf.getvalue()


def cv(codec, **kw):
    """cpu_v2：warn 是回调，直接收集。"""
    warns = []
    cmd = C.build_ffmpeg_cmd(
        SRC, SRC.with_name('o.mp4'), 640, 360, 1920, 1080, 'crop', codec,
        kw.pop('crf', None), kw.pop('cq', None), 'medium', kw.pop('pix_fmt', None),
        0, True, 'copy', '128k', [], warns.append, **kw)
    return cmd, ''.join(warns)


def both(codec, **kw):
    return hw(codec, **kw), cv(codec, **kw)


print('── ① 默认（一个都不传）：命令里不得出现任何本轴选项 ──')
for name, (c, _), want in (('hwaccel/hevc_nvenc', hw('hevc_nvenc', cq=20), '-cq 20'),
                           ('cpu_v2/hevc_nvenc', cv('hevc_nvenc', cq=20), '-cq 20'),
                           ('hwaccel/libx265', hw('libx265', crf=20), '-crf 20'),
                           ('cpu_v2/libx265', cv('libx265', crf=20), '-crf 20')):
    chk(f'① {name} 默认只有既有质量参数', tokens(c), want)
chk('① hwaccel/libx265 默认无 -x265-params（HDR 探测过但不写）',
    '-x265-params' in hw('libx265', crf=20)[0], False)

print('── ② --rc-mode / --qp 的取值解析（含前缀与大小写）──')
cases = [(None, 'auto'), ('', 'auto'), ('auto', 'auto'), ('AUTO', 'auto'),
         ('vbr', 'vbr'), ('vbr_hq', 'vbr_hq'), ('cbr_ld_hq', 'cbr_ld_hq'),
         ('nvenc-vbr', 'vbr'), ('NVENC-VBR_HQ', 'vbr_hq'), ('nvenc-auto', 'auto')]
for spec, want in cases:
    chk(f'② hwaccel parse_rc_mode({spec!r})', H.parse_rc_mode(spec), want)
    chk(f'② cpu_v2  parse_rc_mode({spec!r})', C.parse_rc_mode(spec), want)

bad = ['vaapi-vbr', 'nvenc-', 'nvenc-zzz', 'zzz', '-vbr']
for spec in bad:
    hw_first = cv_first = None
    try:
        H.parse_rc_mode(spec)
    except ValueError as exc:
        hw_first = str(exc).splitlines()[0]
    try:
        C.parse_rc_mode(spec)
    except ValueError as exc:
        cv_first = str(exc).splitlines()[0]
    chk(f'② {spec!r} 两脚本都拒绝', (hw_first is not None, cv_first is not None), (True, True))
    chk(f'② {spec!r} 报错首行一致', hw_first, cv_first)

print('── ③ --lookahead 按编码器落到不同选项 ──')
for codec, want in (('hevc_nvenc', '-rc-lookahead 40'),
                    ('h264_nvenc', '-rc-lookahead 40'),
                    ('av1_nvenc', '-rc-lookahead 40'),
                    ('libx264', '-rc-lookahead 40'),
                    ('libx265', '-x265-params rc-lookahead=40'),
                    ('libvpx-vp9', '-lag-in-frames 40'),
                    ('libaom-av1', '-lag-in-frames 40')):
    kw = dict(cq=20) if codec.endswith('_nvenc') else dict(crf=20)
    h, _ = hw(codec, lookahead=40, **kw)
    c, _ = cv(codec, lookahead=40, **kw)
    chk_in(f'③ hwaccel {codec}', want, tokens(h))
    chk_in(f'③ cpu_v2  {codec}', want, tokens(c))

for name, (cmd, w) in (('hwaccel', hw('libsvtav1', crf=30, lookahead=40)),
                       ('cpu_v2', cv('libsvtav1', crf=30, lookahead=40))):
    chk(f'③ {name} libsvtav1 不下发 lookahead', 'lookahead' in tokens(cmd), False)
    chk_in(f'③ {name} libsvtav1 有告知', '未生效', w)

print('── ④ -x265-params 必须与 HDR 元数据合并成同一条 ──')
HDR = {'derived': {'is_hdr': True,
                   'master_display': 'G(13250,34500)B(7500,3000)R(34000,16000)'
                                     'WP(15635,16450)L(10000000,50)',
                   'max_cll': '1000,400'}}
SDR = {'derived': {'is_hdr': False}}
for name, mod in (('hwaccel', H), ('cpu_v2', C)):
    out = mod.build_hdr_args(HDR, 'libx265', extra_x265_params=['rc-lookahead=40'])
    chk(f'④ {name} 合并后只有一条 -x265-params', out.count('-x265-params'), 1)
    chk_in(f'④ {name} 保留了 HDR 元数据', 'master-display=', out[-1])
    chk_in(f'④ {name} 带上了 lookahead', 'rc-lookahead=40', out[-1])
    # 不传 lookahead 时输出必须与改动前逐字相同（HDR 那三个键、顺序不变）
    chk(f'④ {name} 无 lookahead 时不变',
        mod.build_hdr_args(HDR, 'libx265'),
        ['-x265-params',
         'master-display=G(13250,34500)B(7500,3000)R(34000,16000)'
         'WP(15635,16450)L(10000000,50):max-cll=1000,400:hdr10=1'])
    chk(f'④ {name} SDR + lookahead', mod.build_hdr_args(SDR, 'libx265',
                                                        extra_x265_params=['rc-lookahead=40']),
        ['-x265-params', 'rc-lookahead=40'])
    chk(f'④ {name} SDR 无 lookahead 为空', mod.build_hdr_args(SDR, 'libx265'), [])

print('── ⑤ --bitrate：所有编码器都下发；VP9 的 -b:v 0 让位 ──')
for codec, kw in (('libx264', dict(crf=20)), ('libx265', dict(crf=20)),
                  ('libvpx-vp9', dict(crf=32)), ('libsvtav1', dict(crf=30)),
                  ('hevc_nvenc', dict(cq=20))):
    h, _ = hw(codec, bitrate='8M', **kw)
    c, _ = cv(codec, bitrate='8M', **kw)
    chk_in(f'⑤ hwaccel {codec} 下发 -b:v', '-b:v 8M', tokens(h))
    chk_in(f'⑤ cpu_v2  {codec} 下发 -b:v', '-b:v 8M', tokens(c))
chk('⑤ VP9 + 码率时不再补 -b:v 0（hwaccel）', '-b:v 0' in tokens(hw('libvpx-vp9', crf=32, bitrate='2M')[0]), False)
chk('⑤ VP9 + 码率时不再补 -b:v 0（cpu_v2）', '-b:v 0' in tokens(cv('libvpx-vp9', crf=32, bitrate='2M')[0]), False)
chk('⑤ 既有行为：VP9 无码率仍补 -b:v 0（hwaccel）', '-b:v 0' in tokens(hw('libvpx-vp9', crf=32)[0]), True)
chk('⑤ 既有行为：VP9 无码率仍补 -b:v 0（cpu_v2）', '-b:v 0' in tokens(cv('libvpx-vp9', crf=32)[0]), True)

print('── ⑥ 非 NVENC 编码器：rc-mode / qp 忽略并告知（hwaccel 在 strict 下报错）──')
for name, (cmd, w) in (('hwaccel', hw('libx264', crf=20, rc_mode='vbr')),
                       ('cpu_v2', cv('libx264', crf=20, rc_mode='vbr'))):
    chk(f'⑥ {name} libx264 不下发 -rc', '-rc' in cmd, False)
    chk_in(f'⑥ {name} libx264 有告知', 'NVENC 专属', w)
try:
    hw('libx264', crf=20, rc_mode='vbr', policy='strict')
    chk('⑥ hwaccel strict 下抛错', 'no-raise', 'raise')
except ValueError as exc:
    chk_in('⑥ hwaccel strict 下抛错（含提示）', 'strict 不降级', str(exc))

print('── ⑦ 有效组合：-rc / -qp 真的落到命令上 ──')
h, _ = hw('hevc_nvenc', cq=20, rc_mode='vbr_hq')
c, _ = cv('hevc_nvenc', cq=20, rc_mode='vbr_hq')
chk_in('⑦ hwaccel -rc vbr_hq', '-rc vbr_hq', tokens(h))
chk_in('⑦ cpu_v2  -rc vbr_hq', '-rc vbr_hq', tokens(c))
chk('⑦ 两脚本同参数命令 token 顺序一致', tokens(h), tokens(c))
h, _ = hw('hevc_nvenc', rc_mode='constqp', qp=23)
c, _ = cv('hevc_nvenc', rc_mode='constqp', qp=23)
chk_in('⑦ hwaccel constqp 用 -qp', '-rc constqp -qp 23', tokens(h))
chk_in('⑦ cpu_v2  constqp 用 -qp', '-rc constqp -qp 23', tokens(c))

print('── ⑧ 量程/格式校验与两脚本一致性 ──')
for spec, mods in (('--lookahead', (H, C)), ('--qp', (H, C))):
    rng = H._LOOKAHEAD_RANGE if spec == '--lookahead' else H._QP_RANGE
    hint = H._LOOKAHEAD_HINT if spec == '--lookahead' else H._QP_HINT
    first = []
    for mod in mods:
        try:
            mod.check_int_range(rng[1] + 1, spec, rng, hint)
            first.append('no-raise')
        except ValueError as exc:
            first.append(str(exc))
    chk(f'⑧ {spec} 越界两脚本报错一致', first[0], first[1])
    chk_in(f'⑧ {spec} 报错含量程', f'{rng[0]}~{rng[1]}', first[0])
chk('⑧ 两脚本量程常量一致', (H._LOOKAHEAD_RANGE, H._QP_RANGE, H._LOOKAHEAD_HINT, H._QP_HINT),
    (C._LOOKAHEAD_RANGE, C._QP_RANGE, C._LOOKAHEAD_HINT, C._QP_HINT))
chk('⑧ 两脚本 rc 取值表一致', (H._RC_BACKEND, H._RC_MODES, H._RC_MODE_HELP,
                              H._RC_MODES_WITH_BITRATE),
    (C._RC_BACKEND, C._RC_MODES, C._RC_MODE_HELP, C._RC_MODES_WITH_BITRATE))

for spec, want in (('8M', '8M'), ('8000k', '8000k'), ('12000000', '12000000'),
                   ('2.5M', '2.5M'), (None, None)):
    chk(f'⑧ hwaccel parse_bitrate({spec!r})', H.parse_bitrate(spec), want)
    chk(f'⑧ cpu_v2  parse_bitrate({spec!r})', C.parse_bitrate(spec), want)
firsts = []
for mod in (H, C):
    try:
        mod.parse_bitrate('8X')
        firsts.append('no-raise')
    except ValueError as exc:
        firsts.append(str(exc).splitlines()[0])
chk('⑧ --bitrate 非法值两脚本报错首行一致', firsts[0], firsts[1])

print('── ⑨ CLI 层：两脚本的 [ERROR] 首行一致（走真命令行，不只看函数）──')
cli_cases = [
    (['--rc-mode', 'vaapi-vbr'], None),
    (['--rc-mode', 'constqp'], None),
    (['--rc-mode', 'constqp', '--qp', '23', '--bitrate', '8M'], None),
    (['--rc-mode', 'constqp', '--qp', '23', '--cq', '20'], None),
    (['--lookahead', '301'], None),
    (['--bitrate', '8X'], None),
]
for extra, _ in cli_cases:
    lines = []
    for script in ('vidcrop_hwaccel.py', 'vidcrop_cpu_v2.py'):
        p = subprocess.run([sys.executable, str(ROOT / script),
                            '--input', str(SRC), '--output', str(ROOT / 'temp' / 'o'),
                            '--dry-run', '--mode', 'crop',
                            '--output-width', '640', '--output-height', '360'] + extra,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        hit = next((l.strip() for l in (p.stdout + p.stderr).splitlines()
                    if l.strip().startswith('[ERROR]')), '(无 [ERROR])')
        lines.append(hit)
    chk(f'⑨ {" ".join(extra)} 两脚本报错首行一致', lines[0], lines[1])
    chk_in(f'⑨ {" ".join(extra)} 确有报错', '[ERROR]', lines[0])

print()
if fails:
    print(f'✘ {len(fails)} 项失败：')
    for f in fails:
        print(f'   · {f}')
    sys.exit(1)
print('✓ 全部通过')
