const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const ACCOUNT_TYPES = ["bank", "savings", "investment", "pension", "crypto", "cash", "loan", "other"];
const SERIES_SLOTS = 8; // categorical colours; more accounts than this fold into "Other"
const SVG_NS = "http://www.w3.org/2000/svg";

const api = async (path, opts = {}) => {
  if (opts.json !== undefined) {
    opts = { ...opts, method: opts.method ?? "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.json) };
  }
  const res = await fetch(path, opts);
  if (res.status === 401) location.href = "/login"; // session expired
  if (!res.ok) {
    let detail = await res.text();
    try {
      const body = JSON.parse(detail).detail;
      detail = Array.isArray(body) ? body.map((e) => `${e.loc?.slice(-1)[0] ?? ""}: ${e.msg}`).join("; ") : body;
    } catch {}
    throw new Error(detail || res.statusText);
  }
  return res.status === 204 ? null : res.json();
};

const state = { about: null, accounts: [], history: null, mode: "total", currency: null, editing: null };

// --- Formatting ---

const money = (value, currency, opts = {}) => {
  if (value === null || value === undefined) return "";
  try {
    return new Intl.NumberFormat(fmtLocale(), { style: "currency", currency, ...opts }).format(value);
  } catch {
    return `${Number(value).toFixed(2)} ${currency}`;
  }
};
const compactMoney = (value, currency, digits = 1) => money(value, currency, { notation: "compact", maximumFractionDigits: digits });
// Axis labels: compact (1,2 K), with more decimals when that would repeat a label, or in full
function axisLabels(ticks, currency) {
  for (const digits of [1, 2]) {
    const labels = ticks.map((v) => compactMoney(v, currency, digits));
    if (new Set(labels).size === labels.length) return labels;
  }
  return ticks.map((v) => money(v, currency, { maximumFractionDigits: 0 }));
}
// An amount for an input box, in the regional format without thousands separators (1234,50)
const amountText = (value) => (value === null || value === undefined ? "" : new Intl.NumberFormat(fmtLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false }).format(value));
const signedMoney = (value, currency) => (value > 0 ? "+" : "") + money(value, currency);
const fmtDate = (iso, opts = { dateStyle: "medium" }) => (iso ? new Intl.DateTimeFormat(fmtLocale(), { ...opts, timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`)) : "");
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// Date fields: a text box in the regional format (31/12/2026, 12/31/2026, 31.12.2026...), since the
// browser's own date box always uses the browser's language, plus a button for its calendar
const REGIONAL_LOCALES = ["es-ES", "en-GB", "en-US", "de-DE", "fr-FR", "it-IT", "pt-PT", "pt-BR", "nl-NL", "ca-ES", "es-MX", "ja-JP", "sv-SE"];

function dateLayout() {
  const parts = new Intl.DateTimeFormat(fmtLocale(), { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "UTC" })
    .formatToParts(new Date(Date.UTC(2026, 11, 31)));
  const order = parts.filter((p) => ["day", "month", "year"].includes(p.type)).map((p) => p.type);
  const sep = parts.find((p) => p.type === "literal")?.value ?? "/";
  return { order, sep };
}

function isoToText(iso) {
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  const { order, sep } = dateLayout();
  const values = { year, month, day };
  return order.map((k) => values[k]).join(sep);
}

// The text in the regional format (or YYYY-MM-DD) -> YYYY-MM-DD; error if it isn't a real date
function textToIso(text) {
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

function setDate(input, iso) {
  input.value = isoToText(iso);
  input.placeholder = isoToText(today());
}

const getDate = (input) => textToIso(input.value);

// Every .date-field: the calendar button opens the browser's date picker
$$(".date-field").forEach((field) => {
  const text = $(".date-text", field), native = $(".date-native", field);
  $(".date-btn", field).addEventListener("click", () => {
    try { native.value = textToIso(text.value); } catch { native.value = today(); }
    try { native.showPicker(); } catch { native.focus(); native.click(); }
  });
  native.addEventListener("change", () => { if (native.value) setDate(text, native.value); });
});

// "1.234,56", "1,234.56", "1234.56" or "-50" -> number; the last of "," and "." is the decimal mark
function parseAmount(text) {
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

function changeCell(current, previous, currency) {
  if (current === null || previous === null || previous === undefined) return "";
  const diff = current - previous;
  const cls = diff > 0 ? "up" : diff < 0 ? "down" : "";
  const arrow = diff > 0 ? "▲ " : diff < 0 ? "▼ " : "";
  return `<span class="${cls}">${arrow}${esc(signedMoney(diff, currency))}</span>`;
}

// --- Totals ---

async function loadTotals() {
  const totals = await api("/api/summary");
  $("#totals").innerHTML = totals.length ? totals.map((c) => `
    <div class="tile">
      <div class="tile-label">${esc(t("totals.total", { currency: c.currency }))}</div>
      <div class="tile-value">${esc(money(c.total, c.currency))}</div>
      <div class="tile-sub">${c.previous_total !== null ? `${changeCell(c.total, c.previous_total, c.currency)} ${esc(t("totals.since", { date: fmtDate(c.previous_date) }))}` : esc(t("totals.accounts", { n: c.accounts }))}</div>
    </div>`).join("") : "";
  const currencies = totals.map((c) => c.currency);
  const select = $("#chart-currency");
  select.innerHTML = currencies.map((c) => `<option>${esc(c)}</option>`).join("");
  select.hidden = currencies.length < 2;
  if (!currencies.includes(state.currency)) state.currency = currencies[0] ?? null;
  select.value = state.currency ?? "";
}

// --- Accounts ---

async function loadAccounts() {
  state.accounts = await api("/api/accounts");
  renderAccounts();
}

function renderAccounts() {
  const showArchived = $("#show-archived").checked;
  const rows = state.accounts.filter((a) => showArchived || !a.archived);
  $("#empty").hidden = state.accounts.length > 0;
  $("#accounts-table").hidden = rows.length === 0;
  $("#accounts-table tbody").innerHTML = rows.map((a) => `
    <tr data-id="${a.id}" tabindex="0" class="${a.archived ? "archived" : ""}">
      <td><span class="t">${esc(a.name)}</span>${a.institution ? `<span class="sub">${esc(a.institution)}</span>` : ""}${a.archived ? ` <span class="badge">${esc(t("accounts.archived"))}</span>` : ""}</td>
      <td>${esc(t(`type.${a.type}`))}</td>
      <td class="num">${esc(money(a.balance, a.currency))}</td>
      <td class="num">${changeCell(a.balance, a.previous_balance, a.currency)}</td>
      <td>${esc(fmtDate(a.balance_date))}</td>
    </tr>`).join("");
}

$("#show-archived").addEventListener("change", renderAccounts);
$("#accounts-table tbody").addEventListener("click", (e) => {
  const row = e.target.closest("tr[data-id]");
  if (row) openAccount(Number(row.dataset.id));
});
$("#accounts-table tbody").addEventListener("keydown", (e) => {
  const row = e.target.closest("tr[data-id]");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openAccount(Number(row.dataset.id)); }
});

// --- Chart ---

function sinceDate() {
  const months = $("#chart-range").value;
  if (months === "all") return null;
  const d = new Date();
  d.setMonth(d.getMonth() - Number(months));
  return d.toISOString().slice(0, 10);
}

async function loadHistory() {
  if (!state.currency) { state.history = null; renderChart(); return; }
  const since = sinceDate();
  state.history = await api(`/api/history?currency=${encodeURIComponent(state.currency)}${since ? `&since=${since}` : ""}`);
  renderChart();
}

// The series to draw: the total, or one per account (colour by account id, so it stays the same
// whatever the range or archived filter; past 8 accounts the rest fold into "Other")
function chartSeries(h) {
  if (state.mode === "total") return [{ name: t("chart.total"), values: h.total, slot: 1 }];
  const accounts = h.accounts.filter((a) => a.values.some((v) => v !== null));
  const ids = state.accounts.filter((a) => a.currency === h.currency).map((a) => a.id).sort((a, b) => a - b);
  const slotOf = (id) => ids.indexOf(id) + 1;
  const series = accounts.filter((a) => slotOf(a.account_id) <= SERIES_SLOTS - (ids.length > SERIES_SLOTS ? 1 : 0))
    .map((a) => ({ name: a.name, values: a.values, slot: slotOf(a.account_id) }));
  const rest = accounts.filter((a) => !series.some((s) => s.name === a.name));
  if (rest.length) {
    const values = h.dates.map((_, i) => rest.reduce((sum, a) => (a.values[i] === null ? sum : (sum ?? 0) + a.values[i]), null));
    series.push({ name: t("chart.other"), values, slot: SERIES_SLOTS });
  }
  return series.sort((a, b) => a.slot - b.slot);
}

function niceTicks(min, max, count = 5) {
  if (min === max) { min -= Math.abs(min) * 0.1 || 1; max += Math.abs(max) * 0.1 || 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let v = start; ; v += step) {
    ticks.push(Math.round(v * 100) / 100);
    if (v >= max - step * 1e-9) break;
  }
  return ticks;
}

function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  parent?.appendChild(el);
  return el;
}

function renderChart() {
  const box = $("#chart");
  box.innerHTML = "";
  $("#chart-legend").innerHTML = "";
  $("#chart-table").innerHTML = "";
  const h = state.history;
  if (!h || h.dates.length === 0) {
    box.innerHTML = `<p class="hint chart-empty">${esc(t("chart.empty"))}</p>`;
    $("#chart-table-wrap").hidden = true;
    return;
  }
  $("#chart-table-wrap").hidden = false;
  const series = chartSeries(h);
  const width = Math.max(box.clientWidth, 280);
  const height = width < 500 ? 220 : 300;
  const pad = { top: 12, right: 16, bottom: 28, left: 64 };
  const times = h.dates.map((d) => Date.parse(`${d}T00:00:00Z`));
  const values = series.flatMap((s) => s.values).filter((v) => v !== null);
  const ticks = niceTicks(Math.min(...values), Math.max(...values));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const t0 = times[0], t1 = times[times.length - 1];
  const x = (time) => pad.left + (t1 === t0 ? (width - pad.left - pad.right) / 2 : ((time - t0) / (t1 - t0)) * (width - pad.left - pad.right));
  const y = (v) => pad.top + (1 - (v - y0) / (y1 - y0)) * (height - pad.top - pad.bottom);

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": t("chart.title") }, box);
  // Grid and y axis
  const labels = axisLabels(ticks, h.currency);
  ticks.forEach((tick, i) => {
    svg("line", { x1: pad.left, x2: width - pad.right, y1: y(tick), y2: y(tick), class: tick === 0 ? "zero" : "grid" }, root);
    svg("text", { x: pad.left - 8, y: y(tick), class: "axis", "text-anchor": "end", "dominant-baseline": "middle" }, root).textContent = labels[i];
  });
  // x axis: up to ~6 date labels
  const labelCount = Math.min(times.length, Math.max(2, Math.floor((width - pad.left) / 110)));
  const shown = new Set();
  for (let i = 0; i < labelCount; i++) {
    const idx = labelCount === 1 ? 0 : Math.round((i * (times.length - 1)) / (labelCount - 1));
    if (shown.has(idx)) continue;
    shown.add(idx);
    const anchor = idx === 0 ? "start" : idx === times.length - 1 ? "end" : "middle";
    svg("text", { x: x(times[idx]), y: height - 8, class: "axis", "text-anchor": anchor }, root).textContent = fmtDate(h.dates[idx], { month: "short", year: "2-digit" });
  }
  // Lines (step-free: straight segments between recorded dates), with dots when there are few points
  for (const s of series) {
    const pts = s.values.map((v, i) => (v === null ? null : [x(times[i]), y(v)]));
    let d = "", pen = false;
    for (const p of pts) {
      if (!p) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
      pen = true;
    }
    if (state.mode === "total") {
      const first = pts.find(Boolean), last = [...pts].reverse().find(Boolean);
      const base = y(Math.max(y0, Math.min(0, y1)));
      svg("path", { d: `${d}L${last[0]},${base}L${first[0]},${base}Z`, class: "area", style: `fill: var(--s${s.slot})` }, root);
    }
    svg("path", { d, class: "line", style: `stroke: var(--s${s.slot})` }, root);
    if (times.length <= 24) {
      for (const p of pts) if (p) svg("circle", { cx: p[0], cy: p[1], r: 4, class: "dot", style: `fill: var(--s${s.slot})` }, root);
    }
  }
  // Hover: crosshair on the nearest date, tooltip with every series on it
  const cross = svg("line", { y1: pad.top, y2: height - pad.bottom, class: "crosshair", visibility: "hidden" }, root);
  const marks = series.map((s) => svg("circle", { r: 5, class: "hover-dot", style: `fill: var(--s${s.slot})`, visibility: "hidden" }, root));
  const hit = svg("rect", { x: pad.left, y: 0, width: width - pad.left - pad.right, height, fill: "transparent" }, root);
  const tooltip = $("#tooltip");
  const hide = () => { cross.setAttribute("visibility", "hidden"); marks.forEach((m) => m.setAttribute("visibility", "hidden")); tooltip.hidden = true; };
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("pointermove", (e) => {
    const rect = root.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    let idx = 0;
    times.forEach((time, i) => { if (Math.abs(x(time) - px) < Math.abs(x(times[idx]) - px)) idx = i; });
    const cx = x(times[idx]);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    series.forEach((s, i) => {
      const v = s.values[idx];
      marks[i].setAttribute("visibility", v === null ? "hidden" : "visible");
      if (v !== null) { marks[i].setAttribute("cx", cx); marks[i].setAttribute("cy", y(v)); }
    });
    const rows = series.filter((s) => s.values[idx] !== null).sort((a, b) => b.values[idx] - a.values[idx]);
    tooltip.innerHTML = `<div class="tt-date">${esc(fmtDate(h.dates[idx]))}</div>` + rows.map((s) =>
      `<div class="tt-row"><span class="swatch" style="background: var(--s${s.slot})"></span><span class="tt-name">${esc(s.name)}</span><span class="tt-value">${esc(money(s.values[idx], h.currency))}</span></div>`).join("") +
      (state.mode === "accounts" ? `<div class="tt-row tt-total"><span class="tt-name">${esc(t("chart.total"))}</span><span class="tt-value">${esc(money(h.total[idx], h.currency))}</span></div>` : "");
    tooltip.hidden = false;
    const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
    let left = e.clientX + 14, top = e.clientY - th - 10;
    if (left + tw > window.innerWidth - 8) left = e.clientX - tw - 14;
    if (top < 8) top = e.clientY + 14;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  });

  // Legend (only with more than one series) and the same data as a table
  if (series.length > 1) {
    $("#chart-legend").innerHTML = series.map((s) => `<span class="legend-item"><span class="swatch" style="background: var(--s${s.slot})"></span>${esc(s.name)}</span>`).join("");
  }
  $("#chart-table").innerHTML = `<thead><tr><th>${esc(t("update.date"))}</th>${series.map((s) => `<th class="num">${esc(s.name)}</th>`).join("")}${state.mode === "accounts" ? `<th class="num">${esc(t("chart.total"))}</th>` : ""}</tr></thead><tbody>` +
    h.dates.map((d, i) => `<tr><td>${esc(fmtDate(d))}</td>${series.map((s) => `<td class="num">${esc(money(s.values[i], h.currency))}</td>`).join("")}${state.mode === "accounts" ? `<td class="num">${esc(money(h.total[i], h.currency))}</td>` : ""}</tr>`).reverse().join("") + "</tbody>";
}

$$("#chart-panel .views button").forEach((btn) => btn.addEventListener("click", () => {
  state.mode = btn.dataset.mode;
  $$("#chart-panel .views button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  renderChart();
}));
$("#chart-currency").addEventListener("change", (e) => { state.currency = e.target.value; loadHistory().catch(showError); });
$("#chart-range").addEventListener("change", () => loadHistory().catch(showError));
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 150); });

async function refresh() {
  await Promise.all([loadAccounts(), loadTotals()]);
  await loadHistory();
}

function showError(err) {
  alert(err.message ?? String(err));
}

// --- Dialogs ---

$$("dialog .cancel").forEach((btn) => btn.addEventListener("click", () => btn.closest("dialog").close()));

function setError(el, err) {
  el.textContent = err ? err.message ?? String(err) : "";
  el.hidden = !err;
}

// Update balances
$("#update-btn").addEventListener("click", () => {
  const active = state.accounts.filter((a) => !a.archived);
  if (!active.length) { openAccount(null); return; }
  const form = $("#update-form");
  setDate(form.date, today());
  $("#update-rows").innerHTML = active.map((a) => `
    <label class="update-row">
      <span class="u-name">${esc(a.name)} <span class="sub">${esc(a.balance !== null ? t("update.last", { amount: money(a.balance, a.currency), date: fmtDate(a.balance_date) }) : t("update.never"))}</span></span>
      <span class="u-input"><input type="text" inputmode="decimal" data-id="${a.id}" value="${esc(amountText(a.balance))}" autocomplete="off"> <span class="cur">${esc(a.currency)}</span></span>
    </label>`).join("");
  setError($("#update-error"), null);
  $("#update-dlg").showModal();
});

$("#update-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const items = $$("#update-rows input").map((input) => ({ account_id: Number(input.dataset.id), amount: parseAmount(input.value) }))
      .filter((item) => item.amount !== null);
    await api("/api/balances/bulk", { json: { date: getDate(e.target.date), items } });
    $("#update-dlg").close();
    await refresh();
  } catch (err) {
    setError($("#update-error"), err);
  }
});

// Account dialog
$("#account-type").innerHTML = ACCOUNT_TYPES.map((type) => `<option value="${type}" data-i18n="type.${type}">${esc(t(`type.${type}`))}</option>`).join("");
$("#add-account-btn").addEventListener("click", () => openAccount(null));

async function openAccount(id) {
  const account = id === null ? null : state.accounts.find((a) => a.id === id);
  state.editing = account;
  const form = $("#account-form");
  form.reset();
  $("#account-dlg-title").textContent = account ? account.name : t("account.new");
  form.name.value = account?.name ?? "";
  form.institution.value = account?.institution ?? "";
  form.type.value = account?.type ?? "bank";
  form.currency.value = account?.currency ?? state.about?.default_currency ?? "EUR";
  form.notes.value = account?.notes ?? "";
  form.archived.checked = account?.archived ?? false;
  $("#account-delete").hidden = !account;
  $("#balances-section").hidden = !account;
  setError($("#account-error"), null);
  if (account) {
    setDate($("#nb-date"), today());
    $("#nb-amount").value = "";
    $("#nb-note").value = "";
    await loadBalances();
  }
  $("#account-dlg").showModal();
}

async function loadBalances() {
  const account = state.editing;
  const balances = await api(`/api/accounts/${account.id}/balances`);
  $("#balances-table tbody").innerHTML = balances.length ? balances.map((b, i) => `
    <tr>
      <td>${esc(fmtDate(b.date))}</td>
      <td class="num">${esc(money(b.amount, account.currency))}</td>
      <td class="num">${balances[i + 1] ? changeCell(b.amount, balances[i + 1].amount, account.currency) : ""}</td>
      <td class="note">${esc(b.note ?? "")}</td>
      <td class="actions"><button type="button" class="icon danger" data-delete="${b.id}" title="${esc(t("common.delete"))}" aria-label="${esc(t("common.delete"))}">✕</button></td>
    </tr>`).join("") : `<tr><td class="hint">${esc(t("account.no_balances"))}</td></tr>`;
}

$("#nb-add").addEventListener("click", async () => {
  try {
    const amount = parseAmount($("#nb-amount").value);
    if (amount === null) throw new Error(t("error.amount_required"));
    await api(`/api/accounts/${state.editing.id}/balances`, { json: { date: getDate($("#nb-date")), amount, note: $("#nb-note").value } });
    $("#nb-amount").value = "";
    $("#nb-note").value = "";
    setError($("#account-error"), null);
    await Promise.all([loadBalances(), refresh()]);
  } catch (err) {
    setError($("#account-error"), err);
  }
});

$("#balances-table").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-delete]");
  if (!btn || !confirm(t("account.confirm_delete_balance"))) return;
  try {
    await api(`/api/balances/${btn.dataset.delete}`, { method: "DELETE" });
    await Promise.all([loadBalances(), refresh()]);
  } catch (err) {
    setError($("#account-error"), err);
  }
});

$("#account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const data = {
    name: form.name.value, institution: form.institution.value, type: form.type.value,
    currency: form.currency.value.toUpperCase(), notes: form.notes.value, archived: form.archived.checked,
  };
  try {
    if (state.editing) await api(`/api/accounts/${state.editing.id}`, { method: "PATCH", json: data });
    else await api("/api/accounts", { json: data });
    $("#account-dlg").close();
    await refresh();
  } catch (err) {
    setError($("#account-error"), err);
  }
});

$("#account-delete").addEventListener("click", async () => {
  const account = state.editing;
  if (!confirm(t("account.confirm_delete", { name: account.name }))) return;
  try {
    await api(`/api/accounts/${account.id}`, { method: "DELETE" });
    $("#account-dlg").close();
    await refresh();
  } catch (err) {
    setError($("#account-error"), err);
  }
});

// CSV import
$("#csv-import-btn").addEventListener("click", () => {
  $("#data-menu").open = false;
  $("#csv-form").reset();
  $("#csv-result").hidden = true;
  $("#csv-dlg").showModal();
});

$("#csv-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#csv-result");
  try {
    const body = new FormData(e.target);
    const r = await api("/api/import/csv", { method: "POST", body });
    out.textContent = [t("csv.result", { accounts: r.accounts_created, created: r.balances_created, updated: r.balances_updated }), ...r.errors].join("\n");
    await refresh();
  } catch (err) {
    out.textContent = err.message;
  }
  out.hidden = false;
});
$("#csv-export-btn").addEventListener("click", () => { $("#data-menu").open = false; });

// Settings
function renderLocaleOptions() {
  const names = new Intl.DisplayNames([i18n.lang], { type: "language" });
  const sample = (code) => new Intl.DateTimeFormat(code, { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "UTC" }).format(new Date(Date.UTC(2026, 11, 31)))
    + " · " + new Intl.NumberFormat(code, { minimumFractionDigits: 2 }).format(1234.5);
  const current = savedLocale();
  const codes = current && !REGIONAL_LOCALES.includes(current) ? [current, ...REGIONAL_LOCALES] : REGIONAL_LOCALES;
  $("#locale-select").innerHTML = `<option value="">${esc(t("settings.locale_auto"))}</option>` +
    codes.map((code) => `<option value="${esc(code)}">${esc(names.of(code) ?? code)} — ${esc(sample(code))}</option>`).join("");
  $("#locale-select").value = current;
}

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
  renderAccounts();
  await loadTotals();
  renderChart();
});

// --- Start ---

(async () => {
  await initI18n();
  state.about = await api("/api/about");
  $("#version").textContent = `v${state.about.version}`;
  $("#logout-btn").hidden = !state.about.auth;
  await refresh();
})().catch(showError);
