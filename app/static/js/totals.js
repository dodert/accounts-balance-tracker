// The boxes with the totals per currency and all of them in the default currency.
import { api } from "./api.js";
import { $, esc, showError } from "./dom.js";
import { changeCell, fmtDate, money, signedMoney } from "./format.js";
import { ALL_CURRENCIES, CONVERTED, state } from "./state.js";

// --- Totals ---

// The big figure: available now (main colour) and, if part is not available yet, the total with it
// `conv` (a box in another currency): {rate, currency} to also show each figure in the default currency, small
export const converted = (value, conv) => (conv ? Math.round(value * conv.rate * 100) / 100 : null);
export const convertedLine = (value, conv) => (conv ? `<div class="tile-converted">≈ ${esc(money(converted(value, conv), conv.currency))}</div>` : "");
export const figures = (c, conv = null) => (c.available !== c.total ? `
      <div class="tile-figures">
        <div><div class="tile-value">${esc(money(c.available, c.currency))}</div>${convertedLine(c.available, conv)}<div class="tile-caption">${esc(t("totals.available_now"))}</div></div>
        <div class="tile-locked"><div class="tile-value">${esc(money(c.total, c.currency))}</div>${convertedLine(c.total, conv)}<div class="tile-caption">🔒 ${esc(t("totals.with_locked", { locked: money(c.total - c.available, c.currency) }))}</div></div>
      </div>` : `<div class="tile-value">${esc(money(c.total, c.currency))}</div>${convertedLine(c.total, conv)}`);

// A change in the box's currency and, with `conv`, the same change in the default currency
export function changeLine(current, previous, currency, percent, conv) {
  const own = changeCell(current, previous, currency, percent);
  if (!conv || !own) return own;
  const diff = Math.round((converted(current, conv) - converted(previous, conv)) * 100) / 100;
  return `${own} <span class="tile-converted-change">(≈ ${esc(signedMoney(diff, conv.currency))})</span>`;
}

// The box of one currency
export function currencyTile(c, conv = null) {
  return `
    <div class="tile">
      <div class="tile-label">${esc(t("totals.total", { currency: c.currency }))}</div>
      ${figures(c, conv)}
      <div class="tile-sub">${c.previous_total !== null ? `${changeLine(c.total, c.previous_total, c.currency, false, conv)} ${esc(t("totals.since", { date: fmtDate(c.previous_date) }))}` : esc(t("totals.accounts", { n: c.accounts }))}</div>
      ${c.first_total !== null ? `<div class="tile-sub">${changeLine(c.total, c.first_total, c.currency, true, conv)} ${esc(t("totals.since_first", { date: fmtDate(c.first_date) }))}</div>` : ""}
    </div>`;
}

// Every box: with the combined total (when there is one), the other currencies also in the default one at today's rate
export function renderTotals(totals, combined = null) {
  const rates = Object.fromEntries((combined?.rates ?? []).map((r) => [r.base, r.rate]));
  const conv = (c) => (combined && c.currency !== combined.currency && rates[c.currency] !== undefined
    ? { rate: rates[c.currency], currency: combined.currency } : null);
  $("#totals").innerHTML = (combined ? combinedTile(combined) : "") + totals.map((c) => currencyTile(c, conv(c))).join("");
}

// Every currency added up in the default one, at today's exchange rates (fetched once a day by the server)
export function combinedTile(c) {
  const rate = (r) => `${esc(t("fx.rate", { base: r.base, rate: new Intl.NumberFormat(fmtLocale(), { maximumFractionDigits: 4 }).format(r.rate), quote: r.quote }))}` +
    ` <span class="fx-date">${esc(t(r.stale ? "fx.stale" : "fx.from", { date: fmtDate(r.rate_date) }))}</span>`;
  return `
    <div class="tile tile-combined">
      <div class="tile-label">${esc(t("totals.combined", { currency: c.currency }))}
        <button type="button" class="icon fx-refresh" title="${esc(t("fx.refresh"))}" aria-label="${esc(t("fx.refresh"))}">↻</button></div>
      ${figures(c)}
      ${c.rates.map((r) => `<div class="tile-sub">${rate(r)}</div>`).join("")}
      ${c.missing.length ? `<div class="tile-sub error">${esc(t("fx.missing", { currencies: c.missing.join(", ") }))}</div>` : ""}
    </div>`;
}

export async function loadTotals() {
  const totals = await api("/api/summary");
  state.totals = totals;
  renderTotals(totals);
  const currencies = totals.map((c) => c.currency);
  state.currencies = currencies;
  const select = $("#chart-currency");
  const multi = currencies.length > 1;
  const target = state.about?.default_currency ?? "EUR";
  select.innerHTML = (multi ? `<option value="${ALL_CURRENCIES}">${esc(t("chart.all_currencies"))}</option>` +
    `<option value="${CONVERTED}">${esc(t("chart.converted", { currency: target }))}</option>` : "") +
    currencies.map((c) => `<option>${esc(c)}</option>`).join("");
  select.hidden = !multi;
  // With more than one currency, all of them are shown until one is picked
  if (!currencies.includes(state.currency) && !(multi && [ALL_CURRENCIES, CONVERTED].includes(state.currency))) state.currency = multi ? ALL_CURRENCIES : currencies[0] ?? null;
  select.value = state.currency ?? "";
  // All currencies in the default one, only with more than one currency. Not awaited: fetching the
  // rate can take a while (or fail), and the other boxes and the charts must not wait for it
  if (totals.length > 1) {
    const request = (state.combinedRequest = api("/api/summary/combined").catch(() => null));
    request.then((combined) => {
      if (combined && request === state.combinedRequest) renderTotals(totals, combined);
    });
  }
}

export function initTotals() {
  // Refresh the exchange rates now (only the currencies and the date go to the service, never amounts)
  $("#totals").addEventListener("click", async (e) => {
    const btn = e.target.closest(".fx-refresh");
    if (!btn) return;
    btn.disabled = true;
    btn.classList.add("spinning");
    try {
      renderTotals(state.totals, await api("/api/fx/refresh", { method: "POST" }));
    } catch (err) {
      showError(err);
      btn.disabled = false;
      btn.classList.remove("spinning");
    }
  });
}
