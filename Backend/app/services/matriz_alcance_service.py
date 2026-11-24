"""Consulta de la matriz de recomendación de alcance por NIC.

Solo lee `dbanalitica.matriz_alcance_completa`, que une `recomendacion_aire_scr` con la tabla que
llena cada madrugada `Modelos/Recomendacion Alcance/correr_matriz.py`. Aquí no se calcula nada: el
modelo tarda unos 10 minutos para todos los NICs y no cabe en una petición. El % es el de la
última corrida que terminó: si una falla, se sigue sirviendo el del día anterior.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.matriz_alcance import ConsultaMatrizResponse, RecomendacionNic

# Se lee de la vista y no de la tabla para que «no encontrado» siga significando lo mismo: fuera
# del universo de recomendacion_aire_scr.
_CONSULTA = text(
    "SELECT nic, pct_recomendado "
    "FROM dbanalitica.matriz_alcance_completa WHERE nic = ANY(:nics)"
)


class MatrizAlcanceService:
    async def consultar(self, sesion: AsyncSession, nics: list[str]) -> ConsultaMatrizResponse:
        filas = (await sesion.execute(_CONSULTA, {"nics": nics})).mappings().all()
        por_nic = {str(f["nic"]): f for f in filas}
        encontrados = [por_nic[n] for n in nics if n in por_nic]
        # El campo float convierte el Decimal de Postgres: sin él saldría como texto ("98.5").
        resultados = [
            RecomendacionNic(nic=str(f["nic"]), pct_recomendado=f["pct_recomendado"])
            for f in encontrados
        ]
        return ConsultaMatrizResponse(
            solicitados=len(nics),
            encontrados=len(resultados),
            no_encontrados=[n for n in nics if n not in por_nic],
            resultados=resultados,
        )
