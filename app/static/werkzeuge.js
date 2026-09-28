// Werkzeugauswertung: Einsatzzeit je Werkzeug und Maschine, Maximallaufzeit, Zurücksetzen.

import { api, el, fmtDateTime, fmtToolTime, loadMeta, send, setToolAlerts } from "./common.js";

const REFRESH_MS = 10000;
const $ = (id) => document.getElementById(id);

const query = new URLSearchParams(location.search);
const state = {
  machine: query.get("machine") ?? "",
  status: query.get("status") ?? "all",
  search: "",
};
let data = null;
let editing = null; // null = neues Werkzeug, sonst {machine_id, number}

const nfLimit = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const nfMm = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });
const fmtLimit = (sec) => `${nfLimit.format(sec / 3600)} h`;
// Eingabefelder: genau wie gespeichert (Dezimalkomma), damit Speichern ohne Änderung nichts rundet
const hoursInput = (sec) => (sec ? String(+(sec / 3600).toFixed(4)).replace(".", ",") : "");
const mmInput = (value) => (value == null ? "" : String(value).replace(".", ","));
const toolKey = (t) => `tool-${t.machine_id}-${t.number}`;

function showError(err) {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
}

function syncUrl() {
  const params = new URLSearchParams();
  if (state.machine) params.set("machine", state.machine);
  if (state.status !== "all") params.set("status", state.status);
  history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}${location.hash}`);
  for (const b of $("status").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.status === state.status));
  $("machine").value = state.machine;
}

// --- Filter -----------------------------------------------------------------------

function matches(t) {
  if (state.machine && t.machine_id !== state.machine) return false;
  if (state.status === "limit" && t.status === "none") return false;
  if (state.status === "warn" && t.status !== "warn") return false;
  if (state.status === "over" && t.status !== "over") return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  return `t${t.number} ${t.name} ${t.note} ${t.manufacturer} ${t.article_no}`.toLowerCase().includes(q);
}

// --- Darstellung -------------------------------------------------------------------

function statusBadge(t) {
  if (t.status === "over") return el("span", { class: "tool-badge over", text: "✕ Über Limit" });
  if (t.status === "warn") return el("span", { class: "tool-badge warn", text: "⚠ Vorwarnung" });
  return null;
}

function spindleBadge(t) {
  if (!t.in_spindle) return null;
  return el("span", { class: `tool-badge spindle${t.running ? " running" : ""}`, text: t.running ? "▶ Im Einsatz" : "● In der Spindel" });
}

/** "Ø 10 mm · R 0,5 mm · Garant · Art.-Nr. 202340" */
function specsText(t) {
  return [
    t.diameter != null && `Ø ${nfMm.format(t.diameter)} mm`,
    t.radius != null && `R ${nfMm.format(t.radius)} mm`,
    t.manufacturer,
    t.article_no && `Art.-Nr. ${t.article_no}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function usage(t) {
  const warnText = t.warn_at_s ? el("span", { class: "muted num", text: `Vorwarnung ${fmtLimit(t.warn_at_s)}` }) : null;
  if (!t.limit_s) {
    return el(
      "div",
      { class: "tool-usage" },
      el(
        "div",
        { class: "tool-usage-text" },
        el("span", { class: "tool-used num", text: fmtToolTime(t.used_s) }),
        el("span", { class: "muted", text: "kein Limit" }),
        warnText,
        el("button", { type: "button", class: "link-button", onclick: () => openEditor(t), text: "Limit eintragen" }),
      ),
    );
  }
  const pct = Math.round(t.ratio * 100);
  const over = t.used_s - t.limit_s;
  const warnAt = t.warn_at_s && t.warn_at_s < t.limit_s ? t.warn_at_s : null;
  const title =
    `T${t.number}: ${fmtToolTime(t.used_s)} von ${fmtLimit(t.limit_s)} (${pct} %)` +
    (warnAt ? `, Vorwarnung bei ${fmtLimit(warnAt)}` : "");
  return el(
    "div",
    { class: "tool-usage" },
    el(
      "div",
      {
        class: "meter tool-meter",
        role: "meter",
        "aria-label": `Einsatzzeit T${t.number}`,
        "aria-valuemin": "0",
        "aria-valuemax": String(Math.round(t.limit_s)),
        "aria-valuenow": String(Math.round(Math.min(t.used_s, t.limit_s))),
        "aria-valuetext": title,
        title,
      },
      el("div", { class: "meter-fill", style: { width: `${Math.min(100, t.ratio * 100)}%` } }),
      warnAt ? el("div", { class: "tool-warn-mark", style: { left: `${(warnAt / t.limit_s) * 100}%` } }) : null,
    ),
    el(
      "div",
      { class: "tool-usage-text" },
      el("span", { class: "tool-used num", text: fmtToolTime(t.used_s) }),
      el("span", { class: "muted num", text: `von ${fmtLimit(t.limit_s)}` }),
      el("span", { class: "tool-pct num", text: `${pct} %` }),
      over > 0 ? el("span", { class: "tool-over num", text: `${fmtToolTime(over)} drüber` }) : warnText,
    ),
  );
}

