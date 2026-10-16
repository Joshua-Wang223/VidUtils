#!/usr/bin/env python3
"""Generate ring_meta.json for the libsvtav1 D2 bench (CPU early run)."""
import json
import subprocess
import hashlib
import os

ROOT = "/workspace/VidUtils/Accessory/calib/eqq_evidence/q3_bench/cpu_early"
INROOT = "/workspace/input_videos/eqq_calib"
WORK = ROOT + "/libsvtav1_work"

MATS = [
    "6s/cganim_edu_wordworld_src720x576.mp4", "6s/cganim_subs_src1920x1080.mp4",
    "6s/cganim_talking_tom_src1920x1080.mp4", "6s/doc_dark_earth_src3840x2160.mp4",
    "6s/doc_grassland_src1280x720.mp4", "6s/live_kids_play_src1280x720.mp4",
    "6s/live_kids_seated_src1920x1080.mp4", "6s/live_kids_table_src1920x1080.mp4",
    "6s/screen_ui_code_src1280x720.mp4", "6s/tv_bbc_molly_s01e01_src1920x1080.mp4",
    "6s/tv_bbc_molly_s03e01_src1920x1080.mp4", "6s/tv_bbc_molly_s05e01_src1920x1080.mp4",
    "10s/anim2d_forest_src1280x720.mp4", "10s/anim2d_subs_tobot_src1280x720.mp4",
    "10s/live_night_wolf_src1280x720.mp4", "10s/live_texture_frog_src1280x720.mp4",
    "10s/screen_ui_code_src1280x720.mp4",
]

def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

def probe(path):
    out = sh(f"ffprobe -v error -select_streams v:0 -show_entries "
             f"stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt "
             f"-show_entries format=duration -of json '{path}'")
    d = json.loads(out)
    st = d["streams"][0]
    return {
        "codec": st.get("codec_name"), "width": st.get("width"), "height": st.get("height"),
        "r_frame_rate": st.get("r_frame_rate"), "avg_frame_rate": st.get("avg_frame_rate"),
        "nb_frames": int(st["nb_frames"]) if st.get("nb_frames", "").isdigit() else st.get("nb_frames"),
        "pix_fmt": st.get("pix_fmt"), "duration_s": float(d["format"]["duration"]),
    }

def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

