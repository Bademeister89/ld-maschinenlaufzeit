// Tab „Planung“: Board mit einer Spalte je Meilenstein. Karten ziehen ändert Meilenstein und
// Reihenfolge (oben = wichtigste); am Tablet geht das über „Bearbeiten“. Erledigte Aufgaben (der
// „Fertig“-Meilenstein) stehen nicht im Board, sondern in einer eigenen Liste darunter, ebenso die
// offenen und die umgesetzten Änderungen. Leere Spalten sind ausgeblendet, beim Ziehen erscheinen sie.

import { mountChanges, openChanges, unmountChanges } from "./aenderungen.js";
import { api, el, loadMeta, send, zoomThumb } from "./common.js";

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 60000;
const DONE_CHANGES_SHOWN = 20; // umgesetzte Änderungen: die neuesten, ältere auf Wunsch
const KEY = "ldm-planung-filter";
const PRIORITY = { 1: "hoch", 3: "niedrig" }; // „normal“ ohne Marke

let data = { milestones: [], workers: [], today: "" };
let articles = new Map();
let openChangeRows = [];
let doneChangeRows = [];
let showAllDoneChanges = false;
let editing = null; // Aufgabe im Dialog (null = neue)
let dragging = null; // id der gezogenen Aufgabe

const state = loadFilter();

function loadFilter() {
  try {
    return { worker: "all", overdue: false, layout: "rows", ...JSON.parse(localStorage.getItem(KEY) || "{}"), search: "" };
  } catch {
    return { worker: "all", overdue: false, layout: "rows", search: "" };
  }
}

function saveFilter() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ worker: state.worker, overdue: state.overdue, layout: state.layout }));
  } catch {
    /* privates Fenster: Filter nur für diese Sitzung */
  }
}

const DONE_CHANGES_KEY = "ldm-planung-umgesetzt"; // Liste „Umgesetzte Änderungen“ auf- oder zugeklappt

function loadOpen(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value == null ? fallback : value === "open";
  } catch {
    return fallback;
  }
}

function saveOpen(key, open) {
  try {
    localStorage.setItem(key, open ? "open" : "closed");
  } catch {
    /* privates Fenster: nur für diese Sitzung */
  }
}

function showError(err) {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
}

// Artikelnummer: Auftrag (4–5 Ziffern, optional Version V1 …) oder Felge (8 Ziffern)
const KEY_RE = /^(\d{4,5})(V\d{1,2})?$|^1\d{7}$/;
const normKey = (text) => text.replace(/\s+/g, "").toUpperCase();

/** Hinweis zur eingegebenen Nummer: bekannter Artikel, neue Version, neuer Auftrag oder ungültig. */
function articleHint(text) {
  const key = normKey(text);
  if (!key) return { valid: true, text: "" };
  const known = articles.get(key);
  if (known) return { valid: true, text: known.title || (known.kind === "rim" ? "Felge" : "") };
  const match = KEY_RE.exec(key);
  if (!match) return { valid: false, text: "Keine Artikelnummer – z. B. 21060, 21060V1 (Version) oder 10101018 (Felge)." };
  if (match[2] && articles.has(match[1])) return { valid: true, text: `Neue Version von ${match[1]} – wird beim Speichern angelegt.` };
  return { valid: true, text: `Neuer Auftrag ${key} – wird beim Speichern angelegt. Laufen später seine Programme, buchen sie hierauf.` };
}

const reloadAll = () => Promise.all([loadArticles(), refresh()]);

/** Bekannter Auftragsname zu einer Nummer (bei neuer Version der Name des Auftrags), sonst "". */
function orderTitleOf(text) {
  const key = normKey(text);
  const match = KEY_RE.exec(key);
  return articles.get(key)?.title ?? (match?.[2] ? articles.get(match[1])?.title : null) ?? "";
}

let titleTouched = false; // Auftragsname von Hand geändert: beim Ändern der Nummer nicht überschreiben

const fmtDue = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
const fmtDate = (t) => new Date(t * 1000).toLocaleDateString("de-DE");

