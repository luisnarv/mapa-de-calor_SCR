"""Contrato de la consulta masiva de la matriz de recomendación de alcance."""

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Con 5.000 NICs la consulta tarda unos 2,5 s contra la base real. Más arriba una sola petición
# empieza a acercarse al timeout de nginx; quien tenga más los parte en varias.
MAX_NICS = 5_000


class ConsultaMatrizRequest(BaseModel):
    nics: list[str] = Field(min_length=1, max_length=MAX_NICS)

    @field_validator("nics", mode="before")
    @classmethod
    def _limpiar(cls, v: Any) -> list[str]:
        """Acepta números o texto, quita espacios y repetidos, y conserva el orden pedido."""
        if not isinstance(v, list):
            raise ValueError("`nics` tiene que ser una lista.")
        limpios: list[str] = []
        for x in v:
            s = str(x).strip()
            if not s.isdigit():
                raise ValueError(f"NIC inválido: {x!r}. Debe tener solo dígitos.")
            limpios.append(s)
        return list(dict.fromkeys(limpios))


class RecomendacionNic(BaseModel):
    nic: str
    # Nulo cuando el NIC quedó excluido (p. ej. pagó después de la última suspensión): la matriz
    # no le recomienda ningún alcance.
    pct_recomendado: float | None


class ConsultaMatrizResponse(BaseModel):
    solicitados: int
    encontrados: int
    # No están en dbanalitica.recomendacion_aire_scr: no hay nada que recomendarles.
    no_encontrados: list[str]
    # En el orden pedido. Solo el % del alcance recomendado: el detalle (cuál alcance, otras canastas,
    # propensión, deuda) queda en la vista para quien lo necesite, no en la API.
    resultados: list[RecomendacionNic]
