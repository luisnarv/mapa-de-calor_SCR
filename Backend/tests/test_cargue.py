"""Subida del archivo de órdenes, su almacén y las herramientas que lo consultan."""

import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.deps import get_cargue_store
from app.main import app
from app.services.carga_ordenes import leer_ordenes
from app.services.cargue_store import CargueStore
from app.services.openai_service import OpenAIService
from app.services.tools import ToolRunner

CABECERA = [
    "Num_Lote", "Técnico", "Orden", "Tip_Orden", "NIC", "Municipio", "Localidad",
    "Direccion", "Nombre Cliente", "Tarifa", "Deuda Vencida", "Facturas Vencidas",
    "Antiguedad", "Estado", "Comentario", "Tipo_Suspension(SCR)", "Estado del Servicio",
]


def fila(orden="1", tecnico="ACOSTA MENDOZA JOEL", barrio="EL CORTIZO", deuda=1000.0,
         antiguedad=14, tarifa="RESIDENCIAL | ESTRATO 2", facturas=3, estado="Asignada"):
    return ["1000", tecnico, orden, "TO501", f"NIC{orden}", "SOLEDAD", barrio,
            "CR 8 CL 40", "PEREZ  ANA", tarifa, deuda, facturas,
            antiguedad, estado, "Suspensión del servicio", "B - Bornera", "Activo"]


def excel(filas: list[list]) -> bytes:
    libro = Workbook()
    hoja = libro.active
    hoja.append(["REPORTE:", "ORDENES"])
    hoja.append(CABECERA)
    for datos in filas:
        hoja.append(datos)
    buffer = io.BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def cargue_de(filas: list[list]):
    return leer_ordenes(excel(filas), "ordenes.xlsx")


@pytest.fixture
def store() -> CargueStore:
    return CargueStore(ttl_minutos=60, maximo=3)


@pytest.fixture
def client(store):
    app.dependency_overrides[get_cargue_store] = lambda: store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def subir(client, datos: bytes, nombre="ordenes.xlsx"):
    return client.post(
        "/api/v1/ordenes/cargar",
        files={"archivo": (nombre, datos, "application/vnd.ms-excel")},
    )


# --- Endpoint -----------------------------------------------------------------


def test_subir_devuelve_el_id_y_el_recuento_de_lo_descartado(client):
    datos = excel([
        fila(orden="1"),
        fila(orden="2", barrio="CENTRO"),
        fila(orden="3", tecnico="No asignado"),
    ])

    cuerpo = subir(client, datos).json()

    assert cuerpo["id"]
    assert cuerpo["cargadas"] == 2
    assert cuerpo["leidas"] == 3
    assert cuerpo["sin_tecnico"] == 1
    assert cuerpo["tecnicos"] == 1 and cuerpo["barrios"] == 2
    assert cuerpo["deuda_total"] == pytest.approx(2000.0)


def test_un_excel_que_no_es_de_ordenes_se_rechaza_con_400(client):
    libro = Workbook()
    libro.active.append(["Producto", "Cantidad"])
    buffer = io.BytesIO()
    libro.save(buffer)

    respuesta = subir(client, buffer.getvalue(), "ventas.xlsx")

    assert respuesta.status_code == 400
    assert "órdenes" in respuesta.json()["detail"]


def test_un_archivo_sin_ninguna_orden_asignada_explica_por_que(client):
    """Es válido, pero no queda nada: el aviso tiene que decir eso, no '0 órdenes'."""
    datos = excel([fila(orden="1", tecnico="No asignado"), fila(orden="2", tecnico="No asignado")])

    respuesta = subir(client, datos)

    assert respuesta.status_code == 400
    assert "ninguna tiene técnico asignado" in respuesta.json()["detail"]


def test_el_cargue_queda_guardado_y_se_puede_recuperar(client, store):
    cuerpo = subir(client, excel([fila()])).json()

    guardado = store.obtener(cuerpo["id"])

    assert guardado is not None
    assert guardado.archivo == "ordenes.xlsx"
    assert len(guardado.cargue.ordenes) == 1


# --- Almacén ------------------------------------------------------------------


def test_un_cargue_caducado_ya_no_esta(store):
    guardado = store.guardar(cargue_de([fila()]), "ordenes.xlsx")
    object.__setattr__(guardado, "creado", guardado.creado - timedelta(hours=2))

    assert store.obtener(guardado.id) is None


def test_al_llenarse_el_almacen_se_va_el_mas_viejo(store):
    guardados = [store.guardar(cargue_de([fila()]), f"{i}.xlsx") for i in range(4)]

    assert store.obtener(guardados[0].id) is None
    assert store.obtener(guardados[3].id) is not None


def test_un_id_inventado_no_devuelve_nada(store):
    assert store.obtener("no-existe") is None


