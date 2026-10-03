"""The web process reads plugins; only the display process runs them.

The web interface used to build its own PluginManager and load plugins into
the web process: store installs and updates loaded or reloaded a web-side
copy, and config saves and enable/disable called on_config_change,
on_enable and on_disable on it. None of that reached the panel -- the
display process runs its own instances -- and the web then reported
"runtime" state from copies nothing displayed. An update in particular
looked applied while the display kept running the old code until it
restarted, and nothing said so.

Now the web process has a PluginCatalog (manifests, schemas, config,
installed versions) and nothing that can run a plugin:

- every route a user drives for a plugin works without importing the
  plugin's module at all -- the plugin below records any import and any
  lifecycle call to a file, and the file must never appear;
- the catalog reads what is really installed, in plugin-repos/ and in the
  test fixtures;
- a store install, update or uninstall answers ``restart_required`` exactly
  when the running display will not pick the change up by itself.
"""

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_manager import ConfigManager  # noqa: E402
from src.plugin_system.plugin_catalog import (  # noqa: E402
    PluginCatalog, display_restart_required,
)
from src.plugin_system.schema_manager import SchemaManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_module  # noqa: F401,E402

PLUGIN_ID = "tripwire"

# A plugin that leaves evidence of being run: importing its module, building
# it, or calling any lifecycle hook appends a line to TRIPWIRE_LOG.
MANAGER_PY = '''
import os
LOG = os.environ.get("TRIPWIRE_LOG") or {log!r}

def _note(what):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(what + "\\n")

_note("imported")

from src.plugin_system.base_plugin import BasePlugin


class TripwirePlugin(BasePlugin):
    def __init__(self, *args, **kwargs):
        _note("instantiated")
        super().__init__(*args, **kwargs)

    def update(self):
        _note("update")

    def display(self, force_clear=False):
        _note("display")

    def on_config_change(self, new_config):
        _note("on_config_change")

    def on_enable(self):
        _note("on_enable")

    def on_disable(self):
        _note("on_disable")
'''

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "message": {"type": "string", "default": "hello"},
    },
}


def _write_plugin(plugins_dir, log, version="1.0.0", plugin_id=PLUGIN_ID):
    plugin_dir = plugins_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.json").write_text(json.dumps({
        "id": plugin_id, "name": "Tripwire", "version": version,
        "entry_point": "manager.py", "class_name": "TripwirePlugin",
        "display_modes": ["tripwire"],
    }), encoding="utf-8")
    (plugin_dir / "config_schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (plugin_dir / "manager.py").write_text(MANAGER_PY.format(log=str(log)), encoding="utf-8")
    return plugin_dir


class Web:
    """The api_v3 and pages_v3 blueprints over a real catalog and config."""

    def __init__(self, api_v3_module, tmp_path, monkeypatch, enabled=True):
        self.log = tmp_path / "tripwire.log"
        monkeypatch.setenv("TRIPWIRE_LOG", str(self.log))
        self.plugins_dir = tmp_path / "plugin-repos"
        self.plugins_dir.mkdir()
        _write_plugin(self.plugins_dir, self.log)

        self.config_file = tmp_path / "config.json"
        self.config_file.write_text(json.dumps(
            {PLUGIN_ID: {"enabled": enabled, "message": "hi"}}), encoding="utf-8")
        self.config_manager = ConfigManager(
            config_path=str(self.config_file),
            secrets_path=str(tmp_path / "config_secrets.json"))
        self.config_manager.template_path = str(tmp_path / "no-template.json")
        self.schema_manager = SchemaManager(plugins_dir=self.plugins_dir, project_root=tmp_path,
                                            config_manager=self.config_manager)
        self.catalog = PluginCatalog(self.plugins_dir, self.config_manager, self.schema_manager)

        api = self.api = api_v3_module.api_v3
        api.config_manager = self.config_manager
        api.schema_manager = self.schema_manager
        api.plugin_catalog = self.catalog
        store = api.plugin_store_manager
        store.plugins_dir = str(self.plugins_dir)
        store.get_registry_info.return_value = None
        store.get_plugin_info.return_value = None
        store._get_local_git_info.return_value = None
        store.install_plugin.return_value = True
        store.uninstall_plugin.return_value = True
        store.update_plugin.return_value = True

        from web_interface.blueprints import pages_v3 as pages_module
        pages = pages_module.pages_v3
        for name, value in (("config_manager", self.config_manager),
                            ("schema_manager", self.schema_manager),
                            ("plugin_catalog", self.catalog)):
            monkeypatch.setattr(pages, name, value, raising=False)

        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "web_interface" / "templates"))
        app.config["TESTING"] = True
        app.register_blueprint(api, url_prefix="/api/v3")
        app.register_blueprint(pages, url_prefix="")
        self.client = app.test_client()

    def ran(self):
        """What the plugin recorded: [] when none of its code ever ran."""
        return self.log.read_text(encoding="utf-8").split() if self.log.exists() else []

    def stored(self):
        return json.loads(self.config_file.read_text(encoding="utf-8")).get(PLUGIN_ID, {})

    def post(self, url, body):
        response = self.client.post(url, json=body)
        assert response.status_code == 200, (url, response.get_json())
        return response.get_json()


