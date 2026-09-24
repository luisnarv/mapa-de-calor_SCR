"""Esquemas del cargue de órdenes por ejecutar."""

from typing import Literal

from pydantic import BaseModel, Field


class ResumenCargue(BaseModel):
    """Lo que el frontend necesita saber tras subir un archivo.

    Lleva `leidas` y `sin_tecnico` además de `cargadas` porque el filtro por
    técnico se lleva la mayor parte del archivo: sin esos dos números, quien sube
    10.523 órdenes y ve 721 va a pensar que el cargue falló.
    """

    id: str = Field(description="Identificador del cargue; se manda en cada turno del chat.")
    archivo: str
    cargadas: int = Field(description="Órdenes con técnico asignado, las únicas que quedan.")
    leidas: int = Field(description="Filas con datos que traía el archivo.")
    sin_tecnico: int
    duplicadas: int
    tecnicos: int
    barrios: int
    deuda_total: float


class PuntoOrden(BaseModel):
    """Una orden ubicada en el mapa.

    No lleva el nombre del cliente: es dato personal y este endpoint no pide
    autenticación.
    """

    orden: str
    nic: str
    lat: float
    lon: float
    origen: Literal["nic", "exacta", "cuadra", "via"] = Field(
        description=(
            "De dónde salió la coordenada, de más a menos precisa: 'nic' y "
            "'exacta' son GPS tomado en esa misma puerta; 'cuadra' es otra placa "
            "de la misma cuadra (~32 m); 'via' es la calle correcta (~41 m)."
        )
    )
    tecnico: str
    direccion: str | None = None
    barrio: str | None = None
    municipio: str | None = None
    tipo_os: str | None = None


class OrdenSinUbicar(BaseModel):
    """Una orden que no se pudo situar, para revisarla a mano.

    No lleva coordenada a propósito: antes caía al centro de su barrio, a 357 m
    de mediana de las órdenes reales de allí. Un punto a tres cuadras con
    aspecto de dirección hace que alguien mande una brigada al sitio equivocado.
    """

    orden: str
    nic: str
    tecnico: str
    direccion: str | None = None
    barrio: str | None = None
    municipio: str | None = None


class CandidatoRecomendado(BaseModel):
    """Un técnico o brigada recomendado según efectividad histórica."""

    nombre: str
    efectividad_ajustada: str = Field(description="Efectivas / (total − no controlables), e.g. '87.2%'.")
    efectivas: int
    fallidas: int
    perdidas: int
    ultima_orden: str | None = Field(description="Fecha de la orden más reciente (YYYY-MM-DD).")


class FranjaHoraria(BaseModel):
    """Franja horaria con su efectividad histórica."""

    franja: str = Field(description="Rango horario, e.g. '06:00–08:00'.")
    efectividad: str = Field(description="Efectividad ajustada en esa franja, e.g. '85.3%'.")
    ordenes: int = Field(description="Órdenes ejecutadas en esa franja.")


class MejorHorario(BaseModel):
    """Franjas horarias del barrio ordenadas por efectividad."""

    mejor_dia: str | None = Field(description="Día de la semana con mejor efectividad, o null si no hay datos.")
    franjas: list[FranjaHoraria]


class CausaFrecuente(BaseModel):
    """Causa de fallo frecuente en el barrio."""

    causa: str
    ordenes: int = Field(description="Órdenes no efectivas con esta causa.")
    porcentaje: str = Field(description="Sobre el total de no efectivas, e.g. '40.0%'.")


class HistorialNic(BaseModel):
    """Resumen de visitas históricas a un NIC."""

    total_visitas: int
    efectivas: int
    fallidas: int
    perdidas: int
    efectividad: str = Field(description="Efectividad cruda (efectivas/total), e.g. '60.0%'.")
    ultima_visita: str | None = Field(description="Fecha de la última visita (YYYY-MM-DD).")


class RecomendacionResponse(BaseModel):
    """Técnicos y brigadas recomendados para un NIC."""

    nic: str
    barrio: str
    municipio: str
    tecnicos_recomendados: list[CandidatoRecomendado]
    brigadas_recomendadas: list[CandidatoRecomendado]
    mejor_horario: MejorHorario
    causas_fallo: list[CausaFrecuente]
    historial_nic: HistorialNic


class RecomendacionBatchRequest(BaseModel):
    """Lote de NICs para recomendar."""

    nics: list[str] = Field(description="Lista de NICs a consultar.")


class RecomendacionBatchResponse(BaseModel):
    """Resultado del lote de recomendaciones."""

    resultados: list[RecomendacionResponse]
    no_encontrados: list[str] = Field(description="NICs que no aparecen en el histórico.")


class PuntosCargue(BaseModel):
    """Las órdenes del cargue situadas en el mapa.

    Se resuelve entero en la misma petición: todo sale del índice que genera el
    ETL, sin salir a la red.
    """

    id: str
    total: int = Field(description="Órdenes del cargue.")
    ubicadas: int
    por_origen: dict[str, int]
    puntos: list[PuntoOrden]
    no_ubicadas: list[OrdenSinUbicar] = Field(
        description="Las que no cruzaron con el histórico. Sin punto en el mapa."
    )
