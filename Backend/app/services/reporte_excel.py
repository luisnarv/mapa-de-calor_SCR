"""Genera el reporte operativo corriendo `export_excel.py` en segundo plano.

Cobros tarda más de tres minutos: una petición abierta todo ese tiempo la cortaría
cualquier proxy. Por eso se lanza una vez y el cliente pregunta hasta que esté.
El nombre del archivo lleva la fecha de `data.json`, así el mismo reporte se
reutiliza hasta que el ETL publique cifras nuevas.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock, Thread

from app.core.config import BACKEND_ROOT, settings

logger = logging.getLogger(__name__)

SCRIPT = BACKEND_ROOT.parent / "export_excel.py"
CARPETA = Path(tempfile.gettempdir()) / "reportes_mapa"
TIMEOUT_SEGUNDOS = 900

_candado = Lock()
_en_curso: set[str] = set()
_errores: dict[str, str] = {}


class ReporteFallido(Exception):
    """El mensaje se le muestra al usuario."""


def _ruta_destino(proceso: str) -> Path:
    datos = settings.DATA_DIR / proceso / "data.json"
    if not datos.is_file():
        raise ReporteFallido(f"No hay datos de {proceso}: falta {datos}. Corre el ETL.")
    return CARPETA / f"{proceso}_{datos.stat().st_mtime_ns}.xlsx"


def _generar(proceso: str, destino: Path) -> None:
    # Se escribe aparte y se renombra al final: `is_file` no puede ver un libro a medias.
    parcial = destino.with_suffix(f".{os.getpid()}.parcial.xlsx")
    try:
        if not SCRIPT.is_file():
            raise ReporteFallido(f"El servidor no tiene {SCRIPT.name} junto a Backend/. Despliégalo.")
        CARPETA.mkdir(exist_ok=True)
        subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), proceso,
             "--datos", str(settings.DATA_DIR / proceso), "--salida", str(parcial)],
            check=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_SEGUNDOS,
        )
        os.replace(parcial, destino)
        for viejo in CARPETA.glob(f"{proceso}_*.xlsx"):
            if viejo != destino and ".parcial." not in viejo.name:
                viejo.unlink(missing_ok=True)
    except ReporteFallido as exc:
        _errores[proceso] = str(exc)
    except subprocess.CalledProcessError as exc:
        logger.error("export_excel.py falló para %s: %s", proceso, exc.stderr[-2000:])
        _errores[proceso] = "No se pudo generar el reporte. Revisa el log del backend y reintenta."
    except Exception:
        logger.exception("No se pudo generar el reporte de %s.", proceso)
        _errores[proceso] = "No se pudo generar el reporte. Revisa el log del backend y reintenta."
    finally:
        parcial.unlink(missing_ok=True)
        with _candado:
            _en_curso.discard(proceso)


def solicitar(proceso: str) -> Path | None:
    """Devuelve el libro si ya está; si no, lo pone a generar y devuelve None."""
    destino = _ruta_destino(proceso)
    with _candado:
        if destino.is_file():
            return destino
        error = _errores.pop(proceso, None)
        if error:
            raise ReporteFallido(error)
        if proceso not in _en_curso:
            _en_curso.add(proceso)
            Thread(target=_generar, args=(proceso, destino), daemon=True).start()
    return None
