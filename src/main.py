# pylint: disable=too-few-public-methods
import json
from workers import WorkerEntrypoint, Response
from urllib.parse import urlparse
from pathlib import Path

try:
    from libs.utils import html_response, cors_response, base_headers
except ModuleNotFoundError:
    from src.libs.utils import html_response, cors_response, base_headers

# Route to HTML page mapping
PAGES_MAP = {
    '/': 'index.html',
    '/video-chat': 'video-chat.html',
    '/video-room': 'video-room.html',
    '/notes': 'notes.html',
    '/consent': 'consent.html',
}

APP_SHELL = [
    *PAGES_MAP.keys(),
    '/css/main.css',
    '/js/ui.js',
    '/js/crypto.js',
    '/js/notes.js',
    '/js/consent.js',
    '/js/video.js',
    '/img/logo.png',
    '/manifest.json',
]


def _origin(url) -> str:
    return f'{url.scheme}://{url.netloc}'


def _manifest_payload(origin: str) -> dict:
    return {
        'name': 'BLT-SafeCloak',
        'short_name': 'SafeCloak',
        'description': 'Privacy-focused peer-to-peer communication platform.',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#ffffff',
        'theme_color': '#E10101',
        'icons': [
            {
                'src': f'{origin}/img/logo.png',
                'sizes': '221x210',
                'type': 'image/png',
                'purpose': 'any maskable',
            },
        ],
    }


def _route_manifest_payload() -> dict:
    return {
        'pages': list(PAGES_MAP.keys()),
        'app_shell': APP_SHELL,
    }


class Default(WorkerEntrypoint):
    """Worker entrypoint for handling HTTP requests and serving content."""

    async def on_fetch(self, request, env):
        """Handle incoming HTTP requests and route them to the appropriate response."""
        url = urlparse(request.url)
        path = url.path

        # Handle CORS preflight
        if request.method == 'OPTIONS':
            return cors_response()

        if request.method == 'GET':
            origin = _origin(url)

            if path == '/manifest.json':
                return Response(
                    json.dumps(_manifest_payload(origin)),
                    status=200,
                    headers=base_headers('application/manifest+json; charset=utf-8'),
                )

            if path == '/routes.json':
                return Response(
                    json.dumps(_route_manifest_payload()),
                    status=200,
                    headers=base_headers('application/json; charset=utf-8'),
                )

        # Handle GET requests for HTML pages
        if request.method == 'GET' and path in PAGES_MAP:
            html_path = Path(__file__).parent / 'pages' / PAGES_MAP[path]
            html_content = html_path.read_text()
            return html_response(html_content)

        # Serving static files from the 'public' directory
        if hasattr(env, 'ASSETS'):
            return await env.ASSETS.fetch(request)

        return Response('Not Found', status=404)
