import asyncio
import json
import os
import sys
import types


mock_workers = types.ModuleType('workers')


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body = body.encode('utf-8') if isinstance(body, str) else body
        self.status_code = status
        self.headers = headers or {}


mock_workers.Response = FakeResponse
mock_workers.WorkerEntrypoint = type('WorkerEntrypoint', (), {})
sys.modules['workers'] = mock_workers

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.main import Default


class FakeRequest:
    def __init__(self, url, method='GET'):
        self.url = url
        self.method = method


class FakeAssets:
    def __init__(self):
        self.calls = []

    async def fetch(self, request):
        self.calls.append(request.url)
        return FakeResponse('asset response', status=200)


class FakeEnv:
    def __init__(self, assets=None):
        if assets is not None:
            self.ASSETS = assets


def _fetch(path, method='GET', env=None):
    worker = Default()
    request = FakeRequest(f'https://example.com{path}', method=method)
    return asyncio.run(worker.on_fetch(request, env or FakeEnv()))


def test_manifest_endpoint_returns_web_manifest():
    response = _fetch('/manifest.json')

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/manifest+json; charset=utf-8'

    manifest = json.loads(response.body)
    assert manifest['name'] == 'BLT-SafeCloak'
    assert manifest['start_url'] == '/'
    assert len(manifest['icons']) == 1
    assert manifest['icons'][0]['src'] == 'https://example.com/img/logo.png'


def test_options_requests_return_cors_preflight_response():
    response = _fetch('/manifest.json', method='OPTIONS')

    assert response.status_code == 204
    assert response.headers['Access-Control-Allow-Methods'] == 'GET, POST, OPTIONS'


def test_unknown_path_falls_back_to_assets_when_available():
    assets = FakeAssets()
    response = _fetch('/missing.txt', env=FakeEnv(assets))

    assert response.status_code == 200
    assert response.body == b'asset response'
    assert assets.calls == ['https://example.com/missing.txt']
