"""Esquemas del cargue de órdenes por ejecutar."""

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
