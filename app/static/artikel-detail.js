// Artikel-Fenster: ein Artikel (Auftrag und Version bzw. Felge) mit den Reitern Laufzeiten, Rohling
// und Preise (Stückliste folgt). Kopf mit Auftragsname, Notiz, Bild, Status und Aktionen.
//
// Laufzeiten: bei einer Version (21053V1) nur ihre eigenen Aufspannungen und, als gemeinsam markiert,
// die der Grundversion, die sie mitbenutzt (übernommene Spannung 1, Vorrichtung 08/09). Planzeit, Ø,
// Löschen und Bild wirken dort auf alle Versionen. Abschließen, CSV und Löschen des Auftrags gelten
// für den ganzen Auftrag.

import { openChanges } from "./aenderungen.js";
import {
  api,
  el,
  fmtDateTime,
  fmtDay,
  fmtDuration,
  fmtEta,
  fmtHours,
  legend,
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
// Reiter: [id, Beschriftung, nur Platzhalter]; ein weiterer Reiter kommt hier dazu
const TABS = [
  ["laufzeiten", "Laufzeiten"],
  ["rohling", "Rohling"],
  ["preise", "Preise"],
  ["stueckliste", "Stückliste", true],
];
export const TAB_IDS = TABS.filter((t) => !t[2]).map((t) => t[0]);

const eur = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
const num = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });
const fmtEur = (v) => (v == null ? "—" : eur.format(v));
const toInput = (v) => (v == null ? "" : String(v).replace(".", ","));
const parseNum = (text) => {
  const t = String(text ?? "").trim().replace(/\s|€/g, "");
  if (!t) return null;
  const n = Number(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t);
  return Number.isFinite(n) ? n : NaN;
};

// Felgen (Art "rim") wie Aufträge, aber mit Design und Größe statt Nummer
const isRim = (o) => o?.kind === "rim";
const partWord = (o) => (isRim(o) ? "Felge" : "Teil");
const setupWord = (o) => (isRim(o) ? "Spannung" : "Aufspannung");
const partsWord = (o, n) => (isRim(o) ? (n === 1 ? "Felge" : "Felgen") : n === 1 ? "Teil" : "Teile");
const versionName = (version) => (version ? `Version ${version}` : "Grundversion");
const deleteName = (o) => `${isRim(o) ? `Felge ${o.key}` : `Auftrag ${o.number}`}${o.title ? ` (${o.title})` : ""}`;

let dialog = null;
let win = null; // { key, orderKey, version, tab, article, materials, detail, onChanged, onNavigate, changed, timer }
let runsOpen = false; // Liste „Programmdurchläufe“ aufgeklappt
const imageState = { id: null, text: null, error: false, busy: false }; // Hinweis zum Hochladen
// Handy/Tablet: eigener Kamera-Button. Android (ab 13) zeigt bei einem Bild-Dateifeld nur die
// Fotoauswahl ohne Kamera; die Kamera öffnet sich nur über ein eigenes Feld mit "capture".
const touch = matchMedia("(pointer: coarse)").matches;
// Zwischenablage per Button nur in sicherer Umgebung (https oder localhost); Strg+V geht überall
const clipboardButton = !touch && typeof navigator.clipboard?.read === "function";

function build() {
  dialog = el("dialog", { class: "article-window", "aria-labelledby": "aw-title" });
  dialog.addEventListener("close", () => {
    clearInterval(win?.timer);
    const done = win;
    win = null;
    done?.onNavigate?.(null);
    if (done?.changed) done.onChanged?.();
  });
  // Bild-Dateifelder bleiben beim Neuzeichnen bestehen, damit die Auswahl nicht verloren geht
  const gallery = el("input", { type: "file", id: "aw-image-input", class: "visually-hidden", accept: "image/*", tabindex: "-1", "aria-hidden": "true" });
  const camera = el("input", { type: "file", id: "aw-camera-input", class: "visually-hidden", accept: "image/*", capture: "environment", tabindex: "-1", "aria-hidden": "true" });
  for (const input of [gallery, camera]) input.addEventListener("change", uploadImage);
  document.body.append(dialog, gallery, camera);
  document.addEventListener("paste", pasteImage);
}

/** Artikel-Fenster öffnen. Liefert false, wenn es den Artikel nicht gibt (z. B. die Grundversion, die
 *  nur gemeinsame Vorstufe ihrer Versionen ist) – dann zeigt die Seite stattdessen die Liste. */
export async function openArticle(key, { tab = "laufzeiten", onChanged, onNavigate } = {}) {
  if (!dialog) build();
  clearInterval(win?.timer);
  // Sofort öffnen und „wird geladen“ zeigen; die Daten kommen gleich nach
  const w = { key, tab: TAB_IDS.includes(tab) ? tab : "laufzeiten", article: null, detail: null, error: null, onChanged, changed: false, timer: null };
  win = w;
  dialog.replaceChildren(el("p", { class: "empty aw-loading", text: `${key} wird geladen …` }));
  if (!dialog.open) dialog.showModal();
  let data;
  try {
    data = await api(`/api/articles/${encodeURIComponent(key)}`);
  } catch {
    if (win === w) {
      win = null;
      dialog.close();
    }
    return false;
  }
  if (win !== w) return true; // inzwischen geschlossen oder ein anderer Artikel geöffnet
  const a = data.article;
  Object.assign(w, { key: a.key, orderKey: a.order_key, version: a.version, article: a, materials: data.materials, onNavigate });
  await loadDetail();
  if (win !== w) return true;
  render();
  w.timer = setInterval(refresh, REFRESH_MS);
  w.onNavigate?.(w.key, w.tab);
  return true;
}

async function loadDetail() {
  const w = win;
  try {
    // Immer mit version (auch leer = Grundversion); ohne Versionen und bei Felgen bleibt es der ganze Auftrag
    w.detail = await api(`/api/orders/${encodeURIComponent(w.orderKey)}?version=${encodeURIComponent(w.version)}`);
    w.error = null;
  } catch (err) {
    w.error = err.message;
  }
}

