"""Normalización de direcciones y claves de cruce contra el índice del ETL.

El archivo de órdenes por ejecutar llega sin coordenadas. Hasta ahora se
resolvía con un geocodificador externo, y sobre el archivo real solo acertaba
una de cada tres: OSM no tiene cartografiadas vías como «Carrera 16ASUR», y las
alternativas gratuitas beben de los mismos datos.

La respuesta estaba en casa. Cada orden ya ejecutada trae el GPS que el técnico
capturó en la puerta del predio, y al lado la dirección de esa puerta. Son
~267.000 pares dirección/coordenada de las mismas calles donde trabaja la
operación. Medido contra un cargue real, cruza el 97,7% frente al 35% del
geocodificador: 64% en la dirección exacta (error 0 m), 27% en la misma cuadra
(~32 m) y 7% en la misma vía (~41 m).

Se emiten tres niveles porque una dirección nueva casi nunca está en el
histórico, pero su calle sí. Bajar de «esta puerta» a «esta cuadra» pierde
metros; caer al centro del barrio perdía 357.

ESPEJO de `Etl/etl/direcciones.py`, igual que la taxonomía. El ETL construye
el índice con estas mismas reglas, así que si las dos copias divergen las claves
dejan de cruzar y el mapa pierde precisión sin que falle nada. La prueba
`test_direcciones.py` las compara y falla si se separan.

Se copia en vez de importarse porque el paquete `etl` arrastra pandas y numpy,
que la API no necesita.
"""

from __future__ import annotations

import re

from app.core.taxonomy import norm, norm_dato

# --- Normalización de direcciones (ESPEJO del backend) -----------------------

ABREVIATURAS: dict[str, str] = {
    "cr": "Carrera", "cra": "Carrera", "kr": "Carrera", "kra": "Carrera",
    "k": "Carrera", "carrera": "Carrera",
    "cl": "Calle", "cll": "Calle", "ca": "Calle", "calle": "Calle",
    "dg": "Diagonal", "diag": "Diagonal", "diagonal": "Diagonal",
    "tv": "Transversal", "trv": "Transversal", "tr": "Transversal",
    "transversal": "Transversal",
    "av": "Avenida", "avda": "Avenida", "avenida": "Avenida",
    "cq": "Circunvalar", "via": "Vía",
}

# El cardinal puede venir pegado ('16ASUR') o suelto ('21BIS SUR'); suelto
# rompía la detección del cruce porque 'SUR' se leía como la vía transversal.
_CARDINAL = r"(?:\s+(?:SUR|NORTE|ESTE|OESTE))?"
_NUMERO_VIA = r"\d+[A-Za-z0-9]*" + _CARDINAL

# El sistema escribe el cruce separado: 'CR 8C CL 41 - 111' es la carrera 8C con
# calle 41, placa 111 — o sea 'Carrera 8C # 41-111' de toda la vida.
_CRUCE = re.compile(
    r"^\s*(?P<v1>[A-Za-z]+)\s*(?P<n1>" + _NUMERO_VIA + r")\s+"
    r"(?P<v2>[A-Za-z]+)\s*(?P<n2>" + _NUMERO_VIA + r")"
    r"(?:\s*-\s*(?P<placa>\d+[A-Za-z0-9]*))?",
    re.IGNORECASE,
)

# Dónde empieza lo que no ubica nada: el interior del predio y los códigos
# internos del sistema ('DPL BI4655').
_COMPLEMENTO = frozenset({
    "apto", "apt", "apartamento", "ap", "int", "interior", "piso", "p",
    "torre", "torr", "tor", "t", "bloque", "blo", "bl", "casa", "local",
    "loc", "of", "oficina", "mz", "manzana", "et", "etapa", "lote", "lt",
    "barrio", "br", "dpl", "dp", "entr", "entrada", "cs",
})

# El complemento a veces viene pegado al número ('APTO1', 'TORR20'). Se le
# quitan los dígitos finales antes de comparar, pero solo si empieza por letras:
# así '40-15' no se toca.
_PEGADO = re.compile(r"^([A-Za-z]+)\d+$")


def _es_complemento(palabra: str) -> bool:
    if (pegado := _PEGADO.match(palabra)) is not None:
        palabra = pegado.group(1)
    return norm(palabra) in _COMPLEMENTO


def normalizar_direccion(direccion: str) -> str:
    """Deja la dirección en su forma canónica colombiana.

    'CR 8C CL 41 - 111 DPL BI4655' -> 'Carrera 8C 41-111'
    'CL 40 # 8-15 APTO 3'          -> 'Calle 40 8-15'
    """
    texto = direccion.replace("#", " ")
    texto = re.sub(r"(?:[°º]|\bN(?:o|ro)?[°º.]*)\s*(?=\d)", " ", texto, flags=re.IGNORECASE)
    texto = " ".join(texto.split())

    if (cruce := _CRUCE.match(texto)) and (via := ABREVIATURAS.get(norm(cruce["v1"]))):
        if ABREVIATURAS.get(norm(cruce["v2"])):
            placa = f"{cruce['n2']}-{cruce['placa']}" if cruce["placa"] else cruce["n2"]
            return f"{via} {cruce['n1']} {placa}"

    partes = texto.split()
    if partes and (canon := ABREVIATURAS.get(norm(partes[0]))):
        partes[0] = canon

    corte = len(partes)
    for i, palabra in enumerate(partes):
        if _es_complemento(palabra):
            corte = i
            break

    limpia = " ".join(partes[:corte]).strip(" ,-")
    return re.sub(r"\s*-\s*", "-", limpia)


# --- Claves de cruce (ESPEJO del backend) ------------------------------------


def claves(direccion: str, barrio: str | None) -> tuple[str, str, str] | None:
    """(exacta, cuadra, vía) para una dirección, o None si no hay nada que cruzar.

    Van con el barrio delante porque 'Carrera 8' existe en casi todos: sin él,
    una orden de Soledad heredaría el GPS de una de Malambo.
    """
    canonica = normalizar_direccion(str(direccion))
    if not canonica:
        return None

    prefijo = norm_dato(barrio) if barrio else ""
    partes = canonica.split()
    via = " ".join(partes[:2]) if len(partes) >= 2 else canonica
    tramo = " ".join(partes[:3]) if len(partes) >= 3 else via
    cuadra = re.sub(r"-\d+.*$", "", tramo)  # 'Carrera 8C 41-111' -> 'Carrera 8C 41'
    return (
        f"{prefijo}|{norm_dato(canonica)}",
        f"{prefijo}|{norm_dato(cuadra)}",
        f"{prefijo}|{norm_dato(via)}",
    )
