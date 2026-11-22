// What changed in each version (#205): the whole changelog from the header's button, and the changes of a new
// version on their own the first time the page opens after an update, unless the user turned that off (in ⚙ or in
// the dialog itself). The releases come from CHANGELOG.md (/api/changelog); the last version seen and the choice
// are kept in the user's database (/api/changelog/state), like the tour.
import { api } from "./api.js";
import { $, esc, showError } from "./dom.js";
import { fmtDate } from "./format.js";
import { state } from "./state.js";

const ISSUES = "https://github.com/dodert/accounts-balance-tracker/issues/";
// release-please's section titles (release-please-config.json); any other is shown as written
const SECTIONS = { Features: "features", "Bug fixes": "fixes", Performance: "performance", Reverts: "reverts" };

// "0.49.11" (also "0.49.11-main.4", a branch build) → [0, 49, 11]; [] when it isn't a version ("dev")
export const versionParts = (v) => (/^(\d+)\.(\d+)\.(\d+)/.exec(v ?? "") ?? []).slice(1).map(Number);
export function compareVersions(a, b) {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
}

const saveState = (body) => api("/api/changelog/state", { method: "PUT", json: body });

function renderRelease(release) {
  const sections = release.sections.map((section) => {
    const key = SECTIONS[section.title];
    const items = section.items.map((item) => {
      const issues = item.issues.map((n) =>
        ` <a href="${ISSUES}${n}" target="_blank" rel="noopener">#${n}</a>`).join("");
      return `<li>${item.scope ? `<strong>${esc(item.scope)}:</strong> ` : ""}${esc(item.text)}${issues}</li>`;
    }).join("");
    return `<h4>${esc(key ? t(`changelog.section_${key}`) : section.title)}</h4><ul>${items}</ul>`;
  }).join("");
  const date = release.date ? ` <span class="hint">· ${esc(fmtDate(release.date))}</span>` : "";
  return `<section class="changelog-release" data-version="${esc(release.version)}"><h3>${esc(release.version)}${date}</h3>${
    sections || `<p class="hint">${esc(t("changelog.no_changes"))}</p>`}</section>`;
}

// `releases` newest first; `fresh`: they are the ones new since the last visit (else: the whole changelog)
function show(releases, fresh, auto) {
  $("#changelog-dlg-title").textContent = fresh
    ? t("changelog.new_title", { version: state.about.version })
    : t("changelog.title");
  $("#changelog-list").innerHTML = releases.length
    ? releases.map(renderRelease).join("")
    : `<p class="hint">${esc(t("changelog.empty"))}</p>`;
  $("#changelog-all").hidden = !fresh;
  $("#changelog-auto").checked = auto;
  const dlg = $("#changelog-dlg");
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
}

export async function openChangelog() {
  const [data, current] = await Promise.all([api("/api/changelog"), api("/api/changelog/state")]);
  show(data.releases, false, current.auto);
}

// After the page loads. The first time (no version seen yet: a new user, or the first version with this) and while
// the tour runs, the current version is only marked as seen. `tourStarted`: the tour opened on this load.
export async function maybeShowChangelog(tourStarted) {
  const current = versionParts(state.about?.version);
  if (!current.length) return; // "dev": no version to compare
  const saved = await api("/api/changelog/state");
  const seen = versionParts(saved.seen);
  if (seen.length && compareVersions(seen, current) >= 0) return;
  await saveState({ seen: current.join(".") });
  if (!seen.length || !saved.auto || tourStarted) return;
  const { releases } = await api("/api/changelog");
  const fresh = releases.filter((r) => {
    const v = versionParts(r.version);
    return compareVersions(v, seen) > 0 && compareVersions(v, current) <= 0;
  });
  if (fresh.length) show(fresh, true, saved.auto);
}

export function initChangelog() {
  $("#changelog-btn").addEventListener("click", () => openChangelog().catch(showError));
  $("#changelog-all").addEventListener("click", () => openChangelog().catch(showError));
  $("#changelog-auto").addEventListener("change", (e) => {
    saveState({ auto: e.target.checked }).catch(showError);
  });
}
