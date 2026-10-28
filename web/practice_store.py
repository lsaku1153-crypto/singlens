"""练习记录持久化 —— **让这个系统能真的当系统用**。

━━━ 为什么必须有这个模块 ━━━

2026-09-23 作者明确要求：**做的是系统，不是给答辩看的展示**。
按这个判据做了一次审计（`experiments/audit_product_ready.py`），
查出两个真实用户一定会撞上的硬缺口，这是其中之一：

    ❌ 「练习记录落进 SQLite」 —— 服务重启后
       成长档案 / 进步曲线 / 历史记录 **全部消失**

因为记录只存在 `web/server.py` 的 `SINGERS` 内存字典里。
真实用户不会一次把所有练习做完，关掉服务再打开是很正常的事 ——
而成长档案的基本承诺就是"每次练完都会存下来"。

> ⚠️ 为什么一直没被发现：我每次验证前端都先跑 `seed_frontend_data.py` 造数据。
> 那个脚本把数据"补"上了，于是**重启即失忆**这件事被完美地盖住了。
> 这正是"有人补台"和"独立可用"的差别。

━━━ 与 `exp_recording` 的关系 ━━━

**故意分表，不复用。**

  · `exp_recording` 是**实验样本**：有 `take_index` / `condition` /
    `quality_note`，还要落一份 wav 供盲评播放。它的用途是论文分析。
  · `practice_session` 是**产品记录**：用户练一次就写一条，字段跟着 UI 需要走。

把它们合成一张表的话，日常练习会污染实验数据（"采了几条"的统计、
盲评待评列表都会被灌进无关记录）。分开之后两边各自演化，互不牵连。

⚠️ 但**同一个数据库文件**（`data/experiment.db`）：
  这样"清空所有录音与记录"一个事务就能覆盖全部用户数据，
  不必去猜还有没有第二个库里存着东西。

━━━ `report_json` 与冗余列 ━━━

两者都存，不是冗余失误：
  · **冗余列**（total / pitch / rhythm / expr / 六个指标）给列表与曲线用 ——
    进步曲线要读几十次，不该每次都去 JSON 里挖。
  · **`report_json`** 给"回看某一次的完整报告"用。裁掉它的话，
    历史里的报告只剩几个数字，`segments` / `evidence` / `problems` 就没了，
    用户点开旧记录会看到一份**残缺的报告** —— 而 UI 上完全看不出来，
    只是"图少了一张"。这种缺法最难发现。
  · **`normalized_json`**（2026-10-01，G3）只装六维归一化值。它是
    `/progress` 与 `/profile` **每次都要**的那一小块，却埋在整份
    `report_json` 里 —— 取它就得把几十 KB 的 segments/evidence 一起解出来。
    更要紧的是：`_row_to_session` 从冗余列拼出来的 `expressiveness`
    **没有 `normalized`**，于是服务重启后第 05 屏的"强项与短板"六根横条
    会全部变成「—」—— 而页面上看不出异常，只是"数据没了"。
    （这条是 G3 动手时发现的：以前没暴露，是因为 `/profile` 只读内存。）
  · 同理：`report_json` 是"这一条记录的全貌"，`normalized_json` 是"画图要的那一列"。

━━━ `app_singer`：产品侧的演唱者名册（G3）━━━

**故意不复用 `exp_singer`。**（§58.1①，与上面分表是同一条理由）
  · `exp_singer` 是**实验样本**：性别 / 声部 / 演唱经验 / 同意书 / `trained`
    这个最重要的协变量。它回答的是"我采了几个实验样本"，**这个数字会进论文**。
  · 产品侧"我叫某某"写进去 ⇒ 论文里的样本量虚高，"10 个参与者"变成
    "10 个参与者 + 几个随手建的账号"，而且**没有任何字段能把两者分开**。

⚠️ 为什么名册必须有（而不是继续只看 `practice_session.singer_id`）：
  改名要落在**没有练习记录的人身上**（新建了还没练的人也得能出现在列表里），
  删除要知道"这个人本来叫什么"才能如实回报删了什么。
  这两件事都无法从练习记录里推出来。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "experiment.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_session (
    session_uid       TEXT PRIMARY KEY,
    singer_id         TEXT,
    mode              TEXT,          -- A 教材曲目 / B 跟唱模板 / C 上传
    reference_id      TEXT,
    reference_title   TEXT,          -- 给人看的曲名（模板号对用户没意义）
    template_id       TEXT,          -- 模式 B 用的模板（可追溯）
    retry_of_uid      TEXT,          -- 复练：跟哪一次比
    created_at        REAL,          -- epoch；接口层负责格式化成 ISO
    duration_ms       INTEGER,
    -- 三大分量 + 总分。NULL = 本次未评估，**绝不用 0 代替**（契约 §3.2）
    total             REAL,
    pitch_score       REAL,
    rhythm_score      REAL,
    expr_score        REAL,
    pitch_offset_semitones REAL,     -- 整体移调（半音）
    tempo_ratio       REAL,          -- 整体时长比（>1 = 比参考慢）
    self_reference    INTEGER,       -- 1 = 本次是自比，音准/节奏未评估
    -- 六指标冗余（表现力细节，成长档案/画像要用）
    dynamic_range     REAL,
    phrase_dynamics   REAL,
    rubato            REAL,
    vibrato_rate      REAL,
    vibrato_depth     REAL,
    onset_style       REAL,
    brightness        REAL,
    metrics_excluded  TEXT,          -- JSON 数组
    normalized_json   TEXT,          -- 六维归一化值（画图最常读的那一小块）
    report_json       TEXT           -- 完整 ScoreReport（回看旧记录用）
);
CREATE INDEX IF NOT EXISTS idx_practice_singer
    ON practice_session(singer_id, created_at);

-- 产品侧的演唱者名册（G3）。**不是** exp_singer，理由见文件头。
CREATE TABLE IF NOT EXISTS app_singer (
    singer_id   TEXT PRIMARY KEY,
    name        TEXT,
    created_at  REAL
);

-- 用户自述画像 + 明确说“记住”的偏好（P0，2026-10-26）。
-- 只存用户自己填写/明确保存的内容；不存聊天全文、不存推断结论。
CREATE TABLE IF NOT EXISTS app_profile (
    singer_id       TEXT PRIMARY KEY,
    goal            TEXT,
    preferred_json  TEXT,          -- ["克制抒情","气声"]
    dislikes_json   TEXT,          -- ["不想降调"]
    experience      TEXT,
    favorite_json   TEXT,          -- ["演员","其实"]
    notes_json      TEXT,          -- 用户点“记住这条”保存的短句
    comfort_json    TEXT,          -- 舒适音区自测结果（主观，不是声域）
    updated_at      REAL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _upgrade(con)
    return con


# `CREATE TABLE IF NOT EXISTS` **不会给已存在的表加列** —— 老库（160 条真实记录）
# 里没有 normalized_json，只有 report_json。所以列要单独补，并且补完要回填。
_SCHEMA_UPGRADED = False


def _upgrade(con: sqlite3.Connection) -> None:
    """把老库升到当前 schema（幂等）。进程内只跑一次。

    ⚠️ 为什么要有这一步、而不是改 SCHEMA 就完事：
       `connect()` 里的 `executescript(SCHEMA)` 只做 `CREATE ... IF NOT EXISTS`，
       对**已经存在的表**它一个字都不会改。实测：改完新增列之后，
       老库上每次写 `normalized_json` 都会 `no such column` ——
       而错误发生在**保存练习记录**这条路上，用户看到的是"报告出来了但历史里没有"。
    """
    global _SCHEMA_UPGRADED
    if _SCHEMA_UPGRADED:
        return
    cols = {r["name"] for r in con.execute("PRAGMA table_info(practice_session)")}
    if "normalized_json" not in cols:
        con.execute("ALTER TABLE practice_session ADD COLUMN normalized_json TEXT")
        # 回填老行：它们只有 report_json，不补的话"重启后六维横条全空"
        # 会一直留着（新写的行有列，老行没有 —— 界面上完全看不出来是哪一类）。
        rows = con.execute(
            "SELECT session_uid, report_json FROM practice_session"
            " WHERE normalized_json IS NULL AND report_json IS NOT NULL").fetchall()
        for r in rows:
            try:
                rep = json.loads(r["report_json"])
                norm = ((rep.get("expressiveness") or {}).get("normalized")) or {}
            except (ValueError, TypeError, AttributeError):
                norm = {}
            con.execute("UPDATE practice_session SET normalized_json=? WHERE session_uid=?",
                        (json.dumps(norm, ensure_ascii=False), r["session_uid"]))
        con.commit()
        if rows:
            print(f"[SingLens] practice_session 补 normalized_json 列并回填 {len(rows)} 行")
    # 2026-10-27：舒适音区自测增加 comfort_json；老库的 app_profile 要补列。
    pcols = {r["name"] for r in con.execute("PRAGMA table_info(app_profile)")}
    if pcols and "comfort_json" not in pcols:
        con.execute("ALTER TABLE app_profile ADD COLUMN comfort_json TEXT")
        con.commit()
    _SCHEMA_UPGRADED = True


def default_name(singer_id: str) -> str:
    """自动登记时给的名字。

    §58.2 写的是默认名「我」。这里给 `我（S01）` —— 是**有意的偏离**：
    本机库里现有 9 位演唱者（真实状态：S01 / singer_upload / TPLC / AUDIT …）。
    如果 9 行全叫「我」，切换列表就成了一张没法选的表 —— 用户看不出哪一行是
    自己的记录，切错了就是**别人的数据**（正是这个功能要消灭的那种事）。
    带上 id 至少能对上练习记录里那个 id。
    """
    return f"我（{singer_id}）"


def name_taken(name: str, exclude: str = "") -> bool:
    """是否已经有一个**另一位**演唱者叫这个名字（`exclude` = 改名时排除自己）。

    ⚠️ 重名不拦的话，"切换演唱者"这个动作就没有确定性：列表里两个「同桌」，
       用户点第二个看到的可能是第一个的数据 —— 而这恰恰又是"看不出在查谁"。
       所以重名显式拒绝（接口层 400），而不是默默允许。
    """
    con = connect()
    try:
        row = con.execute("SELECT singer_id FROM app_singer WHERE name=? AND singer_id<>?",
                          (name, exclude)).fetchone()
    finally:
        con.close()
    return row is not None


# ────────────────────────────────────────────────────────────
# 写入
# ────────────────────────────────────────────────────────────
def save(report: dict, *, singer_id: str, session_uid: str, mode: str,
         reference_id: Optional[str] = None, reference_title: Optional[str] = None,
         template_id: Optional[str] = None, retry_of_uid: Optional[str] = None,
         created_at: Optional[float] = None) -> None:
    """把一份 ScoreReport 落库（幂等：同一 session_uid 重复写会覆盖）。

    ⚠️ 用 INSERT OR REPLACE 而不是 INSERT：重算/复评同一次会话时
       不该炸成 "UNIQUE constraint failed"，那是内部状态，不是用户的事。
    """
    r = report or {}
    p = r.get("pitch") or {}
    rh = r.get("rhythm") or {}
    ex = r.get("expressiveness") or {}
    meta = r.get("meta") or {}

    def g(d, k):
        v = (d or {}).get(k)
        return None if v is None else float(v)

    con = connect()
    try:
        con.execute(
            "INSERT OR REPLACE INTO practice_session ("
            " session_uid, singer_id, mode, reference_id, reference_title,"
            " template_id, retry_of_uid, created_at, duration_ms, total,"
            " pitch_score, rhythm_score, expr_score, pitch_offset_semitones,"
            " tempo_ratio, self_reference, dynamic_range, phrase_dynamics,"
            " rubato, vibrato_rate, vibrato_depth, onset_style, brightness,"
            " metrics_excluded, normalized_json, report_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_uid, singer_id, mode,
             reference_id or meta.get("reference_id"),
             reference_title or meta.get("reference_title"),
             template_id, retry_of_uid,
             float(created_at if created_at is not None else time.time()),
             (meta.get("duration_ms") if meta.get("duration_ms") is not None else None),
             g(r, "total"), g(p, "score"), g(rh, "score"), g(ex, "score"),
             g(meta, "pitch_offset_semitones"), g(rh, "tempo_ratio"),
             1 if meta.get("self_reference") else 0,
             g(ex, "dynamic_range"), g(ex, "phrase_dynamics"), g(ex, "rubato"),
             g(ex, "vibrato_rate"), g(ex, "vibrato_depth"),
             g(ex, "onset_style"), g(ex, "brightness"),
             json.dumps(r.get("metrics_excluded") or [], ensure_ascii=False),
             # ★ 六维归一化单独一列（G3）。`normalized` 只存在于报告里，
             #   而冗余列拼出来的 expressiveness 没有它 —— 见 `_row_to_session`。
             json.dumps((ex.get("normalized") if isinstance(ex, dict) else None) or {},
                        ensure_ascii=False),
             json.dumps(r, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


def load_report(session_uid: str) -> Optional[dict]:
    """取回完整报告（历史里点开某一次要用）。找不到返回 None。"""
    con = connect()
    try:
        row = con.execute(
            "SELECT report_json, singer_id FROM practice_session WHERE session_uid=?",
            (session_uid,)).fetchone()
    finally:
        con.close()
    if not row or not row["report_json"]:
        return None
    try:
        rep = json.loads(row["report_json"])
    except (ValueError, TypeError):
        return None
    if isinstance(rep, dict):
        # 落库时是空的就补上，别让回看的界面以为"这次没有演唱者"
        rep.setdefault("singer_id", row["singer_id"])
    return rep


def get_session(session_uid: str) -> Optional[dict]:
    """按 uid 取一行练习记录（库是唯一真相），不存在返回 None。

    为什么需要它：`/compare` 与 `/status` 原来只查内存 `SESSIONS`，
    服务一重启（= 用户每次重新打开应用）这两个接口就 404，
    而 `/report` 与 `/history` 明明还能从库里读到同一条记录 ——
    表现是"报告在、但对比说这条练习不存在"。
    """
    con = connect()
    try:
        row = con.execute(
            "SELECT * FROM practice_session WHERE session_uid=?",
            (session_uid,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    d = _row_to_session(row)
    # `_row_to_session` 是给"某人的历史"用的，不带 singer_id；
    # 这里补上，`/compare` 的归属校验与"上一次"fallback 都要它。
    d["singer_id"] = row["singer_id"]
    return d


# ────────────────────────────────────────────────────────────
# 读：与内存版 `SINGERS` 结构保持一致，这样两处能无缝互换
# ────────────────────────────────────────────────────────────
def _row_to_session(r: sqlite3.Row) -> dict:
    ex = {}
    for k in ("dynamic_range", "phrase_dynamics", "rubato", "vibrato_rate",
              "vibrato_depth", "onset_style", "brightness"):
        ex[k] = r[k]
    ex["score"] = r["expr_score"]
    try:
        excluded = json.loads(r["metrics_excluded"] or "[]")
    except (ValueError, TypeError):
        excluded = []
    # ★ `normalized` 必须一起带上（G3）。内存版条目（_record_for_singer）
    #   带的是报告里那份完整的 expressiveness，**含 normalized**；
    #   这里以前不带 ⇒ 同一个接口从内存读和从库读返回的结构不一样，
    #   而 `/profile` 的六维横条只认 normalized ⇒ **重启后整块变「—」**。
    #   结构不一致的后果是静默的：页面不报错，只是少了六根条。
    try:
        normalized = json.loads(r["normalized_json"] or "{}")
    except (ValueError, TypeError):
        normalized = {}
    if normalized:
        ex["normalized"] = normalized
    return {
        "session_uid": r["session_uid"], "created_at": r["created_at"],
        "total": r["total"], "pitch": r["pitch_score"],
        "rhythm": r["rhythm_score"], "expr": r["expr_score"],
        "expressiveness": ex, "metrics_excluded": excluded,
        "mode": r["mode"], "retry_of_uid": r["retry_of_uid"],
        "reference_id": r["reference_id"], "reference_title": r["reference_title"],
        "template_id": r["template_id"],
    }


def sessions_of(singer_id: str, limit: int = 200) -> list[dict]:
    """某个演唱者的全部练习，**按时间升序**（进步曲线要升序）。

    ⚠️ `limit` 必须**显式钳制**（2026-09-24，审计 §5.3）。
       SQLite 里 `LIMIT -1` 的语义是**不限量** —— 传一个负数进来
       不是"少查几条"，而是"全表读"。这类"参数没下界"的问题不会报错，
       只会在数据变多之后突然变慢，而且没人会怀疑到分页参数上。
       ⚠️ 非正数**回落到 200**（不是钳成 1）：`limit=0/-1` 的意图是
          "参数给错了"，钳成 1 会让调用方以为拿到了一页、其实只有一条。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    limit = 200 if limit <= 0 else min(2000, limit)
    con = connect()
    try:
        rows = con.execute(
            "SELECT * FROM practice_session WHERE singer_id=?"
            " ORDER BY created_at ASC LIMIT ?", (singer_id, limit)).fetchall()
    finally:
        con.close()
    return [_row_to_session(r) for r in rows]


