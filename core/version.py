"""The one place the version number is written down.

`version.txt` (the Windows version resource) and the window title both have to carry it,
and a release where those two disagree is worse than one with no version at all, so
scripts/release.py checks them against this.
"""
from __future__ import annotations

VERSION = "0.2.0"
STAGE = "beta"
LABEL = f"{VERSION} ({STAGE})" if STAGE else VERSION
