// Konfigurations-Tab mit Untertabs: Maschinen (Liste, Fehlersammler), Werkzeuge (Hersteller),
// Artikel (Aufträge/Felgen), System (Allgemein, Diagnose), Versionen.

import { api, baseName, el, fmtDateTime, loadMeta, machineThumb, send, shrinkImage, stateBadge } from "./common.js";

const STATUS_MS = 5000;
const MACHINE_IMAGE = { maxPx: 1024, quality: 0.85 };
const $ = (id) => document.getElementById(id);

let machines = [];
let live = new Map(); // id → Live-Status
const probes = new Map(); // id → letztes Testergebnis (bleibt beim Neuzeichnen erhalten)

// --- Liste ---------------------------------------------------------------------

function showError(err) {
  $("banner").textContent = err.message;
  $("banner").hidden = false;
}

async function reload() {
  const data = await api("/api/config");
  machines = data.machines;
  renderSettings(data.settings);
  await refreshStatus();
}

async function refreshStatus() {
  try {
    const data = await api("/api/machines");
    live = new Map(data.machines.map((m) => [m.id, m]));
  } catch {
    live = new Map();
  }
  if (!busy) renderList();
}

function connectionText(m) {
  const status = live.get(m.id);
  if (!status || !status.state) return "Verbindung wird aufgebaut …";
  if (!status.connected) return status.connection_error ? `Keine Verbindung: ${status.connection_error}` : "Keine Verbindung";
  const control = [status.control?.control, status.control?.nc_sw].filter(Boolean).join(" · ");
  return `Verbunden${control ? ` · ${control}` : ""}`;
}

function renderList() {
  const list = $("machine-list");
  if (!machines.length) {
    list.replaceChildren(el("p", { class: "empty", text: "Noch keine Maschinen eingerichtet. Mit „+ Maschine hinzufügen“ die erste Steuerung eintragen." }));
    return;
  }
  list.replaceChildren(
    ...machines.map((m, index) => {
      const status = live.get(m.id);
      const probeBox = el("div", { class: "probe-result" });
      if (probes.has(m.id)) probeBox.append(probes.get(m.id));
      return el(
        "div",
        { class: "config-row" },
        machineThumb(m),
        el(
          "div",
          { class: "info" },
          el("div", { class: "name" }, m.name, status?.state ? stateBadge(status.state) : null),
          el("div", {
            class: "sub",
            text: [`${m.host}:${m.port}`, m.note, m.check_host && `Prüfadresse ${m.check_host}`, m.tool_slots && `${m.tool_slots} Werkzeugplätze`]
              .filter(Boolean)
              .join(" · "),
          }),
          el("div", { class: "conn", text: connectionText(m) }),
        ),
        el(
          "div",
          { class: "order" },
          el("button", { type: "button", class: "btn icon", title: "Nach oben", "aria-label": `${m.name} nach oben`, disabled: index === 0, onclick: () => move(index, -1), text: "▲" }),
          el("button", { type: "button", class: "btn icon", title: "Nach unten", "aria-label": `${m.name} nach unten`, disabled: index === machines.length - 1, onclick: () => move(index, 1), text: "▼" }),
        ),
        el(
          "div",
          { class: "actions" },
          el("button", { type: "button", class: "btn", onclick: (e) => testMachine(m, e.currentTarget, probeBox), text: "Verbindung testen" }),
          el("button", { type: "button", class: "btn", onclick: () => openEditor(m), text: "Bearbeiten" }),
          el("button", { type: "button", class: "btn danger", onclick: () => removeMachine(m), text: "Entfernen" }),
        ),
        probeBox,
      );
    }),
  );
}

function renderSettings(s) {
  const item = (label, value) => el("div", {}, el("dt", { text: label }), el("dd", { text: value }));
  $("settings").replaceChildren(
    item("Version", s.version),
    item("Build", s.build ?? "lokal (aus dem Quellcode gestartet)"),
    item("Betriebsart", s.simulate ? "Simulation (Demo-Datenbank)" : "Erfassung der echten Steuerungen"),
    item("Datenordner", s.data_dir),
    item("Datenbank", s.db_path),
    item("Abfrageintervall", `${s.poll_interval_s} s`),
    item("Timeout je Anfrage", `${s.timeout_s} s`),
    item("Zeitzone", s.timezone),
    item("Oberfläche", `${s.listen_host === "0.0.0.0" ? "im Netzwerk erreichbar" : "nur lokal"}, Port ${s.port}`),
  );
  $("settings-note").textContent = `Diese Werte stehen in ${s.config_path}. Nach einer Änderung die App neu starten.`;
}

