# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What it is

Self-hosted app to track the balance of accounts over time (FastAPI + SQLite, vanilla JS frontend), shipped as a
multi-arch Docker image (`ghcr.io/dodert/accounts-balance-tracker`) with an Unraid template. The user-facing docs
are in `README.md` (English); keep it up to date when a setting, feature or endpoint changes.

## Commands

```bash
pip install --require-hashes --no-deps -r requirements-dev.txt
DATA_DIR=./data uvicorn app.main:create_app --factory --reload   # run locally
python -m playwright install chromium                            # once, for tests/test_e2e.py
pytest -q                                                        # all tests
pytest tests/test_api.py::test_name -q                           # one test
E2E=1 pytest tests/test_e2e.py                                   # E2E tests fail instead of skipping without a browser
ruff check app tests scripts                                     # lint (settings in pyproject.toml)
pip-audit --requirement requirements.txt --require-hashes --disable-pip
```

The CI (`.github/workflows/ci.yml`) runs exactly these: ruff, pip-audit, pytest with `E2E=1`, then the Docker build.

**Dependencies**: edit only `requirements.in` / `requirements-dev.in`, then regenerate the locked files (Python 3.14):

```bash
pip-compile --generate-hashes --strip-extras requirements.in
pip-compile --generate-hashes --strip-extras --allow-unsafe requirements-dev.in
```

Never edit `requirements*.txt` by hand.

Tips: without network to Frankfurter, point `FX_URL` to a local fake server. The HTML pages are read at startup:
restart uvicorn after changing them. `PLAYWRIGHT_CHROMIUM` can point to an existing Chromium for the E2E tests. A
manual CI run on a branch sometimes fails pushing to GHCR with `permission_denied: write_package`: it is not a
permissions problem, "Re-run failed jobs" fixes it.

## Architecture

- `app/main.py` — `create_app(settings, fx_fetcher)` builds a `deps.Context` shared by the routers in
  `app/routes/` (one module per area) and the middleware (`middleware.py`). One uvicorn process only
  (`--workers 1`): login limits, demos and per-user locks live in memory.
- `app/config.py` — `Settings` (pydantic-settings, env vars). Every user setting must also be in
  `unraid/accounts-balance-tracker.xml` (`tests/test_unraid_template.py` checks it) and in the README's settings table.
- **Two modes**:
  - Single user (`MULTI_USER=false`, default): one SQLite `DATA_DIR/balances.db`, optional login
    (`AUTH_USERNAME`/`AUTH_PASSWORD`, `AUTH_TOTP`). Not encrypted, on purpose.
  - Multi user (`MULTI_USER=true`): `app/users.py`. `users.db` holds users/invitations/sessions; each user's data
    is `users/<id>.db.enc`, AES-256-GCM with a random data key wrapped by Argon2id(password) and a recovery key
    (`app/crypto.py`). The database is decrypted in memory per request. The admin can't read user data.
