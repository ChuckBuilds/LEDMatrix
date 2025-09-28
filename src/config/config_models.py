import re
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# -------------------------
# Small utility models
# -------------------------
class Schedule(BaseModel):
    enabled: bool = Field(default=True, description="Whether the schedule is active")
    start_time: str = Field(
        default="07:00", description="Daily start time in HH:MM format"
    )
    end_time: str = Field(default="23:00", description="Daily end time in HH:MM format")

    @field_validator("start_time", "end_time")
    def _validate_time_format(cls, v: str) -> str:
        if not isinstance(v, str) or not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("Time must be a string in HH:MM format")
        hours, minutes = v.split(":")
        h = int(hours)
        m = int(minutes)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("Hour must be 0-23 and minute 0-59")
        return v


class Location(BaseModel):
    city: str = Field(default="Dallas", description="City of the display location")
    state: str = Field(default="Texas", description="State of the display location")
    country: str = Field(default="US", description="Country code (ISO 3166-1 alpha-2)")


# -------------------------
# Display models
# -------------------------
class HardwareConfig(BaseModel):
    rows: int = Field(default=32, description="Number of LED matrix rows", ge=1)
    cols: int = Field(default=64, description="Number of LED matrix columns", ge=1)
    chain_length: int = Field(
        default=2, description="Number of daisy-chained panels", ge=1
    )
    parallel: int = Field(default=1, description="Parallel chains supported", ge=1)
    brightness: int = Field(
        default=95, description="Panel brightness (0–100)", ge=0, le=100
    )
    hardware_mapping: str = Field(
        default="adafruit-hat-pwm", description="Hardware mapping mode"
    )
    scan_mode: int = Field(
        default=0, description="Scan mode (0 = progressive, 1 = interlaced)", ge=0, le=1
    )
    pwm_bits: int = Field(
        default=9, description="PWM bits for brightness control", ge=1
    )
    pwm_dither_bits: int = Field(default=1, description="Dithering bits for PWM", ge=0)
    pwm_lsb_nanoseconds: int = Field(
        default=130, description="Nanoseconds for LSB timing", ge=1
    )
    disable_hardware_pulsing: bool = Field(
        default=False, description="Disable hardware pulsing if True"
    )
    inverse_colors: bool = Field(
        default=False, description="Invert display colors if True"
    )
    show_refresh_rate: bool = Field(
        default=False, description="Show panel refresh rate overlay"
    )
    limit_refresh_rate_hz: int = Field(
        default=120, description="Maximum refresh rate in Hz", ge=1
    )


class RuntimeConfig(BaseModel):
    gpio_slowdown: int = Field(
        default=3, description="GPIO slowdown factor for Raspberry Pi", ge=0
    )


