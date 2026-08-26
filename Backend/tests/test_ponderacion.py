"""Pruebas de la efectividad ponderada por volumen.

Las de `_ponderar` son puras y deterministas: no dependen de los datos del ETL,
así que no envejecen cuando se regeneran los JSON. La de integración comprueba
propiedades del orden, nunca cifras concretas, por el mismo motivo.
"""

import pytest

from app.core.config import settings
from app.services.metrics_service import PESO_PREVIO, MetricsService, _ponderar

MEDIA = 0.5  # media del conjunto en los casos de abajo, para leerlos fácil


# --- La fórmula ----------------------------------------------------------------

def test_mismo_porcentaje_gana_el_que_tiene_mas_ordenes():
    """El caso que motivó todo: 9 de 10 y 180 de 200 son ambos 90%, pero solo el
    segundo lo ha demostrado."""
    chico = _ponderar(9, 10, MEDIA)
    grande = _ponderar(180, 200, MEDIA)

    assert chico < grande
    assert chico == pytest.approx(56.7, abs=0.1)
    assert grande == pytest.approx(82.0, abs=0.1)


def test_con_mucha_muestra_el_porcentaje_real_apenas_se_toca():
    """Con 2.000 órdenes el previo pesa un 2,4%: el 90% real se respeta."""
    assert _ponderar(1800, 2000, MEDIA) == pytest.approx(90.0, abs=1.0)


def test_con_muestra_minima_el_resultado_se_pega_a_la_media():
    """Un 100% sobre 2 órdenes no puede quedar cerca de 100."""
    resultado = _ponderar(2, 2, MEDIA)
    assert abs(resultado - MEDIA * 100) < 2


def test_sin_muestra_devuelve_la_media_y_no_cero():
    """Hay barrios con TODAS sus órdenes fuera del control de la operación: su
    denominador ajustado es 0. Devolver 0 los coronaba como los peores del mes
    sin tener una sola orden que se les pudiera reprochar."""
    assert _ponderar(0, 0, MEDIA) == pytest.approx(MEDIA * 100)


def test_el_peso_previo_es_el_punto_de_equilibrio():
    """Con tantas órdenes como el peso previo, el resultado cae justo entre el
    porcentaje real y la media. Fija el significado de la constante."""
    assert _ponderar(PESO_PREVIO, PESO_PREVIO, MEDIA) == pytest.approx(
        (100 + MEDIA * 100) / 2, abs=0.1
    )


def test_un_cero_real_no_se_confunde_con_la_ausencia_de_datos():
    """0 de 40 sí es información: debe quedar por debajo de la media, no en ella."""
    assert _ponderar(0, 40, MEDIA) < MEDIA * 100


# --- En el ranking -------------------------------------------------------------

pytestmark_datos = pytest.mark.skipif(
    not (settings.DATA_DIR / "data.json").is_file(),
    reason="No están los JSON del ETL en app/data/.",
)


@pytestmark_datos
@pytest.mark.asyncio
async def test_el_ranking_ponderado_prefiere_barrios_con_historia():
    """Sin ponderar, el top se llena de barrios de 10 a 20 órdenes al 100%. Se
    compara la muestra de los dos primeros, no sus cifras, que cambian con el ETL."""
    service = MetricsService(settings.DATA_DIR)
    crudo = await service.ranking(dimension="barrio", ordenar_por="ef_pct", limite=1)
    ponderado = await service.ranking(
        dimension="barrio", ordenar_por="ef_adj_pond", limite=1
    )

    assert ponderado[0].tot > crudo[0].tot


@pytestmark_datos
@pytest.mark.asyncio
async def test_los_ponderados_solo_se_llenan_en_los_rankings():
    """Una consulta de un solo sitio no tiene conjunto contra el cual ponderar."""
    service = MetricsService(settings.DATA_DIR)
    filas = await service.ranking(dimension="barrio", ordenar_por="ef_adj_pond")
    assert all(f.ef_pond is not None and f.ef_adj_pond is not None for f in filas)

    suelta = await service.efectividad()
    assert suelta.ef_pond is None and suelta.ef_adj_pond is None
