// Mortgages: the list, the mortgage dialog (details, summary, chart, prepayments, simulator, schedule).
// The schedule is computed by the server (app/mortgage.py); the simulator asks it too, and nothing is saved.
import { api } from "./api.js";
import { drawChart, hideTooltip, lineLegend, ttRow } from "./charts.js";
import { setError } from "./dialogs.js";
import { $, $$, esc } from "./dom.js";
import { markClean, openDialog } from "./modal.js";
import { amountText, axisLabels, exactMoney, fmtDate, getDate, money, parseAmount, setDate, today } from "./format.js";
import { state } from "./state.js";

const MODES = { reduce_term: () => t("mortgages.reduce_term"), reduce_payment: () => t("mortgages.reduce_payment") };
const modeText = (mode) => MODES[mode]?.() ?? mode;
// Colours of the chart's lines
const COLORS = { outstanding: "var(--s1)", repaid: "var(--s3)", interest: "var(--s2)", prepayment: "var(--s4)", simulated: "var(--s5)" };

// A rate in % (2.5 -> "2,50 %"), with up to 4 decimals
export const fmtRate = (rate) => new Intl.NumberFormat(fmtLocale(), { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(rate / 100);
// A typed rate (regional decimal separator) -> a number in %, with up to 4 decimals; null when empty
export function parseRate(text) {
  const units = parseAmount(text);
  return units === null ? null : units / 10000;
}
const rateText = (rate) => amountText(Math.round(rate * 10000)).replace(/([.,]\d\d)00$/, "$1");

// The mortgage being shown, its schedule, the prepayment being edited, the simulated prepayments and the simulated
// extra payment every month ({ start, amount, mode, fee } or null)
const view = { mortgage: null, schedule: null, editing: null, simulated: [], monthly: null, simulation: null };
const simulating = () => view.simulated.length > 0 || view.monthly !== null;

export async function loadMortgages() {
  const list = await api("/api/mortgages");
  // Each one's schedule, for its payment, outstanding and end date (there are few mortgages)
  const schedules = await Promise.all(list.map((m) => api(`/api/mortgages/${m.id}/schedule`)));
  state.mortgages = list.map((m, i) => ({ ...m, totals: schedules[i].totals }));
  renderMortgages();
}

export function renderMortgages() {
  const list = state.mortgages;
  $("#mortgages-empty").hidden = list.length > 0;
  $("#mortgages-table").hidden = list.length === 0;
  $("#mortgages-table tbody").innerHTML = list.map((m) => `
    <tr data-id="${m.id}" tabindex="0">
      <td><span class="t">${esc(m.name)}</span>${m.institution ? `<span class="sub">${esc(m.institution)}</span>` : ""}</td>
      <td class="num">${esc(fmtRate(m.totals.applied_rate))}</td>
      <td class="num">${esc(money(m.totals.current_payment, m.currency))}</td>
      <td class="num">${esc(money(m.totals.outstanding, m.currency))}</td>
      <td>${esc(fmtDate(m.totals.end_date))}</td>
    </tr>`).join("");
}

// --- The dialog ---

export async function openMortgage(id) {
  const m = id === null ? null : state.mortgages.find((x) => x.id === id);
  view.mortgage = m;
  view.schedule = null;
  view.simulated = [];
  view.monthly = null;
  view.simulation = null;
  const form = $("#mortgage-form");
  form.reset();
  $("#mortgage-dlg-title").textContent = m ? m.name : t("mortgages.new");
  form.name.value = m?.name ?? "";
  form.institution.value = m?.institution ?? "";
  form.currency.value = m?.currency ?? state.about?.default_currency ?? "EUR";
  form.principal.value = m ? amountText(m.principal) : "";
  form.term_months.value = m?.term_months ?? "";
  form.rate.value = m ? rateText(m.rate) : "";
  form.discount.value = m && m.discount ? rateText(m.discount) : "";
  setDate(form.first_payment, m?.first_payment ?? "");
  form.payment_day.value = m?.payment_day ?? "";
  form.notes.value = m?.notes ?? "";
  $("#mortgage-delete").hidden = !m;
  $("#mortgage-details").hidden = !m;
  setError($("#mortgage-error"), null);
  rateHint();
  const dlg = $("#mortgage-dlg");
  hideTooltip(); // shown again later, it goes above the dialog
  openDialog(dlg);
  if (m) {
    resetPrepaymentForm();
    setDate($("#ns-date"), today());
    for (const input of ["#ns-amount", "#ns-fee"]) $(input).value = "";
    $("#ns-monthly").checked = false;
    renderSimulation();
    await loadSchedule();
  }
}

// "Applied rate: 2,50 % (3,10 % − 0,60 points) · 25 years"
function rateHint() {
  const form = $("#mortgage-form");
  let text = "";
  try {
    const rate = parseRate(form.rate.value), discount = parseRate(form.discount.value) ?? 0;
    if (rate !== null) text = t("mortgages.applied_hint", { applied: fmtRate(rate - discount), rate: fmtRate(rate), discount: fmtRate(discount) });
  } catch {}
  const months = Number(form.term_months.value);
  if (months > 0) text += `${text ? " · " : ""}${t("mortgages.term_years", { years: (months / 12).toLocaleString(fmtLocale(), { maximumFractionDigits: 1 }) })}`;
  $("#mortgage-rate-hint").textContent = text;
}

async function loadSchedule() {
  const schedule = await api(`/api/mortgages/${view.mortgage.id}/schedule`);
  if (!view.mortgage) return; // the dialog was closed meanwhile
  view.schedule = schedule;
  view.mortgage = { ...view.mortgage, ...view.schedule.mortgage, totals: view.schedule.totals };
  renderSummary();
  renderPrepayments();
  renderSchedule();
  drawMortgageChart();
}

function renderSummary() {
  const { totals: s } = view.schedule;
  const cur = view.mortgage.currency;
  const tile = (label, value, sub = "") => `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${esc(value)}</div>${sub ? `<div class="tile-sub">${esc(sub)}</div>` : ""}</div>`;
  $("#mortgage-summary").innerHTML = [
    tile(t("mortgages.payment"), money(s.current_payment, cur), s.next_date ? t("mortgages.next_on", { date: fmtDate(s.next_date) }) : t("mortgages.repaid")),
    tile(t("mortgages.outstanding"), money(s.outstanding, cur), t("mortgages.of_principal", { amount: money(view.mortgage.principal, cur) })),
    tile(t("mortgages.principal_paid"), money(s.principal_paid, cur), t("mortgages.installments_paid", { n: s.installments_paid })),
    tile(t("mortgages.interest_paid"), money(s.interest_paid, cur), t("mortgages.total_interest", { amount: money(s.total_interest, cur) })),
    tile(t("mortgages.prepaid"), money(s.prepaid, cur), t("mortgages.fees_paid", { amount: money(s.fees_paid, cur) })),
    tile(t("mortgages.end_date"), fmtDate(s.end_date), t("mortgages.installments_left", { n: s.installments_left })),
  ].join("");
}

// The month before an ISO date (the loan starts one month before its first installment)
function monthBefore(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const last = new Date(Date.UTC(y, m - 1, 0)).getUTCDate();
  const date = new Date(Date.UTC(y, m - 2, Math.min(d, last)));
  return date.toISOString().slice(0, 10);
}

// Outstanding, principal repaid and interest paid over the whole loan; prepayments as vertical lines, today marked,
// and the simulated outstanding dashed
function drawMortgageChart() {
  const box = $("#mortgage-chart");
  box.innerHTML = "";
  const { rows, prepayments } = view.schedule;
  const m = view.mortgage, cur = m.currency;
  if (!rows.length) { $("#mortgage-legend").innerHTML = ""; return; }
  const dates = [monthBefore(rows[0].date), ...rows.map((r) => r.date)];
  const outstanding = [m.principal, ...rows.map((r) => r.outstanding)];
  let interest = 0;
  const series = [
    { name: t("mortgages.outstanding"), values: outstanding, color: COLORS.outstanding, noDots: true },
    { name: t("mortgages.chart_repaid"), values: outstanding.map((v) => m.principal - v), color: COLORS.repaid, noDots: true },
    { name: t("mortgages.chart_interest"), values: [0, ...rows.map((r) => (interest += r.interest))], color: COLORS.interest, noDots: true },
  ];
  const sim = view.simulation?.as_entered;
  if (sim) series.push({ name: t("mortgages.chart_simulated"), values: [m.principal, ...rows.map((_, i) => sim.outstanding[i] ?? 0)], color: COLORS.simulated, dashed: true, noDots: true });
  const marks = [
    ...prepayments.map((p) => ({ date: p.date, amount: p.applied, currency: cur, color: COLORS.prepayment, description: `${t("mortgages.prepayment")} · ${modeText(p.mode)}` })),
    ...(sim ? view.simulated.map((p) => ({ date: p.date, amount: p.amount, currency: cur, color: COLORS.simulated, description: `${t("mortgages.simulated_prepayment")} · ${modeText(p.mode)}` })) : []),
    ...(sim && view.monthly ? [{ date: view.monthly.start, amount: view.monthly.amount, currency: cur, color: COLORS.simulated, description: `${t("mortgages.monthly_from", { date: fmtDate(view.monthly.start) })} · ${modeText(view.monthly.mode)}` }] : []),
  ];
  const now = today();
  const drawn = drawChart(box, {
    dates, series, height: Math.max(220, Math.min(320, window.innerHeight - 380)), area: true, label: m.name,
    milestones: marks,
    marker: { date: now, label: t("mortgages.today") },
    axis: (ticks) => axisLabels(ticks, cur),
    tooltip: (idx, list) => list.map((s) => ttRow(s, money(s.values[idx], cur))).join(""),
  });
  $("#mortgage-legend").innerHTML = lineLegend([
    ...series.map((s) => ({ kind: s.dashed ? "dashed" : "solid", label: s.name, color: s.color })),
    drawn?.milestones && prepayments.length && { kind: "milestone", label: t("mortgages.prepayment"), color: COLORS.prepayment },
    drawn?.milestones && sim && { kind: "milestone", label: t("mortgages.simulated_prepayment"), color: COLORS.simulated },
    drawn?.marker && { kind: "marker", label: t("mortgages.today") },
  ].filter(Boolean));
}

function renderPrepayments() {
  const cur = view.mortgage.currency;
  const list = [...view.schedule.prepayments].reverse(); // newest first
  $("#prepayments-table thead").hidden = !list.length;
  $("#prepayments-table tbody").innerHTML = list.length ? list.map((p) => `
    <tr>
      <td>${esc(fmtDate(p.date))}</td>
      <td class="num">${esc(exactMoney(p.amount, cur))}${p.applied !== p.amount ? ` <span class="sub">${esc(t("mortgages.applied_only", { amount: money(p.applied, cur) }))}</span>` : ""}</td>
      <td>${esc(modeText(p.mode))}</td>
      <td class="num">${p.fee ? esc(exactMoney(p.fee, cur)) : ""}</td>
      <td class="note">${esc(p.note ?? "")}</td>
      <td class="actions"><button type="button" class="icon" data-edit="${p.id}" title="${esc(t("mortgages.edit"))}" aria-label="${esc(t("mortgages.edit"))}">✎</button> <button type="button" class="icon danger" data-delete="${p.id}" title="${esc(t("common.delete"))}" aria-label="${esc(t("common.delete"))}">✕</button></td>
    </tr>`).join("") : `<tr><td class="hint">${esc(t("mortgages.no_prepayments"))}</td></tr>`;
}

function renderSchedule() {
  const cur = view.mortgage.currency;
  const { rows, prepayments } = view.schedule;
  const head = `<thead><tr><th class="num">#</th><th>${esc(t("update.date"))}</th><th class="num">${esc(t("mortgages.payment"))}</th><th class="num">${esc(t("mortgages.interest"))}</th><th class="num">${esc(t("mortgages.principal_part"))}</th><th class="num">${esc(t("mortgages.outstanding"))}</th><th></th></tr></thead>`;
  const firstUnpaid = rows.find((r) => !r.paid)?.number;
  const prepaymentRow = (p) => `<tr class="prepayment${p.date <= today() ? " paid" : ""}"><td></td><td>${esc(fmtDate(p.date))}</td><td></td><td></td><td class="num">${esc(money(p.applied, cur))}</td><td class="num">${esc(money(p.outstanding, cur))}</td><td>${esc(t("mortgages.prepayment"))} · ${esc(modeText(p.mode))}</td></tr>`;
  const body = rows.map((r) => prepayments.filter((p) => p.before_installment === r.number).map(prepaymentRow).join("") + `
    <tr class="${r.paid ? "paid" : ""}${r.number === firstUnpaid ? " today" : ""}">
      <td class="num">${r.number}</td><td>${esc(fmtDate(r.date))}</td>
      <td class="num">${esc(money(r.payment, cur))}</td><td class="num">${esc(money(r.interest, cur))}</td>
      <td class="num">${esc(money(r.principal, cur))}</td><td class="num">${esc(money(r.outstanding, cur))}</td>
      <td>${r.paid ? `✓ ${esc(t("mortgages.paid"))}` : ""}</td>
    </tr>`).join("") + prepayments.filter((p) => p.before_installment === null).map(prepaymentRow).join("");
  $("#schedule-table").innerHTML = `${head}<tbody>${body}</tbody>`;
}

// --- Prepayments ---

function resetPrepaymentForm() {
  view.editing = null;
  setDate($("#np-date"), today());
  for (const input of ["#np-amount", "#np-fee", "#np-note"]) $(input).value = "";
  $("#np-mode").value = "reduce_term";
  $("#np-add").textContent = t("account.add_balance");
  $("#np-cancel").hidden = true;
  markClean($("#np-add").parentElement);
}

function readPrepayment(prefix) {
  const amount = parseAmount($(`#${prefix}-amount`).value);
  if (amount === null) throw new Error(t("error.amount_required"));
  if (amount <= 0) throw new Error(t("mortgages.amount_positive"));
  const fee = parseAmount($(`#${prefix}-fee`).value) ?? 0;
  if (fee < 0) throw new Error(t("mortgages.fee_negative"));
  return { date: getDate($(`#${prefix}-date`)), amount, mode: $(`#${prefix}-mode`).value, fee };
}

async function afterChange() {
  await Promise.all([loadSchedule(), loadMortgages()]);
  if (simulating()) await simulate();
}

// --- Simulator: prepayments that are not saved ---

async function simulate() {
  view.simulation = simulating()
    ? await api(`/api/mortgages/${view.mortgage.id}/simulate`, { json: { prepayments: view.simulated, monthly: view.monthly } })
    : null;
  renderSimulation();
  drawMortgageChart();
}

function renderSimulation() {
  const cur = view.mortgage?.currency;
  $("#simulation").hidden = !simulating();
  const m = view.monthly;
  // The monthly extra payment first: it can't be saved as real
  $("#simulation-table tbody").innerHTML = (m ? `
    <tr class="monthly">
      <td>${esc(t("mortgages.monthly_from", { date: fmtDate(m.start) }))}</td><td class="num">${esc(exactMoney(m.amount, cur))}</td><td>${esc(modeText(m.mode))}</td>
      <td class="num">${m.fee ? esc(exactMoney(m.fee, cur)) : ""}</td>
      <td class="actions"><button type="button" class="icon danger" data-remove-monthly title="${esc(t("common.delete"))}" aria-label="${esc(t("common.delete"))}">✕</button></td>
    </tr>` : "") + view.simulated.map((p, i) => `
    <tr>
      <td>${esc(fmtDate(p.date))}</td><td class="num">${esc(exactMoney(p.amount, cur))}</td><td>${esc(modeText(p.mode))}</td>
      <td class="num">${p.fee ? esc(exactMoney(p.fee, cur)) : ""}</td>
      <td class="actions"><button type="button" data-save="${i}">${esc(t("mortgages.make_real"))}</button> <button type="button" class="icon danger" data-remove="${i}" title="${esc(t("common.delete"))}" aria-label="${esc(t("common.delete"))}">✕</button></td>
    </tr>`).join("");
  const s = view.simulation;
  if (!s) { $("#simulation-result").innerHTML = ""; return; }
  const cols = [["as_entered", t("mortgages.as_entered")], ["reduce_term", t("mortgages.all_reduce_term")], ["reduce_payment", t("mortgages.all_reduce_payment")]];
  const row = (label, cell) => `<tr><th>${esc(label)}</th>${cols.map(([key]) => `<td class="num">${cell(s[key].compared, s[key].totals)}</td>`).join("")}</tr>`;
  $("#simulation-result").innerHTML = `<thead><tr><th></th>${cols.map(([, label]) => `<th class="num">${esc(label)}</th>`).join("")}</tr></thead><tbody>` +
    row(t("mortgages.interest_saved"), (c) => esc(money(c.interest_saved, cur))) +
    row(t("mortgages.fees_simulated"), (c) => esc(money(c.fees, cur))) +
    row(t("mortgages.net_saved"), (c) => `<strong class="${c.net_saved > 0 ? "up" : c.net_saved < 0 ? "down" : ""}">${esc(money(c.net_saved, cur))}</strong>`) +
    row(t("mortgages.payment_after"), (c) => esc(c.payment_after === null ? "—" : money(c.payment_after, cur)) +
      (c.payment_before !== null && c.payment_after !== null && c.payment_after !== c.payment_before ? ` <span class="sub">${esc(t("mortgages.instead_of", { amount: money(c.payment_before, cur) }))}</span>` : "")) +
    row(t("mortgages.end_date"), (c) => esc(fmtDate(c.end_date)) + (c.months_saved ? ` <span class="sub">${esc(t("mortgages.months_saved", { n: c.months_saved }))}</span>` : "")) +
    `</tbody>`;
}

export function initMortgages() {
  $("#add-mortgage-btn").addEventListener("click", () => openMortgage(null).catch(showMortgageError));
  $("#mortgages-table tbody").addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-id]");
    if (row) openMortgage(Number(row.dataset.id)).catch(showMortgageError);
  });
  $("#mortgages-table tbody").addEventListener("keydown", (e) => {
    const row = e.target.closest("tr[data-id]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openMortgage(Number(row.dataset.id)).catch(showMortgageError); }
  });
  const form = $("#mortgage-form");
  for (const name of ["rate", "discount", "term_months"]) form[name].addEventListener("input", rateHint);
  // The payment day follows the first installment's, unless typed
  form.first_payment.addEventListener("change", () => {
    try { if (!form.payment_day.value) form.payment_day.value = Number(getDate(form.first_payment).slice(8)); } catch {}
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const principal = parseAmount(form.principal.value);
      if (principal === null) throw new Error(t("error.amount_required"));
      const rate = parseRate(form.rate.value);
      if (rate === null) throw new Error(t("mortgages.rate_required"));
      const data = {
        name: form.name.value, institution: form.institution.value, currency: form.currency.value.toUpperCase(),
        principal, rate, discount: parseRate(form.discount.value) ?? 0, term_months: Number(form.term_months.value),
        first_payment: getDate(form.first_payment), payment_day: form.payment_day.value ? Number(form.payment_day.value) : null,
        notes: form.notes.value,
      };
      const saved = view.mortgage
        ? await api(`/api/mortgages/${view.mortgage.id}`, { method: "PUT", json: data })
        : await api("/api/mortgages", { json: data });
      await loadMortgages();
      if (view.mortgage) {
        $("#mortgage-dlg-title").textContent = saved.name;
        markClean(...$$("#mortgage-form > :not(#mortgage-details)"));
        setError($("#mortgage-error"), null);
        view.mortgage = state.mortgages.find((x) => x.id === saved.id);
        await afterChange();
      } else {
        // A new one: show it, with its schedule, chart and prepayments
        $("#mortgage-dlg").close();
        await openMortgage(saved.id);
      }
    } catch (err) {
      setError($("#mortgage-error"), err);
    }
  });

  $("#mortgage-delete").addEventListener("click", async () => {
    if (!confirm(t("mortgages.confirm_delete", { name: view.mortgage.name }))) return;
    try {
      await api(`/api/mortgages/${view.mortgage.id}`, { method: "DELETE" });
      $("#mortgage-dlg").close();
      await loadMortgages();
    } catch (err) {
      setError($("#mortgage-error"), err);
    }
  });

  $("#mortgage-dlg").addEventListener("close", () => {
    // The event comes after a task: a new mortgage's dialog may be open again by then, showing it
    if ($("#mortgage-dlg").open) return;
    view.mortgage = null;
    hideTooltip();
  });

  $("#np-add").addEventListener("click", async () => {
    try {
      const data = { ...readPrepayment("np"), note: $("#np-note").value };
      if (view.editing) await api(`/api/prepayments/${view.editing}`, { method: "PUT", json: data });
      else await api(`/api/mortgages/${view.mortgage.id}/prepayments`, { json: data });
      resetPrepaymentForm();
      setError($("#mortgage-error"), null);
      await afterChange();
    } catch (err) {
      setError($("#mortgage-error"), err);
    }
  });
  $("#np-cancel").addEventListener("click", resetPrepaymentForm);
  $("#prepayments-table").addEventListener("click", async (e) => {
    const edit = e.target.closest("[data-edit]");
    if (edit) {
      const p = view.schedule.prepayments.find((x) => x.id === Number(edit.dataset.edit));
      view.editing = p.id;
      setDate($("#np-date"), p.date);
      $("#np-amount").value = amountText(p.amount);
      $("#np-mode").value = p.mode;
      $("#np-fee").value = p.fee ? amountText(p.fee) : "";
      $("#np-note").value = p.note ?? "";
      $("#np-add").textContent = t("common.save");
      $("#np-cancel").hidden = false;
      markClean($("#np-add").parentElement); // only what is typed next is unsaved
      $("#np-amount").focus();
      return;
    }
    const btn = e.target.closest("[data-delete]");
    if (!btn || !confirm(t("mortgages.confirm_delete_prepayment"))) return;
    try {
      await api(`/api/prepayments/${btn.dataset.delete}`, { method: "DELETE" });
      if (view.editing === Number(btn.dataset.delete)) resetPrepaymentForm();
      await afterChange();
    } catch (err) {
      setError($("#mortgage-error"), err);
    }
  });

  $("#ns-add").addEventListener("click", async () => {
    try {
      const p = readPrepayment("ns");
      if ($("#ns-monthly").checked) view.monthly = { start: p.date, amount: p.amount, mode: p.mode, fee: p.fee }; // one at a time
      else {
        view.simulated.push(p);
        view.simulated.sort((a, b) => a.date.localeCompare(b.date));
      }
      $("#ns-amount").value = "";
      $("#ns-fee").value = "";
      setError($("#mortgage-error"), null);
      await simulate();
    } catch (err) {
      setError($("#mortgage-error"), err);
    }
  });
  $("#ns-clear").addEventListener("click", () => { view.simulated = []; view.monthly = null; simulate().catch(showMortgageError); });
  $("#simulation-table").addEventListener("click", async (e) => {
    if (e.target.closest("[data-remove-monthly]")) {
      view.monthly = null;
      simulate().catch(showMortgageError);
      return;
    }
    const remove = e.target.closest("[data-remove]"), save = e.target.closest("[data-save]");
    if (!remove && !save) return;
    const i = Number((remove ?? save).dataset[remove ? "remove" : "save"]);
    try {
      // Saved as a real prepayment: it leaves the simulation (which now compares with it included)
      if (save) await api(`/api/mortgages/${view.mortgage.id}/prepayments`, { json: view.simulated[i] });
      view.simulated.splice(i, 1);
      if (save) await afterChange(); // simulates again when some are left
      if (!save || !simulating()) await simulate();
    } catch (err) {
      setError($("#mortgage-error"), err);
    }
  });
}

function showMortgageError(err) {
  setError($("#mortgage-error"), err);
}
