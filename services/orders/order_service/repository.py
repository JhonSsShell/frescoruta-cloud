"""Repository de pedidos."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Order, OrderStatus, SagaStep, utcnow


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, order: Order) -> Order:
        self.session.add(order)
        return order

    def get(self, order_id: str) -> Order | None:
        return self.session.get(Order, order_id)

    def get_by_idempotency_key(self, key: str) -> Order | None:
        return self.session.scalars(select(Order).where(Order.idempotency_key == key)).first()

    def list(self, status: str | None = None, limit: int = 50) -> list[Order]:
        stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Order.status == status)
        return list(self.session.scalars(stmt))

    def add_step(self, order: Order, step: str, status: str, detail: str | None = None) -> None:
        order.steps.append(SagaStep(step=step, status=status, detail=(detail or "")[:300] or None))
        order.updated_at = utcnow()

    def stats(self) -> dict:
        rows = self.session.execute(
            select(Order.status, func.count(), func.coalesce(func.sum(Order.total), 0)).group_by(
                Order.status
            )
        ).all()
        by_status = {s.value: 0 for s in OrderStatus}
        revenue = 0
        for status, count, total in rows:
            by_status[status] = count
            if status == OrderStatus.CONFIRMED:
                revenue = int(total)
        return {"by_status": by_status, "total": sum(by_status.values()), "confirmed_revenue": revenue}
