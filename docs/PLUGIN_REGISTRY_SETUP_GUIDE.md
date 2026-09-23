# Plugin Registry Setup Guide

This page explains how the official plugin registry works and how a plugin
gets into it. The registry and the official plugins both live in one
repository, [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins);
its `SUBMISSION.md`, `VERIFICATION.md` and `docs/` are the authoritative
contributor guides.

## How it fits together

```text
ledmatrix-plugins/
├── plugins/
│   ├── clock-simple/        # one directory per official plugin
│   │   ├── manifest.json    # source of truth for the plugin's version
│   │   ├── manager.py
│   │   ├── config_schema.json
│   │   └── requirements.txt
│   └── ...
├── plugins.json             # the registry the Plugin Store reads
└── update_registry.py       # regenerates plugins.json from the manifests
```

- **Registry.** The Plugin Store fetches
  `https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json`
  (`PluginStoreManager.REGISTRY_URL` in `src/plugin_system/store_manager.py`)
  and caches it for 15 minutes.
- **Monorepo plugins** have `repo` set to the ledmatrix-plugins URL and
  `plugin_path` set to their directory (`plugins/<id>`). The store downloads
  just that directory (GitHub API, falling back to the repository ZIP), so
  installed copies have no `.git` directory.
- **Third-party plugins** keep their own repository: `repo` points at it and
  `plugin_path` is empty. The store installs them with `git clone`, falling
  back to an archive download.
- **Updates.** For registry plugins the store compares the installed
  manifest's `version` with the entry's `latest_version`. Git tags and GitHub
  releases are not read.

## A registry entry

```json
{
  "id": "clock-simple",
  "name": "Simple Clock",
  "description": "A clean, simple clock display with date and time",
  "author": "ChuckBuilds",
  "category": "time",
  "tags": ["clock", "time", "date"],
  "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
  "branch": "main",
  "plugin_path": "plugins/clock-simple",
  "stars": 0,
  "downloads": 0,
  "last_updated": "2026-09-03",
  "verified": true,
  "screenshot": "",
  "latest_version": "1.0.0"
}
```

[plugin_registry_template.json](plugin_registry_template.json) shows a
monorepo entry and a third-party entry.

Don't edit `latest_version` or `last_updated` by hand for monorepo plugins:
`update_registry.py` in ledmatrix-plugins writes them from each plugin's
`manifest.json`.

## Adding or changing an official plugin

1. Add or edit `plugins/<your-plugin-id>/` in the monorepo. The store refuses
   a manifest without `id`, `name`, `class_name` and `display_modes`; also
   set `version`.
2. Bump `version` in the plugin's `manifest.json` for every change, or users
   won't be offered the update.
3. Run `python update_registry.py` in ledmatrix-plugins and commit the
   updated `plugins.json` with the plugin change.
4. Open a pull request. The monorepo's CI and review steps are described in
   its `SUBMISSION.md`.

## Adding a third-party plugin

Test it with **Plugin Manager → Install from GitHub → Install Single Plugin**
(or `POST /api/v3/plugins/install-from-url`), then follow the "own
repository" option in the monorepo's `SUBMISSION.md` to request a registry
entry.

## Testing locally

```bash
# Validate a plugin headlessly (from LEDMatrix)
python3 scripts/check_plugin.py --plugin <id>

# Fetch the registry the way the store does
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager(plugins_dir='plugin-repos')
print(len(store.fetch_registry(force_refresh=True).get('plugins', [])), 'plugins')
"
```

To work on monorepo plugins against a LEDMatrix checkout, see
[MULTI_ROOT_WORKSPACE_SETUP.md](MULTI_ROOT_WORKSPACE_SETUP.md) and the
[Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md).

## References

- Plugin Store user guide: [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md)
- Plugin architecture (historical): [PLUGIN_ARCHITECTURE_SPEC.md](PLUGIN_ARCHITECTURE_SPEC.md)
- [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)