class DisplayDurations(BaseModel):
    clock: int = Field(default=15, description="Duration (sec) to show clock", ge=0)
    weather: int = Field(default=30, description="Duration (sec) to show weather", ge=0)
    stocks: int = Field(default=30, description="Duration (sec) to show stocks", ge=0)
    hourly_forecast: int = Field(
        default=30, description="Duration (sec) to show hourly forecast", ge=0
    )
    daily_forecast: int = Field(
        default=30, description="Duration (sec) to show daily forecast", ge=0
    )
    stock_news: int = Field(
        default=20, description="Duration (sec) to show stock news", ge=0
    )
    odds_ticker: int = Field(
        default=60, description="Duration (sec) to show odds ticker", ge=0
    )
    leaderboard: int = Field(
        default=300, description="Duration (sec) to show leaderboard", ge=0
    )
    nhl_live: int = Field(default=30, description="Duration (sec) for nhl_live", ge=0)
    nhl_recent: int = Field(
        default=30, description="Duration (sec) for nhl_recent", ge=0
    )
    nhl_upcoming: int = Field(
        default=30, description="Duration (sec) for nhl_upcoming", ge=0
    )
    nba_live: int = Field(default=30, description="Duration (sec) for nba_live", ge=0)
    nba_recent: int = Field(
        default=30, description="Duration (sec) for nba_recent", ge=0
    )
    nba_upcoming: int = Field(
        default=30, description="Duration (sec) for nba_upcoming", ge=0
    )
    nfl_live: int = Field(default=30, description="Duration (sec) for nfl_live", ge=0)
    nfl_recent: int = Field(
        default=30, description="Duration (sec) for nfl_recent", ge=0
    )
    nfl_upcoming: int = Field(
        default=30, description="Duration (sec) for nfl_upcoming", ge=0
    )
    ncaa_fb_live: int = Field(
        default=30, description="Duration (sec) for ncaa_fb_live", ge=0
    )
    ncaa_fb_recent: int = Field(
        default=30, description="Duration (sec) for ncaa_fb_recent", ge=0
    )
    ncaa_fb_upcoming: int = Field(
        default=30, description="Duration (sec) for ncaa_fb_upcoming", ge=0
    )
    ncaa_baseball_live: int = Field(
        default=30, description="Duration (sec) for ncaa_baseball_live", ge=0
    )
    ncaa_baseball_recent: int = Field(
        default=30, description="Duration (sec) for ncaa_baseball_recent", ge=0
    )
    ncaa_baseball_upcoming: int = Field(
        default=30, description="Duration (sec) for ncaa_baseball_upcoming", ge=0
    )
    calendar: int = Field(
        default=30, description="Duration (sec) to show calendar", ge=0
    )
    youtube: int = Field(
        default=30, description="Duration (sec) for youtube items", ge=0
    )
    mlb_live: int = Field(default=30, description="Duration (sec) for mlb_live", ge=0)
    mlb_recent: int = Field(
        default=30, description="Duration (sec) for mlb_recent", ge=0
    )
    mlb_upcoming: int = Field(
        default=30, description="Duration (sec) for mlb_upcoming", ge=0
    )
    milb_live: int = Field(default=30, description="Duration (sec) for milb_live", ge=0)
    milb_recent: int = Field(
        default=30, description="Duration (sec) for milb_recent", ge=0
    )
    milb_upcoming: int = Field(
        default=30, description="Duration (sec) for milb_upcoming", ge=0
    )
    text_display: int = Field(
        default=10, description="Duration (sec) for text display", ge=0
    )
    soccer_live: int = Field(
        default=30, description="Duration (sec) for soccer_live", ge=0
    )
    soccer_recent: int = Field(
        default=30, description="Duration (sec) for soccer_recent", ge=0
    )
    soccer_upcoming: int = Field(
        default=30, description="Duration (sec) for soccer_upcoming", ge=0
    )
    ncaam_basketball_live: int = Field(
        default=30, description="Duration (sec) for ncaam_basketball_live", ge=0
    )
    ncaam_basketball_recent: int = Field(
        default=30, description="Duration (sec) for ncaam_basketball_recent", ge=0
    )
    ncaam_basketball_upcoming: int = Field(
        default=30, description="Duration (sec) for ncaam_basketball_upcoming", ge=0
    )
    music: int = Field(default=30, description="Duration (sec) for music items", ge=0)
    of_the_day: int = Field(
        default=40, description="Duration (sec) for of the day", ge=0
    )
    news_manager: int = Field(
        default=60, description="Duration (sec) for news manager", ge=0
    )

    @field_validator("*", mode="after")
    def _ensure_non_negative(cls, v):
        if isinstance(v, int) and v < 0:
            raise ValueError("Durations must be non-negative integers")
        return v


class DisplayConfig(BaseModel):
    hardware: HardwareConfig = Field(
        default_factory=HardwareConfig, description="Low-level hardware configuration"
    )
    runtime: RuntimeConfig = Field(
        default_factory=RuntimeConfig, description="Runtime tweaks for the runtime"
    )
    display_durations: DisplayDurations = Field(
        default_factory=DisplayDurations, description="Per-module display durations"
    )
    use_short_date_format: bool = Field(
        default=True, description="Whether to use short date format on the display"
    )


