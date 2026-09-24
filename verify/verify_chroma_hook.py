# verify/verify_chroma_hook.py — 验证「产物色度自检」钩子（防复发）
#
# 背景（2026-09-22，见 memory/project_green_chroma_defect.md）：
#   色度归零缺陷（产物全绿）线下跑了几小时才被发现，因为当时只有 ffmpeg 的 rc、
#   没有"像素对不对"的检查。钩子 `_chroma_check` / `_chroma_verdict` 就是补这个。
#
# 本脚本三件事：
#   ① 阈值矩阵（纯函数，不需要 ffmpeg）——正常/灰度/纯绿/源退化/黑帧都不该误伤；
#   ② 真实文件取样（ffmpeg，不需要 GPU）——人造零色度片必须判失败、正常片不误伤；
#   ③ 降级链（需要 GPU）——把自检强制判失败后，应退到软编策略仍产出正常文件。
#
# 用法: python verify/verify_chroma_hook.py
import contextlib
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import vidcrop_hwaccel as V

TMP = ROOT / 'temp'
TMP.mkdir(parents=True, exist_ok=True)
SRC = TMP / 'fixture_1080p.mp4'
ZERO = TMP / 'fixture_zerochroma.mp4'
GRAY = TMP / 'fixture_gray.mp4'


def gen(path, vf):
    if path.exists():
        return
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=768x576:rate=30:duration=2',
                    '-vf', vf, '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', str(path)], check=True)


if not SRC.exists():
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25:duration=1',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    str(SRC)], check=True)
gen(ZERO, 'format=yuv420p,lut=y=val:u=0:v=0')   # 只把色度清零，Y 保持
gen(GRAY, 'format=gray,format=yuv420p')         # 真灰度：U=V=128（中性，不是 0）

fails = []


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got!r}')
        print(f'        want: {want!r}')
        fails.append(label)


NORMAL = (140.0, 127.0, 122.0)   # 正常：U/V 都在 128 附近
print('── ① 阈值矩阵（纯函数）──')
chk('本缺陷 U=V≈0 → 判失败', V._chroma_verdict(NORMAL, (140, 0.003, 0.003))[0], False)
chk('正常片 → 判通过', V._chroma_verdict(NORMAL, (140, 126, 121))[0], True)
chk('真灰度 U=V=128 → 不误伤', V._chroma_verdict(NORMAL, (140, 128, 128))[0], True)
chk('满屏纯绿 RGB(0,255,0) U≈54 → 不误伤', V._chroma_verdict(NORMAL, (144, 54, 0))[0], True)
chk('源本身退化 → 不判定', V._chroma_verdict((140, 3, 4), (140, 0, 0))[0], True)
chk('黑帧 Y<16 → 不判定（算通过）', V._chroma_verdict((16, 127, 122), (15.9, 0, 0))[0], True)

print('── ② 采样点 clamp(时长×0.1, 1, 60) ──')
chk('1448s → 60s（上限）', V._chroma_sample_ss({'format': {'duration': '1448.32'}}), 60.0)
chk('30s → 3s', V._chroma_sample_ss({'format': {'duration': '30'}}), 3.0)
chk('无时长 → 1s', V._chroma_sample_ss(None), 1.0)

print('── ③ 真实文件取样（ffmpeg，不需要 GPU）──')
src_uv = V._sample_uv_avg(SRC, 'ffmpeg', 0)
chk('源可取样', src_uv is not None, True)
chk('人造零色度片 → 判失败', V._chroma_check(SRC, ZERO, 'ffmpeg', 1)[0], False)
chk('真灰度片 → 不误伤', V._chroma_check(SRC, GRAY, 'ffmpeg', 1)[0], True)
chk('正常片自己对自己 → 通过', V._chroma_check(SRC, SRC, 'ffmpeg', 1)[0], True)

print('── ④ 降级链（需要 GPU，无则跳过）──')
caps = V.detect_cuda_capabilities('ffmpeg')
if not caps.has_nvenc('hevc_nvenc'):
    print('  (无 hevc_nvenc，跳过)')
else:
    orig = V._chroma_check
    calls = {'n': 0}

    def fake(*a, **k):
        # 只让**第一条策略**被判失败：这样第二条（软解/软编或纯 CPU）能真正跑完，
        # 验证的是"失败→降级→最终成功"，而不是"所有策略都被判失败"。
        calls['n'] += 1
        if calls['n'] == 1:
            return False, '人造失败（verify 用）'
        return orig(*a, **k)

    V._chroma_check = fake
    buf = io.StringIO()
    out_file = TMP / 'hook_downgrade_out.mp4'
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            res = V.process_file(
                input_file=SRC, output_path=out_file,
                orig_width=None, orig_height=None, out_width=640, out_height=360,
                codec='hevc_nvenc', crf=None, cq=23, preset='p5', overwrite=True,
                container=None, batch_mode=False, input_root=None,
                decode='auto', hw_caps=caps, crf_ref=None, cq_ref=None,
                ffmpeg_bin='ffmpeg', mode='crop', audio_codec='copy',
                audio_bitrate='128k', extra_args=None, no_skip_same_size=False,
                dry_run=False, file_index=1, file_total=1, flag=None,
                color_range=None, crop_ratio=None, scale_backend='auto', policy='auto',
            )
    finally:
        V._chroma_check = orig
    log = buf.getvalue()
    chk('自检失败触发了降级（日志里有「色度自检未通过」）',
        '色度自检未通过' in log, True)
    chk('降级后仍产出成功（status=done）', res['status'], 'done')
    okf, whyf = orig(SRC, out_file, 'ffmpeg', 1) if out_file.exists() else (False, '无产物')
    chk('降级产物色度正常', okf, True)

if fails:
    print(f'\n✘ {len(fails)} 项失败')
    sys.exit(1)
print('\n✔ 全部通过')
