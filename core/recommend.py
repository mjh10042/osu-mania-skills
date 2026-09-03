"""Map recommendations aimed at a player's weakest skillsets.

mania-tracker's snapshot search already knows every 4K chart's MSD, dan and pattern
classification, so the recommender is a filter over that index rather than a second
local analysis: pick the skillsets the player is furthest behind on, ask for charts of
that pattern near their dan level, and drop anything they have already played.

Two things about that index drive the code below:
  * it hard-caps a response at 48 charts and its `page` parameter returns nothing, so a
    wide dan window silently shows only the 48 most-played charts in it - which at high
    dan are all vibro memes;
  * its own `vibro` flag is set on almost nothing (0 of 48 charts in the delta jack
    band, three of which are literally titled "Vibro Pack"), so junk has to be
    recognised from the pattern profile instead.
"""
from __future__ import annotations

import re
import statistics

import requests

from .dan import _DAN_MSD_OFFSET, _DAN_PER_MSD, dan_proxy
from .sources import MT_THROTTLE, RETRY_STATUS

MT_API = "https://api.mania-tracker.com"

# MinaCalc skillset -> the pattern tag mania-tracker's index uses.
PATTERN_OF = {
    "stream": "stream",
    "jumpstream": "jumpstream",
    "handstream": "handstream",
    "stamina": "stamina",
    "jackspeed": "jack",
    "chordjack": "chordjack",
    "technical": "tech",
}

# The words osu! mappers actually put in a difficulty name or the tags field, grouped the
# way dan skillsets are (see dan._DAN_SOURCES). mania-tracker's own pattern classifier is
# broad - it will call anything with jacks in it "jack" - so a chart named for the exact
# skill being trained is a stronger signal than the classifier, and these are searched for
# by name and ranked above everything else.
SEARCH_TAGS = {
    "jack": ["chordjack", "speedjack", "minijack", "anchorjack", "anchor", "longjack"],
    "tech": ["tech", "technical", "stream tech", "poly", "polyrhythm", "dump", "dump tech"],
    "speed": ["dump", "dumpstream", "speed", "stream", "single stream", "index stream"],
    "stamina": ["jumpstream", "handstream", "stream", "stamina"],
}

# Charts that advertise themselves as these train nothing, whatever the index says about
# them - and the index says almost nothing, which is the whole problem with `vibro`.
AVOID_TAGS = {
    "jack": ["vibro"],
}

# MinaCalc skillset -> the dan skillset whose tag list applies. Same grouping as the dan
# model uses, so "weakest skillset" and "what to search for" cannot drift apart.
DAN_GROUP_OF = {
    "chordjack": "jack", "jackspeed": "jack",
    "technical": "tech",
    "stream": "speed",
    "stamina": "stamina", "jumpstream": "stamina", "handstream": "stamina",
}

MSD_KEY = {
    "stream": "Stream", "jumpstream": "Jumpstream", "handstream": "Handstream",
    "stamina": "Stamina", "jackspeed": "JackSpeed", "chordjack": "Chordjack",
    "technical": "Technical", "overall": "Overall",
}

# Where to centre the search when there is no timing model to ask - relative to the dan
# estimate, which is a *ceiling*: the mean of the best twenty clears. Below the floor a
# chart teaches nothing; far above it is unclearable, and dan only counts clears at >=92%.
DAN_BELOW = 0.5
DAN_ABOVE = 2.0

# With a timing model the search is aimed by predicted accuracy instead, because the dan
# estimate sits far above how the player usually plays: on this machine the median clear is
# dan 10.8 while the estimate is 12.8, so a window hung off the estimate only ever offered
# charts in the top 15% of what had ever been cleared - "good day" charts, every time.
#
# Three bands rather than one target, because they train different things. The hard band
# is where a ceiling gets pushed; the middle is the working range; the easy band is where
# a shaky floor gets made solid. Quotas sum to a hundred.
#
# The bands are deliberately not contiguous. The gaps either side of the middle band keep
# each group recognisably itself rather than blurring into its neighbours.
RECOMMEND_BANDS = (
    ("push", 0.90, 0.93, 40),
    ("work", 0.95, 0.97, 40),
    ("solid", 0.98, 1.00, 20),
)

