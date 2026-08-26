"""Cargue del archivo de órdenes por ejecutar. Solo traduce HTTP."""

from dataclasses import asdict

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.deps import CargueStoreDep, GeolocalizadorDep
from app.core.config import settings
from app.schemas.ordenes import (
    OrdenSinUbicar,
    PuntoOrden,
    PuntosCargue,
    ResumenCargue,
)
from app.services.carga_ordenes import ArchivoInvalido, leer_ordenes
from app.services.payload_store import PayloadNoDisponible

router = APIRouter()


@router.post("/cargar", response_model=ResumenCargue)
async def cargar(store: CargueStoreDep, archivo: UploadFile = File(...)) -> ResumenCargue:
    """Lee un Excel o CSV de órdenes, lo guarda y devuelve su resumen.

    El id que devuelve es lo que el chat necesita para conversar sobre estas
    órdenes: el frontend lo manda en cada turno.
    """
    datos = await archivo.read()
    maximo = settings.CARGUE_MAX_MB * 1024 * 1024
    if len(datos) > maximo:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo pesa más de {settings.CARGUE_MAX_MB} MB.",
        )

    try:
        cargue = leer_ordenes(datos, archivo.filename or "")
    except ArchivoInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not cargue.ordenes:
        # Un archivo válido del que no queda nada útil. Se rechaza en vez de
        # guardar un cargue vacío: así el aviso dice qué pasó y no "0 órdenes".
        raise HTTPException(
            status_code=400,
            detail=(
                f"El archivo trae {cargue.leidas} órdenes, pero ninguna tiene técnico "
                "asignado. Exporta de nuevo incluyendo las ya asignadas."
            ),
        )

    guardado = store.guardar(cargue, archivo.filename or "archivo")
    return ResumenCargue(
        id=guardado.id,
        archivo=guardado.archivo,
        cargadas=len(cargue.ordenes),
        leidas=cargue.leidas,
        sin_tecnico=cargue.descartadas.get("sin_tecnico", 0),
        duplicadas=cargue.descartadas.get("duplicadas", 0),
        tecnicos=len({o.tecnico for o in cargue.ordenes}),
        barrios=len({o.bkey for o in cargue.ordenes if o.bkey}),
        deuda_total=sum(o.deuda or 0.0 for o in cargue.ordenes),
    )


@router.get("/{id_cargue}/puntos", response_model=PuntosCargue)
async def puntos(
    id_cargue: str, store: CargueStoreDep, geo: GeolocalizadorDep
) -> PuntosCargue:
    """Ubica las órdenes del cargue en el mapa.

    Todo sale del índice que genera el ETL desde el GPS del propio histórico, así
    que se resuelve en la misma petición. Lo que no cruza no lleva punto: va en
    `no_ubicadas` para revisarlo a mano.
    """
    guardado = store.obtener(id_cargue)
    if guardado is None:
        raise HTTPException(
            status_code=404,
            detail="Ese cargue no existe o ya caducó. Vuelve a subir el archivo.",
        )

    try:
        estado = geo.ubicar(guardado.cargue.ordenes)
    except PayloadNoDisponible as exc:
        # Sin el payload del ETL no hay con qué cruzar ni una sola dirección.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return PuntosCargue(
        id=id_cargue,
        total=estado.total,
        ubicadas=len(estado.puntos),
        por_origen=estado.por_origen,
        puntos=[PuntoOrden(**asdict(p)) for p in estado.puntos.values()],
        no_ubicadas=[OrdenSinUbicar(**asdict(o)) for o in estado.no_ubicadas],
    )
