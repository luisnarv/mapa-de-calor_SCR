# ============================================================================
# etl.py
# ----------------------------------------------------------------------------
# ETL unico: consolida los XLSX de las 3 zonas y produce DOS salidas:
#
#   Base/*.xlsx  --->  consolidado_ordenes.csv   (datos, con campos derivados)
#                 --->  mapa_operativo.html       (el mapa, listo para abrir)
#
# Uso:
#   python etl.py                 genera CSV + HTML
#   python etl.py --solo-csv      genera solo el CSV
#
# ----------------------------------------------------------------------------
# CAMPOS DERIVADOS QUE SE AGREGAN AL CSV
# (son los que consume el HTML; tambien sirven en Power BI o Excel)
#
#   FECHA_EJECUCION  fecha y hora reales, sacadas del acta "VS: FECHA: ..."
#   LATITUD          numero, partido del campo GPS
#   LONGITUD         numero, partido del campo GPS
#   CAUSA            etiqueta legible de la ACCION ("Cliente pago antes del corte")
#   FAMILIA_CAUSA    agrupador operativo (seguridad, acceso, datos, comercial...)
#   CONTROLABLE      1 = la operacion pudo evitarlo | 0 = no depende del tecnico
#
# Por que existe CONTROLABLE:
#   El 71,8% de las ordenes no efectivas son "cliente pago antes del corte" o
#   "usuario agresivo". Ninguna es culpa del tecnico. Si se cuentan igual que un
#   fallo real, el ranking castiga a los buenos: hay tecnicos con 57% de
#   efectividad bruta que en realidad solo perdieron 17 ordenes de 1.215.
#   La efectividad ajustada saca del denominador las causas no controlables.
# ============================================================================

import argparse
import datetime
import glob
import json
import os
import re
import sys
import unicodedata
import warnings

import numpy as np
import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# Cargar variables de entorno desde dashboard/.env
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", ".env"))


# ============================================================================
# CONFIGURACION
# ============================================================================
BASE_DIR = "C:/Users/P210/Documents/antigravity/Dashboar mapa de calor/Base"
OUT_CSV = "C:/Users/P210/Documents/antigravity/Dashboar mapa de calor/consolidado_ordenes.csv"
OUT_HTML = "C:/Users/P210/Documents/antigravity/Dashboar mapa de calor/mapa_operativo.html"
MAESTRO_PATH = "C:/Users/P210/Documents/antigravity/Dashboar mapa de calor/Base_Maestra_Parametros.xlsx"
PLANTILLA = "plantilla_mapa.html"          # se busca junto a este .py
GEO_BARRIOS = "C:/Users/P210/Documents/antigravity/Dashboar mapa de calor/dashboard/public/geojson/atlantico_barrios.geojson"      # limites reales (opcional)
GEO_MUNICIPIOS = "C:/Users/P210/Documents/antigravity/Dashboar mapa de calor/dashboard/public/geojson/atlantico_municipios.geojson"

# Caja del Atlantico: un GPS valido del planeta puede no ser un GPS del Atlantico.
BBOX = (10.0, 11.35, -75.45, -74.35)

COLS_OBJETIVO = [
    "ZONA", "TERRITORIO", "ORDEN", "NIC", "MUNICIPIO", "CORREGIMIENTO",
    "LOCALIDAD/BARRIO", "TARIFA", "DIRECCION", "ID TECNICO", "TECNICO",
    "TIPO BRIGADA", "TIPO OS", "TIPO SUSPENSION SOLICITADA", "ACCION",
    "SUBACCION/SUBANOMALIA", "Estado", "AV/RESULTADO", "FECHA_CIERRE",
    "OBSERVACION", "OBS_COMBINADA", "GPS",
]

# Campos que calcula este ETL y consume el mapa.
COLS_DERIVADAS = ["FECHA_EJECUCION", "LATITUD", "LONGITUD",
                  "CAUSA", "FAMILIA_CAUSA", "CONTROLABLE"]

# Identificadores: si se dejan a pandas los convierte a float y les pega ".0"
# ("1001778653.0"). Asi no cruzan contra nomina ni SAP.
COLS_TEXTO = ["ORDEN", "NIC", "ID TECNICO"]

HOMOLOG_BRIGADA = {
    "scr pesada disponibilidad": "SCR DISPONIBLE",
    "scr pesada": "Brigada Tipo Pesada",
    "scr liviana": "Brigada Tipo Liviana",
    "scr multifamiliar": "Gestor Integral Multi",
    "scr mini canasta": "Brigada Tipo Minicanasta",
    "scr medida especial": "Pesada MT-AT",
    "canasta": "Brigada Tipo Canasta",
}

