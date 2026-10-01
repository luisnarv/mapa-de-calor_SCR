"""Métricas operativas calculadas sobre el payload del ETL.

La agregación es un espejo de `page.js:470-560`: mismos conteos, mismo criterio
de "no controlable", mismas dos efectividades. Al leer los mismos archivos que
el tablero, las cifras del chat no pueden discrepar de las del mapa.

Todo ocurre en memoria: recorrer 177k enteros toma decenas de milisegundos, así
que no hace falta caché ni precalentado.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from app.core.taxonomy import norm, norm_dato
from app.schemas.metrics import (
    BusquedaObservaciones,
    CandidatoBarrio,
    CasoMencion,
    Efectividad,
    FilaCausa,
    FilaMencion,
    FilaZona,
)
from app.schemas.ordenes import (
    CandidatoRecomendado,
    CausaFrecuente,
    FranjaHoraria,
    HistorialNic,
    MejorHorario,
    RecomendacionResponse,
)
from app.services.payload_store import (
    Payload,
    leer_actas,
    leer_nics,
    obtener,
    obtener_indice_nic,
    obtener_observaciones,
)

logger = logging.getLogger(__name__)


class NicNoEncontrado(Exception):
    def __init__(self, nic: str) -> None:
        super().__init__(f"El NIC {nic} no aparece en el histórico.")
        self.nic = nic


class BarrioNoEncontrado(Exception):
    def __init__(self, texto: str) -> None:
        super().__init__(texto)
        self.texto = texto


class BarrioAmbiguo(Exception):
    def __init__(self, texto: str, candidatos: Sequence[CandidatoBarrio]) -> None:
        super().__init__(texto)
        self.texto = texto
        self.candidatos = list(candidatos)


class FiltroNoResuelto(Exception):
    """Un filtro de catálogo (municipio, zona, tarifa...) no resolvió a un
    único valor del payload: o no existe, o encaja en más de uno.

    Antes esto hacía que `_agrupar` devolviera {} en silencio, y el llamador no
    tenía forma de distinguirlo de «de verdad no hay órdenes»: el caso real fue
    `municipio="Atlántico"` —el departamento, no un municipio— que vació TODO el
    cálculo, incluido el filtro de tarifa que sí era válido, y el mensaje que
    volvió le echó la culpa al mínimo de órdenes pedido en vez de al filtro.
    """

    def __init__(self, campo: str, valor: str, catalogo: Sequence[str]) -> None:
        super().__init__(f"{campo}={valor!r} no está en el catálogo")
        self.campo = campo
        self.valor = valor
        self.catalogo = list(catalogo)


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


def _wilson_lower(exitos: int, n: int) -> float:
    """Límite inferior de Wilson al 95%, espejo de page.js:884."""
    if not n:
        return 0.0
    z = 1.96
    p = exitos / n
    d = 1 + (z * z) / n
    c = p + (z * z) / (2 * n)
    m = z * math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))
    return max(0.0, (c - m) / d)


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


def _colapsar(palabra: str) -> str:
    """«enrrejado» -> «enrejado». La letra doblada es la errata más común."""
    salida: list[str] = []
    for letra in palabra:
        if not salida or salida[-1] != letra:
            salida.append(letra)
    return "".join(salida)


def _patron_flexible(texto: str) -> re.Pattern[str] | None:
    """La frase buscada, tolerante a letras repetidas, pero seguida.

    Las actas las escribe el técnico a mano y vienen con erratas; quien pregunta
    también las comete. Antes esto era un `in` literal, así que «predio enrrejado»
    daba cero y el modelo salía a inventar variantes más cortas por su cuenta
    —«reja», «enrejado»— y respondía sobre un término que nadie le pidió.

    Las letras repetidas se colapsan en lo buscado y se admiten en el acta, así
    que «enrrejado» y «enrejado» son la misma búsqueda en cualquier dirección.

    Lo que NO hace es soltar las palabras. Se intentó y fue peor: buscando cada
    una por su lado, «red trenzada neutro» encontraba 12 actas en San Felipe
    donde en realidad hay 2, porque contaba «…tendido red trenzada, se desconecta
    fase y neutro» —las tres palabras sueltas, otra cosa completamente—. Una
    frase se busca como frase; entre palabra y palabra solo se admite el espacio.
    """
    palabras = norm(texto).split()
    if not palabras:
        return None
    return re.compile(
        r"\s+".join(
            "".join(f"{re.escape(letra)}+" for letra in _colapsar(palabra))
            for palabra in palabras
        )
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
        subaccion: str | None = None,
        tarifa: str | None = None,
        actividad: str | None = None,
        etiqueta: str | None = None,
    ) -> Efectividad:
        """Efectividad del recorte indicado. Sin filtros, de todo el histórico.

        `bkeys` admite varios barrios porque un nombre como "Los Robles" puede
        corresponder a diez etapas que son un mismo sitio para quien pregunta.
        """
        conteos = self._agrupar(
            bkeys=bkeys, municipio=municipio, zona=zona, meses=meses,
            tipo_os=tipo_os, brigada=brigada, subaccion=subaccion, tarifa=tarifa,
            actividad=actividad,
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
        subaccion: str | None = None,
        tarifa: str | None = None,
        actividad: str | None = None,
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
        if dimension not in ("brigada", "tecnico", "barrio", "subaccion", "tarifa", "actividad"):
            raise ValueError(f"Dimensión no soportada: {dimension}")
        if ordenar_por not in CRITERIOS:
            raise ValueError(f"Criterio no soportado: {ordenar_por}")

        p = self.datos
        conteos = self._agrupar(
            bkeys=bkeys, municipio=municipio, meses=meses, brigada=brigada,
            subaccion=subaccion, tarifa=tarifa, actividad=actividad,
            por=dimension,
        )
        catalogo = {
            "brigada": p.brigs, "tecnico": p.tecs, "barrio": p.barrios,
            "subaccion": p.subs, "tarifa": p.tarifas, "actividad": p.acts,
        }[dimension]

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
        zona: str | None = None,
        meses: Sequence[str] | None = None,
        brigada: str | None = None,
        subaccion: str | None = None,
        tarifa: str | None = None,
        actividad: str | None = None,
        limite: int = 6,
    ) -> list[FilaCausa]:
        """Causas de las órdenes NO efectivas, de mayor a menor."""
        p = self.datos
        conteo = self._agrupar(
            bkeys=bkeys, municipio=municipio, zona=zona, meses=meses,
            brigada=brigada, subaccion=subaccion, tarifa=tarifa,
            actividad=actividad,
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
        brigada: str | None = None,
        meses: Sequence[str] | None = None,
        etiqueta: str | None = None,
        limite: int = 10,
        # Tope de casos con NIC. Una búsqueda amplia devuelve miles —«enrejado»
        # pasa de 18.000— y ninguna respuesta puede listarlos: se recorta y se
        # avisa cuántos quedaron fuera, para que se filtre por barrio o por mes.
        limite_casos: int = 20,
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
        patron = _patron_flexible(texto)
        if patron is None:
            raise ValueError("El término de búsqueda está vacío.")

        nombre = etiqueta or self._etiqueta(bkeys, municipio, zona)

        def vacio(sin_resolver: str | None = None) -> BusquedaObservaciones:
            return BusquedaObservaciones(
                termino=texto, base=nombre, coincidencias=0, revisadas=0, pct=0.0,
                por_estado={}, zonas=[], barrios=[], casos=[],
                sin_resolver=sin_resolver,
            )

        f_barrios = self._indices_barrio(bkeys) if bkeys else None
        f_muni = self._indice(p.munis, municipio)
        f_zona = self._indice(p.zonas, zona)
        f_brig = self._indice(p.brigs, brigada)
        f_meses = {p.meses.index(m) for m in meses if m in p.meses} if meses else None
        for pedido, resuelto in (
            (bkeys, f_barrios), (municipio, f_muni), (zona, f_zona),
            (brigada, f_brig), (meses, f_meses or None),
        ):
            if pedido is not None and resuelto is None:
                logger.info("Filtro sin coincidencia: %r", pedido)
                return vacio(str(pedido))

        B, E, G, b_muni, b_zona = p.b, p.e, p.g, p.b_muni, p.b_zona
        coincidencias = revisadas = 0
        por_estado = {"Efectiva": 0, "Fallida": 0, "Perdida": 0}
        menciones: dict[int, int] = {}
        tot_barrio: dict[int, int] = {}
        menciones_zona: dict[int, int] = {}
        tot_zona: dict[int, int] = {}
        # mes -> [(posición en el mes, índice global, barrio)]
        casos_en: dict[str, list[tuple[int, int, int]]] = {}
        recogidos = 0
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
                if f_brig is not None and G[i] != f_brig:
                    continue
                revisadas += 1
                tot_barrio[bi] = tot_barrio.get(bi, 0) + 1
                tot_zona[zi] = tot_zona.get(zi, 0) + 1
                if not patron.search(obs[local]):
                    continue
                coincidencias += 1
                por_estado[("Efectiva", "Fallida", "Perdida")[E[i]]] += 1
                menciones[bi] = menciones.get(bi, 0) + 1
                menciones_zona[zi] = menciones_zona.get(zi, 0) + 1
                if recogidos < limite_casos:
                    casos_en.setdefault(mes_key, []).append((local, i, bi))
                    recogidos += 1

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

        # El acta original y el NIC se leen del archivo del mes, solo para estas
        # posiciones: cargarlos enteros costaría memoria en todas las peticiones
        # para enseñar veinte filas.
        casos: list[CasoMencion] = []
        for mes_key, marcas in casos_en.items():
            posiciones = [local for local, _, _ in marcas]
            actas = leer_actas(self.directorio, mes_key, posiciones)
            nics = leer_nics(self.directorio, mes_key, posiciones)
            for local, i, bi in marcas:
                casos.append(
                    CasoMencion(
                        nic=nics.get(local, ""),
                        barrio=p.barrios[bi],
                        estado=("Efectiva", "Fallida", "Perdida")[E[i]],
                        mes=mes_key,
                        acta=actas.get(local, ""),
                    )
                )

        return BusquedaObservaciones(
            termino=texto,
            base=nombre,
            coincidencias=coincidencias,
            revisadas=revisadas,
            pct=_pct(coincidencias, revisadas),
            por_estado={k: v for k, v in por_estado.items() if v},
            zonas=filas_zona,
            barrios=filas[:limite],
            casos=casos,
            meses_sin_texto=sin_texto,
        )

    async def meses_disponibles(self) -> list[str]:
        """Meses con datos, del más reciente al más antiguo."""
        return list(reversed(self.datos.meses))

    # --- Recomendador (espejo de page.js:recommend) ----------------------------

    async def recomendar(self, *, nic: str) -> RecomendacionResponse:
        """Recomienda técnicos y brigadas para un NIC, replicando la lógica
        Wilson del tab Recomendador del frontend."""
        p = self.datos
        indice = obtener_indice_nic(self.directorio)
        b_idx = indice.barrio_de.get(nic)
        if b_idx is None:
            raise NicNoEncontrado(nic)

        bkey = p.barrios[b_idx]
        muni_idx = p.b_muni[b_idx]
        zona_idx = p.b_zona[b_idx]

        tecnicos = self._wilson(b_idx, muni_idx, zona_idx, None, "tec")
        brigadas = self._wilson(b_idx, muni_idx, zona_idx, None, "brig")
        horario = self._mejor_horario(b_idx, None)
        causas = self._causas_fallo(b_idx, None)
        historial = self._historial_nic(nic, indice)

        return RecomendacionResponse(
            nic=nic,
            barrio=bkey,
            municipio=p.munis[muni_idx],
            tecnicos_recomendados=tecnicos,
            brigadas_recomendadas=brigadas,
            mejor_horario=horario,
            causas_fallo=causas,
            historial_nic=historial,
        )

    _TOPE_TECNICOS = 4
    _TOPE_BRIGADAS = 3
    _TOPE_CAUSAS = 5

    def _causas_fallo(self, b_idx: int, f_tipo: int | None) -> list[CausaFrecuente]:
        p = self.datos
        B, O, E, C = p.b, p.o, p.e, p.c
        ctrl = p.causa_ctrl

        conteo: dict[int, int] = {}
        for i in range(len(E)):
            if B[i] != b_idx:
                continue
            if f_tipo is not None and O[i] != f_tipo:
                continue
            if E[i] == 0:
                continue
            conteo[C[i]] = conteo.get(C[i], 0) + 1

        total_no_ef = sum(conteo.values())
        if total_no_ef == 0:
            return []

        ordenadas = sorted(conteo.items(), key=lambda x: x[1], reverse=True)
        return [
            CausaFrecuente(
                causa=p.causas[c_idx],
                ordenes=n,
                porcentaje=f"{round(n / total_no_ef * 100, 1)}%",
            )
            for c_idx, n in ordenadas[:self._TOPE_CAUSAS]
        ]

    def _historial_nic(self, nic: str, indice: object) -> HistorialNic:
        p = self.datos
        posiciones = indice.ordenes_de.get(nic, [])

        total = len(posiciones)
        efectivas = sum(1 for i in posiciones if p.e[i] == 0)
        fallidas = sum(1 for i in posiciones if p.e[i] == 1)
        perdidas = sum(1 for i in posiciones if p.e[i] == 2)

        ef_pct = round(efectivas / total * 100, 1) if total > 0 else 0.0

        ultima: str | None = None
        if posiciones and p.fecha_min:
            d0 = datetime.strptime(p.fecha_min, "%Y-%m-%d")
            max_min = max(p.m[i] for i in posiciones)
            ultima = (d0 + timedelta(minutes=max_min)).strftime("%Y-%m-%d")

        return HistorialNic(
            total_visitas=total,
            efectivas=efectivas,
            fallidas=fallidas,
            perdidas=perdidas,
            efectividad=f"{ef_pct}%",
            ultima_visita=ultima,
        )

    _FRANJAS = [
        (6, 8), (8, 10), (10, 12), (12, 14), (14, 16), (16, 18),
    ]
    _DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

    def _mejor_horario(self, b_idx: int, f_tipo: int | None) -> MejorHorario:
        p = self.datos
        B, O, E, C, M = p.b, p.o, p.e, p.c, p.m
        ctrl = p.causa_ctrl

        if not p.fecha_min:
            return MejorHorario(mejor_dia=None, franjas=[])

        d0 = datetime.strptime(p.fecha_min, "%Y-%m-%d")

        # Acumuladores por franja: [total, efectivas, no_controlables]
        por_franja: dict[tuple[int, int], list[int]] = {f: [0, 0, 0] for f in self._FRANJAS}
        # Acumuladores por día de la semana: [total, efectivas, no_controlables]
        por_dia: dict[int, list[int]] = {d: [0, 0, 0] for d in range(7)}

        for i in range(len(E)):
            if B[i] != b_idx:
                continue
            if f_tipo is not None and O[i] != f_tipo:
                continue

            minutos = M[i]
            hora = (minutos % 1440) // 60
            dia_semana = (d0 + timedelta(minutes=minutos)).weekday()

            for inicio, fin in self._FRANJAS:
                if inicio <= hora < fin:
                    acc = por_franja[(inicio, fin)]
                    acc[0] += 1
                    if E[i] == 0:
                        acc[1] += 1
                    if E[i] != 0 and ctrl[C[i]] == 0:
                        acc[2] += 1
                    break

            acc_dia = por_dia[dia_semana]
            acc_dia[0] += 1
            if E[i] == 0:
                acc_dia[1] += 1
            if E[i] != 0 and ctrl[C[i]] == 0:
                acc_dia[2] += 1

        franjas: list[tuple[float, FranjaHoraria]] = []
        for (inicio, fin), (tot, ef, noctrl) in por_franja.items():
            den = tot - noctrl
            if den < 3:
                continue
            ef_adj = round(ef / den * 100, 1)
            franjas.append((ef_adj, FranjaHoraria(
                franja=f"{inicio:02d}:00–{fin:02d}:00",
                efectividad=f"{ef_adj}%",
                ordenes=tot,
            )))

        franjas.sort(key=lambda t: t[0], reverse=True)

        mejor_dia: str | None = None
        mejor_ef_dia = -1.0
        for d, (tot, ef, noctrl) in por_dia.items():
            den = tot - noctrl
            if den < 3:
                continue
            ef_adj = ef / den * 100
            if ef_adj > mejor_ef_dia:
                mejor_ef_dia = ef_adj
                mejor_dia = self._DIAS[d]

        return MejorHorario(
            mejor_dia=mejor_dia,
            franjas=[f for _, f in franjas],
        )

    def _wilson(
        self,
        b_idx: int,
        muni_idx: int,
        zona_idx: int,
        f_tipo: int | None,
        kind: str,
    ) -> list[CandidatoRecomendado]:
        p = self.datos
        B, T, G, O, E, C, M = p.b, p.t, p.g, p.o, p.e, p.c, p.m
        ctrl, b_muni, b_zona = p.causa_ctrl, p.b_muni, p.b_zona
        n_total = len(E)

        niveles = [
            ("este barrio y este tipo de orden",
             lambda i: B[i] == b_idx and (f_tipo is None or O[i] == f_tipo)),
            ("este barrio (todos los tipos)",
             lambda i: B[i] == b_idx),
            ("este municipio y este tipo de orden",
             lambda i: b_muni[B[i]] == muni_idx and (f_tipo is None or O[i] == f_tipo)),
            ("esta zona y este tipo de orden",
             lambda i: b_zona[B[i]] == zona_idx and (f_tipo is None or O[i] == f_tipo)),
        ]

        catalogo = p.tecs if kind == "tec" else p.brigs
        columna = T if kind == "tec" else G

        for nombre_nivel, test in niveles:
            agg: dict[int, list[int]] = {}
            for i in range(n_total):
                if not test(i):
                    continue
                key = columna[i]
                if key not in agg:
                    agg[key] = [0, 0, 0, 0, 0, -1]  # n, ef, fa, noCtrl, pe, last
                o = agg[key]
                o[0] += 1
                if E[i] == 0:
                    o[1] += 1
                elif E[i] == 1:
                    o[2] += 1
                elif E[i] == 2:
                    o[4] += 1
                if E[i] != 0 and ctrl[C[i]] == 0:
                    o[3] += 1
                if M[i] > o[5]:
                    o[5] = M[i]

            filas: list[CandidatoRecomendado] = []
            for key, (n, ef, fa, noctrl, pe, last_m) in agg.items():
                den = n - noctrl
                if den < 3:
                    continue
                w = _wilson_lower(ef, den)
                ef_adj = round(ef / den * 100, 1) if den else 0.0
                score = round(w * 100, 1)

                ultima: str | None = None
                if last_m >= 0 and p.fecha_min:
                    d0 = datetime.strptime(p.fecha_min, "%Y-%m-%d")
                    ultima = (d0 + timedelta(minutes=last_m)).strftime("%Y-%m-%d")

                filas.append((score, CandidatoRecomendado(
                    nombre=catalogo[key],
                    efectividad_ajustada=f"{ef_adj}%",
                    efectivas=ef,
                    fallidas=fa,
                    perdidas=pe,
                    ultima_orden=ultima,
                )))

            if len(filas) >= 2:
                filas.sort(key=lambda t: t[0], reverse=True)
                tope = self._TOPE_TECNICOS if kind == "tec" else self._TOPE_BRIGADAS
                return [c for _, c in filas[:tope]]

        return []

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

    def _indice_parcial(self, catalogo: list[str], valor: str | None) -> int | None:
        """Como `_indice`, pero acepta el nombre a medias.

        Los catálogos de tarifa y subacción tienen nombres largos que nadie
        escribe enteros: se pregunta por «estrato 3», no por «RESIDENCIAL |
        ESTRATO 3». Se intenta en tres pasadas, de la más estricta a la más laxa:

        1. El nombre completo.
        2. El último tramo, el que va después de la barra. Es lo que hace que
           «estrato 6» encuentre «RESIDENCIAL | ESTRATO 6» y no se quede fuera por
           existir también «ESTRATO 6 EXENTO»: son tramos distintos.
        3. Subcadena, y solo si encaja en uno. Con dos candidatos se devuelve None
           y el modelo tendrá que precisar: adivinar cuál es peor que no responder.
        """
        if valor is None:
            return None
        objetivo = norm_dato(valor)
        if not objetivo:
            return None

        for i, nombre in enumerate(catalogo):
            if norm_dato(nombre) == objetivo:
                return i
        for pasada in (
            lambda n: norm_dato(n.split("|")[-1]) == objetivo,
            lambda n: objetivo in norm_dato(n),
        ):
            candidatos = [i for i, n in enumerate(catalogo) if pasada(n)]
            if len(candidatos) == 1:
                return candidatos[0]
            if candidatos:
                # Dos o más: «comercial» está en NO REGULADO y en NO RESIDENCIAL,
                # y devolver el primero daba 14 órdenes donde hay 12.871.
                return None
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
        subaccion: str | None = None,
        tarifa: str | None = None,
        actividad: str | None = None,
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
        # Parcial: nadie escribe «RESIDENCIAL | ESTRATO 3» ni «RED CHILENA/CONFIG.
        # ESPECIAL» enteros.
        f_sub = self._indice_parcial(p.subs, subaccion)
        f_tarifa = self._indice_parcial(p.tarifas, tarifa)
        f_act = self._indice_parcial(p.acts, actividad) if p.acts else None
        # Conjunto y no índice: «todo 2026» son varios meses, no uno. Queda en
        # None si no se pidió ninguno, y vacío si ninguno de los pedidos existe
        # —que no es lo mismo y abajo se distinguen.
        f_meses = {p.meses.index(m) for m in meses if m in p.meses} if meses else None

        # `bkeys` y `meses` ya llegan resueltos por `_recorte`/`expandir_meses` en
        # el camino normal: si de todos modos no calzan, el total sin filtrar
        # sería peor que vacío —el usuario creería que la cifra es de su barrio—,
        # así que aquí se quedan en el criterio silencioso de siempre.
        for pedido, resuelto in ((bkeys, f_barrios), (meses, f_meses or None)):
            if pedido is not None and resuelto is None:
                logger.info("Filtro sin coincidencia: %r", pedido)
                return {}

        # Estos seis SÍ llegan como texto libre del modelo, sin pasar por nada
        # que los valide antes. Si no resuelven, no se puede seguir con el resto
        # del cálculo como si ese filtro no existiera: hay que decirlo.
        for campo, pedido, resuelto, catalogo in (
            ("municipio", municipio, f_muni, p.munis),
            ("zona", zona, f_zona, p.zonas),
            ("tipo_os", tipo_os, f_tipo, p.tipos),
            ("brigada", brigada, f_brig, p.brigs),
            ("subaccion", subaccion, f_sub, p.subs),
            ("tarifa", tarifa, f_tarifa, p.tarifas),
            ("actividad", actividad, f_act, p.acts),
        ):
            if pedido is not None and resuelto is None:
                raise FiltroNoResuelto(campo, str(pedido), catalogo)

        B, C, E, MES, O, G, S, F = p.b, p.c, p.e, p.mes, p.o, p.g, p.s, p.f
        A = p.a
        ctrl, b_muni, b_zona = p.causa_ctrl, p.b_muni, p.b_zona
        grupo = {
            "brigada": p.g, "tecnico": p.t, "barrio": p.b,
            "subaccion": p.s, "tarifa": p.f, "actividad": A,
        }.get(por)

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
            if f_sub is not None and S[i] != f_sub:
                continue
            if f_tarifa is not None and F[i] != f_tarifa:
                continue
            if f_act is not None and A[i] != f_act:
                continue

            clave = grupo[i] if grupo is not None else 0
            conteo = conteos.get(clave)
            if conteo is None:
                conteo = conteos[clave] = Conteo()

            estado, causa = E[i], C[i]
            conteo.sumar(estado, causa, estado != 0 and ctrl[causa] == 0)

        return conteos
