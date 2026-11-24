"""Propensión de pago del servicio externo, como variable de la matriz.

Para entrenar hace falta la propensión que el servicio habría dado ANTES de cada suspensión, y el
servicio solo responde por hoy. Por eso aquí se corre el mismo código y los mismos modelos del
servicio (se importan de su carpeta, no se copian) sobre los datos recortados a una fecha de corte.
Para la matriz de hoy se consulta el servicio de verdad (`consultar_servicio`).

Si cambia el servicio (variables, modelos o ventana de meses), esto lo sigue solo; pero hay que
reentrenar la matriz, porque aprendió con los números del servicio anterior.
"""
import json
import os
import pickle
import sys
import urllib.request
from pathlib import Path

import pandas as pd
from catboost import CatBoostClassifier
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

RUTA_SERVICIO = Path(os.getenv('RUTA_SERVICIO_PROPENSION')
                     or Path(__file__).resolve().parents[3] / 'Modelo Propensión de Pago Servic 2')
URL_SERVICIO = os.getenv('URL_SERVICIO_PROPENSION', 'http://52.88.48.137/propension')
COLS = ['PROB_PAGO_SIN_INTERVENCION', 'PROB_PAGO_CON_INTERVENCION']

if not RUTA_SERVICIO.exists():
    raise RuntimeError(f'No encuentro el servicio de propensión en {RUTA_SERVICIO}: '
                       'define RUTA_SERVICIO_PROPENSION con la carpeta del servicio.')
sys.path.insert(0, str(RUTA_SERVICIO))
from app.config import MESES_HISTORICO  # noqa: E402
from app.services import db_service  # noqa: E402
from app.services.variables_service import calcular_variables  # noqa: E402


def _cargar(nombre: str) -> tuple[CatBoostClassifier, dict]:
    m = CatBoostClassifier()
    m.load_model(str(RUTA_SERVICIO / 'modelos' / f'Modelo_ProbPago_{nombre}.cbm'))
    with open(RUTA_SERVICIO / 'modelos' / f'Metadata_Modelo_ProbPago_{nombre}.pkl', 'rb') as f:
        return m, pickle.load(f)


MODELOS = {'PROB_PAGO_SIN_INTERVENCION': _cargar('SinIntervencion'),
           'PROB_PAGO_CON_INTERVENCION': _cargar('ConIntervencion')}


def _formato_balanza(b: pd.DataFrame) -> pd.DataFrame:
    """Lo que devolvería `db_service.consultar_balanza`."""
    b = b.copy()
    b.columns = b.columns.str.upper()
    return b.rename(columns={'FECHA_PAGO': 'F_APLICACION_PAGO'})


def _formato_mo(m: pd.DataFrame) -> pd.DataFrame:
    """Lo que devolvería `db_service.consultar_mano_obra`."""
    m = m.copy()
    m.columns = m.columns.str.upper()
    m = m.rename(columns={'NIC': 'CUENTA', 'TERRITORIO': 'TERRITORIAL',
                          'DESCRIPCION_DE_TIPO_OS': 'DESCRIPCION_TIPO_OS'})
    m['CUENTA'] = pd.to_numeric(m['CUENTA'], errors='coerce')
    return m


def propension_al_corte(balanza: pd.DataFrame, mo: pd.DataFrame, corte: pd.Timestamp,
                        cuentas: list[int]) -> pd.DataFrame:
    """Lo que el servicio habría respondido el día `corte` para esas cuentas (índice = cuenta).

    Recorta lo que el servicio no podía ver ese día: facturas posteriores, pagos aplicados después
    —incluido el que el cliente hace cuando llega la brigada, que es justo lo que se quiere
    predecir— y órdenes cerradas después.
    """
    limite = (corte.replace(day=1) - relativedelta(months=MESES_HISTORICO))
    b = balanza[balanza['cuenta'].isin(cuentas)
                & (balanza['anio'] * 12 + balanza['mes'] >= limite.year * 12 + limite.month)
                & (balanza['fecha_facturacion'].isna() | (balanza['fecha_facturacion'] <= corte))]
    b = b.assign(fecha_pago=b['fecha_pago'].where(b['fecha_pago'] <= corte))
    desde = corte - relativedelta(months=MESES_HISTORICO)
    m = mo[mo['nic'].isin([str(c) for c in cuentas])
           & (mo['fecha_cierre'] >= desde) & (mo['fecha_cierre'] <= corte)]

    # Se reemplazan solo las dos consultas: el cruce balanza × órdenes queda del servicio.
    originales = db_service.consultar_balanza, db_service.consultar_mano_obra
    db_service.consultar_balanza = lambda c, meses=None: _formato_balanza(b)
    db_service.consultar_mano_obra = lambda c, meses=None: _formato_mo(m)
    try:
        df = db_service.consultar_cuenta_completa(list(cuentas))
    finally:
        db_service.consultar_balanza, db_service.consultar_mano_obra = originales
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS, dtype=float)
    df = calcular_variables(df)

    # Misma fila que `seleccionar_fila` sin periodo: el último mes y, dentro de él, la última orden.
    df = df[df['PERIODO_ORDEN'] == df.groupby('CUENTA')['PERIODO_ORDEN'].transform('max')]
    fila = df.sort_values(['CUENTA', 'FECHA_CIERRE'], kind='stable').groupby('CUENTA').tail(1).set_index('CUENTA')

    salida = pd.DataFrame(index=fila.index)
    for col, (modelo, meta) in MODELOS.items():
        # Igual que `_preparar_X` del servicio, pero para todas las filas de una vez.
        X = fila[meta['features']].copy()
        for c in meta['cat_features']:
            X[c] = X[c].astype(object).where(X[c].notna(), 'SIN_VALOR').astype(str)
        for c in meta['num_features']:
            X[c] = pd.to_numeric(X[c], errors='coerce')
        salida[col] = modelo.predict_proba(X)[:, 1]
    return salida