@pytest.fixture
def web(api_v3_module, tmp_path, monkeypatch):
    return Web(api_v3_module, tmp_path, monkeypatch)


@pytest.fixture
def disabled_web(api_v3_module, tmp_path, monkeypatch):
    return Web(api_v3_module, tmp_path, monkeypatch, enabled=False)


def _bump_version(web, version):
    """What a store update does to the files on disk."""
    def update(plugin_id):
        _write_plugin(web.plugins_dir, web.log, version=version, plugin_id=plugin_id)
        return True
    web.api.plugin_store_manager.update_plugin.side_effect = update


class TestTheWebProcessNeverRunsAPlugin:
    """Every plugin route, against a plugin that reports being run."""

    def test_the_tripwire_works(self, web, tmp_path):
        # Sanity: importing the module does leave the mark this suite checks for.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tripwire_probe", web.plugins_dir / PLUGIN_ID / "manager.py")
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        assert web.ran() == ["imported"]

    def test_listing_the_installed_plugins(self, web):
        body = web.client.get("/api/v3/plugins/installed").get_json()
        entry = next(p for p in body["data"]["plugins"] if p["id"] == PLUGIN_ID)
        assert entry["version"] == "1.0.0"
        assert entry["enabled"] is True
        # No display has published a runtime snapshot here, so these are
        # unknown rather than invented (test_plugin_runtime_snapshot.py
        # covers a live one).
        assert entry["loaded"] is None and entry["state"] is None
        assert body["data"]["runtime"]["status"] == "unknown"
        # Nothing in its files declares a participation; the display derives
        # one from its hooks, which are not called here.
        assert (entry["vegas_participation"], entry["vegas_participation_source"]) == (
            None, "runtime")
        assert web.ran() == []

    def test_enabling_and_disabling(self, web):
        web.post("/api/v3/plugins/toggle", {"plugin_id": PLUGIN_ID, "enabled": False})
        assert web.stored()["enabled"] is False
        web.post("/api/v3/plugins/toggle", {"plugin_id": PLUGIN_ID, "enabled": True})
        assert web.stored()["enabled"] is True
        assert web.ran() == []

    def test_saving_its_config(self, web):
        web.post("/api/v3/plugins/config",
                 {"plugin_id": PLUGIN_ID, "config": {"message": "changed"}})
        assert web.stored()["message"] == "changed"
        assert web.ran() == []

    def test_saving_its_section_through_the_main_config(self, web):
        body = web.post("/api/v3/config/main", {PLUGIN_ID: {"message": "via main"}})
        assert web.stored()["message"] == "via main"
        assert body["restart_required"] is True
        assert web.ran() == []

    def test_resetting_its_config(self, web):
        web.post("/api/v3/plugins/config/reset", {"plugin_id": PLUGIN_ID})
        assert web.stored()["message"] == "hello"
        assert web.ran() == []

    def test_rendering_its_settings_page(self, web):
        response = web.client.get(f"/partials/plugin-config/{PLUGIN_ID}")
        assert response.status_code == 200
        assert "Tripwire" in response.get_data(as_text=True)
        assert web.ran() == []

    def test_updating_it(self, web):
        _bump_version(web, "1.1.0")
        body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["data"]["update_status"] == "updated"
        assert web.catalog.get_installed_version(PLUGIN_ID) == "1.1.0"
        assert web.ran() == []

    def test_installing_it(self, web):
        web.post("/api/v3/plugins/install", {"plugin_id": PLUGIN_ID})
        assert PLUGIN_ID in web.catalog.plugin_manifests
        assert web.ran() == []

    def test_uninstalling_it(self, web):
        web.post("/api/v3/plugins/uninstall", {"plugin_id": PLUGIN_ID})
        web.api.plugin_store_manager.uninstall_plugin.assert_called_once_with(PLUGIN_ID)
        assert web.ran() == []

    def test_no_plugin_module_is_registered_in_this_process(self, web):
        web.client.get("/api/v3/plugins/installed")
        web.post("/api/v3/plugins/toggle", {"plugin_id": PLUGIN_ID, "enabled": True})
        assert not [name for name, mod in list(sys.modules.items())
                    if str(web.plugins_dir) in str(getattr(mod, "__file__", "") or "")]

    def test_the_web_app_has_a_catalog_and_no_plugin_manager(self):
        """app.py wires a PluginCatalog, and nothing named like a manager."""
        source = (PROJECT_ROOT / "web_interface" / "app.py").read_text(encoding="utf-8")
        assert "PluginCatalog(" in source
        assert "PluginManager" not in source


