// Login and one-time code pages (kept out of the HTML: the Content-Security-Policy allows no inline scripts)
document.getElementById("login-error").hidden = !new URLSearchParams(location.search).has("error");
const form = document.forms[0];
if (form.dataset.mode) {
  document.querySelectorAll(".setup-only").forEach((el) => { el.hidden = form.dataset.mode !== "setup"; });
}
initI18n();