# Desde cuándo se guarda: la ventana del servicio (6 meses) antes del primer mes del universo.
INICIO = pd.Timestamp('2024-10-01')
# Lo que se vuelve a bajar cada día: la ventana del servicio más un mes, porque a las facturas de
# meses anteriores les siguen llegando pagos. Lo más viejo ya no cambia ni entra en la ventana.
MESES_REFRESCO = MESES_HISTORICO + 1

_SQL_BALANZA = text("""
SELECT cuenta, anio, mes, periodo, subcategoria_comercial, fecha_facturacion, fecha_pago, deuda_total, num_facturas
FROM dbanalitica.historico_balanza
WHERE cuenta = ANY(:c) AND periodo >= :desde
""")
# Todas las órdenes, sin el filtro del universo: así las ve el servicio.
_SQL_MO = text("""
SELECT nic, fecha_cierre, tipo_os, descripcion_de_tipo_os, tipo_suspension_solicitada, tipo_brigada,
       territorio, municipio
FROM dbanalitica.historico_mo
WHERE nic = ANY(:c) AND fecha_cierre IS NOT NULL AND fecha_cierre >= :desde
""")
# tabla -> (consulta, llave, tipo de la llave, columna de fecha y cómo se le pasa el "desde")
_TABLAS = {
    'balanza_propension': (_SQL_BALANZA, 'cuenta', 'int64', 'periodo', lambda f: f.year * 100 + f.month),
    'mo_propension': (_SQL_MO, 'nic', str, 'fecha_cierre', lambda f: f.date()),
}


def _bajar(engine, consulta, valores: list, desde, cache: str) -> pd.DataFrame:
    partes = []
    with engine.connect() as c:
        # De a 20.000: la balanza tiene 20 millones de filas y una sola consulta no termina.
        for i in range(0, len(valores), 20000):
            partes.append(pd.read_sql(consulta, c, params={'c': valores[i:i + 20000], 'desde': desde}))
            print(f'  {cache}: {min(i + 20000, len(valores)):,} de {len(valores):,}', flush=True)
    return pd.concat(partes, ignore_index=True)


