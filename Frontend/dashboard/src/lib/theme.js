"use client";

/**
 * Sistema de tema corporativo ISES.
 *
 * La paleta vive dos veces, a propósito:
 *  - en CSS (globals.css) para todo lo que se pinta con hojas de estilo;
 *  - aquí, en JS, para lo que se pinta en canvas / Leaflet, donde `var(--x)`
 *    no se resuelve (heatmap, puntos GPS, polígonos, tooltips inyectados).
 * Los valores deben mantenerse sincronizados con globals.css.
 */

import { useCallback, useSyncExternalStore } from "react";

export const STORAGE_KEY = "ises-theme";
export const THEME_EVENT = "ises-theme-change";

/* Colores de marca — únicos permitidos para rellenos */
export const BRAND = {
  verde: "#78BE20",
  lima: "#B5BD00",
  verdeMedio: "#509E2F",
  verdeOscuro: "#38764C",
  grisMedio: "#97999B",
  grisClaro: "#E0E0E0"
};

/* Tokens del modo oscuro — Identidad Navy (SIP): superficies azul medianoche,
   marca en azul cielo, acento financiero en verde menta. El verde queda
   reservado para «orden efectiva». */
export const DARK = {
  base: "#0A1A2F",          // azul medianoche profundo
  surface: "#102A4A",       // paneles
  card: "#0F2744",          // tarjetas
  elev: "#16355C",          // elevado
  line: "#1E3A5F",          // divisores
  borderStrong: "#2C5482",  // bordes de control
  text1: "#F2F7FF",         // títulos · blanco hielo
  text2: "#C9DAEE",         // cuerpo · azul acero claro
  text3: "#85A3C4",         // secundario
  text4: "#7E9BBF",         // atenuado · etiquetas y cifras de apoyo (AA sobre base Y paneles)
  acento: "#4A9EE8",        // marca SIP en oscuro · azul cielo
  activo: "#3B8AD9",        // marca presionada / borde de acento
  acentoFondo: "#143A63",   // fondo de acento tenue
  otc: "#2BD98C",           // módulo financiero · verde menta neón
  ok: "#2BD98C",
  alerta: "#F0C040",
  critico: "#FF5A5F",
  perdida: "#FF7A7E"        // variante de texto del crítico
};

/* Serie de datos de claro a oscuro + gris de referencia para totales.
   SERIES es solo para el modo claro (limas y verdes de marca). */
export const SERIES = [BRAND.lima, BRAND.verde, BRAND.verdeMedio, BRAND.verdeOscuro];
export const SERIE_REF = BRAND.grisMedio;

/* Serie del modo oscuro: sobre navy los verdes de marca se leen apagados y
   chocan con el semáforo, así que se declara aparte (azul cielo · menta ·
   ámbar · azul activo). */
export const SERIES_DARK = ["#4A9EE8", "#2BD98C", "#F0C040", "#3B8AD9"];