// --- Felgen-Designs (Ziffern 3–4 der Felgennummer) ----------------------------------------

let designs = [];
let renamingDesign = null; // Nummer des Designs, das gerade umbenannt wird
const designCode = (code) => String(code).padStart(2, "0");

function setDesignError(message) {
  $("rd-error").textContent = message ?? "";
  $("rd-error").hidden = !message;
}

async function loadDesigns() {
  designs = (await api("/api/config/rim-designs")).designs;
  renderDesigns();
}

function designRow(d) {
  const usage = d.rims === 1 ? "1 Felge" : d.rims ? `${d.rims} Felgen` : "noch keine Felge";
  const code = el("span", { class: "rd-code-label num", text: designCode(d.code) });
  if (renamingDesign === d.code) {
    const input = el("input", { type: "text", maxlength: "40", value: d.name, "aria-label": `Design ${designCode(d.code)} umbenennen` });
    queueMicrotask(() => input.focus());
    return el(
      "li",
      {},
      code,
      el(
        "form",
        { class: "mf-rename", onsubmit: (e) => (e.preventDefault(), saveDesign(d.code, input.value)) },
        input,
        el("button", { type: "submit", class: "btn primary", text: "Speichern" }),
        el("button", { type: "button", class: "btn", onclick: () => ((renamingDesign = null), renderDesigns()), text: "Abbrechen" }),
      ),
    );
  }
  return el(
    "li",
    {},
    code,
    el("span", { class: "mf-name", text: d.name }),
    el("span", { class: "muted mf-usage", text: usage }),
    el(
      "span",
      { class: "mf-actions" },
      el("button", { type: "button", class: "btn", onclick: () => ((renamingDesign = d.code), setDesignError(null), renderDesigns()), text: "Umbenennen" }),
      el("button", { type: "button", class: "btn danger", onclick: () => removeDesign(d), text: "Entfernen" }),
    ),
  );
}

function renderDesigns() {
  $("rd-list").replaceChildren(
    ...(designs.length ? designs.map(designRow) : [el("li", { class: "empty", text: "Noch keine Designs – oben Nummer und Name eintragen." })]),
  );
}

async function addDesign(e) {
  e.preventDefault();
  const raw = $("rd-code").value.trim();
  if (!/^\d{1,2}$/.test(raw)) {
    setDesignError("Die Nummer hat ein oder zwei Ziffern, z. B. 15.");
    return;
  }
  const taken = designs.find((d) => d.code === Number(raw));
  if (taken) {
    setDesignError(`Die Nummer ${designCode(taken.code)} ist schon vergeben (${taken.name}). Zum Ändern „Umbenennen“ wählen.`);
    return;
  }
  if (await saveDesign(Number(raw), $("rd-name").value)) {
    $("rd-code").value = "";
    $("rd-name").value = "";
  }
}

async function saveDesign(code, name) {
  try {
    await send("PUT", `/api/config/rim-designs/${code}`, { name });
    renamingDesign = null;
    setDesignError(null);
    await loadDesigns();
    return true;
  } catch (err) {
    setDesignError(err.message);
    return false;
  }
}

async function removeDesign(d) {
  const note = d.rims ? `\n\n${d.rims === 1 ? "Die Felge zeigt" : `Die ${d.rims} Felgen zeigen`} dann „Design ${designCode(d.code)}“ statt des Namens.` : "";
  if (!confirm(`Design ${designCode(d.code)} „${d.name}“ entfernen?${note}`)) return;
  try {
    await send("DELETE", `/api/config/rim-designs/${d.code}`);
    await loadDesigns();
  } catch (err) {
    setDesignError(err.message);
  }
}

// --- Werkzeughersteller ------------------------------------------------------------------

let manufacturers = [];
let renaming = null; // id des Herstellers, der gerade umbenannt wird

function setMfError(message) {
  $("mf-error").textContent = message ?? "";
  $("mf-error").hidden = !message;
}

