#!/usr/bin/env python3
"""空载复测 D2 耗时比（svtav1 6->4、x265 slow->slower）。

只测时间，不跑 VMAF。方法：
  - 17 条真实 eqq 素材，crf 固定 24
  - 每 (素材, preset) 1 次预热 + 3 次计时，取中位
  - 并发上限 8；A/B 交错排队，避免机器忙闲漂移系统性偏袒某一侧
  - 每次计时前记录 loadavg，用于识别并剔除异常争用下的样本
  - 墙钟与 CPU(user+sys) 双记，互为交叉验证
"""
import json
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

POOL = '/workspace/input_videos/eqq_calib'
OUTDIR = ('/workspace/VidUtils/Accessory/calib/eqq_evidence/'
          'q3_bench/cpu_early/timeonly')
ANCHOR = 24
WARMUP = 1
RUNS = 3
CONC = 8

MATERIALS = [
    '6s/cganim_edu_wordworld_src720x576.mp4',
    '6s/cganim_subs_src1920x1080.mp4',
    '6s/cganim_talking_tom_src1920x1080.mp4',
    '6s/doc_dark_earth_src3840x2160.mp4',
    '6s/doc_grassland_src1280x720.mp4',
    '6s/live_kids_play_src1280x720.mp4',
    '6s/live_kids_seated_src1920x1080.mp4',
    '6s/live_kids_table_src1920x1080.mp4',
    '6s/screen_ui_code_src1280x720.mp4',
    '6s/tv_bbc_molly_s01e01_src1920x1080.mp4',
    '6s/tv_bbc_molly_s03e01_src1920x1080.mp4',
    '6s/tv_bbc_molly_s05e01_src1920x1080.mp4',
    '10s/anim2d_forest_src1280x720.mp4',
    '10s/anim2d_subs_tobot_src1280x720.mp4',
    '10s/live_night_wolf_src1280x720.mp4',
    '10s/live_texture_frog_src1280x720.mp4',
    '10s/screen_ui_code_src1280x720.mp4',
]

PAIRS = [
    {'codec': 'libsvtav1', 'a': '6', 'b': '4'},
    {'codec': 'libx265', 'a': 'slow', 'b': 'slower'},
]

def loadavg():
    with open('/proc/loadavg') as f:
        return f.read().split()[:3]


def parse_time_file(path):
    """解析 /usr/bin/time -v 输出。

    ⚠ Elapsed 行形如 `Elapsed (wall clock) time (h:mm:ss or m:ss): 2:01.15`，
    **标签本身含冒号**，不能按 ':' split 后取最后一段——那会把 2:01.15 读成 1.15 秒
    （丢掉分钟数）。必须按最后一个 ': ' 切开标签与值。
    本函数第一版就踩了这个坑，导致墙钟列出现 2.1s 配 574.5s CPU 的不可能值。
    """
    wall = user = sysv = pct = None
    with open(path) as f:
        for line in f:
            if ': ' not in line:
                continue
            label, val = line.rsplit(': ', 1)
            label = label.strip()
            val = val.strip()
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


def one_encode(codec, preset, src, dst, tfile):
    cmd = ['/usr/bin/time', '-v', '-o', tfile,
           'ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', src, '-c:v', codec, '-crf', str(ANCHOR),
           '-preset', preset, dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    wall, user, sysv, pct = parse_time_file(tfile)
    if wall is None:
        return None
    return {
        'wall_s': wall,
        'cpu_s': (user or 0.0) + (sysv or 0.0),
        'cores_used': (pct / 100.0) if pct is not None else None,
        'load_before': loadavg(),
        'bytes': os.path.getsize(dst) if os.path.exists(dst) else -1,
    }


def run_cell(codec, preset, mat, workdir, tag):
    src = os.path.join(POOL, mat)
    cell = os.path.join(workdir, tag)
    os.makedirs(cell, exist_ok=True)
    dst = os.path.join(cell, 'out.mkv')
    # 预热
    one_encode(codec, preset, src, dst, os.path.join(cell, 'warm.time'))
    runs = []
    for i in range(RUNS):
        tf = os.path.join(cell, 'run%d.time' % i)
        r = one_encode(codec, preset, src, dst, tf)
        if r is not None:
            r['idx'] = i
            runs.append(r)
        if os.path.exists(dst):
            os.remove(dst)
    if not runs:
        return None
    return {
        'material': mat, 'preset': preset,
        'wall_median': statistics.median([x['wall_s'] for x in runs]),
        'cpu_median': statistics.median([x['cpu_s'] for x in runs]),
        'wall_spread': max(x['wall_s'] for x in runs) - min(x['wall_s'] for x in runs),
        'load_at_runs': [x['load_before'][0] for x in runs],
        'bytes': runs[0]['bytes'],
        'n_runs': len(runs),
    }


def measure(pair):
    codec = pair['codec']
    workdir = os.path.join(OUTDIR, codec + '_work')
    os.makedirs(workdir, exist_ok=True)
    # A/B 交错排队
    jobs = []
    for mat in MATERIALS:
        for side in ('a', 'b'):
            jobs.append((mat, side, pair[side]))
    results = {}

    def work(job):
        mat, side, preset = job
        tag = '%s__%s' % (mat.replace('/', '_').replace('.mp4', ''), side)
        return (mat, side, run_cell(codec, preset, mat, workdir, tag))

    with ThreadPoolExecutor(max_workers=CONC) as ex:
        for mat, side, res in ex.map(work, jobs):
            if res is not None:
                results[(mat, side)] = res
    return {'codec': codec, 'pair': pair, 'cells': results}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    env0 = {
        'load_at_start': loadavg(),
        'cores': os.cpu_count(),
        'concurrency': CONC,
        'warmup': WARMUP,
        'runs': RUNS,
        'anchor_crf': ANCHOR,
        'ffmpeg': subprocess.run(['ffmpeg', '-version'], capture_output=True,
                                 text=True).stdout.splitlines()[0],
    }
    out = {'environment': env0, 'legs': []}
    for pair in PAIRS:
        print('[measure] %s %s vs %s' % (pair['codec'], pair['a'], pair['b']),
              flush=True)
        leg = measure(pair)
        out['legs'].append(leg)
        print('[measure] %s done, %d cells' % (pair['codec'], len(leg['cells'])),
              flush=True)
    out['environment']['load_at_end'] = loadavg()
    path = os.path.join(OUTDIR, 'retime_raw.json')
    with open(path, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('[measure] wrote', path, flush=True)


if __name__ == '__main__':
    sys.exit(main())
