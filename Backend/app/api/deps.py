"""Dependencias globales que se inyectan en los endpoints."""

from typing import Annotated, AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.metrics_service import MetricsService
from app.services.openai_service import OpenAIService, get_openai_service
from app.services.tools import ToolRunner


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


def get_tool_runner(metrics: MetricsDep) -> ToolRunner:
    """Las herramientas que el modelo puede invocar, atadas a la sesión de esta petición."""
    return ToolRunner(metrics)


ToolRunnerDep = Annotated[ToolRunner, Depends(get_tool_runner)]
OpenAIServiceDep = Annotated[OpenAIService, Depends(get_openai_service)]
