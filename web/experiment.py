"""实验数据层与接口：采集 / 盲评 / 分析。

━━━ 为什么单独一个模块 ━━━
  ① **实验数据必须持久化** —— 会话可以放内存，但采了 30 条录音、
     收了 100 份评分之后进程一重启就没了，那是灾难。所以用 SQLite。
  ② 与 server.py 的业务逻辑分开，避免那个文件继续膨胀。

━━━ 三张表 ━━━
  exp_singer     演唱者 + 元数据（性别/声部/演唱经验/年龄段/设备）
  exp_recording  录音（关联 session_uid，六指标与总分从 ScoreReport 冗余一份）
  exp_rating     评分（rater_id × recording_id，1–5 分，唯一约束防重复）

━━━ 关于盲评 ━━━
  取待评列表时**必须随机顺序、隐藏演唱者身份与遍次**。
  如果让评分者看到文件名里的 "S01_第三遍"，会下意识觉得"后面录的应该更好"。
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
import uuid
from typing import Optional

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "experiment.db")

# 评分者名额上限（2026-09-24，审计 §3.2）。见 `issue_rater` 里对这个数字的说明：
# 它是"操作者可控的硬上限"，不是智能去重 —— 真的来了更多人时由人来调高。
# 环境变量 `EXP_MAX_RATERS` 可覆盖（采集当天人多了不用改代码）。
EXP_MAX_RATERS = int(os.environ.get("EXP_MAX_RATERS") or 20)

SCHEMA = """
CREATE TABLE IF NOT EXISTS exp_singer (
    singer_id     TEXT PRIMARY KEY,
    gender        TEXT,          -- 男/女/其他
    voice_part    TEXT,          -- 高/中/低/不确定
    experience    TEXT,          -- 自由文本，如 "3 年合唱团"
    exp_years     REAL,          -- 数值化，便于分层
    trained       INTEGER,       -- 0/1 是否受过训练  ← 最重要的协变量
    age_band      TEXT,
    device        TEXT,
    consent       TEXT,          -- 签字/口头
    note          TEXT,
    created_at    REAL
);

CREATE TABLE IF NOT EXISTS exp_recording (
    recording_id  TEXT PRIMARY KEY,
    singer_id     TEXT,
    session_uid   TEXT,          -- 关联分析会话
    take_index    INTEGER,       -- 第几遍
    mode          TEXT,          -- A 教材曲目 / B 跟唱
    reference_id  TEXT,
    duration_ms   INTEGER,
    wav_path      TEXT,          -- 相对 data/recordings/exp/
    total         REAL,
    pitch_score   REAL,
    rhythm_score  REAL,
    expr_score    REAL,
    -- 六指标（NULL 表示本次未评估，绝不用 0 代替）
    dynamic_range  REAL,
    phrase_dynamics REAL,
    rubato         REAL,
    vibrato_rate   REAL,
    vibrato_depth  REAL,
    onset_style    REAL,
    brightness     REAL,
    metrics_excluded TEXT,       -- JSON 数组
    quality_note  TEXT,          -- 质检结论
    condition     TEXT,          -- ★ 构造式样本的条件代号（FLAT-1 / DYN-2 / VIB-1 …）
                                 --   原方案（自然变异）留空；备用方案必须填
    -- ★ 整体移调量（半音）：演唱者比参考唱高/唱低了多少。
    --   音准分已经把移调扣掉了（analysis/align.py），这个字段是**单独报告**用的
    --   （论文要报"调性差异 vs 音准误差"时必须分开写）。
    --   降级时（EQUAL_DIVISION）为 NULL。
    pitch_offset_semitones REAL,
    -- ★ 模式 B 两遍制：这次练习是拿哪个**跟唱模板**当参考的。
    --   与 reference_id 的关系：模式 A 的 reference_id 是内置曲目号（REF_TWINKLE_1），
    --   模式 B 的 reference_id 就是 template_id（见 web/templates.py）。
    --   两列都留：reference_id 是统一口径（报告里就是这个），
    --   template_id 让"哪些练习用了这个模板"能直接查（模板失效时好追溯）。
    --   非模式 B 的练习为 NULL。
    template_id   TEXT,
    created_at    REAL,
    FOREIGN KEY (singer_id) REFERENCES exp_singer(singer_id)
);

CREATE TABLE IF NOT EXISTS exp_rating (
    rating_id     TEXT PRIMARY KEY,
    recording_id  TEXT,
    rater_id      TEXT,
    score         INTEGER,       -- 1-5
    comment       TEXT,
    created_at    REAL,
    UNIQUE (recording_id, rater_id)     -- 同一评分者对同一录音只能打一次
);