# The predicted accuracy is computed from MSD, so it is only worth anything on charts
# where MSD is telling the truth about difficulty. It often is not:
#
#   * a jumptrill or LN pack is cleared like a delta and rated MSD 21, because MinaCalc
#     scores the notes rather than the endurance - one such pack was being predicted at
#     99.9% and offered as easy practice while sitting above the player's own dan;
#   * vibro and trolljack go the other way, inflating MSD while staying trivial to clear.
#
# Either way the prediction is meaningless, so those charts are dropped rather than
# mis-sorted. The bound is the proxy's own p95 residual over the 869 charts with a known
# dan, so ordinary charts are unaffected.
MAX_DAN_MSD_GAP = 2.0

# Charts almost nobody has played are, on inspection, mostly somebody's first upload: a
# three-play "Dragon Rises", a thirty-five-play "Novii's Trash Pack". A hundred is low
# enough to keep genuinely niche practice packs - a Stepmania wristjack convert sits
# around 180 - while removing that tier, and it incidentally drops most of the charts
# whose beatmap page no longer exists.
MIN_PLAY_COUNT = 100

# How far past the bands the dan sweep reaches. Dan predicts a chart's MSD only loosely -
# within one dan band the p10-to-p90 difficulty spread is worth 2.4 points of accuracy,
# more than twice the ~1 point between one dan and the next - so the net has to be cast
# wider than the bands themselves and the catch sorted by prediction afterwards.
BAND_DAN_MARGIN = 1.5

# Walking the dan window in thin slices is the only way past the 48-chart response cap.
# A 0.25-wide slice comes back complete even in the densest pattern.
DAN_SLICE = 0.25

# The starting window rarely holds a hundred charts once the junk filters have run, so it
# is widened downwards until it does. Downwards on purpose: a chart below the player's dan
# can still be played accurately, which is the point of training a weak skillset, while one
# above it just cannot be cleared yet.
DAN_WIDEN = 1.0
MAX_DAN_BELOW = 4.0

# Vibro trips every pattern detector at once: a vibro "jack" chart also scores ~0.9 on
# chordjack and ~0.8 on tech, while a real jack trainer leaves every other pattern below
# 0.6. Dominance is the gap between the primary pattern and the runner-up.
#
# Its scale is pattern-specific - jack charts are distinctive (0.4 for a clean one) while
# stream and stamina inherently overlap (0.0 is normal there) - so charts are compared
# against the median of their own band rather than a fixed floor.

# `dominance` is only comparable within one pattern. A jack trainer leaves every other
# detector well below itself and scores 0.15-0.35; a stream chart cannot, because stream,
# jumpstream and stamina genuinely co-occur, so the same measure lands at 0.01-0.09 for
# every stream chart ever written. Shown raw it is a column of near-identical single digits
# that tells the reader nothing, so what gets displayed is the chart's rank within the
# candidates instead: "more focused than this share of the alternatives".
#
# Dan measures how hard a chart is to clear; MSD measures how hard MinaCalc thinks it is.
# Dumps and trolljacks inflate MSD without becoming harder to clear, so they sit far
# above the band's typical ratio. The baseline drifts with dan (~2.9 at dan 8, ~2.0 at
# dan 14) and with pattern, so this cut is band-relative too.
MAX_INFLATION = 1.15

STATUSES = "ranked,loved,graveyard"


def weaknesses(ratings: dict[str, float], limit: int = 3) -> list[tuple[str, float]]:
    """Skillsets ranked by how far they trail the player's overall rating."""
    overall = ratings.get("overall", 0.0)
    gaps = [(s, overall - ratings.get(s, 0.0)) for s in PATTERN_OF]
    gaps.sort(key=lambda x: x[1], reverse=True)
    return gaps[:limit]


