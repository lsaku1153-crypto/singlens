"""MIDI 导入 —— 从**符号数据**读出旋律，当练习参考。

━━━ 为什么需要它（这条路解决了什么）━━━

作者的需求是「唱我喜欢的歌，**并知道我走没走音**」。
这需要**正确的旋律**，而获取它有三条路：

  ① 内置曲目          —— 只有 6 首
  ② 从录音里提旋律     —— ❌ **已证走不通**（§92、§93：PYIN 的单声源假设
                          在多声源混音上失效，13 个片段全败）
  ③ **从 MIDI 读**    —— ✅ **本条**

**MIDI 是符号数据**：音高、时长、轨道都是**读出来的**，不是猜的。
⇒ 所以它**能判断走音**，而"从录音提"永远只能猜。

顺带它**不碰版权**：MIDI 是旋律的符号表示，不是录音本身。

━━━ 与现有管线的接法 ━━━

提出来的东西**直接就是 `RefNote[]`** ⇒
对齐、评分、教练、练习计划**一行都不用改**。
这里只做"MIDI 文件 → RefNote[]"这一段转换。

━━━ ⚠️ 设计原则：**自动提，但必须让用户确认** ━━━

真实 MIDI 比测试文件脏得多：
  · 旋律轨可能挑错（有些 MIDI 根本没有轨名）
  · 可能有和弦/双音（要取最高音还是让用户选？）
  · 可能有多声部（主旋律 + 伴唱）
  · 可能有反复记号没展开

⇒ 所以本模块**只负责"提出候选"**，把选择权交给用户：
   `list_tracks()` 给所有轨的摘要，`extract()` 按用户选的轨提取。
   **绝不像"从混音提旋律"那样闷头给一个错的。**

用法（自检）：
    .venv\\Scripts\\python.exe analysis/midi_import.py
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

import mido

# 旋律轨名字里常见的词 —— 有就直接采信（比任何启发式都准）
_MELODY_HINTS = ("melod", "lead", "vocal", "voice", "solo", "main",
                 "主旋律", "旋律", "唱", "人声", "主音")
# 明显不是旋律的轨名 —— 有就**降权**
_NOT_MELODY = ("drum", "perc", "bass", "chord", "accomp", "pad", "鼓", "贝斯",
               "和弦", "伴奏", "节奏")


@dataclass
class MidiNote:
    """一个 MIDI 音符事件（绝对时间，毫秒）。"""

    start_ms: float
    end_ms: float
    midi: int
    velocity: int = 100
    track: int = 0

    @property
    def dur_ms(self) -> float:
        return self.end_ms - self.start_ms


@dataclass
class TrackInfo:
    """一轨的摘要（给用户看，让他选）。"""

    index: int
    name: str
    note_count: int
    lo_midi: float = 0.0
    hi_midi: float = 0.0
    mean_midi: float = 0.0
    distinct_pitches: int = 0
    chord_ratio: float = 0.0        # 同时响的音占比（高 = 和弦轨）
    is_drum: bool = False
    suggestion: float = 0.0         # 越大越像旋律
    reason: str = ""                # 为什么推荐/不推荐（**给用户看的**）

    def to_dict(self) -> dict:
        return {
            "index": self.index, "name": self.name,
            "note_count": self.note_count,
            "lo_midi": round(self.lo_midi, 1), "hi_midi": round(self.hi_midi, 1),
            "mean_midi": round(self.mean_midi, 1),
            "distinct_pitches": self.distinct_pitches,
            "chord_ratio": round(self.chord_ratio, 3),
            "is_drum": self.is_drum,
            "suggestion": round(self.suggestion, 2),
            "reason": self.reason,
        }


def _read_events(path: str) -> tuple[list[MidiNote], list[str], list[bool], int]:
    """读出**全部**音符事件 + 每轨的名字/是否鼓轨。

    ⚠️ 三个必须处理的点（真实 MIDI 里都会遇到）：
      ① **绝对时间**：mido 给的是 delta tick，要累加
      ② **tempo 变化**：累加时按**当时的** tempo 换算成毫秒。
         不能拿一个固定 BPM 算全曲 —— 真实 MIDI 常有变速（渐慢/自由节奏）。
      ③ **同一音高重复**：`note_off` 要配对上正确的 `note_on`
         （用 `pending` 字典按音高配对；同一音高叠两个的情况很少，
          真遇到就后一个覆盖前一个 —— 比丢掉一个音符好）。
    """
    mid = mido.MidiFile(path)
    tpq = mid.ticks_per_beat
    notes: list[MidiNote] = []
    names: list[str] = []
    is_drum: list[bool] = []
    for ti, track in enumerate(mid.tracks):
        # 鼓轨：MIDI 约定是 channel 9（0-based）
        drum = any(getattr(m, "channel", None) == 9 for m in track)
        is_drum.append(drum)
        names.append((track.name or "").strip())
        tempo = 500000                     # 默认 120 BPM（微秒/四分音符）
        t_ms = 0.0
        pending: dict[int, tuple[float, int]] = {}
        for msg in track:
            t_ms += mido.tick2second(msg.time, tpq, tempo) * 1000.0
            if msg.type == "set_tempo":
                tempo = msg.tempo
            elif msg.type == "note_on" and msg.velocity > 0:
                pending[msg.note] = (t_ms, msg.velocity)
            elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0):
                if msg.note in pending:
                    st, vel = pending.pop(msg.note)
                    notes.append(MidiNote(st, t_ms, msg.note, vel, ti))
        # 结尾没给 note_off 的，用轨尾补上（丢音符比这更糟）
        for pitch, (st, vel) in pending.items():
            notes.append(MidiNote(st, t_ms, pitch, vel, ti))
    notes.sort(key=lambda n: (n.start_ms, n.midi))
    return notes, names, is_drum, tpq


def _chord_ratio(ns: list[MidiNote]) -> float:
    """同时响的音占比。

    ⚠️ 实现说明：**用"起点相同"近似"同时响"**。
       严格做法要检查区间重叠，但 MIDI 里同和弦的音起手几乎完全一致
       （同一 tick），所以起点相等就够了，而且 O(n log n)。
       真正需要担心的"长音叠短音"在旋律轨里很少见。
    """
    if not ns:
        return 0.0
    from collections import Counter
    c = Counter(round(n.start_ms) for n in ns)
    in_chord = sum(v for v in c.values() if v > 1)
    return in_chord / len(ns)


def list_tracks(path: str) -> list[TrackInfo]:
    """列出所有轨的摘要 + 推荐（**给用户选**）。

    推荐分数由四件有物理含义的事加权（不是拍脑袋）：
      · **音域偏高**    —— 旋律通常比贝斯高
      · **音高变化多**  —— 旋律在动；铺底和弦几乎是平的
      · **音符多**      —— 旋律音密；伴奏常是长音
      · **不是和弦轨**  —— 同时响的音多 ⇒ 那是伴奏
    外加两条硬信号（有就压过一切）：
      · 轨名含 melody/lead/vocal/主旋律 ⇒ **+3.0**
      · 轨名含 drum/bass/chord/伴奏     ⇒ **−3.0**
      · 鼓轨（channel 9）              ⇒ **直接排除**
    """
    notes, names, drums, _tpq = _read_events(path)
    by: dict[int, list[MidiNote]] = defaultdict(list)
    for n in notes:
        by[n.track].append(n)

    out: list[TrackInfo] = []
    for ti in range(len(names)):
        ns = by.get(ti, [])
        name = names[ti]
        info = TrackInfo(index=ti, name=name or f"轨 {ti}",
                         note_count=len(ns), is_drum=drums[ti])
        if not ns:
            info.reason = "这一轨没有音符"
            out.append(info)
            continue
        ps = [n.midi for n in ns]
        info.lo_midi, info.hi_midi = float(min(ps)), float(max(ps))
        info.mean_midi = float(sum(ps)) / len(ps)
        info.distinct_pitches = len(set(ps))
        info.chord_ratio = _chord_ratio(ns)

        low = name.lower()
        s = ((info.mean_midi / 127.0) * 1.0
             + min(info.distinct_pitches / 12.0, 1.0) * 1.5
             + min(len(ns) / 32.0, 1.0) * 0.5
             - info.chord_ratio * 1.5)
        why = []
        if any(k in low for k in _MELODY_HINTS):
            s += 3.0
            why.append("轨名像旋律")
        if any(k in low for k in _NOT_MELODY):
            s -= 3.0
            why.append("轨名像伴奏/鼓/贝斯")
        if drums[ti]:
            s -= 5.0
            why.append("是鼓轨")
        if info.chord_ratio > 0.5:
            why.append(f"{info.chord_ratio * 100:.0f}% 的音是同时响的（和弦轨）")
        if info.mean_midi >= 60:
            why.append("音域偏高")
        info.suggestion = s
        info.reason = "、".join(why) if why else "没有明显特征"
        out.append(info)
    return out


def pick_best(tracks: list[TrackInfo]) -> TrackInfo | None:
    """自动挑一轨（**只是给个默认值，最终要用户确认**）。"""
    cand = [t for t in tracks if t.note_count >= 4 and not t.is_drum]
    if not cand:
        return None
    return max(cand, key=lambda t: t.suggestion)


def extract(path: str, track_index: int, *, monophonic: bool = True,
            min_dur_ms: float = 40.0) -> list[dict]:
    """从指定轨提取音符，返回 `RefNote[]` 能用的字典列表。

    `monophonic=True`（默认）：**同一时刻只保留最高的那个音**。
    为什么取最高音而不是最低：旋律通常是**最上面**那条线；
    和弦轨的根音在下面。⚠️ 但这不是铁律 ⇒ 用户能改。

    ⚠️ 为什么要 `min_dur_ms`：MIDI 里常有**极短的装饰音/误触**
      （几毫秒的音）。它们既不是旋律，还会把参考搞乱。
      默认 40ms 是经验值 —— 比任何真人能唱的音都短。

    返回的每一项：
        `{"index", "pitch_midi", "start_ms", "end_ms", "velocity"}`
    —— 与 `analysis.align.RefNote` 的字段**对齐**（多一个 velocity）。
    """
    notes, _names, _drums, _tpq = _read_events(path)
    ns = [n for n in notes if n.track == track_index
          and n.dur_ms >= min_dur_ms]
    if not ns:
        return []
    ns.sort(key=lambda n: (n.start_ms, -n.midi))
    if monophonic:
        # 起点相同（或几乎相同）的，只留**最高**那个
        keep: list[MidiNote] = []
        for n in ns:
            if keep and abs(n.start_ms - keep[-1].start_ms) < 20.0:
                if n.midi > keep[-1].midi:
                    keep[-1] = n
                continue
            keep.append(n)
        ns = keep
    out = []
    for i, n in enumerate(ns):
        out.append({
            "index": i,
            "pitch_midi": float(n.midi),
            "start_ms": int(round(n.start_ms)),
            "end_ms": int(round(n.end_ms)),
            "velocity": int(n.velocity),
        })
    return out


def summarize(path: str, track_index: int | None = None) -> dict:
    """给前端用的一份摘要：轨清单 + 推荐 + （可选）某轨的音符。"""
    tracks = list_tracks(path)
    best = pick_best(tracks)
    idx = track_index if track_index is not None else (
        best.index if best else None)
    notes = extract(path, idx) if idx is not None else []
    return {
        "tracks": [t.to_dict() for t in tracks],
        "suggested_track": best.index if best else None,
        "chosen_track": idx,
        "notes": notes,
        "note_count": len(notes),
        "duration_ms": (notes[-1]["end_ms"] if notes else 0),
        "lo_midi": (min(n["pitch_midi"] for n in notes) if notes else None),
        "hi_midi": (max(n["pitch_midi"] for n in notes) if notes else None),
    }


# ══════════════════════════════════════════════════════════════
# 自检：造一个三轨 MIDI（含**诱饵伴奏轨**）当真值
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import tempfile

    TPQ, BPM = 480, 120
    STEP = TPQ // 2
    TRUTH = [60, 60, 67, 67, 69, 69, 67, 65, 65, 64, 64, 62, 62, 60]

    td = tempfile.mkdtemp()
    p = os.path.join(td, "t.mid")
    mid = mido.MidiFile(ticks_per_beat=TPQ)
    t0 = mido.MidiTrack()
    t0.append(mido.MetaMessage("track_name", name="Test", time=0))
    t0.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
    mid.tracks.append(t0)
    t1 = mido.MidiTrack()          # 诱饵：贝斯
    t1.append(mido.MetaMessage("track_name", name="Bass", time=0))
    for i in range(7):
        m = 36 + (i % 2) * 7
        t1.append(mido.Message("note_on", note=m, velocity=80, time=0))
        t1.append(mido.Message("note_off", note=m, velocity=0, time=TPQ * 2))
    mid.tracks.append(t1)
    t2 = mido.MidiTrack()          # 真旋律
    t2.append(mido.MetaMessage("track_name", name="Melody", time=0))
    for m in TRUTH:
        t2.append(mido.Message("note_on", note=m, velocity=100, time=0))
        t2.append(mido.Message("note_off", note=m, velocity=0, time=STEP))
    mid.tracks.append(t2)
    mid.save(p)

    print("=" * 100)
    print("MIDI 导入模块自检")
    print("=" * 100)
    s = summarize(p)
    print(f"\n  轨清单（{len(s['tracks'])} 轨）：")
    print(f"  {'#':>3s} {'名字':<12s} {'音符':>5s} {'音域':>10s} {'和弦比':>7s} "
          f"{'得分':>6s}  说明")
    for t in s["tracks"]:
        print(f"  {t['index']:3d} {t['name']:<12s} {t['note_count']:5d} "
              f"{t['lo_midi']:4.0f}–{t['hi_midi']:<4.0f} {t['chord_ratio']:7.2f} "
              f"{t['suggestion']:6.2f}  {t['reason']}")

    print(f"\n  自动推荐 = 轨 {s['suggested_track']}"
          f"  {'✅ 正确（是 Melody，不是 Bass）' if s['suggested_track'] == 2 else '❌ 推错了'}")
    got = [n["pitch_midi"] for n in s["notes"]]
    print(f"  提出 {len(got)} 音：{got}")
    print(f"  真值 {len(TRUTH)} 音：{TRUTH}")
    print(f"  音高一致：{'✅' if got == [float(x) for x in TRUTH] else '❌'}")
    if s["notes"]:
        d = s["notes"][0]["end_ms"] - s["notes"][0]["start_ms"]
        exp = STEP * 60_000 / BPM / TPQ
        print(f"  时长 {d}ms（期望 {exp:.0f}ms）：{'✅' if abs(d - exp) <= 2 else '❌'}")
        print(f"  跨度 {s['duration_ms']}ms   音域 {s['lo_midi']:.0f}–{s['hi_midi']:.0f}")

    print("\n  降级自检：")
    print(f"    extract(轨 1 = Bass) 提出 "
          f"{len(extract(p, 1))} 音（应当只有 7 个低音，不该混进旋律）")
    print(f"    extract(轨 0 = 空轨) 提出 {len(extract(p, 0))} 音（应当 0）")
    print(f"    extract(不存在的轨 9) 提出 {len(extract(p, 9))} 音（应当 0）")
