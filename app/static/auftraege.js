// Aufträge: Liste mit Summen, Detail je Auftrag (Aufspannungen, Programme, Tage, Läufe).

import {
  api,
  el,
  fmtDateTime,
  fmtDay,
  fmtDuration,
  fmtHours,
  fmtTime,
  legend,
  loadMeta,
  machineName,
  resultLabel,
  send,
  stateBadge,
  tipContent,
  tooltip,
} from "./common.js";

const REFRESH_MS = 20000;
const STATUS_LABELS = { open: "offen", closed: "abgeschlossen" };
const $ = (id) => document.getElementById(id);

const query = new URLSearchParams(location.search);
const state = {
  status: query.get("status") ?? "open",
  search: "",
  selected: query.get("order"),
};
let orders = [];

function syncUrl() {
  const q = new URLSearchParams();
  if (state.status !== "open") q.set("status", state.status);
  if (state.selected) q.set("order", state.selected);
  history.replaceState(null, "", q.toString() ? `?${q}` : location.pathname);
}

function showError(err) {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
}

const orderTitle = (o) => `Auftrag ${o.number}`;
const dateOnly = (t) => fmtDateTime(t).split(",")[0];

/** Kalenderzeit von der ersten bis zur letzten Aktivität, z. B. "21 Tage" oder "5 h 12 min". */
function spanText(first, last) {
  const days = (last - first) / 86400;
  return days >= 1 ? `${Math.round(days)} ${Math.round(days) === 1 ? "Tag" : "Tage"}` : fmtDuration(last - first);
}

function statusBadge(o, active) {
  if (active?.length) return stateBadge(active[0].state);
  return el("span", { class: `order-status ${o.status}`, text: STATUS_LABELS[o.status] ?? o.status });
}

// --- Liste -------------------------------------------------------------------------------

function table(columns, rows, { onRow, selected } = {}) {
  return el(
    "table",
    {},
    el("thead", {}, el("tr", {}, columns.map((c) => el("th", { class: c.cls, text: c.label })))),
    el(
      "tbody",
      {},
      rows.map((row) =>
        el(
          "tr",
          {
            class: onRow ? "selectable" : null,
            "aria-selected": onRow ? String(row.key === selected) : null,
            onclick: onRow ? () => onRow(row) : null,
          },
          columns.map((c) => {
            const value = c.value(row);
            return el("td", { class: c.cls, title: c.title?.(row) }, value instanceof Node ? value : value ?? "—");
          }),
        ),
      ),
    ),
  );
}

function renderList() {
  const needle = state.search.trim().toLowerCase();
  const rows = orders.filter((o) => !needle || `${o.key} ${o.number} ${o.title}`.toLowerCase().includes(needle));
  $("count").textContent = `${rows.length} ${rows.length === 1 ? "Auftrag" : "Aufträge"}`;
  if (!rows.length) {
    $("orders").replaceChildren(
      el("p", { class: "empty", text: orders.length ? "Kein Auftrag passt zur Suche." : "Keine Aufträge in dieser Ansicht." }),
    );
    return;
  }
  const columns = [
    {
      label: "Auftrag",
      value: (o) =>
        el(
          "div",
          {},
          el("a", { href: `?order=${encodeURIComponent(o.key)}`, class: "order-key", text: o.number, onclick: (e) => e.preventDefault() }),
          el("span", { class: "muted", text: ` · ${o.year}` }),
          o.title ? el("div", { class: "order-title", text: o.title }) : null,
        ),
      cls: "wrap",
    },
    { label: "Status", value: (o) => statusBadge(o, o.active) },
    { label: "Aufspannungen", value: (o) => (o.setups.length ? o.setups.join(", ") : "—") },
    { label: "Programme", value: (o) => String(o.programs), cls: "r" },
    { label: "Laufzeit", value: (o) => fmtHours(o.running_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (o) => fmtHours(o.stopped_s), cls: "r" },
    { label: "Fertige Läufe", value: (o) => String(o.finished), cls: "r" },
    { label: "Maschinen", value: (o) => o.machines.map(machineName).join(", ") || "—" },
    { label: "Letzte Aktivität", value: (o) => fmtDateTime(o.last_activity ?? o.created_at), cls: "r" },
  ];
  $("orders").replaceChildren(table(columns, rows, { onRow: (o) => select(o.key), selected: state.selected }));
}

async function loadList() {
  const data = await api("/api/orders", { status: state.status });
  orders = data.orders;
  for (const b of $("status").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.status === state.status));
  renderList();
}

// --- Detail ------------------------------------------------------------------------------

