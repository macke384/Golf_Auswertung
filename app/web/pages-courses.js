// Golfplätze mit Abschlägen (CR/SR/Par) und Löchern (Par, Loch-HCP).
import { api, h, field, select, showError, toast, badge } from "./lib.js";
import { can, reload } from "./app.js";

const GENDER = [["m", "Herren"], ["w", "Damen"], ["d", "Divers"]];
const DEFAULT_PAR_18 = [4, 4, 3, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 5, 4];

export async function coursesPage() {
  const courses = await api("GET", "/courses");
  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Plätze"),
      can.edit() ? h("a", { class: "button primary", href: "#/plaetze/neu" }, "Neuer Platz") : null),
    courses.length
      ? h("div", { class: "card flush" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Platz"), h("th", {}, "Abschläge"))),
        h("tbody", {}, courses.map((c) => h("tr", { class: "clickable", onclick: () => { location.hash = `#/plaetze/${c.id}`; } },
          h("td", {}, h("a", { href: `#/plaetze/${c.id}` }, c.name)),
          h("td", {}, c.tees.map((t) => h("span", { class: "chip" }, `${t.name} ${t.gender} · ${t.holes_count} L · CR ${num(t.course_rating)} / SR ${t.slope_rating}`))))))))
      : h("div", { class: "card empty" }, h("p", {}, "Noch keine Plätze erfasst."),
        can.edit() ? h("a", { class: "button primary", href: "#/plaetze/neu" }, "Ersten Platz anlegen") : null));
}

function num(v) { return String(v).replace(".", ","); }
function toNum(text) { return Number(String(text).trim().replace(",", ".")); }

/** Eingabebereich für einen Abschlag; liefert {el, read()} */
function teeEditor(initial = {}) {
  const holesSel = select("holes_count", [["18", "18 Löcher"], ["9", "9 Löcher"]], initial.holes_count || 18);
  const parSum = h("output", { class: "sum" });
  const grid = h("div", { class: "hole-grid" });
  const errors = h("div");

  function buildGrid() {
    const n = Number(holesSel.value);
    const pars = DEFAULT_PAR_18.slice(0, n);
    grid.replaceChildren(
      h("div", { class: "hg-label" }, "Loch"), ...pars.map((_, i) => h("div", { class: "hg-head" }, String(i + 1))),
      h("div", { class: "hg-label" }, "Par"), ...pars.map((p, i) => h("input", { class: "par", "aria-label": `Par Loch ${i + 1}`, inputmode: "numeric", value: p, maxlength: 1, oninput: updateSum })),
      h("div", { class: "hg-label" }, "HCP"), ...pars.map((_, i) => h("input", { class: "si", "aria-label": `Loch-HCP Loch ${i + 1}`, inputmode: "numeric", maxlength: 2, placeholder: "–", oninput: checkSi })));
    grid.style.setProperty("--cols", n);
    updateSum();
  }
  function updateSum() {
    const s = [...grid.querySelectorAll("input.par")].reduce((a, i) => a + (Number(i.value) || 0), 0);
    parSum.textContent = `Summe der Loch-Pars: ${s}`;
    const parInput = el.querySelector('input[name="par"]');
    if (parInput && !parInput.dataset.touched) parInput.value = s;
  }
  function checkSi() {
    const inputs = [...grid.querySelectorAll("input.si")];
    const seen = new Map();
    inputs.forEach((i) => { const v = i.value.trim(); if (v) seen.set(v, (seen.get(v) || 0) + 1); });
    inputs.forEach((i) => i.classList.toggle("invalid", !!i.value.trim() && seen.get(i.value.trim()) > 1));
  }

  const el = h("fieldset", { class: "tee" },
    h("legend", {}, "Abschlag"),
    h("div", { class: "form-grid" },
      field("Name", h("input", { name: "name", required: true, maxlength: 40, placeholder: "z. B. Gelb", value: initial.name || "" })),
      field("Geschlecht", select("gender", GENDER, initial.gender || "m")),
      field("Löcher", holesSel),
      field("Course Rating (CR)", h("input", { name: "course_rating", required: true, inputmode: "decimal", placeholder: "z. B. 71,2" })),
      field("Slope Rating (SR)", h("input", { name: "slope_rating", required: true, inputmode: "numeric", placeholder: "55–155" })),
      field("Par gesamt", h("input", { name: "par", required: true, inputmode: "numeric", oninput: (e) => { e.target.dataset.touched = "1"; } }), parSum)),
    h("p", { class: "muted small" }, "Loch-HCP: Schwierigkeit 1 (schwerstes) bis 18 laut Scorekarte. Bei 9-Loch-Plätzen 1–9 oder die ungeraden/geraden Werte der Karte."),
    h("div", { class: "scroll-x" }, grid),
    errors);
  holesSel.addEventListener("change", buildGrid);
  buildGrid();

  function read() {
    const n = Number(holesSel.value);
    const pars = [...grid.querySelectorAll("input.par")].map((i) => Number(i.value));
    const sis = [...grid.querySelectorAll("input.si")].map((i) => Number(i.value));
    return {
      name: el.querySelector('[name="name"]').value.trim(),
      gender: el.querySelector('[name="gender"]').value,
      holes_count: n,
      course_rating: toNum(el.querySelector('[name="course_rating"]').value).toFixed(1),
      slope_rating: Math.round(toNum(el.querySelector('[name="slope_rating"]').value)),
      par: Math.round(toNum(el.querySelector('[name="par"]').value)),
      holes: pars.map((p, i) => ({ number: i + 1, par: p, stroke_index: sis[i] })),
    };
  }
  function localCheck(tee) {
    const problems = [];
    if (tee.holes.some((x) => !x.stroke_index)) problems.push("Bitte für jedes Loch den Loch-HCP eintragen.");
    if (new Set(tee.holes.map((x) => x.stroke_index)).size !== tee.holes.length) problems.push("Loch-HCP ist doppelt vergeben (rot markiert).");
    if (Number.isNaN(Number(tee.course_rating))) problems.push("Course Rating ist keine Zahl.");
    return problems;
  }
  return { el, read, localCheck, errors };
}