# ACCION -> (CAUSA legible, FAMILIA, CONTROLABLE)
# Se puede sobreescribir sin tocar codigo con una hoja "Convertidor_Causas"
# en el maestro, con columnas: ACCION | CAUSA | FAMILIA | CONTROLABLE
CAUSAS = {
    "RESISTENCIA DEL CLIENTE":               ("Resistencia / usuario agresivo",    "seguridad", 0),
    "ACCESO IMPEDIDO":                       ("Acceso impedido",                   "acceso",    1),
    "DIFICIL ACCESO":                        ("Dificil acceso",                    "acceso",    1),
    "SUMINISTRO NO ENCONTRADO":              ("Direccion / suministro no hallado", "datos",     1),
    "SERVICIO INEXISTENTE":                  ("Direccion / suministro no hallado", "datos",     1),
    "PREDIO DEMOLIDO":                       ("Direccion / suministro no hallado", "datos",     1),
    "SIN MEDIDOR":                           ("Sin medidor / infraestructura",     "infra",     1),
    "SIN GESTION":                           ("Sin gestion del tecnico",           "gestion",   1),
    "EXITO - SE REQUIERE NORMALIZACION PQR": ("Requiere normalizacion PQR",        "proceso",   1),
    "IMPOSIBILIDAD TECNICA":                 ("Imposibilidad tecnica",             "infra",     1),
    "CLIENTE HA CANCELADO (PAGO RECIENTE)":  ("Cliente pago antes del corte",      "comercial", 0),
    "CLIENTE NO CORTABLE":                   ("Cliente no cortable (normativo)",   "normativo", 0),
    "EN RECLAMO":                            ("En reclamo",                        "normativo", 0),
    "OTRO COMERCIALIZADOR":                  ("Otro comercializador",              "normativo", 0),
}
# Una ACCION nueva cae aqui y se asume CONTROLABLE=1: preferimos exigir de mas y
# que alguien lo revise, antes que perdonar un fallo real en silencio.
CAUSA_DEFECTO = ("Otras causas", "otros", 1)

COL_ALIASES = {
    "GPS": ["gps", "coordenadas", "coordenada", "ubicacion", "ubicacion gps",
            "geolocalizacion", "geo", "lat lng", "lat lon", "latitud longitud", "coords"],
    "LOCALIDAD/BARRIO": ["localidad barrio", "barrio", "localidad", "barrio localidad"],
    "ID TECNICO": ["id tecnico", "id del tecnico", "idtec", "cedula tecnico"],
    "AV/RESULTADO": ["av resultado", "resultado", "av", "estado orden"],
    "SUBACCION/SUBANOMALIA": ["subaccion subanomalia", "subaccion", "subanomalia", "sub accion"],
    "FECHA_CIERRE": ["fecha cierre", "fecha", "fecha de cierre"],
    "OBS_COMBINADA": ["obs combinada", "observacion combinada"],
}
LAT_ALIASES = ["latitud", "lat", "y", "coord y", "coordenada y", "latitude"]
LNG_ALIASES = ["longitud", "long", "lng", "lon", "x", "coord x", "coordenada x", "longitude"]

RE_VS = re.compile(r"(?i)^\s*v\.?\s*s\.?\s*:")
RE_FECHA = re.compile(r"FECHA:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})")


# ============================================================================
# HELPERS
# ============================================================================
def _norm(name):
    """Normaliza un nombre: minusculas, sin tildes, separadores a espacio."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    t = str(name).strip().lower()
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
    for s in ("/", "-", "_"):
        t = t.replace(s, " ")
    return " ".join(t.split())


def _norm_dato(s):
    """Normalizacion agresiva para cruzar contra el maestro: solo letras y numeros."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    t = str(s)
    if "Ã" in t or "Â" in t:                       # repara mojibake
        try:
            t = t.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", t)


def _buscar_col(df, nombres):
    idx = {_norm(c): c for c in df.columns}
    for n in nombres:
        if _norm(n) in idx:
            return idx[_norm(n)]
    return None


def _partir_gps(serie):
    """'10.95, -74.79' -> (lat, lon) numericos. Vectorizado."""
    p = serie.astype(str).str.strip().str.split(r"[,;]\s*|\s+", n=1, regex=True, expand=True)
    if p.shape[1] < 2:
        n = pd.Series(np.nan, index=serie.index)
        return n, n
    return pd.to_numeric(p[0], errors="coerce"), pd.to_numeric(p[1], errors="coerce")


