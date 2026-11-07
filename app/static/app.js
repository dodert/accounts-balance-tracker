const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const ACCOUNT_TYPES = ["bank", "savings", "investment", "pension", "crypto", "cash", "loan", "other"];
const SERIES_SLOTS = 8; // categorical colours; accounts past these are drawn in grey
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

const state = { about: null, accounts: [], history: null, mode: "amounts", currency: null, editing: null, maximized: null, group: savedGroup() };

// Chart grouping (by account or by institution), remembered in this browser
function savedGroup() {
  try { return localStorage.getItem("abt-group") === "institution" ? "institution" : "account"; } catch { return "account"; }
}

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
// Not available yet: available_from after today (ISO dates compare as text)
const notAvailableYet = (a) => !!a?.available_from && a.available_from > today();
const lockBadge = (a) => (notAvailableYet(a) ? ` <span class="badge lock" title="${esc(t("account.available_hint"))}">🔒 ${esc(t("accounts.available_on", { date: fmtDate(a.available_from) }))}</span>` : "");

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

// The change from `previous` to `current`, coloured; with percent=true also in % of `previous`
function changeCell(current, previous, currency, percent = false) {
  if (current === null || previous === null || previous === undefined) return "";
  const diff = Math.round((current - previous) * 100) / 100;
  const cls = diff > 0 ? "up" : diff < 0 ? "down" : "";
  const arrow = diff > 0 ? "▲ " : diff < 0 ? "▼ " : "";
  const pct = percent && previous !== 0
    ? ` <span class="pct">(${esc(new Intl.NumberFormat(fmtLocale(), { style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(diff / Math.abs(previous)))})</span>`
    : "";
  return `<span class="${cls}"><span class="arrow">${arrow}</span>${esc(signedMoney(diff, currency))}${pct}</span>`;
}

// --- Totals ---

