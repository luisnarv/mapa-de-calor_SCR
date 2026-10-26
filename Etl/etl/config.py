"""Configuración central del ETL.

Todas las rutas se resuelven **relativas a la carpeta del ETL**, de modo que
funcione igual en Windows local y en el runner Linux de GitHub Actions.

Los parámetros ajustables se leen de variables de entorno (ver `.env.example`).

Entradas y salidas viven en sitios distintos y por eso son configurables:

* La **salida** (los JSON del mapa) cae por defecto en `Etl/salida/`. Repartirla
  a `Frontend/dashboard/public/` y a `Backend/app/data/` es un paso posterior,
  todavía manual.
* Los **geojson** son entrada: los lee `geo.py` para el enlace punto-en-polígono
  y hoy viven junto al frontend, que también los necesita para dibujar el mapa.
  No se duplican aquí para no tener dos copias que se desincronicen.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# etl/config.py -> Etl/ -> raíz del repo.
ETL_ROOT: Path = Path(__file__).resolve().parents[1]
REPO_ROOT: Path = ETL_ROOT.parent

load_dotenv(ETL_ROOT / ".env")

SALIDA_POR_DEFECTO: Path = ETL_ROOT / "salida"
GEOJSON_POR_DEFECTO: Path = REPO_ROOT / "Frontend" / "dashboard" / "public" / "geojson"

# --- Caja geográfica del Atlántico: un GPS válido del planeta puede no serlo
#     del Atlántico. (lat_min, lat_max, lon_min, lon_max) ---
BBOX: tuple[float, float, float, float] = (10.0, 11.35, -75.45, -74.35)

# Origen para codificar coordenadas como enteros compactos (la/lo en el JSON).
LAT0: float = 10.0
LON0: float = -76.0

# Nombres de mes en español, para el manifiesto (idénticos al front).
MESES_ES: tuple[str, ...] = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)

# Consulta principal SCR: alias a los nombres que el resto del ETL espera.
QUERY_HISTORICO: str = """
    SELECT
      zona AS "ZONA",
      territorio AS "TERRITORIO",
      orden AS "ORDEN",
      nic AS "NIC",
      municipio AS "MUNICIPIO",
      corregimiento AS "CORREGIMIENTO",
      localidad_barrio AS "LOCALIDAD/BARRIO",
      tarifa AS "TARIFA",
      direccion AS "DIRECCION",
      id_tecnico AS "ID TECNICO",
      tecnico AS "TECNICO",
      tipo_brigada AS "TIPO BRIGADA",
      brigada_homologada AS "BRIGADA HOMOLOGADA",
      tipo_os AS "TIPO OS",
      tipo_suspension_solicitada AS "TIPO SUSPENSION SOLICITADA",
      accion AS "ACCION",
      subaccion_subanomalia AS "SUBACCION/SUBANOMALIA",
      av_resultado AS "AV/RESULTADO",
      fecha_cierre AS "FECHA_CIERRE",
      observacion AS "OBSERVACION",
      obs_combinada AS "OBS_COMBINADA",
      gps AS "GPS"
    FROM dbanalitica.historico_mo
"""

# Cruce subacción -> estado (Efectiva/Fallida/Perdida). Solo SCR.
QUERY_ESTADOS: str = (
    'SELECT DISTINCT "SubAccion", "Estado" '
    "FROM dbanalitica.maestro_tarifas "
    'WHERE "SubAccion" IS NOT NULL AND "Estado" IS NOT NULL'
)

# Consulta COBROS: mismos alias internos para reutilizar el pipeline.
QUERY_COBROS: str = """
    SELECT
      zona AS "ZONA",
      territorio AS "TERRITORIO",
      id::text AS "ORDEN",
      cuenta AS "NIC",
      municipio AS "MUNICIPIO",
      NULL::text AS "CORREGIMIENTO",
      barrio AS "LOCALIDAD/BARRIO",
      tarifa AS "TARIFA",
      NULL::text AS "DIRECCION",
      usuario_gestion AS "ID TECNICO",
      gestor AS "TECNICO",
      plan AS "TIPO BRIGADA",
      gestion AS "TIPO OS",
      NULL::text AS "TIPO SUSPENSION SOLICITADA",
      anomalia AS "ACCION",
      linea_accion AS "SUBACCION/SUBANOMALIA",
      resultado AS "AV/RESULTADO",
      fecha_gestion AS "FECHA_CIERRE",
      observaciones AS "OBSERVACION",
      observaciones AS "OBS_COMBINADA",
      REPLACE(punto_gps, '.-', ',-') AS "GPS",
      CASE WHEN estado_final = 'VF' THEN 'VF' ELSE resultado_reportado END AS "ESTADO_GESTION",
      resultado_gestion AS "RESULTADO_GESTION",
      resultado_cobranza AS "RESULTADO_COBRANZA"
    FROM dbanalitica.historico_aire_cobros
