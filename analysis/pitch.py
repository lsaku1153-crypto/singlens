"""音高提取与音分换算工具。

本项目所有音准相关计算的基础模块。
设计原则：不依赖 Web 层，可独立运行与测试。

⚠️ 性能与参数须知（第 1–2 周实测，详见 experiments/bench_pyin.py）
------------------------------------------------------------------
1) librosa.pyin 用 numba JIT 编译，**进程内首次调用会额外耗时约 44 秒**。
   → Web 服务启动时必须调用 warm_up() 预热，否则第一个用户请求会卡死。
   → 建议设置环境变量 NUMBA_CACHE_DIR 让编译缓存持久化。

2) PYIN 耗时对 fmin 极其敏感（10 秒音频实测，fmax=C6、hop=256）：
       fmin=C2 (65.4 Hz)  -> 3.32 s   ← 本模块默认（覆盖面优先）
       fmin=E2 (82.4 Hz)  -> 2.70 s
       fmin=G2 (98.0 Hz)  -> 2.38 s
       fmin=A2 (110.0 Hz) -> 2.16 s
       fmin=C3 (130.8 Hz) -> 1.85 s
   → 默认取 C2 仍满足"精算 < 5 s"验收目标；
     若已知参考旋律，改用 range_from_melody() 收窄范围可再提速数倍。

3) YIN 比 PYIN 快约 50 倍（10 秒音频 0.03 秒），这是双管线设计的前提。

4) 算法失败时会把输出钉在 fmin/fmax 上（饱和）。必须用 mark_saturated()
   剔除，否则这些假音高会严重污染统计（实测可制造出大量假的八度尖峰）。
"""

from __future__ import annotations

# ★ G7（2026-10-15）：**把 numba 的 JIT 编译缓存持久化。**
# 本文件头第 10 行从第 1 周起就写着「建议设置环境变量 NUMBA_CACHE_DIR
# 让编译缓存持久化」—— **但从来没有人实现它**（文档里写了、代码里没有）。
# ⇒ 每次重启服务，第一次分析都要重新编译 ~44 秒，而界面上没有任何提示，
#   用户只会以为卡住了。
# ⚠️ **必须在 `import librosa` 之前**：numba 在首次 import 时读这个变量。
#   （`web/server.py` 里也放了一份 —— 那边第 40 行就 import librosa，
#     比这里更早；两处都设是为了"谁先被导入都生效"。）
import os as _os
_NC = (_os.environ.get("SINGLENS_NUMBA_CACHE") or "").strip()
if not _NC:
    _NC = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "data", "numba_cache")
try:
    _os.makedirs(_NC, exist_ok=True)
    _os.environ.setdefault("NUMBA_CACHE_DIR", _NC)
except OSError:
    pass

import librosa
import numpy as np

# 人耳对 ±25 音分以内的偏差基本不敏感（音乐心理学常识），
# 故采用软阈值：容差内不扣分。见设计文档 4.2。
PITCH_TOLERANCE_CENTS = 25.0

# 默认基频搜索范围。
#
# ⚠️ 第 2 周实测修正：原设 C3（130.8 Hz）会**切掉低音男声的真实人声**。
#    实测某男声清唱中位基频仅 ~140 Hz，用 fmin=C3 时有 32% 的帧贴在 C3 下界饱和。
#    改为 C2（65.4 Hz）后饱和率降为 0%，且耗时 3.32 s / 10 s 音频，
#    仍满足"精算管线 < 5 s"的验收目标。
#
#    若已知参考旋律，应改用 range_from_melody() 收窄范围以提速。
DEFAULT_FMIN = "C2"
DEFAULT_FMAX = "C6"

# 帧参数
PRECISE_HOP = 256   # 精算管线（精度优先）
REALTIME_HOP = 512  # 实时管线（速度优先）
FRAME_LENGTH = 2048


def hz_to_midi(freq):
    """频率 (Hz) -> MIDI 音符号。支持标量与数组。"""
    return 69.0 + 12.0 * np.log2(np.asarray(freq, dtype=float) / 440.0)


def midi_to_hz(midi):
    """MIDI 音符号 -> 频率 (Hz)。支持标量与数组。"""
    return 440.0 * np.power(2.0, (np.asarray(midi, dtype=float) - 69.0) / 12.0)


