"""音符切分：把逐帧音高变成逐个音符。

这是六个表现力指标的地基。切分质量直接决定后面能不能算：
  · 颤音需要【稳定的长音】—— 切碎了就算不出来
  · rubato 需要【准确的音符起始时间】
  · 力度起伏需要【按乐句分组】

━━━ 为什么不能用常规 onset 检测 ━━━
歌唱不是打击乐。三个难点：
  1. 连音（legato）：音符之间没有静音间隔，onset 很弱甚至没有
  2. 滑音（portamento）：音高连续滑动，没有明确的切换点
  3. 颤音（vibrato）：音高本身在 4–8 Hz 抖动，极易被误判成"换音"

所以本模块用【双路检测再合并】：
  路径 A：能量突变（librosa onset）—— 抓"字头"
  路径 B：音高台阶变化             —— 抓"换音"
两条路的候选点合并后，再做【最小音符时长约束】，防止切碎。

━━━ 关键设计：两套音高序列 ━━━
  · raw_f0    —— 原始逐帧音高，用于算颤音等指标（必须保留抖动）
  · smooth_f0 —— 长窗中值滤波后的音高，仅用于找换音点（去掉颤音干扰）
绝不能用平滑后的音高去算颤音，那样会把要测的东西滤掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import librosa
import numpy as np

from .pitch import (
    FRAME_LENGTH,
    PRECISE_HOP,
    extract_f0_pyin,
    hz_to_midi,
    mark_saturated,
    midi_to_hz,
)

# ── 默认参数（可调，答辩时要能讲清每个值怎么来的）────────────────
DEFAULTS = {
    # 音符边界
    "min_duration_ms": 120,      # 最小音符时长。短于此的碎片会被合并。
                                 # 依据：实测人声稳定段最短约 0.12–0.13 s
    "min_sustain_ms": 70,        # 换音后新音高需持续这么久才认作真换音
    "semitone_threshold": 0.8,   # 音高变化超过此值才考虑是换音
                                 # 依据：小于 0.8 半音基本是颤音/滑音，不是换音
    "smooth_ms": 260,            # 找换音点时的中值滤波窗长
                                 # 依据：颤音周期约 175 ms（5.7 Hz）
    # ★ 跨未发声帧找换音。原实现只比相邻帧，跨过 unvoiced 就漏 ——
    #   实测 14 个真实换音漏掉 11 个（详见 detect_pitch_changes 的文档）。
    #   但 gap 太长就不跨：那是乐句间停顿，不是换音。
    "pitch_gap_max_ms": 400,
    # ★ 振荡抑制：中值滤波压不住低频颤音（4Hz 周期 250ms），
    #   必须再验一步「音高会不会回到原处」—— 颤音会回来，真换音不会。
    #   实测教训：0.9 秒的合成颤音被切成了 139~209ms 的碎片，
    #   根因就是颤音被当成了换音。
    "osc_return_ms": 520,        # 回看窗：约 2 个周期（覆盖到 4 Hz 颤音）
    "osc_reject_ratio": 0.45,    # 回看窗内同时出现接近「变化前」和「变化后」的音高
                                 # 且幅度超过变化量的此比例 → 判定为振荡，不切

    # ★ onset 有效性校验：防止颤音造成的假 onset
    #   谱通量类 onset 检测有个经典失效模式：音高来回摆动时，
    #   能量在频谱 bin 之间搬运，会产生虚假的"能量突变"。
    #   实测：0.9 秒的 5.7Hz 颤音被检出 6 个 onset，间隔正好 174ms = 颤音周期。
    #   判别依据：真实 onset 前面必然有能量低谷（辅音或停顿），
    #             颤音假 onset 前面能量一直很高。所以比「突变前后的能量比」。
    "onset_rise_ratio": 1.30,    # onset 处能量至少要比前面高这么多倍才算数
    "onset_rise_look_ms": 45,    # 前后各看这么长的窗
    "onset_pitch_tol": 0.10,     # 判定音高「穿越基准线」的容差（半音）
                                 # 依据：要小于最浅颤音的幅度（0.3 半音峰峰 → ±0.15），
                                 # 设太大条件根本不触发（曾设 0.45，等于没做）
        "osc_min_crossings": 1,      # 窗内穿越基准线这么多次 → 判定为振荡
                                 # ★ 一开始设 3（想着"至少一个完整周期"），太松：
                                 #   实测短颤音音符里只穿越 1~2 次，假 onset 全溜过去了。
                                 #   改成 1 之后，在合成演示上完美分离（7 个真全留、9 个假全剔）。
                                 #   理由：真换音的音高停在新水平上，穿越次数是 0；
                                 #   只要穿越过就说明在摆动。
    "onset_backtrack": True,     # 把 onset 回退到能量上升起点，更接近真实字头
    "onset_delta": 0.06,         # onset 检测灵敏度
    "rms_gate_db": -42.0,        # 静音门限（相对峰值），低于此认为没在唱
    "bridge_gap_ms": 220,        # ★ 短间隙桥接：有声段之间小于此的间隙视为"同一口气"
                                 # 实测教训：擦音（x/s/sh/f）能量极低，会被静音门限
                                 # 切断。比如"选"字以 x 开头，门控轨里那个字被切成两半。
                                 # 桥接后有声段连续，门控轨就不会在字中间出现静音。
    "merge_gap_ms": 60,          # 相邻音符间隔小于此则合并
    "bridge_loud_db": -25.0,     # 缺口内能量高于此 → 明显有声音，无论多长都桥接
    "bridge_hard_max_ms": 600,   # 但再响也不桥接超过这个长度（防误吞长句间停顿）

    # ── 音符级质量控制（切完之后再过一遍，剔除"假音符"）──────
    # 实测教训：只靠全局静音门限不够。真实录音里气声、尾音衰减、
    # 背景噪声都会被切成"音符"，实测有 RMS 只有正常值 5% 的假音符，
    # 它们会污染音准分和力度动态范围。所以必须逐个音符再筛一遍。
    "rms_min_ratio": 0.18,       # 音符 RMS 至少达到【有声段 RMS 中位数】的此比例
    "voiced_min": 0.35,          # 音符内有有效音高的帧占比下限
    "pitch_std_max": 2.0,        # 【诊断用】音符内音高标准差上限（半音）。
                                 # 超过说明这一段里混了多个音高，即"切分不足"。
                                 #
                                 # ⚠️ 这个阈值**不再参与任何自动处理**：
                                 #   原先它驱动 `split_unstable_notes()`（把这种音符再切一刀），
                                 #   但那个函数**从未被调用过**，已删（2026-09-20, §46 相关）。
                                 #   真正解决"一个音符含两个音高"的是 detect_pitch_changes
                                 #   跨未发声帧找换音点（2026-09-19 修）——
                                 #   修完实测污染音符从 1 个降到 0 个。
                                 #   现在它只用来在排查工具里报警
                                 #   （见 experiments/probe_modeb_selfref.py 的 std>1.5 统计）。
}


@dataclass
class Note:
    """一个音符。字段与契约 §2.1 NoteSegment 对应（其余字段由 align/features 补）。"""
    note_index: int
    start_ms: int
    end_ms: int
    pitch_midi: Optional[float] = None      # 该音符的音高（稳定段中位数）
    pitch_std: Optional[float] = None       # 音符内音高标准差（半音）—— 颤音会抬高它
    rms_mean: Optional[float] = None
    rms_peak: Optional[float] = None
    voiced_ratio: Optional[float] = None    # 音符内有有效音高的帧占比
    # 下面是"切分质量"的诊断字段，不进契约，但决定六指标能不能算
    long_enough_for_vibrato: bool = False
    boundary_source: str = "unknown"        # "onset" | "pitch" | "both" | "silence"

    def to_contract(self) -> dict:
        """转成契约 §2.1 NoteSegment（本模块能填的部分）"""
        return {
            "note_index": self.note_index,
            "ref_note_index": None,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "pitch_midi": round(self.pitch_midi, 2) if self.pitch_midi is not None else None,
            "pitch_cents_dev": None,       # 由 align.py 填
            "onset_dev_ms": None,          # 由 align.py 填
            "rms_mean": round(self.rms_mean, 4) if self.rms_mean is not None else None,
            "rms_peak": round(self.rms_peak, 4) if self.rms_peak is not None else None,
            "onset_rise_ms": None,         # 由 features.py 填
            "vibrato_rate": None,          # 由 features.py 填
            "vibrato_depth": None,         # 由 features.py 填
            "centroid_mean": None,         # 由 features.py 填
            "jump_suppressed": False,
            "align_method": "DTW",
        }


# ══════════════════════════════════════════════════════════════
# 路径 A：能量突变（onset）
# ══════════════════════════════════════════════════════════════
def detect_onsets(y, sr, hop_length=PRECISE_HOP, delta=None, backtrack=None,
                  refine=False):
    """能量突变点，单位：秒。抓"字头"。

    ⚠️ **`refine` 默认为 `False` —— 亚帧细化试过，但它让结果更糟。**

    保留这段代码是为了留下**已排除的方案**和它的实测数据，不是为了用。

    ━━━ 为什么要试 ━━━

      `librosa.onset_detect(units="time")` 返回 `帧索引 × hop / sr`
      ⇒ 只能取 **11.61ms 的整数倍**（hop=256 @22050Hz）。实测：把同一段音频
      整体平移 0–10ms（**内容完全没变**），起音时刻的余数恒为 0，
      而检出的起音个数在 25/26/27 之间跳。根因定位见
      `experiments/_diag_phase_rootcause.py`（H1 成立、H2 成立、H3 不成立）。

    ━━━ 细化做了什么 ━━━

      对每个粗边界，在 `onset_strength` 包络上找附近极值帧，
      再用相邻三点做**抛物线插值**求亚帧位置。粗边界只当搜索起点，
      所以它跳一帧不该影响结果。实测确实生效了：位置余数不再是 0。

    ━━━ 为什么还是放弃它 ━━━

      验收判据（`experiments/_verify_A_acceptance.py`）显示结果**更糟**：

        | 指标（同一段音频、只做亚帧时移） | 改前 | 加细化后 |
        |---|---|---|
        | 段数取值          | [14,15,16]      | **[13,14,16,17,18]** |
        | rubato 跨度       | 88ms            | **494ms** |
        | （第 3 段）rubato | 274ms           | **822ms** |

      **为什么**：位置不再落在一个固定网格上 ⇒ 边界之间的**间距**变成连续量
      ⇒ `min_duration_ms` 的合并、`voiced_regions` 的边界、`pchg` 的配对
      这些**离散决策**更容易翻边。原来是"大家一起跳一帧"（跳得整齐），
      细化之后变成"各自跳不同的小数"（跳得零散），**段数反而散得更开**。

      ⇒ 这否定了一个我原先的假设：**"把位置变准"不等于"把结果变稳"。**
        稳定性来自**决策的连续性**，不是来自**坐标的精度**。

    ━━━ 下一步该往哪走（留给下次动手）━━━

      要做的是让**边界集合**稳定，而不是让**边界位置**变准。三个候选：
        ① 起音检测的峰值挑选加**滞后**（hysteresis）—— 幅度在阈值
           附近的峰不要时而选中、时而不选
        ② `min_duration_ms` 的合并改成**帧数**比较（而不是毫秒），
           让 ±1 帧的扰动无法翻边
        ③ 换更细的 hop 重算（代价：PYIN 的耗时按比例上升，
           40 秒音频会从约 5 秒涨到 20 秒）
      ⚠️ 动手前先跑 `_verify_A_acceptance.py` 拿基线，改完用同一个脚本比 ——
         这次就是靠它才发现"细化让结果更糟"。
    """
    delta = DEFAULTS["onset_delta"] if delta is None else delta
    backtrack = DEFAULTS["onset_backtrack"] if backtrack is None else backtrack
    onsets = librosa.onset.onset_detect(
        y=y, sr=sr, hop_length=hop_length, backtrack=backtrack, delta=delta, units="time",
    )
    onsets = np.asarray(onsets, dtype=float)
    if not refine or onsets.size == 0:
        return onsets

    # 能量上升沿包络（谱通量）——与 librosa 内部用的同一族特征，
    # 但这里我们要的是**连续的曲线**，好在上面做插值。
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    if env.size < 3:
        return onsets
    frame_s = hop_length / sr

    # ⚠️ 第一版这里写错了（2026-10-02），值得留：
    #    我用"以粗边界为中心、±0.75 帧"的**时间窗**去找极值 ——
    #    而窗宽正好 1.5 帧，**只装得下中心那一帧**，
    #    于是 `idx.size < 3` 恒成立 ⇒ 全程走保底分支 ⇒ 细化根本没生效。
    #    实测表现：余数仍然全是 0（等于没改）。
    #    ⇒ 正确做法是**在帧索引上找极值，再对相邻三点做抛物线插值**，
    #      而不是在时间上开窗。
    out = []
    for t in onsets:
        k = int(round(t / frame_s))
        if k <= 0 or k >= env.size - 1:
            out.append(float(t))
            continue
        # 在 k 附近 ±1 帧内取最大者作为峰（容忍粗边界与峰错开一帧）
        lo, hi = max(1, k - 1), min(env.size - 2, k + 1)
        k = max(range(lo, hi + 1), key=lambda i: env[i])
        y0, y1, y2 = float(env[k - 1]), float(env[k]), float(env[k + 1])
        denom = (y0 - 2.0 * y1 + y2)
        if abs(denom) < 1e-12:
            out.append(float(t))
            continue
        # 抛物线顶点相对 k 的偏移（单位：帧）
        shift = float(np.clip(0.5 * (y0 - y2) / denom, -1.0, 1.0))
        out.append(float((k + shift) * frame_s))
    return np.asarray(sorted(out), dtype=float)


# ══════════════════════════════════════════════════════════════
# 路径 B：音高台阶变化
# ══════════════════════════════════════════════════════════════
def _median_filter_nan(x, win):
    """对含 NaN 的序列做中值滤波：只跨有效点插值，滤波后还原 NaN 位置。"""
    x = np.asarray(x, dtype=float)
    if win < 3:
        return x.copy()
    win = win if win % 2 == 1 else win + 1
    idx = np.isfinite(x)
    if idx.sum() < win:
        return x.copy()
    filled = np.interp(np.arange(len(x)), np.flatnonzero(idx), x[idx])
    from scipy.signal import medfilt
    out = medfilt(filled, kernel_size=win)
    out[~idx] = np.nan
    return out


def _is_oscillation(midi_s, i, delta, frame_ms, look_ms, ratio):
    """判断 i 处的音高变化是不是【振荡】（颤音）而不是真的换音。

    ★ 为什么需要这一步：
      中值滤波的窗长受限于「不能模糊真实换音点」，所以压不住低频颤音
      （4 Hz 周期 250 ms，需要 ≥2 个周期共 500 ms 的窗才能压住）。
      但把窗加到 500 ms 会让换音点位置模糊掉。

      所以换个思路：**颤音会回来，真换音不会。**
      在变化点之后的一个回看窗里，如果音高既回到过「变化前」的水平附近，
      又到过「变化后」的水平附近，那它就是来回摆动 —— 振荡，不是换音。

    实测：0.9 秒的纯颤音原本被切成 139~209ms 的碎片，加了这一步之后不再被切。
    """
    a, b = midi_s[i], midi_s[i + 1]
    if not (np.isfinite(a) and np.isfinite(b)) or abs(delta) < 1e-6:
        return False
    n_win = max(2, int(round(look_ms / frame_ms)))
    j = min(len(midi_s), i + 2 + n_win)
    seg = midi_s[i + 1:j]
    seg = seg[np.isfinite(seg)]
    if seg.size < 3:
        return False
    tol = abs(delta) * ratio
    near_after = np.any(np.abs(seg - b) <= tol)     # 到过新水平
    near_before = np.any(np.abs(seg - a) <= tol)    # 又回到旧水平
    return bool(near_after and near_before)


def detect_pitch_changes(f0, sr, hop_length=PRECISE_HOP, **kw):
    """音高台阶变化点，单位：秒。

    做法：
      1. 转 MIDI
      2. 长窗中值滤波 —— 去掉颤音（周期约 175ms）和微抖动，只留真实换音
      3. 找 |相邻差| > 阈值 的位置 —— ★ **要跨过未发声帧**
      4. 验证：新音高必须持续 min_sustain 才认作真换音（防止滑音被切碎）

    ★ 2026-09-19 修正：原来只比较【相邻帧】，跨过任何未发声帧就漏掉换音。
      实测（`experiments/_diag_pitch_changes.py`，Screenrecorder…104322.wav）：
        22 秒里有 **14 个真实换音**，其中 **11 个跨越了 46–950ms 的未发声帧**
        （60.10 → nan×3 → 64.70，跳 4.6 半音）。
      原实现只检出 **3 个**，漏掉 11 个 —— 于是音符边界几乎全靠 onset 路径，
      出现"一个 1393ms 的音符里含两个音高"（std 3.37），
      交给 stable_pitch 就取到错误的音高，对齐随之全错。

      为什么会有未发声帧：唱到字尾/换气时，PYIN 判为 unvoiced。
      对**连音演唱**（一个字一个音、中间无停顿）这是常态，不是异常。
      所以跨 gap 找下一个有效帧才是正确做法。
      但 gap 太长（> pitch_gap_max_ms）就不跨 —— 那是乐句间停顿，不是换音。

    返回 (times_s, deltas_semitones)
    """
    p = {**DEFAULTS, **kw}
    frame_ms = hop_length / sr * 1000.0
    midi = hz_to_midi(f0)
    win = max(3, int(round(p["smooth_ms"] / frame_ms)))
    midi_s = _median_filter_nan(midi, win)

    n = len(midi_s)
    min_sus = max(1, int(round(p["min_sustain_ms"] / frame_ms)))
    th = p["semitone_threshold"]
    max_gap_frames = max(1, int(round(p["pitch_gap_max_ms"] / frame_ms)))

    # 下一个【有效】帧的下标（跨过未发声帧，但不超过 max_gap_frames）
    nxt = np.full(n, -1, dtype=int)
    last = -1
    for i in range(n - 1, -1, -1):
        nxt[i] = last
        if np.isfinite(midi_s[i]):
            last = i

    times, deltas = [], []
    i = 0
    while i < n - 1:
        a = midi_s[i]
        if not np.isfinite(a):
            i += 1
            continue
        j = nxt[i]
        if j < 0 or (j - i) > max_gap_frames:      # 没跨过有效帧 / gap 太长
            i += 1
            continue
        b = midi_s[j]
        d = b - a
        if abs(d) >= th:
            # 先排除振荡（颤音）：音高会回到原处的不算换音
            if _is_oscillation(midi_s, i, d, frame_ms,
                               p["osc_return_ms"], p["osc_reject_ratio"]):
                i += 1
                continue
            # 验证：变化后的音高是否持续
            k = min(n, j + min_sus)
            seg = midi_s[j:k]
            seg = seg[np.isfinite(seg)]
            if seg.size >= min_sus * 0.6:
                med = float(np.median(seg))
                if abs(med - a) >= th * 0.7:      # 确实停在别处
                    times.append(j * frame_ms / 1000.0)
                    deltas.append(float(med - a))
                    i = j + min_sus                # 跳过稳定段，避免同一个换音报多次
                    continue
        i += 1
    return np.asarray(times, dtype=float), np.asarray(deltas, dtype=float)


# ══════════════════════════════════════════════════════════════
# 有声段检测
# ══════════════════════════════════════════════════════════════
def validate_onsets(onsets, y, sr, f0, hop_length=PRECISE_HOP, params=None):
    """校验 onset 是否真实，剔除颤音造成的假 onset。

    ★ 失效模式：谱通量类 onset 检测对音高摆动敏感 —— 颤音时能量在频谱 bin
      之间来回搬运，被误判成"能量突变"。实测 0.9 秒 5.7Hz 颤音产生 6 个假 onset，
      间隔正好等于颤音周期（175ms）。

    ★ 判据必须【两个条件组合】，单用任何一个都会误伤：

        真实换音（连音）  能量无突变 + 音高停在新水平（不回来）  → 保留
        真实字头（辅音）  能量有明显上升                        → 保留
        颤音假 onset      能量无突变 + 音高变了又回来            → 剔除

      教训：一开始只用「能量比」判据，结果连音演唱的音符之间本来就没有
      能量低谷，把真实的换音点全滤掉了（合成《小星星》命中从 7/7 掉到 1/7）。
    """
    p = {**DEFAULTS, **(params or {})}
    if len(onsets) == 0:
        return np.asarray([], dtype=float)

    rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]
    midi = hz_to_midi(np.asarray(f0, dtype=float))
    frame_ms = hop_length / sr * 1000.0
    look = max(1, int(round(p["onset_rise_look_ms"] / frame_ms)))
    ratio = p["onset_rise_ratio"]
    win = max(2, int(round(p["osc_return_ms"] / frame_ms)))
    tol = p.get("onset_pitch_tol", 0.45)

    kept = []
    for t in onsets:
        i = int(round(t * 1000.0 / frame_ms))
        if i <= look or i + 2 >= len(midi):
            kept.append(t)
            continue

        # 条件①：能量是否明显上升（真实字头/换气）
        before = float(np.median(rms[max(0, i - look):i])) or 1e-12
        after = float(np.median(rms[i:min(len(rms), i + look)]))
        if after >= before * ratio:
            kept.append(t)
            continue

        # 条件②：音高是不是在【来回穿越】基准线（颤音特征）
        #   用「穿越次数」而不是「变没变、回没回」：
        #     颤音在一个窗里反复穿越基准线多次；
        #     真换音只穿越一次，然后停在新水平上。
        a_seg = midi[max(0, i - look):i]
        a_seg = a_seg[np.isfinite(a_seg)]
        seg = midi[i:min(len(midi), i + win)]
        seg = seg[np.isfinite(seg)]
        if a_seg.size == 0 or seg.size < 6:
            kept.append(t)
            continue
        a = float(np.median(a_seg))
        excursions = ((seg > a + tol) | (seg < a - tol)).astype(np.int8)
        crossings = int(np.sum(np.diff(excursions) != 0))
        if crossings >= p["osc_min_crossings"]:
            continue                      # 来回穿越 → 振荡 → 假 onset，剔除
        kept.append(t)
    return np.asarray(kept, dtype=float)


def voiced_regions(y, sr, hop_length=PRECISE_HOP, gate_db=None, bridge_ms=None):
    """返回 [(start_s, end_s), ...]，只保留有声音的区间。

    ★ 短间隙桥接（bridge_ms）：
      擦音（x / s / sh / f）和塞音闭塞段的能量极低，会被静音门限切断，
      导致一个字的中间出现"假静音"。后果是门控轨把字切成两半
      —— 实测用户就听出了这个问题（"选"字被切了一半）。
      所以把短于 bridge_ms 的间隙填上，视为同一口气。
    """
    gate_db = DEFAULTS["rms_gate_db"] if gate_db is None else gate_db
    bridge_ms = DEFAULTS["bridge_gap_ms"] if bridge_ms is None else bridge_ms

    rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]
    if rms.max() <= 0:
        return []
    thr = rms.max() * (10 ** (gate_db / 20.0))
    active = rms > thr

    # ① 桥接短间隙：在 active 上做形态学闭运算
    bridge = max(1, int(round(bridge_ms / (hop_length / sr * 1000.0))))
    if bridge > 1:
        a = active.copy()
        n = len(a)
        i = 0
        while i < n:
            if not a[i]:
                j = i
                while j < n and not a[j]:
                    j += 1
                gap = j - i
                # 前后都有声、且间隙够短 → 填上
                if i > 0 and j < n and gap <= bridge:
                    a[i:j] = True
                i = j
            else:
                i += 1
        active = a

    # ② 提取区间
    regions, i, n = [], 0, len(active)
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            regions.append((i * hop_length / sr, (j + 1) * hop_length / sr))
            i = j + 1
        else:
            i += 1
    return regions


# ══════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════
def segment_notes(
    y,
    sr,
    f0=None,
    hop_length=PRECISE_HOP,
    smooth_f0=None,
    **kw,
):
    """主入口：音频 → 音符列表。

    参数
        y           : 波形
        sr          : 采样率
        f0          : 逐帧基频（不传则内部用 PYIN 算）。**必须是原始未平滑的**
        smooth_f0   : 仅用于找换音点的平滑音高（不传则内部生成）
    返回
        (notes, raw_f0, times_s)
    """
    p = {**DEFAULTS, **kw}
    frame_ms = hop_length / sr * 1000.0

    # ── 1. 音高 ────────────────────────────────────────────
    if f0 is None:
        f0, _, _ = extract_f0_pyin(y, sr, hop_length=hop_length)
    f0 = np.asarray(f0, dtype=float)
    n_frames = len(f0)
    times = np.arange(n_frames) * frame_ms / 1000.0

    # ── 2. 两路边界候选 ────────────────────────────────────
    onsets = detect_onsets(
        y, sr, hop_length=hop_length,
        delta=p["onset_delta"], backtrack=p["onset_backtrack"],
    )
    # ★ 先剔除颤音造成的假 onset（否则一个长音会被切成碎片）
    n_raw = len(onsets)
    onsets = validate_onsets(onsets, y, sr, f0, hop_length, p)
    pchg, _ = detect_pitch_changes(f0, sr, hop_length=hop_length, **p)
    pchg = np.asarray([t for t in pchg if t <= times[-1]], dtype=float)

    # ── 3. 有声段内取并集 ──────────────────────────────────
    regions = voiced_regions(y, sr, hop_length=hop_length, gate_db=p["rms_gate_db"])
    if not regions:
        return [], f0, times

    def in_voiced(t):
        return any(a - 0.02 <= t <= b + 0.02 for a, b in regions)

    bounds = sorted(set(
        [round(t, 3) for t in onsets if in_voiced(t)]
        + [round(t, 3) for t in pchg if in_voiced(t)]
    ))
    # 每个有声段的起点/终点也是边界
    for a, b in regions:
        bounds.append(round(a, 3)); bounds.append(round(b, 3))
    bounds = sorted(set(bounds))

    # ── 4. 合并过短的音符 ──────────────────────────────────
    # ★ 关键：过短的片段必须【并入邻近音符】，绝不能【丢弃】。
    #   丢弃会在门控轨里留下一段静音 —— 用户实测听到"某个字被切成两半"，
    #   根因就在这里（擦音能量低被切断，短碎片又被丢掉）。
    min_dur = p["min_duration_ms"] / 1000.0
    min_sus = p["min_sustain_ms"] / 1000.0
    merged = [bounds[0]]
    for t in bounds[1:]:
        if t - merged[-1] < min_dur:
            continue                      # 太近，这个边界作废（两侧并入同一音符）
        merged.append(t)
    # 末尾的碎块并入前一个音符
    if len(merged) >= 2 and (bounds[-1] - merged[-1]) < min_dur * 0.6:
        merged[-1] = bounds[-1]
    bounds = merged

    # ── 5. 逐音符取特征 ────────────────────────────────────
    rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]
    rms = np.pad(rms, (0, max(0, n_frames - len(rms))))[:n_frames]

    notes: list[Note] = []
    for k in range(len(bounds) - 1):
        t0, t1 = bounds[k], bounds[k + 1]
        # 过短的片段并入前一个音符（延长它），而不是丢弃 —— 丢弃会切坏字
        if t1 - t0 < min_dur * 0.6 and notes:
            notes[-1].end_ms = int(round(t1 * 1000))
            continue
        i0 = int(round(t0 / (frame_ms / 1000.0)))
        i1 = int(round(t1 / (frame_ms / 1000.0)))
        i0, i1 = max(0, i0), min(n_frames, max(i0 + 1, i1))
        seg_f0 = f0[i0:i1]
        seg_valid = np.isfinite(seg_f0) & (seg_f0 > 0)
        seg_rms = rms[i0:i1]

        # ⚠️ 注意单位：seg_f0 是 Hz，pitch_midi 必须是 MIDI 音符号。
        #    （这里曾经写错过 —— 直接把 Hz 存进了 pitch_midi，图上一眼能看出来）
        midi_seg = hz_to_midi(seg_f0[seg_valid])
        pitch = float(np.median(midi_seg)) if seg_valid.any() else None
        pstd = float(np.std(midi_seg)) if seg_valid.sum() > 2 else None
        dur_ms = int(round((t1 - t0) * 1000))

        # 判断边界来源（用于诊断，也方便论文里分析切分质量）
        near_onset = any(abs(o - t0) < 0.03 for o in onsets)
        near_pitch = any(abs(o - t0) < 0.03 for o in pchg)
        src = "both" if (near_onset and near_pitch) else "onset" if near_onset else "pitch" if near_pitch else "silence"

        notes.append(Note(
            note_index=len(notes),
            start_ms=int(round(t0 * 1000)),
            end_ms=int(round(t1 * 1000)),
            pitch_midi=pitch,
            pitch_std=pstd,
            rms_mean=float(np.mean(seg_rms)) if seg_rms.size else None,
            rms_peak=float(np.max(seg_rms)) if seg_rms.size else None,
            voiced_ratio=float(seg_valid.mean()) if seg_valid.size else 0.0,
            # 颤音需要足够长的稳定段：≥300ms 且音高不是完全乱跳
            long_enough_for_vibrato=bool(dur_ms >= 300 and (pstd is None or pstd < 1.2)),
            boundary_source=src,
        ))

    for i, nt in enumerate(notes):
        nt.note_index = i

    # ── 6. 音符级质量控制 ──────────────────────────────────
    notes = filter_notes(notes, p)

    # ── 7. 桥接过滤留下的缺口 ──────────────────────────────
    # ★ 关键：过滤掉一个音符后，绝不能留个洞。
    #   留洞 = 门控轨里那段被静音 = 听感上「这个字被切成两半」。
    #   实测用户就听出了这个问题（能量 -17dB 的地方被静音了 255ms）。
    notes = bridge_note_gaps(notes, y, sr, hop_length=hop_length, params=p)

    for i, nt in enumerate(notes):
        nt.note_index = i
    return notes, f0, times


def bridge_note_gaps(notes, y=None, sr=None, hop_length=PRECISE_HOP, params=None):
    """把相邻音符之间的缺口并给前一个音符，让音频覆盖连续。

    ★ 判定规则（按能量，而不是只看时长）：
        缺口内能量 > loud_db（默认 -25 dB）→ 明显有声音 → **无论多长都桥接**
        缺口内能量低但时长 < max_gap_ms → 短停顿，桥接（可能是辅音闭塞段）
        其余 → 真正的换气停顿，保持静音

    为什么不能只看时长：实测有一个 255ms 的缺口，能量却是 -17 dB
    —— 那里面明显有声音（用户的"选"字），只看时长会漏掉它。

    为什么不干脆全部丢弃：缺口里往往有辅音，丢掉会让门控轨断字。
    并进来之后音高类特征仍只取有声帧（不受影响），音色类特征反而更准。
    """
    p = {**DEFAULTS, **(params or {})}
    if len(notes) < 2:
        return notes

    max_gap = p["bridge_gap_ms"]
    loud_db = p.get("bridge_loud_db", -25.0)
    hard_max = p.get("bridge_hard_max_ms", 600)   # 再响也不桥接超过这个长度

    rms = None
    if y is not None and sr is not None:
        rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]
        peak = float(rms.max()) if rms.size else 0.0
    else:
        peak = 0.0

    def gap_level_db(a_ms, b_ms):
        if rms is None or peak <= 0:
            return None
        i0 = max(0, int(a_ms / 1000.0 * sr / hop_length))
        i1 = min(len(rms), int(b_ms / 1000.0 * sr / hop_length) + 1)
        if i1 <= i0:
            return None
        return float(20 * np.log10(max(float(np.median(rms[i0:i1])), 1e-12) / peak))

    out = [notes[0]]
    for n in notes[1:]:
        gap = n.start_ms - out[-1].end_ms
        if gap <= 0:
            out.append(n); continue
        lvl = gap_level_db(out[-1].end_ms, n.start_ms)
        should_bridge = (
            (lvl is not None and lvl > loud_db and gap <= hard_max)
            or (gap <= max_gap)
        )
        if should_bridge:
            out[-1].end_ms = n.start_ms
        out.append(n)
    return out


# ══════════════════════════════════════════════════════════════
# 音符级质量控制
# ══════════════════════════════════════════════════════════════
def filter_notes(notes, params=None):
    """剔除"假音符"。

    三类要剔除的：
      ① 能量太低 —— 气声、尾音衰减、背景噪声被切成了音符
      ② 有声率太低 —— 音高检测在这段里找不到音高，说明不是唱出来的
      ③ 音高没有 —— 压根没测到音高

    ★ 注意 ③ 和「音高波动大」不是一回事：
      波动大是【切分不足】（一个音符里混了多个音高），需要再切，不是丢弃。
      这里只做剔除，不负责再切。
    """
    p = {**DEFAULTS, **(params or {})}
    if not notes:
        return notes

    # 参照能量：所有音符 RMS 的中位数（而不是全局峰值，避免被个别大音压扁）
    rmss = np.array([n.rms_mean for n in notes if n.rms_mean is not None], dtype=float)
    ref_rms = float(np.median(rmss)) if rmss.size else 0.0
    rms_floor = ref_rms * p["rms_min_ratio"]

    kept = []
    for n in notes:
        if n.pitch_midi is None:                       # ③ 没音高
            continue
        if (n.rms_mean or 0.0) < rms_floor:            # ① 能量太低
            continue
        if (n.voiced_ratio or 0.0) < p["voiced_min"]:  # ② 有声率太低
            continue
        kept.append(n)
    return kept


# ══════════════════════════════════════════════════════════════
# 质量诊断 —— 决定六个指标能不能算
# ══════════════════════════════════════════════════════════════
VIBRATO_MIN_DUR_MS = 300      # 颤音检测的下限：短于此无法可靠估计 4–8 Hz 的振荡
VIBRATO_MIN_COUNT = 3         # 至少要有这么多个长音，颤音指标才有统计意义


def audit_gaps(notes, y=None, sr=None, hop_length=PRECISE_HOP, min_gap_ms=25):
    """★ 自动检测「切坏字」。

    原理：门控轨只在【音符没覆盖到】的地方静音。
    如果一段本该有声却没有音符覆盖，门控轨里就会出现静音 —— 听感上就是「字被切成两半」。

    ⚠️ 关键：不能只看「有声段内部」的缺口。
       擦音（x/s/sh）能量太低，可能压根没被算进有声段，
       此时缺口落在有声段之间，用旧方法检测不到。
       所以本函数对**整条时间轴**找缺口，再用【缺口内的能量】判断它是不是真问题：

         缺口内能量高  → 本来该有声却被静音了 → ★ 切坏了
         缺口内能量低  → 真的是换气/停顿        → 正常，忽略

    y / sr 不传时只按时长报，不做能量判断。

    返回 [{start_ms, end_ms, dur_ms, level_db, is_problem}, ...]，按时长降序。
    """
    if not notes:
        return []

    # 音符覆盖区（合并相邻/重叠）
    merged = []
    for a, b in sorted((n.start_ms, n.end_ms) for n in notes):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    total_ms = int(notes[-1].end_ms)
    edges = [0] + [v for iv in merged for v in iv] + [total_ms]
    edges = sorted(set(edges))

    # 逐段找出没被覆盖的区间
    empty = []
    cur = edges[0]
    for a, b in merged:
        if a - cur >= min_gap_ms:
            empty.append((cur, a))
        cur = max(cur, b)
    if total_ms - cur >= min_gap_ms:
        empty.append((cur, total_ms))

    # 算每个缺口里的能量水平
    rms = None
    if y is not None and sr is not None:
        rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]
        peak = float(rms.max()) if rms.size else 0.0
    else:
        peak = 0.0

    out = []
    for a_ms, b_ms in empty:
        level_db = None
        if rms is not None and peak > 0:
            i0 = max(0, int(a_ms / 1000.0 * sr / hop_length))
            i1 = min(len(rms), int(b_ms / 1000.0 * sr / hop_length) + 1)
            seg = rms[i0:i1] if i1 > i0 else np.array([])
            if seg.size:
                level_db = float(20 * np.log10(max(float(np.median(seg)), 1e-12) / peak))
        # 缺口能量分级：
        #   > -25 dB          明显有内容被静音 → ★ 切坏了
        #   -32 ~ -25 dB      气息声/房间噪声   → 存疑，看时长
        #   < -32 dB          真停顿           → 正常
        if level_db is None:
            kind = "unknown"
        elif level_db > -25.0:
            kind = "cut"           # 切坏
        elif level_db > -32.0 and (b_ms - a_ms) < 300:
            kind = "suspect"       # 存疑：短缺口 + 有一点能量
        else:
            kind = "pause"         # 正常停顿
        out.append({
            "start_ms": int(a_ms), "end_ms": int(b_ms), "dur_ms": int(b_ms - a_ms),
            "level_db": round(level_db, 1) if level_db is not None else None,
            "kind": kind,
            "is_problem": kind == "cut",
        })
    order = {"cut": 0, "suspect": 1, "unknown": 2, "pause": 3}
    return sorted(out, key=lambda g: (order[g["kind"]], -g["dur_ms"]))


def note_stats(notes) -> dict:
    """切分质量统计。**这是六指标可行性的判据。**"""
    if not notes:
        return {"count": 0, "feasible": False, "reason": "没有切出任何音符"}

    durs = np.array([n.end_ms - n.start_ms for n in notes], dtype=float)
    stds = np.array([n.pitch_std for n in notes if n.pitch_std is not None], dtype=float)
    long_notes = [n for n in notes if n.long_enough_for_vibrato]

    src_counts = {}
    for n in notes:
        src_counts[n.boundary_source] = src_counts.get(n.boundary_source, 0) + 1

    stats = {
        "count": len(notes),
        "duration_ms": {
            "min": float(durs.min()), "median": float(np.median(durs)),
            "mean": float(durs.mean()), "max": float(durs.max()),
            "p10": float(np.percentile(durs, 10)), "p90": float(np.percentile(durs, 90)),
        },
        "pitch_std_median": float(np.median(stds)) if stds.size else None,
        "long_notes": len(long_notes),
        "long_notes_ratio": len(long_notes) / len(notes),
        "boundary_sources": src_counts,
        "fragmented": bool(np.median(durs) < 180),   # 中位时长 < 180ms 视为切碎
    }

    # ── 六指标可行性判定 ────────────────────────────────
    reasons = []
    if stats["fragmented"]:
        reasons.append(f"切得太碎（中位时长 {np.median(durs):.0f} ms < 180 ms）")
    if len(long_notes) < VIBRATO_MIN_COUNT:
        reasons.append(f"长音不足（{len(long_notes)} 个 < {VIBRATO_MIN_COUNT} 个）→ 颤音指标算不出来")
    stats["feasible"] = not reasons
    stats["reason"] = "；".join(reasons) if reasons else "切分质量良好，六指标可算"
    stats["vibrato_computable"] = len(long_notes) >= VIBRATO_MIN_COUNT
    return stats
