"""千问音频听感 + 一条可执行建议（实验）。

流程：
    1) 把一段 8 秒以内的问题片段发给 Qwen3.8-Omni-Flash，只拿自由文本听感；
    2) 再把听感 + 本地测量发给同一个 Qwen 的文本模式，生成一条
       “下一遍能做什么”的具体动作。

它不返回分数、不改分、不生成训练计划，也不让音频模型直接写结构化评价。
2026-10-29 的实验里，直接让音频模型按固定 JSON 输出会补出调式、音名、
伴奏、分数，甚至和本地证据矛盾；所以这里把它限制成“先听，再由文本模式
结合测量写一条动作”。

隐私：
    · 只发送当前这一遍录音的 8 秒以内片段；
    · 音频只在内存里编码，不落盘、不写进结果；
    · key 只从环境变量或 data/dashscope_key.txt 读取，不回显；
    · 这个模块没有缓存音频，只缓存文本描述。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "experiment.db"
SESSION_AUDIO_DIR = ROOT / "data" / "recordings" / "sessions"
KEY_FILES = (ROOT / "data" / "dashscope_key.txt", ROOT / "data" / "qwen_key.txt")
KEY_FILE = KEY_FILES[0]
MODEL = os.environ.get("SINGLENS_AUDIO_MODEL", "qwen3.8-omni-flash")
BASE = os.environ.get(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).rstrip("/")
MAX_CLIP_SEC = 8.0
PROMPT_VERSION = 9


class AudioCoachError(RuntimeError):
    def __init__(self, message: str, *, code: str = "AUDIO_COACH_FAILED", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


def _read_key() -> str:
    k = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if k:
        return k
    for p in KEY_FILES:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            v = line.strip().lstrip("\ufeff").strip().strip('"').strip("'")
            if v and not v.startswith("#"):
                return v
    return ""


def key_source() -> str:
    """env / file / none。只报来源，不回显 key。"""
    if (os.environ.get("DASHSCOPE_API_KEY") or "").strip():
        return "env"
    for p in KEY_FILES:
        if p.exists() and _read_key_from(p):
            return "file"
    return "none"


def _read_key_from(p: Path) -> str:
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            v = line.strip().lstrip("\ufeff").strip().strip('"').strip("'")
            if v and not v.startswith("#"):
                return v
    except OSError:
        pass
    return ""


def key_hint() -> str:
    k = _read_key()
    if not k:
        return ""
    if len(k) <= 12:
        return "已配置"
    return f"{k[:3]}…{k[-4:]}"


def save_key(key: str) -> None:
    """保存到 data/dashscope_key.txt；环境变量优先，不允许被文件覆盖。"""
    if (os.environ.get("DASHSCOPE_API_KEY") or "").strip():
        raise AudioCoachError(
            "当前 key 来自环境变量 DASHSCOPE_API_KEY，程序改不了它；"
            "请先 unset，或改用它启动服务。",
            code="AUDIO_KEY_ENV", status=400)
    key = (key or "").strip()
    if len(key) < 12:
        raise AudioCoachError("key 太短，看着不像真的。", code="BAD_REQUEST", status=400)
    if any(ch in key for ch in " \t\r\n"):
        raise AudioCoachError("key 里有空白字符，多半是复制时带进来的。",
                              code="BAD_REQUEST", status=400)
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    except OSError as e:
        raise AudioCoachError(f"key 写不进 data/：{e}", code="INTERNAL", status=500) from e


def clear_key() -> bool:
    if (os.environ.get("DASHSCOPE_API_KEY") or "").strip():
        raise AudioCoachError(
            "当前 key 来自环境变量 DASHSCOPE_API_KEY，程序删不掉它。",
            code="AUDIO_KEY_ENV", status=400)
    removed = False
    for p in KEY_FILES:
        try:
            if p.exists():
                p.unlink()
                removed = True
        except OSError:
            pass
    return removed


def test_key(key: str | None = None) -> tuple[bool, str]:
    """只发一句文本验证 key；不发送任何音频。"""
    k = (key or _read_key()).strip()
    if not k:
        return False, "还没有配置 key。"
    try:
        _post_qwen(k, [{"type": "text", "text": "只回复 OK"}], max_tokens=4)
        return True, "连接正常。"
    except AudioCoachError as e:
        return False, str(e)



def _db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS audio_coach (
               session_uid TEXT PRIMARY KEY,
               singer_id TEXT,
               model TEXT NOT NULL,
               prompt_version INTEGER NOT NULL,
               clip_start_ms INTEGER NOT NULL,
               clip_end_ms INTEGER NOT NULL,
               observation TEXT NOT NULL,
               advice_json TEXT,
               usage_json TEXT NOT NULL DEFAULT '{}',
               advice_usage_json TEXT NOT NULL DEFAULT '{}',
               advice_latency_ms INTEGER,
               created_at REAL NOT NULL
           )"""
    )
    # 旧版本（2026-10-29 上午）没有 advice 三列；就地补，不删缓存。
    cols = {r["name"] for r in con.execute("PRAGMA table_info(audio_coach)")}
    for name, decl in (
        ("advice_json", "TEXT"),
        ("advice_usage_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("advice_latency_ms", "INTEGER"),
    ):
        if name not in cols:
            try:
                con.execute(f"ALTER TABLE audio_coach ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError:
                pass
    return con


def available() -> tuple[bool, str]:
    if (os.environ.get("SINGLENS_AUDIO_COACH") or "1").strip().lower() in (
        "0", "false", "off", "closed"
    ):
        return False, "音频听感补充已在服务端关闭。"
    if not _read_key():
        return False, "没有配置千问音频 key。"
    return True, "只发送这一遍的 8 秒以内片段，先听听感，再给一条可执行建议；不改分。"


def status() -> dict:
    ok, reason = available()
    return {
        "available": ok,
        "reason": reason,
        "model": MODEL,
        "max_clip_seconds": MAX_CLIP_SEC,
        "prompt_version": PROMPT_VERSION,
        "disclaimer": "实验功能：补充音色、力度、起音、句尾等听感，并给一条下一遍能做的动作；不是评分，不改分。",
    }


def forget_session(uid: str) -> int:
    """删练习时一起删掉听感文本缓存；不碰任何音频。"""
    con = _db()
    try:
        cur = con.execute("DELETE FROM audio_coach WHERE session_uid=?", (uid,))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def forget_singer(singer_id: str) -> int:
    con = _db()
    try:
        cur = con.execute("DELETE FROM audio_coach WHERE singer_id=?", (singer_id,))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def forget_all() -> int:
    con = _db()
    try:
        cur = con.execute("DELETE FROM audio_coach")
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def _clip_bounds(report: dict, duration: float) -> tuple[float, float]:
    """取问题段前后一点上下文；短录音也优先从问题段开始。

    ⚠️ 旧实现对 5 秒以内录音直接返回 0–duration。实测这条 4.92 秒的录音，
    前 2.3 秒几乎全静音，点“再听一遍”会先静 2 秒多，看起来像没反应。
    现在不管多短，都先看 problems；没有 problems 才用整段。
    """
    problems = report.get("problems") or []
    anchor = None
    for p in problems:
        if p.get("severity") == "HIGH":
            anchor = max(0.0, float(p.get("start_ms") or 0) / 1000.0)
            break
    if anchor is None and problems:
        anchor = max(0.0, float(problems[0].get("start_ms") or 0) / 1000.0)
    if anchor is None:
        return 0.0, duration
    start = max(0.0, anchor - 0.5)
    end = min(duration, start + MAX_CLIP_SEC)
    # 如果问题段在最后 1 秒，往前补到至少 1.5 秒，别只给半句。
    if end - start < 1.5:
        start = max(0.0, end - min(MAX_CLIP_SEC, 2.0))
    return start, end


def _clip_wav(uid: str, report: dict) -> tuple[bytes, float, float]:
    path = SESSION_AUDIO_DIR / f"{uid}.wav"
    if not path.is_file():
        raise AudioCoachError("这一遍的录音已不在本机，没法再听。", code="NO_AUDIO", status=404)
    try:
        info = sf.info(str(path))
        start, end = _clip_bounds(report, float(info.duration))
        data, sr = sf.read(
            str(path),
            start=int(start * info.samplerate),
            frames=max(1, int((end - start) * info.samplerate)),
            always_2d=False,
        )
    except Exception as e:  # noqa: BLE001
        raise AudioCoachError(f"读取这一遍录音失败：{e}", code="AUDIO_READ_FAILED") from e
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if len(data) == 0:
        raise AudioCoachError("这一遍没有可用的人声片段。", code="NO_AUDIO", status=404)
    buf = io.BytesIO()
    try:
        sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
    except Exception as e:  # noqa: BLE001
        raise AudioCoachError(f"编码片段失败：{e}", code="AUDIO_ENCODE_FAILED") from e
    return buf.getvalue(), start, end


PROMPT = """你只做一件事：描述这段用户唱歌片段里你确实听到的听感，帮助声乐教练补充“音色、力度、句尾、起音、咬字”。

规则：
1. 必须输出 3 行，每行分别以“音色：”“力度：”“句尾：”开头；
   听不清的那一类也要保留标签，写成“音色：听不清”这种形式，不要省略整行。
   如果片段里有明显的起音或咬字，再各用“起音：”“咬字：”补一行。
2. 每行尽量短，总字数不超过 110 个汉字。能听清才写；不要为了凑内容编。
3. 不要写：音名、调式、歌词、分数、排名、原因、伴奏、练法、声带/横膈膜。
4. 不要输出 JSON、列表、标题或 Markdown。

这段片段约 {seconds:.1f} 秒。只描述声音本身。{retry}"""


_FORBIDDEN = re.compile(
    r"(?:"
    r"[A-Ga-g][#♯b♭]?\d"
    r"|大调|小调|调式|音名|音分|分贝|Hz|dB|评分|分数|跑调"
    r"|歌词|伴奏|声带|横膈膜|喉部|共鸣|气息支撑|支撑不足"
    r"|音高|音准|节奏|进拍|拍子|偏高|偏低|准确|发声|位置"
    r"|因为|导致|说明你|说明|反映|表明|问题在于|问题"
    r"|建议|练习|练法|放松|保持|注意|需要|应该|可以|解决|改善|改"
    r"|\d{1,3}\s*分"
    r")",
    re.I,
)


def _clean(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip().strip('"“”')
    if not s:
        return ""
    # 按句保留：一句里出现禁词就丢这一句，避免整段被一句“C大调”带走。
    parts = re.split(r"(?<=[。！？!?])", s)
    kept = [p.strip() for p in parts if p.strip() and not _FORBIDDEN.search(p)]
    out = "".join(kept).strip()
    if not out:
        return ""
    if len(out) > 120:
        out = out[:120].rstrip("，,；;、 ") + "。"
    return out


def _facets(text: str) -> dict:
    """从“音色：…/力度：…/句尾：…”里拆出结构化听感。

    这只是**展示层**的拆分，不改变模型原话；raw observation 仍完整保留。
    模型没按标签写就返回空对象，绝不自己编一个值补上。
    """
    out = {}
    for m in re.finditer(r"(音色|力度|句尾|起音|咬字)\s*[：:]\s*([^。；;\n]+)", text or ""):
        label, val = m.group(1), m.group(2).strip(" ，,、")
        if val and not _FORBIDDEN.search(val):
            out[label] = val[:40]
    return out


def _evidence_for_advice(report: dict) -> str:
    p = report.get("pitch") or {}
    rh = report.get("rhythm") or {}
    ex = report.get("expressiveness") or {}
    lines = [
        f"- 音准分 {p.get('score')}，稳定度 {p.get('stability')}",
        f"- 节奏分 {rh.get('score')}，偏差 {rh.get('accuracy')} ms",
        f"- 表现力分 {ex.get('score')}；动态 {ex.get('dynamic_range')} dB；"
        f"起音 {ex.get('onset_style')} ms；谱质心 {ex.get('brightness')} Hz",
    ]
    for x in (report.get("problems") or [])[:2]:
        lines.append(f"- 本地问题：{x.get('headline') or x.get('kind')}；{x.get('detail') or ''}")
    return "\n".join(lines)


ADVICE_PROMPT = """你是声乐教练。请根据下面的本地测量和千问听感，给“下一遍只做的一件事”。

【本地测量】
{evidence}

【千问听感】
{observation}

【上次建议】
{previous}

要求：
1. 只给一条可执行动作，必须具体到“先做什么、再怎么唱一遍”，不能是“多练习/多听/注意气息”这种空话。
2. 优先针对本地 HIGH 问题；如果听感提到音色、咬字、句尾、力度，advice 至少要回应其中一个，把它变成一个动作
   （例如“咬字松散 → 先按歌词念一遍，字头咬紧再唱”）。
3. 不要给分、不要音名/调式/伴奏判断、不要新造测量数字；可以提“第 2–3 个音”这种位置。
4. advice 80 字以内；why 40 字以内；check 40 字以内（怎么算做到，不要写分数）。
只输出 JSON，不要 Markdown：
{{"advice":"...","why":"...","check":"..."}}"""

_ADVICE_BAD = re.compile(
    r"(?:"
    r"[A-Ga-g][#♯b♭]?\d"
    r"|大调|小调|调式|音名|音分|分贝|Hz|dB|评分|分数|跑调"
    r"|伴奏|声带|横膈膜"
    r"|\d{1,3}\s*分"
    r")",
    re.I,
)
_ADVICE_EMPTY = re.compile(r"^(多练|多听|加油|坚持|注意气息|放松一下|慢慢来)")


def _clean_advice(obj: dict) -> dict | None:
    advice = str(obj.get("advice") or "").strip()
    why = str(obj.get("why") or "").strip()
    check = str(obj.get("check") or "").strip()
    if not (8 <= len(advice) <= 120) or _ADVICE_BAD.search(advice) or _ADVICE_EMPTY.search(advice):
        return None
    # why/check 是辅助，不合格就丢弃，不让它们把整条建议拖垮。
    if len(why) > 80 or _ADVICE_BAD.search(why):
        why = ""
    if len(check) > 80 or _ADVICE_BAD.search(check):
        check = ""
    return {"advice": advice, "why": why, "check": check}


def _call_advice(key: str, observation: str, report: dict,
                 previous: str = "", force: bool = False) -> tuple[dict | None, dict, int, str]:
    prompt = ADVICE_PROMPT.format(
        evidence=_evidence_for_advice(report), observation=observation,
        previous=(f"上一版建议：{previous}。请换一个不同的练法，不要重复。"
                  if previous else "（无；这是本次第一次建议）"))
    raw, usage, latency = _post_qwen(
        key, [{"type": "text", "text": prompt}], max_tokens=320, json_mode=True,
        temperature=0.55 if force else 0.0)
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        obj = json.loads(m.group(0)) if m else {}
    except Exception:
        obj = {}
    clean = _clean_advice(obj) if isinstance(obj, dict) else None
    if not clean:
        return None, usage, latency, "千问没有给出符合约束的建议，这次先保留听感。"
    return clean, usage, latency, ""


def _post_qwen(key: str, content: list, *, max_tokens: int = 180,
               json_mode: bool = False, temperature: float = 0.0) -> tuple[str, dict, int]:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": (
                "你只输出合法 JSON，不要输出思考过程或 Markdown。"
                if json_mode else
                "你只输出 2 句中文听感，不要输出任何解释或格式。"
            )},
            {"role": "user", "content": content},
        ],
        "modalities": ["text"],
        "reasoning_effort": "none",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/chat/completions", data=data,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read()
    except Exception as e:  # noqa: BLE001
        raise AudioCoachError("连不上千问音频服务，稍后再试。", code="AUDIO_NETWORK") from e
    latency = int((time.time() - t0) * 1000)
    if status in (401, 403):
        raise AudioCoachError("千问音频 key 无效或没有权限。", code="AUDIO_AUTH", status=502)
    if status == 429:
        raise AudioCoachError("千问音频服务限流了，过一会儿再试。", code="AUDIO_RATE_LIMIT", status=429)
    if status != 200:
        raise AudioCoachError(f"千问音频服务返回 HTTP {status}。", code="AUDIO_UPSTREAM")
    try:
        obj = json.loads(raw.decode("utf-8"))
        msg = (obj.get("choices") or [{}])[0].get("message", {})
        text = msg.get("content") or ""
        usage = obj.get("usage") or {}
    except Exception as e:  # noqa: BLE001
        raise AudioCoachError("千问音频返回了无法解析的内容。", code="AUDIO_BAD_JSON") from e
    return text, usage, latency


