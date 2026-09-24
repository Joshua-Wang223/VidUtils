# verify/verify_overview_lockstep.py — 两个脚本**概览块字段**的对齐判据（2026-09-24）
#
# 背景：用户点名「信息显示还有不对称」——同一份 CLI 下
#   hwaccel 的「编码器」行缺 pix_fmt / color_range、有「解码」行却没有「并发策略」；
#   cpu_v2 反过来（有 pix_fmt/color_range/并发策略，没有解码行）。
# 「取长补短」之后，两个概览块的**字段序列**应当一致 —— 唯一允许的差异是 hwaccel
# 专属的 GPU 三轴概念（「组合」与按需出现的「降级提示」）。
#
# 为什么值得有判据：显示层最容易静默漂移。本轮就顺带抓到 cpu_v2 在
# `--codec hevc_nvenc --rc-mode constqp --qp 18` 下印出**根本不存在的** `CQ: 23`
# （命令里只有 `-rc constqp -qp 18`），与同屏的 `QP: 18` 自相矛盾。
#
# 纯 CPU；`--dry-run` 即可（概览块在 dry-run 下同样打印）。
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BASE = ['--input', str(ROOT / 'temp' / 'fixture_1080p.mp4'),
        '--output', str(ROOT / 'temp' / 'verify_overview' / 'o'), '--dry-run',
        '--mode', 'crop', '--output-width', '640', '--output-height', '360']
# ⚠ 两侧必须**横向对等**：只给一侧传 --scale-algo 会凭空造出「缩放」行的差异
# （那正是另一处 harness 踩过的坑）。
ALGO = ['--scale-algo', 'libswscale-lanczos']
CASES = {
    '默认':                    [],
    'nvenc constqp --qp 18':   ['--codec', 'hevc_nvenc', '--rc-mode', 'constqp', '--qp', '18'],
    'libx265 --crf 0':         ['--codec', 'libx265', '--crf', '0'],
    'cover 模式':              ['--mode', 'cover'],
    'color-range + extra-args': ['--color-range', 'pc',
                                 '--extra-args', '-max_muxing_queue_size', '4096'],
}
# hwaccel 专属（GPU 三轴概念），不属于「字段要对齐」的范围
HW_ONLY = {'组合', '降级提示'}

fails = []


def chk(label, got, want):
    if got != want:
        fails.append(f'{label}\n      得到 {got!r}\n      期望 {want!r}')


def chk_in(label, needle, hay):
    if needle not in (hay or ''):
        fails.append(f'{label}\n      子串 {needle!r} 不在 {hay!r}')


