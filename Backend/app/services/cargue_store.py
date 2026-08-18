"""Almacén en memoria de los archivos de órdenes que sube el usuario.

El chat es sin estado: cada petición trae los mensajes y nada más. Para poder
conversar sobre un archivo hay que conservarlo entre turnos, y de eso se encarga
esto: se guarda una vez al subirlo y el frontend devuelve el id en cada turno.

En memoria y por proceso, a propósito: un cargue es de una conversación y de un
rato. Si mañana la API corre con varios workers, el id dejará de encontrarse en
el worker que no lo guardó y habrá que mover esto a Redis o a la base.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock

from app.services.carga_ordenes import Cargue

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CargueGuardado:
    """Un cargue con lo que hace falta para identificarlo y caducarlo."""

    id: str
    archivo: str
    cargue: Cargue
    creado: datetime


class CargueStore:
    """Cargues vivos, con caducidad y tope de cuántos caben.

    Las dos podas existen por lo mismo: nada borra un cargue cuando el usuario
    cierra el chat, así que sin ellas la memoria del proceso solo crece.
    """

    def __init__(self, ttl_minutos: int, maximo: int) -> None:
        self._ttl = timedelta(minutes=ttl_minutos)
        self._maximo = maximo
        self._items: dict[str, CargueGuardado] = {}
        self._lock = Lock()

    def guardar(self, cargue: Cargue, archivo: str) -> CargueGuardado:
        """Guarda un cargue y devuelve el registro, ya con su id.

        El id se genera con `secrets` y no con un contador: sin autenticación es
        lo único que impide que alguien lea el cargue de otro probando números.
        """
        guardado = CargueGuardado(
            id=secrets.token_urlsafe(16),
            archivo=archivo,
            cargue=cargue,
            creado=datetime.now(timezone.utc),
        )
        with self._lock:
            self._purgar()
            while len(self._items) >= self._maximo:
                viejo = next(iter(self._items))
                del self._items[viejo]
                logger.info("Cargue %s descartado: se llenó el almacén.", viejo)
            self._items[guardado.id] = guardado
        return guardado

    def obtener(self, id_cargue: str) -> CargueGuardado | None:
        """El cargue, o None si no existe o ya caducó."""
        with self._lock:
            self._purgar()
            return self._items.get(id_cargue)

    def _purgar(self) -> None:
        """Saca los caducados. Se llama con el lock tomado."""
        limite = datetime.now(timezone.utc) - self._ttl
        for id_cargue in [i for i, g in self._items.items() if g.creado < limite]:
            del self._items[id_cargue]