async function loadArticle() {
  const data = await api(`/api/articles/${encodeURIComponent(win.key)}`);
  win.article = data.article;
  win.materials = data.materials;
}

/** Nach einer Aktion: Artikel und Auftrag neu laden und zeichnen; die Liste lädt beim Schließen neu. */
async function reload() {
  if (!win) return;
  win.changed = true;
  try {
    await loadArticle();
  } catch (err) {
    showError(err);
  }
  await loadDetail();
  render();
}

/** Alle 20 s: nur die Laufzeiten, und nicht während einer Eingabe oder eines Uploads. */
async function refresh() {
  if (!win || win.tab !== "laufzeiten" || imageState.busy || document.querySelector("dialog[open]:not(.article-window)")) return;
  if (document.activeElement?.matches?.(".article-window input, .article-window select, .article-window textarea")) return;
  await loadDetail();
  render();
}

function showError(err) {
  if (!win) return;
  win.error = err.message;
  render();
}

// --- Zeichnen -----------------------------------------------------------------------------------

function render() {
  if (!win) return;
  const scroll = dialog.querySelector(".aw-body")?.scrollTop ?? 0;
  dialog.replaceChildren(
    head(),
    el(
      "nav",
      { class: "subtabs aw-tabs", role: "tablist", "aria-label": `Artikel ${win.key}` },
      TABS.map(([id, label, placeholder]) =>
        el("button", {
          type: "button",
          role: "tab",
          "aria-selected": String(win.tab === id),
          disabled: placeholder,
          title: placeholder ? "kommt später" : null,
          onclick: () => showTab(id),
          text: label,
        }),
      ),
    ),
    el("div", { class: "aw-body", role: "tabpanel" }, win.error ? el("div", { class: "form-error", role: "alert", text: win.error }) : null, body()),
  );
  const node = dialog.querySelector(".aw-body");
  if (node) node.scrollTop = scroll;
}

function showTab(id) {
  win.tab = id;
  render();
  dialog.querySelector(".aw-body").scrollTop = 0;
  win.onNavigate?.(win.key, id);
}

function body() {
  if (win.tab === "rohling") return blankForm();
  if (win.tab === "preise") return priceForm();
  return win.detail ? runtimes(win.detail) : el("p", { class: "empty", text: "Lädt …" });
}

function articleTitle() {
  const a = win.article;
  if (a.kind === "rim") return `Felge ${a.rim?.design_name ?? a.order_key} · ${a.rim?.size ?? ""}`;
  return a.key;
}

function articleSub() {
  const a = win.article;
  const d = win.detail;
  if (a.kind === "rim") return `${a.rim?.kind_name ?? ""} · Felge ${a.order_key}`;
  const versions = d ? d.versions.length > 1 || d.versions.some((v) => v.version) || d.shared?.length : false;
  const what = a.version ? `${versionName(a.version)} von Auftrag ${a.order_key}` : versions ? `Grundversion von Auftrag ${a.order_key}` : `Auftrag ${a.order_key}`;
  return what;
}

function statusBadge(o, active) {
  if (active?.length) return stateBadge(active[0].state);
  return el("span", { class: `order-status ${o.status}`, text: STATUS_LABELS[o.status] ?? o.status });
}

/** Hat der Auftrag weitere Versionen? Dann gelten Abschließen, CSV und Löschen für alle. */
function otherVersions(d) {
  return (d?.all_versions ?? []).filter((v) => v !== win.version && (v || win.version));
}

function head() {
  const a = win.article;
  const d = win.detail;
  const o = d?.order;
  const others = otherVersions(d);
  const whole = others.length ? " (ganzer Auftrag)" : "";
  const title = el("input", { type: "text", maxlength: "120", value: o?.title ?? a.title ?? "", placeholder: "z. B. Gabelbrücke oben", "aria-label": "Auftragsname" });
  const note = el("input", { type: "text", maxlength: "500", value: a.note ?? "", placeholder: "z. B. Kunde, Zeichnungsstand", "aria-label": "Notiz zum Artikel" });
  const saveHead = async (e) => {
    e.preventDefault();
    try {
      if (o && title.value.trim() !== (o.title ?? "")) await send("PUT", `/api/orders/${encodeURIComponent(o.key)}`, { title: title.value });
      if (note.value.trim() !== (a.note ?? "")) await send("PUT", `/api/articles/${encodeURIComponent(a.key)}`, { note: note.value });
      await reload();
    } catch (err) {
      showError(err);
    }
  };
  const running = d?.runs?.some((r) => r.ended_at == null);
  return el(
    "header",
    { class: "aw-head" },
    el(
      "div",
      { class: "aw-info" },
      el(
        "div",
        { class: "order-head-row" },
        el(
          "div",
          { style: { flex: "1 1 260px", minWidth: "0" } },
          el("h2", { id: "aw-title" }, articleTitle(), (o?.title ?? a.title) ? el("span", { class: "muted", text: ` · ${o?.title ?? a.title}` }) : null),
          el("div", {
            class: "muted",
            text: [
              articleSub(),
              o ? `angelegt ${fmtDateTime(o.created_at)}` : null,
              o?.closed_at ? `abgeschlossen ${fmtDateTime(o.closed_at)}${o.closed_auto ? " (automatisch, ohne Programmlauf)" : ""}` : null,
            ].filter(Boolean).join(" · "),
          }),
        ),
        o ? statusBadge(o, d.active) : null,
        el("button", { type: "button", class: "btn aw-close", "aria-label": "Fenster schließen", onclick: () => dialog.close(), text: "✕" }),
      ),
      el(
        "div",
        { class: "order-head-row" },
        changeButton(a),
        o
          ? el("button", {
              type: "button",
              class: "btn",
              title: whole ? "Gilt für den ganzen Auftrag mit allen Versionen" : null,
              onclick: () => saveOrder(o.key, { status: o.status === "closed" ? "open" : "closed" }),
              text: `${o.status === "closed" ? "Wieder öffnen" : "Abschließen"}${whole}`,
            })
          : null,
        o ? el("a", { class: "btn", href: `/api/orders/${encodeURIComponent(o.key)}/export.csv`, download: true, text: `CSV Läufe${whole}` }) : null,
        o
          ? el("button", {
              type: "button",
              class: "btn danger",
              disabled: running,
              title: running ? "Löschen geht erst, wenn kein Lauf mehr läuft" : null,
              "aria-label": `${deleteName(o)} löschen`,
              onclick: () => deleteOrder(d),
              text: isRim(o) ? "Felge löschen" : whole ? "Ganzen Auftrag löschen" : "Auftrag löschen",
            })
          : null,
      ),
      el(
        "form",
        { class: "aw-head-form", onsubmit: saveHead },
        el("label", { class: "form-field" }, `Auftragsname${others.length ? " (alle Versionen)" : ""}`, title),
        el("label", { class: "form-field" }, "Notiz", note),
        el("button", { type: "submit", class: "btn", text: "Speichern" }),
      ),
      d ? activeBanner(d.active) : null,
    ),
    o ? imageBlock(o) : null,
  );
}

