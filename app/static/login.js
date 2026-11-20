// Login and one-time code pages (kept out of the HTML: the Content-Security-Policy allows no inline scripts)
const loginError = new URLSearchParams(location.search).get("error");
document.getElementById("login-error").hidden = loginError === null || loginError === "expired";
const expiredNote = document.getElementById("login-expired"); // only on the login page
if (expiredNote) expiredNote.hidden = loginError !== "expired";
const form = document.forms[0];
if (form.dataset.mode) {
  document.querySelectorAll(".setup-only").forEach((el) => { el.hidden = form.dataset.mode !== "setup"; });
}
initI18n().then(() => {
  // The demo's sample data in the language of the page
  document.querySelectorAll('a[href="/demo"]').forEach((a) => { a.href = `/demo?lang=${encodeURIComponent(i18n.lang)}`; });
});
