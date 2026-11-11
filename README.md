# FeeFlow – One-tap payment confirmation with admin verification (v2.1)

Parents pay by UPI and tap **"I have paid"**. They don't need a UTR or a screenshot.
The tuition centre administrator checks the real bank credit before anything is marked
paid. This keeps the parent experience simple and the financial records accurate.

| Piece | Where |
|---|---|
| FastAPI backend | `app/` (`app/main.py` factory) |
| State engine | `app/states.py` |
| Parent payment page (mobile-first) | `/pay/{token}` → `app/static/pay.html`, `js/pay.js` |
| Admin **Payment Verification** dashboard | `/admin` → `app/static/admin.html`, `js/admin.js` |
| Supabase / Postgres migration | `migrations/001_feeflow_payment_workflow.sql` |
| Android SIM SMS sync contract | `docs/ANDROID_DEVICE_SYNC.md`, `app/routers/devices.py` |
| State diagrams / UI flows | `docs/PAYMENT_STATE_DIAGRAM.md`, `docs/UI_FLOW.md` |
| Tests (state transitions + scenarios A–G) | `tests/` |
| Android app (Kotlin): admin verification + SIM SMS engine | `android/` ([README](android/README.md)) |
| HTTPS deployment (Render + PostgreSQL) | [docs/DEPLOY.md](docs/DEPLOY.md), `Dockerfile`, `render.yaml` |

## Run locally

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python -m app.seed --reset
```

```bash
.venv/Scripts/python -m uvicorn app.main:create_app --factory --port 8000
```

The seed step prints the admin login (`admin@brightminds.test` / `feeflow-demo-123`, local
development only) and one parent payment link per demo student. Open a link to act as a
parent, and open `http://localhost:8000/admin` to verify. API docs: `http://localhost:8000/docs`.

Run the tests:

```bash
.venv/Scripts/python -m pytest -q
```

On macOS/Linux use `.venv/bin/` instead of `.venv/Scripts/`.

To run the same suite on PostgreSQL (including real-concurrency tests), set
`FEEFLOW_TEST_DATABASE_URL=postgresql+psycopg://user@host:port/dbname`.

## Configuration

Environment variables (see `.env.example`): `FEEFLOW_DATABASE_URL` (SQLite by default; a
Supabase `postgresql+psycopg://…` URL in production), `FEEFLOW_SECRET_KEY`,
`FEEFLOW_PUBLIC_BASE_URL`, `FEEFLOW_RATE_LIMIT`, `FEEFLOW_BACKGROUND_JOBS`.
Per-centre settings are on `tenants`: `verification_grace_hours` (default 24),
`reminder_frequency_hours`, and `ack_sms_enabled`.

## API

| Method & path | Who | Purpose |
|---|---|---|
| `GET /api/v1/payment-declarations/public/{secure_token}` | parent (link token) | Fee + current status (survives refresh) |
| `POST /api/v1/payment-declarations` | parent (link token) | `action`: `INITIATED` / `PROCESSING` / `PAID` / `FAILED`; optional partial amount, app, time, note |
| `POST /api/v1/payment-declarations/{reference}/clarification` | parent (link token) | Add information when asked |
| `GET /api/v1/payment-declarations/{declaration_id}` | admin | Full review detail (moves REPORTED → PENDING_VERIFICATION) |
| `GET /api/v1/payment-verification/pending` | admin | Verification queue |
| `POST /api/v1/payment-verification/{id}/confirm` | admin | Record verified bank transaction(s), receipt, SMS |
| `POST /api/v1/payment-verification/{id}/reject` | admin | Not received (reason required) |
| `POST /api/v1/payment-verification/{id}/request-clarification` | admin | Ask the parent |
| `POST /api/v1/payment-verification/{id}/reopen` | admin | Controlled reopen of a rejected declaration |
| `GET /api/v1/installments/{id}/payment-status` | admin | Balance, declaration status, reminder sync state |
| `POST /api/v1/installments/{id}/payments` | admin | Record an extra genuine credit (e.g. a duplicate transfer) |
| `GET /api/v1/reports/{type}` | admin | 8 reports |
| `GET /api/v1/audit-logs` | admin | Audit trail |
| `GET /api/v1/devices/sync`, `POST /api/v1/devices/ack` | Android device | SIM SMS engine sync |
| `POST /api/v1/devices/register`, `GET /api/v1/devices`, `POST /api/v1/devices/{id}/revoke` | admin | Register / list / revoke the centre's SMS phone (one active per centre) |

Every mutating endpoint needs an `Idempotency-Key` header, except the parent POST, which also
accepts the key in its body. Admin endpoints need `Authorization: Bearer <token>` from
`POST /api/v1/auth/login` and are tenant-scoped. Public endpoints expose no internal IDs.
