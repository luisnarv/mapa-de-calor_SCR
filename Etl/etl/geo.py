"""Capa geográfica: límites de barrio/municipio/zona y su enlace con las órdenes.

Las órdenes se enlazan a los polígonos catastrales por **geometría** (punto en
polígono), no por nombre: el nombre solo cruzaba el 46% de las órdenes; la
geometría cruza el 99,8%. Lógica idéntica al `Index.py` original.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .logging_conf import get_logger

log = get_logger()


def _cargar_geojson(path: Path) -> dict[str, Any] | None:
    """Lee un GeoJSON; devuelve None (con aviso) si no existe."""
    if not path.exists():
        log.warning("No encuentro %s. Se omite esa capa.", path.name)
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _anillos(geom: dict[str, Any]) -> list:
    """GeoJSON usa [lon,lat]; Leaflet quiere [lat,lon]. Devuelve polígonos->anillos."""
    tipo, coords = geom["type"], geom["coordinates"]
    polis = [coords] if tipo == "Polygon" else coords
    return [
        [[[round(p[1], 5), round(p[0], 5)] for p in anillo] for anillo in poli]
        for poli in polis
    ]


def link_barrios(d: pd.DataFrame, path: Path) -> list[dict[str, Any]]:
    """Enlaza cada polígono de barrio con el barrio del dato por mayoría geométrica.

    Args:
        d: DataFrame indexado; requiere columnas ``b`` (índice de barrio),
            ``LATITUD`` y ``LONGITUD``.
        path: ruta al GeoJSON de barrios.

    Returns:
        Lista de polígonos ``{n, m, b, cf, r}`` (nombre, municipio, índice de
        barrio enlazado o -1, confianza, anillos). Lista vacía si no hay geojson
        o falta shapely.
    """
    gj = _cargar_geojson(path)
    if gj is None:
        return []
    try:
        import shapely as _sh
        from shapely.geometry import shape
        from shapely.strtree import STRtree
    except ImportError:
        log.warning("Falta 'shapely' (pip install shapely). Sin límites de barrio.")
        return []

    formas = [shape(f["geometry"]) for f in gj["features"]]
    arbol = STRtree(formas)
    puntos = _sh.points(d["LONGITUD"].values, d["LATITUD"].values)
    pares = arbol.query(puntos, predicate="within")
    asignacion = np.full(len(d), -1)
    asignacion[pares[0]] = pares[1]
    d = d.reset_index(drop=True)
    d["_poly"] = asignacion
    fuera = int((asignacion < 0).sum())

    # Voto de mayoría: barrio del dato -> polígono con más de sus órdenes.
    enlace: dict[int, list[tuple[int, int, int]]] = {}
    for bidx, grp in d[d["_poly"] >= 0].groupby("b"):
        vc = grp["_poly"].value_counts()
        enlace.setdefault(int(vc.index[0]), []).append((int(bidx), int(vc.iloc[0]), len(grp)))

    # Un polígono puede recibir varios barrios del dato: se queda con el mayor.
    pol2b: dict[int, tuple[int, float]] = {}
    for poly, candidatos in enlace.items():
        bidx, n, total = max(candidatos, key=lambda x: x[1])
        pol2b[poly] = (bidx, round(n / total, 2))

    bpoly: list[dict[str, Any]] = []
    bajos = 0
    for i, feature in enumerate(gj["features"]):
        props = feature["properties"]
        bidx, cf = pol2b.get(i, (-1, 0))
        if cf and cf < 0.6:
            bajos += 1
        bpoly.append({
            "n": props.get("nombre", ""),
            "m": props.get("municipio", ""),
            "b": bidx,
            "cf": cf,
            "r": _anillos(feature["geometry"]),
        })

    enlazados = sum(1 for p in bpoly if p["b"] >= 0)
    dentro = len(d) - fuera
    log.info("Límites de barrio: %d polígonos, %d enlazados. %s/%s órdenes dentro (%.1f%%).",
             len(bpoly), enlazados, f"{dentro:,}", f"{len(d):,}",
             dentro / len(d) * 100 if len(d) else 0)
    if bajos:
        log.warning("%d polígonos con enlace de baja confianza (<60%%).", bajos)
    return bpoly


def load_municipios(path: Path) -> list[dict[str, Any]]:
    """Carga los polígonos de municipio: ``{n, r}``."""
    gj = _cargar_geojson(path)
    if gj is None:
        return []
    mpoly = [{"n": f["properties"].get("nombre", ""), "r": _anillos(f["geometry"])}
             for f in gj["features"]]
    log.info("Límites de municipio: %d polígonos.", len(mpoly))
    return mpoly


def load_zonas(path: Path) -> list[dict[str, Any]]:
    """Carga los polígonos de zona: ``{n, c, r}`` (nombre, color, anillos)."""
    gj = _cargar_geojson(path)
    if gj is None:
        return []
    zpoly = [{"n": f["properties"].get("zona", ""),
              "c": f["properties"].get("color", ""),
              "r": _anillos(f["geometry"])}
             for f in gj["features"]]
    log.info("Límites de zona: %d polígonos.", len(zpoly))
    return zpoly
