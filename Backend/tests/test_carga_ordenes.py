"""Lectura del archivo de órdenes: cabecera móvil, validación y limpieza."""

import io

import pytest
from openpyxl import Workbook

from app.services.carga_ordenes import ArchivoInvalido, leer_ordenes

CABECERA = [
    "Num_Lote", "Técnico", "Orden", "Tip_Orden", "NIC", "Municipio", "Localidad",
    "Direccion", "Nombre Cliente", "Tarifa", "Deuda Vencida", "Facturas Vencidas",
    "Antiguedad", "Estado", "Comentario", "Tipo_Suspension(SCR)", "Estado del Servicio",
]

# El bloque que el sistema imprime encima de la cabecera; su alto cambia.
PREAMBULO = [
    [None, "ACISES SCR - ATLANTICO CENTRO"],
    ["REPORTE:", "ORDENES"],
    ["FECHA", "14/08/2026 a las 09:30:39"],
    [],
]


def fila(tecnico="ACOSTA MENDOZA JOEL", orden="153191679", nic="8305993", **campos):
    """Una fila del export, con los valores del archivo real por defecto."""
    valores = {
        "Num_Lote": "1000", "Técnico": tecnico, "Orden": orden, "Tip_Orden": "TO501",
        "NIC": nic, "Municipio": "SOLEDAD", "Localidad": "EL CORTIZO",
        "Direccion": "CR 8 CL 40 - 42", "Nombre Cliente": "BOLIVAR  JORGE",
        "Tarifa": "RESIDENCIAL | ESTRATO 2", "Deuda Vencida": "3502129.82",
        "Facturas Vencidas": "3", "Antiguedad": "14", "Estado": "Asignada",
        "Comentario": "Suspensión del servicio de energía SUSPENSION DE BORNERA. ",
        "Tipo_Suspension(SCR)": "B - Bornera", "Estado del Servicio": "Activo",
    }
    valores.update(campos)
    return [valores[columna] for columna in CABECERA]


def excel(filas: list[list], cabecera: list[str] | None = None, preambulo=PREAMBULO) -> bytes:
    libro = Workbook()
    hoja = libro.active
    for previa in preambulo:
        hoja.append(previa)
    hoja.append(cabecera if cabecera is not None else CABECERA)
    for datos in filas:
        hoja.append(datos)
    buffer = io.BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


# --- Cabecera y validación ----------------------------------------------------


def test_la_cabecera_se_encuentra_aunque_cambie_de_fila():
    """El preámbulo del reporte no tiene alto fijo, así que no se puede asumir."""
    for alto in (0, 4, 11):
        cargue = leer_ordenes(excel([fila()], preambulo=[[]] * alto), "o.xlsx")
        assert cargue.fila_cabecera == alto + 1
        assert len(cargue.ordenes) == 1


def test_un_excel_cualquiera_se_rechaza_diciendo_que_le_falta():
    datos = excel([["x", "y"]], cabecera=["Producto", "Cantidad"], preambulo=[])

    with pytest.raises(ArchivoInvalido) as exc:
        leer_ordenes(datos, "ventas.xlsx")

    assert "no parece un export de órdenes" in str(exc.value)


def test_un_archivo_de_ordenes_al_que_le_falta_una_columna_dice_cual():
    sin_nic = [c for c in CABECERA if c != "NIC"]
    datos = excel([], cabecera=sin_nic, preambulo=[])

    with pytest.raises(ArchivoInvalido) as exc:
        leer_ordenes(datos, "ordenes.xlsx")

    assert "nic" in str(exc.value)


def test_la_cabecera_aguanta_tildes_mayusculas_y_signos():
    raro = ["NUM LOTE", "TECNICO", "orden", "TIP-ORDEN", "N.I.C.", "municipio",
            "LOCALIDAD", "Dirección", "Nombre Cliente", "Tarifa", "Deuda Vencida",
            "Facturas Vencidas", "Antigüedad", "estado", "Comentario",
            "Tipo Suspension (SCR)", "Estado del Servicio"]

    cargue = leer_ordenes(excel([fila()], cabecera=raro, preambulo=[]), "o.xlsx")

    assert len(cargue.ordenes) == 1


def test_el_formato_viejo_de_excel_explica_que_hacer():
    with pytest.raises(ArchivoInvalido, match="xlsx"):
        leer_ordenes(b"cualquier cosa", "ordenes.xls")


def test_un_archivo_danado_no_revienta_con_el_error_de_openpyxl():
    with pytest.raises(ArchivoInvalido, match="dañado"):
        leer_ordenes(b"esto no es un zip", "ordenes.xlsx")


