"""Card-drawing helpers shared by every sports scoreboard plugin.

The eight scoreboards each carried byte-identical copies of the functions
below: the colour pickers, the settings lookup, the date and time formatting,
the favourite-team rules and the font-size grid snapping. One fix had to be
made eight times, and a new scoreboard started by copying them a ninth.

Everything here is a **free function taking explicit arguments**, not a base
class. Adoption is therefore per-function and reversible: a plugin keeps its
method and delegates the body, so the call sites and the override points are
untouched. That is also why `config`, `logger` and `fonts` are parameters
rather than attributes -- the helper never reaches back into the caller.

The bodies are the plugins' own code, moved rather than rewritten. The one
deliberate difference is `crisp_size`, which takes the seven-plugin guard
(`not desired`) instead of football's: they agree on every real input, and
the extra guard only stops a None size raising TypeError.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

__all__ = [
    "ELEMENT_FOR_FONT", "FAVORITE_RESULT_COLOR_DEFAULTS", "FONT_NAME_ALIASES",
    "FONT_PIXEL_GRID", "MONTH_ABBR", "WEEKDAY_ABBR",
    "scroll_card_option", "element_color", "font_color", "coerce_rgb",
    "score_color_for", "recent_score_color", "favorite_teams_for",
    "side_is_favorite", "side_score", "favorite_result",
    "card_tzinfo", "weekday_for", "format_game_date", "format_game_time",
    "vs_text", "upcoming_center_mode", "crisp_size", "schema_font_size",
    "resolve_font_size", "unshare_element_fonts",
]

#: Which customization element owns each font key, for colour resolution.
ELEMENT_FOR_FONT: Dict[str, str] = {
    "score": "score_text",
    "time": "period_text",
    "team": "team_name",
    "status": "status_text",
    "detail": "detail_text",
    "rank": "rank_text",
}

#: Fallback colours when favourite_result_colors is on but a slot is unset.
FAVORITE_RESULT_COLOR_DEFAULTS: Dict[str, Tuple[int, int, int]] = {
    "win": (0, 255, 0),
    "loss": (255, 0, 0),
    "tie": (255, 200, 0),
}

#: Family aliases the web UI may write, mapped to the shipped filename.
FONT_NAME_ALIASES: Dict[str, str] = {
    "press_start": "PressStart2P-Regular.ttf",
    "four_by_six": "4x6-font.ttf",
}

#: Pixel grid each face renders crisply on. Off-grid sizes anti-alias, which
#: on an LED matrix is a dim lamp rather than a soft edge.
FONT_PIXEL_GRID: Dict[str, int] = {
    "PressStart2P-Regular.ttf": 8,
    "4x6-font.ttf": 7,
}

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------------------
# Settings lookup
# ---------------------------------------------------------------------------

def scroll_card_option(config: Optional[Dict[str, Any]], key: str,
                       default: Any = None) -> Any:
    """Read one key from the scroll_card config block."""
    block = (config or {}).get("scroll_card")
    if isinstance(block, dict) and block.get(key) is not None:
        return block.get(key)
    return default


def vs_text(config: Optional[Dict[str, Any]]) -> str:
    """Separator drawn between the teams -- "VS", "@", "at", anything."""
    return str(scroll_card_option(config, "vs_text", "VS"))


def upcoming_center_mode(config: Optional[Dict[str, Any]]) -> str:
    """Middle of an upcoming card: 'vs', 'date_time' or 'none'."""
    mode = str(scroll_card_option(config, "upcoming_center", "vs") or "vs").lower()
    return mode if mode in ("vs", "date_time", "none") else "vs"


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------

def element_color(config: Optional[Dict[str, Any]], element: str,
                  default: Tuple[int, int, int] = (255, 255, 255)):
    """Per-element text colour from customization.<element>.text_color."""
    try:
        cfg = (config or {}).get("customization", {}).get(element, {})
        value = cfg.get("text_color")
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(max(0, min(255, int(c))) for c in value)
        if isinstance(value, str) and value.startswith("#") and len(value) == 7:
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    except (TypeError, ValueError):
        pass
    return default


def font_color(config: Optional[Dict[str, Any]], fonts: Optional[Dict[str, Any]],
               font, default: Tuple[int, int, int] = (255, 255, 255)):
    """Colour for whichever element owns this face.

    Matched on identity, and deliberately gives up when one object is
    shared: the last-resort font path can hand the same face to several
    keys, and there is no right answer for which element's colour that is.
    White is what those draws used before, so ambiguity costs nothing.
    """
    try:
        fonts = fonts or {}
        matches = [element for key, element in ELEMENT_FOR_FONT.items()
                   if fonts.get(key) is font]
        if len(matches) == 1:
            return element_color(config, matches[0], default)
    except (AttributeError, TypeError):
        pass
    return default


def coerce_rgb(value, fallback):
    """Turn a configured [R, G, B] list into a clamped (r, g, b) tuple."""
    # Checked before unpacking: a 3-character string ("123") would otherwise
    # iterate into three digits and yield a colour rather than the fallback.
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return fallback
    try:
        r, g, b = (max(0, min(255, int(channel))) for channel in value)
    except (TypeError, ValueError):
        return fallback
    return (r, g, b)


# ---------------------------------------------------------------------------
# Favourite teams
# ---------------------------------------------------------------------------

def favorite_teams_for(config: Dict[str, Any], game: Dict[str, Any]) -> list:
    """Favorite teams that apply to this game.

    Both sources are used. Games carry the league manager's *resolved*
    favorites, which is the only place dynamic groups such as AP_TOP_25
    appear expanded; the config is read as well so an edit takes effect on
    already-fetched games, and so hand-built game dicts (tests, other
    callers) still work.
    """
    favorites = list(game.get("favorite_teams") or [])
    league_config = config.get(str(game.get("league", "") or ""))
    if isinstance(league_config, dict):
        favorites += list(league_config.get("favorite_teams") or [])
    else:
        favorites += list(config.get("favorite_teams") or [])
    return favorites


def side_is_favorite(game: Dict[str, Any], side: str, favorites: set) -> bool:
    """Is the home/away side of this game a favorite team?

    Reads both the flat (``home_abbr``) and nested (``home_team.abbrev``)
    payload shapes, and matches on the ESPN id too, because a couple of
    leagues (NRL) key favorites by id where abbreviations collide.
    """
    candidates = [game.get(f"{side}_abbr"), game.get(f"{side}_id")]
    team = game.get(f"{side}_team")
    if isinstance(team, dict):
        candidates += [team.get("abbrev"), team.get("abbreviation"), team.get("id")]
    for value in candidates:
        if value is not None and str(value).strip().upper() in favorites:
            return True
    return False


def side_score(game: Dict[str, Any], side: str) -> Optional[int]:
    """Numeric score for one side, from either payload shape."""
    raw = None
    team = game.get(f"{side}_team")
    if isinstance(team, dict) and team.get("score") is not None:
        raw = team.get("score")
    if raw is None:
        raw = game.get(f"{side}_score")
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def favorite_result(config: Dict[str, Any], game: Dict[str, Any]) -> Optional[str]:
    """Say how the favorite team did in a finished game.

    Returns 'win', 'loss' or 'tie', or None when there is no single team
    to root for: no favorites configured, neither side is a favorite, or
    *both* are -- a favorite-vs-favorite game has no losing side worth
    flagging in red. Also None when the scores are not usable numbers.
    """
    favorites = {
        str(team).strip().upper()
        for team in favorite_teams_for(config, game)
        if str(team).strip()
    }
    if not favorites:
        return None

    home_fav = side_is_favorite(game, "home", favorites)
    away_fav = side_is_favorite(game, "away", favorites)
    if home_fav == away_fav:
        return None

    home_score = side_score(game, "home")
    away_score = side_score(game, "away")
    if home_score is None or away_score is None:
        return None

    if home_score == away_score:
        return "tie"
    favorite_score, other_score = (
        (home_score, away_score) if home_fav else (away_score, home_score)
    )
    return "win" if favorite_score > other_score else "loss"


def recent_score_color(config: Dict[str, Any], logger, game: Dict[str, Any], default):
    """Fill color for a finished game's score, per favorite_result_colors."""
    try:
        settings = (config.get("customization") or {}).get(
            "favorite_result_colors"
        ) or {}
        if not settings.get("enabled", False):
            return default
        result = favorite_result(config, game)
        if result is None:
            return default
        return coerce_rgb(
            settings.get(f"{result}_color"),
            FAVORITE_RESULT_COLOR_DEFAULTS[result],
        )
    except Exception:
        logger.debug("Could not resolve favorite result color", exc_info=True)
        return default


