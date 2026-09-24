# Instrucciones para Claude

Este archivo se carga automáticamente al inicio de cada sesión. No hace falta
pedir que se lea.

---

## Cómo quiero que trabajes

<!-- ESTA ES TU SECCIÓN: escribe aquí lo que quieras que Claude respete siempre.
     Lo de abajo ya está acordado en sesiones previas; corrígelo o bórralo. -->

- **Pregunta antes de implementar.** "¿Se puede?", "me gustaría", "explícame" o
  "propón un plan" piden análisis y una recomendación, no código. Implementa solo
  con un imperativo claro: "hazlo", "dale", "hagamos la opción B".
- **Una fase a la vez.** Cierra y reporta lo que está en curso antes de empezar lo
  siguiente. No adelantes trabajo de fases posteriores.
- **Arregla lo que se pidió.** Si pido corregir un error puntual, corrige ese error
  y propón el resto aparte, sin meterlo de una vez.
- **El código más pequeño que resuelva el problema.** Nada de abstracciones para
  un solo caso de uso, opciones que nadie pidió ni capas "por si acaso". Reutiliza
  lo que ya existe antes de escribir algo nuevo, y borra lo que dejó de usarse en
  vez de dejarlo ahí.
- **Comentarios que expliquen el porqué, no el qué.** Si el comentario repite lo
  que el código ya dice, sobra. Se comenta la decisión no evidente: por qué este
  camino y no el otro, qué caso raro se está cubriendo, qué pasa si se cambia.
  Nada de separadores decorativos ni un `TODO` sin contexto.
- **Buenas prácticas del stack, en concreto:** anotaciones de tipo en Python;
  SQL siempre parametrizado; ningún secreto en el código; errores que digan qué
  hacer, no solo que algo falló; pruebas para la lógica de negocio; y respeta las
  convenciones del archivo que estás tocando, aunque no sean tu preferencia.
- **Español**, en el código y en los comentarios.
- **Di lo que no verificaste.** Si no corriste algo, dilo; no lo des por bueno.

---

## Estructura del repositorio

| Carpeta | Qué es |
|---|---|
| `Etl/` | Proceso independiente. Lee `dbanalitica.historico_mo` y genera los JSON del mapa en `Etl/salida/`. |
| `Backend/` | API FastAPI. Chatbot con OpenAI y tool calling sobre los datos del tablero. |
| `Backend/migraciones/` | Los `.sql` del esquema, numerados. Se aplican a mano; no hay Alembic. |
| `Frontend/dashboard/` | Tablero Next.js: mapa de calor, paneles y el chat. |
| `Modelos/` | Modelo CatBoost de riesgo social (predice agresividad del cliente). |
| `.github/workflows/etl.yml` | Cron del ETL. **Debe vivir en la raíz**: GitHub solo lee ahí. |

## Reglas del dominio que no son obvias

- **Fallida y perdida no son lo mismo, y la diferencia es plata.** Una orden
  *fallida* es la que la brigada no pudo ejecutar pero **igual se cobra**. Una
  *perdida* **no se cobra**: por eso se llama así. Cuando alguien pregunta "dónde
  se pierde más", habla de las Perdidas, no de las no efectivas en general — un
  barrio con efectividad pésima puede no tener ni una sola pérdida.
- **Hay dos efectividades y no son intercambiables.** `ef_pct` es cruda
  (`efectivas / total`) y es la que muestra el mapa. `ef_adj` es ajustada
  (`efectivas / (total − no controlables)`) y es la que ordena los rankings.
  Cualquier cifra debe decir cuál está usando.
- **El chat y el mapa leen la misma fuente**: los JSON del ETL. Nunca calcular
  las métricas por otro camino, o las dos pantallas empiezan a discrepar.
