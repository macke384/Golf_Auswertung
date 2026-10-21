// Einzelrunden (privat/Training) und persönliche Auswertung pro Spieler.
import { api, h, field, select, showError, toast, fmtDate, fmtHcp, parseHcp, badge, STATUS, STATUS_KIND } from "./lib.js";
import { can, reload } from "./app.js";

function roundLabel(r) {
  if (r.holes_count === 18) return "18 Loch";
  return r.start_hole === 10 ? "9 Loch (10–18)" : "9 Loch (1–9)";
}

function cardCells(sc) {
  if (!sc) return [h("td", {}, h("span", { class: "muted" }, "offen")), h("td"), h("td"), h("td")];
  return [
    h("td", {}, badge(STATUS[sc.status], STATUS_KIND[sc.status])),
    h("td", { class: "num" }, sc.gross_strokes ?? h("span", { class: "muted", title: "gestrichene Löcher" }, "–")),
    h("td", { class: "num" }, sc.net_strokes ?? h("span", { class: "muted" }, "–")),
    h("td", { class: "num strong" }, sc.net_stableford),
  ];
}

/** Formular zum Anlegen einer Einzelrunde; preset.player_id optional. */
async function newRoundForm(preset = {}) {
  const [players, courses] = await Promise.all([api("GET", "/players"), api("GET", "/courses")]);
  const slot = h("div");
  const teeSel = h("select", { name: "tee_id", required: true });
  const holes = select("holes_count", [["18", "18 Löcher"], ["9", "9 Löcher"]], "18");
  const start = select("start_hole", [["1", "Loch 1–9"], ["10", "Loch 10–18"]], "1");
  const startField = field("Gespielte Löcher", start);
  startField.hidden = true;
  let tees = [];

  const playerSel = select("player_id", players.map((p) => [p.id, `${p.last_name}, ${p.first_name} (${fmtHcp(p.handicap_index)})`]), preset.player_id ?? "", { required: true, onchange: fillTees });
  const courseSel = select("course_id", courses.map((c) => [c.id, c.name]), courses[0]?.id, { required: true, onchange: loadTees });

  async function loadTees() {
    if (!courseSel.value) return;
    tees = (await api("GET", `/courses/${courseSel.value}`)).tees;
    fillTees();
  }
  function fillTees() {
    const gender = players.find((p) => String(p.id) === playerSel.value)?.gender;
    const n = Number(holes.value);
    const usable = tees.filter((t) => n === 9 || t.holes_count === 18);
    const preferred = usable.find((t) => t.gender === gender) || usable[0];
    teeSel.replaceChildren(...usable.map((t) => h("option", { value: t.id, selected: t === preferred },
      `${t.name} (${t.gender}) · ${t.holes_count} L · CR ${String(t.course_rating).replace(".", ",")} / SR ${t.slope_rating}`)));
    // 9-Loch-Abschlag hat keine Löcher 10–18
    const tee = usable.find((t) => String(t.id) === teeSel.value);
    startField.hidden = n !== 9 || tee?.holes_count === 9;
  }
  holes.addEventListener("change", fillTees);
  teeSel.addEventListener("change", fillTees);

  const form = h("form", { class: "card form-grid", onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const hi = parseHcp(fd.get("hi"));
    if (hi !== null && Number.isNaN(Number(hi))) { showError(slot, new Error("HCP-Index bitte als Zahl, z. B. 18,4.")); return; }
    try {
      const res = await api("POST", "/rounds", {
        player_id: Number(fd.get("player_id")), tee_id: Number(fd.get("tee_id")),
        played_on: fd.get("played_on"), holes_count: Number(fd.get("holes_count")),
        start_hole: startField.hidden ? 1 : Number(fd.get("start_hole")),
        handicap_index: hi, note: fd.get("note") || null,
      });
      toast("Einzelrunde angelegt – jetzt Schläge eintragen");
      location.hash = `#/scorekarte/${res.entry_id}`;
    } catch (err) { showError(slot, err); }
  } },
  h("h2", { class: "span-all" }, "Neue Einzelrunde"),
  h("p", { class: "muted small span-all" }, "Private Runde oder Training eines Spielers. Spielvorgabe mit 100 % Handicap-Anteil."),
  slot,
  field("Spieler", playerSel),
  field("Datum", h("input", { name: "played_on", type: "date", required: true, value: new Date().toISOString().slice(0, 10) })),
  field("Platz", courseSel),
  field("Runde", holes),
  field("Abschlag", teeSel),
  startField,
  field("HCP-Index am Spieltag", h("input", { name: "hi", inputmode: "decimal", placeholder: "leer = aktueller Index" })),
  field("Notiz", h("input", { name: "note", maxlength: 300, placeholder: "optional, z. B. Training, Zählspiel mit Freunden" })),
  h("div", { class: "actions span-all" },
    h("button", { type: "submit", class: "primary", disabled: !players.length || !courses.length }, "Anlegen und Scorekarte öffnen")));
  if (!players.length || !courses.length) slot.append(h("div", { class: "alert warn" }, "Zuerst mindestens einen Spieler und einen Platz anlegen."));
  await loadTees();
  return form;
}

