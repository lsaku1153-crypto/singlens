"""用户画像聚合（P0）—— 只描述练习表现，不做人格/声部判断。

设计约束：
- 只读已有 `practice_session`，不重新分析音频；
- 每项都带样本数 `n` 与置信度；样本不够就明确说“还在了解你”；
- 不使用跨歌分数比较；
- 不把 `brightness` 说成“音色鉴定”，不把一次表现说成“你这个人”。

用法：
    portrait = build_portrait(sessions, stated=profile)
"""
from __future__ import annotations

import math
from datetime import datetime

_CONF = {0: "low", 1: "low", 2: "low"}


def _median(vals):
    v = []
    for x in vals:
        if x is None or isinstance(x, bool):
            continue
        try:
            f = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            v.append(f)
    if not v:
        return None
    v.sort()
    m = len(v) // 2
    return round((v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2), 2)


def _conf(n: int) -> str:
    if n < 3:
        return "low"
    return "medium" if n < 8 else "high"


def _ms(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ts(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _song(s) -> str:
    meta = s.get("meta") or {}
    rs = meta.get("reference_song") or {}
    return str(s.get("reference_title") or meta.get("reference_title")
               or rs.get("song_title")
               or "").strip()


def _direction(cents):
    if cents is None:
        return "unknown", "这次没有可用的音准方向"
    if abs(cents) < 15:
        return "near", f"整体接近参考（中位 {cents:+.0f} 音分）"
    if cents < 0:
        return "flat", f"整体偏低约 {abs(cents):.0f} 音分"
    return "sharp", f"整体偏高约 {abs(cents):.0f} 音分"


def build_portrait(sessions, stated: dict | None = None) -> dict:
    ss = list(sessions or [])
    stated = stated or {}
    n = len(ss)
    titles = [x for x in (_song(s) for s in ss) if x]
    # 微练习标题不是“歌单”，不混进曲目统计；它们仍然算练习次数。
    song_titles = [x for x in titles if not x.startswith("微练习")]
    songs = sorted(set(song_titles))
    stamps = [x for x in (_ts(s.get("created_at")) for s in ss) if x]
    days = 0
    if len(stamps) >= 2:
        days = max(1, int(round((max(stamps) - min(stamps)) / 86400)))

    pitch = _median([(s.get("pitch") or {}).get("accuracy") for s in ss])
    stab = _median([(s.get("pitch") or {}).get("stability") for s in ss])
    tempo = _median([(s.get("rhythm") or {}).get("tempo_ratio") for s in ss])
    bright = _median([(s.get("expressiveness") or {}).get("brightness") for s in ss])
    dyn = _median([(s.get("expressiveness") or {}).get("dynamic_range") for s in ss])
    onset = _median([(s.get("expressiveness") or {}).get("onset_style") for s in ss])
    vr = _median([(s.get("expressiveness") or {}).get("vibrato_rate") for s in ss])
    vd = _median([(s.get("expressiveness") or {}).get("vibrato_depth") for s in ss])

    p_n = sum(1 for s in ss if (s.get("pitch") or {}).get("accuracy") is not None)
    b_n = sum(1 for s in ss if (s.get("expressiveness") or {}).get("brightness") is not None)
    t_n = sum(1 for s in ss if (s.get("rhythm") or {}).get("tempo_ratio") is not None)

    # 行为（不评价性格）：最常练的曲目 / 最常出现的练习类型 / 平均时长
    song_counts: dict[str, int] = {}
    for title in song_titles:
        song_counts[title] = song_counts.get(title, 0) + 1
    top_song = max(song_counts, key=song_counts.get) if song_counts else ""
    kinds: dict[str, int] = {}
    for s in ss:
        plan = s.get("practice_plan") or {}
        k = str(plan.get("kind") or "").strip()
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    focus_label = {
        "pitch_segment": "音准片段",
        "tempo": "速度/节奏",
        "onset": "起音",
        "dynamic": "力度",
        "beat": "节拍",
        "unassessed": "补录",
    }
    focus = max(kinds, key=kinds.get) if kinds else ""
    durations = [_ms((s.get("meta") or {}).get("duration_ms") or s.get("duration_ms"))
                 for s in ss]
    durations = [x for x in durations if x and x > 0]
    avg_min = round((sum(durations) / len(durations)) / 60000, 1) if durations else None

    def metric(value, label, n_used, unit=""):
        return {
            "value": value,
            "label": label,
            "n": n_used,
            "unit": unit,
            "confidence": _conf(n_used),
        }

    pitch_dir, pitch_label = _direction(pitch)
    tempo_label = ("这次没有可用的速度数据" if tempo is None else
                   "接近原速" if abs(tempo - 1) < 0.03 else
                   f"比原速慢约 {abs(tempo - 1) * 100:.0f}%" if tempo > 1 else
                   f"比原速快约 {abs(tempo - 1) * 100:.0f}%")
    stable_label = ("这次没有可用的稳定性数据" if stab is None else
                    "音高比较稳" if stab <= 60 else
                    "音高有一些晃动" if stab <= 100 else "音高晃动比较明显")
    bright_label = ("这次没有可用的音色数据" if bright is None else
                    "偏亮" if bright >= 1400 else "偏暖")
    dyn_label = ("这次没有可用的力度数据" if dyn is None else
                 "力度起伏丰富" if dyn >= 12 else "力度比较平")
    onset_label = ("这次没有可用的起音数据" if onset is None else
                   "起音偏柔" if onset > 70 else "起音比较干脆")

    visible = n >= 3
    observed = {
        "visible": visible,
        "pitch": metric(pitch, pitch_label, p_n, "音分"),
        "pitch_direction": pitch_dir,
        "stability": metric(stab, stable_label, p_n, "音分"),
        "tempo": metric(tempo, tempo_label, t_n, "倍"),
        "brightness": metric(bright, bright_label, b_n, "Hz"),
        "dynamic": metric(dyn, dyn_label,
                          sum(1 for s in ss if (s.get("expressiveness") or {}).get("dynamic_range") is not None),
                          "dB"),
        "onset": metric(onset, onset_label,
                        sum(1 for s in ss if (s.get("expressiveness") or {}).get("onset_style") is not None),
                        "ms"),
        "vibrato": (metric(vr, f"约 {vr:.1f} 次/秒" if vr is not None else "这次没有可用的颤音数据",
                           sum(1 for s in ss if (s.get("expressiveness") or {}).get("vibrato_rate") is not None),
                           "Hz") if vr is not None else None),
        "habit": {
            "top_song": top_song,
            "top_song_n": song_counts.get(top_song, 0) if top_song else 0,
            "common_focus": focus,
            "common_focus_label": focus_label.get(focus, ""),
            "avg_minutes": avg_min,
        },
    }

    conf_level = ("low" if n < 3 else
                  "medium" if n < 8 or len(songs) < 2 else "high")
    why = (f"基于 {n} 次练习、{len(songs)} 首歌"
           if n else "还没有练习记录")
    if n and len(songs) < 2:
        why += "；目前主要来自同一首歌"
    if n < 3:
        why += "；样本太少，先不定结论"

    # ── P1：同歌趋势（只比同一首歌/同一评分口径，不跨曲）──
    groups: dict[tuple[str, str], list[dict]] = {}
    for s in ss:
        title = _song(s)
        if not title or title.startswith("微练习"):
            continue
        meta = s.get("meta") or {}
        if meta.get("self_reference"):
            continue
        total = s.get("total")
        if total is None:
            continue
        ver = str(s.get("weight_version") or meta.get("weight_version") or "")
        groups.setdefault((title, ver), []).append(s)
    trends = []
    for (title, ver), rows in groups.items():
        rows.sort(key=lambda x: _ts(x.get("created_at")) or 0)
        if len(rows) < 3:
            continue
        recent = rows[-3:]
        a = _median([x.get("total") for x in recent[:2]])
        b = _median([x.get("total") for x in recent[-2:]])
        if a is None or b is None:
            continue
        delta = round(float(b) - float(a), 1)
        if abs(delta) < 0.8:
            label = "最近 3 次基本持平"
        else:
            label = f"最近 3 次总分 {delta:+.1f}"
        trends.append({
            "song": title,
            "n": len(rows),
            "recent_n": len(recent),
            "from": round(float(a), 1),
            "to": round(float(b), 1),
            "delta": delta,
            "label": label,
            "weight_version": ver,
        })
    trends.sort(key=lambda x: (-x["n"], -abs(x["delta"])))
    trends = trends[:3]

    # ── P1：V 的一段话（规则生成，不调用 LLM；数字只来自上面）──
    goal = str(stated.get("goal") or "").strip()
    pref = [str(x) for x in (stated.get("preferred_style") or []) if str(x).strip()]
    comfort = stated.get("comfort") if isinstance(stated.get("comfort"), dict) else None
    if n == 0:
        narrative = ("我还没听你唱过。先随便唱一遍，我才能慢慢知道"
                     "你的声音往哪走 —— 不用一上来就唱好。")
        if goal:
            narrative = f"你还没唱过，但你说想「{goal}」。先唱一遍，我才能知道从哪儿帮你。"
    elif n < 3:
        narrative = (f"我才听了你 {n} 次，样本还少，先不急着给你下结论。"
                     "再唱一两次，我会把目标、习惯和声音倾向慢慢拼起来。")
    else:
        bits = [f"你已经练了 {n} 次、{len(songs)} 首歌。"]
        if goal:
            bits.append(f"你说想「{goal}」，我会先按这个方向给你建议。")
        elif pref:
            bits.append(f"你偏好「{'、'.join(pref[:2])}」，我先顺着这个方向。")
        else:
            bits.append("你还没填目标，所以我会先多问你一句想要什么。")
        obs_bits = []
        if pitch is not None and abs(pitch) >= 15:
            obs_bits.append(("偏低了" if pitch < 0 else "偏高了")
                            + f"约 {abs(pitch):.0f} 音分")
        elif pitch is not None:
            obs_bits.append("音准方向比较接近参考")
        if bright is not None:
            obs_bits.append("音色偏亮" if bright >= 1400 else "音色偏暖")
        if dyn is not None and dyn >= 12:
            obs_bits.append("力度起伏比较丰富")
        if obs_bits:
            bits.append("从这几次看，" + "、".join(obs_bits[:3]) + "。")
        if comfort and comfort.get("summary"):
            bits.append(f"你自测的{comfort['summary']}，可以先按这个范围选歌。")
        if trends:
            t = trends[0]
            if abs(t["delta"]) < 0.8:
                bits.append(f"同一首《{t['song']}》最近 3 次基本持平。")
            else:
                bits.append(f"同一首《{t['song']}》最近 3 次的总分变化是 "
                            f"{t['delta']:+.1f}，只当参考，不是承诺。")
        bits.append("这些只是练习里的倾向，不是给你贴标签。")
        narrative = "".join(bits)

    return {
        "sample": {"sessions": n, "songs": len(songs), "days": days,
                   "song_titles": songs[:5]},
        "stated": {
            "goal": str(stated.get("goal") or ""),
            "preferred_style": list(stated.get("preferred_style") or []),
            "dislikes": list(stated.get("dislikes") or []),
            "experience": str(stated.get("experience") or ""),
            "favorite_songs": list(stated.get("favorite_songs") or []),
            "notes": list(stated.get("notes") or []),
            "updated_at": stated.get("updated_at"),
        },
        "observed": observed,
        "trends": trends,
        "comfort": comfort,
        "narrative": narrative,
        "confidence": {"level": conf_level, "why": why},
        "disclaimer": "画像只描述练习表现，不是声部、音色身份或人格判断。",
    }


def portrait_brief(portrait: dict) -> dict:
    """压成给 V 的一小段背景；不把整份画像塞给模型。"""
    if not portrait:
        return {}
    stated = portrait.get("stated") or {}
    obs = portrait.get("observed") or {}
    out = {
        "goal": stated.get("goal") or "",
        "preferred_style": stated.get("preferred_style") or [],
        "dislikes": stated.get("dislikes") or [],
        "notes": (stated.get("notes") or [])[:3],
        "confidence": (portrait.get("confidence") or {}).get("level"),
    }
    comfort = portrait.get("comfort") or {}
    if comfort.get("summary") and comfort.get("confidence") in ("medium", "high"):
        out["comfort"] = comfort["summary"]
    tend = []
    for key in ("pitch", "tempo", "brightness", "dynamic"):
        m = obs.get(key) or {}
        if m.get("label") and m.get("confidence") != "low":
            tend.append(m["label"])
    out["tendencies"] = tend[:3]
    return out
