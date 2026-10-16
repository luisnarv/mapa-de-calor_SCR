// Funciones que se inyectan en la página del tablero para manejarlo como lo haría una
// persona (clics en píldoras, casillas y menús) y leer lo que muestra cada panel.
// Es JavaScript de navegador, no un módulo: define `window.__bot`.
(() => {
  const w = (ms) => new Promise((r) => setTimeout(r, ms));
  const num = (s) => +String(s).replace(/[$.\s]/g, "").replace(",", ".");
  const NORM = (t) => (t || "").trim();

  const pill = (n) => [...document.querySelectorAll("#top .hd-pill")].find((b) => b.innerText.trim().startsWith(n));
  const rail = (n) => [...document.querySelectorAll(".rail-btn")].find((b) => b.innerText.toUpperCase().includes(n.toUpperCase()));
  const cerrar = async () => { document.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })); await w(150); };
  const capas = () => [...document.querySelectorAll("#layers label.lay")];
  const casilla = (t) => capas().find((l) => l.innerText.trim() === t)?.querySelector("input");

  // Si Capas está plegado hay que abrirlo para tocar sus casillas.
  async function abrirCapas() {
    if (document.querySelector("#layers label.lay")) return;
    const b = document.querySelector("#layers > button");
    if (b) { b.click(); await w(300); }
  }

  async function opciones(nombre) {
    await cerrar();
    const p = pill(nombre);
    if (!p || p.disabled) return { apagada: !!p?.disabled, valores: [], titulo: p?.title || null };
    p.click();
    await w(250);
    const v = [...document.querySelectorAll(".hd-pop [role=option]")].slice(1).map((x) => x.innerText.trim());
    await cerrar();
    return { apagada: false, valores: v, titulo: null };
  }

  async function elegir(nombre, texto) {
    await cerrar();
    const p = pill(nombre);
    if (!p) return false;
    if (p.disabled && texto !== null) return false;
    p.click();
    await w(250);
    const ops = [...document.querySelectorAll(".hd-pop [role=option]")];
    const op = texto === null ? ops[0] : ops.find((x) => x.innerText.trim() === texto);
    if (!op) { await cerrar(); return false; }
    op.click();
    await w(900);
    return true;
  }

  async function limpiarPildoras() {
    for (const n of ["Zona", "Municipio", "Plan", "Gestión", "Resultado gestión"]) {
      const p = pill(n);
      if (p && /:/.test(p.innerText)) await elegir(n, null);
    }
  }

  async function restaurarTodo() {
    // Botón «Limpiar» de la barra: vuelve a filtros vacíos, mes en curso y casillas marcadas.
    const l = document.querySelector("#top .hd-clear");
    if (l) { l.click(); await w(1500); }
    await limpiarPildoras();
  }

  async function casillas({ perdidas = true, efectivas = true, ocultas = [] }) {
    await rail("MAPA").click();
    await w(400);
    await abrirCapas();
    const fijar = async (texto, quiere) => {
      const c = casilla(texto);
      if (c && !c.disabled && c.checked !== quiere) { c.click(); await w(500); }
    };
    await fijar("Perdidas", perdidas);
    await fijar("Efectivas", efectivas);
    for (const sub of [...document.querySelectorAll("#layers .lay-sub label")]) {
      const n = sub.innerText.trim();
      const c = sub.querySelector("input");
      if (!c.disabled && c.checked !== !ocultas.includes(n)) { c.click(); await w(400); }
    }
    await w(600);
  }

  async function estadoCapas() {
    await rail("MAPA").click();
    await w(300);
    await abrirCapas();
    const filas = capas().slice(0, 2).map((l) => ({ texto: l.innerText.trim(), marcada: l.querySelector("input").checked, apagada: l.querySelector("input").disabled }));
    const sub = [...document.querySelectorAll("#layers .lay-sub label")].map((l) => ({ texto: l.innerText.trim(), marcada: l.querySelector("input").checked, apagada: l.querySelector("input").disabled }));
    return { principales: filas, subRecaudo: sub };
  }

  async function setMeses(etiquetas) {
    await cerrar();
    const p = pill("Meses") || [...document.querySelectorAll("#top .hd-pill")].find((b) => /Meses/.test(b.innerText));
    p.click();
    await w(350);
    for (const l of [...document.querySelectorAll("#top label")].filter((x) => /\d/.test(x.innerText) && /de|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre/i.test(x.innerText))) {
      const mes = l.innerText.trim().split(/\s+/)[0].toLowerCase();
      const quiere = etiquetas.some((e) => e.toLowerCase().startsWith(mes));
      const c = l.querySelector("input");
      if (c && c.checked !== quiere) { c.click(); await w(250); }
    }
    await cerrar();
    for (let i = 0; i < 40; i++) {
      if (!/Cargando|cargando/.test(document.querySelector("#top")?.innerText || "")) break;
      await w(500);
    }
    await w(1800);
  }

  async function paneles() {
    const o = {};
    const total = (sel) => [...document.querySelectorAll(sel)];
    const suma = (rows, i) => rows.reduce((a, r) => a + num(r.children[i]?.innerText || "0"), 0);

    rail("DETALLE").click(); await w(900);
    const b = total(".details-panel .mini b").map((x) => num(x.innerText));
    o.detalle = { tot: b[0], ef: b[1], pe: b[2] };

    rail("TRIAJE").click(); await w(900);
    const t = total("#spine .sp-row");
    o.triaje = { barrios: t.length, tot: t.reduce((a, r) => { const m = r.innerText.match(/([\d.]+)\s+gesti/); return a + (m ? num(m[1]) : 0); }, 0) };

    rail("CARGA").click(); await w(1100);
    const c = total("#dockBody tbody tr");
    o.carga = { barrios: c.length, tot: suma(c, 2) };

    rail("JERARQUÍA").click(); await w(1100);
    o.jerarquia = { tot: suma(total("#dockBody tbody tr"), 2) };

    rail("TENDENCIAS").click(); await w(900);
    [...document.querySelectorAll(".vt-sw button")].find((x) => x.innerText === "Evolución")?.click();
    await w(700);
    o.tendencias = { tot: total("#dockBody .tb").reduce((a, x) => { const m = x.title.match(/([\d.]+) gestiones/); return a + (m ? num(m[1]) : 0); }, 0) };

    rail("CAUSAS").click(); await w(1100);
    o.causas = { perdidas: num((document.querySelector(".mc-t")?.innerText || "0").split(" ")[0]) };

    rail("RANKING").click(); await w(1800);
    const pl = total(".gv-c tbody tr");
    o.rankingPlanes = { tot: suma(pl, 1), conPago: suma(pl, 4) };
    const tt = document.querySelector(".gv-total");
    o.rankingGestores = tt
      ? { filas: total(".gv-t tbody tr").length, efectivas: num(tt.children[2].innerText), conPago: num(tt.children[7].innerText), deuda: num(tt.children[3].innerText), recaudo: num(tt.children[4].innerText) }
      : { filas: 0, efectivas: 0, conPago: 0, deuda: 0, recaudo: 0, vacio: !!document.querySelector("#dockBody .empty") };

    rail("MAPA").click(); await w(300);
    o.contexto = document.querySelector("#top .hd-ctx")?.innerText || "";
    o.mapaVacio = getComputedStyle(document.getElementById("mapEmpty") || document.body).display !== "none" && !!document.getElementById("mapEmpty");
    return o;
  }

  async function estadoPildoras() {
    const r = {};
    for (const n of ["Zona", "Municipio", "Plan", "Gestión", "Resultado gestión"]) {
      const p = pill(n);
      r[n] = { apagada: !!p?.disabled, conValor: /:/.test(p?.innerText || "") };
    }
    return r;
  }

  window.__bot = { w, pill, opciones, elegir, limpiarPildoras, restaurarTodo, casillas, estadoCapas, setMeses, paneles, estadoPildoras, NORM };
})();
