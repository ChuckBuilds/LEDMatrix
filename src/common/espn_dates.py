"""Working around two ESPN site-API behaviours that silently break scoreboards.

Both were found on 2026-09-15, when every scoreboard on the Pi started logging
``400 Client Error: Bad Request`` against URLs that had worked the day before.

1. **Date ranges are rejected.** ``?dates=YYYYMMDD-YYYYMMDD`` answers
   ``400 {"code":400,"message":"Failed to get events endpoint."}`` for *every*
   sport -- football, baseball, hockey, basketball, soccer alike. Single days
   (``?dates=YYYYMMDD``), whole months (``?dates=YYYYMM``) and season years
   (``?dates=YYYY``) still answer 200.

2. **``limit`` above 500 corrupts the response.** ``limit=1000`` -- what every
   caller in this codebase used to send -- makes college-football return 25
   events where the truthful answer is 68 for a single Saturday and 323 for a
   month. No error, just a short list. The cutoff sits between 500 and 600.
   NFL-sized days never noticed, which is why this hid for so long.

The fix for (1) is to re-ask in units ESPN still honours. Whole calendar months
covered by the range become one ``YYYYMM`` request each and the leftover days at
either end become one ``YYYYMMDD`` request each, so the chunks cover the
requested window *exactly* -- no client-side date filtering, and therefore no
guessing at which timezone ESPN means by "a game day". A full NFL season
(20260801-20270301) costs 8 requests rather than 213 per-day ones.

A month can hold more than 500 events (college baseball's March does), and
ESPN answers that with exactly ``limit`` events and no hint that more exist. A
month chunk that comes back full is therefore re-asked day by day.

A window's *partial* edge months are asked for whole, too, once the window
covers ``ESPN_MONTH_COVER_MIN_DAYS`` or more of their days, and the answer is
trimmed back to the window's days. A scoreboard's default fortnight either side
of today (29 days, two partial months) was 29 day requests per league; it is
now 2. Trimming needs ESPN's "game day", which is the event's start in US
Eastern time -- checked against the live API on 2026-10-03: 417 of 417 soccer
events across five leagues and three months (one of them spanning the end of
daylight saving) came back from exactly the day query their Eastern date
names. A short window (a live poll's one or two days) stays day by day, so it
never downloads a whole month to read a day of it.

Chunk requests share one process-wide budget of ``ESPN_CHUNK_WORKERS`` in
flight, however many windows are being fetched at once. Each window used to get
its own six, so a scoreboard starting eight leagues -- each with a recent and
an upcoming manager -- had ~40 requests in flight, every one beyond a session's
pool a new connection and a new DNS lookup. On a Pi whose resolver could not
keep up, that was ~90 ``NameResolutionError`` lines within a minute of every
start.

Once a range has been rejected, later ranges skip straight to chunks for
``RANGE_RETRY_SECONDS`` instead of spending a doomed request first -- live
scoreboards ask every 30 seconds. After that the range is tried again, so the
workaround retires itself if ESPN reverts. A process starts inside that
period, as if a range had just been rejected.

ONE CACHE KEY PER SCOREBOARD
----------------------------
The same ESPN scoreboard used to be cached under a different key by every
consumer: odds-ticker as ``scoreboard_data_{sport}_{league}_{date}``,
``APIHelper`` as ``espn_{sport}_{league}_{date}``, the scoreboards as
``{sport_key}_schedule_{window}`` -- so two plugins showing the same league
fetched and stored it twice. :func:`espn_scoreboard_cache_key` is the one
name for "this sport/league scoreboard for these dates", and
:func:`get_espn_scoreboard` (or :func:`read_espn_scoreboard_cache` and
:func:`store_espn_scoreboard_cache` around :func:`fetch_espn_scoreboard`)
is the cache-through read every consumer can share. A read never returns an
entry older than the reader's own ``max_age``, whoever wrote it and whatever
ttl they stored with it. Old keys are passed as ``legacy_keys`` and read
after the canonical one, so an upgrade does not refetch everything at once;
they can go one release after the one that added this.

Chunks whose days are long over are kept in memory between fetches. The
scoreboards re-fetch their whole Recent/Upcoming window (14 days back, 7
ahead) every hour, and since ranges went away that is 22 day requests per
league. Measured on hdpi on 2026-10-02 (NFL, college football, MLB, college
baseball, NHL): the hourly window refresh was ~270 of 321 ESPN requests and
~21 of 24.6MB in the hour, and the 12 days that ended three or more days ago
were 68% of those bytes (6.9 of 10.2MB per copy of the five windows). A
settled chunk is answered from memory for ``SETTLED_CHUNK_TTL_SECONDS``,
stored as zlib-compressed JSON (~13x smaller than the body, and far smaller
than the parsed objects), so the hourly refresh only goes to ESPN for the
days that can still change.
"""

