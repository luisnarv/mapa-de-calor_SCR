"""Pruebas de las métricas contra los datos reales del ETL.

No son un doble: leen `app/data/*.json`, los mismos archivos que consume el mapa.
Eso convierte estas pruebas en la verificación de paridad que faltaba.
"""

import json

import pytest

from app.core.config import settings
from app.services.metrics_service import BarrioNoEncontrado, MetricsService
from app.services.payload_store import obtener

pytestmark = pytest.mark.skipif(
    not (settings.DATA_DIR / "data.json").is_file(),
    reason="No están los JSON del ETL en app/data/.",
)


@pytest.fixture(scope="module")
def service() -> MetricsService:
    return MetricsService(settings.DATA_DIR)


@pytest.fixture(scope="module")
def meta() -> dict:
    with open(settings.DATA_DIR / "data.json", encoding="utf-8") as fh:
        return json.load(fh)["meta"]


# --- Carga ---------------------------------------------------------------------

def test_se_cargan_todas_las_ordenes_del_manifiesto(meta):
    """Si faltara un mes, los porcentajes saldrían de un universo incompleto."""
    payload = obtener(settings.DATA_DIR)
    assert len(payload) == meta["total_all"]
    assert len(payload.meses) == len(meta["months"])


def test_todas_las_columnas_tienen_el_mismo_largo():
    p = obtener(settings.DATA_DIR)
    largos = {len(p.b), len(p.t), len(p.g), len(p.o), len(p.c), len(p.e), len(p.mes)}
    assert len(largos) == 1


def test_el_payload_se_cachea_entre_llamadas():
    assert obtener(settings.DATA_DIR) is obtener(settings.DATA_DIR)


# --- Efectividad ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_la_efectividad_global_cuadra_con_el_total(service, meta):
    r = await service.efectividad()
    assert r.tot == meta["total_all"]
    assert r.ef + r.fa + r.pe == r.tot


@pytest.mark.asyncio
async def test_la_ajustada_nunca_es_menor_que_la_cruda(service):
    """Sacar del denominador las no controlables solo puede subir el resultado."""
    r = await service.efectividad()
    assert r.ef_adj >= r.ef_pct
    assert 0 <= r.ef_pct <= 100 and 0 <= r.ef_adj <= 100


@pytest.mark.asyncio
async def test_los_meses_suman_el_total(service, meta):
    """Partir por mes no puede perder ni duplicar órdenes."""
    meses = await service.meses_disponibles()
    suma = 0
    for mes in meses:
        suma += (await service.efectividad(mes=mes)).tot
    assert suma == meta["total_all"]


@pytest.mark.asyncio
async def test_un_filtro_que_no_existe_no_devuelve_el_total(service):
    """Devolver el global sería peor: el usuario lo leería como su barrio."""
    r = await service.efectividad(bkey="BARRIO QUE NO EXISTE")
    assert r.tot == 0


# --- Rankings ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_el_ranking_de_brigadas_viene_ordenado(service):
    filas = await service.ranking(dimension="brigada")
    assert filas
    assert [f.ef_adj for f in filas] == sorted((f.ef_adj for f in filas), reverse=True)


@pytest.mark.asyncio
async def test_las_brigadas_suman_el_total(service, meta):
    filas = await service.ranking(dimension="brigada", min_ordenes=0, limite=100)
    assert sum(f.tot for f in filas) == meta["total_all"]


@pytest.mark.asyncio
async def test_peores_invierte_el_orden(service):
    mejores = await service.ranking(dimension="brigada")
    peores = await service.ranking(dimension="brigada", ascendente=True)
    assert mejores[0].nombre == peores[-1].nombre


@pytest.mark.asyncio
async def test_min_ordenes_descarta_los_grupos_chicos(service):
    filas = await service.ranking(dimension="barrio", min_ordenes=500, limite=100)
    assert all(f.tot >= 500 for f in filas)


# --- Barrios y causas ----------------------------------------------------------

@pytest.mark.asyncio
async def test_buscar_barrio_ignora_tildes_y_mayusculas(service):
    p = obtener(settings.DATA_DIR)
    _, _, nombre = p.barrios[0].partition(" | ")

    encontrados = await service.buscar_barrios(nombre.lower())

    assert any(c.barrio == nombre for c in encontrados)


@pytest.mark.asyncio
async def test_un_barrio_inventado_no_se_resuelve(service):
    with pytest.raises(BarrioNoEncontrado):
        await service.resolver_barrio("zzzz no existe zzzz")


@pytest.mark.asyncio
async def test_las_causas_solo_cuentan_lo_no_efectivo(service):
    causas = await service.causas(limite=50)
    assert causas
    assert all(c.causa != "Efectiva" for c in causas)

    r = await service.efectividad()
    assert sum(c.n for c in causas) == r.tot - r.ef
