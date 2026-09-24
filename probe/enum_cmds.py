#!/usr/bin/env python3
"""§4.1 本地物证：mock 出「远程 T4 的能力」，枚举 vidcrop_hwaccel 真正会下发的命令。

动机：本机没有 NVIDIA GPU，--dry-run 只能落到「策略 5 纯 CPU」，拿不到远程实际执行的
那条命令。而命令拼装是纯函数（只依赖 HardwareCapabilities 实例），所以 mock 一下就能
在本地复现远程的命令。

用法：
    python3 probe/enum_cmds.py                 # 全矩阵
    python3 probe/enum_cmds.py --only-default  # 只打印默认（crop + auto）那一条

素材：需要 `probe/src8.mp4`（8bit）与 `probe/src10.mp4`（10bit）各一份（不入库）。
缺素材会直接 exit 2，而不是静默跑出一片空白。
"""
import shlex
import sys
from pathlib import Path

# 仓库根 = 本脚本所在 probe/ 的父目录。别写死绝对路径：这份工装要在 Linux/T4 上跑，
# 旧版硬写 `D:\Workspace_Python\VidUtils`，且 `python probe/enum_cmds.py` 时 sys.path[0]
# 是 probe/ 而非仓库根 → `import vidcrop_hwaccel` 直接 ModuleNotFoundError（2026-09-23 实测）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vidcrop_hwaccel as V  # noqa: E402

HERE = Path(__file__).resolve().parent


def caps_t4(**kw):
    """伪造远程 T4 的能力（按 2026-09-20 实测的真实情况）。"""
    c = V.HardwareCapabilities()
    c.has_decoder = True
    c.has_encoder_h264 = True
    c.has_encoder_hevc = True
    c.has_crop_cuda = False          # 真实：FFmpeg 上游无 crop_cuda
    c.has_cuda_scale = True
    c.cuda_scale_upload_ok = True
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def enum(src: Path, mode='crop', decode='auto', codec='hevc_nvenc',
         ow=768, oh=432, scale_backend='auto', policy='auto'):
    caps = caps_t4()
    meta = V.probe_full_metadata(src, 'ffmpeg')
    d = meta['derived']
    rows = []
    for st in V._generate_strategies(codec, caps, decode=decode, mode=mode,
                                     scale_backend=scale_backend, policy=policy):
        vf = V.build_video_filter(
            mode, d['width'], d['height'], ow, oh,
            use_cuda=st.get('use_hw_filter', False),
            crop_ratio=None,
            cuda_scale=bool(st.get('cuda_scale', False)),
            cuda_upload=bool(st.get('hwupload', False)),
            src_bits=d['src_bits'])
        cmd = V.build_ffmpeg_cmd(
            src, Path('out.mp4'), vf, st['codec'],
            crf=None, cq=23, preset='p4', overwrite=True,
            hwaccel=st.get('hwaccel'),
            hwaccel_output_format=st.get('hwaccel_output_format'))
        rows.append({
            'name': st['name'],
            'bits': d['src_bits'],
            'hwaccel': st.get('hwaccel'),
            'hof': st.get('hwaccel_output_format'),
            'vf': vf,
            'cmd': shlex.join(cmd),
        })
    return d, rows


def main() -> int:
    only_default = '--only-default' in sys.argv
    found = False
    for src_name in ('src8.mp4', 'src10.mp4'):
        src = HERE / src_name
        if not src.exists():
            print(f'!! 缺素材 {src}', file=sys.stderr)
            continue
        found = True
        for mode in ('crop', 'cover'):
            for decode in ('auto', 'cpu', 'cuda'):
                if only_default and not (mode == 'crop' and decode == 'auto'):
                    continue
                d, rows = enum(src, mode=mode, decode=decode)
                print('=' * 78)
                print(f'源 {src_name} ({d["width"]}x{d["height"]} {d["src_bits"]}bit)'
                      f'  mode={mode}  --decode {decode}  --codec hevc_nvenc')
                for r in rows:
                    flag = []
                    if 'hwdownload' in r['vf']:
                        flag.append('显式hwdownload')
                    if r['hof']:
                        flag.append(f"hof={r['hof']}")
                    print(f'  ── {r["name"]}')
                    print(f'     -hwaccel={r["hwaccel"]}  -pix_fmt/hof={r["hof"]}'
                          f'  [{" / ".join(flag) or "无显式下载"}]')
                    print(f'     vf : {r["vf"]}')
                    print(f'     cmd: {r["cmd"]}')
    if not found:
        # fail-fast：没有素材就什么都枚举不出来。原来只是逐条 warn 然后 exit 0，
        # 看起来像"跑成功了但没输出"（本机 2026-09-23 实测踩到）。
        print(f'!! 没找到任何素材：本工具需要 {HERE}/src8.mp4（8bit）与 src10.mp4（10bit）'
              f'各一份，靠它们的真实元数据（尺寸/位深）来 mock 远程命令。', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