def dominance(item: dict) -> float:
    """How far the chart's primary pattern leads every other pattern.

    `msd[skill] / msd["Overall"]` cannot do this job: MinaCalc's Overall is essentially
    the top skillset, so for a chart the index already filed under that pattern the
    ratio is 1.00 every time.
    """
    pats = {k: v for k, v in (item.get("patterns") or {}).items() if k != "ln"}
    primary = item.get("primaryPattern")
    others = [v for k, v in pats.items() if k != primary]
    return 1.0 - max(others) if others else 1.0


def _slices(lo: float, hi: float) -> list[tuple[float, float]]:
    out = []
    a = lo
    while a < hi:
        out.append((a, min(a + DAN_SLICE, hi)))
        a += DAN_SLICE
    return out


def _slice_items(pattern: str, lo: float, hi: float, session: requests.Session,
                 statuses: str, attempts: int = 3) -> list[dict]:
    """One dan slice, retried through the shared throttle.

    Treating a 429 as "no maps here" would quietly shrink the candidate pool and make
    the recommendations look random, so a rate limit costs a wait rather than a slice.
    """
    for attempt in range(attempts):
        MT_THROTTLE.wait()
        try:
            r = session.get(f"{MT_API}/api/snapshots/maps-search",
                            params={"keys": "4k", "patterns": pattern,
                                    "danMin": f"{lo:.2f}", "danMax": f"{hi:.2f}",
                                    "statuses": statuses, "pageSize": 48}, timeout=45)
            if r.status_code == 200:
                return (r.json() or {}).get("items") or []
            if r.status_code not in RETRY_STATUS:
                return []
        except (requests.RequestException, ValueError):
            if attempt == attempts - 1:
                return []
        MT_THROTTLE.penalise(1.5 * (2 ** attempt))
    return []


def _tag_items(tag: str, lo: float, hi: float, session: requests.Session,
               statuses: str, attempts: int = 3) -> list[dict]:
    """Charts whose name or tags contain `tag`, anywhere in the dan window.

    The free-text search is not sliced: it is already narrow enough to come back whole,
    and one request per tag is the entire point of preferring it to another sweep.
    """
    for attempt in range(attempts):
        MT_THROTTLE.wait()
        try:
            r = session.get(f"{MT_API}/api/snapshots/maps-search",
                            params={"keys": "4k", "q": tag, "danMin": f"{lo:.2f}",
                                    "danMax": f"{hi:.2f}", "statuses": statuses,
                                    "pageSize": 48}, timeout=45)
            if r.status_code == 200:
                return (r.json() or {}).get("items") or []
            if r.status_code not in RETRY_STATUS:
                return []
        except (requests.RequestException, ValueError):
            if attempt == attempts - 1:
                return []
        MT_THROTTLE.penalise(1.5 * (2 ** attempt))
    return []


def _haystack(item: dict) -> str:
    return " ".join([str(item.get("title") or ""), str(item.get("version") or ""),
                     " ".join(item.get("patternTags") or [])]).lower()


def matched_tags(item: dict, tags: list[str]) -> list[str]:
    """Which of `tags` the chart names itself after.

    Whole words only: `tech` must not match Techno, and `anchor` must not be claimed by
    every chart that happens to contain anchorjack - the caller lists both when it wants
    both.
    """
    hay = _haystack(item)
    return [t for t in tags if re.search(rf"\b{re.escape(t)}\b", hay)]


