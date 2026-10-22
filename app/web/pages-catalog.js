// Platzsuche in externen Katalogen (handicap.report, golfcourseapi.com) und Übernahme als neuer Platz.
import { api, h, field, select, showError, badge } from "./lib.js";
import { setPendingImport } from "./pages-courses.js";

const GENDER = { m: "Herren", w: "Damen", d: "Divers" };
const num = (v) => (v === null || v === undefined ? "–" : String(v).replace(".", ","));
const SOURCE_LABEL = { "handicap.report": "handicap.report", golfcourseapi: "golfcourseapi.com" };

// Zustand bleibt erhalten, wenn man von der Vorschau zurückkommt.
const last = { source: "handicap-report", q: "", bundesland: "", holes: "", withHoles: false, gq: "" };

export async function catalogPage() {
  const slot = h("div");
  let sources = [];
  try { sources = await api("GET", "/course-catalog/sources"); } catch (err) { showError(slot, err); }
  const enabled = sources.filter((s) => s.enabled);
  if (!enabled.find((s) => s.id === last.source) && enabled.length) last.source = enabled[0].id;

  const body = h("div");
  const results = h("div");
  const detail = h("div");

  const tabs = h("div", { class: "tabs", role: "tablist" }, enabled.map((s) => h("button", {
    type: "button", role: "tab", class: s.id === last.source ? "tab active" : "tab",
    "aria-selected": s.id === last.source ? "true" : "false",
    onclick: () => { last.source = s.id; render(); },
  }, s.name)));

  function render() {
    tabs.querySelectorAll(".tab").forEach((b, i) => {
      const on = enabled[i].id === last.source;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    results.replaceChildren();
    detail.replaceChildren();
    const src = enabled.find((s) => s.id === last.source);
    if (!src) { body.replaceChildren(h("p", { class: "muted" }, "Kein Platzkatalog eingerichtet.")); return; }
    body.replaceChildren(src.id === "golfcourseapi" ? gcaSearch(src) : hrSearch(src));
  }

  // ----------------------------------------------------------- handicap.report
  function hrSearch(src) {
    const meta = h("p", { class: "muted small" });
    const q = h("input", { type: "search", name: "q", value: last.q, autocomplete: "off", maxlength: 100,
      placeholder: "Clubname oder Ort, z. B. „Saarbrücken“, „Homburg“, „Golfpark“", class: "big-search" });
    const bl = select("bundesland", [["", "Alle Bundesländer"]], last.bundesland);
    const holes = select("holes", [["", "9 und 18 Loch"], ["18", "18 Loch und mehr"], ["9", "9 Loch"]], last.holes);
    const withHoles = h("input", { type: "checkbox", checked: last.withHoles });
    api("GET", "/course-catalog/handicap-report/bundeslaender").then((list) => {
      bl.append(...list.map((b) => h("option", { value: b, selected: b === last.bundesland }, b)));
    }).catch(() => {});

    let timer; let seq = 0;
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
        const res = await api("GET", `/course-catalog/handicap-report/search?${params}`);
        if (my !== seq) return;
        slot.replaceChildren();
        meta.replaceChildren(
          `${res.total} Treffer${res.total > res.results.length ? ` – die ersten ${res.results.length} werden angezeigt` : ""}`,
          res.stand ? ` · Katalogstand ${res.stand}` : "",
          res.stale ? h("span", { class: "badge warn" }, "Quelle nicht erreichbar – zwischengespeicherter Stand") : "");
        if (res.hint) meta.append(h("div", { class: "alert warn small" }, res.hint));
        renderTable(res.results, [
          ["Club", (r) => h("strong", {}, r.club)], ["Ort", (r) => r.ort], ["Bundesland", (r) => r.bundesland],
          ["Löcher", (r) => r.holeCount, "num"],
          ["Lochdaten", (r) => (r.hasHoles ? badge("Par + Loch-HCP", "ok") : h("span", { class: "muted small" }, "nur Abschläge"))],
        ], (r) => showDetail("handicap-report", r.id, res.stand));
      } catch (err) {
        if (my === seq) { meta.textContent = ""; results.replaceChildren(); showError(slot, err); }
      }
    }
    if (last.q.trim().length >= 2 || last.bundesland) run();
    setTimeout(() => q.focus(), 0);
    return h("div", {},
      h("p", { class: "muted small" }, src.description),
      q,
      h("div", { class: "inline filters" }, bl, holes, h("label", { class: "inline small" }, withHoles, "nur mit Lochdaten")),
      meta,
      h("p", { class: "muted small" }, "Quelle: ",
        h("a", { href: "https://handicap.report/", target: "_blank", rel: "noopener noreferrer" }, "handicap.report"),
        ". Keine Postleitzahlen – Suche nach Clubname oder Ort."));
  }

  // ---------------------------------------------------------- golfcourseapi.com
  function gcaSearch(src) {
    const usageEl = h("span", { class: "usage" });
    const meta = h("p", { class: "muted small" });
    const showUsage = (u) => {
      if (!u) return;
      usageEl.replaceChildren(badge(`Heute noch ${u.remaining} von ${u.limit} Anfragen`, u.remaining > 5 ? "info" : "warn"));
    };
    showUsage(src.usage);
    if (!src.configured) {
      return h("div", { class: "alert warn" },
        "Für golfcourseapi.com ist auf dem Server noch kein API-Schlüssel eingerichtet. ",
        "Ein Administrator trägt ihn in /etc/golf-auswertung/golf.env als GOLF_GOLFCOURSEAPI_KEY ein und startet den Dienst neu.");
    }
    const q = h("input", { type: "search", name: "gq", value: last.gq, autocomplete: "off", maxlength: 100, minlength: 3,
      placeholder: "Club- oder Platzname, z. B. „Saarbrücken“, „Golfclub Homburg“", class: "big-search", required: true });
    const btn = h("button", { type: "submit", class: "primary" }, "Suchen");
    const form = h("form", { class: "search-row", onsubmit: async (e) => {
      e.preventDefault();
      last.gq = q.value;
      detail.replaceChildren();
      btn.disabled = true;
      meta.textContent = "Suche …";
      try {
        const res = await api("GET", `/course-catalog/golfcourseapi/search?${new URLSearchParams({ q: q.value.trim() })}`);
        slot.replaceChildren();
        showUsage(res.usage);
        meta.replaceChildren(`${res.total} Treffer`,
          res.cached ? " · aus dem Zwischenspeicher (kein Kontingent verbraucht)" : "",
          res.stale ? h("span", { class: "badge warn" }, "veralteter Stand") : "");
        renderTable(res.results, [
          ["Club / Platz", (r) => [h("strong", {}, r.club), r.course_name && r.course_name !== r.club ? h("div", { class: "muted small" }, r.course_name) : null]],
          ["Ort", (r) => [r.city || "", r.state ? h("div", { class: "muted small" }, r.state) : null]],
          ["Land", (r) => r.country || ""],
          ["Abschläge", (r) => `${r.tees_male} H · ${r.tees_female} D`],
        ], (r) => showDetail("golfcourseapi", r.id, null, showUsage));
      } catch (err) {
        meta.textContent = "";
        results.replaceChildren();
        showError(slot, err);
      } finally { btn.disabled = false; }
    } }, q, btn);
    setTimeout(() => q.focus(), 0);
    return h("div", {},
      h("div", { class: "inline space-between" }, h("p", { class: "muted small" }, src.description), usageEl),
      form,
      meta,
      h("p", { class: "muted small" }, "Jede neue Suche und jedes erstmalige Öffnen eines Platzes kostet eine Anfrage. ",
        "Bereits abgerufene Ergebnisse werden gespeichert (Suchen 7 Tage, Plätze 30 Tage) und kosten nichts. Quelle: ",
        h("a", { href: "https://golfcourseapi.com/", target: "_blank", rel: "noopener noreferrer" }, "golfcourseapi.com"), "."));
  }

  // ------------------------------------------------------------- gemeinsam
  function renderTable(rows, cols, onPick) {
    if (!rows.length) { results.replaceChildren(h("div", { class: "card empty" }, h("p", {}, "Kein Platz gefunden."))); return; }
    results.replaceChildren(h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {}, cols.map(([label, , cls]) => h("th", { class: cls || "" }, label)), h("th"))),
      h("tbody", {}, rows.map((r) => h("tr", { class: "clickable", onclick: () => onPick(r) },
        cols.map(([, fn, cls]) => h("td", { class: cls || "" }, fn(r))),
        h("td", { class: "right" }, r.imported_course_id
          ? h("a", { href: `#/plaetze/${r.imported_course_id}`, onclick: (e) => e.stopPropagation() }, "bereits übernommen")
          : h("button", { class: "small" }, "Ansehen"))))))));
  }

  function holesInfo(t) {
    if (!t.holes) return h("span", { class: "muted small" }, "fehlen");
    const si = t.holes.every((x) => x.stroke_index);
    const par = t.holes.every((x) => x.par);
    if (si && par) return badge("Par + Loch-HCP", "ok");
    return badge(par ? "nur Par" : "unvollständig", "warn");
  }

  function holesTable(holes, label) {
    return h("div", { class: "scroll-x" }, label ? h("div", { class: "small strong" }, label) : null, h("table", { class: "holes" },
      h("tr", {}, h("th", {}, "Loch"), holes.map((x) => h("td", {}, x.number))),
      h("tr", {}, h("th", {}, "Par"), holes.map((x) => h("td", {}, x.par ?? "–"))),
      h("tr", {}, h("th", {}, "HCP"), holes.map((x) => h("td", { class: "muted" }, x.stroke_index ?? "–")))));
  }

  async function showDetail(source, id, stand, onUsage) {
    detail.replaceChildren(h("p", { class: "muted" }, "Lädt Platzdaten …"));
    let d;
    try {
      d = await api("GET", `/course-catalog/${source}/courses/${encodeURIComponent(id)}`);
    } catch (err) { detail.replaceChildren(); showError(detail, err); return; }
    if (onUsage) onUsage(d.usage);
    const checks = d.tees.map(() => h("input", { type: "checkbox", checked: true }));
    const errSlot = h("div");
    const perTeeHoles = d.tees.filter((t) => t.holes);
    detail.replaceChildren(h("div", { class: "card catalog-detail" },
      h("div", { class: "card-head" },
        h("div", {}, h("h2", {}, d.club),
          h("span", { class: "muted" }, [d.course_name !== d.club ? d.course_name : null, d.address || d.ort, d.address ? null : d.bundesland,
            d.country && !(d.address || "").includes(d.country) ? d.country : null].filter(Boolean).join(" · "))),
        d.imported_course_id ? h("a", { class: "button", href: `#/plaetze/${d.imported_course_id}` }, "Übernommenen Platz öffnen") : null),
      d.stale ? h("div", { class: "alert warn small" }, "Quelle gerade nicht erreichbar – zwischengespeicherte Daten.") : null,
      d.notice ? h("div", { class: "alert warn small" }, d.notice) : null,
      d.warnings.map((w) => h("div", { class: "alert warn small" }, w)),
      errSlot,
      d.tees.length ? h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Übernehmen"), h("th", {}, "Abschlag"), h("th", {}, "Geschlecht"),
          h("th", { class: "num" }, "Löcher"), h("th", { class: "num" }, "CR"), h("th", { class: "num" }, "Slope"),
          h("th", { class: "num" }, "Par"), h("th", {}, "Lochdaten"), h("th", {}, "Hinweise"))),
        h("tbody", {}, d.tees.map((t, i) => h("tr", {},
          h("td", {}, h("label", { class: "inline" }, checks[i])),
          h("td", {}, t.name),
          h("td", {}, GENDER[t.gender] || t.gender),
          h("td", { class: "num" }, t.holes_count),
          h("td", { class: "num" }, num(t.course_rating)),
          h("td", { class: "num" }, t.slope_rating ?? "–"),
          h("td", { class: "num" }, t.par ?? "–"),
          h("td", {}, d.holes ? badge("je Platz", "ok") : holesInfo(t)),
          h("td", { class: "small" }, t.warnings.length ? t.warnings.map((w) => h("div", { class: "warn-text" }, w)) : h("span", { class: "muted" }, "–")))))) : null,
      d.holes ? holesTable(d.holes) : null,
      !d.holes && perTeeHoles.length ? h("details", {}, h("summary", {}, "Lochdaten je Abschlag anzeigen"),
        perTeeHoles.map((t) => holesTable(t.holes, `${t.name} (${GENDER[t.gender] || t.gender})`))) : null,
      h("div", { class: "actions" },
        h("button", { class: "primary", disabled: !!d.imported_course_id || !d.tees.length, onclick: () => {
          const chosen = d.tees.filter((_, i) => checks[i].checked);
          if (!chosen.length) { showError(errSlot, new Error("Bitte mindestens einen Abschlag auswählen.")); return; }
          const holeCounts = new Set(chosen.map((t) => t.holes_count));
          if (holeCounts.size > 1) {
            showError(errSlot, new Error("Bitte nur Abschläge mit gleicher Lochzahl gemeinsam übernehmen (9 und 18 Loch als getrennte Plätze)."));
            return;
          }
          setPendingImport({ ...d, tees: chosen, stand, source_label: SOURCE_LABEL[d.source] || d.source });
          location.hash = "#/plaetze/neu";
        } }, "Ausgewählte Abschläge übernehmen …"),
        h("span", { class: "muted small" }, "Im nächsten Schritt prüfst und ergänzt du die Werte, bevor gespeichert wird."))));
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const page = h("section", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("a", { href: "#/plaetze", class: "back" }, "← Plätze"), h("h1", {}, "Platz im Katalog suchen")),
      h("a", { class: "button", href: "#/plaetze/neu" }, "Manuell anlegen")),
    h("div", { class: "card" }, enabled.length > 1 ? tabs : null, slot, body),
    results,
    detail);
  render();
  return page;
}
