# CONTEXTO DEL PROYECTO: Air-e Cobros & SCR

Este documento consolida el contexto técnico, funcional y de negocio del proyecto **Air-e Cobros & SCR**. Ha sido diseñado para servir como fuente única de verdad para desarrolladores, científicos de datos, analistas y agentes de IA.

---

## 1. Visión General del Proyecto

El sistema es una plataforma integral de **analítica geoespacial, optimización operativa y asistencia inteligente** diseñada para la gestión de servicios en terreno de la empresa de energía **Air-e** en el departamento del Atlántico, Colombia.

El sistema unifica dos operaciones principales:
1. **SCR (Suspensión, Corte y Reconexión):** Gestión operativa y técnica de órdenes de suspensión, corte y reconexión del servicio eléctrico ejecutadas por brigadas en campo.
2. **COBROS (Gestión de Cartera y Cobranza):** Gestión de visitas domiciliarias y acciones de cobro realizadas por gestores de cartera para recuperar cartera vencida y lograr acuerdos de pago o recaudos inmediatos.

### Componentes de la Solución
- **Tablero Web Geoespacial (Frontend):** Mapas de calor interactivos, capas de polígonos por barrio/municipio/zona, análisis de triaje, Pareto de causales, jerarquía operativa y rankings de rendimiento.
- **Canal de Asistencia Inteligente (Chatbot LLM):** Asistente conversacional con *tool calling* (OpenAI) capaz de responder preguntas analíticas complejas, generar reportes de efectividad, filtrar el mapa en vivo y recomendar asignaciones operativas sin alucinar datos.
- **Motor de Recomendación Operativa (Backend API):** Algoritmo probabilístico (Wilson Score) que determina el técnico/gestor idóneo, mejor día/horario y evalúa riesgos de fallos en terreno para un NIC o lote de NICs.
- **Pipeline ETL de Alto Rendimiento:** Proceso desacoplado que lee millones de filas de la base analítica en PostgreSQL, homologa taxonomías, asigna polígonos espaciales vectorizados (`Point-in-Polygon`) y compila matrices compactas en JSON para consumo inmediato tanto en navegador como en memoria del servidor.
- **Modelos de Inteligencia Artificial (ML):** Modelos CatBoost para predicción de riesgo social / agresividad de usuarios en terreno y microservicios externos de propensión al pago.

---

## 2. Reglas de Dominio y Negocio

Existen diferencias conceptuales y matemáticas cruciales entre el módulo SCR y el módulo Cobros. **Nunca deben mezclarse.**

```
                        ┌───────────────────────────────────────┐
                        │      Operación Terreno Air-e          │
                        └──────────────────┬────────────────────┘
                                           │
                 ┌─────────────────────────┴─────────────────────────┐
                 ▼                                                   ▼
     ┌───────────────────────┐                           ┌───────────────────────┐
     │      Módulo SCR       │                           │     Módulo COBROS     │
     ├───────────────────────┤                           ├───────────────────────┤
     │ • 3 estados:          │                           │ • 2 estados:          │
     │   Efectiva, Fallida,  │                           │   Efectiva, Perdida   │
     │   Perdida             │                           │ • Sin estado Fallida  │
     │ • Fallida = Se cobra  │                           │   (VF = Perdida)      │
     │ • Perdida = $ Perdida │                           │ • Sin efectividad     │
     │ • Ef. Cruda y Ef. Adj │                           │   ajustada            │
     │ • Recomendación de    │                           │ • Indicadores Balanza │
     │   Técnico y Brigada   │                           │ • Geocodificación GPS │
     └───────────────────────┘                           └───────────────────────┘
```

### 2.1. Dominio SCR (Suspensión, Corte y Reconexión)

