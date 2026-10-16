#!/usr/bin/env python3
"""preset A/B 耗时专测 —— 设计给「换到支持 AVX2 的机器上复测」用。

背景：VidUtils `--quality-tier` 的 D2 分化（archive 慢档 / quality 快档）需要知道
preset 相对耗时。本机（Xeon E5-2620 v2，Ivy Bridge）**无 AVX2**，x265 自报只用
`MMX2 SSE2Fast SSSE3 SSE4.2 AVX`，导致 slow→slower 的 CPU 时间比高达 4.67×，
远高于现代 AVX2 机器的通常水平（1.5~2.5×）。这个比值是**机器特有**的，不能直接
外推，所以需要在目标机器上重测。

本脚本特点：
  * **自动检测环境**：读 /proc/cpuinfo 的 avx2/avx512 标记，并以 x265 自己打印的
    `using cpu capabilities:` 行为权威判据（它反映 x265 实际用的指令集，比 cpuinfo
    更可靠）。检测结果写入报告，并明确标注「比值不可跨 asm 级别比较」。
  * **负载守卫**：开测前检查 loadavg，超过 --max-load 直接拒绝跑——白天那轮就是被
    外部 session 抢占到负载 77/24 核，整列耗时应数据作废。
  * **CPU 时间为主判据**：墙钟会被「这一秒分到几核」扭曲（实测 B 侧系统性多拿
    8.7%~13.6% 核，把墙钟比压低了 12%~14%）。CPU(user+sys) 衡量真实工作量。
  * 每次计时前记录 loadavg，便于事后剔除异常样本。

用法：
  python3 bench_preset_ab.py                          # 默认 libx265 slow vs slower
  python3 bench_preset_ab.py --codec libsvtav1 --preset-a 6 --preset-b 4
  python3 bench_preset_ab.py --runs 5 --concurrency 4  # 更稳/更少争用
  python3 bench_preset_ab.py --max-load 8             # 负载超过 8 就拒绝跑
"""
import argparse
import glob
import json
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# ── 本机基线（Xeon E5-2620 v2，无 AVX2，2026-10-15 实测）──────────────────
# 用于在报告里做「可比性」提示，不是判据。
BASELINE = {
    'libx265': {
        'presets': ('slow', 'slower'), 'anchor': 24,
        'cpu_ratio_geomean': 4.6686, 'cpu_ratio_median': 4.703,
        'cpu_ratio_range': (3.637, 5.530),
        'wall_ratio_geomean': 3.8588,
        'asm': 'MMX2 SSE2Fast SSSE3 SSE4.2 AVX',
        'machine': 'Intel Xeon E5-2620 v2 @ 2.10GHz (24 核, 无 AVX2)',
        'bitrate_gain_pct': 10.5,
    },
    'libsvtav1': {
        'presets': ('6', '4'), 'anchor': 24,
        'cpu_ratio_geomean': 1.6822, 'cpu_ratio_median': 1.669,
        'cpu_ratio_range': (1.492, 1.905),
        'wall_ratio_geomean': 1.4737,
        'asm': None,
        'machine': '同上',
        'bitrate_gain_pct': 19.6,
    },
}


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def loadavg():
    with open('/proc/loadavg') as f:
        return [float(x) for x in f.read().split()[:3]]


def cpu_model():
    for line in open('/proc/cpuinfo'):
        if line.startswith('model name'):
            return line.split(':', 1)[1].strip()
    return 'unknown'


def cpu_flags():
    """从 /proc/cpuinfo 取指令集标记（去重）。"""
    flags = set()
    for line in open('/proc/cpuinfo'):
        if line.startswith('flags') or line.startswith('Features'):
            flags.update(line.split(':', 1)[1].split())
    return flags


