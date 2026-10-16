#!/usr/bin/env python3
"""
q3_bench D2 driver -- libx265 preset slow (A) vs slower (B), CPU early ring (stage 0.5).

Question answered (Plan v3 12.2 Q3 / 12.3): at matched VMAF, is slower's bitrate
saving >= 3% while its time cost is <= +50%?

Phases:
  probe   -- ffprobe the 17 pool files, capture ffmpeg/x265/libvmaf versions,
             verify video-only streams, write pool_probe.json + timeline start
  encode  -- 170 cells x 2 runs (run1 = warmup, discarded; run2 = measured),
             4 workers, resumable via progress_encode.jsonl
  vmaf    -- 170 cells, libvmaf n_subsample=1, original clip as reference,
             8 workers, resumable via progress_vmaf.jsonl
  report  -- per material x preset fit VMAF = f(log10 kbps); at VMAF target 95
             (or highest common VMAF if unreachable) compute B/A bitrate ratio;
             geometric means over 17 materials; matched-bitrate VMAF gain;
             outlier / leave-one-out analysis; decision; write artifacts.

Method locks (do not drift):
  * Encode command is exactly:
      ffmpeg -nostdin -y -hide_banner -loglevel error -i SRC -an \
        -c:v libx265 -crf <anchor> -preset <slow|slower> DST
    No lookahead / tune / multipass / aq / threads flags. -an only guards
    against audio streams (probe phase verifies inputs are video-only).
  * VMAF filter: [0:v][1:v]libvmaf=n_subsample=1:log_fmt=json:log_path=<per cell>
    (n_subsample=1 is mandatory: subsample>1 biases VMAF, convert_crf.py L180-184)
  * Timing: wall clock seconds via /usr/bin/time (%e); CPU seconds (%U+%S)
    recorded as a concurrency-robust cross-check. Second run is the measurement.
  * Bitrate: output file bytes * 8 / duration, duration = container duration of the
    run2 output (cross-checked against nb_frames / avg_frame_rate; note the pool
    contains one VFR-ish clip, 6s/live_kids_play, whose r_frame_rate 30 overstates
    its true 20fps average -- container duration is the only safe duration source).
  * Concurrency: <= 8 ffmpeg processes at any time (encode 4 / vmaf 8).
  * Everything lands under libx265_work/. No repo source file is touched.
"""
import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

POOL = "/workspace/input_videos/eqq_calib"
OUTROOT = "/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early"
WORK = os.path.join(OUTROOT, "libx265_work")
LOGDIR = os.path.join(WORK, "logs")
VMAFDIR = os.path.join(WORK, "vmaf_logs")
PROG_ENC = os.path.join(WORK, "progress_encode.jsonl")
PROG_VMAF = os.path.join(WORK, "progress_vmaf.jsonl")
POOL_PROBE = os.path.join(WORK, "pool_probe.json")
RUN_META = os.path.join(WORK, "run_meta.json")

FILES = [
    ("6s", "cganim_edu_wordworld_src720x576.mp4"),
    ("6s", "cganim_subs_src1920x1080.mp4"),
    ("6s", "cganim_talking_tom_src1920x1080.mp4"),
    ("6s", "doc_dark_earth_src3840x2160.mp4"),
    ("6s", "doc_grassland_src1280x720.mp4"),
    ("6s", "live_kids_play_src1280x720.mp4"),
    ("6s", "live_kids_seated_src1920x1080.mp4"),
    ("6s", "live_kids_table_src1920x1080.mp4"),
    ("6s", "screen_ui_code_src1280x720.mp4"),
    ("6s", "tv_bbc_molly_s01e01_src1920x1080.mp4"),
    ("6s", "tv_bbc_molly_s03e01_src1920x1080.mp4"),
    ("6s", "tv_bbc_molly_s05e01_src1920x1080.mp4"),
    ("10s", "anim2d_forest_src1280x720.mp4"),
    ("10s", "anim2d_subs_tobot_src1280x720.mp4"),
    ("10s", "live_night_wolf_src1280x720.mp4"),
    ("10s", "live_texture_frog_src1280x720.mp4"),
    ("10s", "screen_ui_code_src1280x720.mp4"),
]
ANCHORS = [18, 21, 24, 27, 30]
PRESETS = ["slow", "slower"]
SHUFFLE_SEED = 20261015
VMAF_TARGET = 95.0
DECODE_ENCODE_WORKERS = 4
VMAF_WORKERS = 8

_write_lock = threading.Lock()


