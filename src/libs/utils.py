"""
Utility functions for BLT-SafeCloak worker.

This module provides helper functions to generate HTTP responses
for HTML, JSON, and CORS preflight requests.

Key design decisions:
- Centralized header handling (DRY principle)
- Proper CORS support for both preflight AND actual responses
- Safer JSON serialization
"""

from workers import Response
import json
from typing import Any, Dict, Iterable, Optional


def base_headers(content_type: str, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Create a base set of headers for all responses.

    Why this exists:
    - Avoids repeating header logic (DRY)
    - Ensures CORS is applied consistently across all responses



    Args:
        content_type: The MIME type of the response

    Returns:
        Dictionary of headers
    """
    headers = {
        'Content-Type': content_type,

        # TODO: Replace wildcard CORS with an explicit allowed-origin config before
        # exposing any state-changing API routes.
        'Access-Control-Allow-Origin': '*',
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def html_response(html_str: str, status: int = 200) -> Response:
    """
    Create an HTML response.

    Args:
        html_str: HTML content to return
        status: HTTP status code (default: 200)

    Returns:
        Response object with HTML content type and CORS headers
    """
    return Response(
        html_str,
        status=status,
        headers=base_headers('text/html; charset=utf-8')
    )


def json_response(data: Any, status: int = 200) -> Response:
    """
    Create a JSON response.

    Improvements over basic implementation:
    - Supports non-ASCII characters (ensure_ascii=False)
    - Prevents crashes on non-serializable objects (default=str)

    Args:
        data: Any JSON-serializable data (dict, list, etc.)
        status: HTTP status code (default: 200)

    Returns:
        Response object with JSON content type and CORS headers
    """
    return Response(
        json.dumps(
            data,
            ensure_ascii=False,  # Keeps Unicode readable (e.g., हिंदी)
            default=str          # Fallback for non-serializable objects
        ),
        status=status,
        headers=base_headers('application/json; charset=utf-8')
    )


def json_error_response(message: str,
                        status: int = 400,
                        code: Optional[str] = None,
                        extra: Optional[Dict[str, Any]] = None,
                        headers: Optional[Dict[str, str]] = None) -> Response:
    """
    Create a JSON error response with a consistent schema.

    Args:
        message: Human-readable error message
        status: HTTP status code
        code: Optional machine-readable error code
        extra: Optional additional JSON fields
        headers: Optional extra response headers

    Returns:
        Response object with JSON error payload
    """
    payload = {
        'error': message,
        'status': status,
    }
    if code:
        payload['code'] = code
    if extra:
        payload.update(extra)
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        status=status,
        headers=base_headers('application/json; charset=utf-8', headers)
    )


def cors_response(status: int = 204,
                  allow_methods: str = 'GET, POST, OPTIONS',
                  allow_headers: str = 'Content-Type') -> Response:
    """
    Create a CORS preflight (OPTIONS) response.

    When this is used:
    - Browser sends an OPTIONS request before certain requests
    - This tells the browser what is allowed

   
    Args:
        status: HTTP status code (default: 204 No Content)

    Returns:
        Response object with CORS headers
    """
    return Response(
        None,  # 204 responses should not include a body
        status=status,
        headers={
            # TODO: Replace wildcard CORS with an explicit allowed-origin config before
            # exposing any state-changing API routes.
            'Access-Control-Allow-Origin': '*',

            # Allowed HTTP methods
            'Access-Control-Allow-Methods': allow_methods,

            # Allowed request headers
            'Access-Control-Allow-Headers': allow_headers,

            # Cache preflight response (in seconds basically 1 day)
            # Reduces repeated OPTIONS requests
            'Access-Control-Max-Age': '86400',
        }
    )


def method_not_allowed_response(allowed_methods: Iterable[str], message: Optional[str] = None) -> Response:
    """
    Create a 405 Method Not Allowed response with both Allow and CORS method headers.

    Args:
        allowed_methods: Iterable of supported HTTP methods
        message: Optional override for the error message

    Returns:
        Response object with JSON error payload
    """
    methods_list = list(allowed_methods)
    methods = ', '.join(methods_list)
    return json_error_response(
        message or 'Method not allowed',
        status=405,
        code='method_not_allowed',
        extra={'allowed_methods': methods_list},
        headers={
            'Allow': methods,
            'Access-Control-Allow-Methods': methods,
        })