"""

# Tabla por gestor (solo COBROS), calculada con `v_aire_cobros_nic_mes`: una fila por
# cuenta y mes, con su gestión vigente, saldo y recaudo ya sin duplicados. Se agrupa por
# todo lo que el tablero filtra (mes, gestor, zona, municipio, plan, resultado de la
# gestión y recaudo) para que el navegador pueda sumar solo lo que pasa los filtros y
# el resultado cuadre con la vista. `calculo_provisional` es verdadero en el mes en curso.
QUERY_GESTORES: str = """
    SELECT
      n.periodo_mes,
      n.gestor,
      n.zona,
      n.municipio,
      n.plan,
      n.estado_final,
      n.resultado_gestion,
      COUNT(*) AS cuentas,
      COALESCE(SUM(n.saldo), 0) AS saldo,
      COALESCE(SUM(n.recaudo_sin_irr), 0) AS recaudo,
      COALESCE(SUM(c.p_cobro_hoy), 0) AS cobro_hoy,
      COALESCE(SUM(c.p_cobro_ant), 0) AS cobro_ant,
      -- Recaudo de las cuentas que sí traen potencial, para que el % compare lo mismo arriba y
      -- abajo (octubre solo trae potencial en una parte de sus cuentas). Es la suma total: el
      -- recaudo de unas cuentas puede cubrir lo que faltó en otras, y el % puede pasar de 100.
      COALESCE(SUM(n.recaudo_sin_irr) FILTER (WHERE c.p_cobro_hoy > 0), 0) AS rec_pot_hoy,
      -- Mes anterior de las MISMAS cuentas que el gestor tiene en este mes: su recaudo de
      -- balanza del mes previo, contra el p_cobro_ant que traen. Balanza y no la vista, porque
      -- solo la mitad de las cuentas de octubre se gestionó también en septiembre; el recaudo
      -- de balanza coincide con el de la vista en las 63.401 cuentas de septiembre.
      COALESCE(SUM(bp.rec) FILTER (WHERE c.p_cobro_ant > 0), 0) AS rec_pot_ant,
      BOOL_OR(n.calculo_provisional) AS provisional
    FROM dbanalitica.v_aire_cobros_nic_mes n
    -- La vista por cuenta y mes no trae el potencial de cobro: se toma de la de gestiones,
    -- donde vale lo mismo para todas las gestiones de una cuenta en el mes (verificado
    -- en septiembre y octubre de 2026). MAX y no SUM para no multiplicarlo por gestión.
    -- Hay cuentas con potencial negativo (hasta -$129 M en una sola): GREATEST las deja en 0,
    -- porque restadas del resto dejaban a un gestor con deuda negativa y su % sin sentido.
    LEFT JOIN (
      SELECT cuenta, periodo_mes, GREATEST(MAX(p_cobro_hoy), 0) AS p_cobro_hoy, GREATEST(MAX(p_cobro_ant), 0) AS p_cobro_ant
      FROM dbanalitica.v_aire_cobros_gestion
      GROUP BY cuenta, periodo_mes
    ) c ON c.cuenta = n.cuenta AND c.periodo_mes = n.periodo_mes
    LEFT JOIN (
      SELECT cuenta::text AS cuenta, periodo, SUM(recaudo_sin_irr) AS rec
      FROM dbanalitica.historico_balanza
      WHERE periodo >= ((SELECT MAX(anio) FROM dbanalitica.v_aire_cobros_nic_mes) - 1) * 100 + 12
      GROUP BY cuenta::text, periodo
    ) bp ON bp.cuenta = n.cuenta::text
        AND bp.periodo = (EXTRACT(YEAR FROM to_date(n.periodo_mes, 'YYYY-MM') - INTERVAL '1 month') * 100
                          + EXTRACT(MONTH FROM to_date(n.periodo_mes, 'YYYY-MM') - INTERVAL '1 month'))::int
    WHERE n.anio = (SELECT MAX(anio) FROM dbanalitica.v_aire_cobros_nic_mes)
      AND n.gestor IS NOT NULL
      AND n.resultado_gestion IS NOT NULL
    GROUP BY n.periodo_mes, n.gestor, n.zona, n.municipio, n.plan, n.estado_final, n.resultado_gestion
    ORDER BY n.periodo_mes, n.gestor
