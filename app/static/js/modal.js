// How every dialog opens and closes. A click outside the box (on the backdrop), the Cancel / Close button and the
// Escape key all close it, asking first when a field was changed since the dialog opened and was not saved.
// Fields inside [data-unsaved-ignore] (e.g. the prepayment simulator, which saves nothing) never count.
import { $$ } from "./dom.js";

const baseline = new WeakMap(); // each field's value when it was last opened, saved or reset

const fields = (root) => [...root.querySelectorAll("input, select, textarea")]
  .filter((el) => !el.classList.contains("date-native") && !el.closest("[data-unsaved-ignore]"));

function valueOf(el) {
  if (el.type === "checkbox" || el.type === "radio") return el.checked;
  if (el.type === "file") return [...el.files].map((f) => f.name).join("\n");
  return el.value;
}

// The fields' current values are the saved ones: call it after filling or saving them
export function markClean(...roots) {
  for (const root of roots) for (const el of fields(root)) baseline.set(el, valueOf(el));
}

export const isDirty = (dlg) => fields(dlg).some((el) => baseline.has(el) && baseline.get(el) !== valueOf(el));

export function openDialog(dlg) {
  dlg.showModal();
  markClean(dlg);
}

// Closes it, unless it has unsaved changes and the user wants to keep them. True when it closed.
export function requestClose(dlg) {
  if (isDirty(dlg) && !confirm(t("common.discard_changes"))) return false;
  dlg.close();
  return true;
}

function outside(dlg, e) {
  const r = dlg.getBoundingClientRect();
  return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
}

export function initModals() {
  for (const dlg of $$("dialog")) {
    for (const btn of dlg.querySelectorAll(".cancel")) btn.addEventListener("click", () => requestClose(dlg));
    // Escape. (Browsers close it anyway on a second Escape without any click or typing in between.)
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      requestClose(dlg);
    });
    // The backdrop is the dialog element itself, outside its box. Only a click that also started there counts, so
    // selecting text inside and releasing the mouse outside doesn't close it.
    let downOutside = false;
    dlg.addEventListener("pointerdown", (e) => { downOutside = e.target === dlg && outside(dlg, e); });
    dlg.addEventListener("click", (e) => {
      if (downOutside && e.target === dlg && outside(dlg, e)) requestClose(dlg);
      downOutside = false;
    });
  }
}