def all_singers() -> list[str]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT singer_id FROM practice_session"
            " WHERE singer_id IS NOT NULL ORDER BY singer_id").fetchall()
    finally:
        con.close()
    return [r["singer_id"] for r in rows]


# ────────────────────────────────────────────────────────────
# 演唱者名册（G3 多用户）
# ────────────────────────────────────────────────────────────
def _singer_row(r: sqlite3.Row) -> dict:
    return {"singer_id": r["singer_id"], "name": r["name"],
            "created_at": r["created_at"],
            "sessions": r["sessions"] if "sessions" in r.keys() else None,
            "templates": r["templates"] if "templates" in r.keys() else None}


def ensure_singer(singer_id: str, name: Optional[str] = None) -> bool:
    """登记一位演唱者。**已存在就什么都不做**（尤其是**不覆盖已有的名字**）。

    返回 True 表示"这次真的新建了一行"。

    ★ 为什么"不覆盖名字"是关键：这个函数会在**每次写完练习记录**时被调用
      （见 `_record_for_singer`）。如果它顺手改名，用户手动起的名字
      会在下一次练习之后被悄悄换回默认名 —— 而界面上看不出来是"谁改的"。
    """
    if not singer_id:
        return False
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO app_singer (singer_id, name, created_at)"
            " VALUES (?,?,?)",
            (singer_id, (name or "").strip() or default_name(singer_id), time.time()))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def migrate_app_singers() -> dict:
    """启动时的自动登记（§58.1③）：库里出现过的 `singer_id` 全补进名册。

    ★ 为什么必须有这一步（而不是"新用户自己去建一个"）：
      身份以前存在 `sessionStorage`（关标签页就没了），而练习记录用的是
      `singer_id = S01`。换成 `localStorage` + 真实名册之后，
      **已经存在的 S01 那 17 条记录不能变成孤儿** ——
      否则用户升级一次，界面上看到的是"你还没练过"，而数据其实都在库里。
      这正是本项目最怕的那种故障：**空态与数据丢失长得一模一样**。

    ⚠️ 连 `template` 表一起看：模板可以比练习记录活得久（比如某次会话的
      记录被清了、模板还在），只扫 practice_session 会让这类演唱者
      从名册里消失 —— 而"名册里没有"现在等于 404，用户会以为模板也没了。
    """
    added: list[str] = []
    con = connect()
    try:
        known = {r["singer_id"] for r in con.execute("SELECT singer_id FROM app_singer")}
        ids: list[str] = []
        for sql in ("SELECT DISTINCT singer_id FROM practice_session"
                    " WHERE singer_id IS NOT NULL",
                    "SELECT DISTINCT singer_id FROM template"
                    " WHERE singer_id IS NOT NULL"):
            try:
                ids += [r["singer_id"] for r in con.execute(sql)]
            except sqlite3.OperationalError:
                continue          # 某张表还不存在（新库）—— 不是错误
        for sid in dict.fromkeys(ids):        # 去重且保序
            if sid in known:
                continue
            con.execute("INSERT OR IGNORE INTO app_singer (singer_id, name, created_at)"
                        " VALUES (?,?,?)", (sid, default_name(sid), time.time()))
            added.append(sid)
        con.commit()
    finally:
        con.close()
    return {"registered": added, "total": len(list_singers())}


