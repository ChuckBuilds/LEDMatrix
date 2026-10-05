"""One place every core HTTP fetch goes through: pooling, merging, budgets, counts.

Stage 1 of the shared fetch service (docs/PLUGIN_API_REFERENCE.md, "Fetching
data"). Core's own fetch paths call :meth:`FetchService.get` instead of
``session.get``:

- ``APIHelper.get`` / ``post`` (``src/common/api_helper.py``);
- ``fetch_espn_scoreboard`` and its date chunks (``src/common/espn_dates.py``),
  which every scoreboard plugin's live, recent and upcoming fetch and
  ``SportsFetchMixin._fetch_season_directly`` already use;
- ``BackgroundDataService`` (season schedules);
- ``BaseOddsManager.get_odds``.

So the plugins that use those helpers are covered without changing a line of
plugin code. What the service adds, around the caller's own ``session.get``:

**Connection pooling.** :func:`share_connection_pool` mounts one shared
``HTTPAdapter`` per retry policy on a caller's Session. urllib3 keeps one
connection pool per host inside it, so every core Session with the same retry
policy reuses the same TCP/TLS connections to a host -- the dozens of
``BaseOddsManager`` instances (one per scoreboard league manager) stop opening
a pool each. Each caller keeps its own Session object, so headers, cookies and
auth -- the mutable parts -- are never shared. :meth:`FetchService.session_for`
is the pooled per-host Session for a caller that has none.

**Single-flight.** Identical GETs in flight at the same time go to the network
once; the others wait and receive a copy of that response (or the same
exception). Identical means the same URL and query, the same effective request
headers, the same timeout and the same retry policy, so a caller can never be
handed a result its own request could not have produced.

**Per-host token buckets.** Each host may have a budget, ``per_second`` with a
``burst``. A request past the budget waits for a token, but never longer than
``max_wait_seconds``: past that it goes anyway, counted as an overrun. A
limiter that stalls a plugin's update budget is worse than one extra request.
The default budgets ESPN only (``*.espn.com``: 20/s, burst 200), far above the
steady-state rate (well under 1/s) and above a cold season fetch; anything else
is unthrottled.

**Conditional GET.** When a 200 carries ``ETag`` or ``Last-Modified``, the
body is kept in a small bounded store and the next identical GET sends
``If-None-Match`` / ``If-Modified-Since``. A ``304`` is turned back into the
200 the caller would have got, body and all, so callers never see a 304 they
did not ask for. Only server-sent validators are used; a caller that sets its
own conditional headers gets the raw answer. (ESPN sent no validators when
this was written -- see the PR that added this module -- so on ESPN the store
stays empty and costs nothing.)

**Response cache (stage 2).** A 200 that says ``Cache-Control: max-age=N``
is kept in memory for those N seconds (less its ``Age``), and an identical
GET inside that window is answered from it without a request. ESPN sends
max-age (1-496 s measured on 2026-10-02, most of it under 10 s) and no
validators, so this is the only revalidation-free reuse ESPN allows. It
never hands a caller a response older than the caller accepts: a caller
says how old with ``cache_max_age`` (``fetch_get(..., cache_max_age=ttl)``;
0 skips the cache), and one that does not say gets at most
``response_cache.default_max_age`` (30 s). ``no-store``, ``no-cache``,
``private``, ``Vary: *`` and ``Set-Cookie`` responses are never kept.
Identical means what the validator store keys on: URL, query, effective
headers and, for a session with cookies or auth, the session.

**Counters.** Requests, merged requests, bytes (``bytes`` decoded, as the
caller reads them; ``wire_bytes`` as they crossed the network, which is
what a metered connection pays for -- ESPN gzips, so the two differ ~14x),
304s, errors, HTTP errors,
adapter retries, throttled requests and seconds waited, plus requests
answered without the network: ``memo_hits`` (the response cache) and
``cache_hits`` / ``legacy_cache_hits`` (a shared ESPN scoreboard cache entry,
counted by ``src/common/espn_dates.py``). Per plugin and per host.
:class:`FetchStatsPublisher` publishes them for the web interface
(``GET /api/v3/plugins/fetch-stats``).

CALLER IDENTITY
---------------
Counters are kept per plugin without plugins saying who they are:

1. A context variable, set by the core around the code it runs for a plugin:
   ``PluginExecutor`` around ``update()``/``display()``, ``PluginManager``
   around loading a plugin (its constructor and ``on_enable``). The core
   carries it across its own thread hops: ``BackgroundDataService`` records
   the submitter at submit time and its worker runs under it, and
   ``espn_dates`` copies it into each chunk thread.
2. When no scope is set -- a thread the plugin started itself, or a call the
   display makes outside the executor -- the first stack frame whose source
   file lives in a loaded plugin's directory names the plugin
   (:func:`register_plugin_directory`).
3. Otherwise the request is the core's own (``"core"``).

Nothing here raises on account of bookkeeping: a failure in counting,
keying, the validator store or the response cache falls back to a plain
``session.get``.

Core-internal for now (stage 1). Plugins reach it through ``APIHelper`` and
``espn_dates``; a plugin-facing API comes with stage 3.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import http.cookiejar
import json
import logging
import math
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple,
)
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.models import PreparedRequest
from requests.sessions import merge_setting
from requests.structures import CaseInsensitiveDict

logger = logging.getLogger(__name__)

__all__ = [
    "CORE",
    "DEFAULT_CONFIG",
    "FETCH_STATS_KEY",
    "FetchService",
    "FetchStatsPublisher",
    "TokenBucket",
    "configure_fetch_service",
    "current_plugin_id",
    "fetch_get",
    "fetch_post",
    "get_fetch_service",
    "pinned_caller",
    "plugin_scope",
    "read_fetch_stats",
    "register_plugin_directory",
    "share_connection_pool",
    "start_fetch_stats_publisher",
    "unregister_plugin_directory",
]

#: The caller name for requests no plugin made.
CORE = "core"

#: Read when the display starts: ``fetch_service`` in config.json, merged over
#: these. ``rate_limits`` maps a host (``api.example.com``) or a suffix pattern
#: (``*.espn.com``, which also matches ``espn.com``) to a budget; a
#: ``per_second`` of 0 or null removes the budget for that host.
DEFAULT_CONFIG: Mapping[str, Any] = {
    "enabled": True,
    "single_flight": True,
    "conditional_get": True,
    "max_wait_seconds": 2.0,
    "rate_limits": {
        "*.espn.com": {"per_second": 20, "burst": 200},
    },
    "validator_store": {
        "max_entries": 64,
        "max_bytes": 4 * 1024 * 1024,
        "max_entry_bytes": 1024 * 1024,
    },
    # Stage 2: responses ESPN calls fresh (Cache-Control: max-age), reused
    # for identical GETs. A college-football Saturday is ~1 MB decoded, so
    # one entry may be 2 MB; months (5-7 MB) are never kept.
    "response_cache": {
        "enabled": True,
        "default_max_age": 30,
        "max_entries": 64,
        "max_bytes": 6 * 1024 * 1024,
        "max_entry_bytes": 2 * 1024 * 1024,
    },
}

#: However long a server says a response stays fresh, it is not kept longer
#: than this: the cache is for requests that coincide, not for storage.
_RESPONSE_CACHE_CEILING = 600.0

#: Connection pools kept per shared adapter (one per host) and connections
#: kept per pool. Larger than requests' 10 because one adapter now serves
#: every core Session with its retry policy: three background workers each
#: running six ESPN chunk threads is 18 concurrent requests to one host.
_POOL_CONNECTIONS = 16
_POOL_MAXSIZE = 24

#: Distinct hosts tracked individually; the rest are counted under "other".
_MAX_HOSTS = 200
_MAX_PLUGINS = 200
_OTHER = "other"

#: How far up the stack the plugin-directory lookup looks.
_MAX_STACK_DEPTH = 80

_COUNTER_FIELDS = (
    "requests",      # round trips sent (adapter retries inside one are not extra)
    "merged",        # answered by an identical request already in flight
    "not_modified",  # 304s turned back into the stored 200
    "errors",        # transport exceptions (timeouts, connection errors, ...)
    "http_errors",   # responses with status >= 400
    "retries",       # retries the urllib3 adapter made inside a request
    "throttled",     # requests that waited for a host budget
    "overruns",      # requests that went after max_wait_seconds anyway
    "bytes",         # decoded response body bytes received
    "wire_bytes",    # body bytes as they came off the socket (still compressed)
    "wait_seconds",  # time spent waiting for host budgets
    "memo_hits",     # answered from the response cache (max-age); nothing sent
    "cache_hits",    # scoreboard fetches answered from a shared ESPN cache entry
    "legacy_cache_hits",  # cache reads answered from a pre-stage-2 key (any helper)
)


# --- caller identity ---------------------------------------------------------

_current_plugin: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ledmatrix_fetch_plugin", default=None)

_dirs_lock = threading.Lock()
#: (normalised directory prefix ending in a separator, plugin id), longest
#: prefix first so a nested directory wins.
_plugin_dirs: Tuple[Tuple[str, str], ...] = ()
#: co_filename -> plugin id or "" (not a plugin file). Cleared whenever the
#: registered directories change.
_file_owner: Dict[str, str] = {}


def _norm_dir(path: str) -> str:
    text = os.path.normcase(os.path.abspath(path))
    return text if text.endswith(os.sep) else text + os.sep


def register_plugin_directory(plugin_id: str, path: Any) -> None:
    """Attribute code under ``path`` to ``plugin_id`` (the stack fallback).

    Called by the plugin manager when it loads a plugin. Both the path as
    given and its real path are registered: plugin modules import from
    ``sys.path`` (the directory as given, possibly a dev symlink) and from
    the resolved entry point.
    """
    global _plugin_dirs
    if not plugin_id or path is None:
        return
    prefixes = {_norm_dir(str(path))}
    try:
        prefixes.add(_norm_dir(os.path.realpath(str(path))))
    except OSError:
        pass
    with _dirs_lock:
        entries = {prefix: pid for prefix, pid in _plugin_dirs if pid != plugin_id}
        for prefix in prefixes:
            entries[prefix] = plugin_id
        _plugin_dirs = tuple(sorted(entries.items(), key=lambda item: -len(item[0])))
        _file_owner.clear()


def unregister_plugin_directory(plugin_id: str) -> None:
    """Forget ``plugin_id``'s directories (the plugin was unloaded)."""
    global _plugin_dirs
    with _dirs_lock:
        _plugin_dirs = tuple(item for item in _plugin_dirs if item[1] != plugin_id)
        _file_owner.clear()


