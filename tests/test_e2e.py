"""End-to-end tests: the real page in a browser (Chromium, with Playwright) against the app on a local port.

They need `pip install playwright` and `python -m playwright install chromium`. Without them they are
skipped, unless E2E=1 (as in the CI), which turns that into a failure. PLAYWRIGHT_CHROMIUM can point to a
Chromium binary to use instead of Playwright's own.

After each test, passed or failed, the whole page is saved as test-screenshots/<test>.png (the CI keeps the
folder as an artifact of the run).
"""
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.config import Settings
from app.demo import SAMPLES
from app.main import create_app

SCREENSHOTS = Path(__file__).resolve().parent.parent / "test-screenshots"

if os.environ.get("E2E"):
    from playwright import sync_api as playwright_api
else:
    playwright_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve(app):
    """`app` served by uvicorn on a free local port; yields its address."""
    import uvicorn
    port = free_port()
    app_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=app_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not app_server.started:
        assert time.time() < deadline, "the app did not start"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        app_server.should_exit = True
        thread.join(5)


@pytest.fixture
def server(request, tmp_path):
    """The app served on a free local port: single user and no login, unless the test is marked
    `@pytest.mark.settings(...)` with other settings."""
    marker = request.node.get_closest_marker("settings")
    settings = Settings(data_dir=tmp_path, default_currency="EUR", **(marker.kwargs if marker else {}))
    app = create_app(settings)
    request.node.app = app
    with serve(app) as address:
        yield address


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM") or None)
        except Exception as error:  # noqa: BLE001
            if os.environ.get("E2E"):
                raise
            pytest.skip(f"Chromium can't start: {error}")
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser, server, request):
    """A fresh browser page on the app, in English, failing the test on any JavaScript error. Its screenshot is
    saved when the test ends, also when it failed."""
    context = browser.new_context(base_url=server, viewport={"width": 1280, "height": 900})
    context.add_init_script("localStorage.setItem('abt-lang', 'en')")
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.on("dialog", lambda dialog: dialog.accept())  # confirm() questions: yes
    yield tab
    SCREENSHOTS.mkdir(exist_ok=True)
    tab.screenshot(path=SCREENSHOTS / f"{re.sub(r'[^\w.-]', '_', request.node.name)}.png", full_page=True)
    context.close()
    assert errors == [], errors


def open_app(page):
    page.goto("/")
    page.wait_for_selector("#version:not(:empty)")


def test_the_page_loads_with_no_data(page):
    """The page loads its modules, shows the version, the time zone and the 'no accounts yet' message."""
    open_app(page)
    assert page.locator("#version").text_content().startswith("v")
    assert page.locator("#empty").is_visible()
    assert page.locator("#empty .hint").text_content().startswith("No accounts yet")
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    assert page.locator("#tz-badge").text_content() == f"🕒 {zone.replace('_', ' ')}"  # the browser's time zone


def test_add_an_account_and_a_balance(page):
    """An account made in its dialog, then a balance added to it, show in the list and the totals."""
    open_app(page)
    page.click("#add-account-btn")
    page.fill("#account-form input[name=name]", "Savings")
    page.fill("#account-form input[name=institution]", "Bank")
    page.click("#account-form button[type=submit]")
    row = page.locator("#accounts-table tbody tr", has_text="Savings")
    row.wait_for()
    row.click()
    page.fill("#nb-amount", "1,234.5")  # English regional format: a decimal point
    page.click("#nb-add")
    page.locator("#balances-table tbody td.num", has_text="1,234.50").first.wait_for()
    page.keyboard.press("Escape")
    page.locator("#totals .tile-value", has_text="1,234.50").wait_for()
    assert "1,234.50" in row.text_content()


