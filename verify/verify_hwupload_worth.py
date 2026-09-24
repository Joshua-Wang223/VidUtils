# verify/verify_hwupload_worth.py — 验证 `--scale-algo auto` 的「值不值得走 hwupload」门槛
#
# 依据 T4 两批实测（12 组素材，见 memory/project_cuda_scale_cover.md 判据 D）：
#   · 源 ≥10bit **且真在缩放** → 上传链快 14~25%
#   · 8bit + 真在缩放          → 打平或更慢（最差 −14%）
#   · 恒等缩放（无论位深）     → 一定亏（−1.6% ~ −34.7%）
# 机理：上载/回下载开销基本固定，p010le 的 CPU 缩放比 8bit 贵得多，恒等时 CPU 侧
# 本来就没有重采样成本可省 → 只有"高位深 + 真的省下缩放"才划算。
#
# 门槛**只管 auto**；显式 `--scale-algo cuda-*` 是用户点名要的，照旧直接执行。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vidcrop_hwaccel as V

fails = []


def chk(label, got, want):
    ok = got == want
    print(f'  [{"OK" if ok else "FAIL"}] {label}')
    if not ok:
        print(f'        got : {got}')
        print(f'        want: {want}')
        fails.append(label)


def caps():
    c = V.HardwareCapabilities()
    c.has_decoder = True
    c.has_encoder_h264 = True
    c.has_encoder_hevc = True
    c.has_cuda_scale = True
    c.cuda_scale_upload_ok = True
    return c


def uses_hwupload(scale_backend='auto', **kw):
    """cover 模式 + 软解：策略链里有没有 hwupload 形态。"""
    return any(x.get('hwupload') for x in V._generate_strategies(
        'hevc_nvenc', caps(), 'cpu', mode='cover',
        scale_backend=scale_backend, policy='auto', **kw))


print('── ① _hwupload_skip_reason 单元 ──')
chk('恒等（10bit）→ 有理由跳过', bool(V._hwupload_skip_reason(10, True)), True)
chk('恒等（8bit）→ 有理由跳过', bool(V._hwupload_skip_reason(8, True)), True)
chk('8bit 非恒等 → 有理由跳过', bool(V._hwupload_skip_reason(8, False)), True)
chk('10bit 非恒等 → 没有理由跳过（该走）', V._hwupload_skip_reason(10, False), None)
chk('12bit 非恒等 → 没有理由跳过', V._hwupload_skip_reason(12, False), None)
chk('恒等的理由里点明"恒等缩放"', '恒等缩放' in V._hwupload_skip_reason(10, True), True)
chk('8bit 的理由里点明位深', '8bit' in V._hwupload_skip_reason(8, False), True)

print('── ② _cover_scale_dims 单元（与 _build_cover_*_filter_str 同一套几何）──')
chk('1920x1080 → 1440x1080（源更宽）：缩放后仍是 1920x1080 → 恒等',
    V._cover_scale_dims(1920, 1080, 1440, 1080), (1920, 1080))
chk('3840x2160 → 1440x1080（源更宽）：缩到 1920x1080',
    V._cover_scale_dims(3840, 2160, 1440, 1080), (1920, 1080))
chk('1280x720 → 1440x1080（源更窄）：放大到 1920x1080',
    V._cover_scale_dims(1280, 720, 1440, 1080), (1920, 1080))
chk('1920x1080 → 1280x720（同比例）：就是目标尺寸',
    V._cover_scale_dims(1920, 1080, 1280, 720), (1280, 720))
chk('1080x1920 → 1280x720（源更高）：放高、上下裁',
    V._cover_scale_dims(1080, 1920, 1280, 720), (1280, 2276))

print('── ③ auto + 软解：门槛真的生效 ──')
chk('10bit + 非恒等 → 走 hwupload', uses_hwupload(src_bits=10, scale_identity=False), True)
chk('12bit + 非恒等 → 走 hwupload', uses_hwupload(src_bits=12, scale_identity=False), True)
chk('10bit + 恒等 → 不走', uses_hwupload(src_bits=10, scale_identity=True), False)
chk('8bit + 非恒等 → 不走', uses_hwupload(src_bits=8, scale_identity=False), False)
chk('8bit + 恒等 → 不走', uses_hwupload(src_bits=8, scale_identity=True), False)
chk('默认（不传 src_bits）→ 按 8bit 保守处理，不走',
    uses_hwupload(), False)

print('── ④ 显式 --scale-algo cuda 不受门槛约束（用户点名要的照跑）──')
chk('显式 cuda + 8bit + 恒等 → 仍走 hwupload',
    uses_hwupload(scale_backend='cuda', src_bits=8, scale_identity=True), True)
chk('显式 cuda + 10bit + 非恒等 → 走 hwupload',
    uses_hwupload(scale_backend='cuda', src_bits=10, scale_identity=False), True)

print('── ⑤ 门槛不影响别的策略 ──')
s = V._generate_strategies('hevc_nvenc', caps(), 'cpu', mode='cover',
                           scale_backend='auto', policy='auto',
                           src_bits=8, scale_identity=False)
chk('8bit 软解仍保留可用策略（没有空链）', len(s) > 0, True)
chk('8bit 软解首选落入「软件解码 + GPU 编码」', s[0]['name'], '软件解码 + GPU 编码')
chk('整条链里没有任何显存缩放', [x.get('cuda_scale') for x in s if x.get('cuda_scale')], [])

print()
if fails:
    print(f'✘ {len(fails)} 项失败：{fails}')
    sys.exit(1)
print('✓ 全部通过')
