"""Tablas ORM. Se importan aquí para que `Base.metadata` las conozca."""

from app.models.chat_interaccion import ChatInteraccion

__all__ = ["ChatInteraccion"]
