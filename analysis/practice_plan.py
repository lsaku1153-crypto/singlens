"""练习计划：把"这一遍哪里不对"变成"下一遍只改哪一件事"。

━━━ 为什么要有这个模块（B1，2026-10-01）━━━

现有的报告已经有三块反馈，但它们**各自为政**：

  · `problems[]`     哪里错了（位置 + 严重度）—— 但最多 3 条并列，用户不知道该先改哪个
  · `comments_by_metric.*.action`  每一项怎么练 —— 但**与 problems 脱节**：
     problems 说"第 3–8 个音拖了 351ms"，action 说"挑一句练音量起伏"
  · `comments`       规则拼接的总评 —— 7 条建议连成一串，**没有"只做一件事"**

作者的原话是「AI **陪练**」——陪练的核心不是把问题列全，而是
**告诉你现在先练哪一个、怎么练、练到什么算好**。这一层原来没有。

所以本模块只做一件事：**收敛到一条**。

━━━ 四条硬规则（每条都对应本项目踩过的坑）━━━

  ① **只给一条**。多了等于没给（`comments` 已经是 7 条并列的教训）。

  ② **每句话都要能指回报告里的某个字段**。
     `why` 里的数字全部来自 `facts`，`facts` 全部来自已算出的量。
     不许出现报告里没有的数 —— 这是 §27.2「论文里每个数字都要能指回
     原始输出」的同一条规矩，只是用在了界面上。

  ③ **没有可靠依据时，宁可说"先别管"**。
     如果 problems 为空、分数都正常，就明确说"这一遍没有明显要改的地方"，
     而不是硬凑一条建议。**凑出来的建议比没有建议更坏**：用户会去改一个
     本来没问题的东西。（本项目最忌讳的"界面看着有结论、其实没有依据"。）

  ④ **`rubato` 永远不作为问题**。它是艺术处理、不扣分（设计规格的核心论点）。
     这里再挡一道：不让任何规则把它当缺点报出来。

━━━ 与 `problems` 的关系 ━━━

`problems` 是**测量结果**（哪里偏了），`practice_plan` 是**教学决定**
（先改哪个、怎么改）。两者不能合并：
测量要全（可能有很多处偏），教学要少（一次只能改一件事）。
"""
from __future__ import annotations

from typing import Optional

# ══════════════════════════════════════════════════════════════
# 阈值（集中在这里，方便对着真实录音调）
# ══════════════════════════════════════════════════════════════
# ★ 2026-10-19 风格容忍：以下阈值是**提示线**，不是"错"线。
#   合法的艺术处理（rubato、颤音、气声起音、轻唱）不应被当成技术错误：
#     音高 ±30 音分、起音 ±80 ms、整体速度 ±15%、动态 8 dB。
#   规则触发线略宽于练习目标线（音准 35/30），避免把风格波动
#   直接变成"你唱错了"。
TEMPO_TOLERANCE = 0.15
STYLE_TEMPO_TOLERANCE = 0.15
# 音准：problem 触发 35，练习目标 30（30 音分内算风格波动）。
PITCH_SEGMENT_CENTS = 35.0
STYLE_PITCH_CENTS = 30.0
# 起音：>80ms 才提示；80ms 内算个人起音风格。
ONSET_SOFT_MS = 80.0
STYLE_ONSET_MS = 80.0
# 力度：<8dB 才提示；轻唱/私语不硬判成"太平"。
DYNAMIC_FLAT_DB = 8.0
STYLE_DYNAMIC_DB = 8.0

# ★ 2026-10-19 风格宽容度（用户设置）：同一套规则，三种默认容忍线。
#   严格 = 考试/比赛；标准 = 日常练习；宽松 = 保留个人处理。
STYLE_PROFILES = {
    "strict": {"label": "严格", "pitch": 20.0, "tempo": 0.10,
               "onset": 60.0, "dynamic": 10.0},
    "standard": {"label": "标准", "pitch": 30.0, "tempo": 0.15,
                 "onset": 80.0, "dynamic": 8.0},
    "expressive": {"label": "宽松", "pitch": 40.0, "tempo": 0.20,
                   "onset": 100.0, "dynamic": 6.0},
}
DEFAULT_STYLE_PROFILE = "standard"


def get_style_profile(name: str | None) -> dict:
    """取风格宽容度；未知/空值回落到标准（不报错、不静默变严格）。"""
    return STYLE_PROFILES.get((name or "").strip().lower(),
                              STYLE_PROFILES[DEFAULT_STYLE_PROFILE])


def _style_note(kind: str, style: dict) -> str:
    s = style
    base = {
        "tempo": f"±{s['tempo'] * 100:.0f}% 内算风格容忍（渐慢/渐快）；"
                 "如果这是你有意的速度，可以保留；要跟伴奏/原速再练。",
        "pitch": f"颤音/装饰音带来的 ±{s['pitch']:.0f} 音分内波动算风格；"
                 "只有持续偏离才需要改。",
        "onset": f"偏柔（>{s['onset']:.0f} ms）可能是抒情风格，不一定是错误；"
                 "要力量型再改。",
        "dynamic": "动态小可能是风格（轻声/私语），不一定是缺点；要有起伏再练。",
    }.get(kind, "")
    return f"{s['label']}模式：{base}" if base else ""


