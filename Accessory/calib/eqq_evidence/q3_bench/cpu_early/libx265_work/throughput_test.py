import subprocess, time, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_driver import timed

SRC = "/workspace/input_videos/eqq_calib/6s/live_kids_seated_src1920x1080.mp4"
PRESET = sys.argv[1] if len(sys.argv) > 1 else "slow"
CRF = "21"
OUT = "/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early/libx265_work/tt_%s.mp4" % PRESET

def enc_cmd(dst):
    return ["ffmpeg","-nostdin","-y","-hide_banner","-loglevel","error","-i",SRC,"-an",
            "-c:v","libx265","-crf",CRF,"-preset",PRESET,dst]

def batch(n):
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(timed, enc_cmd(OUT + ".%d" % i), "/tmp/tt_%s_%d.time" % (PRESET, i)) for i in range(n)]
        res = [f.result() for f in futs]
    wall = time.perf_counter() - t0
    cpu = sum(r.get("cpu_s") or 0 for r in res)
    walls = [r.get("wall_s") for r in res]
    print("N=%d preset=%s: batch_wall=%.1fs total_cpu=%.1fs aggregate_cores=%.2f per_proc_wall=%s rc=%s"
          % (n, PRESET, wall, cpu, cpu / wall, ["%.1f" % w for w in walls], [r["rc"] for r in res]), flush=True)

for n in [1, 2, 3, 4]:
    batch(n)
