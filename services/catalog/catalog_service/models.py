"""Modelo de dominio y persistencia del catálogo (base de datos propia)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # SKU, p. ej. FR-001
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), index=True)
    unit: Mapped[str] = mapped_column(String(20))
    price: Mapped[int] = mapped_column(Integer)  # pesos colombianos (COP)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=20)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def is_low_stock(self) -> bool:
        return self.stock <= self.low_stock_threshold


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default=ReservationStatus.RESERVED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list[ReservationItem]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan", lazy="selectin"
    )


class ReservationItem(Base):
    __tablename__ = "reservation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("reservations.id"))
    product_id: Mapped[str] = mapped_column(String(20))
    product_name: Mapped[str] = mapped_column(String(120))
    unit_price: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    reservation: Mapped[Reservation] = relationship(back_populates="items")
