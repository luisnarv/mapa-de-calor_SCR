"""Ubica en el mapa las órdenes de un cargue, que llegan sin coordenadas.

Todo sale del histórico del propio ETL. Cuatro niveles, de más a menos preciso,
y cada punto viaja con el suyo en `origen` porque no son equivalentes:

1. **NIC** — el suministro ya se visitó: es su GPS, tomado en la puerta.
2. **Dirección exacta** — otra orden en esa misma puerta. También 0 m.
3. **Cuadra** — misma vía y mismo cruce, otra placa. ~32 m.
4. **Vía** — la calle correcta, sin más. ~41 m.

Lo que no cruza por ninguno NO se pinta. Antes caía al centroide del barrio,
que está a 357 m de mediana de las órdenes reales de ese barrio: un punto a tres
cuadras con aspecto de dirección es peor que ninguno, porque nadie sospecha de
él y alguien manda una brigada. Esas órdenes salen en `no_ubicadas` para
revisarlas a mano.

Antes esto llamaba a un geocodificador externo. Se quitó porque sobre el archivo
real acertaba una de cada tres —OSM no tiene vías como «Carrera 16ASUR»— mientras
que el histórico propio cruza el 97,7%. De paso desapareció toda la maquinaria
de segundo plano: sin red, ubicar un cargue son milisegundos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.direcciones import claves
from app.services.carga_ordenes import Orden
from app.services.payload_store import Ubicaciones, obtener_ubicaciones

logger = logging.getLogger(__name__)

ORIGEN_NIC = "nic"
ORIGEN_EXACTA = "exacta"
ORIGEN_CUADRA = "cuadra"
ORIGEN_VIA = "via"

# El orden importa: se para en el primero que acierte.
NIVELES = (ORIGEN_EXACTA, ORIGEN_CUADRA, ORIGEN_VIA)


@dataclass(frozen=True, slots=True)
class Punto:
    """Una orden ya ubicada, con la procedencia de su coordenada.

    No lleva el nombre del cliente a propósito: es dato personal y este endpoint
    no tiene autenticación.
    """

    orden: str
    nic: str
    lat: float
    lon: float
    origen: str
    tecnico: str
    direccion: str | None = None
    barrio: str | None = None
    municipio: str | None = None
    tipo_os: str | None = None


@dataclass(frozen=True, slots=True)
class SinUbicar:
    """Una orden que no se pudo situar. Va a una lista, no al mapa."""

    orden: str
    nic: str
    tecnico: str
    direccion: str | None = None
    barrio: str | None = None
    municipio: str | None = None


@dataclass
class Estado:
    """Resultado de ubicar un cargue."""

    total: int
    puntos: dict[str, Punto] = field(default_factory=dict)
    no_ubicadas: list[SinUbicar] = field(default_factory=list)

    @property
    def por_origen(self) -> dict[str, int]:
        conteo = {ORIGEN_NIC: 0, ORIGEN_EXACTA: 0, ORIGEN_CUADRA: 0, ORIGEN_VIA: 0}
        for punto in self.puntos.values():
            conteo[punto.origen] += 1
        return conteo


def _punto(orden: Orden, lat: float, lon: float, origen: str) -> Punto:
    return Punto(
        orden=orden.orden,
        nic=orden.nic,
        lat=lat,
        lon=lon,
        origen=origen,
        tecnico=orden.tecnico,
        direccion=orden.direccion,
        barrio=orden.barrio,
        municipio=orden.municipio,
        tipo_os=orden.tipo_os,
    )


class Geolocalizador:
    """Ubica cargues contra el índice del ETL.

    Sin estado entre peticiones: ubicar es tan barato que no compensa guardar el
    resultado. Antes hacía falta porque la geocodificación tardaba minutos.
    """

    def __init__(self, directorio_datos: Path) -> None:
        self._directorio = directorio_datos

    def ubicar(self, ordenes: list[Orden]) -> Estado:
        """Sitúa cada orden en el nivel más preciso que tenga."""
        ubicaciones = obtener_ubicaciones(self._directorio)
        estado = Estado(total=len(ordenes))

        for orden in ordenes:
            if (punto := self._resolver(orden, ubicaciones)) is not None:
                estado.puntos[orden.orden] = punto
            else:
                estado.no_ubicadas.append(
                    SinUbicar(
                        orden=orden.orden,
                        nic=orden.nic,
                        tecnico=orden.tecnico,
                        direccion=orden.direccion,
                        barrio=orden.barrio,
                        municipio=orden.municipio,
                    )
                )

        conteo = estado.por_origen
        logger.info(
            "Cargue ubicado: %s por NIC, %s exactas, %s por cuadra, %s por vía, %s sin ubicar.",
            conteo[ORIGEN_NIC], conteo[ORIGEN_EXACTA], conteo[ORIGEN_CUADRA],
            conteo[ORIGEN_VIA], len(estado.no_ubicadas),
        )
        return estado

    @staticmethod
    def _resolver(orden: Orden, ubicaciones: Ubicaciones) -> Punto | None:
        # El NIC va primero aunque la dirección también acierte: identifica al
        # suministro, no a la puerta, y una puerta puede tener varios.
        if coordenada := ubicaciones.nic.get(orden.nic):
            return _punto(orden, *coordenada, ORIGEN_NIC)

        if not orden.direccion:
            return None
        # Se normaliza con las mismas reglas con las que el ETL armó el índice.
        llaves = claves(orden.direccion, orden.barrio)
        if llaves is None:
            return None

        for nivel, llave in zip(NIVELES, llaves):
            if coordenada := getattr(ubicaciones, nivel).get(llave):
                return _punto(orden, *coordenada, nivel)
        return None
