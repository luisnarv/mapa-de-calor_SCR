// Bot 1 · Filtros individuales.
// Aplica, uno por uno, TODOS los valores que ofrece cada filtro (zona, municipio, plan,
// gestión y resultado gestión) y comprueba que cada panel muestra exactamente la cantidad
// de datos que debe, y que las listas de los demás filtros se ajustan a lo que queda.
import { aplicar, compararOpciones, compararPaneles, nombreCaso } from "./lib/app.js";
import { Informe } from "./lib/informe.js";
import { FILTROS } from "./lib/oraculo.js";

export async function bot1({ bot, oraculo, rapido = false }) {
  const inf = new Informe("Bot 1 · filtros individuales");
  console.log(`\n${inf.bot}`);
  const base = oraculo.opciones({});
  for (const k of FILTROS) {
    const valores = rapido ? base[k].slice(0, 2) : base[k];
    for (const v of valores) {
      const f = { [k]: v };
      const caso = nombreCaso(f);
      try {
        const fallos = await aplicar(bot, oraculo, f);
        const p = await bot("paneles");
        const dif = [...fallos, ...compararPaneles(f, oraculo.esperado(f), oraculo.esperadoGestores(f), p)];
        dif.push(...(await compararOpciones(bot, f, oraculo.opciones(f))));
        inf.registrar(caso, dif, { esperado: oraculo.esperado(f).tot });
      } catch (e) {
        inf.registrar(caso, [`error del bot: ${e.message}`]);
      }
    }
  }
  return inf;
}
