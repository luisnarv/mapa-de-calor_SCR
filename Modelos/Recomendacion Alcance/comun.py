"""Preparación del histórico compartida por el notebook 1 (entrenamiento) y el 3 (matriz).

Vive en un módulo y no copiada en cada notebook porque, si las dos copias se desalinean, el modelo
predice con variables construidas distinto de como aprendió. Eso no lanza ningún error: solo da
probabilidades malas con toda la seguridad de unas buenas.
"""

import unicodedata

import numpy as np
import pandas as pd

# Homologación acordada tras la reunión. Lista blanca a propósito: un alcance nuevo en la base queda
# fuera (CANASTA nula) en vez de colarse con un nombre que el modelo nunca vio.
CANASTAS: dict[str, str] = {
    'BRIGADA PESADA':        'Brigada Pesada',
    '(D) BRIGADA PESADA':    '(D) Brigada Pesada',   # nocturna: aparte, no se fusiona con la Pesada
    'BRIGADA LIVIANA':       'Brigada Liviana',
    'GESTOR INTEGRAL MULTI': 'Gestor Integral Multi',
    'BRIGADA PESADA MT-AT':  'Pesada MT-AT',
    'BRIGADA PESADA/ MT AT': 'Pesada MT-AT',
    'BRIGADA MINICANASTA':   'Brigada Minicanasta',
    'BRIGADA CANASTA':       'Brigada Canasta',
    'PESADA DISPONIBLE':     'Pesada Disponible',
    'SCR PESADA DISPONIBI':  'Pesada Disponible',
}

# El universo de la vista `recomendacion_aire_scr`: el mismo para entrenar (notebook 1) y para la
# matriz diaria (`matriz.calcular_hoy`).
CONSULTA_HISTORICO = """
SELECT id, orden, nic, fecha_cierre, hora_fin, tipo_os, brigada_homologada,
       territorio, zona, municipio, localidad_barrio, tarifa, deuda_act, cant_factura_act,
       av_resultado, estado_norm, subaccion_homologada, id_transformador, id_circuito, id_tecnico
FROM dbanalitica.historico_mo
WHERE av_resultado IS NOT NULL
  AND av_resultado NOT IN ('ON210', 'ON302')
  AND estado_osf IS DISTINCT FROM 'Anulada'
"""

TIPOS_SUSPENSION = ['TO501', 'TO504']
COLS_TEXTO = ['brigada_homologada', 'territorio', 'zona', 'municipio', 'localidad_barrio',
              'tarifa', 'estado_norm', 'subaccion_homologada', 'av_resultado', 'tipo_os',
              'id_transformador', 'id_circuito', 'id_tecnico']
COLS_UBICACION = ['territorio', 'zona', 'municipio', 'localidad_barrio', 'id_transformador', 'id_circuito']

# Efectividad de las suspensiones por grupo, suavizada con K_ENTORNO. Las usan igual el notebook 1
# (en orden calendario, sin la fila actual) y `puntuar_canastas` (todo el histórico al corte).
ENTORNOS: list[tuple[str | list[str], str]] = [
    ('localidad_barrio', 'BARRIO'), ('municipio', 'MUNICIPIO'),
    ('id_transformador', 'TRAFO'), ('id_circuito', 'CIRCUITO'),
]
ENTORNOS_CANASTA: list[tuple[list[str], str]] = [
    (['localidad_barrio', 'CANASTA'], 'BARRIO_CANASTA'), (['municipio', 'CANASTA'], 'MUNICIPIO_CANASTA'),
    (['id_transformador', 'CANASTA'], 'TRAFO_CANASTA'),
]
# El técnico fue la variable que más subió el acierto (octubre 2026: 69,5 % -> 70,3 %), pero solo se
# conoce cuando la orden ya está asignada. Sin él, `puntuar_canastas` usa el técnico típico de la canasta.
ENTORNOS_TECNICO: list[tuple[str | list[str], str]] = [
    ('id_tecnico', 'TECNICO'), (['id_tecnico', 'municipio'], 'TECNICO_MUNICIPIO'),
]
# Qué tan seguido les sale a los vecinos del transformador cada causa de fallo.
VECINOS_TRAFO: dict[str, str] = {
    '_RESISTENCIA': 'TASA_AGRESIVO_TRAFO', '_IMPOSIB': 'TASA_IMPOSIB_TRAFO', '_PAGO_CLIENTE': 'TASA_PAGO_VISITA_TRAFO',
}


