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


# ====================== COBROS ======================

CAUSAS_COBROS: dict[str, tuple[str, str, int]] = {
    "COMPROMISO":                          ("Compromiso de pago",          "gestion",   1),
    "CLIENTE NO TIENE VOLUNTAD DE PAGO":   ("Sin voluntad de pago",        "gestion",   1),
    "NO ES EL TITULAR DE LA DEUDA":        ("No es el titular",            "datos",     0),
    "NO ES POSIBLE CONTACTAR AL CLIENTE EN EL PREDIO": ("Cliente no contactable", "acceso", 1),
    "PAGO TOTAL":                          ("Pago total",                  "exito",     1),
    "FINANCIACION":                        ("Financiación",                "acuerdo",   1),
    "PREDIO DESOCUPADO":                   ("Predio desocupado",           "acceso",    0),
    "INCONFORMIDAD CONSUMOS":              ("Inconformidad consumos",      "comercial", 0),
    "ABONO":                               ("Abono",                       "exito",     1),
    "RECLAMO EN TRAMITE":                  ("Reclamo en trámite",          "normativo", 0),
    "DIRECCION ERRADA":                    ("Dirección errada",            "datos",     1),
    "PREDIO DEMOLIDO / INEXISTENTE":       ("Predio demolido/inexistente", "datos",     0),
    "SECTOR PELIGROSO":                    ("Sector peligroso",            "seguridad", 0),
    "SITUACION VULNERABLE":                ("Situación vulnerable",        "normativo", 0),
    "NINGUNA":                             ("Sin anomalía",                "otros",     1),
    "DOBLE FACTURADO":                     ("Doble facturado",             "proceso",   0),
    "DEUDA ANTERIOR OPERADOR":             ("Deuda anterior operador",     "normativo", 0),
    "DIFICIL ACCESO A LA LOCALIDAD POR VIA EN MAL ESTAD": ("Vía en mal estado", "acceso", 0),
    "NO RECIBIO FACTURA":                  ("No recibió factura",          "proceso",   0),
    "DIFICIL ACCESO A LA LOCALIDAD POR ORDEN PUBLICO": ("Orden público",  "seguridad", 0),
    "TRANSPORTE Y EQUIPOS":                ("Transporte y equipos",        "logistica", 0),
    "CONDICIONES CLIMATICAS":              ("Condiciones climáticas",      "logistica", 0),
}

CAUSA_DEFECTO_COBROS: tuple[str, str, int] = ("Otras anomalías", "otros", 1)

CAUSAS_COBROS_NORM: dict[str, tuple[str, str, int]] = {
    norm(k).upper(): v for k, v in CAUSAS_COBROS.items()
}

RESULTADOS_EFECTIVOS: frozenset[str] = frozenset({
    "REALIZO PAGO", "REALIZO ACUERDO DE PAGO", "PRE-ACUERDO",
})
RESULTADOS_PERDIDOS: frozenset[str] = frozenset({
    "CONTACTO NO EFECTIVO", "NO CONTESTA", "BUZON DE MENSAJES",
    "TELEFONO EQUIVOCADO", "TELEFONO ERRADO", "CLIENTE CUELGA LLAMADA",
})

HOMOLOG_LINEA_ACCION: dict[str, str] = {
    "cobro persuasivo":                "Cobro Persuasivo",
    "multifamiliar":                   "Multifamiliar",
    "multfamiliar":                    "Multifamiliar",
    "multifamiliar cierre":            "Multifamiliar Cierre",
    "visita personalizada":            "Visita Personalizada",
    "visita personalizada cierre":     "Visita Personalizada Cierre",
    "corredores comerciales":          "Comercial",
    "corredor comercial":              "Comercial",
    "comercial":                       "Comercial",
    "comercial est":                   "Comercial",
    "comerciales":                     "Comercial",
    "seguimiento palc":                "Seguimiento PalC",
    "seguimient palc":                 "Seguimiento PalC",
    "seg palc":                        "Seguimiento PalC",
    "personalizada palc":              "Seguimiento PalC",
    "opotunidad normalizacion":        "Normalización",
    "oportunidad normalizacion":       "Normalización",
    "normalizacion mayores a 1000 k":  "Normalización",
    "saneamiento":                     "Normalización",
    "acu fuera plan":                  "Acuerdo Fuera de Plan",
    "acu fuera de plan":               "Acuerdo Fuera de Plan",
    "acuerdo fuera del plan":          "Acuerdo Fuera de Plan",
    "mtto acuerdo":                    "Mantenimiento Acuerdo",
    "plan piloto opf":                 "Plan OPF",
    "piloto opf":                      "Plan OPF",
    "plan opf":                        "Plan OPF",
    "f4":                              "F4",
    "contingencia f4":                 "F4",
    "atlantico gs":                    "Atlántico GS",
    "top multi":                       "Top Multi",
}
