// Aufträge: Liste mit Summen, Detail je Auftrag (Bild, Aufspannungen, Programme, Tage, Läufe).

import {
  api,
  el,
  fmtDateTime,
  fmtDay,
  fmtDuration,
  fmtEta,
  fmtHours,
  legend,
  loadMeta,
  machineName,
  programCell,
  programPaths,
  resultLabel,
  send,
  shrinkImage,
  stateBadge,
  tipContent,
  tooltip,
} from "./common.js";

const REFRESH_MS = 20000;
const STATUS_LABELS = { open: "offen", closed: "abgeschlossen" };
// Bild je Auftrag: der Browser verkleinert das Foto auf beide Größen (ca. 150–250 KB und 10–20 KB)
const ORDER_IMAGE = { maxPx: 1280, quality: 0.8 };
const ORDER_THUMB = { maxPx: 256, quality: 0.7 };
const LIST_THUMB_PX = 44;
const $ = (id) => document.getElementById(id);

const query = new URLSearchParams(location.search);
const state = {
  status: query.get("status") ?? "open",
  kind: ["order", "rim"].includes(query.get("kind")) ? query.get("kind") : "all",
  search: "",
  selected: query.get("order"),
};
let orders = [];

function syncUrl() {
  const q = new URLSearchParams();
  if (state.status !== "open") q.set("status", state.status);
  if (state.kind !== "all") q.set("kind", state.kind);
  if (state.selected) q.set("order", state.selected);
  history.replaceState(null, "", q.toString() ? `?${q}` : location.pathname);
}

function showError(err) {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
}

// Felgen (Art "rim") stehen wie Aufträge in der Liste: Titel aus Design, Zusatz Bauart und Größe
const isRim = (o) => o?.kind === "rim";
const orderTitle = (o) => (isRim(o) ? `Felge ${o.rim?.design_name ?? o.number}` : `Auftrag ${o.number}`);
// Zusatz hinter dem Titel: bei Felgen Bauart und Größe, bei Aufträgen die Versionen (das Jahr zählt nicht)
const orderSub = (o) => (isRim(o) ? `${o.rim?.kind_name ?? ""} · ${o.rim?.size ?? o.key}` : (o.versions ?? []).join(", "));
const partWord = (o) => (isRim(o) ? "Felge" : "Teil");
const setupWord = (o) => (isRim(o) ? "Spannung" : "Aufspannung");
const COUNT_WORDS = { all: ["Eintrag", "Einträge"], order: ["Auftrag", "Aufträge"], rim: ["Felge", "Felgen"] };

function statusBadge(o, active) {
  if (active?.length) return stateBadge(active[0].state);
  return el("span", { class: `order-status ${o.status}`, text: STATUS_LABELS[o.status] ?? o.status });
}

// Bild-Elemente je Adresse wiederverwenden: Liste und Detail werden alle 20 s neu aufgebaut,
// ein neues <img> würde dabei kurz leer blinken. Ein neues Bild hat eine neue Adresse (?v=).
const images = new Map();
function cachedImg(url, props) {
  if (!images.has(url)) images.set(url, el("img", { src: url, ...props }));
  return images.get(url);
}

