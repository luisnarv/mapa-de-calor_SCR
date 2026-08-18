"""Esquemas de las métricas operativas y del filtro que se aplica al mapa."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Efectividad(BaseModel):
    """Métricas de un barrio, brigada o técnico.

    `ef_pct` es la efectividad cruda y `ef_adj` la ajustada (excluye del
    denominador las órdenes no controlables). El tablero usa la cruda en los
    tooltips del mapa y la ajustada para ordenar rankings.
    """

    nombre: str
    municipio: str | None = None
    tot: int
    ef: int
    fa: int
    pe: int
    noctrl: int
    ef_pct: float = Field(description="Efectividad cruda: ef / tot")
    ef_adj: float = Field(description="Efectividad ajustada: ef / (tot - no controlables)")


class FilaCausa(BaseModel):
    causa: str
    familia: str
    n: int
    pct: float
    controlable: bool


class CandidatoBarrio(BaseModel):
    bkey: str
    barrio: str
    municipio: str
    tot: int


class FiltroMapa(BaseModel):
    """Filtro que el frontend aplica al tablero.

    Los valores son **nombres**, no índices: el backend no conoce el orden de
    las dimensiones del payload, así que la traducción la hace el frontend.
    """

    barrio: str | None = Field(default=None, description="BKEY 'MUNICIPIO | BARRIO'")
    municipio: str | None = None
    zona: str | None = None
    brigada: str | None = None
    tipo_os: str | None = None
    meses: list[str] | None = Field(default=None, description="Claves YYYY-MM")
