// Spieler, Mannschaften, Nutzer.
import { api, h, field, select, showError, toast, fmtHcp, parseHcp, badge, nameKey } from "./lib.js";
import { can, reload, state } from "./app.js";

const GENDER = [["m", "Herren"], ["w", "Damen"], ["d", "Divers"]];

// ---------------------------------------------------------------- Spieler

export async function playersPage() {
  const players = await api("GET", "/players");
  const slot = h("div");
  let editing = null;

  const form = h("form", { class: "card form-grid", hidden: !can.edit(), onsubmit: save },
    h("h2", { class: "span-all", id: "player-form-title" }, "Neuer Spieler"),
    slot,
    field("Vorname", h("input", { name: "first_name", required: true, maxlength: 100 })),
    field("Nachname", h("input", { name: "last_name", required: true, maxlength: 100 })),
    field("Geschlecht", select("gender", GENDER, "m")),
    field("Handicap-Index", h("input", { name: "handicap_index", inputmode: "decimal", placeholder: "z. B. 18,4 oder +1,2" })),
    field("DGV-Nr.", h("input", { name: "dgv_id", maxlength: 32 }), "optional"),
    h("div", { class: "actions span-all" },
      h("button", { type: "submit", class: "primary" }, "Speichern"),
      h("button", { type: "button", class: "ghost", id: "cancel-edit", hidden: true, onclick: () => startEdit(null) }, "Abbrechen")));

  function startEdit(p) {
    editing = p;
    form.reset();
    slot.replaceChildren();
    form.querySelector("#player-form-title").textContent = p ? `${p.first_name} ${p.last_name} bearbeiten` : "Neuer Spieler";
    form.querySelector("#cancel-edit").hidden = !p;
    if (p) {
      form.first_name.value = p.first_name;
      form.last_name.value = p.last_name;
      form.gender.value = p.gender;
      form.handicap_index.value = p.handicap_index === null ? "" : fmtHcp(p.handicap_index);
      form.dgv_id.value = p.dgv_id || "";
      form.scrollIntoView({ behavior: "smooth" });
      form.first_name.focus();
    }
  }

  async function save(e) {
    e.preventDefault();
    const hi = parseHcp(form.handicap_index.value);
    if (hi !== null && Number.isNaN(Number(hi))) {
      showError(slot, new Error("Handicap-Index bitte als Zahl angeben, z. B. 18,4."));
      return;
    }
    const body = {
      first_name: form.first_name.value, last_name: form.last_name.value, gender: form.gender.value,
      handicap_index: hi, dgv_id: form.dgv_id.value.trim() || null,
    };
    try {
      if (editing) await api("PATCH", `/players/${editing.id}`, body);
      else await api("POST", "/players", body);
      toast(editing ? "Spieler gespeichert" : "Spieler angelegt");
      reload();
    } catch (err) { showError(slot, err); }
  }

  const tbody = h("tbody");
  const search = h("input", { type: "search", placeholder: "Suchen …", class: "search", oninput: renderRows });
  function renderRows() {
    const q = nameKey(search.value);
    tbody.replaceChildren(...players
      .filter((p) => !q || nameKey(`${p.first_name} ${p.last_name} ${p.dgv_id || ""}`).includes(q))
      .map((p) => h("tr", {},
        h("td", {}, `${p.last_name}, ${p.first_name}`),
        h("td", {}, GENDER.find(([g]) => g === p.gender)?.[1] || p.gender),
        h("td", { class: "num" }, fmtHcp(p.handicap_index)),
        h("td", {}, p.dgv_id || ""),
        h("td", { class: "right" }, can.edit() ? h("button", { class: "link", onclick: () => startEdit(p) }, "Bearbeiten") : null))));
    if (!tbody.children.length) tbody.append(h("tr", {}, h("td", { colspan: 5, class: "muted" }, players.length ? "Keine Treffer." : "Noch keine Spieler erfasst.")));
  }
  renderRows();

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Spieler"), search),
    form,
    h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Geschlecht"), h("th", { class: "num" }, "HCP-Index"), h("th", {}, "DGV-Nr."), h("th", {}))),
      tbody)));
}

// ----------------------------------------------------------- Mannschaften

