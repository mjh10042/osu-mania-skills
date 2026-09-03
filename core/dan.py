"""Dan (단위인정) conversion and player dan estimation.

Map-level dan values come from mania-tracker (rawDan is not a clean function of MSD,
so it is fetched per map and cached). Everything above map level is reproduced locally
so that mamesosu plays - which mania-tracker never sees - can be scored the same way.

The player model was reverse-engineered from /api/profiles/{id}/dan-evidence:
  * a clear counts if accuracy >= MIN_ACCURACY
  * its chart dan is adjusted by an accuracy bonus/penalty around BAR_ACCURACY -> credited dan
  * clears are bucketed into four dan skillsets (jack / tech / speed / stamina)
  * each bucket takes its best AVERAGE_WINDOW credited dans, drops the worst DROP_LOWEST,
    and averages the rest
  * the player dan is the mean of the four bucket dans
"""
from __future__ import annotations

import math

import requests

from .sources import MT_THROTTLE, RETRY_STATUS

MT_API = "https://api.mania-tracker.com"

GREEK = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]

MIN_ACCURACY = 0.92
BAR_ACCURACY = 0.96
AVERAGE_WINDOW = 20
DROP_LOWEST = 3
QUORUM = 4

# A narrow "peak" window (mean of the best 3 credited clears) was tried here and removed.
# It looked justified - the estimate read 13.13 while the database held clears of charts
# rated 16.29 and 17.17 - but those two plays were not the account holder's. A shared PC
# collects guest scores, and a guest who sits down to show off plays exactly one kind of
# chart, so the handful of foreign plays land at the very top of the dan distribution:
# 2% of the rows, and a top-3 statistic is a statistic those rows own outright.
#
# The wide window is what survived them, and it agrees with the REFORM stage results
# (clean through Gamma, collapsing at Delta, walled at Epsilon). The lesson is about
# *whose* plays are in the pool, not about window width: filter the pool by player first,
# and only then ask whether a narrower window is finally readable.

DAN_SKILLS = ["jack", "tech", "speed", "stamina"]

DAN_SKILL_LABELS = {
    "jack": "Jack",
    "tech": "Tech",
    "speed": "Speed",
    "stamina": "Stamina",
}


def dan_label(raw: float | None) -> str:
    """Convert a raw dan number to mania-tracker's label (e.g. 8.25 -> '8+')."""
    if raw is None or raw <= 0:
        return "-"
    base = math.ceil(raw - 0.5)
    if base < 1:
        base = 1
    delta = raw - base

    if delta <= -0.3:
        suffix = "--"
    elif delta < -0.1:
        suffix = "-"
    elif delta < 0.1:
        suffix = ""
    elif delta < 0.3:
        suffix = "+"
    else:
        suffix = "++"

    name = str(base) if base <= 10 else (
        GREEK[base - 11] if base - 11 < len(GREEK) else f"theta+{base - 18}")
    return f"{name}{suffix}"


def accuracy_bonus(accuracy: float) -> float:
    """Credited-dan offset for a clear's accuracy.

    Piecewise linear, fitted to 62 mania-tracker samples (max residual 0.01, i.e. the
    rounding of their published two-decimal values). Below the 96% bar a clear loses dan;
    between 96% and ~97% it is credited as-is; above that it gains, up to +1.50 at 100%.
    """
    a = accuracy * 100.0
    if a < 96.0:
        return 0.2473 * (a - 97.056)
    if a <= 96.9936:
        return 0.0
    if a < 98.61:
        return 0.11627 * (a - 96.9936)
    if a < 99.10:
        return 0.1878 + 1.2 * (a - 98.61)
    return 1.5 - 0.8 * (100.0 - a)


def credited_dan(chart_dan: float, accuracy: float) -> float:
    return chart_dan + accuracy_bonus(accuracy)


# Chart dan has to be fetched one request at a time and mania-tracker allows barely one
# request a second, so with a full local score database there are more charts than can
# be looked up in one sitting. This line - fitted over 1594 charts pulled from their own
# map index across dan 7 to 15 and every pattern - orders the queue so the charts that
# can actually reach a skillset window are asked for first.
#
# It is an ordering aid only. The residual spread is wide (p95 +2.8 dan, and dan measures
# clearability while MSD measures MinaCalc difficulty, which is exactly what jack charts
# break), so it must never be used as a dan value or as a reason to skip a chart forever.
_DAN_PER_MSD = 0.5427
_DAN_MSD_OFFSET = -3.640


