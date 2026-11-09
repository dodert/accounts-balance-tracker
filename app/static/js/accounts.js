// The accounts list, with its search.
import { api } from "./api.js";
import { openAccount } from "./dialogs.js";
import { $, $$, esc } from "./dom.js";
import { changeCell, fmtDate, lockBadge, money } from "./format.js";
import { state } from "./state.js";

// --- Accounts ---

export async function loadMilestones() {
  state.milestones = await api("/api/milestones");
}

export async function loadAccounts() {
  state.accounts = await api("/api/accounts");
  renderAccounts();
}

// Search in the accounts list: rows that do not match are dimmed, not hidden, and the charts do not
// change. Case and accent insensitive ("ahorro" finds "Ahorro", "inversion" finds "Inversión").
export const fold = (c) => c.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
export const searchTerm = () => fold($("#account-search").value.trim());

// The text, escaped, with the part that matches the search marked
export function highlight(text) {
  const term = searchTerm();
  if (!term || !text) return esc(text);
  const chars = [...text];
  const folded = chars.map(fold);
  const flat = folded.join("");
  const at = flat.indexOf(term);
  if (at < 0) return esc(text);
  // Map the match in the folded text back to the original characters
  let pos = 0, start = -1, end = chars.length;
  for (let i = 0; i < chars.length; i++) {
    if (start < 0 && pos + folded[i].length > at) start = i;
    pos += folded[i].length;
    if (pos >= at + term.length) { end = i + 1; break; }
  }
  return esc(chars.slice(0, start).join("")) + `<mark>${esc(chars.slice(start, end).join(""))}</mark>` + esc(chars.slice(end).join(""));
}

export function applySearch() {
  const term = searchTerm();
  const rows = $$("#accounts-table tbody tr[data-id]");
  let hits = 0;
  for (const row of rows) {
    const a = state.accounts.find((x) => x.id === Number(row.dataset.id));
    const match = !term || [a.name, a.institution, t(`type.${a.type}`)].some((v) => v && fold(v).includes(term));
    row.classList.toggle("dim", !match);
    hits += match ? 1 : 0;
  }
  $("#account-search-count").textContent = term ? t("accounts.search_count", { n: hits, total: rows.length }) : "";
}

export function renderAccounts() {
  const showArchived = $("#show-archived").checked;
  const rows = state.accounts.filter((a) => showArchived || !a.archived);
  $("#empty").hidden = state.accounts.length > 0;
  $("#accounts-table").hidden = rows.length === 0;
  $("#accounts-table tbody").innerHTML = rows.map((a) => `
    <tr data-id="${a.id}" tabindex="0" class="${a.archived ? "archived" : ""}">
      <td><span class="t">${highlight(a.name)}</span>${a.institution ? `<span class="sub">${highlight(a.institution)}</span>` : ""}${a.archived ? ` <span class="badge">${esc(t("accounts.archived"))}</span>` : ""}${lockBadge(a)}</td>
      <td>${highlight(t(`type.${a.type}`))}</td>
      <td class="num">${esc(money(a.balance, a.currency))}</td>
      <td class="num">${changeCell(a.balance, a.previous_balance, a.currency)}</td>
      <td class="num">${changeCell(a.balance, a.first_balance, a.currency, true)}</td>
      <td>${esc(fmtDate(a.balance_date))}</td>
    </tr>`).join("");
  applySearch();
}

export function initAccounts() {
  $("#show-archived").addEventListener("change", renderAccounts);
  $("#account-search").addEventListener("input", renderAccounts);
  $("#account-search").addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.target.value = ""; renderAccounts(); }
  });
  $("#accounts-table tbody").addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-id]");
    if (row) openAccount(Number(row.dataset.id));
  });
  $("#accounts-table tbody").addEventListener("keydown", (e) => {
    const row = e.target.closest("tr[data-id]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openAccount(Number(row.dataset.id)); }
  });
}
