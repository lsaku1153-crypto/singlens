"""跟唱模板存储（模式 B 两遍制的基础）。

━━━ 为什么要这个模块 ━━━

模式 B 的产品承诺是「**先唱一遍当样板，第二遍跟它比 —— 想练什么歌都行**」。
但 2026-09-20 之前，服务端是拿**本次演唱自己**造参考（`from_sung_notes(notes)`），
所以"比较"必然是完美的：全部音符偏差恒为 0，音准分/节奏分恒为 100。
（详见 `项目记忆.md` §34/§47；`experiments/probe_modeb_selfref.py` 可复现。）

那等于**「任意歌曲」这个卖点是空的** —— 实际只能唱内置那 2 首。

修法就是"真做两遍制"：
    第一遍 → 提取 Note 序列 → **存成模板**（本模块）
    第二遍 → 与模板对齐 → 才有真实的音准/节奏分

━━━ 存什么 ━━━

只存**音符序列**（起点/终点/音高/音高标准差/时长），不存音频 —— 因为：
  · 对齐只需要 Note 序列（`ReferenceMelody` 就是从 Note 列表构造的）
  · 不存音频 = 体积小、也不涉及"用户录音被长期保留"的隐私问题
  · 与内置曲库同一形态（`ReferenceMelody.from_midi_list`），下游一视同仁

⚠️ `stable_pitch` 的 hop_length 必须一起存：模板的 `pitch_midi` 是
   "去掉滑入段后的稳定段中位数"，换一个 hop 重算会得到略不同的值。
   存下来是为了论文里能复现（也便于以后换 hop 时知道模板是哪个口径算的）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "experiment.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS template (
    template_id   TEXT PRIMARY KEY,
    singer_id     TEXT,
    title         TEXT,          -- 用户可读的名字（默认「我的模板 MM-DD HH:MM」）
    note_count    INTEGER,       -- 音符数（列表页直接用，免得解 JSON）
    duration_ms   INTEGER,
    hop_length    INTEGER,       -- 稳定段音高用的 hop（复现用）
    sr            INTEGER,
    notes_json    TEXT,          -- Note 序列（JSON）
    quality_json  TEXT,          -- assess_template_quality 的结果
    -- ★ 这份模板**代表哪个参考**。模式 B 的模板就是"用户的参考曲目"，
    --   所以它必须有自己的 reference_id —— 否则第二遍的报告里
    --   `meta.reference_id` 只能写个模板号，说不清"这是哪首歌"。
    --   默认等于 template_id（自指），也允许调用方给一个更好认的 id。
    reference_id  TEXT,
    -- ★ 模板是从哪一次会话做出来的。留这个是为了可追溯：
    --   模板不稳/有问题时，能回到那一次录音去看（会话在内存里会丢，
    --   但 uid 至少能对上日志与 data/recordings/exp/ 里的存档文件名）。
    source_session_uid TEXT,
    -- ★ 2026-10-20：模板来源与伴奏。
    --   `user_take`  = 用户自己唱的第一遍（模式 B 原生）
    --   `song_vocal` = 上传歌曲 → Demucs 分离 → 原唱人声当参考
    source_kind   TEXT DEFAULT 'user_take',
    -- 歌曲参考的伴奏音轨（data/backing/{template_id}.wav）。
    -- 有它时 `/api/template/{tid}/guide.wav` 放**真实伴奏**，
    -- 而不是用音符合成的旋律。
    backing_path  TEXT,
    -- 歌曲参考的**原唱人声**副本（data/backing/{tid}_vocal.wav）。
    -- 给报告页「听原唱这一段」用；没有它就只能听合成旋律。
    vocal_path    TEXT,
    -- ★ 2026-10-20：整首跟唱的歌词（Whisper 自动识别，行级时间戳）。
    --   [{start_ms,end_ms,text}]；没有歌词时为空。
    lyrics_json   TEXT,
    lyrics_source TEXT,
    -- 歌曲参考的附加信息（歌名/起止/分离质量/泄漏）——只用于展示与追溯
    source_meta_json TEXT,
    created_at    REAL
);
"""

# ── 轻量迁移 ────────────────────────────────────────────────
# ⚠️ 与 web/experiment.py 同一个坑：`CREATE TABLE IF NOT EXISTS`
#    **对已存在的表不会加列**。这个库在本次改动之前就建过 template 表，
#    不加迁移的话，reference_id 会**静默地永远是 NULL**（列不存在，
#   而这里连 ALTER 都没发）——典型的不报错的错。
_MIGRATIONS = {
    "template": [("reference_id", "TEXT"),
                 ("source_session_uid", "TEXT"),
                 ("source_kind", "TEXT DEFAULT 'user_take'"),
                 ("backing_path", "TEXT"),
                 ("vocal_path", "TEXT"),
                 ("lyrics_json", "TEXT"),
                 ("lyrics_source", "TEXT"),
                 ("source_meta_json", "TEXT")],
}


