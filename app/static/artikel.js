// Tab „Artikel“: je Auftrag und Version (bzw. Felge) ein Artikel mit Rohling, Kosten, Preisen und
// Produktionsdaten. Klick auf eine Zeile öffnet das Artikel-Fenster (artikel-detail.js) mit den Reitern
// Laufzeiten, Rohling und Preise; Material, Rohling, EK und VK lassen sich auch direkt in der Zelle
// bearbeiten. Excel-Export, CAM-Import (Tebis-Doku).

import { openChanges } from "./aenderungen.js";
import { TAB_IDS, openArticle } from "./artikel-detail.js";
import { api, el, fmtDateTime, fmtDuration, fmtHours, loadMeta, send, stateBadge, zoomThumb } from "./common.js";

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 60000;
const KEY = "ldm-artikel-filter";

const eur = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
const num = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });
const fmtEur = (v) => (v == null ? "—" : eur.format(v));
const fmtDate = (t) => (t == null ? "—" : new Date(t * 1000).toLocaleDateString("de-DE"));
const toInput = (v) => (v == null ? "" : String(v).replace(".", ","));
const parseNum = (text) => {
  const t = String(text ?? "").trim().replace(/\s|€/g, "");
  if (!t) return null;
  const n = Number(t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t);
  return Number.isFinite(n) ? n : NaN;
};

const state = loadFilter();
let data = { articles: [], materials: [] };
const selected = new Set(); // angehakte Artikel für den Excel-Export

function loadFilter() {
  try {
    return { status: "all", kind: "all", search: "", ...JSON.parse(localStorage.getItem(KEY) || "{}"), search: "" };
  } catch {
    return { status: "all", kind: "all", search: "" };
  }
}

function saveFilter() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ status: state.status, kind: state.kind }));
  } catch {
    /* privates Fenster: Filter nur für diese Sitzung */
  }
}

function showError(err) {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
}

const name = (a) => a.title || a.rim?.label || "";
const STATUS_LABELS = { open: "offen", closed: "abgeschlossen" };

/** Läuft gerade ein Programm des Artikels? Dann der Zustand mit Maschine, sonst offen/abgeschlossen. */
function statusCell(a) {
  if (a.active?.length) {
    const [x] = a.active;
    return el("span", { class: "art-status", title: a.active.map((r) => `${r.machine}: ${r.program}`).join("\n") }, stateBadge(x.state), el("span", { class: "art-sub", text: x.machine }));
  }
  return el("span", { class: `order-status ${a.status}`, text: STATUS_LABELS[a.status] ?? a.status });
}
const estimated = (text, flag, why) => (flag ? el("span", { class: "estimated", title: why, text: `≈ ${text}` }) : text);

// --- Liste ---------------------------------------------------------------------------------------

// Maße des Rohlings (das Material hat eine eigene Spalte), darunter Form und Volumen
function dimsText(a) {
  const f = (v) => num.format(v);
  if (a.shape === "block" && a.dim_a && a.dim_b && a.dim_c) return `${f(a.dim_a)} × ${f(a.dim_b)} × ${f(a.dim_c)}`;
  if (a.shape === "round" && a.dim_a && a.dim_b) return `Ø ${f(a.dim_a)} × ${f(a.dim_b)}`;
  return null;
}

function dimsCell(a) {
  const text = dimsText(a);
  if (!text) return "—";
  const sub = [a.shape === "round" ? "Rund, mm" : "Block, mm", a.weight_kg != null ? `${num.format(a.weight_kg)} kg` : null].filter(Boolean).join(" · ");
  return el("span", {}, text, el("span", { class: "art-sub", text: sub }));
}