def dan_proxy(msd: dict[str, float], accuracy: float) -> float:
    """Rough credited dan from MSD alone, for deciding lookup order."""
    peak = max((v for k, v in msd.items() if k != "overall"), default=0.0)
    return _DAN_PER_MSD * peak + _DAN_MSD_OFFSET + accuracy_bonus(accuracy)


# Which MinaCalc skillsets feed each dan skillset, and the typical share-of-overall each
# one sits at across a 4K library. Dividing by the typical share is what makes the four
# buckets comparable - raw Stamina MSD is near Overall on almost every chart, while raw
# JackSpeed almost never is.
#
# mania-tracker buckets charts with its own pattern analyser, which is not exposed per
# beatmap, so this is an MSD-shaped stand-in fitted to their published dan-evidence
# (scripts/fit_dan.py). It agrees with their bucketing on 53/58 charts and lands the
# player dan within ~0.1 - close enough to compare pools, not an exact reimplementation.
_DAN_SOURCES: dict[str, list[tuple[str, float]]] = {
    "jack": [("chordjack", 0.902), ("jackspeed", 0.643)],
    "tech": [("technical", 0.845)],
    "speed": [("stream", 0.787)],
    "stamina": [("stamina", 1.404), ("jumpstream", 0.871), ("handstream", 0.755)],
}

# A play is filed under every bucket within this much of the best bucket, mirroring the
# multi-skillset entries mania-tracker publishes (e.g. ["tech", "speed"]).
_SECOND_BUCKET_MARGIN = 0.076


def skillset_scores(msd: dict[str, float]) -> dict[str, float]:
    overall = msd.get("overall") or max(msd.values(), default=0.0)
    if overall <= 0:
        return {s: 0.0 for s in DAN_SKILLS}
    return {name: max(msd.get(k, 0.0) / overall / typical for k, typical in srcs)
            for name, srcs in _DAN_SOURCES.items()}


def split_skillsets(msd: dict[str, float]) -> list[str]:
    """Assign a chart to one or two dan skillsets from its MSD profile."""
    scores = skillset_scores(msd)
    best = max(scores.values())
    if best <= 0:
        return []
    return [s for s in DAN_SKILLS if scores[s] >= best - _SECOND_BUCKET_MARGIN]


def skillset_dan(credited: list[float]) -> float | None:
    """Best `AVERAGE_WINDOW` credited dans minus the worst `DROP_LOWEST`, averaged."""
    window = sorted(credited, reverse=True)[:AVERAGE_WINDOW]
    keep = window[:max(1, len(window) - DROP_LOWEST)]
    return sum(keep) / len(keep) if keep else None


# How far `dan_proxy` is allowed to under-read a chart's real dan before the chart is
# written off. This is a speed/precision dial, not a correctness one: every lookup it
# saves is one request against an API that allows roughly two a second.
#
# Measured over a 576-chart pool whose real dans were already known, against the estimate
# with no pruning at all (13.128, "gamma+"):
#
#     margin   requests   estimate   error
#       off        576     13.128    -
#       3.0        466     13.101    0.027
#       2.0        333     13.082    0.045
#       1.5        255     13.076    0.052     <- here
#       1.0        187     13.036    0.091
#       0.0         99     12.930    0.198
#
# 1.5 halves the requests for 0.05 dan, a quarter of the +-0.2 the model is honest to in
# the first place, and the published label does not move. Below 1.0 the error starts
# eating the whole tolerance, which is the point of diminishing returns rather than a
# cliff - raise this if precision ever matters more than the wait.
#
# Widening the bound is *not* the way to make this cheaper. A quantile regression on all
# eight skillsets, per-bucket fits, and round-robin queue ordering were each tried and
# each landed on the same curve: at margin 3.0 the pruning already matches what a run
# that knew the final windows up front would achieve. The residual is MSD-versus-dan
# noise - clearability and MinaCalc difficulty are different things - and no reordering
# or refitting of MSD gets past it. An oracle that knew the true dans would need 68.
DAN_PROXY_MARGIN = 1.5


