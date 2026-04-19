import asyncio
import importlib
import json
import os
import sys
from datetime import datetime
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body = body.encode('utf-8') if isinstance(body, str) else body
        self.status_code = status
        self.headers = headers or {}


class FakeWorkerEntrypoint:
    pass


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC_ROOT = os.path.join(ROOT, 'src')


class FakeRequest:
    def __init__(self, url, method='GET'):
        self.url = url
        self.method = method


@pytest.fixture
def main_module(monkeypatch):
    mock_workers = ModuleType('workers')
    mock_workers.Response = FakeResponse
    mock_workers.WorkerEntrypoint = FakeWorkerEntrypoint

    monkeypatch.setitem(sys.modules, 'workers', mock_workers)
    monkeypatch.syspath_prepend(SRC_ROOT)
    monkeypatch.delitem(sys.modules, 'main', raising=False)

    importlib.invalidate_caches()
    module = importlib.import_module('main')
    yield module

    monkeypatch.delitem(sys.modules, 'main', raising=False)


def run_fetch(main_module, path, method='GET', env=None):
    app = main_module.Default()
    request = FakeRequest(f'https://example.com{path}', method=method)
    runtime_env = env if env is not None else SimpleNamespace()
    return asyncio.run(app.on_fetch(request, runtime_env))


def test_health_endpoint_returns_service_metadata(main_module):
    response = run_fetch(main_module, '/api/health')

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/json; charset=utf-8'
    payload = json.loads(response.body)
    assert payload['status'] == 'ok'
    assert payload['service'] == 'blt-safecloak'
    assert payload['version'] == '0.1.0'
    assert payload['timestamp'].endswith('Z')
    assert datetime.fromisoformat(payload['timestamp'].replace('Z', '+00:00'))


def test_features_endpoint_describes_read_only_resources(main_module):
    response = run_fetch(main_module, '/api/features')

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload['resources']['notes']['placeholder'] is True
    assert payload['resources']['notes']['storage'] == 'browser-local-storage'
    assert payload['resources']['notes']['allowed_methods'] == ['GET', 'OPTIONS']
    assert payload['resources']['consent']['placeholder'] is True
    assert payload['resources']['consent']['storage'] == 'browser-local-storage'
    assert payload['resources']['consent']['write_methods_disabled'] == [
        'POST',
        'PUT',
        'PATCH',
        'DELETE',
    ]


def test_notes_endpoint_is_read_only_metadata(main_module):
    response = run_fetch(main_module, '/api/notes')

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload['resource'] == 'notes'
    assert payload['placeholder'] is True
    assert payload['writes_enabled'] is False
    assert payload['storage'] == 'browser-local-storage'


def test_consent_endpoint_rejects_post(main_module):
    response = run_fetch(main_module, '/api/consent', method='POST')

    assert response.status_code == 405
    assert response.headers['Allow'] == 'GET, OPTIONS'
    payload = json.loads(response.body)
    assert payload['code'] == 'method_not_allowed'
    assert payload['allowed_methods'] == ['GET', 'OPTIONS']


def test_notes_options_preflight_only_allows_get_and_options(main_module):
    response = run_fetch(main_module, '/api/notes', method='OPTIONS')

    assert response.status_code == 204
    assert response.headers['Access-Control-Allow-Methods'] == 'GET, OPTIONS'


def test_unknown_api_route_returns_json_404(main_module):
    response = run_fetch(main_module, '/api/unknown')

    assert response.status_code == 404
    payload = json.loads(response.body)
    assert payload['code'] == 'not_found'


def test_non_api_routes_still_fall_back_to_assets(main_module):
    asset_response = FakeResponse('asset payload', status=200, headers={'Content-Type': 'text/plain'})
    assets = SimpleNamespace(fetch=AsyncMock(return_value=asset_response))
    env = SimpleNamespace(ASSETS=assets)

    response = run_fetch(main_module, '/css/main.css', env=env)

    assert response is asset_response
    assets.fetch.assert_called_once()
