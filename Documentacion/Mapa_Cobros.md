# Mapa de COBROS — cómo funciona

Documento para quien ajuste el **asistente de chat de cobros**. Explica de dónde salen
los datos del mapa, qué significa cada cifra y en qué se parece o se diferencia de SCR,
para que el chat responda lo mismo que muestra el tablero.

Estado: octubre de 2026. El tablero está en `Frontend/dashboard`, ruta **`/cobros`**
(SCR sigue en `/`). El chat y el backend **no se han tocado**: leen datos viejos y
usan reglas de SCR (ver sección 8).

---

## 1. Flujo de datos

```
BD (dbanalitica)                ETL                         Tablero
────────────────                ───                         ───────
historico_aire_cobros  ──►  etl/run_etl.py --proceso cobros ──►  etl/salida/cobros/*.json
v_aire_cobros_nic_mes  ──►  (misma corrida: gestores.json)          │
                                                                    ▼ (copia manual)
                                              Frontend/dashboard/public/cobros/*.json
```

- **Comando** (desde `etl/`): `python run_etl.py --proceso cobros`. Lee la variable
  `SCR_DATABASE_URL` (formato `postgresql://…`, driver `psycopg2`). Es solo lectura.
- **Salida**: `etl/salida/cobros/`. El ETL **no** la copia al tablero: hay que copiar los
  `.json` a `Frontend/dashboard/public/cobros/` a mano (o con el flujo de despliegue).
- **Año**: el ETL conserva solo el año del registro más reciente (hoy 2026). Con la
  corrida del 10-oct-2026: 955.849 filas leídas, **721.644** entran al mapa (las demás
  son de años anteriores o no traen GPS válido).
- **Caché del navegador**: el tablero guarda cada mes en IndexedDB (`dashboard-cache`)
  durante 1 hora. Tras regenerar los datos, el botón «Actualizar información» o borrar esa
  base evita ver datos viejos.

## 2. Fuentes en la BD

| Objeto | Qué es | Quién lo usa |
|---|---|---|
| `dbanalitica.historico_aire_cobros` | Una fila por gestión. | El ETL del mapa (`QUERY_COBROS` en `etl/etl/config.py`). |
| `dbanalitica.v_aire_cobros_gestion` | La misma tabla sin las **110 filas duplicadas** (clave repetida; se conserva la más reciente). | Nadie todavía. El mapa lee la tabla cruda, así que esas 110 gestiones se cuentan dos veces (≈0,01%). |
| `dbanalitica.v_aire_cobros_nic_mes` | Una fila por **cuenta (NIC) y mes**; los atributos de cartera (saldo, recaudo) aparecen una sola vez. Trae `resultado_gestion` (el de la gestión vigente del mes), `gestiones_con_pago`, `gestiones_sin_recaudo`, `gestiones_no_efectivas` y `recaudo_energia_balanza`; `calculo_provisional` es verdadero para el mes en curso (el mes aún no cierra). | `gestores.json` (`QUERY_GESTORES`). |

Sumar `saldo` o `recaudo_sin_irr` (antes `recaudo_cartera`; la BD lo renombró el 14-oct-2026,
los valores son idénticos) sobre la tabla cruda los repite por cada gestión de la
cuenta: para dinero usar siempre la vista por NIC y mes.

## 3. Definiciones de negocio (las que debe respetar el chat)

### Resultado de la gestión (`estado_final` de la BD)
Es el filtro **«Resultado gestión»** de la barra superior y lo que separa Efectiva de
Perdida. Son 5 valores y ninguna fila queda sin clasificar:

| Código en BD | Nombre en pantalla | Clasificación en el mapa |
|---|---|---|
| `ABONO` | Abono | Efectiva |
| `ACUERDO DE PAGO` | Acuerdo de pago | Efectiva |
| `PAGO TOTAL` | Pago total | Efectiva |
| `VESP` | Visita efectiva sin pago | Efectiva |
| `VF` | Visita fallida | **Perdida** |

- **No existe la categoría «Fallida»** en cobros. En SCR hay Efectiva/Fallida/Perdida;
  aquí solo **Efectiva** y **Perdida**. El código de estado `1` (Fallida) queda en
  `dim.estados` por compatibilidad, pero ningún punto lo usa.