def score_color_for(config: Dict[str, Any], logger, game: Dict[str, Any],
                    game_type: str, default=None):
    """Fill color for a game card's score. Only finished games are tinted.

    The default is the configured score colour rather than a flat white,
    so customization.score_text.text_color shows on games the favourite
    tint does not apply to. The tint still wins where it applies.
    """
    if default is None:
        default = element_color(config, 'score_text')
    if game_type != "recent":
        return default
    return recent_score_color(config, logger, game, default)


# ---------------------------------------------------------------------------
# Date and time
# ---------------------------------------------------------------------------

def card_tzinfo(config: Optional[Dict[str, Any]], logger):
    """Timezone for weekday/24h conversions; falls back to UTC."""
    configured = (config or {}).get("timezone")
    if configured:
        try:
            return ZoneInfo(configured)
        except (KeyError, ValueError, TypeError, OSError) as exc:
            # KeyError covers ZoneInfoNotFoundError. A bad zone name in
            # config should fall back to UTC, not blank the card.
            logger.debug("Unusable timezone %r: %s", configured, exc)
    return timezone.utc


def weekday_for(config: Optional[Dict[str, Any]], logger,
                game: Optional[Dict]) -> str:
    """Weekday abbreviation from the game's start time, or ''."""
    if not game:
        return ""
    raw = game.get("start_time_utc") or game.get("start_time")
    if not raw:
        return ""
    try:
        start = raw if isinstance(raw, datetime) else datetime.fromisoformat(
            str(raw).replace("Z", "+00:00"))
        return WEEKDAY_ABBR[start.astimezone(card_tzinfo(config, logger)).weekday()]
    except (ValueError, TypeError):
        return ""


