// Account and admin pages (kept out of the HTML: the Content-Security-Policy allows no inline scripts)
initI18n();
// Buttons that can't be undone ask first
document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!confirm(t(form.dataset.confirm))) event.preventDefault();
  });
});