# --- Herramientas del chat ----------------------------------------------------


@pytest.fixture
def runner(store):
    """Un runner con un cargue de tres órdenes ya puesto."""
    guardado = store.guardar(
        cargue_de([
            fila(orden="1", deuda=5000.0, antiguedad=100),
            fila(orden="2", deuda=1000.0, barrio="CENTRO"),
            fila(orden="3", deuda=3000.0, tecnico="ZAMBRANO MERCADO JORGE"),
        ]),
        "ordenes.xlsx",
    )
    corredor = ToolRunner(metrics=None, cargues=store)
    corredor.cargue_id = guardado.id
    return corredor


@pytest.mark.asyncio
async def test_sin_archivo_cargado_la_herramienta_dice_que_hacer():
    corredor = ToolRunner(metrics=None, cargues=None)

    resultado, _ = await corredor.run("resumen_cargue", {})

    assert resultado["error"] == "sin_cargue"
    assert "clip" in resultado["sugerencia"]


@pytest.mark.asyncio
async def test_un_cargue_caducado_se_comporta_como_si_no_hubiera(runner, store):
    runner.cargue_id = "ya-no-existe"

    resultado, _ = await runner.run("resumen_cargue", {})

    assert resultado["error"] == "sin_cargue"


@pytest.mark.asyncio
async def test_el_resumen_agrupa_por_tecnico_y_por_barrio(runner):
    resultado, _ = await runner.run("resumen_cargue", {})

    assert resultado["total"] == 3
    assert resultado["deuda_total"] == 9000
    assert resultado["antiguedad_dias"] == {"min": 14, "mediana": 14, "max": 100}
    assert resultado["deuda_promedio"] == 3000
    assert {t["valor"]: t["ordenes"] for t in resultado["top_tecnicos"]} == {
        "ACOSTA MENDOZA JOEL": 2, "ZAMBRANO MERCADO JORGE": 1
    }
    assert {b["valor"] for b in resultado["top_barrios"]} == {
        "SOLEDAD | EL CORTIZO", "SOLEDAD | CENTRO"
    }


@pytest.mark.asyncio
async def test_el_detalle_filtra_por_tecnico_parcial_y_ordena_por_deuda(runner):
    resultado, _ = await runner.run("ordenes_cargadas", {"tecnico": "acosta"})

    assert resultado["total"] == 2
    assert [o["orden"] for o in resultado["ordenes"]] == ["1", "2"]


@pytest.mark.asyncio
async def test_el_detalle_filtra_por_barrio(runner):
    resultado, _ = await runner.run("ordenes_cargadas", {"barrio": "CENTRO"})

    assert [o["orden"] for o in resultado["ordenes"]] == ["2"]


@pytest.mark.asyncio
async def test_un_filtro_sin_resultados_lo_dice_en_vez_de_devolver_vacio(runner):
    resultado, _ = await runner.run("ordenes_cargadas", {"tecnico": "nadie"})

    assert resultado["total"] == 0
    assert "Ninguna orden" in resultado["nota"]


@pytest.mark.asyncio
async def test_el_nombre_del_cliente_nunca_sale_hacia_el_modelo(runner):
    """Es un dato personal y para decidir a quién visitar primero no aporta."""
    resultado, _ = await runner.run("ordenes_cargadas", {})

    assert all("cliente" not in o for o in resultado["ordenes"])


@pytest.mark.asyncio
async def test_un_criterio_de_orden_inventado_vuelve_como_dato_no_como_excepcion(runner):
    resultado, _ = await runner.run("ordenes_cargadas", {"ordenar_por": "lo_que_sea"})

    assert "error" in resultado


# --- Prompt de sistema --------------------------------------------------------


def servicio() -> OpenAIService:
    return OpenAIService(client=None, default_model="gpt-4o-mini", system_prompt="base")


def test_sin_cargue_el_prompt_no_anuncia_ningun_archivo():
    prompt = servicio()._prompt_de_sistema(runner=ToolRunner(metrics=None))

    assert "ARCHIVO CARGADO" not in prompt


def test_con_cargue_el_prompt_lo_anuncia_y_avisa_de_no_mezclar(runner):
    """Si el modelo no sabe que el archivo existe, responde con el histórico."""
    prompt = servicio()._prompt_de_sistema(runner=runner)

    assert "ARCHIVO CARGADO — «ordenes.xlsx»: 3 órdenes" in prompt
    assert "Nunca sumes ni promedies" in prompt


# --- Filtros, agrupación y búsqueda -------------------------------------------