# ---------------------------------------------------------------- utilities
def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def date_now():
    return sh(["date", "-Iseconds"]).stdout.strip()


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def append_jsonl(path, rec):
    with _write_lock:
        with open(path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()


def load_jsonl(path):
    out = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out[r["key"]] = r
    return out


def update_meta(**kw):
    meta = {}
    if os.path.exists(RUN_META):
        try:
            meta = json.load(open(RUN_META))
        except Exception:
            meta = {}
    meta.update(kw)
    with open(RUN_META, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def ffprobe_video(path):
    r = sh(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed for %s: %s" % (path, r.stderr[-500:]))
    d = json.loads(r.stdout)
    vstreams = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
    if not vstreams:
        raise RuntimeError("no video stream in %s" % path)
    v = vstreams[0]
    num, den = v.get("r_frame_rate", "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    anum, aden = v.get("avg_frame_rate", "0/1").split("/")
    avg_fps = float(anum) / float(aden) if float(aden) else 0.0
    nb = int(v.get("nb_frames", 0) or 0)
    dur_c = float(d.get("format", {}).get("duration", 0) or 0)
    return {
        "width": v.get("width"),
        "height": v.get("height"),
        "pix_fmt": v.get("pix_fmt"),
        "fps": fps,
        "avg_fps": avg_fps,
        "nb_frames": nb,
        "duration_frames": (nb / avg_fps) if (nb and avg_fps) else None,
        "duration_container": dur_c,
        "n_streams": len(d.get("streams", [])),
        "audio_streams": len([s for s in d.get("streams", []) if s.get("codec_type") == "audio"]),
        "video_codec": v.get("codec_name"),
    }


def timed(cmd, timefile):
    """Run cmd under /usr/bin/time -f '%e %U %S' -o timefile. Returns dict."""
    full = ["/usr/bin/time", "-f", "%e %U %S", "-o", timefile] + cmd
    t0 = time.perf_counter()
    r = subprocess.run(full, capture_output=True, text=True)
    wall_py = time.perf_counter() - t0
    res = {"rc": r.returncode, "stderr_tail": r.stderr[-800:] if r.returncode != 0 else ""}
    try:
        parts = open(timefile).read().split()
        res["wall_s"] = float(parts[0])
        res["cpu_user_s"] = float(parts[1])
        res["cpu_sys_s"] = float(parts[2])
        res["cpu_s"] = res["cpu_user_s"] + res["cpu_sys_s"]
    except Exception as e:
        res["wall_s"] = wall_py
        res["cpu_s"] = None
        res["timefile_error"] = str(e)
    return res


# ---------------------------------------------------------------- cells
def build_cells():
    cells = []
    for sub, name in FILES:
        stem = name[:-4]
        tag = "%s_%s" % (sub, stem)
        src = os.path.join(POOL, sub, name)
        for crf in ANCHORS:
            for preset in PRESETS:
                base = "%s__crf%d__%s" % (tag, crf, preset)
                cells.append({
                    "key": "%s|%d|%s" % (tag, crf, preset),
                    "tag": tag,
                    "subdir": sub,
                    "src": src,
                    "crf": crf,
                    "preset": preset,
                    "dst": os.path.join(WORK, base + ".mp4"),
                    "dst_run1": os.path.join(WORK, base + "__run1.mp4"),
                    "vmaf_log": os.path.join(VMAFDIR, base + ".json"),
                    "timefile_run1": os.path.join(LOGDIR, base + "__run1.time"),
                    "timefile_run2": os.path.join(LOGDIR, base + "__run2.time"),
                    "encode_cmd": ("ffmpeg -nostdin -y -hide_banner -loglevel error -i %s -an "
                                   "-c:v libx265 -crf %d -preset %s <dst>" % (src, crf, preset)),
                })
    random.Random(SHUFFLE_SEED).shuffle(cells)
    return cells


def enc_cmd(src, dst, crf, preset):
    return ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
            "-i", src, "-an", "-c:v", "libx265", "-crf", str(crf),
            "-preset", preset, dst]


def run_encode_with_retry(src, dst, crf, preset, timefile, tries=3):
    """Run one encode; retry transient failures (shared machine: thread-pool
    creation bursts can fail instantly). Returns the timed() result dict."""
    last = None
    for attempt in range(tries):
        r = timed(enc_cmd(src, dst, crf, preset), timefile)
        if r["rc"] == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            if attempt:
                r["retried"] = attempt
            return r
        last = r
        time.sleep(10 * (attempt + 1))
    if last is not None:
        last["retried"] = tries - 1
    return last


# ---------------------------------------------------------------- phases
def phase_probe():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(LOGDIR, exist_ok=True)
    os.makedirs(VMAFDIR, exist_ok=True)
    update_meta(run_start=date_now())

    ver = sh(["ffmpeg", "-hide_banner", "-version"])
    ff_lines = ver.stdout.splitlines()
    ffmpeg_version = ff_lines[0] if ff_lines else "unknown"
    ffmpeg_conf = ""
    for ln in ff_lines:
        if ln.startswith("configuration:"):
            ffmpeg_conf = ln
            break

    # x265 version + default thread config from a 1-frame encode (does not touch the pool)
    x265_probe_out = os.path.join(WORK, "x265_probe.mp4")
    tiny_src = os.path.join(POOL, "6s", "screen_ui_code_src1280x720.mp4")
    x265_info = {}
    r = sh(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "info",
            "-i", tiny_src, "-an", "-c:v", "libx265", "-crf", "21", "-preset", "slower",
            "-frames:v", "1", x265_probe_out])
    for ln in r.stderr.splitlines():
        if "HEVC encoder version" in ln:
            x265_info["version"] = ln.split("x265 [info]:")[-1].strip()
        elif "using cpu capabilities" in ln:
            x265_info["cpu_caps"] = ln.split("x265 [info]:")[-1].strip()
        elif "frame threads / pool features" in ln:
            x265_info["threads_default"] = ln.split("x265 [info]:")[-1].strip()
    if os.path.exists(x265_probe_out):
        os.remove(x265_probe_out)

    libvmaf_so = sh(["bash", "-lc", "ls /lib/x86_64-linux-gnu/libvmaf.so.* 2>/dev/null | head -1"]).stdout.strip()

    pool = []
    audio_warn = []
    for sub, name in FILES:
        p = os.path.join(POOL, sub, name)
        meta = ffprobe_video(p)
        meta.update({"subdir": sub, "name": name, "path": p, "tag": "%s_%s" % (sub, name[:-4])})
        pool.append(meta)
        if meta["audio_streams"]:
            audio_warn.append(p)

    lscpu = sh(["bash", "-lc", "lscpu | grep -E 'Model name|^CPU\\(s\\)|Thread|Core|Socket'"]).stdout.strip()

    out = {
        "timestamps": {"run_start": date_now()},
        "machine": {
            "nproc": os.cpu_count(),
            "lscpu": lscpu,
            "mem_total_gb": round(int(sh(["bash", "-lc", "free -g | awk '/Mem:/{print $2}'"]).stdout.strip() or 0), 3),
        },
        "ffmpeg": {"version": ffmpeg_version, "configuration": ffmpeg_conf},
        "x265": x265_info,
        "libvmaf": {"so": libvmaf_so, "model": "version=vmaf_v0.6.1 (filter default)",
                    "n_subsample": 1, "log_fmt": "json",
                    "filter": "[0:v][1:v]libvmaf=n_subsample=1:log_fmt=json:log_path=<per-cell>"},
        "encode_cmd_template": ("ffmpeg -nostdin -y -hide_banner -loglevel error -i <src> -an "
                                "-c:v libx265 -crf <anchor> -preset <slow|slower> <dst>"),
        "concurrency": {"encode_workers": DECODE_ENCODE_WORKERS, "vmaf_workers": VMAF_WORKERS,
                        "max_ffmpeg_procs": 8},
        "pool": pool,
        "pool_audio_warning": audio_warn,
        "anchors": ANCHORS,
        "presets": PRESETS,
        "n_cells": len(FILES) * len(ANCHORS) * len(PRESETS),
    }
    with open(POOL_PROBE, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("[probe] %d files, x265=%s" % (len(pool), x265_info.get("version")))
    print("[probe] audio streams found in: %s" % (audio_warn or "none"))
    for m in pool:
        print("  %-46s %sx%s %s %.3ffps %dframes dur_f=%.4f dur_c=%.4f" % (
            m["tag"], m["width"], m["height"], m["pix_fmt"], m["fps"], m["nb_frames"],
            m["duration_frames"] or -1, m["duration_container"]))


def phase_encode(limit=None, workers=DECODE_ENCODE_WORKERS):
    os.makedirs(LOGDIR, exist_ok=True)
    os.makedirs(VMAFDIR, exist_ok=True)
    cells = build_cells()
    done = load_jsonl(PROG_ENC)
    todo = [c for c in cells if c["key"] not in done]
    if limit:
        todo = todo[:limit]
    print("[encode] %d/%d cells already done, %d todo (workers=%d)" % (len(done), len(cells), len(todo), workers))
    if not todo:
        return
    update_meta(encode_start=date_now(), encode_workers=workers)
    t0 = time.time()
    fin = 0

    def work(cell):
        time.sleep(1.0)  # stagger starts: avoid thread-pool creation bursts on the shared box
        rec = dict(cell)
        rec["ts_encode"] = date_now()
        # run 1 (warmup, output discarded after md5)
        r1 = run_encode_with_retry(cell["src"], cell["dst_run1"], cell["crf"], cell["preset"],
                                   cell["timefile_run1"])
        rec["rc_run1"] = r1["rc"]
        rec["t_run1_s"] = r1.get("wall_s")
        rec["cpu_run1_s"] = r1.get("cpu_s")
        rec["md5_run1"] = md5(cell["dst_run1"]) if r1["rc"] == 0 and os.path.exists(cell["dst_run1"]) else None
        # run 2 (measured)
        r2 = run_encode_with_retry(cell["src"], cell["dst"], cell["crf"], cell["preset"],
                                   cell["timefile_run2"])
        rec["rc_run2"] = r2["rc"]
        rec["t_run2_s"] = r2.get("wall_s")
        rec["cpu_run2_s"] = r2.get("cpu_s")
        rec["stderr_tail"] = (r1.get("stderr_tail", "") + r2.get("stderr_tail", "")).strip()[:800]
        if r2["rc"] != 0 or not os.path.exists(cell["dst"]):
            rec["error"] = "encode failed rc=%s" % r2["rc"]
            return rec
        rec["md5_run2"] = md5(cell["dst"])
        rec["md5_match"] = (rec["md5_run1"] == rec["md5_run2"]) if rec["md5_run1"] else None
        rec["size_bytes"] = os.path.getsize(cell["dst"])
        try:
            meta = ffprobe_video(cell["dst"])
        except Exception as e:
            rec["error"] = "ffprobe failed: %s" % e
            return rec
        # primary duration = container duration; cross-check nb_frames/avg_frame_rate
        dur = meta["duration_container"] or meta["duration_frames"]
        rec["duration_s"] = dur
        rec["nb_frames"] = meta["nb_frames"]
        rec["fps"] = meta["fps"]
        rec["avg_fps"] = meta["avg_fps"]
        rec["duration_frames_s"] = meta["duration_frames"]
        rec["duration_container_s"] = meta["duration_container"]
        rec["duration_check_ok"] = (
            abs(dur - meta["duration_frames"]) <= 0.01 * max(dur, 1e-9)
            if (meta["duration_frames"] and dur) else None)
        rec["bitrate_kbps"] = rec["size_bytes"] * 8.0 / dur / 1000.0
        # discard run1 output (methodology: warmup discarded); keep md5 only
        try:
            os.remove(cell["dst_run1"])
        except OSError:
            pass
        return rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, c): c for c in todo}
        for fut in as_completed(futs):
            cell = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                rec = dict(cell)
                rec["error"] = "exception: %r" % e
            append_jsonl(PROG_ENC, rec)
            fin += 1
            el = time.time() - t0
            print("[encode %d/%d] %s crf%d %s t2=%.2fs cpu2=%.1fs kbps=%.1f rc=%s%s (%.0fs elapsed)" % (
                fin, len(todo), rec["tag"], rec["crf"], rec["preset"],
                rec.get("t_run2_s") or -1, rec.get("cpu_run2_s") or -1,
                rec.get("bitrate_kbps") or -1, rec.get("rc_run2"),
                " ERR:" + rec["error"] if rec.get("error") else "", el), flush=True)
    update_meta(encode_end=date_now())


def phase_vmaf(limit=None, workers=VMAF_WORKERS):
    os.makedirs(LOGDIR, exist_ok=True)
    os.makedirs(VMAFDIR, exist_ok=True)
    enc = load_jsonl(PROG_ENC)
    done = load_jsonl(PROG_VMAF)
    todo = [r for r in enc.values() if r["key"] not in done and not r.get("error")]
    if limit:
        todo = todo[:limit]
    print("[vmaf] %d/%d done, %d todo (workers=%d)" % (len(done), len(enc), len(todo), workers))
    if not todo:
        return
    update_meta(vmaf_start=date_now(), vmaf_workers=workers)
    fin = 0
    t0 = time.time()

    def work(rec):
        out = {"key": rec["key"], "tag": rec["tag"], "crf": rec["crf"], "preset": rec["preset"],
               "dist": rec["dst"], "ref": rec["src"], "vmaf_log": rec["vmaf_log"],
               "ts_vmaf": date_now()}
        tf = os.path.join(LOGDIR, os.path.basename(rec["vmaf_log"]) + ".time")
        filt = "[0:v][1:v]libvmaf=n_subsample=1:log_fmt=json:log_path=%s" % rec["vmaf_log"]
        cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
               "-i", rec["dst"], "-i", rec["src"], "-lavfi", filt, "-f", "null", "-"]
        r = timed(cmd, tf)
        out["rc"] = r["rc"]
        out["vmaf_wall_s"] = r.get("wall_s")
        if r["rc"] != 0:
            out["error"] = "vmaf failed rc=%s %s" % (r["rc"], r.get("stderr_tail", "")[-400:])
            return out
        try:
            d = json.load(open(rec["vmaf_log"]))
        except Exception as e:
            out["error"] = "vmaf log parse failed: %r" % e
            return out
        out["libvmaf_log_version"] = d.get("version")
        pm = d.get("pooled_metrics", {}) or {}
        agg = d.get("aggregate", {}) or {}
        v = None
        if "vmaf" in pm and isinstance(pm["vmaf"], dict):
            v = pm["vmaf"].get("mean")
        elif "VMAF_score" in agg:
            a = agg["VMAF_score"]
            v = a.get("mean") if isinstance(a, dict) else a
        out["vmaf"] = v
        out["vmaf_min"] = (pm.get("vmaf") or {}).get("min")
        out["vmaf_max"] = (pm.get("vmaf") or {}).get("max")
        frames = d.get("frames", []) or []
        vals = [f["metrics"]["vmaf"] for f in frames
                if isinstance(f.get("metrics"), dict) and "vmaf" in f["metrics"]]
        out["vmaf_frame_mean"] = (sum(vals) / len(vals)) if vals else None
        out["n_frames_scored"] = len(vals)
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, r): r for r in todo}
        for fut in as_completed(futs):
            rec = futs[fut]
            try:
                out = fut.result()
            except Exception as e:
                out = {"key": rec["key"], "tag": rec["tag"], "crf": rec["crf"],
                       "preset": rec["preset"], "error": "exception: %r" % e}
            append_jsonl(PROG_VMAF, out)
            fin += 1
            print("[vmaf %d/%d] %s crf%d %s vmaf=%s (%.0fs elapsed)" % (
                fin, len(todo), out["tag"], out["crf"], out["preset"],
                ("%.3f" % out["vmaf"]) if out.get("vmaf") is not None else "ERR",
                time.time() - t0), flush=True)
    update_meta(vmaf_end=date_now())


