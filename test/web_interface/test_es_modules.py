"""The ES-module layer of the web UI is served the way browsers need it.

static/v3/js/core/ and js/pages/ are native ES modules, loaded with
<script type="module"> and no bundler (docs/WEB_FRONTEND_ARCHITECTURE.md):

* A module script runs only when served with a JavaScript MIME type, so the
  app pins .js to text/javascript instead of trusting the host's table.
* Modules import each other by plain relative URL, without the ?v= content
  version url_for adds. Those requests must revalidate rather than be cached
  as immutable for a year, or an update would keep running old modules.
* Every import resolves to a file that exists, every registered page has its
  module and a partial whose root names it, and a converted partial carries
  no inline <script> (which htmx-config.js would re-run on every swap).
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JS = PROJECT_ROOT / "web_interface" / "static" / "v3" / "js"
PARTIALS = PROJECT_ROOT / "web_interface" / "templates" / "v3" / "partials"
MODULE_DIRS = (JS / "core", JS / "pages")
MODULES = sorted(p for d in MODULE_DIRS for p in d.glob("*.js"))
JS_TYPES = {"text/javascript", "application/javascript"}

_IMPORT = re.compile(r"""(?:\bimport\s*\(\s*|\bfrom\s+|^\s*import\s+)['"]([^'"]+)['"]""", re.M)


@pytest.fixture(scope="module")
def client():
    import web_interface.app as web_app
    web_app.app.config["TESTING"] = True
    with web_app.app.test_client() as c:
        yield c


def _url(path):
    return "/static/" + path.relative_to(PROJECT_ROOT / "web_interface" / "static").as_posix()


def test_the_module_directories_hold_modules():
    assert {p.name for p in MODULES} >= {"boot.js", "registry.js", "api.js", "facade.js", "cache.js"}
    for directory in MODULE_DIRS:
        # node needs this to import them in the JS tests; browsers ignore it.
        assert '"type": "module"' in (directory / "package.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("module", MODULES, ids=[m.name for m in MODULES])
def test_each_module_is_served_as_javascript_and_revalidated(client, module):
    resp = client.get(_url(module))
    assert resp.status_code == 200
    assert resp.mimetype in JS_TYPES
    # Requested as a relative import would request it: no ?v=.
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers.get("ETag") or resp.headers.get("Last-Modified")


def test_a_versioned_script_is_still_cached_for_good(client):
    resp = client.get(_url(JS / "core" / "boot.js") + "?v=123")
    assert "immutable" in resp.headers["Cache-Control"]


def test_an_unchanged_module_revalidates_to_a_304(client):
    first = client.get(_url(JS / "core" / "registry.js"))
    again = client.get(_url(JS / "core" / "registry.js"),
                       headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def test_classic_scripts_keep_a_javascript_type(client):
    resp = client.get("/static/v3/js/app-early.js")
    assert resp.mimetype in JS_TYPES


def test_base_html_loads_the_entry_module_last(client):
    page = client.get("/").get_data(as_text=True)
    tag = re.search(r'<script type="module" src="(/static/v3/js/core/boot\.js\?v=\d+)"></script>', page)
    assert tag, "base.html must load js/core/boot.js as a versioned module script"
    # After every classic script, so nothing classic can depend on it at load.
    assert page.index(tag.group(0)) > page.index("plugins_manager.js")


@pytest.mark.parametrize("module", MODULES, ids=[m.name for m in MODULES])
def test_every_import_resolves_inside_the_module_tree(module):
    source = module.read_text(encoding="utf-8")
    for spec in _IMPORT.findall(source):
        assert spec.startswith("./") or spec.startswith("../"), (
            f"{module.name}: {spec!r} -- no bundler, so only relative imports work")
        target = (module.parent / spec).resolve()
        assert target.is_file(), f"{module.name}: {spec!r} does not exist"
        assert any(target.parent == d.resolve() for d in MODULE_DIRS), (
            f"{module.name}: {spec!r} leaves js/core and js/pages")


def _registered_pages():
    boot = (JS / "core" / "boot.js").read_text(encoding="utf-8")
    return re.findall(r"registry\.register\('([\w-]+)',\s*function\(\)\s*\{\s*return import\('\.\./pages/([\w-]+)\.js'\)",
                      boot)


def test_every_registered_page_has_its_module_and_partial():
    pages = _registered_pages()
    assert ("cache", "cache") in pages
    for name, module in pages:
        assert name == module, "a page is named after its module"
        assert (JS / "pages" / f"{module}.js").is_file()
        partials = [p for p in PARTIALS.glob("*.html")
                    if f'data-page="{name}"' in p.read_text(encoding="utf-8")]
        assert len(partials) == 1, f"one partial roots page {name!r}: {partials}"


def test_converted_partials_carry_no_inline_script():
    for partial in PARTIALS.glob("*.html"):
        text = partial.read_text(encoding="utf-8")
        if "data-page=" in text:
            assert "<script" not in text.lower(), (
                f"{partial.name} is a page module now; its code belongs in js/pages/")