# -------------------------
# Clock
# -------------------------
class ClockConfig(BaseModel):
    enabled: bool = Field(default=True, description="Whether the clock is enabled")
    format: str = Field(
        default="%I:%M %p", description="Format string for clock display (strftime)"
    )
    update_interval: int = Field(
        default=1, description="Clock update interval in seconds", ge=1
    )


# -------------------------
# Weather
# -------------------------
class WeatherConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable weather display")
    update_interval: int = Field(
        default=1800, description="Weather update interval in seconds", ge=30
    )
    units: str = Field(
        default="imperial", description="Units for weather (imperial/metric)"
    )
    display_format: str = Field(
        default="{temp}°F\n{condition}", description="Display format for weather"
    )


# -------------------------
# Stocks & Crypto & Stock News
# -------------------------
class StocksConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable stock display")
    update_interval: int = Field(
        default=600, description="Update interval in seconds", ge=10
    )
    scroll_speed: int = Field(default=1, description="Scroll speed", ge=0)
    scroll_delay: float = Field(
        default=0.01, description="Scroll delay (seconds)", ge=0
    )
    toggle_chart: bool = Field(default=True, description="Toggle chart on ticker")
    dynamic_duration: bool = Field(
        default=True, description="Enable dynamic duration calculation"
    )
    min_duration: int = Field(
        default=30, description="Minimum display duration (sec)", ge=0
    )
    max_duration: int = Field(
        default=300, description="Maximum display duration (sec)", ge=0
    )
    duration_buffer: float = Field(
        default=0.1, description="Duration buffer multiplier", ge=0.0
    )
    symbols: List[str] = Field(
        default_factory=lambda: ["ASTS", "SCHD", "INTC", "NVDA", "T", "VOO", "SMCI"],
        description="List of stock symbols",
    )
    display_format: str = Field(
        default="{symbol}: ${price} ({change}%)", description="Stock display format"
    )


class CryptoConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable crypto display")
    update_interval: int = Field(
        default=600, description="Update interval in seconds", ge=10
    )
    symbols: List[str] = Field(
        default_factory=lambda: ["BTC-USD", "ETH-USD"],
        description="List of crypto symbols",
    )
    display_format: str = Field(
        default="{symbol}: ${price} ({change}%)", description="Crypto display format"
    )


class StockNewsConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable stock news display")
    update_interval: int = Field(
        default=3600, description="Update interval in seconds", ge=10
    )
    scroll_speed: int = Field(default=1, description="Scroll speed", ge=0)
    scroll_delay: float = Field(
        default=0.01, description="Scroll delay (seconds)", ge=0
    )
    max_headlines_per_symbol: int = Field(
        default=1, description="Maximum headlines per symbol", ge=0
    )
    headlines_per_rotation: int = Field(
        default=2, description="Headlines per rotation", ge=0
    )
    dynamic_duration: bool = Field(default=True, description="Enable dynamic duration")
    min_duration: int = Field(
        default=30, description="Minimum display duration (sec)", ge=0
    )
    max_duration: int = Field(
        default=300, description="Maximum display duration (sec)", ge=0
    )
    duration_buffer: float = Field(
        default=0.1, description="Duration buffer multiplier", ge=0.0
    )


# -------------------------
# Background service (shared)
# -------------------------
class BackgroundServiceConfig(BaseModel):
    enabled: bool = Field(default=True, description="Background service enabled")
    max_workers: int = Field(default=3, description="Max number of workers", ge=1)
    request_timeout: int = Field(
        default=30, description="Request timeout (seconds)", ge=1
    )
    max_retries: int = Field(default=3, description="Maximum retries", ge=0)
    priority: int = Field(default=2, description="Priority for background tasks", ge=0)