import contextvars
import json
import logging
import math
import re
import threading
import time
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone, tzinfo
from functools import partial
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, cast

try:
    import orjson
except ImportError:  # optional; the stdlib parser gives the same objects
    orjson = None

try:
    from src.common.json_body import response_json
except ImportError:
    # Plugins bundle copies of this module for older cores, which predate
    # json_body; the stdlib parse is what those cores always used.
    def response_json(response: Any) -> Any:
        return response.json()

try:
    # The core fetch service: counts, per-host budget, merging of identical
    # requests. Same call, same result and errors as ``session.get``.
    from src.common.fetch_service import fetch_get, get_fetch_service, pinned_caller
    _COUNTS_FETCHES = True
except ImportError:
    # Bundled copies on cores without it call the session directly.
    import contextlib

    def fetch_get(session: Any, url: str, *, share_in_flight: bool = True,
                  cache_max_age: Optional[float] = None, **kwargs: Any) -> Any:
        return session.get(url, **kwargs)

    def pinned_caller() -> Any:
        return contextlib.nullcontext()

    _COUNTS_FETCHES = False

_logger = logging.getLogger(__name__)

# Above this, ESPN returns a truncated list instead of an error. See module
# docstring: 500 is the largest value measured to return complete data.
ESPN_MAX_LIMIT = 500

# How long a rejected range keeps later ranges from being tried as ranges.
RANGE_RETRY_SECONDS = 6 * 60 * 60

# How many chunk requests may be in flight at once. Four busy months of
# college baseball are ~130 chunks once each is re-asked day by day: 17.7s one
# at a time on a Pi 4, 2.6-3.3s six at a time. Kept under requests' default
# pool_maxsize of 10 so the shared Session never has to discard connections.
ESPN_CHUNK_WORKERS = 6

#: An edge month the window covers at least this many days of is asked for
#: whole and trimmed, instead of one request per day (see module docstring).
#: Below it the days are cheaper than the month: a whole month is two to
#: three times the bytes of the half of it a fortnight window holds.
ESPN_MONTH_COVER_MIN_DAYS = 7

# Every chunk request in the process holds one of these while it is in flight
# -- the cap is per process, not per window (see module docstring).
_chunk_slots = threading.BoundedSemaphore(ESPN_CHUNK_WORKERS)


def _eastern_zone() -> Optional[tzinfo]:
    """US Eastern, the zone ESPN's ``dates=YYYYMMDD`` means, or None when
    this Python has no time zone data (no edge month is trimmed then)."""
    zone: Optional[tzinfo] = None
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 - no zoneinfo module or no tz database
        zone = None
    if zone is not None:
        return zone
    try:
        import pytz
        return cast(tzinfo, pytz.timezone("America/New_York"))
    except Exception:  # noqa: BLE001
        return None


_EASTERN = _eastern_zone()

# What _fetch_one_chunk returns for a month that came back at the cap.
_CAPPED: Any = object()

_range_lock = threading.Lock()
# A process starts out assuming ranges are still rejected, as they have been
# since 2026-09-15, and tries one again RANGE_RETRY_SECONDS in. Starting
# from "unknown" cost one doomed range request per window at every start --
# eleven 400s at once from a soccer board, each fetching before any had
# answered -- to learn what every start learns.
_ranges_rejected_until = time.monotonic() + RANGE_RETRY_SECONDS

# A chunk is "settled" once its last day is this many UTC days back. ESPN
# files games under the US Eastern date, and a late West-coast game ends after
# midnight UTC; three days leaves a full day of margin past both, so nothing
# still being played, finalised or rescheduled is ever served from memory.
SETTLED_AFTER_DAYS = 3

# How long a settled chunk is trusted. A day's finals do not change, but a
# rare correction (or an empty answer during an ESPN outage) should not live
# forever: once a day is plenty, and still skips 23 of every 24 hourly asks.
SETTLED_CHUNK_TTL_SECONDS = 24 * 60 * 60

# Bounds on the settled-chunk memory. A settled day measured 90KB (NHL) to
# 990KB (a college-football Saturday) of JSON and 9-74KB compressed; the five
# windows on hdpi need 60 entries and ~0.55MB. The caps only matter for a
# board fetching whole past seasons.
SETTLED_CACHE_MAX_ENTRIES = 512
SETTLED_CACHE_MAX_BYTES = 8 * 1024 * 1024

_settled_lock = threading.Lock()
# key -> (stored_at monotonic, compressed JSON)
_settled_chunks: "OrderedDict[Any, Tuple[float, bytes]]" = OrderedDict()
_settled_bytes = 0

