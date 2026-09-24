# verify/verify_ratio_single_dim.py — 验证「--crop-ratio + 只给一个 --output-* 维度」
# 在**所有**模式下都按比例补全另一个维度。
#
# 背景（2026-09-23 实测复现）：非 crop-cover 模式下这个组合里那个维度会被**静默丢弃** ——
#   --mode cover --crop-ratio 16:9 --output-height 1080      （源 1536x864，本身即 16:9）
# 目标被算成"源在 16:9 下的最大化裁剪"= 1536x864 = 源尺寸 → 直接命中同尺寸跳过，
# 把 1080p 的请求变成 no-op（不报错、不转码、汇总里只有「跳过 1」）。
#
# 判据分三层，缺一不可：
#   ① 正向：目标尺寸确实按比例补全了（1920x1080），且**不再**命中同尺寸跳过
#   ② lockstep：两个脚本的滤镜链与「补全提示」逐字相同
#   ③ 负向回归：既有行为一格都没变（crop-ratio 单独用仍是源最大化裁剪；
#      crop-ratio + 两个维度仍是报错；不带 ratio 仍是原样）
# 另：链取不到时必须判失败（空串相等会被读成"两边一致"）。
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'temp' / 'verify_ratio'

# 源必须**本身就是 16:9**：只有这种源才会让旧代码算出的目标等于源尺寸、
# 从而把"参数被丢弃"伪装成"无需处理"。竖版源用来证明源最大化裁剪这条路还活着。
FIXTURES = ((WORK / 'src_1536x864.mp4', '1536x864'),
            (WORK / 'src_864x1536.mp4', '864x1536'))

# hwaccel 固定用「三轴全 CPU」（理由同 test/dump_filter_chains.sh）：本机是否有 GPU
# 不影响结论，且链与 cpu_v2 可逐字对比。本次改动只动尺寸推导，与解码轴无关。
HW_CPU_ONLY = ('--decode', 'cpu', '--scale-algo', 'libswscale-lanczos')

CHAIN_RE = re.compile(r"-filter:v:0 ('[^']*'|\S+)")   # shlex.join 按需加引号
fails = []


def ensure_fixtures():
    WORK.mkdir(parents=True, exist_ok=True)
    for path, size in FIXTURES:
        if path.exists():
            continue
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=25:duration=1',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        str(path)], check=True)


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got!r}\n        want: {want!r}')
        fails.append(label)


def chk_in(label, needle, hay):
    ok = needle in (hay or '')
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        子串 {needle!r} 不在 {hay!r}')
        fails.append(label)


def chk_nonempty(label, got):
    ok = bool(got)
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print('        取到空串 —— 多半是这条用例根本没产出命令（空值必须判失败）')
        fails.append(label)


def run(script, src, args):
    """跑一次 --dry-run，返回 (rc, 滤镜链, 补全提示行, 全文, 全部提示行的元组)。"""
    # --overwrite 必须有：否则第二次跑（或上一次留下的产物）会命中
    # 「输出文件已存在」跳过，链取空 → 被误读成"这条链路坏了"。
    cmd = [sys.executable, str(ROOT / script), '--input', str(src),
           '--output', str(WORK / 'o'), '--overwrite'] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    out = (p.stdout or '') + (p.stderr or '')
    m = CHAIN_RE.search(out)
    chain = m.group(1).strip("'").split(',setparams=')[0] if m else ''
    prompts = tuple(s.strip() for s in out.splitlines() if s.strip().startswith('提示：'))
    derive = next((s for s in prompts if '仅给了一个维度' in s), '')
    return p.returncode, chain, derive, out, prompts


