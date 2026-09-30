"""The SSE streams must actually enforce their 200-per-minute reconnect limit.

flask-limiter enforces a decorated limit in the wrapper ``limit()`` returns and
marks the original function so the default limit skips it. app.py once called
``limiter.limit(...)(stream_stats)`` and threw the wrapper away, which left the
streams with no limit at all, not even the 1000-per-minute default. These pin
that a client reconnecting in a tight loop gets a 429.

flask-limiter is optional (app.py runs without it), so skip when it's missing.
"""

import pytest

pytest.importorskip("flask_limiter")

from flask import Response

STREAMS = ["/api/v3/stream/stats", "/api/v3/stream/display", "/api/v3/stream/logs"]


@pytest.fixture
def client(monkeypatch):
    import web_interface.app as web_app
    assert web_app.limiter is not None
    # A real stream subscribes to a broadcaster and starts its generator
    # thread; only the connect matters here, so answer it with an empty body.
    monkeypatch.setattr(
        web_app, "_sse_stream",
        lambda broadcaster: Response("", mimetype="text/event-stream"),
    )
    web_app.app.config["TESTING"] = True
    web_app.limiter.reset()
    try:
        with web_app.app.test_client() as c:
            yield c
    finally:
        web_app.limiter.reset()


@pytest.mark.parametrize("url", STREAMS)
def test_reconnecting_more_than_200_times_a_minute_gets_429(client, url):
    statuses = [client.get(url).status_code for _ in range(200)]
    assert statuses == [200] * 200
    assert client.get(url).status_code == 429


def test_each_stream_has_its_own_budget(client):
    for _ in range(200):
        client.get(STREAMS[0])
    assert client.get(STREAMS[0]).status_code == 429
    assert client.get(STREAMS[1]).status_code == 200
