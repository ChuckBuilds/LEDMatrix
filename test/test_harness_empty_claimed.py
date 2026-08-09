"""Tests for the harness empty-frame check (src/plugin_system/testing/harness.py).

The display controller skips a mode whose display() returns False and treats
anything else -- including None -- as "content was shown". A mode that draws
nothing without returning False is therefore never skipped, and since a mode
switch clears the panel first, it sits on a blank screen for its whole display
duration.

Two sports plugins shipped exactly that: their display() returned None on every
path, so an out-of-season league held a blank panel instead of being rotated
past. The harness rendered those modes and passed them, because it discarded
the return value entirely.
"""

from PIL import Image

from src.plugin_system.testing.harness import RenderResult, check_empty_claimed


def _blank(w=64, h=32):
    return Image.new("RGB", (w, h), (0, 0, 0))


def _drawn(w=64, h=32):
    img = _blank(w, h)
    img.paste(Image.new("RGB", (10, 10), (255, 255, 255)), (5, 5))
    return img


def _result(image, returned=None, **kw):
    return RenderResult("p", 64, 32, "mode", image=image,
                        display_returned=returned, **kw)


class TestCheckEmptyClaimed:
    def test_blank_frame_returning_none_is_flagged(self):
        # The shape that shipped: nothing drawn, nothing reported.
        r = _result(_blank(), returned=None)
        check_empty_claimed([r])
        assert r.empty_claimed is True

    def test_blank_frame_returning_true_is_flagged(self):
        # Just as broken, and more explicit about it.
        r = _result(_blank(), returned=True)
        check_empty_claimed([r])
        assert r.empty_claimed is True

    def test_blank_frame_returning_false_is_fine(self):
        # The plugin correctly said "no content"; the controller will skip it.
        r = _result(_blank(), returned=False)
        check_empty_claimed([r])
        assert r.empty_claimed is None

    def test_a_drawn_frame_is_fine_whatever_it_returns(self):
        for returned in (None, True, False):
            r = _result(_drawn(), returned=returned)
            check_empty_claimed([r])
            assert r.empty_claimed is None, returned

    def test_near_black_still_counts_as_drawn(self):
        # Guard the threshold: content dim enough to look black to the eye is
        # still content, and flagging it would train people to ignore this.
        img = _blank()
        img.paste(Image.new("RGB", (4, 4), (60, 60, 60)), (2, 2))
        r = _result(img, returned=None)
        check_empty_claimed([r])
        assert r.empty_claimed is None


class TestWarnVersusStrict:
    def test_warn_only_by_default(self):
        # A scroll mode's first frame is legitimately its blank scroll-in
        # buffer, so this must not fail a run unless opted in.
        r = _result(_blank(), returned=None)
        check_empty_claimed([r])
        assert r.empty_ok is None
        assert r.ok is True

    def test_strict_fails_the_result(self):
        r = _result(_blank(), returned=None)
        check_empty_claimed([r], strict=True)
        assert r.empty_ok is False
        assert r.ok is False

    def test_strict_still_allows_an_honest_false(self):
        r = _result(_blank(), returned=False)
        check_empty_claimed([r], strict=True)
        assert r.empty_ok is None
        assert r.ok is True


class TestSkippedResults:
    def test_a_crashed_render_is_left_alone(self):
        # error already fails the result; adding a second reason just muddies
        # the report.
        r = _result(None, returned=None, error="boom")
        check_empty_claimed([r], strict=True)
        assert r.empty_claimed is None

    def test_a_result_with_no_image_is_left_alone(self):
        r = _result(None, returned=None)
        check_empty_claimed([r], strict=True)
        assert r.empty_claimed is None
