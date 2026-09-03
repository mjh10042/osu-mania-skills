"""Where osu! stable actually lives on this machine.

%LOCALAPPDATA%/osu! is only the default. The installer will put the game on any
drive, and the Songs folder moves separately from the game, so neither path can be
assumed for anybody but the person who happened to accept every default.

Two things make the real location discoverable without asking:
  * stable registers its file associations with a full path to osu!.exe, so the
    registry knows the install root even for a custom install;
  * stable records where Songs went in `osu!.<windows user>.cfg`, as either a plain
    folder name under the install root or an absolute path on another drive.

Neither exists for a portable copy, which is why `set_root` and the folder picker
behind it are the last resort rather than the only path.
"""
from __future__ import annotations

import os
from pathlib import Path

# Any of stable's associations carries the same path; osz is the one every install has.
_ASSOCIATIONS = ("osustable.File.osz", "osustable.Uri.osu", "osustable.File.osr")


def _from_registry() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for name in _ASSOCIATIONS:
        try:
            key = f"Software\\Classes\\{name}\\shell\\open\\command"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                command = winreg.QueryValueEx(k, "")[0]
        except OSError:
            continue
        # '"C:\...\osu!.exe" "%1"' - the executable is the first quoted argument.
        exe = command.split('"')[1] if command.startswith('"') else command.split(" ")[0]
        p = Path(exe).parent
        if (p / "osu!.exe").is_file():
            return p
    return None


def _saved_root() -> Path | None:
    from .settings import load  # deferred: settings reads the cache dir, which lives in beatmap

    saved = load().get("osu_root")
    if saved and Path(saved).is_dir():
        return Path(saved)
    return None


def osu_root() -> Path | None:
    """The osu! stable install folder, or None if it cannot be found."""
    override = os.environ.get("MANIA_SKILLS_OSU_ROOT")
    if override and Path(override).is_dir():
        return Path(override)
    saved = _saved_root()
    if saved:
        return saved
    found = _from_registry()
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / "osu!" / "osu!.exe").is_file():
        return Path(local) / "osu!"
    return None


def set_root(path: Path | None) -> None:
    from .settings import update

    update(osu_root=str(path) if path else None)


def is_root(path: Path) -> bool:
    """Does this folder look like an osu! stable install? Used to vet a hand-picked one."""
    return (path / "osu!.exe").is_file() or (path / "scores.db").is_file()


def _beatmap_directory(root: Path) -> str | None:
    """The BeatmapDirectory line from the cfg belonging to the current Windows user."""
    user = os.environ.get("USERNAME") or ""
    candidates = [root / f"osu!.{user}.cfg"] if user else []
    candidates += sorted(root.glob("osu!.*.cfg"))
    for cfg in candidates:
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "BeatmapDirectory" and value.strip():
                return value.strip()
    return None


def songs_dir() -> Path | None:
    override = os.environ.get("MANIA_SKILLS_SONGS")
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    root = osu_root()
    if root is None:
        return None
    configured = _beatmap_directory(root)
    if configured:
        # Absolute means another drive entirely; a bare name is relative to the install.
        p = Path(configured)
        p = p if p.is_absolute() else root / configured
        if p.is_dir():
            return p
    p = root / "Songs"
    return p if p.is_dir() else None


def scores_db() -> Path | None:
    override = os.environ.get("MANIA_SKILLS_SCORES_DB")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    root = osu_root()
    if root is None:
        return None
    p = root / "scores.db"
    return p if p.is_file() else None


def lazer_dir() -> Path | None:
    """Lazer's data folder, which holds scores in a realm database this cannot read."""
    for var, name in (("APPDATA", "osu"), ("LOCALAPPDATA", "osulazer")):
        base = os.environ.get(var)
        if base and (Path(base) / name / "client.realm").is_file():
            return Path(base) / name
    return None
