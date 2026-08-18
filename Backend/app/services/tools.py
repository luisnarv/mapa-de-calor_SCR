"""Herramientas que el modelo puede invocar (tool calling).

El modelo NO escribe SQL. Elige una de estas funciones y sus argumentos; el
backend ejecuta la consulta con las reglas del ETL y le devuelve JSON para que
redacte la respuesta. Además de datos, una herramienta puede devolver una
`FiltroMapa`, que el frontend aplica al tablero.
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.chat import VistaTablero
from app.schemas.metrics import CandidatoBarrio, FiltroMapa
from app.core.taxonomy import norm_dato
from app.services.metrics_service import (
    BarrioAmbiguo,
    BarrioNoEncontrado,
    MetricsService,
)

logger = logging.getLogger(__name__)

_BARRIO = {
    "type": "string",
    "description": (
        "Barrio a consultar. Si en los filtros activos de su pantalla hay uno, pasa "
        "esa clave completa tal cual ('MUNICIPIO | BARRIO'): hay barrios homónimos "
        "en varios municipios y la clave evita la ambigüedad. Si el usuario nombra "
        "otro barrio, pasa su nombre y se resuelve solo."
    ),
}
_MES = {
    "type": "string",
    "description": "Mes en formato YYYY-MM. Si se omite, se usa todo el histórico.",
}
_MUNICIPIO = {"type": "string", "description": "Nombre del municipio."}
_BRIGADA = {
    "type": "string",
    "description": (
        "Tipo de brigada, p. ej. 'Brigada Tipo Pesada' o 'Brigada Tipo Liviana'. "
        "Pásalo siempre que el usuario nombre una: sin él la cifra sale de todas."
    ),
}
_TIPO_OS = {"type": "string", "description": "Tipo de orden de servicio."}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "efectividad",
            "description": (
                "Efectividad de un barrio, municipio o del total. Devuelve la cruda y "
                "la ajustada, más el desglose de efectivas, fallidas y perdidas. "
                "Úsala para '¿qué efectividad tiene X?'. Devuelve además `base`: "
                "el recorte exacto sobre el que se calculó, que debes citar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barrio": _BARRIO, "municipio": _MUNICIPIO, "mes": _MES,
                    "brigada": _BRIGADA, "tipo_os": _TIPO_OS,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ranking",
            "description": (
                "Ordena brigadas, técnicos o barrios por efectividad ajustada. "
                "Úsala para '¿qué brigada funciona mejor en X?' o "
                "'¿cuáles son los peores barrios?'. Si en el barrio pedido no hay "
                "muestra suficiente, amplía solo al municipio y lo avisa en `ampliado`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["brigada", "tecnico", "barrio"],
                        "description": "Qué se compara.",
                    },
                    "barrio": _BARRIO,
                    "municipio": _MUNICIPIO,
                    "mes": _MES,
                    "brigada": _BRIGADA,
                    "ordenar_por": {
                        "type": "string",
                        "enum": [
                            "ef_adj", "ef_pct", "perdidas", "pct_perdidas",
                            "fallidas", "pct_fallidas", "volumen",
                        ],
                        "description": (
                            "Criterio de orden. Por defecto ef_adj. Usa 'perdidas' "
                            "cuando pregunten dónde se pierde más: son las órdenes "
                            "que NO se cobran. 'fallidas' son las que no se "
                            "ejecutaron pero sí se pagan, que es otra cosa."
                        ),
                    },
                    "peores": {
                        "type": "boolean",
                        "description": (
                            "true para los peores. Con efectividad son los de menor "
                            "valor; con perdidas o fallidas, los de mayor. Por "
                            "defecto, los mejores."
                        ),
                    },
                    "min_ordenes": {
                        "type": "integer",
                        "description": "Mínimo de órdenes para entrar al ranking. Por defecto 10.",
                    },
                },
                "required": ["dimension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "causas_no_efectivas",
            "description": (
                "Causas de las órdenes que no fueron efectivas, de mayor a menor, "
                "indicando si cada una es controlable por la operación."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barrio": _BARRIO, "municipio": _MUNICIPIO, "mes": _MES,
                    "brigada": _BRIGADA,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_barrio",
            "description": (
                "Busca barrios por nombre parcial. Úsala cuando el usuario mencione "
                "un barrio del que no estés seguro, antes de pedir métricas."
            ),
            "parameters": {
                "type": "object",
                "properties": {"texto": {"type": "string"}},
                "required": ["texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "meses_disponibles",
            "description": "Lista los meses con datos, del más reciente al más antiguo.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filtrar_mapa",
            "description": (
                "Aplica un filtro al tablero que el usuario está viendo. No consulta "
                "datos. Úsala en dos situaciones: cuando el usuario pida ver, marcar "
                "o resaltar algo, Y por iniciativa propia cuando tu respuesta "
                "destaque UN resultado concreto (el mejor barrio, la peor brigada), "
                "para dejarlo señalado en el mapa. Si vas a dar una lista o hablar "
                "en general, no la uses: mover la vista sin que lo pidan molesta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barrio": _BARRIO,
                    "municipio": _MUNICIPIO,
                    "zona": {"type": "string"},
                    "brigada": {"type": "string"},
                    "tipo_os": {"type": "string"},
                    "meses": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
]


NOMBRES_DE_HERRAMIENTAS = frozenset(t["function"]["name"] for t in TOOLS)

# Criterios donde un valor alto es malo, no bueno.
MAYOR_ES_PEOR = frozenset({"perdidas", "pct_perdidas", "fallidas", "pct_fallidas"})


class ToolRunner:
    """Ejecuta las herramientas contra la base y acumula el filtro para el mapa."""

    def __init__(self, metrics: MetricsService, vista: VistaTablero | None = None) -> None:
        self.metrics = metrics
        self.vista = vista

    def _pista_municipio(self) -> str | None:
        """Municipio de la pantalla, para desempatar barrios homónimos."""
        if not self.vista:
            return None
        if self.vista.municipio:
            return self.vista.municipio
        return self.vista.barrio.partition(" | ")[0] if self.vista.barrio else None

    async def _resolver(self, barrio: str) -> CandidatoBarrio:
        return await self.metrics.resolver_barrio(barrio, municipio=self._pista_municipio())

    async def _grupo(self, barrio: str) -> list[CandidatoBarrio]:
        """Los barrios que corresponden al texto: uno, o varios de un municipio.

        «Los Robles de Soledad» son diez etapas distintas en el catálogo pero un
        solo sitio para quien pregunta. Cuando todos los homónimos caen en el
        mismo municipio se devuelven juntos y la métrica los suma; devolver un
        menú de diez sería no responder.
        """
        try:
            return [await self._resolver(barrio)]
        except BarrioAmbiguo as ambiguo:
            candidatos = ambiguo.candidatos
            pista = self._pista_municipio()
            if pista:
                del_municipio = [
                    c for c in candidatos if norm_dato(c.municipio) == norm_dato(pista)
                ]
                if del_municipio:
                    return del_municipio
            if len({c.municipio for c in candidatos}) == 1:
                return candidatos
            raise  # Municipios distintos: aquí sí hay que preguntar.

    async def run(self, nombre: str, args: dict[str, Any]) -> tuple[dict[str, Any], FiltroMapa | None]:
        """Devuelve (resultado para el modelo, filtro para el mapa o None).

        Nunca lanza: los errores se devuelven como datos para que el modelo
        pueda explicarlos o repreguntar.
        """
        # Se valida contra la lista publicada, no contra los atributos: si no, un
        # nombre inventado por el modelo alcanzaría cualquier método privado.
        if nombre not in NOMBRES_DE_HERRAMIENTAS:
            return {"error": f"Herramienta desconocida: {nombre}"}, None
        try:
            return await getattr(self, f"_{nombre}")(**args)
        except BarrioNoEncontrado as exc:
            return {
                "error": "barrio_no_encontrado",
                "texto_buscado": exc.texto,
                "sugerencia": "Pide al usuario que confirme el nombre del barrio.",
            }, None
        except BarrioAmbiguo as exc:
            return {
                "error": "barrio_ambiguo",
                "texto_buscado": exc.texto,
                "candidatos": [c.model_dump() for c in exc.candidatos],
                "sugerencia": "Pregunta al usuario cuál de estos barrios quiso decir.",
            }, None
        except (TypeError, ValueError) as exc:
            # Argumentos que el modelo inventó: nombres de parámetro que no
            # existen (TypeError) o valores fuera del enum (ValueError). Vuelven
            # como dato para que reintente, no como excepción que corte el chat.
            logger.warning("Argumentos inválidos para %s: %s", nombre, exc)
            return {"error": f"Argumentos inválidos para {nombre}: {exc}"}, None

    # --- Herramientas ---------------------------------------------------------

    async def _recorte(
        self,
        barrio: str | None,
        municipio: str | None,
        mes: str | None,
        brigada: str | None = None,
    ) -> tuple[list[str] | None, str | None, str, FiltroMapa]:
        """Traduce los argumentos a un recorte concreto y a su descripción.

        La descripción viaja de vuelta al modelo en cada respuesta: es lo que le
        permite decir sobre qué calculó la cifra en vez de dar por hecho que le
        hicieron caso.
        """
        bkeys = None
        if barrio:
            grupo = await self._grupo(barrio)
            bkeys = [c.bkey for c in grupo]
            municipio = municipio or grupo[0].municipio

        if bkeys and len(bkeys) == 1:
            donde = bkeys[0]
        elif bkeys:
            donde = f"{len(bkeys)} barrios de {municipio} con ese nombre"
        else:
            donde = municipio or "todo el Atlántico"

        partes = [donde, mes or "todo el histórico"]
        if brigada:
            partes.append(f"brigada {brigada}")

        filtro = FiltroMapa(
            # El tablero solo marca un barrio a la vez; con varios se encuadra el
            # municipio, que es lo más cercano que se puede mostrar.
            barrio=bkeys[0] if bkeys and len(bkeys) == 1 else None,
            municipio=None if bkeys and len(bkeys) == 1 else municipio,
            brigada=brigada,
            meses=[mes] if mes else None,
        )
        return bkeys, municipio, " · ".join(partes), filtro

    async def _efectividad(
        self,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        brigada: str | None = None,
        tipo_os: str | None = None,
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkeys, municipio, base, filtro = await self._recorte(barrio, municipio, mes, brigada)
        datos = await self.metrics.efectividad(
            bkeys=bkeys,
            municipio=None if bkeys else municipio,
            mes=mes,
            brigada=brigada,
            tipo_os=tipo_os,
            etiqueta=base,
        )

        salida: dict[str, Any] = {
            "base": base,
            "metricas": datos.model_dump(),
            "nota": (
                "ef_pct es la efectividad cruda (la que muestra el mapa en su tooltip) "
                "y ef_adj la ajustada. Di cuál citas y sobre qué base."
            ),
        }
        if bkeys and len(bkeys) > 1:
            # Una sola pasada agrupada, no una consulta por barrio.
            detalle = await self.metrics.ranking(
                dimension="barrio", bkeys=bkeys, mes=mes, brigada=brigada,
                min_ordenes=0, limite=len(bkeys),
            )
            salida["detalle_por_barrio"] = [f.model_dump() for f in detalle]
            salida["nota_agrupacion"] = (
                f"El nombre corresponde a {len(bkeys)} barrios del catálogo y las "
                "cifras están sumadas. Dilo, y ofrece el desglose."
            )
        return salida, filtro

    async def _ranking(
        self,
        dimension: str,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        brigada: str | None = None,
        peores: bool = False,
        min_ordenes: int = 10,
        ordenar_por: str = "ef_adj",
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkeys, municipio, base, filtro = await self._recorte(barrio, municipio, mes, brigada)

        # "Peor" se invierte según el criterio: con efectividad el peor es el de
        # menor valor, pero con pérdidas o fallidas el peor es el que más tiene.
        ascendente = not peores if ordenar_por in MAYOR_ES_PEOR else peores

        async def consultar(en_barrios, en_municipio, minimo):
            return await self.metrics.ranking(
                dimension=dimension, bkeys=en_barrios, municipio=en_municipio,
                mes=mes, brigada=brigada, min_ordenes=minimo,
                ascendente=ascendente, ordenar_por=ordenar_por,
            )

        filas = await consultar(bkeys, None if bkeys else municipio, min_ordenes)

        ampliado = None
        if not filas and bkeys and municipio:
            # Igual que el panel del tablero: si el barrio no da muestra, se sube
            # al municipio. Rendirse sería inútil teniendo el dato al lado.
            filas = await consultar(None, municipio, min_ordenes)
            if filas:
                ampliado = (
                    f"En {base} no hay nadie con {min_ordenes} órdenes o más, "
                    f"así que estos son de todo {municipio}."
                )
                base = f"{municipio} · {mes or 'todo el histórico'}"

        salida = {
            "base": base,
            "dimension": dimension,
            "orden": "peor a mejor" if peores else "mejor a peor",
            "criterio": ordenar_por,
            "min_ordenes": min_ordenes,
            "filas": [f.model_dump() for f in filas],
        }
        if ampliado:
            salida["ampliado"] = ampliado
        elif not filas:
            salida["nota"] = f"Sin resultados en {base} con el mínimo pedido."
        return salida, filtro

    async def _causas_no_efectivas(
        self,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        brigada: str | None = None,
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkeys, municipio, base, filtro = await self._recorte(barrio, municipio, mes, brigada)
        filas = await self.metrics.causas(
            bkeys=bkeys, municipio=None if bkeys else municipio, mes=mes, brigada=brigada
        )
        return {"base": base, "causas": [f.model_dump() for f in filas]}, filtro

    async def _buscar_barrio(self, texto: str) -> tuple[dict[str, Any], None]:
        candidatos = await self.metrics.buscar_barrios(texto)
        mostrados = candidatos[:12]
        salida: dict[str, Any] = {
            "total_encontrados": len(candidatos),
            "candidatos": [c.model_dump() for c in mostrados],
        }
        if len(mostrados) < len(candidatos):
            # Callar el recorte hacía que el usuario no viera su barrio en la lista.
            salida["nota"] = (
                f"Hay {len(candidatos)} en total; se muestran los {len(mostrados)} "
                "de mayor volumen. Dilo si le presentas la lista."
            )
        return salida, None

    async def _meses_disponibles(self) -> tuple[dict[str, Any], None]:
        return {"meses": await self.metrics.meses_disponibles()}, None

    async def _filtrar_mapa(self, **kwargs: Any) -> tuple[dict[str, Any], FiltroMapa]:
        barrio = kwargs.get("barrio")
        if barrio:
            kwargs["barrio"] = (await self._resolver(barrio)).bkey

        filtro = FiltroMapa(**{k: v for k, v in kwargs.items() if k in FiltroMapa.model_fields})
        return {"aplicado": filtro.model_dump(exclude_none=True)}, filtro