def test_contributions_show_next_to_the_value(page):
    """An investment account's contributions show in its dialog, as a dashed line on its chart, and as the gain."""
    open_app(page)
    page.click("#add-account-btn")
    page.fill("#account-form input[name=name]", "Fund")
    page.select_option("#account-type", "bank")
    page.click("#account-form button[type=submit]")
    row = page.locator("#accounts-table tbody tr", has_text="Fund")
    row.wait_for()
    row.click()
    assert page.locator("#contributions-section").is_hidden()  # only for investment-like accounts
    page.select_option("#account-type", "investment")
    assert page.locator("#contributions-section").is_visible()
    for day, amount, shown in (("2026-01-01", "1000", "1,000.00"), ("2026-02-01", "1300", "1,300.00")):
        page.fill("#nb-date", day)
        page.fill("#nb-amount", amount)
        page.click("#nb-add")
        page.locator("#balances-table tbody td.num", has_text=shown).first.wait_for()
    for n, (day, amount) in enumerate((("2026-01-01", "1000"), ("2026-01-15", "200")), 1):
        page.fill("#nc-date", day)
        page.fill("#nc-amount", amount)
        page.click("#nc-add")
        page.locator("#contributions-table tbody tr:has(td.num)").nth(n - 1).wait_for()
    assert page.locator("#contributions-table tbody tr").count() == 2
    summary = page.locator("#contributions-summary").text_content()
    assert "1,200.00" in summary and "+€100.00 (+8.3%)" in summary, summary
    page.click("#account-form button[type=submit]")
    mini = page.locator("#multiples .mini", has_text="Fund")
    mini.locator(".mini-contributed").wait_for()
    assert mini.locator(".mini-contributed").text_content().endswith("Gain +8.3%")
    assert mini.locator("svg path.line.dashed").count() == 1


def test_update_balances_of_every_account_at_once(page):
    """The 'Update balances' dialog saves a balance for each account on one date."""
    open_app(page)
    for name in ("One", "Two"):
        page.click("#add-account-btn")
        page.fill("#account-form input[name=name]", name)
        page.click("#account-form button[type=submit]")
        page.locator("#accounts-table tbody tr", has_text=name).wait_for()
    page.click("#update-btn")
    inputs = page.locator("#update-rows input")
    inputs.nth(0).fill("100")
    inputs.nth(1).fill("50.25")
    page.click("#update-form button[type=submit]")
    page.locator("#totals .tile-value", has_text="150.25").wait_for()


def test_import_a_csv(page):
    """A CSV imported from its dialog says what it added and the accounts appear."""
    open_app(page)
    page.click("#data-menu summary")
    page.click("#csv-import-btn")
    csv = b"account,institution,date,amount\nCurrent,Bank,2026-01-31,100\nCurrent,Bank,2026-02-28,120\n"
    page.set_input_files("#csv-form input[name=file]", files=[{"name": "b.csv", "mimeType": "text/csv", "buffer": csv}])
    page.click("#csv-form button[type=submit]")
    result = page.locator("#csv-result")
    result.wait_for()
    assert "1" in result.text_content() and "2" in result.text_content()
    page.keyboard.press("Escape")
    page.locator("#accounts-table tbody tr", has_text="Current").wait_for()


def test_demo_mode_with_charts(page):
    """The demo shows its sample accounts and charts (amounts, percent, maximized), and leaving it ends it."""
    open_app(page)
    page.click("#demo-try")
    page.wait_for_selector("#demo-banner:not([hidden])")
    page.wait_for_selector("#chart svg")
    assert page.locator("#accounts-table tbody tr").count() == sum(1 for s in SAMPLES if s.closed_month is None)
    assert page.locator("#multiples .mini").count() > 0
    # The background says whether each balance went up (green), trends down (orange) or went down (red)
    assert page.locator("#chart svg .trend-bg").count() == 1
    for trend in ("up", "falling", "down"):
        assert page.locator(f"#multiples .trend-{trend}").count() > 0, trend
    # Investment accounts with contributions: what was put in, dashed, and the gain
    fund = page.locator("#multiples .mini", has_text="Index fund")
    assert fund.locator(".mini-contributed").count() == 1 and fund.locator("svg path.line.dashed").count() == 1
    bitcoin = page.locator("#multiples .mini", has_text="Bitcoin")
    assert bitcoin.locator(".trend-falling").count() == 1
    # On a shorter range too, the background agrees with "since the start": red exactly when that is down
    page.select_option("#chart-range", "3")
    page.wait_for_function("document.querySelectorAll('#multiples .mini .trend-bg').length > 0")
    minis = page.locator("#multiples .mini")
    for i in range(minis.count()):
        mini = minis.nth(i)
        assert (mini.locator(".mini-sub .down").count() > 0) == (mini.locator(".trend-down").count() > 0), mini.inner_text()
    page.select_option("#chart-range", "all")
    page.wait_for_function("document.querySelectorAll('#multiples .mini .trend-falling').length > 0")
    page.click("#chart-panel .views button[data-mode=percent]")
    page.wait_for_selector("#chart svg")
    assert page.locator("#chart svg .trend-bg").count() == 1
    page.click("#chart-panel .views button[data-mode=amounts]")
    page.locator("#multiples .mini-max").first.click()
    page.wait_for_selector("#chart-dlg[open] #chart-dlg-chart svg")
    assert page.locator("#chart-dlg-chart svg .trend-bg").count() == 1
    page.keyboard.press("Escape")
    page.click("#demo-exit")
    page.wait_for_selector("#demo-banner[hidden]", state="attached")
    page.locator("#empty").wait_for()