1. **Tres Estados de Orden:**
   - **Efectiva:** La brigada ejecutó la acción técnica requerida (corte, reconexión, suspensión).
   - **Fallida:** La brigada acudió pero no pudo ejecutar por razones atribuibles al cliente/predio o condiciones técnicas, pero la orden **se le cobra al cliente o a la operación** (no representa pérdida de costo de visita).
   - **Perdida:** La orden no pudo ejecutarse y **no se cobra**. Representa un costo hundido y pérdida directa de dinero para la empresa contratista/operación. Preguntar "dónde se pierde más" se refiere estrictamente a las órdenes **Perdidas**, no al total de no efectivas.
2. **Dos Tipos de Efectividad (No Intercambiables):**
   - **Efectividad Bruta / Cruda (`ef_pct`):**
     $$\text{ef\_pct} = \frac{\text{Efectivas}}{\text{Total de órdenes}}$$
     Es la que muestran los tooltips y marcadores del mapa.
   - **Efectividad Ajustada (`ef_adj`):**
     $$\text{ef\_adj} = \frac{\text{Efectivas}}{\text{Total} - \text{Causas No Controlables}}$$
     Descuenta factores ajenos a la brigada (fuerza mayor, orden público, etc.). **Es la que ordena los rankings y evalúa a los técnicos.**
3. **Casillas vs. Actas de Visita:**
   - La base entrega 12 `causas` estructuradas y 50 `subs` (subacciones). Casillas oficiales: «PREDIO ENREJADO», «RED CHILENA/CONFIG. ESPECIAL», «USUARIO AGRESIVO», «MINIMO VITAL».
   - Las cifras oficiales se obtienen de la **casilla estructurada**, no del texto libre.
   - El acta (`OBSERVACION`, `VS: ... VM: ...`) solo se consulta para detalles técnicos no catalogados (voltaje, estado de la bornera, autoreconexión, etc.). Buscar en el acta cuenta **menciones**, no causas formales.

### 2.2. Dominio COBROS (Gestión de Cartera)

1. **Dos Estados Operativos:**
   - En cobros **no existe la categoría «Fallida»**. Solo existen **Efectiva** y **Perdida**.
   - Cuidado semántico: la anomalía llamada «Visita Fallida» (VF) en la base de datos se clasifica como **Perdida**.
   - Toda visita que no sea VF se considera **Efectiva** (incluso una Visita Efectiva Sin Pago - VESP). La efectividad bruta de cobros suele rondar el ~95% porque mide visitas y contacto, no dinero recuperado.
2. **Efectividad en Cobros:**
   - En cobros no se aplica efectividad ajustada: todas las pérdidas cuentan como controlables (`ef_adj == ef_pct`).
3. **Las Dos Dimensiones de Cobros:**
   - **Resultado de la gestión (`estado_final` de la BD):** Clasifica la visita en 5 valores: `ABONO`, `ACUERDO DE PAGO`, `PAGO TOTAL`, `VESP` (Efectivas) y `VF` (Perdida).
   - **Recaudo de la gestión (`resultado_gestion` de la BD):** Calculado por reglas de negocio con la balanza: `Efectiva con Pago`, `Efectiva sin recaudo`, `Efectiva sin validar`, y `No efectiva`.
4. **Métricas Financieras y Balanza (`v_aire_cobros_nic_mes`):**
   - **Deuda:** Se aproxima como `saldo + recaudo`. El `saldo` es lo que queda pendiente por pagar tras el recaudo (no la deuda inicial).
   - **% con Pago:** Proporción de cuentas donde hubo recaudo efectivo sobre el total de cuentas gestionadas.
   - **Indicadores de Balanza (`historico_balanza`):**
     - `marca`: `"Gestionar"` si lo facturado en el último periodo es $\ge \$30.000$; `"No gestionar"` si es menor o nulo.
     - `pagos_ultimos_6_meses`: Número de facturas pagadas (0 a 6) en el último semestre cerrado.
     - `recaudo_mes_anterior` y `recaudo_mes_actual`: Banderas `"Sí"` / `"No"` según `recaudo_energia > 0`.
