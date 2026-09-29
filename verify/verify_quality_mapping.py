# verify/verify_quality_mapping.py — 质量参数「单点换算」与「0 值边界」的行为判据
#
# 覆盖 2026-09-24 那一轮改动的每一条决策（D1..D7，见方案）：
#   D1  --qp 在 NVENC 策略降级到 CPU 编码器时换算成 -crf（此前静默丢弃、回落默认 CRF 21）
#   D2  --rc-mode constqp 下 --qp / --crf-ref / --cq-ref 三选一（--qp 与 -ref 互斥）
#   D3  --pix-fmt auto + 8bit 源两脚本都不下发 -pix_fmt（保住 4:2:2 / 4:4:4）
#   D4  字面量 --crf 落到只认 -cq/-qp 的编码器时按等效表换算（此前静默回落默认 CQ 23）
#   D5  -threads 只对软件编码器下发，两脚本一致；_HW_ENCODERS / _detect_cpu 相等
#   D8  ⑨ 组：跨项目（VidUtils ↔ Video_Enhancement）换算体检。结构性不变式（真源
#       一致 / 两脚本各表相等）计入 fails；已知分歧（constqp 轴 / librav1e 双链 /
#       默认质量 / preset 档位 / 字面量量程 / 硬件能力表 / 表内数值）只报告，
#       STRICT_KNOWN=1 时升级为门禁
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
print('── ① D1：--qp 落到 CPU 编码器时换算成 -crf（2026-09-28 V2：回基准轴）──')
chk("[1] hevc_nvenc 的 qp 18 → libx265 的 crf（基准 18，经新表 0.9155x+1.6385）",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp'), (18, None, None))
chk("[1] h264_nvenc 的 qp 18 → libx264 的 crf（基准轴直取 18）",
    qq(H, 'libx264', src='h264_nvenc', qp=18, rc='constqp'), (18, None, None))
chk("[1] NVENC 目标仍原样透传 -qp（不降级时）",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', qp=18, rc='constqp'), (None, None, 18))
chk("[1] cpu_v2 侧同一条规则（无 src_codec，按 h264_nvenc 量纲）",
    qq(C, 'libx265', qp=18, rc='constqp'), (18, None, None))
# V1/V2 的核心：-qp（真实 QP / 基准轴）与 --cq（targetQuality / CQ 轴）**不再同量纲**，
# 故同数字下两条路径的结果必须不同（此前本判据把两者钉成相等，是待修值）。
chk("[1] --qp 18 与 --cq 18 现在走了不同的轴（不再等价）",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp')
    != qq(H, 'libx265', cq=18, src='hevc_nvenc'), True)

# 端到端：在无 NVENC 的环境下 hevc_nvenc 会降级到 libx265 → -crf 18；
# 在有 NVENC (L40) 的环境下直接透传 -qp 18。两者都正确。
_rc, _out = run('vidcrop_hwaccel.py',
                ['--input', str(SRC), '--output', str(WORK / 'o1'), '--dry-run',
                 '--codec', 'hevc_nvenc', '--rc-mode', 'constqp', '--qp', '18',
                 '--output-width', '640', '--output-height', '360'], cpu_only=True)
_has_nvenc = '-qp 18' in cmd_of(_out)
_fallback = '-crf 18' in cmd_of(_out)
chk("[1] 端到端命令：hevc_nvenc + constqp + --qp 18 → -qp 18 (有 NVENC) 或 -crf 18 (降级)",
    _has_nvenc or _fallback, True)

print('── ② D2：constqp 下 --qp / --crf-ref / --cq-ref 三选一（V1：-qp 回基准轴）──')
chk("[2] crf-ref 21 + constqp → hevc_nvenc 的 qp 20（基准 21，经 CQ 值回算）",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', cref=21, rc='constqp'), (None, None, 20))
chk("[2] cq-ref 23 + constqp → hevc_nvenc 的 qp 18",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', qref=23, rc='constqp'), (None, None, 18))
chk("[2] h264_nvenc 的 crf-ref 21 + constqp → -qp 21（无 .5 舍入）",
    qq(H, 'h264_nvenc', src='h264_nvenc', cref=21, rc='constqp'), (None, None, 21))
chk("[2] av1_nvenc 的 crf-ref 21 + constqp → -qp 84（AV1 qindex ≈ 4×QP）",
    qq(H, 'av1_nvenc', src='av1_nvenc', cref=21, rc='constqp'), (None, None, 84))
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
# 用 crf=18 验证"确实做了换算"：它算出的 cq 与「未给质量时的默认 CQ」（V7 后为 26）
# 不同，故能排除"静默回落默认值"。
chk("[3] h264_nvenc + --crf 18 → -cq 23（非默认值，证明做了换算）",
    qq(H, 'h264_nvenc', crf=18, src='h264_nvenc'), (None, 23, None))
chk("[3] 换算值 ≠ 未给质量时的默认值",
    qq(H, 'h264_nvenc', crf=18, src='h264_nvenc')[1]
    != C.default_quality_for('h264_nvenc'), True)

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
chk("[6] hevc_nvenc 的 crf 51 + constqp → -qp 44（CQ 值先被 hi=51 截，再回基准）",
    qq(H, 'hevc_nvenc', cref=51, src='hevc_nvenc', rc='constqp'), (None, None, 44))
chk("[6] av1_nvenc 的 crf 51 + constqp → -qp 204（CQ 值截到 63，再 ×4）",
    qq(H, 'av1_nvenc', cref=51, src='av1_nvenc', rc='constqp'), (None, None, 204))
chk("[6] libx265 的 cq 18（hevc 量纲）→ 换算值（新表 0.9155x+1.6385）",
    qq(H, 'libx265', cq=18, src='hevc_nvenc'), (11, None, None))
chk("[6] libx265 的 cq 23（hevc 量纲）→ 换算值",
    qq(H, 'libx265', cq=23, src='hevc_nvenc'), (16, None, None))

print('── ⑦ D5 / 孪生一致性：-threads 只软编 + 两张表相等 ──')
chk("[7] _HW_ENCODERS 两脚本集合相等", H._HW_ENCODERS, C._HW_ENCODERS)
chk("[7] _detect_cpu() 两脚本返回值相等", H._detect_cpu(), C._detect_cpu())
chk("[7] DEFAULT_REF 相等（V7：统一基准）", H.DEFAULT_REF, C.DEFAULT_REF)
chk("[7] _QP_RANGE / _QP_HINT 相等", (H._QP_RANGE, H._QP_HINT), (C._QP_RANGE, C._QP_HINT))
chk("[7] _QP_SCALE / _QP_LIMITS 相等（V1）",
    (H._QP_SCALE, H._QP_LIMITS), (C._QP_SCALE, C._QP_LIMITS))
chk("[7] _QSV_CODECS 相等（V5）", H._QSV_CODECS, C._QSV_CODECS)
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

# ═══════════════════════════════════════════════════════════════════
# ⑨ 跨项目换算体检（VidUtils ↔ Video_Enhancement）
#
# 两个项目共用同一张 QUALITY_MAP（真源在 convert_crf.py），但**消费层**不同：
#   VidUtils → _resolve_quality_params()            → (crf, cq, qp)
#   VE       → quality_map.resolve_quality() + to_constqp_qp()
# 本组只做**可复现的检测**，不改行为，分两类：
#   · 结构性不变式（真源一致 / 两脚本各表相等）→ chk：红了就是回归；
#   · 已知分歧 → known：打印「实际值 vs VE 模型期望值」，**不计入退出码**，
#     修完自动转 ✓；要当门禁跑：STRICT_KNOWN=1 python verify/verify_quality_mapping.py
#
# 分歧清单的来源与判据见仓库 memory/project_rate_control_params.md；VE 侧的
# 反证见 Video_Enhancement/Accessory/verify/crf_cq_unification_verify.py
# （G3 CONSTQP 轴断言 + G7 真实素材实测：h264_nvenc constqp -qp 21 相对
#  libx264 crf21 为 1.40× 码率 / ΔPSNR −0.26 dB）。
# ═══════════════════════════════════════════════════════════════════
print('── ⑨ 跨项目换算体检（VidUtils ↔ Video_Enhancement）──')

import importlib.util                                             # noqa: E402
import os                                                         # noqa: E402

knowns = []


def note(cid, title, ok, actual, expect, why, expect_label='VE 模型/期望'):
    """记一条跨项目体检项。

    V12：本组已从「只报告」转为**门禁** —— ok=False 会进 fails（退出码非 0），
    `STRICT_KNOWN=0` 可临时降级为只报告（调试用）。
    """
    knowns.append((cid, ok))
    mark = '✓' if ok else '✗'
    if ok:
        print(f'  {mark} {cid} {title}：{actual}')
    else:
        print(f'  {mark} {cid} {title}：实际 {actual}，{expect_label} {expect}\n'
              f'        └─ {why}')
        if os.environ.get('STRICT_KNOWN', '1') != '0':
            fails.append(f'{cid} {title}：实际 {actual}，{expect_label} {expect}'
                         f'（{why}）')


def opt_of(text, opt):
    m = re.search(re.escape(opt) + r' (\S+)', cmd_of(text))
    return m.group(1) if m else None


def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a, **kw)


# 真源：两脚本只 import 了换算函数，QUALITY_MAP 直接从 convert_crf.py 取
_spec = importlib.util.spec_from_file_location('vu_convert_crf', ROOT / 'convert_crf.py')
VU_CRF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VU_CRF)

# ── VE 侧模块（可选：仓库不在旁边时整组降级为 SKIP）────────────────────
VE_ROOT  = ROOT.parent / 'Video_Enhancement'
VE_UTILS = VE_ROOT / 'src' / 'utils'
VE_QM    = None
if (VE_UTILS / 'quality_map.py').is_file() and (VE_UTILS / 'convert_crf.py').is_file():
    try:
        if str(VE_UTILS) not in sys.path:
            sys.path.insert(0, str(VE_UTILS))
        _spec = importlib.util.spec_from_file_location('ve_quality_map',
                                                       VE_UTILS / 'quality_map.py')
        VE_QM = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(VE_QM)
    except Exception as _exc:                                     # noqa: BLE001
        print(f'  ℹ VE 侧模块导入失败（跨项目比对降级为 SKIP）：{_exc}')
        VE_QM = None
else:
    print(f'  ℹ 未找到 {VE_UTILS}，跨项目比对降级为 SKIP')

# ── ⑨-1 / ⑨-2 结构性不变式（真源一致 + 两脚本各表相等）───────────────
chk("[9] 两脚本 NVENC_TO_X264_PRESET 相等",
    H.NVENC_TO_X264_PRESET, C.NVENC_TO_X264_PRESET)
chk("[9] 两脚本 X264_TO_SVTAV1_PRESET 相等",
    H.X264_TO_SVTAV1_PRESET, C.X264_TO_SVTAV1_PRESET)
chk("[9] 两脚本 NVENC_TO_SVTAV1_PRESET 相等",
    H.NVENC_TO_SVTAV1_PRESET, C.NVENC_TO_SVTAV1_PRESET)
chk("[9] 两脚本 CRF_SUPPORTED_CODECS 相等",
    H.CRF_SUPPORTED_CODECS, C.CRF_SUPPORTED_CODECS)
note('[9-CQ]', '两脚本 CQ_SUPPORTED_CODECS 相等（孪生约定）',
     H.CQ_SUPPORTED_CODECS == C.CQ_SUPPORTED_CODECS,
     f'hwaccel-only={sorted(H.CQ_SUPPORTED_CODECS - C.CQ_SUPPORTED_CODECS)}'
     f' / cpu_v2-only={sorted(C.CQ_SUPPORTED_CODECS - H.CQ_SUPPORTED_CODECS)}',
     '两集合相等',
     '[V5 已修复] 此前 hwaccel 含 *_videotoolbox、cpu_v2 含 h265_nvenc。'
     '现按实测/规范收敛为 NVENC + AMF（QSV 无 -cq、VT 用 -q:v）',
     expect_label='期望')

if VE_QM is not None:
    chk("[9] QUALITY_MAP 与 Video_Enhancement 的 convert_crf 逐条相等",
        VU_CRF.QUALITY_MAP, VE_QM.QUALITY_MAP)
    # 抽样比对换算函数（防"表相同、函数不同"）
    _rt = [(c, r) for c in ('libx264', 'libx265', 'hevc_nvenc', 'libvpx-vp9', 'librav1e')
           for r in (0, 21, 35, 51)]
    chk("[9] from_x264_crf 与 VE 逐点相等",
        [H.from_x264_crf(c, r) for c, r in _rt],
        [VE_QM.from_x264_crf(c, r) for c, r in _rt])

# ── ⑨-3 constqp 轴：QP 到底是 CQ 刻度还是基准轴？───────────────────────
# [V1 已修复] -qp 回基准轴（不再拿 CQ 轴值直发）；AV1 另加 ×4 的 QP 尺度层。
# 判据与 VE 的 to_constqp_qp() 对齐：把 CQ 轴值回算到基准轴即为该编码器的 -qp。
for _c in ('h264_nvenc', 'hevc_nvenc'):
    _cq_v = qq(H, _c, src=_c, cref=21)[1]                 # 非 constqp：-cq 值
    _qp_v = qq(H, _c, src=_c, cref=21, rc='constqp')[2]   # constqp：-qp 值
    _exp = (VE_QM.to_constqp_qp(_c, _cq_v) if VE_QM is not None
            else int(round(H.to_x264_crf(_c, _cq_v))))    # 无 VE 时按基准轴手算
    note(f'[9-QP:{_c}]', f'constqp 下 --crf-ref 21 的 -qp（CQ 轴值 {_cq_v}）',
         _qp_v == _exp, f'-qp {_qp_v}', f'-qp {_exp}',
         f'V1：CQ 轴值 {_cq_v} 先 --to_x264_crf--> 基准轴，再取为该编码器的 -qp '
         f'（= VE 的 to_constqp_qp）')
# --qp 落到软编：QP 已在基准轴 ⇒ 直接经等效表换算到软编的 CRF。
_qp_soft = qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp')[0]
_soft_exp = int(round(H.from_x264_crf('libx265', 18)))
note('[9-QP:soft]', '--qp 18 落到 libx265 的 -crf',
     _qp_soft == _soft_exp, f'-crf {_qp_soft}', f'-crf {_soft_exp}',
     'V2：--qp 是基准轴上的真实 QP ⇒ 18 经 libx265 的新等效表（0.9155x+1.6385）→ '
     f'{_soft_exp}（此前按 CQ 轴回算得 14）')

# ── ⑨-4 librav1e：字面量与基准轴走了两条换算链 ──────────────────────────
_lit = opt_of(run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'librav1e', '--crf', '21',
                   '--output-width', '640', '--output-height', '360'])[1], '-qp')
