"""El repertorio de casos con los que se mide si el asistente responde bien.

Cada prueba es un error que alguien vio de verdad, con el caso que lo destapó.
Cuando un pulgar abajo del chat se diagnostica, el caso se agrega aquí: es lo que
hace que ese error no vuelva, y es lo único que permite cambiar el prompt o una
herramienta sabiendo si mejoró o si se rompió otra cosa.

**Corren contra `tests/datos/`, no contra los JSON de producción.** Antes se
afirmaban cifras exactas contra `app/data/`, que el ETL regenera a diario: las
pruebas se rompían solas cada vez que entraban datos nuevos, y una suite que
falla por diseño deja de avisar cuando el fallo es de verdad. El recorte
congelado se regenera a mano con `tests/datos/generar.py` y no debería cambiar.
"""

from pathlib import Path

import pytest

from app.core.config import settings
from app.schemas.chat import VistaTablero
from app.services.metrics_service import FiltroNoResuelto, MetricsService
from app.services.tools import ToolRunner

DATOS = Path(__file__).resolve().parent / "datos"

pytestmark = pytest.mark.skipif(
    not (DATOS / "data.json").is_file(),
    reason="Falta el payload congelado: corre tests/datos/generar.py.",
)


@pytest.fixture
def metrics() -> MetricsService:
    return MetricsService(DATOS)


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

    assert todas["metricas"]["tot"] == 38
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

    assert salida["metricas"]["tot"] == 1160
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
    assert filas[0]["pe"] == 301
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
    runner = ToolRunner(MetricsService(DATOS))
    resultado, filtro = await runner.run("filtrar_mapa", {})

    assert filtro is None, "un filtro vacío no debe llegar al tablero"
    assert resultado["error"] == "filtro_vacio"


@pytest.mark.asyncio
async def test_un_ranking_sin_recorte_no_emite_accion(monkeypatch):
    """`_recorte` arma un filtro vacío; emitirlo le cambiaba la pestaña al usuario."""
    from app.services.openai_service import OpenAIService

    runner = ToolRunner(MetricsService(DATOS))
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
    runner = ToolRunner(MetricsService(DATOS))
    resultado, filtro = await runner.run("efectividad", {"mes": "2019"})

    assert resultado["error"] == "periodo_sin_datos"
    assert filtro is None
    # 0% de efectividad y «no hay datos» no pueden parecerse en la respuesta.
    assert "0" not in str(resultado.get("ef_pct", ""))


@pytest.mark.asyncio
async def test_pedir_un_ano_agrega_todos_sus_meses():
    """El caso real: Rebolo no tiene órdenes en enero, pero sí en el año."""
    runner = ToolRunner(MetricsService(DATOS))

    enero, _ = await runner.run("efectividad", {"barrio": "Rebolo", "mes": "2026-01"})
    ano, _ = await runner.run("efectividad", {"barrio": "Rebolo", "mes": "2026"})

    assert enero["metricas"]["tot"] == 0
    assert ano["metricas"]["tot"] > enero["metricas"]["tot"]
    assert ano["metricas"]["ef_pct"] > 0


# --- Bug 5: el mapa y la respuesta hablaban de periodos distintos --------------
#
# El caso real: «mejor barrio de Barranquilla» devolvió El Romance con 17 órdenes
# de todo el histórico (19 en el recorte congelado) y filtró el mapa, pero el mapa se quedó en agosto, donde
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

    assert resultado["metricas"]["tot"] == 5, "enero, no las 21 del histórico"
    assert "2026-01" in resultado["base"]
    assert filtro.meses == ["2026-01"]


@pytest.mark.asyncio
async def test_el_historico_completo_hay_que_pedirlo_por_su_nombre(metrics):
    """Heredar la pantalla no puede dejar sin forma de mirar toda la historia."""
    runner = ToolRunner(metrics, vista=VistaTablero(meses=["2026-01"]))
    resultado, filtro = await runner.run(
        "efectividad", {"barrio": "El Romance", "mes": "todo"}
    )

    assert resultado["metricas"]["tot"] == 21
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

    assert resultado["metricas"]["tot"] == 21
    assert filtro.meses == sorted(await metrics.meses_disponibles())


# --- Bug 7: buscaba dos veces y respondía otra cosa ----------------------------
#
# El caso real: «cuál es el barrio con más predio enrrejado». La frase con la
# errata daba cero con el `in` literal, así que el modelo probaba por su cuenta
# «reja» y «enrejado» y presentaba las dos listas como si se las hubieran pedido.
# Los números eran correctos; la pregunta que respondían, no.

