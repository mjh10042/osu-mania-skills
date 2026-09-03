"""Per-play MSD computation with on-disk caching.

Rate 1.0 plays are computed locally with msd.exe (verified to match mania-tracker
exactly). Rate-changing mods use mania-tracker's rated endpoint when reachable,
because msd.exe does not expose MinaCalc's music_rate parameter; local time-scaling
is the offline fallback and runs ~1.2% high at 1.5x.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .beatmap import LocalSongs, cache_dir, fetch_osu_file, parse_mania_rows
from .dan import (MIN_ACCURACY, DanUnavailable, DanWindows, dan_proxy,
                  fetch_map_dan)
from .msd import SKILLSETS, SUPPORTED_KEYS, MsdError, compute
from .sources import MAX_CONCURRENCY, MT_THROTTLE, thread_session

MT_API = "https://api.mania-tracker.com"

# osu.ppy.sh tolerates a wide fan-out; mania-tracker does not, and MT_THROTTLE is now
# what holds every one of its endpoints to whatever the budget currently allows.
WORKERS = 8

# Dan lookups run as wide as the shared gate is currently allowing, which climbs while
# the server keeps answering and halves the moment it stops. The pool is only the ceiling
# - MT_THROTTLE decides how many of these threads are actually in flight.
DAN_WORKERS = MAX_CONCURRENCY

# How many fetched dans may be held in memory before the cache is written. The dan pass
# can run for many minutes against a quota that will not give the same answers back
# quickly, so losing it to a closed window is the one failure worth paying a small
# write for. The MSD pass has no equivalent need - it recomputes in milliseconds.
DAN_SAVE_EVERY = 20

_UNAVAILABLE = object()


class MsdCache:
    """Rating cache. Safe to share across the rating worker pool."""

    def __init__(self) -> None:
        self.path = cache_dir() / "msd.json"
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}
        self._dirty = False
        self._lock = threading.Lock()

    def key(self, beatmap_id: int, rate: float) -> str:
        """Cache key for a chart *dan*, which is a fact about a beatmap id.

        Keyed by id because that is the only thing mania-tracker can be asked about. Where
        several files share an id its answer is whatever it has for that id, which this
        cannot improve on.
        """
        return f"{beatmap_id}@{rate:.3f}"

    def msd_key(self, md5: str | None, beatmap_id: int, rate: float) -> str:
        """Cache key for an *MSD*, which is a fact about the chart file itself.

        Separate from `key` because a beatmap id is not unique on disk - a rate-changer
        pack keeps the id of the chart it was copied from - so sharing one entry let nine
        different rates of the same song overwrite each other's difficulty.
        """
        return f"md5:{md5.lower()}@{rate:.3f}" if md5 else self.key(beatmap_id, rate)

    def get(self, beatmap_id: int, rate: float):
        return self.data.get(self.key(beatmap_id, rate))

    def put(self, beatmap_id: int, rate: float, value: dict) -> None:
        with self._lock:
            self.data[self.key(beatmap_id, rate)] = value
            self._dirty = True

    def get_msd(self, md5: str | None, beatmap_id: int, rate: float):
        return self.data.get(self.msd_key(md5, beatmap_id, rate))

    def put_msd(self, md5: str | None, beatmap_id: int, rate: float, value: dict) -> None:
        with self._lock:
            self.data[self.msd_key(md5, beatmap_id, rate)] = value
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            blob = json.dumps(self.data)
            self._dirty = False
        self.path.write_text(blob, encoding="utf-8")


def _mt_key(skill: str) -> str:
    return {"jackspeed": "JackSpeed"}.get(skill, skill.capitalize())


def _rated_analysis(beatmap_id: int, rate: float, session: requests.Session):
    """MSD *and* dan for a rate-changed chart, or None if the request could not be made.

    One endpoint answers both. Reading only the MSD out of it - which is what this used
    to do - left `fetch_dans` to come back for the very same URL and the very same chart,
    so every rate-changed clear cost two requests out of a budget that allows about two a
    second. A returned dan of None means the chart genuinely has none, and is worth
    caching as such.

    It also goes through MT_THROTTLE now. It did not before, so its rejections were
    invisible to the shared back-off: it could keep the server saying 429 while every
    other caller politely waited out a penalty it had no way to see coming.
    """
    MT_THROTTLE.acquire()
    ok = False
    try:
        r = session.get(f"{MT_API}/api/chart-analysis/rate",
                        params={"beatmapId": beatmap_id, "rate": f"{rate:.2f}"}, timeout=30)
        ok = r.status_code < 400
        if r.status_code != 200:
            return None
        body = r.json() or {}
        msd = body.get("msd") or {}
        out = {}
        for s in SKILLSETS:
            k = _mt_key(s)
            if k not in msd:
                return None
            out[s] = float(msd[k])
        raw = (body.get("dan") or {}).get("rawDan")
        return out, (float(raw) if raw is not None else None)
    except (requests.RequestException, ValueError):
        return None
    finally:
        MT_THROTTLE.release(ok)


def wants_dan(play) -> bool:
    """Only 4K clears above the dan bar can ever move the estimate - skip the rest.

    Each dan lookup is a mania-tracker request and that API is quota limited, so not
    asking is worth more than asking quickly.
    """
    return play.key_count == 4 and play.accuracy >= MIN_ACCURACY


def _resolve_dan(play, cache: MsdCache, entry: dict, session: requests.Session,
                 allow_remote: bool) -> None:
    """Attach the chart's dan value, fetching it once per (beatmap, rate) and caching."""
    if "dan" in entry:
        play.dan = entry["dan"]
        return
    if not allow_remote or not wants_dan(play):
        return
    try:
        play.dan = fetch_map_dan(play.beatmap_id, play.rate, session)
    except DanUnavailable:
        return  # leave it uncached so the next refresh tries again
    entry["dan"] = play.dan
    cache.put(play.beatmap_id, play.rate, entry)


