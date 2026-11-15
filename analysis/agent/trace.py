"""Agent 运行轨迹：一次运行一行 JSONL。

为什么要留：Agent 出问题时，答案是最后才看到的，**真正能定位的是中间那几步**
——它选了哪个工具、参数是什么、工具返回了什么、哪一步开始跑偏。

文件在 `data/agent_traces/YYYY-MM-DD.jsonl`，已 gitignore（含歌词与用户问题）。
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(ROOT, "data", "agent_traces")


def write(run_id: str, uid: str, question: str, payload: dict) -> str:
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, time.strftime("%Y-%m-%d") + ".jsonl")
    row = {"run_id": run_id, "uid": uid, "ts": round(time.time(), 3),
           "question": (question or "")[:400], **payload}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read(run_id: str, days: int = 7) -> dict | None:
    """按日期倒序找；找不到返回 None（前端显示"已滚掉"而不是报错）。"""
    if not os.path.isdir(DIR):
        return None
    # ⚠️ 先过滤再切片：目录里还有 _script.json / _server.log 这类临时文件，
    #    `_` 的 ASCII 排在日期后面、reverse 后跑到最前；先切 7 个会把 .jsonl
    #    全挤掉，于是"明明写了 trace 却读不到"（2026-11-12 实测踩到）。
    names = [n for n in sorted(os.listdir(DIR), reverse=True)
             if n.endswith(".jsonl")][:days]
    for name in names:
        try:
            with open(os.path.join(DIR, name), encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("run_id") == run_id:
                        return row
        except OSError:
            continue
    return None


def _files() -> list[str]:
    if not os.path.isdir(DIR):
        return []
    return [os.path.join(DIR, n) for n in os.listdir(DIR) if n.endswith(".jsonl")]


def purge(uid: str | None = None) -> int:
    """删掉某个会话的轨迹；`uid=None` 时删全部。返回删除的行数。

    隐私清理（`/api/privacy/purge`）必须覆盖这里 —— trace 里有用户问题、
    歌词原文和工具结果，不删的话"已清空"就是假话。
    """
    removed = 0
    for path in _files():
        if uid is None:
            try:
                with open(path, encoding="utf-8") as f:
                    removed += sum(1 for line in f if line.strip())
                os.remove(path)
            except OSError:
                continue
            continue
        kept: list[str] = []
        dropped = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        kept.append(line if line.endswith("\n") else line + "\n")
                        continue
                    if row.get("uid") == uid:
                        dropped += 1
                    else:
                        kept.append(line if line.endswith("\n") else line + "\n")
        except OSError:
            continue
        if not dropped:
            continue
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp, path)
            removed += dropped
        except OSError:
            continue
    return removed


def cleanup(max_age_days: int = 7) -> int:
    """删掉超过 N 天的整天文件。返回删除的文件数。"""
    if not os.path.isdir(DIR):
        return 0
    cutoff = time.time() - max(1, int(max_age_days)) * 86400
    removed = 0
    for path in _files():
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed
