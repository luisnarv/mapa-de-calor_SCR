"""Resolución de nombres de barrio, con los homónimos como caso central."""

import pytest

from app.core.config import settings
from app.schemas.chat import VistaTablero
from app.services.metrics_service import BarrioAmbiguo, BarrioNoEncontrado, MetricsService
from app.services.tools import ToolRunner

pytestmark = pytest.mark.skipif(
    not (settings.DATA_DIR / "data.json").is_file(),
    reason="No están los JSON del ETL en app/data/.",
)

# LAS MALVINAS existe en Barranquilla y en Campo de la Cruz.
HOMONIMO = "Las Malvinas"


@pytest.fixture
def metrics() -> MetricsService:
    return MetricsService(settings.DATA_DIR)


@pytest.mark.asyncio
async def test_un_homonimo_sin_pistas_sigue_siendo_ambiguo(metrics):
    """Sin nada en pantalla, preguntar es lo correcto."""
    with pytest.raises(BarrioAmbiguo) as exc:
        await metrics.resolver_barrio(HOMONIMO)

    municipios = {c.municipio for c in exc.value.candidatos}
    assert {"BARRANQUILLA", "CAMPO DE LA CRUZ"} <= municipios


@pytest.mark.asyncio
async def test_el_municipio_desempata(metrics):
    r = await metrics.resolver_barrio(HOMONIMO, municipio="CAMPO DE LA CRUZ")
    assert r.bkey == "CAMPO DE LA CRUZ | LAS MALVINAS"


@pytest.mark.asyncio
async def test_la_clave_completa_no_necesita_desempate(metrics):
    r = await metrics.resolver_barrio("BARRANQUILLA | LAS MALVINAS")
    assert r.bkey == "BARRANQUILLA | LAS MALVINAS"
    assert r.municipio == "BARRANQUILLA"


@pytest.mark.asyncio
async def test_el_barrio_de_la_pantalla_desempata(metrics):
    """El caso real: barrio seleccionado en el mapa y el usuario dice «aquí»."""
    runner = ToolRunner(metrics, VistaTablero(barrio="BARRANQUILLA | LAS MALVINAS"))

    resultado, filtro = await runner.run("efectividad", {"barrio": HOMONIMO})

    assert "error" not in resultado
    assert filtro.barrio == "BARRANQUILLA | LAS MALVINAS"


@pytest.mark.asyncio
async def test_sin_vista_el_mismo_caso_pregunta(metrics):
    runner = ToolRunner(metrics)

    resultado, _ = await runner.run("efectividad", {"barrio": HOMONIMO})

    assert resultado["error"] == "barrio_ambiguo"
    assert len(resultado["candidatos"]) == 2


@pytest.mark.asyncio
async def test_un_barrio_inventado_no_se_resuelve(metrics):
    with pytest.raises(BarrioNoEncontrado):
        await metrics.resolver_barrio("zzzz no existe zzzz")


@pytest.mark.asyncio
async def test_una_herramienta_inventada_no_alcanza_metodos_privados(metrics):
    """`run` despacha con getattr; sin validar, «resolver» llamaría a _resolver."""
    runner = ToolRunner(metrics)

    resultado, filtro = await runner.run("resolver", {"barrio": "x"})

    assert "desconocida" in resultado["error"]
    assert filtro is None
