"""Recaudo por barrio y municipio (COBROS). Corre contra un archivo inventado, no
contra los datos de producción: las cifras de aquí no cambian con el ETL diario."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import recaudo_barrio
from app.services.metrics_service import FiltroNoResuelto, MetricsService
from app.services.tools import PeriodoVacio, ToolRunner, tools_para_proceso

# [mes, municipio, barrio, plan, zona, cuentas, saldo, recaudo]. CENTRO de Barranquilla en septiembre
# se reparte en dos planes que suman lo de siempre (10 cuentas, 1000 de saldo, 300 de recaudo).
FILAS = [
    ["2026-08", "BARRANQUILLA", "CENTRO", "Multifamiliar", "ATLANTICO NORTE", 10, 1000.0, 100.0],
    ["2026-09", "BARRANQUILLA", "CENTRO", "Multifamiliar", "ATLANTICO NORTE", 6, 600.0, 200.0],
    ["2026-09", "BARRANQUILLA", "CENTRO", "Cobro Persuasivo", "ATLANTICO NORTE", 4, 400.0, 100.0],
    ["2026-09", "BARRANQUILLA", "EL TABOR", "Multifamiliar", "ATLANTICO SUR", 5, 800.0, 500.0],
    ["2026-09", "SOLEDAD", "CENTRO", "Cobro Persuasivo", "ATLANTICO NORTE", 4, 400.0, 50.0],
    ["2026-10", "PUERTO COLOMBIA", "SABANILLA", "Multifamiliar", "ATLANTICO SUR", 7, 900.0, 900.0],
]


# gestores.json: [gestor, mes, zona, municipio, plan, estado, resultado, cuentas, saldo, recaudo]
GESTORES = {
    "gestores": ["ANA PEREZ - 111", "LUIS RUIZ - 222"],
    "meses": ["2026-08", "2026-09", "2026-10"],
    "zonas": ["ATLANTICO NORTE", "ATLANTICO SUR"],
    "municipios": ["BARRANQUILLA", "SOLEDAD"],
    "planes": ["Cobro Persuasivo", "Multifamiliar"],
    "estados": ["Pago total"], "resultados": ["Efectiva con pago"],
    "provisionales": ["2026-10"],
    "filas": [
        [0, 0, 0, 0, 0, 0, 0, 10, 1000.0, 100.0],
        [0, 1, 0, 0, 0, 0, 0, 10, 1000.0, 300.0],
        [1, 1, 1, 1, 1, 0, 0, 5, 800.0, 500.0],
        [1, 2, 1, 1, 1, 0, 0, 7, 900.0, 900.0],
    ],
}


@pytest.fixture
def carpeta(tmp_path: Path) -> Path:
    destino = tmp_path / "cobros"
    destino.mkdir()
    (destino / recaudo_barrio.NOMBRE_ARCHIVO).write_text(
        json.dumps({"provisionales": ["2026-10"], "filas": FILAS}), encoding="utf-8")
    (destino / recaudo_barrio.NOMBRE_GESTORES).write_text(json.dumps(GESTORES), encoding="utf-8")
    return destino


@pytest.fixture
def tabla(carpeta: Path) -> recaudo_barrio.Tabla:
    return recaudo_barrio.cargar(carpeta)


def test_el_barrio_con_mas_recaudo_se_ordena_por_plata(tabla):
    r = recaudo_barrio.consultar(tabla)
    assert [f["nombre"] for f in r["filas"]][:2] == ["SABANILLA", "EL TABOR"]
    assert r["total_del_recorte"]["recaudo"] == 1850.0


def test_un_barrio_suma_sus_meses_y_no_se_mezcla_con_su_homonimo(tabla):
    r = recaudo_barrio.consultar(tabla, barrio="centro", municipio="Barranquilla")
    # El recaudo suma los dos meses; las cuentas son las del último, no 10 + 10.
    assert r["filas"][0]["recaudo"] == 400.0 and r["filas"][0]["cuentas"] == 10
    assert r["grupos_en_total"] == 1


def test_un_barrio_homonimo_sin_municipio_devuelve_los_dos(tabla):
    r = recaudo_barrio.consultar(tabla, barrio="Centro")
    assert {f["municipio"] for f in r["filas"]} == {"BARRANQUILLA", "SOLEDAD"}


def test_el_barrio_con_el_municipio_pegado_se_encuentra(tabla):
    for texto in ("El Tabor de Barranquilla", "BARRANQUILLA | El Tabor"):
        r = recaudo_barrio.consultar(tabla, barrio=texto)
        assert [f["nombre"] for f in r["filas"]] == ["EL TABOR"], texto


def test_por_municipio_suma_todos_sus_barrios(tabla):
    r = recaudo_barrio.consultar(tabla, agrupar_por="municipio", meses=["2026-09"])
    barranquilla = next(f for f in r["filas"] if f["nombre"] == "BARRANQUILLA")
    assert barranquilla["recaudo"] == 800.0 and barranquilla["saldo"] == 1800.0


def test_el_filtro_de_meses_recorta_y_marca_los_provisionales(tabla):
    solo_agosto = recaudo_barrio.consultar(tabla, meses=["2026-08"])
    assert solo_agosto["total_del_recorte"]["recaudo"] == 100.0 and solo_agosto["provisional"] == []
    assert recaudo_barrio.consultar(tabla, meses=["2026-10"])["provisional"] == ["2026-10"]


def test_peores_y_orden_por_otro_criterio(tabla):
    peor = recaudo_barrio.consultar(tabla, peores=True, limite=1)
    assert peor["filas"][0]["recaudo"] == 50.0
    por_saldo = recaudo_barrio.consultar(tabla, ordenar_por="saldo", limite=1)
    # El saldo es la foto del último mes del grupo, no la suma de sus meses.
    assert por_saldo["filas"][0]["saldo"] == 1000.0


def test_el_saldo_no_se_suma_entre_meses(tabla, carpeta):
    """Caso real: «¿qué gestor tiene más deuda?» sumaba las fotos de 10 meses y daba 67.904
    millones para quien en septiembre debía 9.470."""
    gestores = recaudo_barrio.cargar_gestores(carpeta)

    septiembre = recaudo_barrio.consultar_gestores(gestores, agrupar_por="gestor", meses=["2026-09"])
    todo = recaudo_barrio.consultar_gestores(gestores, agrupar_por="gestor")
    ana = next(f for f in todo["filas"] if f["nombre"] == "ANA PEREZ")

    assert ana["saldo"] == 1000.0 and ana["recaudo"] == 400.0  # recaudo sí suma: 100 + 300
    assert next(f for f in septiembre["filas"] if f["nombre"] == "ANA PEREZ")["saldo"] == 1000.0
    # Octubre es provisional: su saldo es solo lo gestionado hasta hoy, no la deuda.
    assert todo["saldo_al_cierre_de"] == "2026-09"
    octubre = recaudo_barrio.consultar_gestores(gestores, agrupar_por="gestor", meses=["2026-10"])
    assert octubre["saldo_al_cierre_de"] == "2026-10"


def test_el_recaudo_de_un_gestor_se_filtra_por_nombre(carpeta):
    gestores = recaudo_barrio.cargar_gestores(carpeta)

    r = recaudo_barrio.consultar_gestores(
        gestores, agrupar_por="gestor", meses=["2026-09"], gestores=["LUIS RUIZ - 222"])

    assert [f["nombre"] for f in r["filas"]] == ["LUIS RUIZ"] and r["filas"][0]["recaudo"] == 500.0
    with pytest.raises(FiltroNoResuelto):
        recaudo_barrio.consultar_gestores(gestores, agrupar_por="gestor", gestores=["NADIE"])


def test_un_municipio_o_barrio_inventado_no_devuelve_el_total_sin_filtrar(tabla):
    with pytest.raises(FiltroNoResuelto):
        recaudo_barrio.consultar(tabla, municipio="Atlántico")
    with pytest.raises(FiltroNoResuelto):
        recaudo_barrio.consultar(tabla, barrio="Narnia")


def test_sin_archivo_avisa_en_vez_de_dar_cero(tmp_path):
    with pytest.raises(recaudo_barrio.RecaudoNoDisponible):
        recaudo_barrio.cargar(tmp_path)


# Los barrios que conoce el mapa; «SIN BARRIO» y los inventados no están.
BARRIOS_DEL_MAPA = ["BARRANQUILLA | CENTRO", "BARRANQUILLA | EL TABOR",
                    "SOLEDAD | CENTRO", "PUERTO COLOMBIA | SABANILLA"]


# Catálogo de gestores del tablero: trae la cédula pegada, el recaudo no.
GESTORES_DEL_MAPA = ["ANA PEREZ - 111", "LUIS RUIZ - 222"]


def runner(carpeta: Path, barrios: list[str] = BARRIOS_DEL_MAPA) -> ToolRunner:
    datos = SimpleNamespace(barrios=barrios, tecs=GESTORES_DEL_MAPA, fecha_max="2026-10-15")
    # `indices_tecnico` solo lee `datos.tecs`: se usa la real en vez de reescribirla.
    metrics = SimpleNamespace(
        directorio=carpeta, datos=datos,
        indices_tecnico=lambda pedido: MetricsService.indices_tecnico(SimpleNamespace(datos=datos), pedido),
    )
    return ToolRunner(metrics)


@pytest.mark.asyncio
async def test_la_herramienta_responde_con_su_base_y_el_periodo(carpeta):
    salida, _ = await runner(carpeta).run("recaudo", {"mes": "2026-09", "limite": 1})
    assert salida["filas"][0]["nombre"] == "EL TABOR"
    assert salida["base"] == "todo el Atlántico · 2026-09"


@pytest.mark.asyncio
async def test_el_resultado_lleva_el_mapa_al_barrio_nombrado_y_a_sus_meses(carpeta):
    _, filtro = await runner(carpeta).run("recaudo", {"mes": "2026-09", "limite": 1})
    assert filtro.barrio == "BARRANQUILLA | EL TABOR" and filtro.meses == ["2026-09"]


@pytest.mark.asyncio
async def test_sin_mes_el_filtro_manda_todos_para_no_quedarse_en_el_que_hubiera(carpeta):
    _, filtro = await runner(carpeta).run("recaudo", {"limite": 1})
    assert filtro.meses == ["2026-08", "2026-09", "2026-10"]


@pytest.mark.asyncio
async def test_por_municipio_el_mapa_se_encuadra_en_el_municipio(carpeta):
    _, filtro = await runner(carpeta).run("recaudo", {"agrupar_por": "municipio", "mes": "2026-09", "limite": 1})
    assert filtro.municipio == "BARRANQUILLA" and filtro.barrio is None


@pytest.mark.asyncio
async def test_un_barrio_que_el_mapa_no_conoce_no_se_marca(carpeta):
    # Una clave inventada dejaría el tablero vacío: mejor no mover el mapa.
    _, filtro = await runner(carpeta, barrios=[]).run("recaudo", {"limite": 1})
    assert filtro is None


@pytest.mark.asyncio
async def test_un_periodo_sin_datos_avisa(carpeta):
    salida, _ = await runner(carpeta).run("recaudo", {"mes": "2025-01"})
    assert salida["error"] == "periodo_sin_datos"


@pytest.mark.asyncio
async def test_un_municipio_inventado_vuelve_como_filtro_no_reconocido(carpeta):
    salida, _ = await runner(carpeta).run("recaudo", {"municipio": "Atlántico"})
    assert salida["error"] == "filtro_no_reconocido" and salida["campo"] == "municipio"


@pytest.mark.asyncio
async def test_en_scr_la_herramienta_no_existe(tmp_path):
    scr = tmp_path / "scr"
    scr.mkdir()
    salida, _ = await runner(scr).run("recaudo", {})
    assert salida["error"] == "recaudo_solo_cobros"
    assert "recaudo" not in [t["function"]["name"] for t in tools_para_proceso("scr")]
    assert "recaudo" in [t["function"]["name"] for t in tools_para_proceso("cobros")]


# --- Recaudo por gestor, zona y plan (sale de gestores.json) --------------------

@pytest.fixture
def tabla_gestores(carpeta: Path) -> recaudo_barrio.TablaGestores:
    return recaudo_barrio.cargar_gestores(carpeta)


def test_el_gestor_con_mas_recaudo_y_sin_la_cedula_pegada(tabla_gestores):
    r = recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="gestor")

    assert [f["nombre"] for f in r["filas"]] == ["LUIS RUIZ", "ANA PEREZ"]
    assert r["filas"][0]["recaudo"] == 1400.0
    assert r["total_del_recorte"]["recaudo"] == 1800.0


def test_el_filtro_de_mes_cambia_quien_gana_y_marca_el_provisional(tabla_gestores):
    septiembre = recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="gestor", meses=["2026-09"])
    octubre = recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="gestor", meses=["2026-10"])

    assert septiembre["filas"][0]["nombre"] == "LUIS RUIZ" and septiembre["filas"][0]["recaudo"] == 500.0
    assert septiembre["provisional"] == [] and octubre["provisional"] == ["2026-10"]


def test_por_zona_y_por_plan_suman_lo_mismo_que_por_gestor(tabla_gestores):
    por_zona = recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="zona")
    por_plan = recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="plan")

    assert por_zona["filas"][0]["nombre"] == "ATLANTICO SUR"
    assert por_plan["filas"][0]["nombre"] == "Multifamiliar"
    assert por_zona["total_del_recorte"] == por_plan["total_del_recorte"]


def test_los_filtros_de_municipio_y_plan_recortan_los_gestores(tabla_gestores):
    solo_barranquilla = recaudo_barrio.consultar_gestores(
        tabla_gestores, agrupar_por="gestor", municipio="Barranquilla")
    solo_multifamiliar = recaudo_barrio.consultar_gestores(
        tabla_gestores, agrupar_por="gestor", plan="multifamiliar")

    assert [f["nombre"] for f in solo_barranquilla["filas"]] == ["ANA PEREZ"]
    assert [f["nombre"] for f in solo_multifamiliar["filas"]] == ["LUIS RUIZ"]


def test_un_plan_o_zona_inventados_no_devuelven_el_total_sin_filtrar(tabla_gestores):
    with pytest.raises(FiltroNoResuelto):
        recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="gestor", plan="Residencial")
    with pytest.raises(FiltroNoResuelto):
        recaudo_barrio.consultar_gestores(tabla_gestores, agrupar_por="gestor", zona="Atlántico")


@pytest.mark.asyncio
async def test_la_herramienta_responde_el_gestor_que_mas_recaudo_en_septiembre(carpeta):
    salida, filtro = await runner(carpeta).run("recaudo", {"agrupar_por": "gestor", "mes": "2026-09", "limite": 1})

    assert salida["filas"][0]["nombre"] == "LUIS RUIZ"
    assert salida["base"] == "todo el Atlántico · 2026-09"
    # El mapa deja marcado al gestor con el nombre del catálogo (con la cédula).
    assert filtro.tecnicos == ["LUIS RUIZ - 222"] and filtro.meses == ["2026-09"]


@pytest.mark.asyncio
async def test_por_zona_o_plan_el_mapa_se_mueve_a_la_primera_fila(carpeta):
    _, por_zona = await runner(carpeta).run("recaudo", {"agrupar_por": "zona", "mes": "2026-09", "limite": 1})
    _, por_plan = await runner(carpeta).run("recaudo", {"agrupar_por": "plan", "mes": "2026-09", "limite": 1})

    assert por_zona.zona == "ATLANTICO SUR" and por_zona.meses == ["2026-09"]
    assert por_plan.brigada == "Multifamiliar"


@pytest.mark.asyncio
async def test_pedir_gestor_con_un_barrio_avisa_que_no_se_cruzan(carpeta):
    """gestores.json llega hasta zona, municipio y plan: darle un barrio sería inventarlo."""
    salida, _ = await runner(carpeta).run("recaudo", {"agrupar_por": "gestor", "barrio": "Centro"})

    assert salida["error"] == "recaudo_gestor_no_cruza_barrio"


@pytest.mark.asyncio
async def test_un_plan_inventado_vuelve_como_filtro_no_reconocido(carpeta):
    salida, _ = await runner(carpeta).run("recaudo", {"agrupar_por": "gestor", "plan": "Residencial"})

    assert salida["error"] == "filtro_no_reconocido" and salida["campo"] == "plan"


def test_el_recaudo_de_varios_barrios_se_suma(tabla):
    r = recaudo_barrio.consultar(tabla, barrio=["El Tabor", "Sabanilla"])

    assert r["total_del_recorte"]["recaudo"] == 500.0 + 900.0
    assert {f["nombre"] for f in r["filas"]} == {"EL TABOR", "SABANILLA"}


@pytest.mark.asyncio
async def test_una_pregunta_de_totales_no_mueve_el_mapa(carpeta):
    """«¿Cuánto se recaudó en octubre?» dejaba el mapa fijo en el primer barrio del agrupado."""
    salida, filtro = await runner(carpeta).run("recaudo", {"mes": "2026-10"})

    assert salida["total_del_recorte"]["recaudo"] == 900.0 and filtro is None


@pytest.mark.asyncio
async def test_el_recaudo_de_un_gestor_nombrado_es_el_suyo_y_el_mapa_lo_marca(carpeta):
    """Caso real: «recaudo de Fulano» respondía «no hay registros» o con otro gestor, porque
    la herramienta no tenía dónde poner un nombre y lo tomaba por municipio."""
    salida, filtro = await runner(carpeta).run("recaudo", {"gestor": "ana perez", "mes": "2026-09"})

    assert salida["filas"][0]["nombre"] == "ANA PEREZ" and salida["filas"][0]["recaudo"] == 300.0
    assert filtro.tecnicos == ["ANA PEREZ - 111"] and filtro.meses == ["2026-09"]


def test_el_recaudo_sobre_saldo_es_una_razon_y_ordena(carpeta):
    gestores = recaudo_barrio.cargar_gestores(carpeta)

    r = recaudo_barrio.consultar_gestores(
        gestores, agrupar_por="gestor", meses=["2026-09"], ordenar_por="recaudo_sobre_saldo")

    # Septiembre: ANA 300 / 1000 y LUIS 500 / 800.
    assert [f["nombre"] for f in r["filas"]] == ["LUIS RUIZ", "ANA PEREZ"]
    assert r["filas"][0]["recaudo_sobre_saldo"] == round(500 / 800, 4)


def test_el_recaudo_por_gestion_divide_entre_las_gestiones_de_cada_nombre(carpeta):
    gestores = recaudo_barrio.cargar_gestores(carpeta)

    r = recaudo_barrio.consultar_gestores(
        gestores, agrupar_por="gestor", meses=["2026-09"], ordenar_por="recaudo_por_gestion",
        gestiones={"ANA PEREZ - 111": 10, "LUIS RUIZ - 222": 100})

    assert [(f["nombre"], f["recaudo_por_gestion"]) for f in r["filas"]] == [("ANA PEREZ", 30.0), ("LUIS RUIZ", 5.0)]
    sin_gestiones = recaudo_barrio.consultar_gestores(
        gestores, agrupar_por="gestor", meses=["2026-09"], ordenar_por="recaudo_por_gestion", gestiones={"ANA PEREZ - 111": 10})
    assert [f["nombre"] for f in sin_gestiones["filas"]] == ["ANA PEREZ"]  # sin gestiones no hay razón que ordenar


def test_la_variacion_une_dos_meses_y_cuenta_como_cero_al_que_no_aparece(carpeta):
    gestores = recaudo_barrio.cargar_gestores(carpeta)
    ahora = recaudo_barrio.consultar_gestores(gestores, agrupar_por="gestor", meses=["2026-09"], limite=10**6)
    antes = recaudo_barrio.consultar_gestores(gestores, agrupar_por="gestor", meses=["2026-08"], limite=10**6)

    filas = {f["nombre"]: f for f in recaudo_barrio.comparar(ahora, antes)}

    assert filas["ANA PEREZ"]["variacion_recaudo"] == 200.0 and filas["ANA PEREZ"]["variacion_saldo"] == 0.0
    assert filas["LUIS RUIZ"]["saldo_anterior"] == 0.0 and filas["LUIS RUIZ"]["variacion_saldo"] == 800.0


@pytest.mark.asyncio
async def test_la_herramienta_pide_los_dos_meses_para_una_variacion(carpeta):
    salida, _ = await runner(carpeta).run("recaudo", {"agrupar_por": "gestor", "ordenar_por": "variacion_saldo"})
    assert salida["error"] == "falta_comparar_con"

    ok, _ = await runner(carpeta).run(
        "recaudo", {"agrupar_por": "gestor", "mes": "2026-09", "comparar_con": "2026-08", "ordenar_por": "variacion_saldo"})
    assert ok["filas"][0]["nombre"] == "LUIS RUIZ" and ok["comparado_con"] == ["2026-08"]


@pytest.mark.asyncio
async def test_el_recaudo_por_gestor_sale_de_la_zona_de_la_pantalla(carpeta):
    from app.schemas.chat import VistaTablero

    r = runner(carpeta)
    r.vista = VistaTablero(zona="ATLANTICO SUR")

    salida, _ = await r.run("recaudo", {"agrupar_por": "gestor", "mes": "2026-09"})

    # Septiembre, zona sur: solo LUIS RUIZ (500). ANA PEREZ trabaja en la zona norte.
    assert [f["nombre"] for f in salida["filas"]] == ["LUIS RUIZ"]
    assert salida["filtros_de_pantalla"] == {"zona": "ATLANTICO SUR"}


@pytest.mark.asyncio
async def test_el_recaudo_por_barrio_sale_de_la_zona_y_el_plan_de_la_pantalla(carpeta):
    from app.schemas.chat import VistaTablero

    r = runner(carpeta)
    r.vista = VistaTablero(zona="ATLANTICO NORTE", brigada="Multifamiliar")

    salida, _ = await r.run("recaudo", {"mes": "2026-09"})

    assert salida["filtros_de_pantalla"] == {"zona": "ATLANTICO NORTE", "plan": "Multifamiliar"}
    assert [(f["nombre"], f["recaudo"]) for f in salida["filas"]] == [("CENTRO", 200.0)]
    sin_filtros, _ = await r.run("recaudo", {"mes": "2026-09", "sin_filtros_de_pantalla": True})
    assert "filtros_de_pantalla" not in sin_filtros and len(sin_filtros["filas"]) == 3


def test_el_barrio_con_mas_recaudo_en_un_plan_y_una_zona(tabla):
    """«El barrio con más recaudo en el plan Multifamiliar de la zona norte»: antes no se podía
    contestar, porque el recaudo por barrio no traía plan ni zona."""
    r = recaudo_barrio.consultar(
        tabla, meses=["2026-09"], plan="multifamiliar", zona="atlantico norte", ordenar_por="recaudo", limite=1)

    assert (r["filas"][0]["nombre"], r["filas"][0]["recaudo"]) == ("CENTRO", 200.0)
    # Sin filtros, el barrio es la suma de sus planes: lo de siempre.
    todo = recaudo_barrio.consultar(tabla, meses=["2026-09"], barrio="Centro", municipio="Barranquilla")
    assert todo["filas"][0]["recaudo"] == 300.0 and todo["filas"][0]["cuentas"] == 10
    with pytest.raises(FiltroNoResuelto):
        recaudo_barrio.consultar(tabla, plan="Inventado")


def test_el_recaudo_sobre_saldo_de_un_plan_usa_solo_las_cuentas_de_ese_plan(tabla):
    r = recaudo_barrio.consultar(
        tabla, meses=["2026-09"], plan="Cobro Persuasivo", zona="ATLANTICO NORTE",
        ordenar_por="recaudo_sobre_saldo", municipio="Barranquilla")

    assert r["filas"][0]["nombre"] == "CENTRO"
    assert r["filas"][0]["recaudo_sobre_saldo"] == round(100.0 / 400.0, 4)


def test_el_archivo_anterior_sin_plan_ni_zona_se_sigue_leyendo(tmp_path):
    destino = tmp_path / "cobros"
    destino.mkdir()
    (destino / recaudo_barrio.NOMBRE_ARCHIVO).write_text(json.dumps({
        "provisionales": [], "filas": [["2026-09", "SOLEDAD", "CENTRO", 4, 400.0, 50.0]]}), encoding="utf-8")
    tabla_vieja = recaudo_barrio.cargar(destino)

    assert recaudo_barrio.consultar(tabla_vieja)["filas"][0]["recaudo"] == 50.0
    with pytest.raises(recaudo_barrio.RecaudoNoDisponible, match="Corre el ETL"):
        recaudo_barrio.consultar(tabla_vieja, plan="Multifamiliar")


@pytest.mark.asyncio
async def test_la_herramienta_contesta_el_barrio_de_un_plan_y_una_zona(carpeta):
    salida, _ = await runner(carpeta).run(
        "recaudo", {"mes": "2026-09", "plan": "Multifamiliar", "zona": "ATLANTICO SUR", "limite": 1})

    assert "error" not in salida and salida["filas"][0]["nombre"] == "EL TABOR"


def test_sin_barrio_no_se_recomienda_como_barrio_pero_su_plata_sigue_en_el_total(tmp_path):
    """Caso real: «recomiéndame un barrio con mayor porcentaje de pagos» devolvió «SIN BARRIO» de
    Suan con 7.331 %, porque sus pocas cuentas sin GPS debían muy poco y habían pagado más."""
    destino = tmp_path / "cobros"
    destino.mkdir()
    (destino / recaudo_barrio.NOMBRE_ARCHIVO).write_text(json.dumps({"provisionales": [], "filas": [
        ["2026-09", "SUAN", "SIN BARRIO", "Multifamiliar", "ATLANTICO SUR", 2, 10.0, 733.0],
        ["2026-09", "SUAN", "CENTRO", "Multifamiliar", "ATLANTICO SUR", 50, 1000.0, 300.0],
        ["2026-09", "MALAMBO", "EL CARMEN", "Multifamiliar", "ATLANTICO SUR", 40, 800.0, 400.0],
    ]}), encoding="utf-8")
    tabla = recaudo_barrio.cargar(destino)

    r = recaudo_barrio.consultar(tabla, ordenar_por="recaudo_sobre_saldo", limite=5)

    assert [f["nombre"] for f in r["filas"]] == ["EL CARMEN", "CENTRO"]
    assert r["sin_barrio"] == {"saldo": 10.0, "recaudo": 733.0} and r["grupos_en_total"] == 2
    assert r["total_del_recorte"]["recaudo"] == 1433.0  # la plata de «SIN BARRIO» no se pierde
    # Por municipio sí cuenta, y si lo piden por su nombre tampoco se esconde.
    assert recaudo_barrio.consultar(tabla, agrupar_por="municipio")["filas"][0]["recaudo"] == 1033.0
    assert recaudo_barrio.consultar(tabla, barrio="SIN BARRIO")["filas"][0]["nombre"] == "SIN BARRIO"

