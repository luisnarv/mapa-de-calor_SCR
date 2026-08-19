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
from app.schemas.chat import ChatRequest, ChatResponse, VistaTablero
from app.services.tools import TOOLS, ToolRunner

logger = logging.getLogger(__name__)

# Colombia no tiene horario de verano, así que un desfase fijo de -5 es exacto
# y evita depender de la zona horaria del servidor (en la nube suele ser UTC).
COLOMBIA = timezone(timedelta(hours=-5))

# Tope de idas y vueltas modelo -> herramienta -> modelo. Sin esto, un modelo
# confundido puede quedarse pidiendo datos en bucle y consumir la cuota.
MAX_RONDAS = 4

# Solo se usa si tras el cierre forzado el modelo sigue sin redactar nada.
AVISO_SIN_CIERRE = "\n\n(No pude cerrar la consulta; intenta con una pregunta mas concreta.)"


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
        if runner is not None:
            # La vista y el cargue llegan en el cuerpo: no se inyectan por DI.
            runner.vista = request.vista
            runner.cargue_id = request.cargue
        # Después de asignarlos: el prompt anuncia el archivo cargado, si lo hay.
        mensajes = self._build_messages(request, runner)

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
                    # `_recorte` arma un FiltroMapa aunque no haya recorte, así que
                    # un ranking de todo el Atlántico devolvía uno con todos los
                    # campos vacíos. Al llegar al tablero no filtraba nada pero sí
                    # le cambiaba la pestaña al usuario, que es peor que no hacer
                    # nada: parece que respondió moviéndole la vista porque sí.
                    if filtro is not None and filtro.model_dump(exclude_none=True):
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

            # Agotadas las rondas, se pide una ultima respuesta SIN herramientas.
            #
            # Antes se cortaba aqui con «no pude cerrar la consulta» aunque el
            # modelo tuviera ya todos los datos delante: habia consultado cuatro
            # veces y se le negaba la oportunidad de redactar con lo reunido. La
            # misma pregunta contestaba o no segun cuantas consultas se le
            # antojara hacer, que es lo ultimo que el usuario puede adivinar.
            async for evento in self._cerrar(model, mensajes, request, bool(runner)):
                yield evento
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

    async def _cerrar(
        self, model: str, mensajes: list[dict], request: ChatRequest, con_tools: bool
    ) -> AsyncIterator[dict[str, Any]]:
        """Ultima pasada, con tool_choice a none: obliga a responder con texto.

        Se le pasan las herramientas igualmente porque el historial ya contiene
        `tool_calls`; sin declararlas, la API rechaza la conversacion.
        """
        extra = {"tools": TOOLS, "tool_choice": "none"} if con_tools else {}
        texto = ""
        try:
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
                if contenido := chunk.choices[0].delta.content:
                    texto += contenido
                    yield {"delta": contenido}
        except Exception:
            logger.exception("Fallo el cierre tras agotar las rondas.")

        if not texto.strip():
            # Aqui si no hay nada que hacer: ni con los datos delante redacto.
            yield {"delta": AVISO_SIN_CIERRE}

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

    def _build_messages(self, request: ChatRequest, runner: Any = None) -> list[dict]:
        """Antepone el prompt de sistema. El cliente no puede sobrescribirlo."""
        turns = [m.model_dump() for m in request.messages if m.role != "system"]
        sistema = self._prompt_de_sistema(request.vista, runner)
        return [{"role": "system", "content": sistema}, *turns]

    def _prompt_de_sistema(self, vista: VistaTablero | None = None, runner: Any = None) -> str:
        """El prompt configurado, la fecha, lo que el usuario ve y lo que subió."""
        bloques = [
            b
            for b in (
                self._system_prompt,
                self._fecha(),
                self._vista(vista),
                self._cargue(runner),
            )
            if b
        ]
        return "\n\n".join(bloques)

    @staticmethod
    def _cargue(runner: Any) -> str:
        """El archivo de órdenes que subió el usuario, si hay alguno vigente.

        Sin este bloque el modelo no sabe que existe y nunca llama a sus
        herramientas: contestaría con el histórico a una pregunta sobre el
        archivo, que es el peor error posible aquí porque las dos cifras son
        igual de plausibles y nadie notaría el cambiazo.
        """
        guardado = runner.cargue_actual() if runner is not None else None
        if guardado is None:
            return ""
        return (
            f"ARCHIVO CARGADO — «{guardado.archivo}»: "
            f"{len(guardado.cargue.ordenes)} órdenes POR EJECUTAR, ya asignadas a un técnico.\n"
            "Están pendientes: no se han hecho, no tienen resultado y no aparecen en el "
            "histórico ni en el mapa. Para hablar de ellas usa resumen_cargue y "
            "ordenes_cargadas; para lo que ya pasó, las herramientas del histórico.\n"
            "Nunca sumes ni promedies las dos cosas en una misma cifra. Cruzarlas sí "
            "—qué efectividad tiene históricamente el barrio de una orden pendiente—, "
            "siempre que digas cuál es cuál.\n"
            # La regla «no tienes deuda, estrato ni NIC» es del histórico, y sin esta
            # excepción el modelo la aplicaba también al archivo: declinaba con la
            # frase de fuera de alcance preguntas cuyo dato tenía delante.
            "TODA pregunta sobre este archivo está DENTRO de tu alcance, incluidas las "
            "de deuda, tarifa o estrato, NIC, dirección y antigüedad: de estas órdenes "
            "sí tienes esos datos. Nunca respondas a una pregunta sobre este archivo "
            "con la frase de fuera de alcance.\n"
            # Sin el «después de intentarlo», el modelo se acogía a esta salida sin
            # llegar a llamar a ninguna herramienta y daba por imposible lo que sí
            # estaba: una escapatoria fácil se usa siempre.
            "Si después de intentarlo con las herramientas la cuenta que te piden no "
            "sale, dilo así: «eso no lo puedo calcular con lo que tengo», y ofrece lo "
            "más cercano que sí puedas. Nunca lo digas sin haberlo intentado."
        )

    @staticmethod
    def _fecha() -> str:
        """El modelo no tiene reloj.

        Sin esto rellena el año con el de su entrenamiento y responde «agosto de
        2023» estando en 2026. Se calcula por petición, no al arrancar, para que un
        servidor de días no se quede viejo.
        """
        hoy = datetime.now(COLOMBIA).date()
        return (
            f"Hoy es {hoy.isoformat()}. Si el usuario nombra un mes sin año, se "
            f"refiere al del año en curso ({hoy.year}); tradúcelo a YYYY-MM antes "
            f"de llamar a una herramienta. Nunca supongas otro año."
        )

    @staticmethod
    def _vista(vista: VistaTablero | None) -> str:
        """Los filtros que el usuario tiene puestos.

        Sin esto el chat responde sobre todo el histórico mientras la persona mira
        un mes y una zona concretos: las dos cifras se contradicen y el chat pierde
        credibilidad aunque los números estén bien.
        """
        resumen = vista.resumen() if vista else ""
        if not resumen:
            return (
                "El usuario no tiene filtros puestos: está viendo todo el histórico.\n"
                "Al dar una cifra, di siempre sobre qué recorte la calculaste."
            )
        return (
            f"FILTROS ACTIVOS EN SU PANTALLA — {resumen}\n"
            "Úsalos por defecto en cada herramienta, salvo que pida otra cosa "
            "explícitamente. Si él dice «este barrio» o «aquí», se refiere a estos.\n"
            "Al dar una cifra, di siempre sobre qué recorte la calculaste; si por "
            "algún motivo respondes sobre un recorte distinto al de su pantalla, "
            "adviértelo en la misma frase."
        )

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
