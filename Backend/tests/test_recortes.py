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


# --- filtrar_mapa se caía con «DE» en mayúsculas --------------------------------

@pytest.mark.asyncio
async def test_filtrar_mapa_con_el_de_en_mayusculas_no_tumba_el_chat(runner):
    """«LOS ROBLES DE BARANOA»: el código detectaba « de » sin importar las
    mayúsculas pero lo cortaba distinguiéndolas, y el IndexError salía como «Error
    inesperado» al usuario. Pasó con una pregunta real sobre la tasa de financiación."""
    salida, filtro = await runner.run("filtrar_mapa", {"barrio": "LOS ROBLES DE BARANOA"})
    assert "error" not in salida
    assert filtro.barrio == "BARANOA | LOS ROBLES"


# --- Recomendar con las cifras del municipio como si fueran del barrio ----------

@pytest.mark.asyncio
async def test_al_ampliar_al_municipio_la_nota_dice_que_las_cifras_no_son_del_barrio(runner):
    """Villa Sabita no da muestra y se sube a Galapa; el modelo escribía «para el
    barrio Villa Sabita…» con las cifras del municipio. Avisarlo solo en `ampliado`
    no bastó: la nota, que el modelo copia casi literal, tiene que decirlo."""
    salida, _ = await runner.run(
        "ranking", {"dimension": "tecnico", "barrio": "Villa Sabita", "mes": "2026-08"}
    )
    assert "ampliado" in salida
    assert "TODO GALAPA" in salida["nota"] and "NO del barrio" in salida["nota"]


# --- «¿Dónde NO hay…?» se contestaba con el top de los que más tienen -----------

@pytest.mark.asyncio
async def test_ordenar_perdidas_de_menos_a_mas_deja_primero_a_los_que_tienen_cero(runner):
    """El modelo pidió peores=true (los que MÁS pierden) y concluyó «no hay ningún
    barrio sin pérdidas». La llamada correcta es peores=false: salen primero los 0."""
    de_menos, _ = await runner.run("ranking", {
        "dimension": "barrio", "municipio": "Baranoa", "ordenar_por": "perdidas",
        "peores": False, "min_ordenes": 1,
    })
    de_mas, _ = await runner.run("ranking", {
        "dimension": "barrio", "municipio": "Baranoa", "ordenar_por": "perdidas",
        "peores": True, "min_ordenes": 1,
    })
    assert de_menos["filas"][0]["pe"] == 0
    assert de_mas["filas"][0]["pe"] > 0


# --- Seis respuestas que salieron mal en la prueba de 113 preguntas -------------

def test_las_opciones_de_dimension_de_cobros_son_las_que_el_backend_acepta():
    """Pasar las opciones por los reemplazos de texto convirtió 'brigada' en 'plan':
    el modelo ofrecía una dimensión que el backend rechazaba y respondía «en COBROS
    no existe la dimensión plan» a «¿qué rinde más, Cobro Persuasivo o Visita
    Personalizada?»."""
    from app.services.tools import tools_para_proceso

    ranking = next(t for t in tools_para_proceso("cobros") if t["function"]["name"] == "ranking")
    opciones = ranking["function"]["parameters"]["properties"]["dimension"]["enum"]

    assert "brigada" in opciones and "plan" not in opciones


@pytest.mark.asyncio
async def test_cada_dimension_que_cobros_publica_la_acepta_el_ranking(runner):
    from app.services.tools import tools_para_proceso

    ranking = next(t for t in tools_para_proceso("cobros") if t["function"]["name"] == "ranking")
    for dimension in ranking["function"]["parameters"]["properties"]["dimension"]["enum"]:
        salida, _ = await runner.run("ranking", {"dimension": dimension, "municipio": "Soledad"})
        assert not str(salida.get("error", "")).startswith("Argumentos inválidos"), dimension


def test_cero_gestiones_se_avisa_como_cero_gestiones_y_no_como_0_por_ciento(runner):
    """«Los Olivos: 0% sobre ninguna gestión» no significa nada."""
    assert "NO digas «0%»" in runner._avisos(meses=None, actividad=None, vacio=True)
    assert "NO digas «0%»" not in runner._avisos(meses=None, actividad=None, vacio=False)


