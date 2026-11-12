"""Self sign-up, 30-day trial, student profiles + monthly fees, send fee link, platform console."""
from datetime import date, timedelta

import pytest

from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import AdminUser, Installment, ReminderSchedule, SmsOutbox, Tenant
from app.ratelimit import limiter
from app.security import hash_password
from app.services.fees import due_date_for, generate_monthly_fees, month_of
from tests.conftest import key

STUDENT = {
    "name": "Kavya Raman", "parent_name": "Raman K", "parent_mobile": "98111 22233", "parent_email": "raman@example.in",
    "date_of_birth": "2014-06-02", "gender": "FEMALE", "class_grade": "Class 7", "school": "DAV Public School",
    "address": "12 Gandhi St, Chennai", "monthly_fee_paise": 150000, "fee_due_day": 10,
}


def signup(env, email="owner@srividya.in", name="Lakshmi"):
    r = env.c.post("/api/v1/auth/signup", json={"name": name, "email": email, "password": "strong-pass-123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def new_workspace(env, email="owner@srividya.in", name="Sri Vidya Tuition"):
    h = signup(env, email)
    r = env.c.post("/api/v1/workspace", headers=h, json={"name": name, "upi_vpa": "srividya@okhdfcbank",
                                                         "upi_payee_name": "Sri Vidya"})
    assert r.status_code == 201, r.text
    return h


def tenant_of(email):
    db = SessionLocal()
    t = db.get(Tenant, db.query(AdminUser).filter_by(email=email).one().tenant_id)
    db.close()
    return t


def platform_admin(env):
    db = SessionLocal()
    db.add(AdminUser(tenant_id=None, email="owner@feeflow.in", name="Platform", is_platform_admin=True,
                     password_hash=hash_password("platform-pass-1")))
    db.commit()
    db.close()
    r = env.c.post("/api/v1/auth/login", json={"email": "owner@feeflow.in", "password": "platform-pass-1"})
    assert r.json()["is_platform_admin"] is True
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def set_trial_end(email, when):
    db = SessionLocal()
    t = db.get(Tenant, db.query(AdminUser).filter_by(email=email).one().tenant_id)
    t.trial_ends_at = when
    db.commit()
    db.close()


# ------------------------------------------------------------------ sign-up + trial
def test_signup_then_workspace_starts_30_day_trial(env):
    h = signup(env)
    me = env.c.get("/api/v1/auth/me", headers=h).json()
    assert me["has_workspace"] is False and me["is_platform_admin"] is False
    dup = env.c.post("/api/v1/auth/signup", json={"name": "X Y", "email": "OWNER@srividya.in", "password": "strong-pass-123"})
    assert dup.status_code == 409
    weak = env.c.post("/api/v1/auth/signup", json={"name": "X Y", "email": "a@b.in", "password": "short"})
    assert weak.status_code == 422
    env.c.post("/api/v1/workspace", headers=h, json={"name": "Sri Vidya Tuition", "upi_vpa": "srividya@okhdfcbank",
                                                     "upi_payee_name": "Sri Vidya"})
    sub = env.c.get("/api/v1/auth/me", headers=h).json()["workspace"]["subscription"]
    assert sub["status"] == "TRIAL" and sub["trial_days_left"] == 30 and sub["read_only"] is False
    # Same login works afterwards
    r = env.c.post("/api/v1/auth/login", json={"email": "owner@srividya.in", "password": "strong-pass-123"})
    assert r.status_code == 200 and r.json()["has_workspace"] is True


def test_signup_is_rate_limited(env):
    settings.rate_limit_enabled = True
    limiter.reset()
    codes = [env.c.post("/api/v1/auth/signup", json={"name": "Spam Bot", "email": f"s{i}@x.in",
                                                     "password": "strong-pass-123"}).status_code for i in range(7)]
    settings.rate_limit_enabled = False
    assert codes[:5] == [201] * 5 and 429 in codes[5:]


# ------------------------------------------------------------------ students + monthly fees
def test_student_profile_creates_this_months_fee_without_sending(env):
    h = new_workspace(env)
    r = env.c.post("/api/v1/students", headers=h, json=STUDENT)
    assert r.status_code == 201, r.text
    st = r.json()
    assert st["student_id"] == "SVT-0001"  # generated from the workspace's receipt prefix
    assert st["parent_mobile"] == "+919811122233" and st["class_grade"] == "Class 7" and st["gender"] == "FEMALE"
    assert st["date_of_birth"] == "2014-06-02" and st["joined_on"] == utcnow().date().isoformat()
    assert len(st["fees"]) == 1
    fee = st["fees"][0]
    month = month_of(utcnow().date())
    assert fee["kind"] == "MONTHLY" and fee["billing_month"] == month and fee["total_paise"] == 150000
    assert fee["due_date"] == due_date_for(month, 10).isoformat()
    assert st["sms_phone_active"] is False
    db = SessionLocal()
    # Automatic sending is OFF by default: nothing queued, no reminders
    assert db.query(SmsOutbox).filter_by(installment_id=fee["id"]).count() == 0
    assert db.query(ReminderSchedule).filter_by(installment_id=fee["id"]).count() == 0
    db.close()
    second = env.c.post("/api/v1/students", headers=h, json={**STUDENT, "name": "Arjun Raman"}).json()
    assert second["student_id"] == "SVT-0002"


def test_student_validation_search_edit_deactivate(env):
    h = new_workspace(env)
    half = {**STUDENT}
    half.pop("fee_due_day")
    assert env.c.post("/api/v1/students", headers=h, json=half).status_code == 422
    assert env.c.post("/api/v1/students", headers=h, json={**STUDENT, "parent_mobile": "12345"}).status_code == 422
    assert env.c.post("/api/v1/students", headers=h, json={**STUDENT, "date_of_birth": "2999-01-01"}).status_code == 422
    no_plan = {k: v for k, v in STUDENT.items() if k not in ("monthly_fee_paise", "fee_due_day")}
    st = env.c.post("/api/v1/students", headers=h, json={**no_plan, "name": "Meena Iyer", "student_code": "SV-77"}).json()
    assert st["fees"] == [] and st["student_id"] == "SV-77"
    env.c.post("/api/v1/students", headers=h, json=STUDENT)
    assert [s["name"] for s in env.c.get("/api/v1/students?q=meena", headers=h).json()["items"]] == ["Meena Iyer"]
    assert len(env.c.get("/api/v1/students?q=class 7", headers=h).json()["items"]) == 2

    r = env.c.patch(f"/api/v1/students/{st['id']}", headers=h, json={"class_grade": "Class 8", "monthly_fee_paise": 90000})
    assert r.status_code == 422  # plan needs both values
    r = env.c.patch(f"/api/v1/students/{st['id']}", headers=h,
                    json={"class_grade": "Class 8", "monthly_fee_paise": 90000, "fee_due_day": 31})
    assert r.status_code == 200 and r.json()["class_grade"] == "Class 8" and r.json()["fee_due_day"] == 31

    r = env.c.patch(f"/api/v1/students/{st['id']}", headers=h, json={"active": False})
    assert r.json()["active"] is False
    assert len(env.c.get("/api/v1/students", headers=h).json()["items"]) == 1
    assert len(env.c.get("/api/v1/students?include_inactive=true", headers=h).json()["items"]) == 2
    r = env.c.post(f"/api/v1/students/{st['id']}/send-fee-link", headers={**h, "Idempotency-Key": key()},
                   json={"channel": "SMS"})
    assert r.status_code == 409


def test_students_are_private_to_each_workspace(env):
    h = new_workspace(env)
    st = env.c.post("/api/v1/students", headers=h, json=STUDENT).json()
    assert all(s["name"] != "Kavya Raman" for s in env.c.get("/api/v1/students", headers=env.admin).json()["items"])
    assert env.c.get(f"/api/v1/students/{st['id']}", headers=env.admin).status_code == 404
    assert env.c.patch(f"/api/v1/students/{st['id']}", headers=env.admin, json={"name": "Hacked"}).status_code == 404
    r = env.c.post(f"/api/v1/students/{st['id']}/send-fee-link", headers={**env.admin, "Idempotency-Key": key()},
                   json={"channel": "SMS"})
    assert r.status_code == 404


def test_monthly_generation_is_idempotent_and_clamps_due_day(env):
    h = new_workspace(env)
    st = env.c.post("/api/v1/students", headers=h, json={**STUDENT, "fee_due_day": 31}).json()
    db = SessionLocal()
    assert generate_monthly_fees(db) == 0  # this month's fee already exists
    next_month = (utcnow().date().replace(day=28) + timedelta(days=5)).replace(day=1)
    assert generate_monthly_fees(db, today=next_month) >= 1
    assert generate_monthly_fees(db, today=next_month) == 0
    fees_ = db.query(Installment).filter_by(student_id=st["id"], kind="MONTHLY").all()
    assert len(fees_) == 2
    assert due_date_for("2027-02", 31) == date(2027, 2, 28)
    db.close()


def test_auto_send_setting_sends_link_and_schedules_reminders(env):
    h = new_workspace(env)
    r = env.c.patch("/api/v1/workspace", headers=h, json={"auto_send_links": True})
    assert r.json()["auto_send_links"] is True
    st = env.c.post("/api/v1/students", headers=h, json=STUDENT).json()
    fee_id = st["fees"][0]["id"]
    db = SessionLocal()
    msg = db.query(SmsOutbox).filter_by(installment_id=fee_id).one()
    assert msg.to_mobile == "+919811122233" and "/pay/" in msg.body and "Rs. 1,500" in msg.body
    assert db.query(ReminderSchedule).filter_by(installment_id=fee_id).count() == 3
    db.close()


# ------------------------------------------------------------------ send fee link
def test_send_fee_link_sms_and_share(env):
    h = new_workspace(env)
    st = env.c.post("/api/v1/students", headers=h, json=STUDENT).json()
    url = f"/api/v1/students/{st['id']}/send-fee-link"
    assert env.c.post(url, headers=h, json={"channel": "SMS"}).status_code == 400  # Idempotency-Key required

    k = key()
    r = env.c.post(url, headers={**h, "Idempotency-Key": k}, json={"channel": "SMS"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sms_queued"] is True and body["sms_phone_active"] is False
    assert body["fee"]["outstanding_paise"] == 150000 and body["parent_mobile"] == "+919811122233"
    replay = env.c.post(url, headers={**h, "Idempotency-Key": k}, json={"channel": "SMS"})
    assert replay.json() == body  # double tap: same link, nothing queued twice
    db = SessionLocal()
    assert db.query(SmsOutbox).filter_by(installment_id=body["fee"]["id"]).count() == 1
    db.close()
    again = env.c.post(url, headers={**h, "Idempotency-Key": key()}, json={"channel": "SMS"})
    assert again.json()["sms_queued"] is True  # deliberate resend is allowed

    share = env.c.post(url, headers={**h, "Idempotency-Key": key()}, json={"channel": "SHARE"}).json()
    assert share["sms_queued"] is False and share["payment_link"] in share["share_text"]
    assert "Kavya Raman" in share["share_text"] and "Sri Vidya Tuition" in share["share_text"]
    db = SessionLocal()
    assert db.query(SmsOutbox).filter_by(installment_id=body["fee"]["id"]).count() == 2
    db.close()

    # The link opens the parent payment page for this centre's UPI ID
    token = share["payment_link"].rsplit("/", 1)[1]
    view = env.c.get(f"/api/v1/payment-declarations/public/{token}").json()
    assert view["upi"]["payee_vpa"] == "srividya@okhdfcbank" and view["student"]["name"] == "Kavya Raman"


def test_send_link_without_any_fee_is_explained(env):
    h = new_workspace(env)
    no_plan = {k: v for k, v in STUDENT.items() if k not in ("monthly_fee_paise", "fee_due_day")}
    st = env.c.post("/api/v1/students", headers=h, json=no_plan).json()
    r = env.c.post(f"/api/v1/students/{st['id']}/send-fee-link", headers={**h, "Idempotency-Key": key()},
                   json={"channel": "SMS"})
    assert r.status_code == 409 and "No unpaid fee" in r.json()["detail"]


# ------------------------------------------------------------------ trial expiry
def test_expired_trial_is_read_only_but_parents_and_verification_keep_working(env):
    h = new_workspace(env)
    st = env.c.post("/api/v1/students", headers=h, json=STUDENT).json()
    share = env.c.post(f"/api/v1/students/{st['id']}/send-fee-link", headers={**h, "Idempotency-Key": key()},
                       json={"channel": "SHARE"}).json()
    set_trial_end("owner@srividya.in", utcnow() - timedelta(minutes=1))

    sub = env.c.get("/api/v1/auth/me", headers=h).json()["workspace"]["subscription"]
    assert sub["status"] == "EXPIRED" and sub["read_only"] is True
    r = env.c.post("/api/v1/students", headers=h, json={**STUDENT, "name": "New Kid"})
    assert r.status_code == 403 and r.headers["X-FeeFlow-Error"] == "TRIAL_EXPIRED"
    r = env.c.post(f"/api/v1/students/{st['id']}/send-fee-link", headers={**h, "Idempotency-Key": key()},
                   json={"channel": "SMS"})
    assert r.status_code == 403
    assert env.c.get("/api/v1/students", headers=h).status_code == 200  # still readable

    # Parent can still pay and the centre can still verify
    token = share["payment_link"].rsplit("/", 1)[1]
    r = env.c.post("/api/v1/payment-declarations", json={"token": token, "action": "PAID"}, headers={"Idempotency-Key": key()})
    assert r.status_code == 201
    pending = env.c.get("/api/v1/payment-verification/pending", headers=h).json()["items"]
    r = env.c.post(f"/api/v1/payment-verification/{pending[0]['id']}/confirm", headers={**h, "Idempotency-Key": key()},
                   json={"confirm": True, "bank_transaction_checked": True, "belongs_to_centre": True,
                         "transactions": [{"amount_paise": 150000, "bank_reference": "UTR-EXP-1"}]})
    assert r.status_code == 200 and r.json()["installment_status"] == "PAID"
    # No new monthly fees for an expired workspace
    db = SessionLocal()
    next_month = (utcnow().date().replace(day=28) + timedelta(days=5)).replace(day=1)
    generate_monthly_fees(db, today=next_month)
    assert db.query(Installment).filter_by(student_id=st["id"]).count() == 1
    db.close()


# ------------------------------------------------------------------ platform console
def test_platform_console_manages_workspaces(env):
    p = platform_admin(env)
    h = new_workspace(env)
    env.c.post("/api/v1/students", headers=h, json=STUDENT)
    signup(env, email="nowork@x.in")

    assert env.c.get("/api/v1/platform/workspaces", headers=h).status_code == 403  # centre owners can't
    assert env.c.get("/api/v1/platform/workspaces", headers=env.admin).status_code == 403
    data = env.c.get("/api/v1/platform/workspaces", headers=p).json()
    row = next(w for w in data["items"] if w["name"] == "Sri Vidya Tuition")
    assert row["owner_email"] == "owner@srividya.in" and row["students"] == 1 and row["fees"] == 1
    assert row["status"] == "TRIAL" and row["trial_days_left"] == 30 and row["last_activity_at"]
    assert "Kavya" not in str(data)  # counts only, no student/parent personal data
    assert data["summary"]["signed_up_without_workspace"] == 2  # this sign-up + the seeded founder account
    wid = row["id"]

    set_trial_end("owner@srividya.in", utcnow() - timedelta(days=1))
    r = env.c.post(f"/api/v1/platform/workspaces/{wid}/extend-trial", headers=p, json={"days": 30})
    assert r.json()["status"] == "TRIAL" and r.json()["trial_days_left"] == 30
    r = env.c.post(f"/api/v1/platform/workspaces/{wid}/activate", headers=p)
    assert r.json()["status"] == "ACTIVE" and r.json()["trial_days_left"] is None

    # Suspend: owner locked out, payment pages closed, SMS phone stopped
    st = env.c.get("/api/v1/students", headers=h).json()["items"][0]
    link = env.c.post(f"/api/v1/students/{st['id']}/send-fee-link", headers={**h, "Idempotency-Key": key()},
                      json={"channel": "SHARE"}).json()["payment_link"]
    assert env.c.post(f"/api/v1/platform/workspaces/{wid}/suspend", headers=p, json={"reason": "x"}).status_code == 422
    r = env.c.post(f"/api/v1/platform/workspaces/{wid}/suspend", headers=p, json={"reason": "Fake UPI reported"})
    assert r.json()["status"] == "SUSPENDED"
    login = env.c.post("/api/v1/auth/login", json={"email": "owner@srividya.in", "password": "strong-pass-123"})
    assert login.status_code == 403 and "suspended" in login.json()["detail"]
    assert env.c.get("/api/v1/students", headers=h).headers.get("X-FeeFlow-Error") == "WORKSPACE_SUSPENDED"
    token = link.rsplit("/", 1)[1]
    assert env.c.get(f"/api/v1/payment-declarations/public/{token}").status_code == 410

    r = env.c.post(f"/api/v1/platform/workspaces/{wid}/reactivate", headers=p)
    assert r.json()["status"] == "TRIAL"
    assert env.c.get(f"/api/v1/payment-declarations/public/{token}").status_code == 200
    logs = env.c.get("/api/v1/audit-logs", headers=h).json()["items"]
    assert {"PLATFORM_TRIAL_EXTENDED", "PLATFORM_ACTIVATED", "PLATFORM_SUSPENDED", "PLATFORM_REACTIVATED"} <= {
        a["action"] for a in logs}


def test_platform_admin_cannot_suspend_own_workspace(env):
    p = platform_admin(env)
    env.c.post("/api/v1/workspace", headers=p, json={"name": "My Own Centre", "upi_vpa": "mine@okaxis",
                                                     "upi_payee_name": "Mine"})
    wid = tenant_of("owner@feeflow.in").id
    r = env.c.post(f"/api/v1/platform/workspaces/{wid}/suspend", headers=p, json={"reason": "testing"})
    assert r.status_code == 409


def test_suspended_workspace_device_cannot_sync(env):
    p = platform_admin(env)
    wid = tenant_of("admin@brightminds.test").id
    env.c.post(f"/api/v1/platform/workspaces/{wid}/suspend", headers=p, json={"reason": "Abuse report"})
    assert env.c.get("/api/v1/devices/sync", headers=env.device).status_code == 403


def test_bootstrap_admin_is_platform_owner(monkeypatch):
    from app.bootstrap import bootstrap
    from app.db import create_all, init_engine

    init_engine("sqlite://")
    create_all()
    monkeypatch.setenv("FEEFLOW_ADMIN_EMAIL", "boss@feeflow.in")
    monkeypatch.setenv("FEEFLOW_ADMIN_PASSWORD", "boss-password-1")
    db = SessionLocal()
    bootstrap(db)
    assert db.query(AdminUser).one().is_platform_admin is True
    db.close()