_ref = opt_of(run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'librav1e', '--crf-ref', '21',
                   '--output-width', '640', '--output-height', '360'])[1], '-qp')
note('[9-rav1e]', '--crf 21 与 --crf-ref 21 在 librav1e 上同源',
     _lit == _ref, f'--crf→-qp {_lit} / --crf-ref→-qp {_ref}', '两者相同（均为 80）',
     '[V8 已修复] librav1e 移出 CRF_SUPPORTED_CODECS，字面量与 -ref 都先归一到基准轴，'
     '再经 crf_to_rav1e_qp 得 80（此前字面量按 libaom 刻度得 64）')

# ── ⑨-5 默认质量：DEFAULT_CRF 21 与 DEFAULT_CQ 23 不等效 ────────────────
_c_def = opt_of(run('vidcrop_cpu_v2.py',
                    ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                     '--codec', 'libx264',
                     '--output-width', '640', '--output-height', '360'])[1], '-crf')
_q_def = opt_of(run('vidcrop_cpu_v2.py',
                    ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                     '--codec', 'h264_nvenc',
                     '--output-width', '640', '--output-height', '360'])[1], '-cq')
_q_back = H.to_x264_crf('h264_nvenc', int(_q_def)) if _q_def else None
note('[9-default]', '未给质量参数时软编/硬编的默认质量等效',
     bool(_q_def and _c_def and int(_q_back) == int(_c_def)),
     f'libx264 -crf {_c_def} / h264_nvenc -cq {_q_def}（回基准 {_q_back}）',
     f'回基准 {_c_def}',
     '[V7 已修复] 统一用 DEFAULT_REF=21：未给质量时按基准换算到目标编码器'
     '（libx264 21 / libx265 21 / h264_nvenc 26 / hevc_nvenc 28）。'
     '此前 DEFAULT_CRF=21 与 DEFAULT_CQ=23（≡ crf 18）不等效')

