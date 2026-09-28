import json

from gateway.status import gateway_health_body_is_valid, probe_gateway_health


class _Response:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._body[:size]


def test_health_body_requires_hermes_stamp_and_known_live_status():
    assert gateway_health_body_is_valid({"platform": "hermes-agent", "status": "ok"})
    assert gateway_health_body_is_valid({"platform": "hermes-agent", "status": "degraded"})
    assert not gateway_health_body_is_valid({"status": "ok"})
    assert not gateway_health_body_is_valid({"platform": "other", "status": "ok"})
    assert not gateway_health_body_is_valid({"platform": "hermes-agent", "status": "stopped"})


def test_probe_falls_back_to_simple_health_with_shared_deadline():
    calls = []

    def urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        if request.full_url.endswith("/health/detailed"):
            raise OSError("not served")
        return _Response({"platform": "hermes-agent", "status": "ok"})

    alive, body = probe_gateway_health(
        "http://gateway:8642/health/detailed", timeout=1.0, urlopen=urlopen
    )

    assert alive is True
    assert body == {"platform": "hermes-agent", "status": "ok"}
    assert [url for url, _ in calls] == [
        "http://gateway:8642/health/detailed",
        "http://gateway:8642/health",
    ]
    assert 0 < calls[0][1] <= 0.5
    assert 0 < calls[1][1] <= 1.0


def test_probe_rejects_unstamped_200_and_oversized_body():
    responses = iter(
        [
            _Response({"status": "ok"}),
            _Response(b"{" + b"x" * (1024 * 1024 + 1)),
        ]
    )

    alive, body = probe_gateway_health(
        "http://gateway:8642", timeout=1.0,
        urlopen=lambda request, timeout: next(responses),
    )

    assert (alive, body) == (False, None)
