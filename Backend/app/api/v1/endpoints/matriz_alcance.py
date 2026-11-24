"""Consulta de la matriz de recomendación de alcance. Solo traduce HTTP.

Sin autenticación por decisión explícita (2026-11-13). Solo devuelve el % del alcance recomendado,
pero igual dice qué NICs tienen gestiones de SCR: antes de publicarlo fuera de la red interna hay
que ponerle al menos una clave.
"""

from fastapi import APIRouter, HTTPException, Path

from app.api.deps import DbSession, MatrizAlcanceDep
from app.schemas.matriz_alcance import (
    ConsultaMatrizRequest,
    ConsultaMatrizResponse,
    RecomendacionNic,
)

router = APIRouter()


@router.post("", response_model=ConsultaMatrizResponse)
async def consultar(
    peticion: ConsultaMatrizRequest, sesion: DbSession, matriz: MatrizAlcanceDep
) -> ConsultaMatrizResponse:
    """El % del alcance recomendado para hasta 5.000 NICs a la vez, en el orden pedido."""
    return await matriz.consultar(sesion, peticion.nics)


@router.get("/{nic}", response_model=RecomendacionNic)
async def consultar_uno(
    sesion: DbSession, matriz: MatrizAlcanceDep, nic: str = Path(pattern=r"^\d+$")
) -> RecomendacionNic:
    r = await matriz.consultar(sesion, [nic])
    if not r.resultados:
        raise HTTPException(
            status_code=404,
            detail=f"El NIC {nic} no está en dbanalitica.recomendacion_aire_scr: "
                   "no tiene gestiones de SCR que recomendar.",
        )
    return r.resultados[0]
