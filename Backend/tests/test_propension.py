"""Pruebas del cliente de propensión de pago. Nunca tocan el servicio real:
se sustituye el transporte HTTP por uno falso (`httpx.MockTransport`)."""

import httpx
import pytest

from app.services.propension_service import PropensionService, PropensionServiceError

# Formas reales, verificadas contra el servicio corriendo — no las del README,
# que documenta una `encontrado` que la API nunca manda y un 200 donde en
# realidad responde 404.
ENCONTRADO = {
    "cuenta": 1071979,
    "probabilidad_pago_sin_intervencion": 0.99651,
    "probabilidad_pago_con_intervencion": 0.406848,
    "indice_pagador": "PAGADOR EN OBSERVACION",
    "dia_promedio_pago": 26.0,
    "motivo": None,
    "historico": [{"periodo": 202605, "pago": True, "tipo_os": "TO501", "fecha_cierre": "2026-05-28"}],
}

# El 404 real del servicio, no un 200 con encontrado:false.
CUERPO_NO_ENCONTRADO = {
    "error": "cuenta_no_encontrada",
    "mensaje": "La cuenta no existe en el universo de variables: no cruza la balanza con el cierre de mano de obra en ningún periodo.",
    "detalle": {"cuenta": 999999999, "periodo_solicitado": None, "historico": []},
}


def servicio(handler) -> PropensionService:
    cliente = httpx.AsyncClient(
        base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(handler)
    )
    return PropensionService(cliente)


@pytest.mark.asyncio
async def test_pasa_el_nic_tal_cual_como_cuenta():
    """NIC y cuenta son el mismo número: no hay traducción que hacer."""
    pedido = {}

    def handler(request: httpx.Request) -> httpx.Response:
        pedido["ruta"] = request.url.path
        return httpx.Response(200, json=ENCONTRADO)

    await servicio(handler).consultar("1071979")
    assert pedido["ruta"] == "/propension/1071979"


@pytest.mark.asyncio
async def test_una_cuenta_sin_datos_no_lanza():
    datos = await servicio(lambda r: httpx.Response(404, json=CUERPO_NO_ENCONTRADO)).consultar("999999999")
    assert datos["encontrado"] is False
    assert "no existe en el universo" in datos["motivo"]


@pytest.mark.asyncio
async def test_el_servicio_degradado_se_reporta_como_error_de_negocio():
    """503 del servicio (le faltan modelos o universo) no debe tumbar el chat."""
    with pytest.raises(PropensionServiceError):
        await servicio(lambda r: httpx.Response(503, json={"detail": "degradado"})).consultar("1")


@pytest.mark.asyncio
async def test_el_servicio_caido_se_reporta_como_error_de_negocio():
    """Sin conexión (nadie escuchando en el puerto) es el caso más probable en dev."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PropensionServiceError):
        await servicio(handler).consultar("1")


# --- A través del ToolRunner ----------------------------------------------

from app.services.tools import ToolRunner  # noqa: E402


def runner_con(handler) -> ToolRunner:
    return ToolRunner(metrics=None, propension=servicio(handler))


@pytest.mark.asyncio
async def test_el_runner_devuelve_las_dos_probabilidades_tal_cual():
    salida, filtro = await runner_con(lambda r: httpx.Response(200, json=ENCONTRADO)).run(
        "propension_pago", {"nic": "1071979"}
    )

    assert filtro is None
    assert "confiable" not in salida, "se pidió quitar esa distinción de la respuesta"
    assert salida["probabilidad_sin_intervencion"] == 0.99651
    assert salida["probabilidad_con_intervencion"] == 0.406848
    assert salida["indice_pagador"] == "PAGADOR EN OBSERVACION"
    assert salida["dia_promedio_pago"] == 26.0
    assert salida["nic"] == "1071979"


@pytest.mark.asyncio
async def test_el_dia_promedio_de_pago_puede_venir_nulo():
    """Un cliente sin pagos con fecha en su historial no tiene de dónde sacarlo."""
    sin_dia = {**ENCONTRADO, "dia_promedio_pago": None}

    salida, _ = await runner_con(lambda r: httpx.Response(200, json=sin_dia)).run(
        "propension_pago", {"nic": "1071979"}
    )

    assert salida["dia_promedio_pago"] is None


@pytest.mark.asyncio
async def test_el_runner_no_dice_0_por_ciento_si_no_hay_datos():
    salida, _ = await runner_con(lambda r: httpx.Response(404, json=CUERPO_NO_ENCONTRADO)).run(
        "propension_pago", {"nic": "999999999"}
    )

    assert salida["encontrado"] is False
    assert "no es que el cliente tenga 0%" not in salida["nota"]
    assert "no es 0%" in salida["nota"]


@pytest.mark.asyncio
async def test_el_servicio_caido_no_tumba_el_chat():
    """La regla de siempre: un proveedor externo caído se convierte en dato, no en excepción."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    salida, filtro = await runner_con(handler).run("propension_pago", {"nic": "1"})

    assert salida["error"] == "propension_no_disponible"
    assert "no es que el cliente tenga 0%" in salida["sugerencia"]
    assert filtro is None


@pytest.mark.asyncio
async def test_sin_servicio_configurado_lo_dice_en_vez_de_reventar():
    corredor = ToolRunner(metrics=None, propension=None)

    salida, _ = await corredor.run("propension_pago", {"nic": "1"})

    assert salida["error"] == "servicio_no_disponible"


def test_el_prompt_no_confunde_propension_con_facturacion_de_ises():
    """«Facturación» sigue fuera de alcance para lo interno de ISES; la
    propensión de pago de un cliente es otra cosa y sí está dentro."""
    from app.core.config import settings

    assert "propension_pago" in settings.OPENAI_SYSTEM_PROMPT
    assert "Es distinto de «facturación de ISES»" in settings.OPENAI_SYSTEM_PROMPT
