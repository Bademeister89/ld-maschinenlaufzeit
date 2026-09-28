// Live-Seite: Karten je Maschine (alle 2 s) und Tagesverlauf (alle 30 s).

import {
  addDays,
  api,
  baseName,
  dayAxis,
  el,
  epoch,
  execLabel,
  fmtDuration,
  fmtHours,
  fmtInt,
  fmtPct,
  fmtTime,
  fmtToolTime,
  legend,
  loadMeta,
  machineThumb,
  pgmStateLabel,
  startOfDay,
  stateBadge,
  stateLabel,
  timelineTrack,
  tooltip,
} from "./common.js";

const LIVE_MS = 2000;
const TODAY_MS = 30000;
const RUN_STATES = new Set(["RUNNING", "STOPPED", "ERROR"]);

const grid = document.getElementById("machines");
const banner = document.getElementById("banner");
const clock = document.getElementById("clock");
const cards = new Map(); // id → { root, live, today }

function card(id) {
  if (!cards.has(id)) {
    const live = el("div", { class: "live-part" });
    const today = el("div", { class: "today-part" });
    const root = el("article", { class: "card machine-card" }, live, today);
    tooltip().bind(today);
    cards.set(id, { root, live, today });
  }
  return cards.get(id);
}

/** Karten an die konfigurierten Maschinen anpassen (neu, entfernt, Reihenfolge). */
function syncCards(machines) {
  const ids = new Set(machines.map((m) => m.id));
  for (const [id, c] of cards) {
    if (!ids.has(id)) {
      c.root.remove();
      cards.delete(id);
    }
  }
  if (!machines.length) {
    grid.replaceChildren(
      el("p", { class: "empty" }, "Noch keine Maschinen eingerichtet – bitte im Tab ", el("a", { href: "konfiguration.html", text: "Konfiguration" }), " anlegen."),
    );
    return;
  }
  grid.querySelector(".empty")?.remove();
  const isNew = machines.some((m) => !cards.has(m.id));
  // append() verschiebt vorhandene Knoten, dadurch stimmt die Reihenfolge ohne Neuaufbau
  for (const m of machines) grid.append(card(m.id).root);
  if (isNew) refreshToday();
}

/** Bild-Element wiederverwenden, damit es beim 2-s-Neuzeichnen nicht flackert. */
function thumbFor(c, m) {
  const key = `${m.image_url}|${m.name}`;
  if (c.thumbKey !== key) {
    c.thumb = machineThumb(m);
    c.thumbKey = key;
  }
  return c.thumb;
}

const METHOD_TEXT = {
  profile: (f) => `Prognose aus dem Satzverlauf von ${f.basis_runs} früheren ${f.basis_runs === 1 ? "Lauf" : "Läufen"}`,
  history: (f) => `Prognose aus der Laufzeit von ${f.basis_runs} früheren ${f.basis_runs === 1 ? "Lauf" : "Läufen"}`,
  blocks: () => "Grobe Schätzung aus der Satznummer – noch kein vollständiger Lauf dieses Programms erfasst",
};

function blocksText(b) {
  if (!b) return null;
  return b.total ? `Satz ${fmtInt(b.line)} / ${fmtInt(b.total)}` : `Satz ${fmtInt(b.line)}`;
}

