// The client of the app's JSON API.

// The browser's own date (YYYY-MM-DD, local time zone): the server uses it as "today" (app/clock.py)
export const localDate = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

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
