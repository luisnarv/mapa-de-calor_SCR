"use client";

import React from "react";
import { AlertTriangle, ChevronDown, Moon, RefreshCw, Sun, X } from "lucide-react";

import { useTheme } from "@/lib/theme";
import FiltroPildora from "@/components/FiltroPildora";

/**
 * Cuántos días tienen los datos que se están viendo.
 *
 * Se mide contra `meta.generated` —cuándo los generó el ETL— y no contra el
 * último refresco: pulsar el botón y que diga «actualizado hace 1 minuto» sería
 * mentir, porque las cifras pueden seguir siendo de hace una semana. La pregunta
 * que se responde aquí es «¿esto está viejo?», no «¿cuándo pregunté?».
 *
 * `alerta` se enciende a partir del segundo día: un aviso permanente deja de
 * avisar.
 */
function edadDeLosDatos(generated) {
  if (!generated) return null;
  const gen = new Date(`${generated}T00:00:00`);
  if (isNaN(gen.getTime())) return null;

  const hoy = new Date();
  hoy.setHours(0, 0, 0, 0);
  const dias = Math.round((hoy.getTime() - gen.getTime()) / 86400000);
  const exacta = gen.toLocaleDateString("es-CO", {
    day: "numeric",
    month: "long",
    year: "numeric"
  });

  if (dias <= 0) return { txt: "Hoy", exacta, alerta: false };
  if (dias === 1) return { txt: "Ayer", exacta, alerta: false };
  return { txt: `Hace ${dias} días`, exacta, alerta: true };
}

/** «2026-01-01» → «01 ene». Para el contexto de la barra, donde el año sobra. */
function diaCorto(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("es-CO", { day: "2-digit", month: "short" }).replace(".", "");
}

