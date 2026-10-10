// Tab „Artikel“: je Auftrag und Version (bzw. Felge) ein Artikel mit Rohling, Kosten, Preisen und
// Produktionsdaten. Bearbeiten im Dialog, Export als Excel-Datei.

import { api, el, fmtDuration, fmtHours, loadMeta, send } from "./common.js";

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
let editing = null;

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
    cls: "art-thumb-cell",
    value: (a) => {
      if (!a.thumb_url) return el("span", { class: "art-thumb empty-thumb", "aria-hidden": "true" });
      // Beim Drüberfahren groß: das große Bild erst dann laden (die Liste bleibt schnell)
      const zoom = el("img", { class: "art-zoom", alt: `Bild ${a.key}` });
      return el(
        "span",
        {
          class: "art-thumb-wrap",
          onmouseenter: (e) => {
            if (!zoom.src) zoom.src = a.image_url || a.thumb_url;
            // Rechts neben dem Vorschaubild, immer ganz im sichtbaren Bereich
            const r = e.currentTarget.getBoundingClientRect();
            const width = Math.min(360, innerWidth * 0.5);
            const height = (width * 3) / 4;
            zoom.style.width = `${width}px`;
            zoom.style.left = `${r.right + 10}px`;
            zoom.style.top = `${Math.max(8, Math.min(innerHeight - height - 8, r.top + r.height / 2 - height / 2))}px`;
          },
        },
        el("img", { class: "art-thumb", src: a.thumb_url, alt: "", loading: "lazy" }),
        zoom,
      );
    },
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
          { class: "art-key", href: `auftraege.html?status=all&order=${encodeURIComponent(a.order_key)}`, title: "Auftrag öffnen", onclick: (e) => e.stopPropagation() },
          a.order_key,
          a.version ? el("span", { class: "art-version", text: a.version }) : null,
        ),
        el("span", { class: "art-sub", text: [name(a), a.kind === "rim" ? "Felge" : ""].filter(Boolean).join(" · ") || "ohne Bezeichnung" }),
      ),
  },
  { label: "Material", cls: "wrap", edit: "material", value: (a) => a.material ?? "—" },
  { label: "Rohling (Maße)", cls: "wrap", edit: "dims", value: dimsCell },
  { label: "€ je kg", cls: "r", value: (a) => fmtEur(a.price_per_kg) },
  { label: "Material­preis", cls: "r", value: (a) => fmtEur(a.material_cost) },
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
  {
    label: "",
    cls: "r art-actions",
    value: (a) =>
      el("button", {
        type: "button",
        class: "btn small art-edit",
        title: "Bearbeiten",
        "aria-label": `Artikel ${a.key} bearbeiten`,
        onclick: (e) => (e.stopPropagation(), openEditor(a)),
        text: "✎",
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
    { class: a.status === "closed" ? "is-closed" : null, "data-key": a.key },
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
      (!needle || `${a.key} ${name(a)} ${a.material ?? ""} ${a.note ?? ""}`.toLowerCase().includes(needle)),
  );
}

function render() {
  const rows = visible();
  $("count").textContent = `${rows.length} ${rows.length === 1 ? "Artikel" : "Artikel"}`;
  if (!rows.length) {
    $("articles").replaceChildren(el("p", { class: "empty", text: data.articles.length ? "Kein Artikel passt zum Filter." : "Noch keine Artikel." }));
    return;
  }
  const sum = (f) => rows.reduce((s, a) => s + (a[f] ?? 0), 0);
  $("articles").replaceChildren(
    el(
      "table",
      { class: "art-table" },
      el("thead", {}, el("tr", {}, COLUMNS.map((c) => el("th", { class: c.cls, text: c.label })))),
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
}

function syncFilters() {
  for (const b of $("status").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.status === state.status));
  for (const b of $("kind").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.kind === state.kind));
  $("export").href = `/api/articles/export.xlsx?status=${state.status}`;
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
  if (!editing && !inline) render();  // nicht unter offenen Eingaben neu zeichnen
}

// --- Bearbeiten ----------------------------------------------------------------------------------

function volume(shape, a, b, c) {
  if (shape === "block" && a > 0 && b > 0 && c > 0) return (a * b * c) / 1e6;
  if (shape === "round" && a > 0 && b > 0) return (Math.PI * (a / 2) ** 2 * b) / 1e6;
  return null;
}

function renderShape() {
  const round = $("f-shape").value === "round";
  $("l-a").textContent = round ? "Durchmesser (mm)" : "Länge (mm)";
  $("l-b").textContent = round ? "Länge (mm)" : "Breite (mm)";
  $("w-c").hidden = round;
  $("f-dims").hidden = !$("f-shape").value;
  renderPreview();
}

function renderPreview() {
  const shape = $("f-shape").value;
  const v = volume(shape, parseNum($("f-a").value), parseNum($("f-b").value), parseNum($("f-c").value));
  const material = data.materials.find((m) => String(m.id) === $("f-material").value);
  const parts = [];
  const kg = v != null && material?.density != null ? v * material.density : null;
  if (v != null) parts.push(kg != null ? `Gewicht ${num.format(kg)} kg (${num.format(v)} l)` : `Volumen ${num.format(v)} l`);
  if (material && material.density == null) parts.push("Dichte des Materials fehlt");
  if (material?.price_per_kg != null) parts.push(`${fmtEur(material.price_per_kg)} je kg`);
  if (kg != null && material?.price_per_kg != null) parts.push(`Materialpreis ${fmtEur(kg * material.price_per_kg)}`);
  $("f-preview").textContent = parts.join(" · ") || "Material, Form und Maße eintragen – dann rechnet die App den Materialpreis.";
}

function openEditor(a) {
  editing = a;
  $("editor-title").textContent = `Artikel ${a.key}`;
  $("f-info").textContent = [name(a), a.kind === "rim" ? "Felge" : a.version ? `Version ${a.version}` : "Grundversion", `${a.parts} Stück produziert`]
    .filter(Boolean)
    .join(" · ");
  $("f-material").replaceChildren(
    el("option", { value: "", text: data.materials.length ? "—" : "— (noch keine Materialien angelegt)" }),
    ...data.materials.map((m) => el("option", { value: String(m.id), text: m.price_per_kg != null ? `${m.name} (${fmtEur(m.price_per_kg)}/kg)` : m.name })),
  );
  $("f-material").value = a.material_id != null ? String(a.material_id) : "";
  $("f-shape").value = a.shape ?? "";
  $("f-a").value = toInput(a.dim_a);
  $("f-b").value = toInput(a.dim_b);
  $("f-c").value = toInput(a.dim_c);
  $("f-ek").value = toInput(a.price_ek);
  $("f-vk").value = toInput(a.price_vk);
  $("f-note").value = a.note ?? "";
  $("f-costs").textContent = [
    a.part_s != null ? `Laufzeit je Teil ${fmtDuration(a.part_s)}` : null,
    a.mill_cost != null ? `Fräsen ${fmtEur(a.mill_cost)}` : null,
    a.cost != null ? `Herstellkosten ${a.cost_complete ? "" : "mind. "}${fmtEur(a.cost)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  $("f-error").hidden = true;
  renderShape();
  $("editor").showModal();
}

async function save(e) {
  e.preventDefault();
  const a = editing;
  try {
    await send("PUT", `/api/articles/${encodeURIComponent(a.key)}`, {
      material_id: $("f-material").value || null,
      shape: $("f-shape").value || null,
      dim_a: $("f-shape").value ? $("f-a").value : null,
      dim_b: $("f-shape").value ? $("f-b").value : null,
      dim_c: $("f-shape").value === "block" ? $("f-c").value : null,
      price_ek: $("f-ek").value,
      price_vk: $("f-vk").value,
      note: $("f-note").value,
    });
  } catch (err) {
    $("f-error").textContent = err.message;
    $("f-error").hidden = false;
    return;
  }
  $("editor").close();
}

async function remove() {
  const a = editing;
  if (!confirm(`Stammdaten von Artikel ${a.key} entfernen (Rohling, EK, VK, Notiz)?\n\nHat der Auftrag Läufe oder Planzeiten dieser Version, erscheint der Artikel gleich wieder – ohne diese Angaben. Der Auftrag selbst bleibt.`)) return;
  try {
    await send("DELETE", `/api/articles/${encodeURIComponent(a.key)}`);
  } catch (err) {
    $("f-error").textContent = err.message;
    $("f-error").hidden = false;
    return;
  }
  $("editor").close();
}

function openAdder() {
  $("a-key").value = "";
  $("a-error").hidden = true;
  $("adder").showModal();
}

async function addArticle(e) {
  e.preventDefault();
  try {
    const r = await send("POST", "/api/articles", { key: $("a-key").value });
    $("adder").close();
    await load();
    const a = data.articles.find((x) => x.key === r.key);
    if (a) openEditor(a);
  } catch (err) {
    $("a-error").textContent = err.message;
    $("a-error").hidden = false;
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
  $("adder-form").addEventListener("submit", addArticle);
  $("a-cancel").addEventListener("click", () => $("adder").close());
  $("editor-form").addEventListener("submit", save);
  $("f-cancel").addEventListener("click", () => $("editor").close());
  $("f-delete").addEventListener("click", remove);
  $("editor").addEventListener("close", () => {
    editing = null;
    load();
  });
  $("f-shape").addEventListener("change", renderShape);
  for (const id of ["f-a", "f-b", "f-c"]) $(id).addEventListener("input", renderPreview);
  $("f-material").addEventListener("change", renderPreview);
  syncFilters();
  await load();
  setInterval(load, REFRESH_MS);
}

main().catch(showError);
