"""Esquemas Pydantic del chat con OpenAI: contrato de entrada y salida."""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str = Field(min_length=1, max_length=32_000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=50)
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
