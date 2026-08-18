"""Construcción del payload JSON que consume el dashboard.

A partir del DataFrame enriquecido: arma las dimensiones indexadas, codifica los
puntos en arrays compactos, calcula centroides y enlaces geográficos, parte los
datos por mes (con manifiesto) y escribe los archivos en `dashboard/public/`.

Formato idéntico al del `Index.py` original. Se omiten el HTML de respaldo y el
modo público del script viejo por ser código auxiliar no usado en la automatización.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import LAT0, LON0, MESES_ES, Settings
from .geo import link_barrios, load_municipios, load_zonas
from .logging_conf import get_logger

log = get_logger()

EST: dict[str, int] = {"Efectiva": 0, "Fallida": 1, "Perdida": 2}

# BKEY -> b, TECNICO -> t, etc. (columna del df -> letra del índice).
_DIMS: tuple[tuple[str, str], ...] = (
    ("BKEY", "b"), ("TECNICO", "t"), ("TIPO BRIGADA", "g"), ("TIPO OS", "o"),
    ("CAUSA", "c"), ("SUBACCION/SUBANOMALIA", "s"),
    ("TIPO SUSPENSION SOLICITADA", "u"), ("TARIFA", "f"),
)


def _idx(serie: pd.Series) -> tuple[list[str], dict[str, int]]:
    """Valores únicos ordenados + su mapa valor->índice."""
    vals = sorted(serie.dropna().astype(str).unique().tolist())
    return vals, {v: i for i, v in enumerate(vals)}


def _moda(serie: pd.Series) -> Any:
    """Moda de una serie (primer valor si no hay moda clara)."""
    m = serie.mode()
    return m.iloc[0] if len(m) else serie.iloc[0]


def _mes_label(ym: str) -> str:
    """'2026-07' -> 'Julio de 2026'."""
    y, mo = ym.split("-")
    return f"{MESES_ES[int(mo) - 1]} de {y}"


def _pts_dict(df: pd.DataFrame) -> dict[str, list]:
    """Codifica un DataFrame en los arrays compactos que consume el front."""
    return {
        "la": ((df["LATITUD"] - LAT0) * 1e5).round().astype(int).tolist(),
        "lo": ((df["LONGITUD"] - LON0) * 1e5).round().astype(int).tolist(),
        "e": df["e"].tolist(), "b": df["b"].tolist(), "t": df["t"].tolist(),
        "g": df["g"].tolist(), "o": df["o"].tolist(), "c": df["c"].tolist(),
        "s": df["s"].tolist(), "u": df["u"].tolist(), "f": df["f"].tolist(),
        "m": df["m"].tolist(),
        "n": pd.to_numeric(df["ORDEN"], errors="coerce").fillna(0).astype("int64").tolist(),
        "nic": df["NIC"].astype(str).tolist(),
    }


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Filtra a filas con Estado + GPS + fecha válidos y arma BKEY."""
    d = df[df["Estado"].notna() & df["LATITUD"].notna() & df["LONGITUD"].notna()].copy()
    d["dt"] = pd.to_datetime(d["FECHA_EJECUCION"], errors="coerce")
    d = d[d["dt"].notna()].sort_values("dt").reset_index(drop=True)

    d["MUNICIPIO"] = d["MUNICIPIO"].fillna("SIN MUNICIPIO")
    d["LOCALIDAD/BARRIO"] = d["LOCALIDAD/BARRIO"].fillna("SIN BARRIO")
    d["ZONA"] = d["ZONA"].fillna("SIN ZONA")
    for col in ("TECNICO", "TIPO BRIGADA", "TIPO OS", "TIPO SUSPENSION SOLICITADA",
                "SUBACCION/SUBANOMALIA", "TARIFA", "CAUSA", "FAMILIA_CAUSA"):
        d[col] = d[col].fillna("SIN DATO")
    d["BKEY"] = d["MUNICIPIO"] + " | " + d["LOCALIDAD/BARRIO"]
    return d


