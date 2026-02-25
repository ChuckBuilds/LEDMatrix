"""March Madness Plugin — NCAA Tournament bracket tracker for LED Matrix.

Displays a horizontally-scrolling ticker of NCAA Tournament games grouped by
round, with seeds, round logos, live scores, and upset highlighting.
"""

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytz
import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.plugin_system.base_plugin import BasePlugin

try:
    from src.common.scroll_helper import ScrollHelper
except ImportError:
    ScrollHelper = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOREBOARD_URLS = {
    "ncaam": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "ncaaw": "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard",
}

ROUND_ORDER = {"NCG": 0, "F4": 1, "E8": 2, "S16": 3, "R32": 4, "R64": 5, "": 6}

ROUND_DISPLAY_NAMES = {
    "NCG": "Championship",
    "F4": "Final Four",
    "E8": "Elite Eight",
    "S16": "Sweet Sixteen",
    "R32": "Round of 32",
    "R64": "Round of 64",
}

ROUND_LOGO_FILES = {
    "NCG": "CHAMPIONSHIP.png",
    "F4": "FINAL_4.png",
    "E8": "ELITE_8.png",
    "S16": "SWEET_16.png",
    "R32": "ROUND_32.png",
    "R64": "ROUND_64.png",
}

REGION_ORDER = {"E": 0, "W": 1, "S": 2, "MW": 3, "": 4}

# Colors
COLOR_WHITE = (255, 255, 255)
COLOR_GOLD = (255, 215, 0)
COLOR_GRAY = (160, 160, 160)
COLOR_DIM = (100, 100, 100)
COLOR_RED = (255, 60, 60)
COLOR_GREEN = (60, 200, 60)
COLOR_BLACK = (0, 0, 0)
COLOR_DARK_BG = (20, 20, 20)


# ---------------------------------------------------------------------------
# Plugin Class
# ---------------------------------------------------------------------------

