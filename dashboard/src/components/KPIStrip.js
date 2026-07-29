"use client";

import React from "react";

export default function KPIStrip({ A }) {
  const bad = A.tot - A.ef;
  const share = bad ? (A.noCtrl / bad) * 100 : 0;

  const pct = (val) => val.toFixed(1).replace(".", ",");
  const num = (val) => Math.round(val).toLocaleString("es-CO");

  const rows = [
    { l: "Órdenes totales", v: num(A.tot), c: "" },
    { l: "Efectivas", v: num(A.ef), c: "ok" },
    { l: "Fallidas", v: num(A.fa), c: "warn" },
    { l: "Perdidas", v: num(A.pe), c: "bad" },
    { l: "Efectividad", v: `${pct(A.efPct)}%`, c: A.efPct >= 75 ? "ok" : A.efPct >= 60 ? "warn" : "bad" },
    { l: "% Perdidas", v: `${pct(A.pePct)}%`, c: A.pePct >= 8 ? "bad" : "ok" },
    { l: "% Fallidas", v: `${pct(A.faPct)}%`, c: A.faPct >= 25 ? "bad" : "warn" },
    { l: "Técnicos activos", v: num(A.tecs.size), c: "" },
    { l: "Brigadas activas", v: num(A.brigs.size), c: "" }
  ];

  return (
    <section id="strip">
      <div id="kpis">
        {rows.map((row, i) => (
          <div key={i} className={`kpi ${row.c}`.trim()}>
            <span className="kl">{row.l}</span>
            <span className={`kv ${row.c}`}>{row.v}</span>
          </div>
        ))}
      </div>
      <div id="adjbox">
        <div className="adj-main">
          <div className="adj-c">
            <span className="adj-l">Efect. bruta</span>
            <span className="adj-v">{pct(A.efPct)}%</span>
          </div>
          <div className="adj-arrow">→</div>
          <div className="adj-c">
            <span className="adj-l">Efect. ajustada</span>
            <span className="adj-v ok">{pct(A.efAdj)}%</span>
          </div>
          <button className="adj-i" aria-label="Por qué hay dos efectividades">i</button>
        </div>
        <div className="adj-pop">
          <b>{num(A.noCtrl)}</b> de <b>{num(bad)}</b> órdenes no efectivas ({pct(share)}%) tienen causas{" "}
          <b>fuera del control de la operación</b> — sobre todo clientes que pagaron antes del corte
          y usuarios agresivos. La efectividad ajustada excluye esas causas para no castigar
          injustamente a técnicos y brigadas.
        </div>
      </div>
    </section>
  );
}
