"""Fee creation (minimal fee management) and production/bootstrap configuration."""
import pytest

from tests.conftest import key


def test_create_student_and_fee_sends_link_and_schedules_reminders(env):
    r = env.c.post("/api/v1/students", headers=env.admin,
                   json={"student_code": "BM-2001", "name": "Ishaan Rao", "parent_mobile": "98765 12345"})
    assert r.status_code == 201, r.text
    st = r.json()
    assert st["parent_mobile"] == "+919876512345"
    dup = {"student_code": "BM-2001", "name": "Dup", "parent_mobile": "9876512345"}
    assert env.c.post("/api/v1/students", headers=env.admin, json=dup).status_code == 409
    bad = {"student_code": "X1", "name": "Bad", "parent_mobile": "12345"}
    assert env.c.post("/api/v1/students", headers=env.admin, json=bad).status_code == 422

    body = {"student_id": st["id"], "description": "Monthly tuition fee", "billing_month": "2026-12",
            "total_paise": 300000, "due_date": "2026-12-05"}
    # Another centre cannot attach a fee to this student
    other = env.c.post("/api/v1/installments", headers={**env.other_admin, "Idempotency-Key": key()}, json=body)
    assert other.status_code == 404

    k = key()
    r = env.c.post("/api/v1/installments", headers={**env.admin, "Idempotency-Key": k}, json=body)
    assert r.status_code == 201, r.text
    again = env.c.post("/api/v1/installments", headers={**env.admin, "Idempotency-Key": k}, json=body)
    assert again.json() == r.json()  # replay does not create a second fee

    token = r.json()["payment_link"].rsplit("/", 1)[1]
    v = env.c.get(f"/api/v1/payment-declarations/public/{token}").json()
    assert v["student"]["name"] == "Ishaan Rao" and v["fee"]["outstanding_paise"] == 300000 and v["can_pay"]

    msgs = [m for m in env.sync()["sms"] if m["to_mobile"] == "+919876512345"]
    assert len(msgs) == 1 and "Rs. 3,000" in msgs[0]["body"] and "/pay/" in msgs[0]["body"]
    status = env.c.get(f"/api/v1/installments/{r.json()['id']}/payment-status", headers=env.admin).json()
    assert len([x for x in status["reminders"] if x["status"] == "SCHEDULED"]) == 3
    assert len(env.c.get("/api/v1/students", headers=env.admin).json()["items"]) == 5


