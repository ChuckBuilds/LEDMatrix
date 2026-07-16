#!/usr/bin/env python3
"""
Headless skin validator — render a skin against bundled fixture games at
multiple panel sizes without hardware, a network, or a running service.

    python scripts/validate_skin.py --skin my-skin
    python scripts/validate_skin.py --skin my-skin --sport baseball \
        --size 128x32 --size 64x32 --output-dir /tmp/skin_renders

For each (mode x size) it checks: the manifest loads and its API version
matches, the render raises no exception, the canvas isn't blank, and the
render finishes inside a time budget (warn — the live renderer runs every
display-loop pass, and a Pi is far slower than your dev machine). PNGs are
saved (native plus 4x nearest-neighbor previews) so you can eyeball the
result. Exit code is non-zero when any check fails.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FIXTURES_DIR = PROJECT_ROOT / "src" / "skin_system" / "fixtures"
MODES = ("live", "recent", "upcoming")
SPORTS = ("baseball", "basketball", "football", "hockey")
RENDER_BUDGET_S = 0.100


class FixtureHost:
    """Stands in for a SportsCore instance: fonts, logger, logo loading,
    outlined text — everything build_context needs, no network."""

    def __init__(self, sport: str, skin_options: dict) -> None:
        self.sport = sport
        self.sport_key = sport
        self.skin_options = skin_options
        self.logger = logging.getLogger(f"validate_skin.{sport}")
        self.fonts = self._load_fonts()
        self._logo_cache = {}
        self.display_manager = None  # build_context is always given a size

    def _load_fonts(self) -> dict:
        """Load the SportsCore font set (TTF, with PIL default fallback)."""
        fonts = {}
        try:
            press = str(PROJECT_ROOT / "assets/fonts/PressStart2P-Regular.ttf")
            small = str(PROJECT_ROOT / "assets/fonts/4x6-font.ttf")
            fonts['score'] = ImageFont.truetype(press, 10)
            fonts['time'] = ImageFont.truetype(press, 8)
            fonts['team'] = ImageFont.truetype(press, 8)
            fonts['status'] = ImageFont.truetype(small, 6)
            fonts['detail'] = ImageFont.truetype(small, 6)
            fonts['rank'] = ImageFont.truetype(press, 10)
        except IOError:
            default = ImageFont.load_default()
            for key in ('score', 'time', 'team', 'status', 'detail', 'rank'):
                fonts[key] = default
        return fonts

    def _load_and_resize_logo(self, team_id: str, team_abbrev: str,
                              logo_path, logo_url) -> "Image.Image | None":
        """Load a fixture logo from disk (no downloads), cached per team."""
        if team_abbrev in self._logo_cache:
            return self._logo_cache[team_abbrev]
        path = Path(logo_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            return None
        logo = Image.open(path).convert('RGBA')
        self._logo_cache[team_abbrev] = logo
        return logo

    def _draw_text_with_outline(self, draw: "ImageDraw.ImageDraw", text: str,
                                position: tuple, font,
                                fill: tuple = (255, 255, 255),
                                outline_color: tuple = (0, 0, 0)) -> None:
        """Classic outlined scorebug text, same as SportsCore's helper."""
        x, y = position
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
                       (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)


def load_fixture(sport: str, mode: str) -> dict:
    with open(FIXTURES_DIR / f"{sport}_{mode}.json", encoding="utf-8") as f:
        game = json.load(f)
    # Real view models carry start_time_utc as a UTC datetime, not a string.
    if isinstance(game.get("start_time_utc"), str):
        from datetime import datetime
        game["start_time_utc"] = datetime.fromisoformat(game["start_time_utc"])
    return game


def parse_size(value: str) -> "tuple[int, int]":
    try:
        w_text, h_text = value.lower().split("x")
        w, h = int(w_text), int(h_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"size must look like 128x32, got {value!r}") from exc
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(f"size dimensions must be positive, got {value!r}")
    return w, h


def parse_options(value: str) -> dict:
    try:
        options = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"options must be valid JSON: {exc.msg}") from exc
    if not isinstance(options, dict):
        raise argparse.ArgumentTypeError("options must be a JSON object")
    return options