@pytest.mark.asyncio
async def test_una_errata_del_usuario_no_cambia_la_busqueda(metrics):
    """«enrrejado» y «enrejado» son la misma pregunta."""
    runner = ToolRunner(metrics)

    con_errata, _ = await runner.run("buscar_en_observaciones", {"texto": "predio enrrejado"})
    sin_errata, _ = await runner.run("buscar_en_observaciones", {"texto": "predio enrejado"})

    assert con_errata["coincidencias"] == sin_errata["coincidencias"]
    assert con_errata["coincidencias"] > 0, "con el `in` literal esto daba cero"


@pytest.mark.asyncio
async def test_una_frase_se_busca_seguida(metrics):
    """El caso real: «red trenzada neutro» daba 12 en San Felipe donde hay 2.

    Buscando cada palabra por su lado contaba «…tendido red trenzada, se
    desconecta fase y neutro», que tiene las tres palabras y no es lo que se
    preguntó. Una frase es una frase.
    """
    runner = ToolRunner(metrics)

    frase, _ = await runner.run(
        "buscar_en_observaciones", {"texto": "red trenzada neutro", "mes": "2026-08"})
    parte, _ = await runner.run(
        "buscar_en_observaciones", {"texto": "red trenzada", "mes": "2026-08"})

    assert frase["coincidencias"] < parte["coincidencias"], (
        "la frase completa no puede aparecer tanto como su primera mitad"
    )


@pytest.mark.asyncio
async def test_terminos_distintos_siguen_siendo_busquedas_distintas(metrics):
    """La tolerancia es a las erratas, no a los sinónimos: «reja» no es «enrejado»."""
    runner = ToolRunner(metrics)

    reja, _ = await runner.run("buscar_en_observaciones", {"texto": "reja"})
    enrejado, _ = await runner.run("buscar_en_observaciones", {"texto": "enrejado"})

    assert reja["coincidencias"] != enrejado["coincidencias"]


def test_la_herramienta_pide_una_sola_busqueda():
    """Su descripción es lo que el modelo lee justo antes de decidir cuántas hace."""
    from app.services.tools import TOOLS

    descripcion = next(
        t["function"]["description"] for t in TOOLS
        if t["function"]["name"] == "buscar_en_observaciones"
    )

    assert "UNA SOLA BÚSQUEDA" in descripcion
    assert "No repitas con variantes" in descripcion
    assert "Di siempre qué término buscaste" in descripcion
    # La primera línea decía «busca una palabra» y contradecía todo lo anterior.
    assert "una palabra dentro del acta" not in descripcion


def test_el_parametro_no_puede_pedir_lo_contrario_que_la_descripcion():
    """El modelo lee el parámetro justo al rellenarlo: si dice «raíz», acorta.

    Pasó de verdad: la regla nueva estaba en el cuerpo de la descripción y el
    parámetro seguía diciendo «una o dos palabras, en su raíz». Ganó el parámetro,
    y «predio enrejado» se buscó como «predio» y «enrejado» por separado.
    """
    from app.services.tools import TOOLS

    parametro = next(
        t["function"]["parameters"]["properties"]["texto"]["description"]
        for t in TOOLS
        if t["function"]["name"] == "buscar_en_observaciones"
    )

    assert "TODAS sus palabras" in parametro
    assert "no lo partas" in parametro
    assert "en su raíz. " not in parametro


# --- Bug 8: se buscaba en el acta lo que ya tenía casilla ----------------------
#
# El caso real: «red chilena» se respondía buscando texto libre y daba 1.487 en
# producción, cuando la casilla `RED CHILENA/CONFIG. ESPECIAL` tenía 3.738. El
# acta se queda corta cuando el técnico no escribe el término, y se pasa cuando
# lo nombra sin que sea el motivo. La casilla es la cifra buena.

@pytest.mark.asyncio
async def test_la_casilla_y_el_acta_no_cuentan_lo_mismo(metrics):
    """Si coincidieran, una de las dos sobraría. Divergen, y hay que saber cuál usar."""
    por_casilla = await metrics.efectividad(subaccion="red chilena")
    por_acta = await metrics.buscar_en_observaciones(texto="chilena")

    assert por_casilla.tot == 678
    assert por_acta.coincidencias == 32
    assert por_casilla.tot > por_acta.coincidencias, (
        "el acta no nombra el término en todas las órdenes que lo tuvieron por causa"
    )


@pytest.mark.asyncio
async def test_la_subaccion_se_pide_por_su_nombre_a_medias(metrics):
    """Nadie escribe «RED CHILENA/CONFIG. ESPECIAL» entero."""
    completo = await metrics.efectividad(subaccion="RED CHILENA/CONFIG. ESPECIAL")
    a_medias = await metrics.efectividad(subaccion="red chilena")

    assert completo.tot == a_medias.tot == 678


