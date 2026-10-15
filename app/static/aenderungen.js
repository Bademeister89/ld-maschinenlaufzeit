// Änderungsliste eines Artikels (A01, A02 …): als eigener Dialog (Artikelliste, Auftragsdetail, Link
// im Alarm der Live-Ansicht) oder in einem Reiter, z. B. im Dialog „Aufgabe“ der Planung
// (``mountChanges``). Eine neue Änderung legt auf dem Board eine Aufgabe an; „umgesetzt“ und
// „erledigt“ (Fertig-Meilenstein) bleiben gleich. Eine Änderung kann an einer Spannung hängen.
//
// Bilder: Datei wählen, Foto (Handy/Tablet), Strg+V oder „Aus Zwischenablage“. Strg+V fügt in die
// Änderung ein, die zuletzt angeklickt wurde, sonst in die neue Änderung (wird mit ihr gespeichert).

import { api, el, send, shrinkImage, zoomThumb } from "./common.js";

const IMAGE = { maxPx: 1280, quality: 0.8 };
const THUMB = { maxPx: 256, quality: 0.7 };
const touch = matchMedia("(pointer: coarse)").matches;
// Zwischenablage per Button nur in sicherer Umgebung (https oder localhost); Strg+V geht überall
const clipboardButton = !touch && typeof navigator.clipboard?.read === "function";
const fmtDate = (t) => (t == null ? "" : new Date(t * 1000).toLocaleDateString("de-DE"));

let dialog = null;
let current = null;
let listening = false;

function listen() {
  if (listening) return;
  // Strg+V schon auf Dokumentebene abfangen (der Fokus kann nach dem Neuzeichnen außerhalb der Liste
  // liegen): Die Seite darunter (z. B. Auftragsbild) bekommt das Bild dann nicht
  document.addEventListener("paste", onPaste, true);
  listening = true;
}

function build() {
  dialog = el("dialog", { class: "changes-dialog", "aria-labelledby": "ch-title" });
  dialog.addEventListener("close", () => {
    if (current?.root === dialog) release();
  });
  document.body.append(dialog);
}

/** Liste verlassen: vorgemerkte Bilder freigeben, bei Änderungen ``onChanged`` aufrufen. */
function release() {
  for (const p of current?.pending ?? []) URL.revokeObjectURL(p.url);
  const { changed, onChanged } = current ?? {};
  current = null;
  if (changed) onChanged?.();
}

// milestone "" = erste Spalte (Standard), title = Auftragsname bei neuer Nummer, setup "" = alle Spannungen
const emptyDraft = () => ({ description: "", worker: "", due: "", milestone: "", title: "", setup: "" });

function start(articleKey, root, options) {
  if (current) release();
  listen();
  current = {
    key: articleKey,
    root, // Dialog oder Container (Reiter), in den die Liste gezeichnet wird
    active: options.active, // nimmt die Liste gerade Strg+V an?
    onChanged: options.onChanged,
    onLoad: options.onLoad, // nach jedem Laden mit den Daten (z. B. Zahl offener Änderungen am Reiter)
    onBack: options.onBack, // im Reiter: Knopf zurück zur Aufgabe
    changed: false,
    editing: null,
    error: null,
    busy: null, // Hinweis während Bilder verkleinert/hochgeladen werden
    data: null,
    target: null, // id der Änderung, in die Strg+V einfügt (null = neue Änderung)
    draft: emptyDraft(),
    pending: [], // Bilder der neuen Änderung: { file, url }
  };
}

/** Änderungsliste von ``articleKey`` als Dialog öffnen; ``onChanged`` läuft beim Schließen, wenn sich etwas geändert hat. */
export async function openChanges(articleKey, { onChanged } = {}) {
  if (!dialog) build();
  start(articleKey, dialog, { onChanged, active: () => dialog.open });
  await reload();
  if (!dialog.open) dialog.showModal();
}

/** Änderungsliste in ``container`` zeichnen (Reiter). ``active()``: Ist der Reiter gerade sichtbar?
 *  Zum Verlassen ``unmountChanges`` aufrufen – erst dann läuft ``onChanged``. */
