"""Carga en memoria el payload que produce el ETL y consume el mapa.

Es la misma fuente que lee el tablero (`dashboard/public/*.json`), así que las
cifras del chat no pueden discrepar de las del mapa: no hay dos cálculos, hay
uno solo leído dos veces.

El payload viene en columnas de enteros: cada orden es una posición `i` en todos
los arrays, y los valores son índices dentro de las listas de `dim`. Se conserva
ese formato —en vez de expandirlo a objetos— porque 177k órdenes en arrays de
enteros ocupan ~2 MB y se recorren en decenas de milisegundos.
"""

from __future__ import annotations

import json
import logging
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Iterator, Sequence

from app.core.taxonomy import norm, norm_dato

logger = logging.getLogger(__name__)


class PayloadNoDisponible(Exception):
    """No se pudo cargar el payload del ETL."""


@dataclass(frozen=True, slots=True)
class Payload:
    """Las órdenes del tablero, en columnas paralelas."""

    # Catálogos (índice -> nombre)
    barrios: list[str]  # BKEY: "MUNICIPIO | BARRIO"
    munis: list[str]
    zonas: list[str]
    brigs: list[str]
    tecs: list[str]
    tipos: list[str]
    causas: list[str]
    causa_ctrl: list[int]  # 1 = controlable por la operación
    causa_fam: list[str]
    b_muni: list[int]  # barrio -> municipio
    b_zona: list[int]  # barrio -> zona
    meses: list[str]  # "YYYY-MM", del más antiguo al más reciente
    # Posición global donde arranca cada mes. Las observaciones viven en un
    # archivo por mes indexado desde 0, así que hace falta esto para traducir
    # una posición dentro de un mes al índice global de las columnas de abajo.
    inicio_mes: list[int]

    # Una posición por orden
    b: array  # barrio
    t: array  # técnico
    g: array  # brigada
    o: array  # tipo de OS
    c: array  # causa
    e: array  # estado: 0 Efectiva, 1 Fallida, 2 Perdida
    mes: array  # índice en `meses`

    generado: str

    def __len__(self) -> int:
        return len(self.e)


@dataclass(frozen=True, slots=True)
class Ubicaciones:
    """Dónde está cada cosa según el histórico, para ubicar órdenes nuevas.

    Vive aparte de `Payload` porque solo hace falta al geolocalizar un cargue:
    los NIC son ~150k cadenas y cargarlos con las métricas costaría esa memoria
    en todos los procesos, incluidos los que nunca reciben un archivo.
    """

    # NIC -> último GPS conocido. El último gana: si un suministro se remidió,
    # la visita más reciente es la que refleja dónde está hoy.
    nic: dict[str, tuple[float, float]]
    # BKEY normalizado -> centroide del barrio, el último recurso al ubicar.
    barrio: dict[str, tuple[float, float]]
    # Índice que genera el ETL desde el GPS del propio histórico, en tres
    # niveles de precisión: la puerta, la cuadra y la vía. Vacíos si todavía no
    # se ha corrido el ETL con `direcciones.json`.
    exacta: dict[str, tuple[float, float]] = field(default_factory=dict)
    cuadra: dict[str, tuple[float, float]] = field(default_factory=dict)
    via: dict[str, tuple[float, float]] = field(default_factory=dict)


_payload: Payload | None = None
_firma: tuple | None = None
_ubicaciones: Ubicaciones | None = None
_firma_ubi: tuple | None = None
_lock = Lock()


def _firma_de(directorio: Path) -> tuple:
    """Huella de los archivos, para recargar cuando el ETL los regenere."""
    return tuple(
        (p.name, p.stat().st_mtime_ns, p.stat().st_size)
        # El índice de direcciones entra en la huella: si el ETL lo regenera y
        # no se mira, el backend seguiría ubicando con el índice viejo.
        # Las observaciones entran por lo mismo: se regeneran en la misma
        # corrida y su caché tiene que caducar con el resto.
        for p in sorted(
            list(directorio.glob("data*.json"))
            + list(directorio.glob("direcciones.json"))
            + list(directorio.glob("observaciones_*.json"))
        )
    )


def obtener(directorio: Path) -> Payload:
    """Devuelve el payload cacheado, recargándolo si los archivos cambiaron."""
    global _payload, _firma

    try:
        firma = _firma_de(directorio)
    except OSError as exc:
        raise PayloadNoDisponible(f"No se pudo leer {directorio}: {exc}") from exc

    with _lock:
        if _payload is None or firma != _firma:
            _payload = _cargar(directorio)
            _firma = firma
        return _payload


