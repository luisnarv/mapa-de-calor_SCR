"""Cliente del servicio externo "Modelo Propensión de Pago Service".

Ese servicio vive en su propio repositorio, con su propio venv (necesita
`catboost`, que este backend no tiene) y sus propios modelos CatBoost. Aquí
solo se le habla por HTTP, igual que a cualquier otro proveedor externo.

**NIC y CUENTA son el mismo número.** El servicio nace de `historico_balanza`
(facturación) y ahí se llama `cuenta`; el resto de este backend nace de
`historico_mo` (mano de obra) y lo llama `nic`. Verificado contra la base:
206.575 de 206.576 NICs distintos coinciden exacto con una cuenta, con la misma
dirección de cliente en las dos tablas. No hace falta traducir nada: se manda
el NIC tal cual.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class PropensionServiceError(Exception):
    """El servicio no respondió. Nunca deja caído al chat: se atrapa y se
    convierte en un resultado de herramienta, igual que cualquier otro error de
    negocio de `tools.py`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PropensionService:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def consultar(self, nic: str) -> dict[str, Any]:
        """Propensión de pago del cliente de ese NIC (= cuenta), normalizada con
        una clave `encontrado` que el servicio en sí NO manda.

        El servicio real no coincide con su propio README en dos puntos, y esto
        se ajustó contra el servicio corriendo, no contra la documentación:

        * Un cliente sin datos responde **404** con
          `{"error": ..., "mensaje": ...}`, no `200` con `encontrado: false`.
        * Un cliente encontrado no trae ninguna clave `encontrado`: hay que
          inferirlo del código 200.
        """
        try:
            resp = await self._cliente.get(f"/propension/{nic}")
        except httpx.RequestError as exc:
            logger.warning("No se pudo contactar el servicio de propensión: %s", exc)
            raise PropensionServiceError(
                "El servicio de propensión de pago no respondió."
            ) from exc

        if resp.status_code == 404:
            cuerpo = resp.json()
            return {
                "encontrado": False,
                "motivo": cuerpo.get("mensaje") or cuerpo.get("error"),
            }
        if resp.status_code == 503:
            raise PropensionServiceError(
                "El servicio de propensión de pago está en modo degradado "
                "(le faltan los modelos o el universo)."
            )
        resp.raise_for_status()  # cualquier otro fallo (422, 500...) sí es real
        datos = resp.json()
        datos["encontrado"] = True
        return datos


@lru_cache
def get_propension_service() -> PropensionService:
    """Un cliente HTTP por proceso, reutilizando su pool de conexiones."""
    cliente = httpx.AsyncClient(
        base_url=settings.PROPENSION_SERVICE_URL,
        timeout=settings.PROPENSION_TIMEOUT_SECONDS,
    )
    return PropensionService(cliente)
