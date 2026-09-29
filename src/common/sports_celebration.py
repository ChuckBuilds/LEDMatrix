"""How the scoreboards draw a score or win celebration.

Five scoreboards -- afl, football, hockey, nrl and soccer -- take over the
panel when a team scores or wins: a backdrop in the scoring team's colours
read off its crest, scenery for the kind of score, confetti, the headline and
the score with the scoring side's digits breathing. The drawing is identical
in all five ``sports.py`` copies (executable AST, docstrings stripped), and
so are the colour helpers it uses; they were copied here from
ledmatrix-plugins ``30455671`` (origin/main, 2026-09-29).

Only the drawing moved. What *arms* a celebration stays in each plugin,
because it differs: which scores count (``_check_for_goal`` /
``_check_for_score``, and nrl matches favourites by team id), the phrase and
the scenery (``_start_celebration``), and when a win fires
(``_check_for_win``). So does ``display()``, which decides whether the
takeover or the scorebug is on screen. A plugin hands this mixin a
celebration dict and it draws it.

The colour helpers are public free functions here (``logo_palette``,
``lift_color``, ``mix_color``, ...); in the plugins they were the same
functions with a leading underscore.

THE CELEBRATION DICT
--------------------
Built by the plugin's ``_start_celebration``. Read here: ``game`` (a
view-model dict; ``<side>_id``, ``<side>_abbr``, ``<side>_logo_path`` and
``<side>_logo_url`` for the crests, ``id`` for the confetti seed),
``scored_side`` (``"away"`` or ``"home"``), ``away_score``, ``home_score``,
``phrase``, ``started_at`` (a ``time.time()`` value) and ``motif``
(``"score"``, ``"kick"``, ``"touchdown"``, ``"net"`` or ``"win"``; anything
else draws the ``"score"`` diagonals). The drawing caches what it derives in
the same dict, under ``_palette``, ``_backdrop``, ``_confetti`` and
``_crests``, so each is worked out once per celebration.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_celebration.py`` fails if a read is added without
being listed here. All five scoreboards' ``SportsLive`` provide them.

- ``display_manager`` -- ``image`` is replaced with the frame, then
  ``update_display()``; ``clear()`` on ``force_clear``. Its ``matrix``
  width and height are used when it has a matrix, else ``display_width`` /
  ``display_height``.
- ``fonts`` -- ``"time"`` and ``"status"`` for the headline (the first that
  fits), ``"score"`` for the score.
- ``logger``.
- ``_load_and_resize_logo(team_id, abbr, logo_path, logo_url)`` -- a crest
  as an RGBA image, or ``None``.
- ``_draw_text_with_outline(draw, text, position, font, fill=...)`` -- on
  ``SportsCoreSharedMixin``.
- ``celebration_duration``, ``celebration_team_colors`` and
  ``celebration_confetti``, read with ``getattr`` (defaults 8, on, on).

Mix it in ahead of the mode classes, e.g.
``class SportsLive(SportsCelebrationMixin, SportsLiveSharedMixin,
SportsCore)``. It defines nothing any of them define, so the order only
matters for a plugin that keeps its own copy of one of these methods: a
method on the plugin's class always wins over the mixin's.
"""

import colorsys
import logging
import math
import random
import time
from typing import Any, Callable, ClassVar, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

#: A colour as the helpers return it: three 0-255 channels.
Color = Tuple[int, ...]
#: ``deep``, ``glow``, ``headline`` and ``accent``; see ``logo_palette``.
Palette = Dict[str, Color]
#: One confetti flake: column, start height, fall speed, sway phase, size
#: in pixels, colour.
Flake = Tuple[float, float, float, float, int, Color]

# ----------------------------------------------------------------------
# Colour helpers for the score/win celebration
#
# Module level rather than methods: they are pure, which is what makes the
# palette testable without standing up a live manager, and they are shared by
# the takeover's backdrop, confetti and text.
# ----------------------------------------------------------------------

