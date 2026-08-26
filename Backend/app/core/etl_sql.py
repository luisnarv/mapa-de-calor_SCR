r"""Réplica en SQL de las reglas de negocio del ETL.

NO SE USA HOY. Las métricas salen del payload del ETL (`services/payload_store`),
que es más rápido y no puede discrepar del mapa. Este módulo se conserva porque
es la base de la vista materializada que haría falta para responder preguntas que
el payload no cubre: histórico anterior al ETL, deuda, estrato, riesgo por NIC.

Reproduce paso por paso lo que hacen `transform.enrich` y `payload._prepare`, en
el mismo orden. Nada aquí es una interpretación nueva de los datos: es una
traducción.

Correspondencia, en orden de aplicación:

| Paso                    | ETL                            | Aquí            |
|-------------------------|--------------------------------|-----------------|
| Filtro VS               | transform.py:72                | CTE `vs`        |
| Dedup por ORDEN         | transform.py:135               | CTE `vs`        |
| GPS -> lat/lon + BBOX   | transform.py:93                | CTE `geo`       |
| Estado (maestro)        | transform.py:107               | CTE `estado_map`|
| Homologación brigada    | transform.py:104               | CTE `brigadas`  |
| CAUSA / CONTROLABLE     | transform.py:119               | CTE `causas`    |
| Estado+GPS+fecha no nulos | payload.py:68                | CTE `ordenes`   |
| BKEY, "SIN DATO"        | payload.py:72                  | CTE `ordenes`   |

Diferencias conocidas y aceptadas:

* `norm_dato` del ETL repara *mojibake* (`Ã`/`Â`) de la doble decodificación
  latin-1/utf-8. Eso ocurre al leer con pandas; en el servidor los textos ya
  llegan bien, así que la reparación no se replica.
* Si `maestro_tarifas` mapeara una misma subacción normalizada a dos Estados
  distintos, pandas se queda con el último que ve y aquí con el menor
  alfabéticamente. `metrics_service.verificar_maestro()` detecta ese caso.
"""

from __future__ import annotations

from app.core.taxonomy import BBOX, CAUSAS_NORM, HOMOLOG_BRIGADA

# --- Normalización, espejo de etl/text.py ------------------------------------

# lower() ya corrió, así que solo hacen falta las minúsculas acentuadas.
_TILDES_DE = "áéíóúñü"
_TILDES_A = "aeiounu"


def _norm(expr: str) -> str:
    """SQL equivalente a `text.norm`: sin tildes, separadores a espacio."""
    return (
        f"btrim(regexp_replace("
        f"translate(lower(btrim({expr})), '{_TILDES_DE}/-_', '{_TILDES_A}   '),"
        r" '\s+', ' ', 'g'))"
    )


def _norm_dato(expr: str) -> str:
    """SQL equivalente a `text.norm_dato`: solo letras y números."""
    return (
        f"regexp_replace(translate(lower({expr}), '{_TILDES_DE}', '{_TILDES_A}'),"
        r" '[^a-z0-9]', '', 'g')"
    )


def _limpio(expr: str) -> str:
    """Espejo de la limpieza de texto del ETL (transform.py:84)."""
    return f"NULLIF(NULLIF(NULLIF(btrim({expr}), ''), 'nan'), 'None')"


# --- Tablas de consulta generadas desde la taxonomía -------------------------


def _values_causas() -> tuple[str, dict[str, object]]:
    """VALUES con el catálogo de causas, como parámetros ligados."""
    filas: list[str] = []
    params: dict[str, object] = {}
    for i, (clave, (causa, familia, controlable)) in enumerate(CAUSAS_NORM.items()):
        cast = "CAST(:ck{0} AS text), CAST(:cc{0} AS text), CAST(:cf{0} AS text), CAST(:cx{0} AS integer)"
        plano = ":ck{0}, :cc{0}, :cf{0}, :cx{0}"
        filas.append("(" + (cast if i == 0 else plano).format(i) + ")")
        params |= {f"ck{i}": clave, f"cc{i}": causa, f"cf{i}": familia, f"cx{i}": controlable}
    return ", ".join(filas), params


def _values_brigadas() -> tuple[str, dict[str, object]]:
    """VALUES con la homologación de brigadas."""
    filas: list[str] = []
    params: dict[str, object] = {}
    for i, (clave, nombre) in enumerate(HOMOLOG_BRIGADA.items()):
        cast = "CAST(:bk{0} AS text), CAST(:bn{0} AS text)"
        plano = ":bk{0}, :bn{0}"
        filas.append("(" + (cast if i == 0 else plano).format(i) + ")")
        params |= {f"bk{i}": clave, f"bn{i}": nombre}
    return ", ".join(filas), params


# --- CTE base ----------------------------------------------------------------

