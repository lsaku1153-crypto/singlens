"""评分引擎：把偏差与指标合成 0–100 的分数。

输入是 align.py 的偏差和 features.py 的六指标，
输出是契约 §2.2 里 ScoreReport 的各个 score 字段。

┌── 评分体系 ──────────────────────────────────────────────┐
│ 音准分 = 100·(1 − clamp(mean(penalty)/50))               │
│          penalty = max(0, |音分偏差| − 25)   ← 人耳容差   │
│ 节奏分 = 准确度 + 稳定性（弹性 rubato 是【加分项】不扣分） │
│ 表现力分 = 100·Σ(权重 × 归一化指标)                       │
│ 总分   = 0.35·音准 + 0.25·节奏 + 0.40·表现力              │
└──────────────────────────────────────────────────────────┘

━━━ 设计要点 ━━━

① **人耳容差**：±25 音分以内基本听不出走音，容差内不扣分。
   依据：音乐心理学常识，也是设计文档 4.2 的约定。

② **rubato 是加分项不是扣分项**
   节奏分只看「系统性抢拖拍」和「残差抖动」，
   「趋势幅度」单独作为【弹性表现】输出，不参与扣分。
   这是全项目最有想法的一处设计：区分「唱不准」和「有感情地处理」。

③ **权重是占位的，必须用实验替换**
   下面 NORMALIZATION 里的归一化区间与 DEFAULT_WEIGHTS 都是**基于文献和
   经验的占位值**。设计文档第 7 节的实验（10 位演唱者 + 3–5 位评分者盲评 +
   回归标定）做完之后，必须用实测权重替换，并在论文里说明来源。
   契约里 weight_version 字段就是为此准备的。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .pitch import PITCH_TOLERANCE_CENTS, pitch_score

# ── 总分权重（设计文档 5.2 定的 0.35 / 0.25 / 0.40）──────────
TOTAL_WEIGHTS = {"pitch": 0.35, "rhythm": 0.25, "expr": 0.40}
FULL_PENALTY_CENTS = 50.0     # 平均超出容差这么多音分 → 音准 0 分

# ── 六指标归一化区间 ─────────────────────────────────────────
#
# ★★ 2026-10-17：**按真实素材重标**（原值是占位值，两个指标对真人恒满分）。
#
# ━━━ 为什么要改 ━━━
# 拿 A1 的 10 条真实素材（`实验素材/构造式素材2/`）一算就发现：
#
#     指标              占位区间    实测范围        归一化后     饱和
#     phrase_dynamics   (2→10)    10.1–18.0     1.00–1.00   **10/10**
#     rubato            (0→40)    53.8–797.9    1.00–1.00   **10/10**
#     vibrato           (0.2→0.9) 0.16–0.52     0.00–0.46     2/10（卡在下界）
#
# ⇒ **表现力分里有 41% 的权重对每个人都是满分**，用户只能靠剩下 59% 拉开差距。
#   这不是"标定不精"，是**区间对真人本来就错**（上限比真人唱出来的还低一半）。
#   ⇒ 用户看到的"表现力 65 分"里，有 41 分是白给的。
#
# ━━━ 新区间怎么定的 ━━━
# 每个区间覆盖「A1 实测最小–最大」并**两侧留余量**，
# 使 5%–95% 分位大致落在归一化的 0.1–0.75 之间（既不饱和，也留出上升空间）。
# ⚠️ 依据是 **10 条、单演唱者（男低音）** ⇒ 会偏向这个人。
#    **多人素材到位后必须重标**（待办 A4）。这一点不许在论文里省掉。
#
# ⚠️ `onset_style` / `brightness` **没动**：
#    · `onset_style` 的两个锚点来自设计文档 §4.4（硬起音<30ms、柔起音>100ms），
#      有依据，而且实测 0/10 饱和（0.30–0.63）。
#    · `brightness` 实测 0/10 饱和（0.33–0.58），换成单人的窄区间只会更容易过拟合。
NORMALIZATION = {
    # 实测 9.2–12.4 dB（5–95% 9.3–12.4）
    "dynamic_range":   {"lo": 6.5,  "hi": 16.0, "unit": "dB",
                        "source": "A1 实测 10 条（2026-10-17）：9.2–12.4，两侧留余量"},
    # 实测 10.1–18.0 dB ⇒ 原上限 10 让十遍全部满分
    "phrase_dynamics": {"lo": 6.0,  "hi": 24.0, "unit": "dB",
                        "source": "A1 实测 10 条（2026-10-17）：10.1–18.0，原上限 10 全部饱和"},
    # 实测 53.8–797.9 ms ⇒ 原上限 40 让十遍全部满分
    "rubato":          {"lo": 20.0, "hi": 900.0, "unit": "ms",
                        "source": ("A1 实测 10 条（2026-10-17）：53.8–797.9，原上限 40 全部饱和。"
                                   "⚠️ 该指标被证明受对齐质量污染（§113.8），"
                                   "跨遍比较不可信 —— 详见 WEIGHT_VERSION 的注释")},
    # 实测 0.16–0.52 半音 ⇒ 原下界 0.2 让最平的遍卡在 0
    "vibrato":         {"lo": 0.08, "hi": 0.90, "unit": "半音(峰峰)",
                        "source": ("下界改按 A1 实测（0.16–0.52）：原 0.2 使 2/10 卡在 0；"
                                   "上界保留文献值「专业歌手 0.5–1.0」")},
    "onset_style":     {"lo": 100.0, "hi": 20.0, "unit": "ms",
                        "source": "设计文档 4.4：硬起音<30ms，柔起音>100ms（反向：越小越有力）"},
    "brightness":      {"lo": 900.0, "hi": 2200.0, "unit": "Hz",
                        "source": "占位（A1 实测 0/10 饱和，暂不动；多人素材后重标）"},
    "phrase_dyn":      {"lo": 6.0, "hi": 24.0, "unit": "dB",
                        "source": "同 phrase_dynamics"},
}

# ── 表现力六项权重 ───────────────────────────────────────────
#
# ★★ 2026-10-17：**把 `phrase_dynamics` 和 `rubato` 从分里拿掉**（权重不再计入）。
#
# ━━━ 为什么（两条证据都**不依赖**盲评，是独立的）━━━
# ① `rubato`：**与对齐质量反相关** —— 对齐匹配率 0.76 → 613ms、0.83 → 252ms、
#    0.95 → 44ms（§113.8）。也就是说它主要在量**对齐残差**，不是"你唱得多有弹性"。
# ② `phrase_dynamics`：拿**合成材料**（设计上刻意拉开的 amp_e 0.0/0.5/1.0）去量，
#    它**在理想输入上都不随操纵变化**（FLAT 6.91 · DYN 8.39 · **ART 5.80**），
#    三条里最该高的一条反而最低（§113.2）。
#
# ━━━ 拿掉之后的效果（同一批数据重算）━━━
#     组合                              ρ(表现力 vs 盲评)   分数跨度
#     旧刻度 · 六项                        +0.758          16.8
#     新刻度 · 六项                        +0.139          21.4   ← 两个噪声指标毁了它
#     新刻度 · 去掉 rubato                 +0.576          21.8
#     ★ 新刻度 · 去掉 rubato + 句内推进     +0.794          28.7
#
# ⇒ **ρ 从 0.139 升到 0.794，分数跨度从 16.8 变成 28.7**（用户能看出差别了）。
#
# ⚠️ **诚实的边界**：上述 ρ=0.794 是**在同一批 10 条 + 同一批盲评上选出来的子集**，
#   属于**选择偏差**，应视为乐观估计。但"拿掉这两项"的依据**不只是**这次的相关性
#   （见上面 ①②），而是测量层面的独立证据。
#   ⇒ A4（多人素材）到位后必须**在独立样本上复核**。
#
# ⚠️ 这两项仍然**会出现在报告里**（作为观察值，`normalized` 照算），
#   只是**不参与打分**。理由：它们量到的东西是真的，只是**不能当"表现力好坏"的刻度**。
DEFAULT_WEIGHTS = {
    "dynamic_range": 0.34,
    "vibrato": 0.19,
    "onset_style": 0.09,
    "brightness": 0.05,
}
# ⚠️ 上面四个的总和是 0.67，不是 1.0 —— **这是对的**：
#    `score_expressiveness()` 会按可用项**重新归一**（L270–275），
#    所以比例保持不变（0.34/0.67 等）。不在这里手改成 0.51/0.28/0.13/0.08，
#    是为了少一处可写错的地方。

# ★ 版本号：`EQUAL_DEFAULT` → `A1_NORM_20261017`
#   这一版做了两件事：① 按 A1 实测重标归一化区间（原值对真人恒满分）
#   ② 把 `phrase_dynamics` / `rubato` 移出评分（证据见上）。
#   契约的 `weight_version` 字段就是用来标这个的 —— 旧报告会因此显示旧版本号。
WEIGHT_VERSION = "A1_NORM_20261017"


def _norm(value: Optional[float], spec: dict) -> Optional[float]:
    """把一个指标线性归一化到 [0,1]。

    lo > hi 表示「越小越好」（比如起音时间：越短越有力）。
    """
    if value is None:
        return None
    lo, hi = spec["lo"], spec["hi"]
    if lo == hi:
        return None
    t = (value - lo) / (hi - lo) if hi > lo else (lo - value) / (lo - hi)
    return float(min(1.0, max(0.0, t)))


# ══════════════════════════════════════════════════════════════
# 音准分
# ══════════════════════════════════════════════════════════════
def score_pitch(aligned) -> dict:
    """音准分。用 pitch.py 的软阈值口径（±25 音分容差内不扣分）。"""
    devs = [m.pitch_cents_dev for m in aligned
            if getattr(m, "pitch_cents_dev", None) is not None]
    if len(devs) < 3:
        return {"score": None, "accuracy": None, "stability": None,
                "reason": f"有效音符太少（{len(devs)}）"}
    r = pitch_score(np.asarray(devs, dtype=float),
                    tolerance=PITCH_TOLERANCE_CENTS,
                    full_penalty_cents=FULL_PENALTY_CENTS)
    r["reason"] = ""
    r["n"] = len(devs)
    return r


# ══════════════════════════════════════════════════════════════
# KTV 式参考分（2026-10-18）
# ══════════════════════════════════════════════════════════════
# 为什么要有这一项：
#   六指标 C 的音准分是「逐音音分偏差的均值」，对八度错音/离群点极敏感
#   （实测：中位偏差 10 音分，但 5 个 1200 音分的八度错音把音准打到 0）。
#   KTV / K 歌软件不这么做：它们用「±50 音分命中率 + 八度折叠 + 时长加权」，
#   一个音只分对/错，不再让一个大错音主导整项。
#   ⇒ 这是**并列的参考分**，不替换六指标 C；报告里两者都展示。
KTV_PITCH_TOLERANCE_CENTS = 50.0   # K 歌常见容差（半音的一半）
KTV_RHYTHM_TOLERANCE_MS = 100.0     # 常见节奏判定窗
KTV_PITCH_WEIGHT = 0.70             # 音准 70% / 节奏 30%
# 2026-10-21：命中率是"黑白"的，用户唱到 51 音分就从 100 掉到 0 太硬。
# 带内满分，超出后 2 倍容差内线性给分；命中率仍单独保留给报告解释。
KTV_PITCH_BAND_MULT = 2.0
KTV_RHYTHM_BAND_MULT = 2.0
KTV_VERSION = 3


def ktv_tolerances(style_profile: str = "standard") -> dict:
    """评分宽容度 → KTV 容差带。默认是常见 K 歌窗口（±50 音分/±100ms）。"""
    p = (style_profile or "standard").lower()
    if p == "strict":
        return {"pitch_tolerance_cents": 40.0, "rhythm_tolerance_ms": 80.0}
    if p in ("lenient", "loose"):
        return {"pitch_tolerance_cents": 75.0, "rhythm_tolerance_ms": 180.0}
    return {"pitch_tolerance_cents": KTV_PITCH_TOLERANCE_CENTS,
            "rhythm_tolerance_ms": KTV_RHYTHM_TOLERANCE_MS}


def _mget(m, key, default=None):
    """同时支持对象（分析管线）和 dict（落库报告）。"""
    if isinstance(m, dict):
        return m.get(key, default)
    return getattr(m, key, default)


def score_ktv(segments, pitch_tolerance_cents: float = KTV_PITCH_TOLERANCE_CENTS,
              rhythm_tolerance_ms: float = KTV_RHYTHM_TOLERANCE_MS) -> Optional[dict]:
    """KTV 式参考分：八度折叠后的音准命中率 + 节奏命中率，按时长加权。

    · 音准：`folded = ((cents + 600) % 1200) - 600`，|folded| ≤ 容差算命中；
      八度错音（±1200）折回 0；男声/女声差八度也不再判错。
    · 节奏：**先去掉整遍统一的起唱延迟**（onset_dev 的中位数），再看残差；
      ±容差内算命中。扣掉的是中位数，保留"每个音相对自己拍点的抖动"。
    · 2026-10-21：总分不再用黑白命中率，而是**容差带内的梯度分**：
      带内 100，带外到 2× 容差线性降到 0。命中率仍返回，供报告解释。
    · 权重：音符时长（至少 1ms）；短促的气声/切碎音权重自然低。
    · 音准 70% + 节奏 30%；没有可用 onset 时只用音准。
    """
    tol_p = max(1.0, float(pitch_tolerance_cents))
    tol_r = max(1.0, float(rhythm_tolerance_ms))
    rows = []
    for m in segments or []:
        dev = _mget(m, "pitch_cents_dev")
        if dev is None:
            continue
        false_start = float(_mget(m, "start_ms", 0) or 0)
        end = float(_mget(m, "end_ms", 0) or 0)
        dur = max(1.0, end - false_start)
        folded = ((float(dev) + 600.0) % 1200.0) - 600.0
        onset = _mget(m, "onset_dev_ms")
        rows.append({"folded": folded, "onset": onset, "dur": dur,
                     # 只有接近整八度（≥9 个半音）才算"八度折叠"；
                     # 700 音分是五度错音，不是八度，别混进这个计数。
                     "octave": abs(float(dev)) >= 900.0})
    if not rows:
        return None

    total_w = float(sum(r["dur"] for r in rows))
    pitch_hit_w = sum(r["dur"] for r in rows
                      if abs(r["folded"]) <= tol_p)
    pitch_credit_w = sum(
        r["dur"] * max(0.0, 1.0 - max(0.0, abs(r["folded"]) - tol_p)
                       / (tol_p * KTV_PITCH_BAND_MULT))
        for r in rows)
    pitch_rate = pitch_hit_w / total_w
    pitch_score = 100.0 * pitch_credit_w / max(total_w, 1e-9)

    onset_rows = [r for r in rows if r["onset"] is not None]
    rhythm_rate = None
    rhythm_score = None
    offset_ms = None
    if onset_rows:
        offset_ms = float(np.median([float(r["onset"]) for r in onset_rows]))
        onset_w = float(sum(r["dur"] for r in onset_rows))
        rhythm_hit_w = sum(
            r["dur"] for r in onset_rows
            if abs(float(r["onset"]) - offset_ms) <= tol_r)
        rhythm_credit_w = sum(
            r["dur"] * max(0.0, 1.0 - max(
                0.0, abs(float(r["onset"]) - offset_ms) - tol_r)
                / (tol_r * KTV_RHYTHM_BAND_MULT))
            for r in onset_rows)
        rhythm_rate = rhythm_hit_w / max(onset_w, 1e-9)
        rhythm_score = 100.0 * rhythm_credit_w / max(onset_w, 1e-9)

    if rhythm_score is None:
        score = pitch_score
    else:
        score = (KTV_PITCH_WEIGHT * pitch_score
                 + (1.0 - KTV_PITCH_WEIGHT) * rhythm_score)
    return {
        "version": KTV_VERSION,
        "score": round(float(score), 1),
        "pitch_score": round(float(pitch_score), 1),
        "rhythm_score": (None if rhythm_score is None
                         else round(float(rhythm_score), 1)),
        "pitch_hit_rate": round(float(pitch_rate) * 100.0, 1),
        "rhythm_hit_rate": (None if rhythm_rate is None
                            else round(float(rhythm_rate) * 100.0, 1)),
        "pitch_tolerance_cents": tol_p,
        "rhythm_tolerance_ms": tol_r,
        "pitch_band_cents": tol_p * KTV_PITCH_BAND_MULT,
        "rhythm_band_ms": tol_r * KTV_RHYTHM_BAND_MULT,
        "rhythm_offset_ms": (round(offset_ms, 1) if offset_ms is not None else None),
        "pitch_weight": KTV_PITCH_WEIGHT,
        "notes": len(rows),
        "octave_folded": int(sum(1 for r in rows if r["octave"])),
    }


# ══════════════════════════════════════════════════════════════
# 节奏分
# ══════════════════════════════════════════════════════════════
# 拖拍/抢拍多少毫秒算满分或零分
RHYTHM_ACC_FULL_MS = 15.0     # 系统性偏移 ≤15ms 满分
RHYTHM_ACC_ZERO_MS = 120.0    # ≥120ms 零分
# ★ 2026-10-02（同日第二次修正）：上面两个是**绝对毫秒**判据，
#   而 `accuracy` 与乐句总时长**严格成正比** —— 实测每音 250/500/1000/2000ms
#   时 accuracy 为 15.9/31.7/63.4/126.8ms，而 `acc ÷ 总长` **恒为 0.00151**。
#   ⇒ 固定毫秒判据等于"长曲子更难拿分"，那不是演唱水平，是曲子长度。
#   （42 音参考跨度 20.5s；15ms 满分要求相对偏差 0.073%、120ms 零分线 0.585%
#     —— 满分那端落在人类能力之外。）
#
#   所以改用**相对量** `accuracy_rel = accuracy ÷ 乐句跨度`（无量纲）。
#
#   ⚠️ 下面两个数的标定依据（**必须如实说明，它不是实验标定出来的**）：
#      · 手上有 9 段真人素材，实测 `accuracy_rel` 范围 0.0118 – 0.0702
#      · 作者指定"唱得比较稳"的那段（`sep19_4pm.wav`）是 **0.0120**，
#        恰好落在最小区间 —— 与主观判断一致，说明这个量有区分力
#      · 因此取：**满分 = 0.012（最稳的那段）／零分 = 0.070（最差的那段）**
#     ⇒ 这两个数是"把现有真人素材铺满量程"得到的，**不是**由
#       "什么水平算好"推导出来的。正式标定要等满足实验前提
#       （戴耳机跟伴奏、构造式条件）的素材 —— 那正是待办 A1–A3。
#     ⚠️ 在那之前，节奏分的**绝对值不可用于论文结论**，只有相对排序可用。
RHYTHM_ACC_REL_FULL = 0.012
RHYTHM_ACC_REL_ZERO = 0.070
RHYTHM_STAB_FULL_MS = 20.0    # 残差抖动 ≤20ms 满分
RHYTHM_STAB_ZERO_MS = 150.0


def score_rhythm(rhythm: dict) -> dict:
    """节奏分。

    ★ 只用【准确度】和【稳定性】计分，**弹性（rubato）不参与扣分**。
      设计文档 4.3 的核心论点：偏差的均值是"不准"，偏差的规律结构是
      "艺术处理"。把二者混在一起计分，会把"唱得有感情"判成"节奏不好"。
    """
    if not rhythm or not rhythm.get("ok"):
        return {"score": None, "accuracy": None, "stability": None, "rubato": None,
                # 速度比与三分量无关，音符太少时仍可能有值 → 照传，别在这里丢掉
                "tempo_ratio": (rhythm or {}).get("tempo_ratio"),
                "reason": rhythm.get("reason", "节奏分解不可用") if rhythm else "无数据"}

    acc = rhythm.get("accuracy")            # |mean| ms
    stab = rhythm.get("stability")          # std(residual) ms
    rub = rhythm.get("rubato")              # std(trend) ms
    acc_rel = rhythm.get("accuracy_rel")    # ★ 无量纲：accuracy ÷ 乐句跨度

    def ramp(x, full, zero):
        if x is None:
            return None
        return float(min(1.0, max(0.0, (zero - x) / max(1e-9, zero - full))))

    # ★ 2026-10-02：准确度那一项改用**相对量**（与乐句长度无关）。
    #   为什么：`accuracy` 是绝对毫秒且与乐句总时长严格成正比
    #   （实测 acc÷总长 恒为 0.00151）⇒ 固定毫秒判据是"长曲子更难拿分"。
    #   ⚠️ 兜底：万一 `accuracy_rel` 拿不到（老数据/跨度太小），
    #      退回原来的绝对毫秒判据 —— 宁可口径不一致被看见，也不要没分。
    if acc_rel is not None:
        a_s = ramp(acc_rel, RHYTHM_ACC_REL_FULL, RHYTHM_ACC_REL_ZERO)
    else:
        a_s = ramp(acc, RHYTHM_ACC_FULL_MS, RHYTHM_ACC_ZERO_MS)
    s_s = ramp(stab, RHYTHM_STAB_FULL_MS, RHYTHM_STAB_ZERO_MS)
    parts = [v for v in (a_s, s_s) if v is not None]
    score = 100.0 * float(np.mean(parts)) if parts else None
    return {"score": score, "accuracy": acc, "stability": stab, "rubato": rub,
            # ★ 速度比**不参与算分**，只是原样往上传。
            #   它回答"用户有没有跟住拍子"，不是"用户节奏好不好"。
            #   （清唱下这个数会明显偏离 1，见 features.tempo_ratio 的说明）
            "tempo_ratio": rhythm.get("tempo_ratio"),
            # ★ 2026-10-02：`accuracy` 改成"偏离自己速度线的 RMS"之后，
            #   把配套的量也往上传 —— 不然"扣了多少速度"在报告里就看不见了。
            "raw_accuracy": rhythm.get("raw_accuracy"),
            "tempo_removed_ms": rhythm.get("tempo_removed_ms"),
            # ★ 相对量（无量纲）：`accuracy ÷ 乐句跨度`。
            #   `accuracy` 是**绝对毫秒**且与乐句长度严格成正比
            #   （实测每音 250/500/1000/2000ms → 15.9/31.7/63.4/126.8ms，
            #     acc÷总长 恒为 0.00151）⇒ 判据若用固定毫秒，
            #   就变成"长曲子更难拿分"，那不是演唱水平。
            #   ⚠️ 现在只**报出来**、还没拿它算分 —— 要先用真人素材标定
            #      （现有 9 段都不满足实验前提，标不出来）。
            "accuracy_rel": rhythm.get("accuracy_rel"),
            "phrase_span_ms": rhythm.get("phrase_span_ms"),
            "reason": ""}


# ══════════════════════════════════════════════════════════════
# 表现力分
# ══════════════════════════════════════════════════════════════
def score_expressiveness(feats: dict, weights: Optional[dict] = None) -> dict:
    """表现力分 = 100·Σ(权重 × 归一化指标)。

    ★ 被剔除（算不出）的指标不参与加权，权重按比例分给其余指标。
      这样"颤音算不出来"不会拉低总分 —— 契约要求显示"本次未评估"而不是 0 分。
    """
    w = dict(weights or DEFAULT_WEIGHTS)
    excluded = set(feats.get("excluded", []))

    norm = {}
    # 颤音有两个原始值，合成一个
    if "vibrato" not in excluded and feats.get("vibrato_depth") is not None:
        norm["vibrato"] = _norm(feats["vibrato_depth"], NORMALIZATION["vibrato"])
    else:
        norm["vibrato"] = None
        excluded.add("vibrato")

    for key in ("dynamic_range", "phrase_dynamics", "rubato",
                "onset_style", "brightness"):
        if key in excluded:
            norm[key] = None
            continue
        norm[key] = _norm(feats.get(key), NORMALIZATION[key])
        if norm[key] is None:
            excluded.add(key)

    usable = {k: v for k, v in norm.items() if v is not None}
    if not usable:
        return {"score": None, "normalized": norm, "weights": {},
                "reason": "所有指标都算不出来"}

    # 权重按可用项重新归一
    total_w = sum(w.get(k, 0.0) for k in usable)
    if total_w <= 0:
        return {"score": None, "normalized": norm, "weights": {},
                "reason": "可用指标权重全为 0"}
    eff_w = {k: w.get(k, 0.0) / total_w for k in usable}
    score = 100.0 * sum(usable[k] * eff_w[k] for k in usable)

    return {
        "score": float(score),
        "normalized": norm,
        "weights": eff_w,
        "excluded": sorted(excluded),
        "reason": "",
    }


# ══════════════════════════════════════════════════════════════
# 总分
# ══════════════════════════════════════════════════════════════
def score_total(pitch: dict, rhythm: dict, expr: dict) -> Optional[float]:
    """总分。

    ★ 三大分量缺任一 → 返回 None（不给总分）。
      为什么不在三大分量之间按比例分权重：
        实测用户唱一首不是模板的曲目时，对齐正确降级，音准/节奏都是 null，
        而"表现力"仍能算（它只看演唱者自己的力度/颤音，不需要参考）。
        此时若把 0.35+0.25 的权重都推给表现力，会输出一个【总分 54.4】——
        用户会以为"我唱了 54.4 分"，其实我们只测了三分之一。
        **总分的语义是"整体唱得怎么样"，缺了音准和节奏就不成立。**

      对比：六指标【内部】的权重再分配是合理的（见 score_expressiveness）——
        那是同一个构念内，颤音算不出时其余五项分担，不改变"表现力"的含义。
    """
    if pitch.get("score") is None or rhythm.get("score") is None:
        return None
    parts = {"pitch": pitch.get("score"), "rhythm": rhythm.get("score"),
             "expr": expr.get("score")}
    usable = {k: v for k, v in parts.items() if v is not None}
    if not usable:
        return None
    tw = sum(TOTAL_WEIGHTS[k] for k in usable)
    return float(sum(usable[k] * TOTAL_WEIGHTS[k] / tw for k in usable))


# ══════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════
def build_scores(aligned, rhythm: dict, feats: dict,
                 weights: Optional[dict] = None,
                 ktv_tolerance: Optional[dict] = None) -> dict:
    """一次算出契约里的全部 score 字段。"""
    p = score_pitch(aligned)
    r = score_rhythm(rhythm)
    e = score_expressiveness(feats, weights)
    t = score_total(p, r, e)
    ktv = score_ktv(aligned, **(ktv_tolerance or {}))
    return {
        "pitch": {"score": p.get("score"), "accuracy": p.get("accuracy"),
                  "stability": p.get("stability")},
        "rhythm": {"score": r.get("score"), "accuracy": r.get("accuracy"),
                   "stability": r.get("stability"), "rubato": r.get("rubato"),
                   # 速度比照传（不算分；上层要拿它告诉用户"你比参考慢了多少"）
                   "tempo_ratio": r.get("tempo_ratio"),
                   # ★ 2026-10-02 新增的配套字段，**必须在这里逐个列出** ——
                   #   这个 dict 是手工挑选 key 的，漏一个上层就永远看不到
                   #   （实测踩过：`accuracy_rel` 在直接调用时是 0.012，
                   #     走管线却是 None，因为沿途两处手工重建 dict 都没带它）。
                   "raw_accuracy": r.get("raw_accuracy"),
                   "tempo_removed_ms": r.get("tempo_removed_ms"),
                   "accuracy_rel": r.get("accuracy_rel"),
                   "phrase_span_ms": r.get("phrase_span_ms")},
        "expressiveness": {"score": e.get("score"), "weights": e.get("weights", {}),
                           "normalized": e.get("normalized", {})},
        "total": t,
        # ★ KTV 式并列参考分（2026-10-18）：不替换六指标，只多给一个
        #   对离群点不敏感的命中率口径；旧报告在读取时会补算。
        "ktv": ktv,
        "weight_version": WEIGHT_VERSION,
        "excluded": sorted(set(feats.get("excluded", [])) | set(e.get("excluded", []))),
        "detail": {"pitch": p, "rhythm": r, "expressiveness": e},
    }
