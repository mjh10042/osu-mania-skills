"""Fit the dan classifier + aggregation against mania-tracker's dan-evidence.

Run this only when the model needs re-checking; the resulting constants live in core/dan.py.

    python scripts/fit_dan.py                  fit + check against the cached evidence
    python scripts/fit_dan.py --holdout <id>   check against a player it was not fitted on

The fit uses a single profile, so the holdout is the only thing that shows whether the
model generalises. `/api/profiles/{name-or-id}/dan-evidence?keys=4` is the ground truth.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.beatmap import cache_dir
from core.dan import DAN_SKILLS, credited_dan, estimate_dan, skillset_dan, split_skillsets
from core.ratings import MsdCache, rate_plays
from core.sources import MT_API, _session, fetch_official

CACHE = cache_dir()


def load_labelled() -> list[dict]:
    ev = json.loads((CACHE / "dan_evidence.json").read_text(encoding="utf-8"))
    msd = json.loads((CACHE / "msd.json").read_text(encoding="utf-8"))
    rows: dict[int, dict] = {}
    for s in ev["skillsets"]:
        for p in s["plays"]:
            pl = p["play"]
            r = rows.setdefault(pl["scoreId"], {
                "beatmap_id": pl["beatmapId"], "rate": float(pl["rate"]),
                "accuracy": p["clearAccuracy"], "dan": p["chartDan"],
                "credited": p["creditedDan"], "true": set(), "label": pl["version"]})
            r["true"].update(p["skillsets"])
    out = []
    for r in rows.values():
        entry = msd.get(f"{r['beatmap_id']}@{r['rate']:.3f}")
        if entry:
            r["msd"] = entry["msd"]
            out.append(r)
    return out


def main() -> None:
    ev = json.loads((CACHE / "dan_evidence.json").read_text(encoding="utf-8"))
    rows = load_labelled()
    print(f"{len(rows)} labelled plays with cached MSD\n")

    exact = sum(1 for r in rows if set(split_skillsets(r["msd"])) == r["true"])
    overlap = sum(1 for r in rows if set(split_skillsets(r["msd"])) & r["true"])
    print(f"classifier: exact {exact}/{len(rows)}  overlap {overlap}/{len(rows)}")
    for r in sorted(rows, key=lambda r: sorted(r["true"])):
        got = set(split_skillsets(r["msd"]))
        if got != r["true"]:
            print(f"   want {','.join(sorted(r['true'])):12} got {','.join(sorted(got)):12}"
                  f" {r['label'][:40]}")

    print("\ncredited dan check")
    err = max(abs(round(credited_dan(r["dan"], r["accuracy"]), 2) - r["credited"]) for r in rows)
    print(f"   max error {err:.3f}")

    print("\naggregation on mania-tracker's own buckets")
    for s in ev["skillsets"]:
        vals = sorted((p["creditedDan"] for p in s["plays"]), reverse=True)
        got = skillset_dan(vals)
        print(f"   {s['id']:8} true {s['dan']['rawDan']:6.2f}  got {got:6.2f}")
    print(f"   overall  true {ev['dan']['rawDan']:6.2f}")

    print("\nend to end on the official pool")
    plays = json.loads((CACHE / "plays.json").read_text(encoding="utf-8"))
    est = estimate_dan([p for p in plays
                        if p["source"] == "official" and p["key_count"] == 4])
    truth = {s["id"]: s["dan"]["rawDan"] for s in ev["skillsets"]}
    for s in DAN_SKILLS:
        d = est["skills"][s]
        print(f"   {s:8} true {truth[s]:6.2f}  got {d['raw']:6.2f} ({d['label']})"
              f"  clears {d['clears']}")
    print(f"   overall  true {ev['dan']['rawDan']:6.2f} ({ev['dan']['label']})"
          f"  got {est['raw']:6.2f} ({est['label']})")


def holdout(who: str) -> None:
    """Compare the local estimate against mania-tracker's dan for a player, by id or name."""
    session = _session()
    # Only the snapshot endpoint resolves usernames; dan-evidence needs the numeric id.
    snap = session.get(f"{MT_API}/api/profiles/{who}/snapshot", timeout=60)
    snap.raise_for_status()
    user_id = int(snap.json()["user"]["id"])

    r = session.get(f"{MT_API}/api/profiles/{user_id}/dan-evidence", params={"keys": 4},
                    timeout=60)
    r.raise_for_status()
    true = (r.json() or {}).get("dan")
    if not true:
        print(f"{who}: mania-tracker publishes no 4K dan for this profile")
        return

    plays = fetch_official(user_id, session)

    cache = MsdCache()
    rate_plays(plays, cache)
    cache.save()
    est = estimate_dan([asdict(p) for p in plays if p.msd and p.key_count == 4])

    print(f"\nholdout: {who}")
    print(f"   {'':9} {'true':>7} {'ours':>7} {'diff':>6}")
    for s in DAN_SKILLS:
        tv = true["skillsets"][s]["rawDan"]
        ov = est["skills"][s]["raw"]
        got = f"{ov:7.2f} {ov - tv:+6.2f}" if ov is not None else f"{'-':>7} {'-':>6}"
        print(f"   {s:9} {tv:7.2f} {got}")
    print(f"   {'overall':9} {true['rawDan']:7.2f} {est['raw']:7.2f} "
          f"{est['raw'] - true['rawDan']:+6.2f}   ({true['label']} vs {est['label']})")


if __name__ == "__main__":
    if "--holdout" in sys.argv:
        holdout(sys.argv[sys.argv.index("--holdout") + 1])
    else:
        main()
