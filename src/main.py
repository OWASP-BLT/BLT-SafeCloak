# pylint: disable=too-few-public-methods
from workers import WorkerEntrypoint, Response
from urllib.parse import urlparse
from pathlib import Path

from libs.utils import html_response, cors_response

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

        # Handle GET requests for HTML pages
        if request.method == 'GET' and path in PAGES_MAP:
            # Use resolve() to handle path normalization on Windows/Unix
            base_path = Path(__file__).parent.resolve()
            html_path = base_path / 'pages' / PAGES_MAP[path]
            html_content = html_path.read_text()
            return html_response(html_content)

        # Serving static files from the 'public' directory
        if hasattr(env, 'ASSETS'):
            return await env.ASSETS.fetch(request)

        return Response('Not Found', status=404)
