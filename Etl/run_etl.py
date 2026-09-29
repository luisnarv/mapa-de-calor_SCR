#!/usr/bin/env python
"""Punto de entrada del ETL SCR (CLI).

Uso:
    python run_etl.py                 # regenera el JSON desde la BD
    python run_etl.py --csv           # además escribe el CSV consolidado
    python run_etl.py --log-level DEBUG

Requiere la variable de entorno SCR_DATABASE_URL (en Etl/.env o en el entorno
del runner de GitHub Actions).

Los JSON quedan en Etl/salida/ (configurable con ETL_OUTPUT_DIR).
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from etl import load_settings, run
from etl.config import REPO_ROOT
from etl.logging_conf import setup_logging

logger = logging.getLogger(__name__)

# Destinos donde el backend y el frontend leen los JSON.
_DESTINOS_BACKEND = REPO_ROOT / "Backend" / "app" / "data"
_DESTINOS_FRONTEND = REPO_ROOT / "Frontend" / "dashboard" / "public"


def _repartir(proceso: str, salida: Path) -> None:
    """Copia los JSON generados a donde los leen el backend y el frontend."""
    be = _DESTINOS_BACKEND / proceso
    fe = _DESTINOS_FRONTEND / proceso
    be.mkdir(parents=True, exist_ok=True)
    fe.mkdir(parents=True, exist_ok=True)

    for archivo in salida.glob("*.json"):
        shutil.copy2(archivo, be / archivo.name)
        # El frontend no necesita observaciones ni direcciones de NIC.
        if not archivo.name.startswith("observaciones_"):
            shutil.copy2(archivo, fe / archivo.name)

    logger.info("Datos de %s copiados a %s y %s", proceso.upper(), be, fe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETL SCR/COBROS: BD -> JSON del dashboard.")
    parser.add_argument("--proceso", default="todos",
                        choices=["scr", "cobros", "todos"],
                        help="Proceso a regenerar (por defecto: todos).")
    parser.add_argument("--csv", action="store_true",
                        help="También genera el CSV consolidado (por defecto no).")
    parser.add_argument("--csv-path", default=None, help="Ruta del CSV (opcional).")
    parser.add_argument("--log-level", default=None,
                        help="DEBUG | INFO | WARNING | ERROR (por defecto INFO).")
    parser.add_argument("--no-copy", action="store_true",
                        help="No copiar los JSON al backend/frontend (solo generar en salida/).")
    args = parser.parse_args(argv)

    procesos = ["scr", "cobros"] if args.proceso == "todos" else [args.proceso]
    fallos = 0

    for proceso in procesos:
        try:
            settings = load_settings(
                proceso=proceso,
                write_csv=args.csv,
                csv_path=args.csv_path,
                log_level=args.log_level,
            )
        except RuntimeError as exc:
            print(f"ERROR de configuración ({proceso}): {exc}", file=sys.stderr)
            fallos += 1
            continue

        setup_logging(settings.log_level)
        try:
            run(settings)
        except Exception as exc:  # noqa: BLE001
            setup_logging(settings.log_level).error(
                "ETL %s falló: %s", proceso.upper(), exc, exc_info=True,
            )
            fallos += 1
            continue

        if not args.no_copy:
            _repartir(proceso, settings.public_dir)

    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