@pytest.mark.asyncio
async def test_el_mes_en_curso_se_avisa_como_incompleto(runner):
    """El recorte llega hasta el 15 de octubre: compararlo con septiembre entero como si
    fueran iguales dio un «mejoró» por una décima."""
    octubre, _ = await runner.run("efectividad", {"barrio": "Villa Sabita", "mes": "2026-10"})
    septiembre, _ = await runner.run("efectividad", {"barrio": "Villa Sabita", "mes": "2026-09"})

    assert "2026-10 está incompleto" in octubre["nota"] and "2026-10-15" in octubre["nota"]
    assert "incompleto" not in septiembre["nota"]


@pytest.mark.asyncio
async def test_un_nombre_aproximado_de_actividad_avisa_cual_se_uso(runner):
    """«Cobro Persuasivo Residencial» se resolvía a «Cobro Persuasivo» y la respuesta lo
    presentaba como la actividad pedida."""
    salida, _ = await runner.run("efectividad", {"actividad": "Brigada Canasta Residencial"})

    assert "«Brigada Canasta Residencial» no existe" in salida["nota"]
    assert "«Brigada Canasta»" in salida["nota"]
    exacta, _ = await runner.run("efectividad", {"actividad": "Brigada Canasta"})
    assert "no existe:" not in exacta["nota"]


# --- Varios barrios a la vez --------------------------------------------------------

BARRIOS_VARIOS = ["BARRANQUILLA | LAS MALVINAS", "BARRANQUILLA | REBOLO"]


@pytest.mark.asyncio
async def test_varios_barrios_se_suman_y_el_mapa_los_recibe_como_lista(runner):
    juntos, filtro = await runner.run("efectividad", {"barrio": BARRIOS_VARIOS})
    uno, _ = await runner.run("efectividad", {"barrio": BARRIOS_VARIOS[0]})
    otro, _ = await runner.run("efectividad", {"barrio": BARRIOS_VARIOS[1]})

    assert juntos["metricas"]["tot"] == uno["metricas"]["tot"] + otro["metricas"]["tot"]
    assert "2 barrios" in juntos["base"]
    assert len(filtro.barrios) == 2 and filtro.barrio is None and filtro.municipio is None
    assert len(juntos["detalle_por_barrio"]) == 2


@pytest.mark.asyncio
async def test_un_solo_barrio_en_lista_se_trata_como_un_barrio(runner):
    _, filtro = await runner.run("efectividad", {"barrio": [BARRIOS_VARIOS[0]]})

    assert filtro.barrio is not None and filtro.barrios is None


@pytest.mark.asyncio
async def test_el_ranking_acepta_varios_barrios(runner):
    salida, _ = await runner.run("ranking", {"dimension": "tecnico", "barrio": BARRIOS_VARIOS})

    assert "error" not in salida and "2 barrios" in salida["base"]


@pytest.mark.asyncio
async def test_filtrar_mapa_con_varios_barrios_manda_la_lista(runner):
    salida, filtro = await runner.run("filtrar_mapa", {"barrio": BARRIOS_VARIOS, "meses": ["2026-08"]})

    assert len(filtro.barrios) == 2 and filtro.meses == ["2026-08"]
    assert salida["aplicado"]["barrios"] == filtro.barrios


@pytest.mark.asyncio
async def test_un_barrio_con_de_en_mayusculas_ya_no_tumba_ni_efectividad_ni_ranking(runner):
    """`_grupo` tenía el mismo corte sensible a mayúsculas que `filtrar_mapa`."""
    for herramienta in ("efectividad", "ranking"):
        args = {"barrio": "LOS ROBLES DE BARANOA"} | ({"dimension": "tecnico"} if herramienta == "ranking" else {})
        salida, _ = await runner.run(herramienta, args)
        assert not str(salida.get("error", "")).startswith("Argumentos inválidos"), herramienta
        assert "BARANOA | LOS ROBLES" in salida["base"]


@pytest.mark.asyncio
async def test_varios_barrios_con_un_municipio_de_mas_no_dejan_el_filtro_contradictorio(runner):
    """«Chupundum, El Concorde y Suan de Malambo»: Suan es de otro municipio. Con el
    municipio pegado el filtro se contradecía y el mapa no se movía."""
    salida, filtro = await runner.run(
        "filtrar_mapa", {"barrio": BARRIOS_VARIOS, "municipio": "Barranquilla"})

    assert len(filtro.barrios) == 2 and filtro.municipio is None
    assert "municipio" not in salida["aplicado"]


# --- Filtrar y consultar por técnico (gestor en COBROS) -----------------------------

