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
  fmtEta,
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
  stateIcon,
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
    c.thumb = machineThumb(m, "xl");
    c.thumbKey = key;
  }
  return c.thumb;
}

/** Vorschaubild des Bauteils zum Auftrag (falls hinterlegt); ebenfalls wiederverwendet. */
function orderThumbFor(c, m) {
  const url = m.order?.thumb_url;
  if (!url) return null;
  if (c.orderThumbUrl !== url) {
    // Der Link daneben führt schon zum Auftrag: Bild nur für die Maus, nicht doppelt für Tastatur/Screenreader
    c.orderThumb = el(
      "a",
      { class: "program-image", href: `auftraege.html?order=${encodeURIComponent(m.order.key)}`, tabindex: "-1", "aria-hidden": "true", title: `Bauteil zu Auftrag ${m.order.order}` },
      el("img", { src: url, alt: "", width: 64, height: 64, decoding: "async" }),
    );
    c.orderThumbUrl = url;
  }
  return c.orderThumb;
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
  else if (f) headline = `Rest ca. ${fmtDuration(f.remaining_s)} · fertig ca. ${fmtEta(f.eta)}`;
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
  const values = `${fmtToolTime(info.used_s)}${info.limit_s ? ` von ${fmtToolTime(info.limit_s)}` : ""}`;
  return info.status === "over"
    ? el("p", { class: "tool-warning over", role: "alert" }, `✕ Achtung: T${info.number} ist über der Maximallaufzeit (${values}) · `, link)
    : el("p", { class: "tool-warning warn" }, `⚠ T${info.number}: Vorwarnzeit erreicht (${values}) · `, link);
}

const pctText = (v) => (v == null ? "—" : `${Math.round(v)} %`);
const OVR_MAX = 150; // Potis der iTNC 530 gehen bis 150 %
/** Farbzone eines Werts: [CSS-Klasse, Text]. Rot unter 50 %, Gelb unter 100 %, Grün 100–120 %, Orange darüber. */
function overrideZone(v) {
  if (v < 50) return ["low", "unter 50 %"];
  if (v < 100) return ["reduced", "unter 100 %"];
  if (v <= 120) return ["normal", "100–120 %"];
  return ["high", "über 120 %"];
}

/** Ein Poti als Balken 0–150 % mit Markierung bei 100 %. Die Zahl steht immer daneben – nie nur Farbe. */
function overrideBar(label, value) {
  const width = value == null ? 0 : Math.max(0, Math.min(100, (value / OVR_MAX) * 100));
  const zone = value == null ? null : overrideZone(value);
  return el(
    "div",
    { class: `ovr${zone ? ` ovr-${zone[0]}` : ""}` },
    el("div", { class: "ovr-head" }, el("span", { class: "ovr-label", text: label }), el("span", { class: "ovr-value num", text: pctText(value) })),
    el(
      "div",
      { class: "ovr-track" },
      el(
        "div",
        {
          class: "ovr-meter",
          role: "meter",
          "aria-label": `${label}-Override`,
          "aria-valuemin": "0",
          "aria-valuemax": String(OVR_MAX),
          "aria-valuenow": value == null ? null : String(Math.round(value)),
          "aria-valuetext": value == null ? `${label}: keine Angabe` : `${label} ${pctText(value)} (${zone[1]})`,
        },
        el("div", { class: "ovr-fill", style: { width: `${width}%` } }),
      ),
      el("div", { class: "ovr-mark", title: "100 %", "aria-hidden": "true" }),
    ),
  );
}

/** Poti-Stellung Vorschub, Spindel und Eilgang (unter dem Vorschub); liefert die Steuerung nichts, entfällt der Block. */
function overrideBlock(o) {
  if (o.feed == null && o.spindle == null && o.rapid == null) return null;
  return el("div", { class: "overrides" }, overrideBar("Vorschub", o.feed), overrideBar("Spindel", o.spindle), overrideBar("Eilgang", o.rapid));
}

// --- Ablaufliste des Palettenprogramms (.P) ------------------------------------------------------

