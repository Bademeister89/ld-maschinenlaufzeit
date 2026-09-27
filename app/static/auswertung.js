// Auswertungsseite: Zeitraum wählen, Kennzahlen, Tagesübersicht, Programme, Läufe.

import {
  STATE_ORDER,
  addDays,
  api,
  baseName,
  dayAxis,
  el,
  epoch,
  fmtDateTime,
  fmtDay,
  fmtDuration,
  fmtHours,
  fmtInt,
  fmtPct,
  legend,
  loadMeta,
  machineName,
  machineThumb,
  resultLabel,
  startOfDay,
  stateLabel,
  timelineTrack,
  tipContent,
  tooltip,
} from "./common.js";

const MAX_TIMELINE_DAYS = 31;
const MAX_RUN_ROWS = 300;
const $ = (id) => document.getElementById(id);

const query = new URLSearchParams(location.search);
const state = {
  preset: query.get("preset") ?? (query.get("from") ? null : "7d"),
  from: query.get("from"),
  to: query.get("to"),
  machine: query.get("machine") ?? "",
  view: query.get("view") ?? "sum",
};
let meta = null;

// --- Zeitraum -----------------------------------------------------------------

const isoDate = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function parseDate(text) {
  const [y, m, d] = text.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function range() {
  const today = startOfDay();
  const now = new Date();
  switch (state.preset) {
    case "today":
      return [today, now];
    case "yesterday":
      return [addDays(today, -1), today];
    case "7d":
      return [addDays(today, -6), now];
    case "30d":
      return [addDays(today, -29), now];
    case "month":
      return [new Date(today.getFullYear(), today.getMonth(), 1), now];
  }
  const from = state.from ? parseDate(state.from) : addDays(today, -6);
  const to = state.to ? addDays(parseDate(state.to), 1) : now;
  return [from, to > now ? now : to];
}

function syncControls(from, to) {
  for (const b of $("presets").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.preset === state.preset));
  for (const b of $("view").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.view === state.view));
  $("from").value = isoDate(from);
  // "Bis" ist inklusive: ein Ende genau um Mitternacht gehört zum Vortag
  $("to").value = isoDate(to.getHours() === 0 && to.getMinutes() === 0 && to > from ? addDays(to, -1) : to);
  $("machine").value = state.machine;

  const q = new URLSearchParams();
  if (state.preset) q.set("preset", state.preset);
  else {
    q.set("from", state.from ?? isoDate(from));
    q.set("to", state.to ?? isoDate(to));
  }
  if (state.machine) q.set("machine", state.machine);
  if (state.view !== "sum") q.set("view", state.view);
  history.replaceState(null, "", `?${q}`);
}

// --- Laden --------------------------------------------------------------------

async function load() {
  const [from, to] = range();
  const t0 = epoch(from);
  const t1 = epoch(to);
  syncControls(from, to);
  for (const kind of ["intervals", "runs"]) {
    const url = new URL("/api/export.csv", location.origin);
    url.search = new URLSearchParams({ kind, from: t0, to: t1, ...(state.machine && { machine: state.machine }) });
    $(`csv-${kind}`).href = url;
  }
  $("range-label").textContent = `${fmtDateTime(t0)} – ${fmtDateTime(t1)}`;

  const machines = state.machine ? meta.machines.filter((m) => m.id === state.machine) : meta.machines;
  const withTimeline = state.view === "timeline" && t1 - t0 <= MAX_TIMELINE_DAYS * 86400 + 3600;
  const content = $("content");
  content.classList.add("stale");
  try {
    const [stats, runs, timelines] = await Promise.all([
      api("/api/stats", { from: t0, to: t1 }),
      api("/api/runs", { from: t0, to: t1, machine: state.machine }),
      withTimeline
        ? Promise.all(machines.map((m) => api(`/api/machines/${encodeURIComponent(m.id)}/timeline`, { from: t0, to: t1 })))
        : null,
    ]);
    const programs = stats.programs.filter((p) => !state.machine || p.machine_id === state.machine);
    renderKpis(stats, machines, programs);
    renderDays(stats, machines, timelines);
    renderPrograms(programs);
    renderDayTable(stats, machines);
    renderRuns(runs);
    $("banner").hidden = true;
  } catch (err) {
    $("banner").textContent = `Daten konnten nicht geladen werden: ${err.message}`;
    $("banner").hidden = false;
  } finally {
    content.classList.remove("stale");
  }
}

// --- Kennzahlen ---------------------------------------------------------------

function tile(label, value, sub) {
  return el(
    "div",
    {},
    el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value", text: value }),
    sub ? el("div", { class: "tile-sub", text: sub }) : null,
  );
}

