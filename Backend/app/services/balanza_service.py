"""Indicadores de cobro por cuenta, leídos de `dbanalitica.historico_balanza`.

Es la única parte del recomendador que va a la base en vez de a los JSON del
ETL: la facturación y el recaudo no están en el payload, y la balanza se carga a
diario, así que un volcado del ETL ya estaría viejo para el "mes actual".

**NIC y cuenta son el mismo número** (ver `propension_service.py`).

Nunca lanza: si la base no responde devuelve `None` y el endpoint entrega la
recomendación sin el bloque de cobro, en vez de quedarse sin respuesta.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionLocal
from app.schemas.ordenes import IndicadoresCobro

logger = logging.getLogger(__name__)

UMBRAL_NO_GESTIONAR = 30_000
VENTANA_PAGOS_MESES = 6

# Una fila por cuenta y periodo (`historico_balanza_uk`). El periodo en curso
# existe antes de facturarse, con `facturado_energia` nulo (en 202610 iban 591.978
# de 898.727 filas así): por eso "último facturado" salta los nulos en vez de
# tomar la fila más reciente. Los pagos se cuentan
# con `con_pago` y el recaudo del mes con `recaudo_energia > 0`, como se pidió:
# no son equivalentes (en 202609 hubo 14.394 filas con `con_pago` y recaudo <= 0).
_SQL = text(
    """
    SELECT cuenta,
           max(periodo) FILTER (WHERE facturado_energia IS NOT NULL) AS ultimo_periodo,
           (array_agg(facturado_energia ORDER BY periodo DESC)
               FILTER (WHERE facturado_energia IS NOT NULL))[1] AS ultimo_facturado,
           count(*) FILTER (WHERE con_pago AND periodo BETWEEN :desde AND :anterior) AS pagos,
           coalesce(bool_or(recaudo_energia > 0) FILTER (WHERE periodo = :anterior), false)
               AS recaudo_anterior,
           coalesce(bool_or(recaudo_energia > 0) FILTER (WHERE periodo = :actual), false)
               AS recaudo_actual
    FROM dbanalitica.historico_balanza
    WHERE cuenta = ANY(:cuentas)
    GROUP BY cuenta
    """
)


def restar_meses(periodo: int, n: int) -> int:
    """`restar_meses(202601, 1) == 202512`."""
    anio, mes = divmod(periodo, 100)
    total = anio * 12 + (mes - 1) - n
    return (total // 12) * 100 + total % 12 + 1


def armar_indicadores(fila: Any) -> IndicadoresCobro:
    facturado: Decimal | None = fila.ultimo_facturado
    # Sin valor facturado no se marca ni en un sentido ni en el otro: la marca
    # tiene que ser una decisión sobre una cifra, no el efecto de un dato faltante.
    marca = (
        None
        if facturado is None
        else "No gestionar" if facturado < UMBRAL_NO_GESTIONAR else "Gestionar"
    )
    return IndicadoresCobro(
        marca=marca,
        ultimo_periodo=fila.ultimo_periodo,
        facturado_ultimo_periodo=float(facturado) if facturado is not None else None,
        pagos_ultimos_6_meses=fila.pagos,
        recaudo_mes_anterior="Sí" if fila.recaudo_anterior else "No",
        recaudo_mes_actual="Sí" if fila.recaudo_actual else "No",
    )


class BalanzaService:
    def __init__(
        self, sesiones: async_sessionmaker[AsyncSession] = SessionLocal
    ) -> None:
        self._sesiones = sesiones

    async def indicadores(
        self, nics: list[str], hoy: date | None = None
    ) -> dict[str, IndicadoresCobro] | None:
        """Indicadores por NIC. Un NIC sin filas en la balanza no aparece en el
        dict; `None` significa que la base no respondió."""
        # `cuenta` es bigint: un NIC no numérico haría fallar el lote entero. Se
        # guarda el texto original para devolverlo tal cual (p. ej. con ceros).
        por_cuenta = {int(n): n for n in nics if n.isdigit()}
        if not por_cuenta:
            return {}

        hoy = hoy or date.today()
        actual = hoy.year * 100 + hoy.month
        # La ventana son los 6 meses cerrados; el mes en curso aún se está
        # recaudando y ya tiene su propio indicador.
        params = {
            "cuentas": list(por_cuenta),
            "actual": actual,
            "anterior": restar_meses(actual, 1),
            "desde": restar_meses(actual, VENTANA_PAGOS_MESES),
        }
        try:
            async with self._sesiones() as sesion:
                filas = (await sesion.execute(_SQL, params)).all()
        except Exception:
            logger.exception("No se pudo consultar historico_balanza.")
            return None

        return {por_cuenta[f.cuenta]: armar_indicadores(f) for f in filas}