// --- Laden ------------------------------------------------------------------------------------

async function refresh() {
  const [board, open, done] = await Promise.all([
    api("/api/planning/board"),
    api("/api/planning/changes", { status: "open" }),
    api("/api/planning/changes", { status: "done" }),
  ]);
  data = board;
  openChangeRows = open.changes;
  doneChangeRows = done.changes.sort((a, b) => (b.done_at ?? 0) - (a.done_at ?? 0));
  $("banner").hidden = true;
  renderWorkerFilter();
  render();
}

async function loadArticles() {
  const rows = (await api("/api/planning/articles")).articles;
  articles = new Map(rows.map((a) => [a.key, a]));
  $("article-list").replaceChildren(...rows.map((a) => el("option", { value: a.key, text: a.title || (a.kind === "rim" ? "Felge" : "") })));
}

// --- Filter -----------------------------------------------------------------------------------

function renderWorkerFilter() {
  const select = $("worker");
  const value = state.worker;
  select.replaceChildren(
    el("option", { value: "all", text: "alle" }),
    el("option", { value: "none", text: "ohne Bearbeiter" }),
    ...data.workers.map((w) => el("option", { value: String(w.id), text: w.name })),
  );
  select.value = [...select.options].some((o) => o.value === value) ? value : "all";
  state.worker = select.value;
}

function visible(task) {
  if (state.worker === "none" ? task.worker_id != null : state.worker !== "all" && String(task.worker_id) !== state.worker) return false;
  if (state.overdue && !task.overdue) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  const title = articles.get(task.article_key)?.title ?? "";
  return [task.title, task.article_key, title, task.worker, task.change].some((v) => v && v.toLowerCase().includes(q));
}

// --- Board ------------------------------------------------------------------------------------

const doneMilestone = () => data.milestones.find((m) => m.done);
const firstMilestone = () => data.milestones.find((m) => !m.done) ?? data.milestones[0];

function render() {
  let shown = 0;
  const board = data.milestones.filter((m) => !m.done);
  $("board").replaceChildren(
    ...board.map((m) => {
      const tasks = m.tasks.filter(visible);
      shown += tasks.length;
      return column(m, tasks);
    }),
  );
  const total = board.reduce((n, m) => n + m.tasks.length, 0);
  $("count").textContent = shown === total ? `${total} Aufgaben` : `${shown} von ${total} Aufgaben`;
  // Ganz leere Spalten sind ausgeblendet (nicht die, die erst der Filter leer macht)
  const empty = board.filter((m) => !m.tasks.length).map((m) => m.name);
  $("empty-cols").textContent = empty.length
    ? `Ausgeblendet, weil leer: ${empty.join(", ")}. Beim Ziehen einer Karte ${empty.length === 1 ? "erscheint sie" : "erscheinen sie"}.`
    : "";
  renderDoneTasks();
  renderOpenChanges();
  renderDoneChanges();
}

function column(m, tasks) {
  const list = el("div", { class: "board-list", "data-milestone": String(m.id) });
  list.append(...tasks.map(card));
  if (!tasks.length) list.append(el("p", { class: "board-empty", text: m.tasks.length ? "Keine passenden Aufgaben." : "Hierher ziehen" }));
  list.addEventListener("dragover", (e) => onDragOver(e, list));
  list.addEventListener("dragleave", (e) => {
    if (!list.contains(e.relatedTarget)) clearMarker();
  });
  list.addEventListener("drop", (e) => onDrop(e, m, list));
  return el(
    "section",
    { class: `board-col ${m.tasks.length ? "" : "is-empty"}`, "aria-label": m.name },
    el("h3", { class: "board-col-head" }, el("span", { text: m.name }), el("span", { class: "muted num", text: String(tasks.length) })),
    list,
  );
}

/** Aufgabe erledigen: in den „Fertig“-Meilenstein (nicht im Board); eine verknüpfte Änderung gilt dann als umgesetzt. */
const finish = (id) => move(id, doneMilestone().id, 0);
/** Erledigte Aufgabe wieder öffnen: oben in die erste Spalte; eine verknüpfte Änderung ist wieder offen. */
const reopen = (id) => move(id, firstMilestone().id, 0);

