"""Endpoints del feedback del chat. Solo traducen HTTP."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession, FeedbackDep
from app.schemas.feedback import (
    DetalleFeedback,
    FilaFeedback,
    Motivo,
    ResumenFeedback,
    Voto,
    VotoRequest,
)

router = APIRouter()


# `/resumen` va declarado antes que `/{interaccion_id}`: FastAPI resuelve en
# orden y con la ruta variable primero intentaría leer «resumen» como UUID.
@router.get("/resumen", response_model=ResumenFeedback)
async def resumen(
    sesion: DbSession,
    feedback: FeedbackDep,
    desde: date | None = None,
    hasta: date | None = None,
) -> ResumenFeedback:
    """Cuántas respuestas hubo y cuántas salieron mal."""
    return await feedback.resumen(sesion, desde=desde, hasta=hasta)


@router.get("", response_model=list[FilaFeedback])
async def listar(
    sesion: DbSession,
    feedback: FeedbackDep,
    voto: Voto | None = None,
    motivo: Motivo | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    desplazamiento: int = Query(default=0, ge=0),
) -> list[FilaFeedback]:
    """La cola de revisión. Sin filtros trae lo último, calificado o no."""
    return await feedback.listar(
        sesion,
        voto=voto,
        motivo=motivo,
        desde=desde,
        hasta=hasta,
        limite=limite,
        desplazamiento=desplazamiento,
    )


@router.get("/{interaccion_id}", response_model=DetalleFeedback)
async def detalle(
    interaccion_id: UUID, sesion: DbSession, feedback: FeedbackDep
) -> DetalleFeedback:
    """Una interacción con su traza: con qué datos se armó esa respuesta."""
    fila = await feedback.detalle(sesion, interaccion_id)
    if fila is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Esa interacción no existe."
        )
    return fila


@router.patch("/{interaccion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def votar(
    interaccion_id: UUID,
    voto: VotoRequest,
    sesion: DbSession,
    feedback: FeedbackDep,
) -> None:
    """Aplica el voto, o lo retira si llega `voto: null`.

    Es PATCH y no POST porque la fila ya existe: la creó el turno del chat. El
    usuario puede cambiar de opinión las veces que quiera sobre la misma.
    """
    if not await feedback.aplicar_voto(sesion, interaccion_id, voto):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esa interacción no existe: revisa el id que devolvió el chat.",
        )
