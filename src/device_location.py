"""
The device's own location, in the shape a Starlark (Tidbyt/Pixlet) app expects.

A Pixlet ``schema.Location`` field is a JSON string -- ``{"lat": "35.2271",
"lng": "-80.8431", "timezone": "America/New_York", ...}`` -- and an app whose
field is left unset falls back to whatever its author hard-coded. Most
community apps hard-code San Francisco, so a user who set Charlotte under
General settings got San Francisco weather and a San Francisco radar map with
nothing in config.json to explain it.

Regular plugins already default their ``location_city``/``location_state``/
``location_country`` keys to the device location
(``SchemaManager.apply_device_location``). This module is the Starlark
equivalent. The device ``location`` block only has city/state/country, so the
city is geocoded once (Open-Meteo, the same service ledmatrix-weather uses)
and the coordinates are cached permanently -- cities don't move, so the
geocoder is only hit on a cache miss.

The substitution is applied at render time and never written into an app's
config.json, so a later change to the device location is picked up by the
next render. A location saved on the app itself always wins.
"""

import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_TIMEOUT = 10
#: More than the handful ledmatrix-weather asks for: a common name
#: (Springfield, Charlotte, Portland) has several US matches, and the one in
#: the configured state has to be among the results to be picked.
GEOCODE_RESULT_COUNT = 10

#: Coordinates for a fixed city never go stale.
COORDS_MAX_AGE = 10 * 365 * 24 * 3600
#: After a failed lookup, renders use the app's own default until this has
#: passed, so a geocoder outage costs one timeout, not one per render.
FAILURE_RETRY_SECONDS = 30 * 60

CACHE_KEY_PREFIX = "device_location:coords"

LOCATION_FIELD_TYPES = ("location",)

# Open-Meteo reports the full state name in ``admin1``; the device state may be
# typed either way.
US_STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "DC": "district of columbia", "FL": "florida",
    "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky",
    "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio",
    "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "PR": "puerto rico", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}

_COUNTRY_ALIASES = {"usa": "us", "united states": "us",
                    "united states of america": "us", "uk": "gb",
                    "united kingdom": "gb"}

logger = logging.getLogger(__name__)


def _norm(value: Any) -> str:
    """Lower-case, with ``_``/``-`` read as spaces ("North_Carolina")."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("_", " ").replace("-", " ").lower().split())


def _norm_state(value: Any) -> str:
    state = _norm(value)
    return US_STATE_NAMES.get(state.upper(), state)


def _norm_country(value: Any) -> str:
    country = _norm(value)
    return _COUNTRY_ALIASES.get(country, country)


def location_field_ids(schema: Optional[Dict[str, Any]]) -> List[str]:
    """Ids of a Starlark app schema's ``location`` fields."""
    if not isinstance(schema, dict):
        return []
    fields = schema.get("fields") or schema.get("schema") or []
    ids = []
    for field in fields:
        if not isinstance(field, dict) or not field.get("id"):
            continue
        # "typeOf" from both extractors; "type" if a raw pixlet schema slipped
        # through unremapped.
        field_type = field.get("typeOf", field.get("type"))
        if isinstance(field_type, str) and field_type.lower() in LOCATION_FIELD_TYPES:
            ids.append(field["id"])
    return ids