function card(t) {
  const article = t.article_key ? articles.get(t.article_key) : null;
  const badges = [
    PRIORITY[t.priority] ? el("span", { class: `task-badge prio-${t.priority}`, text: PRIORITY[t.priority] }) : null,
    t.due_date ? el("span", { class: `task-badge ${t.overdue ? "overdue" : "due"}`, title: t.overdue ? "überfällig" : "Termin", text: `${t.overdue ? "⚠ " : ""}bis ${fmtDue(t.due_date)}` }) : null,
    t.change
      ? el("button", {
          type: "button",
          class: `task-badge change ${t.change_status === "done" ? "done" : ""}`,
          title: "Änderungsliste öffnen",
          onclick: (e) => (e.stopPropagation(), openChanges(t.article_key, { onChanged: reloadAll })),
          text: `Änderung ${t.change}${t.change_setup != null ? ` · Sp. ${t.change_setup}` : ""}`,
        })
      : null,
  ].filter(Boolean);
  const thumb = article?.thumb_url ? zoomThumb(article.thumb_url, article.image_url, { className: "task-thumb", alt: `Bild ${t.article_key}` }) : null;
  const node = el(
    "article",
    {
      class: `task-card ${thumb ? "has-thumb" : ""}`,
      draggable: "true",
      tabindex: "0",
      "data-id": String(t.id),
      "aria-label": `${t.title}, öffnen zum Bearbeiten`,
      onclick: () => openEditor(t),
      onkeydown: (e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) openEditor(t);
      },
      ondragstart: (e) => {
        dragging = t.id;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(t.id));
        // erst im nächsten Bild umbauen, sonst bricht der Browser das Ziehen ab; leere Spalten erscheinen
        requestAnimationFrame(() => (node.classList.add("dragging"), $("board").classList.add("is-dragging")));
      },
      ondragend: () => {
        dragging = null;
        node.classList.remove("dragging");
        $("board").classList.remove("is-dragging");
        clearMarker();
      },
    },
    thumb,
    el("p", { class: "task-title", text: t.title }),
    t.article_key || t.worker
      ? el(
          "div",
          { class: "task-meta" },
          t.article_key ? el("span", { class: "task-article num", text: t.article_key }) : null,
          t.order_title ? el("span", { class: "task-order-title", text: t.order_title }) : null,
          t.worker ? el("span", { class: "task-worker", text: t.worker }) : null,
        )
      : null,
    el(
      "div",
      { class: "task-foot" },
      badges.length ? el("div", { class: "task-badges" }, badges) : null,
      el("button", {
        type: "button",
        class: "task-done-btn",
        title: t.change ? `Erledigt – vom Board nehmen, Änderung ${t.change} gilt dann als umgesetzt` : "Erledigt – vom Board nehmen",
        "aria-label": `${t.title}: erledigt`,
        onclick: (e) => (e.stopPropagation(), finish(t.id)),
        text: "✓ Erledigt",
      }),
    ),
  );
  return node;
}

// --- Ziehen und Ablegen -------------------------------------------------------------------------

let marker = null;

function clearMarker() {
  marker?.remove();
  marker = null;
  for (const node of document.querySelectorAll(".drop-before, .drop-end")) node.classList.remove("drop-before", "drop-end");
}

const rowsLayout = () => state.layout === "rows";

/** Karte, vor der abgelegt würde (null = ans Ende). In Zeilen liegen die Karten nebeneinander: dann
 *  zählt die Zeile und links oder rechts der Kartenmitte. */
function cardAfter(list, x, y) {
  for (const node of list.querySelectorAll(".task-card:not(.dragging)")) {
    const r = node.getBoundingClientRect();
    if (!rowsLayout() && y < r.top + r.height / 2) return node;
    if (rowsLayout() && (y < r.top || (y < r.bottom && x < r.left + r.width / 2))) return node;
  }
  return null;
}

