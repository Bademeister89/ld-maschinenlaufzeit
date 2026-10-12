// Tab „Planung“: Board mit einer Spalte je Meilenstein. Karten ziehen ändert Meilenstein und
// Reihenfolge (oben = wichtigste); am Tablet geht das über „Bearbeiten“. Darunter die offenen Änderungen.

import { openChanges } from "./aenderungen.js";
import { api, el, loadMeta, send } from "./common.js";

const $ = (id) => document.getElementById(id);
const REFRESH_MS = 60000;
const DONE_DAYS = 14; // „Fertig“ zeigt nur die letzten 14 Tage, ältere auf Wunsch
const KEY = "ldm-planung-filter";
const PRIORITY = { 1: "hoch", 3: "niedrig" }; // „normal“ ohne Marke

let data = { milestones: [], workers: [], today: "" };
let articles = new Map();
let openChangeRows = [];
let showOldDone = false;
let editing = null; // Aufgabe im Dialog (null = neue)
let dragging = null; // id der gezogenen Aufgabe

const state = loadFilter();

function loadFilter() {
  try {
    return { worker: "all", overdue: false, ...JSON.parse(localStorage.getItem(KEY) || "{}"), search: "" };
  } catch {
    return { worker: "all", overdue: false, search: "" };
  }
}

function saveFilter() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ worker: state.worker, overdue: state.overdue }));
  } catch {
    /* privates Fenster: Filter nur für diese Sitzung */
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
  const [board, changes] = await Promise.all([api("/api/planning/board"), api("/api/planning/changes", { status: "open" })]);
  data = board;
  openChangeRows = changes.changes;
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

function render() {
  let shown = 0;
  const now = Date.now() / 1000;
  const columns = data.milestones.map((m) => {
    let tasks = m.tasks.filter(visible);
    let hidden = 0;
    if (m.done && !showOldDone) {
      const recent = tasks.filter((t) => !t.done_at || now - t.done_at < DONE_DAYS * 86400);
      hidden = tasks.length - recent.length;
      tasks = recent;
    }
    shown += tasks.length;
    return column(m, tasks, hidden);
  });
  $("board").replaceChildren(...columns);
  $("board").style.setProperty("--cols", String(data.milestones.length));
  const total = data.milestones.reduce((n, m) => n + m.tasks.length, 0);
  $("count").textContent = shown === total ? `${total} Aufgaben` : `${shown} von ${total} Aufgaben`;
  renderOpenChanges();
}

function column(m, tasks, hidden) {
  const list = el("div", { class: "board-list", "data-milestone": String(m.id) });
  list.append(...tasks.map(card));
  if (!tasks.length) list.append(el("p", { class: "board-empty", text: m.done ? "Noch nichts fertig." : "Keine Aufgaben." }));
  list.addEventListener("dragover", (e) => onDragOver(e, list));
  list.addEventListener("dragleave", (e) => {
    if (!list.contains(e.relatedTarget)) clearMarker();
  });
  list.addEventListener("drop", (e) => onDrop(e, m, list));
  return el(
    "section",
    { class: `board-col ${m.done ? "is-done" : ""}`, "aria-label": m.name },
    el("h3", { class: "board-col-head" }, el("span", { text: m.done ? `✓ ${m.name}` : m.name }), el("span", { class: "muted num", text: String(tasks.length) })),
    list,
    hidden || (m.done && showOldDone)
      ? el("button", {
          type: "button",
          class: "link-button board-more",
          onclick: () => ((showOldDone = !showOldDone), render()),
          text: showOldDone ? `nur die letzten ${DONE_DAYS} Tage` : `+ ${hidden} ältere zeigen`,
        })
      : null,
  );
}

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
          text: `Änderung ${t.change}`,
        })
      : null,
  ].filter(Boolean);
  const node = el(
    "article",
    {
      class: "task-card",
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
        requestAnimationFrame(() => node.classList.add("dragging"));
      },
      ondragend: () => {
        dragging = null;
        node.classList.remove("dragging");
        clearMarker();
      },
    },
    el("p", { class: "task-title", text: t.title }),
    t.article_key || t.worker
      ? el(
          "div",
          { class: "task-meta" },
          article?.thumb_url ? el("img", { class: "task-thumb", src: article.thumb_url, alt: "", loading: "lazy" }) : null,
          t.article_key ? el("span", { class: "task-article num", text: t.article_key }) : null,
          t.order_title ? el("span", { class: "task-order-title", text: t.order_title }) : null,
          t.worker ? el("span", { class: "task-worker", text: t.worker }) : null,
        )
      : null,
    badges.length ? el("div", { class: "task-badges" }, badges) : null,
  );
  return node;
}

