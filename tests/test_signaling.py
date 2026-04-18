"""Tests for worker routing and response utility helpers."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import pytest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, Optional
from concurrent.futures import ThreadPoolExecutor


class FakeResponse:
    """Minimal stand-in for the Cloudflare Workers Response object."""

    def __init__(self, body=None, *, status=200, headers=None):
        self.body = body if body is not None else ""
        self.status = status
        self.headers = headers or {}


class FakeWorkerEntrypoint:
    """Minimal WorkerEntrypoint base class."""


class FakeRequest:
    """Minimal request object used by Default.on_fetch tests."""

    def __init__(self, method, url):
        self.method = method
        self.url = url


class FakeWorkers(ModuleType):
    """Synthetic 'workers' module replacing the Cloudflare runtime import."""
    Response = FakeResponse
    WorkerEntrypoint = FakeWorkerEntrypoint


@pytest.fixture
def app_modules(monkeypatch):
    """Import app modules safely within a fixture, injecting the workers stub."""
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")

    # Prepare environment
    if src_path not in sys.path:
        monkeypatch.syspath_prepend(src_path)
    monkeypatch.setitem(sys.modules, "workers", FakeWorkers("workers"))

    # Import modules (Python caches them in sys.modules)
    utils_module = importlib.import_module("libs.utils")
    main_module = importlib.import_module("main")
    return utils_module, main_module


def _run_async(coro):
    """Run an async function in a private event loop in a separate thread."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


def test_html_response_sets_html_content_type(app_modules) -> None:
    """html_response should include HTML content type and preserve body/status."""
    utils_module, _ = app_modules
    response = utils_module.html_response("<h1>ok</h1>", status=202)

    assert isinstance(response, FakeResponse)
    assert response.status == 202
    assert response.body == "<h1>ok</h1>"
    assert response.headers["Content-Type"].startswith("text/html")


def test_json_response_serializes_payload(app_modules) -> None:
    """json_response should serialize JSON and apply expected response headers."""
    utils_module, _ = app_modules
    payload = {"ok": True, "count": 3}

    response = utils_module.json_response(payload, status=201)

    assert isinstance(response, FakeResponse)
    assert response.status == 201
    assert response.headers["Content-Type"].startswith("application/json")
    assert json.loads(response.body) == payload


def test_cors_response_has_preflight_headers(app_modules) -> None:
    """cors_response should include standard preflight headers and default 204 status."""
    utils_module, _ = app_modules
    response = utils_module.cors_response()

    assert response.status == 204
    assert response.body == ""
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert response.headers["Access-Control-Allow-Headers"] == "Content-Type"


def test_pages_map_contains_expected_routes(app_modules) -> None:
    """PAGES_MAP should expose the four documented clean URLs."""
    _, main_module = app_modules

    assert main_module.PAGES_MAP == {
        "/": "index.html",
        "/video-chat": "video-chat.html",
        "/video-room": "video-room.html",
        "/notes": "notes.html",
        "/consent": "consent.html",
    }


def test_on_fetch_options_returns_cors_response(app_modules) -> None:
    """OPTIONS requests should short-circuit with CORS preflight response."""
    _, main_module = app_modules
    worker = main_module.Default()

    request = FakeRequest("OPTIONS", "https://safecloak.example/video-chat")
    response = _run_async(worker.on_fetch(request, SimpleNamespace()))

    assert isinstance(response, FakeResponse)
    assert response.status == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_on_fetch_get_known_page_returns_html(app_modules) -> None:
    """GET for a mapped route should load and return the corresponding HTML page."""
    _, main_module = app_modules
    worker = main_module.Default()

    request = FakeRequest("GET", "https://safecloak.example/video-chat")
    response = _run_async(worker.on_fetch(request, SimpleNamespace()))

    assert isinstance(response, FakeResponse)
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert "Secure Video Chat" in response.body


def test_on_fetch_uses_assets_binding_for_unknown_path(app_modules) -> None:
    """Unknown routes should be delegated to env.ASSETS when binding is available."""
    _, main_module = app_modules
    worker = main_module.Default()

    async def fake_fetch(_request) -> FakeResponse:
        return FakeResponse("asset-response", status=200)

    env = SimpleNamespace(ASSETS=SimpleNamespace(fetch=fake_fetch))
    request = FakeRequest("GET", "https://safecloak.example/js/video.js")
    response = _run_async(worker.on_fetch(request, env))

    assert isinstance(response, FakeResponse)
    assert response.status == 200
    assert response.body == "asset-response"


def test_on_fetch_without_assets_binding_returns_fallback_response(app_modules) -> None:
    """Without ASSETS binding, unknown routes should return the fallback message."""
    _, main_module = app_modules
    worker = main_module.Default()

    request = FakeRequest("GET", "https://safecloak.example/unknown")
    response = _run_async(worker.on_fetch(request, SimpleNamespace()))

    assert isinstance(response, FakeResponse)
    assert response.status == 404
    assert response.body == "Not Found"
