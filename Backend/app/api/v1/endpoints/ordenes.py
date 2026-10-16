"""Cargue del archivo de órdenes por ejecutar. Solo traduce HTTP."""

from dataclasses import asdict

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.deps import BalanzaDep, CargueStoreDep, GeolocalizadorDep, MetricsDep, MetricsPorActividadDep
from app.core.config import settings
from app.schemas.ordenes import (
    CandidatoRecomendado,
    IndicadoresCobro,
    OrdenSinUbicar,
    PuntoOrden,
    PuntosCargue,
    RecomendacionBatchRequest,
    RecomendacionBatchResponse,
    RecomendacionResponse,
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


def _adaptar_cobros(d: dict, indicadores: dict[str, IndicadoresCobro] | None) -> dict:
    """Pasa la respuesta de SCR al vocabulario de COBROS y le suma la balanza.

    COBROS no recomienda plan: las brigadas de SCR se descartan aquí.
    """
    d["gestores_recomendados"] = d.pop("tecnicos_recomendados")
    d.pop("brigadas_recomendadas")
    for g in d["gestores_recomendados"]:
        g["ultima_gestion"] = g.pop("ultima_orden")

    if indicadores is None:
        d["cobro"] = None
        d["aviso_cobro"] = "historico_balanza no respondió; reintenta para ver los indicadores de cobro."
    elif d["nic"] not in indicadores:
        d["cobro"] = None
        d["aviso_cobro"] = "La cuenta no aparece en historico_balanza."
    else:
        d["cobro"] = indicadores[d["nic"]].model_dump()
    return d


@router.get("/recomendar/{nic}")
async def recomendar(
    nic: str,
    metrics: MetricsPorActividadDep,
    balanza: BalanzaDep,
) -> dict:
    """Recomienda técnicos/gestores para un NIC según desempeño histórico.

    En SCR también brigadas; en COBROS, en vez del plan, los indicadores de
    facturación y recaudo de la balanza.
    """
    from app.services.metrics_service import NicNoEncontrado

    try:
        resultado = await metrics.recomendar(nic=nic)
    except NicNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    d = resultado.model_dump()
    if metrics.directorio.name == "cobros":
        _adaptar_cobros(d, await balanza.indicadores([nic]))
    return d


@router.post("/recomendar")
async def recomendar_batch(
    body: RecomendacionBatchRequest,
    metrics: MetricsPorActividadDep,
    balanza: BalanzaDep,
) -> dict:
    """Lo mismo que `GET /recomendar/{nic}` para un lote de NICs."""
    from app.services.metrics_service import NicNoEncontrado

    resultados: list[dict] = []
    no_encontrados: list[str] = []

    for nic in body.nics:
        try:
            resultados.append((await metrics.recomendar(nic=nic)).model_dump())
        except NicNoEncontrado:
            no_encontrados.append(nic)

    if metrics.directorio.name == "cobros" and resultados:
        # Una sola consulta para todo el lote: una por NIC serían miles de idas
        # a la base en un cargue grande.
        indicadores = await balanza.indicadores([d["nic"] for d in resultados])
        for d in resultados:
            _adaptar_cobros(d, indicadores)

    return {"resultados": resultados, "no_encontrados": no_encontrados}
