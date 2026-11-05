"""`corregimientos_por_barrio` del ETL: el filtro Ubicación necesita un corregimiento por barrio.

Si `b_corr` no queda alineado con `barrios`, el tablero asigna el corregimiento de un
barrio a otro sin avisar. Se saltan solas si el venv no tiene pandas.
"""

import pytest

pd = pytest.importorskip("pandas", reason="El ETL requiere pandas.")
payload = pytest.importorskip("etl.payload", reason="El paquete etl/ no es importable.")


def filas(*datos: tuple[int, object]) -> pd.DataFrame:
    """(índice de barrio, corregimiento) por gestión."""
    return pd.DataFrame({"b": [b for b, _ in datos], "CORREGIMIENTO": [c for _, c in datos]})


def test_b_corr_tiene_el_mismo_largo_que_barrios():
    d = filas((0, "Cascajal"), (1, "Cascajal"), (2, None))

    corrs, b_corr = payload.corregimientos_por_barrio(d, 3)

    assert len(b_corr) == 3
    assert all(0 <= i < len(corrs) for i in b_corr)


def test_nulo_y_vacio_caen_en_cabecera_municipal():
    d = filas((0, None), (1, ""), (2, "   "), (3, "Cascajal"))

    corrs, b_corr = payload.corregimientos_por_barrio(d, 4)

    assert [corrs[i] for i in b_corr] == [
        "Cabecera municipal", "Cabecera municipal", "Cabecera municipal", "Cascajal"]


def test_gana_el_corregimiento_mas_frecuente_del_barrio():
    d = filas((0, "Cascajal"), (0, "Cascajal"), (0, "Aguada"))

    corrs, b_corr = payload.corregimientos_por_barrio(d, 1)

    assert corrs[b_corr[0]] == "Cascajal"


def test_sin_la_columna_todo_es_cabecera_municipal():
    """SCR llega con la columna en NULL; un payload sin ella no debe romper."""
    d = pd.DataFrame({"b": [0, 1]})

    corrs, b_corr = payload.corregimientos_por_barrio(d, 2)

    assert corrs == ["Cabecera municipal"]
    assert b_corr == [0, 0]
