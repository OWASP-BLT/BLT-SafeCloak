# pylint: disable=too-few-public-methods
from workers import WorkerEntrypoint, Response
from urllib.parse import parse_qs, urlparse
from pathlib import Path
import re

from src.libs.utils import cors_response, html_response, json_response

# Route to HTML page mapping
PAGES_MAP = {
    '/': 'index.html',
    '/video-chat': 'video-chat.html',
    '/video-room': 'video-room.html',
    '/notes': 'notes.html',
    '/consent': 'consent.html',
}

API_PREFIX = '/api'
ROOM_ID_PATTERN = re.compile(r'^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$')


class Default(WorkerEntrypoint):
    """Worker entrypoint for handling HTTP requests and serving content."""

    @staticmethod
    def _validate_room_id(room_id: str) -> bool:
        """Validate room IDs using the same format as the frontend."""
        return bool(ROOM_ID_PATTERN.fullmatch(room_id.strip()))

    async def on_fetch(self, request, env):
        """Handle incoming HTTP requests and route them to the appropriate response."""
        url = urlparse(request.url)
        path = url.path

        # Handle CORS preflight
        if request.method == 'OPTIONS':
            return cors_response()

        # Basic JSON APIs
        if path == API_PREFIX or path.startswith(API_PREFIX + '/'):
            if path == '/api/rooms/validate':
                if request.method != 'GET':
                    return json_response({
                        'ok': False,
                        'error': {
                            'code': 'method_not_allowed',
                            'message': 'Only GET is supported for this endpoint.',
                        },
                    }, status=405)

                query = parse_qs(url.query)
                room_id = query.get('room', [''])[0].strip()

                if not room_id:
                    return json_response({
                        'ok': False,
                        'error': {
                            'code': 'missing_room_id',
                            'message': "Query parameter 'room' is required.",
                        },
                    }, status=400)

                return json_response({
                    'ok': True,
                    'roomId': room_id,
                    'isValid': self._validate_room_id(room_id),
                })

            return json_response({
                'ok': False,
                'error': {
                    'code': 'api_not_found',
                    'message': 'API endpoint not found.',
                },
            }, status=404)

        # Handle GET requests for HTML pages
        if request.method == 'GET' and path in PAGES_MAP:
            html_path = Path(__file__).parent / 'pages' / PAGES_MAP[path]
            html_content = html_path.read_text(encoding='utf-8')
            return html_response(html_content)

        # Serving static files from the 'public' directory
        if hasattr(env, 'ASSETS'):
            return await env.ASSETS.fetch(request)

        return Response('Not Found', status=404)