# ── ⑨-6 preset 档位：两项目 x264→NVENC 映射错位 + svtav1 表内不自洽 ──────
_ve_sdk = VE_ROOT / 'external' / 'realesrgan_video' / 'nvenc_sdk.py'
if _ve_sdk.is_file():
    _m = re.search(r'_PRESET_P_INDEX: dict = \{(.*?)\}', _ve_sdk.read_text(encoding='utf-8'),
                   re.S)
    _ve_idx = dict(re.findall(r'"(\w+)":\s*(\d+)', _m.group(1))) if _m else {}
    # 真跑一遍 normalize_preset（而不是反查表）：这样连"表里没这一档、回落到 p5"
    # 的情形也能抓到（x264 的 fast 正是如此）。
    _bad = []
    for _n in ('superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow'):
        if _n not in _ve_idx:
            continue
        _ve_p = f'p{int(_ve_idx[_n]) + 1}'
        _vu_p = quiet(H.normalize_preset, _n, 'h264_nvenc', True)
        if _ve_p != _vu_p:
            _bad.append(f'{_n}: VidUtils {_vu_p} vs VE {_ve_p}')
    note('[9-preset]', 'x264→NVENC preset 档位两项目一致',
         not _bad, '一致' if not _bad else '；'.join(_bad), '逐档相同',
         '[V10 已修复] VidUtils 的 NVENC_TO_X264_PRESET / X264_TO_NVENC_PRESET 已按 VE 的'
         ' _PRESET_P_INDEX 取向对齐（superfast→p1 … slow→p6）')
