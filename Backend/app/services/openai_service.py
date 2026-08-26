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
from app.schemas.chat import ChatRequest, VistaTablero
from app.schemas.metrics import FiltroMapa
from app.services.tools import TOOLS, ToolRunner

logger = logging.getLogger(__name__)

# Colombia no tiene horario de verano, así que un desfase fijo de -5 es exacto
# y evita depender de la zona horaria del servidor (en la nube suele ser UTC).
COLOMBIA = timezone(timedelta(hours=-5))

# Tope de idas y vueltas modelo -> herramienta -> modelo. Sin esto, un modelo
# confundido puede quedarse pidiendo datos en bucle y consumir la cuota.
MAX_RONDAS = 4

# Tope de lo que se guarda del resultado de una herramienta. Un ranking completo
# pesa cientos de kilobytes y la traza es para diagnosticar, no para reconstruir
# el dato: con el principio alcanza para ver qué devolvió.
LIMITE_TRAZA = 20_000

# Solo se usa si tras el cierre forzado el modelo sigue sin redactar nada.
AVISO_SIN_CIERRE = "\n\n(No pude cerrar la consulta; intenta con una pregunta mas concreta.)"


def _para_traza(resultado: dict[str, Any]) -> dict[str, Any]:
    """Recorta un resultado enorme para que quepa en la traza."""
    texto = json.dumps(resultado, ensure_ascii=False, default=str)
    if len(texto) <= LIMITE_TRAZA:
        return resultado
    return {"truncado_de": len(texto), "json": texto[:LIMITE_TRAZA]}


