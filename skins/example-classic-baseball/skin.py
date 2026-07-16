"""
Example: Classic Baseball — the reference skin.

Shows the whole skin API surface on purpose: adaptive regions
(scoreboard_regions), fitted text (ctx.layout.fit_text + ctx.draw_fit),
logos (ctx.load_logo + ctx.draw_image), raw PIL (ctx.draw for the bases
diamond), and per-user options (ctx.options). Everything is derived from
ctx and the game dict — a skin holds no state, does no I/O, and never
touches the display.

Copy this directory to skins/<your-skin-id>/, rename the class and the
manifest fields, and run:

    python scripts/validate_skin.py --skin <your-skin-id>
"""

from src.adaptive_layout import LADDER_GRID, scoreboard_regions
from src.skin_system.skin_base import ScoreboardSkin, SkinContext


class ClassicBaseballSkin(ScoreboardSkin):

    # -- shared pieces ----------------------------------------------------

    def _accent(self, ctx: SkinContext):
        """Users can recolor the skin from config via skin_options."""
        return tuple(ctx.options.get("accent_color", (255, 200, 0)))

    def _draw_card(self, ctx: SkinContext, game: dict, status: str,
                   center_lines: list, detail: str) -> None:
        """The common card: logos left/right, status on top, the given
        center content, detail along the bottom."""
        regions = scoreboard_regions(ctx.layout.bounds, ctx=ctx.layout)

        ctx.draw_image(ctx.load_logo("away"), regions.away_slot,
                       cache_key=f"logo:{game.get('away_abbr')}")
        ctx.draw_image(ctx.load_logo("home"), regions.home_slot,
                       cache_key=f"logo:{game.get('home_abbr')}")

        if status:
            fit = ctx.layout.fit_text(status, regions.status_band, LADDER_GRID)
            ctx.draw_fit(fit, regions.status_band, color=self._accent(ctx))

        if center_lines:
            rows = regions.score_area.split_v(*[1] * len(center_lines))
            for line, row in zip(center_lines, rows):
                if line:
                    fit = ctx.layout.fit_text(line, row, LADDER_GRID)
                    ctx.draw_fit(fit, row)

        if detail:
            fit = ctx.layout.fit_text(detail, regions.detail_band, LADDER_GRID)
            ctx.draw_fit(fit, regions.detail_band, color=(160, 160, 160))

    def _draw_bases_and_outs(self, ctx: SkinContext, game: dict) -> None:
        """Raw-PIL escape hatch: a bases diamond + out dots in the bottom
        band, sized from the layout scale so it works on any panel."""
        size = ctx.layout.px(3, minimum=2)     # half-diagonal of one base
        gap = ctx.layout.px(1)
        cx = ctx.width // 2
        cy = ctx.height - (size * 2) - 1

        bases = game.get("bases_occupied") or [False, False, False]
        # (dx, dy) per base: first (right), second (top), third (left)
        offsets = [(size + gap, 0), (0, -(size + gap)), (-(size + gap), 0)]
        for occupied, (dx, dy) in zip(bases, offsets):
            x, y = cx + dx, cy + dy
            diamond = [(x, y - size), (x + size, y), (x, y + size), (x - size, y)]
            if occupied:
                ctx.draw.polygon(diamond, fill=self._accent(ctx))
            else:
                ctx.draw.polygon(diamond, outline=(110, 110, 110))

        outs = min(int(game.get("outs") or 0), 3)
        r = max(1, size - 1)
        for i in range(3):
            x = cx + (i - 1) * (2 * r + 2 * gap)
            y = ctx.height - r - 1
            dot = [x - r, y - r, x + r, y + r]
            if i < outs:
                ctx.draw.ellipse(dot, fill=(255, 255, 255))
            else:
                ctx.draw.ellipse(dot, outline=(110, 110, 110))

    # -- the three modes --------------------------------------------------

    def render_live(self, ctx: SkinContext, game: dict) -> bool:
        half = "▲" if game.get("inning_half") == "top" else "▼"
        inning = game.get("inning") or ""
        status = f"{half}{inning}" if inning else game.get("status_text", "")
        score = f"{game.get('away_score', '0')}-{game.get('home_score', '0')}"
        count = f"{game.get('balls', 0)}-{game.get('strikes', 0)}"

        self._draw_card(ctx, game, status, [score], "")
        self._draw_bases_and_outs(ctx, game)

        # Ball-strike count in the top-left corner, over the away logo.
        fit = ctx.layout.fit_text(count, (ctx.width // 4, ctx.layout.px(8, minimum=6)), LADDER_GRID)
        ctx.draw_fit(fit, ctx.layout.bounds.top_band(fit.height + 1).left_col(fit.width + 2),
                     color=(200, 200, 200))
        return True

    def render_recent(self, ctx: SkinContext, game: dict) -> bool:
        score = f"{game.get('away_score', '0')}-{game.get('home_score', '0')}"
        self._draw_card(ctx, game, game.get("status_text", "Final"),
                        [score], game.get("series_summary", ""))
        return True

    def render_upcoming(self, ctx: SkinContext, game: dict) -> bool:
        matchup = f"{game.get('away_abbr', '')}@{game.get('home_abbr', '')}"
        self._draw_card(ctx, game, game.get("game_date", ""),
                        [matchup, game.get("game_time", "")],
                        f"{game.get('away_record', '')} {game.get('home_record', '')}".strip())
        return True
