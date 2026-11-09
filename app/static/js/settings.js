// Settings: language, regional format and time zone; and the list of daily backups made by the server.
import { renderAccounts } from "./accounts.js";
import { api, browserTimeZone, savedTimeZone, setTimeZone } from "./api.js";
import { renderChart } from "./charts.js";
import { renderDemo } from "./demo.js";
import { $, esc } from "./dom.js";
import { REGIONAL_LOCALES } from "./format.js";
import { loadTotals } from "./totals.js";

export function renderLocaleOptions() {
  const names = new Intl.DisplayNames([i18n.lang], { type: "language" });
  const sample = (code) => new Intl.DateTimeFormat(code, { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "UTC" }).format(new Date(Date.UTC(2026, 11, 31)))
    + " · " + new Intl.NumberFormat(code, { minimumFractionDigits: 2 }).format(1234.5);
  const current = savedLocale();
  const codes = current && !REGIONAL_LOCALES.includes(current) ? [current, ...REGIONAL_LOCALES] : REGIONAL_LOCALES;
  $("#locale-select").innerHTML = `<option value="">${esc(t("settings.locale_auto"))}</option>` +
    codes.map((code) => `<option value="${esc(code)}">${esc(names.of(code) ?? code)} — ${esc(sample(code))}</option>`).join("");
  $("#locale-select").value = current;
}

// The time zone in use, next to the title; a click opens the settings to change it
export function renderTimeZoneBadge() {
  const zone = savedTimeZone() || browserTimeZone();
  $("#tz-badge").textContent = `🕒 ${zone.replaceAll("_", " ")}`;
  $("#tz-badge").title = t(savedTimeZone() ? "settings.time_zone_chosen" : "settings.time_zone_browser", { zone });
}

// "" = the browser's own time zone; else any zone the browser knows
export function renderTimeZoneOptions() {
  const current = savedTimeZone();
  const zones = Intl.supportedValuesOf?.("timeZone") ?? [];
  if (current && !zones.includes(current)) zones.unshift(current);
  $("#tz-select").innerHTML = `<option value="">${esc(t("settings.time_zone_auto", { zone: browserTimeZone() }))}</option>` +
    zones.map((zone) => `<option value="${esc(zone)}">${esc(zone.replaceAll("_", " "))}</option>`).join("");
  $("#tz-select").value = current;
}

// The daily backups the server made (app/backups.py), newest first: when (in the user's time zone) and size
export async function renderBackups() {
  const list = $("#backups-list"), hint = $("#backups-hint");
  list.innerHTML = "";
  let data;
  try {
    data = await api("/api/backups");
  } catch {
    hint.textContent = t("backups.error");
    return;
  }
  hint.textContent = data.keep > 0 ? t("backups.hint", { keep: data.keep }) : t("backups.off");
  if (data.keep > 0 && !data.backups.length) hint.textContent += " " + t("backups.none");
  const zone = savedTimeZone();
  let when;
  try {
    when = new Intl.DateTimeFormat(fmtLocale(), { dateStyle: "medium", timeStyle: "short", ...(zone ? { timeZone: zone } : {}) });
  } catch { // a zone this browser doesn't know: its own
    when = new Intl.DateTimeFormat(fmtLocale(), { dateStyle: "medium", timeStyle: "short" });
  }
  const size = (bytes) => (bytes < 1024 * 1024
    ? new Intl.NumberFormat(fmtLocale(), { style: "unit", unit: "kilobyte", maximumFractionDigits: 0 }).format(Math.max(1, bytes / 1024))
    : new Intl.NumberFormat(fmtLocale(), { style: "unit", unit: "megabyte", maximumFractionDigits: 1 }).format(bytes / 1024 / 1024));
  list.innerHTML = data.backups.map((b) =>
    `<li data-date="${esc(b.date)}">✓ ${esc(when.format(new Date(b.made_at)))} <span class="hint">· ${esc(size(b.size))}</span></li>`).join("");
}

export function initSettings() {
  $("#tz-badge").addEventListener("click", () => $("#settings-btn").click());
  $("#settings-btn").addEventListener("click", async () => {
    const languages = await api("/api/languages");
    $("#lang-select").innerHTML = languages.map((l) => `<option value="${esc(l.code)}">${esc(l.name)}</option>`).join("");
    $("#lang-select").value = i18n.lang;
    renderLocaleOptions();
    renderTimeZoneOptions();
    renderBackups();
    $("#settings-dlg").showModal();
  });

  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setLang($("#lang-select").value);
    setLocale($("#locale-select").value);
    setTimeZone($("#tz-select").value);
    $("#settings-dlg").close();
    await initI18n();
    renderTimeZoneBadge();
    renderDemo();
    renderAccounts();
    await loadTotals();
    renderChart();
  });
}
