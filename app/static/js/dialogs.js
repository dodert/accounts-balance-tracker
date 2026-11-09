// The dialogs: update balances, milestones, account (with its balances), CSV and JSON import.
import { api } from "./api.js";
import { $, $$, esc } from "./dom.js";
import { amountText, changeCell, fmtDate, getDate, money, parseAmount, setDate, today } from "./format.js";
import { refresh } from "./main.js";
import { ACCOUNT_TYPES, state } from "./state.js";

export function setError(el, err) {
  el.textContent = err ? err.message ?? String(err) : "";
  el.hidden = !err;
}

export async function openAccount(id) {
  const account = id === null ? null : state.accounts.find((a) => a.id === id);
  state.editing = account;
  const form = $("#account-form");
  form.reset();
  $("#account-dlg-title").textContent = account ? account.name : t("account.new");
  form.name.value = account?.name ?? "";
  form.institution.value = account?.institution ?? "";
  setDate(form.available_from, account?.available_from ?? "");
  form.available_from.placeholder = ""; // empty means available: no example date that looks like a value
  form.type.value = account?.type ?? "bank";
  form.currency.value = account?.currency ?? state.about?.default_currency ?? "EUR";
  form.notes.value = account?.notes ?? "";
  form.archived.checked = account?.archived ?? false;
  $("#account-delete").hidden = !account;
  $("#balances-section").hidden = !account;
  setError($("#account-error"), null);
  if (account) {
    setDate($("#nb-date"), today());
    $("#nb-amount").value = "";
    $("#nb-note").value = "";
    setDate($("#nm-date"), today());
    $("#nm-amount").value = "";
    $("#nm-desc").value = "";
    renderMilestones();
    await loadBalances();
  }
  $("#account-dlg").showModal();
}

export async function loadBalances() {
  const account = state.editing;
  const balances = await api(`/api/accounts/${account.id}/balances`);
  // Newest first: the next row is the previous balance, the last row the first one
  const first = balances[balances.length - 1];
  $("#balances-table thead").hidden = !balances.length;
  $("#balances-table tbody").innerHTML = balances.length ? balances.map((b, i) => `
    <tr>
      <td>${esc(fmtDate(b.date))}</td>
      <td class="num">${esc(money(b.amount, account.currency))}</td>
      <td class="num">${balances[i + 1] ? changeCell(b.amount, balances[i + 1].amount, account.currency) : ""}</td>
      <td class="num">${balances[i + 1] ? changeCell(b.amount, first.amount, account.currency, true) : ""}</td>
      <td class="note">${esc(b.note ?? "")}</td>
      <td class="actions"><button type="button" class="icon danger" data-delete="${b.id}" title="${esc(t("common.delete"))}" aria-label="${esc(t("common.delete"))}">✕</button></td>
    </tr>`).join("") : `<tr><td class="hint">${esc(t("account.no_balances"))}</td></tr>`;
}

// The account's milestones, newest first
export function renderMilestones() {
  const account = state.editing;
  const list = state.milestones.filter((m) => m.account_id === account.id).reverse();
  $("#milestones-table thead").hidden = !list.length;
  $("#milestones-table tbody").innerHTML = list.length ? list.map((m) => `
    <tr>
      <td>${esc(fmtDate(m.date))}</td>
      <td class="num">${esc(money(m.amount, account.currency))}</td>
      <td class="note">${esc(m.description)}</td>
      <td class="actions"><button type="button" class="icon danger" data-delete="${m.id}" title="${esc(t("common.delete"))}" aria-label="${esc(t("common.delete"))}">✕</button></td>
    </tr>`).join("") : `<tr><td class="hint">${esc(t("milestones.none"))}</td></tr>`;
}

// What an import did not add because it was already there (the answer lists the first ones)
export function duplicateLines(r) {
  if (!r.duplicates_count) return [];
  const more = r.duplicates_count - r.duplicates.length;
  return [t("import.duplicates", { n: r.duplicates_count }), ...r.duplicates.map((d) => `  ${d}`),
    ...(more > 0 ? [t("import.more", { n: more })] : [])];
}