def _tecnicos_con_ordenes(runner, cuantos: int = 2) -> list[str]:
    """Los técnicos del recorte con más órdenes: los únicos con los que se puede probar."""
    from collections import Counter

    p = runner.metrics.datos
    return [p.tecs[i] for i, _ in Counter(p.t).most_common(cuantos)]


@pytest.mark.asyncio
async def test_un_tecnico_se_encuentra_con_las_palabras_en_otro_orden(runner):
    nombre = _tecnicos_con_ordenes(runner, 1)[0]
    desordenado = " ".join(reversed(nombre.split()))

    exacto, filtro = await runner.run("efectividad", {"tecnico": nombre})
    otro, _ = await runner.run("efectividad", {"tecnico": desordenado})

    assert exacto["metricas"]["tot"] == otro["metricas"]["tot"] > 0
    assert filtro.tecnicos == [nombre], "el tablero recibe el nombre de catálogo, no el que escribió el usuario"
    assert nombre.split(" - ")[0] in exacto["base"]


@pytest.mark.asyncio
async def test_varios_tecnicos_se_suman(runner):
    uno, otro = _tecnicos_con_ordenes(runner)
    juntos, filtro = await runner.run("efectividad", {"tecnico": [uno, otro]})
    solo_uno, _ = await runner.run("efectividad", {"tecnico": uno})
    solo_otro, _ = await runner.run("efectividad", {"tecnico": otro})

    assert juntos["metricas"]["tot"] == solo_uno["metricas"]["tot"] + solo_otro["metricas"]["tot"]
    assert filtro.tecnicos == [uno, otro]


@pytest.mark.asyncio
async def test_el_ranking_y_las_causas_aceptan_tecnico(runner):
    nombre = _tecnicos_con_ordenes(runner, 1)[0]
    ranking, _ = await runner.run("ranking", {"dimension": "barrio", "tecnico": nombre, "min_ordenes": 1})
    causas, _ = await runner.run("causas_no_efectivas", {"tecnico": nombre})

    assert "error" not in ranking and ranking["filas"]
    assert "error" not in causas and nombre.split(" - ")[0] in causas["base"]


@pytest.mark.asyncio
async def test_un_tecnico_inventado_no_devuelve_el_total_sin_filtrar(runner):
    salida, _ = await runner.run("efectividad", {"tecnico": "Zzz Inventado"})

    assert salida["error"] == "filtro_no_reconocido" and salida["campo"] == "tecnico"


def test_un_apellido_comun_devuelve_los_candidatos_en_vez_de_elegir_uno():
    from types import SimpleNamespace

    from app.services.metrics_service import FiltroNoResuelto, MetricsService

    falso = SimpleNamespace(datos=SimpleNamespace(tecs=["ANA PEREZ - 1", "LUIS PEREZ - 2", "ANA GOMEZ - 3"]))
    with pytest.raises(FiltroNoResuelto) as error:
        MetricsService.indices_tecnico(falso, "Perez")
    assert error.value.catalogo == ["ANA PEREZ - 1", "LUIS PEREZ - 2"]
    assert MetricsService.indices_tecnico(falso, "perez ana") == [0]


@pytest.mark.asyncio
async def test_filtrar_mapa_deja_elegido_al_tecnico(runner):
    nombre = _tecnicos_con_ordenes(runner, 1)[0]
    salida, filtro = await runner.run("filtrar_mapa", {"tecnico": nombre.split(" - ")[0]})

    assert filtro.tecnicos == [nombre] and salida["aplicado"]["tecnicos"] == [nombre]


@pytest.mark.asyncio
async def test_los_trabajos_de_un_tecnico_se_listan_y_cuadran_con_su_efectividad(runner, metrics):
    """«Muéstrame los trabajos de un gestor»: sin herramienta que listara visitas, el
    agente respondía con un ranking de gestores que nadie pidió."""
    tec = metrics.datos.tecs[max(range(len(metrics.datos.tecs)), key=lambda i: metrics.datos.t.count(i))]

    lista, filtro = await runner.run("ordenes_de_tecnico", {"tecnico": tec, "limite": 5})
    resumen, _ = await runner.run("efectividad", {"tecnico": tec})

    assert len(lista["filas"]) == 5
    assert lista["total"] == resumen["metricas"]["tot"]
    assert sum(lista["por_estado"].values()) == lista["total"]
    fechas = [f["fecha"] for f in lista["filas"]]
    assert fechas == sorted(fechas, reverse=True)
    assert filtro.tecnicos == [tec]


