"""Security, tenant isolation, duplicate-credit protection, reminders and device sync."""
from datetime import timedelta

from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import AuditLog, PaymentDeclaration, SmsOutbox, VerifiedPayment
from app.ratelimit import limiter
from app.services.reminders import process_grace_expiry
from tests.conftest import key


def test_invalid_and_expired_links(env):
    assert env.c.get("/api/v1/payment-declarations/public/" + "x" * 32).status_code == 404
    v = env.view("BM-1004-expired").json()
    assert v["display_status"] == "EXPIRED" and v["can_pay"] is False and v["link_expired"]
    r = env.declare("BM-1004-expired", action="PAID")
    assert r.status_code == 410


def test_admin_endpoints_require_auth(env):
    assert env.c.get("/api/v1/payment-verification/pending").status_code == 401
    assert env.c.get("/api/v1/payment-verification/pending", headers={"Authorization": "Bearer junk"}).status_code == 401
    r = env.c.post("/api/v1/auth/login", json={"email": "admin@brightminds.test", "password": "wrong"})
    assert r.status_code == 401


def test_frontend_cannot_verify_without_admin(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    body = {"confirm": True, "bank_transaction_checked": True, "belongs_to_centre": True,
            "transactions": [{"amount_paise": 250000, "bank_reference": "U1"}]}
    r = env.c.post(f"/api/v1/payment-verification/{decl_id}/confirm", json=body, headers={"Idempotency-Key": key()})
    assert r.status_code == 401
    # Parent API cannot set VERIFIED
    r = env.c.post("/api/v1/payment-declarations", json={"token": env.token(), "action": "VERIFIED"},
                   headers={"Idempotency-Key": key()})
    assert r.status_code == 422


def test_tenant_isolation(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    assert env.pending(env.other_admin) == []
    assert env.c.get(f"/api/v1/payment-declarations/{decl_id}", headers=env.other_admin).status_code == 404
    assert env.reject(decl_id, headers=env.other_admin).status_code == 404
    assert env.c.get(f"/api/v1/installments/{env.inst()}/payment-status", headers=env.other_admin).status_code == 404
    db = SessionLocal()
    assert db.get(PaymentDeclaration, decl_id).declaration_status == "PAYMENT_REPORTED"
    db.close()


def test_confirm_requires_explicit_checks_and_evidence(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    r = env.confirm(decl_id, [{"amount_paise": 250000, "bank_reference": "U1"}], bank_transaction_checked=False)
    assert r.status_code == 422
    r = env.confirm(decl_id, [{"amount_paise": 250000}])  # no reference and no evidence note
    assert r.status_code == 422
    r = env.confirm(decl_id, [{"amount_paise": 250000}], evidence_note="Seen in HDFC app, 10:42 credit from DIYA P")
    assert r.status_code == 200


def test_bank_transaction_never_credited_twice(env):
    env.declare("BM-1001", action="PAID")
    env.declare("BM-1002", action="PAID")
    assert env.confirm(env.decl_id("BM-1001"), [{"amount_paise": 250000, "bank_reference": "SAME-UTR"}]).status_code == 200
    r = env.confirm(env.decl_id("BM-1002"), [{"amount_paise": 250000, "bank_reference": "SAME-UTR"}])
    assert r.status_code == 409 and "already been credited" in r.json()["detail"]
    # The failed attempt left nothing behind
    st = env.status("BM-1002")
    assert st["paid_paise"] == 0 and st["declaration_status"] == "PAYMENT_REPORTED"  # unchanged
    db = SessionLocal()
    assert db.query(VerifiedPayment).count() == 1
    db.close()


def test_confirm_idempotent_and_not_repeatable(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    k = key()
    tx = [{"amount_paise": 250000, "bank_reference": "UTR-I"}]
    a = env.confirm(decl_id, tx, idem=k)
    b = env.confirm(decl_id, tx, idem=k)
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    c = env.confirm(decl_id, tx)  # new key: declaration is already VERIFIED
    assert c.status_code == 409
    db = SessionLocal()
    assert db.query(VerifiedPayment).count() == 1
    db.close()


def test_request_clarification_and_parent_reply(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    r = env.c.post(
        f"/api/v1/payment-verification/{decl_id}/request-clarification",
        json={"message": "Which UPI app and roughly what time did you pay?"},
        headers={**env.admin, "Idempotency-Key": key()},
    )
    assert r.status_code == 200
    v = env.view().json()
    assert v["declaration"]["clarification_request"].startswith("Which UPI app")
    assert v["installment_status"] == "UNPAID"
    sms = SessionLocal().query(SmsOutbox).filter_by(kind="CLARIFICATION_REQUESTED").one()
    assert "/pay/" in sms.body

    ref = v["declaration"]["reference"]
    r = env.c.post(
        f"/api/v1/payment-declarations/{ref}/clarification",
        json={"token": env.token(), "note": "Paid with PhonePe around 7pm", "approximate_payment_time": "19:00"},
        headers={"Idempotency-Key": key()},
    )
    assert r.status_code == 200, r.text
    assert r.json()["declaration"]["clarification_request"] is None
    detail = env.c.get(f"/api/v1/payment-declarations/{decl_id}", headers=env.admin).json()
    assert "PhonePe around 7pm" in detail["parent_notes"]

    # Sensitive data is refused
    r = env.c.post(
        f"/api/v1/payment-declarations/{ref}/clarification",
        json={"token": env.token(), "note": "my upi pin is 1234"},
        headers={"Idempotency-Key": key()},
    )
    assert r.status_code == 422


def test_reopen_rejected_declaration(env):
    env.declare(action="PAID")
    decl_id = env.decl_id()
    env.reject(decl_id)
    r = env.c.post(f"/api/v1/payment-verification/{decl_id}/reopen", json={"reason": "Credit showed up late"},
                   headers={**env.admin, "Idempotency-Key": key()})
    assert r.status_code == 200
    assert r.json()["declaration"]["verification_status"] == "PENDING_VERIFICATION"
    assert env.confirm(decl_id, [{"amount_paise": 250000, "bank_reference": "LATE"}]).status_code == 200


def test_reopen_blocked_when_another_declaration_active(env):
    env.declare(action="PAID")
    d1 = env.decl_id()
    env.reject(d1)
    env.declare(action="PAID")
    r = env.c.post(f"/api/v1/payment-verification/{d1}/reopen", json={"reason": "retry"},
                   headers={**env.admin, "Idempotency-Key": key()})
    assert r.status_code == 409


def test_failed_then_retry(env):
    r = env.declare(action="FAILED", upi_app="PAYTM")
    assert r.status_code == 201 and r.json()["display_status"] == "FAILED" and r.json()["can_pay"]
    r = env.declare(action="PAID")
    assert r.status_code == 201 and r.json()["display_status"] == "PAYMENT_REPORTED"


def test_grace_period_expiry(env):
    env.declare(action="PAID")
    db = SessionLocal()
    n = process_grace_expiry(db, utcnow() + timedelta(hours=25))
    db.close()
    assert n == 1
    notes = env.c.get("/api/v1/notifications", headers=env.admin).json()["items"]
    assert any(x["kind"] == "VERIFICATION_OVERDUE" for x in notes)
    st = env.status()
    assert st["declaration_status"] == "PAYMENT_REPORTED"  # not assumed failed
    assert st["installment_status"] == "UNPAID"
    # idempotent
    db = SessionLocal()
    assert process_grace_expiry(db, utcnow() + timedelta(hours=26)) == 0
    db.close()


def test_device_sync_and_ack(env):
    env.declare(action="PAID")
    first = env.sync()
    acks = [m for m in first["sms"] if m["kind"] == "DECLARATION_ACK"]
    assert len(acks) == 1
    rems = first["reminder_schedules"]
    assert rems and all(r["hold_until"] for r in rems if r["to_mobile"] == "+919800000001")

    r = env.c.post(
        "/api/v1/devices/ack",
        json={"sms": [{"id": acks[0]["id"], "status": "SENT"}],
              "reminders": [{"id": r["id"], "version": r["version"]} for r in rems]},
        headers=env.device,
    )
    assert r.status_code == 200
    second = env.sync()
    assert second["sms"] == [] and second["reminder_schedules"] == []

    # Verification -> cancellation is pushed to the device
    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "UTR-S"}])
    third = env.sync()
    cancelled = [r for r in third["reminder_schedules"] if r["status"] == "CANCELLED"]
    assert len(cancelled) == 3
    assert any(m["kind"] == "PAYMENT_VERIFIED" for m in third["sms"])
    # Unauthenticated device rejected
    assert env.c.get("/api/v1/devices/sync", headers={"X-Device-Token": "nope"}).status_code == 401


def test_device_sent_reminder_after_cancellation_is_audited(env):
    rems = env.sync()["reminder_schedules"]
    target = [r for r in rems if r["to_mobile"] == "+919800000001"][0]
    env.declare(action="PAID")
    env.confirm(env.decl_id(), [{"amount_paise": 250000, "bank_reference": "UTR-OFF"}])
    # Offline device reports it sent using its stale copy
    env.c.post("/api/v1/devices/ack", json={"reminders": [{"id": target["id"], "version": target["version"],
                                                           "sent_at": "2030-01-01T10:00:00Z"}]}, headers=env.device)
    db = SessionLocal()
    assert db.query(AuditLog).filter_by(action="REMINDER_SENT_AFTER_CANCELLATION").count() == 1
    db.close()


def test_bank_import_suggestions_and_match(env):
    env.declare(action="PAID", note="paid from dad's account")
    decl_id = env.decl_id()
    now = utcnow().isoformat() + "Z"
    r = env.c.post("/api/v1/reconciliation/bank-transactions/import", headers=env.admin, json={
        "transactions": [
            {"bank_reference": "BANK-REF-1", "amount_paise": 250000, "credited_at": now, "narration": "UPI BM-1001 fee"},
            {"bank_reference": "BANK-REF-2", "amount_paise": 99900, "credited_at": now},
        ]})
    assert r.json() == {"created": 2, "skipped_duplicates": 0}
    detail = env.c.get(f"/api/v1/payment-declarations/{decl_id}", headers=env.admin).json()
    matches = detail["suggested_bank_matches"]
    assert len(matches) == 1 and matches[0]["bank_reference"] == "BANK-REF-1"
    r = env.confirm(decl_id, [{"amount_paise": 250000, "bank_transaction_id": matches[0]["bank_transaction_id"]}])
    assert r.status_code == 200, r.text
    assert r.json()["payments"][0]["reconciliation_source"] == "BANK_IMPORT_MATCH"
    # Re-import is de-duplicated
    r = env.c.post("/api/v1/reconciliation/bank-transactions/import", headers=env.admin, json={
        "transactions": [{"bank_reference": "BANK-REF-1", "amount_paise": 250000, "credited_at": now}]})
    assert r.json()["skipped_duplicates"] == 1


def test_reports_available(env):
    for name in ["payment-declarations", "pending-verification", "verified-payments", "rejected-declarations",
                 "partial-payments", "duplicate-payments", "excess-payments", "outstanding-fees"]:
        r = env.c.get(f"/api/v1/reports/{name}", headers=env.admin)
        assert r.status_code == 200, name
    assert env.c.get("/api/v1/reports/nope", headers=env.admin).status_code == 404


def test_idempotency_key_required_and_body_mismatch(env):
    r = env.c.post("/api/v1/payment-declarations", json={"token": env.token(), "action": "PAID"})
    assert r.status_code == 400
    k = key()
    assert env.declare(action="INITIATED", upi_app="GPAY", idem=k).status_code == 201
    assert env.declare(action="INITIATED", upi_app="PAYTM", idem=k).status_code == 422


def test_rate_limiting(env):
    settings.rate_limit_enabled = True
    limiter.reset()
    codes = [env.view().status_code for _ in range(65)]
    assert codes.count(429) >= 1
    settings.rate_limit_enabled = False


def test_qr_code(env):
    r = env.c.get(f"/api/v1/pay/{env.token()}/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")


def test_register_device_replaces_previous(env):
    env.declare(action="PAID")
    first = env.sync()  # old phone takes the ack SMS but never acks it
    assert any(m["kind"] == "DECLARATION_ACK" for m in first["sms"])
    r = env.c.post("/api/v1/devices/register", json={"name": "Pixel 7 office"}, headers=env.admin)
    assert r.status_code == 201, r.text
    new_token = r.json()["device_token"]
    assert r.json()["deactivated_devices"] == 1
    # Old phone is locked out
    assert env.c.get("/api/v1/devices/sync", headers=env.device).status_code == 401
    # New phone gets the unacknowledged SMS and the full reminder schedule
    new = env.c.get("/api/v1/devices/sync", headers={"X-Device-Token": new_token}).json()
    assert any(m["kind"] == "DECLARATION_ACK" for m in new["sms"])
    assert len(new["reminder_schedules"]) == 12
    lst = env.c.get("/api/v1/devices", headers=env.admin).json()
    assert [d["active"] for d in lst["items"]] == [True, False]
    # Other tenants cannot see or revoke it
    assert env.c.get("/api/v1/devices", headers=env.other_admin).json()["items"] == []
    dev_id = lst["items"][0]["id"]
    assert env.c.post(f"/api/v1/devices/{dev_id}/revoke", headers=env.other_admin).status_code == 404
    assert env.c.post(f"/api/v1/devices/{dev_id}/revoke", headers=env.admin).status_code == 200
    assert env.c.get("/api/v1/devices/sync", headers={"X-Device-Token": new_token}).status_code == 401
    assert env.c.post("/api/v1/devices/register", json={"name": "x"}, headers=env.device).status_code in (401, 422)