export async function mountChanges(container, articleKey, { onChanged, onLoad, onBack, active } = {}) {
  start(articleKey, container, { onChanged, onLoad, onBack, active: active ?? (() => container.isConnected && !container.hidden) });
  await reload();
}

export function unmountChanges(container) {
  if (current?.root === container) {
    release();
    container.replaceChildren();
  }
}

async function reload() {
  const state = current;
  try {
    state.data = await api("/api/planning/changes", { article: state.key });
  } catch (err) {
    state.data ??= { changes: [], workers: [], milestones: [], exists: true, setups: null };
    state.error = err.message;
  }
  if (current !== state) return; // inzwischen eine andere Liste geöffnet
  if (state.target != null && !state.data.changes.some((c) => c.id === state.target)) state.target = null;
  render();
  state.onLoad?.(state.data);
}

async function act(fn) {
  try {
    await fn();
    current.changed = true;
    current.error = null;
    current.editing = null;
  } catch (err) {
    current.error = err.message;
  }
  current.busy = null;
  await reload();
}

// --- Bilder -------------------------------------------------------------------------------------

async function upload(changeId, file) {
  const [full, thumb] = await shrinkImage(file, [IMAGE, THUMB]);
  await send("PUT", `/api/planning/changes/${changeId}/images`, new Blob([full, thumb], { type: "application/octet-stream" }), {
    "X-Image-Length": String(full.size),
  });
}

/** Bilder zur Änderung ``target`` hochladen bzw. (``null``) der neuen Änderung vormerken. */
async function addImages(target, files) {
  const images = files.filter((f) => f.type.startsWith("image/"));
  if (!images.length) return;
  if (target == null) {
    current.pending.push(...images.map((file) => ({ file, url: URL.createObjectURL(file) })));
    current.error = null;
    render();
    return;
  }
  current.busy = images.length === 1 ? "Bild wird hochgeladen …" : `${images.length} Bilder werden hochgeladen …`;
  render();
  await act(async () => {
    for (const file of images) await upload(target, file);
  });
}

function onPaste(e) {
  if (!current?.active()) return;
  e.stopPropagation();
  const files = [...(e.clipboardData?.items ?? [])].filter((i) => i.kind === "file" && i.type.startsWith("image/")).map((i) => i.getAsFile());
  if (!files.length || current.busy || current.editing != null) return; // Text einfügen bleibt normal
  e.preventDefault();
  addImages(current.target, files);
}

async function fromClipboard(target) {
  let files = [];
  try {
    for (const item of await navigator.clipboard.read()) {
      const type = item.types.find((t) => t.startsWith("image/"));
      if (type) files.push(new File([await item.getType(type)], "Zwischenablage", { type }));
    }
  } catch {
    current.error = "Kein Zugriff auf die Zwischenablage – Bild bitte mit Strg+V einfügen.";
    render();
    return;
  }
  if (!files.length) {
    current.error = "In der Zwischenablage ist kein Bild. Bild kopieren (z. B. Screenshot) und erneut versuchen.";
    render();
    return;
  }
  addImages(target, files);
}

/** Knöpfe zum Hinzufügen: Datei, Foto (Handy/Tablet), Zwischenablage. */
function imageButtons(target, label) {
  const input = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true });
  input.addEventListener("change", () => addImages(target, [...input.files]));
  const camera = touch ? el("input", { type: "file", accept: "image/*", capture: "environment", hidden: true }) : null;
  camera?.addEventListener("change", () => addImages(target, [...camera.files]));
  return [
    el("button", { type: "button", class: "btn small", "aria-label": `Bild zu ${label} hinzufügen`, onclick: () => input.click(), text: "+ Bild" }),
    camera ? el("button", { type: "button", class: "btn small", onclick: () => camera.click(), text: "Foto" }) : null,
    clipboardButton ? el("button", { type: "button", class: "btn small", onclick: () => fromClipboard(target), text: "Aus Zwischenablage" }) : null,
    input,
    camera,
  ];
}

// --- Darstellung --------------------------------------------------------------------------------

