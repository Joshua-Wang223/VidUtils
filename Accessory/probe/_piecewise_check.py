#!/usr/bin/env python3
"""_piecewise_check.py —— HDR 域等质量表「仿射 vs 分段」候选的 LOO 对比（2026-10-16 §9/§10）。

对比模型（全部按逐素材留一 LOO 重拟合）：
  affine     现表口径（池化最小二乘 a + 各素材截距中位数 b）
  piece      分段·逐锚点中位值连线
  cuminc     分段·稳健增量累积（自 ref15 中位值起，逐段累加素材内增量中位数）★ 候选 A
  cuminc_old 分段·旧 18~30 节点（自 ref18 中位值起累积）+ 前置 ref15 中位值 ☆ 候选 B
  twoseg     两段最小二乘（断点 21 / 22.5）
  quad       二次回归

评价网格：锚点 6 点 / 整数 15..33 / 0.5 步密集 37 点。
真值：target_psnr(ref) 由锚点 (ref, psnr) 线性插值；truth_qp = 该编码器曲线上取等 PSNR 值。
误差给出 qp 与 dB 当量（|Δqp| × 曲线在真值处局部斜率）。

用法：
  python3 Accessory/probe/_piecewise_check.py [codec]     # 默认 libsvtav1
数据：Accessory/temp/hdr_eqq/*.json（10 素材；与 _final_fit.py 同源）
结论落档：memory/hdr_equal_quality_calibration_10mat_20261016.md §9/§10
"""
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
WORK = ROOT / 'Accessory/temp/hdr_eqq'
REFS = [15, 18, 21, 24, 27, 30]
CODEC = sys.argv[1] if len(sys.argv) > 1 else 'libsvtav1'


def monotone_filter(pts):
    cur = sorted(pts, key=lambda d: d['value'])
    dropped = []
    changed = True
    while changed and len(cur) > 2:
        changed = False
        for i in range(len(cur) - 1):
            if cur[i + 1]['psnr'] > cur[i]['psnr'] + 1e-9:
                dropped.append(cur[i]); cur = cur[:i] + cur[i + 1:]; changed = True; break
    return cur, dropped


def interp(pts, t):
    lo = min(p['psnr'] for p in pts); hi = max(p['psnr'] for p in pts)
    if not (lo <= t <= hi):
        return None
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if (a['psnr'] - t) * (b['psnr'] - t) <= 0 and a['psnr'] != b['psnr']:
            return a['value'] + (a['psnr'] - t) * (b['value'] - a['value']) / (a['psnr'] - b['psnr'])
    return None


def local_slope(pts, v):
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a['value'] <= v <= b['value'] and b['value'] != a['value']:
            return abs((b['psnr'] - a['psnr']) / (b['value'] - a['value']))
    return None


mats = []
for p in sorted(WORK.glob('*.json')):
    if p.name == 'aggregate.json':
        continue
    d = json.loads(p.read_text())
    curve, _ = monotone_filter(d['curves'][CODEC]['points'])
    vals = {}
    tgt = {}
    for r_s, a in d['anchors'].items():
        r = int(r_s)
        tgt[r] = a['psnr']
        vals[r] = interp(curve, a['psnr'])
    mats.append({'name': p.stem, 'curve': curve, 'vals': vals, 'target': tgt})

# ---------- 模型 ----------
def fit_affine(rows):
    pts = [(r, row['vals'][r]) for row in rows for r in REFS if row['vals'].get(r) is not None]
    n = len(pts); sx = sum(x for x, _ in pts); sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts); sxy = sum(x * y for x, y in pts)
    a = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    bs = [st.median([row['vals'][r] - a * r for r in REFS if row['vals'].get(r) is not None])
          for row in rows]
    return a, st.median(bs)


def m_affine(rows):
    a, b = fit_affine(rows)
    return lambda x: a * x + b


def _pwl(xs, ys):
    def f(x):
        if x <= xs[0]:
            s = (ys[1] - ys[0]) / (xs[1] - xs[0]); return ys[0] + s * (x - xs[0])
        if x >= xs[-1]:
            s = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]); return ys[-1] + s * (x - xs[-1])
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                s = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i])
                return ys[i] + s * (x - xs[i])
    return f


def m_piece_knots(rows):
    ys = [st.median([row['vals'][r] for row in rows if row['vals'].get(r) is not None]) for r in REFS]
    return _pwl(REFS, ys)


