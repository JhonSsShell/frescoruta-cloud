"""Patrones de resiliencia: Circuit Breaker y Retry con backoff exponencial.

Referencias: Nygard (2018), *Release It!*; Microsoft Azure Architecture
Center, patrones *Circuit Breaker* y *Retry*.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")
log = logging.getLogger("frescoruta.resilience")


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    """Se lanza cuando el circuito está abierto: se falla rápido sin llamar al servicio."""

    def __init__(self, name: str, retry_after: float) -> None:
        super().__init__(f"Circuito '{name}' abierto; reintentar en {retry_after:.1f}s")
        self.name = name
        self.retry_after = retry_after


class CircuitBreaker:
    """Máquina de estados CLOSED → OPEN → HALF_OPEN → CLOSED.

    * CLOSED: las llamadas pasan; se cuentan fallos consecutivos.
    * OPEN: tras ``failure_threshold`` fallos se rechazan llamadas durante
      ``recovery_timeout`` segundos (fail fast).
    * HALF_OPEN: se permite una llamada de prueba; si tiene éxito se cierra,
      si falla se vuelve a abrir.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False
        self.total_calls = 0
        self.total_failures = 0
        self.total_rejected = 0

    # -- estado ---------------------------------------------------------------
    def _refresh_state(self) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._clock() - self._opened_at >= self.recovery_timeout
        ):
            self._transition(CircuitState.HALF_OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        if new_state is not self._state:
            log.warning(f"Circuit breaker '{self.name}': {self._state} -> {new_state}")
        self._state = new_state
        if new_state is CircuitState.OPEN:
            self._opened_at = self._clock()
        if new_state is CircuitState.CLOSED:
            self._failures = 0
        self._half_open_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh_state()
            return self._state

    # -- ejecución ------------------------------------------------------------
    def call(self, fn: Callable[[], T]) -> T:
        with self._lock:
            self._refresh_state()
            if self._state is CircuitState.OPEN or (
                self._state is CircuitState.HALF_OPEN and self._half_open_in_flight
            ):
                self.total_rejected += 1
                retry_after = max(0.0, self.recovery_timeout - (self._clock() - self._opened_at))
                raise CircuitBreakerOpenError(self.name, retry_after)
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_in_flight = True
            self.total_calls += 1
        try:
            result = fn()
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    def _on_success(self) -> None:
        with self._lock:
            self._transition(CircuitState.CLOSED)

    def _on_failure(self) -> None:
        with self._lock:
            self.total_failures += 1
            if self._state is CircuitState.HALF_OPEN:
                self._transition(CircuitState.OPEN)
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._transition(CircuitState.OPEN)

    def reset(self) -> None:
        with self._lock:
            self._transition(CircuitState.CLOSED)

    def snapshot(self) -> dict:
        with self._lock:
            self._refresh_state()
            retry_after = 0.0
            if self._state is CircuitState.OPEN:
                retry_after = max(0.0, self.recovery_timeout - (self._clock() - self._opened_at))
            return {
                "name": self.name,
                "state": self._state.value,
                "consecutive_failures": self._failures,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_s": self.recovery_timeout,
                "retry_after_s": round(retry_after, 1),
                "total_calls": self.total_calls,
                "total_failures": self.total_failures,
                "total_rejected": self.total_rejected,
            }


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Ejecuta ``fn`` con reintentos y backoff exponencial con *full jitter*.

    Solo se reintentan las excepciones de ``retry_on`` (fallos transitorios).
    ``CircuitBreakerOpenError`` nunca debe incluirse: un circuito abierto
    significa que reintentar solo empeoraría la situación.
    """
    if attempts < 1:
        raise ValueError("attempts debe ser >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if isinstance(exc, CircuitBreakerOpenError) or attempt == attempts:
                raise
            delay = random.uniform(0, min(max_delay, base_delay * 2 ** (attempt - 1)))
            if on_retry:
                on_retry(attempt, exc, delay)
            log.info(f"Reintento {attempt}/{attempts - 1} en {delay:.2f}s: {exc}")
            sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover
