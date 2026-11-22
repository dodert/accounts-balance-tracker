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
    saved when the test ends, also when it failed. The guided tour is taken as seen unless the test is marked
    `@pytest.mark.tour`."""
    context = browser.new_context(base_url=server, viewport={"width": 1280, "height": 900})
    context.add_init_script("localStorage.setItem('abt-lang', 'en')")
    if not request.node.get_closest_marker("tour"):
        context.route("**/api/tour", lambda route: route.fulfill(json={"seen": 1000})
                      if route.request.method == "GET" else route.continue_())
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
    page.locator("#empty").wait_for()  # shown once the accounts are loaded, after the version
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


def test_the_background_follows_the_gain_or_the_value_as_chosen_in_the_settings(page):
    """With contributions the background follows the gain (red here) by default, or the value (green) in ⚙."""
    euro = 10000  # amounts in ten-thousandths
    fund = page.request.post("/api/accounts", data={"name": "Fund", "type": "investment"}).json()
    cash = page.request.post("/api/accounts", data={"name": "Cash", "type": "cash"}).json()
    for account, day, amount in ((fund, "2026-01-01", 1000), (fund, "2026-02-01", 1500), (cash, "2026-01-01", 100)):
        page.request.post(f"/api/accounts/{account['id']}/balances", data={"date": day, "amount": amount * euro})
    # Up 500 in value, but 600 of it was put in: the gain went from 0 to -100
    for day, amount in (("2026-01-01", 1000), ("2026-01-15", 600)):
        page.request.post(f"/api/accounts/{fund['id']}/contributions", data={"date": day, "amount": amount * euro})
    open_app(page)
    mini = page.locator("#multiples .mini", has_text="Fund")
    mini.locator(".trend-bg").wait_for()
    assert mini.locator(".trend-down.trend-gain").count() == 1
    assert page.locator("#chart svg .trend-down.trend-gain").count() == 1  # the total too: 100 then 0 of gain
    # "Since the start" says how the gain changed, in the background's colour
    since = mini.locator(".trend-text-down")
    assert "-€100.00" in since.text_content(), since.text_content()
    # and so does the accounts list
    row = page.locator("#accounts-table tbody tr", has_text="Fund")
    assert "-€100.00" in row.locator(".trend-text-down").text_content()
    assert page.locator("#multiples .mini", has_text="Cash").locator(".trend-up:not(.trend-gain)").count() == 1
    assert "Gain down" in mini.locator(".trend-bg title").text_content()

    page.click("#settings-btn")
    assert page.locator("#trend-select").input_value() == "gain"
    page.select_option("#trend-select", "value")
    page.click("#settings-form button[type=submit]")
    mini.locator(".trend-up").wait_for()
    assert mini.locator(".trend-gain").count() == 0 and page.locator("#chart svg .trend-up").count() == 1
    assert "+€500.00" in mini.locator(".trend-text-up").text_content()
    assert "+€500.00" in row.locator(".trend-text-up").text_content()
    page.reload()  # remembered in this browser
    page.locator("#multiples .mini", has_text="Fund").locator(".trend-up").wait_for()

    # In the converted chart, contributions in another currency count at the chart's rate, like the balances
    src = page.evaluate("[...document.scripts].map((s) => s.src).find((s) => s.includes('/js/main.js'))")
    result = page.evaluate("""async (src) => {
        const { contributionsUpTo } = await import(src.replace('main.js', 'charts.js'));
        const { state } = await import(src.replace('main.js', 'state.js'));
        const saved = [state.accounts, state.contributions];
        state.accounts = [{ id: 1, currency: "USD" }, { id: 2, currency: "EUR" }, { id: 3, currency: "GBP" }];
        state.contributions = [{ account_id: 1, date: "2026-01-10", amount: 1000 }, { account_id: 2, date: "2026-02-10", amount: 50 },
                               { account_id: 3, date: "2026-01-10", amount: 7 }];
        const h = { currency: "EUR", dates: ["2026-01-01", "2026-02-01", "2026-03-01"], rates: { USD: 0.9 } };
        const out = { converted: contributionsUpTo([1, 2, 3], h, "2026-01-31"), none: contributionsUpTo([3], h, null) };
        [state.accounts, state.contributions] = saved;
        return out;
    }""", src)
    assert result == {"converted": {"values": [0, 900, 950], "atStart": 900, "firstDate": "2026-01-10", "atFirst": 900},
                      "none": None}


def test_the_accounts_list_follows_the_gain_whatever_currency_the_chart_shows(page):
    """The list's "since the start" follows each account's gain also with "All in EUR" or another currency chosen."""
    euro = 10000
    for name, currency in (("Fund EUR", "EUR"), ("Fund USD", "USD")):
        account = page.request.post("/api/accounts", data={"name": name, "type": "investment", "currency": currency}).json()
        for day, amount in (("2026-01-01", 1000), ("2026-02-01", 1500)):
            page.request.post(f"/api/accounts/{account['id']}/balances", data={"date": day, "amount": amount * euro})
        for day, amount in (("2026-01-01", 1000), ("2026-01-15", 600)):  # up 500, but 600 put in
            page.request.post(f"/api/accounts/{account['id']}/contributions", data={"date": day, "amount": amount * euro})
    open_app(page)
    rows = page.locator("#accounts-table tbody tr")
    page.locator("#accounts-table .trend-text-down").nth(1).wait_for()
    for choice in ("=", "EUR", "USD"):
        page.select_option("#chart-currency", choice)
        page.wait_for_load_state("networkidle")
        for name in ("Fund EUR", "Fund USD"):
            cell = rows.filter(has_text=name).locator(".trend-text-down")
            assert cell.count() == 1 and "100.00" in cell.text_content(), (choice, name)


def test_a_balance_before_any_contribution_is_flagged_until_the_contribution_is_added(page):
    """An investment account worth something before its first contribution shows a warning everywhere until fixed."""
    euro = 10000
    fund = page.request.post("/api/accounts", data={"name": "Shares", "type": "investment"}).json()
    page.request.post("/api/accounts", data={"name": "Crypto", "type": "crypto"})
    page.request.post("/api/accounts", data={"name": "Bank", "type": "bank"})
    for day, amount in (("2025-10-01", 556.67), ("2026-01-10", 510), ("2026-11-20", 855.42)):
        page.request.post(f"/api/accounts/{fund['id']}/balances", data={"date": day, "amount": round(amount * euro)})
    for day, amount in (("2026-01-05", 500), ("2026-11-15", 502.07)):
        page.request.post(f"/api/accounts/{fund['id']}/contributions", data={"date": day, "amount": round(amount * euro)})
    open_app(page)
    row = page.locator("#accounts-table tbody tr", has_text="Shares")
    row.locator(".uncovered").wait_for()
    assert page.locator("#accounts-table tbody tr", has_text="Bank").locator(".uncovered").count() == 0
    mini = page.locator("#multiples .mini", has_text="Shares")
    mini.locator(".uncovered").wait_for()
    # The start is the first contribution (a gain of 0): now 855.42 - 1,002.07 = -146.65, -14.6% of what was put in
    assert "-€146.65 (-14.6%)" in mini.locator(".trend-text-down").text_content()

    row.click()
    warning = page.locator("#uncovered-warning")
    warning.wait_for()
    assert "Oct" in warning.text_content() and page.locator("#nb-warning").is_hidden()
    page.fill("#nb-date", "2025-09-01")  # a balance before any contribution: warned while typing it
    page.locator("#nb-warning").wait_for()
    page.fill("#nb-date", "2026-02-01")
    assert page.locator("#nb-warning").is_hidden()
    page.click("#uncovered-fix")  # takes to the contribution, dated on that balance
    assert "2025" in page.locator("#nc-date").input_value() and page.evaluate("document.activeElement.id") == "nc-amount"
    page.fill("#nc-amount", "556.67")
    page.click("#nc-add")
    warning.wait_for(state="hidden")
    page.keyboard.press("Escape")
    page.locator("#accounts-table tbody tr", has_text="Shares").locator(".uncovered").wait_for(state="detached")
    assert page.locator("#multiples .mini", has_text="Shares").locator(".uncovered").count() == 0

    # "Update balances": the investment-like accounts without a contribution by that date
    page.click("#update-btn")
    page.locator("#update-dlg[open]").wait_for()
    assert page.locator(".u-warn:not([hidden])").count() == 1  # Crypto, with no contributions
    page.fill("#update-form input[name=date]", "2025-01-01")
    page.locator("#update-form input[name=date]").dispatch_event("input")
    assert page.locator(".u-warn:not([hidden])").count() == 2  # and Shares, before its first contribution


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


def test_the_data_menu_closes_with_escape_a_click_outside_or_an_item(page):
    """The Import / export menu opens under its button and closes with Esc, a click outside or one of its items."""
    open_app(page)
    items = page.locator("#data-menu-items")
    button = page.locator("#data-menu .menu-btn")
    for close in (lambda: page.keyboard.press("Escape"), lambda: page.mouse.click(5, 300)):
        button.click()
        items.wait_for(state="visible")
        menu, btn = items.bounding_box(), button.bounding_box()
        assert menu["y"] >= btn["y"] + btn["height"] and abs(menu["x"] + menu["width"] - (btn["x"] + btn["width"])) < 2
        close()
        items.wait_for(state="hidden")
    button.click()
    page.click("#json-import-btn")
    assert page.locator("#json-dlg").evaluate("d => d.open")
    assert not items.is_visible()


def test_the_chart_tooltip_shows_above_the_maximized_chart(page):
    """Hovering an account's maximized chart shows the tooltip in front of its window, and closing it hides it."""
    open_app(page)
    page.click("#demo-try")
    page.wait_for_selector("#demo-banner:not([hidden])")
    page.locator("#multiples .mini-max").first.click()
    page.wait_for_selector("#chart-dlg[open] #chart-dlg-chart svg")
    box = page.locator("#chart-dlg-chart svg rect[fill=transparent]").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    tooltip = page.locator("#tooltip")
    tooltip.wait_for(state="visible")
    assert tooltip.evaluate("t => t.matches(':popover-open') && t.parentElement === document.body")
    # It is drawn in front of the dialog: its area looks different with the tooltip made invisible
    clip = tooltip.bounding_box()
    shown = page.screenshot(clip=clip)
    tooltip.evaluate("t => { t.style.visibility = 'hidden'; }")
    assert page.screenshot(clip=clip) != shown
    tooltip.evaluate("t => { t.style.visibility = ''; }")
    page.keyboard.press("Escape")
    tooltip.wait_for(state="hidden")