function onDragOver(e, list) {
  if (dragging == null) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const before = cardAfter(list, e.clientX, e.clientY);
  if (rowsLayout()) {
    // Karten nebeneinander: Ein eingeschobener Strich würde sie verrücken, deshalb die Karte markieren
    clearMarker();
    (before ?? list).classList.add(before ? "drop-before" : "drop-end");
    return;
  }
  marker ??= el("div", { class: "drop-marker", "aria-hidden": "true" });
  if (before) list.insertBefore(marker, before);
  else list.append(marker);
}

async function onDrop(e, m, list) {
  if (dragging == null) return;
  e.preventDefault();
  const id = dragging;
  const before = cardAfter(list, e.clientX, e.clientY);
  clearMarker();
  // Stelle in der ganzen Spalte (Filter blenden Karten aus): vor der Karte „before“, sonst ans Ende
  const order = m.tasks.map((t) => t.id).filter((x) => x !== id);
  const index = before ? order.indexOf(Number(before.dataset.id)) : order.length;
  await move(id, m.id, index);
}

async function move(id, milestoneId, index) {
  try {
    await send("POST", `/api/planning/tasks/${id}/move`, { milestone_id: milestoneId, index });
    await refresh();
  } catch (err) {
    showError(err);
    await refresh();
  }
}

// --- Erledigte Aufgaben ---------------------------------------------------------------------------

function renderDoneTasks() {
  const done = doneMilestone();
  const all = done?.tasks ?? [];
  const rows = all.filter(visible).sort((a, b) => (b.done_at ?? 0) - (a.done_at ?? 0));
  $("done-count").textContent = rows.length === all.length ? `(${all.length})` : `(${rows.length} von ${all.length})`;
  if (!rows.length) {
    $("done-tasks").replaceChildren(el("p", { class: "empty", text: all.length ? "Keine passenden erledigten Aufgaben." : "Noch nichts erledigt." }));
    return;
  }
  const tr = (t) =>
    el(
      "tr",
      { class: "clickable", tabindex: "0", onclick: () => openEditor(t), onkeydown: (e) => e.key === "Enter" && e.target === e.currentTarget && openEditor(t) },
      el("td", { class: "r" }, t.done_at ? fmtDate(t.done_at) : "—"),
      el("td", { class: "wrap" }, t.title),
      el("td", { class: "num" }, [t.article_key, t.order_title].filter(Boolean).join(" · ") || "—"),
      el("td", {}, t.worker ?? "—"),
      el(
        "td",
        { class: "r" },
        el("button", { type: "button", class: "btn small", onclick: (e) => (e.stopPropagation(), reopen(t.id)), text: "Wieder öffnen" }),
      ),
    );
  $("done-tasks").replaceChildren(
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "change-table" },
        el("thead", {}, el("tr", {}, ["Erledigt", "Aufgabe", "Artikel", "Bearbeiter", ""].map((h, i) => el("th", { class: i === 0 || i === 4 ? "r" : null, text: h })))),
        el("tbody", {}, rows.map(tr)),
      ),
    ),
  );
}

// --- Offene und umgesetzte Änderungen ----------------------------------------------------------------

function changeTable(rows, dateLabel, dateOf) {
  const show = (c) => openChanges(c.article_key, { onChanged: reloadAll });
  const tr = (c) =>
    el(
      "tr",
      { class: "clickable", tabindex: "0", onclick: () => show(c), onkeydown: (e) => e.key === "Enter" && show(c) },
      el("td", { class: "num" }, c.article_key),
      el("td", { class: "num", title: c.setup != null ? `nur Spannung ${c.setup}` : "alle Spannungen" }, c.setup != null ? `${c.label} · Sp. ${c.setup}` : c.label),
      el("td", { class: "wrap" }, c.description),
      el("td", {}, c.worker ?? "—"),
      el("td", { class: "r" }, fmtDate(dateOf(c))),
      el("td", { class: "r num" }, c.images.length === 1 ? "1 Bild" : c.images.length ? `${c.images.length} Bilder` : ""),
    );
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      { class: "change-table" },
      el("thead", {}, el("tr", {}, ["Artikel", "Stand", "Was wird geändert", "Bearbeiter", dateLabel, "Bilder"].map((h, i) => el("th", { class: i >= 4 ? "r" : null, text: h })))),
      el("tbody", {}, rows.map(tr)),
    ),
  );
}