const COLUMNS = [
  {
    label: "",
    cls: "art-select-cell",
    header: () =>
      el("input", {
        type: "checkbox",
        id: "select-all",
        title: "Alle angezeigten anhaken",
        "aria-label": "Alle angezeigten Artikel für den Export anhaken",
        onchange: (e) => selectAll(e.target.checked),
      }),
    value: (a) =>
      el("input", {
        type: "checkbox",
        class: "art-select",
        checked: selected.has(a.key),
        title: "Für den Excel-Export anhaken",
        "aria-label": `${a.key} für den Export anhaken`,
        onclick: (e) => e.stopPropagation(),
        onchange: (e) => {
          if (e.target.checked) selected.add(a.key);
          else selected.delete(a.key);
          syncSelection();
        },
      }),
  },
  {
    label: "",
    cls: "art-thumb-cell",
    value: (a) =>
      a.thumb_url
        ? zoomThumb(a.thumb_url, a.image_url, { alt: `Bild ${a.key}` })
        : el("span", { class: "art-thumb empty-thumb", "aria-hidden": "true" }),
  },
  {
    label: "Artikel / Bezeichnung",
    cls: "wrap art-main",
    value: (a) =>
      el(
        "span",
        {},
        el(
          "a",
          {
            class: "art-key",
            href: `artikel.html?article=${encodeURIComponent(a.key)}`,
            title: "Artikel öffnen (Laufzeiten, Rohling, Preise)",
            onclick: (e) => (e.preventDefault(), e.stopPropagation(), show(a.key)),
          },
          a.order_key,
          a.version ? el("span", { class: "art-version", text: a.version }) : null,
        ),
        el("span", { class: "art-sub", text: [name(a), a.kind === "rim" ? "Felge" : ""].filter(Boolean).join(" · ") || "ohne Bezeichnung" }),
      ),
  },
  { label: "Status", cls: "art-status-cell", value: statusCell },
  { label: "Material", cls: "wrap", edit: "material", value: (a) => a.material ?? "—" },
  { label: "Rohling (Maße)", cls: "wrap", edit: "dims", value: dimsCell },
  { label: "€ je kg", cls: "r", value: (a) => fmtEur(a.price_per_kg) },
  { label: "Material­preis", cls: "r", value: (a) => fmtEur(a.material_cost) },
  {
    label: "Maschine",
    cls: "wrap",
    value: (a) => {
      if (!a.machines?.length) return "—";
      const names = a.machines.join(", ");
      if (!a.machine_planned) return names;
      return el("span", { title: "Noch nicht (vollständig) gelaufen: Maschine aus der Tebis-Doku" }, names, el("span", { class: "art-sub", text: "laut Tebis-Doku" }));
    },
  },
  {
    label: "Laufzeit je Teil",
    cls: "r",
    value: (a) => {
      if (a.part_s == null) return "—";
      const time = estimated(fmtDuration(a.part_s), a.part_estimated, "Teils aus der CAM-Planzeit (noch nicht jedes Programm vollständig gelaufen)");
      if (!a.inherited?.length) return time;
      return el("span", { title: `Inklusive Spannung ${a.inherited.join(", ")} der Grundversion (gemeinsame Vorstufe)` }, time, el("span", { class: "art-sub", text: `inkl. Sp. ${a.inherited.join(", ")}` }));
    },
  },
  {
    label: "Stunden­satz",
    cls: "r",
    value: (a) =>
      a.rate == null
        ? a.part_s != null ? el("a", { class: "muted", href: "konfiguration.html#maschinen", title: "Stundensatz fehlt: Konfiguration → Maschinen → Bearbeiten", onclick: (e) => e.stopPropagation(), text: "fehlt" }) : "—"
        : estimated(`${fmtEur(a.rate)}/h`, a.mill_estimated, "Geschätzt: Durchschnitt der eingetragenen Stundensätze (Maschine unbekannt)"),
  },
  {
    label: "Fräsen",
    cls: "r",
    value: (a) =>
      a.mill_cost == null
        ? a.part_s != null ? el("span", { class: "muted", title: "Stundensatz fehlt: Konfiguration → Maschinen → Bearbeiten", text: "kein €/h" }) : "—"
        : estimated(fmtEur(a.mill_cost), a.mill_estimated || a.part_estimated, "Geschätzt: Planzeit bzw. durchschnittlicher Stundensatz"),
  },
  {
    label: "Herstellkosten",
    cls: "r",
    value: (a) => (a.cost == null ? "—" : a.cost_complete ? fmtEur(a.cost) : el("span", { class: "estimated", title: "Unvollständig: Material oder Fräsen fehlt", text: `mind. ${fmtEur(a.cost)}` })),
  },
  { label: "EK", cls: "r", edit: "price_ek", value: (a) => fmtEur(a.price_ek) },
  { label: "VK", cls: "r", edit: "price_vk", value: (a) => fmtEur(a.price_vk) },
  {
    label: "Marge",
    cls: "r",
    value: (a) =>
      a.margin == null
        ? "—"
        : el(
            "span",
            { class: a.margin < 0 ? "neg" : null },
            fmtEur(a.margin),
            a.margin_pct != null ? el("span", { class: "art-sub", text: `${num.format(a.margin_pct)} %` }) : null,
          ),
  },
  { label: "Stück", cls: "r", value: (a) => String(a.parts) },
  { label: "Gesamtlaufzeit", cls: "r", value: (a) => fmtHours(a.running_s) },
  { label: "Letzte Produktion", cls: "r", value: (a) => fmtDate(a.last_production) },
  { label: "Letzte Aktivität", cls: "r", value: (a) => (a.last_activity ? fmtDateTime(a.last_activity) : "—") },
  {
    label: "Änderung",
    cls: "art-change-cell",
    value: (a) =>
      el("button", {
        type: "button",
        class: `art-change ${a.change?.open ? "open" : ""}`,
        title: "Änderungsliste öffnen",
        "aria-label": `Änderungsliste ${a.key}`,
        onclick: (e) => (e.stopPropagation(), openChanges(a.key, { onChanged: load })),
        text: !a.change ? "+" : a.change.open ? `${a.change.label} · ${a.change.open} offen` : `${a.change.label} ✓`,
      }),
  },
];