// --- Ziehen und Ablegen -------------------------------------------------------------------------

let marker = null;

function clearMarker() {
  marker?.remove();
  marker = null;
}

/** Karte, vor der abgelegt würde (null = ans Ende). */
function cardAfter(list, y) {
  for (const node of list.querySelectorAll(".task-card:not(.dragging)")) {
    const r = node.getBoundingClientRect();
    if (y < r.top + r.height / 2) return node;
  }
  return null;
}

function onDragOver(e, list) {
  if (dragging == null) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  marker ??= el("div", { class: "drop-marker", "aria-hidden": "true" });
  const before = cardAfter(list, e.clientY);
  if (before) list.insertBefore(marker, before);
  else list.append(marker);
}

async function onDrop(e, m, list) {
  if (dragging == null) return;
  e.preventDefault();
  const id = dragging;
  const before = cardAfter(list, e.clientY);
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

// --- Offene Änderungen ----------------------------------------------------------------------------

function renderOpenChanges() {
  const rows = openChangeRows;
  if (!rows.length) {
    $("open-changes").replaceChildren(el("p", { class: "empty", text: "Keine offenen Änderungen." }));
    return;
  }
  const tr = (c) =>
    el(
      "tr",
      { class: "clickable", tabindex: "0", onclick: () => openChanges(c.article_key, { onChanged: reloadAll }), onkeydown: (e) => e.key === "Enter" && openChanges(c.article_key, { onChanged: reloadAll }) },
      el("td", { class: "num" }, c.article_key),
      el("td", { class: "num" }, c.label),
      el("td", { class: "wrap" }, c.description),
      el("td", {}, c.worker ?? "—"),
      el("td", { class: "r" }, fmtDate(c.created_at)),
      el("td", { class: "r num" }, c.images.length === 1 ? "1 Bild" : c.images.length ? `${c.images.length} Bilder` : ""),
    );
  $("open-changes").replaceChildren(
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "change-table" },
        el("thead", {}, el("tr", {}, ["Artikel", "Stand", "Was wird geändert", "Bearbeiter", "Seit", "Bilder"].map((h, i) => el("th", { class: i >= 4 ? "r" : null, text: h })))),
        el("tbody", {}, rows.map(tr)),
      ),
    ),
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
  $("t-milestone").replaceChildren(...data.milestones.map((m) => el("option", { value: String(m.id), text: m.name })));
  $("t-milestone").value = String(task ? task.milestone_id : data.milestones.find((m) => !m.done)?.id ?? data.milestones[0]?.id);
  $("t-priority").value = String(task?.priority ?? 2);
  $("t-due").value = task?.due_date ?? "";
  $("t-note").value = task?.note ?? "";
  $("t-move").hidden = !task;
  $("t-delete").hidden = !task;
  $("t-change").hidden = !task?.change;
  $("t-change").textContent = task?.change
    ? `Gehört zur Änderung ${task.change} von ${task.article_key}: In „Fertig“ gilt sie als umgesetzt, sonst als offen.`
    : "";
  updateChangesButton();
  setEditorError(null);
  if (!$("editor").open) $("editor").showModal();
  if (!task) $("t-title").focus();
}

function onArticleInput() {
  if (!titleTouched) $("t-order-title").value = orderTitleOf($("t-article").value);
  updateChangesButton();
}

function updateChangesButton() {
  const hint = articleHint($("t-article").value);
  $("t-article-hint").textContent = hint.text;
  $("t-article-hint").classList.toggle("is-error", !hint.valid);
  $("t-changes").hidden = !hint.valid || !normKey($("t-article").value);
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
  $("add-task").addEventListener("click", () => openEditor());
  $("add-change").addEventListener("click", () => openPicker());
  $("editor-form").addEventListener("submit", saveEditor);
  $("t-cancel").addEventListener("click", () => $("editor").close());
  $("t-delete").addEventListener("click", removeTask);
  $("t-up").addEventListener("click", () => step(-1));
  $("t-down").addEventListener("click", () => step(1));
  $("t-article").addEventListener("input", onArticleInput);
  $("t-order-title").addEventListener("input", () => (titleTouched = true));
  $("t-changes").addEventListener("click", () => openChanges(normKey($("t-article").value), { onChanged: reloadAll }));
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