def _lsq_line(pts):
    n = len(pts); sx = sum(x for x, _ in pts); sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts); sxy = sum(x * y for x, y in pts)
    a = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return a, (sy - a * sx) / n


def m_twoseg(rows, bk=21.0):
    g1 = [(r, row['vals'][r]) for row in rows for r in REFS if r <= bk and row['vals'].get(r)]
    g2 = [(r, row['vals'][r]) for row in rows for r in REFS if r > bk and row['vals'].get(r)]
    a1, b1 = _lsq_line(g1); a2, b2 = _lsq_line(g2)
    return lambda x: (a1 * x + b1) if x <= bk else (a2 * x + b2)


def m_quad(rows):
    pts = [(r, row['vals'][r]) for row in rows for r in REFS if row['vals'].get(r) is not None]
    # 3x3 正规方程手解
    s = [sum(x ** k for x, _ in pts) for k in range(5)]
    t = [sum(y * x ** k for x, y in pts) for k in range(3)]
    # [s0 s1 s2; s1 s2 s3; s2 s3 s4] [c0 c1 c2]^T = [t0 t1 t2]
    A = [[s[0], s[1], s[2]], [s[1], s[2], s[3]], [s[2], s[3], s[4]]]
    B = t[:]
    for i in range(3):  # 高斯消元
        p = max(range(i, 3), key=lambda r_: abs(A[r_][i]))
        A[i], A[p] = A[p], A[i]; B[i], B[p] = B[p], B[i]
        for r_ in range(i + 1, 3):
            f = A[r_][i] / A[i][i]
            for c_ in range(i, 3):
                A[r_][c_] -= f * A[i][c_]
            B[r_] -= f * B[i]
    c2 = B[2] / A[2][2]
    c1 = (B[1] - A[1][2] * c2) / A[1][1]
    c0 = (B[0] - A[0][1] * c1 - A[0][2] * c2) / A[0][0]
    return lambda x: c0 + c1 * x + c2 * x * x


def _cuminc_ys(rows, xs):
    """稳健增量累积：首节点取中位值，逐段累加素材内增量中位数。"""
    ys = [st.median([row['vals'][xs[0]] for row in rows if row['vals'].get(xs[0]) is not None])]
    for i in range(1, len(xs)):
        deltas = [row['vals'][xs[i]] - row['vals'][xs[i - 1]] for row in rows
                  if row['vals'].get(xs[i]) is not None and row['vals'].get(xs[i - 1]) is not None]
        ys.append(ys[-1] + st.median(deltas))
    return ys


def m_cuminc(rows):
    """候选 A：自 ref15 中位值起，6 节点稳健增量累积（重锚版）。"""
    return _pwl(REFS, _cuminc_ys(rows, REFS))


def m_cuminc_old(rows):
    """候选 B：旧 18~30 累积节点（自 ref18 中位值起）+ 前置 ref15 中位值节点。"""
    y15 = st.median([row['vals'][15] for row in rows if row['vals'].get(15) is not None])
    ys18 = _cuminc_ys(rows, REFS[1:])
    return _pwl(REFS, [y15] + ys18)


MODELS = [('affine', m_affine), ('piece', m_piece_knots), ('cuminc', m_cuminc),
          ('cuminc_old', m_cuminc_old),
          ('twoseg21', lambda rows: m_twoseg(rows, 21.0)),
          ('twoseg22.5', lambda rows: m_twoseg(rows, 22.5)),
          ('quad', m_quad)]

# ---------- 评估 ----------
ANCHOR_GRID = list(REFS)
INT_GRID = list(range(15, 34))
DENSE_GRID = [15 + 0.5 * i for i in range(37)]  # 15.0..33.0


def truth_dense(row, r):
    xs = sorted(row['target'])
    ys = [row['target'][x] for x in xs]
    if not (xs[0] <= r <= xs[-1]):
        return None
    for i in range(len(xs) - 1):
        if xs[i] <= r <= xs[i + 1]:
            f = (r - xs[i]) / (xs[i + 1] - xs[i])
            t = ys[i] + f * (ys[i + 1] - ys[i])
            break
    return interp(row['curve'], t)


