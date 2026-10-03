"""
LEDMatrix Plugin System

This module provides the core plugin infrastructure for the LEDMatrix project.
It enables dynamic loading, management, and discovery of display plugins.

BasePlugin and PluginManager are imported on first use (PEP 562), not when
the package is imported: the web interface imports several submodules
(store_manager, schema_manager, ...) and never needs PluginManager, which
pulls in the loader, executor and the shared helpers behind them.
``from src.plugin_system import BasePlugin`` works as before and returns the
same class.
"""

import importlib
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

__version__ = "1.0.0"

if TYPE_CHECKING:
    from .base_plugin import BasePlugin
    from .plugin_manager import PluginManager

#: Exported name -> (module it lives in, attribute name there).
_LAZY: Dict[str, Tuple[str, str]] = {
    'BasePlugin': ('src.plugin_system.base_plugin', 'BasePlugin'),
    'PluginManager': ('src.plugin_system.plugin_manager', 'PluginManager'),
}

__all__ = [
    'BasePlugin',
    'PluginManager',
]


def __getattr__(name: str) -> Any:
    """Import an exported name on first access (PEP 562); see src.common."""
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name), attr)  # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import -- module_name comes from the fixed _LAZY table
    globals()[name] = value
    return value


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(__all__))
