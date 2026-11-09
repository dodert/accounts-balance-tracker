// The charts: total, one small chart per account, percent view and the maximized dialog.
import { api } from "./api.js";
import { $, $$, esc, showError } from "./dom.js";
import { axisLabels, changeCell, compactMoney, fmtDate, fmtPct, lockBadge, money } from "./format.js";
import { ALL_CURRENCIES, CONVERTED, state } from "./state.js";

export const SERIES_SLOTS = 16; // categorical colours; past these they repeat (never grey)
export const slotColor = (slot) => `var(--s${((slot - 1) % SERIES_SLOTS) + 1})`;
// An account's colour: by its place among all the accounts (the same in every chart)
export const accountColor = (id) => slotColor(state.accounts.map((a) => a.id).sort((a, b) => a - b).indexOf(id) + 1);

// The milestones of these accounts, to draw on a chart: each one in its account's colour and currency
export function milestonesFor(ids) {
  return state.milestones.filter((m) => ids.includes(m.account_id)).map((m) => {
    const account = state.accounts.find((a) => a.id === m.account_id);
    return { ...m, color: accountColor(m.account_id), currency: account?.currency, account: account?.name, institution: account?.institution };
  });
}
// "15 mar 2026 · New car · 12.500 € (Savings · Banco Aurora)"
export const milestoneWhere = (m) => [m.account, m.institution].filter(Boolean).join(" · ");
export const milestoneText = (m) =>
  `${fmtDate(m.date)} · ${m.description} · ${money(m.amount, m.currency)}${m.account ? ` (${milestoneWhere(m)})` : ""}`;
// The tooltip of a milestone line: description, amount, account and institution
export const milestoneTip = (m) => `
  <div class="tt-date">⚑ ${esc(fmtDate(m.date))}</div>
  <div class="tt-row"><span class="swatch" style="background: ${m.color}"></span><span class="tt-name">${esc(m.description)}</span><span class="tt-value">${esc(money(m.amount, m.currency))}</span></div>
  ${m.account ? `<div class="tt-ms-where">${esc(milestoneWhere(m))}</div>` : ""}`;
export const SVG_NS = "http://www.w3.org/2000/svg";

// --- Chart ---

export function sinceDate() {
  const months = $("#chart-range").value;
  if (months === "all") return null;
  const d = new Date();
  d.setMonth(d.getMonth() - Number(months));
  return d.toISOString().slice(0, 10);
}

export async function loadHistory() {
  const since = sinceDate();
  if (state.currency === CONVERTED) {
    const h = await api(`/api/history/converted${since ? `?since=${since}` : ""}`);
    state.histories = [{ ...h, isConverted: true }];
    renderChart();
    return;
  }
  const currencies = state.currency === ALL_CURRENCIES ? state.currencies : state.currency ? [state.currency] : [];
  state.histories = await Promise.all(currencies.map((c) =>
    api(`/api/history?currency=${encodeURIComponent(c)}${since ? `&since=${since}` : ""}`)));
  renderChart();
}

// The series to draw. Each account keeps its colour in every view and whatever the range (colour by
// account id; past 16 accounts the colours repeat). The total is drawn in ink.
// `start` is the first balance ever recorded, also when a shorter range is shown.
export const firstValue = (values) => values.find((v) => v !== null) ?? null;

export function totalSeries(h) {
  const summary = state.totals?.find((c) => c.currency === h.currency);
  return { name: t("chart.total"), values: h.total, color: "var(--total)", start: h.first_total ?? summary?.first_total ?? firstValue(h.total), total: true };
}

export function accountSeries(h) {
  const ids = state.accounts.map((a) => a.id).sort((a, b) => a - b);
  return h.accounts.filter((a) => a.values.some((v) => v !== null)).map((a) => {
    const account = state.accounts.find((x) => x.id === a.account_id);
    const slot = ids.indexOf(a.account_id) + 1;
    return {
      // In the converted chart, an account in another currency says which one
      account_id: a.account_id, ids: [a.account_id], name: a.name, values: a.values, slot, archived: a.archived,
      detail: [account?.institution, a.currency && a.currency !== h.currency ? a.currency : ""].filter(Boolean).join(" · "),
      available_from: account?.available_from ?? null,
      color: slotColor(slot),
      start: a.start ?? account?.first_balance ?? firstValue(a.values),
    };
  }).sort((a, b) => a.slot - b.slot);
}

