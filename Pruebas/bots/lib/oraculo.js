// Oráculo: calcula lo que el tablero DEBE mostrar leyendo directamente los JSON que lo
// alimentan (data.json, data_YYYY-MM.json y gestores.json). No usa nada del código del
// tablero: si los dos coinciden es porque el tablero filtra bien, no porque comparta la
// misma lógica.
import fs from "node:fs";
import path from "node:path";

const RAIZ = path.resolve(import.meta.dirname, "..", "..", "..");
const DIR = process.env.DATOS_DIR || path.join(RAIZ, "Frontend", "dashboard", "public", "cobros");

const leer = (f) => JSON.parse(fs.readFileSync(path.join(DIR, f), "utf-8"));
const sinTildes = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();

export const FILTROS = ["zona", "muni", "plan", "tipo", "res", "cob"];
export const PILDORA = { zona: "Zona", muni: "Municipio", plan: "Plan", tipo: "Gestión", res: "Resultado gestión", cob: "Resultado cobranza" };

export function cargarOraculo() {
  const base = leer("data.json");
  const dim = base.dim;
  const meses = base.meta.months.map((m) => ({ clave: m.key, etiqueta: m.label, archivo: m.file, reciente: !!m.recent }));
  const cache = new Map();
  const ptsDe = (m) => {
    if (m.reciente) return base.pts;
    if (!cache.has(m.clave)) cache.set(m.clave, leer(m.archivo).pts);
    return cache.get(m.clave);
  };
  const gestores = leer("gestores.json");

  const valor = {
    zona: (p, i) => dim.zonas[dim.b_zona[p.b[i]]],
    muni: (p, i) => dim.munis[dim.b_muni[p.b[i]]],
    plan: (p, i) => dim.brigs[p.g[i]],
    tipo: (p, i) => dim.tipos[p.o[i]],
    res: (p, i) => dim.gests[p.x[i]],
    cob: (p, i) => dim.cobranza[p.cb[i]],
  };

  // Un punto pasa si coincide con los filtros (salvo `excepto`) y con las casillas de Capas.
  function pasa(p, i, f, excepto = null, ignorarCasillas = false) {
    for (const k of FILTROS) {
      if (k === excepto || !f[k]) continue;
      if (valor[k](p, i) !== f[k]) return false;
    }
    if (ignorarCasillas) return true;
    const e = p.e[i];
    if (e === 2 && f.perdidas === false) return false;
    if (e === 0 && f.efectivas === false) return false;
    if (e === 0 && f.ocultas?.length && f.ocultas.includes(dim.resultados[p.rg[i]])) return false;
    return true;
  }

  const mesesDe = (f) => meses.filter((m) => (f.meses?.length ? f.meses.includes(m.clave) : m.reciente));

  /** Totales que deben verse en los paneles basados en gestiones. */
  function esperado(f) {
    const r = { tot: 0, ef: 0, pe: 0, conPago: 0, planes: {}, barrios: new Set(), gestores: new Set(), dias: new Set() };
    for (const m of mesesDe(f)) {
      const p = ptsDe(m);
      for (let i = 0; i < p.e.length; i++) {
        if (!pasa(p, i, f)) continue;
        r.tot++;
        if (p.e[i] === 0) r.ef++;
        else r.pe++;
        if (dim.resultados[p.rg[i]] === "Efectiva con pago") r.conPago++;
        const plan = dim.brigs[p.g[i]];
        r.planes[plan] = (r.planes[plan] || 0) + 1;
        r.barrios.add(p.b[i]);
        r.gestores.add(p.t[i]);
        r.dias.add(`${m.clave}/${Math.floor(p.m[i] / 1440)}`);
      }
    }
    return { tot: r.tot, ef: r.ef, pe: r.pe, conPago: r.conPago, planes: r.planes, nBarrios: r.barrios.size, nGestores: r.gestores.size, dias: r.dias.size };
  }

  /** Valores que cada filtro debe ofrecer, dados los demás y las casillas. */
  function opciones(f) {
    const salida = {};
    for (const k of FILTROS) {
      const s = new Set();
      for (const m of mesesDe(f)) {
        const p = ptsDe(m);
        for (let i = 0; i < p.e.length; i++) if (pasa(p, i, f, k)) s.add(valor[k](p, i));
      }
      salida[k] = [...s].sort();
    }
    return salida;
  }

  /** Lo que existe con los filtros de las píldoras, sin mirar las casillas (para apagarlas). */
  function disponible(f) {
    const r = { perdidas: 0, efectivas: 0, recaudo: {} };
    for (const m of mesesDe(f)) {
      const p = ptsDe(m);
      for (let i = 0; i < p.e.length; i++) {
        if (!pasa(p, i, f, null, true)) continue;
        if (p.e[i] === 2) r.perdidas++;
        else {
          r.efectivas++;
          const n = dim.resultados[p.rg[i]];
          r.recaudo[n] = (r.recaudo[n] || 0) + 1;
        }
      }
    }
    return r;
  }

  /** Tabla por gestor, desde la agregación de v_aire_cobros_nic_mes (gestores.json). */
  function esperadoGestores(f) {
    const G = gestores;
    const idx = (lista, v) => (v ? lista.findIndex((x) => sinTildes(x) === sinTildes(v)) : -1);
    const iz = idx(G.zonas, f.zona), im = idx(G.municipios, f.muni), ip = idx(G.planes, f.plan), ie = -1; // la vista de gestores no trae el resultado reportado: el filtro no le aplica
    if ((f.zona && iz < 0) || (f.muni && im < 0) || (f.plan && ip < 0)) {
      return { gestores: 0, efectivas: 0, conPago: 0, deuda: 0, recaudo: 0 };
    }
    const sel = new Set(mesesDe(f).map((m) => G.meses.indexOf(m.clave)));
    const iPerdida = G.estados.indexOf("Visita fallida");
    const idCon = G.resultados.indexOf("Efectiva con pago"), idSin = G.resultados.indexOf("Efectiva sin recaudo");
    const ocultas = new Set((f.ocultas || []).map((n) => G.resultados.indexOf(n)));
    const por = new Map();
    for (const [g, m, z, mu, pl, e, r, cuentas, , recaudo, cobroHoy] of G.filas) {
      if (!sel.has(m)) continue;
      if (iz >= 0 && z !== iz) continue;
      if (im >= 0 && mu !== im) continue;
      if (ip >= 0 && pl !== ip) continue;
      if (ie >= 0 && e !== ie) continue;
      if (e === iPerdida ? f.perdidas === false : f.efectivas === false) continue;
      if (e !== iPerdida && ocultas.has(r)) continue;
      const x = por.get(g) || { ef: 0, pago: 0, deuda: 0, rec: 0 };
      if (r === idCon || r === idSin) { x.ef += cuentas; x.deuda += cobroHoy; x.rec += recaudo; }
      if (r === idCon) x.pago += cuentas;
      por.set(g, x);
    }
    let ef = 0, pago = 0, deuda = 0, rec = 0;
    for (const x of por.values()) { ef += x.ef; pago += x.pago; deuda += x.deuda; rec += x.rec; }
    return { gestores: por.size, efectivas: ef, conPago: pago, deuda: Math.round(deuda), recaudo: Math.round(rec) };
  }

  return { dim, meses, esperado, opciones, disponible, esperadoGestores };
}
