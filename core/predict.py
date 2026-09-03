"""What the player would probably score on a chart they have not played.

Their own history already says how they hit: every rated play yields a timing spread (see
core.wife), and spread grows with chart difficulty in a way that is close enough to linear
over the range anyone is actually recommended charts in. Fitting that line to their plays
turns a chart's MSD into a predicted spread, and a predicted spread into a predicted score.

Measured on this machine's 1108 plays above MSD 20, by repeated 70/30 holdout: the spread
lands within 3.5 ms on average and the accuracy within 1.2 percentage points at the median,
4.1 at the ninetieth. That is an estimate to size a chart with, not a number to quote.

Deliberately fitted on *all* of their plays rather than their best ones. A recommendation
is answering "how would this go", and half of how it would go is that people do not attack
every chart at their limit.
"""
from __future__ import annotations

from .wife import (DEFAULT_OD, _bucket_mass, effective_od, fitted_spread, window_edges,
                   wife_accuracy)

# Below this the fit is meaningless: charts that easy are played carelessly rather than
# accurately, and their spreads run *higher* than charts a band above them.
MIN_FIT_MSD = 20.0

# Fewer plays than this and the line is being drawn through noise.
MIN_FIT_PLAYS = 20

# No prediction is offered outside the range the line was fitted over, plus a little.
EXTRAPOLATION = 3.0


class TimingModel:
    """A player's timing spread as a function of chart difficulty."""

    def __init__(self, slope: float, intercept: float, lo: float, hi: float, n: int) -> None:
        self.slope = slope
        self.intercept = intercept
        self.lo = lo
        self.hi = hi
        self.n = n

    def spread_for(self, msd: float) -> float | None:
        """Predicted timing spread in ms, or None if the chart is outside the fit."""
        if not (self.lo - EXTRAPOLATION <= msd <= self.hi + EXTRAPOLATION):
            return None
        return max(3.0, self.slope * msd + self.intercept)

    def accuracy_for(self, msd: float, od: float = DEFAULT_OD,
                     mods: str = "") -> float | None:
        """Predicted osu!mania accuracy on a chart of this difficulty."""
        spread = self.spread_for(msd)
        if spread is None:
            return None
        mass = _bucket_mass(spread, window_edges(effective_od(od, mods)))
        return (300.0 * (mass[0] + mass[1]) + 200.0 * mass[2]
                + 100.0 * mass[3] + 50.0 * mass[4]) / 300.0


    def msd_for_accuracy(self, target: float, od: float = DEFAULT_OD) -> float | None:
        """The chart difficulty this player would score `target` on. The inverse of above.

        Bisection rather than algebra: accuracy runs through the judgement windows and the
        error function on the way out, and the range being searched is 20 MSD wide.
        """
        lo, hi = self.lo - EXTRAPOLATION, self.hi + EXTRAPOLATION
        if self.accuracy_for(lo, od) is None or self.accuracy_for(hi, od) is None:
            return None
        if self.accuracy_for(hi, od) > target:      # even the hardest is still too easy
            return hi
        if self.accuracy_for(lo, od) < target:      # even the easiest is already too hard
            return lo
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if self.accuracy_for(mid, od) > target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0


def fit_timing_model(plays: list[dict]) -> TimingModel | None:
    """Least squares of timing spread against chart MSD, over the player's own plays."""
    points = []
    for p in plays:
        msd = (p.get("msd") or {}).get("overall")
        if not msd or msd < MIN_FIT_MSD or p.get("key_count") != 4:
            continue
        spread = fitted_spread(p.get("judgements") or {},
                               float(p.get("od") or DEFAULT_OD), p.get("mods") or "")
        # A spread this wide means the score was not really a hit distribution at all -
        # a quit, or a chart played on the wrong layout - and it drags the line with it.
        if spread is None or spread > 120.0:
            continue
        points.append((msd, spread))

    if len(points) < MIN_FIT_PLAYS:
        return None
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return None
    slope = (n * sxy - sx * sy) / denom
    return TimingModel(slope, (sy - slope * sx) / n,
                       min(x for x, _ in points), max(x for x, _ in points), n)