@pytest.mark.asyncio
async def test_los_trabajos_sin_decir_de_cual_tecnico_piden_el_nombre(runner):
    salida, filtro = await runner.run("ordenes_de_tecnico", {"mes": "2026-01"})

    assert salida["error"] == "falta_tecnico" and filtro is None


# --- Orden, conteos y comparaciones (batería de 105 preguntas del coordinador) ------------


@pytest.mark.asyncio
async def test_sin_decir_peores_el_ranking_por_perdidas_deja_primero_al_que_mas_pierde(runner):
    """«Top 5 técnicos por pérdidas» salió con el de 1 pérdida arriba: el valor por defecto de
    `peores` era justo el orden de «dónde NO hay pérdidas»."""
    mas, _ = await runner.run("ranking", {"dimension": "tecnico", "ordenar_por": "perdidas", "min_ordenes": 1})
    menos, _ = await runner.run(
        "ranking", {"dimension": "tecnico", "ordenar_por": "perdidas", "orden": "menos", "min_ordenes": 1})

    perdidas = [f["pe"] for f in mas["filas"]]
    assert perdidas == sorted(perdidas, reverse=True) and perdidas[0] > perdidas[-1]
    asc = [f["pe"] for f in menos["filas"]]
    assert asc == sorted(asc) and mas["orden"] == "de más a menos"


@pytest.mark.asyncio
async def test_el_ranking_dice_cuantos_hay_antes_de_recortar(runner, metrics):
    """«¿Cuántos técnicos trabajaron en agosto?» respondía «al menos 10»: no había cómo contarlos."""
    salida, _ = await runner.run("ranking", {"dimension": "tecnico", "min_ordenes": 1, "limite": 1})

    assert len(salida["filas"]) == 1
    assert salida["total_en_recorte"] == len({t for t in metrics.datos.t})


@pytest.mark.asyncio
async def test_se_puede_ordenar_por_zona(runner):
    salida, _ = await runner.run("ranking", {"dimension": "zona", "ordenar_por": "ef_pct", "min_ordenes": 1})

    assert salida["filas"] and all(f["nombre"].startswith("ATLANTICO") for f in salida["filas"])


@pytest.mark.asyncio
async def test_los_barrios_que_se_nombran_en_un_ranking_quedan_marcados_con_su_clave(runner):
    """Decía «filtré el mapa para estos barrios» y la acción solo traía el mes; marcarlos por
    nombre además chocaba con los homónimos («¿La Central de Soledad o de Repelón?»)."""
    salida, filtro = await runner.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "perdidas", "orden": "mas", "limite": 3, "min_ordenes": 1})

    assert filtro.barrios == [f["nombre"] for f in salida["filas"]] and len(filtro.barrios) == 3


@pytest.mark.asyncio
async def test_comparar_tecnicos_da_una_fila_por_cada_uno(runner, metrics):
    """«Compara a A y a B» los sumaba juntos y no decía quién rinde más."""
    tecs = sorted(set(metrics.datos.t), key=lambda i: -metrics.datos.t.count(i))[:2]
    nombres = [metrics.datos.tecs[i] for i in tecs]

    salida, _ = await runner.run("efectividad", {"tecnico": nombres})

    assert {f["nombre"] for f in salida["detalle_por_tecnico"]} == set(nombres)
    assert sum(f["tot"] for f in salida["detalle_por_tecnico"]) == salida["metricas"]["tot"]


@pytest.mark.asyncio
async def test_si_el_mapa_no_se_movio_la_nota_lo_dice(runner):
    """«Ya te marqué el estrato 6» cuando el tablero no tiene filtro de estrato."""
    salida, filtro = await runner.run(
        "ranking", {"dimension": "tarifa", "ordenar_por": "ef_pct", "min_ordenes": 1, "tarifa": "RESIDENCIAL | ESTRATO 3"})

    assert filtro is None and "no digas que lo marcaste" in salida["nota"]


@pytest.mark.asyncio
async def test_un_nombre_de_persona_en_barrio_se_resuelve_como_tecnico(runner, metrics):
    """«Mapa de calor de Alexander de los Reyes»: el modelo lo mandaba como barrio y la
    respuesta era «no encontré el barrio»."""
    nombre = metrics.datos.tecs[metrics.datos.t[0]]

    salida, filtro = await runner.run("filtrar_mapa", {"barrio": nombre, "capas": ["calor"]})

    assert filtro.tecnicos == [nombre] and filtro.barrio is None and filtro.capas == ["calor"]
    # Algo que no es barrio ni técnico sigue siendo un barrio no encontrado.
    salida, filtro = await runner.run("filtrar_mapa", {"barrio": "Zzzxqw"})
    assert salida["error"] == "barrio_no_encontrado" and filtro is None


