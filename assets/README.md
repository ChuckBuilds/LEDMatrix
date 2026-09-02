# assets/

Static assets bundled with LEDMatrix. **Do not delete these directories** —
several look unused from core code alone but are resolved at runtime by
installed store plugins.

| Directory | Used by |
|---|---|
| `fonts/` | Core (`FontManager`, `DisplayManager`) and most plugins |
| `sports/` | Core logo tooling (`src/logo_downloader.py`) and the sports scoreboard plugins; team logos are downloaded here on demand |
| `stocks/` | `ledmatrix-stocks` plugin (`crypto_icons/`, `ticker_icons/`) |
| `weather/` | `ledmatrix-weather` plugin (weather icons) |
| `news_logos/` | `news` plugin |
| `broadcast_logos/` | `news` and `odds-ticker` plugins |
| `static_images/` | Legacy examples referenced in the `static-image` plugin's docs; the plugin itself stores uploads under `assets/plugins/<plugin-id>/uploads/` |
| `plugins/` | Per-plugin uploaded files (`assets/plugins/<plugin-id>/uploads/`), served by the web interface |

Plugins resolve these paths relative to the LEDMatrix install directory, so
the directories are part of the de-facto plugin API even where no file in
this repo references them. New plugins should bundle their own assets or
use the per-plugin upload directory instead of adding top-level
directories here.