// --- Bearbeiten direkt in der Zelle (wie in Excel) ---------------------------------------------------
// Material, Rohling, EK und VK: Klick (oder Enter) öffnet das Eingabefeld in der Zelle. Enter bzw.
// Verlassen speichert, Esc verwirft, Tab speichert und springt zur nächsten Zelle dieser Zeile.

const EDIT_ORDER = ["material", "dims", "price_ek", "price_vk"];
let inline = 0; // offene Zelleneditoren (dann nicht neu zeichnen)

function rowOf(a) {
  return el(
    "tr",
    {
      class: a.status === "closed" ? "is-closed" : null,
      "data-key": a.key,
      // Klick auf die Zeile öffnet das Fenster – nicht in bearbeitbaren Zellen, Haken, Knöpfen, Links
      onclick: (e) => {
        if (!e.target.closest("td.editable, input, select, button, a, .cell-pop")) show(a.key);
      },
    },
    COLUMNS.map((c) => {
      const v = c.value(a);
      const props = { class: c.cls, "data-label": c.label };
      if (c.edit) {
        Object.assign(props, {
          class: `${c.cls} editable`,
          "data-edit": c.edit,
          tabindex: "0",
          title: "Klicken zum Bearbeiten",
          onclick: (e) => startEdit(a, c.edit, e.currentTarget),
          onkeydown: (e) => {
            if (e.key === "Enter" && e.currentTarget === e.target) startEdit(a, c.edit, e.currentTarget);
          },
        });
      }
      return el("td", props, v instanceof Node ? v : v ?? "—");
    }),
  );
}

function replaceRow(a) {
  const old = document.querySelector(`.art-table tr[data-key="${CSS.escape(a.key)}"]`);
  if (old) old.replaceWith(rowOf(a));
}

function cellOf(key, field) {
  return document.querySelector(`.art-table tr[data-key="${CSS.escape(key)}"] td[data-edit="${field}"]`);
}

