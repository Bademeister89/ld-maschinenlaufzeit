// Gemeinsame Helfer für Live- und Auswertungsseite.
// Alle Texte aus Daten (Programmnamen, Fehlermeldungen) werden per textContent gesetzt.

export const STATE_ORDER = ["RUNNING", "STOPPED", "ERROR", "READY", "OFFLINE", "UNKNOWN", "NO_DATA"];
const STATE_ICONS = { RUNNING: "▶", STOPPED: "❚❚", ERROR: "!", READY: "■", OFFLINE: "○", UNKNOWN: "?", NO_DATA: "–", NETWORK: "✕" };

let meta = null;

async function errorFrom(res) {
  let detail = res.statusText;
  try {
    const body = await res.json();
    detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(", ") : body.detail ?? detail;
  } catch {}
  return new Error(detail);
}

export async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const res = await fetch(url);
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

/** Schreibende Anfrage: JSON-Objekt oder Blob (Bild) als Body. */
export async function send(method, path, body) {
  const options = { method, headers: {} };
  if (body instanceof Blob) {
    options.body = body;
    options.headers["Content-Type"] = body.type || "application/octet-stream";
  } else if (body !== undefined) {
    options.body = JSON.stringify(body);
    options.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, options);
  if (!res.ok) throw await errorFrom(res);
  return res.status === 204 ? null : res.json();
}

export async function loadMeta() {
  meta ??= await api("/api/meta");
  const sim = document.getElementById("sim");
  if (sim) sim.hidden = !meta.simulate;
  return meta;
}

const tz = () => meta?.timezone ?? "Europe/Berlin";

export const stateLabel = (s) => (s ? meta?.labels.state[s] ?? s : "Verbinde …");
export const execLabel = (m) => (m ? meta?.labels.exec_mode[m] ?? m : "—");
export const pgmStateLabel = (p) => (p ? meta?.labels.pgm_state[p] ?? p : "—");
export const resultLabel = (r) => (r ? meta?.labels.run_result[r] ?? r : "läuft noch");
export const machineName = (id) => meta?.machines.find((m) => m.id === id)?.name ?? id;

// --- DOM ----------------------------------------------------------------------

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function stateBadge(state) {
  const cls = `st-${state ?? "UNKNOWN"}`;
  return el(
    "span",
    { class: `state-badge ${cls}` },
    el("span", { class: `state-icon ${cls}`, "aria-hidden": "true", text: STATE_ICONS[state] ?? "…" }),
    el("span", { text: stateLabel(state) }),
  );
}

/** Maschinenbild oder – ohne Bild – ein Kürzel aus dem Namen. */
export function machineThumb(machine, size = "") {
  const cls = `thumb ${size}`.trim();
  if (machine.image_url) return el("img", { class: cls, src: machine.image_url, alt: "" });
  const initials = machine.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
  return el("span", { class: `${cls} thumb-placeholder`, "aria-hidden": "true", text: initials || "?" });
}

export function legend(states = STATE_ORDER) {
  return el(
    "div",
    { class: "legend" },
    states.map((s) => el("span", {}, el("span", { class: `swatch st-${s}` }), stateLabel(s))),
  );
}

// --- Formatierung -------------------------------------------------------------

