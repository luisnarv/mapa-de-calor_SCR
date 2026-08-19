"""Normalización de direcciones y su espejo con el ETL.

Las claves del índice las genera el ETL y las consulta el backend. Si las dos
copias de las reglas divergen, las claves dejan de cruzar y el mapa pierde
precisión **sin que falle nada**: las órdenes caerían al nivel de abajo o se
quedarían sin ubicar, y nadie lo notaría hasta mirar el mapa.
"""

import pytest

from app.core.direcciones import (
    ABREVIATURAS,
    _COMPLEMENTO,
    claves,
    normalizar_direccion,
)

# Formas reales del archivo de órdenes y del histórico. Sirven de corpus para
# comparar las dos copias sin depender de sus interioridades.
MUESTRAS = [
    "CR 8C CL 41 - 111 DPL BI4655",
    "CL 43A CR 1E - 32 DPL BK4947",
    "CR 1H CL 41B - 79 PISO 2 APTO 1",
    "CR 16G CL 45F - 23 BLO 1 PISO 1 APTO 01 BA701",
    "CR 16ASUR CL 45 - 95 APTO 1 BA1278",
    "CR 21BIS SUR CL 76 - 16",
    "CL 18 # 42-35 ENTR M4",
    "CR 18B # 39-66 APTO1 DB8214",
    "CL 73 CR 22 SUR - 28",
    "TR 5 CL 12 - 40",
    "CR 8 40-15",
    "CL 40 # 8-15 APTO 3",
    "KRA 38 No 45-12",
    "VIA 40 CON CALLE 79",
    "CR 12D # CL 72 - 47 TORR 20",
]


# --- El espejo no se debe desincronizar del ETL -------------------------------


def test_la_normalizacion_coincide_con_la_del_etl():
    """Falla si alguien cambia una copia y no la otra."""
    etl = pytest.importorskip(
        "etl.direcciones",
        reason="El paquete etl/ no es importable (requiere pandas instalado).",
    )
    for muestra in MUESTRAS:
        assert etl.normalizar_direccion(muestra) == normalizar_direccion(muestra), muestra


def test_las_claves_de_cruce_coinciden_con_las_del_etl():
    etl = pytest.importorskip("etl.direcciones", reason="El paquete etl/ no es importable.")
    for muestra in MUESTRAS:
        for barrio in ("EL CORTIZO", "REBOLO", None):
            assert etl.claves(muestra, barrio) == claves(muestra, barrio), (muestra, barrio)


def test_las_tablas_coinciden_con_las_del_etl():
    etl = pytest.importorskip("etl.direcciones", reason="El paquete etl/ no es importable.")
    assert etl.ABREVIATURAS == ABREVIATURAS
    assert etl._COMPLEMENTO == _COMPLEMENTO


# --- Claves de cruce ----------------------------------------------------------


def test_los_tres_niveles_van_de_mas_a_menos_preciso():
    exacta, cuadra, via = claves("CR 8C CL 41 - 111 DPL BI4655", "REBOLO")

    # 'Carrera 8C 41-111' -> 'Carrera 8C 41' -> 'Carrera 8C'
    assert exacta.endswith("carrera8c41111")
    assert cuadra.endswith("carrera8c41")
    assert via.endswith("carrera8c")


def test_el_barrio_va_en_la_clave():
    """Sin él, una orden de Soledad heredaría el GPS de una carrera 8 de Malambo."""
    en_rebolo = claves("CR 8 40-15", "REBOLO")
    en_cortizo = claves("CR 8 40-15", "EL CORTIZO")

    assert en_rebolo != en_cortizo
    assert all(a != b for a, b in zip(en_rebolo, en_cortizo))


def test_dos_placas_de_la_misma_cuadra_comparten_clave_de_cuadra():
    """Es lo que permite ubicar una dirección que no está en el histórico."""
    _, cuadra_a, via_a = claves("CR 8C CL 41 - 111", "REBOLO")
    _, cuadra_b, via_b = claves("CR 8C CL 41 - 250", "REBOLO")

    assert cuadra_a == cuadra_b
    assert via_a == via_b


def test_una_direccion_vacia_no_da_claves():
    assert claves("", "REBOLO") is None
    assert claves("   ", "REBOLO") is None
