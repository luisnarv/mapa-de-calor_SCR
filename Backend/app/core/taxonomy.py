"""Taxonomía de negocio y normalización de texto.

ESPEJO de `Etl/etl/taxonomy.py` y `Etl/etl/text.py`. Se copia en vez de
importarse porque el paquete `etl` arrastra pandas, numpy y dotenv, que la API no
necesita. `tests/test_taxonomy.py` compara ambos y falla si se desincronizan.

Si cambias una causa o una homologación en el ETL, cámbiala también aquí.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# --- Normalización (espejo de etl/text.py) -----------------------------------

_SEPARADORES = ("/", "-", "_")


def norm(value: Any) -> str:
    """Minúsculas, sin tildes, separadores a espacio, espacios colapsados."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    for sep in _SEPARADORES:
        text = text.replace(sep, " ")
    return " ".join(text.split())


def norm_dato(value: Any) -> str:
    """Normalización agresiva para cruces: solo deja letras y números."""
    if value is None:
        return ""
    text = str(value)
    if "Ã" in text or "Â" in text:  # repara doble-decodificación latin-1/utf-8
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text)


# --- Taxonomía (espejo de etl/taxonomy.py) -----------------------------------

# ACCION -> (CAUSA legible, FAMILIA, CONTROLABLE 0/1)
CAUSAS: dict[str, tuple[str, str, int]] = {
    "RESISTENCIA DEL CLIENTE": ("Resistencia / usuario agresivo", "seguridad", 0),
    "ACCESO IMPEDIDO": ("Acceso impedido", "acceso", 1),
    "DIFICIL ACCESO": ("Dificil acceso", "acceso", 1),
    "SUMINISTRO NO ENCONTRADO": ("Direccion / suministro no hallado", "datos", 1),
    "SERVICIO INEXISTENTE": ("Direccion / suministro no hallado", "datos", 1),
    "PREDIO DEMOLIDO": ("Direccion / suministro no hallado", "datos", 1),
    "SIN MEDIDOR": ("Sin medidor / infraestructura", "infra", 1),
    "SIN GESTION": ("Sin gestion del tecnico", "gestion", 1),
    "EXITO - SE REQUIERE NORMALIZACION PQR": ("Requiere normalizacion PQR", "proceso", 1),
    "IMPOSIBILIDAD TECNICA": ("Imposibilidad tecnica", "infra", 1),
    "CLIENTE HA CANCELADO (PAGO RECIENTE)": ("Cliente pago antes del corte", "comercial", 0),
    "CLIENTE NO CORTABLE": ("Cliente no cortable (normativo)", "normativo", 0),
    "EN RECLAMO": ("En reclamo", "normativo", 0),
    "OTRO COMERCIALIZADOR": ("Otro comercializador", "normativo", 0),
}

CAUSA_DEFECTO: tuple[str, str, int] = ("Otras causas", "otros", 1)
CAUSA_EFECTIVA: tuple[str, str, int] = ("Efectiva", "exito", 1)

HOMOLOG_BRIGADA: dict[str, str] = {
    "scr pesada disponibilidad": "SCR DISPONIBLE",
    "scr pesada": "Brigada Tipo Pesada",
    "scr liviana": "Brigada Tipo Liviana",
    "scr multifamiliar": "Gestor Integral Multi",
    "scr mini canasta": "Brigada Tipo Minicanasta",
    "scr medida especial": "Pesada MT-AT",
    "canasta": "Brigada Tipo Canasta",
}

# ACCION ya normalizada+upper -> causa. Es la clave con la que cruza el SQL.
CAUSAS_NORM: dict[str, tuple[str, str, int]] = {
    norm(accion).upper(): valor for accion, valor in CAUSAS.items()
}

# Caja geográfica del Atlántico (lat_min, lat_max, lon_min, lon_max).
BBOX: tuple[float, float, float, float] = (10.0, 11.35, -75.45, -74.35)

ESTADOS: tuple[str, ...] = ("Efectiva", "Fallida", "Perdida")