function startEdit(a, field, td) {
  if (td.classList.contains("is-editing")) return;
  td.classList.add("is-editing");
  inline += 1;
  let closed = false;
  const close = () => {
    if (closed) return false;
    closed = true;
    inline -= 1;
    return true;
  };
  const cancel = () => {
    if (close()) replaceRow(a);
  };
  const save = async (payload, next = null) => {
    if (!close()) return;
    try {
      const row = await send("PUT", `/api/articles/${encodeURIComponent(a.key)}`, payload);
      const index = data.articles.findIndex((x) => x.key === a.key);
      if (index >= 0) data.articles[index] = row;
      replaceRow(row);
      $("banner").hidden = true;
      if (next) {
        const target = cellOf(row.key, next);
        if (target) startEdit(row, next, target);
      }
    } catch (err) {
      showError(err);
      replaceRow(a);
    }
  };
  const nextField = (back) => EDIT_ORDER[EDIT_ORDER.indexOf(field) + (back ? -1 : 1)] ?? null;
  const keys = (e, commit) => {
    if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    } else if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Tab") {
      e.preventDefault();
      commit(nextField(e.shiftKey));
    }
  };

  if (field === "material") {
    const select = el(
      "select",
      { class: "cell-input", "aria-label": `Material für ${a.key}` },
      el("option", { value: "", text: data.materials.length ? "—" : "— (Liste: Konfiguration → Artikel)" }),
      ...data.materials.map((m) => el("option", { value: String(m.id), text: m.name })),
    );
    select.value = a.material_id != null ? String(a.material_id) : "";
    const commit = (next = null) => {
      const value = select.value || null;
      if (String(value ?? "") === String(a.material_id ?? "")) {
        cancel();
        if (next) cellOf(a.key, next)?.click();
      } else save({ material_id: value }, next);
    };
    select.addEventListener("change", () => commit());
    select.addEventListener("keydown", (e) => keys(e, commit));
    select.addEventListener("blur", () => commit());
    td.replaceChildren(select);
    select.focus();
    return;
  }

  if (field === "price_ek" || field === "price_vk") {
    const input = el("input", { type: "text", class: "cell-input r", inputmode: "decimal", value: toInput(a[field]), "aria-label": `${field === "price_ek" ? "Preis EK" : "Preis VK"} für ${a.key}` });
    const commit = (next = null) => {
      const value = parseNum(input.value);
      if (value === (a[field] ?? null)) {
        cancel();
        if (next) cellOf(a.key, next)?.click();
      } else save({ [field]: input.value }, next);
    };
    input.addEventListener("keydown", (e) => keys(e, commit));
    input.addEventListener("blur", () => commit());
    td.replaceChildren(input);
    input.focus();
    input.select();
    return;
  }

  // Rohling: kleines Fenster an der Zelle mit Form und Maßen
  const shape = el(
    "select",
    { class: "cell-input", "aria-label": "Form" },
    el("option", { value: "block", text: "Block L × B × H" }),
    el("option", { value: "round", text: "Rund Ø × L" }),
  );
  shape.value = a.shape ?? "block";
  const field_ = (value, label) => el("input", { type: "text", class: "cell-input r", inputmode: "decimal", value: toInput(value), placeholder: label, "aria-label": `${label} (mm)` });
  const ia = field_(a.dim_a, "L");
  const ib = field_(a.dim_b, "B");
  const ic = field_(a.dim_c, "H");
  const preview = el("div", { class: "cell-pop-hint" });
  const update = () => {
    const round = shape.value === "round";
    ia.placeholder = round ? "Ø" : "L";
    ib.placeholder = round ? "L" : "B";
    ic.hidden = round;
    const v = volume(shape.value, parseNum(ia.value), parseNum(ib.value), parseNum(ic.value));
    const m = data.materials.find((x) => x.id === a.material_id);
    preview.textContent = [
      v == null ? "Maße in mm" : m?.density != null ? `${num.format(v * m.density)} kg` : `${num.format(v)} l (Dichte fehlt)`,
      v != null && m?.density != null && m?.price_per_kg != null ? `Material ${fmtEur(v * m.density * m.price_per_kg)}` : null,
    ].filter(Boolean).join(" · ");
  };
  const commit = (next = null) => {
    const payload = {
      shape: shape.value,
      dim_a: ia.value,
      dim_b: ib.value,
      dim_c: shape.value === "block" ? ic.value : null,
    };
    const empty = !ia.value.trim() && !ib.value.trim() && (shape.value === "round" || !ic.value.trim());
    if (empty) payload.shape = null;  // alles leer: Rohling entfernen
    const same = (payload.shape ?? null) === (a.shape ?? null) && parseNum(ia.value) === (a.dim_a ?? null)
      && parseNum(ib.value) === (a.dim_b ?? null) && (shape.value === "round" || parseNum(ic.value) === (a.dim_c ?? null));
    document.removeEventListener("mousedown", outside, true);
    if (same) {
      cancel();
      if (next) cellOf(a.key, next)?.click();
    } else save(payload, next);
  };
  const pop = el(
    "div",
    { class: "cell-pop", role: "dialog", "aria-label": `Rohling für ${a.key}`, onclick: (e) => e.stopPropagation() },
    shape,
    el("div", { class: "cell-pop-dims" }, ia, ib, ic),
    preview,
    el(
      "div",
      { class: "cell-pop-actions" },
      el("button", { type: "button", class: "btn small", onclick: () => (document.removeEventListener("mousedown", outside, true), cancel()), text: "Abbrechen" }),
      el("button", { type: "button", class: "btn small primary", onclick: () => commit(), text: "Übernehmen" }),
    ),
  );
  function outside(e) {
    if (!pop.contains(e.target)) commit();
  }
  pop.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "Enter") {
      if (e.key === "Escape") document.removeEventListener("mousedown", outside, true);
      keys(e, commit);
    }
  });
  for (const input of [shape, ia, ib, ic]) input.addEventListener("input", update);
  shape.addEventListener("change", update);
  update();
  td.append(pop);
  ia.focus();
  document.addEventListener("mousedown", outside, true);
}

