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
| `Frontend/dashboard/` | Tablero Next.js: mapa de calor, paneles y el chat. |
| `Modelos/` | Modelo CatBoost de riesgo social (predice agresividad del cliente). |
| `.github/workflows/etl.yml` | Cron del ETL. **Debe vivir en la raíz**: GitHub solo lee ahí. |

## Reglas del dominio que no son obvias

- **Hay dos efectividades y no son intercambiables.** `ef_pct` es cruda
  (`efectivas / total`) y es la que muestra el mapa. `ef_adj` es ajustada
  (`efectivas / (total − no controlables)`) y es la que ordena los rankings.
  Cualquier cifra debe decir cuál está usando.
- **El chat y el mapa leen la misma fuente**: los JSON del ETL. Nunca calcular
  las métricas por otro camino, o las dos pantallas empiezan a discrepar.
- **`Backend/app/core/taxonomy.py` es un espejo de `Etl/etl/taxonomy.py`.** Si
  cambias una causa o una homologación, cámbiala en los dos. Hay una prueba que
  lo verifica, pero solo corre si pandas está instalado en el venv del backend.
- **`Backend/app/core/etl_sql.py` no se usa.** Es la réplica en SQL de las reglas
  del ETL, guardada como base de una futura vista materializada.

## Comandos

```bash
# Backend
cd Backend && uvicorn app.main:app --reload --reload-dir app
cd Backend && .venv/Scripts/python.exe -m pytest -q

# Frontend
cd Frontend/dashboard && npm run dev

# ETL (toca la base de producción)
cd Etl && python run_etl.py
```

## Pendientes conocidos

- Repartir la salida del ETL a `Frontend/dashboard/public/` y `Backend/app/data/`
  sigue siendo manual.
- El endpoint del chat no tiene autenticación ni límite de peticiones.
- Los pulgares de calificación del chat no se guardan en ningún lado.
- La herramienta `efectividad` no acepta filtro por brigada: si le preguntan por
  una, responde sin ese filtro y redacta como si lo hubiera aplicado.

---

> `Frontend/dashboard/CLAUDE.md` tiene instrucciones adicionales que aplican solo
> al tablero.
