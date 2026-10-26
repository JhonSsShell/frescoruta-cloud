"""Contratos de la API (DTO) del catálogo."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: str
    unit: str
    price: int
    stock: int
    low_stock_threshold: int
    is_low_stock: bool


class ProductCreate(BaseModel):
    id: str = Field(pattern=r"^FR-\d{3,}$", examples=["FR-100"])
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=40)
    unit: str = Field(min_length=1, max_length=20)
    price: int = Field(gt=0)
    stock: int = Field(ge=0)
    low_stock_threshold: int = Field(default=20, ge=0)


class StockAdjustment(BaseModel):
    delta: int = Field(description="Unidades a sumar (positivo) o restar (negativo)")


class ReservationLine(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=10_000)


class ReservationRequest(BaseModel):
    order_id: str = Field(min_length=3, max_length=40)
    items: list[ReservationLine] = Field(min_length=1)


class ReservationItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: str
    product_name: str
    unit_price: int
    quantity: int


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    status: str
    items: list[ReservationItemOut]
    total: int
