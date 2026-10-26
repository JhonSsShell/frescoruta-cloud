"""Reacción a eventos de dominio (Observer / Publish-Subscribe).

``NotificationDispatcher`` traduce cada tipo de evento en una o varias
notificaciones según reglas declarativas. El procesamiento es idempotente:
si Redis Streams entrega dos veces el mismo evento (at-least-once) no se
duplican los mensajes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from frescoruta_common.events import Event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from .channels import EmailChannel, NotificationChannel, WhatsAppChannel
from .models import Notification

log = logging.getLogger("notification-service")
PURCHASING_EMAIL = "compras@frescoruta.co"


def cop(value: int) -> str:
    return "$ " + f"{int(value):,}".replace(",", ".")


@dataclass(frozen=True)
class Rule:
    channel: NotificationChannel
    recipient: Callable[[dict], str]
    subject: Callable[[dict], str]
    message: Callable[[dict], str]


def default_rules() -> dict[str, list[Rule]]:
    email, whatsapp = EmailChannel(), WhatsAppChannel()
    return {
        "order.confirmed": [
            Rule(
                email,
                lambda p: p["customer_email"],
                lambda p: f"Pedido {p['order_id']} confirmado",
                lambda p: (
                    f"Hola {p['customer_name']}, tu pedido {p['order_id']} por {cop(p['total'])} "
                    f"fue confirmado y será despachado a {p['delivery_city']}."
                ),
            ),
            Rule(
                whatsapp,
                lambda p: p["customer_name"],
                lambda p: "Confirmación de pedido",
                lambda p: f"FrescoRuta: pedido {p['order_id']} confirmado ({cop(p['total'])}).",
            ),
        ],
        "order.rejected": [
            Rule(
                email,
                lambda p: p["customer_email"],
                lambda p: f"No pudimos procesar el pedido {p['order_id']}",
                lambda p: f"Hola {p['customer_name']}, tu pedido no fue procesado: {p.get('reason')}.",
            )
        ],
        "payment.refunded": [
            Rule(
                email,
                lambda p: f"pedido {p['order_id']}",
                lambda p: f"Reembolso del pago {p['id']}",
                lambda p: f"Se reembolsaron {cop(p['amount'])} del pedido {p['order_id']}.",
            )
        ],
        "stock.low": [
            Rule(
                email,
                lambda p: PURCHASING_EMAIL,
                lambda p: f"Stock bajo: {p['name']}",
                lambda p: (
                    f"El producto {p['product_id']} ({p['name']}) quedó con {p['stock']} unidades "
                    f"(umbral {p['threshold']}). Programar compra a proveedores."
                ),
            )
        ],
    }


class NotificationDispatcher:
    def __init__(self, session_factory: sessionmaker, rules: dict[str, list[Rule]] | None = None):
        self.session_factory = session_factory
        self.rules = rules or default_rules()
        self.duplicates_ignored = 0

    @property
    def event_types(self) -> list[str]:
        return list(self.rules)

    def handle(self, event: Event) -> None:
        for rule in self.rules.get(event.type, []):
            payload = event.payload
            with self.session_factory() as session:
                already_sent = session.scalars(
                    select(Notification.id).where(
                        Notification.event_id == event.id, Notification.channel == rule.channel.name
                    )
                ).first()
            if already_sent:
                self.duplicates_ignored += 1
                log.info(f"Evento {event.id} ya procesado en {rule.channel.name}; se ignora")
                continue
            recipient, subject = rule.recipient(payload), rule.subject(payload)
            message = rule.message(payload)
            try:
                with self.session_factory() as session, session.begin():
                    session.add(
                        Notification(
                            event_id=event.id,
                            event_type=event.type,
                            channel=rule.channel.name,
                            recipient=recipient,
                            subject=subject,
                            message=message,
                            delivery_id=rule.channel.send(recipient, subject, message),
                            correlation_id=event.correlation_id,
                        )
                    )
            except IntegrityError:  # carrera entre dos réplicas consumidoras
                self.duplicates_ignored += 1
