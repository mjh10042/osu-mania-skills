"""Validate the scores.db parser against the online pools, then show what it adds.

The online sources and scores.db describe the same plays from different sides, so any
map present in both is a free correctness check on the md5 join, the mod bits and the
mania accuracy formula. Run it after touching core/local_scores.py.

Two differences are expected and not bugs:
  * local accuracy reads slightly higher, because the servers publish the best *pp*
    score for a map while this picks the best *accuracy* one;
  * plays carrying the ScoreV2 mod differ by up to 0.7 points, because the server
    reports those on a formula that matches neither ScoreV1 nor ScoreV2 accuracy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.beatmap import LocalSongs, cache_dir  # noqa: E402
from core.dan import DAN_SKILLS, credited_dan, split_skillsets  # noqa: E402
from core.local_scores import read_local_scores  # noqa: E402
from core.ratings import MsdCache, rate_plays  # noqa: E402


def formulas(hits) -> dict[str, float]:
    n300, n100, n50, geki, katu, miss = hits
    total = n300 + n100 + n50 + geki + katu + miss
    if not total:
        return {"v1": 0.0, "v2": 0.0}
    return {  # v1 scores a 320 and a 300 identically; v2 raises the ceiling to 320
        "v1": (300 * (geki + n300) + 200 * katu + 100 * n100 + 50 * n50) / (300 * total),
        "v2": (320 * geki + 300 * n300 + 200 * katu + 100 * n100 + 50 * n50) / (320 * total),
    }


def compare_formulas() -> None:
    from core.local_scores import _raw_scores, find_scores_db

    songs = LocalSongs().load()
    online = json.loads((cache_dir() / "plays.json").read_text(encoding="utf-8"))
    by_key = {}
    for s in _raw_scores(find_scores_db().read_bytes()):
        bid = songs.id_by_md5.get(s["md5"])
        if bid:
            by_key.setdefault((int(bid), s["mods"] & 0x140), []).append(s)

    err = {"v1": [], "v2": []}
    for o in online:
        rate_bits = 64 if o["rate"] > 1.01 else (256 if o["rate"] < 0.99 else 0)
        for s in by_key.get((o["beatmap_id"], rate_bits), []):
            f = formulas(s["hits"])
            # the matching local attempt is whichever is closest under either formula
            if min(abs(f["v1"] - o["accuracy"]), abs(f["v2"] - o["accuracy"])) < 0.004:
                err["v1"].append(abs(f["v1"] - o["accuracy"]))
                err["v2"].append(abs(f["v2"] - o["accuracy"]))
                break
    for name in ("v1", "v2"):
        e = err[name]
        print(f"   {name}: matched {len(e)}  mean err {sum(e) / len(e) * 100:.4f}pp"
              f"  max {max(e) * 100:.4f}pp")


def main() -> None:
    print("which accuracy formula does the online side report?")
    compare_formulas()
    print()
    songs = LocalSongs().load()
    plays = read_local_scores(songs)
    cache = MsdCache()
    rate_plays(plays, cache, songs=songs, allow_remote=False)

    online = json.loads((cache_dir() / "plays.json").read_text(encoding="utf-8"))
    mine = {(p.beatmap_id, round(p.rate, 3)): p for p in plays}

    print("same play seen from both sides (online acc vs scores.db acc)")
    shared = worst = 0
    for o in online:
        p = mine.get((o["beatmap_id"], round(o["rate"], 3)))
        if not p:
            continue
        shared += 1
        d = abs(p.accuracy - o["accuracy"])
        worst = max(worst, d)
        if d > 0.005:
            print(f"   {d * 100:5.2f}pp  online {o['accuracy'] * 100:6.2f}"
                  f"  local {p.accuracy * 100:6.2f}  {o['source']:9} {o['label'][:46]}")
    print(f"   {shared} maps in common, max accuracy difference {worst * 100:.3f} points")

    print("\ntop credited plays scores.db adds, per dan skillset")
    seen = {(p["beatmap_id"], round(p["rate"], 3)) for p in online}
    buckets: dict[str, list] = {s: [] for s in DAN_SKILLS}
    for p in plays:
        if p.dan is None or p.key_count != 4 or not p.msd:
            continue
        for s in split_skillsets(p.msd):
            buckets[s].append(p)
    for s in DAN_SKILLS:
        rows = sorted(buckets[s], key=lambda p: -credited_dan(p.dan, p.accuracy))[:6]
        print(f"  {s}")
        for p in rows:
            tag = "NEW " if (p.beatmap_id, round(p.rate, 3)) not in seen else "    "
            print(f"   {tag}{credited_dan(p.dan, p.accuracy):5.2f}"
                  f"  chart {p.dan:5.2f}  acc {p.accuracy * 100:6.2f}  {p.mods:5}"
                  f"  {p.label[:52]}")


if __name__ == "__main__":
    main()
