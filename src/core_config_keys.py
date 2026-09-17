"""Top-level config.json keys that belong to the LEDMatrix core, not to plugins.

config.json mixes two kinds of top-level section: core settings (``display``,
``schedule``, ``auto_update``, ...) and one section per plugin, keyed by plugin
id. Anything that needs to tell them apart -- plugin-state reconciliation above
all -- must use this list, not a private copy of it.

A private copy is what went wrong: #581 added a top-level ``auto_update``
section, reconciliation's own list did not know about it, and every device with
the new setting was told "In config but not installed: auto_update. Reinstall
via the Plugin Store, or remove these entries from config.json" -- advice that
deletes a real core setting.

When you add a top-level core setting, add its key here. The tests in
``test/test_core_config_keys.py`` fail if a top-level key in
``config/config.template.json``, or one written by the general-settings save
endpoint, is missing from this list.
"""

CORE_CONFIG_KEYS = frozenset({
    # config/config.template.json
    'web_display_autostart',
    'auto_update',
    'schedule',
    'dim_schedule',
    'timezone',
    'target_fps',
    'location',
    'display',
    'sync',
    'plugin_system',
    # Older or optional core sections still found in existing config files.
    'logging',
    'network',
    'system',
    'vegas_scroll_speed',
    'vegas_separator_width',
    'vegas_target_fps',
    'vegas_buffer_ahead',
    'vegas_plugin_order',
    'vegas_excluded_plugins',
    'vegas_scroll_enabled',
})

#: Top-level keys of ``config_secrets.json`` that belong to the core rather than
#: to a plugin: the GitHub token the Plugin Store reads, and the historical
#: ``youtube`` section. Plugin secrets are namespaced by plugin id, so anything
#: deciding whether a secrets section is a plugin's needs this as well as
#: ``CORE_CONFIG_KEYS``.
CORE_SECRETS_KEYS = frozenset({
    'github',
    'youtube',
})
