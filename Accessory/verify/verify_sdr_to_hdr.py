# verify/verify_sdr_to_hdr.py — convert_sdr_to_hdr.py 的行为判据（2026-10-06）
#
# ① gbrp16le 原始帧往返：这是整条链路的数值基础。帧数据全程走 16bit 平面管道
#    而不落中间 PNG，所以「解码字节 → [0,1] float → 再编码回字节」必须无损
#    （允许 16bit 量化级差 1/65535）。RGB 顺序也要对：gbrp 平面序是 [G,B,R]，
#    错一位就会红蓝互换，而这种错误在灰度测试图上看不出来。
#
# ② HDR10 静态元数据必须写进**单条** -x265-params：实测
#    `-x265-params A -x265-params B` 是后者整条覆盖前者（与
#    vidcrop_cpu_v2.py 记录的同一坑）。拆成两条的话 mastering display 会被
#    后一条静默抹掉。
#
# ③ token 顺序契约：输出文件路径必须在最后（--extra-args 插在它之前）。
#
# ④ 真编码（默认开，--no-encode 跳过）：对 2s 短切片真跑一遍 --no-model，
#    然后 ffprobe 校验产物确实是 HEVC Main10 / bt2020nc / smpte2084 / pc，
#    且帧级 side_data 里带 mastering display 与 content light level。
#    这一节不需要 GPU、不需要 torch，但需要 ffmpeg。
#
# 纯 CPU 用例。素材缺失 exit 2。
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _paths import repo_root, temp_root
ROOT = repo_root()
sys.path.insert(0, str(ROOT))

import convert_sdr_to_hdr as S  # noqa: E402

WORK = temp_root() / 'verify_sdr_hdr'
SRC = WORK / 'src.mp4'
ENCODE = '--no-encode' not in sys.argv

fails = []


def chk(label, got, want):
    if got != want:
        fails.append(f'{label}\n      得到 {got!r}\n      期望 {want!r}')


def chk_in(label, needle, hay):
    if needle not in (hay or ''):
        fails.append(f'{label}\n      子串 {needle!r} 不在 {hay!r}')


def chk_not_in(label, needle, hay):
    if needle in (hay or ''):
        fails.append(f'{label}\n      子串 {needle!r} 不该出现在 {hay!r}')


def chk_true(label, cond, detail=''):
    if not cond:
        fails.append(f'{label}' + (f'\n      {detail}' if detail else ''))


