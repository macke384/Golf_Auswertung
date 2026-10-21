// Einstieg: Anmeldung, Navigation und Router (Hash-basiert: #/turniere/3 …).
import { api, h, setUnauthorizedHandler, showError, field } from "./lib.js";
import { playersPage, teamsPage, usersPage } from "./pages-people.js";
import { coursesPage, courseNewPage, courseDetailPage } from "./pages-courses.js";
import { tournamentsPage, tournamentDetailPage, scorecardPage } from "./pages-tournaments.js";
import { importsPage, importDetailPage } from "./pages-imports.js";

export const state = { user: null };

export const can = {
  edit: () => state.user && ["editor", "admin"].includes(state.user.role),
  admin: () => state.user && state.user.role === "admin",
};

const routes = [
  [/^\/?$/, tournamentsPage],
  [/^\/turniere$/, tournamentsPage],
  [/^\/turniere\/(\d+)$/, tournamentDetailPage],
  [/^\/scorekarte\/(\d+)$/, scorecardPage],
  [/^\/spieler$/, playersPage],
  [/^\/mannschaften$/, teamsPage],
  [/^\/plaetze$/, coursesPage],
  [/^\/plaetze\/neu$/, courseNewPage],
  [/^\/plaetze\/(\d+)$/, courseDetailPage],
  [/^\/importe$/, importsPage, "edit"],
  [/^\/importe\/(\d+)$/, importDetailPage, "edit"],
  [/^\/nutzer$/, usersPage, "admin"],
];

const NAV = [
  ["#/turniere", "Turniere", /^\/(turniere|scorekarte)/],
  ["#/spieler", "Spieler", /^\/spieler/],
  ["#/mannschaften", "Mannschaften", /^\/mannschaften/],
  ["#/plaetze", "Plätze", /^\/plaetze/],
  ["#/importe", "Importe", /^\/importe/, "edit"],
  ["#/nutzer", "Nutzer", /^\/nutzer/, "admin"],
];

const main = document.getElementById("main");

function currentPath() {
  return decodeURIComponent(location.hash.replace(/^#/, "")) || "/";
}

function renderNav(path) {
  const nav = document.getElementById("nav");
  nav.replaceChildren(...NAV.filter(([, , , need]) => !need || can[need]())
    .map(([href, label, re]) => {
      const active = re.test(path === "/" ? "/turniere" : path);
      return h("a", { href, class: active ? "active" : "", "aria-current": active ? "page" : null }, label);
    }));
  document.getElementById("who").textContent = `${state.user.email} · ${{ admin: "Admin", editor: "Bearbeiten", viewer: "Lesen" }[state.user.role]}`;
  document.getElementById("topbar").hidden = false;
}

let renderSeq = 0;
async function router() {
  if (!state.user) return;
  const path = currentPath();
  renderNav(path);
  const seq = ++renderSeq;
  for (const [re, page, need] of routes) {
    const m = path.match(re);
    if (!m) continue;
    if (need && !can[need]()) {
      main.replaceChildren(h("p", { class: "pad muted" }, "Keine Berechtigung für diese Seite."));
      return;
    }
    main.setAttribute("aria-busy", "true");
    try {
      const view = await page(...m.slice(1));
      if (seq === renderSeq) main.replaceChildren(view); // veraltete Antworten verwerfen
    } catch (err) {
      if (seq === renderSeq && err.status !== 401) {
        const slot = h("div", { class: "pad" });
        main.replaceChildren(slot);
        showError(slot, err);
      }
    } finally {
      main.removeAttribute("aria-busy");
    }
    window.scrollTo(0, 0);
    return;
  }
  main.replaceChildren(h("p", { class: "pad muted" }, "Seite nicht gefunden."));
}

function loginView(message) {
  document.getElementById("topbar").hidden = true;
  const slot = h("div");
  const form = h("form", { class: "card login", onsubmit: async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button");
    btn.disabled = true;
    try {
      const fd = new FormData(form);
      state.user = await api("POST", "/auth/login", { email: fd.get("email"), password: fd.get("password") });
      if (!location.hash) location.hash = "#/turniere";
      router();
    } catch (err) {
      showError(slot, err);
      btn.disabled = false;
    }
  } },
  h("img", { src: "/static/icon.svg", alt: "", width: 44, height: 44 }),
  h("h1", {}, "Golf Auswertung"),
  message ? h("p", { class: "muted" }, message) : null,
  slot,
  field("E-Mail", h("input", { name: "email", type: "email", autocomplete: "username", required: true, autofocus: true })),
  field("Passwort", h("input", { name: "password", type: "password", autocomplete: "current-password", required: true })),
  h("button", { type: "submit", class: "primary wide" }, "Anmelden"));
  main.replaceChildren(h("div", { class: "login-wrap" }, form));
}

setUnauthorizedHandler(() => {
  const wasLoggedIn = !!state.user;
  state.user = null;
  loginView(wasLoggedIn ? "Die Sitzung ist abgelaufen. Bitte erneut anmelden." : undefined);
});

document.getElementById("logout").addEventListener("click", async () => {
  try { await api("POST", "/auth/logout"); } catch { /* egal */ }
  state.user = null;
  loginView("Abgemeldet.");
});

window.addEventListener("hashchange", router);

(async function start() {
  try {
    state.user = await api("GET", "/auth/me");
    router();
  } catch (err) {
    if (err.status !== 401) loginView(err.message);
  }
})();

/** Für Seiten: nach einer Änderung die aktuelle Ansicht neu laden. */
export function reload() { router(); }