// One series per institution: the sum of its accounts (accounts without one are grouped together).
// Its start is its total on the first date any of its accounts had a balance.
export function institutionSeries(h) {
  const groups = new Map();
  for (const a of accountSeries(h)) {
    const key = state.accounts.find((x) => x.id === a.account_id)?.institution ?? "";
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
    const start = h.isConverted ? firstValue(values) : day ? firsts.filter((a) => a.first_date === day).reduce((sum, a) => sum + a.first_balance, 0) : firstValue(values);
    const slot = i + 1;
    return {
      account_id: `i:${key}`, ids: members.map((a) => a.account_id), name: key || t("chart.no_institution"), values, slot, start,
      detail: t(members.length === 1 ? "chart.one_account" : "chart.n_accounts", { n: members.length }),
      archived: members.every((a) => a.archived),
      color: slotColor(slot),
    };
  });
}

export const chartSeries = (h) => (state.group === "institution" ? institutionSeries(h) : accountSeries(h));
// The series name, with its institution (or its number of accounts) after it
export const seriesName = (s) => esc(s.name) + (s.detail ? ` <span class="detail">· ${esc(s.detail)}</span>` : "");

// Change since the start in % (0 at the start); null where there is no value or the start is 0
export const pctSeries = (s) => ({
  ...s, amounts: s.values,
  values: s.values.map((v) => (v === null || !s.start ? null : ((v - s.start) / Math.abs(s.start)) * 100)),
});

export function niceTicks(min, max, count = 5) {
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

export function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  parent?.appendChild(el);
  return el;
}

export function placeTooltip(e) {
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
export function drawChart(box, opts) {
  const { dates, series, ref } = opts;
  const width = Math.max(box.clientWidth, 200);
  const height = opts.height;
  const pad = { top: 12, right: 12, bottom: 24, left: 64 };
  const times = dates.map((d) => Date.parse(`${d}T00:00:00Z`));
  const values = series.flatMap((s) => s.values).filter((v) => v !== null);
  if (ref) values.push(ref.value);
  if (!values.length) return;
  const ticks = niceTicks(Math.min(...values), Math.max(...values), opts.small ? 3 : 5);
  const labels = opts.axis(ticks);
  // Room for the longest axis label (about 6.5px a character at 11px), e.g. "5,4 mil US$"
  pad.left = Math.max(pad.left, Math.ceil(Math.max(...labels.map((l) => String(l).length)) * 6.5) + 12);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const t0 = times[0], t1 = times[times.length - 1];
  const x = (time) => pad.left + (t1 === t0 ? (width - pad.left - pad.right) / 2 : ((time - t0) / (t1 - t0)) * (width - pad.left - pad.right));
  const y = (v) => pad.top + (1 - (v - y0) / (y1 - y0)) * (height - pad.top - pad.bottom);

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": opts.label ?? t("chart.title") }, box);
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
  // Milestones (notable expenses): a dotted vertical line on their date, in their account's colour
  const milestones = (opts.milestones ?? []).map((m) => ({ ...m, time: Date.parse(`${m.date}T00:00:00Z`) }))
    .filter((m) => m.time >= t0 && m.time <= t1);
  for (const m of milestones) {
    const xm = (m.x = x(m.time));
    m.line = svg("line", { x1: xm, x2: xm, y1: pad.top, y2: height - pad.bottom, class: "milestone-line", style: `stroke: ${m.color}` }, root);
    svg("path", { d: `M${xm - 4},${pad.top - 7}L${xm + 4},${pad.top - 7}L${xm},${pad.top - 1}Z`, style: `fill: ${m.color}` }, root);
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
    milestones.forEach((m) => m.line.classList.remove("hover"));
    $("#tooltip").hidden = true;
  });
  hit.addEventListener("pointermove", (e) => {
    const rect = root.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    // On a milestone line (within 6 screen pixels): only that milestone, with its amount, account and institution
    const near = milestones.filter((m) => Math.abs(m.x - px) * (rect.width / width) <= 6);
    milestones.forEach((m) => m.line.classList.toggle("hover", near.includes(m)));
    if (near.length) {
      cross.setAttribute("visibility", "hidden");
      marks.forEach((m) => m.setAttribute("visibility", "hidden"));
      $("#tooltip").innerHTML = near.map(milestoneTip).join("");
      placeTooltip(e);
      return;
    }
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
    // The milestones since the previous date, under the amounts
    const since = idx > 0 ? times[idx - 1] : -Infinity;
    const notes = milestones.filter((m) => m.time > since && m.time <= times[idx]).map((m) =>
      `<div class="tt-milestone"><span class="swatch" style="background: ${m.color}"></span> ${esc(milestoneText(m))}</div>`).join("");
    $("#tooltip").innerHTML = `<div class="tt-date">${esc(fmtDate(dates[idx]))}</div>` + opts.tooltip(idx, rows) + notes;
    placeTooltip(e);
  });
}

