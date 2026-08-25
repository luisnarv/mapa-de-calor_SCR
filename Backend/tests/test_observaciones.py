"""Pruebas de la búsqueda de texto en las actas de visita.

Se montan sobre un payload de juguete y no sobre `app/data/`: aquí hace falta
controlar el texto para verificar el alineamiento y los recortes, cosa imposible
contra 181k actas que cambian cada vez que corre el ETL.
"""

import json
from pathlib import Path

import pytest

from app.services.metrics_service import MetricsService

# Dos barrios, dos meses, siete órdenes. Las actas imitan las reales: cabecera
# `VS:` con fecha y técnico, y la prosa del técnico al final.
BARRIOS = ["BARRANQUILLA | OLAYA", "SOLEDAD | NUEVO MILENIO"]

# (barrio, estado, acta). Estado: 0 Efectiva, 1 Fallida, 2 Perdida.
ENERO = [
    (0, 2, "VS: FECHA: 01/01/2026 ACTA: 1; TL: ; resistencia usuario agresivo no "
           "permite sacar acometida del MD ya qué va en chilena"),
    (0, 0, "VS: FECHA: 02/01/2026 ACTA: 2; SS: se suspende en bornera por red "
           "chilena, una sola fase"),
    (0, 1, "VS: FECHA: 03/01/2026 ACTA: 3; TL: ; predio enrejado, no atiende nadie"),
    (1, 2, "VS: FECHA: 04/01/2026 ACTA: 4; TL: ; RED CHILENA no se puede intervenir"),
]
FEBRERO = [
    (1, 2, "VS: FECHA: 01/02/2026 ACTA: 5; TL: ; zona con oscilación de voltaje "
           "reportada por el usuario"),
    (1, 0, "VS: FECHA: 02/02/2026 ACTA: 6; SS: suspensión aérea sin novedad"),
    (0, 1, "VS: FECHA: 03/02/2026 ACTA: 7; TL: ; poste en mal estado"),
]


def _pts(filas: list[tuple[int, int, str]]) -> dict:
    n = len(filas)
    return {
        "b": [f[0] for f in filas],
        "e": [f[1] for f in filas],
        "t": [0] * n, "g": [0] * n, "o": [0] * n, "c": [0] * n,
    }


def _montar(destino: Path, *, actas_enero: list[str] | None = None,
            actas_febrero: list[str] | None = None) -> Path:
    """Escribe un payload completo. `None` en unas actas = ese mes sin archivo."""
    destino.mkdir(parents=True, exist_ok=True)
    raiz = {
        "meta": {
            "total": len(FEBRERO),
            "total_all": len(ENERO) + len(FEBRERO),
            "lat0": 10.0, "lon0": -76.0, "generated": "2026-02-15",
            "months": [
                {"key": "2026-01", "label": "Enero de 2026", "n": len(ENERO),
                 "file": "data_2026-01.json"},
                {"key": "2026-02", "label": "Febrero de 2026", "n": len(FEBRERO),
                 "recent": True},
            ],
        },
        "dim": {
            "barrios": BARRIOS,
            "munis": ["BARRANQUILLA", "SOLEDAD"],
            "zonas": ["ATLANTICO CENTRO", "ATLANTICO NORTE"],
            "brigs": ["Brigada Tipo Liviana"],
            "tecs": ["UN TECNICO"],
            "tipos": ["TO501"],
            "causas": ["Efectiva"],
            "causa_ctrl": [1],
            "causa_fam": ["exito"],
            "b_muni": [0, 1],
            "b_zona": [0, 1],  # Olaya en el centro, Nuevo Milenio en el norte
        },
        "geo": {"bc": [[10.9, -74.8], [10.9, -74.8]]},
        "pts": _pts(FEBRERO),
    }
    (destino / "data.json").write_text(json.dumps(raiz), encoding="utf-8")
    (destino / "data_2026-01.json").write_text(
        json.dumps({"pts": _pts(ENERO)}), encoding="utf-8"
    )
    for mes, actas in (("2026-01", actas_enero), ("2026-02", actas_febrero)):
        if actas is not None:
            (destino / f"observaciones_{mes}.json").write_text(
                json.dumps({"obs": actas}), encoding="utf-8"
            )
    return destino


