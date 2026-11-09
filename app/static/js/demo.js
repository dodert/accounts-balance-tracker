// Demo mode: sample data in a database apart from the real one.
import { api } from "./api.js";
import { $, showError } from "./dom.js";
import { refresh } from "./main.js";
import { state } from "./state.js";

export async function startDemo() {
  await api(`/api/demo?lang=${encodeURIComponent(i18n.lang)}`, { method: "POST" });
  location.reload();
}

export async function stopDemo() {
  await api("/api/demo", { method: "DELETE" });
  location.reload();
}

export function renderDemo() {
  const on = !!state.about?.demo;
  $("#demo-banner").hidden = !on;
  document.body.classList.toggle("demo", on);
  $("#demo-toggle").textContent = t(on ? "demo.exit" : "demo.start");
  $("#demo-try").hidden = on;
}

export function initDemo() {
  $("#demo-try").addEventListener("click", () => startDemo().catch(showError));
  $("#demo-exit").addEventListener("click", () => stopDemo().catch(showError));
  $("#demo-toggle").addEventListener("click", () => (state.about?.demo ? stopDemo() : startDemo()).catch(showError));
  $("#demo-reset").addEventListener("click", async () => {
    try {
      await api(`/api/demo/reset?lang=${encodeURIComponent(i18n.lang)}`, { method: "POST" });
      await refresh();
    } catch (err) { showError(err); }
  });
}
