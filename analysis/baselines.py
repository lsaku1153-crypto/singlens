"""对比基线打分器 —— 用来证明六指标体系确实比"常规做法"更能反映表现力。

━━━ 为什么必须有这个 ━━━

  调研的原话：「**没有基线，六指标就是自说自话。**」

  只报自己系统的分数与真人评分的相关性是不够的 —— 评委立刻会问
  「那我用最简单的音准+节奏打分，相关性是不是也差不多？」
  必须有对照，才能说明「多出来的那四个指标」到底带来了什么。

━━━ 三组基线（按调研建议设计）━━━

  A  SmartMusic / DTW 式     音准命中率 + 节奏命中率，**无表现力**
  B  四维式（仿同名开源仓库） 音准 40% + 节奏 30% + 音质 20% + 完整度 10%
  C  本系统                  音准 + 节奏 + 六指标表现力

━━━ ⚠️ 公平性声明（很重要，写进论文）━━━

  基线**必须代表"常规做法的最佳水平"，不能做成稻草人**。
  所以本文件里的每一条规则都：

    1. 参数取该路线的常见值（命中容差 50 音分、节奏窗口 ±100ms 等），
       不刻意调差；
    2. 与 C 组**共用同一套底层测量**（同一个 PYIN、同一次切分、同一次 DTW 对齐），
       差异**只在评分函数**上 —— 这样对比才干净，
       否则会被质疑"你给基线喂了差数据"；
    3. 容量与 C 组对齐（都是 0-100 分），可以直接比相关性。

  这三点会在论文的"实验设置"里明确写出。
"""

from __future__ import annotations

import numpy as np

# ── 基线参数（取各路线的常见值，不刻意调差）──────────────────
BASELINE_A = {
    "pitch_tolerance_cents": 50.0,   # 半音的一半。K 歌类应用的常见容差
    "rhythm_tolerance_ms": 100.0,    # 常见节奏判定窗
    "name": "A · 音准+节奏命中率（SmartMusic/DTW 式）",
    "dimensions": 2,
    "desc": "每个音只有「对/错」两态；无表现力维度。这是教学软件与 K 歌应用的常规做法。",
}

BASELINE_B = {
    "pitch_weight": 0.40,
    "rhythm_weight": 0.30,
    "timbre_weight": 0.20,
    "completeness_weight": 0.10,
    "pitch_linear_cap_cents": 100.0,  # min(|cents|, 100) 线性扣分
    "rhythm_decay_ms": 120.0,         # exp(-|dev| / 120ms)
    "name": "B · 四维式（音准/节奏/音质/完整度）",
    "dimensions": 4,
    "desc": "仿同名开源仓库的四维加权。引入了一个「音质」维度，但只有 1 个、且定义笼统。",
}


# ══════════════════════════════════════════════════════════════
# 基线 A：纯命中率
# ══════════════════════════════════════════════════════════════
def score_baseline_a(aligned, params=None) -> dict:
    """SmartMusic / DTW 式：音准命中率 + 节奏命中率，算术平均。

    ★ 这是最"朴素"的做法，也是绝大多数现成系统的做法：
      把每个音判成对或错，数命中比例。

      它丢失的信息：**差多少、往哪个方向差。**
      「唱低了 30 音分」和「唱低了 300 音分」在它眼里都只是"错了"。
    """
    p = {**BASELINE_A, **(params or {})}
    cents = [m.pitch_cents_dev for m in aligned if getattr(m, "pitch_cents_dev", None) is not None]
    onsets = [m.onset_dev_ms for m in aligned if getattr(m, "onset_dev_ms", None) is not None]
    if len(cents) < 3:
        return {"score": None, "pitch_hit_rate": None, "rhythm_hit_rate": None,
                "reason": "有效音符太少"}

    pitch_hits = float(np.mean([abs(c) <= p["pitch_tolerance_cents"] for c in cents]))
    rhythm_hits = (float(np.mean([abs(o) <= p["rhythm_tolerance_ms"] for o in onsets]))
                   if onsets else None)
    parts = [x for x in (pitch_hits, rhythm_hits) if x is not None]
    return {
        "score": round(100.0 * float(np.mean(parts)), 1),
        "pitch_hit_rate": round(100.0 * pitch_hits, 1),
        "rhythm_hit_rate": round(100.0 * rhythm_hits, 1) if rhythm_hits is not None else None,
        "reason": "",
    }


