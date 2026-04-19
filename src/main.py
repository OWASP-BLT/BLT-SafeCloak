# pylint: disable=too-few-public-methods
import asyncio
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from workers import WorkerEntrypoint, Response

from libs.utils import (
    base_headers,
    cors_response,
    html_response,
    json_error_response,
    json_response,
    method_not_allowed_response,
)

# Route to HTML page mapping
PAGES_MAP = {
    '/': 'index.html',
    '/video-chat': 'video-chat.html',
    '/video-room': 'video-room.html',
    '/notes': 'notes.html',
    '/consent': 'consent.html',
}

API_READ_ONLY_METHODS = ('GET', 'OPTIONS')
API_NOTES_PATH = '/api/notes'
API_CONSENT_PATH = '/api/consent'


def _utc_timestamp():
    """Return a UTC ISO-8601 timestamp for deployment verification."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _api_health_payload():
    """Return basic service metadata for uptime and readiness checks."""
    return {
        'status': 'ok',
        'service': 'blt-safecloak',
        'runtime': 'cloudflare-python-worker',
        'version': '0.1.0',
        'timestamp': _utc_timestamp(),
    }


def _api_features_payload():
    """Return a concise description of the API surface and storage boundaries."""
    return {
        'api': 'v1',
        'resources': {
            'notes': {
                'endpoint': API_NOTES_PATH,
                'storage': 'browser-local-storage',
                'encryption': 'client-side',
                'placeholder': True,
                'allowed_methods': list(API_READ_ONLY_METHODS),
                'write_methods_disabled': ['POST', 'PUT', 'PATCH', 'DELETE'],
            },
            'consent': {
                'endpoint': API_CONSENT_PATH,
                'storage': 'browser-local-storage',
                'integrity': 'client-side-sha256',
                'placeholder': True,
                'allowed_methods': list(API_READ_ONLY_METHODS),
                'write_methods_disabled': ['POST', 'PUT', 'PATCH', 'DELETE'],
            },
        },
    }


def _read_only_resource_payload(resource_name: str, description: str):
    """Return metadata for resources that intentionally do not expose server writes."""
    return {
        'resource': resource_name,
        'mode': 'read-only-metadata',
        'storage': 'browser-local-storage',
        'allowed_methods': list(API_READ_ONLY_METHODS),
        'writes_enabled': False,
        'placeholder': True,
        'message': description,
    }


def _handle_api_request(request, path):
    """Handle API routes and enforce read-only semantics where required."""
    if request.method == 'OPTIONS':
        if path in ('/api/health', '/api/features'):
            return cors_response(allow_methods='GET, OPTIONS')
        if path in (API_NOTES_PATH, API_CONSENT_PATH):
            return cors_response(allow_methods='GET, OPTIONS')
        return cors_response(allow_methods='GET, OPTIONS')

    if path == '/api/health':
        if request.method != 'GET':
            return method_not_allowed_response(API_READ_ONLY_METHODS)
        return json_response(_api_health_payload())

    if path == '/api/features':
        if request.method != 'GET':
            return method_not_allowed_response(API_READ_ONLY_METHODS)
        return json_response(_api_features_payload())

    if path == API_NOTES_PATH:
        # TODO: Replace this placeholder metadata endpoint with a reviewed backend notes contract
        # only after the privacy model, storage design, and allowed CORS origins are approved.
        if request.method != 'GET':
            return method_not_allowed_response(
                API_READ_ONLY_METHODS,
                message='Notes API is read-only; POST, PUT, PATCH, and DELETE are disabled.')
        return json_response(
            _read_only_resource_payload(
                'notes',
                'Notes remain encrypted client-side and stored only in the browser; '
                'the backend does not create, update, or delete note content.',
            ))

    if path == API_CONSENT_PATH:
        # TODO: Replace this placeholder metadata endpoint with a reviewed consent-audit API
        # only after data-retention rules, storage design, and allowed CORS origins are approved.
        if request.method != 'GET':
            return method_not_allowed_response(
                API_READ_ONLY_METHODS,
                message='Consent logging API is read-only; POST, PUT, PATCH, and DELETE are disabled.')
        return json_response(
            _read_only_resource_payload(
                'consent',
                'Consent records remain local to the browser; the backend exposes policy metadata '
                'but does not write or delete consent log entries.',
            ))

    return json_error_response('API route not found', status=404, code='not_found')


class Default(WorkerEntrypoint):
    """Worker entrypoint for handling HTTP requests and serving content."""

    async def on_fetch(self, request, env):
        """Handle incoming HTTP requests and route them to the appropriate response."""
        url = urlparse(request.url)
        path = url.path
        try:
            # Handle CORS preflight
            if request.method == 'OPTIONS':
                if path.startswith('/api/'):
                    return _handle_api_request(request, path)
                return cors_response()

            if path.startswith('/api/'):
                return _handle_api_request(request, path)

            # Handle GET requests for HTML pages
            if request.method == 'GET' and path in PAGES_MAP:
                html_path = Path(__file__).parent / 'pages' / PAGES_MAP[path]
                html_content = html_path.read_text(encoding='utf-8')
                return html_response(html_content)

            # Serving static files from the 'public' directory
            if hasattr(env, 'ASSETS'):
                return await env.ASSETS.fetch(request)

            return Response('Not Found', status=404)

        except FileNotFoundError as exc:
            print(f'[404] Page file not found: {exc}')
            return Response(
                'Not Found',
                status=404,
                headers=base_headers('text/plain; charset=utf-8')
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            traceback.print_exc()
            return Response(
                'Internal Server Error',
                status=500,
                headers=base_headers('text/plain; charset=utf-8')
            )