def _dan_of(head, session: requests.Session):
    """One chart's dan, or the sentinel when the request itself could not be completed."""
    try:
        return fetch_map_dan(head.beatmap_id, head.rate, session)
    except DanUnavailable:
        return _UNAVAILABLE


def fetch_dans(plays: list, cache: MsdCache, session: requests.Session | None = None,
               progress: Callable[[int, int], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> int:
    """Fill in the chart dans that can still change the answer. Returns how many were fetched.

    Kept apart from `rate_plays` because the two have nothing in common operationally:
    MSD is computed from local files in milliseconds, while a dan is one request against
    an API that allows roughly two a second and rejects the rest.

    So the queue is not merely ordered, it is *pruned*. Charts are asked for best-first,
    and a chart is dropped for good once every skillset window it could enter has closed
    above the most its dan could possibly be (see `DanWindows`). With a full local score
    database that is the difference between asking about every clear ever passed on the
    machine and asking about the few hundred that decide the number.
    """
    ses = session or thread_session()
    windows = DanWindows()
    todo = []
    for p in plays:
        if not p.msd:
            continue
        entry = cache.get(p.beatmap_id, p.rate)
        if p.dan is None and entry and "dan" in entry:
            p.dan = entry["dan"]
        if p.dan is not None:
            windows.add(p.msd, p.accuracy, p.dan)   # already known - it holds a slot
            continue
        if not wants_dan(p) or (entry and "dan" in entry):
            continue
        todo.append(p)

    # One request serves every play of the same chart at the same rate.
    charts: dict[tuple[int, float], list] = {}
    for p in todo:
        charts.setdefault((p.beatmap_id, round(p.rate, 3)), []).append(p)
    queue = sorted(charts.values(),
                   key=lambda g: -max(dan_proxy(p.msd, p.accuracy) for p in g))

    total = len(queue)
    done = fetched = 0
    i = 0
    # `done` counts charts *resolved*, pruned ones included: a skip is a real answer
    # ("this cannot matter"), and hiding it would leave the progress line stuck.
    with ThreadPoolExecutor(max_workers=DAN_WORKERS) as pool:
        while i < total:
            if should_stop and should_stop():
                break
            # Sized from the gate, so a healthy quota gets a wide wave and a throttled
            # one narrows to a trickle without any of this code knowing the limit.
            width = max(1, min(DAN_WORKERS, MT_THROTTLE.concurrency()))
            wave = []
            while i < total and len(wave) < width:
                group = queue[i]
                i += 1
                head = max(group, key=lambda p: p.accuracy)
                if not windows.can_matter(head.msd, head.accuracy):
                    done += 1
                    continue
                wave.append((group, head))
            if not wave:
                if progress:
                    progress(done, total)
                continue

            # A wave is small on purpose: the windows only tighten between waves, so a
            # wide one would spend requests on charts the previous result had just ruled out.
            values = list(pool.map(lambda gh: _dan_of(gh[1], thread_session()), wave))
            for (group, head), value in zip(wave, values):
                done += 1
                if value is _UNAVAILABLE:
                    continue  # uncached, so the next run retries it
                entry = cache.get(head.beatmap_id, head.rate) or {}
                entry["dan"] = value
                cache.put(head.beatmap_id, head.rate, entry)
                for p in group:
                    p.dan = value
                    windows.add(p.msd, p.accuracy, value)
                fetched += 1
            if fetched and fetched % DAN_SAVE_EVERY < len(wave):
                cache.save()          # quitting here must not throw the run away
            if progress:
                progress(done, total)
    cache.save()
    return fetched


def rate_play(play, cache: MsdCache, session: requests.Session,
              songs: LocalSongs | None = None, allow_remote: bool = True,
              dans: bool = True) -> str:
    """Fill play.msd, play.key_count and play.dan. Returns a status string.

    `dans=False` still reads cached dans but never requests one, leaving that to
    `fetch_dans` - which can order the requests and be interrupted.
    """
    hit = cache.get_msd(play.md5, play.beatmap_id, play.rate)
    if hit and hit.get("msd"):
        play.msd = {k: float(hit["msd"][k]) for k in SKILLSETS}
        play.key_count = int(hit.get("keys") or play.key_count)
        play.od = play.od or float(hit.get("od") or 0.0)
        _resolve_dan(play, cache, cache.get(play.beatmap_id, play.rate) or {},
                     session, allow_remote and dans)
        return "cached"

    osu_text = songs.read(play.beatmap_id, play.md5) if songs else None
    if osu_text is None:
        osu_text = fetch_osu_file(play.beatmap_id, session)
    if osu_text is None:
        return "no-beatmap"

    keys, notes, od = parse_mania_rows(osu_text)
    play.key_count = keys or play.key_count
    # The server-reported OD wins where there is one; the file is the fallback, and the
    # only source at all for a local play.
    play.od = play.od or od
    if not notes:
        return "not-mania"
    if keys not in SUPPORTED_KEYS:
        return f"unsupported-{keys}k"

    msd = chart_dan = None
    analysis = None
    if abs(play.rate - 1.0) > 1e-9 and allow_remote:
        analysis = _rated_analysis(play.beatmap_id, play.rate, session)
    if analysis is not None:
        msd, chart_dan = analysis
    if msd is None:
        try:
            msd = compute(notes, play.rate)
        except (MsdError, OSError) as exc:
            return f"msd-error: {exc}"

    play.msd = msd
    cache.put_msd(play.md5, play.beatmap_id, play.rate,
                  {"msd": msd, "keys": keys, "od": od})

    dan_entry = cache.get(play.beatmap_id, play.rate) or {}
    if analysis is not None:
        # Came back in the same response, so recording it here is the whole saving.
        dan_entry["dan"] = chart_dan
        play.dan = chart_dan
        cache.put(play.beatmap_id, play.rate, dan_entry)
    _resolve_dan(play, cache, dan_entry, session, allow_remote and dans)
    return "ok"


def rate_plays(plays: list, cache: MsdCache, songs: LocalSongs | None = None,
               allow_remote: bool = True, workers: int = WORKERS,
               progress: Callable[[int, int], None] | None = None,
               dans: bool = True) -> None:
    """Rate a whole score list, overlapping the network waits.

    Rating is almost entirely waiting on osu.ppy.sh and mania-tracker, so this is I/O
    parallel rather than CPU parallel. Plays that share a chart at the same rate -
    common when a map was played on both servers - are rated once and the result copied.
    """
    if songs is None:
        songs = LocalSongs().load()

    groups: dict[tuple[int, float], list] = {}
    for p in plays:
        groups.setdefault((p.beatmap_id, round(p.rate, 3)), []).append(p)

    total = len(groups)
    done = 0
    lock = threading.Lock()

    def work(group: list) -> None:
        nonlocal done
        # Rate the most accurate play of the group so the dan lookup is not skipped for
        # a chart that a sibling play does need it for.
        head = max(group, key=lambda p: p.accuracy)
        rate_play(head, cache, thread_session(), songs, allow_remote, dans)
        for other in group:
            if other is head:
                continue
            other.msd = dict(head.msd)
            other.key_count = head.key_count
            other.dan = head.dan
        with lock:
            done += 1
            if progress:
                progress(done, total)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, groups.values()))
