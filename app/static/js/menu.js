// The header's Import / export menu (#182): a popover opened by its button, so the browser closes it with Esc or a
// click outside. It closes too when one of its items is chosen. Where anchor positioning is missing it is placed
// here, under the button and aligned to its right edge; where popovers are missing it is shown and hidden by hand.
import { $ } from "./dom.js";

const hasPopover = Object.hasOwn(HTMLElement.prototype, "popover");
const hasAnchor = window.CSS?.supports?.("position-area: bottom") ?? false;

function place(btn, items) {
  const r = btn.getBoundingClientRect();
  items.style.top = `${r.bottom + 4}px`;
  items.style.right = `${document.documentElement.clientWidth - r.right}px`;
}

export function closeMenu() {
  const items = $("#data-menu-items");
  if (hasPopover) items.hidePopover();
  else items.hidden = true;
}

export function initMenu() {
  const btn = $("#data-menu .menu-btn"), items = $("#data-menu-items");
  if (hasPopover) {
    if (!hasAnchor) items.addEventListener("beforetoggle", (e) => { if (e.newState === "open") place(btn, items); });
  } else {
    items.hidden = true;
    btn.addEventListener("click", () => {
      place(btn, items);
      items.hidden = !items.hidden;
    });
  }
  items.addEventListener("click", (e) => { if (e.target.closest("a, button")) closeMenu(); });
}