@pytest.fixture
def service(tmp_path: Path) -> MetricsService:
    directorio = _montar(
        tmp_path / "completo",
        actas_enero=[f[2] for f in ENERO],
        actas_febrero=[f[2] for f in FEBRERO],
    )
    return MetricsService(directorio)


# --- Búsqueda ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_encuentra_el_termino_sin_importar_tildes_ni_mayusculas(service):
    """El acta 4 lo escribe en mayúsculas y la 5 con tilde; deben contar igual."""
    r = await service.buscar_en_observaciones(texto="CHILENA")
    assert r.coincidencias == 3
    assert r.revisadas == len(ENERO) + len(FEBRERO)

    r = await service.buscar_en_observaciones(texto="oscilacion")
    assert r.coincidencias == 1


@pytest.mark.asyncio
async def test_agrupa_por_barrio_con_su_total_del_recorte(service):
    """`n` son las menciones y `tot` las órdenes del barrio: sin el segundo no se
    puede distinguir un barrio con problema de uno con mucho volumen."""
    r = await service.buscar_en_observaciones(texto="chilena")
    por_barrio = {f.barrio: f for f in r.barrios}

    assert por_barrio["BARRANQUILLA | OLAYA"].n == 2
    assert por_barrio["BARRANQUILLA | OLAYA"].tot == 4  # 3 de enero + 1 de febrero
    assert por_barrio["SOLEDAD | NUEVO MILENIO"].n == 1
    assert por_barrio["SOLEDAD | NUEVO MILENIO"].tot == 3


@pytest.mark.asyncio
async def test_el_desglose_por_estado_no_asume_que_la_mencion_es_una_falla(service):
    """Una de las tres menciones de chilena está en una orden EFECTIVA."""
    r = await service.buscar_en_observaciones(texto="chilena")
    assert r.por_estado == {"Efectiva": 1, "Perdida": 2}


@pytest.mark.asyncio
async def test_los_ejemplos_conservan_el_texto_original(service):
    """Se busca sobre el normalizado, pero al usuario se le enseña lo que escribió
    el técnico: con sus tildes y mayúsculas."""
    r = await service.buscar_en_observaciones(texto="chilena", n_ejemplos=3)
    assert len(r.ejemplos) == 3
    assert any("RED CHILENA" in e for e in r.ejemplos)
    assert any("qué va en chilena" in e for e in r.ejemplos)


@pytest.mark.asyncio
async def test_un_termino_que_no_esta_devuelve_cero_pero_dice_cuanto_miro(service):
    r = await service.buscar_en_observaciones(texto="transformador")
    assert r.coincidencias == 0
    assert r.revisadas == 7  # el 0 es del término, no de que no haya datos


# --- Recortes ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_el_filtro_por_barrio_recorta_menciones_y_denominador(service):
    r = await service.buscar_en_observaciones(
        texto="chilena", bkeys=["SOLEDAD | NUEVO MILENIO"]
    )
    assert r.coincidencias == 1
    assert r.revisadas == 3
    assert [f.barrio for f in r.barrios] == ["SOLEDAD | NUEVO MILENIO"]


@pytest.mark.asyncio
async def test_el_desglose_por_zona_sale_sin_pedirlo(service):
    """«Cuáles son las zonas con más X» se contesta sin filtrar: el resultado ya
    trae las zonas agrupadas."""
    r = await service.buscar_en_observaciones(texto="chilena")
    por_zona = {f.zona: f for f in r.zonas}

    assert por_zona["ATLANTICO CENTRO"].n == 2  # las dos de Olaya
    assert por_zona["ATLANTICO NORTE"].n == 1
    assert por_zona["ATLANTICO CENTRO"].tot == 4
    assert [f.zona for f in r.zonas] == ["ATLANTICO CENTRO", "ATLANTICO NORTE"]