_p7 = quiet(H.normalize_preset, 'p7', 'libsvtav1', True)
_vs = quiet(H.normalize_preset, H.NVENC_TO_X264_PRESET['p7'], 'libsvtav1', True)
note('[9-preset:svt]', 'libsvtav1 的 p7 与 veryslow 同档',
     _p7 == _vs, f'p7→{_p7} / veryslow→{_vs}', '两者相同',
     '[V10 已修复] NVENC_TO_SVTAV1_PRESET[p7] 与 X264_TO_SVTAV1_PRESET[veryslow] 已一致'
     '（都是 2），且 X264_TO_SVTAV1_PRESET 不再让 fast / medium 都落 8',
     expect_label='期望')

# ── ⑨-7 字面量量程：统一 0-63，与生效编码器量程不符 ──────────────────────
_rc_q, _o_q = run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'h264_nvenc', '--cq', '60',
                   '--output-width', '640', '--output-height', '360'])
_rc_c, _o_c = run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'libx264', '--crf', '60',
                   '--output-width', '640', '--output-height', '360'])
_q60, _r60 = opt_of(_o_q, '-cq'), opt_of(_o_c, '-crf')
note('[9-range]', '字面量质量按生效编码器量程校验',
     _rc_q == 2 and _rc_c == 2,
     f'h264_nvenc --cq 60 → rc={_rc_q}，命令 -cq {_q60}；'
     f'libx264 --crf 60 → rc={_rc_c}，命令 -crf {_r60}',
     'CLI 层按生效编码器量程拒绝（literal_range）',
     '[已由 V4 修复] 此前 --crf/--cq 统一按 0-63 收：-cq 60 让 ffmpeg 报 out of range '
     '[0-51] 直接失败，-crf 60 被静默按 51 编码（最差质量、无提示）。'
     '现在按 literal_range 判定，详见 ⑩ 组的正向断言')