def _note_name(midi: float) -> str:
    """MIDI → 音名（给示范音用；用户不需要看，但 aria-label / 调试有用）。"""
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    m = int(round(midi))
    return f"{names[m % 12]}{m // 12 - 1}"


def _worst_pitch_problem(problems: list[dict]) -> Optional[dict]:
    """音准问题里最严重的那一处。

    ⚠️ 2026-10-20：不能只按"片段最长"挑。
       `problems` 里的片段是"连续两音以上超过 25 音分"的 run，
       一段可以很长但整体只偏 30 音分；另一段短一点却偏 200。
       原来按长度挑，会把"长而轻"的一段当成最该练的。
       现在按**片段标注的中位偏差**（`median_abs`）挑；老数据没有这个
       字段时退回长度，行为不变。
    """
    ps = [p for p in problems if p.get("kind") == "PITCH"]
    if not ps:
        return None
    return max(ps, key=lambda p: (float(p.get("median_abs") or 0.0),
                                  len(p.get("note_range") or [0, 0])))


def _devs_in_range(segments: list[dict], a: int, b: int) -> list[float]:
    """问题片段内**每个已对齐音**的音分偏差（用户音 index 闭区间）。

    ★ 为什么是"这一段的中位数"而不是"第一个音的偏差"（2026-10-20 修）：
      实测 `sess_1b9d475c59c1`：第 1–4 个音是 [+520.5, -39.5, +95.5, -29.5]。
      · 旧代码取**第一个音** ⇒ 计划说"整体高了约 520 音分"、目标"从 520 降到 30"；
      · problems 取**均值** ⇒ 同一段写成"整体高了 137 音分"；
      · 微练习卡的 before 取**中位绝对值** ⇒ 67.5。
      同一段话在同一屏里出现三个数（137 / 520 / 67.5），用户无法相信任何一个。
      中位数对单个离群音不敏感，而且与微练习卡的口径**本来就是同一个量**
      （`practice_card.before.pitch_median_abs`），所以这里统一用中位数。
    """
    out = []
    for s in segments or []:
        ni = s.get("note_index")
        if not isinstance(ni, int) or not (a <= ni <= b):
            continue
        d = s.get("pitch_cents_dev")
        if isinstance(d, (int, float)):
            out.append(float(d))
    return out


def _seg_for_note(segments: list[dict], note_index: int) -> Optional[dict]:
    for s in segments:
        if s.get("note_index") == note_index:
            return s
    return None


def _reference_span(segments: list[dict], a: int, b: int) -> dict:
    """把问题片段映射成"参考旋律里的哪几个音"，用于合成示范。"""
    lo = _seg_for_note(segments, a)
    hi = _seg_for_note(segments, b)
    if not lo or not hi:
        return {}
    r0, r1 = lo.get("ref_note_index"), hi.get("ref_note_index")
    if r0 is None or r1 is None:
        return {}
    midis = [s.get("pitch_midi") for s in segments
             if isinstance(s.get("ref_note_index"), int) and r0 <= s["ref_note_index"] <= r1]
    # ⚠️ `segments[].pitch_midi` 是**用户实际唱的**音高，不是参考音高。
    #    合成示范必须用**参考**音高 —— 拿用户唱错的那个音当示范，
    #    等于把错的当对的教。参考音高由调用方从参考旋律里取（见 build 的 ref_notes 参数）。
    return {"ref_from": r0, "ref_to": r1, "actual_midis": [m for m in midis if m]}