export async function courseNewPage() {
  const slot = h("div");
  const tees = [];
  const teeBox = h("div");
  const addTee = (init) => { const t = teeEditor(init); tees.push(t); teeBox.append(t.el); };
  addTee({ name: "Gelb", gender: "m" });

  const form = h("form", { class: "page", onsubmit: async (e) => {
    e.preventDefault();
    const data = tees.map((t) => t.read());
    const problems = tees.flatMap((t, i) => t.localCheck(data[i]).map((p) => `Abschlag ${i + 1}: ${p}`));
    if (problems.length) { showError(slot, Object.assign(new Error("Bitte Eingaben prüfen."), { errors: problems })); return; }
    try {
      const res = await api("POST", "/courses", { name: form.elements.namedItem("course_name").value, tees: data });
      toast("Platz angelegt");
      location.hash = `#/plaetze/${res.id}`;
    } catch (err) { showError(slot, err); }
  } },
  h("div", { class: "page-head" }, h("h1", {}, "Neuer Platz")),
  h("div", { class: "card" },
    slot,
    field("Name des Platzes", h("input", { name: "course_name", required: true, maxlength: 150, placeholder: "z. B. GC Saarbrücken – 18-Loch" })),
    teeBox,
    h("div", { class: "actions" },
      h("button", { type: "button", class: "ghost", onclick: () => addTee({ name: "Rot", gender: "w" }) }, "+ Weiterer Abschlag"),
      h("button", { type: "submit", class: "primary" }, "Platz speichern"))));
  return form;
}

export async function courseDetailPage(id) {
  const c = await api("GET", `/courses/${id}`);
  const slot = h("div");
  const addBox = h("div");

  const addBtn = can.edit() ? h("button", { class: "ghost", onclick: () => {
    const t = teeEditor({ name: "", gender: "w" });
    addBox.replaceChildren(h("div", { class: "card" }, slot, t.el, h("div", { class: "actions" },
      h("button", { class: "primary", onclick: async () => {
        const data = t.read();
        const problems = t.localCheck(data);
        if (problems.length) { showError(slot, Object.assign(new Error("Bitte Eingaben prüfen."), { errors: problems })); return; }
        try { await api("POST", `/courses/${id}/tees`, data); toast("Abschlag hinzugefügt"); reload(); } catch (err) { showError(slot, err); }
      } }, "Abschlag speichern"),
      h("button", { class: "ghost", onclick: () => addBox.replaceChildren() }, "Abbrechen"))));
    addBtn.blur();
  } }, "+ Abschlag hinzufügen") : null;

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("div", {}, h("a", { href: "#/plaetze", class: "back" }, "← Plätze"), h("h1", {}, c.name)), addBtn),
    addBox,
    c.tees.map((t) => h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h2", {}, `${t.name} `, badge(GENDER.find(([g]) => g === t.gender)?.[1] || t.gender)),
        h("span", { class: "muted" }, `${t.holes_count} Löcher · Par ${t.par} · CR ${num(t.course_rating)} · SR ${t.slope_rating}`)),
      h("div", { class: "scroll-x" }, h("table", { class: "holes" },
        h("tr", {}, h("th", {}, "Loch"), t.holes.map((x) => h("td", {}, x.number))),
        h("tr", {}, h("th", {}, "Par"), t.holes.map((x) => h("td", {}, x.par))),
        h("tr", {}, h("th", {}, "HCP"), t.holes.map((x) => h("td", { class: "muted" }, x.stroke_index))))))));
}