def _unificar_filtros(filtros: list[FiltroMapa]) -> dict[str, Any] | None:
    """Un turno mueve el tablero una sola vez, o no lo mueve.

    Antes cada llamada a una herramienta emitía su filtro en cuanto terminaba, y
    con dos llamadas en un turno —«en junio y agosto»— la segunda pisaba a la
    primera: la respuesta hablaba de dos meses y el mapa enseñaba uno, sin que
    nada avisara. Es el mismo error de los bugs 5 y 6 entrando por otra puerta.

    Cuando los recortes solo se diferencian en el periodo se unen, que es lo que
    el usuario pidió ver. Cuando se diferencian en algo más —dos barrios
    distintos— no hay un tablero que muestre las dos cosas a la vez, así que se
    deja quieto: peor que no moverlo es moverlo a la mitad de la respuesta.
    """
    utiles = [f for f in filtros if f.model_dump(exclude_none=True)]
    if not utiles:
        return None
    if len(utiles) > 1:
        sin_meses = [f.model_dump(exclude={"meses"}) for f in utiles]
        if any(otro != sin_meses[0] for otro in sin_meses[1:]):
            return None

    meses = sorted({m for f in utiles for m in (f.meses or [])})
    return {"tipo": "filtrar_mapa", **utiles[0].model_dump(), "meses": meses or None}


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

    @property
    def modelo_por_defecto(self) -> str:
        """Con cuál se responde si la petición no pide uno."""
        return self._default_model

    async def stream_chat(
        self,
        request: ChatRequest,
        runner: ToolRunner | None = None,
        ejemplos: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Emite `{"delta": ...}` y `{"accion": ...}`, y cierra con `{"fin": ...}`.

        `fin` es interno: lleva la respuesta completa y la traza de herramientas
        para que el endpoint las guarde. No sale a la red tal cual —el endpoint
        lo cambia por el id de la interacción—, porque la traza trae los datos
        que el modelo consultó y el navegador no tiene nada que hacer con ellos.

        Si el turno falla, `fin` no se emite y no se guarda nada: una respuesta
        que nunca llegó a existir no es feedback de nada.
        """
        model = request.model or self._default_model
        partes: list[str] = []
        traza: list[dict[str, Any]] = []
        filtros: list[FiltroMapa] = []

        async for evento in self._conversar(request, runner, model, traza, ejemplos, filtros):
            if "delta" in evento:
                partes.append(evento["delta"])
            yield evento

        # Al final del turno, cuando ya se sabe cuántos recortes hubo. Llega
        # después del texto: el tablero se mueve al cerrar la respuesta, no a
        # media redacción.
        if accion := _unificar_filtros(filtros):
            yield {"accion": accion}

        yield {"fin": {"respuesta": "".join(partes), "traza": traza}}

    async def _conversar(
        self,
        request: ChatRequest,
        runner: ToolRunner | None,
        model: str,
        traza: list[dict[str, Any]],
        ejemplos: list[dict[str, str]] | None = None,
        filtros: list[FiltroMapa] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """El ciclo de tool calling. Anota en `traza` cada herramienta que corre.

        Si se pasa `runner`, el modelo puede consultar la base. Sin él responde
        solo con lo que sabe.
        """
        if runner is not None:
            # La vista y el cargue llegan en el cuerpo: no se inyectan por DI.
            runner.vista = request.vista
            runner.cargue_id = request.cargue
        # Después de asignarlos: el prompt anuncia el archivo cargado, si lo hay.
        mensajes = self._build_messages(request, runner, ejemplos)

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
                    resultado, filtro, args = await self._ejecutar(runner, llamada)
                    traza.append(
                        {
                            "herramienta": llamada["name"],
                            "argumentos": args,
                            "resultado": _para_traza(resultado),
                        }
                    )
                    # `_recorte` arma un FiltroMapa aunque no haya recorte, así que
                    # un ranking de todo el Atlántico devolvía uno con todos los
                    # campos vacíos. Al llegar al tablero no filtraba nada pero sí
                    # le cambiaba la pestaña al usuario, que es peor que no hacer
                    # nada: parece que respondió moviéndole la vista porque sí.
                    if filtro is not None and filtros is not None:
                        filtros.append(filtro)
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
    async def _ejecutar(
        runner: ToolRunner, llamada: dict[str, str]
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        """Ejecuta una herramienta, tolerando argumentos mal formados.

        Devuelve también los argumentos ya parseados: son parte de la traza, y
        volver a parsearlos fuera sería hacer dos veces el mismo trabajo.
        """
        try:
            args = json.loads(llamada["args"] or "{}")
        except json.JSONDecodeError:
            logger.warning("Argumentos no son JSON válido: %r", llamada["args"])
            return {"error": "Los argumentos no eran JSON válido. Reintenta."}, None, {}
        resultado, filtro = await runner.run(llamada["name"], args)
        return resultado, filtro, args

    def _build_messages(
        self,
        request: ChatRequest,
        runner: Any = None,
        ejemplos: list[dict[str, str]] | None = None,
    ) -> list[dict]:
        """Antepone el prompt de sistema. El cliente no puede sobrescribirlo."""
        turns = [m.model_dump() for m in request.messages if m.role != "system"]
        sistema = self._prompt_de_sistema(request.vista, runner, ejemplos)
        return [{"role": "system", "content": sistema}, *turns]

    def _prompt_de_sistema(
        self,
        vista: VistaTablero | None = None,
        runner: Any = None,
        ejemplos: list[dict[str, str]] | None = None,
    ) -> str:
        """El prompt configurado, la fecha, lo que el usuario ve y lo que subió."""
        bloques = [
            b
            for b in (
                self._system_prompt,
                self._fecha(),
                self._vista(vista),
                self._cargue(runner),
                self._ejemplos(ejemplos),
            )
            if b
        ]
        return "\n\n".join(bloques)

    @staticmethod
    def _ejemplos(ejemplos: list[dict[str, str]] | None) -> str:
        """Respuestas que alguien aprobó, para que el modelo copie la forma.

        Va de último, después de las reglas: puesto antes, el modelo tiende a
        imitar el ejemplo por encima de lo que el prompt le pide.

        El aviso sobre las cifras no es decorativo. Un ejemplo guardado trae los
        números de cuando se respondió, y sin la advertencia el modelo los repite
        —con toda seguridad, porque vienen de una respuesta «buena»— en vez de
        volver a calcular. Sería el peor error posible aquí: una cifra vieja y
        una nueva son igual de plausibles y nadie notaría el cambiazo.
        """
        if not ejemplos:
            return ""
        muestras = "\n\n".join(
            f"Pregunta: {e['pregunta']}\nRespuesta: {e['respuesta']}" for e in ejemplos
        )
        return (
            "EJEMPLOS DE RESPUESTAS BIEN CALIFICADAS — copia de ellos el tono, la "
            "estructura y el nivel de detalle, nada más.\n"
            "Las cifras que aparecen son de cuando se respondieron y hoy pueden ser "
            "otras: NUNCA las reutilices ni las cites. Toda cifra que des tiene que "
            "salir de una herramienta llamada en esta conversación.\n"
            "Si la pregunta de ahora no se parece a ninguno, ignóralos.\n\n"
            f"{muestras}"
        )

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