def _call_qwen(key: str, wav: bytes, seconds: float,
               previous: str = "", force: bool = False) -> tuple[str, dict, int]:
    audio = base64.b64encode(wav).decode("ascii")
    prompt = PROMPT.format(seconds=seconds, retry="")
    if force:
        prompt = ("【重听要求】用户要求重新听一遍。"
                  + (f"上次你写的是：{previous}\n" if previous else "")
                  + "这次必须至少改写一半的措辞，并补充一个上次没写到的细节；"
                  + "不要输出和上次相同的句子。音色、力度、句尾三类仍要保留。\n\n"
                  + prompt)
    return _post_qwen(key, [
        {
            "type": "input_audio",
            "input_audio": {
                "data": f"data:audio/wav;base64,{audio}",
                "format": "wav",
            },
        },
        {"type": "text", "text": prompt},
    ], max_tokens=180, temperature=0.8 if force else 0.0)


def _row_result(row) -> dict:
    advice = None
    try:
        if row["advice_json"]:
            advice = json.loads(row["advice_json"])
    except Exception:
        advice = None
    return {
        "observation": row["observation"],
        "facets": _facets(row["observation"]),
        "advice": advice,
        "model": row["model"],
        "clip_start_ms": row["clip_start_ms"],
        "clip_end_ms": row["clip_end_ms"],
        "usage": json.loads(row["usage_json"] or "{}"),
        "advice_usage": json.loads(row["advice_usage_json"] or "{}"),
        "advice_latency_ms": row["advice_latency_ms"],
        "cached": True,
        "disclaimer": status()["disclaimer"],
    }