/** Knopf „Änderungen“: öffnet die Änderungsliste des Artikels (über dem Fenster). */
function changeButton(a) {
  const info = a.change;
  return el("button", {
    type: "button",
    class: `btn order-change ${info?.open ? "open" : ""}`,
    title: `Änderungsliste ${a.key}: Änderung eintragen, Bilder, umgesetzt`,
    "aria-label": `Änderungen ${a.key}`,
    onclick: () => openChanges(a.key, { onChanged: reload }),
    text: !info ? "+ Änderung" : info.open ? `Änderungen · ${info.label}, ${info.open} offen` : `Änderungen · ${info.label} ✓`,
  });
}

function activeBanner(active) {
  if (!active?.length) return null;
  return el(
    "div",
    { class: "active-list" },
    active.map((x) => {
      const f = x.forecast;
      const rest = f && !f.overdue_s ? ` · Rest ca. ${fmtDuration(f.remaining_s)} (fertig ca. ${fmtEta(f.eta)})` : "";
      return el("div", { class: "active-row" }, stateBadge(x.state), el("span", { text: `${x.machine}: ${x.program}${rest}` }));
    }),
  );
}

// --- Reiter Laufzeiten ---------------------------------------------------------------------------

function tile(label, value, sub) {
  return el(
    "div",
    {},
    el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value", text: value }),
    sub ? el("div", { class: "tile-sub", text: sub }) : null,
  );
}

function runtimes(d) {
  const o = d.order;
  const t = d.totals;
  const own = d.versions[0];
  const shared = t.shared_running_s > 0 ? `dazu gemeinsam ${fmtHours(t.shared_running_s)} (alle Versionen)` : null;
  return el(
    "div",
    { class: "aw-runtimes" },
    el(
      "div",
      { class: "kpi-tiles" },
      tile("Laufzeit", fmtHours(t.running_s), [t.fixture_s > 0 ? `davon Vorrichtung ${fmtHours(t.fixture_s)}` : "reine Bearbeitungszeit", shared].filter(Boolean).join(" · ")),
      tile("Gestoppt / Fehler", fmtHours(t.stopped_s), "innerhalb der Läufe"),
      tile(`Fertige ${isRim(o) ? "Felgen" : "Teile"}`, String(t.parts), `letztes Programm der letzten ${setupWord(o)} · ${t.finished} von ${t.runs} Läufen fertig`),
      tile(
        `Ø Bearbeitungszeit je ${partWord(o)}`,
        t.part_complete ? fmtDuration(t.part_run_s) : "—",
        [
          t.part_complete
            ? `Summe über alle ${isRim(o) ? "Spannungen und Programme" : "Aufspannungen"}${d.setups.some((s) => s.fixture) ? ", ohne Vorrichtung" : ""}`
            : "noch nicht jedes Programm vollständig gelaufen",
          own?.inherited?.length ? `inkl. ${setupWord(o)} ${own.inherited.join(", ")} der Grundversion` : null,
          t.plan_part_s ? `Plan (CAM) ${fmtDuration(t.plan_part_s)}` : null,
        ].filter(Boolean).join(" · "),
      ),
    ),
    ownBlock(d, o),
    sharedBlock(d, o),
    planForm(o),
    el("div", { class: "card section" }, el("div", { class: "section-head" }, el("h3", { text: "Laufzeit je Tag" }), legend(["RUNNING", "STOPPED"])), daysChart(d.days)),
    el(
      "div",
      { class: "card section" },
      el(
        "details",
        { open: runsOpen, ontoggle: (e) => (runsOpen = e.currentTarget.open) },
        el("summary", { text: `Programmdurchläufe (${d.runs.length})` }),
        el("div", { class: "table-wrap" }, runsTable(o, d.runs)),
      ),
    ),
  );
}

/** Eigene Aufspannungen des Artikels (bei einer Version mit Kopfzeile der Version). */
function ownBlock(d, o) {
  const v = d.versions[0];
  const setups = v?.setups ?? [];
  const grid = setups.length
    ? el("div", { class: "setup-grid" }, setups.map((s) => setupCard(s, o)))
    : el("p", { class: "empty", text: "Noch keine Programme. Sie erscheinen, sobald ein Programm läuft oder eine Planzeit eingetragen ist." });
  if (!win.version) return grid;
  return el(
    "section",
    { class: "version-block", "aria-label": versionName(win.version) },
    el(
      "div",
      { class: "version-head" },
      el("h2", { text: `${versionName(win.version)} · ${o.number}${win.version}` }),
      el("span", {
        class: "muted",
        text: [`${fmtHours(v?.running_s ?? 0)} Laufzeit`, `${v?.parts ?? 0} ${partsWord(o, v?.parts ?? 0)} fertig`].join(" · "),
      }),
    ),
    grid,
  );
}

