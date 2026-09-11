"""The MQTT bridge settings endpoint must reject bodies it cannot apply.

Two defects CodeRabbit raised on #554, both of which reported success while
doing something other than what the caller asked:

* `request.get_json(silent=True) or {}` turned a missing or unparseable body --
  and the JSON literals `null`, `[]` and `false` -- into an empty dict, which
  then satisfied the `isinstance(data, dict)` guard directly below it. Malformed
  JSON therefore returned 200 having applied nothing.

* `if data.get('clear_password'):` accepted any truthy value. The string
  "false" is truthy in Python, so a client echoing the field back as a string
  wiped a stored password it meant to keep.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints.api_v3 import api_v3  # noqa: E402
import web_interface.blueprints.api_v3 as pkg  # noqa: E402
import web_interface.blueprints.api_v3.misc as misc  # noqa: E402

URL = "/api/v3/integrations/mqtt-bridge/config"


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "bridge_config.json"
    # Both modules: misc.py writes through its own imported copies of these
    # names, while _read_mqtt_bridge_config() lives in the package __init__ and
    # reads the one bound there. Patching only one leaves the read and the
    # write pointing at different files.
    for mod in (misc, pkg):
        monkeypatch.setattr(mod, "_MQTT_BRIDGE_CONFIG", cfg, raising=False)
        monkeypatch.setattr(mod, "_MQTT_BRIDGE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(api_v3, "config_manager", MagicMock(), raising=False)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_v3, url_prefix="/api/v3")
    app.cfg_path = cfg
    return app.test_client(), cfg


class TestABodyItCannotApplyIsRejected:
    @pytest.mark.parametrize("raw", ["{ not json", "null", "[]", "false", '"a string"'])
    def test_unusable_bodies_are_rejected(self, client, raw):
        c, _ = client
        r = c.put(URL, data=raw, content_type="application/json")
        # The regression: every one of these returned 200 having applied nothing.
        assert r.status_code == 400, f"{raw!r} was accepted"
        assert "JSON object" in r.get_json()["message"]

    def test_a_missing_body_is_rejected(self, client):
        c, _ = client
        assert c.put(URL).status_code == 400

    def test_a_real_object_is_still_accepted(self, client):
        c, _ = client
        r = c.put(URL, json={"mqtt_host": "192.168.1.20"})
        assert r.status_code == 200, r.get_json()


class TestClearPasswordNeedsARealBoolean:
    def _seed(self, cfg, password="hunter2"):
        cfg.write_text(json.dumps({"mqtt_password": password}), encoding="utf-8")

    def _stored(self, cfg):
        return json.loads(cfg.read_text(encoding="utf-8")).get("mqtt_password")

    def test_the_string_false_does_not_clear_it(self, client):
        c, cfg = client
        self._seed(cfg)
        assert c.put(URL, json={"clear_password": "false"}).status_code == 200
        # The regression: "false" is truthy, so this wiped the password.
        assert self._stored(cfg) == "hunter2"

    @pytest.mark.parametrize("falsy", [False, "no", "0", "", None])
    def test_other_falsy_spellings_do_not_clear_it(self, client, falsy):
        c, cfg = client
        self._seed(cfg)
        assert c.put(URL, json={"clear_password": falsy}).status_code == 200
        assert self._stored(cfg) == "hunter2"

    @pytest.mark.parametrize("truthy", [True, "true", "1", "yes", "on"])
    def test_real_truthy_values_still_clear_it(self, client, truthy):
        c, cfg = client
        self._seed(cfg)
        assert c.put(URL, json={"clear_password": truthy}).status_code == 200
        assert self._stored(cfg) is None
