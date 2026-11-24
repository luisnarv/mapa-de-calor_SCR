"""Informe HTML del backtest de la matriz de alcance: lo que dijo el modelo en un mes que no vio.

    cd "Modelos/Recomendacion Alcance" && .venv/Scripts/python.exe informe/informe_html.py [2026-10]

El modelo de producción ya vio todos los meses, así que aquí se reentrena una copia con la misma
receta (parámetros e iteraciones de modelos/Metadata_Modelo_Alcance.json) solo con lo anterior al
mes de prueba, y se puntúa ese mes con ella. Las bandas y sus % salen siempre de ese histórico.

Sin las 4 variables del técnico: en el mes de prueba no se sabe quién va a ir, y usar al que fue es
fuga. CANASTA sí entra: el alcance se decide al despachar y es justo lo que la matriz compara.

El HTML lleva NICs (datos de clientes): queda solo como archivo local en salidas/.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from dotenv import load_dotenv
from sklearn.metrics import roc_auc_score
from sqlalchemy import create_engine, text

RAIZ = Path(__file__).resolve().parents[1]
os.chdir(RAIZ)

MES = sys.argv[1] if len(sys.argv) > 1 else '2026-10'
TECNICO = ['ORDENES_PREV_TECNICO', 'TASA_EF_TECNICO', 'ORDENES_PREV_TECNICO_MUNICIPIO',
           'TASA_EF_TECNICO_MUNICIPIO']
TOP = 10
POCO_HISTORIAL = 3

# Cómo mostrar cada variable en unidades legibles. LOG_DEUDA se muestra en pesos: el logaritmo
# no le dice nada a quien lee el informe.
PESOS = {'deuda_act', 'DEUDA_POR_FACTURA', 'DELTA_DEUDA'}
NOMBRES = {
    'CANASTA': 'Alcance de la orden', 'tipo_os': 'Tipo de orden', 'territorio': 'Territorio',
    'zona': 'Zona', 'municipio': 'Municipio', 'localidad_barrio': 'Barrio', 'SEGMENTO': 'Segmento tarifa',
    'RESULTADO_ANTERIOR': 'Resultado anterior', 'TIPO_OS_ANTERIOR': 'Tipo de orden anterior',
    'CANASTA_ANTERIOR': 'Alcance anterior', 'ESTRATO': 'Estrato', 'deuda_act': 'Deuda',
    'LOG_DEUDA': 'Deuda', 'cant_factura_act': 'Facturas adeudadas', 'DEUDA_POR_FACTURA': 'Deuda por factura',
    'ORDENES_PREVIAS': 'Órdenes previas', 'EFECTIVAS_PREVIAS': 'Efectivas previas',
    'FALLIDAS_PREVIAS': 'Fallidas previas', 'PERDIDAS_PREVIAS': 'Perdidas previas',
    'IMPOSIB_PREVIAS': 'Imposibilidades previas', 'PAGOS_CLIENTE_PREVIOS': 'Pagos en la visita previos',
    'RESISTENCIAS_PREVIAS': 'Resistencias previas', 'SUSP_PREVIAS': 'Suspensiones previas',
    'SUSP_EFECTIVAS_PREVIAS': 'Suspensiones efectivas previas', 'TASA_EF_HIST': 'Efectividad previa del NIC',
    'TASA_EF_SUSP_HIST': 'Efectividad previa en suspensión', 'DIAS_DESDE_ANTERIOR': 'Días desde la orden anterior',
    'SIN_ORDEN_90D': 'Sin orden en 90 días', 'DELTA_DEUDA': 'Cambio de deuda',
    'ORDENES_PREVIAS_CANASTA': 'Órdenes previas con este alcance',
    'EF_PREVIAS_CANASTA': 'Efectivas previas con este alcance',
    'TASA_EF_CANASTA_NIC': 'Efectividad previa del NIC con este alcance',
    'ORDENES_PREV_BARRIO': 'Órdenes previas del barrio', 'TASA_EF_BARRIO': 'Efectividad del barrio',
    'TASA_EF_MUNICIPIO': 'Efectividad del municipio', 'ORDENES_PREV_BARRIO_CANASTA': 'Órdenes del barrio con este alcance',
    'TASA_EF_BARRIO_CANASTA': 'Efectividad del barrio con este alcance',
    'TASA_EF_MUNICIPIO_CANASTA': 'Efectividad del municipio con este alcance',
    'ORDENES_PREV_TRAFO': 'Órdenes previas del transformador', 'TASA_EF_TRAFO': 'Efectividad del transformador',
    'TASA_EF_CIRCUITO': 'Efectividad del circuito', 'ORDENES_PREV_TRAFO_CANASTA': 'Órdenes del trafo con este alcance',
    'TASA_EF_TRAFO_CANASTA': 'Efectividad del trafo con este alcance',
    'TASA_AGRESIVO_TRAFO': 'Agresividad en el transformador', 'TASA_IMPOSIB_TRAFO': 'Imposibilidad en el transformador',
    'TASA_PAGO_VISITA_TRAFO': 'Pago en la visita en el transformador',
    'PROB_PAGO_SIN_INTERVENCION': 'Propensión de pago sin intervención',
    'PROB_PAGO_CON_INTERVENCION': 'Propensión de pago con intervención',
}

HISTORIAL_SQL = text("""
SELECT orden, nic, fecha_cierre, tipo_os, descripcion_de_tipo_os, brigada_homologada,
       estado_norm, subaccion_homologada, av_resultado, deuda_act, cant_factura_act