export default function Topbar({
  st,
  dim,
  avail,
  onFilterChange,
  onReset,
  availableMonths,
  loadingMonths = [],
  onRefresh,
  refreshing = false,
  refreshResult = null
}) {
  const { theme, toggle } = useTheme();

  const [monthsOpen, setMonthsOpen] = React.useState(false);
  const mesesRef = React.useRef(null);

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

  // El desplegable de meses es multi-selección, así que conserva su propia lista
  // de casillas; solo el disparador se viste de píldora como los demás.
  React.useEffect(() => {
    if (!monthsOpen) return;
    const fuera = (e) => {
      if (!mesesRef.current?.contains(e.target)) setMonthsOpen(false);
    };
    const esc = (e) => e.key === "Escape" && setMonthsOpen(false);
    document.addEventListener("mousedown", fuera, true);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fuera, true);
      document.removeEventListener("keydown", esc);
    };
  }, [monthsOpen]);

  // Los meses solo cuentan como filtro puesto si no son los de arranque: el
  // tablero abre con el mes en curso y marcarlo cada sesión sería ruido.
  const mesesPorDefecto = React.useMemo(() => {
    const reciente = availableMonths.find((m) => m.recent);
    return reciente ? [reciente.key] : availableMonths.map((m) => m.key);
  }, [availableMonths]);
  const mesesPuestos =
    activeCount > 0 &&
    (activeCount !== mesesPorDefecto.length ||
      !mesesPorDefecto.every((k) => activeMonths.includes(k)));

  // Solo se ofrecen las opciones que el resto de filtros deja con datos: elegir
  // una que devuelve cero órdenes no le sirve a nadie.
  const opcionesDe = (nombres, disponibles) =>
    nombres
      .map((texto, i) => ({ valor: i, texto }))
      .filter((o) => disponibles.has(o.valor));

  const barrioSel =
    st.selBarrio != null ? (dim.barrios[st.selBarrio] || "").split(" | ").pop() : null;

  const hayFiltros =
    st.zona !== "" || st.muni !== "" || st.brig !== "" || st.tipo !== "" ||
    mesesPuestos || barrioSel;

  const edad = edadDeLosDatos(st.generated);
  const estadoRefresco =
    refreshResult === "error" ? "No se pudo actualizar" : edad?.txt || "";

  return (
    <header id="top">
      {/* Una sola fila: identidad, filtros y utilidades. */}
      <div className="hd-id">
        <div className="hd-badge" aria-hidden="true">SCR</div>
        <div className="hd-title">
          <b>Centro operativo</b>
          <span>ISES · Air-E</span>
        </div>

        <div className="hd-ctx">
          {dim.barrios.length.toLocaleString("es-CO")} barrios ·{" "}
          {dim.tecs.length.toLocaleString("es-CO")} técnicos ·{" "}
          {diaCorto(st.fechaMin)} – {diaCorto(st.fechaMax)}
        </div>

        {/* Los filtros van en esta misma fila: como píldoras ya no necesitan una
            franja propia, y separarlos partía el header en dos. Cada una lleva
            dentro su estado, así que tampoco hay fila de chips aparte. */}
        <div className="hd-filtros">
          <FiltroPildora
            etiqueta="Zona"
            valor={st.zona}
            opciones={opcionesDe(dim.zonas, avail.zona)}
            onElegir={(v) => onFilterChange("zona", v)}
            vacio={`Todas las zonas (${avail.zona.size})`}
          />
          <FiltroPildora
            etiqueta="Municipio"
            valor={st.muni}
            opciones={opcionesDe(dim.munis, avail.muni)}
            onElegir={(v) => onFilterChange("muni", v)}
            vacio={`Todos los municipios (${avail.muni.size})`}
          />
          <FiltroPildora
            etiqueta="Brigada"
            valor={st.brig}
            opciones={opcionesDe(dim.brigs, avail.brig)}
            onElegir={(v) => onFilterChange("brig", v)}
            vacio={`Todas las brigadas (${avail.brig.size})`}
          />
          <FiltroPildora
            etiqueta="Tipo OS"
            valor={st.tipo}
            opciones={opcionesDe(dim.tipos, avail.tipo)}
            onElegir={(v) => onFilterChange("tipo", v)}
            vacio={`Todos los tipos (${avail.tipo.size})`}
          />

          <div className="hd-f" ref={mesesRef}>
            <button
              type="button"
              className={`hd-pill ${mesesPuestos ? "on" : ""}`}
              aria-haspopup="dialog"
              aria-expanded={monthsOpen}
              onClick={() => setMonthsOpen((v) => !v)}
            >
              <span className="hd-pill-k">Meses</span>
              <span className="hd-pill-v">: {monthSummary}</span>
              <span className="hd-pill-n">{activeCount}/{totalMonths}</span>
              <span className="hd-pill-ic" aria-hidden="true">
                <ChevronDown size={13} strokeWidth={2.4} />
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

          {/* El barrio no tiene desplegable propio —son 1.240— pero sí llega desde
              el mapa o el chat, y hasta ahora no había forma de ver que estaba
              puesto ni de soltarlo. */}
          {barrioSel && (
            <div className="hd-f">
              <button
                type="button"
                className="hd-pill on"
                onClick={() => onFilterChange("selBarrio", null)}
                aria-label={`Quitar el filtro de barrio ${barrioSel}`}
              >
                <span className="hd-pill-k">Barrio</span>
                <span className="hd-pill-v">: {barrioSel}</span>
                <span className="hd-pill-ic" aria-hidden="true">
                  <X size={13} strokeWidth={2.4} />
                </span>
              </button>
            </div>
          )}

          {hayFiltros && (
            <button type="button" className="hd-clear" onClick={onReset}>
              Limpiar
            </button>
          )}
        </div>

        {estadoRefresco && (
          <span
            className={`hd-fresh ${
              refreshResult === "error" || edad?.alerta ? "warn" : ""
            }`}
            title={edad ? `Datos generados el ${edad.exacta}` : undefined}
            aria-live="polite"
          >
            {(refreshResult === "error" || edad?.alerta) && (
              <AlertTriangle size={14} strokeWidth={2.2} aria-hidden="true" />
            )}
            {estadoRefresco}
          </span>
        )}

        {onRefresh && (
          <button
            type="button"
            className="hd-ico"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="Actualizar datos"
            title="Actualizar información"
          >
            <span
              className={`refresh-ic ${refreshing ? "spinning" : ""}`}
              aria-hidden="true"
            >
              <RefreshCw size={16} strokeWidth={2.2} />
            </span>
          </button>
        )}

        <button
          type="button"
          className="hd-ico"
          onClick={toggle}
          aria-pressed={theme === "light"}
          aria-label={theme === "light" ? "Cambiar a tema oscuro" : "Cambiar a tema claro"}
          title={theme === "light" ? "Cambiar a tema oscuro" : "Cambiar a tema claro"}
        >
          <span aria-hidden="true">
            {theme === "light" ? <Sun size={16} strokeWidth={2.2} /> : <Moon size={16} strokeWidth={2.2} />}
          </span>
        </button>
      </div>
    </header>
  );
}