_PLANTILLA = r"""
WITH estado_map AS (
    -- Un solo Estado por subacción normalizada: el JOIN no debe multiplicar filas.
    SELECT DISTINCT ON (clave) clave, estado
    FROM (
        SELECT {sub_norm} AS clave, mt."Estado" AS estado
        FROM dbanalitica.maestro_tarifas mt
        WHERE mt."SubAccion" IS NOT NULL AND mt."Estado" IS NOT NULL
    ) m
    WHERE clave <> ''
    ORDER BY clave, estado
),
causas (clave, causa, familia, controlable) AS (VALUES {causas}),
brigadas (clave, nombre) AS (VALUES {brigadas}),
vs AS (
    -- Solo órdenes con acta de visita, y una sola fila por orden.
    -- DESC NULLS FIRST reproduce el `sort_values(...).drop_duplicates(keep='last')`
    -- de pandas, que deja arriba la fila sin fecha cuando la hay.
    SELECT DISTINCT ON (h.orden)
        h.orden, h.nic, h.zona, h.municipio, h.localidad_barrio, h.tecnico,
        h.tipo_brigada, h.tipo_os, h.subaccion_subanomalia, h.accion,
        h.fecha_cierre, h.gps
    FROM dbanalitica.historico_mo h
    WHERE h.observacion ~* '^\s*v\.?\s*s\.?\s*:'
    ORDER BY h.orden, h.fecha_cierre DESC NULLS FIRST
),
geo AS (
    SELECT v.*, c.lat, c.lon
    FROM vs v
    CROSS JOIN LATERAL (
        SELECT
            CASE WHEN a[1] ~ '^-?[0-9]+(\.[0-9]+)?$'
                 THEN CAST(a[1] AS double precision) END AS lat,
            CASE WHEN a[2] ~ '^-?[0-9]+(\.[0-9]+)?$'
                 THEN CAST(a[2] AS double precision) END AS lon
        FROM (
            SELECT regexp_split_to_array(btrim(COALESCE(v.gps, '')), '[,;]\s*|\s+') AS a
        ) s
    ) c
),
ordenes AS (
    SELECT
        g.orden,
        g.nic,
        COALESCE({municipio}, 'SIN MUNICIPIO')            AS municipio,
        COALESCE({barrio}, 'SIN BARRIO')                  AS barrio,
        COALESCE({municipio}, 'SIN MUNICIPIO') || ' | ' ||
            COALESCE({barrio}, 'SIN BARRIO')              AS bkey,
        COALESCE({zona}, 'SIN ZONA')                      AS zona,
        COALESCE({tecnico}, 'SIN DATO')                   AS tecnico,
        COALESCE(br.nombre, {brigada_limpia}, 'SIN DATO') AS brigada,
        COALESCE({tipo_os}, 'SIN DATO')                   AS tipo_os,
        em.estado                                         AS estado,
        CASE WHEN em.estado = 'Efectiva' THEN 'Efectiva'
             ELSE COALESCE(cx.causa, :causa_defecto) END  AS causa,
        CASE WHEN em.estado = 'Efectiva' THEN 1
             ELSE COALESCE(cx.controlable, :ctrl_defecto) END AS controlable,
        g.fecha_cierre,
        -- CAST explícito: el ETL hace pd.to_datetime, así que la columna podría
        -- venir como texto. Sobre un timestamp el cast no cuesta nada.
        to_char(CAST(g.fecha_cierre AS timestamp), 'YYYY-MM') AS mes,
        g.lat, g.lon
    FROM geo g
    -- INNER JOIN: sin Estado la fila no entra al tablero (payload.py:68).
    JOIN estado_map em ON em.clave = {sub_g_norm}
    LEFT JOIN causas   cx ON cx.clave = upper({accion_norm})
    LEFT JOIN brigadas br ON br.clave = {brigada_norm}
    WHERE g.fecha_cierre IS NOT NULL
      AND g.lat BETWEEN :lat_min AND :lat_max
      AND g.lon BETWEEN :lon_min AND :lon_max
)
"""


def base_cte() -> tuple[str, dict[str, object]]:
    """Devuelve el CTE `ordenes` (una fila por orden, ya enriquecida) y sus parámetros.

    El resultado se concatena con el SELECT de agregación que toque.
    """
    causas_sql, causas_params = _values_causas()
    brigadas_sql, brigadas_params = _values_brigadas()

    sql = _PLANTILLA.format(
        sub_norm=_norm_dato('mt."SubAccion"'),
        sub_g_norm=_norm_dato("g.subaccion_subanomalia"),
        accion_norm=_norm("g.accion"),
        brigada_norm=_norm("g.tipo_brigada"),
        brigada_limpia=_limpio("g.tipo_brigada"),
        municipio=_limpio("g.municipio"),
        barrio=_limpio("g.localidad_barrio"),
        zona=_limpio("g.zona"),
        tecnico=_limpio("g.tecnico"),
        tipo_os=_limpio("g.tipo_os"),
        causas=causas_sql,
        brigadas=brigadas_sql,
    )

    params: dict[str, object] = {
        "causa_defecto": "Otras causas",
        "ctrl_defecto": 1,
        "lat_min": BBOX[0],
        "lat_max": BBOX[1],
        "lon_min": BBOX[2],
        "lon_max": BBOX[3],
        **causas_params,
        **brigadas_params,
    }
    return sql, params


# --- Métricas compartidas ----------------------------------------------------

# Espejo de page.js:550-558. `noctrl` replica `isNoCtrl = e !== 0 && ctrl === 0`.
AGREGADOS = """
    COUNT(*)                                                      AS tot,
    COUNT(*) FILTER (WHERE estado = 'Efectiva')                   AS ef,
    COUNT(*) FILTER (WHERE estado = 'Fallida')                    AS fa,
    COUNT(*) FILTER (WHERE estado = 'Perdida')                    AS pe,
    COUNT(*) FILTER (WHERE estado <> 'Efectiva' AND controlable = 0) AS noctrl
"""