/** Aufspannungen der Grundversion, die diese Version mitbenutzt: deutlich als gemeinsam markiert. */
function sharedBlock(d, o) {
  if (!d.shared?.length) return null;
  const users = [...new Set(d.shared.flatMap((s) => s.shared_with))].filter((v) => v !== win.version);
  return el(
    "section",
    { class: "version-block shared-block", "aria-label": "Gemeinsam mit anderen Versionen" },
    el(
      "div",
      { class: "version-head" },
      el("h2", { text: users.length ? `Gemeinsam mit ${users.join(", ")}` : "Aus der Grundversion" }),
      el("span", { class: "muted", text: `aus der Grundversion ${o.number} · ${fmtHours(d.totals.shared_running_s)} Laufzeit (alle Versionen)` }),
    ),
    el("p", {
      class: "shared-note",
      text: `Diese ${setupWord(o)}en gehören zur Grundversion und gelten für alle Versionen: Planzeit ändern, Ø zurücksetzen, Löschen und Bild wirken auf alle.`,
    }),
    el("div", { class: "setup-grid" }, d.shared.map((s) => setupCard(s, o))),
  );
}

function table(columns, rows) {
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
          {},
          columns.map((c) => {
            const value = c.value(row);
            return el("td", { class: c.cls, title: c.title?.(row) }, value instanceof Node ? value : value ?? "—");
          }),
        ),
      ),
    ),
  );
}

/** Andere Versionen, die eine Aufspannung mitbenutzen (ohne die eigene). */
const sharedUsers = (setup) => (setup.shared_with ?? []).filter((v) => v !== win.version);

function setupCard(setup, o) {
  const columns = [
    { label: "Programm", value: (p) => programCell(p.name, p.paths), title: programPaths, cls: "wrap" },
    { label: "Maschinen", value: (p) => p.machines.map(machineName).join(", ") || "—" },
    { label: "Läufe", value: (p) => String(p.runs), cls: "r" },
    { label: "Fertig", value: (p) => String(p.finished), cls: "r" },
    { label: "Laufzeit", value: (p) => fmtHours(p.running_s), cls: "r" },
    { label: "Gestoppt / Fehler", value: (p) => fmtHours(p.stopped_s), cls: "r" },
    { label: `Ø Laufzeit je ${partWord(o)}`, value: (p) => avgCell(o, p, setup), cls: "r" },
    { label: "Plan (CAM)", value: (p) => planCell(o, p, setup), cls: "r" },
    { label: "Letzter Lauf", value: (p) => fmtDateTime(p.last_run), cls: "r" },
    { label: "", value: (p) => deleteButton("Löschen", `Programm ${p.name} löschen`, p.open, () => deleteProgram(o, p, setup)), cls: "r" },
  ];
  const spannung = String(setup.setup).padStart(2, "0");
  let partText;
  if (setup.fixture) partText = "Vorrichtungsbau: einmaliger Aufwand, zählt nicht in die Ø-Bearbeitungszeit je Teil";
  else if (setup.part_complete) partText = `Ø Bearbeitungszeit je ${partWord(o)} in dieser ${setupWord(o)}: ${fmtDuration(setup.part_run_s)}`;
  else partText = `Ø je ${partWord(o)} erst, wenn jedes Programm dieser ${setupWord(o)} einmal vollständig gelaufen ist`;
  if (!setup.fixture && setup.plan_complete && setup.plan_s) partText += ` · Plan (CAM) ${fmtDuration(setup.plan_s)}`;
  let title = setup.setup ? `${setupWord(o)} ${setup.setup}` : `Ohne ${setupWord(o)}`;
  if (setup.fixture) title = `Vorrichtung (Spannung ${spannung})`;
  const users = sharedUsers(setup);
  const fullTitle = `${title}${setup.version ? ` (${versionName(setup.version)})` : ""}`;
  return el(
    "div",
    { class: `card${setup.fixture ? " setup-fixture" : ""}${users.length ? " setup-shared" : ""}` },
    el(
      "div",
      { class: "section-head" },
      el("h3", { text: title }),
      users.length ? el("span", { class: "shared-badge", title: "Gilt für alle diese Versionen", text: `gemeinsam mit ${users.join(", ")}` }) : null,
      el("span", { class: "muted", text: `${fmtHours(setup.running_s)} Laufzeit · ${setup.finished} fertige Läufe` }),
      el(
        "span",
        { class: "setup-delete" },
        deleteButton(`${setup.fixture ? "Vorrichtung" : setupWord(o)} löschen`, `${fullTitle} löschen`, setup.open, () => deleteSetup(o, setup, title)),
      ),
    ),
    setupFigure(o, setup, fullTitle),
    el("div", { class: "table-wrap" }, table(columns, setup.programs)),
    el("p", { class: "muted", style: { margin: "10px 0 0" }, text: partText }),
  );
}

/** Hinweis für Aktionen an einer gemeinsamen Aufspannung (wirkt auf alle Versionen). */
const sharedWarning = (setup) => (sharedUsers(setup).length ? `\n\nAchtung: Gilt auch für ${sharedUsers(setup).join(", ")} (gemeinsame ${setup.fixture ? "Vorrichtung" : "Aufspannung"} der Grundversion).` : "");

// --- CAM-Planzeiten und Ø-Zeit -----------------------------------------------------------------------

const hoursMinutes = (sec) => `${Math.floor(sec / 3600)}:${String(Math.round((sec % 3600) / 60)).padStart(2, "0")}`;
const PLAN_SOURCE = { pdf: "aus der Tebis-Doku", manual: "von Hand eingetragen" };