@pytest.mark.asyncio
async def test_el_estrato_se_puede_preguntar(metrics):
    """Vive en la tarifa. Antes el prompt lo declaraba fuera de alcance."""
    e = await metrics.efectividad(tarifa="estrato 3")
    assert e.tot == 11_737


@pytest.mark.asyncio
async def test_los_seis_estratos_se_pueden_preguntar(metrics):
    """«estrato 6» también existe como «ESTRATO 6 EXENTO».

    Buscando por subcadena los dos encajaban, la búsqueda quedaba ambigua y los
    estratos 5 y 6 eran imposibles de consultar. Se resuelve por el tramo que va
    después de la barra, que es lo que la gente dice.
    """
    for estrato in range(1, 7):
        e = await metrics.efectividad(tarifa=f"estrato {estrato}")
        assert e.tot > 0, f"el estrato {estrato} quedó sin poder consultarse"


@pytest.mark.asyncio
async def test_un_nombre_que_encaja_en_dos_no_filtra_a_medias(metrics):
    """«comercial» es el tramo final de NO REGULADO y de NO RESIDENCIAL.

    Antes esto devolvía 14 órdenes donde hay 12.871: un número plausible y
    equivocado, que es la peor clase de respuesta. Ahora ni siquiera se calcula:
    avisa que el filtro no resolvió, en vez de fingir que sí.
    """
    with pytest.raises(FiltroNoResuelto):
        await metrics.efectividad(tarifa="comercial")

    preciso = await metrics.efectividad(tarifa="no residencial | comercial")
    assert preciso.tot > 0


@pytest.mark.asyncio
async def test_se_puede_ordenar_por_estrato_y_por_subaccion(metrics):
    """Dos dimensiones nuevas del ranking: antes solo brigada, técnico y barrio."""
    por_tarifa = await metrics.ranking(dimension="tarifa", min_ordenes=100)
    por_subaccion = await metrics.ranking(
        dimension="subaccion", ordenar_por="perdidas", min_ordenes=1
    )

    assert por_tarifa[0].nombre == "RESIDENCIAL | ESTRATO 3"
    assert por_subaccion[0].nombre == "MULTIFAMILIAR/MULTICOMERCIAL"


@pytest.mark.asyncio
async def test_un_recorte_que_el_tablero_no_sabe_mostrar_no_lo_mueve(metrics):
    """El mapa no filtra por subacción ni tarifa.

    Emitir el filtro igual dejaba la respuesta hablando de estrato 3 y el mapa
    enseñando todos los estratos del municipio: un recorte más ancho que la
    cifra, sin que nada avisara. Es el bug 5 otra vez, por una puerta nueva.
    """
    runner = ToolRunner(metrics)

    _, con_tarifa = await runner.run(
        "efectividad", {"municipio": "SOLEDAD", "tarifa": "estrato 3"}
    )
    _, sin_tarifa = await runner.run("efectividad", {"municipio": "SOLEDAD"})

    assert con_tarifa is None, "no hay tablero que muestre solo el estrato 3"
    assert sin_tarifa is not None, "sin el filtro nuevo, el mapa sí debe seguir a la respuesta"


# --- Bug 9: un filtro inventado vaciaba todo y se le echaba la culpa al mínimo -
#
# El caso real: el modelo puso `municipio: "Atlántico"` —el departamento, no un
# municipio— junto con `tarifa: "estrato 3"`. El municipio no resolvía a nada,
# `_agrupar` devolvía {} en silencio, y el ranking salía vacío con la nota «sin
# resultados con el mínimo pedido»: culpaba al umbral de 10 órdenes cuando en
# realidad SÍ había barrios de estrato 3 con más de 10, solo que el filtro
# nunca llegó a aplicarse. El modelo le creyó al mensaje y avisó de un problema
# de datos que no existía.

@pytest.mark.asyncio
async def test_un_municipio_inventado_no_vacia_el_filtro_valido_en_silencio(runner):
    """«Atlántico» es el departamento, no uno de los 25 municipios del payload."""
    salida, filtro = await runner.run("ranking", {
        "dimension": "barrio", "municipio": "Atlántico", "tarifa": "estrato 3",
        "min_ordenes": 10, "ordenar_por": "ef_adj_pond",
    })

    assert salida["error"] == "filtro_no_reconocido"
    assert salida["campo"] == "municipio"
    assert salida["valor_pedido"] == "Atlántico"
    assert "BARRANQUILLA" in salida["valores_validos"]
    assert filtro is None


@pytest.mark.asyncio
async def test_el_mismo_ranking_sin_el_municipio_inventado_si_tiene_filas(runner):
    """Prueba que el bug era el municipio, no el filtro de tarifa."""
    salida, _ = await runner.run("ranking", {
        "dimension": "barrio", "tarifa": "estrato 3",
        "min_ordenes": 10, "ordenar_por": "ef_adj_pond",
    })

    assert salida["filas"], "sí existen barrios de estrato 3 con al menos 10 órdenes"