def norm_txt(s: object) -> object:
    """Quita tildes, pasa a MAYÚSCULAS y colapsa espacios. NaN se mantiene como NaN.

    Sin esto la misma categoría escrita con y sin tilde (pasa según el archivo de corte del que vino
    la fila) se vuelve dos categorías, y el modelo aprende el archivo de origen y no el negocio.
    """
    if pd.isna(s):
        return s
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(s.upper().split())


def acumulado_previo(frame: pd.DataFrame, key: str | list[str], col: str) -> pd.Series:
    """Suma acumulada de `col` dentro de cada `key`, EXCLUYENDO la fila actual."""
    return frame.groupby(key, sort=False)[col].cumsum() - frame[col]


def preparar_historia(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza, homologa la canasta, ordena por NIC y fecha y marca el resultado de cada orden.

    Las columnas con `_` salen del resultado de la propia orden (post-visita): solo sirven para
    construir acumulados del pasado, nunca como variable de la misma fila.
    """
    df = df.copy()
    for c in COLS_TEXTO:
        df[c] = df[c].map(norm_txt)
    for c in COLS_UBICACION + ['id_tecnico']:
        # CatBoost no acepta nulos en categóricas, y un grupo nulo se perdería en los groupby.
        df[c] = df[c].fillna('SIN DATO')

    df['nic'] = df['nic'].astype(str)
    df['fecha_cierre'] = pd.to_datetime(df['fecha_cierre'])
    # hora_fin es posterior a la visita: solo desempata dos órdenes del mismo día, nunca es variable.
    df['_HORA_FIN'] = pd.to_timedelta(df['hora_fin'].astype(str), errors='coerce').dt.total_seconds()
    df['CANASTA'] = df['brigada_homologada'].map(CANASTAS)

    # Este orden es obligatorio: todo el historial depende de que cada NIC esté en secuencia.
    df = df.sort_values(['nic', 'fecha_cierre', '_HORA_FIN', 'id'], na_position='first').reset_index(drop=True)

    df['_EFECTIVA'] = (df['estado_norm'] == 'EFECTIVA').astype(int)
    df['_FALLIDA'] = (df['estado_norm'] == 'FALLIDA').astype(int)
    # Perdida y fallida van separadas: la perdida no se cobra, la fallida sí.
    df['_PERDIDA'] = (df['estado_norm'] == 'PERDIDA').astype(int)
    # Las dos marcas de imposibilidad: la vista base solo cuenta ON208 en su historial y se le escapan
    # las que el técnico registró por subacción.
    df['_IMPOSIB'] = ((df['av_resultado'] == 'ON208')
                      | df['subaccion_homologada'].str.contains('IMPOSIBILIDAD TECNICA', na=False)).astype(int)
    df['_PAGO_CLIENTE'] = (df['av_resultado'] == 'ON201').astype(int)
    df['_RESISTENCIA'] = (df['av_resultado'] == 'ON202').astype(int)

    df['ES_SUSPENSION'] = df['tipo_os'].isin(TIPOS_SUSPENSION).astype(int)
    df['_SUSP_EFECTIVA'] = df['ES_SUSPENSION'] * df['_EFECTIVA']
    return df


def variables_contexto(frame: pd.DataFrame) -> pd.DataFrame:
    """Segmento y estrato de la tarifa, y las transformaciones de la deuda."""
    frame = frame.copy()
    tar = frame['tarifa'].fillna('SIN DATO').str.split('|', n=1, expand=True)
    frame['SEGMENTO'] = tar[0].str.strip()
    frame['ESTRATO'] = pd.to_numeric(tar[1].str.extract(r'(\d)')[0], errors='coerce')
    frame['DEUDA_POR_FACTURA'] = np.where(frame['cant_factura_act'].fillna(0) > 0,
                                          frame['deuda_act'] / frame['cant_factura_act'], np.nan)
    # log1p: la deuda va de miles a cientos de millones y unos pocos clientes dominarían la variable.
    frame['LOG_DEUDA'] = np.log1p(frame['deuda_act'].clip(lower=0))
    return frame


# Predicción: variables de la PRÓXIMA orden de cada NIC (notebook 3 y evaluaciones hacia atrás).

ACUMULADOS: dict[str, str] = {
    '_EFECTIVA': 'EFECTIVAS_PREVIAS', '_FALLIDA': 'FALLIDAS_PREVIAS', '_PERDIDA': 'PERDIDAS_PREVIAS',
    '_IMPOSIB': 'IMPOSIB_PREVIAS', '_PAGO_CLIENTE': 'PAGOS_CLIENTE_PREVIOS',
    '_RESISTENCIA': 'RESISTENCIAS_PREVIAS', 'ES_SUSPENSION': 'SUSP_PREVIAS',
    '_SUSP_EFECTIVA': 'SUSP_EFECTIVAS_PREVIAS',
}
SIN_HISTORIA = ['RESULTADO_ANTERIOR', 'TIPO_OS_ANTERIOR', 'CANASTA_ANTERIOR']
K_ENTORNO = 20   # mismo suavizado del notebook 1


def variables_nic(h: pd.DataFrame, corte: pd.Timestamp | pd.Series) -> pd.DataFrame:
    """Variables de la próxima orden de cada NIC que no dependen de la canasta.

    Equivale a la fila nueva que el notebook 1 habría construido: los acumulados "previos" son el
    historial completo de `h` y la "orden anterior" es la última. `corte` puede ser una Serie
    indexada por NIC (la usa el chequeo del notebook 3).

    La deuda de la próxima suspensión todavía no existe, así que se estima con la de la **última
    suspensión** del NIC, no con la de su última orden: esa suele ser una reconexión hecha justo
    después de pagar, con la deuda casi en cero. Con la de la última orden, el modelo predecía 35 %
    para las suspensiones de octubre de 2026 cuando salieron efectivas el 51 %; con esta, 47 %.
    """
    h = h.copy()
    g = h.groupby('nic', sort=False)
    ult = g.tail(1).set_index('nic')
    ult_susp = h[h['ES_SUSPENSION'] == 1].groupby('nic', sort=False).tail(1).set_index('nic')

    v = g[list(ACUMULADOS)].sum().rename(columns=ACUMULADOS)
    v['ORDENES_PREVIAS'] = g.size()
    v['TASA_EF_HIST'] = (v['EFECTIVAS_PREVIAS'] / v['ORDENES_PREVIAS']).where(v['ORDENES_PREVIAS'] > 0)
    v['TASA_EF_SUSP_HIST'] = (v['SUSP_EFECTIVAS_PREVIAS'] / v['SUSP_PREVIAS']).where(v['SUSP_PREVIAS'] > 0)

    v['RESULTADO_ANTERIOR'] = ult['av_resultado']
    v['TIPO_OS_ANTERIOR'] = ult['tipo_os']
    v['CANASTA_ANTERIOR'] = ult['CANASTA']
    v['DIAS_DESDE_ANTERIOR'] = (corte - ult['fecha_cierre']).dt.days
    v['SIN_ORDEN_90D'] = (v['DIAS_DESDE_ANTERIOR'] > 90).astype(float)

    for c in COLS_UBICACION + ['tarifa']:
        v[c] = ult[c]
    # Sin suspensión previa (o con su deuda vacía), la última orden es lo único que hay.
    deuda_susp = ult_susp['deuda_act'].reindex(v.index)
    con_susp = deuda_susp.notna()
    v['deuda_act'] = deuda_susp.where(con_susp, ult['deuda_act'])
    v['cant_factura_act'] = ult_susp['cant_factura_act'].reindex(v.index).where(con_susp, ult['cant_factura_act'])
    # Igual que en el entrenamiento: deuda de esta orden menos la de la orden anterior.
    v['DELTA_DEUDA'] = v['deuda_act'] - ult['deuda_act']
    # TO504 si el NIC ya tuvo alguna suspensión MI/MS; si no, la suspensión normal.
    v['tipo_os'] = (h['tipo_os'] == 'TO504').groupby(h['nic'], sort=False).any().map({True: 'TO504', False: 'TO501'})

    v = variables_contexto(v)
    for c in SIN_HISTORIA:
        v[c] = v[c].fillna('SIN HISTORIA')
    v['SEGMENTO'] = v['SEGMENTO'].fillna('SIN DATO')
    return v


def variables_canasta_nic(h: pd.DataFrame) -> pd.DataFrame:
    """Historial de cada NIC con cada canasta: (nic, CANASTA) -> órdenes y efectivas."""
    return (h[h['CANASTA'].notna()].groupby(['nic', 'CANASTA'])['_EFECTIVA']
              .agg(ORDENES_PREVIAS_CANASTA='size', EF_PREVIAS_CANASTA='sum'))


def suspensiones_con_resultado(h: pd.DataFrame) -> pd.DataFrame:
    """Las suspensiones que entran al entrenamiento y a las tasas de entorno."""
    return h[(h['ES_SUSPENSION'] == 1) & h['CANASTA'].notna()
             & h['estado_norm'].isin(['EFECTIVA', 'FALLIDA', 'PERDIDA'])]


def _rellenar_sin_informacion(x: pd.DataFrame, prior: float) -> pd.DataFrame:
    """Un grupo que nunca se dio cae en la tasa global con 0 órdenes: el modelo lo ve como "sin
    información", igual que vio al entrenar al primer caso de cada barrio o transformador."""
    for _, nombre in ENTORNOS + ENTORNOS_CANASTA + ENTORNOS_TECNICO:
        for col, valor in [(f'TASA_EF_{nombre}', prior), (f'ORDENES_PREV_{nombre}', 0)]:
            if col in x:
                x[col] = x[col].fillna(valor)
    return x


def puntuar_canastas(h: pd.DataFrame, corte: pd.Timestamp, modelo, features: list[str],
                     categoricas: list[str], canastas: list[str], propension: pd.DataFrame,
                     tecnicos: pd.Series | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Probabilidad de que la próxima suspensión de cada NIC salga efectiva, con cada canasta.

    `h` es el histórico preparado hasta `corte`: todo lo que haya en `h` cuenta como pasado. Devuelve
    las variables comunes de cada NIC y la tabla NIC × canasta de probabilidades.

    `tecnicos` (NIC -> id_tecnico) es para cuando la orden ya está asignada. Sin él, o para un NIC que
    no esté, se usa el **técnico típico** de cada canasta: la efectividad de la canasta en general y
    en el municipio, y la mediana de órdenes de sus técnicos.

    `propension` (índice = cuenta) es la del servicio de propensión de pago al `corte`. Un NIC que
    el servicio no conoce recibe la mediana y queda marcado en `SIN_PROPENSION`: sin relleno,
    CatBoost lo trataría como propensión mínima y le subiría el %, que es justo la contradicción
    que el negocio no acepta.
    """
    from catboost import Pool

    v = variables_nic(h, corte)
    pc = variables_canasta_nic(h)

    s = suspensiones_con_resultado(h)
    prior = s['_EFECTIVA'].mean()

    def entorno(key: str | list[str], nombre: str) -> pd.DataFrame:
        t = s.groupby(key)['_EFECTIVA'].agg(n='size', ef='sum')
        return pd.DataFrame({f'ORDENES_PREV_{nombre}': t['n'],
                             f'TASA_EF_{nombre}': (t['ef'] + K_ENTORNO * prior) / (t['n'] + K_ENTORNO)})

    base = v
    for key, nombre in ENTORNOS:
        base = base.join(entorno(key, nombre), on=key)
    vecinos = s.groupby('id_transformador')[list(VECINOS_TRAFO)].agg(['sum', 'size'])
    for marca, col in VECINOS_TRAFO.items():
        tasa = (vecinos[(marca, 'sum')] + K_ENTORNO * s[marca].mean()) / (vecinos[(marca, 'size')] + K_ENTORNO)
        base[col] = base['id_transformador'].map(tasa).fillna(s[marca].mean())
    base = _rellenar_sin_informacion(base, prior)
    base = base.join(propension.set_axis(propension.index.astype(str)))
    base['SIN_PROPENSION'] = base[propension.columns[0]].isna()
    base[propension.columns] = base[propension.columns].fillna(propension.median())
    e_canasta ={nombre: entorno(key, nombre) for key, nombre in ENTORNOS_CANASTA}

    # Técnico conocido: su efectividad, la misma para todas las canastas.
    e_tec = entorno('id_tecnico', 'TECNICO')
    e_tec_mun = entorno(['id_tecnico', 'municipio'], 'TECNICO_MUNICIPIO')
    tec = tecnicos.reindex(base.index) if tecnicos is not None else pd.Series(np.nan, index=base.index)
    con_tec = tec.notna() & tec.isin(e_tec.index)
    tec_mun = list(zip(tec.fillna(''), base['municipio']))
    # Técnico típico: mediana de órdenes de los técnicos que trabajan en cada canasta.
    n_tipico = s.groupby(['CANASTA', 'id_tecnico']).size().groupby('CANASTA').median()
    n_tipico_mun = s.groupby(['CANASTA', 'id_tecnico', 'municipio']).size().groupby('CANASTA').median()
    e_can = entorno('CANASTA', 'CANASTA_SOLA')

    cat_idx = [features.index(c) for c in categoricas]
    probs = {}
    # Una canasta a la vez: las 8 juntas son más de un millón de filas y el equipo se queda sin memoria.
    for canasta in canastas:
        x = base.copy()
        x['CANASTA'] = canasta
        claves = list(zip(x.index, [canasta] * len(x)))
        x['ORDENES_PREVIAS_CANASTA'] = pc['ORDENES_PREVIAS_CANASTA'].reindex(claves).fillna(0).values
        x['EF_PREVIAS_CANASTA'] = pc['EF_PREVIAS_CANASTA'].reindex(claves).fillna(0).values
        x['TASA_EF_CANASTA_NIC'] = (x['EF_PREVIAS_CANASTA'] / x['ORDENES_PREVIAS_CANASTA']).where(
            x['ORDENES_PREVIAS_CANASTA'] > 0)

        for key, nombre in ENTORNOS_CANASTA:
            x = x.join(e_canasta[nombre].xs(canasta, level='CANASTA'), on=key[0])
        # Antes del técnico: su respaldo en el municipio es TASA_EF_MUNICIPIO_CANASTA, ya rellena.
        x = _rellenar_sin_informacion(x, prior)

        tasa_can = e_can['TASA_EF_CANASTA_SOLA'].get(canasta, prior)
        x['TASA_EF_TECNICO'] = np.where(con_tec, tec.map(e_tec['TASA_EF_TECNICO']), tasa_can)
        x['ORDENES_PREV_TECNICO'] = np.where(con_tec, tec.map(e_tec['ORDENES_PREV_TECNICO']),
                                             n_tipico.get(canasta, 0))
        conocido_mun = e_tec_mun.reindex(tec_mun)
        x['TASA_EF_TECNICO_MUNICIPIO'] = np.where(con_tec, conocido_mun['TASA_EF_TECNICO_MUNICIPIO'].values,
                                                  x['TASA_EF_MUNICIPIO_CANASTA'])
        x['ORDENES_PREV_TECNICO_MUNICIPIO'] = np.where(con_tec, conocido_mun['ORDENES_PREV_TECNICO_MUNICIPIO'].values,
                                                       n_tipico_mun.get(canasta, 0))
        x = _rellenar_sin_informacion(x, prior)

        X = x[features].copy()
        for c in categoricas:
            X[c] = X[c].astype(str)
        probs[canasta] = modelo.predict_proba(Pool(X, cat_features=cat_idx))[:, 1]

    return base, pd.DataFrame(probs, index=base.index)
