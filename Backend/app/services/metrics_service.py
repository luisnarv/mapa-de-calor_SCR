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

from app.core.taxonomy import norm, norm_dato
from app.schemas.metrics import (
    BusquedaObservaciones,
    CandidatoBarrio,
    Efectividad,
    FilaCausa,
    FilaMencion,
    FilaZona,
)
from app.services.payload_store import (
    Payload,
    leer_actas,
    obtener,
    obtener_observaciones,
)

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


# Criterios por los que se puede ordenar un ranking. `perdidas` son las órdenes
# que NO se pagan: son las que cuestan plata, a diferencia de las fallidas, que
# no se ejecutaron pero sí se cobran.
CRITERIOS = {
    "ef_adj": lambda f: f.ef_adj,
    "ef_pct": lambda f: f.ef_pct,
    "ef_pond": lambda f: f.ef_pond or 0.0,
    "ef_adj_pond": lambda f: f.ef_adj_pond or 0.0,
    "perdidas": lambda f: f.pe,
    "pct_perdidas": lambda f: f.pe / f.tot if f.tot else 0.0,
    "fallidas": lambda f: f.fa,
    "pct_fallidas": lambda f: f.fa / f.tot if f.tot else 0.0,
    "volumen": lambda f: f.tot,
}


# Órdenes "prestadas" de la media que se le suman a cada grupo al ponderar. Con
# 50, un barrio de 10 órdenes queda dominado por ellas y uno de 400 apenas se
# mueve, que es justo el efecto buscado: exigir muestra para creerle a un
# porcentaje. Es del orden del volumen típico de un barrio en un mes; subirlo
# aplana el ranking y bajarlo deja colarse otra vez a los de muestra mínima.
PESO_PREVIO = 50