async function loadManufacturers() {
  manufacturers = (await api("/api/config/manufacturers")).manufacturers;
  renderManufacturers();
}

function manufacturerRow(m) {
  const usage = m.tools === 1 ? "in 1 Werkzeug" : m.tools ? `in ${m.tools} Werkzeugen` : "noch nicht verwendet";
  if (renaming === m.id) {
    const input = el("input", { type: "text", maxlength: "80", value: m.name, "aria-label": `${m.name} umbenennen` });
    const submit = async (e) => {
      e.preventDefault();
      await saveManufacturer(m, input.value);
    };
    queueMicrotask(() => input.focus());
    return el(
      "li",
      {},
      el(
        "form",
        { class: "mf-rename", onsubmit: submit },
        input,
        el("button", { type: "submit", class: "btn primary", text: "Speichern" }),
        el("button", { type: "button", class: "btn", onclick: () => ((renaming = null), renderManufacturers()), text: "Abbrechen" }),
      ),
    );
  }
  return el(
    "li",
    {},
    el("span", { class: "mf-name", text: m.name }),
    el("span", { class: "muted mf-usage", text: usage }),
    el(
      "span",
      { class: "mf-actions" },
      el("button", { type: "button", class: "btn", onclick: () => ((renaming = m.id), setMfError(null), renderManufacturers()), text: "Umbenennen" }),
      el("button", { type: "button", class: "btn danger", onclick: () => removeManufacturer(m), text: "Entfernen" }),
    ),
  );
}

function renderManufacturers() {
  $("mf-list").replaceChildren(
    ...(manufacturers.length
      ? manufacturers.map(manufacturerRow)
      : [el("li", { class: "empty", text: "Noch keine Hersteller – oben eintragen und „Hinzufügen“." })]),
  );
}

async function addManufacturer(e) {
  e.preventDefault();
  try {
    await send("POST", "/api/config/manufacturers", { name: $("mf-name").value });
    $("mf-name").value = "";
    setMfError(null);
    await loadManufacturers();
  } catch (err) {
    setMfError(err.message);
  }
}

async function saveManufacturer(m, name) {
  try {
    await send("PUT", `/api/config/manufacturers/${m.id}`, { name });
    renaming = null;
    setMfError(null);
    await loadManufacturers();
  } catch (err) {
    setMfError(err.message);
  }
}

async function removeManufacturer(m) {
  const note = m.tools
    ? `\n\n${m.tools === 1 ? "1 Werkzeug behält" : `${m.tools} Werkzeuge behalten`} den Eintrag „${m.name}“; er steht dann nur nicht mehr zur Auswahl.`
    : "";
  if (!confirm(`„${m.name}“ aus der Herstellerliste entfernen?${note}`)) return;
  try {
    await send("DELETE", `/api/config/manufacturers/${m.id}`);
    await loadManufacturers();
  } catch (err) {
    setMfError(err.message);
  }
}

// --- Versionen ----------------------------------------------------------------------

const isoToDe = (iso) => iso?.split("-").reverse().join(".");

async function loadChangelog() {
  const data = await api("/api/version");
  if (!data.changelog.length) {
    $("changelog").replaceChildren(el("p", { class: "empty", text: "Kein Änderungsprotokoll gefunden." }));
    return;
  }
  $("changelog").replaceChildren(
    ...data.changelog.map((release, index) =>
      el(
        "details",
        { class: "release", open: index === 0 },
        el(
          "summary",
          {},
          el("span", { class: "num", text: release.version }),
          release.date && el("span", { class: "release-date", text: isoToDe(release.date) }),
          release.version === data.version && el("span", { class: "release-badge", text: "installiert" }),
        ),
        release.sections.map((section) =>
          el(
            "div",
            { class: "release-section" },
            section.title && el("h3", { text: section.title }),
            el("ul", {}, section.items.map((text) => el("li", { text }))),
          ),
        ),
      ),
    ),
  );
}

