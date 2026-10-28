"""DTW 对齐：把演唱的音符与参考旋律配起来。

这个模块产出两个直接决定分数的量：
  pitch_cents_dev  音分偏差  → 音准分的输入
  onset_dev_ms     起始偏差  → 节奏分的输入
以及每个演唱音符对应的参考音符序号 ref_note_index。

━━━ 两个必须处理的现实问题 ━━━

① **滑音（scoop / portamento）**
   唱高音前先"滑上去"是很正常的技巧。实测在放大图上看到：
   某个音符的横条画在 MIDI 55，但音高曲线大部分在 49–50
   —— 因为它内部有一段从 49 快速滑到 55 的过程。
   如果拿【整个音符】的中位音高去比参考，滑音会被判成走音。
   → 所以音准偏差必须用【稳定段】算：去掉开头的滑入段，只比后半段。

② **节奏不准 ≠ 没有弹性**
   起始偏差的【均值】是"不准"，偏差的【规律结构】是"艺术处理"（rubato）。
   本模块只负责产出【原始偏差序列】；拆分交给 scoring.py。
   这里绝不做任何"平滑"或"归零"，否则下游就拿不到真实数据了。

━━━ 两种参考来源（对应产品的 A/B 模式）━━━
  模式 A 教材曲目：参考是已知的音符序列（音名 + 时值）
  模式 B 跟唱模板：参考是用户自己第一遍唱的 Note 列表
两者在 DTW 里统一成同一个结构，只是构造方式不同。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence

import librosa
import numpy as np

from .pitch import PRECISE_HOP, hz_to_midi, midi_to_hz

# ── 默认参数 ────────────────────────────────────────────────
DEFAULTS = {
    # 稳定段提取：去掉音符开头多少比例的滑入段
    "scoop_skip_ratio": 0.35,    # 去掉前 35%
    "scoop_skip_max_ms": 140,    # 但最多去 140ms（短音符不能去掉太多）
    "scoop_skip_min_ms": 25,     # 至少保留这么多，防短音符被削没

    # DTW 代价权重
    "pitch_weight": 1.0,         # 音高差（半音）权重
    "time_weight": 0.55,         # 归一化时间差权重

    # 对齐质量
    "max_pitch_jump": 12.0,      # 匹配对的音高差超过此值（一个八度）→ 视为可疑
    "min_match_ratio": 0.5,      # 【参考侧】被匹配上的参考音符占比下限
    "max_pitch_offset": 18.0,    # 允许演唱者相对参考的整体移调上限（半音）
    "max_residual_semitone": 3.0,  # 去掉整体移调后，中位绝对残差上限（半音）
}


# ══════════════════════════════════════════════════════════════
# 参考旋律
# ══════════════════════════════════════════════════════════════
@dataclass
class RefNote:
    index: int
    start_ms: int
    end_ms: int
    pitch_midi: float

    @property
    def dur_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class ReferenceMelody:
    reference_id: str
    title: str
    notes: list[RefNote]
    mode: str = "A"                    # "A" 教材曲目 / "B" 跟唱模板
    source: str = "builtin"            # 来源说明，写进论文
    # ★ 2026-10-18：这次参考整体移调了几个半音（0 = 原调）。
    #   为什么放进参考对象而不是另传参数：`_builtin_ref_for()` 会按演唱起点
    #   重新 load 内置参考 —— 重载会丢掉"外面传进来的 transpose"。
    #   放在对象上，重载后仍能读回来。
    transpose: int = 0
    # ★ 2026-10-19：内置曲目的歌词行（`{text, from, to}`，from/to 是参考音 index）。
    #   报告里的「第 22–23 个音」需要映射到「第 4 句『好像许多小眼睛』」；
    #   没有歌词行时只给时间/音序号，不硬编。
    lyric_lines: list[dict] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.notes[-1].end_ms if self.notes else 0

    def to_dict(self) -> dict:
        return {
            "reference_id": self.reference_id, "title": self.title,
            "mode": self.mode, "source": self.source,
            "notes": [asdict(n) for n in self.notes],
            "lyric_lines": self.lyric_lines,
        }

    # ── 构造方式 ①：教材曲目（音名序列 + 时值）──
    @classmethod
    def from_midi_list(cls, midi_list: Sequence[float],
                       note_dur_ms: int | Sequence[int] = 500,
                       reference_id: str = "REF", title: str = "",
                       mode: str = "A", first_start_ms: int = 0,
                       phrase_len: int = 0,
                       phrase_gap_ms: int = 0,
                       lyric_lines: list[dict] | None = None) -> "ReferenceMelody":
        """first_start_ms 用于测试：让参考与合成音频从同一个起点计时，
        否则「整体平移」会被归一化掉，测不出偏移。

        `note_dur_ms` 可以是**一个数**（所有音等长，教材曲目的常态）
        或**一个列表**（逐音时长）—— 后者是 2026-10-02 加的，
        因为构造式方案 §2.1b 要求 VIB 组**最后三个音各拖长到约 2 秒**
        （颤音估计需要足够长的观测窗，且判据要求 ≥3 个长音检出）。
        没有逐音时长，那种"刻意改时值"的参考就写不出来。

        ★ 2026-10-02 再加：**句间换气间隔**（`phrase_len` + `phrase_gap_ms`）。

        为什么需要：**唱歌是要换气的**。每句末尾都会插进一段 ~0.5–1 秒的静音，
        6 句就是 **3–6 秒的累积延迟**。而 `tempo_ratio` 量的是**总时长**，
        于是"换气"会被算成"你慢了 15–30%"。

        ⚠️ 这条很可能是**之前那批真人素材 `tempo_ratio` 普遍偏高的部分原因** ——
           不是"没跟拍子"，而是**参考里没有换气的位置**。
           作者的原话：「每句之间的间隔太短了，唱歌是需要换气的」。

        `phrase_len` = 每句几个音（0 = 不分句，与旧行为完全一致）。
        间隔加在**每句之后**（最后一句之后不加，因为唱完就结束了）。
        """
        if isinstance(note_dur_ms, (list, tuple)):
            durs = [int(d) for d in note_dur_ms]
            if len(durs) != len(midi_list):
                raise ValueError(
                    f"note_dur_ms 长度 {len(durs)} 与音符数 {len(midi_list)} 不一致")
        else:
            durs = [int(note_dur_ms)] * len(midi_list)
        n_ph = int(phrase_len or 0)
        gap = int(phrase_gap_ms or 0) if n_ph > 0 else 0
        notes, t = [], first_start_ms
        for i, m in enumerate(midi_list):
            notes.append(RefNote(index=i, start_ms=t, end_ms=t + durs[i],
                                 pitch_midi=float(m)))
            t += durs[i]
            # 句末换气：本句最后一个音之后加间隔（最后一句之后不加）
            if gap and n_ph and (i + 1) % n_ph == 0 and (i + 1) < len(midi_list):
                t += gap
        return cls(reference_id=reference_id, title=title or reference_id,
                   notes=notes, mode=mode, source="builtin_score",
                   lyric_lines=list(lyric_lines or []))

    # ── 构造方式 ②：跟唱模板（用户第一遍唱的）──
    @classmethod
    def from_sung_notes(cls, notes, reference_id: str = "REF_TEMPLATE",
                        title: str = "跟唱模板", mode: str = "B",
                        f0=None, sr=None, hop_length: int = PRECISE_HOP) -> "ReferenceMelody":
        """从用户第一遍演唱构造参考。

        ★ 传了 f0/sr 时用【稳定段音高】而不是 note.pitch_midi。
          因为跟唱对比时，两边必须用同一套口径：
          拿「去滑音后的稳定音高」去比「含滑音的全音符中位」，会凭空多出偏差。
        """
        refs = []
        for n in notes:
            pm = n.pitch_midi
            if f0 is not None and sr is not None:
                sp = stable_pitch(n, f0, sr, hop_length)[0]
                if sp is not None:
                    pm = sp
            if pm is None:
                continue
            refs.append(RefNote(index=len(refs), start_ms=n.start_ms,
                                end_ms=n.end_ms, pitch_midi=float(pm)))
        return cls(reference_id=reference_id, title=title, notes=refs,
                   mode=mode, source="user_first_take")


# ── 内置曲库（只用公有领域曲目，且只存音符序列，不使用任何录音）──
#
# ★ 2026-10-02 加了 `REF_TWINKLE_2`（14 音），**不删旧的**。
#   为什么加长的：构造式方案的操作检验查出 `rubato` 在 7 音乐句上不成立 ——
#   它用 `median_filter(dz, size=5)` 提趋势，而序列只有 7 个音时窗 5
#   几乎不起作用（实测一个台阶就能把它从 13ms 顶到 150ms）。
#   要让它有意义，序列至少要到窗的 2–3 倍 ⇒ ≥12 个音。
#
#   ⚠️ **`REF_TWINKLE_1` 绝不能删**：论文里的 96.41% 是在它上面量的
#      （源文件 `sep12_5pm_vocal.wav`）。删了就复现不出来。
BUILTIN = {
    "REF_TWINKLE_1": {
        "title": "小星星 · 第一句",
        "midi": [60, 60, 67, 67, 69, 69, 67],
        "note_dur_ms": 500,
        "lyric_lines": [{"text": "一闪一闪亮晶晶", "from": 0, "to": 6}],
        "license": "公有领域（法国民谣 Ah! vous dirai-je, maman，18 世纪）",
    },
    "REF_TWINKLE_2": {
        # 小星星**前两句**（14 音）。
        # ⚠️ 2026-10-02：这个参考**写短了** —— 作者唱的是完整四句（28 音）。
        #    见下面 `REF_TWINKLE_FULL` 的说明。保留它是因为已有材料引用了它。
        "title": "小星星 · 前两句",
        "midi": [60, 60, 67, 67, 69, 69, 67,
                 65, 65, 64, 64, 62, 62, 60],
        "note_dur_ms": 500,
        "lyric_lines": [
            {"text": "一闪一闪亮晶晶", "from": 0, "to": 6},
            {"text": "满天都是小星星", "from": 7, "to": 13},
        ],
        "license": "公有领域（法国民谣 Ah! vous dirai-je, maman，18 世纪）",
    },
    "REF_TWINKLE_FULL": {
        # ★ 完整四句（28 音）—— 2026-10-02 加。但**它也短了**，见下面 42 音那个。
        #
        #   作者原话：「我唱的就是一闪一闪亮晶晶，满天都是小星星，
        #   挂在天空放光明，好像许多小眼睛。这五段是用不同的状态唱的，有快有慢。」
        #
        #   实测（`experiments/_verify_full_song.py`，同一批真人素材）：
        #       素材      14 音参考            28 音参考
        #       take01   14/14  match 0.538 →  22/28  match 0.846
        #       take02   13/14  match 0.464 →  25/28  match 0.893
        #       take03   11/14  match 0.333 →  **28/28** match 0.848
        #       take05   12/14  match 0.387 →  27/28  match 0.871
        #   而且 28 音参考下**逆序全为 0**（分配严格单调、一段一段往前走）。
        "title": "小星星 · 完整四句",
        "midi": [60, 60, 67, 67, 69, 69, 67,      # 一闪一闪亮晶晶
                 65, 65, 64, 64, 62, 62, 60,      # 满天都是小星星
                 67, 67, 65, 65, 64, 64, 62,      # 挂在天空放光明
                 67, 67, 65, 65, 64, 64, 62],     # 好像许多小眼睛
        "note_dur_ms": 500,
        "lyric_lines": [
            {"text": "一闪一闪亮晶晶", "from": 0, "to": 6},
            {"text": "满天都是小星星", "from": 7, "to": 13},
            {"text": "挂在天空放光明", "from": 14, "to": 20},
            {"text": "好像许多小眼睛", "from": 21, "to": 27},
        ],
        "license": "公有领域（法国民谣 Ah! vous dirai-je, maman，18 世纪）",
    },
    "REF_TWINKLE_6": {
        # ★★ 六句（42 音）—— 2026-10-02 加。**这才是作者实际唱的形态。**
        #
        #   作者原话：「这一段录音我一共唱了六句，后面两句是重复的
        #   一闪一闪亮晶晶，满天都是小星星。」
        #
        #   ⇒ 四句 + 重复前两句 = 42 音。**前面两版（14 / 28 音）都写短了。**
        #
        #   实测（`experiments/_verify_6phrases.py`，`9月19日 下午4点26分(1).m4a`，
        #   作者指定"唱得比较稳"的那一段，切出 36 段）：
        #
        #       参考        match    tempo   accuracy  stability  rubato   intercept
        #       28 音(四句)  0.639   1.744    1176.8ms   370.0ms   996.0ms  −1022.5ms
        #       42 音(六句)  **0.972**  **1.104**  **246.4ms**  229.6ms   **87.8ms**  654.8ms
        #
        #   `match` 0.639 → 0.972、`rubato` 996 → **87.8ms**、起点不再差 1 秒。
        #
        #   ⚠️ 这一条推翻了 2026-10-02 一整天的一批结论：
        #      "切分坏了" / "音高测不准" / "rubato 不可重复" ——
        #      **根因都是参考写短了**。指标本身没有那么糟。
        "title": "小星星 · 六句（四句 + 重复前两句）",
        "midi": [60, 60, 67, 67, 69, 69, 67,      # 一闪一闪亮晶晶
                 65, 65, 64, 64, 62, 62, 60,      # 满天都是小星星
                 67, 67, 65, 65, 64, 64, 62,      # 挂在天空放光明
                 67, 67, 65, 65, 64, 64, 62,      # 好像许多小眼睛
                 60, 60, 67, 67, 69, 69, 67,      # 一闪一闪亮晶晶（重复）
                 65, 65, 64, 64, 62, 62, 60],     # 满天都是小星星（重复）
        "note_dur_ms": 500,
        "lyric_lines": [
            {"text": "一闪一闪亮晶晶", "from": 0, "to": 6},
            {"text": "满天都是小星星", "from": 7, "to": 13},
            {"text": "挂在天空放光明", "from": 14, "to": 20},
            {"text": "好像许多小眼睛", "from": 21, "to": 27},
            {"text": "一闪一闪亮晶晶", "from": 28, "to": 34},
            {"text": "满天都是小星星", "from": 35, "to": 41},
        ],
        "license": "公有领域（法国民谣 Ah! vous dirai-je, maman，18 世纪）",
    },
    "REF_ODE_1": {
        "title": "欢乐颂 · 第一句",
        "midi": [64, 64, 65, 67, 67, 65, 64, 62],
        "note_dur_ms": 500,
        "lyric_lines": [{"text": "欢乐女神圣洁美丽", "from": 0, "to": 7}],
        "license": "公有领域（贝多芬，1827 年去世）",
    },
    "REF_TWINKLE_6B": {
        # ★★ 带**句间换气**的六句版（2026-10-02 加）—— 推荐用这个。
        #
        #   作者原话：「每句之间的间隔太短了，唱歌是需要换气的」。
        #
        #   为什么必须写进参考：换气会在每句末尾插进 ~0.7 秒静音，
        #   6 句就是 **~3.5 秒的累积延迟**。而 `tempo_ratio` 量的是**总时长**
        #   ⇒ 换气会被算成"你慢了 17%"。**这不是演唱问题，是参考缺了换气位。**
        #
        #   参数：每句 7 音 · 每音 500ms · 句间 700ms（唱一句 3.5s + 换气 0.7s）。
        #   跨度 = 42×500 + 5×700 = **24500ms**（旧版 21000ms）。
        #
        #   ⚠️ 与 `REF_TWINKLE_6`（无换气，21000ms）**并存、不替换**：
        #      旧那版是合成素材（`_constructed_synth.py`）与前面全部验证的依据，
        #      换了会把那些结论全部推翻。真实人声用这一版。
        "title": "小星星 · 六句（含句间换气）",
        "midi": [60, 60, 67, 67, 69, 69, 67,      # 一闪一闪亮晶晶
                 65, 65, 64, 64, 62, 62, 60,      # 满天都是小星星
                 67, 67, 65, 65, 64, 64, 62,      # 挂在天空放光明
                 67, 67, 65, 65, 64, 64, 62,      # 好像许多小眼睛
                 60, 60, 67, 67, 69, 69, 67,      # 一闪一闪亮晶晶（重复）
                 65, 65, 64, 64, 62, 62, 60],     # 满天都是小星星（重复）
        "note_dur_ms": 500,
        "phrase_len": 7,
        "phrase_gap_ms": 700,
        "lyric_lines": [
            {"text": "一闪一闪亮晶晶", "from": 0, "to": 6},
            {"text": "满天都是小星星", "from": 7, "to": 13},
            {"text": "挂在天空放光明", "from": 14, "to": 20},
            {"text": "好像许多小眼睛", "from": 21, "to": 27},
            {"text": "一闪一闪亮晶晶", "from": 28, "to": 34},
            {"text": "满天都是小星星", "from": 35, "to": 41},
        ],
        "license": "公有领域（法国民谣 Ah! vous dirai-je, maman，18 世纪）",
    },
}


def load_builtin(reference_id: str, first_start_ms: int = 0) -> ReferenceMelody:
    """载入内置曲目。

    first_start_ms：让参考与录音从同一起点计时。
    合成/测试音频常带前导静音，不给这个偏移会让所有 onset_dev 凭空多出那段静音。
    """
    spec = BUILTIN[reference_id]
    return ReferenceMelody.from_midi_list(
        spec["midi"], spec["note_dur_ms"], reference_id, spec["title"], mode="A",
        first_start_ms=first_start_ms,
        # ★ 句间换气（2026-10-02）：只有声明了 `phrase_len` 的曲目才加间隔。
        #   旧曲目没有这两个键 ⇒ 取 0 ⇒ 行为与以前**完全一致**（不改动历史结论）。
        phrase_len=int(spec.get("phrase_len", 0) or 0),
        phrase_gap_ms=int(spec.get("phrase_gap_ms", 0) or 0),
        lyric_lines=spec.get("lyric_lines") or [],
    )


# ══════════════════════════════════════════════════════════════
# 稳定段音高 —— 解决「滑音被判成走音」
# ══════════════════════════════════════════════════════════════
def stable_pitch(note, f0, sr, hop_length=PRECISE_HOP, params=None):
    """取音符【稳定段】的中位音高（MIDI），而不是整个音符的。

    为什么要这么做：
      实测放大图上看到，某个音符横条在 MIDI 55，但音高曲线大部分在 49–50
      —— 因为它开头有一段从 49 滑到 55 的过程（scoop）。
      拿整个音符的中位数会把滑音算进去，判成"唱高了"。

    做法：跳过开头的 scoop_skip 比例（但有绝对上限，短音符不能削太多），
          取剩余部分的中位数。

    返回 (pitch_midi, stable_start_ms, stable_end_ms, voiced_ratio)
        全为 (None, ...) 表示这段没有有效音高
    """
    p = {**DEFAULTS, **(params or {})}
    frame_ms = hop_length / sr * 1000.0
    dur = note.end_ms - note.start_ms
    skip = min(dur * p["scoop_skip_ratio"], p["scoop_skip_max_ms"])
    skip = max(0.0, min(skip, dur - p["scoop_skip_min_ms"]))

    a_ms = note.start_ms + skip
    b_ms = note.end_ms
    i0 = max(0, int(round(a_ms / frame_ms)))
    i1 = max(i0 + 1, int(round(b_ms / frame_ms)))
    i1 = min(i1, len(f0))
    if i1 <= i0:
        return None, int(a_ms), int(b_ms), 0.0
    seg = np.asarray(f0[i0:i1], dtype=float)
    ok = np.isfinite(seg) & (seg > 0)
    if not ok.any():
        return None, int(a_ms), int(b_ms), 0.0
    return (float(np.median(hz_to_midi(seg[ok]))), int(a_ms), int(b_ms),
            float(ok.mean()))


# ══════════════════════════════════════════════════════════════
# DTW 对齐
# ══════════════════════════════════════════════════════════════
def build_cost_matrix(sung_pitch, ref_pitch, sung_t, ref_t, params=None):
    """代价矩阵 C[i,j] = 把「演唱第 i 个音」配给「参考第 j 个音」的代价。

    两项相加：
      音高项：|Δ半音|          —— 主要依据
      时间项：|Δ归一化时间|     —— 辅助，防止音高相近但顺序错乱
    时间归一化到 [0,1]，让两项量级可比。
    """
    p = {**DEFAULTS, **(params or {})}
    s = np.asarray(sung_pitch, dtype=float)[:, None]
    r = np.asarray(ref_pitch, dtype=float)[None, :]
    dp = np.abs(s - r)

    st = np.asarray(sung_t, dtype=float)
    rt = np.asarray(ref_t, dtype=float)
    # ⚠️ numpy 2.0 移除了 ndarray.ptp()，必须用 np.ptp()
    st = (st - st.min()) / max(1e-9, float(np.ptp(st)))
    rt = (rt - rt.min()) / max(1e-9, float(np.ptp(rt)))
    dt = np.abs(st[:, None] - rt[None, :])

    return p["pitch_weight"] * dp + p["time_weight"] * dt * 6.0   # ×6 让时间项量级接近音高项


def _offset_peak(diffs, max_abs):
    """把「演唱音高 − 参考音高」的差值投票成一个**移调量**。

    ★ 为什么用直方图峰值而不是中位数：
      对角化失败时中位数会被离群值拖走 —— 实测 31 对里 10 对差值 −3、其余 21 对
      因失步而差值散乱，中位数仍侥幸是 −3，但那是运气，不是稳健。
      峰值（众数）只要求"存在一个占多数的偏移量"，对散乱值免疫。

    分箱用 0.25 半音：真·移调是整数半音，但用户也可能整首歌都没唱准；
      0.25 的粒度够细（能看出 0.5 半音的移调），又不会把 ±5 音分的音准抖动
      拆到不同箱里。
    """
    d = np.asarray([x for x in diffs if x is not None and np.isfinite(x)], dtype=float)
    d = d[(d >= -max_abs) & (d <= max_abs)]
    if d.size == 0:
        return None
    binw = 0.25
    idx = np.round(d / binw)
    vals, counts = np.unique(idx, return_counts=True)
    if vals.size == 0:
        return None
    best = int(vals[int(np.argmax(counts))])
    return float(np.clip(best * binw, -max_abs, max_abs))


def _resid_median_abs(pairs, sp, rp):
    """给定配对，算「去掉整体移调后」的中位绝对残差（半音）。

    这是判断"这次配对到底对不对"的正确口径：残差小 = 旋律走向对得上。
    """
    if not pairs:
        return None
    d = np.array([sp[i] - rp[j] for i, j in pairs if
                  np.isfinite(sp[i]) and np.isfinite(rp[j])], dtype=float)
    if d.size == 0:
        return None
    off = float(np.floor(np.median(d) + 0.5))
    return float(np.median(np.abs(d - off)))


def dtw_align(sung, ref, f0=None, sr=None, hop_length=PRECISE_HOP, params=None):
    """DTW 对齐演唱音符与参考音符。

    返回 (pairs, info)
      pairs : [(sung_idx, ref_idx), ...]  按演唱顺序
      info  : {method, cost, path_len, match_ratio, mean_pitch_diff, ok}

    ★★ 两趟对齐（2026-09-23 修）：**先粗估移调量，再对齐**。
      踩过的坑：原来的代价矩阵用【绝对音高】`|s − r|`。整段移调后每个格子都抬高
      同一个常数（如 3 个半音），代价面被整体压平 —— DTW 失去区分度，路径开始乱走。
      实测（`experiments/_why_transpose.py`，素材整体降 3 个半音）：
        前 10 个音配对完美（差 −3.0、偏差 0 音分），
        **第 10 个音起失步**，配到错误的参考音上：
        偏差 280/260/320 音分、起始偏差一路漂到 2821ms、ref_coverage 只剩 0.677。
        后果是音准分从 98 掉到 **0**，而演唱其实一个音都没错。
      这不是精度问题：模式 B 的参考就是【用户自己第一遍的调】，用户第二遍换个调
      起唱太正常了，所以这条路必须走得通。

      做法：第 1 趟用未移调的矩阵对齐，从配对差值里投票出粗移调量 s；
      第 2 趟用 `sp − s` 对齐（矩阵重新变得有区分度）；
      第 3 趟用「中位差值」做小数精修 —— 但只在它确实更好时才采纳。

      挑选标准是**残差**而不是代价：移调会让 `D[-1,-1]` 凭空变大，用代价比会
      系统性偏向未移调那一趟（正是要修的那个 bug）。
    """
    p = {**DEFAULTS, **(params or {})}
    if not sung or not ref.notes:
        return [], {"method": "NONE", "ok": False, "reason": "音符或参考为空"}

    # 用稳定段音高做对齐依据（没有 f0 时退回音符自身的音高）
    if f0 is not None and sr is not None:
        sp = [stable_pitch(n, f0, sr, hop_length, p)[0] for n in sung]
    else:
        sp = [n.pitch_midi for n in sung]
    rp = [r.pitch_midi for r in ref.notes]

    # 有效音高缺失的音符：用邻近插值补一个，否则矩阵里会出现 NaN
    sp = _fill_missing(sp)
    if sp is None:
        return [], {"method": "NONE", "ok": False, "reason": "演唱音符全部没有有效音高"}

    st = [n.start_ms for n in sung]
    rt = [r.start_ms for r in ref.notes]
    n_ref = len(ref.notes)
    max_abs = float(p.get("max_pitch_offset", 18.0))

    def _run(shift):
        """跑一趟 DTW。shift 是要从演唱音高里扣掉的移调量。"""
        spx = np.asarray(sp, dtype=float) - float(shift)
        C = build_cost_matrix(spx, rp, st, rt, p)
        C = np.nan_to_num(C, nan=1e6, posinf=1e6, neginf=0.0)
        try:
            D, wp = librosa.sequence.dtw(C=C, subseq=False, backtrack=True)
        except Exception as e:  # noqa: BLE001
            return None, None, None, f"DTW 失败({e})"
        pr = [(int(i), int(j)) for i, j in wp[::-1]]
        pr = _dedup_pairs(pr, C)
        return pr, C, float(D[-1, -1]), None

    def _rank(pr):
        """残差 + 未覆盖惩罚。小者胜。

        两条口径上的讲究：
        ① 残差用**真中位数**（不取整），不是 `_resid_median_abs`。
           因为要比较的正是"粗估（整数半音）"和"精修（带小数）"这两趟 ——
           一旦先取整，两者的残差会算成一样，精修那一趟永远赢不了、白跑。
        ② 未覆盖参考音要罚：失步的那一趟往往靠"少配几个音"来压低残差
           （实测失步趟 coverage 0.677 vs 正确趟 1.0），不罚就会选错。
        """
        if not pr:
            return None
        d = np.array([sp[i] - rp[j] for i, j in pr
                      if np.isfinite(sp[i]) and np.isfinite(rp[j])], dtype=float)
        if d.size == 0:
            return None
        r = float(np.median(np.abs(d - np.median(d))))
        cov = len({j for _, j in pr}) / max(1, n_ref)
        return r + 0.05 * (1.0 - cov)

    # ── 第 1 趟：未移调 ──
    pairs0, C0, cost0, err0 = _run(0.0)
    if err0:
        return _equal_division(len(sung), n_ref, err0)
    cands = [(pairs0, C0, cost0, 0.0)]

    # ── 第 2 趟：用粗估移调量 ──
    s_coarse = _offset_peak([sp[i] - rp[j] for i, j in pairs0], max_abs)
    if s_coarse:
        pairs1, C1, cost1, err1 = _run(s_coarse)
        if not err1:
            cands.append((pairs1, C1, cost1, s_coarse))

            # ── 第 3 趟：小数精修 ──
            #   移调不一定是整数半音（用户可能就是没唱到那个调），小数部分留着
            #   会让每个音都凭空多出几音分偏差。用中位差值把它量出来。
            #   只在确实降低残差时才采纳，避免"越修越偏"。
            d1 = np.array([sp[i] - rp[j] for i, j in pairs1], dtype=float)
            if d1.size:
                s_fine = float(np.median(d1))
                if abs(s_fine - s_coarse) > 0.05 and abs(s_fine) <= max_abs:
                    pairs2, C2, cost2, err2 = _run(s_fine)
                    if not err2:
                        cands.append((pairs2, C2, cost2, s_fine))

    # ── 选最好的一趟 ──
    ranked = [(rk, k) for k, c in enumerate(cands)
              if (rk := _rank(c[0])) is not None]
    if not ranked:
        pairs, C, cost, shift = cands[0]
    else:
        ranked.sort()
        shift = cands[ranked[0][1]][3]
        pairs, C, cost, _ = cands[ranked[0][1]]

    diffs = [abs(sp[i] - rp[j]) for i, j in pairs]
    info = {
        "method": "DTW",
        "cost": cost,
        "path_len": len(pairs),
        "match_ratio": len(pairs) / max(1, len(sung)),
        # ★ 参考侧覆盖率：参考音符里有多少个被匹配上了。
        #   这才是"对齐好不好"的正确口径，见下方质量判定的说明。
        "ref_coverage": len({j for _, j in pairs}) / max(1, n_ref),
        "mean_pitch_diff": float(np.mean(diffs)) if diffs else None,
        "max_pitch_diff": float(np.max(diffs)) if diffs else None,
        # ★ 这一趟实际扣掉的移调量。下面算残差/偏移时必须用**同一个值**，
        #   否则会出现"按移调对齐、却按未移调算偏差"的自相矛盾。
        "align_shift": float(shift),
        "ok": True,
    }

    # ── 质量判定 ──────────────────────────────────────────────
    # ⚠️ 2026-09-19 修正：原来的两条判据都会误伤【正常演唱】，必须改。
    #
    # 误伤①：`match_ratio = 配对数 / 演唱音符数` 下限 0.5。
    #   `_dedup_pairs` 强制一对一，所以配对数**最多等于参考音符数**。
    #   参考只有 7 个音（小星星第一句）时，唱 29 个音最多配出 7 对
    #   → match_ratio ≤ 7/29 = 0.24，**恒定低于阈值，必然降级**。
    #   而且用户把同一句多唱几遍是完全合理的（"再唱一遍"），不该因此判失败。
    #   → 改用【参考侧覆盖率】：参考的每个音有没有被唱到。
    #     用户只唱了一部分 → 覆盖率低（合理降级）；
    #     用户唱了好几遍 → 覆盖率仍是 1.0（正确放行）。
    #
    # 误伤②：`mean_pitch_diff > max_pitch_jump*0.5`（即 6 半音）判"不是同一首"。
    #   实测（`experiments/_why_degraded.py`）：某同学把《小星星》一个音不差地
    #   唱低了 11 个半音，DTW 配对完美 (0,0)…(6,6)、match_ratio=1.00，
    #   却因为"平均音高差 11.1 半音"被判成"唱的和参考不是同一首"。
    #   **没唱到参考那个调 ≠ 唱错歌** —— 男声比 C4–A4 低一个八度是常态。
    #   → 改成【移调无关】判据：先求最佳常数移调 offset = median(演唱 − 参考)，
    #     再看**去掉 offset 之后的残差**。残差小 = 旋律走向对得上（只是调不同）。
    #     实测标定（`experiments/_calibrate_gate2.py`）：
    #       真实小星星录音  中位绝对残差 1.80 / 1.95 / 2.60 半音
    #       音阶爬行（负例）              4.00 半音
    #     → 阈值取 3.0 半音。
    #
    # 顺带产出：`pitch_offset_semitones` 写进 info，
    #   让上层能告诉用户"你比参考低了 11 个半音"（这是有用的反馈，不是错误）。
    if pairs:
        d = np.array([sp[i] - rp[j] for i, j in pairs], dtype=float)
        # ★ offset 必须取【整数半音】：移调是半音的整数倍，
        #   小数部分（如 +0.03 半音 = 3 音分）是演唱者真实的音准表现，
        #   必须留给 pitch_cents_dev 去度量，不能被当成移调扣掉。
        #   踩过的坑①：直接取 median（=0.03）会把每个音都平移 3 音分，
        #   导致合成演示的音准分从 93.7 掉到 92.9 —— 凭空改了分数，而那是回归基线。
        #   踩过的坑②：**不要用 np.round** —— 它是"银行家舍入"（四舍六入五取偶），
        #   np.round(0.5) = 0 而不是 1。实测移调 0.5 半音时 offset 被算成 0，
        #   移调没被扣除，音准分仍是 50。改用 floor(x + 0.5) 才是常规四舍五入。
        offset = float(np.floor(np.median(d) + 0.5))
        resid = d - offset
        info["pitch_offset_semitones"] = float(offset)
        info["residual_median_abs"] = round(float(np.median(np.abs(resid))), 3)
    else:
        info["pitch_offset_semitones"] = None
        info["residual_median_abs"] = None

    if info["ref_coverage"] < p["min_match_ratio"]:
        info["ok"] = False
        info["reason"] = (f"参考只有 {info['ref_coverage']:.0%} 的音被唱到"
                          f"（唱的和参考可能不是同一首）")
    elif info["pitch_offset_semitones"] is not None and \
            abs(info["pitch_offset_semitones"]) > p["max_pitch_offset"]:
        info["ok"] = False
        info["reason"] = (f"整体音高偏离 {info['pitch_offset_semitones']:+.1f} 半音，"
                          f"超出参考音域适配范围")
    elif info["residual_median_abs"] is not None and \
            info["residual_median_abs"] > p["max_residual_semitone"]:
        info["ok"] = False
        info["reason"] = (f"去移调后旋律仍对不上（中位残差 "
                          f"{info['residual_median_abs']:.1f} 半音 > "
                          f"{p['max_residual_semitone']}）")
    return pairs, info


def _fill_missing(vals):
    """用邻近值插值补上 None（音高缺失的音符）。返回 np.array 或 None。"""
    v = np.array([np.nan if x is None else float(x) for x in vals], dtype=float)
    ok = np.isfinite(v)
    if not ok.any():
        return None
    if not ok.all():
        v = np.interp(np.arange(len(v)), np.flatnonzero(ok), v[ok])
    return v


def _dedup_pairs(pairs, C):
    """把 DTW 路径整理成【一对一】配对。

    DTW 路径天然是多对多的（一个音符可能匹配多个参考音）。
    旧实现先按 j 选最佳 i，再按 i 去重 —— 会导致某个 i 的最佳 j 被别的 i 占了，
    于是这个 i 被整个丢掉（实测 7 个音符会丢 1 个，对齐比掉到 0.88，
    进而把平均偏差带偏）。

    正确做法：两个方向各做一次贪心，保证任意 i、j 都最多出现一次。
    """
    # ① 每个演唱音符只保留代价最小的那个参考
    best_for_i = {}
    for i, j in pairs:
        if i not in best_for_i or C[i, j] < C[i, best_for_i[i]]:
            best_for_i[i] = j
    # ② 每个参考音符也只允许被一个演唱音符占用（冲突时取代价小者）
    best_for_j = {}
    for i, j in best_for_i.items():
        if j not in best_for_j or C[i, j] < C[best_for_j[j], j]:
            best_for_j[j] = i
    return sorted((i, j) for j, i in best_for_j.items())


def _equal_division(n_sung, n_ref, reason=""):
    """降级方案：等长分段对齐。契约里 align_method 允许 'EQUAL_DIVISION'。"""
    k = min(n_sung, n_ref)
    pairs = [(int(round(i * (n_sung - 1) / max(1, k - 1))),
              int(round(i * (n_ref - 1) / max(1, k - 1)))) for i in range(k)]
    return pairs, {"method": "EQUAL_DIVISION", "ok": True, "reason": reason,
                   "match_ratio": k / max(1, n_sung)}


# ══════════════════════════════════════════════════════════════
# 主入口：产出偏差
# ══════════════════════════════════════════════════════════════
def align_to_reference(notes, ref, f0=None, sr=None, hop_length=PRECISE_HOP,
                       params=None):
    """对齐并计算偏差。

    返回 (notes_with_dev, info)
      notes_with_dev : 新列表，每个音符补上
          ref_note_index / pitch_cents_dev / onset_dev_ms /
          ref_pitch_midi / stable_pitch_midi / align_method
      info           : 对齐质量信息
    """
    p = {**DEFAULTS, **(params or {})}
    if not notes:
        return [], {"method": "NONE", "ok": False, "reason": "没有音符"}

    pairs, info = dtw_align(notes, ref, f0=f0, sr=sr,
                            hop_length=hop_length, params=p)
    method = info.get("method", "DTW")
    # 匹配率太低或平均音高差过大 → 退回等长分段
    if not info.get("ok", False) and method == "DTW":
        pairs, info2 = _equal_division(len(notes), len(ref.notes),
                                       info.get("reason", ""))
        method = "EQUAL_DIVISION"
        info = {**info2, "dtw_reason": info.get("reason")}

    out = []
    pairs_sorted = sorted(pairs)
    # ★ EQUAL_DIVISION 表示"没法可靠地配对"（唱的和参考不是一回事）。
    #   此时如果照常算偏差，会得到 -1120 音分 / +5467ms 这种垃圾值，
    #   然后被 scoring 拿去算分 —— 实测用户唱一首非模板的歌唱出【音准 0 分】，
    #   而真相是"我们根本没法判断"。
    #   契约 §3.2 明确：null 表示"本次未评估"，**绝不用 0 代替**。
    #   所以降级后一律输出 None，让下游走 null 分支（scoring 已能正确处理全 None）。
    degraded = (method == "EQUAL_DIVISION")
    # ★ 整体移调量：演唱者相对参考唱高/唱低了多少个半音。
    #   用来把"调不同"从"音不准"里剥出去（见 dtw_align 的质量判定说明）。
    #   降级时不可信，置 0 不做修正。
    pitch_offset = None if degraded else info.get("pitch_offset_semitones")
    for k, n in enumerate(notes):
        m = _clone_note(n)
        m.align_method = method
        hit = next(((i, j) for i, j in pairs_sorted if i == k), None)
        if hit is None or degraded:
            m.ref_note_index = None
            m.pitch_cents_dev = None
            m.onset_dev_ms = None
            out.append(m)
            continue

        _, j = hit
        rn = ref.notes[j]
        m.ref_note_index = j
        m.ref_pitch_midi = rn.pitch_midi

        # 音准偏差：用【稳定段】音高，避开滑音
        sp, s_a, s_b, vr = (n.pitch_midi, n.start_ms, n.end_ms, 1.0)
        if f0 is not None and sr is not None:
            sp, s_a, s_b, vr = stable_pitch(n, f0, sr, hop_length, p)
        m.stable_pitch_midi = sp
        m.stable_voiced_ratio = vr
        # ⚠️ 单位陷阱：这里两个量都是 MIDI 音符号，不是频率。
        #    1 个半音 = 100 音分，所以系数是 100。
        #    （1200 * log2(f/f_ref) 是【频率比】的公式，别混用。
        #      曾经写成 1200，结果 30 音分被算成 360 音分，差 12 倍。）
        #
        # ★ 2026-09-19：先扣掉整体移调量（pitch_offset），再算音分偏差。
        #   理由："没唱到参考那个调"与"在那个调里唱不准"是两件事。
        #   男声比 C4–A4 低一个八度是常态，不扣的话整段会显示 -1100 音分，
        #   音准分直接 0 —— 那不是"跑调"，那是音域不同。
        #   移调带来的调性差异由 meta.pitch_offset_semitones 单独报告。
        ref_for_dev = rn.pitch_midi + (pitch_offset or 0.0)
        m.pitch_cents_dev = (round(100.0 * (sp - ref_for_dev), 1)
                             if sp is not None else None)

        # 起始偏差：演唱起点 − 参考起点。**原始值，不做任何平滑**
        # 节奏分的「准不准 / 稳不稳 / 弹性」三分量由 scoring.py 从这条序列里拆
        m.onset_dev_ms = float(n.start_ms - rn.start_ms)
        # ★ 参考起点也留下来。为什么需要：`features.tempo_ratio()` 要拿
        #   "偏差 对 参考时刻" 做线性拟合才能把**整体速度比**估出来，
        #   而只有 onset_dev_ms 是拟合不出斜率的（它是因变量）。
        #   虽然理论上 `rn.start_ms = m.start_ms − onset_dev_ms` 可以反推，
        #   但那是第二个副本 —— 一旦以后有人改了 onset_dev 的定义就会静默算错。
        m.ref_start_ms = float(rn.start_ms)
        out.append(m)

    got = [m for m in out if m.pitch_cents_dev is not None]
    info = {**info, "aligned": len(got), "total": len(out),
            "method": method, "align_method": method}
    return out, info


def _clone_note(n):
    """浅拷贝一个 Note，避免污染调用方的对象。需要的话补上对齐用的字段。"""
    import copy
    m = copy.copy(n)
    for attr in ("ref_note_index", "ref_pitch_midi", "stable_pitch_midi",
                 "stable_voiced_ratio", "pitch_cents_dev", "onset_dev_ms",
                 "ref_start_ms", "align_method"):
        if not hasattr(m, attr):
            setattr(m, attr, None)
    return m


# ══════════════════════════════════════════════════════════════
# 模板质量门控（模式 B 专用）
# ══════════════════════════════════════════════════════════════
# 设计依据：模式 B 拿用户第一遍的演唱当参考，评的是【一致性】而不是
# 【绝对正确性】—— 这是模式的固有性质，论文里必须写明适用范围。
# 但如果模板本身就不稳，"和第二遍一致"就失去意义了：模板是歪的，越像越歪。
#
# 阈值依据：实测正常演唱的音符内音高标准差中位约 0.8 半音；
# 超过 1.2 基本是"没站稳"而不是"有颤音"。
#
# ⚠️ 曾经有一条「音符时长离散度」判据，已经删掉：
#    它测的是【乐曲本身的节奏型】（一首歌本来就该有长音短音），
#    不是【演唱的不稳】。实测把一段正常的真实录音判成了"节奏较散"。
#    更根本的是：模式 B 里没有参考，根本无法判断时值稳不稳 ——
#    模板自己就是基准。所以时值这一维在模板质量评估里不可用。
TEMPLATE_GATE = {
    "min_notes": 4,              # 音符太少 → 参考不可靠
    "max_pitch_std": 1.20,       # 音符内音高标准差上限（半音）
    "min_voiced_ratio": 0.55,    # 有有效音高的音符占比下限
    "min_long_notes": 1,         # 至少要有一个够长的音
}
TEMPLATE_GATE_LABELS = {"ok": "可用", "warn": "勉强可用", "reject": "不可用"}


def assess_template_quality(notes, params=None) -> dict:
    """评估「跟唱模板」本身够不够格当参考。

    分三级而不是简单的通过/拒绝：
      ok     直接可用
      warn   可用但节奏偏散，对比结果可信度打折
      reject 模板本身不稳，建议重录或改用教材曲目模式

    返回 {level, level_label, reasons[], blocking[], metrics{}, advice}
    """
    p = {**TEMPLATE_GATE, **(params or {})}
    if not notes:
        return {"level": "reject", "level_label": TEMPLATE_GATE_LABELS["reject"],
                "reasons": ["没有音符"], "blocking": ["没有音符"],
                "metrics": {}, "advice": "请重录一遍。"}

    stds = [n.pitch_std for n in notes if n.pitch_std is not None]
    voiced = [n.voiced_ratio for n in notes if n.voiced_ratio is not None]
    long_notes = sum(1 for n in notes if n.long_enough_for_vibrato)

    pitch_std_med = float(np.median(stds)) if stds else None
    voiced_mean = float(np.mean(voiced)) if voiced else None

    blocking = []
    if len(notes) < p["min_notes"]:
        blocking.append(f"音符太少（{len(notes)} 个，少于 {p['min_notes']} 个）")
    if pitch_std_med is not None and pitch_std_med > p["max_pitch_std"]:
        blocking.append(f"音高不稳（音符内波动中位 {pitch_std_med:.2f} 半音，"
                        f"上限 {p['max_pitch_std']}）")
    if voiced_mean is not None and voiced_mean < p["min_voiced_ratio"]:
        blocking.append(f"有效音高占比过低（{voiced_mean * 100:.0f}%）")
    if long_notes < p["min_long_notes"]:
        blocking.append("没有足够长的音，音高与颤音都测不准")

    level = "reject" if blocking else "ok"
    advice = {
        "ok": "模板质量良好，可以直接用。",
        "reject": "这份模板本身不够稳，拿它当基准会「越像越歪」。"
                  "建议重录一遍，或改用「跟着标准曲目唱」模式。",
    }[level]

    return {
        "level": level,
        "level_label": TEMPLATE_GATE_LABELS[level],
        "reasons": blocking,
        "blocking": blocking,
        "metrics": {
            "note_count": len(notes),
            "pitch_std_median": round(pitch_std_med, 3) if pitch_std_med is not None else None,
            "voiced_ratio_mean": round(voiced_mean, 3) if voiced_mean is not None else None,
            "long_notes": long_notes,
        },
        "advice": advice,
    }


# ══════════════════════════════════════════════════════════════
# 对齐质量诊断
# ══════════════════════════════════════════════════════════════
def align_stats(aligned, info) -> dict:
    """对齐质量统计。决定音准分和节奏分能不能算。"""
    devs = np.array([m.pitch_cents_dev for m in aligned
                     if m.pitch_cents_dev is not None], dtype=float)
    onsets = np.array([m.onset_dev_ms for m in aligned
                       if m.onset_dev_ms is not None], dtype=float)
    st = {
        "method": info.get("method"),
        "aligned": len(devs),
        "total": len(aligned),
        "align_ratio": len(devs) / max(1, len(aligned)),
        "pitch_dev": {
            "mean": float(np.mean(devs)) if devs.size else None,
            "std": float(np.std(devs)) if devs.size else None,
            "median_abs": float(np.median(np.abs(devs))) if devs.size else None,
            "max_abs": float(np.max(np.abs(devs))) if devs.size else None,
        },
        "onset_dev": {
            "mean": float(np.mean(onsets)) if onsets.size else None,
            "median": float(np.median(onsets)) if onsets.size else None,
            "std": float(np.std(onsets)) if onsets.size else None,
            "max_abs": float(np.max(np.abs(onsets))) if onsets.size else None,
        },
        "dtw_reason": info.get("dtw_reason"),
    }
    ok = (st["align_ratio"] >= 0.6 and devs.size >= 3)
    st["ok"] = ok
    st["reason"] = ("对齐良好" if ok else
                    f"对齐不足：仅 {st['aligned']}/{st['total']} 个音符匹配上")
    return st
