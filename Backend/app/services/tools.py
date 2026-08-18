"""Herramientas que el modelo puede invocar (tool calling).

El modelo NO escribe SQL. Elige una de estas funciones y sus argumentos; el
backend ejecuta la consulta con las reglas del ETL y le devuelve JSON para que
redacte la respuesta. Además de datos, una herramienta puede devolver una
`FiltroMapa`, que el frontend aplica al tablero.
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.metrics import FiltroMapa
from app.services.metrics_service import (
    BarrioAmbiguo,
    BarrioNoEncontrado,
    MetricsService,
)

logger = logging.getLogger(__name__)

_BARRIO = {
    "type": "string",
    "description": "Nombre del barrio tal como lo escribió el usuario; se resuelve solo.",
}
_MES = {
    "type": "string",
    "description": "Mes en formato YYYY-MM. Si se omite, se usa todo el histórico.",
}
_MUNICIPIO = {"type": "string", "description": "Nombre del municipio."}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "efectividad",
            "description": (
                "Efectividad de un barrio, municipio o del total. Devuelve la cruda y "
                "la ajustada, más el desglose de efectivas, fallidas y perdidas. "
                "Úsala para '¿qué efectividad tiene X?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"barrio": _BARRIO, "municipio": _MUNICIPIO, "mes": _MES},
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
                "'¿cuáles son los peores barrios?'."
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
                    "peores": {
                        "type": "boolean",
                        "description": "true para los de peor desempeño. Por defecto, los mejores.",
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
                "properties": {"barrio": _BARRIO, "municipio": _MUNICIPIO, "mes": _MES},
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
                "datos. Úsala cuando pida ver, marcar o resaltar algo en el mapa."
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


class ToolRunner:
    """Ejecuta las herramientas contra la base y acumula el filtro para el mapa."""

    def __init__(self, metrics: MetricsService) -> None:
        self.metrics = metrics

    async def run(self, nombre: str, args: dict[str, Any]) -> tuple[dict[str, Any], FiltroMapa | None]:
        """Devuelve (resultado para el modelo, filtro para el mapa o None).

        Nunca lanza: los errores se devuelven como datos para que el modelo
        pueda explicarlos o repreguntar.
        """
        try:
            manejador = getattr(self, f"_{nombre}", None)
            if manejador is None:
                return {"error": f"Herramienta desconocida: {nombre}"}, None
            return await manejador(**args)
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
        except TypeError as exc:  # argumentos que el modelo inventó
            logger.warning("Argumentos inválidos para %s: %s", nombre, exc)
            return {"error": f"Argumentos inválidos para {nombre}: {exc}"}, None

    # --- Herramientas ---------------------------------------------------------

    async def _efectividad(
        self, barrio: str | None = None, municipio: str | None = None, mes: str | None = None
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkey = None
        if barrio:
            resuelto = await self.metrics.resolver_barrio(barrio)
            bkey = resuelto.bkey
            municipio = municipio or resuelto.municipio

        datos = await self.metrics.efectividad(bkey=bkey, municipio=municipio, mes=mes)
        filtro = FiltroMapa(
            barrio=bkey,
            municipio=municipio if not bkey else None,
            meses=[mes] if mes else None,
        )
        return {
            "metricas": datos.model_dump(),
            "nota": (
                "ef_pct es la efectividad cruda (la que muestra el mapa en su tooltip) "
                "y ef_adj la ajustada. Di siempre cuál estás citando."
            ),
        }, filtro

    async def _ranking(
        self,
        dimension: str,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        peores: bool = False,
        min_ordenes: int = 10,
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkey = None
        if barrio:
            resuelto = await self.metrics.resolver_barrio(barrio)
            bkey = resuelto.bkey
            municipio = municipio or resuelto.municipio

        filas = await self.metrics.ranking(
            dimension=dimension,
            bkey=bkey,
            municipio=municipio if not bkey else None,
            mes=mes,
            min_ordenes=min_ordenes,
            ascendente=peores,
        )
        filtro = FiltroMapa(
            barrio=bkey,
            municipio=municipio if not bkey else None,
            meses=[mes] if mes else None,
        )
        return {
            "dimension": dimension,
            "orden": "peor a mejor" if peores else "mejor a peor",
            "criterio": "efectividad ajustada (ef_adj)",
            "filas": [f.model_dump() for f in filas],
        }, filtro

    async def _causas_no_efectivas(
        self, barrio: str | None = None, municipio: str | None = None, mes: str | None = None
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkey = None
        if barrio:
            resuelto = await self.metrics.resolver_barrio(barrio)
            bkey = resuelto.bkey
            municipio = municipio or resuelto.municipio

        filas = await self.metrics.causas(
            bkey=bkey, municipio=municipio if not bkey else None, mes=mes
        )
        filtro = FiltroMapa(
            barrio=bkey,
            municipio=municipio if not bkey else None,
            meses=[mes] if mes else None,
        )
        return {"causas": [f.model_dump() for f in filas]}, filtro

    async def _buscar_barrio(self, texto: str) -> tuple[dict[str, Any], None]:
        candidatos = await self.metrics.buscar_barrios(texto)
        return {"candidatos": [c.model_dump() for c in candidatos]}, None

    async def _meses_disponibles(self) -> tuple[dict[str, Any], None]:
        return {"meses": await self.metrics.meses_disponibles()}, None

    async def _filtrar_mapa(self, **kwargs: Any) -> tuple[dict[str, Any], FiltroMapa]:
        barrio = kwargs.get("barrio")
        if barrio:
            kwargs["barrio"] = (await self.metrics.resolver_barrio(barrio)).bkey

        filtro = FiltroMapa(**{k: v for k, v in kwargs.items() if k in FiltroMapa.model_fields})
        return {"aplicado": filtro.model_dump(exclude_none=True)}, filtro