# --- Análisis mensual: periodos, series, concentración, altas y bajas, cruces, riesgo ------


@pytest.mark.asyncio
async def test_comparar_periodos_es_la_resta_de_las_dos_consultas(runner, metrics):
    """«¿Subió o bajó?»: la diferencia tiene que ser la de las dos cifras que da `efectividad`."""
    a, b = metrics.datos.meses[-3:-1]

    salida, filtro = await runner.run("comparar_periodos", {"mes_a": a, "mes_b": b})
    ea, _ = await runner.run("efectividad", {"mes": a})
    eb, _ = await runner.run("efectividad", {"mes": b})

    total = salida["total"]
    assert total["efectividad_a"] == round(ea["metricas"]["ef_adj"], 1) and total["efectividad_b"] == round(eb["metricas"]["ef_adj"], 1)
    assert total["delta_efectividad_pp"] == round(total["efectividad_b"] - total["efectividad_a"], 1)
    assert filtro is None and "no digas que lo marcaste" in salida["nota"]


@pytest.mark.asyncio
async def test_comparar_periodos_por_causa_muestra_que_causas_crecieron(runner, metrics):
    a, b = metrics.datos.meses[-3:-1]

    salida, _ = await runner.run(
        "comparar_periodos", {"dimension": "causa", "mes_a": a, "mes_b": b, "ordenar_por": "volumen", "orden": "mas"})

    deltas = [f["delta_tot"] for f in salida["filas"]]
    assert deltas == sorted(deltas, reverse=True) and salida["periodo_a"] == a and salida["periodo_b"] == b


@pytest.mark.asyncio
async def test_serie_mensual_repite_la_efectividad_de_cada_mes(runner, metrics):
    salida, _ = await runner.run("serie_mensual", {"municipio": "Soledad"})

    for fila in salida["serie"]:
        e, _ = await runner.run("efectividad", {"municipio": "Soledad", "mes": fila["mes"]})
        assert fila["efectividad"] == e["metricas"]["ef_adj"] and fila["tot"] == e["metricas"]["tot"]
    caidas = salida["meses_seguidos_en_caida"]
    serie = [f["efectividad"] for f in salida["serie"]]
    assert all(serie[-i - 1] < serie[-i - 2] for i in range(caidas))
    assert caidas == len(serie) - 1 or serie[-caidas - 1] >= serie[-caidas - 2]


@pytest.mark.asyncio
async def test_concentracion_cuenta_cuantos_explican_el_porcentaje(runner, metrics):
    salida, _ = await runner.run(
        "concentracion", {"dimension": "barrio", "ordenar_por": "perdidas", "porcentaje": 50, "mes": "todo", "limite": 50})

    n, total = salida["cuantos_lo_explican"], salida["total"]
    valores = [f["perdidas"] for f in salida["primeros"]]
    assert sum(valores[:n]) * 100 >= 50 * total
    assert n == 1 or sum(valores[: n - 1]) * 100 < 50 * total
    assert salida["entidades_con_valor"] <= salida["entidades_en_total"]


@pytest.mark.asyncio
async def test_las_bajas_son_quienes_trabajaron_el_mes_anterior_y_ya_no_aparecen(runner, metrics):
    datos, por_mes = metrics.datos, metrics.tecnicos_por_mes()
    k = len(datos.meses) - 2  # el último suele estar incompleto
    esperadas = {datos.tecs[t] for t, m in por_mes.items() if (k - 1) in m and k not in m}
    nuevas = {datos.tecs[t] for t, m in por_mes.items() if min(m) == k}

    bajas, _ = await runner.run("altas_y_bajas", {"tipo": "bajas", "mes": datos.meses[k], "limite": 100})
    altas, _ = await runner.run("altas_y_bajas", {"tipo": "altas", "mes": datos.meses[k], "limite": 100})

    assert {f["nombre"] for f in bajas["filas"]} == esperadas and bajas["total"] == len(esperadas)
    assert {f["nombre"] for f in altas["filas"]} == nuevas
    primero, _ = await runner.run("altas_y_bajas", {"tipo": "altas", "mes": datos.meses[0]})
    assert primero["error"] == "sin_mes_anterior"


