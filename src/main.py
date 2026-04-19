# pylint: disable=too-few-public-methods
import asyncio
import json
import secrets
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from libs.utils import base_headers, cors_response, html_response, json_response

# Route to HTML page mapping
PAGES_MAP = {
    '/': 'index.html',
    '/video-chat': 'video-chat.html',
    '/video-room': 'video-room.html',
    '/notes': 'notes.html',
    '/consent': 'consent.html',
}

PUBLIC_ROOM_PATH = '/api/public-rooms'
PUBLIC_ROOM_ID_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
PUBLIC_ROOM_ID_LENGTH = 6
PUBLIC_ROOM_TTL_HOURS = 4
PUBLIC_ROOM_MAX_COUNT = 100
PUBLIC_ROOM_ID_MAX_RETRIES = 20

PUBLIC_ROOMS = []


def _utc_now():
    return datetime.now(timezone.utc)


def _normalize_text(value, max_length):
    normalized = ' '.join(str(value or '').strip().split())
    return normalized[:max_length]


def _generate_room_id():
    return ''.join(secrets.choice(PUBLIC_ROOM_ID_CHARS) for _ in range(PUBLIC_ROOM_ID_LENGTH))


def _cleanup_public_rooms():
    cutoff = _utc_now() - timedelta(hours=PUBLIC_ROOM_TTL_HOURS)
    PUBLIC_ROOMS[:] = [room for room in PUBLIC_ROOMS if room['created_at'] >= cutoff]


def _serialize_room(room):
    return {
        'id': room['id'],
        'name': room['name'],
        'topic': room['topic'],
        'hostName': room['host_name'],
        'createdAt': room['created_at'].isoformat(),
    }


def _list_public_rooms():
    _cleanup_public_rooms()
    sorted_rooms = sorted(PUBLIC_ROOMS, key=lambda room: room['created_at'], reverse=True)
    return {'rooms': [_serialize_room(room) for room in sorted_rooms]}


def _create_public_room(payload):
    room_name = _normalize_text(payload.get('roomName'), 60)
    topic = _normalize_text(payload.get('topic'), 120)
    host_name = _normalize_text(payload.get('hostName'), 40)

    if not room_name:
        return {'error': 'roomName is required'}, 400
    if not topic:
        return {'error': 'topic is required'}, 400

    _cleanup_public_rooms()
    existing_ids = {room['id'] for room in PUBLIC_ROOMS}

    room_id = _generate_room_id()
    retries = 0
    while room_id in existing_ids and retries < PUBLIC_ROOM_ID_MAX_RETRIES:
        room_id = _generate_room_id()
        retries += 1

    if room_id in existing_ids:
        return {'error': 'Unable to allocate room ID. Please try again.'}, 503

    room = {
        'id': room_id,
        'name': room_name,
        'topic': topic,
        'host_name': host_name,
        'created_at': _utc_now(),
    }
    PUBLIC_ROOMS.append(room)

    if len(PUBLIC_ROOMS) > PUBLIC_ROOM_MAX_COUNT:
        PUBLIC_ROOMS.sort(key=lambda entry: entry['created_at'])
        PUBLIC_ROOMS[:] = PUBLIC_ROOMS[-PUBLIC_ROOM_MAX_COUNT:]

    return {'room': _serialize_room(room)}, 201


async def _read_json_body(request):
    raw = await request.text()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


class Default(WorkerEntrypoint):
    """Worker entrypoint for handling HTTP requests and serving content."""

    async def on_fetch(self, request, env):
        """Handle incoming HTTP requests and route them to the appropriate response."""
        url = urlparse(request.url)
        path = url.path

        try:
            # Handle CORS preflight
            if request.method == 'OPTIONS':
                return cors_response()

            if path == PUBLIC_ROOM_PATH:
                if request.method == 'GET':
                    return json_response(_list_public_rooms())
                if request.method == 'POST':
                    payload = await _read_json_body(request)
                    if payload is None:
                        return json_response({'error': 'Invalid JSON body'}, status=400)
                    body, status = _create_public_room(payload)
                    return json_response(body, status=status)
                return json_response({'error': 'Method not allowed'}, status=405)

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
            return Response('Not Found',
                            status=404,
                            headers=base_headers('text/plain; charset=utf-8'))
        except asyncio.CancelledError:
            # Preserve cooperative cancellation semantics for async runtimes.
            raise
        except Exception as exc:
            traceback.print_exc()
            return Response('Internal Server Error',
                            status=500,
                            headers=base_headers('text/plain; charset=utf-8'))