const palletLabel = (name) => (name == null ? "" : /^\d+$/.test(name) ? `Palette ${name}` : name);
const programLabel = (path) => baseName(path).replace(/\.[^.]+$/, "");
const PAL_MARK = { done: "✓", current: "▶", pending: "", skipped: "–" };
const PAL_STATUS = { done: "fertig", current: "läuft", pending: "offen", skipped: "gesperrt" };

/** Aufeinanderfolgende Zeilen derselben Palette zusammenfassen (eine Zeile je Palette). */
function palletGroups(entries) {
  const groups = [];
  for (const e of entries) {
    const last = groups.at(-1);
    if (last && e.pallet != null && last.pallet === e.pallet) last.entries.push(e);
    else groups.push({ pallet: e.pallet, entries: [e] });
  }
  for (const g of groups) {
    const states = g.entries.map((e) => e.status).filter((s) => s !== "skipped");
    g.status = !states.length ? "skipped" : states.includes("current") ? "current" : states.every((s) => s === "done") ? "done" : "pending";
  }
  return groups;
}

/** Fehlt die Zeit eines Auftragsprogramms, ist eine Summe nur eine Mindestzeit. Hilfsprogramme wie
 *  Drehen oder P-Ende dauern Sekunden bis wenige Minuten – ohne ihre Zeit bleibt es „ca.“. */
const lacksOrderTime = (entries) => entries.some((e) => e.expected_s == null && e.order);

/** Summe der erwarteten Zeiten. */
function expectedText(entries) {
  const active = entries.filter((e) => e.status !== "skipped");
  const known = active.filter((e) => e.expected_s != null);
  if (!known.length) return active.length ? "Zeit unbekannt" : "";
  const sum = known.reduce((total, e) => total + e.expected_s, 0);
  return `${lacksOrderTime(active) ? "mind. " : "ca. "}${fmtDuration(sum)}`;
}

/** Laufende Palette: Gruppe, Beginn (erste Zeile mit bekanntem Beginn) und Stelle unter den freien Paletten. */
function currentPallet(groups) {
  const active = groups.filter((g) => g.status !== "skipped");
  const index = active.findIndex((g) => g.status === "current");
  if (index < 0) return null;
  const group = active[index];
  const first = group.entries.find((e) => e.started_at != null);
  return { group, number: index + 1, count: active.length, since: first?.started_at ?? null, observed: first?.started_observed ?? false };
}

const programsOf = (entries) => [...new Set(entries.map((e) => programLabel(e.program)))].join(", ");

/** Marker im Programmfeld: welche Palette gerade bearbeitet wird und seit wann. */
function palletMarker(m, now) {
  const cur = m.pallet ? currentPallet(palletGroups(m.pallet.entries)) : null;
  if (!cur) return null;
  const name = palletLabel(cur.group.pallet) || programsOf(cur.group.entries.filter((e) => e.status === "current"));
  const since = cur.since != null ? ` · seit ${fmtTime(cur.since)} Uhr (${cur.observed ? "" : "≥ "}${fmtDuration(now - cur.since)})` : "";
  return el(
    "div",
    { class: "pallet-marker" },
    el("span", { class: "pallet-marker-icon", "aria-hidden": "true", text: "▶" }),
    el("span", {}, el("strong", { text: name }), `${since} · ${cur.number} von ${cur.count}`),
  );
}

