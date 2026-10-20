"""Índice de riesgo por barrio (0 a 100): el de la cola de triaje del tablero.

Es el mismo cálculo de `Tablero.js` (bloque «RISK ENGINE»), pasado a Python para que el chat y el
mapa nombren a los mismos barrios como críticos. Si se cambian los pesos, los cortes o la forma de
medir la tendencia allá, hay que cambiarlos aquí: una prueba compara los dos.
"""

from __future__ import annotations

import math
from typing import Any

from app.services.metrics_service import MetricsService

# Peso de cada componente del índice. En COBROS solo cuentan las pérdidas (V.pesos en Tablero.js).
PESOS_SCR = {"pe": 0.4, "fa": 0.25, "vol": 0.15, "tr": 0.1, "hist": 0.1}
MUESTRA_PEQUENA = 30  # COBROS: un barrio con menos gestiones que esto no es una muestra fiable
MIN_ORDENES_SCR = 10
MIN_ORDENES_COBROS = 1


def _redondear(x: float) -> int:
    """Como `Math.round` de JavaScript (la mitad sube); `round` de Python la lleva al par."""
    return math.floor(x + 0.5)


def _cuantil(ordenado: list[float], q: float) -> float:
    if not ordenado:
        return 0.0
    pos = (len(ordenado) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordenado[lo] + (ordenado[hi] - ordenado[lo]) * (pos - lo)


def _acotar(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _pct(parte: float, total: float) -> float:
    return parte / total * 100 if total else 0.0


def _nivel_cobros(m: float) -> int:
    """Cuántas veces el promedio pierde el barrio, llevado a la escala 0-100 de siempre:
    los cortes 1×, 2× y 3× caen en 30, 60 y 80 para reutilizar los colores del índice."""
    if m < 1:
        return _redondear(30 * m)
    if m < 2:
        return 31 + _redondear((m - 1) * 29)
    if m < 3:
        return 61 + _redondear((m - 2) * 19)
    return 81 + _redondear(min(m - 3, 3) / 3 * 19)


def calcular(
    metrics: MetricsService, *, cobros: bool, min_ordenes: int | None = None, **filtros: Any,
) -> list[dict[str, Any]]:
    """Los barrios del recorte con su riesgo, del más al menos crítico.

    `filtros` son los de `MetricsService._agrupar` (meses, municipio, zona, brigada...). Quedan
    fuera los barrios con menos órdenes que `min_ordenes`, como en el tablero.
    """
    p = metrics.datos
    minimo = min_ordenes if min_ordenes is not None else (MIN_ORDENES_COBROS if cobros else MIN_ORDENES_SCR)
    ctrl = p.causa_ctrl  # en COBROS el payload ya las trae todas como controlables
    barrios: dict[int, dict[str, Any]] = {}
    tecnicos: dict[int, list[int]] = {}  # técnico -> [efectivas, total, no controlables]
    dias: set[int] = set()

    def visitar(i: int) -> None:
        e, b, t, dia = p.e[i], p.b[i], p.t[i], p.m[i] // 1440
        no_ctrl = e != 0 and ctrl[p.c[i]] == 0
        o = barrios.get(b)
        if o is None:
            o = barrios[b] = {"tot": 0, "ef": 0, "fa": 0, "pe": 0, "fa_ctrl": 0, "dia": {}, "tec": {}}
        o["tot"] += 1
        o[("ef", "fa", "pe")[e]] += 1
        if e == 1 and ctrl[p.c[i]] == 1:
            o["fa_ctrl"] += 1
        por_dia = o["dia"].setdefault(dia, [0, 0, 0])
        por_dia[e] += 1
        o["tec"][t] = o["tec"].get(t, 0) + 1
        dias.add(dia)
        x = tecnicos.setdefault(t, [0, 0, 0])
        x[1] += 1
        x[0] += e == 0
        x[2] += no_ctrl

    metrics._agrupar(visitar=visitar, **filtros)
    if not barrios:
        return []

    total = sum(o["tot"] for o in barrios.values())
    pe_global = _pct(sum(o["pe"] for o in barrios.values()), total)
    elegibles = {b: o for b, o in barrios.items() if o["tot"] >= minimo}
    if not elegibles:
        return []

    # Mitad del rango con datos (no del mes calendario), como el tablero.
    mitad = (min(dias) + max(dias)) // 2
    ef_adj_tec = {t: _pct(x[0], x[1] - x[2]) if x[1] - x[2] > 0 else 0.0 for t, x in tecnicos.items()}
    for o in elegibles.values():
        a1 = n1 = a2 = n2 = 0
        for dia, (ef, fa, pe) in o["dia"].items():
            if dia <= mitad:
                a1, n1 = a1 + fa + pe, n1 + ef + fa + pe
            else:
                a2, n2 = a2 + fa + pe, n2 + ef + fa + pe
        o["tendencia"] = _pct(a2, n2) - _pct(a1, n1) if n2 >= 3 and n1 >= 3 else 0.0
        peso = sum(o["tec"][t] for t in o["tec"] if tecnicos[t][1] - tecnicos[t][2] > 0)
        o["hist_tec"] = sum(ef_adj_tec[t] * c for t, c in o["tec"].items() if tecnicos[t][1] - tecnicos[t][2] > 0) / peso if peso else 0.0
        o["pe_pct"], o["fa_pct"] = _pct(o["pe"], o["tot"]), _pct(o["fa"], o["tot"])
        o["fa_ctrl_pct"] = _pct(o["fa_ctrl"], o["tot"])

    if not cobros:
        def q90(f) -> float:
            return _cuantil(sorted(f(o) for o in elegibles.values()), 0.9)

        ref_pe, ref_fa = max(q90(lambda o: o["pe_pct"]), 2), max(q90(lambda o: o["fa_ctrl_pct"]), 2)
        ref_vo = max(q90(lambda o: o["tot"]), 1)
        positivas = sorted(o["tendencia"] for o in elegibles.values() if o["tendencia"] > 0)
        ref_tr = max(_cuantil(positivas, 0.9) or 10, 5)
        hist = sorted(o["hist_tec"] for o in elegibles.values())
        h_bueno, h_malo = _cuantil(hist, 0.9), _cuantil(hist, 0.1)

    filas = []
    for b, o in elegibles.items():
        if cobros:
            m = o["pe_pct"] / pe_global if pe_global > 0 else 0.0
            riesgo = _nivel_cobros(m)
            nivel = "Crítica" if riesgo >= 81 else "Alta" if riesgo >= 61 else "Media" if riesgo >= 31 else "Baja"
            extra = {"veces_el_promedio": round(m, 2), "muestra_pequena": o["tot"] < MUESTRA_PEQUENA}
        else:
            r_hi = 1 - _acotar((o["hist_tec"] - h_malo) / (h_bueno - h_malo)) if h_bueno > h_malo else 0.0
            partes = {
                "pe": PESOS_SCR["pe"] * _acotar(o["pe_pct"] / ref_pe),
                "fa": PESOS_SCR["fa"] * _acotar(o["fa_ctrl_pct"] / ref_fa),
                "vol": PESOS_SCR["vol"] * _acotar(o["tot"] / ref_vo),
                "tr": PESOS_SCR["tr"] * _acotar(o["tendencia"] / ref_tr),
                "hist": PESOS_SCR["hist"] * r_hi,
            }
            riesgo = _redondear(100 * sum(partes.values()))
            nivel = "Alta" if riesgo >= 61 else "Media" if riesgo >= 31 else "Baja"
            extra = {"fallidas_controlables_pct": round(o["fa_ctrl_pct"], 1), "tendencia_pp": round(o["tendencia"], 1)}
        filas.append({
            "barrio": p.barrios[b], "riesgo": riesgo, "nivel": nivel, "ordenes": o["tot"],
            "perdidas": o["pe"], "perdidas_pct": round(o["pe_pct"], 1), **extra,
        })
    if cobros:
        # Como la cola de triaje de COBROS (RiskSpine.js): dentro de cada nivel van primero los de
        # muestra suficiente, para que un barrio de 7 gestiones no quede por encima de uno grande.
        nivel = lambda r: 3 if r >= 81 else 2 if r >= 61 else 1 if r >= 31 else 0  # noqa: E731
        filas.sort(key=lambda f: (-nivel(f["riesgo"]), f["muestra_pequena"], -f["riesgo"], -f["perdidas"]))
    else:
        filas.sort(key=lambda f: (-f["riesgo"], -f["ordenes"]))
    return filas
