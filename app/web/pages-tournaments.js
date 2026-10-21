// Turniere, Meldungen, Rangliste und Scorekarten-Erfassung.
import { api, h, field, select, showError, toast, fmtDate, fmtHcp, parseHcp, badge, STATUS, STATUS_KIND } from "./lib.js";
import { can, reload } from "./app.js";

// ---------------------------------------------------------------- Liste

export async function tournamentsPage() {
  const [tournaments, courses] = await Promise.all([api("GET", "/tournaments"), api("GET", "/courses")]);
  const slot = h("div");

  const startHole = select("start_hole", [["1", "Loch 1–9"], ["10", "Loch 10–18"]], "1");
  const startField = field("Gespielte Löcher", startHole);
  startField.hidden = true;
  const holes = select("holes_count", [["18", "18 Löcher"], ["9", "9 Löcher"]], "18", {
    onchange: () => { startField.hidden = holes.value !== "9"; },
  });

  const form = h("form", { class: "card form-grid", hidden: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    try {
      const res = await api("POST", "/tournaments", {
        name: fd.get("tname"), played_on: fd.get("played_on"), course_id: Number(fd.get("course_id")),
        holes_count: Number(fd.get("holes_count")),
        start_hole: fd.get("holes_count") === "9" ? Number(fd.get("start_hole")) : 1,
        allowance_pct: Number(fd.get("allowance_pct")),
      });
      toast("Turnier angelegt");
      location.hash = `#/turniere/${res.id}`;
    } catch (err) { showError(slot, err); }
  } },
  h("h2", { class: "span-all" }, "Neues Turnier"),
  slot,
  field("Name", h("input", { name: "tname", required: true, maxlength: 150, placeholder: "z. B. 2. Spieltag Herren 1" })),
  field("Datum", h("input", { name: "played_on", type: "date", required: true, value: new Date().toISOString().slice(0, 10) })),
  field("Platz", select("course_id", courses.map((c) => [c.id, c.name]), courses[0]?.id, { required: true })),
  field("Runde", holes),
  startField,
  field("Handicap-Anteil (%)", h("input", { name: "allowance_pct", type: "number", min: 1, max: 100, value: 100, required: true }), "z. B. 95 % im Einzel-Stableford"),
  h("div", { class: "actions span-all" },
    h("button", { type: "submit", class: "primary" }, "Anlegen"),
    h("button", { type: "button", class: "ghost", onclick: () => { form.hidden = true; } }, "Abbrechen")));

  const newBtn = can.edit() ? h("button", { class: "primary", onclick: () => {
    if (!courses.length) { toast("Bitte zuerst einen Platz anlegen."); location.hash = "#/plaetze/neu"; return; }
    form.hidden = false;
    form.querySelector("input").focus();
  } }, "Neues Turnier") : null;

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Turniere"), newBtn),
    form,
    tournaments.length
      ? h("div", { class: "card flush" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Datum"), h("th", {}, "Turnier"), h("th", {}, "Platz"), h("th", {}, "Runde"), h("th", { class: "num" }, "Meldungen"))),
        h("tbody", {}, tournaments.map((t) => h("tr", { class: "clickable", onclick: () => { location.hash = `#/turniere/${t.id}`; } },
          h("td", {}, fmtDate(t.played_on)),
          h("td", {}, h("a", { href: `#/turniere/${t.id}` }, t.name)),
          h("td", {}, t.course),
          h("td", {}, roundLabel(t)),
          h("td", { class: "num" }, t.entries))))))
      : h("div", { class: "card empty" }, h("p", {}, "Noch keine Turniere."),
        h("p", { class: "muted" }, "Reihenfolge: Platz anlegen → Spieler und Mannschaften erfassen → Turnier anlegen → Spieler melden → Scorekarten erfassen.")));
}

function roundLabel(t) {
  if (t.holes_count === 18) return "18 Loch";
  return t.start_hole === 10 ? "9 Loch (10–18)" : "9 Loch (1–9)";
}

// --------------------------------------------------------------- Detail