def list_singers() -> list[dict]:
    """名册 + 各自的练习次数/模板数。

    ⚠️ 用**一条** SQL 把计数带出来，不要在 Python 里循环查 ——
       N+1 次 `connect()` 在 9 个人时看不出来，人数一多就是每次打开
       设置页都要开十几次数据库连接。
    """
    con = connect()
    try:
        rows = con.execute(
            "SELECT s.singer_id, s.name, s.created_at,"
            " (SELECT COUNT(*) FROM practice_session p"
            "   WHERE p.singer_id = s.singer_id) AS sessions,"
            " (SELECT COUNT(*) FROM template t"
            "   WHERE t.singer_id = s.singer_id) AS templates"
            " FROM app_singer s"
            " ORDER BY s.created_at ASC, s.singer_id ASC").fetchall()
    finally:
        con.close()
    return [_singer_row(r) for r in rows]


def get_singer(singer_id: str) -> Optional[dict]:
    con = connect()
    try:
        row = con.execute(
            "SELECT s.singer_id, s.name, s.created_at,"
            " (SELECT COUNT(*) FROM practice_session p"
            "   WHERE p.singer_id = s.singer_id) AS sessions,"
            " (SELECT COUNT(*) FROM template t"
            "   WHERE t.singer_id = s.singer_id) AS templates"
            " FROM app_singer s WHERE s.singer_id=?", (singer_id,)).fetchone()
    finally:
        con.close()
    return _singer_row(row) if row else None