- «Visita fallida» (VF) **sí existe**: es el nombre del resultado que equivale a Perdida.
- Una gestión es **Efectiva** si el resultado no es VF, aunque no haya pago (VESP). Por eso
  la efectividad ronda el 95%: mide visitas realizadas, no cobros logrados.
- Va en `pts.x` (índice en `dim.gests`).

### Recaudo de la gestión (`resultado_gestion` de la BD)
Es **otra dimensión**, calculada por la BD con las reglas de recaudo. Alimenta el
**submenú de «Efectivas» en Capas** y las columnas «con pago» / «sin recaudo» de las
tablas. Va en `pts.rg` (índice en `dim.resultados`).

| Valor en BD | Nombre en pantalla |
|---|---|
| `Efectiva con Pago` | Efectiva con pago |
| `Efectiva sin recaudo` | Efectiva sin recaudo |
| `Efectiva sin validar` | Efectiva sin validar |
| `No efectiva` | No efectiva |

Reglas (las define la BD):
- **Efectiva con pago / sin recaudo**: para los resultados PAGO NO APLICADO, NO REALIZO
  ACUERDO DE PAGO, NO REALIZO PAGO FACTURA UNO y REALIZO PAGO: con pago si
  `recaudo_energia_balanza > 0` (antes `recaudo_energia`) y `fecha_pago >= fecha_gestion`; si no, sin recaudo.
- **REALIZO ACUERDO DE PAGO**: con pago si el acuerdo se valida en el consolidado de
  acuerdos materializados; sin recaudo si se valida que no; **«sin validar»** mientras no
  se haya validado (`acuerdos_evaluados = false`: abril–diciembre de 2025 y el mes en curso).
- **No efectiva**: `CONTACTO NO EFECTIVO` y otros resultados sin contacto (NO CONTESTA,
  BUZON DE MENSAJES, TELEFONO EQUIVOCADO/ERRADO, CLIENTE CUELGA LLAMADA, PRE-ACUERDO).

**Las dos dimensiones no son equivalentes.** Se calculan con reglas distintas y en unas
~500 gestiones de octubre no coinciden (p. ej. «Visita efectiva sin pago» con «Efectiva con
pago»). Para separar Efectiva de Perdida el mapa usa **solo** el resultado de la gestión
(`estado_final`); «No efectiva» incluye llamadas sin contacto que `estado_final` aún
marca como VESP. Las gestiones «No efectiva» que el mapa trata como efectivas no tienen
casilla en el submenú de Capas (siempre se dibujan).

### Efectividad
- **Efectividad (bruta)** = gestiones que no son VF / total de gestiones.
- **No hay efectividad ajustada en cobros.** Ninguna perdida se descarta por «no
  controlable»: todas cuentan. El tablero muestra solo la bruta. (En el código, para cobros
  se fuerza que toda causa sea controlable, así `efAdj == efPct`.)
- **% con pago** = gestiones «Efectiva con pago» / gestiones (en las tablas por gestor
  es cuentas «Efectiva con pago» / cuentas gestionadas).

### Causas y motivos
- **Motivo de pérdida** (solo VF): la anomalía, p. ej. «Cliente no contactable»,
  «Predio desocupado», «Dirección errada». Marcada «no controlable» si
  `CAUSAS_COBROS` (`etl/etl/taxonomy.py`) lo dice. Es solo información: no cambia ningún
  cálculo.
- **Motivo de no pago** (VESP): también la anomalía, p. ej. «No es el titular» (33,5%),
  «Compromiso de pago» (32,9%), «Sin voluntad de pago» (30,4%) en octubre. Va en `pts.ca`.
- **Detalle según el acta** (`pts.sc`): categoría sacada del texto que escribe el gestor
  (`observaciones`) con reglas de palabras clave, en este orden (gana la primera que
  coincide): Usuario agresivo, Sector peligroso, Predio desocupado, Predio demolido o
  inexistente, Dirección no ubicada, Predio cerrado o sin respuesta, No permite el
  ingreso, Animal bravo, Dejó notificación, y «Sin detalle». Las reglas están en
  `SUBCAUSAS_OBS`. Es aproximado: cuenta **menciones en el texto**, no causas oficiales.
