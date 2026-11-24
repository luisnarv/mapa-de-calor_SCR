"""Acceso a la base de datos (PostgreSQL de producción, solo lectura).

Encapsula la conexión y las consultas del ETL.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pandas as pd
import psycopg2

from .logging_conf import get_logger

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

    def fetch_ordenes(self, query: str) -> pd.DataFrame:
        """Trae órdenes como DataFrame usando la query proporcionada."""
        log.info("Consultando la base de datos…")
        with self._cursor() as cur:
            cur.execute(query)
            columns = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=columns)
        log.info("Cargadas %s filas.", f"{len(df):,}")
        return df
