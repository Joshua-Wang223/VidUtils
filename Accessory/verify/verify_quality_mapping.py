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
#   D9  HDR 域覆盖表（2026-10-16，[12] 组）：set_quality_domain('hdr') 仅把
#       libsvtav1/libaom-av1/librav1e 三行切到 HDR 10bit PQ 域等质量标定值；
#       默认 'sdr' 零回归，QUALITY_MAP/SIZE_MAP 本体与跨仓契约不动。
#       libsvtav1 / libaom-av1 行为分段模型（PiecewiseLinear，2026-10-16 升级，§9/§10）。
#
# 本套件是**纯 CPU** 的：本机没有 GPU ⇒ `--codec *_nvenc` 会真的走降级链，
# 正好是 D1 要验的那条路径（见 memory/project_dual_env.md）。
import io
import contextlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _paths import repo_root, temp_root
ROOT = repo_root()
sys.path.insert(0, str(ROOT))
import vidcrop_hwaccel as H                                   # noqa: E402
import vidcrop_cpu_v2 as C                                    # noqa: E402

# 本判据的既有期望值全部按**等体积（size）**口径写成；而 convert_crf 的默认口径已改为
# 'quality'（2026-09-30），故这里显式钉住 size，避免默认口径改动把判据染红。
# （等质量口径的专项断言在 ⑨-eqq 组内临时切到 'quality' 再切回。）
# R1（tier）起另有两条约定：
#   · CLI 调用默认带 --quality-tier balanced（无窗口豁免）；**直接调
#     _resolve_quality_params 的用例一律显式钉 tier** —— 测换算路径的既有用例
#     要把值钉进「含该值的档」（如 qp 18 → archive），否则先被 balanced 窗口
#     钳制成 23 再换算，测的就不是原路径了；
#   · 钉档后期望值与 pre-R1 逐条相同（同一段换算链）；窗口/锚点/哨兵/rc 轴等
#     新语义的专项断言在 ⑬ 组。
H.set_quality_mode('size')

WORK = temp_root() / 'verify_quality'
SRC_444 = WORK / 'src_444.mp4'          # yuv444p 8bit：D3 的判据素材
SRC = temp_root() / 'fixture_1080p.mp4'

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


def qq(mod, codec, crf=None, cq=None, src=None, cref=None, qref=None, qp=None, rc='auto',
       tier=None):
    """tier=None ⇒ 走模块当前档（默认 balanced）；测换算路径时显式钉含该值的档。"""
    return q(mod, codec, user_crf=crf, user_cq=cq, src_codec=src, crf_ref=cref,
             cq_ref=qref, qp=qp, rc_mode=rc, tier=tier) if mod is H else \
        q(mod, codec, user_crf=crf, user_cq=cq, crf_ref=cref,
          cq_ref=qref, qp=qp, rc_mode=rc, tier=tier)


def cmd_of(text):
    m = re.search(r'命令[^:]*: (ffmpeg .*)$', text, re.M)
    return m.group(1) if m else ''


