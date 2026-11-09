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
- [Several users](#several-users)
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
4. Open `http://<your-unraid-ip>:8085`, or click the app's icon in the Docker tab → **WebUI**.

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
3. In Notepad, write a user and a password after `AUTH_USERNAME=` and `AUTH_PASSWORD=`, save and close.
4. Back in PowerShell:
   ```powershell
   docker compose up -d
   ```
5. Open <http://localhost:8085> in your browser.

The data is in `C:\Users\<you>\accounts-balance-tracker\data`.

### macOS

1. Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) (pick *Apple silicon* or *Intel*: you can see which one in the Apple menu → *About This Mac*) and open it. Wait until it says *Engine running*.
2. Open **Terminal** (Spotlight → type *Terminal*) and run:
   ```bash
   mkdir -p ~/accounts-balance-tracker && cd ~/accounts-balance-tracker
   curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/dodert/accounts-balance-tracker/main/docker-compose.yml
   open -e docker-compose.yml
   ```
3. In TextEdit, write a user and a password after `AUTH_USERNAME=` and `AUTH_PASSWORD=`, save and close.
4. Back in Terminal:
   ```bash
   docker compose up -d
   ```
5. Open <http://localhost:8085> in your browser.

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
   The `sed` line makes the data folder belong to your user. In the editor, set `AUTH_USERNAME` and `AUTH_PASSWORD`, then save with `Ctrl+O`, `Enter`, and leave with `Ctrl+X`.
3. Start it:
   ```bash
   docker compose up -d
   ```
   If you get *permission denied*, put `sudo` in front, or add yourself to the `docker` group ([post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/)).
4. Open <http://localhost:8085>, or `http://<that-machine's-ip>:8085` from another device.

The data is in `~/accounts-balance-tracker/data`.

<details>
<summary>Without Compose: a single <code>docker run</code></summary>

