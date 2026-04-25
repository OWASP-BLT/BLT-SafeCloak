# pylint: disable=too-few-public-methods
import asyncio
import traceback

from workers import WorkerEntrypoint, Response
from urllib.parse import urlparse
from pathlib import Path

from libs.utils import html_response, cors_response, base_headers

# Route to HTML page mapping
PAGES_MAP = {
    '/': 'index.html',
    '/video-chat': 'video-chat.html',
    '/video-room': 'video-room.html',
    '/notes': 'notes.html',
    '/consent': 'consent.html',
}


class Default(WorkerEntrypoint):
    """Worker entrypoint for handling HTTP requests and serving content."""

    async def on_fetch(self, request, env):
        """Handle incoming HTTP requests and route them to the appropriate response."""
        url = urlparse(request.url)
        path = url.path

        try:
            if request.method == 'POST' and path == '/_csp-report':
                # Best-effort endpoint for CSP violation reports. We don't persist
                # reports yet, but enabling collection allows future analysis.
                #
                # Keep this handler defensive since it may receive arbitrary traffic.
                headers = getattr(request, 'headers', None)
                content_type = headers.get('content-type') if headers else None
                allowed_types = {'application/csp-report', 'application/reports+json'}
                if content_type:
                    content_type = content_type.split(';', 1)[0].strip().lower()
                    if content_type not in allowed_types:
                        return Response(
                            'Unsupported Media Type',
                            status=415,
                            headers=base_headers('text/plain; charset=utf-8')
                        )
                content_length = headers.get('content-length') if headers else None
                try:
                    if content_length and int(content_length) > 32_768:
                        return Response(
                            'Payload Too Large',
                            status=413,
                            headers=base_headers('text/plain; charset=utf-8')
                        )
                except (TypeError, ValueError):
                    # Ignore malformed content-length and treat as unknown size.
                    pass
                return Response(
                    None,
                    status=204,
                    headers=base_headers('text/plain; charset=utf-8')
                )

            # Handle CORS preflight
            if request.method == 'OPTIONS':
                return cors_response()

            # Handle GET requests for HTML pages
            if request.method == 'GET' and path in PAGES_MAP:
                html_path = Path(__file__).parent / 'pages' / PAGES_MAP[path]
                html_content = html_path.read_text(encoding='utf-8')
                return html_response(html_content)

            # Serving static files from the 'public' directory
            if hasattr(env, 'ASSETS'):
                return await env.ASSETS.fetch(request)

            return Response(
                'Not Found',
                status=404,
                headers=base_headers('text/plain; charset=utf-8')
            )

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