- **Primero la casilla, después el acta: dan cifras distintas y la casilla es la
  buena.** El payload trae 12 `causas` y **50 `subs`** (subacciones), y la tarifa
  con el estrato. Ahí están «PREDIO ENREJADO», «RED CHILENA/CONFIG. ESPECIAL»,
  «USUARIO AGRESIVO», «MINIMO VITAL». Buscarlas en el acta se queda corto cuando
  el técnico no escribió el término y se pasa cuando lo nombró sin ser el motivo:
  «red chilena» daba 1.487 por acta y 3.738 por casilla. El acta es para lo que
  no tiene casilla —voltaje, bornera, sellos, autoreconexión, medios de pago—.
- **Lo que el técnico escribe a mano vive en el acta, y solo el chat la lee.**
  `OBSERVACION` trae el acta de visita (`VS: ... VM: ... SS: ...`), y ahí quedan
  los detalles sin casilla propia: red chilena, voltaje, el estado del poste. El
  ETL la vuelca en `observaciones_YYYY-MM.json`, alineada **por posición** con
  `pts` de ese mes; si los dos largos no coinciden, el backend descarta el mes
  entero en vez de contar mal. Buscar ahí cuenta **menciones, no causas**: una
  orden efectiva puede nombrar el término igual que una perdida.
- **`tests/test_recortes.py` es el repertorio con el que se mide al asistente,
  y corre contra `tests/datos/`, no contra los datos de producción.** Cada caso
  es un error que alguien vio de verdad; cuando un pulgar abajo se diagnostica,
  se agrega ahí. El recorte congelado se regenera a mano con
  `tests/datos/generar.py` y **no debería cambiar**: antes estas pruebas
  afirmaban cifras contra `app/data/`, que el ETL regenera a diario, y se
  rompían solas cada mañana. Una suite que falla por diseño deja de avisar
  cuando el fallo es real.
- **Las respuestas con pulgar arriba se le inyectan al prompt como ejemplo, y
  sus cifras son veneno.** El bloque le prohíbe expresamente reutilizarlas: son
  de cuando se respondió, y si las copia da un número viejo con toda la
  seguridad de uno bueno. Subir `LIMITE_EJEMPLOS` encarece **todas** las
  preguntas, no solo las que se parezcan al ejemplo. Y como el chat no tiene
  autenticación, quien lo use puede votarse a sí mismo y con eso influir en lo
  que se le responde a los demás: es el costo aceptado de que sea automático.
- **El feedback del chat se guarda con la traza, y guardarlo nunca puede
  romper el chat.** Cada respuesta entregada deja una fila en
  `dbanalitica.chat_interaccion` con las tool calls que la produjeron: sin ellas
  un «el dato está mal» no se puede diagnosticar, porque no se sabe si falló la
  herramienta o la redacción. El registro no lanza jamás —si la base está caída
  se pierde el pulgar, no la respuesta—, y por eso `interaccion_id` puede venir
  nulo. El voto en sí (`PATCH /api/v1/feedback/{id}`) sí falla hacia el cliente.
- **Un filtro de catálogo (municipio, zona, subacción, tarifa...) que no resuelve
  a un único valor lanza `FiltroNoResuelto`, no devuelve el total sin filtrar ni
  vacía el cálculo en silencio.** El caso real: `municipio="Atlántico"` —el
  departamento, no uno de los 25 municipios— vaciaba TODO `_agrupar`, incluido un
  filtro de tarifa válido, y la nota de salida le echaba la culpa al mínimo de
  órdenes pedido en vez de al filtro. `bkeys` y `meses` quedan fuera de esta
  regla porque llegan pre-resueltos por `_recorte`/`expandir_meses`.
- **`Backend/app/core/taxonomy.py` es un espejo de `Etl/etl/taxonomy.py`.** Si
  cambias una causa o una homologación, cámbiala en los dos. Hay una prueba que
  lo verifica, pero solo corre si pandas está instalado en el venv del backend.
