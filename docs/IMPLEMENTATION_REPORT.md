# Implementation report – FeeFlow v2.1 payment workflow

## Starting point

The spec says to inspect the existing FeeFlow payment models, APIs, parent page and reminder
logic first. **This folder had no existing FeeFlow code**, only the specification. The
workflow was therefore built as a self-contained FastAPI service. It follows the spec's
architecture: multi-tenant, FastAPI, Supabase Postgres via SQLAlchemy, a web parent page and
admin dashboard, and the Android SIM SMS engine through a device sync API. Existing tables
(`tenants`, `students`, `installments`, `receipts`, `reminder_schedules`, `sms_outbox`,
`devices`) are created with `IF NOT EXISTS` and extended with `ALTER … ADD COLUMN IF NOT EXISTS`,
so the migration can be applied over a real FeeFlow schema after reconciling column names.

## Files

| Area | Files |
|---|---|
| Config / DB | `app/config.py`, `app/db.py`, `app/models.py` |
| State engine | `app/states.py` |
| Security | `app/security.py` (PBKDF2 passwords, HMAC admin tokens, hashed link/device tokens), `app/ratelimit.py`, `app/idempotency.py`, `app/audit.py` |
| Services | `app/services/declarations.py`, `verification.py`, `reminders.py`, `sms.py`, `receipts.py`, `links.py`, `upi.py`, `reconciliation.py`, `reports.py` |
| API | `app/routers/public.py`, `admin.py`, `devices.py`, `pages.py`, `app/schemas.py`, `app/main.py` |
| UI | `app/static/pay.html`, `app/static/js/pay.js`, `app/static/admin.html`, `app/static/js/admin.js`, `app/static/css/app.css` |
| Migration | `migrations/001_feeflow_payment_workflow.sql` |
| Seed | `app/seed.py` |
| Docs | `docs/PAYMENT_STATE_DIAGRAM.md`, `docs/UI_FLOW.md`, `docs/ANDROID_DEVICE_SYNC.md` |
| Tests | `tests/test_state_engine.py`, `tests/test_acceptance.py`, `tests/test_security_and_integrity.py` |

## Key design decisions

* **Declaration ≠ transaction.** `payment_declarations` records what the parent said.
  `verified_payments` records real money and is created only by the admin confirm flow (or a
  future authorized reconciliation process). `installments.status` is derived only from
  verified money.
* **Duplicate protection at three layers:**
  1. Idempotency keys on every mutation.
  2. A partial unique index allowing one *active* declaration per installment, plus row locks
     (`SELECT … FOR UPDATE`) on Postgres.
  3. Unique `(tenant_id, bank_reference)` and unique `bank_transaction_id` on verified payments,
     so a bank transaction can never be credited twice.

  SMS de-duplication uses a unique `dedupe_key`.
* **Genuine double payments** are recorded as separate verified payments. Money above the
  balance goes to `excess_paise` and a `FLAGGED` row in `payment_adjustments` (refund or
  adjust). It is never discarded.
* **The admin confirm flow requires** an explicit confirmation, "checked the bank
  transaction", "credited to the centre's account", a UTR or evidence note per transaction,
  and an explicit acknowledgement when the verified amount differs from the declared amount.
* **Reminders:** a declaration pauses reminders for the grace period (default 24h). Verifying
  cancels them; a partial verification re-renders them for the remaining balance; rejecting
  restores eligibility no earlier than one reminder interval from now. When the grace period
  expires, the admin is notified and reminders resume, but the declaration stays pending.
  Every change is versioned and shown as `PENDING_SYNC` until the Android device acknowledges it.
* **UTR is optional and admin-side.** Parents are never required to enter it. It is offered
  only as an optional field when the centre asks for clarification. Notes that look like they
  contain a UPI PIN, OTP or password are rejected.
* **Public API** uses only the secure link token (stored as SHA-256) and a non-guessable
  declaration reference (`PD-…`). No internal IDs are exposed.
* **Audit log** is append-only. Postgres triggers block UPDATE/DELETE on `audit_logs` and
  block changes to money fields or deletion of `verified_payments`.

## Additions beyond the spec (small, intentional)

* `POST /payment-verification/{id}/reopen` – the "controlled workflow" for reopening rejected
  declarations (spec §7), audited, and blocked if another declaration is active.
* `POST /installments/{id}/payments` – records a genuine extra credit that has no declaration
  (Scenario G after the fee was already PAID).
* `POST /installments/{id}/payment-links`, notifications, adjustment resolution, and bank
  statement import with match suggestions (spec §13, future-ready; it never auto-credits).

## Tests

`.venv/Scripts/python -m pytest -q` → **159 passed**.