# -------------------------
# Odds Ticker
# -------------------------
class OddsTickerConfig(BaseModel):
    enabled: bool = Field(default=True, description="Enable odds ticker")
    show_favorite_teams_only: bool = Field(
        default=True, description="Show only favorite teams"
    )
    games_per_favorite_team: int = Field(
        default=1, description="Games per favorite team", ge=0
    )
    max_games_per_league: int = Field(
        default=5, description="Max games per league", ge=0
    )
    show_odds_only: bool = Field(default=False, description="Show only odds (no teams)")
    sort_order: str = Field(default="soonest", description="Sort order for events")
    enabled_leagues: List[str] = Field(
        default_factory=lambda: ["nfl", "mlb", "ncaa_fb", "milb"],
        description="Enabled leagues",
    )
    update_interval: int = Field(
        default=3600, description="Update interval in seconds", ge=1
    )
    scroll_speed: int = Field(default=1, description="Scroll speed", ge=0)
    scroll_delay: float = Field(
        default=0.01, description="Scroll delay (seconds)", ge=0
    )
    loop: bool = Field(default=True, description="Whether to loop the ticker")
    future_fetch_days: int = Field(
        default=50, description="How many days into the future to fetch", ge=0
    )
    show_channel_logos: bool = Field(default=True, description="Show channel logos")
    dynamic_duration: bool = Field(default=True, description="Enable dynamic duration")
    min_duration: int = Field(default=30, description="Minimum duration (sec)", ge=0)
    max_duration: int = Field(default=300, description="Maximum duration (sec)", ge=0)
    duration_buffer: float = Field(
        default=0.1, description="Duration buffer multiplier", ge=0.0
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service config for odds ticker",
    )


# -------------------------
# Leaderboard
# -------------------------
class LeaderboardSportsConfig(BaseModel):
    enabled: bool = Field(
        default=True, description="Whether this sport leaderboard is enabled"
    )
    top_teams: int = Field(default=10, description="Number of top teams to show", ge=0)
    show_ranking: Optional[bool] = Field(
        default=None, description="Whether to display ranking (optional)"
    )


class LeaderboardConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable leaderboard")
    enabled_sports: Dict[str, LeaderboardSportsConfig] = Field(
        default_factory=lambda: {
            "nfl": LeaderboardSportsConfig(enabled=True, top_teams=10),
            "nba": LeaderboardSportsConfig(enabled=False, top_teams=10),
            "mlb": LeaderboardSportsConfig(enabled=False, top_teams=10),
            "ncaa_fb": LeaderboardSportsConfig(
                enabled=True, top_teams=25, show_ranking=True
            ),
            "nhl": LeaderboardSportsConfig(enabled=False, top_teams=10),
            "ncaam_basketball": LeaderboardSportsConfig(enabled=False, top_teams=25),
            "ncaam_hockey": LeaderboardSportsConfig(
                enabled=True, top_teams=10, show_ranking=True
            ),
        },
        description="Per-sport leaderboard configuration",
    )
    update_interval: int = Field(
        default=3600, description="Update interval in seconds", ge=1
    )
    scroll_speed: int = Field(default=1, description="Scroll speed", ge=0)
    scroll_delay: float = Field(
        default=0.01, description="Scroll delay (seconds)", ge=0
    )
    loop: bool = Field(default=False, description="Whether to loop the leaderboard")
    request_timeout: int = Field(
        default=30, description="Request timeout seconds", ge=1
    )
    dynamic_duration: bool = Field(default=True, description="Dynamic duration enabled")
    min_duration: int = Field(default=30, description="Minimum duration (sec)", ge=0)
    max_display_time: int = Field(
        default=600, description="Maximum display time (sec)", ge=0
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for leaderboard",
    )


# -------------------------
# Calendar
# -------------------------
class CalendarConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable calendar display")
    credentials_file: str = Field(
        default="credentials.json", description="Path to credentials file"
    )
    token_file: str = Field(default="token.pickle", description="Path to token file")
    update_interval: int = Field(
        default=3600, description="Update interval in seconds", ge=1
    )
    max_events: int = Field(
        default=3, description="Maximum number of events to show", ge=0
    )
    calendars: List[str] = Field(
        default_factory=lambda: ["birthdays"], description="List of calendar IDs/names"
    )


