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
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Above this, ESPN returns a truncated list instead of an error. See module
# docstring: 500 is the largest value measured to return complete data.
ESPN_MAX_LIMIT = 500

__all__ = [
    "ESPN_MAX_LIMIT",
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
    """
    params = dict(params or {})
    span = parse_espn_date_range(params.get("dates"))
    if span is None:
        return None

    chunks = espn_date_chunks(*span)
    if logger:
        logger.warning(
            "ESPN rejected the date range %s; re-fetching as %d month/day chunks",
            params.get("dates"), len(chunks),
        )

    payloads: List[Dict[str, Any]] = []
    attempted = 0
    pending = list(chunks)
    while pending:
        chunk = pending.pop(0)
        attempted += 1
        try:
            response = session.get(
                url,
                params=dict(params, dates=chunk, limit=ESPN_MAX_LIMIT),
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - one bad chunk must not sink the rest
            if logger:
                logger.warning("ESPN chunk %s failed, skipping it: %s", chunk, exc)
            continue
        events = payload.get("events") if isinstance(payload, dict) else None
        if len(chunk) == 6 and len(events or []) >= ESPN_MAX_LIMIT:
            if logger:
                logger.info(
                    "ESPN month %s hit the %d-event cap; re-asking it day by day",
                    chunk, ESPN_MAX_LIMIT,
                )
            pending[:0] = _days_of_month(chunk)
            continue
        payloads.append(payload)

    if not payloads:
        return None

    merged = merge_scoreboard_payloads(payloads)
    if logger:
        logger.info(
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

    The happy path is one request with the caller's own parameters (``limit``
    clamped), so single-day and season-year callers see no change. Only a 400
    against a ``YYYYMMDD-YYYYMMDD`` range triggers the fallback; any other
    error, a 400 on a non-range request, and a range whose every chunk fails
    all raise the original error as before.
    """
    params = clamp_espn_limit(params)
    response = session.get(url, params=params, headers=headers, timeout=timeout)
    if response.status_code == 400:
        data = fetch_espn_date_chunks(
            session, url, params=params, headers=headers,
            timeout=timeout, logger=logger,
        )
        if data is not None:
            return data
    response.raise_for_status()
    return response.json()
