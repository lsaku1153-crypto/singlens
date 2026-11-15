"""回答校验：数字、引用、注入。

三道闸的顺序是有讲究的：
  1. 空/过短 → 后面都白搭；
  2. 违禁表述（人格评价、承诺）—— 与报告质量无关，先拦；
  3. 注入 —— 回答里出现"忽略之前的指令"说明它把数据当指令了；
  4. 数字 —— 只允许本次报告与本次工具返回里出现过的值；
  5. 引用 —— 句号、时间必须真实存在。

第 4 步要先剔掉"第 N 句""MM:SS"再查数字：它们是**位置引用**，由第 5 步的
存在性检查负责。不剔的话，"00:37" 里的 37 会被当成凭空出现的测量值误杀。

第 5 步的证据范围（2026-11-14 修）
--------------------------------
以前句号只认 `rhythm.phrase.phrases`、时间认"任意 segment 起点 ±1s"：
  · 但 `get_practice_card` 会给出 `line_no`，`_line_exists` 却不认它 ——
    系统照着自己给的卡答，被自己的校验器判成编造；
  · 真实全曲报告有 506 个 segment，±1s 覆盖 97% 的秒，时间检查等于恒真。
现在：
  · 句号 = 报告里所有出现过的行号（句级行 / 练习卡上下文 / segment 行号）
    + **本次工具真正返回过的行号**（`found:false` 的失败查询不算）；
  · 时间 = 报告里的"被展示过的区间"（句级行、练习卡、节拍最差小节、问题片段）
    + 本次工具返回里的时间，且要落在区间内（不再拿原始音符起点铺满整首歌）。
"""
from __future__ import annotations

import re

from analysis import coach, grounding
from analysis.agent.contracts import ToolContext

_CN_DIGITS = "零一二两三四五六七八九"
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_LINE = re.compile(r"第\s*([0-9%s]+)\s*句" % _CN_DIGITS)
_TIME = re.compile(r"\b([0-5]?\d):([0-5]\d)\b")
_PERFECT = re.compile(r"(?:100\s*分|满分)")

_INJECT = [
    re.compile(r"(忽略|无视)(之前|以上|前面)(的)?(所有)?(指令|提示|规则)"),
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+instructions", re.I),
    re.compile(r"(system\s*prompt|系统提示词|开发者指令)", re.I),
    re.compile(r"你现在是.{0,12}(不受限制|没有规则)"),
]

# 文本字段：里面的数字是歌词/标题/点评，不是测量值，不该进白名单。
_TEXT_KEYS = {"text", "line", "headline", "detail", "label", "title",
              "reference_title", "note", "disclaimer", "why", "reason",
              # 报告里的自由文本（LLM 点评 / 教练结构）不能给数字授权：
              # 以前 `comments` 是顶层字符串，_walk 会把里面的数字也算进白名单。
              "comments", "comment", "comment_source", "coach_struct",
              "body", "drill", "narrative", "summary", "llm_note", "align_reason"}

# 原始逐音序列不进白名单：模型看不到 segments，只能通过工具看。
# 不排除的话，一份全曲报告的允许集合是 4400+ 个数字，等于没有约束。
_SKIP_KEYS = {"segments"}

# "没有第 99 句"是诚实的否定，不是引用不存在的证据。
# 不剔掉它，存在性检查会把正确回答判成错误 —— 写 T7 用例时踩到的。
# 也要认"第 99 句不存在"这种**后置否定**（在线评测第二次跑踩到）。
_MISSING_REF = re.compile(
    r"(?:(?:没有|不存在|无)\s*第\s*[0-9%s]+\s*句"
    r"|第\s*[0-9%s]+\s*句\s*(?:不存在|没有|并不存在))" % (_CN_DIGITS, _CN_DIGITS))

# 句号存在性按"分句"判：一句里如果明确说了"没有/没测到/没法回答"，引用一个
# 不存在的句号是诚实的否定，不是编造。
# 2026-11-14 live 实测踩到：模型写"没有第 99 句……所以'第 99 句怎么样'我这次
# 没法回答"，旧逻辑只认紧跟在"没有"后面的那一次，第二次引用被判成编造。
_SENTENCE = re.compile(r"[^。！？；\n]+")
_DENIAL_HINT = re.compile(
    r"没有|不存在|没测到|没测|未测|没法|不能|无法|不知道|找不到|查不到"
    r"|没查到|没找到|未找到|并不存在|未包含")