5. **Riesgo del Barrio en Cobros:**
   - Mide qué tantas pérdidas sufre el barrio relativo al promedio general de la operación:
     $$\text{veces\_promedio} = \frac{\%\text{ pérdidas del barrio}}{\%\text{ pérdidas de la operación}}$$
   - Categorías: **Baja** ($<1\times$), **Media** ($1\times - 2\times$), **Alta** ($2\times - 3\times$), **Crítica** ($\ge 3\times$).
   - Flag de **Muestra Pequeña**: Barrios con menos de 30 gestiones en el periodo filtrado.
6. **Asignación Geográfica por GPS:**
   - En cobros, el municipio y barrio oficiales se corrigen obligatoriamente haciendo Point-in-Polygon (GPS de la visita contra `zonas_atlantico.geojson` y capas de barrios). Corrige miles de inconsistencias de nombres en la BD.

---

## 3. Arquitectura del Repositorio

El repositorio está organizado en módulos independientes con responsabilidades claras:

```
Aire_Cobros_SCR/
├── .github/
│   └── workflows/
│       └── etl.yml            # Pipeline CI/CD que corre el ETL diario y commitea los JSON
├── Backend/                   # API FastAPI, Asistente Chatbot y Motor de Recomendación
│   ├── app/
│   │   ├── api/               # Rutas REST y SSE (v1/endpoints/openai.py, ordenes.py)
│   │   ├── core/              # Config, DB, taxonomías compartidas y normalizadores
│   │   ├── data/              # Copia local de JSONs (scr/ y cobros/) para lectura en memoria
│   │   ├── prompts/           # Prompts de sistema del LLM (sistema.md, sistema_cobros.md)
│   │   ├── schemas/           # Modelos de validación Pydantic
│   │   └── services/          # Lógica analítica, tool calling, geolocalización, recomendador
│   ├── tests/                 # Suite de pruebas pytest con fixtures congelados (test_recortes.py)
│   ├── requirements.txt
│   └── README.md
├── Documentacion/             # Manuales funcionales y de arquitectura (Mapa_Cobros.md, etc.)
├── etl/                       # Pipeline ETL independiente (Python)
│   ├── etl/                   # Extracción SQL, transformación vectorizada, Point-in-Polygon
│   ├── salida/                # Directorio de exportación de payloads JSON
│   ├── run_etl.py             # CLI del ETL
│   ├── requirements.txt
│   └── README.md
├── Frontend/
│   └── dashboard/             # Aplicación Next.js (App Router, Tailwind, Leaflet)
│       ├── public/            # GeoJSON de límites y JSONs generados por el ETL (scr/ y cobros/)
│       ├── src/
│       │   ├── app/           # Rutas: "/" para SCR y "/cobros" para Cobranzas
│       │   ├── components/    # Componentes React (LeafletMap, ChatBot, Tablero, Dock, etc.)
│       │   └── lib/           # Caché IndexedDB, temas, utilidades matemáticas
│       └── package.json
├── Modelos/                   # Modelos de Machine Learning (CatBoost)
│   └── Recomendacion Social/  # Notebooks y modelos serializados (.cbm) de riesgo social
├── Pruebas/                   # Scripts automatizados de pruebas E2E con bots (Playwright/Node)
│   └── bots/
├── CLAUDE.md                  # Reglas obligatorias de desarrollo y dominio para agentes IA
├── export_excel.py            # Utilidad para consolidar métricas en hojas Excel
└── CONTEXTO.md                # Este documento de referencia integral
```

---

## 4. Flujo de Datos y Pipeline ETL

```mermaid
flowchart TD
    DB[("PostgreSQL de Producción\n(dbanalitica)")] -->|Extracción SQL| ETL["Pipeline ETL (etl/run_etl.py)"]
    GEO["GeoJSON Límites y Barrios\n(zonas_atlantico.geojson)"] -->|Enlace Espacial STRtree| ETL
    
    ETL -->|Generación Vectorizada| JSONS["JSONs Compactos Codificados\n(data.json, data_YYYY-MM.json,\ngestores.json, observaciones.json)"]
    
    JSONS -->|GitHub Actions / Deploy| F_PUB["Frontend/dashboard/public/\n(Caché IndexedDB 1h en Navegador)"]
    JSONS -->|GitHub Actions / Deploy| B_DATA["Backend/app/data/\n(Cargado en memoria por payload_store)"]
    
    F_PUB --> UI["Dashboard Next.js (React + Leaflet)\nRutas: / y /cobros"]
    B_DATA --> API["Backend FastAPI (Services & Tools)"]
    
    UI <-->|SSE Chat & Tool Calling| API
    USER(("Usuario / Operación")) <-->|Interacción| UI
```