* `test_state_engine.py`: the full parent transition matrix (9 × 8), the admin transition
  matrix (7 actions × 8 states), installment status, and display status.
* `test_acceptance.py`: Scenarios **A–G** end to end over HTTP, including receipt rendering,
  SMS content, reminder cancellation/rebasing and excess flagging.
* `test_security_and_integrity.py`: auth, tenant isolation, expired/invalid links, confirm
  safeguards, never crediting a transaction twice, confirm idempotency, clarification
  round-trip, reopen, failed-then-retry, grace-period expiry, device sync/ack, offline-device
  stale send, bank import matching, reports, idempotency mismatch, rate limiting, and QR.

The UI was also exercised manually in a browser: parent page → QR → "I have paid" (tapped
three times, one declaration created) → refresh keeps the status → admin login → review →
confirm with UTR → parent page shows PAID + receipt → receipt page renders.

## Outstanding issues / next steps

1. **No existing FeeFlow codebase was available.** Table and column names in the migration
   are assumptions. Map them to the real schema, especially `installments`, `receipts`,
   `reminder_schedules` and `sms_outbox`, before applying it to Supabase.
2. **The migration was syntax-checked with the Postgres parser (pglast) but not executed
   against a live Postgres/Supabase instance.** The automated tests run on SQLite. Run it on a
   staging Supabase project and run the test suite against Postgres (`FEEFLOW_DATABASE_URL`).
3. **Kotlin Android app** (`android/`) is built and unit/E2E-tested on the JVM but has **not been
   run on a physical phone or emulator** yet. Still to check on a device: real SIM sending,
   WorkManager timing under OEM battery savers, and the runtime permission flow. Choosing a SIM on
   dual-SIM phones is not implemented.
4. The rate limiter is in-process. Use Redis or similar for multiple API instances.
5. The admin auth here is a minimal HMAC bearer token. In production, use the existing FeeFlow
   or Supabase Auth admin identities.
6. Real-time admin notification is in-app (dashboard badge, polled every 30s). Push or e-mail
   is not implemented.
7. UPI app-specific schemes (`tez://`, `phonepe://`, `paytmmp://`) behave differently across
   Android/iOS versions. The generic `upi://pay` chooser ("Other UPI app") and the QR code are
   the fallbacks. Test on real devices.
8. Receipt numbering uses count+1 per tenant, protected by a unique constraint. Under heavy
   concurrency on Postgres, switch to a per-tenant sequence.


## Android app (v2.1)

`android/` is a Kotlin / Jetpack Compose app (minSdk 26, targetSdk 34). See [android/README.md](../android/README.md).

* **Payment Verification for admins:** sign in, see the pending queue, and open the full review
  detail (with bank-statement match suggestions). From there the admin can confirm (one or more
  bank transactions, UTR or evidence note, two required checks, amount-difference acknowledgement,
  and a second "Yes, verify" step), mark as not received (reason), or ask the parent. Every action
  uses one idempotency key per opened form.
* **SIM SMS engine** (`core/SmsEngine.kt`, pure Kotlin): runs every 15 minutes through WorkManager
  and on demand. It de-duplicates by message id with state saved before and after each send,
  checks the latest local schedule state before any reminder (`hold_until`, cancellations,
  versions), holds reminders when the schedule is over 24h stale, skips reminders more than 48h
  late, retries failed sends 3 times, and queues acks while offline. Tokens are stored in
  EncryptedSharedPreferences.
* **Backend additions:** `POST /api/v1/devices/register`, `GET /api/v1/devices`, and
  `POST /api/v1/devices/{id}/revoke`. Only one active SMS phone per centre: the old phone is
  deactivated, its unacknowledged messages are re-queued, and the full schedule is resynced to the
  new phone. A dispatched message is re-offered only to the same phone. Backend tests: **160 passed**.
* **Android tests:** 25 JVM unit tests pass: 15 engine scenarios, money/time/store, and the HTTP
  client against MockWebServer. `LiveBackendE2ETest` passed against the running backend using the
  app's real client and engine with a fake SIM.
* **Builds:** `app-debug.apk` (10.9 MB) and a minified `app-release-unsigned.apk` (1.8 MB).
  The release build needs a signing key before distribution.


## Workspace self-setup (v2.1.1)

* The host configures only `FEEFLOW_ADMIN_EMAIL` and `FEEFLOW_ADMIN_PASSWORD`. On start the server makes
  sure that admin exists, and the configured password always wins. The admin starts **without** a
  workspace.
* On the first sign-in (web and Android) the admin sees **Create your workspace**: centre name, UPI ID
  (validated as `handle@bank`, typed twice and confirmed), payee name and contact. The receipt prefix is
  generated and made unique (e.g. `SVTC`, or `BMTC2` on a clash).
