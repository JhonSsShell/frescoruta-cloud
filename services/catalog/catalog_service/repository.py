"""Patrón Repository: aísla la lógica de negocio del mecanismo de persistencia."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Product, Reservation, utcnow


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, category: str | None = None) -> list[Product]:
        stmt = select(Product).order_by(Product.category, Product.name)
        if category:
            stmt = stmt.where(Product.category == category)
        return list(self.session.scalars(stmt))

    def get(self, product_id: str) -> Product | None:
        return self.session.get(Product, product_id)

    def get_many(self, ids: list[str]) -> dict[str, Product]:
        return {p.id: p for p in self.session.scalars(select(Product).where(Product.id.in_(ids)))}

    def add(self, product: Product) -> Product:
        self.session.add(product)
        return product

    def count(self) -> int:
        return len(self.session.scalars(select(Product.id)).all())

    def try_decrement_stock(self, product_id: str, quantity: int) -> bool:
        """Descuento atómico condicionado: evita sobreventa sin bloqueos explícitos."""
        result = self.session.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock >= quantity)
            .values(stock=Product.stock - quantity, updated_at=utcnow())
        )
        return result.rowcount == 1

    def increment_stock(self, product_id: str, quantity: int) -> None:
        self.session.execute(
            update(Product)
            .where(Product.id == product_id)
            .values(stock=Product.stock + quantity, updated_at=utcnow())
        )


class ReservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, reservation_id: str) -> Reservation | None:
        return self.session.get(Reservation, reservation_id)

    def get_by_order(self, order_id: str) -> Reservation | None:
        return self.session.scalars(
            select(Reservation).where(Reservation.order_id == order_id)
        ).first()

    def add(self, reservation: Reservation) -> Reservation:
        self.session.add(reservation)
        return reservation
