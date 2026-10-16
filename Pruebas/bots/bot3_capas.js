// Bot 3 · Casillas de Capas y coherencia.
// Marca solo "Perdidas", solo "Efectivas", solo un tipo de recaudo, ninguna... (con y sin
// otros filtros) y comprueba que:
//   · en todos los paneles solo queda lo que se puede filtrar (cantidades correctas),
//   · las listas y píldoras ofrecen únicamente valores con datos (y se apagan si no hay),
//   · las casillas se apagan cuando no hay nada de ese tipo y el submenú solo trae tipos
//     de recaudo que existen,
//   · "Limpiar" devuelve todo al inicio.
import { aplicar, compararOpciones, compararPaneles, nombreCaso } from "./lib/app.js";
import { Informe } from "./lib/informe.js";

export async function bot3({ bot, oraculo, rapido = false }) {
  const inf = new Informe("Bot 3 · casillas de Capas y coherencia");
  console.log(`\n${inf.bot}`);
  const tiposRecaudo = oraculo.dim.resultados.filter((n) => n !== "No efectiva");
  const escenarios = [
    { perdidas: true, efectivas: false },
    { perdidas: false, efectivas: true },
    { ocultas: [tiposRecaudo[1]] }, // sin «Efectiva sin recaudo»
    { ocultas: [tiposRecaudo[0]] }, // sin «Efectiva con pago»
    { perdidas: false, ocultas: [tiposRecaudo[0]] },
    { perdidas: false, efectivas: false }, // nada marcado: todo debe quedar vacío
  ];
  const extras = rapido ? [{}] : [{}, { zona: "ATLANTICO SUR" }, { plan: "Cobro Persuasivo", res: "Pago total" }];

  for (const extra of extras) {
    for (const esc of escenarios) {
      const f = { ...extra, ...esc };
      // Un filtro que ya no tiene datos con las casillas elegidas no se puede aplicar: se omite.
      if (f.res && !oraculo.opciones({ ...esc })["res"].includes(f.res)) delete f.res;
      const caso = nombreCaso(f);
      try {
        const fallos = await aplicar(bot, oraculo, f);
        const p = await bot("paneles");
        const esp = oraculo.esperado(f);
        const dif = [...fallos, ...compararPaneles(f, esp, oraculo.esperadoGestores(f), p)];
        dif.push(...(await compararOpciones(bot, f, oraculo.opciones(f))));

        // Capas: una casilla se apaga solo si no hay nada de ese tipo (con los filtros de las píldoras).
        const hay = oraculo.disponible(f);
        const capas = await bot("estadoCapas");
        for (const c of capas.principales) {
          const quedan = c.texto === "Perdidas" ? hay.perdidas : hay.efectivas;
          if (c.apagada !== (quedan === 0)) dif.push(`Capas · «${c.texto}»: ${c.apagada ? "apagada" : "activa"} con ${quedan} gestiones disponibles`);
        }
        const esperadosSub = tiposRecaudo.filter((n) => (hay.recaudo[n] || 0) > 0).sort();
        const sub = capas.subRecaudo.map((s) => s.texto).sort();
        if (JSON.stringify(sub) !== JSON.stringify(esperadosSub)) dif.push(`Capas · submenú de efectivas muestra [${sub}] y debería mostrar [${esperadosSub}]`);

        // Casillas vacías = mapa vacío con aviso.
        if (esp.tot === 0 && !p.mapaVacio) dif.push("No hay datos con esta selección pero el mapa no muestra el aviso de vacío");
        // Coherencia: todos los paneles de gestiones deben dar el mismo total.
        const totales = [p.detalle.tot, p.triaje.tot, p.carga.tot, p.jerarquia.tot, p.tendencias.tot, p.rankingPlanes.tot];
        if (new Set(totales).size > 1) dif.push(`Los paneles no coinciden entre sí: ${totales.join(" / ")}`);
        inf.registrar(`${caso}  (esperado ${esp.tot} gestiones)`, dif);
      } catch (e) {
        inf.registrar(caso, [`error del bot: ${e.message}`]);
      }
    }
  }

  // «Limpiar» debe devolver todo al inicio.
  try {
    await bot("restaurarTodo");
    const p = await bot("paneles");
    const capas = await bot("estadoCapas");
    const dif = compararPaneles({}, oraculo.esperado({}), oraculo.esperadoGestores({}), p);
    if (capas.principales.some((c) => !c.marcada)) dif.push("Limpiar no volvió a marcar Perdidas y Efectivas");
    if (capas.subRecaudo.some((s) => !s.marcada)) dif.push("Limpiar no volvió a marcar los tipos de recaudo");
    const est = await bot("estadoPildoras");
    if (Object.values(est).some((x) => x.conValor)) dif.push("Limpiar dejó algún filtro con valor puesto");
    inf.registrar("Limpiar restaura todo", dif);
  } catch (e) {
    inf.registrar("Limpiar restaura todo", [`error del bot: ${e.message}`]);
  }
  return inf;
}