def _zona_de(nombre):
    """Zona de respaldo. Devuelve el nombre CANONICO ('ATLANTICO NORTE'), que es
    el que traen los datos. Antes devolvia 'NORTE' y creaba una zona fantasma."""
    n = nombre.lower()
    for k, v in (("norte", "ATLANTICO NORTE"), ("centro", "ATLANTICO CENTRO"), ("sur", "ATLANTICO SUR")):
        if k in n:
            return v
    return "DESCONOCIDA"


def _clasificar(accion):
    k = _norm(accion).upper().replace(" / ", " ")
    for orig, val in CAUSAS.items():
        if _norm(orig).upper() == k:
            return val
    return CAUSA_DEFECTO


# ============================================================================
# LECTURA DE ARCHIVOS
# ============================================================================
def procesar_archivo(path, zona):
    fname = os.path.basename(path)
    try:
        df = pd.read_excel(path)
    except Exception as e:
        print(f"  ERROR leyendo {fname}: {e}")
        return None
    if df.empty:
        print(f"  AVISO {fname}: vacio.")
        return None

    # Solo ordenes con acta de visita (OBSERVACION que empieza con "VS:")
    c_obs = _buscar_col(df, ["OBSERVACION", "OBSERVACIONES", "OBSERVACION_1"])
    if c_obs is not None:
        antes = len(df)
        df = df[df[c_obs].astype(str).str.match(RE_VS, na=False)].copy()
        print(f"  Filtro VS:  {antes - len(df):>6,} descartadas de {antes:,}  ->  quedan {len(df):,}")
    else:
        print(f"  AVISO {fname}: sin columna OBSERVACION, no se aplica el filtro VS:")
    if df.empty:
        return None

    # Emparejar columnas (tolerante a tildes, espacios y alias)
    idx = {_norm(c): c for c in df.columns}
    slim = pd.DataFrame(index=df.index)
    faltan = []
    for co in COLS_OBJETIVO:
        c = idx.get(_norm(co))
        if c is None:
            for a in COL_ALIASES.get(co, []):
                c = idx.get(_norm(a))
                if c is not None:
                    break
        if c is not None:
            slim[co] = df[c]
        else:
            slim[co] = None
            if co not in ("ZONA", "Estado"):
                faltan.append(co)
    if faltan:
        print(f"  AVISO {fname}: columnas no encontradas -> {faltan}")

    if slim["ZONA"].isna().all():
        slim["ZONA"] = zona

    # GPS: reconstruir desde LATITUD/LONGITUD separadas si vino vacio
    lat, lon = _partir_gps(slim["GPS"])
    malo = lat.isna() | lon.isna()
    if malo.any():
        cl, cn = _buscar_col(df, LAT_ALIASES), _buscar_col(df, LNG_ALIASES)
        if cl and cn:
            cand = df[cl].astype(str).str.strip() + ", " + df[cn].astype(str).str.strip()
            la2, lo2 = _partir_gps(cand)
            rep = malo & la2.notna() & lo2.notna()
            if rep.any():
                slim.loc[rep, "GPS"] = cand[rep]
                print(f"  GPS reconstruido: {int(rep.sum()):,} filas desde '{cl}' + '{cn}'")

    lat, lon = _partir_gps(slim["GPS"])
    ok = lat.notna() & lon.notna()
    print(f"  OK {fname}: {len(slim):,} filas (zona={zona}). GPS legible: {int(ok.sum()):,} ({ok.mean()*100:.1f}%)")
    return slim