/** Welche Programme das Palettenprogramm nacheinander abarbeitet, mit erwarteter Zeit und Restzeit. */
function palletBlock(m) {
  const p = m.pallet;
  if (!p) return null;
  const groups = palletGroups(p.entries);
  const cur = currentPallet(groups);
  let summary = null;
  if (p.remaining_s != null) {
    // Nur ein offenes Auftragsprogramm ohne Zeit (auch das laufende ohne Prognose) macht daraus eine Mindestzeit
    const minimum = lacksOrderTime(p.entries.filter((e) => e.status === "current" || e.status === "pending"));
    summary = `noch ${minimum ? "mind. " : "ca. "}${fmtDuration(p.remaining_s)}`;
    if (m.state !== "RUNNING") summary += " (pausiert)";
    else summary += ` · fertig ${minimum ? "frühestens" : "ca."} ${fmtEta(p.eta)}`;
  }
  const active = p.entries.filter((e) => e.status !== "skipped");
  const pallets = groups.filter((g) => g.pallet != null && g.status !== "skipped").length;
  const missing = programsOf(active.filter((e) => e.expected_s == null));
  const estimated = programsOf(active.filter((e) => e.source === "forecast"));
  const sources = new Set(active.map((e) => e.source));
  const foot = [
    `Gesamt ${expectedText(p.entries)}${pallets ? ` für ${pallets} ${pallets === 1 ? "Palette" : "Paletten"}` : ""}`,
    missing ? `ohne Zeit (noch nie gelaufen): ${missing}` : null,
    // Gemessen wird ab der ersten fertigen Zeile; bis dahin fehlt der Palettenwechsel in den Zeiten
    sources.has("measured") ? "Zeiten mit Palettenwechsel gemessen" : sources.has("history") ? "Zeiten aus früheren Läufen, ohne Palettenwechsel" : null,
    estimated ? `${estimated}: geschätzt aus dem laufenden Lauf` : null,
  ];
  return el(
    "section",
    { class: "pallet", "aria-label": "Ablauf des Palettenprogramms" },
    el(
      "div",
      { class: "pallet-head" },
      el("span", { class: "pallet-title", text: `Palettenprogramm ${baseName(p.table)}` }),
      summary ? el("span", { class: "pallet-sum num", text: summary }) : null,
    ),
    el(
      "ol",
      { class: "pallet-list" },
      groups.map((g) =>
        el(
          "li",
          { class: `pal-${g.status}` },
          el("span", { class: "pal-mark", "aria-hidden": "true", text: PAL_MARK[g.status] }),
          el("span", { class: "visually-hidden", text: `${PAL_STATUS[g.status]}: ` }),
          el("span", { class: "pal-name", text: palletLabel(g.pallet) }),
          el(
            "span",
            { class: "pal-programs" },
            g.entries.flatMap((e, i) => [
              i ? " · " : null,
              el("span", { class: `pal-pgm${e.status === "current" ? " is-current" : ""}${e.status === "skipped" ? " is-skipped" : ""}`, text: programLabel(e.program) }),
            ]),
          ),
          el(
            "span",
            { class: "pal-time num" },
            g.status === "skipped" ? "gesperrt" : expectedText(g.entries),
            g === cur?.group && cur.since != null ? el("span", { class: "pal-since", text: `seit ${fmtTime(cur.since)} Uhr` }) : null,
          ),
        ),
      ),
    ),
    el("div", { class: "pallet-foot", text: foot.filter(Boolean).join(" · ") }),
  );
}

/** Großer Statusbalken: Symbol + Zustand, rechts wie lange schon. */
function statusBand(m, now) {
  if (!m.state) {
    return el("div", { class: "mc-status pending" }, el("span", { class: "mc-status-label", text: "Verbinde …" }));
  }
  return el(
    "div",
    { class: `mc-status st-${m.state}` },
    el("span", { class: "mc-status-icon", "aria-hidden": "true", text: stateIcon(m.state) }),
    el("span", { class: "mc-status-label", text: stateLabel(m.state) }),
    m.state_since != null
      ? el(
          "span",
          { class: "mc-status-time" },
          el("span", { class: "mc-status-duration num", text: fmtDuration(now - m.state_since) }),
          el("span", { class: "mc-status-since num", text: `seit ${fmtTime(m.state_since)} Uhr` }),
        )
      : null,
  );
}

/** Laufzeit des Programmlaufs – nur wenn sie von der Zustandsdauer abweicht (z. B. nach einem Stopp). */
function runText(m, now) {
  const run = m.run;
  if (!run || !RUN_STATES.has(m.state)) return null;
  if (m.state === "RUNNING" && run.start_observed && Math.abs(run.started_at - (m.state_since ?? 0)) < 5) return null;
  const prefix = run.start_observed ? "" : "≥ ";
  return `Lauf ${prefix}${fmtDuration(now - run.started_at)} · seit ${fmtTime(run.started_at)} Uhr`;
}

