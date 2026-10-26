"""Canales de notificación (simulados) detrás de una interfaz común.

En producción cada adaptador encapsularía un proveedor (Amazon SES, Twilio,
WhatsApp Business API); el resto del servicio no cambiaría.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod

log = logging.getLogger("notification-service.channels")


class NotificationChannel(ABC):
    name: str

    @abstractmethod
    def send(self, recipient: str, subject: str, message: str) -> str:
        """Envía la notificación y devuelve un identificador de entrega."""


class _SimulatedChannel(NotificationChannel):
    prefix = "SIM"

    def send(self, recipient: str, subject: str, message: str) -> str:
        delivery_id = f"{self.prefix}-{uuid.uuid4().hex[:8]}"
        log.info(f"[{self.name}] -> {recipient}: {subject}")
        return delivery_id


class EmailChannel(_SimulatedChannel):
    name = "EMAIL"
    prefix = "MAIL"


class SmsChannel(_SimulatedChannel):
    name = "SMS"
    prefix = "SMS"


class WhatsAppChannel(_SimulatedChannel):
    name = "WHATSAPP"
    prefix = "WA"
