"""Etterna's wife3 accuracy, recovered from osu!mania judgement counts.

The two games do not measure the same thing. osu!mania sorts every hit into one of six
windows and weights them 300/300/200/100/50/0; wife3 scores each hit on a continuous
curve of its millisecond error and weights a miss at *minus* 2.75 notes. So an osu! 93%
and a wife3 93% are not the same play, and MSD - which is the difficulty of a chart at
93% *wife3* - has been getting the osu! number as though they were interchangeable.

Worse, the osu! number is not even comparable to itself across charts. Its windows narrow
by 3 ms per point of OD, so identical timing scores lower on a high-OD chart: simulated at
a 28 ms spread, the same playing reads 99.22% at OD 0 and 91.98% at OD 10. wife3 has no
such drift, because it reads the milliseconds rather than the bucket they fell in.

Which is the difficulty here, since only the buckets are recorded. Assuming hits land
uniformly inside their window does not work - real hits cluster near zero, and a wide
low-OD window is mostly empty space - and it read up to 8 points low. Instead the counts
are used to *infer* the timing spread that produced them: five bucket populations against
one parameter is a comfortably over-determined fit, and the windows move with OD, so the
same playing gives the same answer whatever the chart's OD.

Checked against simulated play at known spreads, over OD 0-10 and spreads from 8 to 28 ms:
the recovered spread lands within 0.1 ms of the real one and the accuracy within 0.12
percentage points. The curve itself is Etterna's, matching the published wife3 tables at
J4 (99.112% at 23 ms).

What it still cannot see is *why* a note was missed. A miss is charged the full -2.75
whether it was mistimed or never pressed, because osu! does not record the difference.
"""
from __future__ import annotations

import math
from functools import lru_cache

# A miss is not zero. This one number is most of the difference between the two systems.
MISS_WEIGHT = -2.75

# osu!mania stable judgement windows in milliseconds. Only the 320 is a constant; the
# rest tighten by 3 ms per point of OD.
_UPPER = [
    ("perfect", lambda od: 16.5),
    ("great", lambda od: 64.0 - 3.0 * od),
    ("good", lambda od: 97.0 - 3.0 * od),
    ("ok", lambda od: 127.0 - 3.0 * od),
    ("meh", lambda od: 151.0 - 3.0 * od),
]

HIT_JUDGEMENTS = [name for name, _ in _UPPER]
JUDGEMENTS = HIT_JUDGEMENTS + ["miss"]

DEFAULT_OD = 8.0

# Etterna's judge difficulties as timing scales. J4 is the one MSD is defined against, so
# it is the default; below it the curve tightens and the same playing scores lower.
JUDGE_SCALES = {1: 1.50, 2: 1.33, 3: 1.16, 4: 1.00,
                5: 0.84, 6: 0.66, 7: 0.50, 8: 0.33}
DEFAULT_JUDGE = 4


def judge_scale(judge: int) -> float:
    return JUDGE_SCALES.get(int(judge), 1.0)

# Bounds for the fitted spread. The floor keeps an all-320 score from driving it to zero
# width; the ceiling is well past any spread that still produces hits rather than misses.
_MIN_SPREAD = 0.5
_MAX_SPREAD = 200.0


def wife3(ms: float, ts: float = 1.0) -> float:
    """Etterna's wife3 score for a hit `ms` away from perfect. 1.0 is exact, a miss -2.75.

    `ts` is the timing scale; J4, what MSD is defined against, is 1.0.
    """
    ridic = 5.0 * ts
    zero = 65.0 * ts ** 0.75
    dev = 22.7 * ts ** 0.75
    max_boo = 180.0 * ts
    if ms <= ridic:
        return 1.0
    if ms <= zero:
        return math.erf((zero - ms) / dev)
    if ms <= max_boo:
        return (ms - zero) * (MISS_WEIGHT / (max_boo - zero))
    return MISS_WEIGHT


def effective_od(od: float, mods: str = "") -> float:
    """OD as the game actually applied it. HR tightens the windows, EZ widens them."""
    up = mods.upper()
    if "HR" in up:
        return min(10.0, od * 1.4)
    if "EZ" in up:
        return od * 0.5
    return od


def window_edges(od: float) -> list[float]:
    """Judgement boundaries in ms, from 0 out to the edge of the last hittable window."""
    return [0.0] + [upper(od) for _, upper in _UPPER]


def _bucket_mass(spread: float, edges: list[float]) -> list[float]:
    """How much of a zero-centred normal of this spread falls in each window."""
    cdf = [math.erf(e / (spread * math.sqrt(2.0))) for e in edges]
    return [cdf[i + 1] - cdf[i] for i in range(len(edges) - 1)]


def _fit_spread(observed: tuple[int, ...], edges: tuple[float, ...]) -> float:
    """The timing spread that best explains these hit counts, by multinomial likelihood.

    Golden section rather than anything cleverer: the likelihood is unimodal in the
    spread and this converges to well under the precision the answer is used at.
    """
    def nll(spread: float) -> float:
        return -sum(n * math.log(max(p, 1e-12))
                    for n, p in zip(observed, _bucket_mass(spread, list(edges))))

    lo, hi = _MIN_SPREAD, _MAX_SPREAD
    for _ in range(80):
        a = hi - (hi - lo) * 0.618
        b = lo + (hi - lo) * 0.618
        if nll(a) < nll(b):
            hi = b
        else:
            lo = a
    return (lo + hi) / 2.0