def _owner_of_file(filename: str, dirs: Tuple[Tuple[str, str], ...]) -> str:
    owner = _file_owner.get(filename)
    if owner is not None:
        return owner
    normalised = os.path.normcase(filename)
    owner = ""
    for prefix, plugin_id in dirs:
        if normalised.startswith(prefix):
            owner = plugin_id
            break
    if len(_file_owner) < 10000:
        _file_owner[filename] = owner
    return owner


def _plugin_from_stack() -> Optional[str]:
    dirs = _plugin_dirs
    if not dirs:
        return None
    frame: Any = sys._getframe(1)
    depth = 0
    while frame is not None and depth < _MAX_STACK_DEPTH:
        owner = _owner_of_file(frame.f_code.co_filename, dirs)
        if owner:
            return owner
        frame = frame.f_back
        depth += 1
    return None


def current_plugin_id() -> Optional[str]:
    """The plugin the current code is running for, or None for the core."""
    plugin_id = _current_plugin.get()
    if plugin_id:
        return plugin_id
    try:
        return _plugin_from_stack()
    except Exception:  # never let attribution break a fetch
        return None


@contextlib.contextmanager
def plugin_scope(plugin_id: Optional[str]) -> Iterator[None]:
    """Attribute fetches made inside the block (on this thread, and on threads
    the core starts from it) to ``plugin_id``. ``None`` leaves the current
    attribution alone."""
    if not plugin_id:
        yield
        return
    token = _current_plugin.set(plugin_id)
    try:
        yield
    finally:
        _current_plugin.reset(token)


