"""Local osu! score database (scores.db) as a third score source.

Both online sources only expose a *top* list - osu! by pp, which exists on ranked maps
only, and mamesosu by its own pp. Dan is decided by clears on graveyard dan courses, so
the online pools structurally miss the plays that matter most.

The stable client writes every passed play to `scores.db` regardless of which server it
was submitted to, so it is a local census rather than a top-N: easy clears are in there
too. That matters because the dan model averages the best 20 clears per skillset, so
extra low plays are inert while the missing high ones are exactly what was wrong.

What it cannot do: it only holds plays made on this machine with the stable client, and
only for maps still installed (the join is by .osu md5). It also records whoever was
logged in, so a shared PC needs `players` - see `players_seen`.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .beatmap import LocalSongs
from .sources import Play, _rate_from_stable_mods

MANIA = 3

# Target Practice is the one mod that appends an extra field to a score record.
MOD_TARGET = 1 << 23


def find_scores_db() -> Path | None:
    from .osu_install import scores_db

    return scores_db()


class _Reader:
    """osu!'s little-endian binary format, with .NET's 7-bit-length-prefixed strings."""

    def __init__(self, blob: bytes) -> None:
        self.b = blob
        self.i = 0

    def u8(self) -> int:
        v = self.b[self.i]
        self.i += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.b, self.i)[0]
        self.i += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.b, self.i)[0]
        self.i += 4
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.b, self.i)[0]
        self.i += 8
        return v

    def string(self) -> str:
        if self.u8() == 0x00:
            return ""
        n = shift = 0
        while True:
            byte = self.u8()
            n |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        s = self.b[self.i:self.i + n].decode("utf-8", "replace")
        self.i += n
        return s


def mania_accuracy(n300: int, n100: int, n50: int, geki: int, katu: int, miss: int) -> float:
    """osu!mania accuracy as osu! itself reports it (ScoreV1 weighting).

    A 320 (geki) and a 300 count the same here. The ScoreV2 formula that raises the
    ceiling to 320 reads ~1.6 points lower and would silently under-credit every clear;
    checked against 52 plays visible from both this file and the online APIs, where V1
    lands within 0.21 points on average and V2 within 1.60.
    """
    total = n300 + n100 + n50 + geki + katu + miss
    if not total:
        return 0.0
    got = 300 * (geki + n300) + 200 * katu + 100 * n100 + 50 * n50
    return got / (300 * total)


def _raw_scores(blob: bytes) -> list[dict]:
    r = _Reader(blob)
    r.i32()  # db version
    out = []
    for _ in range(r.i32()):
        r.string()  # beatmap md5, repeated inside every score below
        for _ in range(r.i32()):
            mode = r.u8()
            r.i32()      # score version
            md5 = r.string()
            player = r.string()
            r.string()   # replay md5
            hits = tuple(r.u16() for _ in range(6))
            r.i32()      # score
            r.u16()      # max combo
            r.u8()       # perfect
            mods = r.i32()
            r.string()   # always empty
            stamp = r.i64()
            r.i32()      # always -1
            r.i64()      # online score id
            if mods & MOD_TARGET:
                r.i64()  # extra double, only ever present here
            if mode == MANIA:
                out.append({"md5": md5.lower(), "player": player, "mods": mods,
                            "hits": hits, "stamp": stamp})
    return out


# .NET ticks are 100ns units since year 1; only used to order plays, never displayed
# as a real date, so the epoch shift is left out.
def _played_at(ticks: int) -> str:
    return str(ticks)


def _metadata(path: str) -> tuple[str, str]:
    title = version = "?"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("Title:"):
                    title = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                elif line.startswith("[HitObjects]"):
                    break
    except OSError:
        pass
    return title, version


def read_local_scores(songs: LocalSongs, path: Path | None = None,
                      players: set[str] | None = None) -> list[Play]:
    """Every mania play in scores.db whose beatmap is still installed.

    Only the best accuracy per (beatmap, rate) is kept: the dan model credits a chart
    once, by its best clear, so the other attempts can only cost rating time.
    """
    path = path or find_scores_db()
    if path is None:
        return []
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    try:
        raw = _raw_scores(blob)
    except (struct.error, IndexError):
        return []  # unknown db revision - fall back to the online sources alone

    lowered = {p.casefold() for p in players} if players else None

    best: dict[tuple[str, float], dict] = {}
    for s in raw:
        if lowered is not None and s["player"].casefold() not in lowered:
            continue
        if s["md5"] not in songs.by_md5:
            continue
        rate, _ = _rate_from_stable_mods(s["mods"])
        acc = mania_accuracy(*s["hits"])
        key = (s["md5"], rate)
        if key not in best or acc > best[key]["acc"]:
            best[key] = {"acc": acc, **s}

    plays = []
    for (md5, rate), s in best.items():
        beatmap_id = int(songs.id_by_md5.get(md5) or 0)
        if not beatmap_id:
            continue  # unsubmitted map: no dan and no way to look one up
        title, version = _metadata(songs.by_md5[md5])
        _, mods = _rate_from_stable_mods(s["mods"])
        plays.append(Play(
            source="local",
            beatmap_id=beatmap_id,
            md5=md5,
            title=title,
            version=version,
            accuracy=s["acc"],
            pp=0.0,
            rate=rate,
            mods=mods,
            played_at=_played_at(s["stamp"]),
            score_id=f"local:{md5}@{rate:.2f}",
            # scores.db order is (300, 100, 50, geki, katu, miss); geki is the 320 and
            # katu the 200, which is the pair osu!'s own accuracy formula treats as equal
            # and wife3 does not.
            judgements=dict(zip(("great", "ok", "meh", "perfect", "good", "miss"),
                                (int(v) for v in s["hits"]))),
            # od is filled in when the chart is rated - it lives in the .osu, not the db.
        ))
    return plays


def players_seen(path: Path | None = None) -> dict[str, int]:
    """Play count per player name, so a shared PC can be noticed rather than averaged in."""
    path = path or find_scores_db()
    if path is None:
        return {}
    try:
        raw = _raw_scores(path.read_bytes())
    except (OSError, struct.error, IndexError):
        return {}
    counts: dict[str, int] = {}
    for s in raw:
        counts[s["player"]] = counts.get(s["player"], 0) + 1
    return counts