@pytest.mark.asyncio
async def test_cruzar_reparte_el_total_entre_las_combinaciones(runner, metrics):
    mes = metrics.datos.meses[-2]

    cruce, _ = await runner.run(
        "cruzar", {"dimension_a": "brigada", "dimension_b": "municipio", "min_ordenes": 1, "mes": mes, "limite": 50})
    total, _ = await runner.run("efectividad", {"mes": mes})

    assert sum(f["tot"] for f in cruce["filas"]) <= total["metricas"]["tot"]
    if cruce["total_combinaciones"] <= 50:
        assert sum(f["tot"] for f in cruce["filas"]) == total["metricas"]["tot"]
    mas, _ = await runner.run("cruzar", {"dimension_a": "tipo_os", "dimension_b": "zona", "mes": mes})
    assert mas["filas"] and mas["filas"][0]["tot"] >= mas["filas"][-1]["tot"]


@pytest.mark.asyncio
async def test_el_desempeno_contra_el_grupo_es_la_diferencia_de_dos_cifras(runner, metrics):
    mes = metrics.datos.meses[-2]

    salida, _ = await runner.run("desempeno_vs_grupo", {"grupo": "brigada", "mes": mes, "min_ordenes": 20, "limite": 5})

    assert salida["filas"]
    for f in salida["filas"]:
        assert f["diferencia_pp"] == round(f["efectividad"] - f["efectividad_del_grupo"], 1)
        grupo, _ = await runner.run("efectividad", {"brigada": f["brigada"], "mes": mes})
        assert f["efectividad_del_grupo"] == round(grupo["metricas"]["ef_adj"], 1)
    diferencias = [f["diferencia_pp"] for f in salida["filas"]]
    assert diferencias == sorted(diferencias)  # por defecto, primero los que quedan más por debajo


@pytest.mark.asyncio
async def test_los_barrios_criticos_salen_ordenados_y_se_marcan_si_piden_pocos(runner, metrics):
    salida, filtro = await runner.run("barrios_criticos", {"mes": metrics.datos.meses[-2], "limite": 3, "min_ordenes": 10})

    riesgos = [f["riesgo"] for f in salida["filas"]]
    assert riesgos == sorted(riesgos, reverse=True) and all(0 <= r <= 100 for r in riesgos)
    assert filtro.barrios == [f["barrio"] for f in salida["filas"]]
    sin_limite, sin_filtro = await runner.run("barrios_criticos", {"mes": metrics.datos.meses[-2]})
    assert sin_filtro is None and len(sin_limite["filas"]) == 10


def test_el_indice_de_riesgo_de_cobros_corta_en_1_2_y_3_veces_el_promedio():
    """Los cortes de `Tablero.js`: 1× cae en 30, 2× en 60 y 3× en 80; de ahí a 100."""
    from app.services.riesgo import _cuantil, _nivel_cobros

    assert [_nivel_cobros(m) for m in (0, 0.5, 0.99, 1, 1.5, 2, 3, 6, 9)] == [0, 15, 30, 31, 46, 61, 81, 100, 100]
    assert _cuantil([1.0, 2.0, 3.0, 4.0, 5.0], 0.9) == 4.6 and _cuantil([], 0.9) == 0.0


# --- Los filtros de la pantalla se aplican solos ---------------------------------------------


@pytest.mark.asyncio
async def test_una_pregunta_general_sale_de_la_zona_que_el_usuario_tiene_puesta(metrics):
    """Con la zona Norte filtrada, «¿cuál es la efectividad?» se contestaba sobre todo el Atlántico:
    el modelo tenía que acordarse de pasar la zona, y a veces no lo hacía."""
    zona = metrics.datos.zonas[0]
    r = ToolRunner(metrics, vista=VistaTablero(zona=zona))
    explicita = ToolRunner(metrics)

    general, _ = await r.run("efectividad", {})
    esperada, _ = await explicita.run("efectividad", {"zona": zona})
    todo, _ = await explicita.run("efectividad", {})

    assert general["metricas"]["tot"] == esperada["metricas"]["tot"] != todo["metricas"]["tot"]
    assert general["filtros_de_pantalla"] == {"zona": zona} and "filtrado en su pantalla" in general["nota"]