async function move(index, delta) {
  const ids = machines.map((m) => m.id);
  [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
  try {
    await send("POST", "/api/config/order", { ids });
    await reload();
  } catch (err) {
    showError(err);
  }
}

async function removeMachine(m) {
  const ok = confirm(
    `„${m.name}“ entfernen?\n\nDie Maschine wird ab sofort nicht mehr erfasst. ` +
      "Bereits erfasste Daten bleiben in der Datenbank erhalten.",
  );
  if (!ok) return;
  try {
    await send("DELETE", `/api/config/machines/${encodeURIComponent(m.id)}`);
    probes.delete(m.id);
    await reload();
  } catch (err) {
    showError(err);
  }
}

// --- Verbindungstest -------------------------------------------------------------

function probeView(result) {
  return el(
    "ul",
    { class: "probe-steps" },
    result.steps.map((step) =>
      el(
        "li",
        { class: step.ok ? "ok" : step.required ? "fail" : "warn" },
        el("span", { class: "mark", "aria-hidden": "true", text: step.ok ? "✓" : step.required ? "✗" : "!" }),
        el(
          "div",
          {},
          el("strong", { text: `${step.title}: ` }),
          el("span", { class: "detail", text: step.detail }),
          step.hints.length ? el("ul", { class: "hints" }, step.hints.map((h) => el("li", { text: h }))) : null,
        ),
      ),
    ),
    result.ok ? el("li", { class: "ok" }, el("span", { class: "mark", text: "✓" }), el("strong", { text: "Die Steuerung kann erfasst werden." })) : null,
  );
}

async function runProbe(host, port, checkHost, button, target) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Teste …";
  target.replaceChildren(el("p", { class: "muted", style: { margin: 0 }, text: `Verbinde mit ${host}:${port} … (bis zu einigen Sekunden)` }));
  try {
    const result = await send("POST", "/api/config/test", { host, port, check_host: checkHost });
    const view = probeView(result);
    target.replaceChildren(view);
    return view;
  } catch (err) {
    target.replaceChildren(el("div", { class: "form-error", text: err.message }));
    return null;
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

let busy = 0; // laufende Tests: solange nicht neu zeichnen, sonst landet das Ergebnis im Nichts

async function testMachine(m, button, target) {
  busy += 1;
  try {
    const view = await runProbe(m.host, m.port, m.check_host, button, target);
    if (view) probes.set(m.id, view);
  } finally {
    busy -= 1;
  }
}

// --- Dialog ----------------------------------------------------------------------

const editor = {
  id: null, // null = neue Maschine
  image: null, // neues Bild (Blob), noch nicht hochgeladen
  removeImage: false,
  current: null,
};

function setError(message) {
  $("f-error").textContent = message ?? "";
  $("f-error").hidden = !message;
}

function renderPreview() {
  const name = $("f-name").value || "?";
  let node;
  if (editor.image) {
    node = el("img", { class: "thumb large", src: URL.createObjectURL(editor.image), alt: "Vorschau" });
  } else {
    const url = editor.removeImage ? null : editor.current?.image_url;
    node = machineThumb({ name, image_url: url }, "large");
  }
  $("img-preview").replaceChildren(node);
  $("img-remove").hidden = !(editor.image || (editor.current?.image_url && !editor.removeImage));
}

function openEditor(m = null) {
  Object.assign(editor, { id: m?.id ?? null, image: null, removeImage: false, current: m });
  $("editor-title").textContent = m ? `${m.name} bearbeiten` : "Maschine hinzufügen";
  $("f-name").value = m?.name ?? "";
  $("f-host").value = m?.host ?? "";
  $("f-port").value = m?.port ?? 19000;
  $("f-note").value = m?.note ?? "";
  $("f-check").value = m?.check_host ?? "";
  $("f-slots").value = m?.tool_slots ?? "";
  $("img-input").value = "";
  $("f-probe").replaceChildren();
  setError(null);
  renderPreview();
  $("editor").showModal();
  $("f-name").focus();
}

async function save(event) {
  event.preventDefault();
  setError(null);
  const payload = {
    name: $("f-name").value.trim(),
    host: $("f-host").value.trim(),
    port: Number($("f-port").value) || 19000,
    note: $("f-note").value.trim(),
    check_host: $("f-check").value.trim(),
    tool_slots: $("f-slots").value.trim() || null,
  };
  const button = $("f-save");
  button.disabled = true;
  try {
    let m = editor.id
      ? await send("PUT", `/api/config/machines/${encodeURIComponent(editor.id)}`, payload)
      : await send("POST", "/api/config/machines", payload);
    // Ab hier existiert die Maschine; scheitert das Bild, bleibt der Dialog im Bearbeiten-Modus
    Object.assign(editor, { id: m.id, current: m });
    $("editor-title").textContent = `${m.name} bearbeiten`;
    if (editor.image) {
      m = await send("PUT", `/api/config/machines/${encodeURIComponent(m.id)}/image`, editor.image);
    } else if (editor.removeImage) {
      m = await send("DELETE", `/api/config/machines/${encodeURIComponent(m.id)}/image`);
    }
    $("editor").close();
    await reload();
  } catch (err) {
    setError(err.message);
  } finally {
    button.disabled = false;
  }
}

// --- Untertabs ------------------------------------------------------------------

const TABS = ["maschinen", "werkzeuge", "artikel", "system", "versionen"];
// Ältere Links (#hersteller, #felgen …) führen zum passenden Reiter und Abschnitt
const ANCHORS = { hersteller: "werkzeuge", felgen: "artikel", allgemein: "system", diagnose: "system", fehler: "maschinen" };

function tabFromHash() {
  const id = location.hash.slice(1);
  if (TABS.includes(id)) return { tab: id, anchor: null };
  if (ANCHORS[id]) return { tab: ANCHORS[id], anchor: id };
  return { tab: "maschinen", anchor: null };
}

function showTab(tab, { anchor = null, push = false } = {}) {
  for (const button of document.querySelectorAll(".subtabs [role=tab]")) {
    const active = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(".tab-panel")) panel.hidden = panel.dataset.tab !== tab;
  if (push) history.replaceState(null, "", `#${tab}`);
  if (anchor) $(anchor)?.scrollIntoView();
  if (tab === "maschinen") loadErrors();
}

function setupTabs() {
  const buttons = [...document.querySelectorAll(".subtabs [role=tab]")];
  for (const button of buttons) {
    button.addEventListener("click", () => showTab(button.dataset.tab, { push: true }));
    // Pfeiltasten wechseln den Reiter (Tablist-Muster)
    button.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      const next = buttons[(buttons.indexOf(button) + step + buttons.length) % buttons.length];
      next.focus();
      showTab(next.dataset.tab, { push: true });
    });
  }
  window.addEventListener("hashchange", () => {
    const { tab, anchor } = tabFromHash();
    showTab(tab, { anchor });
  });
}