- **Usuario agresivo** no es un campo de la BD: solo existe por las menciones en el acta
  (`agresiv|amenaz|insult|grit|maltrat`). En 2026 hay unas 700 gestiones, casi todas VESP.
- **No contacto** = gestiones con la anomalía «No es posible contactar al cliente en el
  predio» (causa «Cliente no contactable»): 81,1% de las perdidas de octubre.

### Riesgo del barrio
Solo cobros. El riesgo mide **qué tanto pierde el barrio frente al promedio**:

```
veces_promedio = %perdidas_del_barrio / %perdidas_de_la_operación (con los filtros activos)
```

| Categoría | Cuándo | Número (0–100) |
|---|---|---|
| Baja | menos de 1× el promedio | 0–30 |
| Media | de 1× a 2× | 31–60 |
| Alta | de 2× a 3× | 61–80 |
| Crítica | 3× o más | 81–100 |

- El número es lineal dentro de cada tramo para poder reutilizar los colores del tablero
  (hotspot = riesgo ≥ 60 por defecto, es decir Alta y Crítica).
- **Muestra pequeña**: barrios con **menos de 30 gestiones**. Se categorizan igual, pero
  llevan la marca y, en la cola de triaje, van después de los de muestra suficiente dentro
  de su nivel.
- Volumen, tendencia e historial del gestor **no entran** en el riesgo de cobros (sí en SCR).
- Referencia (octubre 2026, promedio de la operación 4,6%): 254 barrios Bajos, 26 Medios,
  16 Altos y 36 Críticos de 332.

### Territorio (municipio y barrio)
En cobros **el municipio y el barrio salen del GPS**, no del nombre de la base. Cada
gestión se cruza con los polígonos de `public/geojson` (barrios y municipios): si cae
dentro de un barrio oficial toma su nombre y municipio; si solo cae en un municipio, se
corrige el municipio; si no cae en nada, se deja lo que dice la base. Con el corte de
octubre esto corrigió el municipio de 27.472 gestiones (3,8%). Los nombres de municipio
se normalizan sin tildes (como en la base). Código: `asignar_territorio` en `etl/etl/geo.py`.

«Barrios sin visitar» = polígono sin ninguna gestión **dentro del filtro actual**
(un mes → sin gestión ese mes; varios meses → sin gestión en ninguno).

## 4. Archivos que genera el ETL (`public/cobros/`)

| Archivo | Contenido |
|---|---|
| `data.json` | Mes en curso: `meta`, `dim` (catálogos), `geo` (polígonos) y `pts` (un arreglo por campo, un índice por gestión). |
| `data_YYYY-MM.json` | Solo `pts` de cada mes anterior (carga perezosa). |
| `observaciones_YYYY-MM.json` | `{obs: [...]}`: el acta de cada gestión, **alineada por posición** con `pts` de ese mes. |
| `gestores.json` | Vista por gestor y mes (ver abajo). |
| `direcciones.json` | Vacío en cobros (la base no trae dirección). |

### `pts` (por gestión)
`la`, `lo` (coordenadas codificadas: `lat = la/1e5 + meta.lat0`), `e` (estado: 0 Efectiva,
2 Perdida; el 1 no se usa), `b` barrio, `t` gestor, `g` plan, `o` tipo de gestión, `c`
causa (la anomalía **solo en perdidas**; en efectivas vale «Efectiva»), `s` línea de acción,
`u` suspensión (siempre «SIN DATO»), `f` tarifa, `a` actividad (= línea de acción), `m`
minutos desde `meta.fecha_min`, `n` id de gestión, `nic`, y los **nuevos de cobros**:

- `x` → índice en `dim.gests` (resultado de la gestión, de `estado_final`: los 5 de arriba).
- `rg` → índice en `dim.resultados` (recaudo, de `resultado_gestion`: los 4 de arriba).
- `ca` → índice en `dim.anoms` (anomalía homologada, **también en efectivas**).
- `sc` → índice en `dim.subcausas` (categoría según el acta).

### `dim` (catálogos)
`barrios` (`"MUNICIPIO | BARRIO"`), `tecs` (gestores, con la cédula: `"NOMBRE - 123456"`),
`brigs` (planes), `tipos`, `causas`, `subs`, `susps`, `tarifas`, `acts`, `munis`, `zonas`,
`estados`, `causa_ctrl`, `causa_fam`, `b_muni`, `b_zona`, y los nuevos `gests`, `resultados`,
`anoms`, `subcausas`.

