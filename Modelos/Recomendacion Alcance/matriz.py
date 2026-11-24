"""La matriz de recomendación de hoy: la usan el notebook 3 (CSV y Excel) y `correr_matriz.py` (tabla).

Vive aquí y no en el notebook para que el archivo que mira el analista y la tabla que consulta el
servicio salgan del mismo cálculo; si fueran dos copias, tarde o temprano darían números distintos.
"""
import gc
import json

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

from comun import CONSULTA_HISTORICO, preparar_historia, puntuar_canastas
from propension import datos_servicio, propension_hoy

# Pocas órdenes recientes y el modelo las sobreestima: se muestran pero no compiten (regla acordada).
NO_RECOMENDABLES = ['Pesada Disponible', '(D) Brigada Pesada', 'Brigada Canasta']
# Siempre responde: el negocio necesita un pronóstico para cada orden (la franja «Incierta» se descartó).
NIVELES = [0, 50, 101]
ETIQUETAS = ['No efectiva (menos de 50 %)', 'Efectiva (50 % o más)']


def cargar_modelo(dir_modelos: str) -> tuple[CatBoostClassifier, dict]:
    with open(f'{dir_modelos}/Metadata_Modelo_Alcance.json', encoding='utf-8') as f:
        meta = json.load(f)
    modelo = CatBoostClassifier()
    modelo.load_model(f'{dir_modelos}/Modelo_Alcance.cbm')
    return modelo, meta


def excluidos(engine, h: pd.DataFrame) -> pd.Series:
    """NIC de la vista -> motivo por el que no se recomienda (vacío = se recomienda).

    La reconexión que cuenta es la **efectiva**: la vista da por reconectado a quien solo tuvo una
    reconexión fallida, y con eso dejaba fuera de esta exclusión a clientes que pagaron y siguen
    suspendidos.
    """
    vista = pd.read_sql(
        'SELECT nic, fecha_ultima_suspension, fecha_pago_post_suspension FROM dbanalitica.recomendacion_aire_scr',
        engine)
    vista['nic'] = vista['nic'].astype(str)
    vista = vista.set_index('nic')
    reconexion = (h[(h['tipo_os'] == 'TO502') & (h['estado_norm'] == 'EFECTIVA')]
                    .groupby('nic')['fecha_cierre'].max())
    vista['reconexion_efectiva'] = reconexion
    pagaron_antes = (vista['fecha_pago_post_suspension'].notna()
                     & (vista['reconexion_efectiva'].isna()
                        | (vista['reconexion_efectiva'] < pd.to_datetime(vista['fecha_ultima_suspension']))))
    return pd.Series(np.where(pagaron_antes, 'Pagó después de la última suspensión y no tiene reconexión efectiva', ''),
                     index=vista.index)


def armar_matriz(base: pd.DataFrame, P: pd.DataFrame, nics: pd.Index) -> pd.DataFrame:
    """Una fila por NIC: recomendación entre las canastas elegibles, % de cada una y la propensión."""
    elegibles = [c for c in P.columns if c not in NO_RECOMENDABLES]
    Pn = P.loc[nics]
    orden = np.argsort(-Pn[elegibles].values, axis=1)
    top1 = np.array(elegibles)[orden[:, 0]]
    top2 = np.array(elegibles)[orden[:, 1]]
    p1 = np.take_along_axis(Pn[elegibles].values, orden[:, :1], axis=1)[:, 0]
    p2 = np.take_along_axis(Pn[elegibles].values, orden[:, 1:2], axis=1)[:, 0]
    info = base.loc[nics]
    pago_con = (info['PROB_PAGO_CON_INTERVENCION'].values * 100).round(1)
    # Un solo número por NIC para priorizar: el de la Brigada Pesada, la que más se manda, para que
    # el % de un NIC se pueda comparar con el de otro.
    pesada = (Pn['Brigada Pesada'].values * 100).round(1)

    m = pd.DataFrame({
        'nic': nics,
        'territorio': info['territorio'].values,
        'zona': info['zona'].values,
        'municipio': info['municipio'].values,
        'barrio': info['localidad_barrio'].values,
        'probabilidad_efectividad': pesada,
        'pronostico': pd.cut(pesada, NIVELES, right=False, labels=ETIQUETAS).astype(str),
        'alcance_recomendado': top1,
        'pct_recomendado': (p1 * 100).round(1),
        'segundo_alcance': top2,
        'pct_segundo': (p2 * 100).round(1),
        'ventaja_pp': ((p1 - p2) * 100).round(1),
        'ventaja_sobre_pesada_pp': ((p1 - Pn['Brigada Pesada'].values) * 100).round(1),
    })
    for c in P.columns:
        sufijo = ' (no recomendable)' if c in NO_RECOMENDABLES else ''
        m[f'pct {c}{sufijo}'] = (Pn[c].values * 100).round(1)
    m['prob_pago_con_intervencion'] = pago_con
    # En SCR el servicio no tiene modelo de "no pago": lo devuelve como 1 - con intervención. Se
    # muestra igual que el servicio, pero no entra al modelo (repetiría la otra variable).
    m['prob_no_pago_con_intervencion'] = (100 - pago_con).round(1)
    m['prob_pago_sin_intervencion'] = (info['PROB_PAGO_SIN_INTERVENCION'].values * 100).round(1)
    m['sin_propension'] = info['SIN_PROPENSION'].values
    m['deuda_ultima_suspension'] = info['deuda_act'].round(0).values
    m['canasta_ultima_orden'] = info['CANASTA_ANTERIOR'].values
    m['resultado_ultima_orden'] = info['RESULTADO_ANTERIOR'].values
    m['dias_sin_orden'] = info['DIAS_DESDE_ANTERIOR'].values
    m['ordenes_previas'] = info['ORDENES_PREVIAS'].values
    return m.sort_values('pct_recomendado', ascending=False).reset_index(drop=True)


def calcular_hoy(engine, dir_datos: str, dir_modelos: str) -> tuple[pd.DataFrame, pd.Series, pd.Timestamp]:
    """Todo el cálculo con el histórico fresco de la base: (matriz, excluidos, corte).

    `excluidos` trae también los NICs de la vista sin historial, con su motivo, para que quien
    consulte sepa por qué no tienen recomendación en vez de recibir un vacío.
    """
    modelo, meta = cargar_modelo(dir_modelos)
    h = preparar_historia(pd.read_sql(CONSULTA_HISTORICO, engine))
    # Corte = último cierre que conoce el modelo, no hoy: los "días sin orden" se miden contra eso.
    corte = h['fecha_cierre'].max()
    nics_h = h['nic'].unique().tolist()
    balanza, mo = datos_servicio(engine, nics_h, dir_datos)
    propension = propension_hoy(balanza, mo, corte, nics_h)
    del balanza, mo  # ~700 MB que puntuar_canastas ya no necesita
    gc.collect()
    base, P = puntuar_canastas(h, corte, modelo, meta['features'], meta['categoricas'], meta['canastas'], propension)

    motivo = excluidos(engine, h)
    sin_hist = motivo.index.difference(P.index)
    motivo.loc[sin_hist] = 'Sin órdenes gestionadas en el histórico: el modelo no tiene con qué calcular'
    nics = motivo.index[motivo == '']
    return armar_matriz(base, P, nics), motivo[motivo != ''], corte
