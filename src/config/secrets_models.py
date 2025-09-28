from pydantic import BaseModel, Field


class WeatherSecrets(BaseModel):
    api_key: str = Field(
        default="YOUR_OPENWEATHERMAP_API_KEY",
        description="API key for accessing OpenWeatherMap services.",
    )


class YoutubeSecrets(BaseModel):
    api_key: str = Field(
        default="YOUR_YOUTUBE_API_KEY",
        description="API key for accessing YouTube Data API.",
    )
    channel_id: str = Field(
        default="YOUR_YOUTUBE_CHANNEL_ID",
        description="Channel ID of the YouTube channel to fetch data from.",
    )


class MusicSecrets(BaseModel):
    SPOTIFY_CLIENT_ID: str = Field(
        default="YOUR_SPOTIFY_CLIENT_ID_HERE",
        description="Spotify application Client ID.",
    )
    SPOTIFY_CLIENT_SECRET: str = Field(
        default="YOUR_SPOTIFY_CLIENT_SECRET_HERE",
        description="Spotify application Client Secret.",
    )
    SPOTIFY_REDIRECT_URI: str = Field(
        default="http://127.0.0.1:8888/callback",
        description="Redirect URI for Spotify OAuth authentication.",
    )


class SecretsConfig(BaseModel):
    weather: WeatherSecrets = Field(
        default=WeatherSecrets(),
        description="Weather API authentication configuration.",
    )
    youtube: YoutubeSecrets = Field(
        default=YoutubeSecrets(),
        description="YouTube API authentication configuration.",
    )
    music: MusicSecrets = Field(
        default=MusicSecrets(), description="Spotify authentication configuration."
    )
