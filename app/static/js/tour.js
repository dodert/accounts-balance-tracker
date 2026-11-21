// Guided tour (#156): shown once per user (the version seen is kept in their database: /api/tour) and again from
// the "?" button. driver.js (MIT, app/static/vendor/driver/) draws the highlight and the popovers.
import { driver } from "../vendor/driver/driver.min.js";
import { api } from "./api.js";
import { $, showError } from "./dom.js";

// Raise it when the tour changes enough to be shown again to everyone who saw an older one
export const TOUR_VERSION = 1;

// [element, i18n key]: no element = a popover in the middle of the page
const STEPS = [
  [null, "welcome"],
  ["#totals", "totals"],
  ["#chart-panel", "chart"],
  ["#accounts-panel", "accounts"],
  ["#add-account-btn", "add_account"],
  ["#update-btn", "update"],
  ["#milestone-btn", "milestone"],
  ["#mortgages-panel", "mortgages"],
  ["#data-menu", "data"],
  ["#settings-btn", "settings"],
  ["#columns-btn", "columns"],
  ["#tour-btn", "again"],
];

// Shown on the page: not hidden, nor out of the layout (a narrow screen hides some)
const visible = (selector) => {
  const el = $(selector);
  return !!el && !el.closest("[hidden]") && el.getClientRects().length > 0;
};

let active = null;

export function startTour() {
  if (active?.isActive()) return;
  const steps = STEPS.filter(([selector]) => !selector || visible(selector)).map(([selector, key]) => ({
    ...(selector ? { element: selector } : {}),
    popover: { title: t(`tour.${key}_title`), description: t(`tour.${key}`) },
  }));
  active = driver({
    steps,
    showProgress: true,
    progressText: t("tour.progress"),
    nextBtnText: t("tour.next"),
    prevBtnText: t("tour.previous"),
    doneBtnText: t("tour.done"),
    closeBtnLabel: t("tour.close"),
    popoverClass: "abt-tour",
    stagePadding: 6,
    // Closed at any step (×, Esc, the last "Done"): seen, not shown again on its own
    onDestroyed: () => {
      active = null;
      api("/api/tour", { method: "PUT", json: { seen: TOUR_VERSION } }).catch(showError);
    },
  });
  active.drive();
}

// On its own the first time (or when the tour is newer than the one this user saw)
export async function maybeStartTour() {
  const { seen } = await api("/api/tour");
  if (seen < TOUR_VERSION) startTour();
}

export function initTour() {
  $("#tour-btn").addEventListener("click", startTour);
}
