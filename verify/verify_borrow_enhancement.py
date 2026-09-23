# verify/verify_borrow_enhancement.py — 验证「从 Video_Enhancement 借鉴」的这批改动
#
# 这批改动的主题（对照 Video_Enhancement 的 ffmpeg 路径与 NVENC SDK 取值规则）：
#   [A] NVENC 的 -cq 默认配 `-b:v 0`（纯恒定质量）；给了 --bitrate 时不补。
#   [C] `crf/cq == 0` 的真无损改写：libx265 → `lossless=1`；libx264 本已无损不改写；
#       NVENC 的 `-cq 0` → hwaccel 在 rc auto 下改写 `-rc constqp -qp 0`、否则只告警；
#       cpu_v2 的 NVENC 是原样透传 → 一律只告警（两脚本的**差异化处理**）。
#   [B] `--rc-mode constqp` 下不下发无效的 `-rc-lookahead`（该模式 NVENC 静默禁用）。
#   [E] hwaccel 的 `--nvenc-aq`（`-spatial-aq`/`-temporal-aq`）；cpu_v2 无此开关。
#   [借鉴2] rcParams 取值策略的 ffmpeg 等价表达 —— 上述 A/B/C/E，外加
#          **lookahead ↔ 真无损耦合**：cq 0 改写为 constqp 时同给的 lookahead 一并失效。
#   [借鉴1] NVENC 策略失败先按 preset 降档（`_NVENC_PRESET_RETRY`）；`_result` 带
#          实际生效的 strategy/fallback，批汇总据此打印「实际档位」行。
#   [更名] `--flag` → `--suffix`（硬更名：旧名报错退出 2 并给出等价写法）。
#
# 判据风格沿用 verify_rc_lookahead.py：自生成 fixture、chk/chk_in、失败列出并 exit 1。
import contextlib
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import vidcrop_hwaccel as H
import vidcrop_cpu_v2 as C

SRC = ROOT / 'temp' / 'fixture_borrow.mp4'
if not SRC.exists():
    SRC.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=192x108:rate=10:duration=1',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    str(SRC)], check=True)

fails = []
TOKENS = ('-rc', '-qp', '-cq', '-crf', '-b:v', '-rc-lookahead', '-lag-in-frames',
          '-x265-params', '-spatial-aq', '-temporal-aq')


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


def tokens(cmd):
    return ' '.join(f'{t} {cmd[i + 1]}' for i, t in enumerate(cmd[:-1]) if t in TOKENS)


def hw(codec, policy='auto', **kw):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
        cmd = H.build_ffmpeg_cmd(
            SRC, SRC.with_name('o.mp4'), 'crop=160:96:16:6', codec,
            kw.pop('crf', None), kw.pop('cq', None), 'medium', True,
            policy=policy, **kw)
    return cmd, buf.getvalue()


def cv(codec, **kw):
    warns = []
    with contextlib.redirect_stdout(io.StringIO()):
        cmd = C.build_ffmpeg_cmd(
            SRC, SRC.with_name('o.mp4'), 160, 96, 192, 108, 'crop', codec,
            kw.pop('crf', None), kw.pop('cq', None), 'medium', kw.pop('pix_fmt', None),
            0, True, 'copy', '128k', [], warns.append, **kw)
    return cmd, ''.join(warns)


def cli(script, extra):
    """跑真命令行（--dry-run），返回 (exit, stdout+stderr)。"""
    p = subprocess.run(
        [sys.executable, str(ROOT / script),
         '--input', str(SRC), '--output', str(ROOT / 'temp' / 'borrow_out'),
         '--mode', 'crop', '--output-width', '160', '--output-height', '96',
         '--dry-run'] + extra,
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout + p.stderr)


print('── ① [A] NVENC 的 -cq 默认配 -b:v 0（纯恒定质量）──')
chk('① hwaccel NVENC cq 补 -b:v 0', tokens(hw('hevc_nvenc', cq=20)[0]), '-cq 20 -b:v 0')
chk('① cpu_v2  NVENC cq 补 -b:v 0', tokens(cv('hevc_nvenc', cq=20)[0]), '-cq 20 -b:v 0')
chk('① NVENC cq + --bitrate 不补 -b:v 0（不重复 -b:v）',
    tokens(hw('hevc_nvenc', cq=20, bitrate='8M')[0]), '-cq 20 -b:v 8M')
chk('① 非 NVENC（libx264）不受影响', tokens(hw('libx264', crf=20)[0]), '-crf 20')