def search_maps(pattern: str, dan_min: float, dan_max: float,
                session: requests.Session, statuses: str = STATUSES,
                tags: list[str] | None = None, slices_only: bool = False) -> list[dict]:
    """Every chart of `pattern` in the dan window, collected slice by slice.

    Then the same window searched by name for each of `tags`. That second pass is what
    reaches charts the pattern classifier files elsewhere - a minijack trainer the index
    calls "stream" is exactly the chart somebody weak at jacks is looking for.
    """
    seen: dict[int, dict] = {}
    for lo, hi in _slices(dan_min, dan_max):
        for it in _slice_items(pattern, lo, hi, session, statuses):
            bid = int(it.get("beatmapId") or 0)
            if bid:
                seen.setdefault(bid, it)
    if slices_only:
        return list(seen.values())
    for tag in tags or []:
        for it in _tag_items(tag, dan_min, dan_max, session, statuses):
            bid = int(it.get("beatmapId") or 0)
            dan = float((it.get("dan") or {}).get("rawDan") or 0.0)
            # The free-text search honours the dan window only loosely, so it is enforced
            # here rather than trusted.
            if bid and dan_min <= dan <= dan_max:
                seen.setdefault(bid, it)
    return list(seen.values())


def recommend(skill: str, player_dan: float | None, played_ids: set[int],
              session: requests.Session, limit: int = 100, model=None) -> list[dict]:
    """Charts that train `skill`, sized to the player's dan, that they have not played."""
    pattern = PATTERN_OF.get(skill)
    if pattern is None or player_dan is None:
        return []

    group = DAN_GROUP_OF.get(skill, "")
    tags = SEARCH_TAGS.get(group, [])
    avoid = AVOID_TAGS.get(group, [])

    # Sweep everything the three bands could possibly reach, in one dan window. The
    # fallback for a first run, before there are enough plays to fit a model, is the dan
    # estimate with its old window.
    centre, below, above = player_dan, DAN_BELOW, DAN_ABOVE
    edges = []
    if model:
        for _, lo_acc, hi_acc, _ in RECOMMEND_BANDS:
            for acc in (lo_acc, hi_acc):
                msd = model.msd_for_accuracy(acc)
                if msd:
                    edges.append(dan_proxy({"overall": msd, "peak": msd}, acc))
    if edges:
        centre = (min(edges) + max(edges)) / 2.0
        half = (max(edges) - min(edges)) / 2.0 + BAND_DAN_MARGIN
        below = above = half

    # Widen downwards until there are enough survivors to fill the list. Each round only
    # fetches the band it has newly exposed, so the extra reach costs a few requests
    # rather than repeating the whole sweep.
    items: dict[int, dict] = {}
    first = below
    rows: list[dict] = []
    msd_key = MSD_KEY[skill]

    while True:
        lo = centre - below
        hi = centre + above if below == first else centre - below + DAN_WIDEN
        for it in search_maps(pattern, lo, hi, session, tags=tags,
                              slices_only=below != DAN_BELOW):
            bid = int(it.get("beatmapId") or 0)
            if bid:
                items.setdefault(bid, it)

        rows = []
        for it in items.values():
            if int(it.get("beatmapId") or 0) in played_ids:
                continue
            # A chart that calls itself vibro is out before any of the numbers get a say.
            # The index's own `vibro` flag is set on almost nothing, so its name is the
            # better tell.
            if matched_tags(it, avoid):
                continue
            msd = it.get("msd") or {}
            value = float(msd.get(msd_key) or 0.0)
            dan = float((it.get("dan") or {}).get("rawDan") or 0.0)
            if value <= 0 or dan <= 0:
                continue
            if int(it.get("playCount") or 0) < MIN_PLAY_COUNT:
                continue
            # What the chart's MSD implies its dan should be. Far from the real one means
            # the two disagree about what makes it hard, and the MSD-based prediction
            # below cannot be trusted for it.
            overall = float(msd.get("Overall") or 0.0)
            if abs(dan - (_DAN_PER_MSD * overall + _DAN_MSD_OFFSET)) > MAX_DAN_MSD_GAP:
                continue
            rows.append({
                "tags": matched_tags(it, tags),
                "beatmap_id": it["beatmapId"],
                "title": it.get("title", "?"),
                "label": f"{it.get('title', '?')} [{it.get('version', '?')}]",
                "creator": it.get("creator", ""),
                "status": it.get("status", ""),
                "stars": float(it.get("stars") or 0.0),
                "bpm": float(it.get("bpm") or 0.0),
                "length": int(it.get("length") or 0),
                "dan": dan,
                "dan_label": (it.get("dan") or {}).get("label", "-"),
                "skill_msd": value,
                "msd_overall": float(msd.get("Overall") or 0.0),
                "play_count": int(it.get("playCount") or 0),
                "focus": dominance(it),
                "inflation": value / dan,
                "flagged_vibro": bool(it.get("vibro")),
                # The index does not publish an OD, so the guess is made at the one most
                # 4K charts are set to. It moves the answer by well under the fit's own error.
                "predicted": (model.accuracy_for(float(msd.get("Overall") or 0.0))
                              if model else None),
            })
        # Stop once every band can be filled, not once the total looks big enough: a
        # sweep can hold three hundred charts and still have nothing in the hard band.
        scale = limit / sum(q for *_, q in RECOMMEND_BANDS)
        filled = all(sum(1 for r in rows if r.get("predicted") is not None
                         and lo_a <= r["predicted"] <= hi_a) >= max(1, round(q * scale))
                     for _, lo_a, hi_a, q in RECOMMEND_BANDS)
        if filled or below >= first + MAX_DAN_BELOW:
            break
        below += DAN_WIDEN

    if not rows:
        return []

    infl_cut = statistics.median(r["inflation"] for r in rows) * MAX_INFLATION
    dom_cut = statistics.median(r["focus"] for r in rows)
    for r in rows:
        # Not a filter any more but a rank: both cuts are medians, so they would discard
        # about half of any sweep by construction, and a band that is short on charts is
        # better filled with the least bad of what is left than left short. Six good charts
        # still beat twelve padded with vibro; this only decides what comes after the sixth.
        r["dirty"] = (bool(r["flagged_vibro"]) or r["focus"] < dom_cut
                      or r["inflation"] > infl_cut)

    # Sorted inside a band by whether it is junk, then by whether it names the skill being
    # trained - a "[Longjack/Tech]" pack is a more deliberate jack trainer than one that
    # merely mentions jacks - then by the classifier's own opinion.
    def rank(r: dict) -> tuple:
        return (r["dirty"], -len(r["tags"]), -round(r["focus"], 2),
                abs(r["dan"] - centre))

    # Focus is turned into a rank over the candidates before anything is picked, so the
    # number that reaches the screen means the same thing for every pattern.
    order = sorted(rows, key=lambda r: r["focus"])
    for i, r in enumerate(order):
        r["focus"] = (i + 0.5) / len(order) if len(order) > 1 else 1.0

    # Rate edits and re-hosts are uploaded as ordinary beatmaps, so one song can occupy a
    # whole band as 0.9x / 1.0x / 1.1x / 1.2x. Keyed on the title alone: keying on
    # (title, creator) let "Makiba" through three times from three different uploaders,
    # and one difficulty of a song is enough when the point is to train a skill.
    out: list[dict] = []
    songs: set[str] = set()
    scale = limit / sum(q for *_, q in RECOMMEND_BANDS)
    for name, lo_acc, hi_acc, quota in RECOMMEND_BANDS:
        want = max(1, round(quota * scale))
        members = sorted((r for r in rows
                          if r.get("predicted") is not None
                          and lo_acc <= r["predicted"] <= hi_acc), key=rank)
        taken = 0
        for r in members:
            song = " ".join(r["title"].casefold().split())
            if song in songs:
                continue
            songs.add(song)
            r["band"] = name
            r["url"] = f"https://osu.ppy.sh/b/{r['beatmap_id']}"
            out.append(r)
            taken += 1
            if taken >= want:
                break
    return out
