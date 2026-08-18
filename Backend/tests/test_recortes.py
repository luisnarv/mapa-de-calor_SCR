"""Los cuatro bugs que aparecieron probando el chat a mano.

Cada prueba usa el caso real que los destapó, para que si vuelven se sepa cuál es.
"""

import pytest

from app.core.config import settings
from app.schemas.chat import VistaTablero
from app.services.metrics_service import MetricsService
from app.services.tools import ToolRunner

pytestmark = pytest.mark.skipif(
    not (settings.DATA_DIR / "data.json").is_file(),
    reason="No están los JSON del ETL en app/data/.",
)


@pytest.fixture
def metrics() -> MetricsService:
    return MetricsService(settings.DATA_DIR)


@pytest.fixture
def runner(metrics) -> ToolRunner:
    return ToolRunner(metrics)


# --- Bug 1: la brigada se ignoraba en silencio ---------------------------------

@pytest.mark.asyncio
async def test_el_filtro_de_brigada_cambia_el_resultado(runner):
    """«Brigadas pesadas en Villa Sabita» respondía sobre el barrio completo."""
    todas, _ = await runner.run("efectividad", {"barrio": "Villa Sabita"})
    pesada, _ = await runner.run(
        "efectividad", {"barrio": "Villa Sabita", "brigada": "Brigada Tipo Pesada"}
    )

    assert todas["metricas"]["tot"] == 37
    assert pesada["metricas"]["tot"] == 7
    assert "Brigada Tipo Pesada" in pesada["base"]


@pytest.mark.asyncio
async def test_toda_respuesta_declara_su_recorte(runner):
    for herramienta in ("efectividad", "causas_no_efectivas"):
        salida, _ = await runner.run(herramienta, {"barrio": "Villa Sabita", "mes": "2026-08"})
        assert "GALAPA | VILLA SABITA" in salida["base"]
        assert "2026-08" in salida["base"]


# --- Bug 2: el mínimo de 10 dejaba mudos a los barrios chicos ------------------

@pytest.mark.asyncio
async def test_si_el_barrio_no_da_muestra_se_amplia_al_municipio(runner):
    """Villa Sabita en agosto tiene 2 órdenes: antes respondía «no hay técnicos»."""
    salida, _ = await runner.run(
        "ranking", {"dimension": "tecnico", "barrio": "Villa Sabita", "mes": "2026-08"}
    )

    assert salida["filas"], "debería haber ampliado al municipio"
    assert "ampliado" in salida
    assert "GALAPA" in salida["base"]


@pytest.mark.asyncio
async def test_no_se_amplia_cuando_el_barrio_si_da_muestra(runner):
    salida, _ = await runner.run(
        "ranking", {"dimension": "tecnico", "barrio": "BARRANQUILLA | LAS MALVINAS"}
    )

    assert "ampliado" not in salida
    assert salida["base"].startswith("BARRANQUILLA | LAS MALVINAS")


# --- Bug 3: la lista se recortaba sin avisar -----------------------------------

@pytest.mark.asyncio
async def test_la_busqueda_reporta_el_total(runner):
    """«Los Robles» mostraba 7 de 12 y el usuario no veía el suyo."""
    salida, _ = await runner.run("buscar_barrio", {"texto": "Los Robles"})

    assert salida["total_encontrados"] == 12
    assert len(salida["candidatos"]) == 12
    assert "nota" not in salida, "no se recortó nada, no hay que avisar"


@pytest.mark.asyncio
async def test_si_se_recorta_la_lista_se_avisa(runner):
    salida, _ = await runner.run("buscar_barrio", {"texto": "villa"})

    assert salida["total_encontrados"] > len(salida["candidatos"])
    assert str(salida["total_encontrados"]) in salida["nota"]


# --- Bug 4: no sabía sumar barrios homónimos -----------------------------------

@pytest.mark.asyncio
async def test_los_homonimos_de_un_municipio_se_suman(metrics):
    """Las 10 etapas de Los Robles son un solo sitio para quien pregunta."""
    runner = ToolRunner(metrics, VistaTablero(municipio="SOLEDAD"))

    salida, _ = await runner.run("efectividad", {"barrio": "Los Robles"})

    assert salida["metricas"]["tot"] == 1095
    assert "10 barrios de SOLEDAD" in salida["base"]
    assert len(salida["detalle_por_barrio"]) == 10


@pytest.mark.asyncio
async def test_los_homonimos_de_municipios_distintos_siguen_preguntando(runner):
    """Barranquilla y Campo de la Cruz no son el mismo sitio: hay que preguntar."""
    salida, _ = await runner.run("efectividad", {"barrio": "Las Malvinas"})

    assert salida["error"] == "barrio_ambiguo"


# --- La herramienta y el prompt deben decir lo mismo ---------------------------

def test_filtrar_mapa_autoriza_el_resaltado_por_iniciativa_propia():
    """El modelo lee la descripción de la herramienta justo antes de decidir: si
    contradice al prompt, gana la descripción. Ya pasó con el parámetro `barrio`."""
    from app.core.config import settings
    from app.services.tools import TOOLS

    descripcion = next(
        t["function"]["description"] for t in TOOLS if t["function"]["name"] == "filtrar_mapa"
    )

    assert "iniciativa propia" in descripcion
    assert "UN resultado concreto" in descripcion
    assert "no la uses" in descripcion, "falta el caso en que NO debe resaltar"
    # Y el prompt de sistema tiene que pedir lo mismo, no lo contrario.
    assert "destaque UN resultado concreto" in settings.OPENAI_SYSTEM_PROMPT