__all__ = [
    "ESPN_MAX_LIMIT",
    "ESPN_CHUNK_WORKERS",
    "ESPN_MONTH_COVER_MIN_DAYS",
    "RANGE_RETRY_SECONDS",
    "SETTLED_AFTER_DAYS",
    "SETTLED_CHUNK_TTL_SECONDS",
    "clamp_espn_limit",
    "clear_settled_chunk_cache",
    "parse_espn_date_range",
    "espn_date_chunks",
    "espn_request_chunks",
    "merge_scoreboard_payloads",
    "fetch_espn_date_chunks",
    "fetch_espn_scoreboard",
    "ESPN_SCOREBOARD_URL",
    "espn_scoreboard_url",
    "espn_scoreboard_cache_key",
    "espn_scoreboard_cache_key_for_url",
    "read_espn_scoreboard_cache",
    "store_espn_scoreboard_cache",
    "get_espn_scoreboard",
]

#: The site-API scoreboard every sport and league shares.
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
_ESPN_HOST_URL = "https://site.api.espn.com/"

_PATH_PART = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
_DATES = re.compile(r"^\d{4}(?:\d{2}(?:\d{2})?)?$|^\d{8}-\d{8}$")
_SCOREBOARD_PATH = re.compile(r"/sports/([^/?#]+)/([^/?#]+)/scoreboard/?$")


