# Matriz de recomendación de alcance (SCR)

Para cada NIC con gestiones de SCR, el % de que la próxima orden salga **efectiva** con cada
alcance (Brigada Pesada, Liviana, …) y cuál conviene. Es un CatBoost entrenado sobre
`dbanalitica.historico_mo`.

## Archivos

| Archivo | Qué hace |
|---|---|
| `1_CreacionVariables.ipynb` | Arma el universo y las variables con corte en el tiempo (nada del futuro de cada orden). |
| `2_Modelo_Alcance.ipynb` | Entrena y guarda `modelos/Modelo_Alcance.cbm` y su metadata. |
| `3_Matriz_Recomendacion.ipynb` | Genera la matriz de hoy y el Excel en `salidas/`. |
| `comun.py` | Consulta del histórico y `puntuar_canastas`, compartidos por notebooks y corrida diaria. |
| `propension.py` | Calcula la propensión de pago como lo hace el servicio, pero localmente (ver abajo). |
| `matriz.py` | Exclusiones y armado de la matriz; lo usan el notebook 3 y `correr_matriz.py`. |
| `correr_matriz.py` | La corrida diaria: recalcula todos los NICs y reemplaza `dbanalitica.matriz_alcance`. |

## La propensión de pago es obligatoria y no puede contradecir a la matriz

El modelo usa `PROB_PAGO_SIN_INTERVENCION` y `PROB_PAGO_CON_INTERVENCION` del *Modelo Propensión de
Pago Service* con **restricción monótona negativa**: a más propensión de pago, menor % de
efectividad. El notebook 2 verifica que la regla se cumpla antes de guardar el modelo.

Aun así puede haber NICs con propensión alta y % alto, porque pesan otras variables. Ponerles un
tope bajaba el acierto a 65,1 %, y se decidió dejarlo así.

No se llama al servicio para todos los NICs: acepta 200 cuentas por llamada y tarda unos 9 s, lo que
para 185 mil NICs serían más de 2 horas. `propension.py` importa el código del servicio (su carpeta
va en `RUTA_SERVICIO_PROPENSION`) y lo corre en local. En cada corrida compara una muestra de 300
NICs contra el servicio en vivo y avisa si coinciden menos del 95 %.

## Acierto

Backtest de octubre de 2026: **70,4 %** con el técnico real y 69,9 % con el técnico típico; AUC de
prueba 0,7638. Falla sobre todo con clientes que pagan en la visita o son agresivos **por primera
vez**: con 4 o más antecedentes acierta cerca del 80 %.

## Corrida diaria

```bash
cd "Modelos/Recomendacion Alcance" && .venv/bin/python correr_matriz.py
```

- Necesita `.env` con `SCR_DATABASE_URL` y, si el servicio de propensión no está en la carpeta de
  al lado, `RUTA_SERVICIO_PROPENSION`.
- Tarda unos 10 minutos. La primera vez baja la balanza completa (~40 min) a `datos/`. Después solo
  refresca los últimos 7 meses, que es la ventana del servicio de propensión más uno.
- Reemplaza la tabla en una sola transacción: si falla a mitad, queda la de ayer.
- No reentrena. El reentrenamiento es mensual y a mano, con los notebooks 1 y 2, para revisar que el
  acierto no haya caído.

**Pendiente:** el cron en el servidor Ubuntu. Allá también hacen falta la carpeta del servicio de
propensión con sus modelos `.cbm` y el `.env`.

## Dónde queda el resultado

- `dbanalitica.matriz_alcance` (`Backend/migraciones/002_matriz_alcance.sql`): una fila por NIC.
  Los excluidos (p. ej. pagó después de la última suspensión y no tiene reconexión efectiva) van con
  `motivo_exclusion` y sin cifras.
- `dbanalitica.matriz_alcance_completa` (`003_…sql`): la vista `recomendacion_aire_scr` más las
  columnas de la matriz. Expande `v.*` al crearse, así que si `recomendacion_aire_scr` gana columnas
  hay que volver a correr el `003`. Un `DROP … CASCADE` de la vista base también la borra.
- `POST /api/v1/matriz-alcance` del backend: devuelve solo `pct_recomendado` por NIC.