export const PALETTES = {
  light: {
    name: "light",
    bg: "#FFFFFF",
    pan: "#F4F6EF",
    pan2: "#EDF1E3",
    line: "#E0E0E0",
    line2: "#C9CFC0",
    text: "#3A3A3A",
    title: "#38764C",
    /* #97999B no alcanza AA sobre blanco: para TEXTO secundario se oscurece,
       el gris de marca se reserva para rellenos y series de referencia. */
    muted: "#5F6663",
    dim: "#6F7275",
    accent: BRAND.verde,
    accentText: BRAND.verdeOscuro,

    /* Estados — relleno (marca) y texto (contraste AA) */
    ok: BRAND.verdeMedio,
    warn: BRAND.lima,
    bad: "#C0392B",
    okText: "#3E7B24",
    warnText: "#6E7300",
    badText: "#A5301F",
    none: BRAND.grisMedio,
    /* barrios sin ninguna orden (NIC) enlazada — amarillo fuerte */
    sinNic: "#FFD500",

    /* Efectivas · Fallidas · Perdidas */
    st: [BRAND.verdeMedio, BRAND.lima, "#C0392B"],
    stText: ["#3E7B24", "#6E7300", "#A5301F"],
    /* tinta legible sobre cada relleno */
    stInk: ["#14201A", "#14201A", "#FFFFFF"],

    series: SERIES,
    serieRef: SERIE_REF,

    ink: "#FFFFFF", // texto sobre relleno de color fuerte
    inkSoft: "#14201A", // texto sobre relleno claro (lima)
    mapBg: "#EDF0E8",
    overlay: "rgba(255,255,255,.95)",
    overlayLine: "#C9CFC0",
    pointStroke: "rgba(255,255,255,.85)",
    markerSel: BRAND.verdeOscuro,
    limitStroke: BRAND.verdeOscuro,
    tiles: {
      base: "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
      labels: "https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png"
    },
    heat: [
      { 0.35: "#CFE3B4", 0.65: BRAND.verde, 1.0: BRAND.verdeOscuro },
      { 0.35: "#E8EAA8", 0.65: BRAND.lima, 1.0: "#7E8400" },
      { 0.35: "#F0BDB5", 0.65: "#D9584A", 1.0: "#8E2418" }
    ]
  },

  /* Modo oscuro — Identidad Navy (SIP): superficies azul medianoche, marca en
     azul cielo, acento en verde menta. El verde queda reservado para «efectiva»;
     los estados usan sus variantes claras sobre navy. */
  dark: {
    name: "dark",
    bg: DARK.base,
    pan: DARK.surface,
    pan2: DARK.card,
    elev: DARK.elev,
    line: DARK.line,
    line2: DARK.borderStrong,
    text: DARK.text1,
    title: DARK.text1,
    muted: DARK.text2,
    dim: DARK.text3,
    accent: DARK.acento,
    accentText: DARK.acento,
    accentStrong: DARK.activo,
    accentDeep: DARK.acentoFondo,

    ok: DARK.ok,
    warn: DARK.alerta,
    bad: DARK.critico,
    okText: DARK.ok,
    warnText: DARK.alerta,
    badText: DARK.perdida,
    none: DARK.text3,
    /* barrios sin ninguna orden (NIC) enlazada — amarillo fuerte */
    sinNic: "#FFD500",

    st: [DARK.ok, DARK.alerta, DARK.critico],
    stText: [DARK.ok, DARK.alerta, DARK.perdida],
    /* los tres rellenos son claros: la tinta va sobre la base (guía, lámina 6) */
    stInk: [DARK.base, DARK.base, DARK.base],

    series: SERIES_DARK,
    serieRef: DARK.text3,

    ink: "#FFFFFF",
    inkSoft: DARK.base,
    /* el mapa comparte el fondo base (regla 4 de la guía) */
    mapBg: DARK.base,
    overlay: "rgba(10,26,47,.94)",
    overlayLine: DARK.line,
    pointStroke: "rgba(10,26,47,.75)",
    markerSel: DARK.text1,
    markerHalo: DARK.acento,
    limitStroke: DARK.acento,
    tiles: {
      base: "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
      labels: "https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png"
    },
    heat: [
      { 0.35: "#0E3A2C", 0.65: "#1E8A64", 1.0: "#2BD98C" },
      { 0.35: "#5C4712", 0.65: "#C09526", 1.0: "#F0C040" },
      { 0.35: "#7A1A1E", 0.65: "#C93B41", 1.0: "#FF5A5F" }
    ]
  }
};

export const DEFAULT_THEME = "dark";

/* ---------- store mínimo, sincronizado con <html data-theme> ---------- */

function readDom() {
  if (typeof document === "undefined") return DEFAULT_THEME;
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

let current = DEFAULT_THEME;

function getSnapshot() {
  current = readDom();
  return current;
}

function getServerSnapshot() {
  return DEFAULT_THEME;
}

function subscribe(cb) {
  window.addEventListener(THEME_EVENT, cb);
  return () => window.removeEventListener(THEME_EVENT, cb);
}

export function applyTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  document.documentElement.style.colorScheme = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* almacenamiento no disponible: el tema simplemente no persiste */
  }
  window.dispatchEvent(new Event(THEME_EVENT));
  return next;
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const setTheme = useCallback((t) => applyTheme(t), []);
  const toggle = useCallback(() => applyTheme(readDom() === "light" ? "dark" : "light"), []);
  return { theme, palette: PALETTES[theme] || PALETTES[DEFAULT_THEME], setTheme, toggle };
}

/* ---------- helpers de color de datos ---------- */

/** Color del índice de riesgo (0–100). `text: true` devuelve la variante con contraste AA. */
export function riskColor(p, r, text = false) {
  if (r == null) return p.none;
  const band = r >= 61 ? 2 : r >= 31 ? 1 : 0;
  return text ? p.stText[band] : p.st[band];
}

/** Color de tinta legible sobre un relleno de riesgo. */
export function riskInk(p, r) {
  if (r == null) return p.ink;
  return p.stInk[r >= 61 ? 2 : r >= 31 ? 1 : 0];
}