def format_game_date(config: Optional[Dict[str, Any]], logger, date_text: str,
                     game: Optional[Dict] = None) -> str:
    """Format an upcoming card's date per scroll_card.date_format."""
    raw = str(date_text or "").strip()
    if not raw:
        return ""
    fmt = str(scroll_card_option(config, "date_format", "abbrev") or "abbrev")
    if fmt == "numeric":
        return raw
    parts = raw.replace("-", "/").split("/")
    if not (len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit()):
        return raw
    month, day = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        return raw
    name = MONTH_ABBR[month - 1]
    if fmt == "numeric_day_first":
        return f"{day}/{month}"
    if fmt == "day_first":
        return f"{day} {name}"
    if fmt == "weekday":
        weekday = weekday_for(config, logger, game)
        return f"{weekday} {name} {day}" if weekday else f"{name} {day}"
    return f"{name} {day}"


def format_game_time(config: Optional[Dict[str, Any]], time_text: str) -> str:
    """Return the time as-is (12h) or converted to 24h."""
    raw = str(time_text or "").strip()
    if not raw or str(scroll_card_option(config, "time_format", "12h")) != "24h":
        return raw
    cleaned = raw.upper().replace(" ", "")
    meridiem = "AM" if cleaned.endswith("AM") else "PM" if cleaned.endswith("PM") else ""
    if not meridiem:
        return raw
    try:
        hh, _, mm = cleaned[:-2].partition(":")
        hour, minute = int(hh), int(mm or 0)
    except ValueError:
        return raw
    if not (0 <= hour <= 12 and 0 <= minute <= 59):
        return raw
    hour = hour % 12 + (12 if meridiem == "PM" else 0)
    return f"{hour:02d}:{minute:02d}"


# ---------------------------------------------------------------------------
# Font sizing
# ---------------------------------------------------------------------------

#: Per-schema caches, keyed by the schema's absolute path. Keyed rather than
#: global because each plugin declares its own defaults; keyed rather than
#: per-class because the helper has no class to hang it on.
_SCHEMA_FONT_SIZE_CACHE: Dict[str, Dict[str, int]] = {}