### 4.1. Codificación Compacta de Datos (`pts`)
Para procesar más de 700.000 registros en el navegador y en microservidores sin saturar ancho de banda ni memoria, el ETL no guarda JSONs con diccionarios por fila. Utiliza un esquema de **arrays paralelos por columna indexados numéricamente contra catálogos (`dim`)**:

| Clave | Tipo | Significado |
|---|---|---|
| `la`, `lo` | `int` | Coordenadas codificadas: $\text{lat} = \frac{\text{la}}{10^5} + \text{meta.lat0}$ |
| `e` | `int` | Índice de estado (0: Efectiva, 1: Fallida, 2: Perdida) |
| `b` | `int` | Índice del barrio en `dim.barrios` (`"MUNICIPIO \| BARRIO"`) |
| `t` | `int` | Índice del técnico/gestor en `dim.tecs` |
| `g` | `int` | Índice de brigada o plan de cobro en `dim.brigs` |
| `o` | `int` | Índice del tipo de orden o gestión en `dim.tipos` |
| `c` | `int` | Índice de causa de fallo en `dim.causas` |
| `s` | `int` | Índice de subacción / línea de acción en `dim.subs` |
| `f` | `int` | Índice de tarifa/estrato en `dim.tarifas` |
| `m` | `int` | Minutos transcurridos desde `meta.fecha_min` |
| `n` | `int/str` | Número de orden o id de gestión |
| `nic` | `str` | Identificador de contrato/cuenta del cliente |
| `x` | `int` | *(Cobros)* Índice del resultado de gestión (`dim.gests`: Abono, Acuerdo, Pago Total, VESP, VF) |
| `rg` | `int` | *(Cobros)* Índice de recaudo (`dim.resultados`: Efectiva con pago, sin recaudo, sin validar, no efectiva) |
| `ca` | `int` | *(Cobros)* Índice de la anomalía homologada |
| `sc` | `int` | *(Cobros)* Categoría derivada del texto del acta (`dim.subcausas`) |

---

## 5. Asistente Conversacional y Backend API

El backend implementa un patrón **ReAct / Tool-Calling** mediante OpenAI:

1. **Garantía Anti-Alucinaciones:** El modelo nunca calcula sumas, porcentajes ni efectividades en su texto generativo. Debe invocar herramientas determinísticas (`efectividad`, `ranking`, `causas_no_efectivas`, `buscar_barrio`, `recomendar_tecnicos`, etc.) que consultan las matrices en memoria (`payload_store.py` / `metrics_service.py`).
2. **Acciones Bidireccionales con el Mapa:** Las respuestas del chat pueden emitir directivas `FiltroMapa` como eventos SSE (`data: {"accion": {...}}`), lo que permite que el chatbot filtre interactivamente las capas del mapa del usuario en tiempo real.
3. **Cargue Dinámico de Órdenes:** Permite al usuario subir un Excel/CSV con órdenes pendientes (`POST /api/v1/ordenes/cargar`). La API geolocaliza las direcciones en 3 niveles de cascada:
   - 1) Historial previo de coordenadas del mismo NIC (gratis y exacto).
   - 2) Geocodificador HERE API.
   - 3) Centroide poligonal del barrio si no se logra geocodificar.
4. **Algoritmo de Recomendación (Wilson Score):** Calcula intervalos de confianza binomial inferior para ordenar técnicos y gestores evitando favorecer a aquellos con muestras minúsculas (ej. 1 orden con 100% efectividad queda por debajo de quien tiene 95 órdenes con 90% efectividad).

