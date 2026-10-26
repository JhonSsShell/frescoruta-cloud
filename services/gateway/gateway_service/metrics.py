"""Métricas básicas del gateway (observabilidad)."""

from __future__ import annotations

import threading
from collections import defaultdict, deque


class GatewayMetrics:
    def __init__(self, window: int = 500) -> None:
        self._lock = threading.Lock()
        self.requests = defaultdict(int)
        self.status_classes = defaultdict(int)
        self.latencies = defaultdict(lambda: deque(maxlen=window))

    def record(self, service: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self.requests[service] += 1
            self.status_classes[f"{status // 100}xx"] += 1
            self.latencies[service].append(latency_ms)

    def snapshot(self) -> dict:
        with self._lock:
            per_service = {}
            for service, values in self.latencies.items():
                ordered = sorted(values)
                p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0
                per_service[service] = {
                    "requests": self.requests[service],
                    "avg_ms": round(sum(ordered) / len(ordered), 1) if ordered else 0,
                    "p95_ms": round(p95, 1),
                }
            return {
                "total_requests": sum(self.requests.values()),
                "by_status_class": dict(self.status_classes),
                "by_service": per_service,
            }