def test_import_a_csv(page):
    """A CSV imported from its dialog says what it added and the accounts appear."""
    open_app(page)
    page.click("#data-menu .menu-btn")
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
        page.click("#data-menu .menu-btn")
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


def test_the_legend_explains_each_kind_of_line_drawn(page):
    """The legends list a sample of each kind of line drawn on their charts (solid, dashed, start, milestone), no other."""
    open_app(page)
    page.click("#demo-try")
    page.wait_for_selector("#multiples .grid-legend")

    def check(legend, charts):
        """The kinds in a legend match the lines drawn on its charts."""
        kinds = set(page.locator(f"{legend} .legend-line").evaluate_all("els => els.map((e) => e.dataset.kind)"))
        drawn = page.evaluate("""(charts) => {
            const has = (sel) => [...document.querySelectorAll(charts)].some((svg) => svg.querySelector(sel));
            return { start: has('.start-line'), milestone: has('.milestone-line'), marker: has('.avail-line'),
                     dashed: has('path.line.dashed') };
        }""", charts)
        for kind, present in drawn.items():
            assert (kind in kinds) == present, (legend, kind, kinds)
        assert page.locator(f"{legend} .legend-line svg.legend-sample line").count() == len(kinds)  # a sample each
        return kinds

    total = check("#chart-legend", "#chart svg")
    assert {"total", "start", "milestone"} <= total
    grid = check("#multiples .grid-legend", "#multiples .mini svg")
    assert {"solid", "dashed", "start", "milestone"} <= grid  # contributed (index fund) and milestones in the demo
    assert page.locator("#multiples .grid-legend").count() == 1  # one for the whole grid, not one per card
    assert "Milestone (notable expense)" in page.locator("#multiples .grid-legend").inner_text()
    # An account without milestones nor contributions: its maximized chart's legend has neither
    page.locator("#multiples .mini", has_text="Cash").locator(".mini-max").click()
    page.wait_for_selector("#chart-dlg[open] #chart-dlg-legend .legend-line")
    kinds = check("#chart-dlg-legend", "#chart-dlg-chart svg")
    assert "milestone" not in kinds and "dashed" not in kinds and "solid" in kinds
    page.keyboard.press("Escape")
    # The percent view: the accounts by colour, then the start (0 %) and milestones
    page.click("#chart-panel .views button[data-mode=percent]")
    page.wait_for_selector("#chart-legend .legend-line")
    check("#chart-legend", "#chart svg")
