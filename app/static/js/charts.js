// The charts: total, one small chart per account, percent view and the maximized dialog.
import { api, localDate } from "./api.js";
import { $, $$, esc, showError } from "./dom.js";
import { addBalanceFrom } from "./dialogs.js";
import { openDialog } from "./modal.js";
import { axisLabels, changeCell, compactMoney, exactMoney, fmtDate, fmtPct, lockBadge, money, uncoveredBadge } from "./format.js";
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
  `${fmtDate(m.date)} · ${m.description} · ${exactMoney(m.amount, m.currency)}${m.account ? ` (${milestoneWhere(m)})` : ""}`;
// The tooltip of a milestone line: description, amount, account and institution
export const milestoneTip = (m) => `
  <div class="tt-date">⚑ ${esc(fmtDate(m.date))}</div>
  <div class="tt-row"><span class="swatch" style="background: ${m.color}"></span><span class="tt-name">${esc(m.description)}</span><span class="tt-value">${esc(exactMoney(m.amount, m.currency))}</span></div>
  ${m.account ? `<div class="tt-ms-where">${esc(milestoneWhere(m))}</div>` : ""}`;
export const SVG_NS = "http://www.w3.org/2000/svg";

// What was contributed to a series' accounts by each of the chart's dates (the sum of their contributions up to
// that day), null where the series has no value. null when there is nothing to draw: every account of the series
// needs contributions, in the chart's currency (an account converted from another currency has none to compare).
export function contributedValues(a, h) {
  const ids = a.ids ?? [];
  if (!ids.length) return null;
  for (const id of ids) {
    const account = state.accounts.find((x) => x.id === id);
    if (account?.currency !== h.currency || !state.contributions.some((c) => c.account_id === id)) return null;
  }
  const list = state.contributions.filter((c) => ids.includes(c.account_id)); // oldest first, as the API sends them
  let i = 0, sum = 0;
  return h.dates.map((day, n) => {
    while (i < list.length && list[i].date <= day) sum += list[i++].amount;
    return a.values[n] === null ? null : sum;
  });
}
// "Gain: ▲ +1.200 € (+8,5 %)": the value minus what was contributed, and that in % of what was contributed
export const gainText = (value, contributed, currency) =>
  `${esc(t("chart.gain"))}: ${changeCell(value, contributed, currency, true)}`;

// --- Chart ---

export function sinceDate() {
  const months = $("#chart-range").value;
  if (months === "all") return null;
  return localDate(monthsBefore(new Date(), Number(months))); // local, like today(): toISOString() would give the UTC date
}

// The same day `months` months before `date`, or the last day of that month when it is shorter
// (31 May − 3 months = 28/29 Feb; setMonth alone would roll over into March)
export function monthsBefore(date, months) {
  const d = new Date(date.getFullYear(), date.getMonth() - months, 1);
  const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  d.setDate(Math.min(date.getDate(), lastDay));
  return d;
}

// [min, max] of the values, with a loop: Math.min(...values) throws past ~125,000 values in V8
export function extent(values) {
  let min = Infinity, max = -Infinity;
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return [min, max];
}

export async function loadHistory() {
  const since = sinceDate();
  const own = (c) => api(`/api/history?currency=${encodeURIComponent(c)}${since ? `&since=${since}` : ""}`);
  if (state.currency === CONVERTED) {
    // The rates may take a few seconds (the service is slow or down): say so if it isn't quick
    const loading = $("#chart-loading");
    const timer = setTimeout(() => { loading.hidden = false; }, 300);
    let h, owns;
    try {
      // The accounts list needs each account in its own currency too (#154)
      [h, ...owns] = await Promise.all([api(`/api/history/converted${since ? `?since=${since}` : ""}`), ...state.currencies.map(own)]);
    } finally {
      clearTimeout(timer);
      loading.hidden = true;
    }
    state.histories = [{ ...h, isConverted: true }];
    state.ownHistories = owns;
    renderChart();
    return;
  }
  const currencies = state.currency === ALL_CURRENCIES ? state.currencies : state.currency ? [state.currency] : [];
  const others = state.currencies.filter((c) => !currencies.includes(c));
  const [shown, rest] = await Promise.all([Promise.all(currencies.map(own)), Promise.all(others.map(own))]);
  state.histories = shown;
  state.ownHistories = [...shown, ...rest]; // every currency, for the accounts list (#154)
  renderChart();
}

