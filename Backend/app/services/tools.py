"""Herramientas que el modelo puede invocar (tool calling).

El modelo NO escribe SQL. Elige una de estas funciones y sus argumentos; el
backend ejecuta la consulta con las reglas del ETL y le devuelve JSON para que
redacte la respuesta. Además de datos, una herramienta puede devolver una
`FiltroMapa`, que el frontend aplica al tablero.
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.chat import VistaTablero
from app.schemas.metrics import CandidatoBarrio, FiltroMapa
from app.core.taxonomy import norm_dato
from app.services.carga_ordenes import Orden
from app.services.cargue_store import CargueGuardado, CargueStore
from app.services.metrics_service import (
    BarrioAmbiguo,
    BarrioNoEncontrado,
    MetricsService,
)

logger = logging.getLogger(__name__)

# Tope de órdenes que se le devuelven al modelo de una vez. Un cargue trae
# cientos y volcarlas todas al prompt sale caro y no se lee mejor.
MAX_ORDENES = 20

# El resumen solo enseña la cabeza de cada reparto: la lista completa la da
# agrupar_cargue cuando hace falta, y así no se paga en cada pregunta.
TOPE_RESUMEN = 5

_BARRIO = {
    "type": "string",
    "description": (
        "Barrio a consultar. Si en los filtros activos de su pantalla hay uno, pasa "
        "esa clave completa tal cual ('MUNICIPIO | BARRIO'): hay barrios homónimos "
        "en varios municipios y la clave evita la ambigüedad. Si el usuario nombra "
        "otro barrio, pasa su nombre y se resuelve solo."
    ),
}
_MES = {
    "type": "string",
    "description": "Mes en formato YYYY-MM. Si se omite, se usa todo el histórico.",
}
_MUNICIPIO = {"type": "string", "description": "Nombre del municipio."}
_BRIGADA = {
    "type": "string",
    "description": (
        "Tipo de brigada, p. ej. 'Brigada Tipo Pesada' o 'Brigada Tipo Liviana'. "
        "Pásalo siempre que el usuario nombre una: sin él la cifra sale de todas."
    ),
}
_TIPO_OS = {"type": "string", "description": "Tipo de orden de servicio."}
_TARIFA = {
    "type": "string",
    "description": (
        "Tarifa o estrato, completo o parcial ('estrato 2'). Cuidado: hay variantes "
        "que se parecen ('ESTRATO 3' y 'ESTRATO 3 EXENTO' son distintas y ambas "
        "contienen 'estrato 3'). Si la cifra tiene que ser exacta, mira primero los "
        "valores con agrupar_cargue. El resultado dice siempre cuáles incluyó."
    ),
}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "efectividad",
            "description": (
                "Efectividad de un barrio, municipio o del total. Devuelve la cruda y "
                "la ajustada, más el desglose de efectivas, fallidas y perdidas. "
                "Úsala para '¿qué efectividad tiene X?'. Devuelve además `base`: "
                "el recorte exacto sobre el que se calculó, que debes citar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barrio": _BARRIO, "municipio": _MUNICIPIO, "mes": _MES,
                    "brigada": _BRIGADA, "tipo_os": _TIPO_OS,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ranking",
            "description": (
                "Ordena brigadas, técnicos o barrios por efectividad ajustada. "
                "Úsala para '¿qué brigada funciona mejor en X?' o "
                "'¿cuáles son los peores barrios?'. Si en el barrio pedido no hay "
                "muestra suficiente, amplía solo al municipio y lo avisa en `ampliado`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["brigada", "tecnico", "barrio"],
                        "description": "Qué se compara.",
                    },
                    "barrio": _BARRIO,
                    "municipio": _MUNICIPIO,
                    "mes": _MES,
                    "brigada": _BRIGADA,
                    "ordenar_por": {
                        "type": "string",
                        "enum": [
                            "ef_adj", "ef_pct", "perdidas", "pct_perdidas",
                            "fallidas", "pct_fallidas", "volumen",
                        ],
                        "description": (
                            "Criterio de orden. Por defecto ef_adj. Usa 'perdidas' "
                            "cuando pregunten dónde se pierde más: son las órdenes "
                            "que NO se cobran. 'fallidas' son las que no se "
                            "ejecutaron pero sí se pagan, que es otra cosa."
                        ),
                    },
                    "peores": {
                        "type": "boolean",
                        "description": (
                            "true para los peores. Con efectividad son los de menor "
                            "valor; con perdidas o fallidas, los de mayor. Por "
                            "defecto, los mejores."
                        ),
                    },
                    "min_ordenes": {
                        "type": "integer",
                        "description": "Mínimo de órdenes para entrar al ranking. Por defecto 10.",
                    },
                },
                "required": ["dimension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "causas_no_efectivas",
            "description": (
                "Causas de las órdenes que no fueron efectivas, de mayor a menor, "
                "indicando si cada una es controlable por la operación."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barrio": _BARRIO, "municipio": _MUNICIPIO, "mes": _MES,
                    "brigada": _BRIGADA,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_barrio",
            "description": (
                "Busca barrios por nombre parcial. Úsala cuando el usuario mencione "
                "un barrio del que no estés seguro, antes de pedir métricas."
            ),
            "parameters": {
                "type": "object",
                "properties": {"texto": {"type": "string"}},
                "required": ["texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "meses_disponibles",
            "description": "Lista los meses con datos, del más reciente al más antiguo.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filtrar_mapa",
            "description": (
                "Aplica un filtro al tablero que el usuario está viendo. No consulta "
                "datos. Úsala en dos situaciones: cuando el usuario pida ver, marcar "
                "o resaltar algo, Y por iniciativa propia cuando tu respuesta "
                "destaque UN resultado concreto (el mejor barrio, la peor brigada), "
                "para dejarlo señalado en el mapa. Si vas a dar una lista o hablar "
                "en general, no la uses: mover la vista sin que lo pidan molesta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barrio": _BARRIO,
                    "municipio": _MUNICIPIO,
                    "zona": {"type": "string"},
                    "brigada": {"type": "string"},
                    "tipo_os": {"type": "string"},
                    "meses": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resumen_cargue",
            "description": (
                "Totales del archivo de órdenes POR EJECUTAR que el usuario subió: "
                "cuántas son, la deuda total y la media por orden, el rango de "
                "antigüedad, y la cabeza del reparto por técnico, barrio, tipo de "
                "orden y tipo de suspensión. Úsala como primer paso siempre que "
                "pregunten por 'las órdenes cargadas', 'el archivo' o 'lo que subí', "
                "y para cualquier total o promedio del conjunto. No son órdenes del "
                "histórico: aún no se han hecho."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ordenes_cargadas",
            "description": (
                "Órdenes concretas del archivo cargado, con filtros, ordenadas por "
                "deuda o antigüedad. Úsala cuando pidan el detalle ('¿cuáles le tocan "
                "a Fulano?', 'las de estrato 2 con más de 5 facturas', 'las que llevan "
                "más de 60 días'). Los filtros se combinan. Devuelve un tope de filas "
                "y dice cuántas había en total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tecnico": {
                        "type": "string",
                        "description": "Nombre del técnico, completo o parcial.",
                    },
                    "barrio": {
                        "type": "string",
                        "description": (
                            "Barrio del archivo. Acepta el nombre suelto o la clave "
                            "'MUNICIPIO | BARRIO'."
                        ),
                    },
                    "tarifa": _TARIFA,
                    "estado": {
                        "type": "string",
                        "description": "Estado de la orden: Pendiente, Asignada, Comprometida…",
                    },
                    "deuda_min": {
                        "type": "number",
                        "description": "Solo las que deben esa cantidad o más, en pesos.",
                    },
                    "facturas_min": {
                        "type": "integer",
                        "description": "Solo las que acumulan ese número de facturas vencidas o más.",
                    },
                    "antiguedad_min": {
                        "type": "integer",
                        "description": "Solo las que llevan ese número de días o más sin ejecutar.",
                    },
                    "ordenar_por": {
                        "type": "string",
                        "enum": ["deuda", "antiguedad"],
                        "description": "Por defecto deuda, de mayor a menor.",
                    },
                    "limite": {
                        "type": "integer",
                        "description": f"Cuántas devolver. Por defecto y máximo {MAX_ORDENES}.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agrupar_cargue",
            "description": (
                "Reparte las órdenes cargadas por una dimensión y devuelve, de cada "
                "grupo, cuántas son y cuánta deuda acumulan. Úsala para '¿cuántas son "
                "de estrato 2?', '¿cómo se reparten por tipo de suspensión?' o '¿qué "
                "barrio concentra más deuda?'. También sirve para ver qué valores "
                "existen antes de filtrar con ordenes_cargadas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agrupar_por": {
                        "type": "string",
                        "enum": [
                            "tecnico", "barrio", "tarifa", "tipo_os",
                            "tipo_suspension", "estado",
                        ],
                        "description": "Por qué se agrupa.",
                    },
                    "ordenar_por": {
                        "type": "string",
                        "enum": ["ordenes", "deuda"],
                        "description": "Por defecto ordenes, de mayor a menor.",
                    },
                    "limite": {
                        "type": "integer",
                        "description": f"Cuántos grupos devolver. Por defecto y máximo {MAX_ORDENES}.",
                    },
                },
                "required": ["agrupar_por"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_orden",
            "description": (
                "Busca una orden concreta del archivo cargado por su número o por el "
                "NIC del suministro. Úsala para '¿dónde queda la orden 160921481?' o "
                "'¿a quién le tocó el NIC 8305993?'. Pasa al menos uno de los dos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "orden": {"type": "string", "description": "Número de la orden."},
                    "nic": {"type": "string", "description": "NIC del suministro."},
                },
            },
        },
    },
]


NOMBRES_DE_HERRAMIENTAS = frozenset(t["function"]["name"] for t in TOOLS)

# Criterios donde un valor alto es malo, no bueno.
MAYOR_ES_PEOR = frozenset({"perdidas", "pct_perdidas", "fallidas", "pct_fallidas"})


class ToolRunner:
    """Ejecuta las herramientas contra la base y acumula el filtro para el mapa."""

    def __init__(
        self,
        metrics: MetricsService,
        vista: VistaTablero | None = None,
        cargues: CargueStore | None = None,
    ) -> None:
        self.metrics = metrics
        self.vista = vista
        # El almacén y el id del archivo que subió el usuario. El id llega en el
        # cuerpo de cada turno, igual que la vista, así que se asigna después.
        self.cargues = cargues
        self.cargue_id: str | None = None

    def cargue_actual(self) -> CargueGuardado | None:
        """El archivo cargado en esta conversación, o None si no hay ninguno.

        Devuelve None también si el id caducó: para quien pregunta es lo mismo
        que no haberlo subido, y así el prompt no anuncia un archivo perdido.
        """
        if not (self.cargues and self.cargue_id):
            return None
        return self.cargues.obtener(self.cargue_id)

    def _ordenes(self) -> list[Orden] | None:
        guardado = self.cargue_actual()
        return guardado.cargue.ordenes if guardado else None

    def _pista_municipio(self) -> str | None:
        """Municipio de la pantalla, para desempatar barrios homónimos."""
        if not self.vista:
            return None
        if self.vista.municipio:
            return self.vista.municipio
        return self.vista.barrio.partition(" | ")[0] if self.vista.barrio else None

    async def _resolver(self, barrio: str) -> CandidatoBarrio:
        return await self.metrics.resolver_barrio(barrio, municipio=self._pista_municipio())

    async def _grupo(self, barrio: str) -> list[CandidatoBarrio]:
        """Los barrios que corresponden al texto: uno, o varios de un municipio.

        «Los Robles de Soledad» son diez etapas distintas en el catálogo pero un
        solo sitio para quien pregunta. Cuando todos los homónimos caen en el
        mismo municipio se devuelven juntos y la métrica los suma; devolver un
        menú de diez sería no responder.
        """
        try:
            return [await self._resolver(barrio)]
        except BarrioAmbiguo as ambiguo:
            candidatos = ambiguo.candidatos
            pista = self._pista_municipio()
            if pista:
                del_municipio = [
                    c for c in candidatos if norm_dato(c.municipio) == norm_dato(pista)
                ]
                if del_municipio:
                    return del_municipio
            if len({c.municipio for c in candidatos}) == 1:
                return candidatos
            raise  # Municipios distintos: aquí sí hay que preguntar.

    async def run(self, nombre: str, args: dict[str, Any]) -> tuple[dict[str, Any], FiltroMapa | None]:
        """Devuelve (resultado para el modelo, filtro para el mapa o None).

        Nunca lanza: los errores se devuelven como datos para que el modelo
        pueda explicarlos o repreguntar.
        """
        # Se valida contra la lista publicada, no contra los atributos: si no, un
        # nombre inventado por el modelo alcanzaría cualquier método privado.
        if nombre not in NOMBRES_DE_HERRAMIENTAS:
            return {"error": f"Herramienta desconocida: {nombre}"}, None
        try:
            return await getattr(self, f"_{nombre}")(**args)
        except BarrioNoEncontrado as exc:
            return {
                "error": "barrio_no_encontrado",
                "texto_buscado": exc.texto,
                "sugerencia": "Pide al usuario que confirme el nombre del barrio.",
            }, None
        except BarrioAmbiguo as exc:
            return {
                "error": "barrio_ambiguo",
                "texto_buscado": exc.texto,
                "candidatos": [c.model_dump() for c in exc.candidatos],
                "sugerencia": "Pregunta al usuario cuál de estos barrios quiso decir.",
            }, None
        except (TypeError, ValueError) as exc:
            # Argumentos que el modelo inventó: nombres de parámetro que no
            # existen (TypeError) o valores fuera del enum (ValueError). Vuelven
            # como dato para que reintente, no como excepción que corte el chat.
            logger.warning("Argumentos inválidos para %s: %s", nombre, exc)
            return {"error": f"Argumentos inválidos para {nombre}: {exc}"}, None

    # --- Herramientas ---------------------------------------------------------

    async def _recorte(
        self,
        barrio: str | None,
        municipio: str | None,
        mes: str | None,
        brigada: str | None = None,
    ) -> tuple[list[str] | None, str | None, str, FiltroMapa]:
        """Traduce los argumentos a un recorte concreto y a su descripción.

        La descripción viaja de vuelta al modelo en cada respuesta: es lo que le
        permite decir sobre qué calculó la cifra en vez de dar por hecho que le
        hicieron caso.
        """
        bkeys = None
        if barrio:
            grupo = await self._grupo(barrio)
            bkeys = [c.bkey for c in grupo]
            municipio = municipio or grupo[0].municipio

        if bkeys and len(bkeys) == 1:
            donde = bkeys[0]
        elif bkeys:
            donde = f"{len(bkeys)} barrios de {municipio} con ese nombre"
        else:
            donde = municipio or "todo el Atlántico"

        partes = [donde, mes or "todo el histórico"]
        if brigada:
            partes.append(f"brigada {brigada}")

        filtro = FiltroMapa(
            # El tablero solo marca un barrio a la vez; con varios se encuadra el
            # municipio, que es lo más cercano que se puede mostrar.
            barrio=bkeys[0] if bkeys and len(bkeys) == 1 else None,
            municipio=None if bkeys and len(bkeys) == 1 else municipio,
            brigada=brigada,
            meses=[mes] if mes else None,
        )
        return bkeys, municipio, " · ".join(partes), filtro

    async def _efectividad(
        self,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        brigada: str | None = None,
        tipo_os: str | None = None,
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkeys, municipio, base, filtro = await self._recorte(barrio, municipio, mes, brigada)
        datos = await self.metrics.efectividad(
            bkeys=bkeys,
            municipio=None if bkeys else municipio,
            mes=mes,
            brigada=brigada,
            tipo_os=tipo_os,
            etiqueta=base,
        )

        salida: dict[str, Any] = {
            "base": base,
            "metricas": datos.model_dump(),
            "nota": (
                "ef_pct es la efectividad cruda (la que muestra el mapa en su tooltip) "
                "y ef_adj la ajustada. Di cuál citas y sobre qué base."
            ),
        }
        if bkeys and len(bkeys) > 1:
            # Una sola pasada agrupada, no una consulta por barrio.
            detalle = await self.metrics.ranking(
                dimension="barrio", bkeys=bkeys, mes=mes, brigada=brigada,
                min_ordenes=0, limite=len(bkeys),
            )
            salida["detalle_por_barrio"] = [f.model_dump() for f in detalle]
            salida["nota_agrupacion"] = (
                f"El nombre corresponde a {len(bkeys)} barrios del catálogo y las "
                "cifras están sumadas. Dilo, y ofrece el desglose."
            )
        return salida, filtro

    async def _ranking(
        self,
        dimension: str,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        brigada: str | None = None,
        peores: bool = False,
        min_ordenes: int = 10,
        ordenar_por: str = "ef_adj",
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkeys, municipio, base, filtro = await self._recorte(barrio, municipio, mes, brigada)

        # "Peor" se invierte según el criterio: con efectividad el peor es el de
        # menor valor, pero con pérdidas o fallidas el peor es el que más tiene.
        ascendente = not peores if ordenar_por in MAYOR_ES_PEOR else peores

        async def consultar(en_barrios, en_municipio, minimo):
            return await self.metrics.ranking(
                dimension=dimension, bkeys=en_barrios, municipio=en_municipio,
                mes=mes, brigada=brigada, min_ordenes=minimo,
                ascendente=ascendente, ordenar_por=ordenar_por,
            )

        filas = await consultar(bkeys, None if bkeys else municipio, min_ordenes)

        ampliado = None
        if not filas and bkeys and municipio:
            # Igual que el panel del tablero: si el barrio no da muestra, se sube
            # al municipio. Rendirse sería inútil teniendo el dato al lado.
            filas = await consultar(None, municipio, min_ordenes)
            if filas:
                ampliado = (
                    f"En {base} no hay nadie con {min_ordenes} órdenes o más, "
                    f"así que estos son de todo {municipio}."
                )
                base = f"{municipio} · {mes or 'todo el histórico'}"

        salida = {
            "base": base,
            "dimension": dimension,
            "orden": "peor a mejor" if peores else "mejor a peor",
            "criterio": ordenar_por,
            "min_ordenes": min_ordenes,
            "filas": [f.model_dump() for f in filas],
        }
        if ampliado:
            salida["ampliado"] = ampliado
        elif not filas:
            salida["nota"] = f"Sin resultados en {base} con el mínimo pedido."
        return salida, filtro

    async def _causas_no_efectivas(
        self,
        barrio: str | None = None,
        municipio: str | None = None,
        mes: str | None = None,
        brigada: str | None = None,
    ) -> tuple[dict[str, Any], FiltroMapa | None]:
        bkeys, municipio, base, filtro = await self._recorte(barrio, municipio, mes, brigada)
        filas = await self.metrics.causas(
            bkeys=bkeys, municipio=None if bkeys else municipio, mes=mes, brigada=brigada
        )
        return {"base": base, "causas": [f.model_dump() for f in filas]}, filtro

    async def _buscar_barrio(self, texto: str) -> tuple[dict[str, Any], None]:
        candidatos = await self.metrics.buscar_barrios(texto)
        mostrados = candidatos[:12]
        salida: dict[str, Any] = {
            "total_encontrados": len(candidatos),
            "candidatos": [c.model_dump() for c in mostrados],
        }
        if len(mostrados) < len(candidatos):
            # Callar el recorte hacía que el usuario no viera su barrio en la lista.
            salida["nota"] = (
                f"Hay {len(candidatos)} en total; se muestran los {len(mostrados)} "
                "de mayor volumen. Dilo si le presentas la lista."
            )
        return salida, None

    async def _meses_disponibles(self) -> tuple[dict[str, Any], None]:
        return {"meses": await self.metrics.meses_disponibles()}, None

    async def _filtrar_mapa(self, **kwargs: Any) -> tuple[dict[str, Any], FiltroMapa]:
        barrio = kwargs.get("barrio")
        if barrio:
            kwargs["barrio"] = (await self._resolver(barrio)).bkey

        filtro = FiltroMapa(**{k: v for k, v in kwargs.items() if k in FiltroMapa.model_fields})
        return {"aplicado": filtro.model_dump(exclude_none=True)}, filtro

    # --- Herramientas sobre el archivo cargado --------------------------------

    SIN_CARGUE = {
        "error": "sin_cargue",
        "sugerencia": (
            "No hay ningún archivo de órdenes cargado en esta conversación. Pídele "
            "al usuario que lo suba con el clip que hay junto a la caja de texto."
        ),
    }

    async def _resumen_cargue(self) -> tuple[dict[str, Any], None]:
        ordenes = self._ordenes()
        if ordenes is None:
            return self.SIN_CARGUE, None

        deuda = sum(o.deuda or 0.0 for o in ordenes)
        return {
            "base": f"{len(ordenes)} órdenes por ejecutar del archivo cargado",
            "total": len(ordenes),
            "deuda_total": round(deuda),
            "deuda_promedio": round(deuda / len(ordenes)) if ordenes else 0,
            "antiguedad_dias": _rango(o.antiguedad for o in ordenes),
            # Solo la cabeza de cada reparto: para la lista entera está
            # agrupar_cargue, y volcarla aquí encarece todas las preguntas.
            "top_tecnicos": _conteo(ordenes, lambda o: o.tecnico, TOPE_RESUMEN),
            "top_barrios": _conteo(ordenes, lambda o: o.bkey, TOPE_RESUMEN),
            "por_tipo_os": _conteo(ordenes, lambda o: o.tipo_os, TOPE_RESUMEN),
            "por_tipo_suspension": _conteo(ordenes, lambda o: o.tipo_suspension, TOPE_RESUMEN),
            "nota": (
                "Son órdenes PENDIENTES: no se han ejecutado, no tienen resultado y "
                "no están en el histórico ni en el mapa. No sumes estas cifras con "
                "las del histórico. Los repartos vienen recortados; usa agrupar_cargue "
                "si necesitas la lista completa."
            ),
        }, None

    async def _ordenes_cargadas(
        self,
        tecnico: str | None = None,
        barrio: str | None = None,
        tarifa: str | None = None,
        estado: str | None = None,
        deuda_min: float | None = None,
        facturas_min: int | None = None,
        antiguedad_min: int | None = None,
        ordenar_por: str = "deuda",
        limite: int = MAX_ORDENES,
    ) -> tuple[dict[str, Any], None]:
        ordenes = self._ordenes()
        if ordenes is None:
            return self.SIN_CARGUE, None
        if ordenar_por not in ("deuda", "antiguedad"):
            raise ValueError("ordenar_por debe ser 'deuda' o 'antiguedad'")

        filtradas, base = _filtrar(
            ordenes, tecnico, barrio, tarifa, estado, deuda_min, facturas_min, antiguedad_min
        )
        if not filtradas:
            return {
                "base": base,
                "total": 0,
                "nota": "Ninguna orden del archivo cumple ese filtro.",
            }, None

        filtradas = sorted(
            filtradas,
            key=lambda o: (getattr(o, ordenar_por) or 0),
            reverse=True,
        )
        tope = max(1, min(limite, MAX_ORDENES))
        salida: dict[str, Any] = {
            "base": base,
            "total": len(filtradas),
            "deuda_total": round(sum(o.deuda or 0.0 for o in filtradas)),
            "orden": f"{ordenar_por}, de mayor a menor",
            # Sin el nombre del cliente: es un dato personal y para decidir a
            # quién se visita primero no aporta nada.
            "ordenes": [_ficha(o) for o in filtradas[:tope]],
        }
        if tarifa:
            # 'estrato 3' también casa con 'estrato 3 exento'. Decir qué se contó
            # es lo único que impide dar una cifra equivocada con cara de exacta.
            salida["tarifas_incluidas"] = sorted({o.tarifa for o in filtradas if o.tarifa})
        if len(filtradas) > tope:
            salida["nota"] = (
                f"Son {len(filtradas)} en total; aquí van las {tope} primeras. Dilo."
            )
        return salida, None

    async def _agrupar_cargue(
        self,
        agrupar_por: str,
        ordenar_por: str = "ordenes",
        limite: int = MAX_ORDENES,
    ) -> tuple[dict[str, Any], None]:
        ordenes = self._ordenes()
        if ordenes is None:
            return self.SIN_CARGUE, None

        claves = {
            "tecnico": lambda o: o.tecnico,
            "barrio": lambda o: o.bkey,
            "tarifa": lambda o: o.tarifa,
            "tipo_os": lambda o: o.tipo_os,
            "tipo_suspension": lambda o: o.tipo_suspension,
            "estado": lambda o: o.estado,
        }
        if agrupar_por not in claves:
            raise ValueError(f"agrupar_por debe ser uno de: {', '.join(claves)}")
        if ordenar_por not in ("ordenes", "deuda"):
            raise ValueError("ordenar_por debe ser 'ordenes' o 'deuda'")

        tope = max(1, min(limite, MAX_ORDENES))
        grupos = _conteo(ordenes, claves[agrupar_por], tope, ordenar_por)
        total_grupos = len({claves[agrupar_por](o) for o in ordenes} - {None})

        salida: dict[str, Any] = {
            "base": f"las {len(ordenes)} órdenes del archivo cargado, por {agrupar_por}",
            "dimension": agrupar_por,
            "orden": f"{ordenar_por}, de mayor a menor",
            "total_grupos": total_grupos,
            "grupos": grupos,
        }
        sin_dato = sum(1 for o in ordenes if claves[agrupar_por](o) is None)
        if sin_dato:
            salida["sin_dato"] = sin_dato
        if total_grupos > len(grupos):
            salida["nota"] = (
                f"Hay {total_grupos} valores distintos; aquí van los {len(grupos)} "
                "primeros. Dilo si presentas la lista."
            )
        return salida, None

    async def _buscar_orden(
        self, orden: str | None = None, nic: str | None = None
    ) -> tuple[dict[str, Any], None]:
        ordenes = self._ordenes()
        if ordenes is None:
            return self.SIN_CARGUE, None
        if not orden and not nic:
            raise ValueError("Hay que pasar el número de orden o el NIC")

        clave_orden, clave_nic = norm_dato(orden), norm_dato(nic)
        halladas = [
            o
            for o in ordenes
            if (clave_orden and norm_dato(o.orden) == clave_orden)
            or (clave_nic and norm_dato(o.nic) == clave_nic)
        ]
        buscado = " o ".join(p for p in (f"orden {orden}" if orden else "", f"NIC {nic}" if nic else "") if p)

        if not halladas:
            return {
                "base": buscado,
                "encontradas": 0,
                # Puede estar en el archivo original y haberse caído en la limpieza
                # por no tener técnico; decirlo evita un "no existe" que es falso.
                "nota": (
                    f"No hay ninguna orden con {buscado} entre las cargadas. Puede que "
                    "esté en el archivo pero sin técnico asignado: esas no se cargan."
                ),
            }, None
        return {
            "base": buscado,
            "encontradas": len(halladas),
            "ordenes": [_ficha(o) for o in halladas[:MAX_ORDENES]],
        }, None


def _ficha(orden: Orden) -> dict[str, Any]:
    """Los campos de una orden que sí puede ver el modelo.

    El nombre del cliente se queda fuera a propósito: es un dato personal y para
    decidir a quién se visita primero no aporta nada.
    """
    return {
        "orden": orden.orden, "nic": orden.nic, "tecnico": orden.tecnico,
        "barrio": orden.bkey, "direccion": orden.direccion, "tipo_os": orden.tipo_os,
        "tipo_suspension": orden.tipo_suspension, "tarifa": orden.tarifa,
        "estado": orden.estado, "deuda": orden.deuda, "facturas": orden.facturas,
        "antiguedad_dias": orden.antiguedad,
    }


def _filtrar(
    ordenes: list[Orden],
    tecnico: str | None = None,
    barrio: str | None = None,
    tarifa: str | None = None,
    estado: str | None = None,
    deuda_min: float | None = None,
    facturas_min: int | None = None,
    antiguedad_min: int | None = None,
) -> tuple[list[Orden], str]:
    """Aplica los filtros y devuelve (órdenes, descripción del recorte).

    La descripción vuelve al modelo para que diga sobre qué contó, igual que el
    `base` de las herramientas del histórico.
    """
    partes: list[str] = []

    def texto(valor: str | None, campo, etiqueta: str) -> None:
        nonlocal ordenes
        if not valor:
            return
        clave = norm_dato(valor)
        ordenes = [o for o in ordenes if campo(o) and clave in norm_dato(campo(o))]
        partes.append(f"{etiqueta} {valor}")

    def minimo(valor, campo, etiqueta: str) -> None:
        nonlocal ordenes
        if valor is None:
            return
        ordenes = [o for o in ordenes if campo(o) is not None and campo(o) >= valor]
        partes.append(f"{etiqueta} {valor} o más")

    texto(tecnico, lambda o: o.tecnico, "técnico")
    texto(barrio, lambda o: o.bkey, "barrio")
    texto(tarifa, lambda o: o.tarifa, "tarifa")
    texto(estado, lambda o: o.estado, "estado")
    minimo(deuda_min, lambda o: o.deuda, "deuda de")
    minimo(facturas_min, lambda o: o.facturas, "facturas:")
    minimo(antiguedad_min, lambda o: o.antiguedad, "antigüedad:")

    return ordenes, " · ".join(partes) or "todo el cargue"


def _conteo(
    ordenes: list[Orden], clave, tope: int = MAX_ORDENES, ordenar_por: str = "ordenes"
) -> list[dict[str, Any]]:
    """Cuántas órdenes, cuánta deuda y cuánta de media por cada valor.

    Se recorta a los primeros: un cargue puede tocar cien barrios y la lista
    completa no cabe en el prompt ni le sirve a nadie.
    """
    acumulado: dict[str, list[float]] = {}
    for orden in ordenes:
        valor = clave(orden)
        if valor is None:
            continue
        fila = acumulado.setdefault(valor, [0, 0.0])
        fila[0] += 1
        fila[1] += orden.deuda or 0.0

    posicion = 1 if ordenar_por == "deuda" else 0
    ordenado = sorted(acumulado.items(), key=lambda par: par[1][posicion], reverse=True)
    return [
        {
            "valor": valor,
            "ordenes": int(n),
            "deuda": round(deuda),
            "deuda_promedio": round(deuda / n),
        }
        for valor, (n, deuda) in ordenado[:tope]
    ]


def _rango(valores) -> dict[str, int] | None:
    """Mínimo, mediana y máximo. None si no hay ni un dato."""
    datos = sorted(v for v in valores if v is not None)
    if not datos:
        return None
    return {
        "min": datos[0],
        "mediana": datos[len(datos) // 2],
        "max": datos[-1],
    }