---

## 6. Modelos de Machine Learning (CatBoost)

Ubicados en `Modelos/Recomendacion Social`:
- **Propósito:** Predecir el nivel de riesgo social y probabilidad de agresividad de usuarios contra las brigadas en terreno antes de enviar a los operarios.
- **Artefactos:** `Modelo_RiesgoSocial.cbm` entrenado sobre variables sociodemográficas, antecedentes del predio, estrato, histórico de suspensiones y registro de actas de visitas previas.
- **Servicios Externos:** El chatbot se conecta adicionalmente con el *Modelo Propensión de Pago Service* (microservicio externo con modelos `.cbm`) mediante el NIC para estimar la probabilidad de recaudo voluntario.

---

## 7. Pruebas y Aseguramiento de Calidad

1. **Suite de Regresión Congelada (`Backend/tests/test_recortes.py`):**
   - Corre sobre un recorte de datos congelado (`tests/datos/`) generado mediante `tests/datos/generar.py`.
   - **Regla crítica:** Las pruebas unitarias nunca deben correr contra los datos vivos de producción (`app/data/`), pues las variaciones diarias de datos romperían las pruebas por diseño.
2. **Bots de Interfaz y Navegación (`Pruebas/bots/`):**
   - Scripts en Node.js que simulan combinaciones de filtros en el navegador, validando que los cálculos del frontend (tableros, filtros en cascada, reactividad de capas y Leaflet) coincidan exactamente con el backend y el oráculo analítico.
3. **Trazabilidad y Feedback (`dbanalitica.chat_interaccion`):**
   - Cada interacción del chat guarda el prompt, la respuesta y las tool calls ejecutadas con sus argumentos. Si el usuario califica con pulgar arriba/abajo, queda registrado para análisis forense de discrepancias.

---

## 8. Guía de Ejecución Rápida

### Requisitos Previos
- Python 3.11+
- Node.js 18+ y npm
- PostgreSQL (acceso de red a `dbanalitica`)

### Variables de Entorno Clave
- `SCR_DATABASE_URL`: Cadena de conexión PostgreSQL (`postgresql://usuario:password@host:5432/dbanalitica`).
- `OPENAI_API_KEY`: Llave de OpenAI para el servicio de chat y tool calling.
- `HERE_API_KEY`: Llave de geocodificación para el cargue masivo de órdenes.

### Comandos de Operación

```bash
# 1. Pipeline ETL (genera JSONs en etl/salida/)
cd etl
pip install -r requirements.txt
python run_etl.py --proceso todos

# 2. Servidor Backend (FastAPI en http://127.0.0.1:8000)
cd Backend
python -m venv .venv
.venv\Scripts\activate            # En Windows (.venv/bin/activate en Linux/macOS)
pip install -r requirements.txt
uvicorn app.main:app --reload --reload-dir app

# 3. Pruebas Unitarias del Backend
cd Backend
pytest -q

# 4. Tablero Web Frontend (Next.js en http://localhost:3000)
cd Frontend/dashboard
npm install
npm run dev

# 5. Generación de Reporte Operativo en Excel
python export_excel.py
```

---

## 9. Convenciones Obligatorias para Desarrolladores y Agentes

1. **Pregunta antes de implementar:** Presenta análisis y recomendaciones ante preguntas de diseño antes de generar cambios de código masivos.
2. **Una fase a la vez:** Cierra y valida cada componente antes de abordar el siguiente.
3. **El código más simple posible:** Evita sobreingeniería, abstracciones innecesarias y código muerto.
4. **Comentarios de intención:** Comenta el **porqué** de una decisión de negocio no evidente, no el **qué** del código elemental.
5. **Idioma:** Código, comentarios, esquemas y documentación en **Español**.
6. **Espejo de Taxonomías:** Cualquier cambio en causales, estados u homologaciones en `etl/etl/taxonomy.py` debe replicarse obligatoriamente en `Backend/app/core/taxonomy.py`.
