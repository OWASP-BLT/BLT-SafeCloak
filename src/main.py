# pylint: disable=too-few-public-methods
from workers import WorkerEntrypoint, Response
from urllib.parse import urlparse
from pathlib import Path

from libs.utils import html_response, json_response, cors_response

# Route to HTML page mapping
PAGES_MAP = {
    '/': 'index.html',
    '/video-chat': 'video-chat.html',
    '/notes': 'notes.html',
    '/consent': 'consent.html',
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

        # POST /api/transcribe — Cloudflare AI Whisper transcription (fallback for browsers
        # that do not support the Web Speech API)
        if request.method == 'POST' and path == '/api/transcribe':
            if not hasattr(env, 'AI'):
                return json_response({'error': 'AI binding not configured'}, status=503)
            try:
                audio_bytes = await request.bytes()
                result = await env.AI.run('@cf/openai/whisper', {'audio': list(audio_bytes)})
                text = result.get('text', '') if isinstance(result, dict) else ''
                return json_response({'text': text})
            except Exception as exc:  # pylint: disable=broad-except
                return json_response({'error': str(exc)}, status=500)

        # Handle GET requests for HTML pages
        if request.method == 'GET' and path in PAGES_MAP:
            html_path = Path(__file__).parent / 'pages' / PAGES_MAP[path]
            html_content = html_path.read_text()
            return html_response(html_content)

        # Serving static files from the 'public' directory
        if hasattr(env, 'ASSETS'):
            return await env.ASSETS.fetch(request)

        return Response('Asset server not configured')
