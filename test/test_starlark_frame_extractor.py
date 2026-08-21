"""Animated WebP timing regression tests for Starlark playback."""

import importlib.util
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "plugin-repos/starlark-apps/frame_extractor.py"
spec = importlib.util.spec_from_file_location("test_starlark_frame_extractor_module", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _write_animated_webp(path, durations):
    frames = [Image.new("RGB", (2, 2), (index * 40, 0, 0))
              for index in range(len(durations))]
    frames[0].save(
        path,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        lossless=True,
    )


def test_animated_webp_preserves_each_embedded_frame_duration(tmp_path):
    webp = tmp_path / "timed.webp"
    expected = [40, 120, 250]
    _write_animated_webp(webp, expected)

    success, frames, error = module.FrameExtractor(default_frame_delay=999).load_webp(str(webp))

    assert success, error
    assert [duration for _, duration in frames] == expected


def test_default_delay_is_used_only_without_usable_embedded_duration(tmp_path):
    static_webp = tmp_path / "static.webp"
    Image.new("RGB", (2, 2), "red").save(static_webp, format="WEBP", lossless=True)

    success, frames, error = module.FrameExtractor(default_frame_delay=73).load_webp(
        str(static_webp))

    assert success, error
    assert len(frames) == 1
    assert frames[0][1] == 73
