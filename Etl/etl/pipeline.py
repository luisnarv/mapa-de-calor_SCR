"""Orquestación del ETL: BD -> transformación -> payload JSON.

Es el único lugar que conoce el flujo completo; cada paso vive en su módulo.
"""
from __future__ import annotations

import time
from typing import Any

from .config import QUERY_COBROS, QUERY_GESTORES, QUERY_HISTORICO, Settings
from .database import Database
from .logging_conf import get_logger
from .payload import build_and_write, escribir_gestores
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
    log.info("ETL %s — regeneración del payload del dashboard", settings.proceso.upper())
    log.info("=" * 60)

    query = QUERY_HISTORICO if settings.proceso == "scr" else QUERY_COBROS

    with Database(settings.database_url) as db:
        df_crudo = db.fetch_ordenes(query)
        estado_map = db.fetch_estado_map() if settings.proceso == "scr" else None
        gestores = db.fetch_ordenes(QUERY_GESTORES) if settings.proceso == "cobros" else None

    df = enrich(df_crudo, estado_map, proceso=settings.proceso)

    if settings.write_csv and settings.csv_path is not None:
        settings.csv_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(settings.csv_path, index=False, encoding="utf-8-sig")
        log.info("CSV consolidado -> %s (%s filas)", settings.csv_path.name, f"{len(df):,}")

    resumen = build_and_write(df, settings)
    if gestores is not None:
        escribir_gestores(gestores, settings)

    log.info("=" * 60)
    log.info("OK  total_all=%s  |  %.1fs", f"{resumen['total_all']:,}", time.perf_counter() - inicio)
    log.info("=" * 60)
    return resumen