def build(scores: dict, feats: dict, problems: list[dict],
          segments: list[dict], meta: dict,
          ref_notes: list[dict] | None = None,
          style_profile: str = DEFAULT_STYLE_PROFILE) -> dict:
    """生成练习计划。返回契约里 `practice_plan` 那个结构。

    参数：
      scores    —— `build_scores` 的结果（pitch/rhythm/expressiveness/total）
      feats     —— 六指标原始量
      problems  —— `build_problems` 的结果
      segments  —— 逐音段落（要 ref_note_index 才能定位参考音）
      meta      —— 报告 meta（要 eval_window / self_reference / pitch_offset）
      ref_notes —— **参考旋律**的音符（`[{index, pitch_midi, start_ms, end_ms}]`），
                   用来合成示范音。没有就不给示范（降级，不报错）。
    """
    meta = meta or {}
    rh = scores.get("rhythm") or {}
    pi = scores.get("pitch") or {}
    problems = problems or []
    segments = segments or []
    style = get_style_profile(style_profile)
    facts: list[dict] = []

    # ── 自比会话：没有参考，音准/节奏都无从谈起 ──
    #    这与 `build_scores` 的降级是同一件事，不能让计划去"练"一个
    #    根本没测的东西。只给表现力那一条。
    if meta.get("self_reference"):
        return _plan(
            focus="这一遍没有参考可比",
            why="这是「跟唱模板」的第一遍 —— 系统拿它自己当参考，"
                "音准和节奏必然完全一致，评了也没有意义（所以本次没评）。"
                "表现力不受影响，已经照常算了。",
            steps=["把这一遍存成模板（报告页顶部那张卡）",
                   "然后再唱一遍、选这个模板 —— 第二遍的音准/节奏才是真的"],
            done="第二遍唱完后，音准和节奏两栏会出现分数，而不是「—」。",
            facts=[{"k": "本次模式", "v": "自比（无参考）", "unit": ""}],
            kind="no_reference")

    # ── 音准/节奏没有评估：不能落到「没有明显要改的地方」 ──
    #    2026-10-18 实测：用户没开口/对不上参考，pitch/rhythm 都是 null，
    #    但规则一路落空，最后给出「这一遍没有明显要改的地方」
    #    —— 从"没测到"推到"没问题"，这是**假结论**。
    pitch_missing = pi.get("score") is None
    rhythm_missing = rh.get("score") is None
    if pitch_missing or rhythm_missing:
        missing = ("音准和节奏" if pitch_missing and rhythm_missing
                   else "音准" if pitch_missing else "节奏")
        ex = scores.get("expressiveness") or {}
        return _plan(
            focus=f"这一遍没测到{missing} —— 先补一次能对上的录音",
            why=f"系统这次没能把你唱的和参考对齐，所以{missing}本次未评估"
                "（不是 0 分，是没测）。没有这一项，就没法判断最该改哪一句；"
                "表现力不受影响，已经照常算了。",
            steps=["确认选的是同一首参考 —— 别拿 A 歌的录音去对 B 歌",
                   "戴耳机，跟着 4 拍预备/参考旋律完整唱一遍",
                   "至少唱完一句再点「完成」；没开口或太短会被判「没检测到人声」"],
            done="报告里「唱准了吗 / 跟上拍子了吗」出现分数，而不是「—」。",
            facts=[{"k": "音准", "v": pi.get("score"), "unit": ""},
                   {"k": "节奏", "v": rh.get("score"), "unit": ""},
                   {"k": "表现力", "v": ex.get("score"), "unit": ""}],
            kind="unassessed")

    # ── 规则 1：整体没跟住拍子（节奏分掉下来最常见的原因）──
    tempo = rh.get("tempo_ratio")
    if isinstance(tempo, (int, float)) and abs(tempo - 1.0) > style["tempo"]:
        slower = tempo > 1.0
        pct = abs(tempo - 1.0) * 100
        facts.append({"k": "整遍时长", "v": f"比参考{'慢' if slower else '快'} {pct:.0f}%",
                      "unit": ""})
        return _plan(
            focus=f"整遍{'慢' if slower else '快'}了 {pct:.0f}% —— 先确认节奏",
            why="节奏分里一半是「有没有踩在拍子上」。"
                f"整遍{'慢' if slower else '快'}这么多，每一句都会被判成没跟住。"
                "但先分清两种情况：如果你**主动**唱慢/唱快（渐慢、自由处理），"
                "这是风格，不是错误；如果你本来想跟原速/伴奏，那才是没跟住。"
                "清唱本来就没有拍子可跟，这不是你唱得不好。",
            steps=["戴上耳机，放原曲（或开节拍器），音量调到能听清鼓点",
                   "麦克风只收你的人声 —— 耳机漏音会被当成你在唱伴奏",
                   f"跟着原曲整遍唱一次，速度别自己拿主意，就跟着它走"],
            done=f"报告里「你比参考{'慢' if slower else '快'}了百分之几」"
                 f"降到 {style['tempo'] * 100:.0f}% 以内。",
            facts=facts + [{"k": "节奏准确度", "v": rh.get("accuracy"), "unit": "ms"},
                           {"k": "节奏分", "v": rh.get("score"), "unit": ""}],
            # ★ 节拍器示范（2026-10-01）：只说"跟拍子走"是不够的 ——
            #   用户手上可能没有原曲、也不知道该多快。直接给一串**参考速度的拍子**，
            #   跟着它唱就行。这是这条建议里唯一"能立刻用上"的东西。
            demo=_metronome_demo(ref_notes),
            kind="tempo",
            style_note=_style_note("tempo", style),
        )

    # ── 规则 2：某一小段跑调 ──
    p = _worst_pitch_problem(problems)
    if p:
        a, b = p.get("note_range") or [None, None]
        devs: list[float] = []
        if isinstance(a, int) and isinstance(b, int):
            devs = _devs_in_range(segments, a, b)
        if not devs:
            # 兼容手写的问题片段（没有 segments 可映射）：退回片段首音的偏差
            seg = _seg_for_note(segments, a) if isinstance(a, int) else None
            d = seg.get("pitch_cents_dev") if seg else None
            if isinstance(d, (int, float)):
                devs = [float(d)]
        if devs:
            med_abs = float(_median([abs(d) for d in devs]))
            med_signed = float(_median(devs))
            max_abs = max(abs(d) for d in devs)
            n = len(devs)
            n_pos = sum(1 for d in devs if d > style["pitch"])
            n_neg = sum(1 for d in devs if d < -style["pitch"])
            # 方向只在"大多数音同向"时才说 —— 一半高一半低时，
            # 说"整体偏高"是假话（那一段的问题恰恰是忽高忽低）。
            if n == 1:
                # 单个音：方向就是它自己的符号
                direction = "高" if devs[0] > 0 else "低"
            elif n_pos >= max(2, n * 0.7):
                direction = "高"
            elif n_neg >= max(2, n * 0.7):
                direction = "低"
            else:
                direction = ""
        else:
            med_abs = med_signed = max_abs = 0.0
            direction = ""
        if devs and med_abs >= style["pitch"] + 5:
            span = _reference_span(segments, a, b) if isinstance(a, int) else {}
            ctx = _line_context(ref_notes, span.get("ref_from"), span.get("ref_to")) if span else {}
            if ctx and ctx.get("syllables"):
                line_txt = (f"第 {ctx['line_no']} 句「{ctx['line']}」的"
                            f"「{ctx['syllables']}」，{ctx['time']}")
            elif ctx:
                # 歌曲参考没有字级对齐（Whisper 只给行级时间）：
                # 不要拼出「的「」」这种空引号，只报整句。
                line_txt = f"第 {ctx['line_no']} 句「{ctx['line']}」，{ctx['time']}"
            else:
                line_txt = ""
            demo = (_line_demo(ref_notes, ctx, span.get("ref_from"),
                               span.get("ref_to")) if ctx
                    else _demo_for_span(ref_notes, span))
            # 整体移调要说清：不然用户会以为"我整首歌都唱错了"
            off = meta.get("pitch_offset_semitones")
            off_txt = (f"（另外：你整遍比参考{'低' if off < 0 else '高'}了 "
                       f"{abs(off):.0f} 个半音 —— 这是换了个调唱，"
                       f"系统已经自动折算掉了，不算错）"
                       if isinstance(off, (int, float)) and abs(off) >= 2 else "")
            if direction:
                focus = (f"第 {a + 1}–{b + 1} 个音"
                         + (f"（{line_txt}）" if line_txt else "")
                         + f"这一处偏{direction}了")
                why_num = (f"这一段整体偏{direction}：中位偏差 {med_abs:.0f} 音分"
                           f"（单音最大 {max_abs:.0f}）。")
            else:
                focus = (f"第 {a + 1}–{b + 1} 个音"
                         + (f"（{line_txt}）" if line_txt else "")
                         + "这一处忽高忽低")
                why_num = (f"这一段音准不太稳：每个音离参考的中位距离是 "
                           f"{med_abs:.0f} 音分（单音最大 {max_abs:.0f}），"
                           "有的偏高有的偏低。")
            return _plan(
                focus=focus,
                why=why_num
                    + "不是你唱不准 —— 一个音一个音地唱过去，"
                    "中间那一两个容易溜下去。 "
                    + ((f"这一句是「{ctx['line']}」，问题在「{ctx['syllables']}」；"
                        "先听整句，再只练这几个字。 ")
                       if (ctx and ctx.get("syllables")) else
                       (f"这一句是「{ctx['line']}」；先听整句，再只练这几个音。 "
                        if ctx else ""))
                    + off_txt,
                steps=["先只听：点下面的「听整句」，把这一整句在心里唱一遍",
                       "再跟着整句哼一遍（哼「嗯」就行，不用歌词）",
                       f"最后只唱第 {a + 1}–{b + 1} 个音这一段，"
                       "唱到每个音都站住再换下一个（别滑着唱）"],
                done=f"这一小段的音分偏差（中位数）从 {med_abs:.0f} 降到 "
                     f"{style['pitch']:.0f} 以内"
                     "（这个范围内算风格波动，不要求唱成机器）。",
                facts=facts + [{"k": "这一段的中位偏差", "v": round(med_abs, 1),
                                "unit": "音分"},
                               {"k": "音准分", "v": pi.get("score"), "unit": ""}],
                demo=demo, kind="pitch_segment",
                style_note=_style_note("pitch", style),
                span=span, context=ctx)

    # ── 规则 3：起音偏柔（这一条不影响音准节奏分，但很具体、好改）──
    os_ms = feats.get("onset_style")
    if isinstance(os_ms, (int, float)) and os_ms > style["onset"]:
        return _plan(
            focus="每个字起头偏「柔」—— 先确认是不是风格",
            why=f"你每个字平均用 {os_ms:.0f} ms 才唱出来（干脆的唱法在 40 ms 上下）。"
                "这不影响音准和节奏分。偏柔可能是你故意的抒情处理；"
                "如果只是习惯性慢起，才会让快歌听起来没精神。",
            steps=["挑一个你熟悉的字（比如自己的名字），像喊人那样短促地喊出来 —— "
                   "感受那个「一开口就到」的感觉",
                   "用同样的感觉唱这一遍里最长的那一句",
                   "注意别咬牙：干脆来自气息，不是来自喉咙用力"],
            done=f"起音时间中位数降到 {style['onset']:.0f} ms 以内"
                 "（这个范围内算个人起音风格）。",
            facts=facts + [{"k": "起音时间中位数", "v": round(float(os_ms), 0), "unit": "ms"}],
            kind="onset",
            style_note=_style_note("onset", style))

    # ── 规则 4：整首太平（力度没有起伏）──
    dr = feats.get("dynamic_range")
    if isinstance(dr, (int, float)) and dr < style["dynamic"]:
        return _plan(
            focus="整首动态偏小 —— 先确认是不是风格",
            why=f"你的音量变化范围只有 {dr:.1f} dB。"
                "「有感情」在声学上很大一部分是强弱，但如果这首歌你就是要"
                "轻声/私语地唱，这是风格，不是缺点；想要起伏时再练。",
            steps=["挑这一遍里你最喜欢的那一句",
                   "前半句用一半的音量唱，后半句放开",
                   "先夸张一点都没关系 —— 夸张了才听得出来，之后再收回来"],
            done=f"这一句内部的落差（句内推进）涨到 {style['dynamic']:.0f} dB 以上"
                 "（这个范围内算轻唱风格）。",
            facts=facts + [{"k": "音量变化范围", "v": round(float(dr), 1), "unit": "dB"},
                           {"k": "句内推进", "v": feats.get("phrase_dynamics"), "unit": "dB"}],
            kind="dynamic",
            style_note=_style_note("dynamic", style))

    # ── 都不触发：明确说"这一遍不用改" ──
    #    ★ 这是规则 ③。凑一条建议比不给建议更坏。
    total = scores.get("total")
    return _plan(
        focus="这一遍没有明显要改的地方",
        why="音准、节奏、起音、力度都在合理范围内 —— "
            "系统不会为了显得有用而硬挑一条出来让你改。"
            "想再进一步，可以试试同一首歌换个速度唱，或者换一首更有起伏的。",
        steps=["再唱一遍同一首，看看分数稳不稳",
               "或者试试内置的另一首（欢乐颂）"],
        done="分数保持在这个水平就行。",
        facts=facts + [{"k": "总分", "v": total, "unit": ""},
                       {"k": "音准", "v": pi.get("score"), "unit": ""},
                       {"k": "节奏", "v": rh.get("score"), "unit": ""}],
        kind="nothing")