function renderLive(m, now) {
  const c = card(m.id);
  c.root.className = `card machine-card st-${m.state ?? "UNKNOWN"}`;

  const control = [m.control?.control, m.control?.nc_sw].filter(Boolean).join(" · ");
  const parts = [
    el(
      "header",
      { class: "mc-head" },
      el("div", { class: "mc-image" }, thumbFor(c, m)),
      el(
        "div",
        { class: "mc-title" },
        el("h2", { class: "machine-name", text: m.name }),
        m.note ? el("div", { class: "machine-host", text: m.note }) : null,
        el("div", { class: "machine-host", text: [`${m.host}:${m.port}`, control].filter(Boolean).join(" · ") }),
      ),
      statusBand(m, now),
    ),
  ];

  if (m.state === "NETWORK") {
    parts.push(
      el(
        "p",
        { class: "mc-message" },
        `Weder die Steuerung noch die Prüfadresse ${m.check_host} antworten – die Verbindung zum Standort ist gestört (VPN?). `
          + "Diese Zeit wird als „Keine Daten“ gebucht, nicht als „Offline“.",
      ),
    );
  } else if (m.state === "OFFLINE" || !m.connected) {
    parts.push(
      el("p", { class: "mc-message" }, m.connection_error ? `Keine Verbindung: ${m.connection_error}` : "Keine Verbindung zur Steuerung."),
    );
  } else {
    const programName = baseName(m.program);
    const current = m.current_program && m.current_program !== m.program ? `aktuell: ${baseName(m.current_program)}` : null;
    // Oberprogramm (z. B. Palettenprogramm): gezählt wird das aufgerufene Auftragsprogramm
    const caller = !m.caller ? null
      : m.caller === m.program ? "Oberprogramm – zählt zu keinem Auftrag"
      : `aufgerufen von ${baseName(m.caller)}`;
    // Zur Kontrolle: Welche Programme ruft das Oberprogramm laut seiner Datei auf?
    // (Bei einer lesbaren Palettentabelle steht der Ablauf als eigene Liste darunter.)
    const cf = m.pallet ? null : m.caller_file;
    const callerFile = !cf ? null
      : cf.calls ? `${baseName(m.caller)} ruft auf: ${cf.calls.length ? cf.calls.join(", ") : "– (kein Programmaufruf erkannt)"}`
      : `${baseName(m.caller)} nicht gelesen: ${cf.error}`;
    const run = runText(m, now);
    const orderThumb = orderThumbFor(c, m);
    parts.push(
      el(
        "div",
        { class: `program${orderThumb ? " has-image" : ""}` },
        el(
          "div",
          { class: "program-top" },
          el("div", { class: "program-name", text: programName ?? "Kein Programm angewählt" }),
          run ? el("div", { class: "program-run num", text: run, title: m.run.start_observed ? null : "Start vor Beginn der Erfassung" }) : null,
        ),
        m.program ? el("div", { class: "program-path", text: [m.program, current, caller].filter(Boolean).join(" · ") }) : null,
        callerFile ? el("div", { class: "program-path", text: callerFile }) : null,
        m.order
          ? el(
              "a",
              { class: "order-link", href: `auftraege.html?order=${encodeURIComponent(m.order.key)}` },
              `Auftrag ${m.order.order} (${m.order.year}) · Aufspannung ${m.order.setup} · Programm ${String(m.order.program).padStart(2, "0")}`,
            )
          : null,
        palletMarker(m, now),
        orderThumb,
      ),
      progressBlock(m),
      overrideBlock(m.override),
      el(
        "dl",
        { class: "facts" },
        fact("Betriebsart", execLabel(m.exec_mode)),
        fact("Programmstatus", pgmStateLabel(m.pgm_state)),
        fact("Satz", m.blocks ? blocksText(m.blocks).replace("Satz ", "") : null),
        toolFact(m),
        fact("Lauf-Nr.", m.run ? String(m.run.id) : null),
      ),
    );
    const warning = toolWarning(m);
    if (warning) parts.push(warning);
    if (m.errors.length) {
      parts.push(el("ul", { class: "errors" }, m.errors.map((text) => el("li", { text }))));
    }
    parts.push(palletBlock(m));
  }
  // Leere Bausteine (z. B. kein Fortschritt ohne laufendes Programm) auslassen – sonst stünde „null“ da
  c.live.replaceChildren(...parts.filter(Boolean));
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
            { class: "today-kpis" },
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
