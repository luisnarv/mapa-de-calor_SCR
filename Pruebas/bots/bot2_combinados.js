// Bot 2 · Filtros combinados.
// Arma combinaciones al azar (pero repetibles, con semilla) de 2 a 4 filtros, a veces con
// varios meses y a veces con las casillas de Capas, y comprueba cantidades en todos los
// paneles y en la tabla por gestor. Cada valor se escoge entre los que todavía tienen datos
// con lo ya elegido, para que las combinaciones sean realistas y no queden vacías.
import { aplicar, compararOpciones, compararPaneles, nombreCaso } from "./lib/app.js";
import { Informe } from "./lib/informe.js";
import { FILTROS } from "./lib/oraculo.js";

// Generador pseudoaleatorio con semilla: la misma semilla repite las mismas combinaciones.
function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export async function bot2({ bot, oraculo, rapido = false, semilla = Number(process.env.SEMILLA || 20261015), cuantos = Number(process.env.COMBINACIONES || 0) }) {
  const inf = new Informe("Bot 2 · filtros combinados");
  console.log(`\n${inf.bot} (semilla ${semilla})`);
  const azar = mulberry32(semilla);
  const elige = (arr) => arr[Math.floor(azar() * arr.length)];
  const n = cuantos || (rapido ? 4 : 16);

  for (let c = 0; c < n; c++) {
    const f = {};
    // Meses: 1 a 3 de los disponibles (el mes en curso siempre es una opción).
    if (azar() < 0.4) {
      const claves = oraculo.meses.map((m) => m.clave);
      const k = 1 + Math.floor(azar() * 3);
      f.meses = [...claves].sort(() => azar() - 0.5).slice(0, k).sort();
    }
    // Casillas de Capas: solo en algunas combinaciones.
    const r = azar();
    if (r < 0.15) f.efectivas = false;
    else if (r < 0.3) f.perdidas = false;
    // Entre 2 y 4 filtros, cada uno con un valor que aún tenga datos.
    const cuantosFiltros = 2 + Math.floor(azar() * 3);
    for (const k of [...FILTROS].sort(() => azar() - 0.5).slice(0, cuantosFiltros)) {
      const validos = oraculo.opciones(f)[k];
      // Con un solo valor posible la píldora se apaga a propósito: no hay nada que elegir.
      if (validos.length > 1) f[k] = elige(validos);
    }
    // Los filtros elegidos después pueden dejar a uno anterior con un solo valor (píldora apagada): se quita.
    for (const k of FILTROS) if (f[k] && oraculo.opciones({ ...f, [k]: undefined })[k].length <= 1) delete f[k];
    const caso = nombreCaso(f);
    try {
      const fallos = await aplicar(bot, oraculo, f);
      const p = await bot("paneles");
      const esp = oraculo.esperado(f);
      const dif = [...fallos, ...compararPaneles(f, esp, oraculo.esperadoGestores(f), p)];
      dif.push(...(await compararOpciones(bot, f, oraculo.opciones(f))));
      inf.registrar(`#${c + 1} ${caso}  (esperado ${esp.tot} gestiones)`, dif);
    } catch (e) {
      inf.registrar(`#${c + 1} ${caso}`, [`error del bot: ${e.message}`]);
    }
  }
  return inf;
}
