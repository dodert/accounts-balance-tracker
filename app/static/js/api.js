// The client of the app's JSON API.

// The user's time zone: the one chosen in the settings (saved in this browser), or "" for the browser's own
const TZ_KEY = "abt-tz";
export const browserTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
export function savedTimeZone() {
  try { return localStorage.getItem(TZ_KEY) || ""; } catch { return ""; }
}
export function setTimeZone(zone) {
  try { localStorage.setItem(TZ_KEY, zone || ""); } catch {}
}

// The user's date (YYYY-MM-DD) in their time zone: the server uses it as "today" (app/clock.py)
export function localDate(d = new Date()) {
  const zone = savedTimeZone();
  let format;
  try {
    format = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", ...(zone ? { timeZone: zone } : {}) });
  } catch { // a zone this browser doesn't know: its own
    format = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" });
  }
  const part = Object.fromEntries(format.formatToParts(d).map((p) => [p.type, p.value]));
  return `${part.year}-${part.month}-${part.day}`;
}

export const api = async (path, opts = {}) => {
  const headers = { ...opts.headers, "X-Local-Date": localDate() };
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    opts = { ...opts, method: opts.method ?? "POST", body: JSON.stringify(opts.json) };
  }
  const res = await fetch(path, { ...opts, headers });
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
