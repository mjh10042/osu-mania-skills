"""The one small json file that survives between launches.

Kept in its own module because several unrelated things want to persist a value
(language, user ids, a hand-picked osu! folder) and each of them writing the whole
file would silently drop the others' keys.
"""
from __future__ import annotations

import json

from .beatmap import cache_dir


def _path():
    return cache_dir() / "settings.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def update(**values) -> None:
    """Merge keys into the file. A read-only cache dir is not worth an error."""
    data = load()
    data.update(values)
    try:
        _path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
