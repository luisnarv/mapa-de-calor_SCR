// Maneja el tablero real en un navegador (Chrome o Edge instalados) y compara cada panel
// con el oráculo. Es lo común a los tres bots.
import fs from "node:fs";
import path from "node:path";

import { chromium } from "playwright-core";

import { FILTROS, PILDORA } from "./oraculo.js";

const codigoPagina = fs.readFileSync(path.join(import.meta.dirname, "enPagina.js"), "utf-8");

export async function abrir({ base = process.env.BASE_URL || "http://localhost:3001", visible = process.env.VER === "1" } = {}) {
  let navegador = null;
  for (const channel of ["chrome", "msedge"]) {
    try {
      navegador = await chromium.launch({ channel, headless: !visible });
      break;
    } catch {
      /* se prueba con el siguiente */
    }
  }
  if (!navegador) throw new Error("No encontré Chrome ni Edge instalados. Instala uno de los dos o ajusta lib/app.js.");
  const contexto = await navegador.newContext({ viewport: { width: 1500, height: 950 } });
  const page = await contexto.newPage();
  await page.goto(`${base}/cobros`, { waitUntil: "domcontentloaded", timeout: 90000 });
  // La caché de meses dura una hora: sin borrarla se podrían comparar datos viejos.
  await page.evaluate(
    () => new Promise((r) => { const q = indexedDB.deleteDatabase("dashboard-cache"); q.onsuccess = q.onerror = q.onblocked = () => r(); })
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("#top .hd-pill", { timeout: 90000 });
  await page.waitForSelector(".rail-btn");
  await page.waitForTimeout(3500);
  await page.evaluate(codigoPagina);
  const bot = (nombre, ...args) => page.evaluate(([n, a]) => window.__bot[n](...a), [nombre, args]);
  return { page, bot, cerrar: () => navegador.close() };
}

/** Lleva el tablero al estado `f`: meses, casillas de Capas y filtros, en ese orden. */
export async function aplicar(bot, meta, f) {
  await bot("restaurarTodo");
  if (f.meses?.length) {
    const etiquetas = meta.meses.filter((m) => f.meses.includes(m.clave)).map((m) => m.etiqueta);
    await bot("setMeses", etiquetas);
  }
  if (f.perdidas === false || f.efectivas === false || f.ocultas?.length) {
    await bot("casillas", { perdidas: f.perdidas !== false, efectivas: f.efectivas !== false, ocultas: f.ocultas || [] });
  }
  const fallos = [];
  for (const k of FILTROS) {
    if (!f[k]) continue;
    const ok = await bot("elegir", PILDORA[k], f[k]);
    if (!ok) fallos.push(`no pude elegir ${PILDORA[k]} = ${f[k]}`);
  }
  return fallos;
}

export const nombreCaso = (f) =>
  [
    f.meses?.length ? `meses ${f.meses.join("+")}` : null,
    ...FILTROS.filter((k) => f[k]).map((k) => `${PILDORA[k]}=${f[k]}`),
    f.perdidas === false ? "sin perdidas" : null,
    f.efectivas === false ? "sin efectivas" : null,
    f.ocultas?.length ? `sin ${f.ocultas.join(" ni ")}` : null,
  ].filter(Boolean).join(" · ") || "sin filtros";

/** Compara los paneles leídos con el oráculo y devuelve las diferencias. */
export function compararPaneles(f, esp, gest, p) {
  const d = [];
  const igual = (vista, obtenido, esperado, tol = 0) => {
    if (Math.abs(obtenido - esperado) > tol) d.push(`${vista}: muestra ${obtenido}, esperado ${esperado}`);
  };
  igual("Detalle · total", p.detalle.tot, esp.tot);
  igual("Detalle · efectivas", p.detalle.ef, esp.ef);
  igual("Detalle · perdidas", p.detalle.pe, esp.pe);
  // Triaje solo dibuja los 400 barrios con más riesgo; con más, la suma de la lista queda por debajo del total.
  if (esp.nBarrios > 400) igual("Triaje · barrios (tope de la lista)", p.triaje.barrios, 400);
  else {
    igual("Triaje · gestiones", p.triaje.tot, esp.tot);
    igual("Triaje · barrios", p.triaje.barrios, esp.nBarrios);
  }
  igual("Carga · gestiones", p.carga.tot, esp.tot);
  igual("Carga · barrios", p.carga.barrios, esp.nBarrios);
  igual("Jerarquía · gestiones", p.jerarquia.tot, esp.tot);
  // Tendencias necesita al menos dos días para dibujar la evolución; con uno solo avisa y no grafica.
  igual("Tendencias · gestiones", p.tendencias.tot, esp.dias >= 2 ? esp.tot : 0);
  igual("Causas · no efectivas", p.causas.perdidas, esp.pe);
  igual("Ranking · planes · gestiones", p.rankingPlanes.tot, esp.tot);
  igual("Ranking · planes · con pago", p.rankingPlanes.conPago, esp.conPago);
  // Tabla por gestor: «Gestión» (tipo) no aplica a la vista; si está puesto se omite la comparación.
  if (!f.tipo) {
    igual("Ranking · gestores (filas)", p.rankingGestores.filas, gest.gestores);
    igual("Ranking · cuentas efectivas", p.rankingGestores.efectivas, gest.efectivas);
    igual("Ranking · cuentas con pago", p.rankingGestores.conPago, gest.conPago);
    igual("Ranking · deuda ($)", p.rankingGestores.deuda, gest.deuda, 2);
    igual("Ranking · recaudo ($)", p.rankingGestores.recaudo, gest.recaudo, 2);
  }
  return d;
}

/** Las listas de cada filtro deben ofrecer exactamente lo que existe en los datos. */
export async function compararOpciones(bot, f, ops) {
  const d = [];
  for (const k of FILTROS) {
    const ui = await bot("opciones", PILDORA[k]);
    const esp = ops[k];
    const puesto = !!f[k];
    const debeEstarApagada = !puesto && esp.length <= 1;
    if (ui.apagada !== debeEstarApagada) d.push(`${PILDORA[k]}: ${ui.apagada ? "está apagada" : "está activa"} y debería ${debeEstarApagada ? "estar apagada" : "estar activa"} (${esp.length} valores posibles)`);
    if (!ui.apagada) {
      const sobran = ui.valores.filter((v) => !esp.includes(v));
      const faltan = esp.filter((v) => !ui.valores.includes(v));
      if (sobran.length) d.push(`${PILDORA[k]}: ofrece valores sin datos: ${sobran.join(", ")}`);
      if (faltan.length) d.push(`${PILDORA[k]}: no ofrece valores que sí tienen datos: ${faltan.join(", ")}`);
    }
  }
  return d;
}