def display_path(path: Path) -> str:
    """Repo-relative when inside the repo, absolute otherwise (--output-dir
    may point anywhere, e.g. /tmp/skin_renders)."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skin", required=True, help="skin id (directory name under skins/)")
    parser.add_argument("--sport", choices=SPORTS,
                        help="fixture sport (default: first sport the skin targets, else baseball)")
    parser.add_argument("--size", action="append", type=parse_size, dest="sizes",
                        metavar="WxH", help="panel size to render at (repeatable; default 128x32 and 64x32)")
    parser.add_argument("--output-dir", type=Path,
                        default=PROJECT_ROOT / "skin_renders",
                        help="where rendered PNGs are written")
    parser.add_argument("--options", type=parse_options, default={},
                        help="skin_options JSON to pass the skin")
    args = parser.parse_args()
    sizes = args.sizes or [(128, 32), (64, 32)]

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from src.skin_system import skin_runtime
    from src.skin_system.skin_base import SKIN_API_VERSION

    skins = skin_runtime.discover_skins()
    manifest = skins.get(args.skin)
    if manifest is None:
        print(f"FAIL: skin '{args.skin}' not found under {skin_runtime.get_skins_directory()}")
        if skins:
            print(f"      installed skins: {', '.join(sorted(skins))}")
        return 1

    sport = args.sport
    if sport is None:
        declared = skin_runtime.skin_targets(manifest)[0]
        sport = next((s for s in declared if s in SPORTS), "baseball")

    skin = skin_runtime.load_skin(args.skin, sport=sport, sport_key=sport,
                                  options=args.options)
    if skin is None:
        print(f"FAIL: skin '{args.skin}' did not load "
              f"(see log above; host API is {SKIN_API_VERSION})")
        return 1

    host = FixtureHost(sport, args.options)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    rendered = 0

    for mode in MODES:
        game = load_fixture(sport, mode)
        render = getattr(skin, f"render_{mode}")
        for width, height in sizes:
            label = f"{mode}@{width}x{height}"
            try:
                # Warm-up render absorbs one-time font/image loads, second
                # render is the one timed against the budget.
                ctx = skin_runtime.build_context(host, game, size=(width, height))
                handled = render(ctx, dict(game))
                if handled:
                    ctx = skin_runtime.build_context(host, game, size=(width, height))
                    started = time.monotonic()
                    handled = render(ctx, dict(game))
                    elapsed = time.monotonic() - started
                else:
                    elapsed = 0.0
            except Exception as e:
                print(f"FAIL {label}: render raised {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                failures += 1
                continue

            if not handled:
                print(f"skip {label}: render_{mode} returned False (built-in renderer would be used)")
                continue

            if ctx.canvas.size != (width, height):
                print(f"FAIL {label}: canvas was replaced/resized to {ctx.canvas.size} — draw onto ctx.canvas, never reassign it")
                failures += 1
                continue
            if ctx.canvas.convert("L").getbbox() is None:
                print(f"FAIL {label}: canvas is blank — render returned True but drew nothing")
                failures += 1
                continue
            if elapsed > RENDER_BUDGET_S:
                print(f"WARN {label}: render took {elapsed * 1000:.0f}ms "
                      f"(budget {RENDER_BUDGET_S * 1000:.0f}ms; a Pi is much slower than this machine)")

            out = args.output_dir / f"{args.skin}_{sport}_{mode}_{width}x{height}.png"
            ctx.canvas.save(out)
            preview = ctx.canvas.resize((width * 4, height * 4), Image.NEAREST)
            preview.save(out.with_name(out.stem + "_x4.png"))
            print(f"ok   {label}: {elapsed * 1000:.0f}ms -> {display_path(out)}")
            rendered += 1

        # Vegas card, once per mode at the first size (optional API)
        try:
            width, height = sizes[0]
            ctx = skin_runtime.build_context(host, game, size=(width, height))
            card = skin.render_vegas_card(ctx, dict(game))
            if card is not None:
                out = args.output_dir / f"{args.skin}_{sport}_{mode}_vegas.png"
                card.save(out)
                print(f"ok   {mode} vegas card -> {display_path(out)}")
        except Exception as e:
            print(f"FAIL {mode} vegas card: {type(e).__name__}: {e}")
            failures += 1

    if rendered == 0 and failures == 0:
        print(f"FAIL: skin '{args.skin}' rendered nothing — no render_<mode> returned True")
        return 1
    print(f"\n{'FAILED' if failures else 'PASSED'}: {rendered} renders, {failures} failures "
          f"(PNGs in {args.output_dir})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
