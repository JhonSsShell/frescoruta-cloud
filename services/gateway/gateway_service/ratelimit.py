"""Patrón Throttling / Rate Limiting con algoritmo Token Bucket.

Protege a los servicios internos de picos y abusos: cada cliente (API key o
IP) dispone de un "balde" de ``capacity`` fichas que se recarga a
``refill_per_second``. Cada petición consume una ficha; sin fichas se
responde HTTP 429 con el encabezado ``Retry-After``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    def __init__(self, capacity: int, refill_per_second: float, clock=time.monotonic) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self.rejected = 0

    def allow(self, client_id: str) -> tuple[bool, int, float]:
        """Devuelve ``(permitido, fichas_restantes, segundos_para_reintentar)``."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = self._buckets[client_id] = _Bucket(self.capacity, now)
            elapsed = now - bucket.updated_at
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
            bucket.updated_at = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True, int(bucket.tokens), 0.0
            self.rejected += 1
            retry_after = (1 - bucket.tokens) / self.refill_per_second
            return False, 0, retry_after