function workerSelect(value, label, onchange) {
  const select = el(
    "select",
    { "aria-label": label },
    el("option", { value: "", text: "— kein Bearbeiter —" }),
    ...current.data.workers.map((w) => el("option", { value: String(w.id), text: w.name })),
  );
  select.value = value != null ? String(value) : "";
  if (onchange) select.addEventListener("change", () => onchange(select.value));
  return select;
}

/** „Aufspannung 2“ bzw. bei Felgen „Spannung 2“; null = alle Spannungen. */
function setupText(setup) {
  return setup == null ? null : `${current.data.setups?.label ?? "Spannung"} ${setup}`;
}

/** Auswahl der Spannung: „alle“ und die eigenen Spannungen des Artikels (ohne Vorrichtung, ohne die
 *  von der Grundversion übernommene). Ohne bekannte Spannung keine Auswahl (null). */
function setupSelect(value, onchange) {
  const info = current.data.setups;
  const own = info?.setups ?? [];
  const chosen = value == null || value === "" ? null : Number(value);
  const values = chosen != null && !own.includes(chosen) ? [...own, chosen].sort((a, b) => a - b) : own;
  if (!values.length) return null;
  const select = el(
    "select",
    { "aria-label": info.label },
    el("option", { value: "", text: `alle ${info.label}en` }),
    ...values.map((s) => el("option", { value: String(s), text: `${info.label} ${s}` })),
  );
  select.value = chosen != null ? String(chosen) : "";
  if (onchange) select.addEventListener("change", () => onchange(select.value));
  return select;
}

/** Hinweis, wenn eine Version die erste Spannung von der Grundversion übernimmt. */
function inheritedHint() {
  const info = current.data.setups;
  if (!info?.inherited?.length) return null;
  return el("span", {
    class: "field-hint",
    text: `${info.label} ${info.inherited.join(", ")} ist die gemeinsame der Grundversion ${info.base}. Änderungen daran bitte bei ${info.base} anlegen.`,
  });
}

function nextLabel() {
  const top = Math.max(0, ...current.data.changes.map((c) => c.number));
  return `A${String(top + 1).padStart(2, "0")}`;
}

async function createChange(e) {
  e.preventDefault();
  const { draft, pending } = current;
  await act(async () => {
    const change = await send("POST", "/api/planning/changes", {
      article_key: current.key,
      description: draft.description,
      worker_id: draft.worker || null,
      due_date: draft.due || null,
      milestone_id: draft.milestone || null,
      setup: draft.setup || null,
      ...(current.data.exists ? {} : { order_title: draft.title }),
    });
    current.draft = emptyDraft();
    current.pending = [];
    current.changed = true;
    if (pending.length) {
      current.busy = "Bilder werden hochgeladen …";
      render();
    }
    try {
      for (const p of pending) await upload(change.id, p.file);
    } finally {
      for (const p of pending) URL.revokeObjectURL(p.url);
    }
  });
}

