# verify/verify_quality_mapping.py — 质量参数「单点换算」与「0 值边界」的行为判据
#
# 覆盖 2026-09-24 那一轮改动的每一条决策（D1..D7，见方案）：
#   D1  --qp 在 NVENC 策略降级到 CPU 编码器时换算成 -crf（此前静默丢弃、回落默认 CRF 21）
#   D2  --rc-mode constqp 下 --qp / --crf-ref / --cq-ref 三选一（--qp 与 -ref 互斥）
#   D3  --pix-fmt auto + 8bit 源两脚本都不下发 -pix_fmt（保住 4:2:2 / 4:4:4）
#   D4  字面量 --crf 落到只认 -cq/-qp 的编码器时按等效表换算（此前静默回落默认 CQ 23）
#   D5  -threads 只对软件编码器下发，两脚本一致；_HW_ENCODERS / _detect_cpu 相等
#   D7  0 = 特殊档哨兵（不走线性换算，直接投影目标的 0 档）；非 0 换算结果钳到 ≥1
#       ⚠ 本组钉的是**下发形状**，不是「是不是逐位无损」——0 档是否逐位无损取决于编码器：
#         本机 libx265/libx264 `-crf 0` 实测是（framemd5 0/50），T4 实测 NVENC `-qp 0` 不是
#         （43417/43448，只是最高质量档）。见 probe/probe_lossless_qp0.sh。
#
# 本套件是**纯 CPU** 的：本机没有 GPU ⇒ `--codec *_nvenc` 会真的走降级链，
# 正好是 D1 要验的那条路径（见 memory/project_dual_env.md）。
import io
import contextlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import vidcrop_hwaccel as H                                   # noqa: E402
import vidcrop_cpu_v2 as C                                    # noqa: E402

WORK = ROOT / 'temp' / 'verify_quality'
SRC_444 = WORK / 'src_444.mp4'          # yuv444p 8bit：D3 的判据素材
SRC = ROOT / 'temp' / 'fixture_1080p.mp4'

fails = []


def chk(label, got, want):
    if got != want:
        fails.append(f'{label}\n      得到 {got!r}\n      期望 {want!r}')


def chk_in(label, needle, hay):
    if needle not in (hay or ''):
        fails.append(f'{label}\n      子串 {needle!r} 不在 {hay!r}')


