"""Recaudo y saldo por barrio, municipio, gestor, zona y plan (solo COBROS).

Dos archivos del ETL, los dos desde `v_aire_cobros_nic_mes` y con el mismo universo de
cuentas, así que los totales coinciden:

* `recaudo_barrio.json`: una fila `[mes, municipio, barrio, cuentas, saldo, recaudo]`.
* `gestores.json`: la tabla por gestor del tablero, agrupada por mes, gestor, zona,
  municipio, plan y resultado de la gestión. No trae barrio: gestor y barrio no se cruzan.

Van aparte del payload del mapa porque las gestiones no traen montos: contar gestiones
efectivas no responde «dónde se recaudó más», que es plata.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.taxonomy import norm_dato
from app.services.metrics_service import FiltroNoResuelto

NOMBRE_ARCHIVO = "recaudo_barrio.json"
NOMBRE_GESTORES = "gestores.json"
Criterio = Literal["recaudo", "saldo", "cuentas"]


class RecaudoNoDisponible(Exception):
    """El archivo no existe o no se pudo leer: el ETL todavía no lo generó."""


@dataclass(frozen=True)
class FilaRecaudo:
    mes: str
    municipio: str
    barrio: str
    cuentas: int
    saldo: float
    recaudo: float


@dataclass(frozen=True)
class Tabla:
    filas: tuple[FilaRecaudo, ...]
    provisionales: frozenset[str]

    @property
    def meses(self) -> list[str]:
        return sorted({f.mes for f in self.filas})


@dataclass(frozen=True)
class TablaGestores:
    """`gestores.json`: las filas llevan índices a los catálogos del mismo nombre."""

    gestores: list[str]
    meses: list[str]
    zonas: list[str]
    municipios: list[str]
    planes: list[str]
    filas: tuple[tuple, ...]
    provisionales: frozenset[str]


# Los archivos los regenera el ETL a diario: se releen solo si cambiaron.
_cache: dict[Path, tuple[float, Tabla | TablaGestores]] = {}


def _leer(ruta: Path, construir):
    try:
        firma = ruta.stat().st_mtime
        if ruta not in _cache or _cache[ruta][0] != firma:
            _cache[ruta] = (firma, construir(json.loads(ruta.read_text(encoding="utf-8"))))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RecaudoNoDisponible(
            f"No se pudo leer {ruta}: {exc}. Corre el ETL de cobros para generarlo."
        ) from exc
    return _cache[ruta][1]


def cargar(directorio: Path) -> Tabla:
    return _leer(directorio / NOMBRE_ARCHIVO, lambda c: Tabla(
        tuple(FilaRecaudo(*f) for f in c["filas"]), frozenset(c["provisionales"])))


def cargar_gestores(directorio: Path) -> TablaGestores:
    return _leer(directorio / NOMBRE_GESTORES, lambda c: TablaGestores(
        c["gestores"], c["meses"], c["zonas"], c["municipios"], c["planes"],
        tuple(tuple(f) for f in c["filas"]), frozenset(c["provisionales"])))


def _resolver_municipio(tabla: Tabla, texto: str) -> str:
    municipios = sorted({f.municipio for f in tabla.filas})
    coinciden = [m for m in municipios if norm_dato(m) == norm_dato(texto)]
    if len(coinciden) != 1:
        raise FiltroNoResuelto("municipio", texto, municipios)
    return coinciden[0]


def _resolver_barrio(tabla: Tabla, texto: str, municipio: str | None) -> set[tuple[str, str]]:
    """Pares (municipio, barrio) que coinciden. Puede haber varios: hay barrios
    homónimos en distintos municipios, y se devuelven todos en vez de elegir uno."""
    pares = {(f.municipio, f.barrio) for f in tabla.filas if municipio in (None, f.municipio)}
    buscado = norm_dato(texto)
    # El modelo suele pasar «El Tabor de Barranquilla» o «BARRANQUILLA | El Tabor»,
    # con el municipio pegado: se prueba también sin él antes de dar por perdido el barrio.
    variantes = {buscado}
    for nombre in {f.municipio for f in tabla.filas}:
        nm = norm_dato(nombre)
        if buscado.endswith(nm):
            variantes |= {buscado[: -len(nm)], buscado[: -len(nm)].removesuffix("de")}
        if buscado.startswith(nm):
            variantes.add(buscado[len(nm):])
    for variante in variantes - {""}:
        exactos = {p for p in pares if norm_dato(p[1]) == variante}
        if exactos:
            return exactos
    parciales = {p for p in pares if buscado and buscado in norm_dato(p[1])}
    if not parciales:
        raise FiltroNoResuelto("barrio", texto, sorted({p[1] for p in pares})[:20])
    return parciales


def consultar(
    tabla: Tabla,
    *,
    agrupar_por: Literal["barrio", "municipio"] = "barrio",
    meses: list[str] | None = None,
    municipio: str | None = None,
    barrio: str | list[str] | None = None,
    ordenar_por: Criterio = "recaudo",
    peores: bool = False,
    limite: int = 10,
) -> dict:
    """Suma recaudo y saldo por barrio o por municipio, en los meses pedidos.

    Raises:
        FiltroNoResuelto: si el municipio o el barrio no existen en el archivo.
            No se devuelve el total sin filtrar: la cifra sería de otro recorte.
    """
    filas = [f for f in tabla.filas if meses is None or f.mes in meses]
    if municipio:
        municipio = _resolver_municipio(tabla, municipio)
        filas = [f for f in filas if f.municipio == municipio]
    if barrio:
        nombres = [barrio] if isinstance(barrio, str) else barrio
        pares = set().union(*(_resolver_barrio(tabla, n, municipio) for n in nombres))
        filas = [f for f in filas if (f.municipio, f.barrio) in pares]

    grupos: dict[tuple[str, ...], list[float]] = {}
    for f in filas:
        clave = (f.municipio, f.barrio) if agrupar_por == "barrio" else (f.municipio,)
        acumulado = grupos.setdefault(clave, [0, 0.0, 0.0])
        acumulado[0] += f.cuentas
        acumulado[1] += f.saldo
        acumulado[2] += f.recaudo

    indice = {"cuentas": 0, "saldo": 1, "recaudo": 2}[ordenar_por]
    ordenados = sorted(grupos.items(), key=lambda kv: kv[1][indice], reverse=not peores)
    resultado = [
        {
            "nombre": clave[-1], "municipio": clave[0],
            "cuentas": int(v[0]), "saldo": round(v[1], 2), "recaudo": round(v[2], 2),
        }
        for clave, v in ordenados[:limite]
    ]
    usados = sorted({f.mes for f in filas})
    return {
        "agrupado_por": agrupar_por,
        "meses": usados,
        "provisional": sorted(set(usados) & tabla.provisionales),
        # Sin `cuentas` a propósito: con ese número a la vista el modelo lo pegaba al
        # recaudo de un barrio («sobre un total de 13.875 cuentas en todo el Atlántico»).
        "total_del_recorte": {
            "saldo": round(sum(f.saldo for f in filas), 2),
            "recaudo": round(sum(f.recaudo for f in filas), 2),
        },
        "grupos_en_total": len(grupos),
        "filas": resultado,
    }


# La cédula pegada al nombre («ANA PEREZ - 111») no le sirve a nadie y el tablero tampoco
# la muestra en cobros.
_CEDULA = re.compile(r"\s*-\s*\d+\s*$")


def _resolver(catalogo: list[str], texto: str, campo: str) -> int:
    coinciden = [i for i, nombre in enumerate(catalogo) if norm_dato(nombre) == norm_dato(texto)]
    if len(coinciden) != 1:
        raise FiltroNoResuelto(campo, texto, catalogo)
    return coinciden[0]


def consultar_gestores(
    tabla: TablaGestores,
    *,
    agrupar_por: Literal["gestor", "zona", "plan"],
    meses: list[str] | None = None,
    municipio: str | None = None,
    zona: str | None = None,
    plan: str | None = None,
    ordenar_por: Criterio = "recaudo",
    peores: bool = False,
    limite: int = 10,
) -> dict:
    """Suma recaudo y saldo por gestor, zona o plan, en los meses pedidos.

    Raises:
        FiltroNoResuelto: si el municipio, la zona o el plan no existen. No se devuelve
            el total sin filtrar: la cifra sería de otro recorte.
    """
    en_meses = {tabla.meses.index(m) for m in meses if m in tabla.meses} if meses else None
    i_muni = _resolver(tabla.municipios, municipio, "municipio") if municipio else None
    i_zona = _resolver(tabla.zonas, zona, "zona") if zona else None
    i_plan = _resolver(tabla.planes, plan, "plan") if plan else None

    posicion = {"gestor": 0, "zona": 2, "plan": 4}[agrupar_por]
    grupos: dict[int, list[float]] = {}
    usados: set[int] = set()
    for f in tabla.filas:
        if ((en_meses is not None and f[1] not in en_meses) or (i_muni is not None and f[3] != i_muni)
                or (i_zona is not None and f[2] != i_zona) or (i_plan is not None and f[4] != i_plan)):
            continue
        usados.add(f[1])
        acumulado = grupos.setdefault(f[posicion], [0, 0.0, 0.0])
        acumulado[0] += f[7]
        acumulado[1] += f[8]
        acumulado[2] += f[9]

    catalogo = {"gestor": tabla.gestores, "zona": tabla.zonas, "plan": tabla.planes}[agrupar_por]
    indice = {"cuentas": 0, "saldo": 1, "recaudo": 2}[ordenar_por]
    ordenados = sorted(grupos.items(), key=lambda kv: kv[1][indice], reverse=not peores)
    meses_usados = sorted(tabla.meses[i] for i in usados)
    return {
        "agrupado_por": agrupar_por,
        "meses": meses_usados,
        "provisional": sorted(set(meses_usados) & tabla.provisionales),
        # Sin `cuentas`, por la misma razón que en `consultar`.
        "total_del_recorte": {
            "saldo": round(sum(v[1] for v in grupos.values()), 2),
            "recaudo": round(sum(v[2] for v in grupos.values()), 2),
        },
        "grupos_en_total": len(grupos),
        "filas": [
            {"nombre": _CEDULA.sub("", catalogo[i]), "cuentas": int(v[0]),
             "saldo": round(v[1], 2), "recaudo": round(v[2], 2)}
            for i, v in ordenados[:limite]
        ],
    }
