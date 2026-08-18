"""Esquemas Pydantic del chat con OpenAI: contrato de entrada y salida."""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str = Field(min_length=1, max_length=32_000)


class VistaTablero(BaseModel):
    """Lo que el usuario tiene en pantalla cuando escribe.

    Sin esto el chat responde sobre todo el histórico mientras la persona mira un
    mes y una zona concretos, y las dos cifras se contradicen sin que nadie
    entienda por qué. Llega en **nombres**, no en índices: el backend no conoce el
    orden de las dimensiones del payload.
    """

    barrio: str | None = Field(default=None, description="BKEY 'MUNICIPIO | BARRIO'")
    municipio: str | None = None
    zona: str | None = None
    brigada: str | None = None
    tipo_os: str | None = None
    meses: list[str] = Field(default_factory=list, description="Claves YYYY-MM")

    def resumen(self) -> str:
        """Descripción legible para el prompt. Cadena vacía si no hay filtros."""
        partes = [
            (etiqueta, valor)
            for etiqueta, valor in (
                ("barrio", self.barrio),
                ("municipio", self.municipio),
                ("zona", self.zona),
                ("brigada", self.brigada),
                ("tipo de orden", self.tipo_os),
                ("meses", ", ".join(self.meses) if self.meses else None),
            )
            if valor
        ]
        return " · ".join(f"{k}: {v}" for k, v in partes)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=50)
    vista: VistaTablero | None = Field(
        default=None, description="Filtros activos en el tablero del usuario."
    )
    cargue: str | None = Field(
        default=None,
        description=(
            "Id del archivo de órdenes que el usuario subió. Se manda en cada turno "
            "porque el backend no guarda la conversación."
        ),
    )
    model: str | None = Field(
        default=None, description="Si se omite, se usa OPENAI_MODEL del entorno."
    )
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=16_000)


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    content: str
    model: str
    finish_reason: str | None = None
    usage: Usage | None = None
    acciones: list[dict] = Field(
        default_factory=list, description="Filtros que el frontend debe aplicar al tablero."
    )
