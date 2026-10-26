"""Cliente HTTP entre servicios con descubrimiento, Retry y Circuit Breaker.

Distingue dos tipos de error:

* Errores técnicos (timeouts, conexión rechazada, HTTP 5xx) → se lanzan como
  ``ServiceUnavailableError``, cuentan para el Circuit Breaker y se
  reintentan.
* Respuestas de negocio (HTTP 4xx) → se devuelven al llamador sin
  reintentos; por ejemplo, "stock insuficiente" no es una falla del servicio.
"""

from __future__ import annotations

import httpx

from .context import REQUEST_ID_HEADER, get_request_id
from .middleware import INTERNAL_TOKEN_HEADER
from .registry import ServiceResolver
from .resilience import CircuitBreaker, retry_call


class ServiceUnavailableError(RuntimeError):
    def __init__(self, service: str, reason: str) -> None:
        super().__init__(f"{service} no disponible: {reason}")
        self.service = service
        self.reason = reason


class ServiceClient:
    def __init__(
        self,
        service_name: str,
        resolver: ServiceResolver,
        *,
        timeout: float = 5.0,
        retries: int = 2,
        breaker: CircuitBreaker | None = None,
        internal_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        retry_base_delay: float = 0.2,
    ) -> None:
        self.service_name = service_name
        self.resolver = resolver
        self.retries = retries
        self.breaker = breaker
        self.internal_token = internal_token
        self.retry_base_delay = retry_base_delay
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def _headers(self, extra: dict | None) -> dict:
        headers = dict(extra or {})
        request_id = get_request_id()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        if self.internal_token:
            headers[INTERNAL_TOKEN_HEADER] = self.internal_token
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        retry: bool = True,
    ) -> httpx.Response:
        def attempt() -> httpx.Response:
            base_url = self.resolver.resolve(self.service_name)
            try:
                response = self._client.request(
                    method, base_url + path, json=json, params=params, headers=self._headers(headers)
                )
            except httpx.TransportError as exc:
                self.resolver.invalidate(self.service_name)
                raise ServiceUnavailableError(self.service_name, exc.__class__.__name__) from exc
            if response.status_code >= 500:
                raise ServiceUnavailableError(self.service_name, f"HTTP {response.status_code}")
            return response

        guarded = (lambda: self.breaker.call(attempt)) if self.breaker else attempt
        if not retry or self.retries == 0:
            return guarded()
        return retry_call(
            guarded,
            attempts=self.retries + 1,
            base_delay=self.retry_base_delay,
            retry_on=(ServiceUnavailableError,),
        )

    def close(self) -> None:
        self._client.close()
