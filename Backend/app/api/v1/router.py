"""Unifica las rutas de la versión 1 de la API."""

from fastapi import APIRouter

from app.api.v1.endpoints import feedback, openai, ordenes

api_router = APIRouter()
api_router.include_router(openai.router, prefix="/openai", tags=["openai"])
api_router.include_router(ordenes.router, prefix="/ordenes", tags=["ordenes"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