FROM dbanalitica.historico_mo
WHERE nic = ANY(:nics) AND estado_osf IS DISTINCT FROM 'Anulada'
""")


def unidad(var: str) -> str:
    if var in PESOS or var == 'LOG_DEUDA':
        return 'pesos'
    if var.startswith(('TASA_', 'PROB_')):
        return 'pct'
    if var == 'SIN_ORDEN_90D':
        return 'sino'
    return 'num'


def modelo_backtest(df: pd.DataFrame, features: list[str], categoricas: list[str], meta: dict) -> CatBoostClassifier:
    """Copia de la receta de producción entrenada solo hasta el mes anterior. Se guarda para no
    reentrenar cada vez que se retoca el HTML (tarda varios minutos)."""
    ruta = Path(f'modelos/backtest_hasta_antes_de_{MES}.cbm')
    m = CatBoostClassifier()
    if ruta.exists():
        m.load_model(str(ruta))
        if list(m.feature_names_) == features:
            return m
    tr = df['PERIODO'] < MES
    X = df.loc[tr, features].copy()
    for c in categoricas:
        X[c] = X[c].astype(str)
    params = {k: v for k, v in meta['params'].items()}
    m = CatBoostClassifier(iterations=meta['iteraciones'], verbose=200, thread_count=-1, **params)
    m.fit(Pool(X, df.loc[tr, 'EFECTIVA'], cat_features=categoricas))
    m.save_model(str(ruta))
    return m


Banda = tuple[float, float, bool, bool]  # desde, hasta, incluye desde, incluye hasta


def bandas_numericas(entreno: pd.Series) -> list[Banda]:
    """Quintiles del histórico. Un valor que se repite en varios cortes (el 0 de «fallidas previas»
    ocupa el 60 %) se vuelve una banda propia: si no, los cortes repetidos se funden y quedan 2
    bandas que no dicen nada. Con 6 valores distintos o menos, cada valor es su banda."""
    v = entreno.dropna()
    if v.nunique() <= 6:
        return [(x, x, True, True) for x in sorted(v.unique())]
    bordes, veces = np.unique(np.quantile(v, [0, .2, .4, .6, .8, 1]), return_counts=True)
    puntual = dict(zip(bordes, veces >= 2))
    entero = bool((v % 1 == 0).all())
    bandas: list[Banda] = []
    for k, x in enumerate(bordes):
        if puntual[x]:
            bandas.append((x, x, True, True))
        if k + 1 < len(bordes):
            banda = (x, bordes[k + 1], k == 0 and not puntual[x], not puntual[bordes[k + 1]])
            # Entre dos enteros consecutivos excluidos no cabe nada: (0, 1) en un conteo.
            if not (entero and banda[1] - banda[0] <= 1 and not banda[2] and not banda[3]):
                bandas.append(banda)
    return bandas


def asignar_numerica(x: pd.Series, bandas: list[Banda]) -> pd.Series:
    # Lo que se salga del rango del histórico cae en la banda del extremo: el mes de prueba puede
    # traer valores que el histórico no vio. Los nulos quedan en -1 («sin dato»).
    xc = x.clip(bandas[0][0], bandas[-1][1])
    out = np.full(len(x), -1)
    for i, (a, b, ia, ib) in enumerate(bandas):
        m = (((xc > a) | (ia & (xc == a))) & ((xc < b) | (ib & (xc == b)))).values
        out[(out == -1) & m] = i
    return pd.Series(out, index=x.index)


def fmt(x: float, unid: str) -> str:
    """Número en formato es-CO: punto de miles, coma decimal."""
    miles = lambda n, d: f'{n:,.{d}f}'.replace(',', '#').replace('.', ',').replace('#', '.')  # noqa: E731
    if unid == 'pesos':
        return '$' + miles(x, 0)
    if unid == 'pct':
        return miles(x * 100, 1) + ' %'
    if unid == 'sino':
        return 'Sí' if x else 'No'
    return miles(x, 0) if float(x).is_integer() else miles(x, 2)


def etiqueta(banda: Banda, var: str, entero: bool) -> str:
    a, b, ia, ib = banda
    ver = (lambda y: np.expm1(y)) if var == 'LOG_DEUDA' else (lambda y: y)
    u = unidad(var)
    if a == b:
        return fmt(ver(a), u)
    if entero:
        a, b = (a if ia else a + 1), (b if ib else b - 1)
        return fmt(a, u) if a == b else f'{fmt(a, u)} – {fmt(b, u)}'
    return f"{'' if ia else '> '}{fmt(ver(a), u)} – {'' if ib else '< '}{fmt(ver(b), u)}"


def main() -> None:
    meta = json.load(open('modelos/Metadata_Modelo_Alcance.json', encoding='utf-8'))
    features = [f for f in meta['features'] if f not in TECNICO]
    categoricas = [c for c in meta['categoricas'] if c in features]

    df = pd.read_parquet('datos/df_universo_alcance.parquet')
    df['nic'] = df['nic'].astype(str)
    tr = df[df['PERIODO'] < MES].copy()
    te = df[df['PERIODO'] == MES].copy()
    if te.empty:
        raise SystemExit(f'No hay órdenes de {MES} en datos/df_universo_alcance.parquet: corre el notebook 1.')

    modelo = modelo_backtest(df, features, categoricas, meta)
    Xte = te[features].copy()
    for c in categoricas:
        Xte[c] = Xte[c].astype(str)
    te['p'] = modelo.predict_proba(Xte)[:, 1]
    te['nivel'] = np.select([te['ORDENES_PREVIAS'] == 0, te['ORDENES_PREVIAS'] < POCO_HISTORIAL],
                            ['sin', 'poco'], 'con')
    con_hist = te['nivel'] != 'sin'

    imp = pd.Series(modelo.get_feature_importance(), index=features).sort_values(ascending=False)
    top = imp.head(TOP)

    # Historial completo de los NICs del mes, todas sus órdenes (no solo las del universo).
    cache = Path(f'datos/historial_nics_{MES}.parquet')
    hist = pd.read_parquet(cache) if cache.exists() else None
    if hist is None or 'deuda_act' not in hist.columns:
        load_dotenv('.env')
        engine = create_engine(os.environ['SCR_DATABASE_URL'])
        with engine.connect() as c:
            hist = pd.read_sql(HISTORIAL_SQL, c, params={'nics': te['nic'].unique().tolist()})
        hist.to_parquet(cache, index=False)
    hist['nic'] = hist['nic'].astype(str)
    hist = hist.sort_values(['nic', 'fecha_cierre', 'orden'])

    # «Resultado anterior» viene como código (ON201): se le pega la causa que más se registra con él.
    desc_codigo = (hist.dropna(subset=['av_resultado', 'subaccion_homologada'])
                       .assign(av=lambda h: h['av_resultado'].str.upper().str.strip())
                       .groupby('av')['subaccion_homologada'].agg(lambda s: s.value_counts().index[0]))

    # Mismo formato de datos que la plantilla de Electrohuila (D.kpi, D.vars, D.rows, D.hist), para
    # que los dos informes se vean y se lean igual.
    vars_d, posiciones = [], {}
    for var, peso in top.items():
        cat = var in categoricas
        desc: dict[str, str] = {}
        if cat:
            cats = tr[var].astype(str).value_counts().index[:8].tolist()
            if var == 'RESULTADO_ANTERIOR':
                desc = {c: f'{c} · {desc_codigo[c]}' for c in cats if c in desc_codigo}
            etiquetas = [desc.get(c, c) for c in cats] + ['(otros)']
            def banda(s: pd.Series, cats=cats) -> pd.Series:
                s = s.astype(str)
                return pd.Series(np.where(s.isin(cats), s.map({c: i for i, c in enumerate(cats)}), len(cats)),
                                 index=s.index).astype(int)
        else:
            bandas = bandas_numericas(tr[var])
            entero = bool((tr[var].dropna() % 1 == 0).all())
            etiquetas = [etiqueta(b, var, entero) for b in bandas]
            banda = lambda s, bs=bandas: asignar_numerica(s, bs)  # noqa: E731
        btr, bte = banda(tr[var]), banda(te[var])
        filas, pos = [], {}
        for i in list(range(len(etiquetas))) + [-1]:
            mt, me = btr == i, bte == i
            if not (mt.any() or me.any()):  # «(otros)» o «sin dato» vacíos no se muestran
                continue
            mh = me & con_hist
            pos[i] = len(filas)
            filas.append({
                'k': 'sin dato' if i == -1 else etiquetas[i],
                'h_ef': int(tr.loc[mt, 'EFECTIVA'].sum()), 'h_n': int(mt.sum()),
                'o_ef': int(te.loc[me, 'EFECTIVA'].sum()), 'o_n': int(me.sum()),
                'o_p': round(float(te.loc[mh, 'p'].mean()), 4) if mh.any() else None,
            })
        te[f'_b_{var}'] = bte
        posiciones[var] = pos
        vars_d.append({'var': var, 'nombre': NOMBRES.get(var, var), 'imp': round(float(peso), 2),
                       'cat': cat, 'desc': desc, 'bands': filas})

    def valor(v: dict, x) -> str:
        if pd.isna(x):
            return '—'
        if v['cat']:
            return v['desc'].get(str(x), str(x))
        return fmt(np.expm1(x) if v['var'] == 'LOG_DEUDA' else x, unidad(v['var']))

    def causa(estado, sub) -> str | None:
        """Fallido = no efectiva: la causa dice si fue Fallida o Perdida (las perdidas no se cobran)."""
        if estado == 'EFECTIVA':
            return None
        est = str(estado).capitalize() if pd.notna(estado) else 'Sin estado'
        return f"{est} · {sub if pd.notna(sub) else '—'}"

    def entero_o_none(x) -> int | None:
        return None if pd.isna(x) else int(round(float(x)))

    te = te.sort_values(['fecha_cierre', 'nic']).reset_index(drop=True)
    rows = []
    for rr in te.to_dict('records'):
        sin = rr['nivel'] == 'sin'
        dijo = rr['p'] >= .5
        rows.append([
            rr['nic'], rr['fecha_cierre'].strftime('%Y-%m-%d'), rr['municipio'], rr['zona'],
            None if sin else round(float(rr['p']) * 100, 1),
            'EFECTIVA' if rr['EFECTIVA'] == 1 else 'FALLIDA',
            causa(rr['estado_norm'], rr['subaccion_homologada']),
            int(rr['EFECTIVAS_PREVIAS']), int(rr['ORDENES_PREVIAS'] - rr['EFECTIVAS_PREVIAS']),
            None if sin or pd.isna(rr['TASA_EF_HIST']) else int(round(rr['TASA_EF_HIST'] * 100)),
            entero_o_none(rr['cant_factura_act']), entero_o_none(rr['deuda_act']), rr['CANASTA'],
            None if sin else ('OK' if dijo == (rr['EFECTIVA'] == 1) else 'FP' if dijo else 'FN'),
            [[valor(v, rr[v['var']]), posiciones[v['var']][rr[f"_b_{v['var']}"]]] for v in vars_d],
            int(rr['ORDENES_PREVIAS']),
        ])

    hist_d: dict[str, list] = {}
    for h in hist.to_dict('records'):
        est = str(h['estado_norm']).upper() if pd.notna(h['estado_norm']) else None
        hist_d.setdefault(h['nic'], []).append([
            pd.Timestamp(h['fecha_cierre']).strftime('%Y-%m-%d'), est == 'EFECTIVA',
            # La causa lleva el tipo de orden: aquí también hay reconexiones, no solo suspensiones.
            f"{h['descripcion_de_tipo_os'] or '—'} · {causa(est, h['subaccion_homologada']) or h['subaccion_homologada'] or '—'}",
            h['brigada_homologada'] or '—', entero_o_none(h['cant_factura_act']), entero_o_none(h['deuda_act'])])

    h = te[te['nivel'] != 'sin']
    poco = te[te['nivel'] == 'poco']
    kpi = {
        'desde': str(te['fecha_cierre'].min().date()), 'hasta': str(te['fecha_cierre'].max().date()),
        'corte': str(tr['fecha_cierre'].max().date()), 'tipos': ', '.join(sorted(te['tipo_os'].astype(str).unique())),
        'n': len(h), 'nics': int(h['nic'].nunique()), 'sin_hist': int((te['nivel'] == 'sin').sum()),
        'ef': int(h['EFECTIVA'].sum()), 'fa': int((h['EFECTIVA'] == 0).sum()),
        'real': round(float(h['EFECTIVA'].mean()), 4), 'modelo': round(float(h['p'].mean()), 4),
        'auc': round(float(roc_auc_score(h['EFECTIVA'], h['p'])), 3),
        'h_n': len(tr), 'h_ef': int(tr['EFECTIVA'].sum()),
        'lh': {'n': len(poco), 'modelo': round(float(poco['p'].mean()), 4),
               'real': round(float(poco['EFECTIVA'].mean()), 4)},
    }

    datos = {'kpi': kpi, 'vars': [{k: v[k] for k in ('nombre', 'imp', 'bands')} for v in vars_d],
             'rows': rows, 'hist': hist_d}
    meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
             'octubre', 'noviembre', 'diciembre']
    anio, m = map(int, MES.split('-'))
    previo = meses[m - 2]  # con m=1 da el índice -1, diciembre: el mes anterior a enero
    textos = {'__MES_ISO__': MES, '__MES_MIN__': meses[m - 1], '__MES_ABR__': meses[m - 1][:3].capitalize() + '.',
              '__MES__': f'{meses[m - 1].capitalize()} {anio}', '__PREV_ABR__': previo[:3] + '.', '__PREV__': previo}
    html = Path(__file__).with_name('plantilla_informe.html').read_text(encoding='utf-8')
    for k, v in textos.items():
        html = html.replace(k, v)
    html = html.replace('/*DATOS*/', 'const D=' + json.dumps(datos, ensure_ascii=False, separators=(',', ':')) + ';')
    salida = Path(f'salidas/informe_backtest_{MES}.html')
    salida.write_text(html, encoding='utf-8')
    print(json.dumps({**kpi, 'archivo': str(salida), 'mb': round(salida.stat().st_size / 1e6, 1),
                      'top': list(top.index)}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