@pytest.mark.asyncio
async def test_un_ranking_genuinamente_vacio_sigue_avisando_del_minimo(runner):
    """Regresión: cuando los filtros SÍ resuelven pero nada pasa el umbral, el
    mensaje original sigue siendo el correcto."""
    salida, _ = await runner.run("ranking", {"dimension": "barrio", "min_ordenes": 1_000_000})

    assert "error" not in salida
    assert "con el mínimo pedido" in salida["nota"]


def test_agrupar_cargue_no_se_confunde_con_una_pregunta_del_historico():
    """El caso real: «¿cuáles estratos tienen al menos 10 órdenes?», sin ningún
    archivo cargado en la conversación, disparó `agrupar_cargue` en vez de
    `ranking`. La descripción traía el ejemplo «¿cuántas son de estrato 2?» casi
    calcado a la pregunta real, y el modelo pescó el ejemplo equivocado.
    """
    from app.services.tools import TOOLS

    descripcion = next(
        t["function"]["description"] for t in TOOLS if t["function"]["name"] == "agrupar_cargue"
    )

    assert "SOLO las órdenes que el usuario subió en un archivo" in descripcion
    assert "usa `ranking`" in descripcion
    assert "sin archivo cargado" in descripcion


# --- Bug 10: «condiciones médicas» no aparece con ese nombre en ningún catálogo ---
#
# El caso real, tomado del feedback: «dame un listado de clientes no cortables
# por condiciones médicas» se preguntó tres veces el mismo día y las tres se
# declinó sin llamar a ninguna herramienta. La causa no era el prompt de
# declinar sin buscar —eso ya estaba resuelto—: era que el vocabulario que
# traducía «condiciones médicas» a una subacción real (MINIMO VITAL, ADULTO
# MAYOR/MENOR DE EDAD) se borró sin querer al reescribir esta misma sección
# para priorizar la casilla sobre el acta, y nadie lo notó hasta revisar la
# traza de producción.

def test_el_prompt_traduce_condiciones_medicas_a_una_subaccion_real():
    """Sin esta traducción, el modelo busca «condiciones médicas» literal, no
    encuentra nada en ningún catálogo, y declina en vez de intentar con el
    nombre real de la subacción."""
    prompt = settings.OPENAI_SYSTEM_PROMPT

    assert "condiciones médicas" in prompt
    assert "MINIMO VITAL" in prompt
    assert "ADULTO" in prompt and "MAYOR" in prompt


@pytest.mark.asyncio
async def test_las_subacciones_de_salud_que_promete_el_prompt_existen_con_datos(runner):
    """Que el prompt las nombre no basta: si alguna no resuelve a nada, el
    modelo seguiría sin poder responder aunque siguiera la instrucción al pie
    de la letra."""
    for subaccion in ("minimo vital", "adulto mayor", "protegido constitucionalmente"):
        salida, _ = await runner.run("efectividad", {"subaccion": subaccion})
        assert salida["metricas"]["tot"] > 0, f"«{subaccion}» no tiene órdenes en el recorte"


# --- Bug 11: causas_no_efectivas no filtraba por subacción ni tarifa ---------
#
# El caso real: preguntar «causas de no efectividad en estrato 3» ignoraba el
# estrato en silencio y devolvía las causas de todo el Atlántico. `efectividad`
# y `ranking` ya habían recibido este mismo filtro (Bug 8); a esta se le quedó
# fuera al hacerlo.

@pytest.mark.asyncio
async def test_causas_no_efectivas_filtra_por_tarifa(runner):
    con_filtro, filtro_mapa = await runner.run("causas_no_efectivas", {"tarifa": "estrato 3"})
    sin_filtro, _ = await runner.run("causas_no_efectivas", {})

    assert "estrato 3" in con_filtro["base"]
    assert con_filtro["causas"] != sin_filtro["causas"]
    assert filtro_mapa is None, "el tablero no sabe filtrar por tarifa; no debe moverse"


@pytest.mark.asyncio
async def test_causas_no_efectivas_filtra_por_subaccion(runner):
    """«Predio enrejado» es en sí una subacción de imposibilidad técnica: filtrar
    por ella tiene que dar 100% de esa causa, no la mezcla de todo el histórico."""
    salida, _ = await runner.run("causas_no_efectivas", {"subaccion": "predio enrejado"})

    assert len(salida["causas"]) == 1
    assert salida["causas"][0]["causa"] == "Imposibilidad tecnica"
    assert salida["causas"][0]["pct"] == 100.0
