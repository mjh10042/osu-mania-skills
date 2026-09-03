"""Where files that ship *inside* the executable live at runtime.

PyInstaller unpacks a onefile build into a temporary directory and points `sys._MEIPASS`
at it, so nothing that ships with the app can be found relative to the source tree once
it is frozen. Everything bundled - msd.exe, the window icon - resolves through here.

This is the opposite of `beatmap.cache_dir()`, which is where the app writes what it
learns. Read-only and shipped goes here; personal and accumulated goes there.
"""
from __future__ import annotations

import sys
from pathlib import Path


def bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def bundled(*parts: str) -> Path:
    return bundle_dir().joinpath(*parts)