meta = {
    "bench": "libsvtav1 preset 6 vs preset 4 (D2, CPU early run, stage 0.5)",
    "purpose": "Q3 兜底实测: 等 VMAF 码率收益 vs 耗时代价, 供 quality/archive 两档 D2 分化决策",
    "plan_ref": "Plan/VidUtils_质量参数统一_quality-tier方案_v3_20261015.md §12.2 Q3-落地 / §12.3",
    "timestamps": {
        "batch_start": sh(f"cat {WORK}/batch_start.txt"),
        "batch_end": sh(f"cat {WORK}/batch_end.txt") if os.path.exists(f"{WORK}/batch_end.txt") else None,
        "rescore_start": sh(f"cat {WORK}/rescore_start.txt") if os.path.exists(f"{WORK}/rescore_start.txt") else None,
        "rescore_end": sh(f"cat {WORK}/rescore_end.txt") if os.path.exists(f"{WORK}/rescore_end.txt") else None,
        "meta_generated": sh("date '+%Y-%m-%d %H:%M:%S %Z'"),
    },
    "machine": {
        "cpu_model": sh("lscpu | grep 'Model name' | cut -d: -f2- | xargs"),
        "cpus_logical": int(sh("nproc")),
        "cpus_physical": sh("lscpu | grep -E '^Core|^Socket|^Thread' | tr '\\n' ' '"),
        "mem_total": sh("free -g | awk '/^Mem:/{print $2\" GB\"}'"),
        "kernel": sh("uname -r"),
    },
    "software": {
        "ffmpeg_version": sh("ffmpeg -version | head -1"),
        "ffmpeg_configuration": sh("ffmpeg -version | grep -o 'configuration:.*'"),
        "libvmaf_version_pkgconfig": sh("pkg-config --modversion libvmaf"),
        "libvmaf_soname": sh("ls /usr/lib/x86_64-linux-gnu/ | grep 'libvmaf.so' | tr '\\n' ' '"),
        "libvmaf_json_log_version_field": "3.2.0",
        "python": sh("python3 --version"),
        "numpy": sh("python3 -c 'import numpy; print(numpy.__version__)'"),
    },
    "vmaf_filter": ("[0:v]settb=AVTB,setpts=N/30/TB[d];[1:v]settb=AVTB,setpts=N/30/TB[r];"
                    "[d][r]libvmaf=n_subsample=1:model=version=vmaf_v0.6.1:"
                    "log_fmt=json:log_path=<cell>/vmaf_fixed.json ; "
                    "metric = pooled_metrics.vmaf.mean"),
    "vmaf_note": ("n_subsample=1 强制全帧 (convert_crf.py / calibrate_equal_quality.py 口径: "
                  "subsample>1 会偏置 VMAF 且偏置随编码器而异); 原始切片同时作 reference 与输入. "
                  "settb+setpts 重索引是必需的帧配对修复: 素材多为 VFR / 非毫秒对齐 pts "
                  "(如 0, 1/30, 0.1, 0.1333... 交替), 而 matroska 1/1000 时基无法精确表示 "
                  "1/30s (33ms != 33.333ms), libvmaf framesync 按时间戳配对会奇偶帧错位; "
                  "对两路输入按帧序统一置 pts 后, 编码第 i 帧与参考第 i 帧配对. "
                  "指标本身 (模型/subsample/参考) 不变; 25fps 素材两轮结果一致."),
    "encoder": {
        "cmd": ("ffmpeg -nostdin -y -hide_banner -loglevel error -i <material> -an "
                "-c:v libsvtav1 -crf <anchor> -preset <preset> -f matroska <out.mkv>"),
        "axis_only": "只测 preset 轴: 无 lookahead/tune/multipass/aq/threads 等附加选项",
        "anchors": [18, 21, 24, 27, 30],
        "presets": {"A_quality": 6, "B_archive": 4},
        "cells": 170,
        "runs_per_cell": 2,
        "timing": "墙钟秒 (date +%s.%N), 每格跑2次取第二次, 首轮预热丢弃",
        "bitrate": "输出文件字节数 / ffprobe format duration (不信 ffmpeg 日志)",
        "concurrency": "xargs -P 8 (任一时刻 <=8 个 ffmpeg 进程)",
    },
    "materials": [{"rel": m, **probe(os.path.join(INROOT, m))} for m in MATS],
    "material_pool_root": INROOT,
    "work_dir": WORK,
    "artifacts": {
        "results_json": "libsvtav1_ab.json (170 个测量点 + summary)",
        "raw_tsv": "results_raw.tsv",
        "cells": "libsvtav1_work/cells/<cell>/{run2.mkv,vmaf.json,*.err}",
    },
    "environment_notes": [
        "两阶段执行: 阶段一 (16:41-17:56) 编码+计时+首轮 VMAF; 首轮 VMAF 对非毫秒对齐源 "
        "因 framesync 时间戳错位失效 (仅编码/计时有效); 阶段二用 settb+setpts 帧序重索引 "
        "对全部 170 格重打分 (vmaf_fixed.json), 指标口径不变.",
        "机器为共享环境: 批跑期间同机有其他并行工作流 (sibling bench agents) 的 ffmpeg 进程, "
        "本任务自身并发严格 <=8 (xargs -P 8, 每格串行). 墙钟计时含共享负载噪声; "
        "p6/p4 任务交替下发, 噪声在几何平均中期望抵消.",
        "SVT-AV1 默认使用全部逻辑核 (24), 8 路并发时存在超额订阅, 对两个 preset 影响对称.",
    ],
}
with open(ROOT + "/ring_meta.json", "w") as f:
    json.dump(meta, f, indent=1, ensure_ascii=False)
print(json.dumps(meta, indent=1, ensure_ascii=False)[:2000])
print(f"\nwrote {ROOT}/ring_meta.json")
