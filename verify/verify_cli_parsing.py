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
# 纯 CPU 用例，不需要 GPU。
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'temp' / 'fixture_1080p.mp4'

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
    print(f'✗ 缺素材 {SRC}（先跑一次 test/dump_filter_chains.sh 生成）')
    sys.exit(2)

BASE = ['--input', str(SRC), '--output', str(ROOT / 'temp' / 'verify_cli' / 'o'),
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
    args = ['--input', str(SRC), '--output', str(ROOT / 'temp' / 'verify_cli' / 'o'),
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

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
