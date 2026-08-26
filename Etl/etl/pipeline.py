"""Orquestación del ETL: BD -> transformación -> payload JSON.

Es el único lugar que conoce el flujo completo; cada paso vive en su módulo.
"""
from __future__ import annotations

import time
from typing import Any

from .config import Settings
from .database import Database
from .logging_conf import get_logger
from .payload import build_and_write
from .transform import enrich

log = get_logger()


def run(settings: Settings) -> dict[str, Any]:
    """Ejecuta el ETL de punta a punta.

    Args:
        settings: configuración de la corrida (BD, rutas, opciones).

    Returns:
        Resumen de la corrida (total y conteo por mes).

    Raises:
        RuntimeError: ante errores de negocio (sin conexión, Estado vacío, etc.).
    """
    inicio = time.perf_counter()
    log.info("=" * 60)
    log.info("ETL SCR — regeneración del payload del dashboard")
    log.info("=" * 60)

    with Database(settings.database_url) as db:
        df_crudo = db.fetch_ordenes()
        estado_map = db.fetch_estado_map()

    df = enrich(df_crudo, estado_map)

    if settings.write_csv and settings.csv_path is not None:
        settings.csv_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(settings.csv_path, index=False, encoding="utf-8-sig")
        log.info("CSV consolidado -> %s (%s filas)", settings.csv_path.name, f"{len(df):,}")

    resumen = build_and_write(df, settings)

    log.info("=" * 60)
    log.info("OK  total_all=%s  |  %.1fs", f"{resumen['total_all']:,}", time.perf_counter() - inicio)
    log.info("=" * 60)
    return resumen