* **Settings** (web) edits the centre name, UPI ID, payee name, contact details, reminder grace and
  frequency, and the acknowledgement SMS. A UPI ID change is audited as `UPI_ID_CHANGED` with the old and
  new values, and open payment pages use the new ID immediately.
* Until the workspace exists, every tenant-scoped API returns `403` with `X-FeeFlow-Error: WORKSPACE_REQUIRED`.
* Endpoints: `GET /api/v1/auth/me` (now includes `has_workspace` and `workspace`), `POST /api/v1/workspace`,
  `GET /api/v1/workspace`, `PATCH /api/v1/workspace`.
* Upgrade: on PostgreSQL, start-up drops the old `NOT NULL` on `admin_users.tenant_id`. This was tested
  against a database created by the previous version.
* Tests: backend 166 passed on SQLite and 168 on PostgreSQL (including concurrency). Android: 26 unit tests,
  plus the live end-to-end test passed against the local server.


## Self sign-up, trial, students and platform console (v2.2)

* **Sign-up:** `POST /api/v1/auth/signup` is rate-limited to 5 per hour per IP and needs a password of
  10+ characters. It leads to workspace creation, which starts a **30-day trial**. Sign-up is in the
  web login screen and the Android app.
* **Subscription:** `TRIAL` / `ACTIVE` / `SUSPENDED` are stored and `EXPIRED` is derived
  (`app/services/subscription.py`).
  * **Expired:** the centre can still read its data, verify payments and receive parent payments on
    existing links, but cannot add students or send links (`403 TRIAL_EXPIRED`).
  * **Suspended:** the owner is locked out, payment pages return `410`, and the SMS phone gets `403`.
* **Students:** a full profile (name, auto ID like `SVT-0001`, class, DOB, gender, school, address,
  joining date, parent name/mobile/email) and an optional monthly fee plan (amount + due day 1-31,
  clamped to the month's last day). Saving creates this month's fee. A background job creates each new
  month's fee, and a unique index prevents duplicates. Search, edit and deactivate are supported.
* **Send fee link:** `SMS` queues the link for the latest unpaid fee on the centre's SMS phone (with a
  clear warning if none is registered). `SHARE` returns the message for WhatsApp or the share sheet. It
  is idempotent per tap, and deliberate resends are allowed. Automatic sending when monthly fees are
  created is built in but **off** by default (Settings → "Send the fee link by SMS automatically").
* **Platform console** at `/platform` for `FEEFLOW_ADMIN_EMAIL` (`is_platform_admin`): every workspace
  with owner, status, trial days, counts (students, fees, paid), SMS phone and last activity. It shows
  counts only, never student or parent data. Actions: extend trial, activate, suspend (reason required),
  reactivate, all audited. The platform owner cannot suspend their own workspace.
* **Upgrade:** PostgreSQL adds the new columns and index on start. This was tested on a database from the
  previous version, and existing workspaces get a 30-day trial counted from the upgrade.
* **Tests:** backend 180 on SQLite and 182 on PostgreSQL. Android: 29 unit tests, plus 2 live
  end-to-end tests that pass against the local server, including sign-up → workspace → student → fee link.
  The website flow was walked through in a browser.
* **Not included:** subscription billing (activate centres by hand), email verification, password
  reset by email, and student photos.


## Payments to personal UPI IDs (v2.3)

Google Pay and PhonePe often block **pre-filled payment links to personal UPI IDs** (anti-fraud). They
show misleading errors such as "exceeded the bank limit" or "receiver's bank is facing server issues",
even for Rs. 1. No money is debited.

* **Business (merchant) UPI IDs:** Settings → "This is a business (merchant) UPI ID" plus a merchant
  category code (default `8299`, schools and education). Payment links then include `mc` and a stable,
  unique `tr` reference per fee, as UPI apps expect for merchant payments. Get a free merchant UPI ID from
  Google Pay for Business, PhonePe Business, Paytm for Business or BharatPe.
* **Fallbacks on the parent page:**
  * **Pay using UPI ID:** payee name, UPI ID, amount and note, each with a Copy button, plus steps. If an
    in-app browser blocks copying, the value is selected for long-press copy.
  * **Save QR to gallery:** `GET /api/v1/pay/{token}/qr.png?download=1`, a PNG with the amount, for
    "Scan QR → upload from gallery".
* **Order on the page:** for personal UPI IDs, a short tip is shown and these two ways come first, with the
  one-tap app buttons last. For merchant IDs, the one-tap buttons come first.
* **Tests:** backend 185 on SQLite and 187 on PostgreSQL. The browser check covered both page orders,
  Copy, the QR download and the Settings switch.
