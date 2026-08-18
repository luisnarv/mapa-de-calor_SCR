"""Servicio de OpenAI: única puerta de salida hacia la API del proveedor.

Los endpoints no conocen el SDK; hablan con este servicio y reciben DTOs propios.
El modelo puede pedir datos a través de las herramientas de `services.tools`: se
ejecutan aquí, se le devuelven como JSON y con eso redacta la respuesta.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.tools import TOOLS, ToolRunner

logger = logging.getLogger(__name__)

# Colombia no tiene horario de verano, así que un desfase fijo de -5 es exacto
# y evita depender de la zona horaria del servidor (en la nube suele ser UTC).
COLOMBIA = timezone(timedelta(hours=-5))

# Tope de idas y vueltas modelo -> herramienta -> modelo. Sin esto, un modelo
# confundido puede quedarse pidiendo datos en bucle y consumir la cuota.
MAX_RONDAS = 4


class OpenAIServiceError(Exception):
    """Falla al hablar con OpenAI, ya traducida a un estado HTTP."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class OpenAIService:
    def __init__(
        self, client: AsyncOpenAI, default_model: str, system_prompt: str = ""
    ) -> None:
        self._client = client
        self._default_model = default_model
        self._system_prompt = system_prompt

    # --- API pública ---------------------------------------------------------

    async def stream_chat(
        self, request: ChatRequest, runner: ToolRunner | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Emite eventos `{"delta": texto}` y `{"accion": {...}}` hasta terminar.

        Si se pasa `runner`, el modelo puede consultar la base. Sin él responde
        solo con lo que sabe.
        """
        model = request.model or self._default_model
        mensajes = self._build_messages(request)

        try:
            for ronda in range(MAX_RONDAS):
                texto, llamadas = "", {}

                extra = {"tools": TOOLS, "tool_choice": "auto"} if runner else {}
                stream = await self._client.chat.completions.create(
                    model=model,
                    messages=mensajes,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    stream=True,
                    **extra,
                )

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        texto += delta.content
                        yield {"delta": delta.content}
                    for parcial in delta.tool_calls or []:
                        self._acumular(llamadas, parcial)

                # Sin llamadas a herramientas, esta ronda ya fue la respuesta final.
                if not llamadas or runner is None:
                    return

                mensajes.append(self._mensaje_asistente(texto, llamadas))
                for llamada in llamadas.values():
                    resultado, filtro = await self._ejecutar(runner, llamada)
                    if filtro is not None:
                        yield {"accion": {"tipo": "filtrar_mapa", **filtro.model_dump()}}
                    mensajes.append(
                        {
                            "role": "tool",
                            "tool_call_id": llamada["id"],
                            "content": json.dumps(resultado, ensure_ascii=False, default=str),
                        }
                    )
                logger.info(
                    "Ronda %s: %s herramienta(s) ejecutada(s).", ronda + 1, len(llamadas)
                )

            yield {"delta": "\n\n(No pude cerrar la consulta; intenta con una pregunta más concreta.)"}
        except OpenAIServiceError:
            raise
        except Exception as exc:
            raise self._translate(exc) from exc

    async def chat(self, request: ChatRequest, runner: ToolRunner | None = None) -> ChatResponse:
        """Igual que `stream_chat`, pero devolviendo la respuesta completa.

        Reutiliza el mismo camino para que las dos rutas no se desincronicen.
        """
        partes: list[str] = []
        acciones: list[dict[str, Any]] = []

        async for evento in self.stream_chat(request, runner):
            if "delta" in evento:
                partes.append(evento["delta"])
            elif "accion" in evento:
                acciones.append(evento["accion"])

        return ChatResponse(
            content="".join(partes),
            model=request.model or self._default_model,
            acciones=acciones,
        )

    async def close(self) -> None:
        await self._client.close()

    # --- Interno -------------------------------------------------------------

    @staticmethod
    def _acumular(llamadas: dict[int, dict[str, str]], parcial: Any) -> None:
        """Junta los trozos de una tool call, que llegan repartidos en el stream."""
        slot = llamadas.setdefault(parcial.index, {"id": "", "name": "", "args": ""})
        if parcial.id:
            slot["id"] = parcial.id
        if parcial.function and parcial.function.name:
            slot["name"] += parcial.function.name
        if parcial.function and parcial.function.arguments:
            slot["args"] += parcial.function.arguments

    @staticmethod
    def _mensaje_asistente(texto: str, llamadas: dict[int, dict[str, str]]) -> dict[str, Any]:
        """Reconstruye el turno del asistente para que el modelo lo vea en la siguiente ronda."""
        return {
            "role": "assistant",
            "content": texto or None,
            "tool_calls": [
                {
                    "id": ll["id"],
                    "type": "function",
                    "function": {"name": ll["name"], "arguments": ll["args"] or "{}"},
                }
                for ll in llamadas.values()
            ],
        }

    @staticmethod
    async def _ejecutar(runner: ToolRunner, llamada: dict[str, str]) -> tuple[dict[str, Any], Any]:
        """Ejecuta una herramienta, tolerando argumentos mal formados."""
        try:
            args = json.loads(llamada["args"] or "{}")
        except json.JSONDecodeError:
            logger.warning("Argumentos no son JSON válido: %r", llamada["args"])
            return {"error": "Los argumentos no eran JSON válido. Reintenta."}, None
        return await runner.run(llamada["name"], args)

    def _build_messages(self, request: ChatRequest) -> list[dict]:
        """Antepone el prompt de sistema. El cliente no puede sobrescribirlo."""
        turns = [m.model_dump() for m in request.messages if m.role != "system"]
        return [{"role": "system", "content": self._prompt_de_sistema()}, *turns]

    def _prompt_de_sistema(self) -> str:
        """El prompt configurado más la fecha de hoy.

        El modelo no tiene reloj: sin esto rellena el año con el de su
        entrenamiento y responde «agosto de 2023» estando en 2026. Se calcula por
        petición, no al arrancar, para que un servidor de días no se quede viejo.
        """
        hoy = datetime.now(COLOMBIA).date()
        fecha = (
            f"Hoy es {hoy.isoformat()}. Si el usuario nombra un mes sin año, se "
            f"refiere al del año en curso ({hoy.year}); tradúcelo a YYYY-MM antes "
            f"de llamar a una herramienta. Nunca supongas otro año."
        )
        if not self._system_prompt:
            return fecha
        return "\n\n".join((self._system_prompt, fecha))

    @staticmethod
    def _translate(exc: Exception) -> OpenAIServiceError:
        """Convierte los errores del SDK en algo que el cliente pueda entender."""
        if isinstance(exc, RateLimitError):
            return OpenAIServiceError("OpenAI está limitando las peticiones.", 429)
        if isinstance(exc, APITimeoutError):
            return OpenAIServiceError("OpenAI tardó demasiado en responder.", 504)
        if isinstance(exc, APIConnectionError):
            return OpenAIServiceError("No se pudo conectar con OpenAI.", 502)
        if isinstance(exc, APIStatusError):
            # 401/403 aquí casi siempre es la API key: no lo reveles al cliente.
            logger.error("OpenAI respondió %s: %s", exc.status_code, exc.message)
            if exc.status_code in (401, 403):
                return OpenAIServiceError("La API no está bien configurada.", 500)
            return OpenAIServiceError("OpenAI rechazó la petición.", 502)

        logger.exception("Error inesperado llamando a OpenAI")
        return OpenAIServiceError("Error inesperado al consultar el modelo.", 502)


@lru_cache
def get_openai_service() -> OpenAIService:
    """Un solo cliente por proceso: reutiliza el pool de conexiones HTTP."""
    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.OPENAI_TIMEOUT_SECONDS,
        max_retries=settings.OPENAI_MAX_RETRIES,
    )
    return OpenAIService(client, settings.OPENAI_MODEL, settings.OPENAI_SYSTEM_PROMPT)