def test_a_balance_can_be_added_from_an_account_chart(page):
    """The ＋ of an account's small chart, and the button of its maximized chart, open that account ready to add a balance."""
    open_app(page)
    page.click("#demo-try")
    cash = page.locator("#multiples .mini", has_text="Cash")
    cash.locator(".mini-add").click()
    page.wait_for_selector("#account-dlg[open]")
    assert page.locator("#account-dlg-title").text_content() == cash.locator(".mini-name").evaluate("e => e.firstChild.textContent")
    assert page.evaluate("document.activeElement.id") == "nb-amount"
    page.fill("#nb-amount", "1234")
    page.click("#nb-add")
    cash.locator(".mini-value", has_text="1,234").wait_for()
    page.locator("#account-dlg .cancel").click()
    page.wait_for_selector("#account-dlg:not([open])", state="attached")
    # From the maximized chart: the account opens above it, and closing it goes back to the chart
    cash.locator(".mini-max").click()
    page.wait_for_selector("#chart-dlg[open]")
    page.click("#chart-dlg-add")
    page.wait_for_selector("#account-dlg[open]")
    assert page.evaluate("document.activeElement.id") == "nb-amount"
    page.keyboard.press("Escape")
    page.wait_for_selector("#account-dlg:not([open])", state="attached")
    assert page.locator("#chart-dlg[open]").count() == 1
    page.keyboard.press("Escape")
    # Grouped by institution, a series is several accounts: no button
    page.select_option("#chart-group", "institution")
    page.wait_for_selector("#multiples .mini")
    assert page.locator("#multiples .mini-add").count() == 0
    page.locator("#multiples .mini-max").first.click()
    page.wait_for_selector("#chart-dlg[open]")
    assert page.locator("#chart-dlg-add").is_hidden()


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