# ============================================================================
# CONSTRUCCION DEL MAPA
# ============================================================================
def construir_mapa(df, out_html, publico=False):
    """
    Empaqueta el df ya enriquecido y lo incrusta en plantilla_mapa.html.
    Solo usa las columnas derivadas que este mismo ETL acaba de calcular.
    """
    d = df[df["Estado"].notna() & df["LATITUD"].notna() & df["LONGITUD"].notna()].copy()
    d = d[d["LATITUD"].between(BBOX[0], BBOX[1]) & d["LONGITUD"].between(BBOX[2], BBOX[3])]
    d["dt"] = pd.to_datetime(d["FECHA_EJECUCION"], errors="coerce")
    d = d[d["dt"].notna()].sort_values("dt").reset_index(drop=True)
    if d.empty:
        print("\nAVISO: no quedan filas con Estado + GPS + fecha. No se genera el mapa.")
        return

    # Rellenar antes de indexar: hay ordenes sin MUNICIPIO, y un grupo sin ningun
    # valor hace que .mode()[0] reviente.
    d["MUNICIPIO"] = d["MUNICIPIO"].fillna("SIN MUNICIPIO")
    d["LOCALIDAD/BARRIO"] = d["LOCALIDAD/BARRIO"].fillna("SIN BARRIO")
    d["ZONA"] = d["ZONA"].fillna("SIN ZONA")
    for c in ("TECNICO", "TIPO BRIGADA", "TIPO OS", "TIPO SUSPENSION SOLICITADA",
              "SUBACCION/SUBANOMALIA", "TARIFA", "CAUSA", "FAMILIA_CAUSA"):
        d[c] = d[c].fillna("SIN DATO")
    d["BKEY"] = d["MUNICIPIO"] + " | " + d["LOCALIDAD/BARRIO"]

    if publico:
        # 1. Los nombres de los tecnicos son dato laboral. Se sustituyen por un alias
        #    y la equivalencia se guarda APARTE, no dentro del HTML.
        nombres = sorted(d["TECNICO"].unique())
        alias = {n: f"Tecnico {i:03d}" for i, n in enumerate(nombres, 1)}
        d["TECNICO"] = d["TECNICO"].map(alias)

        # 2. 5 decimales = 1 metro = la casa exacta de un cliente en mora.
        #    3 decimales = ~110 m = la manzana. Los mapas de calor, los hotspots y
        #    el indice de riesgo no cambian: se calculan por barrio, no por casa.
        d["LATITUD"] = d["LATITUD"].round(3)
        d["LONGITUD"] = d["LONGITUD"].round(3)

        # 3. El numero de orden permite volver al sistema y sacar al cliente.
        d["ORDEN"] = 0

        key = os.path.splitext(out_html)[0] + "_equivalencia_tecnicos.csv"
        pd.DataFrame({"ALIAS": list(alias.values()), "TECNICO": list(alias.keys())}).to_csv(
            key, index=False, encoding="utf-8-sig")
        print(f"OK  Modo publico: {len(alias)} tecnicos anonimizados, GPS a ~110 m, sin numero de orden.")
        print(f"    Equivalencia -> {key}   <-- NO la compartas junto al mapa.")

    def idx(col):
        vals = sorted(d[col].dropna().astype(str).unique().tolist())
        return vals, {v: i for i, v in enumerate(vals)}

    barrios, bi = idx("BKEY")
    tecs, ti = idx("TECNICO")
    brigs, gi = idx("TIPO BRIGADA")
    tipos, oi = idx("TIPO OS")
    causas, ci = idx("CAUSA")
    subs, si = idx("SUBACCION/SUBANOMALIA")
    susps, ui = idx("TIPO SUSPENSION SOLICITADA")
    tarifas, fi = idx("TARIFA")
    munis, mi = idx("MUNICIPIO")
    zonas, zi = idx("ZONA")

    EST = {"Efectiva": 0, "Fallida": 1, "Perdida": 2}
    T0 = pd.Timestamp(d["dt"].min().date())

    d["b"] = d["BKEY"].map(bi)
    d["t"] = d["TECNICO"].astype(str).map(ti)
    d["g"] = d["TIPO BRIGADA"].astype(str).map(gi)
    d["o"] = d["TIPO OS"].astype(str).map(oi)
    d["c"] = d["CAUSA"].astype(str).map(ci)
    d["s"] = d["SUBACCION/SUBANOMALIA"].astype(str).map(si)
    d["u"] = d["TIPO SUSPENSION SOLICITADA"].astype(str).map(ui)
    d["f"] = d["TARIFA"].astype(str).map(fi)
    d["e"] = d["Estado"].map(EST)
    d["m"] = ((d["dt"] - T0).dt.total_seconds() // 60).astype(int)
    for c in ("b", "t", "g", "o", "c", "s", "u", "f"):
        d[c] = d[c].fillna(0).astype(int)

    def _moda(x):
        m = x.mode()
        return m.iloc[0] if len(m) else x.iloc[0]

    b_muni = d.groupby("b")["MUNICIPIO"].agg(_moda).to_dict()
    b_zona = d.groupby("b")["ZONA"].agg(_moda).to_dict()

    # Centroide de cada barrio (mediana de sus ordenes): ahi va el marcador.
    bc = []
    for b, grp in d.groupby("b"):
        bc.append([round(float(np.median(grp["LATITUD"])), 5),
                   round(float(np.median(grp["LONGITUD"])), 5)])

    # ---------- LIMITES REALES (GeoJSON) ----------
    # Se enlazan por GEOMETRIA, no por nombre: se mira dentro de que poligono cae
    # cada orden y se asigna por voto de mayoria. El nombre solo cruzaba el 46% de
    # las ordenes; la geometria cruza el 99,8%.
    bpoly, mpoly = [], []
    aqui = os.path.dirname(os.path.abspath(__file__))
    pb = os.path.join(aqui, GEO_BARRIOS)
    pm = os.path.join(aqui, GEO_MUNICIPIOS)

    def _anillos(geom):
        """GeoJSON usa [lon,lat]; Leaflet quiere [lat,lon]. Devuelve poligonos->anillos."""
        t, c = geom["type"], geom["coordinates"]
        polis = [c] if t == "Polygon" else c
        return [[[[round(p[1], 5), round(p[0], 5)] for p in anillo] for anillo in poli]
                for poli in polis]

    try:
        from shapely.geometry import shape
        from shapely.strtree import STRtree
        import shapely as _sh
        tiene_shapely = True
    except ImportError:
        tiene_shapely = False
        print("\n  AVISO: falta 'shapely' (pip install shapely). No se cargan los limites reales.")

    if tiene_shapely and os.path.exists(pb):
        gj = json.load(open(pb, encoding="utf-8"))
        formas = [shape(f["geometry"]) for f in gj["features"]]
        arbol = STRtree(formas)
        pts = _sh.points(d["LONGITUD"].values, d["LATITUD"].values)
        par = arbol.query(pts, predicate="within")
        asign = np.full(len(d), -1)
        asign[par[0]] = par[1]
        d = d.reset_index(drop=True)
        d["_poly"] = asign
        fuera = int((asign < 0).sum())

        # voto de mayoria: barrio del dato -> poligono
        enlace = {}
        for bidx, grp in d[d["_poly"] >= 0].groupby("b"):
            vc = grp["_poly"].value_counts()
            enlace[int(vc.index[0])] = enlace.get(int(vc.index[0]), [])
            enlace[int(vc.index[0])].append((int(bidx), int(vc.iloc[0]), len(grp)))

        # un poligono puede recibir varios barrios del dato: se queda con el mayor
        pol2b = {}
        for p, cands in enlace.items():
            bidx, n, t = max(cands, key=lambda x: x[1])
            pol2b[p] = (bidx, round(n / t, 2))

        bajos = 0
        for i, f in enumerate(gj["features"]):
            pr = f["properties"]
            bidx, cf = pol2b.get(i, (-1, 0))
            if cf and cf < 0.6:
                bajos += 1
            bpoly.append({"n": pr.get("nombre", ""), "m": pr.get("municipio", ""),
                          "b": bidx, "cf": cf, "r": _anillos(f["geometry"])})
        enlazados = sum(1 for p in bpoly if p["b"] >= 0)
        print(f"OK  Limites de barrio: {len(bpoly)} poligonos, {enlazados} enlazados a un barrio del dato.")
        print(f"    {len(d)-fuera:,}/{len(d):,} ordenes ({(len(d)-fuera)/len(d)*100:.1f}%) caen dentro de un poligono.")
        if bajos:
            print(f"    ATENCION: {bajos} poligonos con enlace de baja confianza (<60%).")
            print(f"    El barrio del sistema comercial no coincide con el catastral. Se marcan en el mapa.")
    elif tiene_shapely:
        print(f"  AVISO: no encuentro {GEO_BARRIOS}. Sin limites de barrio.")

    zpoly = []
    pz = os.path.join(aqui, "dashboard", "public", "geojson", "zonas_atlantico.geojson")
    if os.path.exists(pz):
        gj = json.load(open(pz, encoding="utf-8"))
        for f in gj["features"]:
            zpoly.append({
                "n": f["properties"].get("zona", ""),
                "c": f["properties"].get("color", ""),
                "r": _anillos(f["geometry"])
            })
        print(f"OK  Limites de zona: {len(zpoly)} poligonos.")
    else:
        print("  AVISO: no encuentro zonas_atlantico.geojson.")

    if os.path.exists(pm):
        gj = json.load(open(pm, encoding="utf-8"))
        for f in gj["features"]:
            mpoly.append({"n": f["properties"].get("nombre", ""), "r": _anillos(f["geometry"])})
        print(f"OK  Limites de municipio: {len(mpoly)} poligonos.")
    else:
        print(f"  AVISO: no encuentro {GEO_MUNICIPIOS}. Sin limites de municipio.")
    # Encontrar el ultimo mes en curso para division en el frontend
    d["mes_aux"] = d["dt"].dt.strftime("%Y-%m")
    ultimo_mes = d["mes_aux"].max()
    print(f"Ultimo mes en curso detectado: {ultimo_mes}")

    is_recent = d["mes_aux"] == ultimo_mes
    d_recent = d[is_recent].copy()
    d_history = d[~is_recent].copy()
    print(f"Dividiendo datos: {len(d_recent):,} filas para el mes en curso y {len(d_history):,} filas para el historico.")

    LAT0, LON0 = 10.0, -76.0
    ctrl_por_causa = (d.drop_duplicates("CAUSA").set_index("CAUSA")["CONTROLABLE"].to_dict())
    fam_por_causa = (d.drop_duplicates("CAUSA").set_index("CAUSA")["FAMILIA_CAUSA"].to_dict())

    # Etiqueta legible de mes, EXACTA a la del frontend (toLocaleDateString es-CO).
    MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
                "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

    def _mes_label(ym):  # ym = "2026-07"
        y, mo = ym.split("-")
        return f"{MESES_ES[int(mo) - 1]} de {y}"

    def _pts_dict(df):
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

    public_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "public")
    os.makedirs(public_dir, exist_ok=True)

    # ---------- CARGA PEREZOSA POR MES ----------
    # El mes en curso queda dentro de data.json (se carga al arrancar). Cada mes
    # historico se escribe en su propio archivo data_<YYYY-MM>.json y solo se
    # descarga cuando el usuario lo selecciona en el filtro. El manifiesto
    # meta.months lista TODOS los meses con su conteo para poblar el filtro sin
    # descargar nada.
    meses_ordenados = sorted(d["mes_aux"].unique())
    manifest = []
    for ym in meses_ordenados:
        grp = d[d["mes_aux"] == ym]
        if ym == ultimo_mes:
            manifest.append({"key": ym, "label": _mes_label(ym),
                             "n": int(len(grp)), "recent": True})
        else:
            fname = f"data_{ym}.json"
            with open(os.path.join(public_dir, fname), "w", encoding="utf-8") as f:
                json.dump({"pts": _pts_dict(grp)}, f, separators=(",", ":"), ensure_ascii=False)
            manifest.append({"key": ym, "label": _mes_label(ym),
                             "n": int(len(grp)), "file": fname})
            print(f"OK  JSON mes   -> {fname}  ({len(grp):,} ordenes)")

    data = {
        "meta": {"total": int(len(d_recent)),
                 "total_all": int(len(d)),
                 "fecha_min": str(d["dt"].min())[:10], "fecha_max": str(d["dt"].max())[:10],
                 "lat0": LAT0, "lon0": LON0,
                 "generated": str(pd.Timestamp.today().date()),
                 "months": manifest},
        "dim": {"barrios": barrios, "tecs": tecs, "brigs": brigs, "tipos": tipos,
                 "causas": causas, "subs": subs, "susps": susps, "tarifas": tarifas,
                 "munis": munis, "zonas": zonas,
                 "estados": ["Efectiva", "Fallida", "Perdida"],
                 "causa_ctrl": [int(ctrl_por_causa.get(c, 1)) for c in causas],
                 "causa_fam": [fam_por_causa.get(c, "otros") for c in causas],
                 "b_muni": [mi[b_muni[i]] for i in range(len(barrios))],
                 "b_zona": [zi[b_zona[i]] for i in range(len(barrios))]},
        "geo": {"bc": bc, "bp": bpoly, "mp": mpoly, "zp": zpoly},
        "pts": _pts_dict(d_recent),
    }

    # Guardar JSON para Next.js
    json_path = os.path.join(public_dir, "data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    print(f"OK  JSON data  -> {json_path}  ({os.path.getsize(json_path)/1e6:.2f} MB)")

    # El JSON va dentro de una etiqueta <script> para el HTML de respaldo
    aqui = os.path.dirname(os.path.abspath(__file__))
    plantilla_path = os.path.join(aqui, PLANTILLA)
    if not os.path.exists(plantilla_path):
        plantilla_path = os.path.join(aqui, "Nueva carpeta", PLANTILLA)

    if os.path.exists(plantilla_path):
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
        html = open(plantilla_path, encoding="utf-8").read().replace("/*__DATA__*/", payload)
        os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"OK  Mapa       -> {out_html}  ({os.path.getsize(out_html)/1e6:.2f} MB, {len(d):,} ordenes)")
    else:
        print(f"  AVISO: no encuentro {PLANTILLA} en el directorio raiz ni en 'Nueva carpeta'. Se omite la generacion del mapa HTML de respaldo.")


