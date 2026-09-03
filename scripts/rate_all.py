"""Fetch both servers' plays, compute MSD for each, and dump to cache/plays.json.

    python scripts/rate_all.py <osu! name or id> [mamesosu name or id]

Ids are arguments rather than constants: this file used to carry the author's own, which
is exactly the sort of thing that ends up in a public repository. Falls back to whatever
the app itself last saved.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.beatmap import cache_dir
from core.ratings import MsdCache, rate_play
from core.sources import _session, fetch_mamesosu, fetch_official, fetch_official_skills


def _ids() -> tuple[str, str]:
    from core import settings

    args = sys.argv[1:]
    if args:
        # An explicit name means "this player", so the mamesosu side is only filled in
        # when it is given too. Falling back to the saved one here would quietly pool one
        # person's osu! plays with another's mamesosu plays.
        return args[0], (args[1] if len(args) > 1 else "")
    saved = settings.load()
    osu = str(saved.get("osu_id") or "")
    if not osu:
        sys.exit("usage: python scripts/rate_all.py <osu! name or id> "
                 "[mamesosu name or id]")
    return osu, str(saved.get("mame_id") or "")


def main() -> None:
    osu_id, mame_id = _ids()
    sess = _session()
    print(f"fetching scores for {osu_id}...")
    official = fetch_official(osu_id, sess)
    mame = fetch_mamesosu(mame_id, 100, sess) if mame_id else []
    print(f"  official={len(official)}  mamesosu={len(mame)}")

    cache = MsdCache()
    plays = official + mame
    stats = Counter()
    for i, p in enumerate(plays, 1):
        status = rate_play(p, cache, sess)
        stats[status.split(":")[0]] += 1
        print(f"  [{i:3}/{len(plays)}] {p.source:9} {p.mods:6} {status:14} {p.label[:48]}")
        if i % 10 == 0:
            cache.save()
    cache.save()

    print("\nstatus:", dict(stats))

    out = [asdict(p) for p in plays if p.msd]
    (cache_dir() / "plays.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} rated plays -> {cache_dir() / 'plays.json'}")

    truth = fetch_official_skills(osu_id, sess)
    (cache_dir() / "truth.json").write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    print("wrote mania-tracker ground-truth ratings -> truth.json")


if __name__ == "__main__":
    main()
