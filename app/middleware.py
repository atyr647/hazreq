"""Browser-storage session middleware.

Only registered when settings.browser_storage is true. Issues an opaque
hazreq_session cookie if missing and stamps the session id onto the
ASGI scope's state so get_session() can route to a per-cookie SQLite.
The actual per-cookie DB is created (and migrated) lazily on first
dependency resolution.

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) because
the latter wraps the request and state set inside it doesn't always
reach downstream handlers.
"""

from __future__ import annotations

import secrets

from starlette.datastructures import MutableHeaders

COOKIE_NAME = "hazreq_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5  # 5 years; never expire on us


def _new_session_id() -> str:
    return secrets.token_urlsafe(24)


def _parse_cookie(headers: list[tuple[bytes, bytes]]) -> str | None:
    for k, v in headers:
        if k.lower() == b"cookie":
            for chunk in v.decode("latin-1").split(";"):
                name, _, val = chunk.strip().partition("=")
                if name == COOKIE_NAME:
                    return val
    return None


class BrowserStorageMiddleware:
    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        sid = _parse_cookie(scope.get("headers", []))
        issued = False
        if not sid:
            sid = _new_session_id()
            issued = True

        # Make the id visible to dependencies via request.state.session_id.
        # Starlette wraps scope["state"] into request.state; setting it
        # here propagates to every handler reading the same scope.
        state = scope.setdefault("state", {})
        state["session_id"] = sid

        async def _send(message):  # noqa: ANN001
            if issued and message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                cookie = (
                    f"{COOKIE_NAME}={sid}; Max-Age={COOKIE_MAX_AGE}; Path=/; "
                    f"SameSite=Lax"
                )
                headers.append("set-cookie", cookie)
            await send(message)

        await self.app(scope, receive, _send)