def q(mod, codec, **kw):
    """调 _resolve_quality_params 并吞掉它的换算提示（提示文本另有用例覆盖）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return mod._resolve_quality_params(codec, **kw)


def qq(mod, codec, crf=None, cq=None, src=None, cref=None, qref=None, qp=None, rc='auto'):
    return q(mod, codec, user_crf=crf, user_cq=cq, src_codec=src, crf_ref=cref,
             cq_ref=qref, qp=qp, rc_mode=rc) if mod is H else \
        q(mod, codec, user_crf=crf, user_cq=cq, crf_ref=cref,
          cq_ref=qref, qp=qp, rc_mode=rc)


def cmd_of(text):
    m = re.search(r'命令[^:]*: (ffmpeg .*)$', text, re.M)
    return m.group(1) if m else ''


def run(script, args, cpu_only=False):
    base = [sys.executable, str(ROOT / script)]
    if cpu_only:
        base += ['--decode', 'cpu', '--scale-algo', 'libswscale-lanczos']
    p = subprocess.run(base + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def err_first(text):
    for line in (text or '').splitlines():
        if line.startswith('[ERROR]'):
            return line
    return ''


def ensure_fixtures():
    WORK.mkdir(parents=True, exist_ok=True)
    if not SRC.exists():
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25:duration=1',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        str(SRC)], check=False)
    if not SRC_444.exists():
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25:duration=1',
                        '-pix_fmt', 'yuv444p', '-c:v', 'libx264', '-crf', '30',
                        str(SRC_444)], check=False)


# ═══════════════════════════════════════════════════════════════════
print('── ① D1：--qp 落到 CPU 编码器时换算成 -crf（此前静默丢弃）──')
chk("[1] hevc_nvenc 的 qp 18 → libx265 的 crf",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp'), (14, None, None))
chk("[1] 同上，与 --cq 18 走同一条换算（qp 与 cq 同量纲）",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp'),
    qq(H, 'libx265', cq=18, src='hevc_nvenc'))
chk("[1] h264_nvenc 的 qp 18 → libx264 的 crf",
    qq(H, 'libx264', src='h264_nvenc', qp=18, rc='constqp'), (13, None, None))
chk("[1] NVENC 目标仍原样透传 -qp",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', qp=18, rc='constqp'), (None, None, 18))
chk("[1] cpu_v2 侧同一条规则（无 src_codec，按 h264_nvenc 量纲）",
    qq(C, 'libx265', qp=18, rc='constqp'), (16, None, None))
chk("[1] 端到端命令：--codec hevc_nvenc --rc-mode constqp --qp 18 → -crf 14",
    '-crf 14' in cmd_of(run('vidcrop_hwaccel.py',
                            ['--input', str(SRC), '--output', str(WORK / 'o1'), '--dry-run',
                             '--codec', 'hevc_nvenc', '--rc-mode', 'constqp', '--qp', '18',
                             '--output-width', '640', '--output-height', '360'], cpu_only=True)[1]),
    True)

print('── ② D2：constqp 下 --qp / --crf-ref / --cq-ref 三选一 ──')
chk("[2] crf-ref 21 + constqp → hevc_nvenc 的 qp 28",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', cref=21, rc='constqp'), (None, None, 28))
chk("[2] cq-ref 23 + constqp → hevc_nvenc 的 qp 26",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', qref=23, rc='constqp'), (None, None, 26))
chk("[2] 非 constqp 时 -ref 仍走 -cq",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', cref=21), (None, 28, None))
for label, extra in (('constqp 无质量输入', ['--rc-mode', 'constqp']),
                     ('constqp + 字面量 --cq', ['--rc-mode', 'constqp', '--qp', '23', '--cq', '20']),
                     ('constqp + qp 与 -ref 同给', ['--rc-mode', 'constqp', '--qp', '23', '--crf-ref', '21']),
                     ('constqp + --bitrate', ['--rc-mode', 'constqp', '--qp', '23', '--bitrate', '8M'])):
    h_rc, h_out = run('vidcrop_hwaccel.py', ['--input', str(SRC), '--output', str(WORK / 'o2'),
                                             '--output-width', '640', '--output-height', '360'] + extra,
                      cpu_only=True)
    c_rc, c_out = run('vidcrop_cpu_v2.py', ['--input', str(SRC), '--output', str(WORK / 'o2'),
                                            '--output-width', '640', '--output-height', '360'] + extra)
    chk(f"[2] {label}：两脚本退出码一致（都拒绝）", (h_rc, c_rc), (2, 2))
    chk(f"[2] {label}：两脚本报错首行逐字相同", err_first(h_out), err_first(c_out))
    chk(f"[2] {label}：首行非空", bool(err_first(h_out)), True)
for label, extra in (('constqp + --crf-ref 21', ['--rc-mode', 'constqp', '--crf-ref', '21']),
                     ('constqp + --cq-ref 23', ['--rc-mode', 'constqp', '--cq-ref', '23'])):
    h_rc, _ = run('vidcrop_hwaccel.py', ['--input', str(SRC), '--output', str(WORK / 'o2'),
                                         '--dry-run', '--output-width', '640',
                                         '--output-height', '360'] + extra, cpu_only=True)
    c_rc, _ = run('vidcrop_cpu_v2.py', ['--input', str(SRC), '--output', str(WORK / 'o2'),
                                        '--dry-run', '--output-width', '640',
                                        '--output-height', '360'] + extra)
    chk(f"[2] {label}：现在被接受（rc=0）", (h_rc, c_rc), (0, 0))

print('── ③ D4：字面量 --crf 落到只认 -cq/-qp 的编码器时换算 ──')
chk("[3] hevc_nvenc + --crf 21 → -cq 28",
    qq(H, 'hevc_nvenc', crf=21, src='hevc_nvenc'), (None, 28, None))
chk("[3] h264_nvenc + --crf 21 → -cq 26",
    qq(H, 'h264_nvenc', crf=21, src='h264_nvenc'), (None, 26, None))
chk("[3] 不再静默回落默认 CQ 23", qq(H, 'hevc_nvenc', crf=21, src='hevc_nvenc')[1] != C.DEFAULT_CQ, True)

print('── ④ D7：0 是特殊档哨兵（不参与线性换算；实测是否逐位无损见文件头注释）──')
for label, args, want in (
        ('libx265 --cq 0', dict(cq=0, src='h264_nvenc'), (0, None, None)),
        ('libx265 --crf 0', dict(crf=0), (0, None, None)),
        ('libx264 --crf-ref 0', dict(cref=0), (0, None, None)),
        ('libvpx-vp9 --cq 0', dict(cq=0, src='h264_nvenc'), (0, None, None)),
        ('libsvtav1 --crf 0', dict(crf=0), (0, None, None)),
        ('librav1e --crf 0', dict(crf=0), (0, None, None)),
):
    chk(f"[4] {label} → 目标的 0 档", qq(H, label.split()[0], **args), want)
chk("[4] h264_nvenc --cq 0 → 交给 build 层的 0 档改写（实测非逐位无损）",
    qq(H, 'h264_nvenc', cq=0, src='h264_nvenc'), (None, 0, None))
chk("[4] hevc_nvenc constqp --qp 0 → -qp 0",
    qq(H, 'hevc_nvenc', qp=0, src='hevc_nvenc', rc='constqp'), (None, None, 0))
chk("[4] cpu_v2 用同一条规则", qq(C, 'libx265', cq=0, src='h264_nvenc'), (0, None, None))
chk("[4] NVENC 的 qp 0 必须显式进 constqp 并补 -b:v 0",
    H.apply_rc_control_args('hevc_nvenc', 'constqp', 0, None, 'auto', None)[0],
    ['-rc', 'constqp', '-qp', '0', '-b:v', '0'])
_b6_rc, _b6_out = run('vidcrop_cpu_v2.py',
                      ['--input', str(SRC), '--output', str(WORK / 'o4'), '--dry-run',
                       '--codec', 'libx265', '--crf', '0', '--bitrate', '8M',
                       '--output-width', '640', '--output-height', '360'])
chk("[4] 0 档 + --bitrate：不报错（rc=0）", _b6_rc, 0)
chk_in("[4] 0 档 + --bitrate：提示'受码率约束'的语义冲突", '受码率约束', _b6_out)

print('── ⑤ D7/B3：非 0 换算的结果**永不落到 0**（低端不得意外命中 0 档）──')
_z = 0
for src in ('h264_nvenc', 'hevc_nvenc'):
    for dst in ('libx264', 'libx265', 'libvpx-vp9', 'libsvtav1', 'libaom-av1'):
        for v in range(1, 7):
            got = qq(H, dst, cq=v, src=src)
            val = got[0] if got[0] is not None else got[1]
            if val == 0:
                _z += 1
                fails.append(f'[5] {src} cq {v} → {dst} 落到 0（=0 档），应为 ≥1：{got}')
chk("[5] 小值扫描里没有任何一格落到 0", _z, 0)
chk("[5] libx264 --cq 4 → -crf 1（此前是 -crf 0 = 真无损）",
    qq(H, 'libx264', cq=4, src='h264_nvenc'), (1, None, None))
chk("[5] libvpx-vp9 --cq 3 → -crf 1", qq(H, 'libvpx-vp9', cq=3, src='h264_nvenc'), (1, None, None))
chk("[5] --cq-ref 4 同样钳到 1", qq(H, 'libx264', qref=4), (1, None, None))
chk("[5] 端到端：--codec libx264 --cq 4 → -crf 1",
    '-crf 1 ' in cmd_of(run('vidcrop_cpu_v2.py',
                            ['--input', str(SRC), '--output', str(WORK / 'o5'), '--dry-run',
                             '--codec', 'libx264', '--cq', '4',
                             '--output-width', '640', '--output-height', '360'])[1]) + ' ',
    True)

print('── ⑥ 边界：上限饱和 / .5 舍入锁定（防漂移）──')
chk("[6] hevc_nvenc 的 crf 51 → qp 被 clamp 到 51",
    qq(H, 'hevc_nvenc', cref=51, src='hevc_nvenc', rc='constqp'), (None, None, 51))
chk("[6] libx265 的 cq 18（hevc 量纲）→ 13.5 → round → 14（银行家舍入）",
    qq(H, 'libx265', cq=18, src='hevc_nvenc'), (14, None, None))
chk("[6] libx265 的 cq 23（hevc 量纲）→ 18.5 → round → 18",
    qq(H, 'libx265', cq=23, src='hevc_nvenc'), (18, None, None))

print('── ⑦ D5 / 孪生一致性：-threads 只软编 + 两张表相等 ──')
chk("[7] _HW_ENCODERS 两脚本集合相等", H._HW_ENCODERS, C._HW_ENCODERS)
chk("[7] _detect_cpu() 两脚本返回值相等", H._detect_cpu(), C._detect_cpu())
chk("[7] DEFAULT_CRF / DEFAULT_CQ 相等", (H.DEFAULT_CRF, H.DEFAULT_CQ),
    (C.DEFAULT_CRF, C.DEFAULT_CQ))
chk("[7] _QP_RANGE / _QP_HINT 相等", (H._QP_RANGE, H._QP_HINT), (C._QP_RANGE, C._QP_HINT))
chk("[7] 软编两边都下发 -threads",
    ('-threads' in cmd_of(run('vidcrop_hwaccel.py',
                              ['--input', str(SRC), '--output', str(WORK / 'o7'), '--dry-run',
                               '--codec', 'libx265', '--output-width', '640',
                               '--output-height', '360'], cpu_only=True)[1]),
     '-threads' in cmd_of(run('vidcrop_cpu_v2.py',
                              ['--input', str(SRC), '--output', str(WORK / 'o7'), '--dry-run',
                               '--codec', 'libx265', '--output-width', '640',
                               '--output-height', '360'])[1])),
    (True, True))
chk("[7] --threads 显式值两边一致",
    (re.search(r'-threads (\d+)', cmd_of(run('vidcrop_hwaccel.py',
                                             ['--input', str(SRC), '--output', str(WORK / 'o7'),
                                              '--dry-run', '--codec', 'libx265', '--threads', '3',
                                              '--output-width', '640', '--output-height', '360'],
                                             cpu_only=True)[1])).group(1),
     re.search(r'-threads (\d+)', cmd_of(run('vidcrop_cpu_v2.py',
                                             ['--input', str(SRC), '--output', str(WORK / 'o7'),
                                              '--dry-run', '--codec', 'libx265', '--threads', '3',
                                              '--output-width', '640', '--output-height', '360'])[1])).group(1)),
    ('3', '3'))
chk("[7] 硬件编码器两边都不下发 -threads",
    ('-threads' in cmd_of(run('vidcrop_cpu_v2.py',
                              ['--input', str(SRC), '--output', str(WORK / 'o7'), '--dry-run',
                               '--codec', 'hevc_nvenc', '--output-width', '640',
                               '--output-height', '360'])[1])),
    False)

print('── ⑧ D3：--pix-fmt auto + 8bit 源两脚本都不下发 -pix_fmt ──')
ensure_fixtures()
if SRC_444.exists():
    h444 = cmd_of(run('vidcrop_hwaccel.py',
                      ['--input', str(SRC_444), '--output', str(WORK / 'o444'), '--dry-run',
                       '--codec', 'libx265', '--output-width', '256', '--output-height', '192'],
                      cpu_only=True)[1])
    c444 = cmd_of(run('vidcrop_cpu_v2.py',
                      ['--input', str(SRC_444), '--output', str(WORK / 'o444'), '--dry-run',
                       '--codec', 'libx265', '--output-width', '256', '--output-height', '192'])[1])
    chk("[8] hwaccel 命令非空（装置有效）", bool(h444), True)
    chk("[8] cpu_v2 命令非空（装置有效）", bool(c444), True)
    chk("[8] hwaccel 不下发 -pix_fmt", '-pix_fmt' in h444, False)
    chk("[8] cpu_v2  不下发 -pix_fmt", '-pix_fmt' in c444, False)
    # 显式给出时必须照发（别把"不下发"做成"永不发"）
    c10 = cmd_of(run('vidcrop_cpu_v2.py',
                     ['--input', str(SRC_444), '--output', str(WORK / 'o444'), '--dry-run',
                      '--codec', 'libx265', '--bit-depth', '10',
                      '--output-width', '256', '--output-height', '192'])[1])
    chk("[8] --bit-depth 10 仍下发 -pix_fmt yuv420p10le", '-pix_fmt yuv420p10le' in c10, True)
    # 偶数尺寸校验不能被拆分改坏
    _rc, _out = run('vidcrop_cpu_v2.py',
                    ['--input', str(SRC_444), '--output', str(WORK / 'o444'),
                     '--output-width', '641', '--output-height', '360'])
    chk("[8] 奇数尺寸仍被拦下（校验没被削弱）", _rc, 2)
    chk_in("[8] 报错说明偶数要求", '偶数', _out)
else:
    fails.append('[8] 装置失效：yuv444p 素材没生成出来')

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
