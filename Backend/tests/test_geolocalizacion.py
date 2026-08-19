"""Ubicación de las órdenes cargadas contra el índice del ETL."""

import pytest

from app.core.direcciones import claves
from app.services import geolocalizacion as geo_mod
from app.services.carga_ordenes import Orden
from app.services.geolocalizacion import (
    ORIGEN_CUADRA,
    ORIGEN_EXACTA,
    ORIGEN_NIC,
    ORIGEN_VIA,
    Geolocalizador,
)
from app.services.payload_store import Ubicaciones

# Coordenadas de mentira pero dentro del Atlántico, distintas entre sí para que
# se vea de qué nivel salió cada punto.
GPS_NIC = (10.9500, -74.8000)
GPS_EXACTA = (10.9333, -74.7911)
GPS_CUADRA = (10.9340, -74.7920)
GPS_VIA = (10.9350, -74.7930)

DIRECCION = "CR 8 CL 40 - 15"
BARRIO = "EL CORTIZO"
# Las claves las arma `claves()`; se calculan aquí para no acoplar la prueba a
# su formato interno, que es un detalle suyo.
K_EXACTA, K_CUADRA, K_VIA = claves(DIRECCION, BARRIO)


@pytest.fixture
def ubicaciones() -> Ubicaciones:
    return Ubicaciones(
        nic={"NIC-CONOCIDO": GPS_NIC},
        barrio={},
        exacta={K_EXACTA: GPS_EXACTA},
        cuadra={K_CUADRA: GPS_CUADRA},
        via={K_VIA: GPS_VIA},
    )


@pytest.fixture(autouse=True)
def sin_leer_del_disco(monkeypatch, ubicaciones):
    """Las pruebas no dependen del payload real del ETL."""
    monkeypatch.setattr(geo_mod, "obtener_ubicaciones", lambda _dir: ubicaciones)


def orden(numero="1", nic="NIC-NUEVO", direccion=DIRECCION, barrio=BARRIO,
          municipio="SOLEDAD") -> Orden:
    bkey = f"{municipio} | {barrio}" if municipio and barrio else None
    return Orden(
        orden=numero, nic=nic, tecnico="ACOSTA JOEL", tipo_os="TO501",
        municipio=municipio, barrio=barrio, bkey=bkey, direccion=direccion,
    )


def ubicar(ordenes):
    return Geolocalizador(directorio_datos=None).ubicar(ordenes)


# --- La escalera, de más a menos preciso --------------------------------------


def test_el_nic_conocido_gana_a_todo():
    """Identifica al suministro; la dirección solo identifica a la puerta."""
    punto = ubicar([orden(nic="NIC-CONOCIDO")]).puntos["1"]

    assert (punto.lat, punto.lon) == GPS_NIC
    assert punto.origen == ORIGEN_NIC


def test_sin_nic_se_usa_la_direccion_exacta():
    punto = ubicar([orden()]).puntos["1"]

    assert (punto.lat, punto.lon) == GPS_EXACTA
    assert punto.origen == ORIGEN_EXACTA


def test_sin_direccion_exacta_se_cae_a_la_cuadra(ubicaciones):
    ubicaciones.exacta.clear()

    punto = ubicar([orden()]).puntos["1"]

    assert (punto.lat, punto.lon) == GPS_CUADRA
    assert punto.origen == ORIGEN_CUADRA


def test_sin_cuadra_se_cae_a_la_via(ubicaciones):
    ubicaciones.exacta.clear()
    ubicaciones.cuadra.clear()

    punto = ubicar([orden()]).puntos["1"]

    assert (punto.lat, punto.lon) == GPS_VIA
    assert punto.origen == ORIGEN_VIA


def test_una_placa_distinta_de_la_misma_cuadra_cruza(ubicaciones):
    """Es lo que ubica una dirección que nunca se ha visitado."""
    ubicaciones.exacta.clear()

    punto = ubicar([orden(direccion="CR 8 CL 40 - 999")]).puntos["1"]

    assert punto.origen == ORIGEN_CUADRA


# --- Lo que no cruza ----------------------------------------------------------


def test_lo_que_no_cruza_no_lleva_punto(ubicaciones):
    """Antes caía al centro del barrio, a 357 m de mediana. Peor que nada."""
    ubicaciones.exacta.clear()
    ubicaciones.cuadra.clear()
    ubicaciones.via.clear()

    estado = ubicar([orden()])

    assert estado.puntos == {}
    assert len(estado.no_ubicadas) == 1
    assert estado.no_ubicadas[0].direccion == DIRECCION


def test_una_orden_sin_direccion_no_lleva_punto():
    estado = ubicar([orden(direccion=None)])

    assert estado.puntos == {}
    assert estado.no_ubicadas[0].orden == "1"


def test_la_lista_de_no_ubicadas_no_lleva_coordenada():
    """Si la llevara, alguien la pintaría y volveríamos al punto falso."""
    sin = ubicar([orden(direccion=None)]).no_ubicadas[0]

    assert not hasattr(sin, "lat")
    assert not hasattr(sin, "lon")


def test_el_barrio_acota_el_cruce():
    """La misma dirección en otro barrio no puede heredar este GPS."""
    estado = ubicar([orden(barrio="OTRO BARRIO")])

    assert estado.puntos == {}
    assert len(estado.no_ubicadas) == 1


# --- Recuento -----------------------------------------------------------------


def test_el_recuento_por_origen_cuadra_con_los_puntos():
    estado = ubicar([orden("1", nic="NIC-CONOCIDO"), orden("2"), orden("3", direccion=None)])

    assert estado.total == 3
    assert estado.por_origen[ORIGEN_NIC] == 1
    assert estado.por_origen[ORIGEN_EXACTA] == 1
    assert len(estado.puntos) == 2
    assert len(estado.no_ubicadas) == 1


def test_sin_indice_solo_cruza_el_nic(ubicaciones):
    """Mientras el ETL no genere el índice, esto es lo que hay."""
    ubicaciones.exacta.clear()
    ubicaciones.cuadra.clear()
    ubicaciones.via.clear()

    estado = ubicar([orden("1", nic="NIC-CONOCIDO"), orden("2")])

    assert estado.por_origen[ORIGEN_NIC] == 1
    assert len(estado.no_ubicadas) == 1
