"""osu!mania .osu file fetching and conversion to MinaCalc note rows."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

OSU_FILE_URL = "https://osu.ppy.sh/osu/{beatmap_id}"
UA = "mania-skills/1.0 (personal skillset merger)"


def cache_dir() -> Path:
    """Where this copy keeps everything it has learned.

    The frozen build deliberately does *not* write next to the .exe. What accumulates
    here is personal - osu!/mamesosu ids, the play history, and an index of every
    beatmap folder on the machine - so a cache beside the exe means the exe cannot be
    handed to anyone without handing over that too, and remembering to delete it first
    is a step that will eventually be forgotten. Keeping it under LOCALAPPDATA makes the
    distributable exactly one file, by construction. It also survives the exe being
    dropped somewhere unwritable, like Program Files.
    """
    override = os.environ.get("MANIA_SKILLS_CACHE")
    if override:
        d = Path(override)
    elif getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or Path.home()
        d = Path(base) / "mania-skills" / "cache"
    else:
        d = Path(__file__).resolve().parent.parent / "cache"
    (d / "osu").mkdir(parents=True, exist_ok=True)
    return d


def fetch_osu_file(beatmap_id: int, session: requests.Session | None = None) -> str | None:
    """Download a .osu file by beatmap id, with on-disk caching. None if unavailable."""
    path = cache_dir() / "osu" / f"{beatmap_id}.osu"
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8", errors="replace")

    sess = session or requests
    for attempt in range(3):
        try:
            r = sess.get(OSU_FILE_URL.format(beatmap_id=beatmap_id),
                         headers={"User-Agent": UA}, timeout=30)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200 and r.text.strip():
            path.write_text(r.text, encoding="utf-8")
            return r.text
        if r.status_code == 404:
            return None
        time.sleep(1.5 * (attempt + 1))
    return None


def find_songs_dir() -> Path | None:
    """Locate the installed osu! Songs folder, if there is one."""
    from .osu_install import songs_dir

    return songs_dir()


_BEATMAP_ID_RE = re.compile(rb"^BeatmapID:\s*(\d+)", re.M)


class LocalSongs:
    """beatmap id / md5 -> path index over the installed osu! Songs folder.

    osu.ppy.sh serves .osu files at roughly 2/s per IP no matter how many connections
    are opened, so on a first run this index is the difference between a minute and
    nothing. The scan is persisted and only redone when the .osu file count changes.
    """

    def __init__(self, songs_dir: Path | None = None) -> None:
        self.root = songs_dir or find_songs_dir()
        self.by_id: dict[str, str] = {}
        self.by_md5: dict[str, str] = {}
        # scores.db identifies a chart only by md5, so this is the join back to a
        # beatmap id - the one thing mania-tracker's dan lookup needs.
        self.id_by_md5: dict[str, str] = {}
        self.path = cache_dir() / "songs.json"

    def load(self, rescan: bool = True) -> "LocalSongs":
        if self.root is None:
            return self
        files = list(self.root.rglob("*.osu"))
        saved = self._read_saved()
        if (saved and saved.get("root") == str(self.root)
                and saved.get("count") == len(files) and "id_by_md5" in saved):
            self.by_id = saved.get("by_id") or {}
            self.by_md5 = saved.get("by_md5") or {}
            self.id_by_md5 = saved.get("id_by_md5") or {}
            return self
        if rescan:
            self._scan(files)
        return self

    def _read_saved(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _scan(self, files: list[Path]) -> None:
        def read(p: Path):
            try:
                blob = p.read_bytes()
            except OSError:
                return None
            m = _BEATMAP_ID_RE.search(blob)
            return (m.group(1).decode() if m else "",
                    hashlib.md5(blob).hexdigest(), str(p))

        with ThreadPoolExecutor(max_workers=16) as pool:
            for row in pool.map(read, files):
                if row is None:
                    continue
                bid, md5, path = row
                if bid and bid != "0":
                    self.by_id[bid] = path
                    self.id_by_md5[md5] = bid
                self.by_md5[md5] = path

        self.path.write_text(json.dumps({
            "root": str(self.root), "count": len(files), "by_id": self.by_id,
            "by_md5": self.by_md5, "id_by_md5": self.id_by_md5}), encoding="utf-8")

    def read(self, beatmap_id: int, md5: str | None = None) -> str | None:
        """The .osu text for a play, by md5 first and only then by beatmap id.

        BeatmapID is not unique on disk. Rate-changer packs are built by copying a chart
        and editing its timing, and the copy keeps the original's BeatmapID - this machine
        has 169 ids shared by 469 files, one of them by 66 unrelated charts. Trusting the
        id meant a play on `Sahara [_]` was rated with `[Sahara 1.45x]`, +8.5 MSD of
        difficulty the player never touched. The md5 is the file, so it goes first.
        """
        path = (self.by_md5.get(md5.lower()) if md5 else None) or self.by_id.get(str(beatmap_id))
        if not path:
            return None
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


_SECTION_RE = re.compile(r"^\[(.+?)\]\s*$")


def parse_mania_rows(osu_text: str) -> tuple[int, list[dict], float]:
    """Parse a mania .osu into (key_count, rows, overall_difficulty).

    Each row is {"notes": <column bitmask>, "time": <seconds>} which is exactly the
    InputNote shape MinaCalc (msd.exe) expects. Notes sharing a timestamp are merged
    into a single row; LN heads count as taps and LN tails are ignored, matching
    MinaCalc's row-based model.
    """
    section = None
    mode = None
    key_count = 0
    od = 0.0
    raw: list[tuple[int, int]] = []  # (time_ms, column)

    for line in osu_text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue

        if section == "General" and line.startswith("Mode:"):
            mode = int(line.split(":", 1)[1].strip())
        elif section == "Difficulty" and line.startswith("CircleSize:"):
            key_count = int(float(line.split(":", 1)[1].strip()))
        elif section == "Difficulty" and line.startswith("OverallDifficulty:"):
            # Only used for the wife3 estimate, never for MSD - MinaCalc has no idea
            # osu! judgement windows exist.
            od = float(line.split(":", 1)[1].strip())
        elif section == "HitObjects":
            parts = line.split(",")
            if len(parts) < 4:
                continue
            try:
                x = int(float(parts[0]))
                t = int(float(parts[2]))
            except ValueError:
                continue
            raw.append((t, x))

    if mode != 3 or key_count <= 0 or not raw:
        return key_count, [], od

    rows: dict[int, int] = {}
    for t, x in raw:
        col = int(math.floor(x * key_count / 512))
        col = min(max(col, 0), key_count - 1)
        rows[t] = rows.get(t, 0) | (1 << col)

    notes = [{"notes": mask, "time": t / 1000.0} for t, mask in sorted(rows.items())]
    return key_count, notes, od
