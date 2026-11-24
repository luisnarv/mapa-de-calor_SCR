"""Consulta masiva de la matriz de alcance. No toca Postgres: la sesión se sustituye."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_db
from app.main import app
from app.schemas.matriz_alcance import MAX_NICS, ConsultaMatrizRequest

FILAS = [
    {"nic": "200", "calculado_al": date(2026, 11, 13), "alcance_recomendado": "Brigada Liviana",
     "pct_recomendado": Decimal("71.5")},
    {"nic": "100", "calculado_al": date(2026, 11, 12), "alcance_recomendado": None,
     "pct_recomendado": None},
]


class SesionFalsa:
    """Devuelve FILAS filtradas por los NICs pedidos, como haría el `= ANY(:nics)`."""

    def __init__(self) -> None:
        self.parametros: dict = {}

    async def execute(self, _sentencia, parametros):
        self.parametros = parametros
        self._filas = [f for f in FILAS if f["nic"] in parametros["nics"]]
        return self

    def mappings(self):
        return self

    def all(self):
        return self._filas


@pytest.fixture
def cliente():
    sesion = SesionFalsa()

    async def _db():
        yield sesion

    app.dependency_overrides[get_db] = _db
    yield TestClient(app), sesion
    app.dependency_overrides.pop(get_db, None)


def test_devuelve_en_el_orden_pedido_y_separa_los_que_no_estan(cliente):
    c, _ = cliente
    r = c.post("/api/v1/matriz-alcance", json={"nics": ["100", "999", "200"]})
    assert r.status_code == 200
    cuerpo = r.json()
    assert [f["nic"] for f in cuerpo["resultados"]] == ["100", "200"]
    assert cuerpo["no_encontrados"] == ["999"]
    assert (cuerpo["solicitados"], cuerpo["encontrados"]) == (3, 2)


def test_solo_devuelve_el_pct_recomendado(cliente):
    c, _ = cliente
    fila = c.post("/api/v1/matriz-alcance", json={"nics": ["200"]}).json()["resultados"][0]
    # El numeric sale como número, no como texto.
    assert fila == {"nic": "200", "pct_recomendado": 71.5}


def test_un_excluido_sale_sin_recomendacion(cliente):
    c, _ = cliente
    fila = c.post("/api/v1/matriz-alcance", json={"nics": ["100"]}).json()["resultados"][0]
    assert fila == {"nic": "100", "pct_recomendado": None}


def test_acepta_numeros_quita_repetidos_y_espacios(cliente):
    c, sesion = cliente
    c.post("/api/v1/matriz-alcance", json={"nics": [200, " 200 ", "100"]})
    assert sesion.parametros["nics"] == ["200", "100"]


@pytest.mark.parametrize("nics", [[], ["12a"], [str(i) for i in range(MAX_NICS + 1)]])
def test_rechaza_lo_invalido_con_422(cliente, nics):
    c, _ = cliente
    assert c.post("/api/v1/matriz-alcance", json={"nics": nics}).status_code == 422


def test_el_error_dice_cual_nic_esta_mal():
    with pytest.raises(ValidationError, match="'12a'"):
        ConsultaMatrizRequest(nics=["100", "12a"])


def test_consulta_individual(cliente):
    c, _ = cliente
    assert c.get("/api/v1/matriz-alcance/200").json()["pct_recomendado"] == 71.5
    r = c.get("/api/v1/matriz-alcance/999")
    assert r.status_code == 404
    assert "recomendacion_aire_scr" in r.json()["detail"]