def _migrate(con: sqlite3.Connection):
    """给老库补新列（幂等）。类型必须显式写，理由见 web/experiment.py。"""
    for table, wanted in _MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue                       # 表还没建，SCHEMA 会建好
        for col, coltype in wanted:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    con.commit()


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _notes_to_json(notes) -> str:
    """Note 列表 → JSON。只留对齐要用的字段。

    ⚠️ 字段要克制：多存无用字段会让以后改 Note 时模板全失效。
       对齐用到的是 start_ms / end_ms / pitch_midi；
       pitch_std 与 voiced_ratio 用于模板质量门控的复核。
    """
    out = []
    for n in notes:
        out.append({
            "note_index": int(getattr(n, "note_index", len(out))),
            "start_ms": int(n.start_ms),
            "end_ms": int(n.end_ms),
            "pitch_midi": (None if n.pitch_midi is None else round(float(n.pitch_midi), 3)),
            "pitch_std": (None if getattr(n, "pitch_std", None) is None
                          else round(float(n.pitch_std), 3)),
            "voiced_ratio": (None if getattr(n, "voiced_ratio", None) is None
                             else round(float(n.voiced_ratio), 3)),
            "long_enough_for_vibrato": bool(getattr(n, "long_enough_for_vibrato", False)),
        })
    return json.dumps(out, ensure_ascii=False)


@dataclass
class Template:
    template_id: str
    singer_id: Optional[str]
    title: str
    notes: list                       # [{"start_ms", "end_ms", "pitch_midi", ...}]
    note_count: int
    duration_ms: int
    hop_length: int
    sr: int
    quality: dict
    created_at: float
    reference_id: Optional[str] = None       # 这份模板代表哪个参考（默认自指）
    source_session_uid: Optional[str] = None  # 由哪一次会话做出来的
    # ★ 2026-10-20：`user_take` / `song_vocal`
    source_kind: str = "user_take"
    backing_path: Optional[str] = None        # 歌曲参考的伴奏音轨（WAV）
    vocal_path: Optional[str] = None          # 歌曲参考的原唱人声副本（试听用）
    lyrics: list = None                       # [{start_ms,end_ms,text}] 行级
    lyrics_source: Optional[str] = None       # "whisper-small" 等
    source_meta: dict = None                  # 歌名/起止/分离质量等


def save(notes, singer_id: Optional[str] = None, title: str = "",
         hop_length: int = 256, sr: int = 22050,
         quality: Optional[dict] = None,
         reference_id: Optional[str] = None,
         source_session_uid: Optional[str] = None,
         source_kind: str = "user_take",
         backing_path: Optional[str] = None,
         vocal_path: Optional[str] = None,
         lyrics: Optional[list] = None,
         lyrics_source: Optional[str] = None,
         source_meta: Optional[dict] = None,
         template_id: Optional[str] = None) -> str:
    """把一个 Note 序列存成模板，返回 template_id。

    `reference_id` 不给就取 template_id（自指）—— 这样"模板即参考"这件事
    在数据里是**显式**的，报告里的 `meta.reference_id` 不会是个空字符串。
    `template_id` 由调用方给（歌曲导入要先按 id 放伴奏文件）。
    """
    tid = template_id or f"tpl_{uuid.uuid4().hex[:12]}"
    payload = _notes_to_json(notes)
    n = len(notes)
    dur = int(notes[-1].end_ms) if n else 0
    if not title:
        title = "我的模板 " + time.strftime("%m-%d %H:%M")
    con = connect()
    try:
        con.execute(
            "INSERT INTO template (template_id, singer_id, title, note_count,"
            " duration_ms, hop_length, sr, notes_json, quality_json,"
            " reference_id, source_session_uid, source_kind, backing_path,"
            " vocal_path, lyrics_json, lyrics_source, source_meta_json, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, singer_id, title, n, dur, int(hop_length), int(sr), payload,
             json.dumps(quality or {}, ensure_ascii=False),
             reference_id or tid, source_session_uid,
             source_kind or "user_take", backing_path, vocal_path,
             json.dumps(lyrics or [], ensure_ascii=False), lyrics_source,
             json.dumps(source_meta or {}, ensure_ascii=False), time.time()))
        con.commit()
    finally:
        con.close()
    return tid


