# Endpoint de Recomendación por Lote de NICs

## `POST /api/v1/ordenes/recomendar`

Recomienda técnicos y brigadas para un lote de NICs (sin límite de cantidad). Misma lógica Wilson que el endpoint individual, pero procesa todos los NICs en una sola petición.

**Base URL:** `http://52.88.48.137`

---

### Request body

| Campo | Tipo | Req | Descripción |
|-------|------|-----|-------------|
| `nics` | `string[]` | sí | Lista de NICs a consultar (sin límite) |

### Ejemplo

```bash
curl -X POST http://52.88.48.137/api/v1/ordenes/recomendar \
  -H "Content-Type: application/json" \
  -d '{
    "nics": ["7502057", "2313797", "7798002"]
  }'
```

---

### Respuesta 200 — `RecomendacionBatchResponse`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `resultados` | `RecomendacionResponse[]` | Recomendaciones de los NICs encontrados |
| `no_encontrados` | `string[]` | NICs que no aparecen en el histórico del ETL |

> Los NICs que no existen en el histórico **no hacen fallar** la petición: aparecen en `no_encontrados` y el resto se procesa normal.

---

### RecomendacionResponse (cada elemento de `resultados`)

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

### Ejemplo de respuesta

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
        }
      ],
      "mejor_horario": {
        "mejor_dia": "domingo",
        "franjas": []
      },
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
    },
    {
      "nic": "2313797",
      "barrio": "BARRANQUILLA | OLAYA",
      "municipio": "BARRANQUILLA",
      "tecnicos_recomendados": [
        {
          "nombre": "BRAVO CARDENAS JOHN JAIRO",
          "efectividad_ajustada": "97.0%",
          "efectivas": 96,
          "fallidas": 35,
          "perdidas": 4,
          "ultima_orden": "2026-08-21"
        }
      ],
      "brigadas_recomendadas": [
        {
          "nombre": "Brigada Tipo Pesada",
          "efectividad_ajustada": "87.6%",
          "efectivas": 677,
          "fallidas": 181,
          "perdidas": 20,
          "ultima_orden": "2026-08-27"
        }
      ],
      "mejor_horario": {
        "mejor_dia": "domingo",
        "franjas": []
      },
      "causas_fallo": [
        {"causa": "USUARIO AGRESIVO", "ordenes": 12, "porcentaje": "25.0%"}
      ],
      "historial_nic": {
        "total_visitas": 3,
        "efectivas": 2,
        "fallidas": 1,
        "perdidas": 0,
        "efectividad": "66.7%",
        "ultima_visita": "2026-08-21"
      }
    }
  ],
  "no_encontrados": ["9999999"]
}
```

---

### Diferencias con el endpoint individual

| | Individual | Lote |
|---|---|---|
| Método | `GET /recomendar/{nic}` | `POST /recomendar` |
| Entrada | Un NIC en la ruta | Lista de NICs en el body |
| NIC no encontrado | Responde 404 | Lo agrega a `no_encontrados` |
| Límite | 1 NIC | Sin límite |