def datos_servicio(engine, nics: list[str], dir_datos: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Balanza y órdenes de esos NICs, con caché en disco.

    Bajar la balanza entera tarda unos 40 minutos, así que solo se hace la primera vez. Después,
    una vez al día, se reemplazan los últimos `MESES_REFRESCO` meses de todos los NICs (los nuevos
    entran ahí). Para forzar la descarga completa, borra los dos `.parquet`.
    """
    hoy = pd.Timestamp.today().normalize()
    refresco = hoy.replace(day=1) - relativedelta(months=MESES_REFRESCO)
    salida = []
    for nombre, (consulta, llave, tipo, col_fecha, a_param) in _TABLAS.items():
        cache = f'{dir_datos}/{nombre}.parquet'
        pedidos = set(pd.Series(nics).astype(tipo))
        convertir = (lambda xs: [int(x) for x in xs]) if tipo == 'int64' else list
        if not os.path.exists(cache):
            d = _bajar(engine, consulta, convertir(pedidos), a_param(INICIO), nombre)
            d.to_parquet(cache, index=False)
        else:
            d = pd.read_parquet(cache)
            if pd.Timestamp(os.path.getmtime(cache), unit='s').normalize() < hoy:
                todos = pedidos | set(d[llave].astype(tipo))
                nuevo = _bajar(engine, consulta, convertir(todos), a_param(refresco), nombre)
                fecha = d[col_fecha] if col_fecha == 'periodo' else pd.to_datetime(d[col_fecha])
                limite = a_param(refresco) if col_fecha == 'periodo' else refresco
                d = pd.concat([d[fecha < limite], nuevo], ignore_index=True)
                d.to_parquet(cache, index=False)
            elif faltan := pedidos - set(d[llave].astype(tipo)):
                # Ya se refrescó hoy: solo faltan NICs que no estaban, y esos se bajan completos.
                d = pd.concat([d, _bajar(engine, consulta, convertir(faltan), a_param(INICIO), nombre)],
                              ignore_index=True)
                d.to_parquet(cache, index=False)
        for c in d.columns:
            if c.startswith('fecha'):
                d[c] = pd.to_datetime(d[c])
            elif d[c].dtype == object or pd.api.types.is_string_dtype(d[c]):
                # Pocos valores repetidos millones de veces: como categoría ocupan una fracción, y
                # la matriz diaria tiene que caber junto al histórico en equipos de 16 GB.
                if c != llave:
                    d[c] = d[c].astype('category')
        salida.append(d)
    return salida[0], salida[1]


def propension_hoy(balanza: pd.DataFrame, mo: pd.DataFrame, corte: pd.Timestamp, nics: list[str],
                   muestra_servicio: int = 300) -> pd.DataFrame:
    """La propensión de todos los NICs de la matriz al `corte`, y la prueba de que es la del servicio.

    Consultar el servicio para los ~180.000 NICs tarda más de 2 horas (9 s por cada 200 cuentas) y
    carga producción; por eso se corren sus modelos aquí, y una muestra se compara contra el
    servicio real. Si no coinciden, el caché de la balanza está viejo o el servicio cambió.
    """
    cuentas = pd.Series(nics).astype('int64').unique().tolist()
    # De a 20.000 cuentas: calcular_variables sobre todas juntas no cabe en memoria.
    p = pd.concat([propension_al_corte(balanza, mo, corte, cuentas[i:i + 20000])
                   for i in range(0, len(cuentas), 20000)])
    muestra = p.sample(min(muestra_servicio, len(p)), random_state=0).index.tolist()
    real = consultar_servicio(muestra)
    dif = (real - p.loc[real.index]).abs().max(axis=1)
    iguales = (dif < 0.01).mean()
    print(f'propensión: {len(p):,} NICs | coincide con el servicio en producción en {iguales:.1%} '
          f'de {len(real)} consultados')
    if iguales < 0.95:
        print('OJO: no coincide con el servicio. Borra datos/balanza_propension.parquet y '
              'datos/mo_propension.parquet para refrescarlos; si sigue igual, el servicio cambió.')
    return p


def propension_historica(balanza: pd.DataFrame, mo: pd.DataFrame, suspensiones: pd.DataFrame) -> pd.DataFrame:
    """Para cada (nic, PERIODO) de `suspensiones`: lo que el servicio decía el último día del mes anterior.

    Es el mismo corte que usa la validación de octubre (datos hasta el 30 de septiembre) y el mismo
    que vería la matriz si se corre el primer día del mes.
    """
    partes = []
    for per, g in suspensiones.groupby('PERIODO'):
        corte = pd.Period(per, 'M').start_time - pd.Timedelta(days=1)
        p = propension_al_corte(balanza, mo, corte, g['nic'].astype('int64').unique().tolist())
        partes.append(p.rename_axis('nic').reset_index().assign(PERIODO=per))
    r = pd.concat(partes, ignore_index=True)
    r['nic'] = r['nic'].astype(str)
    return r


def consultar_servicio(cuentas: list[int], lote: int = 200) -> pd.DataFrame:
    """Propensión de hoy, del servicio en producción (índice = cuenta). Las que no tienen datos quedan fuera."""
    filas = []
    for i in range(0, len(cuentas), lote):
        cuerpo = json.dumps({'cuentas': [int(c) for c in cuentas[i:i + lote]]}).encode()
        pet = urllib.request.Request(URL_SERVICIO, data=cuerpo, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(pet, timeout=120) as r:
            for x in json.load(r)['resultados']:
                if x['probabilidad_pago_con_intervencion'] is not None:
                    filas.append((x['cuenta'], x['probabilidad_pago_sin_intervencion'],
                                  x['probabilidad_pago_con_intervencion']))
    return pd.DataFrame(filas, columns=['cuenta'] + COLS).set_index('cuenta')