def cents_deviation(freq, ref_freq):
    """相对参考频率的音分偏差（正值偏高，负值偏低）。1 半音 = 100 音分。"""
    freq = np.asarray(freq, dtype=float)
    ref_freq = np.asarray(ref_freq, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 1200.0 * np.log2(freq / ref_freq)


def range_from_melody(melody_midi, margin_semitones=12):
    """由已知参考旋律推导基频搜索范围。

    收窄 fmin/fmax 可显著提速并降低八度误判（见模块头性能须知）。
    模式 A（教材曲目）与模式 B（跟唱模板）都应在拿到参考后调用本函数。
    """
    midi = np.asarray(melody_midi, dtype=float)
    if midi.size == 0:
        return librosa.note_to_hz(DEFAULT_FMIN), librosa.note_to_hz(DEFAULT_FMAX)
    return (
        float(midi_to_hz(np.min(midi) - margin_semitones)),
        float(midi_to_hz(np.max(midi) + margin_semitones)),
    )


def extract_f0_pyin(y, sr, fmin=None, fmax=None, frame_length=FRAME_LENGTH,
                    hop_length=PRECISE_HOP):
    """高精度基频估计（精算管线使用）。

    返回 (f0, voiced_flag, voiced_prob)；未发声帧的 f0 为 NaN。
    """
    fmin = fmin if fmin is not None else librosa.note_to_hz(DEFAULT_FMIN)
    fmax = fmax if fmax is not None else librosa.note_to_hz(DEFAULT_FMAX)
    f0, voiced_flag, voiced_prob = librosa.pyin(
        y, sr=sr, fmin=fmin, fmax=fmax,
        frame_length=frame_length, hop_length=hop_length,
    )
    return f0, voiced_flag, voiced_prob


def extract_f0_yin(y, sr, fmin=None, fmax=None, frame_length=FRAME_LENGTH,
                   hop_length=REALTIME_HOP):
    """快速基频估计（实时管线使用）。

    注意：YIN 不提供发声判定，需调用方用 RMS 自行过滤静音帧。
    """
    fmin = fmin if fmin is not None else librosa.note_to_hz(DEFAULT_FMIN)
    fmax = fmax if fmax is not None else librosa.note_to_hz(DEFAULT_FMAX)
    return librosa.yin(
        y, fmin=fmin, fmax=fmax, sr=sr,
        frame_length=frame_length, hop_length=hop_length,
    )


def pitched_mask(y, sr, f0, hop_length=REALTIME_HOP, gate_db=-35.0,
                 htr_min=0.25, n_harmonics=5, n_fft=2048):
    """发声判定：哪些帧是【真正有音高的声音】，而不是换气/噪声。

    ★ 为什么光有能量门限不够（实测对比，两段真实清唱）：

        条件                    新录音跨度   新录音主八度   旧录音跨度
        仅 YIN 有效              42.2 半音     58.4%        44.2
        + 能量 -35dB             40.1          74.9%        44.2
        + 谐波性 >0.25           30.3          74.4%        13.9
        + 能量 & 谐波性          19.2          81.1%        13.9   ← 最好

      能量门限能去掉"停顿时的房间噪声"，但去不掉**换气声**——
      换气是有能量的，只是没有稳定的音高结构。
      所以还要加【谐波性】判据：f0 的前 n 次谐波处的能量占比。
        真人声：谐波结构清晰，占比高
        换气/摩擦音：能量散布在全频段，占比低

      两者组合后，两段录音的音域跨度都回到合理区间（19.2 / 13.9 半音，
      都是一个八度多一点），主八度覆盖率升到 81% / 94%。

    返回与 f0 等长的布尔数组。
    """
    n = len(f0)
    if n == 0:
        return np.zeros(0, dtype=bool)

    rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=hop_length)[0]
    if rms.size == 0 or rms.max() <= 0:
        return np.zeros(n, dtype=bool)
    energy_ok = rms > rms.max() * (10 ** (gate_db / 20.0))

    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    bin_hz = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
    m = min(n, S.shape[1], len(energy_ok))
    tot = S[:, :m].sum(axis=0) + 1e-12

    htr = np.zeros(m)
    for i in range(m):
        f = f0[i]
        if not np.isfinite(f) or f <= 0:
            continue
        e = 0.0
        for k in range(1, n_harmonics + 1):
            b = int(round(f * k / bin_hz))
            if 0 < b < len(freqs):
                e += S[max(0, b - 1):b + 2, i].sum()
        htr[i] = e / tot[i]

    out = np.zeros(n, dtype=bool)
    out[:m] = energy_ok[:m] & (htr > htr_min)
    return out