# -------------------------
# Generic scoreboard base and specific scoreboard models
# -------------------------
class ScoreboardBaseConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable this scoreboard")
    live_priority: bool = Field(default=True, description="Give live games priority")
    live_game_duration: int = Field(
        default=20, description="How long to show a live game (sec)", ge=0
    )
    show_odds: bool = Field(
        default=True, description="Display betting odds if available"
    )
    test_mode: bool = Field(default=False, description="If true, run in test mode")
    update_interval_seconds: int = Field(
        default=3600, description="General update interval (sec)", ge=1
    )
    live_update_interval: int = Field(
        default=30, description="Live update frequency (sec)", ge=1
    )
    live_odds_update_interval: Optional[int] = Field(
        default=None, description="Live odds update interval (sec)"
    )
    odds_update_interval: Optional[int] = Field(
        default=None, description="Odds update interval (sec)"
    )
    recent_update_interval: Optional[int] = Field(
        default=None, description="Recent games update interval (sec)"
    )
    upcoming_update_interval: Optional[int] = Field(
        default=None, description="Upcoming games update interval (sec)"
    )
    recent_games_to_show: int = Field(
        default=1, description="How many recent games to show", ge=0
    )
    upcoming_games_to_show: int = Field(
        default=1, description="How many upcoming games to show", ge=0
    )
    show_favorite_teams_only: bool = Field(
        default=True, description="Only show favorite teams"
    )
    favorite_teams: List[str] = Field(
        default_factory=list, description="List of favorite team codes"
    )
    logo_dir: str = Field(default="", description="Directory for team logos")
    show_records: bool = Field(default=True, description="Show team records")
    show_ranking: Optional[bool] = Field(
        default=None, description="Show ranking where applicable"
    )
    upcoming_fetch_days: Optional[int] = Field(
        default=None, description="How many days ahead to fetch upcoming games"
    )
    background_service: Optional[BackgroundServiceConfig] = Field(
        default=None, description="Background service config"
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=dict, description="Which display modes are enabled"
    )


class NHLScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable NHL scoreboard")
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["TB"], description="Favorite NHL teams"
    )
    logo_dir: str = Field(
        default="assets/sports/nhl_logos", description="NHL logos directory"
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for NHL",
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "nhl_live": True,
            "nhl_recent": True,
            "nhl_upcoming": True,
        }
    )


class NBAScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable NBA scoreboard")
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["DAL"], description="Favorite NBA teams"
    )
    logo_dir: str = Field(
        default="assets/sports/nba_logos", description="NBA logos directory"
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for NBA",
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "nba_live": True,
            "nba_recent": True,
            "nba_upcoming": True,
        }
    )


class NFLScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable NFL scoreboard")
    live_game_duration: int = Field(
        default=30, description="Live game duration for NFL (sec)", ge=0
    )
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["TB", "DAL"], description="Favorite NFL teams"
    )
    logo_dir: str = Field(
        default="assets/sports/nfl_logos", description="NFL logos directory"
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for NFL",
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "nfl_live": True,
            "nfl_recent": True,
            "nfl_upcoming": True,
        }
    )


class NCAAFBScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable NCAA Football scoreboard")
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["UGA", "AUB", "AP_TOP_25"],
        description="Favorite NCAA football teams",
    )
    logo_dir: str = Field(
        default="assets/sports/ncaa_logos", description="NCAA logos directory"
    )
    show_ranking: bool = Field(
        default=True, description="Show ranking for NCAA football"
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for NCAA FB",
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "ncaa_fb_live": True,
            "ncaa_fb_recent": True,
            "ncaa_fb_upcoming": True,
        }
    )


class NCAABaseballScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable NCAA baseball scoreboard")
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["UGA", "AUB"],
        description="Favorite NCAA baseball teams",
    )
    logo_dir: str = Field(
        default="assets/sports/ncaa_logos", description="NCAA logos directory"
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "ncaa_baseball_live": True,
            "ncaa_baseball_recent": True,
            "ncaa_baseball_upcoming": True,
        }
    )


class NCAAMBasketballScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(
        default=False, description="Enable NCAAM basketball scoreboard"
    )
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["UGA", "AUB"],
        description="Favorite NCAAM basketball teams",
    )
    logo_dir: str = Field(
        default="assets/sports/ncaa_logos", description="NCAA logos directory"
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "ncaam_basketball_live": True,
            "ncaam_basketball_recent": True,
            "ncaam_basketball_upcoming": True,
        }
    )


class NCAAMHockeyScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=True, description="Enable NCAAM hockey scoreboard")
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["RIT"], description="Favorite NCAAM hockey teams"
    )
    logo_dir: str = Field(
        default="assets/sports/ncaa_logos", description="NCAA logos directory"
    )
    show_ranking: bool = Field(
        default=True, description="Show ranking for NCAAM hockey"
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "ncaam_hockey_live": True,
            "ncaam_hockey_recent": True,
            "ncaam_hockey_upcoming": True,
        }
    )


class MLBScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable MLB scoreboard")
    live_priority: bool = Field(
        default=False, description="Whether live priority is used for MLB"
    )
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["TB", "TEX"], description="Favorite MLB teams"
    )
    logo_dir: str = Field(
        default="assets/sports/mlb_logos", description="MLB logos directory"
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "mlb_live": True,
            "mlb_recent": True,
            "mlb_upcoming": True,
        }
    )


class MILBScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable MiLB scoreboard")
    live_priority: bool = Field(
        default=False, description="Whether live priority is used for MiLB"
    )
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["TAM"], description="Favorite MiLB teams"
    )
    logo_dir: str = Field(
        default="assets/sports/milb_logos", description="MiLB logos directory"
    )
    upcoming_fetch_days: int = Field(
        default=7, description="Days to look ahead for upcoming MiLB games", ge=0
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for MiLB",
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "milb_live": True,
            "milb_recent": True,
            "milb_upcoming": True,
        }
    )


class SoccerScoreboardConfig(ScoreboardBaseConfig):
    enabled: bool = Field(default=False, description="Enable soccer scoreboard")
    favorite_teams: List[str] = Field(
        default_factory=lambda: ["DAL"], description="Favorite soccer teams"
    )
    leagues: List[str] = Field(
        default_factory=lambda: ["usa.1"], description="Soccer leagues to include"
    )
    logo_dir: str = Field(
        default="assets/sports/soccer_logos", description="Soccer logos directory"
    )
    background_service: BackgroundServiceConfig = Field(
        default_factory=BackgroundServiceConfig,
        description="Background service for soccer",
    )
    display_modes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "soccer_live": True,
            "soccer_recent": True,
            "soccer_upcoming": True,
        }
    )


# -------------------------
# YouTube
# -------------------------
class YouTubeConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable YouTube module")
    update_interval: int = Field(
        default=3600, description="YouTube update interval in seconds", ge=1
    )


# -------------------------
# Text display
# -------------------------
class TextDisplayConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable custom text display")
    text: str = Field(default="Subscribe to ChuckBuilds", description="Text to display")
    font_path: str = Field(
        default="assets/fonts/press-start-2p.ttf", description="Path to font file"
    )
    font_size: int = Field(default=8, description="Font size in pixels", ge=1)
    scroll: bool = Field(default=True, description="Whether the text scrolls")
    scroll_speed: int = Field(default=40, description="Scroll speed", ge=0)
    text_color: List[int] = Field(
        default_factory=lambda: [255, 0, 0], description="RGB color for text (3 ints)"
    )
    background_color: List[int] = Field(
        default_factory=lambda: [0, 0, 0], description="RGB for background (3 ints)"
    )
    scroll_gap_width: int = Field(
        default=32, description="Gap width between repeated scrolls", ge=0
    )

    @field_validator("text_color", "background_color")
    def _validate_color_array(cls, v):
        if not isinstance(v, list) or len(v) != 3:
            raise ValueError("Color must be a list of 3 integers [R, G, B]")
        for c in v:
            if not isinstance(c, int) or not (0 <= c <= 255):
                raise ValueError("Color components must be integers 0-255")
        return v


