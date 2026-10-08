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

# ═══════════════════════════════════════════════════════════════════
print('── ⑦ 新 CLI 面：质量轴 / 编解码器 / rc 轴 / 容器（纯 CPU，dry-run）──')

def enc_cmd(args):
    """跑一次 dry-run，返回（退出码, 输出全文, 编码命令字符串）。"""
    rc, out = run_script(['--input', str(SRC), '--output', str(WORK / 'o7.mp4'),
                          '--no-model', '--dry-run'] + args)
    return rc, out, cmd_of(out, 2)


# ① 基准轴换算：quality（默认）与 size 两种口径必须给出不同的 libx265 CRF
def enc_crf(args):
    """跑 dry-run，取编码命令里 `-crf N` 的数值（没有则 None）。"""
    rc, out, ec = enc_cmd(args)
    m = re.search(r'-crf (\d+)', ec)
    return rc, (int(m.group(1)) if m else None)

rc, q_val = enc_crf(['--crf-ref', '30'])
chk('⑦ --crf-ref 30（quality 口径）退出码', rc, 0)
chk('⑦ quality 口径换算到 libx265（等质量表 → 31）', q_val, 31)
rc, s_val = enc_crf(['--crf-ref', '30', '--quality-mode', 'size'])
chk('⑦ size 口径换算到 libx265（等体积表 → 29）', s_val, 29)
chk_true('⑦ 两种口径确实给出不同值（否则该参数是摆设）',
         q_val != s_val, f'quality={q_val} size={s_val}')

# ② 字面量同族原样下发；跨族换算
rc, out, ec = enc_cmd(['--crf', '22'])
chk_in('⑦ --crf 22 字面量原样下发', '-crf 22', ec)
rc, out, ec = enc_cmd(['--cq', '26'])
chk_in('⑦ --cq 落到 CPU 编码器时换算为 -crf', '-crf 21', ec)
chk('⑦ 换算后的值不得与输入相同（防"静默丢值"）', '-cq 26' in ec, False)

# ③ HDR10 能力守卫：8bit-only 编码器直接拒绝
rc, out, ec = enc_cmd(['--codec', 'h264_nvenc'])
chk('⑦ --codec h264_nvenc（只做 8bit）退出码', rc, 2)
chk_in('⑦ 拒绝理由点明 10bit 是硬要求', '10bit', err_first(out))
rc, out, ec = enc_cmd(['--codec', 'libx264'])
chk('⑦ --codec libx264（能做 10bit 但非 HDR10 交付格式）应放行', rc, 0)
chk_in('⑦ 但必须告警', 'HDR10 的交付格式', out)

# ④ 降级链：本机无 NVENC → 自动降级 libx265，且质量值要按新编码器换算
rc, out, ec = enc_cmd(['--codec', 'hevc_nvenc', '--cq', '26'])
chk('⑦ hevc_nvenc 不可用时退出码仍为 0（auto 策略降级）', rc, 0)
chk_in('⑦ 降级到 libx265 并入命令', '-c:v libx265', ec)
chk_in('⑦ 降级后 --cq 26 换算成 -crf（质量值不得蒸发）', '-crf 21', ec)
chk('⑦ 降级后不得残留 -cq', '-cq 26' in ec, False)
rc, out, ec = enc_cmd(['--codec', 'hevc_nvenc', '--fallback-policy', 'strict'])
chk('⑦ strict 策略下不可用必须报错', rc, 2)
chk_in('⑦ strict 报错点明不允许降级', 'strict', err_first(out))

# ⑤ 解码轴：默认 cpu；显式 cuda 在本机（无 CUDA）回退且告警
rc, out = run_script(['--input', str(SRC), '--output', str(WORK / 'o7.mp4'),
                      '--no-model', '--dry-run'])
chk('⑦ 默认解码退出码', rc, 0)
dec_cmd = cmd_of(out, 1)
chk_in('⑦ 默认软件解码（解码命令里是 format=gbrp16le）', 'format=gbrp16le', dec_cmd)
chk('⑦ 默认解码命令里不得出现 -hwaccel', '-hwaccel' in dec_cmd, False)
rc, out, ec = enc_cmd(['--decode', 'cuda'])
chk('⑦ --decode cuda 不可用时退出码仍 0（回退）', rc, 0)
dec_cmd = cmd_of(out, 1)
chk('⑦ 回退为软解（解码命令里不得出现 hwdownload）',
    'hwdownload' in dec_cmd, False)
chk('⑦ 回退为软解（不得残留 -hwaccel）', '-hwaccel' in dec_cmd, False)
chk_in('⑦ 回退要有告警', '回退', out)

# ⑥ rc 轴：constqp 的三条互斥规则 + lookahead 量程
for label, extra in (
        ('constqp 缺质量参数', ['--rc-mode', 'constqp']),
        ('constqp 与 --bitrate 互斥', ['--rc-mode', 'constqp', '--qp', '20', '--bitrate', '8M']),
        ('constqp 与字面量 --crf 互斥', ['--rc-mode', 'constqp', '--qp', '20', '--crf', '22']),
        ('--lookahead 超上限 250', ['--lookahead', '300']),
        ('--qp 非 constqp 下不生效但应放行', []),
):
    if extra:
        rc, out, _ = enc_cmd(extra)
        chk(f'⑦ {label}：退出码', rc, 2)
        chk_true(f'⑦ {label}：有 [ERROR] 首行', bool(err_first(out)))

rc, out, ec = enc_cmd(['--lookahead', '40'])
chk('⑦ --lookahead 40 在 libx265 下并入 -x265-params（单条）', rc, 0)
chk_in('⑦ lookahead 与 HDR 元数据同条', 'rc-lookahead=40', ec)
chk('⑦ -x265-params 只发一条（两条是后者覆盖前者）',
    ec.count('-x265-params'), 1)