function renderKpis(stats, machines, programs) {
  $("kpis").replaceChildren(
    ...machines.map((m) => {
      const s = stats.machines[m.id];
      const mine = programs.filter((p) => p.machine_id === m.id);
      const finished = mine.reduce((sum, p) => sum + p.finished, 0);
      const runs = mine.reduce((sum, p) => sum + p.runs, 0);
      return el(
        "div",
        { class: "card kpi-card" },
        el("div", { class: "card-title" }, machineThumb(m, "small"), el("h2", { text: m.name })),
        el(
          "div",
          { class: "kpi-tiles" },
          tile("Laufzeit", fmtHours(s.totals.RUNNING), `von ${fmtHours(s.period_s)} im Zeitraum`),
          tile("Auslastung", fmtPct(s.utilization), `bezogen auf ${fmtHours(s.powered_on_s)} Einschaltzeit`),
          tile("Auslastung Kalenderzeit", fmtPct(s.utilization_calendar), "bezogen auf den ganzen Zeitraum"),
          tile("Fertige Läufe", String(finished), `${runs} Läufe gestartet`),
          tile("Gestoppt / Fehler", fmtHours(s.totals.STOPPED + s.totals.ERROR), "NC-Stopp und Störungen"),
        ),
      );
    }),
  );
}

// --- Tagesübersicht -----------------------------------------------------------

function sumTrack(day, dayStart, dayEnd) {
  // Maßstab ist der volle Kalendertag, damit ein angebrochener Tag (heute) kürzer ist
  const full = epoch(dayEnd) - epoch(dayStart);
  const track = el("div", { class: "track", role: "img", "aria-label": `Zustände am ${fmtDay(day.date)}` });
  let left = 0;
  for (const st of STATE_ORDER) {
    const sec = day.states[st] ?? 0;
    if (sec <= 0) continue;
    const width = (sec / full) * 100;
    const seg = el("div", {
      class: `seg st-${st}`,
      "data-tip": "",
      style: { left: `${left}%`, width: `max(1px, calc(${width}% - 2px))` },
    });
    seg._tip = () => tipContent(st, fmtHours(sec), [`${fmtPct(sec / day.period_s)} von ${fmtDay(day.date)}`, fmtDuration(sec)]);
    track.append(seg);
    left += width;
  }
  return track;
}

function dayTimelineTrack(intervals, dayStart, dayEnd) {
  const t0 = epoch(dayStart);
  const t1 = epoch(dayEnd);
  const now = Date.now() / 1000;
  return timelineTrack(intervals.filter((iv) => iv.end > t0 && iv.start < t1), t0, t1, now < t1 ? now : null);
}

function renderDays(stats, machines, timelines) {
  const container = $("days");
  $("legend").replaceChildren(legend());
  if (state.view === "timeline" && !timelines) {
    container.replaceChildren(
      el("p", { class: "empty", text: `Die Zeitleiste ist für höchstens ${MAX_TIMELINE_DAYS} Tage verfügbar – bitte Zeitraum verkleinern.` }),
    );
    return;
  }
  container.replaceChildren(
    ...machines.map((m, i) => {
      const s = stats.machines[m.id];
      const rows = s.days.map((day) => {
        const dayStart = startOfDay(new Date(`${day.date}T12:00:00`));
        const dayEnd = addDays(dayStart, 1);
        const track = timelines ? dayTimelineTrack(timelines[i].intervals, dayStart, dayEnd) : sumTrack(day, dayStart, dayEnd);
        return el(
          "div",
          { class: "day-row" },
          el("span", { class: "date num", text: fmtDay(day.date) }),
          track,
          el("span", { class: "value num", text: fmtHours(day.states.RUNNING), title: `${stateLabel("RUNNING")}: ${fmtDuration(day.states.RUNNING)}` }),
        );
      });
      const axisRow = s.days.length
        ? el("div", { class: "day-row" }, el("span"), dayAxis(startOfDay()), el("span", { class: "value muted", text: "Laufzeit" }))
        : null;
      const cardNode = el(
        "div",
        { class: "card" },
        el(
          "div",
          { class: "card-title" },
          machineThumb(m, "small"),
          el("h2", { text: m.name }),
          el("span", { class: "muted", text: timelines ? "Uhrzeit" : "Stunden je Tag" }),
        ),
        rows.length ? el("div", { class: "day-rows" }, rows, axisRow) : el("p", { class: "empty", text: "Keine Tage im Zeitraum." }),
      );
      tooltip().bind(cardNode);
      return cardNode;
    }),
  );
}

// --- Tabellen -----------------------------------------------------------------