def clamp_espn_limit(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a copy of ``params`` with any ``limit`` over 500 pulled back to 500."""
    out = dict(params or {})
    raw = out.get("limit")
    if raw is None:
        return out
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return out
    if value > ESPN_MAX_LIMIT:
        out["limit"] = ESPN_MAX_LIMIT
    return out


def parse_espn_date_range(dates: Any) -> Optional[Tuple[date, date]]:
    """Parse ``"YYYYMMDD-YYYYMMDD"`` into dates, or return None.

    None means "not a day range" -- a single day, a month, a season year, or
    anything unparseable. Those forms still work upstream and must be passed
    through untouched rather than rewritten.
    """
    if not isinstance(dates, str):
        return None
    halves = dates.split("-")
    if len(halves) != 2 or len(halves[0]) != 8 or len(halves[1]) != 8:
        return None
    try:
        start = date(int(halves[0][:4]), int(halves[0][4:6]), int(halves[0][6:]))
        end = date(int(halves[1][:4]), int(halves[1][4:6]), int(halves[1][6:]))
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def _memo_kwargs(cache_max_age: Optional[float]) -> Dict[str, Any]:
    """``cache_max_age`` for fetch_get, only when the caller gave one, so a
    call that did not say is the call it always was."""
    return {} if cache_max_age is None else {"cache_max_age": cache_max_age}


def _ranges_known_rejected() -> bool:
    with _range_lock:
        return time.monotonic() < _ranges_rejected_until


def _note_range_rejected() -> None:
    global _ranges_rejected_until
    with _range_lock:
        _ranges_rejected_until = time.monotonic() + RANGE_RETRY_SECONDS


def _first_of_next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _days_of_month(chunk: str) -> List[str]:
    day = date(int(chunk[:4]), int(chunk[4:6]), 1)
    stop = _first_of_next_month(day)
    days = []
    while day < stop:
        days.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return days


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _chunk_last_day(chunk: str) -> Optional[date]:
    try:
        if len(chunk) == 8:
            return date(int(chunk[:4]), int(chunk[4:6]), int(chunk[6:]))
        if len(chunk) == 6:
            first = date(int(chunk[:4]), int(chunk[4:6]), 1)
            return _first_of_next_month(first) - timedelta(days=1)
    except ValueError:
        pass
    return None


def _settled_key(url: str, params: Dict[str, Any], chunk: str) -> Optional[Any]:
    """Memory key for a chunk that can no longer change, else None."""
    last_day = _chunk_last_day(chunk)
    if last_day is None:
        return None
    if last_day > _utc_today() - timedelta(days=SETTLED_AFTER_DAYS):
        return None
    # dates is the chunk itself and limit is always ESPN_MAX_LIMIT here;
    # anything else (groups=80 for FBS, a team filter) changes the answer.
    rest = tuple(sorted(
        (str(k), str(v)) for k, v in params.items() if k not in ("dates", "limit")
    ))
    return (url, rest, chunk)


def _settled_get(key: Any) -> Optional[Dict[str, Any]]:
    with _settled_lock:
        entry = _settled_chunks.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] > SETTLED_CHUNK_TTL_SECONDS:
            _settled_drop(key)
            return None
        _settled_chunks.move_to_end(key)
        blob = entry[1]
    # Decompress and parse outside the lock: every hit gets its own objects,
    # so a caller mutating its payload cannot reach another caller's.
    body = zlib.decompress(blob)
    return cast(Dict[str, Any], orjson.loads(body) if orjson else json.loads(body))


def _settled_drop(key: Any) -> None:
    """Remove one entry. Caller holds _settled_lock."""
    global _settled_bytes
    entry = _settled_chunks.pop(key, None)
    if entry is not None:
        _settled_bytes -= len(entry[1])


def _settled_put(key: Any, response: Any, payload: Dict[str, Any]) -> None:
    global _settled_bytes
    body = getattr(response, "content", None)
    if not isinstance(body, (bytes, bytearray)):
        body = json.dumps(payload).encode("utf-8")
    blob = zlib.compress(bytes(body), 6)
    if len(blob) > SETTLED_CACHE_MAX_BYTES:
        return
    with _settled_lock:
        _settled_drop(key)
        _settled_chunks[key] = (time.monotonic(), blob)
        _settled_bytes += len(blob)
        while _settled_chunks and (
            len(_settled_chunks) > SETTLED_CACHE_MAX_ENTRIES
            or _settled_bytes > SETTLED_CACHE_MAX_BYTES
        ):
            _settled_drop(next(iter(_settled_chunks)))


def clear_settled_chunk_cache() -> None:
    """Forget every remembered settled chunk (tests, or a manual refresh)."""
    global _settled_bytes
    with _settled_lock:
        _settled_chunks.clear()
        _settled_bytes = 0


def espn_date_chunks(start: date, end: date) -> List[str]:
    """Cover ``[start, end]`` inclusive with ``dates=`` values ESPN accepts.

    Whole calendar months inside the window collapse to one ``YYYYMM`` chunk;
    partial months at the edges are spelled out day by day. The chunks tile the
    window exactly -- they never reach outside it -- so merging their events
    needs no date filtering afterwards.
    """
    chunks: List[str] = []
    cursor = start
    while cursor <= end:
        month_end = _first_of_next_month(cursor) - timedelta(days=1)
        if cursor.day == 1 and month_end <= end:
            chunks.append(cursor.strftime("%Y%m"))
            cursor = month_end + timedelta(days=1)
        else:
            chunks.append(cursor.strftime("%Y%m%d"))
            cursor += timedelta(days=1)
    return chunks


def espn_request_chunks(
    start: date,
    end: date,
    month_cover_min_days: Optional[int] = None,
) -> List[Tuple[str, Optional[Tuple[date, date]]]]:
    """The requests that fetch ``[start, end]``, as ``(dates, trim)`` pairs.

    :func:`espn_date_chunks`, except that a partial edge month with
    ``month_cover_min_days`` (default ``ESPN_MONTH_COVER_MIN_DAYS``) or more
    of its days in the window becomes one ``YYYYMM`` request whose ``trim``
    is the first and last of those days: its events that start outside them
    (US Eastern) are dropped. ``trim`` is None for every other request.
    Without time zone data nothing can be trimmed, so the edge days stay day
    requests.
    """
    if month_cover_min_days is None:
        month_cover_min_days = ESPN_MONTH_COVER_MIN_DAYS
    planned: List[Tuple[str, Optional[Tuple[date, date]]]] = []
    run: List[str] = []

    def flush() -> None:
        if (_EASTERN is not None and month_cover_min_days > 0
                and len(run) >= month_cover_min_days):
            planned.append((run[0][:6], (_parse_day(run[0]), _parse_day(run[-1]))))
        else:
            planned.extend((day, None) for day in run)
        run.clear()

    for chunk in espn_date_chunks(start, end):
        if run and (len(chunk) != 8 or chunk[:6] != run[0][:6]):
            flush()
        if len(chunk) == 8:
            run.append(chunk)
        else:
            planned.append((chunk, None))
    flush()
    return planned


def _parse_day(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _eastern_day(stamp: Any) -> Optional[date]:
    """The US Eastern date of an ESPN event ``date`` ("2026-10-10T11:30Z"),
    or None when it cannot be read."""
    if not isinstance(stamp, str) or _EASTERN is None:
        return None
    try:
        moment = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(_EASTERN).date()


def _trim_to_days(payload: Any, first: date, last: date) -> Any:
    """Drop the events of a month payload that start outside ``[first, last]``
    (US Eastern). An event whose date cannot be read is kept: its day query
    might well have returned it, and a game is never dropped on a guess.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return payload
    kept = []
    for event in payload["events"]:
        day = _eastern_day(event.get("date")) if isinstance(event, dict) else None
        if day is None or first <= day <= last:
            kept.append(event)
    payload["events"] = kept
    return payload


def merge_scoreboard_payloads(payloads: List[Any]) -> Dict[str, Any]:
    """Fold chunk responses into one scoreboard payload.

    Events are de-duplicated by id and keep first-seen order. Non-event keys
    (``leagues``, ``season``, ``week``) come from the first payload that has
    them, matching what a single un-chunked response would have looked like.
    """
    merged: Dict[str, Any] = {}
    events: List[Dict[str, Any]] = []
    seen = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if key != "events" and key not in merged:
                merged[key] = value
        for event in payload.get("events") or []:
            event_id = event.get("id") if isinstance(event, dict) else None
            if event_id is not None:
                if event_id in seen:
                    continue
                seen.add(event_id)
            events.append(event)
    merged["events"] = events
    return merged


def _fetch_one_chunk(
    session, url: str, params: Dict[str, Any], headers, timeout, logger, chunk: str,
    cache_max_age: Optional[float] = None,
    trims: Optional[Dict[str, Tuple[date, date]]] = None,
) -> Any:
    """GET a single ``dates=`` chunk, or None when it failed.

    One bad chunk must not sink the rest of the season, so every error is
    logged and swallowed here rather than raised to the gather below.

    A chunk whose days are settled (see ``SETTLED_AFTER_DAYS``) is answered
    from memory when it was fetched in the last day. What is remembered is
    the month as ESPN sent it, so each window still trims it to its own days.

    A month that comes back at the cap is truncated: it returns ``_CAPPED``,
    its payload dropped here before it is ever held beside the others. A
    month in ``trims`` loses its events outside the days given there.

    The request holds one of the process-wide ``_chunk_slots`` while it runs.
    """
    try:
        settled = _settled_key(url, params, chunk)
        payload = _settled_get(settled) if settled is not None else None
        if payload is None:
            with _chunk_slots:
                response = fetch_get(
                    session,
                    url,
                    params=dict(params, dates=chunk, limit=ESPN_MAX_LIMIT),
                    headers=headers,
                    timeout=timeout,
                    **_memo_kwargs(cache_max_age),
                )
                response.raise_for_status()
                payload = response_json(response)
            if len(chunk) == 6 and isinstance(payload, dict):
                if len(payload.get("events") or []) >= ESPN_MAX_LIMIT:
                    return _CAPPED
            if settled is not None and isinstance(payload, dict):
                events = payload.get("events")
                # A capped month never gets here; a day at the cap is
                # truncated too, and remembering it would only cost memory.
                if isinstance(events, list) and len(events) < ESPN_MAX_LIMIT:
                    _settled_put(settled, response, payload)
    except Exception as exc:  # noqa: BLE001 - see docstring
        if logger:
            logger.warning("ESPN chunk %s failed, skipping it: %s", chunk, exc)
        return None
    if len(chunk) == 6 and isinstance(payload, dict):
        trim = (trims or {}).get(chunk)
        if trim is not None:
            payload = _trim_to_days(payload, *trim)
    return payload


def _fetch_chunks(
    session, url: str, params: Dict[str, Any], headers, timeout, logger,
    chunks: List[str], cache_max_age: Optional[float] = None,
    trims: Optional[Dict[str, Tuple[date, date]]] = None,
) -> List[Any]:
    """Fetch every chunk, returning payloads positionally aligned with ``chunks``.

    Requests go out ``ESPN_CHUNK_WORKERS`` at a time because a cold season is
    over a hundred of them -- and no more than that across every window the
    process is fetching, which ``_fetch_one_chunk``'s slot enforces. The order they come back in is not significant --
    callers keep ``chunks`` order from the returned list -- but it does mean
    the session is shared across threads, which is why this only ever issues
    GETs and never touches session state.

    Each chunk runs in a copy of the caller's context, with the caller pinned
    into it, so the fetch service counts the chunks against the plugin that
    asked for the range rather than against the core.
    """
    if not chunks:
        return []
    fetch = partial(
        _fetch_one_chunk, session, url, params, headers, timeout, logger,
        cache_max_age=cache_max_age, trims=trims,
    )
    if len(chunks) == 1:
        return [fetch(chunks[0])]
    workers = min(ESPN_CHUNK_WORKERS, len(chunks))
    with pinned_caller():
        # One copy per chunk: a Context cannot be entered by two threads.
        contexts = [contextvars.copy_context() for _ in chunks]
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="espn-chunk",
    ) as pool:
        futures = [pool.submit(context.run, fetch, chunk)
                   for context, chunk in zip(contexts, chunks)]
        return [future.result() for future in futures]