"""

# Recaudo y saldo por cuenta y mes (solo COBROS), de la vista ya sin duplicados. No se
# agrupa aquí: el barrio lo pone `escribir_recaudo_barrio` con el que el mapa le asignó
# por GPS a la gestión de la cuenta, porque el `barrio` de la tabla de origen no
# coincide con el del mapa (el chat nombraba barrios que el mapa mostraba vacíos). Mismo
# universo que `QUERY_GESTORES` para que los totales de los dos archivos coincidan.
QUERY_RECAUDO_BARRIO: str = """
    SELECT
      periodo_mes,
      cuenta::text AS cuenta,
      municipio,
      COALESCE(saldo, 0) AS saldo,
      COALESCE(recaudo_sin_irr, 0) AS recaudo,
      calculo_provisional AS provisional
    FROM dbanalitica.v_aire_cobros_nic_mes
    WHERE anio = (SELECT MAX(anio) FROM dbanalitica.v_aire_cobros_nic_mes)
      AND gestor IS NOT NULL
      AND resultado_gestion IS NOT NULL
"""

# Columnas que deben tratarse como texto (evita el ".0" que pandas pega a floats).
COLS_TEXTO: tuple[str, ...] = ("ORDEN", "NIC", "ID TECNICO")


PROCESOS_VALIDOS: tuple[str, ...] = ("scr", "cobros")


@dataclass(frozen=True)
class Settings:
    """Parámetros de una corrida del ETL."""

    database_url: str
    public_dir: Path
    geojson_dir: Path
    proceso: str = "scr"
    write_csv: bool = False
    csv_path: Path | None = None
    log_level: str = "INFO"

    @property
    def geo_barrios(self) -> Path:
        return self.geojson_dir / "atlantico_barrios.geojson"

    @property
    def geo_municipios(self) -> Path:
        return self.geojson_dir / "atlantico_municipios.geojson"

    @property
    def geo_zonas(self) -> Path:
        return self.geojson_dir / "zonas_atlantico.geojson"


def load_settings(
    *,
    proceso: str = "scr",
    write_csv: bool = False,
    csv_path: str | os.PathLike[str] | None = None,
    log_level: str | None = None,
) -> Settings:
    """Construye `Settings` a partir del entorno.

    Args:
        proceso: ``"scr"`` o ``"cobros"``.
        write_csv: si se debe generar el CSV consolidado (por defecto no).
        csv_path: ruta del CSV; si es None se usa `<repo>/consolidado_ordenes.csv`.
        log_level: nivel de logging; si es None se lee de `ETL_LOG_LEVEL` o INFO.

    Raises:
        RuntimeError: si no está definida la variable de BD correspondiente.
    """
    if proceso not in PROCESOS_VALIDOS:
        raise RuntimeError(f"Proceso desconocido: {proceso!r}. Válidos: {PROCESOS_VALIDOS}")

    database_url = os.environ.get("SCR_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Falta la variable de entorno SCR_DATABASE_URL "
            "(defínela en Etl/.env o en GitHub Secrets)."
        )

    salida = _ruta_de("ETL_OUTPUT_DIR", SALIDA_POR_DEFECTO / proceso)

    return Settings(
        database_url=database_url,
        public_dir=salida,
        geojson_dir=_ruta_de("ETL_GEOJSON_DIR", GEOJSON_POR_DEFECTO),
        proceso=proceso,
        write_csv=write_csv,
        csv_path=Path(csv_path) if csv_path else (ETL_ROOT / "consolidado_ordenes.csv"),
        log_level=(log_level or os.environ.get("ETL_LOG_LEVEL", "INFO")).upper(),
    )


def _ruta_de(variable: str, por_defecto: Path) -> Path:
    """Ruta tomada del entorno, o la de por defecto si no está definida."""
    valor = os.environ.get(variable, "").strip()
    return Path(valor).expanduser().resolve() if valor else por_defecto
