# API de Recomendación

**Base URL:** `http://52.88.48.137`

---

## 1. Individual — `GET /api/v1/ordenes/recomendar/{nic}`

Recomienda técnicos y brigadas para un NIC según desempeño histórico.

### Parámetro de ruta

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `nic` | `string` | Número de identificación del cliente |

### Ejemplo

```bash
curl http://52.88.48.137/api/v1/ordenes/recomendar/7502057
```

### Respuesta 200 — `RecomendacionResponse`

→ Ver [esquemas de respuesta](#esquemas-de-respuesta) más abajo.

### NIC no encontrado → 404

```json
{"detail": "NIC 9999999 no encontrado en el histórico."}
```

---

## 2. Por lotes — `POST /api/v1/ordenes/recomendar`

Misma lógica Wilson, pero procesa una lista de NICs en una sola petición (sin límite).

### Request body

| Campo | Tipo | Req | Descripción |
|-------|------|-----|-------------|
| `nics` | `string[]` | sí | Lista de NICs a consultar |

### Ejemplo

```bash
curl -X POST http://52.88.48.137/api/v1/ordenes/recomendar \
  -H "Content-Type: application/json" \
  -d '{"nics": ["7502057", "2313797", "7798002"]}'
```

### Respuesta 200 — `RecomendacionBatchResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `resultados` | `RecomendacionResponse[]` | Recomendaciones de los NICs encontrados |
| `no_encontrados` | `string[]` | NICs que no aparecen en el histórico |

> Los NICs que no existen **no hacen fallar** la petición: aparecen en `no_encontrados` y el resto se procesa normal.

---

## Diferencias entre endpoints

| | Individual | Lote |
|---|---|---|
| Método | `GET /recomendar/{nic}` | `POST /recomendar` |
| Entrada | Un NIC en la ruta | Lista de NICs en el body |
| NIC no encontrado | Responde 404 | Lo agrega a `no_encontrados` |
| Límite | 1 NIC | Sin límite |

---

## Esquemas de respuesta

### RecomendacionResponse

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `nic` | `string` | NIC consultado |
| `barrio` | `string` | Barrio (BKEY: `"MUNICIPIO \| BARRIO"`) |
| `municipio` | `string` | Municipio |
| `tecnicos_recomendados` | `CandidatoRecomendado[]` | Top 4 técnicos por Wilson |
| `brigadas_recomendadas` | `CandidatoRecomendado[]` | Top 3 brigadas por Wilson |
| `mejor_horario` | `MejorHorario` | Mejor día de la semana por efectividad |
| `causas_fallo` | `CausaFrecuente[]` | Top 5 causas de fallo en el barrio |
| `historial_nic` | `HistorialNic` | Resumen de visitas históricas al NIC |

### CandidatoRecomendado

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `nombre` | `string` | Nombre del técnico o brigada |
| `efectividad_ajustada` | `string` | Efectivas / (total − no controlables), e.g. `"87.2%"` |
| `efectivas` | `int` | Órdenes efectivas |
| `fallidas` | `int` | Órdenes fallidas (se cobran) |
| `perdidas` | `int` | Órdenes perdidas (no se cobran) |
| `ultima_orden` | `string \| null` | Fecha más reciente (`YYYY-MM-DD`) |

### MejorHorario

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `mejor_dia` | `string \| null` | Día de la semana con mejor efectividad, o null |
| `franjas` | `FranjaHoraria[]` | Franjas de 2h por efectividad (requiere hora en el payload) |

### FranjaHoraria

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `franja` | `string` | Rango horario, e.g. `"06:00–08:00"` |
| `efectividad` | `string` | Efectividad ajustada, e.g. `"85.3%"` |
| `ordenes` | `int` | Órdenes en esa franja |

### CausaFrecuente

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `causa` | `string` | Nombre de la causa |
| `ordenes` | `int` | Órdenes no efectivas con esta causa |
| `porcentaje` | `string` | Sobre el total de no efectivas, e.g. `"40.0%"` |

### HistorialNic

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `total_visitas` | `int` | Total de visitas al NIC |
| `efectivas` | `int` | Visitas efectivas |
| `fallidas` | `int` | Visitas fallidas (se cobran) |
| `perdidas` | `int` | Visitas perdidas (no se cobran) |
| `efectividad` | `string` | Efectividad cruda (efectivas/total), e.g. `"60.0%"` |
| `ultima_visita` | `string \| null` | Fecha de la última visita (`YYYY-MM-DD`) |

---

## Niveles de alcance (Wilson)

El algoritmo prueba 4 niveles progresivos y se detiene en el primero con ≥2 candidatos con ≥3 órdenes comparables:

1. Barrio + tipo de orden (más específico)
2. Barrio, todos los tipos
3. Municipio + tipo de orden
4. Zona + tipo de orden (más amplio)

---

## Ejemplo de respuesta (individual)

```json
{
  "nic": "7502057",
  "barrio": "PUERTO COLOMBIA | LAS MARGARITAS",
  "municipio": "PUERTO COLOMBIA",
  "tecnicos_recomendados": [
    {
      "nombre": "DAIRO JOSE PACHECO CANTILLO",
      "efectividad_ajustada": "99.1%",
      "efectivas": 108,
      "fallidas": 1,
      "perdidas": 1,
      "ultima_orden": "2026-08-26"
    },
    {
      "nombre": "YACID ALBERTO CARDENAS PEREZ",
      "efectividad_ajustada": "100.0%",
      "efectivas": 35,
      "fallidas": 4,
      "perdidas": 0,
      "ultima_orden": "2026-08-27"
    }
  ],
  "brigadas_recomendadas": [
    {
      "nombre": "Brigada Tipo Pesada",
      "efectividad_ajustada": "96.5%",
      "efectivas": 250,
      "fallidas": 23,
      "perdidas": 7,
      "ultima_orden": "2026-08-28"
    },
    {
      "nombre": "Brigada Tipo Liviana",
      "efectividad_ajustada": "80.0%",
      "efectivas": 12,
      "fallidas": 25,
      "perdidas": 4,
      "ultima_orden": "2026-07-23"
    }
  ],
  "mejor_horario": {
    "mejor_dia": "domingo",
    "franjas": []
  },
  "causas_fallo": [
    {"causa": "PREDIO CERRADO", "ordenes": 45, "porcentaje": "38.1%"},
    {"causa": "PREDIO ENREJADO", "ordenes": 22, "porcentaje": "18.6%"}
  ],
  "historial_nic": {
    "total_visitas": 8,
    "efectivas": 5,
    "fallidas": 2,
    "perdidas": 1,
    "efectividad": "62.5%",
    "ultima_visita": "2026-08-26"
  }
}
```

## Ejemplo de respuesta (lote)

```json
{
  "resultados": [
    {
      "nic": "7502057",
      "barrio": "PUERTO COLOMBIA | LAS MARGARITAS",
      "municipio": "PUERTO COLOMBIA",
      "tecnicos_recomendados": [
        {
          "nombre": "DAIRO JOSE PACHECO CANTILLO",
          "efectividad_ajustada": "99.1%",
          "efectivas": 108,
          "fallidas": 1,
          "perdidas": 1,
          "ultima_orden": "2026-08-26"
        }
      ],
      "brigadas_recomendadas": [
        {
          "nombre": "Brigada Tipo Pesada",
          "efectividad_ajustada": "96.5%",
          "efectivas": 250,
          "fallidas": 23,
          "perdidas": 7,
          "ultima_orden": "2026-08-28"
        }
      ],
      "mejor_horario": {"mejor_dia": "domingo", "franjas": []},
      "causas_fallo": [
        {"causa": "PREDIO CERRADO", "ordenes": 45, "porcentaje": "38.1%"}
      ],
      "historial_nic": {
        "total_visitas": 8,
        "efectivas": 5,
        "fallidas": 2,
        "perdidas": 1,
        "efectividad": "62.5%",
        "ultima_visita": "2026-08-26"
      }
    }
  ],
  "no_encontrados": ["9999999"]
}
```
