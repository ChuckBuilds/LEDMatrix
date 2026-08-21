#!/usr/bin/env python3
"""Regenerate schemas and safely repair defaults for installed Starlark apps."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_APPS_DIR = PROJECT_ROOT / "starlark-apps"
RENDERER_PATH = Path(__file__).resolve().parents[1] / "pixlet_renderer.py"
SYMBOLIC_DEFAULT = re.compile(r"^DEFAULT_[A-Z0-9_]+$")


@dataclass
class Summary:
    apps_scanned: int = 0
    schemas_regenerated: int = 0
    configs_repaired: int = 0
    unresolved_fields: int = 0
    errors: int = 0


def _load_renderer_class():
    spec = importlib.util.spec_from_file_location("starlark_schema_rebuild_renderer", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load schema renderer from {RENDERER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.PixletRenderer


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o664
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _find_star_file(app_dir: Path, manifest_entry: dict[str, Any]) -> Optional[Path]:
    configured = manifest_entry.get("star_file")
    if configured:
        configured_path = Path(configured)
        candidate = configured_path if configured_path.is_absolute() else app_dir / configured_path
        if candidate.is_file():
            return candidate
    conventional = app_dir / f"{app_dir.name}.star"
    if conventional.is_file():
        return conventional
    candidates = sorted(app_dir.glob("*.star"))
    return candidates[0] if len(candidates) == 1 else None


def rebuild_schemas(apps_dir: Path = DEFAULT_APPS_DIR) -> Summary:
    summary = Summary()
    renderer = _load_renderer_class()()
    manifest = _read_json(apps_dir / "manifest.json", {"apps": {}})
    manifest_apps = manifest.get("apps", {}) if isinstance(manifest, dict) else {}

    if not apps_dir.is_dir():
        print(f"Apps directory does not exist: {apps_dir}", file=sys.stderr)
        summary.errors += 1
        return summary

    for app_dir in sorted(path for path in apps_dir.iterdir() if path.is_dir()):
        summary.apps_scanned += 1
        try:
            star_file = _find_star_file(app_dir, manifest_apps.get(app_dir.name, {}))
            if star_file is None:
                raise FileNotFoundError("could not identify a single .star file")

            success, schema, error = renderer.extract_schema(str(star_file))
            if not success:
                raise RuntimeError(error or "schema extraction failed")
            if schema is None:
                print(f"{app_dir.name}: no schema")
                continue

            _atomic_write_json(app_dir / "schema.json", schema)
            summary.schemas_regenerated += 1

            fields = schema.get("fields") or schema.get("schema") or []
            defaults = {
                field["id"]: field["default"]
                for field in fields
                if isinstance(field, dict) and "id" in field and "default" in field
            }
            for field in fields:
                if (isinstance(field, dict) and field.get("typeOf") == "dropdown"
                        and not field.get("options")):
                    summary.unresolved_fields += 1

            config_path = app_dir / "config.json"
            config = _read_json(config_path, {})
            if not isinstance(config, dict):
                raise ValueError("config.json must contain an object")
            repaired = False
            for field_id, default in defaults.items():
                if field_id not in config:
                    config[field_id] = default
                    repaired = True
                elif (isinstance(config[field_id], str)
                      and SYMBOLIC_DEFAULT.fullmatch(config[field_id])):
                    config[field_id] = default
                    repaired = True

            for value in config.values():
                if isinstance(value, str) and SYMBOLIC_DEFAULT.fullmatch(value):
                    summary.unresolved_fields += 1

            if repaired:
                _atomic_write_json(config_path, config)
                summary.configs_repaired += 1
            print(f"{app_dir.name}: schema regenerated"
                  + (", config repaired" if repaired else ""))
        except Exception as exc:
            summary.errors += 1
            print(f"{app_dir.name}: ERROR: {exc}", file=sys.stderr)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps-dir", type=Path, default=DEFAULT_APPS_DIR,
                        help=f"installed apps directory (default: {DEFAULT_APPS_DIR})")
    args = parser.parse_args()
    summary = rebuild_schemas(args.apps_dir.resolve())
    print("\nSummary")
    print(f"apps scanned: {summary.apps_scanned}")
    print(f"schemas regenerated: {summary.schemas_regenerated}")
    print(f"configs repaired: {summary.configs_repaired}")
    print(f"unresolved fields: {summary.unresolved_fields}")
    print(f"errors: {summary.errors}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
