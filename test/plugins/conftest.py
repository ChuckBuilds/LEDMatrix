"""
Pytest fixtures for plugin integration tests.
"""

import pytest
import os
import sys
import json
from pathlib import Path
from typing import Any, Dict

# Add project root to path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Set emulator mode
os.environ['EMULATOR'] = 'true'


def load_plugin_manifest(plugin_id: str, plugins_dir: Path) -> Dict[str, Any]:
    """Load plugin manifest.json."""
    manifest_path = plugins_dir / plugin_id / 'manifest.json'
    if not manifest_path.exists():
        pytest.skip(f"Manifest not found for {plugin_id}")
    
    with open(manifest_path, 'r') as f:
        return json.load(f)


def get_plugin_config_schema(plugin_id: str, plugins_dir: Path) -> Dict[str, Any]:
    """Load plugin config_schema.json if it exists."""
    schema_path = plugins_dir / plugin_id / 'config_schema.json'
    if schema_path.exists():
        with open(schema_path, 'r') as f:
            return json.load(f)
    return None


@pytest.fixture
def visual_display_manager() -> Any:
    """Create a VisualTestDisplayManager that renders real pixels for visual testing."""
    from src.plugin_system.testing import VisualTestDisplayManager
    return VisualTestDisplayManager(width=128, height=32)
