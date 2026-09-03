"""Reverse-engineer mania-tracker's MSD -> skill-rating aggregation.

Ground truth is mania-tracker's own published 4K ratings for the official account;
candidates are scored by max absolute error across the 8 skillsets.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.msd import SKILLSETS

CACHE = Path(__file__).resolve().parent.parent / "cache"


def etterna_aggregate(ssrs: list[float]) -> float:
    """Etterna's AggregateSSRs: bisect for the rating that balances an erfc-weighted sum."""
    if not ssrs:
        return 0.0

    def total(rating: float) -> float:
        s = 0.0
        for v in ssrs:
            s += max(0.0, 2.0 / math.erfc(0.1 * (v - rating)) - 2.0)
        return s

    rating, resolution = 0.0, 10.24
    for _ in range(11):
        while math.pow(2, rating * 0.1) < total(rating):
            rating += resolution
        rating -= resolution
        resolution /= 2.0
    return rating + resolution


def top_n_mean(ssrs: list[float], n: int) -> float:
    v = sorted(ssrs, reverse=True)[:n]
    return sum(v) / len(v) if v else 0.0


def decay_weighted(ssrs: list[float], decay: float) -> float:
    v = sorted(ssrs, reverse=True)
    num = sum(x * decay ** i for i, x in enumerate(v))
    den = sum(decay ** i for i in range(len(v)))
    return num / den if den else 0.0


def acc_scaled(play: dict, skill: str, power: float) -> float:
    """Approximate SSR-at-accuracy from MSD-at-93% with a power law."""
    base = play["msd"][skill]
    if power == 0.0:
        return base
    return base * (play["accuracy"] / 0.93) ** power


def main() -> None:
    plays = json.loads((CACHE / "plays.json").read_text(encoding="utf-8"))
    truth_doc = json.loads((CACHE / "truth.json").read_text(encoding="utf-8"))
    truth = {k.lower(): v for k, v in truth_doc["modes"][0]["ratings"].items()}

    off4k = [p for p in plays if p["source"] == "official" and p["key_count"] == 4]
    print(f"official 4K plays: {len(off4k)}   truth analyzedPlays: {truth_doc['modes'][0]['analyzedPlays']}\n")

    candidates: list[tuple[str, callable]] = []
    for power in (0.0, 1.0, 2.0, 3.0, 4.0):
        candidates.append((f"etterna  acc^{power:.0f}",
                           lambda ps, s, p=power: etterna_aggregate([acc_scaled(x, s, p) for x in ps])))
    for n in (3, 5, 8, 10, 15, 20, 25):
        for power in (0.0, 2.0, 3.0):
            candidates.append((f"top{n:<2} mean acc^{power:.0f}",
                               lambda ps, s, n=n, p=power: top_n_mean([acc_scaled(x, s, p) for x in ps], n)))
    for decay in (0.7, 0.8, 0.9, 0.95):
        for power in (0.0, 2.0, 3.0):
            candidates.append((f"decay{decay} acc^{power:.0f}",
                               lambda ps, s, d=decay, p=power: decay_weighted([acc_scaled(x, s, p) for x in ps], d)))

    results = []
    for name, fn in candidates:
        preds = {s: fn(off4k, s) for s in SKILLSETS}
        errs = {s: preds[s] - truth[s] for s in SKILLSETS}
        maxerr = max(abs(e) for e in errs.values())
        rmse = math.sqrt(sum(e * e for e in errs.values()) / len(errs))
        results.append((maxerr, rmse, name, preds, errs))

    results.sort()
    print(f"{'model':22}{'maxerr':>8}{'rmse':>7}   per-skill error")
    for maxerr, rmse, name, preds, errs in results[:12]:
        detail = " ".join(f"{s[:4]}{errs[s]:+.1f}" for s in SKILLSETS)
        print(f"{name:22}{maxerr:8.2f}{rmse:7.2f}   {detail}")

    best = results[0]
    print(f"\nbest = {best[2]}")
    print(f"{'skill':12}{'pred':>8}{'truth':>8}{'err':>8}")
    for s in SKILLSETS:
        print(f"{s:12}{best[3][s]:8.2f}{truth[s]:8.2f}{best[4][s]:+8.2f}")


if __name__ == "__main__":
    main()
