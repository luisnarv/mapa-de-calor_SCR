"""Contrato del feedback del chat: el voto que entra y lo que sale al revisarlo."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

Voto = Literal["util", "inutil"]

# Los mismos cuatro del modal del tablero y del CHECK de la tabla. No son
# intercambiables: cada uno se arregla en un sitio distinto —«el dato está mal»
# es una propuesta para la taxonomía del ETL, los otros tres son del agente—,
# así que un motivo mal puesto manda el diagnóstico al equipo equivocado.
Motivo = Literal["dato_incorrecto", "no_entendio", "filtro_incorrecto", "mal_redactado"]


class VotoRequest(BaseModel):
    voto: Voto | None = Field(description="`null` retira un voto anterior.")
    motivo: Motivo | None = None
    comentario: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def _coherente(self) -> "VotoRequest":
        """Las mismas reglas que la tabla, pero devolviendo un 422 legible.

        Sin esto el choque saldría como un error de integridad de Postgres, que
        no le dice nada a quien está integrando contra la API.
        """
        if self.voto is None and (self.motivo or self.comentario):
            raise ValueError("Retirar el voto no admite motivo ni comentario.")
        if self.motivo and self.voto != "inutil":
            raise ValueError("El motivo solo acompaña a un voto negativo.")
        return self


# --- Lo que se devuelve al revisar --------------------------------------------


class FilaFeedback(BaseModel):
    """Una interacción en la lista de revisión.

    Sin la traza a propósito: son los datos que consultó el modelo y en una lista
    de 50 filas pesarían megabytes. Aquí solo van los nombres de las herramientas
    —suficiente para decidir cuál abrir— y el detalle se pide fila por fila.
    """

    id: UUID
    conversacion_id: UUID
    n_interaccion: int
    pregunta: str
    respuesta: str
    voto: Voto | None
    motivo: Motivo | None
    comentario: str | None
    creado_en: datetime
    calificado_en: datetime | None
    herramientas: list[str]


class DetalleFeedback(FilaFeedback):
    """La interacción completa, con lo que hace falta para diagnosticarla."""

    vista: dict[str, Any] | None
    traza: list[dict[str, Any]]


class ResumenFeedback(BaseModel):
    """Totales del periodo.

    `interacciones` es el denominador: 20 votos negativos sobre 30 respuestas y
    sobre 3.000 son dos diagnósticos opuestos, y sin este número no se distinguen.
    """

    interacciones: int
    calificadas: int
    utiles: int
    inutiles: int
    por_motivo: dict[str, int] = Field(
        default_factory=dict, description="Cuántos negativos de cada motivo."
    )
