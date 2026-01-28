"""
Vegas Mode - Continuous Scrolling Ticker

This package implements a Vegas-style continuous scroll mode where all enabled
plugins' content is composed into a single horizontally scrolling display.

Components:
- VegasModeCoordinator: Main orchestrator for Vegas mode
- StreamManager: Manages plugin content streaming with 1-2 ahead buffering
- RenderPipeline: Handles 125 FPS rendering with double-buffering
- PluginAdapter: Converts plugin content to scrollable images
- VegasModeConfig: Configuration management
"""

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.coordinator import VegasModeCoordinator

__all__ = [
    'VegasModeConfig',
    'VegasModeCoordinator',
]