print('── ② [B] constqp 下不下发无效的 -rc-lookahead ──')
for name, (cmd, w) in (('hwaccel', hw('hevc_nvenc', rc_mode='constqp', qp=23, lookahead=40)),
                       ('cpu_v2', cv('hevc_nvenc', rc_mode='constqp', qp=23, lookahead=40))):
    chk(f'② {name} constqp+LA 不含 -rc-lookahead', '-rc-lookahead' in tokens(cmd), False)
    chk_in(f'② {name} constqp+LA 有告知', '静默禁用', w)
try:
    hw('hevc_nvenc', rc_mode='constqp', qp=23, lookahead=40, policy='strict')
    chk('② hwaccel strict 下 constqp+LA 抛错', 'no-raise', 'raise')
except ValueError as exc:
    chk_in('② hwaccel strict 下报告原因', 'strict 不降级', str(exc))
chk('② 非 constqp（vbr_hq）仍下发 -rc-lookahead',
    tokens(hw('hevc_nvenc', rc_mode='vbr_hq', cq=20, lookahead=40)[0]),
    '-cq 20 -b:v 0 -rc vbr_hq -rc-lookahead 40')

print('── ③ [C] crf/cq == 0 的真无损改写 ──')
chk('③ hwaccel libx265 crf0 → lossless=1',
    tokens(hw('libx265', crf=0)[0]), '-crf 0 -x265-params lossless=1')
chk('③ cpu_v2  libx265 crf0 → lossless=1',
    tokens(cv('libx265', crf=0)[0]), '-crf 0 -x265-params lossless=1')
chk('③ libx264 crf0 保持 -crf 0（本已无损，不改写）',
    tokens(hw('libx264', crf=0)[0]), '-crf 0')
chk('③ NVENC cq0（hwaccel, rc=auto）→ constqp 真无损',
    tokens(hw('hevc_nvenc', cq=0)[0]), '-rc constqp -qp 0 -b:v 0')
chk('③ NVENC cq0 + 显式 rc_mode：只告警不改写（hwaccel）',
    tokens(hw('hevc_nvenc', cq=0, rc_mode='vbr_hq')[0]), '-cq 0 -b:v 0 -rc vbr_hq')
chk_in('③ NVENC cq0 告警不改写（cpu_v2 差异化）', '不是真无损', cv('hevc_nvenc', cq=0)[1])
_cmd = hw('libx265', crf=0, lookahead=40)[0]
chk('③ lossless 与 lookahead 合并进同一条 -x265-params',
    (_cmd.count('-x265-params'),
     set(_cmd[_cmd.index('-x265-params') + 1].split(':'))),
    (1, {'lossless=1', 'rc-lookahead=40'}))

print('── ④ [借鉴2] lookahead ↔ 真无损耦合（cq0 改写后 LA 一并失效）──')
_cmd, _w = hw('hevc_nvenc', cq=0, lookahead=40)
chk('④ cq0 + lookahead 后不再下发 -rc-lookahead', '-rc-lookahead' in _cmd, False)
chk('④ cq0 仍改写为 constqp 真无损', tokens(_cmd), '-rc constqp -qp 0 -b:v 0')
chk_in('④ cq0 + lookahead 有告知', '未生效', _w)

print('── ⑤ [E] --nvenc-aq（hwaccel 有、cpu_v2 无）──')
_cmd, _w = hw('hevc_nvenc', cq=20, nvenc_aq=True)
chk_in('⑤ hwaccel --nvenc-aq 加 -spatial-aq', '-spatial-aq 1', tokens(_cmd))
chk_in('⑤ hwaccel --nvenc-aq 加 -temporal-aq', '-temporal-aq 1', tokens(_cmd))
_cmd2, _w2 = hw('libx264', crf=20, nvenc_aq=True)
chk('⑤ 非 NVENC 策略忽略（不下发 -spatial-aq）', '-spatial-aq' in _cmd2, False)
chk_in('⑤ 非 NVENC 策略有告知', 'nvenc-aq', _w2)
chk('⑤ 默认（不给 --nvenc-aq）不下发 AQ', '-spatial-aq' in hw('hevc_nvenc', cq=20)[0], False)
_rc, _out = cli('vidcrop_cpu_v2.py', ['--nvenc-aq'])
chk('⑤ cpu_v2 无 --nvenc-aq（故意不对称，应被拒绝）',
    _rc != 0 and 'nvenc-aq' in _out, True)

print('── ⑥ build_hdr_args 接受 meta=None（否则 lookahead/lossless 被静默丢弃）──')
for name, mod in (('hwaccel', H), ('cpu_v2', C)):
    chk(f'⑥ {name} meta=None + libx265 仍落 extra',
        mod.build_hdr_args(None, 'libx265', extra_x265_params=['rc-lookahead=40', 'lossless=1']),
        ['-x265-params', 'rc-lookahead=40:lossless=1'])
    chk(f'⑥ {name} meta=None + libx264 为空', mod.build_hdr_args(None, 'libx264'), [])
    chk(f'⑥ {name} meta=None + NVENC 为空', mod.build_hdr_args(None, 'hevc_nvenc'), [])
