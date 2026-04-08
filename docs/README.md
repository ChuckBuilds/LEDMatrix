# LEDMatrix Documentation

This directory contains guides, references, and architectural notes for the
LEDMatrix project. If you are setting up a Pi for the first time, start with
the [project root README](../README.md) — it covers hardware, OS imaging, and
the one-shot installer. The pages here go deeper.

## I'm a new user

1. [GETTING_STARTED.md](GETTING_STARTED.md) — first-time setup walkthrough
2. [WEB_INTERFACE_GUIDE.md](WEB_INTERFACE_GUIDE.md) — using the web UI
3. [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) — installing and managing plugins
4. [WIFI_NETWORK_SETUP.md](WIFI_NETWORK_SETUP.md) — WiFi and AP-mode setup
5. [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — common issues and fixes
6. [SSH_UNAVAILABLE_AFTER_INSTALL.md](SSH_UNAVAILABLE_AFTER_INSTALL.md) — recovering SSH after install
7. [CONFIG_DEBUGGING.md](CONFIG_DEBUGGING.md) — diagnosing config problems

## I want to write a plugin

Start here:

1. [PLUGIN_DEVELOPMENT_GUIDE.md](PLUGIN_DEVELOPMENT_GUIDE.md) — end-to-end workflow
2. [PLUGIN_QUICK_REFERENCE.md](PLUGIN_QUICK_REFERENCE.md) — cheat sheet
3. [PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md) — display, cache, and plugin-manager APIs
4. [PLUGIN_ERROR_HANDLING.md](PLUGIN_ERROR_HANDLING.md) — error-handling patterns
5. [DEV_PREVIEW.md](DEV_PREVIEW.md) — preview plugins on your dev machine without a Pi
6. [EMULATOR_SETUP_GUIDE.md](EMULATOR_SETUP_GUIDE.md) — running the matrix emulator

Going deeper:

- [ADVANCED_PLUGIN_DEVELOPMENT.md](ADVANCED_PLUGIN_DEVELOPMENT.md) — advanced patterns
- [PLUGIN_ARCHITECTURE_SPEC.md](PLUGIN_ARCHITECTURE_SPEC.md) — full plugin-system spec
- [PLUGIN_DEPENDENCY_GUIDE.md](PLUGIN_DEPENDENCY_GUIDE.md) /
  [PLUGIN_DEPENDENCY_TROUBLESHOOTING.md](PLUGIN_DEPENDENCY_TROUBLESHOOTING.md)
- [PLUGIN_WEB_UI_ACTIONS.md](PLUGIN_WEB_UI_ACTIONS.md) (+ [example JSON](PLUGIN_WEB_UI_ACTIONS_EXAMPLE.json))
- [PLUGIN_CUSTOM_ICONS.md](PLUGIN_CUSTOM_ICONS.md) /
  [PLUGIN_CUSTOM_ICONS_FEATURE.md](PLUGIN_CUSTOM_ICONS_FEATURE.md)
- [PLUGIN_REGISTRY_SETUP_GUIDE.md](PLUGIN_REGISTRY_SETUP_GUIDE.md) (+ [registry template](plugin_registry_template.json))
- [STARLARK_APPS_GUIDE.md](STARLARK_APPS_GUIDE.md) — Starlark-based mini-apps
- [widget-guide.md](widget-guide.md) — widget development

## Configuring plugins

- [PLUGIN_CONFIG_QUICK_START.md](PLUGIN_CONFIG_QUICK_START.md) — minimal config you need
- [PLUGIN_CONFIGURATION_GUIDE.md](PLUGIN_CONFIGURATION_GUIDE.md) — schema design
- [PLUGIN_CONFIGURATION_TABS.md](PLUGIN_CONFIGURATION_TABS.md) — multi-tab UI configs
- [PLUGIN_CONFIG_ARCHITECTURE.md](PLUGIN_CONFIG_ARCHITECTURE.md) — how the config system works
- [PLUGIN_CONFIG_CORE_PROPERTIES.md](PLUGIN_CONFIG_CORE_PROPERTIES.md) — properties every plugin honors

## Advanced features

- [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) — Vegas scroll, on-demand display,
  cache management, background services, permissions
- [FONT_MANAGER.md](FONT_MANAGER.md) — font system

## Reference

- [REST_API_REFERENCE.md](REST_API_REFERENCE.md) — all web-interface HTTP endpoints
- [PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md) — Python APIs available to plugins
- [DEVELOPER_QUICK_REFERENCE.md](DEVELOPER_QUICK_REFERENCE.md) — common dev tasks
- [PLUGIN_IMPLEMENTATION_SUMMARY.md](PLUGIN_IMPLEMENTATION_SUMMARY.md) — what the plugin system actually does

## Contributing to LEDMatrix itself

- [DEVELOPMENT.md](DEVELOPMENT.md) — environment setup
- [HOW_TO_RUN_TESTS.md](HOW_TO_RUN_TESTS.md) — running the test suite
- [MULTI_ROOT_WORKSPACE_SETUP.md](MULTI_ROOT_WORKSPACE_SETUP.md) — multi-repo workspace
- [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) — breaking changes between releases

## Archive

`docs/archive/` holds older guides that have been superseded or describe
features that have been removed. They are kept for historical context and
git history but should not be relied on.

## Contributing to the docs

- Markdown only, professional tone, minimal emoji.
- Prefer adding to an existing page over creating a new one. If you add a
  new page, link it from this index in the section it belongs to.
- If a page becomes obsolete, move it to `docs/archive/` rather than
  deleting it, so links don't rot.
- Keep examples runnable — paths, commands, and config keys here should
  match what's actually in the repo.