function visible() {
  const needle = state.search.trim().toLowerCase();
  return data.articles.filter(
    (a) =>
      (state.kind === "all" || a.kind === state.kind) &&
      (!needle || `${a.key} ${name(a)} ${a.material ?? ""} ${a.machines?.join(" ") ?? ""} ${a.note ?? ""}`.toLowerCase().includes(needle)),
  );
}

function render() {
  const rows = visible();
  $("count").textContent = `${rows.length} ${rows.length === 1 ? "Artikel" : "Artikel"}`;
  if (!rows.length) {
    $("articles").replaceChildren(el("p", { class: "empty", text: data.articles.length ? "Kein Artikel passt zum Filter." : "Noch keine Artikel." }));
    syncSelection();
    return;
  }
  const sum = (f) => rows.reduce((s, a) => s + (a[f] ?? 0), 0);
  $("articles").replaceChildren(
    el(
      "table",
      { class: "art-table" },
      el("thead", {}, el("tr", {}, COLUMNS.map((c) => el("th", { class: c.cls, text: c.label }, c.header?.())))),
      el("tbody", {}, rows.map(rowOf)),
      el(
        "tfoot",
        {},
        el(
          "tr",
          {},
          COLUMNS.map((c) =>
            el(
              "td",
              { class: c.cls, "data-label": c.label },
              c.label.startsWith("Artikel") ? "Summe" : c.label === "Stück" ? String(sum("parts")) : c.label === "Gesamtlaufzeit" ? fmtHours(sum("running_s")) : "",
            ),
          ),
        ),
      ),
    ),
  );
  syncSelection();
}

// --- Auswahl für den Excel-Export -----------------------------------------------------------------
// Angehakte Artikel bleiben über Filter und Neuladen hinweg gewählt. Ohne Haken exportiert der Knopf
// alles, was die Liste gerade zeigt.