def run_script(args):
    p = subprocess.run([sys.executable, str(ROOT / 'convert_sdr_to_hdr.py')] + args,
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def cmd_of(text, which=1):
    """抓第 n 条 '  命令: ffmpeg …' 行。"""
    ms = re.findall(r'命令: (ffmpeg .*)$', text, re.M)
    return ms[which - 1] if len(ms) >= which else ''


def err_first(text):
    for line in (text or '').splitlines():
        if line.startswith('[ERROR]'):
            return line
    return ''


# ═══════════════════════════════════════════════════════════════════
print('── ① gbrp16le 原始帧往返：数值无损 + RGB 顺序 ──')
W, H = 64, 48
n = W * H

# 造一帧可判别 RGB 顺序的测试数据。gbrp 的平面顺序是 [G, B, R]：
#   平面0=G、平面1=B、平面2=R
# 三个平面取三个不同的值（满/半/零），这样任何一次平面错位都会被下面三条断言抓到。
buf = bytearray(n * 6)
for i in range(n):
    buf[i * 2:i * 2 + 2] = (32768).to_bytes(2, 'little')            # 平面0 = G = 0.5
    buf[n * 2 + i * 2:n * 2 + i * 2 + 2] = (0).to_bytes(2, 'little')      # 平面1 = B = 0.0
    buf[n * 4 + i * 2:n * 4 + i * 2 + 2] = (65535).to_bytes(2, 'little')   # 平面2 = R = 1.0
raw = bytes(buf)

frame = S.raw_to_frame(raw, W, H)
chk('① raw_to_frame 输出形状', (len(frame), len(frame[0])), (H, W))
px = frame[0][0]
chk('① R 通道（应取 gbrp 第三平面，值满）', round(px[0], 3), 1.0)
chk('① G 通道（应取 gbrp 第一平面，半满）', round(px[1], 3), 0.5)
chk('① B 通道（应取 gbrp 第二平面，值为零）', round(px[2], 3), 0.0)

# 往返：再编码回字节应与原字节逐字节相同（本例的值都能被 16bit 精确表示）
back = S.frame_to_raw(frame)
chk('① 往返字节数', len(back), n * 6)
chk('① 往返逐字节相同', back, raw)

# 用一组非 16bit 精确的 float 值验证量化误差在 1 个 LSB 内
f2 = [[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], [[0.7, 0.8, 0.9], [0.25, 0.35, 0.45]]]
rt = S.raw_to_frame(S.frame_to_raw(f2), 2, 2)
max_err = max(abs(rt[y][x][c] - f2[y][x][c])
              for y in range(2) for x in range(2) for c in range(3))
chk_true('① 往返误差 ≤ 1 个 16bit LSB', max_err <= 1.0 / 65535 + 1e-9,
         f'最大误差 {max_err:.8f}')

# 越界值必须被钳住，不能溢出成 >65535（那会回绕成暗像素）
over = S.frame_to_raw([[[1.5, -0.5, 2.0]]])
chk('① 越界值被钳到 16bit 边界（不溢出）',
    sorted(S.raw_to_frame(over, 1, 1)[0][0]), [0.0, 1.0, 1.0])

# 帧字节不足时必须报错，而不是静默读出半帧
try:
    S.raw_to_frame(b'\x00' * 10, W, H)
    chk('① 字节不足时报错', '没报错', 'ValueError')
except ValueError as exc:
    chk_in('① 字节不足时报错信息点明期望值', '期望', str(exc))

# numpy 快路径与纯 Python 兜底路径必须给出**相同字节**。
# 2026-10-06 实测：numpy 路径的 frame_to_raw 曾把通道堆到最后一维
# （HWC 交错 = rgb48 布局）而非 gbrp 要求的平面布局，纯 Python 路径却是对的
# —— 两条路径不一致、而端到端测试全绿，只有这一条能抓到。
_orig_numpy = S._numpy
try:
    rnd = __import__('random')
    rng = rnd.Random(7)
    vals = [rng.random() for _ in range(8 * 4 * 3)]
    probe = [[[vals[(y * 8 + x) * 3 + c] for c in range(3)] for x in range(8)]
             for y in range(4)]
    S._numpy = lambda: None
    py_raw = S.frame_to_raw(probe)
    py_frame = S.raw_to_frame(py_raw, 8, 4)
finally:
    S._numpy = _orig_numpy
np_mod = _orig_numpy()
if np_mod is not None:
    np_raw = S.frame_to_raw(np_mod.asarray(probe, dtype=np_mod.float32))
    chk('① numpy 路径与纯 Python 路径输出字节一致（平面 vs 交错）', np_raw, py_raw)
    np_frame = S.raw_to_frame(np_raw, 8, 4)
    chk('① 两条路径解出的帧一致',
        np_mod.abs(np_mod.asarray(py_frame, dtype=np_mod.float32)
                   - np_mod.asarray(np_frame, dtype=np_mod.float32)).max(), 0.0)

print('── ② HDR10 静态元数据：单条 -x265-params ──')
p_str = S.build_x265_params(S.DEFAULT_MASTER_DISPLAY, S.DEFAULT_MAX_CLL)
chk('② 默认元数据拼成单条', p_str.count(':') > 0 and '\n' not in p_str, True)
for key in ('master-display=G(13250,34500)', 'max-cll=1000,400', 'hdr10=1'):
    chk_in(f'② 含 {key}', key, p_str)
chk('② --no-master-display 时只剩 max-cll 与 hdr10',
    S.build_x265_params(None, S.DEFAULT_MAX_CLL), 'max-cll=1000,400:hdr10=1')
chk('② 两者都无时仍写 hdr10=1（色彩三参数另走 -color_primaries 等）',
    S.build_x265_params(None, None), 'hdr10=1')

# ═══════════════════════════════════════════════════════════════════
print('── ③ 命令拼装：token 顺序与 HDR10 标签 ──')
WORK.mkdir(parents=True, exist_ok=True)
if not SRC.exists():
    # lavfi 现造：320x240、8fps、2s，带 aac 音轨（验证 -map 1:a:0? 的复用）
    r = subprocess.run(
        ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
         '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=8:duration=2',
         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
         '-c:v', 'libx264', '-crf', '20', '-pix_fmt', 'yuv420p',
         '-c:a', 'aac', '-shortest', str(SRC)],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print(f'✗ 造素材失败：{(r.stderr or "").strip()}')
        sys.exit(2)

BASE = ['--input', str(SRC), '--output', str(WORK / 'o.mp4'),
        '--no-model', '--dry-run']

rc, out = run_script(BASE)
chk('③ dry-run 退出码', rc, 0)
enc_cmd = cmd_of(out, 2)
for tok in ('-c:v libx265', '-pix_fmt yuv420p10le', '-colorspace bt2020nc',
            '-color_primaries bt2020', '-color_trc smpte2084',
            '-color_range pc', 'setparams=colorspace=bt2020nc',
            '-f rawvideo', '-pixel_format gbrp16le', '-c:a copy'):
    chk_in(f'③ 编码命令含 {tok}', tok, enc_cmd)

# 输出路径必须是最后一个 token：--extra-args 插在它之前
rc, out = run_script(BASE + ['--extra-args', '--', '-max_muxing_queue_size', '4096'])
chk('③ --extra-args（带 -- 分隔符）退出码', rc, 0)
enc_cmd = cmd_of(out, 2)
chk_in('③ 额外参数进了命令', '-max_muxing_queue_size 4096', enc_cmd)
# 契约：输出路径永远是最后一个 token，额外参数插在它之前
chk('③ 额外参数紧挨在输出路径之前',
    shlex.split(enc_cmd)[-3:], ['-max_muxing_queue_size', '4096',
                                str(WORK / 'o.mp4')])
chk('③ 输出路径仍是最后一个 token',
    shlex.split(enc_cmd)[-1], str(WORK / 'o.mp4'))

rc, out = run_script(BASE + ['--extra-args', '-max_muxing_queue_size', '4096'])
chk('③ --extra-args（不带 -- 分隔符）退出码', rc, 0)
chk_in('③ 两种写法都生效', '-max_muxing_queue_size 4096', cmd_of(out, 2))

print('── ④ 参数校验：退出码 2 与报错首行 ──')
BAD = (
    ('输入不存在', 2, ['--input', str(WORK / 'nope.mp4')]),
    ('--crf 越界', 2, ['--crf', '99']),
    ('--duration 为负', 2, ['--duration', '-1']),
    ('--frames 为 0', 2, ['--frames', '0']),
    ('--tile 非 8 倍数（1001）', 2, ['--tile', '1001']),
    ('--tile-overlap 非 8 倍数（100）', 2, ['--tile', '1024', '--tile-overlap', '100']),
    ('--tile ≤ --tile-overlap', 2, ['--tile', '512', '--tile-overlap', '512']),
)
for label, expect_rc, extra in BAD:
    rc, out = run_script(BASE + extra)
    chk(f'④ {label}：退出码', rc, expect_rc)
    chk(f'④ {label}：有 [ERROR] 首行', bool(err_first(out)), True)

# --bit-depth 8 由 argparse 的 choices 直接挡下（退出码 2，但它报的是
# "invalid choice" 而不是本脚本的 [ERROR] 行，所以只断言退出码）
rc, out = run_script(BASE + ['--bit-depth', '8'])
chk('④ --bit-depth 非 10（argchoices 挡下）：退出码', rc, 2)
chk_in('④ --bit-depth 非法值的报错点明可选值', 'choose from 10', out)

# --no-model 不该被模型仓库缺失挡住（这是它存在的意义之一）
rc, out = run_script(['--input', str(SRC), '--output', str(WORK / 'o.mp4'),
                      '--no-model', '--dry-run',
                      '--model-repo', str(WORK / 'no_such_repo')])
chk('④ --no-model + 不存在的 model-repo 仍能 dry-run', rc, 0)

# 但不带 --no-model 时必须拦住，并给出 clone 指引
rc, out = run_script(['--input', str(SRC), '--output', str(WORK / 'o.mp4'),
                      '--dry-run', '--model-repo', str(WORK / 'no_such_repo')])
chk('④ 无 --no-model 且仓库缺失：退出码', rc, 2)
chk_in('④ 报错给出 clone 指引', 'git clone', err_first(out) + out)

# ═══════════════════════════════════════════════════════════════════
if ENCODE:
    print('── ⑤ 真编码：短切片跑通并校验产物 HDR10 标签 ──')
    real_out = WORK / 'real.mp4'
    if real_out.exists():
        real_out.unlink()
    rc, out = run_script(
        ['--input', str(SRC), '--output', str(real_out),
         '--no-model', '--crf', '30', '--preset', 'veryfast',
         '--frames', '4', '--overwrite'])
    chk('⑤ 真编码退出码', rc, 0)
    chk('⑤ 产物存在', real_out.exists(), True)
    if real_out.exists():
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json',
             '-show_streams', str(real_out)],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        data = json.loads(probe.stdout)
        vs = next((s for s in data.get('streams', [])
                   if s.get('codec_type') == 'video'), {})
        chk('⑤ 编码器', vs.get('codec_name'), 'hevc')
        chk('⑤ profile（10bit 必须是 Main 10）', vs.get('profile'), 'Main 10')
        chk('⑤ 像素格式', vs.get('pix_fmt'), 'yuv420p10le')
        chk('⑤ 色彩空间（bt2020 非 bt2020nc 之外的都要报错）',
            vs.get('color_space'), 'bt2020nc')
        chk('⑤ 传输函数（PQ）', vs.get('color_transfer'), 'smpte2084')
        chk('⑤ 色度（BT.2020）', vs.get('color_primaries'), 'bt2020')
        chk('⑤ 全范围', vs.get('color_range'), 'pc')
        chk('⑤ 帧数（--frames 4 应生效）', int(vs.get('nb_frames') or 0), 4)
        chk('⑤ 音轨从源复用过来',
            any(s.get('codec_type') == 'audio' for s in data.get('streams', [])),
            True)

        # 帧级 side_data：mastering display + content light level
        sd = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json',
             '-select_streams', 'v:0', '-show_frames',
             '-read_intervals', '%+#1', str(real_out)],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        frames = json.loads(sd.stdout).get('frames') or []
        side = (frames[0].get('side_data_list') or []) if frames else []
        types = [(d.get('side_data_type') or '').lower() for d in side]
        chk('⑤ 帧级带 mastering display 元数据',
            any('mastering display' in t for t in types), True)
        chk('⑤ 帧级带 content light level 元数据',
            any('content light level' in t for t in types), True)
        md = next((d for d in side
                   if 'mastering display' in (d.get('side_data_type') or '').lower()),
                  {})
        chk('⑤ mastering display 色度（红，BT.2020）', md.get('red_x'), '34000/50000')
        chk('⑤ mastering display 亮度上限（1000 nit）',
            md.get('max_luminance'), '10000000/10000')
