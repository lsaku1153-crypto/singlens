"""LLM 教练层 —— 把"测量结果"变成"针对这个人的教法"。

━━━ 为什么要有这一层（以及它为什么接在"测量之后"）━━━

**测量层用算法是刻意的选择。** 端到端模型直接出分，
"偏差的规律结构"这个落点就没了 —— 而那正是本项目的核心论点。

但"陪练"缺的是**教学**：

    规则版（`server.build_summary`）      本模块（LLM）
    ────────────────────────────────    ──────────────────────────────
    "第 3–8 个音拖了 351ms，跟着拍子唱"   "你的问题在句首起唱太急 ——
                                          先只练第一句的第一个字，
                                          用手打拍子"
    一个指标一句，模板拼                结合多个指标的**组合**给一个判断
    "音准好但节奏乱"和"音准差但节奏稳"  这两种人该给完全不同的建议
    给不出                             LLM 能

━━━ ★ 三条安全约束（写死在 prompt 与代码里）━━━

  ① **不许编数字**：只准引用输入里给出的数。要提到别的事就说"这次没测"。
  ② **不许评价人格**："你天生五音不全"这种话，无论数据长什么样都不该由系统说出口。
  ③ **不许承诺**：不预测"下次能到多少分"。

━━━ 降级链（必须保证"没它就空着"不会发生）━━━

  · 没配 API key        → 直接返回 None，调用方用规则版（**不是报错**）
  · 网络不通/超时        → 返回 None + 记原因
  · 返回内容不合规       → 丢弃，用规则版
  · 任何异常            → 吞掉并返回 None（**绝不能让 LLM 故障拖垮报告页**）

⇒ 所以本模块**永远不抛异常**，只返回 `(文本, 来源, 原因)`。

━━━ API key 放哪 ━━━

  ① 环境变量 `SINGLENS_LLM_KEY`（推荐）
  ② 文件 `data/llm_key.txt`（一行，便于本地开发）
  ⚠️ **两者都不进 git**（`.gitignore` 已忽略 `*llm_key*` / `*.key`）
  ⚠️ 报告里**绝不回显 key**，只回显 `comment_source`（模型名）。

用法（自检，不联网也能跑）：
    .venv\\Scripts\\python.exe analysis/coach.py
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

try:  # 服务里按包导入；直接 `python analysis/coach.py` 时按模块导入
    from analysis.persona import system_header as _persona_header
except ImportError:  # pragma: no cover
    from persona import system_header as _persona_header
try:
    from analysis import grounding as _grounding
except ImportError:  # pragma: no cover
    import grounding as _grounding

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILE = os.path.join(ROOT, "data", "llm_key.txt")

# 默认走 DeepSeek（契约 §5.1）。2026-10-24 起默认模型改为
# `deepseek-flash`（DeepSeek 当前可用模型；`deepseek-chat` 已从模型列表移除）。
# ⚠️ `base_url` 可换 —— 任何 **OpenAI 兼容**的 `/chat/completions` 都能用，
#    这样将来换模型不用改代码。
DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
TIMEOUT_S = 20.0          # deepseek-flash 会先产生 reasoning tokens，不能按旧 12s
MAX_CHARS = 260           # 总评长度上限（报告页只有那么大地方）

# ★ 系统提示：三条安全约束 + 角色 + **结构化输出**。
#   **改这个文件时先读一遍这段再动。**
#
#   ⚠️ 2026-10-07 改成结构化：作者反馈「总评太短了，而且不显眼，
#      没有 AI 陪练的感觉」。原来只要 2–4 句、不超过 120 字 ——
#      读起来像一行附注，不像一个教练在说话。
#      ⇒ 改成三个字段：结论 / 展开 / **具体练法**，并给「练法」立了好坏样例。
SYSTEM_PROMPT = _persona_header("report") + """

你会收到一份**机器测量结果**（音准、节奏、表现力的客观数据，以及若干问题片段）。
你的任务不是复述这些数字，而是**判断他这一遍最该改什么，并给一个具体练法**。