def run(script, extra, cpu_only=False):
    cmd = [sys.executable, str(ROOT / script)] + BASE + ALGO
    if cpu_only:
        cmd += ['--decode', 'cpu']
    p = subprocess.run(cmd + extra, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, ((p.stdout or '') + (p.stderr or ''))


def fields(text):
    """抽出概览块的「字段名」序列（行首 <标签><空格>: 形式）。"""
    seen, out = set(), []
    for line in (text or '').splitlines():
        m = re.match(r'^(\S+)\s+:', line)
        if m and m.group(1) not in seen and not line.startswith(('DRY', '命令', '▶', '─')):
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def field_diff(h, c):
    """返回字段差异的描述串；完全一致时返回 None（判据与自检共用）。

    ⚠ **顺序也算差异** —— 概览块是给人读的，字段顺序不同会让两份输出看着不像一套。
    """
    hh = [x for x in h if x not in HW_ONLY]
    if hh == c:
        return None
    if set(hh) == set(c):
        return f'顺序不同：H={hh} C={c}'
    return ('缺字段：只 hwaccel=' + str([x for x in hh if x not in c])
            + ' 只 cpu_v2=' + str([x for x in c if x not in hh]))


# ── 自检：判词装置的正/负/空三格（装置恒真最难发现）────────────────────────
def selftest():
    bad = 0
    for label, h, c, want_same in (
            ('相同', ['A', 'B'], ['A', 'B'], True),
            ('顺序不同', ['A', 'B'], ['B', 'A'], False),
            ('多一个字段', ['A', 'B'], ['A'], False),
            ('hwaccel 专属字段被豁免', ['组合', 'A'], ['A'], True),
    ):
        got = field_diff(h, c)
        ok = (got is None) == want_same
        print(f'  [{"OK" if ok else "目标差异"}] 自检·{label}')
        if not ok:
            print(f'        got : {got}  （want_same={want_same}）')
            bad = 1
    if bad:
        print('✗ SELFTEST 失败：判词装置本身不可信')
        sys.exit(1)
    print('SELFTEST 通过（4 格：相同 / 顺序 / 多字段 / 专属豁免）')
    sys.exit(0)


if '--selftest' in sys.argv:
    selftest()

print('── ① 同一份 CLI：两个概览块的字段序列必须一致 ──')
for name, extra in CASES.items():
    rc_h, hw = run('vidcrop_hwaccel.py', extra, cpu_only=True)
    rc_c, cv = run('vidcrop_cpu_v2.py', extra)
    chk(f'[1] {name}：两脚本都跑通', (rc_h, rc_c), (0, 0))
    d = field_diff(fields(hw), fields(cv))
    chk(f'[1] {name}：字段集一致（hwaccel 专属的 {sorted(HW_ONLY)} 豁免）', d, None)

print('── ② 「编码器」行两脚本都要有 pix_fmt / color_range ──')
for name, extra in CASES.items():
    for who, script, cpu in (('hwaccel', 'vidcrop_hwaccel.py', True),
                             ('cpu_v2', 'vidcrop_cpu_v2.py', False)):
        _, out = run(script, extra, cpu_only=cpu)
        line = next((ln for ln in out.splitlines() if ln.startswith('编码器')), '')
        chk_in(f'[2] {name} · {who} 有 pix_fmt 字段', 'pix_fmt:', line)
        chk_in(f'[2] {name} · {who} 有 color_range 字段', 'color_range:', line)
# --color-range 非 auto 时两脚本同串（含那句解释）
_, hw_pc = run('vidcrop_hwaccel.py', CASES['color-range + extra-args'], cpu_only=True)
_, cv_pc = run('vidcrop_cpu_v2.py', CASES['color-range + extra-args'])
_hl = next(ln for ln in hw_pc.splitlines() if ln.startswith('编码器'))
_cl = next(ln for ln in cv_pc.splitlines() if ln.startswith('编码器'))
chk_in('[2] hwaccel：非 auto 的 color_range 带解释', '（必要时自动做值域转换）', _hl)
chk_in('[2] cpu_v2 ：非 auto 的 color_range 带解释', '（必要时自动做值域转换）', _cl)
chk('[2] 两脚本 color_range 片段同串',
    [x for x in _hl.split() if x.startswith('color_range') or '值域' in x],
    [x for x in _cl.split() if x.startswith('color_range') or '值域' in x])

print('── ③ 负向回归：概览显示的量纲必须与命令里真正下发的那个一致 ──')
# 旧 bug（cpu_v2）：命令只有 `-rc constqp -qp 18`，概览却印 `CQ: 23` + `QP: 18` 自相矛盾。
# 判据写成与**命令**挂钩的不变量，这样在「有 GPU（走 -qp）」与「无 GPU（降级成 -crf）」
# 两种机器上都成立。
for who, script, cpu in (('hwaccel', 'vidcrop_hwaccel.py', True),
                         ('cpu_v2', 'vidcrop_cpu_v2.py', False)):
    _, out = run(script, CASES['nvenc constqp --qp 18'], cpu_only=cpu)
    enc = next((ln for ln in out.splitlines() if ln.startswith('编码器')), '')
    # ⚠ 只在**命令行**里找 -qp/-crf：提示文本里会出现 `--qp 18`，用裸 `-qp` 去 search
    # 整个输出会命中它（`--qp` 含子串 `-qp`），把"降级了却以为走 -qp"这种假绿放过去。
    cmd_line = next((ln for ln in out.splitlines()
                     if ln.lstrip().startswith(('执行命令', '命令'))), '')
    chk(f'[3] {who}：命令行非空（装置有效）', bool(cmd_line), True)
    chk(f'[3] {who}：不显示 CQ（constqp 下命令里没有 -cq）', 'CQ:' in enc, False)
    chk(f'[3] {who}：QP 与 CRF 恰好显示一个（显示命令真正用的那个量纲）',
        ('QP:' in enc) + ('CRF' in enc), 1)
    m = re.search(r'-qp (\d+)', cmd_line)
    if m:
        chk_in(f'[3] {who}：不降级时显示的正是命令里的 -qp', f'QP: {m.group(1)}', enc)
    m = re.search(r'-crf (\d+)', cmd_line)
    if m:
        chk_in(f'[3] {who}：降级时显示的正是命令里的 -crf', f'CRF: {m.group(1)}', enc)

print('── ④ 「解码」「并发策略」「运行模式」三行两脚本都要有，且 dry-run 下可见 ──')
for who, script, cpu in (('hwaccel', 'vidcrop_hwaccel.py', True),
                         ('cpu_v2', 'vidcrop_cpu_v2.py', False)):
    _, out = run(script, [], cpu_only=cpu)
    fs = fields(out)
    for want in ('解码', '并发策略', '运行模式', '系统资源', '额外参数'):
        if want == '额外参数':          # 只有真给了 --extra-args 才出现，单独用例验
            continue
        chk_in(f'[4] {who}：有「{want}」行', want, ' '.join(fs))

print('── ⑤ 系统资源不再重复打印（cpu_v2 曾印两遍同一组数字）──')
_, cv_out = run('vidcrop_cpu_v2.py', [])
n = sum(1 for ln in cv_out.splitlines() if ln.startswith(('系统资源', '资源探测')))
chk('[5] cpu_v2 只印一行系统资源', n, 1)
_, hw_out = run('vidcrop_hwaccel.py', [], cpu_only=True)
chk_in('[5] hwaccel 也有系统资源行', '系统资源', hw_out)
chk_in('[5] hwaccel 的 CPU 用 cgroup 感知的值（标注来源）', '（', 
       next((ln for ln in hw_out.splitlines() if ln.startswith('系统资源')), ''))

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
