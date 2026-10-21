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
function genderLabel(g) { return GENDER.find(([x]) => x === g)?.[1] || g; }

/**
 * Eingabebereich für einen Abschlag.
 * initial: vorhandener Abschlag (bearbeiten) oder {name, gender} (neu)
 * sources: bestehende Abschläge, von denen Par/Loch-HCP übernommen werden können
 */
function teeEditor(initial = {}, sources = []) {
  const hasHoles = Array.isArray(initial.holes) && initial.holes.length;
  const holesSel = select("holes_count", [["18", "18 Löcher"], ["9", "9 Löcher"]], initial.holes_count || sources[0]?.holes_count || 18);
  const parSum = h("output", { class: "sum" });
  const grid = h("div", { class: "hole-grid" });
  const errors = h("div");
  const parInput = h("input", { name: "par", required: true, inputmode: "numeric", value: initial.par ?? "",
    oninput: () => { parInput.dataset.touched = "1"; } });
  if (hasHoles) parInput.dataset.touched = "1";

  // Übernahme von einem bestehenden Abschlag (nur beim Neuanlegen)
  const srcSel = sources.length ? select("source", [["", "— nichts übernehmen —"], ...sources.map((t) => [t.id, `${t.name} (${genderLabel(t.gender)}, ${t.holes_count} L)`])], sources[0].id, { onchange: applySource }) : null;
  const siCheck = sources.length ? h("input", { type: "checkbox", onchange: applySource }) : null;
  const sourceRow = sources.length ? h("div", { class: "source-row" },
    field("Par übernehmen von", srcSel),
    h("label", { class: "inline small" }, siCheck, "Loch-HCP auch übernehmen"),
    h("small", { class: "muted" }, "Damen-Abschläge haben oft eine andere Loch-HCP-Reihenfolge.")) : null;

  function selectedSource() {
    return srcSel && srcSel.value ? sources.find((t) => String(t.id) === srcSel.value) : null;
  }
  function applySource() {
    const src = selectedSource();
    if (src && src.holes_count !== Number(holesSel.value)) holesSel.value = String(src.holes_count);
    buildGrid();
  }

  function buildGrid() {
    const n = Number(holesSel.value);
    const src = selectedSource();
    let pars; let sis;
    if (hasHoles && initial.holes.length === n) {
      pars = initial.holes.map((x) => x.par);
      sis = initial.holes.map((x) => x.stroke_index);
    } else if (src && src.holes.length === n) {
      pars = src.holes.map((x) => x.par);
      sis = siCheck.checked ? src.holes.map((x) => x.stroke_index) : Array(n).fill("");
    } else {
      pars = DEFAULT_PAR_18.slice(0, n);
      sis = Array(n).fill("");
    }
    grid.replaceChildren(
      h("div", { class: "hg-label" }, "Loch"), ...pars.map((_, i) => h("div", { class: "hg-head" }, String(i + 1))),
      h("div", { class: "hg-label" }, "Par"), ...pars.map((p, i) => h("input", { class: "par", "aria-label": `Par Loch ${i + 1}`, inputmode: "numeric", value: p, maxlength: 1, oninput: updateSum })),
      h("div", { class: "hg-label" }, "HCP"), ...sis.map((v, i) => h("input", { class: "si", "aria-label": `Loch-HCP Loch ${i + 1}`, inputmode: "numeric", maxlength: 2, placeholder: "–", value: v, oninput: checkSi })));
    grid.style.setProperty("--cols", n);
    if (!hasHoles && src) parInput.dataset.touched = "";
    updateSum();
    checkSi();
  }
  function updateSum() {
    const s = [...grid.querySelectorAll("input.par")].reduce((a, i) => a + (Number(i.value) || 0), 0);
    parSum.textContent = `Summe der Loch-Pars: ${s}`;
    if (!parInput.dataset.touched) parInput.value = s;
  }
  function checkSi() {
    const inputs = [...grid.querySelectorAll("input.si")];
    const seen = new Map();
    inputs.forEach((i) => { const v = i.value.trim(); if (v) seen.set(v, (seen.get(v) || 0) + 1); });
    inputs.forEach((i) => i.classList.toggle("invalid", !!i.value.trim() && seen.get(i.value.trim()) > 1));
  }

  const el = h("fieldset", { class: "tee" },
    h("legend", {}, hasHoles ? "Abschlag bearbeiten" : "Abschlag"),
    sourceRow,
    h("div", { class: "form-grid" },
      field("Name", h("input", { name: "name", required: true, maxlength: 40, placeholder: "z. B. Gelb", value: initial.name || "" })),
      field("Geschlecht", select("gender", GENDER, initial.gender || "m")),
      field("Löcher", holesSel),
      field("Course Rating (CR)", h("input", { name: "course_rating", required: true, inputmode: "decimal", placeholder: "z. B. 71,2", value: initial.course_rating !== undefined ? num(initial.course_rating) : "" })),
      field("Slope Rating (SR)", h("input", { name: "slope_rating", required: true, inputmode: "numeric", placeholder: "55–155", value: initial.slope_rating ?? "" })),
      field("Par gesamt", parInput, parSum)),
    h("p", { class: "muted small" }, "Loch-HCP: Schwierigkeit 1 (schwerstes) bis 18 laut Scorekarte. Bei 9-Loch-Plätzen 1–9 oder die ungeraden/geraden Werte der Karte."),
    h("div", { class: "scroll-x" }, grid),
    errors);
  holesSel.addEventListener("change", buildGrid);
  buildGrid();

  function read() {
    const pars = [...grid.querySelectorAll("input.par")].map((i) => Number(i.value));
    const sis = [...grid.querySelectorAll("input.si")].map((i) => Number(i.value));
    return {
      name: el.querySelector('[name="name"]').value.trim(),
      gender: el.querySelector('[name="gender"]').value,
      holes_count: Number(holesSel.value),
      course_rating: toNum(el.querySelector('[name="course_rating"]').value).toFixed(1),
      slope_rating: Math.round(toNum(el.querySelector('[name="slope_rating"]').value)),
      par: Math.round(toNum(parInput.value)),
      holes: pars.map((p, i) => ({ number: i + 1, par: p, stroke_index: sis[i] })),
    };
  }
  function localCheck(tee) {
    const problems = [];
    if (tee.holes.some((x) => !x.stroke_index)) problems.push("Bitte für jedes Loch den Loch-HCP eintragen.");
    if (new Set(tee.holes.map((x) => x.stroke_index)).size !== tee.holes.length) problems.push("Loch-HCP ist doppelt vergeben (rot markiert).");
    if (Number.isNaN(Number(tee.course_rating))) problems.push("Course Rating ist keine Zahl.");
    if (Number.isNaN(tee.slope_rating)) problems.push("Slope Rating ist keine Zahl.");
    return problems;
  }
  return { el, read, localCheck, errors };
}