def load(template_id: str) -> Optional[Template]:
    con = connect()
    try:
        row = con.execute("SELECT * FROM template WHERE template_id=?",
                          (template_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    keys = set(row.keys())
    return Template(
        template_id=row["template_id"], singer_id=row["singer_id"],
        title=row["title"], notes=json.loads(row["notes_json"]),
        note_count=row["note_count"], duration_ms=row["duration_ms"] or 0,
        hop_length=row["hop_length"] or 256, sr=row["sr"] or 22050,
        quality=json.loads(row["quality_json"] or "{}"),
        created_at=row["created_at"] or 0.0,
        # 老库补列之后这两列可能不存在（理论上 _migrate 已经补过，
        # 但读的时候再兜一层，免得拿到 IndexError 而不是 None）
        reference_id=(row["reference_id"] if "reference_id" in keys else None),
        source_session_uid=(row["source_session_uid"]
                            if "source_session_uid" in keys else None),
        source_kind=((row["source_kind"] if "source_kind" in keys else None)
                     or "user_take"),
        backing_path=(row["backing_path"] if "backing_path" in keys else None),
        vocal_path=(row["vocal_path"] if "vocal_path" in keys else None),
        lyrics=json.loads((row["lyrics_json"]
                           if "lyrics_json" in keys else None) or "[]"),
        lyrics_source=(row["lyrics_source"] if "lyrics_source" in keys else None),
        source_meta=json.loads((row["source_meta_json"]
                                if "source_meta_json" in keys else None) or "{}"),
    )


def list_templates(singer_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    """列表（不含 notes_json —— 列表页用不上，省流量）。

    ⚠️ `limit` 钳制（审计 §5.3）：SQLite 的 `LIMIT -1` = **不限量**
    （实测 `GET /api/template?limit=-1` 返回全部）。防的是"参数没下界"这类
    不会报错、只在数据变多后才暴露的问题。
    ⚠️ 非正数**回落到 50**，不是钳成 1（钳成 1 会让调用方以为拿到了一页）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = 50 if limit <= 0 else min(500, limit)
    con = connect()
    try:
        if singer_id:
            rows = con.execute(
                "SELECT template_id, singer_id, title, note_count, duration_ms,"
                " quality_json, reference_id, source_kind, backing_path,"
                " vocal_path, lyrics_json, source_meta_json, created_at FROM template"
                " WHERE singer_id=? ORDER BY created_at DESC LIMIT ?",
                (singer_id, limit)).fetchall()
        else:
            rows = con.execute(
                "SELECT template_id, singer_id, title, note_count, duration_ms,"
                " quality_json, reference_id, source_kind, backing_path,"
                " vocal_path, lyrics_json, source_meta_json, created_at FROM template"
                " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        q = json.loads(r["quality_json"] or "{}")
        keys = set(r.keys())
        meta = json.loads((r["source_meta_json"]
                           if "source_meta_json" in keys else None) or "{}")
        lyr = json.loads((r["lyrics_json"]
                          if "lyrics_json" in keys else None) or "[]")
        out.append({
            "template_id": r["template_id"], "singer_id": r["singer_id"],
            "title": r["title"], "note_count": r["note_count"],
            "duration_ms": r["duration_ms"],
            "quality_level": q.get("level"), "quality_reasons": q.get("reasons", []),
            "reference_id": (r["reference_id"] if "reference_id" in keys else None),
            "source_kind": ((r["source_kind"] if "source_kind" in keys else None)
                            or "user_take"),
            "has_backing": bool((r["backing_path"] if "backing_path" in keys else None)),
            "has_vocal": bool((r["vocal_path"] if "vocal_path" in keys else None)),
            "has_lyrics": bool(lyr),
            "lyrics_count": len(lyr),
            "source_meta": meta,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(r["created_at"] or 0)),
        })
    return out


def delete(template_id: str) -> bool:
    # 歌曲参考的伴奏副本由本项目创建（data/backing/{tid}.wav），
    # 删模板时一起删；其它路径一律不动（绝不按模板里的任意路径删文件）。
    tpl = load(template_id)
    con = connect()
    try:
        cur = con.execute("DELETE FROM template WHERE template_id=?", (template_id,))
        con.commit()
        ok = cur.rowcount > 0
    finally:
        con.close()
    if ok and tpl:
        root = os.path.join(ROOT, "data", "backing")
        for p0 in (tpl.backing_path, tpl.vocal_path):
            if not p0:
                continue
            try:
                p = os.path.abspath(p0)
                if os.path.commonpath([os.path.abspath(root), p]) == os.path.abspath(root):
                    os.remove(p)
            except (OSError, ValueError):
                pass
        for p in _guide_files_for(template_id):
            try:
                os.remove(p)
            except OSError:
                pass
    return ok


def _guide_files_for(template_id: str) -> list[str]:
    """`data/exports/guide_template_{tid}[_tN].wav` —— 模板删除时一起清。"""
    root = os.path.join(ROOT, "data", "exports")
    out = []
    try:
        for name in os.listdir(root):
            if not (name.startswith(f"guide_template_{template_id}")
                    and name.endswith(".wav")):
                continue
            tail = name[len(f"guide_template_{template_id}"):-4]
            if re.fullmatch(r"(?:_vocal|_original)?(?:_t-?\d+)?", tail):
                out.append(os.path.join(root, name))
    except OSError:
        pass
    return out


def cleanup_orphan_backing() -> int:
    """删掉 `data/backing/` 里已经没有模板引用的伴奏/原唱文件。

    ⚠️ 为什么需要：`practice_store` 的"清空 / 按人删 / 删单条"是**批量 SQL
       DELETE**，不经过 `templates.delete()`；如果不在这里补一刀，
       `data/backing/{tid}.wav` 会永久留在磁盘上（一首 30s 歌约 10 MB）。
    """
    root = os.path.join(ROOT, "data", "backing")
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    live = set()
    con = connect()
    try:
        live = {r["template_id"] for r in
                con.execute("SELECT template_id FROM template").fetchall()}
    finally:
        con.close()
    removed = 0
    for name in names:
        if not name.endswith(".wav"):
            continue
        stem = name[:-4]
        tid = stem[:-6] if stem.endswith("_vocal") else stem
        if tid in live or not tid.startswith("tpl_"):
            continue
        try:
            os.remove(os.path.join(root, name))
            removed += 1
        except OSError:
            pass
    # 顺便清 guide 缓存：批量 SQL 删除不会经过 templates.delete()
    gdir = os.path.join(ROOT, "data", "exports")
    try:
        for name in os.listdir(gdir):
            m = re.fullmatch(r"guide_template_(tpl_[0-9a-f]+)(?:_vocal|_original)?(?:_t-?\d+)?\.wav", name)
            if m and m.group(1) not in live:
                try:
                    os.remove(os.path.join(gdir, name))
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed


def update_lyrics(template_id: str, lyrics: list, lyrics_source: Optional[str] = None) -> bool:
    """只替换歌词行（旧模板重切歌词用）；音符/身份/路径都不动。

    `lyrics` 必须是 [{start_ms,end_ms,text}]。旧报告不会回溯 ——
    报告里已经冻结了当时的 reference_lyrics_count 和行文本。
    """
    clean = []
    for x in (lyrics or []):
        if not isinstance(x, dict):
            continue
        text = str(x.get("text") or "").strip()
        if not text:
            continue
        try:
            a = int(x.get("start_ms") or 0)
            b = int(x.get("end_ms") or 0)
        except (TypeError, ValueError):
            continue
        if b <= a:
            continue
        clean.append({"start_ms": a, "end_ms": b, "text": text})
    if not clean:
        return False
    con = connect()
    try:
        cur = con.execute(
            "UPDATE template SET lyrics_json=?, lyrics_source=? WHERE template_id=?",
            (json.dumps(clean, ensure_ascii=False), lyrics_source or "whisper",
             template_id))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def rename(template_id: str, singer_id: Optional[str], title: str) -> bool:
    """只改标题，**不动音符/不重算**——模板身份不变。"""
    title = (title or "").strip()
    if not title:
        return False
    con = connect()
    try:
        if singer_id:
            cur = con.execute(
                "UPDATE template SET title=? WHERE template_id=? AND singer_id=?",
                (title, template_id, singer_id))
        else:
            cur = con.execute(
                "UPDATE template SET title=? WHERE template_id=?",
                (title, template_id))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def to_reference_melody(tpl: Template, reference_id: Optional[str] = None):
    """模板 → `ReferenceMelody`，好让下游用完全相同的对齐代码。

    ⚠️ 这里 source 特意标成 `user_first_take`，与服务端报告里的
       `reference_source` 对齐 —— 论文里要能区分"内置谱面"与"用户模板"。

    reference_id 的取值顺序：调用方给的 → 模板自己存的 → 模板号（自指）。
    存下来的那个优先于模板号，因为它是**这份模板的身份**，
    报告里的 `meta.reference_id` 靠它说清"这次是跟哪首歌比的"。
    """
    from analysis.align import RefNote, ReferenceMelody
    refs = []
    for i, n in enumerate(tpl.notes):
        if n.get("pitch_midi") is None:
            continue                       # 没音高的音符当不了参考
        refs.append(RefNote(index=len(refs), start_ms=int(n["start_ms"]),
                            end_ms=int(n["end_ms"]),
                            pitch_midi=float(n["pitch_midi"])))
    return ReferenceMelody(
        reference_id=reference_id or tpl.reference_id or tpl.template_id,
        title=tpl.title, notes=refs, mode="B",
        source=("song_vocal" if (tpl.source_kind or "") == "song_vocal"
                else "user_first_take"))