# ── ⑨-8 硬件编码器能力表：默认路径就发无效选项 / 静默丢质量 ─────────────
_qsv = cmd_of(run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'h264_qsv',
                   '--output-width', '640', '--output-height', '360'])[1])
_vaapi = cmd_of(run('vidcrop_cpu_v2.py',
                    ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                     '--codec', 'h264_vaapi', '--cq', '26',
                     '--output-width', '640', '--output-height', '360'])[1])
note('[9-hw]', 'h264_qsv 默认不发非法 -preset',
     '-preset p5' not in _qsv, '默认下发了 -preset p5' if '-preset p5' in _qsv else '未下发',
     '按 QSV 取值（veryfast..veryslow）或不下发',
     '[V5 已修复] QSV 移出 CQ_SUPPORTED_CODECS ⇒ default_preset_for 给 medium（合法），'
     'normalize_preset 也把 pN 映射成 veryfast..veryslow；不再默认下发 QSV 不认的 p5')
note('[9-hw:vaapi]', 'h264_vaapi 收到 --cq 时不静默丢弃',
     any(o in _vaapi for o in ('-crf ', '-cq ', '-qp ')),
     '命令里没有任何质量参数' if not any(o in _vaapi for o in ('-crf ', '-cq ', '-qp '))
     else '有质量参数',
     '换算成 -qp（VAAPI 有 -qp 0..52）',
     '[已由 V6 修复] 此前 QUALITY_MAP 有 vaapi 条目但 CQ/CRF 能力集都不含 ⇒ '
     '落到 (None,None,None)：用户给的 --cq 26 一条选项都不发、且无告警。'
     '现在走 _QP_ONLY_CODECS → -qp，详见 ⑩ 组')

# ── ⑨-9 / ⑨-10 换算表自身的数值问题（纯计算）────────────────────────────
_vp9 = VU_CRF.QUALITY_MAP['libvpx-vp9']
_lo_sat = sum(1 for r in range(0, 52) if H.from_x264_crf('libvpx-vp9', r) <= _vp9[2])
_hi_sat = sum(1 for r in range(0, 52) if H.from_x264_crf('libvpx-vp9', r) >= _vp9[3])
_common_sat = sum(1 for r in range(18, 29)
                  if H.from_x264_crf('libvpx-vp9', r) <= _vp9[2]
                  or H.from_x264_crf('libvpx-vp9', r) >= _vp9[3])
note('[9-vp9]', 'libvpx-vp9 的等效表在常用区不饱和、零点合理',
     _common_sat == 0 and _lo_sat <= 5,
     f'常用区(18~28) 饱和 {_common_sat}/11；全表低端 {_lo_sat}/52→0、'
     f'高端 {_hi_sat}/52→63（a={_vp9[0]}, b={_vp9[1]}）',
     '常用区不饱和、低端饱和 ≤5',
     '[V9 已修复] 按真实素材 new5_raw.mp4 等体积重标为 1.6198x−5.7553：常用区不再饱和，'
     '零点从 crf≈7.3 移到 ≈3.6（旧表 (1.98,−14.46) 是常用点巧合、两端大面积饱和）。'
     '⚠ a≠1 的线性表在两端仍会饱和，这是固有性质',
     expect_label='期望')
_hvt = VU_CRF.QUALITY_MAP['hevc_videotoolbox']
_reachable = {H.from_x264_crf('hevc_videotoolbox', r) for r in range(0, 52)}
_vt_hi_sat = sum(1 for r in range(0, 52)
                 if H.from_x264_crf('hevc_videotoolbox', r) >= _hvt[3])
note('[9-vt]', 'hevc_videotoolbox 的 lo/hi 与线性参数自洽',
     _hvt[2] in _reachable and _vt_hi_sat <= 1,
     f'声明 [{_hvt[2]}, {_hvt[3]}]，实际可达 [{min(_reachable)}, {max(_reachable)}]；'
     f'高端饱和 {_vt_hi_sat}/52',
     'lo 可达、且只在端点处饱和',
     '[V11 已修复] b 105→100（与 h264_videotoolbox 对齐）：lo=1 现在可达（crf 51 → 1.0），'
     '只有 crf 0 恰落到 hi=100，不再 crf 0~2.58 全饱和到 100',
     expect_label='期望')

# ═══════════════════════════════════════════════════════════════════
# ⑩ V3/V4/V6：量程按生效编码器校验 + VAAPI 族的 -qp 通路
#    V3  QUALITY_MAP['av1_nvenc'] 的 hi 51 → 63（实测 `-cq (0 to 63)`）
#    V4  literal_range()：字面量按生效编码器量程校验；同族下发路径也钳位
#    V6  _QP_ONLY_CODECS（VAAPI 族）：任意质量输入归一到基准轴后走 -qp
# ═══════════════════════════════════════════════════════════════════
print('── ⑩ V3/V4/V6：量程与 QP-only 编码器 ──')