export function initDialogs() {
  $$("dialog .cancel").forEach((btn) => btn.addEventListener("click", () => btn.closest("dialog").close()));

  // Update balances
  $("#update-btn").addEventListener("click", () => {
    const active = state.accounts.filter((a) => !a.archived);
    if (!active.length) { openAccount(null); return; }
    const form = $("#update-form");
    setDate(form.date, today());
    // Sorted by institution, then by name, with a heading per institution (accounts without one last)
    const inst = (a) => a.institution ?? "";
    active.sort((a, b) => (inst(a) === "") - (inst(b) === "") || inst(a).localeCompare(inst(b)) || a.name.localeCompare(b.name));
    $("#update-rows").innerHTML = active.map((a, i) => (i === 0 || inst(a) !== inst(active[i - 1])
      ? `<h3 class="update-group">${esc(inst(a) || t("chart.no_institution"))}</h3>` : "") + `
      <label class="update-row">
        <span class="u-name">${esc(a.name)} <span class="sub">${esc(a.balance !== null ? t("update.last", { amount: money(a.balance, a.currency), date: fmtDate(a.balance_date) }) : t("update.never"))}</span></span>
        <span class="u-input"><input type="text" inputmode="decimal" data-id="${a.id}" value="${esc(amountText(a.balance))}" autocomplete="off"> <span class="cur">${esc(a.currency)}</span></span>
      </label>`).join("");
    setError($("#update-error"), null);
    $("#update-dlg").showModal();
  });

  // New milestone, from the header: the account is picked in the dialog (grouped by institution)
  $("#milestone-btn").addEventListener("click", () => {
    const active = state.accounts.filter((a) => !a.archived);
    if (!active.length) { openAccount(null); return; }
    const inst = (a) => a.institution ?? "";
    active.sort((a, b) => (inst(a) === "") - (inst(b) === "") || inst(a).localeCompare(inst(b)) || a.name.localeCompare(b.name));
    const groups = [...new Set(active.map(inst))];
    $("#milestone-account").innerHTML = groups.map((g) => `<optgroup label="${esc(g || t("chart.no_institution"))}">` +
      active.filter((a) => inst(a) === g).map((a) => `<option value="${a.id}">${esc(a.name)} (${esc(a.currency)})</option>`).join("") +
      "</optgroup>").join("");
    const form = $("#milestone-form");
    form.reset();
    setDate(form.date, today());
    setError($("#milestone-error"), null);
    $("#milestone-dlg").showModal();
  });

  $("#milestone-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      const amount = parseAmount(form.amount.value);
      if (amount === null) throw new Error(t("error.amount_required"));
      if (!form.description.value.trim()) throw new Error(t("milestones.description_required"));
      await api(`/api/accounts/${form.account.value}/milestones`, { json: { date: getDate(form.date), amount, description: form.description.value } });
      $("#milestone-dlg").close();
      await refresh();
    } catch (err) {
      setError($("#milestone-error"), err);
    }
  });

  $("#update-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const items = $$("#update-rows input").map((input) => ({ account_id: Number(input.dataset.id), amount: parseAmount(input.value) }))
        .filter((item) => item.amount !== null);
      await api("/api/balances/bulk", { json: { date: getDate(e.target.date), items } });
      $("#update-dlg").close();
      await refresh();
    } catch (err) {
      setError($("#update-error"), err);
    }
  });

  // Account dialog
  $("#account-type").innerHTML = ACCOUNT_TYPES.map((type) => `<option value="${type}" data-i18n="type.${type}">${esc(t(`type.${type}`))}</option>`).join("");
  $("#add-account-btn").addEventListener("click", () => openAccount(null));

  $("#nb-add").addEventListener("click", async () => {
    try {
      const amount = parseAmount($("#nb-amount").value);
      if (amount === null) throw new Error(t("error.amount_required"));
      await api(`/api/accounts/${state.editing.id}/balances`, { json: { date: getDate($("#nb-date")), amount, note: $("#nb-note").value } });
      $("#nb-amount").value = "";
      $("#nb-note").value = "";
      setError($("#account-error"), null);
      await Promise.all([loadBalances(), refresh()]);
    } catch (err) {
      setError($("#account-error"), err);
    }
  });
  $("#nm-add").addEventListener("click", async () => {
    try {
      const amount = parseAmount($("#nm-amount").value);
      if (amount === null) throw new Error(t("error.amount_required"));
      if (!$("#nm-desc").value.trim()) throw new Error(t("milestones.description_required"));
      await api(`/api/accounts/${state.editing.id}/milestones`, { json: { date: getDate($("#nm-date")), amount, description: $("#nm-desc").value } });
      $("#nm-amount").value = "";
      $("#nm-desc").value = "";
      setError($("#account-error"), null);
      await refresh();
      renderMilestones();
    } catch (err) {
      setError($("#account-error"), err);
    }
  });

  $("#milestones-table").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-delete]");
    if (!btn || !confirm(t("milestones.confirm_delete"))) return;
    try {
      await api(`/api/milestones/${btn.dataset.delete}`, { method: "DELETE" });
      await refresh();
      renderMilestones();
    } catch (err) {
      setError($("#account-error"), err);
    }
  });

  $("#balances-table").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-delete]");
    if (!btn || !confirm(t("account.confirm_delete_balance"))) return;
    try {
      await api(`/api/balances/${btn.dataset.delete}`, { method: "DELETE" });
      await Promise.all([loadBalances(), refresh()]);
    } catch (err) {
      setError($("#account-error"), err);
    }
  });

  $("#account-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      const data = {
        name: form.name.value, institution: form.institution.value, type: form.type.value,
        currency: form.currency.value.toUpperCase(), notes: form.notes.value, archived: form.archived.checked,
        available_from: form.available_from.value.trim() ? getDate(form.available_from) : null,
      };
      if (state.editing) await api(`/api/accounts/${state.editing.id}`, { method: "PATCH", json: data });
      else await api("/api/accounts", { json: data });
      $("#account-dlg").close();
      await refresh();
    } catch (err) {
      setError($("#account-error"), err);
    }
  });

  $("#account-delete").addEventListener("click", async () => {
    const account = state.editing;
    if (!confirm(t("account.confirm_delete", { name: account.name }))) return;
    try {
      await api(`/api/accounts/${account.id}`, { method: "DELETE" });
      $("#account-dlg").close();
      await refresh();
    } catch (err) {
      setError($("#account-error"), err);
    }
  });

  // CSV import
  $("#csv-import-btn").addEventListener("click", () => {
    $("#data-menu").open = false;
    $("#csv-form").reset();
    $("#csv-result").hidden = true;
    $("#csv-dlg").showModal();
  });

  $("#csv-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#csv-result");
    try {
      const body = new FormData(e.target);
      const r = await api("/api/import/csv", { method: "POST", body });
      out.textContent = [t("csv.result", { accounts: r.accounts_created, created: r.balances_created }),
        ...duplicateLines(r), ...r.errors].join("\n");
      await refresh();
    } catch (err) {
      out.textContent = err.message;
    }
    out.hidden = false;
  });
  $("#csv-export-btn").addEventListener("click", () => { $("#data-menu").open = false; });
  $("#json-export-btn").addEventListener("click", () => { $("#data-menu").open = false; });
  // JSON import (everything)
  $("#json-import-btn").addEventListener("click", () => {
    $("#data-menu").open = false;
    $("#json-form").reset();
    $("#json-result").hidden = true;
    $("#json-dlg").showModal();
  });

  $("#json-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#json-result");
    const body = new FormData(e.target);
    if (body.get("mode") === "replace" && !confirm(t("json.confirm_replace"))) return;
    try {
      const r = await api("/api/import/json", { method: "POST", body });
      out.textContent = [t("json.result", { accounts: r.accounts_created, balances: r.balances_created, milestones: r.milestones_created }),
        ...duplicateLines(r)].join("\n");
      await refresh();
    } catch (err) {
      out.textContent = err.message;
    }
    out.hidden = false;
  });
}