### Tabla por gestor (Tendencias > Por gestor y Ranking) y `gestores.json`
**Las cifras son las de `v_aire_cobros_nic_mes`.** El ETL agrega la vista (una fila por
cuenta y mes, ya sin duplicados) por *mes, gestor, zona, municipio, plan, resultado de la
gestión y recaudo* y la escribe en `gestores.json` (≈19.000 filas, ≈650 KB). El navegador
la filtra y la suma (`tablaGestores` en `Tablero.js`):

```
{ "gestores": [...], "meses": ["2026-01", ...], "zonas": [...], "municipios": [...],
  "planes": [...], "estados": [...], "resultados": [...], "provisionales": ["2026-10"],
  "filas": [[gestor, mes, zona, municipio, plan, estado, resultado, cuentas, saldo, recaudo], ...] }
```
`estados` lleva las mismas etiquetas que `dim.gests` y `resultados` las de `dim.resultados`.

Filtros que aplica: **Zona, Municipio, Plan, Resultado gestión y Meses**. El filtro
**«Gestión»** (tipo de gestión) **no aplica** porque la vista no trae el tipo; la tabla lo
avisa. El municipio es el de la BD (el del mapa se corrige por GPS), así que en ese filtro
puede haber una diferencia pequeña con los otros paneles.

Definiciones (una cuenta = una fila de la vista, con el `resultado_gestion` de su gestión
vigente del mes):
- `cuentas gestionadas`: todas las cuentas del gestor en los meses elegidos.
  `cuentas con gestión efectiva`: «Efectiva con pago» + «Efectiva sin recaudo».
  `con pago`: solo «Efectiva con pago». `sin validar`: «Efectiva sin validar», que **no** entra
  en las efectivas. `cuentas = efectivas + sin validar + no efectivas`.
- `deuda` = suma de `saldo` y `recaudo` = suma de `recaudo_sin_irr`, **solo de las cuentas
  efectivas** (con pago + sin recaudo).
- **% Recaudo del gestor** = `recaudo / deuda` del mes seleccionado (el último mes elegido)
  y del mes anterior. La fila de total suma cuentas, deuda y recaudo y **promedia** los
  porcentajes de los gestores.
- **El mes en curso es provisional** (`calculo_provisional = periodo_mes >= mes actual`): el
  encabezado lo marca «(prov.)» porque el recaudo y los acuerdos pueden cambiar hasta el cierre.
- **`saldo` es lo que queda por pagar después del recaudo**, no la deuda de origen: en las
  cuentas «con pago» casi la mitad tiene `saldo = 0` y su recaudo promedio supera al saldo
  promedio. Por eso `recaudo / deuda` puede pasar de 100% en gestores con mucha cartera
  ya pagada. La deuda de origen se aproxima con `saldo + recaudo` (o con `deuda_energia`).
- Con varios meses seleccionados, cuentas, deuda y recaudo se suman, así que una misma
  cuenta cuenta una vez por cada mes.

**Validación de la vista (14-oct-2026).** Llave `(cuenta, periodo_mes)` única en las 771.470
filas; las 955.739 gestiones de la vista suman igual que `v_aire_cobros_gestion` (110 menos que
la tabla cruda: los duplicados); `gestiones = con pago + sin recaudo + no efectivas` en todas
las filas; sin nulos en gestor, zona, municipio, plan, estado ni resultado; no falta ninguna
cuenta-mes de la tabla cruda. Tres cosas a tener presentes: 89 filas con recaudo negativo;
90.470 cuentas-mes (11,7%) fueron gestionadas por más de un gestor y la vista las deja con
uno solo (el de la gestión vigente); y 2.425 cuentas de 2026 donde `estado_final` y
`resultado_gestion` se contradicen (p. ej. VESP con «Efectiva con Pago»: 1.565).

## 5. Qué hace cada menú del tablero