class MarchMadnessPlugin(BasePlugin):
    """NCAA March Madness tournament bracket tracker."""

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any,
    ):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        # Config
        leagues_config = config.get("leagues", {})
        self.show_ncaam: bool = leagues_config.get("ncaam", True)
        self.show_ncaaw: bool = leagues_config.get("ncaaw", True)
        self.favorite_teams: List[str] = [t.upper() for t in config.get("favorite_teams", [])]

        display_options = config.get("display_options", {})
        self.show_seeds: bool = display_options.get("show_seeds", True)
        self.show_round_logos: bool = display_options.get("show_round_logos", True)
        self.highlight_upsets: bool = display_options.get("highlight_upsets", True)
        self.show_bracket_progress: bool = display_options.get("show_bracket_progress", True)
        self.scroll_speed: float = display_options.get("scroll_speed", 1.0)
        self.scroll_delay: float = display_options.get("scroll_delay", 0.02)
        self.target_fps: int = display_options.get("target_fps", 120)
        self.loop: bool = display_options.get("loop", True)
        self.dynamic_duration_enabled: bool = display_options.get("dynamic_duration", True)
        self.min_duration: int = display_options.get("min_duration", 30)
        self.max_duration: int = display_options.get("max_duration", 300)

        data_settings = config.get("data_settings", {})
        self.update_interval: int = data_settings.get("update_interval", 300)
        self.request_timeout: int = data_settings.get("request_timeout", 30)

        # Scrolling flag for display controller
        self.enable_scrolling = True

        # State
        self.games_data: List[Dict] = []
        self.ticker_image: Optional[Image.Image] = None
        self.last_update: float = 0
        self.dynamic_duration: float = 60
        self.total_scroll_width: int = 0
        self._display_start_time: Optional[float] = None
        self._end_reached_logged: bool = False
        self._update_lock = threading.Lock()
        self._has_live_games: bool = False
        self._cached_dynamic_duration: Optional[float] = None
        self._duration_cache_time: float = 0

        # Display dimensions
        self.display_width: int = self.display_manager.matrix.width
        self.display_height: int = self.display_manager.matrix.height

        # HTTP session with retry
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.headers = {"User-Agent": "LEDMatrix/2.0"}

        # ScrollHelper
        if ScrollHelper:
            self.scroll_helper = ScrollHelper(self.display_width, self.display_height, logger=self.logger)
            if hasattr(self.scroll_helper, "set_frame_based_scrolling"):
                self.scroll_helper.set_frame_based_scrolling(True)
            self.scroll_helper.set_scroll_speed(self.scroll_speed)
            self.scroll_helper.set_scroll_delay(self.scroll_delay)
            if hasattr(self.scroll_helper, "set_target_fps"):
                self.scroll_helper.set_target_fps(self.target_fps)
            self.scroll_helper.set_dynamic_duration_settings(
                enabled=self.dynamic_duration_enabled,
                min_duration=self.min_duration,
                max_duration=self.max_duration,
                buffer=0.1,
            )
        else:
            self.scroll_helper = None
            self.logger.warning("ScrollHelper not available")

        # Fonts
        self.fonts = self._load_fonts()

        # Logos
        self._round_logos: Dict[str, Image.Image] = {}
        self._team_logo_cache: Dict[str, Optional[Image.Image]] = {}
        self._march_madness_logo: Optional[Image.Image] = None
        self._load_round_logos()

        self.logger.info(
            f"MarchMadnessPlugin initialized — NCAAM: {self.show_ncaam}, "
            f"NCAAW: {self.show_ncaaw}, favorites: {self.favorite_teams}"
        )

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------

    def _load_fonts(self) -> Dict[str, ImageFont.FreeTypeFont]:
        fonts = {}
        try:
            fonts["score"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
        except IOError:
            fonts["score"] = ImageFont.load_default()
        try:
            fonts["time"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
        except IOError:
            fonts["time"] = ImageFont.load_default()
        try:
            fonts["detail"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
        except IOError:
            fonts["detail"] = ImageFont.load_default()
        return fonts

    # ------------------------------------------------------------------
    # Logo loading
    # ------------------------------------------------------------------

    def _load_round_logos(self) -> None:
        logo_dir = Path("assets/sports/ncaa_logos")
        for round_key, filename in ROUND_LOGO_FILES.items():
            path = logo_dir / filename
            try:
                img = Image.open(path).convert("RGBA")
                # Resize to fit display height
                target_h = self.display_height - 4
                ratio = target_h / img.height
                target_w = int(img.width * ratio)
                self._round_logos[round_key] = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            except (OSError, ValueError) as e:
                self.logger.warning(f"Could not load round logo {filename}: {e}")
            except Exception as e:
                self.logger.exception(f"Unexpected error loading round logo {filename}: {e}")

        # March Madness logo
        mm_path = logo_dir / "MARCH_MADNESS.png"
        try:
            img = Image.open(mm_path).convert("RGBA")
            target_h = self.display_height - 4
            ratio = target_h / img.height
            target_w = int(img.width * ratio)
            self._march_madness_logo = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        except (OSError, ValueError) as e:
            self.logger.warning(f"Could not load March Madness logo: {e}")
        except Exception as e:
            self.logger.exception(f"Unexpected error loading March Madness logo: {e}")

    def _get_team_logo(self, abbr: str) -> Optional[Image.Image]:
        if abbr in self._team_logo_cache:
            return self._team_logo_cache[abbr]
        logo_dir = Path("assets/sports/ncaa_logos")
        path = logo_dir / f"{abbr}.png"
        try:
            img = Image.open(path).convert("RGBA")
            target_h = self.display_height - 6
            ratio = target_h / img.height
            target_w = int(img.width * ratio)
            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            self._team_logo_cache[abbr] = img
            return img
        except Exception:
            self._team_logo_cache[abbr] = None
            return None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _is_tournament_window(self) -> bool:
        today = datetime.now(pytz.utc)
        return (3, 10) <= (today.month, today.day) <= (4, 10)

    def _fetch_tournament_data(self) -> List[Dict]:
        """Fetch tournament games from ESPN scoreboard API."""
        all_games: List[Dict] = []

        leagues = []
        if self.show_ncaam:
            leagues.append("ncaam")
        if self.show_ncaaw:
            leagues.append("ncaaw")

        for league_key in leagues:
            url = SCOREBOARD_URLS.get(league_key)
            if not url:
                continue

            cache_key = f"march_madness_{league_key}_scoreboard"
            cache_max_age = 60 if self._has_live_games else self.update_interval
            cached = self.cache_manager.get(cache_key, max_age=cache_max_age)
            if cached:
                all_games.extend(cached)
                continue

            try:
                # NCAA basketball scoreboard without dates param returns current games
                params = {"limit": 1000, "groups": 100}
                resp = self.session.get(url, params=params, headers=self.headers, timeout=self.request_timeout)
                resp.raise_for_status()
                data = resp.json()
                events = data.get("events", [])

                league_games = []
                for event in events:
                    game = self._parse_event(event, league_key)
                    if game:
                        league_games.append(game)

                self.cache_manager.set(cache_key, league_games)
                self.logger.info(f"Fetched {len(league_games)} {league_key} tournament games")
                all_games.extend(league_games)

            except Exception as e:
                self.logger.error(f"Error fetching {league_key} tournament data: {e}")

        return all_games

    def _parse_event(self, event: Dict, league_key: str) -> Optional[Dict]:
        """Parse an ESPN event into a game dict."""
        competitions = event.get("competitions", [])
        if not competitions:
            return None
        comp = competitions[0]

        # Confirm tournament game
        comp_type = comp.get("type", {})
        is_tournament = comp_type.get("abbreviation") == "TRNMNT"
        notes = comp.get("notes", [])
        headline = ""
        if notes:
            headline = notes[0].get("headline", "")
            if not is_tournament and "Championship" in headline:
                is_tournament = True
        if not is_tournament:
            return None

        # Status
        status = comp.get("status", {}).get("type", {})
        state = status.get("state", "pre")
        status_detail = status.get("shortDetail", "")

        # Teams
        competitors = comp.get("competitors", [])
        home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home_team or not away_team:
            return None

        home_abbr = home_team.get("team", {}).get("abbreviation", "???")
        away_abbr = away_team.get("team", {}).get("abbreviation", "???")
        home_score = home_team.get("score", "0")
        away_score = away_team.get("score", "0")

        # Seeds
        home_seed = home_team.get("curatedRank", {}).get("current", 0)
        away_seed = away_team.get("curatedRank", {}).get("current", 0)
        if home_seed >= 99:
            home_seed = 0
        if away_seed >= 99:
            away_seed = 0

        # Round and region
        tournament_round = self._parse_round(headline)
        tournament_region = self._parse_region(headline)

        # Date/time
        date_str = event.get("date", "")
        start_time_utc = None
        game_date = ""
        game_time = ""
        try:
            if date_str.endswith("Z"):
                date_str = date_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                start_time_utc = dt.replace(tzinfo=pytz.UTC)
            else:
                start_time_utc = dt.astimezone(pytz.UTC)
            local = start_time_utc.astimezone(pytz.timezone("US/Eastern"))
            game_date = local.strftime("%-m/%-d")
            game_time = local.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")
        except (ValueError, AttributeError):
            pass

        # Period / clock for live games
        period = 0
        clock = ""
        period_text = ""
        is_halftime = False
        if state == "in":
            status_obj = comp.get("status", {})
            period = status_obj.get("period", 0)
            clock = status_obj.get("displayClock", "")
            detail_lower = status_detail.lower()
            uses_quarters = league_key == "ncaaw" or "quarter" in detail_lower or detail_lower.startswith("q")
            if period <= (4 if uses_quarters else 2):
                period_text = f"Q{period}" if uses_quarters else f"H{period}"
            else:
                ot_num = period - (4 if uses_quarters else 2)
                period_text = f"OT{ot_num}" if ot_num > 1 else "OT"
            if "halftime" in detail_lower:
                is_halftime = True
        elif state == "post":
            period_text = status.get("shortDetail", "Final")
            if "Final" not in period_text:
                period_text = "Final"

        # Determine winner and upset
        is_final = state == "post"
        is_upset = False
        winner_side = ""
        if is_final:
            try:
                h = int(float(home_score))
                a = int(float(away_score))
                if h > a:
                    winner_side = "home"
                    if home_seed > away_seed > 0:
                        is_upset = True
                elif a > h:
                    winner_side = "away"
                    if away_seed > home_seed > 0:
                        is_upset = True
            except (ValueError, TypeError):
                pass

        return {
            "id": event.get("id", ""),
            "league": league_key,
            "home_abbr": home_abbr,
            "away_abbr": away_abbr,
            "home_score": str(home_score),
            "away_score": str(away_score),
            "home_seed": home_seed,
            "away_seed": away_seed,
            "tournament_round": tournament_round,
            "tournament_region": tournament_region,
            "state": state,
            "is_final": is_final,
            "is_live": state == "in",
            "is_upcoming": state == "pre",
            "is_halftime": is_halftime,
            "period": period,
            "period_text": period_text,
            "clock": clock,
            "status_detail": status_detail,
            "game_date": game_date,
            "game_time": game_time,
            "start_time_utc": start_time_utc,
            "is_upset": is_upset,
            "winner_side": winner_side,
            "headline": headline,
        }

    @staticmethod
    def _parse_round(headline: str) -> str:
        hl = headline.lower()
        if "national championship" in hl:
            return "NCG"
        if "final four" in hl:
            return "F4"
        if "elite 8" in hl or "elite eight" in hl:
            return "E8"
        if "sweet 16" in hl or "sweet sixteen" in hl:
            return "S16"
        if "2nd round" in hl or "second round" in hl:
            return "R32"
        if "1st round" in hl or "first round" in hl:
            return "R64"
        return ""

    @staticmethod
    def _parse_region(headline: str) -> str:
        if "East Region" in headline:
            return "E"
        if "West Region" in headline:
            return "W"
        if "South Region" in headline:
            return "S"
        if "Midwest Region" in headline:
            return "MW"
        m = re.search(r"Regional (\d+)", headline)
        if m:
            return f"R{m.group(1)}"
        return ""

    # ------------------------------------------------------------------
    # Game processing
    # ------------------------------------------------------------------

    def _process_games(self, games: List[Dict]) -> Dict[str, List[Dict]]:
        """Group games by round, sorted by round significance then region/seed."""
        grouped: Dict[str, List[Dict]] = {}
        for game in games:
            rnd = game.get("tournament_round", "")
            grouped.setdefault(rnd, []).append(game)

        # Sort each round's games by region then seed matchup
        for rnd, round_games in grouped.items():
            round_games.sort(
                key=lambda g: (
                    REGION_ORDER.get(g.get("tournament_region", ""), 4),
                    min(g.get("away_seed", 99), g.get("home_seed", 99)),
                )
            )

        return grouped

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _draw_text_with_outline(
        self,
        draw: ImageDraw.Draw,
        text: str,
        xy: tuple,
        font: ImageFont.FreeTypeFont,
        fill: tuple = COLOR_WHITE,
        outline: tuple = COLOR_BLACK,
    ) -> None:
        x, y = xy
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    draw.text((x + dx, y + dy), text, font=font, fill=outline)
        draw.text((x, y), text, font=font, fill=fill)

    def _create_round_separator(self, round_key: str) -> Image.Image:
        """Create a separator tile for a tournament round."""
        height = self.display_height
        name = ROUND_DISPLAY_NAMES.get(round_key, round_key)
        font = self.fonts["time"]

        # Measure text
        tmp = Image.new("RGB", (1, 1))
        tmp_draw = ImageDraw.Draw(tmp)
        text_width = int(tmp_draw.textlength(name, font=font))

        # Logo on each side
        logo = self._round_logos.get(round_key, self._march_madness_logo)
        logo_w = logo.width if logo else 0
        padding = 6

        total_w = padding + logo_w + padding + text_width + padding + logo_w + padding
        total_w = max(total_w, 80)

        img = Image.new("RGB", (total_w, height), COLOR_DARK_BG)
        draw = ImageDraw.Draw(img)

        # Draw logos
        x = padding
        if logo:
            logo_y = (height - logo.height) // 2
            img.paste(logo, (x, logo_y), logo)
            x += logo_w + padding

        # Draw round name
        text_y = (height - 8) // 2  # 8px font
        self._draw_text_with_outline(draw, name, (x, text_y), font, fill=COLOR_GOLD)
        x += text_width + padding

        if logo:
            logo_y = (height - logo.height) // 2
            img.paste(logo, (x, logo_y), logo)

        return img

    def _create_game_tile(self, game: Dict) -> Image.Image:
        """Create a single game tile for the scrolling ticker."""
        height = self.display_height
        font_score = self.fonts["score"]
        font_time = self.fonts["time"]
        font_detail = self.fonts["detail"]

        # Load team logos
        away_logo = self._get_team_logo(game["away_abbr"])
        home_logo = self._get_team_logo(game["home_abbr"])
        logo_w = 0
        if away_logo:
            logo_w = max(logo_w, away_logo.width)
        if home_logo:
            logo_w = max(logo_w, home_logo.width)
        if logo_w == 0:
            logo_w = 24

        # Build text elements
        away_seed_str = f"({game['away_seed']})" if self.show_seeds and game.get("away_seed", 0) > 0 else ""
        home_seed_str = f"({game['home_seed']})" if self.show_seeds and game.get("home_seed", 0) > 0 else ""
        away_text = f"{away_seed_str}{game['away_abbr']}"
        home_text = f"{game['home_abbr']}{home_seed_str}"

        # Measure text widths
        tmp = Image.new("RGB", (1, 1))
        tmp_draw = ImageDraw.Draw(tmp)
        away_text_w = int(tmp_draw.textlength(away_text, font=font_detail))
        home_text_w = int(tmp_draw.textlength(home_text, font=font_detail))

        # Center content: status line
        if game["is_live"]:
            if game["is_halftime"]:
                status_text = "Halftime"
            else:
                status_text = f"{game['period_text']} {game['clock']}".strip()
        elif game["is_final"]:
            status_text = game.get("period_text", "Final")
        else:
            status_text = f"{game['game_date']} {game['game_time']}".strip()

        status_w = int(tmp_draw.textlength(status_text, font=font_time))

        # Score line (for live/final)
        score_text = ""
        if game["is_live"] or game["is_final"]:
            score_text = f"{game['away_score']}-{game['home_score']}"
        score_w = int(tmp_draw.textlength(score_text, font=font_score)) if score_text else 0

        # Calculate tile width
        h_pad = 4
        center_w = max(status_w, score_w, 40)
        tile_w = h_pad + logo_w + h_pad + away_text_w + h_pad + center_w + h_pad + home_text_w + h_pad + logo_w + h_pad

        img = Image.new("RGB", (tile_w, height), COLOR_BLACK)
        draw = ImageDraw.Draw(img)

        # Paste away logo
        x = h_pad
        if away_logo:
            logo_y = (height - away_logo.height) // 2
            img.paste(away_logo, (x, logo_y), away_logo)
        x += logo_w + h_pad

        # Away team text (seed + abbr)
        is_fav_away = game["away_abbr"] in self.favorite_teams if self.favorite_teams else False
        away_color = COLOR_GOLD if is_fav_away else COLOR_WHITE
        if game["is_final"] and game["winner_side"] == "away" and self.highlight_upsets and game["is_upset"]:
            away_color = COLOR_GOLD
        team_text_y = (height - 6) // 2 - 5  # Upper half
        self._draw_text_with_outline(draw, away_text, (x, team_text_y), font_detail, fill=away_color)
        x += away_text_w + h_pad

        # Center block
        center_x = x
        center_mid = center_x + center_w // 2

        # Status text (top center of center block)
        status_x = center_mid - status_w // 2
        status_y = 2
        status_color = COLOR_GREEN if game["is_live"] else COLOR_GRAY
        self._draw_text_with_outline(draw, status_text, (status_x, status_y), font_time, fill=status_color)

        # Score (bottom center of center block, for live/final)
        if score_text:
            score_x = center_mid - score_w // 2
            score_y = height - 13
            # Upset highlighting
            if game["is_final"] and game["is_upset"] and self.highlight_upsets:
                score_color = COLOR_GOLD
            elif game["is_live"]:
                score_color = COLOR_WHITE
            else:
                score_color = COLOR_WHITE
            self._draw_text_with_outline(draw, score_text, (score_x, score_y), font_score, fill=score_color)

        # Date for final games (below score)
        if game["is_final"] and game.get("game_date"):
            date_w = int(draw.textlength(game["game_date"], font=font_detail))
            date_x = center_mid - date_w // 2
            date_y = height - 6
            self._draw_text_with_outline(draw, game["game_date"], (date_x, date_y), font_detail, fill=COLOR_DIM)

        x = center_x + center_w + h_pad

        # Home team text
        is_fav_home = game["home_abbr"] in self.favorite_teams if self.favorite_teams else False
        home_color = COLOR_GOLD if is_fav_home else COLOR_WHITE
        if game["is_final"] and game["winner_side"] == "home" and self.highlight_upsets and game["is_upset"]:
            home_color = COLOR_GOLD
        self._draw_text_with_outline(draw, home_text, (x, team_text_y), font_detail, fill=home_color)
        x += home_text_w + h_pad

        # Paste home logo
        if home_logo:
            logo_y = (height - home_logo.height) // 2
            img.paste(home_logo, (x, logo_y), home_logo)

        return img

    def _create_ticker_image(self) -> None:
        """Build the full scrolling ticker image from game tiles."""
        if not self.games_data:
            self.ticker_image = None
            if self.scroll_helper:
                self.scroll_helper.clear_cache()
            return

        grouped = self._process_games(self.games_data)
        content_items: List[Image.Image] = []

        # Order rounds by significance (most important first)
        sorted_rounds = sorted(grouped.keys(), key=lambda r: ROUND_ORDER.get(r, 6))

        for rnd in sorted_rounds:
            games = grouped[rnd]
            if not games:
                continue

            # Add round separator
            if self.show_round_logos and rnd:
                separator = self._create_round_separator(rnd)
                content_items.append(separator)

            # Add game tiles
            for game in games:
                tile = self._create_game_tile(game)
                content_items.append(tile)

        if not content_items:
            self.ticker_image = None
            if self.scroll_helper:
                self.scroll_helper.clear_cache()
            return

        gap_width = 16

        # Use ScrollHelper to create the scrolling image
        self.ticker_image = self.scroll_helper.create_scrolling_image(
            content_items=content_items,
            item_gap=gap_width,
            element_gap=0,
        )

        # Update cached arrays
        self.scroll_helper.cached_image = self.ticker_image
        self.scroll_helper.cached_array = np.array(self.ticker_image)

        self.total_scroll_width = self.scroll_helper.total_scroll_width
        self.dynamic_duration = self.scroll_helper.get_dynamic_duration()

        self.logger.info(
            f"Ticker image created: {self.ticker_image.width}px wide, "
            f"{len(self.games_data)} games, dynamic_duration={self.dynamic_duration:.0f}s"
        )

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Fetch and process tournament data."""
        if not self.enabled:
            return

        current_time = time.time()
        # Use shorter interval if live games detected
        interval = 60 if self._has_live_games else self.update_interval
        if current_time - self.last_update < interval:
            return

        with self._update_lock:
            self.last_update = current_time

            if not self._is_tournament_window():
                self.logger.debug("Outside tournament window, skipping fetch")
                self.games_data = []
                self.ticker_image = None
                if self.scroll_helper:
                    self.scroll_helper.clear_cache()
                return

            try:
                games = self._fetch_tournament_data()
                self._has_live_games = any(g["is_live"] for g in games)
                self.games_data = games
                self._create_ticker_image()
                self.logger.info(
                    f"Updated: {len(games)} games, "
                    f"live={self._has_live_games}"
                )
            except Exception as e:
                self.logger.error(f"Update error: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """Render one scroll frame."""
        if not self.enabled:
            return

        # Check for live update
        current_time = time.time()
        interval = 60 if self._has_live_games else self.update_interval
        if current_time - self.last_update >= interval:
            with self._update_lock:
                self.last_update = current_time
                try:
                    games = self._fetch_tournament_data()
                    self._has_live_games = any(g["is_live"] for g in games)
                    self.games_data = games
                    # Preserve scroll position during live updates
                    old_pos = self.scroll_helper.scroll_position if self.scroll_helper else 0
                    self._create_ticker_image()
                    if self.scroll_helper and self.ticker_image:
                        self.scroll_helper.scroll_position = min(old_pos, self.ticker_image.width - 1)
                except Exception as e:
                    self.logger.error(f"Live update error: {e}", exc_info=True)

        if force_clear or self._display_start_time is None:
            self._display_start_time = time.time()
            if self.scroll_helper:
                self.scroll_helper.reset_scroll()
            self._end_reached_logged = False

        if not self.games_data or self.ticker_image is None:
            self._display_fallback()
            return

        if not self.scroll_helper:
            self._display_fallback()
            return

        try:
            if self.loop or not self.scroll_helper.is_scroll_complete():
                self.scroll_helper.update_scroll_position()
            elif not self._end_reached_logged:
                self.logger.info("Scroll complete")
                self._end_reached_logged = True

            visible = self.scroll_helper.get_visible_portion()
            if visible is None:
                self._display_fallback()
                return

            self.dynamic_duration = self.scroll_helper.get_dynamic_duration()

            matrix_w = self.display_manager.matrix.width
            matrix_h = self.display_manager.matrix.height
            if not hasattr(self.display_manager, "image") or self.display_manager.image is None:
                self.display_manager.image = Image.new("RGB", (matrix_w, matrix_h), COLOR_BLACK)
            self.display_manager.image.paste(visible, (0, 0))
            self.display_manager.update_display()
            self.scroll_helper.log_frame_rate()

        except Exception as e:
            self.logger.error(f"Display error: {e}", exc_info=True)
            self._display_fallback()

    def _display_fallback(self) -> None:
        w = self.display_manager.matrix.width
        h = self.display_manager.matrix.height
        img = Image.new("RGB", (w, h), COLOR_BLACK)
        draw = ImageDraw.Draw(img)

        if self._is_tournament_window():
            text = "No games"
        else:
            text = "Off-season"

        text_w = int(draw.textlength(text, font=self.fonts["time"]))
        text_x = (w - text_w) // 2
        text_y = (h - 8) // 2
        draw.text((text_x, text_y), text, font=self.fonts["time"], fill=COLOR_GRAY)

        # Show March Madness logo if available
        if self._march_madness_logo:
            logo_y = (h - self._march_madness_logo.height) // 2
            img.paste(self._march_madness_logo, (2, logo_y), self._march_madness_logo)

        self.display_manager.image = img
        self.display_manager.update_display()

    # ------------------------------------------------------------------
    # Duration / cycle management
    # ------------------------------------------------------------------

    def get_display_duration(self) -> float:
        current_time = time.time()
        if self._cached_dynamic_duration is not None:
            cache_age = current_time - self._duration_cache_time
            if cache_age < 5.0:
                return self._cached_dynamic_duration

        self._cached_dynamic_duration = self.dynamic_duration
        self._duration_cache_time = current_time
        return self.dynamic_duration

    def supports_dynamic_duration(self) -> bool:
        if not self.enabled:
            return False
        return self.dynamic_duration_enabled

    def is_cycle_complete(self) -> bool:
        if not self.supports_dynamic_duration():
            return True
        if self._display_start_time is not None and self.dynamic_duration > 0:
            elapsed = time.time() - self._display_start_time
            if elapsed >= self.dynamic_duration:
                return True
        if not self.loop and self.scroll_helper and self.scroll_helper.is_scroll_complete():
            return True
        return False

    def reset_cycle_state(self) -> None:
        super().reset_cycle_state()
        self._display_start_time = None
        self._end_reached_logged = False
        if self.scroll_helper:
            self.scroll_helper.reset_scroll()

    # ------------------------------------------------------------------
    # Vegas mode
    # ------------------------------------------------------------------

    def get_vegas_content(self):
        if not self.games_data:
            return None
        tiles = []
        for game in self.games_data:
            tiles.append(self._create_game_tile(game))
        return tiles if tiles else None

    def get_vegas_content_type(self) -> str:
        return "multi"

    # ------------------------------------------------------------------
    # Info / cleanup
    # ------------------------------------------------------------------

    def get_info(self) -> Dict:
        info = super().get_info()
        info["total_games"] = len(self.games_data)
        info["has_live_games"] = self._has_live_games
        info["dynamic_duration"] = self.dynamic_duration
        info["tournament_window"] = self._is_tournament_window()
        return info

    def cleanup(self) -> None:
        self.games_data = []
        self.ticker_image = None
        if self.scroll_helper:
            self.scroll_helper.clear_cache()
        self._team_logo_cache.clear()
        super().cleanup()