#: The crest is sampled at this resolution. Big enough that a secondary
#: colour survives (a helmet stripe, a trim), small enough that the whole
#: sample is ~1600 pixels of pure-Python work, once per team.
_PALETTE_SAMPLE_PX = 40
#: Above this, a colour carries team identity; below it, it is a grey.
_PALETTE_VIVID_SATURATION = 0.22
#: Ignore pixels this dark -- crest outlines, drop shadows, anti-aliasing.
_PALETTE_MIN_CHANNEL = 24
#: How far apart two bins must be to count as a second, different colour.
_PALETTE_DISTINCT_DISTANCE = 90.0
#: Never bleed a lifted colour below this saturation; past it a hue stops
#: being the team's colour and starts being a pastel.
_PALETTE_MIN_SATURATION = 0.42
#: Lift a headline colour until it is at least this luminous. Chosen so
#: midnight navy reaches a blue that reads at 6px on a panel without
#: becoming a different colour.
_PALETTE_HEADLINE_LUMINANCE = 112.0
#: A crest colour this luminous already reads on a panel, so it is preferred
#: over a darker one that would have to be lifted to get there. Lifting is a
#: compromise -- Green Bay's dark green only reaches legibility as a teal --
#: and most teams whose primary is dark carry a bright second colour that is
#: just as much theirs. This is what picks the Packers' gold over that teal.
_PALETTE_LEGIBLE_LUMINANCE = 90.0
#: ...but only from a colour the crest actually means. The pixels where a
#: bright edge is anti-aliased into a dark fill are luminous too, and there is
#: always a band of them: Kansas City's white-on-red outline leaves a pink at
#: luminance 90 that would otherwise be preferred over the red itself. A blend
#: is a mix, so it is markedly less saturated than either colour it sits
#: between -- that pink is 0.48 where the red is 0.96 and the Packers' gold,
#: which this must keep, is 0.89.
_PALETTE_LEGIBLE_SATURATION = 0.65
#: And it has to be a band of the crest, not a speck of one.
_PALETTE_LEGIBLE_AREA = 0.02
#: Cap the backdrop's luminance so the headline stays legible over it,
#: and the scenery's so it stays behind the headline. Both are luminance and
#: not HSV value on purpose: a silver crest -- the Raiders, or the grey
#: placeholder a failed logo download leaves behind -- has a value of ~0.95,
#: and capping that at 0.34 still yields a light grey card that white text
#: then vanishes into. Scaling the channels down is also hue-exact, which is
#: what lets this be the plain arithmetic that lifting a colour cannot be.
_PALETTE_BACKDROP_LUMINANCE = 34.0
_PALETTE_SCENERY_LUMINANCE = 70.0


def rgb_luminance(color: Sequence[float]) -> float:
    """Rec. 709 relative luminance, 0-255."""
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def rgb_saturation(color: Sequence[float]) -> float:
    """HSV saturation, 0-1."""
    high = max(color)
    return (high - min(color)) / high if high else 0.0


def color_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Euclidean distance between two colours in RGB."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def mix_color(a: Sequence[float], b: Sequence[float], t: float) -> Color:
    """Blend ``a`` towards ``b``; t=0 is all a, t=1 is all b."""
    t = min(max(t, 0.0), 1.0)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def scale_color(color: Sequence[float], factor: float) -> Color:
    """Scale a colour's brightness, clamped to the panel's range."""
    return tuple(min(255, max(0, int(round(c * factor)))) for c in color)