export async function roundsPage() {
  const rounds = await api("GET", "/rounds");
  const formBox = h("div");
  const newBtn = can.edit() ? h("button", { class: "primary", onclick: async () => {
    formBox.replaceChildren(await newRoundForm());
    formBox.querySelector("select").focus();
  } }, "Neue Einzelrunde") : null;

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Einzelrunden"), newBtn),
    formBox,
    rounds.length ? h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, "Datum"), h("th", {}, "Spieler"), h("th", {}, "Platz"), h("th", {}, "Runde"),
        h("th", { class: "num" }, "Spielvorg."), h("th", {}, "Scorekarte"),
        h("th", { class: "num" }, "Brutto"), h("th", { class: "num" }, "Netto"), h("th", { class: "num" }, "Stbf netto"))),
      h("tbody", {}, rounds.map((r) => h("tr", { class: "clickable", onclick: () => { location.hash = `#/scorekarte/${r.entry_id}`; } },
        h("td", {}, fmtDate(r.played_on)),
        h("td", {}, h("a", { href: `#/spieler/${r.player_id}`, onclick: (e) => e.stopPropagation() }, r.player),
          r.note ? h("div", { class: "muted small" }, r.note) : null),
        h("td", {}, r.course, h("div", { class: "muted small" }, r.tee)),
        h("td", {}, roundLabel(r)),
        h("td", { class: "num" }, r.playing_hcp),
        ...cardCells(r.scorecard))))))
      : h("div", { class: "card empty" }, h("p", {}, "Noch keine Einzelrunden erfasst.")));
}

// ------------------------------------------------- Spieler: persönliche Auswertung

export async function playerDetailPage(id) {
  const data = await api("GET", `/players/${id}/rounds`);
  const p = data.player;
  const formBox = h("div");
  const filter = select("kind", [["", "Alle Runden"], ["tournament", "Nur Turniere"], ["single", "Nur Einzelrunden"]], "", { onchange: renderRows });
  const tbody = h("tbody");

  function renderRows() {
    const rows = data.rounds.filter((r) => !filter.value || r.kind === filter.value);
    tbody.replaceChildren(...(rows.length ? rows.map((r) => h("tr", { class: "clickable", onclick: () => { location.hash = `#/scorekarte/${r.entry_id}`; } },
      h("td", {}, fmtDate(r.played_on)),
      h("td", {}, r.kind === "single"
        ? [badge("Einzelrunde"), r.note ? h("span", { class: "muted small" }, ` ${r.note}`) : null]
        : h("a", { href: `#/turniere/${r.tournament_id}`, onclick: (e) => e.stopPropagation() }, r.name)),
      h("td", {}, r.course, h("div", { class: "muted small" }, r.tee)),
      h("td", {}, roundLabel(r)),
      h("td", { class: "num" }, fmtHcp(r.handicap_index)),
      h("td", { class: "num" }, r.playing_hcp),
      ...cardCells(r.scorecard)))
      : [h("tr", {}, h("td", { colspan: 10, class: "muted" }, "Keine Runden."))]));
  }
  renderRows();

  function statBlock(label, st) {
    if (!st.rounds) return null;
    const v = (x) => (x === null || x === undefined ? "–" : String(x).replace(".", ","));
    return h("div", { class: "card" },
      h("h2", {}, label),
      h("div", { class: "summary" },
        h("div", { class: "stat" }, h("span", { class: "label" }, "Runden"), h("span", { class: "value" }, st.rounds),
          st.complete !== st.rounds ? h("span", { class: "sub" }, `${st.complete} vollständig`) : null),
        h("div", { class: "stat" }, h("span", { class: "label" }, "Bestes Brutto"), h("span", { class: "value" }, v(st.best_gross))),
        h("div", { class: "stat" }, h("span", { class: "label" }, "Ø Brutto"), h("span", { class: "value" }, v(st.avg_gross))),
        h("div", { class: "stat big" }, h("span", { class: "label" }, "Beste Stbf netto"), h("span", { class: "value" }, v(st.best_net_stableford))),
        h("div", { class: "stat" }, h("span", { class: "label" }, "Ø Stbf netto"), h("span", { class: "value" }, v(st.avg_net_stableford)))));
  }

  return h("section", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("a", { href: "#/spieler", class: "back" }, "← Spieler"),
        h("h1", {}, `${p.first_name} ${p.last_name}`),
        h("p", { class: "muted" }, `HCP-Index ${fmtHcp(p.handicap_index)}`, p.dgv_id ? ` · DGV ${p.dgv_id}` : "")),
      can.edit() ? h("button", { class: "primary", onclick: async () => {
        formBox.replaceChildren(await newRoundForm({ player_id: p.id }));
      } }, "Einzelrunde erfassen") : null),
    formBox,
    h("div", { class: "grid-2" }, statBlock("18-Loch-Runden", data.stats["18"]), statBlock("9-Loch-Runden", data.stats["9"])),
    h("div", { class: "section-head" }, h("h2", {}, `Runden (${data.rounds.length})`), filter),
    h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, "Datum"), h("th", {}, "Turnier / Art"), h("th", {}, "Platz"), h("th", {}, "Runde"),
        h("th", { class: "num" }, "HCPI"), h("th", { class: "num" }, "Spielvorg."), h("th", {}, "Scorekarte"),
        h("th", { class: "num" }, "Brutto"), h("th", { class: "num" }, "Netto"), h("th", { class: "num" }, "Stbf netto"))),
      tbody)),
    h("p", { class: "muted small" }, "Durchschnitte und Bestwerte Brutto nur aus vollständigen Runden (ohne gestrichene Löcher). Stableford aus allen Runden mit Scorekarte."));
}

