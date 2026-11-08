# Accounts Balance Tracker

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/dodert)

Self-hosted app to keep track of the balance of your accounts over time: current accounts, savings, investments, pension plans, crypto, cash and loans. Record each account's balance whenever you want (for example once a month) and see the total and how it evolves. Your data stays on your server.

> **Note:** this project has been almost entirely vibe-coded with an AI coding assistant ([Claude Code](https://claude.com/claude-code)).

## Contents

- [Screenshots](#screenshots)
- [Install on Unraid](#install-on-unraid)
  - [From Apps](#from-apps-community-applications) · [By hand](#by-hand-with-the-template)
- [Install with Docker on your computer](#install-with-docker-on-your-computer)
  - [Windows](#windows) · [macOS](#macos) · [Linux](#linux)
  - [Update, stop and uninstall](#update-stop-and-uninstall)
- [First steps](#first-steps)
- [Settings](#settings)
- [Backups and updates](#backups-and-updates)
- [Features](#features)
- [Development](#development)
- [API](#api)
- [License](#license)

## Screenshots

![Overview: totals, the total chart with a small chart per account, and the accounts](docs/screenshot.png)

<p>
  <img src="docs/screenshot-dark.png" alt="Dark mode: every currency converted into EUR, grouped by institution, with milestones" width="58%">
  <img src="docs/screenshot-mobile.png" alt="On a phone" width="20%">
  <img src="docs/screenshot-update.png" alt="Update balances, grouped by institution" width="20%">
</p>

<sub>Screenshots from the demo mode: made-up sample data.</sub>

## Install on Unraid

### From Apps (Community Applications)

1. Go to the **Apps** tab and search for **Accounts Balance Tracker**.
2. Click it and then **Install**.
3. Fill in **Login user** and **Login password** (recommended: the app holds financial data) and click **Apply**.
4. Open `http://<your-unraid-ip>:8000`, or click the app's icon in the Docker tab → **WebUI**.

Your data is kept in `/mnt/user/appdata/accounts-balance-tracker`. To update, use **Check for updates** in the Docker tab.

### By hand, with the template

If it does not show up in Apps (Community Applications refreshes its list every few hours, so a new version or app can take a while):

1. Open the Unraid **Terminal** (the `>_` icon at the top right) and paste this line. It downloads the template:
   ```bash
   mkdir -p /boot/config/plugins/dockerMan/templates-user && curl -fsSL https://raw.githubusercontent.com/dodert/unraid-templates/main/accounts-balance-tracker.xml -o /boot/config/plugins/dockerMan/templates-user/my-accounts-balance-tracker.xml
   ```
2. Go to **Docker → Add Container** and pick **accounts-balance-tracker** in the *Template* list.
3. Continue from step 3 above. To get a newer version of the template itself, run the line of step 1 again.

<details>
<summary>Prefer to see it under <b>Apps → Private</b>?</summary>

Run this in the terminal instead of step 1, then install it from **Apps → Private**:

```bash
mkdir -p /boot/config/plugins/community.applications/private/dodert && curl -fsSL https://raw.githubusercontent.com/dodert/unraid-templates/main/accounts-balance-tracker.xml -o /boot/config/plugins/community.applications/private/dodert/accounts-balance-tracker.xml
```
</details>

## Install with Docker on your computer

You only need Docker. The steps are the same on every system: make a folder, download [`docker-compose.yml`](docker-compose.yml) into it, and start it. Your data is saved in a `data` folder next to that file.

### Windows

1. Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) and open it (it may ask to install or update WSL; accept and restart if it asks). Wait until it says *Engine running*.
2. Open **PowerShell** (Start menu → type *PowerShell*) and run, one line at a time:
   ```powershell
   mkdir $HOME\accounts-balance-tracker
   cd $HOME\accounts-balance-tracker
   curl.exe -fsSL -o docker-compose.yml https://raw.githubusercontent.com/dodert/accounts-balance-tracker/main/docker-compose.yml
   notepad docker-compose.yml
   ```
3. In Notepad, write a user and a password after `AUTH_USERNAME=` and `AUTH_PASSWORD=`, change `TZ` to your time zone if you want, save and close.
4. Back in PowerShell:
   ```powershell
   docker compose up -d
   ```
5. Open <http://localhost:8000> in your browser.

The data is in `C:\Users\<you>\accounts-balance-tracker\data`.

### macOS

1. Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) (pick *Apple silicon* or *Intel*: you can see which one in the Apple menu → *About This Mac*) and open it. Wait until it says *Engine running*.
2. Open **Terminal** (Spotlight → type *Terminal*) and run:
   ```bash
   mkdir -p ~/accounts-balance-tracker && cd ~/accounts-balance-tracker
   curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/dodert/accounts-balance-tracker/main/docker-compose.yml
   open -e docker-compose.yml
   ```
3. In TextEdit, write a user and a password after `AUTH_USERNAME=` and `AUTH_PASSWORD=`, change `TZ` if you want, save and close.
4. Back in Terminal:
   ```bash
   docker compose up -d
   ```
5. Open <http://localhost:8000> in your browser.

The data is in `~/accounts-balance-tracker/data`.

### Linux

1. Install Docker Engine with the Compose plugin: follow the [guide for your distribution](https://docs.docker.com/engine/install/) (Ubuntu, Debian, Fedora…). Check it with `docker compose version`.
2. In a terminal:
   ```bash
   mkdir -p ~/accounts-balance-tracker && cd ~/accounts-balance-tracker
   curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/dodert/accounts-balance-tracker/main/docker-compose.yml
   sed -i "s/PUID=1000/PUID=$(id -u)/; s/PGID=1000/PGID=$(id -g)/" docker-compose.yml
   nano docker-compose.yml
   ```
   The `sed` line makes the data folder belong to your user. In the editor, set `AUTH_USERNAME` and `AUTH_PASSWORD` (and `TZ` if you want), then save with `Ctrl+O`, `Enter`, and leave with `Ctrl+X`.
3. Start it:
   ```bash
   docker compose up -d
   ```
   If you get *permission denied*, put `sudo` in front, or add yourself to the `docker` group ([post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/)).
4. Open <http://localhost:8000>, or `http://<that-machine's-ip>:8000` from another device.

The data is in `~/accounts-balance-tracker/data`.

<details>
<summary>Without Compose: a single <code>docker run</code></summary>

```bash
docker run -d --name accounts-balance-tracker --restart unless-stopped -p 8000:8000 \
  -v "$PWD/data:/data" -e PUID=$(id -u) -e PGID=$(id -g) \
  -e AUTH_USERNAME=me -e AUTH_PASSWORD=change-me \
  ghcr.io/dodert/accounts-balance-tracker:latest
```
</details>

### Update, stop and uninstall

Run these in the folder with `docker-compose.yml` (PowerShell on Windows, Terminal on macOS and Linux):

| | |
|---|---|
| Update to the latest version | `docker compose pull` and then `docker compose up -d` |
| Stop it | `docker compose down` |
| Start it again | `docker compose up -d` |
| See its logs | `docker compose logs -f` (`Ctrl+C` to leave) |
| Uninstall | `docker compose down`, then delete the folder (**this deletes your data**: export a CSV first) |

To use another port, change the left number in `"8000:8000"` (e.g. `"8080:8000"`) and open that port instead.

## First steps

1. Click **Try it with sample data** to look around with made-up data, or **+ Account** to create your first account (name, institution, type and currency).
2. Click **Update balances** whenever you want (e.g. once a month) and write the balance of each account on that date.
3. The totals and charts fill in as you add balances. Already have them in a spreadsheet? Use **Import / export → Import CSV** (see [Features](#features)).

## Settings

Set them in `docker-compose.yml` (`environment:`) or in the Unraid template.

| Variable | Default | |
|---|---|---|
| `DEFAULT_CURRENCY` | `EUR` | Currency for new accounts and CSV rows without one; totals in several currencies are added up in it |
| `FX_URL` | `https://api.frankfurter.app` | Exchange-rate service (Frankfurter API or a self-hosted copy of it) |
| `DATABASE_URL` | empty | Empty = SQLite in `/data`. PostgreSQL: `postgresql+psycopg://user:pass@host:5432/balances` |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | Set both to ask for a login; changing the password logs everyone out |
| `AUTH_TOTP` | `false` | `true` also asks for a one-time code; the first login shows the QR code. Delete `.totp_secret` in `/data` to show a new one |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7` | Addresses (or CIDR ranges) of the reverse proxies whose `X-Forwarded-For` / `X-Forwarded-Proto` headers are trusted. The default trusts the local network and Docker networks, so a proxy or `cloudflared` there works as is, and a client coming straight from the internet can't fake its IP. To be stricter, set only your proxy's IP |
| `PUID`, `PGID`, `TZ`, `PORT` | `99`, `100`, `Etc/UTC`, `8000` | |

The app holds financial data: turn the login on, and expose it only on your LAN or behind a reverse proxy with HTTPS.

**Wrong logins**: after 10 wrong passwords, or 10 wrong one-time codes, from one IP in 15 minutes, that IP has to wait (`429`) until the oldest one is 15 minutes old; other IPs can still log in. A right password clears the count of its IP. Behind a reverse proxy the limit uses the real client IP only if the proxy's `X-Forwarded-For` is trusted; otherwise every visitor shares the proxy's IP and its limit. See also [SECURITY.md](SECURITY.md).

## Backups and updates

The database schema is migrated automatically on start, keeping all the data.

**Backups before updates**: when an update changes the database schema, the SQLite database is first copied to `/data/balances.db.backup-<from>-to-<to>-<date>` (if the copy fails, the app does not start and nothing is changed). To go back: stop the container, run the previous image version, and replace `balances.db` with the backup (delete `balances.db-wal` and `balances.db-shm` if there are any). Delete old backups yourself when you no longer need them. With PostgreSQL the app cannot copy the database: run `pg_dump` before updating. Exporting a CSV now and then (**Import / export → Export CSV**) is also a good copy of everything.

**File permissions**: the SQLite database and its backups are only readable by the container's user (`0600`, `PUID`), whatever `UMASK` is; databases created by older versions are fixed on start.

## Features

- **Accounts** with a type, institution, currency and notes, and optionally the date their balance becomes **available** (e.g. unvested stock options: one account per vesting tranche). Until then they are flagged; the totals show two figures, the amount available now (main colour) and the total including what is not available yet (secondary colour), and the total chart also draws the available part. An institution can have several accounts, and the same name can be used in different institutions (e.g. "Payroll" at two banks). Archive the ones you close (record a 0 balance first so the history stays right).
- **Search** in the accounts list (account, institution or type, ignoring case and accents): the accounts that do not match are dimmed, not hidden, and the charts do not change.
- **Balances**: one per account and day; recording another on the same day replaces it. **Update balances** asks for every active account on one date at once, prefilled with the last balance. Loans and credit cards are recorded as negative balances.
- **Milestones**: notable expenses of an account (a car, a trip…) with a date, an amount and a description, added with the **+ Milestone** button at the top or from the account's window. They show on the charts as a dotted vertical line on their date, in the account's colour; hovering the line shows its description, amount, account and institution.
- **Totals** per currency, with the change since the previous recorded date and since the first one (amount and %). Accounts and each account's balance history show the same two changes.
- **Several currencies added up**: with accounts in more than one currency, a first box shows everything in `DEFAULT_CURRENCY` at today's exchange rates, with the rate used and a button to refresh it. Rates are the European Central Bank's, from the free [Frankfurter API](https://frankfurter.dev) (published once per working day, around 16:00 CET). Today's rate is fetched the first time it is needed each day and stored, so later visits do not call the service; past days are stored for good. **Only the two currencies and the date are sent; amounts never leave the app**: the conversion is done locally. If the service cannot be reached, the last stored rate is used and the box says so.
- **Charts**, for all time, 5 years, 1 year, 6 or 3 months, with a hover tooltip and a table view:
  - *Amounts*: the total, then one small chart per account with its own scale. Each one has a dashed line at its first recorded balance (its start) and shows the change since then. The ⤢ button on an account opens its chart maximized (close it with Close, Esc or a click outside).
  - *% since the start*: every account and the total on one chart, as the % change since each one's own start, so they can be compared.

  *Group by*: by account (each one shown with its institution) or by institution (one series per institution, the sum of its accounts; its start is its total on the first date any of them had a balance). The choice is saved in the browser.

  *Currency*: with accounts in more than one currency, *All currencies* (the default) shows the charts of each currency one after the other, each in its own currency (no conversion); or pick one currency. *All in EUR* (the default currency) converts every account into it, each date at that date's ECB rate, and draws them on one chart; the rates are stored, so each date is asked for once, and only currencies and dates are sent, never amounts.

  Each account keeps its colour in every chart (16 colours; past 16 they repeat) and the total is drawn in black (white in dark mode). An account keeps its last recorded balance until a new one is recorded.
- **CSV import / export**: one row per account and date. Columns `account`, `date`, `amount` (required) and `type`, `currency`, `institution`, `note`, `available_from` (optional; `available_from` and `type` are only used when an account is created); Spanish names also work (`cuenta`, `fecha`, `saldo`, `tipo`, `moneda`, `entidad`, `nota`, `disponible_desde`). Comma, semicolon or tab separated; UTF-8 or Windows-1252; amounts as `1234.56`, `1.234,56` or `1,234.56`; dates `YYYY-MM-DD` or `DD/MM/YYYY`. An account is found by its name and institution (without an institution in the row, by its name when only one account has it). Missing accounts are created, and a balance already recorded on that date is replaced.
- **Demo mode** (**⚙ → Demo mode**, the *Try it with sample data* button when there are no accounts yet, or the link `/demo`): the app with made-up sample data (several institutions and accounts, EUR and USD, stock options not available yet, a mortgage, a closed account, two years of history), to show it to other people or to see how it works. The sample data lives in its own temporary database, apart from the real one, which is not read or changed while a browser is in demo mode; a banner shows it, with buttons to reset the sample data and to leave the demo. It is per browser (a cookie) and needs the login like the rest of the app.
- **Optional login** with a user and password, and optionally a one-time code from an authenticator app.
- English and Spanish interface (**⚙ → Language**), and a separate **date and number format** (Spain, UK, US, Germany...) that dates, amounts and the date boxes follow; by default it goes with the language. Both are saved in the browser. To add a language, copy `app/static/i18n/en.json` to `<code>.json` and translate it.

## Development

```bash
pip install -r requirements-dev.txt
DATA_DIR=./data uvicorn app.main:create_app --factory --reload
pytest -q
```

**Image**: `ghcr.io/dodert/accounts-balance-tracker` (linux/amd64 and linux/arm64), built by GitHub Actions on every push to `main` and on `v*` git tags. Versions follow [SemVer](https://semver.org): `main` builds are the release version in `app/__init__.py` (`__version__`), so bump it in every PR (`Z` for fixes, `Y` for features). A push to `main` is tagged `latest`, `X.Y.Z`, `X.Y` and `sha-<commit>`.

**Trying a branch** before merging it: **Actions → CI → Run workflow**, pick the branch. The image is pushed as `ghcr.io/dodert/accounts-balance-tracker:<branch>`, `pre-release` and `X.Y.Z-<branch>.<run>`.

**Old images**: **Actions → Clean up old images** and **Actions → Delete untagged images** delete old image versions (dry run by default). They need the repository to have the Admin role in the package's *Manage Actions access*.

**Unraid template**: the `Sync Unraid template` workflow publishes `unraid/accounts-balance-tracker.xml` and its icon to the public repo [dodert/unraid-templates](https://github.com/dodert/unraid-templates). It needs the repository secret `TEMPLATES_TOKEN`: a fine-grained token with *Contents: Read and write* on `dodert/unraid-templates` only.

**Screenshots**: start the app on port 8012 and run `node scripts/screenshots.js docs` (needs Playwright; see the comment at the top of the script).

## API

Interactive docs at `/docs`. Main endpoints: `/api/accounts`, `/api/accounts/{id}/balances`, `/api/balances/bulk`, `/api/milestones` (and `/api/accounts/{id}/milestones`), `/api/summary`, `/api/summary/combined`, `/api/fx?from=USD&to=EUR&date=YYYY-MM-DD`, `POST /api/fx/refresh`, `/api/history?currency=EUR&since=YYYY-MM-DD`, `/api/history/converted?since=YYYY-MM-DD`, `/api/export/csv`, `/api/import/csv`, `/api/demo` (POST on, DELETE off, `/api/demo/reset`).

## License

[GPL-3.0](LICENSE)
