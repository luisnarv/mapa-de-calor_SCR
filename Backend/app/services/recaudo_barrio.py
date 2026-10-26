"""Recaudo y saldo por barrio, municipio, gestor, zona y plan (solo COBROS).

Dos archivos del ETL, los dos desde `v_aire_cobros_nic_mes` y con el mismo universo de
cuentas, así que los totales coinciden:

* `recaudo_barrio.json`: una fila `[mes, municipio, barrio, plan, zona, cuentas, saldo, recaudo]`
  (una por barrio, plan y zona; el formato anterior, sin plan ni zona, se sigue leyendo).
* `gestores.json`: la tabla por gestor del tablero, agrupada por mes, gestor, zona,
  municipio, plan y resultado de la gestión. No trae barrio: gestor y barrio no se cruzan
  (el plan y la zona sí cruzan con el barrio, por el otro archivo).

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
# Las cuentas sin gestión con GPS en el mapa quedan en «SIN BARRIO» de su municipio (el ETL las
# conserva para que el total cuadre con gestores.json). No es un barrio y no se recomienda ni se rankea.
SIN_BARRIO = "SIN BARRIO"
NOMBRE_GESTORES = "gestores.json"
Criterio = Literal["recaudo", "saldo", "cuentas", "recaudo_sobre_saldo", "recaudo_por_gestion"]


def _valor(grupo: list, criterio: str, gestiones: int = 0) -> float:
    """El número por el que se ordena un grupo `[cuentas, saldo, recaudo, ...]`."""
    if criterio == "recaudo_sobre_saldo":
        return grupo[2] / grupo[1]
    if criterio == "recaudo_por_gestion":
        return grupo[2] / gestiones
    return grupo[{"cuentas": 0, "saldo": 1, "recaudo": 2}[criterio]]


def _tiene_base(grupo: list, criterio: str, gestiones: int = 0) -> bool:
    """Una razón sin denominador (deuda 0, ninguna gestión) no existe: no se ordena como un 0."""
    return not ((criterio == "recaudo_sobre_saldo" and grupo[1] <= 0)
                or (criterio == "recaudo_por_gestion" and gestiones <= 0))


def comparar(ahora: dict, antes: dict) -> list[dict]:
    """Une dos consultas completas (mismas filas posibles) y calcula cuánto cambió cada una.

    Un grupo que no aparece en uno de los dos meses cuenta como 0 en ese: que la deuda de un
    barrio «aparezca» de un mes al otro también es una variación.
    """
    clave = lambda f: (f.get("municipio"), f["nombre"])  # noqa: E731
    previas = {clave(f): f for f in antes["filas"]}
    nuevas = {clave(f): f for f in ahora["filas"]}
    filas = []
    for k in dict.fromkeys([*nuevas, *previas]):
        n, p = nuevas.get(k), previas.get(k)
        base = n or p
        filas.append({
            **{c: base[c] for c in ("nombre", "municipio") if c in base},
            "saldo": n["saldo"] if n else 0.0, "saldo_anterior": p["saldo"] if p else 0.0,
            "variacion_saldo": round((n["saldo"] if n else 0.0) - (p["saldo"] if p else 0.0), 2),
            "recaudo": n["recaudo"] if n else 0.0, "recaudo_anterior": p["recaudo"] if p else 0.0,
            "variacion_recaudo": round((n["recaudo"] if n else 0.0) - (p["recaudo"] if p else 0.0), 2),
        })
    return filas


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
    plan: str | None = None
    zona: str | None = None


def _fila(f: list) -> FilaRecaudo:
    """Una fila del archivo, en el formato nuevo (8 columnas) o en el anterior (6)."""
    if len(f) == 8:
        mes, municipio, barrio, plan, zona, cuentas, saldo, recaudo = f
        return FilaRecaudo(mes, municipio, barrio, cuentas, saldo, recaudo, plan, zona)
    return FilaRecaudo(*f)


@dataclass(frozen=True)
class Tabla:
    filas: tuple[FilaRecaudo, ...]
    provisionales: frozenset[str]

    @property
    def tiene_plan_y_zona(self) -> bool:
        """Los archivos generados antes del cambio del ETL no traen plan ni zona por barrio."""
        return any(f.plan is not None for f in self.filas[:1])

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
        tuple(_fila(f) for f in c["filas"]), frozenset(c["provisionales"])))


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
    zona: str | None = None,
    plan: str | None = None,
    ordenar_por: Criterio = "recaudo",
    peores: bool = False,
    limite: int = 10,
) -> dict:
    """Suma recaudo y saldo por barrio o por municipio, en los meses pedidos.

    Raises:
        FiltroNoResuelto: si el municipio, el barrio, la zona o el plan no existen en el archivo.
            No se devuelve el total sin filtrar: la cifra sería de otro recorte.
        RecaudoNoDisponible: si piden zona o plan y el archivo es del formato anterior.
    """
    filas = [f for f in tabla.filas if meses is None or f.mes in meses]
    for campo, pedido in (("zona", zona), ("plan", plan)):
        if not pedido:
            continue
        if not tabla.tiene_plan_y_zona:
            raise RecaudoNoDisponible(
                "recaudo_barrio.json no trae plan ni zona por barrio: es de antes del cambio del ETL. "
                "Corre el ETL de cobros para regenerarlo.")
        valores = sorted({getattr(f, campo) for f in tabla.filas})
        coinciden = [v for v in valores if norm_dato(v) == norm_dato(pedido)]
        if len(coinciden) != 1:
            raise FiltroNoResuelto(campo, pedido, valores)
        filas = [f for f in filas if getattr(f, campo) == coinciden[0]]
    if municipio:
        municipio = _resolver_municipio(tabla, municipio)
        filas = [f for f in filas if f.municipio == municipio]
    if barrio:
        nombres = [barrio] if isinstance(barrio, str) else barrio
        pares = set().union(*(_resolver_barrio(tabla, n, municipio) for n in nombres))
        filas = [f for f in filas if (f.municipio, f.barrio) in pares]

    # El recaudo es un flujo y se suma entre meses; el saldo y las cuentas son una foto de
    # cada mes: sumarlas daba una deuda de 67.904 millones para quien debe 9.470 en septiembre.
    # Cada grupo conserva su último mes con datos: uno que ya no aparece en el último mes
    # del periodo igual debe lo que debía. Un mes provisional (el que va a medias) solo
    # trae las cuentas gestionadas hasta hoy, así que su saldo no es la deuda: se usa el
    # último mes completo, salvo que solo hayan pedido meses provisionales.
    completos = {f.mes for f in filas} - tabla.provisionales
    foto = completos or {f.mes for f in filas}
    ultimo = max(foto, default=None)
    grupos: dict[tuple[str, ...], list] = {}
    for f in filas:
        clave = (f.municipio, f.barrio) if agrupar_por == "barrio" else (f.municipio,)
        acumulado = grupos.setdefault(clave, [0, 0.0, 0.0, ""])
        acumulado[2] += f.recaudo
        if f.mes not in foto:
            continue
        if f.mes > acumulado[3]:
            acumulado[0], acumulado[1], acumulado[3] = 0, 0.0, f.mes
        if f.mes == acumulado[3]:
            acumulado[0] += f.cuentas
            acumulado[1] += f.saldo

    if ordenar_por == "recaudo_por_gestion":
        raise ValueError("recaudo_por_gestion solo existe por gestor, zona o plan: no hay gestiones por barrio.")
    # «SIN BARRIO» tiene poca deuda y mucho recaudo, así que encabezaba cualquier ranking por razón
    # (7.331 %) y se recomendaba como barrio. Sale de la lista, pero su plata sigue en el total y va
    # aparte en `sin_barrio`. Si piden un barrio por su nombre, no se toca.
    aparte = {}
    if agrupar_por == "barrio" and not barrio:
        aparte = {k: v for k, v in grupos.items() if k[-1] == SIN_BARRIO}
        grupos = {k: v for k, v in grupos.items() if k not in aparte}
    candidatos = [kv for kv in grupos.items() if _tiene_base(kv[1], ordenar_por)]
    ordenados = sorted(candidatos, key=lambda kv: _valor(kv[1], ordenar_por), reverse=not peores)
    resultado = [
        {
            "nombre": clave[-1], "municipio": clave[0],
            "cuentas": int(v[0]), "saldo": round(v[1], 2), "recaudo": round(v[2], 2),
            "recaudo_sobre_saldo": round(v[2] / v[1], 4) if v[1] > 0 else None,
            "recaudo_sobre_saldo_pct": round(100 * v[2] / v[1], 1) if v[1] > 0 else None,
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
            "saldo": round(sum(v[1] for v in (*grupos.values(), *aparte.values())), 2),
            "recaudo": round(sum(v[2] for v in (*grupos.values(), *aparte.values())), 2),
        },
        "saldo_al_cierre_de": ultimo,
        "grupos_en_total": len(grupos),
        "grupos_sin_recaudo": sum(1 for v in grupos.values() if v[2] <= 0),
        **({"sin_barrio": {"saldo": round(sum(v[1] for v in aparte.values()), 2),
                           "recaudo": round(sum(v[2] for v in aparte.values()), 2)}} if aparte else {}),
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
    gestores: list[str] | None = None,
    gestiones: dict[str, int] | None = None,
    ordenar_por: Criterio = "recaudo",
    peores: bool = False,
    limite: int = 10,
) -> dict:
    """Suma recaudo y saldo por gestor, zona o plan, en los meses pedidos.

    `gestores` son nombres exactos del catálogo (con la cédula, como los trae el tablero).
    `gestiones` es cuántas gestiones hizo cada nombre del catálogo en el mismo periodo: sale del
    payload y es lo que permite el recaudo por gestión.

    Raises:
        FiltroNoResuelto: si el municipio, la zona o el plan no existen. No se devuelve
            el total sin filtrar: la cifra sería de otro recorte.
    """
    en_meses = {tabla.meses.index(m) for m in meses if m in tabla.meses} if meses else None
    i_muni = _resolver(tabla.municipios, municipio, "municipio") if municipio else None
    i_zona = _resolver(tabla.zonas, zona, "zona") if zona else None
    i_plan = _resolver(tabla.planes, plan, "plan") if plan else None
    i_gestores = {_resolver(tabla.gestores, g, "gestor") for g in gestores} if gestores else None

    posicion = {"gestor": 0, "zona": 2, "plan": 4}[agrupar_por]
    elegidas = [
        f for f in tabla.filas
        if not ((en_meses is not None and f[1] not in en_meses) or (i_muni is not None and f[3] != i_muni)
                or (i_zona is not None and f[2] != i_zona) or (i_plan is not None and f[4] != i_plan)
                or (i_gestores is not None and f[0] not in i_gestores))
    ]
    usados = {f[1] for f in elegidas}
    # Igual que en `consultar`: el saldo y las cuentas son del último mes completo de cada grupo.
    completos = {m for m in usados if tabla.meses[m] not in tabla.provisionales}
    foto = completos or usados
    ultimo = max(foto, default=None)
    grupos: dict[int, list] = {}
    for f in elegidas:
        acumulado = grupos.setdefault(f[posicion], [0, 0.0, 0.0, -1])
        acumulado[2] += f[9]
        if f[1] not in foto:
            continue
        if f[1] > acumulado[3]:
            acumulado[0], acumulado[1], acumulado[3] = 0, 0.0, f[1]
        if f[1] == acumulado[3]:
            acumulado[0] += f[7]
            acumulado[1] += f[8]

    catalogo = {"gestor": tabla.gestores, "zona": tabla.zonas, "plan": tabla.planes}[agrupar_por]
    gestiones = gestiones or {}
    n_gestiones = lambda i: gestiones.get(catalogo[i], 0)  # noqa: E731
    candidatos = [kv for kv in grupos.items() if _tiene_base(kv[1], ordenar_por, n_gestiones(kv[0]))]
    ordenados = sorted(
        candidatos, key=lambda kv: _valor(kv[1], ordenar_por, n_gestiones(kv[0])), reverse=not peores)
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
        "saldo_al_cierre_de": tabla.meses[ultimo] if ultimo is not None else None,
        "grupos_en_total": len(grupos),
        "grupos_sin_recaudo": sum(1 for v in grupos.values() if v[2] <= 0),
        "filas": [
            {"nombre": _CEDULA.sub("", catalogo[i]), "cuentas": int(v[0]),
             "saldo": round(v[1], 2), "recaudo": round(v[2], 2),
             "recaudo_sobre_saldo": round(v[2] / v[1], 4) if v[1] > 0 else None,
             "recaudo_sobre_saldo_pct": round(100 * v[2] / v[1], 1) if v[1] > 0 else None,
             **({"gestiones": n_gestiones(i),
                 "recaudo_por_gestion": round(v[2] / n_gestiones(i), 2) if n_gestiones(i) else None}
                if gestiones else {})}
            for i, v in ordenados[:limite]
        ],
    }