/** Vorschaubild in der Liste; das große Bild lädt die Liste nie. */
function listThumb(o) {
  if (!o.thumb_url) return el("span", { class: "order-thumb placeholder", "aria-hidden": "true" });
  return cachedImg(o.thumb_url, {
    class: "order-thumb",
    width: LIST_THUMB_PX,
    height: LIST_THUMB_PX,
    alt: "",
    loading: "lazy",
    decoding: "async",
  });
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
  const rows = orders.filter(
    (o) =>
      (state.kind === "all" || (o.kind ?? "order") === state.kind) &&
      (!needle ||
        `${o.key} ${o.number} ${(o.versions ?? []).map((v) => o.number + v).join(" ")} ${o.title} ${o.rim?.label ?? ""}`
          .toLowerCase()
          .includes(needle)),
  );
  $("count").textContent = `${rows.length} ${COUNT_WORDS[state.kind][rows.length === 1 ? 0 : 1]}`;
  if (!rows.length) {
    $("orders").replaceChildren(
      el("p", { class: "empty", text: orders.length ? "Kein Eintrag passt zu Filter und Suche." : "Keine Einträge in dieser Ansicht." }),
    );
    return;
  }
  const columns = [
    {
      label: "Auftrag / Felge",
      value: (o) =>
        el(
          "div",
          { class: "order-cell" },
          listThumb(o),
          el(
            "div",
            {},
            el("a", {
              href: `?order=${encodeURIComponent(o.key)}`,
              class: "order-key",
              text: isRim(o) ? `${o.rim?.design_name} · ${o.rim?.size}` : o.number,
              onclick: (e) => e.preventDefault(),
            }),
            el("span", {
              class: "muted",
              text: isRim(o) ? ` · ${o.rim?.kind_name} · Felge ${o.key}` : o.versions?.length ? ` · ${o.versions.join(", ")}` : "",
            }),
            o.title ? el("div", { class: "order-title", text: o.title }) : null,
          ),
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
  for (const b of $("kind").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.kind === state.kind));
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
      const rest = f && !f.overdue_s ? ` · Rest ca. ${fmtDuration(f.remaining_s)} (fertig ca. ${fmtEta(f.eta)})` : "";
      return el("div", { class: "active-row" }, stateBadge(a.state), el("span", { text: `${a.machine}: ${a.program}${rest}` }));
    }),
  );
}

const versionName = (version) => (version ? `Version ${version}` : "Grundversion");

/** Aufspannungen; hat der Auftrag Versionen (andere Ausführungen des Teils), je Version ein Block
 *  mit eigener Ø-Zeit je Teil. */
function versionBlocks(d, o) {
  const hasVersions = d.versions.length > 1 || d.versions.some((v) => v.version);
  if (!hasVersions) return el("div", { class: "setup-grid" }, d.setups.map((s) => setupCard(s, o)));
  return el(
    "div",
    { class: "version-blocks" },
    d.versions.map((v) =>
      el(
        "section",
        { class: "version-block", "aria-label": versionName(v.version) },
        el(
          "div",
          { class: "version-head" },
          el("h2", { text: `${versionName(v.version)} · ${o.number}${v.version}` }),
          el("span", {
            class: "muted",
            text: [
              `${fmtHours(v.running_s)} Laufzeit`,
              `${v.finished} fertige Läufe`,
              `Ø je ${partWord(o)} ${v.part_complete ? fmtDuration(v.part_run_s) : "noch offen"}`,
              v.plan_part_s ? `Plan ${fmtDuration(v.plan_part_s)}` : null,
            ].filter(Boolean).join(" · "),
          }),
        ),
        el("div", { class: "setup-grid" }, v.setups.map((s) => setupCard(s, o))),
      ),
    ),
  );
}

// --- CAM-Planzeiten (Tebis-Doku oder von Hand) --------------------------------------------------

const hoursMinutes = (sec) => `${Math.floor(sec / 3600)}:${String(Math.round((sec % 3600) / 60)).padStart(2, "0")}`;
const PLAN_SOURCE = { pdf: "aus der Tebis-Doku", manual: "von Hand eingetragen" };

/** Planzeit eines Programms mit Knopf zum Ändern. */
function planCell(o, p) {
  return el(
    "span",
    { class: "plan-cell" },
    el("span", { text: p.plan_s != null ? fmtDuration(p.plan_s) : "—", title: PLAN_SOURCE[p.plan_source] ?? null }),
    el("button", {
      type: "button",
      class: "link-button plan-edit",
      title: "Planzeit ändern",
      "aria-label": `Planzeit für ${p.name} ändern`,
      onclick: () => editPlan(o, p.name, p.plan_s),
      text: "ändern",
    }),
  );
}

async function savePlan(o, program, time) {
  try {
    await send("PUT", `/api/orders/${encodeURIComponent(o.key)}/plans`, { program, time });
    await refresh();
    return true;
  } catch (err) {
    showError(err);
    return false;
  }
}

