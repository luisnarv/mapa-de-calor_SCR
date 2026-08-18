"""Endpoints de OpenAI. Solo traducen HTTP: la lógica vive en el servicio."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import OpenAIServiceDep, ToolRunnerDep
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.openai_service import OpenAIServiceError

router = APIRouter()


def _sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest, service: OpenAIServiceDep, runner: ToolRunnerDep
) -> ChatResponse:
    """Devuelve la respuesta completa, con los filtros a aplicar en `acciones`."""
    try:
        return await service.chat(request, runner)
    except OpenAIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest, service: OpenAIServiceDep, runner: ToolRunnerDep
) -> StreamingResponse:
    """Igual que `/chat`, pero enviando el resultado por trozos vía SSE.

    Tipos de evento:
      * `{"delta": "..."}`   trozo de texto de la respuesta
      * `{"accion": {...}}`  filtro que el tablero debe aplicar
      * `{"error": "..."}`   falla ocurrida ya empezado el flujo
    El flujo termina siempre con `data: [DONE]`.
    """

    async def event_source() -> AsyncIterator[str]:
        try:
            async for evento in service.stream_chat(request, runner):
                yield _sse(evento)
        except OpenAIServiceError as exc:
            # La cabecera 200 ya salió: el error solo puede viajar dentro del flujo.
            yield _sse({"error": exc.message})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