def upsert_singer(singer_id: str, name: str) -> dict:
    """新建或改名。返回 `{"singer": {...}, "created": bool, "renamed_from": str|None}`。

    ⚠️ 新建与改名共用一条路，是因为前端那张卡上"新建"和"改名"是同一件事
       （都是给一个 singer_id 配一个名字）；分成两个端点会多一处
       "到底调哪个"的判断，而判断错的表现是**默默新建了一个人**。
    """
    name = (name or "").strip()
    con = connect()
    try:
        row = con.execute("SELECT * FROM app_singer WHERE singer_id=?",
                          (singer_id,)).fetchone()
        if row is None:
            con.execute("INSERT INTO app_singer (singer_id, name, created_at)"
                        " VALUES (?,?,?)", (singer_id, name, time.time()))
            con.commit()
            return {"created": True, "renamed_from": None,
                    "singer": get_singer(singer_id)}
        old = row["name"]
        if old != name:
            con.execute("UPDATE app_singer SET name=? WHERE singer_id=?",
                        (name, singer_id))
            con.commit()
        return {"created": False, "renamed_from": old, "singer": get_singer(singer_id)}
    finally:
        con.close()


def _cleanup_backing() -> int:
    """删掉已无模板引用的 data/backing/*.wav（批量 SQL 删不到文件）。"""
    try:
        from web import templates as _t
        return int(_t.cleanup_orphan_backing() or 0)
    except Exception:  # noqa: BLE001
        return 0


