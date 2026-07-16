# skins/

User-installable **visual skins** for the sports scoreboards. Each
subdirectory is one skin:

```
skins/<skin-id>/
  skin.json     # manifest
  skin.py       # renderer (a ScoreboardSkin subclass)
  preview.png   # optional
```

- Install a skin: `git clone <skin repo> skins/<skin-id>` (or via the Plugin
  Store for registry entries with `"type": "skin"`).
- Select it: set `"skin": "<skin-id>"` in the plugin's section of
  `config/config.json`, or use the web UI's Visual Skin dropdown.
- Build one: start from `example-classic-baseball/` and read
  [docs/CREATING_SKINS.md](../docs/CREATING_SKINS.md). Validate with
  `python scripts/validate_skin.py --skin <skin-id>`.

Skins survive plugin reinstalls/updates (that's why they live here and not in
the plugin's directory). A skin is Python at the same trust level as a
plugin — review before installing.