function table(columns, rows, emptyText = "Keine Daten im Zeitraum.") {
  if (!rows.length) return el("p", { class: "empty", text: emptyText });
  return el(
    "table",
    {},
    el("thead", {}, el("tr", {}, columns.map((c) => el("th", { class: c.cls, text: c.label })))),
    el(
      "tbody",
      {},
      rows.map((row) => el("tr", {}, columns.map((c) => el("td", { class: c.cls, text: c.value(row) ?? "—", title: c.title?.(row) })))),
    ),
  );
}

function renderPrograms(programs) {
  const columns = [
    ...(state.machine ? [] : [{ label: "Maschine", value: (p) => machineName(p.machine_id) }]),
    { label: "Programm", value: (p) => baseName(p.program), title: (p) => p.program, cls: "wrap" },
    { label: "Sätze", value: (p) => fmtInt(p.blocks), cls: "r" },
    { label: "Läufe", value: (p) => String(p.runs), cls: "r" },
    { label: "Fertig", value: (p) => String(p.finished), cls: "r" },
    { label: "Laufzeit", value: (p) => fmtHours(p.running_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (p) => fmtHours(p.stopped_s), cls: "r" },
    { label: "Ø Laufzeit je Teil", value: (p) => fmtDuration(p.avg_run_s), cls: "r" },
    { label: "Ø Durchlauf je Teil", value: (p) => fmtDuration(p.avg_total_s), cls: "r" },
    { label: "Letzter Start", value: (p) => fmtDateTime(p.last_run), cls: "r" },
  ];
  $("programs").replaceChildren(table(columns, programs, "Im Zeitraum lief kein Programm."));
}

function renderDayTable(stats, machines) {
  const rows = machines.flatMap((m) => stats.machines[m.id].days.map((d) => ({ machine: m, ...d })));
  const columns = [
    { label: "Datum", value: (r) => fmtDay(r.date) },
    ...(state.machine ? [] : [{ label: "Maschine", value: (r) => r.machine.name }]),
    ...STATE_ORDER.map((st) => ({ label: stateLabel(st), value: (r) => fmtHours(r.states[st]), cls: "r" })),
    { label: "Auslastung", value: (r) => fmtPct(r.utilization), cls: "r" },
  ];
  $("day-table").replaceChildren(table(columns, rows));
}

function renderRuns(runs) {
  const newest = runs.slice().reverse();
  const columns = [
    { label: "Nr.", value: (r) => String(r.id), cls: "r" },
    ...(state.machine ? [] : [{ label: "Maschine", value: (r) => machineName(r.machine_id) }]),
    { label: "Programm", value: (r) => baseName(r.program), title: (r) => r.program, cls: "wrap" },
    { label: "Start", value: (r) => (r.start_observed ? "" : "≥ ") + fmtDateTime(r.started_at), cls: "r" },
    { label: "Ende", value: (r) => fmtDateTime(r.ended_at), cls: "r" },
    { label: "Ergebnis", value: (r) => resultLabel(r.result) },
    { label: "Laufzeit", value: (r) => fmtDuration(r.run_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (r) => fmtDuration(r.stop_s), cls: "r" },
    { label: "Gesamt", value: (r) => fmtDuration((r.ended_at ?? r.last_active) - r.started_at), cls: "r" },
  ];
  const note =
    newest.length > MAX_RUN_ROWS
      ? el("p", { class: "muted", text: `Die neuesten ${MAX_RUN_ROWS} von ${newest.length} Läufen – alle Läufe im CSV-Export.` })
      : null;
  $("runs").replaceChildren(...[note, table(columns, newest.slice(0, MAX_RUN_ROWS))].filter(Boolean));
}

// --- Start --------------------------------------------------------------------

async function main() {
  meta = await loadMeta();
  const select = $("machine");
  for (const m of meta.machines) select.append(el("option", { value: m.id, text: m.name }));

  $("presets").addEventListener("click", (e) => {
    const preset = e.target.closest("button")?.dataset.preset;
    if (!preset) return;
    Object.assign(state, { preset, from: null, to: null });
    load();
  });
  $("view").addEventListener("click", (e) => {
    const view = e.target.closest("button")?.dataset.view;
    if (!view || view === state.view) return;
    state.view = view;
    load();
  });
  for (const id of ["from", "to"]) {
    $(id).addEventListener("change", () => {
      if (!$("from").value || !$("to").value) return;
      Object.assign(state, { preset: null, from: $("from").value, to: $("to").value });
      load();
    });
  }
  select.addEventListener("change", () => {
    state.machine = select.value;
    load();
  });
  await load();
}

main().catch((err) => {
  $("banner").textContent = `Start fehlgeschlagen: ${err.message}`;
  $("banner").hidden = false;
});