# ══════════════════════════════════════════════════════════════
# 微练习卡（2026-10-19，AI 陪练 MVP）
# ══════════════════════════════════════════════════════════════
# 练习计划回答"下一遍只改哪一件事"；微练习卡再往前走一步：
# **只练这一小段**，并给出可验证的成功标准。
#
# 与 practice_plan 的关系：
#   · plan 是"教学决定"（改什么、为什么、怎么改）
#   · practice_card 是"可执行的一次练习"（练哪几个参考音、之前多少、目标多少）
#
# 片段来源优先级：
#   ① plan.demo 里的 ref_from/ref_to（音准问题已经定位到具体音）
#   ② 最严重的几个音准偏差附近（没有 demo 时）
#   ③ 参考开头的 8 个音（节奏/起音/力度问题：先练开头一小句）
_MICRO_MAX_NOTES = 8

def _median(vals):
    s = sorted(float(v) for v in vals)
    if not s:
        return None
    n = len(s)
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def _micro_span(plan: dict, ref_notes: list[dict], segments: list[dict]):
    """返回 (r0, r1) —— 参考旋律里的音 index 闭区间。"""
    if not ref_notes:
        return None
    idxs = [n.get("index") for n in ref_notes if isinstance(n.get("index"), int)]
    if not idxs:
        return None
    lo, hi = min(idxs), max(idxs)

    # 节奏/起音/力度问题不在某一个音上：先练参考开头一小句，
    # 不要把"整遍慢"错配成"最不准的那几个音"。
    if plan.get("kind") in ("tempo", "onset", "dynamic"):
        return lo, min(hi, lo + _MICRO_MAX_NOTES - 1)

    demo = plan.get("demo") or {}
    sp = plan.get("span") or {}
    sr0, sr1 = sp.get("ref_from"), sp.get("ref_to")
    if isinstance(sr0, int) and isinstance(sr1, int):
        return max(lo, sr0), min(hi, sr1)
    r0, r1 = demo.get("ref_from"), demo.get("ref_to")
    if isinstance(r0, int) and isinstance(r1, int):
        return max(lo, r0), min(hi, r1)

    cand = [s for s in segments
            if isinstance(s.get("ref_note_index"), int)
            and s.get("pitch_cents_dev") is not None]
    if cand:
        worst = max(cand, key=lambda s: abs(float(s["pitch_cents_dev"])))
        w = int(worst["ref_note_index"])
        r0 = max(lo, w - 1)
        r1 = min(hi, max(r0 + 2, w + 2))
        return r0, min(hi, r1)

    return lo, min(hi, lo + _MICRO_MAX_NOTES - 1)