def delete_singer(singer_id: str) -> dict:
    """删除一位演唱者：名册行 + 他的练习记录 + 他的模板 + 画像。

    ★ **不动录音文件**（§58.3 作者的倾向，与 §54.4「宁可少删，不能删错」一致）：
      wav 文件名里没有可靠的演唱者信息（`practice_session` 根本没存文件路径，
      文件还可能被重命名过）。按名字猜着删，猜错就是**删掉别人的录音** ——
      而录音不可恢复，用户也不会知道被删的是谁的。
      所以这里只删库，并在接口返回里如实写明"文件没删、为什么"。

    ⚠️ 归零与删除是两回事：某人的最后一条记录被删掉之后，名册行**必须一起删**
      （否则设置页会留下一串"0 次练习"的幽灵演唱者，用户下次还得自己分辨
      哪个是活的）。所以这里四张表一个事务里删干净。
    """
    con = connect()
    try:
        cur = con.execute("DELETE FROM practice_session WHERE singer_id=?", (singer_id,))
        n_sess = cur.rowcount
        n_tpl = con.execute("DELETE FROM template WHERE singer_id=?",
                            (singer_id,)).rowcount
        n_profile = con.execute("DELETE FROM app_profile WHERE singer_id=?",
                                (singer_id,)).rowcount
        n_singer = con.execute("DELETE FROM app_singer WHERE singer_id=?",
                               (singer_id,)).rowcount
        con.commit()
    finally:
        con.close()
    _cleanup_backing()
    return {"sessions_deleted": n_sess, "templates_deleted": n_tpl,
            "profile_deleted": n_profile,
            "singer_rows_deleted": n_singer}


