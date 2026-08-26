"""Guarda qué respondió el asistente y qué opinó el usuario.

La escritura tiene dos caminos con reglas distintas a propósito:

* `registrar` corre dentro del turno del chat y **nunca lanza**. El feedback es
  telemetría: una base caída no puede dejar sin respuesta a quien pregunta.
* `aplicar_voto` corre en su propia petición y sí falla hacia el cliente, porque
  ahí el error significa algo (el id no es de esta base, o la fila se purgó).

La lectura (`listar`, `detalle`, `resumen`) es para revisar el feedback: sin ella
la tabla es un pozo donde entra información y nadie la saca.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from datetime import date, datetime, time, timezone
from time import monotonic

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionLocal
from app.models.chat_interaccion import ChatInteraccion
from app.schemas.chat import ChatRequest
from app.schemas.feedback import (
    DetalleFeedback,
    FilaFeedback,
    Motivo,
    ResumenFeedback,
    Voto,
    VotoRequest,
)

logger = logging.getLogger(__name__)

# Cuántos ejemplos se le pasan al modelo y cuánto dura cada uno. Van en el prompt
# de **cada** pregunta de **cada** usuario: subir estos números es subir el costo
# de todas las conversaciones, no solo de las que se parezcan al ejemplo.
LIMITE_EJEMPLOS = 5
LARGO_EJEMPLO = 600

# Los ejemplos cambian cuando alguien aprueba uno, o sea casi nunca. Diez minutos
# de desfase es invisible para quien cura y ahorra una consulta por turno.
TTL_EJEMPLOS_SEGUNDOS = 600


class FeedbackService:
    def __init__(
        self, sesiones: async_sessionmaker[AsyncSession] = SessionLocal
    ) -> None:
        self._ejemplos: list[dict[str, str]] = []
        self._ejemplos_vencen: float = 0.0
        # El registro abre su propia sesión en vez de recibir la de la petición:
        # en el streaming la respuesta HTTP sigue abierta cuando toca guardar, y
        # atarse al ciclo de vida de la dependencia deja la escritura en manos de
        # cuándo decida cerrarla el framework.
        self._sesiones = sesiones

    async def registrar(
        self, request: ChatRequest, fin: dict[str, Any]
    ) -> uuid.UUID | None:
        """Guarda la interacción y devuelve su id. `None` si no se pudo."""
        preguntas = [m.content for m in request.messages if m.role == "user"]
        if not preguntas:
            return None

        valores: dict[str, Any] = {
            "id": uuid.uuid4(),
            "conversacion_id": request.conversacion_id or uuid.uuid4(),
            # La posición sale de contar preguntas, no de un campo que mande el
            # cliente: así no hay dos fuentes que puedan discrepar.
            "n_interaccion": len(preguntas),
            "pregunta": preguntas[-1],
            "respuesta": fin["respuesta"],
            "vista": request.vista.model_dump() if request.vista else None,
            "traza": fin["traza"],
        }

        sentencia = (
            insert(ChatInteraccion)
            .values(**valores)
            # Reintentar la misma pregunta tras un fallo repite la posición. Se
            # reemplaza en vez de perderse contra la unicidad, y se limpia el voto
            # anterior: opinaba sobre una respuesta que ya no es esta.
            .on_conflict_do_update(
                constraint="chat_interaccion_posicion_unica",
                set_={
                    "pregunta": valores["pregunta"],
                    "respuesta": valores["respuesta"],
                    "vista": valores["vista"],
                    "traza": valores["traza"],
                    "voto": None,
                    "motivo": None,
                    "comentario": None,
                    "calificado_en": None,
                },
            )
            .returning(ChatInteraccion.id)
        )

        try:
            async with self._sesiones() as sesion:
                guardado = (await sesion.execute(sentencia)).scalar_one()
                await sesion.commit()
                return guardado
        except Exception:
            logger.exception("No se pudo guardar la interacción del chat.")
            return None

    async def aplicar_voto(
        self, sesion: AsyncSession, interaccion_id: uuid.UUID, voto: VotoRequest
    ) -> bool:
        """Aplica o retira el voto. `False` si ese id no existe."""
        resultado = await sesion.execute(
            update(ChatInteraccion)
            .where(ChatInteraccion.id == interaccion_id)
            .values(
                voto=voto.voto,
                motivo=voto.motivo,
                comentario=voto.comentario or None,
                # La tabla exige que la fecha y el voto existan o falten juntos.
                calificado_en=func.now() if voto.voto else None,
            )
        )
        await sesion.commit()
        return resultado.rowcount > 0

    # --- Lectura --------------------------------------------------------------

    async def listar(
        self,
        sesion: AsyncSession,
        *,
        voto: Voto | None = None,
        motivo: Motivo | None = None,
        desde: date | None = None,
        hasta: date | None = None,
        limite: int = 50,
        desplazamiento: int = 0,
    ) -> list[FilaFeedback]:
        """La cola de revisión, de lo calificado más reciente hacia atrás.

        Ordena por `calificado_en` y no por `creado_en`: alguien puede votar hoy
        una respuesta de hace diez días, y con el otro orden esa queja aparecería
        enterrada al fondo justo cuando acaba de llegar.
        """
        consulta = (
            select(ChatInteraccion)
            .order_by(
                ChatInteraccion.calificado_en.desc().nullslast(),
                ChatInteraccion.creado_en.desc(),
            )
            .limit(limite)
            .offset(desplazamiento)
        )
        consulta = self._filtrar(consulta, voto, motivo, desde, hasta)

        filas = (await sesion.execute(consulta)).scalars().all()
        return [
            FilaFeedback(
                **{c: getattr(f, c) for c in FilaFeedback.model_fields if c != "herramientas"},
                herramientas=[t.get("herramienta", "?") for t in f.traza],
            )
            for f in filas
        ]

    async def detalle(
        self, sesion: AsyncSession, interaccion_id: uuid.UUID
    ) -> DetalleFeedback | None:
        """Una interacción con su traza: con qué se armó la respuesta.

        Es lo que separa «la herramienta calculó mal» de «el modelo redactó mal»,
        que se arreglan en sitios distintos.
        """
        fila = await sesion.get(ChatInteraccion, interaccion_id)
        if fila is None:
            return None
        return DetalleFeedback(
            **{
                c: getattr(fila, c)
                for c in DetalleFeedback.model_fields
                if c != "herramientas"
            },
            herramientas=[t.get("herramienta", "?") for t in fila.traza],
        )

    async def resumen(
        self, sesion: AsyncSession, *, desde: date | None = None, hasta: date | None = None
    ) -> ResumenFeedback:
        """Cuántas respuestas hubo y cuántas salieron mal, en el periodo."""
        totales = (
            await sesion.execute(
                self._filtrar(
                    select(
                        func.count().label("interacciones"),
                        func.count(ChatInteraccion.voto).label("calificadas"),
                        func.count().filter(ChatInteraccion.voto == "util").label("utiles"),
                        func.count().filter(ChatInteraccion.voto == "inutil").label("inutiles"),
                    ),
                    None,
                    None,
                    desde,
                    hasta,
                )
            )
        ).one()

        por_motivo = (
            await sesion.execute(
                self._filtrar(
                    select(ChatInteraccion.motivo, func.count())
                    .where(ChatInteraccion.motivo.is_not(None))
                    .group_by(ChatInteraccion.motivo),
                    None,
                    None,
                    desde,
                    hasta,
                )
            )
        ).all()

        return ResumenFeedback(
            interacciones=totales.interacciones,
            calificadas=totales.calificadas,
            utiles=totales.utiles,
            inutiles=totales.inutiles,
            por_motivo=dict(por_motivo),
        )

    @staticmethod
    def _filtrar(
        consulta: Select,
        voto: Voto | None,
        motivo: Motivo | None,
        desde: date | None,
        hasta: date | None,
    ) -> Select:
        """Los mismos filtros para la lista y para el resumen.

        Las fechas llegan como día suelto y se comparan contra un timestamp con
        zona: `hasta` incluye el día entero, que es lo que espera quien pide
        «del 1 al 15» y si no dejaría fuera todo lo del día 15.
        """
        if voto:
            consulta = consulta.where(ChatInteraccion.voto == voto)
        if motivo:
            consulta = consulta.where(ChatInteraccion.motivo == motivo)
        if desde:
            consulta = consulta.where(
                ChatInteraccion.creado_en >= datetime.combine(desde, time.min, timezone.utc)
            )
        if hasta:
            consulta = consulta.where(
                ChatInteraccion.creado_en <= datetime.combine(hasta, time.max, timezone.utc)
            )
        return consulta

    # --- Ejemplos para el prompt ----------------------------------------------

    async def ejemplos(self) -> list[dict[str, str]]:
        """Las respuestas con pulgar arriba que se le muestran al modelo.

        Se leen en cada turno del chat, así que van con TTL: sin él, cada
        pregunta de cada usuario sumaría una consulta a Postgres antes de poder
        contestar. Y si la base no responde se devuelve lo último que se tenga
        —o nada—: quedarse sin ejemplos empeora las respuestas, pero dejar al
        usuario sin respuesta es peor.
        """
        if monotonic() < self._ejemplos_vencen:
            return self._ejemplos

        consulta = (
            select(ChatInteraccion.pregunta, ChatInteraccion.respuesta)
            .where(ChatInteraccion.voto == "util")
            .order_by(ChatInteraccion.calificado_en.desc())
            .limit(LIMITE_EJEMPLOS)
        )
        try:
            async with self._sesiones() as sesion:
                filas = (await sesion.execute(consulta)).all()
            self._ejemplos = [
                {"pregunta": p, "respuesta": r[:LARGO_EJEMPLO]} for p, r in filas
            ]
            self._ejemplos_vencen = monotonic() + TTL_EJEMPLOS_SEGUNDOS
        except Exception:
            logger.exception("No se pudieron leer los ejemplos; se sigue sin ellos.")
            # Se reintenta al próximo turno, no en bucle contra una base caída.
            self._ejemplos_vencen = monotonic() + TTL_EJEMPLOS_SEGUNDOS
        return self._ejemplos
