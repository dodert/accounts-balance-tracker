// What the page shows now: data from the server and the user's choices.

export const ACCOUNT_TYPES = ["bank", "savings", "investment", "pension", "crypto", "cash", "loan", "other"];
// The types whose account dialog has contributions (money put in), drawn next to the balance on the charts
export const INVESTMENT_TYPES = ["investment", "pension", "crypto"];

// ALL_CURRENCIES: the charts of every currency, one after the other, each in its own currency
export const ALL_CURRENCIES = "*";
// CONVERTED: every account converted into the default currency (every date at today's rate), on one chart
export const CONVERTED = "=";
export const state = { about: null, accounts: [], milestones: [], contributions: [], mortgages: [], histories: [], currencies: [], mode: "amounts", currency: null, editing: null, maximized: null, group: savedGroup(), trendMode: savedTrendMode() };

// Chart grouping (by account or by institution), remembered in this browser
export function savedGroup() {
  try { return localStorage.getItem("abt-group") === "institution" ? "institution" : "account"; } catch { return "account"; }
}

// What the chart's background compares (#148): "gain" (the default) = the value minus what was contributed, for
// the accounts with contributions; "value" = the balance alone. Remembered in this browser
export function savedTrendMode() {
  try { return localStorage.getItem("abt-trend") === "value" ? "value" : "gain"; } catch { return "gain"; }
}
export function setTrendMode(mode) {
  state.trendMode = mode === "value" ? "value" : "gain";
  try { localStorage.setItem("abt-trend", state.trendMode); } catch {}
}
