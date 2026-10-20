"""La vista del tablero que llega con cada pregunta."""

from app.schemas.chat import VistaTablero


def test_la_vista_cuenta_todos_los_barrios_filtrados():
    vista = VistaTablero(barrios=["SOLEDAD | RENACER", "SOLEDAD | LOS CEREZOS"], meses=["2026-10"])

    assert "barrios: SOLEDAD | RENACER; SOLEDAD | LOS CEREZOS" in vista.resumen()


def test_sin_barrios_la_vista_no_menciona_barrios():
    assert "barrios" not in VistaTablero(municipio="SOLEDAD").resumen()


def test_la_vista_cuenta_los_tecnicos_elegidos():
    vista = VistaTablero(tecnicos=["ANA PEREZ - 1", "LUIS RUIZ - 2"])

    assert "técnicos: ANA PEREZ - 1; LUIS RUIZ - 2" in vista.resumen()
