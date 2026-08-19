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


# --- Fallida no es perdida: la diferencia es que una se cobra ------------------

@pytest.mark.asyncio
async def test_mayor_perdida_ordena_por_ordenes_no_cobradas(runner):
    """Antes respondía por efectividad y ponía primero un barrio con 0 perdidas."""
    salida, _ = await runner.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "perdidas", "peores": True}
    )

    filas = salida["filas"]
    assert filas[0]["nombre"] == "BARRANQUILLA | CIUDADELA 20 DE JULIO"
    assert filas[0]["pe"] == 280
    assert [f["pe"] for f in filas] == sorted((f["pe"] for f in filas), reverse=True)
    assert salida["criterio"] == "perdidas"


@pytest.mark.asyncio
async def test_peores_se_invierte_segun_el_criterio(runner):
    """Con efectividad el peor es el de menor valor; con pérdidas, el de mayor."""
    por_perdidas, _ = await runner.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "perdidas", "peores": True}
    )
    por_efectividad, _ = await runner.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "ef_pct", "peores": True}
    )

    assert por_perdidas["filas"][0]["pe"] > 0
    assert por_efectividad["filas"][0]["ef_pct"] == 0.0


@pytest.mark.asyncio
async def test_el_ranking_por_defecto_no_cambia(runner):
    salida, _ = await runner.run("ranking", {"dimension": "brigada"})
    assert salida["criterio"] == "ef_adj"


@pytest.mark.asyncio
async def test_un_criterio_inventado_no_pasa(runner):
    salida, _ = await runner.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "lo_que_sea"}
    )
    assert "error" in salida


def test_la_regla_de_negocio_esta_en_el_prompt():
    """Sin ella el modelo lee «pérdida» como «mal desempeño»."""
    from app.core.config import settings

    prompt = settings.OPENAI_SYSTEM_PROMPT

    assert "Fallida: la brigada fue y no pudo suspender, pero la orden SÍ se paga" in prompt
    assert "Perdida: NO se paga" in prompt


@pytest.mark.asyncio
async def test_filtrar_mapa_sin_campos_no_mueve_el_tablero():
    """El modelo la llama vacía al cerrar un ranking; eso cambiaba de pestaña."""
    runner = ToolRunner(MetricsService(settings.DATA_DIR))
    resultado, filtro = await runner.run("filtrar_mapa", {})

    assert filtro is None, "un filtro vacío no debe llegar al tablero"
    assert resultado["error"] == "filtro_vacio"


@pytest.mark.asyncio
async def test_un_ranking_sin_recorte_no_emite_accion(monkeypatch):
    """`_recorte` arma un filtro vacío; emitirlo le cambiaba la pestaña al usuario."""
    from app.services.openai_service import OpenAIService

    runner = ToolRunner(MetricsService(settings.DATA_DIR))
    _resultado, filtro = await runner.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "ef_adj", "peores": True}
    )

    # La herramienta sigue devolviendo su filtro; quien decide no emitirlo es el
    # servicio, y esto fija que ese filtro está vacío.
    assert filtro is not None
    assert filtro.model_dump(exclude_none=True) == {}


# --- Periodos: un año, varios meses o uno solo --------------------------------
#
# Antes `mes` era UNA cadena "YYYY-MM". Al pedir «todo 2026» el modelo no tenía
# forma de expresarlo, mandaba el primer mes y un año se contestaba con enero.


def test_un_ano_vale_por_todos_sus_meses():
    from app.services.tools import expandir_meses

    disponibles = ["2026-01", "2026-02", "2026-03", "2025-11", "2025-12"]
    assert expandir_meses("2026", disponibles) == ["2026-01", "2026-02", "2026-03"]
    assert expandir_meses("2025", disponibles) == ["2025-11", "2025-12"]


def test_se_pueden_pedir_varios_meses_sueltos():
    from app.services.tools import expandir_meses

    disponibles = ["2026-06", "2026-07", "2026-08"]
    assert expandir_meses(["2026-07", "2026-08"], disponibles) == ["2026-07", "2026-08"]
    # Mezclar año y mes suelto no duplica ni desordena.
    assert expandir_meses(["2026", "2026-07"], disponibles) == ["2026-06", "2026-07", "2026-08"]


def test_un_mes_solo_sigue_funcionando_igual():
    from app.services.tools import expandir_meses

    assert expandir_meses("2026-07", ["2026-06", "2026-07"]) == ["2026-07"]
    assert expandir_meses(None, ["2026-07"]) is None


def test_un_periodo_sin_datos_no_se_confunde_con_todo_el_historico():
    """Devolver el histórico entero daría la cifra de un recorte que nadie pidió."""
    from app.services.tools import PeriodoVacio, expandir_meses

    with pytest.raises(PeriodoVacio):
        expandir_meses("2019", ["2026-07", "2026-08"])


