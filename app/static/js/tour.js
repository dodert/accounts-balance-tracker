// Guided tour (#156): shown once per user (the version seen is kept in their database: /api/tour) and again from
// the "?" button. driver.js (MIT, app/static/vendor/driver/) draws the highlight and the popovers.
import { driver } from "../vendor/driver/driver.min.js";
import { api } from "./api.js";
import { $, esc, showError } from "./dom.js";

// Raise it when the tour changes enough to be shown again to everyone who saw an older one
export const TOUR_VERSION = 2; // 2: the first step says what the app is and is not (#202)

// [element, i18n key]: no element = a popover in the middle of the page
const STEPS = [
  [null, "intro"],
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

// The first step (#202): what the app is, a list of what it is not, and how to close or start the tour again
const introText = () =>
  `<p>${esc(t("tour.intro_is"))}</p><p><strong>${esc(t("tour.intro_not_title"))}</strong></p><ul>${[1, 2, 3, 4]
    .map((n) => `<li>${esc(t(`tour.intro_not_${n}`))}</li>`)
    .join("")}</ul><p>${esc(t("tour.intro_close"))}</p>`;

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
    popover: {
      title: t(`tour.${key}_title`),
      description: key === "intro" ? introText() : esc(t(`tour.${key}`)),
      ...(key === "intro" ? { popoverClass: "abt-tour abt-tour-intro" } : {}),
    },
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