export async function tournamentDetailPage(id) {
  const [t, players, teams, courses] = await Promise.all([
    api("GET", `/tournaments/${id}`), api("GET", "/players"), api("GET", "/teams"), api("GET", "/courses"),
  ]);
  if (t.kind === "single" && t.entries.length) {
    location.replace(`#/scorekarte/${t.entries[0].id}`);
    return h("p", { class: "pad muted" }, "Weiterleitung …");
  }
  const course = courses.find((c) => c.id === t.course_id);
  const tees = (course?.tees || []).filter((x) => t.holes_count === 9 || x.holes_count === 18);
  const entered = new Set(t.entries.map((e) => e.player_id));
  const slot = h("div");

  // Melden: einzelner Spieler
  const free = players.filter((p) => !entered.has(p.id));
  const teeOpts = tees.map((x) => [x.id, `${x.name} (${x.gender}) · CR ${String(x.course_rating).replace(".", ",")} / SR ${x.slope_rating}`]);
  const teamOpts = [["", "— keine —"], ...teams.map((x) => [x.id, x.name])];

  const single = h("form", { class: "form-grid", onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(single);
    const hi = parseHcp(fd.get("hi"));
    try {
      await api("POST", `/tournaments/${id}/entries`, {
        player_id: Number(fd.get("player_id")), tee_id: Number(fd.get("tee_id")),
        team_id: fd.get("team_id") ? Number(fd.get("team_id")) : null, handicap_index: hi,
      });
      toast("Spieler gemeldet");
      reload();
    } catch (err) { showError(slot, err); }
  } },
  field("Spieler", select("player_id", free.map((p) => [p.id, `${p.last_name}, ${p.first_name} (${fmtHcp(p.handicap_index)})`]), "", { required: true })),
  field("Abschlag", select("tee_id", teeOpts, tees.find((x) => x.gender === "m")?.id, { required: true })),
  field("Mannschaft", select("team_id", teamOpts, "")),
  field("HCP-Index am Spieltag", h("input", { name: "hi", inputmode: "decimal", placeholder: "leer = aktueller Index" })),
  h("div", { class: "actions span-all" }, h("button", { type: "submit", class: "primary", disabled: !free.length || !tees.length }, "Melden")));

  // Melden: ganze Mannschaft
  const teamForm = h("form", { class: "form-grid", onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(teamForm);
    const team = teams.find((x) => x.id === Number(fd.get("team_id")));
    const teeId = Number(fd.get("tee_id"));
    const todo = (team?.members || []).filter((m) => !entered.has(m.id));
    const failed = [];
    for (const m of todo) {
      try {
        await api("POST", `/tournaments/${id}/entries`, { player_id: m.id, tee_id: teeId, team_id: team.id });
      } catch (err) { failed.push(`${m.name}: ${err.message}`); }
    }
    if (failed.length) showError(slot, Object.assign(new Error(`${todo.length - failed.length} von ${todo.length} gemeldet.`), { errors: failed }));
    else { toast(`${todo.length} Spieler gemeldet`); reload(); }
  } },
  field("Mannschaft", select("team_id", teams.map((x) => [x.id, `${x.name} (${x.members.length})`]), teams[0]?.id, { required: true })),
  field("Abschlag für alle", select("tee_id", teeOpts, tees.find((x) => x.gender === "m")?.id, { required: true })),
  h("div", { class: "actions span-all" }, h("button", { type: "submit", class: "primary", disabled: !teams.length || !tees.length }, "Alle melden")));

  const entryBox = can.edit() ? h("details", { class: "card", open: t.entries.length === 0 },
    h("summary", {}, "Spieler melden"),
    slot,
    !tees.length ? h("p", { class: "alert warn" }, "Der Platz hat keinen passenden Abschlag für diese Runde.") : null,
    h("div", { class: "grid-2 tight" },
      h("div", {}, h("h3", {}, "Einzeln"), single),
      h("div", {}, h("h3", {}, "Ganze Mannschaft"), teamForm))) : null;

  const entriesTable = h("div", { class: "card flush" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, "Spieler"), h("th", {}, "Mannschaft"), h("th", {}, "Abschlag"),
      h("th", { class: "num" }, "HCPI"), h("th", { class: "num" }, "Spielvorg."),
      h("th", {}, "Scorekarte"), h("th", { class: "num" }, "Brutto"), h("th", { class: "num" }, "Stbf netto"), h("th", {}))),
    h("tbody", {}, t.entries.length ? t.entries.map((e) => {
      const sc = e.scorecard;
      return h("tr", {},
        h("td", {}, e.player),
        h("td", {}, can.edit()
          ? select("team", teamOpts, e.team_id ?? "", { class: "compact", "aria-label": `Mannschaft von ${e.player}`, onchange: async (ev) => {
            try {
              await api("PATCH", `/entries/${e.id}`, { team_id: ev.target.value ? Number(ev.target.value) : null });
              toast("Mannschaft geändert");
              reload();
            } catch (err) { showError(slot, err); }
          } })
          : (e.team || h("span", { class: "muted" }, "–"))),
        h("td", {}, e.tee),
        h("td", { class: "num" }, fmtHcp(e.handicap_index)),
        h("td", { class: "num" }, e.playing_hcp),
        h("td", {}, sc ? [badge(STATUS[sc.status], STATUS_KIND[sc.status]), sc.warnings ? badge(`${sc.warnings} Hinweis${sc.warnings > 1 ? "e" : ""}`, "warn") : null] : h("span", { class: "muted" }, "offen")),
        h("td", { class: "num" }, sc ? (sc.gross_strokes ?? "–") : ""),
        h("td", { class: "num strong" }, sc ? sc.net_stableford : ""),
        h("td", { class: "right nowrap" },
          h("a", { href: `#/scorekarte/${e.id}`, class: "button small" }, sc ? (can.edit() ? "Bearbeiten" : "Ansehen") : "Erfassen"),
          can.edit() && sc?.status !== "confirmed" ? h("button", { class: "link danger", title: "Meldung entfernen", onclick: async () => {
            if (!confirm(`${e.player} aus dem Turnier entfernen?${sc ? " Die Scorekarte wird gelöscht." : ""}`)) return;
            try { await api("DELETE", `/entries/${e.id}`); toast("Meldung entfernt"); reload(); } catch (err) { showError(slot, err); }
          } }, "✕") : null));
    }) : h("tr", {}, h("td", { colspan: 9, class: "muted" }, "Noch keine Spieler gemeldet.")))));

  // --- Turnier bearbeiten / löschen
  const editSlot = h("div");
  const hasEntries = t.entries.length > 0;
  const confirmed = t.entries.filter((e) => e.scorecard?.status === "confirmed").length;
  const eStart = select("start_hole", [["1", "Loch 1–9"], ["10", "Loch 10–18"]], t.start_hole, { disabled: hasEntries });
  const eStartField = field("Gespielte Löcher", eStart);
  eStartField.hidden = t.holes_count !== 9;
  const eHoles = select("holes_count", [["18", "18 Löcher"], ["9", "9 Löcher"]], t.holes_count, {
    disabled: hasEntries, onchange: () => { eStartField.hidden = eHoles.value !== "9"; },
  });
  const editForm = h("form", { class: "card form-grid", hidden: true, onsubmit: async (ev) => {
    ev.preventDefault();
    const fd = new FormData(editForm);
    // Gesperrte Felder fehlen in FormData – nur senden, was änderbar ist.
    const body = { name: fd.get("ename"), played_on: fd.get("played_on"), note: fd.get("note") || null };
    if (fd.has("allowance_pct")) body.allowance_pct = Number(fd.get("allowance_pct"));
    if (!hasEntries) {
      body.course_id = Number(fd.get("course_id"));
      body.holes_count = Number(fd.get("holes_count"));
      body.start_hole = body.holes_count === 9 ? Number(fd.get("start_hole")) : 1;
    }
    const send = async (force) => {
      try {
        const res = await api("PATCH", `/tournaments/${id}${force ? "?force=true" : ""}`, body);
        toast(res.recalculated ? `Gespeichert, ${res.recalculated} Spielvorgabe(n) neu berechnet` : "Turnier gespeichert");
        reload();
      } catch (err) {
        if (err.code === "tournament_in_use" && confirm(`${err.message}\n\nTrotzdem speichern?`)) { send(true); return; }
        showError(editSlot, err);
      }
    };
    send(false);
  } },
  h("h2", { class: "span-all" }, "Turnier bearbeiten"),
  editSlot,
  hasEntries ? h("p", { class: "muted small span-all" }, "Platz und Runde lassen sich nur ändern, solange niemand gemeldet ist. Eine Änderung des Handicap-Anteils berechnet die Spielvorgaben neu.") : null,
  confirmed ? h("div", { class: "alert warn span-all" }, `${confirmed} Scorekarte(n) bestätigt – Handicap-Anteil, Platz und Runde sind gesperrt.`) : null,
  field("Name", h("input", { name: "ename", required: true, maxlength: 150, value: t.name })),
  field("Datum", h("input", { name: "played_on", type: "date", required: true, value: String(t.played_on).slice(0, 10) })),
  field("Platz", select("course_id", courses.map((c) => [c.id, c.name]), t.course_id, { disabled: hasEntries })),
  field("Runde", eHoles),
  eStartField,
  field("Handicap-Anteil (%)", h("input", { name: "allowance_pct", type: "number", min: 1, max: 100, value: t.allowance_pct, required: true, disabled: confirmed > 0 })),
  field("Notiz", h("input", { name: "note", maxlength: 300, value: t.note || "" })),
  h("div", { class: "actions span-all" },
    h("button", { type: "submit", class: "primary" }, "Speichern"),
    h("button", { type: "button", class: "ghost", onclick: () => { editForm.hidden = true; } }, "Abbrechen")));

  const cards = t.entries.filter((e) => e.scorecard).length;
  const headActions = can.edit() ? h("div", { class: "inline" },
    h("button", { class: "ghost", onclick: () => { editForm.hidden = !editForm.hidden; if (!editForm.hidden) editForm.querySelector("input").focus(); } }, "Bearbeiten"),
    h("button", {
      class: "ghost danger", disabled: confirmed > 0,
      title: confirmed ? "Bestätigte Scorekarten verhindern das Löschen" : "Turnier löschen",
      onclick: async () => {
        const what = hasEntries ? ` Dabei werden ${t.entries.length} Meldung(en)${cards ? ` und ${cards} Scorekarte(n)` : ""} unwiderruflich gelöscht.` : "";
        if (!confirm(`Turnier „${t.name}“ löschen?${what}`)) return;
        try { await api("DELETE", `/tournaments/${id}`); toast("Turnier gelöscht"); location.hash = "#/turniere"; } catch (err) { showError(slot, err); }
      },
    }, "Löschen")) : null;

  return h("section", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("a", { href: "#/turniere", class: "back" }, "← Turniere"), h("h1", {}, t.name),
        h("p", { class: "muted" }, `${fmtDate(t.played_on)} · ${t.course} · ${roundLabel(t)} · Handicap-Anteil ${t.allowance_pct} %`),
        t.note ? h("p", { class: "muted small" }, t.note) : null),
      headActions),
    editForm,
    entryBox,
    h("h2", {}, `Meldungen (${t.entries.length})`),
    entriesTable,
    t.entries.some((e) => e.scorecard) ? await leaderboard(id) : null);
}