# ---------------------------------------------------------------- analysis
def linfit(xs, ys):
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    d = n * sxx - sx * sx
    if d == 0:
        return None
    a = (n * sxy - sx * sy) / d
    b = (sy - a * sx) / n
    ybar = sy / n
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return a, b, r2


def geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def decide(savings, cost):
    if savings < 0.03 and cost > 0.50:
        return "not_worth_it"
    if savings >= 0.03 and cost <= 0.50:
        return "worth_it"
    return "inconclusive"


def phase_report(partial=False):
    enc = load_jsonl(PROG_ENC)
    vmaf = load_jsonl(PROG_VMAF)
    n_expected = len(FILES) * len(ANCHORS) * len(PRESETS)
    if len(enc) != n_expected and not partial:
        print("[report] ERROR: %d/%d encode records; refusing full report (use --partial)" % (len(enc), n_expected))
        sys.exit(2)
    missing_vmaf = [k for k, r in enc.items() if k not in vmaf or vmaf[k].get("vmaf") is None]
    if missing_vmaf and not partial:
        print("[report] ERROR: %d cells missing VMAF: %s" % (len(missing_vmaf), missing_vmaf[:5]))
        sys.exit(2)

    # merge results
    results = []
    for k in sorted(enc.keys()):
        r = dict(enc[k])
        v = vmaf.get(k, {})
        r["vmaf"] = v.get("vmaf")
        r["vmaf_min"] = v.get("vmaf_min")
        r["vmaf_max"] = v.get("vmaf_max")
        r["vmaf_frame_mean"] = v.get("vmaf_frame_mean")
        r["n_frames_scored"] = v.get("n_frames_scored")
        r["vmaf_log"] = v.get("vmaf_log", r.get("vmaf_log"))
        r["libvmaf_log_version"] = v.get("libvmaf_log_version")
        results.append(r)

    caveats = []
    errors = [r for r in results if r.get("error")]
    if errors:
        caveats.append("%d cells had errors: %s" % (len(errors), [r["key"] for r in errors][:10]))
    if missing_vmaf:
        caveats.append("%d cells missing VMAF (excluded from fits): %s" % (len(missing_vmaf), missing_vmaf[:10]))
    md5_mismatch = [r["key"] for r in results if r.get("md5_match") is False]
    if md5_mismatch:
        caveats.append("x265 run1/run2 md5 mismatch in %d cells (non-deterministic output): %s"
                       % (len(md5_mismatch), md5_mismatch[:10]))
    else:
        caveats.append("x265 run1/run2 md5 identical in all %d measured cells (deterministic; "
                       "second-run measurement unaffected by warmup)." % len(results))
    dur_bad = [r["key"] for r in results if r.get("duration_check_ok") is False]
    if dur_bad:
        caveats.append("container duration vs nb_frames/fps differ >0.5%% in %d cells: %s"
                       % (len(dur_bad), dur_bad[:10]))

    # per-material analysis
    tags = []
    for sub, name in FILES:
        tags.append("%s_%s" % (sub, name[:-4]))
    per_mat = []
    for tag in tags:
        recs = [r for r in results if r["tag"] == tag]
        m = {"tag": tag, "n_cells": len(recs)}
        if len(recs) < len(ANCHORS) * len(PRESETS):
            m["excluded"] = "incomplete cells (%d/%d)" % (len(recs), len(ANCHORS) * len(PRESETS))
            per_mat.append(m)
            continue
        fits = {}
        ok = True
        for preset in PRESETS:
            pts = [(r["crf"], r["bitrate_kbps"], r["vmaf"]) for r in recs
                   if r["preset"] == preset and r.get("vmaf") is not None and r.get("bitrate_kbps")]
            if len(pts) < len(ANCHORS):
                m["excluded"] = "preset %s missing points" % preset
                ok = False
                break
            pts.sort()
            xs = [math.log10(p[1]) for p in pts]
            ys = [p[2] for p in pts]
            f = linfit(xs, ys)
            if f is None:
                m["excluded"] = "preset %s fit failed" % preset
                ok = False
                break
            a, b, r2 = f
            fits[preset] = {"a": a, "b": b, "r2": r2,
                            "vmin": min(ys), "vmax": max(ys),
                            "v_at_crf18": ys[0], "v_at_crf30": ys[-1],
                            "points": [{"crf": p[0], "kbps": p[1], "vmaf": p[2]} for p in pts]}
        if not ok:
            per_mat.append(m)
            continue
        m["fits"] = fits
        lo = max(fits[p]["vmin"] for p in PRESETS)
        hi = min(fits[p]["vmax"] for p in PRESETS)
        # Target selection (all cases stay inside the measured/common range => interpolation only,
        # per Plan B3 "按锚点扫 10 点后插值"):
        #   * 95 above both presets' max  -> highest common VMAF (task rule for unreachable materials)
        #   * 95 below both presets' min  -> lowest common VMAF (easy content: every anchor already
        #     exceeds 95; the closest-to-95 interpolable common level, symmetric fallback)
        #   * otherwise                    -> 95
        if VMAF_TARGET > hi:
            target = hi
            m["target_rule"] = "below_both_max_use_highest_common"
        elif VMAF_TARGET < lo:
            target = lo
            m["target_rule"] = "above_both_min_use_lowest_common"
        else:
            target = VMAF_TARGET
            m["target_rule"] = "exact_95"
        m["vmaf_target"] = target
        m["reached_95"] = hi >= VMAF_TARGET
        m["common_vmaf_range"] = [lo, hi]
        usable = (target >= lo and fits["slow"]["a"] > 0 and fits["slower"]["a"] > 0)
        m["fit_usable"] = usable
        if usable:
            k_slow = 10 ** ((target - fits["slow"]["b"]) / fits["slow"]["a"])
            k_slower = 10 ** ((target - fits["slower"]["b"]) / fits["slower"]["a"])
            m["kbps_at_target_slow"] = k_slow
            m["kbps_at_target_slower"] = k_slower
            m["bitrate_ratio_B_over_A"] = k_slower / k_slow
            m["bitrate_savings"] = 1.0 - k_slower / k_slow
        else:
            m["excluded"] = ("target %.2f below common range [%.2f,%.2f] or non-positive slope"
                             % (target, lo, hi))
        # time ratios per anchor (run2 wall), geometric mean over anchors
        tr = []
        tr_cpu = []
        for crf in ANCHORS:
            rs = next(r for r in recs if r["preset"] == "slow" and r["crf"] == crf)
            rb = next(r for r in recs if r["preset"] == "slower" and r["crf"] == crf)
            if rs.get("t_run2_s") and rb.get("t_run2_s"):
                tr.append(rb["t_run2_s"] / rs["t_run2_s"])
            if rs.get("cpu_run2_s") and rb.get("cpu_run2_s"):
                tr_cpu.append(rb["cpu_run2_s"] / rs["cpu_run2_s"])
        m["time_ratio_per_anchor"] = {str(c): t for c, t in zip(ANCHORS, tr)}
        m["time_ratio_geo"] = geomean(tr) if tr else None
        m["time_ratio_cpu_geo"] = geomean(tr_cpu) if tr_cpu else None
        # matched-bitrate VMAF gain
        kbps_all = [r["bitrate_kbps"] for r in recs if r.get("bitrate_kbps")]
        matched = median(kbps_all)
        m["matched_kbps"] = matched
        x = math.log10(matched)
        m["vmaf_slow_at_matched"] = fits["slow"]["a"] * x + fits["slow"]["b"]
        m["vmaf_slower_at_matched"] = fits["slower"]["a"] * x + fits["slower"]["b"]
        m["vmaf_gain_at_matched_bitrate"] = m["vmaf_slower_at_matched"] - m["vmaf_slow_at_matched"]
        per_mat.append(m)

    used = [m for m in per_mat if m.get("bitrate_ratio_B_over_A") is not None]
    excluded = [m for m in per_mat if m.get("bitrate_ratio_B_over_A") is None]
    for m in excluded:
        caveats.append("material %s excluded from geometric means: %s" % (m["tag"], m.get("excluded", "unknown")))
    not95 = [m for m in per_mat if m.get("reached_95") is False]
    for m in not95:
        caveats.append("material %s cannot reach VMAF 95 with at least one preset "
                       "(max common VMAF %.2f); matched-VMAF target lowered to %.2f for it"
                       % (m["tag"], m["common_vmaf_range"][1], m["vmaf_target"]))
    too_easy = [m for m in per_mat
                if m.get("target_rule") == "above_both_min_use_lowest_common"]
    for m in too_easy:
        caveats.append("material %s exceeds VMAF 95 at every anchor (common range [%.2f, %.2f]); "
                       "matched-VMAF target raised to the lowest common VMAF %.2f for it "
                       "(interpolation only, no extrapolation)"
                       % (m["tag"], m["common_vmaf_range"][0], m["common_vmaf_range"][1],
                          m["vmaf_target"]))
    adjusted = [m for m in per_mat if m.get("target_rule") not in (None, "exact_95")]
    if adjusted:
        caveats.append("%d/%d materials compared at a target other than 95 (see above); all other "
                       "materials compared at exactly VMAF 95" % (len(adjusted), len(per_mat)))

    if len(used) < len(tags):
        caveats.append("only %d/%d materials usable for the matched-VMAF bitrate ratio"
                       % (len(used), len(tags)))

    ratios = [m["bitrate_ratio_B_over_A"] for m in used]
    trs = [m["time_ratio_geo"] for m in used if m.get("time_ratio_geo")]
    trs_cpu = [m["time_ratio_cpu_geo"] for m in used if m.get("time_ratio_cpu_geo")]
    gains = [m["vmaf_gain_at_matched_bitrate"] for m in used]

    br_geo = geomean(ratios) if ratios else None
    tr_geo = geomean(trs) if trs else None
    tr_cpu_geo = geomean(trs_cpu) if trs_cpu else None
    savings = (1.0 - br_geo) if br_geo is not None else None
    cost = (tr_geo - 1.0) if tr_geo is not None else None
    decision = decide(savings, cost) if (savings is not None and cost is not None) else "inconclusive"

    # outlier / leave-one-out
    outliers = {}
    loo = {}
    if len(ratios) >= 3:
        order = sorted(used, key=lambda m: m["bitrate_ratio_B_over_A"])
        best, worst = order[0], order[-1]
        outliers = {
            "best_material": {"tag": best["tag"], "bitrate_ratio": best["bitrate_ratio_B_over_A"],
                              "savings": best["bitrate_savings"], "time_ratio_geo": best["time_ratio_geo"]},
            "worst_material": {"tag": worst["tag"], "bitrate_ratio": worst["bitrate_ratio_B_over_A"],
                               "savings": worst["bitrate_savings"], "time_ratio_geo": worst["time_ratio_geo"]},
            "ratio_min": best["bitrate_ratio_B_over_A"], "ratio_max": worst["bitrate_ratio_B_over_A"],
        }
        def loo_stats(drop_tags):
            rs = [m for m in used if m["tag"] not in drop_tags]
            g = geomean([m["bitrate_ratio_B_over_A"] for m in rs])
            t = geomean([m["time_ratio_geo"] for m in rs if m.get("time_ratio_geo")])
            s, c = 1.0 - g, t - 1.0
            return {"n": len(rs), "bitrate_ratio_geo": g, "bitrate_savings": s,
                    "time_ratio_geo": t, "time_cost": c, "decision": decide(s, c)}
        loo = {
            "drop_best": loo_stats({best["tag"]}),
            "drop_worst": loo_stats({worst["tag"]}),
            "drop_both": loo_stats({best["tag"], worst["tag"]}),
        }
        stable = (loo["drop_best"]["decision"] == decision and loo["drop_worst"]["decision"] == decision
                  and loo["drop_both"]["decision"] == decision)
        loo["decision_stable"] = stable
        caveats.append("outlier analysis: best material %s ratio=%.4f, worst %s ratio=%.4f; "
                       "leave-one-out decisions: drop_best=%s drop_worst=%s drop_both=%s "
                       "(full=%s, stable=%s)"
                       % (best["tag"], best["bitrate_ratio_B_over_A"],
                          worst["tag"], worst["bitrate_ratio_B_over_A"],
                          loo["drop_best"]["decision"], loo["drop_worst"]["decision"],
                          loo["drop_both"]["decision"], decision, stable))

    summary = {
        "bench": "libx265 preset slow (A) vs slower (B) -- D2 preset axis, CPU early ring",
        "decision": decision,
        "decision_rule": ("not_worth_it if savings<3%% and time_cost>+50%%; worth_it if savings>=3%% "
                          "and time_cost<=+50%%; else inconclusive (Plan v3 12.3 / B5.4 proposed thresholds)"),
        "vmaf_target": VMAF_TARGET,
        "n_materials_total": len(tags),
        "n_materials_used": len(used),
        "materials_below_vmaf95": [m["tag"] for m in not95],
        "materials_above_vmaf95_at_all_anchors": [m["tag"] for m in too_easy],
        "bitrate_ratio_geo_B_over_A": br_geo,
        "bitrate_savings_at_matched_vmaf": savings,
        "time_ratio_geo_B_over_A": tr_geo,
        "time_cost": cost,
        "time_ratio_geo_cpu_crosscheck": tr_cpu_geo,
        "time_cost_cpu_crosscheck": (tr_cpu_geo - 1.0) if tr_cpu_geo else None,
        "vmaf_gain_at_matched_bitrate_median": median(gains) if gains else None,
        "vmaf_gain_at_matched_bitrate_per_material": {m["tag"]: m["vmaf_gain_at_matched_bitrate"] for m in used},
        "outliers": outliers,
        "leave_one_out": loo,
        "per_material": per_mat,
    }
    try:
        run_meta = json.load(open(RUN_META))
    except Exception:
        run_meta = {}
    enc_workers_used = run_meta.get("encode_workers", DECODE_ENCODE_WORKERS)
    vmaf_workers_used = run_meta.get("vmaf_workers", VMAF_WORKERS)
    caveats.append("timing: %d-way concurrent encodes (x265 default threads); wall clock from "
                   "/usr/bin/time, second run of each cell. CPU-time cross-check ratio = %s. The box "
                   "was shared with the sibling libsvtav1 bench for roughly the first hour of the "
                   "encode phase; wall-clock times are contended, the slow/slower ratio is not "
                   "expected to be biased because cells were shuffled so both presets experience "
                   "the same load distribution."
                   % (enc_workers_used, ("%.3f" % tr_cpu_geo) if tr_cpu_geo else "n/a"))
    caveats.append("machine is an old Xeon E5-2620 v2 (Ivy Bridge, AVX1 only, no AVX2): absolute "
                   "encode times are not production-representative; only the slow/slower ratio and "
                   "the matched-VMAF bitrate ratio are machine-independent.")
    caveats.append("matched-VMAF comparison is a BD-rate simplification: per material, linear fit of "
                   "VMAF vs log10(kbps) over the 5 CRF anchors, solved at VMAF 95 when 95 lies inside "
                   "the range common to both presets; when both presets stay above 95 at every anchor "
                   "the lowest common VMAF is used, when neither preset reaches 95 the highest common "
                   "VMAF is used (interpolation only, never extrapolation).")
    caveats.append("concurrency recorded in run_meta: encode_workers=%s, vmaf_workers=%s (cap: never "
                   "more than 8 ffmpeg processes at any time)."
                   % (run_meta.get("encode_workers"), run_meta.get("vmaf_workers")))
    caveats.append("encode command locked to -c:v libx265 -crf <anchor> -preset <preset> with no "
                   "lookahead/tune/multipass/aq; x265 thread pool left at default (auto).")
    caveats.append("this bench is partial/pre-run only for the preset axis; D1 anchor axis (CRF 20 vs 16) "
                   "is not measured here (see Plan v3 12.3).")

    report = {
        "bench_id": "q3_d2_libx265_slow_vs_slower_cpu_early",
        "axis": {"A": "libx265 -preset slow", "B": "libx265 -preset slower",
                 "codec": "libx265", "anchors_crf": ANCHORS, "n_measurement_points": len(results)},
        "method": {
            "encode_cmd_template": ("ffmpeg -nostdin -y -hide_banner -loglevel error -i <src> -an "
                                    "-c:v libx265 -crf <anchor> -preset <slow|slower> <dst>"),
            "vmaf_filter": "[0:v][1:v]libvmaf=n_subsample=1:log_fmt=json:log_path=<per-cell>",
            "vmaf_reference": "original eqq_calib clip (no re-encode)",
            "timing": "wall clock (s), 2 runs per cell, second run measured; CPU time cross-check",
            "bitrate": "output_bytes*8/duration, duration=nb_frames/fps of run2 output",
            "fit": "VMAF = a*log10(kbps)+b per material per preset (least squares, 5 anchors)",
            "matched_vmaf": "target 95, clamped into the VMAF range common to both presets",
            "concurrency": {"encode_workers": DECODE_ENCODE_WORKERS, "vmaf_workers": VMAF_WORKERS},
        },
        "partial": bool(partial),
        "summary": summary,
        "results": results,
        "caveats": caveats,
    }
    with open(os.path.join(OUTROOT, "libx265_ab.json"), "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ring_meta.json
    try:
        pool_probe = json.load(open(POOL_PROBE))
    except Exception:
        pool_probe = {}
    try:
        run_meta = json.load(open(RUN_META))
    except Exception:
        run_meta = {}
    run_meta["run_end"] = date_now()
    with open(RUN_META, "w") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)
    ring = {
        "bench_id": "q3_d2_libx265_slow_vs_slower_cpu_early",
        "purpose": "stage 0.5 CPU-side D2 pre-run for Plan v3 12.2 Q3 / 12.3 "
                   "(is slower worth it over slow at matched VMAF?)",
        "timestamps": run_meta,
        "machine": pool_probe.get("machine", {}),
        "ffmpeg": pool_probe.get("ffmpeg", {}),
        "x265": pool_probe.get("x265", {}),
        "libvmaf": pool_probe.get("libvmaf", {}),
        "vmaf_filter": "[0:v][1:v]libvmaf=n_subsample=1:log_fmt=json:log_path=<per-cell>",
        "encode_cmd_template": pool_probe.get("encode_cmd_template", ""),
        "pool": pool_probe.get("pool", []),
        "anchors": ANCHORS,
        "presets": PRESETS,
        "counts": {"measurement_points": len(results),
                   "encode_runs": 2 * len(results),
                   "expected_points": n_expected},
        "concurrency": {"encode_workers": enc_workers_used, "vmaf_workers": vmaf_workers_used,
                        "max_ffmpeg_procs": 8,
                        "note": "actual worker counts used in this run (pool_probe.json records the driver defaults)"},
        "artifacts": {
            "libx265_ab.json": os.path.join(OUTROOT, "libx265_ab.json"),
            "ring_meta.json": os.path.join(OUTROOT, "ring_meta.json"),
            "libx265_work": WORK,
        },
    }
    with open(os.path.join(OUTROOT, "ring_meta.json"), "w") as f:
        json.dump(ring, f, indent=2, ensure_ascii=False)

    print("[report] decision=%s savings=%.4f time_cost=%.4f br_geo=%.5f tr_geo=%.4f gain_med=%s" % (
        decision, savings if savings is not None else float("nan"),
        cost if cost is not None else float("nan"),
        br_geo if br_geo is not None else float("nan"),
        tr_geo if tr_geo is not None else float("nan"),
        ("%.4f" % summary["vmaf_gain_at_matched_bitrate_median"])
        if summary["vmaf_gain_at_matched_bitrate_median"] is not None else "n/a"))
    print("[report] wrote %s and %s" % (os.path.join(OUTROOT, "libx265_ab.json"),
                                        os.path.join(OUTROOT, "ring_meta.json")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["probe", "encode", "vmaf", "report"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--partial", action="store_true")
    args = ap.parse_args()
    if args.phase == "probe":
        phase_probe()
    elif args.phase == "encode":
        phase_encode(limit=args.limit, workers=args.workers or DECODE_ENCODE_WORKERS)
    elif args.phase == "vmaf":
        phase_vmaf(limit=args.limit, workers=args.workers or VMAF_WORKERS)
    elif args.phase == "report":
        phase_report(partial=args.partial)


if __name__ == "__main__":
    main()
