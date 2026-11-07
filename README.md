# Accounts Balance Tracker

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/dodert)

Self-hosted app to keep track of the balance of your accounts over time: current accounts, savings, investments, pension plans, crypto, cash and loans. Record each account's balance whenever you want (for example once a month) and see the total and how it evolves.

> **Note:** this project has been almost entirely vibe-coded with an AI coding assistant ([Claude Code](https://claude.com/claude-code)).

![Screenshot](docs/screenshot.png)

## Features

- **Accounts** with a type, institution, currency and notes. An institution can have several accounts, and the same name can be used in different institutions (e.g. "Payroll" at two banks). Archive the ones you close (record a 0 balance first so the history stays right).
- **Balances**: one per account and day; recording another on the same day replaces it. **Update balances** asks for every active account on one date at once, prefilled with the last balance. Loans and credit cards are recorded as negative balances.
- **Totals** per currency, with the change since the previous recorded date and since the first one (amount and %). Accounts and each account's balance history show the same two changes.
- **Charts**, for all time, 5 years, 1 year, 6 or 3 months, with a hover tooltip and a table view:
  - *Amounts*: the total, then one small chart per account with its own scale. Each one has a dashed line at its first recorded balance (its start) and shows the change since then. The ⤢ button on an account opens its chart maximized (close it with Close, Esc or a click outside).
  - *% since the start*: every account and the total on one chart, as the % change since each one's own start, so they can be compared.

  *Group by*: by account (each one shown with its institution) or by institution (one series per institution, the sum of its accounts; its start is its total on the first date any of them had a balance). The choice is saved in the browser.

  Each account keeps its colour in every chart (the first 8; the rest are grey) and the total is drawn in black (white in dark mode). An account keeps its last recorded balance until a new one is recorded.
- **CSV import / export**: one row per account and date. Columns `account`, `date`, `amount` (required) and `type`, `currency`, `institution`, `note` (optional); Spanish names also work (`cuenta`, `fecha`, `saldo`, `tipo`, `moneda`, `entidad`, `nota`). Comma, semicolon or tab separated; UTF-8 or Windows-1252; amounts as `1234.56`, `1.234,56` or `1,234.56`; dates `YYYY-MM-DD` or `DD/MM/YYYY`. An account is found by its name and institution (without an institution in the row, by its name when only one account has it). Missing accounts are created, and a balance already recorded on that date is replaced.
- **Optional login** with a user and password, and optionally a one-time code from an authenticator app.
- English and Spanish interface (**⚙ → Language**), and a separate **date and number format** (Spain, UK, US, Germany...) that dates, amounts and the date boxes follow; by default it goes with the language. Both are saved in the browser. To add a language, copy `app/static/i18n/en.json` to `<code>.json` and translate it.

## Docker

Image: `ghcr.io/dodert/accounts-balance-tracker` (linux/amd64 and linux/arm64), built by GitHub Actions on every push to `main` and on `v*` git tags. Versions follow [SemVer](https://semver.org): `main` builds are the release version in `app/__init__.py` (`__version__`), so bump it in every PR (`Z` for fixes, `Y` for features). A push to `main` is tagged `latest`, `X.Y.Z`, `X.Y` and `sha-<commit>`.

To try a branch before merging it: **Actions → CI → Run workflow**, pick the branch. The image is pushed as `ghcr.io/dodert/accounts-balance-tracker:<branch>`, `pre-release` and `X.Y.Z-<branch>.<run>`.

**Actions → Clean up old images** and **Actions → Delete untagged images** delete old image versions (dry run by default). They need the repository to have the Admin role in the package's *Manage Actions access*.

```bash
docker run -d --name accounts-balance-tracker -p 8000:8000 \
  -v /path/to/data:/data -e PUID=1000 -e PGID=1000 \
  ghcr.io/dodert/accounts-balance-tracker:latest
```

Or use [`docker-compose.yml`](docker-compose.yml). Everything lives in `/data` (SQLite database and login secrets). The container runs as `PUID`:`PGID` (default `99:100`, Unraid's `nobody:users`). The database schema is migrated automatically on start.

| Variable | Default | |
|---|---|---|
| `DEFAULT_CURRENCY` | `EUR` | Currency for new accounts and CSV rows without one |
| `DATABASE_URL` | empty | Empty = SQLite in `/data`. PostgreSQL: `postgresql+psycopg://user:pass@host:5432/balances` |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | Set both to ask for a login; changing the password logs everyone out |
| `AUTH_TOTP` | `false` | `true` also asks for a one-time code; the first login shows the QR code. Delete `.totp_secret` in `/data` to show a new one |
| `PUID`, `PGID`, `TZ`, `PORT` | `99`, `100`, `Etc/UTC`, `8000` | |

The app holds financial data: turn the login on, and expose it only on your LAN or behind a reverse proxy with HTTPS.

## Unraid

The `Sync Unraid template` workflow publishes `unraid/accounts-balance-tracker.xml` and its icon to the public repo [dodert/unraid-templates](https://github.com/dodert/unraid-templates). It needs the repository secret `TEMPLATES_TOKEN`: a fine-grained token with *Contents: Read and write* on `dodert/unraid-templates` only.

1. In the Unraid terminal:
   ```bash
   mkdir -p /boot/config/plugins/dockerMan/templates-user && curl -fsSL https://raw.githubusercontent.com/dodert/unraid-templates/main/accounts-balance-tracker.xml -o /boot/config/plugins/dockerMan/templates-user/my-accounts-balance-tracker.xml
   ```
   Run it again to update the template.
2. In **Docker → Add Container**, pick `accounts-balance-tracker` from the template list.
3. Set a login user and password and click **Apply**. Data goes to `/mnt/user/appdata/accounts-balance-tracker`.

To list it in **Apps → Private** (Community Applications) instead:

```bash
mkdir -p /boot/config/plugins/community.applications/private/dodert && curl -fsSL https://raw.githubusercontent.com/dodert/unraid-templates/main/accounts-balance-tracker.xml -o /boot/config/plugins/community.applications/private/dodert/accounts-balance-tracker.xml
```

## Run locally

```bash
pip install -r requirements-dev.txt
DATA_DIR=./data uvicorn app.main:create_app --factory --reload
pytest -q
```

## API

Interactive docs at `/docs`. Main endpoints: `/api/accounts`, `/api/accounts/{id}/balances`, `/api/balances/bulk`, `/api/summary`, `/api/history?currency=EUR&since=YYYY-MM-DD`, `/api/export/csv`, `/api/import/csv`.

## License

[GPL-3.0](LICENSE)
