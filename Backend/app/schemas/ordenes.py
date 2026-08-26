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
