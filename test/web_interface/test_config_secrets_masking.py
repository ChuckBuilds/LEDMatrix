"""GET /config/secrets must not hand out credentials, and the client's
read-modify-write cycle must not destroy them.

This interface has no authentication. The endpoint returned the whole
config_secrets.json to anyone who could reach the port; on one rig that was a
40-character GitHub token, a 183-character Home Assistant token and three API
keys. Masking it alone is not enough: the only client fetches every secret,
edits one field and posts all of them back, so the write path has to treat an
echoed mask as "unchanged".
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v3_secret_roundtrip import env, _on_disk  # noqa: F401,E402
from src.web_interface.secret_helpers import SECRET_MASK  # noqa: E402

STORED = {
    "github": {"api_token": "ghp_" + "x" * 36},
    "ledmatrix-weather": {"api_key": "w" * 32},
    "incoming-packages": {"ha_token": "h" * 183},
    "unset-plugin": {"api_key": ""},
    "placeholder-plugin": {"api_key": "YOUR_API_KEY_HERE"},
}


def _seed(env):
    env.secrets_file.write_text(json.dumps(STORED))


def _get(env):
    r = env.client.get("/api/v3/config/secrets")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    return r.get_json()["data"]


def test_no_credential_leaves_the_process(env):
    _seed(env)
    body = json.dumps(_get(env))
    for secret in ("ghp_" + "x" * 36, "w" * 32, "h" * 183):
        assert secret not in body, "endpoint returned a stored credential"


def test_set_and_unset_remain_distinguishable(env):
    _seed(env)
    data = _get(env)
    assert data["github"]["api_token"] == SECRET_MASK
    assert data["unset-plugin"]["api_key"] == ""
    assert data["placeholder-plugin"]["api_key"] == "YOUR_API_KEY_HERE"


def test_the_clients_read_modify_write_preserves_every_other_secret(env):
    """What the GitHub-token save button actually does."""
    _seed(env)
    secrets = _get(env)                      # everything arrives masked
    secrets["github"]["api_token"] = "ghp_" + "n" * 36   # user changes one
    r = env.client.post("/api/v3/config/raw/secrets", json=secrets)
    assert r.status_code == 200, r.get_data(as_text=True)[:200]

    on_disk = _on_disk(env.secrets_file)
    assert on_disk["github"]["api_token"] == "ghp_" + "n" * 36, "new token not saved"
    assert on_disk["ledmatrix-weather"]["api_key"] == "w" * 32
    assert on_disk["incoming-packages"]["ha_token"] == "h" * 183


def test_a_mask_echoed_back_is_never_stored(env):
    _seed(env)
    env.client.post("/api/v3/config/raw/secrets", json=_get(env))
    on_disk = _on_disk(env.secrets_file)
    assert SECRET_MASK not in json.dumps(on_disk), "the mask was stored as a secret"
    assert on_disk["github"]["api_token"] == "ghp_" + "x" * 36


def test_a_brand_new_secret_can_still_be_added(env):
    _seed(env)
    env.client.post("/api/v3/config/raw/secrets",
                    json={"new-plugin": {"api_key": "brand-new"}})
    on_disk = _on_disk(env.secrets_file)
    assert on_disk["new-plugin"]["api_key"] == "brand-new"
    assert on_disk["github"]["api_token"] == "ghp_" + "x" * 36
