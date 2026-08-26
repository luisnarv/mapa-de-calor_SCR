"""Taxonomía de negocio: clasificación de causas y homologación de brigadas.

Mapea la ACCION cruda a una causa legible + familia + si es *controlable*, y
homologa los nombres de brigada. Valores idénticos a los del `Index.py` original.
"""
from __future__ import annotations

from typing import Any

from .text import norm

# ACCION -> (CAUSA legible, FAMILIA, CONTROLABLE 0/1)
CAUSAS: dict[str, tuple[str, str, int]] = {
    "RESISTENCIA DEL CLIENTE":               ("Resistencia / usuario agresivo",    "seguridad", 0),
    "ACCESO IMPEDIDO":                       ("Acceso impedido",                   "acceso",    1),
    "DIFICIL ACCESO":                        ("Dificil acceso",                    "acceso",    1),
    "SUMINISTRO NO ENCONTRADO":              ("Direccion / suministro no hallado", "datos",     1),
    "SERVICIO INEXISTENTE":                  ("Direccion / suministro no hallado", "datos",     1),
    "PREDIO DEMOLIDO":                       ("Direccion / suministro no hallado", "datos",     1),
    "SIN MEDIDOR":                           ("Sin medidor / infraestructura",     "infra",     1),
    "SIN GESTION":                           ("Sin gestion del tecnico",           "gestion",   1),
    "EXITO - SE REQUIERE NORMALIZACION PQR": ("Requiere normalizacion PQR",        "proceso",   1),
    "IMPOSIBILIDAD TECNICA":                 ("Imposibilidad tecnica",             "infra",     1),
    "CLIENTE HA CANCELADO (PAGO RECIENTE)":  ("Cliente pago antes del corte",      "comercial", 0),
    "CLIENTE NO CORTABLE":                   ("Cliente no cortable (normativo)",   "normativo", 0),
    "EN RECLAMO":                            ("En reclamo",                        "normativo", 0),
    "OTRO COMERCIALIZADOR":                  ("Otro comercializador",              "normativo", 0),
}

# Una ACCION nueva cae aquí y se asume CONTROLABLE=1: preferimos exigir de más y
# que alguien lo revise, antes que perdonar un fallo real en silencio.
CAUSA_DEFECTO: tuple[str, str, int] = ("Otras causas", "otros", 1)

# Resultado fijo para las órdenes efectivas.
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

# Índice precalculado ACCION-normalizada -> causa, para clasificar vectorizado.
_CAUSAS_NORM: dict[str, tuple[str, str, int]] = {
    norm(accion).upper(): valor for accion, valor in CAUSAS.items()
}


def classify_action(accion: Any) -> tuple[str, str, int]:
    """Devuelve (CAUSA, FAMILIA, CONTROLABLE) para una ACCION cruda."""
    return _CAUSAS_NORM.get(norm(accion).upper(), CAUSA_DEFECTO)


def causa_for_norm_key(accion_norm: str) -> tuple[str, str, int]:
    """Igual que `classify_action` pero recibe la clave ya normalizada+upper.

    Permite clasificar de forma vectorizada (map sobre una columna ya normalizada)
    sin volver a normalizar por fila.
    """
    return _CAUSAS_NORM.get(accion_norm, CAUSA_DEFECTO)


def homolog_brigada(valor: Any) -> Any:
    """Homologa un nombre de brigada; si no está en el catálogo, lo deja limpio."""
    if valor is None or (isinstance(valor, float) and valor != valor):  # NaN
        return valor
    return HOMOLOG_BRIGADA.get(norm(valor), str(valor).strip())