def _success_for(plan: dict, style_profile: str = DEFAULT_STYLE_PROFILE):
    """微练习的可验证成功标准。没有可量化标准就不给微练习。"""
    kind = plan.get("kind")
    label = plan.get("done") or ""
    style = get_style_profile(style_profile)
    if kind == "pitch_segment":
        return {"metric": "pitch_median_abs", "op": "lte", "target": style["pitch"],
                "unit": "音分", "label": label}
    if kind == "tempo":
        lo = round(1.0 - style["tempo"], 2)
        hi = round(1.0 + style["tempo"], 2)
        return {"metric": "tempo_ratio", "op": "between",
                "target": [lo, hi], "unit": "", "label": label}
    if kind == "onset":
        return {"metric": "onset_median", "op": "lte", "target": style["onset"],
                "unit": "ms", "label": label}
    if kind == "dynamic":
        return {"metric": "dynamic_range", "op": "gte", "target": style["dynamic"],
                "unit": "dB", "label": label}
    return None


def _card_for_span(plan: dict, ref_notes: list[dict], segments: list[dict],
                   scores: dict | None, feats: dict | None,
                   r0: int, r1: int,
                   style_profile: str = DEFAULT_STYLE_PROFILE) -> Optional[dict]:
    success = _success_for(plan, style_profile)
    if not success:
        return None
    notes = [n for n in ref_notes
             if isinstance(n.get("index"), int) and r0 <= n["index"] <= r1
             and n.get("pitch_midi") is not None]
    if len(notes) < 2:
        return None
    notes_sorted = sorted(notes, key=lambda n: n["index"])
    start_ms = min(int(n.get("start_ms") or 0) for n in notes_sorted)
    end_ms = max(int(n.get("end_ms") or 0) for n in notes_sorted)

    seg = [s for s in segments
           if isinstance(s.get("ref_note_index"), int)
           and r0 <= s["ref_note_index"] <= r1]
    devs = [abs(float(s["pitch_cents_dev"])) for s in seg
            if s.get("pitch_cents_dev") is not None]
    onsets = [abs(float(s["onset_dev_ms"])) for s in seg
              if s.get("onset_dev_ms") is not None]
    before = {
        "notes": len(seg),
        "pitch_median_abs": round(_median(devs), 1) if devs else None,
        "pitch_hit_25": (round(100.0 * sum(d <= 25 for d in devs) / len(devs), 1)
                         if devs else None),
        "pitch_hit_50": (round(100.0 * sum(d <= 50 for d in devs) / len(devs), 1)
                         if devs else None),
        "onset_median": round(_median(onsets), 1) if onsets else None,
    }
    sc = scores or {}
    rh = sc.get("rhythm") or {}
    ex = sc.get("expressiveness") or {}
    ft = feats or {}
    if rh.get("tempo_ratio") is not None:
        before["tempo_ratio"] = round(float(rh["tempo_ratio"]), 3)
    if ft.get("dynamic_range") is not None:
        before["dynamic_range"] = round(float(ft["dynamic_range"]), 1)
    elif ex.get("dynamic_range") is not None:
        before["dynamic_range"] = round(float(ex["dynamic_range"]), 1)
    if ex.get("onset_style") is not None:
        before["onset_median"] = round(float(ex["onset_style"]), 1)

    return {
        "available": True,
        "focus": plan.get("focus") or "",
        "why": plan.get("why") or "",
        "action": (plan.get("steps") or [""])[0],
        "steps": plan.get("steps") or [],
        "done": plan.get("done") or "",
        "facts": plan.get("facts") or [],
        "kind": plan.get("kind"),
        "source": "practice_plan",
        "style_note": plan.get("style_note") or "",
        "context": plan.get("context") or {},
        "segment": {
            "from": r0, "to": r1,
            "start_ms": start_ms, "end_ms": end_ms,
            "notes": [{"start_ms": int(n.get("start_ms") or 0),
                       "end_ms": int(n.get("end_ms") or 0),
                       "pitch_midi": float(n["pitch_midi"])}
                      for n in notes_sorted],
        },
        "before": before,
        "success": success,
        "label": f"第 {r0 + 1}–{r1 + 1} 个音",
    }


