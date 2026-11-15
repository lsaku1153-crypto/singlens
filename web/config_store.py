"""`CONFIG` 的持久化。

为什么要有
----------
`PUT /api/config` 原来只改内存里的 `CONFIG`，重启后全部回默认值。
用户会认为"设置没保存"——尤其是 AI 文字点评、评分宽容度这种改过就忘的项。

边界
----
· **只接受已知键、且类型与默认值一致**：配置文件是可以手改的，不能让它把
  一个字符串塞进数值项，然后在业务里算得莫名其妙；
· `privacy.used_bytes` / `privacy.sample_count` 是**运行时统计**，不落盘
  （每次启动由 `_refresh_privacy()` 重算）；
· 写盘用"临时文件 + `os.replace`"：中途崩溃不会留下半截 JSON；
· 读失败/文件损坏一律当作"没有配置"，**不能因此让服务起不来**。
"""
from __future__ import annotations

import json
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "config.json")

# 运行时统计，不持久化
_RUNTIME_ONLY = {("privacy", "used_bytes"), ("privacy", "sample_count")}


def _same_type(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return False


def merge(defaults: dict, raw) -> dict:
    """只收已知组、已知键、类型一致的值。其它一律忽略。"""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for group, patch in raw.items():
        base = defaults.get(group)
        if not isinstance(base, dict) or not isinstance(patch, dict):
            continue
        picked = {}
        for key, value in patch.items():
            if (group, key) in _RUNTIME_ONLY:
                continue
            if key in base and _same_type(base[key], value):
                picked[key] = value
        if picked:
            out[group] = picked
    return out


def load(defaults: dict, path: str | None = None) -> dict:
    """返回可合并的补丁；文件不存在/损坏时返回 `{}`。"""
    p = path or PATH
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    return merge(defaults, raw)


def save(config: dict, path: str | None = None) -> bool:
    """原子写。返回是否成功；失败只影响"下次启动记不住"，不影响当前运行。"""
    p = path or PATH
    data: dict = {}
    for group, values in (config or {}).items():
        if not isinstance(values, dict):
            continue
        data[group] = {k: v for k, v in values.items()
                       if (group, k) not in _RUNTIME_ONLY}
    folder = os.path.dirname(p) or "."
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False
