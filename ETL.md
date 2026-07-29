# ETL SCR — datos del dashboard

Regenera el payload JSON que consume el mapa de calor, leyendo la base de datos
`dbanalitica.historico_mo` (PostgreSQL de producción) y escribiendo los archivos
estáticos en `dashboard/public/`.

---

## 1. Arquitectura

```
run_etl.py                 # CLI: parsea argumentos, configura logging, corre el pipeline
etl/
  __init__.py              # API pública: load_settings(), run()
  config.py                # Settings, rutas RELATIVAS al repo, constantes, consultas SQL
  logging_conf.py          # logging estándar (reemplaza los print sueltos)
  text.py                  # normalización de texto (norm, norm_dato) + regex
  taxonomy.py              # CAUSAS, HOMOLOG_BRIGADA, clasificación de causa
  database.py              # Database: conexión solo-lectura + consultas
  transform.py             # enrich(): filtros y campos derivados (vectorizado)
  geo.py                   # carga geojson + enlace punto-en-polígono (shapely)
  payload.py               # dims, codificación pts, split por mes, escritura JSON
  pipeline.py              # orquestación BD -> transform -> payload
requirements.txt
.env.example
.github/workflows/etl.yml  # automatización
```

**Flujo:** `Database` trae las órdenes y el mapa subacción→estado → `transform.enrich`
deriva Estado, GPS, causa, dedup → `payload.build_and_write` arma `dim`/`pts`/`geo`,
parte por mes y escribe `data.json` + `data_YYYY-MM.json`. El front los sirve estáticos.

## 2. Mejoras aplicadas y por qué

| Mejora | Motivo |
|---|---|
| **Rutas relativas al repo** (`Path(__file__)`) en vez de `C:/...` absolutas | El script original NO corría en Linux/CI. Ahora es portable. |
| **Separación en módulos** (SRP / SOLID) | El monolito de ~760 líneas mezclaba BD, transformación, geo y escritura. Cada módulo tiene una sola responsabilidad y se testea/reusa aparte. |
| **Vectorización** del `df.apply(..., axis=1)` de causas y de las normalizaciones (una vez por valor único, no por fila) | El `apply` fila-por-fila era el mayor cuello del código. Ahora es `map` con diccionarios. Mismo resultado, mucho más rápido; el tiempo restante lo domina la descarga de 245k filas (red), no la CPU. |
| **Logging estructurado** (timestamp + nivel) en vez de `print` | Se ve claro en los logs de GitHub Actions; niveles configurables. |
| **Manejo de errores** con excepciones tipadas y `exit code != 0` | Si la BD falla o el Estado queda vacío, el job de CI **falla visiblemente** en vez de escribir basura. |
| **Type hints + docstrings** en las funciones principales | Legibilidad y autocompletado; documenta contratos. |
| **`Settings` (dataclass)** + `.env` | Configuración centralizada e inmutable; nada hardcodeado. |
| **CSV opcional / sin HTML ni modo público** (DRY/KISS) | Eran salidas auxiliares no usadas por la app (el HTML ya se saltaba por falta de plantilla). Se elimina código muerto. |
| **`Database` como context manager** | Garantiza cierre de conexión aunque haya error. |

> **Comportamiento preservado:** el JSON regenerado es **byte-idéntico** al del
> script original (verificado: los 6 archivos de mes idénticos y `data.json`
> idéntico salvo `meta.generated`). No se cambió ninguna regla de negocio.

## 3. Cuellos de botella y siguientes optimizaciones

- **Descarga de 245k filas (~14s):** es el costo dominante (red). Se podría bajar
  con un cursor server-side (`fetchmany`) o filtrando en SQL solo columnas usadas.
  Para un job programado no es crítico.
- **Enlace geográfico shapely (~6s):** ya usa `STRtree`; aceptable.
- **Memoria:** se podría reducir con `dtype` categóricos y soltando columnas de
  texto pesadas (observaciones) antes de codificar. No es necesario en CI.

## 4. Uso local

```bash
pip install -r requirements.txt
# dashboard/.env debe tener SCR_DATABASE_URL (ver .env.example)
python run_etl.py                 # regenera el JSON
python run_etl.py --csv           # además el CSV consolidado
python run_etl.py --log-level DEBUG
```

## 5. Automatización con GitHub Actions

El workflow [`.github/workflows/etl.yml`](.github/workflows/etl.yml) corre el ETL,
y si los datos cambiaron **commitea el JSON regenerado** → Vercel redespliega solo.

### Paso a paso

1. **Sube el repo a GitHub** (si no lo está) con esta estructura.
2. **Crea el Secret** con la conexión:
   `Settings → Secrets and variables → Actions → New repository secret`
   - Name: `SCR_DATABASE_URL`
   - Value: `host=... port=5432 dbname=postgres user=... password=...`
3. **Permisos de escritura del workflow** (para que pueda commitear):
   `Settings → Actions → General → Workflow permissions → Read and write permissions`.
   (El YAML ya declara `permissions: contents: write`.)
4. **Verifica el cron.** Está en UTC. El ejemplo `0 6 * * *` = 06:00 UTC = 01:00
   Colombia. Cámbialo a lo que necesites (p. ej. `0 */6 * * *` cada 6 horas).
5. **Primera corrida manual:** pestaña `Actions` → *ETL SCR* → `Run workflow`.
6. **La RDS debe aceptar conexiones desde GitHub** (IPs públicas de los runners).
   Ya confirmaste que es accesible; si no, se usa un runner self-hosted en tu red.

### Estrategia de ejecución recomendada

- **Programada (cron):** para mantener los datos frescos sin intervención. Es la
  principal.
- **Manual (`workflow_dispatch`):** para forzar un refresco puntual.
- **Por push:** NO recomendado aquí — regenerar en cada commit es desperdicio y
  puede crear bucles (por eso el commit del bot lleva `[skip ci]`).

### Variables de entorno y secretos

- El único secreto es `SCR_DATABASE_URL`, en **GitHub Secrets** (cifrado, nunca en
  el repo ni en logs).
- Local: en `dashboard/.env` (ignorado por git). Plantilla en `.env.example`.
- `ETL_LOG_LEVEL` (opcional) controla el detalle de los logs.

### Monitoreo y logs

- Pestaña **Actions** → cada corrida muestra estado (✓/✗), duración y logs por paso.
- Si el ETL falla, el job sale con código ≠ 0 → aparece en rojo y puedes activar
  notificaciones por email (`Settings → Notifications`).
- El paso *Commitear* solo hace push si hubo cambios (`git diff --cached --quiet`).

## 6. Recomendaciones para escalar/mantener

- **Tests:** agregar `pytest` con fixtures pequeños para `transform.enrich`
  (validar Estado/causa/dedup) y `payload` (dims/encoding). El diseño modular ya
  lo facilita.
- **Fijar versiones** en `requirements.txt` (pin exacto) para builds reproducibles.
- **Linter/formatter:** `ruff` + `black` en un workflow de CI aparte.
- **Alertas de calidad:** hacer que el ETL falle (o avise) si el % de filas sin
  Estado o sin GPS supera un umbral.
- **Homologación de barrios:** integrar la tabla de homologación (nombre comercial
  → catastral) como un paso más en `transform`/`payload` cuando se decida aplicar.
- **Secret rotation:** rotar la credencial de BD periódicamente y actualizar el Secret.