```bash
docker run -d --name accounts-balance-tracker --restart unless-stopped -p 8085:80 \
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
| Uninstall | `docker compose down`, then delete the folder (**this deletes your data**: export it first with **Import / export → Export everything (JSON)**) |

To use another port, change the left number in `"8085:80"` (e.g. `"8080:80"`) and open that port instead. The app listens on port 80 inside the container (`PORT`); with `network_mode: host` set `PORT` to a free port (e.g. `8085`), since the host's port 80 is usually taken.

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
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | Set both to ask for a login; changing the password logs everyone out |
| `AUTH_TOTP` | `false` | `true` also asks for a one-time code; the first login shows the QR code. Delete `.totp_secret` in `/data` to show a new one |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7` | Addresses (or CIDR ranges) of the reverse proxies whose `X-Forwarded-For` / `X-Forwarded-Proto` headers are trusted. The default trusts the local network and Docker networks, so a proxy or `cloudflared` there works as is, and a client coming straight from the internet can't fake its IP. To be stricter, set only your proxy's IP |
| `MULTI_USER` | `false` | `true` = several users, each with their own encrypted database, registered by invitation. `AUTH_USERNAME` / `AUTH_PASSWORD` are then the admin's login (the one-time code is always asked). See [Several users](#several-users) |
| `COUNTRY_DB` | `true` | Multi-user only: download DB-IP's free country database once a month, to show the country of each user's last login |
| `ACCOUNT_DAYS` | `30` | Multi-user only: days a new account lasts. When it expires, its data is deleted and the user is blocked; the admin can extend it or remove the expiry. `0` = no expiry |
| `MAX_USERS` | `100` | Multi-user only: most users that can sign up (the admin is not counted) |
| `MAX_DB_MB` | `10` | Multi-user only: most size of each user's database, in MB; a change that would go over it is refused (`413`) |
| `BACKUP_KEEP` | `7` | Daily backups in `/data/backups/<date>/` ([Backups and updates](#backups-and-updates)): how many days are kept; `0` = no daily backups |
| `FX_DAILY_LIMIT` | `30` | Multi-user only: most requests a day to the exchange-rate service, for all users together (then the stored rates are used) |
| `API_DOCS` | `false` | `true` = interactive API docs at `/docs` and `/redoc`, and the schema at `/openapi.json` (behind the login). Off, they answer `404` |
| `PUID`, `PGID`, `PORT` | `99`, `100`, `80` | `PORT` is the port inside the container; change it only with host networking |

The app holds financial data: turn the login on, and expose it only on your LAN or behind a reverse proxy with HTTPS.

**Wrong logins**: after 10 wrong passwords, or 10 wrong one-time codes, from one IP in 15 minutes, that IP has to wait (`429`) until the oldest one is 15 minutes old; other IPs can still log in. A right password clears the count of its IP. IPv6 addresses count per /64 (one connection usually has the whole /64). Each user name also allows at most 30 wrong passwords in 15 minutes from all IPs together, so guessing from many addresses doesn't help either. Behind a reverse proxy the limit uses the real client IP only if the proxy's `X-Forwarded-For` is trusted; otherwise every visitor shares the proxy's IP and its limit. See also [SECURITY.md](SECURITY.md).

**One-time codes** are accepted once: the step of the last code used is kept in `.totp_secret`, so the same code can't be used again, also after a restart.

**One process**: the image runs a single uvicorn process (`--workers 1`). The login limits, the demos and the per-user locks live in its memory, so don't run it with more workers or several replicas behind a load balancer: each would have its own limits.

## Several users

With `MULTI_USER=true`, one instance serves several people, each with their own data. It is meant for sharing the app with friends or family, e.g. through a Cloudflare Tunnel.

- **The admin** logs in with `AUTH_USERNAME` / `AUTH_PASSWORD` and the one-time code (always asked, set up on the first login). It goes to `/admin`, has no data of its own and can't read anyone's.
- **Invitations**: the admin creates a link there (single use, valid for 7 days, optionally only for one email; a new one for the same email replaces the open one) and sends it. Opening a link in a browser where someone is already logged in says so, and signing up logs them out there. The person chooses a user name, email and password (at least 10 characters).
- **Encrypted data**: each user has their own SQLite database in `/data/users/<id>.db.enc`, encrypted (AES-256-GCM) with a random key. That key is stored only encrypted with the user's password (Argon2id) and with a **recovery key** shown once at sign-up. Nobody without one of the two can read the data, the admin included. While the user is logged in the key travels in their session cookie, encrypted with the server's secret, and the database is decrypted in memory for each request.
- **Passwords**: users change theirs in **👤 Account** (their other devices are logged out, and the database is encrypted again with a new random key, so a key that got out, e.g. in a stolen cookie, stops working; the recovery key keeps working), and reset a forgotten one with the recovery key at **Forgot your password?** on the login page. **Without the password and the recovery key the data is lost**: there is no other way to open it.
- **One-time code** (optional, per user): in **👤 Account** a user can turn on a code from an authenticator app (Google Authenticator, Aegis, 1Password...): scan the QR code, enter its first code and the password. Then each login asks for the code after the password; each code works once, and wrong codes count towards the same limits as the admin's. Turning it off asks for the password. A user who loses the app can still log in with their recovery key (**Forgot your password?**) and turn it off, or the admin can **remove** it in `/admin`. The secret is kept in `users.db`, encrypted with a key derived from `/data/.session_secret`.
- `/data/users.db` holds the users, invitations and logged-out sessions (no balances, no account names).
- **Admin panel** (`/admin`): the users with their email, sign-up date, last login (IP and country), size of their encrypted database and status; **remove the one-time code** of a user who lost their app; **block** / unblock (a blocked user is logged out at once and keeps their data), **delete** (the user and their data, for good) or **delete and block the email**; the open invitations; and the **blocked emails**, which can't sign up even with an invitation.
- **Country of the last login**: behind Cloudflare, from its `CF-IPCountry` header (turn on *Add visitor location headers* in Cloudflare's Managed Transforms); otherwise from the free [DB-IP IP to Country Lite](https://db-ip.com/db/download/ip-to-country-lite) database (CC BY 4.0), downloaded once a month to `/data` (`COUNTRY_DB=false` turns the download off). The IP is never sent to another service.
- **Expiry**: a new account lasts `ACCOUNT_DAYS` days (30 by default); the app warns the user in the last 3 days. Once expired, the user is blocked and their data deleted (checked every hour). In `/admin` the admin can **extend** an account by some days (an expired one is unblocked and starts again empty) or set it to **no expiry**.
- **Limits**: at most `MAX_USERS` users (100) and `MAX_DB_MB` MB (10) per user's database.
- **Exchange rates**: a rate for a pair of currencies on a day is the same for everyone, so it is asked once for all users and kept in `/data/rates.db` (only currencies, dates and rates). The user's data is not kept open while the service is asked. At most `FX_DAILY_LIMIT` requests a day (30) go to the service; today's rate is asked again when it is more than 3 hours old, until the day's own rate is published.
- **Demo**: anyone can try the demo from the login page (**Try the demo**) or `/demo`, without an account. Each visitor gets their own copy of the sample data, kept in memory and dropped after an hour without use (at most 200 at once).
- What it does not protect: while a user is logged in, the server has their data in memory to calculate totals and charts, so whoever runs the server could change it to read it. It protects the files on disk and their backups.
- **Backups**: copy the whole `/data` folder; the users' files stay encrypted in it. Before a schema change, each user's file is copied next to it (`<id>.db.enc.backup-<from>-to-<to>-<date>`) the first time they use the new version. `users.db` is migrated on start the same way, after a copy to `users.db.backup-<from>-to-<to>-<date>`.

## Backups and updates

The database schema is migrated automatically on start, keeping all the data.

**Daily backups**: once a day (by the UTC date: on start and then checked every hour) the app copies its data to `/data/backups/<YYYY-MM-DD>/`, and keeps the last `BACKUP_KEEP` days (7). With one user it is `balances.db`; with several users, `users.db` and `users/<id>.db.enc`, still encrypted. A user who is deleted, or whose account expires, is also deleted from every daily backup. The settings (⚙) list the backups made, with when and their size (with several users, only the days with a copy of your own data). To restore a day:

1. Stop the container.
2. Copy that day's files over the ones in `/data`, all of them together. With several users, a day's `users.db` and `.db.enc` files go together, because the keys of each user's data are in `users.db`.
3. Delete `balances.db-wal` and `balances.db-shm` (or `users.db-wal` and `users.db-shm`) if there are any.
4. Start the container again.

These backups sit in the same folder as the data. Keep a copy somewhere else too, for example with Unraid's *Appdata Backup* plugin or by copying `/data/backups`.

**Updating from 0.21 or older**: the app now listens on port **80** inside the container (it was 8000). After updating, change the container side of the port mapping, or the web page will not open: in Unraid, edit the container and set the *WebUI port*'s **Container Port** to `80` (the host port can stay as it was); with Compose, change `"8000:8000"` to `"8000:80"` (or `"8085:80"`); with `docker run`, `-p 8000:80`.

**Backups before updates**: when an update changes the database schema, the SQLite database is first copied to `/data/balances.db.backup-<from>-to-<to>-<date>` (if the copy fails, the app does not start and nothing is changed). To go back: stop the container, run the previous image version, and replace `balances.db` with the backup (delete `balances.db-wal` and `balances.db-shm` if there are any). Delete old backups yourself when you no longer need them. Exporting now and then with **Import / export → Export everything (JSON)** is also a good copy of everything (accounts with all their details, balances and milestones); the CSV export only has the balances.

**File permissions**: the SQLite database and its backups are only readable by the container's user (`0600`, `PUID`), whatever `UMASK` is; databases created by older versions are fixed on start.

## Features

- **Accounts** with a type, institution, currency and notes, and optionally the date their balance becomes **available** (e.g. unvested stock options: one account per vesting tranche). Until then they are flagged; the totals show two figures, the amount available now (main colour) and the total including what is not available yet (secondary colour), and the total chart also draws the available part. An institution can have several accounts, and the same name can be used in different institutions (e.g. "Payroll" at two banks). Archive the ones you close (record a 0 balance first so the history stays right).
- **Search** in the accounts list (account, institution or type, ignoring case and accents): the accounts that do not match are dimmed, not hidden, and the charts do not change.
- **Balances**: one per account and day; recording another on the same day replaces it. **Update balances** asks for every active account on one date at once, prefilled with the last balance. Loans and credit cards are recorded as negative balances.
- **Milestones**: notable expenses of an account (a car, a trip…) with a date, an amount and a description, added with the **+ Milestone** button at the top or from the account's window. They show on the charts as a dotted vertical line on their date, in the account's colour; hovering the line shows its description, amount, account and institution.
- **Totals** per currency, with the change since the previous recorded date and since the first one (amount and %). Accounts and each account's balance history show the same two changes.
- **Several currencies added up**: with accounts in more than one currency, a first box shows everything in `DEFAULT_CURRENCY` at today's exchange rates, with the rate used and a button to refresh it. Rates are the European Central Bank's, from the free [Frankfurter API](https://frankfurter.dev) (published once per working day, around 16:00 CET). Today's rate is fetched the first time it is needed each day and stored, and fetched again on a visit when it is more than 3 hours old (until the ECB has published the day's own rate), without pressing the refresh button; past days are stored for good. The chart **All in `DEFAULT_CURRENCY`** also uses today's rate on every date: it shows what the balances are worth now, without the changes of the exchange rate. Each rate used is written to the log, saying whether it was asked to the service or taken from the stored ones. **Only the two currencies and the date are sent; amounts never leave the app**: the conversion is done locally. If the service cannot be reached, the last stored rate is used and the box says so.
- **Charts**, for all time, 5 years, 1 year, 6 or 3 months, with a hover tooltip and a table view:
  - *Amounts*: the total, then one small chart per account with its own scale. Each one has a dashed line at its first recorded balance (its start) and shows the change since then. The ⤢ button on an account opens its chart maximized (close it with Close, Esc or a click outside).
  - *% since the start*: every account and the total on one chart, as the % change since each one's own start, so they can be compared.

  *Group by*: by account (each one shown with its institution) or by institution (one series per institution, the sum of its accounts; its start is its total on the first date any of them had a balance). The choice is saved in the browser.

  *Currency*: with accounts in more than one currency, *All currencies* (the default) shows the charts of each currency one after the other, each in its own currency (no conversion); or pick one currency. *All in EUR* (the default currency) converts every account into it, each date at that date's ECB rate, and draws them on one chart; the rates are stored, so each date is asked for once, and only currencies and dates are sent, never amounts.

  Each account keeps its colour in every chart (16 colours; past 16 they repeat) and the total is drawn in black (white in dark mode). An account keeps its last recorded balance until a new one is recorded.
- **CSV import / export**: one row per account and date. Columns `account`, `date`, `amount` (required) and `type`, `currency`, `institution`, `note`, `available_from` (optional; `available_from` and `type` are only used when an account is created); Spanish names also work (`cuenta`, `fecha`, `saldo`, `tipo`, `moneda`, `entidad`, `nota`, `disponible_desde`). Comma, semicolon or tab separated; UTF-8 or Windows-1252; amounts as `1234.56`, `1.234,56` or `1,234.56`; dates `YYYY-MM-DD` or `DD/MM/YYYY`. An account is found by its name and institution (without an institution in the row, by its name when only one account has it). Missing accounts are created. A balance already recorded for that account and date is never overwritten: the row is skipped and listed in the result.
- **JSON export / import (everything)**: **Import / export → Export everything (JSON)** downloads all the data (accounts with all their fields, including notes, archived and accounts without balances, plus balances and milestones). **Import JSON** takes such a file in one of two ways: *merge* adds only what is not there yet (an account is found by its name and institution; a balance for the same account and date, or the same milestone, is not imported and is listed), and *replace* deletes all the current data and leaves exactly the file's, after asking for confirmation and copying the database first (`balances.db.backup-before-import-<date>`, or `<id>.db.enc.backup-before-import-<date>` in multi-user mode, encrypted; the 3 newest are kept). A file with any error changes nothing.
- **Demo mode** (**⚙ → Demo mode**, the *Try it with sample data* button when there are no accounts yet, or the link `/demo`): the app with made-up sample data (several institutions and accounts, EUR and USD, stock options not available yet, a mortgage, a closed account, two years of history), to show it to other people or to see how it works. The sample data lives in its own temporary database, apart from the real one, which is not read or changed while a browser is in demo mode; a banner shows it, with buttons to reset the sample data and to leave the demo. It is per browser (a cookie) and needs the login like the rest of the app. A demo's data is limited to 5 MB, and CSV or JSON files imported into it to 1 MB. In multi-user mode, one address (IPv6: one /64) can start 10 demos an hour; demos never ask the exchange-rate service: they use the rates already stored.
- **Optional login** with a user and password, and optionally a one-time code from an authenticator app.
- English and Spanish interface (**⚙ → Language**), and a separate **date and number format** (Spain, UK, US, Germany...) that dates, amounts and the date boxes follow; by default it goes with the language. Dates are in your **time zone** (⚙ → Time zone): the browser's by default, or another one you pick; "today", the default date of new balances, and the export file names follow it. The three are saved in the browser. The server itself always runs in UTC. To add a language, copy `app/static/i18n/en.json` to `<code>.json` and translate it.

## Development

```bash
pip install --require-hashes --no-deps -r requirements-dev.txt
DATA_DIR=./data uvicorn app.main:create_app --factory --reload
python -m playwright install chromium   # once, for the end-to-end tests
pytest -q
ruff check app tests scripts            # lint, as in the CI (settings in pyproject.toml)
```

**Dependencies**: the packages and their minimum versions are in `requirements.in` (the app) and `requirements-dev.in` (tests and checks). `requirements.txt` and `requirements-dev.txt` are generated from them with [pip-tools](https://pip-tools.readthedocs.io) under Python 3.14: every package, indirect ones too, with its exact version and hashes, so each build installs exactly what was tested. Don't edit them by hand; after changing a `.in` file, or to update everything:

```bash
pip install pip-tools
pip-compile --generate-hashes --strip-extras requirements.in                         # add --upgrade to update
pip-compile --generate-hashes --strip-extras --allow-unsafe requirements-dev.in
```

Dependabot does this every week in one PR. The CI also lints with ruff and checks the packages for known vulnerabilities with pip-audit. The Docker base image is pinned by digest, and Dependabot updates it too.

**Frontend**: plain JavaScript modules in `app/static/js/` (no build step), one per part of the page: `main.js` starts it, `api.js`, `format.js`, `totals.js`, `accounts.js`, `charts.js`, `dialogs.js`, `settings.js`, `demo.js`. The page loads them from `/static/v/<version>/js/`, so a new version is never mixed with old copies in the browser's cache. `tests/test_e2e.py` drives the real page in Chromium (load, add an account and a balance, update balances, CSV import, demo with its charts, language); without Playwright those tests are skipped.

**Image**: `ghcr.io/dodert/accounts-balance-tracker` (linux/amd64 and linux/arm64), built by GitHub Actions on every push to `main` and on `v*` git tags. Versions follow [SemVer](https://semver.org): `main` builds are the release version in `app/__init__.py` (`__version__`), so bump it in every PR (`Z` for fixes, `Y` for features). A push to `main` is tagged `latest`, `X.Y.Z`, `X.Y` and `sha-<commit>`.

**Trying a branch** before merging it: **Actions → CI → Run workflow**, pick the branch. The image is pushed as `ghcr.io/dodert/accounts-balance-tracker:<branch>`, `pre-release` and `X.Y.Z-<branch>.<run>`.

**Old images**: **Actions → Clean up old images** and **Actions → Delete untagged images** delete old image versions (dry run by default). They need the repository to have the Admin role in the package's *Manage Actions access*.

**Unraid template**: the `Sync Unraid template` workflow publishes `unraid/accounts-balance-tracker.xml` and its icon to the public repo [dodert/unraid-templates](https://github.com/dodert/unraid-templates). It needs the repository secret `TEMPLATES_TOKEN`: a fine-grained token with *Contents: Read and write* on `dodert/unraid-templates` only.

**Screenshots**: start the app on port 8012 and run `node scripts/screenshots.js docs` (needs Playwright; see the comment at the top of the script).

## API

Interactive docs at `/docs` when `API_DOCS=true` (off by default). Main endpoints: `/api/accounts`, `/api/accounts/{id}/balances`, `/api/balances/bulk`, `/api/milestones` (and `/api/accounts/{id}/milestones`), `/api/summary`, `/api/summary/combined`, `/api/fx?from=USD&to=EUR&date=YYYY-MM-DD`, `POST /api/fx/refresh`, `/api/history?currency=EUR&since=YYYY-MM-DD`, `/api/history/converted?since=YYYY-MM-DD`, `/api/export/csv`, `/api/import/csv`, `/api/export/json`, `/api/import/json` (`mode`: `merge` or `replace`), `/api/demo` (POST on, DELETE off, `/api/demo/reset`).

**Amounts are integer cents**, in and out (since 0.29): `1234.50` is sent and received as `123450`, so no client loses a cent to floating-point numbers. A decimal number (`1234.5`) or text is refused. The JSON export file and the CSV keep amounts as decimal text (`1234.50`).

## License

[GPL-3.0](LICENSE)
