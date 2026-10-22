// Platzsuche im Katalog von handicap.report und Übernahme als neuer Platz.
import { api, h, field, select, showError, badge } from "./lib.js";
import { setPendingImport } from "./pages-courses.js";

const GENDER = { m: "Herren", w: "Damen", d: "Divers" };
const num = (v) => String(v).replace(".", ",");

// Suchzustand bleibt erhalten, wenn man von der Vorschau zurückkommt.
const last = { q: "", bundesland: "", holes: "", withHoles: false };

export async function catalogPage() {
  let bundeslaender = [];
  const slot = h("div");
  try { bundeslaender = await api("GET", "/course-catalog/bundeslaender"); } catch (err) { showError(slot, err); }

  const q = h("input", { type: "search", name: "q", value: last.q, autocomplete: "off", maxlength: 100,
    placeholder: "Clubname oder Ort, z. B. „Saarbrücken“, „Homburg“, „Golfpark“", class: "big-search" });
  const bl = select("bundesland", [["", "Alle Bundesländer"], ...bundeslaender.map((b) => [b, b])], last.bundesland);
  const holes = select("holes", [["", "9 und 18 Loch"], ["18", "18 Loch und mehr"], ["9", "9 Loch"]], last.holes);
  const withHoles = h("input", { type: "checkbox", checked: last.withHoles });
  const meta = h("p", { class: "muted small" });
  const results = h("div");
  const detail = h("div");

  let timer;
  let seq = 0;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(run, 300); };
  [q, bl, holes, withHoles].forEach((el) => el.addEventListener(el === q ? "input" : "change", schedule));

  async function run() {
    Object.assign(last, { q: q.value, bundesland: bl.value, holes: holes.value, withHoles: withHoles.checked });
    detail.replaceChildren();
    if (q.value.trim().length < 2 && !bl.value) {
      results.replaceChildren();
      meta.textContent = "Mindestens 2 Zeichen eingeben oder ein Bundesland wählen.";
      return;
    }
    const params = new URLSearchParams({ q: q.value.trim() });
    if (bl.value) params.set("bundesland", bl.value);
    if (holes.value) params.set("holes", holes.value);
    if (withHoles.checked) params.set("with_holes", "true");
    const my = ++seq;
    meta.textContent = "Suche …";
    try {
      const res = await api("GET", `/course-catalog/search?${params}`);
      if (my !== seq) return;
      slot.replaceChildren();
      renderResults(res);
    } catch (err) {
      if (my === seq) { meta.textContent = ""; results.replaceChildren(); showError(slot, err); }
    }
  }

  function renderResults(res) {
    meta.replaceChildren(
      `${res.total} Treffer${res.total > res.results.length ? ` – die ersten ${res.results.length} werden angezeigt, bitte Suche verfeinern` : ""}`,
      res.stand ? ` · Katalogstand ${res.stand}` : "",
      res.stale ? h("span", { class: "badge warn" }, "Quelle gerade nicht erreichbar – zwischengespeicherter Stand") : "");
    if (res.hint) meta.append(h("div", { class: "alert warn small" }, res.hint));
    if (!res.results.length) { results.replaceChildren(h("div", { class: "card empty" }, h("p", {}, "Kein Platz gefunden."))); return; }
    results.replaceChildren(h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Club"), h("th", {}, "Ort"), h("th", {}, "Bundesland"),
        h("th", { class: "num" }, "Löcher"), h("th", {}, "Lochdaten"), h("th", {}))),
      h("tbody", {}, res.results.map((r) => h("tr", { class: "clickable", onclick: () => showDetail(r.id, res.stand) },
        h("td", {}, h("strong", {}, r.club)),
        h("td", {}, r.ort),
        h("td", {}, r.bundesland),
        h("td", { class: "num" }, r.holeCount),
        h("td", {}, r.hasHoles ? badge("Par + Loch-HCP", "ok") : h("span", { class: "muted small" }, "nur Abschläge")),
        h("td", { class: "right" }, r.imported_course_id
          ? h("a", { href: `#/plaetze/${r.imported_course_id}`, onclick: (e) => e.stopPropagation() }, "bereits übernommen")
          : h("button", { class: "small" }, "Ansehen"))))))));
  }

  async function showDetail(id, stand) {
    detail.replaceChildren(h("p", { class: "muted" }, "Lädt Platzdaten …"));
    let d;
    try { d = await api("GET", `/course-catalog/courses/${encodeURIComponent(id)}`); } catch (err) { showError(detail, err); return; }
    const checks = d.tees.map(() => h("input", { type: "checkbox", checked: true }));
    const errSlot = h("div");
    detail.replaceChildren(h("div", { class: "card catalog-detail" },
      h("div", { class: "card-head" },
        h("div", {}, h("h2", {}, d.club),
          h("span", { class: "muted" }, [d.ort, d.bundesland, d.course_name].filter(Boolean).join(" · "))),
        d.imported_course_id ? h("a", { class: "button", href: `#/plaetze/${d.imported_course_id}` }, "Übernommenen Platz öffnen") : null),
      d.stale ? h("div", { class: "alert warn small" }, "Quelle gerade nicht erreichbar – zwischengespeicherte Daten.") : null,
      d.warnings.map((w) => h("div", { class: "alert warn small" }, w)),
      errSlot,
      d.tees.length ? h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Übernehmen"), h("th", {}, "Abschlag"), h("th", {}, "Geschlecht"),
          h("th", { class: "num" }, "CR"), h("th", { class: "num" }, "Slope"), h("th", { class: "num" }, "Par"), h("th", {}, "Hinweise"))),
        h("tbody", {}, d.tees.map((t, i) => h("tr", {},
          h("td", {}, h("label", { class: "inline" }, checks[i])),
          h("td", {}, t.name),
          h("td", {}, GENDER[t.gender] || t.gender),
          h("td", { class: "num" }, num(t.course_rating)),
          h("td", { class: "num" }, t.slope_rating),
          h("td", { class: "num" }, t.par),
          h("td", { class: "small" }, t.warnings.length ? t.warnings.map((w) => h("div", { class: "warn-text" }, w)) : h("span", { class: "muted" }, "–")))))) : null,
      d.holes ? h("div", { class: "scroll-x" }, h("table", { class: "holes" },
        h("tr", {}, h("th", {}, "Loch"), d.holes.map((x) => h("td", {}, x.number))),
        h("tr", {}, h("th", {}, "Par"), d.holes.map((x) => h("td", {}, x.par))),
        h("tr", {}, h("th", {}, "HCP"), d.holes.map((x) => h("td", { class: "muted" }, x.stroke_index ?? "–"))))) : null,
      h("div", { class: "actions" },
        h("button", { class: "primary", disabled: !!d.imported_course_id || !d.tees.length, onclick: () => {
          const chosen = d.tees.filter((_, i) => checks[i].checked);
          if (!chosen.length) { showError(errSlot, new Error("Bitte mindestens einen Abschlag auswählen.")); return; }
          setPendingImport({ ...d, tees: chosen, stand });
          location.hash = "#/plaetze/neu";
        } }, "Ausgewählte Abschläge übernehmen …"),
        h("span", { class: "muted small" }, "Im nächsten Schritt prüfst und ergänzt du die Werte, bevor gespeichert wird."))));
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const page = h("section", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("a", { href: "#/plaetze", class: "back" }, "← Plätze"), h("h1", {}, "Platz im Katalog suchen")),
      h("a", { class: "button", href: "#/plaetze/neu" }, "Manuell anlegen")),
    h("div", { class: "card" },
      slot,
      q,
      h("div", { class: "inline filters" }, bl, holes, h("label", { class: "inline small" }, withHoles, "nur mit Lochdaten")),
      meta,
      h("p", { class: "muted small" }, "Quelle: ",
        h("a", { href: "https://handicap.report/", target: "_blank", rel: "noopener noreferrer" }, "handicap.report"),
        ". Der Katalog enthält keine Postleitzahlen – Suche nach Clubname oder Ort.")),
    results,
    detail);
  if (last.q.trim().length >= 2 || last.bundesland) run();
  setTimeout(() => q.focus(), 0);
  return page;
}
