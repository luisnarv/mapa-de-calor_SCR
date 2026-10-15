// Registro de resultados de un bot: cada caso pasa o falla, con el detalle de lo que difiere.
import fs from "node:fs";
import path from "node:path";

export class Informe {
  constructor(bot) {
    this.bot = bot;
    this.casos = [];
    this.inicio = Date.now();
  }

  registrar(caso, diferencias, extra = {}) {
    const ok = diferencias.length === 0;
    this.casos.push({ caso, ok, diferencias, ...extra });
    const marca = ok ? "OK   " : "FALLA";
    console.log(`  [${marca}] ${caso}${ok ? "" : "\n          - " + diferencias.join("\n          - ")}`);
  }

  get resumen() {
    const fallas = this.casos.filter((c) => !c.ok);
    return { bot: this.bot, casos: this.casos.length, ok: this.casos.length - fallas.length, fallas: fallas.length, segundos: Math.round((Date.now() - this.inicio) / 1000) };
  }
}

export function guardar(informes) {
  const dir = path.resolve(import.meta.dirname, "..", "resultados");
  fs.mkdirSync(dir, { recursive: true });
  const fecha = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  fs.writeFileSync(path.join(dir, `informe_${fecha}.json`), JSON.stringify(informes.map((i) => ({ ...i.resumen, detalle: i.casos })), null, 2));
  const lineas = ["# Informe de los bots del mapa de cobros", "", `Fecha: ${new Date().toLocaleString("es-CO")}`, "", "| Bot | Casos | OK | Fallas | Segundos |", "|---|---|---|---|---|"];
  for (const i of informes) {
    const r = i.resumen;
    lineas.push(`| ${r.bot} | ${r.casos} | ${r.ok} | ${r.fallas} | ${r.segundos} |`);
  }
  for (const i of informes) {
    const fallas = i.casos.filter((c) => !c.ok);
    if (!fallas.length) continue;
    lineas.push("", `## Fallas de ${i.bot}`);
    for (const c of fallas) lineas.push("", `**${c.caso}**`, ...c.diferencias.map((d) => `- ${d}`));
  }
  const md = path.join(dir, `informe_${fecha}.md`);
  fs.writeFileSync(md, lineas.join("\n"));
  return md;
}