# ⑦ NVENC 专属轴的忽略告警必须可见（此前被 warn=None 吞掉）
rc, out, ec = enc_cmd(['--nvenc-aq'])
chk('⑦ --nvenc-aq 在非 NVENC 下退出码', rc, 0)
chk_in('⑦ --nvenc-aq 忽略要有告警', '已忽略', out)
chk('⑦ 非 NVENC 下不得下发 -spatial-aq', '-spatial-aq' in ec, False)

# ⑧ 容器 / color-range / 音频
rc, out, ec = enc_cmd(['--container', '.mkv'])
chk('⑦ --container .mkv 生效（输出扩展名）', ec.rstrip().endswith('.mkv'), True)
rc, out, ec = enc_cmd(['--color-range', 'tv'])
chk_in('⑦ --color-range tv 落到 -color_range', '-color_range tv', ec)
chk_in('⑦ 同时落到 setparams 的 range', 'range=tv', ec)
rc, out, ec = enc_cmd(['--audio-codec', 'aac', '--audio-bitrate', '192k'])
chk_in('⑦ 音频重编码', '-c:a aac', ec)
chk_in('⑦ 音频码率', '-b:a 192k', ec)

# ═══════════════════════════════════════════════════════════════════
if ENCODE:
    print('── ⑧ 批处理与单文件并行（真跑，--no-model）──')
    BATCH_IN = WORK / 'batch_in'
    BATCH_OUT = WORK / 'batch_out'
    if not BATCH_IN.is_dir() or len(list(BATCH_IN.glob('*.mp4'))) < 3:
        BATCH_IN.mkdir(parents=True, exist_ok=True)
        for i in (1, 2, 3):
            subprocess.run(
                ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                 '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=6:duration=1',
                 '-c:v', 'libx264', '-crf', '30', '-pix_fmt', 'yuv420p',
                 str(BATCH_IN / f'c{i}.mp4')],
                check=True, capture_output=True)

    rc, out = run_script(['--input', str(BATCH_IN), '--output', str(BATCH_OUT),
                          '-r', '--no-model', '--preset', 'ultrafast',
                          '--suffix', '_HDR', '--overwrite'])
    chk('⑧ 批量目录处理退出码', rc, 0)
    made = sorted(p.name for p in BATCH_OUT.glob('*.mp4'))
    chk('⑧ 三个文件都产出且后缀生效', made,
        ['c1_HDR.mp4', 'c2_HDR.mp4', 'c3_HDR.mp4'])

    # 已存在 + 无 --overwrite → 必须跳过（而不是报错或重编）
    rc, out = run_script(['--input', str(BATCH_IN), '--output', str(BATCH_OUT),
                          '-r', '--no-model', '--preset', 'ultrafast',
                          '--suffix', '_HDR'])
    chk('⑧ 已存在且未给 --overwrite：退出码仍 0', rc, 0)
    chk_in('⑧ 报为跳过', '跳过', out)

    # 单文件分段并行：时长/帧数必须守恒（段边界允许极小漂移）
    SEG_SRC = WORK / 'seg_src.mp4'
    if not SEG_SRC.exists():
        subprocess.run(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
             '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=5:duration=22',
             '-f', 'lavfi', '-i', 'sine=frequency=440:duration=22',
             '-c:v', 'libx264', '-crf', '30', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-shortest', str(SEG_SRC)], check=True,
            capture_output=True)
    seg_out = WORK / 'seg_out.mp4'
    rc, out = run_script(['--input', str(SEG_SRC), '--output', str(seg_out),
                          '--no-model', '--preset', 'ultrafast',
                          '--split-mode', 'segment', '--workers', '2',
                          '--overwrite'])
    chk('⑧ 分段并行退出码', rc, 0)
    chk('⑧ 分段并行确实分了段', '分段并行' in out, True)

    def _dur_frames(p):
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=nb_frames',
                            '-show_entries', 'format=duration',
                            '-of', 'json', str(p)],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        d = json.loads(r.stdout or '{}')
        v = (d.get('streams') or [{}])[0]
        return float((d.get('format') or {}).get('duration') or 0), int(v.get('nb_frames') or 0)

    d_src, f_src = _dur_frames(SEG_SRC)
    d_out, f_out = _dur_frames(seg_out)
    chk('⑧ 分段并行：帧数守恒', f_out, f_src)
    chk_true('⑧ 分段并行：时长漂移 < 0.2s（段边界重编码所致）',
             abs(d_out - d_src) < 0.2, f'源 {d_src:.3f}s 输出 {d_out:.3f}s')

    # 分段产物的 HDR10 标签必须齐（-c copy 拼接不得丢元数据）
    pr = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json',
                         '-show_streams', str(seg_out)],
                        capture_output=True, text=True, encoding='utf-8',
                        errors='replace')
    vs = next((s for s in json.loads(pr.stdout)['streams']
               if s.get('codec_type') == 'video'), {})
    chk('⑧ 分段产物仍是 HEVC Main10', (vs.get('codec_name'), vs.get('profile')),
        ('hevc', 'Main 10'))
    chk('⑧ 分段产物色彩标签不被拼接破坏',
        (vs.get('color_space'), vs.get('color_transfer'), vs.get('color_primaries')),
        ('bt2020nc', 'smpte2084', 'bt2020'))
else:
    print('── ⑧ 批处理与单文件并行：跳过（--no-encode）──')

print()
if fails:
    print(f'✗ {len(fails)} 项不通过：')
    for f in fails:
        print('  ✗ ' + f)
    sys.exit(1)
print('✓ 全部通过')
