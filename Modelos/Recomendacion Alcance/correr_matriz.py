"""Calcula la matriz de hoy y la guarda en dbanalitica.matriz_alcance. Lo corre el cron del servidor.

    cd "Modelos/Recomendacion Alcance" && .venv/bin/python correr_matriz.py

Usa el modelo ya entrenado (`modelos/`): no reentrena. El reentrenamiento es mensual y a mano,
con los notebooks 1 y 2, porque es cuando se revisa que el acierto no se haya caído.

Necesita en `.env`: SCR_DATABASE_URL y, si el servicio de propensión no está en la carpeta de al
lado de siempre, RUTA_SERVICIO_PROPENSION.
"""
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB

AQUI = Path(__file__).resolve().parent
os.chdir(AQUI)  # las rutas de datos/ y modelos/ son relativas a esta carpeta
sys.path.insert(0, str(AQUI))
from matriz import calcular_hoy  # noqa: E402

TABLA = 'matriz_alcance'
ESQUEMA = 'dbanalitica'


def filas_para_tabla(matriz: pd.DataFrame, motivos: pd.Series, corte: pd.Timestamp) -> pd.DataFrame:
    """La matriz con los nombres de la tabla, más una fila por NIC excluido con su motivo."""
    pct = [c for c in matriz.columns if c.startswith('pct ')]
    t = matriz.drop(columns=pct).copy()
    # 'pct Brigada Canasta (no recomendable)' -> 'Brigada Canasta': la marca ya la da la regla.
    nombres = [c.removeprefix('pct ').removesuffix(' (no recomendable)') for c in pct]
    t['pct_por_alcance'] = [dict(zip(nombres, fila)) for fila in matriz[pct].itertuples(index=False)]
    fuera = pd.DataFrame({'nic': motivos.index, 'motivo_exclusion': motivos.values})
    t = pd.concat([t, fuera], ignore_index=True)
    t['calculado_al'] = corte.date()
    return t


def main() -> None:
    load_dotenv('.env')
    url = os.getenv('SCR_DATABASE_URL')
    if not url:
        raise RuntimeError('Falta SCR_DATABASE_URL en Modelos/Recomendacion Alcance/.env: '
                           'es la misma conexión del modelo de riesgo social.')
    engine = create_engine(url)
    t0 = time.time()
    matriz, motivos, corte = calcular_hoy(engine, 'datos', 'modelos')
    t = filas_para_tabla(matriz, motivos, corte)

    # Una sola transacción: si algo falla a mitad, queda la matriz de ayer entera.
    with engine.begin() as c:
        c.execute(text(f'DELETE FROM {ESQUEMA}.{TABLA}'))
        t.to_sql(TABLA, c, schema=ESQUEMA, if_exists='append', index=False,
                 method='multi', chunksize=2000, dtype={'pct_por_alcance': JSONB})
    print(json.dumps({'calculado_al': str(corte.date()), 'recomendados': len(matriz),
                      'excluidos': len(motivos), 'segundos': round(time.time() - t0)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