def _leer_raiz(directorio: Path) -> dict:
    entrada = directorio / "data.json"
    if not entrada.is_file():
        raise PayloadNoDisponible(f"Falta {entrada}. ¿Corriste el ETL?")
    with open(entrada, encoding="utf-8") as fh:
        return json.load(fh)


def _iter_meses(directorio: Path, raiz: dict) -> Iterator[tuple[dict, dict]]:
    """Recorre los meses del manifiesto, del más antiguo al más reciente.

    Devuelve (mes, pts). Centraliza dónde vive cada mes —el actual dentro de
    `data.json` y el resto en su archivo— para que quien lea el payload y quien
    lea las ubicaciones no puedan discrepar en eso.
    """
    manifiesto = sorted(raiz["meta"].get("months", []), key=lambda m: m["key"])
    if not manifiesto:
        raise PayloadNoDisponible("El payload no trae manifiesto de meses.")

    for mes in manifiesto:
        if mes.get("recent"):
            yield mes, raiz["pts"]
            continue
        archivo = directorio / mes["file"]
        if not archivo.is_file():
            # Saltárselo daría totales incompletos sin que nadie se entere.
            raise PayloadNoDisponible(f"Falta el archivo del mes {mes['key']}: {archivo}")
        with open(archivo, encoding="utf-8") as fh:
            yield mes, json.load(fh)["pts"]


def obtener_ubicaciones(directorio: Path) -> Ubicaciones:
    """Índice de ubicaciones conocidas, cacheado igual que el payload."""
    global _ubicaciones, _firma_ubi

    try:
        firma = _firma_de(directorio)
    except OSError as exc:
        raise PayloadNoDisponible(f"No se pudo leer {directorio}: {exc}") from exc

    with _lock:
        if _ubicaciones is None or firma != _firma_ubi:
            _ubicaciones = _cargar_ubicaciones(directorio)
            _firma_ubi = firma
        return _ubicaciones


def _cargar_ubicaciones(directorio: Path) -> Ubicaciones:
    """Recorre los meses quedándose solo con NIC y coordenada de cada orden."""
    raiz = _leer_raiz(directorio)
    meta = raiz["meta"]
    # El ETL guarda las coordenadas como enteros relativos a este origen para
    # que el JSON pese menos; aquí se deshace esa codificación.
    lat0, lon0 = meta["lat0"], meta["lon0"]

    nic: dict[str, tuple[float, float]] = {}
    for _mes, pts in _iter_meses(directorio, raiz):
        for n, la, lo in zip(pts["nic"], pts["la"], pts["lo"]):
            if n:
                nic[n] = (la / 1e5 + lat0, lo / 1e5 + lon0)

    # El índice de direcciones es opcional a propósito: hasta que no corra el
    # ETL nuevo no existe, y el cargue debe seguir funcionando con NIC y barrio.
    niveles: dict[str, dict[str, tuple[float, float]]] = {"exacta": {}, "cuadra": {}, "via": {}}
    ruta = directorio / "direcciones.json"
    if ruta.is_file():
        try:
            with open(ruta, encoding="utf-8") as fh:
                crudo = json.load(fh)
            for nivel in niveles:
                niveles[nivel] = {k: (v[0], v[1]) for k, v in crudo.get(nivel, {}).items()}
            logger.info(
                "Índice de direcciones: %s exactas, %s cuadras, %s vías.",
                *(f"{len(niveles[n]):,}" for n in ("exacta", "cuadra", "via")),
            )
        except (OSError, ValueError, TypeError, IndexError) as exc:
            # Un índice ilegible no puede tumbar el cargue: se ubica sin él.
            logger.warning("No se pudo leer %s (%s); se ubicará sin él.", ruta.name, exc)
    else:
        logger.info("No hay %s todavía: el cargue se ubicará solo por NIC y barrio.", ruta.name)

    barrios: list[str] = raiz["dim"]["barrios"]
    centros: list[list[float]] = raiz["geo"]["bc"]
    barrio = {
        norm_dato(bkey): (centro[0], centro[1])
        for bkey, centro in zip(barrios, centros)
        if centro
    }

    logger.info(
        "Ubicaciones cargadas: %s NIC con GPS, %s centroides de barrio.",
        f"{len(nic):,}", len(barrio),
    )
    return Ubicaciones(nic=nic, barrio=barrio, **niveles)