function selectAll(on) {
  for (const a of visible()) {
    if (on) selected.add(a.key);
    else selected.delete(a.key);
  }
  for (const box of document.querySelectorAll(".art-select")) box.checked = selected.has(box.closest("tr").dataset.key);
  syncSelection();
}

function syncSelection() {
  const known = new Set(data.articles.map((a) => a.key));
  for (const key of selected) if (!known.has(key)) selected.delete(key); // Artikel entfernt
  const rows = visible();
  const shown = rows.filter((a) => selected.has(a.key)).length;
  const all = $("select-all");
  if (all) {
    all.checked = rows.length > 0 && shown === rows.length;
    all.indeterminate = shown > 0 && shown < rows.length;
  }
  const n = selected.size;
  $("export").textContent = n ? `Excel-Export (${n} angehakt)` : "Excel-Export";
  $("export").title = n ? "Nur die angehakten Artikel exportieren" : "Alle angezeigten Artikel exportieren (oder links Artikel anhaken)";
  $("clear-selection").hidden = !n;
  const hidden = n - shown;
  $("selection-note").hidden = !hidden;
  $("selection-note").textContent = hidden ? `davon ${hidden} gerade ausgeblendet (Filter)` : "";
}

function syncFilters() {
  for (const b of $("status").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.status === state.status));
  for (const b of $("kind").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.kind === state.kind));
}

async function load() {
  try {
    data = await api("/api/articles", { status: state.status });
    $("banner").hidden = true;
  } catch (err) {
    showError(err);
    return;
  }
  const notice = $("notice");
  notice.hidden = !data.rates_missing;
  if (data.rates_missing) {
    notice.replaceChildren(
      "Für den Preis Fräsen fehlt der Stundensatz der Maschinen: ",
      el("a", { href: "konfiguration.html#maschinen", text: "Konfiguration → Maschinen → Bearbeiten" }),
    );
  }
  if (!inline) render();  // nicht unter offenen Eingaben neu zeichnen
}

function volume(shape, a, b, c) {
  if (shape === "block" && a > 0 && b > 0 && c > 0) return (a * b * c) / 1e6;
  if (shape === "round" && a > 0 && b > 0) return (Math.PI * (a / 2) ** 2 * b) / 1e6;
  return null;
}

// --- Artikel-Fenster -----------------------------------------------------------------------------
// Adresse artikel.html?article=21053V1&tab=laufzeiten öffnet das Fenster gleich beim Laden. Gibt es
// keinen eigenen Artikel (z. B. 21054, wenn die Grundversion nur gemeinsame Vorstufe ihrer Versionen
// ist), zeigt die Liste stattdessen alle Artikel dieser Nummer.

/** Adresse an das offene Fenster anpassen (``key`` null: Fenster zu). */
function syncUrl(key, tab) {
  const q = new URLSearchParams();
  if (key) q.set("article", key);
  if (key && tab && tab !== "laufzeiten") q.set("tab", tab);
  history.replaceState(null, "", q.toString() ? `?${q}` : location.pathname);
}

async function show(key, tab = "laufzeiten") {
  const opened = await openArticle(key, { tab, onChanged: load, onNavigate: syncUrl });
  if (opened) return;
  syncUrl(null);
  const number = /^\d+/.exec(key)?.[0] ?? key;
  state.search = number;
  $("search").value = number;
  render();
  showNotice([`Zu ${key} gibt es keinen eigenen Artikel – hier alle Artikel zur Nummer ${number}.`], false);
}

function showNotice(lines, failed) {
  const notice = $("cam-notice");
  notice.classList.toggle("warn", failed);
  notice.replaceChildren(
    ...lines.map((line) => el("div", { text: line })),
    el("button", { type: "button", class: "link-button", onclick: () => (notice.hidden = true), text: "Schließen" }),
  );
  notice.hidden = false;
}

// --- CAM-Doku importieren -------------------------------------------------------------------------