-- ★ 评分者登记表（2026-09-24，审计 §3.2）。
--   原来 `rater_id` 完全由客户端自报，于是"伪造任意多评分者"是一条命令的事：
--   同一段录音用 3 个编造的 id 各打一次分 → 全部 200，盲评的"评分者"维度被注水，
--   直接污染 Spearman / ICC。而这份数据是论文第 7 节的**原始数据**。
--   现在：评分者 id 必须**由服务端签发**（`POST /api/experiment/rater`），
--   并登记签发时间 / 来源 IP / User-Agent —— 事后能看出"同一个人开了 40 个号"。
--   ⚠️ 这**不是**账号体系，挡不住铁了心的攻击者；它做的是
--     **① 硬上限（超过 EXP_MAX_RATERS 直接拒）② 可追溯**。
--     真要有更强保证，得让操作者线下发放评分者编号。
CREATE TABLE IF NOT EXISTS exp_rater (
    rater_id      TEXT PRIMARY KEY,
    label         TEXT,          -- 操作者给的编号（如 "P07"），可空
    issued_at     REAL,
    first_ip      TEXT,
    user_agent    TEXT,
    n_rating      INTEGER DEFAULT 0
);
"""

METRICS = ["dynamic_range", "phrase_dynamics", "rubato", "vibrato_rate",
           "vibrato_depth", "onset_style", "brightness"]

# ── 轻量迁移 ────────────────────────────────────────────────
# ★ 为什么需要：SCHEMA 里用的是 `CREATE TABLE IF NOT EXISTS`，
#   **对已存在的表不会加列**。所以给 exp_recording 加列时，
#   老库（已经跑过采集的）不会自动获得新列，插入会静默少字段。
#
#   这里在 connect() 时对比 PRAGMA 与 SCHEMA 想要的列，缺哪个补哪个。
#   新加的列一律允许 NULL —— 老数据没有这个值，不能硬塞默认值。
#
# ⚠️ 列类型必须显式写出来。
#    踩过的坑：第一版 `_migrate` 把 ALTER 的列类型**硬编码成 REAL** ——
#    当时只加过一个数值列（pitch_offset_semitones），看着没问题。
#    后来要加 TEXT 列（template_id）就会写成 REAL。SQLite 是动态类型，
#    照样能塞字符串进去、也不报错，但 schema 从此对不上 SCHEMA 里的声明，
#    `PRAGMA table_info` 与文档不一致 —— 属于"不会报错的错"。
_MIGRATIONS = {
    "exp_recording": [("pitch_offset_semitones", "REAL"),
                      ("template_id", "TEXT")],
}


def _migrate(con):
    """给已存在的表补上 SCHEMA 里新增的列（幂等）。"""
    for table, wanted in _MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue                      # 表还没建，SCHEMA 会建好
        for col, coltype in wanted:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    con.commit()


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)                         # ★ 给老库补新列（幂等）
    return con


# ══════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════
def add_singer(con, meta: dict) -> str:
    sid = meta.get("singer_id") or f"S{con.execute('SELECT COUNT(*) c FROM exp_singer').fetchone()['c'] + 1:02d}"
    exp_txt = (meta.get("experience") or "").strip()
    # 从自由文本里粗提"年数"和"是否受过训练"，便于后续分层。
    # 采集时是自由文本（"3 年合唱团"），这里做一次尽力而为的解析，
    # 解析不出来就留 None —— 不要瞎猜。
    years, trained = _parse_experience(exp_txt)
    con.execute(
        "INSERT OR REPLACE INTO exp_singer VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, meta.get("gender"), meta.get("voice_part"), exp_txt, years, trained,
         meta.get("age_band"), meta.get("device"), meta.get("consent"),
         meta.get("note"), time.time()))
    con.commit()
    return sid


# ══════════════════════════════════════════════════════════════
# 入口校验（2026-09-24，审计 §5.1）
#
# ⚠️ 为什么这些"啰嗦"的小函数必须有：审计实测**十余处一击即 500** ——
#    `{"gender":{"x":1}}` → `'dict' object has no attribute 'strip'`、
#    `{"session_uid":{"a":1}}` → SQLite 绑定错误外泄、
#    `score:"abc"` → `invalid literal for int()`、
#    `{"experience":"\ud800"}` → UnicodeEncodeError。
#    它们的共同点：**类型错误不在入口挡，就会在下游以内部异常的形式炸出来**，
#    而那个异常字符串还会被回给客户端（等于免费送内部结构情报）。
#    判据很简单：**不可信输入在进入数据层之前必须已经是对的类型**。
# ══════════════════════════════════════════════════════════════
class BadInput(Exception):
    """入参不合法 —— 由各端点转成 400，不当作内部错误。"""


def s_field(payload: dict, key: str, *, required: bool = False, default=None,
            max_len: int = 200) -> Optional[str]:
    v = payload.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise BadInput(f"缺少必填字段 {key}")
        return default
    if not isinstance(v, str):
        raise BadInput(f"字段 {key} 必须是字符串（收到 {type(v).__name__}）")
    v = v.strip()
    if len(v) > max_len:
        raise BadInput(f"字段 {key} 太长（上限 {max_len} 字符）")
    # 代理对/孤立代理项（`"\ud800"`）会让 sqlite3 在编码时抛 UnicodeEncodeError。
    # 这里直接拒掉，而不是等它在写库那一刻炸成 500。
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise BadInput(f"字段 {key} 含非法字符（不能编码成 UTF-8）") from None
    return v


def i_field(payload: dict, key: str, *, required: bool = False, default=None,
            lo: int = None, hi: int = None) -> Optional[int]:
    v = payload.get(key)
    if v is None:
        if required:
            raise BadInput(f"缺少必填字段 {key}")
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        if isinstance(v, str) and v.strip().lstrip("+-").isdigit():
            v = int(v.strip())
        else:
            raise BadInput(f"字段 {key} 必须是整数（收到 {v!r}）")
    v = int(v)
    if lo is not None and v < lo or hi is not None and v > hi:
        raise BadInput(f"字段 {key} 超出范围（{lo}–{hi}），收到 {v}")
    return v


def f_field(payload: dict, key: str, *, default=None) -> Optional[float]:
    """有限浮点。`1e400` 会被 json 解析成 `inf` —— 那正是审计 §2.7 的入口。"""
    v = payload.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise BadInput(f"字段 {key} 必须是数字（收到 {type(v).__name__}）")
    f = float(v)
    if not math.isfinite(f):
        # 存进去只要一次，**导出接口就永久 500**（Out of range float values…）。
        raise BadInput(f"字段 {key} 必须是有限数字（NaN / 无穷大不接受）")
    if abs(f) > 1e9:
        raise BadInput(f"字段 {key} 的数值不合理（{f}）")
    return f


def _parse_experience(txt: str):
    import re
    if not txt:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*年", txt)
    years = float(m.group(1)) if m else 0.0
    # "训练"的判据：明确提到训练形式，或年数 > 0
    trained = 1 if (years and years > 0) or re.search(
        r"合唱|声乐|美声|民族|钢琴|考级|乐团|训练|老师|课", txt) else 0
    return years, trained


def _safe_wav_path(v) -> Optional[str]:
    """把客户端给的 `wav_path` 收成一个**纯文件名**；不合格返回 None。

    ⚠️ 为什么要在这里收：`POST /api/experiment/recording` 是**匿名可写**的，
    而它的 `wav_path` 会被 `GET /api/experiment/audio/{id}` 拼成路径去读文件。
    2026-09-23 的审计证明那条链可以读走**任意文件**（含整个 `experiment.db`）。

    宁可在这里丢掉（存 NULL，界面显示"文件丢了"），也不要存一个能穿越的路径 ——
    存进去之后，将来任何一个"读这个路径"的地方都得自己再防一遍，漏一个就出事。

    判据（全部要满足）：
      · 是字符串、非空、长度合理
      · `os.path.basename` 之后与原文相同（⇒ 不含任何目录分隔符）
      · 不含 `..`、不含盘符冒号、不含 NUL
      · 扩展名在白名单里（本来只有 wav/mp3/m4a 这类）
    """
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or len(s) > 200:
        return None
    if "\x00" in s or ":" in s or ".." in s:
        return None
    if s != os.path.basename(s):              # 含 / 或 \
        return None
    if not s.lower().endswith((".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus")):
        return None
    return s


def add_recording(con, rec: dict) -> str:
    """登记一条录音。

    ★ 三处硬校验（2026-09-24，审计 §2.7 / §3.2 / §5.4）：
      ① `recording_id` **不再接受客户端指定** —— 服务端生成。
         原来 `INSERT OR REPLACE` + 客户端自带 id ⇒ 可以**覆盖任意已有录音行**
         （审计 §5.7 标为"未实测"，因为会改动真实数据 —— 但代码路径是明确的）。
      ② `session_uid` 必须**真的存在**（库里有报告，或内存里有这次会话）。
         原来只检查"字段非空"，于是可以凭空插入一条录音、再往盲评池注入条目，
         让评分者去听攻击者指定的文件。
      ③ 所有数值字段必须是**有限数**：`1e400` 会被 json 解析成 `inf` 存进 SQLite，
         之后**导出接口永久 500**（`Out of range float values are not JSON compliant`）。
    """
    rid = f"R{uuid.uuid4().hex[:10]}"          # ★ 服务端生成，不用客户端给的
    # ★ wav_path 必须过白名单再入库（见 _safe_wav_path 的说明）。
    #   这是"不可信输入挡在数据之外"的那一层；读侧的 realpath 校验是兜底。
    wav_path = _safe_wav_path(rec.get("wav_path"))
    # 数值字段统一收敛：不合法就 400（BadInput），而不是写进去之后炸导出
    nums = {k: f_field(rec, k) for k in
            ("duration_ms", "total", "pitch_score", "rhythm_score", "expr_score",
             "pitch_offset_semitones", *METRICS)}
    cols = ["recording_id", "singer_id", "session_uid", "take_index", "mode",
            "reference_id", "duration_ms", "wav_path", "total", "pitch_score",
            "rhythm_score", "expr_score", *METRICS, "metrics_excluded",
            "quality_note", "condition", "pitch_offset_semitones", "template_id",
            "created_at"]
    vals = [rid, s_field(rec, "singer_id", max_len=64),
            s_field(rec, "session_uid", required=True, max_len=64),
            i_field(rec, "take_index", lo=0, hi=99),
            s_field(rec, "mode", max_len=4),
            s_field(rec, "reference_id", max_len=200),
            nums["duration_ms"], wav_path, nums["total"], nums["pitch_score"],
            nums["rhythm_score"], nums["expr_score"]]
    vals += [nums[k] for k in METRICS]
    import json
    vals += [json.dumps(rec.get("metrics_excluded") or [], ensure_ascii=False),
             s_field(rec, "quality_note", max_len=200),
             s_field(rec, "condition", max_len=40),
             nums["pitch_offset_semitones"],
             s_field(rec, "template_id", max_len=64),
             time.time()]
    con.execute(f"INSERT OR REPLACE INTO exp_recording ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", vals)
    con.commit()
    return rid


def session_known(session_uid: str) -> bool:
    """这个 session_uid 是不是**我们服务自己发出去的**。

    两种来源都算：
      · `practice_session` 表里有（练过、且已落库）
      · 内存里还有这次会话（刚录完还没落库）

    ⚠️ 为什么要查这个：审计 §3.2 实测"往盲评池注入条目"——
       匿名 POST 一条 `{"recording_id":"__AUDIT__pool","wav_path":"S01_take01.wav"}`
       就能让它出现在盲评列表里，评分者会被要求去听攻击者指定的文件。
       要求 `session_uid` 真实存在之后，要注入就得先真的走一遍录音流程
       （留下真实痕迹），不再是"一条 curl 的事"。
    """
    if not session_uid:
        return False
    try:
        from web import practice_store
        if practice_store.load_report(session_uid) is not None:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from web import server as _srv          # 延迟导入：避免循环 import
        return session_uid in _srv.SESSIONS
    except Exception:  # noqa: BLE001
        return False


def issue_rater(con, label: Optional[str], ip: str, ua: str) -> tuple[Optional[str], str]:
    """签发一个评分者 id。超过上限就拒绝（返回 (None, 原因)）。

    ★ 上限的意义：盲评的"评分者"维度**本身就是数据**（Spearman 靠它算）。
       一个人开 40 个号，等于凭空造出 40 个评分者。
       所以这里给一个**操作者可控的硬上限**（默认 20）：真的来了 25 个同学，
       操作者改 `EXP_MAX_RATERS` 或调 `POST /api/experiment/rater?raise=30`。
       ⚠️ 刻意不做得"聪明"（比如按 IP 去重）——
         同学们可能在同一个 WiFi/同一个出口 IP 下，按 IP 去重会**误杀真人**。
         宁可留一个需要人来判断的上限，也不要一个会悄悄吃掉数据的规则。
    """
    n = con.execute("SELECT COUNT(*) c FROM exp_rater").fetchone()["c"]
    if n >= EXP_MAX_RATERS:
        return None, (f"评分者名额已满（{n}/{EXP_MAX_RATERS}）。"
                      f"这通常说明有人在刷号 —— 确认人数后由操作者调高上限。")
    rid = f"RT{uuid.uuid4().hex[:8]}"
    con.execute("INSERT INTO exp_rater (rater_id,label,issued_at,first_ip,user_agent,n_rating)"
                " VALUES (?,?,?,?,?,0)", (rid, label, time.time(), ip, (ua or "")[:200]))
    con.commit()
    return rid, ""


def rater_known(con, rater_id: str) -> bool:
    if not rater_id:
        return False
    return con.execute("SELECT 1 FROM exp_rater WHERE rater_id=?",
                       (rater_id,)).fetchone() is not None


# ══════════════════════════════════════════════════════════════
# 操作者令牌（2026-09-24，审计 §3.2 的建议）
#
# 为什么需要它 —— 说清**它挡的是谁**：
#   部署形态是"主试的笔记本连教室 WiFi，同学的手机打开 /rate 评分"。
#   于是**同一个局域网里的任何人**都能 curl 这个服务。审计实测过：
#   匿名 POST 一条录音就能往盲评池注入条目、匿名就能改条件代号。
#   网络守卫挡的是**浏览器里的第三方页面**（跨站/rebinding），挡不住
#   "同一网段里直接用 curl 的人" —— 那一层要靠令牌。
#
# 分工（按角色，不是按端点）：
#   · **操作者**（主试，坐在服务器那台机器前）：采集、导出、看名单 ⇒ **要令牌**
#   · **评分者**（同学，手机）：取待评列表、听音频、提交评分 ⇒ 不要令牌，
#     但要**服务端签发的编号** + 名额上限 + 来源可追溯（见 exp_rater）
#
# 令牌怎么到操作者手里：启动时打印在控制台（还写了一份到
# `data/operator_token.txt`），控制台直接给一条**带令牌的链接**，
# 点开就是 /collect，页面把它存进 localStorage。
# ⚠️ 为什么不做成登录框：这个系统的前提是"本地、无账号"。
#    令牌只解决"同网段的陌生人"，不假装是账号体系。
# ══════════════════════════════════════════════════════════════
TOKEN_FILE = os.path.join(ROOT, "data", "operator_token.txt")
_OPERATOR_TOKEN: Optional[str] = None
# 允许不带令牌的开关（自动化脚本/审计用）。默认关。
# ⚠️ 只认环境变量，不认 URL 参数 —— URL 会进日志、进浏览器历史。
DISABLE_TOKEN = os.environ.get("SINGLENS_NO_OPERATOR_TOKEN") == "1"


def operator_token() -> str:
    """取（必要时生成）操作者令牌。"""
    global _OPERATOR_TOKEN
    if _OPERATOR_TOKEN:
        return _OPERATOR_TOKEN
    try:
        if os.path.exists(TOKEN_FILE):
            t = open(TOKEN_FILE, encoding="utf-8").read().strip()
            if len(t) >= 8:
                _OPERATOR_TOKEN = t
                return t
    except OSError:
        pass
    t = uuid.uuid4().hex[:16]
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(t)
    except OSError:
        pass
    _OPERATOR_TOKEN = t
    return t


def token_ok(request) -> bool:
    """请求里带没带正确的操作者令牌。三种带法都接受。"""
    if DISABLE_TOKEN:
        return True
    want = operator_token()
    got = (request.headers.get("x-operator-token")
           or request.query_params.get("token")
           or "")
    if not got:
        # 也接受 cookie：页面第一次带 ?token= 打开后由服务端种一个，
        # 这样**页面内的 fetch 不用改**（老页面也能继续用）。
        got = request.cookies.get("sl_op") or ""
    return bool(got) and got == want


def require_operator(request):
    """操作者专用端点用这个挡一道。返回 None = 放行，否则返回 401 响应。"""
    if token_ok(request):
        return None
    return _fail("UNAUTHORIZED",
                 "这个接口只给操作者（主试）用，需要操作者令牌。"
                 "打开服务时控制台会打印一条带令牌的链接，用它打开采集页即可；"
                 "令牌也存在 data/operator_token.txt。", 401)



def add_rating(con, recording_id: str, rater_id: str, score: int,
               comment: str = "") -> tuple[bool, str]:
    """记一条评分。

    ★ 三处校验（2026-09-24，审计 §3.2）：
      ① 录音**必须存在** —— 原来给一个不存在的 id 打分也返回 200，
         于是 `exp_rating` 里出现"孤儿评分"，而它对任何分析都是噪声。
      ② 评分者**必须是服务端签发的**（见 `exp_rater`）——
         原来 `rater_id` 自报，可以无限注水"评分者"这一维。
      ③ 分数范围与类型在入口挡（`i_field`）——
         原来 `score:"abc"` / `score:null` 都是 500。
    """
    if not recording_id:
        return False, "缺少 recording_id"
    if con.execute("SELECT 1 FROM exp_recording WHERE recording_id=?",
                   (recording_id,)).fetchone() is None:
        return False, f"录音 {recording_id} 不存在（不能给不存在的录音打分）"
    if not rater_known(con, rater_id):
        return False, ("评分者编号无效。请用 /rate 页面（它会向服务端登记一个编号）——"
                       "自报的编号一律不接受，否则评分者维度可以被随意注水。")
    if not (1 <= int(score) <= 5):
        return False, "分数必须在 1–5 之间"
    try:
        con.execute("INSERT INTO exp_rating VALUES (?,?,?,?,?,?)",
                    (f"RT{uuid.uuid4().hex[:10]}", recording_id, rater_id,
                     int(score), comment, time.time()))
        con.execute("UPDATE exp_rater SET n_rating=n_rating+1 WHERE rater_id=?",
                    (rater_id,))
        con.commit()
        return True, ""
    except sqlite3.IntegrityError:
        return False, "你已经给这条录音打过分了"


# ══════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════
def list_singers(con):
    return [dict(r) for r in con.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM exp_recording r WHERE r.singer_id=s.singer_id) "
        "AS n_rec FROM exp_singer s ORDER BY s.singer_id")]


def list_recordings(con, singer_id: Optional[str] = None):
    q = "SELECT * FROM exp_recording"
    a = ()
    if singer_id:
        q += " WHERE singer_id=?"
        a = (singer_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY singer_id, take_index", a)]


def rating_matrix(con):
    """返回 (recording_ids, rater_ids, 评分矩阵，缺项为 nan)。"""
    rows = list(con.execute("SELECT recording_id, rater_id, score FROM exp_rating"))
    recs = sorted({r["recording_id"] for r in rows})
    raters = sorted({r["rater_id"] for r in rows})
    M = np.full((len(recs), len(raters)), np.nan)
    ri = {r: i for i, r in enumerate(recs)}
    qi = {r: i for i, r in enumerate(raters)}
    for r in rows:
        M[ri[r["recording_id"]], qi[r["rater_id"]]] = r["score"]
    return recs, raters, M


def progress(con) -> dict:
    """采集进度：用来判断还差多少人/多少条。"""
    n_s = con.execute("SELECT COUNT(*) c FROM exp_singer").fetchone()["c"]
    n_r = con.execute("SELECT COUNT(*) c FROM exp_recording").fetchone()["c"]
    n_rate = con.execute("SELECT COUNT(*) c FROM exp_rating").fetchone()["c"]
    g = {r["gender"]: r["c"] for r in con.execute(
        "SELECT gender, COUNT(*) c FROM exp_singer GROUP BY gender")}
    t = {r["trained"]: r["c"] for r in con.execute(
        "SELECT trained, COUNT(*) c FROM exp_singer GROUP BY trained")}
    # 目标与底线（见 docs/实验方案与知情同意书.md）
    return {
        "singers": n_s, "recordings": n_r, "ratings": n_rate,
        "by_gender": g, "by_trained": t,
        "target": {"singers": 10, "recordings": 30, "raters": 4},
        "floor": {"singers": 8, "recordings": 24, "raters": 2},
        "checks": {
            "演唱者达标": n_s >= 10,
            "演唱者达底线": n_s >= 8,
            "男女至少各 3（底线）": g.get("男", 0) >= 3 and g.get("女", 0) >= 3,
            "男女各 5（目标）": g.get("男", 0) >= 5 and g.get("女", 0) >= 5,
            "受训与未受训都有（关键）": (t.get(1, 0) >= 1 and t.get(0, 0) >= 1),
            "录音条数达标": n_r >= 30,
        },
    }


# ══════════════════════════════════════════════════════════════
# HTTP 接口（挂到 server.py 的 /api/experiment/* 下）
# ══════════════════════════════════════════════════════════════
from fastapi import APIRouter, Body, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

router = APIRouter(prefix="/api/experiment", tags=["experiment"])


def _ok(d=None):
    return {"ok": True, "data": d}


def _fail(code, msg, status=400):
    return JSONResponse(status_code=status,
                        content={"ok": False, "error": {"code": code, "message": msg}})


def _client_meta(request) -> tuple[str, str]:
    """来源 IP / User-Agent —— 只用于**事后追溯**（谁签发的评分者、从哪来）。

    ⚠️ 不做任何"按 IP 拦截"的判断：同学们常在同一个 WiFi、同一个出口 IP 下，
       按 IP 去重会**误杀真人**。这里只是把事实记下来。
    """
    ip = ""
    try:
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
             or (request.client.host if request.client else "")
    except Exception:  # noqa: BLE001
        ip = ""
    ua = ""
    try:
        ua = request.headers.get("user-agent") or ""
    except Exception:  # noqa: BLE001
        ua = ""
    return ip[:64], ua[:200]


@router.post("/singer")
async def api_add_singer(request: Request, payload: dict = Body(...)):
    """登记一位演唱者（含元数据）。

    ⚠️ 字段类型**在入口挡**（2026-09-24，审计 §5.1）：
       原来 `{"gender":{"x":1}}` → 500 `'dict' object has no attribute 'strip'`。
    ⚠️ **操作者专用**：登录实验参与者是主试的动作（见 require_operator）。
    """
    deny = require_operator(request)
    if deny:
        return deny
    try:
        gender = s_field(payload, "gender", required=True, max_len=40)
        experience = s_field(payload, "experience", required=True, max_len=200)
        clean = dict(payload)
        clean.update({
            "gender": gender, "experience": experience,
            "voice_part": s_field(payload, "voice_part", max_len=40),
            "age_band": s_field(payload, "age_band", max_len=40),
            "device": s_field(payload, "device", max_len=120),
            "note": s_field(payload, "note", max_len=400),
            "consent": payload.get("consent"),
        })
    except BadInput as e:
        return _fail("BAD_REQUEST", str(e))
    con = connect()
    try:
        sid = add_singer(con, clean)
        return _ok({"singer_id": sid, "progress": progress(con)})
    finally:
        con.close()


@router.get("/progress")
async def api_progress():
    """采集进度 + 达标检查。用来判断还差多少人/多少条。"""
    con = connect()
    try:
        return _ok(progress(con))
    finally:
        con.close()


@router.get("/singers")
async def api_singers(request: Request):
    """参与者名单（含性别/声部/经验/consent 备注）—— **操作者专用**。

    ⚠️ 这是审计 §3.1 点名的身份数据：一次性导出全部演唱者元数据。
       同学不需要它（盲评页故意不返回演唱者身份）。
    """
    deny = require_operator(request)
    if deny:
        return deny
    con = connect()
    try:
        return _ok({"singers": list_singers(con), "progress": progress(con)})
    finally:
        con.close()


@router.post("/rater")
async def api_issue_rater(request: Request, label: str = ""):
    """签发一个评分者编号（`/rate` 页面打开时自己调一次）。

    ★ 为什么要有这一步（审计 §3.2）：盲评的"评分者"维度是**数据本身**，
       而原来它由客户端自报 ⇒ 一个人可以凭空造出 40 个评分者。
       现在编号由服务端签发、登记签发时间与来源，并受 `EXP_MAX_RATERS` 上限约束。
    """
    ip, ua = _client_meta(request)
    lab = ""
    try:
        lab = (label or "").strip()[:40]
    except Exception:  # noqa: BLE001
        lab = ""
    con = connect()
    try:
        rid, why = issue_rater(con, lab or None, ip, ua)
        if not rid:
            return _fail("CONFLICT", why, 409)
        return _ok({"rater_id": rid, "max_raters": EXP_MAX_RATERS,
                    "progress": progress(con)})
    finally:
        con.close()


@router.get("/raters")
async def api_raters(request: Request):
    """已签发的评分者（操作者用来核对人数是否合理）—— **操作者专用**。

    ⚠️ 里面有 `first_ip`：来源地址属于隐私，不该给评分者自己看。
    """
    deny = require_operator(request)
    if deny:
        return deny
    con = connect()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT rater_id,label,issued_at,first_ip,n_rating FROM exp_rater"
            " ORDER BY issued_at")]
        return _ok({"raters": rows, "count": len(rows), "max": EXP_MAX_RATERS})
    finally:
        con.close()


@router.post("/recording")
async def api_add_recording(request: Request, payload: dict = Body(...)):
    """登记一条录音。通常由采集页在分析完成后自动调用。

    ⚠️ **操作者专用**（审计 §3.2：匿名可往盲评池注入条目）。
    ⚠️ 这里只做**入口校验**，真正的写入规则在 `add_recording()` 里
       （服务端生成 id、session_uid 必须真实存在、数值必须有限）。
    """
    deny = require_operator(request)
    if deny:
        return deny
    try:
        suid = s_field(payload, "session_uid", required=True, max_len=64)
    except BadInput as e:
        return _fail("BAD_REQUEST", str(e))
    if not session_known(suid):
        return _fail("NOT_FOUND",
                     f"会话 {suid} 不是这台服务发出来的（或已经过期）。"
                     f"录音只能登记真实的练习会话 —— 这一条挡的是"
                     f"「凭空往盲评池注入条目」。", 404)
    con = connect()
    try:
        rid = add_recording(con, payload)
        return _ok({"recording_id": rid, "progress": progress(con)})
    except BadInput as e:
        return _fail("BAD_REQUEST", str(e))
    finally:
        con.close()


@router.get("/rate/list")
async def api_rate_list(rater_id: str = "", shuffle_seed: str = ""):
    """取待评列表。

    ★ 盲评要求：
      · **随机顺序**（按 rater_id 播种，同一个人每次刷新顺序一致，不同人不同）
      · **不返回演唱者身份、不返回第几遍** —— 只给录音 id 和音频地址
    """
    import json
    import random
    con = connect()
    try:
        recs = list_recordings(con)
        done = {r["recording_id"] for r in con.execute(
            "SELECT recording_id FROM exp_rating WHERE rater_id=?", (rater_id,))}
        pool = [r for r in recs if r["wav_path"]]
        rnd = random.Random(shuffle_seed or rater_id or "default")
        rnd.shuffle(pool)
        items = [{
            "recording_id": r["recording_id"],
            "audio_url": f"/api/experiment/audio/{r['recording_id']}",
            "duration_ms": r["duration_ms"],
            "rated": r["recording_id"] in done,
            # ⚠️ 故意不返回 singer_id / take_index / 任何分数
        } for r in pool]
        return _ok({"rater_id": rater_id, "total": len(items),
                    "rated": len(done), "items": items})
    finally:
        con.close()


@router.get("/audio/{recording_id}")
async def api_audio(recording_id: str):
    """返回录音音频（盲评用）。只暴露录音 id，不暴露演唱者。

    ⚠️⚠️ **这里曾经是一个任意文件读取（P0）**，2026-09-23 由另一条会话的审计查出、
    我复核确认（`experiments/_verify_audit_findings.py`），当天修掉。

    原代码只有：
        p = os.path.join(ROOT, "data", "recordings", "exp", row["wav_path"])
        if not os.path.exists(p): 404
        return FileResponse(p, ...)
    而 `wav_path` 来自**请求体**（`POST /api/experiment/recording` 只校验了
    `session_uid` 存在，其余字段原样入库）。两个后果：
      · `os.path.join` 遇到**绝对路径**会丢弃前面的前缀 ⇒ `C:/Windows/win.ini` 直接读
      · `..\` 也能穿越 ⇒ `../../experiment.db` 把**整个数据库**吐出来
        （实测 1683456 字节、SQLite 魔数开头 —— 里面有全体用户的练习报告与模板）

    修法两层，**缺一不可**：
      ① 读的时候兜底（下面）—— realpath 之后必须仍在 exp 目录内
      ② 写的时候不再相信客户端（见 `_safe_wav_path`）—— 更根本
    只做①也能堵住，但②才是"把不可信输入挡在数据之外"。
    """
    from fastapi.responses import FileResponse
    con = connect()
    try:
        row = con.execute("SELECT wav_path FROM exp_recording WHERE recording_id=?",
                          (recording_id,)).fetchone()
    finally:
        con.close()
    if not row or not row["wav_path"]:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "录音不存在"})

    exp_root = os.path.realpath(os.path.join(ROOT, "data", "recordings", "exp"))
    p = os.path.realpath(os.path.join(exp_root, row["wav_path"]))
    # ⚠️ 判据必须用 realpath 之后再比前缀：软链接/`..`/大小写都会骗过字符串比较。
    #    并且要 `exp_root + os.sep`，否则 `/data/recordings/expX/…` 这种同前缀目录会被放行。
    inside = (p == exp_root) or p.startswith(exp_root + os.sep)
    if not inside or not os.path.isfile(p):
        # 不区分"越界"与"不存在" —— 告诉攻击者"这个路径存在但被拦了"没有好处
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "录音不存在"})
    return FileResponse(p, media_type="audio/wav")


@router.post("/rate")
async def api_rate(payload: dict = Body(...)):
    """提交一条评分。

    ⚠️ 类型/范围在入口挡（2026-09-24，审计 §5.1）：
       原来 `score:"abc"` → 500 `invalid literal for int()`、
       `score:null` → 500 `int() argument must be…`。
    """
    try:
        rec_id = s_field(payload, "recording_id", required=True, max_len=64)
        rater = s_field(payload, "rater_id", required=True, max_len=64)
        score = i_field(payload, "score", required=True, lo=1, hi=5)
        comment = s_field(payload, "comment", max_len=400, default="")
    except BadInput as e:
        return _fail("BAD_REQUEST", str(e))
    con = connect()
    try:
        okk, msg = add_rating(con, rec_id, rater, score, comment or "")
        if not okk:
            return _fail("BAD_REQUEST", msg)
        return _ok({"progress": progress(con)})
    finally:
        con.close()


# 构造式方案的 10 个条件代号（见 docs/备用实验方案-构造式单人样本.md §2.1）
CONDITION_CODES = ["FLAT-1", "FLAT-2", "DYN-1", "DYN-2", "VIB-1", "VIB-2",
                   "RUB-1", "RUB-2", "ART-1", "ART-2"]


@router.post("/set-condition")
async def api_set_condition(request: Request, payload: dict = Body(...)):
    """给已有录音补/改条件代号。

    ★ 为什么需要这个：录音是**边录边攒**的 —— 先录下来，之后再决定哪条是什么条件。
      如果只能在导入时指定，录到一半发现文件名写错了就得整批重来。

    ⚠️ **操作者专用**：条件代号是论文的自变量，只有主试能定。
    ⚠️ 校验（2026-09-24，审计 §5.1/§3.2）：
       · `condition` 必须是字符串（原来传 123 → 500 `'int' object has no attribute 'strip'`）
       · 长度上限，且**必须是已知条件代号之一**（原来任意字符串都能写进去，
         于是"条件"这一列可以被写成任何东西，而它是论文的自变量）
    """
    deny = require_operator(request)
    if deny:
        return deny
    try:
        rec_id = s_field(payload, "recording_id", required=True, max_len=64)
        raw = s_field(payload, "condition", max_len=40, default="")
    except BadInput as e:
        return _fail("BAD_REQUEST", str(e))
    cond = (raw or "").strip().upper() or None
    if cond is not None and cond not in CONDITION_CODES:
        return _fail("BAD_REQUEST",
                     f"条件代号必须是这 10 个之一：{', '.join(CONDITION_CODES)}"
                     f"（收到 {cond!r}）—— 这一列是论文的自变量，不能是任意字符串。")
    con = connect()
    try:
        cur = con.execute("UPDATE exp_recording SET condition=? WHERE recording_id=?",
                          (cond, rec_id))
        con.commit()
        if cur.rowcount == 0:
            return _fail("NOT_FOUND", f"找不到录音 {rec_id}", 404)
        return _ok({"recording_id": rec_id, "condition": cond})
    finally:
        con.close()


@router.get("/recordings")
async def api_recordings(request: Request, singer_id: str = ""):
    """列出录音（含条件代号）。用于"边录边攒"时检查还缺哪几条。

    ⚠️ 这属于实验原始数据（singer_id / 条件代号 / 分数 / wav_path），
    必须走操作者令牌；不能像 `/rate/list` 那样匿名返回。
    """
    deny = require_operator(request)
    if deny:
        return deny
    con = connect()
    try:
        recs = list_recordings(con, singer_id or None)
        have = {r["condition"] for r in recs if r.get("condition")}
        return _ok({
            "recordings": recs,
            "with_condition": len(have),
            "missing": sorted(set(CONDITION_CODES) - have),
            "unlabeled": [r["recording_id"] for r in recs if not r.get("condition")],
            "required": CONDITION_CODES,
        })
    finally:
        con.close()


def _finite(v):
    """把非有限浮点收成 None。**导出前的最后一道兜底**（审计 §2.7）。

    ⚠️ 为什么写侧已经校验了还要有这一道：
       库里可能**已经**躺着 `inf`（这次修之前写进去的），而只要有一行，
       `json.dumps` / `JSONResponse` 就会整体抛
       `Out of range float values are not JSON compliant` ⇒
       **导出接口永久 500**，整个实验数据取不出来。
       修数据要另说，但导出不该被一行脏数据拖死。
    """
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _clean_row(d: dict) -> dict:
    return {k: _finite(v) for k, v in d.items()}


@router.get("/export")
async def api_export(request: Request):
    """导出原始数据（录音 + 评分矩阵），便于外部复算。

    ⚠️ **操作者专用**（审计 §3.1 点名："一次性导出全部演唱者元数据
       （性别/声部/经验/consent/备注）+ 全部录音 + 评分矩阵"）。
    """
    deny = require_operator(request)
    if deny:
        return deny
    con = connect()
    try:
        recs, raters, M = rating_matrix(con)
        n_bad = 0
        for r in list_recordings(con):
            for v in r.values():
                if isinstance(v, float) and not math.isfinite(v):
                    n_bad += 1
        return _ok({
            "singers": [_clean_row(r) for r in list_singers(con)],
            "recordings": [_clean_row(r) for r in list_recordings(con)],
            "raters": raters,
            "rating_matrix": [[None if np.isnan(x) else int(x) for x in row] for row in M],
            "recording_order": recs,
            "progress": progress(con),
            # 有脏数据就**说出来**，不要悄悄修好让操作者以为数据是干净的
            "non_finite_values": n_bad,
        })
    finally:
        con.close()