// --- Artikel: Aufträge automatisch abschließen ------------------------------------

async function loadCloseDays() {
  const data = await api("/api/config/orders");
  $("close-days").value = data.close_days;
}

async function saveCloseDays(e) {
  e.preventDefault();
  const msg = $("close-msg");
  try {
    const data = await send("PUT", "/api/config/orders", { close_days: $("close-days").value });
    $("close-days").value = data.close_days;
    const closed = data.closed.length ? ` Gerade abgeschlossen: ${data.closed.join(", ")}.` : "";
    msg.textContent = (data.close_days
      ? `Gespeichert: abgeschlossen nach ${data.close_days} ${data.close_days === 1 ? "Tag" : "Tagen"} ohne Programmlauf.`
      : "Gespeichert: Aufträge werden nicht mehr automatisch abgeschlossen.") + closed;
    msg.classList.remove("error");
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  }
  msg.hidden = false;
}

// --- Fehlersammler ----------------------------------------------------------------

const errState = { machine: null, days: 30, search: "", data: null };
const ERR_SHOW = 500;

function renderErrorMachines() {
  const box = $("err-machine");
  if (!machines.length) {
    box.replaceChildren();
    return;
  }
  if (!machines.some((m) => m.id === errState.machine)) errState.machine = machines[0].id;
  box.replaceChildren(
    ...machines.map((m) =>
      el("button", {
        type: "button",
        "aria-pressed": String(m.id === errState.machine),
        onclick: () => {
          errState.machine = m.id;
          renderErrorMachines();
          loadErrors();
        },
        text: m.name,
      }),
    ),
  );
}

async function loadErrors() {
  if (!errState.machine) return;
  try {
    errState.data = await api(`/api/machines/${encodeURIComponent(errState.machine)}/errors?days=${errState.days}`);
  } catch (err) {
    errState.data = null;
    $("err-list").replaceChildren(el("p", { class: "empty", text: err.message }));
    return;
  }
  renderErrors();
}