def crisp_size(font_file, desired, aliases=None, grid_table=None):
    """Snap *desired* to the nearest size *font_file* renders crisply at.

    A face with no known grid is returned unchanged, so a user-supplied
    font is never second-guessed.

    ``aliases`` and ``grid_table`` default to the shared tables; a plugin
    that ships an extra face can pass its own without forking this.
    """
    aliases = FONT_NAME_ALIASES if aliases is None else aliases
    grid_table = FONT_PIXEL_GRID if grid_table is None else grid_table
    font_file = aliases.get(font_file, font_file)
    grid = grid_table.get(font_file)
    if not grid or not desired or desired <= 0:
        return desired
    return max(grid, int(round(float(desired) / grid)) * grid)


def schema_font_size(schema_path: str, element_key) -> Optional[int]:
    """The font_size this plugin's config_schema.json declares, or None.

    Cached per schema path. The plugins cached this on their own class; the
    path is the same distinction expressed without one, so two plugins never
    share an entry.
    """
    if not element_key:
        return None
    cache = _SCHEMA_FONT_SIZE_CACHE.get(schema_path)
    if cache is None:
        cache = {}
        try:
            import json
            with open(schema_path) as fh:
                schema = json.load(fh)
            props = (schema.get('properties', {})
                           .get('customization', {})
                           .get('properties', {}))
            for key, spec in props.items():
                size = spec.get('properties', {}).get('font_size', {}).get('default')
                if size is not None:
                    cache[key] = int(size)
        except Exception as exc:
            # See sports_shared._schema_font_size: an unreadable schema
            # silently disables the pixel-grid snap for every element.
            # Built once per schema path, so this cannot repeat per frame.
            logger.warning(
                "could not read %s (%s: %s); font sizes will skip their "
                "pixel grid snap and may render a pixel narrow",
                schema_path, type(exc).__name__, exc)
            cache = {}
        _SCHEMA_FONT_SIZE_CACHE[schema_path] = cache
    return cache.get(element_key)


def resolve_font_size(schema_path: str, element_config, element_key,
                      default_size, font_name, aliases=None, grid_table=None):
    """Size to render at: the user's choice, or a grid-snapped default.

    A configured size counts as a real choice only when it differs from
    the schema default. The web UI writes the whole schema default block
    on every save, so "font_size == schema default" carries no intent and
    would otherwise pin every install to an anti-aliased size forever.
    """
    configured = (element_config or {}).get('font_size')
    if configured is not None:
        try:
            configured = int(configured)
            if configured != schema_font_size(schema_path, element_key):
                return configured
        except (TypeError, ValueError):
            pass
    return crisp_size(font_name, default_size, aliases, grid_table)


def unshare_element_fonts(logger, fonts):
    """Give each colourable element its own face object.

    The colour a draw gets is resolved from the face it was handed, and
    several of these loaders legitimately hand one object to more than one
    element -- a size resolver that lands two elements on the same face, a
    fallback that fills every key from one default, football's narrowing
    step that deliberately shrinks the clock along with the score. Sharing
    the object makes the element ambiguous and the colour unresolvable.

    Re-instantiating from the same path and size gives a distinct object
    with identical metrics, so nothing about the rendering changes; only
    the ability to tell two elements apart does. Faces that cannot be
    rebuilt (a BDF loaded through freetype.Face, anything without a usable
    path) are left shared, and their draws stay white as before.
    """
    try:
        from PIL import ImageFont as _IF
    except ImportError:  # pragma: no cover
        return fonts
    seen = {}
    for key in ELEMENT_FOR_FONT:
        font = fonts.get(key)
        if font is None:
            continue
        if id(font) not in seen:
            seen[id(font)] = key
            continue
        path, size = getattr(font, "path", None), getattr(font, "size", None)
        if not path or not size:
            continue
        try:
            fonts[key] = _IF.truetype(path, size)
        except (OSError, ValueError, TypeError):
            logger.debug(
                "Could not un-share the %s face; it keeps the default colour", key)
    return fonts