def fetch_espn_date_chunks(
    session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    logger=None,
    cache_max_age: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a ``YYYYMMDD-YYYYMMDD`` window as month and day chunks.

    Returns None when ``params["dates"]`` is not a day range, or when every
    chunk failed. Callers treat None as "re-raise the original error": caching
    an empty payload would read as "no games this season".

    Chunks are always asked with ``limit=500``. ESPN's default page is smaller
    than a busy month (100 for NFL, 300 for college football), and 500 is the
    largest value that does not corrupt the answer. A month that comes back
    with 500 events is assumed truncated and re-asked day by day. A failed
    chunk is logged and skipped so one bad day cannot cost a whole season.

    Chunks go out ``ESPN_CHUNK_WORKERS`` at a time, in two passes: the months
    and edge days first, then the days of any month that came back capped.
    Merged events keep ``espn_date_chunks`` order regardless of which request
    finished first, so the result does not depend on the race.
    """
    params = dict(params or {})
    span = parse_espn_date_range(params.get("dates"))
    if span is None:
        return None

    planned = espn_request_chunks(*span)
    chunks = [chunk for chunk, _ in planned]
    trims = {chunk: trim for chunk, trim in planned if trim is not None}
    if logger:
        logger.debug(
            "Fetching ESPN date range %s as %d month/day chunks",
            params.get("dates"), len(chunks),
        )

    results = _fetch_chunks(
        session, url, params, headers, timeout, logger, chunks, cache_max_age,
        trims,
    )
    attempted = len(chunks)

    # A month that came back at the cap is truncated; its days (only the
    # window's, for a trimmed edge month) replace it in place, so merged
    # events stay in chunk order however the requests raced. Its payload was
    # already dropped in the worker: a capped college-baseball month is ~2MB
    # of parsed JSON, and holding four of them through ~120 day requests added
    # ~25MB to the peak -- more than the concurrency itself. Low-memory boards
    # (docs/LOW_MEMORY_BOARDS.md) have under 200MB of headroom.
    slots: List[Any] = results
    capped: Dict[int, List[str]] = {}
    for index, chunk in enumerate(chunks):
        if slots[index] is not _CAPPED:
            continue
        if logger:
            logger.info(
                "ESPN month %s hit the %d-event cap; re-asking it day by day",
                chunk, ESPN_MAX_LIMIT,
            )
        trim = trims.get(chunk)
        capped[index] = (_days_of_month(chunk) if trim is None
                         else espn_date_chunks(*trim))
        slots[index] = None

    if capped:
        days = [day for index in sorted(capped) for day in capped[index]]
        attempted += len(days)
        by_day = dict(zip(days, _fetch_chunks(
            session, url, params, headers, timeout, logger, days, cache_max_age,
        )))
        for index, month_days in capped.items():
            slots[index] = [by_day.get(day) for day in month_days]

    payloads: List[Dict[str, Any]] = []
    for slot in slots:
        if slot is None:
            continue
        if isinstance(slot, list):
            payloads.extend(payload for payload in slot if payload is not None)
        else:
            payloads.append(slot)

    if not payloads:
        return None

    merged = merge_scoreboard_payloads(payloads)
    if logger:
        logger.debug(
            "Recovered %d events for %s from %d/%d chunk requests",
            len(merged["events"]), params.get("dates"), len(payloads), attempted,
        )
    return merged


def fetch_espn_scoreboard(
    session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    logger=None,
    cache_max_age: Optional[float] = None,
) -> Dict[str, Any]:
    """GET an ESPN scoreboard, re-asking in month/day chunks if a range 400s.

    Anything that is not a ``YYYYMMDD-YYYYMMDD`` range is one request with the
    caller's own parameters (``limit`` clamped), so single-day and season-year
    callers see no change. A range that ESPN rejects is re-fetched in chunks,
    and later ranges go straight to chunks for ``RANGE_RETRY_SECONDS``. A 400 on
    a non-range request, any other error, and a range whose every chunk fails
    all raise as before.

    ``cache_max_age`` is the oldest response, in seconds, the caller takes
    from the fetch service's short response cache (its own TTL; 0 always
    asks ESPN). None leaves it to the service default.
    """
    params = clamp_espn_limit(params)
    is_range = parse_espn_date_range(params.get("dates")) is not None

    chunks_tried = False
    if is_range and _ranges_known_rejected():
        data = fetch_espn_date_chunks(
            session, url, params=params, headers=headers,
            timeout=timeout, logger=logger, cache_max_age=cache_max_age,
        )
        if data is not None:
            return data
        # Every chunk failed: ask for the range itself so the caller gets a
        # real error to log, without spending the chunks a second time.
        chunks_tried = True

    response = fetch_get(session, url, params=params, headers=headers, timeout=timeout,
                         **_memo_kwargs(cache_max_age))
    if is_range and response.status_code == 400 and not chunks_tried:
        _note_range_rejected()
        if logger:
            logger.warning(
                "ESPN rejected the date range %s (400); fetching it as month/day "
                "chunks, and fetching ranges that way for the next %d hours",
                params.get("dates"), RANGE_RETRY_SECONDS // 3600,
            )
        data = fetch_espn_date_chunks(
            session, url, params=params, headers=headers,
            timeout=timeout, logger=logger, cache_max_age=cache_max_age,
        )
        if data is not None:
            return data
    response.raise_for_status()
    return cast(Dict[str, Any], response_json(response))


# --- one cache key per scoreboard --------------------------------------------------

def espn_scoreboard_url(sport: str, league: str) -> str:
    """The site-API scoreboard URL for an ESPN ``sport`` / ``league`` path."""
    return ESPN_SCOREBOARD_URL.format(sport=_path_part(sport, "sport"),
                                      league=_path_part(league, "league"))


def _path_part(value: Any, what: str) -> str:
    text = str(value or "").strip().lower()
    if not _PATH_PART.match(text):
        raise ValueError(f"not an ESPN {what} path segment: {value!r}")
    return text


def _day(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"not an ESPN day (YYYYMMDD): {value!r}")
    return text


def _dates_part(dates: Any) -> str:
    """``dates`` as ESPN spells it, or ``current`` for no ``dates`` at all."""
    if dates is None or dates == "":
        return "current"
    if isinstance(dates, (date, datetime)):
        return _day(dates)
    if isinstance(dates, (tuple, list)):
        if len(dates) != 2:
            raise ValueError(f"a date range is (start, end): {dates!r}")
        start, end = _day(dates[0]), _day(dates[1])
        return start if start == end else f"{start}-{end}"
    text = str(dates).strip()
    if isinstance(dates, bool) or not _DATES.match(text):
        raise ValueError(
            f"not an ESPN dates value (YYYY, YYYYMM, YYYYMMDD or "
            f"YYYYMMDD-YYYYMMDD): {dates!r}")
    return text


def espn_scoreboard_cache_key(sport: str, league: str, dates: Any = None) -> str:
    """The one cache key for an ESPN scoreboard, whoever caches it.

    ``sport`` and ``league`` are ESPN's own path segments -- ``football`` /
    ``college-football``, ``soccer`` / ``eng.1`` -- not a plugin's
    ``sport_key``, so every plugin showing a league names it the same way.
    ``dates`` is what the request sends as ``dates=``: ``"YYYYMMDD"``,
    ``"YYYYMM"``, ``"YYYY"``, ``"YYYYMMDD-YYYYMMDD"``, a ``date``, or a
    ``(start, end)`` pair of either; None is the undated "current"
    scoreboard. Anything else raises ValueError rather than invent a key.

    The key says nothing about ``limit``: a cached copy is meant to be a
    whole one (the helpers here always ask for ``ESPN_MAX_LIMIT``).
    """
    return (f"espn_scoreboard_{_path_part(sport, 'sport')}_"
            f"{_path_part(league, 'league')}_{_dates_part(dates)}")


def espn_scoreboard_cache_key_for_url(url: str, dates: Any = None) -> Optional[str]:
    """:func:`espn_scoreboard_cache_key` for a scoreboard URL, or None when
    ``url`` is not ``.../sports/{sport}/{league}/scoreboard``."""
    match = _SCOREBOARD_PATH.search(str(url or "").split("?", 1)[0])
    if match is None:
        return None
    try:
        return espn_scoreboard_cache_key(match.group(1), match.group(2), dates)
    except ValueError:
        return None


def _note_cache_hit(legacy: bool, avoided_request: bool = True) -> None:
    if not _COUNTS_FETCHES:
        return
    try:
        get_fetch_service().note_cache_hit(
            _ESPN_HOST_URL, legacy=legacy, avoided_request=avoided_request)
    except Exception:  # noqa: BLE001 - counting never breaks a read
        _logger.debug("could not count a scoreboard cache hit", exc_info=True)


def _fresh_cached(cache_manager: Any, key: str, max_age: Optional[float],
                  now: float) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """The data cached under ``key`` if it is at most ``max_age`` seconds
    old, and its age (None when the cache does not say).

    The age is the stored record's own timestamp, checked here: CacheManager
    lets a ttl stored by the writer override the reader's max_age, and its
    memory tier times an entry from when it was loaded, not written. A key
    shared by readers with different TTLs can rely on neither.
    """
    reader = getattr(cache_manager, "get_cached_data", None)
    limit = None if max_age is None else max(1, int(math.ceil(max_age)))
    if not callable(reader):
        # A cache without records (a test double, a plugin's own store).
        value = cache_manager.get(key, max_age=limit)
        return (value if isinstance(value, dict) else None), None
    record = reader(key, max_age=limit, memory_ttl=limit)
    if not isinstance(record, dict):
        return None, None
    if "data" not in record:
        return record, None  # unwrapped; the cache already judged it by mtime
    stamp = record.get("timestamp")
    age: Optional[float] = None
    if not isinstance(stamp, bool) and isinstance(stamp, (int, float)):
        age = max(0.0, now - float(stamp))
    if max_age is not None and (age is None or age > max_age):
        return None, None
    data = record["data"]
    return (data if isinstance(data, dict) else None), age


def read_espn_scoreboard_cache(
    cache_manager: Any,
    key: str,
    max_age: Optional[float],
    legacy_keys: Iterable[str] = (),
    now: Optional[float] = None,
    accept: Optional[Callable[[Dict[str, Any], Optional[float]], bool]] = None,
) -> Optional[Dict[str, Any]]:
    """The cached scoreboard under ``key``, or under the first of
    ``legacy_keys`` that has one, if it is at most ``max_age`` seconds old.

    None on a miss, a stale entry, ``max_age`` of 0 or less, no cache
    manager, or any cache error -- a read never raises. ``max_age=None``
    takes an entry of any age. ``accept(data, age_seconds)`` can turn down
    an entry the age alone would allow (a payload holding a live game wants
    a shorter limit); ``age_seconds`` is None when the cache cannot say. A
    hit is counted in the fetch statistics (``cache_hits``;
    ``legacy_cache_hits`` too for an old key).
    """
    if cache_manager is None:
        return None
    if max_age is not None and max_age <= 0:
        return None
    clock = time.time() if now is None else now
    for index, candidate in enumerate([key, *legacy_keys]):
        if not candidate:
            continue
        try:
            data, age = _fresh_cached(cache_manager, candidate, max_age, clock)
            if data is not None and accept is not None and not accept(data, age):
                data = None
        except Exception:  # noqa: BLE001 - a broken cache is a miss
            _logger.debug("scoreboard cache read failed for %s", candidate, exc_info=True)
            continue
        if data is not None:
            _note_cache_hit(legacy=index > 0)
            return data
    return None


def store_espn_scoreboard_cache(cache_manager: Any, key: str, data: Any) -> None:
    """Cache a fetched scoreboard under ``key``. Never raises.

    No ttl is stored: each reader applies its own ``max_age`` (a live
    reader 30 s, a schedule reader an hour), and a stored ttl would
    override theirs in CacheManager.
    """
    if cache_manager is None or data is None:
        return
    try:
        cache_manager.set(key, data)
    except Exception:  # noqa: BLE001 - the caller still has its data
        _logger.warning("Could not cache scoreboard %s", key, exc_info=True)


def get_espn_scoreboard(
    session: Any,
    sport: str,
    league: str,
    dates: Any = None,
    *,
    cache_manager: Any = None,
    max_age: Optional[float] = 300,
    legacy_keys: Iterable[str] = (),
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    logger: Any = None,
) -> Dict[str, Any]:
    """An ESPN scoreboard through the shared cache, fetched on a miss.

    Reads :func:`espn_scoreboard_cache_key` (then ``legacy_keys``) and
    returns an entry at most ``max_age`` seconds old. Otherwise it fetches
    with :func:`fetch_espn_scoreboard` -- ``limit=ESPN_MAX_LIMIT``, ranges
    split as ESPN needs -- caches the result under the canonical key and
    returns it. ``max_age=0`` always fetches (and still caches, for other
    readers). Errors raise exactly as :func:`fetch_espn_scoreboard` does,
    and nothing is cached then. ``session=None`` uses the fetch service's
    pooled session for the ESPN host.
    """
    key = espn_scoreboard_cache_key(sport, league, dates)
    cached = read_espn_scoreboard_cache(cache_manager, key, max_age, legacy_keys)
    if cached is not None:
        return cast(Dict[str, Any], cached)
    params: Dict[str, Any] = {"limit": ESPN_MAX_LIMIT}
    spelled = _dates_part(dates)
    if spelled != "current":
        params["dates"] = spelled
    data = fetch_espn_scoreboard(
        session,
        espn_scoreboard_url(sport, league),
        params=params,
        headers=headers,
        timeout=timeout,
        logger=logger,
        # The response cache must not hand back anything older than the
        # cache read above would have accepted.
        cache_max_age=None if max_age is None else max(0.0, float(max_age)),
    )
    store_espn_scoreboard_cache(cache_manager, key, data)
    return data