@pytest.mark.asyncio
async def test_lo_que_el_usuario_pide_expresamente_manda_sobre_la_pantalla(metrics):
    zona = metrics.datos.zonas[0]
    municipio = metrics.datos.munis[0]
    r = ToolRunner(metrics, vista=VistaTablero(zona=zona))

    pedido, _ = await r.run("efectividad", {"municipio": municipio})
    todo, _ = await r.run("efectividad", {"sin_filtros_de_pantalla": True})
    sin_vista, _ = await ToolRunner(metrics).run("efectividad", {})

    assert "filtros_de_pantalla" not in pedido and municipio in pedido["base"]
    assert "filtros_de_pantalla" not in todo and todo["metricas"]["tot"] == sin_vista["metricas"]["tot"]


@pytest.mark.asyncio
async def test_un_ranking_por_zona_no_se_limita_a_la_zona_elegida(metrics):
    """Preguntan «¿qué zona rinde peor?»: comparar la zona elegida consigo misma no responde nada."""
    r = ToolRunner(metrics, vista=VistaTablero(zona=metrics.datos.zonas[0]))

    salida, _ = await r.run("ranking", {"dimension": "zona", "min_ordenes": 1})

    assert len(salida["filas"]) > 1 and "filtros_de_pantalla" not in salida


@pytest.mark.asyncio
async def test_todos_los_filtros_de_la_pantalla_entran_juntos(metrics):
    p = metrics.datos
    vista = VistaTablero(
        municipio=p.munis[0], brigada=p.brigs[0], tipo_os=p.tipos[0],
        barrio=None, tecnicos=[p.tecs[p.t[0]]])
    r = ToolRunner(metrics, vista=vista)

    salida, _ = await r.run("efectividad", {})

    assert set(salida["filtros_de_pantalla"]) == {"municipio", "brigada", "tipo de orden", "técnicos"}
    explicita, _ = await ToolRunner(metrics).run(
        "efectividad", {"municipio": p.munis[0], "brigada": p.brigs[0], "tipo_os": p.tipos[0], "tecnico": p.tecs[p.t[0]]})
    assert salida["metricas"]["tot"] == explicita["metricas"]["tot"]


@pytest.mark.asyncio
async def test_un_barrio_elegido_gana_a_su_municipio_y_su_zona(metrics):
    p = metrics.datos
    bkey = p.barrios[p.b[0]]
    municipio = bkey.partition(" | ")[0]
    r = ToolRunner(metrics, vista=VistaTablero(barrio=bkey, municipio=municipio, zona=p.zonas[0]))

    salida, _ = await r.run("efectividad", {})

    assert salida["filtros_de_pantalla"]["barrio"] == bkey.partition(" | ")[2]
    assert "zona" not in salida["filtros_de_pantalla"]


# --- COBROS no carga archivos -------------------------------------------------------------


def test_el_bot_de_cobros_no_tiene_herramientas_de_archivo_y_el_de_scr_si():
    """Preguntas reales en COBROS terminaban en «¿tienes un archivo cargado?»: el modelo veía las
    herramientas del cargue (que solo existe en SCR) y las ofrecía."""
    from app.services.tools import _HERRAMIENTAS_DE_CARGUE, tools_para_proceso

    cobros = {t["function"]["name"] for t in tools_para_proceso("cobros")}
    scr = {t["function"]["name"] for t in tools_para_proceso("scr")}

    assert not cobros & _HERRAMIENTAS_DE_CARGUE
    assert _HERRAMIENTAS_DE_CARGUE <= scr
    assert {"ranking", "recaudo", "barrios_criticos", "propension_pago"} <= cobros


@pytest.mark.asyncio
async def test_pedir_una_herramienta_de_archivo_en_cobros_dice_que_no_hay_archivos(metrics, tmp_path):
    destino = tmp_path / "cobros"
    destino.mkdir()
    for archivo in DATOS.iterdir():
        if archivo.is_file():
            (destino / archivo.name).write_bytes(archivo.read_bytes())
    runner_cobros = ToolRunner(MetricsService(destino))

    salida, filtro = await runner_cobros.run("resumen_cargue", {})

    assert salida["error"] == "sin_archivos_en_cobros" and filtro is None
    scr, _ = await ToolRunner(metrics).run("resumen_cargue", {})
    assert scr.get("error") != "sin_archivos_en_cobros"


# --- Financiaciones: acuerdo_m verdadero Y resultado «Realizó acuerdo de pago» ------------------