function tile(label, value, sub) {
  return el(
    "div",
    {},
    el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value", text: value }),
    sub ? el("div", { class: "tile-sub", text: sub }) : null,
  );
}

function activeBanner(active) {
  if (!active.length) return null;
  return el(
    "div",
    { class: "active-list" },
    active.map((a) => {
      const f = a.forecast;
      const rest = f && !f.overdue_s ? ` · Rest ca. ${fmtDuration(f.remaining_s)} (fertig ca. ${fmtTime(f.eta)} Uhr)` : "";
      return el("div", { class: "active-row" }, stateBadge(a.state), el("span", { text: `${a.machine}: ${a.program}${rest}` }));
    }),
  );
}

function setupCard(setup) {
  const columns = [
    { label: "Programm", value: (p) => p.name, title: (p) => p.program, cls: "wrap" },
    { label: "Maschinen", value: (p) => p.machines.map(machineName).join(", ") || "—" },
    { label: "Läufe", value: (p) => String(p.runs), cls: "r" },
    { label: "Fertig", value: (p) => String(p.finished), cls: "r" },
    { label: "Laufzeit", value: (p) => fmtHours(p.running_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (p) => fmtHours(p.stopped_s), cls: "r" },
    { label: "Ø Laufzeit je Teil", value: (p) => fmtDuration(p.avg_run_s), cls: "r" },
    { label: "Letzter Lauf", value: (p) => fmtDateTime(p.last_run), cls: "r" },
  ];
  const partText = setup.part_complete
    ? `Ø Bearbeitungszeit je Teil in dieser Aufspannung: ${fmtDuration(setup.part_run_s)}`
    : "Ø je Teil erst, wenn jedes Programm dieser Aufspannung einmal vollständig gelaufen ist";
  return el(
    "div",
    { class: "card" },
    el(
      "div",
      { class: "section-head" },
      el("h2", { text: setup.setup ? `Aufspannung ${setup.setup}` : "Ohne Aufspannung" }),
      el("span", { class: "muted", text: `${fmtHours(setup.running_s)} Laufzeit · ${setup.finished} fertige Läufe` }),
    ),
    el("div", { class: "table-wrap" }, table(columns, setup.programs)),
    el("p", { class: "muted", style: { margin: "10px 0 0" }, text: partText }),
  );
}

function daysChart(days) {
  if (!days.length) return el("p", { class: "empty", text: "Noch keine Laufzeit erfasst." });
  const max = Math.max(...days.map((d) => d.running_s + d.stopped_s));
  const rows = days.map((d) => {
    const track = el("div", { class: "track", role: "img", "aria-label": `Laufzeit am ${fmtDay(d.date)}` });
    let left = 0;
    for (const [st, sec] of [["RUNNING", d.running_s], ["STOPPED", d.stopped_s]]) {
      if (sec <= 0) continue;
      const width = (sec / max) * 100;
      const seg = el("div", { class: `seg st-${st}`, "data-tip": "", style: { left: `${left}%`, width: `max(1px, calc(${width}% - 2px))` } });
      seg._tip = () => tipContent(st, fmtHours(sec), [fmtDay(d.date), d.machines.map(machineName).join(", ")]);
      track.append(seg);
      left += width;
    }
    return el(
      "div",
      { class: "day-row" },
      el("span", { class: "date num", text: fmtDay(d.date) }),
      track,
      el("span", { class: "value num", text: fmtHours(d.running_s) }),
    );
  });
  const node = el("div", { class: "day-rows" }, rows);
  tooltip().bind(node);
  return node;
}

function runsTable(runs) {
  const columns = [
    { label: "Nr.", value: (r) => String(r.id), cls: "r" },
    { label: "Programm", value: (r) => r.program.split(/[\\/]/).pop().replace(/\.[^.]+$/, "") },
    { label: "Maschine", value: (r) => machineName(r.machine_id) },
    { label: "Start", value: (r) => (r.start_observed ? "" : "≥ ") + fmtDateTime(r.started_at), cls: "r" },
    { label: "Ende", value: (r) => fmtDateTime(r.ended_at), cls: "r" },
    { label: "Ergebnis", value: (r) => resultLabel(r.result) },
    { label: "Laufzeit", value: (r) => fmtDuration(r.run_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (r) => fmtDuration(r.stop_s), cls: "r" },
  ];
  return runs.length ? table(columns, runs.slice().reverse()) : el("p", { class: "empty", text: "Noch keine Läufe." });
}

async function saveOrder(key, changes) {
  try {
    await send("PUT", `/api/orders/${encodeURIComponent(key)}`, changes);
    await refresh();
  } catch (err) {
    showError(err);
  }
}

function renderDetail(d) {
  const o = d.order;
  const t = d.totals;
  const titleInput = el("input", { type: "text", id: "order-title", maxlength: "120", value: o.title, placeholder: "z. B. Kunde, Bauteil, Zeichnungsnummer" });
  const section = $("detail");
  section.hidden = false;
  section.replaceChildren(
    el(
      "div",
      { class: "card order-head" },
      el(
        "div",
        { class: "order-head-row" },
        el(
          "div",
          { style: { flex: "1 1 260px", minWidth: "0" } },
          el("h1", { style: { margin: 0 } }, orderTitle(o), " ", el("span", { class: "muted", text: `· ${o.year}` })),
          el("div", { class: "muted", text: `Schlüssel ${o.key} · angelegt ${fmtDateTime(o.created_at)}${o.closed_at ? ` · abgeschlossen ${fmtDateTime(o.closed_at)}` : ""}` }),
        ),
        statusBadge(o, d.active),
        el(
          "button",
          { type: "button", class: "btn", onclick: () => saveOrder(o.key, { status: o.status === "closed" ? "open" : "closed" }) },
          o.status === "closed" ? "Wieder öffnen" : "Abschließen",
        ),
        el("a", { class: "btn", href: `/api/orders/${encodeURIComponent(o.key)}/export.csv`, download: true, text: "CSV Läufe" }),
      ),
      el(
        "form",
        {
          class: "order-title-form",
          onsubmit: (e) => {
            e.preventDefault();
            saveOrder(o.key, { title: titleInput.value });
          },
        },
        el("label", { class: "field", for: "order-title", text: "Bezeichnung" }),
        titleInput,
        el("button", { type: "submit", class: "btn", text: "Speichern" }),
      ),
      activeBanner(d.active),
      el(
        "div",
        { class: "kpi-tiles", style: { marginTop: "16px" } },
        tile("Laufzeit", fmtHours(t.running_s), "reine Bearbeitungszeit"),
        tile("Gestoppt / Fehler", fmtHours(t.stopped_s), "innerhalb der Läufe"),
        tile("Fertige Läufe", String(t.finished), `${t.runs} Läufe gestartet`),
        tile(
          "Ø Bearbeitungszeit je Teil",
          t.part_complete ? fmtDuration(t.part_run_s) : "—",
          t.part_complete ? "Summe über alle Aufspannungen" : "noch nicht jedes Programm vollständig gelaufen",
        ),
        tile(
          "Durchlaufzeit",
          t.first_activity ? spanText(t.first_activity, t.last_activity) : "—",
          [
            t.first_activity ? `${dateOnly(t.first_activity)} – ${dateOnly(t.last_activity)}` : null,
            t.machines.length ? t.machines.map(machineName).join(", ") : "noch keine Maschine",
          ].filter(Boolean).join(" · "),
        ),
      ),
    ),
    el("div", { class: "setup-grid" }, d.setups.map(setupCard)),
    el(
      "div",
      { class: "card section" },
      el("div", { class: "section-head" }, el("h2", { text: "Laufzeit je Tag" }), legend(["RUNNING", "STOPPED"])),
      daysChart(d.days),
    ),
    el("div", { class: "card section" }, el("details", {}, el("summary", { text: `Programmdurchläufe (${d.runs.length})` }), el("div", { class: "table-wrap" }, runsTable(d.runs)))),
  );
}

async function loadDetail() {
  if (!state.selected) {
    $("detail").hidden = true;
    return;
  }
  try {
    renderDetail(await api(`/api/orders/${encodeURIComponent(state.selected)}`));
  } catch (err) {
    $("detail").hidden = true;
    showError(err);
  }
}

async function select(key) {
  state.selected = key;
  syncUrl();
  renderList();
  await loadDetail();
  $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function refresh() {
  try {
    await loadList();
    // Eingaben im Bezeichnungsfeld nicht beim automatischen Aktualisieren überschreiben
    if (document.activeElement?.id !== "order-title") await loadDetail();
    $("banner").hidden = true;
  } catch (err) {
    showError(err);
  }
}

async function main() {
  await loadMeta();
  $("status").addEventListener("click", (e) => {
    const status = e.target.closest("button")?.dataset.status;
    if (!status) return;
    state.status = status;
    syncUrl();
    loadList().catch(showError);
  });
  $("search").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderList();
  });
  await refresh();
  if (state.selected) $("detail").scrollIntoView({ block: "start" });
  setInterval(refresh, REFRESH_MS);
}

main().catch(showError);
