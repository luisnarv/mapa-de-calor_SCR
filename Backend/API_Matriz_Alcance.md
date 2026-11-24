# API Matriz de Alcance: guía de consumo

El servicio devuelve, por cada NIC, el porcentaje de probabilidad de que la orden de SCR salga
efectiva con el alcance recomendado (`pct_recomendado`, de 0 a 100).

## Datos generales

| Dato | Valor |
| --- | --- |
| URL base | `http://52.88.48.137` |
| Autenticación | Ninguna |
| Formato | JSON (`Content-Type: application/json`) |
| Máximo por petición | 5.000 NICs |
| Tiempo de respuesta | 1 a 2 segundos |
| Documentación interactiva | `http://52.88.48.137/docs` |

| Método | Ruta | Para qué |
| --- | --- | --- |
| POST | `/api/v1/matriz-alcance` | Varios NICs a la vez |
| GET | `/api/v1/matriz-alcance/{nic}` | Un solo NIC |

Los porcentajes se actualizan una vez al día, en la madrugada.

## POST: varios NICs

`POST /api/v1/matriz-alcance`

Cuerpo de la petición: una lista `nics` de 1 a 5.000 NICs. Se pueden mandar como texto o como
número; los espacios y los repetidos se ignoran.

```json
{
  "nics": ["7913631", "1072118", "123"]
}
```

Respuesta `200`:

```json
{
  "solicitados": 3,
  "encontrados": 2,
  "no_encontrados": ["123"],
  "resultados": [
    {"nic": "7913631", "pct_recomendado": 98.5},
    {"nic": "1072118", "pct_recomendado": null}
  ]
}
```

| Campo | Tipo | Qué es |
| --- | --- | --- |
| `solicitados` | número | NICs distintos que se pidieron |
| `encontrados` | número | NICs que vinieron en `resultados` |
| `no_encontrados` | lista de texto | NICs sin gestiones de SCR: no tienen porcentaje |
| `resultados` | lista | Un elemento por NIC encontrado, en el mismo orden en que se pidió |
| `resultados[].nic` | texto | El NIC |
| `resultados[].pct_recomendado` | número o `null` | % de efectividad con el alcance recomendado, con un decimal |

Cuando `pct_recomendado` viene en `null`, el NIC no tiene recomendación; por ejemplo, porque pagó
después de su última suspensión.

Con más de 5.000 NICs hay que partir la lista y hacer varias peticiones.

## GET: un NIC

`GET /api/v1/matriz-alcance/{nic}`

El NIC va en la ruta y solo puede tener dígitos.

```
GET http://52.88.48.137/api/v1/matriz-alcance/7913631
```

Respuesta `200`:

```json
{"nic": "7913631", "pct_recomendado": 98.5}
```

Los campos significan lo mismo que en el POST. Si el NIC no tiene gestiones de SCR, responde `404`
(ver [Errores](#errores)).

## Errores

| Código | Cuándo | Qué hacer |
| --- | --- | --- |
| `404` | GET de un NIC sin gestiones de SCR | Nada: ese NIC no tiene porcentaje |
| `422` | Lista vacía, más de 5.000 NICs o un NIC con algo distinto de dígitos | Corregir la petición; `msg` dice cuál es el problema |

En el POST, un NIC sin gestiones no da error: sale en `no_encontrados`.

`404`:

```json
{"detail": "El NIC 123 no está en dbanalitica.recomendacion_aire_scr: no tiene gestiones de SCR que recomendar."}
```

`422` (un NIC inválido):

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "nics"],
      "msg": "Value error, NIC inválido: '12a'. Debe tener solo dígitos."
    }
  ]
}
```

## Ejemplos

### curl

```bash
# Varios NICs
curl -X POST http://52.88.48.137/api/v1/matriz-alcance \
  -H "Content-Type: application/json" \
  -d '{"nics": ["7913631", "1072118", "123"]}'

# Un NIC
curl http://52.88.48.137/api/v1/matriz-alcance/7913631
```

### Python

Para listas de más de 5.000 NICs, este ejemplo las parte en bloques.

```python
import requests

URL = "http://52.88.48.137/api/v1/matriz-alcance"

def consultar(nics: list[str]) -> dict[str, float | None]:
    pct: dict[str, float | None] = {}
    for i in range(0, len(nics), 5000):
        r = requests.post(URL, json={"nics": nics[i:i + 5000]}, timeout=60)
        r.raise_for_status()
        for fila in r.json()["resultados"]:
            pct[fila["nic"]] = fila["pct_recomendado"]
    return pct

print(consultar(["7913631", "1072118", "123"]))
# {'7913631': 98.5, '1072118': None}

# Un NIC
r = requests.get(f"{URL}/7913631", timeout=30)
if r.status_code == 404:
    print("Sin gestiones de SCR")
else:
    print(r.json()["pct_recomendado"])
```

### JavaScript

```javascript
const URL = "http://52.88.48.137/api/v1/matriz-alcance";

// Varios NICs
const r = await fetch(URL, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ nics: ["7913631", "1072118", "123"] }),
});
const datos = await r.json();
for (const fila of datos.resultados) {
  console.log(fila.nic, fila.pct_recomendado);
}

// Un NIC
const uno = await fetch(`${URL}/7913631`);
if (uno.ok) console.log((await uno.json()).pct_recomendado);
```

### PowerShell

```powershell
$url = "http://52.88.48.137/api/v1/matriz-alcance"

# Varios NICs
$cuerpo = @{ nics = @("7913631", "1072118", "123") } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri $url -ContentType "application/json" -Body $cuerpo
$r.resultados | Format-Table nic, pct_recomendado

# Un NIC
(Invoke-RestMethod -Uri "$url/7913631").pct_recomendado
```
