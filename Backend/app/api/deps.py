"""Dependencias globales que se inyectan en los endpoints."""

import logging
from functools import lru_cache
from typing import Annotated, AsyncIterator

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PROCESOS_VALIDOS, settings
from app.core.database import SessionLocal
from app.services.cargue_store import CargueStore
from app.services.feedback_service import FeedbackService
from app.services.geolocalizacion import Geolocalizador
from app.services.propension_service import PropensionService, get_propension_service
from app.services.metrics_service import MetricsService
from app.services.openai_service import OpenAIService, get_openai_service
from app.services.tools import ToolRunner

logger = logging.getLogger(__name__)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Una sesión por petición; se cierra siempre y revierte si algo falla.

    La usa el endpoint del voto. Las métricas no: salen del payload del ETL.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


def _validar_proceso(proceso: str) -> str:
    if proceso not in PROCESOS_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"Proceso inválido: {proceso!r}. Válidos: {', '.join(PROCESOS_VALIDOS)}",
        )
    return proceso


def get_metrics_service(
    proceso: str = Query("scr", description="Proceso: scr o cobros"),
) -> MetricsService:
    return MetricsService(settings.DATA_DIR / _validar_proceso(proceso))


MetricsDep = Annotated[MetricsService, Depends(get_metrics_service)]


@lru_cache
def get_cargue_store() -> CargueStore:
    """Uno por proceso: los cargues tienen que sobrevivir entre peticiones."""
    return CargueStore(settings.CARGUE_TTL_MINUTOS, settings.CARGUE_MAXIMOS)


CargueStoreDep = Annotated[CargueStore, Depends(get_cargue_store)]


_geolocalizadores: dict[str, Geolocalizador] = {}


def get_geolocalizador(
    proceso: str = Query("scr", description="Proceso: scr o cobros"),
) -> Geolocalizador:
    p = _validar_proceso(proceso)
    if p not in _geolocalizadores:
        _geolocalizadores[p] = Geolocalizador(settings.DATA_DIR / p)
    return _geolocalizadores[p]


GeolocalizadorDep = Annotated[Geolocalizador, Depends(get_geolocalizador)]


def get_tool_runner(
    metrics: MetricsDep, store: CargueStoreDep,
    propension: Annotated[PropensionService, Depends(get_propension_service)],
) -> ToolRunner:
    """Las herramientas que el modelo puede invocar, atadas a la sesión de esta petición."""
    return ToolRunner(metrics, cargues=store, propension=propension)


@lru_cache
def get_feedback_service() -> FeedbackService:
    """Uno por proceso: no guarda estado, solo abre sesiones cuando escribe."""
    return FeedbackService()


FeedbackDep = Annotated[FeedbackService, Depends(get_feedback_service)]

ToolRunnerDep = Annotated[ToolRunner, Depends(get_tool_runner)]


def _get_openai(
    proceso: str = Query("scr", description="Proceso: scr o cobros"),
) -> OpenAIService:
    return get_openai_service(_validar_proceso(proceso))


OpenAIServiceDep = Annotated[OpenAIService, Depends(_get_openai)]
