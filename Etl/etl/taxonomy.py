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


# ====================== COBROS ======================

# ANOMALIA -> (causa legible, familia, controlable 0/1)
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

_CAUSAS_COBROS_NORM: dict[str, tuple[str, str, int]] = {
    norm(k).upper(): v for k, v in CAUSAS_COBROS.items()
}


def causa_cobros_for_norm_key(anomalia_norm: str) -> tuple[str, str, int]:
    """Clasifica una anomalía de COBROS ya normalizada+upper."""
    return _CAUSAS_COBROS_NORM.get(anomalia_norm, CAUSA_DEFECTO_COBROS)


# Resultado → Estado para COBROS (no usa maestro_tarifas).
RESULTADOS_EFECTIVOS: frozenset[str] = frozenset({
    "REALIZO PAGO", "REALIZO ACUERDO DE PAGO", "PRE-ACUERDO",
})
RESULTADOS_PERDIDOS: frozenset[str] = frozenset({
    "CONTACTO NO EFECTIVO", "NO CONTESTA", "BUZON DE MENSAJES",
    "TELEFONO EQUIVOCADO", "TELEFONO ERRADO", "CLIENTE CUELGA LLAMADA",
})


# Homologación de linea_accion (35 variantes → ~12 limpias).
# Las claves están normalizadas con norm() (minúsculas, _ → espacio).
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


def homolog_linea_accion(valor: Any) -> Any:
    """Homologa una línea de acción de COBROS."""
    if valor is None or (isinstance(valor, float) and valor != valor):
        return valor
    return HOMOLOG_LINEA_ACCION.get(norm(valor), str(valor).strip().title())
