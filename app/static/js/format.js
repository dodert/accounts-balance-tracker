// Formatting and reading amounts, dates and changes in the user's regional format.
import { $, $$, esc } from "./dom.js";

// --- Formatting ---

export const money = (value, currency, opts = {}) => {
  if (value === null || value === undefined) return "";
  try {
    return new Intl.NumberFormat(fmtLocale(), { style: "currency", currency, ...opts }).format(value);
  } catch {
    return `${Number(value).toFixed(2)} ${currency}`;
  }
};
export const compactMoney = (value, currency, digits = 1) => money(value, currency, { notation: "compact", maximumFractionDigits: digits });
// Axis labels: compact (1,2 K), with more decimals when that would repeat a label, or in full
export function axisLabels(ticks, currency) {
  for (const digits of [1, 2]) {
    const labels = ticks.map((v) => compactMoney(v, currency, digits));
    if (new Set(labels).size === labels.length) return labels;
  }
  return ticks.map((v) => money(v, currency, { maximumFractionDigits: 0 }));
}
// An amount for an input box, in the regional format without thousands separators (1234,50)
export const amountText = (value) => (value === null || value === undefined ? "" : new Intl.NumberFormat(fmtLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false }).format(value));
export const signedMoney = (value, currency) => (value > 0 ? "+" : "") + money(value, currency);
export const fmtDate = (iso, opts = { dateStyle: "medium" }) => (iso ? new Intl.DateTimeFormat(fmtLocale(), { ...opts, timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`)) : "");
export const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// Date fields: a text box in the regional format (31/12/2026, 12/31/2026, 31.12.2026...), since the
// browser's own date box always uses the browser's language, plus a button for its calendar
export const REGIONAL_LOCALES = ["es-ES", "en-GB", "en-US", "de-DE", "fr-FR", "it-IT", "pt-PT", "pt-BR", "nl-NL", "ca-ES", "es-MX", "ja-JP", "sv-SE"];

export function dateLayout() {
  const parts = new Intl.DateTimeFormat(fmtLocale(), { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "UTC" })
    .formatToParts(new Date(Date.UTC(2026, 11, 31)));
  const order = parts.filter((p) => ["day", "month", "year"].includes(p.type)).map((p) => p.type);
  const sep = parts.find((p) => p.type === "literal")?.value ?? "/";
  return { order, sep };
}

export function isoToText(iso) {
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  const { order, sep } = dateLayout();
  const values = { year, month, day };
  return order.map((k) => values[k]).join(sep);
}

// The text in the regional format (or YYYY-MM-DD) -> YYYY-MM-DD; error if it isn't a real date
export function textToIso(text) {
  const value = String(text).trim();
  let year, month, day;
  const iso = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (iso) [, year, month, day] = iso;
  else {
    const nums = value.split(/\D+/).filter(Boolean);
    if (nums.length !== 3) throw new Error(t("error.date", { value: text }));
    ({ year, month, day } = Object.fromEntries(dateLayout().order.map((k, i) => [k, nums[i]])));
    if (year.length === 2) year = `20${year}`;
  }
  const d = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (year.length !== 4 || d.getUTCMonth() !== Number(month) - 1 || d.getUTCDate() !== Number(day)) throw new Error(t("error.date", { value: text }));
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function setDate(input, iso) {
  input.value = isoToText(iso);
  input.placeholder = isoToText(today());
}

export const getDate = (input) => textToIso(input.value);
// Not available yet: available_from after today (ISO dates compare as text)
export const notAvailableYet = (a) => !!a?.available_from && a.available_from > today();
export const lockBadge = (a) => (notAvailableYet(a) ? ` <span class="badge lock" title="${esc(t("account.available_hint"))}">🔒 ${esc(t("accounts.available_on", { date: fmtDate(a.available_from) }))}</span>` : "");

// "1.234,56", "1,234.56", "1234.56" or "-50" -> number; the last of "," and "." is the decimal mark
export function parseAmount(text) {
  let value = String(text).replace(/[\s €$£]/g, "");
  if (!value) return null;
  if (value.includes(",") && value.includes(".")) {
    value = value.lastIndexOf(",") > value.lastIndexOf(".") ? value.replace(/\./g, "") : value.replace(/,/g, "");
  }
  value = value.replace(",", ".");
  const n = Number(value);
  if (!Number.isFinite(n)) throw new Error(t("error.amount", { value: text }));
  return Math.round(n * 100) / 100;
}

// The change from `previous` to `current`, coloured; with percent=true also in % of `previous`
export function changeCell(current, previous, currency, percent = false) {
  if (current === null || previous === null || previous === undefined) return "";
  const diff = Math.round((current - previous) * 100) / 100;
  const cls = diff > 0 ? "up" : diff < 0 ? "down" : "";
  const arrow = diff > 0 ? "▲ " : diff < 0 ? "▼ " : "";
  const pct = percent && previous !== 0
    ? ` <span class="pct">(${esc(new Intl.NumberFormat(fmtLocale(), { style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(diff / Math.abs(previous)))})</span>`
    : "";
  return `<span class="${cls}"><span class="arrow">${arrow}</span>${esc(signedMoney(diff, currency))}${pct}</span>`;
}

export const fmtPct = (v, digits = 1) => new Intl.NumberFormat(fmtLocale(), { style: "percent", maximumFractionDigits: digits, signDisplay: "exceptZero" }).format(v / 100);

export function initFormat() {
  // Every .date-field: the calendar button opens the browser's date picker
  $$(".date-field").forEach((field) => {
    const text = $(".date-text", field), native = $(".date-native", field);
    $(".date-btn", field).addEventListener("click", () => {
      try { native.value = textToIso(text.value); } catch { native.value = today(); }
      try { native.showPicker(); } catch { native.focus(); native.click(); }
    });
    native.addEventListener("change", () => { if (native.value) setDate(text, native.value); });
  });
}
