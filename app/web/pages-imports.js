// Import von Scorekarten (CSV, PDF, Foto): hochladen, prüfen, zuordnen, übernehmen.
import { api, h, field, select, showError, toast, fmtDate, badge, nameKey, sha256Hex } from "./lib.js";
import { reload } from "./app.js";

const KIND = { csv: "CSV", pdf: "PDF", photo: "Foto" };
const IMPORT_STATUS = {
  needs_review: ["Zu prüfen", "info"], failed: ["Nicht lesbar", "warn"],
  applied: ["Übernommen", "ok"], rejected: ["Verworfen", ""],
};
const MAX_MB = 15;

export async function importsPage() {
  const list = await api("GET", "/imports");
  const slot = h("div");
  const progress = h("p", { class: "muted", hidden: true });

  async function upload(file) {
    slot.replaceChildren();
    if (file.size > MAX_MB * 1024 * 1024) { showError(slot, new Error(`Datei ist größer als ${MAX_MB} MB.`)); return; }
    progress.hidden = false;
    progress.textContent = `„${file.name}“ wird hochgeladen und gelesen …`;
    try {
      const buf = await file.arrayBuffer();
      const headers = { "X-Filename": encodeURIComponent(file.name).slice(0, 250) };
      const hash = await sha256Hex(buf);
      if (hash) headers["X-Content-SHA256"] = hash; // erkennt Beschädigung auf dem Weg
      const res = await api("POST", "/imports", new Blob([buf]), { headers });
      toast(res.duplicate ? "Diese Datei wurde schon einmal hochgeladen" : "Datei gelesen");
      location.hash = `#/importe/${res.id}`;
    } catch (err) { showError(slot, err); } finally { progress.hidden = true; }
  }

  const fileInput = h("input", { type: "file", accept: ".csv,.txt,.pdf,image/jpeg,image/png", hidden: true,
    onchange: () => fileInput.files[0] && upload(fileInput.files[0]) });
  const drop = h("div", { class: "dropzone", tabindex: 0, role: "button",
    onclick: () => fileInput.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } },
    ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); },
    ondragleave: () => drop.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) upload(f); } },
  h("strong", {}, "Datei hierher ziehen oder klicken"),
  h("span", { class: "muted" }, `CSV, PDF oder Foto (JPEG/PNG), max. ${MAX_MB} MB`),
  fileInput);

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Importe")),
    h("div", { class: "card" }, slot, drop, progress,
      h("details", { class: "small" }, h("summary", {}, "Welches CSV-Format?"),
        h("p", {}, "Kopfzeile mit „Name“ (optional „DGV“) und Lochspalten „1“ … „18“ oder „Loch 1“. Trennzeichen ; , oder Tab. „-“, „x“ oder leer = gestrichen."),
        h("pre", {}, "Name;DGV;1;2;3;4;5;6;7;8;9\nMax Muster;123456;5;4;6;3;5;-;4;5;6"),
        h("p", { class: "muted" }, "Fotos werden per Texterkennung gelesen. Das klappt nur bei gut lesbaren, gerade fotografierten Karten – jede Zeile vor der Übernahme prüfen."))),
    list.length ? h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Datum"), h("th", {}, "Datei"), h("th", {}, "Art"), h("th", { class: "num" }, "Karten"), h("th", {}, "Status"))),
      h("tbody", {}, list.map((i) => h("tr", { class: "clickable", onclick: () => { location.hash = `#/importe/${i.id}`; } },
        h("td", {}, fmtDate(i.created_at)),
        h("td", {}, h("a", { href: `#/importe/${i.id}` }, safeName(i.original_name))),
        h("td", {}, KIND[i.kind]),
        h("td", { class: "num" }, i.cards),
        h("td", {}, badge(...IMPORT_STATUS[i.status]))))))) : null);
}

function safeName(n) {
  try { return decodeURIComponent(n); } catch { return n; }
}