function addForm() {
  const { draft } = current;
  const label = nextLabel();
  const text = el("textarea", { rows: "2", maxlength: "500", placeholder: "Was wird geändert? z. B. Bohrung 8,5 → 10 mm, Fase 1 mm", "aria-label": "Beschreibung der Änderung" });
  text.value = draft.description;
  text.addEventListener("input", () => (draft.description = text.value));
  const due = el("input", { type: "date", "aria-label": "Termin (optional)", value: draft.due || null });
  due.addEventListener("input", () => (draft.due = due.value));
  // Spalte des Boards, in die die Aufgabe der Änderung kommt (Standard: erste Spalte)
  const milestones = current.data.milestones ?? [];
  const firstOpen = milestones.find((m) => !m.done) ?? milestones[0];
  const milestone = el(
    "select",
    { "aria-label": "Meilenstein der Aufgabe" },
    milestones.map((m) => el("option", { value: String(m.id), text: m.done ? "✓ Erledigt (gleich umgesetzt)" : m.name })),
  );
  const setup = setupSelect(draft.setup, (v) => (draft.setup = v));
  milestone.value = draft.milestone || (firstOpen ? String(firstOpen.id) : "");
  milestone.addEventListener("change", () => (draft.milestone = milestone.value));
  // Neue Nummer: Der Auftrag entsteht mit der ersten Änderung – hier gleich seinen Namen eintragen
  let titleField = null;
  if (!current.data.exists) {
    const title = el("input", { type: "text", maxlength: "120", autocomplete: "off", placeholder: "z. B. Gabelbrücke oben", value: draft.title || null });
    title.addEventListener("input", () => (draft.title = title.value));
    titleField = el("label", { class: "form-field" }, `Auftragsname (neuer Auftrag ${current.key})`, title);
  }
  const pending = current.pending.map((p, index) =>
    el(
      "span",
      { class: "change-image" },
      el("img", { src: p.url, alt: `Bild ${index + 1} der neuen Änderung` }),
      el("button", {
        type: "button",
        class: "change-image-remove",
        title: "Bild entfernen",
        "aria-label": `Bild ${index + 1} entfernen`,
        onclick: () => {
          URL.revokeObjectURL(p.url);
          current.pending.splice(index, 1);
          render();
        },
        text: "✕",
      }),
    ),
  );
  const targeted = current.target == null;
  return el(
    "form",
    {
      class: `change-add ${targeted ? "is-target" : ""}`,
      onsubmit: createChange,
      novalidate: true,
      onfocusin: () => setTarget(null),
      onpointerdown: () => setTarget(null),
    },
    titleField,
    el("label", { class: "form-field" }, `Neue Änderung ${label}`, text),
    el("div", { class: "change-images" }, pending, imageButtons(null, `neuer Änderung ${label}`)),
    el(
      "div",
      { class: "change-add-row" },
      setup ? el("label", { class: "form-field" }, current.data.setups.label, setup) : null,
      el("label", { class: "form-field" }, "Meilenstein", milestone),
      el("label", { class: "form-field" }, "Bearbeiter", workerSelect(draft.worker, "Bearbeiter", (v) => (draft.worker = v))),
      el("label", { class: "form-field" }, "Termin (optional)", due),
      el("button", { type: "submit", class: "btn primary", disabled: !!current.busy, text: `${label} anlegen` }),
    ),
    inheritedHint(),
    el("span", {
      class: "field-hint",
      text: `${touch ? "" : "Bilder mit Strg+V einfügen. "}Legt dazu im Board oben im gewählten Meilenstein eine Aufgabe an. Ist sie erledigt, gilt die Änderung als umgesetzt.`
        + (setup ? ` Mit einer ${current.data.setups.label} kommt der Alarm auf der Live-Karte nur bei ihren Programmen.` : ""),
    }),
  );
}

/** Ziel für Strg+V wählen (Klick in eine Änderung bzw. in die neue Änderung). */
function setTarget(id) {
  if (current.target === id) return;
  current.target = id;
  for (const node of current.root.querySelectorAll(".is-target")) node.classList.remove("is-target");
  const node = id == null ? current.root.querySelector(".change-add") : current.root.querySelector(`.change-item[data-id="${id}"]`);
  node?.classList.add("is-target");
}

function imagesBlock(change) {
  return el(
    "div",
    { class: "change-images" },
    change.images.map((image) =>
      el(
        "span",
        { class: "change-image" },
        image.thumb_url
          ? el(
              "a",
              { href: image.image_url, target: "_blank", rel: "noopener", title: "Groß anzeigen (neuer Tab)" },
              zoomThumb(image.thumb_url, image.image_url, { className: "change-thumb", alt: `Bild zu ${change.label}`, contain: true }),
            )
          : el("span", { class: "muted", text: "Bild fehlt" }),
        el("button", {
          type: "button",
          class: "change-image-remove",
          title: "Bild entfernen",
          "aria-label": `Bild zu ${change.label} entfernen`,
          onclick: () => confirm("Dieses Bild entfernen?") && act(() => send("DELETE", `/api/planning/images/${image.id}`)),
          text: "✕",
        }),
      ),
    ),
    imageButtons(change.id, change.label),
    touch ? null : el("span", { class: "field-hint paste-hint", text: "Strg+V fügt hier ein" }),
  );
}