@contextlib.contextmanager
def pinned_caller() -> Iterator[None]:
    """Resolve the caller now and pin it into the context, so a context copied
    inside the block (``contextvars.copy_context()``) for a worker thread
    carries the plugin even when only the stack could name it."""
    with plugin_scope(current_plugin_id()):
        yield


# --- token bucket -------------------------------------------------------------

class TokenBucket:
    """``per_second`` tokens a second, holding at most ``burst``.

    :meth:`reserve` takes a token and says how long the caller must wait for
    it. Tokens may go negative -- each caller reserves its own slot -- but
    never further than ``max_wait`` seconds' worth, so no request waits
    longer than that.
    """

    def __init__(self, per_second: float, burst: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.per_second = float(per_second)
        self.burst = max(1.0, float(burst))
        self._clock = clock
        self._tokens = self.burst
        self._last = clock()
        self._lock = threading.Lock()

    def reserve(self, max_wait: float) -> Tuple[float, bool]:
        """Take a token. Returns (seconds to wait, whether max_wait capped it)."""
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._last)
            self._last = now
            self._tokens = min(self.burst, self._tokens + elapsed * self.per_second)
            self._tokens -= 1.0
            if self._tokens >= 0:
                return 0.0, False
            wait = -self._tokens / self.per_second
            if wait > max_wait:
                self._tokens = -max_wait * self.per_second
                return max(0.0, max_wait), True
            return wait, False

    @property
    def tokens(self) -> float:
        with self._lock:
            return self._tokens


# --- validator store ------------------------------------------------------------

@dataclass
class _Stored:
    etag: Optional[str]
    last_modified: Optional[str]
    body: bytes
    headers: Dict[str, str]
    encoding: Optional[str]


class _ValidatorStore:
    """LRU of (body, validators) for responses that carried validators."""

    def __init__(self, max_entries: int, max_bytes: int, max_entry_bytes: int) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self._entries: "OrderedDict[Any, _Stored]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[_Stored]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: Any, entry: _Stored) -> None:
        size = len(entry.body)
        with self._lock:
            self._drop_locked(key)
            if size > self.max_entry_bytes or self.max_entries <= 0:
                return
            self._entries[key] = entry
            self._bytes += size
            while self._entries and (len(self._entries) > self.max_entries
                                     or self._bytes > self.max_bytes):
                _, old = self._entries.popitem(last=False)
                self._bytes -= len(old.body)

    def drop(self, key: Any) -> None:
        with self._lock:
            self._drop_locked(key)

    def _drop_locked(self, key: Any) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._bytes -= len(old.body)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "bytes": self._bytes}


# --- response cache (Cache-Control: max-age) --------------------------------------

@dataclass
class _Fresh:
    response: requests.Response
    stored_at: float
    #: Seconds after stored_at the server said the response stays fresh.
    lifetime: float
    size: int


class _ResponseCache:
    """LRU of finished 200 responses, each kept for its server max-age.

    :meth:`get` answers only while the entry is younger than both its own
    lifetime and the caller's limit, so nobody is handed a response older
    than they asked for. Expired entries are dropped as they are met and on
    every insert, so the cache holds only what is still fresh.
    """

    def __init__(self, max_entries: int, max_bytes: int, max_entry_bytes: int,
                 clock: Callable[[], float]) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self._clock = clock
        self._entries: "OrderedDict[Any, _Fresh]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Any, max_age: float) -> Optional[requests.Response]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            age = self._clock() - entry.stored_at
            if age < 0 or age >= entry.lifetime:
                self._drop_locked(key)
                return None
            if age > max_age:
                return None  # fresh for someone less strict; kept
            self._entries.move_to_end(key)
            clone: requests.Response = _clone_response(entry.response)
            return clone

    def put(self, key: Any, response: requests.Response, lifetime: float,
            size: int) -> None:
        with self._lock:
            self._drop_locked(key)
            if size > self.max_entry_bytes or self.max_entries <= 0 or lifetime <= 0:
                return
            now = self._clock()
            for old_key in [k for k, e in self._entries.items()
                            if now - e.stored_at >= e.lifetime]:
                self._drop_locked(old_key)
            self._entries[key] = _Fresh(_clone_response(response), now, lifetime, size)
            self._bytes += size
            while self._entries and (len(self._entries) > self.max_entries
                                     or self._bytes > self.max_bytes):
                _, old = self._entries.popitem(last=False)
                self._bytes -= old.size

    def _drop_locked(self, key: Any) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._bytes -= old.size

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "bytes": self._bytes}


def _cache_directives(value: Optional[str]) -> Dict[str, Optional[str]]:
    directives: Dict[str, Optional[str]] = {}
    for part in (value or "").split(","):
        name, _, arg = part.strip().partition("=")
        if name:
            directives[name.strip().lower()] = arg.strip().strip('"') if arg else None
    return directives


def _fresh_for(response: Any) -> Optional[float]:
    """Seconds a finished response stays fresh by its own headers, or None
    when it must not be reused: not a 200 with its body read, ``no-store``,
    ``no-cache``, ``private``, ``Vary: *``, ``Set-Cookie``, or no max-age."""
    if _status_of(response) != 200 or _body_of(response) is None:
        return None
    directives = _cache_directives(_str_header(response, "Cache-Control"))
    if {"no-store", "no-cache", "private"} & set(directives):
        return None
    if (_str_header(response, "Vary") or "").strip() == "*":
        return None
    if _str_header(response, "Set-Cookie"):
        return None
    try:
        max_age = int(directives.get("max-age") or "")
    except ValueError:
        return None
    try:
        age = int(_str_header(response, "Age") or 0)
    except ValueError:
        age = 0
    fresh = float(min(max_age - max(age, 0), _RESPONSE_CACHE_CEILING))
    return fresh if fresh > 0 else None


# --- helpers ------------------------------------------------------------------------

def _host_of(url: Any) -> str:
    try:
        host = urlsplit(str(url)).hostname
    except ValueError:
        host = None
    return (host or "unknown").lower()


def _prepared_url(url: str, params: Any) -> str:
    prepared = PreparedRequest()
    prepared.prepare_url(url, params)
    return str(prepared.url)