# --- Filtro por técnico -------------------------------------------------------


def test_las_ordenes_sin_tecnico_se_descartan_y_se_cuentan():
    datos = excel([
        fila(orden="1", tecnico="No asignado"),
        fila(orden="2", tecnico="NO ASIGNADO"),
        fila(orden="3", tecnico=None),
        fila(orden="4", tecnico="ACOSTA MENDOZA JOEL"),
    ])

    cargue = leer_ordenes(datos, "o.xlsx")

    assert [o.orden for o in cargue.ordenes] == ["4"]
    assert cargue.leidas == 4
    assert cargue.descartadas["sin_tecnico"] == 3


def test_las_ordenes_repetidas_se_quedan_una_sola_vez():
    datos = excel([fila(orden="7"), fila(orden="7"), fila(orden="8")])

    cargue = leer_ordenes(datos, "o.xlsx")

    assert [o.orden for o in cargue.ordenes] == ["7", "8"]
    assert cargue.descartadas["duplicadas"] == 1


def test_las_filas_en_blanco_no_cuentan_como_descarte():
    datos = excel([fila(), [None] * len(CABECERA), []])

    cargue = leer_ordenes(datos, "o.xlsx")

    assert cargue.leidas == 1
    assert sum(cargue.descartadas.values()) == 0


# --- Limpieza de valores ------------------------------------------------------


def test_los_identificadores_no_se_vuelven_decimales():
    """Excel entrega los números como float y '153191679.0' no es una orden."""
    cargue = leer_ordenes(excel([fila(orden=153191679, nic=8305993)]), "o.xlsx")

    orden = cargue.ordenes[0]
    assert orden.orden == "153191679"
    assert orden.nic == "8305993"


def test_los_importes_quedan_numericos_y_lo_vacio_queda_en_none():
    datos = excel([
        fila(orden="1"),
        fila(orden="2", **{"Deuda Vencida": None, "Facturas Vencidas": None}),
    ])

    primera, segunda = leer_ordenes(datos, "o.xlsx").ordenes
    assert primera.deuda == pytest.approx(3502129.82)
    assert primera.facturas == 3 and primera.antiguedad == 14
    assert segunda.deuda is None and segunda.facturas is None


def test_el_bkey_queda_armado_como_lo_espera_el_historico():
    cargue = leer_ordenes(excel([fila()]), "o.xlsx")

    assert cargue.ordenes[0].bkey == "SOLEDAD | EL CORTIZO"


def test_sin_barrio_no_se_inventa_un_bkey():
    cargue = leer_ordenes(excel([fila(**{"Localidad": None})]), "o.xlsx")

    assert cargue.ordenes[0].bkey is None
    assert cargue.ordenes[0].municipio == "SOLEDAD"


def test_el_comentario_se_repara_y_se_unifica():
    """El export trae el mismo texto con la codificación rota, sin tildes y con
    o sin punto final; los tres tienen que acabar siendo el mismo valor."""
    roto = "SuspensiÃ³n del servicio de energÃ\xada SUSPENSION DE BORNERA. "
    datos = excel([
        fila(orden="1", Comentario=roto),
        fila(orden="2", Comentario=roto),
        fila(orden="3", Comentario="Suspension del servicio de energia SUSPENSION DE BORNERA"),
    ])

    comentarios = {o.comentario for o in leer_ordenes(datos, "o.xlsx").ordenes}

    assert comentarios == {"Suspensión del servicio de energía SUSPENSION DE BORNERA"}


def test_a_igual_frecuencia_gana_la_variante_con_tildes():
    datos = excel([
        fila(orden="1", Comentario="Reconexion por pago"),
        fila(orden="2", Comentario="Reconexión por pago"),
    ])

    comentarios = {o.comentario for o in leer_ordenes(datos, "o.xlsx").ordenes}

    assert comentarios == {"Reconexión por pago"}


# --- CSV ----------------------------------------------------------------------


@pytest.mark.parametrize("separador", [",", ";"])
def test_el_csv_se_lee_con_cualquiera_de_los_dos_separadores(separador):
    lineas = [separador.join(["REPORTE:", "ORDENES"]), separador.join(CABECERA)]
    for datos in (fila(orden="1"), fila(orden="2", tecnico="No asignado")):
        lineas.append(separador.join("" if v is None else str(v) for v in datos))

    cargue = leer_ordenes("\n".join(lineas).encode("utf-8"), "ordenes.csv")

    assert [o.orden for o in cargue.ordenes] == ["1"]
    assert cargue.descartadas["sin_tecnico"] == 1
