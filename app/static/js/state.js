// What the page shows now: data from the server and the user's choices.

export const ACCOUNT_TYPES = ["bank", "savings", "investment", "pension", "crypto", "cash", "loan", "other"];

// ALL_CURRENCIES: the charts of every currency, one after the other, each in its own currency
export const ALL_CURRENCIES = "*";
// CONVERTED: every account converted into the default currency (each date at that date's rate), on one chart
export const CONVERTED = "=";
export const state = { about: null, accounts: [], milestones: [], histories: [], currencies: [], mode: "amounts", currency: null, editing: null, maximized: null, group: savedGroup() };

// Chart grouping (by account or by institution), remembered in this browser
export function savedGroup() {
  try { return localStorage.getItem("abt-group") === "institution" ? "institution" : "account"; } catch { return "account"; }
}
