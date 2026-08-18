"use client";

import React, { useState } from "react";
import { Info, Star } from "lucide-react";

import { useTheme, riskColor as riskColorOf, riskInk } from "@/lib/theme";

export default function DetailsPanel({
  st,
  A,
  ELIG,
  dim,
  geo,
  dayLabel,
  recommend,
  techRoute,
  zoneAvg,
  actionsFor,
  onFilterChange,
  onSelectBarrio
}) {
  // Controla si el panel de detalles está desplegado u oculto (empieza oculto)
  const [collapsed, setCollapsed] = useState(true);
  const { palette: P } = useTheme();

  const num = (val) => Math.round(val).toLocaleString("es-CO");
  const pct = (val) => val.toFixed(1).replace(".", ",");
  const delta = (v, ref) => {
    const d = v - ref;
    const cls = d > 0 ? "ok" : "bad";
    return (
      <span className={`dl ${Math.abs(d) < 3 ? "" : cls}`}>
        {d >= 0 ? "+" : ""}
        {pct(d)} pp
      </span>
    );
  };

  const riskColor = (r) => riskColorOf(P, r);

  /* Estados: relleno de marca para barras y leyendas */
  const ST_COLOR = P.st;

  const barrioName = (b) => dim.barrios[b].split(" | ")[1];
  const barrioMuni = (b) => dim.barrios[b].split(" | ")[0];

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

  const topList = (map_, k = 5) => {
    return [...map_.entries()].sort((a, b) => b[1] - a[1]).slice(0, k);
  };

  // --- 1. BARRIO / GLOBAL PANEL ---
  const renderBarrio = () => {
    const b = st.selBarrio;

    // GLOBAL OVERVIEW
    if (b == null) {
      const badTot = A.tot - A.ef;
      const causes = [...A.causa.entries()]
        .filter(([c]) => dim.causas[c] !== "Efectiva")
        .sort((x, y) => y[1] - x[1]);
      const topRisky = ELIG.slice()
        .sort((x, y) => (y[1].risk ?? -1) - (x[1].risk ?? -1))
        .slice(0, 5);
      const subRows = [...A.sub.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);

      return (
        <>
          <div className="pb-head" style={{ marginBottom: "12px" }}>
            <div
              className="pb-risk"
              style={{
                background: "var(--badge-bg)",
                width: "38px",
                height: "38px",
                fontSize: "14px",
                flex: "0 0 38px",
                color: "var(--badge-tx)",
                fontWeight: "600"
              }}
            >
              GLO
            </div>
            <div>
              <h2>Resumen de Operación</h2>
              <span className="pb-sub">
                {st.zona === "" ? "Todo el departamento" : "Zona: " + dim.zonas[+st.zona]}{" "}
                &middot; {st.muni === "" ? "Todos los municipios" : dim.munis[+st.muni]}
              </span>
            </div>
          </div>

          <div className="mini-grid" style={{ marginBottom: "14px" }}>
            <div className="mini">
              <span>Total</span>
              <b>{num(A.tot)}</b>
            </div>
            <div className="mini">
              <span>Efectivas</span>
              <b className="ok">{num(A.ef)}</b>
            </div>
            <div className="mini">
              <span>Fallidas</span>
              <b className="warn">{num(A.fa)}</b>
            </div>
            <div className="mini">
              <span>Perdidas</span>
              <b className="bad">{num(A.pe)}</b>
            </div>
          </div>

          <h3>Efectividad Global</h3>
          <table className="cmp" style={{ marginBottom: "14px" }}>
            <tbody>
              <tr>
                <td>Efectividad bruta</td>
                <td className="num">{pct(A.efPct)}%</td>
              </tr>
              <tr>
                <td>Efectividad ajustada</td>
                <td className="num ok" style={{ fontWeight: "600" }}>
                  {pct(A.efAdj)}%
                </td>
              </tr>
              <tr>
                <td>Tasa de pérdida</td>
                <td className="num bad">{pct(A.pePct)}%</td>
              </tr>
              <tr>
                <td>Tasa de fallas</td>
                <td className="num warn">{pct(A.faPct)}%</td>
              </tr>
            </tbody>
          </table>

          <h3>
            Barrios con mayor riesgo <span className="hint">clic para analizar</span>
          </h3>
          {topRisky.length ? (
            <table className="mt rk" style={{ marginBottom: "14px" }}>
              <thead>
                <tr>
                  <th>Barrio</th>
                  <th>Municipio</th>
                  <th className="num">Riesgo</th>
                </tr>
              </thead>
              <tbody>
                {topRisky.map(([bIdx, o]) => (
                  <tr
                    key={bIdx}
                    className="clk"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectBarrio(bIdx);
                    }}
                  >
                    <td>{barrioName(bIdx)}</td>
                    <td className="dim">{barrioMuni(bIdx)}</td>
                    <td className="num">
                      <span
                        className="pill"
                        style={{ background: riskColor(o.risk), color: riskInk(P, o.risk) }}
                      >
                        {o.risk}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty" style={{ marginBottom: "14px" }}>
              Sin barrios con muestra suficiente.
            </p>
          )}

          <h3>
            Motivos de no efectividad <span className="hint">{num(badTot)} órdenes</span>
          </h3>
          <div className="causes" style={{ marginBottom: "14px" }}>
            {causes.slice(0, 5).map(([c, n]) => {
              const p = badTot ? (n / badTot) * 100 : 0;
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
            {!causes.length && (
              <p className="empty" style={{ marginBottom: "14px" }}>
                Sin órdenes no efectivas.
              </p>
            )}
          </div>

          <h3>
            Distribución de trabajo realizado <span className="hint">efectivas</span>
          </h3>
          {A.ef
            ? miniTable(
                subRows.map(([s, n]) => [
                  dim.subs[s],
                  num(n),
                  pct((n / A.ef) * 100) + "%"
                ]),
                ["Subacción", "Órdenes", "Part."]
              )
            : <p className="empty">Sin datos.</p>}

          <p
            className="hint"
            style={{ marginTop: "16px", lineHeight: "1.45", color: "var(--dim)" }}
          >
            * Selecciona un barrio en la columna de riesgo o haz clic en cualquier
            círculo del mapa para ver el diagnóstico específico de esa zona.
          </p>
        </>
      );
    }

    // SPECIFIC BARRIO OVERVIEW
    const o = A.barrio.get(b);
    if (!o) {
      return (
        <p className="empty">
          Ese barrio no tiene órdenes con los filtros actuales.
        </p>
      );
    }

    const z = zoneAvg(dim.b_zona[b]);
    const zonaN = dim.zonas[dim.b_zona[b]];

    const causes = [...o.causa.entries()]
      .filter(([c]) => dim.causas[c] !== "Efectiva")
      .sort((x, y) => y[1] - x[1]);
    const badTot = o.tot - o.ef;

    const acts = actionsFor(b, o, z);
    const recT = recommend(b, st.tipo === "" ? -1 : +st.tipo, "tec").slice(0, 3);

    // local effective technicians
    const localTechs = [...o.tecEf.entries()]
      .map(([t, s]) => {
        const den = s.tot - s.noCtrl;
        return {
          t,
          tot: s.tot,
          ef: s.ef,
          efAdj: den > 0 ? (s.ef / den) * 100 : 0,
          den
        };
      })
      .filter((r) => r.tot >= 3)
      .sort((a, b) => b.efAdj - a.efAdj);

    return (
      <>
        <div className="pb-head">
          <div
            className="pb-risk"
            style={{ background: riskColor(o.risk), color: riskInk(P, o.risk) }}
          >
            {o.risk ?? "—"}
          </div>
          <div>
            <h2>{barrioName(b)}</h2>
            <span className="pb-sub">
              {barrioMuni(b)} · {zonaN} · Prioridad{" "}
              <b style={{ color: riskColorOf(P, o.risk, true) }}>
                {o.prio ?? "muestra insuficiente"}
              </b>
            </span>
          </div>
        </div>

        <div className="mini-grid">
          <div className="mini">
            <span>Total</span>
            <b>{num(o.tot)}</b>
          </div>
          <div className="mini">
            <span>Efectivas</span>
            <b className="ok">{num(o.ef)}</b>
          </div>
          <div className="mini">
            <span>Fallidas</span>
            <b className="warn">{num(o.fa)}</b>
          </div>
          <div className="mini">
            <span>Perdidas</span>
            <b className="bad">{num(o.pe)}</b>
          </div>
        </div>

        <h3>Comparación contra la zona {zonaN}</h3>
        <table className="cmp">
          <thead>
            <tr>
              <th></th>
              <th>Barrio</th>
              <th>Zona</th>
              <th>Δ</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Efectividad</td>
              <td>{pct(o.efPct)}%</td>
              <td>{pct(z.efPct)}%</td>
              <td>{delta(o.efPct, z.efPct)}</td>
            </tr>
            <tr>
              <td>Efectividad ajustada</td>
              <td>{pct(o.efAdj)}%</td>
              <td>{pct(z.efAdj)}%</td>
              <td>{delta(o.efAdj, z.efAdj)}</td>
            </tr>
            <tr>
              <td>% Perdidas</td>
              <td>{pct(o.pePct)}%</td>
              <td>{pct(z.pePct)}%</td>
              <td>{delta(z.pePct, o.pePct)}</td>
            </tr>
            <tr>
              <td>% Fallidas</td>
              <td>{pct(o.faPct)}%</td>
              <td>{pct(z.faPct)}%</td>
              <td>{delta(z.faPct, o.faPct)}</td>
            </tr>
          </tbody>
        </table>

        {o.risk != null && (
          <>
            <h3>
              Composición del índice de riesgo <span className="hint">(0-100)</span>
            </h3>
            <div className="parts">
              {[
                ["% perdidas", o.parts.pe, 40],
                ["% fallidas", o.parts.fa, 25],
                ["Volumen", o.parts.vol, 15],
                ["Tendencia", o.parts.tr, 10],
                ["Desempeño histórico", o.parts.hist, 10]
              ].map(([l, v, mx]) => (
                <div key={l} className="part">
                  <span>{l}</span>
                  <i>
                    <b style={{ width: `${(v / mx) * 100}%` }}></b>
                  </i>
                  <em>
                    {v.toFixed(1)}/{mx}
                  </em>
                </div>
              ))}
            </div>
          </>
        )}

        <h3>
          Acciones recomendadas{" "}
          <span className="hint" title="Sugerencias automáticas derivadas de reglas operativas sobre los datos del barrio (causas, efectividad, tendencia). No usan IA.">
            generadas por reglas operativas
          </span>
        </h3>
        <div className="acts">
          {acts.map((a, idx) => (
            <div key={idx} className={`act ${a.lvl}`}>
              <b>{a.t}</b>
              <p dangerouslySetInnerHTML={{ __html: a.d }} />
            </div>
          ))}
        </div>

        <h3>
          Motivos de no efectividad <span className="hint">{num(badTot)} órdenes</span>
        </h3>
        <div className="causes">
          {causes.map(([c, n]) => {
            const p = badTot ? (n / badTot) * 100 : 0;
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

        <h3>Técnico recomendado aquí</h3>
        {recT.length ? (
          <div className="recs">
            {recT.map((r, i) => (
              <div key={i} className={`rec ${i === 0 ? "best" : ""}`}>
                <div className="rec-h">
                  <b>{r.name}</b>
                  <span 
                    className={`conf ${r.conf.toLowerCase()}`}
                    title="La confianza estadística se calcula según la cantidad de órdenes comparables:&#10;• Alta: 30 o más órdenes&#10;• Media: 10 a 29 órdenes&#10;• Baja: menos de 10 órdenes"
                    style={{ cursor: "help", textDecoration: "underline dotted", display: "inline-flex", alignItems: "center", gap: "2px" }}
                  >
                    Confianza {r.conf} <Info size={11} strokeWidth={2.2} style={{ opacity: 0.8 }} aria-hidden="true" />
                  </span>
                </div>
                <div className="rec-m">
                  <span>
                    <b>{pct(r.efAdj)}%</b> efect. ajustada
                  </span>
                  <span>
                    <b>{num(r.den)}</b> órdenes comparables
                  </span>
                  <span>
                    Última:{" "}
                    {r.last >= 0
                      ? dayLabel(Math.floor(r.last / 1440))
                      : "—"}
                  </span>
                </div>
                <div className="rec-s">Base: {r.scope}</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="empty">
            Muestra insuficiente para recomendar con fiabilidad en este barrio.
          </p>
        )}

        <h3>
          Técnicos más efectivos aquí <span className="hint">en este barrio</span>
        </h3>
        {localTechs.length
          ? miniTable(
              localTechs
                .slice(0, 6)
                .map((r) => [
                  dim.tecs[r.t],
                  num(r.tot),
                  pct(r.efAdj) + "%"
                ]),
              ["Técnico", "Órdenes", "Efect. aj. aquí"]
            )
          : <p className="empty">Ningún técnico tiene ≥3 órdenes aquí.</p>}

        <h3>
          Trabajo efectivo realizado <span className="hint">para saber qué distribuir</span>
        </h3>
        {o.ef
          ? miniTable(
              topList(o.subEf, 6).map(([s, n]) => [
                dim.subs[s],
                num(n),
                pct((n / o.ef) * 100) + "%"
              ]),
              ["Acción ejecutada", "Órdenes", "Part."]
            )
          : <p className="empty">Sin órdenes efectivas en este barrio.</p>}

        <h3>Brigadas asignadas</h3>
        {miniTable(
          topList(o.brig, 5).map(([g, c]) => [
            dim.brigs[g],
            num(c),
            pct((c / o.tot) * 100) + "%"
          ]),
          ["Brigada", "Órdenes", "Part."]
        )}

        <h3>Tipos de orden</h3>
        {miniTable(
          topList(o.tipo, 5).map(([t, c]) => [
            dim.tipos[t],
            num(c),
            pct((c / o.tot) * 100) + "%"
          ]),
          ["Tipo OS", "Órdenes", "Part."]
        )}

        <h3>Tipos de suspensión</h3>
        {miniTable(
          topList(o.susp, 5).map(([u, c]) => [
            dim.susps[u],
            num(c),
            pct((c / o.tot) * 100) + "%"
          ]),
          ["Suspensión", "Órdenes", "Part."]
        )}

        <h3>
          Subacciones en no efectivas <span className="hint">{num(badTot)} órdenes</span>
        </h3>
        {miniTable(
          topList(o.sub, 6).map(([sx, c]) => [
            dim.subs[sx],
            num(c),
            badTot ? pct((c / badTot) * 100) + "%" : "—"
          ]),
          ["Subacción", "No efect.", "Part."]
        )}

        <h3>
          Causales en efectivas <span className="hint">distribución de lo ejecutado</span>
        </h3>
        {miniTable(
          topList(o.efTipo || new Map(), 5).map(([t, c]) => [
            dim.tipos[t],
            num(c),
            pct(o.ef ? (c / o.ef) * 100 : 0) + "%"
          ]),
          ["Tipo OS", "Efectivas", "Part."]
        )}

        <h3>
          Subacciones en efectivas <span className="hint">qué se ejecutó</span>
        </h3>
        {miniTable(
          topList(o.subEf || new Map(), 6).map(([sx, c]) => [
            dim.subs[sx],
            num(c),
            pct(o.ef ? (c / o.ef) * 100 : 0) + "%"
          ]),
          ["Subacción", "Efectivas", "Part."]
        )}
      </>
    );
  };

  // --- 2. TECNICO PANEL ---
  const renderTecnico = () => {
    const opts = [...A.tec.entries()]
      .sort((a, b) => b[1].tot - a[1].tot)
      .map(([t]) => (
        <option key={t} value={t}>
          {dim.tecs[t]}
        </option>
      ));

    let body = (
      <p className="empty">
        Selecciona un técnico para ver su cobertura, recorrido y desempeño.
      </p>
    );

    if (st.selTec != null && A.tec.has(st.selTec)) {
      const o = A.tec.get(st.selTec);
      const R = techRoute(st.selTec);
      const mainBrigList = [...o.brig.entries()].sort((a, b) => b[1] - a[1]);
      const mainBrig = mainBrigList.length ? mainBrigList[0][0] : null;
      const bo = mainBrig ? A.brig.get(mainBrig) : null;
      const brigName = mainBrig ? dim.brigs[mainBrig] : "—";
      const barrios = [...o.barrios]
        .map((b) => [barrioName(b), A.barrio.get(b)])
        .filter((x) => x[1]);

      body = (
        <>
          <div className="mini-grid">
            <div className="mini">
              <span>Órdenes</span>
              <b>{num(o.tot)}</b>
            </div>
            <div className="mini">
              <span>Efectivas · {num(o.ef)}</span>
              <b className="ok">{pct(o.efPct)}%</b>
            </div>
            <div className="mini">
              <span>Fallidas · {num(o.fa)}</span>
              <b className="warn">{pct(o.faPct)}%</b>
            </div>
            <div className="mini">
              <span>Perdidas · {num(o.pe)}</span>
              <b className="bad">{pct(o.pePct)}%</b>
            </div>
          </div>
          <div className="mini-grid">
            <div className="mini">
              <span>Efect. ajustada</span>
              <b className="ok">{pct(o.efAdj)}%</b>
            </div>
            <div className="mini">
              <span>Distancia</span>
              <b>{num(R.km)} km</b>
            </div>
            <div className="mini">
              <span>Mediana por orden</span>
              <b>{num(R.medMin)} min</b>
            </div>
            <div className="mini">
              <span>Días en campo</span>
              <b>{num(R.days)}</b>
            </div>
          </div>

          <h3>Distribución de resultados</h3>
          <div className="dist-bar">
            <i style={{ width: `${o.efPct}%`, background: ST_COLOR[0] }} title="Efectivas"></i>
            <i style={{ width: `${o.faPct}%`, background: ST_COLOR[1] }} title="Fallidas"></i>
            <i style={{ width: `${o.pePct}%`, background: ST_COLOR[2] }} title="Perdidas"></i>
          </div>
          <div className="dist-leg">
            <span>
              <i style={{ background: ST_COLOR[0] }}></i> Efectivas <b>{pct(o.efPct)}%</b>
            </span>
            <span>
              <i style={{ background: ST_COLOR[1] }}></i> Fallidas <b>{pct(o.faPct)}%</b>
            </span>
            <span>
              <i style={{ background: ST_COLOR[2] }}></i> Perdidas <b>{pct(o.pePct)}%</b>
            </span>
          </div>

          <h3>Comparación</h3>
          {bo && (
            <table className="cmp">
              <thead>
                <tr>
                  <th></th>
                  <th>Técnico</th>
                  <th>Brigada {brigName}</th>
                  <th>Δ</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Efectividad ajustada</td>
                  <td>{pct(o.efAdj)}%</td>
                  <td>{pct(bo.efAdj)}%</td>
                  <td>{delta(o.efAdj, bo.efAdj)}</td>
                </tr>
                <tr>
                  <td>Efectividad bruta</td>
                  <td>{pct(o.efPct)}%</td>
                  <td>{pct(bo.efPct)}%</td>
                  <td>{delta(o.efPct, bo.efPct)}</td>
                </tr>
              </tbody>
            </table>
          )}

          <h3>Brigadas con las que trabaja</h3>
          {miniTable(
            [...o.brig.entries()]
              .sort((a, b) => b[1] - a[1])
              .map(([g, c]) => [
                dim.brigs[g],
                num(c),
                pct((c / o.tot) * 100) + "%"
              ]),
            ["Brigada", "Órdenes", "Part."]
          )}

          <h3>
            Barrios atendidos <span className="hint">{barrios.length}</span>
          </h3>
          {miniTable(
            barrios
              .sort((a, b) => b[1].tot - a[1].tot)
              .slice(0, 12)
              .map(([nm, bo]) => [nm, num(bo.tot), pct(bo.efPct) + "%"]),
            ["Barrio", "Órdenes", "Efect."]
          )}

          <h3>Motivos de no efectividad</h3>
          {miniTable(
            topList(o.causa, 8)
              .filter(([c]) => dim.causas[c] !== "Efectiva")
              .slice(0, 6)
              .map(([c, n]) => [
                dim.causas[c],
                num(n),
                dim.causa_ctrl[c] ? "controlable" : "no controlable"
              ]),
            ["Causa", "Órdenes", ""]
          )}
        </>
      );
    }

    return (
      <>
        <div className="sel-wrap">
          <label>Técnico</label>
          <select
            id="tecSel"
            value={st.selTec ?? ""}
            onChange={(e) =>
              onFilterChange("selTec", e.target.value === "" ? null : +e.target.value)
            }
          >
            <option value="">— elegir —</option>
            {opts}
          </select>
        </div>
        {body}
      </>
    );
  };

  // --- 3. RECOMENDADOR PANEL ---
  const renderRecomendador = () => {
    const bopts = [...A.barrio.entries()]
      .sort((a, b) => b[1].tot - a[1].tot)
      .map(([b]) => (
        <option key={b} value={b}>
          {barrioName(b)} — {barrioMuni(b)}
        </option>
      ));

    const topts = dim.tipos.map((t, i) => (
      <option key={i} value={i}>
        {t}
      </option>
    ));

    let body = (
      <p className="empty">
        Elige un barrio y un tipo de orden para recibir la asignación sugerida.
      </p>
    );

    if (st.selBarrio != null) {
      const tp = st.tipo === "" ? -1 : +st.tipo;
      const rt = recommend(st.selBarrio, tp, "tec");
      const rb = recommend(st.selBarrio, tp, "brig");

      const card = (r, i, kind) => (
        <div key={i} className={`rec ${i === 0 ? "best" : ""}`}>
          <div className="rec-h">
            <b style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
              {i === 0 && (
                <Star size={12} strokeWidth={2.2} fill="currentColor" aria-hidden="true" />
              )}
              {r.name}
            </b>
            <span 
              className={`conf ${r.conf.toLowerCase()}`}
              title="La confianza estadística se calcula según la cantidad de órdenes comparables:&#10;• Alta: 30 o más órdenes&#10;• Media: 10 a 29 órdenes&#10;• Baja: menos de 10 órdenes"
              style={{ cursor: "help", textDecoration: "underline dotted", display: "inline-flex", alignItems: "center", gap: "2px" }}
            >
              Confianza {r.conf} <Info size={11} strokeWidth={2.2} style={{ opacity: 0.8 }} aria-hidden="true" />
            </span>
          </div>
          <div className="rec-m">
            <span>
              <b>{pct(r.efAdj)}%</b> efect. ajustada
            </span>
            <span>
              <b>{num(r.den)}</b> órdenes comparables
            </span>
            <span>
              <b>{num(r.pe)}</b> perdidas
            </span>
            <span>
              Última:{" "}
              {r.last >= 0 ? dayLabel(Math.floor(r.last / 1440)) : "—"}
            </span>
          </div>
          <div className="rec-s">
            Base: {r.scope} &middot; puntaje Wilson {pct(r.score)}
          </div>
        </div>
      );

      body = (
        <>
          <h3>Técnico recomendado</h3>
          {rt.length ? (
            <div className="recs">
              {rt.slice(0, 4).map((r, i) => card(r, i, "tec"))}
            </div>
          ) : (
            <p className="empty">
              Muestra insuficiente: no hay suficientes órdenes comparables para recomendar con fiabilidad.
            </p>
          )}

          <h3>Brigada recomendada</h3>
          {rb.length ? (
            <div className="recs">
              {rb.slice(0, 3).map((r, i) => card(r, i, "brig"))}
            </div>
          ) : (
            <p className="empty">Muestra insuficiente.</p>
          )}

          <p className="hint">
            El puntaje usa el <b>límite inferior de Wilson (95%)</b> sobre la
            efectividad ajustada. Las causas fuera de control no penalizan al técnico.
          </p>
        </>
      );
    }

    return (
      <>
        <div className="sel-wrap">
          <label>Barrio</label>
          <select
            id="recBarrio"
            value={st.selBarrio ?? ""}
            onChange={(e) =>
              onFilterChange("selBarrio", e.target.value === "" ? null : +e.target.value)
            }
          >
            <option value="">— elegir —</option>
            {bopts}
          </select>
        </div>
        <div className="sel-wrap">
          <label>Tipo de orden</label>
          <select
            id="recTipo"
            value={st.tipo}
            onChange={(e) => onFilterChange("tipo", e.target.value)}
          >
            <option value="">Todos</option>
            {topts}
          </select>
        </div>
        {body}
      </>
    );
  };

  const renderNic = () => {
    const nicVal = st.selNic;
    if (!nicVal) {
      return (
        <p className="empty">
          Ningún NIC seleccionado. Haz clic en un punto GPS real en el mapa para ver su historial detallado.
        </p>
      );
    }

    const { E_raw, C_raw, S_raw, T_raw, DAY_raw, M_raw, ORD_raw, B_raw, NIC_raw } = st;
    const history = [];

    if (NIC_raw) {
      const N = NIC_raw.length;
      for (let i = 0; i < N; i++) {
        if (NIC_raw[i] === nicVal) {
          history.push({
            idx: i,
            ord: ORD_raw[i],
            est: E_raw[i],
            causa: dim.causas[C_raw[i]],
            sub: dim.subs[S_raw[i]],
            tec: dim.tecs[T_raw[i]],
            m: M_raw[i],
            day: DAY_raw[i],
            barrio: barrioName(B_raw[i]),
            muni: barrioMuni(B_raw[i])
          });
        }
      }
    }

    history.sort((a, b) => b.m - a.m);
    const clientInfo = history[0] || {};

    const tableRows = history.map((h) => {
      const hh = String(Math.floor((h.m % 1440) / 60)).padStart(2, "0");
      const mm = String(h.m % 60).padStart(2, "0");
      const estClass = h.est === 0 ? "ok" : h.est === 1 ? "warn" : "bad";
      return [
        <span className="mono" style={{ fontSize: "10.5px" }}>{h.ord || "—"}</span>,
        <span className={estClass} style={{ fontWeight: "600" }}>{dim.estados[h.est]}</span>,
        `${dayLabel(h.day)} ${hh}:${mm}`,
        <span style={{ display: "block", fontSize: "10px", lineHeight: "1.1" }}>
          <b>{h.causa}</b>
          <span style={{ display: "block", color: "var(--dim)", marginTop: "2px" }}>{h.sub}</span>
        </span>,
        h.tec
      ];
    });

    return (
      <>
        <div className="pb-head" style={{ marginBottom: "14px" }}>
          <div
            className="pb-risk"
            style={{
              background: "var(--badge-bg)",
              width: "38px",
              height: "38px",
              fontSize: "13px",
              flex: "0 0 38px",
              color: "var(--badge-tx)",
              fontWeight: "600"
            }}
          >
            NIC
          </div>
          <div>
            <h2 style={{ fontSize: "15px" }}>NIC {nicVal}</h2>
            <span className="pb-sub">
              {clientInfo.barrio || "—"} &middot; {clientInfo.muni || "—"}
            </span>
          </div>
        </div>

        <div className="mini-grid" style={{ marginBottom: "14px" }}>
          <div className="mini">
            <span>Visitas</span>
            <b>{history.length}</b>
          </div>
          <div className="mini">
            <span>Efectivas</span>
            <b className="ok">{history.filter(h => h.est === 0).length}</b>
          </div>
          <div className="mini">
            <span>Fallidas</span>
            <b className="warn">{history.filter(h => h.est === 1).length}</b>
          </div>
          <div className="mini">
            <span>Perdidas</span>
            <b className="bad">{history.filter(h => h.est === 2).length}</b>
          </div>
        </div>

        <h3>Historial de Órdenes</h3>
        <div className="tbl-wrap" style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: "var(--r)", background: "var(--pan2)", maxHeight: "400px" }}>
          {miniTable(tableRows, ["Orden", "Estado", "Fecha/Hora", "Clasificación", "Técnico"])}
        </div>
      </>
    );
  };

  const isEnfoque = st.layout === "enfoque";
  const showRight = isEnfoque ? true : !collapsed;

  return (
    <>
      {!isEnfoque && (
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          title={collapsed ? "Mostrar panel de detalles" : "Ocultar panel de detalles"}
          aria-expanded={!collapsed}
          style={{
            position: "fixed",
            top: "50%",
            right: 0,
            transform: "translateY(-50%)",
            zIndex: 1200,
            display: "flex",
            alignItems: "center",
            gap: "6px",
            padding: "12px 7px",
            background: "var(--pan2)",
            border: "1px solid var(--line)",
            borderRight: "none",
            borderRadius: "var(--r) 0 0 var(--r)",
            color: "var(--cu2)",
            cursor: "pointer",
            font: "inherit",
            fontFamily: "var(--ff)",
            fontSize: "12px",
            fontWeight: 600,
            letterSpacing: ".16em",
            textTransform: "uppercase",
            writingMode: "vertical-rl"
          }}
        >
          {collapsed ? "Detalles" : "Ocultar"}
        </button>
      )}

      <aside id="right" style={!showRight ? { display: "none" } : undefined}>
        <div className="ph">
          <button
            className={`ptab ${st.tab === "barrio" ? "on" : ""}`}
            onClick={() => onFilterChange("tab", "barrio")}
          >
            Análisis de barrio
          </button>
          <button
            className={`ptab ${st.tab === "tecnico" ? "on" : ""}`}
            onClick={() => onFilterChange("tab", "tecnico")}
          >
            Cobertura del técnico
          </button>
          <button
            className={`ptab ${st.tab === "recomendador" ? "on" : ""}`}
            onClick={() => onFilterChange("tab", "recomendador")}
          >
            Recomendador
          </button>
          {st.selNic && (
            <button
              className={`ptab ${st.tab === "nic" ? "on" : ""}`}
              onClick={() => onFilterChange("tab", "nic")}
            >
              Historial del NIC
            </button>
          )}
        </div>
        <div id="panelBody">
          {st.tab === "barrio" && renderBarrio()}
          {st.tab === "tecnico" && renderTecnico()}
          {st.tab === "recomendador" && renderRecomendador()}
          {st.tab === "nic" && renderNic()}
        </div>
      </aside>
    </>
  );
}