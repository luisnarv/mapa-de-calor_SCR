#!/usr/bin/env python
"""Punto de entrada del ETL SCR (CLI).

Uso:
    python run_etl.py                 # regenera el JSON desde la BD
    python run_etl.py --csv           # además escribe el CSV consolidado
    python run_etl.py --log-level DEBUG

Requiere la variable de entorno SCR_DATABASE_URL (en dashboard/.env o en el
entorno del runner de GitHub Actions).
"""
from __future__ import annotations

import argparse
import sys

from etl import load_settings, run
from etl.logging_conf import setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETL SCR: BD -> JSON del dashboard.")
    parser.add_argument("--csv", action="store_true",
                        help="También genera el CSV consolidado (por defecto no).")
    parser.add_argument("--csv-path", default=None, help="Ruta del CSV (opcional).")
    parser.add_argument("--log-level", default=None,
                        help="DEBUG | INFO | WARNING | ERROR (por defecto INFO).")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(
            write_csv=args.csv, csv_path=args.csv_path, log_level=args.log_level
        )
    except RuntimeError as exc:
        print(f"ERROR de configuración: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_level)
    try:
        run(settings)
    except Exception as exc:  # noqa: BLE001 - queremos salir con código != 0 en CI
        setup_logging(settings.log_level).error("ETL falló: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