def _retry_fingerprint(retries: Any) -> Tuple[Any, ...]:
    """What makes two urllib3 Retry configurations behave alike."""
    names = ("total", "connect", "read", "redirect", "status", "other",
             "backoff_factor", "backoff_max", "raise_on_redirect",
             "raise_on_status", "respect_retry_after_header")
    values: List[Any] = [type(retries).__name__]
    for name in names:
        values.append(getattr(retries, name, None))
    for name in ("status_forcelist", "allowed_methods"):
        value = getattr(retries, name, None)
        if isinstance(value, (set, frozenset, list, tuple)):
            value = tuple(sorted(str(v) for v in value))
        values.append(value if value is None or isinstance(value, (tuple, bool)) else repr(value))
    return tuple(values)


def _adapter_fingerprint(adapter: Any) -> Tuple[Any, ...]:
    if type(adapter) is HTTPAdapter:
        return ("HTTPAdapter", _retry_fingerprint(adapter.max_retries))
    return ("adapter", id(adapter))


def _header_items(headers: Any) -> Tuple[Tuple[str, str], ...]:
    if not headers:
        return ()
    return tuple(sorted((str(k).lower(), str(v)) for k, v in dict(headers).items()
                        if v is not None))


def _has_conditional_headers(headers: Any) -> bool:
    if not headers:
        return False
    try:
        names = {str(k).lower() for k in dict(headers)}
    except (TypeError, ValueError):
        return True
    return bool(names & {"if-none-match", "if-modified-since", "if-match",
                         "if-unmodified-since", "if-range"})


def _str_header(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None)
    try:
        value = headers.get(name) if headers is not None else None
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _status_of(response: Any) -> Optional[int]:
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _body_of(response: Any) -> Optional[bytes]:
    """The already-read body, or None. Never reads a stream."""
    if not isinstance(response, requests.Response):
        return None
    content = getattr(response, "_content", None)
    return content if isinstance(content, bytes) else None


def _wire_bytes_of(response: Any, body: Optional[bytes]) -> int:
    """How many body bytes came off the socket for ``response``: the
    compressed size when the server sent gzip, which ESPN does for every
    scoreboard (63 KB on the wire for an 865 KB college football Saturday).

    urllib3's ``HTTPResponse.tell()`` counts the raw bytes read before
    decoding. A response without one (a test double, an adapter that is not
    urllib3) or one whose body was not read is counted at its decoded size,
    or as 0, so the counter never claims less than it can prove.
    """
    if body is None:
        return 0
    raw = getattr(response, "raw", None)
    tell = getattr(raw, "tell", None)
    if callable(tell):
        try:
            read = tell()
        except Exception:
            read = None
        if isinstance(read, int) and not isinstance(read, bool) and read > 0:
            return read
    return len(body)


def _retries_of(response: Any) -> int:
    raw = getattr(response, "raw", None)
    retries = getattr(raw, "retries", None)
    history = getattr(retries, "history", None)
    return len(history) if isinstance(history, tuple) else 0


def _clone_response(response: Any) -> Any:
    """A copy of a finished response for a merged caller."""
    if not isinstance(response, requests.Response):
        return response
    clone = copy.copy(response)
    clone.headers = CaseInsensitiveDict(response.headers)
    return clone


def _clone_error(error: BaseException) -> BaseException:
    try:
        return copy.copy(error)
    except Exception:
        return error


def _from_stored(entry: _Stored, not_modified: requests.Response) -> requests.Response:
    """The 200 the caller would have got, rebuilt from a 304 and the store."""
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response._content = entry.body
    response._content_consumed = True  # type: ignore[attr-defined]
    headers = CaseInsensitiveDict(entry.headers)
    for name, value in (not_modified.headers or {}).items():
        if name.lower() not in ("content-length", "content-encoding", "transfer-encoding"):
            headers[name] = value
    response.headers = headers
    response.encoding = entry.encoding
    response.url = not_modified.url
    response.request = not_modified.request
    response.history = not_modified.history
    response.elapsed = not_modified.elapsed
    response.cookies = not_modified.cookies
    response.connection = getattr(not_modified, "connection", None)  # type: ignore[assignment]
    response.raw = not_modified.raw
    return response


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value >= 0 else default


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    response: Any = None
    error: Optional[BaseException] = None
    #: Callers waiting on this flight (read by tests).
    waiters: int = 0


@dataclass
class _Request:
    """What one call resolved to before it goes out."""
    plugin: str
    host: str
    # Same URL + effective headers: what the validator store is keyed by.
    representation: Optional[Tuple[Any, ...]] = None
    # representation + timeout + retry policy + other kwargs: what merges.
    flight: Optional[Tuple[Any, ...]] = None


# --- the service ----------------------------------------------------------------------

