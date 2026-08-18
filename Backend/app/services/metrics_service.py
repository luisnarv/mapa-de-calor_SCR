"""Métricas operativas calculadas sobre el payload del ETL.

La agregación es un espejo de `page.js:470-560`: mismos conteos, mismo criterio
de "no controlable", mismas dos efectividades. Al leer los mismos archivos que
el tablero, las cifras del chat no pueden discrepar de las del mapa.

Todo ocurre en memoria: recorrer 177k enteros toma decenas de milisegundos, así
que no hace falta caché ni precalentado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app.core.taxonomy import norm_dato
from app.schemas.metrics import CandidatoBarrio, Efectividad, FilaCausa
from app.services.payload_store import Payload, obtener

logger = logging.getLogger(__name__)


class BarrioNoEncontrado(Exception):
    def __init__(self, texto: str) -> None:
        super().__init__(texto)
        self.texto = texto


class BarrioAmbiguo(Exception):
    def __init__(self, texto: str, candidatos: Sequence[CandidatoBarrio]) -> None:
        super().__init__(texto)
        self.texto = texto
        self.candidatos = list(candidatos)


@dataclass
class Conteo:
    """Acumulador por grupo. Espejo del `bump()` de page.js."""

    tot: int = 0
    ef: int = 0
    fa: int = 0
    pe: int = 0
    noctrl: int = 0
    causas: dict[int, int] = field(default_factory=dict)

    def sumar(self, estado: int, causa: int, no_controlable: bool) -> None:
        self.tot += 1
        if estado == 0:
            self.ef += 1
        elif estado == 1:
            self.fa += 1
        else:
            self.pe += 1
        if no_controlable:
            self.noctrl += 1
        if estado != 0:
            self.causas[causa] = self.causas.get(causa, 0) + 1


def _pct(x: int, y: int) -> float:
    return round(x / y * 100, 1) if y else 0.0


def _a_dto(conteo: Conteo, nombre: str, municipio: str | None = None) -> Efectividad:
    den = conteo.tot - conteo.noctrl
    return Efectividad(
        nombre=nombre,
        municipio=municipio,
        tot=conteo.tot,
        ef=conteo.ef,
        fa=conteo.fa,
        pe=conteo.pe,
        noctrl=conteo.noctrl,
        ef_pct=_pct(conteo.ef, conteo.tot),
        ef_adj=_pct(conteo.ef, den) if den > 0 else 0.0,
    )


class MetricsService:
    """Consultas de negocio sobre el payload. Los métodos son `async` solo para
    no cambiar el contrato con `tools.py`; el cálculo es síncrono."""

    def __init__(self, directorio: Path) -> None:
        self.directorio = directorio

    @property
    def datos(self) -> Payload:
        return obtener(self.directorio)

    # --- Resolución de nombres ------------------------------------------------

    async def buscar_barrios(self, texto: str, limite: int = 8) -> list[CandidatoBarrio]:
        """Busca por el nombre del barrio (sin el municipio), tolerando tildes."""
        p = self.datos
        objetivo = norm_dato(texto)
        if not objetivo:
            return []

        totales = self._totales_por_barrio()
        encontrados = []
        for i, bkey in enumerate(p.barrios):
            _, _, barrio = bkey.partition(" | ")
            if objetivo in norm_dato(barrio):
                encontrados.append(
                    CandidatoBarrio(
                        bkey=bkey,
                        barrio=barrio,
                        municipio=p.munis[p.b_muni[i]],
                        tot=totales[i],
                    )
                )

        encontrados.sort(key=lambda c: c.tot, reverse=True)
        return encontrados[:limite]

    async def resolver_barrio(self, texto: str) -> CandidatoBarrio:
        """Devuelve el barrio único que coincide con el texto.

        Raises:
            BarrioNoEncontrado: si no coincide ninguno.
            BarrioAmbiguo: si coinciden varios y ninguno es coincidencia exacta.
        """
        candidatos = await self.buscar_barrios(texto, limite=8)
        if not candidatos:
            raise BarrioNoEncontrado(texto)
        if len(candidatos) == 1:
            return candidatos[0]

        objetivo = norm_dato(texto)
        exactos = [c for c in candidatos if norm_dato(c.barrio) == objetivo]
        if len(exactos) == 1:
            return exactos[0]
        raise BarrioAmbiguo(texto, exactos or candidatos)

    # --- Métricas -------------------------------------------------------------

    async def efectividad(
        self,
        *,
        bkey: str | None = None,
        municipio: str | None = None,
        zona: str | None = None,
        mes: str | None = None,
        tipo_os: str | None = None,
    ) -> Efectividad:
        """Efectividad del recorte indicado. Sin filtros, de todo el histórico."""
        conteos = self._agrupar(
            bkey=bkey, municipio=municipio, zona=zona, mes=mes, tipo_os=tipo_os
        )
        etiqueta = bkey or municipio or zona or "Todo el Atlántico"
        return _a_dto(conteos.get(0, Conteo()), etiqueta)

    async def ranking(
        self,
        *,
        dimension: str = "brigada",
        bkey: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        min_ordenes: int = 10,
        limite: int = 10,
        ascendente: bool = False,
    ) -> list[Efectividad]:
        """Ordena brigadas, técnicos o barrios por efectividad ajustada.

        Se usa la ajustada porque es como el tablero ya ordena estos rankings
        (Dock.js:295): comparar por la cruda castiga a quien recibe más órdenes
        con causas fuera de su control.
        """
        if dimension not in ("brigada", "tecnico", "barrio"):
            raise ValueError(f"Dimensión no soportada: {dimension}")

        p = self.datos
        conteos = self._agrupar(bkey=bkey, municipio=municipio, mes=mes, por=dimension)
        catalogo = {"brigada": p.brigs, "tecnico": p.tecs, "barrio": p.barrios}[dimension]

        filas = [
            _a_dto(c, catalogo[i]) for i, c in conteos.items() if c.tot >= min_ordenes
        ]
        filas.sort(key=lambda f: f.ef_adj, reverse=not ascendente)
        return filas[:limite]

    async def causas(
        self,
        *,
        bkey: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        limite: int = 6,
    ) -> list[FilaCausa]:
        """Causas de las órdenes NO efectivas, de mayor a menor."""
        p = self.datos
        conteo = self._agrupar(bkey=bkey, municipio=municipio, mes=mes).get(0, Conteo())

        total = sum(conteo.causas.values())
        ordenadas = sorted(conteo.causas.items(), key=lambda kv: kv[1], reverse=True)
        return [
            FilaCausa(
                causa=p.causas[c],
                familia=p.causa_fam[c],
                n=n,
                pct=_pct(n, total),
                controlable=bool(p.causa_ctrl[c]),
            )
            for c, n in ordenadas[:limite]
        ]

    async def meses_disponibles(self) -> list[str]:
        """Meses con datos, del más reciente al más antiguo."""
        return list(reversed(self.datos.meses))

    # --- Interno --------------------------------------------------------------

    def _totales_por_barrio(self) -> list[int]:
        p = self.datos
        totales = [0] * len(p.barrios)
        for bi in p.b:
            totales[bi] += 1
        return totales

    def _indice(self, catalogo: list[str], valor: str | None) -> int | None:
        """Traduce un nombre a su índice, tolerando tildes y mayúsculas."""
        if valor is None:
            return None
        objetivo = norm_dato(valor)
        for i, nombre in enumerate(catalogo):
            if norm_dato(nombre) == objetivo:
                return i
        return None

    def _agrupar(
        self,
        *,
        bkey: str | None = None,
        municipio: str | None = None,
        zona: str | None = None,
        mes: str | None = None,
        tipo_os: str | None = None,
        por: str | None = None,
    ) -> dict[int, Conteo]:
        """Recorre las órdenes una vez, filtrando y acumulando.

        Sin `por`, todo cae en la clave 0. El criterio de no controlable es el
        mismo de page.js:474: `estado != Efectiva AND causa no controlable`.
        """
        p = self.datos

        f_barrio = self._indice(p.barrios, bkey)
        f_muni = self._indice(p.munis, municipio)
        f_zona = self._indice(p.zonas, zona)
        f_tipo = self._indice(p.tipos, tipo_os)
        f_mes = p.meses.index(mes) if mes in p.meses else None

        # Un filtro que no resuelve a nada devolvería el total sin filtrar, que es
        # peor que devolver vacío: el usuario creería que la cifra es de su barrio.
        for pedido, resuelto in (
            (bkey, f_barrio), (municipio, f_muni), (zona, f_zona),
            (tipo_os, f_tipo), (mes, f_mes),
        ):
            if pedido is not None and resuelto is None:
                logger.info("Filtro sin coincidencia: %r", pedido)
                return {}

        B, C, E, MES, O = p.b, p.c, p.e, p.mes, p.o
        ctrl, b_muni, b_zona = p.causa_ctrl, p.b_muni, p.b_zona
        grupo = {"brigada": p.g, "tecnico": p.t, "barrio": p.b}.get(por)

        conteos: dict[int, Conteo] = {}
        for i in range(len(E)):
            bi = B[i]
            if f_barrio is not None and bi != f_barrio:
                continue
            if f_muni is not None and b_muni[bi] != f_muni:
                continue
            if f_zona is not None and b_zona[bi] != f_zona:
                continue
            if f_mes is not None and MES[i] != f_mes:
                continue
            if f_tipo is not None and O[i] != f_tipo:
                continue

            clave = grupo[i] if grupo is not None else 0
            conteo = conteos.get(clave)
            if conteo is None:
                conteo = conteos[clave] = Conteo()

            estado, causa = E[i], C[i]
            conteo.sumar(estado, causa, estado != 0 and ctrl[causa] == 0)

        return conteos
