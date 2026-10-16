# API de Recomendación

**Base URL:** `http://52.88.48.137`

---

## 1. Individual — `GET /api/v1/ordenes/recomendar/{nic}`

Recomienda técnicos y brigadas (SCR) o gestores (COBROS) para un NIC según desempeño histórico. En COBROS agrega además los indicadores de facturación y recaudo de `historico_balanza` (ver [IndicadoresCobro](#indicadorescobro)).

### Parámetros

| Parámetro | En | Tipo | Default | Descripción |
|-----------|------|------|---------|-------------|
| `nic` | ruta | `string` | — | Número de identificación del cliente |
| `actividad` | query | `string` | `"scr"` | Actividad: `scr`, `cobros` o `cobro` |

> `cobro` se normaliza a `cobros`. La actividad determina qué datos históricos se consultan y los nombres de los campos en la respuesta.

### Ejemplo SCR

```bash
curl http://52.88.48.137/api/v1/ordenes/recomendar/7502057?actividad=scr
```

### Ejemplo COBROS

```bash
curl http://52.88.48.137/api/v1/ordenes/recomendar/2120682?actividad=cobros
```

### Respuesta 200

→ Ver [esquemas de respuesta](#esquemas-de-respuesta). Los campos cambian según la actividad.

### NIC no encontrado → 404

```json
{"detail": "El NIC 9999999 no aparece en el histórico."}
```

---

## 2. Por lotes — `POST /api/v1/ordenes/recomendar`

Misma lógica Wilson, pero procesa una lista de NICs en una sola petición (sin límite).

### Parámetros

| Parámetro | En | Tipo | Default | Descripción |
|-----------|------|------|---------|-------------|
| `actividad` | query | `string` | `"scr"` | Actividad: `scr`, `cobros` o `cobro` |

### Request body

| Campo | Tipo | Req | Descripción |
|-------|------|-----|-------------|
| `nics` | `string[]` | sí | Lista de NICs a consultar |

### Ejemplo

```bash
curl -X POST "http://52.88.48.137/api/v1/ordenes/recomendar?actividad=cobros" \
  -H "Content-Type: application/json" \
  -d '{"nics": ["2120682", "7502057", "9999999"]}'
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

## Campos según actividad

La respuesta cambia el nombre de los campos según la actividad:

| Campo | SCR (`actividad=scr`) | COBROS (`actividad=cobros`) |
|-------|---|---|
| Quién ejecuta | `tecnicos_recomendados` | `gestores_recomendados` |
| Tipo de grupo | `brigadas_recomendadas` | — (COBROS no recomienda plan) |
| Facturación y recaudo | — | `cobro`, `aviso_cobro` |
| Fecha más reciente | `ultima_orden` | `ultima_gestion` |

Los demás campos (`nic`, `barrio`, `municipio`, `mejor_horario`, `causas_fallo`, `historial_nic`) son iguales en ambas actividades.

---

## Recomendaciones de COBROS

En COBROS cada NIC trae dos tipos de recomendación:

- **Quién gestiona** (`gestores_recomendados`): los gestores con mejor desempeño histórico en el barrio del NIC, igual que los técnicos en SCR.
- **Cómo está la cuenta** (`cobro`): indicadores de facturación y recaudo que ayudan a decidir si vale la pena gestionarla y qué tan buena pagadora es.

COBROS **ya no recomienda plan** (`planes_recomendados` se eliminó de la respuesta).

### De dónde salen los indicadores

Se consultan en el momento, en la tabla `dbanalitica.historico_balanza` (facturación y recaudo, cargada a diario). El NIC se busca como `cuenta`: son el mismo número. Los indicadores aplican al endpoint individual y al de lote; en el lote se resuelven todos los NICs con una sola consulta.

### 1. Cuentas de muy bajo valor — `marca`

Identifica las cuentas cuyo valor es tan bajo que no justifica una gestión ordinaria.

| Valor | Significa |
|-------|-----------|
| `"No gestionar"` | Lo facturado en el último periodo es **menor a $30.000** |
| `"Gestionar"` | Lo facturado en el último periodo es **$30.000 o más** |
| `null` | La cuenta nunca tuvo un valor facturado: no hay cifra sobre la cual decidir |

- **Qué valor se compara:** `facturado_energia` del **último periodo facturado** de la cuenta, que se devuelve en `ultimo_periodo` y `facturado_ultimo_periodo` para que se pueda ver la cifra que decidió.
- **Por qué "último facturado" y no "último periodo":** el mes en curso aparece en la balanza antes de facturarse, con el valor vacío. En ese caso se toma el mes anterior. Por ejemplo, una cuenta sin factura de octubre se evalúa con la de septiembre.
- **Valores en cero o negativos** (saldo a favor) quedan como `"No gestionar"`.
- La marca **no saca la cuenta de la respuesta**: la cuenta sigue trayendo sus gestores recomendados. Quien consume la API decide qué hacer con las que vienen `"No gestionar"`.

### 2. Comportamiento de pago reciente — `pagos_ultimos_6_meses`

Cuántas facturas pagó la cuenta en los **últimos 6 meses cerrados**, de 0 a 6. Mide la frecuencia de pago: 6 es una cuenta que paga todos los meses, 0 una que no ha pagado en medio año.

- **Qué cuenta como pago:** el periodo tiene `con_pago` verdadero en la balanza.
- **Qué meses entran:** los 6 anteriores al mes en curso. Consultando en octubre de 2026 son abril a septiembre. El mes en curso no entra porque todavía se está recaudando; se ve aparte en `recaudo_mes_actual`.

### 3. Recaudo del mes anterior y del mes actual — `recaudo_mes_anterior`, `recaudo_mes_actual`

Si la cuenta tuvo recaudo (`"Sí"`) o no (`"No"`) en el mes inmediatamente anterior y en el mes en curso.

- **Qué cuenta como recaudo:** `recaudo_energia` mayor a 0 en ese periodo.
- **Mes actual** es el mes calendario de la consulta. Como la balanza se carga a diario, un `"No"` a principio de mes puede cambiar a `"Sí"` días después.
- Si la cuenta no tiene fila en la balanza para ese mes, se responde `"No"`.

> `pagos_ultimos_6_meses` usa `con_pago` y los indicadores de recaudo usan `recaudo_energia`. En la balanza no siempre coinciden (hay periodos marcados con pago cuyo recaudo es 0), así que una cuenta puede mostrar un pago en un mes y `"No"` de recaudo en ese mismo mes.

### Cuando no hay indicadores — `aviso_cobro`

Si no se pudieron calcular, `cobro` viene en `null` y `aviso_cobro` explica por qué. La recomendación de gestores **sale igual**: la balanza nunca hace fallar el endpoint.

| `aviso_cobro` | Qué hacer |
|---------------|-----------|
| `"La cuenta no aparece en historico_balanza."` | El NIC tiene gestiones pero no facturación registrada. No es un error de la API |
| `"historico_balanza no respondió; reintenta para ver los indicadores de cobro."` | La base de datos falló o no estaba disponible. Reintentar |

### Cómo leerlos juntos

| Situación | Lectura sugerida |
|-----------|------------------|
| `"No gestionar"` | Valor muy bajo: fuera de la gestión ordinaria, sin importar lo demás |
| `"Gestionar"`, 5–6 pagos, recaudo anterior `"Sí"` | Buena pagadora: probablemente pague sin gestión intensiva |
| `"Gestionar"`, 0–1 pagos, recaudo anterior y actual `"No"` | Morosidad sostenida: candidata prioritaria a gestión |
| `"Gestionar"`, varios pagos pero recaudo anterior `"No"` | Pagadora que se atrasó recién: vale un recordatorio temprano |

Esta tabla es una guía de interpretación: la API entrega los indicadores, no una decisión combinada.

---

## Esquemas de respuesta

### RecomendacionResponse

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `nic` | `string` | NIC consultado |
| `barrio` | `string` | Barrio (BKEY: `"MUNICIPIO \| BARRIO"`) |
| `municipio` | `string` | Municipio |
| `tecnicos_recomendados` / `gestores_recomendados` | `CandidatoRecomendado[]` | Top 4 por Wilson |
| `brigadas_recomendadas` | `CandidatoRecomendado[]` | Top 3 por Wilson. Solo SCR |
| `mejor_horario` | `MejorHorario` | Mejor día de la semana por efectividad |
| `causas_fallo` | `CausaFrecuente[]` | Top 5 causas de fallo en el barrio |
| `historial_nic` | `HistorialNic` | Resumen de visitas históricas al NIC |
| `cobro` | `IndicadoresCobro \| null` | Solo COBROS. Ver [Recomendaciones de COBROS](#recomendaciones-de-cobros) |
| `aviso_cobro` | `string` | Solo COBROS y solo cuando `cobro` es `null`: dice por qué |

### CandidatoRecomendado

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `nombre` | `string` | Nombre del técnico/gestor o brigada |
| `efectividad_ajustada` | `string` | Efectivas / (total − no controlables), e.g. `"87.2%"` |
| `efectivas` | `int` | Órdenes/gestiones efectivas |
| `fallidas` | `int` | Fallidas (se cobran en SCR) |
| `perdidas` | `int` | Perdidas (no se cobran en SCR) |
| `ultima_orden` / `ultima_gestion` | `string \| null` | Fecha más reciente (`YYYY-MM-DD`) |

### IndicadoresCobro

Referencia rápida; la explicación de cada campo está en [Recomendaciones de COBROS](#recomendaciones-de-cobros).

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `marca` | `"Gestionar" \| "No gestionar" \| null` | `"No gestionar"` si `facturado_energia` del último periodo es menor a $30.000; `"Gestionar"` si no. `null` solo si la cuenta nunca tuvo facturado |
| `ultimo_periodo` | `int \| null` | Último periodo con `facturado_energia` no nulo (`YYYYMM`): el mes en curso aparece en la balanza antes de facturarse |
| `facturado_ultimo_periodo` | `float \| null` | `facturado_energia` de ese periodo |
| `pagos_ultimos_6_meses` | `int` | Periodos con `con_pago` en los 6 meses cerrados antes del actual (hoy en octubre → abril a septiembre) |
| `recaudo_mes_anterior` | `"Sí" \| "No"` | `recaudo_energia > 0` en el periodo del mes anterior |
| `recaudo_mes_actual` | `"Sí" \| "No"` | `recaudo_energia > 0` en el periodo del mes en curso |

### MejorHorario

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `mejor_dia` | `string \| null` | Día de la semana con mejor efectividad, o null |
| `franjas` | `FranjaHoraria[]` | Franjas de 2h por efectividad |

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
| `fallidas` | `int` | Visitas fallidas |
| `perdidas` | `int` | Visitas perdidas |
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

## Ejemplo de respuesta SCR (individual)

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

## Ejemplo de respuesta COBROS (individual)

```json
{
  "nic": "2120682",
  "barrio": "BARRANQUILLA | EL PORVENIR",
  "municipio": "BARRANQUILLA",
  "gestores_recomendados": [
    {
      "nombre": "NINO JIMENEZ CHARLES ESTEBAN - 8505934",
      "efectividad_ajustada": "35.7%",
      "efectivas": 5,
      "fallidas": 16,
      "perdidas": 3,
      "ultima_gestion": "2026-09-09"
    },
    {
      "nombre": "LUIS ALFREDO MEZA ARGUELLES - 1143470220",
      "efectividad_ajustada": "26.5%",
      "efectivas": 9,
      "fallidas": 94,
      "perdidas": 0,
      "ultima_gestion": "2026-06-05"
    }
  ],
  "mejor_horario": {
    "mejor_dia": "viernes",
    "franjas": [
      {"franja": "10:00–12:00", "efectividad": "7.1%", "ordenes": 953},
      {"franja": "14:00–16:00", "efectividad": "7.1%", "ordenes": 846}
    ]
  },
  "causas_fallo": [
    {"causa": "Compromiso de pago", "ordenes": 1674, "porcentaje": "55.0%"},
    {"causa": "No es el titular", "ordenes": 994, "porcentaje": "32.7%"}
  ],
  "historial_nic": {
    "total_visitas": 13,
    "efectivas": 0,
    "fallidas": 10,
    "perdidas": 3,
    "efectividad": "0.0%",
    "ultima_visita": "2026-09-05"
  },
  "cobro": {
    "marca": "Gestionar",
    "ultimo_periodo": 202610,
    "facturado_ultimo_periodo": 114269.98,
    "pagos_ultimos_6_meses": 0,
    "recaudo_mes_anterior": "No",
    "recaudo_mes_actual": "No"
  }
}
```

## Ejemplo de respuesta (lote, SCR)

En COBROS cada elemento de `resultados` tiene la misma forma que el ejemplo individual de COBROS.

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
