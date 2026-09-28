// Konfigurations-Tab: Maschinen anlegen, bearbeiten, sortieren, entfernen, Verbindung testen.

import { api, el, loadMeta, machineThumb, send, stateBadge } from "./common.js";

const STATUS_MS = 5000;
const IMAGE_MAX_PX = 1024;
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
          el("div", { class: "sub", text: [`${m.host}:${m.port}`, m.note, m.check_host && `Prüfadresse ${m.check_host}`].filter(Boolean).join(" · ") }),
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
  $("img-input").value = "";
  $("f-probe").replaceChildren();
  setError(null);
  renderPreview();
  $("editor").showModal();
  $("f-name").focus();
}

/** Große Fotos im Browser verkleinern (max. 1024 px, JPEG) – spart Speicher und Ladezeit. */
async function shrinkImage(file) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("Dieses Bildformat wird nicht unterstützt. Bitte JPG, PNG oder WebP verwenden.");
  }
  const scale = Math.min(1, IMAGE_MAX_PX / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff"; // transparente PNGs auf weißem Grund
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Bild konnte nicht verarbeitet werden."))), "image/jpeg", 0.85),
  );
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
      editor.image = await shrinkImage(file);
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
  await Promise.all([reload(), loadChangelog(), loadManufacturers()]);
  // Link „v1.2.0“ aus der Kopfzeile: erst nach dem Laden springen, die Liste darüber wächst noch
  if (["#versionen", "#hersteller"].includes(location.hash)) $(location.hash.slice(1)).scrollIntoView();
  setInterval(refreshStatus, STATUS_MS);
}

main().catch(showError);
