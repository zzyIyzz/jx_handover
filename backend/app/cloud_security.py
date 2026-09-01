"""HTTP protections applied when the service runs behind cloud HTTPS."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from app import config


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _normalized_origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.lower(), parsed.port or default_port


def origin_is_allowed(origin: str) -> bool:
    expected = _normalized_origin(config.PUBLIC_URL)
    actual = _normalized_origin(origin)
    return bool(expected and actual and _origins_match(actual, expected))


def _origins_match(
    actual: tuple[str, str, int | None],
    expected: tuple[str, str, int | None],
) -> bool:
    """Compare normalized origin tuples without accepting prefix lookalikes."""
    return actual == expected


async def protect_cloud_requests(request: Request, call_next):
    """Reject cross-site mutations and attach browser security headers.

    Requests without an ``Origin`` header remain valid for the container
    health check and administrator CLI tools.  Browser mutations do include an
    Origin header, so a foreign website cannot reuse an authenticated cookie.
    """
    if (
        config.APP_MODE == "cloud"
        and request.method in MUTATING_METHODS
        and request.url.path.startswith("/api/")
    ):
        origin = request.headers.get("origin", "").strip()
        if origin and not origin_is_allowed(origin):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": {
                        "code": "ORIGIN_NOT_ALLOWED",
                        "message": "请求来源与系统 HTTPS 地址不一致，操作已阻止。",
                    }
                },
            )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    if config.APP_MODE == "cloud":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: blob:; "
            "object-src 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'"
        )
    return response