// ------------------------------------------------------------ Rangliste

const METRICS = [
  ["net_stableford", "Stableford netto"], ["gross_stableford", "Stableford brutto"],
  ["net_strokes", "Zählspiel netto"], ["gross_strokes", "Zählspiel brutto"],
];

async function leaderboard(id) {
  const box = h("div");
  const metricSel = select("metric", METRICS, "net_stableford", { onchange: load });
  const bestN = h("input", { type: "number", min: 1, max: 50, placeholder: "alle", class: "narrow", onchange: load });

  async function load() {
    const q = new URLSearchParams({ metric: metricSel.value });
    if (bestN.value) q.set("team_best_n", bestN.value);
    try {
      const lb = await api("GET", `/tournaments/${id}/leaderboard?${q}`);
      const label = METRICS.find(([m]) => m === lb.metric)[1];
      // replaceChildren würde null als Text "null" einfügen – daher filtern.
      box.replaceChildren(...[
        h("div", { class: "card flush" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Platz"), h("th", {}, "Spieler"), h("th", { class: "num" }, "Spielvorg."), h("th", { class: "num" }, label))),
          h("tbody", {}, lb.players.map((r) => h("tr", {},
            h("td", { class: "num" }, r.place ?? "–"),
            h("td", {}, r.player),
            h("td", { class: "num" }, r.playing_hcp),
            h("td", { class: "num strong" }, r.value ?? h("span", { class: "muted", title: "Gestrichene Löcher – nicht in der Zählspielwertung" }, "n. g."))))))),
        h("p", { class: "muted small" }, "Gleichstand: Stechen über die letzten 9, 6, 3 und das letzte Loch (bei 9 Loch: 6, 3, 1)."),
        lb.teams.length ? h("div", {},
          h("h3", {}, "Mannschaftswertung (Stableford netto)"),
          h("div", { class: "card flush" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Platz"), h("th", {}, "Mannschaft"), h("th", { class: "num" }, "Gewertet"), h("th", { class: "num" }, "Punkte"))),
            h("tbody", {}, lb.teams.map((tm) => h("tr", {},
              h("td", { class: "num" }, tm.place),
              h("td", {}, teamName(tm.team_id)),
              h("td", { class: "num" }, tm.counted),
              h("td", { class: "num strong" }, tm.points))))))) : null,
      ].filter(Boolean));
    } catch (err) { showError(box, err); }
  }
  let teamNames = {};
  const teamName = (tid) => teamNames[tid] || `Mannschaft ${tid}`;
  try { teamNames = Object.fromEntries((await api("GET", "/teams")).map((x) => [x.id, x.name])); } catch { /* optional */ }
  await load();
  return h("div", {},
    h("div", { class: "section-head" }, h("h2", {}, "Rangliste"),
      h("div", { class: "inline" }, metricSel, h("label", { class: "inline small" }, "Mannschaft: beste ", bestN))),
    box);
}

