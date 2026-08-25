"""Esquemas de las métricas operativas y del filtro que se aplica al mapa."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Efectividad(BaseModel):
    """Métricas de un barrio, brigada o técnico.

    `ef_pct` es la efectividad cruda y `ef_adj` la ajustada (excluye del
    denominador las órdenes no controlables). El tablero usa la cruda en los
    tooltips del mapa y la ajustada para ordenar rankings.
    """

    nombre: str
    municipio: str | None = None
    tot: int
    ef: int
    fa: int
    pe: int
    noctrl: int
    ef_pct: float = Field(description="Efectividad cruda: ef / tot")
    ef_adj: float = Field(description="Efectividad ajustada: ef / (tot - no controlables)")
    # Solo se llenan en los rankings: ponderar exige una media del conjunto con
    # la que comparar, y una consulta de un solo sitio no tiene conjunto.
    ef_pond: float | None = Field(
        default=None,
        description="Efectividad ponderada por volumen: acerca a la media a quien tiene poca muestra.",
    )
    ef_adj_pond: float | None = Field(
        default=None,
        description="La ajustada, ponderada por volumen igual que `ef_pond`.",
    )


class FilaCausa(BaseModel):
    causa: str
    familia: str
    n: int
    pct: float
    controlable: bool


class FilaMencion(BaseModel):
    """Un barrio dentro de una búsqueda de texto en las actas.

    `n` son las actas donde apareció el término y `tot` las órdenes que ese
    barrio tiene en el mismo recorte: sin el segundo, un barrio con mucho
    volumen siempre encabeza la lista aunque su tasa sea de las más bajas.
    """

    barrio: str
    municipio: str
    zona: str
    n: int
    tot: int
    pct: float


class FilaZona(BaseModel):
    """Una zona del tablero dentro de una búsqueda de texto.

    Va aparte de los barrios porque «zona» tiene dos sentidos: la gente suele
    decirlo por «sector», pero en el tablero son tres (Atlántico Centro, Norte y
    Sur). Devolviendo las dos agrupaciones, el chat contesta sin tener que
    adivinar cuál quiso decir.
    """

    zona: str
    n: int
    tot: int
    pct: float


class BusquedaObservaciones(BaseModel):
    """Resultado de buscar un término en el texto de las actas de visita.

    Es un conteo de MENCIONES, no una causa: que el técnico escriba el término
    no significa que la orden se haya caído por eso. Sirve para localizar dónde
    se reporta algo, no como métrica oficial.
    """

    termino: str
    base: str = Field(description="Recorte sobre el que se buscó.")
    coincidencias: int
    revisadas: int = Field(description="Actas del recorte que se pudieron leer.")
    pct: float
    por_estado: dict[str, int]
    zonas: list[FilaZona]
    barrios: list[FilaMencion]
    ejemplos: list[str]
    meses_sin_texto: list[str] = Field(
        default_factory=list,
        description="Meses del recorte sin archivo de actas: quedaron fuera del conteo.",
    )
    sin_resolver: str | None = Field(
        default=None,
        description=(
            "El filtro que no correspondió a nada. Si viene lleno, el cero es de "
            "un recorte que no existe, no de un término que no aparece."
        ),
    )


class CandidatoBarrio(BaseModel):
    bkey: str
    barrio: str
    municipio: str
    tot: int


class FiltroMapa(BaseModel):
    """Filtro que el frontend aplica al tablero.

    Los valores son **nombres**, no índices: el backend no conoce el orden de
    las dimensiones del payload, así que la traducción la hace el frontend.
    """

    barrio: str | None = Field(default=None, description="BKEY 'MUNICIPIO | BARRIO'")
    municipio: str | None = None
    zona: str | None = None
    brigada: str | None = None
    tipo_os: str | None = None
    meses: list[str] | None = Field(default=None, description="Claves YYYY-MM")