function editPlan(o, program, current) {
  const value = prompt(
    `Planzeit (CAM) für ${program}\nStunden (z. B. 4,5) oder h:mm (z. B. 4:30). Leer lassen = Planzeit entfernen.`,
    current != null ? hoursMinutes(current) : "",
  );
  if (value !== null) savePlan(o, program, value);
}

/** Planzeit für ein Programm, das hier noch keine Zeile hat (z. B. eine Version ohne Tebis-Doku). */
function planForm(o) {
  const program = el("input", {
    type: "text",
    class: "plan-program",
    placeholder: isRim(o) ? `${o.key}-01` : `26-${o.number}-01-01`,
    "aria-label": "Programmname",
  });
  const time = el("input", { type: "text", class: "plan-time", placeholder: "4,5 oder 4:30", "aria-label": "Planzeit" });
  return el(
    "form",
    {
      class: "card plan-form",
      onsubmit: async (e) => {
        e.preventDefault();
        if (await savePlan(o, program.value, time.value)) program.value = time.value = "";
      },
    },
    el("span", { class: "plan-form-label", text: "Planzeit (CAM) für ein weiteres Programm eintragen:" }),
    program,
    time,
    el("button", { type: "submit", class: "btn", text: "Eintragen" }),
  );
}

function showNotice(lines, failed) {
  const notice = $("notice");
  notice.classList.toggle("warn", failed);
  notice.replaceChildren(
    ...lines.map((line) => el("div", { text: line })),
    el("button", { type: "button", class: "link-button", onclick: () => (notice.hidden = true), text: "Schließen" }),
  );
  notice.hidden = false;
}

/** Tebis-Doku (PDF) importieren: legt Aufträge an und übernimmt die Planzeiten der Programme. */
async function importCam(e) {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  const lines = [];
  let failed = false;
  let open = null;
  for (const file of files) {
    try {
      const r = await send("POST", "/api/orders/import-cam", new Blob([file], { type: "application/pdf" }), {
        "X-File-Name": encodeURIComponent(file.name),
      });
      const where = [r.setup != null ? `Spannung ${String(r.setup).padStart(2, "0")}` : null, r.machine].filter(Boolean).join(", ");
      for (const o of r.orders) {
        const programs = o.programs.map((p) => `${p.program} ${fmtDuration(p.planned_s)}`).join(", ");
        lines.push(
          `${file.name}: ${o.kind === "rim" ? "Felge" : "Auftrag"} ${o.key}${o.created ? " angelegt" : ""}` +
            `${where ? ` (${where})` : ""} – Planzeit: ${programs}`,
        );
        open ??= o.key;
      }
      if (r.ignored.length) lines.push(`${file.name}: ohne Auftragsnummer übergangen: ${r.ignored.join(", ")}`);
    } catch (err) {
      failed = true;
      lines.push(err.message);
    }
  }
  showNotice(lines, failed);
  await loadList();
  if (open) await select(open);
}

