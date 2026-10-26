import pytest
from frescoruta_common.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    retry_call,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def boom():
    raise ConnectionError("fallo")


def test_circuit_opens_after_threshold_and_fails_fast():
    clock = FakeClock()
    cb = CircuitBreaker("pagos", failure_threshold=3, recovery_timeout=10, clock=clock)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            cb.call(boom)
    assert cb.state is CircuitState.OPEN
    calls = []
    with pytest.raises(CircuitBreakerOpenError):
        cb.call(lambda: calls.append(1))
    assert calls == []  # no se invocó al servicio
    assert cb.snapshot()["total_rejected"] == 1


def test_half_open_success_closes_circuit():
    clock = FakeClock()
    cb = CircuitBreaker("pagos", failure_threshold=1, recovery_timeout=5, clock=clock)
    with pytest.raises(ConnectionError):
        cb.call(boom)
    clock.now = 6
    assert cb.state is CircuitState.HALF_OPEN
    assert cb.call(lambda: "ok") == "ok"
    assert cb.state is CircuitState.CLOSED


def test_half_open_failure_reopens_circuit():
    clock = FakeClock()
    cb = CircuitBreaker("pagos", failure_threshold=1, recovery_timeout=5, clock=clock)
    with pytest.raises(ConnectionError):
        cb.call(boom)
    clock.now = 6
    with pytest.raises(ConnectionError):
        cb.call(boom)
    assert cb.state is CircuitState.OPEN


def test_success_resets_consecutive_failures():
    cb = CircuitBreaker("x", failure_threshold=2)
    with pytest.raises(ConnectionError):
        cb.call(boom)
    cb.call(lambda: None)
    with pytest.raises(ConnectionError):
        cb.call(boom)
    assert cb.state is CircuitState.CLOSED


def test_retry_eventually_succeeds_with_backoff():
    attempts = {"n": 0}
    delays = []

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("transitorio")
        return "ok"

    result = retry_call(flaky, attempts=3, retry_on=(TimeoutError,), sleep=delays.append)
    assert result == "ok"
    assert attempts["n"] == 3
    assert len(delays) == 2


def test_retry_does_not_retry_non_transient_errors():
    attempts = {"n": 0}

    def bad():
        attempts["n"] += 1
        raise ValueError("error de negocio")

    with pytest.raises(ValueError):
        retry_call(bad, attempts=3, retry_on=(TimeoutError,), sleep=lambda _: None)
    assert attempts["n"] == 1


def test_retry_never_retries_open_circuit():
    attempts = {"n": 0}

    def open_circuit():
        attempts["n"] += 1
        raise CircuitBreakerOpenError("x", 5)

    with pytest.raises(CircuitBreakerOpenError):
        retry_call(open_circuit, attempts=3, retry_on=(Exception,), sleep=lambda _: None)
    assert attempts["n"] == 1
