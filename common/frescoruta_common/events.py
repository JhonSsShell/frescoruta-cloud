"""Bus de eventos: patrón Publicador/Suscriptor (Publisher-Subscriber).

Dos implementaciones intercambiables detrás de la misma abstracción
(``EventBus``), lo que también ilustra el principio de inversión de
dependencias:

* ``RedisStreamEventBus``: usa Redis Streams con *consumer groups*, lo que
  da entrega *at-least-once*, persistencia mientras viva Redis y reparto de
  carga entre réplicas (Competing Consumers). Los mensajes que fallan
  repetidamente se envían a una cola de mensajes muertos (Dead Letter Queue).
* ``InMemoryEventBus``: despacho síncrono en el mismo proceso; útil para
  pruebas y para ejecutar un servicio de forma aislada.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import redis

from .context import get_request_id

log = logging.getLogger("frescoruta.events")

DEFAULT_STREAM = "frescoruta:events"
WILDCARD = "*"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Event:
    type: str
    source: str
    payload: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: str = field(default_factory=_now_iso)
    correlation_id: str | None = field(default_factory=get_request_id)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, raw: str) -> Event:
        return cls(**json.loads(raw))


Handler = Callable[[Event], None]


class EventBus(ABC):
    def __init__(self) -> None:
        self._handlers: list[tuple[set[str], Handler]] = []

    @abstractmethod
    def publish(self, event: Event) -> None: ...

    def subscribe(self, event_types: Iterable[str], handler: Handler) -> None:
        self._handlers.append((set(event_types) or {WILDCARD}, handler))

    def start(self) -> None:  # noqa: B027 - implementación opcional
        """Inicia la recepción de eventos (si aplica)."""

    def stop(self) -> None:  # noqa: B027 - implementación opcional
        """Detiene la recepción de eventos (si aplica)."""

    @property
    def kind(self) -> str:
        return self.__class__.__name__

    def _dispatch(self, event: Event) -> bool:
        ok = True
        for types, handler in self._handlers:
            if WILDCARD in types or event.type in types:
                try:
                    handler(event)
                except Exception:  # noqa: BLE001 - se registra y se reintenta
                    ok = False
                    log.exception(f"Error procesando evento {event.type} ({event.id})")
        return ok


class InMemoryEventBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[Event] = []

    def publish(self, event: Event) -> None:
        self.published.append(event)
        self._dispatch(event)


class RedisStreamEventBus(EventBus):
    MAX_DELIVERIES = 5

    def __init__(
        self,
        client: redis.Redis,
        group: str,
        consumer: str = "consumer-1",
        stream: str = DEFAULT_STREAM,
        block_ms: int = 2000,
        maxlen: int = 5000,
        pending_rescan_s: float = 30.0,
    ) -> None:
        super().__init__()
        self.client = client
        self.group = group
        self.consumer = consumer
        self.stream = stream
        self.dlq_stream = f"{stream}:dlq"
        self.block_ms = block_ms
        self.maxlen = maxlen
        self.pending_rescan_s = pending_rescan_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures: dict[str, int] = {}
        self.processed = 0

    # -- publicación ------------------------------------------------------------
    def publish(self, event: Event) -> None:
        try:
            self.client.xadd(
                self.stream, {"event": event.to_json()}, maxlen=self.maxlen, approximate=True
            )
            log.info(
                f"Evento publicado {event.type}", extra={"extra_fields": {"event_id": event.id}}
            )
        except redis.RedisError:
            # El flujo de negocio no se detiene si el broker falla. En producción
            # se usaría Transactional Outbox para garantizar la publicación.
            log.exception(f"No se pudo publicar el evento {event.type}")

    # -- consumo ----------------------------------------------------------------
    def start(self) -> None:
        if not self._handlers or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name=f"bus-{self.group}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.block_ms / 1000 + 1)
            self._thread = None

    def _ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def poll_once(self, cursor: str = ">") -> int:
        """Lee y procesa un lote. ``cursor='0'`` relee los pendientes del consumidor."""
        response = self.client.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: cursor},
            count=20,
            block=None if cursor == "0" else self.block_ms,
        )
        handled = 0
        for _stream, messages in response or []:
            for message_id, fields in messages:
                self._handle(message_id, fields)
                handled += 1
        return handled

    def _handle(self, message_id: str, fields: dict) -> None:
        try:
            event = Event.from_json(fields["event"])
        except (KeyError, TypeError, ValueError):
            log.error(f"Mensaje inválido {message_id}; se envía a DLQ")
            self._dead_letter(message_id, fields)
            return
        if self._dispatch(event):
            self.client.xack(self.stream, self.group, message_id)
            self._failures.pop(message_id, None)
            self.processed += 1
            return
        attempts = self._failures.get(message_id, 0) + 1
        self._failures[message_id] = attempts
        if attempts >= self.MAX_DELIVERIES:
            self._dead_letter(message_id, fields)

    def _dead_letter(self, message_id: str, fields: dict) -> None:
        self.client.xadd(
            self.dlq_stream, {**fields, "original_id": message_id, "group": self.group}
        )
        self.client.xack(self.stream, self.group, message_id)
        self._failures.pop(message_id, None)

    def _run(self) -> None:
        backoff = 1.0
        last_rescan = 0.0
        while not self._stop.is_set():
            try:
                self._ensure_group()
                if time.monotonic() - last_rescan > self.pending_rescan_s:
                    self.poll_once("0")  # reintenta mensajes pendientes (at-least-once)
                    last_rescan = time.monotonic()
                self.poll_once(">")
                backoff = 1.0
            except redis.RedisError as exc:
                log.warning(f"Bus de eventos sin conexión ({exc}); reintento en {backoff:.0f}s")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception:  # noqa: BLE001
                log.exception("Error inesperado en el consumidor de eventos")
                self._stop.wait(1.0)


def create_event_bus(
    redis_client: redis.Redis | None, group: str, consumer: str = "consumer-1"
) -> EventBus:
    if redis_client is None:
        return InMemoryEventBus()
    return RedisStreamEventBus(redis_client, group=group, consumer=consumer)