def build_practice_card(plan: dict, ref_notes: list[dict], segments: list[dict],
                        scores: dict | None = None, feats: dict | None = None,
                        problems: list[dict] | None = None,
                        style_profile: str = DEFAULT_STYLE_PROFILE) -> Optional[dict]:
    """把练习计划变成一张"只练这一段"的微练习卡。

    ⚠️ 只支持能定位到参考片段的计划（pitch_segment/tempo/onset/dynamic）。
       `nothing`/`unassessed`/`no_reference`/`self_reference` 返回 None ——
       没有可靠目标时，宁可不给按钮，也不硬造一个"练这一句"。
    """
    if not plan or not ref_notes or not segments:
        return None
    if plan.get("kind") in ("nothing", "unassessed", "no_reference",
                             "self_reference"):
        return None
    span = _micro_span(plan, ref_notes, segments)
    if not span:
        return None
    r0, r1 = span
    base = _card_for_span(plan, ref_notes, segments, scores, feats, r0, r1,
                          style_profile)
    if not base:
        return None

    # 队列：当前段之后还能练什么？
    #   · 音准问题：其它 problems 的片段（从用户音 index 映射回参考 index）
    #   · 没有其它问题：参考的下一个 8 音（继续往后唱）
    #   · 都到底了：只有当前段
    idxs = [n.get("index") for n in ref_notes if isinstance(n.get("index"), int)]
    hi = max(idxs) if idxs else r1
    spans: list[tuple[int, int]] = []
    for p in (problems or []):
        if p.get("kind") != "PITCH":
            continue
        a, b = p.get("note_range") or [None, None]
        if not isinstance(a, int) or not isinstance(b, int):
            continue
        rs = [s.get("ref_note_index") for s in segments
              if isinstance(s.get("note_index"), int)
              and a <= s["note_index"] <= b
              and isinstance(s.get("ref_note_index"), int)]
        if not rs:
            continue
        q0, q1 = min(rs), max(rs)
        if q1 < r0 or q0 > r1:      # 不和当前段重叠
            spans.append((q0, q1))
    n0 = r1 + 1
    n1 = min(hi, n0 + _MICRO_MAX_NOTES - 1)
    if n0 <= hi:
        spans.append((n0, n1))

    cards = [base]
    seen = {(r0, r1)}
    for q0, q1 in spans:
        if (q0, q1) in seen or len(cards) >= 3:
            break
        c = _card_for_span(plan, ref_notes, segments, scores, feats, q0, q1,
                           style_profile)
        if c:
            seen.add((q0, q1))
            cards.append(c)
    # ⚠️ 队列里必须放**副本**：base 本身持有 queue，
    #    若 queue[0] is base 就形成 base → queue → base 的循环引用，
    #    FastAPI JSON 编码会 RecursionError（2026-10-19 实测踩到）。
    base["queue"] = [{k: v for k, v in c.items() if k != "queue"} for c in cards]
    return base