export async function importDetailPage(id) {
  const [imp, tournaments] = await Promise.all([api("GET", `/imports/${id}`), api("GET", "/tournaments")]);
  const slot = h("div");
  const cards = imp.parsed?.cards || [];

  // Vorschau der Originaldatei
  const previewBox = h("div", { class: "file-preview" });
  if (imp.kind === "photo") {
    previewBox.append(h("img", { src: `/imports/${id}/file`, alt: "Hochgeladene Scorekarte" }));
  } else if (imp.kind === "pdf") {
    // Browser zeigen PDFs in sandbox-iframes oft nicht an; daher normales iframe
    // (der Server liefert die Datei mit eigener, restriktiver CSP) plus Link.
    previewBox.append(
      h("iframe", { src: `/imports/${id}/file`, title: "PDF-Vorschau" }),
      h("a", { href: `/imports/${id}/file`, target: "_blank", rel: "noopener" }, "PDF in neuem Tab öffnen"));
  } else {
    const pre = h("pre", {}, "Lädt …");
    previewBox.append(pre);
    api("GET", `/imports/${id}/file`, undefined, { raw: true })
      .then((r) => r.text()).then((t) => { pre.textContent = t.slice(0, 20000); })
      .catch(() => { pre.textContent = "Vorschau nicht verfügbar."; });
  }

  const header = h("div", { class: "page-head" },
    h("div", {}, h("a", { href: "#/importe", class: "back" }, "← Importe"),
      h("h1", {}, safeName(imp.original_name)),
      h("p", {}, badge(...IMPORT_STATUS[imp.status]), " ", h("span", { class: "muted" }, `${KIND[imp.kind]} · ${cards.length} Karte(n) erkannt`))));

  if (imp.status === "failed") {
    return h("section", { class: "page" }, header,
      h("div", { class: "alert error" }, imp.error || "Die Datei konnte nicht gelesen werden."),
      h("div", { class: "card" }, previewBox));
  }

  const readOnly = imp.status !== "needs_review";
  const tSel = select("tournament", [["", "— Turnier wählen —"], ...tournaments.map((t) => [t.id, `${fmtDate(t.played_on)} · ${t.name}`])], "", { disabled: readOnly });
  const table = h("div");
  const rows = [];

  async function buildRows() {
    table.replaceChildren();
    rows.length = 0;
    if (readOnly) {
      table.append(...cards.map((c) => h("div", { class: "import-card" },
        h("strong", {}, c.player),
        h("div", { class: "scroll-x" }, h("table", { class: "scorecard compact" },
          h("tr", {}, h("th", {}, "Schläge"), c.strokes.map((v) => h("td", {}, v ?? "–"))))))));
      return;
    }
    if (!tSel.value) { table.append(h("p", { class: "muted" }, "Zuerst das Turnier wählen, zu dem die Karten gehören.")); return; }
    const t = await api("GET", `/tournaments/${tSel.value}`);
    const holeCount = t.holes_count;
    const firstHole = t.holes_count === 18 ? 1 : t.start_hole;
    const used = new Set();
    const entryOpts = [["", "— nicht übernehmen —"], ...t.entries.map((e) => [e.id, `${e.player}${e.scorecard ? " (hat Scorekarte)" : ""}`])];

    for (const [idx, c] of cards.entries()) {
      // Automatische Zuordnung über den Namen (ggf. "Nachname, Vorname")
      const key = nameKey(c.player.includes(",") ? c.player.split(",").reverse().join(" ") : c.player);
      const match = t.entries.find((e) => !used.has(e.id) && nameKey(e.player) === key);
      if (match) used.add(match.id);
      const entrySel = select("entry", entryOpts, match ? match.id : "", { disabled: readOnly });
      const inputs = Array.from({ length: holeCount }, (_, i) => {
        const v = c.strokes[i];
        return h("input", { class: "stroke", inputmode: "numeric", maxlength: 2, value: v === null || v === undefined ? "–" : v,
          "aria-label": `Loch ${firstHole + i}`, readonly: readOnly });
      });
      const lengthWarn = c.strokes.length !== holeCount
        ? h("div", { class: "alert warn small" }, `Karte hat ${c.strokes.length} Werte, das Turnier ${holeCount} Löcher.`) : null;
      rows.push({ idx, entrySel, inputs, lengthOk: c.strokes.length === holeCount });
      table.append(h("div", { class: `import-card ${c.confidence === "low" ? "low" : ""}` },
        h("div", { class: "card-head" },
          h("div", {}, h("strong", {}, c.player), c.dgv_id ? h("span", { class: "muted" }, ` · DGV ${c.dgv_id}`) : null,
            " ", c.confidence === "low" ? badge("unsicher – prüfen", "warn") : badge("sicher", "ok")),
          field("Spieler im Turnier", entrySel)),
        lengthWarn,
        h("div", { class: "scroll-x" }, h("table", { class: "scorecard compact" },
          h("tr", {}, h("th", {}, "Loch"), inputs.map((_, i) => h("th", { class: "hole" }, firstHole + i))),
          h("tr", { class: "input-row" }, h("th", {}, "Schläge"), inputs.map((inp) => h("td", {}, inp))))),
        c.notes?.length ? h("ul", { class: "small muted" }, c.notes.map((n) => h("li", {}, n))) : null));
    }
    if (!readOnly) {
      table.append(h("div", { class: "actions" },
        h("button", { class: "primary", onclick: apply }, "Ausgewählte übernehmen"),
        h("button", { class: "ghost danger", onclick: reject }, "Import verwerfen")),
      h("p", { class: "muted small" }, "Übernommene Karten werden als Entwurf gespeichert. Bestätigte Scorekarten werden nicht überschrieben. Alles oder nichts: tritt ein Fehler auf, wird keine Karte übernommen."));
    }
  }

  async function apply() {
    const assignments = [];
    const problems = [];
    for (const r of rows) {
      if (!r.entrySel.value) continue;
      const strokes = r.inputs.map((i) => {
        const v = i.value.trim();
        if (v === "" || v === "-" || v === "–" || v.toLowerCase() === "x") return null;
        return /^\d{1,2}$/.test(v) ? Number(v) : NaN;
      });
      if (strokes.some(Number.isNaN)) { problems.push(`Karte ${r.idx + 1}: ungültige Eingabe.`); continue; }
      assignments.push({ card: r.idx, entry_id: Number(r.entrySel.value), strokes });
    }
    const ids = assignments.map((a) => a.entry_id);
    if (new Set(ids).size !== ids.length) problems.push("Ein Spieler ist mehreren Karten zugeordnet.");
    if (!assignments.length) problems.push("Keine Karte einem Spieler zugeordnet.");
    if (problems.length) { showError(slot, Object.assign(new Error("Bitte prüfen."), { errors: problems })); return; }
    try {
      const res = await api("POST", `/imports/${id}/apply`, { assignments });
      toast(`${res.applied.length} Scorekarte(n) übernommen`);
      location.hash = `#/turniere/${tSel.value}`;
    } catch (err) { showError(slot, err); }
  }

  async function reject() {
    if (!confirm("Import verwerfen? Es werden keine Scorekarten übernommen.")) return;
    try { await api("POST", `/imports/${id}/reject`); toast("Import verworfen"); reload(); } catch (err) { showError(slot, err); }
  }

  tSel.addEventListener("change", () => buildRows().catch((err) => showError(slot, err)));
  await buildRows();

  return h("section", { class: "page" }, header, slot,
    (imp.parsed?.warnings || []).map((w) => h("div", { class: "alert warn" }, w)),
    h("div", { class: imp.kind === "csv" ? "stack" : "split" },
      h("div", { class: "card" }, h("h2", {}, "Original"), previewBox),
      h("div", { class: "card" }, h("h2", {}, "Erkannte Karten"),
        readOnly ? null : field("Turnier", tSel), table)));
}