def _datos_de_cobros_con_acuerdo(tmp_path):
    """Copia de los datos congelados como COBROS, con `am` y `x` inventados: acuerdo_m = sí cuando el
    índice es múltiplo de 5 y resultado «Realizó acuerdo de pago» cuando lo es de 3. Solo es financiación
    el que cumple las DOS cosas (múltiplo de 15)."""
    import json

    destino = tmp_path / "cobros"
    destino.mkdir()
    esperadas = 0
    for archivo in DATOS.iterdir():
        if not archivo.is_file():
            continue
        if archivo.suffix != ".json" or not archivo.name.startswith("data"):
            (destino / archivo.name).write_bytes(archivo.read_bytes())
            continue
        d = json.loads(archivo.read_text(encoding="utf-8"))
        pts = d["pts"]
        n = len(pts["e"])
        pts["am"] = [1 if i % 5 == 0 else 0 for i in range(n)]
        pts["x"] = [1 if i % 3 == 0 else 0 for i in range(n)]
        if "dim" in d:
            d["dim"]["gests"] = ["Pago no aplicado", "Realizó acuerdo de pago"]
        (destino / archivo.name).write_text(json.dumps(d), encoding="utf-8")
    return destino


def test_una_financiacion_exige_acuerdo_m_y_el_resultado_de_acuerdo_de_pago(tmp_path):
    """«¿En qué barrio hay más financiaciones?» se contestaba con las efectivas: una financiación es
    solo la efectiva con acuerdo de pago, que es acuerdo_m = sí y resultado = «Realizó acuerdo de pago»."""
    datos = MetricsService(_datos_de_cobros_con_acuerdo(tmp_path)).datos

    assert datos.fin is not None and len(datos.fin) == len(datos)
    assert 0 < sum(datos.fin) < len(datos) // 10  # un múltiplo de 15 por archivo, no los múltiplos de 5 ni de 3


@pytest.mark.asyncio
async def test_el_ranking_por_financiaciones_cuenta_solo_las_financiaciones(tmp_path):
    metrics = MetricsService(_datos_de_cobros_con_acuerdo(tmp_path))
    r = ToolRunner(metrics)
    p = metrics.datos

    salida, _ = await r.run(
        "ranking", {"dimension": "barrio", "ordenar_por": "financiaciones", "orden": "mas", "min_ordenes": 1,
                    "mes": "todo", "limite": 3})

    esperado: dict[str, int] = {}
    for i in range(len(p)):
        if p.fin[i]:
            esperado[p.barrios[p.b[i]]] = esperado.get(p.barrios[p.b[i]], 0) + 1
    filas = salida["filas"]
    assert [f["fin"] for f in filas] == sorted(esperado.values(), reverse=True)[:3]
    assert all(f["fin"] <= f["ef"] <= f["tot"] for f in filas)  # una financiación es una efectiva, no al revés
    assert "NO son todas las efectivas" in salida["nota"]
    # El total del recorte no es la primera fila: con `limite` 3 se leía como si lo fuera.
    assert salida["total_financiaciones"] == sum(p.fin) > sum(f["fin"] for f in filas)
    total, _ = await r.run("efectividad", {"mes": "todo"})
    assert total["metricas"]["fin"] == sum(p.fin)


@pytest.mark.asyncio
async def test_sin_acuerdo_m_en_los_datos_no_se_contesta_cero_financiaciones(tmp_path):
    """Los archivos de antes del ETL nuevo no traen acuerdo_m: «0 financiaciones» sería una mentira."""
    destino = tmp_path / "cobros"
    destino.mkdir()
    for archivo in DATOS.iterdir():
        if archivo.is_file():
            (destino / archivo.name).write_bytes(archivo.read_bytes())
    r = ToolRunner(MetricsService(destino))

    salida, _ = await r.run("ranking", {"dimension": "barrio", "ordenar_por": "financiaciones"})
    total, _ = await r.run("efectividad", {})

    assert salida["error"] == "financiaciones_no_disponibles"
    assert "fin" not in total["metricas"]


@pytest.mark.asyncio
async def test_las_financiaciones_solo_existen_en_cobros(runner):
    salida, _ = await runner.run("ranking", {"dimension": "barrio", "ordenar_por": "financiaciones"})
    total, _ = await runner.run("efectividad", {})

    assert salida["error"] == "financiaciones_solo_cobros" and "fin" not in total["metricas"]