def _cargar(directorio: Path) -> Payload:
    """Lee `data.json` y los archivos por mes, y los concatena en columnas."""
    raiz = _leer_raiz(directorio)
    dim, meta = raiz["dim"], raiz["meta"]

    meses: list[str] = []
    columnas = {
        "b": array("h"), "t": array("h"), "g": array("b"),
        "o": array("b"), "c": array("b"), "e": array("b"), "mes": array("b"),
    }

    inicio_mes: list[int] = []
    for i, (mes, pts) in enumerate(_iter_meses(directorio, raiz)):
        meses.append(mes["key"])
        inicio_mes.append(len(columnas["e"]))
        n = len(pts["e"])
        if n != mes["n"]:
            logger.warning(
                "El mes %s declara %s órdenes y trae %s.", mes["key"], mes["n"], n
            )
        for clave in ("b", "t", "g", "o", "c", "e"):
            columnas[clave].extend(pts[clave])
        columnas["mes"].extend([i] * n)

    payload = Payload(
        barrios=dim["barrios"],
        munis=dim["munis"],
        zonas=dim["zonas"],
        brigs=dim["brigs"],
        tecs=dim["tecs"],
        tipos=dim["tipos"],
        causas=dim["causas"],
        causa_ctrl=dim["causa_ctrl"],
        causa_fam=dim["causa_fam"],
        b_muni=dim["b_muni"],
        b_zona=dim["b_zona"],
        meses=meses,
        inicio_mes=inicio_mes,
        generado=meta.get("generated", ""),
        **columnas,
    )

    esperado = meta.get("total_all")
    if esperado is not None and len(payload) != esperado:
        logger.warning(
            "El payload declara %s órdenes y se cargaron %s.", esperado, len(payload)
        )
    logger.info(
        "Payload cargado: %s órdenes, %s meses, generado el %s.",
        f"{len(payload):,}", len(meses), payload.generado,
    )
    return payload


# --- Observaciones -------------------------------------------------------------

# Se cachean por mes y no de una: son ~60 MB de texto en el histórico completo y
# la mayoría de conversaciones no busca en ellas nunca. El payload en enteros
# sigue cargándose entero al arrancar; esto entra solo si alguien lo pide.
#
# Lo que se guarda es el texto YA NORMALIZADO, no el original. Normalizar el
# histórico completo tarda ~2 s, y hacerlo en cada búsqueda era inaceptable
# dentro de una conversación; guardar las dos versiones costaba el doble de
# memoria. Los extractos que se le muestran al usuario salen de `leer_actas`,
# que relee el archivo para las pocas posiciones que hagan falta.
_observaciones: dict[str, list[str]] = {}
_firma_obs: tuple | None = None


def obtener_observaciones(directorio: Path, mes: str) -> list[str]:
    """Actas de un mes, normalizadas y alineadas por posición con las columnas.

    La posición `i` de la lista es la orden `inicio_mes[m] + i` del payload.
    Devuelve `[]` si ese mes todavía no tiene archivo: hasta que no corra el ETL
    nuevo no existe ninguno, y buscar sin resultados es mejor que caerse.
    """
    global _firma_obs

    try:
        firma = _firma_de(directorio)
    except OSError as exc:
        raise PayloadNoDisponible(f"No se pudo leer {directorio}: {exc}") from exc

    with _lock:
        if firma != _firma_obs:
            _observaciones.clear()
            _firma_obs = firma
        if mes not in _observaciones:
            crudas = _leer_observaciones(directorio, mes)
            _observaciones[mes] = [norm(t) for t in crudas]
        return _observaciones[mes]


def leer_actas(directorio: Path, mes: str, posiciones: Sequence[int]) -> dict[int, str]:
    """Texto original de unas pocas actas, para mostrarlas como ejemplo.

    No se cachea a propósito: releer el archivo cuesta unos milisegundos y
    guardar el original de todo un mes duplicaría la memoria de `_observaciones`
    para enseñar tres líneas.
    """
    if not posiciones:
        return {}
    crudas = _leer_observaciones(directorio, mes)
    return {i: crudas[i] for i in posiciones if 0 <= i < len(crudas)}


def _leer_observaciones(directorio: Path, mes: str) -> list[str]:
    ruta = directorio / f"observaciones_{mes}.json"
    if not ruta.is_file():
        logger.info("No hay %s: ese mes no se puede buscar por texto.", ruta.name)
        return []
    try:
        with open(ruta, encoding="utf-8") as fh:
            textos = json.load(fh)["obs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Un archivo ilegible no puede tumbar el chat: se busca sin ese mes.
        logger.warning("No se pudo leer %s (%s); se buscará sin él.", ruta.name, exc)
        return []
    return textos
