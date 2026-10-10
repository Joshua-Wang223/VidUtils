---
name: 写测试/清理代码时的三个自身踩坑
description: ①finally 里引用外层不存在的变量会把清理 bug 放大成主流程失败 ②测试用 --frames N 超过素材实际帧数会产生假失败 ③跨脚本照抄 helper 函数名会 NameError
type: feedback
---

本会话做 `convert_sdr_to_hdr.py` 字幕功能时，连踩三个**测试/收尾代码**的坑，
它们都不在业务逻辑里、但都让「本来正常的功能」看起来是坏的。

## 坑一：在 `finally` 里引用外层作用域里不存在的变量

给 burn 加临时字幕清理时，我写在 `run_job` 的 `finally` 里：

```python
if st.subs_burn_file:      # ❌ run_job 里没有 st（它在 plan["settings"]）
```

⇒ `NameError: name 'st' is not defined` 把**整个转换**搞失败（EXIT=1、目录还在），
而这个函数的主逻辑（编码）本身是好的。

**Why:** `finally` 里的清理代码看起来是「收尾」，直觉上不会影响主流程；
但异常会从 `finally` 抛出并**替换掉**原有的返回值/异常。
⇒ 一个「清理 bug」的破坏力可以远大于它本身。

**How to apply:**
- 在 `finally` / `except` 里引用变量前，先确认它在本函数作用域内存在；
  不确定就用 `locals()` 或显式传参。
- **清理代码整段包 `try/except Exception: pass`** —— 连引用本身也要在保护内，
  否则清理 bug 会升级成主流程失败（实测到了：EXIT=1、临时目录还在）。
- ⚠ 配套原则：**清理失败不该影响转换结果**。清理是「尽力而为」，
  拿不准就静默跳过 + 让核心结果照常返回。
- ⚠ **跨分支共享的标志必须在函数开头显式初始化**。同型案例：想共享
  「`-i src` 是否已加入」的标志，结果该变量只在其中一个分支里赋值，
  跨分支读到未定义 ⇒ 也是 NameError。

## 坑二：测试用 `--frames N` 超过素材实际帧数 ⇒ 假失败

跑 8 组功能矩阵时用统一的 `--frames 40`，其中两组用 `fixture_1080p.mp4`
（**只有 25 帧**）⇒ 两组报 EXIT=1「输出不完整：期望 40 帧、实际 25 帧」。
我一度以为是无字幕源的字幕逻辑有问题。

**Why:** 帧数守恒检查（期望 vs 实际）是**正确**的行为，是我的**测试参数**错了。
⇒ 这类失败看起来像功能 bug，其实是「测试用例本身不合法」。

⚠ **本会话因此复发两次**（同一现象），说明它极易复发。

**How to apply:**
- 跑功能矩阵前先 `ffprobe` 确认每个素材的**实际帧数**，
  `--frames` 取 `min(各素材帧数)` 或直接省略。
- ⚠ 出现「输出不完整：期望 N 帧、实际 M 帧」时，**先核对 N 是不是自己传的**
  —— 别立刻去查产品逻辑。
- 判别方法：把 `--frames` 降到素材真实帧数以内重跑；若通过 ⇒ 是测试参数问题。

## 坑三：跨脚本照抄 helper 函数名 ⇒ NameError

复刻「auto 是否会分段」的判据时，我参照 **benchmark 脚本**写了
`_to_int(meta.get("nb_frames"))`，而**被测脚本**里只有 `_frac_to_float`
⇒ `NameError: name '_to_int' is not defined`。

同型：写字幕导出时用了 `_run()` / `_ffmpeg_bin()` / `_probe_json()` ——
这三个都只存在于 `Accessory/probe/bench_sdr_to_hdr.py`，
`convert_sdr_to_hdr.py` 各处是**裸 `subprocess.run(...)`** ⇒ 连击三次 NameError。

**Why:** 两个脚本在同一仓库里做同类工作（一个跑基准、一个被测），
它们的 helper 名字很像但**归属不同**。凭印象写就会串。

**How to apply:**
- 在本文件加代码前先 `grep` 同类调用（`grep -n 'subprocess.run' xxx.py`），
  **照既有风格写**，不要凭另一个脚本的习惯。
- 关联 `feedback_interlock_predicate_layer.md`（复刻判据时同一个坑复发）。