硬性要求：
1. **只能引用给你的数字**。不要推测、不要补充你没看到的指标。
   如果某个指标这次没测出来，就说"这次没测"，不要猜。
2. **不评价这个人**，只评价这一遍。"你五音不全"这类话禁止出现。
   可以说"这一句的低音偏低了"，不能说"你音准不好"。
3. **不承诺结果**。不要写"再练三次就能到 80 分"这种话。
4. 用**第二人称**、口语、像老师当面说话。**只讲最要紧的一件事**，不要面面俱到。
5. **不要逐项报分**：最多引用 3 个真正影响判断的数字；不要从总分开始念。
6. **音准分是两半**：准确度（`pitch_accuracy_cents`，整首偏高/偏低多少）
   与稳定性（`pitch_stability_cents`，每个音上下浮动多少）。
   `pitch_score` 低而 `pitch_accuracy_cents` 很小，说明问题是**音没站住**，
   不能说"音高不是主要问题"—— 那会把一个 14 分的音准说成没问题。
   节奏同理：`rhythm_accuracy_ms` 是整遍快慢，`rhythm_stability_ms` 是忽快忽慢。
   输入的 `pitch_note` / `rhythm_note` / `tempo_note` 已经替你判断好了，以它为准。

输出格式：**只输出一个 JSON 对象**，不要 markdown 代码块、不要任何额外文字：

{
  "headline": "一句话结论（不超过 24 字）。要具体，不要写'继续加油'这种空话。",
  "body": "2–3 句展开：为什么是这个结论、依据是哪个数。60–110 字。",
  "drill": "一个**具体到能立刻照做**的练法。说明练什么、怎么练、练到什么算好。50–100 字。"
}

关于 `drill` 的好坏（**这条最重要**）：
  好："打开节拍器调到 60，只唱第一句，每个字落在拍点上。念顺了再带旋律。"
  差："多练习节奏。"（等于没说）
  好："把第 4–8 个音单独抽出来，降一个八度慢慢唱三遍，再回到原调。"
  差："注意音准。"
