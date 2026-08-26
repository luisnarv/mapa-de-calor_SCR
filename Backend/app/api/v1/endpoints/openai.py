"""Endpoints de OpenAI. Solo traducen HTTP: la lógica vive en el servicio."""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import FeedbackDep, OpenAIServiceDep, ToolRunnerDep
from app.services.feedback_service import FeedbackService
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.openai_service import OpenAIService, OpenAIServiceError
from app.services.tools import ToolRunner

router = APIRouter()


def _sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


async def _responder(
    service: OpenAIService,
    request: ChatRequest,
    runner: ToolRunner,
    feedback: FeedbackService,
) -> AsyncIterator[dict[str, Any]]:
    """Camino único de las dos rutas: reenvía eventos y guarda al terminar.

    El `fin` que emite el servicio no sale de aquí: se cambia por el id contra el
    que el usuario podrá votar. Guardar en este punto y no antes es lo que hace
    que solo queden registradas las respuestas que de verdad se entregaron.
    """
    # `ejemplos` va cacheado y no lanza: si la base no responde se contesta sin
    # ellos, que es peor respuesta pero respuesta al fin.
    async for evento in service.stream_chat(request, runner, await feedback.ejemplos()):
        if "fin" not in evento:
            yield evento
            continue
        # `registrar` no lanza: si la base falla devuelve None y el turno se
        # entrega igual, solo que sin pulgares.
        interaccion_id = await feedback.registrar(request, evento["fin"])
        if interaccion_id is not None:
            yield {"interaccion_id": str(interaccion_id)}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    service: OpenAIServiceDep,
    runner: ToolRunnerDep,
    feedback: FeedbackDep,
) -> ChatResponse:
    """Devuelve la respuesta completa, con los filtros a aplicar en `acciones`."""
    partes: list[str] = []
    acciones: list[dict] = []
    interaccion_id: str | None = None
    modelo = request.model or ""

    try:
        async for evento in _responder(service, request, runner, feedback):
            if "delta" in evento:
                partes.append(evento["delta"])
            elif "accion" in evento:
                acciones.append(evento["accion"])
            elif "interaccion_id" in evento:
                interaccion_id = evento["interaccion_id"]
    except OpenAIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return ChatResponse(
        content="".join(partes),
        model=modelo or service.modelo_por_defecto,
        acciones=acciones,
        interaccion_id=interaccion_id,
    )


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    service: OpenAIServiceDep,
    runner: ToolRunnerDep,
    feedback: FeedbackDep,
) -> StreamingResponse:
    """Igual que `/chat`, pero enviando el resultado por trozos vía SSE.

    Tipos de evento:
      * `{"delta": "..."}`           trozo de texto de la respuesta
      * `{"accion": {...}}`          filtro que el tablero debe aplicar
      * `{"interaccion_id": "..."}`  contra qué votar; llega al final
      * `{"error": "..."}`           falla ocurrida ya empezado el flujo
    El flujo termina siempre con `data: [DONE]`.
    """

    async def event_source() -> AsyncIterator[str]:
        try:
            async for evento in _responder(service, request, runner, feedback):
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
