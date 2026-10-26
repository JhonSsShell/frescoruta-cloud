"""Utilidades de persistencia (Database per Service).

Cada microservicio tiene su propia base de datos y nadie más accede a ella:
por defecto un archivo SQLite local; en producción basta con apuntar
``DATABASE_URL`` a PostgreSQL sin cambiar el código (SQLAlchemy).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> Engine:
    if url.startswith("postgres://"):  # formato antiguo de algunos proveedores
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite"):
        if url in ("sqlite://", "sqlite:///:memory:"):
            return create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        path = url.split("sqlite:///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