# -------------------------
# Music
# -------------------------
class MusicConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable music module")
    preferred_source: str = Field(default="ytm", description="Preferred music source")
    YTM_COMPANION_URL: str = Field(
        default="http://192.168.86.12:9863", description="YTM companion URL"
    )
    POLLING_INTERVAL_SECONDS: int = Field(
        default=1, description="Polling interval for music (sec)", ge=1
    )


# -------------------------
# Of the day
# -------------------------
class OfTheDayCategory(BaseModel):
    enabled: bool = Field(default=True, description="Enable this category")
    data_file: str = Field(default="", description="Path to data file")
    display_name: str = Field(default="", description="Display name for category")


class OfTheDayConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable of-the-day feature")
    display_rotate_interval: int = Field(
        default=20, description="Rotate interval in seconds", ge=0
    )
    update_interval: int = Field(
        default=3600, description="Update interval in seconds", ge=1
    )
    subtitle_rotate_interval: int = Field(
        default=10, description="Subtitle rotate interval in seconds", ge=0
    )
    category_order: List[str] = Field(
        default_factory=lambda: ["word_of_the_day", "slovenian_word_of_the_day"],
        description="Order of categories to rotate",
    )
    categories: Dict[str, OfTheDayCategory] = Field(
        default_factory=lambda: {
            "word_of_the_day": OfTheDayCategory(
                enabled=True,
                data_file="of_the_day/word_of_the_day.json",
                display_name="Word of the Day",
            ),
            "slovenian_word_of_the_day": OfTheDayCategory(
                enabled=True,
                data_file="of_the_day/slovenian_word_of_the_day.json",
                display_name="Slovenian Word of the Day",
            ),
        },
        description="Category specific configs",
    )


# -------------------------
# News manager
# -------------------------
class NewsManagerConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable news manager")
    update_interval: int = Field(
        default=300, description="Update interval in seconds", ge=1
    )
    scroll_speed: int = Field(default=1, description="Scroll speed", ge=0)
    scroll_delay: float = Field(
        default=0.01, description="Scroll delay (seconds)", ge=0
    )
    headlines_per_feed: int = Field(
        default=2, description="Headlines per feed to show", ge=0
    )
    enabled_feeds: List[str] = Field(
        default_factory=lambda: ["NFL", "NCAA FB", "F1", "BBC F1"],
        description="Pre-enabled feed names",
    )
    custom_feeds: Dict[str, str] = Field(
        default_factory=lambda: {
            "F1": "https://www.espn.com/espn/rss/rpm/news",
            "BBC F1": "http://feeds.bbci.co.uk/sport/formula1/rss.xml",
        },
        description="User-specified custom RSS feeds",
    )
    rotation_enabled: bool = Field(
        default=True, description="Enable rotation between feeds"
    )
    rotation_threshold: int = Field(
        default=3, description="Rotation threshold value", ge=0
    )
    dynamic_duration: bool = Field(default=True, description="Enable dynamic duration")
    min_duration: int = Field(
        default=30, description="Minimum duration in seconds", ge=0
    )
    max_duration: int = Field(
        default=300, description="Maximum duration in seconds", ge=0
    )
    duration_buffer: float = Field(
        default=0.1, description="Duration buffer multiplier", ge=0.0
    )
    font_size: int = Field(default=8, description="Font size for news text", ge=1)
    font_path: str = Field(
        default="assets/fonts/PressStart2P-Regular.ttf", description="Path to font"
    )
    text_color: List[int] = Field(
        default_factory=lambda: [255, 255, 255], description="RGB text color (3 ints)"
    )
    separator_color: List[int] = Field(
        default_factory=lambda: [255, 0, 0], description="RGB separator color (3 ints)"
    )

    @field_validator("text_color", "separator_color")
    def _validate_colors(cls, v):
        if not isinstance(v, list) or len(v) != 3:
            raise ValueError("Color must be a list of 3 integers [R, G, B]")
        for c in v:
            if not isinstance(c, int) or not (0 <= c <= 255):
                raise ValueError("Color components must be integers 0-255")
        return v