async function loadTotals() {
  const totals = await api("/api/summary");
  state.totals = totals;
  $("#totals").innerHTML = totals.length ? totals.map((c) => `
    <div class="tile">
      <div class="tile-label">${esc(t("totals.total", { currency: c.currency }))}</div>
      <div class="tile-value">${esc(money(c.total, c.currency))}</div>
      <div class="tile-sub">${c.previous_total !== null ? `${changeCell(c.total, c.previous_total, c.currency)} ${esc(t("totals.since", { date: fmtDate(c.previous_date) }))}` : esc(t("totals.accounts", { n: c.accounts }))}</div>
      ${c.first_total !== null ? `<div class="tile-sub">${changeCell(c.total, c.first_total, c.currency, true)} ${esc(t("totals.since_first", { date: fmtDate(c.first_date) }))}</div>` : ""}
      ${c.available !== c.total ? `<div class="tile-sub tile-available">🔒 ${esc(t("totals.available", { amount: money(c.available, c.currency), locked: money(c.total - c.available, c.currency) }))}</div>` : ""}
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
      <td><span class="t">${esc(a.name)}</span>${a.institution ? `<span class="sub">${esc(a.institution)}</span>` : ""}${a.archived ? ` <span class="badge">${esc(t("accounts.archived"))}</span>` : ""}${lockBadge(a)}</td>
      <td>${esc(t(`type.${a.type}`))}</td>
      <td class="num">${esc(money(a.balance, a.currency))}</td>
      <td class="num">${changeCell(a.balance, a.previous_balance, a.currency)}</td>
      <td class="num">${changeCell(a.balance, a.first_balance, a.currency, true)}</td>
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

// The series to draw. Each account keeps its colour in every view and whatever the range (colour by
// account id; past 8 accounts the rest have no colour of their own). The total is drawn in ink.
// `start` is the first balance ever recorded, also when a shorter range is shown.
const firstValue = (values) => values.find((v) => v !== null) ?? null;

function totalSeries(h) {
  const summary = state.totals?.find((c) => c.currency === h.currency);
  return { name: t("chart.total"), values: h.total, color: "var(--total)", start: summary?.first_total ?? firstValue(h.total), total: true };
}

function accountSeries(h) {
  const ids = state.accounts.filter((a) => a.currency === h.currency).map((a) => a.id).sort((a, b) => a - b);
  return h.accounts.filter((a) => a.values.some((v) => v !== null)).map((a) => {
    const account = state.accounts.find((x) => x.id === a.account_id);
    const slot = ids.indexOf(a.account_id) + 1;
    return {
      account_id: a.account_id, name: a.name, detail: account?.institution ?? "", values: a.values, slot, archived: a.archived,
      available_from: account?.available_from ?? null,
      color: slot <= SERIES_SLOTS ? `var(--s${slot})` : "var(--muted)",
      start: account?.first_balance ?? firstValue(a.values),
    };
  }).sort((a, b) => a.slot - b.slot);
}

// One series per institution: the sum of its accounts (accounts without one are grouped together).
// Its start is its total on the first date any of its accounts had a balance.
function institutionSeries(h) {
  const groups = new Map();
  for (const a of accountSeries(h)) {
    const key = a.detail;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  }
  const keys = [...groups.keys()].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
  return keys.map((key, i) => {
    const members = groups.get(key);
    const values = h.dates.map((_, n) => {
      const present = members.map((a) => a.values[n]).filter((v) => v !== null);
      return present.length ? present.reduce((sum, v) => sum + v, 0) : null;
    });
    const firsts = members.map((a) => state.accounts.find((x) => x.id === a.account_id)).filter((a) => a?.first_date);
    const day = firsts.map((a) => a.first_date).sort()[0];
    const start = day ? firsts.filter((a) => a.first_date === day).reduce((sum, a) => sum + a.first_balance, 0) : firstValue(values);
    const slot = i + 1;
    return {
      account_id: `i:${key}`, name: key || t("chart.no_institution"), values, slot, start,
      detail: t(members.length === 1 ? "chart.one_account" : "chart.n_accounts", { n: members.length }),
      archived: members.every((a) => a.archived),
      color: slot <= SERIES_SLOTS ? `var(--s${slot})` : "var(--muted)",
    };
  });
}

const chartSeries = (h) => (state.group === "institution" ? institutionSeries(h) : accountSeries(h));
// The series name, with its institution (or its number of accounts) after it
const seriesName = (s) => esc(s.name) + (s.detail ? ` <span class="detail">· ${esc(s.detail)}</span>` : "");

// Change since the start in % (0 at the start); null where there is no value or the start is 0
const pctSeries = (s) => ({
  ...s, amounts: s.values,
  values: s.values.map((v) => (v === null || !s.start ? null : ((v - s.start) / Math.abs(s.start)) * 100)),
});
const fmtPct = (v, digits = 1) => new Intl.NumberFormat(fmtLocale(), { style: "percent", maximumFractionDigits: digits, signDisplay: "exceptZero" }).format(v / 100);

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

function placeTooltip(e) {
  const tooltip = $("#tooltip");
  tooltip.hidden = false;
  const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
  let left = e.clientX + 14, top = e.clientY - th - 10;
  if (left + tw > window.innerWidth - 8) left = e.clientX - tw - 14;
  if (left < 8) left = 8;
  if (top < 8) top = e.clientY + 14;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

// One line chart in `box`. opts: dates, series [{name, values, color, total?}], height,
// axis(ticks) -> labels, ref {value, label} for the dashed start line, area (fill under the
// first series), small (fewer labels), tooltip(idx, rows) -> html
function drawChart(box, opts) {
  const { dates, series, ref } = opts;
  const width = Math.max(box.clientWidth, 200);
  const height = opts.height;
  const pad = { top: 12, right: 12, bottom: 24, left: 64 };
  const times = dates.map((d) => Date.parse(`${d}T00:00:00Z`));
  const values = series.flatMap((s) => s.values).filter((v) => v !== null);
  if (ref) values.push(ref.value);
  if (!values.length) return;
  const ticks = niceTicks(Math.min(...values), Math.max(...values), opts.small ? 3 : 5);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const t0 = times[0], t1 = times[times.length - 1];
  const x = (time) => pad.left + (t1 === t0 ? (width - pad.left - pad.right) / 2 : ((time - t0) / (t1 - t0)) * (width - pad.left - pad.right));
  const y = (v) => pad.top + (1 - (v - y0) / (y1 - y0)) * (height - pad.top - pad.bottom);

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": opts.label ?? t("chart.title") }, box);
  const labels = opts.axis(ticks);
  ticks.forEach((tick, i) => {
    svg("line", { x1: pad.left, x2: width - pad.right, y1: y(tick), y2: y(tick), class: "grid" }, root);
    svg("text", { x: pad.left - 8, y: y(tick), class: "axis", "text-anchor": "end", "dominant-baseline": "middle" }, root).textContent = labels[i];
  });
  const labelCount = Math.min(times.length, Math.max(2, Math.floor((width - pad.left) / (opts.small ? 90 : 110))));
  // Labels evenly spread in time (dates can be unevenly spaced), never closer than `gap` px nor repeated
  const gap = opts.small ? 70 : 80, nearest = (time) => times.reduce((best, tm, i) => (Math.abs(tm - time) < Math.abs(times[best] - time) ? i : best), 0);
  const picked = [];
  for (let i = 0; i < labelCount; i++) {
    const idx = labelCount === 1 ? 0 : nearest(t0 + ((t1 - t0) * i) / (labelCount - 1));
    const prev = picked[picked.length - 1];
    if (prev !== undefined && (x(times[idx]) - x(times[prev]) < gap || fmtDate(dates[idx], { month: "short", year: "2-digit" }) === fmtDate(dates[prev], { month: "short", year: "2-digit" }))) {
      if (i === labelCount - 1 && picked.length > 1) picked[picked.length - 1] = idx; // keep the last date
      continue;
    }
    picked.push(idx);
  }
  for (const idx of picked) {
    const anchor = times.length === 1 ? "middle" : idx === 0 ? "start" : idx === times.length - 1 ? "end" : "middle";
    svg("text", { x: x(times[idx]), y: height - 6, class: "axis", "text-anchor": anchor }, root).textContent = fmtDate(dates[idx], { month: "short", year: "2-digit" });
  }
  if (ref) {
    const ys = y(ref.value);
    svg("line", { x1: pad.left, x2: width - pad.right, y1: ys, y2: ys, class: "start-line" }, root);
    const below = ys - pad.top < 14;
    svg("text", { x: pad.left + 6, y: below ? ys + 13 : ys - 5, class: "start-label" }, root).textContent = ref.label;
  }
  // The day the balance becomes available, when it falls inside the range shown
  if (opts.marker) {
    const time = Date.parse(`${opts.marker.date}T00:00:00Z`);
    if (time >= t0 && time <= t1 && t1 > t0) {
      const xm = x(time);
      svg("line", { x1: xm, x2: xm, y1: pad.top, y2: height - pad.bottom, class: "avail-line" }, root);
      svg("text", { x: xm + 4, y: pad.top + 10, class: "avail-label" }, root).textContent = opts.marker.label;
    }
  }
  // Total last, so it is drawn over the accounts
  const order = [...series].sort((a, b) => Number(!!a.total) - Number(!!b.total));
  order.forEach((s, n) => {
    const pts = s.values.map((v, i) => (v === null ? null : [x(times[i]), y(v)]));
    let d = "", pen = false;
    for (const p of pts) {
      if (!p) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
      pen = true;
    }
    if (!d) return;
    if (opts.area && s === series[0]) {
      const first = pts.find(Boolean), last = [...pts].reverse().find(Boolean);
      const base = y(Math.max(y0, Math.min(ref ? ref.value : 0, y1)));
      svg("path", { d: `${d}L${last[0]},${base}L${first[0]},${base}Z`, class: "area", style: `fill: ${s.color}` }, root);
    }
    svg("path", { d, class: `line${s.total ? " total" : ""}${s.dashed ? " dashed" : ""}`, style: `stroke: ${s.color}` }, root);
    if (times.length <= 24) {
      for (const p of pts) if (p) svg("circle", { cx: p[0], cy: p[1], r: opts.small ? 3 : 4, class: "dot", style: `fill: ${s.color}` }, root);
    }
  });
  // Hover: crosshair on the nearest date, tooltip with every series on it
  const cross = svg("line", { y1: pad.top, y2: height - pad.bottom, class: "crosshair", visibility: "hidden" }, root);
  const marks = series.map((s) => svg("circle", { r: 5, class: "hover-dot", style: `fill: ${s.color}`, visibility: "hidden" }, root));
  const hit = svg("rect", { x: pad.left, y: 0, width: width - pad.left - pad.right, height, fill: "transparent" }, root);
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    marks.forEach((m) => m.setAttribute("visibility", "hidden"));
    $("#tooltip").hidden = true;
  });
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
    const rows = series.filter((s) => s.values[idx] !== null).sort((a, b) => Number(!!b.total) - Number(!!a.total) || b.values[idx] - a.values[idx]);
    $("#tooltip").innerHTML = `<div class="tt-date">${esc(fmtDate(dates[idx]))}</div>` + opts.tooltip(idx, rows);
    placeTooltip(e);
  });
}

const ttRow = (s, value, sub = "") =>
  `<div class="tt-row${s.total ? " tt-total" : ""}"><span class="swatch${s.dashed ? " dashed-swatch" : ""}" style="${s.dashed ? "" : `background: ${s.color}`}"></span><span class="tt-name">${seriesName(s)}</span><span class="tt-value">${esc(value)}</span></div>` +
  (sub ? `<div class="tt-since">${sub}</div>` : "");
const sinceText = (s, v, currency) => (s.start !== null && s.start !== undefined ? `${changeCell(v, s.start, currency, true)} ${esc(t("chart.since_start"))}` : "");

function renderChart() {
  const box = $("#chart"), grid = $("#multiples");
  box.innerHTML = "";
  grid.innerHTML = "";
  $("#chart-legend").innerHTML = "";
  $("#chart-table").innerHTML = "";
  const h = state.history;
  if (!h || h.dates.length === 0) {
    box.innerHTML = `<p class="hint chart-empty">${esc(t("chart.empty"))}</p>`;
    $("#chart-table-wrap").hidden = true;
    grid.hidden = true;
    return;
  }
  $("#chart-table-wrap").hidden = false;
  const total = totalSeries(h);
  const accounts = chartSeries(h);
  const cur = h.currency;
  const big = box.clientWidth < 500 ? 220 : 300;

  if (state.mode === "percent") {
    // Every account and the total on one axis: % change since each one's own first balance
    grid.hidden = true;
    const series = [...accounts, total].map(pctSeries).filter((s) => s.values.some((v) => v !== null));
    drawChart(box, {
      dates: h.dates, series, height: big, ref: { value: 0, label: t("chart.start_pct") },
      axis: (ticks) => ticks.map((v) => fmtPct(v, 0)),
      tooltip: (idx, rows) => rows.map((s) => ttRow(s, fmtPct(s.values[idx]), esc(money(s.amounts[idx], cur)))).join(""),
    });
    $("#chart-legend").innerHTML = series.map((s) =>
      `<span class="legend-item${s.total ? " legend-total" : ""}"><span class="swatch" style="background: ${s.color}"></span>${seriesName(s)}</span>`).join("");
  } else {
    // The total on its own chart, then one small chart per account with its own scale and start
    // The available part of the total, when some balance was not available on some date
    const locked = h.available.some((v, i) => v !== h.total[i]);
    const available = { name: t("chart.available"), values: h.available, color: "var(--total)", dashed: true };
    drawChart(box, {
      dates: h.dates, series: locked ? [total, available] : [total], height: big, area: true,
      ref: total.start !== null ? { value: total.start, label: t("chart.start", { amount: money(total.start, cur) }) } : null,
      axis: (ticks) => axisLabels(ticks, cur),
      tooltip: (idx, rows) => rows.map((s) => ttRow(s, money(s.values[idx], cur), sinceText(s, s.values[idx], cur))).join("") +
        accounts.filter((a) => a.values[idx] !== null).map((a) => ttRow(a, money(a.values[idx], cur))).join(""),
    });
    if (locked) {
      $("#chart-legend").innerHTML = `<span class="legend-item legend-total"><span class="swatch" style="background: var(--total)"></span>${esc(t("chart.total"))}</span>` +
        `<span class="legend-item"><span class="swatch dashed-swatch"></span>${esc(t("chart.available_hint"))}</span>`;
    }
    grid.hidden = accounts.length === 0;
    for (const a of accounts) {
      const card = document.createElement("div");
      card.className = "mini";
      const max = `<button type="button" class="icon mini-max" title="${esc(t("chart.maximize"))}" aria-label="${esc(t("chart.maximize"))}: ${esc(a.name)}">⤢</button>`;
      card.innerHTML = accountHead(a, cur, max) + `<div class="chart mini-chart"></div>`;
      grid.appendChild(card);
      $(".mini-max", card).addEventListener("click", () => openMaximized(a.account_id));
      drawAccountChart($(".mini-chart", card), a, h, true);
    }
  }
  if ($("#chart-dlg").open) drawMaximized();

  // The amounts as a table: total and every account
  const cols = [total, ...(h.available.some((v, i) => v !== h.total[i]) ? [{ name: t("chart.available"), values: h.available }] : []), ...accounts];
  $("#chart-table").innerHTML = `<thead><tr><th>${esc(t("update.date"))}</th>${cols.map((s) => `<th class="num">${esc(s.name)}${s.detail ? `<span class="sub">${esc(s.detail)}</span>` : ""}</th>`).join("")}</tr></thead><tbody>` +
    h.dates.map((d, i) => `<tr><td>${esc(fmtDate(d))}</td>${cols.map((s) => `<td class="num">${esc(money(s.values[i], cur))}</td>`).join("")}</tr>`).reverse().join("") + "</tbody>";
}

$$("#chart-panel .views button").forEach((btn) => btn.addEventListener("click", () => {
  state.mode = btn.dataset.mode;
  $$("#chart-panel .views button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  renderChart();
}));
$("#chart-currency").addEventListener("change", (e) => { state.currency = e.target.value; loadHistory().catch(showError); });
$("#chart-range").addEventListener("change", () => loadHistory().catch(showError));
$("#chart-group").value = state.group;
$("#chart-group").addEventListener("change", (e) => {
  state.group = e.target.value;
  try { localStorage.setItem("abt-group", state.group); } catch {}
  renderChart();
});
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 150); });

async function refresh() {
  await Promise.all([loadAccounts(), loadTotals()]);
  await loadHistory();
}

function showError(err) {
  alert(err.message ?? String(err));
}

// Name, last balance and change since the start of an account (small card and maximized chart)
function accountHead(a, cur, extra = "") {
  const last = [...a.values].reverse().find((v) => v !== null);
  return `
    <div class="mini-head">
      <span class="swatch" style="background: ${a.color}"></span>
      <span class="mini-name">${seriesName(a)}${a.archived ? ` <span class="badge">${esc(t("accounts.archived"))}</span>` : ""}</span>
      <span class="mini-value">${esc(money(last, cur))}</span>${extra}
    </div>
    <div class="mini-sub">${sinceText(a, last, cur)}${lockBadge(a)}</div>`;
}

function drawAccountChart(box, a, h, small) {
  const cur = h.currency;
  const amount = small ? compactMoney(a.start, cur) : money(a.start, cur);
  drawChart(box, {
    dates: h.dates, series: [a], height: small ? 130 : Math.max(240, Math.min(420, window.innerHeight - 260)), small, label: a.name,
    area: !small, ref: a.start !== null ? { value: a.start, label: t("chart.start", { amount }) } : null,
    marker: a.available_from ? { date: a.available_from, label: `🔓 ${small ? "" : t("chart.available") + " "}${fmtDate(a.available_from, { day: "numeric", month: "short", year: "2-digit" })}` } : null,
    axis: (ticks) => axisLabels(ticks, cur),
    tooltip: (idx, rows) => rows.map((s) => ttRow(s, money(s.values[idx], cur), sinceText(s, s.values[idx], cur))).join(""),
  });
}

// One account's chart, big, in a dialog (the tooltip moves into the dialog so it shows above it)
function openMaximized(accountId) {
  state.maximized = accountId;
  const dlg = $("#chart-dlg");
  dlg.appendChild($("#tooltip"));
  dlg.showModal();
  drawMaximized();
}

function drawMaximized() {
  const h = state.history;
  const a = h && chartSeries(h).find((s) => s.account_id === state.maximized);
  if (!a) { $("#chart-dlg").close(); return; }
  $("#chart-dlg-head").innerHTML = accountHead(a, h.currency);
  const box = $("#chart-dlg-chart");
  box.innerHTML = "";
  drawAccountChart(box, a, h, false);
}

// A click outside the dialog box (on the backdrop) closes it too
$("#chart-dlg").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.currentTarget.close();
});
$("#chart-dlg").addEventListener("close", () => {
  state.maximized = null;
  $("#tooltip").hidden = true;
  document.body.appendChild($("#tooltip"));
});

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
      <span class="u-name">${esc(a.name)}${a.institution ? ` <span class="detail">· ${esc(a.institution)}</span>` : ""} <span class="sub">${esc(a.balance !== null ? t("update.last", { amount: money(a.balance, a.currency), date: fmtDate(a.balance_date) }) : t("update.never"))}</span></span>
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
  setDate(form.available_from, account?.available_from ?? "");
  form.available_from.placeholder = ""; // empty means available: no example date that looks like a value
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
  // Newest first: the next row is the previous balance, the last row the first one
  const first = balances[balances.length - 1];
  $("#balances-table thead").hidden = !balances.length;
  $("#balances-table tbody").innerHTML = balances.length ? balances.map((b, i) => `
    <tr>
      <td>${esc(fmtDate(b.date))}</td>
      <td class="num">${esc(money(b.amount, account.currency))}</td>
      <td class="num">${balances[i + 1] ? changeCell(b.amount, balances[i + 1].amount, account.currency) : ""}</td>
      <td class="num">${balances[i + 1] ? changeCell(b.amount, first.amount, account.currency, true) : ""}</td>
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
  try {
    const data = {
      name: form.name.value, institution: form.institution.value, type: form.type.value,
      currency: form.currency.value.toUpperCase(), notes: form.notes.value, archived: form.archived.checked,
      available_from: form.available_from.value.trim() ? getDate(form.available_from) : null,
    };
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
