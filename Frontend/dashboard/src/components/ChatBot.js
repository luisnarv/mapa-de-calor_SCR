"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  Crosshair,
  Maximize2,
  MessageSquare,
  Mic,
  Minus,
  Pin,
  PinOff,
  Send,
  ThumbsDown,
  ThumbsUp,
  X
} from "lucide-react";

const BUBBLE = 56;
const MARGIN = 20;
const PANEL_W = 380;
const PANEL_MAX_H = 640;
const DRAG_SLOP = 4;
// Debe coincidir con la animación `cb-out` de globals.css.
const CLOSE_MS = 160;

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const STREAM_ENDPOINT = `${API_URL}/api/v1/openai/chat/stream`;

// PENDIENTE: los votos solo viven en la pantalla. Falta el endpoint que los
// guarde; hasta entonces se pierden al recargar.
//
// Cada motivo se arregla en un lugar distinto: "el dato está mal" es una
// propuesta para la taxonomía del ETL; los otros tres son fallas del agente.
const MOTIVOS = [
  { id: "dato_incorrecto", txt: "El dato está mal" },
  { id: "no_entendio", txt: "No entendió" },
  { id: "filtro_incorrecto", txt: "Filtró mal el mapa" },
  { id: "mal_redactado", txt: "Mal redactado" }
];

const CHIPS = ["Barrios críticos", "Causas de pérdida", "Rendimiento por brigada"];

const SALUDO = {
  role: "assistant",
  content: "Hola. En qué puedo ayudarte hoy?"
};

// Se van mostrando en orden mientras no llegue el primer trozo de respuesta.
const PENSANDO = [
  "Pensando…",
  "Analizando la consulta…",
  "Revisando las órdenes…",
  "Cruzando los datos del tablero…",
  "Redactando la respuesta…",
  "Sigo en ello, dame un momento…"
];
const PENSANDO_MS = 2800;

// `.` no cruza saltos de línea a propósito: si el modelo deja un ** suelto, se
// come una palabra y no el resto del mensaje.
const NEGRITA = /\*\*(.+?)\*\*/g;

/**
 * Pone en negrita los `**...**` que devuelve el modelo.
 *
 * Devuelve nodos de React, nunca HTML: el texto viene de un modelo de lenguaje
 * y con `dangerouslySetInnerHTML` cualquier etiqueta que escupiera se ejecutaría.
 * Mientras llega el streaming, un `**` sin cerrar se ve literal hasta que cierra.
 */
function conNegritas(texto) {
  const nodos = [];
  let cursor = 0;

  for (const m of texto.matchAll(NEGRITA)) {
    if (m.index > cursor) nodos.push(texto.slice(cursor, m.index));
    nodos.push(<strong key={m.index}>{m[1]}</strong>);
    cursor = m.index + m[0].length;
  }
  if (!nodos.length) return texto;
  if (cursor < texto.length) nodos.push(texto.slice(cursor));
  return nodos;
}

/** Convierte el filtro del backend en una línea legible. Null si no filtra nada. */
function resumirFiltro(accion) {
  const partes = [
    accion.barrio,
    accion.municipio,
    accion.zona,
    accion.brigada,
    accion.tipo_os,
    accion.meses?.join(", ")
  ].filter(Boolean);
  return partes.length ? partes.join(" · ") : null;
}

/**
 * Pulgares bajo una respuesta. El pulgar abajo pregunta *por qué*: un voto
 * negativo suelto no dice si falló el dato, la comprensión o la redacción, y
 * cada una se corrige en un sitio distinto.
 */