def lift_color(color: Sequence[float], min_luminance: float = _PALETTE_HEADLINE_LUMINANCE,
               cap_saturation: float = 0.92) -> Color:
    """Raise a colour's brightness until it reads on a panel, keeping its hue.

    Scaling the channels directly is what the obvious version of this does,
    and it shifts hue badly on exactly the colours that need lifting: it turns
    Baltimore's navy-purple into magenta. Working in HSV and raising only the
    value leaves the hue where the team put it.
    """
    if rgb_luminance(color) >= min_luminance:
        return tuple(int(c) for c in color)
    hue, saturation, value = colorsys.rgb_to_hsv(*[c / 255.0 for c in color])
    if saturation < 0.12:
        # A grey or a silver has no hue to preserve; just make it bright.
        lifted = colorsys.hsv_to_rgb(hue, saturation, max(value, 0.85))
        return tuple(int(round(c * 255)) for c in lifted)
    saturation = min(saturation, cap_saturation)

    def _rgb(s: float, v: float) -> Color:
        return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(hue, s, v))

    out = _rgb(saturation, value)
    while value < 1.0 and rgb_luminance(out) < min_luminance:
        value = min(1.0, value + 0.05)
        out = _rgb(saturation, value)
    # Blue carries almost no luminance -- pure blue sits at 18 of 255 -- so a
    # navy or a deep purple runs out of value long before it is legible.
    # Bleeding saturation out of it is the only way up, and it keeps the hue
    # (Baltimore stays purple, just a lighter one) where giving up would
    # leave the headline unreadable. Floored so it never washes out to white.
    while saturation > _PALETTE_MIN_SATURATION and rgb_luminance(out) < min_luminance:
        saturation = max(_PALETTE_MIN_SATURATION, saturation - 0.05)
        out = _rgb(saturation, value)
    return out


def cap_luminance(color: Sequence[float], max_luminance: float) -> Color:
    """Darken a colour until it is no brighter than ``max_luminance``.

    A straight channel scale, which is exactly hue-preserving on the way down
    -- unlike lifting, where clamping at 255 is what bends the hue.
    """
    luminance = rgb_luminance(color)
    if luminance <= max_luminance or luminance <= 0:
        return tuple(int(c) for c in color)
    return scale_color(color, max_luminance / luminance)


def dim_rgba(image: Image.Image, factor: float) -> Image.Image:
    """Scale an RGBA image's colour channels, leaving its alpha alone.

    ImageEnhance.Brightness would scale the alpha band too, which fades the
    crest out instead of dimming it and leaves its anti-aliased edge looking
    chewed against the backdrop.
    """
    red, green, blue, alpha = image.split()
    lut = [min(255, int(i * factor)) for i in range(256)]
    return Image.merge(
        "RGBA", (red.point(lut), green.point(lut), blue.point(lut), alpha)
    )


_Buckets = Dict[Tuple[int, int, int], List[int]]


def _palette_buckets(logo: Image.Image) -> Tuple[_Buckets, _Buckets]:
    """Bucket a crest's opaque pixels into coarse colour bins.

    Returns ``(vivid, neutral)``; each maps a 3-bit-per-channel key to
    ``[r_sum, g_sum, b_sum, count]``. Neutral holds the greys, silvers and
    whites that carry no identity on their own but are all a monochrome crest
    -- the Raiders' silver on black -- has to offer.
    """
    sample = logo.convert("RGBA")
    sample.thumbnail((_PALETTE_SAMPLE_PX, _PALETTE_SAMPLE_PX), Image.Resampling.BOX)
    vivid: _Buckets = {}
    neutral: _Buckets = {}
    # tobytes() rather than getdata(): same pixels, no per-pixel Python
    # object, and getdata() is deprecated from Pillow 14.
    raw = sample.tobytes()
    for i in range(0, len(raw) - 3, 4):
        red, green, blue, alpha = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if alpha < 160:
            continue
        high, low = max(red, green, blue), min(red, green, blue)
        if high < _PALETTE_MIN_CHANNEL:
            continue
        target = vivid if (high - low) / high >= _PALETTE_VIVID_SATURATION else neutral
        acc = target.setdefault((red >> 5, green >> 5, blue >> 5), [0, 0, 0, 0])
        acc[0] += red
        acc[1] += green
        acc[2] += blue
        acc[3] += 1
    return vivid, neutral