function problemsError(problems) {
  return Object.assign(new Error("Bitte Eingaben prüfen."), { errors: problems });
}

export async function courseNewPage() {
  const slot = h("div");
  const tees = [];
  const teeBox = h("div");
  const addTee = (init) => {
    // Weitere Abschläge übernehmen die Pars des ersten Abschlags.
    const t = teeEditor(init);
    if (tees.length) {
      const first = tees[0].read();
      t.el.querySelector('[name="holes_count"]').value = String(first.holes_count);
      t.el.querySelector('[name="holes_count"]').dispatchEvent(new Event("change"));
      t.el.querySelectorAll("input.par").forEach((inp, i) => { inp.value = first.holes[i]?.par ?? inp.value; });
      t.el.querySelector("input.par")?.dispatchEvent(new Event("input"));
    }
    tees.push(t);
    teeBox.append(t.el);
  };
  addTee({ name: "Gelb", gender: "m" });

  const form = h("form", { class: "page", onsubmit: async (e) => {
    e.preventDefault();
    const data = tees.map((t) => t.read());
    const problems = tees.flatMap((t, i) => t.localCheck(data[i]).map((p) => `Abschlag ${i + 1}: ${p}`));
    if (problems.length) { showError(slot, problemsError(problems)); return; }
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
  const editable = can.edit();

  // --- Platz umbenennen / löschen
  const title = h("h1", {}, c.name);
  const renameForm = h("form", { class: "inline-form", hidden: true, onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api("PATCH", `/courses/${id}`, { name: renameForm.elements.namedItem("cname").value });
      toast("Platz umbenannt");
      reload();
    } catch (err) { showError(slot, err); }
  } },
  field("Name des Platzes", h("input", { name: "cname", required: true, maxlength: 150, value: c.name })),
  h("button", { type: "submit", class: "primary" }, "Speichern"),
  h("button", { type: "button", class: "ghost", onclick: () => { renameForm.hidden = true; title.hidden = false; } }, "Abbrechen"));

  const deleteCourseBtn = h("button", {
    class: "ghost danger", disabled: c.tournaments > 0,
    title: c.tournaments ? `Auf dem Platz gibt es ${c.tournaments} Turnier(e).` : "Platz mit allen Abschlägen löschen",
    onclick: async () => {
      if (!confirm(`Platz „${c.name}“ mit allen Abschlägen löschen?`)) return;
      try { await api("DELETE", `/courses/${id}`); toast("Platz gelöscht"); location.hash = "#/plaetze"; } catch (err) { showError(slot, err); }
    },
  }, "Platz löschen");

  const headActions = editable ? h("div", { class: "inline" },
    h("button", { class: "ghost", onclick: () => { renameForm.hidden = false; title.hidden = true; renameForm.querySelector("input").select(); } }, "Umbenennen"),
    h("button", { class: "ghost", onclick: () => openEditor(null) }, "+ Abschlag hinzufügen"),
    deleteCourseBtn) : null;

  // --- Abschlag hinzufügen / bearbeiten (gleicher Editor)
  function openEditor(tee, cardEl) {
    const t = tee ? teeEditor(tee) : teeEditor({ name: "", gender: "w" }, c.tees);
    const errSlot = h("div");
    const info = tee && tee.entries
      ? h("div", { class: "alert warn" }, `Dieser Abschlag wird in ${tee.entries} Meldung(en) verwendet. Änderungen an CR, SR, Par oder Loch-HCP berechnen deren Spielvorgaben neu und ändern die Ergebnisse.`)
      : null;
    const box = h("div", { class: "card" }, errSlot, info, t.el, h("div", { class: "actions" },
      h("button", { class: "primary", onclick: () => save(false) }, tee ? "Änderungen speichern" : "Abschlag speichern"),
      h("button", { class: "ghost", onclick: () => (tee ? reload() : addBox.replaceChildren()) }, "Abbrechen")));

    async function save(force) {
      const data = t.read();
      const problems = t.localCheck(data);
      if (problems.length) { showError(errSlot, problemsError(problems)); return; }
      try {
        if (tee) {
          const res = await api("PUT", `/tees/${tee.id}${force ? "?force=true" : ""}`, data);
          toast(res.recalculated ? `Abschlag gespeichert, ${res.recalculated} Spielvorgabe(n) neu berechnet` : "Abschlag gespeichert");
        } else {
          await api("POST", `/courses/${id}/tees`, data);
          toast("Abschlag hinzugefügt");
        }
        reload();
      } catch (err) {
        if (err.code === "tee_in_use" && confirm(`${err.message}\n\nTrotzdem speichern?`)) { save(true); return; }
        showError(errSlot, err);
      }
    }

    if (tee) cardEl.replaceWith(box);
    else addBox.replaceChildren(box);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    box.querySelector('input[name="name"]').focus();
  }

  function teeCard(t) {
    const card = h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("div", {},
          h("h2", {}, `${t.name} `, badge(genderLabel(t.gender))),
          h("span", { class: "muted" }, `${t.holes_count} Löcher · Par ${t.par} · CR ${num(t.course_rating)} · SR ${t.slope_rating}`,
            t.entries ? ` · in ${t.entries} Meldung(en)` : "")),
        editable ? h("div", { class: "inline" },
          h("button", { class: "small", onclick: () => openEditor(t, card) }, "Bearbeiten"),
          h("button", {
            class: "small ghost danger", disabled: t.entries > 0,
            title: t.entries ? "Wird in Meldungen verwendet" : "Abschlag löschen",
            onclick: async () => {
              if (!confirm(`Abschlag „${t.name}“ löschen?`)) return;
              try { await api("DELETE", `/tees/${t.id}`); toast("Abschlag gelöscht"); reload(); } catch (err) { showError(slot, err); }
            },
          }, "Löschen")) : null),
      h("div", { class: "scroll-x" }, h("table", { class: "holes" },
        h("tr", {}, h("th", {}, "Loch"), t.holes.map((x) => h("td", {}, x.number))),
        h("tr", {}, h("th", {}, "Par"), t.holes.map((x) => h("td", {}, x.par))),
        h("tr", {}, h("th", {}, "HCP"), t.holes.map((x) => h("td", { class: "muted" }, x.stroke_index))))));
    return card;
  }

  return h("section", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("a", { href: "#/plaetze", class: "back" }, "← Plätze"), title, renameForm),
      headActions),
    slot,
    addBox,
    c.tees.length ? c.tees.map(teeCard) : h("div", { class: "card empty" }, h("p", { class: "muted" }, "Dieser Platz hat keinen Abschlag.")));
}
