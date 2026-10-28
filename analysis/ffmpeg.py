"""ffmpeg 定位 —— **全项目唯一一处**。

━━━ 为什么要有这个模块 ━━━

2026-10-07 修"上传 m4a 失败"时，在 `web/server.py` 里加了一个
`_find_ffmpeg()`（按优先级找一圈）。**但同一件事在 `experiments/verify_new_take.py`
里是另一份实现** —— 而那份的兜底列表是三个**硬编码路径**
（`D:\\AipaiClip\\...` 等，都是**别的机器/别的软件**留下的），在这台机器上**一个都不存在**。

后果很具体：`librosa.load()` 读 m4a 会抛 `NoBackendError`
⇒ **作者用手机录了 m4a，跑验收脚本会直接失败** —— 而那正是
"录完才发现白录"的场景（A0c 存在的全部意义就是避免它）。

⇒ 抽到这里，两边**共用一处**。这正是本项目反复强调的那条：
   **同一件事不要在多处各写一遍**（两份实现必然有一天不同步，而不同步是静默的）。
"""
from __future__ import annotations

import os
import shutil
from typing import Optional


def find_ffmpeg() -> Optional[str]:
    """按优先级找 ffmpeg，找不到返回 `None`。

    优先级（从"最可移植"到"最将就"）：
      ① 环境变量 `SINGLENS_FFMPEG` —— 部署时显式指定，最可控
      ② `imageio_ffmpeg` 自带的 —— **跟着 venv 走，能打包、能跨机器**
      ③ PATH 上的 —— 用户自己装过就用
      ④ 几个常见的 Windows 安装位置 —— 最后的将就

    ⚠️ 第 ④ 条是"将就"：那些是别的软件的私有副本
      （实测 `D:\\AipaiClip\\bin\\ffmpeg.exe` 只有 0.3MB，
        **依赖同目录 24MB 的 DLL**，拷走就废）⇒ 不拷贝、只借用路径。
      真要可移植，靠 ② 而不是 ④。
    ⚠️ 实测（2026-10-10）这台机器 **PATH 上没有 ffmpeg**，
      所以"假设 PATH 上有"是一种会**静默失败**的写法 —— 别那么写。
    """
    env = (os.environ.get("SINGLENS_FFMPEG") or "").strip()
    if env and os.path.exists(env):
        return env
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:  # noqa: BLE001
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    for p in (r"D:\AipaiClip\bin\ffmpeg.exe",
              r"D:\oopz\ffmpeg.exe",
              r"C:\ffmpeg\bin\ffmpeg.exe"):
        if os.path.exists(p):
            return p
    return None


def to_wav(src: str, dst: str, sr: int = 22050, timeout: int = 600) -> bool:
    """任意格式 → 单声道 wav。**先试 librosa，再用 ffmpeg 兜底。**

    ⚠️ 为什么不能只靠 `librosa.load()`：它读 m4a 会抛 `NoBackendError`
      （底层 audioread 找不到 ffmpeg）—— 而 **m4a 正是手机录音的主力格式**。
    """
    try:
        import librosa
        import soundfile as sf
        y, _ = librosa.load(src, sr=sr, mono=True)
        if y.size:
            sf.write(dst, y.astype("float32"), sr)
            return True
    except Exception:  # noqa: BLE001
        pass
    ff = find_ffmpeg()
    if not ff:
        return False
    import subprocess
    try:
        r = subprocess.run([ff, "-y", "-i", src, "-ac", "1", "-ar", str(sr), dst],
                           capture_output=True, timeout=timeout)
        return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception:  # noqa: BLE001
        return False


def load_audio_any(path: str, sr: Optional[int] = None):
    """读任意格式的音频，返回 `(y, sr)`；**读不了就抛**（不静默返回空）。

    ★ 2026-10-15 加（A0c-2 ②）。这个坑已经被踩到**第四次**了：
        ① `web/server.py` 的上传解码（2026-10-07 修，§89）
        ② `experiments/verify_new_take.py` 的 `to_wav`（2026-10-15 修，§105）
        ③ `_dump_notes.py` / `_plot_contours.py`（这次）
        ④ 全项目**约 100 个脚本**都直接 `soundfile.read()`
           —— 那批**不动**：它们多数只跑过一次，改了只是 diff 噪音；
              真再要用到时换成这个函数即可
      ⇒ **以后脚本要读音频，用这个**，不要再写 `sf.read`。

    ⚠️ 与 `to_wav` 的区别：那个落盘再返回路径（给子进程用），
      这个**直接在内存里返回数组**（给当前进程用）。
    ⚠️ 失败时**抛异常**而不是返回空数组 —— "读不出来"和"音频本来就是静的"
      必须分得开；否则脚本会拿着空数组算出一堆 0，还报"成功"。
    """
    import numpy as np
    # ① wav / flac / ogg：soundfile 系直接读，不需要 ffmpeg
    try:
        import librosa
        y, s = librosa.load(path, sr=sr, mono=True)
        if y.size:
            return np.asarray(y, dtype="float32"), int(s)
    except Exception:  # noqa: BLE001
        pass
    # ② m4a / mp3 等：走 ffmpeg
    import os as _os
    import subprocess
    import tempfile
    import soundfile as sf
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError(
            f"读不了 {_os.path.basename(path)}：它不是 wav/flac/ogg，"
            f"而系统里找不到 ffmpeg（装了 imageio_ffmpeg 就会自带一个）")
    with tempfile.TemporaryDirectory() as td:
        out = _os.path.join(td, "a.wav")
        args = [ff, "-y", "-i", path, "-ac", "1"]
        if sr:
            args += ["-ar", str(sr)]
        args.append(out)
        r = subprocess.run(args, capture_output=True, timeout=900)
        if r.returncode != 0 or not _os.path.exists(out):
            raise RuntimeError(f"ffmpeg 解码失败（{_os.path.basename(path)}）："
                               f"{r.stderr[-200:].decode('utf-8', 'ignore')}")
        y, s = sf.read(out, dtype="float32", always_2d=False)
        return np.asarray(y, dtype="float32"), int(s)