function Calificar({ voto, onVotar }) {
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState(null);
  const [comentario, setComentario] = useState("");

  if (voto) {
    return (
      <p className="cb-voto-ok">
        {voto === "util" ? "Gracias." : "Gracias, queda registrado para revisión."}
      </p>
    );
  }

  if (!abierto) {
    return (
      <div className="cb-voto">
        <button type="button" onClick={() => onVotar("util")} title="Respuesta útil"
          aria-label="Marcar la respuesta como útil">
          <ThumbsUp size={13} strokeWidth={2.2} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => setAbierto(true)} title="Respuesta inútil"
          aria-label="Reportar un problema con la respuesta">
          <ThumbsDown size={13} strokeWidth={2.2} aria-hidden="true" />
        </button>
      </div>
    );
  }

  return (
    <div className="cb-voto-form">
      <span className="cb-voto-t">¿Qué falló?</span>

      <div className="cb-voto-chips">
        {MOTIVOS.map((m) => (
          <button
            key={m.id}
            type="button"
            className={motivo === m.id ? "on" : ""}
            aria-pressed={motivo === m.id}
            onClick={() => setMotivo(m.id)}
          >
            {m.txt}
          </button>
        ))}
      </div>

      <input
        type="text"
        value={comentario}
        onChange={(e) => setComentario(e.target.value)}
        aria-label="Detalle del problema"
        placeholder={
          motivo === "dato_incorrecto"
            ? "¿Qué debería decir?"
            : "Detalle (opcional)"
        }
      />

      <div className="cb-voto-acts">
        <button type="button" onClick={() => setAbierto(false)}>
          Cancelar
        </button>
        <button
          type="button"
          className="primary"
          disabled={!motivo}
          onClick={() => onVotar("inutil", motivo, comentario)}
        >
          Enviar
        </button>
      </div>
    </div>
  );
}

/** Etiqueta de espera. Avanza por las frases y se queda en la última. */
function Pensando() {
  const [i, setI] = useState(0);

  useEffect(() => {
    if (i >= PENSANDO.length - 1) return;
    const id = setTimeout(() => setI((n) => n + 1), PENSANDO_MS);
    return () => clearTimeout(id);
  }, [i]);

  return (
    <div className="cb-msg bot cb-thinking">
      <span key={i} className="cb-thinking-t">
        {PENSANDO[i]}
      </span>
    </div>
  );
}

/**
 * @param {{ onAccion?: (accion: object) => void }} props
 *   `onAccion` recibe los filtros que el backend pide aplicar al tablero.
 */