function toolRow(t) {
  return el(
    "div",
    { class: `tool-row st-${t.status}`, id: toolKey(t) },
    el(
      "div",
      { class: "tool-id" },
      el("div", { class: "tool-number num", text: `T${t.number}` }),
      el(
        "div",
        { class: "tool-names" },
        t.name ? el("div", { class: "tool-name", text: t.name }) : null,
        specsText(t) ? el("div", { class: "tool-specs", text: specsText(t) }) : null,
        t.note ? el("div", { class: "tool-note", text: t.note }) : null,
      ),
    ),
    usage(t),
    el("div", { class: "tool-flags" }, statusBadge(t), spindleBadge(t)),
    el(
      "div",
      { class: "tool-actions" },
      el("button", { type: "button", class: "btn", onclick: () => resetTool(t), text: "Zurücksetzen" }),
      el("button", { type: "button", class: "btn", onclick: () => openEditor(t), text: "Bearbeiten" }),
    ),
    el(
      "div",
      { class: "tool-meta muted" },
      [
        `gezählt seit ${fmtDateTime(t.reset_at)}`,
        t.last_used_at ? `zuletzt im Einsatz ${fmtDateTime(t.last_used_at)}` : "noch nicht im Einsatz",
      ].join(" · "),
    ),
  );
}

function tile(label, value, sub) {
  return el(
    "div",
    { class: "card" },
    el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value num", text: value }),
    sub ? el("div", { class: "tile-sub", text: sub }) : null,
  );
}

function renderAlert(tools) {
  const over = tools.filter((t) => t.status === "over" && (!state.machine || t.machine_id === state.machine));
  $("alert").hidden = !over.length;
  if (!over.length) return;
  $("alert").replaceChildren(
    el(
      "div",
      { class: "tool-alert-head" },
      el("span", { class: "tool-alert-icon", "aria-hidden": "true", text: "✕" }),
      over.length === 1
        ? "Achtung: Ein Werkzeug ist über der Maximallaufzeit"
        : `Achtung: ${over.length} Werkzeuge sind über der Maximallaufzeit`,
    ),
    el(
      "ul",
      {},
      over.map((t) =>
        el(
          "li",
          {},
          el("a", { href: `#${toolKey(t)}`, text: `${t.machine} · T${t.number}${t.name ? ` ${t.name}` : ""}` }),
          ` – ${fmtToolTime(t.used_s)} von ${fmtLimit(t.limit_s)}${t.in_spindle ? " – steckt in der Spindel" : ""}`,
        ),
      ),
    ),
  );
}

