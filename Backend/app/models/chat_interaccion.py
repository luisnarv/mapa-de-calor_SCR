"""Espejo ORM de `dbanalitica.chat_interaccion`.

La tabla se crea con `migraciones/001_chat_interaccion.sql`. Las restricciones
viven allá —es donde no se pueden saltar— y no se repiten aquí; lo único que se
declara es la unicidad de la posición, porque el upsert la nombra.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, SmallInteger, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ChatInteraccion(Base):
    """Una pregunta, su respuesta, cómo se produjo y qué opinó el usuario."""

    __tablename__ = "chat_interaccion"
    __table_args__ = (
        UniqueConstraint(
            "conversacion_id", "n_interaccion", name="chat_interaccion_posicion_unica"
        ),
        {"schema": "dbanalitica"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # El id de la conversación lo pone el frontend: el backend no la guarda entre
    # peticiones, así que no tiene forma de saber cuál es la de quien pregunta.
    conversacion_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    n_interaccion: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    pregunta: Mapped[str] = mapped_column(Text, nullable=False)
    respuesta: Mapped[str] = mapped_column(Text, nullable=False)
    vista: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    traza: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    voto: Mapped[str | None] = mapped_column(Text)
    motivo: Mapped[str | None] = mapped_column(Text)
    comentario: Mapped[str | None] = mapped_column(Text)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # El instante del clic en el pulgar. Distinto de `creado_en`, que es cuándo
    # respondió el asistente: entre los dos pueden pasar días.
    calificado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
