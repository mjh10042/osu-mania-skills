"""Score collection from the official osu! server and the mamesosu private server.

Neither path needs an API key:
  * official  -> mania-tracker's public backend proxies osu! API v2 top plays
  * mamesosu  -> bancho.py's open v1 API
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

MT_API = "https://api.mania-tracker.com"
MAME_API = "https://api.mamesosu.net"
UA = "mania-skills/1.0 (personal skillset merger)"

# osu! stable mod bitfield values that change music rate.
MOD_DT = 1 << 6
MOD_HT = 1 << 8
MOD_NC = 1 << 9


@dataclass
class Play:
    source: str            # "official" | "mamesosu"
    beatmap_id: int
    md5: str | None
    title: str
    version: str
    accuracy: float        # 0..1
    pp: float
    rate: float            # music rate, 1.0 unless DT/HT/custom
    mods: str
    played_at: str
    score_id: str = ""
    key_count: int = 0
    # osu! judgement counts, keyed the way core.wife names them, and the OD they were
    # judged at. Both are needed to say anything about wife3 accuracy; without them the
    # play simply has none.
    judgements: dict[str, int] = field(default_factory=dict)
    od: float = 0.0
    msd: dict[str, float] = field(default_factory=dict)
    dan: float | None = None
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.title} [{self.version}]"


class SourceError(RuntimeError):
    """One server could not be read. Carries which one, and why in a translatable code.

    The reason is a code rather than a sentence because the only place it is shown is the
    UI, which speaks three languages; a raw `404 Client Error: ... ?id=999999&scope=best`
    is not something to put in front of somebody who just opened the exe.
    """

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


def _get_json(session: requests.Session, url: str, source: str,
              params: dict | None = None) -> dict:
    """GET and decode, turning every failure into a named SourceError."""
    try:
        r = session.get(url, params=params, timeout=60)
    except requests.RequestException as exc:
        raise SourceError(source, "offline") from exc
    # 422 is bancho.py rejecting the name against its own username pattern, which for
    # somebody who mistyped theirs is the same answer as 404: no such player.
    if r.status_code in (404, 422):
        raise SourceError(source, "not_found")
    if r.status_code >= 400:
        raise SourceError(source, "server")
    try:
        return r.json()
    except ValueError as exc:
        raise SourceError(source, "server") from exc


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16)
    s.mount("https://", adapter)
    return s


_local = threading.local()


def thread_session() -> requests.Session:
    """A Session per worker thread - requests.Session is not safe to share."""
    s = getattr(_local, "session", None)
    if s is None:
        s = _local.session = _session()
    return s


# The limiter is a rolling budget, so the right number of parallel requests is not a
# constant: it depends on the account, the hour, and how much this client has already
# spent. Rather than hard-code a guess, the gate below finds it - additive increase while
# requests come back clean, halving on every rejection.
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 8


class Throttle:
    """Shared back-pressure *and* shared concurrency for mania-tracker's quota.

    Its limiter is a rolling budget rather than a per-connection cap, so one caller
    getting a 429 means every caller should back off. Sleeping independently just burns
    the budget again the moment each one wakes up. Every mania-tracker endpoint we use -
    ratings, dan lookups, map search - goes through this one instance.

    Callers that only need the back-off use `wait()`. Callers that want to run several
    requests at once use `acquire()`/`release(ok)` instead, which additionally holds them
    to however many the server is currently tolerating.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._resume = 0.0
        self._limit = 2.0
        self._inflight = 0

    def wait(self) -> None:
        while True:
            with self._cond:
                delay = self._resume - time.monotonic()
            if delay <= 0:
                return
            time.sleep(min(delay, 0.5))

    def acquire(self) -> None:
        """Block until it is both this caller's turn and past any back-off."""
        with self._cond:
            while True:
                delay = self._resume - time.monotonic()
                if delay <= 0 and self._inflight < max(MIN_CONCURRENCY, int(self._limit)):
                    self._inflight += 1
                    return
                self._cond.wait(timeout=min(0.25, delay) if delay > 0 else 0.25)

    def release(self, ok: bool) -> None:
        """Hand the slot back. `ok` is False for a rejection, which costs half the width."""
        with self._cond:
            self._inflight -= 1
            if ok:
                # +1/limit per success is one extra slot per full clean round, so the
                # climb slows down as it approaches whatever the budget actually allows.
                self._limit = min(MAX_CONCURRENCY, self._limit + 1.0 / self._limit)
            else:
                self._limit = max(MIN_CONCURRENCY, self._limit / 2.0)
            self._cond.notify_all()

    def concurrency(self) -> int:
        with self._cond:
            return max(MIN_CONCURRENCY, int(self._limit))

    def penalise(self, seconds: float) -> None:
        with self._cond:
            self._resume = max(self._resume, time.monotonic() + seconds)
            self._cond.notify_all()


MT_THROTTLE = Throttle()

RETRY_STATUS = (429, 500, 502, 503, 504)


