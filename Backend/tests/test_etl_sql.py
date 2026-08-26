"""Pruebas de la réplica del ETL: no tocan la base, verifican la traducción.

La comprobación que de verdad importa —que las cifras coincidan con el JSON del
ETL— exige una base y vive en `test_paridad_etl.py`, marcada como integración.
"""

import re

import pytest

from app.core import etl_sql
from app.core.taxonomy import CAUSAS, CAUSAS_NORM, HOMOLOG_BRIGADA, norm, norm_dato


# --- El espejo de la taxonomía no se debe desincronizar del ETL ---------------

def test_taxonomia_coincide_con_el_etl():
    """Falla si alguien cambia el ETL y no actualiza el espejo del backend."""
    etl = pytest.importorskip(
        "etl.taxonomy",
        reason="El paquete etl/ no es importable (requiere pandas instalado).",
    )
    assert etl.CAUSAS == CAUSAS
    assert etl.HOMOLOG_BRIGADA == HOMOLOG_BRIGADA
    assert etl.CAUSA_DEFECTO == ("Otras causas", "otros", 1)


def test_normalizacion_coincide_con_la_del_etl():
    texto = pytest.importorskip("etl.text", reason="El paquete etl/ no es importable.")
    muestras = ["SUBACCIÓN/SUBANOMALÍA", "  SCR   Pesada  ", "Villa Muvdi", "Ñuñez / Sur"]
    for m in muestras:
        assert texto.norm(m) == norm(m)
        assert texto.norm_dato(m) == norm_dato(m)


# --- Normalización -------------------------------------------------------------

@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("SUBACCIÓN/SUBANOMALÍA", "subaccion subanomalia"),
        ("  SCR   Pesada  ", "scr pesada"),
        ("EXITO - SE REQUIERE NORMALIZACION PQR", "exito se requiere normalizacion pqr"),
        (None, ""),
    ],
)
def test_norm(entrada, esperado):
    assert norm(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Villa Muvdi", "villamuvdi"),
        ("CIUDADELA 20 DE JULIO", "ciudadela20dejulio"),
        ("Ñuñez / Sur", "nunezsur"),
    ],
)
def test_norm_dato(entrada, esperado):
    assert norm_dato(entrada) == esperado


def test_las_claves_de_causas_estan_normalizadas():
    """El SQL cruza por `upper(norm(accion))`; las claves deben tener esa forma."""
    for clave in CAUSAS_NORM:
        assert clave == norm(clave).upper()


# --- CTE base ------------------------------------------------------------------

def test_base_cte_incluye_cada_regla_del_etl():
    sql, _ = etl_sql.base_cte()

    assert r"~* '^\s*v\.?\s*s\.?\s*:'" in sql, "falta el filtro VS"
    assert "DISTINCT ON (h.orden)" in sql, "falta el dedup por orden"
    assert "fecha_cierre DESC NULLS FIRST" in sql, "el dedup no replica a pandas"
    assert "dbanalitica.maestro_tarifas" in sql, "falta el cruce de Estado"
    assert "' | '" in sql, "falta la BKEY municipio | barrio"
    assert ":lat_min" in sql and ":lon_max" in sql, "falta el recorte por BBOX"


def test_base_cte_liga_toda_la_taxonomia_como_parametros():
    sql, params = etl_sql.base_cte()

    # Ni una causa ni una brigada aparecen interpoladas en el texto del SQL.
    for causa, _familia, _ctrl in CAUSAS_NORM.values():
        assert causa not in sql
    for nombre in HOMOLOG_BRIGADA.values():
        assert nombre not in sql

    assert set(CAUSAS_NORM) <= set(params.values())
    assert set(HOMOLOG_BRIGADA.values()) <= set(params.values())


def test_cada_parametro_del_sql_tiene_valor():
    """Un `:param` sin valor revienta en tiempo de ejecución, no al escribirlo."""
    sql, params = etl_sql.base_cte()
    usados = set(re.findall(r"(?<!:):([a-z_][a-z0-9_]*)", sql))
    assert usados <= set(params), f"sin valor: {usados - set(params)}"


def test_bbox_es_el_del_etl():
    _, params = etl_sql.base_cte()
    assert (params["lat_min"], params["lat_max"]) == (10.0, 11.35)
    assert (params["lon_min"], params["lon_max"]) == (-75.45, -74.35)
