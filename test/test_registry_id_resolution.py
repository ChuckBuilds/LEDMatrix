"""A plugin must be findable in the registry by the id it calls itself.

Four shipped plugins have a registry ``id`` that differs from the ``id`` in
their own ``manifest.json``:

    directory / manifest.json id      registry id
    ledmatrix-weather                 weather
    ledmatrix-stocks                  stocks
    ledmatrix-music                   music
    ledmatrix-leaderboard             leaderboard

The installer already knows about this: it deliberately names the install
directory after the *manifest* id (store_manager, "Use manifest ID for
directory name"), and warns when the two disagree. So on disk, in
``config.json`` and in a backup manifest, these plugins are called
``ledmatrix-weather``. Only the registry calls them ``weather``.

Nothing resolved that in reverse. Asking the store to install
``ledmatrix-weather`` -- which is exactly what restoring a backup does --
failed with "Plugin not found in registry", and four enabled plugins went
missing from a restored device with no error surfaced to the user.

Renaming the registry ids would orphan existing ``plugin_state.json`` entries
keyed on the old ones, so the lookup resolves ``plugin_path`` instead: the
registry already records ``plugins/ledmatrix-weather``, which is unambiguous
and needs no published identity to change.
"""

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.store_manager import PluginStoreManager  # noqa: E402


# Shaped like the real registry: id and plugin_path basename disagree for the
# first entry, agree for the second.
REGISTRY: Dict[str, List[Dict[str, Any]]] = {
    "plugins": [
        {
            "id": "weather",
            "name": "Weather",
            "plugin_path": "plugins/ledmatrix-weather",
            "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
        },
        {
            "id": "ledmatrix-flights",
            "name": "Flights",
            "plugin_path": "plugins/ledmatrix-flights",
            "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
        },
        {
            "id": "third-party",
            "name": "Third Party",
            "plugin_path": "",
            "repo": "https://github.com/someone/thing",
        },
    ]
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> PluginStoreManager:
    manager = PluginStoreManager.__new__(PluginStoreManager)
    monkeypatch.setattr(manager, "fetch_registry", lambda *a, **k: REGISTRY, raising=False)
    return manager


def _ids(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    return entry.get("id") if entry else None


class TestRegistryLookupByManifestId:
    def test_exact_registry_id_still_resolves(self, store: PluginStoreManager) -> None:
        assert _ids(store.get_registry_info("weather")) == "weather"

    def test_manifest_id_resolves_via_plugin_path(self, store: PluginStoreManager) -> None:
        """The case that broke restore: asked by the name on disk."""
        assert _ids(store.get_registry_info("ledmatrix-weather")) == "weather", (
            "a plugin installed as 'ledmatrix-weather' could not be found in a "
            "registry that lists it under plugin_path plugins/ledmatrix-weather")

    def test_matching_id_and_path_unaffected(self, store: PluginStoreManager) -> None:
        assert _ids(store.get_registry_info("ledmatrix-flights")) == "ledmatrix-flights"

    def test_unknown_plugin_still_returns_none(self, store: PluginStoreManager) -> None:
        assert store.get_registry_info("no-such-plugin") is None

    def test_empty_plugin_path_is_not_a_wildcard(self, store: PluginStoreManager) -> None:
        """Third-party entries carry plugin_path "" — that must not match ""."""
        assert store.get_registry_info("") is None

    def test_exact_id_wins_over_a_path_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If some other entry's path collides with a real id, id wins."""
        registry = {
            "plugins": [
                {"id": "decoy", "plugin_path": "plugins/weather"},
                {"id": "weather", "plugin_path": "plugins/ledmatrix-weather"},
            ]
        }
        manager = PluginStoreManager.__new__(PluginStoreManager)
        monkeypatch.setattr(manager, "fetch_registry", lambda *a, **k: registry, raising=False)
        assert _ids(manager.get_registry_info("weather")) == "weather"
