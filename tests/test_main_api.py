import asyncio
import json
import os
import sys
from types import SimpleNamespace

# Provide a minimal workers module for local test execution.
class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status
        self.headers = headers or {}


class FakeWorkerEntrypoint:
    pass


sys.modules["workers"] = SimpleNamespace(Response=FakeResponse, WorkerEntrypoint=FakeWorkerEntrypoint)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.main import Default  # noqa: E402


class FakeRequest:
    def __init__(self, url: str, method: str = "GET"):
        self.url = url
        self.method = method


class FakeEnv:
    pass


def _run(request: FakeRequest):
    return asyncio.run(Default().on_fetch(request, FakeEnv()))


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def test_api_health_returns_ok_payload():
    response = _run(FakeRequest("https://example.com/api/health"))

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    payload = _json(response)
    assert payload["ok"] is True
    assert payload["service"] == "blt-safecloak"
    assert "timestamp" in payload


def test_api_room_validation_valid_id():
    response = _run(FakeRequest("https://example.com/api/rooms/validate?room=ABC234"))

    assert response.status_code == 200
    payload = _json(response)
    assert payload == {"ok": True, "roomId": "ABC234", "isValid": True}


def test_api_room_validation_missing_param_returns_400():
    response = _run(FakeRequest("https://example.com/api/rooms/validate"))

    assert response.status_code == 400
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "missing_room_id"


def test_api_room_validation_invalid_id_returns_false():
    response = _run(FakeRequest("https://example.com/api/rooms/validate?room=ABCD12"))

    assert response.status_code == 200
    payload = _json(response)
    assert payload == {"ok": True, "roomId": "ABCD12", "isValid": False}


def test_api_unknown_path_returns_json_404():
    response = _run(FakeRequest("https://example.com/api/unknown"))

    assert response.status_code == 404
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "api_not_found"


def test_api_wrong_method_returns_json_405():
    response = _run(FakeRequest("https://example.com/api/health", method="POST"))

    assert response.status_code == 405
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "method_not_allowed"
