import asyncio
import json
import os
import sys
import pytest
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


VALIDATE_URL = "https://example.com/api/rooms/validate"


def _run(request: FakeRequest):
    return asyncio.run(Default().on_fetch(request, FakeEnv()))


def _json(response):
    return json.loads(response.body.decode("utf-8"))


def _assert_json_error(response, *, status_code: int, error_code: str):
    assert response.status_code == status_code
    payload = _json(response)
    assert payload["ok"] is False
    assert payload["error"]["code"] == error_code


def test_api_room_validation_get_valid_id():
    response = _run(FakeRequest(f"{VALIDATE_URL}?room=ABC234"))

    assert response.status_code == 200
    payload = _json(response)
    assert payload == {"ok": True, "roomId": "ABC234", "isValid": True}


def test_api_room_validation_get_missing_param_returns_400():
    response = _run(FakeRequest(VALIDATE_URL))

    _assert_json_error(response, status_code=400, error_code="missing_room_id")


@pytest.mark.parametrize("room_id", ["ABCD12", "abc234", "I1O0QZ", "ABCDE", "ABCDEFG"])
def test_api_room_validation_get_invalid_ids_return_false(room_id):
    response = _run(FakeRequest(f"{VALIDATE_URL}?room={room_id}"))

    assert response.status_code == 200
    payload = _json(response)
    assert payload == {"ok": True, "roomId": room_id, "isValid": False}


def test_api_room_validation_get_strips_whitespace():
    response = _run(FakeRequest(f"{VALIDATE_URL}?room=%20ABC234%20"))

    assert response.status_code == 200
    payload = _json(response)
    assert payload == {"ok": True, "roomId": "ABC234", "isValid": True}


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_api_room_validation_non_get_returns_json_405(method):
    response = _run(FakeRequest(VALIDATE_URL, method=method))

    _assert_json_error(response, status_code=405, error_code="method_not_allowed")


def test_api_unknown_path_returns_json_404():
    response = _run(FakeRequest("https://example.com/api/unknown"))

    _assert_json_error(response, status_code=404, error_code="api_not_found")


def test_api_base_path_returns_json_404():
    response = _run(FakeRequest("https://example.com/api"))

    _assert_json_error(response, status_code=404, error_code="api_not_found")


def test_options_preflight_returns_cors_response():
    response = _run(FakeRequest(VALIDATE_URL, method="OPTIONS"))

    assert response.status_code == 204
    assert "Access-Control-Allow-Methods" in response.headers