class TestNoLifecycleCallsInWebCode:
    """A static backstop for the behavioural tests above: the web process's
    own code has no call that runs a plugin. Comments may mention the hooks;
    calls may not."""

    FORBIDDEN = (".on_enable(", ".on_disable(", ".on_config_change(",
                 ".load_plugin(", ".unload_plugin(", ".reload_plugin(",
                 ".get_plugin(", ".update(force", ".display(force_clear",
                 # Vegas participation past config and manifest calls the
                 # plugin's hooks; the installed route reports it from files.
                 "resolve_vegas_participation(", "legacy_vegas_participation(",
                 ".get_vegas_participation(")

    def _code_lines(self):
        for path in sorted((PROJECT_ROOT / "web_interface").rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                yield path.relative_to(PROJECT_ROOT), number, code

    def test_no_web_module_calls_a_plugin_lifecycle_hook(self):
        offenders = [f"{path}:{number}: {code.strip()}"
                     for path, number, code in self._code_lines()
                     if any(call in code for call in self.FORBIDDEN)]
        assert offenders == []

    def test_plugin_code_is_imported_in_exactly_one_place(self):
        """exec_module in the web blueprints happens only in the seam named
        for it (Starlark helpers and oauth_flow action scripts)."""
        blueprints = PROJECT_ROOT / "web_interface" / "blueprints"
        hits = [f"{path.name}:{n}" for path in sorted(blueprints.rglob("*.py"))
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                if "exec_module(" in line.split("#", 1)[0]]
        assert len(hits) == 1, hits
        source = (blueprints / "api_v3" / "__init__.py").read_text(encoding="utf-8")
        seam = source.index("def _import_plugin_code_in_web_process(")
        assert source.index("exec_module(", seam) < source.index("\ndef ", seam + 1)


class TestStoreOperationsReachTheDisplay:
    """Each answer says whether the display needs a restart to show it."""

    def test_an_update_of_a_plugin_the_display_runs_asks_for_a_restart(self, web):
        _bump_version(web, "2.0.0")
        body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is True
        assert "restart the display" in body["restart_message"]

    def test_an_update_of_a_disabled_plugin_does_not(self, disabled_web):
        _bump_version(disabled_web, "2.0.0")
        body = disabled_web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["data"]["update_status"] == "updated"
        assert body["restart_required"] is False
        assert "restart_message" not in body

    def test_an_update_that_changed_nothing_does_not(self, web):
        body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["data"]["update_status"] == "up_to_date"
        assert body["restart_required"] is False

    def test_installing_a_plugin_config_already_enables_asks_for_a_restart(self, web):
        body = web.post("/api/v3/plugins/install", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is True

    def test_installing_a_disabled_plugin_does_not(self, disabled_web):
        # Enabling it later loads it live, from disk, in the display.
        body = disabled_web.post("/api/v3/plugins/install", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is False

    def test_the_queued_install_carries_the_flag_in_the_operation_result(self, web):
        results = []

        def enqueue(operation_type, plugin_id, operation_callback=None):
            results.append(operation_callback(MagicMock()))
            return "op-1"

        web.api.operation_queue = MagicMock()
        web.api.operation_queue.enqueue_operation.side_effect = enqueue
        web.post("/api/v3/plugins/install", {"plugin_id": PLUGIN_ID})
        assert results[0]["success"] is True
        assert results[0]["restart_required"] is True

    def test_install_from_url_carries_the_flag(self, web):
        web.api.plugin_store_manager.install_from_url.return_value = {
            "success": True, "plugin_id": PLUGIN_ID}
        body = web.post("/api/v3/plugins/install-from-url", {"repo_url": "https://example.invalid/r"})
        assert body["restart_required"] is True

    def test_uninstall_that_removes_the_config_needs_no_restart(self, web):
        # The removed section reaches the display's config watcher, and its
        # reconcile unloads the plugin.
        body = web.post("/api/v3/plugins/uninstall", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is False
        assert PLUGIN_ID not in json.loads(web.config_file.read_text(encoding="utf-8"))

    def test_uninstall_that_keeps_an_enabled_config_asks_for_a_restart(self, web):
        body = web.post("/api/v3/plugins/uninstall",
                        {"plugin_id": PLUGIN_ID, "preserve_config": True})
        assert body["restart_required"] is True

    @pytest.mark.parametrize("action,enabled,kwargs,expected", [
        ("install", False, {}, False),
        ("install", True, {}, True),
        ("update", True, {"changed": True}, True),
        ("update", True, {"changed": False}, False),
        ("update", False, {"changed": True}, False),
        ("uninstall", True, {}, False),
        ("uninstall", True, {"preserve_config": True}, True),
        ("uninstall", False, {"preserve_config": True}, False),
    ])
    def test_the_rule(self, action, enabled, kwargs, expected):
        assert display_restart_required(action, enabled, **kwargs) is expected

    def test_an_unknown_action_is_a_programming_error(self):
        with pytest.raises(ValueError):
            display_restart_required("reinstall", True)


RELOAD = "web_interface.blueprints.api_v3.control_client.plugin_reload"


class TestAnUpdateIsReloadedByTheDisplay:
    """Control socket stage 2: instead of asking for a restart, the update
    route asks the running display to reload the plugin (``plugin.reload``),
    and only falls back to ``restart_required`` when that does not work. The
    web process still runs none of the plugin's code."""

    def test_reloaded_over_the_socket(self, web):
        from unittest.mock import patch
        _bump_version(web, "2.0.0")
        with patch(RELOAD, return_value={"plugin_id": PLUGIN_ID, "reloaded": True,
                                         "version": "2.0.0", "modes": ["tripwire"]}) as reload:
            body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        reload.assert_called_once_with(PLUGIN_ID)
        assert body["restart_required"] is False
        assert body["reloaded"] is True and body["reloaded_version"] == "2.0.0"
        assert "restart_message" not in body and "reload_error" not in body
        assert "running the new version" in body["message"]
        assert web.ran() == []

    @pytest.mark.parametrize("reason", [
        "no_socket",         # display stopped, or predates the socket
        "unknown_command",   # a stage-1 display
        "not_loaded", "failed", "pending", "busy", "timeout", "refused",
    ])
    def test_any_failure_falls_back_to_a_restart(self, web, reason):
        from unittest.mock import patch
        from src.ipc import client as control_client
        _bump_version(web, "2.0.0")
        with patch(RELOAD, side_effect=control_client.ControlError(reason, "x")):
            body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is True
        assert "restart the display" in body["restart_message"]
        assert body["reload_error"] == reason
        assert "reloaded" not in body

    def test_a_client_bug_falls_back_too(self, web):
        from unittest.mock import patch
        _bump_version(web, "2.0.0")
        with patch(RELOAD, side_effect=RuntimeError("bug")):
            body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is True and body["reload_error"] == "internal"

    def test_without_a_socket_it_is_the_restart_banner_as_before(self, web):
        # The test suite runs with LEDMATRIX_CONTROL_SOCKET=off (conftest).
        _bump_version(web, "2.0.0")
        body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is True
        # 'unsupported' on Windows, which has no Unix sockets at all.
        assert body["reload_error"] in ("disabled", "unsupported")

    def test_an_unchanged_plugin_asks_nothing(self, web):
        from unittest.mock import patch
        with patch(RELOAD) as reload:
            body = web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["data"]["update_status"] == "up_to_date"
        assert body["restart_required"] is False
        reload.assert_not_called()

    def test_a_disabled_plugin_asks_nothing(self, disabled_web):
        from unittest.mock import patch
        _bump_version(disabled_web, "2.0.0")
        with patch(RELOAD) as reload:
            body = disabled_web.post("/api/v3/plugins/update", {"plugin_id": PLUGIN_ID})
        assert body["restart_required"] is False
        reload.assert_not_called()

    def test_the_display_is_asked_by_the_manifest_id(self, web):
        """A store id can differ from the id the display runs the plugin
        under (a registry alias: weather / ledmatrix-weather). The config
        section and the reload both use the manifest's."""
        from unittest.mock import patch
        manifest_id = "ledmatrix-alias"
        _write_plugin(web.plugins_dir, web.log, plugin_id=manifest_id)
        web.config_file.write_text(json.dumps({manifest_id: {"enabled": True}}),
                                   encoding="utf-8")

        def update(plugin_id):
            _write_plugin(web.plugins_dir, web.log, version="2.0.0", plugin_id=manifest_id)
            return True
        web.api.plugin_store_manager.update_plugin.side_effect = update
        with patch(RELOAD, return_value={"reloaded": True, "version": "2.0.0"}) as reload:
            body = web.post("/api/v3/plugins/update", {"plugin_id": "alias"})
        reload.assert_called_once_with(manifest_id)
        assert body["restart_required"] is False


class TestCatalogReadsWhatIsInstalled:
    """Real plugin directories: the repository's plugin-repos/ and fixtures."""

    @staticmethod
    def _manifests(root):
        found = {}
        for child in sorted(Path(root).iterdir()):
            manifest = child / "manifest.json"
            if child.is_dir() and manifest.exists():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id"):
                    found[data["id"]] = (child, data)
        return found

    @pytest.mark.parametrize("root", [
        PROJECT_ROOT / "plugin-repos",
        PROJECT_ROOT / "test" / "fixtures" / "plugins",
    ], ids=["plugin-repos", "fixtures"])
    def test_every_installed_plugin_is_read_from_its_files(self, root):
        expected = self._manifests(root)
        assert expected, f"no plugins under {root}"
        before = set(sys.modules)
        schema_manager = SchemaManager(plugins_dir=root, project_root=PROJECT_ROOT)
        catalog = PluginCatalog(root, schema_manager=schema_manager)

        assert set(catalog.discover_plugins()) == set(expected)
        for plugin_id, (plugin_dir, manifest) in expected.items():
            assert catalog.get_manifest(plugin_id) == manifest
            assert catalog.get_plugin_directory(plugin_id) == str(plugin_dir)
            assert catalog.get_installed_version(plugin_id) == manifest.get("version", "")
            assert catalog.get_plugin_display_modes(plugin_id) == manifest.get("display_modes", [])
            if (plugin_dir / "config_schema.json").exists():
                schema = catalog.get_schema(plugin_id, use_cache=False)
                assert isinstance(schema, dict) and "properties" in schema, plugin_id

        imported = [name for name in set(sys.modules) - before
                    if str(root) in str(getattr(sys.modules[name], "__file__", "") or "")]
        assert imported == [], "reading the catalog imported plugin code"

    def test_a_mode_finds_its_plugin(self):
        catalog = PluginCatalog(PROJECT_ROOT / "test" / "fixtures" / "plugins")
        catalog.discover_plugins()
        assert catalog.find_plugin_for_mode("CI-Fixture") == "ci-fixture-plugin"
        assert catalog.find_plugin_for_mode("nope") is None

    def test_discovery_follows_the_directory(self, tmp_path):
        plugins = tmp_path / "plugin-repos"
        root = PROJECT_ROOT / "test" / "fixtures" / "plugins" / "ci-fixture-plugin"
        shutil.copytree(root, plugins / "ledmatrix-ci-fixture-plugin")
        (plugins / "broken").mkdir()
        (plugins / "broken" / "manifest.json").write_text("[]", encoding="utf-8")
        catalog = PluginCatalog(plugins)

        assert catalog.discover_plugins() == ["ci-fixture-plugin"]
        # Installed as ledmatrix-<id>, found by its manifest id.
        assert catalog.get_plugin_directory("ci-fixture-plugin").endswith("ledmatrix-ci-fixture-plugin")
        # Not a plain name: refused, never joined onto the plugins directory.
        assert catalog.get_plugin_directory("../plugin-repos") is None

        shutil.rmtree(plugins / "ledmatrix-ci-fixture-plugin")
        assert catalog.discover_plugins() == []
        assert catalog.get_manifest("ci-fixture-plugin") is None

    def test_enabled_follows_the_display_rule(self, tmp_path):
        config = MagicMock()
        config.load_config.return_value = {"a": {"enabled": True}, "b": {}, "c": "junk"}
        catalog = PluginCatalog(tmp_path, config_manager=config)
        assert catalog.is_enabled("a") is True
        # The display runs a plugin only when its section says so.
        assert catalog.is_enabled("b") is False
        assert catalog.is_enabled("c") is False
        assert catalog.is_enabled("missing") is False
        assert catalog.get_config("c") == {}

    def test_it_has_nothing_that_runs_a_plugin(self, tmp_path):
        catalog = PluginCatalog(tmp_path)
        for name in ("load_plugin", "unload_plugin", "reload_plugin", "get_plugin",
                     "plugins", "run_scheduled_updates"):
            assert not hasattr(catalog, name), name