function renderOpenChanges() {
  const rows = openChangeRows;
  $("open-changes").replaceChildren(
    rows.length ? changeTable(rows, "Seit", (c) => c.created_at) : el("p", { class: "empty", text: "Keine offenen Änderungen." }),
  );
}

/** Umgesetzte Änderungen, neueste zuerst; ältere auf Wunsch. */
function renderDoneChanges() {
  const rows = doneChangeRows;
  $("done-changes-count").textContent = `(${rows.length})`;
  if (!rows.length) {
    $("done-changes").replaceChildren(el("p", { class: "empty", text: "Noch keine Änderung umgesetzt." }));
    return;
  }
  const shown = showAllDoneChanges ? rows : rows.slice(0, DONE_CHANGES_SHOWN);
  $("done-changes").replaceChildren(
    changeTable(shown, "Umgesetzt", (c) => c.done_at),
    rows.length > DONE_CHANGES_SHOWN
      ? el("button", {
          type: "button",
          class: "link-button board-more",
          onclick: () => ((showAllDoneChanges = !showAllDoneChanges), renderDoneChanges()),
          text: showAllDoneChanges ? `nur die neuesten ${DONE_CHANGES_SHOWN}` : `alle ${rows.length} zeigen`,
        })
      : null,
  );
}

// --- Dialog „Aufgabe“ -------------------------------------------------------------------------------

function setEditorError(message) {
  $("t-error").textContent = message ?? "";
  $("t-error").hidden = !message;
}

function columnOf(id) {
  return data.milestones.find((m) => m.tasks.some((t) => t.id === id));
}

function openEditor(task = null) {
  editing = task;
  $("editor-title").textContent = task ? "Aufgabe bearbeiten" : "Neue Aufgabe";
  $("t-title").value = task?.title ?? "";
  $("t-article").value = task?.article_key ?? "";
  $("t-order-title").value = task ? task.order_title ?? orderTitleOf(task.article_key ?? "") : "";
  titleTouched = false;
  $("t-worker").replaceChildren(
    el("option", { value: "", text: "— kein Bearbeiter —" }),
    ...data.workers.map((w) => el("option", { value: String(w.id), text: w.name })),
  );
  $("t-worker").value = task?.worker_id != null ? String(task.worker_id) : state.worker !== "all" && state.worker !== "none" ? state.worker : "";
  $("t-milestone").replaceChildren(
    ...data.milestones.map((m) => el("option", { value: String(m.id), text: m.done ? "✓ Erledigt (nicht im Board)" : m.name })),
  );
  $("t-milestone").value = String(task ? task.milestone_id : firstMilestone()?.id);
  $("t-priority").value = String(task?.priority ?? 2);
  $("t-due").value = task?.due_date ?? "";
  $("t-note").value = task?.note ?? "";
  const isDone = task != null && task.milestone_id === doneMilestone()?.id;
  $("t-move").hidden = !task || isDone;
  $("t-delete").hidden = !task;
  $("t-done").hidden = !task;
  $("t-done").textContent = isDone ? "Wieder öffnen" : "✓ Erledigt";
  $("t-change").hidden = !task?.change;
  $("t-change").textContent = task?.change
    ? `Gehört zur Änderung ${task.change} von ${task.article_key}: Ist die Aufgabe erledigt, gilt die Änderung als umgesetzt, sonst als offen.`
    : "";
  showTab("task");
  updateChangesTab();
  setEditorError(null);
  if (!$("editor").open) $("editor").showModal();
  if (!task) $("t-title").focus();
}