export const ttRow = (s, value, sub = "") =>
  `<div class="tt-row${s.total ? " tt-total" : ""}"><span class="swatch${s.dashed ? " dashed-swatch" : ""}" style="${s.dashed ? `border-top-color: ${s.color}` : `background: ${s.color}`}"></span><span class="tt-name">${seriesName(s)}</span><span class="tt-value">${esc(value)}</span></div>` +
  (sub ? `<div class="tt-since">${sub}</div>` : "");
export const sinceText = (s, v, currency) => (s.start !== null && s.start !== undefined ? `${changeCell(v, s.start, currency, true)} ${esc(t("chart.since_start"))}` : "");

// One block of charts per currency shown: the first in the panel's own elements, the others below it
export function renderChart() {
  const hs = state.histories;
  const multi = hs.length > 1;
  const head = $("#chart-cur-head");
  head.hidden = !multi;
  head.textContent = multi ? hs[0].currency : "";
  const more = $("#chart-more");
  more.innerHTML = "";
  // The converted chart says which currencies were converted, and which could not be
  const note = $("#chart-note");
  const conv = hs[0]?.isConverted ? hs[0] : null;
  note.hidden = !conv;
  note.textContent = conv ? [
    conv.converted.length ? t("chart.converted_note", { currencies: conv.converted.join(", "), currency: conv.currency }) : "",
    conv.missing.length ? t("chart.converted_missing", { currencies: conv.missing.join(", ") }) : "",
    conv.stale ? t("chart.converted_stale") : "",
  ].filter(Boolean).join(" ") : "";
  renderBlock(hs[0] ?? null, { box: $("#chart"), legend: $("#chart-legend"), grid: $("#multiples"), table: $("#chart-table"), tableWrap: $("#chart-table-wrap") });
  for (const h of hs.slice(1)) {
    const block = document.createElement("div");
    block.className = "chart-block";
    block.innerHTML = `<h3 class="chart-cur-head">${esc(h.currency)}</h3><div class="chart"></div><div class="legend"></div>` +
      `<div class="multiples" hidden></div><details><summary>${esc(t("chart.show_table"))}</summary><div class="table-wrap"><table class="data"></table></div></details>`;
    more.appendChild(block);
    renderBlock(h, { box: $(".chart", block), legend: $(".legend", block), grid: $(".multiples", block), table: $("table", block), tableWrap: $("details", block) });
  }
  if ($("#chart-dlg").open) drawMaximized();
}

