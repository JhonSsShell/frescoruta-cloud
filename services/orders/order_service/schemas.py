from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

City = Literal["Bogotá", "Medellín", "Cali"]
PaymentMethod = Literal["CARD", "PSE", "CASH_ON_DELIVERY"]


class OrderLineIn(BaseModel):
    product_id: str = Field(examples=["FR-001"])
    quantity: int = Field(gt=0, le=10_000)


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=120, examples=["Restaurante La Huerta"])
    customer_email: str = Field(
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", examples=["compras@lahuerta.co"]
    )
    delivery_city: City = "Bogotá"
    payment_method: PaymentMethod = "CARD"
    items: list[OrderLineIn] = Field(min_length=1, max_length=50)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: str
    product_name: str | None
    unit_price: int
    quantity: int
    subtotal: int


class SagaStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: str
    status: str
    detail: str | None
    at: datetime


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_name: str
    customer_email: str
    delivery_city: str
    payment_method: str
    status: str
    total: int
    rejection_reason: str | None
    reservation_id: str | None
    payment_id: str | None
    created_at: datetime
    items: list[OrderItemOut]
    steps: list[SagaStepOut]