@pytest.mark.asyncio
async def test_pedir_un_ano_sin_datos_avisa_en_vez_de_dar_cero():
    runner = ToolRunner(MetricsService(settings.DATA_DIR))
    resultado, filtro = await runner.run("efectividad", {"mes": "2019"})

    assert resultado["error"] == "periodo_sin_datos"
    assert filtro is None
    # 0% de efectividad y «no hay datos» no pueden parecerse en la respuesta.
    assert "0" not in str(resultado.get("ef_pct", ""))


@pytest.mark.asyncio
async def test_pedir_un_ano_agrega_todos_sus_meses():
    """El caso real: Rebolo no tiene órdenes en enero, pero sí en el año."""
    runner = ToolRunner(MetricsService(settings.DATA_DIR))

    enero, _ = await runner.run("efectividad", {"barrio": "Rebolo", "mes": "2026-01"})
    ano, _ = await runner.run("efectividad", {"barrio": "Rebolo", "mes": "2026"})

    assert enero["metricas"]["tot"] == 0
    assert ano["metricas"]["tot"] > enero["metricas"]["tot"]
    assert ano["metricas"]["ef_pct"] > 0


# --- Bug 5: el mapa y la respuesta hablaban de periodos distintos --------------
#
# El caso real: «mejor barrio de Barranquilla» devolvió El Romance con 17 órdenes
# de todo el histórico y filtró el mapa, pero el mapa se quedó en agosto, donde
# ese barrio no tiene ninguna. El usuario vio la pantalla vacía y preguntó si los
# datos eran de agosto; el modelo dijo que sí.

@pytest.mark.asyncio
async def test_un_recorte_historico_lleva_los_meses_al_mapa(runner, metrics):
    """Sin `meses`, el tablero conservaba los suyos y enseñaba otro periodo."""
    _, filtro = await runner.run("efectividad", {"barrio": "El Romance"})

    assert filtro.meses == sorted(await metrics.meses_disponibles())


@pytest.mark.asyncio
async def test_un_recorte_con_mes_manda_solo_ese_mes(runner):
    """Pedir un mes concreto sigue mandando ese mes, no el histórico entero."""
    _, filtro = await runner.run("efectividad", {"barrio": "El Romance", "mes": "2026-01"})

    assert filtro.meses == ["2026-01"]


# --- Bug 6: el chat contestaba el histórico mientras el mapa mostraba un mes ----
#
# El caso real: con agosto en pantalla, «mejor barrio de Barranquilla» devolvió El
# Romance con 17 órdenes de TODO el histórico —en agosto no tiene ninguna—, filtró
# el mapa y lo dejó en blanco. Al preguntarle si eran de agosto, dijo que sí.

@pytest.mark.asyncio
async def test_sin_mes_se_hereda_el_periodo_de_la_pantalla(metrics):
    """La cifra del chat tiene que salir del mismo periodo que el tablero."""
    runner = ToolRunner(metrics, vista=VistaTablero(meses=["2026-01"]))
    resultado, filtro = await runner.run("efectividad", {"barrio": "El Romance"})

    assert resultado["metricas"]["tot"] == 5, "enero, no las 17 del histórico"
    assert "2026-01" in resultado["base"]
    assert filtro.meses == ["2026-01"]


@pytest.mark.asyncio
async def test_el_historico_completo_hay_que_pedirlo_por_su_nombre(metrics):
    """Heredar la pantalla no puede dejar sin forma de mirar toda la historia."""
    runner = ToolRunner(metrics, vista=VistaTablero(meses=["2026-01"]))
    resultado, filtro = await runner.run(
        "efectividad", {"barrio": "El Romance", "mes": "todo"}
    )

    assert resultado["metricas"]["tot"] == 17
    assert filtro.meses == sorted(await metrics.meses_disponibles())


@pytest.mark.asyncio
async def test_un_mes_explicito_le_gana_a_la_pantalla(metrics):
    """Si lo piden, manda lo pedido: heredar es solo el valor por defecto."""
    runner = ToolRunner(metrics, vista=VistaTablero(meses=["2026-08"]))
    resultado, _ = await runner.run(
        "efectividad", {"barrio": "El Romance", "mes": "2026-01"}
    )

    assert resultado["metricas"]["tot"] == 5


@pytest.mark.asyncio
async def test_sin_vista_se_sigue_respondiendo_el_historico(runner, metrics):
    """El chat también se usa sin tablero detrás; ahí no hay nada que heredar."""
    resultado, filtro = await runner.run("efectividad", {"barrio": "El Romance"})

    assert resultado["metricas"]["tot"] == 17
    assert filtro.meses == sorted(await metrics.meses_disponibles())