/** Artikel zum ersten Programm eines importierten Auftrags: 26-21053V1-02-01 → 21053V1, Felge → Nummer. */
function articleOf(o) {
  const match = /^\d{2}[-_](\d{4,5})([Vv]\d{1,2})?[-_]/.exec(o.programs?.[0]?.program ?? "");
  return match ? `${match[1]}${(match[2] ?? "").toUpperCase()}` : o.key;
}

/** Tebis-Doku (PDF) importieren: legt Aufträge an und übernimmt die Planzeiten der Programme; danach
 *  öffnet sich das Fenster des ersten importierten Artikels. */
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
        open ??= articleOf(o);
      }
      if (r.ignored.length) lines.push(`${file.name}: ohne Auftragsnummer übergangen: ${r.ignored.join(", ")}`);
    } catch (err) {
      failed = true;
      lines.push(err.message);
    }
  }
  showNotice(lines, failed);
  await load();
  if (open) await show(open);
}

function openAdder() {
  $("a-key").value = "";
  $("a-title").value = "";
  $("a-error").hidden = true;
  $("adder").showModal();
}

async function addArticle(e) {
  e.preventDefault();
  try {
    const r = await send("POST", "/api/articles", { key: $("a-key").value, title: $("a-title").value || null });
    $("adder").close();
    await load();
    await show(r.key, "rohling");
  } catch (err) {
    $("a-error").textContent = err.message;
    $("a-error").hidden = false;
  }
}

// --- Excel-Export: angehakte Artikel, sonst alle angezeigten -----------------------------------------

async function startExport() {
  const keys = selected.size ? data.articles.filter((a) => selected.has(a.key)).map((a) => a.key) : visible().map((a) => a.key);
  if (!keys.length) {
    showError(new Error("Keine Artikel zum Exportieren – Filter prüfen oder Artikel anhaken."));
    return;
  }
  const selection = selected.size ? `${keys.length} angehakte Artikel` : `${keys.length} angezeigte Artikel`;
  const button = $("export");
  button.disabled = true;
  button.textContent = "Export läuft …";
  try {
    const res = await fetch("/api/articles/export.xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keys, selection }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {}
      throw new Error(detail);
    }
    const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "artikel.xlsx";
    const url = URL.createObjectURL(await res.blob());
    const link = el("a", { href: url, download: name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    $("banner").hidden = true;
  } catch (err) {
    showError(err);
  } finally {
    button.disabled = false;
    syncSelection();
  }
}

// --- Start ---------------------------------------------------------------------------------------

async function main() {
  await loadMeta();
  for (const b of $("status").querySelectorAll("button")) {
    b.addEventListener("click", () => {
      state.status = b.dataset.status;
      saveFilter();
      syncFilters();
      load();
    });
  }
  for (const b of $("kind").querySelectorAll("button")) {
    b.addEventListener("click", () => {
      state.kind = b.dataset.kind;
      saveFilter();
      syncFilters();
      render();
    });
  }
  $("search").addEventListener("input", (e) => {
    state.search = e.target.value;
    render();
  });
  $("add").addEventListener("click", openAdder);
  $("export").addEventListener("click", startExport);
  $("clear-selection").addEventListener("click", () => {
    selected.clear();
    for (const box of document.querySelectorAll(".art-select")) box.checked = false;
    syncSelection();
  });
  $("adder-form").addEventListener("submit", addArticle);
  $("a-cancel").addEventListener("click", () => $("adder").close());
  $("cam-import").addEventListener("click", () => $("cam-input").click());
  $("cam-input").addEventListener("change", importCam);
  syncFilters();
  // Direktlink: artikel.html?article=21053V1&tab=rohling (auch aus der Live-Karte und alten Lesezeichen).
  // Das Fenster wartet nicht auf die ganze Liste.
  const query = new URLSearchParams(location.search);
  const article = query.get("article")?.trim().toUpperCase();
  await Promise.all([load(), article ? show(article, TAB_IDS.includes(query.get("tab")) ? query.get("tab") : "laufzeiten") : null]);
  setInterval(() => !document.querySelector("dialog[open]") && load(), REFRESH_MS);
}

main().catch(showError);
