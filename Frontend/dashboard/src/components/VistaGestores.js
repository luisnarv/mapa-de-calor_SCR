"use client";

import React, { useEffect, useMemo, useState } from "react";

const MESES = [
  "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
  "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
];

// "Octubre de 2026" -> "2026-10". Los filtros del tablero guardan la etiqueta.
const claveDeEtiqueta = (etiqueta) => {
  const [mes, , anio] = etiqueta.split(" ");
  const i = MESES.indexOf(mes);
  return i < 0 ? null : `${anio}-${String(i + 1).padStart(2, "0")}`;
};

const etiquetaCorta = (clave) => {
  const [anio, mes] = clave.split("-");
  return `${MESES[+mes - 1].slice(0, 3)} ${anio}`;
};

const mesAnterior = (clave) => {
  const [anio, mes] = clave.split("-").map(Number);
  return mes === 1 ? `${anio - 1}-12` : `${anio}-${String(mes - 1).padStart(2, "0")}`;
};

// La base trae "NOMBRE APELLIDO - 1002031754": la cédula sobra en una tabla de lectura.
const nombreDe = (gestor) => gestor.replace(/\s*-\s*\d+\s*$/, "").trim();

const METRICAS = [
  { id: "recaudo", texto: "Recaudo" },
  { id: "deuda", texto: "Deuda" },
  { id: "cuentas", texto: "Cuentas efectivas" },
  { id: "efectividad", texto: "% Efectividad" },
];

// El archivo es el mismo para todas las visitas a la vista: se pide una sola vez.
let cache = null;

// `ranking` la usa el menú Ranking: misma tabla, con posición, cuentas con pago y
// clic por gestor; en lugar del gráfico muestra `lateral`.
const MUESTRA_MINIMA = 30;

