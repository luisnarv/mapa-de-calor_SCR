"""Descarga del reporte operativo en Excel. Solo traduce HTTP."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from app.services import reporte_excel

router = APIRouter()


@router.get("/{proceso}")
def descargar(proceso: Literal["scr", "cobros"]) -> Response:
    """Responde 202 mientras se genera y 200 con el libro cuando está listo."""
    try:
        archivo = reporte_excel.solicitar(proceso)
    except reporte_excel.ReporteFallido as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if archivo is None:
        return Response(status_code=202)
    return FileResponse(
        archivo,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"Reporte_Operativo_{proceso.upper()}.xlsx",
    )