def test_change_the_language(page):
    """Choosing Spanish in the settings translates the page."""
    open_app(page)
    page.click("#settings-btn")
    page.locator("#lang-select option[value=es]").wait_for(state="attached")
    page.select_option("#lang-select", "es")
    page.click("#settings-form button[type=submit]")
    page.locator("#update-btn", has_text="Actualizar").wait_for()


def test_the_page_sends_its_local_date(page):
    """Each API call carries the browser's own date, which the server uses as "today"."""
    with page.expect_request(lambda r: "/api/" in r.url) as first:
        open_app(page)
    local = page.evaluate("(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1)"
                          ".padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); })()")
    assert first.value.headers.get("x-local-date") == local


def test_typed_amounts_become_exact_ten_thousandths(page):
    """Typed amounts reach the API as exact integer ten-thousandths; totals show the currency's 2 decimals."""
    open_app(page)
    for name in ("One", "Two"):
        page.click("#add-account-btn")
        page.fill("#account-form input[name=name]", name)
        page.click("#account-form button[type=submit]")
        page.locator("#accounts-table tbody tr", has_text=name).wait_for()
    page.click("#update-btn")
    inputs = page.locator("#update-rows input")
    inputs.nth(0).fill("1,234,567.89995")  # more than 4 decimals: rounded half up
    inputs.nth(1).fill("0.1")
    with page.expect_request(lambda r: r.url.endswith("/api/balances/bulk")) as sent:
        page.click("#update-form button[type=submit]")
    assert [i["amount"] for i in sent.value.post_data_json["items"]] == [12345679000, 1000]
    page.locator("#totals .tile-value", has_text="1,234,568.00").wait_for()  # 1,234,567.90 + 0.10


def test_the_dates_follow_the_time_zone_chosen_in_the_settings(page):
    """With another time zone chosen in the settings, the page sends the date of that zone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    open_app(page)
    page.click("#settings-btn")
    page.locator("#tz-select option[value='Pacific/Kiritimati']").wait_for(state="attached")
    page.select_option("#tz-select", "Pacific/Kiritimati")  # UTC+14
    with page.expect_request(lambda r: "/api/" in r.url) as sent:
        page.click("#settings-form button[type=submit]")
    assert sent.value.headers.get("x-local-date") == datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
    page.locator("#tz-badge", has_text="Pacific/Kiritimati").wait_for()  # shown next to the title


def test_the_settings_list_the_daily_backups(page, server):
    """The settings show the daily backups the server made (one made on start)."""
    deadline = time.time() + 10  # the start's backup is made in the background
    while not page.request.get(f"{server}/api/backups").json()["backups"]:
        assert time.time() < deadline, "no backup made on start"
        time.sleep(0.05)
    open_app(page)
    page.click("#settings-btn")
    page.locator("#backups-list li").first.wait_for()
    assert page.locator("#backups-list li").count() == 1
    assert "7 days" in page.locator("#backups-hint").inner_text()


def test_typed_amounts_follow_the_decimal_separator_of_the_regional_format(page):
    """With a comma as decimal separator, 1.234 is one thousand; an amount that doesn't fit it is an error."""
    page.add_init_script("localStorage.setItem('abt-locale', 'es-ES')")
    open_app(page)
    page.click("#add-account-btn")
    page.fill("#account-form input[name=name]", "Savings")
    page.click("#account-form button[type=submit]")
    page.locator("#accounts-table tbody tr", has_text="Savings").wait_for()
    page.click("#update-btn")
    amount = page.locator("#update-rows input").first
    amount.fill("1,234.56")  # a point does not fit es-ES
    page.click("#update-form button[type=submit]")
    page.locator("#update-dlg", has_text="Invalid amount: 1,234.56").wait_for()
    amount.fill("1.234")
    with page.expect_request(lambda r: r.url.endswith("/api/balances/bulk")) as sent:
        page.click("#update-form button[type=submit]")
    assert sent.value.post_data_json["items"][0]["amount"] == 12340000