function changeItem(change) {
  const done = change.status === "done";
  const head = el(
    "div",
    { class: "change-head" },
    el("span", { class: "change-label num", text: change.label }),
    el("span", { class: `change-status ${done ? "done" : "open"}`, text: done ? "✓ umgesetzt" : "● offen" }),
    change.setup != null ? el("span", { class: "change-setup", text: setupText(change.setup) }) : null,
    el("span", { class: "muted change-meta", text: [fmtDate(change.created_at), change.worker, done ? `umgesetzt ${fmtDate(change.done_at)}` : null].filter(Boolean).join(" · ") }),
  );
  if (current.editing === change.id) {
    const text = el("textarea", { rows: "2", maxlength: "500", "aria-label": `Beschreibung ${change.label}` });
    text.value = change.description;
    const worker = workerSelect(change.worker_id, "Bearbeiter");
    const setup = setupSelect(change.setup);
    queueMicrotask(() => text.focus());
    const save = (e) => {
      e.preventDefault();
      act(() =>
        send("PUT", `/api/planning/changes/${change.id}`, {
          description: text.value,
          worker_id: worker.value || null,
          status: change.status,
          ...(setup ? { setup: setup.value || null } : {}),
        }),
      );
    };
    return el(
      "li",
      { class: "change-item", "data-id": String(change.id) },
      head,
      el(
        "form",
        { class: "change-edit", onsubmit: save, novalidate: true },
        text,
        el(
          "div",
          { class: "change-add-row" },
          setup,
          worker,
          el("button", { type: "submit", class: "btn primary", text: "Speichern" }),
          el("button", { type: "button", class: "btn", onclick: () => ((current.editing = null), render()), text: "Abbrechen" }),
        ),
      ),
    );
  }
  const toggle = () =>
    act(() => send("PUT", `/api/planning/changes/${change.id}`, { description: change.description, worker_id: change.worker_id, status: done ? "open" : "done" }));
  const remove = () => {
    if (confirm(`Änderung ${change.label} von ${current.key} entfernen? Die Aufgabe auf dem Board und die Bilder werden mit entfernt.`)) {
      act(() => send("DELETE", `/api/planning/changes/${change.id}`));
    }
  };
  return el(
    "li",
    {
      class: `change-item ${done ? "is-done" : ""} ${current.target === change.id ? "is-target" : ""}`,
      "data-id": String(change.id),
      onfocusin: () => setTarget(change.id),
      onpointerdown: () => setTarget(change.id),
    },
    head,
    el("p", { class: "change-text", text: change.description }),
    imagesBlock(change),
    el(
      "div",
      { class: "change-actions" },
      el("button", { type: "button", class: done ? "btn" : "btn primary", onclick: toggle, text: done ? "Wieder öffnen" : "Als umgesetzt markieren" }),
      el("button", { type: "button", class: "btn", onclick: () => ((current.editing = change.id), render()), text: "Bearbeiten" }),
      el("button", { type: "button", class: "btn danger", onclick: remove, text: "Entfernen" }),
    ),
  );
}

function render() {
  const { changes } = current.data;
  const open = changes.filter((c) => c.status === "open").length;
  const inDialog = current.root === dialog;
  const title = `Änderungen ${current.key}${current.data.order_title ? ` · ${current.data.order_title}` : ""}`;
  const back = current.onBack;
  current.root.replaceChildren(
    el(
      "div",
      { class: "changes-body" },
      el(
        "header",
        { class: "changes-header" },
        inDialog ? el("h2", { id: "ch-title", text: title }) : el("h3", { text: title }),
        el("span", { class: "muted", text: changes.length ? `${changes.length} Stände · ${open} offen` : "noch keine Änderung" }),
        el("span", { class: "spacer" }),
        inDialog
          ? el("button", { type: "button", class: "btn", onclick: () => dialog.close(), text: "Schließen" })
          : back
            ? el("button", { type: "button", class: "btn", onclick: () => back(), text: "← Zur Aufgabe" })
            : null,
      ),
      current.error ? el("div", { class: "form-error", role: "alert", text: current.error }) : null,
      current.busy ? el("div", { class: "form-msg", role: "status", text: current.busy }) : null,
      addForm(),
      changes.length ? el("ul", { class: "change-list" }, changes.map(changeItem)) : null,
    ),
  );
}