export default function VistaGestores({ proceso, meses, palette: P, ranking = false, lateral = null, onPick, seleccion = null }) {
  const [datos, setDatos] = useState(cache);
  const [error, setError] = useState(false);
  const [orden, setOrden] = useState({ col: ranking ? "pctPago" : "recaudo", asc: false });
  const [metrica, setMetrica] = useState("recaudo");

  useEffect(() => {
    if (cache) return;
    fetch(`/${proceso}/gestores.json`)
      .then((r) => r.json())
      .then((j) => {
        cache = j;
        setDatos(j);
      })
      .catch(() => setError(true));
  }, [proceso]);

  const vista = useMemo(() => {
    if (!datos) return null;
    const claves = new Set(datos.meses);
    const sel = (meses || []).map(claveDeEtiqueta).filter((k) => k && claves.has(k));
    const elegidos = sel.length ? sel : datos.meses;
    const actual = [...elegidos].sort().at(-1);
    const anterior = mesAnterior(actual);
    const iElegidos = new Set(elegidos.map((k) => datos.meses.indexOf(k)));
    const iActual = datos.meses.indexOf(actual);
    const iAnterior = datos.meses.indexOf(anterior);

    const porGestor = new Map();
    const fila = (g) => {
      let o = porGestor.get(g);
      if (!o) {
        o = { g, cuentas: 0, deuda: 0, recaudo: 0, pago: 0, gestionadas: 0, efAct: null, efAnt: null };
        porGestor.set(g, o);
      }
      return o;
    };
    for (const [g, m, cuentas, ef, deuda, recaudo, pago] of datos.filas) {
      if (iElegidos.has(m)) {
        const o = fila(g);
        o.cuentas += ef;
        o.deuda += deuda;
        o.recaudo += recaudo;
        o.pago += pago;
        o.gestionadas += cuentas;
      }
      if (m === iActual) fila(g).efAct = cuentas ? (ef / cuentas) * 100 : null;
      if (m === iAnterior) fila(g).efAnt = cuentas ? (ef / cuentas) * 100 : null;
    }
    // Un gestor sin nada en los meses elegidos no aporta fila.
    const filas = [...porGestor.values()]
      .filter((o) => o.cuentas > 0 || o.recaudo > 0 || o.efAct != null)
      .map((o) => ({
        ...o,
        completo: datos.gestores[o.g],
        nombre: nombreDe(datos.gestores[o.g]),
        pctPago: o.gestionadas ? (o.pago / o.gestionadas) * 100 : null,
      }));

    const prom = (k) => {
      const v = filas.map((o) => o[k]).filter((x) => x != null);
      return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    };
    const total = {
      cuentas: filas.reduce((a, o) => a + o.cuentas, 0),
      deuda: filas.reduce((a, o) => a + o.deuda, 0),
      recaudo: filas.reduce((a, o) => a + o.recaudo, 0),
      pago: filas.reduce((a, o) => a + o.pago, 0),
      gestionadas: filas.reduce((a, o) => a + o.gestionadas, 0),
      efAnt: prom("efAnt"),
      efAct: prom("efAct"),
    };
    return { filas, total, actual, anterior };
  }, [datos, meses]);

  if (error) return <p className="empty">No se pudo cargar la vista por gestor.</p>;
  if (!vista) return <p className="empty">Cargando…</p>;

  const num = (v) => Math.round(v).toLocaleString("es-CO");
  const dinero = (v) => "$" + Math.round(v).toLocaleString("es-CO");
  const pct = (v) => (v == null ? "—" : v.toFixed(1).replace(".", ",") + "%");

  // En el ranking un % sobre pocas cuentas no vale: los de muestra corta van al fondo.
  const valor = (o) =>
    ranking && orden.col === "pctPago" && o.gestionadas < MUESTRA_MINIMA ? -1 : o[orden.col] ?? -1;
  const filas = [...vista.filas].sort((a, b) => {
    const x = orden.col === "nombre" ? a.nombre : valor(a);
    const y = orden.col === "nombre" ? b.nombre : valor(b);
    const c = typeof x === "string" ? x.localeCompare(y, "es") : x - y;
    return orden.asc ? c : -c;
  });

  const cabecera = (col, texto, num_ = true) => (
    <th
      className={num_ ? "num" : ""}
      style={{ cursor: "pointer", whiteSpace: "nowrap" }}
      onClick={() => setOrden((o) => ({ col, asc: o.col === col ? !o.asc : false }))}
    >
      {texto}
      {orden.col === col ? (orden.asc ? " ↑" : " ↓") : ""}
    </th>
  );

  // Gráfico: los 12 primeros según la métrica elegida.
  const valorDe = (o) => (metrica === "efectividad" ? o.efAct ?? 0 : o[metrica] ?? 0);
  const top = [...vista.filas].sort((a, b) => valorDe(b) - valorDe(a)).slice(0, 12);
  const maximo = Math.max(...top.map(valorDe), 1);
  const formato = metrica === "recaudo" || metrica === "deuda" ? dinero : metrica === "cuentas" ? num : pct;

  return (
    <div className="gv">
      <div className="gv-t">
        <table className="mt">
          <thead>
            <tr>
              {ranking && <th className="num">#</th>}
              {cabecera("nombre", "Gestor", false)}
              {cabecera("cuentas", "Cuentas con gestión efectiva")}
              {cabecera("deuda", "Deuda")}
              {cabecera("recaudo", "Recaudo")}
              {cabecera("efAnt", `% Efect. ${etiquetaCorta(vista.anterior)}`)}
              {cabecera("efAct", `% Efect. ${etiquetaCorta(vista.actual)}`)}
              {ranking && cabecera("pago", "Con pago")}
              {ranking && cabecera("pctPago", "% con pago")}
            </tr>
          </thead>
          <tbody>
            {filas.map((o, i) => (
              <tr
                key={o.g}
                className={ranking ? `rk-fila${seleccion === o.completo ? " sel" : ""}` : undefined}
                onClick={ranking && onPick ? () => onPick(o.completo) : undefined}
                title={ranking ? "Ver solo las gestiones de este gestor en el mapa" : undefined}
              >
                {ranking && <td className="num">{i + 1}</td>}
                <td>{o.nombre}</td>
                <td className="num">{num(o.cuentas)}</td>
                <td className="num">{dinero(o.deuda)}</td>
                <td className="num">{dinero(o.recaudo)}</td>
                <td className="num">{pct(o.efAnt)}</td>
                <td className="num">{pct(o.efAct)}</td>
                {ranking && <td className="num">{num(o.pago)}</td>}
                {ranking && (
                  <td className="num">
                    {pct(o.pctPago)}
                    {o.gestionadas < MUESTRA_MINIMA ? "*" : ""}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="gv-total">
              {ranking && <td></td>}
              <td>Total ({filas.length} gestores)</td>
              <td className="num">{num(vista.total.cuentas)}</td>
              <td className="num">{dinero(vista.total.deuda)}</td>
              <td className="num">{dinero(vista.total.recaudo)}</td>
              <td className="num">{pct(vista.total.efAnt)}</td>
              <td className="num">{pct(vista.total.efAct)}</td>
              {ranking && <td className="num">{num(vista.total.pago)}</td>}
              {ranking && (
                <td className="num">
                  {pct(vista.total.gestionadas ? (vista.total.pago / vista.total.gestionadas) * 100 : null)}
                </td>
              )}
            </tr>
          </tfoot>
        </table>
        {ranking && (
          <p className="gv-nota">
            * Menos de {MUESTRA_MINIMA} cuentas gestionadas: el porcentaje se calcula pero no entra al orden.
            Con pago = cuentas con pago total, abono o acuerdo de pago.
          </p>
        )}
      </div>

      {ranking ? (
        lateral && <div className="gv-c">{lateral}</div>
      ) : (
      <div className="gv-c">
        <div className="gv-ch">
          <b>Top 12 gestores</b>
          <select value={metrica} onChange={(e) => setMetrica(e.target.value)}>
            {METRICAS.map((m) => (
              <option key={m.id} value={m.id}>{m.texto}</option>
            ))}
          </select>
        </div>
        {top.map((o) => (
          <div key={o.g} className="gv-b" title={`${o.nombre}: ${formato(valorDe(o))}`}>
            <span className="gv-n">{o.nombre}</span>
            <i>
              {metrica === "efectividad" && o.efAnt != null && (
                <b style={{ width: `${(o.efAnt / maximo) * 100}%`, background: P.serieRef, height: 4 }}></b>
              )}
              <b style={{ width: `${(valorDe(o) / maximo) * 100}%`, background: P.series[1] }}></b>
            </i>
            <span className="gv-v">{formato(valorDe(o))}</span>
          </div>
        ))}
        {metrica === "efectividad" && (
          <p className="gv-nota">
            Barra gruesa: {etiquetaCorta(vista.actual)} · barra fina: {etiquetaCorta(vista.anterior)}
          </p>
        )}
      </div>
      )}
    </div>
  );
}