function onArticleInput() {
  if (!titleTouched) $("t-order-title").value = orderTitleOf($("t-article").value);
  updateChangesTab();
}

/** Artikel der Aufgabe, für den der Reiter „Änderungsliste“ gilt (null = keine gültige Nummer). */
function editorArticle() {
  const key = normKey($("t-article").value);
  return key && articleHint(key).valid ? key : null;
}

/** Reiter „Änderungsliste (2 offen)“: nur mit Artikel an der Aufgabe. */
function updateChangesTab(openCount) {
  const hint = articleHint($("t-article").value);
  $("t-article-hint").textContent = hint.text;
  $("t-article-hint").classList.toggle("is-error", !hint.valid);
  const key = editorArticle();
  const open = openCount ?? openChangeRows.filter((c) => c.article_key === key).length;
  const tab = $("tab-changes");
  tab.disabled = !key;
  tab.title = key ? `Änderungen von ${key}` : "Erst eine Artikelnummer eintragen";
  tab.textContent = key && open ? `Änderungsliste (${open} offen)` : "Änderungsliste";
}

/** Reiter wechseln. Die Änderungsliste wird beim Öffnen frisch geladen und beim Verlassen abgemeldet,
 *  dann lädt das Board neu, falls sich dort etwas geändert hat. */
function showTab(name) {
  const changes = name === "changes";
  $("tab-task").setAttribute("aria-selected", String(!changes));
  $("tab-changes").setAttribute("aria-selected", String(changes));
  $("editor-form").hidden = changes;
  $("changes-panel").hidden = !changes;
  if (!changes) {
    unmountChanges($("changes-panel"));
    return;
  }
  mountChanges($("changes-panel"), editorArticle(), {
    onChanged: reloadAll,
    onLoad: (d) => updateChangesTab(d.changes.filter((c) => c.status === "open").length),
    onBack: () => showTab("task"),
    active: () => $("editor").open && !$("changes-panel").hidden,
  });
}

function payload() {
  const key = normKey($("t-article").value);
  const title = $("t-order-title").value.trim();
  return {
    title: $("t-title").value,
    article_key: key || null,
    // Auftragsname nur mitschicken, wenn er sich ändert (neuer Auftrag: wenn einer eingetragen ist)
    ...(key && title !== orderTitleOf(key) ? { order_title: title } : {}),
    worker_id: $("t-worker").value || null,
    priority: Number($("t-priority").value),
    due_date: $("t-due").value || null,
    note: $("t-note").value,
    milestone_id: Number($("t-milestone").value),
  };
}

async function saveEditor(e) {
  e.preventDefault();
  const body = payload();
  try {
    if (editing) {
      await send("PUT", `/api/planning/tasks/${editing.id}`, body);
      if (body.milestone_id !== editing.milestone_id) {
        await send("POST", `/api/planning/tasks/${editing.id}/move`, { milestone_id: body.milestone_id, index: 0 });
      }
    } else {
      await send("POST", "/api/planning/tasks", body);
    }
    $("editor").close();
    await ((body.article_key && !articles.has(body.article_key)) || "order_title" in body ? reloadAll() : refresh());
  } catch (err) {
    setEditorError(err.message);
  }
}

async function step(delta) {
  const m = columnOf(editing.id);
  const order = m.tasks.map((t) => t.id);
  const index = order.indexOf(editing.id) + delta;
  if (index < 0 || index >= order.length) return;
  try {
    await send("POST", `/api/planning/tasks/${editing.id}/move`, { milestone_id: m.id, index });
    await refresh();
    setEditorError(null);
  } catch (err) {
    setEditorError(err.message);
  }
}

/** Knopf im Dialog: erledigen bzw. wieder öffnen; ungespeicherte Eingaben gehen dabei nicht verloren,
 *  weil zuerst gespeichert wird. */
async function toggleDone() {
  const isDone = editing.milestone_id === doneMilestone()?.id;
  try {
    await send("PUT", `/api/planning/tasks/${editing.id}`, payload());
    const target = isDone ? firstMilestone() : doneMilestone();
    await send("POST", `/api/planning/tasks/${editing.id}/move`, { milestone_id: target.id, index: 0 });
    $("editor").close();
    await reloadAll();
  } catch (err) {
    setEditorError(err.message);
  }
}