"""


def load_key() -> str | None:
    """读 API key。环境变量优先，其次文件。都读不到返回 None。

    `SINGLENS_DISABLE_LLM=1` 用于验证套件/离线演示：即使本机有 key，
    也强制走规则版，避免测试真的调用外部模型。
    """
    if (os.environ.get("SINGLENS_DISABLE_LLM") or "").strip().lower() in ("1", "true", "yes"):
        return None
    k = (os.environ.get("SINGLENS_LLM_KEY") or "").strip()
    if k:
        return k
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip()
        return k or None
    except OSError:
        return None


def available() -> bool:
    """LLM 这条路现在能不能走。`/api/config` 的 `llm_enabled` 用它。"""
    return load_key() is not None


def _fmt(v, unit="", nd=1):
    if v is None:
        return "没测出来"
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}{unit}"
    return str(v)


def build_payload(scores, feats, problems, ref_title=None,
                  tempo_ratio=None, self_reference=False) -> dict:
    """把报告里的结构化数据**压成一小块**给模型。

    ⚠️ 刻意只给这几项，不给全量：
      · 给得越多，模型越容易"挑一个数复述"而不是给判断
      · 而且 token 越少越快 —— 报告页在等它

    ⚠️ **不给原始 segment 列表**（可能有上百条），只给聚合后的 `problems`。
    """
    p = scores.get("pitch") or {}
    rh = scores.get("rhythm") or {}
    ex = scores.get("expressiveness") or {}
    metrics = {}
    for k in ("vibrato_rate", "vibrato_depth", "brightness", "dynamic_range"):
        if feats and feats.get(k) is not None:
            metrics[k] = round(float(feats[k]), 3)

    out = {
        "reference": ref_title,
        "total": _fmt(scores.get("total"), nd=1),
        "pitch_score": _fmt(p.get("score"), nd=1),
        "pitch_accuracy_cents": _fmt(p.get("accuracy"), "音分", 1),
        # ★ 2026-10-20 必须有：音准分是「准确度 + 稳定性」两半。
        #   只给 accuracy 时模型会写出"整体偏差只有 -1.2 音分，音高不是主要问题"，
        #   而同一份报告的音准分是 14.5 —— 因为稳定性是 115 音分。
        #   实测（sess_1b9d475c59c1）教练卡与总评直接互相打脸。
        "pitch_stability_cents": _fmt(p.get("stability"), "音分", 1),
        "rhythm_score": _fmt(rh.get("score"), nd=1),
        "rhythm_accuracy_ms": _fmt(rh.get("accuracy"), "ms", 0),
        "rhythm_stability_ms": _fmt(rh.get("stability"), "ms", 0),
        "rubato_ms": _fmt(rh.get("rubato"), "ms", 0),
        "expressiveness_score": _fmt(ex.get("score"), nd=1),
        "metrics": metrics,
        "problems": [
            {"what": x.get("headline"), "detail": x.get("detail"),
             "from_note": x.get("start_note"), "to_note": x.get("end_note")}
            for x in (problems or [])[:4]
        ],
    }
    # ★ 把"分数为什么低"先用规则算成人话再给模型。
    #   理由与 tempo_note 相同：方向/因果不能丢给模型自己从两个数里推，
    #   它推错时写的是一段看起来很有道理的错话。
    pscore, acc, stab = p.get("score"), p.get("accuracy"), p.get("stability")
    if isinstance(pscore, (int, float)):
        if (isinstance(stab, (int, float)) and stab > 60
                and (not isinstance(acc, (int, float)) or abs(acc) < 30)):
            out["pitch_note"] = (
                f"音准分 {pscore:.0f} 的主要原因是**音高不稳**：每个音上下浮动"
                f"约 {stab:.0f} 音分；整遍并没有跑调（整体偏差只有 {acc:.1f} 音分）。"
                "说的时候要把这两半分开，不能因为 '整体偏差小' 就说音准没问题。")
        elif isinstance(acc, (int, float)) and abs(acc) >= 30:
            out["pitch_note"] = (
                f"整遍音准偏向{'低' if acc < 0 else '高'} {abs(acc):.0f} 音分"
                f"（稳定性 {stab:.0f} 音分）。这是「整首不在调上」与「音没站住」"
                "两类问题，要分开说。")
    rscore = rh.get("score")
    if (isinstance(rscore, (int, float))
            and isinstance(rh.get("stability"), (int, float))
            and rh.get("stability") > 120):
        out["rhythm_note"] = (
            f"节奏分 {rscore:.0f} 的主要原因是**每个音前后不稳**"
            f"（残差抖动 {rh['stability']:.0f} ms），不是整遍快慢"
            "（那条看 tempo_note）。")
    if tempo_ratio is not None:
        # ⚠️ 方向必须**替模型算好**，不能丢一个比值让它自己解释。
        #    实测踩过：`tempo_ratio=0.9294` 被模型写成
        #    「还比参考快了 **0.93 倍**」—— 数字对，但"0.93 倍"这个说法
        #    在中文里会被读成"慢了"（倍数 <1 表示不到一倍）。
        #    ⇒ 直接把结论写成人话给它，模型就不会算错。
        #    （本项目为这个方向错过一次：把"慢了 27%"写成"快了 27%"。）
        pct = abs(1.0 - float(tempo_ratio)) * 100
        if abs(1.0 - float(tempo_ratio)) < 0.03:
            out["tempo_note"] = "整体速度与参考基本一致。"
        elif tempo_ratio > 1:
            out["tempo_note"] = (f"整体比参考慢约 {pct:.0f}%"
                                 f"（时长是参考的 {tempo_ratio:.2f} 倍）。"
                                 f"注意：这不一定是缺点，可能是句间换气或有意放慢。")
        else:
            out["tempo_note"] = (f"整体比参考快约 {pct:.0f}%"
                                 f"（时长是参考的 {tempo_ratio:.2f} 倍）。"
                                 f"说明在赶。")
    if self_reference:
        out["note"] = "这一遍是拿它自己当参考的（模式 B 第一遍），音准与节奏不评。"
    return out


# ══════════════════════════════════════════════════════════════
# ★ API key 的读写 —— 让用户能在**前端**直接配（2026-10-07）
# ══════════════════════════════════════════════════════════════
def save_key(key: str) -> bool:
    """把 API key 写进 `data/llm_key.txt`。成功返回 True。

    ⚠️ 安全约束（三条都要守住）：
      · 文件在 `.gitignore` 里（`*.key` / `*llm_key*`）—— **绝不进 git**
      · 尽量收窄文件权限（`0o600`）；Windows 上 chmod 是弱保证，
        但**真正重要的是它不在版本库里**
      · **key 本身任何情况下都不回显** —— `/api/config` 只暴露布尔，
        要看"是不是我输的那把"只能用 `key_hint()`（前 3 + 后 4）
    """
    k = (key or "").strip()
    if not k:
        return False
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(k)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return True


def clear_key() -> bool:
    """删掉 key 文件。

    ⚠️ 环境变量那份**删不掉**（进程外的东西）⇒ 返回 False 时调用方要说明这一点，
       否则用户会以为"清了但还生效"是这个系统坏了。
    """
    try:
        os.remove(KEY_FILE)
        return True
    except OSError:
        return False


def key_source() -> str:
    """key 从哪来：`env` / `file` / `none`。**只说来源，不说值。**"""
    if (os.environ.get("SINGLENS_LLM_KEY") or "").strip():
        return "env"
    if os.path.exists(KEY_FILE):
        return "file"
    return "none"


def key_hint() -> str | None:
    """给用户辨认用的一小段提示（`sk-…a1b2`）。

    ⚠️ 只露**前 3 位 + 后 4 位** —— 够辨认，不足以复原。
       长度不足 12 位时**一个字符都不给**：太短的 key 露头尾就等于露全部。
    """
    k = load_key()
    if not k or len(k) < 12:
        return None
    return f"{k[:3]}…{k[-4:]}"


def test_key(key: str | None = None) -> tuple[bool, str]:
    """拿这把 key **真调一次**，验证能不能用。返回 `(可用, 说明)`。

    ⚠️ 为什么必须真调一次，而不是只看格式：
       格式对但**欠费 / 被撤销 / 额度用完**的 key 到处都是，
       而"保存成功"会让用户以为能用了 —— 到答辩现场才发现不行。
       代价是一次极小的请求（`max_tokens=1`）。
    """
    k = (key or load_key() or "").strip()
    if not k:
        return False, "没有 key"
    base = (os.environ.get("SINGLENS_LLM_BASE") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("SINGLENS_LLM_MODEL") or DEFAULT_MODEL
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1}
    try:
        _post(f"{base}/chat/completions", k, body, 20.0)
        return True, "可以正常调用"
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        # 把最常见的几种翻成人话 —— 否则用户只看到一串 JSON，
        # 而"401"和"402"该做的事完全不同（换 key / 去充值）
        HINT = {401: "key 不对（或已被撤销）", 402: "账户欠费",
                403: "这把 key 没有权限", 429: "调用太频繁或额度用完",
                404: "模型名不对（可用 SINGLENS_LLM_BASE / SINGLENS_LLM_MODEL 换）"}
        head = HINT.get(e.code, f"HTTP {e.code}")
        # ⚠️ 原文只留**一句**（面板上塞不下整段 JSON）。
        #    第一版把整个 body 拼进去，界面上倒了 200 字符的 JSON ——
        #    用户看不懂，还把关键信息挤出视野。
        #    挑出 `message` 字段（如果有），否则截断。
        brief = ""
        try:
            obj = json.loads(raw)
            brief = str((obj.get("error") or {}).get("message") or "")[:120]
        except Exception:  # noqa: BLE001
            brief = raw[:120]
        return False, (f"{head}｜{brief}" if brief else head)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"网络不通：{str(e)[:80]}"
    except Exception as e:  # noqa: BLE001
        return False, f"未预期异常：{str(e)[:80]}"


def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _parse_struct(text: str) -> dict | None:
    """把模型返回的内容解析成 `{headline, body, drill}`。

    ⚠️ 模型**经常不听话**：可能包在 ```json 里、可能前后带一句话、
       也可能返回纯散文（prompt 要求 JSON 但它没照做）。
       ⇒ 这里**尽量宽容**，解析不出来就返回 None，
         由调用方**退回散文模式** —— **绝不能因为格式问题让总评消失。**
    """
    if not text:
        return None
    s = text.strip()
    # 剥掉 markdown 代码围栏
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
        s = s.strip()
    # 前后可能有废话 —— 取第一个 `{` 到最后一个 `}`
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        s = s[a:b + 1]
    try:
        obj = json.loads(s)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(obj, dict):
        return None
    out = {}
    for k in ("headline", "body", "drill"):
        v = obj.get(k)
        out[k] = v.strip() if isinstance(v, str) and v.strip() else None
    # 至少要有一个非空字段，否则等于没解析出来
    return out if any(out.values()) else None


def generate(scores, feats, problems, ref_title=None, tempo_ratio=None,
             self_reference=False) -> tuple[str | None, dict | None, str, str]:
    """产出总评。**永远不抛异常。**

    返回 `(文本, 结构化, 来源, 原因)`：
      · 成功 → `(拼好的文本, {headline, body, drill}, "deepseek-flash", "")`
        `结构化` 可能为 `None`（模型没按 JSON 回）——那时前端按散文渲染。
      · 失败 → `(None, None, "rule_fallback", 失败原因)` —— 调用方走规则版

    ⚠️ **两个都返回**，不是二选一：
       前端优先用结构化（好看、能突出"练法"），
       拿不到就退回散文 —— 这样**换模型/换 prompt 都不会让界面空掉**。
    """
    key = load_key()
    if not key:
        return None, None, "rule_fallback", "没配 API key"

    base = (os.environ.get("SINGLENS_LLM_BASE") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("SINGLENS_LLM_MODEL") or DEFAULT_MODEL
    payload = build_payload(scores, feats, problems, ref_title, tempo_ratio,
                            self_reference)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                "测量结果（JSON）：\n" + json.dumps(payload, ensure_ascii=False,
                                                    indent=1)
                + "\n\n请按系统提示要求，只输出那个 JSON 对象。"},
        ],
        "temperature": 0.6,
        # ⚠️ `deepseek-flash` 是推理型模型：completion 里先消耗 reasoning tokens。
        #    700 时实测 reasoning 把额度吃满、content 为空（finish_reason=length）。
        #    留出 reasoning + 结构化输出的空间。
        "max_tokens": 1500,
    }

    t0 = time.time()
    try:
        resp = _post(f"{base}/chat/completions", key, body, TIMEOUT_S)
    except urllib.error.HTTPError as e:
        # 把状态码带上 —— 401/402/429 处置完全不同，不能只说"失败了"
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return None, None, "rule_fallback", f"HTTP {e.code} {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, None, "rule_fallback", f"网络不通/超时：{str(e)[:80]}"
    except Exception as e:  # noqa: BLE001
        return None, None, "rule_fallback", f"未预期异常：{str(e)[:80]}"

    try:
        raw = (resp["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        return None, None, "rule_fallback", f"返回结构不对：{str(e)[:80]}"

    # ── 先试结构化 ──
    struct = _parse_struct(raw)
    if struct:
        # 合规检查要对**三段都做** —— 违禁话可能只出现在 drill 里
        joined = " ".join(v for v in struct.values() if v)
        bad = _violates(joined)
        if bad:
            return None, None, "rule_fallback", f"生成内容不合规（{bad}），已丢弃"
        bad_num = _grounding.validate_text(joined, payload)
        if bad_num:
            return None, None, "rule_fallback", f"生成内容没有数字依据（{bad_num}），已丢弃"
        text = "\n".join(v for v in (struct.get("headline"),
                                     struct.get("body"),
                                     struct.get("drill")) if v)
        return text, struct, model, ""

    # ── 退回散文（模型没按 JSON 回）──
    text = raw
    bad = _violates(text)
    if bad:
        return None, None, "rule_fallback", f"生成内容不合规（{bad}），已丢弃"
    bad_num = _grounding.validate_text(text, payload)
    if bad_num:
        return None, None, "rule_fallback", f"生成内容没有数字依据（{bad_num}），已丢弃"
    if len(text) > MAX_CHARS * 2:
        text = text[:MAX_CHARS * 2].rstrip() + "…"
    _ = time.time() - t0
    return text, None, model, ""


# 违禁词：**只拦最明确的那几类**，不做过度过滤
#   （过度过滤会把正常表达也拦掉，反而让 LLM 这条路形同虚设）
_BANNED = [
    # ① 评价人格（“笨办法/笨功夫”是正常教学词，不放进裸词表）
    "五音不全", "没有天赋", "不是这块料", "差劲",
    # ② 承诺结果
    "一定能", "保证", "几天就", "很快就能到", "包你",
]
_BANNED_PATTERNS = [
    # 只拦“你这个人/你/学生 + 笨/差”的用法，不拦“用笨办法练”
    r"(?:你|他|她|学生|人)(?:这个人)?(?:真|很|太|有点|就是)?(?:笨|差劲)",
    r"笨(?:死|蛋|人|学生|猪)",
    r"天生(?:就)?(?:五音不全|不适合|不会唱|音痴|笨)",
]


def _violates(text: str) -> str:
    if not text or len(text) < 8:
        return "内容过短"
    for w in _BANNED:
        if w in text:
            return f"含违禁表述「{w}」"
    for pat in _BANNED_PATTERNS:
        m = re.search(pat, text)
        if m:
            return f"含违禁表述「{m.group(0)}」"
    return ""


# ══════════════════════════════════════════════════════════════
# 自检：不联网也要能跑
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 92)
    print("LLM 教练层自检（不联网）")
    print("=" * 92)

    print(f"\n  API key：{'✅ 已配置' if available() else '❌ 没配（会走规则兜底）'}")
    print(f"    环境变量 SINGLENS_LLM_KEY："
          f"{'有' if os.environ.get('SINGLENS_LLM_KEY') else '无'}")
    print(f"    文件 {os.path.relpath(KEY_FILE, ROOT)}："
          f"{'有' if os.path.exists(KEY_FILE) else '无'}")

    demo_scores = {
        "total": 55.5,
        "pitch": {"score": 51.0, "accuracy": 38.2, "stability": 22.1},
        "rhythm": {"score": 50.0, "accuracy": 246.4, "stability": 229.6,
                   "rubato": 87.8, "tempo_ratio": 1.104},
        "expressiveness": {"score": 62.8},
    }
    demo_feats = {"vibrato_rate": 5.42, "vibrato_depth": 0.61,
                  "brightness": 1832.0, "dynamic_range": 11.4}
    demo_problems = [
        {"headline": "第 3–8 个音起唱偏晚", "detail": "平均晚了 351ms",
         "start_note": 3, "end_note": 8},
    ]

    pl = build_payload(demo_scores, demo_feats, demo_problems,
                       "小星星 · 六句（含句间换气）", 1.104)
    print("\n  发给模型的 payload：")
    print("  " + json.dumps(pl, ensure_ascii=False, indent=1).replace("\n", "\n  "))

    print("\n  合规检查自测：")
    for t in ("这一句的低音偏低了，先把第一句的第一个字单独练五遍。",
              "你天生五音不全，建议放弃。",
              "再练三次一定能到 80 分。",
              "太短",
              ""):
        bad = _violates(t)
        print(f"    {'❌ 拦下' if bad else '✅ 放行'}  "
              f"{(bad or '—'):28s}  「{t[:32]}」")

    text, src, why = generate(demo_scores, demo_feats, demo_problems,
                              "小星星 · 六句", 1.104)
    print(f"\n  实调：来源={src}  原因={why or '成功'}")
    if text:
        print(f"  生成内容：{text}")
    else:
        print("  ⇒ 走规则兜底（这是**设计如此**，不是故障）")