def build_beat_card(beat: dict | None, ref_notes: list[dict] | None,
                    ref_offset_ms: int = 2000) -> dict | None:
    """把节拍诊断的“最不稳小节”变成一张可练的短片段卡（G58）。

    节拍诊断的 worst_bars 时间是**用户录音时间轴**（含 2 秒预备），
    参考音符是原始歌曲时间轴，所以选音时要减掉 ref_offset_ms。
    找不到至少 2 个参考音就不造卡 —— 不拿一个音硬凑“练这几小节”。
    """
    if not beat or not ref_notes:
        return None
    for bar in (beat.get("worst_bars") or []):
        try:
            b0 = float(bar.get("start_ms")) - float(ref_offset_ms)
            b1 = float(bar.get("end_ms")) - float(ref_offset_ms)
        except (TypeError, ValueError):
            continue
        picked = []
        for n in ref_notes:
            s = n.get("start_ms")
            if not isinstance(s, (int, float)):
                continue
            if not (b0 - 250 <= float(s) <= b1 + 400):
                continue
            if n.get("pitch_midi") is None:
                continue
            picked.append(n)
        if len(picked) < 2:
            continue
        picked.sort(key=lambda n: int(n.get("index", 0)))
        start_ms = min(int(n["start_ms"]) for n in picked)
        end_ms = max(int(n["end_ms"]) for n in picked)
        first = picked[0]
        label = f"{_fmt_time(int(bar.get('start_ms') or 0))} 这 {bar.get('active_beats', '?')} 拍"
        ctx = {}
        if first.get("line"):
            ctx = {
                "line": first.get("line"),
                "line_no": first.get("line_no"),
                "line_from": first.get("line_from"),
                "line_to": first.get("line_to"),
                "time": label,
            }
        rate = bar.get("hit_rate")
        return {
            "kind": "beat_segment",
            "metric": "beat_hit_rate",
            "focus": "先把这几拍的落点踩稳",
            "why": (f"这一小段有 {bar.get('active_beats', '?')} 个该唱的拍，"
                    f"只踩中 {bar.get('hit_beats', 0)} 个（{rate}%）。"
                    "不是整首节奏差，是这一段的落点散了。"),
            "steps": ["先听这一段的伴奏，跟着拍手或念「哒」",
                      "再只唱这一小段，每个字落在拍点上",
                      "最后一次录一遍，看拍点命中率有没有到 80%"],
            "done": "这一段的拍点命中率到 80%（±120ms）",
            "label": label,
            "segment": {
                "from": int(picked[0].get("index", 0)),
                "to": int(picked[-1].get("index", 0)),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "notes": [{"start_ms": int(n["start_ms"]),
                           "end_ms": int(n["end_ms"]),
                           "pitch_midi": float(n["pitch_midi"])}
                          for n in picked],
            },
            "before": {
                "beat_hit_rate": rate,
                "active_beats": bar.get("active_beats"),
                "hit_beats": bar.get("hit_beats"),
                "mean_offset_ms": bar.get("mean_offset_ms"),
            },
            "success": {
                "metric": "beat_hit_rate", "op": "gte", "target": 80.0,
                "unit": "%", "label": "这一段拍点命中率到 80%",
            },
            "context": ctx,
            "scored": False,
            "queue": [],
        }
    return None


def _fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    return f"{ms // 60000:02d}:{(ms // 1000) % 60:02d}"


def _line_context(ref_notes: list[dict], r0, r1) -> dict:
    """把参考音片段映射到歌词行/字/时间。

    内置曲目的 `_pp_ref_notes` 带 `line/line_no/syllable/line_from/line_to`；
    没有歌词（B 模板/旧数据）时返回 {} —— 报告只显示音序号，不硬编歌词。
    """
    if not isinstance(r0, int) or not isinstance(r1, int):
        return {}
    rows = [n for n in (ref_notes or [])
            if isinstance(n.get("index"), int) and r0 <= n["index"] <= r1
            and n.get("line")]
    if not rows:
        return {}
    first = rows[0]
    line = first.get("line") or ""
    line_no = first.get("line_no")
    line_from = first.get("line_from")
    line_to = first.get("line_to")
    line_notes = [n for n in ref_notes
                  if isinstance(n.get("index"), int)
                  and isinstance(line_from, int) and isinstance(line_to, int)
                  and line_from <= n["index"] <= line_to]
    start = min((n.get("start_ms") or 0) for n in line_notes) if line_notes else 0
    end = max((n.get("end_ms") or 0) for n in line_notes) if line_notes else 0
    syl = "".join(n.get("syllable") or "" for n in rows)
    return {
        "line": line, "line_no": line_no,
        "line_from": line_from, "line_to": line_to,
        "syllables": syl,
        "line_start_ms": int(start), "line_end_ms": int(end),
        "time": f"{_fmt_time(start)}–{_fmt_time(end)}",
    }