async function removeTask() {
  const note = editing.change ? `\n\nDie Änderung ${editing.change} bleibt in der Änderungsliste von ${editing.article_key}.` : "";
  if (!confirm(`Aufgabe „${editing.title}“ entfernen?${note}`)) return;
  try {
    await send("DELETE", `/api/planning/tasks/${editing.id}`);
    $("editor").close();
    await refresh();
  } catch (err) {
    setEditorError(err.message);
  }
}

// --- Neue Änderung: erst den Artikel wählen ------------------------------------------------------

function openPicker(value = "") {
  $("p-article").value = value;
  $("p-error").hidden = true;
  $("change-picker").showModal();
  $("p-article").focus();
}

function pickArticle(e) {
  e.preventDefault();
  const key = normKey($("p-article").value);
  if (!KEY_RE.test(key)) {
    $("p-error").textContent = key ? articleHint(key).text : "Bitte einen Artikel angeben.";
    $("p-error").hidden = false;
    return;
  }
  $("change-picker").close();
  openChanges(key, { onChanged: reloadAll });
}

// --- Start ------------------------------------------------------------------------------------------

async function main() {
  loadMeta().catch(() => {});
  $("worker").addEventListener("change", (e) => ((state.worker = e.target.value), saveFilter(), render()));
  $("overdue").checked = state.overdue;
  $("overdue").addEventListener("change", (e) => ((state.overdue = e.target.checked), saveFilter(), render()));
  $("search").addEventListener("input", (e) => ((state.search = e.target.value), render()));
  const applyLayout = () => {
    $("board").dataset.layout = state.layout;
    for (const b of $("layout").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.layout === state.layout));
  };
  applyLayout();
  $("layout").addEventListener("click", (e) => {
    const layout = e.target.closest("button")?.dataset.layout;
    if (!layout || layout === state.layout) return;
    state.layout = layout;
    saveFilter();
    applyLayout();
  });
  $("add-task").addEventListener("click", () => openEditor());
  $("add-change").addEventListener("click", () => openPicker());
  $("editor-form").addEventListener("submit", saveEditor);
  $("t-cancel").addEventListener("click", () => $("editor").close());
  $("t-delete").addEventListener("click", removeTask);
  $("t-done").addEventListener("click", toggleDone);
  $("done-changes-box").open = loadOpen(DONE_CHANGES_KEY, true);
  $("done-changes-box").addEventListener("toggle", () => saveOpen(DONE_CHANGES_KEY, $("done-changes-box").open));
  $("t-up").addEventListener("click", () => step(-1));
  $("t-down").addEventListener("click", () => step(1));
  $("t-article").addEventListener("input", onArticleInput);
  $("t-order-title").addEventListener("input", () => (titleTouched = true));
  $("tab-task").addEventListener("click", () => showTab("task"));
  $("tab-changes").addEventListener("click", () => editorArticle() && showTab("changes"));
  $("editor").addEventListener("close", () => showTab("task"));
  $("p-article").addEventListener("input", () => {
    $("p-error").hidden = true;
    const hint = articleHint($("p-article").value);
    if (hint.text) $("p-article-hint").textContent = hint.text;
  });
  $("picker-form").addEventListener("submit", pickArticle);
  $("p-cancel").addEventListener("click", () => $("change-picker").close());

  await Promise.all([loadArticles(), refresh()]);

  // Link aus dem Alarm der Live-Ansicht: planung.html?article=21053V1
  const article = new URLSearchParams(location.search).get("article");
  if (article) {
    state.search = article;
    $("search").value = article;
    render();
    openChanges(article.toUpperCase(), { onChanged: reloadAll });
  }

  setInterval(() => {
    if (dragging == null && !document.querySelector("dialog[open]")) refresh().catch(showError);
  }, REFRESH_MS);
}

main().catch(showError);
