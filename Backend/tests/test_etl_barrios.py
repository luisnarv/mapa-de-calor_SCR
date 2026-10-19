"""`unificar_barrios` del ETL: un mismo barrio no puede quedar escrito de dos maneras.

Si lo hace, el catálogo lo parte en dos y el chat y el mapa eligen una variante u otra:
«El Bosque» (13.226 gestiones) contra «EL BOSQUE» (22) dio una recomendación equivocada.
Se saltan solas si el venv del backend no tiene pandas: con el Python del sistema corren.
"""

import pytest

pd = pytest.importorskip("pandas", reason="El ETL requiere pandas.")
payload = pytest.importorskip("etl.payload", reason="El paquete etl/ no es importable.")


def barrios(*filas: tuple[str, str, bool, int]) -> pd.DataFrame:
    """Una fila por gestión: (municipio, barrio, viene_de_polígono, cuántas)."""
    return pd.DataFrame(
        [{"MUNICIPIO": m, "LOCALIDAD/BARRIO": b, "_BARRIO_POLIGONO": p}
         for m, b, p, n in filas for _ in range(n)]
    )


def resultado(d: pd.DataFrame) -> set[tuple[str, str]]:
    d = d.assign(**{"LOCALIDAD/BARRIO": payload.unificar_barrios(d)})
    return set(zip(d["MUNICIPIO"], d["LOCALIDAD/BARRIO"]))


def test_gana_la_escritura_del_poligono_aunque_sea_la_menos_frecuente():
    d = barrios(("BARRANQUILLA", "El Bosque", True, 3), ("BARRANQUILLA", "EL BOSQUE", False, 10))

    assert resultado(d) == {("BARRANQUILLA", "El Bosque")}


def test_sin_poligono_gana_la_mas_frecuente():
    """SCR no trae la columna `_BARRIO_POLIGONO`: todas cuentan como escritura cruda."""
    d = barrios(("SOLEDAD", "Los Robles", False, 2), ("SOLEDAD", "LOS ROBLES", False, 9)).drop(
        columns="_BARRIO_POLIGONO")

    assert resultado(d) == {("SOLEDAD", "LOS ROBLES")}


def test_un_empate_se_resuelve_siempre_igual():
    a = barrios(("MALAMBO", "Centro", False, 4), ("MALAMBO", "CENTRO", False, 4))
    b = a.iloc[::-1].reset_index(drop=True)

    assert resultado(a) == resultado(b)


def test_las_tildes_y_los_signos_no_parten_un_barrio():
    d = barrios(("BARANOA", "San José", True, 2), ("BARANOA", "SAN JOSE", False, 5),
                ("BARANOA", "san  jose.", False, 1))

    assert resultado(d) == {("BARANOA", "San José")}


def test_el_mismo_nombre_en_otro_municipio_no_se_funde():
    """«Centro» hay en 16 municipios: son barrios distintos."""
    d = barrios(("SOLEDAD", "Centro", True, 2), ("MALAMBO", "CENTRO", False, 2))

    assert resultado(d) == {("SOLEDAD", "Centro"), ("MALAMBO", "CENTRO")}


def test_los_nombres_sin_letras_no_se_tocan():
    """Fundir «-» con «.» sería inventar que son el mismo barrio."""
    d = barrios(("PIOJO", "-", False, 2), ("PIOJO", ".", False, 3))

    assert resultado(d) == {("PIOJO", "-"), ("PIOJO", ".")}
