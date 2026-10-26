"""Inyección de fallas (Fault Injection / Chaos Engineering) para la demostración.

Permite simular que la pasarela de pagos está caída o lenta y observar en
vivo cómo reaccionan el Retry y el Circuit Breaker del servicio de pedidos.
"""

from __future__ import annotations

import random
import threading
import time


class ChaosFailure(RuntimeError):
    pass


class ChaosMonkey:
    def __init__(self, failure_rate: float = 0.0, latency_ms: int = 0, rng=random.random) -> None:
        self._lock = threading.Lock()
        self.failure_rate = failure_rate
        self.latency_ms = latency_ms
        self._rng = rng
        self.injected_failures = 0

    def configure(self, failure_rate: float, latency_ms: int) -> None:
        with self._lock:
            self.failure_rate = failure_rate
            self.latency_ms = latency_ms

    def maybe_fail(self) -> None:
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        if self.failure_rate and self._rng() < self.failure_rate:
            with self._lock:
                self.injected_failures += 1
            raise ChaosFailure("Pasarela de pagos no disponible (falla inyectada)")

    def snapshot(self) -> dict:
        return {
            "failure_rate": self.failure_rate,
            "latency_ms": self.latency_ms,
            "injected_failures": self.injected_failures,
        }
