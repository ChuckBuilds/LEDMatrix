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

Once a range has been rejected, later ranges skip straight to chunks for
``RANGE_RETRY_SECONDS`` instead of spending a doomed request first -- live
scoreboards ask every 30 seconds. After that the range is tried again, so the
workaround retires itself if ESPN reverts.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

# Above this, ESPN returns a truncated list instead of an error. See module
# docstring: 500 is the largest value measured to return complete data.
ESPN_MAX_LIMIT = 500

# How long a rejected range keeps later ranges from being tried as ranges.
RANGE_RETRY_SECONDS = 6 * 60 * 60

# How many chunk requests may be in flight at once. A cold college-baseball
# season is ~130 chunks once the busy months are re-asked day by day; asking
# for them one at a time took long enough that scoreboard plugins blew the
# 20s update() timeout on first run. Kept under requests' default
# pool_maxsize of 10 so the shared Session never has to discard connections.
ESPN_CHUNK_WORKERS = 6

_range_lock = threading.Lock()
_ranges_rejected_until = 0.0

__all__ = [
    "ESPN_MAX_LIMIT",
    "ESPN_CHUNK_WORKERS",
    "RANGE_RETRY_SECONDS",
    "clamp_espn_limit",
    "parse_espn_date_range",
    "espn_date_chunks",
    "merge_scoreboard_payloads",
    "fetch_espn_date_chunks",
    "fetch_espn_scoreboard",
]


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


def merge_scoreboard_payloads(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
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
) -> Optional[Dict[str, Any]]:
    """GET a single ``dates=`` chunk, or None when it failed.

    One bad chunk must not sink the rest of the season, so every error is
    logged and swallowed here rather than raised to the gather below.
    """
    try:
        response = session.get(
            url,
            params=dict(params, dates=chunk, limit=ESPN_MAX_LIMIT),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 - see docstring
        if logger:
            logger.warning("ESPN chunk %s failed, skipping it: %s", chunk, exc)
        return None


def _fetch_chunks(
    session, url: str, params: Dict[str, Any], headers, timeout, logger,
    chunks: List[str],
) -> List[Optional[Dict[str, Any]]]:
    """Fetch every chunk, returning payloads positionally aligned with ``chunks``.

    Requests go out ``ESPN_CHUNK_WORKERS`` at a time because a cold season is
    over a hundred of them. The order they come back in is not significant --
    callers keep ``chunks`` order from the returned list -- but it does mean
    the session is shared across threads, which is why this only ever issues
    GETs and never touches session state.
    """
    if not chunks:
        return []
    fetch = partial(
        _fetch_one_chunk, session, url, params, headers, timeout, logger,
    )
    if len(chunks) == 1:
        return [fetch(chunks[0])]
    workers = min(ESPN_CHUNK_WORKERS, len(chunks))
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="espn-chunk",
    ) as pool:
        return list(pool.map(fetch, chunks))


def fetch_espn_date_chunks(
    session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 15,
    logger=None,
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

    chunks = espn_date_chunks(*span)
    if logger:
        logger.debug(
            "Fetching ESPN date range %s as %d month/day chunks",
            params.get("dates"), len(chunks),
        )

    results = _fetch_chunks(
        session, url, params, headers, timeout, logger, chunks,
    )
    attempted = len(chunks)

    # A month that came back at the cap is truncated; its days replace it in
    # place, so merged events stay in chunk order however the requests raced.
    slots: List[Any] = results
    capped: Dict[int, List[str]] = {}
    for index, chunk in enumerate(chunks):
        payload = slots[index]
        if payload is None or len(chunk) != 6:
            continue
        events = payload.get("events") if isinstance(payload, dict) else None
        if len(events or []) >= ESPN_MAX_LIMIT:
            if logger:
                logger.info(
                    "ESPN month %s hit the %d-event cap; re-asking it day by day",
                    chunk, ESPN_MAX_LIMIT,
                )
            capped[index] = _days_of_month(chunk)
            # Drop the truncated month now rather than after its days arrive:
            # a capped college-baseball month is ~2MB of parsed JSON, and
            # holding four of them through ~120 day requests added ~25MB to
            # the peak -- more than the concurrency itself. Low-memory boards
            # (docs/LOW_MEMORY_BOARDS.md) have under 200MB of headroom.
            slots[index] = None
    payload = events = None

    if capped:
        days = [day for index in sorted(capped) for day in capped[index]]
        attempted += len(days)
        by_day = dict(zip(days, _fetch_chunks(
            session, url, params, headers, timeout, logger, days,
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
) -> Dict[str, Any]:
    """GET an ESPN scoreboard, re-asking in month/day chunks if a range 400s.

    Anything that is not a ``YYYYMMDD-YYYYMMDD`` range is one request with the
    caller's own parameters (``limit`` clamped), so single-day and season-year
    callers see no change. A range that ESPN rejects is re-fetched in chunks,
    and later ranges go straight to chunks for ``RANGE_RETRY_SECONDS``. A 400 on
    a non-range request, any other error, and a range whose every chunk fails
    all raise as before.
    """
    params = clamp_espn_limit(params)
    is_range = parse_espn_date_range(params.get("dates")) is not None

    chunks_tried = False
    if is_range and _ranges_known_rejected():
        data = fetch_espn_date_chunks(
            session, url, params=params, headers=headers,
            timeout=timeout, logger=logger,
        )
        if data is not None:
            return data
        # Every chunk failed: ask for the range itself so the caller gets a
        # real error to log, without spending the chunks a second time.
        chunks_tried = True

    response = session.get(url, params=params, headers=headers, timeout=timeout)
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
            timeout=timeout, logger=logger,
        )
        if data is not None:
            return data
    response.raise_for_status()
    return response.json()
