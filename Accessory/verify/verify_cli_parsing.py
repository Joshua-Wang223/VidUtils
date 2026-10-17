# verify/verify_cli_parsing.py — CLI 解析与参数校验的两个行为判据（2026-09-24）
#
# ① `--extra-args`：README / docstring / `--help` 一直教的是
#    `--extra-args -- -max_muxing_queue_size 4096`（带 `--` 分隔符）。而 argparse 的
#    `nargs=REMAINDER` **从 Python 3.12 起不再容忍开头的 `--`** —— 实测那条命令直接报
#    `unrecognized arguments: -- …`，于是 `normalize_extra_args()` 里剥 `--` 的那段
#    长期是**死代码**。现在改成自己预切 argv（`_split_extra_args`），两种写法都必须能用。
#
# ② 输出尺寸的偶数校验：hwaccel 的 `validate_output_dimensions()` 此前**零调用点**
#    （死代码）⇒ 奇数尺寸会一路带到 ffmpeg、在编码器初始化时才报错；cpu_v2 早就有。
#    现在两脚本的退出码与报错首行必须逐字一致。
#
# ③ R1 quality-tier 的 CLI 契约（2026-10-17）：`--quality-tier` 走 argparse choices；
#    §4.4 越窗告警三脚本逐字一致；`--strict-quality`（以及隐含它的
#    `--fallback-policy strict`，§5.3）下同组合 exit 2；cpu_v2 有意无这三道开关
#    （§8-5：恒为告警）。
#
# 纯 CPU 用例，不需要 GPU。
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _paths import repo_root, temp_root
ROOT = repo_root()
SRC = temp_root() / 'fixture_1080p.mp4'

fails = []


def chk(label, got, want):
    if got != want:
        fails.append(f'{label}\n      得到 {got!r}\n      期望 {want!r}')


def chk_in(label, needle, hay):
    if needle not in (hay or ''):
        fails.append(f'{label}\n      子串 {needle!r} 不在 {hay!r}')