def test_create_a_mortgage_and_add_a_prepayment(page):
    """A mortgage made in its dialog shows its applied rate, payment and schedule; a prepayment that reduces the term
    shortens it, and both show in the list."""
    open_app(page)
    page.locator("#mortgages-empty").wait_for()
    page.click("#add-mortgage-btn")
    form = page.locator("#mortgage-form")
    form.locator("input[name=name]").fill("Home")
    form.locator("input[name=principal]").fill("100,000")  # English regional format
    form.locator("input[name=term_months]").fill("360")
    form.locator("input[name=rate]").fill("3.5")
    form.locator("input[name=discount]").fill("0.5")
    assert page.locator("#mortgage-rate-hint").text_content().startswith("Applied rate: 3.00%")
    form.locator("input[name=first_payment]").fill("2020-01-31")
    form.locator("button[type=submit]").click()
    # Saved: the dialog opens again with its summary, chart and schedule
    page.locator("#mortgage-summary .tile-value", has_text="€421.60").wait_for()
    page.locator("#mortgage-chart svg").wait_for()
    assert page.locator("#mortgage-legend").text_content().count("Today") == 1
    page.click("#schedule-wrap summary")
    first = page.locator("#schedule-table tbody tr").first
    assert "€250.00" in first.text_content() and "Paid" in first.text_content()

    page.fill("#np-date", "2021-01-10")
    page.fill("#np-amount", "10,000")
    page.select_option("#np-mode", "reduce_term")
    page.fill("#np-fee", "100")
    page.click("#np-add")
    page.locator("#prepayments-table tbody td", has_text="Reduce the term").wait_for()
    page.locator("#schedule-table tr.prepayment").wait_for()
    assert page.locator("#mortgage-chart svg .milestone-line").count() == 1
    page.keyboard.press("Escape")
    row = page.locator("#mortgages-table tbody tr", has_text="Home")
    assert "3.00%" in row.text_content() and "€421.60" in row.text_content()
    assert "2045" in row.text_content()  # 308 installments instead of 360


