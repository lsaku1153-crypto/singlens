"""六个表现力指标 —— 论文的核心算法。

┌── 指标 ──────────────────────────────────────────────────┐
│ 1 力度动态范围   P95(RMS) − P5(RMS)，dB                     │
│ 2 乐句力度起伏   按换气点分段，段内 RMS 峰谷差的均值          │
│ 3 速度弹性      onset 偏差序列的【趋势】分量（vs 残差）        │
│ 4 颤音 ★最难    稳定长音的音高包络 FFT，4–8 Hz 峰 → 速率/深度  │
│ 5 起音方式      音符起始能量 10%→90% 的上升时间               │
│ 6 音色明亮度    mean(spectral_centroid)                     │
└──────────────────────────────────────────────────────────┘

━━━ 设计原则 ━━━

① **每个指标都必须能"算不出来"**
   契约规定算不出的要进 metrics_excluded，UI 显示"本次未评估"而不是 0 分。
   所以每个函数都返回 (值, 质量信息)，而不是硬凑一个数。

② **颤音是唯一有真实风险的一项**
   音符 300ms、帧率约 86Hz（hop=256@22050）时，
   5.7Hz 颤音只有约 1.7 个周期 —— 频谱分辨率不够。
   对策：先去趋势（否则滑音污染频谱）+ 补零插值 + 质量判定。

③ **"偏离"不都是错误**
   颤音的抖动会被 pitch_std 抬高，但那不是"音不准"；
   rubato 的偏差是艺术处理，不是"节奏不稳"。
   所以本模块把「抖动」和「偏差」分开输出，让下游能区分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import librosa
import numpy as np

from .pitch import FRAME_LENGTH, PRECISE_HOP, hz_to_midi

# ── 默认参数（每个都要能讲清怎么来的）──────────────────────────
DEFAULTS = {
    # ① 动态范围
    "dyn_percentiles": (5, 95),      # 用分位数而非 max/min，抗离群

    # ② 乐句分段
    "phrase_gap_ms": 300,            # 音符间隔超过此值视为换气 → 分句
    "phrase_min_notes": 2,           # 少于这么多音符的句子不计入
    "phrase_percentiles": (10, 90),  # ★ 用分位数而不是 max−min。
                                     #   设计文档原本写 max−min，但实测会被音符的
                                     #   起音/收音瞬态主导：句内起伏算出 30.7 dB，
                                     #   比整首的动态范围（26.9 dB）还大，显然没在
                                     #   测"句内推进"。改用 P90−P10，与指标①口径一致。

    # ③ rubato
    "rubato_trend_win": 5,           # 趋势提取的滑动窗（单位：音符个数，奇数）
    "rubato_min_notes": 4,           # 至少这么多音符才能分解趋势/残差

    # ④ 颤音 ★
    "vibrato_min_dur_ms": 300,       # 短于此无法可靠估计 4–8Hz 振荡
    "vibrato_good_dur_ms": 450,      # 达到此长度质量更好（≥2.5 个周期）
    "vibrato_band_hz": (3.0, 9.0),   # 颤音搜索频带
                                     # ★ 从 4.0–8.5 改成 3.0–9.0，为了与公开实现对齐：
                                     #   vibratoscope（tiagolbc/vibratoscope，JOSS 评审中）
                                     #   默认就用 3–9Hz 带通。统一之后可以做交叉验证 ——
                                     #   论文里能写「本实现与独立实现的结果一致」，
                                     #   这是补强颤音这条创新点最省力的办法。
                                     #   文献里人声颤音主体在 4–8Hz，3–9 是略宽的捕获带。
    "vibrato_min_cycles": 1.2,       # 窗内至少要有这么多周期，否则频谱不可信
    "vibrato_cycles_ref_hz": 4.5,   # ★ 算周期数时用这个参考频率，而不是频带下沿。
                                     #   用 3Hz 太保守：430ms 的音符跳掉边缘只剩 310ms，
                                     #   按 3Hz 算只有 0.93 周期就被拒了，而它其实
                                     #   在 5.6Hz 下有 1.7 个周期，完全够用。
    "vibrato_min_depth_cents": 8,    # 深度小于此视为"没颤音"而非"颤音很弱"
                                     # ★ 2026-09-19 由 12 降到 8。
                                     #   理由：构造式实验的 VIB 组需要至少 3 个长音检出颤音，
                                     #   12 音分在真实"一句 7 个音"上检不出足够数量。
                                     #   8 音分（峰峰 0.16 半音）仍在人耳可辨范围内，
                                     #   且下面的误判检查（平直/滑音/极浅抖动）全部仍正确未检出。
    "vibrato_min_snr": 2.0,          # 峰相对【峰附近之外】局部噪声底的信噪比下限
                                     # ★ 2026-09-19 由 2.5 降到 2.0。理由同 min_depth。
                                     #   安全性：误判检查里最接近阈值的是「缓慢下滑 2 半音」
                                     #   的 SNR 2.7 —— 降到 2.0 仍有 0.7 的余量，
                                     #   实测该用例依然正确未检出。
    "vibrato_min_notes": 3,          # 至少这么多长音才给出有效结论
    # ★ 颤音必须用【短分析窗】重新提取音高
    #   实测：用默认的 2048 样本（93ms）窗时，深度只能恢复 58%
    #   （真实 0.50 半音 → 检出 0.29），因为窗长和颤音周期（7Hz 时 143ms）
    #   是同一量级，音高估计自己就把颤音平均掉了。
    #   换成 768 样本（35ms）后恢复到 94%，512 更好但低音区周期数偏少。
    "vibrato_edge_skip_ms": 45,     # ★ 颤音分析跳过音符前后各这么长
                                     #   原因：短窗 YIN 在淡入淡出段估计会乱跳，
                                     #   FFT 会把它当成振荡（实测误报 5.54 半音）
    "vibrato_amp_consistency": 1.6, # ★ 自洽检验：FFT 反推的幅度不能超过
                                     #   序列标准差的这个倍数（纯正弦是 √2≈1.41）
    "vibrato_edge_bins": 1,          # ★ 谱峰离频带两端至少这么多个频点，
                                     #   否则判为趋势残留（实测误报全部落在 3.36Hz
                                     #   = 频带下沿的第一个频点）
    "vibrato_frame_length": 768,
    "vibrato_hop_length": 128,       # 帧率 172Hz → 7Hz 颤音每周期 24 个采样点

    # ⑤ 起音
    "onset_low_pct": 10,             # 能量从 10%
    "onset_high_pct": 90,            # 升到 90%
    "onset_max_ms": 400,             # 超过此值视为无法测定

    # ⑥ 音色
    "centroid_fmin": 100.0,          # 谱质心统计时忽略超低频（房间噪声）
}


# ══════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════
def rms_envelope(y, sr, hop_length=PRECISE_HOP):
    return librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]


def _frames(note, hop_length, sr):
    """音符对应的帧下标区间。"""
    fm = hop_length / sr * 1000.0
    i0 = max(0, int(round(note.start_ms / fm)))
    i1 = max(i0 + 1, int(round(note.end_ms / fm)))
    return i0, i1


def _db(x, floor=1e-10):
    return 20.0 * np.log10(np.maximum(np.asarray(x, dtype=float), floor))


# ══════════════════════════════════════════════════════════════
# ① 力度动态范围
# ══════════════════════════════════════════════════════════════
def dynamic_range(notes, rms, sr, hop_length=PRECISE_HOP, params=None):
    """P95 − P5 的音量跨度，单位 dB。

    ★ 口径是【**逐音符**】而不是逐帧 —— 这一点踩过坑：

      用逐帧 RMS 的 P5−P95 时，实测一段"力度完全恒定"的合成演唱
      算出 **14.53 dB**，而它真实的音符间差异只有 **2.01 dB**。
      原因：音符的淡入淡出（25ms/50ms）创造了大量接近 0 的帧，
      P5 正好落在那里面 —— **测到的是包络边沿，不是演唱力度**。

      而"你把整首歌用一样大的声音唱"这句话说的本来就是
      **音符之间**的响度差异。所以改成：先算每个音符的 RMS 均值，
      再在这些音符级数值上取 P95−P5。

    参考：设计文档 6.1 原本写的是逐帧分位数，此处按实测修正。
    """
    p = {**DEFAULTS, **(params or {})}
    per_note = []
    for n in notes:
        i0, i1 = _frames(n, hop_length, sr)
        seg = rms[i0:i1]
        if seg.size:
            per_note.append(float(np.mean(seg)))
    if len(per_note) < 3:
        return None, {"ok": False, "reason": f"可用音符太少（{len(per_note)}）"}

    a = np.array(per_note)
    lo_p, hi_p = p["dyn_percentiles"]
    lo, hi = np.percentile(a, lo_p), np.percentile(a, hi_p)
    if lo <= 0:
        return None, {"ok": False, "reason": "能量过低无法测动态范围"}
    return float(_db(hi) - _db(lo)), {
        "ok": True, "notes": len(per_note), "unit": "per_note",
        "p_low": round(float(lo), 5), "p_high": round(float(hi), 5),
    }


# ══════════════════════════════════════════════════════════════
# ② 乐句力度起伏
# ══════════════════════════════════════════════════════════════
def split_phrases(notes, params=None):
    """按换气点把音符分成乐句。

    换气点怎么找：音符之间的【时间间隔】超过阈值就是换气。
    这个做法成立是因为：唱的时候换气必然在音符之间，不会在音符中间。
    （音符中间的"停顿"已经被切分阶段处理掉了。）
    """
    p = {**DEFAULTS, **(params or {})}
    if not notes:
        return []
    phrases, cur = [], [notes[0]]
    for prev, n in zip(notes, notes[1:]):
        if n.start_ms - prev.end_ms >= p["phrase_gap_ms"]:
            phrases.append(cur)
            cur = [n]
        else:
            cur.append(n)
    phrases.append(cur)
    return [ph for ph in phrases if len(ph) >= p["phrase_min_notes"]]


def phrase_dynamics(notes, rms, sr, hop_length=PRECISE_HOP, params=None):
    """每个乐句内 RMS 峰谷差的均值（dB）。

    和指标①的区别：① 看【整首】的力度范围，② 看【每句内部】的起伏。
    一首歌可以整体变化很大（①高）但每句内部都很平（②低），反之亦然。
    """
    p = {**DEFAULTS, **(params or {})}
    phrases = split_phrases(notes, p)
    if not phrases:
        return None, {"ok": False, "reason": "分不出乐句（音符太少或全是换气）"}

    ranges = []
    for ph in phrases:
        segs = []
        for n in ph:
            i0, i1 = _frames(n, hop_length, sr)
            if i1 > i0:
                segs.append(rms[i0:i1])
        if not segs:
            continue
        a = np.concatenate(segs)
        if a.size < 3 or a.max() <= 0:
            continue
        lo_p, hi_p = p["phrase_percentiles"]
        lo, hi = np.percentile(a, lo_p), np.percentile(a, hi_p)
        if lo <= 0:
            continue
        ranges.append(float(_db(hi) - _db(lo)))
    if not ranges:
        return None, {"ok": False, "reason": "乐句里没有有效能量"}
    return float(np.mean(ranges)), {
        "ok": True, "phrases": len(phrases),
        "ranges_db": [round(r, 1) for r in ranges],
    }


# ══════════════════════════════════════════════════════════════
# ③ 速度弹性 rubato
# ══════════════════════════════════════════════════════════════
@dataclass
class RhythmDecomposition:
    """节奏偏差的三分量分解。对应设计文档 4.3。"""
    accuracy: Optional[float] = None      # 系统性偏移 |mean|  → 抢拍/拖拍
    stability: Optional[float] = None     # 残差 std           → 忽快忽慢
    rubato: Optional[float] = None        # 趋势 std           → 有控制的弹性（加分项）
    # ★ 2026-10-02：`accuracy` 改成"扣掉速度线之后的残差中位"，下面是配套字段
    #   · `raw_accuracy`   = 旧口径 |mean(偏差)|（含速度）—— 保留供对照与老报告回填
    #   · `tempo_removed_ms` = 被扣掉的那条速度线的 RMS 幅度（不静默：让"扣了多少"可见）
    raw_accuracy: Optional[float] = None
    tempo_removed_ms: Optional[float] = None
    # ★ 2026-10-02（同日第二次修正）：`accuracy` 是**绝对毫秒**，与乐句总时长
    #   **严格成正比**（实测：每音 250/500/1000/2000ms 时，accuracy 为
    #   15.9/31.7/63.4/126.8ms，而 acc÷总长 恒为 0.00151）。
    #   而 `score_rhythm` 的判据是**固定值**（15ms 满分 / 120ms 零分）
    #   ⇒ 同一个"相对节奏水平"，在长乐句上必然零分、在短乐句上容易满分。
    #   所以再导出一个**与长度无关**的相对量：
    #       `accuracy_rel`    = accuracy ÷ 乐句跨度（毫秒）→ 无量纲
    #       `phrase_span_ms`  = 用到的那个跨度，报出来便于复核
    accuracy_rel: Optional[float] = None
    phrase_span_ms: Optional[float] = None
    trend: list = field(default_factory=list)
    residual: list = field(default_factory=list)
    raw: list = field(default_factory=list)
    ok: bool = False
    reason: str = ""
    # ★ 整体**时长比**（演唱用时 / 参考用时）。1.0 = 完全同速。
    #   ⚠️ 是时长比，不是速度比：1.27 表示"多花了 27% 的时间"，也就是**慢**。
    #      要换算成人话里的"快慢百分之几"，得取倒数。
    #   为什么单独算它：见 `tempo_ratio()` 的说明 —— 它把"选了另一个速度"
    #   与"忽快忽慢"分开，而这两件事在清唱里必须分开讲。
    tempo_ratio: Optional[float] = None


def tempo_ratio(aligned) -> Optional[float]:
    """整体**时长比** = 演唱用时 / 参考用时。只对**节奏指标**有意义。

    ⚠️ 命名与方向的坑：这个量是"时长比"（>1 表示更慢），
      但人话里的"你比参考快/慢百分之几"说的是**速度**（= 1/时长比）。
      第一版把它当速度比直接 `(r−1)×100` 显示，于是一个慢了 27% 的录音
      被写成"你比参考快了 27%" —— 方向完全相反，而且看起来毫无破绽。
      凡是这种"比值 + 方向"的量，都要在**消费端**再确认一次方向。

    ━━━ 为什么需要单独报这个量 ━━━

    实测（`experiments/_why_rhythm_tempo.py`）三段"清唱"素材：
    分别比参考**慢 17% / 6% / 28%**。而 `score_rhythm` 的「准确度」判据是
    `|onset偏差均值|`，上限 120ms —— 选慢 20% 就**直接清零**。

    问题在于：**清唱没有外部拍子**。没有伴奏、没有节拍器时，用户按自己的速度走，
    "绝对时刻对得上参考"这件事根本无从谈起 —— 它不是"你节奏不好"，
    而是"你选的速度不同"。把这两件事混成一个分数，用户会读成
    "我节奏感很差"，而真相是他只是唱慢了一点。

    （`项目记忆.md` §53 有完整推导。产品决定：**要求戴耳机跟伴奏唱**，
      这个量因此必须被**报出来** —— 让用户知道自己有没有跟住，
      而不是沉默地扣分。它是给用户的反馈，不是惩罚。）

    算法：onset 偏差 d 与**参考起点时刻**成近似线性关系。
      演唱时长 ×r ⇒ 演唱起点时刻 ×r ⇒ d ≈ (r−1)·t_ref。
      所以对 (t_ref, d) 做带截距的一元线性拟合，斜率 + 1 就是**时长比**。
      ⚠️ 必须带截距：整句抢拍/拖拍会体现为截距，混进斜率里会污染它
        （实测：带截距与不带截距差 0.02~0.05，正好是"整体偏移"那一项）。
      ⚠️ 样本量要够：5~7 个音时，换一种拟合口径（对 segments 起点最小二乘
        vs 对 ref_start_ms 拟合）就能差 0.11。A1 每遍至少 20 个音才谈得上稳。

    返回 None 表示样本太少（<4 个音）或参考时长跨度为 0，算不出来。
    """
    pts = []
    for m in aligned or []:
        d = getattr(m, "onset_dev_ms", None)
        r = getattr(m, "ref_start_ms", None)
        if d is None:
            continue
        if r is None:
            # 退化路径：没有参考起点就用"演唱起点 − 偏差"反推：
            #   onset_dev = sung_start − ref_start  ⇒  ref_start = sung_start − dev
            s = getattr(m, "start_ms", None)
            if s is None:
                continue
            r = s - d
        pts.append((float(r), float(d)))
    if len(pts) < 4:
        return None
    t = np.array([p[0] for p in pts], dtype=float)
    d = np.array([p[1] for p in pts], dtype=float)
    if not np.isfinite(t).all() or not np.isfinite(d).all():
        return None
    if float(np.ptp(t)) < 1e-6:
        return None                       # 所有音符同一时刻 → 斜率无意义
    slope = float(np.polyfit(t, d, 1)[0])
    ratio = 1.0 + slope
    if not np.isfinite(ratio) or ratio <= 0.05:
        return None                       # 拟合发散，宁可给 None 也不给垃圾值
    return float(ratio)


def _ref_dev_pairs(aligned) -> list:
    """取出 `(参考起点时刻, 起音偏差)` 对。

    与 `tempo_ratio` 用同一套兜底逻辑（没有 `ref_start_ms` 就反推），
    这样两处看到的是同一批点、不会有口径差。
    """
    pts = []
    for m in aligned or []:
        d = getattr(m, "onset_dev_ms", None)
        if d is None:
            continue
        r = getattr(m, "ref_start_ms", None)
        if r is None:
            s = getattr(m, "start_ms", None)
            if s is None:
                continue
            r = s - d
        pts.append((float(r), float(d)))
    return pts


def tempo_line(aligned, params=None):
    """拟合"偏差 ~ 参考时刻"的直线，返回 `(slope, intercept)`；算不出返回 None。

    这条线就是**"演唱者按自己的速度匀速唱"时偏差应有的样子**：
      · `slope` = 时长比 − 1（`slope > 0` ⇒ 比参考慢）
      · `intercept` = 整体抢拍/拖拍量

    ⚠️ 为什么需要它（2026-10-02）：`accuracy = |mean(偏差)|` 实际上主要在测
      **"演唱者选了什么速度"**。实测把参考缩放到演唱者自己的速度后，
      accuracy 下降 38–85%（take03: 2968ms → 443ms）。而 accuracy 是**参与算分**的
      （`ramp(acc, 15, 120)`）⇒ 选慢 20% 就**直接清零**。
      有了这条线就能把"速度"那一项从偏差里减掉，剩下的才是"节奏"。

    样本不足 4 个、或参考跨度近 0 时返回 None（宁可不算，也不给垃圾值）。
    """
    pts = _ref_dev_pairs(aligned)
    if len(pts) < 4:
        return None
    t = np.array([p[0] for p in pts], dtype=float)
    d = np.array([p[1] for p in pts], dtype=float)
    if not np.isfinite(t).all() or not np.isfinite(d).all():
        return None
    if float(np.ptp(t)) < 1e-6:
        return None
    try:
        slope, intercept = np.polyfit(t, d, 1)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not np.isfinite(slope) or not np.isfinite(intercept):
        return None
    return float(slope), float(intercept)


def decompose_rhythm(aligned, params=None) -> RhythmDecomposition:
    """把 onset 偏差序列拆成「准确度 / 稳定性 / 弹性」三分量。

    ★ 这是全项目最有想法的一处设计：
        偏差的【均值】大   → 唱不准（整体抢拍或拖拍）
        偏差的【残差】大   → 不稳（忽快忽慢）
        偏差的【趋势】有结构 → rubato，是艺术处理，**加分而非扣分**

    做法：滑动中位数提取慢变趋势，剩下的就是残差。
    用中位数而不是均值，是因为要抗单个音符的切分误差。
    """
    p = {**DEFAULTS, **(params or {})}
    raw = [m.onset_dev_ms for m in aligned if getattr(m, "onset_dev_ms", None) is not None]
    if len(raw) < p["rubato_min_notes"]:
        return RhythmDecomposition(raw=[float(x) for x in raw],
                                   reason=f"音符太少（{len(raw)} < {p['rubato_min_notes']}）",
                                   tempo_ratio=tempo_ratio(aligned))

    d = np.asarray(raw, dtype=float)
    raw_acc = abs(float(np.mean(d)))          # 旧口径：|mean(偏差)|（含速度）
    mu = float(np.mean(d))
    dz = d - mu                                     # 去掉系统性偏移
    win = p["rubato_trend_win"]
    win = win if win % 2 == 1 else win + 1

    # ── ★ 2026-10-02：先扣掉"速度线"，再分解 ──
    #   原来 `accuracy = |mean(偏差)|` 把"选了什么速度"与"节奏准不准"混在一起。
    #   偏差与参考时刻近似线性（`d ≈ slope·t_ref + intercept`），
    #   所以"整体慢"表现成一条长斜坡，被 |mean| 整个吃进 accuracy ——
    #   而 accuracy 参与算分（ramp(acc, 15, 120)）⇒ 唱得慢就直接清零。
    line = tempo_line(aligned, params)
    tempo_part = 0.0
    if line is not None:
        slope, intercept = line
        pts = _ref_dev_pairs(aligned)
        t = np.array([q[0] for q in pts], dtype=float)
        trend_line = slope * t + intercept
        d_adj = d - trend_line
        # 报出"被扣掉了多少" —— 不静默。它是"整段速度差 + 整体偏移"的幅度。
        tempo_part = float(np.sqrt(np.mean(trend_line ** 2)))
        dz = d_adj - float(np.mean(d_adj))
    else:
        d_adj = d

    if win >= 3 and dz.size >= win:
        from scipy.ndimage import median_filter
        trend = median_filter(dz, size=win, mode="nearest")
    else:
        trend = np.zeros_like(dz)
    resid = dz - trend

    # ── ★ 相对量：accuracy ÷ 乐句跨度 ──
    #   为什么必须报它：`accuracy` 是绝对毫秒，与乐句长度严格成正比
    #   （见 dataclass 里的实测数字）。判据若用固定毫秒，就等于
    #   "长曲子更难拿分" —— 那不是演唱水平，是曲子长度。
    span_ms = None
    acc_rel = None
    if line is not None:
        pts2 = _ref_dev_pairs(aligned)
        if pts2:
            t2 = np.array([q[0] for q in pts2], dtype=float)
            span_ms = float(np.ptp(t2))
            acc_abs = float(np.sqrt(np.mean(d_adj ** 2)))
            if span_ms > 1.0:
                acc_rel = acc_abs / span_ms

    return RhythmDecomposition(
        # ★ 新口径：**偏离"自己那条速度线"多远**（RMS）。
        #
        #   ⚠️ 这里连着写错了两版，教训留下：
        #     ① 第一版 `|median(resid)|` —— 残差按构造中位近 0，**永远给 0**，
        #        是个不反映任何东西的摆设。
        #     ② 第二版 `MAD(resid)` —— 又踩了另一个坑：`median_filter(dz, size=5)`
        #        会把**平滑的抛物线原样复现**（28 个点、曲线够平滑，
        #        5 点中位数正好落在曲线上）⇒ `resid ≡ 0` ⇒ 仍然给 0。
        #        也就是说"非线性越唱越拖"被 `rubato` 独吞了，`accuracy` 拿不到。
        #     ⇒ 所以 `accuracy` 直接量**原始偏差离那条拟合直线的距离**，
        #        不经 `resid`（那条路上已经有 `rubato` 了）。
        #        它对"线性漂移"仍不敏感（那些点就落在直线上），
        #        只对**非线性**偏差起反应 —— 那正是"越唱越拖/越唱越赶"。
        accuracy=float(np.sqrt(np.mean(d_adj ** 2))) if line is not None else None,
        stability=float(np.std(resid)),
        rubato=float(np.std(trend)),
        raw_accuracy=raw_acc,
        tempo_removed_ms=tempo_part,
        accuracy_rel=acc_rel,
        phrase_span_ms=span_ms,
        trend=[round(float(x), 1) for x in trend],
        residual=[round(float(x), 1) for x in resid],
        raw=[round(float(x), 1) for x in d],
        ok=True, reason="",
        # ★ 速度比不受 `rubato_min_notes` 那个提前返回影响 —— 它只要 4 个音、
        #   也不依赖趋势分解。所以放在这里算，音符太少时仍可能拿到值。
        tempo_ratio=tempo_ratio(aligned),
    )


# ══════════════════════════════════════════════════════════════
# ④ 颤音 ★ 最难的一项
# ══════════════════════════════════════════════════════════════
@dataclass
class VibratoResult:
    rate_hz: Optional[float] = None
    depth_semitone: Optional[float] = None
    notes_used: int = 0
    per_note: list = field(default_factory=list)
    ok: bool = False
    reason: str = ""


def vibrato_of_note(note, f0, sr, hop_length=PRECISE_HOP, params=None):
    """单个音符的颤音估计。

    ★ f0 必须是【短窗】提的音高（见 DEFAULTS 里的 vibrato_frame_length 说明）。
      用默认长窗会因窗内平均效应导致深度只恢复约 58%。

    返回 (rate_hz, depth_semitone, quality) 或 (None, None, 原因)

    步骤：
      1 取音符的音高 → 转成相对该音符中位音高的【音分】序列
      2 **去趋势** —— 这一步最关键：不去掉慢变漂移，滑音会在
        低频段产生巨大能量，把 4–8Hz 的颤音峰淹没
      3 加 Hann 窗（减少频谱泄漏）
      4 补零到 256 点做 FFT（否则分辨率只有 3.3Hz，分不清 5Hz 和 8Hz）
      5 在 4–8.5Hz 里找峰 → 速率；峰幅度 ×2/Σw → 深度
    """
    p = {**DEFAULTS, **(params or {})}
    fm = hop_length / sr
    dur_ms = note.end_ms - note.start_ms
    if dur_ms < p["vibrato_min_dur_ms"]:
        return None, None, f"音符太短（{dur_ms}ms < {p['vibrato_min_dur_ms']}ms）"

    i0, i1 = _frames(note, hop_length, sr)
    i1 = min(i1, len(f0))
    seg = np.asarray(f0[i0:i1], dtype=float)

    # ★ 跳过音符边缘。颤音分析用的是【短窗 YIN】（为保住颤音的保真度），
    #   它的估计噪声远大于主流程的长窗 PYIN —— 尤其在淡入淡出段会乱跳，
    #   FFT 会把这些抖动当成振荡。
    #   实测：一段「力度恒定、无颤音」的合成演唱被检出 5.54 半音峰峰的颤音，
    #   而该音符实际的音高标准差只有 0.052 半音 —— 自相矛盾。
    edge = max(int(round(p["vibrato_edge_skip_ms"] / (hop_length / sr * 1000.0))), 1)
    if seg.size > 4 * edge:
        seg = seg[edge:-edge]

    ok_mask = np.isfinite(seg) & (seg > 0)
    if ok_mask.sum() < 8:
        return None, None, "有效音高帧不足"

    # 1–2 转音分
    cents = 100.0 * (hz_to_midi(seg[ok_mask]) - np.median(hz_to_midi(seg[ok_mask])))
    # 轻度中值滤波去掉孤立跳变。k=3 对 5.7Hz 的颤音几乎无损
    # （帧率 172Hz，颤音远低于奈奎斯特），但能挡掉短窗估计的孤立尖峰。
    if cents.size >= 5:
        from scipy.signal import medfilt
        cents = medfilt(cents, kernel_size=3)
    n = cents.size
    std_cents = float(np.std(cents))

    lo, hi = p["vibrato_band_hz"]
    cycles = (n * fm) * p["vibrato_cycles_ref_hz"]         # 按参考频率算周期数（不用频带下沿）
    if cycles < p["vibrato_min_cycles"]:
        return None, None, (f"窗内周期数不足（{cycles:.1f} < "
                            f"{p['vibrato_min_cycles']}），频谱不可信")

    # 3 去趋势：减去低阶多项式，去掉滑音/漂移
    t = np.arange(n, dtype=float)
    deg = 2 if n >= 6 else 1
    try:
        coef = np.polyfit(t, cents, deg)
        det = cents - np.polyval(coef, t)
    except Exception:
        det = cents - np.mean(cents)

    # 4 加窗
    w = np.hanning(n)
    x = det * w

    # 5 补零 FFT
    N = 256
    spec = np.abs(np.fft.rfft(x, n=N))
    freqs = np.fft.rfftfreq(N, d=fm)

    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return None, None, "频带内无频点"
    idx = np.flatnonzero(band)
    k_local = int(np.argmax(spec[band]))
    k = int(idx[k_local])
    peak_f = float(freqs[k])
    peak_a = float(spec[k])

    # ★ 边界判据：峰落在频带两端时，要分清两种情况 ——
    #     (a) 没除干净的低频漂移：[能量从低端单调衰减]，峰必然在下沿，是趋势残留
    #     (b) 真实颤音恰好接近频带下沿：峰两侧都有支撑，谱形是个"包"
    #
    #   ⚠️ 2026-09-19 修正：原来只看"峰是不是落在边缘 N 个频点内"就一律拒绝，
    #      在**短音符**上误杀严重。实测（`experiments/_diag_vibrato.py`，
    #      40 秒真实录音 55 个长音）：**92 次拒绝里绝大多数是这个判据**，
    #      而被拒的峰频率集中在 **4.04 / 4.19 / 3.36 / 8.75 Hz** ——
    #      4–5 Hz 正是男声颤音的主体频率！
    #      根因：FFT 分辨率 df=0.838Hz（N=256, hop=128@22050），
    #      而频带只有 3–9Hz 宽 → 下沿第一个频点就是 4.19Hz，
    #      edge_bins=1 等于把 4.2Hz 以下全判死，把真颤音一起拒了。
    #      改法：边缘峰再验一步"谱形是否单调衰减向频带内侧"，
    #      只有单调衰减才判为趋势残留。
    interior = spec[idx]
    if k_local <= p["vibrato_edge_bins"] or k_local >= len(idx) - 1 - p["vibrato_edge_bins"]:
        if k_local <= p["vibrato_edge_bins"]:
            seq = interior[: max(3, k_local + 3)]          # 从下沿往内看
        else:
            seq = interior[max(0, k_local - 2):][::-1]     # 从上沿往内看
        mono_decay = bool(seq.size >= 3 and np.all(np.diff(seq) <= 0))
        if mono_decay:
            return None, None, (f"谱峰落在频带边缘（{peak_f:.2f} Hz）且能量向带内"
                                "单调衰减，判定为趋势残留而非颤音")
        # 否则：边缘峰但两侧有支撑 → 当作真颤音，继续走后面的判据

    # ★ 抛物线插值求亚格精度。
    #   踩过的坑：不做插值时，速率精度就只有 FFT 的频率分辨率
    #     df = 1/(N · hop/sr) = 1/(256 × 0.005805) = **0.673 Hz**
    #   在 4–8 Hz 的颤音范围里是 **8–17% 的误差**，而且取值被量化到固定的几个格点上。
    #   实测：两段完全不同的录音（男声 C3 / 女声 E4）中位数都落在 5.38 Hz ——
    #   看起来"一样"，其实是被量化了。**我曾据此得出"女声颤音更快"的结论，那是假象。**
    #   用峰值格与左右邻格拟合抛物线，可把精度提到远优于一格。
    if 0 < k_local < len(idx) - 1:
        y0, y1, y2 = float(spec[k - 1]), float(spec[k]), float(spec[k + 1])
        den = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
        delta = float(np.clip(delta, -0.5, 0.5))
        df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0
        peak_f = peak_f + delta * df

    # 6 速率与深度
    # ⚠️ 幅度公式：实数信号补零 FFT 后，单边谱在峰处的幅度 ≈ A·Σw/2，
    #    所以 A = 2·peak/Σw。曾写成 2·peak/(coherent·n/2) = 4·peak/Σw，多了一倍。
    sw = float(np.sum(w))
    amp_cents = 2.0 * peak_a / max(1e-9, sw)
    depth_semi = amp_cents / 100.0                          # 峰到零 → 半音

    # ★ 自洽检验（原理性的，不是调参）：
    #   纯正弦的幅度 = 标准差 × √2。所以从 FFT 反推出的幅度
    #   **不可能超过原始序列标准差的 1.6 倍**。
    #   实测抓到过「检出 277 音分、但 std 只有 5.2 音分」的矛盾结果，
    #   那一定是频谱泄漏或噪声被误读成峰。
    if std_cents > 1e-6 and amp_cents > std_cents * p["vibrato_amp_consistency"]:
        return None, None, (f"幅度与序列标准差矛盾（{amp_cents:.0f} 音分 vs "
                            f"std {std_cents:.1f} 音分），判定为噪声")

    # 噪声底：用【峰附近之外】的局部中位数，而不是整个频带的中位数。
    # 整个频带的中位数会被颤音峰本身抬高（颤音峰通常就占满 4–8.5Hz 这个窄带），
    # 导致信噪比恒为 1 点几，判据失效。曾因此把所有颤音都判成"未检出"。
    excl = (np.abs(freqs - peak_f) > 1.2) & (freqs >= 1.0) & (freqs <= 14.0)
    noise = float(np.median(spec[excl])) if excl.any() else float(np.median(spec[band]))
    snr = peak_a / max(1e-9, noise)

    if amp_cents < p["vibrato_min_depth_cents"] or snr < p["vibrato_min_snr"]:
        return None, None, (f"未检出明显振荡（深度 {amp_cents:.0f} 音分，"
                            f"信噪比 {snr:.1f} < {p['vibrato_min_snr']}）")
    return peak_f, depth_semi, {
        "ok": True, "dur_ms": dur_ms, "frames": int(n),
        "cycles": round(cycles, 2), "depth_cents": round(amp_cents, 1),
        "snr": round(snr, 2),
    }


def vibrato(notes, y, sr, params=None) -> VibratoResult:
    """整段演唱的颤音估计。

    ★ 内部用【短窗】重新提一次音高 —— 这是本指标能测准的前提。
      用主流程的长窗音高，深度只能恢复约 58%（实测）。
      代价是多跑一次 YIN（比 PYIN 快约 55 倍，几毫秒级，可接受）。

    ★ 这个指标最容易"算不出来"，而且**算不出来是正常结果**：
      初学者往往没有稳定的颤音。契约规定此时进 metrics_excluded，
      UI 显示"本次未评估"，不能给 0 分 —— 0 分会让人以为"颤音很差"。
    """
    from .pitch import extract_f0_yin

    p = {**DEFAULTS, **(params or {})}
    res = VibratoResult()

    fl = p["vibrato_frame_length"]
    hop = p["vibrato_hop_length"]
    f0s = extract_f0_yin(y, sr, frame_length=fl, hop_length=hop)

    rates, depths = [], []
    for n in notes:
        r, d, q = vibrato_of_note(n, f0s, sr, hop, p)
        res.per_note.append({"note_index": n.note_index,
                             "dur_ms": n.end_ms - n.start_ms,
                             "rate_hz": r, "depth_semitone": d,
                             "quality": q if isinstance(q, str) else q.get("ok")})
        if r is not None:
            rates.append(r); depths.append(d)

    res.notes_used = len(rates)
    if len(rates) < p["vibrato_min_notes"]:
        res.ok = False
        res.reason = (f"只有 {len(rates)} 个音符检出颤音"
                      f"（需 ≥ {p['vibrato_min_notes']} 个）—— 可能本来就没有稳定颤音")
        return res
    res.rate_hz = float(np.median(rates))
    res.depth_semitone = float(np.median(depths))
    res.ok = True
    res.reason = ""
    return res


# ══════════════════════════════════════════════════════════════
# ⑤ 起音方式
# ══════════════════════════════════════════════════════════════
def onset_style(notes, rms, sr, hop_length=PRECISE_HOP, params=None):
    """音符起始处能量从 10% 升到 90% 的平均时间（ms）。

    音乐学依据：硬起音（<30ms）有爆发力，柔起音（>100ms）抒情。
    """
    p = {**DEFAULTS, **(params or {})}
    rises = []
    for n in notes:
        i0, i1 = _frames(n, hop_length, sr)
        seg = rms[i0:min(i1, len(rms))]
        if seg.size < 4:
            continue
        peak = float(seg.max())
        if peak <= 0:
            continue
        lo_th = peak * p["onset_low_pct"] / 100.0
        hi_th = peak * p["onset_high_pct"] / 100.0
        # 在起始附近找第一次越过阈值的帧
        above_lo = np.flatnonzero(seg >= lo_th)
        above_hi = np.flatnonzero(seg >= hi_th)
        if above_lo.size == 0 or above_hi.size == 0:
            continue
        a, b = int(above_lo[0]), int(above_hi[0])
        if b < a:
            continue
        ms = (b - a) * hop_length / sr * 1000.0
        if ms <= p["onset_max_ms"]:
            rises.append(ms)
    if len(rises) < 3:
        return None, {"ok": False, "reason": f"可用起音太少（{len(rises)}）"}
    return float(np.mean(rises)), {
        "ok": True, "count": len(rises),
        "median_ms": round(float(np.median(rises)), 1),
        "std_ms": round(float(np.std(rises)), 1),
    }


# ══════════════════════════════════════════════════════════════
# ⑥ 音色明亮度
# ══════════════════════════════════════════════════════════════
def brightness(notes, y, sr, hop_length=PRECISE_HOP, params=None):
    """谱质心均值（Hz）。音乐学依据：与唤醒度/情绪强度相关。"""
    p = {**DEFAULTS, **(params or {})}
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
    vals = []
    for n in notes:
        i0, i1 = _frames(n, hop_length, sr)
        seg = cent[i0:min(i1, len(cent))]
        if seg.size:
            vals.append(seg)
    if not vals:
        return None, {"ok": False, "reason": "没有音符"}
    a = np.concatenate(vals)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None, {"ok": False, "reason": "谱质心全为无效值"}
    return float(np.mean(a)), {"ok": True, "frames": int(a.size)}


# ══════════════════════════════════════════════════════════════
# 汇总：一次算出六个指标
# ══════════════════════════════════════════════════════════════
def compute_all(notes, y, sr, f0, aligned=None, hop_length=PRECISE_HOP,
                params=None) -> dict:
    """算出六个表现力指标 + 节奏三分量。

    返回：
      {
        "dynamic_range": 8.2, "phrase_dynamics": 5.4, "rubato": 18.0,
        "vibrato_rate": 5.7, "vibrato_depth": 0.61,     # 算不出时为 None
        "onset_style": 42.0, "brightness": 1320.0,
        "rhythm": {accuracy, stability, rubato, trend, residual, raw},
        "excluded": ["vibrato"],        # 算不出来的 → 契约 metrics_excluded
        "quality": { ... },             # 每个指标的质量信息
        "vibrato_detail": { ... },      # 颤音逐音符明细，便于排查
      }
    """
    p = {**DEFAULTS, **(params or {})}
    rms = rms_envelope(y, sr, hop_length)
    out: dict = {"quality": {}, "excluded": []}

    def put(key, fn, *a, **kw):
        val, q = fn(*a, **kw)
        out[key] = val
        out["quality"][key] = q
        if val is None:
            out["excluded"].append(key)
        return val

    put("dynamic_range", dynamic_range, notes, rms, sr, hop_length, p)
    put("phrase_dynamics", phrase_dynamics, notes, rms, sr, hop_length, p)
    put("onset_style", onset_style, notes, rms, sr, hop_length, p)
    put("brightness", brightness, notes, y, sr, hop_length, p)

    # 颤音：两个输出字段，但只要一个算不出就两个都算不出
    vr = vibrato(notes, y, sr, p)
    out["vibrato_detail"] = {
        "notes_used": vr.notes_used, "reason": vr.reason,
        "per_note": vr.per_note,
    }
    if vr.ok:
        out["vibrato_rate"] = vr.rate_hz
        out["vibrato_depth"] = vr.depth_semitone
        out["quality"]["vibrato"] = {"ok": True, "notes_used": vr.notes_used}
    else:
        out["vibrato_rate"] = None
        out["vibrato_depth"] = None
        out["quality"]["vibrato"] = {"ok": False, "reason": vr.reason}
        out["excluded"].append("vibrato")

    # 节奏三分量（从对齐结果来）
    dec = decompose_rhythm(aligned or [], p)
    out["rhythm"] = {
        "accuracy": dec.accuracy, "stability": dec.stability,
        "rubato": dec.rubato, "trend": dec.trend, "residual": dec.residual,
        "raw": dec.raw, "ok": dec.ok, "reason": dec.reason,
        # ★ 整体速度比也要往上带。它**不算分**（rubric 一个字没改），
        #   是给用户的反馈："你比参考慢了 22%" —— 比沉默地扣分有用得多。
        #   （产品决定见 docs/实验操作卡.html：要求戴耳机跟伴奏唱，
        #     所以这个数就是"你有没有跟住"的直接证据。）
        "tempo_ratio": dec.tempo_ratio,
        # ★ 2026-10-02：`accuracy` 改成"偏离自己速度线的 RMS"后的配套字段。
        #   ⚠️ 这里**是手工重建 dict** —— 新增字段必须在这里补一行，
        #      否则 `decompose_rhythm` 算出来了也会在这一层丢掉
        #      （实测踩过：`accuracy_rel` 在直接调用时为 0.012，
        #        走管线却是 None，就是因为漏了这里）。
        "raw_accuracy": dec.raw_accuracy,
        "tempo_removed_ms": dec.tempo_removed_ms,
        "accuracy_rel": dec.accuracy_rel,
        "phrase_span_ms": dec.phrase_span_ms,
    }
    if dec.ok:
        out["rubato"] = dec.rubato
    else:
        out["rubato"] = None
        out["excluded"].append("rubato")
    out["quality"]["rubato"] = {"ok": dec.ok, "reason": dec.reason}

    return out


def feature_stats(feats: dict) -> dict:
    """给出一句话结论：六个指标里哪些能算、哪些算不出。"""
    names = {
        "dynamic_range": "力度动态范围", "phrase_dynamics": "乐句力度起伏",
        "rubato": "速度弹性", "vibrato": "颤音",
        "onset_style": "起音方式", "brightness": "音色明亮度",
    }
    usable = [k for k in names if k not in feats.get("excluded", [])]
    missing = feats.get("excluded", [])
    return {
        "usable": usable,
        "usable_labels": [names[k] for k in usable],
        "missing": missing,
        "missing_labels": [names[k] for k in missing if k in names],
        "count": len(usable),
        "ok": len(usable) >= 4,
        "reason": (f"可用 {len(usable)}/6 项" +
                   (f"；缺：{'、'.join(names[k] for k in missing if k in names)}"
                    if missing else "")),
        "vibrato_reason": feats.get("vibrato_detail", {}).get("reason", ""),
    }
