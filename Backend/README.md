# Backend SCR

API en FastAPI. Capas: **endpoints** (HTTP) → **services** (negocio y proveedores
externos) → **models** (ORM), con **schemas** Pydantic como contrato de entrada/salida.

## Estructura

```
app/
  api/
    v1/
      endpoints/openai.py     # POST /chat y /chat/stream
      endpoints/ordenes.py    # POST /ordenes/cargar
      router.py               # une las rutas de la v1
    deps.py                   # sesión de BD e inyección de servicios
  core/
    config.py                 # variables de entorno (Pydantic Settings)
    database.py               # engine, SessionLocal y Base de SQLAlchemy
    taxonomy.py               # espejo de la taxonomía del ETL + normalización
    etl_sql.py                # las reglas del ETL traducidas a SQL
  prompts/
    sistema.md                # el prompt de sistema del asistente
  models/                     # tablas ORM (vacío por ahora)
  schemas/
    chat.py                   # ChatRequest, ChatResponse, Message
    metrics.py                # Efectividad, FilaCausa, FiltroMapa
    ordenes.py                # ResumenCargue
  services/
    metrics_service.py        # consultas de negocio contra Postgres
    tools.py                  # herramientas que el modelo puede invocar
    openai_service.py         # ciclo de tool calling y streaming
    carga_ordenes.py          # lee y limpia el Excel/CSV que sube el usuario
    cargue_store.py           # guarda los cargues vivos entre turnos
  main.py                     # instancia FastAPI, CORS y /health
tests/
```

### El prompt del asistente

Vive en `app/prompts/sistema.md` y se carga al arrancar. Se edita ahí, no en
`config.py`: la variable de entorno `OPENAI_SYSTEM_PROMPT` sigue pudiendo
sobrescribirlo, pero lo normal es tocar el Markdown.

Todo lo que haya en el archivo llega tal cual al modelo, así que no se escriben
dentro notas para quien lo mantiene. Si falta el archivo, la API no arranca.

A ese texto el backend le añade en cada petición tres bloques que no están en el
Markdown porque cambian: la fecha de hoy, los filtros que el usuario tiene en
pantalla y, si subió un archivo de órdenes, cuál es.

## Cómo consulta datos el asistente

El modelo **no escribe SQL ni inventa cifras**. Elige una herramienta de
`services/tools.py` (`efectividad`, `ranking`, `causas_no_efectivas`,
`buscar_barrio`, `meses_disponibles`, `filtrar_mapa`) y el backend la resuelve.

Los datos salen de `app/data/*.json`: **los mismos archivos que consume el mapa**,
generados por el ETL (`Etl/`). No hay dos cálculos que puedan discrepar, hay uno
leído dos veces. `services/payload_store.py` los carga en columnas de enteros
(~2 MB para 177k órdenes) y `services/metrics_service.py` agrega sobre ellas
replicando `page.js:470-560`.

Medido sobre los datos reales: 370 ms la carga inicial, ~110 ms por consulta.

La contrapartida es la frescura: el chat sabe lo mismo que el mapa, ni más ni
menos. Para preguntas que el payload no cubre (histórico anterior, deuda,
estrato, riesgo por NIC) hace falta ir a la base; `core/etl_sql.py` tiene esas
mismas reglas traducidas a SQL, listas para una vista materializada.

### Actualizar los datos

`app/data/` debe recibir los JSON que produce el ETL. El servidor detecta el
cambio por la fecha de los archivos y los recarga solo, sin reiniciar.

Dos métricas de efectividad, como en el tablero:

- `ef_pct` cruda (`ef / tot`), la que muestra el mapa en sus tooltips.
- `ef_adj` ajustada (`ef / (tot - no controlables)`), la que ordena los rankings.

El modelo tiene instrucción de decir siempre cuál está citando.

## Filtrado del mapa desde el chat

Una herramienta puede devolver un `FiltroMapa` con **nombres** (no índices). Va
al navegador como un evento SSE `{"accion": {...}}`; `ChatBot.js` lo pasa a
`page.js`, que traduce los nombres a los índices de `data.dim` y actualiza el
estado del tablero.

## Puesta en marcha

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- Documentación interactiva: http://127.0.0.1:8000/docs
- Salud: http://127.0.0.1:8000/health

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/api/v1/openai/chat` | Respuesta completa del modelo |
| POST | `/api/v1/openai/chat/stream` | Igual, por trozos vía SSE (`data: {"delta": …}`, cierra con `data: [DONE]`) |

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/openai/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hola"}]}'
```

## Pruebas

```bash
pytest
```

Sustituyen el servicio de OpenAI por un doble: no consumen créditos ni tocan la red.

## Pendientes conocidos

- **Los datos de `app/data/` se copian a mano.** Habría que engancharlos al
  workflow del ETL para que lleguen solos tras cada corrida.
- **`core/etl_sql.py` no se usa.** Se conserva como base de la vista materializada
  que haría falta para consultar la base directamente.

- **El endpoint está abierto.** Cualquiera que alcance la API puede gastar los
  créditos de OpenAI. Antes de exponerlo hay que ponerle autenticación y un límite
  de peticiones.
- **Secretos:** `OPENAI_API_KEY` y `DATABASE_URL` están en `.env` (ignorado por git).
  En despliegue van como secretos del entorno.
- **Migraciones:** falta Alembic; aún no hay tablas propias.