print('  [V3] av1_nvenc 的 CQ 量程')
# 规格检查：静态表 QUALITY_MAP 应符合 NVIDIA 规格（0~63）
_av1_hi = H.QUALITY_MAP.get('av1_nvenc', (0,0,0,0))[3]
chk("[10] QUALITY_MAP['av1_nvenc'] hi == 63（NVIDIA 规格 0~63）", _av1_hi, 63)
# 运行时校验：literal_range 返回 ffmpeg 实际量程（ffmpeg 6.1.1 报 0~51，升级后将匹配 0~63）
_rt_cq = H.literal_range('av1_nvenc', 'cq')
chk("[10] literal_range('av1_nvenc','cq') 返回 ffmpeg 实际量程", _rt_cq, (0, 51))
chk("[10] crf_ref 51 → av1_nvenc -cq 57（此前被 hi=51 截到 51）",
    qq(H, 'av1_nvenc', cref=51), (None, 57, None))
chk("[10] crf_ref 40 → av1_nvenc -cq 46（常用区不受影响）",
    qq(H, 'av1_nvenc', cref=40), (None, 46, None))

print('  [V4] literal_range：字面量按生效编码器量程（运行时查询 ffmpeg 实际范围）')
# 期望值基于当前 ffmpeg 6.1.1 的实际报告值
for _c, _k, _want in (('libx264', 'crf', (0, 51)), ('libx265', 'crf', (0, 51)),
                      ('libvpx-vp9', 'crf', (-1, 63)), ('libsvtav1', 'crf', (0, 63)),
                      ('libaom-av1', 'crf', (-1, 63)), ('h264_nvenc', 'cq', (0, 51)),
                      ('av1_nvenc', 'cq', (0, 51)),   # ffmpeg 6.1.1 报 0~51，升级后为 0~63
                      ('libx264', 'cq', (0, 51)),        # 软编不认 cq ⇒ 回退源轴
                      ('libvpx-vp9', 'cq', (0, 51)),
                      ('auto', 'crf', (0, 51)), ('no_such_codec', 'crf', (0, 51))):
    chk(f"[10] literal_range({_c}, {_k}) == {_want}", H.literal_range(_c, _k), _want)
chk("[10] 两脚本 literal_range 逐点相等",
    [H.literal_range(c, k) for c in ('libx264', 'libvpx-vp9', 'libsvtav1', 'av1_nvenc',
                                     'h264_vaapi', 'auto')
     for k in ('crf', 'cq')],
    [C.literal_range(c, k) for c in ('libx264', 'libvpx-vp9', 'libsvtav1', 'av1_nvenc',
                                     'h264_vaapi', 'auto')
     for k in ('crf', 'cq')])
# 端到端：超量程在 CLI 层被拦（两脚本退出码 + 报错首行逐字相同）
_h_r, _h_o = run('vidcrop_hwaccel.py',
                 ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                  '--codec', 'h264_nvenc', '--cq', '60',
                  '--output-width', '640', '--output-height', '360'], cpu_only=True)
_c_r, _c_o = run('vidcrop_cpu_v2.py',
                 ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                  '--codec', 'h264_nvenc', '--cq', '60',
                  '--output-width', '640', '--output-height', '360'])
chk("[10] --cq 60 --codec h264_nvenc：两脚本都拒绝（rc=2）", (_h_r, _c_r), (2, 2))
chk("[10] 两脚本报错首行逐字相同", err_first(_h_o), err_first(_c_o))
chk_in("[10] 报错点明实际量程 0~51", '0~51', _h_o)
_c2_r, _c2_o = run('vidcrop_cpu_v2.py',
                   ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                    '--codec', 'libx264', '--crf', '60',
                    '--output-width', '640', '--output-height', '360'])
chk("[10] --crf 60 --codec libx264：拒绝（此前被静默按 51 编码）", _c2_r, 2)
for _lbl, _opt, _val, _codec in (('vp9 的 0~63 不被误伤', '--crf', '60', 'libvpx-vp9'),
                                 ('svtav1 的 0~63 不被误伤', '--crf', '55', 'libsvtav1'),
                                 ('nvenc 量程内正常放行', '--cq', '51', 'h264_nvenc')):
    _rc, _ = run('vidcrop_cpu_v2.py',
                 ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                  '--codec', _codec, _opt, _val,
                  '--output-width', '640', '--output-height', '360'])
    chk(f"[10] {_lbl}", _rc, 0)

print('  [V6] VAAPI 族：-cq/-crf 归一到基准轴后走 -qp')
chk("[10] 两脚本 _QP_ONLY_CODECS 相等且非空",
    (C._QP_ONLY_CODECS, C._QP_ONLY_CODECS == H._QP_ONLY_CODECS),
    (H._QP_ONLY_CODECS, True))