def test_simulate_prepayments_without_saving_them(page):
    """The simulator compares a prepayment in both ways, draws it dashed on the chart, saves nothing until asked, and
    can turn it into a real prepayment."""
    open_app(page)
    page.click("#demo-try")
    row = page.locator("#mortgages-table tbody tr", has_text="Home mortgage")
    row.click()
    page.locator("#mortgage-chart svg").wait_for()
    real = page.locator("#prepayments-table tbody tr").count()
    page.fill("#ns-amount", "10000")
    page.select_option("#ns-mode", "reduce_payment")
    page.click("#ns-add")
    page.locator("#simulation-result tbody").wait_for()
    result = page.locator("#simulation-result")
    assert [h.strip() for h in result.locator("thead th").all_text_contents()[1:]] == [
        "As entered", "All reducing the term", "All reducing the payment"]
    assert "months sooner" in result.locator("tbody tr", has_text="Ends").text_content()
    assert "instead of" in result.locator("tbody tr", has_text="Payment after them").text_content()
    assert page.locator("#mortgage-chart svg path.line.dashed").count() == 1
    assert "Outstanding (simulated)" in page.locator("#mortgage-legend").text_content()
    assert page.locator("#prepayments-table tbody tr").count() == real  # nothing saved
    # Plus an extra payment every month: one row more, and it can't be saved as real
    page.fill("#ns-amount", "200")
    page.check("#ns-monthly")
    page.click("#ns-add")
    page.locator("#simulation-table tbody tr.monthly", has_text="Every month from").wait_for()
    assert page.locator("#simulation-table tr.monthly [data-save]").count() == 0
    page.click("#simulation-table [data-remove-monthly]")
    page.locator("#simulation-table tbody tr.monthly").wait_for(state="detached")
    page.click("#simulation-table [data-save]")
    page.locator("#prepayments-table tbody tr").nth(real).wait_for()
    page.locator("#simulation").wait_for(state="hidden")
    assert page.locator("#mortgage-chart svg path.line.dashed").count() == 0


def test_dialog_buttons_and_title_stay_in_view_while_its_content_scrolls(page):
    """In a dialog taller than the screen only the content scrolls: the title and the Save button stay in view."""
    page.set_viewport_size({"width": 390, "height": 700})
    open_app(page)
    page.click("#demo-try")
    page.locator("#mortgages-table tbody tr").first.click()
    page.locator("#mortgage-chart svg").wait_for()
    page.click("#schedule-wrap summary")
    dlg = page.locator("#mortgage-dlg")
    for where in ["d.scrollHeight / 2", "d.scrollHeight"]:
        dlg.evaluate(f"d => {{ d.scrollTop = {where}; }}")
        assert dlg.evaluate("d => d.scrollTop") > 0
        for el in [page.locator("#mortgage-dlg-title"), page.locator("#mortgage-form button[type=submit]")]:
            box = el.bounding_box()
            assert box["y"] >= 0 and box["y"] + box["height"] <= 700
            # Nothing of the scrolled content is drawn on top of it
            hit = el.evaluate("e => { const r = e.getBoundingClientRect(); "
                              "return e.contains(document.elementFromPoint(r.x + 5, r.y + r.height / 2)); }")
            assert hit


