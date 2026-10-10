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
from .direcciones import construir as construir_direcciones
from .geo import asignar_territorio, link_barrios, load_municipios, load_zonas
from .logging_conf import get_logger

log = get_logger()

EST: dict[str, int] = {"Efectiva": 0, "Fallida": 1, "Perdida": 2}

# BKEY -> b, TECNICO -> t, etc. (columna del df -> letra del índice).
_DIMS: tuple[tuple[str, str], ...] = (
    ("BKEY", "b"), ("TECNICO", "t"), ("TIPO BRIGADA", "g"), ("TIPO OS", "o"),
    ("CAUSA", "c"), ("SUBACCION/SUBANOMALIA", "s"),
    ("TIPO SUSPENSION SOLICITADA", "u"), ("TARIFA", "f"),
    ("ACTIVIDAD", "a"),
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
        "a": df["a"].tolist(),
        "m": df["m"].tolist(),
        **({"x": df["x"].tolist()} if "x" in df else {}),
        **({"ca": df["ca"].tolist(), "sc": df["sc"].tolist()} if "ca" in df else {}),
        "n": pd.to_numeric(df["ORDEN"], errors="coerce").fillna(0).astype("int64").tolist(),
        "nic": df["NIC"].astype(str).tolist(),
    }


def _prepare(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Filtra a filas con Estado + GPS + fecha válidos y arma BKEY.

    Solo conserva el año en curso (el del registro más reciente), igual que SCR.
    """
    d = df[df["Estado"].notna() & df["LATITUD"].notna() & df["LONGITUD"].notna()].copy()
    d["dt"] = pd.to_datetime(d["FECHA_EJECUCION"], errors="coerce")
    d = d[d["dt"].notna()].sort_values("dt").reset_index(drop=True)

    anio_actual = d["dt"].max().year
    antes = len(d)
    d = d[d["dt"].dt.year == anio_actual].reset_index(drop=True)
    if len(d) < antes:
        log.info("Filtro año %d: %s descartadas -> quedan %s",
                 anio_actual, f"{antes - len(d):,}", f"{len(d):,}")

    # Solo cobros: su barrio y municipio salen del GPS, no del nombre en la base.
    if settings.proceso == "cobros":
        d = asignar_territorio(d, settings.geo_barrios, settings.geo_municipios)
    d["MUNICIPIO"] = d["MUNICIPIO"].fillna("SIN MUNICIPIO")
    d["LOCALIDAD/BARRIO"] = d["LOCALIDAD/BARRIO"].fillna("SIN BARRIO")
    d["ZONA"] = d["ZONA"].fillna("SIN ZONA")
    for col in ("TECNICO", "TIPO BRIGADA", "TIPO OS", "TIPO SUSPENSION SOLICITADA",
                "SUBACCION/SUBANOMALIA", "TARIFA", "CAUSA", "FAMILIA_CAUSA",
                "ACTIVIDAD"):
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
    d = _prepare(df, settings)
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
    acts, ai = _idx(d["ACTIVIDAD"])
    _, mi_map = _idx(d["MUNICIPIO"])
    munis = sorted(d["MUNICIPIO"].dropna().astype(str).unique().tolist())
    zonas = sorted(d["ZONA"].dropna().astype(str).unique().tolist())

    mapas = {"BKEY": bi, "TECNICO": ti, "TIPO BRIGADA": gi, "TIPO OS": oi,
             "CAUSA": ci, "SUBACCION/SUBANOMALIA": si,
             "TIPO SUSPENSION SOLICITADA": ui, "TARIFA": fi,
             "ACTIVIDAD": ai}
    for col, letra in _DIMS:
        d[letra] = d[col].map(mapas[col]).fillna(0).astype(int)
    # Solo cobros: estado de la gestión (filtro aparte de Efectiva/Perdida).
    anoms: list[str] = []
    subcausas: list[str] = []
    if "ANOMALIA_CAUSA" in d:
        d["ANOMALIA_CAUSA"] = d["ANOMALIA_CAUSA"].fillna("SIN DATO")
        anoms, ai2 = _idx(d["ANOMALIA_CAUSA"])
        subcausas, si2 = _idx(d["SUBCAUSA"])
        d["ca"] = d["ANOMALIA_CAUSA"].map(ai2).astype(int)
        d["sc"] = d["SUBCAUSA"].map(si2).astype(int)
    gests: list[str] = []
    if "ESTADO_GESTION" in d:
        gests, xi = _idx(d["ESTADO_GESTION"])
        d["x"] = d["ESTADO_GESTION"].map(xi).astype(int)
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
        _escribir_observaciones(grp, ym, settings.public_dir)

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
            "acts": acts,
            **({"gests": gests} if gests else {}),
            **({"anoms": anoms, "subcausas": subcausas} if anoms else {}),
            "munis": munis, "zonas": zonas,
            "estados": ["Efectiva", "Fallida", "Perdida"],  # índice 1 sin uso en cobros
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

    # Va en su propio archivo y no dentro de data.json porque son ~5 MB que solo
    # necesita el backend al ubicar un cargue: el tablero lo descargaría en cada
    # visita para nada. Se construye sobre `df` y no sobre `d` a propósito: `d`
    # ya está recortado a las filas con Estado y fecha válidos, y una dirección
    # con GPS sirve para ubicar aunque su orden no cuente para las métricas.
    indice = construir_direcciones(df)
    dir_path = settings.public_dir / "direcciones.json"
    _escribir(dir_path, indice)
    log.info("JSON direcciones -> %s (%.2f MB)", dir_path.name, dir_path.stat().st_size / 1e6)

    return {"total_all": int(len(d)), "meses": resumen_meses}


def escribir_gestores(df: pd.DataFrame, settings: Settings) -> None:
    """Vuelca la vista por gestor y mes (solo COBROS) en `gestores.json`.

    Va aparte del mapa porque no es por gestión sino por cuenta y mes, y el tablero
    solo la pide al abrir la vista por gestor. Formato compacto: cada fila es
    ``[gestor, mes, cuentas, cuentas_con_gestión_efectiva, deuda, recaudo, cuentas_con_pago]`` con
    índices a las listas ``gestores`` y ``meses``.
    """
    gestores = sorted(df["gestor"].dropna().astype(str).unique().tolist())
    meses = sorted(df["periodo_mes"].dropna().astype(str).unique().tolist())
    gi = {g: i for i, g in enumerate(gestores)}
    mi = {m: i for i, m in enumerate(meses)}
    filas = [
        [gi[str(r.gestor)], mi[str(r.periodo_mes)], int(r.cuentas), int(r.cuentas_ef),
         round(float(r.deuda)), round(float(r.recaudo)), int(r.cuentas_pago)]
        for r in df.itertuples(index=False)
    ]
    settings.public_dir.mkdir(parents=True, exist_ok=True)
    path = settings.public_dir / "gestores.json"
    _escribir(path, {"gestores": gestores, "meses": meses, "filas": filas})
    log.info("JSON gestores -> %s (%s gestores, %s meses, %s filas)",
             path.name, len(gestores), len(meses), f"{len(filas):,}")


def _escribir_observaciones(grp: pd.DataFrame, ym: str, destino: Path) -> None:
    """Vuelca el acta de visita de un mes, alineada por posición con `pts`.

    Va en archivos aparte —uno por mes— y NO dentro de data.json porque es el
    campo más pesado de todos (~350 caracteres por orden, unos 60 MB en el
    histórico completo) y el tablero no lo usa: solo lo lee el backend cuando
    alguien busca un término en el chat. Mismo criterio que `direcciones.json`.

    El acta se guarda íntegra, sin trocear por las etiquetas del formato
    (`VS:`, `VM:`, `SS:`, `TL:`), porque esas etiquetas no aparecen siempre ni
    en el mismo orden: el texto útil unas veces sigue a `SS:` y otras cuelga
    suelto detrás de un `TL:` vacío. Un parser que se equivoque no falla, solo
    deja de encontrar menciones, y eso no se nota.

    El orden es el mismo que el de `_pts_dict(grp)`: la posición `i` de esta
    lista es la orden `i` de ese mes. Así no hay que repetir barrio, estado ni
    fecha, que ya viajan en el payload.
    """
    textos = grp["OBS_COMBINADA"].fillna("").astype(str).tolist()
    fname = f"observaciones_{ym}.json"
    path = destino / fname
    _escribir(path, {"obs": textos})
    log.info("JSON observaciones -> %s (%s actas, %.2f MB)",
             fname, f"{len(textos):,}", path.stat().st_size / 1e6)


def _escribir(path: Path, obj: Any) -> None:
    """Vuelca `obj` como JSON compacto UTF-8."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), ensure_ascii=False)
