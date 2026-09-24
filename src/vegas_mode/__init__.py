"""
Vegas Mode - Continuous Scrolling Ticker

This package implements a Vegas-style continuous scroll mode where all enabled
plugins' content is composed into a single horizontally scrolling display.

Components:
- VegasModeCoordinator: Main orchestrator for Vegas mode
- StreamManager: Plugin rotation, content fetching and pending-update tracking
- RenderPipeline: Strip composition and per-frame rendering
- PluginAdapter: Converts plugin content to scrollable images
- VegasModeConfig: Configuration management
"""

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.coordinator import VegasModeCoordinator

__all__ = [
    'VegasModeConfig',
    'VegasModeCoordinator',
]