@pytest.mark.asyncio
async def test_el_filtro_por_zona_recorta(service):
    r = await service.buscar_en_observaciones(texto="chilena", zona="ATLANTICO NORTE")
    assert r.coincidencias == 1
    assert r.revisadas == 3
    assert [f.barrio for f in r.barrios] == ["SOLEDAD | NUEVO MILENIO"]


@pytest.mark.asyncio
async def test_cada_barrio_dice_a_que_zona_pertenece(service):
    r = await service.buscar_en_observaciones(texto="chilena")
    zonas = {f.barrio: f.zona for f in r.barrios}
    assert zonas["BARRANQUILLA | OLAYA"] == "ATLANTICO CENTRO"
    assert zonas["SOLEDAD | NUEVO MILENIO"] == "ATLANTICO NORTE"


@pytest.mark.asyncio
async def test_el_filtro_por_mes_recorta(service):
    r = await service.buscar_en_observaciones(texto="chilena", meses=["2026-01"])
    assert r.coincidencias == 3
    assert r.revisadas == 4


@pytest.mark.asyncio
async def test_un_filtro_que_no_resuelve_devuelve_vacio_y_no_el_total(service):
    """Devolver el total sin filtrar haría que el usuario lea como suya una cifra
    de todo el Atlántico."""
    r = await service.buscar_en_observaciones(texto="chilena", municipio="MEDELLIN")
    assert r.coincidencias == 0 and r.revisadas == 0


@pytest.mark.asyncio
async def test_el_cero_de_un_filtro_roto_se_distingue_del_cero_de_verdad(service):
    """Los dos devuelven 0, pero significan cosas opuestas: «aquí no pasa nada» y
    «no entendí dónde». Sin `sin_resolver` el chat responde lo primero."""
    roto = await service.buscar_en_observaciones(texto="chilena", zona="ATLANTICO ESTE")
    assert roto.coincidencias == 0
    assert roto.sin_resolver == "ATLANTICO ESTE"

    real = await service.buscar_en_observaciones(texto="transformador")
    assert real.coincidencias == 0
    assert real.sin_resolver is None


# --- Integridad ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_un_mes_sin_actas_queda_fuera_y_se_avisa(tmp_path: Path):
    """Contarlo como revisado diluiría el porcentaje; callarlo daría un conteo
    parcial con pinta de completo."""
    directorio = _montar(
        tmp_path / "sin_enero", actas_enero=None, actas_febrero=[f[2] for f in FEBRERO]
    )
    r = await MetricsService(directorio).buscar_en_observaciones(texto="chilena")

    assert r.meses_sin_texto == ["2026-01"]
    assert r.revisadas == len(FEBRERO)
    assert r.coincidencias == 0  # las tres menciones eran de enero


@pytest.mark.asyncio
async def test_un_mes_desalineado_se_omite_en_vez_de_contar_mal(tmp_path: Path):
    """Si el archivo trae menos actas que órdenes, no se sabe a cuál pertenece
    cada texto. Un conteo desalineado es peor que ninguno."""
    directorio = _montar(
        tmp_path / "roto",
        actas_enero=[f[2] for f in ENERO][:2],  # faltan dos
        actas_febrero=[f[2] for f in FEBRERO],
    )
    r = await MetricsService(directorio).buscar_en_observaciones(texto="chilena")

    assert r.meses_sin_texto == ["2026-01"]
    assert r.revisadas == len(FEBRERO)


@pytest.mark.asyncio
async def test_el_termino_vacio_no_hace_pasar_todas_las_actas(service):
    """`"" in texto` es siempre cierto: sin este corte, buscar nada devolvería
    todas las órdenes como si todas mencionaran algo."""
    with pytest.raises(ValueError):
        await service.buscar_en_observaciones(texto="   ")
