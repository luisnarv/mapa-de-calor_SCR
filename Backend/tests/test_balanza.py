"""Indicadores de cobro de la balanza. No tocan Postgres: la sesión se sustituye."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.ordenes import _adaptar_cobros
from app.services.balanza_service import BalanzaService, armar_indicadores, restar_meses


def fila(**kw):
    base = dict(
        cuenta=6900622, ultimo_periodo=202610, ultimo_facturado=Decimal("36510"),
        pagos=4, recaudo_anterior=True, recaudo_actual=False,
    )
    return SimpleNamespace(**{**base, **kw})


class SesionFalsa:
    def __init__(self, filas, params: list, falla: bool = False):
        self.filas, self.params, self.falla = filas, params, falla

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, _sql, params):
        if self.falla:
            raise ConnectionError("la base no responde")
        self.params.append(params)
        return self

    def all(self):
        return self.filas


def servicio(filas=(), falla=False):
    params: list = []
    return BalanzaService(lambda: SesionFalsa(list(filas), params, falla)), params


def test_restar_meses_cruza_el_anio():
    assert restar_meses(202610, 1) == 202609
    assert restar_meses(202601, 1) == 202512
    assert restar_meses(202603, 6) == 202509


@pytest.mark.parametrize(
    "facturado, marca",
    [(Decimal("29999.99"), "No gestionar"), (Decimal("30000"), "Gestionar"),
     (Decimal("-72547"), "No gestionar"), (None, None)],
)
def test_no_gestionar_por_debajo_de_30000(facturado, marca):
    assert armar_indicadores(fila(ultimo_facturado=facturado)).marca == marca


def test_recaudo_se_traduce_a_si_no():
    ind = armar_indicadores(fila())
    assert (ind.recaudo_mes_anterior, ind.recaudo_mes_actual) == ("Sí", "No")


@pytest.mark.asyncio
async def test_la_ventana_son_los_6_meses_cerrados_antes_del_actual():
    svc, params = servicio([fila()])
    await svc.indicadores(["6900622"], hoy=date(2026, 2, 15))
    assert params[0]["actual"] == 202602
    assert params[0]["anterior"] == 202601
    assert params[0]["desde"] == 202508


@pytest.mark.asyncio
async def test_devuelve_el_nic_como_llego_y_salta_los_no_numericos():
    svc, params = servicio([fila(cuenta=123)])
    r = await svc.indicadores(["00123", "ABC"], hoy=date(2026, 10, 13))
    assert params[0]["cuentas"] == [123]
    assert list(r) == ["00123"]


@pytest.mark.asyncio
async def test_base_caida_devuelve_none_sin_lanzar():
    svc, _ = servicio(falla=True)
    assert await svc.indicadores(["6900622"]) is None


def respuesta_scr(nic="6900622"):
    cand = {"nombre": "X", "ultima_orden": "2026-10-01"}
    return {"nic": nic, "tecnicos_recomendados": [dict(cand)],
            "brigadas_recomendadas": [dict(cand)]}


def test_cobros_no_trae_plan_y_si_trae_cobro():
    d = _adaptar_cobros(respuesta_scr(), {"6900622": armar_indicadores(fila())})
    assert "planes_recomendados" not in d and "brigadas_recomendadas" not in d
    assert d["gestores_recomendados"][0]["ultima_gestion"] == "2026-10-01"
    assert d["cobro"]["pagos_ultimos_6_meses"] == 4


@pytest.mark.parametrize("indicadores, aviso", [(None, "no respondió"), ({}, "no aparece")])
def test_sin_balanza_la_recomendacion_sale_igual_con_aviso(indicadores, aviso):
    d = _adaptar_cobros(respuesta_scr(), indicadores)
    assert d["cobro"] is None and aviso in d["aviso_cobro"]
    assert d["gestores_recomendados"]