export function renderBlock(h, el) {
  const { box, grid } = el;
  box.innerHTML = "";
  grid.innerHTML = "";
  el.legend.innerHTML = "";
  el.table.innerHTML = "";
  if (!h || h.dates.length === 0) {
    box.innerHTML = `<p class="hint chart-empty">${esc(t("chart.empty"))}</p>`;
    el.tableWrap.hidden = true;
    grid.hidden = true;
    return;
  }
  el.tableWrap.hidden = false;
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
      milestones: milestonesFor(h.accounts.map((a) => a.account_id)),
      axis: (ticks) => ticks.map((v) => fmtPct(v, 0)),
      tooltip: (idx, rows) => rows.map((s) => ttRow(s, fmtPct(s.values[idx]), esc(money(s.amounts[idx], cur)))).join(""),
    });
    el.legend.innerHTML = series.map((s) =>
      `<span class="legend-item${s.total ? " legend-total" : ""}"><span class="swatch" style="background: ${s.color}"></span>${seriesName(s)}</span>`).join("");
  } else {
    // The total on its own chart, then one small chart per account with its own scale and start
    // The available part of the total, when some balance was not available on some date
    const locked = h.available.some((v, i) => v !== h.total[i]);
    // With balances not available yet, the main line is what is available; the total with them is secondary
    const summary = state.totals?.find((c) => c.currency === cur);
    const main = locked ? { name: t("chart.available"), values: h.available, color: "var(--total)", total: true, start: h.first_available ?? summary?.first_available ?? firstValue(h.available) } : total;
    const withLocked = { ...total, name: t("chart.with_locked"), color: "var(--muted)", total: false, dashed: true };
    drawChart(box, {
      dates: h.dates, series: locked ? [main, withLocked] : [total], height: big, area: true,
      milestones: milestonesFor(h.accounts.map((a) => a.account_id)),
      ref: main.start !== null ? { value: main.start, label: t("chart.start", { amount: money(main.start, cur) }) } : null,
      axis: (ticks) => axisLabels(ticks, cur),
      tooltip: (idx, rows) => rows.map((s) => ttRow(s, money(s.values[idx], cur), sinceText(s, s.values[idx], cur))).join("") +
        accounts.filter((a) => a.values[idx] !== null).map((a) => ttRow(a, money(a.values[idx], cur))).join(""),
    });
    if (locked) {
      el.legend.innerHTML = `<span class="legend-item legend-total"><span class="swatch" style="background: var(--total)"></span>${esc(t("chart.available"))}</span>` +
        `<span class="legend-item"><span class="swatch dashed-swatch" style="border-top-color: var(--muted)"></span>${esc(t("chart.with_locked"))}</span>`;
    }
    grid.hidden = accounts.length === 0;
    for (const a of accounts) {
      const card = document.createElement("div");
      card.className = "mini";
      const max = `<button type="button" class="icon mini-max" title="${esc(t("chart.maximize"))}" aria-label="${esc(t("chart.maximize"))}: ${esc(a.name)}">⤢</button>`;
      card.innerHTML = accountHead(a, cur, max) + `<div class="chart mini-chart"></div>`;
      grid.appendChild(card);
      $(".mini-max", card).addEventListener("click", () => openMaximized(a.account_id, cur));
      drawAccountChart($(".mini-chart", card), a, h, true);
    }
  }

  // The amounts as a table: total and every account
  const cols = h.available.some((v, i) => v !== h.total[i])
    ? [{ name: t("chart.available"), values: h.available }, { name: t("chart.with_locked"), values: h.total }, ...accounts]
    : [total, ...accounts];
  el.table.innerHTML = `<thead><tr><th>${esc(t("update.date"))}</th>${cols.map((s) => `<th class="num">${esc(s.name)}${s.detail ? `<span class="sub">${esc(s.detail)}</span>` : ""}</th>`).join("")}</tr></thead><tbody>` +
    h.dates.map((d, i) => `<tr><td>${esc(fmtDate(d))}</td>${cols.map((s) => `<td class="num">${esc(money(s.values[i], cur))}</td>`).join("")}</tr>`).reverse().join("") + "</tbody>";
}

// Name, last balance and change since the start of an account (small card and maximized chart)
export function accountHead(a, cur, extra = "") {
  const last = [...a.values].reverse().find((v) => v !== null);
  return `
    <div class="mini-head">
      <span class="swatch" style="background: ${a.color}"></span>
      <span class="mini-name">${seriesName(a)}${a.archived ? ` <span class="badge">${esc(t("accounts.archived"))}</span>` : ""}</span>
      <span class="mini-value">${esc(money(last, cur))}</span>${extra}
    </div>
    <div class="mini-sub">${sinceText(a, last, cur)}${lockBadge(a)}</div>`;
}

export function drawAccountChart(box, a, h, small) {
  const cur = h.currency;
  const amount = small ? compactMoney(a.start, cur) : money(a.start, cur);
  drawChart(box, {
    dates: h.dates, series: [a], height: small ? 130 : Math.max(240, Math.min(420, window.innerHeight - 260)), small, label: a.name,
    milestones: milestonesFor(a.ids ?? []),
    area: !small, ref: a.start !== null ? { value: a.start, label: t("chart.start", { amount }) } : null,
    marker: a.available_from ? { date: a.available_from, label: `🔓 ${small ? "" : t("chart.available") + " "}${fmtDate(a.available_from, { day: "numeric", month: "short", year: "2-digit" })}` } : null,
    axis: (ticks) => axisLabels(ticks, cur),
    tooltip: (idx, rows) => rows.map((s) => ttRow(s, money(s.values[idx], cur), sinceText(s, s.values[idx], cur))).join(""),
  });
}

// One account's chart, big, in a dialog (the tooltip moves into the dialog so it shows above it)
export function openMaximized(accountId, currency) {
  state.maximized = { id: accountId, currency };
  const dlg = $("#chart-dlg");
  dlg.appendChild($("#tooltip"));
  dlg.showModal();
  drawMaximized();
}

export function drawMaximized() {
  const h = state.histories.find((x) => x.currency === state.maximized?.currency);
  const a = h && chartSeries(h).find((s) => s.account_id === state.maximized.id);
  if (!a) { $("#chart-dlg").close(); return; }
  $("#chart-dlg-head").innerHTML = accountHead(a, h.currency);
  const box = $("#chart-dlg-chart");
  box.innerHTML = "";
  drawAccountChart(box, a, h, false);
}

export function initCharts() {
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
}