# ────────────────────────────────────────────────────────────
# 用户画像（P0，2026-10-26）
# ────────────────────────────────────────────────────────────
def _profile_defaults(singer_id: str) -> dict:
    return {
        "singer_id": singer_id,
        "goal": "",
        "preferred_style": [],
        "dislikes": [],
        "experience": "",
        "favorite_songs": [],
        "notes": [],
        "comfort": None,
        "updated_at": None,
    }


def _json_list(raw) -> list:
    try:
        v = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(x)[:120] for x in v if str(x).strip()][:20] if isinstance(v, list) else []


def get_profile(singer_id: str) -> dict:
    """读用户自述画像。没有记录时返回空结构，不编默认人格。"""
    out = _profile_defaults(singer_id)
    con = connect()
    try:
        row = con.execute("SELECT * FROM app_profile WHERE singer_id=?",
                          (singer_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return out
    out.update({
        "goal": str(row["goal"] or ""),
        "preferred_style": _json_list(row["preferred_json"]),
        "dislikes": _json_list(row["dislikes_json"]),
        "experience": str(row["experience"] or ""),
        "favorite_songs": _json_list(row["favorite_json"]),
        "notes": _json_list(row["notes_json"]),
        "updated_at": row["updated_at"],
    })
    try:
        comfort = json.loads(row["comfort_json"]) if row["comfort_json"] else None
    except (ValueError, TypeError):
        comfort = None
    out["comfort"] = comfort if isinstance(comfort, dict) else None
    return out


def save_profile(singer_id: str, fields: dict) -> dict:
    """保存用户自述字段；未给的字段保留原值（PUT 是部分更新）。"""
    cur = get_profile(singer_id)
    allowed = {
        "goal": 120, "experience": 40,
    }
    for key, max_len in allowed.items():
        if key in fields:
            cur[key] = str(fields.get(key) or "").strip()[:max_len]
    for key in ("preferred_style", "dislikes", "favorite_songs"):
        if key in fields and isinstance(fields[key], list):
            cur[key] = [str(x).strip()[:40] for x in fields[key]
                        if str(x).strip()][:12]
    if "notes" in fields and isinstance(fields["notes"], list):
        cur["notes"] = [str(x).strip()[:160] for x in fields["notes"]
                        if str(x).strip()][:20]
    if "comfort" in fields and (fields["comfort"] is None
                                or isinstance(fields["comfort"], dict)):
        cur["comfort"] = fields["comfort"]
    now = time.time()
    con = connect()
    try:
        con.execute(
            "INSERT INTO app_profile (singer_id, goal, preferred_json, dislikes_json,"
            " experience, favorite_json, notes_json, comfort_json, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(singer_id) DO UPDATE SET"
            " goal=excluded.goal, preferred_json=excluded.preferred_json,"
            " dislikes_json=excluded.dislikes_json, experience=excluded.experience,"
            " favorite_json=excluded.favorite_json, notes_json=excluded.notes_json,"
            " comfort_json=excluded.comfort_json, updated_at=excluded.updated_at",
            (singer_id, cur["goal"], json.dumps(cur["preferred_style"], ensure_ascii=False),
             json.dumps(cur["dislikes"], ensure_ascii=False), cur["experience"],
             json.dumps(cur["favorite_songs"], ensure_ascii=False),
             json.dumps(cur["notes"], ensure_ascii=False),
             json.dumps(cur.get("comfort"), ensure_ascii=False), now))
        con.commit()
    finally:
        con.close()
    cur["updated_at"] = now
    return cur


def save_comfort(singer_id: str, comfort: dict | None) -> dict:
    """保存/清除舒适音区自测；不碰目标/偏好/notes。"""
    return save_profile(singer_id, {"comfort": comfort})


def append_profile_note(singer_id: str, text: str) -> dict:
    """只追加用户明确点击“记住这条”的短句，不自动记录聊天。"""
    cur = get_profile(singer_id)
    t = str(text or "").strip()[:160]
    if t and t not in cur["notes"]:
        cur["notes"] = (cur["notes"] + [t])[-20:]
    return save_profile(singer_id, {"notes": cur["notes"]})


def delete_profile_note(singer_id: str, index: int) -> dict:
    cur = get_profile(singer_id)
    try:
        i = int(index)
    except (TypeError, ValueError):
        return cur
    if 0 <= i < len(cur["notes"]):
        cur["notes"].pop(i)
    return save_profile(singer_id, {"notes": cur["notes"]})


def delete_profile(singer_id: str) -> int:
    con = connect()
    try:
        n = con.execute("DELETE FROM app_profile WHERE singer_id=?",
                        (singer_id,)).rowcount
        con.commit()
    finally:
        con.close()
    return n



def session_owner(session_uid: str) -> Optional[str]:
    con = connect()
    try:
        row = con.execute("SELECT singer_id FROM practice_session WHERE session_uid=?",
                          (session_uid,)).fetchone()
    finally:
        con.close()
    return row["singer_id"] if row else None


def delete_session(session_uid: str) -> dict:
    """删掉**某一次**练习记录。返回 `{"deleted": bool, "templates_deleted": int}`。

    ★ 2026-10-15 加（审计发现）：原来 `DELETE` 只有三个
      （`singer` / `template` / `llm_key`）—— **没有"删掉某一次练习"**。
      而用户攒了几十次记录之后，能做的只有 `purge`（**清空全部**）。
      那不是一个真实用户可以接受的选择：他只想删掉唱砸的那一次。

    ⚠️ **只删 `practice_session` 里那一行**，连带把它存过的模板删掉
      （模板是从这次会话提炼的，会话没了模板就成了孤儿）。
    ⚠️ `exp_*` 那张几张表**不动** —— 那是研究数据，与 `purge` 划的是同一条界线。
    ⚠️ **录音文件不在这里删** —— 那个目录归 `web/server.py` 管
      （文件名 = 会话 uid，由服务端按号精确删，见 `session_audio_path`）。
      这里只动库，职责清楚。
    """
    con = connect()
    try:
        tpl = con.execute(
            "DELETE FROM template WHERE source_session_uid=?",
            (session_uid,)).rowcount
        cur = con.execute("DELETE FROM practice_session WHERE session_uid=?",
                          (session_uid,))
        con.commit()
        res = {"deleted": cur.rowcount > 0, "templates_deleted": tpl}
    finally:
        con.close()
    _cleanup_backing()
    return res


def all_session_uids() -> set[str]:
    """库里（含历史归档）出现过的 session_uid。用于清理孤儿录音。"""
    con = connect()
    try:
        ids = {r[0] for r in con.execute(
            "SELECT session_uid FROM practice_session")}
        ids |= {r[0] for r in con.execute(
            "SELECT session_uid FROM practice_session_archived")}
        return ids
    finally:
        con.close()


def cleanup_orphan_session_audio(audio_dir: str, backup_dir: str) -> dict:
    """把没有数据库记录引用的会话录音移到备份目录。

    `delete_singer` 故意不按名字猜删录音；测试/调试也常只删库。
    这些文件在 UI 里已经无法被任何报告引用，继续留在 sessions 目录
    会让隐私页虚高，也会让“清空”看起来没清干净。移动而不是物理删除，
    保留人工回滚的可能。
    """
    if not os.path.isdir(audio_dir):
        return {"moved": 0, "bytes": 0}
    ids = all_session_uids()
    moved = 0
    total = 0
    try:
        os.makedirs(backup_dir, exist_ok=True)
        for name in os.listdir(audio_dir):
            if not name.endswith(".wav") or name[:-4] in ids:
                continue
            src = os.path.join(audio_dir, name)
            dst = os.path.join(backup_dir, name)
            if os.path.exists(dst):
                dst = os.path.join(backup_dir, f"{int(time.time())}_{name}")
            os.replace(src, dst)
            moved += 1
            total += os.path.getsize(dst)
    except OSError as e:
        print(f"[SingLens] ⚠️ 孤儿录音清理失败：{e}")
    return {"moved": moved, "bytes": total}


def count() -> int:
    con = connect()
    try:
        return int(con.execute("SELECT COUNT(*) c FROM practice_session").fetchone()["c"])
    finally:
        con.close()


def count_singers() -> int:
    con = connect()
    try:
        return int(con.execute("SELECT COUNT(*) c FROM app_singer").fetchone()["c"])
    finally:
        con.close()


# ────────────────────────────────────────────────────────────
# 清空（"清空所有录音与记录"的后端）
# ────────────────────────────────────────────────────────────
# ★ 这是一句**产品承诺**（设置页的隐私板块明说会删掉全部历史，无法恢复）。
#   所以它必须真的删干净，而且要**如实报告删了什么** ——
#   悄悄留下什么，比不提供这个功能更糟。
def purge(singer_id: Optional[str] = None) -> dict:
    """删除练习记录。`singer_id=None` 表示删全部。

    只删 `practice_session`；实验数据（`exp_*`）**不动** ——
    那是研究数据，不属于"用户的练习历史"，而且被删掉无法重来。

    ⚠️ `app_singer`（名册）**也不动**（G3 的决定）。理由：
      "清空所有录音与记录"删的是**记录**，而"我叫什么"是身份。
      把身份一起删掉的后果是——界面上写着的那位演唱者**不存在了**，
      于是所有 `/api/singer/{sid}/*` 都变成 404，用户回来看到的
      不是"记录清空了"，而是"这个应用坏了"。身份留着，
      记录清零，界面上就是一句诚实的"你还没练过"。
    """
    con = connect()
    try:
        if singer_id:
            cur = con.execute("DELETE FROM practice_session WHERE singer_id=?",
                              (singer_id,))
            tpl = con.execute("DELETE FROM template WHERE singer_id=?",
                              (singer_id,)).rowcount
            prof = con.execute("DELETE FROM app_profile WHERE singer_id=?",
                               (singer_id,)).rowcount
        else:
            cur = con.execute("DELETE FROM practice_session")
            tpl = con.execute("DELETE FROM template").rowcount
            prof = con.execute("DELETE FROM app_profile").rowcount
        con.commit()
        n = cur.rowcount
    finally:
        con.close()
    _cleanup_backing()
    return {"sessions_deleted": n, "templates_deleted": tpl,
            "profile_deleted": prof}


def purge_recordings(dir_path: str) -> dict:
    """删除录音文件。返回删了几个、释放了多少字节。

    ⚠️ 只删这个目录下的**音频文件**，不递归删目录本身、不碰别的东西 ——
       "清空数据"这个动作最容易误伤，范围要写死。
    """
    exts = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac")
    freed, n = 0, 0
    if not os.path.isdir(dir_path):
        return {"files_deleted": 0, "bytes_freed": 0}
    for name in os.listdir(dir_path):
        fp = os.path.join(dir_path, name)
        if not os.path.isfile(fp) or not name.lower().endswith(exts):
            continue
        try:
            sz = os.path.getsize(fp)
            os.remove(fp)
            freed += sz
            n += 1
        except OSError:
            continue
    return {"files_deleted": n, "bytes_freed": freed}