function setupCard(setup, o) {
  const columns = [
    { label: "Programm", value: (p) => programCell(p.name, p.paths), title: programPaths, cls: "wrap" },
    { label: "Maschinen", value: (p) => p.machines.map(machineName).join(", ") || "—" },
    { label: "Läufe", value: (p) => String(p.runs), cls: "r" },
    { label: "Fertig", value: (p) => String(p.finished), cls: "r" },
    { label: "Laufzeit", value: (p) => fmtHours(p.running_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (p) => fmtHours(p.stopped_s), cls: "r" },
    { label: `Ø Laufzeit je ${partWord(o)}`, value: (p) => fmtDuration(p.avg_run_s), cls: "r" },
    { label: "Plan (CAM)", value: (p) => planCell(o, p), cls: "r" },
    { label: "Letzter Lauf", value: (p) => fmtDateTime(p.last_run), cls: "r" },
    {
      label: "",
      value: (p) => deleteButton("Löschen", `Programm ${p.name} löschen`, p.open, () => deleteProgram(o, p)),
      cls: "r",
    },
  ];
  const spannung = String(setup.setup).padStart(2, "0");
  let partText;
  if (setup.fixture) partText = "Vorrichtungsbau: einmaliger Aufwand, zählt nicht in die Ø-Bearbeitungszeit je Teil";
  else if (setup.part_complete) partText = `Ø Bearbeitungszeit je ${partWord(o)} in dieser ${setupWord(o)}: ${fmtDuration(setup.part_run_s)}`;
  else partText = `Ø je ${partWord(o)} erst, wenn jedes Programm dieser ${setupWord(o)} einmal vollständig gelaufen ist`;
  if (!setup.fixture && setup.plan_complete && setup.plan_s) partText += ` · Plan (CAM) ${fmtDuration(setup.plan_s)}`;
  let title = setup.setup ? `${setupWord(o)} ${setup.setup}` : `Ohne ${setupWord(o)}`;
  if (setup.fixture) title = `Vorrichtung (Spannung ${spannung})`;
  return el(
    "div",
    { class: `card${setup.fixture ? " setup-fixture" : ""}` },
    el(
      "div",
      { class: "section-head" },
      el("h2", { text: title }),
      el("span", { class: "muted", text: `${fmtHours(setup.running_s)} Laufzeit · ${setup.finished} fertige Läufe` }),
      el(
        "span",
        { class: "setup-delete" },
        deleteButton(
          `${setup.fixture ? "Vorrichtung" : setupWord(o)} löschen`,
          `${title}${setup.version ? ` (${versionName(setup.version)})` : ""} löschen`,
          setup.open,
          () => deleteSetup(o, setup, title),
        ),
      ),
    ),
    el("div", { class: "table-wrap" }, table(columns, setup.programs)),
    el("p", { class: "muted", style: { margin: "10px 0 0" }, text: partText }),
  );
}

// --- Löschen: Programm, Aufspannung, ganzer Auftrag ------------------------------------------------

const DELETE_NOTE =
  "Die Laufzeit der Maschine in der Auswertung bleibt erhalten. Läuft ein Programm später wieder, wird es neu erfasst.\n\n" +
  "Das lässt sich nicht rückgängig machen.";

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
/** Aufzählung wie „3 Programme, 5 Läufe, 2 Planzeiten“ (Programme nur, wenn angegeben; Nullen entfallen). */
const countText = (programs, runs, plans) =>
  [
    programs != null ? plural(programs, "Programm", "Programme") : null,
    runs ? plural(runs, "Lauf", "Läufe") : null,
    plans ? plural(plans, "Planzeit", "Planzeiten") : null,
  ]
    .filter(Boolean)
    .join(", ");
const deleteName = (o) => `${isRim(o) ? `Felge ${o.key}` : `Auftrag ${o.number}`}${o.title ? ` (${o.title})` : ""}`;

/** Löschen-Knopf; solange ein Lauf nicht beendet ist, nur der Hinweis „läuft“. */
function deleteButton(label, ariaLabel, open, onclick) {
  if (open) return el("span", { class: "muted", title: "Löschen geht erst, wenn der Lauf beendet ist", text: "läuft" });
  return el("button", { type: "button", class: "btn small danger", "aria-label": ariaLabel, onclick, text: label });
}

async function deleteItems(path, question, done) {
  if (!confirm(question)) return;
  try {
    await done(await send("DELETE", path));
  } catch (err) {
    showError(err);
  }
}

function deleteProgram(o, p) {
  deleteItems(
    `/api/orders/${encodeURIComponent(o.key)}/programs/${encodeURIComponent(p.call_name)}`,
    `Programm ${p.name} aus ${deleteName(o)} löschen?\n\n` +
      `Gelöscht werden: ${countText(null, p.runs, p.plan_s != null ? 1 : 0)}.\n\n${DELETE_NOTE}`,
    refresh,
  );
}

function deleteSetup(o, setup, title) {
  const runs = setup.programs.reduce((n, p) => n + p.runs, 0);
  const plans = setup.programs.filter((p) => p.plan_s != null).length;
  const version = setup.version ? `?version=${encodeURIComponent(setup.version)}` : "";
  deleteItems(
    `/api/orders/${encodeURIComponent(o.key)}/setups/${setup.setup}${version}`,
    `${title}${setup.version ? ` (${versionName(setup.version)})` : ""} aus ${deleteName(o)} löschen?\n\n` +
      `Gelöscht werden: ${countText(setup.programs.length, runs, plans)}.\n\n${DELETE_NOTE}`,
    refresh,
  );
}

function deleteOrder(d) {
  const o = d.order;
  const programs = d.setups.flatMap((s) => s.programs);
  const plans = programs.filter((p) => p.plan_s != null).length;
  const it = isRim(o) ? "die Felge" : "den Auftrag";
  deleteItems(
    `/api/orders/${encodeURIComponent(o.key)}`,
    `${deleteName(o)} komplett löschen?\n\n` +
      `Gelöscht werden: ${countText(programs.length, d.runs.length, plans)}${o.title ? ", die Bezeichnung" : ""}` +
      `${o.image_url ? ", das Bild" : ""}.\n\n` +
      `Ist ein Programm noch an einer Maschine angewählt oder läuft es später wieder, legt die App ${it} neu an – ohne die gelöschten Daten. ` +
      "Die Laufzeit der Maschine in der Auswertung bleibt erhalten.\n\nDas lässt sich nicht rückgängig machen.",
    async (r) => {
      state.selected = null;
      syncUrl();
      showNotice([`${deleteName(o)} gelöscht${r.runs || r.plans ? `: ${countText(null, r.runs, r.plans)}` : ""}.`], false);
      await refresh();
    },
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

const programName = (path) => path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "");

function runsTable(key, runs) {
  const columns = [
    { label: "Nr.", value: (r) => String(r.id), cls: "r" },
    { label: "Programm", value: (r) => programName(r.program) },
    { label: "Maschine", value: (r) => machineName(r.machine_id) },
    { label: "Start", value: (r) => (r.start_observed ? "" : "≥ ") + fmtDateTime(r.started_at), cls: "r" },
    { label: "Ende", value: (r) => fmtDateTime(r.ended_at), cls: "r" },
    { label: "Ergebnis", value: (r) => resultLabel(r.result) },
    { label: "Laufzeit", value: (r) => fmtDuration(r.run_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (r) => fmtDuration(r.stop_s), cls: "r" },
    {
      label: "",
      // Nur beendete Läufe: Ein laufender Lauf wird noch fortgeschrieben
      value: (r) =>
        r.ended_at == null
          ? el("span", { class: "muted", text: "läuft" })
          : el("button", {
              type: "button",
              class: "btn small danger",
              "aria-label": `Lauf ${r.id} löschen`,
              onclick: () => deleteRun(key, r),
              text: "Löschen",
            }),
      cls: "r",
    },
  ];
  return runs.length ? table(columns, runs.slice().reverse()) : el("p", { class: "empty", text: "Noch keine Läufe." });
}

/** Fehllauf oder Nachprogramm aus dem Auftrag löschen (mit Rückfrage, nicht umkehrbar). */
async function deleteRun(key, r) {
  const what = [
    programName(r.program),
    machineName(r.machine_id),
    `${fmtDateTime(r.started_at)} – ${fmtDateTime(r.ended_at)}`,
    resultLabel(r.result),
    `Laufzeit ${fmtDuration(r.run_s)}`,
  ].join(" · ");
  const ok = confirm(
    `Lauf ${r.id} löschen?\n\n${what}\n\n` +
      `Der Lauf zählt danach nicht mehr zu ${isRim(current) ? "dieser Felge" : "diesem Auftrag"}, zur Ø-Bearbeitungszeit je ${partWord(current)} und zur Restlaufzeit-Prognose. ` +
      "Die Laufzeit der Maschine in der Auswertung bleibt erhalten.\n\nDas lässt sich nicht rückgängig machen.",
  );
  if (!ok) return;
  try {
    await send("DELETE", `/api/orders/${encodeURIComponent(key)}/runs/${r.id}`);
    await refresh();
  } catch (err) {
    showError(err);
  }
}

async function saveOrder(key, changes) {
  try {
    await send("PUT", `/api/orders/${encodeURIComponent(key)}`, changes);
    await refresh();
  } catch (err) {
    showError(err);
  }
}

// --- Bild des Auftrags (fertiges Bauteil) ------------------------------------------------------

let current = null; // angezeigter Auftrag
let runsOpen = false; // Liste „Programmdurchläufe“ aufgeklappt
const imageState = { key: null, text: null, error: false, busy: false }; // Hinweis zum Hochladen
// Handy/Tablet: eigener Kamera-Button. Android (ab 13) zeigt bei einem Bild-Dateifeld nur die
// Fotoauswahl ohne Kamera; die Kamera öffnet sich nur über ein eigenes Feld mit "capture".
const touch = matchMedia("(pointer: coarse)").matches;
// Zwischenablage per Button nur in sicherer Umgebung (https oder localhost); Strg+V geht überall
const clipboardButton = !touch && typeof navigator.clipboard?.read === "function";

function imageButtons(o, disabled) {
  const button = (text, source) => el("button", { type: "button", class: "btn", disabled, onclick: () => pickImage(o.key, source) }, text);
  if (touch) return [button("Foto aufnehmen", "camera"), button("Aus Galerie", "gallery")];
  return [
    button(o.image_url ? "Bild ersetzen" : "Bild hinzufügen", "gallery"),
    clipboardButton ? el("button", { type: "button", class: "btn", disabled, onclick: () => imageFromClipboardApi(o) }, "Aus Zwischenablage") : null,
  ];
}

function imageBlock(o) {
  const own = imageState.key === o.key;
  const picture = o.image_url
    ? el(
        "a",
        { class: "order-image", href: o.image_url, target: "_blank", rel: "noopener", title: "Bild in voller Größe öffnen" },
        cachedImg(o.image_url, { alt: `Bild zu ${orderTitle(o)}`, decoding: "async" }),
      )
    : el("div", { class: "order-image empty", text: "Noch kein Bild vom Bauteil" });
  return el(
    "figure",
    { class: "order-figure", id: "order-figure" },
    picture,
    el(
      "figcaption",
      { class: "order-image-actions" },
      imageButtons(o, own && imageState.busy),
      o.image_url
        ? el("button", { type: "button", class: "btn danger", disabled: own && imageState.busy, onclick: () => removeImage(o) }, "Bild entfernen")
        : null,
    ),
    own && imageState.text
      ? el("div", { class: imageState.error ? "form-error" : "order-image-note", role: "status", text: imageState.text })
      : !touch
        ? el("div", { class: "order-image-hint", text: "Tipp: Bild kopieren (z. B. Screenshot) und hier mit Strg+V einfügen" })
        : null,
  );
}

function setImageState(key, text = null, { error = false, busy = false } = {}) {
  Object.assign(imageState, { key, text, error, busy });
  if (current) $("order-figure")?.replaceWith(imageBlock(current));
}

function pickImage(key, source) {
  const input = $(source === "camera" ? "camera-input" : "image-input");
  input.value = "";
  input.dataset.key = key;
  input.click();
}

const kb = (bytes) => `${Math.max(1, Math.round(bytes / 1024))} KB`;

function uploadImage(e) {
  const file = e.target.files[0];
  const key = e.target.dataset.key;
  if (file && key) uploadFile(key, file);
}

/** Bild aus der Zwischenablage (Strg+V auf der Seite). Text einfügen, z. B. in die Bezeichnung, bleibt unberührt. */
function pasteImage(e) {
  const o = current;
  if (!o || $("detail").hidden || imageState.busy) return;
  const item = [...(e.clipboardData?.items ?? [])].find((i) => i.kind === "file" && i.type.startsWith("image/"));
  const file = item?.getAsFile();
  if (!file) return;
  e.preventDefault();
  if (o.image_url && !confirm(`Bild von ${orderTitle(o)} durch das Bild aus der Zwischenablage ersetzen?`)) return;
  uploadFile(o.key, file);
}

/** Button „Aus Zwischenablage“ (Clipboard-API; der Browser fragt evtl. nach Erlaubnis). */
async function imageFromClipboardApi(o) {
  let file = null;
  try {
    for (const item of await navigator.clipboard.read()) {
      const type = item.types.find((t) => t.startsWith("image/"));
      if (type) {
        file = new File([await item.getType(type)], "Zwischenablage", { type });
        break;
      }
    }
  } catch {
    setImageState(o.key, "Kein Zugriff auf die Zwischenablage – Bild bitte mit Strg+V einfügen.", { error: true });
    return;
  }
  if (!file) {
    setImageState(o.key, "In der Zwischenablage ist kein Bild. Bild kopieren (z. B. Screenshot) und erneut versuchen.", { error: true });
    return;
  }
  if (o.image_url && !confirm(`Bild von ${orderTitle(o)} durch das Bild aus der Zwischenablage ersetzen?`)) return;
  uploadFile(o.key, file);
}

async function uploadFile(key, file) {
  setImageState(key, "Bild wird verkleinert …", { busy: true });
  try {
    const [full, thumb] = await shrinkImage(file, [ORDER_IMAGE, ORDER_THUMB]);
    setImageState(key, `Bild wird hochgeladen (${kb(full.size + thumb.size)}) …`, { busy: true });
    // Beide Größen in einer Anfrage, damit nie ein Bild ohne Vorschau gespeichert wird
    await send("PUT", `/api/orders/${encodeURIComponent(key)}/image`, new Blob([full, thumb], { type: "application/octet-stream" }), {
      "X-Image-Length": String(full.size),
    });
    setImageState(null);
    await refresh();
  } catch (err) {
    setImageState(key, err.message, { error: true });
  }
}

async function removeImage(o) {
  if (!confirm(`Bild von ${orderTitle(o)} entfernen?`)) return;
  setImageState(o.key, "Bild wird entfernt …", { busy: true });
  try {
    await send("DELETE", `/api/orders/${encodeURIComponent(o.key)}/image`);
    setImageState(null);
    await refresh();
  } catch (err) {
    setImageState(o.key, err.message, { error: true });
  }
}

function renderDetail(d) {
  const o = d.order;
  const t = d.totals;
  current = o;
  const titleInput = el("input", { type: "text", id: "order-title", maxlength: "120", value: o.title, placeholder: "z. B. Kunde, Bauteil, Zeichnungsnummer" });
  const section = $("detail");
  section.hidden = false;
  section.replaceChildren(
    el(
      "div",
      { class: "card order-head" },
      el(
        "div",
        { class: "order-top" },
        el(
          "div",
          { class: "order-info" },
          el(
            "div",
            { class: "order-head-row" },
            el(
              "div",
              { style: { flex: "1 1 260px", minWidth: "0" } },
              el("h1", { style: { margin: 0 } }, orderTitle(o), orderSub(o) ? el("span", { class: "muted", text: ` · ${orderSub(o)}` }) : null),
              el("div", { class: "muted", text: `Schlüssel ${o.key} · angelegt ${fmtDateTime(o.created_at)}${o.closed_at ? ` · abgeschlossen ${fmtDateTime(o.closed_at)}${o.closed_auto ? " (automatisch, 7 Tage ohne Programmlauf)" : ""}` : ""}` }),
            ),
            statusBadge(o, d.active),
            el(
              "button",
              { type: "button", class: "btn", onclick: () => saveOrder(o.key, { status: o.status === "closed" ? "open" : "closed" }) },
              o.status === "closed" ? "Wieder öffnen" : "Abschließen",
            ),
            el("a", { class: "btn", href: `/api/orders/${encodeURIComponent(o.key)}/export.csv`, download: true, text: "CSV Läufe" }),
            el("button", {
              type: "button",
              class: "btn danger",
              disabled: d.runs.some((r) => r.ended_at == null),
              title: d.runs.some((r) => r.ended_at == null) ? "Löschen geht erst, wenn kein Lauf mehr läuft" : null,
              "aria-label": `${deleteName(o)} löschen`,
              onclick: () => deleteOrder(d),
              text: `${isRim(o) ? "Felge" : "Auftrag"} löschen`,
            }),
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
        ),
        imageBlock(o),
      ),
      el(
        "div",
        { class: "kpi-tiles", style: { marginTop: "16px" } },
        tile("Laufzeit", fmtHours(t.running_s), t.fixture_s > 0 ? `davon Vorrichtung ${fmtHours(t.fixture_s)}` : "reine Bearbeitungszeit"),
        tile("Gestoppt / Fehler", fmtHours(t.stopped_s), "innerhalb der Läufe"),
        tile("Fertige Läufe", String(t.finished), `${t.runs} Läufe gestartet`),
        d.versions.length > 1
          ? tile(
              `Ø Bearbeitungszeit je ${partWord(o)}`,
              "je Version",
              d.versions
                .map((v) => `${versionName(v.version)} ${v.part_complete ? fmtDuration(v.part_run_s) : "—"}${v.plan_part_s ? ` (Plan ${fmtDuration(v.plan_part_s)})` : ""}`)
                .join(" · "),
            )
          : tile(
              `Ø Bearbeitungszeit je ${partWord(o)}`,
              t.part_complete ? fmtDuration(t.part_run_s) : "—",
              [
                t.part_complete
                  ? `Summe über alle ${isRim(o) ? "Spannungen und Programme" : "Aufspannungen"}${d.setups.some((s) => s.fixture) ? ", ohne Vorrichtung" : ""}`
                  : "noch nicht jedes Programm vollständig gelaufen",
                t.plan_part_s ? `Plan (CAM) ${fmtDuration(t.plan_part_s)}` : null,
              ].filter(Boolean).join(" · "),
            ),
      ),
    ),
    versionBlocks(d, o),
    planForm(o),
    el(
      "div",
      { class: "card section" },
      el("div", { class: "section-head" }, el("h2", { text: "Laufzeit je Tag" }), legend(["RUNNING", "STOPPED"])),
      daysChart(d.days),
    ),
    el(
      "div",
      { class: "card section" },
      el(
        "details",
        // Aufgeklappt bleiben, wenn die Seite alle 20 s neu aufgebaut wird
        { open: runsOpen, ontoggle: (e) => (runsOpen = e.currentTarget.open) },
        el("summary", { text: `Programmdurchläufe (${d.runs.length})` }),
        el("div", { class: "table-wrap" }, runsTable(o.key, d.runs)),
      ),
    ),
  );
}

async function loadDetail() {
  if (!state.selected) {
    current = null;
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
    // Eingaben im Bezeichnungsfeld und den Hinweis beim Hochladen nicht beim Aktualisieren überschreiben
    const typing = document.activeElement?.matches?.("#detail input");
    if (!typing && !imageState.busy) await loadDetail();
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
  $("kind").addEventListener("click", (e) => {
    const kind = e.target.closest("button")?.dataset.kind;
    if (!kind) return;
    state.kind = kind;
    syncUrl();
    for (const b of $("kind").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.kind === state.kind));
    renderList();
  });
  $("search").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderList();
  });
  $("image-input").addEventListener("change", uploadImage);
  $("camera-input").addEventListener("change", uploadImage);
  $("cam-import").addEventListener("click", () => $("cam-input").click());
  $("cam-input").addEventListener("change", importCam);
  document.addEventListener("paste", pasteImage);
  await refresh();
  if (state.selected) $("detail").scrollIntoView({ block: "start" });
  setInterval(refresh, REFRESH_MS);
}

main().catch(showError);