else:
    print('── ⑤ 真编码：跳过（--no-encode）──')

# ═══════════════════════════════════════════════════════════════════
print('── ⑥ 模型推理：权重能加载 + 输出值域合理（需 torch + 模型仓库）──')
REPO = Path(S.DEFAULT_MODEL_REPO)
if not REPO.is_dir():
    print(f'  跳过：模型仓库 {REPO} 不存在'
          f'（git clone --depth 1 https://github.com/xiaom233/HDRTVNet-plus.git {REPO}）')
elif '--no-model-test' in sys.argv:
    print('  跳过（--no-model-test）')
else:
    try:
        import numpy as np
        import torch  # noqa: F401
    except ImportError as exc:
        print(f'  跳过：未装 torch/numpy（{exc}）'
              f' —— 可用 --no-model 验证编码链路')
    else:
        try:
            eng = S.HdrTvNetPlus(REPO, device='cpu', threads=4, verbose=False)
        except Exception as exc:
            fails.append(f'⑥ 模型加载失败：{exc}')
        else:
            # 参数量的意义：确认加载的是 Ensemble_AGCM_LE（AGCM+LE 两个子模块）
            nparam = sum(p.numel() for p in eng.net.parameters())
            chk_true('⑥ 参数量与 Ensemble_AGCM_LE 一致（AGCM+LE 级联）',
                     nparam == 591158, f'实际 {nparam}')
            chk('⑥ 子模块齐备（AGCM + LE）',
                sorted(m for m, _ in eng.net.named_children()), ['AGCM', 'LE'])

            # 24x24 是实测的最小可推理边长（条件网络 4 次 stride-2 池化）
            frame = np.random.RandomState(0).rand(24, 24, 3).astype(np.float32)
            try:
                out = eng.enhance(frame)
            except Exception as exc:
                fails.append(f'⑥ 24x24 推理失败：{exc}')
            else:
                chk('⑥ 输出形状与输入一致', out.shape, frame.shape)
                chk_true('⑥ 输出已钳到 [0,1]', 0.0 <= out.min() and out.max() <= 1.0,
                         f'范围 {out.min():.4f}..{out.max():.4f}')
                chk_true('⑥ 输出非平凡（不是全 0 / 全 1）',
                         out.std() > 1e-3, f'std={out.std():.6f}')
                # 权重复用同一张输入应给出同一输出（确定性）
                chk('⑥ 同样输入两次推理结果一致',
                    np.abs(eng.enhance(frame) - out).max(), 0.0)

            # 16x16 必须失败：末层退化成 1x1，InstanceNorm 报错。
            # 这是 --tile 最小值要卡 24 的依据。
            small = np.random.RandomState(0).rand(16, 16, 3).astype(np.float32)
            try:
                eng.enhance(small)
                fails.append('⑥ 16x16 本该失败却成功了（--tile 下界 24 的依据变了）')
            except Exception:
                pass  # 预期失败

            # 分块路径：形状必须与整帧一致（接缝拼接没写错）
            frame2 = np.random.RandomState(1).rand(72, 88, 3).astype(np.float32)
            full = eng.enhance(frame2)
            for tile, ov in ((32, 0), (32, 8), (64, 8)):
                got = eng.enhance(frame2, tile=tile, tile_overlap=ov)
                chk(f'⑥ 分块 {tile}+{ov} 输出形状与整帧一致', got.shape, full.shape)
                chk_true(f'⑥ 分块 {tile}+{ov} 无未写入的空洞',
                         np.isfinite(got).all(), '存在 NaN/Inf')

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