def build_and_write(df: pd.DataFrame, settings: Settings) -> dict[str, Any]:
    """Construye y escribe el payload JSON completo.

    Args:
        df: DataFrame enriquecido (salida de `transform.enrich`).
        settings: rutas y opciones de la corrida.

    Returns:
        Resumen ``{"total_all": int, "meses": {...}}`` para logging/verificación.
    """
    d = _prepare(df)
    if d.empty:
        raise RuntimeError("No quedan filas con Estado + GPS + fecha; no se genera el mapa.")

    # --- Dimensiones e índices ---
    barrios, bi = _idx(d["BKEY"])
    tecs, ti = _idx(d["TECNICO"])
    brigs, gi = _idx(d["TIPO BRIGADA"])
    tipos, oi = _idx(d["TIPO OS"])
    causas, ci = _idx(d["CAUSA"])
    subs, si = _idx(d["SUBACCION/SUBANOMALIA"])
    susps, ui = _idx(d["TIPO SUSPENSION SOLICITADA"])
    tarifas, fi = _idx(d["TARIFA"])
    _, mi_map = _idx(d["MUNICIPIO"])
    munis = sorted(d["MUNICIPIO"].dropna().astype(str).unique().tolist())
    zonas = sorted(d["ZONA"].dropna().astype(str).unique().tolist())

    mapas = {"BKEY": bi, "TECNICO": ti, "TIPO BRIGADA": gi, "TIPO OS": oi,
             "CAUSA": ci, "SUBACCION/SUBANOMALIA": si,
             "TIPO SUSPENSION SOLICITADA": ui, "TARIFA": fi}
    for col, letra in _DIMS:
        d[letra] = d[col].map(mapas[col]).fillna(0).astype(int)
    d["e"] = d["Estado"].map(EST)

    # Minutos desde la fecha mínima (resolución que usa el front).
    t0 = pd.Timestamp(d["dt"].min().date())
    d["m"] = ((d["dt"] - t0).dt.total_seconds() // 60).astype(int)

    # Barrio -> municipio / zona (por moda) y centroides.
    muni_idx = {n: i for i, n in enumerate(munis)}
    zona_idx = {n: i for i, n in enumerate(zonas)}
    b_muni_nombre = d.groupby("b")["MUNICIPIO"].agg(_moda).to_dict()
    b_zona_nombre = d.groupby("b")["ZONA"].agg(_moda).to_dict()
    b_muni = [muni_idx[b_muni_nombre[i]] for i in range(len(barrios))]
    b_zona = [zona_idx[b_zona_nombre[i]] for i in range(len(barrios))]

    bc: list[list[float]] = []
    for _b, grp in d.groupby("b"):
        bc.append([round(float(grp["LATITUD"].median()), 5),
                   round(float(grp["LONGITUD"].median()), 5)])

    ctrl_por_causa = d.drop_duplicates("CAUSA").set_index("CAUSA")["CONTROLABLE"].to_dict()
    fam_por_causa = d.drop_duplicates("CAUSA").set_index("CAUSA")["FAMILIA_CAUSA"].to_dict()

    # --- Geografía ---
    bpoly = link_barrios(d, settings.geo_barrios)
    mpoly = load_municipios(settings.geo_municipios)
    zpoly = load_zonas(settings.geo_zonas)

    # --- Split por mes ---
    d["mes_aux"] = d["dt"].dt.strftime("%Y-%m")
    ultimo_mes = d["mes_aux"].max()
    d_recent = d[d["mes_aux"] == ultimo_mes]
    log.info("Último mes en curso: %s (%s filas); histórico: %s filas.",
             ultimo_mes, f"{len(d_recent):,}", f"{len(d) - len(d_recent):,}")

    settings.public_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    resumen_meses: dict[str, int] = {}
    for ym in sorted(d["mes_aux"].unique()):
        grp = d[d["mes_aux"] == ym]
        resumen_meses[ym] = len(grp)
        if ym == ultimo_mes:
            manifest.append({"key": ym, "label": _mes_label(ym), "n": int(len(grp)), "recent": True})
        else:
            fname = f"data_{ym}.json"
            _escribir(settings.public_dir / fname, {"pts": _pts_dict(grp)})
            manifest.append({"key": ym, "label": _mes_label(ym), "n": int(len(grp)), "file": fname})
            log.info("JSON mes -> %s (%s órdenes)", fname, f"{len(grp):,}")

    data = {
        "meta": {
            "total": int(len(d_recent)),
            "total_all": int(len(d)),
            "fecha_min": str(d["dt"].min())[:10],
            "fecha_max": str(d["dt"].max())[:10],
            "lat0": LAT0, "lon0": LON0,
            "generated": str(pd.Timestamp.today().date()),
            "months": manifest,
        },
        "dim": {
            "barrios": barrios, "tecs": tecs, "brigs": brigs, "tipos": tipos,
            "causas": causas, "subs": subs, "susps": susps, "tarifas": tarifas,
            "munis": munis, "zonas": zonas,
            "estados": ["Efectiva", "Fallida", "Perdida"],
            "causa_ctrl": [int(ctrl_por_causa.get(c, 1)) for c in causas],
            "causa_fam": [fam_por_causa.get(c, "otros") for c in causas],
            "b_muni": b_muni, "b_zona": b_zona,
        },
        "geo": {"bc": bc, "bp": bpoly, "mp": mpoly, "zp": zpoly},
        "pts": _pts_dict(d_recent),
    }
    data_path = settings.public_dir / "data.json"
    _escribir(data_path, data)
    log.info("JSON data -> %s (%.2f MB)", data_path.name, data_path.stat().st_size / 1e6)

    return {"total_all": int(len(d)), "meses": resumen_meses}


def _escribir(path: Path, obj: Any) -> None:
    """Vuelca `obj` como JSON compacto UTF-8."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), ensure_ascii=False)