def test_csv_import_states_the_decimal_separator(page):
    """The CSV dialog starts with the regional format's decimal separator, and changing it changes the amounts."""
    page.add_init_script("localStorage.setItem('abt-locale', 'es-ES')")
    open_app(page)
    csv = b"account,date,amount\nComma,2026-01-31,1.234\n"
    for decimal, name, shown in ((",", "Comma", "1234,00"), (".", "Point", "1,23")):
        page.click("#data-menu summary")
        page.click("#csv-import-btn")
        assert page.locator("#csv-decimal").input_value() == ","  # es-ES: a comma, whatever was chosen before
        assert "es-ES" in page.locator("#csv-decimal-hint").text_content()
        page.select_option("#csv-decimal", decimal)
        data = csv.replace(b"Comma", name.encode())
        page.set_input_files("#csv-form input[name=file]", files=[{"name": "b.csv", "mimeType": "text/csv", "buffer": data}])
        page.click("#csv-form button[type=submit]")
        page.locator("#csv-result").wait_for()
        page.keyboard.press("Escape")
        page.locator("#accounts-table tbody tr", has_text=name).locator("td", has_text=shown).first.wait_for()


def test_a_balance_with_four_decimals_is_kept_and_shown(page):
    """A balance typed with four decimals is saved as such and shown back with them; the total shows two."""
    page.add_init_script("localStorage.setItem('abt-locale', 'es-ES')")
    open_app(page)
    page.click("#add-account-btn")
    page.fill("#account-form input[name=name]", "Fund")
    page.click("#account-form button[type=submit]")
    row = page.locator("#accounts-table tbody tr", has_text="Fund")
    row.wait_for()
    row.click()
    page.fill("#nb-amount", "1,2345")
    with page.expect_request(lambda r: r.url.endswith("/balances") and r.method == "POST") as sent:
        page.click("#nb-add")
    assert sent.value.post_data_json["amount"] == 12345
    page.locator("#balances-table tbody td.num", has_text="1,2345").first.wait_for()
    page.keyboard.press("Escape")
    page.locator("#totals .tile-value", has_text="1,23").wait_for()
    assert "1,2345" in row.text_content() and "1,2345" not in page.locator("#totals").text_content()
    page.click("#update-btn")
    assert page.locator("#update-rows input").first.input_value() == "1,2345"  # edited with its 4 decimals


def test_chart_date_and_range_helpers(page):
    """monthsBefore clamps to the end of a shorter month and extent works past the Math.min spread limit."""
    open_app(page)
    src = page.evaluate("[...document.scripts].map((s) => s.src).find((s) => s.includes('/js/main.js'))")
    result = page.evaluate("""async (src) => {
        const { monthsBefore, extent } = await import(src.replace('main.js', 'charts.js'));
        const iso = (d) => [d.getFullYear(), d.getMonth() + 1, d.getDate()].join('-');
        const many = Array.from({ length: 200000 }, (_, i) => i - 1000);
        return {
            may31: iso(monthsBefore(new Date(2026, 4, 31), 3)),
            may31Leap: iso(monthsBefore(new Date(2028, 4, 31), 3)),
            mar31: iso(monthsBefore(new Date(2026, 2, 31), 6)),
            jan15: iso(monthsBefore(new Date(2026, 0, 15), 1)),
            extent: extent(many),
        };
    }""", src)
    assert result == {"may31": "2026-2-28", "may31Leap": "2028-2-29", "mar31": "2025-9-30", "jan15": "2025-12-15",
                      "extent": [-1000, 198999]}


@pytest.mark.settings(multi_user=True, auth_username="admin", auth_password="admin-password-1", country_db=False)
def test_the_login_page_says_when_the_account_expired(page, request, tmp_path):
    """With the right password of an expired account, the login page says it expired; with a wrong one, the usual error."""
    import sqlite3
    users = request.node.app.state.users
    users.sign_up(users.invite(), "ana", "ana@example.com", "ana-password")
    with sqlite3.connect(tmp_path / "users.db") as db:
        db.execute("UPDATE users SET expires_at = '2020-01-01 00:00:00' WHERE username = 'ana'")

    def log_in(password):
        page.goto("/login")
        page.fill("input[name=username]", "ana")
        page.fill("input[name=password]", password)
        page.click("button[type=submit]")
        page.wait_for_url("**/login?error=*")

    log_in("not-hers")
    assert page.locator("#login-error").is_visible() and page.locator("#login-expired").is_hidden()
    log_in("ana-password")
    page.locator("#login-expired").wait_for()
    assert page.locator("#login-expired").text_content() == "Your account has expired. Contact the administrator to extend it."
    assert page.locator("#login-error").is_hidden()
