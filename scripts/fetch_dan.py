"""Fetch per-map dan values for every rated play and validate the player estimate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.beatmap import cache_dir
from core.dan import dan_label, estimate_dan, fetch_map_dan
from core.ratings import MsdCache
from core.sources import _session

CACHE = cache_dir()


def main() -> None:
    plays = json.loads((CACHE / "plays.json").read_text(encoding="utf-8"))
    cache = MsdCache()
    sess = _session()

    todo = [p for p in plays if p.get("dan") is None]
    print(f"{len(plays)} plays, {len(todo)} need a dan value")

    for i, p in enumerate(plays, 1):
        entry = cache.get(p["beatmap_id"], p["rate"]) or {}
        raw = entry.get("dan")
        if raw is None:
            raw = fetch_map_dan(p["beatmap_id"], p["rate"], sess)
            if raw is not None:
                entry["dan"] = raw
                cache.put(p["beatmap_id"], p["rate"], entry)
        p["dan"] = raw
        if i % 20 == 0:
            cache.save()
            print(f"  {i}/{len(plays)}")
    cache.save()

    (CACHE / "plays.json").write_text(json.dumps(plays, ensure_ascii=False), encoding="utf-8")
    have = sum(1 for p in plays if p.get("dan") is not None)
    print(f"dan values resolved for {have}/{len(plays)} plays\n")

    truth = json.loads((CACHE / "truth.json").read_text(encoding="utf-8"))
    t_dan = truth["modes"][0]["dan"]["rc"]

    off = [p for p in plays if p["source"] == "official" and p["key_count"] == 4]
    mame = [p for p in plays if p["source"] == "mamesosu" and p["key_count"] == 4]

    for name, pool in (("official", off), ("mamesosu", mame), ("combined", off + mame)):
        est = estimate_dan(pool)
        raw = f"{est['raw']:.2f}" if est["raw"] is not None else "-"
        print(f"  {name:10} dan {est['label']:8} (raw {raw}, {est['clears']} clears)")

    print(f"\n  mania-tracker official dan: {t_dan['label']} "
          f"(raw {t_dan['rawDan']}, {t_dan['clears']} clears)")
    print(f"  label check: dan_label({t_dan['rawDan']}) -> {dan_label(t_dan['rawDan'])}")


if __name__ == "__main__":
    main()