const nf1 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export function fmtDuration(sec) {
  if (sec == null || !Number.isFinite(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")} min`;
}

export const fmtHours = (sec) => (sec == null ? "—" : `${nf1.format(sec / 3600)} h`);
export const fmtInt = (n) => (n == null ? "—" : Math.round(n).toLocaleString("de-DE"));
export const fmtPct = (x) => (x == null ? "—" : `${Math.round(x * 100)} %`);

export function fmtTime(t, seconds = false) {
  return new Intl.DateTimeFormat("de-DE", {
    timeZone: tz(),
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
  }).format(t * 1000);
}

export function fmtDateTime(t) {
  if (t == null) return "—";
  return new Intl.DateTimeFormat("de-DE", {
    timeZone: tz(),
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(t * 1000);
}

/** "2026-09-21" → "Mo 21.09." */
export function fmtDay(isoDate) {
  const d = new Date(`${isoDate}T12:00:00Z`);
  const wd = new Intl.DateTimeFormat("de-DE", { weekday: "short", timeZone: "UTC" }).format(d).replace(".", "");
  const dm = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(d);
  return `${wd} ${dm}`;
}

export const baseName = (path) => (path ? path.split(/[\\/]/).pop() : null);

export function startOfDay(date = new Date()) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

export function addDays(date, days) {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

export const epoch = (date) => date.getTime() / 1000;

// --- Tooltip -----------------------------------------------------------------

class Tooltip {
  constructor() {
    this.node = el("div", { class: "tooltip", role: "tooltip" });
    document.body.append(this.node);
  }

  show(point, content) {
    this.node.replaceChildren(...content);
    this.node.style.display = "block";
    const pad = 14;
    const r = this.node.getBoundingClientRect();
    let x = point.clientX + pad;
    let y = point.clientY + pad;
    if (x + r.width > innerWidth - 8) x = point.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = point.clientY - r.height - pad;
    this.node.style.left = `${Math.max(8, x)}px`;
    this.node.style.top = `${Math.max(8, y)}px`;
  }

  hide() {
    this.node.style.display = "none";
  }

  /** Hover/Fokus für alle Elemente im Container, die eine _tip()-Funktion tragen. */
  bind(container) {
    const find = (target) => target.closest?.("[data-tip]");
    container.addEventListener("pointermove", (e) => {
      const node = find(e.target);
      node?._tip ? this.show(e, node._tip()) : this.hide();
    });
    container.addEventListener("pointerleave", () => this.hide());
    container.addEventListener("focusin", (e) => {
      const node = find(e.target);
      if (!node?._tip) return;
      const r = node.getBoundingClientRect();
      this.show({ clientX: r.left + r.width / 2, clientY: r.bottom }, node._tip());
    });
    container.addEventListener("focusout", () => this.hide());
  }
}

let tooltipInstance = null;
export const tooltip = () => (tooltipInstance ??= new Tooltip());

export function tipContent(state, value, lines = []) {
  return [
    el("div", { class: "tt-value", text: value }),
    el("div", { class: `tt-row st-${state}` }, el("span", { class: "tt-key" }), el("span", { text: stateLabel(state) })),
    ...lines.filter(Boolean).map((line) => el("div", { class: "tt-row", text: line })),
  ];
}

// --- Zeitleiste ----------------------------------------------------------------

/** Aufeinanderfolgende Intervalle mit gleichem Zustand und Programm zusammenfassen. */
export function mergeIntervals(intervals) {
  const out = [];
  for (const iv of intervals) {
    const last = out.at(-1);
    if (last && last.state === iv.state && last.program === iv.program && iv.start - last.end < 1) {
      last.end = iv.end;
    } else {
      out.push({ ...iv });
    }
  }
  return out;
}

const GAP_MIN_S = 30; // kürzere Lücken sind Abfrage-Rauschen

/** Lücken zwischen Intervallen (bis ``until``) als Zustand "Keine Daten" ergänzen. */
function withGaps(intervals, t0, until) {
  const out = [];
  let cursor = t0;
  for (const iv of intervals) {
    if (iv.start - cursor > GAP_MIN_S) out.push({ state: "NO_DATA", start: cursor, end: iv.start, program: null });
    out.push(iv);
    cursor = Math.max(cursor, iv.end);
  }
  if (until - cursor > GAP_MIN_S) out.push({ state: "NO_DATA", start: cursor, end: until, program: null });
  return out;
}

/** Zustandsband für den Bereich [t0, t1]. Nach ``now`` (Zukunft) bleibt die Spur leer. */
export function timelineTrack(intervals, t0, t1, now = null) {
  const span = t1 - t0;
  const until = now != null ? Math.min(now, t1) : t1;
  const track = el("div", { class: "track", role: "img", "aria-label": "Zeitleiste der Maschinenzustände" });
  for (const iv of withGaps(mergeIntervals(intervals), t0, until)) {
    const start = Math.max(iv.start, t0);
    const end = Math.min(iv.end, t1);
    if (end <= start) continue;
    const seg = el("div", {
      class: `seg st-${iv.state}`,
      "data-tip": "",
      style: {
        left: `${((start - t0) / span) * 100}%`,
        width: `max(1px, calc(${((end - start) / span) * 100}% - 2px))`,
      },
    });
    seg._tip = () =>
      tipContent(iv.state, fmtDuration(end - start), [
        `${fmtTime(start)} – ${fmtTime(end)} Uhr`,
        baseName(iv.program),
        iv.exec_mode ? execLabel(iv.exec_mode) : null,
      ]);
    track.append(seg);
  }
  if (now != null && now > t0 && now < t1) {
    track.append(el("div", { class: "now-marker", style: { left: `${((now - t0) / span) * 100}%` } }));
  }
  return track;
}

/** Stundenachse für einen Kalendertag (sommerzeitfest über echte Zeitpunkte). */
export function dayAxis(dayStart, stepHours = 3) {
  const t0 = epoch(dayStart);
  const t1 = epoch(addDays(dayStart, 1));
  const axis = el("div", { class: "axis", "aria-hidden": "true" });
  for (let h = 0; h <= 24; h += stepHours) {
    const d = new Date(dayStart);
    d.setHours(h);
    const t = h === 24 ? t1 : epoch(d);
    axis.append(el("span", { class: "num", style: { left: `${((t - t0) / (t1 - t0)) * 100}%` }, text: `${h}` }));
  }
  return axis;
}
