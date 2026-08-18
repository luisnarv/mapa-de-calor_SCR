"""ETL SCR: regenera el payload JSON del dashboard desde la base de datos.

Punto de entrada programático:

    from etl import load_settings, run
    run(load_settings())
"""
from __future__ import annotations

from .config import Settings, load_settings
from .pipeline import run

__all__ = ["Settings", "load_settings", "run"]