/** Fortschritt und Restlaufzeit des laufenden Programms (nur solange ein Lauf aktiv ist). */
function progressBlock(m) {
  if (!m.run || !RUN_STATES.has(m.state)) return null;
  const f = m.forecast;
  const b = m.blocks;
  const progress = f?.progress ?? (b?.total ? Math.min(b.line / b.total, 1) : null);
  if (progress == null && !b) return null;

  let headline = "Restlaufzeit noch unbekannt";
  if (f?.overdue_s) headline = `Läuft länger als üblich (+${fmtDuration(f.overdue_s)})`;
  else if (f) headline = `Rest ca. ${fmtDuration(f.remaining_s)} · fertig ca. ${fmtTime(f.eta)} Uhr`;
  if (f && m.state !== "RUNNING") headline += " (pausiert)";

  const pct = progress != null ? Math.round(progress * 100) : null;
  const notes = [];
  if (f) notes.push(METHOD_TEXT[f.method](f) + (f.typical_run_s ? ` · üblich ${fmtDuration(f.typical_run_s)} Laufzeit` : ""));
  else notes.push("Für eine Prognose fehlen frühere Läufe oder die Satzanzahl des Programms.");
  if (b?.error) notes.push(`Satzanzahl: ${b.error}`);

  return el(
    "div",
    { class: `progress${f?.overdue_s ? " overdue" : ""}` },
    el("div", { class: "progress-row" }, el("span", { class: "progress-head", text: headline }), el("span", { class: "num secondary", text: blocksText(b) ?? "" })),
    pct != null
      ? el(
          "div",
          { class: "progress-row meter-row" },
          el(
            "div",
            { class: "meter", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": "Fortschritt des Programms" },
            el("div", { class: "meter-fill", style: { width: `${pct}%` } }),
          ),
          el("span", { class: "num meter-value", text: `${pct} %` }),
        )
      : null,
    el("div", { class: "progress-note", text: notes.join(" · ") }),
  );
}

function fact(label, value) {
  return el("div", {}, el("dt", { text: label }), el("dd", { text: value ?? "—" }));
}

/** Werkzeug mit Einsatzzeit (und Limit, falls eingetragen). */
function toolFact(m) {
  const info = m.tool_info;
  const life = info
    ? `${fmtToolTime(info.used_s)}${info.limit_s ? ` von ${fmtToolTime(info.limit_s)}` : ""} im Einsatz`
    : null;
  // Die Steuerung meldet nur "T12"; der Name kommt aus der Werkzeugtabelle (TOOL.T)
  const label = info ? `T${info.number}${info.name ? ` ${info.name}` : ""}` : m.tool;
  return el(
    "div",
    {},
    el("dt", { text: "Werkzeug" }),
    el("dd", {}, label ?? "—", life ? el("span", { class: "tool-life num", text: life }) : null),
  );
}

/** Warnung, wenn das Werkzeug in der Spindel über (oder kurz vor) der Maximallaufzeit ist. */
function toolWarning(m) {
  const info = m.tool_info;
  if (!info || (info.status !== "over" && info.status !== "warn")) return null;
  const link = el("a", { href: `werkzeuge.html#tool-${m.id}-${info.number}`, text: "Werkzeugauswertung" });
  const values = `${fmtToolTime(info.used_s)} von ${fmtToolTime(info.limit_s)}`;
  return info.status === "over"
    ? el("p", { class: "tool-warning over", role: "alert" }, `✕ Achtung: T${info.number} ist über der Maximallaufzeit (${values}) · `, link)
    : el("p", { class: "tool-warning warn" }, `⚠ T${info.number} bei ${Math.round(info.ratio * 100)} % der Maximallaufzeit (${values}) · `, link);
}

function overrideText(o) {
  const p = (v) => (v == null ? "—" : `${Math.round(v)} %`);
  return `F ${p(o.feed)} · S ${p(o.spindle)} · FMAX ${p(o.rapid)}`;
}

function hero(m, now) {
  const run = m.run;
  if (run && RUN_STATES.has(m.state)) {
    const prefix = run.start_observed ? "" : "≥ ";
    let label = `Programm läuft seit ${fmtTime(run.started_at)} Uhr`;
    if (m.state !== "RUNNING" && m.state_since) {
      label += ` · ${stateLabel(m.state).toLowerCase()} seit ${fmtDuration(now - m.state_since)}`;
    }
    if (!run.start_observed) label += " (Start vor Beginn der Erfassung)";
    return el(
      "div",
      { class: "hero" },
      el("span", { class: "hero-value", text: prefix + fmtDuration(now - run.started_at) }),
      el("span", { class: "hero-label", text: label }),
    );
  }
  if (!m.state) return el("div", { class: "hero" }, el("span", { class: "hero-label", text: "Verbindung wird aufgebaut …" }));
  return el(
    "div",
    { class: "hero" },
    el("span", { class: "hero-value", text: fmtDuration(now - m.state_since) }),
    el("span", { class: "hero-label", text: `${stateLabel(m.state)} seit ${fmtTime(m.state_since)} Uhr` }),
  );
}

function renderLive(m, now) {
  const c = card(m.id);
  c.root.className = `card machine-card st-${m.state ?? "UNKNOWN"}`;

  const control = [m.control?.control, m.control?.nc_sw].filter(Boolean).join(" · ");
  const parts = [
    el(
      "div",
      { class: "machine-head" },
      thumbFor(c, m),
      el(
        "div",
        { class: "title" },
        el("div", { class: "machine-name", text: m.name }),
        m.note ? el("div", { class: "machine-host", text: m.note }) : null,
        el("div", { class: "machine-host", text: [`${m.host}:${m.port}`, control].filter(Boolean).join(" · ") }),
      ),
      stateBadge(m.state),
    ),
    hero(m, now),
    progressBlock(m),
  ];

  if (m.state === "NETWORK") {
    parts.push(
      el(
        "p",
        { class: "secondary", style: { margin: 0 } },
        `Weder die Steuerung noch die Prüfadresse ${m.check_host} antworten – die Verbindung zum Standort ist gestört (VPN?). `
          + "Diese Zeit wird als „Keine Daten“ gebucht, nicht als „Offline“.",
      ),
    );
  } else if (m.state === "OFFLINE" || !m.connected) {
    parts.push(
      el("p", { class: "secondary", style: { margin: 0 } }, m.connection_error ? `Keine Verbindung: ${m.connection_error}` : "Keine Verbindung zur Steuerung."),
    );
  } else {
    const programName = baseName(m.program);
    const current = m.current_program && m.current_program !== m.program ? `aktuell: ${baseName(m.current_program)}` : null;
    parts.push(
      el(
        "div",
        { class: "program" },
        el("div", { class: "program-name", text: programName ?? "Kein Programm angewählt" }),
        m.program ? el("div", { class: "program-path", text: [m.program, current].filter(Boolean).join(" · ") }) : null,
        m.order
          ? el(
              "a",
              { class: "order-link", href: `auftraege.html?order=${encodeURIComponent(m.order.key)}` },
              `Auftrag ${m.order.order} (${m.order.year}) · Aufspannung ${m.order.setup} · Programm ${String(m.order.program).padStart(2, "0")}`,
            )
          : null,
      ),
      el(
        "dl",
        { class: "facts" },
        fact("Betriebsart", execLabel(m.exec_mode)),
        fact("Programmstatus", pgmStateLabel(m.pgm_state)),
        fact("Satz", m.blocks ? blocksText(m.blocks).replace("Satz ", "") : null),
        toolFact(m),
        fact("Override", overrideText(m.override)),
        fact("Lauf-Nr.", m.run ? String(m.run.id) : null),
      ),
    );
    const warning = toolWarning(m);
    if (warning) parts.push(warning);
    if (m.errors.length) {
      parts.push(el("ul", { class: "errors" }, m.errors.map((text) => el("li", { text }))));
    }
  }
  c.live.replaceChildren(...parts);
}

async function refreshLive() {
  try {
    const data = await api("/api/machines");
    syncCards(data.machines);
    for (const m of data.machines) renderLive(m, data.now);
    clock.textContent = `Stand ${fmtTime(data.now, true)} Uhr`;
    banner.hidden = true;
    grid.classList.remove("stale");
  } catch (err) {
    banner.textContent = `Server nicht erreichbar (${err.message}) – Anzeige ist nicht aktuell.`;
    banner.hidden = false;
    grid.classList.add("stale");
  }
}

async function refreshToday() {
  const dayStart = startOfDay();
  const t0 = epoch(dayStart);
  const t1 = epoch(addDays(dayStart, 1));
  const now = Date.now() / 1000;
  try {
    const stats = await api("/api/stats", { from: t0 });
    await Promise.all(
      [...cards.keys()].map(async (id) => {
        const tl = await api(`/api/machines/${encodeURIComponent(id)}/timeline`, { from: t0 });
        const s = stats.machines[id];
        const finished = stats.programs.filter((p) => p.machine_id === id).reduce((sum, p) => sum + p.finished, 0);
        cards.get(id).today.replaceChildren(
          el(
            "div",
            { class: "today-kpis", style: { marginBottom: "10px" } },
            kpi("Laufzeit heute", fmtHours(s.totals.RUNNING)),
            kpi("Auslastung heute", fmtPct(s.utilization), "bezogen auf Einschaltzeit"),
            kpi("Fertige Läufe heute", String(finished)),
          ),
          timelineTrack(tl.intervals, t0, t1, now),
          dayAxis(dayStart),
        );
      }),
    );
  } catch (err) {
    console.warn("Tagesverlauf konnte nicht geladen werden", err);
  }
}

function kpi(label, value, title) {
  return el("div", { title }, el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: value }));
}

async function main() {
  await loadMeta();
  document.getElementById("legend").append(legend());
  await refreshLive();
  await refreshToday();
  setInterval(refreshLive, LIVE_MS);
  setInterval(refreshToday, TODAY_MS);
}

main().catch((err) => {
  banner.textContent = `Start fehlgeschlagen: ${err.message}`;
  banner.hidden = false;
});