def _lazer_judgements(stats: dict) -> dict[str, int]:
    """osu! API v2 statistics -> the six mania judgements, largest window last."""
    return {"perfect": int(stats.get("perfect") or 0), "great": int(stats.get("great") or 0),
            "good": int(stats.get("good") or 0), "ok": int(stats.get("ok") or 0),
            "meh": int(stats.get("meh") or 0), "miss": int(stats.get("miss") or 0)}


def _rate_from_lazer_mods(mods: list[dict]) -> tuple[float, str]:
    """Lazer mods are objects; DT/HT may carry a custom speed_change."""
    rate = 1.0
    names = []
    for m in mods:
        acr = m.get("acronym", "")
        if acr == "CL":
            continue
        names.append(acr)
        settings = m.get("settings") or {}
        if acr in ("DT", "NC"):
            rate = float(settings.get("speed_change", 1.5))
        elif acr in ("HT", "DC"):
            rate = float(settings.get("speed_change", 0.75))
    return rate, ",".join(names) or "NM"


def _rate_from_stable_mods(bits: int) -> tuple[float, str]:
    names = []
    rate = 1.0
    if bits & (MOD_DT | MOD_NC):
        rate = 1.5
        names.append("NC" if bits & MOD_NC else "DT")
    if bits & MOD_HT:
        rate = 0.75
        names.append("HT")
    return rate, ",".join(names) or "NM"


def fetch_official(user: str | int, session: requests.Session | None = None) -> list[Play]:
    """Top plays from the official osu! server, via mania-tracker's snapshot endpoint.

    Takes a username as readily as a numeric id, because the endpoint resolves both and a
    player knows their own name while almost nobody knows their id.
    """
    s = session or _session()
    who = quote(str(user).strip(), safe="")
    data = _get_json(s, f"{MT_API}/api/profiles/{who}/snapshot", "official")

    plays = []
    for sc in data.get("bestScores", []):
        bm = sc.get("beatmap") or {}
        bs = sc.get("beatmapset") or {}
        rate, mods = _rate_from_lazer_mods(sc.get("mods") or [])
        plays.append(Play(
            source="official",
            beatmap_id=int(sc.get("beatmap_id") or bm.get("id") or 0),
            md5=bm.get("checksum"),
            title=bs.get("title", "?"),
            version=bm.get("version", "?"),
            accuracy=float(sc.get("accuracy") or 0.0),
            pp=float(sc.get("pp") or 0.0),
            rate=rate,
            mods=mods,
            played_at=str(sc.get("ended_at") or ""),
            score_id=str(sc.get("id") or ""),
            key_count=int(float(bm.get("cs") or 0)),
            judgements=_lazer_judgements(sc.get("statistics") or {}),
            # osu! calls the overall difficulty "accuracy" on a beatmap.
            od=float(bm.get("accuracy") or 0.0),
        ))
    return [p for p in plays if p.beatmap_id]


def fetch_official_skills(user_id: int, session: requests.Session | None = None) -> dict:
    """mania-tracker's own computed skill ratings - used as ground truth when fitting."""
    s = session or _session()
    r = s.get(f"{MT_API}/api/profiles/{user_id}/skills", timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_mamesosu(user: str | int, limit: int = 100,
                   session: requests.Session | None = None) -> list[Play]:
    """Top mania plays from the mamesosu private server (bancho.py v1 API).

    bancho.py looks a player up by `id` or by `name`, never both, so which parameter to
    send is decided by what was typed.
    """
    s = session or _session()
    who = str(user).strip()
    key = "id" if who.isdigit() else "name"
    data = _get_json(s, f"{MAME_API}/v1/get_player_scores", "mamesosu",
                     params={key: who, "scope": "best", "mode": 3, "limit": limit})
    if data.get("status") != "success":
        raise SourceError("mamesosu", "not_found")

    plays = []
    for sc in data.get("scores") or []:
        bm = sc.get("beatmap") or {}
        rate, mods = _rate_from_stable_mods(int(sc.get("mods") or 0))
        plays.append(Play(
            source="mamesosu",
            beatmap_id=int(bm.get("id") or 0),
            md5=bm.get("md5"),
            title=bm.get("title", "?"),
            version=bm.get("version", "?"),
            accuracy=float(sc.get("acc") or 0.0) / 100.0,
            pp=float(sc.get("pp") or 0.0),
            rate=rate,
            mods=mods,
            played_at=str(sc.get("play_time") or ""),
            score_id=str(sc.get("id") or ""),
            key_count=int(float(bm.get("cs") or 0)),
            # bancho.py keeps the stable names: geki is the 320 and katu the 200.
            judgements={"perfect": int(sc.get("ngeki") or 0),
                        "great": int(sc.get("n300") or 0),
                        "good": int(sc.get("nkatu") or 0),
                        "ok": int(sc.get("n100") or 0),
                        "meh": int(sc.get("n50") or 0),
                        "miss": int(sc.get("nmiss") or 0)},
            od=float(bm.get("od") or 0.0),
        ))
    return [p for p in plays if p.beatmap_id]
