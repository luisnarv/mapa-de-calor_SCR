"use client";

import React, { useState } from "react";

import { useTheme, riskColor as riskColorOf, riskInk } from "@/lib/theme";

export default function Dock({
  A,
  st,
  dim,
  dayLabel,
  onFilterChange,
  onSelectBarrio
}) {
  const [gran, setGran] = useState("dia");
  const { palette: P } = useTheme();

  const num = (val) => Math.round(val).toLocaleString("es-CO");
  const pct = (val) => val.toFixed(1).replace(".", ",");

  const riskColor = (r) => riskColorOf(P, r);

  /* Estados en texto: variante con contraste AA */
  const ST_COLOR = P.stText;

  const barrioName = (b) => dim.barrios[b].split(" | ")[1];
  const barrioMuni = (b) => dim.barrios[b].split(" | ")[0];

  const topList = (map_, k = 5) => {
    return [...map_.entries()].sort((a, b) => b[1] - a[1]).slice(0, k);
  };

  const miniTable = (rows, heads) => {
    if (!rows.length) return <p className="empty">Sin datos.</p>;
    return (
      <table className="mt">
        <thead>
          <tr>
            {heads.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rIdx) => (
            <tr key={rIdx}>
              {row.map((cell, cIdx) => (
                <td key={cIdx} className={cIdx ? "num" : ""}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  // --- 1. TENDENCIAS VIEW ---
  const renderTrend = () => {
    const baseDateStr = st.fechaMin ? st.fechaMin + "T00:00:00" : "2026-05-01T00:00:00";
    const buck = new Map();
    const key = (d) =>
      gran === "dia"
        ? d
        : gran === "semana"
        ? Math.floor(d / 7)
        : new Date(baseDateStr).getTime() + d * 86400000;

    const D0 = new Date(baseDateStr);

    const label = (k) => {
      if (gran === "dia") return dayLabel(k);
      if (gran === "semana") return "Sem " + (k - Math.floor(st.d0 / 7) + 1);
      const d = new Date(k);
      return ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][d.getMonth()];
    };

    for (const [d, v] of A.byDay) {
      const k = key(d);
      let o = buck.get(k);
      if (!o) {
        o = [0, 0, 0];
        buck.set(k, o);
      }
      o[0] += v[0];
      o[1] += v[1];
      o[2] += v[2];
    }

    const ks = [...buck.keys()].sort((x, y) => x - y);
    if (ks.length < 2) {
      return (
        <p className="empty">
          El rango es demasiado corto para calcular una tendencia. Amplía las
          fechas o cambia la granularidad.
        </p>
      );
    }

    const series = ks.map((k) => {
      const v = buck.get(k);
      const t = v[0] + v[1] + v[2];
      return { k, t, v };
    });
    const maxT = Math.max(...series.map((s) => s.t)) || 1;

    // split comparisons
    const mid = Math.floor((st.d0 + st.d1) / 2);
    const H = [
      [0, 0, 0],
      [0, 0, 0]
    ];
    for (const [d, v] of A.byDay) {
      const h = d <= mid ? 0 : 1;
      H[h][0] += v[0];
      H[h][1] += v[1];
      H[h][2] += v[2];
    }

    const rate = (h) => {
      const t = h[0] + h[1] + h[2];
      return t
        ? {
            ef: (h[0] / t) * 100,
            fa: (h[1] / t) * 100,
            pe: (h[2] / t) * 100,
            t
          }
        : { ef: 0, fa: 0, pe: 0, t: 0 };
    };
    const R1 = rate(H[0]),
      R2 = rate(H[1]);

    const card = (lab, v1, v2, goodUp) => {
      const d = v2 - v1;
      const flat = Math.abs(d) < 0.5;
      const good = goodUp ? d > 0 : d < 0;
      const cls = flat ? "dim" : good ? "ok" : "bad";
      const ar = flat ? "=" : d > 0 ? "▲" : "▼";
      return (
        <div className="tc">
          <span>{lab}</span>
          <b>{pct(v2)}%</b>
          <span className={cls}>
            {ar} {pct(Math.abs(d))} pp
          </span>
        </div>
      );
    };

    const volD = R1.t ? ((R2.t - R1.t) / R1.t) * 100 : 0;

    return (
      <div className="dock-row">
        <div className="trend-cards">
          <div className="tc-h">1.ª mitad → 2.ª mitad</div>
          {card("Efectividad", R1.ef, R2.ef, true)}
          {card("Tasa perdida", R1.pe, R2.pe, false)}
          {card("Tasa fallida", R1.fa, R2.fa, false)}
          <div className="tc">
            <span>Volumen</span>
            <b>{num(R2.t)} órdenes</b>
            <span className={volD >= 0 ? "ok" : "bad"}>
              {volD >= 0 ? "+" : ""}
              {pct(volD)}%
            </span>
          </div>
        </div>

        <div className="chart">
          {series.map((s, i) => {
            const h = (s.t / maxT) * 100;
            const seg = (e) => (s.t ? (s.v[e] / s.t) * 100 : 0);
            return (
              <div
                key={i}
                className="tb"
                style={{ width: `${100 / series.length}%` }}
                title={`${label(s.k)} · ${num(s.t)} órdenes · ${pct(
                  seg(0)
                )}% efectividad · ${num(s.v[1])} fallidas · ${num(
                  s.v[2]
                )} perdidas`}
              >
                <div className="tb-stack" style={{ height: `${h}%` }}>
                  <i style={{ height: `${seg(2)}%`, background: P.st[2] }}></i>
                  <i style={{ height: `${seg(1)}%`, background: P.st[1] }}></i>
                  <i style={{ height: `${seg(0)}%`, background: P.st[0] }}></i>
                </div>
                <span className="tb-l">
                  {series.length <= 12 ||
                  i % Math.ceil(series.length / 12) === 0
                    ? label(s.k)
                    : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // --- 2. MOTIVOS VIEW ---
  const renderCauses = () => {
    const bad = A.tot - A.ef;
    const causes = [...A.causa.entries()]
      .filter(([c]) => dim.causas[c] !== "Efectiva")
      .sort((x, y) => y[1] - x[1]);

    return (
      <div className="causes">
        {causes.map(([c, n]) => {
          const p = bad ? (n / bad) * 100 : 0;
          const ctrl = dim.causa_ctrl[c] === 1;
          return (
            <div key={c} className="cz">
              <span className="cz-n">
                {dim.causas[c]}{" "}
                {!ctrl && (
                  <em title="Fuera del control de la operación">
                    no controlable
                  </em>
                )}
              </span>
              <i>
                <b
                  style={{
                    width: `${p}%`,
                    background: ctrl ? P.series[1] : P.serieRef
                  }}
                ></b>
              </i>
              <span className="cz-v">
                {pct(p)}% <em>{num(n)}</em>
              </span>
            </div>
          );
        })}
        {!causes.length && <p className="empty">Sin órdenes no efectivas.</p>}
      </div>
    );
  };

  // --- 3. RANKING VIEW ---
  const renderRanking = () => {
    const minN = 30;
    const T = [...A.tec.entries()].filter(([, o]) => o.tot >= minN);
    const Bg = [...A.brig.entries()];

    const tbl = (rows, heads, fmt) => (
      <table className="mt rk">
        <thead>
          <tr>
            {heads.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.map(fmt)}</tbody>
      </table>
    );

    const tecRow = ([t, o]) => (
      <tr key={t}>
        <td>{dim.tecs[t]}</td>
        <td className="num">{num(o.tot)}</td>
        <td className="num ok">{pct(o.efAdj)}%</td>
        <td className="num">{pct(o.efPct)}%</td>
        <td className="num warn">{num(o.fa)}</td>
        <td className="num bad">{num(o.pe)}</td>
      </tr>
    );

    const brigRow = ([g, o]) => (
      <tr key={g}>
        <td>{dim.brigs[g]}</td>
        <td className="num">{num(o.tot)}</td>
        <td className="num ok">{pct(o.efAdj)}%</td>
        <td className="num">{pct(o.efPct)}%</td>
        <td className="num warn">{num(o.fa)}</td>
        <td className="num bad">{num(o.pe)}</td>
      </tr>
    );

    const H = ["", "Órdenes", "Efect. aj.", "Efect. bruta", "Fallidas", "Perdidas"];

    const best = T.slice()
      .sort((a, b) => b[1].efAdj - a[1].efAdj)
      .slice(0, 8);
    const worst = T.slice()
      .sort((a, b) => a[1].efAdj - b[1].efAdj)
      .slice(0, 8);
    const lost = T.slice()
      .sort((a, b) => b[1].pe - a[1].pe)
      .slice(0, 8);
    const vol = T.slice()
      .sort((a, b) => b[1].tot - a[1].tot)
      .slice(0, 8);

    return (
      <>
        <div className="dock-row rk-grid">
          <div>
            <h4>
              Técnicos · mayor efectividad ajustada{" "}
              <span className="hint">≥{minN} órdenes</span>
            </h4>
            {tbl(best, ["Técnico", ...H.slice(1)], tecRow)}
          </div>
          <div>
            <h4>Técnicos · menor efectividad ajustada</h4>
            {tbl(worst, ["Técnico", ...H.slice(1)], tecRow)}
          </div>
          <div>
            <h4>Técnicos · más órdenes perdidas</h4>
            {tbl(lost, ["Técnico", ...H.slice(1)], tecRow)}
          </div>
          <div>
            <h4>Técnicos · mayor volumen</h4>
            {tbl(vol, ["Técnico", ...H.slice(1)], tecRow)}
          </div>
          <div className="wide">
            <h4>Brigadas · desempeño</h4>
            {tbl(
              Bg.sort((a, b) => b[1].efAdj - a[1].efAdj),
              ["Brigada", ...H.slice(1)],
              brigRow
            )}
          </div>
        </div>
        <p className="hint" style={{ marginTop: "12px" }}>
          El ranking ordena por <b>efectividad ajustada</b>. Ordenar por efectividad bruta penalizaría a los técnicos que
          recibieron más órdenes de clientes que ya habían pagado — algo que no depende de ellos.
        </p>
      </>
    );
  };

  // --- 4. CARGA VIEW ---
  const renderCarga = () => {
    const rows = [...A.barrio.entries()]
      .filter(([, o]) => o.tot >= st.minOrders)
      .sort((a, b) => b[1].tot - a[1].tot)
      .slice(0, 60);

    return (
      <table className="mt rk">
        <thead>
          <tr>
            <th>Barrio</th>
            <th>Municipio</th>
            <th className="num">Órdenes</th>
            <th className="num">Efect.</th>
            <th className="num ok">Efect. aj.</th>
            <th className="num warn">Fallidas</th>
            <th className="num bad">Perdidas</th>
            <th className="num">Técnicos</th>
            <th className="num">Brigadas</th>
            <th className="num">Riesgo</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([b, o]) => (
            <tr key={b} className="clk" onClick={() => onSelectBarrio(b)}>
              <td>{barrioName(b)}</td>
              <td className="dim">{barrioMuni(b)}</td>
              <td className="num">{num(o.tot)}</td>
              <td className="num">{pct(o.efPct)}%</td>
              <td className="num ok">{pct(o.efAdj)}%</td>
              <td className="num warn">{num(o.fa)}</td>
              <td className="num bad">{num(o.pe)}</td>
              <td className="num">{o.tec.size}</td>
              <td className="num">{o.brig.size}</td>
              <td className="num">
                <span
                  className="pill"
                  style={{ background: riskColor(o.risk), color: riskInk(P, o.risk) }}
                >
                  {o.risk ?? "—"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  // --- 5. JERARQUIA VIEW ---
  const renderJerarquia = () => {
    const bsel = st.jerBarrio,
      gsel = st.jerBrig;

    const crumb = (
      <div className="jer-crumb">
        <button
          className={`jc ${bsel == null ? "on" : ""}`}
          onClick={() => {
            onFilterChange("jerBarrio", null);
            onFilterChange("jerBrig", null);
          }}
        >
          Todos los barrios
        </button>
        {bsel != null && (
          <>
            <span>&rsaquo;</span>
            <button
              className={`jc ${gsel == null ? "on" : ""}`}
              onClick={() => onFilterChange("jerBrig", null)}
            >
              {barrioName(bsel)}
            </button>
          </>
        )}
        {gsel != null && (
          <>
            <span>&rsaquo;</span>
            <button className="jc on" disabled>
              {dim.brigs[gsel]}
            </button>
          </>
        )}
      </div>
    );

    let cuerpo = "";

    if (bsel == null) {
      // Level 1: Barrios
      const barrios = [...A.barrio.entries()]
        .filter(([, o]) => o.tot >= st.minOrders)
        .sort((a, b) => b[1].tot - a[1].tot);

      cuerpo = (
        <table className="mt rk">
          <thead>
            <tr>
              <th>Barrio</th>
              <th>Municipio</th>
              <th className="num">Órdenes</th>
              <th className="num ok">Efectividad</th>
              <th className="num">Técnicos</th>
              <th className="num">Brigadas</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {barrios.map(([b, o]) => (
              <tr
                key={b}
                className="clk"
                onClick={() => onFilterChange("jerBarrio", b)}
              >
                <td>{barrioName(b)}</td>
                <td className="dim">{barrioMuni(b)}</td>
                <td className="num">{num(o.tot)}</td>
                <td className="num ok">{pct(o.efAdj)}%</td>
                <td className="num">{o.tec.size}</td>
                <td className="num">{o.brig.size}</td>
                <td className="num">&rsaquo;</td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    } else {
      const o = A.barrio.get(bsel);
      if (!o) return <p className="empty">Sin datos en este barrio.</p>;

      if (gsel == null) {
        // Level 2: Brigadas within selected Barrio
        const brigs = [...o.brig.entries()].sort((a, b) => b[1] - a[1]);
        cuerpo = (
          <table className="mt rk">
            <thead>
              <tr>
                <th>Brigada</th>
                <th className="num">Órdenes</th>
                <th className="num">Participación</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {brigs.map(([g, c]) => (
                <tr
                  key={g}
                  className="clk"
                  onClick={() => onFilterChange("jerBrig", g)}
                >
                  <td>{dim.brigs[g]}</td>
                  <td className="num">{num(c)}</td>
                  <td className="num">{pct((c / o.tot) * 100)}%</td>
                  <td className="num">&rsaquo;</td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      } else {
        // Level 3 & 4: Techs and Orders
        const porTec = new Map();
        const I = A.IDX || [];
        for (let j = 0; j < I.length; j++) {
          const i = I[j];
          if (st.B_raw[i] !== bsel || st.G_raw[i] !== gsel) continue;
          let t = porTec.get(st.T_raw[i]);
          if (!t) {
            t = { ef: 0, fa: 0, pe: 0, tot: 0, ords: [] };
            porTec.set(st.T_raw[i], t);
          }
          t.tot++;
          if (st.E_raw[i] === 0) t.ef++;
          else if (st.E_raw[i] === 1) t.fa++;
          else t.pe++;
          if (t.ords.length < 40) t.ords.push(i);
        }
        const tecs = [...porTec.entries()].sort((a, b) => b[1].tot - a[1].tot);

        cuerpo = tecs.map(([t, s]) => (
          <div key={t} className="jer-tec" style={{ borderBottom: "1px solid var(--line)", paddingBottom: "8px", marginBottom: "8px" }}>
            <div className="jer-tec-h" style={{ borderBottom: "none", paddingBottom: 0 }}>
              <b>{dim.tecs[t]}</b>
              <span className="jer-tec-m">
                {num(s.tot)} órdenes &middot;{" "}
                <em className="ok">{s.ef} ef</em> &middot;{" "}
                <em className="warn">{s.fa} fa</em> &middot;{" "}
                <em className="bad">{s.pe} pe</em> &middot; {pct((s.ef / s.tot) * 100)}% efect.
              </span>
            </div>
          </div>
        ));
      }
    }

    return (
      <>
        {crumb}
        <div className="jer-body" style={{ marginTop: "8px" }}>
          {cuerpo}
        </div>
      </>
    );
  };

  return (
    <div id="dock" className={st.dockCollapsed ? "min" : ""}>
      <div className="dh">
        <button
          className={`dtab ${st.dock === "tendencias" ? "on" : ""}`}
          onClick={() => {
            onFilterChange("dock", "tendencias");
            onFilterChange("dockCollapsed", false);
          }}
        >
          Tendencias
        </button>
        <button
          className={`dtab ${st.dock === "motivos" ? "on" : ""}`}
          onClick={() => {
            onFilterChange("dock", "motivos");
            onFilterChange("dockCollapsed", false);
          }}
        >
          Motivos de pérdida
        </button>
        <button
          className={`dtab ${st.dock === "ranking" ? "on" : ""}`}
          onClick={() => {
            onFilterChange("dock", "ranking");
            onFilterChange("dockCollapsed", false);
          }}
        >
          Ranking operativo
        </button>
        <button
          className={`dtab ${st.dock === "carga" ? "on" : ""}`}
          onClick={() => {
            onFilterChange("dock", "carga");
            onFilterChange("dockCollapsed", false);
          }}
        >
          Carga operativa
        </button>
        <button
          className={`dtab ${st.dock === "jerarquia" ? "on" : ""}`}
          onClick={() => {
            onFilterChange("dock", "jerarquia");
            onFilterChange("dockCollapsed", false);
          }}
        >
          Jerarquía
        </button>

        {st.dock === "tendencias" && !st.dockCollapsed && (
          <select
            id="gran"
            value={gran}
            onChange={(e) => setGran(e.target.value)}
          >
            <option value="dia">Por día</option>
            <option value="semana">Por semana</option>
            <option value="mes">Por mes</option>
          </select>
        )}

        <button
          className="btn"
          id="dockToggle"
          onClick={() => onFilterChange("dockCollapsed", !st.dockCollapsed)}
        >
          {st.dockCollapsed ? "Expandir" : "Contraer"}
        </button>
      </div>

      <div id="dockBody">
        {!st.dockCollapsed && (
          <>
            {st.dock === "tendencias" && renderTrend()}
            {st.dock === "motivos" && renderCauses()}
            {st.dock === "ranking" && renderRanking()}
            {st.dock === "carga" && renderCarga()}
            {st.dock === "jerarquia" && renderJerarquia()}
          </>
        )}
      </div>
    </div>
  );
}