def test_dialogs_close_outside_and_ask_before_discarding_changes(page):
    """A click outside a dialog closes it; with unsaved changes, the backdrop, Escape and Cancel ask first."""
    open_app(page)
    dlg = page.locator("#account-dlg")
    is_open = lambda: dlg.evaluate("d => d.open")  # noqa: E731
    page.click("#add-account-btn")
    page.mouse.click(5, 5)  # the backdrop, no changes: it just closes
    assert not is_open()

    page.click("#add-account-btn")
    # A drag from inside the box to the backdrop (e.g. selecting text) doesn't close it
    box = page.locator("#account-dlg-title").bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    page.mouse.move(5, 5)
    page.mouse.up()
    assert is_open()

    page.fill("#account-form input[name=name]", "Draft")
    page.evaluate("window.asked = []; window.confirm = (q) => { window.asked.push(q); return false; }; null")  # keep them
    page.mouse.click(5, 5)
    page.keyboard.press("Escape")
    page.click("#account-dlg .cancel")
    assert is_open()
    assert page.locator("#account-form input[name=name]").input_value() == "Draft"
    asked = page.evaluate("window.asked")
    assert len(asked) == 3 and all("unsaved changes" in q for q in asked)
    page.evaluate("window.confirm = () => true; null")  # discard them
    page.mouse.click(5, 5)
    assert not is_open()
    assert page.locator("#accounts-table tbody tr", has_text="Draft").count() == 0

    # A balance added inside the dialog is saved: closing after it doesn't ask
    page.click("#add-account-btn")
    page.fill("#account-form input[name=name]", "Savings")
    page.click("#account-form button[type=submit]")
    page.locator("#accounts-table tbody tr", has_text="Savings").click()
    page.fill("#nb-date", "2026-01-15")
    page.fill("#nb-amount", "100")
    page.click("#nb-add")
    page.locator("#balances-table tbody td.num", has_text="100.00").first.wait_for()
    page.evaluate("window.asked = []; window.confirm = (q) => { window.asked.push(q); return false; }; null")
    page.keyboard.press("Escape")
    assert not is_open() and page.evaluate("window.asked") == []


def test_dialogs_have_names_and_mark_a_wrong_field_only_after_it_was_touched(page):
    """Every dialog is announced by its title; a required field turns red only once the user has left it empty."""
    open_app(page)
    names = page.evaluate("""[...document.querySelectorAll('dialog')].map((d) => [d.id,
        document.getElementById(d.getAttribute('aria-labelledby') ?? '')?.id ?? null])""")
    assert names and all(title for _, title in names), names
    page.click("#add-account-btn")
    assert page.get_by_role("dialog", name="New account").is_visible()
    name = page.locator("#account-form input[name=name]")
    border = lambda: name.evaluate("e => getComputedStyle(e).borderColor")  # noqa: E731
    danger = page.evaluate("""(() => { const d = document.createElement('div'); d.style.color = 'var(--danger)';
        document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c; })()""")
    assert border() != danger  # empty but not touched yet
    name.fill("x")
    name.fill("")
    name.blur()
    assert border() == danger
    name.fill("Savings")
    assert border() != danger


