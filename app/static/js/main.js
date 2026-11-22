// Start: loads everything and wires every part of the page. The other modules are in this folder, one
// per part of the page; i18n.js (t, initI18n...) is a classic script loaded before, shared with the other pages.
import { initAccounts, loadAccounts, loadContributions, loadMilestones } from "./accounts.js";
import { api } from "./api.js";
import { initCharts, loadHistory } from "./charts.js";
import { initDemo, renderDemo } from "./demo.js";
import { initDialogs } from "./dialogs.js";
import { $, showError } from "./dom.js";
import { initFormat } from "./format.js";
import { initMenu } from "./menu.js";
import { initModals } from "./modal.js";
import { initMortgages, loadMortgages } from "./mortgages.js";
import { initSettings, renderTimeZoneBadge } from "./settings.js";
import { state } from "./state.js";
import { initTotals, loadTotals } from "./totals.js";
import { initTour, maybeStartTour } from "./tour.js";

export async function refresh() {
  await Promise.all([loadAccounts(), loadTotals(), loadMilestones(), loadContributions(), loadMortgages()]);
  await loadHistory();
}

// --- Start ---

initFormat();
initTotals();
initAccounts();
initCharts();
initModals();
initMenu();
initDialogs();
initMortgages();
initSettings();
initDemo();
initTour();

(async () => {
  await initI18n();
  renderTimeZoneBadge();
  state.about = await api("/api/about");
  $("#version").textContent = `v${state.about.version}`;
  $("#logout-btn").hidden = !state.about.auth;
  $("#account-link").hidden = !state.about.user;
  const daysLeft = state.about.days_left;
  $("#expiry-banner").hidden = !(daysLeft !== null && daysLeft !== undefined && daysLeft <= 3);
  $("#expiry-banner").textContent = t("account.expires_soon", { n: daysLeft ?? 0 });
  $("#account-link").textContent = state.about.user ? `👤 ${state.about.user}` : "";
  renderDemo();
  await refresh();
  await maybeStartTour();
})().catch(showError);
