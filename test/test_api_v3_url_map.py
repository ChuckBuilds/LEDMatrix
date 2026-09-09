"""The /api/v3 URL map is a contract, and the split must not have moved it.

api_v3.py was one 10,469-line module and is now a package. Every route module
in it decorates the *same* Blueprint object, so this refactor was supposed to
be invisible from outside: same URLs, same endpoint names, same methods.

"Supposed to be" is the problem. A route function silently dropped during a
move -- a module that never gets imported, a decorator left behind -- costs
nothing at import time and fails only when someone hits the URL. So the map is
pinned here.

The snapshot is intentionally the *whole* map rather than a count. A count
passes when one route is deleted and another added, which is exactly the shape
a careless move produces.

If you are adding a route, this test is meant to fail: add the entry to
EXPECTED. If you are moving one between modules, it is meant to pass unchanged
-- endpoint names are `api_v3.<function>` regardless of which module the
function lives in, and that is the property that makes the package safe.
"""
import json
import os

import pytest
from flask import Flask

from web_interface.blueprints.api_v3 import api_v3

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "api_v3_url_map.json")


def _current_map():
    app = Flask(__name__)
    app.register_blueprint(api_v3, url_prefix="/api/v3")
    return sorted(
        [r.rule, r.endpoint, sorted(r.methods)]
        for r in app.url_map.iter_rules()
        if r.endpoint != "static"
    )


def test_the_url_map_matches_the_snapshot():
    current = _current_map()
    with open(SNAPSHOT, encoding="utf-8") as fh:
        expected = json.load(fh)

    cur = {(r, e) for r, e, _ in current}
    exp = {(r, e) for r, e, _ in expected}

    lost = sorted(exp - cur)
    added = sorted(cur - exp)
    assert not lost, (
        f"{len(lost)} route(s) disappeared from /api/v3: {lost[:5]}. "
        "A route lost in a module move costs nothing at import time and fails "
        "only when someone hits the URL.")
    assert not added, (
        f"{len(added)} new route(s): {added[:5]}. If that is intended, "
        f"regenerate {os.path.relpath(SNAPSHOT)}.")

    # Methods too: a route that quietly loses POST is still a broken route.
    cur_methods = {(r, e): m for r, e, m in current}
    for rule, endpoint, methods in expected:
        assert cur_methods[(rule, endpoint)] == methods, (
            f"{rule} ({endpoint}) methods changed: "
            f"{methods} -> {cur_methods[(rule, endpoint)]}")


def test_every_endpoint_is_on_the_one_blueprint():
    """The package must not fragment into several blueprints.

    Splitting into per-domain *blueprints* would rename every endpoint from
    `api_v3.foo` to `api_v3_plugins.foo` and break any url_for() that names
    one. Keeping a single Blueprint object across the modules is what makes
    the split a pure code move, so assert it rather than trusting it.
    """
    for rule, endpoint, _ in _current_map():
        assert endpoint.startswith("api_v3."), (
            f"{rule} is registered as {endpoint}, not on the api_v3 blueprint")


def test_the_snapshot_is_not_empty():
    """Guards the failure mode this file exists to prevent.

    An empty or truncated snapshot would make every assertion above pass
    vacuously -- the same trap as a route map that imports no modules.
    """
    with open(SNAPSHOT, encoding="utf-8") as fh:
        expected = json.load(fh)
    assert len(expected) > 100, (
        f"snapshot has only {len(expected)} routes; it should have the whole "
        "/api/v3 surface")


@pytest.mark.parametrize("module", [
    "backup", "config", "display", "fonts", "misc",
    "plugins", "starlark", "system", "wifi",
])
def test_every_route_module_contributes(module):
    """Each module must actually register something.

    A module that fails to import, or that is left out of __init__, takes its
    routes with it silently -- the package still imports and the app still
    starts.
    """
    import importlib
    mod = importlib.import_module(f"web_interface.blueprints.api_v3.{module}")
    routes = [n for n in dir(mod)
              if callable(getattr(mod, n, None))
              and getattr(getattr(mod, n), "__module__", "") == mod.__name__]
    assert routes, f"{module}.py defines no view functions"


def test_project_root_points_at_the_project():
    """PROJECT_ROOT is derived from __file__, so moving the file breaks it.

    The split moved this code from web_interface/blueprints/api_v3.py to
    web_interface/blueprints/api_v3/_common.py -- one directory deeper -- and
    `Path(__file__).parent.parent.parent` quietly began resolving to
    web_interface/ instead of the project root. Nothing failed at import. It
    surfaced as routes returning 404 and "installation script not found",
    because every path built from it pointed one level too shallow.

    A URL-map check cannot catch that: the routes were all registered, they
    just could not find anything.
    """
    from pathlib import Path

    from web_interface.blueprints.api_v3 import PROJECT_ROOT

    # The project root is the directory holding run.py and web_interface/.
    assert (PROJECT_ROOT / "run.py").is_file(), (
        f"PROJECT_ROOT is {PROJECT_ROOT}, which has no run.py; it is not the "
        "project root")
    assert (PROJECT_ROOT / "web_interface").is_dir()
    assert PROJECT_ROOT == Path(__file__).resolve().parents[1]
