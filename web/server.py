"""SingLens Web 层：把 analysis/ 的算法包成契约里定义的 HTTP + WebSocket 接口。

契约见 docs/接口契约.md §3。**本文件必须严格遵守它**：
统一响应包、时间单位毫秒、分数 0-100 保留一位小数、
null 表示"本次未评估"（绝不用 0 代替）、六个错误码。

━━━ 双管线 ━━━
  实时管线（WS 上行）  分帧 → YIN → RMS → 滚动指标 → 20Hz 回推
  精算管线（POST finish 触发） PYIN → 切分 → DTW → 六指标 → ScoreReport

  实时管线存在的意义是"让用户看见自己的声音"，**精算管线才算分数**。
  两条管线用不同算法（YIN/PYIN），这个取舍本身就是论文的一节实验。

━━━ 关于延迟 ━━━
  验收指标"端到端延迟 < 200ms"在这个文件里有两条测量路径：
    · 服务端处理耗时 proc_ms —— 从收到音频块到算出音高
    · 客户端往返 RTT —— 前端发块到收到对应回帧（真正的端到端）
  只有后者能作为验收依据，前者只是分解项。见 /api/latency。

运行：
    .venv\\Scripts\\python.exe web\\server.py
    或  .venv\\Scripts\\uvicorn.exe web.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import parse_qsl, urlencode
from dataclasses import dataclass, field
from typing import Optional

# ══════════════════════════════════════════════════════════════
# ★ G7（2026-10-15）：**把 numba 的 JIT 编译缓存持久化。**
# ══════════════════════════════════════════════════════════════
# `librosa.pyin` 走 numba JIT，**进程内首次调用要编译约 44 秒**。
# 原来每次启动都要重来一遍 —— `analysis/pitch.py` 的文件头从第 1 周起就写着
# 「建议设置环境变量 NUMBA_CACHE_DIR 让编译缓存持久化」，
# **但从来没有人实现它**（典型的"文档里写了、代码里没有"）。
#
# 后果是具体的：每次重启服务，第一次分析要等 40 多秒；
# 而设置页/界面没有任何地方说"正在编译"⇒ 用户只会以为**卡住了**。
#
# ⚠️⚠️ **必须写在 `import librosa` 之前** —— numba 在**首次 import 时**
#    读这个环境变量，之后设就没用了。本文件第 40 行就 import 了 librosa，
#    所以这段必须放在它上面（`analysis/pitch.py` 里也放了一份，
#    好让直接 import pitch 的脚本同样受益）。
# ⚠️ 用 `setdefault`：部署时可以用 `SINGLENS_NUMBA_CACHE` 或直接设
#    `NUMBA_CACHE_DIR` 覆盖，不会被这里写死。
_NUMBA_CACHE = (os.environ.get("SINGLENS_NUMBA_CACHE") or "").strip()
if not _NUMBA_CACHE:
    _NUMBA_CACHE = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "numba_cache")
try:
    os.makedirs(_NUMBA_CACHE, exist_ok=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", _NUMBA_CACHE)
except OSError:
    # 建不出来就算了 —— 那只是"每次启动慢 40 秒"，不该让服务起不来
    pass

import librosa
import numpy as np
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
# ⚠️ 不再 import CORSMiddleware —— 本服务与页面同源，跨源需求为零，
#    而 `allow_origins=["*"]` 正是"任意网站可读走全部数据"的根因（审计 §3.1）。
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.align import (  # noqa: E402
    BUILTIN, ReferenceMelody, align_stats, align_to_reference,
    assess_template_quality, load_builtin,
)
from analysis.features import compute_all, feature_stats  # noqa: E402
from analysis.notes import audit_gaps, note_stats, segment_notes, voiced_regions  # noqa: E402
from analysis.pitch import (  # noqa: E402
    DEFAULT_FMAX, DEFAULT_FMIN, PITCH_TOLERANCE_CENTS, PRECISE_HOP, REALTIME_HOP,
    extract_f0_pyin, extract_f0_pyin_auto, extract_f0_yin, hz_to_midi,
    mark_saturated, pitch_score, warm_up,
)
from analysis.scoring import (  # noqa: E402
    FULL_PENALTY_CENTS, WEIGHT_VERSION, build_scores, ktv_tolerances, score_ktv,
)
# ★ 练习计划（B1，2026-10-01）：把"哪里不对"收敛成"下一遍只改哪一件事"。
#   单独一个模块而不是塞进 server.py —— 它是**教学规则**，不是服务逻辑，
#   而且规则要能脱开 HTTP 单独测（见 experiments/_verify_practice_plan.py）。
from analysis import practice_plan  # noqa: E402
from analysis.phrase_rhythm import score_phrases  # noqa: E402
from analysis.beat_diagnostic import beat_grid_from_audio, diagnose_beats  # noqa: E402
from analysis import coach_agent  # noqa: E402
from analysis import portrait as portrait_mod  # noqa: E402
from analysis import comfort as comfort_mod  # noqa: E402
from analysis import recommender as recommender_mod  # noqa: E402
from analysis import portrait_narrative as portrait_narrative_mod  # noqa: E402


# ══════════════════════════════════════════════════════════════
# AI 人声（G43，2026-10-19）
# ══════════════════════════════════════════════════════════════
# 本地 DiffSinger ONNX + onnxruntime CPU，跑在独立 `.venv_svs` 里；
# 服务端**不 import torch / onnxruntime**，只 spawn 子进程 + 按句缓存。
# 女声：绮萱 Qixuan v2.7.0；男声：Alemon（柠檬君）v3.5。
AI_ROOT = os.path.join(ROOT, "实验素材", "AI歌唱")
AI_PY = os.path.join(AI_ROOT, ".venv_svs", "Scripts", "python.exe")
AI_VOICES = {
    "female": {
        "label": "女声 · 绮萱",
        "attribution": "AI 合成 · 绮萱 / DiffSinger",
        "script": os.path.join(AI_ROOT, "scripts", "render_qixuan.py"),
        "voicebank": os.path.join(
            AI_ROOT, "voicebanks", "Qixuan_v2.7.0_DiffSinger_OpenUtau"),
        "vocoder": os.path.join(AI_ROOT, "vocoder_2025.02_onnx"),
        "transpose": 0,
    },
    "male": {
        "label": "男声 · Alemon",
        "attribution": "AI 合成 · Alemon / 柠檬君 · DiffSinger",
        "script": os.path.join(AI_ROOT, "scripts", "render_ds.py"),
        "voicebank": os.path.join(
            AI_ROOT, "voicebanks", "Alemon_CN&EN&JP_v3.5"),
        "vocoder": None,
        "transpose": -12,
    },
}
AI_CACHE_DIR = os.path.join(ROOT, "data", "exports", "ai_sing")
_AI_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════
# 歌曲导入（2026-10-20）：上传一首歌 → 分离人声/伴奏 → 原唱当参考
# ══════════════════════════════════════════════════════════════
# 为什么单独一套目录/状态机：
#   · 原曲文件大（几分钟），分析要 30–120 秒 ⇒ **不能同步 HTTP**，
#     必须"先上传拿 song_id，再分析、轮询进度"；
#   · 分离在 `.venv_ml`（torch/demucs）里跑，主服务只 spawn 子进程；
#   · 用户的歌曲是**私密文件**，只存本机、不进 git（data/ 已忽略）。
SONG_ROOT = os.path.join(ROOT, "data", "songs")
SONG_JOB_ROOT = os.path.join(SONG_ROOT, "_jobs")
SONG_BACKING_ROOT = os.path.join(ROOT, "data", "backing")
SONG_SEP_PY = os.path.join(ROOT, ".venv_ml", "Scripts", "python.exe")
SONG_SEP_SCRIPT = os.path.join(ROOT, "analysis", "song_separate.py")
SONG_LYRICS_SCRIPT = os.path.join(ROOT, "analysis", "song_lyrics.py")
SONG_MAX_SECTION_MS = 600_000       # 整首上限 10 分钟（4–5 分钟的歌可一次唱完）
SONG_MIN_SECTION_MS = 8_000         # 少于 8 秒没有练习意义
SONG_MAX_SOURCE_BYTES = 120 * 1024 * 1024
SONG_MAX_SOURCE_S = 600             # 上传文件最长 10 分钟（与单次分析上限一致）
SONG_JOBS: dict[str, dict] = {}
_SONG_LOCK = threading.Lock()
_SONG_SEP_LOCK = threading.Lock()    # CPU 分离串行，别让两个 4 分钟任务互相拖死
_SONG_ASR_LOCK = threading.Lock()    # Whisper 也吃 CPU；串行


def _cleanup_song_jobs(max_age_s: int = 24 * 3600) -> None:
    """清掉 24 小时前没确认的歌曲分析目录（source.wav 可能几十 MB）。

    ⚠️ 只动 `data/songs/_jobs/` 下以 `song_` 开头的目录；不碰 data/backing
       （那是已确认模板的伴奏/原唱，由模板删除负责）。
    """
    try:
        os.makedirs(SONG_JOB_ROOT, exist_ok=True)
        now = time.time()
        for name in os.listdir(SONG_JOB_ROOT):
            if not _song_id_ok(name):
                continue
            p = os.path.join(SONG_JOB_ROOT, name)
            try:
                if now - os.path.getmtime(p) > max_age_s:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def _song_id_ok(sid: str) -> bool:
    return bool(re.fullmatch(r"song_[0-9a-f]{12}", sid or ""))


def _song_dir(sid: str) -> str:
    return os.path.join(SONG_JOB_ROOT, sid)


def _song_set(sid: str, **kw) -> None:
    with _SONG_LOCK:
        job = SONG_JOBS.get(sid)
        if job is not None:
            job.update(kw)


def _song_get(sid: str) -> Optional[dict]:
    with _SONG_LOCK:
        job = SONG_JOBS.get(sid)
        return dict(job) if job else None


def _job_rel(p: Optional[str]) -> str:
    if not p:
        return ""
    try:
        return os.path.relpath(os.path.abspath(p), ROOT)
    except (OSError, ValueError):
        return p


def _job_abs(p: Optional[str]) -> str:
    if not p:
        return ""
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def _notes_payload(notes) -> list:
    """只存确认模板需要的字段；与 `templates._notes_to_json` 同一口径。"""
    out = []
    for i, n in enumerate(notes or []):
        if getattr(n, "pitch_midi", None) is None:
            continue
        out.append({
            "note_index": int(getattr(n, "note_index", i)),
            "start_ms": int(n.start_ms), "end_ms": int(n.end_ms),
            "pitch_midi": round(float(n.pitch_midi), 3),
            "pitch_std": (None if getattr(n, "pitch_std", None) is None
                          else round(float(n.pitch_std), 3)),
            "voiced_ratio": (None if getattr(n, "voiced_ratio", None) is None
                             else round(float(n.voiced_ratio), 3)),
            "long_enough_for_vibrato": bool(
                getattr(n, "long_enough_for_vibrato", False)),
        })
    return out


def _notes_from_payload(rows: list):
    from analysis.notes import Note
    out = []
    for i, r in enumerate(rows or []):
        try:
            out.append(Note(
                note_index=int(r.get("note_index", i)),
                start_ms=int(r["start_ms"]), end_ms=int(r["end_ms"]),
                pitch_midi=float(r["pitch_midi"]),
                pitch_std=(None if r.get("pitch_std") is None
                           else float(r["pitch_std"])),
                voiced_ratio=(None if r.get("voiced_ratio") is None
                              else float(r["voiced_ratio"])),
                long_enough_for_vibrato=bool(r.get("long_enough_for_vibrato")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _save_song_job(sid: str) -> None:
    """把 READY 的分析结果落盘，服务重启/页面重开后还能看到这首歌。"""
    job = _song_get(sid)
    if not job or job.get("status") != "READY":
        return
    d = _song_dir(sid)
    payload = {
        "song_id": sid,
        "title": job.get("title"),
        "singer_id": job.get("singer_id") or "",
        "duration_ms": int(job.get("duration_ms") or 0),
        "start_ms": int(job.get("start_ms") or 0),
        "end_ms": int(job.get("end_ms") or 0),
        "status": "READY",
        "quality": job.get("quality") or {},
        "quality_mode": job.get("quality_mode") or "clean",
        "lyrics": job.get("lyrics") or [],
        "lyrics_source": job.get("lyrics_source") or "",
        "lyrics_error": job.get("lyrics_error") or "",
        "notes": _notes_payload(job.get("notes") or []),
        "paths": {
            "vocals": _job_rel(job.get("vocals_path")),
            "instrumental": _job_rel(job.get("instrumental_path")),
            "guide": _job_rel(job.get("guide_path")),
        },
        "leak": job.get("leak") or {},
        "seconds": job.get("seconds"),
    }
    try:
        tmp = os.path.join(d, "job.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(d, "job.json"))
    except OSError as e:
        print(f"[SingLens] ⚠️ 歌曲分析结果落盘失败（{sid}）：{e}")


def _load_song_jobs() -> None:
    """启动时把 data/songs/_jobs 里没确认的歌曲读回内存。

    这样"上传了 → 关掉页面 → 再打开"还能在 E 屏看到它；
    已确认的歌曲不在 _jobs 里（确认时删了分析目录），由模板列表显示。
    """
    try:
        os.makedirs(SONG_JOB_ROOT, exist_ok=True)
        names = os.listdir(SONG_JOB_ROOT)
    except OSError:
        return
    loaded = 0
    for name in names:
        if not _song_id_ok(name):
            continue
        d = _song_dir(name)
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        job = {
            "song_id": name, "singer_id": meta.get("singer_id") or "",
            "title": meta.get("title") or "未命名歌曲",
            "duration_ms": int(meta.get("duration_ms") or 0),
            "start_ms": 0, "end_ms": int(meta.get("duration_ms") or 0),
            "status": "INSPECTED", "stage": "已读取，未分析",
            "progress": 0, "notes": [], "quality": None,
            "created": meta.get("created") or 0,
        }
        jp = os.path.join(d, "job.json")
        if os.path.exists(jp):
            try:
                with open(jp, encoding="utf-8") as f:
                    saved = json.load(f)
                paths = saved.get("paths") or {}
                vp = _job_abs(paths.get("vocals"))
                ip = _job_abs(paths.get("instrumental"))
                gp = _job_abs(paths.get("guide"))
                if os.path.exists(vp) and os.path.exists(ip):
                    notes = _notes_from_payload(saved.get("notes") or [])
                    if notes and os.path.exists(gp):
                        job.update({
                            "status": "READY", "stage": "上次已分析，可继续",
                            "progress": 100,
                            "start_ms": int(saved.get("start_ms") or 0),
                            "end_ms": int(saved.get("end_ms") or 0),
                            "notes": notes,
                            "quality": saved.get("quality") or {},
                            "quality_mode": saved.get("quality_mode") or "clean",
                            "lyrics": saved.get("lyrics") or [],
                            "lyrics_source": saved.get("lyrics_source") or "",
                            "lyrics_error": saved.get("lyrics_error") or "",
                            "vocals_path": vp, "instrumental_path": ip,
                            "guide_path": gp, "leak": saved.get("leak") or {},
                        })
            except (OSError, json.JSONDecodeError) as e:
                print(f"[SingLens] ⚠️ 歌曲记录读不出来（{name}）：{e}")
        with _SONG_LOCK:
            SONG_JOBS[name] = job
        loaded += 1
    if loaded:
        print(f"[SingLens] 读回 {loaded} 首未确认的上传歌曲")

def _song_public(job: dict) -> dict:
    """只把前端要的字段发出去（不把 Note 对象/内部路径丢给客户端）。"""
    out = {
        "song_id": job.get("song_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "progress": job.get("progress", 0),
        "title": job.get("title"),
        "duration_ms": job.get("duration_ms"),
        "start_ms": job.get("start_ms"),
        "end_ms": job.get("end_ms"),
        "error": job.get("error"),
        "template_id": job.get("template_id"),
        "quality_mode": job.get("quality_mode") or "clean",
    }
    if job.get("status") == "READY":
        q = job.get("quality") or {}
        lyrics = job.get("lyrics") or []
        out.update({
            "quality": q,
            "note_count": len(job.get("notes") or []),
            "section_ms": int(job.get("end_ms") or 0) - int(job.get("start_ms") or 0),
            "lyrics": lyrics,
            "lyrics_count": len(lyrics),
            "lyrics_source": job.get("lyrics_source") or "",
            "lyrics_error": job.get("lyrics_error") or "",
            "preview": {
                "vocal": f"/api/song/{job['song_id']}/preview.wav?kind=vocal",
                "instrumental": f"/api/song/{job['song_id']}/preview.wav?kind=instrumental",
                "guide": f"/api/song/{job['song_id']}/preview.wav?kind=guide",
            },
        })
    return out


def _song_lyrics(vocals: str) -> tuple[list, str, str]:
    """跑 Whisper 识别歌词。失败**不阻断**跟唱，只把原因带回去。

    返回 (lines, source, error)。
    """
    if not os.path.exists(SONG_LYRICS_SCRIPT):
        return [], "", "歌词脚本不存在"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 2026-10-20：优先用 medium（中文歌词明显比 small 准）；
    #   没有 medium 时退回 small；两个都没有就交给脚本报错。
    model = "small"
    mm = os.path.join(ROOT, "data", "models", "whisper", "medium", "model.bin")
    sm = os.path.join(ROOT, "data", "models", "whisper", "small", "model.bin")
    try:
        if os.path.exists(mm) and os.path.getsize(mm) > 100 * 1024 * 1024:
            model = "medium"
        elif os.path.exists(sm) and os.path.getsize(sm) > 100 * 1024 * 1024:
            model = "small"
    except OSError:
        pass
    cmd = [SONG_SEP_PY, SONG_LYRICS_SCRIPT, "--input", vocals,
           "--model", model,
           "--model-dir", os.path.join(ROOT, "data", "models", "whisper")]
    try:
        with _SONG_ASR_LOCK:
            p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=900)
    except subprocess.TimeoutExpired:
        return [], "", "歌词识别超时（模型首次下载/CPU 太慢）"
    except Exception as e:  # noqa: BLE001
        return [], "", f"歌词识别起不来：{e}"
    out = (p.stdout or "").strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if j.get("ok"):
            lines = [x for x in (j.get("lyrics") or [])
                     if isinstance(x, dict) and x.get("text")]
            return lines, f"whisper-{j.get('model') or 'small'}", ""
        return [], "", (j.get("message") or "歌词识别失败")[:200]
    return [], "", ((p.stderr or out or "歌词识别没有输出")[-200:])


def _run_song_job(sid: str) -> None:
    """后台线程：Demucs 子进程 → 原唱参考 → 质量门控 → 伴奏引导。"""
    job = _song_get(sid)
    if not job:
        return
    d = _song_dir(sid)
    src = os.path.join(d, "source.wav")
    start = int(job.get("start_ms") or 0)
    end = int(job.get("end_ms") or 0)
    dur_s = max(0.0, (end - start) / 1000.0)
    vocals = os.path.join(d, "vocals.wav")
    inst = os.path.join(d, "instrumental.wav")
    guide = os.path.join(d, "guide.wav")
    try:
        if not os.path.exists(SONG_SEP_PY):
            raise RuntimeError("缺少 .venv_ml（Demucs 环境）。"
                               "请先按文档安装 torch + demucs。")
        _song_set(sid, status="RUNNING", stage="正在分离人声与伴奏…",
                  progress=15)
        cmd = [SONG_SEP_PY, SONG_SEP_SCRIPT,
               "--input", src, "--out-dir", d,
               "--start", f"{start / 1000.0:.3f}",
               "--duration", f"{dur_s:.3f}"]
        # ★ 2026-10-20：单模型快，但高频「滋滋」明显；四模型 bag 慢 2–3 倍，
        #   人声/伴奏干净很多。默认 clean；赶时间可选 fast。
        qmode = job.get("quality_mode") or "clean"
        if qmode == "clean":
            cmd += ["--model", "htdemucs_ft"]
        else:
            cmd += ["--model", "f7e0c4bc"]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("SINGLENS_DEMUCS_REPO",
                       os.path.join(ROOT, "data", "models", "demucs"))
        with _SONG_SEP_LOCK:
            t0 = time.time()
            # 进度估算：单模型约 0.5× 实时；四模型 bag 约 2× 实时
            factor = 2.0 if qmode == "clean" else 0.62
            eta = max(8.0, dur_s * factor)
            p = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True,
                                 encoding="utf-8", errors="replace",
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
            last = {"err": ""}
            assert p.stdout is not None

            def _pump() -> None:
                for raw in p.stdout:                       # 阻塞在这个线程里
                    line = (raw or "").strip()
                    if not line:
                        continue
                    print(f"[song {sid}] {line[:300]}", flush=True)
                    if '"ok": false' in line or '"ok":false' in line:
                        last["err"] = line
                    if '"stage": "separate"' in line:
                        _song_set(sid, stage="正在分离人声与伴奏…", progress=30)
                    elif '"stage": "load"' in line:
                        _song_set(sid, stage="正在加载分离模型…", progress=18)

            th = threading.Thread(target=_pump, daemon=True)
            th.start()
            while p.poll() is None:
                el = time.time() - t0
                _song_set(sid, progress=min(72, 30 + int(42 * el / eta)))
                time.sleep(0.4)
            code = p.wait()
            th.join(timeout=2.0)
            last_err = last["err"]
        if code != 0:
            raise RuntimeError("分离失败。" + (last_err[:200] if last_err else ""))
        _song_set(sid, stage="正在从原唱提取旋律…", progress=78)
        from analysis import song_import as si
        ref = si.extract_reference(vocals)
        if not ref.get("ok"):
            raise RuntimeError(ref.get("message") or "原唱人声提取失败。")
        leak = si.measure_leak(vocals, inst)
        quality = si.assess_quality(ref, leak)
        _song_set(sid, stage="正在准备伴奏…", progress=82)
        si.make_guide(inst, guide)
        _song_set(sid, stage="正在识别歌词（首次可能要下载模型）…", progress=86)
        lyrics, lsrc, lerr = _song_lyrics(vocals)
        _song_set(sid, stage="准备就绪", progress=100,
                  notes=ref["notes"], quality=quality,
                  vocals_path=vocals, instrumental_path=inst,
                  guide_path=guide, leak=leak, lyrics=lyrics,
                  lyrics_source=lsrc, lyrics_error=lerr,
                  seconds=round(time.time() - t0, 1),
                  status="READY")
        _save_song_job(sid)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 歌曲分析失败（{sid}）：{e}")
        _song_set(sid, status="FAILED", stage="分析失败", progress=0,
                  error=str(e)[:300])


def _wav_duration(path: str) -> float:
    import soundfile as sf
    try:
        info = sf.info(path)
        return float(info.frames) / float(info.samplerate)
    except Exception:  # noqa: BLE001
        return float(librosa.get_duration(path=path))



# ══════════════════════════════════════════════════════════════
# 参考旋律与演唱时长的匹配
# ══════════════════════════════════════════════════════════════
# ⚠️ 为什么需要这一段（2026-09-20 修，实测教训）：
#
#   内置参考《小星星》第一句只有 **7 个音 / 3.5 秒**，而真实录音常是 **19–29 秒**
#   （用户会把一句唱好几遍，或唱整首歌）。
#
#   把整段丢给对齐，会发生什么（实测 `experiments/_pick_server_alignment.py`）：
#     · 匹配到的音散落在整段 29 秒上 → 节奏偏差被算成几秒
#     · `rubato` 飙到 **7000+ ms**（正常应是几十 ms）
#     · 这些垃圾值**直接进了实验数据库** —— 比降级更危险，
#       因为它看起来是个正常数字，与 `docs/同类项目评分算法源码级对比.md`
#       §6.3 记的"分数与演唱质量脱钩"是同一类问题。
#
#   也试过"把参考重复到覆盖整段"，**更差**：音准分全 0、rubato 更大、还有降级。
#   参考旋律本来就是**一句**，硬套多句只会更乱。
#
#   ⇒ 正确做法：**只评参考真正覆盖的那一段演唱**（与 `score_phrase.py` 同口径）。
#     窗口在所有等长位置里挑，判据：先过对齐质量，再按**音准分**取胜者。
#     （踩过坑：只按"残差最小"挑窗口是错的 —— 残差小 ≠ 唱得准。）
#
#   并明确告诉用户"本次只评了前 N 个音"，而不是悄悄给一个覆盖不上的分数。
ALIGN_WINDOW_BEST_OF = True


def _transpose_ref(ref: ReferenceMelody, semitones: int) -> ReferenceMelody:
    """把参考整体移调 N 个半音（0 原样返回）。

    ★ 只改参考，不改用户音频；音准/节奏都按移调后的参考算。
      这让"这首歌太高，唱不上去"有了正解，而不是让用户硬唱、再被扣分。
    """
    n = int(semitones or 0)
    if not n:
        return ref
    from analysis.align import RefNote
    notes = [RefNote(index=i, start_ms=x.start_ms, end_ms=x.end_ms,
                     pitch_midi=float(x.pitch_midi) + n)
             for i, x in enumerate(ref.notes)]
    return ReferenceMelody(reference_id=ref.reference_id, title=ref.title,
                           notes=notes, mode=ref.mode, source=ref.source,
                           transpose=int(getattr(ref, "transpose", 0) or 0) + n)


def _builtin_ref_for(ref: ReferenceMelody, first_start_ms: int) -> ReferenceMelody:
    """内置参考要按演唱起点重新锚定；用户模板（模式 B）不重新锚定。

    ⚠️ 重载时必须把 `ref.transpose` 带回去：否则移调 +2 的参考在
       “按起点重锚”这一步会悄悄回到原调（用户唱 +2，系统按 0 判）。
    """
    tr = int(getattr(ref, "transpose", 0) or 0)
    if ref.source == "builtin_score" and ref.reference_id in BUILTIN:
        base = load_builtin(ref.reference_id, first_start_ms=first_start_ms)
        return _transpose_ref(base, tr) if tr else base
    return ref


def _align_window(notes, ref, f0b, sr):
    """在演唱音符里挑一段与参考等长的最佳窗口并对齐。

    ★ 判据顺序：先过对齐质量（ok + 覆盖 + 残差），再按**音准分**（softmax 后的
      实际分数）取胜者。
      踩过的坑：只按"残差最小"挑窗口是错的 —— 一个错位的窗口照样能对齐得很紧，
      实测会把音准 82.9 的窗口换成音准 0 的窗口。**残差小 ≠ 唱得准。**

    返回 (aligned, align_info, used_notes, extra)
    """
    n_ref = len(ref.notes)
    extra = {"notes_total": len(notes), "phrase_window": False}
    # ★ 2026-10-23：**先看时长，再看音符数**。
    #   之前只要“用户音符比参考多”就进窗口搜索；实测一首 4:17 的整歌
    #   （447 个用户音符）对 4:21 的整歌模板（355 个参考音符）时，
    #   搜索把用户第 86 个音（75.2s）起的 355 个音当窗口，
    #   硬对到参考第 0 个音（21.1s）—— `onset_dev` 开头变成 54s，
    #   后续拟合出 7s 的假 rubato，整份报告的节奏分失真。
    #   参考与演唱的**跨度**都接近整首歌时，就应该整段对齐；
    #   窗口只用于“参考是一句、用户唱了多遍/多句”的场景。
    ref_span = (ref.notes[-1].end_ms - ref.notes[0].start_ms
                if len(ref.notes) >= 2 else 0)
    user_span = (notes[-1].end_ms - notes[0].start_ms
                 if len(notes) >= 2 else 0)
    full_song = (ref_span >= 60_000 and user_span >= ref_span * 0.8
                 and user_span <= ref_span * 1.25)
    if n_ref == 0 or len(notes) <= n_ref or full_song:
        a, i = align_to_reference(notes, ref, f0=f0b, sr=sr)
        # ★ 这条提前返回也必须带 pitch_offset。
        #   踩过的坑：只有"选窗口"那条分支传了它，于是**刚好一句、不用挑窗口**的
        #   情况（最常见的正常情况！）meta.pitch_offset_semitones 恒为 None，
        #   用户降调唱了也得不到"你比参考低了 3 个半音，换个调跟"的提示。
        extra["pitch_offset_semitones"] = i.get("pitch_offset_semitones")
        extra["notes_used"] = len(notes)
        extra["full_song"] = bool(full_song)
        return a, i, notes, extra

    cands = []
    for s in range(0, len(notes) - n_ref + 1):
        used = notes[s:s + n_ref]
        r = _builtin_ref_for(ref, int(used[0].start_ms))
        try:
            a, i = align_to_reference(used, r, f0=f0b, sr=sr)
        except Exception:  # noqa: BLE001
            continue
        if not i.get("ok") or i.get("residual_median_abs") is None:
            continue
        devs = [m.pitch_cents_dev for m in a if m.pitch_cents_dev is not None]
        if len(devs) < 3:
            continue
        # 与 pitch.py 同口径的软阈值音准分（±25 音分容差）
        pen = float(np.mean([max(0.0, abs(d) - PITCH_TOLERANCE_CENTS) for d in devs]))
        score = 100.0 * (1.0 - min(pen / FULL_PENALTY_CENTS, 1.0))
        cands.append((-score, -float(i.get("ref_coverage", 0.0)), s))

    if not cands:
        a, i = align_to_reference(notes, ref, f0=f0b, sr=sr)
        extra["pitch_offset_semitones"] = i.get("pitch_offset_semitones")
        extra["notes_used"] = len(notes)
        return a, i, notes, extra

    cands.sort()
    s = cands[0][2]
    used = notes[s:s + n_ref]
    a, i = align_to_reference(used, _builtin_ref_for(ref, int(used[0].start_ms)),
                              f0=f0b, sr=sr)
    # ★ pitch_offset 必须从这里传下去：align_stats() 只保留固定的几个键，
    #   会把 pitch_offset_semitones 丢掉（实测 meta 里变成 None）。
    extra["pitch_offset_semitones"] = i.get("pitch_offset_semitones")
    extra.update({"phrase_window": True, "notes_used": len(used),
                  "phrase_start_note": s})
    return a, i, used, extra
# ── 契约里的错误码（UI 第 08 屏的状态与之一一对应）──────────────
# 契约 §3.2 的 6 个【业务】错误码（对应 UI 第 08 屏的失败状态）
ERROR_CODES = ("MIC_DENIED", "TOO_NOISY", "NO_VOICE", "TOO_SHORT",
               "RANGE_SATURATED", "REF_MISSING",
               # 2026-09-23 审计后加：体积/时长超限。
               # 契约 §3.2 原本只有上面 6 个业务码，但"上传太大/太长"是**正常会遇到**
               # 的用户错误（手机录了整首歌），必须有明确的码让前端给出可读提示。
               "TOO_LARGE", "TOO_LONG")

# ★ 契约 v1.1 §3.4 补的【通用】错误码 —— 原来只定义了上面 6 个业务码，
#   API 层的 404/409/400 该报什么码从没写。
#   这导致一个真 bug：compare 接口在"报告没算完"时调 fail("NOT_FOUND", …, 409)，
#   409 配 NOT_FOUND 语义自相矛盾（UI 会以为会话不存在，其实只是要等）。
#   而改成 fail("CONFLICT", …, 409) 又会踩到下面 fail() 里的 assert —— 因为
#   CONFLICT 不在白名单里，assert 直接把 500 抛出去。
HTTP_CODES = {
    "BAD_REQUEST": 400,   # 入参不合法
    "UNAUTHORIZED": 401,  # 没有凭据（本项目的"凭据"是**操作者令牌**，见 §实验写接口）
    "FORBIDDEN":  403,    # 有这个端点，但不允许你这么用（跨站/非本机 Host）
    "NOT_FOUND":   404,   # 资源不存在
    "METHOD_NOT_ALLOWED": 405,
    "CONFLICT":    409,   # 资源在，但当前状态不允许该操作（如报告未就绪）
    "TOO_LARGE":  413,    # 体积/时长超限（也是 ERROR_CODES 里的业务码）
    "INTERNAL":    500,   # 服务端内部错误
}
ALL_ERROR_CODES = ERROR_CODES + tuple(HTTP_CODES)

SR = 22050
# 引导音轨开头的 4 拍预备（2 秒）。实时时间轴从第一块音频开始，
# 而参考音符号表从歌曲 0 秒开始；实时参考必须整体后移这 2 秒，
# 否则屏幕上的“该唱这个音”会比耳朵听到的早 2 秒。
GUIDE_LEAD_MS = 2000
RT_WINDOW_MS = 5000          # 契约：实时滚动窗口统一 5 秒
RT_PITCH_HZ = 20             # 契约：pitch 帧 ~20Hz
RT_METRICS_HZ = 1            # 契约：metrics 帧 1Hz
RT_PITCH_HZ_MAX = 50         # pitch 帧上限保护（防止客户端灌数据打爆连接）
MIN_USABLE_MS = 2000         # 短于此的录音判 TOO_SHORT

# 实验录音目录的配额（默认 1 GiB）。见上传处理器里的说明：单次有上限、
# **次数没有** ⇒ 可以被反复上传写满磁盘。环境变量 `EXP_DIR_QUOTA_MB` 可调。
EXP_DIR_QUOTA_BYTES = int(os.environ.get("EXP_DIR_QUOTA_MB") or 1024) * 1048576

# ══════════════════════════════════════════════════════════════
# 验收基准（离线实测值）—— ★ 唯一真相源
# ══════════════════════════════════════════════════════════════
# 为什么要有这块（2026-09-24）：
#   第 06 屏要显示"三条验收指标达标了吗"。其中前两条是**离线一次性实测**的
#   基准（不是每次请求都能重算的运行时值），于是它们被写死在两处：
#     · `web/js/views/s6.js:66`（页面上那行字）
#     · `web/js/bindings.js:1191`（动态重画那行字时又写了一遍）
#   两个副本必然漂移，而且**已经漂了**：两处都写着"一致率 95.06%"，
#   而原始实测是 96.41%（见下面 source）。`项目记忆.md` §27.2 早就记过
#   "95.06% 是错的，原文 96.41%" —— 文档改了，代码里的两个副本没改。
#   ⇒ 数字只留**这一份**，由 `/api/experiment/summary` 发给前端，界面不再自己写。
#
# ★ `source` 字段是给答辩用的：每个数字都要能指回原始输出文件（§27.2 的规矩）。
#   `measured` 取自设计规格的变更记录，不是凭记忆复述的二手值。
ACCEPTANCE_BENCHMARKS = {
    "rt_latency": {
        "label": "实时管线端到端延迟",
        "measured": 3.1, "unit": "ms", "threshold": 200.0,
        "op": "lt", "decimals": 1,
        "measured_text": "P50 2.2ms / P95 3.1ms",
        "source": "docs/论文骨架与数据地图.md §6 数据地图（目标 <200ms，余量 60 倍）",
    },
    "pitch_agreement": {
        "label": "双管线音高一致率",
        "measured": 96.41, "unit": "%", "threshold": 95.0,
        "op": "gt", "decimals": 2,
        "measured_text": "96.41%（八度错误率 0.00%）",
        "source": "docs/superpowers/specs/2026-09-11-演唱AI陪练-design.md:548 "
                  "（第 1 周检查点，来源 sep12_5pm_vocal.wav 实测）",
    },
}


def _acceptance_benchmarks() -> list[dict]:
    """把基准算成"达标/未达标"的判据，供前端统一渲染。

    ★ 判据在这里算，不在前端算：前端只负责显示，不然又是公式两份副本
      （同样的坑见 `/api/singer/{sid}/profile` 的 `normalized`）。
    """
    out = []
    for key, b in ACCEPTANCE_BENCHMARKS.items():
        # 单位是 % 时不留空格（中文排版里 "95 %" 是错的）
        _sp = "" if b["unit"] == "%" else " "
        if b["op"] == "lt":
            ok = b["measured"] < b["threshold"]
            cmp_text = f"&lt; {b['threshold']:g}{_sp}{b['unit']}"
        else:
            ok = b["measured"] > b["threshold"]
            cmp_text = f"&gt; {b['threshold']:g}{_sp}{b['unit']}"
        out.append({
            "id": key, "label": b["label"],
            "measured": b["measured"], "unit": b["unit"],
            "threshold": b["threshold"], "cmp_text": cmp_text,
            "measured_text": b["measured_text"],
            "decimals": b["decimals"],
            "ok": ok, "source": b["source"],
        })
    return out


def _dir_bytes(path: str) -> int:
    """目录里所有普通文件的大小之和（读不到的文件跳过）。"""
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_file():
                        total += e.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total

# ── 上传/录音上限（2026-09-23 审计后加）─────────────────────
# ⚠️ 为什么需要：原来这两项**都没有上限**。
#    实测 64 MiB 的请求体被完整接受（RSS +80 MB），300 秒的录音
#    能让整个 API 冻结 10 秒（见 MAX_AUDIO_SEC 的说明）。
#    真实用户撞不上，但"一个请求打爆服务"不该是本地工具也留着的口子。
#
# 取值依据：
#   · 上传 64 MiB —— 22050Hz 单声道 PCM16 约 43 KB/秒，64 MiB ≈ 25 分钟；
#     整首 4–5 分钟的手机录音通常 5–15 MB。
#   · 录音 600 秒 —— 整首 4–5 分钟，留 2 分钟余量。
#     整首跟唱后 PYIN 仍在线性可接受范围内（实测 0.075–0.13× 实时）。
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_RECORD_SEC = 600
# 分析用的总音频上限 —— 与录音上限一致，整首可一次分析。
MAX_AUDIO_SEC = 600
# 实时管线**缓冲**的音频上限（样本数）。超过就丢头部并记账。
#
# ⚠️ 这一条我第一版设计错了，记下来免得再犯：
#    原来写 `RT_KEEP_SEC = MAX_AUDIO_SEC + 5`，意思是"保留 185 秒、超过才裁"。
#    但流在**达到 MAX_AUDIO_SEC 时就主动结束了**（见 stream 循环里的 done 分支），
#    ⇒ 缓冲永远到不了 185 秒 ⇒ **滚动窗口那段是死代码，一次都不会执行**。
#    验证时看出来的：灌了 1900 秒音频，`analyzed_from_ms` 却是 None（"没丢过"）。
#
# 而且"滚动窗口"这个思路本身就不对：`finish()` 要拿**全程**去对齐、算六指标与
# 分段证据 —— 把开头的音频丢掉，报告就残了。所以正确的做法不是"边收边丢"，
# 而是 ① 给缓冲一个**硬上限**防内存爆掉 ② 上限之内**照单全收**
# ③ 真丢了就**如实记账**。
#
# 取值：MAX_AUDIO_SEC + 10 秒的余量 —— 正常情况下永远碰不到；
# 碰到的唯一可能是客户端**单帧塞进巨量数据**（比如一帧 16 MiB 而不是 46ms 的 2 KB），
# 那时才丢，并让 `finish()` 如实说明"只分析了后 N 秒"。
MAX_BUFFER_SAMPLES = SR * (MAX_AUDIO_SEC + 10)
CHUNK_GUARD_SEC = 2            # 攒够这么多才检查一次，避免每块都动列表
# 内存里的会话上限。超过就回收最久没活动的。
# 依据：正常使用同时最多一两个会话（一个人练、一个标签页）；
# 留 50 是给"同一 WiFi 下几个同学各开一个"的余量。
MAX_SESSIONS = 50
SESSION_TTL_SEC = 30 * 60      # 30 分钟没动静就当废弃（还在唱的不会被回收）
WS_IDLE_TIMEOUT_SEC = 120      # WS 空闲这么久就断开（前端每 46ms 发一块，唱歌时不会触发）

# ★ 实时管线的 YIN 参数
#   踩过的坑：一开始对【近 3 秒】窗口跑 YIN、hop=512（129 帧），
#   每块音频（93ms）要算一次 —— 服务端根本跟不上，而且 mark_saturated 收到了
#   字符串 "C2"（它要 Hz 数值）直接抛异常。
#   正确做法：只对最近一小段跑，用大 hop 只出 2~3 帧，取最后一帧。
#   实时管线的目标是「让用户看见自己的声音」，不是精确测量 —— 精确测量是精算管线的事。
REALTIME_FRAME = 2048        # 93ms，标准帧长
RT_YIN_SAMPLES = 4096        # 只保留最近 ~190ms 做实时音高
FMIN_HZ = librosa.note_to_hz(DEFAULT_FMIN)
FMAX_HZ = librosa.note_to_hz(DEFAULT_FMAX)

app = FastAPI(title="SingLens API", version="1.0",
              # ★ 关掉自带的文档面（2026-09-24，审计 §5.5）。
              #   原先 `/docs`（Swagger UI，从 CDN 拉资源）与 `/openapi.json`
              #   （15761 字节，含全部端点与参数名）**匿名可访问** ——
              #   等于把"怎么调这个没有鉴权的后端"做成了可视化面板，
              #   Swagger 里可以直接点 Try it out。
              #   需要时用环境变量打开：`SINGLENS_DOCS=1`（本机调试用）。
              docs_url=("/docs" if os.environ.get("SINGLENS_DOCS") else None),
              redoc_url=None,
              openapi_url=("/openapi.json" if os.environ.get("SINGLENS_DOCS") else None))

# ══════════════════════════════════════════════════════════════
# 只服务"本机 / 本局域网"的守卫（2026-09-24，审计 §3.1、§5.6）
#
# ★ 审计查出的真实问题：**任何网页都能读走全部数据**。
#   根因不是"没有登录"，而是这一行：
#       CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
#   它给每个网站的 JS 发了通行证 —— 同学在手机上打开一个恶意页面，
#   那个页面就能 `fetch('http://192.168.1.10:8000/api/singer/S01/history')`
#   并把响应**读出来**（`Access-Control-Allow-Origin: *` 正是允许读的那张票）。
#   配合 §3.2 的写接口，还能**伪造盲评数据**。
#
# ★ 为什么不是"加个登录"：
#   这个系统的产品前提是 **LOCAL ONLY · NO UPLOAD**，没有账号，
#   用户就是"坐在这台电脑前的人"。真正的威胁是**浏览器里的第三方页面**
#   和**DNS rebinding**（攻击者域名解析到 127.0.0.1 ⇒ 与我们的服务同源 ⇒
#   绕过同源策略直接读）。两者都能用"看 Host / Origin / Sec-Fetch-Site"挡掉，
#   而且**不需要用户做任何事**（不要密码、不要令牌）。
#
# ★ 顺带说明**挡不住什么**（诚实标注，别假装有了鉴权）：
#   本机上任何进程都能直接读 `data/experiment.db`，甚至在同目录起一个脚本
#   以「本机请求」的身份访问。令牌挡不住它 —— 所以这一层做的是
#   **浏览器侧**的防线 + 数据校验，不是账号体系。
#   等有了多用户（G3）再谈"谁能看谁的数据"。
# ══════════════════════════════════════════════════════════════
ALLOWED_HOSTS: set[str] = set()
ALLOWED_ORIGINS: set[str] = set()
_SERVE_PORT = 8000


def _local_hostnames() -> set[str]:
    """本机的名字（`Saku`、`saku.local`…）。

    ⚠️ 为什么必须有（2026-09-24，**线上实测踩到的回归**）：
       第一版白名单只有 `127.0.0.1 / localhost / ::1` + 绑定地址 + 网卡 IP。
       而用**机器名**打开是很自然的用法（`http://Saku:8000/`，
       尤其局域网里从别的设备访问）—— 那会命中 Host 检查，
       **所有请求（含 `/`、`/js/*.js`、`/css/*.css`）全 403**。
       在浏览器里看到的就是："没有能互动的组件、也没有大屏"，
       因为页面和脚本一个都没进来。**安全改动把正常用法堵死了** ——
       这正是 §62.1 里我自己写下的那条"最容易顺手堵死功能"，
       结果还是踩了：当时只想到 IP 和 localhost，没想到机器名。
    """
    names: set[str] = set()
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            n = getter()
            if n:
                names.add(n.lower())
        except Exception:  # noqa: BLE001
            pass
    for env in ("COMPUTERNAME", "HOSTNAME"):
        v = os.environ.get(env)
        if v:
            names.add(v.lower())
    # `saku.local` → 也接受 `saku`（Windows 上两种写法都能通）
    for n in list(names):
        if "." in n:
            names.add(n.split(".")[0])
    return {n for n in names if n}


def _all_local_ips() -> set[str]:
    """**所有**本机 IPv4（含虚拟网卡、VPN、Hyper-V 那些）。

    ⚠️ 为什么不用 `_lan_candidates()` 那份"挑过的"列表：
       那份是为**打印给人看**排序的（排除了 VMware / Hyper-V / PPP），
       而白名单要回答的是另一个问题 ——"这个 Host 是不是指向本机"。
       虚拟网卡的地址**也是本机地址**，用它打开完全正常，
       排除掉就会重演"安全改动堵死正常用法"。
       （而且 rebinding 用的是**域名**，放行本机 IP 不会削弱那道防线。）
    """
    ips: set[str] = {"127.0.0.1", "::1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except Exception:  # noqa: BLE001
        pass
    try:
        import re as _re
        import subprocess as _sp
        r = _sp.run(["ipconfig"], capture_output=True, text=True,
                    encoding="gbk", errors="replace")
        for m in _re.finditer(r"IPv4[^:：]*[:：]\s*([0-9.]+)", r.stdout or ""):
            ips.add(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    try:
        # 默认路由那张网卡的地址（UDP connect 不真的发包）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:  # noqa: BLE001
        pass
    return {i for i in ips if i}


def init_network_guard(host: str, port: int, extra_hosts=()) -> None:
    """按实际绑定的地址算出"合法 Host / Origin"白名单。

    为什么要按绑定地址算，而不是写死 `127.0.0.1`：
      本服务的**默认模式是绑局域网 IP**（同学的手机要能打开 /rate 评分）。
      写死回环会把正常用法一起挡掉 —— "堵漏洞顺手堵死功能"是本项目
      反复踩到的坑（见 §60 的配置写入、§57 的夹具、以及 `_local_hostnames`
      里记的那次回归）。
    """
    global _SERVE_PORT
    _SERVE_PORT = port
    hosts = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
    hosts |= _local_hostnames()
    hosts |= _all_local_ips()
    if host and host not in ("0.0.0.0", "::"):
        hosts.add(host)
    for h in extra_hosts:
        if h:
            hosts.add(h)
    ALLOWED_HOSTS.clear()
    ALLOWED_HOSTS.update(h.lower() for h in hosts)
    ALLOWED_ORIGINS.clear()
    for h in ALLOWED_HOSTS - {"0.0.0.0"}:
        # IPv6 字面量在 Origin 里要带方括号
        lit = f"[{h}]" if ":" in h else h
        ALLOWED_ORIGINS.add(f"http://{lit}:{port}")
        ALLOWED_ORIGINS.add(f"https://{lit}:{port}")
    # 局域网候选也放进 Origin（`_lan_candidates` 覆盖面比 `_all_local_ips` 更"干净"，
    # 但两个集合都加一次最省心）
    for h in extra_hosts:
        if h:
            ALLOWED_ORIGINS.add(f"http://{h}:{port}")


init_network_guard("127.0.0.1", 8000)


def _guard_reject(request: Request, why: str) -> "JSONResponse":
    """拒绝跨站/陌生 Host 的请求。

    ★ 页面请求要给**人话 HTML**（2026-09-24 加）：
      第一版对所有请求都回 `{"ok":false,...}` 那样的 JSON。
      用机器名打开时，浏览器里显示的就是一坨 JSON ——
      用户看到的是"前端坏了、没有能互动的组件、也没有大屏"，
      **根本想不到是安全守卫拦的**（我自己就是这么被自己的改动绊了一次）。
      现在：`/api/*` 仍然回 JSON（调用方是程序），
      页面/静态资源请求回一页 HTML，直接告诉用户**该用哪个地址打开**。
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=403, content={"ok": False, "error": {
            "code": "FORBIDDEN", "message": why}})
    hosts = sorted(h for h in ALLOWED_HOSTS
                   if h not in ("0.0.0.0", "::1") and ":" not in h)
    links = "".join(
        f'<li><a href="http://{h}:{_SERVE_PORT}/">http://{h}:{_SERVE_PORT}/</a></li>'
        for h in hosts)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(status_code=403, content=f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>请换一个地址打开 · SingLens</title>
<style>
 body{{font:15px/1.9 "PingFang SC","Microsoft YaHei",system-ui,sans-serif;
      max-width:640px;margin:64px auto;padding:0 24px;color:#222}}
 h1{{font-size:20px}} code{{background:#0000000d;padding:2px 6px;border-radius:4px}}
 ul{{line-height:2.2}} .note{{color:#666;font-size:13px;margin-top:22px}}
</style></head><body>
<h1>这个地址打不开 SingLens</h1>
<p>你用的是 <code>{request.headers.get("host") or "?"}</code>。
   SingLens 只响应**本机 / 本局域网**的地址（这是为了防止别的网站
   通过 DNS rebinding 读到你的练习数据，见 <code>docs/接口契约.md</code> §8.1）。</p>
<p>请用下面任意一个地址打开：</p>
<ul>{links}</ul>
<p class="note">启动服务时控制台会直接打印一条可用链接（采集页还会带上操作者令牌）。<br>
如果这些地址都打不开，检查服务是否还在运行。</p>
</body></html>""")


@app.middleware("http")
async def _local_only_guard(request, call_next):
    """挡掉「跨站请求」与「DNS rebinding」。

    三层判据，任何一层不过就 403：
      ① `Host` 不在白名单 —— 攻击者域名（rebinding）到这里就死了
      ② `Sec-Fetch-Site: cross-site` —— 现代浏览器一定会带这个头
      ③ `Origin` 不在白名单 —— 老浏览器没有 ②，靠它兜底

    ⚠️ 正常客户端不受影响：
       · 命令行/脚本（curl、我们自己的验证脚本）**不发** Origin 与
         Sec-Fetch-Site ⇒ 三层都过；
       · 页面自己发的 fetch 是 same-origin ⇒ `Sec-Fetch-Site: same-origin`，
         Origin 就是 `http://<本机IP>:8000` ⇒ 在白名单里；
       · **机器名 / 所有本机网卡地址**都在白名单里（见 `_local_hostnames`、
         `_all_local_ips`）—— 第一版漏了机器名，把正常打开方式堵死了。
    """
    host = (request.headers.get("host") or "").split(":")[0].strip("[]").lower()
    if host and ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
        return _guard_reject(
            request,
            "这个服务只响应本机 / 本局域网的地址。（用 IP、localhost 或机器名打开）")
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return _guard_reject(request, "拒绝了来自其它网站的请求。")
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and origin.lower() not in ALLOWED_ORIGINS:
        return _guard_reject(request, "拒绝了来自其它网站的请求。")
    return await call_next(request)


# ⚠️ 确认过：这里**故意不再挂 CORSMiddleware**。
#    本项目的页面与 API 同源（都是这个服务发出来的），没有任何跨源需求；
#    而 `allow_origins=["*"]` 会让任意网站的 JS 读走全部数据。见上面 §3.1 的说明。


# ── 上传体积上限：在**进路由之前**就拒掉 ────────────────────
# ⚠️ 为什么必须有中间件这一层（2026-09-23，验证时实测到的）：
#   处理器内部读 `Content-Length` 再返回 413 是**不够**的 ——
#   实测"声明 64 MB、实际只发 1 KB"会让服务干等 60 秒（等 body 读完），
#   最后超时而不是 413。**声明超限就该立刻拒，不去读 body。**
#   处理器里那段流式累加仍然保留：它挡的是"声明很小、实际很大"，
#   而这一层挡的是"声明就超了"。两层判据不同，缺一不可。
#   （顺带：真实用户传了个 200 MB 的手机录像时，这一层会立刻给出可读提示，
#     而不是让浏览器转圈几十秒再报网络错误。）
@app.middleware("http")
async def _limit_upload_size(request, call_next):
    if request.method in ("POST", "PUT") and request.url.path.startswith("/api/"):
        # 模式 E 的歌曲文件允许 120 MB（还要留 multipart 余量）；
        # 其它上传仍走 64 MB。此前统一 64 MB，导致 65–120 MB 的歌在
        # 进入 /api/song/inspect 之前就被通用中间件拒掉。
        limit = (SONG_MAX_SOURCE_BYTES + (1 << 20)
                 if request.url.path == "/api/song/inspect"
                 else MAX_UPLOAD_BYTES)
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > limit:
                    return JSONResponse(
                        status_code=413,
                        content={"ok": False, "error": {
                            "code": "TOO_LARGE",
                            "message": f"上传的内容太大（{int(cl) / 1048576:.0f} MB > "
                                       f"{limit // 1048576} MB）。"
                                       f"请只上传要练的那一段。"}})
            except ValueError:
                pass
    return await call_next(request)

# 实验模块（采集 / 盲评 / 分析）—— 见 web/experiment.py
from web.experiment import router as experiment_router  # noqa: E402
from web import templates  # noqa: E402  （模式 B 两遍制的模板存储）
# ★ G3（2026-10-01）：练习记录 + 演唱者名册。**提到模块级 import** ——
#   以前每个用到它的函数里都写一句 `from web import practice_store`，
#   那是"怕循环 import"的历史遗留；实际 practice_store 只依赖标准库。
#   G3 之后读接口全部走它，散在各处的局部 import 只会让人以为它可以缺席。
from web import practice_store  # noqa: E402
from web import auth as auth_mod  # noqa: E402
app.include_router(experiment_router)


# ══════════════════════════════════════════════════════════════
# 账号登录（2026-10-28）
#
# 产品接口按 session 认人；研究采集/盲评仍走操作者令牌，不在这里统一。
# 这层只做三件事：
#   ① 产品 API 没登录 → 401；
#   ② 普通 singer 的 singer_id 一律以 session 为准，路径/query 不能越权；
#   ③ 把当前用户放进 request.state，给 _json() 和名册接口使用。
# ══════════════════════════════════════════════════════════════
_AUTH_PRODUCT_PREFIXES = (
    "/api/session", "/api/singer", "/api/template", "/api/upload",
    "/api/song", "/api/ai-sing", "/api/config", "/api/privacy",
    "/api/references", "/api/bootstrap",
)
_AUTH_PUBLIC_PREFIXES = (
    "/api/auth", "/api/health", "/api/latency",
    "/api/experiment", "/api/rate",
    "/login", "/css/", "/js/", "/fonts/", "/img/",
    "/collect", "/rate", "/selftest", "/apitest", "/favicon",
)


def _auth_is_product_path(path: str) -> bool:
    return path.startswith("/api/") and any(
        path == p or path.startswith(p + "/") for p in _AUTH_PRODUCT_PREFIXES
    )


def _auth_singer_in_path(path: str) -> str:
    m = re.match(r"^/api/singer/([^/]+)(?:/|$)", path)
    if not m:
        return ""
    from urllib.parse import unquote
    return unquote(m.group(1))


def _auth_deny(message: str = "请先登录。", status_code: int = 401):
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {
            "code": "UNAUTHORIZED" if status_code == 401 else "FORBIDDEN",
            "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _auth_same_origin(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin:
        return True
    return origin.lower() in {x.lower() for x in ALLOWED_ORIGINS}


@app.middleware("http")
async def _account_guard(request: Request, call_next):
    """产品 API 的 session 守卫。

    `SINGLENS_AUTH=0` 时完全旁路（旧自动化/离线演示用）。
    登录页与静态资源、研究页面不受影响。
    """
    if not auth_mod.auth_enabled():
        return await call_next(request)
    path = request.url.path
    if not _auth_is_product_path(path):
        return await call_next(request)
    user = auth_mod.user_from_request(request)
    if not user:
        return _auth_deny("请先登录后再访问练习数据。")
    request.state.user = user
    if user.get("role") != "admin":
        sid = user.get("singer_id") or ""
        # 路径里直接写别人的 singer_id 是最明确的越权。
        path_sid = _auth_singer_in_path(path)
        if path_sid and path_sid != sid:
            return _auth_deny("不能访问其他演唱者的数据。", 403)
        # 普通账号不能改全局配置、清空全库、删演唱者。
        if ((request.method == "PUT" and path == "/api/config")
                or (request.method == "POST" and path.startswith("/api/privacy/"))
                or (request.method == "DELETE" and path.startswith("/api/singer/"))):
            return _auth_deny("这个操作需要管理员账号。", 403)
        # query 里的 singer_id 一律覆盖为当前用户；不能让前端自报身份。
        q = parse_qsl(request.scope.get("query_string", b"").decode("utf-8"),
                      keep_blank_values=True)
        q = [(k, sid if k == "singer_id" else v) for k, v in q]
        if not any(k == "singer_id" for k, _ in q):
            q.append(("singer_id", sid))
        request.scope["query_string"] = urlencode(q, doseq=True).encode("utf-8")
    return await call_next(request)


# ══════════════════════════════════════════════════════════════
# 统一响应包（契约 §3.2）
# ══════════════════════════════════════════════════════════════
def ok(data=None):
    return {"ok": True, "data": data}


def fail(code: str, message: str, status: int = 400):
    # 白名单外直接报 500 太粗暴（曾经 CONFLICT 不在表里，assert 一抛就是 500，
    # 掩盖了真正的业务错误）。改成宽松处理：不认识的码保留原样，仍旧按 status 返回。
    if code not in ALL_ERROR_CODES:
        status = status if status >= 400 else 400
    return JSONResponse(status_code=status,
                        content={"ok": False, "error": {"code": code, "message": message}})


def r1(x):
    """分数统一保留 1 位小数（契约 §3.2）。None 保持 None。"""
    return None if x is None else round(float(x), 1)


def _llm_key_present() -> bool:
    """有没有配 LLM 的 API key。

    ⚠️ 只返回**布尔**，绝不回显 key 本身 —— 它会进 `/api/config`，
      而那个接口前端随时在拉。
    """
    try:
        from analysis.coach import available as _av
        return bool(_av())
    except Exception:  # noqa: BLE001
        return False


# ══════════════════════════════════════════════════════════════
# 会话
# ══════════════════════════════════════════════════════════════
@dataclass
class RealtimeState:
    """实时管线的滚动状态。按契约 §2.3，滚动窗口统一 5 秒。

    ⚠️⚠️ `audio` 曾经是**只 append、从不裁剪、不限长**的列表
    （2026-09-23 审计查出、我复核确认）：

        实测：一个会话 0.77 秒灌进 40 MiB int16，服务照单全收；
              两个会话 → 进程 RSS 147 MB → **506 MB**，数分钟后仍 518 MB 不降。
              20 个并发 WS 全部被接受，空闲连接无限期占着（无空闲超时）。

    而 `SESSIONS` 里**没有任何** `pop/clear/TTL`（全文件 grep 零命中）——
    每个被占用的会话、每条空闲连接都不会释放，长时间运行必然 OOM。

    修法（三处，都在下面）：
      ① 实时只留**滚动窗口**：`audio` 超出 `RT_KEEP_SEC` 就从头丢，
         同时用 `audio_dropped` 记账 —— 被丢掉的部分**不能假装没发生**，
         `finish()` 要据此如实告诉用户"只分析了前 N 秒"。
      ② `audio` 总长上限（`MAX_AUDIO_SEC`）—— 与上传那条同一个道理：
         PYIN 的成本与时长线性相关，而它逐帧持 GIL。
      ③ `SESSIONS` 加容量上限 + 启动清理（见 `_reap_sessions`），
         WS 加**空闲超时**（见 stream 循环）。
    """
    audio: list = field(default_factory=list)      # 已收到的音频块（滚动窗口内）
    audio_dropped: int = 0       # 被滚动窗口丢掉的样本数（**要如实上报**）
    n_samples: int = 0
    tail: object = None          # 最近 ~190ms，仅供实时 YIN
    f0_hist: list = field(default_factory=list)    # [(t_ms, f0_hz, rms)]
    onset_hist: list = field(default_factory=list)  # 起音偏差（ms）
    last_pitch_sent: float = 0.0
    last_metrics_sent: float = 0.0
    proc_ms: list = field(default_factory=list)    # 每帧服务端处理耗时
    started: float = field(default_factory=time.perf_counter)
    last_seen: float = field(default_factory=time.perf_counter)   # 回收判据
    ref_notes: list = field(default_factory=list)  # [(start_ms, end_ms, midi)]


@dataclass
class Session:
    uid: str
    singer_id: str
    mode: str                       # "A" 教材曲目 / "B" 跟唱模板 / "C" 上传
    reference_id: Optional[str]
    status: str = "RECORDING"       # RECORDING → ANALYZING → SCORED / FAILED
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    audio: Optional[np.ndarray] = None
    sr: int = SR
    report: Optional[dict] = None
    error: Optional[dict] = None
    progress: dict = field(default_factory=dict)   # 四步进度（契约 §5.3）
    rt: RealtimeState = field(default_factory=RealtimeState)
    template_quality: Optional[dict] = None
    retry_of_uid: Optional[str] = None
    # ★ 模式 B 两遍制：本会话要比对的「跟唱模板」（第一遍存的）。
    #   有它 → 真实两遍对比；没它 → 只能自比，必须降级（见 run_precise 的说明）。
    template_id: Optional[str] = None
    # ★ 给人看的参考曲名（"小星星·第一句" / "我的练习曲"）。
    #   为什么不能只靠 reference_id 显示：模式 B 的 reference_id 可能是
    #   `tpl_c341f5b99908` 这种号，用户看不懂，而且那一行会撑爆被 CSS 裁掉
    #   （截图才发现：报告页的曲目行只显示到 "2026-09-23T10:26:44 · sess_158b3"）。
    reference_title: Optional[str] = None
    # ★ 2026-10-18：这次参考移调了几个半音（0 = 原调）。只影响参考音高，
    #   不改变用户音频；报告/对比/引导音轨都读这个值。
    transpose: int = 0
    # ★ 实时管线的滚动窗口丢掉了开头的音频时，记下"从第几毫秒开始分析的"。
    #   None = 没丢过。见 finish() 与 RealtimeState.audio 的说明 ——
    #   没评的部分必须说出来，不能静默。
    truncated_at_ms: Optional[int] = None
    # ★ G13：这一遍的录音有没有落盘。`{"saved": bool, "bytes": int, "reason": str}`
    #   None = 还没走到那一步。报告页据此决定要不要显示"听这一段"按钮 ——
    #   **按下去没反应的按钮比没有按钮更坏**（用户会以为是自己浏览器的问题）。
    audio_info: Optional[dict] = None
    # ★ C14（2026-10-15）：这个**参考**里相邻两个音之间最大的空隙是多少毫秒
    #   （0 = 它一个空隙都没有，即**没给人留换气的时间**）。
    #
    #   为什么报告里要带上它：换气会在每句末尾插进 ~0.5–1 秒静音，而
    #   `tempo_ratio` 量的是**总时长** ⇒ 参考里没留换气时间时，
    #   "换气"会被算成"你慢了 15–30%"。有了这个数，前端才能说清
    #   "这里面有多少可能是换气，而不全是没跟拍子"。
    #
    #   ⚠️ **不是**读 `ref.phrase_gap_ms` —— 我第一版就那么写，而
    #      `ReferenceMelody` 根本没有这个字段（只有 reference_id / title /
    #      notes / mode / source）。换气位是**烘进音符时间轴**的：
    #      实测 `REF_TWINKLE_6B` 每 7 个音之后有 **700ms** 空隙，`REF_TWINKLE_6` 没有。
    #      ⇒ 所以这个值是**算**出来的（见 `run_precise`）。
    reference_max_gap_ms: int = 0


SESSIONS: dict[str, Session] = {}
SINGERS: dict[str, dict] = {}          # 简化：内存版档案，后续换 SQLite
# 设置项。**结构以 UI 契约为准**（契约 §7.5）—— 四组对应设置页的四个板块。
# （原来是一个扁平 dict，`selftest.html?src=api` 因此报"缺 range/recording/feedback/privacy"）
CONFIG: dict = {
    "range": {
        "lo_midi": 36,                    # C2
        "hi_midi": 84,                    # C6
        "preset": "自动收窄",              # 实际用 estimate_range() 按录音自适应
        "pitch_tolerance_cents": PITCH_TOLERANCE_CENTS,
    },
    "recording": {
        "device": "default",
        # ★ `device_id`（2026-10-01）：浏览器的输入设备 id，设置页那两个
        #   麦克风按钮以前是**死的**（点了没反应、openMic 从来没收到过设备）。
        #   为什么值得接通：产品要求"戴耳机跟着伴奏唱"，而**选错输入设备会
        #   直接改分数** —— 用内置麦录，外放的伴奏会一起进麦克风，
        #   音高检测会去追伴奏的贝斯线（设计规格 §5.4 实测八度错误率 6.91%）。
        #   空串 = 系统默认设备（`openMic` 里空串**不能**传 `exact`）。
        #   `device` 给人看，`device_id` 给 getUserMedia 用，两个都留着。
        "device_id": "",
        "sample_rate": SR,
        # ★ 2026-09-24：这个开关**接通了**（以前全仓没有一行代码读它）。
        #   它现在真的决定精算管线跑不跑 HPSS 谐波分量预处理。
        #   默认 **False** —— 依据是实测，不是图省事：
        #     本项目现有素材全是**清唱**，而 HPSS 在清唱素材上一致率只有
        #     89.5%~91.9%（即会改动约 10% 的帧），40 秒录音另需约 4.9 s。
        #     默认开着等于**默默改掉论文的基线数字**，而现有数据都是清唱的。
        #   什么时候该开：**跟着伴奏唱**的录音。设计规格 §5.4 实测那里它是
        #     有效对策（带伴奏八度错误率 6.89% → 谐波分量后六项指标全部改善）。
        #   切换入口：设置页「伴奏处理」开关 → PUT /api/config → 这里。
        "accompaniment_mode": False,
        "rt_window_ms": RT_WINDOW_MS,
    },
    "feedback": {
        # ★ 2026-10-02：**默认跟着"有没有 API key"走**。
        #   以前恒为 False ⇒ 即使配了 key，总评也永远走规则版，
        #   而用户看不出"为什么没走 LLM"。现在：
        #     · 配了 key ⇒ 默认开（LLM 名副其实）
        #     · 没配 key ⇒ 默认关（不会每次都白试一次网络请求）
        #   ⚠️ 用户在设置页手动改过之后，以用户的选择为准（存在库里）。
        "ai_comment": _llm_key_present(),
        "show_jargon": False,             # 是否默认展开专业术语
        "colorize_deviation": True,       # 音准曲线是否按偏差着色
        "guide_volume": 0.8,              # 引导音轨音量（0–1），不影响麦克风/评分
        # ★ 2026-10-19：评分宽容度（严格/标准/宽松），见 analysis/practice_plan.py
        #   的 STYLE_PROFILES。只影响**练习计划/微练习目标**，不改六指标原始分。
        "style_tolerance": "standard",
        # ★ 2026-10-19：AI 人声（女声绮萱 / 男声 Alemon / 关闭）。
        #   只影响「AI 唱」按钮，不影响评分。
        "ai_voice": "female",
        "weight_version": WEIGHT_VERSION,
    },
    "privacy": {
        "record_dir": "./data/recordings/sessions/",
        # ⚠️ 只描述**录音文件**。唱后解释/指导会走 LLM，报告文本/歌词/画像
        #    文本会外发；不要把这个字段读成“整个系统不上传”。
        "audio_local_only": True,
        "text_to_llm": True,              # 报告文本/歌词/画像文本会发给 LLM
        "used_bytes": 0,
        "sample_count": 0,
    },
    # ★ 2026-10-02：顶层显式标注 LLM 是否可用（**只暴露布尔，不暴露 key**）。
    #   为什么单独放一个键而不是靠 `feedback.ai_comment` 推断：
    #     后者是**用户的开关**（可手动关掉），前者是**能力**（有没有配 key）。
    #     两件事必须分开报 —— "开关关着"和"没配 key"是完全不同的状态，
    #     混在一起会让人以为"配了 key 也没用"。
    "llm_available": _llm_key_present(),
}


def load_practice_history():
    """启动时把 SQLite 里的练习记录读回内存。

    ★ 为什么必须有这一步（2026-09-23 产品就绪度审计查出的硬缺口）：
      在这之前，练习记录**只存在 `SINGERS` 这个进程内字典里** ——
      服务一重启，成长档案/进步曲线/历史记录全部消失。
      而"每次练完都会存下来"是成长档案写在界面上的承诺。
      真实用户不会一次把练习做完，关掉再打开太正常了。

    ⚠️ 为什么一直没被发现：我每次验证前端都先跑 `seed_frontend_data.py`
       造数据 —— 那个脚本把数据"补"上了，"重启即失忆"被完美盖住。
       这就是"有人补台"与"独立可用"的差别。

    内存仍然是**读缓存**（报告里还有 segments/evidence 这种大对象，
    每次翻历史都解 JSON 太慢），写的时候**写穿**到 SQLite（见
    `_record_for_singer`）。所以内存是可丢的，磁盘才是真相。
    """
    try:
        from web import practice_store
        # ★ G3（§58.1③）：先把库里出现过的 singer_id 补进名册，再读回记录。
        #   顺序不能反 —— 名册是 `/api/singer/{sid}/*` 的存在性判据（现在查不到就 404），
        #   登记晚一步的话，启动瞬间打开页面的用户会看到"没有这位演唱者"。
        mig = practice_store.migrate_app_singers()
        if mig["registered"]:
            shown = "、".join(mig["registered"][:6])
            more = f" 等 {len(mig['registered'])} 位" if len(mig["registered"]) > 6 else ""
            print(f"[SingLens] 自动登记演唱者：{shown}{more}"
                  f"（库里原有记录里的身份不能变成孤儿）")
        n_total = 0
        for sid in practice_store.all_singers():
            ss = practice_store.sessions_of(sid)
            if not ss:
                continue
            SINGERS.setdefault(sid, {"id": sid, "sessions": []})
            SINGERS[sid]["sessions"] = ss
            n_total += len(ss)
        if n_total:
            print(f"[SingLens] 从库里读回 {n_total} 条练习记录"
                  f"（{len(practice_store.all_singers())} 位演唱者）")
        else:
            print("[SingLens] 库里还没有练习记录（新装或刚清空过）")
    except Exception as e:  # noqa: BLE001
        # 读不回来不该拦着服务启动 —— 但**必须显式报出来**。
        # 悄悄降级成"空档案"是最坏的结果：用户会以为自己的练习全丢了。
        print(f"[SingLens] ⚠️ 练习记录读取失败：{e}")
        print("           服务照常启动，但成长档案/历史记录可能不完整。")


# ══════════════════════════════════════════════════════════════
# 会话录音的持久化（G13「逐段复听 / 只练这一段」）
# ══════════════════════════════════════════════════════════════
# ⚠️ 为什么必须持久化：会话录音原来**只活在内存里**（`Session.rt.audio` /
#    `Session.audio`），精算一结束就随会话一起被回收。后果很具体：
#      · 报告页能告诉你"第 3 句偏低"，但你**没法把那一段放出来听**
#      · 更没法只对着那一段重练 —— 只能整首再唱一遍
#    这就是待办里 G13 说的「练习闭环里唯一缺的能力」。
#
# ⚠️ 与隐私承诺的关系（**必须一起想清楚**，见 G13 的原话）：
#    设置页写着"录音只存在本机、可一键清空"。持久化之后，
#    `POST /api/privacy/purge` **也必须删这些文件**，而且如实回报删了几个。
#    ⇒ 清空列表里已经加上 `data/recordings/sessions`（见 purge_endpoint）。
SESSION_AUDIO_DIR = os.path.join(ROOT, "data", "recordings", "sessions")


def session_audio_path(uid: str) -> str:
    """这一遍录音落盘的位置。

    ⚠️ `uid` 要过一遍白名单：它来自 URL，直接拼进路径就是**路径穿越**
       （`../../etc/passwd`）。会话 uid 由服务端生成（`sess_` + hex），
       所以"不匹配就拒绝"既安全又不误伤。
    """
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "_-")
    if not safe or safe != uid:
        return ""
    return os.path.join(SESSION_AUDIO_DIR, f"{safe}.wav")


def persist_session_audio(uid: str, y, sr: int) -> dict:
    """把这一遍的录音写成 wav。返回 `{"saved": bool, "bytes": int, "reason": str}`。

    ⚠️ **写失败绝不能影响评分**：录音存不下来是"少了复听功能"，
       而不是"这一遍白唱了"。所以这里吞异常，但把原因**说出来**
       （报告页据此决定要不要显示"听这一段"按钮）——
       这与本项目「失败不许静默」同一条：没做到的事必须让人知道。
    """
    p = session_audio_path(uid)
    if not p:
        return {"saved": False, "bytes": 0, "reason": "会话号不合法，未落盘"}
    if y is None or len(y) == 0:
        return {"saved": False, "bytes": 0, "reason": "没有音频可存"}
    try:
        os.makedirs(SESSION_AUDIO_DIR, exist_ok=True)
        import soundfile as sf
        # PCM_16：22.05kHz 单声道 1 分钟约 2.6MB，用 float32 会翻倍。
        sf.write(p, np.clip(np.asarray(y, dtype="float32"), -1, 1), sr,
                 subtype="PCM_16")
        return {"saved": True, "bytes": os.path.getsize(p), "reason": ""}
    except Exception as e:  # noqa: BLE001
        return {"saved": False, "bytes": 0, "reason": f"{type(e).__name__}: {e}"}


# 一段最多能取多长。⚠️ 必须有上限：`to_ms` 来自查询串，
# 不设限的话一个请求就能让服务端把整场录音读出来编码成 wav。
AUDIO_SEGMENT_MAX_MS = 60_000


def _read_session_audio(uid: str):
    """读回这一遍的录音。返回 `(y, sr)` 或 `(None, None)`。

    优先读**内存里还活着的** `sess.audio`（省一次磁盘 IO，也更准），
    内存里没有才读落盘的文件 —— 报告页常常是在会话已被回收之后才打开的。
    """
    sess = SESSIONS.get(uid)
    if sess is not None and sess.audio is not None and len(sess.audio):
        return np.asarray(sess.audio, dtype="float32"), int(sess.sr or SR)
    p = session_audio_path(uid)
    if not p or not os.path.exists(p):
        return None, None
    try:
        import soundfile as sf
        y, sr = sf.read(p, dtype="float32", always_2d=False)
        return np.asarray(y, dtype="float32"), int(sr)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 读会话录音失败（{uid}）：{e}")
        return None, None


# ── 保留预算（2026-10-15 补，审计发现）──────────────────────────
# ⚠️ **为什么需要**：G13 之后每次练习都会留一份 `sessions/{uid}.wav`
#   （22.05kHz PCM_16 单声道 ≈ 44 KB/秒 ⇒ 30 秒一遍 ~1.3MB、3 分钟 ~8MB），
#   而它**只增不减**：没有配额、没有保留策略、也**没有"删掉某一次练习"**的端点。
#   ⇒ 对"一个真实用户长期用"这是实质缺陷：磁盘单向增长，
#     而他能做的只有「清空全部」。
#
# ★ 这个取舍是**有依据**的：**录音是"便利"，不是"记录"本身。**
#   真相在 `practice_session` 里（报告、分数、日期），复听那一段只是增强。
#   所以旧录音可以按预算淘汰 —— 但**必须让人知道**：
#   淘汰之后报告页的「听这一段」会**消失**（不是变成死按钮），
#   并且 `meta.audio_reason` 会说明为什么。
SESSION_AUDIO_BUDGET_BYTES = 1024 ** 3        # 1 GiB，约 700–800 次 30 秒练习


def session_audio_state(uid: str) -> tuple:
    """这一遍的录音**现在**还在不在。返回 `(available, reason)`。

    ⚠️ **报告页必须用它，而不是信任报告里缓存的 `audio_available`。**
      报告是落库的（`practice_session.report_json`），里面的 meta 是
      **当时**的快照；而录音文件可能后来被保留策略淘汰、被「清空」删掉、
      或压根没落盘成功。
      ⇒ 缓存说"有"、文件其实没了 ⇒ 报告页会显示「听这一段」，
        点下去 404 —— 那正是本项目写过两次的「按下去没反应的按钮比没有按钮更坏」。
    """
    sess = SESSIONS.get(uid)
    if sess is not None and sess.audio is not None and len(sess.audio):
        return True, ""
    p = session_audio_path(uid)
    if p and os.path.exists(p):
        return True, ""
    return False, ("这一遍的录音已不在本机（可能被自动清理了 —— "
                   "为了不让磁盘无限增长，较早的录音会按预算淘汰；"
                   "报告本身不受影响）。")


def enforce_session_audio_budget(budget: int = SESSION_AUDIO_BUDGET_BYTES) -> dict:
    """录音目录超预算时，**从最旧的开始淘汰**。返回 `{deleted, bytes, left_bytes}`。

    ⚠️ 只在**写新文件之后**调用（那时候才可能超）—— 不搞定时任务，
       因为"多一个后台线程"换不来什么，而它多一处可能静默死掉的地方。
    ⚠️ 淘汰的是**文件**，不动数据库 —— 报告照样打得开，只是听不了那一段。
    """
    out = {"deleted": 0, "bytes": 0, "left_bytes": 0}
    if not os.path.isdir(SESSION_AUDIO_DIR):
        return out
    files = []
    total = 0
    try:
        for name in os.listdir(SESSION_AUDIO_DIR):
            if not name.endswith(".wav"):
                continue
            fp = os.path.join(SESSION_AUDIO_DIR, name)
            try:
                stt = os.stat(fp)
            except OSError:
                continue
            files.append((stt.st_mtime, stt.st_size, fp))
            total += stt.st_size
    except OSError:
        return out
    if total <= budget:
        out["left_bytes"] = total
        return out
    files.sort()                                  # 最旧的在前
    for _mt, sz, fp in files:
        if total <= budget:
            break
        try:
            os.remove(fp)
            total -= sz
            out["deleted"] += 1
            out["bytes"] += sz
        except OSError:
            pass
    out["left_bytes"] = total
    if out["deleted"]:
        print(f"[SingLens] 录音目录超预算（{budget // 1048576} MB），"
              f"已淘汰最旧的 {out['deleted']} 个（{out['bytes'] / 1048576:.1f} MB），"
              f"现 {out['left_bytes'] / 1048576:.1f} MB")
    return out


def _refresh_privacy():
    """把**练习会话录音**的实际占用刷进 config（设置页要显示）。

    ⚠️ 只统计 `data/recordings/sessions`：`data/recordings/exp`、
    `constructed` 和目录顶层的素材是研究数据，不属于“用户一键清空”的范围。
    以前统计整个 `data/recordings`，设置页会把研究素材也算成用户的录音。
    """
    d = SESSION_AUDIO_DIR
    if not os.path.isdir(d):
        CONFIG["privacy"]["used_bytes"] = 0
        CONFIG["privacy"]["sample_count"] = 0
        return
    n = tot = 0
    for dp, _, fns in os.walk(d):
        for f in fns:
            if f.lower().endswith((".wav", ".m4a", ".mp3")):
                n += 1
                tot += os.path.getsize(os.path.join(dp, f))
    CONFIG["privacy"]["used_bytes"] = tot
    CONFIG["privacy"]["sample_count"] = n


def _reap_sessions():
    """回收内存里的会话（容量上限 + 空闲超时）。

    ★ 为什么必须有：`SESSIONS` 原来**只增不减**（全文件 grep `SESSIONS.pop|clear`
    零命中）。审计实测：两个流式会话把 RSS 从 147 MB 推到 506 MB 且数分钟不降，
    20 个并发 WS 全部被接受 —— 长时间运行必然 OOM。

    两条判据，都很保守：
      · **容量**：超过 MAX_SESSIONS 就丢最久没活动的
      · **空闲**：SESSION_TTL_SEC 没动静的丢掉
    ⚠️ 判据用 `rt.last_seen`（**最后一次收到音频/请求的时间**），不是 `created_at`。
       用创建时间会把"正在唱"的会话误杀 —— 那是比不回收更糟的错。

    ⚠️ 只丢内存里的会话，**不删库**。`practice_session` 是真相（§54），
       回收内存不该影响用户的练习记录。
    """
    now = time.perf_counter()
    # ① 空闲超时
    idle = [uid for uid, s in SESSIONS.items()
            if now - getattr(s.rt, "last_seen", now) > SESSION_TTL_SEC]
    for uid in idle:
        SESSIONS.pop(uid, None)
    # ② 容量上限：丢最久没活动的
    over = len(SESSIONS) - MAX_SESSIONS
    if over > 0:
        by_age = sorted(SESSIONS.items(),
                        key=lambda kv: getattr(kv[1].rt, "last_seen", 0.0))
        for uid, _ in by_age[:over]:
            SESSIONS.pop(uid, None)
    if idle or over > 0:
        print(f"[SingLens] 回收会话：空闲 {len(idle)} 个、超容量 {max(0, over)} 个，"
              f"剩余 {len(SESSIONS)} 个")


def _touch(sess: Session):
    """记一次"这个会话还活着"，供 `_reap_sessions` 判断。"""
    try:
        sess.rt.last_seen = time.perf_counter()
    except Exception:  # noqa: BLE001
        pass


def get_session(uid: str) -> Session:
    s = SESSIONS.get(uid)
    if s is None:
        raise HTTPException(status_code=404,
                            detail={"code": "NOT_FOUND", "message": f"会话 {uid} 不存在"})
    return s


def get_session_or_db(uid: str) -> Session:
    """内存里有就用内存；没有就从库里拼一个**只读** Session。

    为什么要有这个（2026-10-18 审查）：
      · `/status` 与 `/compare` 原来只查内存；
      · 服务一重启，`SESSIONS` 是空的，而 `practice_session` 里 19 条记录都在；
      · 结果：`/report` 200（有读库回退）、`/history` 200，
        但 `/compare` 404「会话不存在」—— 同一份数据两个答案。

    这里只拼出 `singer_id / mode / reference_id / retry_of_uid /
    template_id / report` 这几项，够只读接口用；**不写回内存**，
    避免让一个"读"动作改变 `SESSIONS` 的容量与回收判断。
    """
    s = SESSIONS.get(uid)
    if s is not None:
        return s
    row = practice_store.get_session(uid)
    if row is None:
        raise HTTPException(status_code=404,
                            detail={"code": "NOT_FOUND", "message": f"会话 {uid} 不存在"})
    s = Session(
        uid=uid,
        singer_id=row.get("singer_id") or "",
        mode=row.get("mode") or "A",
        reference_id=row.get("reference_id"),
        status="SCORED",
        retry_of_uid=row.get("retry_of_uid"),
        template_id=row.get("template_id"),
    )
    s.reference_title = row.get("reference_title")
    s.report = practice_store.load_report(uid)
    return s


# ══════════════════════════════════════════════════════════════
# 实时指标（契约 §2.3）
# ══════════════════════════════════════════════════════════════
def _recent(state: RealtimeState, t_ms: float, win_ms: int = RT_WINDOW_MS):
    lo = t_ms - win_ms
    return [x for x in state.f0_hist if x[0] >= lo]


def compute_realtime_metrics(sess: Session, t_ms: float) -> dict:
    """算四个滚动分。**由后端计算**（契约 §5.2：避免前后端两套实现）。"""
    st = sess.rt
    recent = _recent(st, t_ms)
    out = {"t_ms": int(t_ms), "pitch": None, "rhythm": None,
           "dynamic": None, "vibrato": None,
           "current_note": None, "advice": None}

    if not recent:
        out["advice"] = "还没听到你的声音，靠近麦克风一点。"
        return out

    f0s = np.array([f for _, f, _ in recent], dtype=float)
    rmss = np.array([r for _, _, r in recent], dtype=float)
    valid = np.isfinite(f0s) & (f0s > 0)

    if valid.sum() < 5:
        out["advice"] = "系统还听不清你的音高，试试把手机拿远一点、别对着出风口。"
        return out

    # ── 音准：与参考音高比 ──
    #   ★ 判据同样是"有没有参考音高"，不是"是不是模式 A"。
    #     模式 B 带模板时 `st.ref_notes` 是填好的 —— 那时**必须**给音准分，
    #     否则用户跟着自己的模板唱，实时面板上音准永远是空的。
    #     （模式 B 不带模板 = 自比，没有参考，那时才只报音高不报分。）
    cents_dev = None
    current = None
    if st.ref_notes:
        rn = _ref_note_at(st.ref_notes, t_ms)
        if rn is not None:
            ref_hz = 440.0 * 2 ** ((rn[2] - 69) / 12)
            dev = 1200.0 * np.log2(f0s[valid] / ref_hz)
            dev = dev[np.abs(dev) < 600]        # 去掉明显是八度错的帧
            if dev.size:
                cents_dev = float(np.median(dev))
                out["pitch"] = r1(pitch_score(np.array([cents_dev]),
                                              tolerance=PITCH_TOLERANCE_CENTS,
                                              full_penalty_cents=50.0)["score"])
                verdict = "OK" if abs(cents_dev) <= PITCH_TOLERANCE_CENTS else (
                    "SHARP" if cents_dev > 0 else "FLAT")
                current = {"pitch_midi": round(float(np.median(hz_to_midi(f0s[valid]))), 1),
                           "cents_dev": round(cents_dev, 1), "verdict": verdict}
    else:
        # 没有参考（模式 B 的第一遍 / 自比 / 上传）→ 只报音高，不报音准分
        current = {"pitch_midi": round(float(np.median(hz_to_midi(f0s[valid]))), 1),
                   "cents_dev": None, "verdict": "OK"}
    out["current_note"] = current

    # ── 节奏：近窗内的起音偏差 ──
    if st.onset_hist:
        od = np.array([x for t, x in st.onset_hist if t >= t_ms - RT_WINDOW_MS])
        if od.size >= 2:
            acc = abs(float(np.mean(od)))
            stab = float(np.std(od))
            a = max(0.0, min(1.0, (120.0 - acc) / (120.0 - 15.0)))
            s = max(0.0, min(1.0, (150.0 - stab) / (150.0 - 20.0)))
            out["rhythm"] = r1(100.0 * (a + s) / 2)

    # ── 力度起伏：近窗 RMS 的相对变化 ──
    if rmss.size >= 8 and rmss.max() > 0:
        p95, p5 = np.percentile(rmss, 95), np.percentile(rmss, 5)
        if p5 > 0:
            dyn_db = 20 * np.log10(p95 / p5)
            out["dynamic"] = r1(100.0 * min(1.0, max(0.0, dyn_db / 16.0)))

    # ── 颤音：需要足够长的稳定段，早期必然为 null ──
    #   契约明确要求此时返回 null（"尚无可测长音时"），不是 0。
    out["vibrato"] = None

    # ── 实时建议（模板，200ms 预算内）──
    if out["dynamic"] is not None and out["dynamic"] < 25:
        out["advice"] = "你这一句从头到尾用一样大的声音在唱。试试句尾收一点。"
    elif out["pitch"] is not None and out["pitch"] < 60:
        out["advice"] = "这几句整体偏低，把气吸深一点再开口。"
    elif out["rhythm"] is not None and out["rhythm"] < 60:
        out["advice"] = "拍子有点赶，跟着心里的拍子稳一下。"
    elif t_ms < RT_WINDOW_MS:
        out["advice"] = "刚开始，再多唱几句我才有数据给你建议。"
    return out


def _ref_note_at(ref_notes, t_ms):
    """按时间找当前该唱哪个参考音（简单前向搜索，允许 ±400ms 容差）。"""
    for st, en, midi in ref_notes:
        if st - 400 <= t_ms <= en + 400:
            return (st, en, midi)
    return None


# ══════════════════════════════════════════════════════════════
# 精算管线
# ══════════════════════════════════════════════════════════════
def run_precise(sess: Session):
    """PYIN → 切分 → 对齐 → 六指标 → 评分。在后台任务里跑。"""
    try:
        y = sess.audio
        if y is None or len(y) < SR * MIN_USABLE_MS / 1000:
            sess.status = "FAILED"
            sess.error = {"code": "TOO_SHORT",
                          "message": f"录音太短（不足 {MIN_USABLE_MS/1000:.0f} 秒），再唱长一点。"}
            return

        # ★ G13：**在这里落盘**，而不是在 finish() 里 ——
        #   因为 `run_precise` 是**所有模式汇聚的唯一一处**
        #   （实时 A/B 走 finish→这里，上传走 upload→这里）。
        #   写在别处就得写两遍，而"同一件事两处各写一遍"必然有一天不同步
        #   （这个教训刚在 §105 的 ffmpeg 上付过一次）。
        #   ⚠️ 失败不影响评分，只影响"能不能复听"。
        if sess.audio_info is None:
            sess.audio_info = persist_session_audio(sess.uid, y, sess.sr)
            if not sess.audio_info.get("saved"):
                print(f"[SingLens] ⚠️ 会话录音未落盘（{sess.uid}）："
                      f"{sess.audio_info.get('reason')} —— 报告页将不提供逐段复听")
            else:
                # ★ 保留预算：写完之后才可能超。淘汰最旧的，**不动数据库** ——
                #   旧报告照样打得开，只是「听这一段」会消失（见 session_audio_state）。
                enforce_session_audio_budget()

        sess.progress = {"step": 1, "total": 4, "label": "正在提取音高"}
        t0 = time.perf_counter()
        # ★ 自动收窄音域：实测 0.486× → 0.142× 实时，音高结果 100% 一致
        # ★ 伴奏处理开关（2026-09-24 接通）：跟着伴奏唱时先取 HPSS 谐波分量。
        #   以前这个开关**没有任何代码读它** —— 界面上能切、能存、刷新还在，
        #   但对分析毫无影响。现在它真的决定这条管线跑不跑 `harmonic_component`。
        hpss = bool((CONFIG.get("recording") or {}).get("accompaniment_mode"))
        f0, _, _, range_info = extract_f0_pyin_auto(y, sess.sr, hpss=hpss)
        t_pitch = time.perf_counter() - t0

        sess.progress = {"step": 2, "total": 4, "label": "正在切分音符"}
        t0 = time.perf_counter()
        notes, f0b, times = segment_notes(y, sess.sr, f0=f0)
        t_seg = time.perf_counter() - t0

        if not notes:
            sess.status = "FAILED"
            sess.error = {"code": "NO_VOICE",
                          "message": "没检测到人声。确认麦克风没被静音，再试一次。"}
            return
        st = note_stats(notes)
        if not st["feasible"] and st["count"] < 3:
            sess.status = "FAILED"
            sess.error = {"code": "NO_VOICE", "message": st["reason"]}
            return

        sess.progress = {"step": 3, "total": 4, "label": "正在与参考对齐"}
        t0 = time.perf_counter()
        window_extra = {}
        self_reference = False
        phrase_lyrics = []
        if sess.mode == "A" and sess.reference_id:
            ref = _transpose_ref(load_builtin(sess.reference_id), sess.transpose)
            # ★ 参考只有"一句"，而用户可能唱了多句/多遍 ——
            #   只评参考真正覆盖的那一段，见 _align_window 的说明。
            aligned, align_info, notes_used, window_extra = _align_window(
                notes, ref, f0b, sess.sr)
        elif sess.mode == "B" and sess.template_id:
            # ★★ 模式 B 两遍制的正路：与【第一遍存的模板】对齐。
            #    这才是"先唱一遍当样板，第二遍跟它比"的真实实现。
            tpl = templates.load(sess.template_id)
            if tpl is None:
                sess.status = "FAILED"
                sess.error = {"code": "REF_MISSING",
                              "message": f"跟唱模板 {sess.template_id} 不见了，请重录模板。"}
                return
            ref = _transpose_ref(templates.to_reference_melody(tpl), sess.transpose)
            phrase_lyrics = list(tpl.lyrics or [])
            sess.template_quality = tpl.quality or None
            # 模板时长可能比本次演唱短（用户第二遍多唱了）→ 同样只评覆盖段
            aligned, align_info, notes_used, window_extra = _align_window(
                notes, ref, f0b, sess.sr)
        else:
            # 模式 B 没带模板 / 模式 C：拿用户自己的演唱当参考（契约 §3.1 的两种来源）
            ref = ReferenceMelody.from_sung_notes(
                notes, "REF_SELF", "自身", mode=sess.mode, f0=f0b, sr=sess.sr)
            sess.template_quality = assess_template_quality(notes)
            aligned, align_info = align_to_reference(notes, ref, f0=f0b, sr=sess.sr)
            notes_used = notes
            self_reference = True
        ast = align_stats(aligned, align_info)
        t_align = time.perf_counter() - t0
        # ★ C14：把"这个参考有没有给人留换气的时间"记下来，供报告页解释
        #   `tempo_ratio`。三条分支（内置曲目 / 跟唱模板 / 自比）都会走到这里。
        #   ⚠️ **从音符时间轴算**，不是读 `ref.phrase_gap_ms`（那个字段不存在）——
        #      见 `Session.reference_max_gap_ms` 上的说明。
        try:
            _ns = list(getattr(ref, "notes", []) or [])
            _gaps = [_ns[i + 1].start_ms - _ns[i].end_ms
                     for i in range(len(_ns) - 1)]
            sess.reference_max_gap_ms = int(max(_gaps)) if _gaps else 0
        except Exception:  # noqa: BLE001
            sess.reference_max_gap_ms = 0

        sess.progress = {"step": 4, "total": 4, "label": "正在计算表现力"}
        t0 = time.perf_counter()
        # ★ 表现力也只在这段上算，保证六指标与音准/节奏**口径一致**
        #   （否则力度/音色取自整段、音准取自一句，两个数说的不是一回事）
        feats = compute_all(notes_used, y, sess.sr, f0b, aligned=aligned)
        _style_profile = str(
            (CONFIG.get("feedback") or {}).get("style_tolerance") or "standard")
        scores = build_scores(
            aligned, feats.get("rhythm", {}), feats,
            ktv_tolerance=ktv_tolerances(_style_profile))
        t_feat = time.perf_counter() - t0

        # ══════════════════════════════════════════════════════════
        # ★ 模式 B：参考就是本次演唱本身 → 音准/节奏/总分一律降级
        # ══════════════════════════════════════════════════════════
        # 实测（`experiments/probe_modeb_selfref.py`）：
        #   参考由 `from_sung_notes(notes, ...)` 构造，随后 `align_to_reference(notes, ref)`
        #   —— **拿自己和自己比**。后果是全部音符的 pitch_cents_dev 与 onset_dev_ms
        #   **恒为 0**，音准分和节奏分**恒为 100**；把音符顺序完全打乱再比，仍是 100。
        #   即这两个分数与"唱得怎么样"**完全无关**。
        #
        # 这不是精度问题，是语义问题：模式 B 评的是【与模板的一致性】，
        # 而这里的"模板"就是它自己 —— 一致性必然完美。
        # 与同类项目里"分数与演唱质量脱钩"是同一类缺陷
        # （见 docs/同类项目评分算法源码级对比.md §6.3）。
        #
        # ⇒ 处置（方案 b，2026-09-20 定案）：**显式降级**，不假装能评。
        #   音准/节奏/总分输出 null（契约 §3.2：null = 本次未评估，绝不用 0 代替），
        #   只保留表现力 —— 它只看演唱者自己的力度/颤音/音色，**不需要参考**，是有效的。
        #   并把原因告诉 UI，否则用户以为是自己唱得差。
        if self_reference:
            scores["pitch"]["score"] = None
            scores["pitch"]["accuracy"] = None
            scores["pitch"]["stability"] = None
            scores["rhythm"]["score"] = None
            scores["rhythm"]["accuracy"] = None
            scores["rhythm"]["stability"] = None
            scores["rhythm"]["rubato"] = None
            scores["total"] = None
            window_extra["self_reference"] = True
            window_extra["self_reference_reason"] = (
                "本次用的是「跟唱模板」模式，而模板就是这一段演唱本身 —— "
                "拿自己和自己比，音准与节奏必然完全一致，评分没有意义。"
                "所以本次**不评音准和节奏**，只给表现力（力度、颤音、音色等，"
                "这些只看你自己的演唱，不需要参考）。")
            # 逐音偏差同样是自比产物（恒为 0），必须一并清掉，
            # 否则 UI 会把"完美音准"画成一条笔直的零线。
            cleaned = []
            for m in aligned:
                c = copy.copy(m)
                c.pitch_cents_dev = None
                c.onset_dev_ms = None
                cleaned.append(c)
            aligned = cleaned

        # ★ 2026-10-20：整首跟唱用 **KTV 命中率** 作总评口径（见报告 meta）。
        _is_song = False
        _song_tpl = None
        if sess.template_id:
            _song_tpl = templates.load(sess.template_id)
            _is_song = bool(_song_tpl and (_song_tpl.source_kind or "") == "song_vocal")

        # ★ 2026-10-24：歌曲参考再加一层**句级节奏**。
        #   逐音对齐在整首歌上会被切分错误放大（实测 506 个用户音只对上 312 个），
        #   句级只看每句的进唱、句长和句内残差，更接近用户听到的“跟上拍子”。
        #   句级结果可靠时用它当节奏主分；不可靠时仍走下面的逐音门槛。
        _phrase_rhythm = None
        if _is_song and not self_reference:
            try:
                _phrase_rhythm = score_phrases(
                    aligned, ref,
                    lyrics=(phrase_lyrics or (getattr(ref, "lyric_lines", None) or [])))
            except Exception as _e:  # noqa: BLE001
                print(f"[SingLens] ⚠️ 句级节奏计算失败：{_e}")
                _phrase_rhythm = None
            if _phrase_rhythm:
                if (_phrase_rhythm.get("phrase_count", 0) < 5
                        or _phrase_rhythm.get("phrase_coverage", 0) < 0.5
                        or _phrase_rhythm.get("note_coverage", 0) < 0.5):
                    _phrase_rhythm = None
        if _phrase_rhythm:
            window_extra["phrase_rhythm"] = {
                "method": "phrase",
                "score": _phrase_rhythm.get("score"),
                "phrase_count": _phrase_rhythm.get("phrase_count"),
                "phrase_total": _phrase_rhythm.get("phrase_total"),
                "phrase_coverage": _phrase_rhythm.get("phrase_coverage"),
                "note_coverage": _phrase_rhythm.get("note_coverage"),
                "global_tempo": _phrase_rhythm.get("global_tempo"),
                "global_offset_ms": _phrase_rhythm.get("global_offset_ms"),
            }

        # ★ 2026-10-24：节拍级只做**诊断**，暂不参与总分。
        #   拍点从模板的 backing 伴奏上取；参考音符是原始歌曲时间轴，
        #   而 beat grid / 用户录音都含 2 秒预备 ⇒ ref 要加 GUIDE_LEAD_MS。
        _beat = None
        if (_is_song and not self_reference and _song_tpl
                and getattr(_song_tpl, "backing_path", None)):
            try:
                _bg = beat_grid_from_audio(
                    _song_tpl.backing_path, offset_ms=GUIDE_LEAD_MS)
                if _bg:
                    _beat = diagnose_beats(
                        notes, ref.notes, _bg["beats"],
                        ref_offset_ms=GUIDE_LEAD_MS)
                    if _beat:
                        _beat["tempo_bpm"] = _bg.get("tempo_bpm")
                        scores["beat"] = _beat
                        window_extra["beat_diagnostic"] = {
                            k: _beat.get(k) for k in (
                                "hit_rate", "raw_hit_rate", "phase_ms",
                                "stability_ms", "active_beats", "hit_beats",
                                "tolerance_ms", "tempo_bpm", "worst_bars")
                        }
            except Exception as _e:  # noqa: BLE001
                print(f"[SingLens] ⚠️ 节拍诊断失败：{_e}")

        _total_basis = "weighted"
        _total_strict = scores.get("total")
        if _is_song and not self_reference:
            _k = (scores.get("ktv") or {}).get("score")
            if _k is not None:
                scores["total_strict"] = _total_strict
                scores["total"] = float(_k)
                # 2026-10-21：报告卡片和总分必须同一口径。以前总分用 KTV
                # （命中率），卡片却还是六指标的 0/50，用户看到"0 分音准"
                # 不可能理解 35.5 怎么来的。现在歌曲参考的 pitch/rhythm 卡片
                # 显示 KTV 容差带分；严格分仍在 total_strict。
                _kk = scores.get("ktv") or {}
                if _kk.get("pitch_score") is not None:
                    scores["pitch"]["score"] = _kk["pitch_score"]
                    scores["pitch"]["basis"] = "ktv"
                if _kk.get("rhythm_score") is not None:
                    scores["rhythm"]["score"] = _kk["rhythm_score"]
                    scores["rhythm"]["basis"] = "ktv"
                _total_basis = "ktv"

        # 句级节奏可靠时，接管“节奏”主分；音准仍用 KTV 口径。
        # 总分用同一口径：0.7 音准 + 0.3 句级节奏。
        if _phrase_rhythm and _phrase_rhythm.get("score") is not None:
            _rhythm_score = float(_phrase_rhythm["score"])
            # 保留逐音的对齐统计，但把卡片上的 accuracy/stability 换成句级口径，
            # 否则会出现“节奏 64 分，旁边写着每音抖 285ms”的前后矛盾。
            scores["rhythm"]["note_accuracy"] = scores["rhythm"].get("accuracy")
            scores["rhythm"]["note_stability"] = scores["rhythm"].get("stability")
            scores["rhythm"]["score"] = _rhythm_score
            scores["rhythm"]["accuracy"] = _phrase_rhythm.get("mean_abs_start_dev_ms")
            scores["rhythm"]["stability"] = _phrase_rhythm.get("mean_residual_ms")
            scores["rhythm"]["basis"] = "phrase"
            scores["rhythm"]["phrase"] = _phrase_rhythm
            _pitch_score = (scores.get("pitch") or {}).get("score")
            if _pitch_score is not None:
                scores["total"] = round(
                    0.7 * float(_pitch_score) + 0.3 * _rhythm_score, 1)
            # 逐音 KTV 总分和句级总分不是一个口径，不能同时挂在“总评”上。
            # 保留 pitch/rhythm 明细供拆解，隐藏 KTV 总分卡。
            _kk = scores.get("ktv") or {}
            _kk["score"] = None
            _kk["rhythm_score"] = None
            _kk["rhythm_hit_rate"] = None
            _total_basis = "ktv_phrase"

        # ★ 2026-10-23：**逐音对齐率太低时，不把逐音节奏当结论。**
        #   如果句级节奏已经可靠，就走句级；只有句级也拿不到时才清空。
        _low_align = (not self_reference and ast.get("align_ratio") is not None
                      and ast["align_ratio"] < 0.70)
        if _low_align and not _phrase_rhythm:
            _pct = ast["align_ratio"] * 100
            _why = (f"对齐率仅 {_pct:.1f}%（{ast.get('aligned')}/{ast.get('total')}），"
                    f"能对上的音符太少，本次节奏与总分不计算")
            scores["rhythm"]["score"] = None
            scores["rhythm"]["accuracy"] = None
            scores["rhythm"]["stability"] = None
            scores["rhythm"]["rubato"] = None
            scores["rhythm"]["reason"] = _why
            _kk = scores.get("ktv") or {}
            _kk["score"] = None
            _kk["rhythm_score"] = None
            _kk["rhythm_hit_rate"] = None
            scores["total"] = None
            window_extra["rhythm_unassessed"] = _why
            _total_basis = "unassessed"

        gaps = audit_gaps(notes, y, sess.sr)
        bad = [g for g in gaps if g["is_problem"]]
        sess.report = build_report(sess, notes, aligned, scores, feats, ast, {
            "pitch_ms": int(t_pitch * 1000), "segment_ms": int(t_seg * 1000),
            "align_ms": int(t_align * 1000), "features_ms": int(t_feat * 1000),
            "total_ms": int((t_pitch + t_seg + t_align + t_feat) * 1000),
            "audio_ms": int(len(y) / sess.sr * 1000),
            "audio_quality": {"cut_characters": len(bad)},
            "pitch_range": range_info,
            # ★ 记下这次到底做没做伴奏预处理。设置页可以改它，所以**不同次练习的
            #   口径可能不同** —— 报告里不写清楚，事后没人能复现这个分数
            #   （§27.2 的规矩：每个数字都要能指回产生它的条件）。
            "accompaniment_mode": hpss,
            "eval_window": window_extra,
            "total_basis": _total_basis,
            "total_strict": _total_strict,
        }, y)
        sess.status = "SCORED"
        sess.finished_at = time.time()
        _record_for_singer(sess)
    except Exception as e:  # noqa: BLE001
        sess.status = "FAILED"
        sess.error = {"code": "INTERNAL", "message": f"分析失败：{e}"}


def build_report(sess, notes, aligned, scores, feats, ast, perf, y=None) -> dict:
    """组装契约 §2.2 的 ScoreReport。"""
    # ★ 2026-10-20：歌曲参考（原唱人声自动分离）要能在报告里说清来源与质量。
    #   只读一次模板；模板不存在时当作普通 B 模板（不阻塞报告）。
    _ref_tpl = None
    try:
        _ref_tpl = templates.load(sess.template_id) if sess.template_id else None
    except Exception:  # noqa: BLE001
        _ref_tpl = None
    _ref_is_song = bool(_ref_tpl and (_ref_tpl.source_kind or "") == "song_vocal")
    segments = []
    vd = {p["note_index"]: p for p in feats.get("vibrato_detail", {}).get("per_note", [])}
    for m in aligned:
        v = vd.get(m.note_index, {})
        segments.append({
            "note_index": m.note_index,
            "ref_note_index": getattr(m, "ref_note_index", None),
            "start_ms": int(m.start_ms), "end_ms": int(m.end_ms),
            "pitch_midi": round(m.pitch_midi, 2) if m.pitch_midi is not None else None,
            "pitch_cents_dev": getattr(m, "pitch_cents_dev", None),
            "onset_dev_ms": getattr(m, "onset_dev_ms", None),
            "rms_mean": round(m.rms_mean, 4) if m.rms_mean is not None else None,
            "rms_peak": round(m.rms_peak, 4) if m.rms_peak is not None else None,
            "onset_rise_ms": None,
            "vibrato_rate": round(v["rate_hz"], 2) if v.get("rate_hz") else None,
            "vibrato_depth": round(v["depth_semitone"], 3) if v.get("depth_semitone") else None,
            "centroid_mean": None,
            "jump_suppressed": False,
            "align_method": getattr(m, "align_method", "DTW"),
        })

    # 总评与问题片段要先算出来（总评要用到 problems）
    problems = build_problems(segments, feats)
    # ★ 2026-10-20：整首跟唱里，个别音会**对到和声/重复段**（自评实测 9/190
    #   个 |偏差|>200 音分）。这些点会把练习计划带偏成"第 1–2 个音低 639"，
    #   而用户实际唱得没问题。有其它问题时先剔掉这些离群段；
    #   如果**全部**都很大（真的一直跑调），就保留原样，不掩盖问题。
    _outliers_removed = 0
    if _ref_is_song and problems:
        _clean = [p for p in problems if (p.get("median_abs") or 0) <= 300]
        if _clean:
            _outliers_removed = len(problems) - len(_clean)
            problems = _clean
    _self_ref = bool((perf.get("eval_window") or {}).get("self_reference"))
    # ★ 2026-10-02：总评现在**先试 LLM，失败走规则兜底**（契约 §5.1）。
    #   见 `analysis/coach.py`：三条安全约束 + 永不抛异常的降级链。
    #   ⚠️ 规则版**仍然每次都算**（`_rule_summary`）—— 它既是兜底，
    #      也是"LLM 说了什么"的对照物。不这么做的话，
    #      一旦 LLM 出问题，报告页就空着（而这个洞以前真的存在过）。
    _rule_text, _ = build_summary(scores, feats, problems, _self_ref)
    comments, comment_source = _rule_text, "rule_fallback"
    _coach_struct = None
    _llm_why = ""
    if bool(CONFIG.get("feedback", {}).get("ai_comment")):
        try:
            from analysis import coach as _coach
            _t, _st, _src, _why = _coach.generate(
                scores, feats, problems,
                ref_title=(perf.get("reference_title")
                           or (perf.get("eval_window") or {}).get("reference_title")),
                tempo_ratio=(scores.get("rhythm") or {}).get("tempo_ratio"),
                self_reference=_self_ref)
            if _t:
                comments, comment_source = _t, _src
                # ★ 结构化那一份也带上（前端优先用它渲染"教练卡"）。
                #   拿不到就是 None ⇒ 前端退回散文，**界面不会空**。
                _coach_struct = _st
            else:
                _llm_why = _why
        except Exception as _e:  # noqa: BLE001
            # LLM 的任何故障都**不许**拖垮报告页
            _llm_why = f"调用异常：{str(_e)[:80]}"
    else:
        _llm_why = "开关未打开（feedback.ai_comment=false）"
    if _llm_why:
        # 不静默：把"为什么没用 LLM"记进 meta，排查时不用猜
        perf.setdefault("llm_note", _llm_why)

    # 练习计划（B1）要用到两样这里才拿得到的东西：
    #   ① 报告 meta 的一部分（`self_reference` / `pitch_offset_semitones`）——
    #      它们此刻还没组装成最终的 meta 字典，所以先备一份
    #   ② **参考旋律的音高**（合成示范音用）——
    #      绝不能用 `segments[].pitch_midi`：那是**用户实际唱的**音高，
    #      拿它当示范等于把错的当对的教。
    #      模式 A 从内置曲目取；模式 B 从模板取；自比时没有参考 ⇒ 空列表，
    #      计划里那条规则本来也会因为 `self_reference` 先返回。
    _pp_meta = {
        "self_reference": _self_ref,
        "pitch_offset_semitones": (perf.get("eval_window") or {}).get("pitch_offset_semitones"),
    }
    _pp_ref_notes = []
    try:
        _rid = sess.reference_id or ""
        if _rid and not _self_ref:
            from analysis.align import BUILTIN as _B, load_builtin as _lb
            if _rid in _B:
                _ref = _lb(_rid, first_start_ms=0)
                _lines = _ref.lyric_lines or []
                for n in _ref.notes:
                    d = {"index": n.index, "pitch_midi": n.pitch_midi,
                         "start_ms": n.start_ms, "end_ms": n.end_ms}
                    ln = next((x for x in _lines
                               if x.get("from", 0) <= n.index <= x.get("to", -1)), None)
                    if ln:
                        pos = n.index - int(ln["from"])
                        text = ln.get("text") or ""
                        d.update({
                            "line": text, "line_index": _lines.index(ln),
                            "line_no": _lines.index(ln) + 1,
                            "syllable": text[pos] if 0 <= pos < len(text) else "",
                            "line_from": int(ln["from"]), "line_to": int(ln["to"]),
                        })
                    _pp_ref_notes.append(d)
            elif sess.template_id:
                _tpl = templates.load(sess.template_id)
                if _tpl:
                    _pp_ref_notes = [{"index": i, "pitch_midi": n.get("pitch_midi"),
                                      "start_ms": n.get("start_ms"), "end_ms": n.get("end_ms")}
                                     for i, n in enumerate(_tpl.notes or [])
                                     if n.get("pitch_midi") is not None]
                    # ★ 2026-10-20：整首歌词按**时间**映射到参考音。
                    #   这样练习计划能说「第 2 句「…」」，而不是「第 36–37 个音」。
                    _lyr = _tpl.lyrics or []
                    if _lyr:
                        for d in _pp_ref_notes:
                            mid = (int(d.get("start_ms") or 0) + int(d.get("end_ms") or 0)) / 2
                            li = next((k for k, x in enumerate(_lyr)
                                       if int(x.get("start_ms") or 0) <= mid
                                       < int(x.get("end_ms") or 0)), None)
                            if li is None:
                                continue
                            ids = [q["index"] for q in _pp_ref_notes
                                   if int(_lyr[li].get("start_ms") or 0)
                                   <= (int(q.get("start_ms") or 0)
                                       + int(q.get("end_ms") or 0)) / 2
                                   < int(_lyr[li].get("end_ms") or 0)]
                            if not ids:
                                continue
                            d.update({
                                "line": _lyr[li].get("text") or "",
                                "line_index": li, "line_no": li + 1,
                                "line_from": min(ids), "line_to": max(ids),
                            })
    except Exception as _e:  # noqa: BLE001
        # 取不到参考不影响报告本身 —— 只是没有示范音可播（计划会自动降级）
        print(f"[SingLens] ⚠️ 取参考旋律失败（示范音将不可用）：{_e}")
        _pp_ref_notes = []

    # ★ 练习计划 + 微练习卡（2026-10-19）：同一个教学决定，两种粒度。
    #   plan 说"下一遍只改哪一件事"；card 把它变成"只练这一段 + 过关标准"。
    #   style_profile 来自设置页「评分宽容度」；报告生成时定格（旧报告不追溯）。
    _style_profile = str(
        (CONFIG.get("feedback") or {}).get("style_tolerance") or "standard")
    _plan = practice_plan.build(
        scores, feats, problems, segments, _pp_meta, _pp_ref_notes,
        style_profile=_style_profile)
    _practice_card = practice_plan.build_practice_card(
        _plan, _pp_ref_notes, segments, scores, feats, problems,
        style_profile=_style_profile)
    # G58：把节拍诊断里最不稳的一小节也做成可练的短片段。
    # 只对有 beat 诊断的整首歌报告生效；没有就 None，不造死按钮。
    _beat_card = practice_plan.build_beat_card(
        scores.get("beat"), _pp_ref_notes, ref_offset_ms=GUIDE_LEAD_MS)

    return {
        "session_uid": sess.uid,
        "retry_of_uid": sess.retry_of_uid,
        "pitch": {"score": r1(scores["pitch"]["score"]),
                  "accuracy": r1(scores["pitch"]["accuracy"]),
                  "stability": r1(scores["pitch"]["stability"])},        "rhythm": {"score": r1(scores["rhythm"]["score"]),
                   "basis": scores["rhythm"].get("basis"),
                   "phrase": scores["rhythm"].get("phrase"),
                   "accuracy": r1(scores["rhythm"]["accuracy"]),
                   "stability": r1(scores["rhythm"]["stability"]),
                   "rubato": r1(scores["rhythm"]["rubato"]),
                   # ★ 2026-10-02：`accuracy` 的口径改了 —— 它原来量的是
                   #   `|mean(逐音起音偏差)|`，而那个量**主要在测"演唱者选了什么速度"**
                   #   （实测：把参考缩放到演唱者自己的速度后，accuracy 下降 38–85%）。
                   #   现在改成"偏离**自己那条速度线**的 RMS"，速度那一项被减掉了。
                   #   下面两个配套字段让"扣了多少"在报告里可见（不静默）：
                   #     · `raw_accuracy`     = 旧口径的值（含速度），供对照与回填
                   #     · `tempo_removed_ms` = 被扣掉那条速度线的 RMS 幅度
                   "raw_accuracy": r1(scores["rhythm"].get("raw_accuracy")),
                   "tempo_removed_ms": r1(scores["rhythm"].get("tempo_removed_ms")),
                   # ★ 相对量（无量纲）= accuracy ÷ 乐句跨度。
                   #   `accuracy` 是绝对毫秒、与乐句长度**严格成正比**
                   #   （实测每音 250/500/1000/2000ms → 15.9/31.7/63.4/126.8ms，
                   #     acc÷总长 恒为 0.00151）⇒ 固定毫秒判据等于
                   #   "长曲子更难拿分"。这个量消掉了长度，现在只报不算分
                   #   （要先用满足实验前提的真人素材标定）。
                   "accuracy_rel": (None if scores["rhythm"].get("accuracy_rel")
                                    is None
                                    else round(float(
                                        scores["rhythm"]["accuracy_rel"]), 6)),
                   "phrase_span_ms": r1(scores["rhythm"].get("phrase_span_ms")),
                   # ★ 整体**时长比**（演唱用时 / 参考用时）。**不参与算分** ——
                   #   它回答的是"你有没有跟住拍子"，是反馈不是惩罚。
                   #   ⚠️ 是时长比不是速度比：1.27 表示"多花了 27% 的时间"（= 慢）。
                   #      前端换算成人话时要取倒数（速度 = 1/时长比），
                   #      第一版直接把 +27% 说成"快了 27%"，方向反了。
                   #   清唱时这个数会明显偏离 1（实测 1.06 ~ 1.51），
                   #   因为"按自己的速度走"根本没有可对齐的外部拍子；
                   #   产品决定是要求戴耳机跟伴奏唱，所以它必须被报出来。
                   #   见 `项目记忆.md` §53。
                   "tempo_ratio": (round(float(scores["rhythm"]["tempo_ratio"]), 4)
                                   if scores["rhythm"].get("tempo_ratio") else None)},
        "expressiveness": {
            "dynamic_range": _f(feats.get("dynamic_range")),
            "phrase_dynamics": _f(feats.get("phrase_dynamics")),
            "rubato": _f(feats.get("rubato")),
            "vibrato_rate": _f(feats.get("vibrato_rate")),
            "vibrato_depth": _f(feats.get("vibrato_depth")),
            "onset_style": _f(feats.get("onset_style")),
            "brightness": _f(feats.get("brightness")),
            "score": r1(scores["expressiveness"]["score"]),
            "weights": scores["expressiveness"]["weights"],
            "normalized": scores["expressiveness"]["normalized"],
        },
        "total": r1(scores["total"]),
        # ★ KTV 式并列参考分（2026-10-18）：八度折叠 + 命中率 + 时长加权。
        #   不替换六指标 C，只多给一个对离群点不敏感的口径。
        "ktv": scores.get("ktv"),
        # ★ 2026-10-24：节拍级诊断（scored=false，不参与 total）。
        "beat_diagnostic": scores.get("beat"),
        "weight_version": scores["weight_version"],
        "metrics_excluded": scores["excluded"],
        "segments": segments,
        "problems": problems,
        # ★ 练习计划（B1，2026-10-01）：把"哪里不对"收敛成"下一遍只改哪一件事"。
        #   为什么单独一个字段、而不是塞进 `comments`：
        #     `problems` 是**测量**（可能有很多处偏，要全），
        #     `practice_plan` 是**教学决定**（一次只能改一件事，所以要少）。
        #     混在一起的结果就是现在这样 —— 7 条建议并列，用户不知道该先改哪个。
        #   参考音高从参考旋律取（合成示范音要用**应该唱的音**，
        #     不是用户实际唱的那个 —— 那等于把错的当对的教）。
        "practice_plan": _plan,
        # ★ 微练习卡（2026-10-19）：plan 的"只改一件事"再收敛成
        #   "只练这一小段 + 可验证的过关标准"；前端据此长出「练这一句」。
        #   没有可定位参考片段时是 None —— 不硬造按钮。
        "practice_card": _practice_card,
        # G58：节拍诊断的短片段（可点“只练这几小节”）；不参与评分。
        "beat_practice": _beat_card,
        # ★ 总评（契约 §5.1：分项用模板、**总评用 LLM**；LLM 不可用时退到规则）。
        #   2026-09-24 之前这里恒为 `None` + `rule_fallback` ——
        #   也就是说**规则兜底那一条路从来没被实现**，而前端当时显示的是
        #   v7 稿里四段冻结的文案，所以谁都没发现。
        #   这轮把报告页接上真实数据后，教练卡立刻显示"后端没返回 comments"，
        #   这个洞才露出来（页面上最有人味的一块一直是假的）。
        "comments": comments,
        "comment_source": comment_source,
        # ★ 2026-10-07：总评的**结构化**那一份（`{headline, body, drill}`）。
        #   前端优先用它渲染「教练卡」—— 三段分开摆，练法单独突出。
        #   `None` 时不报错：前端退回按 `comments` 的散文渲染。
        #   （这样**换模型 / 换 prompt 都不会让界面空掉**。）
        "coach_struct": _coach_struct,
        "comments_by_metric": build_metric_comments(feats),
        "evidence": build_evidence(segments, feats, y, sess.sr),
        "meta": {
            "mode": sess.mode, "duration_ms": perf["audio_ms"],
            "rt_latency_ms_p50": _pct(sess.rt.proc_ms, 50),
            "rt_latency_ms_p95": _pct(sess.rt.proc_ms, 95),
            "precise_cost_ms": perf["total_ms"],
            # ★ 本次是否做了伴奏预处理（HPSS 谐波分量）。可复现性的一部分：
            #   设置页能改这个开关，改了之后同样的音频会得到不同的帧，
            #   报告里不写就没人能解释"为什么昨天 82.9 今天 78.1"。
            "accompaniment_mode": bool(perf.get("accompaniment_mode")),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(sess.created_at)),
            "reference_id": sess.reference_id or "REF_SELF",
            # ★ 给人看的曲名。reference_id 可能是 `tpl_c341f5b99908` 这种号，
            #   直接显示对用户毫无意义（实测那一行会撑爆并**被 CSS 裁掉**）。
            #   模式 A 取内置曲目的标题，模式 B 取模板标题。
            "reference_title": sess.reference_title,
            # ★ 2026-10-19：**全曲参考音数**。报告里的「参考共 8 个音」原来是
            #   eval_window 的窗口音数，被误读成整首参考只有 8 个音；
            #   这里显式给全曲数量，前端写「对齐窗口 8 / 全曲 42」。
            "reference_notes": len(_pp_ref_notes) or None,
            # 参考从哪来。四态，别再用二元的 mode=="A" 判断 ——
            # 模式 B 带模板走的是模板，不带模板才是自比，两者的论文含义完全不同。
            # ★ 2026-10-20 新增 `song_vocal`：上传歌曲自动分离出的原唱参考。
            "reference_source": ("builtin_score" if sess.mode == "A"
                                 else "song_vocal" if _ref_is_song
                                 else "user_template" if sess.template_id
                                 else "self_reference"),
            # ★ 歌曲参考的质量与段落元信息（前端要显示"自动提取，不是人工谱"）。
            "reference_quality": ((_ref_tpl.quality or {}) if _ref_is_song else None),
            "reference_song": ((_ref_tpl.source_meta or {}) if _ref_is_song else None),
            "reference_lyrics_count": (len(_ref_tpl.lyrics or [])
                                       if _ref_is_song else 0),
            "reference_has_lyrics": bool(_ref_tpl.lyrics) if _ref_is_song else False,
            # 整首跟唱的总评口径：weighted（A/B 的加权总分）或 ktv（跟唱命中）
            "total_basis": (perf.get("total_basis") or "weighted"),
            "rhythm_basis": scores["rhythm"].get("basis"),
            "phrase_rhythm": (perf.get("eval_window") or {}).get("phrase_rhythm"),
            "beat_diagnostic": (perf.get("eval_window") or {}).get("beat_diagnostic"),
            "total_strict": perf.get("total_strict"),
            "problems_outliers_removed": _outliers_removed,
            # ★ 2026-10-18：这次参考整体移调了多少半音（0 = 原调）。
            #   报告页要显示，否则用户不知道分数是跟移调后的参考比的。
            "transpose": int(sess.transpose or 0),
            "align": ast,
            # ★ G13：这一遍的录音在不在（报告页要不要给"听这一段"按钮）。
            #   ⚠️ 这个 meta 是**手工重建**的字典 —— 上面设了值这里不显式取一次
            #      就到不了报告里（这个坑 §101 已经踩过一次）。
            "audio_available": bool((sess.audio_info or {}).get("saved")),
            "audio_reason": (sess.audio_info or {}).get("reason") or "",
            # ★ C14：参考里相邻两音最大的空隙（0 = 没给人留换气的时间）。
            #   前端据此决定要不要说"你这个慢里有一部分可能是换气 ——
            #   而那不算没跟住拍子"。有换气位的参考要说这句话就是**替用户找借口**。
            "reference_max_gap_ms": int(sess.reference_max_gap_ms or 0),
            # ★ 对齐降级说明。降级时音准/节奏分是 null（未评估），
            #   UI 必须能告诉用户【为什么】没评 —— 否则用户以为是自己唱得差。
            "align_degraded": (ast or {}).get("method") == "EQUAL_DIVISION",
            "align_reason": (ast or {}).get("reason") or (ast or {}).get("dtw_reason"),
            # ★ 2026-10-02：总评**没用 LLM 的原因**。
            #   ⚠️ 这个 meta 是**手工重建**的字典 —— 上面 `perf["llm_note"]`
            #      设了值，但这里不显式取一次就**到不了报告里**，
            #      而且不报错（静默丢字段）。
            #      **今天第二次踩这个坑**：上一次是 `accuracy_rel`
            #      （沿途 `compute_all` 与 `build_scores` 两处手工重建 dict）。
            #   ⇒ 规矩：新增字段要沿着每一处手工重建的 dict 各补一行。
            "llm_note": perf.get("llm_note"),
            "comment_source": comment_source,
            # ★ 整体移调量（半音）。演唱者比参考唱高/唱低了多少。
            #   音准分已经把移调扣掉了（见 analysis/align.py），
            #   这个字段用来告诉用户"你比参考低了 11 个半音" —— 这是有用的
            #   反馈（"换个调跟"），不是错误。降级时为 None。
            "pitch_offset_semitones": ((ast or {}).get("pitch_offset_semitones")
                                       if (ast or {}).get("pitch_offset_semitones") is not None
                                       else (perf.get("eval_window") or {}).get(
                                           "pitch_offset_semitones")),
            # ★ 本次【实际评了哪一段】。参考曲目只有一句，而用户可能唱了多句/多遍；
            #   此时只评参考覆盖的那一段。UI 必须告诉用户"只评了前 N 个音"，
            #   否则用户以为整首都评了。
            "eval_window": perf.get("eval_window") or {},
            # ★ 实时只分析了后一段时的说明。null = 全程都分析了。
            #   前端要显示"本次只分析了后 N 秒" —— 与 eval_window 同一类信息：
            #   **用户有权知道哪一部分没被评**。
            "analyzed_from_ms": sess.truncated_at_ms,
            # ★ 模式 B 自比降级：参考就是本次演唱本身 → 音准/节奏/总分为 null。
            #   UI 必须显示原因，否则用户以为是自己唱得差。
            "self_reference": bool((perf.get("eval_window") or {}).get("self_reference")),
            "self_reference_reason": (perf.get("eval_window") or {}).get(
                "self_reference_reason"),
            "perf_breakdown_ms": perf,
            "template_quality": sess.template_quality,
        },
    }


def _f(x):
    """六指标：None 保持 None（契约：null 表示本次未评估，绝不用 0 代替）"""
    return None if x is None else round(float(x), 2)


def _pct(a, p):
    return None if not a else round(float(np.percentile(a, p)), 1)


def build_problems(segments, feats) -> list:
    """按契约 §2.4 的聚合规则生成问题片段（后端聚合，前端不重算）。

    ★ 2026-10-20：片段内部的"代表值"从**均值**改成**中位数**。
      实测 `sess_1b9d475c59c1` 第 1–4 个音是 [+520.5, -39.5, +95.5, -29.5]：
      均值 137（被第一个音的 +520 拉走），中位符号 33、中位绝对值 67.5。
      同一段话在报告里有三处会引用它（问题卡、练习计划、微练习卡），
      旧口径让它们写成 137 / 520 / 67.5 三个数 —— 用户无法判断信哪个。
      中位数与微练习卡本来就是同一个量，现在三处统一。
    """
    out = []

    def scan(key, thr, kind, sev_thr, headline_fn, detail_fn):
        run = []
        for s in segments:
            v = s.get(key)
            if v is not None and abs(v) > thr:
                run.append(s)
            else:
                if len(run) >= 2:
                    out.append(_mk(run, kind, sev_thr, headline_fn, detail_fn))
                run = []
        if len(run) >= 2:
            out.append(_mk(run, kind, sev_thr, headline_fn, detail_fn))

    scan("pitch_cents_dev", 25, "PITCH", 35,
         lambda r: f"第 {r[0]['note_index']+1}–{r[-1]['note_index']+1} 个音",
         lambda r, med, mabs, mx, vals: _pitch_problem_text(med, mabs, mx, vals))
    scan("onset_dev_ms", 80, "RHYTHM", 150,
         lambda r: f"第 {r[0]['note_index']+1}–{r[-1]['note_index']+1} 个音",
         lambda r, med, mabs, mx, vals: (
             f"这一小段整体{'抢' if med < 0 else '拖'}了约 {mabs:.0f} 毫秒"
             f"（单音最大 {mx:.0f}）。跟着心里的拍子走会更稳。"))

    order = {"HIGH": 0, "MEDIUM": 1}
    return sorted(out, key=lambda p: order.get(p["severity"], 2))[:3]   # 契约：最多 3 条


def _pitch_problem_text(med: float, med_abs: float, max_abs: float,
                        vals: list[float] | None = None) -> str:
    """问题片段的人话描述。方向只在多数音同向时才说（否则是"忽高忽低"）。"""
    vals = vals or []
    n = len(vals)
    n_pos = sum(1 for v in vals if v > 25)
    n_neg = sum(1 for v in vals if v < -25)
    if n and n_pos >= max(2, n * 0.7):
        return (f"这一段整体偏高：中位偏差 {med_abs:.0f} 音分"
                f"（单音最大 {max_abs:.0f}）。先把这几个字放慢、一个一个唱准，再连起来。")
    if n and n_neg >= max(2, n * 0.7):
        return (f"这一段整体偏低：中位偏差 {med_abs:.0f} 音分"
                f"（单音最大 {max_abs:.0f}）。先把这几个字放慢、一个一个唱准，再连起来。")
    if abs(med) >= 20 and n <= 1:
        return (f"这一段偏{'高' if med > 0 else '低'}：中位偏差 {med_abs:.0f} 音分"
                f"（单音最大 {max_abs:.0f}）。先把这个字唱准，再连起来。")
    return (f"这一段音准不太稳：音分偏差的中位数 {med_abs:.0f}"
            f"（单音最大 {max_abs:.0f}），有的偏高有的偏低。"
            "先慢一点，让每个音站住再换下一个。")


def _mk(run, kind, sev_thr, headline_fn, detail_fn):
    key = "pitch_cents_dev" if kind == "PITCH" else "onset_dev_ms"
    vals = [float(s[key]) for s in run if s.get(key) is not None]
    if not vals:                       # 理论上不会发生（scan 已过滤 None）
        vals = [0.0]
    med = float(np.median(vals))
    med_abs = float(np.median([abs(v) for v in vals]))
    max_abs = float(max(abs(v) for v in vals))
    return {
        "kind": kind,
        "start_ms": run[0]["start_ms"], "end_ms": run[-1]["end_ms"],
        "note_range": [run[0]["note_index"], run[-1]["note_index"]],
        "severity": "HIGH" if med_abs > sev_thr else "MEDIUM",
        # 给练习计划排序/挑片段用：**稳健**的代表值，不是均值
        "median": round(med, 1),
        "median_abs": round(med_abs, 1),
        "max_abs": round(max_abs, 1),
        "headline": headline_fn(run),
        "detail": detail_fn(run, med, med_abs, max_abs, vals),
    }


def build_summary(scores, feats, problems, self_reference: bool = False
                  ) -> tuple[Optional[str], str]:
    """总评（`comments` + `comment_source`）。契约 §5.1。

    ★ 这个函数在 2026-09-24 之前**根本不存在** —— 报告里 `comments` 恒为
      `None`、`comment_source` 恒为 `"rule_fallback"`。也就是说：
        · 接入 LLM 的那条路没写，
        · **规则兜底的那条路也没写**。
      而前端显示的是 v7 稿里四段冻结文案（"22 秒到 31 秒那段副歌…"），
      所以两边的洞互相遮着，一直没人发现。这轮把报告页接上真实数据后，
      页面上立刻显示"后端没返回 comments"，洞才露出来。

    ★ 现在这里实现**规则兜底**：不编造、不联网、只用本次真算出来的数。
      为什么先做规则版而不是接 LLM：
        ① 规则版**零依赖、可复现**，答辩现场断网也能跑；
        ② 它的每一句都能追溯到报告里的某个字段 —— 论文里可以逐句核对；
        ③ LLM 版接上以后是"锦上添花"，不是"没它就空着"。
      返回 `(文本, 来源)`，来源写进 `comment_source`，UI 会照实显示
      （用户有权知道这段评价是模型写的还是规则拼的）。

    ⚠️ 语气上只说**这一遍测出来的事**，不预测、不评价人格。
       "你天生五音不全"这种话，无论数据长什么样都不该由系统说出口。
    """
    p = scores.get("pitch") or {}
    rh = scores.get("rhythm") or {}
    ex = scores.get("expressiveness") or {}
    excluded = set(scores.get("excluded") or [])
    seg = []

    total = scores.get("total")
    if total is None:
        # 总分算不出来时说清为什么，而不是留空 —— 空着会被读成"0 分"。
        why = []
        if p.get("score") is None:
            why.append("音准")
        if rh.get("score") is None:
            why.append("节奏")
        if self_reference:
            seg.append("这一遍是拿它自己当参考的（模式 B 的第一遍），"
                       "所以音准和节奏这次不评 —— 第二遍才有得比。")
        elif why:
            seg.append(f"这次没能给出总分：{'、'.join(why)}这两项没有算出来，"
                       "所以没法合成一个总分。")
        else:
            seg.append("这次没能给出总分。")
        # ⚠️ 说了"下面这些问题看得出来"就**必须真的列出来** ——
        #    第一版只说了这句话就 return 了，于是文案承诺了内容却没有下文。
        for x in problems:
            if x.get("headline") or x.get("detail"):
                seg.append(f"{x.get('headline', '')}。{x.get('detail', '')}")
        if problems:
            seg.append("这些具体问题仍然看得出来，可以照着练。")
        return "\n".join(s for s in seg if s), "rule_fallback"

    ps, rs = p.get("score"), rh.get("score")
    if ps is not None and rs is not None:
        if ps >= 80 and rs >= 80:
            seg.append(f"音准 {ps:.0f}、节奏 {rs:.0f}，这两样都很扎实 —— 先把这件事记住。")
        elif ps >= 80:
            seg.append(f"音准 {ps:.0f}，唱得挺准；节奏 {rs:.0f}，是这次拖后腿的一项。")
        elif rs >= 80:
            seg.append(f"节奏 {rs:.0f}，拍子跟得住；音准 {ps:.0f}，这次音高上还差一些。")
        elif ps < 50 or rs < 50:
            # ⚠️ 低分**不许**用"还有余量"这种软话糊过去（第一版就是那么写的：
            #    节奏 0 分说成"还有余量"）。分数低就是低，说清楚才有用。
            seg.append(f"音准 {ps:.0f}、节奏 {rs:.0f} —— 这一次离参考差得比较多。")
            # ★ 2026-10-01（B1）：这里原来还跟着一句"下一遍戴耳机放原曲…这一项会
            #   立刻不一样"。**删掉了** —— 那句"下一遍怎么做"现在归
            #   `practice_plan`（报告页那张「下一遍只改这一件事」卡）。
            #   两处都写的结果是：同一屏里一个说"先解决速度"、另一个说"逐音对比"，
            #   用户不知道听谁的。**诊断归教练卡，处方归计划卡。**
            #   这里只保留"为什么慢"，那是**观察**不是建议。
            tr = rh.get("tempo_ratio")
            if isinstance(tr, (int, float)) and tr > 1.15:
                seg.append(f"其中节奏这一项，你整遍比参考慢了约 {(1 - 1 / tr) * 100:.0f}% —— "
                           "节奏分里有一半是「有没有踩在拍子上」，慢这么多会被当成没跟住。")
        else:
            seg.append(f"音准 {ps:.0f}、节奏 {rs:.0f}，这两项这次都还有余量。")

    # ★ 2026-10-20：把"分数低到底扣在哪一半"说出来。
    #   原来的规则只会说"音准 14.5"—— 而用户从报告上分不清
    #   「整首跑调」和「每个音不稳」是两种完全不同的毛病，
    #   更读不懂教练卡为什么说"整体偏差只有 -1.2 音分"。
    #   这两句只引用已算出的 accuracy / stability，不新增任何测量。
    if ps is not None and ps < 60:
        acc, stab = p.get("accuracy"), p.get("stability")
        if (isinstance(stab, (int, float)) and stab > 60
                and (not isinstance(acc, (int, float)) or abs(acc) < 30)):
            seg.append(f"音准分主要扣在「忽高忽低」：每个音上下浮动约 "
                       f"{stab:.0f} 音分；整遍并没有跑调"
                       f"（整体偏差只有 {acc:.1f} 音分）。")
        elif isinstance(acc, (int, float)) and abs(acc) >= 30:
            seg.append(f"整遍音准偏{'低' if acc < 0 else '高'}约 "
                       f"{abs(acc):.0f} 音分"
                       + (f"（忽高忽低的幅度约 {stab:.0f} 音分）。"
                          if isinstance(stab, (int, float)) else "。"))
    if rs is not None and rs < 60:
        stab = rh.get("stability")
        if isinstance(stab, (int, float)) and stab > 120:
            seg.append(f"节奏分主要扣在「每个音前后不稳」：残差抖动约 "
                       f"{stab:.0f} 毫秒，不是整遍快慢。")

    # 具体问题：只报**后端真的聚合出来的**片段（契约 §2.4，最多 3 条）
    if problems:
        first = problems[0]
        where = first.get("headline") or ""
        det = first.get("detail") or ""
        if where or det:
            seg.append(f"最明显的一处是{where}。{det}")
        if len(problems) > 1:
            others = "、".join(x.get("headline", "") for x in problems[1:] if x.get("headline"))
            if others:
                seg.append(f"另外还有：{others}。")
    else:
        seg.append("没有检出成段的问题 —— 音准和节奏都在容差内。")

    # 表现力：只说算出来的那些项，未评估的**不提**（提了就等于说它差）
    if "dynamic_range" not in excluded and feats.get("dynamic_range") is not None:
        dr = feats["dynamic_range"]
        if dr < 6:
            # ⚠️ 这里原来还跟着"这是这次最值得改的一件事：挑一句，前半句小声…"
            #    —— 那是**处方**，2026-10-01 起归 `practice_plan`。
            #    同一份报告里两处都说"最值得改的一件事"，还指的不是同一件事
            #    （一处说速度、一处说音量），用户只会觉得系统在瞎指。
            seg.append(f"音量几乎是一条直线（变化只有 {dr:.1f} dB）。")
        elif dr >= 14:
            seg.append(f"音量起伏做得开了（{dr:.1f} dB），这一项是你的优点，保持。")
    if "vibrato" in excluded:
        # 说清"为什么没评"，否则用户看到「本次未评估」会以为系统坏了
        reason = (feats.get("vibrato_detail") or {}).get("reason")
        if reason:
            seg.append(f"颤音这次没评：{reason}。想让它可测，唱的时候把一个音拖长一点。")
    if "rubato" not in excluded:
        rub = rh.get("rubato")
        # ⚠️ 分档说（第一版只有一个 `> 40` 的门槛）：
        #    实测有一份素材的 rubato 是 **3102 ms**（3 秒的趋势起伏），
        #    而文案照样说"稍微拖长了一点点、不用改" —— 那是**明显地误报**。
        #    40–300ms 才是"艺术处理"的量级；再大就不是"稍微"了。
        if isinstance(rub, (int, float)) and 40 < rub <= 300:
            seg.append(f"有几处你稍微拖长了（趋势幅度 {rub:.0f} ms）—— "
                       "这不算唱错，是跟着感觉走，系统不扣这种分。")
        elif isinstance(rub, (int, float)) and rub > 300:
            seg.append(f"节奏弹性这项测到 {rub:.0f} ms（它是加分项，不计入扣分）。"
                       "幅度偏大时听感上会像等了一下 —— 值不值得改，你自己听着定。")

    # ★ 收尾那句交给 `practice_plan`（2026-10-01）：
    #   原来这里固定加一句"一次只改一件事：先把上面第一条改掉，再唱一遍听效果。"
    #   而"上面第一条"**取决于列出顺序**，用户还得自己数；现在计划卡直接点名
    #   改哪一件事、怎么练、练到什么算好。同一句话两处说，只会互相削弱。
    return "\n".join(s for s in seg if s), "rule_fallback"


def ensure_summary(rep: dict) -> dict:
    """报告里没有总评时**就地补一条规则总评**，返回（可能是同一个）报告。

    ★ 为什么要有这个（2026-09-24）：
      总评是这轮才实现的东西，而**库里已经存了一批 `comments: null` 的报告**
      （`practice_session.report_json`）。不补的话，用户点开"昨天那次练习"
      会看到教练卡空着，而今天新唱的却有总评 —— 同一个页面上两种表现，
      用户只会以为系统坏了。

    ★ 为什么补出来的不算"编造"：
      它**只用已存报告里的字段**（分数、problems、comments_by_metric 里
      后端自己写的结论），一个数字都不新造。等价于"把当时该说的话补上"。
      真正需要新算的东西（分段、证据曲线）都原样保留，不重算。
    """
    if rep.get("comments"):
        return rep
    try:
        ex = rep.get("expressiveness") or {}
        scores = {"total": rep.get("total"),
                  "pitch": rep.get("pitch") or {},
                  "rhythm": rep.get("rhythm") or {},
                  "expressiveness": ex,
                  "excluded": rep.get("metrics_excluded") or []}
        cbm = rep.get("comments_by_metric") or {}
        feats = {"dynamic_range": ex.get("dynamic_range")}
        # 颤音的"为什么没评"当初写在 comments_by_metric.vibrato.plain 里
        if "vibrato" in scores["excluded"]:
            feats["vibrato_detail"] = {"reason": (cbm.get("vibrato") or {}).get("plain")}
        txt, src = build_summary(scores, feats, rep.get("problems") or [],
                                 bool((rep.get("meta") or {}).get("self_reference")))
    except Exception as e:  # noqa: BLE001 —— 补总评失败不该让整个报告打不开
        print(f"[SingLens] ⚠️ 补总评失败（{rep.get('session_uid')}）：{e}")
        return rep
    if not txt:
        return rep
    rep = dict(rep)
    rep["comments"] = txt
    rep["comment_source"] = src
    return rep


def _ensure_plan_matches_scores(rep: dict) -> dict:
    """旧报告里的练习计划如果和"没测到"矛盾，就地重算计划。

    ★ 2026-10-18 实测：没开口/对不上参考时 pitch/rhythm 都是 null，
      旧版 `practice_plan.build` 一路落空，给「这一遍没有明显要改的地方」——
      **从"没测到"推出"没问题"**，是假结论。
      新报告在 build 里已修；这里把**已经落库的旧报告**在读取时也纠正。
    ⚠️ 只改返回的内存副本，不写回 report_json；下次再读按当时逻辑再算。
    """
    if not isinstance(rep, dict):
        return rep
    pi = rep.get("pitch") or {}
    rh = rep.get("rhythm") or {}
    missing = pi.get("score") is None or rh.get("score") is None
    if not missing:
        return rep
    pp = rep.get("practice_plan") or {}
    if pp.get("kind") == "unassessed":
        return rep
    try:
        ex = rep.get("expressiveness") or {}
        feats = {k: ex.get(k) for k in
                 ("dynamic_range", "phrase_dynamics", "onset_style")}
        scores = {"pitch": pi, "rhythm": rh, "expressiveness": ex,
                  "total": rep.get("total")}
        rep["practice_plan"] = practice_plan.build(
            scores, feats, rep.get("problems") or [], rep.get("segments") or [],
            rep.get("meta") or {}, None,
            style_profile=str((CONFIG.get("feedback") or {}).get(
                "style_tolerance") or "standard"))
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 重算练习计划失败：{e}")
    return rep


def _ensure_practice_context(rep: dict) -> dict:
    """旧报告没有歌词/句子上下文时，按同一个参考重算 plan/card。

    2026-10-19：报告里「第 22–23 个音」用户根本不知道是哪句；
    新报告已带 context，旧报告读取时补上（不写回 report_json）。
    只对 A 内置曲目补；B 模板没有歌词，补了也只会是空。
    """
    if not isinstance(rep, dict):
        return rep
    meta = rep.get("meta") or {}
    # 旧报告没有全曲参考音数（2026-10-19）：补上给前端写
    # 「对齐窗口 8 / 全曲 42」，避免把窗口数当成整首参考。
    if not meta.get("reference_notes") and (
            meta.get("reference_source") or "") == "builtin_score":
        try:
            from analysis.align import BUILTIN as _B0, load_builtin as _lb0
            _rid0 = meta.get("reference_id") or ""
            if _rid0 in _B0:
                meta["reference_notes"] = len(_lb0(_rid0).notes)
        except Exception:  # noqa: BLE001
            pass
    pp = rep.get("practice_plan") or {}
    # ★ 2026-10-20：旧计划里的音准偏差取的是"片段首音"，会出现
    #   "这一处的偏差 520 音分"（而卡片 before 是 67.5）。
    #   facts 的旧键名就是标记；命中时即使已有 context 也要重算。
    _old_plan = any(f.get("k") == "这一处的偏差" for f in (pp.get("facts") or []))
    if pp.get("context") and not _old_plan:
        return rep
    if pp.get("kind") not in (
            "pitch_segment", "tempo", "onset", "dynamic"):
        return rep
    rid = meta.get("reference_id") or ""
    src = meta.get("reference_source") or ""
    try:
        ref_notes = []
        if src == "builtin_score":
            from analysis.align import BUILTIN as _B, load_builtin as _lb
            if rid not in _B:
                return rep
            ref = _lb(rid, first_start_ms=0)
            lines = ref.lyric_lines or []
            for n in ref.notes:
                d = {"index": n.index, "pitch_midi": n.pitch_midi,
                     "start_ms": n.start_ms, "end_ms": n.end_ms}
                ln = next((x for x in lines
                           if x.get("from", 0) <= n.index <= x.get("to", -1)), None)
                if ln:
                    pos = n.index - int(ln["from"])
                    text = ln.get("text") or ""
                    d.update({
                        "line": text, "line_index": lines.index(ln),
                        "line_no": lines.index(ln) + 1,
                        "syllable": text[pos] if 0 <= pos < len(text) else "",
                        "line_from": int(ln["from"]), "line_to": int(ln["to"]),
                    })
                ref_notes.append(d)
        else:
            # ★ N12 / 2026-10-24：旧 B 模板报告也要补 plan/card 上下文。
            #   模板的歌词是行级时间戳（没有字级），所以只补“第几句/整句”，
            #   不硬编 syllable —— 报告里显示整句，不假装知道是哪个字。
            tpl = templates.load(rid)
            if tpl is None:
                return rep
            lyr = list(tpl.lyrics or [])
            line_spans = []
            for k, line in enumerate(lyr):
                a = int(line.get("start_ms") or 0)
                b = int(line.get("end_ms") or 0)
                idxs = []
                for i, n in enumerate(tpl.notes or []):
                    if n.get("pitch_midi") is None:
                        continue
                    mid = (int(n.get("start_ms") or 0) + int(n.get("end_ms") or 0)) / 2
                    if a <= mid < b:
                        idxs.append(i)
                if idxs:
                    line_spans.append((k, a, b, line, min(idxs), max(idxs)))
            for i, n in enumerate(tpl.notes or []):
                if n.get("pitch_midi") is None:
                    continue
                d = {"index": i, "pitch_midi": n["pitch_midi"],
                     "start_ms": int(n.get("start_ms") or 0),
                     "end_ms": int(n.get("end_ms") or 0)}
                mid = (d["start_ms"] + d["end_ms"]) / 2
                hit = next((x for x in line_spans if x[1] <= mid < x[2]), None)
                if hit:
                    k, a, b, line, lo, hi = hit
                    d.update({
                        "line": line.get("text") or "",
                        "line_index": k, "line_no": k + 1,
                        "line_from": lo, "line_to": hi,
                        "line_start_ms": a, "line_end_ms": b,
                    })
                ref_notes.append(d)
        pi = rep.get("pitch") or {}
        rh = rep.get("rhythm") or {}
        ex = rep.get("expressiveness") or {}
        scores = {"pitch": pi, "rhythm": rh, "expressiveness": ex,
                  "total": rep.get("total")}
        if not meta.get("reference_notes"):
            meta["reference_notes"] = len(ref_notes) or None
        feats = {k: ex.get(k) for k in
                 ("dynamic_range", "phrase_dynamics", "onset_style")}
        style = str((CONFIG.get("feedback") or {}).get(
            "style_tolerance") or "standard")
        plan = practice_plan.build(
            scores, feats, rep.get("problems") or [], rep.get("segments") or [],
            meta, ref_notes, style_profile=style)
        rep["practice_plan"] = plan
        rep["practice_card"] = practice_plan.build_practice_card(
            plan, ref_notes, rep.get("segments") or [], scores, feats,
            rep.get("problems") or [], style_profile=style)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 补练习上下文失败：{e}")
    return rep


def _ensure_ktv(rep: dict) -> dict:
    """旧报告没有 ktv、或 ktv 是旧口径时，用已存 segments 补算（不重跑分析）。

    ★ 2026-10-21：version=3 起不再用黑白命中率当总分，改为容差带梯度分；
      旧报告（version<3）读取时按当前「评分宽容度」重算，并把总分/卡片
      一起换到同一口径。严格加权保留在 total_strict。
    """
    if not isinstance(rep, dict):
        return rep
    cur = rep.get("ktv") or {}
    try:
        ver = int(cur.get("version") or 0) if isinstance(cur, dict) else 0
    except (TypeError, ValueError):
        ver = 0
    if ver >= 3:
        return rep
    try:
        _style = str((CONFIG.get("feedback") or {}).get("style_tolerance") or "standard")
        k = score_ktv(rep.get("segments") or [], **ktv_tolerances(_style))
        if k:
            rep["ktv"] = k
            # 报告总分/卡片跟着新口径走；严格分保留在 total_strict。
            meta = rep.get("meta") or {}
            if (meta.get("total_basis") == "ktv"
                    or meta.get("reference_source") == "song_vocal"):
                if rep.get("total_strict") is None:
                    rep["total_strict"] = (
                        meta.get("total_strict") or rep.get("total"))
                meta["total_basis"] = "ktv"
                meta["total_strict"] = (
                    rep.get("total_strict") or meta.get("total_strict")
                    or rep.get("total"))
                rep["total"] = k["score"]
                if isinstance(rep.get("pitch"), dict) and k.get("pitch_score") is not None:
                    rep["pitch"]["score"] = k["pitch_score"]
                    rep["pitch"]["basis"] = "ktv"
                if isinstance(rep.get("rhythm"), dict) and k.get("rhythm_score") is not None:
                    rep["rhythm"]["score"] = k["rhythm_score"]
                    rep["rhythm"]["basis"] = "ktv"
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 补算 KTV 分失败：{e}")
    return rep


def _ensure_problems(rep: dict) -> dict:
    """旧报告的问题片段是"均值"口径时，用已存 segments 就地重算。

    ★ 为什么（2026-10-20）：旧口径把第 1–4 个音写成"整体高了 137 音分"
      （均值被一个 +520 的离群音拉走），而同一屏的练习计划写 520、
      微练习卡写 67.5。三个数互相矛盾。新口径统一用中位数，
      新字段 `median_abs` 就是"这份报告是不是旧口径"的标记。
      ⚠️ 只改返回的内存副本，不写回 report_json。
    """
    if not isinstance(rep, dict) or not rep.get("problems"):
        return rep
    ps = rep.get("problems") or []
    if all(isinstance(p.get("median_abs"), (int, float)) for p in ps):
        return rep
    try:
        rep["problems"] = build_problems(rep.get("segments") or [], {})
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 重算问题片段失败：{e}")
    return rep


def build_metric_comments(feats) -> dict:
    """六指标的人话文案（模板）。契约 §5.1：分项用模板，总评用 LLM。"""
    NA = {"level": "本次未评估", "plain": None, "action": None}
    c = {}
    dr = feats.get("dynamic_range")
    c["dynamic_range"] = NA if dr is None else {
        "level": "太 平 了" if dr < 6 else ("还 行" if dr < 14 else "有 起 伏"),
        "plain": f"你的音量变化范围是 {dr:.1f} dB。" +
                 ("整首歌基本是同一个大小声唱下来的。" if dr < 10 else "句与句之间有明显的强弱变化。"),
        "action": "挑一句你最喜欢的，前半句小声哼、后半句放开唱。先夸张一点都没关系。",
    }
    pd = feats.get("phrase_dynamics")
    c["phrase_dynamics"] = NA if pd is None else {
        "level": "比 较 平" if pd < 4 else "还 行",
        "plain": f"每句内部的起伏平均 {pd:.1f} dB。",
        "action": "把每一句当成一句话来说：句头轻起、句中最用力、句尾收回来。",
    }
    rt = feats.get("rhythm", {})
    c["rubato"] = NA if not rt.get("ok") else {
        "level": "很 好",
        "plain": f"你有几处稍微拖长了一点点（趋势幅度 {rt.get('rubato', 0):.1f} ms）。这不算唱错，是跟着感觉走。",
        "action": "不用改。别拖到下一句都开始了就行。",
    }
    if feats.get("vibrato_rate") is None:
        c["vibrato"] = dict(NA)
        c["vibrato"]["plain"] = feats.get("vibrato_detail", {}).get("reason") or None
    else:
        c["vibrato"] = {
            "level": "很 好",
            "plain": (f"你唱长音时声音有自然的轻微波动 —— 每秒约 {feats['vibrato_rate']:.1f} 次、"
                      f"幅度 {feats['vibrato_depth']*2:.2f} 半音。这是好习惯。"),
            "action": "保持就好。",
        }
    os_ = feats.get("onset_style")
    c["onset_style"] = NA if os_ is None else {
        "level": "偏 柔" if os_ > 70 else "比 较 干 脆",
        "plain": f"你每个字平均用 {os_:.0f} ms 唱出来。" +
                 ("慢慢滑出来的，听起来温柔。" if os_ > 70 else "起头比较干脆。"),
        "action": "想唱有力量的歌，把每个字的开头唱得更干脆，像喊人名字那样。",
    }
    br = feats.get("brightness")
    c["brightness"] = NA if br is None else {
        "level": "温 暖 厚 实" if br < 1400 else "清 亮",
        "plain": f"你的谱质心均值 {br:.0f} Hz，属于偏{'暖' if br < 1400 else '亮'}的音色。",
        "action": "想更清亮，唱的时候把嘴型撑开一点、笑肌抬起来。",
    }
    return c


def build_evidence(segments, feats, y=None, sr=SR, hop_length=256) -> dict:
    """证据曲线。**结构以 UI 契约为准**（契约 §7.5）。

    三个子结构，都带【时间轴】和【单位】—— 没有这两样 UI 画不出图：
      rms_envelope     {t[], v[], p95, p5, unit:'dB'}
      vibrato_spectrum {f[], v[], peak_hz, unit:'Hz'}
      onset_dev_trend  {t[], dev[], trend[], residual[], unit:'ms'}

    ★ 原本返回的是 cents_dev[] / onset_dev_ms[] 这样的裸数组，没有时间轴 ——
      前端画不了。这个结构差异是 `selftest.html?src=api` 报 FAIL 的原因之一。
    """
    cap = 120

    def thin(vals, ts=None):
        vals = list(vals or [])
        if not vals:
            return [], []
        if ts is None:
            ts = list(range(len(vals)))
        if len(vals) <= cap:
            return list(ts), vals
        idx = np.linspace(0, len(vals) - 1, cap).astype(int)
        return [ts[i] for i in idx], [vals[i] for i in idx]

    # ── ① RMS 包络（逐音符，与指标①口径一致）──
    t_note = [s_["start_ms"] for s_ in segments]
    rms_v = [s_["rms_mean"] for s_ in segments]
    pairs = [(t, v) for t, v in zip(t_note, rms_v) if v is not None]
    if pairs:
        tt, vv = thin([v for _, v in pairs], [t for t, _ in pairs])
        db = [round(float(20 * np.log10(max(v, 1e-10))), 2) for v in vv]
        arr = np.array(db)
        rms_env = {"t": tt, "v": db,
                   "p95": round(float(np.percentile(arr, 95)), 2),
                   "p5": round(float(np.percentile(arr, 5)), 2),
                   "unit": "dB"}
    else:
        rms_env = None

    # ── ② 颤音频谱（把各音符的频谱平均起来）──
    vib_spec = None
    vd = feats.get("vibrato_detail", {}).get("per_note", [])
    if vd and feats.get("vibrato_rate") is not None and y is not None:
        try:
            from analysis.pitch import extract_f0_yin
            from analysis.notes import Note
            from analysis.features import DEFAULTS, _frames
            hop_v = DEFAULTS["vibrato_hop_length"]
            f0v = extract_f0_yin(y, sr, frame_length=DEFAULTS["vibrato_frame_length"],
                                 hop_length=hop_v)
            specs = []
            for seg in segments:
                if seg.get("vibrato_rate") is None:
                    continue
                n = Note(note_index=seg["note_index"], start_ms=seg["start_ms"],
                         end_ms=seg["end_ms"])
                i0, i1 = _frames(n, hop_v, sr)
                s0 = np.asarray(f0v[i0:min(i1, len(f0v))], dtype=float)
                ok = np.isfinite(s0) & (s0 > 0)
                if ok.sum() < 12:
                    continue
                from analysis.pitch import hz_to_midi
                cents = 100.0 * (hz_to_midi(s0[ok]) - np.median(hz_to_midi(s0[ok])))
                t = np.arange(cents.size, dtype=float)
                det = cents - np.polyval(np.polyfit(t, cents, 2), t)
                specs.append(np.abs(np.fft.rfft(det * np.hanning(cents.size), n=256)))
            if specs:
                S = np.mean(specs, axis=0)
                fr = np.fft.rfftfreq(256, d=hop_v / sr)
                m = (fr >= 1.0) & (fr <= 12.0)
                vib_spec = {
                    "f": [round(float(x), 3) for x in fr[m]],
                    "v": [round(float(x / max(S[m].max(), 1e-12)), 4) for x in S[m]],
                    "peak_hz": round(float(feats["vibrato_rate"]), 2),
                    "unit": "Hz",
                }
        except Exception:  # noqa: BLE001
            vib_spec = None

    # ── ③ 起音偏差 + 三分量分解 ──
    rt = feats.get("rhythm", {})
    onset_trend = None
    if rt.get("ok") and rt.get("raw"):
        tt = [segments[0]["start_ms"] + i * 500 for i in range(len(rt["raw"]))]
        t2, dev = thin(rt["raw"], tt)
        _, trend = thin(rt["trend"], tt)
        _, resid = thin(rt["residual"], tt)
        onset_trend = {"t": t2, "dev": dev, "trend": trend, "residual": resid,
                       "unit": "ms"}

    ev = {}
    if rms_env:
        ev["rms_envelope"] = rms_env
    if vib_spec:
        ev["vibrato_spectrum"] = vib_spec
    if onset_trend:
        ev["onset_dev_trend"] = onset_trend
    return ev or None


def _record_for_singer(sess: Session):
    """写进档案：内存（快读）+ SQLite（真相）。

    ★ 两处都要写，缺一不可：
      · 内存 `SINGERS` 是**读缓存** —— 报告里带 segments/evidence 这种大对象，
        每次翻历史都解一遍 JSON 太慢；
      · SQLite 是**真相** —— 内存一重启就没了，而"每次练完都会存下来"
        是成长档案写在界面上的承诺。

    ⚠️⚠️ G3 之后（2026-10-01）这句话要说得更死一点：**库是唯一的真相，
       内存降级成纯缓存**（§58.1②）。原因是身份以前有**两个真相源**，
       而 `/progress` `/profile` 只读内存 ⇒ 按演唱者过滤时两边会不一致，
       结果是**静默串数据**（A 的成长档案里出现 B 的练习）。
       现在所有读接口都查库；这份缓存只用来在 `GET /api/singer` 里
       对照条数（库里 160、缓存 158 ⇒ 有两次写穿失败了，见下面的 except）。

    ⚠️ 写库失败**不能**让整次评分失败（用户刚唱完，报告必须给出来），
       但也不能吞掉 —— 吞掉就等于"练习悄悄没保存"，用户永远不知道。
       所以：照常返回报告，同时在服务日志里明确报错。
    """
    sid = sess.singer_id
    rec = SINGERS.setdefault(sid, {"id": sid, "sessions": []})
    r = sess.report
    entry = {
        "session_uid": sess.uid, "created_at": sess.created_at,
        "total": r["total"], "pitch": r["pitch"]["score"],
        "rhythm": r["rhythm"]["score"], "expr": r["expressiveness"]["score"],
        "expressiveness": r["expressiveness"], "metrics_excluded": r["metrics_excluded"],
        "mode": sess.mode, "retry_of_uid": sess.retry_of_uid,
        # ★ 这次跟哪个参考比。成长档案的进步曲线靠它分辨"中间换了首歌"——
        #   不同参考的分数不可比（模式 A 的《小星星》 vs 模式 B 的自建模板），
        #   混成一条线会得出"你退步了 31 分"这种假结论。
        "reference_id": (r.get("meta") or {}).get("reference_id"),
        "reference_title": (r.get("meta") or {}).get("reference_title"),
        "template_id": sess.template_id,
    }
    rec["sessions"].append(entry)
    try:
        from web import practice_store
        # ★ 顺手登记演唱者（G3）。放在写记录的同一条路上，是因为
        #   "这个人有练习记录"就是"这个人存在"的定义 ——
        #   漏登记的表现是：记录在库里，但 `/api/singer/{sid}/*` 一律 404，
        #   用户看到"没有这个演唱者"，而他的练习记录就躺在库里。
        practice_store.ensure_singer(sid)
        practice_store.save(r, singer_id=sid, session_uid=sess.uid, mode=sess.mode,
                            reference_id=entry["reference_id"],
                            reference_title=entry["reference_title"],
                            template_id=sess.template_id,
                            retry_of_uid=sess.retry_of_uid,
                            created_at=sess.created_at)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 练习记录写库失败（{sess.uid}）：{e}")
        print("           报告仍会返回，但这一条**重启后会丢**。")


# ══════════════════════════════════════════════════════════════
# ① POST /api/session
# ══════════════════════════════════════════════════════════════
@app.post("/api/session")
async def create_session(req: Request):
    body = await _json(req)
    mode = body.get("mode", "A")
    ref_id = body.get("reference_id")
    user = getattr(req.state, "user", None)
    if user and user.get("role") != "admin":
        singer = user.get("singer_id") or ""
        if not singer:
            return fail("FORBIDDEN", "这个账号没有绑定演唱者。", 403)
    else:
        singer = body.get("singer_id") or f"singer_{uuid.uuid4().hex[:6]}"
    template_id = body.get("template_id")
    # ★ 2026-10-18：移调只用于现场练习（A/B）；C 上传是"分析已有录音"，
    #   没有"先移调再唱"这回事。范围限 ±12，前端通常只给 ±6。
    try:
        transpose = int(body.get("transpose") or 0)
    except (TypeError, ValueError):
        return fail("BAD_REQUEST", "transpose 必须是整数。")
    transpose = max(-12, min(12, transpose))

    ref_obj = None
    # 这次会话"跟什么比"。分开命名是为了不再出现
    # "meta.reference_id 里塞了个模板号"这种含糊（见下面模式 B 的注释）。
    sess_reference_id = ref_id
    tpl_for_session = None
    if mode == "A":
        from analysis.align import BUILTIN
        if not ref_id:
            # ⚠️ 注意：不指定就默认《小星星》，而内置库只有 2 首。
            #    想唱别的歌请走模式 B（跟唱模板，两遍制）。
            ref_id = "REF_TWINKLE_1"
        if ref_id not in BUILTIN:
            return fail("REF_MISSING", f"找不到参考曲目 {ref_id}", 404)
        sess_reference_id = ref_id
    elif mode == "B":
        # ★ 模式 B 两遍制：第二遍必须带上第一遍存的 template_id。
        #   不带 → 只能自比（音准/节奏恒满分），那种情况下服务层会降级；
        #   带了但找不到 → 直接报错，别让用户拿到一个"降级成没评"的结果还以为是唱得差。
        if template_id:
            tpl_for_session = templates.load(template_id)
            if tpl_for_session is None:
                return fail("REF_MISSING", f"找不到跟唱模板 {template_id}", 404)
            tpl = tpl_for_session
            # ★ 2026-10-20：歌曲参考的伴奏是**原调音频**，移调会同时要求
            #   移伴奏；当前实现不搬动音频，所以明确拒绝，而不是让用户
            #   唱 +2 却被按原调判（那会静默毁掉音准分）。
            if (tpl.source_kind or "") == "song_vocal" and transpose:
                return fail("BAD_REQUEST",
                            "歌曲参考的伴奏是原调，不能移调。"
                            "想换调请重新上传歌曲并选一段。", 400)
            # ★ 会话的 reference_id 用【模板自己的 reference_id】，不是模板号。
            #   理由：`meta.reference_id` 是"这次跟哪首歌比"的唯一权威字段
            #   （契约 §2.2），报告/成长档案/实验表都读它。
            #   写模板号的话，一个"用户自建的参考"在数据里就没有名字了。
            #   template_id 另存一份 —— 那是"用哪一份模板"，两者不是一回事。
            sess_reference_id = tpl.reference_id or tpl.template_id
            ref_obj = {"reference_id": sess_reference_id, "title": tpl.title,
                       "note_count": tpl.note_count, "duration_ms": tpl.duration_ms,
                       "source": ("song_vocal"
                                  if (tpl.source_kind or "") == "song_vocal"
                                  else "user_first_take"),
                       "transpose": transpose,
                       "quality_level": (tpl.quality or {}).get("level"),
                       "has_vocal": bool(tpl.vocal_path),
                       "has_backing": bool(tpl.backing_path)}
            # ★ 2026-10-20：整首歌词随会话下发；实时页跟着高亮。
            ref_obj["lyrics"] = tpl.lyrics or []
    uid = f"sess_{uuid.uuid4().hex[:12]}"
    # 可读曲名（报告页那一行要显示它，不能显示 tpl_xxx / REF_TWINKLE_1）
    if mode == "A":
        from analysis.align import BUILTIN
        sess_title = BUILTIN[ref_id]["title"]
    elif tpl_for_session is not None:
        sess_title = tpl_for_session.title
    else:
        sess_title = None
    sess = Session(uid=uid, singer_id=singer, mode=mode,
                   reference_id=sess_reference_id, reference_title=sess_title,
                   retry_of_uid=body.get("retry_of_uid"), template_id=template_id,
                   transpose=transpose)
    if mode == "A":
        ref = _transpose_ref(load_builtin(ref_id), transpose)
        sess.rt.ref_notes = [(n.start_ms + GUIDE_LEAD_MS,
                              n.end_ms + GUIDE_LEAD_MS, n.pitch_midi)
                             for n in ref.notes]
    elif tpl_for_session is not None:
        # 模板有音符序列 → 实时管线也能显示参考音高（与模式 A 同等体验）
        # ★ 2026-10-23：同样要加 2 秒引导；否则实时参考早于耳朵听到的伴奏。
        sess.rt.ref_notes = [(n["start_ms"] + GUIDE_LEAD_MS,
                              n["end_ms"] + GUIDE_LEAD_MS,
                              float(n["pitch_midi"]) + transpose)
                             for n in tpl_for_session.notes
                             if n.get("pitch_midi") is not None]
    _reap_sessions()      # 先回收再放新的，保证不超过容量上限
    SESSIONS[uid] = sess
    # DataSource 契约要求返回 reference 对象（UI 要显示曲目名），
    # 不能只给 reference_id。
    if mode == "A" and ref_id:
        # ⚠️ 这里**只能 import BUILTIN**，绝对不要再写 `load_builtin`！！
        #
        # 🛑 2026-10-17 的重大 bug：原来这一行是
        #        from analysis.align import BUILTIN, load_builtin
        #    —— 函数体内的 import 会把 `load_builtin` 变成**局部名**，
        #    而它在本函数**更早**的 L2075（`ref = load_builtin(ref_id)`）就被用了
        #    ⇒ `UnboundLocalError: cannot access local variable 'load_builtin'`
        #    ⇒ **`POST /api/session` 一直返回 500**
        #
        #    这意味着**模式 A / B / D 的"开始"按钮全是点了没反应**（前端只在列表
        #    最下面塞一行红字，用户看到的就是"没反应"）。
        #
        # ⚠️ 为什么这么久没被发现：A1/A3 的素材走的是 `/api/upload`（模式 C），
        #    **从来没有真实点击过模式 A/B/D 的「开始」**。
        #    G15「真实麦克风端到端」正是唯一会覆盖这条路的那条验证 —— 它还挂着。
        #    ⇒ 这就是"没做端到端"的代价：**主入口坏着，而所有自动化都是绿的**。
        #
        #    `load_builtin` 在模块顶部（L82）已经导入过，这里**不需要**再导。
        from analysis.align import BUILTIN
        spec = BUILTIN[ref_id]
        # ★ 2026-10-07 修：原来是 `len(midi) × note_dur_ms`，
        #   **没算句间换气** ⇒ `REF_TWINKLE_6B` 报 21000ms，
        #   而它的真实跨度是 42×500 + 5×700 = **24500ms**（差 3.5 秒）。
        #
        #   为什么这个数要紧：它是参考的**总时长锚点** ——
        #   报告的节奏时间轴按它铺。
        #   ⚠️ 而且这类"由多个参数拼出来的量"最容易漏项：加了 `phrase_gap_ms`
        #      之后**所有手算总时长的地方都要跟着改**，这里与 L3264 都漏了。
        #   ⇒ 改用 `ReferenceMelody.duration_ms`（现成的属性，读 notes 的 end_ms）
        #      —— **只有一个来源**，以后再加时间参数也不会漏。
        try:
            _dur = load_builtin(ref_id).duration_ms
        except Exception:  # noqa: BLE001
            _dur = len(spec["midi"]) * spec["note_dur_ms"]
        ref_obj = {"reference_id": ref_id, "title": spec["title"],
                   "note_count": len(spec["midi"]),
                   "duration_ms": _dur, "transpose": transpose}
    return ok({"session_uid": uid, "singer_id": singer, "mode": mode,
               "reference_id": sess_reference_id, "reference": ref_obj,
               "template_id": template_id or None, "status": sess.status,
               "transpose": transpose,
               "sample_rate": SR, "rt_window_ms": RT_WINDOW_MS,
               "pitch_frame_hz": RT_PITCH_HZ, "metrics_frame_hz": RT_METRICS_HZ})


async def _json(req: Request) -> dict:
    """取 JSON 请求体。**形状不对就是 400，不是 500**（2026-09-24，审计 §5.1）。

    ⚠️ 原来是 `except: return {}` —— 这个"宽容"造成了两类问题：
      ① 畸形 JSON（`123` / `"a string"` / `[1,2,3]` / `null`）被当成空对象
         继续往下走，然后在 `body.get(...)` 处抛 `AttributeError` ⇒ **500**，
         而且 `str(exc)` 原样回给客户端（`'int' object has no attribute 'get'`）。
         审计实测 15 处这样的一击即 500。
      ② 前端/脚本发错形状时**没有任何提示**：明明是自己发错了，
         看到的却是一个内部异常字符串，排查方向完全被带偏。

    现在：解析失败 ⇒ 400 `BAD_REQUEST`；解析出来不是对象 ⇒ 400。
    ⚠️ 空 body 也走 400 —— 这四个端点（session/template/purge/config）
       都必须带 JSON 对象，没有一个允许空 body。真要允许空 body 的端点
       不该调这个函数（例如 finish 端点本来就没调）。
    """
    raw = await req.body()
    if not raw:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": "这个接口需要一个 JSON 对象作为请求体，但收到的是空 body。"})
    try:
        body = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": f"请求体不是合法的 JSON（{type(e).__name__}）。"
                       f"应形如 {{\"mode\": \"A\"}}。"}) from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": f"请求体必须是一个 JSON 对象，收到的是 {type(body).__name__}。"})
    # 普通账号的 singer_id 以 session 为准，不信任前端请求体。
    # /api/singer 是新建/改名端点：只在显式带了 singer_id 时覆盖，
    # 这样“没有 singer_id = 想新建”仍能被端点识别并拒绝。
    user = getattr(req.state, "user", None)
    if user and user.get("role") != "admin" and user.get("singer_id"):
        if req.url.path == "/api/singer":
            if "singer_id" in body:
                body["singer_id"] = user["singer_id"]
        else:
            body["singer_id"] = user["singer_id"]
    return body


def _s(body: dict, key: str, *, required: bool = False, default: str = "",
       max_len: int = 200) -> str:
    """取一个**字符串**字段：类型不对就 400，而不是让它在下游炸成 500。

    审计实测：`{"gender":{"x":1}}` → 500 `'int'/'dict' object has no attribute 'strip'`，
    `{"condition":123}` 同理。这里统一在入口挡住。
    """
    v = body.get(key, None)
    if v is None:
        if required:
            raise HTTPException(status_code=400, detail={
                "code": "BAD_REQUEST", "message": f"缺少必填字段 {key}。"})
        return default
    if not isinstance(v, str):
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": f"字段 {key} 必须是字符串，收到的是 {type(v).__name__}。"})
    if len(v) > max_len:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST", "message": f"字段 {key} 太长了（上限 {max_len} 字符）。"})
    return v


def _i(body: dict, key: str, *, required: bool = False, default=None,
       lo: int = None, hi: int = None):
    """取一个**整数**字段。`"abc"` / `null` / `1.5` 都要 400（原来直接 int() 炸 500）。"""
    v = body.get(key, None)
    if v is None:
        if required:
            raise HTTPException(status_code=400, detail={
                "code": "BAD_REQUEST", "message": f"缺少必填字段 {key}。"})
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        # 允许 "5" 这种数字字符串（老前端可能这么发），其余一律拒
        if isinstance(v, str) and v.strip().lstrip("+-").isdigit():
            v = int(v)
        else:
            raise HTTPException(status_code=400, detail={
                "code": "BAD_REQUEST",
                "message": f"字段 {key} 必须是整数，收到的是 {v!r}。"})
    if lo is not None and v < lo or hi is not None and v > hi:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": f"字段 {key} 超出允许范围（{lo}–{hi}），收到 {v}。"})
    return v


def _fnum(body: dict, key: str, *, default=None):
    """取一个**有限浮点**字段。NaN / inf / 1e400（解析成 inf）一律 400。

    ⚠️ 为什么必须挡：审计 §2.7 —— `1e400` 被 json 解析成 `inf` 存进 SQLite，
    之后**导出接口永久 500**（`Out of range float values are not JSON compliant`）。
    写进去只要一次，坏掉的是整张表和所有导出。
    """
    v = body.get(key, None)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": f"字段 {key} 必须是数字，收到的是 {type(v).__name__}。"})
    if not math.isfinite(float(v)):
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST",
            "message": f"字段 {key} 必须是有限数字（NaN / 无穷大不接受）。"})
    if abs(float(v)) > 1e12:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_REQUEST", "message": f"字段 {key} 的数值太大（{v}）。"})
    return float(v)


def _ws_guard_reason(ws: WebSocket) -> Optional[str]:
    """WS 版的跨站守卫。返回拒绝原因（None = 放行）。判据与 HTTP 那道一致。

    ⚠️ 浏览器的 WS 握手**会带** Origin（这是 WS 与普通 GET 的区别），
       所以 `Origin` 这一条在 WS 上比在 HTTP 上更可靠。
       `Sec-Fetch-Site` 现代浏览器也带。两个都查。
    """
    h = ws.headers
    host = (h.get("host") or "").split(":")[0].strip("[]").lower()
    if host and ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
        return "这个服务只响应本机 / 本局域网的地址。"
    if (h.get("sec-fetch-site") or "").lower() == "cross-site":
        return "拒绝了来自其它网站的连接。"
    origin = (h.get("origin") or "").rstrip("/")
    if origin and origin.lower() not in ALLOWED_ORIGINS:
        return "拒绝了来自其它网站的连接。"
    return None


# ══════════════════════════════════════════════════════════════
# ② WS /api/session/{uid}/stream
# ══════════════════════════════════════════════════════════════
@app.websocket("/api/session/{uid}/stream")
async def stream(ws: WebSocket, uid: str):
    # ★ WS **不经过 HTTP 中间件**（Starlette 的 `@app.middleware("http")` 只管 HTTP），
    #   所以 §3.1 的跨站/DNS-rebinding 守卫必须在这里单独做一遍（2026-09-24）。
    #   漏掉这一处的后果很具体：一个第三方页面能通过 rebinding 打开 WS，
    #   把用户的**实时音频**推给我们的服务、或者把实时指标读走。
    bad = _ws_guard_reason(ws)
    if bad:
        await ws.accept()
        await ws.send_json({"type": "error", "data": {"code": "FORBIDDEN",
                                                     "message": bad}})
        await ws.close(code=1008)
        return
    await ws.accept()
    user = auth_mod.user_from_request(ws) if auth_mod.auth_enabled() else None
    if auth_mod.auth_enabled() and not user:
        await ws.send_json({"type": "error", "data": {"code": "UNAUTHORIZED",
                                                     "message": "请先登录。"}})
        await ws.close(code=4401)
        return
    sess = SESSIONS.get(uid)
    if sess is None:
        await ws.send_json({"type": "error", "data": {"code": "NOT_FOUND",
                                                      "message": f"会话 {uid} 不存在"}})
        await ws.close()
        return
    if (user and user.get("role") != "admin"
            and sess.singer_id != user.get("singer_id")):
        await ws.send_json({"type": "error", "data": {"code": "FORBIDDEN",
                                                     "message": "不能操作其他演唱者的录音。"}})
        await ws.close(code=4403)
        return

    st = sess.rt
    st.started = time.perf_counter()
    st.last_seen = st.started
    try:
        while True:
            # ★ WS 空闲超时（2026-09-23 审计后加）。
            #   审计实测：一个 WS 空闲 15 秒后发 stop 仍被正常响应 ⇒
            #   **服务端没有空闲超时**，连接可以无限期占着。
            #   正常唱歌时前端每 46ms 发一块，绝不会触发这个；触发的一定是
            #   "用户关掉了页面/拔了网线"这类**该释放**的情况。
            try:
                msg = await asyncio.wait_for(ws.receive(),
                                             timeout=WS_IDLE_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                print(f"[SingLens] WS {uid} 空闲超过 {WS_IDLE_TIMEOUT_SEC}s，断开")
                break
            if msg.get("type") == "websocket.disconnect":
                break

            # ── 文本控制帧 ──
            # ★ **畸形帧不许断流**（2026-09-24，审计 §5.2）。
            #   原来 `ctl = json.loads(msg["text"])` 与 `ctl.get("type")`
            #   都没有保护：发一个 `123` / `[]` / `null` / 一行乱码，
            #   异常被外层捕获 ⇒ 回一帧 error 然后 `ws.close()` ——
            #   **整段录音就此中断**，而且把 Python 异常字符串回给了客户端。
            #   正常客户端不会发这种帧，但"一个杂帧（前端 bug / 代理 / 恶意客户端）
            #   就毁掉用户正在唱的这一遍"是不可接受的代价。
            #   现在：非对象帧**忽略**（与 `{"type":"nonsense"}` 的处理一致），
            #   连接继续；只对"能解析出 type 的帧"做动作。
            if msg.get("text") is not None:
                try:
                    ctl = json.loads(msg["text"])
                except Exception:  # noqa: BLE001
                    ctl = None
                if not isinstance(ctl, dict):
                    continue                      # 忽略，不断流
                if ctl.get("type") == "stop":
                    await ws.send_json({"type": "done",
                                        "data": {"session_uid": uid, "status": sess.status}})
                    break
                continue

            # ── 二进制音频帧 ──
            raw = msg.get("bytes")
            if not raw:
                continue
            t_recv = time.perf_counter()
            chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            st.audio.append(chunk)
            st.n_samples += len(chunk)
            st.last_seen = t_recv

            # ★ 缓冲硬上限（2026-09-23 审计后加）。
            #   原来这里只 append、从不裁剪 ⇒ 实测一个会话 0.77s 就能灌进 40 MiB，
            #   两个会话把进程 RSS 从 147 MB 推到 506 MB 且**数分钟不降**。
            #
            # ⚠️ 设计要点（第一版做错过，见 MAX_BUFFER_SAMPLES 的注释）：
            #   · 这个上限**不是**滚动窗口 —— 正常路径永远碰不到它
            #     （流在 MAX_AUDIO_SEC 就结束了，而这里留了 10 秒余量）。
            #     它挡的是"客户端单帧塞巨量数据"那种异常。
            #   · 一旦真丢了，**必须记账**（`audio_dropped`），
            #     让 finish/report 如实说"只分析了后 N 秒"。
            #     静默截断比明确说"只评了一段"坏得多（契约 §3.2 的同一条道理）。
            if st.n_samples - st.audio_dropped > MAX_BUFFER_SAMPLES:
                drop = st.n_samples - st.audio_dropped - MAX_BUFFER_SAMPLES
                while drop > 0 and st.audio:
                    head = st.audio[0]
                    if len(head) <= drop:
                        st.audio.pop(0)
                        drop -= len(head)
                        st.audio_dropped += len(head)
                    else:
                        st.audio[0] = head[drop:]     # 只丢一半，保持时间轴连续
                        st.audio_dropped += drop
                        drop = 0

            # ★ 性能：不能在每块上做 np.concatenate 整段音频（那是 O(n²)）。
            #   实时只需要最近一小段，精算等到 finish 时再拼一次。
            st.tail = chunk if st.tail is None else np.concatenate([st.tail, chunk])
            if len(st.tail) > RT_YIN_SAMPLES:
                st.tail = st.tail[-RT_YIN_SAMPLES:]

            now_ms = st.n_samples / SR * 1000.0
            # ★ 超过总时长上限就收尾（不是"静默丢掉剩下的"）。
            #   与上传那条同一个道理：PYIN 的成本随时长线性增长，而它持 GIL。
            #   这里主动发 done 让前端去 finish，用户拿到的是"唱到这儿为止"的报告，
            #   而不是一个永远不结束、内存一直涨的会话。
            if st.n_samples / SR > MAX_AUDIO_SEC:
                await ws.send_json({"type": "done", "data": {
                    "session_uid": uid, "status": sess.status,
                    "reason": f"已达单次上限 {MAX_AUDIO_SEC} 秒，自动结束。"}})
                break
            # 实时音高：只对最近 ~190ms 跑 YIN，且用大 hop 只出 2~3 帧
            f0 = extract_f0_yin(st.tail, SR,
                                frame_length=REALTIME_FRAME, hop_length=REALTIME_FRAME)
            f0 = mark_saturated(f0, FMIN_HZ, FMAX_HZ)
            rms = float(np.sqrt(np.mean(st.tail[-2048:] ** 2))) if st.tail.size >= 2048 else 0.0
            cur_f0 = float(f0[-1]) if f0.size and np.isfinite(f0[-1]) else float("nan")
            st.f0_hist.append((now_ms, cur_f0, rms))

            # 契约：pitch 帧 ~20Hz。
            # ★ 音频块本身就是天然的节拍，所以【每收到一块就回一帧】，
            #   帧率由客户端用块大小控制：46ms 块 → 21.7Hz，93ms 块 → 10.8Hz。
            #   踩过的坑：一开始写成「距上次发送 ≥50ms 才发」，结果 46ms 的块
            #   隔一块才触发一次，实测只有 10.6Hz，达不到契约的 20Hz。
            #   只保留一个上限保护，防止客户端疯狂灌数据打爆连接。
            if now_ms - st.last_pitch_sent >= 1000.0 / RT_PITCH_HZ_MAX:
                st.last_pitch_sent = now_ms
                proc = (time.perf_counter() - t_recv) * 1000.0
                st.proc_ms.append(proc)
                ref_f0 = None
                # ★ 判据是"有没有参考音高"，不是"是不是模式 A"。
                #   踩过的坑：这里原来写 `sess.mode == "A" and st.ref_notes` ——
                #   而模式 B 带模板时 `st.ref_notes` 是**填好的**
                #   （见 create_session 里那一段），却被门控挡在外面，
                #   于是"跟着自己的模板练"在实时画面上看不到参考音高条。
                #   模式 B 恰恰是最需要它的时候（没有内置谱面兜底）。
                if st.ref_notes:
                    rn = _ref_note_at(st.ref_notes, now_ms)
                    if rn:
                        ref_f0 = round(float(440.0 * 2 ** ((rn[2] - 69) / 12)), 2)
                await ws.send_json({"type": "pitch", "data": {
                    "t_ms": int(now_ms),
                    "f0": None if not np.isfinite(cur_f0) else round(cur_f0, 2),
                    "ref_f0": ref_f0,
                    "cents_dev": (None if ref_f0 is None or not np.isfinite(cur_f0)
                                  else round(1200.0 * np.log2(cur_f0 / ref_f0), 1)),
                    "proc_ms": round(proc, 2),
                }})

            # 契约：metrics 帧 1Hz
            if now_ms - st.last_metrics_sent >= 1000.0 / RT_METRICS_HZ:
                st.last_metrics_sent = now_ms
                await ws.send_json({"type": "metrics",
                                    "data": compute_realtime_metrics(sess, now_ms)})

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error",
                                "data": {"code": "INTERNAL", "message": str(e)}})
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ══════════════════════════════════════════════════════════════
# ③ POST /api/session/{uid}/finish
# ══════════════════════════════════════════════════════════════
@app.post("/api/session/{uid}/finish")
async def finish(uid: str, bg: BackgroundTasks, req: Request):
    """结束录音 → 触发精算。

    ⚠️ 关于 `analyzed_from_ms`（2026-09-23）：
       实时缓冲有一个**硬上限**（`MAX_BUFFER_SAMPLES`），正常路径永远碰不到
       （流在 `MAX_AUDIO_SEC` 就结束了，而缓冲留了 10 秒余量）。
       碰到的唯一可能是客户端异常地塞进巨量数据。

       真丢了就**必须说出来**：用户唱了 5 分钟、只分析了后 3 分钟，
       而他以为全评了 —— 这与契约 §3.2「null = 未评估」是同一条道理：
       **没评的部分不能沉默**。
    """
    sess = get_session(uid)
    user = getattr(req.state, "user", None)
    if (user and user.get("role") != "admin"
            and sess.singer_id != user.get("singer_id")):
        return fail("FORBIDDEN", "不能结束其他演唱者的录音。", 403)
    _touch(sess)
    if not sess.rt.audio:
        return fail("NO_VOICE", "没有收到任何音频。")
    sess.audio = np.concatenate(sess.rt.audio) if sess.rt.audio else None
    dropped = int(getattr(sess.rt, "audio_dropped", 0))
    sess.truncated_at_ms = (int(dropped / SR * 1000) if dropped > 0 else None)
    sess.status = "ANALYZING"
    sess.progress = {"step": 1, "total": 4, "label": "正在提取音高"}
    bg.add_task(run_precise, sess)
    return ok({"session_uid": uid, "status": sess.status,
               "duration_ms": int(len(sess.audio) / SR * 1000),
               "analyzed_from_ms": sess.truncated_at_ms,
               "note": ("没有丢弃任何音频。" if sess.truncated_at_ms is None else
                        f"实时只保留了最近一段，本次将从第 "
                        f"{sess.truncated_at_ms // 1000} 秒开始分析。")})


# ══════════════════════════════════════════════════════════════
# ④ GET /api/session/{uid}/status
# ══════════════════════════════════════════════════════════════
@app.get("/api/session/{uid}/status")
async def status(request: Request, uid: str):
    # 重启后内存是空的，但库里这条记录在 —— 状态必须能说出来，
    # 否则上传路径在"分析中刷新页面"时会得到 404，而它其实已经 SCORED。
    sess = get_session_or_db(uid)
    user = getattr(request.state, "user", None)
    if (user and user.get("role") != "admin"
            and sess.singer_id != user.get("singer_id")):
        return fail("FORBIDDEN", "不能查看其他演唱者的会话状态。", 403)
    return ok({"session_uid": uid, "status": sess.status,
               "progress": sess.progress, "error": sess.error})


# ══════════════════════════════════════════════════════════════
# ⑤ GET /api/session/{uid}/report
# ══════════════════════════════════════════════════════════════
@app.get("/api/session/{uid}/report")
async def report(uid: str, singer_id: str = ""):
    # ★ 给了 singer_id 就校验归属（2026-10-01，见 `_assert_session_owner`）：
    #   前端"当前演唱者"在 localStorage、"当前会话"在 sessionStorage，
    #   两者可以不同步 ⇒ 否则会拿上一个人的会话 uid 去取报告。
    _assert_session_owner(uid, singer_id)
    sess = SESSIONS.get(uid)
    if sess is None:
        # ★ 回退到库里（2026-09-23）。落库时**存了完整报告**（report_json），
        #   所以服务重启之后，历史里点开某一次仍然能看到完整的报告 ——
        #   segments / evidence / problems 都在，而不是只剩几个数字。
        #   没有这个回退的话，用户点开昨天的记录会看到一份残缺的报告，
        #   而界面上完全看不出来（只是"图少了一张"）—— 这种缺法最难发现。
        try:
            from web import practice_store
            saved = practice_store.load_report(uid)
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ 读库取报告失败（{uid}）：{e}")
            saved = None
        if saved is None:
            return fail("NOT_FOUND", f"会话 {uid} 不存在，也没有存过它的报告", 404)
        # ★ 老报告（这轮之前落的库）没有总评 —— 用**报告里已有的字段**补一条，
        #   否则"昨天的记录"点开是空教练卡，今天新唱的却有内容。
        # ★ 2026-10-20：旧问题片段是"均值"口径，与新练习计划/微练习卡
        #   互相矛盾（137 / 520 / 67.5）。先重算问题片段；若真的变了，
        #   旧总评与旧教练卡里引用的数也过期了 ⇒ 一起清掉，
        #   让 `ensure_summary` 用新数重写规则总评。
        #   （旧 LLM 文本不再回放：它里面写的就是那个 137。）
        _old_problems = saved.get("problems")
        saved = _ensure_problems(saved)
        if saved.get("problems") is not _old_problems:
            saved["comments"] = None
            saved["coach_struct"] = None
        # ★ KTV 口径先修正，后面的总评/计划/卡片才会用同一套分数。
        saved = _ensure_ktv(saved)
        saved = ensure_summary(saved)
        saved = _ensure_plan_matches_scores(saved)
        saved = _ensure_practice_context(saved)
        return ok(_live_audio_meta(uid, saved))
    if sess.status == "FAILED":
        e = sess.error or {}
        return fail(e.get("code", "INTERNAL"), e.get("message", "分析失败"), 422)
    if sess.report is None:
        # ⚠️ 这个 code 必须是 CONFLICT，不能是 NOT_FOUND。
        #    2026-10-18 审查实测：前端 s3 只要看到 NOT_FOUND 就认为
        #    "这条记录被删了"，清掉 sl.sess 并整屏空掉 —— 而它其实
        #    只是**还在分析**（HTTP 状态 409 已经说明了这一点）。
        #    上传路径会轮询 /status；现场唱这条路当时没轮询，于是
        #    "唱完必丢报告"。语义与状态码必须一致。
        return fail("CONFLICT", f"报告还没准备好（当前 {sess.status}）", 409)
    _old_problems = sess.report.get("problems")
    sess.report = _ensure_problems(sess.report)
    if sess.report.get("problems") is not _old_problems:
        sess.report["comments"] = None
        sess.report["coach_struct"] = None
    sess.report = _ensure_ktv(sess.report)
    sess.report = ensure_summary(sess.report)
    sess.report = _ensure_plan_matches_scores(sess.report)
    sess.report = _ensure_practice_context(sess.report)
    return ok(_live_audio_meta(uid, sess.report))


@app.post("/api/session/{uid}/coach/chat")
async def coach_chat_endpoint(uid: str, req: Request):
    """P0 追问教练：只基于这一份报告回答，不重新分析、不改分。

    请求：{message, singer_id, history:[{role,content}], focus:{line_no}}
    返回：{answer, evidence, suggestions, practice, mode, ...}
    """
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是对象。")
    singer_id = str(body.get("singer_id") or "").strip()
    message = str(body.get("message") or "").strip()
    if not message:
        return fail("BAD_REQUEST", "问点什么再发送。")
    if len(message) > 400:
        message = message[:400]
    _assert_session_owner(uid, singer_id or None)
    rep = None
    sess = SESSIONS.get(uid)
    if sess is not None and sess.report is not None:
        rep = sess.report
    else:
        try:
            from web import practice_store
            rep = practice_store.load_report(uid)
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ 追问读报告失败（{uid}）：{e}")
            rep = None
    if not rep:
        return fail("NOT_FOUND", "这条练习还没有可追问的报告。", 404)
    # 与 /report 同口径补旧报告；句级/节拍缺失时 agent 会明确说“没测到”。
    try:
        rep = _ensure_ktv(rep)
        rep = _ensure_practice_context(rep)
    except Exception:  # noqa: BLE001
        pass
    history = body.get("history")
    focus = body.get("focus")
    profile_brief = {}
    profile_sid = singer_id or (getattr(sess, "singer_id", "") if sess else "")
    if profile_sid:
        try:
            profile_brief = portrait_mod.portrait_brief(
                _portrait_for(profile_sid, limit=120))
        except Exception as e:  # noqa: BLE001
            # 画像读不到不能拖垮追问；教练只少一点背景。
            print(f"[SingLens] ⚠️ 追问读取画像失败（{profile_sid}）：{e}")
    try:
        out = await asyncio.to_thread(
            coach_agent.chat, rep, message,
            history if isinstance(history, list) else [],
            focus if isinstance(focus, dict) else None,
            profile_brief)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 追问教练失败：{e}")
        return fail("INTERNAL", "教练暂时回答不了，请稍后再试。", 500)
    return ok(out)


# ══════════════════════════════════════════════════════════════
# AI 人声试听（G43，2026-10-19）
# ══════════════════════════════════════════════════════════════
# 前端「AI 唱」按钮 → 这里取这一句的参考音/歌词 → spawn `.venv_svs`
# 里的 DiffSinger 渲染 → 按句缓存 WAV。服务端不加载模型、不 import torch。
def _ai_demo_for_session(uid: str) -> dict | None:
    """取这次报告「下一遍」那一句的歌词 + 参考音；取不到返回 None。"""
    sess = SESSIONS.get(uid)
    rep = None
    if sess is not None and sess.report:
        rep = sess.report
    else:
        try:
            from web import practice_store
            rep = practice_store.load_report(uid)
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ AI 人声取报告失败（{uid}）：{e}")
            rep = None
    if not isinstance(rep, dict):
        return None
    rep = _ensure_practice_context(rep)
    card = rep.get("practice_card") or {}
    ctx = card.get("context") or {}
    line = str(ctx.get("line") or "").strip()
    line_from = ctx.get("line_from")
    line_to = ctx.get("line_to")
    rid = str((rep.get("meta") or {}).get("reference_id") or "")
    if not line or not isinstance(line_from, int) or not isinstance(line_to, int):
        return None
    if rid not in BUILTIN:
        return None
    ref = load_builtin(rid, first_start_ms=0)
    notes = [n for n in ref.notes if line_from <= n.index <= line_to]
    if len(notes) < 2:
        return None
    return {"line": line, "notes": notes, "reference_id": rid}


def _render_ai_wav(voice: str, line: str, notes: list, out: str) -> None:
    cfg = AI_VOICES[voice]
    midis = [int(round(n.pitch_midi)) + int(cfg["transpose"]) for n in notes]
    cmd = [
        AI_PY, cfg["script"],
        "--voicebank", cfg["voicebank"],
        "--lyrics", line,
        "--notes", ",".join(str(m) for m in midis),
        "--note-ms", "500",
        "--steps", "32" if voice == "female" else "20",
        "--trim", "--normalize",
        "--out", out,
    ]
    if voice == "female":
        cmd += ["--vocoder", cfg["vocoder"], "--dur-mode", "model"]
    else:
        cmd += ["--speaker", "0"]
    r = subprocess.run(cmd, cwd=AI_ROOT, capture_output=True,
                       timeout=180, check=False)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace")[-600:]
        raise RuntimeError(f"DiffSinger 渲染失败（exit {r.returncode}）：{tail}")


@app.get("/api/ai-sing")
async def ai_sing(uid: str = "", singer_id: str = "", voice: str = ""):
    """这一句的 AI 人声试听。返回 audio/wav；同一句/同一音色会命中缓存。"""
    uid = uid.strip()
    if not uid:
        return fail("BAD_REQUEST", "缺少 session_uid。")
    _assert_session_owner(uid, singer_id)
    v = (voice or (CONFIG.get("feedback") or {}).get("ai_voice") or "female").strip()
    if v not in AI_VOICES:
        return fail("BAD_REQUEST", f"未知 AI 人声：{v}（可选 female / male）")
    demo = _ai_demo_for_session(uid)
    if demo is None:
        return fail("BAD_REQUEST",
                    "这次报告没有可用的句子上下文，暂时不能 AI 唱。", 400)
    cfg = AI_VOICES[v]
    notes = demo["notes"]
    key = hashlib.sha1(
        (f"{v}|{demo['line']}|{demo['reference_id']}|"
         + ",".join(f"{n.pitch_midi:.0f}" for n in notes)).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    out = os.path.join(AI_CACHE_DIR, f"{v}_{key}.wav")
    if not os.path.isfile(out):
        os.makedirs(AI_CACHE_DIR, exist_ok=True)
        try:
            # 渲染 5–6 秒且吃 CPU：串行，避免两个请求同时把机器打满。
            # ⚠️ 必须 to_thread：这是 async 端点，直接在事件循环里 subprocess.run
            #    会把整个服务卡住 5–6 秒（AI 唱时其它请求全排队）。
            with _AI_LOCK:
                if not os.path.isfile(out):
                    await asyncio.to_thread(
                        _render_ai_wav, v, demo["line"], notes, out)
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ AI 人声渲染失败（{v} / {demo['line']}）：{e}")
            return fail("AI_RENDER_FAILED",
                        f"AI 人声这次没合成出来：{e}", 503)
    return FileResponse(
        out, media_type="audio/wav",
        headers={
            # ⚠️ HTTP header 只能 latin-1；中文标签/署名不要塞进 header，
            #    由前端按 voice 显示（X-AI-Label 曾直接 500）。
            "X-AI-Voice": v,
            "Cache-Control": "public, max-age=86400",
        })


def _live_audio_meta(uid: str, rep: dict) -> dict:
    """★ 把报告里**缓存的** `audio_available` 换成**现在**的实况。

    为什么必须这么做：报告是**落库**的（`practice_session.report_json`），
    里面的 `meta` 是**当时**的快照。而录音文件可能后来被
      · 保留预算淘汰（`enforce_session_audio_budget`）、
      · 「清空所有录音与记录」删掉、
      · 或压根没落盘成功。
    ⇒ 缓存说"有"、文件其实没了 ⇒ 报告页显示「听这一段」，点下去 404。
      那正是本项目写过两次的「按下去没反应的按钮比没有按钮更坏」。

    ⚠️ 这里**只改内存里的这个 dict**（`ensure_summary` 返回的就是它），
       不写回库 —— 下次再取时按当时的实况再算一遍，永远准。
    """
    if not isinstance(rep, dict):
        return rep
    meta = rep.get("meta")
    if not isinstance(meta, dict):
        return rep
    avail, why = session_audio_state(uid)
    meta["audio_available"] = bool(avail)
    if not avail:
        meta["audio_reason"] = why
    elif meta.get("audio_reason"):
        meta["audio_reason"] = ""
    return rep


# ══════════════════════════════════════════════════════════════
# ⑤b GET /api/session/{uid}/audio?from_ms=&to_ms=  —— 取这一遍的一小段（G13）
# ══════════════════════════════════════════════════════════════
@app.get("/api/session/{uid}/audio")
async def session_audio(uid: str, from_ms: int = 0, to_ms: int = 0,
                        singer_id: str = ""):
    """把这一遍录音的 `[from_ms, to_ms)` 切出来，返回 **wav 字节**。

    为什么要有它：报告页能说"第 3 句偏低"，但用户**听不到那一段** ——
    于是"知道哪里不好"和"把它练好"之间断了一截。这个端点接上那一截。

    ⚠️ **`singer_id` 是必填的**（比 `report` 更严）。两个理由：
      ① `<audio src="...">` **发不出自定义请求头**，所以令牌那套在这里用不上，
         身份只能走查询串 —— 那就更该要求它真的带上，而不是可选；
      ② 这个端点吐的是**用户的裸声音**，比一份分数更敏感。
    归属校验复用 `_assert_session_owner`（不属于 → 403，不是 404：
    "不存在"和"不是你的"是两件事，前端该让用户做的事完全不同）。

    ⚠️ 时间轴口径：`from_ms/to_ms` 是**分析时间轴**（与报告里显示的
    秒数、`problems[].start_ms` 同一套）。实时管线丢过开头时，
    这个轴从"开始分析的位置"算起 —— 报告页本来就在这条轴上显示时间，
    所以两边一致；`meta.truncated_at_ms` 是那条说明。
    """
    singer_id = (singer_id or "").strip()
    if not singer_id:
        return fail("BAD_REQUEST", "缺少 singer_id。", 400)
    _assert_session_owner(uid, singer_id)

    y, sr = _read_session_audio(uid)
    if y is None or not len(y):
        return fail("NO_AUDIO",
                    "这一遍的录音没有留存（可能唱得太短、或这次会话早于"
                    "「逐段复听」功能上线）—— 再唱一遍就有了。", 404)

    total_ms = int(len(y) / sr * 1000)
    a = max(0, int(from_ms or 0))
    b = int(to_ms or 0)
    if b <= 0 or b > total_ms:
        b = total_ms
    if b <= a:
        return fail("BAD_REQUEST",
                    f"时间区间不合法（{a}–{b} ms，而这一遍只有 {total_ms} ms）。", 400)
    if b - a > AUDIO_SEGMENT_MAX_MS:
        b = a + AUDIO_SEGMENT_MAX_MS

    i0, i1 = int(a / 1000 * sr), int(b / 1000 * sr)
    seg = np.clip(np.asarray(y[i0:i1], dtype="float32"), -1, 1)

    import io as _io
    import soundfile as sf
    buf = _io.BytesIO()
    sf.write(buf, seg, sr, format="WAV", subtype="PCM_16")
    return Response(
        content=buf.getvalue(), media_type="audio/wav",
        headers={
            # ⚠️ `no-store`：这是**用户的裸声音**，不能让浏览器/中间层缓存下来
            #   （清空数据之后缓存里还留着一份，正是隐私承诺最怕的那种残留）。
            "Cache-Control": "no-store",
            "X-Audio-From-Ms": str(a), "X-Audio-To-Ms": str(b),
        })


# ══════════════════════════════════════════════════════════════
# ⑤c DELETE /api/session/{uid}  —— 删掉**某一次**练习（审计发现缺这个）
# ══════════════════════════════════════════════════════════════
@app.delete("/api/session/{uid}")
async def delete_session_endpoint(uid: str, singer_id: str = ""):
    """删掉这一次练习：**库里那一行 + 它的录音 + 内存里的会话**。

    ★ 为什么加（2026-10-15）：原来 `DELETE` 只有 `singer` / `template` / `llm_key`
      三个 —— **没有"删掉某一次练习"**。用户攒了几十次记录之后，
      能做的只有 `purge`（**清空全部**）；那对一个真实用户不是可接受的选择，
      他只想删掉唱砸的那一次。

    ⚠️ **不动的东西要如实说**（`exp_*` 研究数据、演唱者身份）——
      悄悄留着比不提供这个功能更坏（与 `purge` / `delete_singer` 同一条规矩）。
    """
    singer_id = (singer_id or "").strip()
    if not singer_id:
        return fail("BAD_REQUEST",
                    "缺少 singer_id —— 删记录是破坏性操作，必须说清是谁的。", 400)
    _assert_session_owner(uid, singer_id)

    from web import practice_store
    if practice_store.session_owner(uid) is None:
        # 库里没有，但内存里/磁盘上可能还留着（例如没算完就刷新了）
        sess = SESSIONS.get(uid)
        if sess is None and not (session_audio_path(uid)
                                 and os.path.exists(session_audio_path(uid))):
            return fail("NOT_FOUND", f"没有这次练习（{uid}）。", 404)
        db = {"deleted": False, "templates_deleted": 0}
    else:
        db = practice_store.delete_session(uid)

    # 录音（文件名就是 uid ⇒ **精确删**，不是猜）
    audio_deleted = 0
    p = session_audio_path(uid)
    if p and os.path.exists(p):
        try:
            os.remove(p)
            audio_deleted = 1
        except OSError as e:
            print(f"[SingLens] ⚠️ 删会话录音失败（{uid}）：{e}")

    # 内存里的会话：丢掉，但**先把它标记成"记录已删"**，
    # 否则前端下一步去取报告会拿到内存里那份（而库里已经没有了 —— 前后不一致）
    SESSIONS.pop(uid, None)
    SINGERS.pop(singer_id, None)          # 档案缓存会过期（少了一条），清掉重算

    _refresh_privacy()
    return ok({
        "deleted": bool(db.get("deleted") or audio_deleted),
        "session_uid": uid,
        "templates_deleted": db.get("templates_deleted", 0),
        "audio_files_deleted": audio_deleted,
        # 如实写清**没删**什么
        "kept": ("实验数据（`exp_*` 表：演唱者元数据、构造式样本、盲评打分）"
                 "和你的演唱者身份都**没有**动 —— "
                 "删的只是这一次记录。"),
    })


# ══════════════════════════════════════════════════════════════
# ⑥ GET /api/session/{uid}/compare
# ══════════════════════════════════════════════════════════════
@app.get("/api/session/{uid}/compare")
async def compare(uid: str, singer_id: str = ""):
    """复练对比。基准那一次**从库里找**（G3，§58.1②）。

    ★ 改了两处，都是"库是唯一真相"的直接后果：
      ① 找"上一次"：原来是 `SINGERS.get(uid)`（内存），重启后内存里
         只剩启动时读回的那一批 —— 而现在一律查库，重启前后一致。
      ② 取"上一次的完整报告"：原来要求 `base_uid in SESSIONS`（内存），
         不在就报 404。服务重启后所有历史会话都不在内存里 ⇒ **复练对比
         在重启后永远是"没有可以对比的上一次练习"**，而用户明明练过。
         现在内存里没有就读库（`load_report` 读 `report_json`）。
      ⚠️ 顺带加了一道**归属校验**：不能拿别人的那一次来比。多用户之后
         "上一次"如果跨了人，比出来的差值毫无意义，而界面上看不出是跟谁比。

    ★ 2026-10-01 补：上面那道校验只挡了"**基准**是不是别人"，
      **没挡"这次会话本身是不是别人的"**。而前端"当前演唱者"在 localStorage、
      "当前会话"在 sessionStorage，两者可以不同步 ⇒ 拿上一个人的 uid 进来，
      就会看到上一个人的报告。现在两端都校验（`_assert_session_owner`）。
    """
    _assert_session_owner(uid, singer_id)
    cur = get_session_or_db(uid)
    base_uid = cur.retry_of_uid
    if not base_uid:
        # 没指定就找同一歌手、**同一参考曲目**的上一次（库，按时间升序 → 取最后一条）。
        # ⚠️ 必须过滤 reference_id（2026-10-18 审查）：
        #    原来只按 singer 取"上一次"，于是先唱《小星星》再唱《欢乐颂》，
        #    对比页会把两首不同的歌摆在一起算"进步/退步" —— 数字全无意义，
        #    而页面上完全看不出比的是另一首。
        ss = practice_store.sessions_of(cur.singer_id, 2000)
        prev = [s for s in ss
                if s["session_uid"] != uid
                and (s.get("reference_id") or None) == (cur.reference_id or None)]
        base_uid = prev[-1]["session_uid"] if prev else None
        if not base_uid:
            return fail("NOT_COMPARABLE",
                        "没有找到同一首曲目的上一次练习。"
                        "复练对比只能比同一首 —— 换个参考唱的不能直接比。", 409)
    owner = practice_store.session_owner(base_uid)
    if owner is None and base_uid not in SESSIONS:
        return fail("NOT_FOUND", "没有可以对比的上一次练习（它可能已经被删除）。", 404)
    if owner and cur.singer_id and owner != cur.singer_id:
        return fail("FORBIDDEN",
                    f"上一次练习属于 {owner}，不是 {cur.singer_id} —— "
                    f"不能拿另一个人的记录来比。", 403)
    # 基准报告：内存里有就用（快），没有就读库（重启后仍能对比）
    base_report = SESSIONS[base_uid].report if base_uid in SESSIONS else None
    if base_report is None:
        base_report = practice_store.load_report(base_uid)
    if base_report is None or cur.report is None:
        # ⚠️ 这里是【状态未就绪】，不是"资源不存在" —— 资源明明在，只是还没算完。
        #    原来报 NOT_FOUND（409 配 NOT_FOUND），语义自相矛盾：
        #    UI 收到 NOT_FOUND 会当成"这条会话不存在"，而它其实存在、只是要等。
        #    契约 v1.1 §3.4 冻结了通用错误码，这里应报 CONFLICT。
        return fail("CONFLICT", "两次报告都准备好之后才能对比。", 409)

    # ⚠️ 显式 retry_of_uid 也可能指到另一首（例如手改会话指针 / 旧数据）。
    #    这里再兜一道：只要两份报告的 meta.reference_id 不同，就不比。
    cur_ref = ((cur.report.get("meta") or {}).get("reference_id")
               or cur.reference_id or None)
    base_ref = ((base_report.get("meta") or {}).get("reference_id") or None)
    if base_ref and cur_ref and base_ref != cur_ref:
        return fail("NOT_COMPARABLE",
                    f"上一次练的是 {base_ref}，这一遍是 {cur_ref} —— "
                    f"两首不同，不能直接比较。", 409)

    def diff(a, b):
        return None if (a is None or b is None) else round(b - a, 1)

    metrics = ["dynamic_range", "phrase_dynamics", "rubato", "vibrato_rate",
               "vibrato_depth", "onset_style", "brightness"]
    # ⚠️ 这里发的是**完整报告**，不是摘要。
    #    踩过的坑（2026-09-23）：原来只发 {session_uid, created_at, total, expressiveness}，
    #    而 UI 契约 §2.2 说 base/retry 是两份 ScoreReport —— 少的是 pitch / rhythm /
    #    evidence / meta，而 `expressiveness` 还退化成原始值的字典（没有 .score）。
    #    后果不是报错，是**半真半假的界面**：抬头换成了"第一次 / 按建议练过之后"，
    #    三行对比数字却还是原型稿里的 74 → 88。混合态比全空更难发现。
    #    完整报告本来就在内存里，直接发，前端不用再查两次（少两次口径漂移的机会）。
    return ok({
        "base": base_report,
        "current": cur.report,
        "base_uid": base_uid,
        "delta": {
            "total": diff(base_report["total"], cur.report["total"]),
            "pitch": diff(base_report["pitch"]["score"], cur.report["pitch"]["score"]),
            "rhythm": diff(base_report["rhythm"]["score"], cur.report["rhythm"]["score"]),
            "expr": diff(base_report["expressiveness"]["score"],
                         cur.report["expressiveness"]["score"]),
            "metrics": {k: diff(base_report["expressiveness"].get(k),
                                cur.report["expressiveness"].get(k)) for k in metrics},
        },
    })


# ══════════════════════════════════════════════════════════════
# ⑦⑧⑨ /api/singer/{id}/*
# ══════════════════════════════════════════════════════════════
# ★ 分页参数一律**显式钳制**（2026-09-24，审计 §5.3）。
#   原来 `limit: int = 50` 没有任何边界：
#     · `limit=-1` → `[: -1]` **少返回最后一条**（用户以为记录丢了）
#     · 传进 SQL 的 `LIMIT -1` → SQLite 语义是**不限量**（一次全表读）
#   两者的共同点是"都不报错" —— 一个少了数据、一个多了负载，
#   而日志里看不出任何异常。所以钳制要放在**能钳的地方**：
#   HTTP 参数用 FastAPI 的 Query(ge/le)（拼错会给 422），
#   而 store 层再钳一次（脚本/内部调用绕过 HTTP 时同样安全）。
LIMIT_DEFAULT = 50
LIMIT_MAX = 200


def _clamp_limit(v: int, default: int = LIMIT_DEFAULT, hi: int = LIMIT_MAX) -> int:
    """把分页参数收进 [1, hi]。

    ⚠️ **非正数一律回落到默认值**，不是钳成 1（2026-09-24 实测发现的差别）：
       第一版写 `max(1, min(hi, n))`，于是 `limit=-1` 变成"只返回 1 条" ——
       比原来的少返回一条**更糟**：调用方以为拿到了一页，其实只有一条。
       `limit<=0` 的意图从来不是"给我一条"，而是"参数给错了/想全要"，
       两种解释下**给默认值都是最安全的**（不丢数据、也不全表读）。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(hi, n)


@app.get("/api/singer/{sid}/history")
async def history(sid: str, limit: int = LIMIT_DEFAULT):
    """某个演唱者的练习历史。**从库里查**，不再读内存。

    ★ G3（§58.1②）改的两处：
      ① 数据来源：`SINGERS`（内存）→ `practice_store.sessions_of()`（库）。
         以前这里虽然写了读库回退，但**只在内存里没有这个人时**才回退；
         内存里有、库里更多（或反过来）时，读到的是内存那一份 ——
         而两个源不一致的表现是"重启后少了/多了几条"，没人查得出来。
      ② 演唱者不存在 → **404**，不是空数组。见 `_require_singer`。
    """
    _require_singer(sid)
    ss = practice_store.sessions_of(sid, 2000)
    ss.sort(key=lambda s: s["created_at"], reverse=True)
    ss = ss[: _clamp_limit(limit)]
    out = []
    for s in ss:
        d = {k: v for k, v in s.items() if k != "expressiveness"}
        # ★ created_at 必须是**可读的 ISO 串**，不能把内部用的 epoch 浮点直接发出去。
        #   踩过的坑：这里原来发的是 `1790130304.4616742` ——
        #   mock 源发的是 '2026-09-14'，api 源发浮点，两者形状不同 →
        #   前端按 YYYY-MM-DD 切片时切出 `23` 这种鬼东西（截图才看见）。
        #   "数值对但形状不对"是最难查的一类：探针只查元素非空，它照样通过。
        d["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(s["created_at"]))
        out.append(d)
    return ok({"singer_id": sid, "count": len(out), "sessions": out})


@app.get("/api/singer/{sid}/progress")
async def progress(sid: str):
    """进步曲线。**从库里查**（G3 之前只读内存 ⇒ 重启后这条线是空的）。"""
    _require_singer(sid)
    ss = practice_store.sessions_of(sid, 2000)      # 已按时间升序
    if not ss:
        # 注意：这里是**"这个人存在但还没练过"**，与"没有这个演唱者"（404）
        # 是两件事，界面上也必须说成两句不同的话。
        return ok({"singer_id": sid, "points": 0, "labels": [], "series": {}})
    metrics = ["dynamic_range", "phrase_dynamics", "rubato", "vibrato_rate",
               "vibrato_depth", "onset_style", "brightness"]

    def col(fn):
        out = []
        for s in ss:
            v = fn(s)
            out.append(None if v is None else round(float(v), 2))
        return out

    return ok({
        "singer_id": sid, "points": len(ss),
        # labels 是给图表 x 轴用的日期串；t 是原始时间戳，两个都留。
        # ★ 2026-10-20：同一天练很多次时，只显示"10-19"会得到一排相同的刻度。
        #   再加一列 `times`（HH:MM），前端在"同一天"的情况下用它做刻度。
        "labels": [time.strftime("%m-%d", time.localtime(s["created_at"])) for s in ss],
        "times": [time.strftime("%H:%M", time.localtime(s["created_at"])) for s in ss],
        "series": {
            "t": [s["created_at"] for s in ss],
            # ★ 每一次用的是哪个参考。**趋势必须能分辨"换了首歌"** ——
            #   模式 A 的《小星星》(~63.8) 与模式 B 的自建模板(33.1) 分数本来就
            #   不可比，混在一条曲线上会得出"你退步了 31 分"这种假结论。
            #   前端靠这一列在参考变化处断开，而不是把两段连起来。
            "reference_id": [s.get("reference_id") for s in ss],
            # ★ 2026-10-20：曲名必须跟着来。原来只有 tpl_xxxx 这种内部 id，
            #   前端只能显示"REF_SELF"—— 用户看不懂自己练的是哪首。
            "reference_title": [s.get("reference_title") for s in ss],
            "mode": [s.get("mode") for s in ss],
            "expr": col(lambda s: s["expr"]),
            "total": col(lambda s: s["total"]),
            "pitch": col(lambda s: s["pitch"]),
            "rhythm": col(lambda s: s["rhythm"]),
            # ★ 契约要求：null 表示本次未评估，**不能补 0**
            #   前端画折线时必须在这里断开
            **{k: col(lambda s, k=k: (s.get("expressiveness") or {}).get(k))
               for k in metrics},
        },
        "excluded_counts": {k: sum(1 for s in ss if k in (s.get("metrics_excluded") or []))
                            for k in metrics},
    })


@app.get("/api/singer/{sid}/profile")
async def profile(sid: str):
    """演唱特点画像。**从库里查**。

    ⚠️ G3 顺带修掉一个一直没暴露的缺陷：六维归一化值（`normalized`）
       以前只存在于**内存里的报告对象**中，`_row_to_session` 从冗余列
       拼出来的 `expressiveness` 没有它 —— 也就是说**服务一重启，
       第 05 屏"强项与短板"的六根横条全部变成「—」**。
       以前没暴露是因为这个接口只读内存（重启后本来就空）。
       现在改成读库，就必须有 `normalized_json` 那一列（见 practice_store）。
    """
    _require_singer(sid)
    ss = practice_store.sessions_of(sid, 2000)
    if not ss:
        return ok({"singer_id": sid, "sessions": 0, "profile": None})

    def med(vals):
        v = [x for x in vals if x is not None]
        return round(float(np.median(v)), 2) if v else None

    dr = med([(s.get("expressiveness") or {}).get("dynamic_range") for s in ss])
    br = med([(s.get("expressiveness") or {}).get("brightness") for s in ss])

    # ★ 六维的**归一化中位数**（0–1）—— 第 05 屏"强项与短板"的横条要的就是它。
    #   为什么不把 NORMALIZATION 的区间抄到前端去算：
    #   那就等于同一个公式有两个副本，改一处漏一处，而漏掉时**不会报错**，
    #   只会让界面上的横条与报告里的分数悄悄对不上。
    #   这里直接取 `expressiveness.normalized` —— 那是评分器**当时用的值**，
    #   天然与报告同源。
    #   ⚠️ 某项为 null 表示"从没评估过"，前端必须显示「—」，不能画成 0 长的条。
    def nmed(key):
        v = [(s.get("expressiveness") or {}).get("normalized", {}).get(key)
             for s in ss]
        v = [float(x) for x in v if x is not None]
        return round(float(np.median(v)), 4) if v else None

    norm_med = {k: nmed(k) for k in
                ("dynamic_range", "phrase_dynamics", "rubato",
                 "vibrato", "onset_style", "brightness")}
    # 音准/节奏不在这六维里（它们是两大分量，不是表现力指标），单独给
    norm_med["pitch"] = med([s["pitch"] for s in ss])
    norm_med["rhythm"] = med([s["rhythm"] for s in ss])

    n_sess = len(ss)
    excluded_counts = {}
    for k in ("dynamic_range", "phrase_dynamics", "rubato",
              "vibrato", "onset_style", "brightness"):
        c = sum(1 for s in ss if k in (s.get("metrics_excluded") or []))
        if c:
            excluded_counts[k] = c

    return ok({
        "singer_id": sid, "sessions": n_sess,
        "avg_total": med([s["total"] for s in ss]),
        "normalized": norm_med,
        "excluded_counts": excluded_counts,
        "profile": {
            "dynamic_range": dr, "brightness": br,
            "style": _style_label(dr, br),
            "vibrato_rate": med([(s.get("expressiveness") or {}).get("vibrato_rate")
                                 for s in ss]),
            "onset_style": med([(s.get("expressiveness") or {}).get("onset_style")
                                for s in ss]),
        },
    })


def _style_label(dr, br):
    parts = []
    parts.append("力度起伏丰富" if (dr or 0) >= 12 else "力度比较平")
    parts.append("音色偏亮" if (br or 0) >= 1400 else "音色偏暖")
    return "，".join(parts)


# ────────────────────────────────────────────────────────────
# 用户画像 P0（2026-10-26）
# ────────────────────────────────────────────────────────────
def _portrait_for(sid: str, limit: int = 500) -> dict:
    """从库里已有的练习报告聚合画像。

    ⚠️ 为什么这里要 load_report：`sessions_of` 的历史行只有分数/六指标，
       `pitch.accuracy`、`pitch.stability`、`rhythm.tempo_ratio` 在完整
       报告里；画像需要“偏低多少/稳不稳”，不能只看一个音准分。
       只做一次只读聚合，不重新分析音频。
    """
    ss = practice_store.sessions_of(sid, min(500, max(1, int(limit))))
    reports = []
    for s in ss:
        rep = practice_store.load_report(s.get("session_uid")) or {}
        if not isinstance(rep, dict) or not rep:
            continue
        # 历史摘要里的时间/曲名比报告更可靠（旧报告可能没存 meta.created_at）
        rep.setdefault("created_at", s.get("created_at"))
        rep.setdefault("reference_title", s.get("reference_title"))
        reports.append(rep)
    prof = practice_store.get_profile(sid)
    out = portrait_mod.build_portrait(reports, prof)
    out["singer_id"] = sid
    return out


@app.get("/api/singer/{sid}/portrait")
async def portrait_endpoint(sid: str):
    """用户画像：自述偏好 + 练习中稳定出现的倾向。"""
    _require_singer(sid)
    return ok(_portrait_for(sid))


@app.post("/api/singer/{sid}/portrait/narrative")
async def portrait_narrative_endpoint(sid: str):
    """显式让 V 写一段画像；失败/无 key 退回规则版，绝不自动调用。"""
    _require_singer(sid)
    p = _portrait_for(sid)
    return ok(portrait_narrative_mod.build(p))


@app.get("/api/singer/{sid}/recommendations")
async def recommendations_endpoint(sid: str):
    """按舒适音区/偏好推荐本机已有曲目；不按历史分数排名。"""
    _require_singer(sid)
    prof = practice_store.get_profile(sid)
    p = _portrait_for(sid, limit=120)
    comfort = p.get("comfort") or prof.get("comfort")
    stated = dict(prof)
    stated["top_song"] = ((p.get("observed") or {}).get("habit") or {}).get("top_song") or ""
    candidates = []
    for rid in BUILTIN:
        try:
            ref = load_builtin(rid, first_start_ms=0)
            candidates.append({
                "kind": "builtin", "id": rid, "title": ref.title,
                "notes": [{"midi": n.pitch_midi} for n in ref.notes],
                "duration_ms": (ref.notes[-1].end_ms if ref.notes else 0),
            })
        except Exception:  # noqa: BLE001
            continue
    try:
        for t in templates.list_templates(sid, 50):
            title = str(t.get("title") or "").strip()
            if not title or title.startswith("微练习"):
                continue
            try:
                tpl = templates.load(t.get("template_id"))
            except Exception:  # noqa: BLE001
                tpl = None
            if not tpl or not tpl.notes:
                continue
            candidates.append({
                "kind": "template", "id": t.get("template_id"),
                "title": title,
                "source_kind": (t.get("source_kind") or tpl.source_kind or ""),
                "notes": [{"midi": n.get("pitch_midi")} for n in tpl.notes],
                "duration_ms": t.get("duration_ms"),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 读取模板推荐失败：{e}")
    rec = recommender_mod.recommend(comfort, stated, candidates)
    rec["singer_id"] = sid
    return ok(rec)


@app.put("/api/singer/{sid}/profile")
async def save_profile_endpoint(sid: str, req: Request):
    """保存用户自述字段。部分更新：只改请求里出现的字段。"""
    _require_singer(sid)
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是对象。")
    fields = {}
    if "goal" in body:
        fields["goal"] = str(body.get("goal") or "")[:120]
    if "experience" in body:
        fields["experience"] = str(body.get("experience") or "")[:40]
    for key in ("preferred_style", "dislikes", "favorite_songs"):
        if key in body:
            if not isinstance(body[key], list):
                return fail("BAD_REQUEST", f"{key} 必须是数组。")
            fields[key] = [str(x).strip()[:40] for x in body[key]
                           if str(x).strip()][:12]
    saved = practice_store.save_profile(sid, fields)
    return ok(saved)


@app.post("/api/singer/{sid}/profile/note")
async def add_profile_note_endpoint(sid: str, req: Request):
    """显式“记住这条”——只追加用户点击保存的短句。"""
    _require_singer(sid)
    body = await _json(req)
    text = str((body or {}).get("text") or "").strip()[:160]
    if not text:
        return fail("BAD_REQUEST", "要记住的内容不能为空。")
    return ok(practice_store.append_profile_note(sid, text))


@app.post("/api/singer/{sid}/profile/comfort")
async def save_comfort_endpoint(sid: str, req: Request):
    """保存舒适音区自测（主观；不录音、不分析音频）。"""
    _require_singer(sid)
    body = await _json(req)
    notes = (body or {}).get("notes")
    if not isinstance(notes, list) or not notes:
        return fail("BAD_REQUEST", "notes 必须是非空数组。")
    if len(notes) > 20:
        return fail("BAD_REQUEST", "一次最多提交 20 个音。")
    result = comfort_mod.build_comfort(notes)
    if result["tested_count"] < 1:
        return fail("BAD_REQUEST", "没有有效的音高/舒适度数据。")
    saved = practice_store.save_comfort(sid, result)
    return ok(saved)


@app.delete("/api/singer/{sid}/profile/comfort")
async def clear_comfort_endpoint(sid: str):
    _require_singer(sid)
    return ok(practice_store.save_comfort(sid, None))


@app.delete("/api/singer/{sid}/profile/note/{index}")
async def delete_profile_note_endpoint(sid: str, index: int):
    _require_singer(sid)
    return ok(practice_store.delete_profile_note(sid, index))


# ══════════════════════════════════════════════════════════════
# ⑯ 演唱者名册 —— G3 多用户（2026-10-01）
# ══════════════════════════════════════════════════════════════
# ★ 这一节回答的问题不是"能不能换人"，而是**"屏幕上的数据是谁的"**。
#   改之前：前端 `bootstrap()` 写死返回一个人（S01），界面上没有任何换人入口，
#   于是两个人的记录会串在一起；而 `/api/singer/{sid}/*` 查不到人时返回
#   **空数组** —— 用户把 id 打错一个字，看到的是一句温和的"你还没练过"。
#   **空态与查错人长得一模一样**，这是本项目反复出现的同一类故障
#   （§29 误判、§54 空档案、§61 静默失效）。
def _require_singer(sid: str) -> dict:
    """确认这位演唱者真的存在；不存在就 **404**（不是空数组）。

    ⚠️ 为什么必须是 404：`{"count": 0, "sessions": []}` 这句话有两个含义 ——
       "这个人还没练过" 和 "没有这个人"。界面上它们长得完全一样，
       而用户唯一能做的判断就是"我的记录没了"。
       现在这两种情况**在状态码上就分开了**（404 vs 200 空列表），
       前端也就能说成两句不同的话。
    """
    s = practice_store.get_singer(sid)
    if s is None:
        raise HTTPException(status_code=404, detail={
            "code": "NOT_FOUND",
            "message": f"没有演唱者 {sid}。先看 GET /api/singer 里有哪些人 —— "
                       f"可能是切换到了别人的身份，或者这位已经被删除了。"})
    return s


def _assert_session_owner(uid: str, singer_id: Optional[str]) -> None:
    """给了 `singer_id` 就校验这次会话属不属于他；不属于 → 403。

    ★ 为什么加（2026-10-01，G3 收尾时发现的缺口）：

    前端把"当前演唱者"存在 localStorage，把"当前会话"存在 sessionStorage。
    两者**可以不同步** —— 换人会清 `sl.sess`，但下面这条路它盖不住：

        A 练完（`sl.sess = A 的会话`）→ 关掉标签页
        → 换 B 打开（localStorage 里还是 A？不是，换过就是 B）
        → 或者直接打开带 `?sess=<A 的会话>` 的链接

    这时前端会拿着 **A 的会话 uid** 去问"我和上一次比怎么样"，
    而它属于 A。后果有两层：
      · 至少是一次**必然 404 的多余请求**（浏览器控制台一条红字）；
      · 更糟的情况：那个会话真的存在 → **B 看到 A 的报告/对比**。
        这种"看得到但看不出来是谁的"是本项目最忌讳的一类错。

    没给 `singer_id` 时**保持不变**（老调用方不受影响）—— 这层校验是"调用方
    自报身份"级别的，不是鉴权（真正的访问控制见接口契约 §8.1 的残余风险）。
    """
    if not singer_id:
        return
    try:
        owner = practice_store.session_owner(uid)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 查会话归属失败（{uid}）：{e}")
        return                      # 查不动不该把功能堵死，但这层就没生效
    if owner is None:
        return                      # 库里没有 → 交给各自的"找不到"分支去报
    if owner != singer_id:
        # ★ 403 而不是 404：两种失败必须分开 ——
        #     "这个会话不存在"（404）和"它存在但不是你的"（403）
        #   混成一个码，前端就只能说一句含糊的话，而这两种情况用户该做的事
        #   完全不同（一个去重唱，一个去检查身份是否被切错）。
        #   语义也与 `compare` 里原有的归属校验保持一致（它报的就是 403）。
        raise HTTPException(status_code=403, detail={
            "code": "FORBIDDEN",
            "message": f"这次练习不属于当前演唱者（它属于 {owner}）—— "
                       f"多半是切换了身份，但页面上还留着上一个人的会话。"})


def _remote_operator_denied(request: Request):
    """本机回环免令牌；非本机请求必须带操作者令牌。

    本应用没有账号体系，不能让“局域网里随手 curl 的人”直接清空数据/
    改设置；但本机设置页也不该突然弹令牌框。这个折中只保护非回环来源；
    真正要账号隔离仍然得做 G3 多用户。
    """
    host = ((request.client.host if request.client else "") or "").lower()
    if host in ("127.0.0.1", "::1", "localhost", "testclient",
                "::ffff:127.0.0.1"):
        return None
    try:
        from web.experiment import require_operator
        return require_operator(request)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 远端操作者校验失败：{e}")
        return fail("UNAUTHORIZED", "无法验证操作者令牌。", 401)


def _singer_out(s: dict) -> dict:
    """名册行的对外形状。`created_at` 与其他接口一致地发 ISO 串。"""
    out = {"singer_id": s.get("singer_id"), "name": s.get("name"),
           "sessions": s.get("sessions"), "templates": s.get("templates")}
    ts = s.get("created_at")
    out["created_at"] = (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
                         if ts else None)
    return out


SINGER_ID_OK = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}$")
SINGER_NAME_MAX = 24


@app.get("/api/singer")
async def list_singers_endpoint(request: Request):
    """演唱者名册 + 每人的练习次数/模板数（前端切换列表的数据源）。

    ★ 登录后普通 singer 只返回自己；admin 才看全量名册。
      UI 的“切换演唱者”因此只对 admin 有意义。

    ⚠️ `cache_sessions` 不是给人看的功能，是一条**自检**：
       它是内存缓存里的练习条数，正常情况下应该等于库里那个数。
       不相等 ⇒ 某次“写穿到库”失败了（`_record_for_singer` 的 except），
       那条记录重启后就没了。把这个数露出来，是因为那种失败**只在服务日志里**
       报一行，而服务日志没人天天看。
    """
    rows = practice_store.list_singers()
    user = getattr(request.state, "user", None)
    if user and user.get("role") != "admin" and user.get("singer_id"):
        rows = [s for s in rows if s.get("singer_id") == user["singer_id"]]
    cached = sum(len(v.get("sessions") or []) for v in SINGERS.values())
    return ok({
        "singers": [_singer_out(s) for s in rows],
        "count": len(rows),
        "total_sessions": sum((s.get("sessions") or 0) for s in rows),
        "cache_sessions": cached,
        "cache_consistent": cached == practice_store.count(),
    })


@app.post("/api/singer")
async def upsert_singer_endpoint(req: Request):
    """新建 / 改名。body: `{"name": "同桌", "singer_id": "S02"?}`

    `singer_id` 不给就自动生成（前端"新建"只让用户填名字 —— 让用户自己
    发明 id 是本末倒置：那是内部标识，写错了就变成另一个人的记录）。

    ★ 登录后：普通 singer 只能改自己的名字，不能新建/改别人；
      admin 才能新建/改名任意一位。
    """
    body = await _json(req)
    name = (body.get("name") or "").strip()
    if not name:
        return fail("BAD_REQUEST", "请给这位演唱者起个名字。")
    if len(name) > SINGER_NAME_MAX:
        return fail("BAD_REQUEST", f"名字太长了（{len(name)} > {SINGER_NAME_MAX} 字）。")
    sid = (body.get("singer_id") or "").strip()
    user = getattr(req.state, "user", None)
    if user and user.get("role") != "admin":
        if not sid or sid != user.get("singer_id"):
            return fail("FORBIDDEN", "普通账号不能新建演唱者，也不能改别人的名字。", 403)
    if sid and not SINGER_ID_OK.match(sid):
        return fail("BAD_REQUEST",
                    "singer_id 只允许中文/字母/数字/下划线/短横线，最长 32 字符。")
    if not sid:
        sid = f"u_{uuid.uuid4().hex[:8]}"
    if practice_store.name_taken(name, sid):
        return fail("BAD_REQUEST",
                    f"已经有一位演唱者叫「{name}」了。名字要能区分开 —— "
                    f"两个同名的人摆在切换列表里，点错就是看到别人的数据。")
    r = practice_store.upsert_singer(sid, name)
    return ok({"created": r["created"], "renamed_from": r["renamed_from"],
               "singer": _singer_out(r["singer"])})


@app.delete("/api/singer/{sid}")
async def delete_singer_endpoint(req: Request, sid: str):
    """删除一位演唱者：他的练习记录 + 跟唱模板 + 名册行。

    ★ **不删录音文件**（§58.3 的倾向，与 §54.4「宁可少删，不能删错」一致）：
      wav 文件名里没有可靠的演唱者信息 —— `practice_session` 根本没存文件路径，
      文件也可能被重命名过。按名字猜着删，猜错就是删掉**别人**的录音，
      而且不可恢复。所以只删库，并在返回里**如实说明**文件还在、
      以及用户想连文件一起清该怎么走（设置页的"清空所有录音与记录"）。
    """
    user = getattr(req.state, "user", None)
    if user and user.get("role") != "admin":
        return fail("FORBIDDEN", "删除演唱者需要管理员账号。", 403)
    s = _require_singer(sid)              # 不存在 → 404（不是"删了 0 条"）
    if practice_store.count_singers() <= 1:
        # 删掉最后一个人 ⇒ 前端无处可去（名册为空时它连"当前是谁"都答不出来）。
        # 这不是洁癖：真删空的后果是整个应用变成"没有演唱者"的死状态，
        # 而用户本来想做的只是"把数据清掉" —— 那件事有专门的入口。
        return fail("CONFLICT",
                    "这是最后一位演唱者。删掉它界面就没有'当前是谁'了 —— "
                    "想清掉练习数据请用设置页的「清空所有录音与记录」，"
                    "或先新建一位再删这一位。", 409)
    r = practice_store.delete_singer(sid)
    # 内存缓存同步丢掉。缓存是只读的旧数据，不清的话
    # `cache_sessions` 会一直报不一致（而那是一条真的告警，不能被假警报淹没）。
    SINGERS.pop(sid, None)
    return ok({
        "deleted": True, "singer_id": sid, "name": s.get("name"),
        **r,
        # ★ 如实回报"没删什么"。悄悄留下录音，比不提供这个功能更坏 ——
        #   用户以为删干净了。（§54.4 同一条规矩。）
        "files_deleted": 0, "bytes_freed": 0,
        "skipped_reason": ("录音文件没有删：文件名里没有可靠的演唱者信息，"
                           "猜错会删掉别人的录音。想连录音一起清，"
                           "请用设置页的「清空所有录音与记录」。"),
        "kept": "实验数据（exp_* 表）未删除。",
        "remaining": {"singers": practice_store.count_singers(),
                      "practice_sessions": practice_store.count()},
    })


# ══════════════════════════════════════════════════════════════
# ⑩ GET /api/experiment/summary
# ══════════════════════════════════════════════════════════════
@app.get("/api/experiment/summary")
async def experiment_summary():
    """实验统计。真人评分数据还没采集，所以现在是空的——
    但**结构必须就位**，否则第 7 节的标定实验没有落点。

    ★ 数字必须来自**实验表**，不能拿练习会话凑（2026-09-24 修）。
      原来 `samples = n_sessions`（= 全部演唱者的练习会话数）。
      后果有两层：
        ① 第 06 屏上"样本 N 条"是**论文要引的数**，而它把
           `RT`/`G4A`/`TPLC`/`TRSP` 这些**测试用演唱者**的会话全算了进去
           （本次验证又添了十几条）——数字虚高，且没人能从界面上看出来；
        ② "评分者 N 人"绑的是 `n_ratings`（评分**条数**），而且恒为 0 ——
           标签与数据不是一个东西，等真人评完就会出现"3 个人打了 200 条"式的错读。
      现在四个数各归其位：参与者/录音/评分者/评分条数，全部来自 `exp_*` 表。
      ⚠️ 练习会话数另给一个字段（`n_practice_sessions`），
         它有意义（产品使用量），但不该冒充实验样本量。
      ⚠️ G3 顺带改了一处：`n_practice_sessions` 原来是
         `sum(len(v["sessions"]) for v in SINGERS.values())` —— **从内存缓存数**。
         缓存读写失败一次，这个数就悄悄少一条（而它是产品使用量，不是论文数，
         所以没人核对）。现在直接问库。
    """
    from analysis.scoring import DEFAULT_WEIGHTS
    n_sess = practice_store.count()
    weights = [{"metric": k, "weight": v, "is_placeholder": True}
               for k, v in DEFAULT_WEIGHTS.items()]
    # ── 实验表里的真实计数 ──
    n_singers = n_samples = n_raters = n_ratings = 0
    pairs: list[tuple[float, float]] = []
    exp_err = None
    try:
        from web import experiment as _exp
        con = _exp.connect()
        try:
            n_singers = con.execute("SELECT COUNT(*) c FROM exp_singer").fetchone()["c"]
            n_samples = con.execute("SELECT COUNT(*) c FROM exp_recording").fetchone()["c"]
            n_raters = con.execute(
                "SELECT COUNT(DISTINCT rater_id) c FROM exp_rating").fetchone()["c"]
            n_ratings = con.execute("SELECT COUNT(*) c FROM exp_rating").fetchone()["c"]
            # ★ 2026-10-18：把已有的盲评数据**现场配对**，不再写死 rho=null。
            #   原来 `spec_rho = None` 挂着"采集后由 analyze_experiment 落库"，
            #   但 A3 采完 10 条 × 2 位评分者之后，落库这一步从来没写；
            #   于是页面永远显示"待验证"，与论文的 ρ 打架。
            #   这里用同一条 SQL 直接算：每条录音的评分者均分 × 系统表现力分。
            rows = con.execute(
                "SELECT r.recording_id, r.expr_score, AVG(t.score) AS human "
                "FROM exp_recording r JOIN exp_rating t "
                "  ON t.recording_id = r.recording_id "
                "WHERE r.expr_score IS NOT NULL "
                "GROUP BY r.recording_id").fetchall()
            pairs = [(float(r["human"]), float(r["expr_score"])) for r in rows
                     if r["human"] is not None]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 —— 实验库坏了不该让整个页面挂掉
        exp_err = f"{type(e).__name__}: {e}"
        print(f"[SingLens] ⚠️ 读实验统计失败：{exp_err}")

    # Spearman ρ：系统表现力分 vs 评分者均分（A3 的主结论就是这个数）。
    # 只算两项都有值的录音；样本少时如实显示 n（不拿 0 冒充）。
    rho = None
    if len(pairs) >= 3:
        try:
            from scipy.stats import spearmanr
            stat, _p = spearmanr([p[0] for p in pairs], [p[1] for p in pairs])
            if stat is not None and np.isfinite(stat):
                rho = round(float(stat), 3)
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ 算 Spearman 失败：{e}")
    # 散点图的坐标轴是 1–5（charts.drawScatter 的约定）：
    # x = 评分者均分，y = 系统表现力分 / 20（0–100 → 1–5）。
    scatter = [[round(h, 2), round(expr / 20.0, 2)] for h, expr in pairs]
    stats = {"singers": n_singers, "samples": n_samples,
             "raters": n_raters, "ratings": n_ratings,
             # 兼容旧字段名（DataSource 契约 v1.0 用的是这几个）
             "n_singers": n_singers, "n_sessions": n_samples, "n_ratings": n_ratings}
    if exp_err:
        stats["error"] = "实验库读取失败，下面的数字不完整"

    # ── 验收指标（三条）：前两条是离线基准，第三条是现场算的盲评相关 ──
    # ★ 三条一起给，且**带出处** —— 见 ACCEPTANCE_BENCHMARKS 的说明。
    #   第三条的判据（ρ>0.6）也在这里算：前端只显示，不算公式。
    spec_rho = rho
    benchmarks = _acceptance_benchmarks()
    benchmarks.append({
        "id": "rho", "label": "系统与人评分相关",
        "measured": spec_rho, "unit": "", "threshold": 0.6,
        "cmp_text": "&gt; 0.6", "decimals": 2,
        "measured_text": (f"ρ={spec_rho:.2f}（n={len(pairs)} 条录音）"
                          if spec_rho is not None else None),
        "ok": (spec_rho is not None and spec_rho > 0.6),
        "pending": spec_rho is None,     # ★ 与"没达标"是两回事，必须分开
        "source": "现场计算：exp_rating 的评分者均分 × exp_recording 的 expr_score"
                  "（只算两项都有值的录音；n<3 时不算）",
    })
    n_ok = sum(1 for b in benchmarks if b["ok"])
    n_pending = sum(1 for b in benchmarks if b.get("pending"))

    return ok({
        "weight_version": WEIGHT_VERSION,
        # ★★ 2026-10-17：这个标志**不再由版本号推导**。
        #
        # 原来写的是 `WEIGHT_VERSION == "EQUAL_DEFAULT"` —— 一旦标定过、
        # 版本号改成别的，它就变 False，而前端据此显示
        # 「Ridge 回归，已做 VIF 共线性检验」 —— **那是一句假话**
        # （从来没有跑过回归，也没做过 VIF）。
        # 这正是本项目反复出现的病：**一个标志被当成了"做过某件事"的证据。**
        #
        # ⇒ 现在如实写 True：**权重确实还不是回归结果**
        #   （`DEFAULT_WEIGHTS` 是工程直觉的比例，只是删掉了两项不可信的指标）。
        #   真正的进展记在 `normalization_calibrated` 里，别混为一谈。
        "weights_are_placeholder": True,
        # 新增：**归一化区间**是否按真实素材标定过（这才是 2026-10-17 做的事）
        "normalization_calibrated": True,
        "scored_metrics": ["dynamic_range", "vibrato", "onset_style", "brightness"],
        "dropped_metrics": ["phrase_dynamics", "rubato"],
        "n_singers": n_singers,
        "n_sessions": n_samples,
        "n_ratings": n_ratings,
        "n_practice_sessions": n_sess,       # 产品使用量，**不是**实验样本量
        # DataSource 契约要的字段名（UI 第 06 屏直接用）
        "stats": stats,
        "weights": weights,
        "scatter": scatter,
        "r2": None, "rho": spec_rho,      # 未标定，保持 null（不填 0）
        "spearman": spec_rho,
        "baselines": [],                  # 三组对比基线，见 docs/同类项目调研-* §四
        # ★ 验收指标：界面直接渲染这个数组，不再自己在 JS 里拼字符串
        "benchmarks": benchmarks,
        "benchmarks_summary": {"ok": n_ok, "total": len(benchmarks),
                               "pending": n_pending},
        "note": (f"已采集 {n_ratings} 条评分（{len(pairs)} 条录音有配对）；"
                 f"权重仍为占位值，需第 7 节实验标定后替换。"
                 if n_ratings else
                 "真人主观评分未采集；权重为占位值，需第 7 节实验标定后替换。"),
    })


# ══════════════════════════════════════════════════════════════
# ⑪ POST /api/upload（模式 C）
# ══════════════════════════════════════════════════════════════
def _find_ffmpeg() -> Optional[str]:
    """找 ffmpeg —— **转发到 `analysis/ffmpeg.py` 的那一份**（全项目唯一一处）。

    ★ 2026-10-11：这里原来自己实现了一遍查找逻辑。问题不在这一份，
      而在**另一份**：`experiments/verify_new_take.py` 里也有一份，
      而那份的兜底列表是三个**别的机器留下的硬编码路径**，在这台机器上
      **一个都不存在** ⇒ 作者手机录的 m4a 跑验收脚本会直接失败
      （`librosa.load` 读 m4a 抛 `NoBackendError`）—— 而那正是
      "**录完才发现白录**"的场景。

      ⇒ 抽到 `analysis/ffmpeg.py`，两边共用。这正是本项目反复强调的那条：
        **同一件事不要在多处各写一遍**（两份实现必然有一天不同步，
          而不同步是**静默**的 —— 这次就静默了很久）。
    """
    from analysis.ffmpeg import find_ffmpeg
    return find_ffmpeg()


@app.post("/api/upload")
async def upload(bg: BackgroundTasks, req: Request,
                 file: UploadFile = File(...),
                 singer_id: str = "singer_upload", reference_id: str = "",
                 template_id: str = "", mode: str = "",
                 retry_of_uid: str = "", save_as: str = ""):
    """上传一段已有录音，走精算管线。

    ⚠️⚠️ 这里修了两条审计查出的问题（2026-09-23）：

    **① 体积无上限**。原来 `raw = await file.read()` 把**整个请求体**一次读进内存，
    没有任何校验 —— 实测 64 MiB 的 body 被完整接受（7.96s 后才因格式报 400），
    RSS +80 MB。真实用户撞不上，但这是"一个请求打爆服务"的入口。
    修法：先看 `Content-Length`（有就早退），**再**在流式累加里兜底
    （`Content-Length` 可以是假的或缺失）。

    **② 同步解码写在 `async def` 里**。`sf.read` + `librosa.resample` 是纯 CPU 调用，
    直接在协程里跑会**阻塞事件循环** —— 实测 15 MB 上传期间无关请求最坏卡 61.7ms
    （基线 2.5ms），且随体积线性增长。修法：丢进线程池
    （`run_in_threadpool`），让事件循环继续转。
    """
    import io
    import soundfile as sf
    from starlette.concurrency import run_in_threadpool

    # ── ①a 先看声明的长度（便宜、能挡住绝大多数） ──
    declared = req.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_UPLOAD_BYTES:
                return fail("TOO_LARGE",
                            f"文件太大了（{int(declared) / 1048576:.0f} MB > "
                            f"{MAX_UPLOAD_BYTES // 1048576} MB）。"
                            f"请只上传要练的那一段。", 413)
        except ValueError:
            pass

    # ── ①b 流式累加兜底（Content-Length 可能是假的或缺失） ──
    #   ⚠️ 分块读，不一次性 read()：否则"声明 1 KB、实际发 1 GB"照样打爆内存。
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = await file.read(1 << 20)          # 1 MiB 一块
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            return fail("TOO_LARGE",
                        f"文件太大了（超过 {MAX_UPLOAD_BYTES // 1048576} MB）。"
                        f"请只上传要练的那一段。", 413)
        buf.write(chunk)
    raw = buf.getvalue()

    # ── ② 解码与重采样丢进线程池（别阻塞事件循环） ──
    def _decode(data):
        """把上传的字节解成 22.05kHz 单声道 float32。

        ★ 2026-10-07 修：**原来只有 `soundfile` 一条路，读不了 m4a** ——
          而 **m4a 是手机录音的主力格式**（iPhone 录音、安卓录音机、
          微信导出大多是它）。实测：作者传 `9月19日 下午4点26分(1).m4a`
          得到 `Format not recognised.` ⇒ **最常用的用法直接失败。**

        ⇒ 改成**三级回退**，顺序有讲究：

          ① `soundfile` —— 对 wav/flac/ogg 最准最快，**先试它**
          ② `librosa.load` —— 走 audioread/ffmpeg，能读 m4a/mp3/aac
             （项目里 `import_phone_recordings.py` 一直用的就是这条）
          ③ `ffmpeg` 子进程 —— 前两条都不行时的最后手段

        ⚠️ 顺序不能反：`librosa.load` 对 wav 也可能绕 audioread，
           比 `soundfile` 慢且对某些位深不友好。**能走快路就走快路。**
        ⚠️ 三条都失败时，错误信息要**告诉用户怎么办**，而不是只丢一个
           `Format not recognised`（那是给开发者看的）。
        """
        # ── ① soundfile（wav / flac / ogg / aiff …）──
        try:
            y, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
            if y.ndim > 1:
                y = y.mean(axis=1)
            if sr != SR:
                import librosa
                y = librosa.resample(y, orig_sr=sr, target_sr=SR)
            if y.size:
                return y
        except Exception:  # noqa: BLE001
            pass

        # ── ② librosa（m4a / mp3 / aac / amr …）──
        try:
            import librosa
            y, _sr = librosa.load(io.BytesIO(data), sr=SR, mono=True)
            if y.size:
                return y.astype("float32")
        except Exception:  # noqa: BLE001
            pass

        # ── ③ ffmpeg 子进程（最后手段）──
        #   ⚠️ **不要用 `shutil.which("ffmpeg")` 就完事** —— 实测这台机器
        #      PATH 上**根本没有** ffmpeg，而项目里 `import_phone_recordings.py`
        #      当年能用只是因为当时恰好有。⇒ 找 ffmpeg 要**按优先级找一圈**。
        import subprocess
        import tempfile
        ff = _find_ffmpeg()
        if ff:
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "in.bin")
                dst = os.path.join(td, "out.wav")
                with open(src, "wb") as f:
                    f.write(data)
                try:
                    r = subprocess.run(
                        [ff, "-y", "-i", src, "-ac", "1", "-ar", str(SR), dst],
                        capture_output=True, timeout=120)
                    if r.returncode == 0 and os.path.exists(dst):
                        y, _sr2 = sf.read(dst, dtype="float32", always_2d=False)
                        if y.ndim > 1:
                            y = y.mean(axis=1)
                        if y.size:
                            return y
                except (OSError, subprocess.SubprocessError):
                    pass

        raise ValueError(
            "这个文件的音频格式读不出来。系统支持 wav / mp3 / m4a / aac / "
            "flac / ogg —— 手机上录的音一般都能用。"
            "如果是微信语音或视频文件（.mp4/.mov），请先转成 mp3 或 wav 再传。")

    try:
        y = await run_in_threadpool(_decode, raw)
    except Exception as e:  # noqa: BLE001
        return fail("BAD_REQUEST", f"读不出这个音频文件：{e}")
    if len(y) > SR * MAX_RECORD_SEC:
        return fail("TOO_LONG",
                    f"录音太长了（{len(y) / SR:.0f} 秒 > {MAX_RECORD_SEC} 秒）。"
                    f"整首最多 {MAX_RECORD_SEC // 60} 分钟；更长的请先剪一段再传。", 413)
    if len(y) < SR * MIN_USABLE_MS / 1000:
        return fail("TOO_SHORT", f"录音太短（不足 {MIN_USABLE_MS/1000:.0f} 秒）。")

    # ══════════════════════════════════════════════════════════
    # ★ 三种参考来源，判定顺序不能乱（否则模式 B 会被当成模式 C 自比）
    #   ① reference_id   → 内置曲目，模式 A
    #   ② template_id    → 第一遍存的跟唱模板，模式 B（两遍制的正路）
    #   ③ 都没有          → 模式 C，拿自己当参考 → 服务层会降级音准/节奏
    #   ⚠️ 曾经的 bug：这个端点压根不接 template_id，于是**上传路径永远是③**，
    #      第二遍无论带不带模板都在自比，音准/节奏恒被降级成 null。
    #      `_verify_modeb_twotake.py` 就是为了锁死这条回归。
    #   校验放在 save_as 之前：不合格的请求不该在磁盘上留下任何东西。
    # ══════════════════════════════════════════════════════════
    if reference_id and template_id:
        return fail("BAD_REQUEST", "reference_id 与 template_id 只能给一个。")
    tpl = None
    if template_id:
        tpl = templates.load(template_id)
        if tpl is None:
            return fail("REF_MISSING", f"找不到跟唱模板 {template_id}", 404)

    # ★ 实验采集：把规范化后的音频另存一份，盲评页面要靠它播放。
    #   不这么做的话采集页得手动备份，一定会漏。
    #
    # ⚠️ 三处加固（2026-09-24，审计 §2.5 / §5.1 / §5.6）：
    #   ① **长度上限**。原来传 300 个字符的 save_as → 写文件失败 →
    #      500，而异常字符串里带着**服务端绝对路径**
    #      （`Error opening 'D:\nodejs\project\毕设\data\recordings\exp\_AUDIT_AAA…'`）。
    #      现在超长直接 400，且在拼路径之前就拒。
    #   ② 拒绝**空名/纯符号**（清洗后可能变成空串或只剩 `.wav`）。
    #   ③ 同名不再静默覆盖：文件已存在就加 `-2`、`-3` 后缀，
    #      并在响应里说明**实际存成了什么名字**。
    #      原来同名两次都 200、后一个把前一个覆盖掉、没有任何提示 ——
    #      对采集是**数据丢失**（录了两遍，回来只剩一遍）。
    if save_as:
        import re as _re
        if len(save_as) > 80:
            return fail("BAD_REQUEST",
                        f"save_as 太长了（{len(save_as)} 字符，上限 80）。"
                        f"它会被当成文件名，不该是一整段说明文字。")
        safe = _re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", save_as).strip("._-") or "take"
        if not safe.lower().endswith(".wav"):
            safe += ".wav"
        exp_dir = os.path.join(ROOT, "data", "recordings", "exp")
        os.makedirs(exp_dir, exist_ok=True)
        # ★ 目录配额（2026-09-24，审计 §2.5："录音落盘无配额"）。
        #   单次上传有 32 MiB 上限，但**次数没有** —— 局域网里任何人都能
        #   反复传 32 MiB 的 wav（`/api/upload` 不需要令牌，它是主应用的
        #   模式 C 上传），把磁盘写满。写满之后**整个服务连日志都写不出来**。
        #   配额把"无界"变成"有界"，且失败时说的是**怎么处理**（归档/清理），
        #   不是一句"内部错误"。
        used = _dir_bytes(exp_dir)
        if used + len(y) * 2 > EXP_DIR_QUOTA_BYTES:
            print(f"[SingLens] ⚠️ 实验录音目录已用 {used / 1048576:.0f} MB，"
                  f"达到上限 {EXP_DIR_QUOTA_BYTES / 1048576:.0f} MB，拒绝本次落盘")
            return fail("TOO_LARGE",
                        f"实验录音目录已占用 {used / 1048576:.0f} MB，"
                        f"超过上限 {EXP_DIR_QUOTA_BYTES // 1048576} MB。"
                        f"请把 data/recordings/exp 里的录音归档或清理后再录。", 413)
        final, k = safe, 1
        while os.path.exists(os.path.join(exp_dir, final)):
            k += 1
            final = safe[:-4] + f"-{k}.wav"
            if k > 50:                       # 兜底：极端情况下别死循环
                final = safe[:-4] + f"-{uuid.uuid4().hex[:6]}.wav"
                break
        try:
            sf.write(os.path.join(exp_dir, final), y, SR, subtype="PCM_16")
        except Exception as e:  # noqa: BLE001
            # 不把 os 的异常原文回给客户端（里面有绝对路径）。写日志，回固定文案。
            print(f"[SingLens] ⚠️ 另存实验音频失败（{safe}）：{type(e).__name__}: {e}")
            return fail("INTERNAL", "另存实验音频失败（文件名可能不合法）。"
                                    "换一个简单点的名字试试。", 500)
        saved_as = final
        if final != safe:
            print(f"[SingLens] 实验音频同名，已另存为 {final}（避免覆盖已有文件）")
    else:
        saved_as = None

    uid = f"sess_{uuid.uuid4().hex[:12]}"
    if reference_id:
        eff_mode = "A"
    elif template_id:
        eff_mode = "B"
    else:
        eff_mode = mode if mode in ("B", "C") else "C"
    # ★ 模式 B 的 reference_id 必须是【模板的身份】，不是 None。
    #   踩过的坑：这里原来写 `reference_id=reference_id or None` ——
    #   upload 路径的 query 里没有 reference_id（用户给的是 template_id），
    #   于是 sess.reference_id = None ⇒ 报告里 `meta.reference_id` 退化成
    #   "REF_SELF"，成长档案与实验表都读不到"这次跟哪首歌比"。
    #   也就是说：库里的 reference_id 存对了，但**报告这条链没接上**。
    sess_ref = reference_id or (tpl.reference_id or tpl.template_id if tpl else None)
    sess_title = (tpl.title if (tpl is not None and not reference_id) else None)
    if reference_id:
        from analysis.align import BUILTIN as _B
        sess_title = _B[reference_id]["title"]
    sess = Session(uid=uid, singer_id=singer_id, mode=eff_mode,
                   reference_id=sess_ref, reference_title=sess_title,
                   retry_of_uid=retry_of_uid or None,
                   template_id=template_id or None)
    sess.audio = y.astype(np.float32)
    sess.status = "ANALYZING"
    if reference_id:
        ref = load_builtin(reference_id)
        sess.rt.ref_notes = [(n.start_ms, n.end_ms, n.pitch_midi) for n in ref.notes]
    elif tpl is not None:
        sess.template_quality = tpl.quality or None
        sess.rt.ref_notes = [(n["start_ms"], n["end_ms"], n["pitch_midi"])
                             for n in tpl.notes if n.get("pitch_midi") is not None]
    _reap_sessions()      # 先回收再放新的，保证不超过容量上限
    SESSIONS[uid] = sess
    bg.add_task(run_precise, sess)
    return ok({"session_uid": uid, "status": sess.status, "mode": sess.mode,
               "reference_id": sess_ref, "template_id": template_id or None,
               "reference": ({"reference_id": sess_ref, "title": tpl.title,
                              "note_count": tpl.note_count,
                              "duration_ms": tpl.duration_ms,
                              "source": "user_first_take",
                              "has_vocal": bool(tpl.vocal_path),
                              "has_backing": bool(tpl.backing_path),
                              "lyrics": (tpl.lyrics or [])}
                             if tpl is not None and not reference_id else None),
               "duration_ms": int(len(y) / SR * 1000),
               # ★ 实际存成了什么名字要回给调用方（2026-09-24）。
               #   同名不再静默覆盖，而是自动加 -2/-3 后缀 ——
               #   采集页拿到这个字段才能把"哪条录音对应哪个文件"记对。
               "saved_as": saved_as})


# ══════════════════════════════════════════════════════════════
# ⑬ 跟唱模板（模式 B 两遍制）
# ══════════════════════════════════════════════════════════════
# 用法（前端的两步）：
#   1) 第一遍：createSession({mode:'B'}) 正常录 + finish → 拿到 session_uid
#   2) POST /api/template  {session_uid} → 存成模板，拿到 template_id
#   3) 第二遍：createSession({mode:'B', template_id}) 录 + finish
#      → 这一次的音准/节奏是**真的**（与第一遍对齐），不再是恒满分
@app.post("/api/template")
async def create_template(req: Request):
    """把某次会话的演唱存成跟唱模板（模式 B 的第一遍）。"""
    body = await _json(req)
    uid = body.get("session_uid") or ""
    sess = SESSIONS.get(uid)
    if sess is None:
        # ★ 2026-10-19：服务重启后，老会话只剩报告（内存里没有 audio）。
        #   不能因此说“会话不存在”：用户刚唱完、报告还在，模板卡也能点。
        #   这时直接从报告的 segments（用户逐音音高/时间）建模板。
        from web import practice_store
        from analysis.notes import Note as _Note
        rep = practice_store.load_report(uid)
        if not rep:
            return fail("NOT_FOUND", f"会话 {uid} 不存在", 404)
        # 优先用落盘录音重切（与在线路径同口径）；没有再退回 report.segments。
        wav = os.path.join(ROOT, "data", "recordings", "sessions", f"{uid}.wav")
        notes = []
        if os.path.isfile(wav):
            try:
                y, sr0 = librosa.load(wav, sr=SR, mono=True)
                _hpss = bool((CONFIG.get("recording") or {}).get("accompaniment_mode"))
                f0, _, _, _ = extract_f0_pyin_auto(y, sr0, hpss=_hpss)
                notes, _, _ = segment_notes(y, sr0, f0=f0)
            except Exception as e:  # noqa: BLE001
                print(f"[SingLens] ⚠️ 老会话重切失败（{uid}）：{e}")
                notes = []
        if not notes:
            segs = [s for s in (rep.get("segments") or [])
                    if s.get("pitch_midi") is not None
                    and s.get("start_ms") is not None
                    and s.get("end_ms") is not None]
            if len(segs) < 2:
                return fail("BAD_REQUEST", "这次报告没有可用的音符，无法做模板。", 400)
            start0 = min(int(s["start_ms"]) for s in segs)
            notes = [_Note(
                note_index=i,
                start_ms=max(0, int(s["start_ms"]) - start0),
                end_ms=max(1, int(s["end_ms"]) - start0),
                pitch_midi=float(s["pitch_midi"]),
                boundary_source="user_take",
            ) for i, s in enumerate(segs)]
        quality = assess_template_quality(notes)
        if quality.get("level") == "reject":
            return fail("BAD_REQUEST",
                        "这份模板不够稳，不建议拿它当基准：" +
                        "；".join(quality.get("reasons") or []) +
                        "。建议重录一遍，或改用「跟着标准曲目唱」。", 400)
        tid = templates.save(
            notes, singer_id=body.get("singer_id") or None,
            title=body.get("title") or "",
            hop_length=PRECISE_HOP, sr=SR, quality=quality,
            reference_id=body.get("reference_id") or None,
            source_session_uid=uid)
        tpl = templates.load(tid)
        return ok({"template_id": tid, "reference_id": tpl.reference_id,
                   "title": tpl.title,
                   "note_count": tpl.note_count, "duration_ms": tpl.duration_ms,
                   "source_session_uid": tpl.source_session_uid,
                   "quality": quality})
    if sess.audio is None:
        return fail("BAD_REQUEST", "这次会话没有音频，无法做模板（可能已经重启服务）。", 400)

    # 重新切一次音符。为什么不在 finish 时缓存：
    #   · 切分结果不进契约，缓存它等于多一份状态要维护；
    #   · 重算一次只要几百 ms（PYIN 结果已随会话缓存在 sess.audio 上，
    #     这里重跑的是 segment_notes，不是 PYIN 全段）。
    try:
        # ⚠️ 必须与 `run_precise` 用**同一个** hpss 设置：模板是"用户的参考"，
        #    参考与演唱的提取口径不一致会凭空造出偏差。
        _hpss = bool((CONFIG.get("recording") or {}).get("accompaniment_mode"))
        f0, _, _, _ = extract_f0_pyin_auto(sess.audio, sess.sr, hpss=_hpss)
        notes, _, _ = segment_notes(sess.audio, sess.sr, f0=f0)
    except Exception as e:  # noqa: BLE001
        return fail("INTERNAL", f"模板提取失败：{e}", 500)
    if not notes:
        return fail("NO_VOICE", "没检测到音符，这段不能当模板。", 400)

    quality = assess_template_quality(notes)
    if quality.get("level") == "reject":
        # 模板本身不稳 → 拿它当基准会"越像越歪"。**不静默接受。**
        return fail("BAD_REQUEST",
                    "这份模板不够稳，不建议拿它当基准：" +
                    "；".join(quality.get("reasons") or []) +
                    "。建议重录一遍，或改用「跟着标准曲目唱」。", 400)

    # ★ reference_id 落库：模板即"用户的参考曲目"，它必须有自己的身份。
    #   调用方可以给一个（比如从模板库选一首时带过来的曲名），不给就自指。
    tid = templates.save(notes, singer_id=sess.singer_id,
                         title=body.get("title") or "",
                         hop_length=PRECISE_HOP, sr=sess.sr, quality=quality,
                         reference_id=body.get("reference_id") or None,
                         source_session_uid=uid)
    tpl = templates.load(tid)
    return ok({"template_id": tid, "reference_id": tpl.reference_id,
               "title": tpl.title,
               "note_count": tpl.note_count, "duration_ms": tpl.duration_ms,
               "source_session_uid": tpl.source_session_uid,
               "quality": quality})


@app.post("/api/practice/segment")
async def practice_segment(req: Request):
    """把报告里的「练这一句」变成一段可跟唱的短模板。

    2026-10-19：AI 陪练 MVP。
    · 报告里的 `practice_card` 已经选好参考片段（第 r0–r1 个音）；
    · 这里把这几个**参考音**存成跟唱模板，前端用现有 B 模式录一小段；
    · 片段起点归一化到 0，否则用户要等参考开头的 3 秒静音。
    """
    from analysis.notes import Note as _Note
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是对象。")
    uid = str(body.get("session_uid") or "").strip()
    singer_id = str(body.get("singer_id") or "").strip()
    kind = str(body.get("kind") or "note").lower()
    if not uid:
        return fail("BAD_REQUEST", "缺 session_uid。")
    try:
        _assert_session_owner(uid, singer_id or None)
        sess = get_session_or_db(uid)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, dict) else {}
        return fail(detail.get("code", "NOT_FOUND"),
                    detail.get("message", "找不到这次练习"), e.status_code)
    rep = sess.report
    if not rep:
        return fail("CONFLICT", "这次练习还没有报告，不能练这一句。", 409)
    # ★ 2026-10-19：旧报告的 practice_card 没有 context（歌词行），
    #   报告页读取时会补，但这里直接读 sess.report，必须同样补一次；
    #   否则微练习仍按两个音建模板，用户还是不知道从哪进拍。
    rep = _ensure_practice_context(rep)
    # G58：节拍诊断的短片段走 beat_practice；普通“练这一句”仍走 practice_card。
    card = (rep.get("beat_practice") if kind == "beat"
            else rep.get("practice_card"))
    if not card or not (card.get("segment") or {}).get("notes"):
        return fail("BAD_REQUEST", "这一遍没有可拆的参考片段。", 400)
    queue = [card] if kind == "beat" else (card.get("queue") or [card])
    try:
        sidx = int(body.get("segment_index") or 0)
    except (TypeError, ValueError):
        sidx = 0
    if sidx < 0 or sidx >= len(queue):
        return fail("BAD_REQUEST", f"微练习队列没有第 {sidx + 1} 段。", 400)
    item = queue[sidx]
    notes = item["segment"]["notes"]
    if len(notes) < 2:
        return fail("BAD_REQUEST", "这一段太短，不足以单独练。", 400)
    # ★ 2026-10-19：微练习模板改用**整句歌词行**，而不是问题两个音。
    #   只给两个音时用户不知道从哪进拍（作者实测：进不了拍 → 整段没对上 →
    #   本次未评估）。整句有上下文，B 模式的 4 拍预备之后跟着整句唱。
    ctx = item.get("context") or card.get("context") or {}
    line_from, line_to = ctx.get("line_from"), ctx.get("line_to")
    line_notes = []
    if (kind != "beat"
            and isinstance(line_from, int) and isinstance(line_to, int)
            and sess.reference_id):
        try:
            from analysis.align import BUILTIN as _B, load_builtin as _lb
            if sess.reference_id in _B:
                _ref = _lb(sess.reference_id, first_start_ms=0)
                line_notes = [n for n in _ref.notes
                              if line_from <= n.index <= line_to]
            elif sess.template_id:
                # ★ 2026-10-20：歌曲模板的「整句」= 歌词行覆盖的参考音。
                _t = templates.load(sess.template_id)
                if _t:
                    line_notes = [n for i, n in enumerate(_t.notes or [])
                                  if line_from <= i <= line_to
                                  and n.get("pitch_midi") is not None]
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ 取整句模板失败：{e}")
    if len(line_notes) >= 3:
        notes = [{"start_ms": int(n["start_ms"]) if isinstance(n, dict) else n.start_ms,
                  "end_ms": int(n["end_ms"]) if isinstance(n, dict) else n.end_ms,
                  "pitch_midi": float(n["pitch_midi"]) if isinstance(n, dict) else n.pitch_midi}
                 for n in line_notes]
        label = f"第 {ctx.get('line_no', '')} 句「{ctx.get('line') or ''}」".strip()
    else:
        label = item.get("label") or "微练习"
    start0 = min(int(n["start_ms"]) for n in notes)
    tpl_notes = []
    for i, n in enumerate(notes):
        tpl_notes.append(_Note(
            note_index=i,
            start_ms=max(0, int(n["start_ms"]) - start0),
            end_ms=max(1, int(n["end_ms"]) - start0),
            pitch_midi=float(n["pitch_midi"]),
            boundary_source="practice_reference",
        ))
    try:
        # ★ 2026-10-20：歌曲参考的微练习也放**真实伴奏的那一段**，
        #   而不是合成旋律。否则用户听到的是"机器叮叮"、不知道词和进拍点。
        #   源模板有 backing_path 时，裁出这一段（含 2 秒倒数由 guide 端点加），
        #   并把原唱人声一起裁出来（报告页「听原唱这一段」用）。
        src_tpl = templates.load(sess.template_id) if sess.template_id else None
        new_tid = f"tpl_{uuid.uuid4().hex[:12]}"
        rel_back = rel_vocal = None
        if (src_tpl and (src_tpl.source_kind or "") == "song_vocal"
                and src_tpl.backing_path):
            try:
                import shutil
                from analysis.song_import import crop_wav
                bsrc = src_tpl.backing_path
                if not os.path.isabs(bsrc):
                    bsrc = os.path.join(ROOT, bsrc)
                vsrc = src_tpl.vocal_path
                if vsrc and not os.path.isabs(vsrc):
                    vsrc = os.path.join(ROOT, vsrc)
                seg_end = max(int(n["end_ms"]) for n in notes)
                os.makedirs(os.path.join(ROOT, "data", "backing"), exist_ok=True)
                rel_back = os.path.join("data", "backing", f"{new_tid}.wav")
                crop_wav(bsrc, os.path.join(ROOT, rel_back), start0, seg_end + 200)
                if vsrc and os.path.exists(vsrc):
                    rel_vocal = os.path.join("data", "backing", f"{new_tid}_vocal.wav")
                    crop_wav(vsrc, os.path.join(ROOT, rel_vocal), start0, seg_end + 200)
            except Exception as e:  # noqa: BLE001
                print(f"[SingLens] ⚠️ 微练习伴奏裁剪失败（回退合成旋律）：{e}")
                rel_back = rel_vocal = None
        tid = templates.save(
            tpl_notes, singer_id=sess.singer_id or singer_id,
            title=f"微练习 · {label}", hop_length=PRECISE_HOP, sr=SR,
            source_kind=("song_vocal" if rel_back else "user_take"),
            backing_path=rel_back, vocal_path=rel_vocal,
            source_meta=({"song_title": (src_tpl.source_meta or {}).get("song_title"),
                          "start_ms": start0, "end_ms": seg_end + 200,
                          "section_ms": seg_end + 200 - start0,
                          "parent_template_id": src_tpl.template_id,
                          "auto_extracted": True} if rel_back else None),
            template_id=new_tid)
    except Exception as e:  # noqa: BLE001
        return fail("SERVER_ERROR", f"微练习模板存不下来：{str(e)[:100]}", 500)
    if not tid:
        return fail("SERVER_ERROR", "微练习模板存不下来。", 500)
    tpl = templates.load(tid)
    return ok({"template_id": tid, "title": tpl.title if tpl else label,
               "note_count": len(tpl_notes),
               "duration_ms": int(tpl_notes[-1].end_ms),
               "card": item, "segment_index": sidx,
               "queue_length": len(queue)})


@app.get("/api/template")
async def list_templates_endpoint(singer_id: str = "", limit: int = LIMIT_DEFAULT):
    """我的模板列表。

    ★ **必须带 `singer_id`**（2026-09-24，审计 §3.1 点名的一处身份泄露）：
      原来不带 `singer_id` 时返回**全部演唱者**的模板，而且每条都带
      `singer_id` —— 也就是说，任何能访问到这个服务的人（同一 WiFi 下的同学）
      都能看到"这台机器上有几个演唱者、各自叫什么、各有多少模板"。
      模板归属本身就是身份信息。

    ⚠️ 说清这一层的**强度**：`singer_id` 是调用方自报的，所以这不是鉴权 ——
      它挡的是"**不带身份就把全表列出来**"这种默认泄露，以及明文的跨人枚举。
      真正的访问控制要等 G3 多用户（有了身份才谈得上"谁能看谁的"）。
      为什么现在做而不是等 G3：**默认行为**必须安全 ——
      一个不传参数就吐出全表的接口，上线即泄露，而"等你补上参数"不会发生。
    """
    if not singer_id:
        return fail("BAD_REQUEST",
                    "请带上 singer_id —— 模板是分人的，"
                    "不带就返回全部会泄露'这台机器上有哪些演唱者'。")
    return ok({"templates": templates.list_templates(singer_id, _clamp_limit(limit))})


@app.get("/api/template/{tid}")
async def get_template_endpoint(tid: str, notes: bool = False):
    """单条模板。

    ⚠️ `notes=true` 才会带上音符序列 —— **默认不带**。
       列表接口（`GET /api/template`）故意不含音符（省流量），
       但实时画"它希望你唱的音"那条阶梯需要它（G4-b）。
       默认不带是因为音符序列对一个长模板能有几十 KB，
       而大多数调用方（比如"给用户看模板信息"）不需要。
    """
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    out = {"template_id": tpl.template_id, "reference_id": tpl.reference_id,
           "title": tpl.title,
           "note_count": tpl.note_count, "duration_ms": tpl.duration_ms,
           "source_session_uid": tpl.source_session_uid,
           "source_kind": tpl.source_kind or "user_take",
           "has_backing": bool(tpl.backing_path),
           "has_vocal": bool(tpl.vocal_path),
           "has_lyrics": bool(tpl.lyrics),
           "lyrics_count": len(tpl.lyrics or []),
           "lyrics": tpl.lyrics or [],
           "lyrics_source": tpl.lyrics_source,
           "source_meta": tpl.source_meta or {},
           "quality": tpl.quality,
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                       time.localtime(tpl.created_at))}
    if notes:
        out["notes"] = tpl.notes
    return ok(out)


@app.delete("/api/template/{tid}")
async def delete_template_endpoint(tid: str, singer_id: str = ""):
    """删除一条模板。

    ★ 为什么要带 `singer_id` 并做归属校验（2026-09-24，审计 §3.2）：
      原来这个端点只有一句 `templates.delete(tid)` —— **没有任何归属判断**，
      也就是说前端一个拼错的 id、或者任何知道 id 的人，都能删掉别人的模板。
      模板是用户自己唱出来的东西（模式 B 的样板），删错了不可恢复。

    ⚠️ 说清这一层的**强度**：本服务没有账号体系，`singer_id` 是调用方自报的，
      所以它不是鉴权 —— 它挡的是"**没带身份的误删**"和"**跨演唱者删除**"
      （前端一定会带上当前演唱者）。真正的访问控制要等 G3 多用户。
      这里**不**把它伪装成安全机制：超管式的一次性清理请用
      `POST /api/privacy/purge`（那个有显式 confirm）。
    """
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if not singer_id:
        return fail("BAD_REQUEST",
                    "删除模板必须带上 singer_id —— 否则不知道该删谁的模板。")
    if tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN",
                    f"这条模板属于 {tpl.singer_id}，不是 {singer_id}。", 403)
    return ok({"deleted": templates.delete(tid), "template_id": tid})


@app.patch("/api/template/{tid}")
async def rename_template_endpoint(tid: str, req: Request):
    """给模板改名（只改 title，不动音符）。

    ★ 为什么单独做一个端点（2026-10-18）：模板行已经有「删」，但**没有「改名」**
      —— 用户录了 10 个模板之后只能看到一串「我的模板 10-18 ...」。
      改名必须与删除带同一套归属校验：模板是用户自己唱出来的东西。
    """
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是对象。")
    title = str(body.get("title") or "").strip()[:60]
    singer_id = str(body.get("singer_id") or "").strip()
    if not title:
        return fail("BAD_REQUEST", "模板名不能为空。")
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if not singer_id:
        return fail("BAD_REQUEST",
                    "改模板名必须带上 singer_id —— 否则不知道该改谁的模板。")
    if tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN",
                    f"这条模板属于 {tpl.singer_id}，不是 {singer_id}。", 403)
    if not templates.rename(tid, singer_id, title):
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    return ok({"template_id": tid, "title": title})


@app.post("/api/template/{tid}/lyrics/retry")
async def retry_template_lyrics(tid: str, req: Request):
    """旧歌曲模板重切歌词（G52）：只对原唱人声重跑 Whisper，不改音符/分数。

    ★ 为什么需要：G51 的“停顿断行”只对**新分析**生效；旧模板的歌词是
      旧切法，报告里可能把两句粘成一行。重新识别一次即可修好文本，
      但**旧报告不会自动变** —— 报告里的行已经冻结，只有下次跟唱生效。
    """
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是对象。")
    singer_id = str(body.get("singer_id") or "").strip()
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if not singer_id:
        return fail("BAD_REQUEST", "重切歌词必须带 singer_id。")
    if tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN",
                    f"这条模板属于 {tpl.singer_id}，不是 {singer_id}。", 403)
    if (tpl.source_kind or "") != "song_vocal" or not tpl.vocal_path:
        return fail("BAD_REQUEST", "只有上传歌曲（带原唱人声）的模板才能重切歌词。")
    p = tpl.vocal_path
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    if not os.path.exists(p):
        return fail("NOT_FOUND", "这条模板的原唱人声文件不在了，无法重切。", 404)
    lines, source, err = await asyncio.to_thread(_song_lyrics, p)
    if err or not lines:
        return fail("LYRICS_FAILED", err or "没识别到歌词。", 502)
    if not templates.update_lyrics(tid, lines, source):
        return fail("NOT_FOUND", "歌词保存失败：模板可能已被删除。", 404)
    return ok({
        "template_id": tid,
        "lyrics": lines,
        "lyrics_source": source,
        "lyrics_count": len(lines),
        "old_reports_unchanged": True,
        "message": "歌词已重切；旧报告不会变，下次跟唱生效。",
    })


# ══════════════════════════════════════════════════════════════
# ⑭ POST /api/privacy/purge —— 「清空所有录音与记录」
# ══════════════════════════════════════════════════════════════
# ★ 这是一句**产品承诺**（设置页隐私板块写着"这会删掉全部历史，无法恢复"）。
#   原型里它只是个静态按钮 —— 而一个骗人的"删除"按钮，
#   在"做系统"的标准下比没有这个按钮更糟。
#
# ⚠️ 两个设计要点：
#   ① **必须显式确认**。不带 `confirm` 直接拒绝，防止误触/被脚本调。
#   ② **如实回报删了什么**。悄悄留下东西（比如那 26 个 wav）比不提供更坏：
#      用户以为删干净了。所以返回 sessions_deleted / templates_deleted /
#      files_deleted / bytes_freed 四项，前端要显示出来。
@app.post("/api/privacy/purge")
async def purge_endpoint(req: Request):
    deny = _remote_operator_denied(req)
    if deny:
        return deny
    body = await _json(req)
    if body.get("confirm") not in (True, "YES", "yes", "DELETE"):
        return fail("BAD_REQUEST",
                    "这是一个不可恢复的操作。请带上 confirm=true 明确确认："
                    "它会删掉全部练习记录、跟唱模板和录音文件。", 400)

    scope = body.get("scope") or "all"          # all / singer
    sid = (body.get("singer_id") or "").strip() or None
    if scope == "singer" and not sid:
        return fail("BAD_REQUEST", "scope=singer 时必须给 singer_id。", 400)

    from web import practice_store
    target = sid if scope == "singer" else None

    # ★ G13：**在删库之前**先把这次要清的会话号收下来。
    #   为什么必须赶在删库前：会话录音的文件名**就是会话 uid**，
    #   而 uid→演唱者 的对应关系只存在于 `practice_session` 里。
    #   等库删完再想按演唱者找文件，就只能靠猜文件名了 —— 那是这个
    #   项目明确拒绝过的做法（"宁可少删，也不能删错"）。
    #   ⚠️ 这也让"按演唱者清空"第一次能**可靠地**删掉属于他的会话录音，
    #      而不像 `data/recordings/` 下那些老录音（文件名里没有演唱者信息）。
    session_uids: Optional[list] = None
    if target is not None:
        try:
            session_uids = [s["session_uid"]
                            for s in practice_store.sessions_of(target)]
        except Exception as e:  # noqa: BLE001
            print(f"[SingLens] ⚠️ 取 {target} 的会话号失败（会话录音将不删）：{e}")
            session_uids = []

    # ① 数据库：练习记录 + 模板
    try:
        db = practice_store.purge(target)
    except Exception as e:  # noqa: BLE001
        return fail("INTERNAL", f"清空练习记录失败：{e}", 500)

    # ② 内存缓存（G3：它只是缓存，清不清都不影响正确性 —— 但不清的话
    #    `GET /api/singer` 的 `cache_sessions` 会对不上库，那条告警会变成噪音）
    if target:
        SINGERS.pop(target, None)
    else:
        SINGERS.clear()

    # ③ ★★ G13：**内存里的音频副本必须一起丢**。
    #
    #   这是实测抓出来的一个**真隐私漏洞**（不是理论风险）：
    #   清空文件之后，`GET /api/session/{uid}/audio` **仍然吐得出音频** ——
    #   因为 `_read_session_audio` 优先读内存里还活着的 `sess.audio`。
    #   于是我们对用户说的"已删除"是**一句假话**，而报告页还能把
    #   他的声音放出来。隐私功能上，这种"说了删其实还在"比不提供功能更坏。
    #
    #   ⚠️ 同时要把**报告缓存里的 `audio_available` 改掉**：
    #      否则报告页会继续显示"听这一段"按钮，点下去 404 ——
    #      而"按下去没反应的按钮比没有按钮更坏"是本项目写过两次的规矩。
    audio_dropped = 0
    for uid, s in list(SESSIONS.items()):
        if target is not None and getattr(s, "singer_id", None) != target:
            continue
        had = (s.audio is not None and len(s.audio)) or \
              bool((s.audio_info or {}).get("saved"))
        s.audio = None
        s.audio_info = {"saved": False, "bytes": 0,
                        "reason": "已被「清空所有录音与记录」删除"}
        if isinstance(s.report, dict):
            m = s.report.get("meta")
            if isinstance(m, dict):
                m["audio_available"] = False
                m["audio_reason"] = "已被「清空所有录音与记录」删除"
        if had:
            audio_dropped += 1

    # ④ 录音文件。⚠️ 老录音只有"全部清空"才动 ——
    #    按演唱者删的话，wav 文件名里没有可靠的演唱者信息（可能被重命名过），
    #    猜错就会删掉别人的录音。宁可少删，也不能删错。
    #    ★ 但**会话录音**不一样：文件名就是会话 uid（见上面 ③ 之前那段），
    #      所以按演唱者也能**可靠地**删。
    files = {"files_deleted": 0, "bytes_freed": 0}
    session_files = {"files_deleted": 0, "bytes_freed": 0}
    skipped_reason = ""
    if target is None:
        # ⚠️ 只删**练习会话录音**（文件名=会话 uid，应用自己生成）。
        #    `data/recordings/exp` / `constructed` / 顶层素材是研究数据，
        #    不属于用户“清空我的练习”；之前把它们一起删掉会让
        #    exp_recording 指向不存在的 wav，也让论文原始音频不可恢复。
        for sub in ("data/recordings/sessions",):
            p = os.path.join(ROOT, sub)
            r = practice_store.purge_recordings(p)
            files["files_deleted"] += r["files_deleted"]
            files["bytes_freed"] += r["bytes_freed"]
        skipped_reason = (
            "只删除练习会话录音（`data/recordings/sessions`）。"
            "`data/recordings/exp`、`constructed` 和顶层研究素材未动 —— "
            "它们是实验原始数据，删了会让盲评/论文复算无法重来。")
    else:
        # ★ G13：按演唱者清空时，**会话录音可以可靠地删** ——
        #   文件名就是会话 uid，而 uid 是删库前从 `practice_session` 里取的
        #   （见本节开头）。所以这里不是"猜文件名"，是**精确对应**。
        #   `data/recordings/` 下那些老录音仍然不删（它们的文件名里
        #   没有演唱者信息），理由与原来一致。
        for uid in (session_uids or []):
            p = session_audio_path(uid)
            if p and os.path.exists(p):
                try:
                    sz = os.path.getsize(p)
                    os.remove(p)
                    session_files["files_deleted"] += 1
                    session_files["bytes_freed"] += sz
                except OSError as e:
                    print(f"[SingLens] ⚠️ 删会话录音失败（{uid}）：{e}")
        skipped_reason = (
            "按演唱者清空时**不动旧的录音文件**（`data/recordings/` 下那些）—— "
            "文件名里没有可靠的演唱者信息，猜错会删掉别人的录音。"
            "★ 但**这次的会话录音**（逐段复听用的）已经按会话号**精确删除**："
            f"{session_files['files_deleted']} 个。")

    _refresh_privacy()
    return ok({
        "scope": scope, "singer_id": target,
        "sessions_deleted": db["sessions_deleted"],
        "templates_deleted": db["templates_deleted"],
        "files_deleted": files["files_deleted"],
        "bytes_freed": files["bytes_freed"],
        # ★ G13：分别回报 —— 混成一个数会让"按演唱者清空"看起来删了文件
        #   而实际上一个都没删（那正是这轮之前的状态）。
        "session_audio_files_deleted": session_files["files_deleted"],
        "session_audio_bytes_freed": session_files["bytes_freed"],
        "session_audio_dropped_from_memory": audio_dropped,
        "skipped_reason": skipped_reason,
        "remaining": {"practice_sessions": practice_store.count(),
                      "singers": practice_store.count_singers(),
                      "recorded_files": CONFIG["privacy"]["sample_count"],
                      "used_bytes": CONFIG["privacy"]["used_bytes"]},
        # 明确写清**没删**什么 —— 不然用户以为实验数据也没了
        "kept": "实验数据（exp_* 表、data/recordings/exp、constructed 与研究素材）未删除。"
                "演唱者身份（app_singer 名册：谁叫什么）保留 —— "
                "删的是记录，不是身份；身份一起删的话，界面上的"
                "「当前演唱者」会变成一个不存在的人（所有接口 404）。",
    })


# ══════════════════════════════════════════════════════════════
# ⑫ GET/PUT /api/config
# ══════════════════════════════════════════════════════════════
@app.get("/api/config")
async def get_config():
    _refresh_privacy()
    return ok(CONFIG)


@app.put("/api/config")
async def put_config(req: Request):
    """按【组】更新。可以只传某一组里的某几个键：
         {"feedback": {"show_jargon": true}}

    ⚠️⚠️ 这里曾经有一个**永久性**故障（审计查出、我复核确认、2026-09-23 修）：
        旧实现是"边校验边写入"——`for k, v in patch.items(): CONFIG[grp][k] = v`。
        Python 的 `json.loads` **默认接受非标准的 `NaN` / `Infinity`**，
        于是 `PUT {"feedback":{"ai_comment":NaN}}` 会把这个 NaN **写进 CONFIG**，
        然后渲染响应时 `JSONResponse`（`allow_nan=False`）抛错 → 500。
        而值已经写进去了 ⇒ **此后每一次 `GET /api/config` 都是 500，重启前不自愈**。
        实测：设置页与任何读配置的流程全部失效，现场只看到"配置坏了"。

    修法三条，缺一不可：
      ① **先全量校验、再原子替换** —— 校验失败时 CONFIG 一个字节都不动
      ② 拒绝非有限浮点（`math.isfinite`）与类型不符的值
      ③ 组名/键名不在 CONFIG 里就报错（旧实现是 `continue` 静默忽略，
         于是前端拼错键名时以为存上了，其实什么都没发生）
    """
    deny = _remote_operator_denied(req)
    if deny:
        return deny
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是一个对象，形如 {\"feedback\": {\"show_jargon\": true}}。")

    def bad(v) -> Optional[str]:
        """值不合法时返回原因，合法返回 None。"""
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            # ★ 关键：NaN/Inf 会让 JSONResponse 抛错，而旧实现已经把它写进去了
            if not math.isfinite(float(v)):
                return f"{v!r} 不是有限数值"
            return None
        if isinstance(v, str):
            return None
        return f"不支持的类型 {type(v).__name__}"

    # ── ① 先全量校验（不动 CONFIG）──
    staged, changed = {}, {}
    for grp, patch in body.items():
        if grp not in CONFIG:
            return fail("BAD_REQUEST", f"没有「{grp}」这一组设置。"
                                      f"可用的组：{', '.join(sorted(CONFIG))}")
        if not isinstance(patch, dict):
            return fail("BAD_REQUEST", f"「{grp}」的值必须是一个对象。")
        staged[grp] = {}
        for k, v in patch.items():
            if k not in CONFIG[grp]:
                return fail("BAD_REQUEST", f"「{grp}」里没有「{k}」这一项。"
                                          f"可用的键：{', '.join(sorted(CONFIG[grp]))}")
            why = bad(v)
            if why is not None:
                return fail("BAD_REQUEST", f"「{grp}.{k}」的值不合法：{why}。")
            # 类型要与现值一致（避免把字符串塞进数值项，日后算得莫名其妙）
            cur = CONFIG[grp][k]
            if isinstance(cur, bool) and not isinstance(v, bool):
                return fail("BAD_REQUEST", f"「{grp}.{k}」应该是 true/false。")
            if isinstance(cur, (int, float)) and not isinstance(cur, bool) \
                    and not isinstance(v, (int, float)):
                return fail("BAD_REQUEST", f"「{grp}.{k}」应该是数值。")
            staged[grp][k] = v
            changed[f"{grp}.{k}"] = v

    # ── ② 校验全过了才原子替换 ──
    for grp, kv in staged.items():
        CONFIG[grp].update(kv)
    return ok({"updated": changed, "config": CONFIG})


# ══════════════════════════════════════════════════════════════
# 附加：参考曲目列表（前端 bootstrap 需要）
# ══════════════════════════════════════════════════════════════
# ★ LLM API key 的前端管理（2026-10-07）
#
#   为什么要这三个端点：原来 key 只能靠环境变量或手写文件 ——
#   用户在**界面上配不了**，而"要在答辩现场用真 LLM"就必须能当场配。
#
#   ⚠️ 三条安全约束（改动这里之前先读）：
#     ① **key 永不回显** —— GET 只返回 `configured` / `hint`（前3+后4）/ `source`
#     ② 保存前**先真调一次**验证（`test_key`）—— 格式对但欠费的 key
#        到处都是，不验的话答辩现场才发现
#     ③ 文件在 `.gitignore` 里（`*.key` / `*llm_key*`），绝不进版本库
# ══════════════════════════════════════════════════════════════
@app.get("/api/llm/key")
async def llm_key_status():
    """查询 LLM key 的**状态**（不回显 key 本身）。"""
    from analysis import coach as _c
    src = _c.key_source()
    return ok({
        "configured": src != "none",
        "source": src,                       # env / file / none
        "hint": _c.key_hint(),
        "base": (os.environ.get("SINGLENS_LLM_BASE")
                 or _c.DEFAULT_BASE),
        "model": (os.environ.get("SINGLENS_LLM_MODEL")
                  or _c.DEFAULT_MODEL),
        "enabled": bool(CONFIG.get("feedback", {}).get("ai_comment")),
    })


@app.post("/api/llm/key")
async def llm_key_save(req: Request):
    """保存 key，并**立刻真调一次**验证。

    请求体：`{"key": "sk-...", "enable": true}`
    `enable` 省略时默认 true（配 key 的意图就是要用它）。
    """
    from analysis import coach as _c
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是一个对象，形如 {\"key\": \"sk-...\"}。")
    key = body.get("key")
    if not isinstance(key, str) or not key.strip():
        return fail("BAD_REQUEST", "缺 key，或 key 不是字符串。")
    key = key.strip()
    if len(key) < 12:
        return fail("BAD_REQUEST", "这把 key 太短了，看着不像真的。")
    if any(ch in key for ch in " \t\r\n"):
        return fail("BAD_REQUEST", "key 里有空白字符，多半是复制时带进来的 —— "
                                   "请重新复制。")

    # ★ 先验证再保存：验证不通过就不落盘，避免"存了一把不能用的"
    good, why = _c.test_key(key)
    if not good:
        return fail("BAD_REQUEST", f"这把 key 用不了：{why}")
    if not _c.save_key(key):
        return fail("SERVER_ERROR", "key 写不进文件（检查 data/ 目录权限）。")

    # 配了 key 就默认打开开关 —— 否则用户会以为"配了但没生效"
    if body.get("enable", True):
        # ⚠️ 这里**不要**调 `save_config()` —— 那个函数**根本不存在**。
        #    项目的 CONFIG 是**内存态**（`PUT /api/config` 也只改内存），
        #    重启就回到默认值。所以设置页的开关本来就不持久化，
        #    本端点跟着同样处理即可（保持行为一致）。
        #    ⚠️ 但要如实说明：用户会以为"配好了会一直有效"，
        #       所以前端要提示"重启服务后需要重新配置"。
        CONFIG.setdefault("feedback", {})["ai_comment"] = True
    print(f"[SingLens] LLM key 已保存（{_c.key_source()}，"
          f"{_c.key_hint()}），AI 点评 = "
          f"{CONFIG.get('feedback', {}).get('ai_comment')}")
    return ok({"saved": True, "verified": True, "hint": _c.key_hint(),
               "enabled": bool(CONFIG.get("feedback", {}).get("ai_comment"))})


@app.post("/api/llm/key/test")
async def llm_key_test(req: Request):
    """测试当前 key 能不能用（不保存）。

    也接受 `{"key": "..."}` 来测一把**还没保存**的 —— 便于"先测再存"。
    """
    from analysis import coach as _c
    try:
        body = await _json(req)
    except Exception:  # noqa: BLE001
        body = {}
    k = body.get("key") if isinstance(body, dict) else None
    k = k.strip() if isinstance(k, str) and k.strip() else None
    good, why = _c.test_key(k)
    return ok({"ok": good, "reason": why,
               "hint": _c.key_hint() if k is None else None})


@app.delete("/api/llm/key")
async def llm_key_clear():
    """清掉保存的 key（**环境变量那份清不掉**，会如实说明）。"""
    from analysis import coach as _c
    src = _c.key_source()
    if src == "env":
        return fail("BAD_REQUEST",
                    "这把 key 来自环境变量 SINGLENS_LLM_KEY，"
                    "程序改不了它 —— 请在启动服务前 unset，或改用它启动。")
    removed = _c.clear_key()
    CONFIG.setdefault("feedback", {})["ai_comment"] = False
    return ok({"cleared": removed, "enabled": False})


# ══════════════════════════════════════════════════════════════
# ★ MIDI 导入（2026-10-07）
#
#   为什么做这个：作者要「唱我喜欢的歌，**并知道我走没走音**」。
#   从录音里提旋律已证走不通（§92/§93：PYIN 的单声源假设在多声源混音上失效）。
#   而 **MIDI 是符号数据** —— 旋律是**读出来的**，不是猜的。
#
#   ⚠️ 三条设计原则：
#     ① **自动提，但必须让用户确认** —— 真实 MIDI 的旋律轨可能挑错
#        （有些没有轨名），所以给出**全部轨的摘要 + 推荐**，让用户选/改
#     ② **能试听** —— 用户得能听见"提出的旋律"是不是他要唱的
#        （否则还是在盲选，跟"从混音提旋律"一个毛病）
#     ③ 不落库、不持久化 —— MIDI 只是"提出参考"的中间物
# ══════════════════════════════════════════════════════════════
MIDI_MAX_BYTES = 2 * 1024 * 1024          # 2MB —— MIDI 通常几十 KB


@app.post("/api/midi/parse")
async def midi_parse(file: UploadFile = File(...), track_index: Optional[int] = None,
                     preview: bool = True):
    """解析 MIDI：给出**轨清单 + 推荐 + 该轨的音符**（并可试听）。

    `track_index` 省略时用自动推荐的那一轨。

    ━━━ ⚠️ 这个端点是**无状态的**，而产品的流程**不用它** ━━━

    2026-10-15 的接口审计（`experiments/_audit_endpoints.py`）发现：
    **全仓库零引用** —— 前端、实验脚本、自检页、文档都没有调它。
    UI 走的是 `/api/midi/upload`（那个会落库、返回 `file_id`），
    因为**试听与换轨要能反复取同一份数据**：
    用户会点"换一轨"再听一遍，无状态版本意味着每次都要重新上传。

    ⇒ 保留它是给**一次性脚本**用的（不想在 `data/midi/` 留文件时），
      但**必须在这里写明**，否则下一个人会以为它是主路径、或者以为是漏接线
      （"后端有、前端没接"在这个项目里出现过四次，见待办 G4-a / G14 / 模式 C / B7）。
    ⚠️ **它不落库、不持久化** —— 与 `upload` 的差别就在这里。
    """
    import tempfile
    from analysis import midi_import as _mi
    raw = await file.read()
    if not raw:
        return fail("BAD_REQUEST", "文件是空的。")
    if len(raw) > MIDI_MAX_BYTES:
        return fail("TOO_LARGE",
                    f"MIDI 文件太大了（{len(raw) / 1024:.0f} KB）。"
                    f"正常的 MIDI 只有几十 KB —— 是不是传错文件了？", 413)
    if raw[:4] != b"MThd":
        return fail("BAD_REQUEST",
                    "这不是 MIDI 文件（开头不是 MThd）。"
                    "请确认传的是 .mid / .midi，而不是音频或歌词文件。")
    # 落临时文件（mido 要路径；BytesIO 它也支持但版本间行为不一致）
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
        f.write(raw)
        tmp = f.name
    try:
        info = _mi.summarize(tmp, track_index)
    except Exception as e:  # noqa: BLE001
        return fail("BAD_REQUEST", f"读不出这个 MIDI：{str(e)[:120]}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    notes = info.get("notes") or []
    if not notes and info.get("chosen_track") is not None:
        return fail("BAD_REQUEST",
                    f"轨 {info['chosen_track']} 里没有可用的音符。换一轨试试。")
    return ok(_midi_payload(info, preview=preview))


# ══════════════════════════════════════════════════════════════
# ⑤c 歌曲导入（2026-10-20）
# ══════════════════════════════════════════════════════════════
# 三步，**都不在请求里做重活**：
#   ① POST /api/song/inspect  上传 + 转 WAV + 读时长（几秒）
#   ② POST /api/song/analyze  起后台线程：Demucs 分离 → 原唱参考
#   ③ POST /api/song/confirm  用户试听/确认后，落成模板（带伴奏）
# 前端按 ①→②→轮询 status→③ 走；任何一步失败都保留可读的错误。
def _ffmpeg_to_wav44(src: str, dst: str) -> bool:
    """任意格式 → 44.1kHz 立体声 WAV（Demucs 要的格式）。"""
    try:
        from analysis.ffmpeg import find_ffmpeg
        ff = find_ffmpeg()
        if not ff:
            raise RuntimeError("没有 ffmpeg")
        p = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
             "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", dst],
            capture_output=True, text=True, timeout=300)
        return p.returncode == 0 and os.path.exists(dst)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ ffmpeg 转 WAV 失败（{type(e).__name__}: {e}），"
              f"改走 soundfile/librosa")
    try:
        import soundfile as sf
        y, sr = sf.read(src, dtype="float32", always_2d=True)
        if y.ndim == 1:
            y = np.stack([y, y], axis=1)
        if y.shape[1] == 1:
            y = np.repeat(y, 2, axis=1)
        if sr != 44100:
            y = librosa.resample(y.T, orig_sr=sr, target_sr=44100).T
        sf.write(dst, y[:, :2], 44100, subtype="PCM_16")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 读不了上传的歌曲：{e}")
        return False


def _song_bad(sid: str, msg: str, code: str = "BAD_REQUEST", status: int = 400):
    _song_set(sid, status="FAILED", stage="失败", progress=0, error=msg)
    return fail(code, msg, status)


@app.post("/api/song/inspect")
async def song_inspect(req: Request, file: UploadFile = File(...),
                       title: str = "", singer_id: str = ""):
    """上传歌曲，转成可分析的 WAV，返回时长与建议的练习区间。

    ⚠️ 这一步**不做分离**（可能要几十秒）—— 先让用户选"练哪一段"，
       再点「分析这一段」。否则用户传完 4 分钟的歌只能干等。
    """
    import io
    declared = req.headers.get("content-length")
    if declared:
        try:
            if int(declared) > SONG_MAX_SOURCE_BYTES + (1 << 20):
                return fail("TOO_LARGE",
                            f"歌曲文件太大了（>{SONG_MAX_SOURCE_BYTES // 1048576} MB）。"
                            f"请上传 MP3/M4A，或先剪出要练的一段。", 413)
        except ValueError:
            pass
    sid = f"song_{uuid.uuid4().hex[:12]}"
    d = _song_dir(sid)
    os.makedirs(d, exist_ok=True)
    raw_path = os.path.join(d, "source.bin")
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > SONG_MAX_SOURCE_BYTES:
            return fail("TOO_LARGE",
                        f"歌曲文件太大了（超过 {SONG_MAX_SOURCE_BYTES // 1048576} MB）。"
                        f"请上传 MP3/M4A，或先剪出要练的一段。", 413)
        buf.write(chunk)
    if total < 1024:
        return fail("BAD_REQUEST", "这个文件是空的。", 400)
    with open(raw_path, "wb") as f:
        f.write(buf.getvalue())
    wav = os.path.join(d, "source.wav")
    from starlette.concurrency import run_in_threadpool
    ok_conv = await run_in_threadpool(_ffmpeg_to_wav44, raw_path, wav)
    if not ok_conv or not os.path.exists(wav):
        return fail("BAD_AUDIO",
                    "这个音频解不开。支持 MP3 / M4A / WAV / FLAC；"
                    "如果是视频，先导出成音频。", 400)
    try:
        duration = _wav_duration(wav)
    except Exception as e:  # noqa: BLE001
        return fail("BAD_AUDIO", f"读不出音频时长（{e}）。", 400)
    if duration < SONG_MIN_SECTION_MS / 1000:
        return fail("TOO_SHORT",
                    f"这首歌太短了（不足 {SONG_MIN_SECTION_MS // 1000} 秒）。", 400)
    if duration > SONG_MAX_SOURCE_S:
        return fail("TOO_LONG",
                    f"这首歌有 {duration / 60:.1f} 分钟，超过单次上限 "
                    f"{SONG_MAX_SOURCE_S // 60} 分钟。请先剪掉前奏/尾奏。", 400)
    name = (title or os.path.splitext(os.path.basename(file.filename or ""))[0]
            or "未命名歌曲").strip()[:80]
    duration_ms = int(round(duration * 1000))
    # ★ 2026-10-20：默认**整首**。用户要的是"上传一首歌跟着唱整首"；
    #   想只练副歌时前端可以再选 30/60/90 秒。
    default_len = duration_ms
    start = 0
    meta = {"song_id": sid, "title": name, "duration_ms": duration_ms,
            "singer_id": singer_id, "created": time.time()}
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    try:
        os.remove(raw_path)          # 原始上传文件不再需要，WAV 已足够
    except OSError:
        pass
    with _SONG_LOCK:
        SONG_JOBS[sid] = {
            "song_id": sid, "singer_id": singer_id, "title": name,
            "duration_ms": duration_ms, "status": "INSPECTED",
            "stage": "已读取，默认整首；也可以只练一段", "progress": 0,
            "start_ms": start, "end_ms": start + default_len,
            "notes": [], "quality": None, "created": time.time(),
            "quality_mode": "clean",
        }
    return ok({
        "song_id": sid, "title": name, "duration_ms": duration_ms,
        "suggested": {"start_ms": start, "end_ms": start + default_len},
        "limits": {"min_ms": SONG_MIN_SECTION_MS,
                   "max_ms": min(SONG_MAX_SECTION_MS, duration_ms)},
    })


@app.post("/api/song/analyze")
async def song_analyze(req: Request):
    body = await _json(req)
    sid = str(body.get("song_id") or "")
    if not _song_id_ok(sid):
        return fail("BAD_REQUEST", "song_id 不对。", 400)
    job = _song_get(sid)
    if not job:
        return fail("NOT_FOUND", "这首歌的会话不在了（服务可能重启过）。"
                                 "重新上传一次即可。", 404)
    singer = str(body.get("singer_id") or "")
    if job.get("singer_id") and singer and job["singer_id"] != singer:
        return fail("FORBIDDEN", "这首歌不属于当前演唱者。", 403)
    if job.get("status") == "RUNNING":
        return fail("CONFLICT", "正在分析中，等这一遍完成再换区间。", 409)
    duration = int(job.get("duration_ms") or 0)
    try:
        start = int(body.get("start_ms", job.get("start_ms") or 0))
        end = int(body.get("end_ms", job.get("end_ms") or 0))
    except (TypeError, ValueError):
        return fail("BAD_REQUEST", "start_ms / end_ms 必须是整数。", 400)
    start = max(0, min(start, duration))
    end = max(0, min(end, duration))
    if end - start < SONG_MIN_SECTION_MS:
        return fail("BAD_REQUEST",
                    f"选得太短了：至少 {SONG_MIN_SECTION_MS // 1000} 秒。", 400)
    if end - start > SONG_MAX_SECTION_MS:
        return fail("BAD_REQUEST",
                    f"一次最多分析 {SONG_MAX_SECTION_MS // 60000} 分钟。"
                    f"整首超过这个长度请先剪掉前奏/尾奏。", 400)
    title = str(body.get("title") or job.get("title") or "").strip()[:80]
    if title:
        job["title"] = title
    qmode = str(body.get("quality") or body.get("quality_mode") or "clean").lower()
    if qmode not in ("fast", "clean"):
        return fail("BAD_REQUEST", "quality 只能是 fast / clean。", 400)
    _song_set(sid, status="QUEUED", stage="排队中…", progress=2,
              start_ms=start, end_ms=end, title=title,
              error=None, template_id=None, quality=None, notes=[],
              quality_mode=qmode)
    try:
        os.remove(os.path.join(_song_dir(sid), "job.json"))
    except OSError:
        pass
    threading.Thread(target=_run_song_job, args=(sid,), daemon=True).start()
    return ok(_song_public(_song_get(sid) or job))


@app.get("/api/song/library")
async def song_library(singer_id: str = ""):
    """E 屏「我上传过的歌」：已确认的歌曲模板 + 未确认的分析记录。

    ★ 为什么需要（2026-10-20 作者实测）：上传/分析后关掉页面再打开，
      E 屏只剩一个空文件框 —— 用户以为"上传的东西没了"。
      已确认的模板其实在 B 列表里，但用户不会去那里找；未确认的分析
      更是只活在内存在（服务重启就没了）。这里把两者一起列出来。
    """
    if not singer_id:
        return fail("BAD_REQUEST", "请带上 singer_id —— 上传记录是分人的。")
    tpls = []
    _seen_song = set()
    try:
        for t in templates.list_templates(singer_id, 200):
            if (t.get("source_kind") or "") != "song_vocal":
                continue
            meta = t.get("source_meta") or {}
            if meta.get("parent_template_id"):
                continue          # 微练习片段不是「上传过的整首歌」
            # 同一首歌重复上传/确认两次时只留最新一份（同标题+同起止）。
            key = (meta.get("song_title") or t.get("title"),
                   meta.get("start_ms"), meta.get("end_ms"))
            if key in _seen_song:
                continue
            _seen_song.add(key)
            tpls.append({
                "kind": "template",
                "template_id": t["template_id"],
                "title": t.get("title") or "歌曲参考",
                "duration_ms": t.get("duration_ms") or 0,
                "quality_level": t.get("quality_level"),
                "lyrics_count": t.get("lyrics_count") or 0,
                "has_vocal": bool(t.get("has_vocal")),
                "has_backing": bool(t.get("has_backing")),
                "source_meta": t.get("source_meta") or {},
                "created_at": t.get("created_at"),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 歌曲库读模板失败：{e}")
    jobs = []
    with _SONG_LOCK:
        for j in SONG_JOBS.values():
            if j.get("singer_id") and j["singer_id"] != singer_id:
                continue
            st = j.get("status")
            if st in ("CONFIRMED", "INSPECTED", "READY", "RUNNING", "FAILED"):
                jobs.append({
                    "kind": "job",
                    "song_id": j.get("song_id"),
                    "title": j.get("title") or "未命名歌曲",
                    "duration_ms": j.get("duration_ms") or 0,
                    "start_ms": j.get("start_ms") or 0,
                    "end_ms": j.get("end_ms") or 0,
                    "status": st, "stage": j.get("stage") or "",
                    "progress": j.get("progress") or 0,
                    "quality_level": (j.get("quality") or {}).get("level"),
                    "lyrics_count": len(j.get("lyrics") or []),
                    "created": j.get("created") or 0,
                })
    # 未确认的排前面（正在进行的），已确认的按创建时间
    jobs.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return ok({"templates": tpls, "jobs": jobs})


@app.get("/api/song/{sid}/status")
async def song_status(sid: str, singer_id: str = ""):
    if not _song_id_ok(sid):
        return fail("BAD_REQUEST", "song_id 不对。", 400)
    job = _song_get(sid)
    if not job:
        return fail("NOT_FOUND", "没有这首歌的分析记录（服务可能重启过）。", 404)
    if job.get("singer_id") and singer_id and job["singer_id"] != singer_id:
        return fail("FORBIDDEN", "这首歌不属于当前演唱者。", 403)
    return ok(_song_public(job))


@app.get("/api/song/{sid}/preview.wav")
async def song_preview(sid: str, kind: str = "instrumental", singer_id: str = ""):
    if not _song_id_ok(sid):
        return fail("BAD_REQUEST", "song_id 不对。", 400)
    job = _song_get(sid)
    if not job:
        return fail("NOT_FOUND", "没有这首歌的分析记录。", 404)
    if job.get("singer_id") and singer_id and job["singer_id"] != singer_id:
        return fail("FORBIDDEN", "这首歌不属于当前演唱者。", 403)
    if job.get("status") != "READY":
        return fail("CONFLICT", "这一段还没分析完。", 409)
    key = {"vocal": "vocals_path", "instrumental": "instrumental_path",
           "guide": "guide_path"}.get(kind or "")
    if not key:
        return fail("BAD_REQUEST", "kind 只能是 vocal / instrumental / guide。", 400)
    p = job.get(key)
    if not p or not os.path.exists(p):
        return fail("NOT_FOUND", "这个音轨不在了（可能被清理过）。", 404)
    return FileResponse(p, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})


@app.delete("/api/song/{sid}")
async def song_delete(sid: str, singer_id: str = ""):
    """删掉一首**未确认**的分析记录（确认后的模板用 DELETE /api/template）。"""
    if not _song_id_ok(sid):
        return fail("BAD_REQUEST", "song_id 不对。", 400)
    job = _song_get(sid)
    if not job:
        return fail("NOT_FOUND", "没有这首歌的分析记录。", 404)
    if job.get("singer_id") and singer_id and job["singer_id"] != singer_id:
        return fail("FORBIDDEN", "这首歌不属于当前演唱者。", 403)
    with _SONG_LOCK:
        SONG_JOBS.pop(sid, None)
    try:
        shutil.rmtree(_song_dir(sid), ignore_errors=True)
    except OSError:
        pass
    return ok({"deleted": True, "song_id": sid})


@app.post("/api/song/{sid}/confirm")
async def song_confirm(sid: str, req: Request):
    """用户试听、确认参考质量之后，把这一段落成**跟唱模板**。"""
    import shutil
    body = await _json(req)
    if not _song_id_ok(sid):
        return fail("BAD_REQUEST", "song_id 不对。", 400)
    job = _song_get(sid)
    if not job:
        return fail("NOT_FOUND", "没有这首歌的分析记录。", 404)
    if job.get("status") != "READY":
        return fail("CONFLICT", "还没分析完，不能开始跟唱。", 409)
    singer = str(body.get("singer_id") or job.get("singer_id") or "")
    if job.get("singer_id") and singer and job["singer_id"] != singer:
        return fail("FORBIDDEN", "这首歌不属于当前演唱者。", 403)
    q = job.get("quality") or {}
    force = bool(body.get("force"))
    if q.get("level") == "bad" and not force:
        return fail("LOW_QUALITY",
                    "参考质量不足，直接评分可能不准。"
                    + "；".join(q.get("flags") or [])
                    + "。确认要试的话，勾选「仍然使用」再点。", 409)
    notes = job.get("notes") or []
    if len(notes) < 8:
        return fail("LOW_QUALITY", "只提取到几个音，这一段不适合当参考。", 409)
    title = str(body.get("title") or job.get("title") or "这首歌").strip()[:80]
    if not title.endswith("原唱参考"):
        title = f"{title} · 原唱参考"
    # ★ 2026-10-20：自动歌词可以**人工改**。Whisper 对唱歌难免出错，
    #   用户从音乐 App 粘贴正确歌词比"识别错了只能忍着"有用得多。
    #   行数相同就沿用自动识别的时间；不同就按字数比例铺满整段。
    auto_lyr = job.get("lyrics") or []
    lyrics = auto_lyr
    lsrc = job.get("lyrics_source") or ""
    edited = str(body.get("lyrics_text") or "").strip()
    if edited:
        raw = [x.strip() for x in edited.splitlines() if x.strip()][:300]
        if raw:
            L = max(1000, int(job.get("end_ms") or 0) - int(job.get("start_ms") or 0))
            if len(raw) == len(auto_lyr):
                lyrics = [{"start_ms": int(auto_lyr[i].get("start_ms") or 0),
                           "end_ms": int(auto_lyr[i].get("end_ms") or 0),
                           "text": raw[i]} for i in range(len(raw))]
            else:
                total = sum(max(1, len(x)) for x in raw) or 1
                t = 0
                lyrics = []
                for x in raw:
                    d = max(600, int(round(L * max(1, len(x)) / total)))
                    lyrics.append({"start_ms": t, "end_ms": min(L, t + d),
                                   "text": x})
                    t += d
            lsrc = "user_edited"
    tid = f"tpl_{uuid.uuid4().hex[:12]}"
    rel = os.path.join("data", "backing", f"{tid}.wav")
    backing = os.path.join(ROOT, rel)
    rel_vocal = os.path.join("data", "backing", f"{tid}_vocal.wav")
    vocal_copy = os.path.join(ROOT, rel_vocal)
    os.makedirs(os.path.dirname(backing), exist_ok=True)
    try:
        shutil.copyfile(job["instrumental_path"], backing)
        # 原唱副本：报告页/计划卡的「听原唱这一段」要用；没有它只能听合成旋律。
        if job.get("vocals_path") and os.path.exists(job["vocals_path"]):
            shutil.copyfile(job["vocals_path"], vocal_copy)
        source_meta = {
            "song_title": job.get("title"),
            "song_id": sid,
            "start_ms": job.get("start_ms"),
            "end_ms": job.get("end_ms"),
            "duration_ms": job.get("duration_ms"),
            "section_ms": int(job.get("end_ms") or 0) - int(job.get("start_ms") or 0),
            "leak_db": (job.get("leak") or {}).get("leak_db"),
            "auto_extracted": True,
            "separation_mode": job.get("quality_mode") or "clean",
            "lyrics_count": len(lyrics),
            "lyrics_source": lsrc,
        }
        templates.save(
            notes, singer_id=singer or None, title=title,
            quality=q, source_kind="song_vocal",
            backing_path=rel, vocal_path=(rel_vocal if os.path.exists(vocal_copy) else None),
            lyrics=lyrics, lyrics_source=(lsrc or None),
            source_meta=source_meta, template_id=tid)
    except Exception as e:  # noqa: BLE001
        for p in (backing, vocal_copy):
            try:
                os.remove(p)
            except OSError:
                pass
        return fail("SERVER_ERROR", f"存成模板失败：{str(e)[:150]}", 500)
    _song_set(sid, template_id=tid, stage="已存成模板")
    # 模板已经有自己的伴奏/原唱副本；分析目录（source/vocals/instrumental/guide）
    # 可以删掉，别让每首歌在 data/songs 下留几十 MB 原始文件。
    try:
        shutil.rmtree(_song_dir(sid), ignore_errors=True)
    except OSError:
        pass
    _song_set(sid, status="CONFIRMED", vocals_path=None,
              instrumental_path=None, guide_path=None)
    return ok({
        "template_id": tid, "title": title, "note_count": len(notes),
        "quality": q, "song_id": sid,
        "lyrics_count": len(lyrics),
        "backing": f"/api/template/{tid}/guide.wav?singer_id={singer}",
    })


# ══════════════════════════════════════════════════════════════
# ⑤d GET /api/reference/{rid}/guide.wav  —— **练习时的伴奏/引导音轨**
# ══════════════════════════════════════════════════════════════
# ★★ 为什么必须有这个（2026-10-17，作者的原话）：
#
#   > 用户没办法听到伴奏，不知道从哪里开始唱，唱完还没有暂停键，没有引导性的东西
#
#   而代码里早就写着（`bindings.js` L281 / L2512）：
#     「产品决定：**要求戴耳机跟伴奏/节拍器唱**」
#   —— **可这个系统从来没给过用户一个音**。
#   它假设用户自己找伴奏、自己数拍子、自己知道什么时候开口。
#   ⇒ 这是「文档里写了、代码里没有」的产品版：**要求存在，功能不存在**。
#
#   没有引导音轨，"跟着唱"这件事根本不成立 —— 用户只能凭记忆清唱，
#   而清唱时 **tempo 和调都可能和参考差很远**，量出来的分自然也不可信。
#
# 返回：前 **4 拍预备**（清晰的节拍器音）+ 参考旋律（正弦，每音带包络）。
# ⚠️ 旋律本身也放出来（不是只给节拍）：用户要"跟着它唱"才对得上调。
#    如果只给节拍，用户仍然得自己找调 —— 而找错调会让音准分崩掉（§118 就是这样）。
#
# ⚠️ 缓存：合成一次写 `data/exports/guide_{rid}.wav`，之后直接发。
#    参考是静态的，不需要每次重算。
GUIDE_CACHE_DIR = os.path.join(ROOT, "data", "exports")
GUIDE_PRE = 2.0          # 预备多少秒（0.5 秒一拍 ⇒ 4 拍）
GUIDE_BEAT = 0.5


def _synth_guide(ref) -> "np.ndarray":
    """把参考旋律合成成一段可跟唱的 wav（float32，SR）。

    ⚠️ 用**正弦 + 起落包络**而不是方波/锯齿：方波的谐波太强，
       外放时会被人声麦克风收进去，把音高检测带偏（而产品要求戴耳机，
       但架不住有人外放）。正弦最不容易污染。
    """
    import numpy as np
    end_ms = max((n.end_ms for n in ref.notes), default=0)
    total = GUIDE_PRE + end_ms / 1000.0 + 0.4
    y = np.zeros(int(SR * total), dtype=np.float32)
    # 预备拍：4 下短促的"哒"（1000Hz，60ms，指数衰减）
    for k in range(4):
        t0 = k * GUIDE_BEAT
        i0 = int(t0 * SR)
        n = int(0.06 * SR)
        tt = np.arange(n) / SR
        env = np.exp(-tt * 55.0)
        y[i0:i0 + n] += (0.28 * np.sin(2 * np.pi * 1000.0 * tt) * env).astype(np.float32)
    # 旋律
    for nt in ref.notes:
        s = GUIDE_PRE + nt.start_ms / 1000.0
        e = GUIDE_PRE + nt.end_ms / 1000.0
        i0, i1 = int(s * SR), int(e * SR)
        n = max(i1 - i0, 1)
        tt = np.arange(n) / SR
        f = 440.0 * (2.0 ** ((nt.pitch_midi - 69) / 12.0))
        env = np.minimum(1.0, np.minimum(tt / 0.02, (n / SR - tt) / 0.05))
        env = np.clip(env, 0.0, 1.0)
        y[i0:i0 + n] += (0.22 * np.sin(2 * np.pi * f * tt) * env).astype(np.float32)
    peak = float(np.max(np.abs(y))) or 1.0
    if peak > 0.9:
        y = y * (0.9 / peak)
    return y


def _guide_path(rid: str, transpose: int = 0) -> str:
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in rid)
    suffix = f"_t{transpose}" if transpose else ""
    return os.path.join(GUIDE_CACHE_DIR, f"guide_{safe}{suffix}.wav")


@app.get("/api/reference/{rid}/guide.wav")
async def reference_guide(rid: str, transpose: int = 0):
    """练习用的引导音轨（4 拍预备 + 参考旋律）。

    ★ 客户端在**进入练习画面的那一刻**就放它，并用它的进度驱动
      「准备 → 3 2 1 → 唱」的提示。用户不需要自己数拍子、也不需要自己找调。
    ★ 2026-10-18：`transpose` 让引导音轨跟着参考移调；缓存按半音数分开。
    """
    from analysis.align import BUILTIN
    if rid not in BUILTIN:
        return fail("NOT_FOUND", f"没有这个内置曲目：{rid}", 404)
    t = max(-12, min(12, int(transpose or 0)))
    # ① 原调且已有预渲染的现成伴奏就直接发
    if t == 0:
        pre = os.path.join(GUIDE_CACHE_DIR, f"singalong_{rid.lower()}.wav")
        if os.path.exists(pre):
            try:
                with open(pre, "rb") as f:
                    return Response(content=f.read(), media_type="audio/wav",
                                    headers={"Cache-Control": "public, max-age=86400"})
            except OSError as e:
                print(f"[SingLens] ⚠️ 读预渲染伴奏失败（{rid}）：{e}，改用合成")
    # ② 否则合成一次并缓存（移调版本单独缓存）
    p = _guide_path(rid, t)
    if not os.path.exists(p):
        try:
            import soundfile as sf
            y = _synth_guide(_transpose_ref(load_builtin(rid), t))
            os.makedirs(GUIDE_CACHE_DIR, exist_ok=True)
            sf.write(p, y, SR, subtype="PCM_16")
        except Exception as e:  # noqa: BLE001
            return fail("INTERNAL",
                        f"这个曲目的伴奏合成失败（{type(e).__name__}）。"
                        f"你仍然可以清唱，只是没有预备拍。", 500)
    try:
        with open(p, "rb") as f:
            return Response(content=f.read(), media_type="audio/wav",
                            headers={"Cache-Control": "public, max-age=86400"})
    except OSError as e:
        return fail("INTERNAL", f"伴奏读不出来：{e}", 500)


def _template_guide_path(tid: str, transpose: int = 0,
                         kind: str = "backing") -> str:
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in tid)
    k = (kind or "").lower()
    ks = "_vocal" if k == "vocal" else ("_original" if k in ("original", "mix") else "")
    suffix = f"_t{transpose}" if transpose else ""
    return os.path.join(GUIDE_CACHE_DIR, f"guide_template_{safe}{ks}{suffix}.wav")


@app.get("/api/template/{tid}/guide.wav")
async def template_guide(tid: str, singer_id: str = "", transpose: int = 0,
                         kind: str = "backing"):
    """模板的引导音轨：4 拍预备 + **伴奏 / 原唱 / 原唱+伴奏 / 合成旋律**。

    ★ 2026-10-20：歌曲参考支持：
      · backing  = 纯伴奏（默认，用户跟伴奏唱）
      · original = **原唱 + 伴奏一起放**（用户说的「跟原曲」）
      · vocal    = 纯原唱（保留给调试/只想要人声的场景）
      都加 2 秒 4 拍预备；前端实时页在伴奏/原唱（带伴奏）间切。
    ★ `transpose`：只有合成旋律能移调；歌曲参考是原调音频，拒绝移调。
    """
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if singer_id and tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN",
                    f"这条模板属于 {tpl.singer_id}，不是 {singer_id}。", 403)
    t = max(-12, min(12, int(transpose or 0)))
    k = (kind or "backing").lower()
    if k == "mix":
        k = "original"
    if k not in ("backing", "vocal", "original"):
        return fail("BAD_REQUEST", "kind 只能是 backing / vocal / original。", 400)
    # ★ 2026-10-20：歌曲参考的模板有**真实伴奏/原唱**。此时放真实音频，
    #   不再用音符合成旋律（合成旋律只能当调性提示，听起来不像歌）。
    if tpl.backing_path or tpl.vocal_path:
        if t:
            return fail("BAD_REQUEST",
                        "歌曲参考的伴奏/原唱是原调，不能移调。"
                        "想换调请重新上传并选一段。", 400)
        bp = tpl.backing_path
        vp = tpl.vocal_path
        if k == "vocal":
            src = vp
        elif k == "original":
            src = bp or vp
        else:
            src = bp
        if not src:
            return fail("NOT_FOUND",
                        f"这条模板没有{'原唱' if k == 'vocal' else '伴奏'}副本。", 404)
        if not os.path.isabs(src):
            src = os.path.join(ROOT, src)
        if not os.path.exists(src):
            return fail("NOT_FOUND", "这首歌的音轨文件不在了。", 404)
        if vp and not os.path.isabs(vp):
            vp = os.path.join(ROOT, vp)
        p = _template_guide_path(tid, 0, k)
        if not os.path.exists(p):
            try:
                from analysis.song_import import make_guide
                # original：伴奏 + 原唱一起混；只有一条时退化成那一条。
                make_guide(src, p, vocal_path=(vp if (k == "original" and bp and vp) else None))
            except Exception as e:  # noqa: BLE001
                return fail("INTERNAL",
                            f"这首歌的音轨准备失败（{type(e).__name__}: {e}）。", 500)
        # ★ FileResponse 支持 Range —— 浏览器才能 seek。原来整块 Response
        #   的 seekable 是 [0,0]，切音轨时 currentTime 设不进去，只能从 0 放。
        return FileResponse(p, media_type="audio/wav",
                            headers={"Cache-Control": "no-store"})
    p = _template_guide_path(tid, t)
    if not os.path.exists(p):
        try:
            import soundfile as sf
            ref = _transpose_ref(templates.to_reference_melody(tpl), t)
            y = _synth_guide(ref)
            os.makedirs(GUIDE_CACHE_DIR, exist_ok=True)
            sf.write(p, y, SR, subtype="PCM_16")
        except Exception as e:  # noqa: BLE001
            return fail("INTERNAL",
                        f"这个模板的引导音轨合成失败（{type(e).__name__}）。"
                        f"你仍然可以清唱，只是没有预备拍。", 500)
    return FileResponse(p, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/template/{tid}/vocal.wav")
async def template_vocal(tid: str, singer_id: str = ""):
    """歌曲参考的**原唱人声**（报告页「听原唱这一段」）。

    ★ 2026-10-20：音符合成的示范只能听音高/节奏，听不出原唱怎么处理这一句；
      用户练歌最需要的恰恰是"原唱这一句怎么唱"。有原件就发原件。
    """
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if singer_id and tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN", f"这条模板属于 {tpl.singer_id}。", 403)
    p = tpl.vocal_path
    if not p:
        return fail("NOT_FOUND", "这条模板没有原唱人声（不是歌曲参考）。", 404)
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    if not os.path.exists(p):
        return fail("NOT_FOUND", "原唱人声文件不在了。", 404)
    return FileResponse(p, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/template/{tid}/backing.wav")
async def template_backing(tid: str, singer_id: str = ""):
    """歌曲参考的**纯伴奏**（不带 2 秒倒数；报告页按段落试听用）。"""
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if singer_id and tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN", f"这条模板属于 {tpl.singer_id}。", 403)
    p = tpl.backing_path
    if not p:
        return fail("NOT_FOUND", "这条模板没有伴奏音轨。", 404)
    if not os.path.isabs(p):
        p = os.path.join(ROOT, p)
    if not os.path.exists(p):
        return fail("NOT_FOUND", "伴奏文件不在了。", 404)
    return FileResponse(p, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/template/{tid}/segment.wav")
async def template_segment(tid: str, kind: str = "mix",
                           start_ms: int = 0, end_ms: int = 0,
                           singer_id: str = ""):
    """截一小段模板音频（原唱 / 伴奏 / 混在一起），给"听整句"用。

    ★ 为什么不能直接让 `<audio>` 播整首 `vocal.wav`：
      整首 3–5 分钟的 44.1k WAV 是 30–50 MB，浏览器要下载完/支持 Range
      才能 seek；实测用户点了「听原唱这一段」没有声音（要么在等几十 MB，
      要么 seek 失败）。这里在服务端截 2–6 秒，返回 ~200 KB，点了立刻响。
    """
    import io
    import soundfile as sf
    tpl = templates.load(tid)
    if tpl is None:
        return fail("NOT_FOUND", f"找不到模板 {tid}", 404)
    if singer_id and tpl.singer_id and singer_id != tpl.singer_id:
        return fail("FORBIDDEN", f"这条模板属于 {tpl.singer_id}。", 403)
    kind = (kind or "mix").lower()
    if kind not in ("vocal", "backing", "mix"):
        return fail("BAD_REQUEST", "kind 只能是 vocal / backing / mix。", 400)
    paths = {"vocal": tpl.vocal_path, "backing": tpl.backing_path}
    for k, p in paths.items():
        if p and not os.path.isabs(p):
            paths[k] = os.path.join(ROOT, p)
    if kind in ("vocal", "mix") and not (paths["vocal"]
                                        and os.path.exists(paths["vocal"])):
        if kind == "vocal":
            return fail("NOT_FOUND", "这条模板没有原唱副本。", 404)
        kind = "backing"
    if kind == "backing" and not (paths["backing"]
                                  and os.path.exists(paths["backing"])):
        return fail("NOT_FOUND", "这条模板没有伴奏副本。", 404)
    try:
        info = sf.info(paths["vocal"] if kind != "backing" else paths["backing"])
        dur_ms = int(round(info.frames / info.samplerate * 1000))
    except Exception:  # noqa: BLE001
        dur_ms = int(tpl.duration_ms or 0)
    a = max(0, min(int(start_ms), max(0, dur_ms - 200)))
    b = int(end_ms) if end_ms else a + 6000
    b = max(a + 200, min(b, dur_ms, a + 60_000))
    if b - a < 200:
        return fail("BAD_REQUEST", "这一段太短。", 400)

    def _read(p):
        y, sr = sf.read(p, start=int(a * sr0 / 1000),
                        stop=int(b * sr0 / 1000),
                        dtype="float32", always_2d=True)
        if y.ndim == 1:
            y = np.stack([y, y], axis=1)
        if y.shape[1] == 1:
            y = np.repeat(y, 2, axis=1)
        return y[:, :2], sr

    # 先拿采样率，再按采样率截（两次读同一文件，避免 librosa 重采样）
    try:
        sr0 = int(sf.info(paths["vocal"] if kind != "backing"
                          else paths["backing"]).samplerate)
    except Exception:  # noqa: BLE001
        sr0 = 44100
    try:
        if kind == "mix":
            v, sr = _read(paths["vocal"])
            bk, _ = _read(paths["backing"])
            n = min(len(v), len(bk))
            y = (v[:n] * 0.95 + bk[:n] * 0.55)
        else:
            y, sr = _read(paths[kind])
    except Exception as e:  # noqa: BLE001
        return fail("INTERNAL", f"截取这一段失败：{str(e)[:100]}", 500)
    peak = float(np.max(np.abs(y))) or 1.0
    if peak > 0.95:
        y = y * (0.95 / peak)
    buf = io.BytesIO()
    sf.write(buf, y, sr, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


def _render_midi_preview(notes: list, max_notes: int = 60) -> str:
    """把提出来的音符合成一段可播放的 wav，放到 `data/exports/`。

    ★ 为什么必须有：用户要确认"提出的旋律对不对"。
      只给他一张音符列表/一条曲线，**他判断不了**（除非他懂谱）。
      放一段声音给他听，是唯一可靠的确认方式。
    """
    import numpy as np
    import soundfile as sf
    ns = notes[:max_notes]
    if not ns:
        raise ValueError("没有音符")
    step = 0.45                       # 每个音至少占 0.45 秒（听得清）
    total = 0.3
    for n in ns:
        total += max(step, (n["end_ms"] - n["start_ms"]) / 1000.0)
    total += 0.6
    sr = SR
    buf = np.zeros(int(sr * total), dtype=np.float32)
    t = 0.3
    for n in ns:
        dur = max(step, (n["end_ms"] - n["start_ms"]) / 1000.0)
        f0 = 440.0 * (2.0 ** ((float(n["pitch_midi"]) - 69.0) / 12.0))
        ln = int(sr * dur * 0.82)
        tt = np.arange(ln) / sr
        y = (np.sin(2 * np.pi * f0 * tt)
             + 0.30 * np.sin(4 * np.pi * f0 * tt)
             + 0.12 * np.sin(6 * np.pi * f0 * tt))
        y /= max(1e-9, float(np.max(np.abs(y))))
        a = max(1, int(sr * 0.012))
        r = max(1, min(int(sr * 0.09), ln // 2))
        env = np.ones(ln)
        env[:a] = np.linspace(0, 1, a, endpoint=False)
        env[ln - r:] = np.linspace(1, 0, r)
        at = int(sr * t)
        seg = (y * env * 0.45).astype(np.float32)
        end = min(len(buf), at + len(seg))
        buf[at:end] += seg[:end - at]
        t += dur
    name = f"midi_preview_{uuid.uuid4().hex[:8]}.wav"
    out_dir = os.path.join(ROOT, "data", "exports")
    os.makedirs(out_dir, exist_ok=True)
    sf.write(os.path.join(out_dir, name), np.clip(buf, -1, 1), sr)
    # ★ 2026-10-10 改成**同源相对路径**（原为 `http://127.0.0.1:8010/{name}`）。
    #
    #   为什么必须改：那个 8010 是**另一个手工起的静态服务**
    #   （`python -m http.server 8010 --directory data/exports`），
    #   而主服务从来不挂 `data/exports` —— 于是只起 `uvicorn web.server:app`
    #   的时候，试听地址指向一个**没在监听的端口**，
    #   `<audio>` 只会安安静静地不出声（不报错、不弹提示）。
    #   这正是本项目最忌讳的形状：**功能看着做了，用户那儿是坏的**。
    #
    #   现在 `/exports` 由主服务自己挂（见文件末尾的 mount）：
    #   一个进程、一个源、一个端口，没有"顺手起个 8010"这种隐性前提。
    return f"/exports/{name}"


def _midi_payload(info: dict, preview: bool = True) -> dict:
    """把 `midi_import.summarize()` 的输出整理成**前端能直接用**的形状。

    ★ 2026-10-07 抽出来的理由：三个 MIDI 端点（`parse` / `upload` /
      `track`）原来**各写各的**，于是 `upload` 漏了"生成试听"这一步 ——
      而它恰恰是前端**最先调**的那个 ⇒ 用户拿不到试听。
      ⇒ **共用一处**，以后加字段不会再漏。

    三件事：
      ① 试听：把提出来的音符合成一段 wav（**用户必须能听才能确认**）
      ② 截断：音符太多会撑爆响应（有些 MIDI 几千个音），
         只回前 400 个给前端画图 —— 但 `note_count` 是**真实总数**
      ③ 失败**不静默**：试听合成坏了要能查出来（`preview_error`）
    """
    notes = info.get("notes") or []
    if preview and notes:
        try:
            info["preview_url"] = _render_midi_preview(notes)
        except Exception as _e:  # noqa: BLE001
            info["preview_error"] = f"{type(_e).__name__}: {str(_e)[:120]}"
            print(f"[SingLens] MIDI 试听合成失败：{info['preview_error']}")
    else:
        info.setdefault("preview_url", None)
    info["notes_preview"] = notes[:400]
    info["notes"] = info["notes_preview"]
    info["note_count"] = len(notes)
    return info


@app.post("/api/midi/confirm")
async def midi_confirm(req: Request):
    """确认某一轨 → **存成跟唱模板**（模式 B），之后就能直接用它练。

    请求体：`{"file_id": "...", "track_index": 2, "title": "不将就"}`

    ⚠️ 为什么不直接存"参考"而要绕过模板：
       本项目里"用户自己的参考"只有一处落点 —— `template` 表。
       复用它意味着**对齐/评分/教练/练习计划全都不用改**（§94.2）。
       代价是它和"跟唱模板"共表，所以 `title` 要写清楚来源。
    """
    from analysis import midi_import as _mi
    body = await _json(req)
    if not isinstance(body, dict):
        return fail("BAD_REQUEST", "请求体必须是对象。")
    fid = str(body.get("file_id") or "").strip()
    ti = body.get("track_index")
    title = str(body.get("title") or "").strip()[:60]
    if not fid:
        return fail("BAD_REQUEST", "缺 file_id。")
    src = os.path.join(ROOT, "data", "midi", f"{fid}.mid")
    if not os.path.exists(src):
        return fail("NOT_FOUND", "那个 MIDI 已经不在了 —— 请重新上传。", 404)
    try:
        ti = int(ti)
    except (TypeError, ValueError):
        return fail("BAD_REQUEST", "track_index 必须是整数。")
    notes = _mi.extract(src, ti)
    if len(notes) < 4:
        return fail("BAD_REQUEST",
                    f"轨 {ti} 只有 {len(notes)} 个可用音符，太少了，换一轨试试。")
    # ⚠️ `templates.save` 要的是 **`Note` 对象**，不是字典 ——
    #    它内部 `_notes_to_json` 读的是**属性**（`n.start_ms`）。
    #    第一版传了 dict，报 `'dict' object has no attribute 'start_ms'`。
    #
    #    `voiced_ratio` / `pitch_std` 对 MIDI 音符**没有意义**
    #    （它是符号数据，不存在"有没有发声""稳不稳"）——
    #    填 `None`，而 `_notes_to_json` 对 None 是安全的（getattr 兜底）。
    #    ⚠️ 同理**不传 `quality`**：`assess_template_quality` 是为
    #       "用户唱的稳不稳"设计的，拿它评价 MIDI 是错的。
    from analysis.notes import Note as _Note
    tpl_notes = [_Note(note_index=i, start_ms=int(n["start_ms"]),
                       end_ms=int(n["end_ms"]),
                       pitch_midi=float(n["pitch_midi"]),
                       boundary_source="midi")
                 for i, n in enumerate(notes)]
    # ⚠️ `templates.save` 返回的是 **tid 字符串**（不是对象）——
    #    第一版写成 `tpl.template_id` / `tpl.get(...)`，两样都会炸。
    #    它还要求 `hop_length` / `sr`：模板里存了这两个，
    #    因为 `stable_pitch` 的 hop 必须与提取时一致（见 templates.py 的注释）。
    try:
        tid = templates.save(tpl_notes, singer_id=body.get("singer_id"),
                             title=title or f"MIDI 导入 · 轨 {ti}",
                             hop_length=PRECISE_HOP, sr=SR)
    except Exception as e:  # noqa: BLE001
        return fail("SERVER_ERROR", f"模板存不下来：{str(e)[:100]}")
    if not tid:
        return fail("SERVER_ERROR", "模板存不下来。")
    return ok({"template_id": tid,
               "title": title or f"MIDI 导入 · 轨 {ti}",
               "note_count": len(notes),
               "duration_ms": notes[-1]["end_ms"]})


@app.post("/api/midi/upload")
async def midi_upload(file: UploadFile = File(...)):
    """只上传、先解析轨清单 —— 给前端做"选轨"界面的第一步。

    返回 `file_id`，后续 `parse` / `confirm` 都用它。
    """
    from analysis import midi_import as _mi
    raw = await file.read()
    if not raw or raw[:4] != b"MThd":
        return fail("BAD_REQUEST",
                    "这不是 MIDI 文件（开头不是 MThd）。请传 .mid / .midi。")
    if len(raw) > MIDI_MAX_BYTES:
        return fail("TOO_LARGE", "MIDI 文件太大了。", 413)
    fid = uuid.uuid4().hex[:12]
    d = os.path.join(ROOT, "data", "midi")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, f"{fid}.mid")
    with open(dst, "wb") as f:
        f.write(raw)
    info = _mi.summarize(dst)
    info["file_id"] = fid
    info["filename"] = file.filename or "MIDI"
    # ⚠️ 这里原来**没有**这一步 —— 而 `upload` 是前端**最先调**的接口，
    #    于是用户拿不到试听。⇒ 改用共用的 `_midi_payload`。
    return ok(_midi_payload(info))


@app.get("/api/midi/track/{fid}")
async def midi_track(fid: str, track_index: int):
    """换一轨重新解析（用户在前端改选之后）。"""
    from analysis import midi_import as _mi
    src = os.path.join(ROOT, "data", "midi", f"{fid}.mid")
    if not os.path.exists(src):
        return fail("NOT_FOUND", "那个 MIDI 已经不在了 —— 请重新上传。", 404)
    info = _mi.summarize(src, track_index)
    info["file_id"] = fid
    # 换轨之后**也要重新合成试听** —— 用户改选就是为了听另一轨
    return ok(_midi_payload(info))


# ══════════════════════════════════════════════════════════════
@app.get("/api/references")
async def list_references():
    """教材曲目清单。契约的 12 个端点里没列这一条，
    但前端 bootstrap() 要它才能填「模式 A 选哪首」。"""
    from analysis.align import BUILTIN, load_builtin
    out = []
    for rid, spec in BUILTIN.items():
        # ★ 2026-10-07 修：与 L1838 同一处错误 ——
        #   手算 `len(midi) × note_dur_ms` **漏了句间换气**。
        #   改用 `ReferenceMelody.duration_ms`（单一来源）。
        try:
            _dur = load_builtin(rid).duration_ms
        except Exception:  # noqa: BLE001
            _dur = len(spec["midi"]) * spec["note_dur_ms"]
        out.append({
            "reference_id": rid,
            "title": spec["title"],
            "note_count": len(spec["midi"]),
            "duration_ms": _dur,
            "source": "builtin_score",
            "license": spec["license"],     # 版权合规清单要求标明来源
            "notes": [{"index": i, "midi": m} for i, m in enumerate(spec["midi"])],
        })
    return ok(out)


# ══════════════════════════════════════════════════════════════
# 附加：延迟自测（验收指标 <200ms 的测量入口）
# ══════════════════════════════════════════════════════════════
@app.get("/api/latency")
async def latency():
    """报告实时管线实测到的服务端处理耗时。

    ⚠️ 这只是**分解项**。真正的端到端延迟还要加上网络往返与前端绘制，
    需要用 web/latency_test.py 在前端侧测。验收以那个数为准。
    """
    allproc = [p for s in SESSIONS.values() for p in s.rt.proc_ms]
    if not allproc:
        return ok({"samples": 0, "note": "还没有实时数据。唱一段再看。"})
    a = np.array(allproc)
    return ok({
        "samples": int(a.size),
        "server_proc_ms": {"p50": round(float(np.percentile(a, 50)), 2),
                           "p95": round(float(np.percentile(a, 95)), 2),
                           "max": round(float(a.max()), 2)},
        "note": "这是服务端处理耗时，不含网络与前端绘制。端到端延迟请用 latency_test.py。",
    })


# ══════════════════════════════════════════════════════════════
# 静态文件（同源提供前端，避免 CORS）
# ══════════════════════════════════════════════════════════════
WEB_DIR = os.path.join(ROOT, "web")

# ★ 站点图标（2026-09-24 加）。
#   为什么值得花这几行：浏览器**自动**请求 `/favicon.ico`，没有就 404，
#   于是**每个页面的控制台都有一条红色报错**。
#   后果不是"少个图标"，而是"**控制台常态有红字**" ——
#   真实错误混在噪声里，谁都不会再去看控制台（探针也因此误报过 3 条失败）。
#   用 SVG（红方块 + S，与侧栏 logo 同形），并在各页 `<head>` 里声明，
#   现代浏览器看到声明就不会再去要 `/favicon.ico`。
_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="10" fill="#c93a2a"/>'
    '<text x="32" y="45" font-family="Outfit,Helvetica,Arial,sans-serif" '
    'font-size="42" font-weight="800" fill="#ffffff" text-anchor="middle">S</text>'
    '</svg>'
)


@app.get("/favicon.svg")
async def favicon_svg():
    from fastapi.responses import Response
    return Response(content=_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.ico")
async def favicon_ico():
    """兜底：老浏览器/没声明 icon 的页面仍会来要 `.ico`，给同一份 SVG。

    ⚠️ Content-Type 是 image/svg+xml（不是 image/x-icon）—— 这是**故意的**：
       Chrome/Edge/Firefox 都按内容嗅探，SVG 能正常显示；
       而如果这里返回 404，控制台就会一直挂一条红色报错（见上）。
    """
    from fastapi.responses import Response
    return Response(content=_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/selftest.html")
async def selftest_page():
    """契约自检页（前端侧 71 项）。"""
    return FileResponse(os.path.join(WEB_DIR, "selftest.html"))


@app.get("/apitest.html")
async def apitest_page():
    """适配层测试页（真浏览器打真实后端）。"""
    return FileResponse(os.path.join(WEB_DIR, "apitest.html"))


@app.get("/collect")
async def collect_page(request: Request, token: str = ""):
    """录音采集页（实验用）。

    ★ 带 `?token=<操作者令牌>` 打开时，顺手种一个 cookie（`sl_op`）。
      为什么要有这一步：采集页里那些 fetch 是**页面自己**发的，如果要求
      每个请求都手动带 header，就得改一堆调用点；种了 cookie 之后
      "一次带令牌打开、后续自动带上"，操作者只需点控制台打印的那条链接。
      cookie 设成 SameSite=Strict + HttpOnly。（不是登录态，本服务没有账号。）
    """
    from fastapi.responses import FileResponse as _FR
    resp = _FR(os.path.join(WEB_DIR, "collect.html"))
    want = ""
    try:
        from web.experiment import operator_token, DISABLE_TOKEN
        want = "" if DISABLE_TOKEN else operator_token()
    except Exception:  # noqa: BLE001
        want = ""
    if want and token == want:
        resp.set_cookie("sl_op", token, httponly=True, samesite="strict",
                        max_age=30 * 24 * 3600)
    return resp


@app.get("/rate")
async def rate_page():
    """盲评页（实验用）。"""
    return FileResponse(os.path.join(WEB_DIR, "rate.html"))


@app.get("/experiment")
async def experiment_page():
    """采集进度页：直接返回 JSON，浏览器看即可。"""
    from web.experiment import connect, progress
    con = connect()
    try:
        return ok(progress(con))
    finally:
        con.close()


@app.get("/apitest")
async def apitest():
    """apiSource 适配层测试页（真浏览器里打真实后端）。"""
    return FileResponse(os.path.join(WEB_DIR, "apitest.html"))


@app.get("/login")
async def login_page(request: Request):
    """产品登录页。

    已登录访问 /login 直接回首页；未登录才显示。登录页本身不加载 SPA，
    避免在未认证时把整套应用 JS 和配置请求先跑一遍。
    """
    if not auth_mod.auth_enabled():
        return RedirectResponse("/", status_code=302)
    if auth_mod.user_from_request(request):
        return RedirectResponse("/", status_code=302)
    p = os.path.join(WEB_DIR, "login.html")
    if not os.path.exists(p):
        return JSONResponse({"ok": False, "error": {
            "code": "NOT_FOUND", "message": "登录页文件不存在。"}}, status_code=500)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/api/auth/me")
async def auth_me(request: Request):
    if not auth_mod.auth_enabled():
        return ok({"auth_enabled": False, "user": {
            "user_id": "local", "username": "local", "role": "admin",
            "singer_id": None, "display_name": "本机", "must_change_password": False}})
    user = auth_mod.user_from_request(request)
    if not user:
        return fail("UNAUTHORIZED", "请先登录。", 401)
    return ok({"auth_enabled": True, "user": user})


@app.post("/api/auth/login")
async def auth_login(request: Request):
    if not auth_mod.auth_enabled():
        return fail("CONFLICT", "当前服务未启用登录。", 409)
    if not _auth_same_origin(request):
        return fail("FORBIDDEN", "拒绝了跨站登录请求。", 403)
    body = await _json(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    remember = bool(body.get("remember"))
    ip = (request.client.host if request.client else "") or ""
    key = auth_mod.rate_key(ip, username)
    if auth_mod.attempts_left(key) <= 0:
        return fail("TOO_MANY", "尝试次数过多，请 10 分钟后再试。", 429)
    user = auth_mod.authenticate(username, password)
    if not user:
        auth_mod.record_failure(key)
        return fail("UNAUTHORIZED", "账号或密码对不上，再试一次。", 401)
    auth_mod.clear_failures(key)
    ttl = auth_mod.REMEMBER_TTL if remember else auth_mod.SESSION_TTL
    token = auth_mod.create_session(
        user["user_id"], ttl=ttl, ip=ip,
        user_agent=request.headers.get("user-agent") or "")
    resp = JSONResponse({"ok": True, "data": {"user": user}})
    resp.set_cookie(
        auth_mod.COOKIE_NAME, token, max_age=ttl,
        httponly=True, samesite="lax", secure=auth_mod.cookie_secure(), path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/auth/register")
async def auth_register_status(request: Request):
    """登录页用它决定是否显示“创建练习账号”。

    默认只允许回环地址注册；局域网开放必须显式设 SINGLENS_REGISTER=open。
    """
    if not auth_mod.auth_enabled():
        return ok({"allowed": False, "mode": "closed", "reason": "登录未启用。"})
    ip = (request.client.host if request.client else "") or ""
    allowed = auth_mod.register_allowed(ip)
    reason = ("本机可以创建练习账号。"
              if allowed else "注册只在本机开放；局域网账号由管理员发放。")
    return ok({"allowed": allowed, "mode": auth_mod.register_mode(),
               "reason": reason})


@app.post("/api/auth/register")
async def auth_register(request: Request):
    """创建“演唱者 + 账号”并直接登录。

    ⚠️ 默认 loopback：只有本机能注册。局域网要开放，必须显式设置
       `SINGLENS_REGISTER=open`，并且自己承担同网段开号的风险。
    """
    if not auth_mod.auth_enabled():
        return fail("CONFLICT", "当前服务未启用登录。", 409)
    ip = (request.client.host if request.client else "") or ""
    if not auth_mod.register_allowed(ip):
        return fail("FORBIDDEN", "注册只在本机开放；局域网账号由管理员发放。", 403)
    if not _auth_same_origin(request):
        return fail("FORBIDDEN", "拒绝了跨站注册请求。", 403)
    body = await _json(request)
    name = str(body.get("name") or "").strip()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    confirm = str(body.get("confirm") or "")
    if not name:
        return fail("BAD_REQUEST", "先给自己起一个练习名字。", 400)
    if len(name) > SINGER_NAME_MAX:
        return fail("BAD_REQUEST", f"名字太长了（{len(name)} > {SINGER_NAME_MAX} 字）。", 400)
    if not auth_mod.USERNAME_RE.match(username):
        return fail("BAD_REQUEST", "账号只允许字母/数字/下划线/点/短横线，3–32 位。", 400)
    if len(password) < auth_mod.MIN_PASSWORD:
        return fail("BAD_REQUEST", f"密码至少 {auth_mod.MIN_PASSWORD} 位。", 400)
    if confirm and password != confirm:
        return fail("BAD_REQUEST", "两次输入的密码不一致。", 400)
    if auth_mod.get_user_by_username(username):
        return fail("CONFLICT", "这个账号已经被占用了。", 409)
    sid = f"u_{uuid.uuid4().hex[:8]}"
    if practice_store.name_taken(name, sid):
        return fail("CONFLICT", f"已经有一位练习者叫「{name}」了。", 409)
    created_singer = False
    try:
        practice_store.upsert_singer(sid, name)
        created_singer = True
        user = auth_mod.create_user(
            username=username, password=password, role="singer",
            singer_id=sid, display_name=name)
    except (ValueError, sqlite3.IntegrityError) as e:
        if created_singer:
            try:
                practice_store.delete_singer(sid)
            except Exception:
                pass
        return fail("CONFLICT", f"创建账号失败：{e}", 409)
    except Exception as e:  # noqa: BLE001
        if created_singer:
            try:
                practice_store.delete_singer(sid)
            except Exception:
                pass
        return fail("INTERNAL", f"创建账号失败：{e}", 500)
    token = auth_mod.create_session(
        user["user_id"], ttl=auth_mod.SESSION_TTL, ip=ip,
        user_agent=request.headers.get("user-agent") or "")
    resp = JSONResponse({"ok": True, "data": {"user": user}})
    resp.set_cookie(
        auth_mod.COOKIE_NAME, token, max_age=auth_mod.SESSION_TTL,
        httponly=True, samesite="lax", secure=auth_mod.cookie_secure(), path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = auth_mod.token_from_cookie_header(request.headers.get("cookie", ""))
    auth_mod.revoke_session(token)
    resp = JSONResponse({"ok": True, "data": {"logged_out": True}})
    resp.delete_cookie(auth_mod.COOKIE_NAME, path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/password")
async def auth_password(request: Request):
    if not auth_mod.auth_enabled():
        return fail("CONFLICT", "当前服务未启用登录。", 409)
    user = auth_mod.user_from_request(request)
    if not user:
        return fail("UNAUTHORIZED", "请先登录。", 401)
    body = await _json(request)
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    full = auth_mod.get_user_by_id(user["user_id"])
    if not full or not auth_mod.verify_password(current, full["password_hash"]):
        return fail("UNAUTHORIZED", "当前密码不正确。", 401)
    try:
        auth_mod.set_password(user["user_id"], new)
    except ValueError as e:
        return fail("BAD_REQUEST", str(e), 400)
    auth_mod.revoke_user_sessions(user["user_id"])
    resp = JSONResponse({"ok": True, "data": {
        "changed": True, "message": "密码已更新，请重新登录。"}})
    resp.delete_cookie(auth_mod.COOKIE_NAME, path="/")
    return resp


@app.get("/")
async def index(request: Request):
    """产品入口。

    ★ B1-1（2026-09-20）之前这里返回的是 `selftest.html`（接口自检页）——
      因为当时还没有产品前端。现在 `web/index.html` 已就位（v7 拆分而来），
      所以 `/` 指向它；自检页移到 `/selftest.html`（本来就挂着）。
      若 index.html 不存在，退回自检页，不至于白屏。

    ★ 2026-10-28：启用登录后，未登录先回 `/login`。
      只保护产品入口；`/collect`、`/rate` 仍按原来的操作者令牌走。
    """
    if auth_mod.auth_enabled() and not auth_mod.user_from_request(request):
        return RedirectResponse("/login", status_code=302)
    for name in ("index.html", "selftest.html"):
        p = os.path.join(WEB_DIR, name)
        if os.path.exists(p):
            return FileResponse(p)
    return JSONResponse(
        {"ok": True, "data": {"service": "SingLens API", "docs": "/docs"}})


class _NoCacheStatic(StaticFiles):
    """静态文件：**每次都要回服务端确认**（`Cache-Control: no-cache`）。

    ★ 2026-10-07 加。为什么必须：
      作者改了代码后，浏览器**一直用缓存的旧 JS** ——
      他看到的教练卡是"一坨原始 JSON"（旧代码不会解析结构化），
      而后端、服务吐出的 JS、浏览器实测**三处都是对的**。
      排查花了很久，因为**三个环节看起来都没问题**。

    ⚠️ `no-cache` 不是"不缓存" —— 它只要求**每次带 ETag 回服务端确认**，
       没变仍然返回 304（不重传）。开发期与答辩现场都该这样：
       改了代码刷新就该生效，而不必教用户按 Ctrl+F5。

    ⚠️ 为什么不用"给 URL 加版本号"：那要改 `index.html` 与全部
       `import`（视图是动态 import 的）—— 动静大、易漏。
       而这个项目**每次上线都是就地重启**，`no-cache` 正好够。
    """

    def file_response(self, *args, **kwargs):  # noqa: D102
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


if os.path.isdir(os.path.join(WEB_DIR, "js")):
    app.mount("/js", _NoCacheStatic(directory=os.path.join(WEB_DIR, "js")),
              name="js")
if os.path.isdir(os.path.join(WEB_DIR, "fonts")):
    app.mount("/fonts", _NoCacheStatic(directory=os.path.join(WEB_DIR, "fonts")),
              name="fonts")
if os.path.isdir(os.path.join(WEB_DIR, "css")):
    app.mount("/css", _NoCacheStatic(directory=os.path.join(WEB_DIR, "css")),
              name="css")

# ★ MIDI 试听的落点（2026-10-10）。
#   为什么挂在**主服务**上：`_render_midi_preview()` 生成的试听 wav 放在
#   `data/exports/`，而它以前只由**另一个手工起的 8010 静态服务**提供 ——
#   那个进程没起时，试听地址就是个死链，`<audio>` 不报错也不出声。
#   挂在这里，"起一个服务"就等于"试听可用"，没有第二个前提。
#   ⚠️ 与 /js /css 同样用 `_NoCacheStatic`：预览文件名带随机后缀，本来就不会撞，
#      但保持"静态资源一律回服务端确认"这一条一致，免得下次有人以为这里可以例外。
_EXPORTS_DIR = os.path.join(ROOT, "data", "exports")
os.makedirs(_EXPORTS_DIR, exist_ok=True)
app.mount("/exports", _NoCacheStatic(directory=_EXPORTS_DIR), name="exports")


async def _startup_body():
    warm_up(SR)
    _cleanup_song_jobs()
    _load_song_jobs()
    # ★ 把磁盘上的练习记录读回内存。放在 warm_up 之后：预热要 40 多秒，
    #   用户在这期间打开页面也该看得到历史。
    load_practice_history()
    try:
        from web import practice_store as _ps
        _r = _ps.cleanup_orphan_session_audio(
            SESSION_AUDIO_DIR,
            os.path.join(ROOT, "data", "backups", "orphan_session_audio"))
        if _r["moved"]:
            print(f"[SingLens] 已移走 {_r['moved']} 个无记录引用的练习录音"
                  f"（{_r['bytes'] / 1048576:.1f} MB）")
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 孤儿录音清理跳过：{e}")
    try:
        auth_mod.init_db()
        auth_mod.cleanup_sessions()
        if auth_mod.auth_enabled():
            n_users = len(auth_mod.list_users())
            print(f"[SingLens] 登录已开启：{n_users} 个账号；入口 /login", flush=True)
            if n_users == 0:
                print("[SingLens] ⚠️ 还没有账号：先运行 "
                      ".venv\\Scripts\\python.exe tools\\accounts.py init", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 账号表初始化失败：{e}")
    print(f"[SingLens] 预热完成。实时窗口 {RT_WINDOW_MS}ms，"
          f"pitch {RT_PITCH_HZ}Hz / metrics {RT_METRICS_HZ}Hz", flush=True)


# ★ 用 lifespan 而不是 `@app.on_event("startup")`（2026-09-24）。
#   为什么这次顺手改了：`on_event` 已废弃，**每次启动都在控制台刷一段
#   DeprecationWarning**，而启动横幅里正好有操作者要用的令牌链接 ——
#   噪声挤在有用的信息旁边，人就不看了。
#   （FastAPI 0.141 里 on_event 仍能用，所以这不是必须改的；
#     但它只有三行，且改完控制台干净。）
from contextlib import asynccontextmanager as _asynccontextmanager  # noqa: E402


@_asynccontextmanager
async def _lifespan(_app):
    await _startup_body()
    yield


app.router.lifespan_context = _lifespan


# ★ 契约 §3.2 要求**所有**响应走统一包 {ok,data} / {ok,error}。
#   但 FastAPI 的 HTTPException 默认会包成 {"detail": ...} —— 不一致。
#   实测：请求一个不存在的会话，返回的是
#     {"detail":{"code":"NOT_FOUND","message":"..."}}
#   而不是契约要求的 {"ok":false,"error":{...}}。这里统一转回来。
# ⚠️ 必须注册在 Starlette 的**基类**上，不能只注册 fastapi.HTTPException。
#    路由未匹配时（比如请求一个不存在的端点），Starlette 抛的是基类，
#    只注册子类会漏掉 —— 实测 bootstrap 因此拿到 {"detail":"Not Found"}
#    而不是契约要求的 {"ok":false,"error":{...}}，前端解包后报 UNKNOWN。
@app.exception_handler(StarletteHTTPException)
async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
    d = exc.detail
    if isinstance(d, dict) and "code" in d:
        return JSONResponse(status_code=exc.status_code,
                            content={"ok": False, "error": d})
    # 按 status 映射语义码（2026-09-24，审计 §5.5）。
    # 原来一律 `BAD_REQUEST` —— 于是**路由未命中**返回的是
    # `HTTP 404 + code:"BAD_REQUEST"`，而契约 §3.4 里 4xx 的业务码是有语义的：
    # 前端按码分支时会走错分支（把"这个端点不存在"当成"你参数写错了"）。
    code = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "TOO_LARGE",
        422: "BAD_REQUEST",
    }.get(exc.status_code, "BAD_REQUEST")
    msg = str(d)
    # 422 的 detail 是 FastAPI 的校验明细（一长串结构），原样回给用户没法看。
    # 它是**给开发者**的，用户只需要知道"参数不对"。明细进日志。
    if exc.status_code == 422:
        print(f"[SingLens] 422 参数校验失败 {request.url.path}：{msg[:300]}")
        msg = "请求参数不合法（字段名/类型/范围）。"
    return JSONResponse(status_code=exc.status_code,
                        content={"ok": False,
                                 "error": {"code": code, "message": msg}})


@app.exception_handler(Exception)
async def _any_exc_handler(request: Request, exc: Exception):
    """兜底：**只回固定文案 + 追踪 id**，细节进服务端日志（2026-09-24，审计 §5.6）。

    ⚠️ 原来是 `"message": str(exc)` —— 实测泄露过：
       · 服务端**绝对路径**（`Error opening 'D:\\nodejs\\project\\毕设\\data\\…'`）
       · SQLite 绑定错误（`Error binding parameter 3: type 'dict' is not supported`）
       · Python 内部异常（`'int' object has no attribute 'get'`）
       · **内存地址**（`<_io.BytesIO object at 0x0000014C…>`）
       这些对用户毫无用处，对攻击者是免费的内部结构情报。
       追踪 id 保留可排查性：用户报错时把 id 报给我们，日志里能查到完整堆栈。
    """
    tid = uuid.uuid4().hex[:12]
    print(f"[SingLens] ❌ 未捕获异常 {tid} {request.method} {request.url.path}："
          f"{type(exc).__name__}: {exc}")
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500,
                        content={"ok": False,
                                 "error": {"code": "INTERNAL",
                                           "message": f"服务内部错误（追踪号 {tid}）。"
                                                      f"这次操作没有完成，请重试；"
                                                      f"若反复出现请把追踪号告诉我们。"}})


def _lan_candidates():
    """列出可用于局域网访问的地址，**按可用性排序**。

    ⚠️ 为什么不能只用 socket.connect(("8.8.8.8", 80)) 那一招：
       实测这台机器上，那一招返回的是 **PPP 宽带连接的公网 IP（115.46.x.x）**，
       而不是无线网卡的 192.168.1.10 —— 因为默认路由走的是 PPPoE。
       同学用手机连的是 WiFi，必须给 192.168.x.x 那个。

    所以改成读 ipconfig，按网卡名字判断：
       优先  无线 / WLAN / Wi-Fi / 以太网
       排除  VMware / VirtualBox / Hyper-V / PPP / 宽带 / 回环 / 169.254.*
    """
    import re
    import subprocess
    cands = []
    try:
        r = subprocess.run(["ipconfig"], capture_output=True, text=True,
                           encoding="gbk", errors="replace")
        cur = ""
        for line in (r.stdout or "").splitlines():
            if "适配器" in line or "adapter" in line.lower():
                cur = line.strip().rstrip(":")
                continue
            # ★ 只认「IPv4 地址」那一行。
            #   退化成"行里任意 IP"会把【默认网关】和【DNS 服务器】也收进来
            #   （实测 192.168.1.1 这个网关会被误当成候选地址）。
            m = re.search(r"IPv4[^:]*:\s*([0-9.]+)", line)
            if not m:
                continue
            ip = m.group(1)
            if not re.fullmatch(r"(\d+\.){3}\d+", ip):
                continue
            if ip.startswith(("127.", "169.254.", "0.", "255.")):
                continue
            bad = re.search(r"VMware|VirtualBox|Hyper-V|Loopback|PPP|宽带|虚拟", cur, re.I)
            good = re.search(r"WLAN|Wi-?Fi|无线|以太网|Ethernet", cur, re.I)
            if bad:
                score = 0
            elif good:
                score = 2
            elif ip.startswith(("192.168.", "10.")):
                score = 1
            else:
                score = 0
            cands.append((score, ip, cur))
    except Exception:
        pass
    # 去重 + 按分数排序
    seen, out = set(), []
    for sc, ip, name in sorted(cands, key=lambda x: -x[0]):
        if ip in seen:
            continue
        seen.add(ip)
        out.append((sc, ip, name))
    if not out:
        out = [(1, "127.0.0.1", "（没找到网卡，只能本机访问）")]
    return out


def _safe_bind_hosts() -> list[str]:
    """默认要监听的地址：**回环 + 所有私网/链路本地 IPv4**。

    ★ 为什么要绑多个（2026-09-24，用户反馈"前端打不开、没有能互动的组件"）：
      原来默认**只绑一个**地址（挑出来的那个局域网 IP）。于是"换一个地址打开"
      （127.0.0.1、机器名、另一张网卡）**连 TCP 都连不上** ——
      浏览器显示的是"无法访问此网站"，用户只会以为前端坏了。
      实测我自己就踩了：习惯用 `--host 127.0.0.1` 起服务，
      而用户拿的是局域网 IP 或机器名的链接。

    ★ 为什么不是 `0.0.0.0`：这台机器通过 PPPoE 有一个**公网 IP**，
      绑 0.0.0.0 等于把服务放到公网上（§早先实测：启动几秒内就被境外 IP 扫到）。
      本服务没有鉴权，能上传/分析音频、能下载实验录音 —— 不能那么做。
      所以规则是"**所有私网地址都绑，公网地址一个都不绑**"。
    """
    import ipaddress
    out = ["127.0.0.1"]
    for ip in sorted(_all_local_ips()):
        if ":" in ip:            # IPv6 链路本地要 scope id 才能绑，跳过（回环已覆盖 IPv6）
            continue
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if a.is_private or a.is_link_local:      # 私网 + 169.254.x.x
            if ip not in out:
                out.append(ip)
    return out


async def _serve_all(hosts: list[str], port: int, app_) -> None:
    """在一个事件循环里同时监听多个地址。某个地址绑不上只警告，不终止。"""
    import uvicorn

    servers = []
    for i, h in enumerate(hosts):
        cfg = uvicorn.Config(app_, host=h, port=port, log_level="info")
        srv = uvicorn.Server(cfg)
        # 只让第一个接管 Ctrl+C；其余靠下面的镜像任务一起退。
        srv.install_signal_handlers = (i == 0)
        servers.append((h, srv))

    async def _mirror_exit():
        while True:
            await asyncio.sleep(0.3)
            if any(s.should_exit for _h, s in servers):
                for _h, s in servers:
                    s.should_exit = True
                return

    tasks = [asyncio.create_task(s.serve(), name=f"serve-{h}") for h, s in servers]
    tasks.append(asyncio.create_task(_mirror_exit(), name="mirror-exit"))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for (h, _s), r in zip(servers, results):
        if isinstance(r, Exception):
            # 最常见的失败就是"地址不属于这台机器"（网卡换了/拔了）
            print(f"  ⚠️ 监听 {h}:{port} 失败（{type(r).__name__}: {r}）—— 跳过这个地址",
                  flush=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None,
                    help="监听地址。**默认绑回环 + 所有私网地址**（自己用 127.0.0.1、"
                         "同学的手机用局域网 IP 都能连），公网地址一个都不绑。"
                         "传具体地址则只绑它；传 0.0.0.0 会绑所有网卡（公网也能访问，不安全）")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()

    # ★ 行缓冲（2026-09-24）。为什么必须有：
    #   stdout 被**重定向到文件**时 Python 用块缓冲 ⇒ 启动横幅（里面是
    #   **操作者令牌链接**）会一直卡在缓冲区里，操作者看不到它，
    #   后面所有"要令牌"的接口就都无从下手 —— 页面上只说"需要令牌"，
    #   而令牌在哪没人知道。
    #   （实测：`Start-Process -RedirectStandardOutput` 之后文件是空的。）
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    cands = _lan_candidates()
    if a.host is None:
        bind_hosts = _safe_bind_hosts()
    else:
        bind_hosts = [a.host]
    # ★ 把"合法 Host 名单"按**真实绑定情况**算出来（2026-09-24，审计 §3.1）。
    #   除了绑定的地址，机器名与所有本机网卡地址也放进去 ——
    #   用户完全可能用 `http://<机器名>:8000/` 打开（见 `_local_hostnames`）。
    init_network_guard(bind_hosts[0], a.port,
                       extra_hosts=bind_hosts + [ip for _sc, ip, _n in cands])
    print()
    print("  " + "=" * 64)
    print("  SingLens 服务已启动")
    print("  " + "=" * 64)
    loop = [h for h in bind_hosts if h.startswith("127.")]
    lan = [h for h in bind_hosts if not h.startswith("127.")]
    # ⚠️ "同学的手机用"这一行要用 **_lan_candidates() 挑出来的那个**
    #   （按"无线/以太网优先、排除 PPP/虚拟网卡"排序），
    #   不能直接用 `bind_hosts` 里的第一个 —— 排序后第一个常常是
    #   169.254.x.x 这种链路本地地址，手机连不上（2026-09-24 实测：
    #   横幅一度把 169.254.211.66 写成"同学的手机用"，是错的）。
    best_lan = next((ip for sc, ip, _n in cands if sc >= 1 and ip in lan), None) \
        or (lan[0] if lan else None)
    print(f"    你自己用      http://127.0.0.1:{a.port}")
    if best_lan:
        print(f"    同学的手机用  http://{best_lan}:{a.port}/rate")
    else:
        print("    ⚠️ 只绑了回环 —— 同学用手机访问不了（加 --host 0.0.0.0 或指定局域网 IP）")
    print(f"    正在监听      {'、'.join(loop + lan)}:{a.port}")
    # ★ 采集页需要**操作者令牌**（2026-09-24，审计 §3.2）。
    #   直接打印一条**带令牌的链接** —— 操作者点它就行，不用手抄 16 位十六进制。
    try:
        from web.experiment import operator_token, DISABLE_TOKEN
        if DISABLE_TOKEN:
            print(f"    采集页        http://127.0.0.1:{a.port}/collect")
            print("    ⚠️ 操作者令牌被环境变量关掉了（SINGLENS_NO_OPERATOR_TOKEN=1）")
        else:
            tok = operator_token()
            print(f"    采集页     →  http://127.0.0.1:{a.port}/collect?token={tok}")
            if best_lan:
                print(f"    （手机/别的设备用 http://{best_lan}:{a.port}/collect?token={tok}）")
            print("    （采集/导出要这个令牌；同学的 /rate **不需要**。"
                  "令牌也在 data/operator_token.txt）")
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠️ 生成操作者令牌失败：{type(e).__name__}: {e}")
    if lan:
        print()
        print("    局域网地址（同学用手机连同一个 WiFi 后访问）：")
        for k, (sc, ip, name) in enumerate(cands):
            if ip not in lan:
                continue      # ★ 只列**真的在监听**的地址（没绑的地址列出来只会误导）
            mark = "★ 用这个" if ip == best_lan else ""
            print(f"      http://{ip}:{a.port}/rate{'':<4}{mark}")
            if name:
                print(f"        └ {name[:52]}")
        print()
        print("    ⚠️ 第一次连可能被 Windows 防火墙拦，弹窗要选「允许访问」")
        print("    ⚠️ 采集页（含录音）在手机上用不了 —— 浏览器录音必须 https。")
        print("       手机录音请用自带录音机 + import_phone_recordings.py")
    print("  " + "=" * 64)
    print()

    asyncio.run(_serve_all(bind_hosts, a.port, app))
