"""Recaudo y saldo por barrio y municipio (solo COBROS).

Lee `recaudo_barrio.json`, que genera el ETL desde `v_aire_cobros_nic_mes`: una fila
`[mes, municipio, barrio, cuentas, saldo, recaudo]` por barrio y mes. Va aparte del
payload del mapa porque el tablero no lo usa y las gestiones no traen montos: contar
gestiones efectivas no responde «dónde se recaudó más», que es plata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.taxonomy import norm_dato
from app.services.metrics_service import FiltroNoResuelto

NOMBRE_ARCHIVO = "recaudo_barrio.json"
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


# El archivo pesa ~400 KB y lo regenera el ETL a diario: se relee solo si cambió.
_cache: dict[Path, tuple[float, Tabla]] = {}


def cargar(directorio: Path) -> Tabla:
    ruta = directorio / NOMBRE_ARCHIVO
    try:
        firma = ruta.stat().st_mtime
        if ruta not in _cache or _cache[ruta][0] != firma:
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
            filas = tuple(FilaRecaudo(*f) for f in crudo["filas"])
            _cache[ruta] = (firma, Tabla(filas, frozenset(crudo["provisionales"])))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RecaudoNoDisponible(
            f"No se pudo leer {ruta}: {exc}. Corre el ETL de cobros para generarlo."
        ) from exc
    return _cache[ruta][1]


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
    barrio: str | None = None,
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
        pares = _resolver_barrio(tabla, barrio, municipio)
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