@pytest.fixture
def cargue_variado(store):
    """Un cargue con estratos, deudas y antigüedades distintas."""
    guardado = store.guardar(
        cargue_de([
            fila(orden="1", tarifa="RESIDENCIAL | ESTRATO 2", deuda=500.0, facturas=2,
                 antiguedad=10),
            fila(orden="2", tarifa="RESIDENCIAL | ESTRATO 3", deuda=9000.0, facturas=12,
                 antiguedad=90, estado="Pendiente"),
            fila(orden="3", tarifa="RESIDENCIAL | ESTRATO 3 EXENTO", deuda=1500.0,
                 facturas=4, antiguedad=70, barrio="CENTRO"),
        ]),
        "ordenes.xlsx",
    )
    corredor = ToolRunner(metrics=None, cargues=store)
    corredor.cargue_id = guardado.id
    return corredor


@pytest.mark.asyncio
async def test_filtra_por_deuda_minima_y_lo_dice_en_la_base(cargue_variado):
    resultado, _ = await cargue_variado.run("ordenes_cargadas", {"deuda_min": 1000})

    assert [o["orden"] for o in resultado["ordenes"]] == ["2", "3"]
    assert "deuda de 1000 o más" in resultado["base"]


@pytest.mark.asyncio
async def test_los_filtros_se_combinan(cargue_variado):
    resultado, _ = await cargue_variado.run(
        "ordenes_cargadas", {"antiguedad_min": 60, "facturas_min": 10}
    )

    assert [o["orden"] for o in resultado["ordenes"]] == ["2"]


@pytest.mark.asyncio
async def test_filtrar_por_estrato_avisa_que_arrastro_la_variante_exenta(cargue_variado):
    """'estrato 3' también casa con 'estrato 3 exento': hay que decir qué se contó."""
    resultado, _ = await cargue_variado.run("ordenes_cargadas", {"tarifa": "estrato 3"})

    assert resultado["total"] == 2
    assert resultado["tarifas_incluidas"] == [
        "RESIDENCIAL | ESTRATO 3", "RESIDENCIAL | ESTRATO 3 EXENTO"
    ]


@pytest.mark.asyncio
async def test_el_estrato_exacto_no_arrastra_al_otro(cargue_variado):
    resultado, _ = await cargue_variado.run("ordenes_cargadas", {"tarifa": "estrato 2"})

    assert resultado["total"] == 1


@pytest.mark.asyncio
async def test_agrupar_por_tarifa_cuenta_y_suma_cada_grupo(cargue_variado):
    resultado, _ = await cargue_variado.run("agrupar_cargue", {"agrupar_por": "tarifa"})

    assert resultado["total_grupos"] == 3
    assert {g["valor"]: g["ordenes"] for g in resultado["grupos"]} == {
        "RESIDENCIAL | ESTRATO 2": 1,
        "RESIDENCIAL | ESTRATO 3": 1,
        "RESIDENCIAL | ESTRATO 3 EXENTO": 1,
    }


@pytest.mark.asyncio
async def test_agrupar_puede_ordenarse_por_deuda(cargue_variado):
    resultado, _ = await cargue_variado.run(
        "agrupar_cargue", {"agrupar_por": "barrio", "ordenar_por": "deuda"}
    )

    assert [g["valor"] for g in resultado["grupos"]] == [
        "SOLEDAD | EL CORTIZO", "SOLEDAD | CENTRO"
    ]
    assert resultado["grupos"][0]["deuda_promedio"] == 4750


@pytest.mark.asyncio
async def test_una_dimension_inventada_vuelve_como_dato(cargue_variado):
    resultado, _ = await cargue_variado.run("agrupar_cargue", {"agrupar_por": "color"})

    assert "error" in resultado


@pytest.mark.asyncio
async def test_buscar_por_numero_de_orden(cargue_variado):
    resultado, _ = await cargue_variado.run("buscar_orden", {"orden": "2"})

    assert resultado["encontradas"] == 1
    assert resultado["ordenes"][0]["deuda"] == 9000.0


@pytest.mark.asyncio
async def test_buscar_por_nic(cargue_variado):
    resultado, _ = await cargue_variado.run("buscar_orden", {"nic": "NIC3"})

    assert resultado["ordenes"][0]["orden"] == "3"


@pytest.mark.asyncio
async def test_una_orden_que_no_esta_explica_que_pudo_caerse_en_el_filtro(cargue_variado):
    """Decir 'no existe' sería falso: puede estar en el archivo pero sin técnico."""
    resultado, _ = await cargue_variado.run("buscar_orden", {"orden": "999"})

    assert resultado["encontradas"] == 0
    assert "sin técnico asignado" in resultado["nota"]


@pytest.mark.asyncio
async def test_buscar_sin_orden_ni_nic_no_devuelve_todo_el_cargue(cargue_variado):
    resultado, _ = await cargue_variado.run("buscar_orden", {})

    assert "error" in resultado