def voiced_mask(y, sr, hop_length=REALTIME_HOP, gate_db=-35.0, frame_length=FRAME_LENGTH):
    """用能量给 YIN 的输出做发声判定（YIN 本身不提供）。

    ★ 为什么必须有这一步（实测教训）：

      YIN 对任何输入都会给出一个基频 —— 包括换气声、停顿时的房间噪声。
      实测一段真实清唱（女声，主体 E4=334 Hz）：
        低能量帧的 RMS 中位 -54.3 dB，正常帧 -20.1 dB，**差 34 dB**
        这些噪声帧被 YIN 报成 65~198 Hz 的"音高"
      → 直接后果：
        · estimate_range 把噪声算进音域，跨度从真实的 ~1 个八度被撑到 43 个半音
        · 音域收窄因此失效（退回全音域），精算耗时翻 3 倍
        · 音高分布的主八度覆盖率只有 58%（正常应为 85%+）

      加 -35 dB 门限后：噪声帧占比 16.6% → 3.1%，中位频率回到 348 Hz。
      **pitch.py 的模块注释早就写了"需调用方用 RMS 自行过滤静音帧"，
      但精算管线里一直没有真的这么做。**

    返回与 f0 等长的布尔数组。
    """
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    if rms.size == 0 or rms.max() <= 0:
        return np.zeros(0, dtype=bool)
    thr = rms.max() * (10 ** (gate_db / 20.0))
    m = rms > thr
    if len(m) < len(y) // hop_length + 1:
        m = np.pad(m, (0, len(y) // hop_length + 1 - len(m)), constant_values=False)
    return m


def _main_cluster(midi, window=12, keep_ratio=0.90):
    """找出音高的【主簇】，返回 (lo, hi, 簇内覆盖率)。

    做法：
      1 先找最密集的 12 半音窗口（人声一首歌通常就在一个八度左右）
      2 再以该窗口为中心，按分位数向外扩，直到覆盖 keep_ratio 的帧
      3 返回扩展后的上下界

    ★ 为什么要用主簇而不是直接取分位数：
      分位数会被少量 outlier 拉偏。实测一段主八度覆盖 85.6% 的正常录音，
      因为极少数帧被报到了很低的频率，1~99% 跨度算出来有 33.8 半音，
      导致音域收窄失效、性能翻几倍变差。主簇法对这类 outlier 免疫。
    """
    midi = np.asarray(midi, dtype=float)
    midi = midi[np.isfinite(midi)]
    if midi.size < 20:
        return float("nan"), float("nan"), 0.0

    lo_m, hi_m = float(midi.min()), float(midi.max())
    if hi_m - lo_m <= window:
        return lo_m, hi_m, 1.0

    # 1 最密集的 window 半音窗口（用 1 半音分辨率的直方图 + 滑窗）
    edges = np.arange(np.floor(lo_m), np.ceil(hi_m) + 1.0, 1.0)
    h, e = np.histogram(midi, bins=edges)
    if h.size <= window:
        return lo_m, hi_m, 1.0
    csum = np.concatenate([[0], np.cumsum(h)])
    n_win = window
    counts = csum[n_win:] - csum[:-n_win]
    c = int(np.argmax(counts))
    core_lo, core_hi = float(e[c]), float(e[c + n_win])

    # 2 向外扩到覆盖 keep_ratio
    order = np.sort(midi)
    k0 = int(np.searchsorted(order, core_lo))
    k1 = int(np.searchsorted(order, core_hi))
    need = int(np.ceil(keep_ratio * midi.size))
    inside = k1 - k0
    lo, hi = core_lo, core_hi
    # 每次把更"近"的那一侧多纳入一些，直到够
    left, right = k0, midi.size - k1
    while inside < need and (left > 0 or right > 0):
        take_l = left > 0 and (right == 0 or (midi.size - k1 - right) <= (k0 - left))
        if take_l:
            left -= 1; lo = float(order[left])
        else:
            right -= 1; hi = float(order[midi.size - 1 - right])
        inside += 1
    cov = inside / midi.size
    return lo, hi, cov


def estimate_range(y, sr, hop_length=REALTIME_HOP, lo_pct=2.0, hi_pct=98.0,
                   margin_semitones=6, gate_db=-35.0):
    """先跑一次便宜的 YIN，用实测音域收窄 PYIN 的搜索范围。

    ★ 为什么这招值得做（实测数据，39.8 秒真实录音）：

        C2–C6 全音域 PYIN   19.31s（0.486× 实时）
        收窄到实测音域       4.65s（0.117× 实时）   ← 提速 4.15×
        预跑 YIN 的成本      0.21s（0.005×）
        音高一致性           100% 帧差 <0.5 半音，中位差 0.000 半音

      也就是说：**几乎零成本、零质量损失，换来 4 倍提速**。
      原因很直接 —— 全音域意味着 YIN 要在 4 个八度里找周期，
      而真人唱一段歌通常只跨 1~1.5 个八度。搜索空间小了，代价自然降。

      这也修正了设计文档 §9.4 的性能预算：0.486× 是"未优化"的数，
      真实可达 0.117×。验收指标「精算 <0.5× 实时」有 4 倍余量。

    返回 (fmin_hz, fmax_hz, info)。任何一步不可靠时退回全音域默认值。
    """
    fmin_d = librosa.note_to_hz(DEFAULT_FMIN)
    fmax_d = librosa.note_to_hz(DEFAULT_FMAX)
    info = {"method": "FULL", "reason": "", "fmin": DEFAULT_FMIN, "fmax": DEFAULT_FMAX}

    if y is None or len(y) < sr * 0.5:
        info["reason"] = "音频太短"
        return fmin_d, fmax_d, info
    try:
        f0 = extract_f0_yin(y, sr, hop_length=hop_length)
    except Exception as e:  # noqa: BLE001
        info["reason"] = f"YIN 预估失败：{e}"
        return fmin_d, fmax_d, info

    ok = np.isfinite(f0) & (f0 > 0)
    # ★ 发声判定：把低能量帧（换气/停顿/房间噪声）排除掉。
    #   不做这一步的话，YIN 在噪声上给的假音高会把音域撑爆，
    #   导致收窄失效、性能翻倍变差。实测见 voiced_mask 的文档。
    vm = pitched_mask(y, sr, f0, hop_length=hop_length, gate_db=gate_db)
    n = min(len(ok), len(vm))
    ok = ok[:n] & vm[:n]
    f0 = f0[:n]
    if ok.sum() < 20:
        info["reason"] = "有效音高帧太少"
        return fmin_d, fmax_d, info

    midi = hz_to_midi(f0[ok])
    # ★ 用【主簇】而不是分位数来定音域。
    #   踩过的坑：原来取 2/98 分位，会被少量 outlier 拉偏 ——
    #   实测一段录音主八度覆盖 85.6%（很正常），但 1~99% 跨度却有 33.8 半音，
    #   原因是极少数帧被 YIN 报到了很低的频率上，把下界整个拽了下去。
    #   主簇法（找最密集的 12 半音窗口，再按分位向外扩）对 outlier 免疫。
    lo, hi, cluster_cov = _main_cluster(midi)
    span = float(hi - lo)
    # 音域太窄（近乎单音）说明可能只录到了一点声音，不可靠
    if span < 3.0:
        info["reason"] = f"主簇过窄（{span:.1f} 半音）"
        return fmin_d, fmax_d, info
    info["cluster_coverage"] = round(cluster_cov, 3)

    fmin, fmax = range_from_melody([lo, hi], margin_semitones=margin_semitones)
    fmin = max(float(fmin), fmin_d)          # 不超出默认边界
    fmax = min(float(fmax), fmax_d)
    if fmax <= fmin * 1.05:
        info["reason"] = "收窄后范围无效"
        return fmin_d, fmax_d, info

    info.update({
        "method": "NARROWED",
        "fmin": librosa.hz_to_note(fmin), "fmax": librosa.hz_to_note(fmax),
        "observed_midi": [round(float(lo), 1), round(float(hi), 1)],
        "span_semitones": round(span, 1),
    })
    return fmin, fmax, info


def harmonic_component(y, sr, margin=1.0):
    """HPSS 谐波分量 —— 跟着伴奏唱时去掉鼓与低频伴奏的干扰。

    ━━━ 为什么需要它 ━━━
    设计规格 §5.4 实测：带伴奏录音的八度错误率 **6.89%**，是纯语音（0.41%）的
    **17 倍**；频谱图在 60–250 Hz 有独立于人声的持续条带（贝斯/低音声部）。
    三种预处理里只有 HPSS 谐波分量**六项指标全部改善**
    （有效帧率 0.8698→0.8798、音级聚集度 0.3329→0.3488、低频占比 0.0606→0.0572）。
    REPET 反而失败（0.65 有效帧率）—— 因为**歌曲旋律本身也重复**，
    "伴奏重复、人声不重复"这个假设在演唱场景不成立。

    ━━━ ⚠️ 默认**不开**，而且是有实测依据的（2026-09-24）━━━
    本项目现有的素材**全是清唱**。对清唱素材实测 HPSS 的影响：

        sep12_5pm_vocal.wav   39.8s  一致率 91.9%（相对不预处理）
        VID_20260918_104326   19.2s  一致率 89.5%
        constructed/take01    19.8s  一致率 90.3%

    也就是说它在**干净人声上也会改动约 10% 的帧**，并不"无副作用"；
    40 秒录音另需约 4.9 s。所以：
      · 清唱素材 → 关（默认）。开了会动到论文的基线数字，而现有数据全是清唱。
      · 带伴奏素材 → 开。这时它是设计规格里已验证的对策。
    切换开关是 `CONFIG["recording"]["accompaniment_mode"]`（设置页可改，
    服务端把它传进 `extract_f0_pyin_auto(hpss=...)`）。

    返回与输入等长的 float32 谐波分量。
    """
    import librosa as _lr
    yh, _yp = _lr.effects.hpss(np.asarray(y, dtype=np.float32), margin=margin)
    return yh.astype(np.float32)


def extract_f0_pyin_auto(y, sr, hop_length=PRECISE_HOP, frame_length=FRAME_LENGTH,
                         hpss=False):
    """自动收窄音域后再跑 PYIN —— **精算管线的推荐入口**。

    参数 `hpss=True` 时先取谐波分量（见 `harmonic_component` 的说明：
    带伴奏时开、清唱时关）。★ 预处理必须在 `estimate_range()` **之前**做 ——
    否则"用来定音域的那段音频"和"真正拿去估音高的音频"不是同一个信号。

    返回 (f0, voiced_flag, voiced_prob, range_info)
    """
    if hpss:
        y = harmonic_component(y, sr)
    fmin, fmax, info = estimate_range(y, sr)
    f0, vf, vp = extract_f0_pyin(y, sr, fmin=fmin, fmax=fmax,
                                 frame_length=frame_length, hop_length=hop_length)
    if hpss:
        info = dict(info or {})
        info["hpss"] = True
    return f0, vf, vp, info


def mark_saturated(f0, fmin, fmax, tol_ratio=0.01):
    """把贴在搜索边界上的帧标记为无效（NaN）。

    算法失败时往往直接输出 fmin 或 fmax（饱和），这些不是真实音高。
    实测（真实录音 116 s）：仅此一步就把 YIN 的真实八度错误率从"看起来很高"
    压到 **0.41%**——此前观察到的多数"八度尖峰"其实是 fmax 饱和。
    """
    out = np.array(f0, dtype=float)
    hi = fmax * (1 - tol_ratio)
    lo = fmin * (1 + tol_ratio)
    out[(out >= hi) | (out <= lo)] = np.nan
    return out


def warm_up(sr=22050):
    """预热 PYIN 的 numba JIT 编译。

    ⚠️ 服务启动时必须调用。否则进程内第一次 PYIN 请求会额外等待约 44 秒。
    """
    y = np.zeros(sr // 4, dtype=np.float32)
    y[sr // 8: sr // 8 + 512] = np.sin(2 * np.pi * 440.0 * np.arange(512) / sr)
    extract_f0_pyin(y, sr)


def median_pitch_in_window(f0, sr, hop_length, start_s, end_s):
    """取时间窗内所有已发声帧的中位基频 (Hz)。

    用于"每个音符一个音高"的降维，供音准评分使用。
    返回 np.nan 表示该窗内无有效音高。
    """
    if f0 is None or len(f0) == 0:
        return float("nan")
    times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop_length)
    mask = (times >= start_s) & (times < end_s) & np.isfinite(f0)
    if not np.any(mask):
        return float("nan")
    return float(np.median(f0[mask]))


def pitch_penalty(dev_cents, tolerance=PITCH_TOLERANCE_CENTS):
    """软阈值惩罚：超出容差的部分才计分。"""
    dev_cents = np.asarray(dev_cents, dtype=float)
    return np.maximum(0.0, np.abs(dev_cents) - tolerance)


def pitch_score(dev_cents, tolerance=PITCH_TOLERANCE_CENTS, full_penalty_cents=50.0):
    """音准分（0-100）。

    平均超出容差 full_penalty_cents 音分即得 0 分。
    同时返回两个分量：
      accuracy  = mean(dev)  系统性偏高/偏低
      stability = std(dev)   音符间忽高忽低
    """
    dev = np.asarray(dev_cents, dtype=float)
    dev = dev[np.isfinite(dev)]
    if dev.size == 0:
        return {"score": 0.0, "accuracy": float("nan"), "stability": float("nan")}

    mean_penalty = float(np.mean(pitch_penalty(dev, tolerance)))
    score = 100.0 * (1.0 - min(mean_penalty / full_penalty_cents, 1.0))
    return {
        "score": score,
        "accuracy": float(np.mean(dev)),
        "stability": float(np.std(dev)),
    }