SDR = {'derived': {'is_hdr': False}}
chk('⑥ SDR 源 + lookahead 仍落 extra',
    H.build_hdr_args(SDR, 'libx265', extra_x265_params=['rc-lookahead=40']),
    ['-x265-params', 'rc-lookahead=40'])

print('── ⑦ [D] --workers 反推每任务线程预算（cpu_v2 专属）──')
# 8 核 / 显式 4 任务 / 未给 --threads → 每任务线程钳到 cpu//workers = 2
chk('⑦ 显式 --workers 自动钳制', C.compute_parallelism(8, 'libx264', 8, 32.0, 16.0,
                                                    workers_override=4, threads_override=0),
    (4, 2))
chk('⑦ 显式 --threads 尊重用户意图（不覆盖）',
    C.compute_parallelism(8, 'libx264', 8, 32.0, 16.0,
                          workers_override=4, threads_override=3), (4, 3))
chk('⑦ 自动分支不受影响（仍取 CODEC_PROFILE 的 4 线程）',
    C.compute_parallelism(8, 'libx264', 8, 32.0, 16.0)[1] <= 4, True)
chk('⑦ 单任务分支不受影响',
    C.compute_parallelism(1, 'libx264', 8, 32.0, 16.0), (1, 8))

print('── ⑧ [借鉴1] preset 降档表 + _result 带实际档位 ──')
chk('⑧ 降档表只降不升、落到 p4',
    H._NVENC_PRESET_RETRY, {'p5': 'p4', 'p6': 'p4', 'p7': 'p4'})
_r = H._result('done', 10, 1.0, strategy='纯 CPU 处理', fallback=True)
chk('⑧ _result 带 strategy/fallback',
    (_r['status'], _r['strategy'], _r['fallback']), ('done', '纯 CPU 处理', True))
chk('⑧ _result 默认值向后兼容',
    H._result('failed'), {'status': 'failed', 'frames': 0, 'elapsed': 0.0,
                          'strategy': None, 'fallback': False})
# 端到端：真跑一次（libx264 策略在本机属"降级"档）→ 汇总应出现「实际档位」
_rc, _out = cli('vidcrop_hwaccel.py', ['--codec', 'libx264', '--crf', '28',
                                       '--overwrite'])
chk('⑧ hwaccel dry-run 正常退出', _rc, 0)


def _real_run(script, extra):
    p = subprocess.run(
        [sys.executable, str(ROOT / script),
         '--input', str(SRC), '--output', str(ROOT / 'temp' / 'borrow_real.mp4'),
         '--mode', 'crop', '--output-width', '160', '--output-height', '96',
         '--overwrite'] + extra,
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout + p.stderr)


_rc, _out = _real_run('vidcrop_hwaccel.py', ['--codec', 'libx264', '--crf', '28'])
chk('⑧ 真实转码成功（libx264）', _rc, 0)
chk_in('⑧ 批汇总打印「实际档位」（借借鉴1a 的 _active_level 报告）', '实际档位', _out)

print('── ⑨ [更名] --flag → --suffix ──')
for script in ('vidcrop_hwaccel.py', 'vidcrop_cpu_v2.py'):
    _rc, _out = cli(script, ['--suffix', '_Zz'])
    chk_in(f'⑨ {script} 接受 --suffix（预览里出现后缀）', '_Zz', _out)
    chk_in(f'⑨ {script} --help 列出 --suffix', '--suffix',
           subprocess.run([sys.executable, str(ROOT / script), '--help'],
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace').stdout)
firsts = []
for script in ('vidcrop_hwaccel.py', 'vidcrop_cpu_v2.py'):
    _rc, _out = cli(script, ['--flag', '_Old'])
    chk(f'⑨ {script} --flag 报错退出 2', _rc, 2)
    hit = next((l.strip() for l in _out.splitlines() if l.strip().startswith('[ERROR]')), '')
    firsts.append(hit)
chk('⑨ 两脚本 --flag 报错首行一致', firsts[0], firsts[1])
chk_in('⑨ 报错给出 --suffix 等价写法', '--suffix', firsts[0])
chk_in('⑨ 报错点明旧名', '--flag 已更名', firsts[0])

print()
if fails:
    print(f'✘ {len(fails)} 项失败：')
    for f in fails:
        print(f'   · {f}')
    sys.exit(1)
print('✓ 全部通过')
