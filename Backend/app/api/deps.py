"""Dependencias globales que se inyectan en los endpoints."""

import logging
from functools import lru_cache
from typing import Annotated, AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.cargue_store import CargueStore
from app.services.geolocalizacion import Geolocalizador
from app.services.metrics_service import MetricsService
from app.services.openai_service import OpenAIService, get_openai_service
from app.services.tools import ToolRunner

logger = logging.getLogger(__name__)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Una sesión por petición; se cierra siempre y revierte si algo falla.

    Hoy nadie la usa: las métricas salen del payload del ETL. Queda lista para
    cuando haga falta consultar la base directamente.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_metrics_service() -> MetricsService:
    return MetricsService(settings.DATA_DIR)


MetricsDep = Annotated[MetricsService, Depends(get_metrics_service)]


@lru_cache
def get_cargue_store() -> CargueStore:
    """Uno por proceso: los cargues tienen que sobrevivir entre peticiones."""
    return CargueStore(settings.CARGUE_TTL_MINUTOS, settings.CARGUE_MAXIMOS)


CargueStoreDep = Annotated[CargueStore, Depends(get_cargue_store)]


@lru_cache
def get_geolocalizador() -> Geolocalizador:
    """Uno por proceso: conserva el avance de la geolocalización entre peticiones."""
    return Geolocalizador(settings.DATA_DIR)


GeolocalizadorDep = Annotated[Geolocalizador, Depends(get_geolocalizador)]


def get_tool_runner(metrics: MetricsDep, store: CargueStoreDep) -> ToolRunner:
    """Las herramientas que el modelo puede invocar, atadas a la sesión de esta petición."""
    return ToolRunner(metrics, cargues=store)


ToolRunnerDep = Annotated[ToolRunner, Depends(get_tool_runner)]
OpenAIServiceDep = Annotated[OpenAIService, Depends(get_openai_service)]