// The series to draw. Each account keeps its colour in every view and whatever the range (colour by
// account id; past 16 accounts the colours repeat). The total is drawn in ink.
// `start` is the first balance ever recorded, also when a shorter range is shown; for an account with
// contributions, its first contribution (#179): nothing was at stake before money was put in. `startDay` is its date.
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
    const contributed = contributionsUpTo([a.account_id], h, null);
    return {
      // In the converted chart, an account in another currency says which one
      account_id: a.account_id, ids: [a.account_id], name: a.name, values: a.values, slot, archived: a.archived,
      detail: [account?.institution, a.currency && a.currency !== h.currency ? a.currency : ""].filter(Boolean).join(" · "),
      available_from: account?.available_from ?? null,
      color: slotColor(slot),
      start: contributed ? contributed.atFirst : a.start ?? account?.first_balance ?? firstValue(a.values),
      startDay: contributed ? contributed.firstDate : account?.first_date ?? h.dates[a.values.findIndex((v) => v !== null)],
    };
  }).sort((a, b) => a.slot - b.slot);
}

// One series per institution: the sum of its accounts (accounts without one are grouped together).
// Its start is the sum of its accounts' starts on the earliest of their start days (a first balance or contribution).
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
    const firsts = members.filter((a) => a.startDay && a.start !== null && a.start !== undefined);
    const day = firsts.map((a) => a.startDay).sort()[0];
    const start = day ? firsts.filter((a) => a.startDay === day).reduce((sum, a) => sum + a.start, 0) : firstValue(values);
    const slot = i + 1;
    return {
      account_id: `i:${key}`, ids: members.map((a) => a.account_id), name: key || t("chart.no_institution"), values, slot, start, startDay: day ?? null,
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

// Whether a series went up or down, for the chart's background: "down" when its last value is below
// its start (the dashed "Start" line: its first balance ever, or its first value shown when it has
// none); "falling" when it is not, but the least-squares line through the points shown (by date, as
// dates can be unevenly spaced) goes down, e.g. falling since a peak; "up" otherwise (up or flat).
// null when there is nothing to compare.
export function trendOf(dates, values, start = null) {
  const pts = [];
  values.forEach((v, i) => { if (v !== null && v !== undefined) pts.push([Date.parse(`${dates[i]}T00:00:00Z`) / 86400000, Number(v)]); });
  if (!pts.length || (pts.length < 2 && (start === null || start === undefined))) return null;
  const first = start === null || start === undefined ? pts[0][1] : Number(start), last = pts[pts.length - 1][1];
  if (last < first) return "down";
  const mx = pts.reduce((sum, p) => sum + p[0], 0) / pts.length;
  const my = pts.reduce((sum, p) => sum + p[1], 0) / pts.length;
  let sxy = 0, sxx = 0;
  for (const [px, py] of pts) { sxy += (px - mx) * (py - my); sxx += (px - mx) ** 2; }
  // The fitted change over the whole range, ignoring rounding noise (under a cent)
  const change = sxx ? (sxy / sxx) * (pts[pts.length - 1][0] - pts[0][0]) : 0;
  return change < -0.005 ? "falling" : "up";
}

// The contributions to these accounts, in the chart's currency (an account in another currency at the converted
// chart's rate, as its balances), summed up to each of the chart's dates, and up to `startDay` (the day of the
// series' start). Accounts without contributions add nothing; null when none of them has any.
export function contributionsUpTo(ids, h, startDay) {
  const rateOf = (id) => {
    const currency = state.accounts.find((x) => x.id === id)?.currency;
    return currency === h.currency ? 1 : h.rates?.[currency] ?? null;
  };
  const list = state.contributions.filter((c) => ids.includes(c.account_id) && rateOf(c.account_id) !== null)
    .map((c) => ({ date: c.date, amount: Math.round(c.amount * rateOf(c.account_id)) }))
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  if (!list.length) return null;
  let i = 0, sum = 0;
  const values = h.dates.map((day) => {
    while (i < list.length && list[i].date <= day) sum += list[i++].amount;
    return sum;
  });
  const upTo = (day) => list.filter((c) => c.date <= day).reduce((total, c) => total + c.amount, 0);
  return { values, atStart: startDay ? upTo(startDay) : null, firstDate: list[0].date, atFirst: upTo(list[0].date) };
}

// The trend for a chart's background (#148). With the "gain" setting (the default), for accounts with
// contributions it is the trend of the gain (the value minus what was contributed up to each date, its start the
// start minus what was contributed by then), so money put in doesn't make it look green; else of the values.
// Returns { trend, gain } (gain: whether it was computed from the gain), or null like trendOf.
export function backgroundTrend(h, ids, values, start, startDay = null) {
  const known = ids.map((id) => state.accounts.find((x) => x.id === id)).filter(Boolean);
  // The day of the start: the series' own (an account's first contribution, #179), else the first balance ever of
  // these accounts (as the start, #110), else the first shown
  startDay ??= known.map((a) => a.first_date).filter(Boolean).sort()[0] ?? h.dates[values.findIndex((v) => v !== null)];
  const contributed = state.trendMode === "value" ? null : contributionsUpTo(ids, h, startDay);
  if (!contributed) {
    const trend = trendOf(h.dates, values, start);
    return trend && { trend, gain: false };
  }
  // Nothing before the start counts (a balance older than the first contribution, #153)
  const gain = values.map((v, n) => (v === null || v === undefined || h.dates[n] < startDay ? null : v - contributed.values[n]));
  let gainStart = start === null || start === undefined ? null : start - contributed.atStart;
  let valueStart = start ?? null, putInStart = contributed.atStart;
  // Nothing can be gained before money was put in: with a balance older than the first contribution (a contribution
  // missing, #153), the gain starts at the first contribution, with the balance of that day
  if (startDay && contributed.firstDate > startDay) {
    let at = h.dates.findLastIndex((day, n) => day <= contributed.firstDate && values[n] !== null && values[n] !== undefined);
    if (at < 0) at = values.findIndex((v) => v !== null && v !== undefined);
    if (at >= 0) {
      valueStart = values[at];
      putInStart = h.dates[at] <= contributed.firstDate ? contributed.atFirst : contributed.values[at];
      gainStart = valueStart - putInStart;
      for (let n = 0; n < at; n++) gain[n] = null;
      gain[at] = gainStart;
    }
  }
  const trend = trendOf(h.dates, gain, gainStart);
  const lastAt = gain.findLastIndex((v) => v !== null);
  // What the change of the gain is a % of: the value at the start plus what was put in since then
  const base = valueStart === null || lastAt < 0 ? null : valueStart + contributed.values[lastAt] - putInStart;
  return trend && { trend, gain: true, start: gainStart, last: lastAt < 0 ? null : gain[lastAt], base };
}
const trendText = ({ trend, gain }) => (gain
  ? { up: t("chart.gain_trend_up"), falling: t("chart.gain_trend_falling"), down: t("chart.gain_trend_down") }
  : { up: t("chart.trend_up"), falling: t("chart.trend_falling"), down: t("chart.trend_down") })[trend];

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
// first series), small (fewer labels), tooltip(idx, rows) -> html, trend (trendOf: background colour).
// Returns which kinds of line it drew besides the series (for lineLegend): { start, marker, milestones }, or null.
export function drawChart(box, opts) {
  const { dates, series, ref } = opts;
  const width = Math.max(box.clientWidth, 200);
  const height = opts.height;
  const pad = { top: 12, right: 12, bottom: 24, left: 64 };
  const times = dates.map((d) => Date.parse(`${d}T00:00:00Z`));
  const values = series.flatMap((s) => s.values).filter((v) => v !== null);
  if (ref) values.push(ref.value);
  if (!values.length) return null;
  const drawn = { start: false, marker: false, milestones: false };
  const ticks = niceTicks(...extent(values), opts.small ? 3 : 5);
  const labels = opts.axis(ticks);
  // Room for the longest axis label (about 6.5px a character at 11px), e.g. "5,4 mil US$"
  pad.left = Math.max(pad.left, Math.ceil(Math.max(...labels.map((l) => String(l).length)) * 6.5) + 12);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const t0 = times[0], t1 = times[times.length - 1];
  const x = (time) => pad.left + (t1 === t0 ? (width - pad.left - pad.right) / 2 : ((time - t0) / (t1 - t0)) * (width - pad.left - pad.right));
  const y = (v) => pad.top + (1 - (v - y0) / (y1 - y0)) * (height - pad.top - pad.bottom);

  const label = [opts.label ?? t("chart.title"), opts.trend ? trendText(opts.trend) : ""].filter(Boolean).join(" · ");
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": label }, box);
  // Background: green when the balance went up or stayed the same, orange when it ends above its first
  // value but trends down, red when it went down
  if (opts.trend) {
    const bg = svg("rect", { x: pad.left, y: pad.top, width: width - pad.left - pad.right, height: height - pad.top - pad.bottom, class: `trend-bg trend-${opts.trend.trend}${opts.trend.gain ? " trend-gain" : ""}` }, root);
    svg("title", {}, bg).textContent = trendText(opts.trend);
  }
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
    drawn.start = true;
    const below = ys - pad.top < 14;
    svg("text", { x: pad.left + 6, y: below ? ys + 13 : ys - 5, class: "start-label" }, root).textContent = ref.label;
  }
  // The day the balance becomes available, when it falls inside the range shown
  if (opts.marker) {
    const time = Date.parse(`${opts.marker.date}T00:00:00Z`);
    if (time >= t0 && time <= t1 && t1 > t0) {
      const xm = x(time);
      svg("line", { x1: xm, x2: xm, y1: pad.top, y2: height - pad.bottom, class: "avail-line" }, root);
      drawn.marker = true;
      svg("text", { x: xm + 4, y: pad.top + 10, class: "avail-label" }, root).textContent = opts.marker.label;
    }
  }
  // Milestones (notable expenses): a dotted vertical line on their date, in their account's colour
  const milestones = (opts.milestones ?? []).map((m) => ({ ...m, time: Date.parse(`${m.date}T00:00:00Z`) }))
    .filter((m) => m.time >= t0 && m.time <= t1);
  drawn.milestones = milestones.length > 0;
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
    if (times.length <= 24 && !s.noDots) {
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
  return drawn;
}

// A legend of the kinds of line on a chart (#137): each with a small sample drawn with the chart's own classes (so a
// style change updates it too). items: [{ kind, label, color? }], only for the kinds that were drawn.
const LINE_SAMPLES = {
  solid: (c) => `<line x1="2" x2="22" y1="6" y2="6" class="line" style="stroke: ${c}"/>`,
  total: (c) => `<line x1="2" x2="22" y1="6" y2="6" class="line total" style="stroke: ${c}"/>`,
  dashed: (c) => `<line x1="1" x2="23" y1="6" y2="6" class="line dashed" style="stroke: ${c}"/>`,
  start: () => `<line x1="0" x2="24" y1="6" y2="6" class="start-line"/>`,
  milestone: (c) => `<line x1="12" x2="12" y1="0" y2="12" class="milestone-line" style="stroke: ${c}"/>`,
  marker: () => `<line x1="12" x2="12" y1="0" y2="12" class="avail-line"/>`,
};
export function lineLegend(items) {
  return items.map(({ kind, label, color = "var(--muted)" }) =>
    `<span class="legend-item legend-line${kind === "total" ? " legend-total" : ""}" data-kind="${kind}">` +
    `<svg class="legend-sample" width="24" height="12" viewBox="0 0 24 12" aria-hidden="true">${LINE_SAMPLES[kind](color)}</svg>${esc(label)}</span>`).join("");
}

// The legend entries of the start line, milestones and available-from line, when a chart drew them
export const extraLines = (drawn) => [
  drawn?.start && { kind: "start", label: t("legend.start") },
  drawn?.milestones && { kind: "milestone", label: t("legend.milestone") },
  drawn?.marker && { kind: "marker", label: t("legend.available_from") },
].filter(Boolean);

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
  document.dispatchEvent(new Event("abt-history")); // the accounts list's "since the start" follows the charts
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
    const drawn = drawChart(box, {
      dates: h.dates, series, height: big, ref: { value: 0, label: t("chart.start_pct") }, trend: backgroundTrend(h, h.accounts.map((a) => a.account_id), total.values, total.start),
      milestones: milestonesFor(h.accounts.map((a) => a.account_id)),
      axis: (ticks) => ticks.map((v) => fmtPct(v, 0)),
      tooltip: (idx, rows) => rows.map((s) => ttRow(s, fmtPct(s.values[idx]), esc(money(s.amounts[idx], cur)))).join(""),
    });
    el.legend.innerHTML = series.map((s) =>
      `<span class="legend-item${s.total ? " legend-total" : ""}"><span class="swatch" style="background: ${s.color}"></span>${seriesName(s)}</span>`).join("") +
      lineLegend(extraLines(drawn));
  } else {
    // The total on its own chart, then one small chart per account with its own scale and start
    // The available part of the total, when some balance was not available on some date
    const locked = h.available.some((v, i) => v !== h.total[i]);
    // With balances not available yet, the main line is what is available; the total with them is secondary
    const summary = state.totals?.find((c) => c.currency === cur);
    const main = locked ? { name: t("chart.available"), values: h.available, color: "var(--total)", total: true, start: h.first_available ?? summary?.first_available ?? firstValue(h.available) } : total;
    const withLocked = { ...total, name: t("chart.with_locked"), color: "var(--muted)", total: false, dashed: true };
    const drawn = drawChart(box, {
      dates: h.dates, series: locked ? [main, withLocked] : [total], height: big, area: true, trend: backgroundTrend(h, h.accounts.map((a) => a.account_id), main.values, main.start),
      milestones: milestonesFor(h.accounts.map((a) => a.account_id)),
      ref: main.start !== null ? { value: main.start, label: t("chart.start", { amount: money(main.start, cur) }) } : null,
      axis: (ticks) => axisLabels(ticks, cur),
      tooltip: (idx, rows) => rows.map((s) => ttRow(s, money(s.values[idx], cur), sinceText(s, s.values[idx], cur))).join("") +
        accounts.filter((a) => a.values[idx] !== null).map((a) => ttRow(a, money(a.values[idx], cur))).join(""),
    });
    el.legend.innerHTML = lineLegend([
      { kind: "total", label: main.name, color: "var(--total)" },
      locked && { kind: "dashed", label: t("chart.with_locked") },
      ...extraLines(drawn),
    ].filter(Boolean));
    grid.hidden = accounts.length === 0;
    // One legend for the whole grid: every kind of line drawn on any of the small charts
    const kinds = { start: false, marker: false, milestones: false, contributed: false };
    for (const a of accounts) {
      const card = document.createElement("div");
      card.className = "mini";
      // One account (not an institution's group): a balance can be added from its card (#171)
      const one = typeof a.account_id === "number";
      const add = one ? `<button type="button" class="icon mini-add" title="${esc(t("chart.add_balance"))}" aria-label="${esc(t("chart.add_balance"))}: ${esc(a.name)}">＋</button>` : "";
      const max = `<button type="button" class="icon mini-max" title="${esc(t("chart.maximize"))}" aria-label="${esc(t("chart.maximize"))}: ${esc(a.name)}">⤢</button>`;
      card.innerHTML = accountHead(a, h, add + max, true) + `<div class="chart mini-chart"></div>`;
      grid.appendChild(card);
      $(".mini-max", card).addEventListener("click", () => openMaximized(a.account_id, cur));
      if (one) $(".mini-add", card).addEventListener("click", () => addBalanceFrom(a.account_id).catch(showError));
      const drawnHere = drawAccountChart($(".mini-chart", card), a, h, true);
      for (const kind in kinds) kinds[kind] ||= !!drawnHere?.[kind];
    }
    if (accounts.length) {
      const legend = document.createElement("div");
      legend.className = "legend grid-legend";
      legend.innerHTML = accountLineLegend(kinds);
      grid.appendChild(legend);
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
export function accountHead(a, h, extra = "", small = false) {
  const cur = h.currency;
  const values = [...a.values].reverse();
  const at = values.findIndex((v) => v !== null);
  const last = at < 0 ? null : values[at];
  // With contributions: what was put in by the last balance, and the gain
  const contributed = at < 0 ? null : [...(contributedValues(a, h) ?? [])].reverse()[at] ?? null;
  // On a small card, short: "Contributed 38.2K € · Gain +14.6%"
  const pct = contributed ? ((last - contributed) / Math.abs(contributed)) * 100 : null;
  const shortGain = pct === null ? "" : ` · ${esc(t("chart.gain"))} <span class="${pct > 0 ? "up" : pct < 0 ? "down" : ""}">${esc(fmtPct(pct))}</span>`;
  const gain = contributed === null ? "" : `
    <div class="mini-sub mini-contributed"><span class="swatch dashed-swatch" style="border-top-color: ${a.color}"></span> ${esc(t("chart.contributed"))} ${esc(small ? compactMoney(contributed, cur) : money(contributed, cur))}${small ? shortGain : ` · ${gainText(last, contributed, cur)}`}</div>`;
  return `
    <div class="mini-head">
      <span class="swatch" style="background: ${a.color}"></span>
      <span class="mini-name">${seriesName(a)}${a.archived ? ` <span class="badge">${esc(t("accounts.archived"))}</span>` : ""}${(a.ids ?? []).map((id) => uncoveredBadge(state.accounts.find((x) => x.id === id))).join("")}</span>
      <span class="mini-value">${esc(money(last, cur))}</span>${extra}
    </div>
    <div class="mini-sub">${headSince(a, h, last)}${lockBadge(a)}</div>${gain}`;
}

// "Since the start" of an account's head, tied to its background (#148): the same trend and colour (green, orange
// or red) and the same setting. With the gain, it is how much the gain changed: the value's change minus what was
// contributed since the start
function headSince(a, h, last, label = true) {
  const tail = label ? ` ${esc(t("chart.since_start"))}` : "";
  const plain = a.start !== null && a.start !== undefined ? `${changeCell(last, a.start, h.currency, true)}${tail}` : "";
  const bg = backgroundTrend(h, a.ids ?? [], a.values, a.start, a.startDay);
  if (!bg) return plain;
  let text = plain;
  if (bg.gain) {
    // The % of what was at stake: the value at the start plus what was put in since (not of the gain at the start)
    const pct = bg.base > 0 && bg.last !== null ? ` <span class="pct">(${esc(fmtPct(((bg.last - bg.start) / bg.base) * 100))})</span>` : "";
    text = `${changeCell(bg.last, bg.start, h.currency).replace(/<\/span>$/, `${pct}</span>`)}${tail}`;
  }
  return `<span class="trend-text trend-text-${bg.trend}">${text}</span>`;
}

// An account's "since the start" in the accounts list, the same as on its chart (#148): from the history of its own
// currency, loaded whatever the charts show (#154); null until it is loaded
export function accountSinceCell(account) {
  if (account.first_balance === null || account.first_balance === undefined) return null; // one balance: nothing to compare
  const h = (state.ownHistories ?? []).find((x) => x.currency === account.currency && x.accounts.some((s) => s.account_id === account.id));
  const a = h && accountSeries(h).find((s) => s.account_id === account.id);
  if (!a) return null;
  return headSince(a, h, [...a.values].reverse().find((v) => v !== null) ?? null, false);
}

// The legend of an account's chart (small ones share one): balance, contributed, start, milestone, available from.
// The start is the first contribution of an account with contributions (#179): `single` says it of one account.
export const accountLineLegend = (drawn, color = "var(--muted)", single = false) => lineLegend([
  { kind: "solid", label: t("legend.balance"), color },
  drawn.contributed && { kind: "dashed", label: t("chart.contributed"), color },
  ...extraLines(drawn).map((line) => (line.kind === "start" && drawn.contributed
    ? { ...line, label: t(single ? "legend.start_contribution" : "legend.start_either") } : line)),
].filter(Boolean));

// An account's chart starts on its own start day (its first contribution or balance, #179), not on the first date of
// every account of the currency; a balance older than that (a contribution missing, #153) is still shown. When the
// first contribution is older than every balance shown, the chart's first point is that contribution: worth what was
// put in. Only with the whole range: a shorter one starts where it starts.
function fromStart(a, h) {
  const firstAt = a.values.findIndex((v) => v !== null);
  const day = [a.startDay, h.dates[firstAt]].filter(Boolean).sort()[0];
  const from = Math.max(0, day ? h.dates.findIndex((d) => d >= day) : 0);
  const cut = (list) => list?.slice(from);
  let values = cut(a.values), dates = cut(h.dates), total = cut(h.total), available = cut(h.available);
  if (a.startDay && a.start !== null && a.start !== undefined && firstAt >= 0 && a.startDay < h.dates[firstAt] && !sinceDate()) {
    if (dates[0] === a.startDay) values = [a.start, ...values.slice(1)];
    else {
      dates = [a.startDay, ...dates];
      values = [a.start, ...values];
      total = total && [null, ...total];
      available = available && [null, ...available];
    }
  }
  return [{ ...a, values }, { ...h, dates, total, available }];
}

// Returns which kinds of line it drew (drawChart's, plus `contributed`)
export function drawAccountChart(box, account, history, small) {
  const [a, h] = fromStart(account, history);
  const cur = h.currency;
  const amount = small ? compactMoney(a.start, cur) : money(a.start, cur);
  // With contributions: what was put in so far, dashed in the account's colour, and the gain in the tooltip
  const contributed = contributedValues(a, h);
  const series = contributed ? [a, { name: t("chart.contributed"), values: contributed, color: a.color, dashed: true, noDots: true, contributed: true }] : [a];
  const drawn = drawChart(box, {
    dates: h.dates, series, height: small ? 130 : Math.max(240, Math.min(420, window.innerHeight - 260)), small, label: a.name,
    trend: backgroundTrend(h, a.ids ?? [], a.values, a.start, a.startDay),
    milestones: milestonesFor(a.ids ?? []),
    area: !small, ref: a.start !== null ? { value: a.start, label: t("chart.start", { amount }) } : null,
    marker: a.available_from ? { date: a.available_from, label: `🔓 ${small ? "" : t("chart.available") + " "}${fmtDate(a.available_from, { day: "numeric", month: "short", year: "2-digit" })}` } : null,
    axis: (ticks) => axisLabels(ticks, cur),
    tooltip: (idx, rows) => rows.map((s) => ttRow(s, money(s.values[idx], cur), s.contributed ? "" : sinceText(s, s.values[idx], cur))).join("") +
      (contributed && a.values[idx] !== null ? `<div class="tt-since tt-gain">${gainText(a.values[idx], contributed[idx], cur)}</div>` : ""),
  });
  return drawn && { ...drawn, contributed: !!contributed };
}

// One account's chart, big, in a dialog (the tooltip moves into the dialog so it shows above it)
export function openMaximized(accountId, currency) {
  state.maximized = { id: accountId, currency };
  const dlg = $("#chart-dlg");
  dlg.appendChild($("#tooltip"));
  openDialog(dlg);
  drawMaximized();
}

export function drawMaximized() {
  const h = state.histories.find((x) => x.currency === state.maximized?.currency);
  const a = h && chartSeries(h).find((s) => s.account_id === state.maximized.id);
  if (!a) { $("#chart-dlg").close(); return; }
  $("#chart-dlg-head").innerHTML = accountHead(a, h);
  $("#chart-dlg-add").hidden = typeof a.account_id !== "number";
  const box = $("#chart-dlg-chart");
  box.innerHTML = "";
  const drawn = drawAccountChart(box, a, h, false);
  $("#chart-dlg-legend").innerHTML = drawn ? accountLineLegend(drawn, a.color, true) : "";
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
  // The account's dialog opens above the maximized chart, which is redrawn with the new balance when it is saved
  $("#chart-dlg-add").addEventListener("click", () => addBalanceFrom(state.maximized.id).catch(showError));
  $("#chart-dlg").addEventListener("close", () => {
    state.maximized = null;
    $("#tooltip").hidden = true;
    document.body.appendChild($("#tooltip"));
  });
}