def listen(uid: str, report: dict, *, force: bool = False) -> dict:
    """返回听感 + 一条下一遍可执行的建议；不返回分数，也不改报告。

    `force=True`：跳过缓存并换一个角度重新问，供「重新问千问」使用。
    """
    ok, reason = available()
    if not ok:
        raise AudioCoachError(reason, code="AUDIO_COACH_OFF", status=409)
    key = _read_key()

    con = _db()
    try:
        row = con.execute(
            "SELECT * FROM audio_coach WHERE session_uid=? AND model=? AND prompt_version=?",
            (uid, MODEL, PROMPT_VERSION),
        ).fetchone()
    finally:
        con.close()
    prev_obs = row["observation"] if row else ""
    prev_advice = ""
    if row and row["advice_json"]:
        try:
            prev_advice = (json.loads(row["advice_json"]) or {}).get("advice") or ""
        except Exception:
            prev_advice = ""

    if row and not force:
        result = _row_result(row)
        if result["advice"]:
            return result
        # 听感已缓存但建议没生成（旧缓存/上次建议失败）：只补文本建议，不再听音频。
        try:
            advice, usage, latency, advice_error = _call_advice(
                key, result["observation"], report)
        except Exception as e:  # noqa: BLE001
            advice, usage, latency = None, {}, 0
            advice_error = "千问这次没给出可执行建议，先保留听感。"
            print(f"[SingLens] ⚠️ 千问建议生成失败（{uid}）：{e}")
        if advice:
            con = _db()
            try:
                con.execute(
                    """UPDATE audio_coach SET advice_json=?, advice_usage_json=?,
                       advice_latency_ms=? WHERE session_uid=?""",
                    (json.dumps(advice, ensure_ascii=False),
                     json.dumps(usage, ensure_ascii=False), latency, uid),
                )
                con.commit()
            finally:
                con.close()
        result["advice"] = advice
        result["advice_usage"] = usage
        result["advice_latency_ms"] = latency
        if not advice:
            result["advice_error"] = advice_error
        return result

    wav, start, end = _clip_wav(uid, report)
    text, usage, latency = _call_qwen(
        key, wav, end - start,
        previous=prev_obs, force=force)
    clean = _clean(text)
    if not clean:
        raise AudioCoachError(
            "千问这次没有给出符合约束的听感描述，已丢弃，避免把猜测写进报告。",
            code="AUDIO_UNUSABLE",
            status=422,
        )
    advice = None
    advice_usage = {}
    advice_latency = 0
    advice_error = ""
    try:
        advice, advice_usage, advice_latency, advice_error = _call_advice(
            key, clean, report, previous=prev_advice, force=force)
    except Exception as e:  # noqa: BLE001
        advice_error = "千问这次只给出了听感，建议稍后再试。"
        print(f"[SingLens] ⚠️ 千问建议生成失败（{uid}）：{e}")
    result = {
        "observation": clean,
        "facets": _facets(clean),
        "advice": advice,
        "model": MODEL,
        "clip_start_ms": int(round(start * 1000)),
        "clip_end_ms": int(round(end * 1000)),
        "usage": usage,
        "advice_usage": advice_usage,
        "latency_ms": latency,
        "advice_latency_ms": advice_latency,
        "cached": False,
        "disclaimer": status()["disclaimer"],
    }
    if not advice:
        result["advice_error"] = advice_error
    con = _db()
    try:
        con.execute(
            """INSERT OR REPLACE INTO audio_coach
               (session_uid, singer_id, model, prompt_version, clip_start_ms,
                clip_end_ms, observation, advice_json, usage_json,
                advice_usage_json, advice_latency_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, report.get("singer_id"), MODEL, PROMPT_VERSION,
             result["clip_start_ms"], result["clip_end_ms"], clean,
             json.dumps(advice, ensure_ascii=False) if advice else None,
             json.dumps(usage, ensure_ascii=False),
             json.dumps(advice_usage, ensure_ascii=False),
             advice_latency, time.time()),
        )
        con.commit()
    finally:
        con.close()
    return result