def test_the_page_uses_the_whole_width_with_two_columns_that_can_be_turned_off(page):
    """Wide screens: the chart beside the accounts from 1600px; the table fits its panel; ▥ keeps one column."""
    open_app(page)
    page.click("#demo-try")
    page.locator("#accounts-table tbody tr").first.wait_for()
    table, button = page.locator("#accounts-table").locator("xpath=.."), page.locator("#columns-btn")
    type_column = page.locator("#accounts-table thead th").nth(1)

    def beside():  # both measured at once: the totals above can change height meanwhile (exchange rate refresh)
        return page.evaluate("""() => {
            const c = document.querySelector("#chart-panel").getBoundingClientRect();
            const a = document.querySelector("#accounts-table").closest("section").getBoundingClientRect();
            const m = document.querySelector("#mortgages-panel").getBoundingClientRect();
            // the mortgages right below the accounts, not down where the chart ends
            return a.left > c.right && Math.abs(a.top - c.top) < 2 && Math.abs(m.left - a.left) < 2
              && m.top - a.bottom < 40; }""")

    page.set_viewport_size({"width": 700, "height": 1000})  # the table follows its panel's width, not the screen's
    assert not type_column.is_visible() and table.evaluate("e => e.scrollWidth <= e.clientWidth")
    page.set_viewport_size({"width": 1280, "height": 1000})
    assert not button.is_visible() and not beside() and type_column.is_visible()
    for width in [1600, 1920, 2560]:
        page.set_viewport_size({"width": width, "height": 1000})
        assert button.is_visible() and beside(), width
        assert table.evaluate("e => e.scrollWidth <= e.clientWidth"), width  # the whole table fits, nothing cut
        assert type_column.is_visible() == (width >= 2400), width  # hidden while the accounts panel is narrow
    assert page.locator("main").bounding_box()["width"] <= 2400

    button.click()  # one column, remembered after a reload
    assert not beside() and button.get_attribute("aria-pressed") == "false" and type_column.is_visible()
    page.reload()
    page.locator("#accounts-table tbody tr").first.wait_for()
    assert not beside() and button.get_attribute("aria-pressed") == "false"
    page.click("#columns-btn")
    assert beside()


def test_a_slow_converted_chart_says_it_is_getting_the_rates(page):
    """While "All in EUR" waits for the exchange rates, a notice says so; it goes when the chart is drawn."""
    for name, currency in (("Euros", "EUR"), ("Dollars", "USD")):
        a = page.request.post("/api/accounts", data={"name": name, "currency": currency}).json()
        page.request.post(f"/api/accounts/{a['id']}/balances", data={"date": "2026-01-01", "amount": 5000000})
    # The rates take a while: the converted chart's answer is held back
    page.add_init_script("""
      const fetch0 = window.fetch;
      window.fetch = async (url, ...rest) => {
        if (String(url).includes("/api/history/converted")) await new Promise((r) => setTimeout(r, 1500));
        return fetch0(url, ...rest);
      };""")
    open_app(page)
    page.locator("#chart-currency:not([hidden])").wait_for()
    assert page.locator("#chart-loading").is_hidden()
    page.select_option("#chart-currency", "=")  # All in EUR
    page.locator("#chart-loading").wait_for()
    assert "exchange rates" in page.locator("#chart-loading").text_content()
    page.locator("#chart-loading").wait_for(state="hidden")
    page.locator("#chart svg").first.wait_for()


@pytest.mark.tour
def test_guided_tour_on_first_visit_then_from_its_button(page):
    """The tour starts on its own the first time, skips what is not shown; once closed it does not come back, and ?
    starts it again."""
    open_app(page)
    popover = page.locator(".driver-popover")
    popover.wait_for()
    assert popover.locator(".driver-popover-title").text_content() == "Welcome"
    page.click(".driver-popover-next-btn")
    page.locator(".driver-popover-title", has_text="Chart").wait_for()  # no totals yet: that step is skipped
    page.keyboard.press("Escape")
    popover.wait_for(state="detached")
    page.wait_for_function("fetch('/api/tour').then((r) => r.json()).then((d) => d.seen > 0)")
    page.reload()
    page.wait_for_selector("#version:not(:empty)")
    page.locator("#empty").wait_for()
    page.wait_for_timeout(300)
    assert popover.count() == 0  # seen: not again on its own
    page.click("#tour-btn")
    popover.wait_for()
    assert popover.locator(".driver-popover-title").text_content() == "Welcome"