| Menú | Qué calcula (cobros) |
|---|---|
| **Mapa** | Marcadores por barrio (color = categoría de riesgo), calor, puntos GPS, límites y barrios sin visitar. Con muchos puntos dibuja hasta 8.000 y siempre incluye todas las perdidas. |
| **Triaje** | Cola de barrios por categoría (todos, incluidos los de muestra pequeña). Umbral hotspot y mínimo de gestiones (por defecto 1). |
| **Tendencias** | «1.ª mitad → 2.ª mitad» del periodo (efectividad, tasa perdida, tasa de no contacto, volumen) y barras por día. Vista **Por gestor**: tabla con deuda, recaudo y % de recaudo (recaudo / deuda) y gráfico. |
| **Causas** | Motivos de las gestiones «No efectivas» y de las «Efectivas sin recaudo»; clic filtra el mapa; detalle según el acta; conteo de menciones de usuario agresivo. |
| **Ranking** | La tabla por gestor (con cuentas con pago, sin recaudo y sin validar), ordenada por % con pago (los de menos de 30 cuentas van al final), más el desempeño por plan. Clic en un gestor filtra el mapa. |
| **Carga** | Todos los barrios con gestiones, gestores, planes, gestiones por gestor, % con pago y categoría. |
| **Jerarquía** | Barrio → plan → gestor → gestiones individuales (clic abre el historial del NIC). |
| **Detalle** | Resumen, ficha de barrio, cobertura del gestor, recomendador de gestor/plan e historial del NIC. |

Todo —incluida la tabla por gestor (salvo «Gestión»), el conteo de la barra superior («N barrios · N gestores ·
fechas») y «Barrios sin visitar»— respeta los filtros de la barra superior (zona, municipio, plan, gestión, **resultado
gestión** y meses). En Capas, «Efectivas» tiene un submenú (con pago, sin recaudo, sin
validar) que solo cambia lo que se dibuja en el mapa, no las tablas. «Actividad» no se muestra en cobros.

### Filtros: solo ofrecen lo que existe en los datos
- Cada lista (Zona, Municipio, Plan, Gestión, Resultado gestión) muestra únicamente los valores
  con gestiones dadas las demás selecciones. Una píldora con 0 o 1 valores posibles se apaga
  (con el motivo en el tooltip); si ya tiene un valor puesto sigue activa para poder quitarlo.
- En Capas, «Perdidas», «Efectivas» y cada tipo de recaudo del submenú se apagan o se ocultan si
  no hay gestiones de ese tipo con los filtros actuales.
- En Tendencias, «Por semana» y «Por mes» solo aparecen si el rango trae al menos dos.

### Zonas (`zonas_atlantico.geojson`)
Hay tres copias con la misma geometría: `Frontend/dashboard/public/geojson/` (la que lee el ETL y
dibuja el mapa), `Backend/app/data/geojson/` y `Frontend/dashboard/zonas_atlantico.geojson` (solo
cambian los colores). **Cualquier ajuste de límites se hace en las tres y en `geo.zp` de cada
`data.json`** (el ETL lo regenera desde la copia de `public/`). El barrio **Puerta Dorada**
(Soledad) estaba casi todo fuera de las tres zonas (solo 2% dentro de CENTRO); desde el
14-oct-2026 queda completo dentro de CENTRO. Comparación: `Documentacion/ajuste_zona_puerta_dorada.png`.
La zona de cada gestión en los datos sale de la columna `zona` de la BD, no de estos polígonos:
el cambio afecta solo a lo que se dibuja con «Límites oficiales > Zonas».

## 6. Diferencias con SCR (lo que NO aplica a cobros)

- Sin «Fallida» (ni columnas, ni barras, ni tooltips).
- Sin «Suspensión» (las suspensiones son órdenes de SCR, tipo TO501).
- Sin efectividad ajustada ni causas «no controlables» que cambien el cálculo.
- Riesgo por veces el promedio, no por la mezcla ponderada de SCR.
- Barrio y municipio por GPS.
- El filtro «Actividad» no existe.
- Etiquetas: Gestor (no técnico), Plan (no brigada), Gestión (no tipo OS), Línea de
  acción (no subacción).

## 7. Cifras de referencia para validar al chat (octubre 2026, datos del 13-oct)

- Gestiones del mes en curso: **14.334** (13.669 Efectivas, 665 Perdidas). Tasa de pérdida
  4,6%; efectividad 95,4%.