/** Ø-Laufzeit mit „zurücksetzen“ (Einfahren: Abbrüche, Neustarts) bzw. „aufheben“. */
function avgCell(o, p, setup) {
  const path = `/api/orders/${encodeURIComponent(o.key)}/programs/${encodeURIComponent(p.call_name)}/reset`;
  if (p.avg_reset_at != null) {
    return el(
      "span",
      { class: "plan-cell" },
      el("span", { text: fmtDuration(p.avg_run_s), title: `Ø zurückgesetzt am ${fmtDateTime(p.avg_reset_at)} – es zählen nur Läufe danach` }),
      el("span", { class: "muted avg-reset-mark", text: "neu ab " + fmtDateTime(p.avg_reset_at).split(",")[0] }),
      el("button", {
        type: "button",
        class: "link-button plan-edit",
        title: "Zurücksetzen aufheben: alle Läufe zählen wieder",
        onclick: () => resetAverage(path, "DELETE", null),
        text: "aufheben",
      }),
    );
  }
  if (p.avg_run_s == null) return "—";
  return el(
    "span",
    { class: "plan-cell" },
    el("span", { text: fmtDuration(p.avg_run_s) }),
    el("button", {
      type: "button",
      class: "link-button plan-edit",
      title: "Bisherige Läufe nicht mehr in die Ø-Zeit zählen",
      "aria-label": `Ø-Zeit von ${p.name} zurücksetzen`,
      onclick: () =>
        resetAverage(
          path,
          "POST",
          `Ø-Zeit von ${p.name} zurücksetzen?\n\n` +
            `Die bisherigen ${p.runs} Läufe bleiben mit ihren Zeiten erhalten, zählen aber nicht mehr in die Ø-Zeit, ` +
            "die Restlaufzeit-Prognose und die Artikelkosten. Bis zum nächsten fertigen Lauf gilt die CAM-Planzeit (falls eingetragen).\n\n" +
            "Lässt sich mit „aufheben“ rückgängig machen." +
            sharedWarning(setup),
        ),
      text: "zurücksetzen",
    }),
  );
}

async function resetAverage(path, method, question) {
  if (question && !confirm(question)) return;
  try {
    await send(method, path);
    await reload();
  } catch (err) {
    showError(err);
  }
}

function planCell(o, p, setup) {
  return el(
    "span",
    { class: "plan-cell" },
    el("span", { text: p.plan_s != null ? fmtDuration(p.plan_s) : "—", title: PLAN_SOURCE[p.plan_source] ?? null }),
    el("button", {
      type: "button",
      class: "link-button plan-edit",
      title: "Planzeit ändern",
      "aria-label": `Planzeit für ${p.name} ändern`,
      onclick: () => editPlan(o, p.name, p.plan_s, setup),
      text: "ändern",
    }),
  );
}

async function savePlan(o, program, time) {
  try {
    await send("PUT", `/api/orders/${encodeURIComponent(o.key)}/plans`, { program, time });
    await reload();
    return true;
  } catch (err) {
    showError(err);
    return false;
  }
}

function editPlan(o, program, current, setup) {
  const value = prompt(
    `Planzeit (CAM) für ${program}\nStunden (z. B. 4,5) oder h:mm (z. B. 4:30). Leer lassen = Planzeit entfernen.${sharedWarning(setup)}`,
    current != null ? hoursMinutes(current) : "",
  );
  if (value !== null) savePlan(o, program, value);
}