def _expected_hit(spread: float, cap: float, ts: float, steps: int = 400) -> float:
    """Mean wife3 of one hit, given the fitted spread and that it was not a miss."""
    step = cap / steps
    num = den = 0.0
    for i in range(steps):
        x = (i + 0.5) * step
        density = math.exp(-x * x / (2.0 * spread * spread))
        num += wife3(x, ts) * density
        den += density
    return num / den if den else 0.0


@lru_cache(maxsize=4096)
def _estimate(observed: tuple[int, ...], misses: int, edges: tuple[float, ...],
              ts: float) -> tuple[float, float]:
    """(accuracy, fitted spread) for one score. Cached - a library repeats many shapes."""
    hits = sum(observed)
    if not hits:
        return MISS_WEIGHT, float("nan")
    spread = _fit_spread(observed, edges)
    per_hit = _expected_hit(spread, edges[-1], ts)
    return (hits * per_hit + misses * MISS_WEIGHT) / (hits + misses), spread


def fitted_spread(counts: dict[str, int], od: float = DEFAULT_OD,
                  mods: str = "", ts: float = 1.0) -> float | None:
    """The player's timing spread in ms for this score, as inferred from its judgements."""
    result = _prepare(counts, od, mods, ts)
    if result is None:
        return None
    spread = result[1]
    return None if math.isnan(spread) else spread


def _prepare(counts: dict[str, int], od: float, mods: str, ts: float):
    if not counts:
        return None
    observed = tuple(int(counts.get(j) or 0) for j in HIT_JUDGEMENTS)
    misses = int(counts.get("miss") or 0)
    if sum(observed) + misses <= 0:
        return None
    edges = tuple(round(e, 3) for e in window_edges(effective_od(od, mods)))
    return _estimate(observed, misses, edges, ts)


# The OD to restate every score at, so two plays can be compared as numbers. 8 because
# it is what most 4K charts are set to, so the normalised figure stays close to the one
# the player actually saw rather than being an unfamiliar rescaling of everything.
REFERENCE_OD = 8.0


def osu_accuracy_at(counts: dict[str, int], od: float = DEFAULT_OD, mods: str = "",
                    target_od: float = REFERENCE_OD, ts: float = 1.0) -> float | None:
    """What this same playing would have scored in osu! on a chart of `target_od`.

    An osu! accuracy is not comparable to another one unless both charts share an OD: the
    windows narrow by 3 ms per point, so identical timing reads lower on a stricter chart.
    The recorded judgements pin the timing spread; re-bucketing that spread at another OD
    restates the score there.

    Misses are carried across as-is. A note that was never pressed would have been missed
    at any OD, and one that was mistimed past the last window is not distinguishable from
    it in what osu! records.
    """
    result = _prepare(counts, od, mods, ts)
    if result is None:
        return None
    spread = result[1]
    misses = int(counts.get("miss") or 0)
    hits = sum(int(counts.get(j) or 0) for j in HIT_JUDGEMENTS)
    if math.isnan(spread) or hits <= 0:
        return 0.0                                 # nothing but misses scores zero

    def modelled(at_od: float) -> float:
        mass = _bucket_mass(spread, window_edges(effective_od(at_od, mods)))
        inside = sum(mass)
        if inside <= 0:
            return 0.0
        share = [m / inside for m in mass]
        points = (300.0 * (share[0] + share[1]) + 200.0 * share[2]
                  + 100.0 * share[3] + 50.0 * share[4])
        return (hits * points) / (300.0 * (hits + misses))

    # Anchored on the score as it was actually recorded, and only the *difference* between
    # the two ODs is modelled. Asking for the OD the play was already on then returns the
    # real number rather than the fit's opinion of it, and the fit's small bias cancels
    # instead of accumulating.
    return recorded_accuracy(counts) + modelled(target_od) - modelled(od)


def recorded_accuracy(counts: dict[str, int]) -> float:
    """osu!mania accuracy as the game reported it: a 320 and a 300 both count 300."""
    total = sum(int(counts.get(j) or 0) for j in JUDGEMENTS)
    if total <= 0:
        return 0.0
    got = (300 * (int(counts.get("perfect") or 0) + int(counts.get("great") or 0))
           + 200 * int(counts.get("good") or 0) + 100 * int(counts.get("ok") or 0)
           + 50 * int(counts.get("meh") or 0))
    return got / (300.0 * total)


def wife_accuracy(counts: dict[str, int], od: float = DEFAULT_OD,
                  mods: str = "", ts: float = 1.0) -> float | None:
    """Estimated wife3 accuracy, or None when the judgements are unknown.

    Can go negative on a score that missed more than it hit; callers showing it as a
    percentage should clamp, because neither game prints "-40%".
    """
    result = _prepare(counts, od, mods, ts)
    return None if result is None else result[0]
