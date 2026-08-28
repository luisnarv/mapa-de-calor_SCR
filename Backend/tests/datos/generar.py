"""Genera el payload congelado de `tests/datos/` desde los JSON del ETL.

Se corre a mano y solo cuando haga falta: el objetivo del recorte es NO cambiar.
Las pruebas afirman cifras exactas, y si los datos se movieran con cada corrida
del ETL diario volveríamos al problema que este archivo existe para resolver.

    .venv/Scripts/python.exe tests/datos/generar.py

Qué recorta: se queda con un puñado de barrios —los que las pruebas nombran, más
los peores por pérdidas para que el ranking siga significando algo— y con las
seis columnas que `payload_store._cargar` realmente lee. El resto del payload
(GPS, NIC, direcciones) no lo toca ninguna métrica y solo pesaría en el repo.
"""

import json
from collections import Counter
from pathlib import Path

ORIGEN = Path(__file__).resolve().parents[2] / "app" / "data"
DESTINO = Path(__file__).resolve().parent
NOMBRADOS = (
    "VILLA SABITA", "LAS MALVINAS", "LOS ROBLES", "EL ROMANCE",
    "CIUDADELA 20 DE JULIO", "REBOLO",
)
# Cuántos barrios más se arrastran por volumen y por pérdidas, para que los
# rankings tengan contra quién comparar.
TOPE_POR_PERDIDAS = 25
TOPE_POR_VOLUMEN = 25

COLUMNAS = ("b", "t", "g", "o", "c", "e", "s", "f", "nic")

# Las actas solo se copian del mes más reciente. Con los ocho meses el recorte
# pasaría de 1 MB a ~10 MB, y las pruebas de búsqueda comparan un término contra
# otro sobre el mismo recorte: un mes alcanza. Los demás quedan como meses sin
# acta, que es un estado que el backend ya sabe reportar.


def cargar_meses(raiz: dict) -> list[tuple[dict, dict]]:
    salida = []
    for mes in sorted(raiz["meta"]["months"], key=lambda m: m["key"]):
        if mes.get("recent"):
            salida.append((mes, raiz["pts"]))
        else:
            with open(ORIGEN / mes["file"], encoding="utf-8") as fh:
                salida.append((mes, json.load(fh)["pts"]))
    return salida


def main() -> None:
    with open(ORIGEN / "data.json", encoding="utf-8") as fh:
        raiz = json.load(fh)

    dim = raiz["dim"]
    barrios = dim["barrios"]
    meses = cargar_meses(raiz)

    # 0 = Efectiva, 2 = Perdida, según `dim.estados`.
    perdidas: Counter[int] = Counter()
    volumen: Counter[int] = Counter()
    efectivas: Counter[int] = Counter()
    for _, pts in meses:
        for b, e in zip(pts["b"], pts["e"]):
            volumen[b] += 1
            if e == 0:
                efectivas[b] += 1
            elif e == 2:
                perdidas[b] += 1

    elegidos = {
        i
        for i, bkey in enumerate(barrios)
        if any(nombre in bkey.upper() for nombre in NOMBRADOS)
    }
    # Un barrio con efectividad 0: hay una prueba que verifica que «el peor por
    # efectividad» es el de menor valor, y sin un 0 real no distingue el orden.
    sin_ninguna_efectiva = [
        b for b, n in volumen.items() if n >= 10 and efectivas[b] == 0
    ]
    elegidos |= set(sin_ninguna_efectiva[:3])
    elegidos |= {b for b, _ in perdidas.most_common(TOPE_POR_PERDIDAS)}
    elegidos |= {b for b, _ in volumen.most_common(TOPE_POR_VOLUMEN)}

    meta = dict(raiz["meta"])
    manifiesto, total = [], 0
    for mes, pts in meses:
        indices = [i for i, b in enumerate(pts["b"]) if b in elegidos]
        recorte = {c: [pts[c][i] for i in indices] for c in COLUMNAS}
        entrada = {k: v for k, v in mes.items() if k in ("key", "label", "recent", "file")}
        entrada["n"] = len(indices)
        total += len(indices)

        if mes.get("recent"):
            raiz_nueva_pts = recorte
            nombre = f"observaciones_{mes['key']}.json"
            actas = json.loads((ORIGEN / nombre).read_text(encoding="utf-8"))["obs"]
            # Alineadas por posición con `pts`: se recortan con los mismos índices.
            (DESTINO / nombre).write_text(
                json.dumps({"obs": [actas[i] for i in indices]}, separators=(",", ":")),
                encoding="utf-8")
        else:
            with open(DESTINO / mes["file"], "w", encoding="utf-8") as fh:
                json.dump({"pts": recorte}, fh, separators=(",", ":"))
        manifiesto.append(entrada)

    meta["months"] = manifiesto
    meta["total_all"] = total
    meta["total"] = manifiesto[-1]["n"]

    with open(DESTINO / "data.json", "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "dim": dim, "pts": raiz_nueva_pts}, fh, separators=(",", ":"))

    print(f"{len(elegidos)} barrios, {total} órdenes, generado {meta['generated']}")
    for i in sorted(elegidos)[:8]:
        print(f"   {barrios[i]}")
    print("   ...")


if __name__ == "__main__":
    main()
