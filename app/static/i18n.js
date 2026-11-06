// Interface translations. Each language is one JSON file in /static/i18n/ named by its
// code (en.json, es.json...) with a "_name" entry for the language menu; en.json is the
// reference, and any key a translation lacks falls back to English.
const LANG_KEY = "abt-lang";
const DEFAULT_LANG = "en";
const ASSET_VERSION = document.querySelector("meta[name=asset-version]")?.content ?? "";
const i18n = { lang: DEFAULT_LANG, strings: {}, fallback: {} };

// t("key", {n: 3}) -> the text for the current language with {n} filled in
function t(key, vars = {}) {
  const text = i18n.strings[key] ?? i18n.fallback[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
}

async function fetchLocale(code) {
  const res = await fetch(`/static/i18n/${encodeURIComponent(code)}.json?v=${ASSET_VERSION}`);
  if (!res.ok) throw new Error(`Language '${code}' not found`);
  return res.json();
}

function savedLang() {
  try { return localStorage.getItem(LANG_KEY) || DEFAULT_LANG; } catch { return DEFAULT_LANG; }
}

function setLang(code) {
  try { localStorage.setItem(LANG_KEY, code); } catch {}
}

// Texts and attributes marked with data-i18n* in the page
const I18N_ATTRS = { placeholder: "placeholder", title: "title", "aria-label": "ariaLabel", alt: "alt" };

function translatePage(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  for (const [attr, prop] of Object.entries(I18N_ATTRS)) {
    const dataKey = `i18n${prop[0].toUpperCase()}${prop.slice(1)}`;
    root.querySelectorAll(`[data-i18n-${attr}]`).forEach((el) => el.setAttribute(attr, t(el.dataset[dataKey])));
  }
}

async function initI18n() {
  i18n.fallback = await fetchLocale(DEFAULT_LANG);
  let lang = savedLang();
  try {
    i18n.strings = lang === DEFAULT_LANG ? i18n.fallback : await fetchLocale(lang);
  } catch {
    lang = DEFAULT_LANG;
    i18n.strings = i18n.fallback;
  }
  i18n.lang = lang;
  document.documentElement.lang = lang;
  translatePage();
}
