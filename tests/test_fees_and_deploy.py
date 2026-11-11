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


def test_bootstrap_creates_first_centre_once(monkeypatch):
    from app.bootstrap import bootstrap
    from app.db import SessionLocal, create_all, init_engine
    from app.models import AdminUser, Tenant
    from app.security import verify_password

    init_engine("sqlite://")
    create_all()
    values = {"CENTRE_NAME": "Sunshine Tuition", "CENTRE_CODE": "sun", "UPI_VPA": "sun@okicici",
              "UPI_NAME": "Sunshine", "ADMIN_EMAIL": "Owner@Sunshine.in", "ADMIN_PASSWORD": "a-long-password"}
    for k, v in values.items():
        monkeypatch.setenv(f"FEEFLOW_BOOTSTRAP_{k}", v)
    db = SessionLocal()
    assert bootstrap(db) == "created"
    assert bootstrap(db) == "existing"
    assert db.query(Tenant).one().short_code == "SUN"
    admin = db.query(AdminUser).one()
    assert admin.email == "owner@sunshine.in" and verify_password("a-long-password", admin.password_hash)
    db.close()


def test_healthz(env):
    assert env.c.get("/healthz").json() == {"status": "ok"}