def run(script, args, cpu_only=False):
    base = [sys.executable, str(ROOT / script)]
    if cpu_only:
        base += ['--decode', 'cpu', '--scale-algo', 'libswscale-lanczos']
    p = subprocess.run(base + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def cmd_of(text):
    m = re.search(r'命令[^:]*: (ffmpeg .*)$', text, re.M)
    return m.group(1) if m else ''


def err_first(text):
    for line in (text or '').splitlines():
        if line.startswith('[ERROR]'):
            return line
    return ''


if not SRC.exists():
    print(f'✗ 缺素材 {SRC}')
    print('  它由 verify_color_tagging.py（或任一带 if not exists 守卫的 verify）自建：')
    print('      python3 Accessory/verify/verify_color_tagging.py')
    print('  或直接手搓：ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=25:duration=1 '
          '-c:v libx264 -preset ultrafast -pix_fmt yuv420p <路径>')
    sys.exit(2)

BASE = ['--input', str(SRC), '--output', str(temp_root() / 'verify_cli' / 'o'),
        '--dry-run', '--output-width', '640', '--output-height', '360']

print('── ① --extra-args 的两种写法都要能落地 ──')
for label, tail in (('文档写法（带 -- 分隔符）', ['--extra-args', '--', '-max_muxing_queue_size', '4096']),
                    ('不带 -- 的写法', ['--extra-args', '-max_muxing_queue_size', '4096'])):
    for script, cpu_only in (('vidcrop_hwaccel.py', True), ('vidcrop_cpu_v2.py', False)):
        rc, out = run(script, BASE + tail, cpu_only=cpu_only)
        chk(f"[1] {script} · {label}：不报错（rc=0）", rc, 0)
        chk_in(f"[1] {script} · {label}：参数真的进了命令",
               '-max_muxing_queue_size 4096', cmd_of(out))
        chk(f"[1] {script} · {label}：没有 unrecognized", 'unrecognized' in out, False)
# --extra-args 后面为空：合法，且命令里不该多出东西
for script, cpu_only in (('vidcrop_hwaccel.py', True), ('vidcrop_cpu_v2.py', False)):
    rc, out = run(script, BASE + ['--extra-args'], cpu_only=cpu_only)
    chk(f'[1] {script}：--extra-args 后为空不报错', rc, 0)
# 不写 --extra-args：命令里不该有额外参数
for script, cpu_only in (('vidcrop_hwaccel.py', True), ('vidcrop_cpu_v2.py', False)):
    rc, out = run(script, BASE, cpu_only=cpu_only)
    chk(f'[1] {script}：不写 --extra-args 时无额外参数',
        '-max_muxing_queue_size' in cmd_of(out), False)

print('── ② 输出尺寸的偶数校验：两脚本退出码与报错首行一致 ──')
DIM_CASES = (
    ('641x360 奇数宽', 2, ['--output-width', '641', '--output-height', '360']),
    ('640x361 奇数高', 2, ['--output-width', '640', '--output-height', '361']),
    ('640x360 偶数（应通过）', 0, ['--output-width', '640', '--output-height', '360']),
    ('yuv422p + 641x360（4:2:2 只要求宽偶）', 2,
     ['--output-width', '641', '--output-height', '360', '--pix-fmt', 'yuv422p']),
    ('yuv422p + 640x361（高奇也给过）', 0,
     ['--output-width', '640', '--output-height', '361', '--pix-fmt', 'yuv422p']),
    ('--bit-depth 10 + 641x360', 2,
     ['--output-width', '641', '--output-height', '360', '--bit-depth', '10']),
    ('--output-width 0（零值，走另一条分支）', 2,
     ['--output-width', '0', '--output-height', '360']),
)
for label, expect_rc, dims in DIM_CASES:
    args = ['--input', str(SRC), '--output', str(temp_root() / 'verify_cli' / 'o'),
            '--dry-run'] + dims
    h_rc, h_out = run('vidcrop_hwaccel.py', args, cpu_only=True)
    c_rc, c_out = run('vidcrop_cpu_v2.py', args)
    chk(f'[2] {label}：hwaccel 退出码', h_rc, expect_rc)
    chk(f'[2] {label}：cpu_v2 退出码', c_rc, expect_rc)
    chk(f'[2] {label}：两脚本报错首行逐字相同', err_first(h_out), err_first(c_out))
    if expect_rc == 0:
        chk(f'[2] {label}：通过时两边都不该有 [ERROR]',
            (err_first(h_out), err_first(c_out)), ('', ''))
    else:
        chk(f'[2] {label}：拒绝时首行非空', bool(err_first(h_out)), True)

print('── ③ --quality-tier：choices 拒绝 / 窗口告警三脚本逐字 / strict 与隐含（R1 §5.3/§7）──')
SDR_BASE = ['--input', str(SRC), '--output', str(temp_root() / 'verify_cli' / 'os'),
            '--dry-run', '--no-model']


def after_error(text):
    """argparse 报错行 `prog: error: <正文>` 的正文（跨脚本可比；无 → ''）。"""
    for line in (text or '').splitlines():
        if ': error: ' in line:
            return line.split(': error: ', 1)[1]
    return ''


def first_with(text, needle):
    """含 needle 的第一行（无 → ''）。"""
    return next((ln for ln in (text or '').splitlines() if needle in ln), '')


def run3(extra):
    """同一组质量参数跑三脚本（sdr 不吃 BASE 的宽高/scale-algo）。"""
    return {
        'hwaccel': run('vidcrop_hwaccel.py', BASE + extra, cpu_only=True),
        'cpu_v2': run('vidcrop_cpu_v2.py', BASE + extra),
        'sdr': run('convert_sdr_to_hdr.py', SDR_BASE + extra),
    }


# ③-1 非法档名：argparse choices 拒绝（rc=2）且报错正文三脚本逐字一致
_e = {k: (v[0], after_error(v[1])) for k, v in run3(['--quality-tier', 'bogus']).items()}
chk('[3] --quality-tier bogus 三脚本 rc=2', [v[0] for v in _e.values()], [2, 2, 2])
chk('[3] bogus 报错正文三脚本逐字一致',
    [_e['cpu_v2'][1], _e['sdr'][1]], [_e['hwaccel'][1]] * 2)
chk_in('[3] 报错正文含 invalid choice', 'invalid choice', _e['hwaccel'][1])
chk_in('[3] 报错正文指名 --quality-tier', '--quality-tier', _e['hwaccel'][1])

# ③-2 越窗告警首行（--quality-tier archive --crf 50；钉 libx265 使三脚本原生值同串）
_r = run3(['--codec', 'libx265', '--quality-tier', 'archive', '--crf', '50'])
chk('[3] archive+--crf 50 默认仅告警（三脚本 rc=0）',
    [v[0] for v in _r.values()], [0, 0, 0])
_w = {k: first_with(v[1], '质量窗口为') for k, v in _r.items()}
chk('[3] 窗口告警首行三脚本逐字一致且非空',
    (_w['cpu_v2'], _w['sdr']), (_w['hwaccel'], _w['hwaccel']))
chk_in('[3] 告警首行含 §4.4 固定句（钳后值 + 原生值）',
       '⚠ --quality-tier archive 的质量窗口为 CRF 13~18；--crf 50（基准轴 ≈47.6）'
       '已钳到 18（libx265 -crf 17）', _w['hwaccel'])

# ③-3 --strict-quality：越窗 exit 2、首行同告警句；窗内组合放行（负向）
_sq = ['--codec', 'libx265', '--quality-tier', 'archive', '--crf', '50',
       '--strict-quality']
_rh, _oh = run('vidcrop_hwaccel.py', BASE + _sq, cpu_only=True)
_rs, _os = run('convert_sdr_to_hdr.py', SDR_BASE + _sq)
chk('[3] hwaccel/sdr --strict-quality 越窗 exit 2', (_rh, _rs), (2, 2))
chk('[3] strict 首行与默认告警同句',
    (first_with(_oh, '质量窗口为'), first_with(_os, '质量窗口为')),
    (_w['hwaccel'], _w['hwaccel']))
_ok = ['--codec', 'libx265', '--quality-tier', 'archive', '--crf-ref', '16',
       '--strict-quality']
_rh2, _ = run('vidcrop_hwaccel.py', BASE + _ok, cpu_only=True)
_rs2, _ = run('convert_sdr_to_hdr.py', SDR_BASE + _ok)
chk('[3] --strict-quality 窗内组合放行（负向：rc=0）', (_rh2, _rs2), (0, 0))

# ③-4 --fallback-policy strict 隐含打开 --strict-quality（§5.3）
_fp = ['--codec', 'libx265', '--quality-tier', 'archive', '--crf', '50',
       '--fallback-policy', 'strict']
_rh3, _oh3 = run('vidcrop_hwaccel.py', BASE + _fp, cpu_only=True)
_rs3, _os3 = run('convert_sdr_to_hdr.py', SDR_BASE + _fp)
chk('[3] --fallback-policy strict 隐含 strict-quality ⇒ exit 2', (_rh3, _rs3), (2, 2))
chk('[3] 隐含路径首行同告警句',
    (first_with(_oh3, '质量窗口为'), first_with(_os3, '质量窗口为')),
    (_w['hwaccel'], _w['hwaccel']))

# ③-5 cpu_v2 有意无这三个开关（§8-5）：两道判定恒为告警、参数被 argparse 拒
for _flag in (['--strict-quality'], ['--fallback-policy', 'strict']):
    _rc, _o = run('vidcrop_cpu_v2.py',
                  BASE + ['--codec', 'libx265', '--quality-tier', 'archive',
                          '--crf', '50'] + _flag)
    chk(f'[3] cpu_v2 无 {_flag[0]}（有意不对称，未识别）',
        _rc != 0 and 'unrecognized' in _o, True)

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