class FetchService:
    """See the module docstring. One per process: :func:`get_fetch_service`."""

    def __init__(self, config: Any = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock
        self.started_at = wall_clock()
        self._lock = threading.Lock()
        self._inflight: Dict[Tuple[Any, ...], _Flight] = {}
        self._buckets: Dict[str, Optional[TokenBucket]] = {}
        self._adapters: Dict[Tuple[Any, ...], HTTPAdapter] = {}
        self._sessions: Dict[str, requests.Session] = {}
        self._plugins: Dict[str, Dict[str, float]] = {}
        self._plugin_hosts: Dict[str, Dict[str, int]] = {}
        self._hosts: Dict[str, Dict[str, float]] = {}
        self._totals: Dict[str, float] = dict.fromkeys(_COUNTER_FIELDS, 0)
        #: Bumped on every counter change; the publisher compares it.
        self.change_count = 0
        self.enabled = True
        self.single_flight = True
        self.conditional_get = True
        self.max_wait_seconds = 2.0
        self._rate_limits: Dict[str, Tuple[float, float]] = {}
        self._validators = _ValidatorStore(0, 0, 0)
        self.response_cache = True
        self.default_max_age = 30.0
        self._fresh = _ResponseCache(0, 0, 0, clock)
        self._applied: Optional[str] = None
        self.configure(config)

    # -- configuration --

    def configure(self, config: Any = None) -> None:
        """Apply ``config`` (the ``fetch_service`` section) over DEFAULT_CONFIG.
        Bad values are ignored with a warning; this never raises. Applying
        the same section again is a no-op: a config hot reload for any other
        setting keeps the budgets and the validator store."""
        try:
            signature = json.dumps(config, sort_keys=True, default=repr)
        except (TypeError, ValueError):
            signature = repr(config)
        if signature == self._applied:
            return
        self._applied = signature
        merged: Dict[str, Any] = dict(DEFAULT_CONFIG)
        if isinstance(config, Mapping):
            merged.update({k: v for k, v in config.items() if v is not None})
        elif config is not None:
            logger.warning("fetch_service config is not an object; using the defaults")

        self.enabled = merged.get("enabled") is not False
        self.single_flight = merged.get("single_flight") is not False
        self.conditional_get = merged.get("conditional_get") is not False
        self.max_wait_seconds = _as_float(merged.get("max_wait_seconds"),
                                          float(DEFAULT_CONFIG["max_wait_seconds"]))

        limits: Dict[str, Tuple[float, float]] = {}
        raw_limits = merged.get("rate_limits")
        if isinstance(raw_limits, Mapping):
            for pattern, spec in raw_limits.items():
                if not isinstance(pattern, str) or not pattern.strip():
                    continue
                if spec is None:
                    continue
                if not isinstance(spec, Mapping):
                    logger.warning("fetch_service.rate_limits[%r] is not an object; ignored", pattern)
                    continue
                per_second = _as_float(spec.get("per_second"), -1.0)
                if spec.get("per_second") in (None, 0):
                    continue  # explicitly unthrottled
                if per_second <= 0:
                    logger.warning("fetch_service.rate_limits[%r].per_second must be a "
                                   "positive number; ignored", pattern)
                    continue
                burst = _as_float(spec.get("burst"), max(1.0, per_second))
                limits[pattern.strip().lower()] = (per_second, max(1.0, burst))
        elif raw_limits is not None:
            logger.warning("fetch_service.rate_limits is not an object; no host budgets")

        store = merged.get("validator_store")
        store = store if isinstance(store, Mapping) else {}
        default_store = DEFAULT_CONFIG["validator_store"]
        fresh = merged.get("response_cache")
        if fresh is not None and not isinstance(fresh, Mapping):
            logger.warning("fetch_service.response_cache is not an object; using the defaults")
        fresh = fresh if isinstance(fresh, Mapping) else {}
        default_fresh = DEFAULT_CONFIG["response_cache"]
        self.response_cache = fresh.get("enabled") is not False
        self.default_max_age = _as_float(fresh.get("default_max_age"),
                                         float(default_fresh["default_max_age"]))
        with self._lock:
            self._rate_limits = limits
            self._buckets.clear()
            self._validators = _ValidatorStore(
                _as_int(store.get("max_entries"), default_store["max_entries"]),
                _as_int(store.get("max_bytes"), default_store["max_bytes"]),
                _as_int(store.get("max_entry_bytes"), default_store["max_entry_bytes"]),
            )
            self._fresh = _ResponseCache(
                _as_int(fresh.get("max_entries"), default_fresh["max_entries"]),
                _as_int(fresh.get("max_bytes"), default_fresh["max_bytes"]),
                _as_int(fresh.get("max_entry_bytes"), default_fresh["max_entry_bytes"]),
                self._clock,
            )
            self.change_count += 1

    def describe_config(self) -> Dict[str, Any]:
        with self._lock:
            limits = {pattern: {"per_second": rate, "burst": burst}
                      for pattern, (rate, burst) in sorted(self._rate_limits.items())}
        return {
            "enabled": self.enabled,
            "single_flight": self.single_flight,
            "conditional_get": self.conditional_get,
            "max_wait_seconds": self.max_wait_seconds,
            "rate_limits": limits,
            "response_cache": self.response_cache,
            "default_max_age": self.default_max_age,
        }

    def _limit_for(self, host: str) -> Optional[Tuple[float, float]]:
        limits = self._rate_limits
        exact = limits.get(host)
        if exact is not None:
            return exact
        best: Optional[Tuple[float, float]] = None
        best_len = -1
        for pattern, limit in limits.items():
            if pattern.startswith("*."):
                suffix = pattern[1:]          # ".espn.com"
                if (host.endswith(suffix) or host == suffix[1:]) and len(suffix) > best_len:
                    best, best_len = limit, len(suffix)
        return best

    def _bucket_for(self, host: str) -> Optional[TokenBucket]:
        with self._lock:
            if host in self._buckets:
                return self._buckets[host]
            limit = self._limit_for(host)
            bucket = None if limit is None else TokenBucket(limit[0], limit[1], self._clock)
            if len(self._buckets) < 1000:
                self._buckets[host] = bucket
            return bucket

    # -- pooling --

    def shared_adapter(self, max_retries: Any = 0) -> HTTPAdapter:
        """The shared adapter for a retry policy (an int or a urllib3 Retry)."""
        probe = HTTPAdapter(max_retries=max_retries)
        key = _retry_fingerprint(probe.max_retries)
        with self._lock:
            adapter = self._adapters.get(key)
            if adapter is None:
                adapter = HTTPAdapter(pool_connections=_POOL_CONNECTIONS,
                                      pool_maxsize=_POOL_MAXSIZE,
                                      max_retries=max_retries)
                self._adapters[key] = adapter
            return adapter

    def session_for(self, url: str) -> requests.Session:
        """The pooled Session for ``url``'s host, for a caller without one.

        Stateless on purpose -- it is shared by every such caller -- so it
        accepts no cookies; pass headers per request.
        """
        host = _host_of(url)
        with self._lock:
            session = self._sessions.get(host)
        if session is not None:
            return session
        session = requests.Session()
        session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        try:
            from src.common.api_helper import DEFAULT_HTTP_HEADERS
            session.headers.update(DEFAULT_HTTP_HEADERS)
        except ImportError:  # pragma: no cover - always importable in core
            pass
        adapter = self.shared_adapter(0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        with self._lock:
            return self._sessions.setdefault(host, session)

    # -- requests --

    def get(self, session: Any, url: str, *, share_in_flight: bool = True,
            cache_max_age: Optional[float] = None, **kwargs: Any) -> Any:
        """``session.get(url, **kwargs)`` through the service.

        Same return value, same exceptions, and ``session.get`` is called
        with the caller's own arguments (plus validator headers when the
        store holds some). ``session=None`` uses :meth:`session_for`.

        ``share_in_flight=False`` always sends this request itself rather
        than joining an identical one in flight -- for a caller that may
        retry *because* an earlier request hung (BackgroundDataService
        cancels and replaces a fetch) and must not be handed that one.

        ``cache_max_age`` is the oldest response, in seconds, the caller
        will take from the response cache (its own TTL); 0 always asks the
        network. None means ``response_cache.default_max_age``. A response
        is never reused past the max-age its server gave it either.
        """
        transport = session if session is not None else self.session_for(url)
        if not self.enabled:
            return transport.get(url, **kwargs)
        try:
            request = self._resolve(transport, url, kwargs)
        except Exception:
            logger.debug("fetch_service could not key a request to %s", url, exc_info=True)
            request = _Request(plugin=self._caller(), host=_host_of(url))

        reusable = (self.response_cache and request.representation is not None
                    and not _has_conditional_headers(kwargs.get("headers")))
        if reusable:
            try:
                limit = self._accepted_age(cache_max_age)
                cached = self._fresh.get(request.representation, limit) if limit > 0 else None
            except Exception:
                logger.debug("fetch_service response cache lookup failed", exc_info=True)
                cached = None
            if cached is not None:
                self._count(request.plugin, request.host, memo_hits=1)
                return cached

        if request.flight is None or not self.single_flight or not share_in_flight:
            response = self._send_get(transport, url, kwargs, request)
            if reusable:
                self._remember(request, response)
            return response

        with self._lock:
            flight = self._inflight.get(request.flight)
            leader = flight is None
            if flight is None:
                flight = _Flight()
                self._inflight[request.flight] = flight
            else:
                flight.waiters += 1
        if not leader:
            flight.event.wait()
            self._count(request.plugin, request.host, merged=1)
            if flight.error is not None:
                raise _clone_error(flight.error)
            return _clone_response(flight.response)
        try:
            response = self._send_get(transport, url, kwargs, request)
            flight.response = response
            if reusable:
                self._remember(request, response)
            return response
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._lock:
                if self._inflight.get(request.flight) is flight:
                    del self._inflight[request.flight]
            flight.event.set()

    def post(self, session: Any, url: str, **kwargs: Any) -> Any:
        """``session.post(url, **kwargs)``: counted and budgeted, never merged."""
        transport = session if session is not None else self.session_for(url)
        if not self.enabled:
            return transport.post(url, **kwargs)
        request = _Request(plugin=self._caller(), host=_host_of(url))
        return self._send(lambda: transport.post(url, **kwargs), request)

    def note_merged(self, url: Any = None, plugin_id: Optional[str] = None) -> None:
        """Count a request answered by another caller's fetch outside this
        service (BackgroundDataService joining an in-flight cache key)."""
        try:
            self._count(plugin_id or self._caller(), _host_of(url), merged=1)
        except Exception:
            logger.debug("fetch_service could not count a merged request", exc_info=True)

    def note_cache_hit(self, url: Any = None, *, legacy: bool = False,
                       avoided_request: bool = True,
                       plugin_id: Optional[str] = None) -> None:
        """Count a read answered from a shared cache entry instead of the
        network (``espn_dates``). ``legacy`` marks a read from a key that
        predates the canonical one; ``avoided_request=False`` counts only
        that, for a read that was never going to fetch on a miss."""
        try:
            self._count(plugin_id or self._caller(), _host_of(url),
                        cache_hits=int(avoided_request), legacy_cache_hits=int(legacy))
        except Exception:
            logger.debug("fetch_service could not count a cache hit", exc_info=True)

    def _accepted_age(self, cache_max_age: Any) -> float:
        """The oldest cached response this call accepts, in seconds."""
        if cache_max_age is None:
            return self.default_max_age
        if isinstance(cache_max_age, bool) or not isinstance(cache_max_age, (int, float)):
            return self.default_max_age
        value = float(cache_max_age)
        return value if math.isfinite(value) and value > 0 else 0.0

    def _remember(self, request: _Request, response: Any) -> None:
        """Keep a finished response for its server max-age. Never raises."""
        try:
            lifetime = _fresh_for(response)
            if lifetime is None:
                return
            body = _body_of(response)
            self._fresh.put(request.representation, response, lifetime,
                            len(body) if body is not None else 0)
        except Exception:
            logger.debug("fetch_service could not keep a response", exc_info=True)

    def _caller(self) -> str:
        return current_plugin_id() or CORE

    def _resolve(self, transport: Any, url: str, kwargs: Dict[str, Any]) -> _Request:
        request = _Request(plugin=self._caller(), host=_host_of(url))
        if kwargs.get("stream"):
            return request  # body not read here: nothing to share or store
        full_url = _prepared_url(url, kwargs.get("params"))
        headers = kwargs.get("headers")
        identity = getattr(transport, "fetch_identity_session", None)
        session = identity if isinstance(identity, requests.Session) else transport
        if isinstance(session, requests.Session):
            effective = merge_setting(headers, session.headers, dict_class=CaseInsensitiveDict)
            header_items = _header_items(effective)
            policy: Tuple[Any, ...] = (type(transport).__name__,
                                       _adapter_fingerprint(session.get_adapter(full_url)))
            stateful = bool(session.cookies) or session.auth is not None
            owner = id(session) if stateful else None
        else:
            header_items = _header_items(headers)
            policy = (type(transport).__name__, id(transport))
            owner = id(transport)
        request.representation = (full_url, header_items, owner)
        others = tuple(sorted((k, repr(v)) for k, v in kwargs.items()
                              if k not in ("params", "headers")))
        request.flight = ("GET", request.representation, policy, others)
        return request

    def _send_get(self, transport: Any, url: str, kwargs: Dict[str, Any],
                  request: _Request) -> Any:
        stored: Optional[_Stored] = None
        call_kwargs = kwargs
        conditional = (self.conditional_get and request.representation is not None
                       and not _has_conditional_headers(kwargs.get("headers")))
        if conditional:
            stored = self._validators.get(request.representation)
            if stored is not None:
                extra: Dict[str, str] = {}
                if stored.etag:
                    extra["If-None-Match"] = stored.etag
                if stored.last_modified:
                    extra["If-Modified-Since"] = stored.last_modified
                headers = kwargs.get("headers")
                merged_headers: Any = (CaseInsensitiveDict(headers) if headers
                                       else CaseInsensitiveDict())
                merged_headers.update(extra)
                call_kwargs = dict(kwargs, headers=merged_headers)

        response = self._send(lambda: transport.get(url, **call_kwargs), request,
                              stored=stored)
        if conditional:
            try:
                response = self._after_conditional(request, response, stored)
            except Exception:
                logger.debug("fetch_service validator bookkeeping failed for %s", url,
                             exc_info=True)
        return response

    def _after_conditional(self, request: _Request, response: Any,
                           stored: Optional[_Stored]) -> Any:
        status = _status_of(response)
        key = request.representation
        if status == 304 and stored is not None and isinstance(response, requests.Response):
            return _from_stored(stored, response)
        if status == 200:
            body = _body_of(response)
            etag = _str_header(response, "ETag")
            last_modified = _str_header(response, "Last-Modified")
            no_store = "no-store" in (_str_header(response, "Cache-Control") or "").lower()
            if body is not None and (etag or last_modified) and not no_store:
                self._validators.put(key, _Stored(
                    etag=etag, last_modified=last_modified, body=body,
                    headers=dict(response.headers), encoding=response.encoding))
            else:
                self._validators.drop(key)
        return response

    def _send(self, call: Callable[[], Any], request: _Request,
              stored: Optional[_Stored] = None) -> Any:
        waited = 0.0
        overrun = False
        try:
            bucket = self._bucket_for(request.host)
            if bucket is not None:
                waited, overrun = bucket.reserve(self.max_wait_seconds)
        except Exception:
            logger.debug("fetch_service budget check failed", exc_info=True)
            waited, overrun = 0.0, False
        if waited > 0:
            self._sleep(waited)
        try:
            response = call()
        except BaseException:
            self._count(request.plugin, request.host, requests=1, errors=1,
                        throttled=int(waited > 0), overruns=int(overrun),
                        wait_seconds=waited)
            raise
        try:
            status = _status_of(response)
            body = _body_of(response)
            not_modified = int(status == 304 and stored is not None)
            self._count(request.plugin, request.host, requests=1,
                        not_modified=not_modified,
                        http_errors=int(status is not None and status >= 400),
                        retries=_retries_of(response),
                        bytes=len(body) if body is not None else 0,
                        wire_bytes=_wire_bytes_of(response, body),
                        throttled=int(waited > 0), overruns=int(overrun),
                        wait_seconds=waited)
        except Exception:
            logger.debug("fetch_service could not count a response", exc_info=True)
        return response

    # -- counters --

    def _count(self, plugin: str, host: str, **deltas: float) -> None:
        """Add ``deltas`` to the plugin's, the host's and the totals. Never
        raises: it runs on every fetch's success and failure paths."""
        try:
            self._apply_counts(plugin, host, deltas)
        except Exception:
            logger.debug("fetch_service could not update its counters", exc_info=True)

    def _apply_counts(self, plugin: str, host: str, deltas: Dict[str, float]) -> None:
        changes = {k: v for k, v in deltas.items() if v}
        if not changes:
            return
        with self._lock:
            if plugin not in self._plugins and len(self._plugins) >= _MAX_PLUGINS:
                plugin = _OTHER
            if host not in self._hosts and len(self._hosts) >= _MAX_HOSTS:
                host = _OTHER
            per_plugin = self._plugins.setdefault(plugin, dict.fromkeys(_COUNTER_FIELDS, 0))
            per_host = self._hosts.setdefault(host, dict.fromkeys(_COUNTER_FIELDS, 0))
            for name, value in changes.items():
                per_plugin[name] += value
                per_host[name] += value
                self._totals[name] += value
            asked = int(changes.get("requests", 0) + changes.get("merged", 0)
                        + changes.get("memo_hits", 0) + changes.get("cache_hits", 0))
            if asked:
                hosts = self._plugin_hosts.setdefault(plugin, {})
                hosts[host] = hosts.get(host, 0) + asked
            self.change_count += 1

    def reset_counters(self) -> None:
        with self._lock:
            self._plugins.clear()
            self._plugin_hosts.clear()
            self._hosts.clear()
            self._totals = dict.fromkeys(_COUNTER_FIELDS, 0)
            self.change_count += 1

    def reset(self) -> None:
        """Counters, validators, response cache, budgets and in-flight
        table (tests)."""
        self.reset_counters()
        with self._lock:
            self._buckets.clear()
            self._inflight.clear()
        self._validators.clear()
        self._fresh.clear()

    def snapshot(self) -> Dict[str, Any]:
        """Counters since the service started, JSON-ready."""
        def rounded(counters: Mapping[str, float]) -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            for name in _COUNTER_FIELDS:
                value = counters.get(name, 0)
                out[name] = round(float(value), 3) if name == "wait_seconds" else int(value)
            return out

        with self._lock:
            plugins = {pid: dict(rounded(c), hosts=dict(self._plugin_hosts.get(pid, {})))
                       for pid, c in self._plugins.items()}
            hosts = {host: rounded(c) for host, c in self._hosts.items()}
            totals = rounded(self._totals)
            change = self.change_count
        return {
            "since": self.started_at,
            "change_count": change,
            "totals": totals,
            "plugins": plugins,
            "hosts": hosts,
            "validators": self._validators.stats(),
            "response_cache": self._fresh.stats(),
            "config": self.describe_config(),
        }


# --- process-wide instance ---------------------------------------------------------

_service: Optional[FetchService] = None
_service_lock = threading.Lock()


def get_fetch_service() -> FetchService:
    """The process's FetchService, created with the defaults on first use."""
    global _service
    service = _service
    if service is not None:
        return service
    with _service_lock:
        if _service is None:
            _service = FetchService()
        return _service


def configure_fetch_service(config: Any) -> FetchService:
    """Apply config.json's ``fetch_service`` section. Never raises."""
    service = get_fetch_service()
    try:
        service.configure(config)
    except Exception:
        logger.warning("Could not apply the fetch_service config; keeping the defaults",
                       exc_info=True)
    return service


def fetch_get(session: Any, url: str, *, share_in_flight: bool = True,
              cache_max_age: Optional[float] = None, **kwargs: Any) -> Any:
    """``session.get(url, **kwargs)`` through the process's FetchService.
    ``cache_max_age``: see :meth:`FetchService.get`."""
    return get_fetch_service().get(session, url, share_in_flight=share_in_flight,
                                   cache_max_age=cache_max_age, **kwargs)


def fetch_post(session: Any, url: str, **kwargs: Any) -> Any:
    """``session.post(url, **kwargs)`` through the process's FetchService."""
    return get_fetch_service().post(session, url, **kwargs)


def share_connection_pool(session: Any, max_retries: Any = 0) -> None:
    """Mount the shared adapter for ``max_retries`` on ``session``'s http and
    https prefixes, replacing what was there. Pass the same retry policy the
    Session would otherwise mount: only the pool becomes shared. A no-op for
    anything that is not a requests Session (a test double)."""
    if not isinstance(session, requests.Session):
        return
    adapter = get_fetch_service().shared_adapter(max_retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)


# --- publishing (display) and reading (web) ----------------------------------------

FETCH_STATS_KEY = "fetch_stats_snapshot"
SNAPSHOT_SCHEMA = 1

#: Shortest gap between two writes: the snapshot is on the SD card.
MIN_INTERVAL = 60.0
#: An unchanged snapshot is rewritten this often so readers can tell a quiet
#: display from a dead one, and the cache's retention sweep keeps it.
REFRESH_INTERVAL = 600.0
TICK_INTERVAL = 15.0
STALE_AFTER = REFRESH_INTERVAL + 2 * MIN_INTERVAL

LIVE = "live"
STALE = "stale"
STOPPED = "stopped"
UNKNOWN = "unknown"


class FetchStatsPublisher:
    """Publishes the service's counters to the shared cache (display only).

    Written when the counters changed, at most once every ``min_interval``
    seconds, and otherwise every ``refresh_interval`` seconds. Never raises.
    """

    def __init__(self, cache_manager: Any, service: Optional[FetchService] = None,
                 min_interval: float = MIN_INTERVAL,
                 refresh_interval: float = REFRESH_INTERVAL,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self.cache_manager = cache_manager
        self.service = service or get_fetch_service()
        self.min_interval = min_interval
        self.refresh_interval = refresh_interval
        self._clock = clock
        self._wall_clock = wall_clock
        self._published_change: Optional[int] = None
        self._last_attempt: Optional[float] = None
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _write(self, running: bool) -> None:
        snapshot = self.service.snapshot()
        snapshot.update({
            "schema": SNAPSHOT_SCHEMA,
            "running": running,
            "published_at": self._wall_clock(),
            "stale_after": STALE_AFTER,
            "pid": os.getpid(),
        })
        self.cache_manager.set(FETCH_STATS_KEY, snapshot)

    def tick(self) -> bool:
        """Publish if due. True if a snapshot was written."""
        with self._tick_lock:
            try:
                change = self.service.change_count
                now = self._clock()
                if self._last_attempt is not None:
                    since = now - self._last_attempt
                    if change == self._published_change:
                        if since < self.refresh_interval:
                            return False
                    elif since < self.min_interval:
                        return False
                self._last_attempt = now
                self._write(running=True)
                self._published_change = change
                return True
            except Exception as err:
                logger.debug("Could not publish fetch stats: %s", err, exc_info=True)
                return False

    def start(self, interval: float = TICK_INTERVAL) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            # First write replaces whatever a previous run left behind.
            self.tick()
            while not self._stop.wait(interval):
                self.tick()

        self._thread = threading.Thread(target=run, name="fetch-stats-publisher", daemon=True)
        self._thread.start()

    def stop(self, publish_stopped: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if publish_stopped:
            with self._tick_lock:
                try:
                    self._write(running=False)
                except Exception as err:
                    logger.debug("Could not publish stopped fetch stats: %s", err, exc_info=True)


def start_fetch_stats_publisher(cache_manager: Any) -> Optional[FetchStatsPublisher]:
    """Start publishing this process's fetch counters. Display service only.
    Never raises."""
    try:
        publisher = FetchStatsPublisher(cache_manager)
        publisher.start()
        return publisher
    except Exception as err:
        logger.warning("Fetch statistics for the web interface are unavailable: %s", err)
        return None


def read_fetch_stats(cache_manager: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """The display's latest counters, judged for staleness. Never raises.

    ``status`` is ``live``, ``stale`` (older than its ``stale_after``),
    ``stopped`` (the display said so on exit; the final counters are still
    given) or ``unknown`` (no readable snapshot). ``data`` is the snapshot,
    or None when unknown.
    """
    if cache_manager is None:
        return {"status": UNKNOWN, "age_seconds": None, "data": None}
    try:
        snapshot = cache_manager.get(FETCH_STATS_KEY, max_age=None, memory_ttl=0)
    except Exception as err:
        logger.debug("Could not read fetch stats: %s", err, exc_info=True)
        snapshot = None
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        return {"status": UNKNOWN, "age_seconds": None, "data": None}
    published_at = snapshot.get("published_at")
    if isinstance(published_at, bool) or not isinstance(published_at, (int, float)):
        return {"status": UNKNOWN, "age_seconds": None, "data": None}
    stale_after = _as_float(snapshot.get("stale_after"), STALE_AFTER) or STALE_AFTER
    age = (time.time() if now is None else now) - float(published_at)
    if snapshot.get("running") is not True:
        status = STOPPED
    elif age > stale_after or age < -stale_after:
        status = STALE
    else:
        status = LIVE
    return {"status": status, "age_seconds": round(age, 1), "data": snapshot}