for _c, _kw, _want in (('h264_vaapi', {'cq': 26}, 21),      # 26 - 5 = 21
                       ('h264_vaapi', {'crf': 21}, 21),
                       ('hevc_vaapi', {'cq': 26}, 21),
                       ('hevc_vaapi', {'qref': 26}, 21),    # 经 h264_nvenc 归一
                       ('h264_vaapi', {'cref': 30}, 30),
                       ('h264_vaapi', {'qp': 23, 'rc': 'constqp'}, 23),
                       ('h264_vaapi', {'cq': 0}, 0)):       # 0 档保持 0
    chk(f"[10] {_c} {_kw} → -qp {_want}", qq(H, _c, **_kw), (None, None, _want))
_va = cmd_of(run('vidcrop_cpu_v2.py',
                 ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                  '--codec', 'h264_vaapi', '--cq', '26',
                  '--output-width', '640', '--output-height', '360'])[1])
chk_in("[10] VAAPI 命令含 -qp 21（此前静默无任何质量参数）", '-qp 21', _va)
chk("[10] VAAPI 命令不再出现无效的 -cq / -crf",
    ('-cq ' in _va or '-crf ' in _va), False)
# --rc-mode 落到 VAAPI：模式名要告警，但 --qp 不该被误报为"已忽略"
_rc_rc, _rc_o = run('vidcrop_cpu_v2.py',
                    ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                     '--codec', 'h264_vaapi', '--rc-mode', 'constqp', '--qp', '23',
                     '--output-width', '640', '--output-height', '360'])
chk("[10] VAAPI + constqp + --qp：仍能出命令（rc=0）", _rc_rc, 0)
chk_in("[10] 命令里 --qp 生效", '-qp 23', cmd_of(_rc_o))
chk_in("[10] --rc-mode 的模式名告警保留", '-rc 是 NVENC 专属选项', _rc_o)

# ═══════════════════════════════════════════════════════════════════
# ⑪ V1/V2/V5/V7/V8/V10/V11：2026-09-28 修复的正向断言
# ═══════════════════════════════════════════════════════════════════
print('── ⑪ V1/V2/V5/V7/V8/V10/V11 正向断言 ──')

print('  [V1] constqp 轴：-qp 回基准轴（含 AV1 的 ×4 QP 尺度）')
chk("[11] qp_scale：AV1 族 4、其余 1",
    [H.qp_scale(c) for c in ('av1_nvenc', 'librav1e', 'h264_nvenc', 'libx265')],
    [4, 4, 1, 1])
chk("[11] qp_limits('av1_nvenc') == (0,255)，≠ CQ 轴规格量程 (0,63)（运行时 ffmpeg 6.1.1 报 0~51）",
    (H.qp_limits('av1_nvenc'), H.literal_range('av1_nvenc', 'cq')), ((0, 255), (0, 51)))
chk("[11] to_constqp_qp('h264_nvenc', 26) == 21（对齐 VE 的 to_constqp_qp）",
    H.to_constqp_qp('h264_nvenc', 26), 21)
chk("[11] to_constqp_qp('hevc_nvenc', 28) == 20（20.5 银行家舍入）",
    H.to_constqp_qp('hevc_nvenc', 28), 20)
chk("[11] to_constqp_qp('av1_nvenc', 27) == 84（(27−6)=21 基准 ×4）",
    H.to_constqp_qp('av1_nvenc', 27), 84)
chk("[11] from_constqp_qp('av1_nvenc', 84) == 21（反向自洽）",
    H.from_constqp_qp('av1_nvenc', 84), 21.0)
chk("[11] 两脚本 to_constqp_qp 逐点相等",
    [H.to_constqp_qp(c, v) for c in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc', 'librav1e')
     for v in (0, 18, 26, 40)],
    [C.to_constqp_qp(c, v) for c in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc', 'librav1e')
     for v in (0, 18, 26, 40)])
chk("[11] qp_range 按编码器取",
    (H.qp_range('av1_nvenc'), H.qp_range('h264_vaapi'), H.qp_range('libx264')),
    ((0, 255), (0, 52), (0, 51)))

print('  [V2] --qp 落到软编按基准轴回算（不再走 CQ 轴）')
chk("[11] libx265：--qp 18 → -crf 18（新表），不是 CQ 轴的 14",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp'), (18, None, None))
chk("[11] 端到端 cpu_v2：libx265 + constqp --qp 18 → -crf 18",
    '-crf 18' in cmd_of(run('vidcrop_cpu_v2.py',
                            ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                             '--codec', 'libx265', '--rc-mode', 'constqp', '--qp', '18',
                             '--output-width', '640', '--output-height', '360'])[1]),
    True)

print('  [V5] 硬件能力表：QSV / VideoToolbox')
chk("[11] QSV 不在 CQ_SUPPORTED_CODECS",
    sorted({'h264_qsv', 'hevc_qsv', 'av1_qsv'} & H.CQ_SUPPORTED_CODECS), [])
