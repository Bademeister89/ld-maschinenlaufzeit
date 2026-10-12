// Änderungsliste eines Artikels (A01, A02 …) als Dialog – geöffnet aus der Planung, der Artikelliste,
// dem Auftragsdetail und über den Link im Alarm der Live-Ansicht. Eine neue Änderung legt auf dem
// Board eine Aufgabe an; „umgesetzt“ und die Fertig-Spalte des Boards bleiben gleich.
//
// Bilder: Datei wählen, Foto (Handy/Tablet), Strg+V oder „Aus Zwischenablage“. Strg+V fügt in die
// Änderung ein, die zuletzt angeklickt wurde, sonst in die neue Änderung (wird mit ihr gespeichert).

import { api, el, send, shrinkImage } from "./common.js";

const IMAGE = { maxPx: 1280, quality: 0.8 };
const THUMB = { maxPx: 256, quality: 0.7 };
const touch = matchMedia("(pointer: coarse)").matches;
// Zwischenablage per Button nur in sicherer Umgebung (https oder localhost); Strg+V geht überall
const clipboardButton = !touch && typeof navigator.clipboard?.read === "function";
const fmtDate = (t) => (t == null ? "" : new Date(t * 1000).toLocaleDateString("de-DE"));

let dialog = null;
let current = null;

function build() {
  dialog = el("dialog", { class: "changes-dialog", "aria-labelledby": "ch-title" });
  dialog.addEventListener("close", () => {
    for (const p of current?.pending ?? []) URL.revokeObjectURL(p.url);
    if (current?.changed) current.onChanged?.();
    current = null;
  });
  // Strg+V schon auf Dokumentebene abfangen (der Fokus kann nach dem Neuzeichnen außerhalb des
  // Dialogs liegen): Die Seite darunter (z. B. Auftragsbild) bekommt das Bild dann nicht
  document.addEventListener("paste", onPaste, true);
  document.body.append(dialog);
}

// milestone "" = erste Spalte (Standard), title = Auftragsname bei neuer Nummer
const emptyDraft = () => ({ description: "", worker: "", due: "", milestone: "", title: "" });

/** Änderungsliste von ``articleKey`` öffnen; ``onChanged`` läuft beim Schließen, wenn sich etwas geändert hat. */
export async function openChanges(articleKey, { onChanged } = {}) {
  if (!dialog) build();
  current = {
    key: articleKey,
    onChanged,
    changed: false,
    editing: null,
    error: null,
    busy: null, // Hinweis während Bilder verkleinert/hochgeladen werden
    data: null,
    target: null, // id der Änderung, in die Strg+V einfügt (null = neue Änderung)
    draft: emptyDraft(),
    pending: [], // Bilder der neuen Änderung: { file, url }
  };
  await reload();
  if (!dialog.open) dialog.showModal();
}

async function reload() {
  try {
    current.data = await api("/api/planning/changes", { article: current.key });
  } catch (err) {
    current.data ??= { changes: [], workers: [], milestones: [], exists: true };
    current.error = err.message;
  }
  if (current.target != null && !current.data.changes.some((c) => c.id === current.target)) current.target = null;
  render();
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
  if (!dialog.open || !current) return;
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
    milestones.map((m) => el("option", { value: String(m.id), text: m.done ? `${m.name} (gleich umgesetzt)` : m.name })),
  );
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
      el("label", { class: "form-field" }, "Meilenstein", milestone),
      el("label", { class: "form-field" }, "Bearbeiter", workerSelect(draft.worker, "Bearbeiter", (v) => (draft.worker = v))),
      el("label", { class: "form-field" }, "Termin (optional)", due),
      el("button", { type: "submit", class: "btn primary", disabled: !!current.busy, text: `${label} anlegen` }),
    ),
    el("span", {
      class: "field-hint",
      text: `${touch ? "" : "Bilder mit Strg+V einfügen. "}Legt dazu im Board oben im gewählten Meilenstein eine Aufgabe an. Ist sie in „Fertig“, gilt die Änderung als umgesetzt.`,
    }),
  );
}

/** Ziel für Strg+V wählen (Klick in eine Änderung bzw. in die neue Änderung). */
function setTarget(id) {
  if (current.target === id) return;
  current.target = id;
  for (const node of dialog.querySelectorAll(".is-target")) node.classList.remove("is-target");
  const node = id == null ? dialog.querySelector(".change-add") : dialog.querySelector(`.change-item[data-id="${id}"]`);
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
          ? el("a", { href: image.image_url, target: "_blank", rel: "noopener", title: "Groß anzeigen" }, el("img", { src: image.thumb_url, alt: `Bild zu ${change.label}`, loading: "lazy" }))
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
    el("span", { class: "muted change-meta", text: [fmtDate(change.created_at), change.worker, done ? `umgesetzt ${fmtDate(change.done_at)}` : null].filter(Boolean).join(" · ") }),
  );
  if (current.editing === change.id) {
    const text = el("textarea", { rows: "2", maxlength: "500", "aria-label": `Beschreibung ${change.label}` });
    text.value = change.description;
    const worker = workerSelect(change.worker_id, "Bearbeiter");
    queueMicrotask(() => text.focus());
    const save = (e) => {
      e.preventDefault();
      act(() => send("PUT", `/api/planning/changes/${change.id}`, { description: text.value, worker_id: worker.value || null, status: change.status }));
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
  dialog.replaceChildren(
    el(
      "div",
      { class: "changes-body" },
      el(
        "header",
        { class: "changes-header" },
        el("h2", { id: "ch-title", text: `Änderungen ${current.key}${current.data.order_title ? ` · ${current.data.order_title}` : ""}` }),
        el("span", { class: "muted", text: changes.length ? `${changes.length} Stände · ${open} offen` : "noch keine Änderung" }),
        el("span", { class: "spacer" }),
        el("button", { type: "button", class: "btn", onclick: () => dialog.close(), text: "Schließen" }),
      ),
      current.error ? el("div", { class: "form-error", role: "alert", text: current.error }) : null,
      current.busy ? el("div", { class: "form-msg", role: "status", text: current.busy }) : null,
      addForm(),
      changes.length ? el("ul", { class: "change-list" }, changes.map(changeItem)) : null,
    ),
  );
}