/** Planzeit für ein Programm, das hier noch keine Zeile hat (z. B. eine Version ohne Tebis-Doku). */
function planForm(o) {
  const program = el("input", {
    type: "text",
    class: "plan-program",
    placeholder: isRim(o) ? `${o.key}-01` : `26-${o.number}${win.version}-01-01`,
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

// --- Löschen: Lauf, Programm, Aufspannung, ganzer Auftrag ------------------------------------------

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

function deleteProgram(o, p, setup) {
  deleteItems(
    `/api/orders/${encodeURIComponent(o.key)}/programs/${encodeURIComponent(p.call_name)}`,
    `Programm ${p.name} aus ${deleteName(o)} löschen?\n\n` +
      `Gelöscht werden: ${countText(null, p.runs, p.plan_s != null ? 1 : 0)}.${sharedWarning(setup)}\n\n${DELETE_NOTE}`,
    reload,
  );
}

function deleteSetup(o, setup, title) {
  const runs = setup.programs.reduce((n, p) => n + p.runs, 0);
  const plans = setup.programs.filter((p) => p.plan_s != null).length;
  const version = setup.version ? `?version=${encodeURIComponent(setup.version)}` : "";
  deleteItems(
    `/api/orders/${encodeURIComponent(o.key)}/setups/${setup.setup}${version}`,
    `${title}${setup.version ? ` (${versionName(setup.version)})` : ""} aus ${deleteName(o)} löschen?\n\n` +
      `Gelöscht werden: ${countText(setup.programs.length, runs, plans)}${setup.image_url ? ", das Bild" : ""}.${sharedWarning(setup)}\n\n${DELETE_NOTE}`,
    reload,
  );
}

/** Ganzen Auftrag (alle Versionen) bzw. die Felge löschen – dafür lädt das Fenster einmal den ganzen
 *  Auftrag, damit die Rückfrage alles nennt, was verschwindet. */
async function deleteOrder(d) {
  const o = d.order;
  let all = d;
  try {
    all = await api(`/api/orders/${encodeURIComponent(o.key)}`);
  } catch {
    /* dann eben die Zahlen dieses Fensters */
  }
  const programs = all.setups.flatMap((s) => s.programs);
  const plans = programs.filter((p) => p.plan_s != null).length;
  const it = isRim(o) ? "die Felge" : "den Auftrag";
  const pictures = (o.image_url ? 1 : 0) + all.setups.filter((s) => s.image_url).length;
  const versions = all.versions.map((v) => v.version).filter(Boolean);
  deleteItems(
    `/api/orders/${encodeURIComponent(o.key)}`,
    `${deleteName(o)} komplett löschen${versions.length ? ` – mit allen Versionen (${versions.join(", ")})` : ""}?\n\n` +
      `Gelöscht werden: ${countText(programs.length, all.runs.length, plans)}${o.title ? ", die Bezeichnung" : ""}` +
      `${pictures ? `, ${pictures === 1 ? "das Bild" : `${pictures} Bilder`}` : ""}.\n\n` +
      `Ist ein Programm noch an einer Maschine angewählt oder läuft es später wieder, legt die App ${it} neu an – ohne die gelöschten Daten. ` +
      "Die Laufzeit der Maschine in der Auswertung bleibt erhalten.\n\nDas lässt sich nicht rückgängig machen.",
    async () => {
      win.changed = true;
      dialog.close();
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
    return el("div", { class: "day-row" }, el("span", { class: "date num", text: fmtDay(d.date) }), track, el("span", { class: "value num", text: fmtHours(d.running_s) }));
  });
  const node = el("div", { class: "day-rows" }, rows);
  tooltip().bind(node);
  return node;
}

const programName = (path) => path.split(/[\\/]/).pop().replace(/\.[^.]+$/, "");

function runsTable(o, runs) {
  const columns = [
    { label: "Nr.", value: (r) => String(r.id), cls: "r" },
    {
      label: "Programm",
      value: (r) =>
        r.shared
          ? el("span", {}, programName(r.program), " ", el("span", { class: "shared-badge", title: "Gemeinsame Aufspannung der Grundversion – keiner Version zuzuordnen", text: "gemeinsam" }))
          : programName(r.program),
    },
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
          : el("button", { type: "button", class: "btn small danger", "aria-label": `Lauf ${r.id} löschen`, onclick: () => deleteRun(o, r), text: "Löschen" }),
      cls: "r",
    },
  ];
  return runs.length ? table(columns, runs.slice().reverse()) : el("p", { class: "empty", text: "Noch keine Läufe." });
}

/** Fehllauf oder Nachprogramm aus dem Auftrag löschen (mit Rückfrage, nicht umkehrbar). */
async function deleteRun(o, r) {
  const what = [programName(r.program), machineName(r.machine_id), `${fmtDateTime(r.started_at)} – ${fmtDateTime(r.ended_at)}`, resultLabel(r.result), `Laufzeit ${fmtDuration(r.run_s)}`].join(" · ");
  const ok = confirm(
    `Lauf ${r.id} löschen?\n\n${what}\n\n` +
      `Der Lauf zählt danach nicht mehr zu ${isRim(o) ? "dieser Felge" : "diesem Auftrag"}, zur Ø-Bearbeitungszeit je ${partWord(o)} und zur Restlaufzeit-Prognose. ` +
      "Die Laufzeit der Maschine in der Auswertung bleibt erhalten.\n\nDas lässt sich nicht rückgängig machen.",
  );
  if (!ok) return;
  try {
    await send("DELETE", `/api/orders/${encodeURIComponent(o.key)}/runs/${r.id}`);
    await reload();
  } catch (err) {
    showError(err);
  }
}

async function saveOrder(key, changes) {
  try {
    await send("PUT", `/api/orders/${encodeURIComponent(key)}`, changes);
    await reload();
  } catch (err) {
    showError(err);
  }
}

// --- Bilder: Auftrag (fertiges Bauteil) und Aufspannung (Spannsituation) ------------------------

// Bild-Elemente je Adresse wiederverwenden: Das Fenster wird alle 20 s neu aufgebaut, ein neues <img>
// würde dabei kurz leer blinken. Ein neues Bild hat eine neue Adresse (?v=).
const images = new Map();
function cachedImg(url, props) {
  if (!images.has(url)) images.set(url, el("img", { src: url, ...props }));
  return images.get(url);
}

// Ziel eines Bildes: der Auftrag oder eine Aufspannung. ``id`` ordnet Hinweise und Neuzeichnen zu,
// ``render`` zeichnet den Bildbereich neu.
const targets = new Map();
const orderName = (o) => (isRim(o) ? `Felge ${o.rim?.design_name ?? o.number}` : `Auftrag ${o.number}`);

function orderTarget(o) {
  const t = { id: o.key, key: o.key, setup: null, version: "", name: `Bild von ${orderName(o)}`, hasImage: Boolean(o.image_url), render: () => imageBlock(o) };
  targets.set(t.id, t);
  return t;
}

function setupTarget(o, setup, title) {
  const id = `${o.key}|${setup.version}|${setup.setup}`;
  const t = { id, key: o.key, setup: setup.setup, version: setup.version, name: `Bild von ${title}`, hasImage: Boolean(setup.image_url), render: () => setupFigure(o, setup, title) };
  targets.set(id, t);
  return t;
}

function imagePath(t) {
  const base = `/api/orders/${encodeURIComponent(t.key)}`;
  return t.setup == null ? `${base}/image` : `${base}/setups/${t.setup}/image${t.version ? `?version=${encodeURIComponent(t.version)}` : ""}`;
}

function imageButtons(t, disabled, small = false) {
  const cls = `btn${small ? " small" : ""}`;
  const button = (text, source) => el("button", { type: "button", class: cls, disabled, onclick: () => pickImage(t, source) }, text);
  if (touch) return [button("Foto aufnehmen", "camera"), button("Aus Galerie", "gallery")];
  return [
    button(t.hasImage ? "Bild ersetzen" : "Bild hinzufügen", "gallery"),
    clipboardButton ? el("button", { type: "button", class: cls, disabled, onclick: () => imageFromClipboardApi(t) }, "Aus Zwischenablage") : null,
  ];
}

function imageNote(t, hint) {
  if (imageState.id === t.id && imageState.text) {
    return el("div", { class: imageState.error ? "form-error" : "order-image-note", role: "status", text: imageState.text });
  }
  return hint ? el("div", { class: "order-image-hint", text: hint }) : null;
}

/** Bild einer Aufspannung: erscheint auf der Live-Karte, solange ein Programm dieser Aufspannung läuft. */
function setupFigure(o, setup, title) {
  const t = setupTarget(o, setup, title);
  const busy = imageState.id === t.id && imageState.busy;
  const picture = setup.image_url
    ? el(
        "a",
        { class: "setup-image", href: setup.image_url, target: "_blank", rel: "noopener", title: "Bild in voller Größe öffnen" },
        cachedImg(setup.thumb_url, { alt: t.name, decoding: "async" }),
      )
    : el("div", { class: "setup-image empty", text: "Kein Bild" });
  let hint = "Erscheint auf der Live-Karte, solange ein Programm dieser " + setupWord(o) + " läuft.";
  if (!touch) hint += " Hier anklicken und Bild mit Strg+V einfügen geht auch.";
  return el(
    "figure",
    // Fokus im Bildbereich: Strg+V setzt das Bild dieser Aufspannung statt des Auftragsbilds
    { class: "setup-figure", tabindex: "0", "data-image-id": t.id, "aria-label": `${t.name}${setup.image_url ? "" : " (noch keins)"}` },
    picture,
    el(
      "figcaption",
      { class: "setup-image-body" },
      el(
        "div",
        { class: "order-image-actions" },
        imageButtons(t, busy, true),
        setup.image_url ? el("button", { type: "button", class: "btn small danger", disabled: busy, onclick: () => removeImage(t) }, "Bild entfernen") : null,
      ),
      imageNote(t, hint),
    ),
  );
}

function imageBlock(o) {
  const t = orderTarget(o);
  const own = imageState.id === t.id;
  const picture = o.image_url
    ? el(
        "a",
        { class: "order-image", href: o.image_url, target: "_blank", rel: "noopener", title: "Bild in voller Größe öffnen" },
        cachedImg(o.image_url, { alt: `Bild zu ${orderName(o)}`, decoding: "async" }),
      )
    : el("div", { class: "order-image empty", text: "Noch kein Bild vom Bauteil" });
  return el(
    "figure",
    { class: "order-figure aw-figure", "data-image-id": t.id },
    picture,
    el(
      "figcaption",
      { class: "order-image-actions" },
      imageButtons(t, own && imageState.busy, true),
      o.image_url ? el("button", { type: "button", class: "btn small danger", disabled: own && imageState.busy, onclick: () => removeImage(t) }, "Bild entfernen") : null,
    ),
    imageNote(t, touch ? null : "Bild kopieren (z. B. Screenshot) und hier mit Strg+V einfügen"),
  );
}

/** Hinweis setzen und den Bildbereich des Ziels neu zeichnen (``id`` null: nur Hinweis löschen). */
function setImageState(id, text = null, { error = false, busy = false } = {}) {
  const previous = imageState.id;
  Object.assign(imageState, { id, text, error, busy });
  for (const which of new Set([previous, id])) {
    const t = which != null ? targets.get(which) : null;
    const node = t && dialog?.querySelector(`[data-image-id="${CSS.escape(t.id)}"]`);
    node?.replaceWith(t.render());
  }
}

function pickImage(t, source) {
  const input = document.getElementById(source === "camera" ? "aw-camera-input" : "aw-image-input");
  input.value = "";
  input.dataset.target = t.id;
  input.click();
}

const kb = (bytes) => `${Math.max(1, Math.round(bytes / 1024))} KB`;

function uploadImage(e) {
  const file = e.target.files[0];
  const t = targets.get(e.target.dataset.target);
  if (file && t) uploadFile(t, file);
}

/** Bild aus der Zwischenablage (Strg+V im Fenster): ins Bild der Aufspannung, in deren Bildbereich der
 *  Fokus steht, sonst ins Auftragsbild. Text einfügen, z. B. in die Notiz, bleibt unberührt. */
function pasteImage(e) {
  const o = win?.detail?.order;
  // nur im Reiter Laufzeiten; ein Dialog darüber (z. B. Änderungsliste) behandelt Strg+V selbst
  if (!o || !dialog.open || win.tab !== "laufzeiten" || imageState.busy || document.querySelector("dialog[open]:not(.article-window)")) return;
  const item = [...(e.clipboardData?.items ?? [])].find((i) => i.kind === "file" && i.type.startsWith("image/"));
  const file = item?.getAsFile();
  if (!file) return;
  e.preventDefault();
  const focused = document.activeElement?.closest?.(".setup-figure")?.dataset.imageId;
  const t = (focused && targets.get(focused)) || orderTarget(o);
  if (t.hasImage && !confirm(`${t.name} durch das Bild aus der Zwischenablage ersetzen?`)) return;
  uploadFile(t, file);
}

/** Button „Aus Zwischenablage“ (Clipboard-API; der Browser fragt evtl. nach Erlaubnis). */
async function imageFromClipboardApi(t) {
  let file = null;
  try {
    for (const item of await navigator.clipboard.read()) {
      const type = item.types.find((ty) => ty.startsWith("image/"));
      if (type) {
        file = new File([await item.getType(type)], "Zwischenablage", { type });
        break;
      }
    }
  } catch {
    setImageState(t.id, "Kein Zugriff auf die Zwischenablage – Bild bitte mit Strg+V einfügen.", { error: true });
    return;
  }
  if (!file) {
    setImageState(t.id, "In der Zwischenablage ist kein Bild. Bild kopieren (z. B. Screenshot) und erneut versuchen.", { error: true });
    return;
  }
  if (t.hasImage && !confirm(`${t.name} durch das Bild aus der Zwischenablage ersetzen?`)) return;
  uploadFile(t, file);
}

async function uploadFile(t, file) {
  setImageState(t.id, "Bild wird verkleinert …", { busy: true });
  try {
    const [full, thumb] = await shrinkImage(file, [ORDER_IMAGE, ORDER_THUMB]);
    setImageState(t.id, `Bild wird hochgeladen (${kb(full.size + thumb.size)}) …`, { busy: true });
    // Beide Größen in einer Anfrage, damit nie ein Bild ohne Vorschau gespeichert wird
    await send("PUT", imagePath(t), new Blob([full, thumb], { type: "application/octet-stream" }), { "X-Image-Length": String(full.size) });
    setImageState(null);
    await reload();
  } catch (err) {
    setImageState(t.id, err.message, { error: true });
  }
}

async function removeImage(t) {
  if (!confirm(`${t.name} entfernen?`)) return;
  setImageState(t.id, "Bild wird entfernt …", { busy: true });
  try {
    await send("DELETE", imagePath(t));
    setImageState(null);
    await reload();
  } catch (err) {
    setImageState(t.id, err.message, { error: true });
  }
}

// --- Reiter Rohling und Preise -------------------------------------------------------------------

function volume(shape, a, b, c) {
  if (shape === "block" && a > 0 && b > 0 && c > 0) return (a * b * c) / 1e6;
  if (shape === "round" && a > 0 && b > 0) return (Math.PI * (a / 2) ** 2 * b) / 1e6;
  return null;
}

/** Speichern der Stammdaten (Rohling bzw. Preise) mit Rückmeldung im Formular. */
async function saveArticle(payload, error) {
  try {
    await send("PUT", `/api/articles/${encodeURIComponent(win.key)}`, payload);
    await reload();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

async function removeArticle(error) {
  const a = win.article;
  if (!confirm(`Stammdaten von Artikel ${a.key} entfernen (Rohling, EK, VK, Notiz)?\n\nHat der Auftrag Läufe oder Planzeiten dieser Version, erscheint der Artikel gleich wieder – ohne diese Angaben. Der Auftrag selbst bleibt.`)) return;
  try {
    await send("DELETE", `/api/articles/${encodeURIComponent(a.key)}`);
    win.changed = true;
    dialog.close();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

function formFooter(error, onSave) {
  return el(
    "div",
    { class: "aw-form-foot" },
    el("button", { type: "button", class: "btn danger", title: "Rohling, Preise und Notiz dieses Artikels entfernen", onclick: () => removeArticle(error), text: "Stammdaten entfernen" }),
    el("span", { class: "spacer" }),
    el("button", { type: "submit", class: "btn primary", onclick: onSave, text: "Speichern" }),
  );
}

function blankForm() {
  const a = win.article;
  const materials = win.materials;
  const material = el(
    "select",
    { "aria-label": "Material" },
    el("option", { value: "", text: materials.length ? "—" : "— (noch keine Materialien angelegt)" }),
    ...materials.map((m) => el("option", { value: String(m.id), text: m.price_per_kg != null ? `${m.name} (${fmtEur(m.price_per_kg)}/kg)` : m.name })),
  );
  material.value = a.material_id != null ? String(a.material_id) : "";
  const shape = el("select", { "aria-label": "Form" }, el("option", { value: "", text: "—" }), el("option", { value: "block", text: "Block (L × B × H)" }), el("option", { value: "round", text: "Rund (Ø × L)" }));
  shape.value = a.shape ?? "";
  const input = (value) => el("input", { type: "text", inputmode: "decimal", autocomplete: "off", value: toInput(value) });
  const [ia, ib, ic] = [input(a.dim_a), input(a.dim_b), input(a.dim_c)];
  const la = el("span", { text: "Länge (mm)" });
  const lb = el("span", { text: "Breite (mm)" });
  const wc = el("label", { class: "form-field" }, el("span", { text: "Höhe (mm)" }), ic);
  const dims = el("div", { class: "form-row three" }, el("label", { class: "form-field" }, la, ia), el("label", { class: "form-field" }, lb, ib), wc);
  const preview = el("span", { class: "field-hint", "aria-live": "polite" });
  const error = el("div", { class: "form-error", role: "alert", hidden: true });
  const update = () => {
    const round = shape.value === "round";
    la.textContent = round ? "Durchmesser (mm)" : "Länge (mm)";
    lb.textContent = round ? "Länge (mm)" : "Breite (mm)";
    wc.hidden = round;
    dims.hidden = !shape.value;
    const v = volume(shape.value, parseNum(ia.value), parseNum(ib.value), parseNum(ic.value));
    const m = materials.find((x) => String(x.id) === material.value);
    const kg = v != null && m?.density != null ? v * m.density : null;
    const parts = [];
    if (v != null) parts.push(kg != null ? `Gewicht ${num.format(kg)} kg (${num.format(v)} l)` : `Volumen ${num.format(v)} l`);
    if (m && m.density == null) parts.push("Dichte des Materials fehlt");
    if (m?.price_per_kg != null) parts.push(`${fmtEur(m.price_per_kg)} je kg`);
    if (kg != null && m?.price_per_kg != null) parts.push(`Materialpreis ${fmtEur(kg * m.price_per_kg)}`);
    preview.textContent = parts.join(" · ") || "Material, Form und Maße eintragen – dann rechnet die App den Materialpreis.";
  };
  for (const node of [material, shape]) node.addEventListener("change", update);
  for (const node of [ia, ib, ic]) node.addEventListener("input", update);
  update();
  const save = (e) => {
    e.preventDefault();
    error.hidden = true;
    saveArticle(
      {
        material_id: material.value || null,
        shape: shape.value || null,
        dim_a: shape.value ? ia.value : null,
        dim_b: shape.value ? ib.value : null,
        dim_c: shape.value === "block" ? ic.value : null,
      },
      error,
    );
  };
  return el(
    "form",
    { class: "aw-form", novalidate: true, onsubmit: save },
    el(
      "div",
      { class: "form-row two" },
      el("label", { class: "form-field" }, "Material", material, el("a", { class: "field-hint", href: "konfiguration.html#materialien", text: "Materialien verwalten" })),
      el("label", { class: "form-field" }, "Form", shape),
    ),
    dims,
    preview,
    error,
    formFooter(error, null),
  );
}

function priceForm() {
  const a = win.article;
  const ek = el("input", { type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "z. B. 120,00", value: toInput(a.price_ek) });
  const vk = el("input", { type: "text", inputmode: "decimal", autocomplete: "off", placeholder: "z. B. 249,00", value: toInput(a.price_vk) });
  const error = el("div", { class: "form-error", role: "alert", hidden: true });
  const costs = [
    a.part_s != null ? `Laufzeit je Teil ${fmtDuration(a.part_s)}` : null,
    a.material_cost != null ? `Material ${fmtEur(a.material_cost)}` : null,
    a.mill_cost != null ? `Fräsen ${fmtEur(a.mill_cost)}` : null,
    a.cost != null ? `Herstellkosten ${a.cost_complete ? "" : "mind. "}${fmtEur(a.cost)}` : null,
    a.margin != null ? `Marge ${fmtEur(a.margin)}${a.margin_pct != null ? ` (${num.format(a.margin_pct)} %)` : ""}` : null,
  ].filter(Boolean);
  const save = (e) => {
    e.preventDefault();
    error.hidden = true;
    saveArticle({ price_ek: ek.value, price_vk: vk.value }, error);
  };
  return el(
    "form",
    { class: "aw-form", novalidate: true, onsubmit: save },
    el("div", { class: "form-row two" }, el("label", { class: "form-field" }, "Preis EK (€)", ek), el("label", { class: "form-field" }, "Preis VK (€)", vk)),
    el("span", { class: "field-hint", text: costs.join(" · ") || "Kosten erscheinen, sobald Rohling und Laufzeit bekannt sind." }),
    error,
    formFooter(error, null),
  );
}