class DanWindows:
    """The four skillset windows as they fill, for deciding which charts still matter.

    A chart can only move the estimate by entering the best AVERAGE_WINDOW credited dans
    of a bucket it belongs to, and which buckets those are is decided by MSD alone -
    known locally, without asking anyone. So a chart whose *highest possible* credited
    dan still falls under every window it could enter is provably inert, and its lookup
    can be skipped outright rather than merely deferred.

    That distinction is the whole point: each lookup is one request against an API that
    allows roughly two a second, so on a full local score database the difference is
    twenty minutes of asking versus a few of them.
    """

    def __init__(self, margin: float = DAN_PROXY_MARGIN) -> None:
        self.margin = margin
        self._buckets: dict[str, list[float]] = {s: [] for s in DAN_SKILLS}

    def add(self, msd: dict[str, float], accuracy: float, chart_dan: float | None) -> None:
        """Record a clear whose chart dan is known."""
        if chart_dan is None or accuracy < MIN_ACCURACY or not msd:
            return
        credited = credited_dan(chart_dan, accuracy)
        for s in split_skillsets(msd):
            b = self._buckets[s]
            b.append(credited)
            if len(b) > AVERAGE_WINDOW:
                b.sort(reverse=True)
                del b[AVERAGE_WINDOW:]

    def can_matter(self, msd: dict[str, float], accuracy: float) -> bool:
        """Could this chart's dan still change any bucket, at its most generous?"""
        if accuracy < MIN_ACCURACY or not msd:
            return False
        ceiling = dan_proxy(msd, accuracy) + self.margin
        for s in split_skillsets(msd):
            b = self._buckets[s]
            if len(b) < AVERAGE_WINDOW or ceiling > min(b):
                return True
        return False


class DanUnavailable(RuntimeError):
    """The dan for this chart could not be read - unknown, not 'this chart has none'."""


def fetch_map_dan(beatmap_id: int, rate: float, session: requests.Session,
                  attempts: int = 3) -> float | None:
    """The chart's raw dan, or None if mania-tracker has no dan for it.

    Raises DanUnavailable when the request itself could not be completed, so callers
    do not cache a rate limit as though the chart were unrated.
    """
    for attempt in range(attempts):
        MT_THROTTLE.acquire()
        ok = False
        try:
            r = session.get(f"{MT_API}/api/chart-analysis/rate",
                            params={"beatmapId": beatmap_id, "rate": f"{rate:.2f}"},
                            timeout=30)
            ok = r.status_code < 400
            if r.status_code == 200:
                raw = ((r.json() or {}).get("dan") or {}).get("rawDan")
                return float(raw) if raw is not None else None
            if r.status_code == 404:
                ok = True          # a definite answer, not a rejection - do not back off
                return None
            if r.status_code not in RETRY_STATUS:
                raise DanUnavailable(f"HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as exc:
            if attempt == attempts - 1:
                raise DanUnavailable(str(exc)) from exc
        finally:
            MT_THROTTLE.release(ok)
        MT_THROTTLE.penalise(1.5 * (2 ** attempt))
    raise DanUnavailable("rate limited")


def estimate_dan(plays: list[dict]) -> dict:
    """Player dan from rated plays: bucket, credit, window, then average the buckets."""
    buckets: dict[str, list[dict]] = {s: [] for s in DAN_SKILLS}
    total = 0
    for p in plays:
        if p.get("dan") is None or p.get("accuracy", 0.0) < MIN_ACCURACY or not p.get("msd"):
            continue
        total += 1
        entry = dict(p)
        entry["credited"] = credited_dan(p["dan"], p["accuracy"])
        for s in split_skillsets(p["msd"]):
            buckets[s].append(entry)

    skills = {}
    for s in DAN_SKILLS:
        items = sorted(buckets[s], key=lambda e: e["credited"], reverse=True)
        raw = skillset_dan([e["credited"] for e in items])
        skills[s] = {
            "raw": raw,
            "label": dan_label(raw),
            "clears": len(items),
            "need": AVERAGE_WINDOW,
            "plays": items[:AVERAGE_WINDOW],
        }

    have = [skills[s]["raw"] for s in DAN_SKILLS if skills[s]["raw"] is not None]
    raw = sum(have) / QUORUM if len(have) == QUORUM else (
        sum(have) / len(have) if have else None)
    return {
        "raw": raw,
        "label": dan_label(raw),
        "clears": total,
        "partial": len(have) < QUORUM,
        "skills": skills,
    }