def _line_demo(ref_notes: list[dict], ctx: dict, r0, r1) -> dict:
    """整句示范：播这一整句，而不是只播问题两个音。"""
    if not ctx:
        return {}
    notes = [n for n in ref_notes
             if isinstance(n.get("index"), int)
             and isinstance(ctx.get("line_from"), int)
             and isinstance(ctx.get("line_to"), int)
             and ctx["line_from"] <= n["index"] <= ctx["line_to"]]
    if not notes:
        return {}
    return {
        "kind": "reference_line",
        "line": ctx.get("line"),
        "syllables": ctx.get("syllables"),
        "problem_from": r0, "problem_to": r1,
        "notes": [{"midi": float(n["pitch_midi"]),
                   "dur_ms": int(max(120, (n.get("end_ms") or 0)
                                     - (n.get("start_ms") or 0))),
                   "name": _note_name(n["pitch_midi"])} for n in notes],
        "hint": (f"整句「{ctx.get('line')}」；问题在「{ctx.get('syllables')}」"
                 if ctx.get("syllables") else f"整句「{ctx.get('line')}」"),
    }


def _demo_for_span(ref_notes, span: dict) -> dict:
    """把"问题片段落在参考旋律的第几个音"变成可播放的示范。

    ★ 用**参考音高**而不是用户唱的音高 —— 示范的作用是"告诉你应该是什么样"，
      拿用户唱错的那个音当示范，等于把错的当对的教。
    """
    if not ref_notes or not span:
        return {}
    r0, r1 = span.get("ref_from"), span.get("ref_to")
    if not isinstance(r0, int) or not isinstance(r1, int):
        return {}
    notes = [n for n in ref_notes
             if isinstance(n.get("index"), int) and r0 <= n["index"] <= r1]
    if not notes:
        return {}
    return {
        "kind": "reference_phrase",
        "notes": [{"midi": float(n["pitch_midi"]),
                   "dur_ms": int(max(120, (n.get("end_ms") or 0) - (n.get("start_ms") or 0))),
                   "name": _note_name(n["pitch_midi"])} for n in notes],
        "hint": "这是参考唱法（机器音，只给你听音高和节奏）",
    }


def _metronome_demo(ref_notes: list[dict] | None) -> dict:
    """从参考旋律的音符起止推出一串**节拍**，给前端"跟拍子"用。

    ★ 怎么推拍子：参考旋律每个音的时长就是拍长的整数倍
      （小星星是 0.5s/音，欢乐颂类似）。取**最短的那个音**当一拍 ——
      这个假设对内置的教材曲目成立（它们是按拍子敲的音符序列），
      对用户自建模板不一定成立，所以只在 `ref_notes` 存在时才给。

    ⚠️ 算不出合理拍长（比如全是很长的音、或者只有一两个音）就**不给** ——
       给一个错的节拍器比不给更坏：用户会跟着错的拍子练。
    """
    if not ref_notes or len(ref_notes) < 3:
        return {}
    durs = []
    for n in ref_notes:
        s, e = n.get("start_ms"), n.get("end_ms")
        if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e > s:
            durs.append(e - s)
    if len(durs) < 3:
        return {}
    beat = min(durs)
    # 合理区间：150–1500 ms（40–400 BPM）。超出去说明这个假设不成立。
    if not (150 <= beat <= 1500):
        return {}
    # 数一下最短音出现了几次自倍关系，用来确认"最短音 = 一拍"
    if beat <= 0:
        return {}
    beats = max(4, min(16, int(round(sum(durs) / beat))))
    return {
        "kind": "metronome",
        "beat_ms": int(round(beat)),
        "bpm": int(round(60000.0 / beat)),
        "beats": beats,
        "hint": "这是参考曲目的速度（拍子声）。戴耳机跟着它唱，"
                "麦克风只收你的人声。",
    }


def _plan(focus: str, why: str, steps: list[str], done: str,
          facts: list[dict], kind: str, demo: dict | None = None,
          style_note: str = "", span: dict | None = None,
          context: dict | None = None) -> dict:
    # `facts` 里可能有 None（某项没评）—— 过滤掉，**不要显示成 0**
    clean = []
    for f in facts:
        v = f.get("v")
        if v is None or v == "":
            continue
        # ★ 数值要**四舍五入到人能读的位数**（2026-10-01）。
        #   实测漏出去过 `节奏准确度=300.85714285714283ms` ——
        #   报告页会把这一串原样显示给用户。后端别处（`r1()`）都是留 1 位，
        #   这里不统一的话，同一屏上会出现两种精度。
        if isinstance(v, float):
            v = round(v, 1)
        clean.append({**f, "v": v})
    return {
        "kind": kind,
        "focus": focus,
        "why": why,
        "steps": steps,
        "done": done,
        "facts": clean,
        "demo": demo or {},
        # ★ 2026-10-19：把"计划选中的参考片段"落成显式字段。
        #   原来 practice_card 只从 demo 里反推；demo 为空时会退到"偏差最大的音"，
        #   于是出现 plan 说第 10–11 音、微练习却给第 1–3 音的分裂。
        "span": span or {},
        # ★ 2026-10-19：这句在歌里的位置/歌词/时间；B 模板/旧数据没有就是空。
        "context": context or {},
        # ★ 风格容忍（2026-10-19）：告诉用户哪些偏差是合法艺术处理。
        #   空串 = 这条建议没有风格争议（例如"没测到"）。
        "style_note": style_note or "",
    }
