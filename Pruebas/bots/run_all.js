// Ejecuta los tres bots contra el tablero que esté corriendo y escribe un informe.
//   node run_all.js              -> corrida completa (≈ 20-25 min)
//   node run_all.js --rapido     -> muestra reducida (≈ 5 min)
//   node run_all.js --bot 2      -> solo un bot (1, 2 o 3)
// Variables: BASE_URL (por defecto http://localhost:3001), VER=1 (ver el navegador),
// SEMILLA y COMBINACIONES (bot 2), DATOS_DIR (carpeta de los JSON).
import { abrir } from "./lib/app.js";
import { guardar } from "./lib/informe.js";
import { cargarOraculo } from "./lib/oraculo.js";
import { bot1 } from "./bot1_individuales.js";
import { bot2 } from "./bot2_combinados.js";
import { bot3 } from "./bot3_capas.js";

const args = process.argv.slice(2);
const rapido = args.includes("--rapido");
const soloBot = args.includes("--bot") ? args[args.indexOf("--bot") + 1] : null;

const oraculo = cargarOraculo();
console.log(`Datos: ${oraculo.meses.length} meses · ${oraculo.esperado({}).tot} gestiones del mes en curso · ${rapido ? "modo rápido" : "corrida completa"}`);

const { bot, cerrar } = await abrir();
const informes = [];
try {
  const lista = [["1", bot1], ["2", bot2], ["3", bot3]].filter(([n]) => !soloBot || soloBot === n);
  for (const [, correr] of lista) informes.push(await correr({ bot, oraculo, rapido }));
} finally {
  await cerrar();
}

console.log("\n===== RESUMEN =====");
for (const i of informes) {
  const r = i.resumen;
  console.log(`${r.bot}: ${r.ok}/${r.casos} casos OK${r.fallas ? `  ← ${r.fallas} CON FALLAS` : ""}  (${r.segundos}s)`);
}
console.log(`Informe: ${guardar(informes)}`);
process.exit(informes.some((i) => i.resumen.fallas) ? 1 : 0);