def _bucket_mean(acc: List[int]) -> Color:
    count = acc[3]
    return (acc[0] // count, acc[1] // count, acc[2] // count)


def _bucket_headline_score(acc: List[int]) -> float:
    """How well a colour bin would serve as 6px of text on a panel.

    Area alone picks the biggest block of colour, which on a lot of crests is
    a dark navy fill -- correct as a backdrop, invisible as text. Weighting
    area by saturation and by luminance picks the colour the team is loud in:
    Chicago's orange over its navy, Baltimore's gold over its purple.
    """
    color = _bucket_mean(acc)
    return (
        acc[3]
        * (0.30 + 0.70 * rgb_saturation(color))
        * (0.20 + 0.80 * min(1.0, rgb_luminance(color) / 120.0))
    )


def logo_palette(logo: Image.Image) -> Optional[Palette]:
    """Pick a celebration palette out of a team crest, or None.

    Two rankings, because a crest's largest colour and its most legible one
    are usually not the same and the takeover needs both:

    * ``deep`` -- the largest vivid area, darkened into the background wash.
      This is what the team reads as at a glance: Chicago navy, Dallas navy,
      Baltimore purple.
    * ``headline`` -- the vivid area that best survives being shrunk to text,
      then lifted until it is legible: Chicago orange, Baltimore gold.
    * ``accent`` -- the next vivid colour far enough away from the headline to
      be told apart, for confetti. Falls back to the headline.

    A crest with no vivid pixels at all falls back to its brightest neutral,
    which for the Raiders' silver-on-black is exactly the right answer.
    """
    try:
        vivid, neutral = _palette_buckets(logo)
    except Exception:  # noqa: BLE001 - a crest is never worth the takeover
        return None

    pool = list(vivid.values())
    if not pool and neutral:
        pool = [
            max(
                neutral.values(),
                key=lambda acc: acc[3]
                * (0.2 + 0.8 * min(1.0, rgb_luminance(_bucket_mean(acc)) / 160.0)),
            )
        ]
    if not pool:
        return None

    deep_base = _bucket_mean(max(pool, key=lambda acc: acc[3]))
    ranked = sorted(pool, key=_bucket_headline_score, reverse=True)
    headline_base = _bucket_mean(ranked[0])
    vivid_pixels = sum(acc[3] for acc in pool)
    for acc in ranked:
        candidate = _bucket_mean(acc)
        if (
            rgb_luminance(candidate) >= _PALETTE_LEGIBLE_LUMINANCE
            and rgb_saturation(candidate) >= _PALETTE_LEGIBLE_SATURATION
            and acc[3] >= max(3, vivid_pixels * _PALETTE_LEGIBLE_AREA)
        ):
            headline_base = candidate
            break
    headline = lift_color(headline_base)

    accent = headline
    for acc in ranked[1:]:
        candidate = _bucket_mean(acc)
        if color_distance(candidate, headline_base) > _PALETTE_DISTINCT_DISTANCE:
            accent = lift_color(candidate)
            break

    deep = cap_luminance(deep_base, _PALETTE_BACKDROP_LUMINANCE)
    return {
        "deep": deep,
        # Scenery is the backdrop carried a little way towards the headline:
        # tied to the team's colours, and guaranteed to be visible even when
        # the backdrop is nearly black.
        "glow": cap_luminance(
            mix_color(deep, headline, 0.22), _PALETTE_SCENERY_LUMINANCE
        ),
        "headline": headline,
        "accent": accent,
    }


class SportsCelebrationMixin:
    """Draws a score/win celebration takeover. See the module docstring."""

    # The host contract, declared for type checking only: these create no
    # attributes, so the host's own values are what the methods read.
    display_manager: Any
    display_width: int
    display_height: int
    fonts: Dict[str, Any]
    logger: logging.Logger
    _load_and_resize_logo: Callable[..., Optional[Image.Image]]
    _draw_text_with_outline: Callable[..., None]

    def _fit_font(self, draw, text: str, max_width: int, fonts: list):
        """Return the first font whose rendered ``text`` fits ``max_width``,
        falling back to the last (smallest) font."""
        for font in fonts:
            if draw.textlength(text, font=font) <= max_width - 2:
                return font
        return fonts[-1]

    # ------------------------------------------------------------------
    # Celebration palette
    #
    # The takeover is drawn in the scoring team's own colours, taken from the
    # pixels of its crest.
    #
    # ESPN does serve team.color / team.alternateColor, but only inside
    # _extract_game_details_common -- a function each scoreboard lineage
    # keeps its own copy of -- so reading it there would drag every one of
    # them into a celebration change. The crest is already downloaded,
    # decoded and sitting in the logo cache by the time a celebration draws,
    # so the colours come from it instead: no extra request, no per-league
    # colour table to maintain, and it works for any team ESPN can name --
    # including the FCS opponents no table would list.
    #
    # Where a crest's colour differs from the club's published one it
    # tends to differ usefully: a published primary is often a near-black
    # navy, or an actual #000000, where what the crest carries is the colour
    # that reads on an LED panel. Measured across all 32 clubs in
    # football-scoreboard.
    # ------------------------------------------------------------------

    #: Used when the crest yields nothing (no logo on disk yet, or the grey
    #: placeholder a failed download leaves) or team colours are switched
    #: off -- the navy and amber the celebration wore before it had a palette.
    _DEFAULT_CELEBRATION_PALETTE: ClassVar[Palette] = {
        "deep": (10, 10, 40),
        "glow": (30, 30, 86),
        "headline": (255, 208, 56),
        "accent": (255, 255, 255),
    }

    def _celebration_palette(self, celebration: Dict) -> Palette:
        """The scoring team's colours, derived once per celebration."""
        cached: Optional[Palette] = celebration.get("_palette")
        if cached is not None:
            return cached

        palette = dict(self._DEFAULT_CELEBRATION_PALETTE)
        if getattr(self, "celebration_team_colors", True):
            try:
                game = celebration["game"]
                side = celebration.get("scored_side") or "home"
                logo = self._load_and_resize_logo(
                    game.get("%s_id" % side),
                    game.get("%s_abbr" % side),
                    game.get("%s_logo_path" % side),
                    game.get("%s_logo_url" % side),
                )
                derived = logo_palette(logo) if logo is not None else None
                if derived:
                    palette = derived
            except Exception as e:  # noqa: BLE001 - never lose a takeover to a crest
                self.logger.debug(f"Celebration palette fell back to the default: {e}")

        celebration["_palette"] = palette
        return palette

    # ------------------------------------------------------------------
    # Celebration choreography
    #
    # Every frame is a finished card. The beats below shift the emphasis --
    # an opening colour hit, confetti, a breathing score -- but none of them
    # leaves the panel mid-wipe, because on a switch-mode board the core
    # drives this plugin at 1 FPS (display_controller reserves its high-FPS
    # loop for plugins that scroll or declare needs_high_fps), so any single
    # frame may be the only one a viewer ever sees of it.
    # ------------------------------------------------------------------

    #: Fraction of the celebration spent on the opening colour hit.
    _CELEBRATION_IMPACT: ClassVar[float] = 0.11
    #: Fraction of it after which the takeover eases back down.
    _CELEBRATION_SETTLE: ClassVar[float] = 0.80
    #: Seconds per breath of the scoring side's digits. Deliberately a
    #: continuous sine rather than an on/off toggle: the 4 Hz flash this
    #: replaced was sampled once a second on a switch-mode board, which
    #: aliases into a colour that changes at random. A ramp degrades into a
    #: slow glow instead, and still reads as a pulse at 125 FPS.
    _CELEBRATION_BREATH_SECONDS: ClassVar[float] = 1.7

    def _celebration_backdrop(
        self,
        celebration: Dict,
        width: int,
        height: int,
        palette: Palette,
    ) -> Image.Image:
        """The static half of the takeover: a team-colour gradient with the
        scenery for this kind of score painted into it.

        Built once per celebration per panel size and copied per frame, so the
        per-pixel work never lands on the render path.
        """
        cached: Optional[Tuple[Tuple[int, int], Image.Image]] = celebration.get("_backdrop")
        if cached is not None and cached[0] == (width, height):
            return cached[1]

        # One column, then stretched: filling the panel pixel by pixel would
        # be `width` times the work for the same image.
        column = Image.new("RGB", (1, max(height, 1)))
        pixels: Any = column.load()
        for y in range(height):
            k = y / max(height - 1, 1)
            pixels[0, y] = mix_color(palette["deep"], (0, 0, 0), 0.18 + 0.82 * k)
        backdrop = column.resize((width, height)).convert("RGBA")

        try:
            self._draw_celebration_motif(
                ImageDraw.Draw(backdrop),
                celebration.get("motif") or "score",
                width,
                height,
                palette,
            )
        except Exception as e:  # noqa: BLE001 - scenery is never worth a blank panel
            self.logger.debug(f"Celebration motif skipped: {e}")

        celebration["_backdrop"] = ((width, height), backdrop)
        return backdrop

    def _draw_celebration_motif(
        self,
        draw,
        motif: str,
        width: int,
        height: int,
        palette: Palette,
    ) -> None:
        """Paint the scenery for one kind of score, dim enough to stay behind
        the headline and the score instead of competing with them."""
        glow = palette["glow"]
        if motif == "kick":
            # The uprights a field goal or an extra point went through,
            # spread wide enough to frame the score rather than sit beside it.
            half = max(8, min(width // 3, height))
            mid = width // 2
            crossbar = int(height * 0.60)
            draw.line([(mid - half, int(height * 0.08)), (mid - half, crossbar)], fill=glow)
            draw.line([(mid + half, int(height * 0.08)), (mid + half, crossbar)], fill=glow)
            draw.line([(mid - half, crossbar), (mid + half, crossbar)], fill=glow)
            draw.line([(mid, crossbar), (mid, height - 1)], fill=glow)
        elif motif == "touchdown":
            # The goal line, with its hash marks.
            line_y = int(height * 0.36)
            draw.line([(0, line_y), (width, line_y)], fill=glow)
            for x in range(3, width, 9):
                draw.line([(x, line_y - 2), (x, line_y + 2)], fill=glow)
        elif motif == "net":
            # The goal a puck just went into: frame, posts and mesh, sized to
            # frame the score the way the uprights do.
            half = max(7, min(width // 4, height))
            mid = width // 2
            top = int(height * 0.34)
            draw.rectangle([(mid - half, top), (mid + half, height - 1)], outline=glow)
            step = max(3, (half * 2) // 6)
            for x in range(mid - half + step, mid + half, step):
                draw.line([(x, top + 1), (x, height - 2)], fill=glow)
            for y in range(top + step, height - 1, step):
                draw.line([(mid - half + 1, y), (mid + half - 1, y)], fill=glow)
        elif motif == "win":
            # A sunburst behind the winner.
            cx, cy = width // 2, height // 2
            reach = max(width, height)
            for i in range(10):
                angle = (math.pi * 2 * i / 10) + math.pi / 20
                draw.line(
                    [
                        (cx, cy),
                        (cx + math.cos(angle) * reach, cy + math.sin(angle) * reach),
                    ],
                    fill=glow,
                )
        else:
            for x in range(-height, width + height, 11):
                draw.line([(x, height), (x + height, 0)], fill=glow)

    def _celebration_confetti(
        self,
        celebration: Dict,
        width: int,
        height: int,
        palette: Palette,
    ) -> List[Flake]:
        """Seed the confetti once per celebration.

        Seeded from the game rather than the clock, so the same score always
        produces the same fall -- which is what lets a golden screen lock the
        effect down instead of having to tolerate it.
        """
        cached: Optional[Tuple[Tuple[int, int], List[Flake]]] = celebration.get("_confetti")
        if cached is not None and cached[0] == (width, height):
            return cached[1]

        # Sparse on purpose. At one flake per 170 square pixels a 128x32
        # panel carried 24 single-pixel specks over the headline and the
        # score, which reads as a dead-pixel problem rather than as confetti.
        count = max(6, min(22, (width * height) // 260))
        seed = "%s/%s" % (
            (celebration.get("game") or {}).get("id", "?"),
            celebration.get("phrase", ""),
        )
        rng = random.Random(seed)  # nosec B311 - confetti, not security
        # Team colours, plus a pale tint of the headline rather than a flat
        # white, so the fall still belongs to the team that scored.
        colors = [
            palette["headline"],
            palette["accent"],
            mix_color(palette["headline"], (255, 255, 255), 0.55),
        ]
        flakes = [
            (
                float(rng.randrange(max(width, 1))),  # column
                rng.uniform(0.0, float(height)),      # start height
                rng.uniform(0.40, 1.15),              # fall speed
                rng.uniform(0.0, math.pi * 2),        # sway phase
                2 if rng.random() < 0.6 else 1,       # size in pixels
                colors[rng.randrange(len(colors))],
            )
            for _ in range(count)
        ]
        celebration["_confetti"] = ((width, height), flakes)
        return flakes

    def _draw_celebration_confetti(
        self,
        draw,
        celebration: Dict,
        width: int,
        height: int,
        palette: Palette,
        elapsed: float,
        progress: float,
    ) -> None:
        """Draw the confetti for this instant, thinning it out as the
        celebration eases back towards the scorebug."""
        flakes = self._celebration_confetti(celebration, width, height, palette)
        fade = 1.0
        if progress > self._CELEBRATION_SETTLE:
            fade = max(
                0.0,
                1.0
                - (progress - self._CELEBRATION_SETTLE)
                / (1.0 - self._CELEBRATION_SETTLE),
            )
        if fade <= 0.02:
            return
        alpha = int(235 * fade)
        for column, start, speed, phase, size, color in flakes:
            y = (start + speed * elapsed * height * 0.42) % (height + 4) - 2
            x = column + math.sin(elapsed * 2.1 + phase) * 2.4
            draw.rectangle(
                [(int(x), int(y)), (int(x) + size - 1, int(y) + size - 1)],
                fill=tuple(color) + (alpha,),
            )

    def _celebration_crests(
        self, celebration: Dict, height: int
    ) -> Dict[str, Optional[Image.Image]]:
        """The two crests for the takeover, with the side that did not score
        dimmed so the scoring team reads at a glance."""
        cached: Optional[Tuple[int, Dict[str, Optional[Image.Image]]]] = celebration.get("_crests")
        if cached is not None and cached[0] == height:
            return cached[1]

        game = celebration["game"]
        scored = celebration.get("scored_side")
        crests: Dict[str, Optional[Image.Image]] = {}
        for side in ("away", "home"):
            logo = None
            try:
                logo = self._load_and_resize_logo(
                    game.get("%s_id" % side),
                    game.get("%s_abbr" % side),
                    game.get("%s_logo_path" % side),
                    game.get("%s_logo_url" % side),
                )
            except Exception as e:  # noqa: BLE001 - a crest is never worth the panel
                self.logger.debug(f"Celebration logo load failed: {e}")
            if logo is not None and side != scored:
                logo = dim_rgba(logo, 0.40)
            crests[side] = logo

        celebration["_crests"] = (height, crests)
        return crests

    def _draw_celebration_layout(self, celebration: Dict, force_clear: bool = False) -> None:
        """Render the full-screen goal/win takeover."""
        if force_clear:
            self.display_manager.clear()

        display_width = (
            self.display_manager.matrix.width
            if hasattr(self.display_manager, "matrix") and self.display_manager.matrix
            else self.display_width
        )
        display_height = (
            self.display_manager.matrix.height
            if hasattr(self.display_manager, "matrix") and self.display_manager.matrix
            else self.display_height
        )

        elapsed = max(0.0, time.time() - celebration["started_at"])
        # getattr throughout the render path: the golden-screen tests build a
        # live manager through __new__ and set only what they draw with, and a
        # celebration must never be lost to a missing knob.
        duration = max(float(getattr(self, "celebration_duration", 8) or 8), 0.5)
        progress = min(elapsed / duration, 1.0)
        palette = self._celebration_palette(celebration)

        main_img = self._celebration_backdrop(
            celebration, display_width, display_height, palette
        ).copy()

        # Crests at the edges, bleeding off as the scorebug's do.
        crests = self._celebration_crests(celebration, display_height)
        center_y = display_height // 2
        home_logo, away_logo = crests.get("home"), crests.get("away")
        if home_logo is not None:
            main_img.paste(
                home_logo,
                (display_width - home_logo.width + 2, center_y - home_logo.height // 2),
                home_logo,
            )
        if away_logo is not None:
            main_img.paste(away_logo, (-2, center_y - away_logo.height // 2), away_logo)

        # The opening hit: the team's headline colour washes the panel and
        # decays out of it. Held below opaque so the outlined text drawn on
        # top still reads in whichever frame happens to catch it.
        impact = max(0.0, 1.0 - progress / self._CELEBRATION_IMPACT)
        if impact > 0.0:
            # Scaled by how colourful the team is. A saturated crest gets the
            # full hit; a silver one -- the Raiders, or the grey placeholder a
            # failed logo download leaves -- would otherwise wash the whole
            # panel out to the same flat grey as its own headline colour.
            punch = 0.45 + 0.55 * rgb_saturation(palette["headline"])
            alpha = int(140 * punch * (impact ** 1.5))
            if alpha > 0:
                main_img = Image.alpha_composite(
                    main_img,
                    Image.new(
                        "RGBA",
                        (display_width, display_height),
                        tuple(palette["headline"]) + (alpha,),
                    ),
                )

        overlay = Image.new("RGBA", (display_width, display_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        if getattr(self, "celebration_confetti", True):
            try:
                self._draw_celebration_confetti(
                    draw,
                    celebration,
                    display_width,
                    display_height,
                    palette,
                    elapsed,
                    progress,
                )
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Celebration confetti skipped: {e}")

        # Headline across the top, shrunk to fit the panel width, struck
        # white on the opening hit and settling into the team's colour.
        phrase = celebration["phrase"]
        phrase_font = self._fit_font(
            draw, phrase, display_width, [self.fonts["time"], self.fonts["status"]]
        )
        phrase_width = draw.textlength(phrase, font=phrase_font)
        # Eased in by colour rather than by position. Sliding it down into
        # place put the first frame at y=-3 with its top row cut off, and on a
        # 1 FPS board that clipped frame can be the only one anyone sees.
        self._draw_text_with_outline(
            draw,
            phrase,
            ((display_width - phrase_width) // 2, 1),
            phrase_font,
            fill=mix_color(palette["headline"], (255, 255, 255), impact),
        )

        # Score centred low, the scoring side's digits breathing in the team's
        # headline colour so the change reads at a glance.
        away_text = str(celebration["away_score"])
        home_text = str(celebration["home_score"])
        score_font = self.fonts["score"]
        segments = [
            (away_text, celebration["scored_side"] == "away"),
            ("-", False),
            (home_text, celebration["scored_side"] == "home"),
        ]
        total_width = sum(draw.textlength(seg, font=score_font) for seg, _ in segments)
        breath = 0.72 + 0.28 * (
            0.5
            + 0.5 * math.sin(2 * math.pi * elapsed / self._CELEBRATION_BREATH_SECONDS)
        )
        highlight = scale_color(palette["headline"], breath)
        x = (display_width - total_width) // 2
        # display_height - 14 was sized for the old fixed 8px score. #338
        # scales the score with the panel (16px at 48 and 64 tall), which put
        # the bottom of the digits off the panel. Lift it by the measured ink
        # (+1 for the outline stroke) only when it would clip, so panels where
        # it always fitted render exactly as before.
        score_text = "".join(seg for seg, _ in segments)
        ink_bottom = draw.textbbox((0, 0), score_text, font=score_font)[3]
        y = min(display_height - 14, display_height - ink_bottom - 2)
        for seg, is_highlight in segments:
            color = highlight if is_highlight else (216, 216, 216)
            self._draw_text_with_outline(draw, seg, (int(x), y), score_font, fill=color)
            x += draw.textlength(seg, font=score_font)

        main_img = Image.alpha_composite(main_img, overlay).convert("RGB")
        self.display_manager.image = main_img
        self.display_manager.update_display()