@pytest.mark.tour
def test_guided_tour_follows_the_user_not_the_browser(page, browser, server):
    """Seen in one browser, the tour does not start in another one (it is kept in the database)."""
    open_app(page)
    page.locator(".driver-popover").wait_for()
    page.click(".driver-popover-close-btn")
    page.wait_for_function("fetch('/api/tour').then((r) => r.json()).then((d) => d.seen > 0)")
    other = browser.new_context(base_url=server)
    tab = other.new_page()
    tab.goto("/")
    tab.wait_for_selector("#version:not(:empty)")
    tab.locator("#empty").wait_for()
    tab.wait_for_timeout(300)
    assert tab.locator(".driver-popover").count() == 0
    other.close()


@pytest.mark.tour
def test_guided_tour_in_spanish_and_in_the_demo(page):
    """The demo starts the tour with its sample data, in the page's language, and goes through every step."""
    page.context.add_init_script("localStorage.setItem('abt-lang', 'es')")
    open_app(page)
    page.locator(".driver-popover").wait_for()
    page.keyboard.press("Escape")
    page.click("#demo-try")
    page.locator("#demo-banner").wait_for()
    title = page.locator(".driver-popover-title")
    title.wait_for()
    assert title.text_content() == "Bienvenido"
    while page.locator(".driver-popover-next-btn", has_text="Siguiente").count():
        page.click(".driver-popover-next-btn")
    assert title.text_content() == "¿La quieres otra vez?"
    page.click(".driver-popover-next-btn")  # Hecho
    page.locator(".driver-popover").wait_for(state="detached")


def test_an_account_with_contributions_starts_at_its_first_contribution(page):
    """The start of an account with contributions is its first one, also when its first balance is later."""
    euro = 10000
    fund = page.request.post("/api/accounts", data={"name": "Fund", "type": "investment"}).json()
    bank = page.request.post("/api/accounts", data={"name": "Bank", "type": "bank"}).json()
    page.request.post(f"/api/accounts/{bank['id']}/balances", data={"date": "2025-06-01", "amount": 50 * euro})
    page.request.post(f"/api/accounts/{fund['id']}/contributions", data={"date": "2026-01-01", "amount": 1000 * euro})
    for day, amount in (("2026-03-01", 1300), ("2026-04-01", 1200)):
        page.request.post(f"/api/accounts/{fund['id']}/balances", data={"date": day, "amount": amount * euro})
    open_app(page)
    mini = page.locator("#multiples .mini", has_text="Fund")
    mini.locator(".trend-bg").wait_for()
    # The whole gain (1,200 - 1,000), not only the change since the first balance (-100)
    assert "+€200.00 (+20%)" in mini.locator(".trend-text").text_content(), mini.locator(".trend-text").text_content()
    row = page.locator("#accounts-table tbody tr", has_text="Fund")
    assert "+€200.00" in row.locator(".trend-text").text_content()
    # Its chart starts on its first contribution, not on the bank account's first balance
    assert mini.locator("svg text.axis:not([dominant-baseline])").first.text_content() == "Jan 26"
    mini.locator(".mini-max").click()
    page.wait_for_selector("#chart-dlg[open] #chart-dlg-chart svg")
    assert page.locator("#chart-dlg-chart svg text.axis:not([dominant-baseline])").first.text_content() == "Jan 26"
    assert "first contribution" in page.locator("#chart-dlg-legend").text_content()
    page.keyboard.press("Escape")
    # Its window's balances count from the first contribution too
    row.click()
    page.locator("#balances-table tbody tr").first.wait_for()
    assert "+€200.00" in page.locator("#balances-table tbody tr").first.text_content()
    assert "+€300.00" in page.locator("#balances-table tbody tr").nth(1).text_content()
    page.keyboard.press("Escape")
    page.click("#settings-btn")
    page.select_option("#trend-select", "value")
    page.click("#settings-form button[type=submit]")
    mini.locator(".trend-bg:not(.trend-gain)").wait_for()
    assert "+€200.00" in mini.locator(".trend-text").text_content()  # the value from the same start
    assert mini.locator(".start-label").text_content() == "Start: €1k"  # the first contribution
