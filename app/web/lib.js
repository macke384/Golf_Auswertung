// Kleine Helfer für DOM, API und Formatierung.
// Grundregel: Daten werden nie als HTML eingesetzt, nur als Text (Schutz vor XSS).

export class ApiError extends Error {
  constructor(status, detail, errors = []) {
    super(detail);
    this.status = status;
    this.errors = errors;
  }
}

/** Element bauen: h("div", {class: "x", onclick: fn}, "Text", kind, …) */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "hidden") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(method, path, body, { raw = false, headers = {} } = {}) {
  const opts = { method, credentials: "same-origin", headers: { ...headers } };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) {
      opts.body = body;
    } else {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError(0, "Server nicht erreichbar. Netzwerk prüfen und erneut versuchen.");
  }
  if (res.status === 401 && !path.startsWith("/auth/login")) {
    onUnauthorized();
    throw new ApiError(401, "Bitte erneut anmelden.");
  }
  if (raw) return res;
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* kein JSON */ }
  if (!res.ok) {
    const errs = (data && data.errors) || [];
    const list = errs.map((e) => (typeof e === "string" ? e : `${e.field}: ${e.message}`));
    throw new ApiError(res.status, (data && data.detail) || `Fehler ${res.status}`, list);
  }
  return data;
}

export function errorBox(err) {
  const box = h("div", { class: "alert error", role: "alert" }, h("strong", {}, err.message || String(err)));
  if (err.errors && err.errors.length) box.append(h("ul", {}, err.errors.map((e) => h("li", {}, e))));
  return box;
}

/** Fehler in einem Bereich anzeigen (ersetzt vorherige Meldung). */
export function showError(slot, err) {
  slot.replaceChildren(errorBox(err));
  slot.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

let toastTimer;
export function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

export function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = String(iso).slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

export function fmtHcp(v) {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  if (n < 0) return `+${Math.abs(n).toFixed(1).replace(".", ",")}`;
  return n.toFixed(1).replace(".", ",");
}

/** "18,4" oder "+2,1" → "18.4" / "-2.1" für die API (null bei leer). */
export function parseHcp(text) {
  const t = String(text || "").trim().replace(",", ".");
  if (!t) return null;
  if (t.startsWith("+")) return (-Number(t.slice(1))).toFixed(1);
  return Number(t).toFixed(1);
}

export function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

/** Vergleichsschlüssel für Namen (Groß/klein, Umlaute, Leerzeichen egal). */
export function nameKey(s) {
  return String(s || "")
    .toLowerCase()
    .replaceAll("ä", "ae").replaceAll("ö", "oe").replaceAll("ü", "ue").replaceAll("ß", "ss")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ").trim();
}

export function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", { class: "muted" }, hint) : null);
}

export function select(name, options, value, props = {}) {
  return h("select", { name, ...props },
    options.map(([v, text]) => h("option", { value: v, selected: String(v) === String(value ?? "") }, text)));
}

export function badge(text, kind = "") {
  return h("span", { class: `badge ${kind}` }, text);
}

export const STATUS = { draft: "Entwurf", submitted: "Eingereicht", confirmed: "Bestätigt" };
export const STATUS_KIND = { draft: "", submitted: "info", confirmed: "ok" };

export async function sha256Hex(buffer) {
  if (!crypto.subtle) return null; // nur in sicherem Kontext (HTTPS) verfügbar
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
