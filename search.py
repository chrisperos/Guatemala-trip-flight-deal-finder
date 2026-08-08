"""Thin, retrying wrapper over fast-flights (Google Flights).

Everything downstream consumes `Quote` objects. The important part beyond raw
price is the itinerary detail -- stop count, layover lengths, overnight
layovers -- because those are exactly the tradeoffs a human (or Claude) needs
in order to say "the $340 one is not actually better than the $395 one."
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import date as _date, datetime, timedelta
from typing import Callable, Iterable, Sequence

import gzip
import threading
import urllib.parse
import urllib.request
import zlib

from fast_flights import FlightQuery, Passengers, create_query, get_flights
from fast_flights.integrations.base import FetchIntegration

import config


class _RateLimiter:
    """Token bucket shared across worker threads.

    Google tolerates short bursts fine -- 420 queries at 8 threads came back
    100% clean -- but starts refusing somewhere past ~1-2k cumulative requests
    from one IP. A daily unattended scan has no reason to sprint, so we trade
    wall-clock time for a complete, trustworthy dataset.
    """

    def __init__(self, rate_per_sec: float):
        self.interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            time.sleep(wait)


_limiter = _RateLimiter(config.REQUESTS_PER_SECOND)


def set_rate(rate_per_sec: float) -> None:
    global _limiter
    _limiter = _RateLimiter(rate_per_sec)


# --------------------------------------------------------------- scan deadline
# Module-level, set ONCE per scan. It previously lived inside parallel(), which
# meant every phase got its own fresh 90 minutes -- four phases, so a worst
# case of six hours from something documented as a 90-minute ceiling. A budget
# that resets whenever you spend it is not a budget.
_scan_started_at: float | None = None


def begin_scan() -> None:
    """Start the whole-scan clock. Call once, before the first phase."""
    global _scan_started_at
    _scan_started_at = time.monotonic()


def elapsed_min() -> float:
    if _scan_started_at is None:
        return 0.0
    return (time.monotonic() - _scan_started_at) / 60


def deadline_exceeded() -> bool:
    if _scan_started_at is None:
        return False
    return elapsed_min() > config.SCAN_DEADLINE_MIN


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")


class _PlainFetcher(FetchIntegration):
    """stdlib urllib with a Chrome User-Agent. No TLS impersonation at all.

    This is the default, and the reason is worth recording. fast-flights ships
    a primp client that forges a real browser's TLS fingerprint. Anthropic's
    cloud sandbox routes egress through a proxy that RESETS forged TLS
    handshakes -- every impersonation profile, against every host including
    discord.com. Two scheduled runs died on it.

    Measured side by side against the impersonating client on the same query:
    identical results, 5 itineraries, same cheapest fare. Impersonation buys
    nothing here. The ONLY thing that matters is the User-Agent header -- with
    Python's default UA, Google serves a stripped page the parser cannot read,
    which is precisely the "200 OK but no flight data" symptom reported from
    the sandbox.
    """

    URL = "https://www.google.com/travel/flights"

    def __init__(self, timeout: float, proxy: str | None = None):
        self.timeout = timeout
        self.proxy = proxy

    def _opener(self):
        if self.proxy:
            handler = urllib.request.ProxyHandler({"http": self.proxy,
                                                   "https": self.proxy})
            return urllib.request.build_opener(handler)
        return urllib.request.build_opener()

    def fetch_html(self, q, /) -> str:
        from fast_flights.querying import Query as _Q

        params = q.params() if isinstance(q, _Q) else {"q": q}
        url = f"{self.URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": CHROME_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Upgrade-Insecure-Requests": "1",
        })
        with self._opener().open(req, timeout=self.timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        return raw.decode("utf-8", "replace")


class _TimeoutFetcher(FetchIntegration):
    """fast-flights' own client, but with a timeout it otherwise never sets.

    This is not a micro-optimisation. `fetch_flights_html` builds its primp
    Client with no timeout at all, so a host that silently blackholes traffic
    -- exactly what a cloud sandbox on "Trusted" network access does to
    google.com -- makes every request hang forever rather than error. With
    retries and 8 worker threads on top, a 20-minute scan became a 2-hour
    zombie that produced no output and no error.

    Using the library's own FetchIntegration hook rather than monkeypatching,
    so a version bump can't silently undo it.
    """

    URL = "https://www.google.com/travel/flights"

    def __init__(self, timeout: float, proxy: str | None = None):
        self.timeout = timeout
        self.proxy = proxy

    def fetch_html(self, q, /) -> str:
        from primp import Client
        from fast_flights.querying import Query as _Q

        client = Client(
            impersonate="chrome_145", impersonate_os="macos", referer=True,
            proxy=self.proxy, cookie_store=True,
            timeout=self.timeout, connect_timeout=min(self.timeout, 10.0),
        )
        params = q.params() if isinstance(q, _Q) else {"q": q}
        return client.get(self.URL, params=params).text


_fetcher = _PlainFetcher(config.REQUEST_TIMEOUT_SEC, config.PROXY)


def _probe_query():
    from datetime import date, timedelta

    probe = date.today() + timedelta(days=45)
    return create_query(
        flights=[FlightQuery(date=probe.isoformat(), from_airport="MIA",
                             to_airport="GUA")],
        trip="one-way", passengers=Passengers(adults=1),
        currency="USD", language="en-US",
    )


def preflight() -> tuple[bool, str]:
    """Prove we can actually get flight data before firing thousands of queries.

    Tries the plain fetcher first, then the TLS-impersonating one. Whichever
    returns real itineraries becomes the fetcher for the whole scan, so the
    same code works on a home connection and inside a sandbox that resets
    forged TLS handshakes -- without anyone having to configure which.
    """
    global _fetcher

    q = _probe_query()
    attempts: list[str] = []

    for name, cand in (("plain", _PlainFetcher(config.REQUEST_TIMEOUT_SEC,
                                               config.PROXY)),
                       ("impersonated", _TimeoutFetcher(config.REQUEST_TIMEOUT_SEC,
                                                        config.PROXY))):
        try:
            res = list(get_flights(q, integration=cand))
        except Exception as e:  # noqa: BLE001
            attempts.append(f"{name}: {type(e).__name__}: {str(e)[:90]}")
            continue
        if not res:
            attempts.append(f"{name}: connected but returned 0 itineraries "
                            "(served a stripped page)")
            continue
        _fetcher = cand
        return True, f"network ok via {name} fetcher ({len(res)} itineraries)"

    return False, (
        "could not retrieve flight data by any method. Attempts -- "
        + " | ".join(attempts)
        + ". If this is a Claude cloud session: set the environment's Network "
          "access to 'Full' (or 'Custom' with www.google.com and discord.com). "
          "If the network is already Full and the plain fetcher still returns "
          "0 itineraries, Google is refusing this datacenter IP and the scan "
          "needs to run elsewhere -- see the GitHub Actions workflow."
    )


# --------------------------------------------------------------------- models

@dataclass
class Leg:
    from_airport: str
    to_airport: str
    depart: str          # ISO 8601 local, "2026-12-02T06:41"
    arrive: str
    duration_min: int
    plane: str


@dataclass
class Quote:
    kind: str                    # "oneway" | "roundtrip"
    origin: str
    destination: str
    depart_date: str             # ISO date of the first departure
    return_date: str | None      # roundtrip only
    price_usd: int               # fare as quoted by Google, excludes bags
    airlines: list[str]
    stops: int                   # connections on the priced (outbound) journey
    total_duration_min: int
    layovers_min: list[int]
    overnight_layover: bool
    red_eye: bool
    legs: list[Leg] = field(default_factory=list)
    url: str = ""
    scanned_at: str = ""

    def to_row(self) -> dict:
        d = asdict(self)
        d["airlines"] = "|".join(self.airlines)
        d["layovers_min"] = "|".join(str(x) for x in self.layovers_min)
        d["legs"] = len(self.legs)
        return d


class Blocked(RuntimeError):
    """Google is refusing us -- transient, worth retrying."""


class NoResults(Exception):
    """This route/date genuinely has no itinerary. Permanent; never retry.

    Distinguishing this from Blocked matters more than it looks. Guatemala has
    no service at all from some airports (RSW, PBI), so those queries always
    fail. Treating that as transient and retrying 3x tripled our request volume
    for zero information and was itself what triggered real rate-limiting --
    a 39% failure rate that vanished once these were separated.
    """


# ------------------------------------------------------------------- internals

def _dt(sd) -> datetime | None:
    """fast-flights SimpleDatetime -> datetime, or None if unusable.

    Two sharp edges here, both of which cost us real data:

    1. `time` is a VARIABLE-LENGTH tuple. A flight landing at 23:00 comes back
       as `[23]`, not `[23, 0]`.
    2. Components can be None. Google omits the arrival date on legs where it
       considers it implied, so `date` arrives as `(None, None, None)`.

    An earlier version assumed both were always ints. The resulting TypeError
    killed ~40% of all itineraries, and because the caller caught bare
    Exception it looked exactly like rate-limiting. Returning None and
    degrading gracefully is the whole fix.
    """
    if sd is None:
        return None
    try:
        d = list(getattr(sd, "date", None) or [])
        t = list(getattr(sd, "time", None) or [])
    except TypeError:
        return None
    d += [None] * 3
    t += [0, 0]
    y, mo, dy = d[0], d[1], d[2]
    if y is None or mo is None or dy is None:
        return None
    try:
        return datetime(int(y), int(mo), int(dy), int(t[0] or 0), int(t[1] or 0))
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="minutes") if dt else ""


def _build_legs(flight) -> list[Leg]:
    legs = []
    for lg in flight.flights or []:
        legs.append(
            Leg(
                from_airport=getattr(lg.from_airport, "code", "") or "",
                to_airport=getattr(lg.to_airport, "code", "") or "",
                depart=_iso(_dt(lg.departure)),
                arrive=_iso(_dt(lg.arrival)),
                duration_min=int(lg.duration or 0),
                plane=lg.plane_type or "",
            )
        )
    return legs


def _leg_times(fl) -> list[tuple[datetime | None, datetime | None]]:
    """(departure, arrival) per leg, reconstructing arrival from duration."""
    out = []
    for lg in fl:
        dep = _dt(lg.departure)
        arr = _dt(lg.arrival)
        if arr is None and dep is not None and lg.duration:
            arr = dep + timedelta(minutes=int(lg.duration))
        out.append((dep, arr))
    return out


def _itinerary_stats(flight) -> tuple[int, int, list[int], bool, bool]:
    """-> (stops, total_duration_min, layovers, overnight_layover, red_eye)

    Every field degrades independently: a missing timestamp on leg 2 costs us
    that one layover figure, not the entire itinerary.
    """
    fl = flight.flights or []
    if not fl:
        return 0, 0, [], False, False

    times = _leg_times(fl)
    stops = len(fl) - 1

    layovers: list[int] = []
    overnight = False
    for (_, prev_arr), (next_dep, _) in zip(times, times[1:]):
        if prev_arr is None or next_dep is None:
            continue
        gap = int((next_dep - prev_arr).total_seconds() // 60)
        if gap < 0:
            continue
        layovers.append(gap)
        # "overnight" = 8h+, or straddling the 01:00-05:00 dead zone
        if gap >= 8 * 60:
            overnight = True
            continue
        probe = prev_arr
        while probe < next_dep:
            if 1 <= probe.hour < 5:
                overnight = True
                break
            probe += timedelta(minutes=30)

    first_dep = times[0][0]
    last_arr = next((a for _, a in reversed(times) if a is not None), None)
    if first_dep is not None and last_arr is not None:
        total = int((last_arr - first_dep).total_seconds() // 60)
    else:
        # fall back to summing what we do know
        total = sum(int(lg.duration or 0) for lg in fl) + sum(layovers)

    red_eye = bool(first_dep and (first_dep.hour < 6 or first_dep.hour >= 23))
    return stops, max(total, 0), layovers, overnight, red_eye


def _classify(e: Exception) -> str:
    """-> "empty" (permanent, don't retry) | "blocked" (transient, retry)."""
    from fast_flights import FlightsNotFound

    if isinstance(e, (IndexError, FlightsNotFound)):
        return "empty"
    msg = str(e).lower()
    if any(s in msg for s in ("captcha", "403", "429", "unusual traffic",
                              "too many requests")):
        return "blocked"
    if isinstance(e, (KeyError, AttributeError, ValueError, TypeError)):
        # a parse failure against an unexpected page shape -- retrying the same
        # request will reproduce it exactly, so don't
        return "empty"
    return "blocked"


def _run_with_retry(query, label: str):
    delay = config.RETRY_BACKOFF_SEC
    last: Exception | None = None
    for attempt in range(config.REQUEST_RETRIES):
        try:
            _limiter.acquire()
            return get_flights(query, integration=_fetcher)
        except Exception as e:  # noqa: BLE001 - upstream raises bare Exceptions
            last = e
            if _classify(e) == "empty":
                raise NoResults(label) from e
            if attempt == config.REQUEST_RETRIES - 1:
                break
            # jitter so parallel workers don't retry in lockstep
            time.sleep(delay + random.uniform(0, 0.75))
            delay *= 2
    raise Blocked(f"{label}: {type(last).__name__}: {last}") from last


# ---------------------------------------------------------------- public search

def search_oneway(origin: str, destination: str, day: _date) -> list[Quote]:
    q = create_query(
        flights=[FlightQuery(date=day.isoformat(), from_airport=origin,
                             to_airport=destination)],
        seat="economy",
        trip="one-way",
        passengers=Passengers(adults=1),
        currency="USD",
        language="en-US",
    )
    res = _run_with_retry(q, f"{origin}->{destination} {day}")
    now = datetime.now().isoformat(timespec="seconds")
    url = q.url()

    out: list[Quote] = []
    for f in res:
        if not f.price:
            continue
        stops, dur, lay, overnight, red = _itinerary_stats(f)
        out.append(
            Quote(
                kind="oneway", origin=origin, destination=destination,
                depart_date=day.isoformat(), return_date=None,
                price_usd=int(f.price), airlines=list(f.airlines or []),
                stops=stops, total_duration_min=dur, layovers_min=lay,
                overnight_layover=overnight, red_eye=red,
                legs=_build_legs(f), url=url, scanned_at=now,
            )
        )
    return out


def search_roundtrip(origin: str, destination: str,
                     out_day: _date, back_day: _date) -> list[Quote]:
    q = create_query(
        flights=[
            FlightQuery(date=out_day.isoformat(), from_airport=origin,
                        to_airport=destination),
            FlightQuery(date=back_day.isoformat(), from_airport=destination,
                        to_airport=origin),
        ],
        seat="economy",
        trip="round-trip",
        passengers=Passengers(adults=1),
        currency="USD",
        language="en-US",
    )
    res = _run_with_retry(q, f"{origin}<->{destination} {out_day}/{back_day}")
    now = datetime.now().isoformat(timespec="seconds")
    url = q.url()

    out: list[Quote] = []
    for f in res:
        if not f.price:
            continue
        stops, dur, lay, overnight, red = _itinerary_stats(f)
        out.append(
            Quote(
                kind="roundtrip", origin=origin, destination=destination,
                depart_date=out_day.isoformat(), return_date=back_day.isoformat(),
                price_usd=int(f.price), airlines=list(f.airlines or []),
                stops=stops, total_duration_min=dur, layovers_min=lay,
                overnight_layover=overnight, red_eye=red,
                legs=_build_legs(f), url=url, scanned_at=now,
            )
        )
    return out


# ------------------------------------------------------------------ concurrency

@dataclass
class ScanStats:
    ok: int = 0
    empty: int = 0       # route genuinely has no service on that date
    blocked: int = 0     # Google refused us
    errors: int = 0      # a bug on OUR side
    empty_routes: list[str] = field(default_factory=list)
    error_samples: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.ok + self.empty + self.blocked + self.errors

    @property
    def block_rate(self) -> float:
        return self.blocked / max(self.total, 1)

    @property
    def error_rate(self) -> float:
        return self.errors / max(self.total, 1)

    def __str__(self) -> str:
        s = f"ok={self.ok} empty={self.empty} blocked={self.blocked}"
        if self.errors:
            s += f" ERRORS={self.errors}"
        return s


def parallel(tasks: Sequence[tuple], fn: Callable,
             on_progress: Callable[[int, int, ScanStats], None] | None = None
             ) -> tuple[list[Quote], ScanStats]:
    """Run `fn(*task)` across a thread pool.

    `empty` and `blocked` are counted separately and it matters: a high empty
    count is normal (many US airports simply have no Guatemala itinerary), a
    high blocked count means back off.
    """
    quotes: list[Quote] = []
    stats = ScanStats()
    done = 0
    total = len(tasks)
    aborted = False

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
        futures = {ex.submit(fn, *t): t for t in tasks}
        for fut in as_completed(futures):
            done += 1
            task = futures[fut]
            if not aborted and deadline_exceeded():
                # Stop queueing more work and return what we have. Partial
                # results clearly labelled beat an unbounded run every time.
                aborted = True
                for pending in futures:
                    pending.cancel()
                print(f"\n  !! whole-scan deadline ({config.SCAN_DEADLINE_MIN} "
                      f"min) hit at {elapsed_min():.0f} min, {done}/{total} of "
                      f"this phase; returning partial results")
            try:
                got = fut.result()
                quotes.extend(got)
                if got:
                    stats.ok += 1
                else:
                    stats.empty += 1
                    stats.empty_routes.append(_route_of(task))
            except NoResults:
                stats.empty += 1
                stats.empty_routes.append(_route_of(task))
            except Blocked:
                stats.blocked += 1
            except Exception as e:  # noqa: BLE001
                # Deliberately NOT counted as "blocked". Lumping our own crashes
                # in with Google refusals is precisely how a 40% data loss got
                # misread as rate-limiting for three rounds of debugging.
                stats.errors += 1
                if len(stats.error_samples) < 5:
                    stats.error_samples.append(
                        f"{_route_of(task)}: {type(e).__name__}: {e}"[:200])
            if on_progress and (done % 100 == 0 or done == total):
                on_progress(done, total, stats)

    return quotes, stats


def _route_of(task: tuple) -> str:
    """Must match deadroutes.route_key -- keys are per-mode, not per-pair."""
    try:
        mode = "roundtrip" if len(task) >= 4 else "oneway"
        return f"{task[0]}->{task[1]}:{mode}"
    except (IndexError, TypeError):
        return "?"
