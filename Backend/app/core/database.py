"""Conexión a la base de datos con SQLAlchemy 2.0 asíncrono."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,  # descarta conexiones muertas tras un reinicio de la BD
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base declarativa de la que heredan todos los modelos ORM."""


async def dispose_engine() -> None:
    """Cierra el pool ordenadamente.

    Sin esto, al apagar el proceso las conexiones de asyncpg quedan vivas y el
    recolector de basura intenta cerrarlas cuando el greenlet de SQLAlchemy ya
    murió: de ahí el `RuntimeError: greenlet is being finalized`.
    """
    await engine.dispose()