# ============================================================================
# MAIN
# ============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DIR)
    ap.add_argument("--csv", default=None,
                    help="Ruta del CSV consolidado. Por defecto NO se genera (solo JSON).")
    ap.add_argument("--html", default=OUT_HTML)
    ap.add_argument("--maestro", default=MAESTRO_PATH)
    ap.add_argument("--solo-csv", action="store_true", help="No genera el mapa")
    ap.add_argument("--publico", action="store_true",
                    help="Mapa apto para compartir: tecnicos anonimizados, GPS a ~110 m, sin numero de orden")
    a = ap.parse_args()

    print("=" * 72)
    print("  ETL SCR — consolidacion + mapa operativo")
    print("=" * 72)

    db_url = os.environ.get("SCR_DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: No se especifico la variable de entorno SCR_DATABASE_URL en el archivo .env")

    print("\nConectando a la base de datos...")
    import psycopg2
    try:
        conn = psycopg2.connect(db_url)
        print("OK  Conexion establecida con exito.")
    except Exception as e:
        sys.exit(f"ERROR conectando a la base de datos: {e}")

    query = """
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
    conv = {}
    print("Ejecutando consulta SQL en dbanalitica.historico_mo...")
    try:
        cur = conn.cursor()
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        data = cur.fetchall()
        df = pd.DataFrame(data, columns=columns)
        print(f"OK  Se cargaron {len(df):,} filas desde la base de datos.")

        # Cargar Convertidor_Estados de dbanalitica.maestro_tarifas
        print("Cargando Convertidor_Estados desde dbanalitica.maestro_tarifas...")
        cur.execute('SELECT DISTINCT "SUBACCION/SUBANOMALIA", estado FROM dbanalitica.maestro_tarifas WHERE "SUBACCION/SUBANOMALIA" IS NOT NULL AND estado IS NOT NULL')
        for sub, est in cur.fetchall():
            k = _norm_dato(sub)
            if k:
                conv[k] = str(est).strip()
        print(f"  OK Convertidor_Estados desde BD: {len(conv)} subacciones mapeadas.")

        cur.close()
        conn.close()
    except Exception as e:
        sys.exit(f"ERROR ejecutando la consulta en la base de datos: {e}")

    df["Estado"] = None
    print("-" * 72)

    # --- Solo ordenes con acta de visita (OBSERVACION que empieza con "VS:") ---
    antes = len(df)
    df = df[df["OBSERVACION"].astype(str).str.match(RE_VS, na=False)].copy()
    print(f"Filtro VS:  {antes - len(df):>6,} descartadas de {antes:,}  ->  quedan {len(df):,}")

    # --- identificadores como TEXTO (sin el ".0" que les pega pandas) ---
    for c in COLS_TEXTO:
        num = pd.to_numeric(df[c], errors="coerce")
        ent = num.notna() & (num % 1 == 0)
        df[c] = df[c].astype("object")
        df.loc[ent, c] = num[ent].astype("int64").astype(str)
    print("OK  ID TECNICO / ORDEN / NIC forzados a texto (sin '.0').")

    # --- limpieza de texto ---
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.strip() if isinstance(v, str) else v)
            df[c] = df[c].replace({"": None, "nan": None, "None": None})

    # --- OBS_COMBINADA: el fallback que estaba comentado pero nunca escrito ---
    n = int(df["OBS_COMBINADA"].isna().sum())
    df["OBS_COMBINADA"] = df["OBS_COMBINADA"].fillna(df["OBSERVACION"])
    if n:
        print(f"OK  OBS_COMBINADA: {n:,} filas rellenadas con OBSERVACION.")

    # --- DERIVADA 1-2: LATITUD / LONGITUD ---
    df["LATITUD"], df["LONGITUD"] = _partir_gps(df["GPS"])
    dentro = (df["LATITUD"].between(BBOX[0], BBOX[1]) & df["LONGITUD"].between(BBOX[2], BBOX[3]))
    df.loc[~dentro, ["LATITUD", "LONGITUD"]] = np.nan
    print(f"OK  LATITUD/LONGITUD: {int(dentro.sum()):,}/{len(df):,} dentro del Atlantico ({dentro.mean()*100:.1f}%).")

    # --- DERIVADA 3: FECHA_EJECUCION (tomada directamente de FECHA_CIERRE) ---
    dt = pd.to_datetime(df["FECHA_CIERRE"], errors="coerce")
    df["FECHA_EJECUCION"] = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    print(f"OK  FECHA_EJECUCION (desde FECHA_CIERRE): {int(dt.notna().sum()):,}/{len(df):,} con fecha y hora.")

    # --- homologacion de brigada ---
    df["TIPO BRIGADA"] = df["TIPO BRIGADA"].map(
        lambda v: HOMOLOG_BRIGADA.get(_norm(v), str(v).strip()) if pd.notna(v) else v)
    canon = set(HOMOLOG_BRIGADA.values())
    raras = sorted(set(df.loc[~df["TIPO BRIGADA"].isin(canon) & df["TIPO BRIGADA"].notna(), "TIPO BRIGADA"]))
    print(f"OK  Brigadas: {int(df['TIPO BRIGADA'].isin(canon).sum()):,} homologadas.")
    if raras:
        print(f"    AVISO: sin homologar -> {raras[:5]}")

    # --- Estado (cruce con el maestro desde la BD) ---
    if conv:
        df["Estado"] = df["SUBACCION/SUBANOMALIA"].map(
            lambda s: conv.get(_norm_dato(s)) if pd.notna(s) else None)
    else:
        df["Estado"] = None

    if df["Estado"].notna().sum() == 0:
        print("\n" + "!" * 72)
        print("  ERROR: 'Estado' quedo vacio en TODAS las filas.")
        print("  Sin Estado no hay efectivas/fallidas/perdidas, y el mapa no se puede construir.")
        print("  Revisa la tabla dbanalitica.maestro_tarifas en la base de datos.")
        print("!" * 72)
        sys.exit(1)

    sin = df["Estado"].isna()
    print(f"OK  Estado: {int((~sin).sum()):,} asignados, {int(sin.sum()):,} sin match.")
    if sin.any():
        faltantes = sorted(set(df.loc[sin, "SUBACCION/SUBANOMALIA"].dropna()))
        print(f"    ATENCION: estas {int(sin.sum()):,} ordenes NO entran al mapa.")
        print(f"    Agrega estas subacciones al Convertidor_Estados:")
        for s in faltantes[:8]:
            print(f"      - {s}")

    # --- DERIVADA 4-6: CAUSA / FAMILIA_CAUSA / CONTROLABLE ---
    cl = df.apply(lambda r: ("Efectiva", "exito", 1) if r["Estado"] == "Efectiva"
                  else _clasificar(r["ACCION"]), axis=1, result_type="expand")
    df["CAUSA"], df["FAMILIA_CAUSA"], df["CONTROLABLE"] = cl[0], cl[1], cl[2].astype(int)

    no_ef = df[df["Estado"].notna() & (df["Estado"] != "Efectiva")]
    sin_tax = sorted(set(no_ef.loc[no_ef["CAUSA"] == "Otras causas", "ACCION"].dropna()))
    if sin_tax:
        nn = int((no_ef["CAUSA"] == "Otras causas").sum())
        print(f"    AVISO: {len(sin_tax)} ACCION sin clasificar ({nn:,} ordenes). Cuentan EN CONTRA del tecnico:")
        for s in sin_tax[:6]:
            print(f"      - {s}")

    n_nc = int((df["CONTROLABLE"] == 0).sum())
    n_bad = int((df["Estado"].notna() & (df["Estado"] != "Efectiva")).sum())
    print(f"OK  CONTROLABLE: {n_nc:,} de {n_bad:,} no efectivas ({n_nc/max(n_bad,1)*100:.1f}%) NO dependen de la operacion.")

    # --- duplicados ---
    dup = int(df["ORDEN"].duplicated().sum())
    if dup:
        df = df.sort_values("FECHA_EJECUCION").drop_duplicates("ORDEN", keep="last")
        print(f"OK  Duplicados: {dup:,} ordenes repetidas, se conserva la mas reciente.")

    df = df[COLS_OBJETIVO + COLS_DERIVADAS]

    # --- resumen ---
    print("\n" + "=" * 72)
    print("  RESUMEN")
    print("=" * 72)
    for z, k in df["ZONA"].value_counts().items():
        print(f"  {z:<20} {k:>8,}")
    print(f"  {'TOTAL':<20} {len(df):>8,}\n")
    ef = int((df["Estado"] == "Efectiva").sum())
    for e, k in df["Estado"].value_counts().items():
        print(f"  {e:<20} {k:>8,}  ({k/len(df)*100:.1f}%)")
    den = len(df) - n_nc
    print(f"\n  Efectividad bruta     {ef/len(df)*100:>7.1f} %")
    print(f"  Efectividad ajustada  {ef/max(den,1)*100:>7.1f} %   (excluye las causas no controlables)")
    print()

    # CSV: solo si se pide explícitamente (--csv RUTA) o en modo --solo-csv.
    # Por defecto el ETL produce ÚNICAMENTE el JSON que consume la app.
    if a.csv or a.solo_csv:
        _guardar_csv(df, a.csv or OUT_CSV)
    if not a.solo_csv:
        construir_mapa(df, a.html, publico=a.publico)
    print("\nETL - fin")


def _guardar_csv(df, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    try:
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"OK  Consolidado -> {out}  ({len(df):,} filas, {len(df.columns)} columnas)")
    except PermissionError:
        b, e = os.path.splitext(out)
        alt = f"{b}_{datetime.datetime.now():%Y%m%d_%H%M%S}{e}"
        df.to_csv(alt, index=False, encoding="utf-8-sig")
        print("\n" + "!" * 72)
        print("  El CSV destino esta ABIERTO EN EXCEL y no se pudo sobreescribir.")
        print(f"  Se guardo como: {alt}")
        print("!" * 72)


if __name__ == "__main__":
    main()