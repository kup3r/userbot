
## 10.1.0 — Phone Login

- `/connect` now offers phone-number login alongside String Session.
- Added short-lived browser authorization jobs using an in-memory Pyrogram client.
- Phone, verification code and 2FA password are not written to the service database.
- Successful authorization is checked against the tenant owner Telegram ID before the session can be stored.
- Added resend, expiry, cancel, wrong-attempt limits and human-readable Telegram errors.
- On Render, the public login URL automatically uses `RENDER_EXTERNAL_URL`; `PUBLIC_BASE_URL` can override it.

# vNext change log

## 2026-09-23 — vNext service build

### Tenant / subscription
- One isolated Pyrogram worker per subscribed account.
- Per-tenant `storage.db` and `modules/` directory.
- String Session is validated against the customer's Telegram ID before being encrypted.
- Fernet encryption for stored sessions.
- Basic / Pro / Premium plans.
- Telegram Stars payments (`XTR`) with idempotent successful-payment handling.
- Trial period, referral links/bonus days and one-time promo codes.
- Admin user controls from Control Bot.
- Worker queue and crash circuit breaker.

### Dashboard
- `inline.py` is native: it uses the current userbot account and no Helper Bot/BOT_TOKEN.
- Owner-only callback validation.
- Latest-panel binding prevents stale/copied callbacks from controlling the current worker.
- Module pagination, enable/disable, info and reload.
- Statistics, subscription, notes, bookmarks, snippets, triggers, settings, doctor and reload-all pages.
- `Reload All` rebinds the current panel to the new inline module instance.

### New local modules
- `snippets.py` — text templates and capture-from-reply.
- `media.py` — media/file identifiers and metadata inspection.
- `triggers.py` — keyword monitoring with private Saved Messages notifications and cooldown.
- `exporter.py` — JSON export of tenant-owned data without secrets.
- `presets.py` — persistent module presets; the preset module now keeps itself enabled when applying a preset.
- `watchdog.py` — recovery attempts for enabled-but-not-loaded modules.
- Plan custom-module quotas: Basic 5 / Pro 20 / Premium 50 by default.
- Admin queue/worker statistics commands.

### Operations
- Read-only authenticated `/admin` dashboard.
- `/metrics` operational metrics endpoint.
- `/queue` and `/workerstats` admin commands.
- Stale pending payment cleanup.
- Subscription expiry notifications.
- `render.yaml` for Web Service + Persistent Disk.
- Optional `render.postgres.yaml` for Render Postgres.

### Verification
- Dependency-light self-test suite: syntax, command conflicts, native inline, feature presence, Render configuration and DB schema.
- Final package excludes `.env`, databases, `__pycache__`, backups and other local secret/state files.

> Real Telegram API/payment execution must still be smoke-tested after deployment because this build environment does not have the external Telegram dependencies/network required for a live integration test.

## 2026-09-23 — v10 Hikka-style runtime expansion

### Framework API
- Added Hikka-style localized `strings`, `strings_ru`, `strings_en` lookup via `BaseModule.tr()`.
- Added `.lang ru|en|uk` and localized command documentation (`ru_doc`, `en_doc`, `uk_doc`).
- `@command()` can infer names; `_cmd` suffix is normalized away.
- Added optional command cooldowns.
- Added public `@callback(...)` decorator with owner-only-by-default callback registration.
- Added `BaseModule.answer()`, `get_args_raw()`, `get_args()` and tenant-local `${name}` variable expansion helpers.

### Runtime / security
- Added tenant-scoped command history that excludes command arguments by design.
- Added Quiet Mode to pause watchers without disabling commands.
- Added per-chat command rules.
- Added five-version rolling history for replaced custom modules with rollback through the normal security scanner.
- Custom modules cannot overwrite names of built-in modules.

### New modules
- `quiet.py`
- `variables.py`
- `logs.py`
- `scanner.py`
- `download.py`
- `chatrules.py`

### Dashboard / docs
- Native dashboard now exposes command history and security/runtime state.
- Developer template now demonstrates localized strings, callback handlers and config.
- README expanded with Hikka-style API notes and v10 migration guidance.


## v10.1.1 local login fix
- Never generate phone-login links to 127.0.0.1.
- Added optional LAN URL auto-detection for local testing.
- Added clear setup error when no browser-reachable base URL is configured.
- Fixed scheduler syntax error shown on Python 3.11 startup.
