"""Middlewares transversales: correlación de peticiones y autenticación interna."""

from __future__ import annotations

import hmac
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .context import REQUEST_ID_HEADER, new_request_id, reset_request_id, set_request_id
from .logging_setup import log_extra

INTERNAL_TOKEN_HEADER = "X-Internal-Token"
PUBLIC_PATHS = ("/health", "/docs", "/openapi.json", "/redoc")

log = logging.getLogger("frescoruta.http")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propaga (o genera) ``X-Request-ID`` y registra cada petición con su latencia."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = set_request_id(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            if request.url.path != "/health":
                log.info(
                    f"{request.method} {request.url.path} -> {status}",
                    extra=log_extra(
                        method=request.method,
                        path=request.url.path,
                        status=status,
                        latency_ms=elapsed_ms,
                    ),
                )
            reset_request_id(token)


class InternalAuthMiddleware(BaseHTTPMiddleware):
    """Exige un token compartido entre servicios (defensa en profundidad).

    En Render los servicios gratuitos son públicos, por lo que solo el API
    Gateway (que conoce el token) debe poder invocarlos. Si no se configura
    ``INTERNAL_TOKEN`` (desarrollo local) el control se desactiva.
    """

    def __init__(self, app, token: str | None) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if not self.token or request.url.path.startswith(PUBLIC_PATHS):
            return await call_next(request)
        provided = request.headers.get(INTERNAL_TOKEN_HEADER, "")
        if not hmac.compare_digest(provided, self.token):
            return JSONResponse(
                {"detail": "Acceso interno no autorizado", "code": "INTERNAL_AUTH"},
                status_code=401,
            )
        return await call_next(request)