export default function ChatBot({ onAccion }) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [draft, setDraft] = useState("");

  const [messages, setMessages] = useState([SALUDO]);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [filtroAplicado, setFiltroAplicado] = useState(null);

  const [escuchando, setEscuchando] = useState(false);
  const [soportaVoz, setSoportaVoz] = useState(false);
  const [errorVoz, setErrorVoz] = useState(null);

  const [panelPos, setPanelPos] = useState(null);
  const [panelDragging, setPanelDragging] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [minimized, setMinimized] = useState(false);

  const [place, setPlace] = useState(() => {
    if (typeof window === "undefined") return null;
    return { x: window.innerWidth - BUBBLE - MARGIN, y: window.innerHeight - BUBBLE - MARGIN };
  });

  const anclaX = panelPos ? panelPos.x + PANEL_W / 2 : place ? place.x + BUBBLE / 2 : null;
  const side =
    anclaX !== null && typeof window !== "undefined" && anclaX < window.innerWidth / 2
      ? "left"
      : "right";

  const launcherRef = useRef(null);
  const panelRef = useRef(null);
  const bodyRef = useRef(null);
  const abortRef = useRef(null);
  const closeTimer = useRef(null);
  const dragRef = useRef({ dx: 0, dy: 0, ox: 0, oy: 0, moved: false });
  const panelDragRef = useRef({ dx: 0, dy: 0, w: 0, h: 0 });

  const vozRef = useRef(null);
  const previoRef = useRef("");

  // En un ref para que `send` no se recree cada vez que cambie el callback.
  const accionRef = useRef(onAccion);
  useEffect(() => {
    accionRef.current = onAccion;
  }, [onAccion]);

  // El reconocimiento de voz se crea una vez. En Firefox no existe y el botón
  // simplemente no se dibuja: un botón permanentemente inhabilitado no ayuda.
  useEffect(() => {
    const Reconocimiento = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Reconocimiento) return;

    const rec = new Reconocimiento();
    rec.lang = "es-CO";
    rec.continuous = false;
    rec.interimResults = true; // el texto aparece mientras hablas

    rec.onresult = (e) => {
      let dicho = "";
      for (let i = 0; i < e.results.length; i++) dicho += e.results[i][0].transcript;
      setDraft(`${previoRef.current} ${dicho}`.trim());
    };
    rec.onerror = (e) => {
      setEscuchando(false);
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setErrorVoz("Falta el permiso del micrófono.");
      } else if (e.error === "no-speech") {
        setErrorVoz("No escuché nada.");
      } else if (e.error !== "aborted") {
        setErrorVoz("No pude usar el micrófono.");
      }
    };
    rec.onend = () => setEscuchando(false);

    vozRef.current = rec;
    setSoportaVoz(true);
    return () => {
      rec.onresult = rec.onerror = rec.onend = null;
      rec.abort();
    };
  }, []);

  const alternarVoz = useCallback(() => {
    const rec = vozRef.current;
    if (!rec) return;
    if (escuchando) {
      rec.stop();
      return;
    }
    setErrorVoz(null);
    previoRef.current = draft;
    try {
      rec.start();
      setEscuchando(true);
    } catch {
      // start() lanza si ya estaba activo; el estado se corrige con onend.
    }
  }, [escuchando, draft]);

  // Cerrar o plegar el panel con el micrófono abierto lo dejaría grabando.
  useEffect(() => {
    if (!open || minimized) vozRef.current?.abort();
  }, [open, minimized]);

  const openPanel = useCallback(() => {
    clearTimeout(closeTimer.current);
    setClosing(false);
    setOpen(true);
  }, []);

  /** Marca la salida y desmonta cuando la animación termina, no antes. */
  const closePanel = useCallback(() => {
    setClosing(true);
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => {
      setOpen(false);
      setClosing(false);
    }, CLOSE_MS);
  }, []);

  /** La burbuja se queda donde la sueltes; solo se impide que salga del viewport. */
  const clamp = (x, y) => {
    const w = window.innerWidth;
    const h = window.innerHeight;
    return {
      x: Math.min(Math.max(x, MARGIN), w - BUBBLE - MARGIN),
      y: Math.min(Math.max(y, MARGIN), h - BUBBLE - MARGIN)
    };
  };

  /** Igual que `clamp`, pero para una caja del tamaño del panel. */
  const clampPanel = (x, y, w, h) => ({
    x: Math.max(MARGIN, Math.min(x, window.innerWidth - w - MARGIN)),
    y: Math.max(MARGIN, Math.min(y, window.innerHeight - h - MARGIN))
  });

  useEffect(() => {
    const onResize = () => {
      setPlace((prev) => (prev ? clamp(prev.x, prev.y) : prev));
      setPanelPos((prev) => {
        const el = panelRef.current;
        if (!prev || !el) return prev;
        const r = el.getBoundingClientRect();
        return clampPanel(prev.x, prev.y, r.width, r.height);
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") closePanel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, closePanel]);

  // Corta el streaming y el temporizador de cierre si el componente se desmonta.
  useEffect(
    () => () => {
      abortRef.current?.abort();
      clearTimeout(closeTimer.current);
    },
    []
  );

  // El hilo siempre pegado abajo mientras entra texto.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open, minimized]);

  /** Añade texto al último mensaje del asistente, que es el que se está escribiendo. */
  const appendToLast = useCallback((delta) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, content: last.content + delta };
      return next;
    });
  }, []);

  const votar = useCallback((indice, voto, motivo = null, comentario = "") => {
    // Solo estado local por ahora: no hay dónde guardarlo.
    setMessages((prev) =>
      prev.map((m, i) => (i === indice ? { ...m, voto, motivo, comentario } : m))
    );
  }, []);

  const send = useCallback(
    async (text) => {
      const pregunta = text.trim();
      if (!pregunta || busy) return;

      // Lo que viaja al backend: el historial más la pregunta nueva.
      const historial = [...messages, { role: "user", content: pregunta }];
      setMessages([...historial, { role: "assistant", content: "" }]);
      setDraft("");
      setBusy(true);
      setOffline(false);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await fetch(STREAM_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: historial.map(({ role, content }) => ({ role, content }))
          }),
          signal: controller.signal
        });

        if (!res.ok || !res.body) {
          throw new Error(`El servidor respondió ${res.status}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        // SSE: los eventos llegan separados por una línea en blanco.
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const eventos = buffer.split("\n\n");
          buffer = eventos.pop() ?? "";

          for (const evento of eventos) {
            const linea = evento.trim();
            if (!linea.startsWith("data:")) continue;

            const dato = linea.slice(5).trim();
            if (dato === "[DONE]") continue;

            const payload = JSON.parse(dato);
            if (payload.error) throw new Error(payload.error);
            if (payload.delta) appendToLast(payload.delta);
            // El backend resolvió una consulta: que el tablero se filtre solo.
            if (payload.accion) {
              accionRef.current?.(payload.accion);
              setFiltroAplicado(resumirFiltro(payload.accion));
            }
          }
        }
      } catch (err) {
        if (err.name === "AbortError") return;
        setOffline(true);
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          // Si no alcanzó a llegar nada, la burbuja vacía se convierte en el aviso.
          next[next.length - 1] = {
            ...last,
            content: last.content || `No pude responder: ${err.message}.`
          };
          return next;
        });
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [appendToLast, busy, messages]
  );

  const onPointerDown = useCallback((e) => {
    if (e.button != null && e.button !== 0) return;
    const el = launcherRef.current;
    if (!el) return;
    // Se parte de la caja real: mientras está indexada, el transform la desplaza.
    const r = el.getBoundingClientRect();
    dragRef.current = {
      dx: e.clientX - r.left,
      dy: e.clientY - r.top,
      ox: e.clientX,
      oy: e.clientY,
      moved: false
    };
    el.setPointerCapture(e.pointerId);
    setPlace({ x: r.left, y: r.top });
    setDragging(true);
  }, []);

  const onPointerMove = useCallback(
    (e) => {
      if (!dragging) return;
      const d = dragRef.current;
      if (!d.moved && (Math.abs(e.clientX - d.ox) > DRAG_SLOP || Math.abs(e.clientY - d.oy) > DRAG_SLOP)) {
        d.moved = true;
      }
      setPlace(clamp(e.clientX - d.dx, e.clientY - d.dy));
    },
    [dragging]
  );

  const endDrag = useCallback((e) => {
    const el = launcherRef.current;
    if (el && e.pointerId != null && el.hasPointerCapture?.(e.pointerId)) {
      el.releasePointerCapture(e.pointerId);
    }
    setDragging(false);
    if (!dragRef.current.moved) openPanel();
  }, [openPanel]);

  // --- Arrastre del panel abierto, tomándolo por la cabecera ------------------

  const onHeadPointerDown = useCallback(
    (e) => {
      if (pinned || (e.button != null && e.button !== 0)) return;
      // Los botones de la cabecera no arrastran: son clics.
      if (e.target.closest("button")) return;

      const el = panelRef.current;
      if (!el) return;

      const r = el.getBoundingClientRect();
      panelDragRef.current = {
        dx: e.clientX - r.left,
        dy: e.clientY - r.top,
        w: r.width,
        h: r.height
      };
      // Fija la posición actual antes de moverla: hasta ahora era automática.
      setPanelPos({ x: r.left, y: r.top });
      e.currentTarget.setPointerCapture(e.pointerId);
      setPanelDragging(true);
    },
    [pinned]
  );

  const onHeadPointerMove = useCallback(
    (e) => {
      if (!panelDragging) return;
      const d = panelDragRef.current;
      setPanelPos(clampPanel(e.clientX - d.dx, e.clientY - d.dy, d.w, d.h));
    },
    [panelDragging]
  );

  const endHeadDrag = useCallback((e) => {
    const el = e.currentTarget;
    if (el && e.pointerId != null && el.hasPointerCapture?.(e.pointerId)) {
      el.releasePointerCapture(e.pointerId);
    }
    setPanelDragging(false);
  }, []);

  /** Al plegar, congela la posición actual para que la barra no salte. */
  const toggleMinimized = useCallback(() => {
    setPanelPos((prev) => {
      if (prev) return prev;
      const r = panelRef.current?.getBoundingClientRect();
      return r ? { x: r.left, y: r.top } : prev;
    });
    setMinimized((m) => !m);
  }, []);

  const panelStyle = () => {
    if (typeof window === "undefined") return { right: MARGIN, bottom: MARGIN };

    const w = window.innerWidth;
    const h = window.innerHeight;
    const width = Math.min(PANEL_W, w - MARGIN * 2);
    const height = Math.min(PANEL_MAX_H, h - MARGIN * 2);
    // Plegado: la altura la marca la cabecera, así que se deja libre.
    const size = { width, height: minimized ? "auto" : height };

    if (panelPos) return { left: panelPos.x, top: panelPos.y, ...size };
    if (!place) return { right: MARGIN, bottom: MARGIN, ...size };

    const top = Math.min(Math.max(place.y + BUBBLE / 2 - height / 2, MARGIN), h - height - MARGIN);
    const left = side === "left" ? MARGIN : w - width - MARGIN;
    return { left, top, ...size };
  };

  const launcherStyle = place
    ? { left: place.x, top: place.y }
    : { right: MARGIN, bottom: MARGIN };

  const esperandoPrimerTrozo = busy && messages[messages.length - 1]?.content === "";

  return (
    <>
      {!open && (
        <button
          ref={launcherRef}
          type="button"
          className={`cb-launcher ${dragging ? "dragging" : ""}`}
          style={launcherStyle}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          aria-label="Abrir el asistente SCR"
          title="Asistente SCR · arrástrame donde quieras"
        >
          <MessageSquare size={22} strokeWidth={2} aria-hidden="true" />
          <span className="cb-launcher-dot" aria-hidden="true"></span>
        </button>
      )}

      {open && (
        <section
          ref={panelRef}
          className={`cb-panel ${side === "left" ? "from-l" : "from-r"} ${
            closing ? "closing" : ""
          } ${minimized ? "min" : ""}`}
          style={panelStyle()}
          role="dialog"
          aria-label="Asistente SCR"
        >
          <header
            className={`cb-head ${pinned ? "pinned" : ""} ${panelDragging ? "dragging" : ""}`}
            onPointerDown={onHeadPointerDown}
            onPointerMove={onHeadPointerMove}
            onPointerUp={endHeadDrag}
            onPointerCancel={endHeadDrag}
            title={pinned ? "Fijado en su sitio" : "Arrástrame para mover el asistente"}
          >
            <span className="cb-head-ic" aria-hidden="true">
              <Bot size={18} strokeWidth={2} />
            </span>
            <div className="cb-head-t">
              <b>Asistente SCR</b>
              <span>
                <i className="cb-live" aria-hidden="true"></i>
                {offline ? "Sin conexión con el servidor" : busy ? "Escribiendo…" : "En línea"}
              </span>
            </div>

            <div className="cb-head-acts">
              <button
                type="button"
                className={`cb-x ${pinned ? "on" : ""}`}
                onClick={() => setPinned((p) => !p)}
                aria-pressed={pinned}
                aria-label={pinned ? "Soltar el asistente" : "Fijar el asistente"}
                title={pinned ? "Soltar: vuelve a moverse" : "Fijar: no se podrá mover"}
              >
                {pinned ? (
                  <PinOff size={13} strokeWidth={2.2} aria-hidden="true" />
                ) : (
                  <Pin size={13} strokeWidth={2.2} aria-hidden="true" />
                )}
              </button>

              <button
                type="button"
                className="cb-x"
                onClick={toggleMinimized}
                aria-expanded={!minimized}
                aria-label={minimized ? "Restaurar el asistente" : "Minimizar el asistente"}
                title={minimized ? "Restaurar" : "Minimizar"}
              >
                {minimized ? (
                  <Maximize2 size={12} strokeWidth={2.4} aria-hidden="true" />
                ) : (
                  <Minus size={14} strokeWidth={2.6} aria-hidden="true" />
                )}
              </button>

              <button
                type="button"
                className="cb-x"
                onClick={closePanel}
                aria-label="Cerrar el asistente"
                title="Cerrar"
              >
                <X size={14} strokeWidth={2.5} aria-hidden="true" />
              </button>
            </div>
          </header>

          {!minimized && (
            <>
              <div className="cb-body" ref={bodyRef} aria-live="polite">
                {messages.length === 1 && (
                  <div className="cb-chips">
                    {CHIPS.map((c) => (
                      <button
                        key={c}
                        type="button"
                        className="cb-chip"
                        disabled={busy}
                        onClick={() => send(c)}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                )}

                {messages.map((m, i) => {
                  const ultimo = i === messages.length - 1;
                  if (ultimo && esperandoPrimerTrozo) return <Pensando key={i} />;
                  // El saludo (índice 0) no es una respuesta: no hay qué calificar.
                  const calificable =
                    m.role === "assistant" && i > 0 && !(ultimo && busy);
                  return (
                    <React.Fragment key={i}>
                      <div className={`cb-msg ${m.role === "user" ? "me" : "bot"}`}>
                        {conNegritas(m.content)}
                      </div>
                      {calificable && (
                        <Calificar
                          voto={m.voto}
                          onVotar={(v, mo, co) => votar(i, v, mo, co)}
                        />
                      )}
                    </React.Fragment>
                  );
                })}

                {filtroAplicado && (
                  <p className="cb-filtro">
                    <Crosshair size={12} strokeWidth={2.2} aria-hidden="true" />
                    Mapa filtrado: <b>{filtroAplicado}</b>
                  </p>
                )}
              </div>

              <footer className="cb-foot">
                <form
                  className="cb-input"
                  onSubmit={(e) => {
                    e.preventDefault();
                    vozRef.current?.abort();
                    send(draft);
                  }}
                >
                  <input
                    type="text"
                    value={draft}
                    onChange={(e) => {
                      setDraft(e.target.value);
                      if (errorVoz) setErrorVoz(null); // si ya está escribiendo, sobra el aviso
                    }}
                    placeholder={
                      escuchando ? "Escuchando…" : "Escribe una consulta sobre el mapa…"
                    }
                    aria-label="Consulta para el asistente"
                    disabled={busy}
                  />

                  {soportaVoz && (
                    <button
                      type="button"
                      className={`cb-mic ${escuchando ? "on" : ""}`}
                      onClick={alternarVoz}
                      disabled={busy}
                      aria-pressed={escuchando}
                      aria-label={escuchando ? "Detener el dictado" : "Dictar por voz"}
                      title={escuchando ? "Detener" : "Dictar por voz"}
                    >
                      <Mic size={15} strokeWidth={2.2} aria-hidden="true" />
                    </button>
                  )}

                  <button
                    type="submit"
                    className="cb-send"
                    disabled={busy || !draft.trim()}
                    aria-label="Enviar consulta"
                  >
                    <Send size={15} strokeWidth={2.2} aria-hidden="true" />
                  </button>
                </form>
                <p className={`cb-legal ${errorVoz ? "err" : ""}`}>
                  {errorVoz ||
                    "Las respuestas las genera un modelo de lenguaje. Verifica antes de operar."}
                </p>
              </footer>
            </>
          )}
        </section>
      )}
    </>
  );
}