def parse_location(value: Any) -> Optional[Dict[str, Any]]:
    """The saved location as a dict, or None if it has no usable lat/lng.

    Blank, missing, unparseable, or lat/lng-less values (the config form sends
    ``{"timezone": ...}`` when only the timezone box is filled) all mean the
    user has not given the app a place.
    """
    if isinstance(value, dict):
        loc = value
    elif isinstance(value, str) and value.strip():
        try:
            loc = json.loads(value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not isinstance(loc, dict):
        return None
    try:
        float(loc["lat"])
        float(loc["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    return loc


def pick_geocode_result(results: Iterable[Dict[str, Any]], state: Any,
                        country: Any) -> Optional[Dict[str, Any]]:
    """Best geocoder hit: same country and state, then same country, then first."""
    results = [r for r in results if isinstance(r, dict)
               and "latitude" in r and "longitude" in r]
    if not results:
        return None
    want_state = _norm_state(state)
    want_country = _norm_country(country)

    def country_matches(r):
        return bool(want_country) and want_country in (
            _norm_country(r.get("country_code")), _norm_country(r.get("country")))

    def state_matches(r):
        return bool(want_state) and _norm_state(r.get("admin1")) == want_state

    for test in (lambda r: country_matches(r) and state_matches(r),
                 country_matches,
                 state_matches):
        for r in results:
            if test(r):
                return r
    return results[0]


def geocode(city: str, state: Any = None, country: Any = None,
            timeout: float = GEOCODE_TIMEOUT) -> Optional[Dict[str, Any]]:
    """Look the city up on Open-Meteo. Raises on a network/HTTP failure."""
    import requests

    response = requests.get(GEOCODE_URL, params={
        "name": city, "count": GEOCODE_RESULT_COUNT,
        "language": "en", "format": "json",
    }, timeout=timeout)
    response.raise_for_status()
    best = pick_geocode_result(response.json().get("results") or [], state, country)
    if best is None:
        return None
    return {
        "lat": best["latitude"],
        "lng": best["longitude"],
        "timezone": best.get("timezone"),
    }


class DeviceLocationResolver:
    """Resolves the device location to a Pixlet location JSON string.

    Cached coordinates live in ``cache_manager`` (shared on disk by the
    display and web processes) and in memory. A failed lookup is remembered
    for ``FAILURE_RETRY_SECONDS`` so renders in the meantime fall straight
    back to the app's own default.
    """

    def __init__(self, cache_manager: Any = None,
                 log: Optional[logging.Logger] = None,
                 geocoder: Callable[..., Optional[Dict[str, Any]]] = geocode,
                 clock: Callable[[], float] = time.time):
        self.cache_manager = cache_manager
        self.logger = log or logger
        self._geocode = geocoder
        self._clock = clock
        self._coords: Dict[str, Dict[str, Any]] = {}
        self._failed_at: Dict[str, float] = {}

    @staticmethod
    def _cache_key(city: str, state: str, country: str) -> str:
        # The key is a filename on disk: no spaces.
        parts = (_norm(city), _norm_state(state), _norm_country(country))
        return ":".join((CACHE_KEY_PREFIX,) + tuple(p.replace(" ", "_") for p in parts))

    def _cached(self, key: str) -> Optional[Dict[str, Any]]:
        if key in self._coords:
            return self._coords[key]
        if self.cache_manager is None:
            return None
        try:
            cached = self.cache_manager.get(key, max_age=COORDS_MAX_AGE)
        except Exception:
            self.logger.debug("Could not read cached device coordinates", exc_info=True)
            return None
        if isinstance(cached, dict) and "lat" in cached and "lng" in cached:
            self._coords[key] = cached
            return cached
        return None

    def coordinates(self, device_location: Any) -> Optional[Dict[str, Any]]:
        """``{"lat", "lng", "timezone"}`` for the device city, or None."""
        if not isinstance(device_location, dict):
            return None
        city = device_location.get("city")
        if not isinstance(city, str) or not city.strip():
            return None
        city = city.strip()
        state = device_location.get("state") or ""
        country = device_location.get("country") or ""
        key = self._cache_key(city, state, country)

        cached = self._cached(key)
        if cached is not None:
            return cached

        failed_at = self._failed_at.get(key)
        if failed_at is not None and self._clock() - failed_at < FAILURE_RETRY_SECONDS:
            return None

        try:
            coords = self._geocode(city, state, country)
        except Exception as e:
            self._failed_at[key] = self._clock()
            self.logger.warning(
                "Could not geocode device location %r: %s - Starlark apps "
                "without a saved location use their own default", city, e)
            return None
        if not coords:
            self._failed_at[key] = self._clock()
            self.logger.warning(
                "Geocoder found no match for device location %r, %r, %r - "
                "Starlark apps without a saved location use their own default",
                city, state, country)
            return None

        self._failed_at.pop(key, None)
        self._coords[key] = coords
        if self.cache_manager is not None:
            try:
                self.cache_manager.set(key, coords, ttl=COORDS_MAX_AGE)
            except Exception:
                self.logger.debug("Could not cache device coordinates", exc_info=True)
        return coords

    def location_json(self, device_location: Any,
                      device_timezone: Optional[str] = None,
                      saved: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """The device location as a Pixlet location string, or None.

        The timezone is the city's own (from the geocoder) when known, since
        it belongs to the coordinates; the device timezone is the fallback. A
        timezone the user typed into the app's location form (with no lat/lng)
        is kept.
        """
        coords = self.coordinates(device_location)
        if coords is None:
            return None
        city = str(device_location.get("city", "")).strip()
        state = str(device_location.get("state") or "").replace("_", " ").strip()
        country = str(device_location.get("country") or "").strip()
        timezone = ((saved or {}).get("timezone") or coords.get("timezone")
                    or device_timezone or "UTC")
        return json.dumps({
            "lat": f"{float(coords['lat']):.4f}",
            "lng": f"{float(coords['lng']):.4f}",
            "locality": city,
            "description": ", ".join(p for p in (city, state, country) if p),
            "timezone": timezone,
        })


def apply_device_location(pixlet_config: Dict[str, Any],
                          schema: Optional[Dict[str, Any]],
                          resolver: DeviceLocationResolver,
                          device_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fill a Starlark app's unset location fields with the device location.

    Returns a new dict. A usable saved location is left alone. An unset field
    gets the device location, or -- when that can't be resolved -- is dropped,
    so the app sees no value and uses its own default instead of failing to
    decode an empty string. Never raises.
    """
    config = dict(pixlet_config)
    unset = [fid for fid in location_field_ids(schema)
             if parse_location(config.get(fid)) is None]
    if not unset:
        return config

    device_config = device_config if isinstance(device_config, dict) else {}
    for field_id in unset:
        saved = config.get(field_id)
        partial = None
        if isinstance(saved, str) and saved.strip():
            try:
                partial = json.loads(saved)
            except (TypeError, ValueError):
                partial = None
        try:
            value = resolver.location_json(
                device_config.get("location"), device_config.get("timezone"),
                partial if isinstance(partial, dict) else None)
        except Exception:
            logger.warning("Could not build device location for %s", field_id, exc_info=True)
            value = None
        if value is None:
            config.pop(field_id, None)
        else:
            config[field_id] = value
    return config