def both(label, src, args, hw_extra=HW_CPU_ONLY, expect_derive=True):
    """同一组参数喂两个脚本，返回 (hw, cv)，并断言两边链都非空、链相同、提示相同。

    expect_derive=False 用于负向格：那种组合**不该**出现「补全」提示，
    此时提示必须为空（否则就是新行为漏到了旧路径上）。
    """
    # hw_extra 要**横向对等**：hwaccel 侧为了与 v2 的 CPU 链可比才带
    # `--scale-algo libswscale-lanczos`，而这条参数本身会各自触发一句
    # 「crop 模式不做缩放…」提示 —— crop 模式下没有缩放步骤，故只传 `--decode cpu`
    # （否则差出来的提示是**装置**造成的，不是被测对象的分叉）。
    a = run('vidcrop_hwaccel.py', src, tuple(hw_extra) + ('--codec', 'libx264') + tuple(args))
    b = run('vidcrop_cpu_v2.py', src, ('--codec', 'libx264') + tuple(args))
    chk_nonempty(f'{label} · hwaccel 链非空', a[1])
    chk_nonempty(f'{label} · cpu_v2  链非空', b[1])
    chk(f'{label} · 滤镜链逐字相同', a[1], b[1])
    if expect_derive:
        chk_nonempty(f'{label} · 两边都打了「仅给了一个维度」提示', a[2] and b[2])
    else:
        chk(f'{label} · 两边都没打「补全」提示', (a[2], b[2]), ('', ''))
    chk(f'{label} · 提示逐字相同', a[2], b[2])
    # 强断言：**全部**提示行都要一致（不只补全那条）——crop-cover 下 v2 曾多打一条
    # 「--output-width/height 是缩放后的最终尺寸…」而 hwaccel 没有，已按 hwaccel 对齐。
    chk(f'{label} · 全部提示行逐字相同', a[4], b[4])
    return a, b


ensure_fixtures()
print('── ① 正向：crop-ratio + 单维度 → 按比例补全（本次修的就是它）──')
a, _ = both('cover ratio16:9 h=1080', FIXTURES[0][0],
            ('--mode', 'cover', '--crop-ratio', '16:9', '--output-height', '1080'))
chk_in('目标 1920x1080 进了滤镜链', 'scale=1920:1080', a[1])
chk_in('补全提示写明 1920x1080', '补全为 1920x1080', a[2])
# 判据要指名道姓：这里只关心**同尺寸**跳过（「输出文件已存在」是另一条分支，
# 用 --overwrite 已经排除；若只写 '跳过' 会被它误伤成假失败）。
chk('不再命中同尺寸跳过', '目标尺寸与原始尺寸相同' in a[3], False)

a, _ = both('cover ratio16:9 w=1920', FIXTURES[0][0],
            ('--mode', 'cover', '--crop-ratio', '16:9', '--output-width', '1920'))
chk_in('另一个维度对称（→ 1920x1080）', 'scale=1920:1080', a[1])

a, _ = both('crop ratio16:9 h=432', FIXTURES[0][0],
            ('--mode', 'crop', '--crop-ratio', '16:9', '--output-height', '432'),
            hw_extra=('--decode', 'cpu'))
chk_in('crop 模式也补全（→ 768x432）', 'crop=768:432', a[1])

both('crop-cover ratio16:9 w=1280', FIXTURES[0][0],
     ('--mode', 'crop-cover', '--crop-ratio', '16:9', '--output-width', '1280'))

print('\n── ② 负向回归：既有行为一格未变 ──')
# ③a crop-ratio 单独用：仍是「源最大化裁剪」（竖版源 864x1536 → 864x486）
a, b = both('cover ratio16:9（不给尺寸）', FIXTURES[1][0],
            ('--mode', 'cover', '--crop-ratio', '16:9'), expect_derive=False)
chk_in('仍走源最大化裁剪', 'crop=864:486', a[1])

# ③b 16:9 源 + ratio 单独用 → 目标==源 → 仍然跳过（这正是旧代码的输入）
a = run('vidcrop_hwaccel.py', FIXTURES[0][0],
        HW_CPU_ONLY + ('--codec', 'libx264', '--mode', 'cover', '--crop-ratio', '16:9'))
chk('16:9 源仍按同尺寸跳过', (a[0], '目标尺寸与原始尺寸相同' in a[3]), (0, True))