def run(grid, tag):
    print(f'===== 网格: {tag}（{len(grid)} 点 × 10 素材，LOO） =====')
    stats = {}
    for name, fitter in MODELS:
        errs = []; dbs = []; per_mat = {}; per_pt = []
        for i, row in enumerate(mats):
            others = [r for j, r in enumerate(mats) if j != i]
            f = fitter(others)
            for r in grid:
                tv = truth_dense(row, r)
                if tv is None:
                    continue
                e = abs(f(r) - tv)
                errs.append(e)
                sl = local_slope(row['curve'], tv)
                if sl:
                    dbs.append(e * sl)
                per_mat.setdefault(row['name'], []).append(e)
                per_pt.append((e if sl else 0, (e * sl) if sl else 0, row['name'], r))
        errs_s = sorted(errs); dbs_s = sorted(dbs)
        q = lambda a, p: a[min(len(a) - 1, int(len(a) * p))]
        print(f'  {name:>10}: |Δqp| 中位 {st.median(errs):5.2f}  P90 {q(errs_s,0.9):5.2f}  max {errs_s[-1]:5.2f}  '
              f'均值 {sum(errs)/len(errs):5.2f} | dB 中位 {st.median(dbs):4.2f} P90 {q(dbs_s,0.9):4.2f} max {dbs_s[-1]:4.2f}')
        stats[name] = (errs, dbs, per_mat, per_pt)
    print('  -- top10 单点误差（qp, dB, 素材, ref） --')
    for n in [x for x, _ in MODELS]:
        top = sorted(stats[n][3], key=lambda t: -t[1])[:10]
        print(f'    {n}:')
        for e, db, m, r in top:
            print(f'      {m[:34]:<34} ref{r:<5} |Δqp|={e:5.2f}  dB={db:4.2f}')
    # 每素材最差
    print('  -- 逐素材 LOO max |Δqp| --')
    names = [n for n, _ in MODELS]
    for row in mats:
        line = f'    {row["name"][:34]:<34}'
        for n in names:
            line += f' {n}={max(stats[n][2].get(row["name"], [0])):5.2f}'
        print(line)
    return stats


s_anchor = run(ANCHOR_GRID, '锚点 6 点')
s_int = run(INT_GRID, '整数 15..33')
print()
s_dense = run(DENSE_GRID, '密集 15..33 step0.5')

# ---------- 复算校验（affine LOO 对 JSON） ----------
errs = s_anchor['affine'][0]
jf = ROOT / 'memory/hdr_equal_quality_calibration_10mat_20261016.json'
jd = json.loads(jf.read_text())['codecs'][CODEC]
print(f'\n[校验] {CODEC} affine 锚点 LOO: 中位 {st.median(errs):.4f} max {max(errs):.4f} '
      f'(JSON: {jd["loo_abs_median"]:.4f} / {jd["loo_abs_max"]:.4f})')

# ---------- 生产影响：全数据拟合（非 LOO），整数下发口径 int(round()) ----------
print('\n===== 6 锚点全数据拟合：整数落点（int(round())） =====')
f_aff = m_affine(mats)
f_cum = m_cuminc(mats)
f_old = m_cuminc_old(mats)
f_pie = m_piece_knots(mats)
print(f'  候选 A cuminc 节点: ' + ' / '.join(f'ref{r} {y:.4f}' for r, y in zip(REFS, _cuminc_ys(mats, REFS))))
y15 = st.median([row['vals'][15] for row in mats if row['vals'].get(15) is not None])
print('  候选 B cuminc_old 节点: ' + ' / '.join(f'ref{r} {y:.4f}' for r, y in zip(REFS, [y15] + _cuminc_ys(mats, REFS[1:]))))
print(f'  {"ref":>3} {"affine":>8} {"cuminc":>8} {"old":>8} {"piece":>8} | {"round":>5} {"roundA":>6} {"roundB":>6}')
for r in INT_GRID:
    print(f'  {r:>3} {f_aff(r):8.2f} {f_cum(r):8.2f} {f_old(r):8.2f} {f_pie(r):8.2f} | '
          f'{round(f_aff(r)):>5} {round(f_cum(r)):>6} {round(f_old(r)):>6}')
print('\n注：roundA = 候选 A（重锚）下发整数，roundB = 候选 B（旧节点+前置）下发整数。')
print(f'候选 A 整数表: ' + ', '.join(f'{r}:{round(f_cum(r))}' for r in INT_GRID))
print(f'候选 B 整数表: ' + ', '.join(f'{r}:{round(f_old(r))}' for r in INT_GRID))
print(f'仿射（旧发布）: ' + ', '.join(f'{r}:{round(f_aff(r))}' for r in INT_GRID))