# "我不能给你 100 分"是拒绝，不是承诺满分。剔除后再看有没有满分断言。
_REFUSE_PERFECT = re.compile(
    r"(?:不会|不能|没法|无法|不可|不)(?:给|说|打|评)?[^。；！？\n]{0,8}"
    r"(?:100\s*分|满分)")


def strip_denials(text: str) -> str:
    """把"拒绝给满分/否认某句"这类否定句剔掉，供关键词检查使用。"""
    return _MISSING_REF.sub(" ", _REFUSE_PERFECT.sub(" ", text or ""))


def _int_cn(s: str) -> int | None:
    """支持 12 / 十二 / 三。两位以上（三十五）不处理。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        return (_CN_NUM.get(a, 1) if a else 1) * 10 + (_CN_NUM.get(b, 0) if b else 0)
    return _CN_NUM.get(s) if len(s) == 1 else None


def _numeric_view(obj):
    """去掉歌词/标题/点评类文本与原始逐音序列，避免它们变成"合法测量值"。"""
    if isinstance(obj, dict):
        return {k: _numeric_view(v) for k, v in obj.items()
                if k not in _TEXT_KEYS and k not in _SKIP_KEYS}
    if isinstance(obj, list):
        return [_numeric_view(x) for x in obj]
    return obj


def _allowed(ctx: ToolContext, calls: list[dict]) -> set[float]:
    return grounding.allowed_numbers({
        "report": _numeric_view(ctx.report),
        # ⚠️ 工具结果也要洗：`get_phrase` 会把歌词原文带回来，
        #    歌词里的"100 分"如果原样进白名单，模型编的 100 分就被放行了。
        #    2026-11-12 端到端注入用例实测踩到：`mode=agent`、答案带着 100 分出门。
        "tools": [_numeric_view(c.get("data")) for c in calls if c.get("data")],
    })


def _rows(rep: dict) -> list[dict]:
    ph = ((rep.get("rhythm") or {}).get("phrase") or {})
    rows = ph.get("phrases") if isinstance(ph, dict) else None
    return [x for x in (rows or []) if isinstance(x, dict)]


def _line_numbers(ctx: ToolContext, calls: list[dict]) -> set[int]:
    """这份报告"出现过"的行号：句级行 + 练习卡上下文 + 本次工具返回值。"""
    out: set[int] = set()
    for r in _rows(ctx.report):
        try:
            out.add(int(r.get("line_index", -1)) + 1)
        except (TypeError, ValueError):
            continue
    for src in (ctx.report.get("practice_card"), ctx.report.get("practice_plan")):
        if isinstance(src, dict):
            c = src.get("context")
            if isinstance(c, dict) and isinstance(c.get("line_no"), int):
                out.add(c["line_no"])
    for s in (ctx.report.get("segments") or []):
        if isinstance(s, dict) and isinstance(s.get("line_no"), int):
            out.add(s["line_no"])
    for c in calls:
        data = c.get("data")
        if not isinstance(data, dict):
            continue
        # 查不到的结果不能把"查不到的行号"变成合法引用：get_phrase(99) 也带 line_no
        if data.get("found") is False or data.get("available") is False:
            continue
        if isinstance(data.get("line_no"), int):
            out.add(data["line_no"])
        for m in (data.get("matches") or []):
            if isinstance(m, dict) and isinstance(m.get("line_no"), int):
                out.add(m["line_no"])
        for p in (data.get("phrases") or []):
            if isinstance(p, dict) and isinstance(p.get("line_no"), int):
                out.add(p["line_no"])
    return out


def _line_exists(ctx: ToolContext, n: int, calls: list[dict] | None = None) -> bool:
    return n in _line_numbers(ctx, calls or [])


_TIME_RE = re.compile(r"\b([0-5]?\d):([0-5]\d)\b")


def _collect_time(obj, pts: set[int], wins: list[tuple[int, int]]) -> None:
    """从一个结构里收集"被展示过的时间"：区间用 start/end，散点用 MM:SS。"""
    if isinstance(obj, dict):
        s, e = obj.get("start_ms"), obj.get("end_ms")
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            pts.add(int(s) // 1000)
        if isinstance(e, (int, float)) and not isinstance(e, bool):
            pts.add(int(e) // 1000)
        if (isinstance(s, (int, float)) and not isinstance(s, bool)
                and isinstance(e, (int, float)) and not isinstance(e, bool)
                and e > s):
            wins.append((int(s) // 1000, int(e) // 1000))
        for v in obj.values():
            _collect_time(v, pts, wins)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_time(v, pts, wins)
    elif isinstance(obj, str):
        for m in _TIME_RE.finditer(obj):
            pts.add(int(m.group(1)) * 60 + int(m.group(2)))


def _time_sources(rep: dict) -> list:
    """报告里"给用户展示过"的时间来源。

    刻意**不含 segments**：把每个音符的 start_ms 都算进去，一首 4 分钟的歌
    会覆盖 97% 的秒，`00:59` 这种编造时刻也能过。只认句级行、练习卡、
    节拍最差小节、问题片段这些"报告上真的写出来的区间"。
    """
    rh = rep.get("rhythm") or {}
    meta = rep.get("meta") or {}
    phrase = rh.get("phrase") if isinstance(rh.get("phrase"), dict) else None
    return [
        (phrase or {}).get("phrases"),
        rep.get("practice_card"),
        rep.get("practice_plan"),
        rep.get("beat_diagnostic"),
        meta.get("beat_diagnostic"),
        rep.get("problems"),
    ]


def _time_ok(ctx: ToolContext, mmss: str,
             calls: list[dict] | None = None) -> bool:
    try:
        mm, ss = mmss.split(":")
        target = int(mm) * 60 + int(ss)
    except ValueError:
        return False
    pts: set[int] = set()
    wins: list[tuple[int, int]] = []
    for src in _time_sources(ctx.report):
        _collect_time(src, pts, wins)
    for c in (calls or []):
        if c.get("data") is not None:
            _collect_time(c["data"], pts, wins)
    if any(abs(target - p) <= 1 for p in pts):
        return True
    return any(a - 1 <= target <= b + 1 for a, b in wins)


def detect_injection(text: str) -> str:
    for rx in _INJECT:
        if rx.search(text or ""):
            return f"疑似指令注入：{rx.pattern[:28]}"
    return ""


def _line_ref_failure(text: str, lines: set[int]) -> str:
    """不存在的句号：允许"否定句"里引用，不允许平铺直叙地引用。"""
    for sent in _SENTENCE.finditer(text or ""):
        chunk = sent.group(0)
        refs = list(_LINE.finditer(chunk))
        if not refs:
            continue
        missing: list[int] = []
        for m in refs:
            n = _int_cn(m.group(1))
            if n is not None and n not in lines and n not in missing:
                missing.append(n)
        if not missing or _DENIAL_HINT.search(chunk):
            continue
        return f"引用了不存在的第 {missing[0]} 句"
    return ""


def verify(answer: str, ctx: ToolContext, calls: list[dict] | None = None) -> tuple[bool, str]:
    """通过返回 `(True, "")`；否则 `(False, 一句可展示的原因)`。"""
    calls = calls or []
    if not answer or len(answer) < 8:
        return False, "回答过短"
    bad = coach._violates(answer)
    if bad:
        return False, bad
    inj = detect_injection(answer)
    if inj:
        return False, inj

    # "100 分 / 满分" 是语义断言，不是普通数字：报告里 KTV 容差恰好有 100ms，
    # 数字白名单会把 100 放行，于是"给你 100 分"能混过去。
    # 先剔掉"我不能给你 100 分"这种拒绝，否则会把守规矩的回答判成违规。
    if _PERFECT.search(strip_denials(answer)):
        total = ctx.report.get("total")
        if not (isinstance(total, (int, float)) and float(total) >= 99.95):
            return False, "把成绩说成 100 分/满分，但报告总分不是"

    scrubbed = strip_denials(_LINE.sub(" ", _TIME.sub(" ", answer)))
    unsupported = grounding.unsupported_numbers(scrubbed, _allowed(ctx, calls))
    if unsupported:
        return False, "出现输入里没有的数字：" + "、".join(unsupported[:3])

    lines = _line_numbers(ctx, calls)
    bad_line = _line_ref_failure(answer, lines)
    if bad_line:
        return False, bad_line
    for m in _TIME.finditer(answer):
        stamp = f"{m.group(1)}:{m.group(2)}"
        if not _time_ok(ctx, stamp, calls):
            return False, f"引用了不存在的时刻 {stamp}"
    return True, ""