function renderErrors() {
  const data = errState.data;
  if (!data) return;
  const needle = errState.search.trim().toLowerCase();
  const match = (e) => !needle || `${e.text} ${e.program ?? ""}`.toLowerCase().includes(needle);
  const rows = data.errors.filter(match);
  // Häufigste Meldungen: Klick filtert die Liste
  $("err-top").replaceChildren(
    ...data.top.slice(0, 8).map((t) =>
      el(
        "button",
        {
          type: "button",
          class: "err-chip",
          "aria-pressed": String(needle === t.text.toLowerCase()),
          title: `zuletzt ${fmtDateTime(t.last)}`,
          onclick: () => {
            errState.search = needle === t.text.toLowerCase() ? "" : t.text;
            $("err-search").value = errState.search;
            renderErrors();
          },
        },
        el("span", { class: "err-chip-text", text: t.text }),
        el("span", { class: "err-chip-count num", text: `${t.count}×` }),
      ),
    ),
  );
  if (!rows.length) {
    $("err-list").replaceChildren(
      el("p", { class: "empty", text: data.errors.length ? "Keine Meldung passt zur Suche." : `Keine Meldungen in den letzten ${data.days} Tagen.` }),
    );
    return;
  }
  const shown = rows.slice(0, ERR_SHOW);
  $("err-list").replaceChildren(
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "err-table" },
        el("thead", {}, el("tr", {}, ["Datum und Uhrzeit", "Meldung", "Programm"].map((h) => el("th", { text: h })))),
        el(
          "tbody",
          {},
          shown.map((e) =>
            el(
              "tr",
              {},
              el("td", { class: "num nowrap", text: fmtDateTime(e.ts) }),
              el("td", { class: "wrap", text: e.text }),
              el("td", { class: "wrap muted", title: e.program ?? null, text: e.program ? baseName(e.program) : "—" }),
            ),
          ),
        ),
      ),
    ),
    el("p", {
      class: "muted err-count",
      text: `${rows.length} ${rows.length === 1 ? "Meldung" : "Meldungen"}${rows.length > ERR_SHOW ? `, die neuesten ${ERR_SHOW} angezeigt` : ""}`,
    }),
  );
}

function setupErrors() {
  for (const button of $("err-days").querySelectorAll("button")) {
    button.addEventListener("click", () => {
      errState.days = Number(button.dataset.days);
      for (const b of $("err-days").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === button));
      loadErrors();
    });
  }
  $("err-search").addEventListener("input", (e) => {
    errState.search = e.target.value;
    renderErrors();
  });
}

// --- Start -----------------------------------------------------------------------

async function main() {
  await loadMeta();
  $("add").addEventListener("click", () => openEditor());
  $("editor-form").addEventListener("submit", save);
  $("f-cancel").addEventListener("click", () => $("editor").close());
  $("f-name").addEventListener("input", renderPreview);
  $("f-test").addEventListener("click", (e) => {
    const host = $("f-host").value.trim();
    if (!host) return setError("Bitte zuerst die IP-Adresse eintragen.");
    setError(null);
    runProbe(host, Number($("f-port").value) || 19000, $("f-check").value.trim(), e.currentTarget, $("f-probe"));
  });
  $("img-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      // Große Fotos im Browser verkleinern – spart Speicher und Ladezeit
      [editor.image] = await shrinkImage(file, [MACHINE_IMAGE]);
      editor.removeImage = false;
      setError(null);
    } catch (err) {
      setError(err.message);
    }
    renderPreview();
  });
  $("img-remove").addEventListener("click", () => {
    editor.image = null;
    editor.removeImage = true;
    $("img-input").value = "";
    renderPreview();
  });

  $("mf-form").addEventListener("submit", addManufacturer);
  $("rd-form").addEventListener("submit", addDesign);
  $("close-form").addEventListener("submit", saveCloseDays);
  setupTabs();
  setupErrors();
  const { tab, anchor } = tabFromHash();
  showTab(tab);
  await Promise.all([reload(), loadChangelog(), loadManufacturers(), loadDesigns(), loadCloseDays()]);
  renderErrorMachines();
  // Erst nach dem Laden springen, die Liste darüber wächst noch
  showTab(tab, { anchor });
  setInterval(refreshStatus, STATUS_MS);
}

main().catch(showError);