chk("[11] VideoToolbox 不在 CQ_SUPPORTED_CODECS",
    sorted({'h264_videotoolbox', 'hevc_videotoolbox'} & H.CQ_SUPPORTED_CODECS), [])
chk("[11] h265_nvenc 冗余项已移除", 'h265_nvenc' in H.CQ_SUPPORTED_CODECS, False)
chk("[11] QSV 默认 preset = medium（合法档，非 p5）", H.default_preset_for('h264_qsv'), 'medium')
chk("[11] QSV：--preset p5 映射为 medium（反向降级表的 p5）",
    quiet(H.normalize_preset, 'p5', 'h264_qsv', True), 'medium')
_qsq = cmd_of(run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                   '--codec', 'h264_qsv',
                   '--output-width', '640', '--output-height', '360'])[1])
chk("[11] QSV 默认命令不含 -cq", '-cq ' in _qsq, False)
chk_in("[11] QSV 默认命令的 preset 是 medium", '-preset medium', _qsq)
_qsq2_rc, _qsq2 = run('vidcrop_cpu_v2.py',
                      ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                       '--codec', 'h264_qsv', '--cq', '26',
                       '--output-width', '640', '--output-height', '360'])
chk("[11] QSV 收到 --cq 仍能出命令（rc=0）", _qsq2_rc, 0)
chk_in("[11] QSV --cq 有告警（不静默丢值）", '没有可用的质量参数', _qsq2)

print('  [V7] 默认质量统一到基准 DEFAULT_REF=21')
chk("[11] DEFAULT_REF=21（两脚本）", (H.DEFAULT_REF, C.DEFAULT_REF), (21, 21))
chk("[11] default_quality_for：libx264/21、libx265/21、h264_nvenc/26、hevc_nvenc/28",
    [C.default_quality_for(c) for c in ('libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc')],
    [21, 21, 26, 28])
for _codec, _opt, _val in (('libx264', '-crf', '21'), ('libx265', '-crf', '21'),
                           ('h264_nvenc', '-cq', '26'), ('hevc_nvenc', '-cq', '28')):
    _cmd = cmd_of(run('vidcrop_cpu_v2.py',
                      ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                       '--codec', _codec,
                       '--output-width', '640', '--output-height', '360'])[1])
    chk_in(f"[11] 未给质量：{_codec} → {_opt} {_val}", f'{_opt} {_val}', _cmd)

print('  [V8] librav1e 单链')
chk("[11] librav1e 不在 CRF_SUPPORTED_CODECS（两脚本）",
    ('librav1e' in H.CRF_SUPPORTED_CODECS, 'librav1e' in C.CRF_SUPPORTED_CODECS),
    (False, False))
chk("[11] librav1e 字面量 --crf 21 → -qp 80",
    '-qp 80' in cmd_of(run('vidcrop_cpu_v2.py',
                           ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                            '--codec', 'librav1e', '--crf', '21',
                            '--output-width', '640', '--output-height', '360'])[1]), True)
chk("[11] librav1e --crf-ref 21 → -qp 80（与字面量同源）",
    '-qp 80' in cmd_of(run('vidcrop_cpu_v2.py',
                           ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                            '--codec', 'librav1e', '--crf-ref', '21',
                            '--output-width', '640', '--output-height', '360'])[1]), True)

print('  [V10] preset 表')
chk("[11] 两脚本 X264_TO_NVENC_PRESET 相等", H.X264_TO_NVENC_PRESET, C.X264_TO_NVENC_PRESET)
chk("[11] superfast→p1 / medium→p4（官方枚举，与 VE 对齐）",
    (H.X264_TO_NVENC_PRESET['superfast'], H.X264_TO_NVENC_PRESET['medium']), ('p1', 'p4'))
chk("[11] svtav1：fast≠medium，且 p7==veryslow",
    (H.X264_TO_SVTAV1_PRESET['fast'] != H.X264_TO_SVTAV1_PRESET['medium'],
     H.NVENC_TO_SVTAV1_PRESET['p7'] == H.X264_TO_SVTAV1_PRESET['veryslow']), (True, True))
chk("[11] libsvtav1 默认 preset 固定为 8（不随核数漂移）", H.default_preset_for('libsvtav1'), '8')

print('  [V11] hevc_videotoolbox 量程')
chk("[11] hevc_videotoolbox b == 100", VU_CRF.QUALITY_MAP['hevc_videotoolbox'][1], 100.0)

if knowns:
    _bad = [cid for cid, ok in knowns if not ok]
    print(f'── ⑨ 跨项目体检：{len(knowns) - len(_bad)}/{len(knowns)} 项一致'
          + (f'，{len(_bad)} 项失败（已计入退出码）' if _bad else '') + '──')
    for cid in _bad:
        print(f'  · {cid}')
    # V12：⑨ 组现在默认就是门禁（note() 里 ok=False 直接进 fails）。
    # STRICT_KNOWN=0 仅供本地调试降级为"只报告"。
    if _bad and os.environ.get('STRICT_KNOWN') == '0':
        fails[:] = [f for f in fails if not any(f.startswith(cid) for cid in _bad)]

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
