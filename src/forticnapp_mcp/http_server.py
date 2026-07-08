"""Streamable HTTP transport for the MCP server, for a shared/remote Docker deployment.

Reuses main.build_mcp_server() for everything transport-agnostic (spec loading, auth,
tool registry). The only things specific to this module: the Starlette app, a bearer-token
auth gate in front of /mcp, and a /healthz endpoint for Docker's HEALTHCHECK.

Single shared FortiCNAPP tenant, same as stdio: one FORTICNAPP_KEY_ID/SECRET per running
instance. All HTTP clients that present the correct FORTICNAPP_MCP_HTTP_TOKEN see that one
account's data -- there is no per-client credential isolation.
"""

from __future__ import annotations

import contextlib
import hmac
from collections.abc import AsyncIterator

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from .config import Settings
from .logging_utils import get_logger
from .main import build_mcp_server, fail, load_settings_or_fail

logger = get_logger(__name__)


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        presented = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if not presented or not hmac.compare_digest(presented, self._token):
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)


async def _healthz(_request: Request) -> Response:
    return PlainTextResponse("ok")


def build_app(settings: Settings) -> Starlette:
    if not settings.forticnapp_mcp_http_token:
        fail("FORTICNAPP_MCP_HTTP_TOKEN is required to run forticnapp-mcp-http")

    server, http_client = build_mcp_server(settings)
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            async with session_manager.run():
                yield
        finally:
            await http_client.aclose()

    return Starlette(
        routes=[
            Route("/healthz", _healthz, methods=["GET"]),
            # Mount() only matches "/mcp/..." -- a bare "/mcp" 307-redirects here, which
            # is fine (redirects preserve method+body for POST); clients should ideally
            # call "/mcp/" directly.
            Mount("/mcp", app=session_manager.handle_request),
        ],
        middleware=[
            Middleware(
                _BearerAuthMiddleware,
                token=settings.forticnapp_mcp_http_token.get_secret_value(),
            )
        ],
        lifespan=lifespan,
    )


def run() -> None:
    """Entry point for the `forticnapp-mcp-http` console script (see pyproject.toml)."""
    settings = load_settings_or_fail()
    app = build_app(settings)
    logger.info(
        f"forticnapp-mcp-http listening on {settings.forticnapp_mcp_http_host}:{settings.forticnapp_mcp_http_port}"
    )
    uvicorn.run(app, host=settings.forticnapp_mcp_http_host, port=settings.forticnapp_mcp_http_port)


if __name__ == "__main__":
    run()