# ══════════════════════════════════════════════════════════════
# 基线 B：四维加权
# ══════════════════════════════════════════════════════════════
def score_baseline_b(aligned, feats, n_expected_notes=None, params=None) -> dict:
    """四维式：音准 40% + 节奏 30% + 音质 20% + 完整度 10%。

    ★ 与 A 的区别：音准和节奏都改成【连续扣分】而不是二值，
      并且**引入了第一个"音质"维度** —— 但要诚实指出：

        · 它的"音质"只有 1 个笼统维度，本实现取谱质心的偏离度；
        · 它没有颤音、没有 rubato、没有起音方式、没有句内起伏；
        · 这与 C 组的六指标是**维度数量上的差别**，不只是算法精度的差别。
    """
    p = {**BASELINE_B, **(params or {})}
    cents = [m.pitch_cents_dev for m in aligned if getattr(m, "pitch_cents_dev", None) is not None]
    onsets = [m.onset_dev_ms for m in aligned if getattr(m, "onset_dev_ms", None) is not None]
    if len(cents) < 3:
        return {"score": None, "reason": "有效音符太少", "parts": {}}

    # 音准：线性扣分，超出 100 音分记 0
    pitch_pen = np.mean([min(abs(c), p["pitch_linear_cap_cents"])
                         / p["pitch_linear_cap_cents"] for c in cents])
    pitch = 100.0 * (1.0 - pitch_pen)

    # 节奏：指数衰减
    rhythm = (100.0 * float(np.mean([np.exp(-abs(o) / p["rhythm_decay_ms"]) for o in onsets]))
              if onsets else None)

    # 音质：谱质心偏离"正常演唱"的程度（越接近越"好"）
    br = feats.get("brightness")
    timbre = None
    if br is not None:
        # 以 1400 Hz 为参考中心，偏离越多扣越多（对数尺度）
        dev = abs(np.log2(max(br, 1e-6) / 1400.0))
        timbre = 100.0 * max(0.0, 1.0 - dev / 1.2)

    # 完整度：实际唱出的音符数 / 参考音符数
    completeness = None
    if n_expected_notes:
        completeness = 100.0 * min(1.0, len(aligned) / n_expected_notes)

    parts = {"pitch": pitch, "rhythm": rhythm, "timbre": timbre, "completeness": completeness}
    used = {k: v for k, v in parts.items() if v is not None}
    wsum = sum(p[f"{k}_weight"] if f"{k}_weight" in p else 0.0 for k in used)
    if wsum <= 0:
        return {"score": None, "reason": "没有可用维度", "parts": parts}
    score = sum(v * p.get(f"{k}_weight", 0.0) for k, v in used.items()) / wsum
    return {
        "score": round(float(score), 1),
        "parts": {k: (round(v, 1) if v is not None else None) for k, v in parts.items()},
        "reason": "",
    }


# ══════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════
def run_all_baselines(aligned, feats, scores, n_expected_notes=None) -> dict:
    """跑全部三组，返回可直接对比的结构。

    注意 C 组直接用 analysis/scoring.py 的结果（同一个分数引擎），
    保证三组是"同一批输入、同一套底层测量、不同评分函数"。
    """
    a = score_baseline_a(aligned)
    b = score_baseline_b(aligned, feats, n_expected_notes)
    c = {
        "score": scores.get("total"),
        "parts": {
            "pitch": scores["pitch"]["score"],
            "rhythm": scores["rhythm"]["score"],
            "expressiveness": scores["expressiveness"]["score"],
        },
    }
    return {
        "A": {**a, "name": BASELINE_A["name"], "dimensions": BASELINE_A["dimensions"],
              "desc": BASELINE_A["desc"]},
        "B": {**b, "name": BASELINE_B["name"], "dimensions": BASELINE_B["dimensions"],
              "desc": BASELINE_B["desc"]},
        "C": {"score": c["score"], "parts": c["parts"],
              "name": "C · 本系统（音准+节奏+六指标表现力）", "dimensions": 9,
              "desc": "六指标覆盖时值-力度-音色三个子域；含 rubato 识别（不扣分）。"},
    }


def compare_table(results: dict) -> list:
    """把三组结果排成一张表，便于打印或返回前端。"""
    rows = []
    for k in ("A", "B", "C"):
        r = results.get(k, {})
        rows.append({
            "id": k, "name": r.get("name", k), "dimensions": r.get("dimensions"),
            "score": r.get("score"), "parts": r.get("parts", {}),
            "desc": r.get("desc", ""),
        })
    return rows
