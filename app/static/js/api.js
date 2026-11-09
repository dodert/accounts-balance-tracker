// The client of the app's JSON API.

export const api = async (path, opts = {}) => {
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