def run(script, args, cpu_only=False):
    base = [sys.executable, str(ROOT / script)]
    if cpu_only:
        base += ['--decode', 'cpu', '--scale-algo', 'libswscale-lanczos']
    # 既有期望值按**等体积**口径写成；脚本默认已是 'quality'，故显式钉 size 保证可复现。
    base += ['--quality-mode', 'size']
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
# R1：全部钉在 archive（窗 [13,18] 恰好含 18/13）——显式值不再被默认档 balanced 钳制。
chk("[1] hevc_nvenc 的 qp 18 → libx265 的 crf（基准 18，经新表 0.9155x+1.6385）",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp', tier='archive'), (18, None, None))
chk("[1] h264_nvenc 的 qp 18 → libx264 的 crf（基准轴直取 18）",
    qq(H, 'libx264', src='h264_nvenc', qp=18, rc='constqp', tier='archive'), (18, None, None))
chk("[1] NVENC 目标仍原样透传 -qp（不降级时）",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', qp=18, rc='constqp', tier='archive'), (None, None, 18))
chk("[1] cpu_v2 侧同一条规则（无 src_codec，按 h264_nvenc 量纲）",
    qq(C, 'libx265', qp=18, rc='constqp', tier='archive'), (18, None, None))
# V1/V2 的核心：-qp（真实 QP / 基准轴）与 --cq（targetQuality / CQ 轴）**不再同量纲**，
# 故同数字下两条路径的结果必须不同（此前本判据把两者钉成相等，是待修值）。
# R1 附注：--cq 18 在 hevc 量纲下归一到 10.5 → 被 archive 下界钳到 13 → libx265 13；
# 而 --qp 18 是基准轴上的 18 → 仍 18。两者不同，且顺带覆盖「非 0 归一后被窗口钳制」。
chk("[1] --qp 18 与 --cq 18 现在走了不同的轴（不再等价）",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp', tier='archive')
    != qq(H, 'libx265', cq=18, src='hevc_nvenc', tier='archive'), True)
chk("[1] --cq 18（hevc 量纲）在 archive 归一后被钳到 13 → libx265 13",
    qq(H, 'libx265', cq=18, src='hevc_nvenc', tier='archive'), (13, None, None))

# 端到端：在无 NVENC 的环境下 hevc_nvenc 会降级到 libx265 → -crf 18；
# 在有 NVENC (L40) 的环境下直接透传 -qp 18。两者都正确。
# R1：钉 archive，否则默认档 balanced 会把 18 钳到 23（窗口接管）。
_rc, _out = run('vidcrop_hwaccel.py',
                ['--input', str(SRC), '--output', str(WORK / 'o1'), '--dry-run',
                 '--codec', 'hevc_nvenc', '--rc-mode', 'constqp', '--qp', '18',
                 '--quality-tier', 'archive',
                 '--output-width', '640', '--output-height', '360'], cpu_only=True)
_has_nvenc = '-qp 18' in cmd_of(_out)
_fallback = '-crf 18' in cmd_of(_out)
chk("[1] 端到端命令：hevc_nvenc + constqp + --qp 18 → -qp 18 (有 NVENC) 或 -crf 18 (降级)",
    _has_nvenc or _fallback, True)

print('── ② D2：constqp 下 --qp / --crf-ref / --cq-ref 三选一（V1：-qp 回基准轴）──')
chk("[2] crf-ref 21 + constqp → hevc_nvenc 的 qp 20（基准 21，经 CQ 值回算；钉 quality）",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', cref=21, rc='constqp', tier='quality'),
    (None, None, 20))
# cq-ref 23 归一到基准 18.02 → round 18（刚好是 archive 窗的上界，钉 archive）。
chk("[2] cq-ref 23 + constqp → hevc_nvenc 的 qp 18（钉 archive）",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', qref=23, rc='constqp', tier='archive'),
    (None, None, 18))
chk("[2] h264_nvenc 的 crf-ref 21 + constqp → -qp 21（无 .5 舍入；钉 quality）",
    qq(H, 'h264_nvenc', src='h264_nvenc', cref=21, rc='constqp', tier='quality'),
    (None, None, 21))
chk("[2] av1_nvenc 的 crf-ref 21 + constqp → -qp 63（AV1 qindex ≈ 3×QP，L40 扩扫最佳；钉 quality）",
    qq(H, 'av1_nvenc', src='av1_nvenc', cref=21, rc='constqp', tier='quality'),
    (None, None, 63))
chk("[2] 非 constqp 时 -ref 仍走 -cq（钉 quality）",
    qq(H, 'hevc_nvenc', src='hevc_nvenc', cref=21, tier='quality'), (None, 28, None))
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
# R1：--crf 在不支持 CRF 的编码器上按「libx264 CRF 量纲」直取基准轴（与 crf_to_cq
# 及 rav1e 单链口径一致），再过档窗口。21 钉 quality、18 钉 archive。
chk("[3] hevc_nvenc + --crf 21 → -cq 28（钉 quality）",
    qq(H, 'hevc_nvenc', crf=21, src='hevc_nvenc', tier='quality'), (None, 28, None))
chk("[3] h264_nvenc + --crf 21 → -cq 26（钉 quality）",
    qq(H, 'h264_nvenc', crf=21, src='h264_nvenc', tier='quality'), (None, 26, None))
# 用 crf=18 验证"确实做了换算"：它算出的 cq 与「未给质量时的默认 CQ」（R1 后：锚点
# 24 → 29）不同，故能排除"静默回落默认值"。
chk("[3] h264_nvenc + --crf 18 → -cq 23（非默认值，证明做了换算；钉 archive）",
    qq(H, 'h264_nvenc', crf=18, src='h264_nvenc', tier='archive'), (None, 23, None))
chk("[3] 换算值 ≠ 未给质量时的默认值",
    qq(H, 'h264_nvenc', crf=18, src='h264_nvenc', tier='archive')[1]
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
# R1：默认（balanced）下小值先被窗口钳到 23 ⇒ 更不存在落到 0 的可能；本扫描保留为
# 负向回归（任何一格出 0 都说明哨兵/钳制被绕过）。
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
# 钉 near-lossless（窗 [1,12]）：低端钳到 1 的既有语义。
chk("[5] libx264 --cq 4 → -crf 1（此前是 -crf 0 = 真无损；钉 near-lossless）",
    qq(H, 'libx264', cq=4, src='h264_nvenc', tier='near-lossless'), (1, None, None))
chk("[5] libvpx-vp9 --cq 3 → -crf 1（钉 near-lossless）",
    qq(H, 'libvpx-vp9', cq=3, src='h264_nvenc', tier='near-lossless'), (1, None, None))
chk("[5] --cq-ref 4 同样钳到 1（钉 near-lossless）",
    qq(H, 'libx264', qref=4, tier='near-lossless'), (1, None, None))
chk("[5] 端到端：--codec libx264 --cq 4 --quality-tier near-lossless → -crf 1",
    '-crf 1 ' in cmd_of(run('vidcrop_cpu_v2.py',
                            ['--input', str(SRC), '--output', str(WORK / 'o5'), '--dry-run',
                             '--codec', 'libx264', '--cq', '4',
                             '--quality-tier', 'near-lossless',
                             '--output-width', '640', '--output-height', '360'])[1]) + ' ',
    True)
# R1 读法 A 的正向：默认档（balanced）同样钳制 —— 低端小值全部上钳到 23。
chk("[5] 默认档不豁免：libx264 --cq 4（balanced）→ -crf 23 + 窗口告警",
    (qq(H, 'libx264', cq=4, src='h264_nvenc', tier='balanced'),
     qq(H, 'libvpx-vp9', cq=3, src='h264_nvenc', tier='balanced')),
    ((23, None, None), (31, None, None)))

print('── ⑥ 边界：上限饱和 / .5 舍入锁定（防漂移）──')
chk("[6] hevc_nvenc 的 crf 51 + constqp → -qp 44（CQ 值先被 hi=51 截，再回基准；钉 test）",
    qq(H, 'hevc_nvenc', cref=51, src='hevc_nvenc', rc='constqp', tier='test'), (None, None, 44))
chk("[6] av1_nvenc 的 crf 51 + constqp → -qp 153（CQ 值 57，基准 51 ×3；钉 test）",
    qq(H, 'av1_nvenc', cref=51, src='av1_nvenc', rc='constqp', tier='test'), (None, None, 153))
chk("[6] libx265 的 cq 18（hevc 量纲）→ 13（钉 archive：round(10.5)=10 → 钳到 13）",
    qq(H, 'libx265', cq=18, src='hevc_nvenc', tier='archive'), (13, None, None))
chk("[6] libx265 的 cq 23（hevc 量纲）→ 16（钉 archive：15.5→16 在窗内）",
    qq(H, 'libx265', cq=23, src='hevc_nvenc', tier='archive'), (16, None, None))

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

# ── VE 侧模块 ──────────────────────────────────────────────────────
# 2026-10-06 起本脚本移进 Accessory/verify/，原先的 `ROOT.parent` 已不再指向
# 仓库的父目录。改用 _paths.repo_root() 的**父目录**，不再靠数层级。
VE_ROOT  = ROOT.parent / 'Video_Enhancement'
VE_UTILS = VE_ROOT / 'src' / 'utils'
VE_QM    = None
# ⚠ 找不到 VE 时**硬失败**，不再静默降级为 SKIP。
#   这条判据守的是跨仓共享真源（QUALITY_MAP / QUALITY_MAP_QUALITY 必须逐条相等），
#   静默 SKIP 意味着「两仓表漂移了但门禁照样绿」——本仓已吃过一次假门禁的亏
#   （见 2026-10-06 提交 ee3bfd9）。真要临时跳过，用环境变量显式声明。
if os.environ.get('VU_SKIP_VE_CROSSCHECK') == '1':
    print('  ⚠ VU_SKIP_VE_CROSSCHECK=1 —— 按环境变量要求跳过跨仓比对（本次不算通过）')
elif not ((VE_UTILS / 'quality_map.py').is_file()
          and (VE_UTILS / 'convert_crf.py').is_file()):
    fails.append(f'[9] 找不到 VE 侧模块：{VE_UTILS}\n'
                 f'      跨仓共享真源判据无法执行 —— 两仓的 QUALITY_MAP 会各自漂移而无人发现。\n'
                 f'      确认 Video_Enhancement 在 {ROOT.parent} 下，或显式跳过：'
                 f'VU_SKIP_VE_CROSSCHECK=1')
else:
    try:
        if str(VE_UTILS) not in sys.path:
            sys.path.insert(0, str(VE_UTILS))
        # ⚠ 关键：VE 的 quality_map.py 内部 `from convert_crf import …`。若直接 exec_module，
        #   `convert_crf` 会命中 sys.modules 里 **VU 的**同名缓存（本文件已 `import vidcrop_*`
        #   → 触发 VU convert_crf）⇒ VE_QM 拿到的是 **VU 的表**，跨仓「逐条相等」退化为 VU↔VU、
        #   **从不真正检查 VE**。故临时把 VE 的 convert_crf 以名 'convert_crf' 挂进 sys.modules，
        #   让 VE_QM 绑定到 **VE 的**表/函数，加载完再还原（VU 侧不受影响）。
        _ve_cc_spec = importlib.util.spec_from_file_location('convert_crf',
                                                             VE_UTILS / 'convert_crf.py')
        _ve_cc = importlib.util.module_from_spec(_ve_cc_spec)
        _saved_cc = sys.modules.get('convert_crf')
        sys.modules['convert_crf'] = _ve_cc
        try:
            _ve_cc_spec.loader.exec_module(_ve_cc)
            _spec = importlib.util.spec_from_file_location('ve_quality_map',
                                                           VE_UTILS / 'quality_map.py')
            VE_QM = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(VE_QM)
        finally:
            if _saved_cc is not None:
                sys.modules['convert_crf'] = _saved_cc
            else:
                sys.modules.pop('convert_crf', None)
    except Exception as _exc:                                     # noqa: BLE001
        fails.append(f'[9] VE 侧模块导入失败（跨项目比对未能执行）：{_exc}\n'
                     f'      与「表内容漂移」同等严重 —— 这次门禁并没有真的检查跨仓一致性。')

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
    chk("[9] SIZE_MAP（等体积）与 Video_Enhancement 的 convert_crf 逐条相等",
        VU_CRF.SIZE_MAP, getattr(VE_QM, 'SIZE_MAP', None))
    chk("[9] QUALITY_MAP（等质量）与 Video_Enhancement 的 convert_crf 逐条相等",
        VU_CRF.QUALITY_MAP, getattr(VE_QM, 'QUALITY_MAP', None))
    # 抽样比对换算函数（防"表相同、函数不同"）——两口径各比一遍
    _rt = [(c, r) for c in ('libx264', 'libx265', 'hevc_nvenc', 'libvpx-vp9', 'librav1e')
           for r in (0, 21, 35, 51)]
    VU_CRF.set_quality_mode('size')
    if hasattr(VE_QM, 'set_quality_mode'):
        VE_QM.set_quality_mode('size')
    chk("[9] size 模式下 from_x264_crf 与 VE 逐点相等",
        [VU_CRF.from_x264_crf(c, r) for c, r in _rt],
        [VE_QM.from_x264_crf(c, r) for c, r in _rt])
    VU_CRF.set_quality_mode('quality')
    if hasattr(VE_QM, 'set_quality_mode'):
        VE_QM.set_quality_mode('quality')
    chk("[9] quality 模式下 from_x264_crf 与 VE 逐点相等",
        [VU_CRF.from_x264_crf(c, r) for c, r in _rt],
        [VE_QM.from_x264_crf(c, r) for c, r in _rt])
    # 默认口径 = quality（用全新加载的模块验证，避免受上面 set_quality_mode 影响）
    _fs = importlib.util.spec_from_file_location('vu_crf_fresh', ROOT / 'convert_crf.py')
    _fm = importlib.util.module_from_spec(_fs)
    _fs.loader.exec_module(_fm)
    chk("[9] convert_crf 默认口径 = quality",
        getattr(_fm, 'get_quality_mode', lambda: None)(), 'quality')

    # ── ⑨-eqq：两种口径的差异 + 切回 size 零回归（表的跨项目相等已在上面断言）──
    VU_CRF.set_quality_mode('size')
    if hasattr(VE_QM, 'set_quality_mode'):
        VE_QM.set_quality_mode('size')
    _soft = ('libx265', 'libvpx-vp9', 'libaom-av1', 'libsvtav1', 'librav1e')
    _base = [H.from_x264_crf(c, r) for c, r in _rt]
    _s21 = {c: VU_CRF.from_x264_crf(c, 21) for c in _soft}
    VU_CRF.set_quality_mode('quality')
    if hasattr(VE_QM, 'set_quality_mode'):
        VE_QM.set_quality_mode('quality')
    _q21 = {c: VU_CRF.from_x264_crf(c, 21) for c in _soft}
    VU_CRF.set_quality_mode('size')
    if hasattr(VE_QM, 'set_quality_mode'):
        VE_QM.set_quality_mode('size')
    chk("[9] 切回 size 模式后换算逐字不变（旧行为零回归）",
        [H.from_x264_crf(c, r) for c, r in _rt], _base)
    note('[9-eqq]', '两口径在 crf21 的差异（size→quality）', True,
         '; '.join(f'{c}:{_s21[c]:.2f}→{_q21[c]:.2f}' for c in _soft),
         '—', 'QUALITY_MAP 未覆盖的编码器回退 SIZE_MAP，故未标定项两值相同')

# ── ⑨-3 constqp 轴：QP 到底是 CQ 刻度还是基准轴？───────────────────────
# [V1 已修复] -qp 回基准轴（不再拿 CQ 轴值直发）；AV1 另加 ×4 的 QP 尺度层。
# 判据与 VE 的 to_constqp_qp() 对齐：把 CQ 轴值回算到基准轴即为该编码器的 -qp。
for _c in ('h264_nvenc', 'hevc_nvenc'):
    _cq_v = qq(H, _c, src=_c, cref=21, tier='quality')[1]            # 非 constqp：-cq 值
    _qp_v = qq(H, _c, src=_c, cref=21, rc='constqp', tier='quality')[2]  # constqp：-qp 值
    _exp = (VE_QM.to_constqp_qp(_c, _cq_v) if VE_QM is not None
            else int(round(H.to_x264_crf(_c, _cq_v))))    # 无 VE 时按基准轴手算
    note(f'[9-QP:{_c}]', f'constqp 下 --crf-ref 21 的 -qp（CQ 轴值 {_cq_v}）',
         _qp_v == _exp, f'-qp {_qp_v}', f'-qp {_exp}',
         f'V1：CQ 轴值 {_cq_v} 先 --to_x264_crf--> 基准轴，再取为该编码器的 -qp '
         f'（= VE 的 to_constqp_qp；钉 quality 档）')
# --qp 落到软编：QP 已在基准轴 ⇒ 直接经等效表换算到软编的 CRF。
# R1：钉 archive（18 在该档窗内；默认档会把 18 钳到 23）。
_qp_soft = qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp', tier='archive')[0]
_soft_exp = int(round(H.from_x264_crf('libx265', 18)))
note('[9-QP:soft]', '--qp 18 落到 libx265 的 -crf',
     _qp_soft == _soft_exp, f'-crf {_qp_soft}', f'-crf {_soft_exp}',
     'V2：--qp 是基准轴上的真实 QP ⇒ 18 直接经 libx265 的等效表（size 0.9272x+1.336）→ '
     f'{_soft_exp}（此前按 CQ 轴回算得 14；钉 archive）')

# ── ⑨-4 librav1e：字面量与基准轴走了两条换算链 ──────────────────────────
_lit = opt_of(run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'librav1e', '--crf', '21',
                   '--quality-tier', 'quality',
                   '--output-width', '640', '--output-height', '360'])[1], '-qp')
_ref = opt_of(run('vidcrop_cpu_v2.py',
                  ['--input', str(SRC), '--output', str(WORK / 'o9'), '--dry-run',
                   '--codec', 'librav1e', '--crf-ref', '21',
                   '--quality-tier', 'quality',
                   '--output-width', '640', '--output-height', '360'])[1], '-qp')
note('[9-rav1e]', '--crf 21 与 --crf-ref 21 在 librav1e 上同源（钉 quality）',
     _lit == _ref, f'--crf→-qp {_lit} / --crf-ref→-qp {_ref}', '两者相同（均为 66）',
     '[V8 已修复] librav1e 移出 CRF_SUPPORTED_CODECS，字面量与 -ref 都先归一到基准轴，'
     "再由 crf_to_rav1e_qp 查 SIZE_MAP['librav1e'] 的**等体积（native 档）**标定值得 66。"
     '⚠ 2026-09-30 起 crf_to_rav1e_qp 不再经 libaom 中转（旧链式给 64，且会随 '
     'libaom 行重标而漂移），改为直接查表，两仓口径统一')

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
     'R1：DEFAULT_REF 语义迁移为「默认档 balanced 锚点」= 24（size 口径：libx264 24 / '
     'libx265 24 / h264_nvenc 29 / hevc_nvenc 32）——回基准与锚点相等仍成立。'
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
_vp9 = VU_CRF.SIZE_MAP['libvpx-vp9']
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
_hvt = VU_CRF.SIZE_MAP['hevc_videotoolbox']
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
_av1_hi = H.SIZE_MAP.get('av1_nvenc', (0,0,0,0))[3]
chk("[10] SIZE_MAP['av1_nvenc'] hi == 63（NVIDIA 规格 0~63）", _av1_hi, 63)
# 运行时校验：literal_range 返回 ffmpeg 实际量程（本机 ffmpeg 报 0~63，符合 V3 修正）
_rt_cq = H.literal_range('av1_nvenc', 'cq')
chk("[10] literal_range('av1_nvenc','cq') 返回 ffmpeg 实际量程", _rt_cq, (0, 63))
chk("[10] crf_ref 51 → av1_nvenc -cq 57（此前被 hi=51 截到 51；钉 test）",
    qq(H, 'av1_nvenc', cref=51, tier='test'), (None, 57, None))
chk("[10] crf_ref 40 → av1_nvenc -cq 46（常用区不受影响；钉 draft）",
    qq(H, 'av1_nvenc', cref=40, tier='draft'), (None, 46, None))

print('  [V4] literal_range：字面量按生效编码器量程（运行时查询 ffmpeg 实际范围）')
# 期望值基于本机 ffmpeg 的实际报告值
for _c, _k, _want in (('libx264', 'crf', (0, 51)), ('libx265', 'crf', (0, 51)),
                      ('libvpx-vp9', 'crf', (-1, 63)), ('libsvtav1', 'crf', (0, 63)),
                      ('libaom-av1', 'crf', (-1, 63)), ('h264_nvenc', 'cq', (0, 51)),
                      ('av1_nvenc', 'cq', (0, 63)),   # 本机 ffmpeg 报 0~63（V3 修正）
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
for _c, _kw, _want in (('h264_vaapi', {'cq': 26, 'tier': 'quality'}, 21),   # 26-5=21，钉 quality
                       ('h264_vaapi', {'crf': 21, 'tier': 'quality'}, 21),
                       ('hevc_vaapi', {'cq': 26, 'tier': 'quality'}, 21),
                       ('hevc_vaapi', {'qref': 26, 'tier': 'quality'}, 21),  # 经 h264_nvenc 归一
                       ('h264_vaapi', {'cref': 30, 'tier': 'preview'}, 30),
                       ('h264_vaapi', {'qp': 23, 'rc': 'constqp', 'tier': 'balanced'}, 23),
                       ('h264_vaapi', {'cq': 0, 'tier': 'balanced'}, 0)):    # 0 档保持 0
    chk(f"[10] {_c} {_kw} → -qp {_want}", qq(H, _c, **_kw), (None, None, _want))
_va = cmd_of(run('vidcrop_cpu_v2.py',
                 ['--input', str(SRC), '--output', str(WORK / 'o10'), '--dry-run',
                  '--codec', 'h264_vaapi', '--cq', '26',
                  '--output-width', '640', '--output-height', '360'])[1])
# R1 读法 A：默认档 balanced 下 --cq 26 归一到 21 → 被窗口上钳到 23 ⇒ -qp 23 + 告警。
chk_in("[10] VAAPI 命令含 -qp 23（默认档钳制后的落值；此前静默无任何质量参数）", '-qp 23', _va)
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

print('  [V1] constqp 轴：-qp 回基准轴（含 AV1 的 ×3 QP 尺度，L40 扩扫确认）')
chk("[11] qp_scale：AV1 族 3、其余 1",
    [H.qp_scale(c) for c in ('av1_nvenc', 'librav1e', 'h264_nvenc', 'libx265')],
    [3, 4, 1, 1])
chk("[11] qp_limits('av1_nvenc') == (0,255)，≠ CQ 轴规格量程 (0,63)（本机 ffmpeg 报 0~63）",
    (H.qp_limits('av1_nvenc'), H.literal_range('av1_nvenc', 'cq')), ((0, 255), (0, 63)))
chk("[11] to_constqp_qp('h264_nvenc', 26) == 21（对齐 VE 的 to_constqp_qp）",
    H.to_constqp_qp('h264_nvenc', 26), 21)
chk("[11] to_constqp_qp('hevc_nvenc', 28) == 20（20.5 银行家舍入）",
    H.to_constqp_qp('hevc_nvenc', 28), 20)
chk("[11] to_constqp_qp('av1_nvenc', 27) == 63（size 口径：(27−6)=21 基准 ×3）",
    H.to_constqp_qp('av1_nvenc', 27), 63)
chk("[11] from_constqp_qp('av1_nvenc', 63) == 21（size 口径反向自洽）",
    H.from_constqp_qp('av1_nvenc', 63), 21.0)
chk("[11] to_constqp_qp(c, 0) 恒 0（V14/A6 无损守卫；不依赖 _QP_LIMITS clamp）",
    [H.to_constqp_qp(c, 0) for c in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
                                     'librav1e', 'libsvtav1', 'libx264')],
    [0, 0, 0, 0, 0, 0])
# ── CR-4：quality 口径下 NVENC 的 -qp 与 VE `QUALITY_MAP_QP` 同源（h264/hevc/av1）────
#    av1：L40 17 素材实测等质 QP 中位 65.7/92.3/117.9/140.8（ref 21/24/27/30）⇒ 仿射
#    7.9338·ref−97.5136（残差 ≤3.4）；过原点 ×3 只在 ref≈21 成立；h264/hevc 取 VE 的 QP 行。
#    判据锚点口径 = 生产工作区间 [0,27]（见 memory/feedback_loo_gate_anchor_range）；crf>27 仅监控。
_prev_mode = H.get_quality_mode()
H.set_quality_mode('quality')
C.set_quality_mode('quality')
if VE_QM is not None and hasattr(VE_QM, 'set_quality_mode'):
    VE_QM.set_quality_mode('quality')
_cq21 = int(round(H.from_x264_crf('av1_nvenc', 21)))   # quality 口径 ref21 的 CQ 值（≈32）
chk("[11] quality 口径 av1 crf-ref21 → -qp = 70（仿射，≠ size ×3 的 63）",
    H.to_constqp_qp('av1_nvenc', _cq21), 70)
chk("[11] quality 口径 from_constqp_qp('av1_nvenc', 70) ≈ 21.11（仿射反解）",
    round(H.from_constqp_qp('av1_nvenc', 70), 2), 21.11)
if VE_QM is not None:
    _qp_strict, _qp_mon = [], []
    for _c in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc'):
        for _ref in range(0, 52):
            _v = H.from_x264_crf(_c, _ref)
            if _v is None:
                continue
            _vv = int(round(_v))
            _a, _cc, _e = (H.to_constqp_qp(_c, _vv), C.to_constqp_qp(_c, _vv),
                           VE_QM.to_constqp_qp(_c, _vv))
            if _a == _cc == _e:
                continue
            (_qp_strict if _ref <= 27 else _qp_mon).append((_c, _ref, _a, _cc, _e))
    chk("[11] CR-4：quality 口径 VU↔VE 的 -qp 在 ref∈[0,27] 逐点相等（h264/hevc/av1）",
        _qp_strict, [])
    print(f'  [监控] quality QP 在 ref 28..51 ' +
          (f'不一致（不计 FAIL）：{_qp_mon[:6]}' + (' …' if len(_qp_mon) > 6 else '')
           if _qp_mon else '亦全等（监控项）'))
else:
    print('  ℹ 无 VE 仓，CR-4 跨仓 QP 一致性断言跳过')
H.set_quality_mode(_prev_mode)
C.set_quality_mode(_prev_mode)
if VE_QM is not None and hasattr(VE_QM, 'set_quality_mode'):
    VE_QM.set_quality_mode(_prev_mode)
chk("[11] 两脚本 to_constqp_qp 逐点相等",
    [H.to_constqp_qp(c, v) for c in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc', 'librav1e')
     for v in (0, 18, 26, 40)],
    [C.to_constqp_qp(c, v) for c in ('h264_nvenc', 'hevc_nvenc', 'av1_nvenc', 'librav1e')
     for v in (0, 18, 26, 40)])
chk("[11] qp_range 按编码器取",
    (H.qp_range('av1_nvenc'), H.qp_range('h264_vaapi'), H.qp_range('libx264')),
    ((0, 255), (0, 52), (0, 51)))

print('  [V2] --qp 落到软编按基准轴回算（不再走 CQ 轴）')
chk("[11] libx265：--qp 18 → -crf 18（新表），不是 CQ 轴的 14（钉 archive）",
    qq(H, 'libx265', src='hevc_nvenc', qp=18, rc='constqp', tier='archive'), (18, None, None))
chk("[11] 端到端 cpu_v2：libx265 + constqp --qp 18 --quality-tier archive → -crf 18",
    '-crf 18' in cmd_of(run('vidcrop_cpu_v2.py',
                            ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                             '--codec', 'libx265', '--rc-mode', 'constqp', '--qp', '18',
                             '--quality-tier', 'archive',
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

print('  [V7] 默认质量统一到 B1 基准（R1：DEFAULT_REF = 默认档 balanced 锚点 24）')
chk("[11] DEFAULT_REF=24（两脚本）且 = tier_anchor(默认档)",
    (H.DEFAULT_REF, C.DEFAULT_REF,
     H.DEFAULT_REF == H.tier_anchor(H.DEFAULT_QUALITY_TIER)), (24, 24, True))
chk("[11] default_quality_for（size 口径）：libx264/24、libx265/24、h264_nvenc/29、hevc_nvenc/32",
    [C.default_quality_for(c) for c in ('libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc')],
    [24, 24, 29, 32])
for _codec, _opt, _val in (('libx264', '-crf', '24'), ('libx265', '-crf', '24'),
                           ('h264_nvenc', '-cq', '29'), ('hevc_nvenc', '-cq', '32')):
    _cmd = cmd_of(run('vidcrop_cpu_v2.py',
                      ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                       '--codec', _codec,
                       '--output-width', '640', '--output-height', '360'])[1])
    chk_in(f"[11] 未给质量：{_codec} → {_opt} {_val}", f'{_opt} {_val}', _cmd)

print('  [V8] librav1e 单链')
chk("[11] librav1e 不在 CRF_SUPPORTED_CODECS（两脚本）",
    ('librav1e' in H.CRF_SUPPORTED_CODECS, 'librav1e' in C.CRF_SUPPORTED_CODECS),
    (False, False))
chk("[11] librav1e 字面量 --crf 21 → -qp 66（查表口径，native 档；钉 quality）",
    '-qp 66' in cmd_of(run('vidcrop_cpu_v2.py',
                           ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                            '--codec', 'librav1e', '--crf', '21',
                            '--quality-tier', 'quality',
                            '--output-width', '640', '--output-height', '360'])[1]), True)
chk("[11] librav1e --crf-ref 21 → -qp 66（与字面量同源；钉 quality）",
    '-qp 66' in cmd_of(run('vidcrop_cpu_v2.py',
                           ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                            '--codec', 'librav1e', '--crf-ref', '21',
                            '--quality-tier', 'quality',
                            '--output-width', '640', '--output-height', '360'])[1]), True)
_r1e_cmd = cmd_of(run('vidcrop_cpu_v2.py',
                     ['--input', str(SRC), '--output', str(WORK / 'o11'), '--dry-run',
                      '--codec', 'librav1e', '--crf', '21',
                      '--output-width', '640', '--output-height', '360'])[1])
# R1（§13.10R）：显式档值 6 与 rav1e native 默认同值（逐字节等价），写成显式只为
# 可复现/消歧；tier=None 的直连调用保持旧行为（不下发）。
chk("[11] librav1e 默认档（balanced）显式下发 -speed（档值 6 = native）",
    '-speed 6' in _r1e_cmd, True)
_b_none = ' '.join(C.build_encoder_options_v2('librav1e', 21, None, lambda _m: None))
_b_bal = ' '.join(C.build_encoder_options_v2('librav1e', 21, None, lambda _m: None,
                                             tier='balanced'))
chk("[11] librav1e：tier=None 不下发 -speed / tier=balanced 下发 6",
    ('-speed' in _b_none, '-speed 6' in _b_bal), (False, True))

print('  [V10] preset 表')
chk("[11] 两脚本 X264_TO_NVENC_PRESET 相等", H.X264_TO_NVENC_PRESET, C.X264_TO_NVENC_PRESET)
chk("[11] superfast→p1 / medium→p4（官方枚举，与 VE 对齐）",
    (H.X264_TO_NVENC_PRESET['superfast'], H.X264_TO_NVENC_PRESET['medium']), ('p1', 'p4'))
chk("[11] svtav1：fast≠medium，且 p7==veryslow",
    (H.X264_TO_SVTAV1_PRESET['fast'] != H.X264_TO_SVTAV1_PRESET['medium'],
     H.NVENC_TO_SVTAV1_PRESET['p7'] == H.X264_TO_SVTAV1_PRESET['veryslow']), (True, True))
chk("[11] libsvtav1 默认 preset 固定为 8（不随核数漂移）", H.default_preset_for('libsvtav1'), '8')

print('  [V11] hevc_videotoolbox 量程')
chk("[11] hevc_videotoolbox b == 100", VU_CRF.SIZE_MAP['hevc_videotoolbox'][1], 100.0)

print('  [12] HDR 域覆盖表（SDR→HDR 管线专用；默认 sdr = 零回归）')
# 2026-10-16：convert_sdr_to_hdr 的等质量换算改走 HDR 域（HDR 10bit PQ 域 PSNR
# 等质量标定，覆盖 libsvtav1/libaom-av1/librav1e 三行）。QUALITY_MAP/SIZE_MAP
# 本体与跨仓契约（[9] 组断言逐条相等）不动。libsvtav1 / libaom-av1 为分段模型
# （PiecewiseLinear，2026-10-16 §9/§10 升级）；期望值取自标定产物
# memory/hdr_equal_quality_calibration_10mat_20261016.json（.codecs.*.fit /
# .codecs.libsvtav1.piecewise.knots_cuminc_old / .codecs.libaom-av1.piecewise.knots_cuminc），
# 独立于 convert_crf.py 的落表值（防"表错、测试跟着错"）。
_12_mode = VU_CRF.get_quality_mode()
chk('[12] 默认域 = sdr', VU_CRF.get_quality_domain(), 'sdr')
_fs3 = importlib.util.spec_from_file_location('vu_crf_fresh2', ROOT / 'convert_crf.py')
_fm3 = importlib.util.module_from_spec(_fs3)
_fs3.loader.exec_module(_fm3)
chk('[12] 全新加载的默认域也是 sdr（零回归）', _fm3.get_quality_domain(), 'sdr')
VU_CRF.set_quality_mode('quality')
_sdr21 = [round(VU_CRF.from_x264_crf(c, 21), 2) for c in
          ('libsvtav1', 'libaom-av1', 'librav1e')]
_sdr_ctrl = [VU_CRF.from_x264_crf(c, 21) for c in ('libx265', 'libvpx-vp9', 'hevc_nvenc')]
chk('[12] sdr 域 @21（QUALITY_MAP 旧值）', _sdr21, [28.96, 26.37, 64.38])
VU_CRF.set_quality_domain('hdr')
chk('[12] hdr 域 @21（HDR EQ 标定；§10 后 svtav1 候选 B 不变、aom 分段、rav1e 新仿射）',
    [round(VU_CRF.from_x264_crf(c, 21), 2) for c in
     ('libsvtav1', 'libaom-av1', 'librav1e')],
    [22.77, 31.11, 73.66])
chk('[12] hdr 域 @30（锚点斜率锁定；svtav1 候选 B 不变 50.74）',
    [round(VU_CRF.from_x264_crf(c, 30), 2) for c in
     ('libsvtav1', 'libaom-av1', 'librav1e')],
    [50.74, 52.77, 142.77])
# [12]-P：svtav1 / aom 分段模型专项（svtav1 §9 升级、§10 前置 ref15；aom §10 升级）
_pw = VU_CRF.HDR_EQUAL_QUALITY_OVERRIDE['libsvtav1']
_pw_aom = VU_CRF.HDR_EQUAL_QUALITY_OVERRIDE['libaom-av1']
chk('[12] libsvtav1 行为 PiecewiseLinear（分段升级）',
    isinstance(_pw, VU_CRF.PiecewiseLinear), True)
chk('[12] aom 亦为 PiecewiseLinear（§10）；rav1e 保持仿射元组（升级范围受控）',
    (isinstance(_pw_aom, VU_CRF.PiecewiseLinear),
     type(VU_CRF.HDR_EQUAL_QUALITY_OVERRIDE['librav1e']) is tuple), (True, True))
chk('[12] svtav1 分段节点（候选 B：旧 18~30 + 前置 ref15；对照 json .piecewise.knots_cuminc_old）',
    ([x for x, _ in _pw.knots], [round(y, 4) for _, y in _pw.knots]),
    ([15.0, 18.0, 21.0, 24.0, 27.0, 30.0],
     [5.9812, 12.5065, 22.7691, 32.9637, 43.037, 50.7369]))
chk('[12] aom 分段节点（候选 A 稳健累积；对照 json .piecewise.knots_cuminc）',
    ([x for x, _ in _pw_aom.knots], [round(y, 4) for _, y in _pw_aom.knots]),
    ([15.0, 18.0, 21.0, 24.0, 27.0, 30.0],
     [14.4219, 23.151, 31.1133, 38.5473, 46.0841, 52.7733]))
_kx = [x for x, _ in _pw.knots]
_ky = [y for _, y in _pw.knots]
chk('[12] 段间线性插值（18/21 中点 = 两端点均值）',
    round(VU_CRF.from_x264_crf('libsvtav1', 19.5), 6), round((_ky[1] + _ky[2]) / 2, 6))
chk('[12] ref15 = 首节点值（实测锚点，不再是外推）',
    round(VU_CRF.from_x264_crf('libsvtav1', 15), 6), _ky[0])
chk('[12] 首段斜率外推后钳位（ref12 < lo → 0）',
    VU_CRF.from_x264_crf('libsvtav1', 12), 0.0)
chk('[12] 高端端段斜率外推后钳位（ref63 = hi）',
    VU_CRF.from_x264_crf('libsvtav1', 63), 63.0)
chk('[12] 反解往返（y@18 → 18）',
    round(VU_CRF.to_x264_crf('libsvtav1', _ky[1]), 6), 18.0)
chk('[12] aom 反解往返（y@21 → 21）',
    round(VU_CRF.to_x264_crf('libaom-av1', _pw_aom.knots[2][1]), 6), 21.0)
chk('[12] row_limits 双形态兼容',
    (VU_CRF.row_limits(_pw), VU_CRF.row_limits(VU_CRF.HDR_EQUAL_QUALITY_OVERRIDE['librav1e'])),
    ((0, 63), (0, 255)))
chk('[12] 生产下发整数（round）：svtav1 @15/@16/@17/@18→6/8/10/13（新锚区）、@21→23、@30→51',
    [int(round(VU_CRF.from_x264_crf('libsvtav1', r))) for r in (15, 16, 17, 18, 21, 30)],
    [6, 8, 10, 13, 23, 51])
chk('[12] 生产下发整数：aom @21→31、@30→53（旧仿射 54）；rav1e @21→74（旧 71，新仿射）',
    [int(round(VU_CRF.from_x264_crf(c, r))) for c, r in
     (('libaom-av1', 21), ('libaom-av1', 30), ('librav1e', 21))], [31, 53, 74])
chk('[12] 分段模型单调不减（ref 12→33 下发整数）',
    all(int(round(VU_CRF.from_x264_crf('libsvtav1', r)))
        <= int(round(VU_CRF.from_x264_crf('libsvtav1', r + 1)))
        for r in range(12, 33)), True)
try:
    VU_CRF.PiecewiseLinear(((18, 10), (18, 12)), 0, 63)
    chk('[12] 非法节点（x 不递增）应报 ValueError', '没报错', 'ValueError')
except ValueError:
    pass
chk('[12] 覆盖只动三行（libx265 / libvpx-vp9 / hevc_nvenc 逐位不变）',
    [VU_CRF.from_x264_crf(c, 21) for c in ('libx265', 'libvpx-vp9', 'hevc_nvenc')],
    _sdr_ctrl)
VU_CRF.set_quality_mode('size')
_sz_hdr = [VU_CRF.from_x264_crf(c, 21) for c in ('libsvtav1', 'libaom-av1', 'librav1e')]
VU_CRF.set_quality_domain('sdr')
chk('[12] size 口径下 hdr 域不生效（SIZE_MAP 原样）',
    _sz_hdr, [VU_CRF.from_x264_crf(c, 21) for c in ('libsvtav1', 'libaom-av1', 'librav1e')])
VU_CRF.set_quality_domain('hdr')
VU_CRF.set_quality_mode('quality')
chk('[12] 先域后口径切换：覆盖仍生效（无顺序依赖）',
    round(VU_CRF.from_x264_crf('libsvtav1', 21), 2), 22.77)
chk('[12] QUALITY_MAP 本体未被修改（跨仓契约安全）',
    (VU_CRF.QUALITY_MAP['libsvtav1'], VU_CRF.QUALITY_MAP['libaom-av1'],
     VU_CRF.QUALITY_MAP['librav1e']),
    ((2.3961, -21.3615, 0, 63), (2.3219, -22.3927, 0, 63), (7.9326, -102.2078, 0, 255)))
try:
    VU_CRF.set_quality_domain('hdr2')
    chk('[12] 非法域名报错', '没报错', 'ValueError')
except ValueError:
    pass
VU_CRF.set_quality_domain('sdr')
VU_CRF.set_quality_mode(_12_mode)

# ═══════════════════════════════════════════════════════════════════
# ⑬ R1 quality-tier（阶段一 DoD）：档表 / 锚点 / 窗口 / 哨兵 / 标定告警 / 速度·rc 轴
# ═══════════════════════════════════════════════════════════════════
print('── ⑬ R1 quality-tier 核心语义（阶段一 DoD）──')

# ── ⑬-1 档表结构自检（§6.2-420：八档覆盖 [0,51] + 互斥 + preset 合法）────────
_tiers = list(VU_CRF.QUALITY_TIERS)
chk('[13] 八档齐全（松→紧）', _tiers,
    ['lossless', 'near-lossless', 'archive', 'quality', 'balanced', 'preview', 'draft', 'test'])
_wins = sorted((VU_CRF.tier_window(t)[0], VU_CRF.tier_window(t)[1], t) for t in _tiers)
chk('[13] 窗口从 0 起、到 51 止、相邻无缝隙（⇒ 天然互斥）',
    (_wins[0][0], _wins[-1][1],
     all(_wins[i][1] + 1 == _wins[i + 1][0] for i in range(len(_wins) - 1))),
    (0, 51, True))
chk('[13] 各档锚点落在本档窗内',
    [VU_CRF.tier_window(t)[0] <= VU_CRF.tier_anchor(t) <= VU_CRF.tier_window(t)[1]
     for t in _tiers], [True] * 8)
_t9 = ('libx264', 'libx265', 'libvpx-vp9', 'libsvtav1', 'libaom-av1', 'librav1e',
       'h264_nvenc', 'hevc_nvenc', 'av1_nvenc')
chk('[13] 每档九个编码器键齐全（无多余键）',
    [sorted((VU_CRF.TIER_PROFILES[t].get('preset') or {})) == sorted(_t9) for t in _tiers],
    [True] * 8)
_bad_p = [(t, c, (VU_CRF.TIER_PROFILES[t].get('preset') or {}).get(c))
          for t in _tiers for c in _t9
          if not isinstance((VU_CRF.TIER_PROFILES[t].get('preset') or {}).get(c), str)]
chk('[13] 每档 preset 值全为非空字符串', _bad_p, [])
_bad_ax = [(t, k, VU_CRF.TIER_PROFILES[t].get(k)) for t in _tiers
           for k, ok in (('tune', lambda v: v in (None, 'll', 'uhq')),
                         ('lookahead', lambda v: v is None or (isinstance(v, int) and v > 0)),
                         ('multipass', lambda v: v in (None, 'qres', 'fullres', 'disabled')),
                         ('aq', lambda v: isinstance(v, bool)))
           if not ok(VU_CRF.TIER_PROFILES[t].get(k))]
chk('[13] tune/lookahead/multipass/aq 取值域合法', _bad_ax, [])

# ── ⑬-2 clamp / suggest 助手 ────────────────────────────────────────────────
_bad_c = []
for t in _tiers:
    lo, hi = VU_CRF.tier_window(t)
    if VU_CRF.clamp_ref_to_tier(t, lo) != (lo, False):
        _bad_c.append((t, 'lo', VU_CRF.clamp_ref_to_tier(t, lo)))
    if VU_CRF.clamp_ref_to_tier(t, hi) != (hi, False):
        _bad_c.append((t, 'hi', VU_CRF.clamp_ref_to_tier(t, hi)))
    if lo > 0 and VU_CRF.clamp_ref_to_tier(t, lo - 1) != (lo, True):
        _bad_c.append((t, 'below', VU_CRF.clamp_ref_to_tier(t, lo - 1)))
    if hi < 51 and VU_CRF.clamp_ref_to_tier(t, hi + 1) != (hi, True):
        _bad_c.append((t, 'above', VU_CRF.clamp_ref_to_tier(t, hi + 1)))
chk('[13] clamp：窗内原样不改位、窗外钳到边界并置位', _bad_c, [])
_own = {}
for t in _tiers:
    _lo, _hi = VU_CRF.tier_window(t)
    for _v in range(_lo, _hi + 1):
        _own[_v] = t
chk('[13] tier_suggest_for(0..51) 恰好归属唯一档',
    [VU_CRF.tier_suggest_for(v) for v in range(52)], [_own[v] for v in range(52)])

# ── ⑬-3 标定指纹与告警（H4：只由 CALIB_FINGERPRINT 判定）────────────────────
chk('[13] CALIB_FINGERPRINT 键 = 八档', sorted(VU_CRF.CALIB_FINGERPRINT), sorted(_tiers))
chk('[13] 阶段一仅 balanced 标定（其余档待标定产）',
    [t for t in _tiers if VU_CRF.tier_is_calibrated(t)], ['balanced'])
chk('[13] balanced + 无偏离 ⇒ 无告警（组合未偏离）',
    VU_CRF.tier_calib_notice('balanced', 'libx264'), None)
chk_in('[13] balanced + 组合偏离 ⇒ 逐字告警且含偏离项',
       '⚠ 当前组合偏离 --quality-tier balanced 的标定点（--preset slow ≠ 档值 p4）',
       VU_CRF.tier_calib_notice('balanced', 'hevc_nvenc', diff='--preset slow ≠ 档值 p4') or '')
chk_in('[13] 未标定档 ⇒ 固定未标定句（不看表键存在性）',
       VU_CRF.CALIB_UNCALIB_DIFF, VU_CRF.tier_calib_notice('archive', 'libx265') or '')

# ── ⑬-4 锚点注入（无质量输入 ⇒ 本档锚点；换算提示静音）────────────────────
chk('[13] 八档锚点注入（libx264 = 锚点原文）',
    [(t, qq(H, 'libx264', tier=t)[0]) for t in _tiers],
    [('lossless', 0), ('near-lossless', 6), ('archive', 16), ('quality', 20),
     ('balanced', 24), ('preview', 28), ('draft', 36), ('test', 45)])
chk('[13] 锚点注入落目标编码器（libx265，size 口径换算）',
    [(t, qq(H, 'libx265', tier=t)[0])
     for t in ('near-lossless', 'archive', 'quality', 'preview', 'draft', 'test')],
    [('near-lossless', 7), ('archive', 16), ('quality', 20),
     ('preview', 27), ('draft', 35), ('test', 43)])
_buf13 = io.StringIO()
with contextlib.redirect_stdout(_buf13):
    H._resolve_quality_params('libx265', None, None, src_codec=None, crf_ref=None,
                              cq_ref=None, qp=None, rc_mode='auto', tier='quality')
chk('[13] 锚点注入静音三处换算提示（libx265 不刷屏）', _buf13.getvalue().strip(), '')

# ── ⑬-5 0 哨兵（M1）────────────────────────────────────────────────────────
chk('[13] 原始输入 0（--crf 0 + lossless）⇒ 0 哨兵、保留原文通道',
    qq(H, 'libx264', crf=0, tier='lossless'), (0, None, None))
chk('[13] 非 0 归一后被窗口钳到 0（--crf 5 + lossless）⇒ 0 哨兵',
    qq(H, 'libx264', crf=5, tier='lossless'), (0, None, None))
chk('[13] 钳到 0 时按目标能力走通道（hevc_nvenc --cq 5 + lossless ⇒ -cq 0）',
    qq(H, 'hevc_nvenc', cq=5, tier='lossless'), (None, 0, None))
chk('[13] 表内钳制反解到 0.0 不算无损（hevc --cq 5 + near-lossless ⇒ 落 1 → -cq 8）',
    qq(H, 'hevc_nvenc', cq=5, tier='near-lossless'), (None, 8, None))

# ── ⑬-6 §4.4 五样例（**生产默认 quality 口径**，逐字告警）───────────────────
def run_q(script, args, cpu_only=False):
    """默认口径（quality）跑一遍：五样例的期望值按生产默认口径写。"""
    base = [sys.executable, str(ROOT / script)]
    if cpu_only:
        base += ['--decode', 'cpu', '--scale-algo', 'libswscale-lanczos']
    p = subprocess.run(base + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def _t5(tier, qarg, qv):
    return ['--input', str(SRC), '--output', str(WORK / 'o13'), '--dry-run',
            '--codec', 'libx265', '--quality-tier', tier, qarg, qv,
            '--output-width', '640', '--output-height', '360']


_w13 = ('⚠ --quality-tier archive 的质量窗口为 CRF 13~18；--crf 50（基准轴 ≈47.6）'
        '已钳到 18（libx265 -crf 17）。CRF 48 落在 test 档（41~51），'
        '请改用 --quality-tier test')
_r13, _o13 = run_q('vidcrop_cpu_v2.py', _t5('archive', '--crf', '50'))
chk('[13] 五样例 1：rc=0 且落 -crf 17', (_r13, '-crf 17' in cmd_of(_o13)), (0, True))
chk_in('[13] 五样例 1：窗口告警逐字（含建议 test）', _w13, _o13)
_r13b, _o13b = run_q('vidcrop_hwaccel.py', _t5('archive', '--crf', '50'), cpu_only=True)
chk_in('[13] 五样例 1：告警两脚本逐字一致（hwaccel）', _w13, _o13b)
_r13, _o13 = run_q('vidcrop_cpu_v2.py', _t5('quality', '--crf-ref', '26'))
chk_in('[13] 五样例 2：--crf-ref 26 + quality ⇒ -crf 22 + 建议 balanced',
       '⚠ --quality-tier quality 的质量窗口为 CRF 19~22；--crf-ref 26（基准轴 ≈26.0）'
       '已钳到 22（libx265 -crf 22）。CRF 26 落在 balanced 档（23~26），'
       '请改用 --quality-tier balanced', _o13)
_r13, _o13 = run_q('vidcrop_cpu_v2.py', _t5('preview', '--crf-ref', '30'))
chk('[13] 五样例 3：--crf-ref 30 + preview ⇒ -crf 31、无窗口告警',
    ('-crf 31' in cmd_of(_o13), '⚠ --quality-tier' in _o13), (True, False))
_r13, _o13 = run_q('vidcrop_cpu_v2.py', _t5('archive', '--crf-ref', '16'))
chk('[13] 五样例 4：--crf-ref 16 + archive ⇒ -crf 15、无窗口告警',
    ('-crf 15' in cmd_of(_o13), '⚠ --quality-tier' in _o13), (True, False))
_r13, _o13 = run_q('vidcrop_cpu_v2.py', _t5('balanced', '--crf', '50'))
chk('[13] 五样例 5：--crf 50 + balanced ⇒ 钳到 26（不豁免默认档）',
    ('-crf 26' in cmd_of(_o13), 'CRF 48 落在 test 档（41~51）' in _o13), (True, True))

# ── ⑬-7 rc 轴注入（直调 build；无 GPU 也能验证注入逻辑）────────────────────
def _cv13(codec, **kw):
    warns13 = []
    _cmd = C.build_ffmpeg_cmd(SRC, WORK / 'o13.mp4', 640, 360, 1920, 1080, 'crop', codec,
                              kw.pop('crf', None), kw.pop('cq', None), 'medium',
                              kw.pop('pix_fmt', None), 0, True, 'copy', '128k',
                              kw.pop('extra_args', []), warns13.append, **kw)
    return ' '.join(_cmd)


def _hw13(codec, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        _cmd = H.build_ffmpeg_cmd(SRC, WORK / 'o13.mp4', 'crop=640:360:640:360', codec,
                                  kw.pop('crf', None), kw.pop('cq', None), 'medium', True, **kw)
    return ' '.join(_cmd)


_a16 = _cv13('hevc_nvenc', cq=16, tier='archive')
chk('[13] cpu_v2 rc 轴：archive 注入 lookahead+multipass；不注入 tune/aq（§8-2 不对称）',
    ('-rc-lookahead 250' in _a16, '-multipass fullres' in _a16,
     '-tune' in _a16, '-spatial-aq' in _a16), (True, True, False, False))
_b24 = _cv13('hevc_nvenc', cq=24, tier='balanced')
chk('[13] cpu_v2 rc 轴：balanced 一条不注入',
    ('-rc-lookahead' in _b24, '-multipass' in _b24), (False, False))
_z16 = _cv13('hevc_nvenc', cq=0, tier='archive')
chk('[13] cpu_v2 rc 轴：质量归零（cq 0 哨兵）时不注入',
    ('-rc-lookahead' in _z16, '-multipass' in _z16), (False, False))
_h16 = _hw13('hevc_nvenc', cq=16, tier='archive')
chk('[13] hwaccel rc 轴：archive 注入 lookahead+multipass+aq；tune 由调用方派生（此处不出现）',
    ('-rc-lookahead 250' in _h16, '-multipass fullres' in _h16,
     '-spatial-aq 1' in _h16, '-temporal-aq 1' in _h16, '-tune' in _h16),
    (True, True, True, True, False))
_h16t = _hw13('hevc_nvenc', cq=16, tier='archive', nvenc_tune='uhq')
chk('[13] hwaccel：tune 派生值经参数传入后原样下发（-tune uhq）',
    '-tune uhq' in _h16t, True)
_hpv = _hw13('hevc_nvenc', cq=28, tier='preview')
chk('[13] hwaccel rc 轴：preview 注入 multipass disabled（无 lookahead）',
    ('-multipass disabled' in _hpv, '-rc-lookahead' in _hpv), (True, False))
chk('[13] hwaccel：tune 派生（archive→uhq / balanced→None）；cpu_v2 不派生（§8-2）',
    (H._strategy_tune_for('hevc_nvenc', 'archive', None, False),
     H._strategy_tune_for('hevc_nvenc', 'balanced', None, False),
     hasattr(C, '_strategy_tune_for')), ('uhq', None, False))

# ── ⑬-8 速度轴（§13.10R）：aom/vp9 按档钉值、--extra-args 优先 ─────────────
_aom_b = _cv13('libaom-av1', crf=24, tier='balanced')
_aom_n = _cv13('libaom-av1', crf=24)
chk('[13] aom 速度轴：balanced 钉 -cpu-used 6；tier=None 落旧自动值（本机核数）',
    ('-cpu-used 6' in _aom_b, f'-cpu-used {C.auto_effort()[0]}' in _aom_n), (True, True))
_vp9_b = _cv13('libvpx-vp9', crf=24, tier='balanced')
_vp9_n = _cv13('libvpx-vp9', crf=24)
chk('[13] vp9 速度轴：balanced 新显式下发 2；tier=None 保持不主动下发',
    ('-cpu-used 2' in _vp9_b, '-cpu-used' in _vp9_n), (True, False))
_vp9_x = _cv13('libvpx-vp9', crf=24, tier='balanced', extra_args=['-cpu-used', '4'])
chk('[13] aom/vp9 速度轴尊重 --extra-args 显式值（原样保留、不叠加档值）',
    ('-cpu-used 4' in _vp9_x, '-cpu-used 2' in _vp9_x), (True, False))

# ── ⑬-9 档值 preset / tune（§4.1 = §13.10R）────────────────────────────────
chk('[13] 档值 preset 周换：libx265 archive/quality/balanced = slower/slow/medium',
    [H.default_preset_for('libx265', t) for t in ('archive', 'quality', 'balanced')],
    ['slower', 'slow', 'medium'])
chk('[13] 档值 preset：h264_nvenc archive/balanced = p6/p4',
    [H.default_preset_for('h264_nvenc', t) for t in ('archive', 'balanced')],
    ['p6', 'p4'])

# ── ⑬-10 sdr 端口（convert_sdr_to_hdr）：四步 / HDR 域表（M5）/ 速度轴 / tune ──
# sdr 的生产语义 = quality 口径 + HDR 域（validate_args 里 set_quality_mode(args.
# quality_mode) 后 set_quality_domain('hdr')）；直接调 S 的用例先复现该状态、用完
# 复原。三脚本共享同一份 convert_crf 模块实例（都走 `from convert_crf import`），
# 状态从本文件加载的 H 实例上取。
_sdr_spec = importlib.util.spec_from_file_location('vu_sdr',
                                                   ROOT / 'convert_sdr_to_hdr.py')
S = importlib.util.module_from_spec(_sdr_spec)
_sdr_spec.loader.exec_module(S)
_cc10 = sys.modules['convert_crf']
_pm10, _pd10 = _cc10.get_quality_mode(), _cc10.get_quality_domain()
_cc10.set_quality_mode('quality')
_cc10.set_quality_domain('hdr')
try:
    chk('[13] sdr 锚点注入（HDR 域）：libx265 balanced=24 / archive=15（域表落值）',
        [qq(S, 'libx265', tier=t)[0] for t in ('balanced', 'archive')], [24, 15])
    chk('[13] sdr HDR 域覆盖行落值：svtav1/aom/vp9 balanced = 33/39/32',
        [qq(S, c, tier='balanced')[0]
         for c in ('libsvtav1', 'libaom-av1', 'libvpx-vp9')], [33, 39, 32])
    _cc10.set_quality_domain('sdr')
    chk('[13] sdr vs HDR 域对照（同值不同落点 ⇒ 域表确实被使用）：svtav1 36 / aom 33',
        [qq(S, c, tier='balanced')[0] for c in ('libsvtav1', 'libaom-av1')], [36, 33])
    _cc10.set_quality_domain('hdr')
    _buf10 = io.StringIO()
    with contextlib.redirect_stdout(_buf10):
        _v10 = S._resolve_quality_params('libx265', 50, None, tier='archive')
    chk('[13] sdr 四步：--crf 50 + archive ⇒ 落 -crf 17', _v10, (17, None, None))
    chk_in('[13] sdr 窗口告警逐字（与另两脚本同句）', _w13, _buf10.getvalue())
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            S._resolve_quality_params('libx265', 50, None, tier='archive',
                                      strict_quality=True)
        _se10 = None
    except SystemExit as _e10:
        _se10 = _e10.code
    chk('[13] sdr --strict-quality：越窗直接退出码 2', _se10, 2)
    chk('[13] sdr 0 哨兵：--crf 0（原始）保留通道；--crf 5 + lossless 归零',
        (qq(S, 'libx265', crf=0, tier='archive'),
         qq(S, 'libx265', crf=5, tier='lossless')),
        ((0, None, None), (0, None, None)))
    chk('[13] sdr 档值 preset 同表：libx265 archive/balanced = slower/medium',
        [S.default_preset_for('libx265', t) for t in ('archive', 'balanced')],
        ['slower', 'medium'])
    chk('[13] sdr tune 派生与 hwaccel 逐项一致（archive→uhq / balanced→None / h264→None / 归零→None）',
        [S._strategy_tune_for('hevc_nvenc', 'archive', None, False),
         S._strategy_tune_for('hevc_nvenc', 'balanced', None, False),
         S._strategy_tune_for('h264_nvenc', 'archive', None, False),
         S._strategy_tune_for('hevc_nvenc', 'archive', None, True)],
        ['uhq', None, None, None])

    def _sdr13(codec=None, st=None, **kw):
        if st is None:
            st = S.EncodeSettings(codec=codec, crf=kw.pop('crf', None),
                                  preset='medium', pix_fmt='yuv420p10le',
                                  tier=kw.pop('tier', None))
        return ' '.join(S.build_encode_cmd(640, 360, 30.0, WORK / 'o13_sdr.mp4', SRC,
                                           st, False,
                                           extra_args=kw.pop('extra_args', None)))

    _aom_s = _sdr13('libaom-av1', crf=24, tier='balanced')
    _vp9_s = _sdr13('libvpx-vp9', crf=24, tier='balanced')
    _vp9_n = _sdr13('libvpx-vp9', crf=24)
    _rv_s = _sdr13('librav1e', crf=24, tier='balanced')
    _rv_n = _sdr13('librav1e', crf=24)
    chk('[13] sdr 速度轴：aom=6 / vp9=2（新显式）/ rav1e -speed 6',
        ('-cpu-used 6' in _aom_s, '-cpu-used 2' in _vp9_s, '-speed 6' in _rv_s),
        (True, True, True))
    chk('[13] sdr 速度轴：无 tier 时保持旧行为（vp9 不下发 / rav1e 不下发 -speed）',
        ('-cpu-used' in _vp9_n, '-speed' in _rv_n), (False, False))
    _vp9_x = _sdr13('libvpx-vp9', crf=24, tier='balanced',
                    extra_args=['-cpu-used', '4'])
    chk('[13] sdr 速度轴尊重 --extra-args 显式值',
        ('-cpu-used 4' in _vp9_x, '-cpu-used 2' in _vp9_x), (True, False))
    # rc 轴注入在 build_encode_settings（sdr 的 aq 走结构层，§8-3 登记的不对称）
    class _NS10:
        pass
    _ns10 = _NS10()
    _ns10.__dict__.update(dict(
        codec='hevc_nvenc', preset='p4', bit_depth=10, threads=0,
        audio_codec='copy', audio_bitrate='128k', no_master_display=False,
        master_display=None, max_cll=None, color_range='pc', rc_mode='auto',
        quality_tier='archive', lookahead=None, nvenc_multipass=None,
        nvenc_aq=False, nvenc_tune=None, crf=None, cq=None, qp=None,
        bitrate=None))
    _st10 = S.build_encode_settings(_ns10)
    chk('[13] sdr rc 轴：archive ⇒ lookahead 250 + multipass fullres + aq（§8-3 走结构层）',
        (_st10.lookahead, _st10.nvenc_multipass, _st10.nvenc_aq),
        (250, 'fullres', True))
    _cmd10 = _sdr13(st=_st10)
    chk('[13] sdr rc 轴进命令：-rc-lookahead 250 / -multipass fullres / -spatial-aq 1',
        ('-rc-lookahead 250' in _cmd10, '-multipass fullres' in _cmd10,
         '-spatial-aq 1' in _cmd10), (True, True, True))
    _ns10.cq = 0
    _st10z = S.build_encode_settings(_ns10)
    chk('[13] sdr rc 轴：质量归零（cq 0 哨兵）时不注入',
        (_st10z.lookahead, _st10z.nvenc_multipass, _st10z.nvenc_aq),
        (None, None, False))

    # CLI 级：概览的范围提示（M5）+ 标定告警 + strict 隐含（§5.3）
    def _t10(*extra):
        return ['--input', str(SRC), '--output', str(WORK / 'o13sdr'), '--dry-run',
                '--no-model'] + list(extra)

    _r10, _o10 = run_q('convert_sdr_to_hdr.py', _t10('--quality-tier', 'archive'))
    chk('[13] sdr 概览：HDR 域范围提示（M5）+ 未标定档告警',
        (S._HDR_TIER_BOUNDARY in _o10, VU_CRF.CALIB_UNCALIB_DIFF in _o10),
        (True, True))
    _r10, _o10 = run_q('convert_sdr_to_hdr.py', _t10())
    chk('[13] sdr 概览：balanced 默认 ⇒ 范围提示在、无"组合偏离"句',
        (S._HDR_TIER_BOUNDARY in _o10, '当前组合偏离' in _o10), (True, False))
    _r10, _o10 = run_q('convert_sdr_to_hdr.py',
                       _t10('--quality-tier', 'archive', '--crf', '50',
                            '--fallback-policy', 'strict'))
    chk('[13] sdr --fallback-policy strict 隐含 strict-quality ⇒ rc=2 且首行同告警句',
        (_r10, _w13.split('。')[0] in _o10), (2, True))
finally:
    _cc10.set_quality_mode(_pm10)
    _cc10.set_quality_domain(_pd10)

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
