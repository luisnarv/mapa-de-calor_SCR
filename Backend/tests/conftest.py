"""Configuración común de las pruebas."""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

# El ETL vive fuera del backend. Se agrega al path para poder comparar contra él
# la taxonomía copiada en `app/core/taxonomy.py`. Si no está (o le falta pandas),
# esas pruebas se saltan solas.
ETL = REPO_ROOT / "Etl"
if ETL.is_dir() and str(ETL) not in sys.path:
    sys.path.append(str(ETL))