function render() {
  if (!data) return;
  const all = data.tools.filter((t) => !state.machine || t.machine_id === state.machine);
  const shown = data.tools.filter(matches);
  renderAlert(data.tools);
  setToolAlerts(data.alerts);

  $("kpis").replaceChildren(
    tile("Werkzeuge", String(all.length), `${all.filter((t) => t.limit_s).length} mit Maximallaufzeit`),
    tile("Über Limit", String(all.filter((t) => t.status === "over").length), "Werkzeug tauschen und zurücksetzen"),
    tile("Vorwarnung", String(all.filter((t) => t.status === "warn").length), "Vorwarnzeit erreicht (je Werkzeug einstellbar)"),
    tile("Im Einsatz", String(all.filter((t) => t.running).length), "Programm läuft mit diesem Werkzeug"),
  );

  const machines = data.machines.filter((m) => !state.machine || m.id === state.machine);
  $("lists").replaceChildren(
    ...machines.map((m) => {
      const rows = shown.filter((t) => t.machine_id === m.id);
      const total = all.filter((t) => t.machine_id === m.id).length;
      return el(
        "section",
        { class: "section card" },
        el(
          "div",
          { class: "section-head" },
          el("h2", { text: m.name }),
          el("span", {
            class: "muted",
            text: rows.length === total ? `${total} ${total === 1 ? "Werkzeug" : "Werkzeuge"}` : `${rows.length} von ${total}`,
          }),
        ),
        rows.length
          ? el("div", { class: "tool-list" }, rows.map(toolRow))
          : el("p", {
              class: "empty",
              text: total ? "Kein Werkzeug passt zum Filter." : "Noch keine Werkzeuge – sie erscheinen, sobald ein Werkzeug in der Spindel ist.",
            }),
      );
    }),
  );
}

async function load() {
  data = await api("/api/tools");
  if (!$("machine").options.length || $("machine").options.length !== data.machines.length + 1) {
    $("machine").replaceChildren(
      el("option", { value: "", text: "Alle" }),
      ...data.machines.map((m) => el("option", { value: m.id, text: m.name })),
    );
    if (!data.machines.some((m) => m.id === state.machine)) state.machine = "";
    $("machine").value = state.machine;
  }
  render();
}

async function refresh() {
  try {
    await load();
    $("banner").hidden = true;
  } catch (err) {
    showError(err);
  }
}

// --- Aktionen ------------------------------------------------------------------------

async function resetTool(t) {
  const ok = confirm(
    `T${t.number} an ${t.machine} zurücksetzen?\n\n` +
      `Neues Werkzeug eingespannt? Die Einsatzzeit (${fmtToolTime(t.used_s)}) wird auf 0 gesetzt. ` +
      "Der bisherige Stand bleibt als Standzeit in der Historie.",
  );
  if (!ok) return;
  try {
    await send("POST", `/api/tools/${encodeURIComponent(t.machine_id)}/${t.number}/reset`);
    await load();
  } catch (err) {
    showError(err);
  }
}

function setError(message) {
  $("f-error").textContent = message ?? "";
  $("f-error").hidden = !message;
}

/** Auswahl aus der Herstellerliste (Konfiguration); ein alter Eintrag, der nicht mehr in der
 * Liste steht, bleibt wählbar, damit Speichern ihn nicht stillschweigend löscht. */
function manufacturerOptions(current) {
  const names = data.manufacturers;
  const options = [el("option", { value: "", text: names.length ? "– kein Hersteller –" : "– noch keine Hersteller angelegt –" })];
  if (current && !names.includes(current)) options.push(el("option", { value: current, text: `${current} (nicht in der Liste)` }));
  options.push(...names.map((name) => el("option", { value: name, text: name })));
  return options;
}

function historyView(detail) {
  if (!detail.resets.length) return null;
  return el(
    "div",
    { class: "tool-history" },
    el("h3", { text: "Standzeiten bisher" }),
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, el("th", { text: "Zurückgesetzt am" }), el("th", { class: "r", text: "Einsatzzeit" }), el("th", { class: "r", text: "Limit" }))),
      el(
        "tbody",
        {},
        detail.resets.map((r) =>
          el(
            "tr",
            {},
            el("td", { text: fmtDateTime(r.reset_at) }),
            el("td", { class: "r", text: fmtToolTime(r.used_s) }),
            el("td", { class: "r", text: r.limit_s ? fmtLimit(r.limit_s) : "—" }),
          ),
        ),
      ),
    ),
    detail.avg_life_s ? el("p", { class: "muted", text: `Ø Standzeit: ${fmtToolTime(detail.avg_life_s)}` }) : null,
  );
}