# ③b2 crop 模式 + 补全后超过源尺寸 → 两个脚本都**不产出命令**，且记账口径一致：
# 都记「跳过」（不是失败）、消息文本逐字相同、退出码都是 0。
# （2026-09-23 对齐：此前 v2 记「失败」并让整批 rc=1。行**格式**仍不同——
#   v2 的跳过行带文件名、hwaccel 带全角冒号——那是 v2 顺序执行器对所有跳过原因的
#   统一样式，只对齐这一条反而会不一致，故这里只断言「消息文本」这一层。）
_msgs = {}
for _script, _extra in (('vidcrop_hwaccel.py', list(HW_CPU_ONLY)), ('vidcrop_cpu_v2.py', [])):
    _r = run(_script, FIXTURES[0][0], _extra + ['--codec', 'libx264', '--mode', 'crop',
                                               '--crop-ratio', '16:9', '--output-height', '1080'])
    chk(f'{_script} · crop 超源：不产命令但目标已补全', (_r[1], '1920x1080' in _r[3]), ('', True))
    chk(f'{_script} · crop 超源：记跳过且 rc=0', (_r[0], '⏭' in _r[3], '✘' in _r[3]), (0, True, False))
    _m = re.search(r'crop 模式下目标尺寸 \([^)]*\) 大于原始尺寸 \([^)]*\)', _r[3])
    chk(f'{_script} · crop 超源：消息文本取到了', bool(_m), True)
    _msgs[_script] = _m.group(0) if _m else ''
chk('crop 超源：两脚本消息文本逐字相同', _msgs['vidcrop_hwaccel.py'], _msgs['vidcrop_cpu_v2.py'])
chk_nonempty('crop 超源：消息文本非空', _msgs['vidcrop_hwaccel.py'])

# ③b3 同尺寸跳过：消息文本同样逐字一致（既有的一处措辞分叉，同轮对齐）
_ss = {}
for _script, _extra in (('vidcrop_hwaccel.py', list(HW_CPU_ONLY)), ('vidcrop_cpu_v2.py', [])):
    _r = run(_script, FIXTURES[0][0], _extra + ['--codec', 'libx264', '--mode', 'cover',
                                               '--output-width', '1536', '--output-height', '864'])
    _m = re.search(r'目标尺寸与原始尺寸相同（[^）]*）。', _r[3])
    _ss[_script] = _m.group(0) if _m else ''
    chk(f'{_script} · 同尺寸跳过：消息文本取到了', bool(_m), True)
chk('同尺寸跳过：两脚本消息文本逐字相同', _ss['vidcrop_hwaccel.py'], _ss['vidcrop_cpu_v2.py'])

# ③c crop-ratio + 两个维度：仍然报错退出 2，且两脚本首行逐字相同
def err_first(script, src, args, hw_extra=True):
    out = run(script, src, (HW_CPU_ONLY if hw_extra else ()) + ('--codec', 'libx264') + tuple(args))
    for line in out[3].splitlines():
        if line.strip().startswith('[ERROR]'):
            return out[0], line.strip()
    return out[0], ''

hw = err_first('vidcrop_hwaccel.py', FIXTURES[0][0],
               ('--mode', 'cover', '--crop-ratio', '16:9', '--output-width', '1920', '--output-height', '1080'))
cv = err_first('vidcrop_cpu_v2.py', FIXTURES[0][0],
               ('--mode', 'cover', '--crop-ratio', '16:9', '--output-width', '1920', '--output-height', '1080'), hw_extra=False)
chk('两维度 + ratio 仍 rc=2', (hw[0], cv[0]), (2, 2))
chk('报错首行逐字相同', hw[1], cv[1])
chk_nonempty('报错首行非空', hw[1])

# ③d 不带 ratio：逐字不受影响
a, b = both('cover 无 ratio 两维度', FIXTURES[0][0],
            ('--mode', 'cover', '--output-width', '1920', '--output-height', '1080'),
            expect_derive=False)
chk('链与改动前一致', a[1], 'scale=1920:1080:flags=lanczos')

print()
if fails:
    print(f'✗ {len(fails)} 项未通过: {fails}')
    sys.exit(1)
print('✓ 全部通过')