// ------------------------------------------------------------ Scorekarte

export async function scorecardPage(entryId) {
  const info = await api("GET", `/entries/${entryId}`);
  let card = null;
  try { card = await api("GET", `/entries/${entryId}/scorecard`); } catch (err) { if (err.status !== 404) throw err; }
  const editable = can.edit();
  const holes = info.holes;
  const values = new Map(holes.map((x) => [x.number, null]));
  if (card) card.holes.forEach((x) => values.set(x.number, x.strokes));
  let version = card ? card.version : null;
  const touched = new Set(card ? holes.map((x) => x.number) : []);

  const slot = h("div");
  const summary = h("div", { class: "summary" });
  const notes = h("div");
  const inputs = new Map();
  const cells = new Map(); // Ausgabezellen je Loch: received / net / gross

  function strokesInput(hole, idx) {
    const v = values.get(hole.number);
    const inp = h("input", {
      class: "stroke", inputmode: "numeric", maxlength: 2, autocomplete: "off",
      "aria-label": `Schläge Loch ${hole.number}`,
      value: v === null ? (touched.has(hole.number) ? "–" : "") : v,
      readonly: !editable,
    });
    inp.addEventListener("input", () => {
      const raw = inp.value.trim();
      if (raw === "-" || raw === "–" || raw.toLowerCase() === "x") { inp.value = "–"; values.set(hole.number, null); touched.add(hole.number); }
      else if (raw === "") { values.set(hole.number, null); touched.delete(hole.number); }
      else if (/^\d{1,2}$/.test(raw)) { values.set(hole.number, Number(raw)); touched.add(hole.number); }
      else { inp.classList.add("invalid"); return; }
      inp.classList.remove("invalid");
      schedulePreview();
      // Nach einer einstelligen Zahl 2–9 direkt weiter; bei "1" auf 10–20 warten.
      if (/^[2-9]$/.test(raw) || /^1\d$/.test(raw) || inp.value === "–") focusNext(idx);
    });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); focusNext(idx); }
      if (e.key === "Backspace" && !inp.value) focusNext(idx - 2);
    });
    inp.addEventListener("focus", () => inp.select());
    inputs.set(hole.number, inp);
    return inp;
  }
  function focusNext(idx) {
    const next = [...inputs.values()][idx + 1];
    if (next) next.focus();
  }

  function half(list, offset, label) {
    const outCells = { received: h("td"), strokes: h("td", { class: "sum" }), net: h("td", { class: "sum" }), gross: h("td", { class: "sum" }) };
    const rows = {
      head: h("tr", {}, h("th", {}, "Loch"), list.map((x) => h("th", { class: "hole" }, x.number)), h("th", { class: "sum" }, label)),
      par: h("tr", { class: "muted" }, h("th", {}, "Par"), list.map((x) => h("td", {}, x.par)), h("td", { class: "sum" }, list.reduce((a, x) => a + x.par, 0))),
      si: h("tr", { class: "muted" }, h("th", {}, "HCP"), list.map((x) => h("td", {}, x.stroke_index)), h("td")),
      recv: h("tr", { class: "muted" }, h("th", { title: "Vorgabeschläge auf diesem Loch" }, "Vorgabe"), list.map((x) => h("td", {}, dots(x.received))), outCells.received),
      strokes: h("tr", { class: "input-row" }, h("th", {}, "Schläge"), list.map((x, i) => h("td", {}, strokesInput(x, offset + i))), outCells.strokes),
      net: h("tr", {}, h("th", {}, "Pkt. netto"), list.map((x) => { const c = h("td", { class: "pts" }); cells.set(`n${x.number}`, c); return c; }), outCells.net),
      gross: h("tr", { class: "muted" }, h("th", {}, "Pkt. brutto"), list.map((x) => { const c = h("td", { class: "pts" }); cells.set(`g${x.number}`, c); return c; }), outCells.gross),
    };
    return { table: h("table", { class: "scorecard" }, Object.values(rows)), outCells, list };
  }
  const halves = holes.length === 18
    ? [half(holes.slice(0, 9), 0, "Out"), half(holes.slice(9), 9, "In")]
    : [half(holes, 0, "Summe")];

  function dots(n) {
    if (n === 0) return "";
    if (n < 0) return `−${-n}`;
    return n <= 3 ? "•".repeat(n) : String(n);
  }

  let timer;
  let seq = 0;
  function schedulePreview() { clearTimeout(timer); timer = setTimeout(preview, 250); }
  async function preview() {
    const my = ++seq;
    try {
      const res = await api("POST", `/entries/${entryId}/scorecard/preview`, { strokes: holes.map((x) => values.get(x.number)) });
      if (my !== seq) return;
      renderResult(res);
    } catch (err) { showError(notes, err); }
  }

  function renderResult(res) {
    notes.replaceChildren(
      ...res.errors.map((e) => h("div", { class: "alert error" }, e)),
      ...res.warnings.map((w) => h("div", { class: "alert warn" }, w)));
    const r = res.result;
    for (const x of holes) {
      const hr = r && r.holes.find((y) => y.number === x.number);
      const has = values.get(x.number) !== null;
      cells.get(`n${x.number}`).textContent = hr && has ? hr.net_points : "";
      cells.get(`g${x.number}`).textContent = hr && has ? hr.gross_points : "";
      cells.get(`n${x.number}`).className = `pts ${hr && has ? `p${Math.min(hr.net_points, 4)}` : ""}`;
    }
    for (const hv of halves) {
      const s = (f) => hv.list.reduce((a, x) => a + (f(x) || 0), 0);
      const hrs = (x) => r && r.holes.find((y) => y.number === x.number);
      hv.outCells.strokes.textContent = s((x) => values.get(x.number)) || "";
      hv.outCells.net.textContent = r ? s((x) => hrs(x)?.net_points) : "";
      hv.outCells.gross.textContent = r ? s((x) => hrs(x)?.gross_points) : "";
      hv.outCells.received.textContent = s((x) => x.received) || "";
    }
    const missing = holes.filter((x) => values.get(x.number) === null && !touched.has(x.number)).length;
    summary.replaceChildren(...[
      stat("Spielvorgabe", info.playing_hcp),
      stat("Brutto", r?.gross_strokes ?? "–", r && !r.complete ? "gestrichene/offene Löcher" : ""),
      stat("Netto", r?.net_strokes ?? "–"),
      stat("Stableford brutto", r ? r.gross_stableford : "–"),
      stat("Stableford netto", r ? r.net_stableford : "–", null, true),
      missing ? h("p", { class: "muted small span-all" }, `${missing} Loch/Löcher noch ohne Eingabe.`) : null,
    ].filter(Boolean));
  }
  function stat(label, value, sub, big) {
    return h("div", { class: `stat${big ? " big" : ""}` }, h("span", { class: "label" }, label), h("span", { class: "value" }, value), sub ? h("span", { class: "sub" }, sub) : null);
  }

  const statusSel = select("status", Object.entries(STATUS), card ? card.status : "draft", { disabled: !editable });
  const saveBtn = h("button", { class: "primary", hidden: !editable, onclick: save }, "Speichern");
  async function save() {
    const open = holes.filter((x) => values.get(x.number) === null && !touched.has(x.number));
    if (open.length && !confirm(`${open.length} Loch/Löcher ohne Eingabe (${open.map((x) => x.number).join(", ")}) werden als gestrichen gespeichert. Fortfahren?`)) return;
    saveBtn.disabled = true;
    try {
      const res = await api("PUT", `/entries/${entryId}/scorecard`, {
        strokes: holes.map((x) => values.get(x.number)),
        expected_version: version,
        status: statusSel.value,
      });
      version = res.version;
      open.forEach((x) => { touched.add(x.number); inputs.get(x.number).value = "–"; });
      slot.replaceChildren();
      toast("Scorekarte gespeichert");
    } catch (err) {
      if (err.status === 409) err.errors = [...(err.errors || []), "Tipp: Seite neu laden, um den aktuellen Stand zu sehen."];
      showError(slot, err);
    } finally { saveBtn.disabled = false; }
  }

  // Erste Auswertung
  if (card) renderResult({ errors: [], warnings: card.warnings || [], result: card });
  else renderResult({ errors: [], warnings: [], result: null });

  return h("section", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, info.kind === "single"
        ? h("a", { href: `#/spieler/${info.player_id}`, class: "back" }, `← Runden von ${info.player}`)
        : h("a", { href: `#/turniere/${info.tournament_id}`, class: "back" }, `← ${info.tournament}`),
        h("h1", {}, info.player),
        h("p", { class: "muted" }, info.kind === "single" ? `Einzelrunde · ${fmtDate(info.played_on)} · ` : "",
          `Abschlag ${info.tee} · HCP-Index ${fmtHcp(info.handicap_index)} · Spielvorgabe ${info.playing_hcp}`)),
      info.kind === "single" && editable ? h("button", { class: "ghost danger", onclick: async () => {
        if (!confirm("Diese Einzelrunde mit Scorekarte löschen?")) return;
        try { await api("DELETE", `/entries/${entryId}`); toast("Einzelrunde gelöscht"); location.hash = `#/spieler/${info.player_id}`; } catch (err) { showError(slot, err); }
      } }, "Runde löschen") : null),
    slot,
    h("div", { class: "card" },
      editable ? h("p", { class: "muted small" }, "Schläge eintragen – der Cursor springt automatisch weiter. „-“ oder „x“ = Loch gestrichen. Enter = nächstes Loch.") : null,
      h("div", { class: "scroll-x" }, halves.map((x) => x.table)),
      notes),
    h("div", { class: "card sticky-bottom" },
      summary,
      h("div", { class: "actions" }, field("Status", statusSel), saveBtn)));
}