- **En `tools.py`, cada herramienta tiene su propio parámetro para lo mismo, y
  no comparten variable aunque se llamen igual.** Pasó de verdad: `_TARIFA` (del
  histórico, se niega a filtrar si el nombre es ambiguo) y la tarifa de
  `ordenes_cargadas` (del archivo, coincide por subcadena y avisa cuáles
  incluyó) se llamaban las dos `_TARIFA`; la segunda definición tapó a la
  primera en silencio y `ordenes_cargadas` le mostró al modelo el
  comportamiento de la otra herramienta durante semanas. Por eso la del cargue
  se llama `_TARIFA_CARGUE`. Antes de reutilizar una constante de parámetro
  entre dos herramientas, confirma que de verdad se comportan igual.
- **El chat consume el "Modelo Propensión de Pago Service" por HTTP, como un
  proveedor externo más.** Vive en otro repositorio, con su propio venv
  (`catboost`) y sus modelos `.cbm`. La herramienta `propension_pago` le manda
  el NIC como si fuera la cuenta —son el mismo número, verificado contra la
  base: 206.575 de 206.576 NICs coinciden exactos con una cuenta, misma
  dirección de cliente— porque no existe ninguna tabla que los una en
  `dbanalitica`. **Su comportamiento real no coincide con su propio README**:
  un cliente sin datos responde 404, no `200` con `encontrado: false`, y un
  cliente encontrado no trae ninguna clave `encontrado` que haya que inferir
  del código. `PropensionService.consultar()` normaliza eso. Si el README se
  actualiza, hay que volver a probar contra el servicio corriendo, no fiarse
  del texto. El servicio nunca puede tumbar el chat: si está caído o en modo
  degradado (503), se convierte en un resultado de herramienta.
- **`recomendar_tecnicos` cruza el archivo cargado con el histórico, todo en
  Python dentro de una sola llamada.** Para cada barrio del archivo (por orden
  pendiente descendente) busca quién rindió mejor ahí, reutilizando el mismo
  "ampliar al municipio" de `ranking`. No se hace llamando `ranking` una vez
  por barrio desde el modelo: un archivo de 100+ barrios agotaría las 4 rondas
  de tool calling en el primer intento. Es una recomendación por desempeño
  pasado, no una asignación óptima: no reparte carga ni conoce disponibilidad;
  por eso reporta cuánto tiene YA ese técnico en el archivo completo, para que
  se note si el mismo nombre sale repetido y corre riesgo de saturarse.
- **`Backend/app/core/etl_sql.py` no se usa.** Es la réplica en SQL de las reglas
  del ETL, guardada como base de una futura vista materializada.

## Comandos

```bash
# Backend
cd Backend && uvicorn app.main:app --reload --reload-dir app
cd Backend && .venv/Scripts/python.exe -m pytest -q

# Regenerar el recorte congelado de las pruebas (rara vez: cambia las cifras)
cd Backend && .venv/Scripts/python.exe tests/datos/generar.py

# Frontend
cd Frontend/dashboard && npm run dev

# ETL (toca la base de producción)
cd Etl && python run_etl.py
```

## Pendientes conocidos

- Repartir la salida del ETL a `Frontend/dashboard/public/` y `Backend/app/data/`
  sigue siendo manual. Los `observaciones_*.json` son la excepción: van solo al
  backend. Copiarlos al frontend sería peso muerto en cada visita al tablero.
- El endpoint del chat no tiene autenticación ni límite de peticiones. Y
  `ChatRequest.model` es libre: el cliente elige con qué modelo se le responde.
- Los endpoints de lectura del feedback (`GET /api/v1/feedback`) tampoco tienen
  autenticación, y ahí sí hay conversaciones completas de usuarios.
- El ciclo de leer votos, diagnosticar con la traza y convertirlos en casos de
  `test_recortes.py` sí se corrió una vez (Bug 10), pero con las 105
  interacciones sin calificar que había en la tabla: nadie ha dado un pulgar
  todavía en la interfaz real. Sin votos genuinos no hay forma de saber si el
  chat mejora, solo si no repite los errores ya encontrados a mano.

---

> `Frontend/dashboard/CLAUDE.md` tiene instrucciones adicionales que aplican solo
> al tablero.
