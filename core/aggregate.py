"""MSD -> skill rating aggregation.

Reproduces mania-tracker's published 4K ratings to within ~0.4 (rmse 0.24) using
Etterna's AggregateSSRs bisection over accuracy-scaled MSD values, times Etterna's
1.04 player-rating constant.
"""
from __future__ import annotations

import math

from .msd import SKILLSETS
from .wife import (DEFAULT_JUDGE, DEFAULT_OD, REFERENCE_OD, fitted_spread,
                   judge_scale, osu_accuracy_at, wife_accuracy)

RATING_SCALE = 1.04
MSD_GOAL = 0.93
RADAR_SKILLS = [s for s in SKILLSETS if s != "overall"]

DISPLAY_NAME = {
    "overall": "Overall",
    "stream": "Stream",
    "jumpstream": "Jumpstream",
    "handstream": "Handstream",
    "stamina": "Stamina",
    "jackspeed": "JackSpeed",
    "chordjack": "Chordjack",
    "technical": "Technical",
}


# The two accuracy systems a play can be judged by. "osu" is what osu! and mania-tracker
# report and what this module's constants were fitted against; "wife" is Etterna's own,
# which is what MSD_GOAL actually refers to. They are not interchangeable - a play of
# nothing but 300s reads 100% on one and 96.5% on the other - so both are offered rather
# than one being quietly swapped for the other.
BASES = ("osu", "wife")


def play_accuracy(play: dict, basis: str = "osu") -> float | None:
    """The play's accuracy on one system, or None if that system cannot see it.

    Only osu! plays recorded before judgement counts were carried come back None; a
    refresh fills them in.
    """
    if basis == "osu":
        return play.get("accuracy")
    return wife_accuracy(play.get("judgements") or {},
                         float(play.get("od") or DEFAULT_OD), play.get("mods") or "")


def effective_ssr(play: dict, skill: str, basis: str = "osu") -> float:
    """MSD is defined at 93%; scale linearly by how far the play's accuracy exceeds that."""
    acc = play_accuracy(play, basis)
    return play["msd"][skill] * ((acc if acc is not None else 0.0) / MSD_GOAL)


def counts_toward_rating(play: dict, basis: str = "osu") -> bool:
    """Whether a play is inside the range the accuracy scale was ever fitted for.

    MSD is the difficulty of a chart *at MSD_GOAL accuracy*, so scaling it by
    accuracy/MSD_GOAL interpolates while the play is at or above that bar and extrapolates
    below it - and the line is far too shallow out there. At 84% it still credits 90% of
    the chart's MSD, which is how one sloppy 1.5x pass came to outrank every clean play in
    the list while scoring 15 points of accuracy worse than the same chart at 1.0x.

    It went unnoticed because the published ratings this module reproduces are computed
    from osu! top-play pools, where only 6% of plays sit under the goal and none of them
    are near the top. A local scores.db is a census rather than a top list - 17% of it is
    under the goal - so nothing held the extrapolation back.

    Checked against mania-tracker's own published numbers for this account: a bar anywhere
    up to 93% leaves the agreement untouched (max error 0.408, the same as with no bar at
    all) and 94% breaks it (2.188). The goal itself is therefore both the principled place
    to stop and the highest one that is still safe.
    """
    acc = play_accuracy(play, basis)
    return acc is not None and acc >= MSD_GOAL


def etterna_aggregate(ssrs: list[float]) -> float:
    if not ssrs:
        return 0.0

    def total(rating: float) -> float:
        return sum(max(0.0, 2.0 / math.erfc(0.1 * (v - rating)) - 2.0) for v in ssrs)

    rating, resolution = 0.0, 10.24
    for _ in range(11):
        while math.pow(2, rating * 0.1) < total(rating):
            rating += resolution
        rating -= resolution
        resolution /= 2.0
    return rating + resolution


def rate_skillsets(plays: list[dict], basis: str = "osu") -> dict[str, float]:
    """Full 8-skillset rating for a pool of plays."""
    pool = [p for p in plays if counts_toward_rating(p, basis)]
    return {s: RATING_SCALE * etterna_aggregate([effective_ssr(p, s, basis) for p in pool])
            for s in SKILLSETS}


def contributions(plays: list[dict], skill: str, limit: int = 40,
                  ref_od: float = REFERENCE_OD, judge: int = DEFAULT_JUDGE) -> list[dict]:
    """Plays ranked by their effective SSR in one skillset, for the detail list.

    Filtered the same way as the rating itself, so the list is the working out for the
    number above it rather than a separate ranking that happens to look similar.
    """
    ts = judge_scale(judge)
    scored = [(effective_ssr(p, skill), p) for p in plays if counts_toward_rating(p)]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [dict(p, effective=v,
                 # Restated at whichever OD and judge the reader picked. The ranking above
                 # is not: it is the osu!-accuracy rating's own working out, and would stop
                 # being that if a display choice could reorder it.
                 wife=wife_accuracy(p.get("judgements") or {},
                                    float(p.get("od") or DEFAULT_OD),
                                    p.get("mods") or "", ts),
                 # The osu! number restated at one OD, because the recorded one is only
                 # comparable to other plays on charts of the same OD.
                 acc_at=osu_accuracy_at(p.get("judgements") or {},
                                        float(p.get("od") or DEFAULT_OD),
                                        p.get("mods") or "", ref_od, ts),
                 # The timing spread behind all three numbers above, in milliseconds.
                 # It is the only one of them that is a fact about the playing rather
                 # than about the scale it is being read on.
                 spread=fitted_spread(p.get("judgements") or {},
                                      float(p.get("od") or DEFAULT_OD),
                                      p.get("mods") or ""))
            for v, p in scored[:limit]]


def build_pools(plays: list[dict], key_count: int = 4) -> dict[str, list[dict]]:
    """Split rated plays into official / mamesosu / combined pools.

    Maps played on both servers are deduplicated in the combined pool, keeping the
    better accuracy so one map cannot count twice toward a rating.
    """
    pool = [p for p in plays if p.get("msd") and p.get("key_count") == key_count]
    official = [p for p in pool if p["source"] == "official"]
    mamesosu = [p for p in pool if p["source"] == "mamesosu"]

    # Keyed by md5 where there is one, because a beatmap id is shared by every difficulty
    # of a rate-changer pack: deduplicating on it collapsed nine distinct charts into one.
    best: dict[object, dict] = {}
    for p in pool:
        key = (p.get("md5") or "").lower() or p["beatmap_id"]
        cur = best.get(key)
        if cur is None or p["accuracy"] > cur["accuracy"]:
            best[key] = p
    combined = list(best.values())

    return {"official": official, "mamesosu": mamesosu, "combined": combined}
