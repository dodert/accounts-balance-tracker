// Settings: language and regional format.
import { renderAccounts } from "./accounts.js";
import { api } from "./api.js";
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

export function initSettings() {
  $("#settings-btn").addEventListener("click", async () => {
    const languages = await api("/api/languages");
    $("#lang-select").innerHTML = languages.map((l) => `<option value="${esc(l.code)}">${esc(l.name)}</option>`).join("");
    $("#lang-select").value = i18n.lang;
    renderLocaleOptions();
    $("#settings-dlg").showModal();
  });

  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setLang($("#lang-select").value);
    setLocale($("#locale-select").value);
    $("#settings-dlg").close();
    await initI18n();
    renderDemo();
    renderAccounts();
    await loadTotals();
    renderChart();
  });
}
