"""Enriquecimiento del DataFrame crudo: filtros y campos derivados.

Replica exactamente la lógica de negocio del `Index.py` original (filtro VS,
GPS, fecha, Estado, causa, dedup), pero **vectorizada**:

* Las normalizaciones (`norm`, `norm_dato`) se calculan una vez por valor único
  y no por fila (antes había un `df.apply(..., axis=1)` que dominaba el tiempo).
* La clasificación de causa pasa de row-wise a `map` con diccionarios.

El resultado es idéntico al del script original, en una fracción del tiempo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BBOX, COLS_TEXTO
from .logging_conf import get_logger
from .taxonomy import causa_for_norm_key, homolog_brigada
from .text import RE_VS, norm, norm_dato

log = get_logger()

# Columnas finales que consume el constructor del mapa.
COLS_OBJETIVO: tuple[str, ...] = (
    "ZONA", "TERRITORIO", "ORDEN", "NIC", "MUNICIPIO", "CORREGIMIENTO",
    "LOCALIDAD/BARRIO", "TARIFA", "DIRECCION", "ID TECNICO", "TECNICO",
    "TIPO BRIGADA", "TIPO OS", "TIPO SUSPENSION SOLICITADA", "ACCION",
    "SUBACCION/SUBANOMALIA", "Estado", "AV/RESULTADO", "FECHA_CIERRE",
    "OBSERVACION", "OBS_COMBINADA", "GPS",
)
COLS_DERIVADAS: tuple[str, ...] = (
    "FECHA_EJECUCION", "LATITUD", "LONGITUD", "CAUSA", "FAMILIA_CAUSA", "CONTROLABLE",
)


def split_gps(serie: pd.Series) -> tuple[pd.Series, pd.Series]:
    """'10.95, -74.79' -> (lat, lon) numéricos. Vectorizado."""
    partes = serie.astype(str).str.strip().str.split(r"[,;]\s*|\s+", n=1, regex=True, expand=True)
    if partes.shape[1] < 2:
        nan = pd.Series(np.nan, index=serie.index)
        return nan, nan
    return pd.to_numeric(partes[0], errors="coerce"), pd.to_numeric(partes[1], errors="coerce")


def _map_por_unico(serie: pd.Series, fn) -> pd.Series:
    """Aplica `fn` una sola vez por valor único y lo propaga (vectorizado)."""
    tabla = {v: fn(v) for v in pd.unique(serie)}
    return serie.map(tabla)


def enrich(df: pd.DataFrame, estado_map: dict[str, str]) -> pd.DataFrame:
    """Aplica todos los filtros y campos derivados del ETL.

    Args:
        df: DataFrame crudo tal cual sale de historico_mo.
        estado_map: {subacción normalizada -> Estado}.

    Returns:
        DataFrame enriquecido, deduplicado por ORDEN, con las columnas
        `COLS_OBJETIVO + COLS_DERIVADAS`.

    Raises:
        RuntimeError: si el cruce de Estado deja TODAS las filas sin estado
            (indicaría un problema con maestro_tarifas).
    """
    df = df.copy()
    df["Estado"] = None

    # --- Solo órdenes con acta de visita (OBSERVACION que empieza con "VS:") ---
    antes = len(df)
    df = df[df["OBSERVACION"].astype(str).str.match(RE_VS, na=False)].copy()
    log.info("Filtro VS: %s descartadas de %s -> quedan %s",
             f"{antes - len(df):,}", f"{antes:,}", f"{len(df):,}")

    # --- Identificadores como texto (sin el ".0" que pandas pega a floats) ---
    for col in COLS_TEXTO:
        num = pd.to_numeric(df[col], errors="coerce")
        enteros = num.notna() & (num % 1 == 0)
        df[col] = df[col].astype("object")
        df.loc[enteros, col] = num[enteros].astype("int64").astype(str)

    # --- Limpieza de texto ---
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)
            df[col] = df[col].replace({"": None, "nan": None, "None": None})

    # --- OBS_COMBINADA: fallback a OBSERVACION ---
    df["OBS_COMBINADA"] = df["OBS_COMBINADA"].fillna(df["OBSERVACION"])

    # --- LATITUD / LONGITUD desde GPS, filtradas a la caja del Atlántico ---
    df["LATITUD"], df["LONGITUD"] = split_gps(df["GPS"])
    dentro = df["LATITUD"].between(BBOX[0], BBOX[1]) & df["LONGITUD"].between(BBOX[2], BBOX[3])
    df.loc[~dentro, ["LATITUD", "LONGITUD"]] = np.nan
    log.info("LATITUD/LONGITUD dentro del Atlántico: %s/%s (%.1f%%).",
             f"{int(dentro.sum()):,}", f"{len(df):,}", dentro.mean() * 100 if len(df) else 0)

    # --- FECHA_EJECUCION (tomada de FECHA_CIERRE) ---
    dt = pd.to_datetime(df["FECHA_CIERRE"], errors="coerce")
    df["FECHA_EJECUCION"] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")

    # --- Homologación de brigada (una vez por valor único) ---
    df["TIPO BRIGADA"] = _map_por_unico(df["TIPO BRIGADA"], homolog_brigada)

    # --- Estado (cruce con maestro_tarifas por subacción normalizada) ---
    df["Estado"] = _map_por_unico(
        df["SUBACCION/SUBANOMALIA"],
        lambda s: estado_map.get(norm_dato(s)) if pd.notna(s) else None,
    )
    asignados = int(df["Estado"].notna().sum())
    if asignados == 0:
        raise RuntimeError(
            "'Estado' quedó vacío en TODAS las filas. Revisa dbanalitica.maestro_tarifas."
        )
    log.info("Estado: %s asignados, %s sin match.",
             f"{asignados:,}", f"{int(df['Estado'].isna().sum()):,}")

    # --- CAUSA / FAMILIA_CAUSA / CONTROLABLE (vectorizado) ---
    accion_norm = _map_por_unico(df["ACCION"], lambda a: norm(a).upper())
    unicas = pd.unique(accion_norm)
    causa_lut = {k: causa_for_norm_key(k) for k in unicas}
    df["CAUSA"] = accion_norm.map({k: v[0] for k, v in causa_lut.items()})
    df["FAMILIA_CAUSA"] = accion_norm.map({k: v[1] for k, v in causa_lut.items()})
    df["CONTROLABLE"] = accion_norm.map({k: v[2] for k, v in causa_lut.items()})
    efectiva = df["Estado"].eq("Efectiva")
    df.loc[efectiva, "CAUSA"] = "Efectiva"
    df.loc[efectiva, "FAMILIA_CAUSA"] = "exito"
    df.loc[efectiva, "CONTROLABLE"] = 1
    df["CONTROLABLE"] = df["CONTROLABLE"].astype(int)

    # --- Duplicados por ORDEN: se conserva la más reciente ---
    duplicados = int(df["ORDEN"].duplicated().sum())
    if duplicados:
        df = df.sort_values("FECHA_EJECUCION").drop_duplicates("ORDEN", keep="last")
        log.info("Duplicados: %s órdenes repetidas, se conserva la más reciente.", f"{duplicados:,}")

    return df[list(COLS_OBJETIVO) + list(COLS_DERIVADAS)]