export async function teamsPage() {
  const [teams, players] = await Promise.all([api("GET", "/teams"), api("GET", "/players")]);
  const slot = h("div");

  const create = h("form", { class: "card inline-form", hidden: !can.edit(), onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api("POST", "/teams", { name: create.elements.namedItem("name").value, player_ids: [] });
      toast("Mannschaft angelegt");
      reload();
    } catch (err) { showError(slot, err); }
  } },
  field("Neue Mannschaft", h("input", { name: "name", required: true, maxlength: 120, placeholder: "z. B. Herren 1" })),
  h("button", { type: "submit", class: "primary" }, "Anlegen"));

  function teamCard(t) {
    const memberIds = new Set(t.members.map((m) => m.id));
    const list = h("div", { class: "member-list" });
    const editBox = h("div", { class: "checklist", hidden: true },
      players.map((p) => h("label", {},
        h("input", { type: "checkbox", value: p.id, checked: memberIds.has(p.id) }),
        ` ${p.last_name}, ${p.first_name} `, h("span", { class: "muted" }, fmtHcp(p.handicap_index)))));
    const errSlot = h("div");
    const editBtn = h("button", { class: "link", hidden: !can.edit(), onclick: () => {
      editBox.hidden = !editBox.hidden;
      saveBtn.hidden = editBox.hidden;
      editBtn.textContent = editBox.hidden ? "Spieler zuordnen" : "Schließen";
    } }, "Spieler zuordnen");
    const saveBtn = h("button", { class: "primary small", hidden: true, onclick: async () => {
      const ids = [...editBox.querySelectorAll("input:checked")].map((c) => Number(c.value));
      try {
        await api("PUT", `/teams/${t.id}/members`, { player_ids: ids });
        toast("Mannschaft gespeichert");
        reload();
      } catch (err) { showError(errSlot, err); }
    } }, "Speichern");
    list.append(...(t.members.length ? t.members.map((m) => h("span", { class: "chip" }, m.name)) : [h("span", { class: "muted" }, "Noch keine Spieler")]));
    return h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, t.name), h("span", { class: "muted" }, `${t.members.length} Spieler`)),
      list, errSlot, editBox, h("div", { class: "actions" }, editBtn, saveBtn));
  }

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Mannschaften")),
    slot, create,
    teams.length ? h("div", { class: "grid-2" }, teams.map(teamCard)) : h("p", { class: "muted" }, "Noch keine Mannschaften."));
}

// ------------------------------------------------------------------ Nutzer

const ROLES = [["viewer", "Lesen"], ["editor", "Bearbeiten"], ["admin", "Admin"]];

export async function usersPage() {
  const users = await api("GET", "/users");
  const slot = h("div");

  const create = h("form", { class: "card form-grid", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api("POST", "/users", { email: create.email.value, password: create.password.value, role: create.role.value });
      toast("Nutzer angelegt");
      reload();
    } catch (err) { showError(slot, err); }
  } },
  h("h2", { class: "span-all" }, "Neuer Nutzer"),
  h("p", { class: "muted span-all" }, "Es gibt keine offene Registrierung. Teile das Startpasswort persönlich mit."),
  slot,
  field("E-Mail", h("input", { name: "email", type: "email", required: true })),
  field("Startpasswort", h("input", { name: "password", type: "text", minlength: 12, required: true, autocomplete: "off" }), "mindestens 12 Zeichen"),
  field("Rolle", select("role", ROLES, "editor")),
  h("div", { class: "actions span-all" }, h("button", { type: "submit", class: "primary" }, "Anlegen")));

  const rows = users.map((u) => {
    const err = h("div");
    const self = u.email === state.user.email;
    const patch = async (body, msg) => {
      try { await api("PATCH", `/users/${u.id}`, body); toast(msg); reload(); } catch (e) { showError(err, e); }
    };
    return h("tr", {},
      h("td", {}, u.email, self ? h("span", { class: "muted" }, " (du)") : null, err),
      h("td", {}, select("role", ROLES, u.role, { disabled: self, onchange: (e) => patch({ role: e.target.value }, "Rolle geändert") })),
      h("td", {}, u.is_active ? badge("aktiv", "ok") : badge("gesperrt", "warn")),
      h("td", { class: "right" },
        self ? null : h("button", { class: "link", onclick: () => patch({ is_active: !u.is_active }, u.is_active ? "Nutzer gesperrt" : "Nutzer entsperrt") }, u.is_active ? "Sperren" : "Entsperren"),
        " ",
        h("button", { class: "link", onclick: () => {
          const pw = prompt(`Neues Passwort für ${u.email} (mind. 12 Zeichen):`);
          if (pw) patch({ password: pw }, "Passwort gesetzt, bestehende Sitzungen beendet");
        } }, "Passwort setzen")));
  });

  return h("section", { class: "page" },
    h("div", { class: "page-head" }, h("h1", {}, "Nutzer")),
    create,
    h("div", { class: "card flush" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "E-Mail"), h("th", {}, "Rolle"), h("th", {}, "Status"), h("th", {}))),
      h("tbody", {}, rows))));
}
