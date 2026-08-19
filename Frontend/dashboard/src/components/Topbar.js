"use client";

import React from "react";
import { ChevronDown, Moon, RefreshCw, Sun } from "lucide-react";

import { useTheme } from "@/lib/theme";

export default function Topbar({
  st,
  dim,
  avail,
  onFilterChange,
  onReset,
  dayLabel,
  availableMonths,
  loadingMonths = [],
  onRefresh,
  refreshing = false
}) {
  const { theme, toggle } = useTheme();

  const [monthsOpen, setMonthsOpen] = React.useState(false);

  const monthName = (key) => key.split(" de ")[0];
  const activeMonths = st.months || [];
  const totalMonths = availableMonths.length;
  const activeOrdered = availableMonths.filter((m) => activeMonths.includes(m.key));
  const activeCount = activeOrdered.length;
  const allActive = totalMonths > 0 && activeCount === totalMonths;

  let monthSummary;
  if (allActive || activeCount === 0) {
    monthSummary = "Todos los meses";
  } else if (activeCount <= 2) {
    monthSummary = activeOrdered.map((m) => monthName(m.key)).join(" · ");
  } else {
    monthSummary = `${monthName(activeOrdered[0].key)} – ${monthName(activeOrdered[activeCount - 1].key)}`;
  }

  const toggleMonth = (key) => {
    const isActive = activeMonths.includes(key);
    if (isActive) {
      if (activeCount <= 1) return; // nunca dejar cero meses
      onFilterChange("months", activeMonths.filter((x) => x !== key));
    } else {
      onFilterChange("months", [...activeMonths, key]);
    }
  };

  const shortcutLabel = allActive ? "Solo el último" : "Todos";
  const applyShortcut = () => {
    if (allActive) {
      const last = availableMonths[totalMonths - 1];
      if (last) onFilterChange("months", [last.key]);
    } else {
      onFilterChange("months", availableMonths.map((m) => m.key));
    }
  };

  return (
    <header id="top">
      <div className="brand">
        <b>ISES <i>|</i> SCR</b>
        <span>Centro Operativo</span>
      </div>
      <div id="meta">
        {st.totalLoaded.toLocaleString("es-CO")} órdenes · {st.fechaMin} a {st.fechaMax} · {dim.barrios.length} barrios · {dim.tecs.length} técnicos
      </div>
      <div className="sp"></div>

      <div className="f">
        <label>Zona</label>
        <select
          value={st.zona}
          onChange={(e) => onFilterChange("zona", e.target.value)}
        >
          <option value="">Todas las zonas ({avail.zona.size})</option>
          {dim.zonas.map((name, i) =>
            avail.zona.has(i) ? (
              <option key={i} value={i}>
                {name}
              </option>
            ) : null
          )}
        </select>
      </div>

      <div className="f">
        <label>Municipio</label>
        <select
          value={st.muni}
          onChange={(e) => onFilterChange("muni", e.target.value)}
        >
          <option value="">Todos los municipios ({avail.muni.size})</option>
          {dim.munis.map((name, i) =>
            avail.muni.has(i) ? (
              <option key={i} value={i}>
                {name}
              </option>
            ) : null
          )}
        </select>
      </div>

      <div className="f">
        <label>Brigada</label>
        <select
          value={st.brig}
          onChange={(e) => onFilterChange("brig", e.target.value)}
        >
          <option value="">Todas las brigadas ({avail.brig.size})</option>
          {dim.brigs.map((name, i) =>
            avail.brig.has(i) ? (
              <option key={i} value={i}>
                {name}
              </option>
            ) : null
          )}
        </select>
      </div>

      <div className="f">
        <label>Tipo OS</label>
        <select
          value={st.tipo}
          onChange={(e) => onFilterChange("tipo", e.target.value)}
        >
          <option value="">Todos los tipos ({avail.tipo.size})</option>
          {dim.tipos.map((name, i) =>
            avail.tipo.has(i) ? (
              <option key={i} value={i}>
                {name}
              </option>
            ) : null
          )}
        </select>
      </div>

      <div className="f mm">
        <label>Meses de operación</label>
        <button
          type="button"
          className={`mm-btn ${monthsOpen ? "open" : ""}`}
          onClick={() => setMonthsOpen((v) => !v)}
          aria-expanded={monthsOpen}
        >
          <span className="mm-sum" title={monthSummary}>{monthSummary}</span>
          <span className="mm-count">{activeCount}/{totalMonths}</span>
          <span className="mm-chev" aria-hidden="true">
            <ChevronDown size={13} strokeWidth={2.2} />
          </span>
        </button>

        {monthsOpen && (
          <div className="mm-pop">
            <div className="mm-head">
              <span className="mm-title">Selecciona meses</span>
              <button type="button" className="mm-all" onClick={applyShortcut}>
                {shortcutLabel}
              </button>
            </div>

            <div className="mm-grid">
              {availableMonths.map((m) => {
                const on = activeMonths.includes(m.key);
                const isLoading = loadingMonths.includes(m.key);
                return (
                  <label
                    key={m.key}
                    className={`mm-item ${on ? "on" : ""}`}
                    title={m.recent ? "Mes en curso" : isLoading ? "Descargando…" : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => toggleMonth(m.key)}
                    />
                    <span className="mm-name">{monthName(m.key)}</span>
                    {isLoading ? (
                      <span className="mm-spin" aria-label="Descargando" />
                    ) : (
                      <span className="mm-n">
                        {(m.n || 0).toLocaleString("es-CO")}
                      </span>
                    )}
                  </label>
                );
              })}
            </div>


          </div>
        )}
      </div>



      <button className="btn" onClick={onReset}>
        Reiniciar filtros
      </button>

      {onRefresh && (
        <button
          type="button"
          className="btn refresh-btn"
          onClick={onRefresh}
          disabled={refreshing}
          title="Borra la caché local y vuelve a descargar el mes en curso"
        >
          <span
            className={`refresh-ic ${refreshing ? "spinning" : ""}`}
            aria-hidden="true"
          >
            <RefreshCw size={13} strokeWidth={2.2} />
          </span>
          {refreshing ? "Actualizando…" : "Actualizar información"}
        </button>
      )}

      <button
        type="button"
        className="theme-btn"
        onClick={toggle}
        aria-pressed={theme === "light"}
        title={theme === "light" ? "Cambiar a tema oscuro" : "Cambiar a tema claro"}
      >
        <span aria-hidden="true">
          {theme === "light" ? <Sun size={13} strokeWidth={2.2} /> : <Moon size={13} strokeWidth={2.2} />}
        </span>
        {theme === "light" ? "Tema claro" : "Tema oscuro"}
      </button>
    </header>
  );
}
