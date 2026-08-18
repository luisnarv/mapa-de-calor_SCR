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
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

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


_payload: Payload | None = None
_firma: tuple | None = None
_lock = Lock()


def _firma_de(directorio: Path) -> tuple:
    """Huella de los archivos, para recargar cuando el ETL los regenere."""
    return tuple(
        (p.name, p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(directorio.glob("data*.json"))
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


def _cargar(directorio: Path) -> Payload:
    """Lee `data.json` y los archivos por mes, y los concatena en columnas."""
    entrada = directorio / "data.json"
    if not entrada.is_file():
        raise PayloadNoDisponible(f"Falta {entrada}. ¿Corriste el ETL?")

    with open(entrada, encoding="utf-8") as fh:
        raiz = json.load(fh)

    dim, meta = raiz["dim"], raiz["meta"]
    manifiesto = sorted(meta.get("months", []), key=lambda m: m["key"])
    if not manifiesto:
        raise PayloadNoDisponible("El payload no trae manifiesto de meses.")

    meses = [m["key"] for m in manifiesto]
    columnas = {
        "b": array("h"), "t": array("h"), "g": array("b"),
        "o": array("b"), "c": array("b"), "e": array("b"), "mes": array("b"),
    }

    for i, mes in enumerate(manifiesto):
        # El mes en curso viaja dentro de data.json; el resto en su propio archivo.
        if mes.get("recent"):
            pts = raiz["pts"]
        else:
            archivo = directorio / mes["file"]
            if not archivo.is_file():
                # Saltárselo daría totales incompletos sin que nadie se entere.
                raise PayloadNoDisponible(f"Falta el archivo del mes {mes['key']}: {archivo}")
            with open(archivo, encoding="utf-8") as fh:
                pts = json.load(fh)["pts"]

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