- Por resultado de la gestión: Visita efectiva sin pago 8.362, Pago total 4.661, Visita
  fallida 665, Abono 559, Acuerdo de pago 87. Por recaudo: Efectiva sin recaudo 8.124,
  Efectiva con pago 5.448, No efectiva 665, Efectiva sin validar 97. (La BD sigue
  actualizando `resultado_gestion`; estas cifras son de la corrida del 14-oct y pueden
  moverse un poco.)
- Motivos de las 665 perdidas: Cliente no contactable 539 (81,1%), Predio desocupado 104,
  Dirección errada 10, Predio demolido/inexistente 8, Vía en mal estado 3, Sector peligroso 1.
- Según el acta, de las 539 de «No contactable»: Predio cerrado o sin respuesta 60,1%,
  Sector peligroso 20,6%, Sin detalle 9,6%, Dejó notificación 8,7%.
- Año 2026: 721.644 gestiones en el mapa; 103 gestores en el año, 82 en octubre.
- Tabla por gestor de octubre: 12.959 cuentas con gestión efectiva (5.123 con pago + 7.836
  sin recaudo), 283 sin validar, $54.807 millones de deuda, $1.554 millones de recaudo y
  36,9% de cuentas con pago. (Con la definición anterior —cuentas que no eran VF— eran
  13.242: la diferencia son las 283 «sin validar».)

## 8. Lo que hay que ajustar en el chat y el backend (pendiente, fuera del tablero)

Verificado leyendo el código; **nada de esto se ha cambiado**:

1. **Datos viejos.** `Backend/app/data/cobros/` tiene los JSON anteriores: sin `x`, `ca`,
   `sc`, sin `gestores.json`, con barrio/municipio de la base (no del GPS) y con el estado
   calculado por la regla anterior (Efectiva/Fallida/Perdida por `resultado`). Hay que
   copiar ahí los JSON nuevos de `etl/salida/cobros/` y hacer que `payload_store.py` lea
   los campos nuevos. Cada punto trae dos campos nuevos: `x` (resultado de la gestión, de `estado_final`) y `rg` (recaudo, de `resultado_gestion`).
2. **Prompt** `Backend/app/prompts/sistema_cobros.md`: define Efectiva/**Fallida**/Perdida,
   explica `ef_adj` («efectividad ajustada») y manda a usarla para rankings, y habla de
   «no controlables». En cobros hay solo Efectiva/Perdida, no hay ajustada y el ranking del
   tablero va por % con pago. (La nota de que «Suspensión» no existe en cobros sigue siendo
   correcta.)
3. **Taxonomía** `Backend/app/core/taxonomy.py`: espejo de la del ETL. `ESTADOS` sigue en
   tres valores y `RESULTADOS_EFECTIVOS/PERDIDOS` ya no existen en el ETL (se reemplazaron
   por `ESTADOS_GESTION` —con los valores de `estado_final`—, `ESTADO_GESTION_PERDIDA` y `RESULTADOS_GESTION` —con los de `resultado_gestion`—).
   Hay una prueba que compara ambas.
4. **Métricas y herramientas** (`services/metrics_service.py`, `services/tools.py`): cuentan
   `fallidas`/`pct_fallidas` y ofrecen la efectividad ajustada como métrica principal.
   Habría que quitar las fallidas para cobros y ofrecer: % con pago, resultado de la gestión,
   motivo (anomalía), detalle según el acta, tasa de no contacto, riesgo por categoría y la
   vista por gestor (deuda, recaudo, cuentas con pago).
5. **Riesgo.** El chat no debe usar la fórmula de SCR: en cobros es veces el promedio de
   % perdidas, con las categorías y la marca de muestra pequeña de la sección 3.
6. **Duplicados.** Mientras no se borren las 110 filas duplicadas (o el ETL no lea
   `v_aire_cobros_gestion`), el chat y el mapa deben coincidir en contarlas.

## 9. Limitaciones conocidas del tablero

- Las reglas del acta son por palabras clave: pueden clasificar mal textos ambiguos.
- La deuda por gestor se suma por mes; con varios meses la deuda de una cuenta se repite.
- El promedio de referencia del riesgo cambia con los filtros (zona, municipio, meses…).
- Una sola gestión puede cambiar la categoría de un barrio de muestra pequeña.
- El filtro por motivo o gestor del mapa se conserva al cambiar de menú hasta quitarlo con
  la X del aviso «Mostrando solo: …».