def x265_asm():
    """跑一次极短编码，抓 x265 自报的 `using cpu capabilities:`。

    这比 /proc/cpuinfo 更权威：它反映 x265 **实际使用**的指令集
    （构建时若未启用某 asm 路径，cpuinfo 有该 flag 也用不上）。
    """
    r = subprocess.run(
        ['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'info',
         '-f', 'lavfi', '-i', 'testsrc2=size=256x144:rate=1:duration=1',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-crf', '30', '-f', 'null', '-'],
        capture_output=True, text=True)
    for line in (r.stderr or '').splitlines():
        m = re.search(r'using cpu capabilities:\s*(.+)$', line)
        if m:
            return m.group(1).strip()
    return None


def parse_time_file(path):
    """解析 /usr/bin/time -v。

    ⚠ Elapsed 行标签本身含冒号（`(h:mm:ss or m:ss)`），不能按 ':' split 取末段，
    否则 2:01.15 会被读成 1.15 秒。必须 rsplit(': ', 1)。
    """
    wall = user = sysv = pct = None
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
            elif label.startswith('Percent of CPU'):
                pct = float(val.replace('%', ''))
            elif label.startswith('Elapsed'):
                secs = 0.0
                for part in val.split(':'):
                    secs = secs * 60 + float(part)
                wall = secs
    return wall, user, sysv, pct


def one_encode(codec, preset, anchor, src, dst, tfile):
    cmd = ['/usr/bin/time', '-v', '-o', tfile,
           'ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', src, '-c:v', codec, '-crf', str(anchor), '-preset', preset, dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    wall, user, sysv, pct = parse_time_file(tfile)
    if wall is None:
        return None
    return {'wall_s': wall, 'cpu_s': (user or 0) + (sysv or 0),
            'cores': (pct / 100.0) if pct else None,
            'load_before': loadavg(),
            'bytes': os.path.getsize(dst) if os.path.exists(dst) else -1}


def measure_cell(codec, preset, anchor, src, workdir, tag, runs, warmup):
    cell = os.path.join(workdir, tag)
    os.makedirs(cell, exist_ok=True)
    dst = os.path.join(cell, 'out.mkv')
    for _ in range(warmup):
        one_encode(codec, preset, anchor, src, dst,
                   os.path.join(cell, 'warm.time'))
    got = []
    for i in range(runs):
        r = one_encode(codec, preset, anchor, src, dst,
                       os.path.join(cell, 'run%d.time' % i))
        if r:
            r['idx'] = i
            got.append(r)
        if os.path.exists(dst):
            os.remove(dst)
    if not got:
        return None
    return {
        'wall_median': statistics.median([x['wall_s'] for x in got]),
        'cpu_median': statistics.median([x['cpu_s'] for x in got]),
        'cores_median': statistics.median(
            [x['cores'] for x in got if x['cores'] is not None]),
        'wall_spread': max(x['wall_s'] for x in got) - min(x['wall_s'] for x in got),
        'loads': [x['load_before'][0] for x in got],
        'bytes': got[0]['bytes'],
        'n_runs': len(got),
    }


def main():
    ap = argparse.ArgumentParser(
        description='preset A/B 耗时专测（自动检测 AVX2 等环境）')
    ap.add_argument('--codec', default='libx265')
    ap.add_argument('--preset-a', default='slow')
    ap.add_argument('--preset-b', default='slower')
    ap.add_argument('--anchor', type=int, default=24)
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--warmup', type=int, default=1)
    ap.add_argument('--concurrency', type=int, default=8)
    ap.add_argument('--pool', default='/workspace/input_videos/eqq_calib')
    ap.add_argument('--max-load', type=float, default=8.0,
                    help='开测时 1 分钟负载超过此值即拒绝跑（防外部争用污染）')
    ap.add_argument('--outdir', default=None)
    args = ap.parse_args()

    outdir = args.outdir or os.path.join(
        '/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench',
        'preset_ab_%s' % args.codec)
    os.makedirs(outdir, exist_ok=True)

    # ── 环境指纹 ───────────────────────────────────────────────
    flags = cpu_flags()
    asm = x265_asm() if args.codec == 'libx265' else None
    avx2_cpu = 'avx2' in flags
    avx512_cpu = 'avx512f' in flags
    avx2_used = bool(asm and 'AVX2' in asm)
    env = {
        'cpu_model': cpu_model(),
        'cores': os.cpu_count(),
        'cpuinfo_avx2': avx2_cpu,
        'cpuinfo_avx512f': avx512_cpu,
        'x265_asm': asm,
        'x265_uses_avx2': avx2_used,
        'ffmpeg': sh(['ffmpeg', '-version']).splitlines()[0],
        'load_at_start': loadavg(),
        'concurrency': args.concurrency,
        'runs_per_cell': args.runs,
        'warmup': args.warmup,
        'anchor_crf': args.anchor,
    }

    print('=' * 72)
    print('环境指纹')
    print('  CPU        : %s（%d 核）' % (env['cpu_model'], env['cores']))
    print('  cpuinfo    : avx2=%s avx512f=%s' % (avx2_cpu, avx512_cpu))
    if asm:
        print('  x265 asm   : %s' % asm)
        print('  ⇒ x265 实际使用 AVX2：%s' % avx2_used)
    print('  ffmpeg     : %s' % env['ffmpeg'])
    print('  load(1/5/15): %s' % ['%.1f' % x for x in env['load_at_start']])

    # ── 负载守卫 ────────────────────────────────────────────────
    if env['load_at_start'][0] > args.max_load:
        print('\n[拒绝] 开测时 1 分钟负载 %.1f 超过 --max-load %.1f。'
              % (env['load_at_start'][0], args.max_load))
        print('       外部争用会污染耗时应列（白天那轮就是这样作废的）。')
        print('       等机器空闲，或用 --max-load 放宽。')
        return 2
    print('\n[OK] 负载 %.1f ≤ %.1f，开测。'
          % (env['load_at_start'][0], args.max_load))

    if not avx2_used and args.codec == 'libx265':
        print('\n[注意] 本机 x265 未使用 AVX2。')
        print('       slow→slower 的 CPU 时间比在无 AVX2 时会被显著放大')
        print('       （E5-2620 v2 实测 4.67×，AVX2 机器通常 1.5~2.5×）。')
        print('       本结果**不可**与 AVX2 机器的结果直接比较。')

    # ── 素材 ────────────────────────────────────────────────────
    mats = sorted(glob.glob(os.path.join(args.pool, '*', '*.mp4')))
    if not mats:
        print('[错误] %s 下没有素材' % args.pool)
        return 2
    print('\n素材 %d 条，锚点 crf %d，A=%s B=%s，每格 %d 预热 + %d 计时'
          % (len(mats), args.anchor, args.preset_a, args.preset_b,
             args.warmup, args.runs))

    workdir = os.path.join(outdir, 'work')
    os.makedirs(workdir, exist_ok=True)
    jobs = []
    for m in mats:
        for side in ('a', 'b'):
            jobs.append((m, side, args.preset_a if side == 'a' else args.preset_b))

    results = {}

    def work(job):
        m, side, preset = job
        # ⚠ tag 必须含父目录：素材池曾有一条 basename 在 10s/ 与 6s/ 下同名
        # （screen_ui_code_src1280x720.mp4），basename 当 tag 会让两条素材的单元目录
        # 互相覆盖（并发下还会 races）。用相对路径保证 (目录, 文件名, 侧别) 唯一。
        tag = '%s__%s' % (os.path.relpath(m, args.pool).replace('.mp4', '')
                          .replace(os.sep, '__'), side)
        return m, side, measure_cell(args.codec, preset, args.anchor, m,
                                     workdir, tag, args.runs, args.warmup)

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for m, side, res in ex.map(work, jobs):
            if res:
                results[(m, side)] = res

    # ── 汇总 ────────────────────────────────────────────────────
    env['load_at_end'] = loadavg()
    rows = []
    for m in mats:
        a = results.get((m, 'a'))
        b = results.get((m, 'b'))
        if a and b:
            rows.append({
                'material': os.path.relpath(m, args.pool),
                'wall_a_s': a['wall_median'], 'wall_b_s': b['wall_median'],
                'cpu_a_s': a['cpu_median'], 'cpu_b_s': b['cpu_median'],
                'cores_a': a['cores_median'], 'cores_b': b['cores_median'],
                'wall_ratio': b['wall_median'] / a['wall_median'],
                'cpu_ratio': b['cpu_median'] / a['cpu_median'],
                'wall_spread': max(a['wall_spread'], b['wall_spread']),
            })

    def geomean(xs):
        import math
        return math.exp(sum(math.log(x) for x in xs) / len(xs))

    wr = [r['wall_ratio'] for r in rows]
    cr = [r['cpu_ratio'] for r in rows]
    report = {
        'environment': env,
        'params': {'codec': args.codec, 'preset_a': args.preset_a,
                   'preset_b': args.preset_b, 'anchor': args.anchor,
                   'runs': args.runs, 'warmup': args.warmup},
        'n_materials': len(rows),
        'per_material': rows,
        'summary': {
            'wall_ratio_geomean': geomean(wr) if wr else None,
            'cpu_ratio_geomean': geomean(cr) if cr else None,
            'cpu_ratio_median': statistics.median(cr) if cr else None,
            'cpu_ratio_min': min(cr) if cr else None,
            'cpu_ratio_max': max(cr) if cr else None,
            'cores_a_median': statistics.median([r['cores_a'] for r in rows]) if rows else None,
            'cores_b_median': statistics.median([r['cores_b'] for r in rows]) if rows else None,
            'verdict_vs_1_5x': ('over' if geomean(cr) >= 1.5 else 'under') if cr else None,
        },
    }

    path = os.path.join(outdir, 'preset_ab_report.json')
    with open(path, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print('\n' + '=' * 72)
    print('结果（%d 素材）' % len(rows))
    print('%-38s %8s %8s %7s %7s' % ('素材', 'A墙钟', 'B墙钟', '墙钟比', 'CPU比'))
    for r in rows:
        print('%-38s %8.1f %8.1f %7.3f %7.3f'
              % (r['material'][:36], r['wall_a_s'], r['wall_b_s'],
                 r['wall_ratio'], r['cpu_ratio']))
    s = report['summary']
    print('-' * 72)
    print('CPU  比 几何平均 %.4f（%+.1f%%） 中位 %.3f 范围 %.3f~%.3f'
          % (s['cpu_ratio_geomean'], (s['cpu_ratio_geomean'] - 1) * 100,
             s['cpu_ratio_median'], s['cpu_ratio_min'], s['cpu_ratio_max']))
    print('墙钟比 几何平均 %.4f（%+.1f%%）'
          % (s['wall_ratio_geomean'], (s['wall_ratio_geomean'] - 1) * 100))
    print('A 侧 %.2f 核 / B 侧 %.2f 核 ⇒ 墙钟比被压低 %.1f%%'
          % (s['cores_a_median'], s['cores_b_median'],
             (s['cores_b_median'] / s['cores_a_median'] - 1) * 100))
    print('阈值 1.5x：CPU 比 %.3f ⇒ %s'
          % (s['cpu_ratio_geomean'], s['verdict_vs_1_5x']))

    # ── 与已知基线对比 ──────────────────────────────────────────
    base = BASELINE.get(args.codec)
    if base and base['presets'] == (args.preset_a, args.preset_b):
        print('\n' + '=' * 72)
        print('与 E5-2620 v2（无 AVX2）基线对比')
        print('  基线 CPU 比 %.3f / 本机 %.3f'
              % (base['cpu_ratio_geomean'], s['cpu_ratio_geomean']))
        if avx2_used:
            print('  本机 x265 使用 AVX2 ⇒ 若本机比值明显低于基线，符合预期')
            print('  （AVX2 缓解 slower 的 SIMD 惩罚）；若仍接近，说明该 preset 对')
            print('  SIMD 不敏感，基线可直接外推。')
        else:
            print('  本机也未用 AVX2 ⇒ 与基线同类机器，结果应接近。')

    print('\n报告已写：%s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