async function openEditor(t = null) {
  editing = t ? { machine_id: t.machine_id, number: t.number } : null;
  setError(null);
  $("f-machine").replaceChildren(...data.machines.map((m) => el("option", { value: m.id, text: m.name })));
  $("f-machine").value = t?.machine_id ?? (state.machine || data.machines[0]?.id || "");
  $("f-number").value = t ? String(t.number) : "";
  // Neue Werkzeuge starten mit Standard-Maximallaufzeit und -Vorwarnzeit
  $("f-limit").value = hoursInput(t ? t.limit_s : data.default_limit_h * 3600);
  $("f-warn").value = hoursInput(t ? t.warn_s : data.default_warn_h * 3600);
  $("f-manufacturer").replaceChildren(...manufacturerOptions(t?.manufacturer ?? ""));
  $("f-manufacturer").value = t?.manufacturer ?? "";
  $("f-article").value = t?.article_no ?? "";
  $("f-diameter").value = mmInput(t?.diameter);
  $("f-radius").value = mmInput(t?.radius);
  $("f-note").value = t?.note ?? "";
  $("f-machine").disabled = $("f-number").disabled = Boolean(t);
  $("f-delete").hidden = !t;
  $("editor-title").textContent = t ? `T${t.number} · ${t.machine}` : "Werkzeug anlegen";
  $("f-history").replaceChildren();
  $("editor").showModal();
  (t ? $("f-manufacturer") : $("f-number")).focus();
  if (t) {
    try {
      const detail = await api(`/api/tools/${encodeURIComponent(t.machine_id)}/${t.number}`);
      const view = historyView(detail);
      if (view) $("f-history").replaceChildren(view);
    } catch (err) {
      setError(err.message);
    }
  }
}

async function save(e) {
  e.preventDefault();
  const value = (id) => $(id).value.trim();
  const payload = {
    manufacturer: value("f-manufacturer"),
    article_no: value("f-article"),
    diameter: value("f-diameter") || null,
    radius: value("f-radius") || null,
    limit_h: value("f-limit") || null,
    warn_h: value("f-warn") || null,
    note: value("f-note"),
  };
  $("f-save").disabled = true;
  try {
    if (editing) {
      await send("PUT", `/api/tools/${encodeURIComponent(editing.machine_id)}/${editing.number}`, payload);
    } else {
      await send("POST", "/api/tools", { ...payload, machine_id: $("f-machine").value, number: $("f-number").value.trim() });
    }
    $("editor").close();
    await load();
  } catch (err) {
    setError(err.message);
  } finally {
    $("f-save").disabled = false;
  }
}

async function removeTool() {
  const t = data.tools.find((x) => x.machine_id === editing?.machine_id && x.number === editing?.number);
  if (!t) return;
  const ok = confirm(
    `T${t.number} an ${t.machine} aus der Liste entfernen?\n\n` +
      "Werkzeugdaten, Limit, Notiz und die Historie der Standzeiten werden gelöscht. Taucht das Werkzeug wieder in der " +
      "Spindel auf, wird es neu angelegt und zählt ab dann.",
  );
  if (!ok) return;
  try {
    await send("DELETE", `/api/tools/${encodeURIComponent(t.machine_id)}/${t.number}`);
    $("editor").close();
    await load();
  } catch (err) {
    setError(err.message);
  }
}

// --- Start ---------------------------------------------------------------------------

async function main() {
  await loadMeta();
  syncUrl();
  $("status").addEventListener("click", (e) => {
    const status = e.target.closest("button")?.dataset.status;
    if (!status) return;
    state.status = status;
    syncUrl();
    render();
  });
  $("machine").addEventListener("change", (e) => {
    state.machine = e.target.value;
    syncUrl();
    render();
  });
  $("search").addEventListener("input", (e) => {
    state.search = e.target.value;
    render();
  });
  $("add").addEventListener("click", () => openEditor());
  $("editor-form").addEventListener("submit", save);
  $("f-cancel").addEventListener("click", () => $("editor").close());
  $("f-delete").addEventListener("click", removeTool);

  await refresh();
  syncUrl();
  // Sprung aus der Warnung auf der Live-Seite (#tool-…): erst nach dem Laden möglich
  if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "center" });
  setInterval(refresh, REFRESH_MS);
}

main().catch(showError);