def _ponderar(exitos: int, base: int, media: float) -> float:
    """Acerca el porcentaje a la media del conjunto según la muestra.

    El promedio ponderado de siempre: a `base` intentos observados se le suman
    `PESO_PREVIO` imaginarios que salieron como el promedio. Con poca muestra
    mandan los imaginarios y el grupo se pega a la media; con mucha, pesan tan
    poco que el porcentaje real queda casi intacto.

    Sin esto, «el mejor barrio» era siempre uno de diez órdenes que salieron
    todas bien, y los barrios con historia no aparecían nunca.
    """
    if base <= 0:
        # Sin nada observado, lo único honesto es la media: no sabemos si es
        # bueno o malo. Devolver 0 lo mandaba a encabezar «los peores», y hay
        # barrios enteros con todas sus órdenes fuera de control de la operación
        # —su denominador ajustado es 0— que aparecían como el peor del mes sin
        # tener una sola orden que se les pudiera reprochar.
        return round(media * 100, 1)
    return round((exitos + PESO_PREVIO * media) / (base + PESO_PREVIO) * 100, 1)


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
        # Recorrer 177k filas por cada candidato saldría caro; se hace una vez.
        self._totales: list[int] | None = None

    @property
    def datos(self) -> Payload:
        return obtener(self.directorio)

    # --- Resolución de nombres ------------------------------------------------

    async def buscar_barrios(
        self, texto: str, limite: int | None = None
    ) -> list[CandidatoBarrio]:
        """Busca por el nombre del barrio (sin el municipio), tolerando tildes.

        Sin `limite` devuelve todos: recortar aquí hacía que una desambiguación
        listara 7 de 10 barrios y el usuario no encontrara el suyo.
        """
        p = self.datos
        objetivo = norm_dato(texto)
        if not objetivo:
            return []

        encontrados = [
            self._candidato(i)
            for i, bkey in enumerate(p.barrios)
            if objetivo in norm_dato(bkey.partition(" | ")[2])
        ]
        encontrados.sort(key=lambda c: c.tot, reverse=True)
        return encontrados[:limite] if limite else encontrados

    async def resolver_barrio(
        self, texto: str, *, municipio: str | None = None
    ) -> CandidatoBarrio:
        """Devuelve el barrio único que coincide con el texto.

        Args:
            texto: nombre del barrio, o la clave completa "MUNICIPIO | BARRIO".
            municipio: pista para desempatar cuando el nombre se repite. Hay
                barrios homónimos en varios municipios (LAS MALVINAS está en
                Barranquilla y en Campo de la Cruz).

        Raises:
            BarrioNoEncontrado: si no coincide ninguno.
            BarrioAmbiguo: si quedan varios y no hay con qué desempatar.
        """
        # Clave completa: no hay nada que adivinar.
        if " | " in texto:
            objetivo = norm_dato(texto)
            for i, bkey in enumerate(self.datos.barrios):
                if norm_dato(bkey) == objetivo:
                    return self._candidato(i)

            # La clave no existe tal cual. Pasa siempre por lo mismo: el chat ve
            # "BARRANQUILLA | OLAYA" en los filtros activos, copia ese formato y
            # pide "BARRANQUILLA | EL CONCORD" aunque El Concord sea de Malambo.
            # Se reintenta con las dos mitades por separado, porque seguir de
            # largo buscaba la clave ENTERA entre los nombres de barrio —donde el
            # municipio nunca aparece— y siempre daba "no encontrado".
            muni_texto, _, barrio_texto = texto.partition(" | ")
            return await self.resolver_barrio(
                barrio_texto, municipio=municipio or muni_texto
            )

        candidatos = await self.buscar_barrios(texto)
        if not candidatos:
            raise BarrioNoEncontrado(texto)

        # El municipio acota ANTES de preferir coincidencias exactas. Al revés,
        # "Los Robles" mirando Soledad se quedaba con el homónimo de Sabanalarga
        # y descartaba las diez etapas que el usuario sí tenía en pantalla.
        if municipio:
            del_municipio = [
                c for c in candidatos if norm_dato(c.municipio) == norm_dato(municipio)
            ]
            if del_municipio:
                candidatos = del_municipio

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
        bkeys: Sequence[str] | None = None,
        municipio: str | None = None,
        zona: str | None = None,
        meses: Sequence[str] | None = None,
        tipo_os: str | None = None,
        brigada: str | None = None,
        etiqueta: str | None = None,
    ) -> Efectividad:
        """Efectividad del recorte indicado. Sin filtros, de todo el histórico.

        `bkeys` admite varios barrios porque un nombre como "Los Robles" puede
        corresponder a diez etapas que son un mismo sitio para quien pregunta.
        """
        conteos = self._agrupar(
            bkeys=bkeys, municipio=municipio, zona=zona, meses=meses,
            tipo_os=tipo_os, brigada=brigada,
        )
        nombre = etiqueta or self._etiqueta(bkeys, municipio, zona)
        return _a_dto(conteos.get(0, Conteo()), nombre)

    async def ranking(
        self,
        *,
        dimension: str = "brigada",
        bkeys: Sequence[str] | None = None,
        municipio: str | None = None,
        meses: Sequence[str] | None = None,
        brigada: str | None = None,
        min_ordenes: int = 10,
        limite: int = 10,
        ascendente: bool = False,
        ordenar_por: str = "ef_adj",
    ) -> list[Efectividad]:
        """Ordena brigadas, técnicos o barrios por el criterio pedido.

        Por defecto la efectividad ajustada, que es como el tablero ordena estos
        rankings (Dock.js:295): comparar por la cruda castiga a quien recibe más
        órdenes con causas fuera de su control.

        Pero no toda pregunta se contesta con efectividad. «Dónde pierdo más» se
        ordena por órdenes perdidas, que son las que no se cobran; responderla con
        efectividad devuelve barrios sin una sola pérdida.
        """
        if dimension not in ("brigada", "tecnico", "barrio"):
            raise ValueError(f"Dimensión no soportada: {dimension}")
        if ordenar_por not in CRITERIOS:
            raise ValueError(f"Criterio no soportado: {ordenar_por}")

        p = self.datos
        conteos = self._agrupar(
            bkeys=bkeys, municipio=municipio, meses=meses, brigada=brigada, por=dimension
        )
        catalogo = {"brigada": p.brigs, "tecnico": p.tecs, "barrio": p.barrios}[dimension]

        # Las medias salen de TODO el recorte, incluidos los grupos que luego
        # descarta `min_ordenes`: son la referencia contra la que se compara cada
        # uno, y calcularlas solo sobre los que pasan el corte las sesgaría.
        tot_global = sum(c.tot for c in conteos.values())
        ef_global = sum(c.ef for c in conteos.values())
        den_global = sum(c.tot - c.noctrl for c in conteos.values())
        media = ef_global / tot_global if tot_global else 0.0
        media_adj = ef_global / den_global if den_global > 0 else 0.0

        filas = []
        for i, c in conteos.items():
            if c.tot < min_ordenes:
                continue
            fila = _a_dto(c, catalogo[i])
            fila.ef_pond = _ponderar(c.ef, c.tot, media)
            fila.ef_adj_pond = _ponderar(c.ef, c.tot - c.noctrl, media_adj)
            filas.append(fila)

        filas.sort(key=CRITERIOS[ordenar_por], reverse=not ascendente)
        return filas[:limite]

    async def causas(
        self,
        *,
        bkeys: Sequence[str] | None = None,
        municipio: str | None = None,
        meses: Sequence[str] | None = None,
        brigada: str | None = None,
        limite: int = 6,
    ) -> list[FilaCausa]:
        """Causas de las órdenes NO efectivas, de mayor a menor."""
        p = self.datos
        conteo = self._agrupar(
            bkeys=bkeys, municipio=municipio, meses=meses, brigada=brigada
        ).get(0, Conteo())

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

    async def buscar_en_observaciones(
        self,
        *,
        texto: str,
        bkeys: Sequence[str] | None = None,
        municipio: str | None = None,
        zona: str | None = None,
        meses: Sequence[str] | None = None,
        etiqueta: str | None = None,
        limite: int = 10,
        n_ejemplos: int = 3,
    ) -> BusquedaObservaciones:
        """Busca un término dentro del acta de visita y lo agrupa por barrio.

        Es la única consulta que mira texto libre. El resto de métricas salen de
        campos codificados; aquí se lee lo que el técnico escribió a mano, que es
        donde quedan los detalles que no tienen casilla propia —la red chilena,
        el estado del poste, por qué no se pudo sacar la acometida—.

        Cuenta MENCIONES, no causas: una orden efectiva puede nombrar el término
        igual que una perdida. Por eso devuelve también `tot` por barrio y el
        desglose por estado, para que la cifra no se lea como una tasa de fallo.
        """
        p = self.datos
        objetivo = norm(texto)
        if not objetivo:
            raise ValueError("El término de búsqueda está vacío.")

        nombre = etiqueta or self._etiqueta(bkeys, municipio, zona)

        def vacio(sin_resolver: str | None = None) -> BusquedaObservaciones:
            return BusquedaObservaciones(
                termino=texto, base=nombre, coincidencias=0, revisadas=0, pct=0.0,
                por_estado={}, zonas=[], barrios=[], ejemplos=[],
                sin_resolver=sin_resolver,
            )

        f_barrios = self._indices_barrio(bkeys) if bkeys else None
        f_muni = self._indice(p.munis, municipio)
        f_zona = self._indice(p.zonas, zona)
        f_meses = {p.meses.index(m) for m in meses if m in p.meses} if meses else None
        for pedido, resuelto in (
            (bkeys, f_barrios), (municipio, f_muni), (zona, f_zona),
            (meses, f_meses or None),
        ):
            if pedido is not None and resuelto is None:
                # Se devuelve QUÉ no resolvió: sin eso, el 0 se lee como «ahí no
                # pasa nada» cuando lo que pasó es que el recorte no existe.
                logger.info("Filtro sin coincidencia: %r", pedido)
                return vacio(str(pedido))

        B, E, b_muni, b_zona = p.b, p.e, p.b_muni, p.b_zona
        coincidencias = revisadas = 0
        por_estado = {"Efectiva": 0, "Fallida": 0, "Perdida": 0}
        menciones: dict[int, int] = {}
        tot_barrio: dict[int, int] = {}
        menciones_zona: dict[int, int] = {}
        tot_zona: dict[int, int] = {}
        ejemplos_en: dict[str, list[int]] = {}
        sin_texto: list[str] = []

        for m, mes_key in enumerate(p.meses):
            if f_meses is not None and m not in f_meses:
                continue
            inicio = p.inicio_mes[m]
            fin = p.inicio_mes[m + 1] if m + 1 < len(p.inicio_mes) else len(p)
            obs = obtener_observaciones(self.directorio, mes_key)
            # Sin la misma cantidad de actas que de órdenes no se sabe a cuál
            # pertenece cada texto, y un conteo desalineado es peor que ninguno.
            if len(obs) != fin - inicio:
                if obs:
                    logger.warning(
                        "El mes %s trae %s actas para %s órdenes; se omite.",
                        mes_key, len(obs), fin - inicio,
                    )
                sin_texto.append(mes_key)
                continue

            for local in range(fin - inicio):
                i = inicio + local
                bi = B[i]
                if f_barrios is not None and bi not in f_barrios:
                    continue
                if f_muni is not None and b_muni[bi] != f_muni:
                    continue
                zi = b_zona[bi]
                if f_zona is not None and zi != f_zona:
                    continue
                revisadas += 1
                tot_barrio[bi] = tot_barrio.get(bi, 0) + 1
                tot_zona[zi] = tot_zona.get(zi, 0) + 1
                if objetivo not in obs[local]:
                    continue
                coincidencias += 1
                por_estado[("Efectiva", "Fallida", "Perdida")[E[i]]] += 1
                menciones[bi] = menciones.get(bi, 0) + 1
                menciones_zona[zi] = menciones_zona.get(zi, 0) + 1
                if sum(len(v) for v in ejemplos_en.values()) < n_ejemplos:
                    ejemplos_en.setdefault(mes_key, []).append(local)

        filas = [
            FilaMencion(
                barrio=p.barrios[bi],
                municipio=p.munis[b_muni[bi]],
                zona=p.zonas[b_zona[bi]],
                n=n,
                tot=tot_barrio[bi],
                pct=_pct(n, tot_barrio[bi]),
            )
            for bi, n in sorted(menciones.items(), key=lambda kv: kv[1], reverse=True)
        ]

        filas_zona = [
            FilaZona(
                zona=p.zonas[zi], n=n, tot=tot_zona[zi], pct=_pct(n, tot_zona[zi])
            )
            for zi, n in sorted(menciones_zona.items(), key=lambda kv: kv[1], reverse=True)
        ]

        ejemplos = [
            acta
            for mes_key, posiciones in ejemplos_en.items()
            for acta in leer_actas(self.directorio, mes_key, posiciones).values()
        ]

        return BusquedaObservaciones(
            termino=texto,
            base=nombre,
            coincidencias=coincidencias,
            revisadas=revisadas,
            pct=_pct(coincidencias, revisadas),
            por_estado={k: v for k, v in por_estado.items() if v},
            zonas=filas_zona,
            barrios=filas[:limite],
            ejemplos=ejemplos,
            meses_sin_texto=sin_texto,
        )

    async def meses_disponibles(self) -> list[str]:
        """Meses con datos, del más reciente al más antiguo."""
        return list(reversed(self.datos.meses))

    # --- Interno --------------------------------------------------------------

    @staticmethod
    def _etiqueta(
        bkeys: Sequence[str] | None, municipio: str | None, zona: str | None
    ) -> str:
        """Nombre legible del recorte, para que la cifra nunca viaje sin su alcance."""
        if bkeys:
            if len(bkeys) == 1:
                return bkeys[0]
            comun = bkeys[0].partition(" | ")[0]
            return f"{len(bkeys)} barrios de {comun}"
        return municipio or zona or "Todo el Atlántico"

    def _candidato(self, i: int) -> CandidatoBarrio:
        p = self.datos
        return CandidatoBarrio(
            bkey=p.barrios[i],
            barrio=p.barrios[i].partition(" | ")[2],
            municipio=p.munis[p.b_muni[i]],
            tot=self._totales_por_barrio()[i],
        )

    def _totales_por_barrio(self) -> list[int]:
        if self._totales is None:
            p = self.datos
            totales = [0] * len(p.barrios)
            for bi in p.b:
                totales[bi] += 1
            self._totales = totales
        return self._totales

    def _indice(self, catalogo: list[str], valor: str | None) -> int | None:
        """Traduce un nombre a su índice, tolerando tildes y mayúsculas."""
        if valor is None:
            return None
        objetivo = norm_dato(valor)
        for i, nombre in enumerate(catalogo):
            if norm_dato(nombre) == objetivo:
                return i
        return None

    def _indices_barrio(self, bkeys: Sequence[str]) -> set[int] | None:
        """Índices de los barrios pedidos. `None` si alguno no existe."""
        p = self.datos
        encontrados = set()
        for bkey in bkeys:
            i = self._indice(p.barrios, bkey)
            if i is None:
                return None
            encontrados.add(i)
        return encontrados

    def _agrupar(
        self,
        *,
        bkeys: Sequence[str] | None = None,
        municipio: str | None = None,
        zona: str | None = None,
        meses: Sequence[str] | None = None,
        tipo_os: str | None = None,
        brigada: str | None = None,
        por: str | None = None,
    ) -> dict[int, Conteo]:
        """Recorre las órdenes una vez, filtrando y acumulando.

        Sin `por`, todo cae en la clave 0. El criterio de no controlable es el
        mismo de page.js:474: `estado != Efectiva AND causa no controlable`.
        """
        p = self.datos

        f_barrios = self._indices_barrio(bkeys) if bkeys else None
        f_muni = self._indice(p.munis, municipio)
        f_zona = self._indice(p.zonas, zona)
        f_tipo = self._indice(p.tipos, tipo_os)
        f_brig = self._indice(p.brigs, brigada)
        # Conjunto y no índice: «todo 2026» son varios meses, no uno. Queda en
        # None si no se pidió ninguno, y vacío si ninguno de los pedidos existe
        # —que no es lo mismo y abajo se distinguen.
        f_meses = {p.meses.index(m) for m in meses if m in p.meses} if meses else None

        # Un filtro que no resuelve a nada devolvería el total sin filtrar, que es
        # peor que devolver vacío: el usuario creería que la cifra es de su barrio.
        for pedido, resuelto in (
            (bkeys, f_barrios), (municipio, f_muni), (zona, f_zona),
            (tipo_os, f_tipo), (brigada, f_brig), (meses, f_meses or None),
        ):
            if pedido is not None and resuelto is None:
                logger.info("Filtro sin coincidencia: %r", pedido)
                return {}

        B, C, E, MES, O, G = p.b, p.c, p.e, p.mes, p.o, p.g
        ctrl, b_muni, b_zona = p.causa_ctrl, p.b_muni, p.b_zona
        grupo = {"brigada": p.g, "tecnico": p.t, "barrio": p.b}.get(por)

        conteos: dict[int, Conteo] = {}
        for i in range(len(E)):
            bi = B[i]
            if f_barrios is not None and bi not in f_barrios:
                continue
            if f_muni is not None and b_muni[bi] != f_muni:
                continue
            if f_zona is not None and b_zona[bi] != f_zona:
                continue
            if f_meses is not None and MES[i] not in f_meses:
                continue
            if f_tipo is not None and O[i] != f_tipo:
                continue
            if f_brig is not None and G[i] != f_brig:
                continue

            clave = grupo[i] if grupo is not None else 0
            conteo = conteos.get(clave)
            if conteo is None:
                conteo = conteos[clave] = Conteo()

            estado, causa = E[i], C[i]
            conteo.sumar(estado, causa, estado != 0 and ctrl[causa] == 0)

        return conteos