def test_production_config_validation(monkeypatch):
    from app.config import Settings, normalize_database_url

    assert normalize_database_url("postgres://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
    assert normalize_database_url("postgresql://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"

    monkeypatch.setenv("FEEFLOW_ENV", "production")
    monkeypatch.delenv("FEEFLOW_SECRET_KEY", raising=False)
    monkeypatch.delenv("FEEFLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("FEEFLOW_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/d")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://feeflow.onrender.com")
    with pytest.raises(RuntimeError, match="FEEFLOW_SECRET_KEY"):
        Settings().validate()
    monkeypatch.setenv("FEEFLOW_SECRET_KEY", "x" * 40)
    s = Settings()
    s.validate()
    assert s.public_base_url == "https://feeflow.onrender.com"
    assert s.database_url.startswith("postgresql+psycopg://")


def test_bootstrap_creates_admin_only_and_env_controls_password(monkeypatch):
    from app.bootstrap import bootstrap
    from app.db import SessionLocal, create_all, init_engine
    from app.models import AdminUser, Tenant
    from app.security import verify_password

    init_engine("sqlite://")
    create_all()
    db = SessionLocal()
    monkeypatch.delenv("FEEFLOW_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("FEEFLOW_ADMIN_PASSWORD", raising=False)
    assert bootstrap(db) == "unconfigured"
    monkeypatch.setenv("FEEFLOW_ADMIN_EMAIL", "Owner@Sunshine.in")
    monkeypatch.setenv("FEEFLOW_ADMIN_PASSWORD", "a-long-password")
    assert bootstrap(db) == "created"
    assert bootstrap(db) == "existing"
    admin = db.query(AdminUser).one()
    assert admin.email == "owner@sunshine.in" and admin.tenant_id is None
    assert db.query(Tenant).count() == 0  # no workspace until the admin creates it
    monkeypatch.setenv("FEEFLOW_ADMIN_PASSWORD", "a-new-long-password")
    assert bootstrap(db) == "updated"
    db.refresh(admin)
    assert verify_password("a-new-long-password", admin.password_hash)
    monkeypatch.setenv("FEEFLOW_ADMIN_PASSWORD", "short")
    with pytest.raises(RuntimeError):
        bootstrap(db)
    db.close()


def _fresh_admin(env, email="founder@newcentre.in", password="founder-password-1"):
    from app.db import SessionLocal
    from app.models import AdminUser
    from app.security import hash_password

    db = SessionLocal()
    db.add(AdminUser(tenant_id=None, email=email, name="Administrator", password_hash=hash_password(password)))
    db.commit()
    db.close()
    r = env.c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200 and r.json()["has_workspace"] is False
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admin_creates_workspace_and_edits_upi(env):
    h = _fresh_admin(env)
    me = env.c.get("/api/v1/auth/me", headers=h).json()
    assert me["has_workspace"] is False and me["workspace"] is None and me["centre"] is None
    # Everything tenant-scoped is blocked until the workspace exists
    r = env.c.get("/api/v1/payment-verification/pending", headers=h)
    assert r.status_code == 403 and r.headers["X-FeeFlow-Error"] == "WORKSPACE_REQUIRED"

    bad = {"name": "New Centre", "upi_vpa": "not-a-upi", "upi_payee_name": "New Centre"}
    assert env.c.post("/api/v1/workspace", headers=h, json=bad).status_code == 422
    body = {"name": "Sri Vidya Tuition Centre", "upi_vpa": " srividya@okhdfcbank ", "upi_payee_name": "Sri Vidya",
            "contact_phone": "+91 90000 22222"}
    r = env.c.post("/api/v1/workspace", headers=h, json=body)
    assert r.status_code == 201, r.text
    ws = r.json()
    assert ws["upi_vpa"] == "srividya@okhdfcbank" and ws["short_code"] == "SVTC"
    assert env.c.post("/api/v1/workspace", headers=h, json=body).status_code == 409  # only one
    me = env.c.get("/api/v1/auth/me", headers=h).json()
    assert me["has_workspace"] and me["centre"]["name"] == "Sri Vidya Tuition Centre"

    # Same session token now works for tenant-scoped endpoints; the new workspace is empty
    assert env.c.get("/api/v1/payment-verification/pending", headers=h).json() == {"items": []}
    assert env.c.get("/api/v1/installments", headers=h).json() == {"items": []}

    # Add a student + fee; the parent page pays to THIS centre's UPI ID
    st = env.c.post("/api/v1/students", headers=h, json={"student_code": "SV-1", "name": "Kavya",
                                                           "parent_mobile": "9811122233"}).json()
    fee = env.c.post("/api/v1/installments", headers={**h, "Idempotency-Key": key()},
                     json={"student_id": st["id"], "description": "Tuition", "billing_month": "2026-12",
                           "total_paise": 120000, "due_date": "2026-12-10"}).json()
    token = fee["payment_link"].rsplit("/", 1)[1]
    view = env.c.get(f"/api/v1/payment-declarations/public/{token}").json()
    assert view["upi"]["payee_vpa"] == "srividya@okhdfcbank"

    # Edit the UPI ID in Settings -> payment page follows, change is audited
    r = env.c.patch("/api/v1/workspace", headers=h, json={"upi_vpa": "srividya.new@okicici", "reminder_frequency_hours": 48})
    assert r.status_code == 200 and r.json()["upi_vpa"] == "srividya.new@okicici"
    assert env.c.patch("/api/v1/workspace", headers=h, json={"upi_vpa": "bad upi"}).status_code == 422
    view = env.c.get(f"/api/v1/payment-declarations/public/{token}").json()
    assert view["upi"]["payee_vpa"] == "srividya.new@okicici"
    assert "pa=srividya.new@okicici" in view["upi"]["uris"]["OTHER_UPI"]
    logs = env.c.get("/api/v1/audit-logs", headers=h).json()["items"]
    upi_log = [a for a in logs if a["action"] == "UPI_ID_CHANGED"][0]
    assert "srividya@okhdfcbank" in upi_log["details"] and "srividya.new@okicici" in upi_log["details"]

    # Tenant isolation: the demo centre's admin sees nothing of the new workspace
    assert all(i["student_id"] != "SV-1" for i in env.c.get("/api/v1/installments", headers=env.admin).json()["items"])


def test_workspace_short_code_collisions_are_resolved(env):
    h = _fresh_admin(env)
    # Demo centre already uses BMTC
    r = env.c.post("/api/v1/workspace", headers=h, json={"name": "Bright Minds Tuition Centre", "upi_vpa": "bm2@okaxis",
                                                         "upi_payee_name": "BM"})
    assert r.status_code == 201 and r.json()["short_code"] == "BMTC2"
    h2 = _fresh_admin(env, email="other@x.in")
    r = env.c.post("/api/v1/workspace", headers=h2, json={"name": "Xyz Classes", "upi_vpa": "xyzclasses@ybl", "upi_payee_name": "Xyz",
                                                          "short_code": "bmtc"})
    assert r.status_code == 409


def test_healthz(env):
    assert env.c.get("/healthz").json() == {"status": "ok"}
