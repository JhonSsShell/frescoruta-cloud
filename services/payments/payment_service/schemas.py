from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaymentRequest(BaseModel):
    order_id: str = Field(min_length=3, max_length=40)
    amount: int = Field(gt=0, description="Monto en COP")
    method: str = Field(examples=["CARD", "PSE", "CASH_ON_DELIVERY"])


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    amount: int
    method: str
    status: str
    reason: str | None
    reference: str | None
    created_at: datetime


class ChaosSettings(BaseModel):
    failure_rate: float = Field(ge=0, le=1, description="Probabilidad de fallo técnico (HTTP 503)")
    latency_ms: int = Field(default=0, ge=0, le=10_000, description="Latencia artificial")
