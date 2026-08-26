"""Acceso a la base de datos (PostgreSQL de producción, solo lectura).

Encapsula la conexión y las dos consultas del ETL: las órdenes de
`dbanalitica.historico_mo` y el mapa subacción->estado de
`dbanalitica.maestro_tarifas`.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pandas as pd
import psycopg2

from .config import QUERY_ESTADOS, QUERY_HISTORICO
from .logging_conf import get_logger
from .text import norm_dato

log = get_logger()


class Database:
    """Cliente de solo lectura sobre la BD operativa.

    Se usa como *context manager* para garantizar el cierre de la conexión:

        with Database(url) as db:
            df = db.fetch_ordenes()
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._conn: "psycopg2.extensions.connection | None" = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connect(self) -> None:
        """Abre la conexión en modo solo-lectura. Lanza si falla."""
        log.info("Conectando a la base de datos…")
        try:
            self._conn = psycopg2.connect(self._url)
            self._conn.set_session(readonly=True, autocommit=True)
        except psycopg2.Error as exc:  # pragma: no cover - depende de la red
            raise RuntimeError(f"No se pudo conectar a la base de datos: {exc}") from exc
        log.info("Conexión establecida.")

    def close(self) -> None:
        """Cierra la conexión si está abierta."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _cursor(self) -> Iterator["psycopg2.extensions.cursor"]:
        if self._conn is None:
            raise RuntimeError("La conexión no está abierta (usa connect() o el context manager).")
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def fetch_ordenes(self) -> pd.DataFrame:
        """Trae todas las órdenes de historico_mo como DataFrame."""
        log.info("Consultando dbanalitica.historico_mo…")
        with self._cursor() as cur:
            cur.execute(QUERY_HISTORICO)
            columns = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=columns)
        log.info("Cargadas %s filas desde historico_mo.", f"{len(df):,}")
        return df

    def fetch_estado_map(self) -> dict[str, str]:
        """Devuelve el mapa {subacción normalizada -> estado} desde maestro_tarifas."""
        log.info("Cargando Convertidor_Estados desde maestro_tarifas…")
        conv: dict[str, str] = {}
        with self._cursor() as cur:
            cur.execute(QUERY_ESTADOS)
            for sub, estado in cur.fetchall():
                clave = norm_dato(sub)
                if clave:
                    conv[clave] = str(estado).strip()
        log.info("Convertidor_Estados: %d subacciones mapeadas.", len(conv))
        return conv
