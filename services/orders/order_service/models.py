"""Modelo de dominio de pedidos y bitácora de la saga (Saga Log)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_order_id() -> str:
    return f"PED-{uuid.uuid4().hex[:8].upper()}"


class Base(DeclarativeBase):
    pass


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class StepStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default=new_order_id)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_email: Mapped[str] = mapped_column(String(120))
    delivery_city: Mapped[str] = mapped_column(String(40))
    payment_method: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default=OrderStatus.PENDING, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    steps: Mapped[list[SagaStep]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SagaStep.id",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[str] = mapped_column(String(20))
    product_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    unit_price: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[int] = mapped_column(Integer)
    order: Mapped[Order] = relationship(back_populates="items")

    @property
    def subtotal(self) -> int:
        return self.unit_price * self.quantity


class SagaStep(Base):
    __tablename__ = "saga_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    step: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12))
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    order: Mapped[Order] = relationship(back_populates="steps")