- **Migrations**: Alembic, applied automatically at startup by `app/migrate.py`. Two trees:
  `app/migrations/` (each user's data: `balances.db` and the encrypted per-user DBs) and `app/migrations_users/`
  (`users.db`). Models: `app/models.py` (data) and `app/users.py` (system tables).
- **Demo mode** (`app/demo.py`, cookie `abt_demo`): sample data in a temporary SQLite per visitor, never in
  `DATA_DIR`. Built from `SAMPLES` (en/es), relative to today.
- **Exchange rates**: `app/fx.py` (Frankfurter API, `FX_URL`); stored rates in the DB, shared `rates.db` in multi-user.
- **Dates**: the server runs in UTC; the user's "today" comes from the `X-Local-Date` header (`app/clock.py`).
- **Amounts are integer cents** in the API (since 0.29); CSV/JSON files keep decimal text.
- **Backups**: daily copies in `DATA_DIR/backups/<date>/` (`app/backups.py`, `BACKUP_KEEP`), plus copies before a
  migration and before an import that replaces data.
- **Frontend**: plain ES modules in `app/static/js/` (no build step), served from `/static/v/<version>/js/`.
  `i18n.js` + `app/static/i18n/{en,es}.json`: every text in both languages (`tests/test_i18n.py` checks keys match
  and that every key used exists). Admin page is a Jinja template (`app/templates/admin.html`).
- **Docker**: `Dockerfile` (base image pinned by digest), `docker/entrypoint.sh` (PUID/PGID 99:100 + setpriv),
  healthcheck on `/api/health`.
- **Unraid**: `unraid/` is synced to the public repo `dodert/unraid-templates` by the `Sync Unraid template`
  workflow (secret `TEMPLATES_TOKEN`); that repo builds its own README. All variables visible (`Display="always"`);
  only `API_DOCS`, `PUID`, `PGID`, `FORWARDED_ALLOW_IPS` go under advanced.

## Decisions from the architecture review (2026-11-09)

The review (issues #66-#73, then #92-#105; PRs #74-#106) settled these. Don't undo them without asking:

- **SQLite only.** Postgres and `DATABASE_URL` were removed (#73): nobody used them. Don't reintroduce other engines.
- **Exchange rates** (#66): the "All in EUR" chart (`/api/history/converted`) converts every date at **today's**
  rate, so it shows what the balances are worth now. Today's rate is fetched again when the stored one is more than
  3 hours old, until the ECB's own rate of the day (~16:00 CET) is stored. `FX_DAILY_LIMIT` defaults to 30. Rates are
  fetched without holding the user's database lock; in multi-user they are stored only in the shared `rates.db`.
  Every rate logs at INFO where it came from (service, stored rates, shared store).
- **Import / export** (#67): "Export everything (JSON)" is the full, versioned copy (accounts with all fields,
  balances, milestones); the CSV is only a table of balances and stays that way (no milestones, by choice). Import
  JSON has two modes: *merge* never overwrites: it skips what already exists (same account and date, same milestone)
  and lists it; *replace* asks for confirmation and makes a backup first (`before-import`, newest 3 kept). CSV import
  behaves like merge. A file with any error changes nothing.
- **Reads** (#68): balances are read as plain rows with window queries (`history.py`), never as ORM objects; this
  took `/api/accounts` from 2.1 s to 0.2 s with 104k balances. No `/api/dashboard` endpoint (decided: not needed).
- **Encrypted per-user DB**: every commit re-encrypts and fsyncs the whole file, so batch writes in one commit.
  Requests of one user are serialized by a per-user lock: keep slow work (network) outside it.
- **Code layout** (#69): one router module per area in `app/routes/`, shared state in `deps.Context`, middleware in
  `middleware.py`, admin HTML in a Jinja template (autoescape), never f-strings.
- **One process** (#70): `--workers 1`; the last used TOTP step is persisted in `.totp_secret` (atomic write).
- **Frontend** (#71): ES modules, `init*()` called from `main.js`; Playwright E2E tests save a screenshot per test
  (`test-screenshots/`), which the CI uploads.
- **Time** (#72, #92): the server always runs in UTC (`use_utc()` forces it, `TZ` is not offered). The user's dates
  use the browser's time zone or the one chosen in ⚙ (`localStorage` `abt-tz`), sent as `X-Local-Date`
  (`?local_date=` on downloads).
- **Amounts** (#94): integer cents in the API; the JS works in cents too. Database, JSON and CSV unchanged.
- **Port** (#82): the app listens on 80 inside the container; the host port defaults to 8085.
- **Backups** (#96): daily, `BACKUP_KEEP=7`; multi-user copies `users.db` and the `.enc` files still encrypted; users
  see their own list in ⚙ (`/api/backups`); deleting a user removes them from the backups too.
- **users.db** has its own Alembic tree (#98), with the same backup-before-migrate rule.
- **Locked dependencies** (#100): hashes, ruff, pip-audit, base image by digest.
- **Demo** (#102): one demo per browser in both modes, at most 10 new demos per hour per IP (`DEMOS_PER_IP`).
- **TOTP per user** (#105): optional in multi-user; the recovery key logs in without the code; the admin can remove it.
- **Discarded on purpose** (plan #7): extending an expired account also unblocks one the admin blocked by hand; the
  invitation token appears in the access log; `FORWARDED_ALLOW_IPS` trusts the LAN and Docker ranges by default.
- **Mobile app**: parked. A sync would need UUIDs, `updated_at` with soft deletes and `/api/sync` in every database.

## Project rules

- **Issue first**: every change request gets a GitHub issue written in English, assigned to `dodert`; the PR
  references it with `Closes #N`.
- **Branches**: short descriptive English names (`feat/demo-mode`, `fix/login-rate-limit`, `docs/...`), never a
  session/thread id.
- **Version**: bump `__version__` in `app/__init__.py` in every PR that changes the app (SemVer: `Z` fixes, `Y`
  features). When several PRs are open at once, give each a different number.
- **Migrations never lose data**: `upgrade_database` makes a backup before migrating and refuses to migrate
  without it; foreign keys are off during the migration and checked with `PRAGMA foreign_key_check` after (a batch
  rebuild with `ON DELETE CASCADE` once wiped all balances). Non backward-compatible changes must migrate the existing
  data. Every new migration needs a test that starts from the previous revision **with data** and uses the app's
  engine (foreign keys ON), like `tests/test_migrations.py`.
- **Privacy**: never send amounts, account names or institutions to external services. External calls carry only
  generic parameters (currencies, dates); conversions are computed locally. Cover new external calls with a test
  that checks what is sent. Database files and backups are `0600`.
- **Demo data**: a new feature must work in demo mode; add sample data to `app/demo.py` when it needs any.
- Tests: each test has a one-line docstring (it goes into the CI's test report). New UI flows get an E2E test.
- README and code comments in English.
