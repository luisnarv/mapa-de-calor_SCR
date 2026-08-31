"""Recomendación de técnico por barrio, cruzando el archivo cargado con el
histórico. Corre contra `tests/datos/`, el recorte congelado — mismo criterio
que `test_recortes.py`: las cifras no deberían cambiar."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.carga_ordenes import Cargue, Orden
from app.services.cargue_store import CargueGuardado
from app.services.metrics_service import MetricsService
from app.services.tools import ToolRunner

DATOS = Path(__file__).resolve().parent / "datos"

pytestmark = pytest.mark.skipif(
    not (DATOS / "data.json").is_file(),
    reason="Falta el payload congelado: corre tests/datos/generar.py.",
)


def cargue_con(*, bkeys: dict[str, int], tecnico: str = "SIN ASIGNAR AUN") -> CargueGuardado:
    """Arma un cargue con N órdenes por bkey, todas de un mismo `tecnico` salvo
    que la prueba pase uno distinto por fila."""
    ordenes = []
    n = 1
    for bkey, cuantas in bkeys.items():
        for _ in range(cuantas):
            ordenes.append(Orden(orden=str(n), nic=str(n), tecnico=tecnico, bkey=bkey))
            n += 1
    cargue = Cargue(ordenes=ordenes, fila_cabecera=1, leidas=len(ordenes))
    return CargueGuardado(id="x", archivo="prueba.xlsx", cargue=cargue, creado=datetime.now(timezone.utc))


class StoreFalso:
    def __init__(self, guardado: CargueGuardado) -> None:
        self.guardado = guardado

    def obtener(self, id_):
        return self.guardado


def runner_con(guardado: CargueGuardado) -> ToolRunner:
    r = ToolRunner(MetricsService(DATOS), cargues=StoreFalso(guardado))
    r.cargue_id = "x"
    return r


@pytest.mark.asyncio
async def test_recomienda_al_de_mejor_efectividad_historica_en_ese_barrio():
    guardado = cargue_con(bkeys={"BARRANQUILLA | LAS MALVINAS": 4})
    salida, filtro = await runner_con(guardado).run("recomendar_tecnicos", {"min_ordenes": 10})

    rec = salida["recomendaciones"][0]
    assert rec["barrio"] == "BARRANQUILLA | LAS MALVINAS"
    assert rec["ordenes_pendientes_en_el_archivo"] == 4
    assert rec["tecnico_recomendado"] == "LUIS JAVIER TORRENEGRA MOLINA"
    assert rec["efectividad_ajustada_historica"] == 100.0
    assert rec["ampliado_al_municipio"] is False
    assert filtro is None, "el mapa no sabe filtrar por esta combinación; se queda quieto"


@pytest.mark.asyncio
async def test_dice_cuando_la_recomendacion_es_del_municipio_y_no_del_barrio():
    """Villa Sabita no tiene ningún técnico con 10+ órdenes propias: se sube a
    todo Galapa. Ocultarlo haría pasar la ampliación por un dato del barrio."""
    guardado = cargue_con(bkeys={"GALAPA | VILLA SABITA": 2})
    salida, _ = await runner_con(guardado).run("recomendar_tecnicos", {"min_ordenes": 10})

    rec = salida["recomendaciones"][0]
    assert rec["ampliado_al_municipio"] is True
    assert rec["tecnico_recomendado"] == "EDINSON FONTALVO VIZCAINO"


@pytest.mark.asyncio
async def test_sin_ningun_tecnico_que_alcance_el_minimo_lo_dice_y_no_inventa():
    guardado = cargue_con(bkeys={"GALAPA | VILLA SABITA": 1})
    salida, _ = await runner_con(guardado).run("recomendar_tecnicos", {"min_ordenes": 100_000})

    assert salida["recomendaciones"] == []
    assert salida["sin_historial_suficiente"] == ["GALAPA | VILLA SABITA"]


@pytest.mark.asyncio
async def test_detecta_si_el_recomendado_ya_esta_cargado_en_el_archivo():
    """El mismo técnico, con tildes y mayúsculas distintas a como lo tiene el
    histórico: si no se reconoce, la sobrecarga pasa inadvertida."""
    guardado = cargue_con(
        bkeys={"BARRANQUILLA | LAS MALVINAS": 6},
        tecnico="luis javier torrenegra molina",
    )
    salida, _ = await runner_con(guardado).run("recomendar_tecnicos", {"min_ordenes": 10})

    rec = salida["recomendaciones"][0]
    assert rec["tecnico_recomendado"] == "LUIS JAVIER TORRENEGRA MOLINA"
    assert rec["ordenes_del_tecnico_en_el_archivo"] == 6


@pytest.mark.asyncio
async def test_se_puede_pedir_un_solo_barrio_del_archivo():
    guardado = cargue_con(bkeys={"BARRANQUILLA | LAS MALVINAS": 4, "GALAPA | VILLA SABITA": 1})
    salida, _ = await runner_con(guardado).run(
        "recomendar_tecnicos", {"min_ordenes": 10, "barrio": "villa sabita"}
    )

    assert len(salida["recomendaciones"]) == 1
    assert salida["recomendaciones"][0]["barrio"] == "GALAPA | VILLA SABITA"


@pytest.mark.asyncio
async def test_un_barrio_que_no_esta_en_el_archivo_da_error_claro():
    guardado = cargue_con(bkeys={"BARRANQUILLA | LAS MALVINAS": 4})
    salida, _ = await runner_con(guardado).run(
        "recomendar_tecnicos", {"barrio": "un barrio que no existe en el archivo"}
    )

    assert salida["error"] == "barrio_sin_ordenes_en_el_archivo"


@pytest.mark.asyncio
async def test_sin_archivo_cargado_dice_que_hacer():
    r = ToolRunner(MetricsService(DATOS), cargues=None)

    salida, _ = await r.run("recomendar_tecnicos", {})

    assert "sugerencia" in salida
