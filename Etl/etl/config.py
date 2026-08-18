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

# Consulta principal: alias a los nombres que el resto del ETL espera.
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

# Cruce subacción -> estado (Efectiva/Fallida/Perdida).
QUERY_ESTADOS: str = (
    'SELECT DISTINCT "SubAccion", "Estado" '
    "FROM dbanalitica.maestro_tarifas "
    'WHERE "SubAccion" IS NOT NULL AND "Estado" IS NOT NULL'
)

# Columnas que deben tratarse como texto (evita el ".0" que pandas pega a floats).
COLS_TEXTO: tuple[str, ...] = ("ORDEN", "NIC", "ID TECNICO")


@dataclass(frozen=True)
class Settings:
    """Parámetros de una corrida del ETL."""

    database_url: str
    public_dir: Path
    geojson_dir: Path
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
    write_csv: bool = False,
    csv_path: str | os.PathLike[str] | None = None,
    log_level: str | None = None,
) -> Settings:
    """Construye `Settings` a partir del entorno.

    Args:
        write_csv: si se debe generar el CSV consolidado (por defecto no).
        csv_path: ruta del CSV; si es None se usa `<repo>/consolidado_ordenes.csv`.
        log_level: nivel de logging; si es None se lee de `ETL_LOG_LEVEL` o INFO.

    Raises:
        RuntimeError: si no está definida `SCR_DATABASE_URL`.
    """
    database_url = os.environ.get("SCR_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Falta la variable de entorno SCR_DATABASE_URL "
            "(defínela en Etl/.env o en GitHub Secrets)."
        )

    return Settings(
        database_url=database_url,
        public_dir=_ruta_de("ETL_OUTPUT_DIR", SALIDA_POR_DEFECTO),
        geojson_dir=_ruta_de("ETL_GEOJSON_DIR", GEOJSON_POR_DEFECTO),
        write_csv=write_csv,
        csv_path=Path(csv_path) if csv_path else (ETL_ROOT / "consolidado_ordenes.csv"),
        log_level=(log_level or os.environ.get("ETL_LOG_LEVEL", "INFO")).upper(),
    )


def _ruta_de(variable: str, por_defecto: Path) -> Path:
    """Ruta tomada del entorno, o la de por defecto si no está definida."""
    valor = os.environ.get(variable, "").strip()
    return Path(valor).expanduser().resolve() if valor else por_defecto