# -------------------------
# Root configuration (everything together)
# -------------------------
class RootConfig(BaseModel):
    web_display_autostart: bool = Field(
        default=True, description="Autostart the web display service"
    )
    schedule: Schedule = Field(
        default_factory=Schedule, description="Schedule configuration"
    )
    timezone: str = Field(default="America/Chicago", description="System timezone")
    location: Location = Field(
        default_factory=Location, description="Geographic location of display"
    )
    display: DisplayConfig = Field(
        default_factory=DisplayConfig, description="Display configuration"
    )
    clock: ClockConfig = Field(
        default_factory=ClockConfig, description="Clock configuration"
    )
    weather: WeatherConfig = Field(
        default_factory=WeatherConfig, description="Weather configuration"
    )
    stocks: StocksConfig = Field(
        default_factory=StocksConfig, description="Stock ticker configuration"
    )
    crypto: CryptoConfig = Field(
        default_factory=CryptoConfig, description="Crypto ticker configuration"
    )
    stock_news: StockNewsConfig = Field(
        default_factory=StockNewsConfig, description="Stock news configuration"
    )
    odds_ticker: OddsTickerConfig = Field(
        default_factory=OddsTickerConfig, description="Odds ticker configuration"
    )
    leaderboard: LeaderboardConfig = Field(
        default_factory=LeaderboardConfig, description="Leaderboard configuration"
    )
    calendar: CalendarConfig = Field(
        default_factory=CalendarConfig, description="Calendar configuration"
    )
    nhl_scoreboard: NHLScoreboardConfig = Field(
        default_factory=NHLScoreboardConfig, description="NHL scoreboard configuration"
    )
    nba_scoreboard: NBAScoreboardConfig = Field(
        default_factory=NBAScoreboardConfig, description="NBA scoreboard configuration"
    )
    nfl_scoreboard: NFLScoreboardConfig = Field(
        default_factory=NFLScoreboardConfig, description="NFL scoreboard configuration"
    )
    ncaa_fb_scoreboard: NCAAFBScoreboardConfig = Field(
        default_factory=NCAAFBScoreboardConfig,
        description="NCAA Football scoreboard configuration",
    )
    ncaa_baseball_scoreboard: NCAABaseballScoreboardConfig = Field(
        default_factory=NCAABaseballScoreboardConfig,
        description="NCAA Baseball scoreboard configuration",
    )
    ncaam_basketball_scoreboard: NCAAMBasketballScoreboardConfig = Field(
        default_factory=NCAAMBasketballScoreboardConfig,
        description="NCAAM Basketball scoreboard configuration",
    )
    ncaam_hockey_scoreboard: NCAAMHockeyScoreboardConfig = Field(
        default_factory=NCAAMHockeyScoreboardConfig,
        description="NCAAM Hockey scoreboard configuration",
    )
    youtube: YouTubeConfig = Field(
        default_factory=YouTubeConfig, description="YouTube configuration"
    )
    mlb_scoreboard: MLBScoreboardConfig = Field(
        default_factory=MLBScoreboardConfig, description="MLB scoreboard configuration"
    )
    milb_scoreboard: MILBScoreboardConfig = Field(
        default_factory=MILBScoreboardConfig,
        description="MiLB scoreboard configuration",
    )
    text_display: TextDisplayConfig = Field(
        default_factory=TextDisplayConfig, description="Text display configuration"
    )
    soccer_scoreboard: SoccerScoreboardConfig = Field(
        default_factory=SoccerScoreboardConfig,
        description="Soccer scoreboard configuration",
    )
    music: MusicConfig = Field(
        default_factory=MusicConfig, description="Music configuration"
    )
    of_the_day: OfTheDayConfig = Field(
        default_factory=OfTheDayConfig, description="Of-the-day configuration"
    )
    news_manager: NewsManagerConfig = Field(
        default_factory=NewsManagerConfig, description="News manager configuration"
    )

    @field_validator("timezone")
    def _cross_validate(cls, tz):
        # example: ensure timezone non-empty
        if not tz or not isinstance(tz, str):
            raise ValueError("timezone must be a non-empty string")
        return tz
