#!/usr/bin/env python3
"""AVX2 影响的反证实验（同机、只改 x265 的 asm 掩码）。

动机：`bench_preset_ab.py` 的环境探针只证明「x265 自报会用 AVX2」，不能证明
AVX2 对 slow→slower 的**耗时比**有可测影响。本脚本用**同机对照**把 AVX2 关掉，
看绝对 CPU 时间和预设比值各自变化多少。

方法：x265 支持 `-x265-params asm=<整数>` 限制指令集，但**它不是朴素位掩码**，
实测（本机 8255C，slow preset）：
    asm=255  → MMX2 SSE2 LZCNT SSSE3 SSE4.1
    asm=511  → MMX2 SSE2 LZCNT SSSE3 SSE4.2
    asm=1023 → MMX2 SSE2 LZCNT SSSE3 SSE4.2 AVX      ← 本脚本用的「去 AVX2」臂
    asm=2047 → … AVX XOP           （XOP 是 AMD 专属，Intel 上 SIGILL, rc=132）
    asm=4095 → … AVX XOP FMA4      （同上，SIGILL）
    asm=0/1/256/426/2048 → 「none!」（这些位型被 x265 判为无效集合）
所以：
  * 臂 A（对照）：不传 asm → 自动检测（本机=到 AVX2）
  * 臂 B（去 AVX2）：asm=1023（到 AVX 为止；同时去掉 FMA3/BMI2）
  * 臂 C（可选）：asm=0 → 纯 C

⚠ 强开 XOP/FMA4 会 SIGILL —— 这是 Intel 上关 AVX2 时最容易踩的坑，脚本的
  「掩码自证」因此必须用真实 preset 跑 1 秒（ultrafast 走不到 XOP 原语会漏判）。

判据：CPU(user+sys) 中位数。分别给出
  * 绝对代价：ΔCPU = CPU(去AVX2)/CPU(自动) − 1
  * 比值漂移：ratio_b / ratio_a − 1，其中 ratio = CPU(slower)/CPU(slow)
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time


def loadavg():
    with open('/proc/loadavg') as f:
        return [float(x) for x in f.read().split()[:3]]


def parse_time_file(path):
    wall = user = sysv = None
    with open(path) as f:
        for line in f:
            if ': ' not in line:
                continue
            label, val = line.rsplit(': ', 1)
            label, val = label.strip(), val.strip()
            if label.startswith('User time'):
                user = float(val)
            elif label.startswith('System time'):
                sysv = float(val)
            elif label.startswith('Elapsed'):
                secs = 0.0
                for part in val.split(':'):
                    secs = secs * 60 + float(part)
                wall = secs
    return wall, user, sysv


def asm_probe(mask, clip, preset='slow'):
    """用**真实素材 + 与实验同档 preset** 跑 1 秒编码，返回 (能力串, rc)。

    ⚠ 必须用真实 preset（不能 ultrafast）：掩码若强开了本机不存在的指令集
    （如把 AMD 的 XOP/FMA4 位打开），只有 slow/slower 的 RD 路径才会真正执行到
    相应原语并触发 SIGILL(132)；ultrafast 走不到那个原语，会**漏判**（本脚本
    第一版就因此跑了 10min 全 132 却浑然不觉）。
    """
    cmd = ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'info',
           '-t', '1', '-i', clip, '-c:v', 'libx265', '-crf', '30',
           '-preset', preset]
    if mask is not None:
        cmd += ['-x265-params', 'asm=%d' % mask]
    cmd += ['-f', 'null', '-']
    r = subprocess.run(cmd, capture_output=True, text=True)
    cap = '(none / 无 asm)'
    for line in (r.stderr or '').splitlines():
        m = re.search(r'using cpu capabilities:\s*(.+)$', line)
        if m:
            cap = m.group(1).strip()
    return cap, r.returncode


def one_encode(clip, preset, crf, mask, tfile, dst):
    cmd = ['/usr/bin/time', '-v', '-o', tfile,
           'ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', clip, '-c:v', 'libx265', '-crf', str(crf), '-preset', preset]
    if mask is not None:
        cmd += ['-x265-params', 'asm=%d' % mask]
    cmd += [dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    wall, user, sysv = parse_time_file(tfile)
    if wall is None:
        return None
    return {'wall': wall, 'cpu': (user or 0) + (sysv or 0), 'load': loadavg()}


def cell(clip, preset, crf, mask, workdir, tag, runs, warmup):
    d = os.path.join(workdir, tag)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'out.mkv')
    for _ in range(warmup):
        one_encode(clip, preset, crf, mask, os.path.join(d, 'warm.time'), dst)
    got = []
    for i in range(runs):
        r = one_encode(clip, preset, crf, mask,
                       os.path.join(d, 'run%d.time' % i), dst)
        if r:
            got.append(r)
    if not got:
        print('  [失败] %s：0/%d 次编码成功（掩码 %s）—— 检查是否 SIGILL'
              % (tag, runs, mask))
        return None
    return {'cpu_median': statistics.median([x['cpu'] for x in got]),
            'wall_median': statistics.median([x['wall'] for x in got]),
            'loads': [x['load'][0] for x in got], 'n': len(got)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--clip', default='/workspace/input_videos/eqq_calib/'
                    '10s/anim2d_forest_src1280x720.mp4')
    ap.add_argument('--crf', type=int, default=24)
    ap.add_argument('--presets', nargs='+', default=['slow', 'slower'])
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--warmup', type=int, default=1)
    ap.add_argument('--mask-noavx2', type=int, default=1023,
                    help='去 AVX2 的掩码（默认 1023=到 AVX 为止）')
    ap.add_argument('--mask-noasm', type=int, default=0,
                    help='纯 C 掩码（附加臂，设为 <0 可跳过）')
    ap.add_argument('--max-load', type=float, default=4.0)
    ap.add_argument('--outdir', default='/workspace/VidUtils/Accessory/calib/'
                    'eqq_evidence/q3_bench/avx2_falsify')
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    print('=' * 72)
    print('AVX2 反证实验')
    print('  clip   : %s' % args.clip)
    print('  crf    : %d   presets: %s' % (args.crf, args.presets))
    print('  runs   : %d (+%d 预热)' % (args.runs, args.warmup))
    print('  load   : %s' % ['%.2f' % x for x in loadavg()])

    if loadavg()[0] > args.max_load:
        print('[拒绝] 负载 %.2f > %.2f，机器不静，实验无效。'
              % (loadavg()[0], args.max_load))
        return 2

    # ── 掩码自证 ────────────────────────────────────────────────
    arms = {'auto': None, 'noavx2': args.mask_noavx2}
    if args.mask_noasm >= 0:
        arms['noasm'] = args.mask_noasm
    caps = {}
    rcs = {}
    print('\n掩码自证（x265 自报能力 + 1 秒真编 rc）：')
    for name, mask in arms.items():
        caps[name], rcs[name] = asm_probe(mask, args.clip, args.presets[0])
        print('  %-8s asm=%-6s rc=%-4s → %s'
              % (name, 'auto' if mask is None else mask, rcs[name], caps[name]))
    bad = [n for n, rc in rcs.items() if rc != 0]
    if bad:
        print('\n[错误] 以下臂编码 rc≠0（SIGILL=132 通常是强开了 XOP/FMA4 等'
              '本机不存在的指令集）：%s' % bad)
        return 2
    if 'AVX2' not in caps['auto']:
        print('\n[警告] 自动臂竟未含 AVX2，本机无法验证 AVX2 影响。')
    if 'AVX2' in caps.get('noavx2', ''):
        print('\n[错误] noavx2 臂仍含 AVX2，掩码无效，实验作废。')
        return 2
    print('[OK] 掩码生效：auto 含 AVX2，noavx2 不含，且两臂 rc=0。')

    # ── 计时 ────────────────────────────────────────────────────
    workdir = os.path.join(args.outdir, 'work')
    os.makedirs(workdir, exist_ok=True)
    res = {}
    print('\n开测（每格 %d 计时）…' % args.runs)
    for arm, mask in arms.items():
        for p in args.presets:
            t0 = time.time()
            r = cell(args.clip, p, args.crf, mask, workdir,
                     '%s__%s' % (arm, p), args.runs, args.warmup)
            res[(arm, p)] = r
            if r:
                print('  %-8s %-8s CPU中位 %8.2fs  墙钟 %7.2fs  (耗时 %.0fs)'
                      % (arm, p, r['cpu_median'], r['wall_median'],
                         time.time() - t0))

    # ── 汇总 ────────────────────────────────────────────────────
    report = {'clip': args.clip, 'crf': args.crf, 'arms': arms,
              'capabilities': caps, 'runs': args.runs,
              'results': {('%s|%s' % k): v for k, v in res.items()},
              'load_at_end': loadavg()}

    pa, pb = args.presets[0], args.presets[-1]
    print('\n' + '=' * 72)
    print('绝对代价（去 AVX2 / 自动 − 1）：')
    for p in args.presets:
        a = res.get(('auto', p))
        b = res.get(('noavx2', p))
        if a and b:
            d = b['cpu_median'] / a['cpu_median'] - 1
            print('  %-8s 自动 %8.2fs → 去AVX2 %8.2fs  = %+.1f%%'
                  % (p, a['cpu_median'], b['cpu_median'], d * 100))
            report.setdefault('delta_cpu_pct', {})[p] = d * 100

    def ratio(r, x, y):
        if res.get((r, x)) and res.get((r, y)):
            return res[(r, x)]['cpu_median'] / res[(r, y)]['cpu_median']
        return None

    ra = ratio('auto', pb, pa)
    rb = ratio('noavx2', pb, pa)
    report['ratio_auto'] = ra
    report['ratio_noavx2'] = rb
    print('\n预设比值 CPU(%s)/CPU(%s)：' % (pb, pa))
    if ra:
        print('  自动   : %.4f' % ra)
    if rb:
        print('  去AVX2 : %.4f' % rb)
    if ra and rb:
        print('  比值漂移: %+.1f%%  ⇒ %s'
              % ((rb / ra - 1) * 100,
                 'AVX2 对该比值影响可忽略（<5%）' if abs(rb / ra - 1) < 0.05
                 else 'AVX2 明显改变该比值，跨 asm 级别不可外推'))
        report['ratio_drift_pct'] = (rb / ra - 1) * 100

    path = os.path.join(args.outdir, 'avx2_falsify_report.json')
    with open(path, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print('\n报告已写：%s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
