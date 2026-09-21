"""composer_bp must actually be registered on the Flask app.

web_interface/blueprints/composer.py defines a full blueprint -- the
/composer/ page, /composer/api/generate, /composer/api/install, etc. -- but
nothing in web_interface/app.py imported or registered it. pages_v3 and
api_v3 both go through the same "import, wire managers, register_blueprint"
sequence; composer_bp had none of the three. The routes existed and worked
in isolation (every other composer test builds its own minimal Flask app
and registers composer_bp directly), but in the real running app every
composer URL -- including the page itself -- was a 404, and the frontend's
hardcoded '/composer/api/...' fetches (composer-app.js) had nothing to
reach.

app.py has import-time side effects (it loads config, builds managers, and
in some configurations starts background threads), so this scans the source
text rather than importing the module -- the same approach already used for
test_composer_js_contracts.py's structural checks.
"""
import re
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "web_interface" / "app.py"


def _source() -> str:
    return APP_PY.read_text()


def test_composer_blueprint_is_imported():
    src = _source()
    assert re.search(
        r"from web_interface\.blueprints\.composer import composer_bp", src
    ), "app.py never imports composer_bp"


def test_composer_blueprint_is_registered_under_composer_prefix():
    src = _source()
    match = re.search(
        r"app\.register_blueprint\(\s*composer_bp\s*,\s*url_prefix\s*=\s*"
        r"['\"](?P<prefix>[^'\"]*)['\"]",
        src,
    )
    assert match, "app.py never calls app.register_blueprint(composer_bp, ...)"
    # composer.html and composer-app.js hardcode '/composer/api/...' and
    # '/composer/api/fonts/...' -- any other prefix serves a page whose own
    # fetches and @font-face all 404.
    assert match.group("prefix") == "/composer"


def test_composer_blueprint_managers_are_wired_before_registration():
    """Registered with no managers set means every route 503s or crashes.

    composer.py initialises config_manager/plugin_manager/plugins_dir/
    project_root to None at import time specifically so app.py can set them
    -- the same pattern api_v3 and pages_v3 use.
    """
    src = _source()
    register_match = re.search(r"app\.register_blueprint\(\s*composer_bp", src)
    assert register_match, "app.py never registers composer_bp"
    before_registration = src[: register_match.start()]
    for attr in ("config_manager", "plugin_manager", "plugins_dir", "project_root"):
        assert re.search(rf"composer_bp\.{attr}\s*=", before_registration), (
            f"composer_bp.{attr} is never set before registration"
        )
